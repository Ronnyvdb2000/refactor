from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
import yaml

from research import filter_ab as ab
from scripts import bot_041mV2 as native

NOW=datetime(2026,11,28,5,tzinfo=timezone.utc)


def inputs(vol=.15,volume=1_000_000):
    rng=np.random.default_rng(42)
    close=100*np.exp(np.cumsum(rng.normal(0,vol/np.sqrt(252),252)))
    hist=pd.DataFrame(dict(Open=close,High=close*1.01,Low=close*.99,Close=close,Volume=volume),
                      index=pd.bdate_range(end='2026-11-27',periods=252))
    info=dict(returnOnEquity=.1,revenueGrowth=.05,profitMargins=.12,debtToEquity=30.,sector='Industrials')
    return info,hist


def test_only_minimum_volatility_changes_and_native_defaults_stay_intact():
    original=deepcopy(native.CRITERIA)
    p=ab.protocol()
    assert p['A']['VOL_MIN']==.20 and p['B']['VOL_MIN']==0
    assert {k for k in p['A'] if p['A'][k]!=p['B'][k]}=={'VOL_MIN'}
    assert p['hold_weeks']==native.MAX_WEKEN_BUITEN==3
    assert native.CRITERIA==original


def test_quiet_financially_eligible_stock_is_the_intended_single_difference():
    info,hist=inputs()
    r=ab.paired_observation('QUIET',info,hist,now=NOW)
    assert r['available'] and not r['pass_A'] and r['pass_B']
    assert r['annual_volatility']<.20
    assert r['observed_at']==NOW.isoformat() and r['quote_day']=='2026-11-27'
    assert r['features'] and r['info']['returnOnEquity']==.1


@pytest.mark.parametrize('vol',[.05,.2,.4,.6,.9,1.2])
def test_b_never_excludes_a_stock_that_passes_a(vol):
    info,hist=inputs(vol)
    r=ab.paired_observation('TEST',info,hist,now=NOW)
    assert not r['pass_A'] or r['pass_B']


@pytest.mark.parametrize('change',['low_margin','high_debt','illiquid','too_volatile'])
def test_other_rules_still_reject_b(change):
    info,hist=inputs(vol=1.3 if change=='too_volatile' else .2,
                     volume=100 if change=='illiquid' else 1_000_000)
    if change=='low_margin':info['profitMargins']=.01
    if change=='high_debt':info['debtToEquity']=200.
    r=ab.paired_observation('TEST',info,hist,now=NOW)
    assert not r['pass_A'] and not r['pass_B']


def test_existing_growth_alternative_and_sector_debt_exceptions_are_preserved():
    info,hist=inputs();info.update(returnOnEquity=.01,revenueGrowth=.2,debtToEquity=300,sector='Financial Services')
    assert ab.paired_observation('BANK',info,hist,now=NOW)['pass_B']


def test_missing_fundamentals_and_incomplete_current_day_are_not_historical_passes():
    info,hist=inputs()
    missing=ab.paired_observation('MISSING',{},hist,now=NOW,applied=False)
    assert missing['pass_A'] is None and missing['pass_B'] is None
    future=ab.paired_observation('FUTURE',info,hist,now=datetime(2026,11,27,tzinfo=timezone.utc))
    assert not future['available'] and future['pass_B'] is None


def test_identical_grace_periods_separate_shadow_state_and_no_mutation():
    info,hist=inputs()
    obs=ab.paired_observation('QUIET',info,hist,now=NOW)
    seed={'QUIET':dict(ticker='QUIET',status='zwakker',weken_buiten=2)};original=deepcopy(seed)
    result=ab.compare_states(seed,seed,[obs],actual_a=[])
    assert seed==original and not result['A_replay_mismatch']
    assert result['A_count']==0 and result['B_members']==['QUIET']
    assert result['B_state']['QUIET']['weken_buiten']==0
    assert result['portfolio_ready'] is False and result['return_pct'] is None


