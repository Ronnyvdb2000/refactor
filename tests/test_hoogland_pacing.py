from unittest.mock import patch
import logging
import pytest
from strats import bot_01hoogl as bot


def test_retry_recovers_same_ticker_and_fx_cache():
    cache = {}
    with patch.object(bot, 'analyse_ticker', side_effect=[(None, True), ('ok', False)]) as analyse, patch.object(bot.time, 'sleep') as sleep:
        assert bot.analyse_met_herpoging('A', bot.MODUS_CFG['full'], cache) == ('ok', False)
    assert analyse.call_count == 2
    assert all(c.kwargs['fx_cache'] is cache for c in analyse.call_args_list)
    assert sum(c.args[0] for c in sleep.call_args_list) == 60
    assert all(c.args[0] <= 30 for c in sleep.call_args_list)


def test_logged_limit_cannot_be_reported_as_success():
    def provider(*args, **kwargs):
        logging.getLogger('yfinance').error('HTTP Error 429: Too Many Requests')
        return object(), False
    with patch.object(bot, 'analyse_ticker', side_effect=provider) as analyse, patch.object(bot.time, 'sleep'):
        assert bot.analyse_met_herpoging('A', bot.MODUS_CFG['full'], {}) == (None, True)
    assert analyse.call_count == 2


def test_unexpected_error_is_not_retried():
    with patch.object(bot, 'analyse_ticker', side_effect=ValueError('bug')) as analyse, patch.object(bot.time, 'sleep') as sleep:
        with pytest.raises(ValueError, match='bug'):
            bot.analyse_met_herpoging('A', bot.MODUS_CFG['live'], {})
    analyse.assert_called_once()
    sleep.assert_not_called()
