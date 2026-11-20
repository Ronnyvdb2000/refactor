"""Fixed-horizon follow-up of archived baskets, not a stop-order backtest."""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
import math
import os
from pathlib import Path

from analyses.evaluate_xgboost import outcome, HORIZONS
from .allocation import AllocationPolicy


def evaluate_basket(row, bars, horizon, as_of):
    payload = row['payload']
    policy = AllocationPolicy(**payload['manifest']['policy'])
    positions = payload['simulation']['positions']
    selected = [p for p in positions if p['shares'] > 0]
    result = dict(basket_id=row['basket_id'], recorded_at=str(row['recorded_at']),
                  horizon=horizon, status='incomplete', n_positions=len(selected))
    if payload['manifest']['currency'] != 'USD':
        return dict(result, status='unsupported_currency')
    if not selected:
        return dict(result, status='empty_basket')
    if len({p['ticker'] for p in selected}) != len(selected):
        raise ValueError('Dubbele positie in opgeslagen mandje')
    details = []
    for position in selected:
        shares = position['shares']
        if not isinstance(shares, int) or shares < 1:
            raise ValueError('Ongeldig opgeslagen aantal aandelen')
        ticker = position['ticker']
        data = bars.get(ticker)
        if isinstance(data, Exception):
            value = dict(status='provider_error')
        elif isinstance(data, str) and data == 'pending':
            value = dict(status='pending')
        else:
            value = outcome(data, row['recorded_at'], HORIZONS[horizon], as_of)
        details.append(dict(ticker=ticker, shares=shares, **value))
    result.update(positions=details, states=dict(Counter(d['status'] for d in details)))
    if any(d['status'] != 'mature' for d in details):
        return result
    # Do not silently compare asynchronously entered or shortened positions.
    if len({(d['entry_date'], d['exit_date']) for d in details}) != 1:
        return dict(result, status='inconsistent_sessions')
    slip = policy.slippage_fraction
    for d in details:
        d['entry_with_slippage'] = d['entry_price'] * (1 + slip)
        d['exit_with_slippage'] = d['exit_price'] * (1 - slip)
    notional = sum(d['shares'] * d['entry_with_slippage'] for d in details)
    entry_fees = sum(policy.commission(d['shares'] * d['entry_with_slippage']) for d in details)
    # Preserve frozen quantities; reject an unaffordable next-open basket.
    if notional + entry_fees > policy.capital:
        return dict(result, status='entry_budget_exceeded')
    pnl = sum(d['shares'] * (d['exit_with_slippage'] - d['entry_with_slippage'])
              - policy.commission(d['shares'] * d['entry_with_slippage'])
              - policy.commission(d['shares'] * d['exit_with_slippage']) for d in details)
    equal_notional = notional / len(details)
    equal_entry_fees = len(details) * policy.commission(equal_notional)
    if notional + equal_entry_fees > policy.capital:
        return dict(result, status='baseline_budget_exceeded')
    baseline_pnl = 0.
    for d in details:
        # Fractional units form an analytical equal-dollar comparator, at the
        # same entry exposure, with identical commission/slippage assumptions.
        qty = equal_notional / d['entry_with_slippage']
        sale = qty * d['exit_with_slippage']
        baseline_pnl += sale - equal_notional - policy.commission(equal_notional) - policy.commission(sale)
    result.update(status='complete', entry_date=details[0]['entry_date'],
                  exit_date=details[0]['exit_date'], entry_notional=notional,
                  net_pnl=pnl, net_return_pct=100 * pnl / policy.capital,
                  baseline_net_pnl=baseline_pnl,
                  baseline_net_return_pct=100 * baseline_pnl / policy.capital,
                  excess_pct_points=100 * (pnl - baseline_pnl) / policy.capital)
    if not all(math.isfinite(result[k]) for k in ('net_pnl', 'baseline_net_pnl')):
        raise ValueError('Niet-eindige evaluatie')
    return result


def build_report(rows, as_of, fetch=None):
    import pandas as pd
    if fetch is None:
        import yfinance as yf
        def fetch(ticker, start, end):
            return yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True,
                                            repair=False, raise_errors=True)
    earliest = {}
    for row in rows:
        stamp = pd.Timestamp(row['recorded_at'])
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError('Opslagtijdstip moet een tijdzone bevatten')
        start = stamp.tz_convert('UTC').date() + timedelta(days=1)
        for p in row['payload']['simulation']['positions']:
            if p['shares'] > 0:
                earliest[p['ticker']] = min(start, earliest.get(p['ticker'], start))
    bars = {}
    for ticker, start in sorted(earliest.items()):
        # No download before even the shortest fixed horizon can be mature.
        if (as_of - start).days < 10:
            bars[ticker] = 'pending'
            continue
        try:
            bars[ticker] = fetch(ticker, start.isoformat(), as_of.isoformat())
        except Exception:
            bars[ticker] = RuntimeError('provider_error')
    results = [evaluate_basket(row, bars, h, as_of) for row in rows for h in HORIZONS]
    return dict(schema_version=1, as_of=as_of.isoformat(), baskets=len(rows),
                results=results, states=dict(Counter(r['status'] for r in results)),
                definition='Frozen quantities; adjusted open strictly after UTC recording day to Hth adjusted close. Current day excluded.',
                limitations=[
                    'Fixed horizons, no stop execution, reinvestment, broker fills or continuous portfolio.',
                    'Equal-dollar comparator uses fractional units on the same held stocks and entry exposure; cash earns zero.',
                    'Separate baskets overlap and are not independent; returns must not be added into a track record.',
                    'Adjusted Yahoo prices, not a corporate-action share ledger; provider gaps/delistings require review.',
                    'Missing or unaffordable baskets remain incomplete, never zero-return winners.'])


def render(report):
    lines = ['Onderzoek — opvolging opgeslagen mandjes',
             f"Peildatum {report['as_of']} | {report['baskets']} unieke mandjes"]
    for horizon in HORIZONS:
        items = [r for r in report['results'] if r['horizon'] == horizon]
        complete = [r for r in items if r['status'] == 'complete']
        lines.append(f"{horizon}: {len(complete)}/{len(items)} volledig")
        for r in complete:
            lines.append(f"  {r['basket_id'][:12]} ({r['entry_date']}–{r['exit_date']}): "
                         f"netto {r['net_return_pct']:+.2f}% | gelijk gewogen "
                         f"{r['baseline_net_return_pct']:+.2f}% | verschil {r['excess_pct_points']:+.2f} pp")
    lines.append('Status: ' + (', '.join(f'{s}={n}' for s, n in sorted(report['states'].items())) or 'nog geen logboek'))
    lines.append('Vaste meettermijnen, geen stopuitvoering. Overlappende mandjes zijn geen onafhankelijke resultaten.')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--telegram', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('results/research_evaluation.json'))
    args = parser.parse_args()
    import psycopg2
    from .journal import load
    conn = psycopg2.connect(os.environ['SUPABASE_DB_URL'], connect_timeout=20)
    try:
        rows = load(conn)
    finally:
        conn.close()
    report = build_report(rows, datetime.now(timezone.utc).date())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, allow_nan=False, indent=2), encoding='utf-8')
    message = render(report)
    args.output.with_suffix('.md').write_text(message, encoding='utf-8')
    print(message)
    if args.telegram:
        from .telegram import send_text
        send_text(message)


if __name__ == '__main__':
    main()
