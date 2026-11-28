from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
import yaml

from analyses import backtest_europe_ml as ml
from analyses.backtest_europe_ml_telegram import message
from analyses.backtest_telegram import notify
from analyses.europe_market import BENCHMARK


def fixture():
    rows=[];meta=[]
    dates=list(pd.bdate_range('2026-07-01',periods=10))+[pd.Timestamp('2026-09-15')]
    for day in dates:
        for i in range(20):
            rows.append(dict(ticker=f'T{i:02}.AS',datum=day,
                             fwd_ret_10d=np.nan if day.month==9 else i/4.-2.,
                             **{f:float(i+1) for f in ml.FEATURES}))
            meta.append(dict(ticker=f'T{i:02}.AS',datum=day,beurs='041 Benelux Ierland',labels_review=None,
                             label_date=pd.Timestamp('2026-10-01' if day.month==9 else '2026-07-31')))
    anchor=dict(selection_date='2026-09-15',decided_at='2026-09-16T06:00:00+00:00',
                sessions=[str(d.date()) for d in pd.bdate_range('2026-09-17',periods=10)],
                universe=[f'T{i:02}.AS' for i in range(20)],
                candidates={ml.APAR:[dict(ticker=f'T{i:02}.AS',score=100.-i,currency='EUR') for i in range(20)]})
    return pd.DataFrame(rows),pd.DataFrame(meta),[anchor]


class FakeModel:
    def predict_proba(self,current):
        p=current.iloc[:,0].to_numpy()/100
        return np.column_stack([1-p,p])
    def predict(self,current):return current.iloc[:,0].to_numpy()/10-1


def fake_fit(name,train):
    assert name in ml.MODEL_BOOKS[1:] and list(train.columns)==[*ml.FEATURES,'target']
    if name in (ml.ELASTIC,ml.EXTRA):assert train.target.nunique()==20
    else:assert set(train.target.unique())=={0,1}
    return FakeModel()


@pytest.fixture(autouse=True)
def fake_diagnostics(monkeypatch):
    monkeypatch.setattr(ml,'model_diagnostics',lambda *args:dict(test_diagnostic=True))


def test_all_models_get_identical_european_candidates_and_only_mature_training():
    frame,meta,anchors=fixture();original=deepcopy(anchors);calls=[]
    def fit(name,train):
        assert len(train)==200
        calls.append(name);return fake_fit(name,train)
    p=ml.prepare_comparison(frame,meta,anchors,10,fit)['plans'][0]
    assert anchors==original and calls==list(ml.MODEL_BOOKS[1:])
    assert set(p['books'])==set(ml.MODEL_BOOKS)|{'equal_universe','benchmark'}
    assert p['books'][ml.APAR][0]=='T00.AS' and p['books'][ml.XGB][0]=='T19.AS'
    assert p['sessions']==anchors[0]['sessions'] and p['books']['benchmark']==[BENCHMARK]
    assert p['training']['rows']==200 and p['training']['dates']==10
    assert p['training']['latest_label']<'2026-09-16'


def test_future_labels_and_non_european_rows_do_not_change_selection():
    frame,meta,anchors=fixture()
    first=ml.prepare_comparison(frame,meta,anchors,10,fake_fit)
    frame.loc[frame.datum==pd.Timestamp('2026-09-15'),'fwd_ret_10d']=1e12
    extra=frame.iloc[:1].copy();extra['ticker']='US';extra['ma50']=99999
    other=meta.iloc[:1].copy();other['ticker']='US';other['beurs']='048 Nasdaq/NYSE'
    second=ml.prepare_comparison(pd.concat([frame,extra]),pd.concat([meta,other]),anchors,10,fake_fit)
    assert first==second


@pytest.mark.parametrize('problem',['review','unknown','embargo','one_class','few_candidates'])
def test_shared_invalid_cohorts_do_not_fit_or_invent_returns(problem):
    frame,meta,anchors=fixture();old=frame.datum<pd.Timestamp('2026-09-15')
    if problem=='review':meta['labels_review']='review'
    if problem=='unknown':meta['label_date']=pd.Timestamp('2026-12-01')
    if problem=='embargo':
        frame.loc[old,'datum']+=pd.Timedelta(days=60);meta.loc[old,'datum']+=pd.Timedelta(days=60)
    if problem=='one_class':frame.loc[old,'fwd_ret_10d']=5.
    if problem=='few_candidates':frame.loc[~old & (frame.ticker>'T08.AS'),'ma50']=np.nan
    fit=MagicMock();result=ml.prepare_comparison(frame,meta,anchors,10,fit)
    assert not result['plans'] and result['skipped_cohorts'];fit.assert_not_called()


def test_missing_feature_affects_every_book_and_continuous_targets_are_hashed():
    frame,meta,anchors=fixture()
    frame.loc[(frame.datum==pd.Timestamp('2026-09-15')) & (frame.ticker=='T19.AS'),'ma50']=np.nan
    first=ml.prepare_comparison(frame,meta,anchors,10,fake_fit)['plans'][0]
    assert len(first['universe'])==19 and first['books'][ml.XGB][0]=='T18.AS'
    frame.loc[frame.datum<pd.Timestamp('2026-09-15'),'fwd_ret_10d']*=2
    second=ml.prepare_comparison(frame,meta,anchors,10,fake_fit)['plans'][0]
    assert first['training']['input_hash']!=second['training']['input_hash']


