"""European A Parameter reconstruction: EUR portfolios and shared European sessions."""
from common.costs import execution_defaults, execution_rates, FX_BPS_PER_SIDE
from datetime import date,datetime,timedelta,timezone
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
from common.reproducibility import source_fingerprint
import warnings

import pandas as pd
from psycopg2.extras import Json,RealDictCursor
from scipy.stats import ConstantInputWarning

from analyses import backtest_parameter as native
from analyses.paper_trial import metrics
from analyses.europe_execution import plan,simulate
from analyses.europe_market import BENCHMARK,FX,CALENDARS,CommonCalendar,eligible,convert,valid_price
from common.horizons import horizon_days,calendar_embargo
from meta_models import a_par_model as model


def configuration(as_of,horizon=10):
    root=Path(__file__).resolve().parents[1]
    files=['analyses/backtest_europe.py','analyses/europe_market.py','analyses/europe_execution.py',
           'requirements-europe.txt','requirements-elastic-net.txt']
    files += ['common/model_definitions.py','common/costs.py','requirements-paper.txt']
    return {**native.configuration(as_of,horizon),
            'mode':'historical_europe_parameter','currency':'EUR','benchmark':BENCHMARK,
            'europe_hash':source_fingerprint(root, files),
            'universe':'historical European venue and predeclared supported ticker suffix; no future price selection',
            'entry':'first_common_European_session_strictly_after_decision_UTC_date',
            'calendar_policy':'intersection_of_all_predeclared_covered_European_exchanges',
            'calendars':CALENDARS,'fx_pairs':FX,'fx_fee_bps_each_side':FX_BPS_PER_SIDE,
            'benchmark_policy':'STOXX_Europe_600_ETF_EXSA.DE_same_dates_and_costs_EUR_total_return_proxy',
            'training_policy':'unchanged_global_parameter_IC_on_local_currency_training_returns',
            'library_versions':{name:version(name) for name in ('exchange-calendars','pandas','numpy')},
            'limitations':native.configuration(as_of,horizon)['limitations']+[
                'Daily stock/FX open-close proxies are not synchronized executable prices',
                'Common-European-session horizon differs from a single exchange session horizon',
                'ETF benchmark includes its own fund costs and tracking difference',
                'Training objective remains local-currency return; execution portfolio is EUR',
                'Unsupported suffixes excluded before outcome evaluation; cash omissions may bias comparison']}


def prepare(frame, metadata, as_of, calendar=None, horizon=10):
    horizon=horizon_days(horizon)
    as_of=date.fromisoformat(str(as_of))
    calendar = calendar or CommonCalendar(str(frame.datum.min().date()),str(as_of+timedelta(days=180)))
    cfg = model.ModelConfig()
    target=f'fwd_ret_{horizon}d'
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
        raise ValueError(f'{horizon}d trainingsuitkomsten ontbreken')
    plans=[];skipped=[];last_exit=pd.Timestamp.min
    for day in sorted(work.datum.unique()):
        day=pd.Timestamp(day)
        decision=day+pd.Timedelta(days=1,hours=6)
        if day<=last_exit or decision.date()>=as_of:
            continue
        available=work.loc[work.datum<=day].copy()
        # Actual horizon exit date, not an approximate elapsed-day test.
        known=(label_dates.notna() & (label_dates<decision.normalize()) & ~reviewed)
        available.loc[~known.loc[available.index],target]=float('nan')
        embargo=calendar_embargo(horizon,minimum=cfg.embargo_dagen)
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
            if eligible(r.ticker,venue):
                rows.append(dict(ticker=r.ticker,score=float(r.score),currency='EUR'))
        try:
            selected=plan(rows,cfg.top_n,BENCHMARK)
        except ValueError:
            skipped.append(dict(date=str(day.date()),reason='insufficient_EU_universe'));continue
        calendar_window=max(60,math.ceil(horizon*7/5)+30)
        sessions=calendar.sessions_in_range(decision.date()+timedelta(days=1),decision.date()+timedelta(days=calendar_window))[:horizon]
        if len(sessions)!=horizon:
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


