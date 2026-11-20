"""Offline checks of model storage paths, reloads and workflow publication targets."""
from pathlib import Path
import importlib
import json
import subprocess
import sys

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyClassifier

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('module_name', ['xgboostV3', 'xgboostV4'])
def test_atomic_publication_saves_and_reloads_in_models(module_name, tmp_path, monkeypatch):
    module = importlib.import_module('models.' + module_name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module, 'MIN_MODEL_BYTES', 1)
    data = pd.DataFrame({'a': [1, 2], 'b': [3, 4]})
    model = DummyClassifier().fit(data, [0, 1])
    path = Path(module.sla_model_veilig_op(model, '10d', ['a', 'b']))
    assert path.parent == Path('models')
    assert path.exists() and not Path(str(path) + '.tmp').exists()
    assert np.allclose(joblib.load(path).predict_proba(data), model.predict_proba(data))
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        module.sla_model_veilig_op(model, '10d', ['wrong'])
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('*.pkl'))


@pytest.mark.parametrize('module_name', ['bot_01xgboost', 'bot_01xgboostMeta'])
def test_json_models_and_metadata_share_models_directory(module_name, tmp_path, monkeypatch):
    module = importlib.import_module('strats.' + module_name)
    monkeypatch.chdir(tmp_path)
    import xgboost
    data = pd.DataFrame({'a': [0., 1., 2., 3.], 'b': [1., 2., 3., 4.]})
    model = xgboost.XGBClassifier(n_estimators=1, max_depth=1, n_jobs=1).fit(data, [0, 1, 0, 1])
    module.save_model(model, '2026-11-19', 4, {'auc': .5})
    loaded, meta = module.load_model()
    assert np.allclose(model.predict_proba(data), loaded.predict_proba(data))
    assert meta['n_rows'] == 4
    assert Path(module.MODEL_PATH).parent == Path('models')
    assert Path(module.MODEL_META_PATH).exists()
    assert not list(tmp_path.glob('*.json'))


def test_production_scorer_and_controlled_trainer_share_v3_paths():
    from models import score_xgboost, xgboostV3_gecontroleerd
    for horizon, path in score_xgboost.MODEL_FILES.items():
        assert Path(path) == Path(xgboostV3_gecontroleerd.model_path(horizon))
        assert (ROOT / path).is_file()


@pytest.mark.parametrize('module', ['common.lightgbm_v2_preflight', 'maintenance.patch_xgboost_quality',
                                   'models.xLightgbmV2', 'models.xLightgbmV2_gecontroleerd'])
def test_relocated_cli_help(module):
    result = subprocess.run([sys.executable, '-m', module, '--help'], cwd=ROOT,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_patch_cli_uses_relocated_source_and_produces_importable_code(tmp_path):
    output = tmp_path / 'nested/candidate.py'
    result = subprocess.run([sys.executable, '-m', 'maintenance.patch_xgboost_quality',
                             'models/xgboostV3.py', '--output', str(output)], cwd=ROOT,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    source = output.read_text()
    compile(source, str(output), 'exec')
    assert 'from common.model_quality_gate import evaluate_candidate' in source
