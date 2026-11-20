from datetime import date

import pandas as pd
import pytest

from weekly import shared as w
from weekly.download import DownloadOnderbroken


def test_interrupted_report_preserves_completed_data_and_unknown_status(monkeypatch, tmp_path):
    tickers = [f'A{i:03}' for i in range(201)]
    calls, messages = [], []

    def download(batch, **kwargs):
        calls.append(batch)
        if len(calls) == 2:
            raise DownloadOnderbroken('Yahoo blijft beperken')
        return pd.DataFrame({'Ticker': ['A000', 'A000'],
                             'Date': pd.to_datetime(['2026-11-06', '2026-11-13']),
                             'Close': [100., 110.]})

    monkeypatch.setattr(w, 'ROOT', tmp_path)
    monkeypatch.setattr(w, 'weekgrenzen', lambda: (date(2026, 11, 9), date(2026, 11, 13)))
    monkeypatch.setattr(w, 'laad_lijsten', lambda *_: {'tickers_051a.txt': tickers})
    monkeypatch.setattr(w, 'download_history', download)
    monkeypatch.setattr(w.time, 'sleep', lambda *_: None)
    monkeypatch.setattr(w, 'meld', messages.append)
    snapshot = tmp_path / 'weekly/laatste_toppers.json'
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text('oude snapshot')

    with pytest.raises(DownloadOnderbroken):
        w.maak_rapport()

    assert len(calls) == 2  # Diagnose start geen nieuwe downloads.
    assert snapshot.read_text() == 'oude snapshot'
    assert messages and 'ONVOLLEDIG' in messages[0]
    coverage = pd.read_csv(tmp_path / 'weekly_dekking_a.csv').set_index('ticker')
    assert len(coverage) == 201
    assert coverage.loc['A000', 'status'] == 'bruikbaar'
    assert coverage.loc['A001', 'status'] == 'geen_bruikbare_weekdata'
    assert coverage.loc['A020', 'status'] == 'download_afgebroken'
    assert coverage.loc['A200', 'status'] == 'niet_opgevraagd'
    assert coverage.loc[['A020', 'A200'], 'bruikbaar'].isna().all()
    assert coverage.loc['A000', 'bruikbaar'] == True
    assert coverage.loc['A001', 'bruikbaar'] == False


def test_retired_opvolg_cannot_start_another_universe_scan(monkeypatch):
    from weekly import weekly_report_opvolg as o
    monkeypatch.setattr(o.WeekKoersen, 'laad', lambda *_: pytest.fail('Vervallen scan gestart'))
    with pytest.raises(RuntimeError, match='losse opvolgscan is vervallen'):
        o.main()