def test_missing_data_does_not_age_a_stock_and_delisted_blocks_both_variants():
    info,hist=inputs();seed={'HOLD':dict(ticker='HOLD',status='actief',weken_buiten=0)}
    obs=ab.paired_observation('HOLD',{},None,applied=False,now=NOW)
    result=ab.compare_states(seed,seed,[obs])
    assert result['A_members']==result['B_members']==['HOLD']
    obs['blocked']=True
    result=ab.compare_states(seed,seed,[obs])
    assert result['A_count']==result['B_count']==0


def test_native_applied_failure_is_replayed_even_when_input_is_unavailable():
    seed={'A':dict(ticker='A',status='zwakker',weken_buiten=2)}
    obs=ab.paired_observation('A',{},None,applied=True,now=NOW)
    r=ab.compare_states(seed,seed,[obs],actual_a=[])
    assert r['A_count']==r['B_count']==0
    assert r['unavailable']==1 and not r['A_replay_mismatch']


def test_mismatch_of_replayed_control_is_exposed():
    info,hist=inputs(.6)
    obs=ab.paired_observation('A',info,hist,now=NOW)
    r=ab.compare_states({}, {},[obs],actual_a=[])
    assert r['A_replay_mismatch']==['A']


def test_supplied_fundamental_snapshot_never_fetches_current_data(monkeypatch):
    info,_=inputs();fetch=MagicMock(side_effect=AssertionError('Unexpected live fetch'))
    monkeypatch.setattr(native.yf,'Ticker',fetch)
    assert native.check_fundamenteel('A',native.get_criteria('048'),info=info)[0]
    fetch.assert_not_called()


def test_observations_are_first_write_only_and_errors_are_visible():
    recorder=object.__new__(ab.Recorder);recorder.conn=MagicMock();recorder.key='test';recorder.errors=0
    recorder.expected=['A','B']
    info,hist=inputs()
    recorder.observe_safe('A',info,hist)
    sql,args=recorder.conn.cursor.return_value.__enter__.return_value.execute.call_args.args
    assert 'ON CONFLICT(cycle_key,ticker) DO NOTHING' in sql
    assert len(args[3])==64
    recorder.conn.cursor.side_effect=RuntimeError('database error')
    recorder.observe_safe('B',info,hist)
    assert recorder.errors==1


def test_schema_and_workflows_isolate_observations_from_production():
    root=Path(__file__).resolve().parents[1]
    sql=(root/'maintenance/sql/filter_ab.sql').read_text()
    assert sql.count('ENABLE ROW LEVEL SECURITY')==3 and 'REVOKE ALL' in sql
    assert 'observations_guard' in sql
    source=(root/'research/filter_ab.py').read_text()
    assert 'INSERT INTO public.sel_par' not in source and 'INSERT INTO public.selecties' not in source
    assert 'sla_export_op(' not in source and 'sla_master_op(' not in source
    workflow=yaml.load((root/'.github/workflows/filter_ab.yml').read_text(),Loader=yaml.BaseLoader)
    steps=workflow['jobs']['observe']['steps']
    sample=next(s for s in steps if s.get('run')=='python -m research.filter_ab sample --limit 60')
    assert not any(k.startswith('TELEGRAM') for k in sample['env'])
    production=yaml.load((root/'.github/workflows/run__filtertickersmungerV2.yml').read_text(),Loader=yaml.BaseLoader)
    run=next(s for s in production['jobs']['filter_tickers']['steps'] if s.get('name')=='Run filter bot')
    assert run['env']['FILTER_AB_CAPTURE']=='1'


def test_us_twenty_percent_does_not_change_canada_or_other_criteria():
    for venue in ('048','056','057'):
        expected={**native.CRITERIA['noordamerika'],'VOL_MIN':.20}
        assert native.get_criteria(venue)==expected
    for venue in ('047','055','058'):
        assert native.get_criteria(venue)['VOL_MIN']==.35
    assert native.get_criteria('041')['VOL_MIN']==.20
    assert ab.protocol()['version']==2
