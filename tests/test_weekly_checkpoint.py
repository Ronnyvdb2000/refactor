from datetime import date

import pandas as pd
import pytest

from weekly import shared as w
from weekly.checkpoint import KoersCheckpoint
from weekly.download import DownloadOnderbroken


def historie(tickers):
    return pd.DataFrame([
        {'Ticker': t, 'Date': pd.Timestamp(day), 'Close': close}
        for t in tickers for day, close in [('2026-11-06', 100.), ('2026-11-13', 110.)]
    ])


def test_restart_reuses_completed_batches_and_retries_interrupted_batch(monkeypatch, tmp_path):
    monkeypatch.setenv('WEEKLY_CHECKPOINT_DIR', str(tmp_path))
    pauses, calls = [], []
    monkeypatch.setattr(w.time, 'sleep', pauses.append)
    tickers = [f'A{i:03}' for i in range(45)]

    def download(batch, **kwargs):
        calls.append(batch)
        if len(calls) == 2:
            raise DownloadOnderbroken('providerstop')
        return historie(batch)

    monkeypatch.setattr(w, 'download_history', download)
    first = w.WeekKoersen(date(2026, 11, 9), date(2026, 11, 13))
    with pytest.raises(DownloadOnderbroken):
        first.laad(tickers)
    assert len(list(tmp_path.rglob('*.csv'))) == 20
    second = w.WeekKoersen(date(2026, 11, 9), date(2026, 11, 13))
    second.laad(tickers)
    assert calls == [tickers[:20], tickers[20:40], tickers[20:40], tickers[40:]]
    assert len(second.beschikbare_performance(tickers)) == 45
    assert pauses == [2, 2, 2, 2]


def test_missing_or_stale_weekdata_is_retried_on_restart(monkeypatch, tmp_path):
    monkeypatch.setenv('WEEKLY_CHECKPOINT_DIR', str(tmp_path))
    monkeypatch.setattr(w.time, 'sleep', lambda *_: None)
    calls = []

    def download(batch, **kwargs):
        calls.append(batch)
        if len(calls) == 1:
            # A heeft alleen een oude eindkoers; B ontbreekt geheel.
            return historie(['A']).iloc[:1]
        return historie(batch)

    monkeypatch.setattr(w, 'download_history', download)
    w.WeekKoersen(date(2026, 11, 9), date(2026, 11, 13)).laad(['A', 'B'])
    second = w.WeekKoersen(date(2026, 11, 9), date(2026, 11, 13))
    second.laad(['A', 'B'])
    assert calls == [['A', 'B'], ['A', 'B']]
    assert len(second.beschikbare_performance(['A', 'B'])) == 2


def test_checkpoint_rejects_wrong_week_history_ticker_and_broken_csv(monkeypatch, tmp_path):
    monkeypatch.setenv('WEEKLY_CHECKPOINT_DIR', str(tmp_path))
    cache = KoersCheckpoint(date(2026, 10, 26), date(2026, 11, 13))
    frame = historie(['A']).set_index('Date').drop(columns='Ticker')
    cache.bewaar({'A': frame})
    assert set(cache.laad(['A'])) == {'A'}
    assert not KoersCheckpoint(date(2026, 11, 2), date(2026, 11, 20)).laad(['A'])
    assert not KoersCheckpoint(date(2025, 10, 5), date(2026, 11, 13)).laad(['A'])
    raw = pd.read_csv(cache.bestand('A'))
    raw['Ticker'] = 'B'
    raw.to_csv(cache.bestand('A'), index=False)
    assert not cache.laad(['A'])
    raw['Ticker'] = 'A'
    raw['Date'] = '2026-11-16'
    raw.to_csv(cache.bestand('A'), index=False)
    assert not cache.laad(['A'])
    cache.bestand('A').write_text('broken\n"unterminated')
    assert not cache.laad(['A'])


def test_checkpoints_are_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv('WEEKLY_CHECKPOINT_DIR', raising=False)
    cache = KoersCheckpoint(date(2026, 10, 26), date(2026, 11, 13))
    cache.bewaar({'A': historie(['A']).set_index('Date').drop(columns='Ticker')})
    assert not cache.laad(['A'])
    assert cache.pad is None
