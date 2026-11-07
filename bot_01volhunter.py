#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01volhunter.py — HOGE-ATR MEAN REVERSION SCANNER v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Scant de volledige a-lijsten op hoge ATR en IBS/RSI3-instap.
"""

import os
import sys
import math
import time
import warnings
from dataclasses import dataclass
from typing import List, Dict, Optional

import pandas as pd

from common import (
    today_str, safe_float, yahoo_link,
    load_tickers_from_file, bouw_bestandslijst, label_voor,
    compute_rsi_series, compute_atr_series, compute_ibs, wilder_smooth,
    send_telegram, send_email
)

try:
    from db_logger import log_selectie
except Exception as _e:
    print(f"[WARN] db_logger niet beschikbaar ({_e}) — DB-logging wordt overgeslagen")
    def log_selectie(*args, **kwargs):
        return False

warnings.filterwarnings("ignore", category=FutureWarning)

# ============================================================
# CONFIG
# ============================================================

MIN_ATR_PCT     = float(os.getenv("MIN_ATR_PCT", "6.0"))
MAX_ATR_PCT     = float(os.getenv("MAX_ATR_PCT", "60.0"))
IBS_MAX         = float(os.getenv("IBS_MAX", "0.25"))
RSI_MAX         = float(os.getenv("RSI_MAX", "15.0"))
VEREIST_UPTREND = os.getenv("VEREIST_UPTREND", "1") == "1"
TOP_N           = int(os.getenv("TOP_N", "3"))
BATCH_SIZE      = int(os.getenv("BATCH_SIZE", "150"))

RSI_PERIOD = 3
ATR_PERIOD = 14
MA_TREND   = 100

def bouw_bestandslijst_a() -> List[str]:
    return [f"tickers_{n:03d}a.txt" for n in range(41, 60)]

# ============================================================
# INDICATOREN & BULK DOWNLOAD
# ============================================================

def _normalise(df, ticker: str) -> Optional[pd.DataFrame]:
    if df is None or df.empty:
        return None
    df = df.reset_index() if df.index.name in ("Date", "Datetime") else df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if "Close" not in df.columns:
        return None
    df["Ticker"] = ticker
    return df

def download_batch(tickers: List[str], period: str = "8mo") -> Dict[str, pd.DataFrame]:
    import yfinance as yf
    out: Dict[str, pd.DataFrame] = {}
    if not tickers:
        return out
    try:
        data = yf.download(tickers, auto_adjust=True, group_by="ticker", progress=False, threads=True, period=period)
        if data is not None and not data.empty:
            if isinstance(data.columns, pd.MultiIndex):
                for t in tickers:
                    try:
                        norm = _normalise(data.xs(t, axis=1, level=0).copy(), t)
                        if norm is not None and len(norm) >= MA_TREND:
                            out[t] = norm
                    except Exception:
                        pass
            elif len(tickers) == 1:
                norm = _normalise(data, tickers[0])
                if norm is not None and len(norm) >= MA_TREND:
                    out[tickers[0]] = norm
    except Exception as e:
        print(f"  [WARN] batch-download mislukt ({len(tickers)} tickers): {e}")

    ontbrekend = [t for t in tickers if t not in out]
    for t in ontbrekend:
        try:
            import yfinance as yf
            raw = yf.download(t, period=period, auto_adjust=True, progress=False)
            norm = _normalise(raw, t)
            if norm is not None and len(norm) >= MA_TREND:
                out[t] = norm
            time.sleep(0.2)
        except Exception:
            continue
    return out

@dataclass
class VolSignaal:
    ticker:     str
    price:      float
    atr:        float
    atr_pct:    float
    ibs:        float
    rsi3:       float
    ma100:      float
    pct_from_20d_high: float
    stop:       float
    tp:         float
    score:      float

def analyse_ticker_df(ticker: str, df: pd.DataFrame) -> Optional[VolSignaal]:
    try:
        g = df.copy()
        close, high, low = g["Close"], g["High"], g["Low"]

        g["IBS"] = compute_ibs(high, low, close)
        g["RSI3"] = compute_rsi_series(close, RSI_PERIOD)
        g["ATR14"] = compute_atr_series(high, low, close, ATR_PERIOD)
        g["ATR14_PCT"] = g["ATR14"] / close * 100
        g["MA100"] = close.rolling(MA_TREND).mean()
        g["HOOG20"] = high.rolling(20).max()

        row = g.iloc[-1]
        close  = safe_float(row["Close"])
        atr    = safe_float(row["ATR14"])
        atrp   = safe_float(row["ATR14_PCT"])
        ibs    = safe_float(row["IBS"])
        rsi3   = safe_float(row["RSI3"])
        ma100  = safe_float(row["MA100"])
        hoog20 = safe_float(row["HOOG20"])

        if any(math.isnan(x) for x in [close, atr, atrp, ibs, rsi3]) or close <= 0:
            return None

        if not (MIN_ATR_PCT <= atrp <= MAX_ATR_PCT):
            return None

        if ibs > IBS_MAX or rsi3 > RSI_MAX:
            return None
        if VEREIST_UPTREND and not (not math.isnan(ma100) and close > ma100):
            return None

        pct_from_high = (close / hoog20 - 1) * 100 if not math.isnan(hoog20) and hoog20 > 0 else 0.0
        score = atrp * (1 - ibs)

        return VolSignaal(
            ticker=ticker, price=round(close, 2), atr=round(atr, 3),
            atr_pct=round(atrp, 2), ibs=round(ibs, 3), rsi3=round(rsi3, 1),
            ma100=round(ma100, 2) if not math.isnan(ma100) else 0.0,
            pct_from_20d_high=round(pct_from_high, 1),
            stop=round(close - 1.5 * atr, 2), tp=round(close + 2.5 * atr, 2),
            score=round(score, 2),
        )
    except Exception:
        return None

# ============================================================
# OUTPUT & LIVE ENGINE
# ============================================================

def format_bericht(exchange_name: str, signalen: List[VolSignaal], universum_grootte: int, hoge_atr_grootte: int) -> str:
    nu = today_str()
    delen = [
        f"🌊 *VOLATIELE DIP-SCAN — {exchange_name}*",
        f"_{nu} | {universum_grootte} tickers gescand | {hoge_atr_grootte} met ATR%>={MIN_ATR_PCT:.0f} | {len(signalen)} kandidaten_",
        "─────────────────────────────",
    ]
    for i, s in enumerate(signalen, start=1):
        delen.append(
            f"{i}. `{s.ticker}` | {s.price:.2f} | ATR%:{s.atr_pct:.1f} | IBS:{s.ibs:.2f} | RSI3:{s.rsi3:.1f} | vs 20d-high:{s.pct_from_20d_high:+.1f}%\n"
            f"   🎯 richtdoel: {s.tp:.2f} | 🛑 richt-stop: {s.stop:.2f} | [Grafiek]({yahoo_link(s.ticker)})"
        )
    delen.append(f"⚙️ _Hoge-ATR universum: {MIN_ATR_PCT:.0f}-{MAX_ATR_PCT:.0f}% ATR14 | Instap: IBS<={IBS_MAX:.2f} + RSI3<={RSI_MAX:.0f}" + (" + koers>MA100" if VEREIST_UPTREND else "") + "_")
    delen.append("⚠️ _Pure instapsignaal — geen automatische exit-tracking._")
    return "\n\n".join(delen)

def run_live():
    print("=" * 60)
    print(f"VOLATIELE DIP-SCAN (hoge-ATR mean reversion)  {today_str()}")
    print(f"MIN_ATR_PCT={MIN_ATR_PCT} MAX_ATR_PCT={MAX_ATR_PCT} IBS_MAX={IBS_MAX} RSI_MAX={RSI_MAX} VEREIST_UPTREND={VEREIST_UPTREND}")
    print("=" * 60)

    bestanden = bouw_bestandslijst_a()
    email_delen: List[str] = []

    for f_name in bestanden:
        tlist = load_tickers_from_file(f_name)
        if not tlist:
            continue
        ex_name = label_voor(f_name)
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")

        alle_signalen: List[VolSignaal] = []
        hoge_atr_teller = 0

        for i in range(0, len(tlist), BATCH_SIZE):
            batch = tlist[i:i + BATCH_SIZE]
            data = download_batch(batch)
            for ticker, df in data.items():
                sig = analyse_ticker_df(ticker, df)
                if sig is not None:
                    alle_signalen.append(sig)
                    hoge_atr_teller += 1
            print(f"  batch {i // BATCH_SIZE + 1}/{(len(tlist) - 1) // BATCH_SIZE + 1} verwerkt ({len(data)}/{len(batch)} opgehaald)")

        alle_signalen.sort(key=lambda s: s.score, reverse=True)
        top = alle_signalen[:TOP_N]
        print(f"  → {len(top)} van {len(alle_signalen)} kandidaten in hoge-ATR universum")

        for rank, s in enumerate(top, start=1):
            log_selectie(
                ticker=s.ticker, datum=today_str(), strategie="bot_01volhunter", beurs=ex_name, koers=s.price,
                parameters={
                    "score": s.score, "rank": rank, "atr": s.atr, "atr_pct": s.atr_pct,
                    "ibs": s.ibs, "rsi3": s.rsi3, "ma100": s.ma100, "pct_from_high": s.pct_from_20d_high,
                    "stop": s.stop, "tp": s.tp, "grafiek": yahoo_link(s.ticker),
                },
            )

        if top:
            bericht = format_bericht(ex_name, top, len(tlist), hoge_atr_teller)
            send_telegram(bericht, parse_mode="Markdown")
            email_delen.append(bericht)
            print("  → Telegram verstuurd")
        else:
            print(f"  → Geen kandidaten, overgeslagen: {ex_name}")

    if email_delen:
        send_email(f"Volatiele Dip-Scan {today_str()}", "\n\n" + ("=" * 40 + "\n\n").join(email_delen))

    print(f"\n{'=' * 60}\nKlaar.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    run_live()
