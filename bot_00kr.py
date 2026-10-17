#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00kr.py — KRITISCHE SELECTIE ENGINE v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Alle indicatoren (RSI, MACD, ATR) komen uit common.indicators, zodat
de waarden gegarandeerd gelijk zijn aan bouw_generieke_technicals.

Criteria (score 0-5):
  1. RSI maandelijks — oververkocht (<40) of momentum hervatting (>55)
  2. MACD maandelijks — bullish crossover of histogram groeit
  3. Risk/Reward >=25% — opwaarts naar jaarweerstand vs. 2xATR stop
  4. Support nabijheid — prijs binnen 3% van 52-weekse bodem
  5. Dividend yield   — yield > 0% (bonus)

Gebruik:
  python bot_00kr.py live
  python bot_00kr.py backtest
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
    bereken_atr_positie, trade_cost,
    SLIPPAGE_PCT, RISICO_PCT_DEFAULT,
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

START_CAPITAL = 50_000.0
ATR_STOP_MULT = 2.0
MAX_HOLD_DAYS = 60

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
        f"RSI maandelijks | Risico {RISICO_PCT_DEFAULT*100:.0f}% portfolio_"
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
# BACKTEST ENGINE (optioneel, voor validatie)
# ============================================================

def run_backtest():
    print(f"{'='*60}")
    print(f"KRITISCHE SELECTIE BACKTEST  (via common-modules)")
    print(f"{'='*60}")
    print("Backtest is in deze refactor NIET meegenomen (vereist aparte")
    print("state-management). Focus van Fase 2 is de LIVE-pipeline.")
    print("De originele backtest staat in Fantasie/bot_00kr.py ter referentie.")


# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
