#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00kr.py — KRITISCHE SELECTIE ENGINE v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Alle indicatoren (RSI, MACD, ATR) komen uit common.indicators.
Backtest-functionaliteit is volledig behouden.
"""

import sys
import math
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import pandas as pd

from common import (
    today_str, safe_float, yahoo_link, esc,
    download_history, load_tickers_from_file, bouw_bestandslijst, label_voor,
    compute_rsi_series, compute_macd_series, compute_atr_series,
    send_telegram, send_email,
    bereken_positie, bereken_atr_positie, trade_cost,
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
SLIPPAGE_PCT         = 0.001
TRADE_COST_FIXED     = 15.0
TRADE_COST_PCT       = 0.0035
TAX_RATE             = 0.10
MAX_HOLD_DAYS        = 60

KS_CFG = {
    "rsi_period": 14,
    "rsi_oversold": 40.0,
    "rsi_momentum": 55.0,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "min_rr_pct": 25.0,
    "atr_stop_mult": 2.0,
    "atr_period": 14,
    "support_lookback": 252,
    "support_proximity": 3.0,
    "resistance_lookback": 252,
    "min_score": 3,
}

BACKTEST_START = "2021-01-01"
BACKTEST_END   = today_str()

# ============================================================
# SIGNAAL
# ============================================================

@dataclass
class KSSignaal:
    ticker: str
    price: float
    score: int
    atr: float
    resistance: float
    stop: float
    rr_pct: float
    support: float
    rsi_monthly: float
    rsi_label: str
    macd_label: str
    rr_label: str
    support_label: str
    div_label: str
    div_yield: float
    tp_pct: float


def analyse_ticker(ticker: str, g: pd.DataFrame) -> Optional[KSSignaal]:
    try:
        g = g.sort_values("Date").copy()
        if len(g) < 60:
            return None

        close = g["Close"]
        high = g["High"]
        low = g["Low"]

        current_price = safe_float(close.iloc[-1])
        if current_price <= 0 or math.isnan(current_price):
            return None

        atr_s = compute_atr_series(high, low, close, KS_CFG["atr_period"])
        atr_val = safe_float(atr_s.iloc[-1])
        if math.isnan(atr_val) or atr_val <= 0:
            atr_val = current_price * 0.02

        score = 0

        # Maandelijkse RSI — resample naar maand-einde
        g_indexed = g.set_index("Date")
        monthly = g_indexed["Close"].resample("ME").last().dropna()

        rsi_label = "neutraal"
        rsi_val = 50.0
        if len(monthly) >= KS_CFG["rsi_period"] + 2:
            rsi_m = compute_rsi_series(monthly, KS_CFG["rsi_period"])
            rsi_val = safe_float(rsi_m.iloc[-1], 50.0)
            rsi_prv = safe_float(rsi_m.iloc[-2], 50.0)
            if rsi_val < KS_CFG["rsi_oversold"]:
                score += 1
                rsi_label = f"✓ oververkocht ({rsi_val:.1f})"
            elif rsi_val > KS_CFG["rsi_momentum"] and rsi_prv <= KS_CFG["rsi_momentum"]:
                score += 1
                rsi_label = f"✓ momentum ({rsi_val:.1f} up)"
            elif KS_CFG["rsi_oversold"] <= rsi_val <= 52 and rsi_val > rsi_prv:
                score += 1
                rsi_label = f"✓ herstel ({rsi_val:.1f} up)"
            else:
                rsi_label = f"✗ neutraal ({rsi_val:.1f})"
        else:
            rsi_label = "✗ onvoldoende data"

        # Maandelijkse MACD
        macd_label = "✗ neutraal"
        if len(monthly) >= KS_CFG["macd_slow"] + KS_CFG["macd_signal"] + 2:
            macd_line, sig_line, hist = compute_macd_series(
                monthly, KS_CFG["macd_fast"], KS_CFG["macd_slow"], KS_CFG["macd_signal"]
            )
            m_now = safe_float(macd_line.iloc[-1])
            m_prv = safe_float(macd_line.iloc[-2])
            s_now = safe_float(sig_line.iloc[-1])
            s_prv = safe_float(sig_line.iloc[-2])
            h_now = safe_float(hist.iloc[-1])
            h_prv = safe_float(hist.iloc[-2])
            if (m_prv < s_prv) and (m_now >= s_now):
                score += 1
                macd_label = "✓ bullish crossover"
            elif h_now > h_prv and h_now > 0:
                score += 1
                macd_label = f"✓ histogram up ({h_now:.3f})"
            else:
                macd_label = f"✗ neutraal ({m_now:.3f})"

        # Risk/Reward
        lb = min(KS_CFG["resistance_lookback"], len(close))
        resistance = float(close.iloc[-lb:].max())
        stop_price = current_price - KS_CFG["atr_stop_mult"] * atr_val
        rr_pct = 0.0
        rr_label = "✗ onvoldoende"
        if resistance > current_price and stop_price > 0:
            upside = (resistance - current_price) / current_price * 100
            downside = (current_price - stop_price) / current_price * 100
            if downside > 0:
                ratio = upside / downside
                rr_pct = upside
                if upside >= KS_CFG["min_rr_pct"] and ratio >= 1.5:
                    score += 1
                    rr_label = f"✓ {upside:.1f}% opwaarts (ratio {ratio:.1f}:1)"
                else:
                    rr_label = f"✗ {upside:.1f}% / ratio {ratio:.1f}:1"

        tp_pct = rr_pct / 100.0

        # Support nabijheid
        lb_sup = min(KS_CFG["support_lookback"], len(close))
        support_level = float(close.iloc[-lb_sup:].min())
        dist_pct = ((current_price - support_level) / support_level) * 100
        if dist_pct <= KS_CFG["support_proximity"]:
            score += 1
            support_label = f"✓ {dist_pct:.1f}% boven bodem ({support_level:.2f})"
        else:
            support_label = f"✗ {dist_pct:.1f}% boven bodem ({support_level:.2f})"

        # Dividend
        div_yield = 0.0
        div_label = "✗ geen dividend"
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info
            div_yield = (info.get("dividendYield") or 0.0) * 100
            if div_yield > 0:
                score += 1
                div_label = f"✓ {div_yield:.2f}%"
        except Exception:
            pass

        return KSSignaal(
            ticker=ticker, price=round(current_price, 2), score=score,
            atr=round(atr_val, 4), resistance=round(resistance, 2),
            stop=round(stop_price, 2), rr_pct=round(rr_pct, 1),
            support=round(support_level, 2), rsi_monthly=round(rsi_val, 1),
            rsi_label=rsi_label, macd_label=macd_label,
            rr_label=rr_label, support_label=support_label,
            div_label=div_label, div_yield=round(div_yield, 2),
            tp_pct=max(tp_pct, 0.25),
        )
    except Exception as e:
        print(f"[WARN] {ticker}: fout — {e}")
        return None


# ============================================================
# OUTPUT
# ============================================================

def _score_bar(score: int) -> str:
    return "█" * score + "░" * (5 - score) + f" {score}/5"


def format_bericht(
    exchange_name: str,
    signalen: List[KSSignaal],
    alle: List[KSSignaal],
    portfolio_waarde: float,
) -> Optional[str]:
    if not alle:
        return None

    nu = today_str()
    top2 = sorted(alle, key=lambda s: (s.score * 10 + s.rr_pct), reverse=True)[:2]
    max_sc = max((s.score for s in signalen), default=0) if signalen else 0
    toon = [s for s in signalen if s.score == max_sc] if signalen else []
    lbl = {5: "⭐ PERFECTE SCORE (5/5)", 4: "🟡 STERK (4/5)",
           3: "🟠 WATCHLIST (3/5)"}.get(max_sc, "📊")

    def sig_regel(s: KSSignaal) -> str:
        entry = s.price * (1 + SLIPPAGE_PCT)
        aandelen, stop, max_loss = bereken_atr_positie(
            portfolio_waarde, entry, s.atr, KS_CFG["atr_stop_mult"]
        )
        investering = round(entry * aandelen, 2)
        kosten = round(trade_cost(investering), 2)
        return (
            f"• `{s.ticker}` {_score_bar(s.score)} | EUR{s.price:.2f} | "
            f"RR:{s.rr_pct:.1f}% | {yahoo_link(s.ticker)}\n"
            f"  Entry: EUR{entry:.2f} | Stop: EUR{stop:.2f} | {aandelen} stuks\n"
            f"  Investering: EUR{investering:,.2f} | Max verlies: EUR{max_loss:,.2f} | Kosten: EUR{kosten:.2f}"
        )

    delen = [
        f"🔍 *KRITISCHE SELECTIE — {esc(exchange_name)}*",
        f"_{nu} | {len(alle)} geanalyseerd | {len(signalen)} kandidaten (score>={KS_CFG['min_score']})_",
        "─────────────────────────────",
        f"🏆 *TOP 2 HOOGSTE POTENTIEEL:*",
        "\n\n".join(sig_regel(s) for s in top2),
    ]

    if toon:
        delen += ["─────────────────────────────", f"*{lbl}:*"]
        for s in [x for x in toon if x not in top2]:
            delen.append(
                f"• `{s.ticker}` {_score_bar(s.score)} | EUR{s.price:.2f} | "
                f"RR:{s.rr_pct:.1f}% | RSI:{s.rsi_monthly:.1f}\n"
                f"  {s.rsi_label} | {s.macd_label}"
            )

    delen.append(
        f"⚙️ _Stop 2×ATR | RR>={KS_CFG['min_rr_pct']:.0f}% | "
        f"RSI maandelijks | Risico {RISICO_PCT_PER_TRADE*100:.0f}% portfolio_"
    )
    return "\n\n".join(delen)


# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    print(f"{'='*60}")
    print(f"KRITISCHE SELECTIE — LIVE  {today_str()}")
    print(f"{'='*60}")

    exchange_tickers: Dict[str, List[str]] = {}
    all_tickers: List[str] = []

    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist:
            print(f"Bestand {f_name} niet gevonden of leeg, overslaan.")
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
    print("Data downloaden (3 jaar)...")
    df = download_history(all_tickers, period="3y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    print(f"Data geladen: {df['Ticker'].nunique()} tickers, {len(df)} rijen")
    portfolio_waarde = START_CAPITAL
    email_delen: List[str] = []

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")
        df_ex = df[df["Ticker"].isin(tlist)].copy()

        alle: List[KSSignaal] = []
        for ticker, group in df_ex.groupby("Ticker", sort=False):
            sig = analyse_ticker(ticker, group)
            if sig is not None:
                alle.append(sig)
                if sig.score >= KS_CFG["min_score"]:
                    print(f"  ✓ {ticker}: score {sig.score}/5 | RSI={sig.rsi_monthly:.1f} | RR={sig.rr_pct:.1f}%")

        signalen = [s for s in alle if s.score >= KS_CFG["min_score"]]
        signalen.sort(key=lambda s: (s.score, s.rr_pct), reverse=True)
        alle.sort(key=lambda s: (s.score * 10 + s.rr_pct), reverse=True)

        print(f"  → {len(signalen)} kandidaten (score >= {KS_CFG['min_score']}) | top-2 uit {len(alle)} geanalyseerd")

        for s in signalen:
            log_selectie(
                ticker=s.ticker,
                datum=today_str(),
                strategie="bot_00kr",
                beurs=ex_name,
                koers=s.price,
                parameters={
                    "score": s.score,
                    "rsi_monthly": s.rsi_monthly,
                    "rsi_label": s.rsi_label,
                    "macd_label": s.macd_label,
                    "rr_pct": s.rr_pct,
                    "resistance": s.resistance,
                    "stop": s.stop,
                    "support": s.support,
                    "div_yield": s.div_yield,
                    "atr": s.atr,
                    "grafiek": yahoo_link(s.ticker),
                },
            )

        bericht = format_bericht(ex_name, signalen, alle, portfolio_waarde)
        if bericht:
            send_telegram(bericht, parse_mode="Markdown")
            email_delen.append(bericht)
            print(f"  → Telegram verstuurd")
        else:
            print(f"  → Overgeslagen: {ex_name}")

    if email_delen:
        send_email(
            f"Kritische Selectie rapport {today_str()}",
            "\n\n" + ("=" * 40 + "\n\n").join(email_delen),
        )

    print(f"\n{'='*60}")
    print("Klaar.")


# ============================================================
# BACKTEST ENGINE
# ============================================================

@dataclass
class BTPosition:
    ticker:      str
    strategy:    str
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
    print(f"KRITISCHE SELECTIE BACKTEST  {BACKTEST_START} -> {BACKTEST_END}")
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
            close  = price_map[ticker]
            reason = None
            if pos.sl is not None and close <= pos.sl:
                reason = f"SL ({pos.sl:.2f})"
            elif pos.tp is not None and close >= pos.tp:
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
                    "entry_date":  pos.entry_date.isoformat(),
                    "exit_date":   scan_date.isoformat(),
                    "ticker":      ticker,
                    "score":       pos.strategy,
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
            sig = analyse_ticker(ticker, group)
            if not sig or sig.score < KS_CFG["min_score"]:
                continue
            
            entry = sig.price * (1 + SLIPPAGE_PCT)
            total_waarde = cash + sum(price_map.get(t, p.entry_price) * p.size for t, p in positions.items())
            
            # Gebruik de ATR sizing methode
            aandelen, stop_loss, _ = bereken_atr_positie(total_waarde, entry, sig.atr, KS_CFG["atr_stop_mult"])
            
            if aandelen <= 0:
                continue
            investering = entry * aandelen + trade_cost(entry * aandelen)
            if investering > cash:
                continue
            cash -= investering
            
            # Bepaal target price (bijv. resistance of entry + tp_pct)
            tp_price = entry * (1 + sig.tp_pct) if sig.tp_pct > 0 else sig.resistance
            
            positions[ticker] = BTPosition(
                ticker=ticker, strategy=str(sig.score),
                entry_date=scan_date, entry_price=round(entry, 4),
                size=aandelen, cost=trade_cost(investering),
                sl=stop_loss, tp=tp_price,
                atr=sig.atr, days=0
            )

    if trades:
        tdf  = pd.DataFrame(trades)
        tdf.to_csv("kr_backtest_trades.csv", index=False)
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
        print("Opgeslagen: kr_backtest_trades.csv")
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