def evaluate(plans, saved, as_of, horizon=10, model_books=None):
    horizon=horizon_days(horizon)
    benchmark=saved.get(BENCHMARK,{})
    benchmark_dates={r['day'] for r in benchmark.get('bars',[]) if
                     all(valid_price(r.get(k)) for k in ('open','close'))}
    expected={d for p in plans for d in p['sessions'] if d<str(as_of)}
    if expected and (benchmark.get('currency')!='EUR' or not expected<=benchmark_dates):
        raise ValueError('Europese benchmark mist beleggingsdagen; geen cash-benchmark verzonnen')
    if model_books is None:
        model_books=([name for name in plans[0]['books'] if name not in ('equal_universe','benchmark')]
                     if plans else ['a_par:a_par_model_v1'])
    names=[*model_books,'equal_universe','benchmark']
    capital=dict.fromkeys(names,100000.)
    nav=[];trades=[];periods=[];closed=0
    config=dict(currency='EUR',horizon=horizon,top_n=10,**execution_defaults())
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
                    prices[t,row['day']]=dict(open=row['open'],close=row['close'],currency=value.get('currency'),dividend=0.,split=0.,fx_fee_bps=value.get('fx_fee_bps',0))
        value=simulate(snapshot,prices,capital,config,as_of=as_of)
        periods.append(dict(selection_date=snapshot['selection_date'],planned_entry=snapshot['sessions'][0],
                            planned_exit=snapshot['sessions'][-1],universe=len(snapshot['universe']),
                            top=snapshot['books'][model_books[0]],status=value['status'],
                            tops={name:snapshot['books'][name] for name in model_books},
                            skipped_entries=value.get('skipped_entries',{}),
                            allocations=value.get('allocations',{}),
                            starting_capital=capital,ending=value['ending']))
        nav.extend(value['nav']);trades.extend(value.get('trades',[]))
        if value['status']!='complete':
            break  # Never compound across an unresolved cohort or select only successful periods.
        capital=value['ending'];closed+=1
    report=metrics(nav,trades,100000.,closed)
    report.update(mode='historical_reconstruction',horizon=horizon,periods=periods,closed_batches=closed,
                  planned_batches=len(plans),stopped_at=periods[-1]['status'] if periods else 'no_plans',
                  capital=capital,profitability_proven=False,
                  valuation_start=min((r['day'] for r in nav),default=None),
                  valuation_end=max((r['day'] for r in nav),default=None),
                  missing_price_policy='historical_complete_case_fixed_slots_cash',
                  coverage_bias='Historical exclusions use later quote availability; descriptive research only')
    return report


