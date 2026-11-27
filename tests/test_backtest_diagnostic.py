from copy import deepcopy
from analyses.backtest_diagnostic import selected_market_view


def inputs():
    dates=['2026-11-03','2026-11-04','2026-11-05','2026-11-06','2026-11-09',
           '2026-11-10','2026-11-11','2026-11-12','2026-11-13','2026-11-16']
    members=[f'A{i}' for i in range(10)]
    plan=dict(selection_date='2026-11-01',decided_at='2026-11-02T06:00:00+00:00',sessions=dates,
              universe=members+['MISSING'],books={'a_par:a_par_model_v1':members,'equal_universe':members+['MISSING'],'benchmark':['SPY']})
    cache={t:dict(currency='USD',bars=[dict(day=d,open=100.,close=110.) for d in dates]) for t in members+['SPY']}
    return [plan],cache


def test_limited_view_keeps_all_model_positions_and_original_full_plan():
    plans,cache=inputs();original=deepcopy(plans)
    r=selected_market_view(plans,cache,'2026-11-20')
    assert plans==original
    assert r['closed_batches']==1
    assert set(r['books'])=={'a_par:a_par_model_v1','benchmark'}
    assert r['equal_universe_comparison']=='unavailable_in_this_view'
    assert not r['full_comparison_available'] and not r['selection_changed']
    assert r['books']['a_par:a_par_model_v1']['excess_vs_universe_pp'] is None
    assert not r['profitability_proven']


def test_missing_held_stock_is_not_dropped_to_make_view_complete():
    plans,cache=inputs();del cache['A0']
    r=selected_market_view(plans,cache,'2026-11-20')
    assert r['closed_batches']==0 and r['stopped_at']=='missing_prices'
    assert r['books']=={}
