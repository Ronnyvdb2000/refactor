#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bouw_generieke_technicals.py — FEATURE STORE v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Berekent de indicatoren via common.indicators en schrijft naar Supabase.
"""

import os
import sys
import math
import time
import datetime as dt
import warnings

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
import yfinance as yf

from common import (
    alle_tickers, bereken_alle_indicatoren,
    download_history, today_str
)

warnings.filterwarnings("ignore")

# ============================================================
# CONFIG
# ============================================================

DB_URL = os.environ.get("SUPABASE_DB_URL")
LOOKBACK_BUFFER_DAGEN = 400
MAX_BAR_AFSTAND_DAGEN = 7
INDEX_TICKER = "^GSPC"

# ============================================================
# DATABASE WRITE
# ============================================================

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

def _veilig(waarde, is_int=False):
    try:
        f = float(waarde)
        if math.isnan(f) or math.isinf(f):
            return None
        return int(round(f)) if is_int else round(f, 4)
    except Exception:
        return None

def schrijf_rij(conn, ticker, datum, rij):
    waarden = [
        ticker, datum,
        _veilig(rij.get("ATR14")), _veilig(rij.get("ATR14_PCT")),
        _veilig(rij.get("RSI14")), _veilig(rij.get("IBS")),
        _veilig(rij.get("MA50")), _veilig(rij.get("MA200")),
        _veilig(rij.get("PCT_FROM_MA50")), _veilig(rij.get("PCT_FROM_MA200")),
        _veilig(rij.get("VOL_RATIO_20D")), _veilig(rij.get("HIGH52W")),
        _veilig(rij.get("PCT_FROM_HIGH52W")), _veilig(rij.get("MACD")),
        _veilig(rij.get("MACD_SIGNAAL")), _veilig(rij.get("MACD_HIST")),
        _veilig(rij.get("BB_BREEDTE")), _veilig(rij.get("BB_PERCENT_B")),
        _veilig(rij.get("STOCH_K")), _veilig(rij.get("STOCH_D")),
        _veilig(rij.get("ADX14")), _veilig(rij.get("REL_STERKTE_20D")),
        _veilig(rij.get("HV20")), _veilig(rij.get("HV60")),
        _veilig(rij.get("DAGEN_SINDS_LOW52W"), is_int=True),
        _veilig(rij.get("VOL_RATIO_50D")), _veilig(rij.get("EMA8")),
        _veilig(rij.get("EMA20")), _veilig(rij.get("PCT_FROM_EMA8")),
        _veilig(rij.get("PCT_FROM_EMA20")), _veilig(rij.get("EMA8_MINUS_EMA20")),
    ]
    with conn.cursor() as cur:
        cur.execute(INSERT_QUERY, waarden)

# ============================================================
# MAIN
# ============================================================

def main():
    if not DB_URL:
        sys.exit("SUPABASE_DB_URL ontbreekt")

    datum_doel = os.environ.get("BACKFILL_DATUM")
    datum_doel_ts = pd.Timestamp(datum_doel).normalize() if datum_doel else None

    tickers = alle_tickers()
    if not tickers:
        sys.exit("Geen tickers gevonden in tickers_*.txt")

    print(f"Tickers: {len(tickers)}")
    print(f"Doeldatum (override): {datum_doel_ts.date()}" if datum_doel_ts is not None else "Doeldatum: (laatste bar per ticker)")

    ref_start_ts = datum_doel_ts if datum_doel_ts is not None else pd.Timestamp.now().normalize()
    start = (ref_start_ts - pd.Timedelta(days=LOOKBACK_BUFFER_DAGEN)).strftime("%Y-%m-%d")
    
    # Referentie-index voor rel_sterkte
    print("Referentie-index ophalen...")
    try:
        idx_hist = yf.Ticker(INDEX_TICKER).history(start=start, auto_adjust=True)
        index_ret = idx_hist["Close"].pct_change() if not idx_hist.empty else None
        if index_ret is not None:
            index_ret.index = pd.to_datetime(index_ret.index).tz_localize(None)
    except Exception as e:
        print(f"[index] download mislukt: {e}")
        index_ret = None

    conn = psycopg2.connect(DB_URL)
    n_ok = 0
    n_skip = 0
    fouten = []

    try:
        for i, ticker in enumerate(tickers, 1):
            if "/" in ticker:
                continue
            try:
                # Download via de centralisatie in common/io.py
                df_hist = download_history([ticker], period="2y")
                if df_hist.empty:
                    n_skip += 1
                    continue

                g = bereken_alle_indicatoren(df_hist, index_ret)
                if g.empty:
                    n_skip += 1
                    continue

                if datum_doel_ts is not None:
                    pos = g.index.searchsorted(datum_doel_ts, side="right") - 1
                else:
                    pos = len(g) - 1

                if pos < 0:
                    n_skip += 1
                    continue

                bar_datum_ts = g.index[pos]
                referentie_ts = datum_doel_ts if datum_doel_ts is not None else pd.Timestamp.now().normalize()
                
                if (referentie_ts - bar_datum_ts).days > MAX_BAR_AFSTAND_DAGEN:
                    n_skip += 1
                    continue

                rij = g.iloc[pos]

                if (pd.isna(rij.get("Close")) or pd.isna(rij.get("High")) or pd.isna(rij.get("Low"))):
                    n_skip += 1
                    continue

                schrijf_rij(conn, ticker, bar_datum_ts.date(), rij)
                n_ok += 1

                if i % 100 == 0:
                    conn.commit()
                    print(f"  [{i}/{len(tickers)}] {n_ok} geschreven")
                time.sleep(0.15)

            except Exception as e:
                fouten.append((ticker, str(e)))

        conn.commit()
    finally:
        conn.close()

    print("\n" + "=" * 60)
    print("SAMENVATTING")
    print("=" * 60)
    print(f"Tickers verwerkt : {len(tickers)}")
    print(f"Rijen geschreven : {n_ok}")
    print(f"Overgeslagen     : {n_skip}")
    if fouten:
        print(f"Fouten           : {len(fouten)}")
        for t, e in fouten[:10]:
            print(f"  {t}: {e}")

if __name__ == "__main__":
    main()
