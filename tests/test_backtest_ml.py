from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
import yaml

from analyses import backtest_ml as ml
from analyses.backtest_parameter import evaluate
from analyses.backtest_telegram import message
from common.horizons import SUPPORTED_HORIZONS, calendar_embargo


def fixture():
    rows=[];metadata=[]
    dates=list(pd.bdate_range('2026-07-01',periods=10))+[pd.Timestamp('2026-09-15')]
    for day in dates:
        for i in range(20):
            rows.append(dict(ticker=f'T{i:02}',datum=day,
                             fwd_ret_10d=(float('nan') if day.month==9 else (-1. if i%2 else 1.)),
                             **{f:float(i+1) for f in ml.FEATURES}))
            metadata.append(dict(ticker=f'T{i:02}',datum=day,beurs='048 Nasdaq/NYSE',labels_review=None,
                                 label_date=pd.Timestamp('2026-10-01' if day.month==9 else '2026-07-31')))
    anchor=dict(selection_date='2026-09-15',decided_at='2026-09-16T06:00:00+00:00',
                sessions=[str(d.date()) for d in pd.bdate_range('2026-09-17',periods=10)],
                universe=[f'T{i:02}' for i in range(20)],
                candidates={ml.APAR:[dict(ticker=f'T{i:02}',score=100.-i,currency='USD') for i in range(20)]})
    return pd.DataFrame(rows),pd.DataFrame(metadata),[anchor]


class FakeModel:
    def predict_proba(self,frame):
        p=frame.iloc[:,0].to_numpy()/100
        return np.column_stack([1-p,p])


def fitter(name,train):
    assert name in (ml.XGB,ml.LGB,ml.CAT)
    assert list(train.columns)==[*ml.FEATURES,'target']
    assert train.target.nunique()==2
    return FakeModel()


def test_all_models_use_the_same_candidates_calendar_and_only_known_training():
    frame,meta,anchors=fixture()
    # Old input with an outcome not known yet must not enter training.
    extra=frame.iloc[:1].copy();extra['ticker']='UNKNOWN';extra['datum']=pd.Timestamp('2026-07-01')
    extra['atr14']=9999.
    frame=pd.concat([frame,extra],ignore_index=True)
    extra_meta=meta.iloc[:1].copy();extra_meta['ticker']='UNKNOWN';extra_meta['label_date']=pd.Timestamp('2026-10-01')
    meta=pd.concat([meta,extra_meta],ignore_index=True)
    calls=[]
    def checked(name,train):
        assert train.atr14.max()<9999
        assert len(train)==200
        calls.append(name)
        return fitter(name,train)
    result=ml.prepare_comparison(frame,meta,anchors,10,checked)
    assert calls==[ml.XGB,ml.LGB,ml.CAT]
    p=result['plans'][0]
    assert list(p['books'])==[*ml.MODEL_BOOKS,'equal_universe','benchmark']
    assert p['sessions']==anchors[0]['sessions']
    assert len(p['universe'])==20
    assert p['training']['dates']==10 and p['training']['latest_label']=='2026-07-31'
    assert p['training']['end']<=p['training']['cutoff']<'2026-09-15'
    assert p['books'][ml.APAR][0]=='T00' and p['books'][ml.XGB][0]=='T19'


def test_candidate_eligibility_uses_features_not_future_returns_or_prices():
    frame,meta,anchors=fixture()
    frame.loc[(frame.ticker=='T00') & (frame.datum==pd.Timestamp('2026-09-15')),'ma50']=float('nan')
    first=ml.prepare_comparison(frame,meta,anchors,10,fitter)
    assert len(first['plans'][0]['universe'])==19
    assert first['plans'][0]['books'][ml.APAR][0]=='T01'
    # Score-date outcomes, all unknown, cannot change the selection.
    frame.loc[frame.datum==pd.Timestamp('2026-09-15'),'fwd_ret_10d']=100000.
    second=ml.prepare_comparison(frame,meta,anchors,10,fitter)
    assert first==second


def test_no_known_training_labels_records_insufficient_history_without_fitting():
    frame,meta,anchors=fixture();meta['label_date']=pd.Timestamp('2026-12-01')
    fit=MagicMock()
    result=ml.prepare_comparison(frame,meta,anchors,10,fit)
    assert result['plans']==[] and result['skipped_cohorts'][0]['reason']=='insufficient_mature_training'
    fit.assert_not_called()


@pytest.mark.parametrize('horizon',SUPPORTED_HORIZONS)
def test_every_horizon_uses_its_own_target_cutoff_and_sessions(horizon):
    frame,meta,anchors=fixture()
    historical=frame.datum<pd.Timestamp('2026-09-15')
    frame.loc[historical,'datum']-=pd.Timedelta(days=180)
    meta.loc[historical,'datum']-=pd.Timedelta(days=180)
    meta.loc[historical,'label_date']=pd.Timestamp('2026-05-01')
    if horizon!=10:
        frame=frame.rename(columns={'fwd_ret_10d':f'fwd_ret_{horizon}d'})
    anchors[0]['sessions']=[str(d.date()) for d in pd.bdate_range('2026-09-17',periods=horizon)]
    result=ml.prepare_comparison(frame,meta,anchors,horizon,fitter)
    assert len(result['plans'])==1
    p=result['plans'][0]
    assert len(p['sessions'])==horizon
    assert p['training']['embargo_days']==calendar_embargo(horizon,minimum=25)
    assert p['training']['latest_label']<'2026-09-16'


