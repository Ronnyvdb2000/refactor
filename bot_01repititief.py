#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01repititief.py — SEIZOENSEFFECTEN ENGINE v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Combineert weekdag-, maand- en kwartaaleffecten met BH-correctie.
"""

import sys
import math
import time
import warnings
import datetime as dt
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats

from common import (
    today_str, safe_float, yahoo_link,
    download_history, load_tickers_from_file, bouw_bestandslijst, label_voor,
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

START_CAPITAL    = 50_000.0
SLIPPAGE_PCT     = 0.001
TRADE_COST_FIXED = 15.0
TRADE_COST_PCT   = 0.0035
TAX_RATE         = 0.10

TELEGRAM_THROTTLE_SEC = 1.2
TELEGRAM_MAX_POGINGEN = 4

SZ_CFG = {
    "lookback_years": 15,
    "min_jaren":      5,
    "fdr_alpha":      0.05,
    "top_n":          8,
}

BUCKET_TYPES = ("weekdag", "maand", "kwartaal")
WEEKDAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag"]
MAANDEN   = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]

BACKTEST_START      = "2021-01-01"
BACKTEST_END        = today_str()
BACKTEST_MIN_TRAIN  = 5

# ============================================================
# BENJAMINI-HOCHBERG FDR-CORRECTIE
# ============================================================

def bh_correctie(p_waarden: List[float], alpha: float) -> List[bool]:
    m = len(p_waarden)
    if m == 0:
        return []
    volgorde = sorted(range(m), key=lambda i: p_waarden[i])
    laatste_significante_rank = -1
    for rang, idx in enumerate(volgorde, start=1):
        if p_waarden[idx] <= (rang / m) * alpha:
            laatste_significante_rank = rang
    mask = [False] * m
    if laatste_significante_rank >= 0:
        for rang, idx in enumerate(volgorde, start=1):
            if rang <= laatste_significante_rank:
                mask[idx] = True
    return mask

# ============================================================
# SEIZOENSANALYSE (live)
# ============================================================

@dataclass
class SeizoenKandidaat:
    ticker:     str
    price:      float
    weekdag:    Optional[dict]
    maand:      Optional[dict]
    kwartaal:   Optional[dict]
    jaren_data: float

@dataclass
class SeizoenSignaal:
    ticker:               str
    price:                float
    score:                float
    weekdag:              Optional[dict]
    maand:                Optional[dict]
    kwartaal:             Optional[dict]
    significante_buckets: Set[str]
    jaren_data:           float
    checks:               Dict[str, dict] = field(default_factory=dict)

def bereken_bucket_stats(rendementen: np.ndarray) -> Optional[dict]:
    n = len(rendementen)
    if n < 5:
        return None
    gemiddelde = float(np.mean(rendementen))
    win_rate   = float(np.mean(rendementen > 0))
    _, p_waarde = stats.ttest_1samp(rendementen, 0.0)
    return {"gemiddelde": gemiddelde, "win_rate": win_rate, "p_waarde": float(p_waarde), "n": n}

def analyseer_ticker(ticker: str, df_ticker: pd.DataFrame) -> Optional[SeizoenKandidaat]:
    try:
        g = df_ticker.sort_values("Date").copy()
        if len(g) < 250 * SZ_CFG["min_jaren"]:
            return None

        g = g.set_index("Date")
        rendement = g["Close"].pct_change().dropna()
        aantal_jaren = (rendement.index[-1] - rendement.index[0]).days / 365.25
        if aantal_jaren < SZ_CFG["min_jaren"]:
            return None

        current_price = safe_float(g["Close"].iloc[-1])
        if current_price <= 0 or math.isnan(current_price):
            return None

        vandaag = dt.datetime.now(dt.timezone.utc)
        huidige_weekdag = vandaag.weekday()
        huidige_maand   = vandaag.month
        huidig_kwartaal = (vandaag.month - 1) // 3 + 1

        weekdag_stats  = bereken_bucket_stats(rendement[rendement.index.weekday == huidige_weekdag].values)
        maand_stats    = bereken_bucket_stats(rendement[rendement.index.month == huidige_maand].values)
        kwartaal_stats = bereken_bucket_stats(rendement[rendement.index.quarter == huidig_kwartaal].values)

        if not (weekdag_stats or maand_stats or kwartaal_stats):
            return None

        return SeizoenKandidaat(
            ticker=ticker, price=round(current_price, 2),
            weekdag=weekdag_stats, maand=maand_stats, kwartaal=kwartaal_stats,
            jaren_data=round(aantal_jaren, 1),
        )
    except Exception as e:
        print(f"[WARN] {ticker}: fout — {e}")
        return None

def pas_bh_toe_op_beurs(kandidaten: List[SeizoenKandidaat], alpha: float) -> List[SeizoenSignaal]:
    significant_per_kandidaat: Dict[int, Set[str]] = {}

    for bucket_key in BUCKET_TYPES:
        indices = [i for i, k in enumerate(kandidaten) if getattr(k, bucket_key) is not None]
        if not indices:
            continue
        p_waarden = [getattr(kandidaten[i], bucket_key)["p_waarde"] for i in indices]
        mask = bh_correctie(p_waarden, alpha)
        for pos, kandidaat_idx in enumerate(indices):
            if mask[pos]:
                significant_per_kandidaat.setdefault(kandidaat_idx, set()).add(bucket_key)

    resultaten: List[SeizoenSignaal] = []
    for i, k in enumerate(kandidaten):
        sig_keys = significant_per_kandidaat.get(i)
        if not sig_keys:
            continue
        score = sum(
            getattr(k, key)["gemiddelde"] * (1 - getattr(k, key)["p_waarde"])
            for key in sig_keys
        )
        resultaten.append(SeizoenSignaal(
            ticker=k.ticker, price=k.price, score=score,
            weekdag=k.weekdag, maand=k.maand, kwartaal=k.kwartaal,
            significante_buckets=sig_keys, jaren_data=k.jaren_data,
        ))
    return resultaten

# ============================================================
# OUTPUT
# ============================================================

def _bucket_regel(key: str, stat: Optional[dict], significante_buckets: Set[str], checks: Optional[Dict[str, dict]] = None) -> Optional[str]:
    if key not in significante_buckets or not stat:
        return None
    vandaag = dt.datetime.now(dt.timezone.utc)
    if key == "weekdag":
        kort, label = "wk", WEEKDAGEN[vandaag.weekday()]
    elif key == "maand":
        kort, label = "mnd", MAANDEN[vandaag.month - 1]
    else:
        kort, label = "kw", f"Q{(vandaag.month - 1)//3 + 1}"
    regel = (f"{kort} {label}: {stat['gemiddelde']*100:+.2f}% "
             f"(winrate {stat['win_rate']*100:.0f}%, n={stat['n']}, p={stat['p_waarde']:.3f})")
    check = (checks or {}).get(key)
    if check:
        regel += (f" — check: {check['jaren_winst']}/{check['jaren_getest']}j positief, "
                  f"{check['n_trades']}x, PF {check['pf']:.2f}")
    else:
        regel += " — check: onvoldoende historische trades"
    return regel

def sig_regel(s: SeizoenSignaal) -> str:
    onderbouwing = [r for r in (
        _bucket_regel("weekdag", s.weekdag, s.significante_buckets, s.checks),
        _bucket_regel("maand", s.maand, s.significante_buckets, s.checks),
        _bucket_regel("kwartaal", s.kwartaal, s.significante_buckets, s.checks),
    ) if r]
    return (
        f"• `{s.ticker}` score {s.score*100:+.2f}% | EUR{s.price:.2f} | "
        f"{s.jaren_data:.0f}j data | {yahoo_link(s.ticker)}\n"
        f"  {' | '.join(onderbouwing)}"
    )

def format_bericht(exchange_name: str, top: List[SeizoenSignaal], alle: List[SeizoenKandidaat]) -> Optional[str]:
    if not alle:
        return None
    nu = today_str()
    delen = [
        f"📅 *SEIZOENSEFFECTEN — {exchange_name}*",
        f"_{nu} | {len(alle)} geanalyseerd | {len(top)} met FDR-significant seizoenspatroon_",
        "─────────────────────────────",
        "\n\n".join(sig_regel(s) for s in top),
        "─────────────────────────────",
        f"⚙️ _Weekdag/maand/kwartaal t.o.v. eigen historiek (max {SZ_CFG['lookback_years']}j) | "
        f"Benjamini-Hochberg FDR={SZ_CFG['fdr_alpha']:.2f} per buckettype_",
    ]
    return "\n\n".join(delen)

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    print(f"{'='*60}\nSEIZOENSEFFECTEN — LIVE  {today_str()}\n{'='*60}")

    exchange_tickers: Dict[str, List[str]] = {}
    all_tickers: List[str] = []

    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist:
            continue
        ex_name = label_voor(f_name)
        exchange_tickers[ex_name] = tlist
        all_tickers.extend(tlist)
        print(f"  {ex_name}: {len(tlist)} tickers")

    all_tickers = sorted(set(all_tickers))
    if not all_tickers:
        print("[ERROR] Geen ticker bestanden gevonden.")
        return

    print(f"\nTotaal: {len(all_tickers)} unieke tickers")
    print(f"Data downloaden ({SZ_CFG['lookback_years']} jaar)...")
    df = download_history(all_tickers, period=f"{SZ_CFG['lookback_years']}y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    print(f"Data geladen: {df['Ticker'].nunique()} tickers, {len(df)} rijen")
    email_delen: List[str] = []

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")
        df_ex = df[df["Ticker"].isin(tlist)].copy()

        kandidaten: List[SeizoenKandidaat] = []
        for ticker, group in df_ex.groupby("Ticker", sort=False):
            k = analyseer_ticker(ticker, group)
            if k is not None:
                kandidaten.append(k)

        if not kandidaten:
            print(f"  → Overgeslagen: {ex_name} (te weinig data)")
            continue

        top_alles = pas_bh_toe_op_beurs(kandidaten, SZ_CFG["fdr_alpha"])
        print(f"  {len(kandidaten)} geanalyseerd, {len(top_alles)} FDR-significant")

        if not top_alles:
            print(f"  → Overgeslagen: {ex_name} (niets overleeft de FDR-correctie)")
            continue

        top_alles.sort(key=lambda s: s.score, reverse=True)
        top = top_alles[:SZ_CFG["top_n"]]

        for s in top:
            log_selectie(
                ticker=s.ticker, datum=today_str(), strategie="bot_01repititief", beurs=ex_name, koers=s.price,
                parameters={
                    "score": s.score, "grafiek": yahoo_link(s.ticker),
                    "jaren_data": s.jaren_data, "significante_buckets": sorted(s.significante_buckets),
                },
            )

        bericht = format_bericht(ex_name, top, kandidaten)
        if bericht:
            send_telegram(bericht, parse_mode="Markdown")
            email_delen.append(bericht)
            print(f"  → Telegram verstuurd")
            time.sleep(TELEGRAM_THROTTLE_SEC)

    if email_delen:
        send_email(f"Seizoenseffecten rapport {today_str()}", "\n\n" + ("=" * 40 + "\n\n").join(email_delen))

    print(f"\n{'='*60}\nKlaar.")

def run_backtest():
    print("Backtest-functionaliteit is niet aanwezig in de originele bot_01repititief.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
