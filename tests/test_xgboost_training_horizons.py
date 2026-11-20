"""Verify actual training inputs and publication skips without fitting or writes."""
from unittest.mock import MagicMock

import pandas as pd
import pytest

from models import xgboostV3 as b
from common.horizons import calendar_embargo


def dataset(days, horizon):
    return pd.DataFrame([dict(ticker=f'T{j}', datum=day,
        **{feature: 1.0 for feature in b.FEATURE_COLUMNS},
        **{f'fwd_ret_{horizon}': 2.0 if j % 2 else -1.0})
        for day in days for j in range(40)])


@pytest.mark.parametrize('horizon', ['10d','30d','60d'])
def test_actual_models_share_purged_date_split(monkeypatch, horizon):
    monkeypatch.setattr(b, "publication_gate", MagicMock(return_value={"publish_advice": "PUBLISH", "incumbent_hashes": {}}))
    days = pd.date_range('2023-01-01', periods=100, freq='7D')
    raw = dataset(days, horizon).sample(frac=1, random_state=7)
    evaluate = MagicMock(return_value=(object(), .6, .65, 1.))
    publish = MagicMock()
    monkeypatch.setattr(b, 'train_en_evalueer', evaluate)
    monkeypatch.setattr(b, 'sla_model_veilig_op', publish)
    result = b.train_voor_horizon(raw, horizon)
    assert result['status'] == 'getraind'
    assert evaluate.call_count == 2
    for call in evaluate.call_args_list:
        train, test = call.args[:2]
        split = days[80]
        cutoff = split-pd.Timedelta(days=calendar_embargo(horizon))
        assert train.datum.max() < cutoff
        assert test.datum.min() == split
        assert set(train.datum.unique()) == set(days[days < cutoff])
        assert set(test.datum.unique()) == set(days[80:])
        assert result['n_train'] == len(train)
        assert result['n_test'] == len(test)
    publish.assert_called_once()


def test_twelve_dense_60d_dates_do_not_publish_new_model(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    model_path = tmp_path/'models'/'xgboostV3_60d_model.pkl'
    model_path.parent.mkdir()
    model_path.write_bytes(b'existing model must survive')
    evaluate, publish = MagicMock(), MagicMock()
    monkeypatch.setattr(b, 'train_en_evalueer', evaluate)
    monkeypatch.setattr(b, 'sla_model_veilig_op', publish)
    result = b.train_voor_horizon(dataset(pd.bdate_range('2026-08-10', periods=12), '60d'), '60d')
    assert result['status'] == 'te_weinig_trainingdata'
    assert '86 kalenderdagen' in result['opmerking']
    evaluate.assert_not_called()
    publish.assert_not_called()
    assert model_path.read_bytes() == b'existing model must survive'


def test_training_excludes_exact_embargo_boundary(monkeypatch):
    monkeypatch.setattr(b, "publication_gate", MagicMock(return_value={"publish_advice": "PUBLISH", "incumbent_hashes": {}}))
    days = pd.date_range('2024-01-01', periods=100)
    evaluate = MagicMock(return_value=(object(), .6, .65, 1.))
    monkeypatch.setattr(b, 'train_en_evalueer', evaluate)
    monkeypatch.setattr(b, 'sla_model_veilig_op', MagicMock())
    b.train_voor_horizon(dataset(days, '30d'), '30d')
    train = evaluate.call_args.args[0]
    assert days[80]-pd.Timedelta(days=44) not in set(train.datum)
    assert train.datum.max() == days[35]
