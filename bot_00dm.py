#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00dm.py — DUAL MOMENTUM ENGINE v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Combineert top 20% omzetgroei met top 20% relatieve sterkte.
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
MAX_HOLD_DAYS        = 120

DM_CFG = {
    "top_pct":              20.0,
    "high52w_lookback":     252,
    "high_proximity_pct":   5.0,
    "breakout_vol_mult":    1.5,
    "vol_ma_period":        50,
    "ma_exit":              50,
    "atr_period":           14,
    "stop_pct":             10.0,
    "min_score":            3,
}

BACKTEST_START = "2021-01-01"
BACKTEST_END   = today_str()

# ============================================================
# HULPFUNCTIES (Data)
# ============================================================

def compute_revenue_growth(tickers: List[str]) -> Dict[str, float]:
    groei: Dict[str, float] = {}
    for i, ticker in enumerate(tickers, 1):
        try:
            t  = yf.Ticker(ticker)
            fs = t.financials
            if fs is None or fs.empty:
                continue
            rev_row = None
            for lbl in ["Total Revenue", "Revenue", "Net Revenue"]:
                if lbl in fs.index:
                    rev_row = fs.loc[lbl].dropna().sort_index()
                    break
            if rev_row is None or len(rev_row) < 2:
                continue
            rev_now  = float(rev_row.iloc[-1])
            rev_prev = float(rev_row.iloc[-2])
            if rev_prev > 0:
                groei[ticker] = round((rev_now - rev_prev) / rev_prev * 100, 1)
        except Exception:
            pass
        if i % 25 == 0:
            print(f"  Omzetgroei: {i}/{len(tickers)} opgehaald...")
    return groei

def top_pct_filter(scores: Dict[str, float], pct: float = 20.0) -> List[str]:
    if not scores:
        return []
    threshold = np.percentile(list(scores.values()), 100 - pct)
    return [t for t, v in scores.items() if v >= threshold]

def compute_rs_ratings(df: pd.DataFrame) -> Dict[str, float]:
    perf: Dict[str, float] = {}
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

    rs_map: Dict[str, float] = {}
    if not perf:
        return rs_map
    values = sorted(perf.values())
    n = len(values)
    for ticker, p in perf.items():
        rank = sum(1 for v in values if v < p)
        rs_map[ticker] = round((rank / n) * 99, 1)
    return rs_map

# ============================================================
# DUAL MOMENTUM SIGNAAL
# ============================================================

@dataclass
class DMSignaal:
    ticker:          str
    price:           float
    score:           int
    score_labels:    List[str]
    rev_growth:      float
    rev_rank:        float
    rs:              float
    rs_rank:         float
    high52w:         float
    pct_from_high:   float
    breakout:        bool
    breakout_vol:    float
    ma50:            float
    above_ma50:      bool
    stop:            float
    total_score:     float

