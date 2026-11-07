#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00ms.py — MINERVINI SEPA ENGINE v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Alle indicatoren (ATR, MA's) komen uit common.indicators.
Backtest-functionaliteit is volledig behouden.
"""

import sys
import math
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from common import (
    today_str, safe_float, yahoo_link, esc,
    download_history, load_tickers_from_file, bouw_bestandslijst, label_voor,
    compute_atr_series,
    send_telegram, send_email,
    bereken_positie, trade_cost,
    SLIPPAGE_PCT, RISICO_PCT_DEFAULT, TRADE_COST_FIXED, TRADE_COST_PCT
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

MS_CFG = {
    "ma_fast":             50,
    "ma_mid":              150,
    "ma_slow":             200,
    "max_from_high_pct":   25.0,
    "min_from_low_pct":    30.0,
    "rs_min":              70,
    "atr_period":          14,
    "vcp_lookback":        60,
    "vol_contraction_pct": 20.0,
    "volume_dry_pct":      30.0,
    "pivot_lookback":      20,
    "pivot_breakout_vol":  1.5,
    "stop_pct":            8.0,
    "min_score":           2,
}

BACKTEST_START = "2021-01-01"
BACKTEST_END   = today_str()

# ============================================================
# INDICATOREN
# ============================================================

def add_indicators(df: pd.DataFrame, universe_rs: Dict[str, float]) -> pd.DataFrame:
    parts = []
    for ticker, group in df.groupby("Ticker", sort=False):
        g     = group.copy()
        close = g["Close"]
        high  = g["High"]
        low   = g["Low"]
        vol   = g["Volume"]

        g["MA50"]        = close.rolling(MS_CFG["ma_fast"]).mean()
        g["MA150"]       = close.rolling(MS_CFG["ma_mid"]).mean()
        g["MA200"]       = close.rolling(MS_CFG["ma_slow"]).mean()
        g["MA200_slope"] = g["MA200"].diff(20)

        g["ATR14"]  = compute_atr_series(high, low, close, MS_CFG["atr_period"])
        g["VolMA20"] = vol.rolling(20).mean()
        g["High52w"] = close.rolling(252).max()
        g["Low52w"]  = close.rolling(252).min()
        g["RS"]      = universe_rs.get(ticker, 50.0)
        g["Ticker"]  = ticker
        parts.append(g)

    if not parts:
        return df
    return pd.concat(parts).sort_values(["Ticker", "Date"]).reset_index(drop=True)

def compute_rs_ratings(df: pd.DataFrame) -> Dict[str, float]:
    rs_map: Dict[str, float] = {}
    perf:   Dict[str, float] = {}
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

    if not perf:
        return rs_map
    values = sorted(perf.values())
    n = len(values)
    for ticker, p in perf.items():
        rank = sum(1 for v in values if v < p)
        rs_map[ticker] = round((rank / n) * 99, 1)
    return rs_map

# ============================================================
# TREND TEMPLATE
# ============================================================

def check_trend_template(row: pd.Series) -> Tuple[bool, List[str]]:
    close  = safe_float(row.get("Close"))
    ma50   = safe_float(row.get("MA50"))
    ma150  = safe_float(row.get("MA150"))
    ma200  = safe_float(row.get("MA200"))
    ma200s = safe_float(row.get("MA200_slope"))
    h52    = safe_float(row.get("High52w"))
    l52    = safe_float(row.get("Low52w"))
    rs     = safe_float(row.get("RS"), 0.0)

    checks = []
    passed = True

    def chk(condition: bool, ok_msg: str, fail_msg: str):
        nonlocal passed
        if condition:
            checks.append(f"✓ {ok_msg}")
        else:
            checks.append(f"✗ {fail_msg}")
            passed = False

    chk(not math.isnan(close) and not math.isnan(ma150) and not math.isnan(ma200) and close > ma150 and close > ma200, "Close>MA150/MA200", "Close niet boven MA150/MA200")
    chk(not math.isnan(ma150) and not math.isnan(ma200) and ma150 > ma200, "MA150>MA200", "MA150 niet boven MA200")
    chk(not math.isnan(ma200s) and ma200s > 0, "MA200 stijgt", "MA200 daalt/vlak")
    chk(not math.isnan(ma50) and not math.isnan(ma150) and not math.isnan(ma200) and ma50 > ma150 and ma50 > ma200, "MA50>MA150/MA200", "MA50 niet boven MA150/MA200")
    chk(not math.isnan(close) and not math.isnan(ma50) and close > ma50, "Close>MA50", "Close niet boven MA50")

    if not math.isnan(h52) and h52 > 0:
        pct_from_high = (h52 - close) / h52 * 100
        chk(pct_from_high <= MS_CFG["max_from_high_pct"], f"{pct_from_high:.1f}% onder 52w high (<=25%)", f"{pct_from_high:.1f}% onder 52w high (>25%)")
    else:
        checks.append("✗ geen 52w high data")
        passed = False

    if not math.isnan(l52) and l52 > 0:
        pct_from_low = (close - l52) / l52 * 100
        chk(pct_from_low >= MS_CFG["min_from_low_pct"], f"{pct_from_low:.1f}% boven 52w low (>=30%)", f"{pct_from_low:.1f}% boven 52w low (<30%)")
    else:
        checks.append("✗ geen 52w low data")
        passed = False

    chk(rs >= MS_CFG["rs_min"], f"RS={rs:.0f} (>=70)", f"RS={rs:.0f} (<70)")

    return passed, checks

# ============================================================
# VCP DETECTIE
# ============================================================

def detect_vcp(g: pd.DataFrame) -> Tuple[int, List[str], float, float]:
    score   = 0
    details = []
    close   = g["Close"]
    volume  = g["Volume"]
    atr     = g["ATR14"]
    vol_ma  = g["VolMA20"]

    if len(g) < MS_CFG["vcp_lookback"] + 10:
        return 0, ["onvoldoende data"], float("nan"), float("nan")

    recent      = g.iloc[-MS_CFG["vcp_lookback"]:]
    very_recent = g.iloc[-MS_CFG["pivot_lookback"]:]

    atr_now   = safe_float(atr.iloc[-1])
    atr_start = safe_float(atr.iloc[-MS_CFG["vcp_lookback"]])
    if not math.isnan(atr_now) and not math.isnan(atr_start) and atr_start > 0:
        contraction = (atr_start - atr_now) / atr_start * 100
        if contraction >= MS_CFG["vol_contraction_pct"]:
            score += 1
            details.append(f"✓ ATR contractie {contraction:.1f}%")
        else:
            details.append(f"✗ ATR contractie {contraction:.1f}% (min {MS_CFG['vol_contraction_pct']}%)")
    else:
        details.append("✗ ATR data onvoldoende")

    vol_now  = safe_float(volume.iloc[-1])
    vol_mean = safe_float(vol_ma.iloc[-1])
    if not math.isnan(vol_now) and not math.isnan(vol_mean) and vol_mean > 0:
        vol_ratio = vol_now / vol_mean * 100
        if vol_ratio <= (100 - MS_CFG["volume_dry_pct"]):
            score += 1
            details.append(f"✓ Volume droogvalt {vol_ratio:.0f}% van gem.")
        else:
            details.append(f"✗ Volume {vol_ratio:.0f}% van gem.")
    else:
        details.append("✗ Volume data onvoldoende")

    n = len(recent)
    third = n // 3
    if third >= 5:
        r1 = recent.iloc[:third]["Close"]
        r2 = recent.iloc[third:2*third]["Close"]
        r3 = recent.iloc[2*third:]["Close"]
        range1 = float(r1.max() - r1.min())
        range2 = float(r2.max() - r2.min())
        range3 = float(r3.max() - r3.min())
        if range1 > 0 and range2 < range1 and range3 < range2:
            score += 1
            details.append(f"✓ Pullbacks krimpen: {range1:.2f}->{range2:.2f}->{range3:.2f}")
        else:
            details.append(f"✗ Pullbacks krimpen niet: {range1:.2f}->{range2:.2f}->{range3:.2f}")
    else:
        details.append("✗ Onvoldoende data voor pullback analyse")

    pivot_high      = float(very_recent["Close"].iloc[:-1].max())
    current         = safe_float(close.iloc[-1])
    vol_recent_mean = safe_float(vol_ma.iloc[-MS_CFG["pivot_lookback"]])
    vol_today       = safe_float(volume.iloc[-1])

    breakout = (not math.isnan(current) and current > pivot_high and not math.isnan(vol_today) and not math.isnan(vol_recent_mean) and vol_recent_mean > 0 and vol_today >= vol_recent_mean * MS_CFG["pivot_breakout_vol"])
    if breakout:
        score += 1
        details.append(f"✓ Pivot breakout boven {pivot_high:.2f}")
    else:
        details.append(f"✗ Geen pivot breakout (pivot={pivot_high:.2f})")

    stop_prijs  = float(very_recent["Close"].min())
    stop_max    = current * (1 - MS_CFG["stop_pct"] / 100)
    stop_prijs  = max(stop_prijs, stop_max)
    pivot_prijs = float(recent["Close"].max())

    return score, details, pivot_prijs, stop_prijs

# ============================================================
# SEPA SIGNAAL
# ============================================================

@dataclass
class SEPASignaal:
    ticker:        str
    price:         float
    trend_passed:  bool
    trend_details: List[str]
    vcp_score:     int
    vcp_details:   List[str]
    pivot:         float
    stop:          float
    rs:            float
    pct_from_high: float
    pct_from_low:  float
    atr:           float
    total_score:   float

def analyse_ticker(ticker: str, g: pd.DataFrame) -> Optional[SEPASignaal]:
    try:
        g = g.sort_values("Date").copy()
        if len(g) < MS_CFG["ma_slow"] + 10:
            return None

        last = g.iloc[-1]
        current_price = safe_float(last.get("Close"))
        if current_price <= 0 or math.isnan(current_price):
            return None

        trend_passed, trend_details = check_trend_template(last)
        if not trend_passed:
            return None

        vcp_score, vcp_details, pivot, stop = detect_vcp(g)
        if vcp_score < MS_CFG["min_score"]:
            return None

        h52 = safe_float(last.get("High52w"))
        l52 = safe_float(last.get("Low52w"))
        rs  = safe_float(last.get("RS"), 0.0)
        atr = safe_float(last.get("ATR14"), current_price * 0.02)

        pct_from_high = ((h52 - current_price) / h52 * 100) if h52 > 0 else 0.0
        pct_from_low  = ((current_price - l52) / l52 * 100) if l52 > 0 else 0.0
        total_score   = rs * 0.4 + vcp_score * 15 + (25 - pct_from_high) * 0.5

        return SEPASignaal(
            ticker=ticker, price=round(current_price, 2),
            trend_passed=trend_passed, trend_details=trend_details,
            vcp_score=vcp_score, vcp_details=vcp_details,
            pivot=round(pivot, 2), stop=round(stop, 2),
            rs=round(rs, 1), pct_from_high=round(pct_from_high, 1),
            pct_from_low=round(pct_from_low, 1), atr=round(atr, 4),
            total_score=round(total_score, 1),
        )
    except Exception as e:
        print(f"[WARN] {ticker}: fout — {e}")
        return None

# ============================================================
# OUTPUT
# ============================================================

def _vcp_bar(score: int) -> str:
    return "█" * score + "░" * (4 - score) + f" {score}/4"

def format_bericht(exchange_name: str, signalen: List[SEPASignaal], portfolio_waarde: float) -> Optional[str]:
    if not signalen:
        return None

    nu      = today_str()
    max_sc  = max(s.vcp_score for s in signalen)
    toon    = [s for s in signalen if s.vcp_score == max_sc]
    top2    = signalen[:2]
    lbl     = {4: "🔥 PERFECTE VCP (4/4)", 3: "⚡ STERK (3/4)", 2: "📊 WATCHLIST (2/4)"}.get(max_sc, "📊")

    delen = [
        f"📈 *MINERVINI SEPA — {esc(exchange_name)}*",
        f"_{nu} | Stage 2 + VCP | {len(signalen)} kandidaten_",
        "─────────────────────────────",
        f"🏆 *TOP 2:*",
    ]
    for s in top2:
        rr = ((s.pivot - s.price) / (s.price - s.stop)) if (s.price - s.stop) > 0 else 0
        entry = s.price * (1 + SLIPPAGE_PCT)
        aandelen, max_loss = bereken_positie(portfolio_waarde, entry, s.stop)
        investering = round(entry * aandelen, 2)
        
        delen.append(
            f"• `{s.ticker}` VCP:{_vcp_bar(s.vcp_score)} RS:{s.rs:.0f} "
            f"EUR{s.price:.2f} {yahoo_link(s.ticker)}\n"
            f"  {s.pct_from_high:.1f}% onder high | R/R:{rr:.1f}:1\n"
            f"  Entry: EUR{entry:.2f} | Stop: EUR{s.stop:.2f} | {aandelen} stuks\n"
            f"  Investering: EUR{investering:,.2f} | Max verlies: EUR{max_loss:,.2f}"
        )

    delen += ["─────────────────────────────", f"*{lbl}:*"]
    for s in [x for x in toon if x not in top2]:
        delen.append(f"• `{s.ticker}` VCP:{_vcp_bar(s.vcp_score)} RS:{s.rs:.0f} EUR{s.price:.2f} | {s.pct_from_high:.1f}% onder high")
    delen.append(f"⚙️ _Stop max {MS_CFG['stop_pct']:.0f}% | RS>={MS_CFG['rs_min']} | Risico 5% portfolio_")
    
    return "\n\n".join(delen)

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    print(f"{'='*60}")
    print(f"MINERVINI SEPA — LIVE  {today_str()}")
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
    print("Data downloaden (2 jaar)...")
    df = download_history(all_tickers, period="2y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    print(f"Data geladen: {df['Ticker'].nunique()} tickers")
    print("RS ratings berekenen...")
    rs_ratings = compute_rs_ratings(df)
    print(f"RS ratings: {len(rs_ratings)} tickers")
    print("Indicatoren berekenen...")
    df = add_indicators(df, rs_ratings)

    portfolio_waarde = START_CAPITAL
    email_delen: List[str] = []

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")
        df_ex = df[df["Ticker"].isin(tlist)].copy()

        signalen: List[SEPASignaal] = []
        for ticker, group in df_ex.groupby("Ticker", sort=False):
            sig = analyse_ticker(ticker, group)
            if sig:
                signalen.append(sig)
                print(f"  ✓ {ticker}: VCP={sig.vcp_score}/4 | RS={sig.rs:.0f} | {sig.pct_from_high:.1f}% onder high")

        signalen.sort(key=lambda s: s.total_score, reverse=True)
        print(f"  → {len(signalen)} SEPA kandidaten")

        for s in signalen:
            log_selectie(
                ticker=s.ticker, datum=today_str(), strategie="bot_00ms",
                beurs=ex_name, koers=s.price,
                parameters={
                    "score": s.vcp_score, "total_score": s.total_score,
                    "rs": s.rs, "pivot": s.pivot, "stop": s.stop,
                    "pct_from_high": s.pct_from_high, "pct_from_low": s.pct_from_low,
                    "atr": s.atr, "grafiek": yahoo_link(s.ticker),
                },
            )

        bericht = format_bericht(ex_name, signalen, portfolio_waarde)
        if bericht:
            send_telegram(bericht, parse_mode="Markdown")
            email_delen.append(bericht)
            print(f"  → Telegram verstuurd")
        else:
            print(f"  → Overgeslagen: {ex_name}")

    if email_delen:
        send_email(subject=f"Minervini SEPA rapport {today_str()}", body="\n\n" + ("=" * 40 + "\n\n").join(email_delen))

    print(f"\n{'='*60}")
    print("Klaar.")

# ============================================================
# BACKTEST ENGINE
# ============================================================

@dataclass
class BTPosition:
    ticker:      str
    entry_date:  pd.Timestamp
    entry_price: float
    size:        int
    cost:        float
    sl:          Optional[float]
    tp:          Optional[float]
    atr:         float
    days:        int = 0

def run_backtest():
    print(f"{'='*60}")
    print(f"MINERVINI SEPA BACKTEST  {BACKTEST_START} -> {BACKTEST_END}")
    print(f"{'='*60}")

    all_tickers: List[str] = []
    for f_name in bouw_bestandslijst():
        all_tickers.extend(load_tickers_from_file(f_name))
    all_tickers = sorted(set(all_tickers))

    if not all_tickers:
        print("[ERROR] Geen tickers gevonden.")
        return

    print(f"Tickers: {len(all_tickers)} | Data downloaden (5y)...")
    df = download_history(all_tickers, period="5y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    all_dates  = sorted(df["Date"].dt.date.unique())
    rs_ratings = compute_rs_ratings(df)
    df         = add_indicators(df, rs_ratings)

    cash      = START_CAPITAL
    positions: Dict[str, BTPosition] = {}
    trades:    List[Dict]      = []
    scan_dates = [d for d in all_dates if d.weekday() == 0]
    print(f"Scanmomenten: {len(scan_dates)} (wekelijks maandag)")

    for scan_date in scan_dates:
        df_hist = df[df["Date"] <= pd.Timestamp(scan_date)].copy()

        for ticker, group in df_hist.groupby("Ticker", sort=False):
            if ticker in positions or len(positions) >= MAX_POSITIONS:
                continue
            sig = analyse_ticker(ticker, group)
            if not sig:
                continue
            entry      = sig.price * (1 + SLIPPAGE_PCT)
            total_waarde = cash + sum(price_map.get(t, p.entry_price) * p.size for t, p in positions.items()) if 'price_map' in locals() else cash
            aandelen, _ = bereken_positie(total_waarde, entry, sig.stop)
            if aandelen <= 0:
                continue
            investering = entry * aandelen + trade_cost(entry * aandelen)
            if investering > cash:
                continue
            cash -= investering
            positions[ticker] = BTPosition(
                ticker=ticker, entry_date=scan_date, entry_price=round(entry, 4),
                size=aandelen, cost=trade_cost(investering),
                sl=sig.stop, tp=sig.pivot,
                atr=sig.atr, days=0
            )

        day_df = df[df["Date"] == pd.Timestamp(scan_date)].copy()
        price_map: Dict[str, float] = {}
        for _, row in day_df.iterrows():
            t = row.get("Ticker")
            c = safe_float(row.get("Close"))
            if t and not math.isnan(c):
                price_map[t] = c

        for ticker, pos in list(positions.items()):
            pos.days += 1
            if ticker not in price_map:
                continue
            close  = price_map[ticker]
            reason = None
            if close <= pos.sl:
                reason = f"SL ({pos.sl:.2f})"
            elif close >= pos.tp:
                reason = f"TP ({pos.tp:.2f})"
            elif pos.days >= MAX_HOLD_DAYS:
                reason = f"Time ({pos.days}d)"

            if reason:
                exit_slip = close * (1 - SLIPPAGE_PCT)
                gross     = exit_slip * pos.size
                cost      = trade_cost(gross)
                pnl       = gross - cost - (pos.entry_price * pos.size + pos.cost)
                tax       = pnl * TAX_RATE if pnl > 0 else 0.0
                cash     += gross - cost - tax
                trades.append({
                    "entry_date": pos.entry_date.isoformat(),
                    "exit_date":  scan_date.isoformat(),
                    "ticker": ticker, "vcp_score": pos.strategy if hasattr(pos, 'strategy') else 0, "rs": rs_ratings.get(ticker, 0),
                    "entry_price": pos.entry_price, "exit_price": round(exit_slip, 4),
                    "size": pos.size, "pnl": round(pnl, 2),
                    "tax": round(tax, 2), "net": round(pnl - tax, 2),
                    "reason": reason, "days": pos.days,
                })
                del positions[ticker]

    if trades:
        tdf  = pd.DataFrame(trades)
        tdf.to_csv("ms_backtest_trades.csv", index=False)
        n    = len(tdf)
        nwin = (tdf["net"] > 0).sum()
        pf   = abs(tdf.loc[tdf["net"] > 0, "net"].sum()) / max(abs(tdf.loc[tdf["net"] <= 0, "net"].sum()), 1e-9)
        final_val = cash + sum(price_map.get(t, p.entry_price) * p.size for t, p in positions.items())
        print(f"\n{'='*60}")
        print(f"Startkapitaal    : EUR{START_CAPITAL:>12,.2f}")
        print(f"Eindkapitaal     : EUR{final_val:>12,.2f}")
        print(f"Totaal rendement : {(final_val-START_CAPITAL)/START_CAPITAL*100:>+.1f}%")
        print(f"Trades           : {n} | Winnaars: {nwin} ({nwin/n*100:.1f}%)")
        print(f"Profit Factor    : {pf:.2f}")
        print(f"Belasting betaald: EUR{tdf['tax'].sum():,.2f}")
        print(f"Gem. houdduur    : {tdf['days'].mean():.1f} dagen")
        print(f"{'='*60}")
        print(f"Opgeslagen: ms_backtest_trades.csv")
    else:
        print("Geen trades gegenereerd.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
