"""Permanent daily model assessment: signal study and next-open follow-up.

Never compounds overlapping daily baskets. No retrospective row is promoted to a
prospective forecast. Missing outcomes keep the entire cohort incomplete.
"""
import argparse
from datetime import datetime, timezone
from itertools import combinations
import math
import os

import pandas as pd
from psycopg2.extras import RealDictCursor, execute_values
from scipy.stats import spearmanr
from analyses.evaluate_xgboost import outcome
from common.model_archive import number


def assess(rows, top_n=10, cost_bps=20):
    if top_n<1 or not math.isfinite(cost_bps) or cost_bps<0:
        raise ValueError('Ongeldige top-N of kostenscenario')
    ranked=sorted(rows,key=lambda r:(-float(r['score']),r['ticker']))
    if len({r['ticker'] for r in ranked})!=len(ranked):
        raise ValueError('Dubbele ticker in cohort')
    mature=[r for r in ranked if r['status']=='mature' and number(r.get('gross_pct')) is not None]
    result=dict(status='incomplete',n_scores=len(ranked),n_mature=len(mature),n_top=min(top_n,len(ranked)),
                top_gross_pct=None,top_net_pct=None,win_rate=None,baseline_pct=None,excess_pp=None,rank_ic=None)
    if not ranked or len(mature)!=len(ranked):
        return result
    values=[float(r['gross_pct']) for r in ranked]
    top=values[:top_n]
    gross=sum(top)/len(top)
    baseline=sum(values)/len(values)
    ic=None
    if len(ranked)>=3 and len(set(values))>1 and len({r['score'] for r in ranked})>1:
        ic=number(spearmanr([r['score'] for r in ranked],values).statistic)
    return dict(result,status='complete',top_gross_pct=gross,top_net_pct=gross-cost_bps/100,
                win_rate=sum(v>0 for v in top)/len(top),baseline_pct=baseline,
                excess_pp=gross-baseline,rank_ic=ic)


def compare(a,b,top_n=10,cost_bps=20):
    left={r['ticker']:r for r in a};right={r['ticker']:r for r in b}
    common=sorted(left.keys() & right.keys())
    first=assess([left[t] for t in common],top_n,cost_bps)
    second=assess([right[t] for t in common],top_n,cost_bps)
    result=dict(status='incomplete',n_common=len(common),n_mature=min(first['n_mature'],second['n_mature']),
                top_a_pct=None,top_b_pct=None,difference_pp=None,baseline_pct=None,ic_a=None,ic_b=None)
    # A comparison needs at least the chosen basket size, with identical entry/
    # exit sessions and return definitions. Never compare mismatched price bases.
    if len(common)<top_n or first['status']!='complete' or second['status']!='complete':
        return result
    for t in common:
        if (str(left[t].get('entry_date')),str(left[t].get('exit_date'))) != (
                str(right[t].get('entry_date')),str(right[t].get('exit_date'))):
            return dict(result,status='different_sessions')
        if not math.isclose(float(left[t]['gross_pct']),float(right[t]['gross_pct']),abs_tol=1e-8):
            return dict(result,status='different_prices')
    return dict(result,status='complete',top_a_pct=first['top_net_pct'],top_b_pct=second['top_net_pct'],
                difference_pp=first['top_net_pct']-second['top_net_pct'],baseline_pct=first['baseline_pct'],
                ic_a=first['rank_ic'],ic_b=second['rank_ic'])


def selection_outcome(row, as_of):
    """Use directly dated sel_par labels, never unverified copied labels."""
    end=row.get('label_date')
    if row.get('labels_review'):
        return dict(status='review')
    if not end or pd.Timestamp(end).date()>as_of:
        return dict(status='pending')
    gross=number(row.get('label_return'))
    basis=number(row.get('label_basis'))
    entry=number(row.get('entry_close'))
    # A stock/date may have inconsistent source prices. A frozen forecast must
    # not silently acquire another entry price after publication.
    if gross is None or basis is None or entry is None or entry<=0:
        return dict(status='missing_or_unverified')
    source=number(row.get('selection_close'))
    if source is None or not math.isclose(entry,source,rel_tol=1e-6):
        return dict(status='entry_conflict')
    return dict(status='mature',entry_date=str(row['datum']),exit_date=str(end),
                entry_price=basis,exit_price=number(row.get('label_close')),gross_pct=gross)


