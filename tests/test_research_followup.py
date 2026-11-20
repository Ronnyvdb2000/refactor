from copy import deepcopy
from datetime import date, datetime, timezone
import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from research import journal
from research.evaluate import build_report, evaluate_basket, render


def basket():
    return dict(basket_id='basket-1', recorded_at=datetime(2026, 1, 1, 20, tzinfo=timezone.utc),
                payload=dict(run_id='run-1', manifest=dict(currency='USD', signals=[],
                    revision='old', policy=dict(capital=10000, slippage_fraction=0)),
                    simulation=dict(positions=[dict(ticker='A', shares=10), dict(ticker='B', shares=20)])))


def bars(close=110, n=70):
    return pd.DataFrame(dict(Open=[100.] * n, Close=[float(close)] * n),
                        index=pd.bdate_range('2026-01-02', periods=n))


def test_fixed_shares_costs_cash_and_equal_weight_comparator():
    report = build_report([basket()], date(2026, 5, 1),
                          fetch=lambda ticker, *_: bars(110 if ticker == 'A' else 90))
    assert report['baskets'] == 1
    assert len(report['results']) == 3
    for result in report['results']:
        assert result['status'] == 'complete'
        # Actual: 10*10 - 20*10 - 4*15 = -160. Baseline: 15*10 - 15*10 - 60 = -60.
        assert result['net_pnl'] == pytest.approx(-160)
        assert result['net_return_pct'] == pytest.approx(-1.6)
        assert result['baseline_net_return_pct'] == pytest.approx(-.6)
        assert result['excess_pct_points'] == pytest.approx(-1)
        assert result['entry_date'] == '2026-01-02'
    json.dumps(report, allow_nan=False)
    assert 'netto -1.60%' in render(report)


def test_earlier_prices_and_current_day_are_never_counted():
    frame = bars(n=10)
    before = pd.DataFrame(dict(Open=[1.], Close=[1000.]), index=pd.to_datetime(['2025-12-31']))
    frame = pd.concat([before, frame])
    row = basket()
    result = evaluate_basket(row, {'A': frame, 'B': frame}, '10d', date(2026, 1, 15))
    assert result['status'] == 'incomplete'  # 10th close belongs to current day.
    result = evaluate_basket(row, {'A': frame, 'B': frame}, '10d', date(2026, 1, 16))
    assert result['status'] == 'complete'
    assert result['positions'][0]['entry_price'] == 100.


@pytest.mark.parametrize('problem', ['missing', 'provider', 'short', 'duplicate', 'gap'])
def test_missing_candidates_are_not_dropped_or_counted_as_zero(problem):
    data = bars()
    if problem == 'missing': data = pd.DataFrame()
    if problem == 'provider': data = RuntimeError()
    if problem == 'short': data = bars(n=4)
    if problem == 'duplicate': data = pd.concat([data.iloc[:1], data])
    if problem == 'gap': data.index = data.index + pd.Timedelta(days=20)
    result = evaluate_basket(basket(), {'A': bars(), 'B': data}, '10d', date(2026, 5, 1))
    assert result['status'] == 'incomplete'
    assert len(result['positions']) == 2
    assert 'net_return_pct' not in result


def test_entry_gap_can_make_frozen_quantities_unaffordable():
    row = basket()
    row['payload']['manifest']['policy']['capital'] = 2000
    result = evaluate_basket(row, {'A': bars(), 'B': bars()}, '10d', date(2026, 5, 1))
    assert result['status'] == 'entry_budget_exceeded'
    assert 'net_return_pct' not in result


def test_no_provider_request_for_young_basket_and_one_download_per_ticker():
    fetch = MagicMock(return_value=bars())
    report = build_report([basket()], date(2026, 1, 3), fetch=fetch)
    fetch.assert_not_called()
    assert report['states'] == {'incomplete': 3}
    assert report['results'][0]['states'] == {'pending': 2}
    build_report([basket()], date(2026, 5, 1), fetch=fetch)
    assert fetch.call_count == 2  # Not a new request for every horizon.


def test_identity_ignores_repeated_run_and_code_revision_but_keeps_policy():
    first = basket()['payload']
    other = deepcopy(first)
    other['run_id'] = 'new-code-run'
    other['manifest']['revision'] = 'new'
    assert journal.basket_id(first) == journal.basket_id(other)
    other['manifest']['policy']['capital'] = 20000
    assert journal.basket_id(first) != journal.basket_id(other)


def test_archive_conflict_retains_original_timestamp_and_payload():
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    original = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cur.fetchone.side_effect = [None, (original, {'run_id': 'original'})]
    result = journal.record(conn, basket()['payload'])
    assert result['new'] is False
    assert result['recorded_at'] == original.isoformat()
    assert result['original_run_id'] == 'original'
    commands = [call.args[0] for call in cur.execute.call_args_list]
    assert 'ON CONFLICT (basket_id) DO NOTHING' in commands[1]
    assert not any('UPDATE ' in cmd or 'DELETE ' in cmd for cmd in commands)


def test_archive_failure_rolls_back_and_hides_connection_details(monkeypatch):
    import psycopg2
    conn = MagicMock()
    monkeypatch.setenv('SUPABASE_DB_URL', 'secret')
    monkeypatch.setattr(psycopg2, 'connect', lambda *a, **k: conn)
    def fail(*args):
        raise ValueError('secret')
    monkeypatch.setattr(journal, 'record', fail)
    with pytest.raises(RuntimeError) as exc:
        journal.archive(basket()['payload'])
    assert 'secret' not in str(exc.value)
    assert conn.__exit__.call_args.args[0] is ValueError
    conn.close.assert_called_once()


def test_archive_reader_is_readonly_and_handles_missing_table():
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = {'name': None}
    assert journal.load(conn) == []
    conn.set_session.assert_called_once_with(readonly=True, isolation_level='REPEATABLE READ')
    assert all('CREATE ' not in c.args[0] for c in cur.execute.call_args_list)


def test_store_precedes_telegram_and_failure_stops_publication(monkeypatch, tmp_path):
    import research.__main__ as cli
    import research.telegram as telegram
    import sys
    data = dict(currency='USD', data_provenance='test', signals=[])
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(data))
    config = tmp_path / 'config.json'
    config.write_text('{}')
    monkeypatch.setattr(sys, 'argv', ['research', str(source), '--config', str(config), '--archive', '--telegram'])
    sender = MagicMock()
    monkeypatch.setattr(telegram, 'send_text', sender)
    def fail(*args):
        raise RuntimeError('storage failure')
    monkeypatch.setattr(journal, 'archive', fail)
    with pytest.raises(RuntimeError, match='storage failure'):
        cli.main()
    sender.assert_not_called()
