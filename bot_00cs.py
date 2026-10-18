#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00cs.py — CAN SLIM ENGINE v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Technische indicatoren via common.indicators, sizing via common.sizing.
Backtest-functionaliteit is volledig behouden.
"""

import sys
import math
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

from common import (
    today_str, safe_float, yahoo_link, esc,
    download_history, load_tickers_from_file, bouw_bestandslijst, label_voor,
    wilder_smooth, compute_atr_series,
    send_telegram, send_email,
    bereken_positie, trade_cost,
    SLIPPAGE_PCT, TRADE_COST_FIXED, TRADE_COST_PCT, RISICO_PCT_DEFAULT
)

try:
    from db_logger import log_selectie
except Exception as _e:
    print(f"[WARN] db_logger niet beschikbaar ({_e}) — DB-logging wordt overgeslagen")
    def log_selectie(*args, **kwargs):
        return False

# ============================================================
# CONFIG
# ============================================================

START_CAPITAL        = 50_000.0
MAX_POSITIONS        = 10
RISICO_PCT_PER_TRADE = 0.05
TAX_RATE             = 0.10
MAX_HOLD_DAYS        = 60

CS_CFG = {
    "eps_quarterly_growth":  25.0,
    "eps_annual_growth":     25.0,
    "eps_annual_years":      3,
    "max_from_high_pct":     15.0,
    "vol_ma_period":         50,
    "vol_up_days":           10,
    "rs_min":                80,
    "ma_trend":              200,
    "stop_pct":              8.0,
    "atr_period":            14,
    "min_score":             4,
}

BACKTEST_START = "2021-01-01"
BACKTEST_END   = today_str()

# ============================================================
# FUNDAMENTELE DATA
# ============================================================

def get_fundamentals(ticker: str) -> Dict:
    result = {
        "eps_q_growth":    float("nan"),
        "eps_annual_ok":   False,
        "eps_annual_cagr": float("nan"),
        "institutional":   False,
        "inst_pct":        0.0,
    }
    try:
        t    = yf.Ticker(ticker)
        info = t.info or {}
        q_growth = info.get("earningsQuarterlyGrowth")
        if q_growth is not None:
            result["eps_q_growth"] = round(float(q_growth) * 100, 1)
        try:
            income = t.financials
            if income is not None and not income.empty:
                if "Net Income" in income.index:
                    net = income.loc["Net Income"].dropna().sort_index()
                    if len(net) >= CS_CFG["eps_annual_years"]:
                        vals = net.values[-CS_CFG["eps_annual_years"]:]
                        if vals[0] > 0 and vals[-1] > 0:
                            cagr = ((vals[-1] / vals[0]) ** (1 / (CS_CFG["eps_annual_years"] - 1)) - 1) * 100
                            result["eps_annual_cagr"] = round(cagr, 1)
                            grew_each_year = all(vals[i] > vals[i - 1] for i in range(1, len(vals)))
                            result["eps_annual_ok"] = (cagr >= CS_CFG["eps_annual_growth"] and grew_each_year)
        except Exception:
            pass
        inst_pct = info.get("heldPercentInstitutions", 0.0) or 0.0
        result["institutional"] = inst_pct > 0.05
        result["inst_pct"] = round(inst_pct * 100, 1)
    except Exception:
        pass
    return result

# ============================================================
# CAN SLIM SIGNAAL
# ============================================================

@dataclass
class CSSignaal:
    ticker:        str
    price:         float
    score:         int
    score_labels:  List[str]
    c_ok:          bool
    a_ok:          bool
    n_ok:          bool
    s_ok:          bool
    l_ok:          bool
    i_ok:          bool
    m_ok:          bool
    eps_q_growth:  float
    eps_cagr:      float
    rs:            float
    pct_from_high: float
    vol_ratio:     float
    inst_pct:      float
    ma200:         float
    stop:          float
    high52w:       float
    total_score:   float

def analyse_ticker(
    ticker:       str,
    g:            pd.DataFrame,
    rs_ratings:   Dict[str, float],
    fundamentals: Dict,
) -> Optional[CSSignaal]:
    try:
        g = g.sort_values("Date").copy()
        if len(g) < CS_CFG["ma_trend"] + 10:
            return None

        close  = g["Close"]
        high   = g["High"]
        volume = g["Volume"]

        current_price = safe_float(close.iloc[-1])
        if current_price <= 0 or math.isnan(current_price):
            return None

        ma200 = safe_float(close.rolling(CS_CFG["ma_trend"]).mean().iloc[-1])
        lb    = min(252, len(close))
        h52w  = float(close.iloc[-lb:].max())
        rs    = rs_ratings.get(ticker, 0.0)
        
        stop = current_price * (1 - CS_CFG["stop_pct"] / 100)
        pct_from_high = ((h52w - current_price) / h52w * 100) if h52w > 0 else 100.0

        recent = g.iloc[-CS_CFG["vol_up_days"]:].copy()
        recent["up"] = recent["Close"] > recent["Close"].shift()
        vol_up   = recent.loc[recent["up"] == True,  "Volume"].mean()
        vol_down = recent.loc[recent["up"] == False, "Volume"].mean()
        vol_ratio = (vol_up / vol_down) if (vol_down > 0 and not math.isnan(vol_down)) else 1.0
        if math.isnan(vol_ratio):
            vol_ratio = 1.0

        score  = 0
        labels = []

        def chk(ok: bool, letter: str, ok_msg: str, fail_msg: str) -> bool:
            nonlocal score
            if ok:
                score += 1
                labels.append(f"✓ {letter}: {ok_msg}")
            else:
                labels.append(f"✗ {letter}: {fail_msg}")
            return ok

        eps_q = fundamentals.get("eps_q_growth", float("nan"))
        c_ok  = not math.isnan(eps_q) and eps_q >= CS_CFG["eps_quarterly_growth"]
        chk(c_ok, "C", f"kwartaal EPS +{eps_q:.1f}% YoY", f"kwartaal EPS {eps_q:.1f}% YoY" if not math.isnan(eps_q) else "geen EPS data")

        a_ok     = fundamentals.get("eps_annual_ok", False)
        eps_cagr = fundamentals.get("eps_annual_cagr", float("nan"))
        chk(a_ok, "A", f"EPS CAGR {eps_cagr:.1f}% over {CS_CFG['eps_annual_years']}j", f"EPS CAGR {eps_cagr:.1f}%" if not math.isnan(eps_cagr) else "geen jaar EPS data")

        n_ok = pct_from_high <= CS_CFG["max_from_high_pct"]
        chk(n_ok, "N", f"{pct_from_high:.1f}% onder 52w high", f"{pct_from_high:.1f}% onder 52w high")

        s_ok = vol_ratio >= 1.2
        chk(s_ok, "S", f"volume up-days {vol_ratio:.1f}x down-days", f"volume up-days {vol_ratio:.1f}x down-days")

        l_ok = rs >= CS_CFG["rs_min"]
        chk(l_ok, "L", f"RS={rs:.0f} (>={CS_CFG['rs_min']})", f"RS={rs:.0f} (<{CS_CFG['rs_min']})")

        i_ok     = fundamentals.get("institutional", False)
        inst_pct = fundamentals.get("inst_pct", 0.0)
        chk(i_ok, "I", f"institutioneel {inst_pct:.1f}%", f"institutioneel {inst_pct:.1f}%")

        m_ok = not math.isnan(ma200) and current_price > ma200
        chk(m_ok, "M", f"Close > MA200 ({ma200:.2f})", f"Close < MA200 ({ma200:.2f})")

        if score < CS_CFG["min_score"]:
            return None

        total_score = score * 10 + (rs * 0.2 if l_ok else 0) + (eps_q * 0.1 if c_ok and not math.isnan(eps_q) else 0) + (10 if n_ok and pct_from_high <= 5 else 0)

        return CSSignaal(
            ticker=ticker, price=round(current_price, 2),
            score=score, score_labels=labels,
            c_ok=c_ok, a_ok=a_ok, n_ok=n_ok, s_ok=s_ok, l_ok=l_ok, i_ok=i_ok, m_ok=m_ok,
            eps_q_growth=round(eps_q, 1) if not math.isnan(eps_q) else 0.0,
            eps_cagr=round(eps_cagr, 1) if not math.isnan(eps_cagr) else 0.0,
            rs=round(rs, 1), pct_from_high=round(pct_from_high, 1),
            vol_ratio=round(vol_ratio, 2), inst_pct=round(inst_pct, 1),
            ma200=round(ma200, 2) if not math.isnan(ma200) else 0.0,
            stop=round(stop, 2), high52w=round(h52w, 2),
            total_score=round(total_score, 1),
        )
    except Exception as e:
        print(f"[WARN] {ticker}: fout — {e}")
        return None

# ============================================================
# OUTPUT
# ============================================================

def _score_bar(score: int, max_score: int = 7) -> str:
    return "█" * score + "░" * (max_score - score) + f" {score}/{max_score}"

def format_bericht(exchange_name: str, signalen: List[CSSignaal], portfolio_waarde: float) -> Optional[str]:
    if not signalen:
        return None

    nu        = today_str()
    max_score = max(s.score for s in signalen)
    toon      = [s for s in signalen if s.score == max_score]
    top2      = signalen[:2]
    lbl       = {7: "⭐ PERFECTE SCORE (7/7)", 6: "🔥 UITSTEKEND (6/7)", 5: "⚡ STERK (5/7)", 4: "📊 WATCHLIST (4/7)"}.get(max_score, "📊")

    def sig_blok(sigs: List[CSSignaal]) -> str:
        lines = []
        for s in sigs:
            entry = s.price * (1 + SLIPPAGE_PCT)
            aandelen, max_loss = bereken_positie(portfolio_waarde, entry, s.stop)
            investering = round(entry * aandelen, 2)
            lines.append(
                f"• `{s.ticker}` {_score_bar(s.score)} | EUR{s.price:.2f} | {yahoo_link(s.ticker)}\n"
                f"  RS:{s.rs:.0f} | EPS +{s.eps_q_growth:.1f}% | Inst:{s.inst_pct:.1f}%\n"
                f"  {s.pct_from_high:.1f}% onder 52w high | Vol:{s.vol_ratio:.1f}x\n"
                f"  Entry: EUR{entry:.2f} | Stop: EUR{s.stop:.2f} | {aandelen} stuks\n"
                f"  Investering: EUR{investering:,.2f} | Max verlies: EUR{max_loss:,.2f}"
            )
        return "\n\n".join(lines)

    delen = [
        f"📈 *CAN SLIM — {esc(exchange_name)}*",
        f"_{nu} | {len(signalen)} kandidaten_",
        "─────────────────────────────",
        f"🏆 *TOP 2:*",
        sig_blok(top2),
        "─────────────────────────────",
        f"*{lbl}:*",
    ]
    
    extra = [s for s in toon if s not in top2]
    if extra:
        for s in extra:
            delen.append(f"• `{s.ticker}` {_score_bar(s.score)} | EUR{s.price:.2f} | RS:{s.rs:.0f}")
    else:
        delen.append("_Zie top 2 hierboven_")

    delen.append(f"⚙️ _Stop {CS_CFG['stop_pct']:.0f}% | RS>={CS_CFG['rs_min']} | Risico 5%_")
    return "\n\n".join(delen)

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    print(f"{'='*60}")
    print(f"CAN SLIM — LIVE  {today_str()}")
    print(f"{'='*60}")

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
    print("Koersdata downloaden (2 jaar)...")
    df = download_history(all_tickers, period="2y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    print(f"Data geladen: {df['Ticker'].nunique()} tickers")
    print("RS ratings berekenen...")
    
    rs_ratings = {}
    perf = {}
    for ticker, group in df.groupby("Ticker", sort=False):
        g = group.sort_values("Date")
        if len(g) < 252:
            continue
        c_now = safe_float(g["Close"].iloc[-1])
        c_3m  = safe_float(g["Close"].iloc[-63])
        c_12m = safe_float(g["Close"].iloc[-252])
        if math.isnan(c_now) or math.isnan(c_12m) or c_12m <= 0 or c_3m <= 0:
            continue
        perf[ticker] = 0.4 * (c_now - c_3m) / c_3m + 0.6 * (c_now - c_12m) / c_12m

    if perf:
        values = sorted(perf.values())
        n = len(values)
        for ticker, p in perf.items():
            rank = sum(1 for v in values if v < p)
            rs_ratings[ticker] = round((rank / n) * 99, 1)

    print(f"RS ratings: {len(rs_ratings)} tickers")
    rs_candidates = [t for t, r in rs_ratings.items() if r >= 70]
    
    print(f"Fundamentele data ophalen voor {len(rs_candidates)} RS-kandidaten...")
    fundamentals: Dict[str, Dict] = {}
    for i, ticker in enumerate(rs_candidates, 1):
        fundamentals[ticker] = get_fundamentals(ticker)
        if i % 20 == 0:
            print(f"  {i}/{len(rs_candidates)} fundamentals opgehaald...")

    portfolio_waarde = START_CAPITAL
    email_delen: List[str] = []

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")
        df_ex = df[df["Ticker"].isin(tlist)].copy()

        signalen: List[CSSignaal] = []
        for ticker, group in df_ex.groupby("Ticker", sort=False):
            fund = fundamentals.get(ticker, {})
            sig  = analyse_ticker(ticker, group, rs_ratings, fund)
            if sig:
                signalen.append(sig)
                print(f"  ✓ {ticker}: {sig.score}/7 | RS={sig.rs:.0f} | EPS+{sig.eps_q_growth:.0f}%")

        signalen.sort(key=lambda s: s.total_score, reverse=True)
        print(f"  → {len(signalen)} CAN SLIM kandidaten")

        for s in signalen:
            log_selectie(
                ticker=s.ticker, datum=today_str(), strategie="bot_00cs",
                beurs=ex_name, koers=s.price,
                parameters={
                    "score": s.score, "total_score": s.total_score,
                    "eps_q_growth_pct": s.eps_q_growth, "eps_annual_cagr_pct": s.eps_cagr,
                    "rs": s.rs, "pct_from_high": s.pct_from_high,
                    "vol_ratio": s.vol_ratio, "inst_pct": s.inst_pct,
                    "ma200": s.ma200, "high52w": s.high52w, "stop": s.stop,
                    "grafiek": yahoo_link(s.ticker),
                },
            )

        bericht = format_bericht(ex_name, signalen, portfolio_waarde)
        if bericht:
            send_telegram(bericht, parse_mode="Markdown")
            email_delen.append(bericht)
            print(f"  → Telegram verstuurd: {ex_name}")
        else:
            print(f"  → Overgeslagen (geen signalen): {ex_name}")

    if email_delen:
        send_email(
            subject=f"CAN SLIM rapport {today_str()}",
            body="\n\n" + ("=" * 40 + "\n\n").join(email_delen),
        )

    print(f"\n{'='*60}")
    print("Klaar.")
