from datetime import date
import json
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
import yaml

from analyses import backtest_parameter as bt
from analyses.backtest_replay import run_cached
from common.horizons import SUPPORTED_HORIZONS, calendar_embargo


@pytest.mark.parametrize('horizon', SUPPORTED_HORIZONS)
def test_labels_use_the_requested_number_of_provider_sessions(horizon):
    days=pd.bdate_range('2026-01-02',periods=horizon+2)
    frame=pd.DataFrame([dict(ticker='A',datum=days[0],fwd_ret_10d=-999.)])
    metadata=pd.DataFrame([dict(ticker='A',datum=days[0],label_date=None)])
    cache={'A':dict(bars=[dict(day=str(d.date()),close=100.+i) for i,d in enumerate(days)])}
    rebuilt,dated=bt.reconstruct_targets(frame,metadata,cache,horizon)
    assert rebuilt[f'fwd_ret_{horizon}d'].iloc[0]==pytest.approx(horizon)
    assert dated.label_date.iloc[0]==days[horizon]
    if horizon!=10:
        assert rebuilt.fwd_ret_10d.iloc[0]==-999.
    cache['A']['bars']=cache['A']['bars'][:horizon]
    rebuilt,dated=bt.reconstruct_targets(frame,metadata,cache,horizon)
    assert rebuilt[f'fwd_ret_{horizon}d'].isna().all() and dated.label_date.isna().all()


@pytest.mark.parametrize('horizon', SUPPORTED_HORIZONS)
def test_training_target_maturity_embargo_and_nonoverlap_follow_horizon(monkeypatch,horizon):
    target=f'fwd_ret_{horizon}d'
    frame=pd.DataFrame([dict(ticker=f'A{i}',datum=d,factor=float(i),**{target:float(i)})
                        for d in pd.bdate_range('2026-01-01','2026-10-31') for i in range(20)])
    metadata=frame[['ticker','datum']].copy()
    metadata['beurs']='048 Nasdaq/NYSE'
    metadata['label_date']=metadata.datum+pd.Timedelta(days=horizon*2)
    metadata['labels_review']=None
    def fit(df,requested,day,embargo,*args):
        assert requested==target
        assert embargo==calendar_embargo(horizon,minimum=bt.model.ModelConfig().embargo_dagen)
        mature=df.loc[df[target].notna()]
        assert all(mature.datum+pd.Timedelta(days=horizon*2)<day+pd.Timedelta(days=1))
        return pd.DataFrame([dict(parameter='factor',ic=.1,ic_abs=.1)]),'fixed',day-pd.Timedelta(days=100),day-pd.Timedelta(days=embargo)
    monkeypatch.setattr(bt.model,'ic_met_fallback',fit)
    plans=bt.prepare(frame,metadata,date(2026,11,1),horizon=horizon)['plans']
    assert len(plans)>1
    assert all(len(p['sessions'])==horizon for p in plans)
    assert all(right['selection_date']>left['sessions'][-1] for left,right in zip(plans,plans[1:]))


@pytest.mark.parametrize('horizon', SUPPORTED_HORIZONS)
def test_costed_evaluation_uses_requested_horizon_and_fixed_cash_slots(horizon):
    sessions=[str(d.date()) for d in pd.bdate_range('2026-01-05',periods=horizon)]
    snapshot=dict(selection_date='2026-01-01',decided_at='2026-01-02T06:00:00+00:00',sessions=sessions,
                  universe=['A','MISSING'],books={'a_par:a_par_model_v1':['A','MISSING'],
                    'equal_universe':['A','MISSING'],'benchmark':['SPY']})
    cache={t:dict(currency='USD',bars=[dict(day=d,open=100.,close=100.) for d in sessions]) for t in ('A','SPY')}
    result=bt.evaluate([snapshot],cache,'2026-12-31',horizon)
    assert result['horizon']==horizon and result['closed_batches']==1
    model=result['books']['a_par:a_par_model_v1']
    expected=90000+10000*((1-.001)*(1-.0005))/((1+.001)*(1+.0005))
    assert model['ending_equity']==pytest.approx(expected)
    assert result['periods'][0]['allocations']['a_par:a_par_model_v1']['invested_budget']==10000
    assert model['valued_through']==sessions[-1]


def test_another_horizon_rebuilds_labels_and_ranking_from_the_same_frozen_inputs(monkeypatch):
    from copy import deepcopy
    parent=dict(run_key='original_10d',config=bt.configuration('2026-11-27'),
                selection_snapshot=dict(plans=[{'original':'10d'}],raw_frame={'unchanged':True}))
    original=deepcopy(parent)
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},parent,None]
    cur.fetchall.return_value=[dict(ticker='A',data={'bars':[]})]
    monkeypatch.setattr(bt,'restore_inputs',lambda selection: ('features','metadata'))
    def rebuild(frame,metadata,cache,horizon):
        assert (frame,metadata,horizon)==('features','metadata',20)
        return 'rebuilt20','dated20'
    monkeypatch.setattr(bt,'reconstruct_targets',rebuild)
    def prepare(frame,metadata,as_of,**kwargs):
        assert (frame,metadata,as_of)==('rebuilt20','dated20','2026-11-27')
        assert kwargs['horizon']==20
        return dict(plans=[{'ranking':'20d'}])
    monkeypatch.setattr(bt,'prepare',prepare)
    def evaluate(plans,cache,as_of,horizon):
        assert plans==[{'ranking':'20d'}] and horizon==20
        return dict(status='insufficient_history',closed_batches=0,planned_batches=1,stopped_at='open')
    monkeypatch.setattr(bt,'evaluate',evaluate)
    result=run_cached(conn,'20d-run',horizon=20)
    assert parent==original and result['selection_changed']
    assert result['status']=='pending_final_period'
    insert=next(c.args for c in cur.execute.call_args_list if c.args[0].startswith('INSERT INTO public.research_backtests'))
    assert insert[1][2].adapted['horizon']==20
    assert insert[1][3].adapted['plans']==[{'ranking':'20d'}]


def test_no_mature_training_history_is_not_reported_as_a_successful_zero_return(monkeypatch):
    parent=dict(run_key='original',config=bt.configuration('2026-11-27'),selection_snapshot={'plans':[]})
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},parent,None];cur.fetchall.return_value=[]
    result=run_cached(conn,'new')
    assert result['status']=='insufficient_history'
    assert result['books']=={} and not result['profitability_proven']


def test_workflow_covers_all_horizons_without_fail_fast_or_lock_contention():
    root=Path(__file__).resolve().parents[1]
    workflow=yaml.load((root/'.github/workflows/backtest_parameter.yml').read_text(),Loader=yaml.BaseLoader)
    job=workflow['jobs']['backtest']
    assert [int(h) for h in job['strategy']['matrix']['horizon']]==list(SUPPORTED_HORIZONS)
    assert job['strategy']['fail-fast']=='false' and job['strategy']['max-parallel']=='1'
    assert job['env']['BACKTEST_HORIZON']=='${{ matrix.horizon }}'


@pytest.mark.parametrize('horizon',[0,7,90,'bad'])
def test_unsupported_horizons_are_rejected(horizon):
    with pytest.raises(ValueError):
        bt.configuration('2026-11-27',horizon)
