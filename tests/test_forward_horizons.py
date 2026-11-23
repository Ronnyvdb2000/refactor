"""Offline label maturity and model-horizon regressions; no network or DB writes."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from scripts import bouw_forward_returns as forward
from meta_models import a_meta_model as meta
from meta_models import a_meta_model_score as scorer
from meta_models import a_par_model as par
from common.horizons import calendar_embargo, horizon_days


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(forward, 'today_str', lambda: '2026-11-15')
    monkeypatch.setattr(forward, 'HORIZONS', [5, 60])
    forward.WAARSCHUWINGEN.clear()


def selection(day='2026-08-10'):
    return dict(ticker='A', datum=day, strategie='test', beurs='market', koers=100.)


def history(periods=65, start='2026-08-10'):
    return pd.DataFrame({'Close': np.arange(periods, dtype=float) + 100.,
                         'Stock Splits': 0.}, index=pd.bdate_range(start, periods=periods))


def labels(hist, rows=None):
    with patch.object(forward.yf, 'Ticker') as ticker:
        ticker.return_value.history.return_value = hist
        return forward.bereken_labels_voor_ticker('A', rows or [selection()])


@pytest.mark.parametrize('h,expected', [(5, 9), ('10d', 16), (20, 30), (30, 44), ('60d', 86)])
def test_trading_horizons_have_calendar_embargo(h, expected):
    assert calendar_embargo(h) == expected
    assert calendar_embargo(h, minimum=100) == 100


@pytest.mark.parametrize('h', [0, -1, 7, '60dd', '5;DROP', 2.5, True, ''])
def test_unknown_horizon_is_rejected_before_sql(h):
    with pytest.raises(ValueError):
        horizon_days(h)


def test_60d_label_is_exactly_sixty_completed_sessions_later():
    result = labels(history())[0]
    assert result['fwd_close_60d'] == 160
    assert result['fwd_ret_60d'] == 60
    assert result['fwd_ret_5d'] == 5


def test_60d_label_waits_until_session_exists():
    result = labels(history(periods=60))[0]
    assert result['fwd_ret_60d'] is None
    assert result['fwd_ret_5d'] == 5


def test_only_intraday_or_future_endpoint_is_not_mature(monkeypatch):
    monkeypatch.setattr(forward, 'today_str', lambda: '2026-08-17')
    result = labels(history(periods=6))
    assert result == []  # Five completed later sessions do not yet exist.


def test_nontrading_selection_date_does_not_shift_to_next_session():
    assert labels(history(), [selection('2026-08-09')]) == []
    assert 'geen koers op selectiedatum' in forward.WAARSCHUWINGEN[-1]


def test_sorting_history_does_not_change_horizon():
    assert labels(history().iloc[::-1]) == labels(history())


def test_duplicate_dates_do_not_count_as_extra_sessions():
    raw = history()
    assert labels(pd.concat([raw, raw.iloc[[2]]])) == []


def test_invalid_intermediate_bar_is_not_dropped_to_shift_horizon():
    raw = history()
    raw.iloc[2, 0] = np.nan
    assert labels(raw) == []


def test_long_gap_blocks_long_horizon_but_preserves_short_one():
    raw = history()
    raw.index = raw.index[:10].append(raw.index[10:] + pd.Timedelta(days=20))
    result = labels(raw)[0]
    assert result['fwd_ret_5d'] == 5
    assert result['fwd_ret_60d'] is None


def test_60d_database_eligibility_no_longer_waits_99_days():
    conn = MagicMock()
    with patch.object(forward, 'datetime') as clock:
        clock.now.return_value = pd.Timestamp('2026-11-15', tz='UTC').to_pydatetime()
        forward.haal_openstaande_rijen(conn)
    query, params = conn.cursor.return_value.__enter__.return_value.execute.call_args.args
    assert params['cutoff_60'] == '2026-08-21'
    assert 'f.fwd_ret_60d IS NULL' in query


def test_report_recounts_open_rows_instead_of_counting_partial_updates(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(forward, 'SUPABASE_DB_URL', 'offline')
    row = selection()
    with (
        patch.object(forward.psycopg2, 'connect'),
        patch.object(forward, 'haal_openstaande_rijen', side_effect=[[row], [row]]),
        patch.object(forward, 'bereken_labels_voor_ticker', return_value=[dict(row, fwd_ret_5d=5, fwd_ret_60d=None)]),
        patch.object(forward, 'upsert_labels', return_value=1),
        patch.object(forward, 'send_telegram', return_value=True) as telegram,
        patch.object(forward, 'send_email', return_value=True),
        patch.object(forward.time, 'sleep'),
    ):
        forward.run_build()
    assert '1 blijven nog wachten' in telegram.call_args.args[0]
    assert '0 openstaande rijen afgerond' in telegram.call_args.args[0]


def test_meta_walk_forward_uses_only_mature_training_outcomes(monkeypatch):
    days = pd.bdate_range('2026-01-01', periods=100)
    df = pd.DataFrame([dict(datum=day, x=float(i * 2 + j), target=float(j))
                       for i, day in enumerate(days) for j in range(2)])
    fits = []

    class IdentityScaler:
        def fit_transform(self, x): return x
        def transform(self, x): return x

    class Model:
        coef_ = np.array([1.])
        def __init__(self, **kwargs): pass
        def fit(self, x, y): self.last_train = int(x[:, 0].max()) // 2
        def predict(self, x):
            fits.append((days[self.last_train], days[int(x[:, 0].min()) // 2]))
            return x[:, 0]

    monkeypatch.setattr(meta, 'StandardScaler', IdentityScaler)
    monkeypatch.setattr(meta, 'Ridge', Model)
    result = meta.walk_forward(df, ['x'], min_train_datums=2, min_train_rijen=4, min_test_rijen=2)
    assert not result.empty and fits
    assert all((test - train).days > calendar_embargo(meta.MODEL_HORIZON) for train, test in fits)


def test_parameter_model_does_not_subtract_horizon_twice(monkeypatch):
    args = par.ModelConfig(
        horizons=['60d'], top_n=10, min_ic=.02, rolling_dagen=28, fallback_dagen=90,
        embargo_dagen=25, ic_max_abs=.15, min_datums_ic=2,
        max_data_leeftijd=5, statement_timeout_ms=0, datum='2026-11-13',
        geen_db_write=True, geen_notify=True)
    df = pd.DataFrame(dict(ticker=['A', 'A'], datum=pd.to_datetime(['2026-08-10','2026-11-13']),
                           koers=[100.,110.], fwd_ret_60d=[5.,np.nan]))
    monkeypatch.setattr(par, 'SUPABASE_DB_URL', 'offline')
    with (
        patch.object(par, 'parse_args', return_value=args),
        patch.object(par.psycopg2, 'connect'),
        patch.object(par, 'get_data', return_value=df),
        patch.object(par, 'ic_met_fallback', return_value=(pd.DataFrame(), 'geen', pd.Timestamp('2026-01-01'),pd.Timestamp('2026-08-19'))) as ic,
        patch.object(par, 'schrijf_model_metrics'),
    ):
        par.main()
    assert ic.call_args.args[2] == pd.Timestamp('2026-11-13')
    assert ic.call_args.args[3] == 86


def test_score_loader_only_selects_configured_model_horizon():
    conn = MagicMock()
    scorer.haal_laatste_model(conn)
    query, params = conn.cursor.return_value.__enter__.return_value.execute.call_args.args
    assert 'WHERE horizon_dagen = %s' in query
    assert params == (scorer.MODEL_HORIZON,)


def test_filling_60d_does_not_relabel_existing_shorter_horizons():
    # Execute the generated portable UPSERT in SQLite; PostgreSQL integration
    # regressions additionally run where pgserver is available.
    import re
    import sqlite3

    conn = sqlite3.connect(':memory:')
    conn.create_function('now', 0, lambda: '2026-11-15')
    conn.create_function('GREATEST', -1, max)
    conn.execute("""CREATE TABLE forward_returns (
        ticker text, datum text, strategie text, beurs text, entry_koers real,
        fwd_close_5d real, fwd_ret_5d real, fwd_close_60d real, fwd_ret_60d real,
        bijgewerkt_op text, PRIMARY KEY(ticker,datum,strategie))""")
    query = re.sub(r'%\((\w+)\)s', r':\1', forward.bouw_upsert_query())
    initial = dict(selection(), entry_koers=100., fwd_ret_5d=5., fwd_close_5d=105.,
                   fwd_ret_60d=None, fwd_close_60d=None)
    conn.execute(query, initial)
    conn.execute(query, dict(initial, fwd_ret_5d=999., fwd_close_5d=1099.,
                             fwd_ret_60d=60., fwd_close_60d=160.))
    assert conn.execute('SELECT fwd_ret_5d,fwd_ret_60d FROM forward_returns').fetchone() == (5.,60.)
    # Replaying partial results must neither count a write nor touch its timestamp.
    conn.execute("UPDATE forward_returns SET bijgewerkt_op='original'")
    assert conn.execute(query, initial).rowcount == 0
    assert conn.execute('SELECT bijgewerkt_op FROM forward_returns').fetchone() == ('original',)
    conn.execute(query, dict(initial, entry_koers=200., fwd_ret_60d=999.))
    assert conn.execute('SELECT entry_koers,fwd_ret_5d,fwd_ret_60d FROM forward_returns').fetchone() == (100.,5.,60.)
    conn.close()


@pytest.mark.parametrize('day', [pd.Timestamp('2026-10-30'), np.datetime64('2026-10-30'), '2026-10-30'])
def test_meta_report_formats_iso_dates_and_explains_reference(day, capsys):
    res = pd.DataFrame([dict(datum=day, n_train=200, n_test=27, ic=.1,
        top_decile_ret=2., markt_ret=1., excess_top=1., coefs={'x': .2})])
    text = meta.print_rapport(res, ['x'])
    assert '2026-10-30' in text and '<12' not in text
    assert 'geen beursindex' in text and 'procentpunten' in text
    assert 'minder dan vijf toprijen' in text
    assert ' *' in text and 'Naïeve t-stat' not in text
    assert text in capsys.readouterr().out


def test_meta_report_recent_summary_uses_last_dates_and_fold_equal_weights():
    res = pd.DataFrame([dict(datum=day, n_train=200, n_test=100 if i==0 else 20,
        ic=float(i), top_decile_ret=float(i+1), markt_ret=1., excess_top=float(i),
        coefs={'x': .2}) for i,day in enumerate(pd.bdate_range('2026-10-01', periods=6))])
    core = '\n'.join(meta.rapport_kern(res.iloc[::-1]))
    assert '2026-10-01 t/m 2026-10-08' in core
    assert 'testwaarnemingen: 200' in core
    assert 'Gemiddelde IC: +2.5000' in core
    assert 'Laatste 5 testdatums: IC +3.0000; verschil +3.00 procentpunt' in core
    assert 'iedere testdatum even zwaar' in core