def analyse_ticker(
    ticker:       str,
    g:            pd.DataFrame,
    rs_ratings:   Dict[str, float],
    rev_growth:   Dict[str, float],
    rs_top20:     List[str],
    rev_top20:    List[str],
) -> Optional[DMSignaal]:
    try:
        g = g.sort_values("Date").copy()
        if len(g) < DM_CFG["ma_exit"] + 10:
            return None

        close  = g["Close"]
        high   = g["High"]
        volume = g["Volume"]

        current_price = safe_float(close.iloc[-1])
        if current_price <= 0 or math.isnan(current_price):
            return None

        ma50    = safe_float(close.rolling(DM_CFG["ma_exit"]).mean().iloc[-1])
        vol_ma  = volume.rolling(DM_CFG["vol_ma_period"]).mean()
        vol_now = safe_float(volume.iloc[-1])
        vol_avg = safe_float(vol_ma.iloc[-1])
        vol_ratio = (vol_now / vol_avg) if vol_avg > 0 else 0.0

        lb   = min(DM_CFG["high52w_lookback"], len(close))
        h52w = float(close.iloc[-lb:].max())

        pct_from_high = ((h52w - current_price) / h52w * 100) if h52w > 0 else 100.0
        breakout      = current_price > h52w or pct_from_high <= 0.5
        above_ma50    = not math.isnan(ma50) and current_price > ma50

        stop = current_price * (1 - DM_CFG["stop_pct"] / 100)

        rv       = rev_growth.get(ticker, float("nan"))
        rs       = rs_ratings.get(ticker, 0.0)
        in_rev20 = ticker in rev_top20
        in_rs20  = ticker in rs_top20

        all_rs  = list(rs_ratings.values())
        all_rv  = [v for v in rev_growth.values() if not math.isnan(v)]
        rs_pct  = (sum(1 for x in all_rs if x < rs) / len(all_rs) * 100) if all_rs else 0.0
        rv_pct  = (sum(1 for x in all_rv if x < rv) / len(all_rv) * 100) if all_rv and not math.isnan(rv) else 0.0

        score  = 0
        labels = []

        def chk(ok: bool, ok_msg: str, fail_msg: str):
            nonlocal score
            if ok:
                score += 1
                labels.append(f"✓ {ok_msg}")
            else:
                labels.append(f"✗ {fail_msg}")

        chk(in_rev20, f"omzetgroei +{rv:.1f}% (top {DM_CFG['top_pct']:.0f}% universe)", f"omzetgroei {rv:.1f}% (niet top {DM_CFG['top_pct']:.0f}%)" if not math.isnan(rv) else "geen omzetdata")
        chk(in_rs20, f"RS={rs:.0f} — top {DM_CFG['top_pct']:.0f}% universe ({rs_pct:.0f}e percentiel)", f"RS={rs:.0f} — niet top {DM_CFG['top_pct']:.0f}% ({rs_pct:.0f}e percentiel)")
        chk(pct_from_high <= DM_CFG["high_proximity_pct"], f"{pct_from_high:.1f}% van 52w high ({h52w:.2f})", f"{pct_from_high:.1f}% onder 52w high (>{DM_CFG['high_proximity_pct']:.0f}%)")
        chk(breakout and vol_ratio >= DM_CFG["breakout_vol_mult"], f"uitbraak boven {h52w:.2f} op {vol_ratio:.1f}× volume", f"geen uitbraak (vol={vol_ratio:.1f}×, high={h52w:.2f})" if not breakout else f"uitbraak maar volume zwak ({vol_ratio:.1f}×)")
        chk(above_ma50, f"boven MA{DM_CFG['ma_exit']} ({ma50:.2f}) — geen exit", f"onder MA{DM_CFG['ma_exit']} ({ma50:.2f}) — EXIT SIGNAAL")

        if score < DM_CFG["min_score"]:
            return None

        total_score = score * 10 + (rs * 0.3 if in_rs20 else 0) + (rv * 0.1 if in_rev20 and not math.isnan(rv) else 0) + (15 if breakout else 0) + (5 if vol_ratio >= DM_CFG["breakout_vol_mult"] else 0)

        return DMSignaal(
            ticker=ticker, price=round(current_price, 2), score=score, score_labels=labels,
            rev_growth=round(rv, 1) if not math.isnan(rv) else 0.0, rev_rank=round(rv_pct, 1),
            rs=round(rs, 1), rs_rank=round(rs_pct, 1),
            high52w=round(h52w, 2), pct_from_high=round(pct_from_high, 1),
            breakout=breakout, breakout_vol=round(vol_ratio, 2),
            ma50=round(ma50, 2) if not math.isnan(ma50) else 0.0,
            above_ma50=above_ma50, stop=round(stop, 2), total_score=round(total_score, 1),
        )

    except Exception as e:
        print(f"[WARN] {ticker}: fout — {e}")
        return None

# ============================================================
# OUTPUT
# ============================================================

def _score_bar(score: int, max_score: int = 5) -> str:
    return "█" * score + "░" * (max_score - score) + f" {score}/{max_score}"

