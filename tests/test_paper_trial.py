from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from analyses.paper_trial import PROTOCOL, TRIAL, digest, plan, simulate, metrics, freeze_batch, load_candidates


def fixture():
    config = {**PROTOCOL, 'horizon': 3, 'top_n': 2, 'commission_bps': 0, 'slippage_bps': 0}
    snapshot = dict(decided_at='2026-11-20T20:00:00+00:00', sessions=['2026-11-23','2026-11-24','2026-11-25'],
                    books={'m':['A','B'], 'equal_universe':['A','B','C'], 'benchmark':['SPY']})
    prices = {(t,d):dict(open=100.,close=100.,dividend=0.,split=0.,currency='USD')
              for t in ['A','B','C','SPY'] for d in snapshot['sessions']}
    initial = dict.fromkeys(snapshot['books'], 1000.)
    return config,snapshot,prices,initial


def test_ranking_uses_frozen_common_usd_universe():
    data={'a':[dict(ticker=t,score=s,currency=c) for t,s,c in [('A',2,'USD'),('B',1,'USD'),('C',99,'EUR')]],
          'b':[dict(ticker=t,score=s,currency=c) for t,s,c in [('A',0,'USD'),('B',2,'USD'),('X',99,'USD')]]}
    p=plan(data,['a','b'],2)
    assert p['universe']==['A','B']
    assert p['books']['a']==['A','B'] and p['books']['b']==['B','A']
    assert p['books']['equal_universe']==['A','B']
    with pytest.raises(ValueError,match='Te weinig'):
        plan(data,['a','b'],3)
    data['a'].append(data['a'][0])
    with pytest.raises(ValueError,match='Dubbele'):
        plan(data,['a','b'],2)


def test_entry_is_not_selection_close_and_exact_horizon_liquidates():
    cfg,s,p,initial=fixture()
    p['A','2026-11-23']['open']=200.
    p['A','2026-11-23']['close']=200.
    p['A','2026-11-24']['close']=200.
    p['A','2026-11-25']['close']=220.
    r=simulate(s,p,initial,cfg,as_of='2026-11-26')
    assert r['status']=='complete'
    assert r['ending']['m']==pytest.approx(1050.)
    assert r['entry']=='2026-11-23' and r['exit']=='2026-11-25'
    assert all(n['cash']>=0 for n in r['nav'])
    assert all(n['positions']==0 for n in r['nav'] if n['day']==r['exit'])


def test_future_prices_do_not_accelerate_exit():
    cfg,s,p,initial=fixture()
    r=simulate(s,p,initial,cfg,as_of='2026-11-25')
    assert r['status']=='open' and r['exit'] is None
    assert {x['day'] for x in r['nav']}=={'2026-11-23','2026-11-24'}


@pytest.mark.parametrize('ticker', ['A','C','SPY'])
def test_missing_prices_block_whole_comparison_including_baseline(ticker):
    cfg,s,p,initial=fixture();del p[ticker,'2026-11-24']
    r=simulate(s,p,initial,cfg,as_of='2026-11-26')
    assert r['status']=='missing_prices' and r['nav']==[]


def test_missing_first_benchmark_session_cannot_shift_the_calendar():
    cfg,s,p,initial=fixture();del p['SPY','2026-11-23']
    assert simulate(s,p,initial,cfg,as_of='2026-11-26')['status']=='missing_prices'


def test_costs_are_charged_on_both_sides_and_capital_is_finite():
    cfg,s,p,initial=fixture();cfg.update(commission_bps=5,slippage_bps=10)
    r=simulate(s,p,initial,cfg,as_of='2026-11-26')
    expected=1000*((1-.001)*(1-.0005))/((1+.001)*(1+.0005))
    assert r['ending']['m']==pytest.approx(expected)
    assert all(n['equity']<=1000 for n in r['nav'])
    assert max(n['positions'] for n in r['nav'] if n['book']=='m')==2


def test_dividends_only_for_shares_held_before_ex_date():
    cfg,s,p,initial=fixture()
    p['A','2026-11-23']['dividend']=10.
    p['A','2026-11-24']['dividend']=2.
    r=simulate(s,p,initial,cfg,as_of='2026-11-26')
    assert r['ending']['m']==pytest.approx(1010.)


def test_split_blocks_even_if_provider_restates_later_history():
    cfg,s,p,initial=fixture()
    p['A','2026-11-30']=dict(open=50.,close=50.,split=2.,dividend=0.,currency='USD')
    assert simulate(s,p,initial,cfg,as_of='2026-12-01')['status']=='corporate_action_review'


