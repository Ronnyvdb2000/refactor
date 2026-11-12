#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""XGBoost V3.4 — zelfstandige, conservatieve trainingsvariant.

Upload dit bestand als xgboostV3_gecontroleerd.py voor een aparte proefrun.
Vervang de bestaande xgboostV3.py pas na een succesvolle integratietest.
Bestaande modelbestandsnamen, xgboost_runs-schema en meldingen blijven behouden.
Geen extra Python-module nodig; SUPABASE_DB_URL is vereist.

LET OP: bij een eerste publicatie wordt het model op de embargo-veilige
trainingsperiode getraind. De testperiode wordt NIET in het productiemodel
opgenomen. Vergelijking met het bestaande model gebruikt exact dezelfde test.
"""
import datetime as dt
import math
import os
import smtplib
from email.mime.text import MIMEText

import joblib
import numpy as np
import pandas as pd
import requests
import xgboost as xgb
from sklearn.metrics import roc_auc_score

MODEL_VERSIE = 'xgboostV3'
HORIZONS = ['10d', '30d', '60d']
FEATURE_COLUMNS = ['atr14', 'atr14_pct', 'rsi14', 'ibs', 'ma50', 'ma200',
                   'pct_from_ma50', 'pct_from_ma200', 'vol_ratio_20d',
                   'high52w', 'pct_from_high52w']
FEATURE_COLUMNS_RELATIEF = ['atr14_pct', 'rsi14', 'ibs', 'pct_from_ma50',
                             'pct_from_ma200', 'vol_ratio_20d', 'pct_from_high52w']
BASELINE_KOLOM = 'pct_from_ma50'
TOP_N_FRACTIE = .20
MIN_RIJEN_TRAINING = 30
MIN_RIJEN_TEST = 30
MIN_TEST_DATUMS = 5
MIN_AUC = .55
MAX_AUC_GAP = .10
MIN_MODEL_BYTES = 1000

JOIN_QUERY = '''
WITH fr_dedup AS (
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
FROM generieke_technicals gt JOIN fr_dedup fr
 ON fr.ticker = gt.ticker AND fr.datum = gt.datum
'''


def embargo_dagen(horizon):
    # Conservatieve kalenderbenadering voor horizon in handelsdagen.
    return math.ceil(int(horizon[:-1]) * 7 / 5) + 2


def model_path(horizon):
    return f'{MODEL_VERSIE}_{horizon}_model.pkl'


def leeg_resultaat(horizon, status, opmerking):
    return dict(horizon=horizon, status=status, opmerking=opmerking,
                n_train=None, n_test=None, split_datum=None,
                accuratesse=None, auc=None, auc_relatief=None, top_n=None,
                top_model=None, top_baseline=None, top_relatief=None,
                test_gem=None)


def model_geldig(path, features=FEATURE_COLUMNS):
    if not os.path.isfile(path) or os.path.getsize(path) < MIN_MODEL_BYTES:
        return False
    try:
        model = joblib.load(path)
        return (hasattr(model, 'predict_proba') and
                list(getattr(model, 'feature_names_in_', [])) == list(features))
    except Exception as exc:
        print(f'[WARN] Model niet leesbaar: {path}: {exc}')
        return False


def nieuw_model():
    return xgb.XGBClassifier(n_estimators=150, learning_rate=.03, max_depth=5,
                             subsample=.8, colsample_bytree=.8,
                             random_state=42, eval_metric='logloss')


def auc_score(y, proba):
    return float(roc_auc_score(y, proba)) if y.nunique() == 2 else float('nan')


def top_mean(frame, score, ret, ascending=False):
    n = max(1, int(len(frame) * TOP_N_FRACTIE))
    return float(frame.sort_values(score, ascending=ascending).head(n)[ret].mean())


def metrics_for_model(model, test, horizon):
    ret = f'fwd_ret_{horizon}'
    p = model.predict_proba(test[FEATURE_COLUMNS])[:, 1]
    ranked = test.assign(_score=p)
    return dict(auc=auc_score(test['is_profitable'], p),
                top=top_mean(ranked, '_score', ret),
                accuracy=float(((p >= .5) == test['is_profitable'].to_numpy()).mean()))


def quality_decision(candidate, baseline, relative_auc, test, incumbent=None):
    reasons = []
    if not np.isfinite(candidate['auc']) or candidate['auc'] < MIN_AUC:
        reasons.append(f"AUC {candidate['auc']:.3f} < {MIN_AUC:.2f}")
    if not np.isfinite(candidate['top']) or candidate['top'] <= baseline:
        reasons.append('Toprendement niet beter dan baseline')
    if len(test) < MIN_RIJEN_TEST or test['datum'].nunique() < MIN_TEST_DATUMS:
        reasons.append('Te weinig testdata of testdatums')
    if not np.isfinite(relative_auc):
        reasons.append('Relatief controlemodel heeft geen geldige AUC')
    elif candidate['auc'] - relative_auc > MAX_AUC_GAP:
        reasons.append('Mogelijk prijsniveau/ticker-fingerprint')
    if incumbent is not None:
        if not np.isfinite(incumbent['auc']):
            reasons.append('Bestaand model heeft geen meetbare AUC op dezelfde testset')
        elif candidate['auc'] <= incumbent['auc']:
            reasons.append('Nieuwe AUC niet hoger dan bestaande AUC op dezelfde testset')
        if (np.isfinite(incumbent['top']) and
                candidate['top'] <= incumbent['top']):
            reasons.append('Nieuw toprendement niet hoger dan bestaand model')
    return reasons


def save_atomic(model, horizon):
    target = model_path(horizon)
    tmp = target + '.tmp'
    try:
        joblib.dump(model, tmp)
        if not model_geldig(tmp):
            raise RuntimeError('Tijdelijk model faalt validatie')
        os.replace(tmp, target)
        print(f'[{horizon}] Model gepubliceerd: {target}')
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def train_voor_horizon(df, horizon):
    ret = f'fwd_ret_{horizon}'
    if ret not in df:
        return leeg_resultaat(horizon, 'geen_doelkolom', f'{ret} ontbreekt')
    work = df.dropna(subset=FEATURE_COLUMNS + [ret, 'datum']).copy()
    if work.empty:
        return leeg_resultaat(horizon, 'onvoldoende_data', 'Geen labels/features')
    work['datum'] = pd.to_datetime(work['datum'])
    work[ret] = pd.to_numeric(work[ret], errors='coerce')
    work = work.dropna(subset=[ret]).sort_values(['datum', 'ticker'])
    work['is_profitable'] = (work[ret] > 0).astype(int)
    dates = work['datum'].drop_duplicates().sort_values().reset_index(drop=True)
    if len(dates) < MIN_TEST_DATUMS + 2:
        return leeg_resultaat(horizon, 'onvoldoende_datums', 'Te weinig historische datums')
    idx = min(max(int(len(dates) * .8), 1), len(dates) - 1)
    split_date = dates.iloc[idx]
    train_end = split_date - pd.Timedelta(days=embargo_dagen(horizon))
    train = work[work['datum'] < train_end].copy()
    test = work[work['datum'] >= split_date].copy()
    print(f'[{horizon}] Train {len(train)} rijen / {train.datum.nunique()} datums '
          f'tot {train_end.date()}, test {len(test)} rijen / '
          f'{test.datum.nunique()} datums vanaf {split_date.date()}, '
          f'embargo {embargo_dagen(horizon)} kalenderdagen')
    if len(train) < MIN_RIJEN_TRAINING or train.is_profitable.nunique() < 2:
        return leeg_resultaat(horizon, 'onvoldoende_data', 'Onvoldoende trainrijen na embargo')
    if len(test) < MIN_RIJEN_TEST or test.datum.nunique() < MIN_TEST_DATUMS:
        return leeg_resultaat(horizon, 'te_weinig_testdata', 'Onvoldoende testdata')
    model = nieuw_model()
    model.fit(train[FEATURE_COLUMNS], train['is_profitable'])
    candidate = metrics_for_model(model, test, horizon)
    relative = nieuw_model()
    relative.fit(train[FEATURE_COLUMNS_RELATIEF], train['is_profitable'])
    relative_p = relative.predict_proba(test[FEATURE_COLUMNS_RELATIEF])[:, 1]
    relative_auc = auc_score(test['is_profitable'], relative_p)
    relative_top = top_mean(test.assign(_score=relative_p), '_score', ret)
    baseline = top_mean(test, BASELINE_KOLOM, ret, ascending=True)
    incumbent = None
    if model_geldig(model_path(horizon)):
        incumbent = metrics_for_model(joblib.load(model_path(horizon)), test, horizon)
        print(f'[{horizon}] Bestaand model op dezelfde test: '
              f"AUC={incumbent['auc']:.3f}, top={incumbent['top']:.3f}%")
    reasons = quality_decision(candidate, baseline, relative_auc, test, incumbent)
    print(f'[{horizon}] Nieuw AUC={candidate["auc"]:.3f}, '
          f'top={candidate["top"]:.3f}%, baseline={baseline:.3f}%, '
          f'relatieve AUC={relative_auc:.3f}')
    status = 'getraind' if not reasons else 'kwaliteitscontrole'
    note = None if not reasons else '; '.join(reasons)
    if reasons:
        print(f'[{horizon}] NIET GEPUBLICEERD: {note}')
    else:
        save_atomic(model, horizon)
    return dict(horizon=horizon, status=status, opmerking=note,
                n_train=len(train), n_test=len(test),
                split_datum=str(split_date.date()), accuratesse=candidate['accuracy'],
                auc=candidate['auc'], auc_relatief=relative_auc,
                top_n=max(1, int(len(test)*TOP_N_FRACTIE)),
                top_model=candidate['top'], top_baseline=baseline,
                top_relatief=relative_top, test_gem=float(test[ret].mean()))


def log_runs(conn, resultaten):
    query = '''INSERT INTO xgboost_runs
    (model_versie, horizon, status, opmerking, n_train, n_test,
     split_datum, accuratesse, auc, auc_relatief, top_n,
     top_model_rendement, top_baseline_rendement,
     top_relatief_rendement, test_gemiddelde)
    VALUES (%(model_versie)s, %(horizon)s, %(status)s, %(opmerking)s,
     %(n_train)s, %(n_test)s, %(split_datum)s, %(accuratesse)s,
     %(auc)s, %(auc_relatief)s, %(top_n)s, %(top_model)s,
     %(top_baseline)s, %(top_relatief)s, %(test_gem)s)'''
    with conn.cursor() as cur:
        for result in resultaten:
            params = {**result, 'model_versie': MODEL_VERSIE}
            params = {k: (None if isinstance(v, (float, np.floating)) and
                                   not np.isfinite(v) else v) for k, v in params.items()}
            cur.execute(query, params)
    conn.commit()


def send_telegram(message):
    token = os.getenv('TELEGRAM_TOKEN')
    chat = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:
        print('Telegram niet geconfigureerd')
        return
    try:
        for offset in range(0, len(message), 4096):
            r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                              json={'chat_id': chat, 'text': message[offset:offset+4096],
                                    'disable_web_page_preview': True}, timeout=15)
            r.raise_for_status()
    except Exception as exc:
        print(f'[WARN] Telegram: {exc}')


def send_email(subject, message):
    user, password, receiver = (os.getenv('EMAIL_USER'), os.getenv('EMAIL_PASS'),
                                os.getenv('EMAIL_RECEIVER'))
    if not all((user, password, receiver)):
        return
    try:
        msg = MIMEText(message, 'plain', 'utf-8')
        msg['From'], msg['To'], msg['Subject'] = user, receiver, subject
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
    except Exception as exc:
        print(f'[WARN] Email: {exc}')


def main():
    url = os.getenv('SUPABASE_DB_URL')
    if not url:
        raise RuntimeError('SUPABASE_DB_URL ontbreekt')
    import psycopg2
    conn = psycopg2.connect(url)
    try:
        df = pd.read_sql(JOIN_QUERY, conn)
        print(f'Dataset: {len(df)} rijen, {df.datum.nunique()} datums')
        results = []
        for horizon in HORIZONS:
            try:
                results.append(train_voor_horizon(df, horizon))
            except Exception as exc:
                print(f'[{horizon}] FOUT: {exc}')
                results.append(leeg_resultaat(horizon, 'fout', str(exc)))
        try:
            log_runs(conn, results)
        except Exception as exc:
            conn.rollback()
            print(f'[WARN] xgboost_runs niet gelogd: {exc}')
    finally:
        conn.close()
    lines = [f'XGBoost V3.4 kwaliteitscontrole {dt.date.today()}']
    for r in results:
        lines.append(f"[{r['horizon']}] {r['status']} | "
                     f"AUC={r['auc']} | top={r['top_model']}% | "
                     f"baseline={r['top_baseline']}% | {r['opmerking'] or '-'} | "
                     f"modelbestand={'geldig' if model_geldig(model_path(r['horizon'])) else 'ontbreekt'}")
    message = '\n'.join(lines)
    print(message)
    send_telegram(message)
    send_email('XGBoost V3.4 kwaliteitscontrole', message)
    if not any(model_geldig(model_path(h)) for h in HORIZONS):
        print('[WARN] Geen geldig productiemodel beschikbaar; geen bestanden vervangen')


if __name__ == '__main__':
    main()
