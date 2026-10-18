#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00mr.py — MULTI-SYSTEEM ENGINE v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Combineert IBS+RSI Mean Reversion en Opening Range Breakout (ORB).
Backtest-functionaliteit is volledig behouden.
"""

import os
import sys
import math
import json
import warnings
import datetime as dt
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

from common import (
    today_str, safe_float, yahoo_link,
    download_history, load_tickers_from_file, bouw_bestandslijst, label_voor,
    wilder_smooth, compute_rsi_series, compute_ibs,
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

try:
    from supabase import create_client, Client
    _SUPABASE_AVAILABLE = True
except ImportError:
    _SUPABASE_AVAILABLE = False

warnings.filterwarnings("ignore", category=FutureWarning)

# ============================================================
# CONFIG
# ============================================================

PORTFOLIO_FILE   = "mr_portfolio.json"
SUPABASE_URL     = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY     = os.getenv("SUPABASE_KEY", "")
TRADES_TABLE     = "mr_trades"
SNAPSHOTS_TABLE  = "mr_snapshots"

CFG = {
    "start_capital":        50_000.0,
    "max_positions":        10,
    "min_cash_ratio":       0.10,
    "budget_mr":            0.40,
    "budget_orb":           0.30,
    "budget_reserve":       0.30,
    "risico_pct":           0.04,
    "max_daily_loss_pct":   0.03,
    "cost_fixed":           15.0,
    "cost_pct":             0.0035,
    "tax_rate":             0.10,
    "ibs_entry":            0.20,
    "ibs_exit":             0.70,
    "rsi_period":           3,
    "rsi_entry":            10.0,
    "ma_trend":             200,
    "ma_exit":              20,
    "atr_period":           14,
    "mr_max_hold":          10,
    "orb_minutes":          15,
    "orb_vol_mult":         1.5,
    "orb_tp_mult":          2.0,
    "orb_max_hold":         1,
}

# ============================================================
# PORTFOLIO STATE
# ============================================================

def load_portfolio() -> Dict:
    default = {
        "cash":           CFG["start_capital"],
        "positions":      {},
        "daily_pnl":      0.0,
        "daily_start":    today_str(),
        "total_trades":   0,
    }
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE) as f:
                data = json.load(f)
            if data.get("daily_start") != today_str():
                data["daily_pnl"]   = 0.0
                data["daily_start"] = today_str()
            return data
        except Exception:
            pass
    return default

def save_portfolio(p: Dict) -> None:
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(p, f, indent=2)

def portfolio_waarde(p: Dict, prices: Dict[str, float]) -> float:
    pos_val = sum(prices.get(t, pos["entry_price"]) * pos["size"] for t, pos in p["positions"].items())
    return p["cash"] + pos_val

def risk_budget(p: Dict, systeem: str, prices: Dict[str, float]) -> float:
    totaal = portfolio_waarde(p, prices)
    pct    = CFG[f"budget_{systeem}"]
    return totaal * pct

def max_daily_loss_bereikt(p: Dict, prices: Dict[str, float]) -> bool:
    totaal = portfolio_waarde(p, prices)
    return p["daily_pnl"] <= -(totaal * CFG["max_daily_loss_pct"])

def log_trade(datum, ticker, systeem, side, price, size, cost, pnl, tax, net, reason=""):
    if not _SUPABASE_AVAILABLE or not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        sb.table(TRADES_TABLE).insert({
            "datum": str(datum), "ticker": ticker, "systeem": systeem,
            "side": side, "price": price, "size": size, "cost": cost,
            "pnl": pnl, "tax": tax, "net": net, "reason": reason,
        }).execute()
    except Exception as e:
        print(f"[supabase] Kon trade voor {ticker} niet wegschrijven: {e}")

def log_snapshot(p: Dict, prices: Dict[str, float]) -> None:
    if not _SUPABASE_AVAILABLE or not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        totaal  = portfolio_waarde(p, prices)
        pos_val = totaal - p["cash"]
        sb.table(SNAPSHOTS_TABLE).insert({
            "datum": today_str(), "cash": round(p["cash"], 2),
            "pos_value": round(pos_val, 2), "total": round(totaal, 2),
            "n_pos": len(p["positions"]), "daily_pnl": round(p["daily_pnl"], 2),
        }).execute()
    except Exception as e:
        print(f"[supabase] Kon snapshot niet wegschrijven: {e}")

# ============================================================
# DATA DOWNLOAD (EOD & Intraday)
# ============================================================

def download_eod(tickers: List[str], period: str = "1y") -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()
    frames = []
    try:
        data = yf.download(tickers, auto_adjust=True, group_by="ticker", progress=False, threads=True, period=period)
        if data is not None and not data.empty:
            if isinstance(data.columns, pd.MultiIndex):
                ticker_level = 1
                for lvl in range(data.columns.nlevels):
                    if any(t in set(data.columns.get_level_values(lvl)) for t in tickers):
                        ticker_level = lvl
                        break
                for t in tickers:
                    try:
                        df = data.xs(t, axis=1, level=ticker_level).dropna(how="all").copy()
                        df["Ticker"] = t
                        if "Date" not in df.columns:
                            df = df.reset_index()
                        frames.append(df)
                    except Exception:
                        pass
            else:
                df = data.copy()
                df["Ticker"] = tickers[0]
                if "Date" not in df.columns:
                    df = df.reset_index()
                frames.append(df)
    except Exception as e:
        print(f"[WARN] Batch download mislukt: {e}")

    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
    df.sort_values(["Ticker", "Date"], inplace=True)
    return df.reset_index(drop=True)

def download_intraday(tickers: List[str], interval: str = "15m") -> pd.DataFrame:
    frames = []
    for t in tickers:
        try:
            raw = yf.download(t, period="5d", interval=interval, auto_adjust=True, progress=False)
            if raw is None or raw.empty:
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            raw = raw.reset_index()
            if "Datetime" in raw.columns:
                raw = raw.rename(columns={"Datetime": "Date"})
            raw["Ticker"] = t
            frames.append(raw)
        except Exception as e:
            pass

    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"], utc=True).dt.tz_localize(None)
    df.sort_values(["Ticker", "Date"], inplace=True)
    return df.reset_index(drop=True)

# ============================================================
# INDICATOREN
# ============================================================

def add_eod_indicators(df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for ticker, g in df.groupby("Ticker", sort=False):
        g     = g.copy()
        close = g["Close"]
        high  = g["High"]
        low   = g["Low"]

        g["IBS"]   = compute_ibs(high, low, close)
        g["RSI3"]  = compute_rsi_series(close, CFG["rsi_period"])
        g["MA20"]  = close.rolling(CFG["ma_exit"]).mean()
        g["MA200"] = close.rolling(CFG["ma_trend"]).mean()

        hl  = high - low
        hcp = (high - close.shift()).abs()
        lcp = (low  - close.shift()).abs()
        tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
        g["ATR14"] = wilder_smooth(tr, CFG["atr_period"])

        g["Ticker"] = ticker
        parts.append(g)

    if not parts:
        return df
    return pd.concat(parts).sort_values(["Ticker", "Date"]).reset_index(drop=True)

# ============================================================
# SYSTEEM 1: IBS + RSI MEAN REVERSION
# ============================================================

@dataclass
class MRSignaal:
    ticker:    str
    price:     float
    ibs:       float
    rsi3:      float
    ma20:      float
    ma200:     float
    atr:       float
    stop:      float
    tp:        float
    rr:        float
    exchange:  str = ""

@dataclass
class MRExitSignaal:
    ticker:    str
    price:     float
    ma20:      float
    ibs:       float
    reden:     str
    pnl_est:   float

def generate_mr_signals(df: pd.DataFrame, exchange: str) -> Tuple[List[MRSignaal], List[MRExitSignaal]]:
    entries: List[MRSignaal]     = []
    exits:   List[MRExitSignaal] = []

    last_df = df.sort_values("Date").groupby("Ticker").last().reset_index()

    for _, row in last_df.iterrows():
        ticker = row.get("Ticker")
        close  = safe_float(row.get("Close"))
        high   = safe_float(row.get("High"))
        low    = safe_float(row.get("Low"))
        ibs    = safe_float(row.get("IBS"))
        rsi3   = safe_float(row.get("RSI3"))
        ma20   = safe_float(row.get("MA20"))
        ma200  = safe_float(row.get("MA200"))
        atr    = safe_float(row.get("ATR14"))

        if any(math.isnan(x) for x in [close, ibs, rsi3, ma20, ma200]):
            continue
        if close <= 0 or atr <= 0 or math.isnan(atr):
            continue

        if (ibs < CFG["ibs_entry"] and rsi3 < CFG["rsi_entry"] and close > ma200):
            stop = low - 0.5 * atr
            tp   = ma20
            rr   = ((tp - close) / (close - stop)) if (close - stop) > 0 else 0

            if rr > 0.5:
                entries.append(MRSignaal(
                    ticker=ticker, price=round(close, 2),
                    ibs=round(ibs, 3), rsi3=round(rsi3, 1),
                    ma20=round(ma20, 2), ma200=round(ma200, 2),
                    atr=round(atr, 4), stop=round(stop, 2), tp=round(tp, 2), rr=round(rr, 2),
                    exchange=exchange,
                ))

        if ibs > CFG["ibs_exit"]:
            exits.append(MRExitSignaal(ticker=ticker, price=round(close, 2), ma20=round(ma20, 2), ibs=round(ibs, 3), reden=f"IBS={ibs:.2f} > {CFG['ibs_exit']}", pnl_est=0.0))
        elif close > ma20:
            exits.append(MRExitSignaal(ticker=ticker, price=round(close, 2), ma20=round(ma20, 2), ibs=round(ibs, 3), reden=f"Slot {close:.2f} > MA20 {ma20:.2f}", pnl_est=0.0))

    entries.sort(key=lambda s: s.ibs)
    return entries, exits

# ============================================================
# SYSTEEM 2: OPENING RANGE BREAKOUT (ORB)
# ============================================================

@dataclass
class ORBSignaal:
    ticker:       str
    orb_high:     float
    orb_low:      float
    orb_range:    float
    breakout:     bool
    current:      float
    stop:         float
    tp:           float
    vol_ratio:    float
    exchange:     str = ""

def generate_orb_signals(tickers: List[str], exchange: str) -> List[ORBSignaal]:
    df = download_intraday(tickers, interval="15m")
    if df.empty:
        return []

    signalen: List[ORBSignaal] = []
    today    = pd.Timestamp.now().date()

    for ticker, g in df.groupby("Ticker", sort=False):
        g_today = g[g["Date"].dt.date == today].copy()
        if len(g_today) < 2:
            continue

        g_today = g_today.sort_values("Date")
        orb_candle = g_today.iloc[0]
        orb_high   = safe_float(orb_candle.get("High"))
        orb_low    = safe_float(orb_candle.get("Low"))

        if math.isnan(orb_high) or math.isnan(orb_low) or orb_high <= orb_low:
            continue

        orb_range = orb_high - orb_low
        risk      = orb_range
        last       = g_today.iloc[-1]
        current    = safe_float(last.get("Close"))
        current_vol = safe_float(last.get("Volume"))

        if math.isnan(current):
            continue

        vol_mean  = g_today["Volume"].mean()
        vol_ratio = (current_vol / vol_mean) if vol_mean > 0 else 0.0
        breakout = (current > orb_high and vol_ratio >= CFG["orb_vol_mult"])
        stop = orb_low
        tp   = orb_high + CFG["orb_tp_mult"] * risk

        if breakout or current > orb_high * 0.995:
            signalen.append(ORBSignaal(
                ticker=ticker, orb_high=round(orb_high, 2), orb_low=round(orb_low, 2),
                orb_range=round(orb_range, 2), breakout=breakout, current=round(current, 2),
                stop=round(stop, 2), tp=round(tp, 2), vol_ratio=round(vol_ratio, 2), exchange=exchange,
            ))

    signalen.sort(key=lambda s: (s.breakout, s.vol_ratio), reverse=True)
    return signalen
  
# ============================================================
# PERFORMANCE TRACKER
# ============================================================

def compute_performance() -> Dict:
    perf = {"MR": {"trades": 0, "win": 0, "net": 0.0, "tax": 0.0}, "ORB": {"trades": 0, "win": 0, "net": 0.0, "tax": 0.0}}
    if not _SUPABASE_AVAILABLE or not SUPABASE_URL or not SUPABASE_KEY:
        return perf

    try:
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        resp = sb.table(TRADES_TABLE).select("*").execute()
        rows = resp.data or []
        if not rows:
            return perf
        df = pd.DataFrame(rows)
        for systeem in ["MR", "ORB"]:
            sub = df[df["systeem"] == systeem]
            if sub.empty:
                continue
            sells = sub[sub["side"] == "SELL"]
            if sells.empty:
                continue
            perf[systeem]["trades"] = len(sells)
            perf[systeem]["win"]    = int((sells["net"] > 0).sum())
            perf[systeem]["net"]    = round(float(sells["net"].sum()), 2)
            perf[systeem]["tax"]    = round(float(sells["tax"].sum()), 2)
    except Exception as e:
        print(f"[WARN] Performance berekening: {e}")

    return perf

# ============================================================
# TELEGRAM RAPPORTEN
# ============================================================

def rapport_eod(
    mr_entries:   Dict[str, List[MRSignaal]],
    mr_exits:     Dict[str, List[MRExitSignaal]],
    portfolio:    Dict,
    prices:       Dict[str, float],
) -> str:
    nu    = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    totaal = portfolio_waarde(portfolio, prices)
    perf  = compute_performance()

    dagverlies_pct = abs(portfolio["daily_pnl"]) / totaal * 100 if totaal > 0 else 0

    lines = [
        f"📊 *MULTI-SYSTEEM BOT — EOD RAPPORT*",
        f"_{nu}_", f"",
        f"💼 *PORTFOLIO STATUS:*",
        f"  Totaal waarde : EUR{totaal:,.2f}",
        f"  Cash          : EUR{portfolio['cash']:,.2f}",
        f"  Posities      : {len(portfolio['positions'])}/{CFG['max_positions']}",
        f"  Dagverlies    : EUR{portfolio['daily_pnl']:+,.2f} ({dagverlies_pct:.1f}%)",
        f"",
    ]

    if max_daily_loss_bereikt(portfolio, prices):
        lines.append(f"🚨 *MAX DAGVERLIES BEREIKT ({CFG['max_daily_loss_pct']*100:.0f}%) — GEEN NIEUWE TRADES*")
        lines.append("")

    lines += [
        f"💰 *RISK BUDGETTEN:*",
        f"  MR  (40%): EUR{risk_budget(portfolio, 'mr', prices):,.0f}",
        f"  ORB (30%): EUR{risk_budget(portfolio, 'orb', prices):,.0f}",
        f"  Reserve  : EUR{risk_budget(portfolio, 'reserve', prices):,.0f}",
        f"", f"─────────────────────────────", f"📈 *PERFORMANCE PER SYSTEEM:*"
    ]
    for sys_name, p in perf.items():
        if p["trades"] > 0:
            wr = p["win"] / p["trades"] * 100
            lines.append(f"  {sys_name}: {p['trades']} trades | WR:{wr:.0f}% | Net:EUR{p['net']:+,.2f} | Tax:EUR{p['tax']:,.2f}")
        else:
            lines.append(f"  {sys_name}: nog geen trades")
    lines.append("")
    lines.append("─────────────────────────────")

    lines.append(f"🛡️ *SYSTEEM 1: IBS+RSI MEAN REVERSION — ENTRY SIGNALEN*")
    total_mr = sum(len(v) for v in mr_entries.values())
    if total_mr == 0:
        lines.append("_Geen paniek-koop signalen vandaag_")
    else:
        for ex, sigs in mr_entries.items():
            if not sigs: continue
            lines.append(f"\n*{ex}:*")
            for s in sigs[:5]:
                n, ml = bereken_positie(totaal, s.price, s.stop)
                lines.append(f"• `{s.ticker}` | IBS:{s.ibs:.2f} | RSI3:{s.rsi3:.1f} | EUR{s.price:.2f} | [Grafiek](https://finance.yahoo.com/quote/{s.ticker})\n  Entry: {s.price:.2f} | Stop: {s.stop:.2f} | {n} stuks | Max verlies: EUR{ml:,.2f}")
    lines.append("")

    total_exits = sum(len(v) for v in mr_exits.values())
    if total_exits > 0:
        lines.append(f"🔴 *SYSTEEM 1: EXIT SIGNALEN*")
        for ex, exits in mr_exits.items():
            open_tickers = set(portfolio["positions"].keys())
            rel_exits    = [e for e in exits if e.ticker in open_tickers]
            for e in rel_exits:
                pos = portfolio["positions"].get(e.ticker, {})
                pnl = (e.price - pos.get("entry_price", e.price)) * pos.get("size", 0)
                lines.append(f"• `{e.ticker}` 🔴 VERKOOP | {e.reden}\n  Prijs: EUR{e.price:.2f} | PnL est: EUR{pnl:+.2f}")
        lines.append("")

    if portfolio["positions"]:
        lines.append("📂 *OPEN POSITIES:*")
        for t, pos in portfolio["positions"].items():
            cur = prices.get(t, pos["entry_price"])
            pnl = (cur - pos["entry_price"]) * pos["size"]
            lines.append(f"  • `{t}` [{pos['systeem']}] {pos['size']}× @ EUR{pos['entry_price']:.2f} | nu EUR{cur:.2f} | PnL: EUR{pnl:+.2f} | dag {pos.get('days_open', 0)}")
        lines.append("")

    lines += [
        "⚙️ *PARAMETERS:*",
        f"_IBS entry <{CFG['ibs_entry']} | RSI(3) <{CFG['rsi_entry']} | Close>MA{CFG['ma_trend']}_",
        f"_Exit: IBS>{CFG['ibs_exit']} OF Close>MA{CFG['ma_exit']}_",
        f"_Risico: {CFG['risico_pct']*100:.0f}% equity/trade | Max dagverlies: {CFG['max_daily_loss_pct']*100:.0f}%_",
    ]
    return "\n".join(lines)

def rapport_orb(orb_signalen: Dict[str, List[ORBSignaal]], portfolio: Dict, prices: Dict[str, float]) -> str:
    nu    = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    totaal = portfolio_waarde(portfolio, prices)

    lines = [
        f"⚡ *SYSTEEM 2: ORB RAPPORT — {nu}*",
        f"_Opening Range Breakout | 15m candles_",
        f"_Budget: EUR{risk_budget(portfolio, 'orb', prices):,.0f} (30% portfolio)_", f"",
    ]

    if max_daily_loss_bereikt(portfolio, prices):
        lines.append(f"🚨 *MAX DAGVERLIES BEREIKT — GEEN ORB TRADES*")
        return "\n".join(lines)

    total = sum(len(v) for v in orb_signalen.values())
    if total == 0:
        lines.append("_Geen ORB breakouts vandaag_")
    else:
        for ex, sigs in orb_signalen.items():
            if not sigs: continue
            lines.append(f"*{ex}:*")
            for s in sigs[:3]:
                n, ml = bereken_positie(totaal, s.current, s.stop)
                status = "🚀 BREAKOUT" if s.breakout else "⏳ Bijna"
                lines.append(f"• `{s.ticker}` {status} | EUR{s.current:.2f} | Vol:{s.vol_ratio:.1f}× | [Grafiek](https://finance.yahoo.com/quote/{s.ticker})\n  ORB: {s.orb_low:.2f}–{s.orb_high:.2f} (range EUR{s.orb_range:.2f})\n  Entry: {s.current:.2f} | Stop: {s.stop:.2f} | {n} stuks | Max verlies: EUR{ml:,.2f}")
            lines.append("")

    lines += [
        "⚙️ *ORB PARAMETERS:*",
        f"_Opening range: eerste {CFG['orb_minutes']} minuten_",
        f"_Breakout: boven ORB high op ≥{CFG['orb_vol_mult']}× volume_",
        f"_Stop: onder ORB low | TP: {CFG['orb_tp_mult']}× risk_",
        f"_Exit: zelfde dag (intraday only)_",
    ]
    return "\n".join(lines)

# ============================================================
# EOD RUN
# ============================================================

def run_eod():
    print(f"{'='*60}\nMULTI-SYSTEEM BOT — EOD  {today_str()}\n{'='*60}")

    portfolio = load_portfolio()
    exchange_tickers: Dict[str, List[str]] = {}
    all_tickers: List[str] = []
    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist: continue
        ex_name = label_voor(f_name)
        exchange_tickers[ex_name] = tlist
        all_tickers.extend(tlist)
    all_tickers = sorted(set(all_tickers))
    print(f"Totaal tickers: {len(all_tickers)}")

    print("EOD data downloaden...")
    df = download_eod(all_tickers, period="1y")
    if df.empty:
        print("[ERROR] Geen data.")
        return
    df = add_eod_indicators(df)

    prices: Dict[str, float] = {}
    last_df = df.groupby("Ticker").last().reset_index()
    for _, row in last_df.iterrows():
        t = row.get("Ticker")
        c = safe_float(row.get("Close"))
        if t and not math.isnan(c):
            prices[t] = c

    if max_daily_loss_bereikt(portfolio, prices):
        print(f"🚨 MAX DAGVERLIES BEREIKT — geen nieuwe trades")
        send_telegram(f"🚨 *MAX DAGVERLIES BEREIKT* ({CFG['max_daily_loss_pct']*100:.0f}%) — bot gestopt voor vandaag")
        return

    for t in portfolio["positions"]:
        portfolio["positions"][t]["days_open"] = portfolio["positions"][t].get("days_open", 0) + 1

    mr_entries: Dict[str, List[MRSignaal]]     = {}
    mr_exits:   Dict[str, List[MRExitSignaal]] = {}
    email_delen: List[str] = []
  
    for ex_name, tlist in exchange_tickers.items():
        df_ex = df[df["Ticker"].isin(tlist)].copy()
        entries, exits = generate_mr_signals(df_ex, ex_name)

        for e in exits:
            if e.ticker in portfolio["positions"]:
                pos       = portfolio["positions"][e.ticker]
                exit_p    = e.price * (1 - SLIPPAGE_PCT)
                gross     = exit_p * pos["size"]
                cost      = trade_cost(gross)
                pnl       = gross - cost - (pos["entry_price"] * pos["size"] + pos.get("cost", 0))
                tax       = pnl * CFG["tax_rate"] if pnl > 0 else 0.0
                portfolio["cash"] += gross - cost - tax
                portfolio["daily_pnl"] = portfolio.get("daily_pnl", 0) + pnl - tax
                log_trade(today_str(), e.ticker, "MR", "SELL", exit_p, pos["size"], cost, pnl, tax, pnl - tax, e.reden)
                del portfolio["positions"][e.ticker]
                print(f"  EXIT {e.ticker}: {e.reden} | PnL: EUR{pnl:+.2f}")

        mr_entries[ex_name] = entries
        mr_exits[ex_name]   = exits

        totaal = portfolio_waarde(portfolio, prices)
        budget = risk_budget(portfolio, "mr", prices)

        for sig in entries:
            if sig.ticker in portfolio["positions"]: continue
            if len(portfolio["positions"]) >= CFG["max_positions"]: break
            if max_daily_loss_bereikt(portfolio, prices): break

            entry_p  = sig.price * (1 + SLIPPAGE_PCT)
            n, ml    = bereken_positie(totaal, entry_p, sig.stop)
            if n <= 0: continue
            investering = entry_p * n + trade_cost(entry_p * n)
            if investering > portfolio["cash"] or investering > budget: continue

            portfolio["cash"] -= investering
            portfolio["positions"][sig.ticker] = {
                "entry_price": round(entry_p, 4), "size": n, "stop": sig.stop, "tp": sig.tp,
                "systeem": "MR", "exchange": ex_name, "days_open": 0, "cost": trade_cost(entry_p * n),
            }
            portfolio["total_trades"] = portfolio.get("total_trades", 0) + 1
            log_trade(today_str(), sig.ticker, "MR", "BUY", entry_p, n, trade_cost(entry_p * n), 0, 0, 0, "IBS+RSI entry")
            log_selectie(ticker=sig.ticker, datum=today_str(), strategie="bot_00mr_ibsrsi", beurs=ex_name, koers=entry_p, parameters={"ibs": sig.ibs, "rsi3": sig.rsi3, "ma20": sig.ma20, "ma200": sig.ma200, "atr": sig.atr, "stop": sig.stop, "tp": sig.tp, "rr_pct": sig.rr, "grafiek": yahoo_link(sig.ticker)})
            print(f"  KOOP {sig.ticker}: IBS={sig.ibs:.2f} RSI3={sig.rsi3:.1f} | {n}× EUR{entry_p:.2f}")

    for t, pos in list(portfolio["positions"].items()):
        if (pos.get("systeem") == "MR" and pos.get("days_open", 0) >= CFG["mr_max_hold"]):
            cur   = prices.get(t, pos["entry_price"])
            exit_p = cur * (1 - SLIPPAGE_PCT)
            gross  = exit_p * pos["size"]
            cost   = trade_cost(gross)
            pnl    = gross - cost - (pos["entry_price"] * pos["size"] + pos.get("cost", 0))
            tax    = pnl * CFG["tax_rate"] if pnl > 0 else 0.0
            portfolio["cash"] += gross - cost - tax
            portfolio["daily_pnl"] = portfolio.get("daily_pnl", 0) + pnl - tax
            log_trade(today_str(), t, "MR", "SELL", exit_p, pos["size"], cost, pnl, tax, pnl - tax, f"Max houdduur {CFG['mr_max_hold']}d")
            del portfolio["positions"][t]
            print(f"  TIME EXIT {t}: max houdduur | PnL: EUR{pnl:+.2f}")

    log_snapshot(portfolio, prices)
    save_portfolio(portfolio)

    rapport = rapport_eod(mr_entries, mr_exits, portfolio, prices)
    send_telegram(rapport)
    send_email(f"Multi-Systeem EOD rapport {today_str()}", rapport.replace("*","").replace("`",""))
    print("EOD rapport verzonden.")

# ============================================================
# ORB RUN (INTRADAY)
# ============================================================

def run_orb():
    print(f"{'='*60}\nMULTI-SYSTEEM BOT — ORB  {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}\n{'='*60}")

    portfolio = load_portfolio()
    orb_exchanges = {"048 Nasdaq/NYSE": "tickers_048x.txt", "047 Toronto": "tickers_047x.txt", "045 Londen": "tickers_045x.txt", "041 Benelux": "tickers_041x.txt"}

    all_tickers = []
    for path in orb_exchanges.values():
        all_tickers.extend(load_tickers_from_file(path))
    all_tickers = sorted(set(all_tickers))

    prices: Dict[str, float] = {}
    try:
        data = yf.download(all_tickers[:50], period="1d", auto_adjust=True, progress=False, group_by="ticker")
        if data is not None and not data.empty and isinstance(data.columns, pd.MultiIndex):
            for t in all_tickers[:50]:
                try:
                    c = safe_float(data.xs(t, axis=1, level=1)["Close"].iloc[-1])
                    if not math.isnan(c): prices[t] = c
                except Exception: pass
    except Exception: pass

    if max_daily_loss_bereikt(portfolio, prices):
        print("Max dagverlies bereikt — geen ORB trades")
        return

    orb_signalen: Dict[str, List[ORBSignaal]] = {}
    for ex_name, path in orb_exchanges.items():
        tlist = load_tickers_from_file(path)
        if not tlist: continue
        tlist = tlist[:30]
        print(f"  ORB scan: {ex_name} ({len(tlist)} tickers)...")
        sigs = generate_orb_signals(tlist, ex_name)
        orb_signalen[ex_name] = sigs

        totaal = portfolio_waarde(portfolio, prices)
        budget = risk_budget(portfolio, "orb", prices)

        for sig in [s for s in sigs if s.breakout]:
            if sig.ticker in portfolio["positions"]: continue
            if len(portfolio["positions"]) >= CFG["max_positions"]: break

            entry_p  = sig.current * (1 + SLIPPAGE_PCT)
            n, ml    = bereken_positie(totaal, entry_p, sig.stop)
            if n <= 0: continue
            investering = entry_p * n + trade_cost(entry_p * n)
            if investering > portfolio["cash"] or investering > budget: continue

            portfolio["cash"] -= investering
            portfolio["positions"][sig.ticker] = {
                "entry_price": round(entry_p, 4), "size": n, "stop": sig.stop, "tp": sig.tp,
                "systeem": "ORB", "exchange": ex_name, "days_open": 0, "cost": trade_cost(investering), "intraday": True,
            }
            log_trade(today_str(), sig.ticker, "ORB", "BUY", entry_p, n, trade_cost(investering), 0, 0, 0, "ORB breakout")
            log_selectie(ticker=sig.ticker, datum=today_str(), strategie="bot_00mr_orb", beurs=ex_name, koers=entry_p, parameters={"orb_high": sig.orb_high, "orb_low": sig.orb_low, "orb_range": sig.orb_range, "breakout": sig.breakout, "breakout_vol": sig.vol_ratio, "stop": sig.stop, "tp": sig.tp, "grafiek": yahoo_link(sig.ticker)})
            prices[sig.ticker] = sig.current
            print(f"  ORB KOOP {sig.ticker}: EUR{sig.current:.2f} | vol {sig.vol_ratio:.1f}×")

    save_portfolio(portfolio)
    rapport = rapport_orb(orb_signalen, portfolio, prices)
    send_telegram(rapport)
    send_email(f"ORB rapport {today_str()}", rapport.replace("*","").replace("`",""))
    print("ORB rapport verzonden.")

# ============================================================
# BACKTEST
# ============================================================

def run_backtest():
    print(f"{'='*60}\nMULTI-SYSTEEM BACKTEST — IBS+RSI  {BACKTEST_START} -> {BACKTEST_END}\n{'='*60}")
    print("NB: ORB backtest vereist historische intraday data (niet beschikbaar via yfinance)")

    all_tickers = []
    for f_name in bouw_bestandslijst():
        all_tickers.extend(load_tickers_from_file(f_name))
    all_tickers = sorted(set(all_tickers))

    if not all_tickers:
        print("[ERROR] Geen tickers gevonden.")
        return

    print(f"Tickers: {len(all_tickers)} | Data downloaden (3y)...")
    df = download_eod(all_tickers, period="3y")
    if df.empty:
        print("[ERROR] Geen data.")
        return
    df = add_eod_indicators(df)

    all_dates = sorted(df["Date"].dt.date.unique())
    print(f"Handelsdagen: {len(all_dates)}")

    cash      = CFG["start_capital"]
    positions: Dict[str, Dict] = {}
    trades:    List[Dict] = []
    snapshots: List[Dict] = []

    for date in all_dates:
        day_df = df[df["Date"] == pd.Timestamp(date)].copy()
        prices: Dict[str, float] = {}
        for _, row in day_df.iterrows():
            t = row.get("Ticker")
            c = safe_float(row.get("Close"))
            if t and not math.isnan(c): prices[t] = c

        for pos in positions.values(): pos["days"] = pos.get("days", 0) + 1

        for t, pos in list(positions.items()):
            if t not in prices: continue
            row_s = day_df[day_df["Ticker"] == t]
            if row_s.empty: continue
            row   = row_s.iloc[0]
            close = prices[t]
            ibs   = safe_float(row.get("IBS"))
            ma20  = safe_float(row.get("MA20"))

            reason = None
            if not math.isnan(ibs) and ibs > CFG["ibs_exit"]: reason = f"IBS exit ({ibs:.2f})"
            elif not math.isnan(ma20) and close > ma20: reason = f"MA20 exit ({ma20:.2f})"
            elif pos["days"] >= CFG["mr_max_hold"]: reason = f"Time exit ({pos['days']}d)"
            elif close <= pos["stop"]: reason = f"Stop ({pos['stop']:.2f})"

            if reason:
                exit_p = close * (1 - SLIPPAGE_PCT)
                gross  = exit_p * pos["size"]
                cost   = trade_cost(gross)
                pnl    = gross - cost - (pos["entry_price"] * pos["size"] + pos["cost"])
                tax    = pnl * CFG["tax_rate"] if pnl > 0 else 0.0
                cash  += gross - cost - tax
                trades.append({"entry_date": pos["entry_date"].isoformat(), "exit_date": date.isoformat(), "ticker": t, "entry_price": pos["entry_price"], "exit_price": round(exit_p, 4), "size": pos["size"], "pnl": round(pnl, 2), "tax": round(tax, 2), "net": round(pnl - tax, 2), "reason": reason, "days": pos["days"]})
                del positions[t]

        totaal = cash + sum(prices.get(t, p["entry_price"]) * p["size"] for t, p in positions.items())

        for _, row in day_df.iterrows():
            t   = row.get("Ticker")
            if not t or t in positions: continue
            if len(positions) >= CFG["max_positions"]: break

            close = safe_float(row.get("Close"))
            ibs   = safe_float(row.get("IBS"))
            rsi3  = safe_float(row.get("RSI3"))
            ma200 = safe_float(row.get("MA200"))
            ma20  = safe_float(row.get("MA20"))
            atr   = safe_float(row.get("ATR14"))
            low   = safe_float(row.get("Low"))

            if any(math.isnan(x) for x in [close, ibs, rsi3, ma200, atr]): continue
            if not (ibs < CFG["ibs_entry"] and rsi3 < CFG["rsi_entry"] and close > ma200): continue

            entry_p = close * (1 + SLIPPAGE_PCT)
            stop    = low - 0.5 * atr
            tp      = ma20
            n, _    = bereken_positie(totaal, entry_p, stop)
            if n <= 0: continue
            investering = entry_p * n + trade_cost(entry_p * n)
            if investering > cash: continue

            cash -= investering
            positions[t] = {"entry_date": date, "entry_price": round(entry_p, 4), "size": n, "stop": round(stop, 4), "tp": round(tp, 4) if not math.isnan(tp) else round(entry_p * 1.05, 4), "cost": trade_cost(investering), "days": 0}

        totaal = cash + sum(prices.get(t, p["entry_price"]) * p["size"] for t, p in positions.items())
        snapshots.append({"date": date.isoformat(), "total": round(totaal, 2)})

    if trades:
        tdf = pd.DataFrame(trades)
        tdf.to_csv("mr_backtest_trades.csv", index=False)
        n    = len(tdf)
        nwin = (tdf["net"] > 0).sum()
        pf   = abs(tdf.loc[tdf["net"] > 0, "net"].sum()) / max(abs(tdf.loc[tdf["net"] <= 0, "net"].sum()), 1e-9)
        snap = pd.DataFrame(snapshots)
        start = CFG["start_capital"]
        end   = snap["total"].iloc[-1]
        years = max(len(snap) / 252, 0.1)
        cagr  = ((end / start) ** (1 / years) - 1) * 100

        print(f"\n{'='*60}")
        print(f"BACKTEST RESULTATEN — IBS+RSI Mean Reversion")
        print(f"{'='*60}")
        print(f"Startkapitaal    : EUR{start:>12,.2f}")
        print(f"Eindkapitaal     : EUR{end:>12,.2f}")
        print(f"Totaal rendement : {(end-start)/start*100:>+.1f}%")
        print(f"CAGR             : {cagr:>+.1f}%")
        print(f"Trades           : {n} | Winnaars: {nwin} ({nwin/n*100:.1f}%)")
        print(f"Profit Factor    : {pf:.2f}")
        print(f"Gem. houdduur    : {tdf['days'].mean():.1f} dagen")
        print(f"Belasting betaald: EUR{tdf['tax'].sum():,.2f}")
        print(f"{'='*60}")
    else:
        print("Geen trades gegenereerd.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "eod"
    if mode == "eod": run_eod()
    elif mode == "orb": run_orb()
    elif mode == "backtest": run_backtest()
    else: print(f"Onbekende modus: {mode}\nGebruik: python bot_00mr.py [eod|orb|backtest]")
