from unittest.mock import patch


def test_beurssign_logged_limit_stops_after_one_retry():
    import logging
    from strats import bot_01beurssign as bot
    import pytest
    def provider(*args):
        logging.getLogger('yfinance').error('HTTP Error 429: Too Many Requests')
        return object()
    with patch.object(bot, 'analyse_ticker', side_effect=provider) as analyse, patch.object(bot, 'pause') as pause:
        with pytest.raises(bot.YahooRateLimited):
            bot.analyse_with_cooldown('A', bot.MODUS_CFG['full'], False)
    assert analyse.call_count == 2
    pause.assert_called_once_with(60)