def run(conn,key,horizon=10,provider=native.fetch):
    horizon=horizon_days(horizon)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT pg_try_advisory_lock(741206,46) AS locked')
        if not cur.fetchone()['locked']:
            raise RuntimeError('Europese backtest is al bezig')
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM public.research_backtests WHERE run_key=%s',(key,))
            existing=cur.fetchone()
            if existing:
                cur.execute('SELECT * FROM public.research_backtests WHERE run_key=%s',
                            (existing['config']['parent_run'],))
            else:
                cur.execute("SELECT * FROM public.research_backtests WHERE config->>'mode'='historical_reconstruction' "
                            'ORDER BY created_at DESC LIMIT 1')
            parent=cur.fetchone()
            if not parent:
                raise ValueError('Geen bevroren historische bron beschikbaar')
            config={**configuration(parent['config']['as_of'],horizon),'parent_run':parent['run_key']}
            if config['implementation_hash']!=parent['config']['implementation_hash']:
                raise ValueError('Bronmodel gewijzigd; eerst nieuwe historische bron vastleggen')
            if existing and existing['config']!=config:
                raise ValueError('Europese proefdefinitie gewijzigd; start een nieuwe run')
            if existing and existing['status'] in ('complete','pending_final_period','insufficient_history'):
                return dict(existing['result'],run_key=key)
            if not existing:
                selection={**parent['selection_snapshot'],'plans':None}
                cur.execute('INSERT INTO public.research_backtests(run_key,source_sha,config,selection_snapshot) '
                            'VALUES(%s,%s,%s,%s)',(key,os.getenv('GITHUB_SHA'),Json(config),Json(selection)))
                cur.execute('INSERT INTO public.research_backtest_cache(run_key,ticker,data) '
                            'SELECT %s,ticker,data FROM public.research_backtest_cache WHERE run_key=%s',
                            (key,parent['run_key']))
            else:
                selection=existing['selection_snapshot']
            cur.execute('SELECT ticker,data FROM public.research_backtest_cache WHERE run_key=%s',(key,))
            saved={r['ticker']:r['data'] for r in cur.fetchall()}
        conn.commit()
        print(f'Europese bron bevroren: {len(saved)} koersreeksen uit {parent["run_key"]}.',flush=True)
        frame,metadata=native.restore_inputs(selection)
        merged=frame[['ticker','datum']].merge(metadata[['ticker','datum','beurs']].assign(
            datum=lambda x:pd.to_datetime(x.datum)),on=['ticker','datum'],validate='one_to_one')
        euro=sorted({r.ticker for r in merged.itertuples() if eligible(r.ticker,r.beurs)})
        currencies={saved.get(t,{}).get('currency') for t in euro}
        currencies={'GBP' if c in ('GBp','GBX') else c for c in currencies}
        required=[BENCHMARK,*sorted(FX[c] for c in currencies if c in FX)]
        start=str((frame.datum.min()-pd.Timedelta(days=10)).date())
        for ticker in required:
            if ticker in saved and 'error' not in saved[ticker]:
                continue
            try:
                value=provider(ticker,start,config['as_of'])
                if value.get('currency')!='EUR' or not value.get('bars'):
                    raise ValueError('unverified_EUR_benchmark_or_FX')
            except Exception as exc:
                value=dict(error=type(exc).__name__,bars=[])
                from common.yahoo import YahooRateLimited
                stopped=isinstance(exc,YahooRateLimited)
            else:
                stopped=False
            with conn.cursor() as cur:
                cur.execute('INSERT INTO public.research_backtest_cache(run_key,ticker,data) VALUES(%s,%s,%s) '
                            'ON CONFLICT(run_key,ticker) DO UPDATE SET data=EXCLUDED.data '
                            "WHERE research_backtest_cache.data ? 'error'",(key,ticker,Json(value)))
            conn.commit();saved[ticker]=value
            print(f'Europese aanvullende reeks {ticker}: '+('onbeschikbaar' if 'error' in value else 'vastgelegd'),flush=True)
            if stopped:
                break
        errors={t:saved.get(t,{}).get('error','missing') for t in required
                if not saved.get(t,{}).get('bars') or saved.get(t,{}).get('currency')!='EUR' or 'error' in saved.get(t,{})}
        if errors:
            report=dict(status='partial',reason='Benchmark- of valutareeksen nog onvolledig',
                        quote_errors=errors,books={},closed_batches=0,planned_batches=0,
                        profitability_proven=False)
        else:
            try:
                if selection.get('plans') is None:
                    rebuilt,dated=native.reconstruct_targets(frame,metadata,saved,horizon)
                    print('Europese gewichten en selecties worden historisch opgebouwd.',flush=True)
                    selection.update(prepare(rebuilt,dated,config['as_of'],horizon=horizon))
                    with conn.cursor() as cur:
                        cur.execute('UPDATE public.research_backtests SET selection_snapshot=%s WHERE run_key=%s',
                                    (Json(selection),key))
                    conn.commit()  # All weights/ranks frozen before EUR execution evaluation.
                converted={t:convert(t,saved.get(t,{}),saved) for t in [*euro,BENCHMARK]}
                report=evaluate(selection['plans'],converted,config['as_of'],horizon)
                report['evidence_status']=report.pop('status')
                status='complete' if report['closed_batches']==report['planned_batches'] else 'partial'
                if not selection['plans']:status='insufficient_history'
                elif report['stopped_at'] in ('pending','open'):status='pending_final_period'
                report.update(status=status,quote_errors={t:v['error'] for t,v in converted.items() if 'error' in v},
                              eligible_stocks=len(euro),
                              source_uncovered_rows=sum(not eligible(r.ticker,r.beurs) and str(r.beurs)[:3] in
                                  {'041','042','043','044','045','046','049','050','051','052','053','054','059'}
                                  for r in merged.itertuples()),
                              data_start=selection.get('data_start'),data_end=selection.get('data_end'))
            except Exception as exc:
                report=dict(status='error',reason=f'{type(exc).__name__}: {str(exc)[:200]}',books={},
                            closed_batches=0,planned_batches=0,profitability_proven=False)
        report.update(currency='EUR',benchmark=BENCHMARK,horizon=horizon,parent_run=parent['run_key'],
                      limitations=config['limitations'],quote_basis=config['quote_basis'])
        with conn.cursor() as cur:
            cur.execute('UPDATE public.research_backtests SET status=%s,result=%s,updated_at=clock_timestamp() WHERE run_key=%s',
                        (report['status'],Json(report),key))
        conn.commit()
        return dict(report,run_key=key)
    finally:
        conn.rollback()
        with conn.cursor() as cur:cur.execute('SELECT pg_advisory_unlock(741206,46)')
        conn.commit()


def main():
    import psycopg2
    horizon=horizon_days(os.getenv('BACKTEST_HORIZON','10'))
    key=f'a_par_europe_{horizon}d:'+os.environ['GITHUB_RUN_ID']
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=20) as conn:
        result=run(conn,key,horizon)
    print(json.dumps(result,allow_nan=False,sort_keys=True),flush=True)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('## Europese parameterproef in EUR\n\n```json\n'+json.dumps(result,indent=2)+'\n```\n')
    if result['status'] in ('error','partial'):
        raise SystemExit('Europese proef onvolledig; status en voortgang zijn bewaard')


if __name__=='__main__':main()


