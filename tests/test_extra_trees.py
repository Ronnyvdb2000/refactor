from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
import yaml

from analyses import backtest_elastic_net as elastic
from analyses import backtest_extra_trees as et
from analyses.backtest_telegram import message
from common.horizons import SUPPORTED_HORIZONS, calendar_embargo


def fixture(horizon=10):
    rows=[];metadata=[]
    dates=list(pd.bdate_range('2026-01-01',periods=10))+[pd.Timestamp('2026-09-15')]
    for day in dates:
        for i in range(20):
            rows.append(dict(ticker=f'T{i:02}',datum=day,
                             **{f'fwd_ret_{horizon}d':float('nan') if day.month==9 else i/3-3,
                                **{f:float(i+1) for f in et.FEATURES}}))
            metadata.append(dict(ticker=f'T{i:02}',datum=day,beurs='048 Nasdaq/NYSE',labels_review=None,
                                 label_date=pd.Timestamp('2026-10-01' if day.month==9 else '2026-05-01')))
    anchor=dict(selection_date='2026-09-15',decided_at='2026-09-16T06:00:00+00:00',
                sessions=[str(d.date()) for d in pd.bdate_range('2026-09-17',periods=horizon)],
                universe=[f'T{i:02}' for i in range(20)],
                candidates={et.APAR:[dict(ticker=f'T{i:02}',score=100.-i,currency='USD') for i in range(20)]})
    frame,meta=pd.DataFrame(rows),pd.DataFrame(metadata)
    return frame,meta,elastic.prepare_comparison(frame,meta,[anchor],horizon)['plans']


@pytest.mark.parametrize('horizon',SUPPORTED_HORIZONS)
def test_real_forest_keeps_reference_scores_candidates_and_calendar(horizon):
    frame,meta,anchors=fixture(horizon);original=deepcopy(anchors)
    p=et.prepare_comparison(frame,meta,anchors,horizon)['plans'][0]
    assert anchors==original
    assert p['sessions']==anchors[0]['sessions']
    assert p['candidates'][et.ELASTIC]==anchors[0]['candidates'][et.ELASTIC]
    assert p['books'][et.APAR]==anchors[0]['books'][et.APAR]
    assert p['books'][et.ELASTIC]==anchors[0]['books'][et.ELASTIC]
    assert p['books'][et.EXTRA][0]=='T19'
    assert p['training']['rows']==200 and p['training']['dates']==10
    assert p['training']['embargo_days']==calendar_embargo(horizon,minimum=25)
    assert p['model_diagnostics']['trees']==300
    assert max(p['model_diagnostics']['depths'])<=8


def test_future_outcomes_and_unknown_training_rows_cannot_change_fit():
    frame,meta,anchors=fixture();first=et.prepare_comparison(frame,meta,anchors,10)['plans'][0]
    frame.loc[frame.datum==pd.Timestamp('2026-09-15'),'fwd_ret_10d']=1e12
    extra=frame.iloc[:1].copy();extra['ticker']='FUTURE';extra['ma50']=1e12
    future=meta.iloc[:1].copy();future['ticker']='FUTURE';future['label_date']=pd.Timestamp('2026-12-01')
    second=et.prepare_comparison(pd.concat([frame,extra]),pd.concat([meta,future]),anchors,10)['plans'][0]
    assert first['books']==second['books'] and first['training']==second['training']
    assert first['model_diagnostics']==second['model_diagnostics']
    assert np.allclose([r['score'] for r in first['candidates'][et.EXTRA]],
                       [r['score'] for r in second['candidates'][et.EXTRA]])


@pytest.mark.parametrize('problem',['unknown','review','embargo','constant','few_candidates'])
def test_bad_cohorts_are_visible_without_fitting(problem):
    frame,meta,anchors=fixture();historical=frame.datum<pd.Timestamp('2026-09-15')
    if problem=='unknown':meta['label_date']=pd.Timestamp('2026-12-01')
    if problem=='review':meta['labels_review']='review'
    if problem=='embargo':
        frame.loc[historical,'datum']+=pd.Timedelta(days=240)
        meta.loc[historical,'datum']+=pd.Timedelta(days=240)
    if problem=='constant':frame.loc[historical,'fwd_ret_10d']=2.
    if problem=='few_candidates':frame.loc[~historical & (frame.ticker>'T08'),'ma50']=np.nan
    fit=MagicMock();r=et.prepare_comparison(frame,meta,anchors,10,fit)
    assert not r['plans'] and r['skipped_cohorts'];fit.assert_not_called()


def test_flat_and_nonfinite_predictions_are_not_ranked_as_valid_signals():
    frame,meta,anchors=fixture();model=MagicMock();model.predict.return_value=np.zeros(20)
    result=et.prepare_comparison(frame,meta,anchors,10,lambda train:model)
    assert not result['plans'] and result['skipped_cohorts'][0]['reason']=='no_cross_sectional_signal'
    model.predict.return_value=np.full(20,np.nan)
    with pytest.raises(ValueError,match='rendementsscores'):
        et.prepare_comparison(frame,meta,anchors,10,lambda train:model)


