"""Offline Yahoo workload-budget regressions; no provider calls or job dispatches."""
import logging
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
import yaml

from common import io
from common.yahoo import YahooRateLimited, download_workers
from maintenance.yahoo_lanes import LANES, JOB_LANES, group_for, check_budget

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.delenv('YAHOO_DOWNLOAD_WORKERS', raising=False)
    monkeypatch.setattr(io.time, 'sleep', lambda *_: None)


def frame():
    return pd.DataFrame({'Open':[10.], 'High':[11.], 'Low':[9.], 'Close':[10.], 'Volume':[100.]},
                        index=pd.DatetimeIndex(['2026-11-13'], name='Date'))


def test_default_batch_download_preserves_every_ticker_with_four_workers():
    tickers = [f'T{i:03}' for i in range(205)]
    raw = pd.concat({ticker:frame() for ticker in tickers}, axis=1)
    with patch.object(io.yf, 'download', return_value=raw) as download:
        result = io.download_history(tickers)
    download.assert_called_once()
    assert download.call_args.kwargs['threads'] == 4
    assert set(result.Ticker) == set(tickers)


def test_lower_worker_setting_reaches_download_but_fallback_stays_serial(monkeypatch):
    monkeypatch.setenv('YAHOO_DOWNLOAD_WORKERS', '2')
    with patch.object(io.yf, 'download', side_effect=[pd.DataFrame(), frame()]) as download:
        result = io.download_history(['A'])
    assert download.call_args_list[0].kwargs['threads'] == 2
    assert download.call_args_list[1].kwargs['threads'] is False
    assert list(result.Ticker) == ['A']


@pytest.mark.parametrize('setting', ['0','5','64','-1','nan','true','2.5'])
def test_invalid_worker_count_fails_before_provider_call(monkeypatch, setting):
    monkeypatch.setenv('YAHOO_DOWNLOAD_WORKERS', setting)
    with patch.object(io.yf, 'download') as download, pytest.raises(ValueError, match='1 en 4'):
        io.download_history(['A'])
    download.assert_not_called()


@pytest.mark.parametrize('partial', [True, False])
def test_logged_throttle_does_not_trigger_individual_fallbacks(partial):
    logger = logging.getLogger('yfinance')
    original_handlers, original_level = list(logger.handlers), logger.level
    logger.setLevel(logging.CRITICAL)
    def limited(**kwargs):
        logger.error("HTTP Error 429: Too Many Requests")
        return pd.concat({'A':frame()},axis=1) if partial else pd.DataFrame()
    try:
        with patch.object(io.yf, 'download', side_effect=limited) as download:
            with pytest.raises(YahooRateLimited):
                io.download_history(['A','B','C'])
        download.assert_called_once()
        assert logger.level == logging.CRITICAL
        assert logger.handlers == original_handlers
    finally:
        logger.setLevel(original_level)


def test_throttle_during_individual_recovery_stops_remaining_tickers():
    with patch.object(io.yf, 'download', side_effect=[pd.concat({'A':frame()},axis=1),RuntimeError('429')]) as download:
        with pytest.raises(YahooRateLimited):
            io.download_history(['A','B','C'])
    assert download.call_count == 2
    assert download.call_args_list[1].kwargs['tickers'] == 'B'


def test_missing_symbol_404_can_recover_without_being_called_rate_limited():
    logger = logging.getLogger('yfinance')
    def missing(**kwargs):
        if isinstance(kwargs['tickers'],list):
            logger.error('HTTP Error 404: quote not found for 429.HK')
            return pd.DataFrame()
        return frame()
    with patch.object(io.yf, 'download', side_effect=missing):
        assert set(io.download_history(['429.HK']).Ticker) == {'429.HK'}


def test_budget_uses_four_shared_groups_and_no_branch_bypass():
    assert len(set(LANES.values())) == 4
    assert check_budget(ROOT) == []
    for filename,jobs in JOB_LANES.items():
        data = yaml.load((ROOT/'.github/workflows'/filename).read_text(), Loader=yaml.BaseLoader)
        for job_id,lane in jobs.items():
            guard = data['jobs'][job_id]['concurrency']
            assert guard['group'] == group_for(lane)
            assert 'github.ref' not in guard['group']
            assert data['concurrency']['group'] != guard['group']


def test_long_jobs_cannot_take_daily_or_timed_lane():
    for filename in ['run_01hoogl_full.yml','run_01beurssignfull.yml','run_01volatieledipscan.yml','backfill.yml']:
        assert set(JOB_LANES[filename].values()) == {'full'}
    assert JOB_LANES['run_00mrORB.yml']['bot-00mr-orb'] == 'timed'
    assert set(JOB_LANES['run_00supervar.yml'].values()) == {'timed'}
    assert set(JOB_LANES['run_forward_returns.yml'].values()) == {'features'}


def test_cointegration_backtest_is_routed_away_from_daily_queue():
    expression = group_for('cointegration-mode')
    assert "github.event.inputs.mode == 'backtest'" in expression
    assert "&& 'refactor-yahoo-full' || 'refactor-yahoo-daily'" in expression


def test_weekly_and_other_provider_jobs_are_not_budgeted_here():
    assert not any(name.startswith('weekly_') or name=='analyse_correlaties_weeklytop.yml' for name in JOB_LANES)
    assert 'run_02xgboostV3.yml' not in JOB_LANES   # Training on stored features/labels.


def test_filter_batch_also_uses_worker_limit_and_preserves_tickers(monkeypatch):
    import importlib
    logger = logging.getLogger('yfinance')
    old = logger.level
    try:
        module = importlib.import_module('scripts.bot_041mV2')
        history = pd.concat([frame()] * 60)
        raw = pd.concat({'A':history,'B':history}, axis=1)
        with patch.object(module.yf,'download',return_value=raw) as download:
            result = module.batch_download(['A','B'])
        assert download.call_args.kwargs['threads'] == 4
        assert set(result) == {'A','B'}
    finally:
        logger.setLevel(old)
