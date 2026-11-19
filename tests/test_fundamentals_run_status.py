"""Fundamentals run status and notifications, without network or database writes."""
import logging
from unittest.mock import MagicMock

import pytest
import pandas as pd

import bouw_generieke_fundamentals as b


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(b, 'SUPABASE_DB_URL', 'offline')
    monkeypatch.setattr(b, 'MAX_TICKERS_PER_RUN', 2)
    connection = MagicMock()
    monkeypatch.setattr(b.psycopg2, 'connect', lambda *_: connection)
    monkeypatch.setattr(b, 'haal_openstaande_paren', lambda _: [
        dict(ticker=t, datum='2026-11-18') for t in ['A','B','C']])
    monkeypatch.setattr(b, 'haal_recente_rijen', lambda *_: {})
    store = MagicMock(side_effect=lambda conn, rows: len(rows))
    monkeypatch.setattr(b, 'upsert_rijen', store)
    telegram = MagicMock(return_value=True)
    monkeypatch.setattr(b, 'send_telegram', telegram)
    monkeypatch.setattr(b.time, 'sleep', lambda *_: None)
    fetch = MagicMock(return_value=({'market_cap':100}, 'ok'))
    monkeypatch.setattr(b, 'haal_fundamentals_op', fetch)
    return connection, fetch, store, telegram


def test_planned_run_limit_is_reported_without_false_failure(run):
    connection, fetch, store, telegram = run
    b.run_build()
    assert fetch.call_count == store.call_count == 2
    assert 'runlimiet/providerstop: 1' in telegram.call_args.args[0]
    assert 'ONVOLLEDIG' not in telegram.call_args.args[0]
    connection.close.assert_called_once()


@pytest.mark.parametrize('logged', [False, True])
def test_rate_limit_stops_further_tickers_and_keeps_failure_status(run, logged):
    connection, fetch, store, telegram = run
    def limited(ticker):
        if logged:
            logging.getLogger('yfinance').error('HTTP Error 429: Too Many Requests')
            return {'market_cap':100}, 'ok'
        return None, 'rate_limit'
    fetch.side_effect = limited
    with pytest.raises(b.OnvolledigeRun): b.run_build()
    fetch.assert_called_once_with('A')
    store.assert_not_called()
    assert 'ONVOLLEDIG' in telegram.call_args.args[0]
    assert telegram.call_args.kwargs['parse_mode'] is None
    connection.close.assert_called_once()


@pytest.mark.parametrize('reason', ['geen_data','fout'])
def test_no_usable_results_does_not_succeed(run, reason):
    _, fetch, store, telegram = run
    fetch.return_value = None, reason
    with pytest.raises(b.OnvolledigeRun): b.run_build()
    store.assert_not_called()
    assert 'ONVOLLEDIG' in telegram.call_args.args[0]


def test_partial_success_keeps_committed_rows_but_other_errors_are_red(run):
    _, fetch, store, _ = run
    fetch.side_effect = [({'market_cap':100}, 'ok'), (None, 'fout')]
    with pytest.raises(b.OnvolledigeRun): b.run_build()
    store.assert_called_once()


def test_database_failure_remains_original_error_and_does_not_leak_to_telegram(run):
    connection, _, store, telegram = run
    error = RuntimeError('private-connection-details')
    store.side_effect = error
    with pytest.raises(RuntimeError) as caught: b.run_build()
    assert caught.value is error
    assert 'private-connection-details' not in telegram.call_args.args[0]
    connection.close.assert_called_once()


def test_failed_notification_does_not_hide_scan_failure(run):
    _, fetch, _, telegram = run
    fetch.return_value = None, 'rate_limit'
    telegram.side_effect = RuntimeError('notification failed')
    with pytest.raises(b.OnvolledigeRun): b.run_build()


def test_nothing_to_do_still_sends_telegram(run, monkeypatch):
    _, fetch, store, telegram = run
    monkeypatch.setattr(b, 'haal_openstaande_paren', lambda _: [])
    b.run_build()
    fetch.assert_not_called()
    store.assert_not_called()
    assert 'Niets te doen' in telegram.call_args.args[0]


def test_cache_only_run_does_not_require_new_provider_calls(run, monkeypatch):
    _, fetch, store, telegram = run
    monkeypatch.setattr(b, 'haal_recente_rijen', lambda *_: {t:{'market_cap':100} for t in ['A','B','C']})
    b.run_build()
    fetch.assert_not_called()
    store.assert_not_called()
    assert '3 hergebruikt' in telegram.call_args.args[0]


@pytest.mark.parametrize('source', ['info','cashflow','financials','balance_sheet'])
def test_explicit_rate_limits_are_not_hidden_as_missing_optional_data(monkeypatch, source):
    class Ticker:
        def __getattr__(self, name):
            if name == source: raise RuntimeError('HTTP Error 429: Too Many Requests')
            if name == 'info': return {'marketCap':100}
            return pd.DataFrame()
    monkeypatch.setattr(b.yf, 'Ticker', lambda *_: Ticker())
    try:
        values, reason = b.haal_fundamentals_op('A')
    except b.YahooRateLimited:
        return  # The run wrapper converts this to the same provider-stop reason.
    assert values is None and reason == 'rate_limit'
