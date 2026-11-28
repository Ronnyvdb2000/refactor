"""Matched European ML experiment on the frozen EUR parameter-study source."""
from importlib import import_module
from pathlib import Path
from common.reproducibility import source_fingerprint
import hashlib
import json
import os

import numpy as np
import pandas as pd
from psycopg2.extras import Json,RealDictCursor
from threadpoolctl import threadpool_limits

from analyses import backtest_europe as europe
from analyses import backtest_parameter as native
from analyses.backtest_ml import APAR,XGB,LGB,CAT,FEATURES,fit_model as classifier_fit
from analyses.paper_trial import digest
from analyses.europe_market import eligible,convert,BENCHMARK
from analyses.europe_execution import plan
from common.horizons import horizon_days,calendar_embargo
from models import elastic_net

EXTRA_MODEL=import_module('models.10.extra_trees')
ELASTIC='elastic_net:walk_forward_return_v1'
EXTRA='extra_trees:walk_forward_return_v1'
MODEL_BOOKS=(APAR,XGB,LGB,CAT,ELASTIC,EXTRA)
MIN_ROWS=100
MIN_DATES=10
MIN_PER_DATE=20


def fit_model(name,train):
    if name==ELASTIC:return elastic_net.fit_model(train)
    if name==EXTRA:return EXTRA_MODEL.fit_model(train)
    return classifier_fit(name,train)


def model_diagnostics(name,model):
    return elastic_net.diagnostics(model) if name==ELASTIC else EXTRA_MODEL.diagnostics(model)


def matched_plan(candidates):
    if set(candidates)!=set(MODEL_BOOKS):raise ValueError('Niet alle modellen beschikbaar')
    current=plan(candidates[APAR],10,BENCHMARK)
    expected=set(current['universe'])
    for name,rows in candidates.items():
        if len({r['ticker'] for r in rows})!=len(rows) or {r['ticker'] for r in rows}!=expected:
            raise ValueError('Modellen hebben geen gelijk kandidaatuniversum')
        if any(r.get('currency')!='EUR' or not np.isfinite(r['score']) for r in rows):
            raise ValueError('Ongeldige EUR-kandidaten of scores')
    current['books']={name:[r['ticker'] for r in sorted(candidates[name],key=lambda r:(-r['score'],r['ticker']))[:10]]
                      for name in MODEL_BOOKS}
    current['books'].update(equal_universe=current['universe'],benchmark=[BENCHMARK])
    current['candidates']=candidates
    return current


