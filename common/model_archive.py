"""Permanent, immutable prediction/parameter snapshots; no runtime DDL.

Caller supplies the complete scored cohort BEFORE inspecting future outcomes.
An identical run key is idempotent; a changed payload under that key is rejected.
"""
import hashlib
import json
import math
import os
import uuid
from pathlib import Path

import pandas as pd
from psycopg2.extras import Json, execute_values


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def day(value):
    if value is None:
        return None
    stamp=pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError('Ongeldige datum')
    return stamp.date().isoformat()


def ranked_scores(frame, top_n):
    if top_n < 1 or frame.empty:
        raise ValueError('Geen scores of ongeldige top-N')
    work = frame.copy()
    if work.ticker.isna().any():
        raise ValueError('Ticker ontbreekt')
    work['datum'] = work.datum.map(day)
    work['ticker'] = work.ticker.astype(str).str.strip()
    work['score'] = work.score.map(number)
    if (work[['datum','score']].isna().any().any() or work.ticker.eq('').any()
            or work.duplicated(['datum','ticker']).any()):
        raise ValueError('Ongeldige of dubbele score in archief')
    work = work.sort_values(['datum','score','ticker'], ascending=[True,False,True])
    work['rank'] = work.groupby('datum').cumcount()+1
    work['picked'] = work['rank'] <= top_n
    if 'koers' not in work:
        work['koers'] = None
    return [dict(datum=r.datum,ticker=r.ticker,score=float(r.score),rank=int(r.rank),
                 picked=bool(r.picked),entry_close=number(r.koers)) for r in work.itertuples()]


def parameter_rows(frame, kind='signed_ic', selected=None):
    if frame is None or frame.empty:
        return []
    if frame.parameter.duplicated().any():
        raise ValueError('Dubbele parameter')
    chosen = set(frame.parameter if selected is None else selected)
    denom = sum(abs(number(r.ic) or 0) for r in frame.itertuples() if r.parameter in chosen) if kind=='signed_ic' else 0
    rows=[]
    for r in frame.to_dict('records'):
        used=r['parameter'] in chosen
        ic=number(r.get('ic'))
        weight=(ic/denom if used and denom and ic is not None else None) if kind=='signed_ic' else number(r.get('coefficient' if kind=='coefficient' else 'importance'))
        rows.append(dict(parameter=r['parameter'],kind=kind,selected=used,weight=weight,
                         ic=ic,ic_raw=number(r.get('ic_raw')),ir=number(r.get('ic_ir')),
                         n_dates=number(r.get('n_datums')),n_obs=number(r.get('n_obs'))))
    return sorted(rows,key=lambda r:r['parameter'])


def record(conn, frame, *, model, version, horizon, mode, target, top_n=10,
           parameters=None, kind='signed_ic', selected=None, train_start=None,
           train_end=None, model_hash=None, config=None, run_key=None):
    horizon=int(str(horizon).removesuffix('d'))
    if horizon not in (5,10,20,30,60) or mode not in ('prospective','backtest','legacy'):
        raise ValueError('Ongeldige meettermijn of modus')
    scores=ranked_scores(frame,top_n)
    params=parameter_rows(parameters,kind,selected)
    if (mode=='backtest' and train_end is None) or (train_end is not None and any(r['datum']<=day(train_end) for r in scores)):
        raise ValueError('Train/test datums overlappen of trainingsgrens ontbreekt')
    run=os.getenv('GITHUB_RUN_ID')
    attempt=int(os.getenv('GITHUB_RUN_ATTEMPT','1'))
    key=run_key or f"{run or uuid.uuid4()}:{attempt}:{model}:{version}:{horizon}:{target}"
    raw=json.dumps(dict(model=model,version=version,horizon=horizon,target=target,top_n=top_n,
                        scores=scores,parameters=params,config=config or {},train_start=day(train_start),
                        train_end=day(train_end),mode=mode,model_hash=model_hash),sort_keys=True,allow_nan=False)
    digest=hashlib.sha256(raw.encode()).hexdigest()
    ident=str(uuid.uuid5(uuid.NAMESPACE_URL,key))
    with conn.cursor() as cur:
        cur.execute('''INSERT INTO public.ml_runs
            (id,run_key,model,version,horizon,mode,source_sha,source_run,source_attempt,
             model_hash,train_start,train_end,target,top_n,cohort_hash,config)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(run_key) DO NOTHING RETURNING id''',
            (ident,key,model,version,horizon,mode,os.getenv('GITHUB_SHA'),run,attempt,
             model_hash,day(train_start),day(train_end),target,top_n,digest,Json(config or {})))
        inserted=cur.fetchone()
        if not inserted:
            cur.execute('SELECT cohort_hash FROM public.ml_runs WHERE run_key=%s',(key,))
            if cur.fetchone()[0]!=digest:
                raise ValueError('Run bestaat met andere scores/gewichten; archief niet overschreven')
            return ident
        execute_values(cur,'''INSERT INTO public.ml_scores
            (run_id,datum,ticker,score,rank,picked,entry_close) VALUES %s''',
            [(ident, r['datum'],r['ticker'],r['score'],r['rank'],r['picked'],r['entry_close']) for r in scores])
        if params:
            execute_values(cur,'''INSERT INTO public.ml_params
                (run_id,parameter,kind,selected,weight,ic,ic_raw,ir,n_dates,n_obs) VALUES %s''',
                [(ident,r['parameter'],r['kind'],r['selected'],r['weight'],r['ic'],r['ic_raw'],r['ir'],
                  int(r['n_dates']) if r['n_dates'] is not None else None,
                  int(r['n_obs']) if r['n_obs'] is not None else None) for r in params])
    return ident


