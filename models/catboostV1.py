"""Manual CatBoost candidate evaluation; no production scoring; test archive in Supabase."""

from common.temporal_contract import label_column, overlap_report, input_fingerprint
from common.sel_par_inputs import sel_par_query
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from common.model_quality_gate import evaluate_candidate
from models.xLightgbmV2_gecontroleerd import FEATURES, RELATIVE, HORIZONS, SQL
from models.xLightgbmV2_gecontroleerd import embargo_days, split

OUT = Path('models/catboostV1_controle')


class WaitData(ValueError):
    """Valid input, but insufficient history for evaluation."""


def prepare(df, horizon, target_mode):
    label = 'fwd_ret_' + horizon
    required = ['ticker', 'datum', label, *FEATURES]
    missing = sorted(set(required) - set(df.columns))
    if missing:
        raise ValueError(f'Ontbrekende kolommen: {missing}')
    work = df[required + [c for c in [label_column(horizon), 'labels_review'] if c in df]].copy()
    work['datum'] = pd.to_datetime(work['datum'], errors='raise', utc=True).dt.normalize()
    if work[['ticker', 'datum']].isna().any().any():
        raise ValueError('Ticker of datum ontbreekt')
    if work.duplicated(['ticker', 'datum']).any():
        raise ValueError('Dubbele ticker/datum-records')
    for column in [label, *FEATURES]:
        work[column] = pd.to_numeric(work[column], errors='raise')
    if np.isinf(work[[label, *FEATURES]].to_numpy(dtype=float)).any():
        raise ValueError('Oneindige feature- of rendementswaarde')
    # Same complete-input cohort as the controlled LightGBM candidate.
    work = work.dropna(subset=[label, *FEATURES])
    work = work.sort_values(['datum', 'ticker']).reset_index(drop=True)
    if target_mode == 'excess':
        work = work.loc[work.groupby('datum')[label].transform('size') >= 10].copy()
        median = work.groupby('datum')[label].transform('median')
        work['target'] = (work[label] > median).astype(int)
    elif target_mode == 'abs':
        work['target'] = (work[label] > 0).astype(int)
    else:
        raise ValueError('Ongeldig target')
    work.attrs['target_mode'] = target_mode
    return work


def fit_model(train, columns, iterations=400):
    from catboost import CatBoostClassifier
    # Fixed parameters; the outer test set is never supplied to fit.
    model = CatBoostClassifier(
        iterations=iterations, depth=5, learning_rate=.03,
        loss_function='Logloss', l2_leaf_reg=5, random_seed=42,
        thread_count=2, allow_writing_files=False, verbose=False,
    )
    model.fit(train[columns], train['target'])
    return model


def evaluate(test, probabilities, relative_probabilities, horizon):
    from sklearn.metrics import roc_auc_score
    label = 'fwd_ret_' + horizon
    scored = test.assign(score=probabilities, relative_score=relative_probabilities)
    if not np.isfinite(scored[['score', 'relative_score']]).all().all():
        raise ValueError('Ongeldige voorspelling')
    cohorts = []
    for date, group in scored.groupby('datum', sort=True):
        n = max(1, int(len(group) * .2))
        top = group.sort_values(['score', 'ticker'], ascending=[False, True]).head(n)
        base = group.sort_values(['pct_from_ma50', 'ticker']).head(n)
        cohorts.append({
            'date': date.date().isoformat(), 'n': len(group), 'top_n': n,
            'top_model': float(top[label].mean()),
            'top_baseline': float(base[label].mean()),
            'all_candidates': float(group[label].mean()),
        })
    metrics = {
        'auc': float(roc_auc_score(test.target, probabilities)),
        'auc_relative': float(roc_auc_score(test.target, relative_probabilities)),
        'top_model': float(np.mean([c['top_model'] for c in cohorts])),
        'top_baseline': float(np.mean([c['top_baseline'] for c in cohorts])),
        'n_test': len(test), 'n_test_dates': len(cohorts),
    }
    return metrics, cohorts, scored


def save_candidate(model, test, path):
    """Validate a real CatBoost roundtrip before atomic replacement."""
    from catboost import CatBoostClassifier
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix='.cbm')
    os.close(fd)
    try:
        model.save_model(tmp, format='cbm')
        check = CatBoostClassifier()
        check.load_model(tmp)
        if check.feature_names_ != FEATURES:
            raise ValueError('Featurevolgorde veranderd na opslag')
        np.testing.assert_allclose(
            check.predict_proba(test[FEATURES]), model.predict_proba(test[FEATURES]),
            rtol=1e-10, atol=1e-12,
        )
        fingerprint = hashlib.sha256(Path(tmp).read_bytes()).hexdigest()
        os.replace(tmp, path)
        return fingerprint
    finally:
        Path(tmp).unlink(missing_ok=True)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')


