#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00xxxV2.py — GLOBAL ENGINE v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Bevat de ATR-sizing, de LivePortfolio state en de CSV-logging.
"""

import os
import sys
import math
import csv
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
    wilder_smooth, compute_rsi_series, compute_atr_series, compute_ibs, compute_adx_series,
    send_telegram, send_email,
    bereken_atr_positie, trade_cost, sizing_tekst,
    SLIPPAGE_PCT, RISICO_PCT_DEFAULT, TRADE_COST_FIXED, TRADE_COST_PCT
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
MAX_POSITIONS        = 10
MIN_CASH_RATIO       = 0.10
RISICO_PCT_PER_TRADE = 0.05
ATR_STOP_MULT        = 2.0
TAX_RATE             = 0.10
MAX_HOLD_DAYS        = 20

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

LIVE_TRADES_FILE    = "trades_live.csv"
LIVE_POSITIONS_FILE = "positions_live.csv"
LIVE_PORTFOLIO_FILE = "portfolio_live.csv"

STRAT_CONFIG = {
    "Traag":       {"sl_mult": 2.0, "tp_mult": 4.0},
    "Snel":        {"sl_mult": 2.0, "tp_mult": 3.0},
    "Hyper Trend": {"sl_mult": 2.5, "tp_mult": 5.0},
    "Hyper Scalp": {"sl_mult": 1.5, "tp_mult": 2.5},
    "MRA Snel":    {"sl_mult": 2.0, "tp_mult": 3.0},
    "MRA Traag":   {"sl_mult": 2.5, "tp_mult": 4.0},
}

# ============================================================
# INDICATOREN
# ============================================================

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for ticker, group in df.groupby("Ticker", sort=False):
        g     = group.copy()
        close = g["Close"]
        high  = g["High"]
        low   = g["Low"]

        g["MA20"]  = close.rolling(20).mean()
        g["MA50"]  = close.rolling(50).mean()
        g["MA200"] = close.rolling(200).mean()

        g["RSI14"] = compute_rsi_series(close, 14)
        g["ATR14"] = compute_atr_series(high, low, close, 14)
        g["IBS"]   = compute_ibs(high, low, close)
        g["ADX14"] = compute_adx_series(high, low, close, 14)

        g["Ticker"] = ticker
        parts.append(g)

    if not parts:
        return df
    return pd.concat(parts).sort_values(["Ticker", "Date"]).reset_index(drop=True)

# ============================================================
# SIGNALEN
# ============================================================

@dataclass
class Signal:
    ticker:    str
    date:      dt.date
    strategy:  str
    direction: str
    reason:    str
    price:     float
    atr:       float
    sl:        Optional[float] = None
    tp:        Optional[float] = None
    rr_ratio:  float = 0.0
    next_open: Optional[float] = None

def _calc_rr(price, sl, tp) -> float:
    if sl is None or tp is None:
        return 0.0
    risk   = abs(price - sl)
    reward = abs(tp - price)
    return (reward / risk) if risk > 1e-9 else 0.0

def generate_signals_for_day(df: pd.DataFrame, date: dt.date) -> List[Signal]:
    signals: List[Signal] = []
    day_df = df[df["Date"] == pd.Timestamp(date)].copy()
    if day_df.empty:
        return signals

    for _, row in day_df.iterrows():
        t     = row.get("Ticker")
        if not t:
            continue
        close     = safe_float(row.get("Close"))
        ma20      = safe_float(row.get("MA20"))
        ma50      = safe_float(row.get("MA50"))
        ma200     = safe_float(row.get("MA200"))
        rsi       = safe_float(row.get("RSI14"))
        ibs       = safe_float(row.get("IBS"))
        atr       = safe_float(row.get("ATR14"))
        adx       = safe_float(row.get("ADX14"))
        next_open = safe_float(row.get("Next_Open"), default=close)

        if math.isnan(close) or close <= 0 or math.isnan(atr) or atr <= 0:
            continue

        def make(strategy: str, reason: str) -> Signal:
            cfg = STRAT_CONFIG.get(strategy, {"sl_mult": 2.0, "tp_mult": 3.0})
            sl  = close - cfg["sl_mult"] * atr
            tp  = close + cfg["tp_mult"] * atr
            return Signal(ticker=t, date=date, strategy=strategy, direction="BUY",
                          reason=reason, price=close, atr=atr, sl=sl, tp=tp,
                          rr_ratio=_calc_rr(close, sl, tp), next_open=next_open)

        if not (math.isnan(ma50) or math.isnan(ma200) or math.isnan(adx)):
            if ma50 > ma200 and close > ma50 and adx > 15:
                signals.append(make("Traag", "MA50>MA200 & Close>MA50 & ADX>15"))

        if not (math.isnan(ma20) or math.isnan(ma50) or math.isnan(adx)):
            if ma20 > ma50 and close > ma20 and adx > 15:
                signals.append(make("Snel", "MA20>MA50 & Close>MA20 & ADX>15"))

        if not (math.isnan(ma50) or math.isnan(ma200) or math.isnan(adx) or math.isnan(rsi)):
            if ma50 > ma200 and close > ma50 and adx > 20 and rsi > 55:
                signals.append(make("Hyper Trend", "ADX>20 & RSI>55 & MA50>MA200"))

        if not (math.isnan(rsi) or math.isnan(ibs)):
            if rsi < 30 and ibs < 0.2:
                signals.append(make("Hyper Scalp", "RSI<30 & IBS<0.2"))
            if rsi < 35 and ibs < 0.3:
                signals.append(make("MRA Snel", "RSI<35 & IBS<0.3"))
            if rsi < 40 and ibs < 0.4:
                signals.append(make("MRA Traag", "RSI<40 & IBS<0.4"))

    signals.sort(key=lambda s: s.rr_ratio, reverse=True)
    return signals

# ============================================================
# LIVE PORTEFEUILLE
# ============================================================

@dataclass
class LivePosition:
    ticker:      str
    strategy:    str
    entry_date:  str
    entry_price: float
    size:        int
    cost:        float
    sl:          Optional[float]
    tp:          Optional[float]
    atr:         float = 0.0
    days_open:   int   = 0

class LivePortfolio:
    def __init__(self, start_capital: float):
        self.cash       = start_capital
        self.positions: Dict[str, LivePosition] = {}
        self.load_state()

    def load_state(self):
        if os.path.exists(LIVE_PORTFOLIO_FILE):
            df = pd.read_csv(LIVE_PORTFOLIO_FILE)
            if not df.empty:
                self.cash = float(df.iloc[-1]["cash"])
        if os.path.exists(LIVE_POSITIONS_FILE):
            dfp = pd.read_csv(LIVE_POSITIONS_FILE)
            for _, r in dfp.iterrows():
                self.positions[r["ticker"]] = LivePosition(
                    ticker=r["ticker"], strategy=r["strategy"],
                    entry_date=r["entry_date"], entry_price=float(r["entry_price"]),
                    size=int(r["size"]), cost=float(r["cost"]),
                    sl=float(r["sl"]) if not pd.isna(r["sl"]) else None,
                    tp=float(r["tp"]) if not pd.isna(r["tp"]) else None,
                    atr=float(r.get("atr", 0.0)), days_open=int(r.get("days_open", 0)),
                )

    def save_state(self, date: str, prices: Dict[str, float]):
        ensure_csv_header(LIVE_POSITIONS_FILE,
            ["ticker","strategy","entry_date","entry_price","size","cost","sl","tp","atr","days_open"])
        with open(LIVE_POSITIONS_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ticker","strategy","entry_date","entry_price","size","cost","sl","tp","atr","days_open"])
            for p in self.positions.values():
                w.writerow([p.ticker, p.strategy, p.entry_date, p.entry_price,
                            p.size, p.cost,
                            p.sl if p.sl is not None else "",
                            p.tp if p.tp is not None else "",
                            p.atr, p.days_open])
        ensure_csv_header(LIVE_PORTFOLIO_FILE, ["date","cash","positions_value","total"])
        pos_val = sum(prices.get(t, p.entry_price) * p.size for t, p in self.positions.items())
        with open(LIVE_PORTFOLIO_FILE, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([date, self.cash, pos_val, self.cash + pos_val])

    def current_total_value(self, prices: Dict[str, float]) -> float:
        return self.cash + sum(prices.get(t, p.entry_price) * p.size for t, p in self.positions.items())

    def can_open_new_position(self, prices: Dict[str, float], investering: float) -> bool:
        if len(self.positions) >= MAX_POSITIONS:
            return False
        total = self.current_total_value(prices)
        return (self.cash - investering) >= total * MIN_CASH_RATIO and investering <= self.cash

    def open_position(self, sig: Signal, prices: Dict[str, float]) -> Optional[LivePosition]:
        if sig.ticker in self.positions:
            return None
        cfg         = STRAT_CONFIG.get(sig.strategy, {"sl_mult": 2.0, "tp_mult": 3.0})
        entry       = (sig.next_open or sig.price) * (1 + SLIPPAGE_PCT)
        aandelen, stop, _ = bereken_atr_positie(self.current_total_value(prices), entry, sig.atr, cfg["sl_mult"])
        if aandelen <= 0:
            return None
        investering = entry * aandelen
        cost        = trade_cost(investering)
        if not self.can_open_new_position(prices, investering + cost):
            return None
        self.cash -= investering + cost
        p = LivePosition(ticker=sig.ticker, strategy=sig.strategy,
                         entry_date=sig.date.isoformat(), entry_price=round(entry, 4),
                         size=aandelen, cost=cost,
                         sl=round(stop, 4), tp=round(entry + cfg["tp_mult"] * sig.atr, 4),
                         atr=sig.atr, days_open=0)
        self.positions[sig.ticker] = p
        self.log_trade(sig.date.isoformat(), sig.ticker, sig.strategy,
                       "BUY", entry, aandelen, cost, 0.0, 0.0, 0.0)
        return p

    def close_position(self, ticker: str, date: str, exit_price: float, reason: str):
        if ticker not in self.positions:
            return
        p          = self.positions[ticker]
        exit_slip  = exit_price * (1 - SLIPPAGE_PCT)
        gross      = exit_slip * p.size
        cost       = trade_cost(gross)
        pnl        = gross - cost - (p.entry_price * p.size + p.cost)
        tax        = pnl * TAX_RATE if pnl > 0 else 0.0
        self.cash += gross - cost - tax
        self.log_trade(date, ticker, p.strategy, "SELL",
                       exit_slip, p.size, cost, pnl, tax, pnl - tax, reason)
        del self.positions[ticker]

    def log_trade(self, date, ticker, strategy, side, price, size, cost, pnl, tax, net, reason=""):
        ensure_csv_header(LIVE_TRADES_FILE,
            ["date","ticker","strategy","side","price","size","cost","pnl","tax","net","reason"])
        with open(LIVE_TRADES_FILE, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([date, ticker, strategy, side, price, size, cost, pnl, tax, net, reason])

def ensure_csv_header(path: str, header: List[str]) -> None:
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(header)

# ============================================================
# EXIT ENGINE
# ============================================================

def generate_exit_signals(portfolio: LivePortfolio, df: pd.DataFrame, date: dt.date) -> List[Signal]:
    signals: List[Signal] = []
    day_df = df[df["Date"] == pd.Timestamp(date)].copy()
    if day_df.empty:
        return signals

    day_map: Dict = {}
    for _, row in day_df.iterrows():
        t = row.get("Ticker")
        if t:
            day_map[t] = row

    for t, p in list(portfolio.positions.items()):
        if t not in day_map:
            continue
        row   = day_map[t]
        close = safe_float(row.get("Close"))
        ma20  = safe_float(row.get("MA20"))
        rsi   = safe_float(row.get("RSI14"))
        if math.isnan(close):
            continue

        reason: Optional[str] = None
        if p.sl is not None and close <= p.sl:
            reason = f"Stoploss geraakt (SL={p.sl:.2f})"
        elif p.tp is not None and close >= p.tp:
            reason = f"Take Profit geraakt (TP={p.tp:.2f})"
        elif not math.isnan(ma20) and close < ma20 and p.strategy in ("Traag", "Snel", "Hyper Trend"):
            reason = f"Trend exit: Close<MA20 ({close:.2f}<{ma20:.2f})"
        elif not math.isnan(rsi) and rsi > 70 and p.strategy in ("MRA Snel", "MRA Traag", "Hyper Scalp"):
            reason = f"RSI exit: RSI>70 ({rsi:.1f})"
        elif p.days_open >= MAX_HOLD_DAYS:
            reason = f"Time exit: {p.days_open} dagen open"

        if reason:
            signals.append(Signal(ticker=t, date=date, strategy=p.strategy,
                                  direction="SELL", reason=reason, price=close, atr=p.atr))
    return signals

# ============================================================
# OUTPUT
# ============================================================

def _strat_emoji(strategy: str) -> str:
    return {"Traag":"🐢","Snel":"⚡","Hyper Trend":"🚀","Hyper Scalp":"🔥","MRA Snel":"🛡️","MRA Traag":"🐢"}.get(strategy,"📊")

def format_signals_per_exchange(
    exchange_name: str, buy_signals: List[Signal], sell_signals: List[Signal],
    portfolio: LivePortfolio, portfolio_waarde: float,
) -> Tuple[str, str]:
    nu = today_str()

    def koop_blok(signals: List[Signal]) -> str:
        if not signals:
            return "_Geen signalen_"
        lines = []
        for s in signals:
            cfg = STRAT_CONFIG.get(s.strategy, {"sl_mult": 2.0, "tp_mult": 3.0})
            lines.append(
                f"• `{s.ticker}` {_strat_emoji(s.strategy)} *{s.strategy}*"
                f" | EUR{s.price:.2f} | R/R:{s.rr_ratio:.1f} | {yahoo_link(s.ticker)}\n"
                + sizing_tekst(s.price, s.sl, s.tp, portfolio_waarde)
            )
        return "\n\n".join(lines)

    def verkoop_blok(signals: List[Signal]) -> str:
        if not signals:
            return "_Geen signalen_"
        lines = []
        for s in signals:
            pos  = portfolio.positions.get(s.ticker)
            size = pos.size if pos else 0
            pnl_str = f" | PnL est: EUR{(s.price*(1-SLIPPAGE_PCT)-pos.entry_price)*size:+.2f}" if pos else ""
            lines.append(
                f"• `{s.ticker}` {_strat_emoji(s.strategy)} *VERKOOP*\n"
                f"  Reden: {s.reason} | Prijs: EUR{s.price:.2f}{pnl_str}\n"
                f"  Commando: `/sell {s.ticker} {s.price:.2f} {size}`"
            )
        return "\n\n".join(lines)

    deel1 = "\n\n".join([
        f"📊 *{exchange_name}*",
        f"_{nu} | Portfolio: EUR{portfolio_waarde:,.0f} | Risico: 5%/trade_",
        "─────────────────────────────",
        "🐢 *TRAAG (50/200):*",  koop_blok([s for s in buy_signals if s.strategy=="Traag"]),
        "⚡ *SNEL (20/50):*",    koop_blok([s for s in buy_signals if s.strategy=="Snel"]),
        "🔴 *VERKOOPSIGNALEN:*", verkoop_blok(sell_signals),
    ])

    deel2_parts = [
        f"📊 *{exchange_name} (2/2)*", "",
        "🚀 *HYPER TREND:*",  koop_blok([s for s in buy_signals if s.strategy=="Hyper Trend"]), "",
        "🔥 *HYPER SCALP:*",  koop_blok([s for s in buy_signals if s.strategy=="Hyper Scalp"]), "",
        "🛡️ *MRA SNEL:*",     koop_blok([s for s in buy_signals if s.strategy=="MRA Snel"]),   "",
        "🐢 *MRA TRAAG:*",    koop_blok([s for s in buy_signals if s.strategy=="MRA Traag"]),  "",
        f"💼 *PORTFOLIO:* {len(portfolio.positions)}/{MAX_POSITIONS} posities | Cash: EUR{portfolio.cash:,.2f}",
    ]
    for t, pos in portfolio.positions.items():
        deel2_parts.append(
            f"  • `{t}`: {pos.size}x @ EUR{pos.entry_price:.2f} "
            f"({pos.strategy}, {pos.days_open}d | SL:{pos.sl} TP:{pos.tp})"
        )
    deel2_parts += ["", "⚙️ *PARAMETERS:*", f"_ATR-sizing: 5% risico/trade | Slippage: 0.1%_", f"_Entry: open T+1 | SL: 2×ATR | TP: 3-5×ATR | Time-exit: {MAX_HOLD_DAYS}d_"]
    return deel1, "\n".join(deel2_parts)

# ============================================================
# MAIN: LIVE ENGINE
# ============================================================

def run_live_engine():
    all_tickers: List[str] = []
    exchange_tickers: Dict[str, List[str]] = {}
    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist:
            continue
        ex_name = label_voor(f_name)
        exchange_tickers[ex_name] = tlist
        all_tickers.extend(tlist)
    all_tickers = sorted(set(all_tickers))

    df = download_history(all_tickers, period="5y")
    if df.empty:
        print("[ERROR] Geen data. Bot gestopt.")
        return
    df["Next_Open"] = df.groupby("Ticker")["Open"].shift(-1)
    df = add_indicators(df)

    last_date = df["Date"].max().date()
    portfolio = LivePortfolio(START_CAPITAL)
    day_df    = df[df["Date"] == pd.Timestamp(last_date)].copy()

    price_map: Dict[str, float] = {}
    for _, row in day_df.iterrows():
        t = row.get("Ticker")
        c = safe_float(row.get("Close"))
        if t and not math.isnan(c):
            price_map[t] = c

    for p in portfolio.positions.values():
        p.days_open += 1

    portfolio_waarde = portfolio.current_total_value(price_map)
    exit_signals_all = generate_exit_signals(portfolio, df, last_date)

    for ex_name, tlist in exchange_tickers.items():
        if not tlist:
            continue
        df_ex       = df[df["Ticker"].isin(tlist)].copy()
        buy_signals = generate_signals_for_day(df_ex, last_date)

        filtered_buys: List[Signal] = []
        for sig in buy_signals:
            cfg   = STRAT_CONFIG.get(sig.strategy, {"sl_mult": 2.0, "tp_mult": 3.0})
            entry = (sig.next_open or sig.price) * (1 + SLIPPAGE_PCT)
            n, _, _ = bereken_atr_positie(portfolio_waarde, entry, sig.atr, cfg["sl_mult"])
            if portfolio.can_open_new_position(price_map, entry * n + trade_cost(entry * n)):
                filtered_buys.append(sig)
                if len(filtered_buys) + len(portfolio.positions) >= MAX_POSITIONS:
                    break

        for rank, sig in enumerate(filtered_buys, start=1):
            log_selectie(
                ticker=sig.ticker, datum=today_str(), strategie="bot_00xxxV2", beurs=ex_name, koers=sig.price,
                parameters={
                    "rank": rank, "substrategie": sig.strategy, "richting": sig.direction, "reden": sig.reason,
                    "atr": sig.atr, "stop": sig.sl, "rr_pct": sig.rr_ratio, "grafiek": yahoo_link(sig.ticker),
                },
            )

        exit_ex = [s for s in exit_signals_all if s.ticker in tlist]
        deel1, deel2 = format_signals_per_exchange(ex_name, filtered_buys, exit_ex, portfolio, portfolio_waarde)
        send_telegram(deel1, parse_mode="Markdown")
        time.sleep(1)
        send_telegram(deel2, parse_mode="Markdown")

    portfolio.save_state(last_date.isoformat(), price_map)

def run_backtest():
    print("Backtest-functionaliteit is niet aanwezig in de originele bot_00xxxV2.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