def test_missing_feature_universe_is_skipped_equally_for_every_book():
    frame,meta,anchors=fixture()
    frame.loc[(frame.datum==pd.Timestamp('2026-09-15')) & (frame.ticker>'T08'),'ma50']=float('nan')
    fit=MagicMock()
    result=ml.prepare_comparison(frame,meta,anchors,10,fit)
    assert not result['plans'] and result['skipped_cohorts'][0]['candidates']==9
    fit.assert_not_called()


def test_embargo_and_review_flags_exclude_rows_even_when_labels_are_present():
    frame,meta,anchors=fixture()
    # All original July rows are embargo-safe but reviewed labels are unusable.
    meta['labels_review']='review'
    fit=MagicMock()
    result=ml.prepare_comparison(frame,meta,anchors,10,fit)
    assert not result['plans']
    fit.assert_not_called()


def test_duplicate_input_or_invalid_probabilities_is_a_visible_error():
    frame,meta,anchors=fixture()
    with pytest.raises((ValueError,pd.errors.MergeError)):
        ml.prepare_comparison(pd.concat([frame,frame.iloc[:1]]),meta,anchors,10,fitter)
    class BadModel:
        def predict_proba(self,current):
            return np.full((len(current),2),float('nan'))
    with pytest.raises(ValueError,match='waarschijnlijkheden'):
        ml.prepare_comparison(frame,meta,anchors,10,lambda *a: BadModel())


def test_real_shared_portfolio_engine_and_telegram_support_all_four_models():
    frame,meta,anchors=fixture()
    prepared=ml.prepare_comparison(frame,meta,anchors,10,fitter)
    cache={t:dict(currency='USD',bars=[dict(day=d,open=100.,close=100.) for d in anchors[0]['sessions']])
           for t in anchors[0]['universe']+['SPY'] if t!='T19'}
    result=evaluate(prepared['plans'],cache,'2026-11-27',10,model_books=ml.MODEL_BOOKS)
    assert set(result['books'])==set(ml.MODEL_BOOKS)|{'benchmark','equal_universe'}
    assert result['closed_batches']==1
    assert result['periods'][0]['allocations'][ml.XGB]['invested_budget']==90000
    assert result['periods'][0]['allocations'][ml.APAR]['invested_budget']==100000
    result.update(comparison_scope='matched_common_feature_universe',selection_changed=True,parent_run='frozen',
                  skipped_cohorts=[{'reason':'known_input_unavailable'}])
    text=message(dict(run_key='ml_compare_10d:run',status='complete',config=ml.configuration('2026-11-27'),result=result))
    assert 'XGBoost' in text and 'LightGBM' in text and 'CatBoost' in text
    assert 'belegd $90,000' in text and 'T19' in text
    assert len(text.encode('utf-16-le'))//2<3900


@pytest.mark.parametrize('name',[ml.XGB,ml.LGB,ml.CAT])
def test_real_models_fit_only_provided_training_without_loading_or_publishing(name):
    pytest.importorskip({ml.XGB:'xgboost',ml.LGB:'lightgbm',ml.CAT:'catboost'}[name])
    rng=np.random.default_rng(42)
    train=pd.DataFrame(rng.normal(size=(100,len(ml.FEATURES))),columns=ml.FEATURES)
    train['target']=(train.iloc[:,0]>0).astype(int)
    model=ml.fit_model(name,train)
    scores=model.predict_proba(train[ml.FEATURES].iloc[:5])
    assert scores.shape==(5,2) and np.isfinite(scores).all()


def test_database_source_is_durable_before_model_fitting_and_uses_only_research_tables(monkeypatch):
    frame,meta,anchors=fixture()
    parent=dict(run_key='frozen_10d',config=ml.configuration('2026-11-27'),
                selection_snapshot=dict(plans=anchors,data_start='2026-07-01',data_end='2026-09-15'))
    original=deepcopy(parent)
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},parent,None]
    cur.fetchall.return_value=[]
    monkeypatch.setattr(ml,'restore_inputs',lambda s:(frame,meta))
    monkeypatch.setattr(ml,'reconstruct_targets',lambda *a:(frame,meta))
    prepare=ml.prepare_comparison
    def checked(*args):
        assert conn.commit.call_count==1
        return prepare(*args,fitter=fitter)
    monkeypatch.setattr(ml,'prepare_comparison',checked)
    result=ml.run(conn,'ml-run')
    assert result['status']=='complete' and result['comparison_scope']=='matched_common_feature_universe'
    assert parent==original
    writes=[c.args[0] for c in cur.execute.call_args_list if c.args[0].startswith(('INSERT','UPDATE'))]
    assert writes and all('public.research_backtest' in sql for sql in writes)


def test_ml_workflow_covers_horizons_and_separates_telegram_secrets():
    root=Path(__file__).resolve().parents[1]
    data=yaml.load((root/'.github/workflows/backtest_ml.yml').read_text(),Loader=yaml.BaseLoader)
    job=data['jobs']['comparison']
    assert job['strategy']['matrix']['horizon']==['5','10','20','30','60']
    assert job['strategy']['fail-fast']=='false'
    calc=next(s for s in job['steps'] if s.get('id')=='backtest')
    assert not any(k.startswith('TELEGRAM') for k in calc['env'])
    source=(root/'analyses/backtest_ml.py').read_text()
    assert 'joblib.load' not in source and 'INSERT INTO public.ml_runs' not in source
    assert 'publication_gate(' not in source
