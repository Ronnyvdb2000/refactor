"""Report exports preserve records, dates and missing data without live services."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import importlib
import sys

import numpy as np
import pandas as pd
import pytest
from common.reports import write_json


def test_json_records_preserve_dates_unicode_and_nulls(tmp_path):
    output = tmp_path / 'nested/report.json'
    frame = pd.DataFrame({'ticker': ['ÉÉ'], 'datum': [pd.Timestamp('2026-11-20')],
                          'score': [np.nan], 'count': [3]})
    write_json(frame, output)
    records = json.loads(output.read_text())
    assert records == [{'ticker': 'ÉÉ', 'datum': '2026-11-20T00:00:00.000', 'score': None, 'count': 3}]
    assert not list(tmp_path.rglob('*.csv'))
    write_json(pd.DataFrame(), output)
    assert json.loads(output.read_text()) == []


@pytest.mark.parametrize('empty', [False, True])
def test_ic_analysis_exports_json_and_horizon_hit_rate(tmp_path, monkeypatch, capsys, empty):
    from analyses import analyse_parameter_ic as module
    monkeypatch.setenv('SUPABASE_DB_URL', 'offline')
    monkeypatch.setattr(module, 'RESULTS_DIR', str(tmp_path))
    df = pd.DataFrame({'ticker': ['A'], 'datum': ['2026-11-19'], 'rsi14': [50.]})
    if empty:
        df = df.drop(columns='rsi14')
    with patch.object(module.psycopg2, 'connect', return_value=MagicMock()), \
         patch.object(module, 'get_data', return_value=df), \
         patch.object(module, 'ic_voor_parameter', return_value={'ic': .1234, 'ic_ir': .5, 'hit': .875}):
        module.main()
    records = json.loads((tmp_path / 'parameter_ic_matrix.json').read_text())
    if empty:
        assert records == []
    else:
        assert records[0]['hit_60d'] == .875
        assert records[0]['ic_60d'] == .1234
        assert '0.875' in capsys.readouterr().out
    assert not list(tmp_path.glob('*.csv'))


@pytest.mark.parametrize('name', ['vergelijk_meta_par_horizons', 'vergelijk_meta_par_horizons_diagnose'])
@pytest.mark.parametrize('empty', [False, True])
def test_horizon_comparison_replaces_reports_even_when_empty(name, empty, tmp_path, monkeypatch):
    module = importlib.import_module('meta_models.' + name)
    monkeypatch.setenv('SUPABASE_DB_URL', 'offline')
    monkeypatch.setattr(module, 'OUT', tmp_path)
    monkeypatch.setattr(module, 'HORIZONS', [10])
    monkeypatch.setattr(sys, 'argv', ['test'])
    (tmp_path / 'folds.json').write_text('[{"old":true}]')
    df = pd.DataFrame() if empty else pd.DataFrame({'ticker': ['A']})
    args = ({'horizon': '10d', 'status': 'ok'}, [{'ic': .3}], [{'ticker': 'A', 'datum': pd.Timestamp('2026-11-19')}])
    if name.endswith('diagnose'):
        args += ({'strategies': [{'name': 'A'}], 'factors': [], 'universe': [], 'skipped': []},)
    with patch.object(module.psycopg2, 'connect', return_value=MagicMock()), \
         patch.object(module, 'load', return_value=df), patch.object(module, 'run_horizon', return_value=args):
        module.main()
    assert json.loads((tmp_path / 'folds.json').read_text()) == ([] if empty else [{'ic': .3}])
    picks = json.loads((tmp_path / 'top_selecties.json').read_text())
    assert len(picks) == (0 if empty else 1)
    assert (tmp_path / 'samenvatting.json').exists()
    if name.endswith('diagnose'):
        assert json.loads((tmp_path / 'diagnose_factors.json').read_text()) == []
    assert not list(tmp_path.glob('*.csv'))


@pytest.mark.parametrize('empty', [False, True])
def test_10d_comparison_writes_record_reports(empty, tmp_path, monkeypatch):
    from meta_models import vergelijk_meta_par_10d as module
    monkeypatch.setenv('SUPABASE_DB_URL', 'offline')
    monkeypatch.setattr(sys, 'argv', ['test', '--out', str(tmp_path)])
    df = pd.DataFrame({'datum': [pd.Timestamp('2026-11-19')]})
    rows = pd.DataFrame() if empty else pd.DataFrame({'model': ['ma50'], 'ic': [.2]})
    picks = pd.DataFrame() if empty else pd.DataFrame({'ticker': ['A']})
    with patch.object(module.psycopg2, 'connect', return_value=MagicMock()), \
         patch.object(module, 'load_data', return_value=df), patch.object(module, 'normalize', return_value=df), \
         patch.object(module, 'run', return_value=(rows, picks, pd.Timestamp('2026-11-01'))), \
         patch.object(module, 'summarize', return_value={'status': 'test'}):
        module.main()
    assert len(json.loads((tmp_path / 'folds.json').read_text())) == (0 if empty else 1)
    assert not list(tmp_path.glob('*.csv'))


def test_lightgbm_feature_importance_remains_sorted(tmp_path, monkeypatch):
    import xLightgbmV2 as module
    monkeypatch.setattr(module, 'RESULTS_DIR', str(tmp_path))
    booster = SimpleNamespace(feature_importance=lambda importance_type: [1, 9] if importance_type == 'gain' else [4, 5])
    module.save_feature_importance(SimpleNamespace(booster_=booster), ['A', 'B'], '10d')
    records = json.loads((tmp_path / 'lightgbmV2_10d_feature_importance.json').read_text())
    assert [r['feature'] for r in records] == ['B', 'A']
    assert records[0]['importance_split'] == 5
    assert not list(tmp_path.glob('*.csv'))


def test_lightgbm_correlations_preserve_missing_values(tmp_path, monkeypatch):
    import xLightgbmV2 as module
    monkeypatch.setattr(module, 'RESULTS_DIR', str(tmp_path))
    monkeypatch.setattr(module, 'FEATURE_COLUMNS', ['feature'])
    module.calculate_correlations(pd.DataFrame({'feature': [1., 2.], 'target': [3., 4.]}), 'target', '10d')
    records = json.loads((tmp_path / 'lightgbmV2_10d_correlations.json').read_text())
    assert records[0]['feature'] == 'feature'
    assert records[0]['pearson'] is None
    assert records[0]['spearman'] is None


def test_report_only_models_do_not_require_provider_imports():
    import subprocess
    command = "import sys; sys.modules['yfinance']=None; import common.reports; import meta_models.vergelijk_meta_par_10d; import analyses.analyse_parameter_ic"
    result = subprocess.run([sys.executable, '-c', command], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
