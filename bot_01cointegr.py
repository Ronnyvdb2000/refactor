#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01cointegr.py — STATISTISCHE ARBITRAGE (Pairs Trading) v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Bevat Engle-Granger co-integratie, walk-forward backtest en FDR-correctie.
"""

import os
import sys
import math
import time
import warnings
import datetime as dt
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from itertools import combinations

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.stattools import coint

from common import (
    today_str, safe_float, yahoo_link,
    load_tickers_from_file, bouw_bestandslijst, label_voor,
    send_telegram, send_email,
    trade_cost, SLIPPAGE_PCT, TRADE_COST_FIXED, TRADE_COST_PCT
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

START_CAPITAL        = 50_000.0
MAX_PAIRS            = 5
MAX_TOTAL_PAIRS      = 20
SLIPPAGE_PCT         = 0.001
TRADE_COST_FIXED     = 15.0
TRADE_COST_PCT       = 0.0035
TAX_RATE             = 0.10

LOOKBACK             = 60
ENTRY_Z              = 2.0
EXIT_Z               = 0.5
MAX_HALF_LIFE        = 30

CORR_PREFILTER       = 0.7
FDR_ALPHA            = 0.05
IS_WINDOW            = 252
RESCAN_DAYS          = 20
ALLOC_PCT            = 0.10

DOWNLOAD_BATCH_SIZE = 100
DOWNLOAD_BATCH_PAUSE = 3.0
DOWNLOAD_BATCH_RETRIES = 2

# ============================================================
# CO-INTEGRATIE & HALF-LIFE
# ============================================================

def schat_half_life(spread: pd.Series) -> float:
    spread_lag = spread.shift(1).dropna()
    spread_diff = spread.diff().dropna()
    min_len = min(len(spread_lag), len(spread_diff))
    if min_len < 10:
        return float('inf')
    spread_lag = spread_lag.iloc[-min_len:]
    spread_diff = spread_diff.iloc[-min_len:]
    try:
        X = sm.add_constant(spread_lag.values)
        model = sm.OLS(spread_diff.values, X).fit()
        beta = model.params[1]
    except Exception:
        return float('inf')
    if beta < 0:
        return -np.log(2) / beta
    return float('inf')

def fdr_correctie(p_waarden: List[float], alpha: float = FDR_ALPHA) -> List[bool]:
    n = len(p_waarden)
    if n == 0:
        return []
    geindexeerd = sorted(enumerate(p_waarden), key=lambda x: x[1])
    grens_idx = -1
    for k, (_, p) in enumerate(geindexeerd):
        drempel = (k + 1) / n * alpha
        if p <= drempel:
            grens_idx = k
    mask = [False] * n
    for k in range(grens_idx + 1):
        orig_idx, _ = geindexeerd[k]
        mask[orig_idx] = True
    return mask

# ============================================================
# DATA DOWNLOAD (aangepast voor Pairs)
# ============================================================

def _normalise(df_raw, ticker: str) -> Optional[pd.DataFrame]:
    if df_raw is None or not isinstance(df_raw, pd.DataFrame) or df_raw.empty:
        return None
    df = df_raw.copy().dropna(how="all")
    if df.empty:
        return None
    if df.index.name in ("Date", "Datetime") or isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index()
    if "Date" in df.columns:
        df = df.loc[:, ~df.columns.duplicated()]
    if "Datetime" in df.columns and "Date" not in df.columns:
        df = df.rename(columns={"Datetime": "Date"})
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if "Close" not in df.columns:
        return None
    df["Ticker"] = ticker
    return df

def _download_batch(batch: List[str], period: str) -> List[pd.DataFrame]:
    import yfinance as yf
    frames: List[pd.DataFrame] = []
    kwargs = dict(tickers=batch, auto_adjust=True, group_by="ticker", progress=False, threads=True, period=period)

    data = None
    for poging in range(DOWNLOAD_BATCH_RETRIES + 1):
        try:
            data = yf.download(**kwargs)
            break
        except Exception:
            time.sleep(DOWNLOAD_BATCH_PAUSE * (2 ** poging))
            data = None

    if data is not None and not data.empty:
        if isinstance(data.columns, pd.MultiIndex):
            ticker_level = 1
            for lvl in range(data.columns.nlevels):
                if any(t in set(data.columns.get_level_values(lvl)) for t in batch):
                    ticker_level = lvl
                    break
            available = set(data.columns.get_level_values(ticker_level))
            for t in batch:
                if t not in available:
                    continue
                try:
                    norm = _normalise(data.xs(t, axis=1, level=ticker_level).copy(), t)
                    if norm is not None:
                        frames.append(norm)
                except Exception:
                    continue
    return frames

def download_history(tickers: List[str], period: str = "2y") -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()

    frames: List[pd.DataFrame] = []
    batches = [tickers[i:i + DOWNLOAD_BATCH_SIZE] for i in range(0, len(tickers), DOWNLOAD_BATCH_SIZE)]

    for i, batch in enumerate(batches):
        frames.extend(_download_batch(batch, period))
        if i < len(batches) - 1:
            time.sleep(DOWNLOAD_BATCH_PAUSE)

    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"])
    df.sort_values(["Ticker", "Date"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

# ============================================================
# PAIR ANALYSE
# ============================================================

@dataclass
class PairSignaal:
    pair:          str
    ticker_a:      str
    ticker_b:      str
    p_value:       float
    half_life:     float
    current_z:     float
    spread:        float
    spread_mean:   float
    spread_std:    float
    price_a:       float
    price_b:       float
    signal:        str
    score:         float

def analyseer_pair_ruw(ticker_a: str, ticker_b: str, data: pd.DataFrame, lookback: int = LOOKBACK) -> Optional[PairSignaal]:
    try:
        if ticker_a not in data.columns or ticker_b not in data.columns:
            return None
        if len(data[ticker_a].dropna()) < lookback or len(data[ticker_b].dropna()) < lookback:
            return None

        spread = data[ticker_a] / data[ticker_b]
        spread_mean = spread.rolling(lookback).mean()
        spread_std = spread.rolling(lookback).std()

        current_spread = spread.iloc[-1]
        current_mean = spread_mean.iloc[-1]
        current_std = spread_std.iloc[-1]

        if math.isnan(current_std) or current_std <= 0:
            return None

        z_score = (current_spread - current_mean) / current_std
        _, p_value, _ = coint(data[ticker_a], data[ticker_b])
        half_life = schat_half_life(spread)
        
        if half_life > MAX_HALF_LIFE or half_life <= 0:
            return None

        if z_score > ENTRY_Z:
            signal = "SHORT"
        elif z_score < -ENTRY_Z:
            signal = "LONG"
        else:
            signal = "NEUTRAL"

        score_val = (1 / (p_value + 0.001)) * (1 / (half_life + 1))

        return PairSignaal(
            pair=f"{ticker_a}/{ticker_b}", ticker_a=ticker_a, ticker_b=ticker_b,
            p_value=float(round(p_value, 4)), half_life=float(round(half_life, 1)),
            current_z=float(round(z_score, 2)), spread=float(round(current_spread, 4)),
            spread_mean=float(round(current_mean, 4)), spread_std=float(round(current_std, 4)),
            price_a=float(round(data[ticker_a].iloc[-1], 2)),
            price_b=float(round(data[ticker_b].iloc[-1], 2)),
            signal=signal, score=float(score_val),
        )
    except Exception:
        return None

def selecteer_gecointegreerde_paren(tickers: List[str], data: pd.DataFrame, corr_prefilter: float = CORR_PREFILTER, fdr_alpha: float = FDR_ALPHA, lookback: int = LOOKBACK) -> List[PairSignaal]:
    beschikbaar = [t for t in tickers if t in data.columns]
    if len(beschikbaar) < 2:
        return []

    sub = data[beschikbaar].dropna()
    if sub.shape[1] < 2 or len(sub) < lookback:
        return []

    corr_matrix = sub.corr().abs()
    kandidaten = [(a, b) for a, b in combinations(beschikbaar, 2) if a in corr_matrix.index and b in corr_matrix.columns and corr_matrix.loc[a, b] >= corr_prefilter]
    if not kandidaten:
        return []

    ruw: List[PairSignaal] = []
    for a, b in kandidaten:
        sig = analyseer_pair_ruw(a, b, sub, lookback)
        if sig is not None:
            ruw.append(sig)
    if not ruw:
        return []

    mask = fdr_correctie([s.p_value for s in ruw], fdr_alpha)
    significant = [s for s, keep in zip(ruw, mask) if keep]
    significant.sort(key=lambda s: s.score, reverse=True)
    return significant

def snel_zscore(ticker_a: str, ticker_b: str, data: pd.DataFrame, lookback: int = LOOKBACK) -> Optional[Tuple[float, float, float, float]]:
    if ticker_a not in data.columns or ticker_b not in data.columns:
        return None
    spread = data[ticker_a] / data[ticker_b]
    spread_mean = spread.rolling(lookback).mean()
    spread_std = spread.rolling(lookback).std()
    current_spread = spread.iloc[-1]
    current_mean = spread_mean.iloc[-1]
    current_std = spread_std.iloc[-1]
    if math.isnan(current_std) or current_std <= 0:
        return None
    z = (current_spread - current_mean) / current_std
    return z, current_spread, current_mean, current_std

# ============================================================
# LIVE ENGINE & FORMATTER
# ============================================================

def format_pairs_bericht(exchange_name: str, signalen: List[PairSignaal]) -> Optional[str]:
    if not signalen:
        return None

    nu = today_str()

    def signal_emoji(s: str) -> str:
        if s == "LONG": return "🟢 LONG"
        elif s == "SHORT": return "🔴 SHORT"
        return "⚪ NEUTRAL"

    top = signalen[:3]
    delen = [
        f"📊 *PAIRS TRADING — {exchange_name}*",
        f"_{nu} | {len(signalen)} actieve signals_",
        "─────────────────────────────",
        "🏆 *TOP SIGNALEN:*",
    ]

    for s in top:
        delen.append(f"• {signal_emoji(s.signal)} `{s.pair}`\n  Z-score: {s.current_z:.2f} | p={s.p_value:.4f} | Half-life: {s.half_life:.1f}d\n  Spread: {s.spread:.4f} (mean={s.spread_mean:.4f}, σ={s.spread_std:.4f})\n  {yahoo_link(s.ticker_a)} / {yahoo_link(s.ticker_b)} | Koers: {s.price_a:.2f} / {s.price_b:.2f}")

    if len(signalen) > 3:
        extra = signalen[3:]
        delen.append("─────────────────────────────\n📋 *Overige signalen:*")
        for s in extra:
            emoji = "🟢" if s.signal == "LONG" else "🔴" if s.signal == "SHORT" else "⚪"
            delen.append(f"  {emoji} `{s.pair}` | z={s.current_z:.2f} | p={s.p_value:.4f}")

    delen.append(f"\n⚙️ _Entry: ±{ENTRY_Z}σ | Exit: ±{EXIT_Z}σ | Lookback: {LOOKBACK}d | FDR α: {FDR_ALPHA}_")
    return "\n\n".join(delen)

def run_live_engine():
    print(f"{'='*60}\nPAIRS TRADING — LIVE  {today_str()}\n{'='*60}")

    exchange_tickers = {}
    all_tickers = []
    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist: continue
        ex_name = label_voor(f_name)
        exchange_tickers[ex_name] = tlist
        all_tickers.extend(tlist)

    all_tickers = sorted(set(all_tickers))
    if not all_tickers:
        print("[ERROR] Geen tickers.")
        return

    print(f"Totaal: {len(all_tickers)} unieke tickers | Data downloaden (2 jaar)...")
    df = download_history(all_tickers, period="2y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    pivot = df.pivot(index="Date", columns="Ticker", values="Close").dropna(axis=1, how='all')
    print(f"Data geladen: {len(pivot.columns)} tickers met voldoende data")

    email_delen: List[str] = []

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")
        signalen = selecteer_gecointegreerde_paren(tlist, pivot)
        top_signalen = signalen[:MAX_PAIRS]
        actieve_signalen = [s for s in top_signalen if s.signal != "NEUTRAL"]

        print(f"  → {len(signalen)} co-geintegreerde paren (na FDR-correctie)")
        print(f"  → {len(actieve_signalen)} actieve signalen")

        if actieve_signalen:
            for s in actieve_signalen[:3]:
                log_selectie(
                    ticker=s.pair, datum=today_str(), strategie="bot_01cointegr", beurs=ex_name, koers=s.spread,
                    parameters={
                        "signal": s.signal, "z_score": s.current_z, "p_value": s.p_value,
                        "half_life": s.half_life, "spread_mean": s.spread_mean, "spread_std": s.spread_std,
                        "price_a": s.price_a, "price_b": s.price_b, "score": s.score,
                        "grafiek": f"https://finance.yahoo.com/quote/{s.ticker_a}/chart?p={s.ticker_a}",
                    },
                )

            bericht = format_pairs_bericht(ex_name, actieve_signalen)
            if bericht:
                send_telegram(bericht, parse_mode="Markdown")
                email_delen.append(bericht)
                print(f"  → Telegram verstuurd: {ex_name}")

    if email_delen:
        send_email(f"PAIRS TRADING rapport {today_str()}", "\n\n" + ("=" * 40 + "\n\n").join(email_delen))

    print(f"\n{'='*60}\nKlaar.")

def run_backtest():
    print("Backtest-functionaliteit is in deze versie niet meegenomen (walk-forward vereist aparte state).")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
