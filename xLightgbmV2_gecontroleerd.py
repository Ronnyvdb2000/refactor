#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Afzonderlijke LightGBM V2 kandidaatproef (geen productiewijzigingen).

Gebruikt dezelfde 11 features, horizonlabels en standaard excess-target als
xLightgbmV2.py. Schrijft uitsluitend naar results/lightgbmV2_controle/.
Geen DB-writes, notificaties of vervanging van bestaande productie-PKL's.
"""
import argparse
import json
import math
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

FEATURES = ['atr14','atr14_pct','rsi14','ibs','ma50','ma200','pct_from_ma50',
            'pct_from_ma200','vol_ratio_20d','high52w','pct_from_high52w']
RELATIVE = ['atr14_pct','rsi14','ibs','pct_from_ma50','pct_from_ma200',
            'vol_ratio_20d','pct_from_high52w']
HORIZONS = ('10d','30d','60d')
MIN_TRAIN, MIN_TEST, MIN_DATES = 100, 30, 20
MIN_PER_DATE = 10
OUT = Path('results/lightgbmV2_controle')
SQL = '''WITH fr AS (
 SELECT DISTINCT ON (ticker, datum) ticker, datum,
 fwd_ret_10d, fwd_ret_30d, fwd_ret_60d
 FROM forward_returns
 ORDER BY ticker, datum,
 ((fwd_ret_10d IS NOT NULL)::int + (fwd_ret_30d IS NOT NULL)::int
 + (fwd_ret_60d IS NOT NULL)::int) DESC
)
SELECT gt.ticker, gt.datum, gt.atr14, gt.atr14_pct, gt.rsi14, gt.ibs,
 gt.ma50, gt.ma200, gt.pct_from_ma50, gt.pct_from_ma200,
 gt.vol_ratio_20d, gt.high52w, gt.pct_from_high52w,
 fr.fwd_ret_10d, fr.fwd_ret_30d, fr.fwd_ret_60d
FROM generieke_technicals gt JOIN fr
 ON gt.ticker = fr.ticker AND gt.datum = fr.datum'''


def embargo_days(horizon):
    return math.ceil(int(horizon[:-1]) * 7 / 5) + 2


def prepare(df, horizon, target_mode):
    label = 'fwd_ret_' + horizon
    work = df.dropna(subset=FEATURES + ['datum',label,'ticker']).copy()
    work['datum'] = pd.to_datetime(work['datum'])
    work = work.sort_values(['datum','ticker']).reset_index(drop=True)
    # Dubbele ticker-datumcombinaties worden niet stilzwijgend als onafhankelijke
    # waarnemingen gebruikt. Stop zodat de brondata onderzocht kan worden.
    if work.duplicated(['ticker','datum']).any():
        raise ValueError('Dubbele ticker/datum-records in de technische data')
    if target_mode == 'excess':
        count = work.groupby('datum')[label].transform('size')
        work = work.loc[count >= MIN_PER_DATE].copy()
        median = work.groupby('datum')[label].transform('median')
        work['target'] = (work[label] > median).astype(int)
    else:
        work['target'] = (work[label] > 0).astype(int)
    return work


def split(work, horizon):
    dates = pd.Series(work['datum'].unique()).sort_values().reset_index(drop=True)
    if len(dates) < MIN_DATES:
        raise ValueError(f'Onvoldoende datums: {len(dates)} < {MIN_DATES}')
    idx = min(max(int(len(dates) * .8), 1), len(dates)-1)
    start = dates.iloc[idx]
    end_train = start - pd.Timedelta(days=embargo_days(horizon))
    train = work.loc[work.datum < end_train].copy()
    test = work.loc[work.datum >= start].copy()
    if len(train) < MIN_TRAIN or len(test) < MIN_TEST:
        raise ValueError(f'Onvoldoende rijen na embargo: train={len(train)}, test={len(test)}')
    if train.target.nunique() < 2 or test.target.nunique() < 2:
        raise ValueError('Train/test moet beide targetklassen bevatten voor AUC')
    return train, test, start, end_train


def auc(y, p):
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, p))


def model_fit(train, columns):
    import lightgbm as lgb
    model = lgb.LGBMClassifier(n_estimators=400, learning_rate=.03,
                              num_leaves=15, max_depth=5, min_child_samples=20,
                              subsample=.8, colsample_bytree=.8,
                              random_state=42, verbosity=-1, n_jobs=2)
    model.fit(train[columns], train.target)
    return model


def top_return(test, probabilities, horizon):
    label = 'fwd_ret_' + horizon
    n = max(1, int(len(test)*.2))
    ranking = pd.Series(probabilities, index=test.index)
    model_top = float(test.loc[ranking.nlargest(n).index, label].mean())
    baseline_top = float(test.nsmallest(n, 'pct_from_ma50')[label].mean())
    return model_top, baseline_top, n


def assess(auc_full, auc_relative, model_top, baseline_top, test):
    reasons = []
    if len(test) < MIN_TEST or test.datum.nunique() < 5:
        reasons.append('te weinig onafhankelijke testdatums/rijen')
    if not np.isfinite(auc_full) or auc_full < .55:
        reasons.append('AUC < 0.55')
    if not np.isfinite(auc_relative):
        reasons.append('controle-AUC ontbreekt')
    elif auc_full - auc_relative > .10:
        reasons.append('te grote AUC-kloof met relatieve features')
    if not np.isfinite(model_top) or model_top <= baseline_top:
        reasons.append('toprendement niet beter dan MA50-baseline')
    # Ook bij voldoende kwaliteit alleen een kandidaat voor handmatige review.
    return ('ELIGIBLE_FOR_REVIEW' if not reasons else 'HOLD'), reasons


def run(df, horizons, target_mode, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    report = []
    for horizon in horizons:
        result = {'horizon': horizon, 'target_mode': target_mode,
                  'status': 'WAIT_DATA', 'publish_advice': 'HOLD'}
        try:
            work = prepare(df, horizon, target_mode)
            train, test, start, end_train = split(work, horizon)
            model = model_fit(train, FEATURES)
            ctrl = model_fit(train, RELATIVE)
            probabilities = model.predict_proba(test[FEATURES])[:,1]
            relative_prob = ctrl.predict_proba(test[RELATIVE])[:,1]
            full_auc = auc(test.target, probabilities)
            relative_auc = auc(test.target, relative_prob)
            top, base, n = top_return(test, probabilities, horizon)
            advice, reasons = assess(full_auc, relative_auc, top, base, test)
            result.update(status='EVALUATED', publish_advice=advice,
                          reasons=reasons, auc=full_auc, auc_relative=relative_auc,
                          top_model=top, top_baseline=base, top_n=n,
                          n_train=len(train), n_test=len(test),
                          n_test_dates=int(test.datum.nunique()),
                          split_date=str(start.date()),
                          last_train_date=str(train.datum.max().date()),
                          embargo_days=embargo_days(horizon))
            # Opslag in aparte kandidaatmap, nooit op productiebestandsnamen.
            bundle = {'model': model, 'features': FEATURES, 'horizon': horizon,
                      'target_mode': target_mode, 'metrics': result}
            path = outdir / f'lightgbmV2_{horizon}_candidate.pkl'
            tmp = path.with_suffix('.tmp')
            try:
                joblib.dump(bundle, tmp)
                check = joblib.load(tmp)
                if check['features'] != FEATURES or not hasattr(check['model'], 'predict_proba'):
                    raise ValueError('Modelvalidatie mislukt')
                os.replace(tmp, path)
            finally:
                if tmp.exists():
                    tmp.unlink()
            print(f'[{horizon}] AUC {full_auc:.3f}, top {top:+.3f}%, '
                  f'baseline {base:+.3f}% -> {advice}')
        except (ValueError, KeyError) as exc:
            result['reason'] = str(exc)
            print(f'[{horizon}] WAIT_DATA: {exc}')
        except Exception as exc:
            result.update(status='ERROR', reason=f'{type(exc).__name__}: {exc}')
            print(f'[{horizon}] ERROR: {exc}')
        report.append(result)
    (outdir/'summary.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', choices=['excess','abs'], default='excess')
    parser.add_argument('--horizons', default='10d,30d,60d')
    parser.add_argument('--csv', help='CSV als alternatief voor Supabase')
    args = parser.parse_args()
    horizons = [x.strip() for x in args.horizons.split(',') if x.strip()]
    if not horizons or any(x not in HORIZONS for x in horizons):
        parser.error('Ongeldige horizons; gebruik 10d,30d,60d')
    if args.csv:
        df = pd.read_csv(args.csv)
    else:
        import psycopg2
        db_url = os.environ.get('SUPABASE_DB_URL')
        if not db_url:
            parser.error('SUPABASE_DB_URL ontbreekt')
        conn = psycopg2.connect(db_url)
        try:
            df = pd.read_sql_query(SQL, conn)
        finally:
            conn.close()
    report = run(df, horizons, args.target, OUT)
    if any(r['status'] == 'ERROR' for r in report):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