def prepare_comparison(frame, metadata, anchors, horizon, fitter=fit_model):
    """Eligibility uses current features; training uses strictly mature past labels.

    No future return or future quote availability is used to choose candidates.
    Insufficient cohorts are recorded; the common schedule is shared by all books.
    """
    horizon=horizon_days(horizon)
    target=f'fwd_ret_{horizon}d'
    missing=sorted(set(['ticker','datum',target,*FEATURES])-set(frame.columns))
    if missing:
        raise ValueError('Ontbrekende modelkolommen: '+', '.join(missing))
    work=frame[['ticker','datum',target,*FEATURES]].copy()
    work['datum']=pd.to_datetime(work['datum'])
    info=metadata[['ticker','datum','label_date','labels_review','beurs']].copy()
    info['datum']=pd.to_datetime(info['datum'])
    info['label_date']=pd.to_datetime(info['label_date'],errors='coerce')
    work=work.merge(info,on=['ticker','datum'],validate='one_to_one')
    if work.duplicated(['ticker','datum']).any():
        raise ValueError('Dubbele ticker/selectiedatum')
    work=work.loc[[eligible(r.ticker,r.beurs) for r in work.itertuples()]].copy()
    for column in [target,*FEATURES]:
        work[column]=pd.to_numeric(work[column],errors='coerce')
    complete=pd.Series(np.isfinite(work[FEATURES].to_numpy(dtype=float)).all(axis=1),index=work.index)
    plans=[];skipped=[]
    embargo=calendar_embargo(horizon,minimum=25)
    for anchor in anchors:
        day=pd.Timestamp(anchor['selection_date'])
        decision=pd.Timestamp(anchor['decided_at']).tz_convert(None).normalize()
        # Same conservative training cutoff for all three ML estimators.
        cutoff=day-pd.Timedelta(days=embargo)
        known=(work.label_date.notna() & (work.label_date<decision) & work.labels_review.isna())
        train=work.loc[complete & known & (work.datum<=cutoff) & np.isfinite(work[target])].copy()
        train=train.loc[train.groupby('datum')[target].transform('size')>=MIN_PER_DATE].copy()
        train=train.sort_values(['datum','ticker']).reset_index(drop=True)
        train['target']=(train[target]>0).astype(int)
        if len(train)<MIN_ROWS or train.datum.nunique()<MIN_DATES or train.target.nunique()!=2:
            skipped.append(dict(selection_date=str(day.date()),reason='insufficient_mature_training',
                                rows=len(train),dates=int(train.datum.nunique())))
            continue
        current=work.loc[complete & (work.datum==day) & work.ticker.isin(anchor['universe'])].copy()
        current=current.sort_values('ticker').reset_index(drop=True)
        apar={r['ticker']:r for r in anchor['candidates'][APAR]}
        current=current.loc[current.ticker.isin(apar)].copy()
        if len(current)<10:
            skipped.append(dict(selection_date=str(day.date()),reason='insufficient_common_feature_universe',
                                candidates=len(current)))
            continue
        candidates={APAR:[apar[t] for t in current.ticker]}
        training=train[[*FEATURES,'target']].copy()
        regression=train[[*FEATURES,target]].rename(columns={target:'target'})
        details={};flat=[]
        for name in (XGB,LGB,CAT,ELASTIC,EXTRA):
            print(f"Europa {day.date()} — {name}: training op {len(train)} bekende rijen.",flush=True)
            model=fitter(name,regression.copy() if name in (ELASTIC,EXTRA) else training.copy())
            if name in (ELASTIC,EXTRA):
                scores=np.asarray(model.predict(current[FEATURES]),dtype=float)
                if scores.shape!=(len(current),) or not np.isfinite(scores).all():
                    raise ValueError('Ongeldige regressiescores: '+name)
                details[name]=model_diagnostics(name,model)
            else:
                proba=np.asarray(model.predict_proba(current[FEATURES]),dtype=float)
                if (proba.shape!=(len(current),2) or not np.isfinite(proba).all()
                        or (proba<0).any() or (proba>1).any() or not np.allclose(proba.sum(axis=1),1,atol=1e-6)):
                    raise ValueError('Ongeldige modelwaarschijnlijkheden: '+name)
                scores=proba[:,1]
            if np.ptp(scores)<=1e-10:flat.append(name)
            candidates[name]=[dict(ticker=t,score=float(p),currency='EUR') for t,p in zip(current.ticker,scores)]
        if flat:
            skipped.append(dict(selection_date=str(day.date()),reason='no_cross_sectional_signal',models=flat))
            continue
        selected=matched_plan(candidates)
        selected.update(selection_date=anchor['selection_date'],decided_at=anchor['decided_at'],
                        sessions=anchor['sessions'],
                        training=dict(rows=len(train),dates=int(train.datum.nunique()),
                            start=str(train.datum.min().date()),end=str(train.datum.max().date()),
                            latest_label=str(train.label_date.max().date()),cutoff=str(cutoff.date()),
                            embargo_days=embargo,target='classifier_positive_local_return_and_regressor_local_return_pct',features=FEATURES,
                            input_hash=hashlib.sha256(pd.util.hash_pandas_object(
                                train[['ticker','datum',*FEATURES,target,'target']],index=False).values.tobytes()).hexdigest()),
                        model_diagnostics=details,scores_hash=digest(candidates),original_universe=len(anchor['universe']))
        plans.append(selected)
    return dict(plans=plans,skipped_cohorts=skipped,anchor_cohorts=len(anchors))


