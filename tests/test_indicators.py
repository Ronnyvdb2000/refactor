"""Unit tests voor de canonieke indicatoren."""
import numpy as np
import pandas as pd
import pytest

from common import (
    wilder_smooth, compute_rsi_series, compute_atr_series,
    compute_macd_series, compute_ibs, compute_adx_series,
)

def _serie(*waarden) -> pd.Series:
    return pd.Series(waarden, dtype=float)

def test_wilder_seed_is_simple_average_of_first_n():
    s = _serie(*range(1, 21))
    uit = wilder_smooth(s, 14)
    assert uit.iloc[:13].isna().all()
    assert uit.iloc[13] == pytest.approx(sum(range(1, 15)) / 14)

def test_wilder_recursie_klopt():
    s = _serie(*([10.0] * 14), 20.0, 20.0)
    uit = wilder_smooth(s, 14)
    assert uit.iloc[13] == pytest.approx(10.0)
    assert uit.iloc[14] == pytest.approx((10 * 13 + 20) / 14)

def test_wilder_te_korte_reeks_is_leeg():
    s = _serie(1.0, 2.0, 3.0)
    assert wilder_smooth(s, 14).isna().all()

def test_rsi_constante_stijging_geeft_100():
    s = pd.Series(np.arange(1.0, 40.0))
    rsi = compute_rsi_series(s, 14)
    assert rsi.dropna().iloc[-1] == pytest.approx(100.0, abs=0.01)

def test_rsi_constante_daling_geeft_0():
    s = pd.Series(np.arange(40.0, 1.0, -1.0))
    rsi = compute_rsi_series(s, 14)
    assert rsi.dropna().iloc[-1] == pytest.approx(0.0, abs=0.01)

def test_atr_constante_range():
    n = 40
    close = pd.Series([100.0] * n)
    high = pd.Series([101.0] * n)
    low = pd.Series([99.0] * n)
    atr = compute_atr_series(high, low, close, 14)
    assert atr.dropna().iloc[-1] == pytest.approx(2.0, abs=0.001)

def test_macd_lengtes_en_teken():
    s = pd.Series(np.linspace(100, 200, 200))
    macd, sig, hist = compute_macd_series(s)
    assert len(macd) == len(s)
    assert macd.iloc[-1] > 0

def test_ibs_sluit_op_hoog_geeft_1():
    close = pd.Series([105.0])
    high = pd.Series([105.0])
    low = pd.Series([100.0])
    assert compute_ibs(high, low, close).iloc[0] == pytest.approx(1.0)

def test_ibs_sluit_op_laag_geeft_0():
    close = pd.Series([100.0])
    high = pd.Series([105.0])
    low = pd.Series([100.0])
    assert compute_ibs(high, low, close).iloc[0] == pytest.approx(0.0)

def test_adx_stijgende_trend_geeft_hoog():
    n = 100
    close = pd.Series(np.linspace(100, 150, n))
    high = close + 0.5
    low = close - 0.5
    adx = compute_adx_series(high, low, close, 14)
    assert adx.dropna().iloc[-1] > 40
