"""Exploratory historical walk-forward reconstruction; never a live paper trial.

Decisions/weights are frozen before fetching execution proxies. Adjusted Yahoo
OHLC are a total-return research proxy, not contemporaneous executable prices.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from io import StringIO
import time
import warnings

import pandas as pd
from psycopg2.extras import Json, RealDictCursor
from scipy.stats import ConstantInputWarning

from common.horizons import calendar_embargo
from common.model_archive import implementation_hash
from meta_models import a_par_model as model
from analyses.paper_trial import PROTOCOL, plan, simulate, metrics

US_VENUES = {'048 Nasdaq/NYSE','056 NYSE int','057 NYSE'}


def configuration(as_of):
    return dict(mode='historical_reconstruction', horizon=10, top_n=10, as_of=str(as_of),
                initial_cash=100000., commission_bps=5, slippage_bps=10,
                implementation_hash=implementation_hash(),
                backtest_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                model_config=PROTOCOL['model_config'],
                universe='historically recorded US venue; provider currency checked later without reselection',
                decision='selection_date_plus_one_calendar_day_06UTC',
                entry='first_XNYS_session_strictly_after_decision_UTC_date',
                training_labels='reconstructed_adjusted_close_at_actual_tenth_provider_session',
                quote_basis='Yahoo auto_adjust=True total_return_proxy; no extra dividends or splits',
                limitations=['Historical selections/features may have been reconstructed later',
                             'No immutable point-in-time historical feature archive',
                             'Adjusted research prices differ from live raw-price execution',
                             'Survivorship/provider revisions/delisted histories remain limitations',
                             'Short history, settings already chosen with knowledge of this dataset'],
                profitability_proven=False)


def prepare(frame, metadata, as_of, calendar=None):
    import exchange_calendars as xcals
    calendar = calendar or xcals.get_calendar('XNYS')
    cfg = model.ModelConfig()
    target='fwd_ret_10d'
    info=metadata.copy()
    info['datum']=pd.to_datetime(info['datum'])
    info['label_date']=pd.to_datetime(info['label_date'],errors='coerce')
    work=frame.merge(info,on=['ticker','datum'],validate='one_to_one')
    label_dates=work.pop('label_date')
    venues=work.pop('beurs')
    reviewed=work.pop('labels_review').notna()
    if work.duplicated(['ticker','datum']).any():
        raise ValueError('Dubbel aandeel/selectiedatum')
    if target not in work:
        raise ValueError('10d trainingsuitkomsten ontbreken')
    plans=[];skipped=[];last_exit=pd.Timestamp.min
    for day in sorted(work.datum.unique()):
        day=pd.Timestamp(day)
        decision=day+pd.Timedelta(days=1,hours=6)
        if day<=last_exit or decision.date()>=as_of:
            continue
        available=work.loc[work.datum<=day].copy()
        # Date of the actual tenth trading session, not an approximate elapsed-day test.
        known=(label_dates.notna() & (label_dates<decision.normalize()) & ~reviewed)
        available.loc[~known.loc[available.index],target]=float('nan')
        embargo=calendar_embargo(10,minimum=cfg.embargo_dagen)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',ConstantInputWarning)
            weights,window,start,end=model.ic_met_fallback(available,target,day,embargo,
                cfg.rolling_dagen,cfg.fallback_dagen,cfg.min_datums_ic,cfg.ic_max_abs)
        if weights.empty:
            skipped.append(dict(date=str(day.date()),reason='insufficient_training'));continue
        weights=weights.loc[weights.ic_abs>=cfg.min_ic].copy()
        if weights.empty:
            skipped.append(dict(date=str(day.date()),reason='no_selected_parameters'));continue
        ranked=model.score_voor_datum(available,day,weights,len(available))
        rows=[]
        for r in ranked.itertuples():
            idx=work.index[(work.ticker==r.ticker)&(work.datum==day)]
            venue=venues.loc[idx[0]]
            if venue in US_VENUES:
                rows.append(dict(ticker=r.ticker,score=float(r.score),currency='USD'))
        try:
            selected=plan({'a_par:a_par_model_v1':rows},['a_par:a_par_model_v1'],cfg.top_n)
        except ValueError:
            skipped.append(dict(date=str(day.date()),reason='insufficient_US_universe'));continue
        sessions=calendar.sessions_in_range(decision.date()+timedelta(days=1),decision.date()+timedelta(days=60))[:10]
        if len(sessions)!=10:
            raise ValueError('Handelskalender onvolledig')
        selected.update(selection_date=str(day.date()),decided_at=decision.tz_localize('UTC').isoformat(),
                        sessions=[str(s.date()) for s in sessions],window=window,
                        train_start=str(start.date()),train_end=str(end.date()),
                        parameters=json.loads(weights.to_json(orient='records')))
        plans.append(selected)
        last_exit=pd.Timestamp(sessions[-1]).tz_localize(None)
    return dict(plans=plans,skipped=skipped,rows=len(work),dates=int(work.datum.nunique()),
                input_hash=hashlib.sha256(pd.util.hash_pandas_object(frame,index=False).values.tobytes()).hexdigest(),
                data_start=str(work.datum.min().date()) if len(work) else None,
                data_end=str(work.datum.max().date()) if len(work) else None)


def reconstruct_targets(frame, metadata, quotes):
    """Derive dates and training returns from actual ordered provider sessions.

    Outcome columns never become factors. The later prepare() masks each target
    until its actual exit session is strictly before the assumed decision date.
    """
    work=frame.copy()
    info=metadata.copy()
    info['datum']=pd.to_datetime(info['datum'])
    mapping={}
    for ticker, value in quotes.items():
        bars=sorted(value.get('bars',[]),key=lambda r:r['day'])
        if len({r['day'] for r in bars})!=len(bars):
            continue
        days=[pd.Timestamp(r['day']) for r in bars]
        for d in work.loc[work.ticker==ticker,'datum'].unique():
            d=pd.Timestamp(d)
            prior=[i for i,day in enumerate(days) if day<=d]
            if not prior:
                continue
            i=prior[-1]
            if i+10>=len(bars) or (d-days[i]).days>7:
                continue
            if any((days[j+1]-days[j]).days>7 for j in range(i,i+10)):
                continue
            start,end=float(bars[i]['close']),float(bars[i+10]['close'])
            if not all(math.isfinite(x) and x>0 for x in (start,end)):
                continue
            mapping[ticker,d]=(100*(end/start-1),days[i+10])
    work['fwd_ret_10d']=[mapping.get((r.ticker,pd.Timestamp(r.datum)),(float('nan'),None))[0] for r in work.itertuples()]
    info['label_date']=[mapping.get((r.ticker,pd.Timestamp(r.datum)),(None,pd.NaT))[1] for r in info.itertuples()]
    return work,info


def evaluate(plans, saved, as_of):
    names=['a_par:a_par_model_v1','equal_universe','benchmark']
    capital=dict.fromkeys(names,100000.)
    nav=[];trades=[];periods=[];closed=0
    config={**PROTOCOL,'horizon':10,'top_n':10,'commission_bps':5,'slippage_bps':10}
    for snapshot in plans:
        # Explicit research-only complete-case filter; never used for prospective entry.
        snapshot = {**snapshot, 'skipped_entries': {t: dict(reason='incomplete_historical_prices')
            for book in snapshot['books'].values() for t in book
            if any(not any(r['day']==d and all(math.isfinite(float(r[k])) and float(r[k])>0 for k in ('open','close'))
                           for r in saved.get(t,{}).get('bars',[]))
                   for d in snapshot['sessions'] if d < str(as_of))}}
        members={t for book in snapshot['books'].values() for t in book}
        prices={}
        for t in members:
            value=saved.get(t,{})
            for row in value.get('bars',[]):
                if row['day']>snapshot['decided_at'][:10]:
                    prices[t,row['day']]=dict(open=row['open'],close=row['close'],currency=value.get('currency'),dividend=0.,split=0.)
        value=simulate(snapshot,prices,capital,config,as_of=as_of)
        periods.append(dict(selection_date=snapshot['selection_date'],planned_entry=snapshot['sessions'][0],
                            planned_exit=snapshot['sessions'][-1],universe=len(snapshot['universe']),
                            top=snapshot['books']['a_par:a_par_model_v1'],status=value['status'],
                            skipped_entries=value.get('skipped_entries',{}),
                            allocations=value.get('allocations',{}),
                            starting_capital=capital,ending=value['ending']))
        nav.extend(value['nav']);trades.extend(value.get('trades',[]))
        if value['status']!='complete':
            break  # Never compound across an unresolved cohort or select only successful periods.
        capital=value['ending'];closed+=1
    report=metrics(nav,trades,100000.,closed)
    report.update(mode='historical_reconstruction',periods=periods,closed_batches=closed,
                  planned_batches=len(plans),stopped_at=periods[-1]['status'] if periods else 'no_plans',
                  capital=capital,profitability_proven=False,
                  missing_price_policy='historical_complete_case_fixed_slots_cash',
                  coverage_bias='Historical exclusions use later quote availability; descriptive research only')
    return report


def fetch(ticker,start,end):
    import yfinance as yf
    from common.yahoo import stop_on_throttle
    with stop_on_throttle():
        obj=yf.Ticker(ticker)
        bars=obj.history(start=start,end=end,auto_adjust=True,actions=True,repair=False,
                         raise_errors=True,timeout=20)
        currency=obj.history_metadata.get('currency')
    return dict(currency=currency,bars=[dict(day=str(stamp.date()),open=float(row['Open']),close=float(row['Close']))
                                      for stamp,row in bars.iterrows()
                                      if all(math.isfinite(float(row[k])) and float(row[k])>0 for k in ('Open','Close'))])


def run(conn, key, as_of=None, provider=fetch, budget_minutes=40):
    requested_as_of=as_of
    as_of=as_of or datetime.now(timezone.utc).date()
    config=configuration(as_of)
    conn.set_session(isolation_level='REPEATABLE READ')
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT pg_try_advisory_lock(741206,42) AS locked')
        if not cur.fetchone()['locked']:
            raise RuntimeError('Backtest is al bezig')
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM public.research_backtests WHERE run_key=%s',(key,))
            existing=cur.fetchone()
        if existing and requested_as_of is None:
            as_of=date.fromisoformat(existing['config']['as_of'])
            config=configuration(as_of)
        if existing and existing['config']!=config:
            raise ValueError('Backtestdefinitie/datum gewijzigd; start een nieuwe run')
        if existing:
            selection=existing['selection_snapshot']
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT ticker,data FROM public.research_backtest_cache WHERE run_key=%s',(key,))
                saved={r['ticker']:r['data'] for r in cur.fetchall()}
        else:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout='10min'")
            frame=model.get_data(conn,max_datum=pd.Timestamp(as_of))
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT ticker,selectiedatum AS datum,beurs,koersdatums->>'10' AS label_date, "
                            "verrijking_status->>'labels_review' AS labels_review FROM public.sel_par")
                metadata=pd.DataFrame([dict(r) for r in cur.fetchall()])
            selection=dict(raw_frame=json.loads(frame.to_json(orient='split',date_format='iso',double_precision=15)),
                           dtypes={c:str(t) for c,t in frame.dtypes.items()},
                           metadata=json.loads(metadata.to_json(orient='split',date_format='iso')),plans=None)
            saved={}
            with conn.cursor() as cur:
                cur.execute('INSERT INTO public.research_backtests(run_key,source_sha,config,selection_snapshot) VALUES(%s,%s,%s,%s)',
                            (key,os.getenv('GITHUB_SHA'),Json(config),Json(selection)))
            conn.commit()  # Historical source inputs are frozen before any provider retrieval.
        frame=pd.read_json(StringIO(json.dumps(selection['raw_frame'])),orient='split')
        for column,kind in selection.get('dtypes',{}).items():
            if kind=='object':
                frame[column]=frame[column].astype(object)
        frame['datum']=pd.to_datetime(frame['datum'])
        metadata=pd.read_json(StringIO(json.dumps(selection['metadata'])),orient='split')
        tickers=sorted(set(frame.ticker)|{'SPY'})
        deadline=time.monotonic()+budget_minutes*60
        start=str((frame.datum.min()-pd.Timedelta(days=10)).date())
        for t in tickers:
            if t in saved and 'error' not in saved[t]:
                continue
            if time.monotonic()>=deadline:
                break
            try:
                value=provider(t,start,str(as_of))
                if not value['bars']:
                    raise ValueError('missing_prices')
            except Exception as exc:
                value=dict(error=type(exc).__name__,bars=[])
                from common.yahoo import YahooRateLimited
                stopped=isinstance(exc,YahooRateLimited)
            else:
                stopped=False
            saved[t]=value
            with conn.cursor() as cur:
                cur.execute('INSERT INTO public.research_backtest_cache(run_key,ticker,data) VALUES(%s,%s,%s) '
                            'ON CONFLICT(run_key,ticker) DO UPDATE SET data=EXCLUDED.data '
                            "WHERE research_backtest_cache.data ? 'error'",(key,t,Json(value)))
            conn.commit()
            if stopped:
                break
        if len(saved)<len(tickers):
            result=dict(mode='historical_reconstruction',status='partial',quotes_cached=len(saved),
                        tickers_requested=len(tickers),closed_batches=0,books={},profitability_proven=False)
            with conn.cursor() as cur:
                cur.execute("UPDATE public.research_backtests SET status='partial',result=%s WHERE run_key=%s",(Json(result),key))
            conn.commit()
            return dict(run_key=key,**result)
        if selection.get('plans') is None:
            rebuilt,dated=reconstruct_targets(frame,metadata,saved)
            prepared=prepare(rebuilt,dated,as_of)
            selection.update(prepared)
            with conn.cursor() as cur:
                cur.execute('UPDATE public.research_backtests SET selection_snapshot=%s WHERE run_key=%s',(Json(selection),key))
            conn.commit()  # Freeze all ranks/weights before the execution evaluator sees future quotes.
        plans=selection['plans']
        result=evaluate(plans,saved,as_of)
        result.update(data_start=selection['data_start'],data_end=selection['data_end'],
                      rows=selection['rows'],dates=selection['dates'],quotes_cached=len(saved),
                      tickers_requested=len(tickers),quote_errors={t:v['error'] for t,v in saved.items() if 'error' in v},
                      limitations=config['limitations'],quote_basis=config['quote_basis'])
        result['evidence_status']=result.pop('status')
        status='complete' if result['planned_batches']==result['closed_batches'] else 'partial'
        if not plans:
            status='insufficient_history'
        if result['stopped_at']=='open' or result['stopped_at']=='pending':
            status='pending_final_period'
        with conn.cursor() as cur:
            cur.execute('UPDATE public.research_backtests SET status=%s,result=%s,updated_at=clock_timestamp() WHERE run_key=%s',
                        (status,Json(result),key))
        conn.commit()
        return {**result,'run_key':key,'status':status}
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_unlock(741206,42)')
        conn.commit()


def main():
    import psycopg2
    key='a_par_10d:'+os.getenv('GITHUB_RUN_ID',datetime.now(timezone.utc).isoformat())
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=20) as conn:
        source=os.getenv('BACKTEST_SOURCE','fresh')
        if source=='cached':
            from analyses.backtest_replay import run_cached
            result=run_cached(conn,key)
        elif source=='fresh':
            result=run(conn,key)
        else:
            raise ValueError('Onbekende backtestbron')
    print(json.dumps(result,sort_keys=True,allow_nan=False))
    summary=os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a') as f:
            f.write('## Historische reconstructie — geen prospectief bewijs\n\n```json\n'+json.dumps(result,indent=2)+'\n```\n')
    if result['status']=='partial':
        raise SystemExit('Backtest onvolledig; voortgang in Supabase bewaard')


if __name__=='__main__':
    main()
