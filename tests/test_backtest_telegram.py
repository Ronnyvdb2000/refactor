from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from analyses.backtest_telegram import message, notify


def sample():
    return dict(run_key='a_par_10d:123', status='complete',
                config=dict(initial_cash=100000, horizon=10, as_of='2026-11-27'),
                result=dict(closed_batches=1, planned_batches=1, parent_run='previous',
                    books={'a_par:a_par_model_v1': dict(net_return_pct=4.5,
                        ending_equity=104500, max_drawdown_pct=-2., valued_through='2026-11-19')},
                    periods=[dict(planned_entry='2026-11-06', planned_exit='2026-11-19', status='complete',
                        starting_capital={'a_par:a_par_model_v1': 100000},
                        allocations={'a_par:a_par_model_v1': dict(planned=10,purchased=9,invested_budget=90000)},
                        skipped_entries={'ACA': dict(reason='missing_prices')})]))


def test_summary_reports_returns_cash_exclusions_and_historical_limitations():
    text = message(sample())
    for expected in ('historische reconstructie', '+4.50%', '-2.00%', '9/10 aandelen',
                     'belegd $90,000', 'cash bij instap $10,000', 'ACA',
                     'geen nieuwe koersophaling', 'nog niet bewezen', 'vertekenen'):
        assert expected in text
    assert len(text.encode('utf-16-le')) // 2 < 3900


def test_partial_run_without_results_never_claims_zero_or_completed_returns():
    row = sample(); row.update(status='partial', result=dict(quotes_cached=20,closed_batches=0))
    text = message(row)
    assert 'Nog geen berekend rendement' in text and 'Onvolledige run' in text
    assert 'Netto rendement' not in text


def test_all_cash_allocations_and_many_exclusions_fit_in_one_message():
    row=sample();p=row['result']['periods'][0]
    p['allocations']['a_par:a_par_model_v1'].update(purchased=0,invested_budget=0)
    p['skipped_entries']={f'TICK{i}':{} for i in range(1000)}
    text=message(row)
    assert 'belegd $0; cash bij instap $100,000' in text
    assert '1000 unieke aandelen' in text and 'TICK999' not in text
    assert len(text.encode('utf-16-le'))//2 < 3900


def db(previous=None):
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},sample(), {'status':'claimed'} if previous is None else None] + (
        [{'status':previous}] if previous is not None else [])
    return conn,cur


def test_claim_is_durable_before_http_and_only_explicit_run_is_loaded():
    conn,cur=db()
    def sender(text, token, chat):
        assert conn.commit.call_count==1
        return 'sent',456
    assert notify(conn,'a_par_10d:123','secret','chat',sender)==1
    selects=[call.args for call in cur.execute.call_args_list if call.args[0].startswith('SELECT run_key')]
    assert selects[0][1]==('a_par_10d:123',)
    update=[call.args for call in cur.execute.call_args_list if call.args[0].startswith('UPDATE')][-1]
    assert update[1][:2]==('sent',456)


@pytest.mark.parametrize('previous', ['claimed','uncertain'])
def test_uncertain_delivery_is_not_sent_twice(previous):
    conn,_=db(previous);sender=MagicMock()
    with pytest.raises(RuntimeError,match='onzeker'):
        notify(conn,'key','token','chat',sender)
    sender.assert_not_called()


def test_sent_is_deduplicated_but_confirmed_rejection_can_retry():
    conn,_=db('sent');sender=MagicMock(return_value=('sent',456))
    assert notify(conn,'key','token','chat',sender)==0
    sender.assert_not_called()
    conn,_=db('failed')
    assert notify(conn,'key','token','chat',sender)==1
    sender.assert_called_once()


def test_unconfirmed_http_result_is_persisted_and_not_reported_as_success():
    conn,cur=db()
    with pytest.raises(RuntimeError,match='niet bevestigd'):
        notify(conn,'key','token','chat',lambda *a: ('uncertain',None))
    update=[call.args for call in cur.execute.call_args_list if call.args[0].startswith('UPDATE')][-1]
    assert update[1][:2]==('uncertain',None)


def test_missing_secrets_do_not_claim_a_message():
    conn=MagicMock()
    with pytest.raises(ValueError,match='ontbreken'):
        notify(conn,'key','','',MagicMock())
    conn.cursor.assert_not_called()


def test_workflow_notifies_even_when_calculation_fails_and_keeps_secrets_separate():
    root=Path(__file__).resolve().parents[1]
    workflow=yaml.load((root/'.github/workflows/backtest_parameter.yml').read_text(),Loader=yaml.BaseLoader)
    steps=workflow['jobs']['backtest']['steps']
    calculation=next(s for s in steps if s.get('id')=='backtest')
    notification=next(s for s in steps if s.get('run')=='python -m analyses.backtest_telegram')
    assert not any(k.startswith('TELEGRAM') for k in calculation['env'])
    assert "steps.backtest.outcome == 'failure'" in notification['if']
    assert notification['env']['TELEGRAM_TOKEN']=='${{ secrets.TELEGRAM_TOKEN }}'
    sql=(root/'maintenance/sql/backtest_notices.sql').read_text()
    assert 'ENABLE ROW LEVEL SECURITY' in sql and 'REVOKE ALL' in sql
    assert 'REFERENCES public.research_backtests' in sql
