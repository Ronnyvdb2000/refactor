"""Matched walk-forward research: refit ML recipes; never load production models."""
import hashlib
import importlib
import json
import os
from pathlib import Path
from common.reproducibility import source_fingerprint

import numpy as np
import pandas as pd
from psycopg2.extras import Json, RealDictCursor

from analyses.backtest_parameter import configuration, restore_inputs, reconstruct_targets, evaluate
from analyses.paper_trial import plan, digest
from common.horizons import horizon_days, calendar_embargo
from models.xLightgbmV2_gecontroleerd import FEATURES

APAR='a_par:a_par_model_v1'
XGB='xgboostV3:walk_forward_abs'
LGB='lightgbmV2:walk_forward_abs'
CAT='catboostV1:walk_forward_abs'
MODEL_BOOKS=(APAR,XGB,LGB,CAT)
MIN_ROWS=100
MIN_DATES=10
MIN_PER_DATE=20


def fit_model(name, train):
    if name==XGB:
        from models.xgboostV3_gecontroleerd import nieuw_model
        model=nieuw_model().set_params(n_jobs=2,tree_method='hist')
        model.fit(train[FEATURES],train['target'])
        return model
    if name==LGB:
        from models.xLightgbmV2_gecontroleerd import model_fit
        return model_fit(train,FEATURES)
    if name==CAT:
        from models.catboostV1 import fit_model as cat_fit
        return cat_fit(train,FEATURES)
    raise ValueError('Onbekend ML-model')


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
    from analyses.backtest_parameter import US_VENUES
    work=work.loc[work.beurs.isin(US_VENUES)].copy()
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
        # Estimators receive ONLY the fixed input columns and known target.
        training=train[[*FEATURES,'target']].copy()
        for name in (XGB,LGB,CAT):
            model=fitter(name,training.copy())
            proba=np.asarray(model.predict_proba(current[FEATURES]))
            if proba.shape!=(len(current),2) or not np.isfinite(proba).all() or (proba<0).any() or (proba>1).any():
                raise ValueError('Ongeldige modelwaarschijnlijkheden')
            candidates[name]=[dict(ticker=t,score=float(p),currency='USD') for t,p in zip(current.ticker,proba[:,1])]
        selected=plan(candidates,list(MODEL_BOOKS),10)
        selected.update(selection_date=anchor['selection_date'],decided_at=anchor['decided_at'],
                        sessions=anchor['sessions'],
                        training=dict(rows=len(train),dates=int(train.datum.nunique()),
                            start=str(train.datum.min().date()),end=str(train.datum.max().date()),
                            latest_label=str(train.label_date.max().date()),cutoff=str(cutoff.date()),
                            embargo_days=embargo,target='positive_adjusted_return',features=FEATURES,
                            input_hash=hashlib.sha256(pd.util.hash_pandas_object(
                                train[['ticker','datum',*FEATURES,'target']],index=False).values.tobytes()).hexdigest()),
                        scores_hash=digest(candidates),original_universe=len(anchor['universe']))
        plans.append(selected)
    return dict(plans=plans,skipped_cohorts=skipped,anchor_cohorts=len(anchors))


def recipe_hash():
    root=Path(__file__).resolve().parents[1]
    names=['analyses/backtest_ml.py','analyses/backtest_parameter.py','analyses/paper_trial.py',
           'models/xgboostV3_gecontroleerd.py','models/xLightgbmV2_gecontroleerd.py','models/catboostV1.py']
    names += ['common/model_definitions.py','common/costs.py','requirements-paper.txt','requirements-ml-research.txt']
    return source_fingerprint(root, names)