def test_real_recipe_is_seeded_and_uses_continuous_return_units():
    frame,_,_=fixture();train=frame.iloc[:200][et.FEATURES].copy()
    train['target']=np.tile(np.arange(20),10)*2.-10
    a,b=et.fit_model(train),et.fit_model(train)
    assert np.allclose(a.predict(train[et.FEATURES]),b.predict(train[et.FEATURES]))
    assert et.diagnostics(a)['tree_structure_hash']==et.diagnostics(b)['tree_structure_hash']
    assert max(a.predict(train[et.FEATURES]))>1
    assert a.random_state==42 and not a.bootstrap and a.min_samples_leaf==10
    d=et.diagnostics(a)
    assert set(d['feature_importances'])==set(et.FEATURES)
    assert sum(d['feature_importances'].values())==pytest.approx(1.)


def test_negative_varied_targets_are_supported_and_invalid_training_rejected():
    frame,_,_=fixture();train=frame.iloc[:200][et.FEATURES].copy()
    train['target']=-np.tile(np.arange(1,21),10).astype(float)
    assert np.isfinite(et.fit_model(train).predict(train[et.FEATURES])).all()
    train.loc[0,'target']=np.nan
    with pytest.raises(ValueError,match='eindige'):et.fit_model(train)


def test_shared_execution_cash_slot_and_five_book_telegram_fit():
    frame,meta,anchors=fixture();prepared=et.prepare_comparison(frame,meta,anchors,10)
    cache={t:dict(currency='USD',bars=[dict(day=d,open=100.,close=100.) for d in anchors[0]['sessions']])
           for t in anchors[0]['universe']+['SPY'] if t!='T19'}
    result=et.evaluate(prepared['plans'],cache,'2026-11-27',10,model_books=et.MODEL_BOOKS)
    assert result['periods'][0]['allocations'][et.EXTRA]['invested_budget']==90000
    result.update(comparison_scope='matched_common_feature_universe',ml_target='adjusted_return_percentage_points',
                  regression_model='Extra Trees')
    text=message(dict(run_key='extra_trees_10d:test',status='complete',config=et.configuration('2026-11-27'),result=result))
    assert 'Extra Trees schat rendement' in text and 'Elastic Net-scores uit de bevroren bron' in text
    assert len(result['books'])==5 and len(text.encode('utf-16-le'))//2<3900


def test_source_is_frozen_before_training_and_retry_keeps_parent(monkeypatch):
    frame,meta,anchors=fixture()
    parent=dict(run_key='elastic-frozen',status='complete',config={**et.configuration('2026-11-27'),'features':et.FEATURES},
                selection_snapshot=dict(plans=anchors,data_start='2026-01-01',data_end='2026-09-15'))
    original=deepcopy(parent);conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},None,parent];cur.fetchall.return_value=[]
    monkeypatch.setattr(et,'restore_inputs',lambda s:(frame,meta))
    monkeypatch.setattr(et,'reconstruct_targets',lambda *a:(frame,meta))
    prepare=et.prepare_comparison
    def checked(*args):
        assert conn.commit.call_count==1
        return prepare(*args)
    monkeypatch.setattr(et,'prepare_comparison',checked)
    result=et.run(conn,'extra-run');assert result['status']=='complete' and parent==original
    writes=[c.args for c in cur.execute.call_args_list if c.args[0].startswith(('INSERT','UPDATE'))]
    assert all('public.research_backtest' in args[0] for args in writes)
    config=writes[0][1][2].adapted
    cur.reset_mock();cur.fetchone.side_effect=[{'locked':True},dict(config=config,result=result,status='complete'),parent]
    assert et.run(conn,'extra-run')==result
    assert cur.execute.call_args_list[2].args[1]==('elastic-frozen',)
    assert not any('ORDER BY created_at' in c.args[0] for c in cur.execute.call_args_list)


@pytest.mark.parametrize('parent',[None,dict(run_key='incompatible',config={'features':['different']})])
def test_missing_or_incompatible_reference_is_reported_without_fitting(parent):
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},None,parent]
    result=et.run(conn,'blocked-source')
    assert result['status']=='blocked_source' and result['books']=={}
    insert=next(c.args for c in cur.execute.call_args_list if c.args[0].startswith('INSERT'))
    assert insert[1][4]=='blocked_source'
    text=message(dict(run_key='blocked-source',status=result['status'],config=insert[1][2].adapted,result=result))
    assert result['reason'] in text and 'Nog geen berekend rendement' in text
    cur.reset_mock();cur.fetchone.side_effect=[{'locked':True},dict(status='blocked_source',result=result)]
    assert et.run(conn,'blocked-source')==result
    assert not any(c.args[0].startswith('INSERT') for c in cur.execute.call_args_list)


def test_workflow_is_serial_and_separates_notification_credentials():
    root=Path(__file__).resolve().parents[1]
    workflow=yaml.load((root/'.github/workflows/backtest_extra_trees.yml').read_text(),Loader=yaml.BaseLoader)
    job=workflow['jobs']['comparison']
    assert job['strategy']['matrix']['horizon']==['5','10','20','30','60']
    assert job['strategy']['max-parallel']=='1' and job['strategy']['fail-fast']=='false'
    calc=next(s for s in job['steps'] if s.get('id')=='backtest')
    assert not any(k.startswith('TELEGRAM') for k in calc['env'])
    assert workflow['concurrency']['group']=='matched-historical-ml-backtest'
