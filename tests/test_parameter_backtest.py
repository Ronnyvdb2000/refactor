from datetime import date
from unittest.mock import MagicMock
import json
from pathlib import Path

import pandas as pd
import pytest
from analyses import backtest_parameter as bt


def data():
    dates=pd.date_range('2026-08-01','2026-10-31',freq='B')
    rows=[dict(ticker=f'A{i}',datum=d,factor=float(i),fwd_ret_10d=float(i)) for d in dates for i in range(25)]
    meta=[dict(ticker=r['ticker'],datum=r['datum'],beurs='048 Nasdaq/NYSE',
               label_date=r['datum']+pd.Timedelta(days=16),labels_review=None) for r in rows]
    return pd.DataFrame(rows),pd.DataFrame(meta)


def test_training_never_gets_future_labels_and_decisions_do_not_overlap(monkeypatch):
    frame,meta=data();calls=[]
    def fit(df,target,day,embargo,*a):
        mature=df.loc[df[target].notna()]
        assert all(mature.datum+pd.Timedelta(days=16)<day+pd.Timedelta(days=1))
        assert all(df.datum<=day)
        calls.append(day)
        return pd.DataFrame([dict(parameter='factor',ic=.1,ic_abs=.1)]),'fixed',day-pd.Timedelta(days=53),day-pd.Timedelta(days=25)
    monkeypatch.setattr(bt.model,'ic_met_fallback',fit)
    p=bt.prepare(frame,meta,date(2026,11,1))['plans']
    assert len(p)>1
    for left,right in zip(p,p[1:]):
        assert right['selection_date']>left['sessions'][-1]
    assert p[0]['books']['a_par:a_par_model_v1']==[f'A{i}' for i in range(24,14,-1)]
    assert p[0]['sessions'][0]>p[0]['decided_at'][:10]
    assert len(p[0]['sessions'])==10


def test_change_future_outcomes_cannot_change_first_historical_ranking(monkeypatch):
    frame,meta=data()
    weights=pd.DataFrame([dict(parameter='factor',ic=.1,ic_abs=.1)])
    monkeypatch.setattr(bt.model,'ic_met_fallback',lambda *a:(weights,'fixed',pd.Timestamp('2026-07-01'),pd.Timestamp('2026-07-15')))
    first=bt.prepare(frame,meta,date(2026,11,1))['plans'][0]
    frame['fwd_ret_10d']=frame['fwd_ret_10d']*-1000
    second=bt.prepare(frame,meta,date(2026,11,1))['plans'][0]
    assert first['books']==second['books']


def test_future_or_reviewed_training_targets_are_masked(monkeypatch):
    frame,meta=data();meta['labels_review']='needs_review'
    def fit(df,target,day,*args):
        assert df[target].isna().all()
        return pd.DataFrame(),'none',day,day
    monkeypatch.setattr(bt.model,'ic_met_fallback',fit)
    assert bt.prepare(frame,meta,date(2026,11,1))['plans']==[]


def test_insufficient_universe_does_not_select_using_available_outcomes(monkeypatch):
    frame,meta=data();meta['beurs']='045 Londen'
    weights=pd.DataFrame([dict(parameter='factor',ic=.1,ic_abs=.1)])
    monkeypatch.setattr(bt.model,'ic_met_fallback',lambda *a:(weights,'fixed',pd.Timestamp('2026-07-01'),pd.Timestamp('2026-07-15')))
    result=bt.prepare(frame,meta,date(2026,11,1))
    assert not result['plans']
    assert all(r['reason']=='insufficient_US_universe' for r in result['skipped'])


def test_missing_baseline_stops_all_compounding():
    snapshot=dict(selection_date='2026-11-01',decided_at='2026-11-02T06:00:00+00:00',
                  sessions=['2026-11-03','2026-11-04','2026-11-05','2026-11-06','2026-11-09',
                            '2026-11-10','2026-11-11','2026-11-12','2026-11-13','2026-11-16'],
                  universe=['A','B'],books={'a_par:a_par_model_v1':['A'],'equal_universe':['A','B'],'benchmark':['SPY']})
    saved={t:dict(currency='USD',bars=[dict(day=d,open=100.,close=110.) for d in snapshot['sessions']]) for t in ['A','SPY']}
    r=bt.evaluate([snapshot,snapshot],saved,date(2026,11,20))
    assert len(r['periods'])==1 and r['closed_batches']==0
    assert r['stopped_at']=='missing_prices'
    assert r['books']=={} and not r['profitability_proven']


def test_backtest_workflow_and_storage_are_separate_from_live_paper():
    root=Path(__file__).resolve().parents[1]
    source=(root/'analyses/backtest_parameter.py').read_text()
    assert 'paper_prices' not in source and 'paper_batches' not in source
    assert 'INSERT INTO public.ml_runs' not in source
    assert 'model.main(' not in source
    assert 'auto_adjust=True' in source
    workflow=(root/'.github/workflows/backtest_parameter.yml').read_text()
    assert 'TELEGRAM' not in workflow and 'schedule:' not in workflow
    assert "ON CONFLICT" not in (root/'maintenance/sql/research_backtests.sql').read_text()


def test_training_target_uses_tenth_real_session_not_calendar_days():
    frame=pd.DataFrame([dict(ticker='A',datum=pd.Timestamp('2026-10-02'),fwd_ret_10d=999.)])
    meta=pd.DataFrame([dict(ticker='A',datum=pd.Timestamp('2026-10-02'),label_date=None)])
    days=pd.bdate_range('2026-10-02',periods=12)
    quotes={'A':dict(bars=[dict(day=str(d.date()),close=100.+i) for i,d in enumerate(days)])}
    rebuilt,dated=bt.reconstruct_targets(frame,meta,quotes)
    assert rebuilt.fwd_ret_10d.iloc[0]==pytest.approx(10.)
    assert dated.label_date.iloc[0]==days[10]
    quotes['A']['bars']=quotes['A']['bars'][:10]
    rebuilt,dated=bt.reconstruct_targets(frame,meta,quotes)
    assert rebuilt.fwd_ret_10d.isna().all() and dated.label_date.isna().all()
