"""Synthetic feature-store coverage regressions; no DB, network or notifications."""
import csv
import logging
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from scripts import bouw_generieke_technicals as b
def history(periods=280, end=None):
    end = end if end is not None else pd.Timestamp.now().normalize() - pd.Timedelta(days=1)
    close = 100 + np.arange(periods) * .01 + np.sin(np.arange(periods))
    return pd.DataFrame(dict(Open=close, High=close+2, Low=close-2,
                             Close=close, Volume=1000.),
                        index=pd.bdate_range(end=end, periods=periods))


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('BACKFILL_DATUM', raising=False)
    monkeypatch.setattr(b, 'DB_URL', 'offline')
    monkeypatch.setattr(b, 'MIN_SUCCES_RATIO', .8)
    monkeypatch.setattr(b, 'alle_tickers', lambda: ['A', 'B'])
    monkeypatch.setattr(b, 'download_index_returns', lambda start: None)
    conn = MagicMock()
    monkeypatch.setattr(b.psycopg2, 'connect', lambda *_: conn)
    write = MagicMock()
    monkeypatch.setattr(b, 'schrijf_rij', write)
    telegram, email = MagicMock(return_value=True), MagicMock(return_value=True)
    monkeypatch.setattr(b, 'send_telegram', telegram)
    monkeypatch.setattr(b, 'send_email', email)
    monkeypatch.setattr(b.time, 'sleep', lambda *_: None)
    ticker = MagicMock()
    monkeypatch.setattr(b.yf, 'Ticker', ticker)
    return conn, write, ticker, telegram, email


def diagnosis():
    with open('results/generieke_technicals_dekking.csv', newline='') as source:
        return list(csv.DictReader(source))


def test_low_coverage_keeps_gate_and_records_exact_missing_ticker(run):
    conn, write, ticker, telegram, email = run
    ticker.return_value.history.side_effect = [history(), pd.DataFrame()]
    with pytest.raises(RuntimeError, match='1/2'):
        b.main()
    rows = diagnosis()
    assert [(r['ticker'], r['reden']) for r in rows] == [('A', 'ok'), ('B', 'geen_historie')]
    write.assert_called_once()
    conn.commit.assert_called_once()
    conn.close.assert_called_once()
    assert '50.0%' in telegram.call_args.args[0]
    assert 'geen historie: 1' in telegram.call_args.args[0]
    email.assert_called_once()
    assert all(c.kwargs['timeout'] == 15 for c in ticker.return_value.history.call_args_list)


@pytest.mark.parametrize('raw,reason', [
    (history(periods=150), 'ontbrekende_features'),
    (history(end=pd.Timestamp.now().normalize()-pd.Timedelta(days=20)), 'verouderde_koers'),
    (pd.DataFrame({'Something': [1]}, index=pd.DatetimeIndex(['2020-01-01'])), 'geen_indicatorhistorie'),
])
def test_skipped_data_has_specific_reason(run, raw, reason):
    _, write, ticker, _, _ = run
    ticker.return_value.history.return_value = raw
    with pytest.raises(RuntimeError): b.main()
    rows = diagnosis()
    assert {r['reden'] for r in rows} == {reason}
    if reason == 'ontbrekende_features':
        assert all('MA200' in r['detail'] and 'HIGH52W' in r['detail'] for r in rows)
    write.assert_not_called()


@pytest.mark.parametrize('partial', [False, True])
def test_logged_throttle_stops_remaining_requests_without_retry(run, partial):
    _, write, ticker, telegram, _ = run
    def limited(**kwargs):
        logging.getLogger('yfinance').error('HTTP Error 429: Too Many Requests')
        return history() if partial else pd.DataFrame()
    ticker.return_value.history.side_effect = limited
    with pytest.raises(RuntimeError, match='Yahoo-rate-limit'): b.main()
    ticker.assert_called_once_with('A')
    write.assert_not_called()
    assert [(r['status'], r['reden']) for r in diagnosis()] == [
        ('afgebroken', 'providerstop'), ('niet_opgevraagd', 'providerstop')]
    assert 'providerstop: 2' in telegram.call_args.args[0]


def test_failed_commit_is_not_counted_and_next_ticker_can_commit(run):
    conn, _, ticker, _, _ = run
    ticker.return_value.history.return_value = history()
    conn.commit.side_effect = [RuntimeError('commit failed'), None]
    with pytest.raises(RuntimeError, match='1/2'): b.main()
    assert [r['status'] for r in diagnosis()] == ['fout', 'geschreven']
    conn.rollback.assert_called_once()


