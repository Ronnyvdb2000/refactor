"""EUR execution variant; the existing live USD paper engine is unchanged."""
from common.costs import execution_rates, bps_fraction
from datetime import datetime, timezone
import math

from analyses.paper_trial import positive


def plan(rows,top_n=10,benchmark='EXSA.DE'):
    if len({r['ticker'] for r in rows})!=len(rows) or any(not math.isfinite(float(r['score'])) for r in rows):
        raise ValueError('Dubbele ticker of ongeldige score')
    if any(r.get('currency')!='EUR' for r in rows):
        raise ValueError('De Europese proef rekent alleen in EUR')
    if len(rows)<top_n:
        raise ValueError('Te weinig Europese kandidaten')
    rows=sorted(rows,key=lambda r:r['ticker'])
    return dict(universe=[r['ticker'] for r in rows],
                books={'a_par:a_par_model_v1':[r['ticker'] for r in sorted(rows,key=lambda r:(-r['score'],r['ticker']))[:top_n]],
                       'equal_universe':[r['ticker'] for r in rows],'benchmark':[benchmark]},
                candidates={'a_par:a_par_model_v1':rows})


def simulate(snapshot, prices, initial, config, as_of=None):
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
        if (not bar or bar.get('currency') != 'EUR' or not positive(bar.get('open'))
                or not positive(bar.get('close'))):
            skipped.setdefault(t, dict(day=calendar[0], reason='missing_entry_price'))
    active = tickers - set(skipped)
    for d in calendar:
        for t in active:
            bar = prices.get((t, d))
            if (not bar or bar.get('currency') != 'EUR' or not positive(bar.get('open'))
                    or not positive(bar.get('close')) or not math.isfinite(bar.get('dividend', float('nan')))
                    or bar['dividend'] < 0):
                return dict(status='missing_prices', ticker=t, day=d, nav=[], ending=initial,
                            skipped_entries=skipped, unresolved_holding=True)
            if bar.get('split', 0) != 0:
                return dict(status='corporate_action_review', ticker=t, day=d, nav=[], ending=initial)
    fee, slip = execution_rates(config)
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
        fx_fees={t:bps_fraction(prices[t,calendar[0]].get('fx_fee_bps',0)) for t in members}
        cash = capital - slot * len(members)
        if cash < -1e-7:
            raise ValueError('Negatief saldo bij aankoop')
        for t in members:
            price = prices[t, calendar[0]]['open'] * (1 + slip)
            quantities[t] = slot / (price * (1 + fee + fx_fees[t]))
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
                    cash += proceeds * (1-fee-fx_fees[t])
                    turnover += proceeds
                    trades.append(dict(book=book, ticker=t, entry=calendar[0], exit=d,
                                       cost=buy_cost[t], net_pnl=proceeds*(1-fee-fx_fees[t])+dividends[t]-buy_cost[t]))
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


