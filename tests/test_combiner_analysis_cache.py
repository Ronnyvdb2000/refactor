"""Overlap must save work without merging exchange-specific statistical tests."""
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from combiners import a_trade_combi as b


def history(ticker):
    return pd.DataFrame(dict(Ticker=ticker, Date=pd.bdate_range(end='2026-11-13', periods=400),
                             Open=20., High=22., Low=18., Close=20., Volume=100.))


def test_seasonal_analysis_is_cached_but_bh_remains_exchange_specific():
    candidates = {ticker: b.repititief.SeizoenKandidaat(
        ticker=ticker, price=20., weekdag=dict(p_waarde=p, gemiddelde=.01),
        maand=None, kwartaal=None, jaren_data=10.) for ticker,p in [('A',.03),('B',.9)]}
    raw = pd.concat([history(t) for t in ['A','B','X']])
    def analyse(ticker, group):
        if ticker == 'X': raise b.repititief.DataUnavailable('short history')
        return candidates[ticker]
    markets = {'small':['A','X'], 'large':['A','B','X'], 'empty':['MISSING']}
    with patch.object(b, 'download_universe', return_value=raw) as download, \
         patch.object(b.repititief, 'analyseer_ticker', side_effect=analyse) as compute, \
         patch.object(b.repititief, 'pas_bh_toe_op_beurs', wraps=b.repititief.pas_bh_toe_op_beurs) as bh:
        result = b.selecties_repititief(markets, ['A','B','X','MISSING'])
    assert result == {'small':{'A'}, 'large':set(), 'empty':set()}
    assert [c.args[0] for c in compute.call_args_list] == ['A','B','X']
    assert [[v.ticker for v in c.args[0]] for c in bh.call_args_list] == [['A'],['A','B']]
    download.assert_called_once()


def test_vcp_success_rejection_and_bad_history_are_not_repeated_per_exchange():
    bad = history('X'); bad.loc[bad.index[-1], 'Close'] = float('nan')
    raw = pd.concat([history('A'),history('B'),bad,history('UNREQUESTED')])
    markets = {'first':['A','B','X'], 'second':['A','B','X'], 'empty':['MISSING']}
    with patch.object(b, 'download_universe') as download, \
         patch.object(b, 'prepare_daily', wraps=b.prepare_daily) as prepare, \
         patch.object(b.vcp, 'analyse_ticker', side_effect=lambda t,g: SimpleNamespace() if t=='A' else None) as compute:
        result = b.selecties_vcp(markets, ['A','B','X','MISSING'], data=raw)
    assert result == {'first':{'A'}, 'second':{'A'}, 'empty':set()}
    assert prepare.call_count == 3
    assert [c.args[0] for c in compute.call_args_list] == ['A','B']
    download.assert_not_called()


@pytest.mark.parametrize('message,match', [
    ('HTTP Error 401: Invalid Crumb', 'Yahoo weigert'),
    ('HTTP Error 429: Too Many Requests', 'Yahoo beperkt')])
def test_fundamental_error_detection_works_with_suppressed_logger(message, match):
    logger = logging.getLogger('yfinance')
    old_level, handlers = logger.level, list(logger.handlers)
    logger.setLevel(logging.CRITICAL)
    def unavailable():
        logger.error(message)
        return None
    try:
        with pytest.raises(b.YahooSourceUnavailable, match=match):
            b.fundamental_call(unavailable)
        assert logger.level == logging.CRITICAL
        assert logger.handlers == handlers
    finally:
        logger.setLevel(old_level)


def test_fundamental_logger_is_restored_after_success_and_unrelated_error():
    logger = logging.getLogger('yfinance')
    old_level, handlers = logger.level, list(logger.handlers)
    logger.setLevel(logging.CRITICAL)
    try:
        assert b.fundamental_call(lambda: 7) == 7
        def broken(): raise ValueError('strategy bug')
        with pytest.raises(ValueError, match='strategy bug'): b.fundamental_call(broken)
        assert logger.level == logging.CRITICAL
        assert logger.handlers == handlers
    finally:
        logger.setLevel(old_level)
