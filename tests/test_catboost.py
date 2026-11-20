import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from models import catboostV1 as cat


@pytest.fixture
def data():
    rng = np.random.default_rng(42)
    n_dates, n_tickers = 160, 12
    n = n_dates * n_tickers
    df = pd.DataFrame({f: rng.normal(size=n) for f in cat.FEATURES})
    df['ticker'] = np.tile([f'T{i:02}' for i in range(n_tickers)], n_dates)
    df['datum'] = np.repeat(pd.bdate_range('2024-01-01', periods=n_dates), n_tickers)
    for h in cat.HORIZONS:
        df['fwd_ret_' + h] = df['rsi14'] * 5 + rng.normal(size=n)
    return df


@pytest.mark.parametrize('horizon', cat.HORIZONS)
def test_split_embargo_and_target(data, horizon):
    work = cat.prepare(data, horizon, 'excess')
    train, test, start, cutoff = cat.split(work, horizon)
    assert train.datum.max() < cutoff
    assert cutoff == start - pd.Timedelta(days=cat.embargo_days(horizon))
    assert test.datum.min() == start
    assert set(train.index).isdisjoint(test.index)
    assert (work.groupby('datum').target.sum() == 6).all()


def test_bad_data_is_error_and_short_history_waits(data, tmp_path):
    bad = pd.concat([data, data.iloc[:1]])
    result = cat.run(bad, ['10d'], 'abs', tmp_path)[0]
    assert result['status'] == 'ERROR'
    assert 'Dubbele' in result['reason']
    result = cat.run(data.iloc[:60], ['10d'], 'abs', tmp_path)[0]
    assert result['status'] == 'WAIT_DATA'
    data.loc[0, 'rsi14'] = np.inf
    assert cat.run(data, ['10d'], 'abs', tmp_path)[0]['status'] == 'ERROR'
    json.loads((tmp_path / 'summary.json').read_text())


def test_top_returns_are_ranked_per_date_and_dates_equal_weight():
    rows = []
    for date, n, ret in [('2024-01-01', 10, 2.), ('2024-01-02', 20, 8.)]:
        for i in range(n):
            rows.append(dict(datum=pd.Timestamp(date), ticker=f'T{i:02}',
                             target=i % 2, pct_from_ma50=i,
                             fwd_ret_10d=ret))
    test = pd.DataFrame(rows)
    p = np.linspace(.1, .9, len(test))
    metrics, cohorts, _ = cat.evaluate(test, p, p, '10d')
    assert metrics['top_model'] == 5.
    assert [c['top_n'] for c in cohorts] == [2, 4]


def test_hold_never_saves_and_removes_stale_candidate(data, tmp_path, monkeypatch):
    class Model:
        def predict_proba(self, df):
            return np.tile([.5, .5], (len(df), 1))
    monkeypatch.setattr(cat, 'fit_model', lambda *args: Model())
    candidate = tmp_path / 'catboostV1_10d_candidate.cbm'
    candidate.write_bytes(b'old')
    result = cat.run(data, ['10d'], 'excess', tmp_path)[0]
    assert result['status'] == 'EVALUATED'
    assert result['publish_advice'] == 'HOLD'
    assert not candidate.exists()
    assert (tmp_path / 'catboostV1_10d_test_predictions.json').exists()


def test_real_training_roundtrip_and_failed_save_preserves_file(data, tmp_path, monkeypatch):
    pytest.importorskip('catboost')
    work = cat.prepare(data, '10d', 'excess')
    train, test, *_ = cat.split(work, '10d')
    model = cat.fit_model(train, cat.FEATURES, iterations=8)
    path = tmp_path / 'candidate.cbm'
    digest = cat.save_candidate(model, test, path)
    assert len(digest) == 64
    before = path.read_bytes()
    def fail(*args, **kwargs):
        raise RuntimeError('disk error')
    monkeypatch.setattr(model, 'save_model', fail)
    with pytest.raises(RuntimeError):
        cat.save_candidate(model, test, path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_real_candidate_report_and_predictions(data, tmp_path):
    pytest.importorskip('catboost')
    result = cat.run(data, ['10d'], 'excess', tmp_path)[0]
    assert result['status'] == 'EVALUATED'
    assert result['publish_advice'] == 'ELIGIBLE_FOR_REVIEW'
    path = tmp_path / 'catboostV1_10d_candidate.cbm'
    assert path.exists()
    metadata = json.loads(path.with_suffix('.json').read_text())
    assert metadata['model_sha256'] == result['model_sha256']
    predictions = json.loads((tmp_path / 'catboostV1_10d_test_predictions.json').read_text())
    assert len(predictions) == result['metrics']['n_test']
    assert len(result['cohorts']) == result['metrics']['n_test_dates']
    assert result['metrics']['auc'] > .8


def test_database_transaction_is_readonly(monkeypatch):
    import psycopg2
    calls = []
    class Connection:
        def set_session(self, **kwargs):
            calls.append(kwargs)
        def cursor(self):
            return self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, sql):
            calls.append(sql)
        def rollback(self):
            calls.append('rollback')
        def close(self):
            calls.append('close')
    monkeypatch.setattr(psycopg2, 'connect', lambda *a, **k: Connection())
    monkeypatch.setattr(pd, 'read_sql_query', lambda *a: pd.DataFrame())
    cat.load_database('unused')
    assert calls[0] == {'readonly': True}
    assert calls[-2:] == ['rollback', 'close']


def test_workflow_is_manual_and_inputs_are_not_shell_code():
    import yaml
    path = Path(__file__).resolve().parents[1] / '.github/workflows/catboostV1_gecontroleerd.yml'
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch'}
    assert workflow['permissions'] == {'contents': 'read'}
    for step in workflow['jobs']['evaluate']['steps']:
        assert '${{ inputs.' not in step.get('run', '')
