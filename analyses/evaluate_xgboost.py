"""Prospective V3 evaluation. Reads prediction journal; never relabels old scores.

Entry: first provider session strictly AFTER the UTC publication date, at adjusted
open. Exit: adjusted close of the Hth session including entry. Today excluded.
No orders, no DB writes, no inference from selection-day forward_returns.
"""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import statistics

HORIZONS = {'10d': 10, '30d': 30, '60d': 60}


def outcome(bars, predicted_at, horizon, as_of):
    """Return pending/invalid explicitly; never substitute shorter holding periods."""
    import pandas as pd
    stamp = pd.Timestamp(predicted_at)
    if stamp.tzinfo is None:
        return dict(status='invalid_timestamp')
    publication_day = stamp.tz_convert('UTC').date()
    if bars is None or bars.empty:
        return dict(status='missing_prices')
    frame = bars.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        return dict(status='invalid_prices')
    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        return dict(status='invalid_prices')
    frame = frame.loc[(frame.index.date > publication_day) & (frame.index.date < as_of)]
    if len(frame) < horizon:
        return dict(status='pending', sessions_available=len(frame))
    frame = frame.iloc[:horizon]
    if (frame.index[0].date() - publication_day).days > 7 or frame.index.to_series().diff().dt.days.gt(7).any():
        return dict(status='price_gap')
    try:
        for column in ['Open', 'Close']:
            if not all(math.isfinite(float(v)) and float(v) > 0 for v in frame[column]):
                return dict(status='invalid_prices')
        entry, exit_price = float(frame.iloc[0]['Open']), float(frame.iloc[-1]['Close'])
    except (KeyError, TypeError, ValueError):
        return dict(status='invalid_prices')
    return dict(status='mature', entry_date=str(frame.index[0].date()),
                exit_date=str(frame.index[-1].date()), entry_price=entry,
                exit_price=exit_price, gross_return_pct=100 * (exit_price / entry - 1))


def summarize(rows, *, top_n=10, cost_bps=0):
    """Rank BEFORE inspecting outcomes. Equal-weight matched daily cohorts only."""
    if top_n < 1 or not math.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError('Invalid top size or round-trip cost')
    groups = defaultdict(list)
    for row in rows:
        groups[(row['run_key'], row['horizon'], row['model_sha256'])].append(row)
    cohorts = []
    for (run, horizon, model), cohort in sorted(groups.items()):
        # A ticker appearing on multiple selection dates gets one vote: newest date.
        latest = {}
        for row in sorted(cohort, key=lambda r: (str(r['selection_date']), r['ticker'])):
            if row['ticker'] in latest and str(row['selection_date']) == str(latest[row['ticker']]['selection_date']):
                raise ValueError('Ambiguous duplicate prediction')
            latest[row['ticker']] = row
        ranked = sorted(latest.values(), key=lambda r: (-float(r['score']), r['ticker']))
        counts = Counter(r['outcome']['status'] for r in ranked)
        item = dict(run_key=run, horizon=horizon, model_sha256=model,
                    n=len(ranked), top_n=min(top_n, len(ranked)), states=dict(counts))
        # Do not evaluate only the survivors; wait for all baseline candidates.
        if counts.get('mature', 0) != len(ranked):
            item['status'] = 'incomplete'
        else:
            def metrics(values):
                gross = [r['outcome']['gross_return_pct'] for r in values]
                net = [v - cost_bps / 100 for v in gross]
                return dict(n=len(gross), mean_gross_pct=statistics.mean(gross),
                            mean_net_pct=statistics.mean(net), median_gross_pct=statistics.median(gross),
                            win_rate_gross=statistics.mean(v > 0 for v in gross),
                            worst_gross_pct=min(gross))
            item.update(status='complete', top=metrics(ranked[:top_n]), baseline=metrics(ranked))
            item['excess_pct_points'] = item['top']['mean_gross_pct'] - item['baseline']['mean_gross_pct']
            # Unique date count is reported; multiple reruns are NOT independent days.
        item['publication_day'] = str(min(datetime.fromisoformat(str(r['predicted_at'])).astimezone(timezone.utc).date() for r in ranked))
        cohorts.append(item)
    overview = []
    for horizon in HORIZONS:
        matching = [c for c in cohorts if c['horizon'] == horizon]
        complete = [c for c in matching if c['status'] == 'complete']
        # Average reruns within publication day before averaging days.
        days = defaultdict(list)
        for c in complete:
            days[c['publication_day']].append(c)
        item = dict(horizon=horizon, cohorts=len(matching), complete_cohorts=len(complete),
                    publication_days=len(days), status='pending' if not days else 'descriptive_only',
                    states=dict(Counter(r['outcome']['status'] for r in rows if r['horizon'] == horizon)))
        if days:
            for name, getter in [('top_mean_net_pct', lambda c: c['top']['mean_net_pct']),
                                 ('baseline_mean_net_pct', lambda c: c['baseline']['mean_net_pct']),
                                 ('excess_pct_points', lambda c: c['excess_pct_points'])]:
                item[name] = statistics.mean(statistics.mean(getter(c) for c in values) for values in days.values())
        overview.append(item)
    return dict(cohorts=cohorts, horizons=overview)


def load_predictions(conn, since):
    """One consistent read snapshot; no CREATE/UPDATE even on an empty installation."""
    import psycopg2.extras
    conn.set_session(readonly=True, isolation_level='REPEATABLE READ')
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT count(*) AS n FROM public.xgboost_scores WHERE model_versie='xgboostV3'")
        existing = cur.fetchone()['n']
        cur.execute("SELECT to_regclass('public.xgboost_prediction_log') AS name")
        if cur.fetchone()['name'] is None:
            return [], existing
        cur.execute('''SELECT run_key,ticker,selection_date,horizon,score,model_sha256,source_sha,predicted_at
                       FROM public.xgboost_prediction_log WHERE predicted_at >= %s
                       ORDER BY run_key,horizon,ticker,selection_date''', (since,))
        rows = [dict(r) for r in cur.fetchall()]
    return rows, existing