def format_dm_per_exchange(
    exchange_name:    str,
    signalen:         List[DMSignaal],
    exits:            List[Dict],
    portfolio_waarde: float,
) -> Optional[str]:
    if not signalen and not exits:
        return None
        
    nu = today_str()

    def koop_blok(sigs: List[DMSignaal]) -> str:
        if not sigs:
            return "_Geen kandidaten_"
        lines = []
        for s in sigs:
            entry = s.price * (1 + SLIPPAGE_PCT)
            aandelen, max_loss = bereken_positie(portfolio_waarde, entry, s.stop)
            investering = round(entry * aandelen, 2)
            
            lines.append(
                f"• `{s.ticker}` | Score: {_score_bar(s.score)} | EUR{s.price:.2f} | {yahoo_link(s.ticker)}\n"
                f"  Omzet: +{s.rev_growth:.1f}% ({s.rev_rank:.0f}e pct) | RS:{s.rs:.0f} ({s.rs_rank:.0f}e pct)\n"
                f"  52w high: EUR{s.high52w:.2f} ({s.pct_from_high:.1f}% eronder) | Vol: {s.breakout_vol:.1f}×\n"
                + "\n".join(f"  {lbl}" for lbl in s.score_labels) + "\n"
                f"  Entry: EUR{entry:.2f} | Stop: EUR{s.stop:.2f} | {aandelen} stuks\n"
                f"  Investering: EUR{investering:,.2f} | Max verlies: EUR{max_loss:,.2f}"
            )
        return "\n\n".join(lines)

    def exit_blok(ex: List[Dict]) -> str:
        if not ex:
            return "_Geen exit signalen_"
        lines = []
        for e in ex:
            lines.append(
                f"• `{e['ticker']}` 🔴 *VERKOOP*\n"
                f"  {e['reason']}\n"
                f"  Entry was: EUR{e.get('entry_price', 0):.2f}"
            )
        return "\n\n".join(lines)

    top2   = signalen[:2]
    score5 = [s for s in signalen if s.score == 5]
    score4 = [s for s in signalen if s.score == 4]
    score3 = [s for s in signalen if s.score == 3]

    delen = [
        f"🚀 *DUAL MOMENTUM — {esc(exchange_name)}*",
        f"_{nu} | Top 20% omzet × top 20% RS → uitbraak 52w high_",
        f"_Exit: slot onder MA{DM_CFG['ma_exit']}_",
        "─────────────────────────────",
        f"🏆 *TOP 2 HOOGSTE POTENTIEEL:*",
        koop_blok(top2) if top2 else "_Geen kandidaten vandaag_",
        "─────────────────────────────",
        f"🔴 *EXIT SIGNALEN (slot < MA{DM_CFG['ma_exit']}):*",
        exit_blok(exits),
        "─────────────────────────────",
        f"⭐ *PERFECTE SCORE (5/5):*",
        koop_blok(score5) if score5 else "_Geen_",
        f"⚡ *STERK (4/5):*",
        koop_blok(score4) if score4 else "_Geen_",
        f"📊 *WATCHLIST (3/5):*",
        koop_blok(score3) if score3 else "_Geen_",
        "─────────────────────────────",
        f"⚙️ _Top {DM_CFG['top_pct']:.0f}% omzet×RS | Exit<MA{DM_CFG['ma_exit']} | Risico 5%_"
    ]
    
    return "\n\n".join([d for d in delen if d])

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    print(f"{'='*60}")
    print(f"DUAL MOMENTUM — LIVE  {today_str()}")
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

    print(f"\nStap 1: Omzetgroei ophalen ({len(all_tickers)} tickers)...")
    rev_growth = compute_revenue_growth(all_tickers)
    print(f"  Omzetgroei beschikbaar: {len(rev_growth)} tickers")

    print("Stap 2: RS ratings berekenen...")
    rs_ratings = compute_rs_ratings(df)
    print(f"  RS ratings: {len(rs_ratings)} tickers")

    rev_top20 = top_pct_filter(rev_growth, DM_CFG["top_pct"])
    rs_top20  = top_pct_filter(rs_ratings, DM_CFG["top_pct"])
    print(f"  Top {DM_CFG['top_pct']:.0f}% omzetgroei : {len(rev_top20)} tickers")
    print(f"  Top {DM_CFG['top_pct']:.0f}% RS         : {len(rs_top20)} tickers")

    kandidaten = sorted(set(rev_top20) & set(rs_top20))
    print(f"  Overlap (beide top 20%) : {len(kandidaten)} tickers")

    portfolio_waarde = START_CAPITAL
    email_delen: List[str] = []
  
    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name}...")
        tlist_set = set(tlist)
        df_ex     = df[df["Ticker"].isin(tlist_set)].copy()
        
        signalen: List[DMSignaal] = []
        for ticker, group in df_ex.groupby("Ticker", sort=False):
            sig = analyse_ticker(ticker, group, rs_ratings, rev_growth, rs_top20, rev_top20)
            if sig:
                signalen.append(sig)
                print(f"  ✓ {ticker}: {sig.score}/5 | omzet+{sig.rev_growth:.1f}% | RS={sig.rs:.0f} | {'BREAKOUT' if sig.breakout else 'setup'}")

        signalen.sort(key=lambda s: s.total_score, reverse=True)

        exits: List[Dict] = []
        print(f"  → {len(signalen)} Dual Momentum kandidaten")

        for rank, s in enumerate(signalen, start=1):
            log_selectie(
                ticker=s.ticker, datum=today_str(), strategie="bot_00dm",
                beurs=ex_name, koers=s.price,
                parameters={
                    "score": s.score, "total_score": s.total_score, "rank": rank,
                    "rev_growth_pct": s.rev_growth, "rs": s.rs,
                    "pct_from_high": s.pct_from_high, "breakout": s.breakout,
                    "breakout_vol": s.breakout_vol, "high52w": s.high52w,
                    "grafiek": yahoo_link(s.ticker),
                },
            )

        bericht = format_dm_per_exchange(ex_name, signalen, exits, portfolio_waarde)
        if bericht:
            send_telegram(bericht, parse_mode="Markdown")
            email_delen.append(bericht)
            print(f"  → Telegram verstuurd: {ex_name}")
        else:
            print(f"  → Overgeslagen: {ex_name}")

    if email_delen:
        send_email(
            subject=f"Dual Momentum rapport {today_str()}",
            body="\n\n" + ("=" * 40 + "\n\n").join(email_delen),
        )

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
    print(f"DUAL MOMENTUM BACKTEST  {BACKTEST_START} -> {BACKTEST_END}")
    print(f"NB: omzetgroei backtest gebruikt technische proxy (12m prijsprestatie)")
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
    cash       = START_CAPITAL
    positions: Dict[str, BTPosition] = {}
    trades:    List[Dict]      = []
    price_map: Dict[str, float] = {}

    rs_ratings = compute_rs_ratings(df)
    rs_top20   = top_pct_filter(rs_ratings, DM_CFG["top_pct"])

    perf_proxy: Dict[str, float] = {}
    for ticker, group in df.groupby("Ticker", sort=False):
        g = group.sort_values("Date")
        if len(g) < 252:
            continue
        c_now  = safe_float(g["Close"].iloc[-1])
        c_12m  = safe_float(g["Close"].iloc[-252])
        if c_12m > 0:
            perf_proxy[ticker] = (c_now - c_12m) / c_12m * 100
    rev_top20_proxy = top_pct_filter(perf_proxy, DM_CFG["top_pct"])

    scan_dates = [d for d in all_dates if d.weekday() == 0]
    print(f"Scanmomenten: {len(scan_dates)} (wekelijks maandag)")

    for scan_date in scan_dates:
        df_hist = df[df["Date"] <= pd.Timestamp(scan_date)].copy()
        day_df  = df[df["Date"] == pd.Timestamp(scan_date)].copy()

        price_map = {}
        for _, row in day_df.iterrows():
            t = row.get("Ticker")
            c = safe_float(row.get("Close"))
            if t and not math.isnan(c):
                price_map[t] = c

        for ticker, pos in list(positions.items()):
            pos.days += 1
            if ticker not in price_map:
                continue
            g     = df_hist[df_hist["Ticker"] == ticker].sort_values("Date")
            close = g["Close"]
            ma50  = safe_float(close.rolling(DM_CFG["ma_exit"]).mean().iloc[-1])
            c_now = price_map[ticker]
            reason = None

            if not math.isnan(ma50) and c_now < ma50:
                reason = f"Slot<MA{DM_CFG['ma_exit']} ({c_now:.2f}<{ma50:.2f})"
            elif c_now <= pos.sl:
                reason = f"Hard SL ({pos.sl:.2f})"
            elif pos.days >= MAX_HOLD_DAYS:
                reason = f"Time ({pos.days}d)"

            if reason:
                exit_slip = c_now * (1 - SLIPPAGE_PCT)
                gross     = exit_slip * pos.size
                cost      = trade_cost(gross)
                pnl       = gross - cost - (pos.entry_price * pos.size + pos.cost)
                tax       = pnl * TAX_RATE if pnl > 0 else 0.0
                cash     += gross - cost - tax
                trades.append({
                    "entry_date":  pos.entry_date.isoformat(),
                    "exit_date":   scan_date.isoformat(),
                    "ticker":      ticker,
                    "entry_price": pos.entry_price,
                    "exit_price":  round(exit_slip, 4),
                    "size":        pos.size,
                    "pnl":         round(pnl, 2),
                    "tax":         round(tax, 2),
                    "net":         round(pnl - tax, 2),
                    "reason":      reason,
                    "days":        pos.days,
                })
                del positions[ticker]

        for ticker, group in df_hist.groupby("Ticker", sort=False):
            if ticker in positions or len(positions) >= MAX_POSITIONS:
                continue
            if ticker not in rs_top20 or ticker not in rev_top20_proxy:
                continue
            sig = analyse_ticker(
                ticker, group, rs_ratings, perf_proxy, rs_top20, rev_top20_proxy
            )
            if not sig or not sig.breakout:
                continue

            entry      = sig.price * (1 + SLIPPAGE_PCT)
            total_waarde = cash + sum(price_map.get(t, p.entry_price) * p.size for t, p in positions.items())
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
                sl=sig.stop, tp=None,
                atr=0.0, days=0
            )

    if trades:
        tdf = pd.DataFrame(trades)
        tdf.to_csv("dm_backtest_trades.csv", index=False)
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
        print(f"Opgeslagen       : dm_backtest_trades.csv")
        print(f"{'='*60}")
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