def test_flat_predictions_skip_whole_cohort_without_alphabetic_winners():
    frame,meta,anchors=fixture()
    class Flat(FakeModel):
        def predict(self,current):return np.zeros(len(current))
    def fit(name,train):return Flat() if name==ml.ELASTIC else fake_fit(name,train)
    r=ml.prepare_comparison(frame,meta,anchors,10,fit)
    assert not r['plans'] and r['skipped_cohorts'][0]['models']==[ml.ELASTIC]


@pytest.mark.parametrize('bad',['nan','shape','not_probability'])
def test_invalid_classifier_outputs_fail_visibly(bad):
    frame,meta,anchors=fixture()
    model=MagicMock()
    model.predict_proba.return_value=(np.full((20,2),np.nan) if bad=='nan' else
                                     np.zeros((20,3)) if bad=='shape' else np.full((20,2),.2))
    with pytest.raises(ValueError,match='waarschijnlijkheden'):
        ml.prepare_comparison(frame,meta,anchors,10,lambda *args:model)


@pytest.mark.parametrize('name',[ml.XGB,ml.LGB,ml.CAT,ml.ELASTIC,ml.EXTRA])
def test_real_recipes_fit_and_score_without_loading_or_publishing_models(name):
    rng=np.random.default_rng(42)
    train=pd.DataFrame(rng.normal(size=(200,len(ml.FEATURES))),columns=ml.FEATURES)
    train['target']=train.iloc[:,0]*3 if name in (ml.ELASTIC,ml.EXTRA) else (train.iloc[:,0]>0).astype(int)
    with ml.threadpool_limits(limits=1,user_api='blas'):model=ml.fit_model(name,train)
    scores=model.predict(train[ml.FEATURES].iloc[:5]) if name in (ml.ELASTIC,ml.EXTRA) else model.predict_proba(train[ml.FEATURES].iloc[:5])[:,1]
    assert np.isfinite(scores).all() and np.ptp(scores)>0


def test_frozen_ranks_precede_evaluation_private_storage_and_parent_is_pinned(monkeypatch):
    frame,meta,anchors=fixture()
    parent=dict(run_key='euro-parameter-frozen',config=ml.europe.configuration('2026-11-27'),
                selection_snapshot=dict(plans=anchors,data_start='2026-07-01',data_end='2026-09-15'))
    original=deepcopy(parent);conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},None,parent]
    cache={t:dict(currency='EUR',bars=[dict(day=d,open=100.,close=100.) for d in anchors[0]['sessions']])
           for t in anchors[0]['universe']+[BENCHMARK] if t!='T19.AS'}
    cur.fetchall.return_value=[dict(ticker=t,data=v) for t,v in cache.items()]
    monkeypatch.setattr(ml.native,'restore_inputs',lambda *args:(frame,meta))
    monkeypatch.setattr(ml.native,'reconstruct_targets',lambda *args:(frame,meta))
    monkeypatch.setattr(ml,'fit_model',fake_fit)
    prepare=ml.prepare_comparison
    monkeypatch.setattr(ml,'prepare_comparison',lambda *args:prepare(*args,fitter=fake_fit))
    evaluate=ml.europe.evaluate
    def checked(*args,**kwargs):
        assert conn.commit.call_count==2
        assert any('SET selection_snapshot' in c.args[0] for c in cur.execute.call_args_list)
        return evaluate(*args,**kwargs)
    monkeypatch.setattr(ml.europe,'evaluate',checked)
    result=ml.run(conn,'euro-ml')
    assert result['status']=='complete' and parent==original
    assert result['periods'][0]['allocations'][ml.XGB]['invested_budget']==90000
    assert len(result['books'])==8
    writes=[c.args for c in cur.execute.call_args_list if c.args[0].startswith(('INSERT','UPDATE'))]
    assert all('public.research_backtest' in args[0] for args in writes)
    config=writes[0][1][2].adapted
    text=message(dict(run_key='euro-ml',config=config,status=result['status'],result=result))
    assert 'belegd €90,000' in text and '$' not in text and len(text.encode('utf-16-le'))//2<3900
    cur.reset_mock();cur.fetchone.side_effect=[{'locked':True},dict(config=config,status='complete',result=result),parent]
    assert ml.run(conn,'euro-ml')==result and cur.execute.call_args_list[2].args[1]==('euro-parameter-frozen',)


def test_eight_book_formatter_uses_durable_transport_and_separate_credentials():
    row=dict(run_key='europe_ml:test',status='partial',config=dict(horizon=10,initial_cash=100000.),
             result=dict(books={},closed_batches=0,reason='Testmelding onvolledige invoer'))
    text=message(row)
    assert 'Nog geen geldige rendementsvergelijking' in text and '0.00%' not in text
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},row,{'status':'claimed'}]
    def send(text,*args):
        assert conn.commit.call_count==1 and 'geen geldige' in text
        return 'sent',123
    assert notify(conn,row['run_key'],'token','chat',send,formatter=message)==1
    root=Path(__file__).resolve().parents[1]
    workflow=yaml.load((root/'.github/workflows/backtest_europe_ml.yml').read_text(),Loader=yaml.BaseLoader)
    steps=workflow['jobs']['comparison']['steps']
    assert not any(k.startswith('TELEGRAM') for s in steps if s.get('id')=='backtest' for k in s['env'])
    assert workflow['jobs']['comparison']['env']['OPENBLAS_NUM_THREADS']=='1'