def load(conn):
    # Unpivot only the requested horizon. No joins to retired feature tables.
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT r.*,s.datum,s.ticker,s.score,s.rank,s.picked,s.entry_close,
            p.koers_selectiedatum AS selection_close,p.rendement_basis_koers AS label_basis,
            p.koersdatums->>r.horizon::text AS label_date,
            p.verrijking_status->>'labels_review' AS labels_review,
            CASE r.horizon WHEN 5 THEN p.rendement_5d_pct WHEN 10 THEN p.rendement_10d_pct
              WHEN 20 THEN p.rendement_20d_pct WHEN 30 THEN p.rendement_30d_pct
              WHEN 60 THEN p.rendement_60d_pct END AS label_return,
            CASE r.horizon WHEN 5 THEN p.koers_5d WHEN 10 THEN p.koers_10d
              WHEN 20 THEN p.koers_20d WHEN 30 THEN p.koers_30d WHEN 60 THEN p.koers_60d END AS label_close
            FROM public.ml_runs r JOIN public.ml_scores s ON s.run_id=r.id
            LEFT JOIN public.sel_par p ON p.ticker=s.ticker AND p.selectiedatum=s.datum
            ORDER BY r.recorded_at,r.id,s.datum,s.rank''')
        return [dict(r) for r in cur.fetchall()]


def run(conn, *, as_of=None, cost_bps=20, fetch=None):
    as_of=as_of or datetime.now(timezone.utc).date()
    rows=load(conn)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM public.ml_outcomes WHERE basis='next_open' AND status='mature'")
        saved={(str(r['run_id']),r['datum'],r['ticker']):dict(r) for r in cur.fetchall()}
    if fetch is None:
        import yfinance as yf
        def fetch(ticker,start,end):
            from common.yahoo import stop_on_throttle
            with stop_on_throttle():
                return yf.Ticker(ticker).history(start=start,end=end,auto_adjust=True,repair=False,
                                                  raise_errors=True,timeout=20)
    cache={}; grouped={}; writes=[]
    for row in rows:
        results={'selection_close':selection_outcome(row,as_of)}
        if row['mode']=='prospective':
            key=(str(row['id']),row['datum'],row['ticker'])
            if key in saved:
                results['next_open']=saved[key]
            elif (as_of-row['recorded_at'].astimezone(timezone.utc).date()).days<row['horizon']:
                results['next_open']=dict(status='pending')
            else:
                start=row['recorded_at'].astimezone(timezone.utc).date().isoformat()
                ck=(row['ticker'],start)
                try:
                    if ck not in cache:
                        cache[ck]=fetch(row['ticker'],start,as_of.isoformat())
                    if isinstance(cache[ck],Exception):
                        raise cache[ck]
                    value=outcome(cache[ck],row['recorded_at'],row['horizon'],as_of)
                    if 'gross_return_pct' in value:
                        value['gross_pct']=value.pop('gross_return_pct')
                    results['next_open']=value
                except Exception as exc:
                    from common.yahoo import YahooRateLimited
                    if isinstance(exc,YahooRateLimited):
                        raise
                    cache[ck]=RuntimeError('provider_error')
                    results['next_open']=dict(status='provider_error')
        for basis,value in results.items():
            values=[value.get(k) for k in ('status','entry_date','exit_date','entry_price','exit_price','gross_pct')]
            writes.append((row['id'],row['datum'],row['ticker'],basis,*values))
            grouped.setdefault((str(row['id']),row['datum'],basis),[]).append({**row,**value})
    with conn.cursor() as cur:
        if writes:
            execute_values(cur,'''INSERT INTO public.ml_outcomes
                (run_id,datum,ticker,basis,status,entry_date,exit_date,entry_price,exit_price,gross_pct) VALUES %s
                ON CONFLICT(run_id,datum,ticker,basis) DO UPDATE SET
                status=EXCLUDED.status,entry_date=EXCLUDED.entry_date,exit_date=EXCLUDED.exit_date,
                entry_price=EXCLUDED.entry_price,exit_price=EXCLUDED.exit_price,gross_pct=EXCLUDED.gross_pct,
                checked_at=clock_timestamp()
                WHERE ml_outcomes.basis='selection_close' OR ml_outcomes.status<>'mature' ''',writes)
        candidates={}; assessments=[]
        for (ident,datum,basis),group in grouped.items():
            meta=group[0]
            metrics=assess(group,meta['top_n'],cost_bps)
            cols=list(metrics)
            cur.execute('''INSERT INTO public.ml_eval
                (run_id,datum,basis,cost_bps,'''+','.join(cols)+''') VALUES ('''+','.join(['%s']*(4+len(cols)))+''')
                ON CONFLICT(run_id,datum,basis,cost_bps) DO UPDATE SET '''+
                ','.join(f'{c}=EXCLUDED.{c}' for c in cols)+',checked_at=clock_timestamp()',
                (ident,datum,basis,cost_bps,*metrics.values()))
            assessments.append(metrics)
            # First publication wins per version/target/date; reruns are not new
            # independent observations. Modes stay separate.
            key=(datum,basis,meta['horizon'],meta['mode'])
            candidates.setdefault(key,{}) .setdefault((meta['model'],meta['version'],meta['target']),group)
        compared=0
        for (datum,basis,_,_),versions in candidates.items():
            for a,b in combinations(versions.values(),2):
                if a[0]['model']==b[0]['model']:
                    continue
                result=compare(a,b,min(a[0]['top_n'],b[0]['top_n']),cost_bps)
                cols=list(result)
                cur.execute('''INSERT INTO public.ml_compare
                    (run_a,run_b,datum,basis,cost_bps,'''+','.join(cols)+''') VALUES ('''+','.join(['%s']*(5+len(cols)))+''')
                    ON CONFLICT(run_a,run_b,datum,basis,cost_bps) DO UPDATE SET '''+
                    ','.join(f'{c}=EXCLUDED.{c}' for c in cols)+',checked_at=clock_timestamp()',
                    (a[0]['id'],b[0]['id'],datum,basis,cost_bps,*result.values()))
                compared+=1
    return dict(scores=len(rows),cohorts=len(grouped),complete=sum(m['status']=='complete' for m in assessments),
                comparisons=compared,cost_bps=cost_bps)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cost-bps',type=float,default=20,help='Round-trip kostenscenario, geen gemeten kosten')
    args=parser.parse_args()
    if not math.isfinite(args.cost_bps) or args.cost_bps<0:
        parser.error('Kostenscenario moet eindig en niet negatief zijn')
    import psycopg2
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=20) as conn:
        result=run(conn,cost_bps=args.cost_bps)
    print('Modelbeoordeling opgeslagen in Supabase:',result)
    summary=os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a') as f:
            f.write('## Modelbeoordeling in Supabase\n\n'+str(result)+'\n\n'
                    'selectiekoers = signaalonderzoek; next_open = na publicatie. '
                    'Overlappende dagelijkse mandjes zijn geen onafhankelijke portefeuille.\n')


if __name__=='__main__':
    main()