def run(conn,key,horizon=10):
    horizon=horizon_days(horizon)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT pg_try_advisory_lock(741206,44) AS locked')
        if not cur.fetchone()['locked']:
            raise RuntimeError('ML-backtest is al bezig')
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM public.research_backtests WHERE config->>'mode'='historical_cached_replay' "
                        "AND config->>'horizon'=%s ORDER BY created_at DESC LIMIT 1",(str(horizon),))
            parent=cur.fetchone()
            if not parent:
                raise ValueError('Geen vastgelegde parameter-backtest voor deze horizon')
            config={**configuration(parent['config']['as_of'],horizon),
                    'mode':'historical_ml_comparison','parent_run':parent['run_key'],
                    'comparison_hash':recipe_hash(),'features':FEATURES,
                    'ml_target':'positive_adjusted_return',
                    'recipes':{'xgboost':'V3 controlled 150 trees; 2 CPU threads; hist',
                               'lightgbm':'V2 controlled 400 trees; target abs',
                               'catboost':'V1 400 trees; target abs'},
                    'minimum_training':dict(rows=MIN_ROWS,dates=MIN_DATES,stocks_per_date=MIN_PER_DATE),
                    'protocol':'same frozen parameter calendar; expanding mature training; matched complete-feature candidates',
                    'minimum_embargo_days':25,'production_models_used':False}
            if parent['config']['implementation_hash']!=config['implementation_hash']:
                raise ValueError('Bronmodel gewijzigd; maak eerst nieuwe parameter-backtests')
            cur.execute('SELECT config,result,status FROM public.research_backtests WHERE run_key=%s',(key,))
            existing=cur.fetchone()
            if existing and existing['config']!=config:
                raise ValueError('Definitie gewijzigd; start een nieuwe run')
            if existing and existing['status']!='running':
                return dict(existing['result'],run_key=key)
            if not existing:
                cur.execute('INSERT INTO public.research_backtests(run_key,source_sha,config,selection_snapshot) VALUES(%s,%s,%s,%s)',
                            (key,os.getenv('GITHUB_SHA'),Json(config),Json(parent['selection_snapshot'])))
                cur.execute('INSERT INTO public.research_backtest_cache(run_key,ticker,data) '
                            'SELECT %s,ticker,data FROM public.research_backtest_cache WHERE run_key=%s',
                            (key,parent['run_key']))
            cur.execute('SELECT ticker,data FROM public.research_backtest_cache WHERE run_key=%s',(key,))
            cache={r['ticker']:r['data'] for r in cur.fetchall()}
        conn.commit()  # Freeze source data/quotes before any fitting.
        selection=parent['selection_snapshot']
        try:
            frame,metadata=restore_inputs(selection)
            rebuilt,dated=reconstruct_targets(frame,metadata,cache,horizon)
            prepared=prepare_comparison(rebuilt,dated,selection['plans'],horizon)
            report=evaluate(prepared['plans'],cache,config['as_of'],horizon,model_books=MODEL_BOOKS)
            report['evidence_status']=report.pop('status')
            status='complete' if report['closed_batches']==report['planned_batches'] else 'partial'
            if not prepared['plans']:
                status='insufficient_history'
            elif report['stopped_at'] in ('pending','open'):
                status='pending_final_period'
            report.update(status=status,comparison_scope='matched_common_feature_universe',
                          parent_run=parent['run_key'],selection_changed=True,
                          data_start=selection.get('data_start'),data_end=selection.get('data_end'),
                          skipped_cohorts=prepared['skipped_cohorts'],anchor_cohorts=prepared['anchor_cohorts'],
                          model_versions={name:importlib.import_module(name).__version__ for name in ('xgboost','lightgbm','catboost')},
                          quote_basis=config['quote_basis'],limitations=config['limitations'])
            snapshot={**selection,**prepared}
        except Exception as exc:
            report=dict(status='error',reason=f'{type(exc).__name__}: {str(exc)[:240]}',closed_batches=0,books={},
                        horizon=horizon,profitability_proven=False)
            snapshot=selection
        with conn.cursor() as cur:
            cur.execute('UPDATE public.research_backtests SET status=%s,result=%s,selection_snapshot=%s,updated_at=clock_timestamp() WHERE run_key=%s',
                        (report['status'],Json(report),Json(snapshot),key))
        conn.commit()
        return dict(report,run_key=key)
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_unlock(741206,44)')
        conn.commit()


def main():
    import psycopg2
    horizon=horizon_days(os.getenv('BACKTEST_HORIZON','10'))
    key=f'ml_compare_{horizon}d:'+os.environ['GITHUB_RUN_ID']
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=20) as conn:
        result=run(conn,key,horizon)
    print(json.dumps(result,sort_keys=True,allow_nan=False))
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write(f'## ML-modelvergelijking {horizon} handelsdagen\n\n```json\n'+json.dumps(result,indent=2)+'\n```\n')
    if result['status'] in ('error','partial'):
        raise SystemExit('ML-backtest onvolledig; opgeslagen status wordt via Telegram gemeld')


if __name__=='__main__':
    main()

