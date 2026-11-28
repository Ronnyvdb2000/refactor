"""Offline gates: production bytes survive weak or incomparable candidates."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import MagicMock

import joblib
import numpy as np
import pandas as pd
import pytest

from common.model_publication import publication_gate, check_incumbents


class Predictor:
    feature_names_in_ = np.array(['signal'])

    def __init__(self, constant=False):
        self.constant = constant

    def predict_proba(self, frame):
        p = np.full(len(frame), .5) if self.constant else frame.signal.to_numpy()
        return np.column_stack([1 - p, p])


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    train = pd.DataFrame(dict(datum=pd.to_datetime(['2023-01-01'] * 100), label_end_10d=pd.Timestamp('2023-01-16')))
    test = pd.DataFrame([dict(datum=d, signal=.9 if i % 2 else .1,
                             is_profitable=i % 2, fwd_ret_10d=1 if i % 2 else -1, label_end_10d=d+pd.offsets.BDay(10))
                         for d in pd.date_range('2024-01-01', periods=6, freq='21D') for i in range(10)])
    return dict(version='xgboostV3', horizon='10d', target_mode='abs',
                features=['signal'], train=train, test=test,
                metrics=dict(auc=.8, auc_relative=.75, top_model=5., top_baseline=0.),
                incumbent_paths=[Path('models/incumbent.pkl')])


def save_old(setup, *, metadata=True, constant=False):
    path = setup['incumbent_paths'][0]
    path.parent.mkdir(parents=True, exist_ok=True)
    old = Predictor(constant)
    if metadata:
        old.publication_metadata_ = dict(schema_version=1, version='xgboostV3',
            horizon='10d', target_mode='abs', features=['signal'], last_train_date='2023-01-01', last_train_label_end='2023-01-16')
    joblib.dump(old, path)
    return path, old


@pytest.mark.parametrize('change', ['auc', 'baseline', 'relative', 'few_dates', 'few_rows', 'nan'])
def test_weak_candidate_never_changes_production(setup, change):
    path, _ = save_old(setup, metadata=False)
    before = path.read_bytes()
    if change == 'auc': setup['metrics']['auc'] = .49
    if change == 'baseline': setup['metrics']['top_model'] = -1
    if change == 'relative': setup['metrics']['auc_relative'] = .5
    if change == 'few_dates': setup['test'] = setup['test'].iloc[:40]
    if change == 'few_rows': setup['test'] = setup['test'].iloc[::3]
    if change == 'nan': setup['metrics']['auc'] = float('nan')
    result = publication_gate(Predictor(), **setup)
    assert result['publish_advice'] == 'HOLD'
    assert path.read_bytes() == before
    stored = json.loads(Path(result['candidate']).with_suffix('.json').read_text())
    assert stored['issues']


@pytest.mark.parametrize('problem', ['legacy', 'overlap', 'target', 'corrupt', 'equal_auc', 'worse_auc', 'worse_top'])
def test_incumbent_comparison_blocks_unknown_or_worse(setup, problem):
    path, old = save_old(setup, metadata=problem != 'legacy', constant=problem == 'worse_top')
    if problem == 'overlap': old.publication_metadata_['last_train_date'] = '2024-01-01'
    if problem == 'target': old.publication_metadata_['target_mode'] = 'top25'
    if problem == 'equal_auc': setup['metrics'].update(auc=1., auc_relative=1.)
    if problem == 'worse_top': setup['metrics'].update(top_model=-.1, top_baseline=-1.)
    if problem != 'legacy': joblib.dump(old, path)
    if problem == 'corrupt': path.write_bytes(b'broken')
    before = path.read_bytes()
    decision = publication_gate(Predictor(), **setup)
    assert decision['publish_advice'] == 'HOLD'
    assert path.read_bytes() == before


def test_good_bootstrap_and_better_known_candidate_are_publishable(setup):
    initial = publication_gate(Predictor(), **setup)
    assert initial['publish_advice'] == 'PUBLISH'
    check_incumbents(initial)
    path, _ = save_old(setup, constant=True)
    with pytest.raises(RuntimeError, match='gewijzigd'):
        check_incumbents(initial)
    decision = publication_gate(Predictor(), **setup)
    assert decision['publish_advice'] == 'PUBLISH'
    assert decision['incumbents'][0]['same_oos_test_set']
    check_incumbents(decision)
    path.write_bytes(b'changed by another writer')
    with pytest.raises(RuntimeError, match='gewijzigd'):
        check_incumbents(decision)


@pytest.mark.parametrize('name', ['xgboostV3', 'xgboostV4'])
def test_actual_trainers_call_gate_and_never_publish_on_hold(name, tmp_path, monkeypatch):
    from models import xgboostV3, xgboostV4
    module = xgboostV3 if name == 'xgboostV3' else xgboostV4
    monkeypatch.chdir(tmp_path)
    dates = pd.date_range('2023-01-01', periods=100, freq='7D')
    features = xgboostV3.FEATURE_COLUMNS if name == 'xgboostV3' else xgboostV4.FEATURE_TECHNISCH
    frame = pd.DataFrame([dict(datum=d, ticker=f'T{i}',
                **{f:1. for f in features}, fwd_ret_10d=1 if i % 2 else -1)
                for d in dates for i in range(10)])
    gate = MagicMock(return_value=dict(publish_advice='HOLD', issues=['test quality hold']))
    monkeypatch.setattr(module, 'publication_gate', gate)
    publish = MagicMock()
    monkeypatch.setattr(module, 'sla_model_veilig_op', publish)
    if name == 'xgboostV3':
        monkeypatch.setattr(module, 'train_en_evalueer', lambda *a: (Predictor(), .5, .49, -1))
    else:
        monkeypatch.setattr(module, 'train_model', lambda *a: Predictor())
        monkeypatch.setattr(module, 'evalueer_model', lambda *a: (.5, .49, -1))
    frame['label_end_10d'] = frame.datum + pd.offsets.BDay(10)
    result = module.train_voor_horizon(frame, '10d')
    assert result['status'] == 'kwaliteit_hold'
    assert 'HOLD' in result['opmerking']
    publish.assert_not_called()
    gate.assert_called_once()
    assert gate.call_args.kwargs['metrics']['auc'] == .49


def test_v4_feature_selection_sees_training_only(tmp_path, monkeypatch):
    from models import xgboostV4 as v4
    monkeypatch.chdir(tmp_path)
    dates = pd.date_range('2023-01-01', periods=100, freq='7D')
    frame = pd.DataFrame([dict(datum=d, ticker=f'T{i}', fwd_ret_10d=1 if i % 2 else -1,
                              **{f:1. for f in v4.FEATURE_TECHNISCH}) for d in dates for i in range(10)])
    # Available in test only: including test would make its coverage look better.
    frame['trailing_pe'] = np.where(frame.datum >= dates[80], 12., np.nan)
    select = MagicMock(wraps=v4.selecteer_features)
    monkeypatch.setattr(v4, 'selecteer_features', select)
    monkeypatch.setattr(v4, 'train_model', lambda *a: Predictor())
    monkeypatch.setattr(v4, 'evalueer_model', lambda *a: (.5, .6, 1))
    gate = MagicMock(return_value=dict(publish_advice='HOLD', issues=['test']))
    monkeypatch.setattr(v4, 'publication_gate', gate)
    frame['label_end_10d'] = frame.datum + pd.offsets.BDay(10)
    v4.train_voor_horizon(frame, '10d')
    assert all(call.args[0].datum.max() < dates[80] for call in select.call_args_list)
    assert 'trailing_pe' not in gate.call_args.kwargs['features']


def test_real_v3_bootstrap_saves_metadata_and_hold_keeps_the_model(tmp_path, monkeypatch):
    from models import xgboostV3 as v3
    monkeypatch.chdir(tmp_path)
    original = v3.xgb.XGBClassifier
    def small_model(**kwargs):
        return original(**dict(kwargs, n_estimators=5, n_jobs=2))
    monkeypatch.setattr(v3.xgb, 'XGBClassifier', small_model)
    dates = pd.date_range('2023-01-01', periods=100, freq='7D')
    frame = pd.DataFrame([dict(datum=d, ticker=f'T{i}',
        **{f:float(i % 2) for f in v3.FEATURE_COLUMNS},
        fwd_ret_10d=2. if i % 2 else -1.) for d in dates for i in range(40)])
    frame['label_end_10d'] = frame.datum + pd.offsets.BDay(10)
    result = v3.train_voor_horizon(frame, '10d')
    assert result['status'] == 'getraind'
    path = Path('models/xgboostV3_10d_model.pkl')
    model = joblib.load(path)
    assert model.publication_metadata_['target_mode'] == 'abs'
    assert v3.valideer_modelbestand(str(path), v3.FEATURE_COLUMNS, '10d', stil=True)
    before = path.read_bytes()
    # Exact same candidate cannot replace a tied incumbent.
    again = v3.train_voor_horizon(frame, '10d')
    assert again['status'] == 'kwaliteit_hold'
    assert path.read_bytes() == before


def test_high_auc_with_overlapping_daily_windows_still_holds(setup):
    test=setup['test'].copy()
    test['datum']=np.repeat(pd.date_range('2024-01-01',periods=6),10)
    test['label_end_10d']=test.datum+pd.offsets.BDay(10)
    decision=publication_gate(Predictor(),**dict(setup,test=test))
    assert decision['publish_advice']=='HOLD'
    assert decision['metadata']['test_intervals']['dates']==6
    assert decision['metadata']['test_intervals']['nonoverlapping_blocks']==1
    assert any('niet-overlappende' in issue for issue in decision['issues'])


def test_actual_late_training_label_blocks_publication_even_with_old_selection_date(setup):
    train=setup['train'].copy()
    train.loc[0,'label_end_10d']=setup['test'].datum.min()
    decision=publication_gate(Predictor(),**dict(setup,train=train))
    assert decision['publish_advice']=='HOLD'
    assert any('onbekende labels' in issue for issue in decision['issues'])
