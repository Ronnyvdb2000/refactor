
    if index_ret is not None:
        aandeel_ret_20 = close.pct_change(20)
        # Reindex met forward fill voor afwijkende beurskalenders.
        # Niet ontbrekende indexrendementen stilzwijgend als nul behandelen.
        index_close = index_ret.reindex(df.index, method="ffill")
        index_ret_20 = index_close.pct_change(20, fill_method=None)
        out["REL_STERKTE_20D"] = (aandeel_ret_20 - index_ret_20) * 100
    else:
        out["REL_STERKTE_20D"] = None

    log_ret = np.log(close / close.shift(1))
    out["HV20"] = log_ret.rolling(20).std() * np.sqrt(252) * 100
    out["HV60"] = log_ret.rolling(60).std() * np.sqrt(252) * 100

    low52w = low.rolling(252).min()
    dagen_low = []
    for i in range(len(df)):
        if i < 251:
            dagen_low.append(None)
            continue
        venster = low.iloc[i-251:i+1]
        idx_low = venster.idxmin()
        dagen = (df.index[i] - idx_low).days
        dagen_low.append(dagen)
    out["DAGEN_SINDS_LOW52W"] = dagen_low

    vol_avg_50 = volume.rolling(50).mean()
    out["VOL_RATIO_50D"] = volume / vol_avg_50

    try:
        ema8_reeks = close.ewm(span=8, adjust=False).mean()
        ema20_reeks = close.ewm(span=20, adjust=False).mean()

        out["EMA8"] = ema8_reeks
        out["EMA20"] = ema20_reeks
        out["PCT_FROM_EMA8"] = ((close - ema8_reeks) / ema8_reeks) * 100
        out["PCT_FROM_EMA20"] = ((close - ema20_reeks) / ema20_reeks) * 100
        out["EMA8_MINUS_EMA20"] = ema8_reeks - ema20_reeks
    except Exception:
        out["EMA8"] = None
        out["EMA20"] = None
        out["PCT_FROM_EMA8"] = None
        out["PCT_FROM_EMA20"] = None
        out["EMA8_MINUS_EMA20"] = None

    out["Open"] = df["Open"] if "Open" in df.columns else df["Close"]
    out["High"] = df["High"]
    out["Low"] = df["Low"]
    out["Close"] = df["Close"]
    out["Volume"] = df["Volume"]

    return out


# --------------------------------------------------------------------------
# DB-write
# --------------------------------------------------------------------------
INSERT_QUERY = """
INSERT INTO generieke_technicals (
    ticker, datum,
    atr14, atr14_pct, rsi14, ibs,
    ma50, ma200, pct_from_ma50, pct_from_ma200,
    vol_ratio_20d, high52w, pct_from_high52w,
    macd, macd_signaal, macd_hist,
    bb_breedte, bb_percent_b,
    stoch_k, stoch_d, adx14,
    rel_sterkte_20d, hv20, hv60,
    dagen_sinds_low52w, vol_ratio_50d,
    ema8, ema20, pct_from_ema8, pct_from_ema20, ema8_minus_ema20
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (ticker, datum) DO UPDATE SET
    atr14 = EXCLUDED.atr14,
    atr14_pct = EXCLUDED.atr14_pct,
    rsi14 = EXCLUDED.rsi14,
    ibs = EXCLUDED.ibs,
    ma50 = EXCLUDED.ma50,
    ma200 = EXCLUDED.ma200,
    pct_from_ma50 = EXCLUDED.pct_from_ma50,
    pct_from_ma200 = EXCLUDED.pct_from_ma200,
    vol_ratio_20d = EXCLUDED.vol_ratio_20d,
    high52w = EXCLUDED.high52w,
    pct_from_high52w = EXCLUDED.pct_from_high52w,
    macd = EXCLUDED.macd,
    macd_signaal = EXCLUDED.macd_signaal,
    macd_hist = EXCLUDED.macd_hist,
    bb_breedte = EXCLUDED.bb_breedte,
    bb_percent_b = EXCLUDED.bb_percent_b,
    stoch_k = EXCLUDED.stoch_k,
    stoch_d = EXCLUDED.stoch_d,
    adx14 = EXCLUDED.adx14,
    rel_sterkte_20d = EXCLUDED.rel_sterkte_20d,
    hv20 = EXCLUDED.hv20,
    hv60 = EXCLUDED.hv60,
    dagen_sinds_low52w = EXCLUDED.dagen_sinds_low52w,
    vol_ratio_50d = EXCLUDED.vol_ratio_50d,
    ema8 = EXCLUDED.ema8,
    ema20 = EXCLUDED.ema20,
    pct_from_ema8 = EXCLUDED.pct_from_ema8,
    pct_from_ema20 = EXCLUDED.pct_from_ema20,
    ema8_minus_ema20 = EXCLUDED.ema8_minus_ema20;
"""


def schrijf_rij(conn, ticker, datum, rij):
    waarden = [
        ticker, datum,
        _veilig(rij.get("ATR14")),
        _veilig(rij.get("ATR14_PCT")),
        _veilig(rij.get("RSI14")),
        _veilig(rij.get("IBS")),
        _veilig(rij.get("MA50")),
        _veilig(rij.get("MA200")),
        _veilig(rij.get("PCT_FROM_MA50")),
        _veilig(rij.get("PCT_FROM_MA200")),
        _veilig(rij.get("VOL_RATIO_20D")),
        _veilig(rij.get("HIGH52W")),
        _veilig(rij.get("PCT_FROM_HIGH52W")),
        _veilig(rij.get("MACD")),
        _veilig(rij.get("MACD_SIGNAAL")),
        _veilig(rij.get("MACD_HIST")),
        _veilig(rij.get("BB_BREEDTE")),
        _veilig(rij.get("BB_PERCENT_B")),
        _veilig(rij.get("STOCH_K")),
        _veilig(rij.get("STOCH_D")),
        _veilig(rij.get("ADX14")),
        _veilig(rij.get("REL_STERKTE_20D")),
        _veilig(rij.get("HV20")),
        _veilig(rij.get("HV60")),
        _veilig(rij.get("DAGEN_SINDS_LOW52W"), is_int=True),
        _veilig(rij.get("VOL_RATIO_50D")),
        _veilig(rij.get("EMA8")),
        _veilig(rij.get("EMA20")),
        _veilig(rij.get("PCT_FROM_EMA8")),
        _veilig(rij.get("PCT_FROM_EMA20")),
        _veilig(rij.get("EMA8_MINUS_EMA20")),
    ]
    with conn.cursor() as cur:
        cur.execute(INSERT_QUERY, waarden)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    if not DB_URL:
        sys.exit("SUPABASE_DB_URL ontbreekt")

    datum_doel = os.environ.get("BACKFILL_DATUM")
    datum_doel_ts = pd.Timestamp(datum_doel).normalize() if datum_doel else None

    tickers = alle_tickers()
    if not tickers:
        sys.exit("Geen tickers gevonden in tickers_*.txt")

    print(f"Tickers: {len(tickers)}")
    if datum_doel_ts is not None:
