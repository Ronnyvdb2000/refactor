from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
import yaml

from analyses import backtest_europe_ml as ml
from common.horizons import SUPPORTED_HORIZONS,calendar_embargo
from test_europe_ml import fixture,fake_fit


@pytest.mark.parametrize('horizon',SUPPORTED_HORIZONS)
def test_each_horizon_uses_own_labels_embargo_and_exact_source_calendar(horizon,monkeypatch):
    frame,meta,anchors=fixture()
    old=frame.datum<pd.Timestamp('2026-09-15')
    frame.loc[old,'datum']-=pd.Timedelta(days=180)
    meta.loc[old,'datum']-=pd.Timedelta(days=180)
    meta.loc[old,'label_date']=pd.Timestamp('2026-05-01')
    frame=frame.rename(columns={'fwd_ret_10d':f'fwd_ret_{horizon}d'})
    anchors[0]['sessions']=[str(d.date()) for d in pd.bdate_range('2026-09-17',periods=horizon)]
    monkeypatch.setattr(ml,'model_diagnostics',lambda *args:{})
    p=ml.prepare_comparison(frame,meta,anchors,horizon,fake_fit)['plans'][0]
    assert len(p['sessions'])==horizon
    assert p['training']['embargo_days']==calendar_embargo(horizon,minimum=25)
    assert p['training']['latest_label']<'2026-09-16'


def test_explicit_source_is_loaded_instead_of_latest_horizon_source(monkeypatch):
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    parent=dict(run_key='explicit-source',status='insufficient_history',
                config=ml.europe.configuration('2026-11-27',60),selection_snapshot=dict(plans=[]))
    cur.fetchone.side_effect=[{'locked':True},None,parent];cur.fetchall.return_value=[]
    # With no eligible source cohorts, no training or predictions can be manufactured.
    frame,meta,_=fixture();frame=frame.rename(columns={'fwd_ret_10d':'fwd_ret_60d'})
    monkeypatch.setattr(ml.native,'restore_inputs',lambda *args:(frame,meta))
    monkeypatch.setattr(ml.native,'reconstruct_targets',lambda *args:(frame,meta))
    result=ml.run(conn,'new-60d',60,source_key='explicit-source')
    assert result['status']=='insufficient_history' and result['books']=={}
    assert cur.execute.call_args_list[2].args[1]==('explicit-source',)
    assert not any('ORDER BY created_at' in c.args[0] for c in cur.execute.call_args_list)


@pytest.mark.parametrize('bad',['horizon','type','running'])
def test_incompatible_or_unfinished_pinned_source_is_rejected_before_writing(bad):
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cfg=ml.europe.configuration('2026-11-27',20)
    if bad=='horizon':cfg['horizon']=10
    if bad=='type':cfg['mode']='historical_reconstruction'
    cur.fetchone.side_effect=[{'locked':True},None,dict(config=cfg,status='running' if bad=='running' else 'complete')]
    with pytest.raises(ValueError,match='andere horizon'):
        ml.run(conn,'bad-source',20,source_key='pinned')
    assert not any(c.args[0].startswith(('INSERT','UPDATE')) for c in cur.execute.call_args_list)


def test_resumed_run_cannot_silently_switch_pinned_source():
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},dict(config={'parent_run':'original'})]
    with pytest.raises(ValueError,match='andere bevroren'):
        ml.run(conn,'retry',20,source_key='replacement')


def test_horizon_workflow_builds_parameter_before_ml_and_isolates_failures():
    root=Path(__file__).resolve().parents[1]
    doc=yaml.load((root/'.github/workflows/backtest_europe_horizons.yml').read_text(),Loader=yaml.BaseLoader)
    job=doc['jobs']['comparison'];steps=job['steps']
    assert job['strategy']['matrix']['horizon']==['5','20','30','60']
    assert job['strategy']['max-parallel']=='1' and job['strategy']['fail-fast']=='false'
    parameter=next(i for i,s in enumerate(steps) if s.get('id')=='parameter')
    machine=next(i for i,s in enumerate(steps) if s.get('id')=='ml')
    assert parameter<machine and "steps.parameter.outcome == 'success'" in steps[machine]['if']
    assert 'a_par_europe_${{ matrix.horizon }}d:${{ github.run_id }}'==steps[machine]['env']['EUROPE_SOURCE_RUN_KEY']
    for step in (steps[parameter],steps[machine]):
        assert not any(k.startswith('TELEGRAM') for k in step['env'])
