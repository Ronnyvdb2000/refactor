"""
Canonieke technische indicatoren.

ALLE bots gebruiken deze functies. Geen eigen implementaties meer in
bot_*.py — die gaven onderling verschillende waarden (Wilder-loop vs
ewm(alpha=1/14), met/zonder min_periods), wat features onvergelijkbaar
maakte tussen training (bouw_generieke_technicals) en live scoring.

Wilder-smoothing volgt TA-Lib: seed = simpel gemiddelde van de eerste
`period` geldige waarden, daarna recursief.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


# ---------------------------------------------------------------
# Basis: Wilder-smoothing
# ---------------------------------------------------------------
def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder-smoothing (TA-Lib-conform). Seed = gemiddelde van eerste
    `period` geldige waarden, daarna recursief."""
    result = pd.Series(index=series.index, dtype=float)
    valid = series.dropna()
    if len(valid) < period:
        return result
    result[valid.index[period - 1]] = valid.iloc[:period].mean()
    for i in range(period, len(valid)):
        result[valid.index[i]] = (
            result[valid.index[i - 1]] * (period - 1) / period
            + valid.iloc[i] / period
        )
    return result


# ---------------------------------------------------------------
# RSI / ATR / ADX
# ---------------------------------------------------------------
def compute_rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    rs = wilder_smooth(gain, period) / (wilder_smooth(loss, period) + 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


def compute_atr_series(high: pd.Series, low: pd.Series,
                       close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return wilder_smooth(tr, period)


def compute_adx_series(high: pd.Series, low: pd.Series,
                       close: pd.Series, period: int = 14) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = wilder_smooth(tr, period)
    plus_di = 100 * wilder_smooth(plus_dm, period) / (atr + 1e-9)
    minus_di = 100 * wilder_smooth(minus_dm, period) / (atr + 1e-9)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)) * 100
    return wilder_smooth(dx, period)


# ---------------------------------------------------------------
# MACD
# ---------------------------------------------------------------
def compute_macd_series(close: pd.Series, fast: int = 12,
                        slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


# ---------------------------------------------------------------
# Bollinger Bands, Stochastics, IBS
# ---------------------------------------------------------------
def compute_bb(close: pd.Series, period: int = 20, std_mult: float = 2.0):
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = ma + std_mult * std
    lower = ma - std_mult * std
    breedte = (upper - lower) / ma * 100
    percent_b = (close - lower) / (upper - lower).replace(0, np.nan)
    return breedte, percent_b


def compute_stoch(high: pd.Series, low: pd.Series, close: pd.Series,
                  k_period: int = 14, k_smooth: int = 3, d_smooth: int = 3):
    low_n = low.rolling(k_period).min()
    high_n = high.rolling(k_period).max()
    k_raw = 100 * (close - low_n) / (high_n - low_n).replace(0, np.nan)
    k = k_raw.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()
    return k, d


def compute_ibs(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Internal Bar Strength: waar sloot de koers binnen de dagrange."""
    return (close - low) / (high - low).replace(0, np.nan)


# ---------------------------------------------------------------
# Volatiliteit
# ---------------------------------------------------------------
def compute_hv(close: pd.Series, period: int) -> pd.Series:
    """Historische volatiliteit, geannualiseerd (%)."""
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(period).std() * np.sqrt(252) * 100


def compute_ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


# ---------------------------------------------------------------
# Hoog-niveau: alles in één keer (voor bouw_generieke_technicals)
# ---------------------------------------------------------------
def bereken_alle_indicatoren(hist: pd.DataFrame,
                             index_ret: pd.Series | None = None) -> pd.DataFrame:
    """Berekent de volledige featureset voor één ticker. Output-index =
    dezelfde als `hist`. Signature compatibel met de bestaande
    bouw_generieke_technicals.bereken_indicatoren()."""
    if hist is None or hist.empty:
        return pd.DataFrame()
    df = hist.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
        df = df.loc[:, ~df.columns.duplicated()]
    if "Close" not in df.columns:
        return pd.DataFrame()

    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df.sort_index()

    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "High" not in df.columns:
        df["High"] = df["Close"]
    if "Low" not in df.columns:
        df["Low"] = df["Close"]
    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]
    out = pd.DataFrame(index=df.index)

    atr = compute_atr_series(high, low, close, 14)
    out["ATR14"] = atr
    out["ATR14_PCT"] = atr / close * 100

    out["RSI14"] = compute_rsi_series(close, 14)
    out["IBS"] = compute_ibs(high, low, close)

    out["MA50"] = close.rolling(50).mean()
    out["MA200"] = close.rolling(200).mean()
    out["PCT_FROM_MA50"] = (close - out["MA50"]) / out["MA50"] * 100
    out["PCT_FROM_MA200"] = (close - out["MA200"]) / out["MA200"] * 100

    out["VOL_RATIO_20D"] = volume / volume.rolling(20).mean()
    high52w = high.rolling(252).max()
    out["HIGH52W"] = high52w
    out["PCT_FROM_HIGH52W"] = (close - high52w) / high52w * 100

    macd, sig, hist_m = compute_macd_series(close)
    out["MACD"] = macd
    out["MACD_SIGNAAL"] = sig
    out["MACD_HIST"] = hist_m

    bb_breedte, bb_pct_b = compute_bb(close)
    out["BB_BREEDTE"] = bb_breedte
    out["BB_PERCENT_B"] = bb_pct_b

    k, d = compute_stoch(high, low, close)
    out["STOCH_K"] = k
    out["STOCH_D"] = d

    out["ADX14"] = compute_adx_series(high, low, close, 14)

    if index_ret is not None:
        idx20 = index_ret.reindex(df.index).pct_change(20).fillna(0)
        out["REL_STERKTE_20D"] = (close.pct_change(20) - idx20) * 100
    else:
        out["REL_STERKTE_20D"] = np.nan

    out["HV20"] = compute_hv(close, 20)
    out["HV60"] = compute_hv(close, 60)

    low52w = low.rolling(252).min()
    dagen = pd.Series(index=df.index, dtype=float)
    for i in range(len(df)):
        if i < 252:
            continue
        venster = low.iloc[i - 251:i + 1]
        idx_low = venster.idxmin()
        dagen.iloc[i] = (df.index[i] - idx_low).days
    out["DAGEN_SINDS_LOW52W"] = dagen

    out["VOL_RATIO_50D"] = volume / volume.rolling(50).mean()

    ema8 = compute_ema(close, 8)
    ema20 = compute_ema(close, 20)
    out["EMA8"] = ema8
    out["EMA20"] = ema20
    out["PCT_FROM_EMA8"] = (close - ema8) / ema8 * 100
    out["PCT_FROM_EMA20"] = (close - ema20) / ema20 * 100
    out["EMA8_MINUS_EMA20"] = ema8 - ema20

    out["Open"] = df.get("Open", df["Close"])
    out["High"] = high
    out["Low"] = low
    out["Close"] = close
    out["Volume"] = volume
    return out
