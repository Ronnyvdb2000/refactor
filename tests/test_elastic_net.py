from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
import yaml

from analyses import backtest_elastic_net as en
from analyses.backtest_telegram import message
from common.horizons import SUPPORTED_HORIZONS, calendar_embargo
from models.elastic_net import fit_model, diagnostics


def fixture(horizon=10):
    rows=[];metadata=[]
    dates=list(pd.bdate_range('2026-01-01',periods=10))+[pd.Timestamp('2026-09-15')]
    for day in dates:
        for i in range(20):
            rows.append(dict(ticker=f'T{i:02}',datum=day,
                             **{f'fwd_ret_{horizon}d':float('nan') if day.month==9 else i/3-3,
                                **{f:float(i+1) for f in en.FEATURES}}))
            metadata.append(dict(ticker=f'T{i:02}',datum=day,beurs='048 Nasdaq/NYSE',labels_review=None,
                                 label_date=pd.Timestamp('2026-10-01' if day.month==9 else '2026-05-01')))
    anchor=dict(selection_date='2026-09-15',decided_at='2026-09-16T06:00:00+00:00',
                sessions=[str(d.date()) for d in pd.bdate_range('2026-09-17',periods=horizon)],
                universe=[f'T{i:02}' for i in range(20)],
                candidates={en.APAR:[dict(ticker=f'T{i:02}',score=100.-i,currency='USD') for i in range(20)]})
    return pd.DataFrame(rows),pd.DataFrame(metadata),[anchor]


@pytest.mark.parametrize('horizon',SUPPORTED_HORIZONS)
def test_real_regression_uses_mature_training_and_same_calendar(horizon):
    frame,meta,anchors=fixture(horizon)
    result=en.prepare_comparison(frame,meta,anchors,horizon)
    p=result['plans'][0]
    assert p['books'][en.ELASTIC][0]=='T19' and p['books'][en.APAR][0]=='T00'
    assert set(p['books'])=={*en.MODEL_BOOKS,'equal_universe','benchmark'}
    assert p['sessions']==anchors[0]['sessions']
    assert p['training']['rows']==200 and p['training']['dates']==10
    assert p['training']['embargo_days']==calendar_embargo(horizon,minimum=25)
    assert p['training']['latest_label']<'2026-09-16'
    assert p['model_diagnostics']['nonzero_coefficients']>0
    assert p['training']['target']=='adjusted_return_percentage_points'


def test_scaling_and_scores_do_not_see_current_outcomes_or_unmature_labels():
    frame,meta,anchors=fixture()
    original=en.prepare_comparison(frame,meta,anchors,10)
    frame.loc[frame.datum==pd.Timestamp('2026-09-15'),'fwd_ret_10d']=1e12
    extra=frame.iloc[:1].copy();extra['ticker']='FUTURE';extra['atr14']=1e12
    future=meta.iloc[:1].copy();future['ticker']='FUTURE';future['label_date']=pd.Timestamp('2026-12-01')
    actual=en.prepare_comparison(pd.concat([frame,extra]),pd.concat([meta,future]),anchors,10)
    assert original==actual


def test_missing_current_features_exclude_same_stock_from_all_books():
    frame,meta,anchors=fixture()
    frame.loc[(frame.datum==pd.Timestamp('2026-09-15')) & (frame.ticker=='T19'),'ma50']=np.nan
    p=en.prepare_comparison(frame,meta,anchors,10)['plans'][0]
    assert len(p['universe'])==19
    assert all('T19' not in tickers for tickers in p['books'].values())


@pytest.mark.parametrize('problem',['unknown','review','embargo','constant','few_candidates'])
def test_unusable_cohorts_never_fit_or_invent_a_return(problem):
    frame,meta,anchors=fixture();historical=frame.datum<pd.Timestamp('2026-09-15')
    if problem=='unknown':meta['label_date']=pd.Timestamp('2026-12-01')
    if problem=='review':meta['labels_review']='review'
    if problem=='embargo':
        frame.loc[historical,'datum']+=pd.Timedelta(days=240)
        meta.loc[historical,'datum']+=pd.Timedelta(days=240)
    if problem=='constant':frame.loc[historical,'fwd_ret_10d']=2.
    if problem=='few_candidates':frame.loc[~historical & (frame.ticker>'T08'),'ma50']=np.nan
    fitter=MagicMock()
    result=en.prepare_comparison(frame,meta,anchors,10,fitter)
    assert not result['plans'] and result['skipped_cohorts']
    fitter.assert_not_called()


def test_regression_can_train_on_varied_returns_of_only_one_sign():
    frame,meta,anchors=fixture();frame.loc[frame.datum<pd.Timestamp('2026-09-15'),'fwd_ret_10d']+=10
    assert en.prepare_comparison(frame,meta,anchors,10)['plans']


