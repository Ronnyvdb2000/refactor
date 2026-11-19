"""Offline coverage: shorter pacing must not truncate lists or remove cooldowns."""
import contextlib
import io
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
from common import io as shared_io
from strats import bot_01volhunter as vol
from strats import bot_01hoogl as hoogl
from strats import bot_01marktsent as sentiment


def test_download_timeout_also_reaches_individual_fallback():
    with patch.object(shared_io.yf, 'download', return_value=pd.DataFrame()) as download:
        shared_io.download_history(['A'], request_timeout=15)
    assert download.call_count == 2
    assert all(call.kwargs['timeout'] == 15 for call in download.call_args_list)


def test_default_download_options_unchanged():
    with patch.object(shared_io.yf, 'download', return_value=pd.DataFrame()) as download:
        shared_io.download_history(['A'])
    assert all('timeout' not in call.kwargs for call in download.call_args_list)


def test_dipscan_visits_every_batch_and_only_waits_between_batches():
    tickers = [f'T{i:03}' for i in range(121)]
    def fetch(batch, **kwargs):
        return dict.fromkeys(batch, pd.DataFrame())
    with patch.multiple(vol, bouw_bestandslijst_a=MagicMock(return_value=['a']),
        load_tickers_from_file=MagicMock(return_value=tickers), BATCH_SIZE=50, BATCH_PAUSE_SEC=5,
        download_batch=MagicMock(side_effect=fetch), evaluate_history=MagicMock(return_value=(False, None)),
        send_email=MagicMock(return_value=True), send_telegram=MagicMock(return_value=True)), patch.object(vol, 'pause') as pause, \
        contextlib.redirect_stdout(io.StringIO()):
        vol.run_live()
        visited = [t for call in vol.download_batch.call_args_list for t in call.args[0]]
        assert visited == tickers
        assert vol.evaluate_history.call_count == len(tickers)
        assert [call.args[0] for call in pause.call_args_list] == [5, 5]


def test_hoogland_returns_to_base_pacing_after_provider_recovers():
    with patch.multiple(hoogl, RATE_LIMIT_PAUZE_SEC=0,
        bouw_bestandslijst=MagicMock(return_value=['a']),
        load_tickers_from_file=MagicMock(return_value=['A', 'B']),
        analyse_ticker=MagicMock(side_effect=[(None, True), (None, False), (None, False)]),
        send_email=MagicMock(return_value=True)), patch.object(hoogl.time, 'sleep') as sleep, \
        contextlib.redirect_stdout(io.StringIO()):
        # No usable signals is deliberately reported as a failed scan.
        with pytest.raises(RuntimeError):
            hoogl.run_engine('full')
        assert hoogl.analyse_ticker.call_count == 3
        assert sleep.call_args_list[-1].args[0] == hoogl.MODUS_CFG['full']['throttle_sec']
        assert max(call.args[0] for call in sleep.call_args_list) <= hoogl.MODUS_CFG['full']['throttle_sec']


def test_sentiment_batches_cover_all_unique_tickers_despite_download_failure():
    tickers = [f'T{i:03}' for i in range(205)]
    with patch.multiple(sentiment, bouw_bestandslijst=MagicMock(return_value=['x', 'y']),
        load_tickers_from_file=MagicMock(return_value=tickers),
        download_history=MagicMock(side_effect=[pd.DataFrame(), RuntimeError('provider'), pd.DataFrame()]),
        send_email=MagicMock(return_value=True)), contextlib.redirect_stdout(io.StringIO()):
        with pytest.raises(RuntimeError):
            sentiment.run_live_engine()
        calls = sentiment.download_history.call_args_list
        assert [len(call.args[0]) for call in calls] == [100, 100, 5]
        assert [t for call in calls for t in call.args[0]] == tickers
        assert all(call.kwargs['request_timeout'] == 15 for call in calls)
        assert 'batch 2: download mislukt' in sentiment.send_email.call_args.args[1]
