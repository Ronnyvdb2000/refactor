import numpy as np
import pandas as pd
import pytest
from maintainance import backfill_technische_features as b


def history(n=300, constant=False):
    close = np.full(n, 100.) if constant else 100 + np.arange(n) * .1 + np.sin(np.arange(n))
    return pd.DataFrame(dict(Open=close, High=close+1, Low=close-1, Close=close,
                             Volume=np.full(n, 1000.)), index=pd.bdate_range('2025-01-01', periods=n))


def test_single_bar_cannot_seed_backfill():
    g = b.veilige_indicatoren(history(1), None)
    assert g[list(b.KOLOM_NAAR_G.values())].isna().all().all()


def test_real_zero_with_history_is_allowed():
    g = b.veilige_indicatoren(history(constant=True), None)
    assert g.iloc[-1].MACD == 0
    assert g.iloc[-1].EMA8 == g.iloc[-1].EMA20 == 100


def test_future_prices_do_not_change_past():
    h = history()
    short = b.veilige_indicatoren(h.iloc[:260], None)
    full = b.veilige_indicatoren(h, None)
    pd.testing.assert_frame_equal(short, full.loc[short.index])


@pytest.mark.parametrize('kind', ['invalid', 'gap'])
def test_broken_history_restarts_warmup(kind):
    h = history()
    if kind == 'invalid':
        h.iloc[-2, h.columns.get_loc('High')] = -1
    else:
        h.index = list(h.index[:-1]) + [h.index[-1] + pd.Timedelta(days=20)]
    g = b.veilige_indicatoren(h, None)
    assert pd.isna(g.iloc[-1].MACD)
    assert g.iloc[-1]._balken == 1


def test_duplicate_dates_rejected():
    h = history(2)
    h.index = [h.index[0]] * 2
    with pytest.raises(ValueError, match='Dubbele'):
        b.veilige_indicatoren(h, None)


def test_controls_fail_closed():
    row = {'MA50': 100., 'RSI14': 50.}
    assert b.controleer_rij(row, (100., 50.)) == (True, 2, 0)
    assert not b.controleer_rij(row, (110., 50.))[0]
    assert not b.controleer_rij(row, (None, None))[0]
    assert not b.controleer_rij({'MA50': 100}, (100., 50.))[0]
    assert b._sql_datum(pd.Timestamp('2026-09-04')) == '2026-09-04'


def test_mismatch_blocks_before_update(monkeypatch):
    h = history()
    monkeypatch.setattr(b.yf, 'Ticker', lambda _: type('T', (), {'history': lambda self, **kw: h})())
    monkeypatch.setattr(b, 'haal_controlewaarden', lambda *a: {b._sql_datum(h.index[-1]): (1., 50.)})
    monkeypatch.setattr(b, 'update_features', lambda *a: pytest.fail('blocked row reached UPDATE'))
    monkeypatch.setattr(b, 'DRY_RUN', True)
    stat = b.verwerk_ticker(None, 'TEST', [h.index[-1]], None)
    assert stat['n_geblokkeerd'] == 1
    assert stat['n_bijgewerkt'] == 0