def test_successful_run_records_all_committed_rows(run):
    _, write, ticker, _, _ = run
    raw = history()
    ticker.return_value.history.return_value = raw
    b.main()
    assert all(c.args[2] == raw.index[-1].date() for c in write.call_args_list)
    assert [r['status'] for r in diagnosis()] == ['geschreven', 'geschreven']


def test_index_throttle_is_not_swallowed(monkeypatch):
    ticker = MagicMock()
    ticker.return_value.history.side_effect = b.YahooRateLimited('429')
    monkeypatch.setattr(b.yf, 'Ticker', ticker)
    with pytest.raises(b.YahooRateLimited): b.download_index_returns('2020-01-01')


def test_index_throttle_records_every_ticker_as_unrequested(run, monkeypatch):
    _, write, ticker, telegram, _ = run
    monkeypatch.setattr(b, 'download_index_returns', MagicMock(side_effect=b.YahooRateLimited('429')))
    with pytest.raises(RuntimeError, match='Yahoo-rate-limit'): b.main()
    ticker.assert_not_called()
    write.assert_not_called()
    assert [r['status'] for r in diagnosis()] == ['niet_opgevraagd', 'niet_opgevraagd']
    telegram.assert_called_once()


def test_providerstop_stays_red_even_after_eighty_percent_committed(run, monkeypatch):
    conn, write, ticker, _, _ = run
    monkeypatch.setattr(b, 'alle_tickers', lambda: ['A', 'B', 'C', 'D', 'E'])
    ticker.return_value.history.side_effect = [history() for _ in range(4)] + [b.YahooRateLimited('429')]
    with pytest.raises(RuntimeError, match='Yahoo-rate-limit'): b.main()
    assert conn.commit.call_count == write.call_count == 4
    assert [r['status'] for r in diagnosis()] == ['geschreven']*4 + ['afgebroken']


def broken_history():
    raw = history()
    raw.loc[raw.index[-1], ['Open','High','Low','Close']] = np.nan
    return raw


def test_missing_close_repaired_on_same_day_and_indicators_recomputed(run):
    _, write, ticker, telegram, _ = run
    fixed = history()
    ticker.return_value.history.side_effect = [broken_history(), fixed, broken_history(), fixed]
    b.main()
    assert write.call_count == 2
    assert all(c.args[2] == fixed.index[-1].date() for c in write.call_args_list)
    assert all(pd.notna(c.args[3]['MA200']) for c in write.call_args_list)
    repairs = [c for c in ticker.return_value.history.call_args_list if c.kwargs.get('repair')]
    assert len(repairs) == 2
    assert all(c.kwargs['timeout'] == 15 for c in repairs)
    assert all(r['detail'].startswith('Yahoo-repair') for r in diagnosis())
    assert 'Yahoo-repair opgeslagen:* 2' in telegram.call_args.args[0]


@pytest.mark.parametrize('failure', ['older','invalid','short','incoherent','zero_volume'])
def test_failed_repair_never_substitutes_an_older_bar_or_bypasses_quality_gate(run, failure):
    _, write, ticker, _, _ = run
    fixed = history()
    if failure == 'older': fixed = fixed.iloc[:-1]
    if failure == 'invalid': fixed = broken_history()
    if failure == 'short': fixed = history(periods=100)
    if failure == 'incoherent': fixed.loc[fixed.index[-1], 'Low'] = 999.
    if failure == 'zero_volume': fixed.loc[fixed.index[-1], 'Volume'] = 0.
    ticker.return_value.history.side_effect = [broken_history(), fixed, broken_history(), fixed]
    with pytest.raises(RuntimeError, match='0/2'): b.main()
    write.assert_not_called()
    assert ticker.return_value.history.call_count == 4
    expected = 'ontbrekende_features' if failure == 'short' else 'ongeldige_koers'
    assert all(r['reden'] == expected for r in diagnosis())


def test_rate_limit_during_repair_stops_all_remaining_requests(run):
    _, write, ticker, _, _ = run
    def respond(**kwargs):
        if kwargs.get('repair'):
            logging.getLogger('yfinance').error('HTTP Error 429: Too Many Requests')
            return history()
        return broken_history()
    ticker.return_value.history.side_effect = respond
    with pytest.raises(RuntimeError, match='Yahoo-rate-limit'): b.main()
    assert ticker.return_value.history.call_count == 2
    ticker.assert_called_with('A')
    write.assert_not_called()
    assert [r['status'] for r in diagnosis()] == ['afgebroken','niet_opgevraagd']