def build_report(rows, *, as_of, top_n=10, cost_bps=0, fetch=None):
    import pandas as pd
    if fetch is None:
        import yfinance as yf
        def fetch(ticker, start, end):
            return yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True, repair=False, raise_errors=True)
    by_ticker = defaultdict(list)
    for row in rows:
        if row['horizon'] not in HORIZONS or not math.isfinite(float(row['score'])) or not 0 <= float(row['score']) <= 1:
            raise ValueError('Invalid journal prediction')
        stamp = pd.Timestamp(row['predicted_at'])
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError('Publication timestamp must be timezone-aware')
        if stamp.tz_convert('UTC').date() >= as_of:
            # Current-day predictions may not have any tradable session yet.
            row['outcome'] = dict(status='pending')
        else:
            by_ticker[row['ticker']].append(row)
    for ticker, candidates in by_ticker.items():
        start = min(pd.Timestamp(r['predicted_at']).tz_convert('UTC').date() for r in candidates) + timedelta(days=1)
        # Avoid requesting data while even the shortest holding period is immature.
        if (as_of - start).days < min(HORIZONS[r['horizon']] for r in candidates):
            for row in candidates:
                row['outcome'] = dict(status='pending')
            continue
        try:
            bars = fetch(ticker, start.isoformat(), as_of.isoformat())
        except Exception:
            for row in candidates:
                row['outcome'] = dict(status='provider_error')
            continue
        for row in candidates:
            row['outcome'] = outcome(bars, row['predicted_at'], HORIZONS[row['horizon']], as_of)
    result = summarize(rows, top_n=top_n, cost_bps=cost_bps)
    result.update(schema_version=1, as_of=as_of.isoformat(), predictions=len(rows),
                  round_trip_cost_bps=cost_bps, top_n=top_n, predictions_detail=rows,
                  definition='Next available session after UTC publication day: adjusted open to Hth adjusted close; current day excluded.',
                  limitations=['Newly scored candidates only; not the entire original bot universe or the old mixed-horizon Telegram top.',
                               'Baseline is the same cohort, not an index. No FX conversion; percentage returns in local price units.',
                               'Fixed round-trip cost assumption, not broker fees or liquidity-dependent slippage.',
                               'Overlapping holdings and days are dependent. No significance, portfolio drawdown or proven edge claim.',
                               'Provider sessions are not an authoritative exchange calendar; short missing segments and delistings require review.',
                               'Older xgboost_scores are not backfilled with invented fingerprints or publication-time returns.'])
    return result


def render(report):
    lines = ['XGBoost V3 — evaluatie na publicatie', f"Peildatum: {report['as_of']} | voorspellingen: {report['predictions']}",
             f"Top {report['top_n']} per run/horizon, unieke tickers; kosten: {report['round_trip_cost_bps']} bp retour."]
    for h in report['horizons']:
        line = f"{h['horizon']}: {h['complete_cohorts']}/{h['cohorts']} volledige cohorten, {h['publication_days']} publicatiedagen"
        if 'top_mean_net_pct' in h:
            line += f" | top netto {h['top_mean_net_pct']:.2f}% | cohort netto {h['baseline_mean_net_pct']:.2f}% | verschil {h['excess_pct_points']:.2f} pp"
        else:
            line += ' | nog geen volledig evalueerbare uitkomsten'
        lines.append(line)
        if h['states']:
            lines.append('  Uitkomsten: ' + ', '.join(f'{k}={v}' for k, v in sorted(h['states'].items())))
    lines += ['Onvolledige cohorten worden niet als succes of nulrendement meegeteld.',
              'Beschrijvende resultaten; overlappende perioden, geen bewijs van voorspellend voordeel.',
              'Baseline: nieuw gescoorde kandidaten. Historische scores zonder logboek worden niet als praktijkproef gebruikt.']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since', type=date.fromisoformat, default=date(2026, 11, 20))
    parser.add_argument('--top', type=int, default=10)
    parser.add_argument('--cost-bps', type=float, default=0)
    parser.add_argument('--output', default='results/xgboost_evaluation.json')
    parser.add_argument('--telegram', action='store_true')
    args = parser.parse_args()
    if args.top < 1 or not math.isfinite(args.cost_bps) or args.cost_bps < 0:
        parser.error('Top moet positief zijn; retourkosten moeten eindig en niet-negatief zijn')
    import psycopg2
    with psycopg2.connect(os.environ['SUPABASE_DB_URL']) as conn:
        rows, existing = load_predictions(conn, args.since)
    report = build_report(rows, as_of=datetime.now(timezone.utc).date(), top_n=args.top, cost_bps=args.cost_bps)
    report['existing_v3_scores'] = existing
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, default=str, ensure_ascii=False, allow_nan=False, indent=2))
    message = render(report)
    path.with_suffix('.md').write_text(message)
    print(message)
    if args.telegram:
        import requests
        response = requests.post('https://api.telegram.org/bot'+os.environ['TELEGRAM_TOKEN']+'/sendMessage',
                                 json=dict(chat_id=os.environ['TELEGRAM_CHAT_ID'], text=message), timeout=30)
        response.raise_for_status()
        if response.json().get('ok') is not True:
            raise RuntimeError('Telegram heeft de evaluatie niet bevestigd')


if __name__ == '__main__':
    main()