def run(df, horizons, target_mode, outdir=OUT):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    report = []
    for horizon in horizons:
        result = dict(horizon=horizon, target_mode=target_mode,
                      status='WAIT_DATA', publish_advice='HOLD')
        # A previous candidate must never be mistaken for this run's result.
        path = outdir / f'catboostV1_{horizon}_candidate.cbm'
        metadata = path.with_suffix('.json')
        predictions = outdir / f'catboostV1_{horizon}_test_predictions.json'
        for old in (path, metadata, predictions):
            old.unlink(missing_ok=True)
        try:
            if horizon not in HORIZONS:
                raise ValueError('Ongeldige horizon')
            work = prepare(df, horizon, target_mode)
            try:
                train, test, start, cutoff = split(work, horizon)
            except ValueError as exc:
                raise WaitData(str(exc)) from exc
            if test.datum.nunique() < 5:
                raise WaitData('Minder dan vijf testdatums')
            model = fit_model(train, FEATURES)
            relative = fit_model(train, RELATIVE)
            metrics, cohorts, scored = evaluate(
                test, model.predict_proba(test[FEATURES])[:, 1],
                relative.predict_proba(test[RELATIVE])[:, 1], horizon,
            )
            from common.model_archive import archive_candidate
            archive_candidate(scored, model='catboost', version='catboostV1',
                              horizon=horizon, target=target_mode, train=train,
                              features=FEATURES, estimator=model,
                              config={'embargo_days': embargo_days(horizon), 'candidate_only': True})
            evidence = overlap_report(test,horizon)
            metrics['test_intervals'] = evidence
            assessment = evaluate_candidate(metrics)
            if evidence['unknown_intervals'] or evidence['nonoverlapping_blocks'] < 3:
                assessment['issues'].append('Onvoldoende geldige niet-overlappende testvensters (minimum 3)')
                assessment['publish_advice'] = 'HOLD'
            result.update(
                status='EVALUATED', **assessment, n_train=len(train), input_hash=input_fingerprint(train),
                n_train_dates=int(train.datum.nunique()),
                split_date=start.date().isoformat(),
                train_cutoff=cutoff.date().isoformat(),
                last_train_date=train.datum.max().date().isoformat(),
                embargo_days=embargo_days(horizon), features=FEATURES,
                cohorts=cohorts, source_commit=os.getenv('GITHUB_SHA'),
                run_id=os.getenv('GITHUB_RUN_ID'),
            )
            # HOLD models are not saved. Eligible means review, not production.
            if result['publish_advice'] == 'ELIGIBLE_FOR_REVIEW':
                result['model_sha256'] = save_candidate(model, test, path)
            records = scored[['ticker', 'datum', 'target', 'fwd_ret_' + horizon,
                              'score', 'relative_score']].copy()
            records['datum'] = records['datum'].dt.strftime('%Y-%m-%d')
            write_json(predictions, records.to_dict(orient='records'))
            write_json(metadata, result)
        except WaitData as exc:
            result['reason'] = str(exc)
        except Exception as exc:
            path.unlink(missing_ok=True)
            result.update(status='ERROR', publish_advice='HOLD',
                          reason=f'{type(exc).__name__}: {exc}')
        print(f"[{horizon}] {result['status']} / {result['publish_advice']}")
        report.append(result)
    write_json(outdir / 'summary.json', report)
    return report


def load_database(db_url):
    import psycopg2
    conn = psycopg2.connect(db_url, connect_timeout=20)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cursor:
            cursor.execute(sel_par_query("SET LOCAL statement_timeout = '300s'"))
        return pd.read_sql_query(sel_par_query(SQL), conn)
    finally:
        conn.rollback()
        conn.close()


def summary_message(report):
    lines = ['CatBoost V1 — kandidaat-evaluatie']
    for row in report:
        lines.append(f"{row['horizon']}: {row['status']} / {row['publish_advice']}")
        if 'metrics' in row:
            m = row['metrics']
            lines.append(f"AUC {m['auc']:.3f}; top {m['top_model']:+.2f}%; "
                         f"baseline {m['top_baseline']:+.2f}%; {m['n_test_dates']} testdatums")
        lines.extend(row.get('issues', []))
        if 'reason' in row:
            lines.append(row['reason'])
    lines.append('Historische test, bruto rendementen; geen productiepublicatie.')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', help='Lokale invoer in plaats van Supabase')
    parser.add_argument('--horizons', default='10d,30d,60d')
    parser.add_argument('--target', choices=['excess', 'abs'], default='excess')
    parser.add_argument('--telegram', action='store_true')
    args = parser.parse_args()
    horizons = list(dict.fromkeys(x.strip() for x in args.horizons.split(',') if x.strip()))
    if not horizons or any(h not in HORIZONS for h in horizons):
        parser.error('Gebruik horizons 10d,30d,60d')
    try:
        if args.csv:
            df = pd.read_csv(args.csv)
        else:
            db_url = os.getenv('SUPABASE_DB_URL')
            if not db_url:
                parser.error('SUPABASE_DB_URL ontbreekt')
            df = load_database(db_url)
        report = run(df, horizons, args.target)
    except Exception as exc:
        report = [dict(horizon=h, status='ERROR', publish_advice='HOLD',
                       reason=f'{type(exc).__name__}: {exc}') for h in horizons]
        OUT.mkdir(parents=True, exist_ok=True)
        write_json(OUT / 'summary.json', report)
    message = summary_message(report)
    print(message)
    if args.telegram:
        from common.notify import send_telegram_report
        if not send_telegram_report(message):
            raise SystemExit('Telegram-rapport niet verzonden; resultaten staan in artifacts')
    if any(r['status'] == 'ERROR' for r in report):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
