"""Synthetic model-input regressions: no DB writes, network, or notifications."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from meta_models import a_meta_model_score as meta
from meta_models import a_meta_model_train as train
from models import score_xgboost as xgb
from common.model_inputs import InputUnavailable, scoring_inputs

TODAY = '2026-11-15'


def inputs(n=20, day='2026-11-13'):
    return pd.DataFrame(dict(ticker=[f'A{i}' for i in range(n)],strategie='bot',datum=day,
                             technische_datum=day,x=np.arange(n,dtype=float),y=2.))


def prepare(df, **kwargs):
    return scoring_inputs(df,['x','y'],date_column='datum',today=TODAY,**kwargs)


def test_partial_snapshot_is_rejected_instead_of_scoring_old_features():
    raw=inputs();raw['technische_datum']='2026-11-12'
    with pytest.raises(InputUnavailable,match='0/20'):
        prepare(raw)


def test_infinity_is_missing_before_ranking_and_coverage():
    raw=inputs();raw.loc[:4,'x']=np.inf
    with pytest.raises(InputUnavailable,match='15/20'):
        prepare(raw,rank_features=True)


def test_valid_partial_snapshot_keeps_original_training_rank_cohort():
    raw=inputs();raw.loc[:3,'y']=np.nan
    ready,report=prepare(raw,rank_features=True)
    assert len(ready)==16 and report[0]['ratio']==.8
    assert ready.x.iloc[0]==.25  # rank of A4 in original 20-row cohort, not 1/16.


def test_exact_repeats_do_not_change_coverage_or_scores():
    raw=inputs()
    a,r1=prepare(raw,rank_features=True)
    b,r2=prepare(pd.concat([raw,raw.iloc[:5]]),rank_features=True)
    pd.testing.assert_frame_equal(a,b)
    assert r1==r2


def test_conflicting_repeats_block_scoring():
    raw=inputs();other=raw.iloc[:1].copy();other.x=999.
    with pytest.raises(InputUnavailable,match='Tegenstrijdige'):
        prepare(pd.concat([raw,other]))


@pytest.mark.parametrize('day',['2026-11-16','2026-10-01'])
def test_future_and_stale_scoring_dates_are_rejected(day):
    with pytest.raises(InputUnavailable,match='toekomstig of verouderd'):
        prepare(inputs(day=day))


def test_explicit_historical_scoring_still_requires_exact_snapshot():
    ready,_=prepare(inputs(day='2026-10-01'),historical=True)
    assert len(ready)==20
    with pytest.raises(InputUnavailable):
        prepare(inputs(day='2026-11-16'),historical=True)


def test_local_midnight_is_not_shifted_to_previous_trading_day():
    raw=inputs(day=pd.Timestamp('2026-11-13',tz='Europe/Brussels'))
    ready,_=prepare(raw)
    assert ready.datum.iloc[0]==pd.Timestamp('2026-11-13')


def test_older_incomplete_date_does_not_hide_latest_complete_date():
    old=inputs(day='2026-11-12');old.technische_datum=None
    ready,report=prepare(pd.concat([old,inputs()],ignore_index=True))
    assert ready.datum.nunique()==1 and len(report)==2
    latest=inputs(day='2026-11-14');latest.technische_datum=None
    with pytest.raises(InputUnavailable,match='2026-11-14'):
        prepare(pd.concat([inputs(),latest],ignore_index=True))


def test_60d_few_mature_dates_do_not_become_a_new_ridge_model():
    raw=inputs();raw['target']=1.
    with pytest.raises(InputUnavailable,match='Onvoldoende'):
        train.prepare_training(raw,['x','y'],today=TODAY)


def test_training_filters_nonfinite_targets_and_unmatured_dates():
    rows=[]
    for day in pd.bdate_range('2026-08-10',periods=12):
        day_rows=inputs(day=day);day_rows['target']=np.arange(20.)
        rows.append(day_rows)
    recent=inputs(day='2026-11-13');recent['target']=999.
    raw=pd.concat([*rows,recent],ignore_index=True)
    ready=train.prepare_training(raw,['x','y'],today=TODAY)
    assert len(ready)==240 and ready.datum.max()<pd.Timestamp('2026-10-16')


def test_empty_retraining_exits_failed_and_never_inserts(monkeypatch):
    monkeypatch.setattr(train,'SUPABASE_DB_URL','offline')
    with patch.object(train.argparse.ArgumentParser,'parse_args',return_value=SimpleNamespace(alpha=1.,notities='')), \
         patch.object(train.psycopg2,'connect') as connect, \
         patch.object(train,'haal_training_data',return_value=pd.DataFrame()), \
         patch.object(train,'send_telegram',return_value=True) as telegram:
        assert train.main()==1
    connect.return_value.cursor.assert_not_called()
    connect.return_value.commit.assert_not_called()
    assert 'ONVOLLEDIG' in telegram.call_args.args[0]


def test_meta_input_failure_precedes_every_score_write(monkeypatch):
    monkeypatch.setattr(meta,'SUPABASE_DB_URL','offline')
    raw=inputs(20);raw['koers']=100.
    for feature in meta.MODEL_FEATURES:raw[feature]=1.
    raw['technische_datum']='2026-11-12'
    model=dict(versie='test',horizon_dagen=meta.MODEL_HORIZON,features=meta.MODEL_FEATURES,
               coefs=[1.]*7,scaler_mean=[0.]*7,scaler_std=[1.]*7)
    with patch.object(meta.argparse.ArgumentParser,'parse_args',return_value=SimpleNamespace(datum=None)), \
         patch.object(meta.psycopg2,'connect'), \
         patch.object(meta,'haal_laatste_model',return_value=model), \
         patch.object(meta,'haal_selecties',return_value=(raw,'2026-11-13')), \
         patch.object(meta,'send_telegram',return_value=True) as telegram, \
         patch.object(meta.psycopg2.extras,'execute_batch') as write, \
         pytest.raises(SystemExit) as error:
        meta.main()
    assert error.value.code==1
    write.assert_not_called()
    assert 'FOUT' in telegram.call_args.args[0]


def test_meta_complete_snapshot_retains_scoring_and_notification(monkeypatch):
    monkeypatch.setattr(meta,'SUPABASE_DB_URL','offline')
    raw=inputs(20);raw['koers']=100.
    for feature in meta.MODEL_FEATURES:raw[feature]=np.arange(20.)
    model=dict(versie='test',horizon_dagen=meta.MODEL_HORIZON,features=meta.MODEL_FEATURES,
               coefs=[1.]*7,scaler_mean=[0.]*7,scaler_std=[1.]*7)
    original=meta.scoring_inputs
    with patch.object(meta.argparse.ArgumentParser,'parse_args',return_value=SimpleNamespace(datum=None)), \
         patch.object(meta.psycopg2,'connect') as connect, \
         patch.object(meta,'haal_laatste_model',return_value=model), \
         patch.object(meta,'haal_selecties',return_value=(raw,'2026-11-13')), \
         patch.object(meta,'scoring_inputs',side_effect=lambda *a,**k: original(*a,**k,today=TODAY)), \
         patch.object(meta,'send_telegram',return_value=True) as telegram, \
         patch.object(meta.psycopg2.extras,'execute_batch') as write:
        meta.main()
    assert len(write.call_args.args[2])==20
    connect.return_value.commit.assert_called_once()
    assert 'Top 10' in telegram.call_args.args[0]


def test_xgboost_missing_technicals_are_not_hidden_by_join(monkeypatch):
    raw=inputs();raw=raw.rename(columns={'datum':'selectie_datum'})
    for name in xgb.VERWACHTE_FEATURES:raw[name]=1.
    raw['technische_datum']=None
    with patch.object(xgb.pd,'read_sql_query',return_value=raw),pytest.raises(InputUnavailable):
        xgb.haal_nieuwe_selecties_op(MagicMock())


@pytest.mark.parametrize('value',[np.nan,np.inf,-.1,1.1])
def test_invalid_model_probability_is_not_a_score(value):
    model=SimpleNamespace(classes_=np.array([0,1]),predict_proba=lambda x:np.array([[.2,value]]))
    with pytest.raises(InputUnavailable):
        xgb.positieve_score(model,pd.DataFrame({'x':[1.]}))


def test_partial_model_failure_stops_before_storage():
    raw=pd.DataFrame([dict(ticker='A',selectie_datum='2026-11-13',strategie='bot',**{name:1. for name in xgb.VERWACHTE_FEATURES})])
    good=SimpleNamespace(classes_=np.array([0,1]),predict_proba=lambda x:np.array([[.3,.7]]))
    bad=SimpleNamespace(classes_=np.array([0,1]),predict_proba=lambda x:np.array([[.3,np.inf]]))
    with pytest.raises(InputUnavailable,match='geen nieuwe scores gepubliceerd'):
        xgb.score_selecties(raw,{'10d':good,'30d':bad},set())


def test_valid_training_inserts_one_finite_model_after_input_checks(monkeypatch):
    rows=[]
    for day in pd.bdate_range('2026-08-10',periods=12):
        batch=inputs(day=day)
        for name in train.MODEL_FEATURES:batch[name]=np.arange(20.)
        batch['target']=np.arange(20.)
        rows.append(batch)
    monkeypatch.setattr(train,'SUPABASE_DB_URL','offline')
    monkeypatch.setattr(train,'today_str',lambda:TODAY)
    with patch.object(train.argparse.ArgumentParser,'parse_args',return_value=SimpleNamespace(alpha=1.,notities='offline')), \
         patch.object(train.psycopg2,'connect') as connect, \
         patch.object(train,'haal_training_data',return_value=pd.concat(rows,ignore_index=True)), \
         patch.object(train,'send_telegram',return_value=True):
        assert train.main()==0
    cur=connect.return_value.cursor.return_value.__enter__.return_value
    values=cur.execute.call_args.args[1]
    assert values[7:9]==(240,12)
    assert np.isfinite(values[3:6]).all()
    connect.return_value.commit.assert_called_once()


def test_xgboost_input_failure_notifies_and_does_not_publish(monkeypatch):
    with patch.object(xgb,'controleer_configuratie'), \
         patch.object(xgb,'laad_modellen',return_value={'10d':object()}), \
         patch.object(xgb,'open_database',return_value=MagicMock()), \
         patch.object(xgb,'controleer_database_schema',return_value={}), \
         patch.object(xgb,'controleer_belangrijke_kolommen'), \
         patch.object(xgb,'haal_nieuwe_selecties_op',side_effect=InputUnavailable('0/20 snapshots')), \
         patch.object(xgb,'registreer_run'), \
         patch.object(xgb,'sla_scores_op') as write, \
         patch.object(xgb,'meld_input_status',return_value=True) as telegram:
        assert xgb.main()==1
    write.assert_not_called()
    assert 'ONVOLLEDIG' in telegram.call_args.args[0]


@pytest.mark.parametrize('classes', [[0],['negative','positive'],[1,2]])
def test_unknown_binary_class_mapping_is_not_guessed(classes):
    model=SimpleNamespace(classes_=classes,predict_proba=lambda x:np.array([[.3,.7]]))
    with pytest.raises(InputUnavailable,match='binaire klassen'):
        xgb.positieve_score(model,pd.DataFrame({'x':[1.]}))


@pytest.mark.parametrize('day', ['2026-11-14', '2026-11-15'])
def test_weekend_accepts_only_preceding_friday_and_reports_both_dates(day):
    raw = inputs(day=day)
    raw['technische_datum'] = '2026-11-13'
    ready, report = prepare(raw)
    assert len(ready) == 20
    assert ready.datum.eq(pd.Timestamp(day)).all()  # no relabelled selection
    assert report[0]['technische_datum'] == '2026-11-13'
    assert report[0]['weekend'] is True
    for bad in ['2026-11-12', day, '2026-11-16']:
        raw['technische_datum'] = bad
        with pytest.raises(InputUnavailable):
            prepare(raw)


def test_weekend_does_not_reduce_required_coverage():
    raw = inputs(day='2026-11-14'); raw['technische_datum'] = '2026-11-13'
    raw.loc[:4, 'x'] = np.nan
    with pytest.raises(InputUnavailable, match='15/20'):
        prepare(raw)


def test_monday_does_not_silently_use_friday():
    raw = inputs(day='2026-11-16'); raw['technische_datum'] = '2026-11-13'
    with pytest.raises(InputUnavailable, match='0/20'):
        scoring_inputs(raw, ['x', 'y'], date_column='datum', today='2026-11-16')


def test_weekend_technical_snapshot_must_also_be_fresh():
    raw = inputs(day='2026-11-14'); raw['technische_datum'] = '2026-11-13'
    with pytest.raises(InputUnavailable, match='0/20'):
        scoring_inputs(raw, ['x', 'y'], date_column='datum', today='2026-11-21')


def test_meta_weekend_query_requests_specific_friday_not_unbounded_asof():
    conn = MagicMock()
    meta.haal_selecties(conn, meta.MODEL_FEATURES, '2026-11-14')
    query = conn.cursor.return_value.__enter__.return_value.execute.call_args.args[0]
    assert 'WHEN 6 THEN 1 WHEN 7 THEN 2 ELSE 0 END' in query
    assert 'LEFT JOIN __sp_technicals' in query
    assert 'FROM public.sel_par' in query
    assert 'LEFT JOIN LATERAL' not in query  # JSON strategy expansion is allowed; no as-of feature search.


def test_xgboost_weekend_keeps_dates_through_prediction_to_notification():
    from common.model_inputs import technical_day_sql
    raw = inputs(day='2026-11-14').rename(columns={'datum': 'selectie_datum'})
    raw['technische_datum'] = '2026-11-13'
    raw['selectie_koers'] = 100.; raw['beurs'] = 'test'
    for feature in xgb.VERWACHTE_FEATURES: raw[feature] = 1.
    original = xgb.scoring_inputs
    with patch.object(xgb.pd, 'read_sql_query', return_value=raw) as read, \
         patch.object(xgb, 'scoring_inputs', side_effect=lambda *a, **k: original(*a, **k, today=TODAY)):
        ready = xgb.haal_nieuwe_selecties_op(MagicMock())
    assert technical_day_sql() in read.call_args.args[0]
    model = SimpleNamespace(classes_=np.array([0, 1]), predict_proba=lambda x: np.array([[.3, .7]]))
    results = xgb.score_selecties(ready.iloc[:1], {'10d': model}, set())
    assert results[0]['technische_datum'] == pd.Timestamp('2026-11-13')
    message = xgb.bouw_telegram_bericht(xgb.bepaal_top_resultaten(results))
    assert 'Selectiedatum: 2026-11-14; koersdatum: 2026-11-13' in message
