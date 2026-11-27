"""Prospective, fixed-protocol paper portfolios; no orders or notifications."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import re
import uuid

from psycopg2.extras import Json, RealDictCursor, execute_values

PROTOCOL = dict(version=2, horizon=10, top_n=10, initial_cash=100000.0,
                currency='USD', benchmark='SPY', commission_bps=5, slippage_bps=10,
                max_prediction_age_days=3, minimum_closed_batches=12,
                models=[dict(model='a_par', version='a_par_model_v1')],
                execution='next_session_after_snapshot_utc_date',
                rebalance='nonoverlapping_10_sessions', fractional_shares=True,
                split_policy='block_entire_batch_for_review',
                missing_entry_policy='skip_fixed_slot_to_cash',
                dividend_policy='gross_ex_date_cash_proxy_no_tax_or_payment_lag',
                benchmark_policy='SPY_same_entry_exit_and_costs_not_continuous_buy_hold',
                evidence='descriptive_only_no_automatic_promotion',
                model_config=dict(top_n=10,min_ic=0.02,rolling_dagen=28,fallback_dagen=90,
                                  embargo_dagen=25,ic_max_abs=0.15,min_datums_ic=10,max_data_leeftijd=5))


from common.model_archive import implementation_hash

PROTOCOL['implementation_hash'] = implementation_hash()
PROTOCOL['engine_hash'] = hashlib.sha256(__import__('pathlib').Path(__file__).read_bytes()).hexdigest()
TRIAL = 'paper_10d_usd_v2'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def positive(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def plan(candidates, models, top_n):
    """Currency eligibility is determined at snapshot, never using later returns."""
    maps = {name: {r['ticker']: r for r in rows} for name, rows in candidates.items()}
    if set(maps) != set(models):
        raise ValueError('Niet alle vooraf vastgelegde modellen beschikbaar')
    for name, rows in candidates.items():
        if len(maps[name]) != len(rows) or any(not math.isfinite(float(r['score'])) for r in rows):
            raise ValueError('Dubbele ticker of ongeldige voorspelling')
    common = sorted(set.intersection(*(set(m) for m in maps.values())))
    common = [t for t in common if all(maps[m][t].get('currency') == 'USD' for m in models)]
    if len(common) < top_n:
        raise ValueError('Te weinig geverifieerde gemeenschappelijke USD-kandidaten')
    books = {m: sorted(common, key=lambda t: (-float(maps[m][t]['score']), t))[:top_n] for m in models}
    books.update(equal_universe=common, benchmark=['SPY'])
    return dict(universe=common, books=books,
                candidates={m: [maps[m][t] for t in common] for m in models})


def simulate(snapshot, prices, initial, config=PROTOCOL, as_of=None):
    """One shared capital pool per book, no new basket until the prior one closes.

    Missing entry quotes leave their original slot in cash. Quotes missing after
    purchase remain unresolved holdings. Splits require review rather
    than silently mixing Yahoo's split-adjusted history with frozen real prices.
    """
    as_of = as_of or datetime.now(timezone.utc).date().isoformat()
    calendar = [d for d in snapshot['sessions'] if d < str(as_of)]
    if len(snapshot['sessions']) != config['horizon'] or calendar != sorted(set(calendar)):
        raise ValueError('Ongeldige bevroren handelskalender')
    if any(r.get('split',0) != 0 for r in prices.values()):
        return dict(status='corporate_action_review', nav=[], ending=initial)
    if not calendar:
        return dict(status='pending', nav=[], ending=initial)
    skipped = dict(snapshot.get('skipped_entries', {}))
    tickers = set(t for book in snapshot['books'].values() for t in book)
    for t in sorted(tickers):
        bar = prices.get((t, calendar[0]))
        if (not bar or bar.get('currency') != 'USD' or not positive(bar.get('open'))
                or not positive(bar.get('close'))):
            skipped.setdefault(t, dict(day=calendar[0], reason='missing_entry_price'))
    active = tickers - set(skipped)
    for d in calendar:
        for t in active:
            bar = prices.get((t, d))
            if (not bar or bar.get('currency') != 'USD' or not positive(bar.get('open'))
                    or not positive(bar.get('close')) or not math.isfinite(bar.get('dividend', float('nan')))
                    or bar['dividend'] < 0):
                return dict(status='missing_prices', ticker=t, day=d, nav=[], ending=initial,
                            skipped_entries=skipped, unresolved_holding=True)
            if bar.get('split', 0) != 0:
                return dict(status='corporate_action_review', ticker=t, day=d, nav=[], ending=initial)
    fee = config['commission_bps'] / 10000
    slip = config['slippage_bps'] / 10000
    nav, ending, trades = [], {}, []
    complete = len(calendar) == config['horizon']
    for book, members in snapshot['books'].items():
        planned_members = members
        members = [t for t in members if t not in skipped]
        capital = initial[book]
        if not positive(capital) or len(planned_members) != len(set(planned_members)) or not planned_members:
            raise ValueError('Ongeldig kapitaal of mandje')
        slot = capital / (config['top_n'] if book not in ('equal_universe', 'benchmark') else len(planned_members))
        if slot > capital + 1e-8:
            raise ValueError('Meer kapitaal gevraagd dan beschikbaar')
        quantities, buy_cost, turnover = {}, {}, 0.0
        cash = capital - slot * len(members)
        if cash < -1e-7:
            raise ValueError('Negatief saldo bij aankoop')
        for t in members:
            price = prices[t, calendar[0]]['open'] * (1 + slip)
            quantities[t] = slot / (price * (1 + fee))
            buy_cost[t] = slot
            turnover += quantities[t] * price
        dividends = {t: 0.0 for t in members}
        for i, d in enumerate(calendar):
            # No dividend on entry day: shares bought on the ex-date have no entitlement.
            if i > 0:
                for t in members:
                    div = quantities[t] * prices[t, d]['dividend']
                    dividends[t] += div
                    cash += div
            value = sum(quantities[t] * prices[t, d]['close'] for t in members)
            if complete and i == config['horizon'] - 1:
                for t in members:
                    proceeds = quantities[t] * prices[t, d]['close'] * (1-slip)
                    cash += proceeds * (1-fee)
                    turnover += proceeds
                    trades.append(dict(book=book, ticker=t, entry=calendar[0], exit=d,
                                       cost=buy_cost[t], net_pnl=proceeds*(1-fee)+dividends[t]-buy_cost[t]))
                value = 0.0
            nav.append(dict(book=book, day=d, cash=cash, equity=cash+value,
                            invested=value, positions=len(members) if value else 0))
        ending[book] = cash if complete else cash+value
        trades_for_book = [t for t in trades if t['book'] == book]
        if complete:
            for t in trades_for_book:
                t['turnover_notional_book'] = turnover
    return dict(status='complete' if complete else 'open', nav=nav, ending=ending,
                entry=calendar[0], exit=calendar[-1] if complete else None, trades=trades,
                skipped_entries=skipped, allocations={book: dict(planned=len(members),
                    purchased=sum(t not in skipped for t in members),
                    invested_budget=initial[book] / (config['top_n'] if book not in ('equal_universe','benchmark') else len(members)) * sum(t not in skipped for t in members))
                    for book,members in snapshot['books'].items()})


def metrics(nav, trades, initial_cash, closed_batches, minimum=12):
    books = sorted({r['book'] for r in nav})
    result = {}
    for book in books:
        series = sorted([r for r in nav if r['book'] == book], key=lambda r: r['day'])
        if len({r['day'] for r in series}) != len(series):
            raise ValueError('Overlappende portefeuilledagen')
        peak = initial_cash
        drawdown = 0.0
        for row in series:
            peak = max(peak, row['equity'])
            drawdown = min(drawdown, row['equity']/peak-1)
        own = [r for r in trades if r['book'] == book]
        turnover_by_exit = {r['exit']: r['turnover_notional_book'] for r in own}
        result[book] = dict(net_return_pct=100*(series[-1]['equity']/initial_cash-1),
                            max_drawdown_pct=100*drawdown, ending_equity=series[-1]['equity'],
                            win_rate=(sum(r['net_pnl']>0 for r in own)/len(own) if own else None),
                            closed_positions=len(own),
                            turnover_over_initial=sum(turnover_by_exit.values())/initial_cash,
                            max_positions=max(r['positions'] for r in series),
                            valued_through=series[-1]['day'])
    for book, value in result.items():
        value['excess_vs_benchmark_pp'] = (value['net_return_pct']-result['benchmark']['net_return_pct']
                                            if 'benchmark' in result else None)
        value['excess_vs_universe_pp'] = (value['net_return_pct']-result['equal_universe']['net_return_pct']
                                           if 'equal_universe' in result else None)
    return dict(status='insufficient_history' if closed_batches<minimum else 'descriptive_only',
                closed_batches=closed_batches, required_batches=minimum, books=result,
                profitability_proven=False)


def load_candidates(conn, trial, now):
    config = trial['protocol']
    result, provenance = {}, {}
    cutoff = now-timedelta(days=config['max_prediction_age_days'])
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        for model in config['models']:
            cur.execute('''SELECT r.*, max(s.datum) AS score_day FROM public.ml_runs r
                JOIN public.ml_scores s ON s.run_id=r.id
                WHERE r.mode='prospective' AND r.horizon=10 AND r.model=%s AND r.version=%s
                  AND r.recorded_at>=%s AND r.recorded_at>=%s AND r.recorded_at<=%s
                  AND r.train_end IS NOT NULL
                GROUP BY r.id ORDER BY r.recorded_at DESC,r.id LIMIT 1''',
                (model['model'], model['version'], trial['started_at'], cutoff, now))
            run = cur.fetchone()
            if not run:
                return {}, {}
            if (run['config'].get('implementation_hash') != config['implementation_hash']
                    or run['config'].get('model_config') != config['model_config']
                    or run['config'].get('explicit_date') is not None):
                return {}, {}
            key = model['model']+':'+model['version']
            if (run['score_day'] > now.date() or run['train_end'] >= run['score_day']
                    or (now.date()-run['score_day']).days > config['max_prediction_age_days']
                    or run['config'].get('comparison_eligible') is False):
                return {}, {}
            cur.execute('SELECT ticker,score FROM public.ml_scores WHERE run_id=%s AND datum=%s ORDER BY rank',
                        (run['id'], run['score_day']))
            result[key] = [dict(r) for r in cur.fetchall()]
            cur.execute('SELECT parameter,kind,selected,weight,ic,ic_raw,ir,n_dates,n_obs FROM public.ml_params WHERE run_id=%s ORDER BY parameter', (run['id'],))
            parameters = [dict(r) for r in cur.fetchall()]
            provenance[key] = dict(parameters=parameters, run_id=str(run['id']), selection_date=str(run['score_day']),
                                   recorded_at=run['recorded_at'].isoformat(),
                                   train_start=str(run['train_start']), model_hash=run['model_hash'], cohort_hash=run['cohort_hash'],
                                   source_sha=run['source_sha'], train_end=str(run['train_end']), config=run['config'])
    if len({r['selection_date'] for r in provenance.values()}) != 1:
        return {}, {}
    return result, provenance


def provider(ticker, start, end):
    import yfinance as yf
    from common.yahoo import stop_on_throttle
    with stop_on_throttle():
        obj = yf.Ticker(ticker)
        bars = obj.history(start=start, end=end, auto_adjust=False, repair=False,
                           actions=True, raise_errors=True, timeout=20)
        currency = obj.history_metadata.get('currency')
    return bars, currency


def freeze_batch(conn, trial, now, fetch=provider):
    candidates, provenance = load_candidates(conn, trial, now)
    if not candidates:
        return None
    # Refuse a retrospective repeated forecast, even in another daily archive run.
    with conn.cursor() as cur:
        cur.execute('SELECT snapshot FROM public.paper_batches WHERE trial_id=%s', (trial['id'],))
        previous = [r[0] for r in cur.fetchall()]
    days = {r['selection_date'] for r in provenance.values()}
    if any(days & {r['selection_date'] for r in p['provenance'].values()} for p in previous):
        return None
    eligibility = {}
    # Scope excludes international suffixes up front; USD is still checked via provider metadata.
    tickers = sorted({r['ticker'] for rows in candidates.values() for r in rows})
    for t in tickers:
        currency = None
        if re.fullmatch(r'[A-Z][A-Z0-9-]{0,11}', t):
            try:
                _, currency = fetch(t, (now.date()-timedelta(days=5)).isoformat(), now.date().isoformat())
            except Exception:
                currency = None
        eligibility[t] = currency
    for rows in candidates.values():
        for r in rows:
            r['currency'] = eligibility[r['ticker']]
    try:
        snapshot = plan(candidates, list(provenance), trial['protocol']['top_n'])
    except ValueError:
        return None
    # Metadata fetch may take time. Freeze AFTER it ends; never use its old time as entry boundary.
    decided = datetime.now(timezone.utc)
    import exchange_calendars as xcals
    calendar=xcals.get_calendar('XNYS')
    sessions=calendar.sessions_in_range(decided.date()+timedelta(days=1),decided.date()+timedelta(days=60))
    snapshot.update(decided_at=decided.isoformat(), provenance=provenance,
                    sessions=[str(d.date()) for d in sessions[:trial['protocol']['horizon']]],
                    calendar_version=xcals.__version__,
                    eligibility=eligibility, protocol_hash=digest(trial['protocol']))
    ident = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute('''INSERT INTO public.paper_batches(id,trial_id,snapshot,snapshot_hash)
                       VALUES(%s,%s,%s,%s)''', (ident, trial['id'], Json(snapshot), digest(snapshot)))
    conn.commit()  # Immutable decision is durable BEFORE any execution-price retrieval.
    return ident


def refresh_prices(conn, batch, now, fetch=provider):
    snapshot = batch['snapshot']
    start = datetime.fromisoformat(snapshot['decided_at']).date()
    tickers = sorted({t for v in snapshot['books'].values() for t in v})
    for t in tickers:
        try:
            bars, currency = fetch(t, start.isoformat(), now.date().isoformat())
        except Exception:
            continue  # One unavailable symbol must not prevent refreshing the others.
        if currency != 'USD' or bars is None or bars.empty:
            continue
        values = []
        for stamp, row in bars.iterrows():
            day = stamp.date()
            if not start < day < now.date():
                continue
            op, close = float(row['Open']), float(row['Close'])
            div, split = float(row.get('Dividends', 0)), float(row.get('Stock Splits', 0))
            if not positive(op) or not positive(close) or not all(math.isfinite(v) and v>=0 for v in (div,split)):
                continue
            values.append((batch['id'], t, day, op, close, div, split, currency))
        if values:
            with conn.cursor() as cur:
                execute_values(cur, '''INSERT INTO public.paper_prices
                    (batch_id,ticker,day,open,close,dividend,split,currency) VALUES %s
                    ON CONFLICT(batch_id,ticker,day) DO NOTHING''', values)
            conn.commit()  # Preserve partial provider progress; never replace frozen prices.


def run(conn, now=None, fetch=provider):
    now = now or datetime.now(timezone.utc)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT pg_try_advisory_lock(741206,40) AS locked')
        if not cur.fetchone()['locked']:
            raise RuntimeError('Paperproef is al bezig')
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM public.paper_trials WHERE id=%s', (TRIAL,))
            trial = dict(cur.fetchone())
        if trial['protocol'] != PROTOCOL:
            raise ValueError('Protocol wijkt af; maak een afzonderlijke proefversie')
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM public.paper_batches WHERE trial_id=%s ORDER BY created_at,id', (TRIAL,))
            batches = [dict(r) for r in cur.fetchall()]
        book_names = [m['model']+':'+m['version'] for m in PROTOCOL['models']] + ['equal_universe','benchmark']
        capital = dict.fromkeys(book_names, PROTOCOL['initial_cash'])
        all_nav, all_trades, closed, blocked = [], [], 0, None
        for batch in batches:
            if batch['snapshot_hash'] != digest(batch['snapshot']):
                raise ValueError('Beslissingssnapshot gewijzigd')
            if batch['status'] != 'complete':
                refresh_prices(conn, batch, now, fetch)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT * FROM public.paper_prices WHERE batch_id=%s', (batch['id'],))
                prices = {(r['ticker'], str(r['day'])):dict(r) for r in cur.fetchall()}
            snapshot = {**batch['snapshot'], 'skipped_entries': batch['result'].get('skipped_entries', {})}
            value = simulate(snapshot, prices, capital, as_of=now.date())
            with conn.cursor() as cur:
                cur.execute('UPDATE public.paper_batches SET status=%s,result=%s WHERE id=%s',
                            (value['status'], Json(value), batch['id']))
                if value['nav']:
                    execute_values(cur, '''INSERT INTO public.paper_nav(batch_id,book,day,equity,cash,invested,positions)
                        VALUES %s ON CONFLICT(batch_id,book,day) DO NOTHING''',
                        [(batch['id'], r['book'], r['day'], r['equity'], r['cash'], r['invested'],r['positions']) for r in value['nav']])
            conn.commit()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT book,day,equity,cash,invested,positions FROM public.paper_nav WHERE batch_id=%s ORDER BY day,book', (batch['id'],))
                all_nav.extend({**dict(r),'day':str(r['day'])} for r in cur.fetchall())
            all_trades.extend(value.get('trades', []))
            if value['status'] != 'complete':
                blocked = value['status']; break
            capital = value['ending']; closed += 1
        new_batch = None
        if not blocked:
            new_batch = freeze_batch(conn, trial, now, fetch)
        result = metrics(all_nav, all_trades, PROTOCOL['initial_cash'], closed,
                         PROTOCOL['minimum_closed_batches'])
        result.update(trial=TRIAL, protocol_hash=digest(PROTOCOL), active_status=blocked,
                      new_batch=new_batch, capital=capital, checked_at=now.isoformat())
        with conn.cursor() as cur:
            cur.execute('INSERT INTO public.paper_reports(trial_id,report) VALUES(%s,%s)', (TRIAL, Json(result)))
        conn.commit()
        return result
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_unlock(741206,40)')
        conn.commit()


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    import psycopg2
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'], connect_timeout=20) as conn:
        report = run(conn)
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    summary = os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as f:
            f.write('## Vaste paperproef 10d\n\n```json\n'+json.dumps(report, indent=2)+'\n```\n')
    # Missing held-position quotes are reported and retried without failing the workflow.


if __name__ == '__main__':
    main()