def archive_candidate(frame, *, model, version, horizon, target, train, features, estimator, config=None):
    """Historical test archive, deliberately separate from prospective scoring."""
    db=os.getenv('SUPABASE_DB_URL')
    if not db:
        return None  # Offline/CSV experiments do not require a database.
    import psycopg2
    import pickle
    importance=pd.DataFrame({'parameter':features,'importance':estimator.feature_importances_})
    with psycopg2.connect(db,connect_timeout=20) as conn:
        frame=frame.copy()
        with conn.cursor() as cur:
            cur.execute('SELECT ticker,selectiedatum,koers_selectiedatum FROM public.sel_par '
                        'WHERE ticker=ANY(%s) AND selectiedatum BETWEEN %s AND %s',
                        (list(frame.ticker.unique()),day(frame.datum.min()),day(frame.datum.max())))
            prices={(t,day(d)):p for t,d,p in cur.fetchall()}
        frame['koers']=[prices.get((r.ticker,day(r.datum))) for r in frame.itertuples()]
        return record(conn,frame,model=model,version=version,horizon=horizon,mode='backtest',
                      target=target,parameters=importance,kind='importance',
                      model_hash=hashlib.sha256(pickle.dumps(estimator,protocol=5)).hexdigest(),
                      train_start=train.datum.min(),train_end=train.datum.max(),config={**(config or {}), 'model_params': estimator.get_params(),
                              'hash_basis': 'pickle of fitted estimator'})


def archive_xgboost(conn, results, model_files):
    if not results:
        return
    frame=pd.DataFrame(results)
    for horizon,group in frame.groupby('horizon'):
        fingerprint=hashlib.sha256(Path(model_files[horizon]).read_bytes()).hexdigest()
        import joblib
        estimator=joblib.load(model_files[horizon])
        importance=pd.DataFrame({'parameter':estimator.feature_names_in_,
                                 'importance':estimator.feature_importances_})
        record(conn,group,parameters=importance,kind='importance',model='xgboost',version=str(group.model_versie.iloc[0]),horizon=horizon,
               mode='prospective',target='model_score',model_hash=fingerprint,
               config={'training_provenance':'existing model file; training cutoff not inferred',
                       'cohort':'newly scored candidates; existing-score exclusions retained'})


def archive_meta_fold(conn, test, train, estimator, scaler, features, *, horizon,
                      threshold, model_type, scope, batch_id, outcome_conditioned=True):
    """Archive the existing outcome-conditioned historical meta-label experiment.

    Multiple strategies for one stock/date become one explicitly averaged score.
    The original per-signal probabilities remain in meta_label_scores.
    """
    import pickle
    grouped=test.groupby(['ticker','datum'],as_index=False).agg(score=('proba','mean'))
    with conn.cursor() as cur:
        cur.execute('SELECT ticker,selectiedatum,koers_selectiedatum FROM public.sel_par '
                    'WHERE ticker=ANY(%s) AND selectiedatum BETWEEN %s AND %s',
                    (list(grouped.ticker.unique()),day(grouped.datum.min()),day(grouped.datum.max())))
        prices={(t,day(d)):p for t,d,p in cur.fetchall()}
    grouped['koers']=[prices.get((r.ticker,day(r.datum))) for r in grouped.itertuples()]
    kind='importance' if model_type=='xgb' else 'coefficient'
    values=estimator.feature_importances_ if model_type=='xgb' else estimator.coef_[0]
    params=pd.DataFrame({'parameter':features,kind:values})
    fold=day(test.datum.min())
    config={'scope':scope,'threshold_pct':float(threshold),'outcome_conditioned':outcome_conditioned,
            'training_extremes_only':True,
            'comparison_eligible':not outcome_conditioned,'aggregation':'mean probability per ticker/date',
            'n_strategy_signals':len(test),'features':list(features),
            'scaler_mean':scaler.mean_.tolist(),'scaler_scale':scaler.scale_.tolist(),
            'intercept':estimator.intercept_.tolist() if hasattr(estimator,'intercept_') else None}
    ident=record(conn,grouped,model='meta_label',version=f'meta_label_{model_type}_{scope}'+('_full_test' if not outcome_conditioned else '_extremes_test'),
                 horizon=horizon,mode='backtest',target=f'extremes_±{threshold}pct',
                 parameters=params,kind=kind,train_start=train.datum.min(),train_end=train.datum.max(),
                 model_hash=hashlib.sha256(pickle.dumps(estimator,protocol=5)).hexdigest(),config=config,
                 run_key=f'{batch_id}:meta_label:{model_type}:{scope}:{horizon}:{threshold}:{fold}:{outcome_conditioned}')
    conn.commit()
    return ident