def test_drawdown_and_net_winrate_include_costs_and_overlap_is_rejected():
    nav=[dict(book='m',day=d,equity=e,positions=1) for d,e in [('1',1100),('2',880),('3',990)]]
    trades=[dict(book='m',exit='3',net_pnl=-10,turnover_notional_book=1990)]
    r=metrics(nav,trades,1000,1)
    assert r['status']=='insufficient_history' and not r['profitability_proven']
    assert r['books']['m']['max_drawdown_pct']==pytest.approx(-20)
    assert r['books']['m']['net_return_pct']==pytest.approx(-1)
    assert r['books']['m']['win_rate']==0
    with pytest.raises(ValueError,match='Overlappende'):
        metrics(nav+nav,trades,1000,2)
    assert metrics(nav,trades,1000,12)['status']=='descriptive_only'


def test_reinvestment_uses_previous_ending_capital_not_fresh_money():
    cfg,s,p,initial=fixture()
    for t in ('A','B'):
        p[t,'2026-11-25']['close']=110.
    r=simulate(s,p,initial,cfg,as_of='2026-11-26')
    s['decided_at']='2026-11-25T22:00:00+00:00'
    s['sessions']=['2026-11-26','2026-11-27','2026-11-30']
    p={(t,d):dict(open=100.,close=110. if d=='2026-11-30' else 100.,dividend=0.,split=0.,currency='USD')
        for t in ['A','B','C','SPY'] for d in s['sessions']}
    second=simulate(s,p,r['ending'],cfg,as_of='2026-12-01')
    assert second['ending']['m']==pytest.approx(1210.)


def test_protocol_migration_is_same_as_runtime_and_private():
    root=Path(__file__).resolve().parents[1]
    sql=(root/'maintenance/sql/paper_trial.sql').read_text()
    seed=sql.split("VALUES('",1)[1].split("','",1)[1].split("'::jsonb",1)[0]
    assert json.loads(seed)==PROTOCOL
    assert sql.count('ENABLE ROW LEVEL SECURITY')==5
    assert 'SECURITY DEFINER' not in sql
    assert 'paper_prices_immutable' in sql and 'paper_nav_immutable' in sql
    workflow=(root/'.github/workflows/paper_trial.yml').read_text()
    import yaml
    steps=yaml.load(workflow,Loader=yaml.BaseLoader)['jobs']['paper']['steps']
    calculate=next(s for s in steps if s.get('run')=='python -m analyses.paper_dispatch')
    notify=next(s for s in steps if s.get('run')=='python -m analyses.paper_telegram')
    assert not any(k.startswith('TELEGRAM') for k in calculate.get('env',{}))
    assert notify['env']['TELEGRAM_TOKEN']=='${{ secrets.TELEGRAM_TOKEN }}'
    assert 'EMAIL' not in workflow


def test_no_forecast_ever_starts_from_before_trial_start():
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value=None
    now=datetime(2026,11,27,tzinfo=timezone.utc)
    trial=dict(id=TRIAL,protocol=PROTOCOL,started_at=now)
    assert load_candidates(conn,trial,now)==({}, {})
    sql,params=cur.execute.call_args.args
    assert "r.mode='prospective'" in sql and 'r.train_end IS NOT NULL' in sql
    assert params[2]==trial['started_at'] and params[4]==now


def test_snapshot_uses_official_sessions_not_provider_availability(monkeypatch):
    import analyses.paper_trial as module
    now=datetime(2026,11,27,6,tzinfo=timezone.utc)
    rows=[dict(ticker=f'A{i}',score=float(i)) for i in range(10)]
    provenance={'m':dict(selection_date='2026-11-26',run_id='run')}
    monkeypatch.setattr(module,'load_candidates',lambda *a: ({'m':rows},provenance))
    class Clock:
        @staticmethod
        def now(tz): return now
        fromisoformat=datetime.fromisoformat
    monkeypatch.setattr(module,'datetime',Clock)
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value=[]
    fetch=MagicMock(return_value=(None,'USD'))
    assert freeze_batch(conn,dict(id=TRIAL,protocol=PROTOCOL),now,fetch)
    sql,args=cur.execute.call_args.args
    snap=args[2].adapted
    assert snap['sessions'][0]=='2026-11-30'  # conservative next UTC day, then weekend
    assert len(snap['sessions'])==10
    assert snap['decided_at']==now.isoformat()
    assert args[3]==digest(snap)
    conn.commit.assert_called_once()