def recipe_hash():
    root=Path(__file__).resolve().parents[1]
    names=['analyses/backtest_europe_ml.py','analyses/backtest_ml.py','models/elastic_net.py',
           'models/10/extra_trees.py','models/xgboostV3_gecontroleerd.py','models/xLightgbmV2_gecontroleerd.py',
           'models/catboostV1.py','requirements-europe-ml.txt','requirements-europe.txt',
           'requirements-elastic-net.txt','requirements-catboost.txt']
    names += ['common/model_definitions.py','common/costs.py','requirements-paper.txt','requirements-ml-research.txt']
    return source_fingerprint(root, names)


def run(conn,key,horizon=10,source_key=None):
    horizon=horizon_days(horizon)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT pg_try_advisory_lock(741206,44) AS locked')
        if not cur.fetchone()['locked']:raise RuntimeError('ML-vergelijking is al bezig')
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM public.research_backtests WHERE run_key=%s',(key,))
            existing=cur.fetchone()
            if existing:
                if source_key and source_key!=existing['config']['parent_run']:
                    raise ValueError('Deze run heeft al een andere bevroren Europese bron')
                cur.execute('SELECT * FROM public.research_backtests WHERE run_key=%s',(existing['config']['parent_run'],))
            elif source_key:
                cur.execute('SELECT * FROM public.research_backtests WHERE run_key=%s',(source_key,))
            else:
                cur.execute("SELECT * FROM public.research_backtests WHERE config->>'mode'='historical_europe_parameter' "
                            "AND config->>'horizon'=%s AND status IN ('complete','pending_final_period','insufficient_history') "
                            'ORDER BY created_at DESC LIMIT 1',(str(horizon),))
            parent=cur.fetchone()
            if not parent:raise ValueError('Geen geldige Europese parameterbron voor deze horizon')
            if (parent['config'].get('mode')!='historical_europe_parameter' or parent['config']['horizon']!=horizon
                    or parent.get('status') not in ('complete','pending_final_period','insufficient_history')):
                raise ValueError('Europese bron heeft een andere horizon, type of onvolledige berekening')
            base=europe.configuration(parent['config']['as_of'],horizon)
            if any(base[k]!=parent['config'][k] for k in ('europe_hash','implementation_hash','currency','benchmark')):
                raise ValueError('Europese brondefinitie gewijzigd; eerst een nieuwe bronproef')
            versions={n:import_module(n).__version__ for n in ('sklearn','numpy','pandas','xgboost','lightgbm','catboost')}
            config={**parent['config'],'mode':'historical_europe_ml_comparison','parent_run':parent['run_key'],
                    'comparison_hash':recipe_hash(),'ml_features':FEATURES,'model_books':list(MODEL_BOOKS),
                    'ml_training':'European rows only; expanding mature local-currency labels; embargo >=25 days',
                    'parameter_training':'frozen global-IC scores from source; reranked on common feature universe',
                    'minimum_training':dict(rows=MIN_ROWS,dates=MIN_DATES,stocks_per_date=MIN_PER_DATE),
                    'model_versions':versions,'blas_threads':1,'new_price_retrieval':False,
                    'regression_recipes':{'elastic_net':elastic_net.RECIPE,'extra_trees':EXTRA_MODEL.RECIPE}}
            if existing and existing['config']!=config:raise ValueError('Vergelijkingsdefinitie gewijzigd; start een nieuwe run')
            if existing and existing['status']!='running':return dict(existing['result'],run_key=key)
            if existing:
                selection=existing['selection_snapshot']
            else:
                selection=parent['selection_snapshot']
                cur.execute('INSERT INTO public.research_backtests(run_key,source_sha,config,selection_snapshot) '
                            'VALUES(%s,%s,%s,%s)',(key,os.getenv('GITHUB_SHA'),Json(config),Json(selection)))
                cur.execute('INSERT INTO public.research_backtest_cache(run_key,ticker,data) '
                            'SELECT %s,ticker,data FROM public.research_backtest_cache WHERE run_key=%s',(key,parent['run_key']))
            cur.execute('SELECT ticker,data FROM public.research_backtest_cache WHERE run_key=%s',(key,))
            saved={r['ticker']:r['data'] for r in cur.fetchall()}
        conn.commit()
        print(f'Europese ML-bron bevroren: {parent["run_key"]}; {len(saved)} koersreeksen.',flush=True)
        try:
            if not selection.get('comparison_plans_frozen'):
                frame,metadata=native.restore_inputs(selection)
                rebuilt,dated=native.reconstruct_targets(frame,metadata,saved,horizon)
                with threadpool_limits(limits=1,user_api='blas'):
                    prepared=prepare_comparison(rebuilt,dated,selection['plans'],horizon)
                selection={**selection,**prepared,'comparison_plans_frozen':True}
                with conn.cursor() as cur:
                    cur.execute('UPDATE public.research_backtests SET selection_snapshot=%s WHERE run_key=%s',
                                (Json(selection),key))
                conn.commit()  # All model ranks are durable before EUR outcome evaluation.
            members={t for p in selection['plans'] for t in p['universe']}|{BENCHMARK}
            eur={t:convert(t,saved.get(t,{}),saved) for t in members}
            report=europe.evaluate(selection['plans'],eur,config['as_of'],horizon,model_books=MODEL_BOOKS)
            report['evidence_status']=report.pop('status')
            status='complete' if report['closed_batches']==report['planned_batches'] else 'partial'
            if not selection['plans']:
                skipped=selection['skipped_cohorts']
                status='insufficient_signal' if skipped and all(r['reason']=='no_cross_sectional_signal' for r in skipped) else 'insufficient_history'
            elif report['stopped_at'] in ('pending','open'):status='pending_final_period'
            report.update(status=status,comparison_scope='matched_European_complete_feature_universe',
                          skipped_cohorts=selection['skipped_cohorts'],anchor_cohorts=selection['anchor_cohorts'],
                          data_start=selection.get('data_start'),data_end=selection.get('data_end'),
                          model_versions=versions,currency='EUR',benchmark=BENCHMARK,parent_run=parent['run_key'])
        except Exception as exc:
            report=dict(status='error',reason=f'{type(exc).__name__}: {str(exc)[:240]}',books={},closed_batches=0,
                        planned_batches=0,profitability_proven=False,currency='EUR',parent_run=parent['run_key'])
        with conn.cursor() as cur:
            cur.execute('UPDATE public.research_backtests SET status=%s,result=%s,updated_at=clock_timestamp() WHERE run_key=%s',
                        (report['status'],Json(report),key))
        conn.commit()
        return dict(report,run_key=key)
    finally:
        conn.rollback()
        with conn.cursor() as cur:cur.execute('SELECT pg_advisory_unlock(741206,44)')
        conn.commit()


def main():
    import psycopg2
    horizon=horizon_days(os.getenv('BACKTEST_HORIZON','10'))
    key=f'europe_ml_{horizon}d:'+os.environ['GITHUB_RUN_ID']
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=20) as conn:
        result=run(conn,key,horizon,source_key=os.getenv('EUROPE_SOURCE_RUN_KEY'))
    print(json.dumps(result,allow_nan=False,sort_keys=True),flush=True)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('## Europese ML-vergelijking in EUR\n\n```json\n'+json.dumps(result,indent=2)+'\n```\n')
    if result['status'] in ('error','partial'):raise SystemExit('Europese ML-proef onvolledig; status wordt gemeld')


if __name__=='__main__':main()


