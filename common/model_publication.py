"""Production gate for V3/V4; unknown incumbent provenance blocks replacement."""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from .horizons import calendar_embargo
from .model_quality_gate import evaluate_candidate


def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, (float, np.floating)) and not math.isfinite(value):
        return None
    return value


def publication_gate(model, *, version, horizon, target_mode, features, train, test,
                     metrics, incumbent_paths):
    """Return a decision and preserve a reload-validated candidate plus evidence.

    Metrics describe the candidate's held-out test rows. Old model metrics are
    recomputed on those exact rows, only with known prior training boundaries.
    No production model is written here, including when loading an old one fails.
    """
    assessment = evaluate_candidate(dict(metrics, n_test=len(test),
                                         n_test_dates=int(test.datum.nunique())))
    issues = assessment['issues']
    embargo = calendar_embargo(horizon)
    metadata = dict(schema_version=1, version=version, horizon=horizon,
                    target_mode=target_mode, features=list(features),
                    last_train_date=pd.Timestamp(train.datum.max()).isoformat(),
                    split_date=pd.Timestamp(test.datum.min()).isoformat(),
                    source_sha=os.getenv('GITHUB_SHA', 'local'))
    model.publication_metadata_ = metadata
    comparisons, hashes = [], {}
    n_top = max(1, int(len(test) * .2))
    for path in sorted(set(map(Path, incumbent_paths))):
        if not path.exists():
            hashes[str(path)] = None
            continue
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            old = joblib.load(path)
            info = getattr(old, 'publication_metadata_', None)
            if not isinstance(info, dict) or info.get('schema_version') != 1:
                raise ValueError('trainingsherkomst bestaand model ontbreekt')
            if any(info.get(k) != metadata[k] for k in ('version', 'horizon', 'target_mode')):
                raise ValueError('doel/modelversie van bestaand model verschilt')
            cutoff = pd.Timestamp(test.datum.min()) - pd.Timedelta(days=embargo)
            if not pd.Timestamp(info['last_train_date']) < cutoff:
                raise ValueError('bestaand model is niet aantoonbaar buiten deze testperiode getraind')
            old_features = list(old.feature_names_in_)
            if old_features != info.get('features') or not set(old_features) <= set(test.columns):
                raise ValueError('kenmerken bestaand model ontbreken of zijn inconsistent')
            probabilities = np.asarray(old.predict_proba(test[old_features]))[:, 1]
            if not np.isfinite(probabilities).all() or ((probabilities < 0) | (probabilities > 1)).any():
                raise ValueError('ongeldige voorspellingen bestaand model')
            old_auc = float(roc_auc_score(test.is_profitable, probabilities))
            old_top = float(test.assign(_prob=probabilities).sort_values('_prob', ascending=False)
                            .head(n_top)['fwd_ret_' + horizon].mean())
            comparisons.append(dict(path=str(path), auc=old_auc, top_model=old_top,
                                    same_oos_test_set=True))
            if metrics['auc'] <= old_auc:
                issues.append(f'{path.name}: AUC niet strikt beter dan bestaand model ({old_auc:.3f})')
            if metrics['top_model'] < old_top:
                issues.append(f'{path.name}: toprendement slechter dan bestaand model ({old_top:.3f})')
        except Exception as exc:
            issues.append(f'{path.name}: vergelijking geblokkeerd ({type(exc).__name__}: {exc})')
    assessment.update(publish_advice='HOLD' if issues else 'PUBLISH',
                      metadata=metadata, incumbents=comparisons, incumbent_hashes=hashes,
                      note='Publicatiebesluit op historische test; geen bewijs van toekomstig rendement.')
    directory = Path('results/model_candidates')
    directory.mkdir(parents=True, exist_ok=True)
    stem = f'{version}_{horizon}'
    candidate = directory / (stem + '.pkl')
    fd, temporary = tempfile.mkstemp(dir=directory, suffix='.pkl')
    os.close(fd)
    try:
        joblib.dump(model, temporary)
        check = joblib.load(temporary)
        if list(check.feature_names_in_) != list(features) or check.publication_metadata_ != metadata:
            raise RuntimeError('Kandidaatmetadata/features veranderd na opslag')
        expected = np.asarray(model.predict_proba(test[features]))
        actual = np.asarray(check.predict_proba(test[features]))
        if (expected.shape != (len(test), 2) or not np.isfinite(expected).all()
                or ((expected < 0) | (expected > 1)).any()):
            raise RuntimeError('Ongeldige kandidaatvoorspellingen')
        np.testing.assert_allclose(actual, expected, rtol=1e-8, atol=1e-10, equal_nan=False)
        os.replace(temporary, candidate)
    finally:
        Path(temporary).unlink(missing_ok=True)
    assessment['candidate'] = str(candidate)
    assessment['candidate_sha256'] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    report = directory / (stem + '.json')
    report.write_text(json.dumps(_json_safe(assessment), indent=2, allow_nan=False), encoding='utf-8')
    print(f"[{horizon}] Kwaliteit: {assessment['publish_advice']} | " + '; '.join(issues))
    return assessment


def check_incumbents(decision):
    """Fail closed if another writer changed a model since the comparison."""
    for name, expected in decision['incumbent_hashes'].items():
        path = Path(name)
        if expected is None:
            if path.exists():
                raise RuntimeError('Bestaand model is gewijzigd sinds de kwaliteitscontrole')
            continue
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Bestaand model is gewijzigd sinds de kwaliteitscontrole')
