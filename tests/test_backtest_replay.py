from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from analyses import backtest_parameter as bt
from analyses.backtest_replay import run_cached


def inputs():
    return dict(run_key='parent',config=bt.configuration('2026-11-27'),
                selection_snapshot=dict(plans=[{'frozen':'original ranking'}],
                                        data_start='2026-08-01',data_end='2026-11-26'))


def test_replay_uses_exact_frozen_ranking_and_cache_without_provider_or_training(monkeypatch):
    parent=inputs();original=deepcopy(parent)
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},parent,None]
    cur.fetchall.return_value=[dict(ticker='A',data={'bars':[]})]
    def evaluate(plans,cache,as_of,horizon=10):
        assert plans==parent['selection_snapshot']['plans'] and cache=={'A':{'bars':[]}}
        assert as_of=='2026-11-27'
        return dict(status='insufficient_history',closed_batches=1,planned_batches=1,stopped_at='complete')
    monkeypatch.setattr(bt,'evaluate',evaluate)
    monkeypatch.setattr(bt,'fetch',lambda *a: pytest.fail('Replay must not fetch quotes'))
    monkeypatch.setattr(bt,'prepare',lambda *a: pytest.fail('Replay must not rerank'))
    result=run_cached(conn,'new')
    assert parent==original and result['status']=='complete'
    assert not result['selection_changed'] and result['parent_run']=='parent'
    insert=next(call.args for call in cur.execute.call_args_list if call.args[0].startswith('INSERT INTO public.research_backtests'))
    assert insert[1][3].adapted==parent['selection_snapshot']
    copy=next(call.args for call in cur.execute.call_args_list if call.args[0].startswith('INSERT INTO public.research_backtest_cache'))
    assert copy[1]==('new','parent')


@pytest.mark.parametrize('field',['quote_basis','implementation_hash'])
def test_incompatible_model_or_price_basis_is_rejected(field):
    parent=inputs();parent['config'][field]='different'
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},parent]
    with pytest.raises(ValueError):
        run_cached(conn,'new')
    assert not any(call.args[0].startswith('INSERT') for call in cur.execute.call_args_list)


def test_no_frozen_parent_never_silently_runs_new_selections():
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},None]
    with pytest.raises(ValueError,match='Geen vastgelegde'):
        run_cached(conn,'new')
