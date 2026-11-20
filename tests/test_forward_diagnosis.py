"""Blocked source identities are reported, never silently relabelled or shifted."""
import csv
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from scripts import bouw_forward_returns as b
def selection(ticker='A', day='2026-08-10', stored=None):
    return dict(ticker=ticker, datum=day, strategie='test', beurs='market',
                koers=100., opgeslagen_instapkoers=stored)


def history():
    return pd.DataFrame({'Close':100.+np.arange(65), 'Stock Splits':0.},
                        index=pd.bdate_range('2026-08-10', periods=65))


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(b, 'today_str', lambda: '2026-11-19')
    monkeypatch.setattr(b, 'HORIZONS', [5,60])
    monkeypatch.setattr(b, 'SUPABASE_DB_URL', 'offline')
    monkeypatch.setattr(b.psycopg2, 'connect', MagicMock())
    monkeypatch.setattr(b.time, 'sleep', lambda *_: None)
    monkeypatch.setattr(b, 'send_telegram', MagicMock(return_value=True))
    monkeypatch.setattr(b, 'send_email', MagicMock(return_value=True))
    ticker=MagicMock()
    ticker.return_value.history.return_value=history()
    monkeypatch.setattr(b.yf, 'Ticker', ticker)
    return ticker


def diagnosis():
    with open('results/forward_returns_diagnose.csv', newline='') as f:
        return list(csv.DictReader(f))


@pytest.mark.parametrize('stored,blocked',[(None,False),(100.,False),(100.00001,False),(101.,True)])
def test_preflight_uses_existing_upsert_tolerance(stored, blocked):
    assert bool(b.instap_blokkade(selection(stored=stored))) == blocked


def test_conflict_never_downloads_or_writes_and_remains_open(setup, monkeypatch):
    rows=[selection(stored=200.)]
    monkeypatch.setattr(b, 'haal_openstaande_rijen', lambda _: rows)
    write=MagicMock()
    monkeypatch.setattr(b, 'upsert_labels', write)
    b.run_build()
    setup.assert_not_called()
    write.assert_not_called()
    row=diagnosis()[0]
    assert row['status']=='beoordeling_nodig' and row['reden']=='instapkoers_conflict'
    assert row['bronkoers']=='100.0' and row['opgeslagen_instapkoers']=='200.0'
    message=b.send_telegram.call_args.args[0]
    assert '1 blijven nog wachten' in message
    assert '0 openstaande rijen afgerond' in message


def test_conflicted_source_is_not_replaced_but_other_row_can_be_labelled(setup, monkeypatch):
    rows=[selection(stored=200.), selection(ticker='B',stored=100.)]
    monkeypatch.setattr(b, 'haal_openstaande_rijen', MagicMock(side_effect=[rows, rows[:1]]))
    write=MagicMock(return_value=1)
    monkeypatch.setattr(b, 'upsert_labels', write)
    b.run_build()
    setup.assert_called_once_with('B')
    assert write.call_args.args[1][0]['entry_koers']==100.
    assert write.call_args.args[1][0]['fwd_ret_60d']==60.
    report={r['ticker']:r for r in diagnosis()}
    assert report['A']['reden']=='instapkoers_conflict'
    assert report['B']['status']=='berekend'


def test_weekend_date_is_recorded_but_never_shifted(setup, monkeypatch):
    rows=[selection(day='2026-08-09')]
    monkeypatch.setattr(b, 'haal_openstaande_rijen', lambda _: rows)
    monkeypatch.setattr(b, 'upsert_labels', MagicMock(return_value=0))
    b.run_build()
    row=diagnosis()[0]
    assert row['datum']=='2026-08-09' and row['weekenddatum']=='True'
    assert row['reden']=='geen_koers_op_selectiedatum'
    assert b.upsert_labels.call_args.args[1]==[]


def test_weekend_sessions_are_still_eligible_when_provider_has_real_bars(setup, monkeypatch):
    raw=pd.DataFrame({'Close':100.+np.arange(65), 'Stock Splits':0.},
                     index=pd.date_range('2026-08-09',periods=65))
    setup.return_value.history.return_value=raw
    rows=[selection(day='2026-08-09')]
    monkeypatch.setattr(b, 'haal_openstaande_rijen', MagicMock(side_effect=[rows,[]]))
    monkeypatch.setattr(b, 'upsert_labels', MagicMock(return_value=1))
    b.run_build()
    assert diagnosis()[0]['status']=='berekend'
    assert b.upsert_labels.call_args.args[1][0]['fwd_ret_5d']==5.


def test_diagnosis_survives_storage_failure(setup, monkeypatch):
    monkeypatch.setattr(b, 'haal_openstaande_rijen', lambda _: [selection()])
    monkeypatch.setattr(b, 'upsert_labels', MagicMock(side_effect=RuntimeError('DB failed')))
    with pytest.raises(RuntimeError,match='DB failed'): b.run_build()
    assert diagnosis()[0]['status']=='berekend'  # Computed is not a storage confirmation.