def test_constant_predictions_skip_every_book_instead_of_alphabetic_selection():
    frame,meta,anchors=fixture();model=MagicMock();model.predict.return_value=np.ones(20)
    result=en.prepare_comparison(frame,meta,anchors,10,lambda train:model)
    assert not result['plans'] and result['skipped_cohorts'][0]['reason']=='no_cross_sectional_signal'


def test_nonfinite_scores_and_duplicate_inputs_fail_visibly():
    frame,meta,anchors=fixture();model=MagicMock();model.predict.return_value=np.full(20,np.nan)
    with pytest.raises(ValueError,match='rendementsscores'):
        en.prepare_comparison(frame,meta,anchors,10,lambda train:model)
    with pytest.raises((ValueError,pd.errors.MergeError)):
        en.prepare_comparison(pd.concat([frame,frame.iloc[:1]]),meta,anchors,10)


def test_target_units_diagnostics_and_deterministic_fit():
    frame,_,_=fixture();train=frame.iloc[:200][en.FEATURES].copy()
    train['target']=np.tile(np.arange(20),10)*2.-10
    a=fit_model(train);b=fit_model(train)
    assert np.allclose(a.predict(train[en.FEATURES]),b.predict(train[en.FEATURES]))
    assert np.max(a.predict(train[en.FEATURES]))>1  # Regression scores are not probabilities.
    assert np.allclose(a.named_steps['features'].mean_,train[en.FEATURES].mean())
    d=diagnostics(a);assert len(d['coefficients'])==11 and d['target_scale']>0
    standardized=a.named_steps['features'].transform(train[en.FEATURES])
    assert np.allclose(standardized@np.array(list(d['coefficients'].values()))+d['intercept_return_pct'],
                       a.predict(train[en.FEATURES]))


def test_shared_execution_keeps_missing_quote_slot_in_cash_and_notifies_correct_target():
    frame,meta,anchors=fixture();prepared=en.prepare_comparison(frame,meta,anchors,10)
    cache={t:dict(currency='USD',bars=[dict(day=d,open=100.,close=100.) for d in anchors[0]['sessions']])
           for t in anchors[0]['universe']+['SPY'] if t!='T19'}
    result=en.evaluate(prepared['plans'],cache,'2026-11-27',10,model_books=en.MODEL_BOOKS)
    assert result['periods'][0]['allocations'][en.ELASTIC]['invested_budget']==90000
    result.update(comparison_scope='matched_common_feature_universe',ml_target='adjusted_return_percentage_points')
    text=message(dict(run_key='elastic_net_10d:test',status='complete',config=en.configuration('2026-11-27'),result=result))
    assert 'Elastic Net schat rendement' in text and 'doel positief rendement' not in text
    assert 'belegd $90,000' in text and len(text.encode('utf-16-le'))//2<3900


def test_database_source_is_frozen_before_fitting_and_reruns_pin_the_parent(monkeypatch):
    frame,meta,anchors=fixture()
    parent=dict(run_key='ml-frozen',status='complete',config=en.configuration('2026-11-27'),
                selection_snapshot=dict(plans=anchors,data_start='2026-01-01',data_end='2026-09-15'))
    original=deepcopy(parent)
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},None,parent];cur.fetchall.return_value=[]
    monkeypatch.setattr(en,'restore_inputs',lambda s:(frame,meta))
    monkeypatch.setattr(en,'reconstruct_targets',lambda *a:(frame,meta))
    prepare=en.prepare_comparison
    def checked(*args):
        assert conn.commit.call_count==1
        return prepare(*args)
    monkeypatch.setattr(en,'prepare_comparison',checked)
    result=en.run(conn,'elastic-run')
    assert result['status']=='complete' and parent==original
    writes=[c.args for c in cur.execute.call_args_list if c.args[0].startswith(('INSERT','UPDATE'))]
    assert all('public.research_backtest' in args[0] for args in writes)
    config=writes[0][1][2].adapted
    cur.reset_mock();cur.fetchone.side_effect=[{'locked':True},dict(config=config,result=result,status='complete'),parent]
    again=en.run(conn,'elastic-run')
    assert again==result
    assert cur.execute.call_args_list[2].args[1]==('ml-frozen',)
    assert not any('ORDER BY created_at' in c.args[0] for c in cur.execute.call_args_list)


def test_workflow_is_separate_covers_horizons_and_keeps_secrets_in_notification():
    root=Path(__file__).resolve().parents[1]
    job=yaml.load((root/'.github/workflows/backtest_elastic_net.yml').read_text(),Loader=yaml.BaseLoader)['jobs']['comparison']
    assert job['strategy']['matrix']['horizon']==['5','10','20','30','60']
    assert job['strategy']['max-parallel']=='1' and job['strategy']['fail-fast']=='false'
    calculation=next(s for s in job['steps'] if s.get('id')=='backtest')
    assert not any(k.startswith('TELEGRAM') for k in calculation['env'])
