#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01marktsent.py — MARKTSENTIMENT ENGINE v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Combineert technische indicatoren met VADER-nieuwssentiment.
Let op: backtest test enkel de technische component (NewsAPI biedt geen
historisch archief op de gratis tier).
"""

import sys
import math
import time
import warnings
import datetime as dt
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from common import (
    today_str, safe_float, yahoo_link,
    download_history, load_tickers_from_file, bouw_bestandslijst, label_voor,
    compute_rsi_series, compute_macd_series, compute_atr_series,
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

warnings.filterwarnings("ignore", category=FutureWarning)

# ============================================================
# CONFIG
# ============================================================

START_CAPITAL        = 50_000.0
MAX_POSITIONS        = 10
RISICO_PCT_PER_TRADE = 0.05
TAX_RATE             = 0.10
NEWSAPI_KEY          = os.getenv("NEWSAPI_KEY", "")
NEWSAPI_DAILY_BUDGET = 95
_newsapi_calls_used   = 0
_newsapi_rate_limited = False

MS_CFG = {
    "rsi_period":         14,
    "rsi_oversold":       35.0,
    "rsi_momentum_low":   55.0,
    "rsi_momentum_high":  70.0,
    "macd_fast":          12,
    "macd_slow":          26,
    "macd_signal":        9,
    "macd_cross_lookback": 5,
    "sma_trend_period":   200,
    "sma_short_period":   50,
    "max_pct_above_sma_short": 15.0,
    "atr_period":         14,
    "sentiment_lookback_days": 7,
    "sentiment_min_articles":  2,
    "min_score":        4.0,
}

BACKTEST_START = "2021-01-01"
BACKTEST_END   = today_str()

# ============================================================
# SENTIMENT
# ============================================================

_vader = SentimentIntensityAnalyzer()
_company_name_cache: Dict[str, str] = {}

def resolve_company_name(ticker: str) -> str:
    if ticker in _company_name_cache:
        return _company_name_cache[ticker]
    naam = ticker.split(".")[0]
    try:
        info = yf.Ticker(ticker).get_info()
        kandidaat = info.get("shortName") or info.get("longName")
        if kandidaat:
            naam = kandidaat
    except Exception as e:
        print(f"[WARN] Kon bedrijfsnaam voor {ticker} niet ophalen ({e}), val terug op '{naam}'")
    _company_name_cache[ticker] = naam
    return naam

def fetch_news_sentiment(ticker: str, company_hint: str = "") -> Tuple[Optional[float], int]:
    global _newsapi_calls_used, _newsapi_rate_limited
    if not NEWSAPI_KEY or _newsapi_calls_used >= NEWSAPI_DAILY_BUDGET or _newsapi_rate_limited:
        return None, 0

    query   = company_hint or ticker
    since   = (dt.date.today() - dt.timedelta(days=MS_CFG["sentiment_lookback_days"])).isoformat()
    url     = "https://newsapi.org/v2/everything"
    params  = {"q": query, "from": since, "sortBy": "publishedAt", "language": "en", "pageSize": 20, "apiKey": NEWSAPI_KEY}
    try:
        _newsapi_calls_used += 1
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 429:
            _newsapi_rate_limited = True
            return None, 0
        if resp.status_code != 200:
            return None, 0
        articles = resp.json().get("articles", [])
    except Exception:
        return None, 0

    if len(articles) < MS_CFG["sentiment_min_articles"]:
        return None, len(articles)

    scores = []
    for art in articles:
        tekst = " ".join(filter(None, [art.get("title"), art.get("description")]))
        if tekst.strip():
            scores.append(_vader.polarity_scores(tekst)["compound"])

    if not scores:
        return None, 0
    return float(np.mean(scores)), len(scores)

def sentiment_bonus(score: Optional[float]) -> float:
    if score is None:
        return 0.0
    if score >= 0.15:
        return min(1.0, score)
    if score <= -0.15:
        return max(-1.0, score)
    return 0.0

# ============================================================
# ANALYSE
# ============================================================

@dataclass
class MSSignaal:
    ticker: str
    price: float
    rsi_monthly: float
    macd_label: str
    tech_score: float
    sentiment_score: Optional[float]
    sentiment_n: int
    score: float
    atr: float
    stop: float

def analyse_ticker(ticker: str, df_ticker: pd.DataFrame, met_sentiment: bool = False) -> Optional[MSSignaal]:
    df = df_ticker.sort_values("Date").reset_index(drop=True)
    if len(df) < 210:
        return None

    close = df["Close"]
    high  = df["High"] if "High" in df.columns else close
    low   = df["Low"] if "Low" in df.columns else close

    rsi_series = compute_rsi_series(close, MS_CFG["rsi_period"])
    macd_line, signal_line, hist = compute_macd_series(close, MS_CFG["macd_fast"], MS_CFG["macd_slow"], MS_CFG["macd_signal"])
    atr_series = compute_atr_series(high, low, close, MS_CFG["atr_period"])
    sma_trend  = close.rolling(MS_CFG["sma_trend_period"], min_periods=MS_CFG["sma_trend_period"]).mean()
    sma_short  = close.rolling(MS_CFG["sma_short_period"], min_periods=MS_CFG["sma_short_period"]).mean()

    rsi    = safe_float(rsi_series.iloc[-1])
    macd   = safe_float(macd_line.iloc[-1])
    sig    = safe_float(signal_line.iloc[-1])
    atr    = safe_float(atr_series.iloc[-1])
    price  = safe_float(close.iloc[-1])
    sma    = safe_float(sma_trend.iloc[-1])
    sma50  = safe_float(sma_short.iloc[-1])

    if math.isnan(rsi) or math.isnan(price) or math.isnan(atr):
        return None

    tech_score = 0.0

    if rsi <= MS_CFG["rsi_oversold"] or (MS_CFG["rsi_momentum_low"] <= rsi <= MS_CFG["rsi_momentum_high"]):
        tech_score += 1.0

    diff = (macd_line - signal_line).tail(MS_CFG["macd_cross_lookback"] + 1)
    diff_prev = diff.shift(1)
    fresh_cross = bool(((diff_prev <= 0) & (diff > 0)).any())
    if fresh_cross:
        tech_score += 1.0
    macd_label = "Verse crossover" if fresh_cross else ("Bullish" if macd > sig else "Bearish")

    if not math.isnan(sma) and price > sma:
        tech_score += 1.0

    if not math.isnan(sma50) and sma50 > 0:
        pct_above_sma50 = (price - sma50) / sma50 * 100.0
        if pct_above_sma50 <= MS_CFG["max_pct_above_sma_short"]:
            tech_score += 1.0

    sent_score, sent_n = (None, 0)
    if met_sentiment and NEWSAPI_KEY and not _newsapi_rate_limited:
        naam = resolve_company_name(ticker)
        sent_score, sent_n = fetch_news_sentiment(ticker, company_hint=naam)

    eind_score = tech_score + sentiment_bonus(sent_score)
    stop = price - 2.0 * atr

    return MSSignaal(
        ticker=ticker, price=price, rsi_monthly=rsi, macd_label=macd_label,
        tech_score=tech_score, sentiment_score=sent_score, sentiment_n=sent_n,
        score=eind_score, atr=atr, stop=stop,
    )

# ============================================================
# OUTPUT
# ============================================================

def format_bericht(ex_name: str, signalen: List[MSSignaal]) -> str:
    if not signalen:
        return ""
    regels = [f"*MARKTSENTIMENT — {ex_name}*  ({today_str()})\n"]
    for s in signalen[:15]:
        sent_txt = f"{s.sentiment_score:+.2f} ({s.sentiment_n}art.)" if s.sentiment_score is not None else f"n/a ({s.sentiment_n}art.)"
        regels.append(f"• *{s.ticker}* — score {s.score:.1f} | RSI {s.rsi_monthly:.0f} | MACD {s.macd_label} | sentiment {sent_txt} | EUR{s.price:.2f}")
    return "\n".join(regels)

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    print(f"{'='*60}\nMARKTSENTIMENT — LIVE  {today_str()}\n{'='*60}")

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

    print(f"\nTotaal: {len(all_tickers)} unieke kwaliteitstickers")
    print("Koersdata downloaden (2 jaar)...")
    df = download_history(all_tickers, period="2y")
    if df.empty:
        print("[ERROR] Geen koersdata.")
        return

    email_delen: List[str] = []

    globale_kandidaten: List[Tuple[str, MSSignaal]] = []
    for ex_name, tlist in exchange_tickers.items():
        df_ex = df[df["Ticker"].isin(tlist)].copy()
        for ticker, group in df_ex.groupby("Ticker", sort=False):
            sig = analyse_ticker(ticker, group, met_sentiment=False)
            if sig is not None and sig.tech_score >= 2.0:
                globale_kandidaten.append((ex_name, sig))

    print(f"\nTechnische voorselectie klaar: {len(globale_kandidaten)} kandidaten over {len(exchange_tickers)} beurzen (tech_score >= 2.0).")

    grensgevallen = [t for t in globale_kandidaten if t[1].tech_score < MS_CFG["min_score"]]
    al_gekwalificeerd = [t for t in globale_kandidaten if t[1].tech_score >= MS_CFG["min_score"]]
    al_gekwalificeerd.sort(key=lambda t: t[1].tech_score, reverse=True)
    verwerkingsvolgorde = grensgevallen + al_gekwalificeerd

    per_beurs: Dict[str, List[MSSignaal]] = {ex: [] for ex in exchange_tickers}
    for ex_name, kand in verwerkingsvolgorde:
        df_ex_ticker = df[(df["Ticker"] == kand.ticker) & (df["Ticker"].isin(exchange_tickers[ex_name]))]
        sig = analyse_ticker(kand.ticker, df_ex_ticker, met_sentiment=True)
        if sig is not None and sig.score >= MS_CFG["min_score"]:
            per_beurs[ex_name].append(sig)

    print(f"NewsAPI-calls gebruikt: {_newsapi_calls_used}/{NEWSAPI_DAILY_BUDGET} ({len(grensgevallen)} grensgevallen voorrang gegeven)\n")

    for ex_name, tlist in exchange_tickers.items():
        signalen = per_beurs[ex_name]
        signalen.sort(key=lambda s: s.score, reverse=True)
        if not signalen:
            continue
        print(f"Analyseren: {ex_name} ({len(tlist)} tickers)... → {len(signalen)} kandidaten (min_score {MS_CFG['min_score']})")
        for s in signalen:
            print(f"  ✓ {s.ticker}: score {s.score:.1f} | RSI={s.rsi_monthly:.1f} | sentiment={s.sentiment_score}")

        for s in signalen:
            log_selectie(
                ticker=s.ticker, datum=today_str(), strategie="bot_01marktsent", beurs=ex_name, koers=s.price,
                parameters={
                    "score": s.score, "rsi_monthly": s.rsi_monthly, "macd_label": s.macd_label,
                    "stop": s.stop, "atr": s.atr, "sentiment_score": s.sentiment_score,
                    "sentiment_n": s.sentiment_n, "grafiek": yahoo_link(s.ticker),
                },
            )

        bericht = format_bericht(ex_name, signalen)
        if bericht:
            send_telegram(bericht, parse_mode="Markdown")
            email_delen.append(bericht)
            print(f"  → Telegram verstuurd")

    if email_delen:
        send_email(f"Marktsentiment rapport {today_str()}", "\n\n" + ("=" * 40 + "\n\n").join(email_delen))
    else:
        print("\nGeen kandidaten vandaag, geen berichten verstuurd.")

    print(f"{'='*60}\nKlaar.")

# ============================================================
# BACKTEST (technisch only)
# ============================================================

def run_backtest():
    print(f"{'='*60}\nMARKTSENTIMENT BACKTEST (technisch, walk-forward)  {BACKTEST_START} -> {BACKTEST_END}\n{'='*60}")
    print("LET OP: sentiment zit hier NIET in — geen historisch nieuwsarchief beschikbaar.")
    
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

    all_dates = sorted(df["Date"].dt.date.unique())
    cash      = START_CAPITAL
    positions: Dict[str, Dict] = {}
    trades:    List[Dict]      = []

    scan_dates = []
    prev_month = None
    for d in all_dates:
        if d.month != prev_month:
            scan_dates.append(d)
            prev_month = d.month
    print(f"Scanmomenten: {len(scan_dates)} (maandelijks)")

    for scan_date in scan_dates:
        df_hist = df[df["Date"] <= pd.Timestamp(scan_date)].copy()

        for ticker, group in df_hist.groupby("Ticker", sort=False):
            if ticker in positions or len(positions) >= MAX_POSITIONS:
                continue
            sig = analyse_ticker(ticker, group, met_sentiment=False)
            if not sig or sig.tech_score < MS_CFG["min_score"]:
                continue
            entry = sig.price * (1 + SLIPPAGE_PCT)
            risico_eur   = cash * RISICO_PCT_PER_TRADE
            stop_afstand = 2.0 * sig.atr
            if math.isnan(stop_afstand) or stop_afstand <= 0:
                continue
            aandelen    = max(1, int(risico_eur / stop_afstand))
            investering = entry * aandelen + trade_cost(entry * aandelen)
            if investering > cash:
                continue
            cash -= investering
            positions[ticker] = {
                "entry_date": scan_date, "entry_price": round(entry, 4),
                "size": aandelen, "stop": sig.stop, "days": 0,
                "cost": trade_cost(investering), "score": sig.tech_score,
            }

        day_df = df[df["Date"] == pd.Timestamp(scan_date)].copy()
        price_map: Dict[str, float] = {}
        for _, row in day_df.iterrows():
            t = row.get("Ticker")
            c = safe_float(row.get("Close"))
            if t and not math.isnan(c):
                price_map[t] = c

        for ticker, pos in list(positions.items()):
            pos["days"] += 1
            if ticker not in price_map:
                continue
            close  = price_map[ticker]
            reason = None
            if close <= pos["stop"]:
                reason = f"SL ({pos['stop']:.2f})"
            elif pos["days"] >= 60:
                reason = "Time (60d)"

            if reason:
                exit_slip = close * (1 - SLIPPAGE_PCT)
                gross     = exit_slip * pos["size"]
                cost      = trade_cost(gross)
                pnl       = gross - cost - (pos["entry_price"] * pos["size"] + pos["cost"])
                tax       = pnl * TAX_RATE if pnl > 0 else 0.0
                cash     += gross - cost - tax
                trades.append({
                    "entry_date": pos["entry_date"].isoformat(), "exit_date": scan_date.isoformat(),
                    "ticker": ticker, "score": pos["score"], "entry_price": pos["entry_price"],
                    "exit_price": round(exit_slip, 4), "size": pos["size"], "pnl": round(pnl, 2),
                    "tax": round(tax, 2), "net": round(pnl - tax, 2), "reason": reason, "days": pos["days"],
                })
                del positions[ticker]

    if trades:
        tdf   = pd.DataFrame(trades)
        n     = len(tdf)
        nwin  = (tdf["net"] > 0).sum()
        pf    = abs(tdf.loc[tdf["net"] > 0, "net"].sum()) / max(abs(tdf.loc[tdf["net"] <= 0, "net"].sum()), 1e-9)
        final = cash + sum(price_map.get(t, p["entry_price"]) * p["size"] for t, p in positions.items())
        print(f"\n{'='*60}\nStartkapitaal : EUR{START_CAPITAL:>12,.2f}\nEindkapitaal  : EUR{final:>12,.2f}\nRendement     : {(final-START_CAPITAL)/START_CAPITAL*100:>+.1f}%")
        print(f"Trades        : {n} | Winnaars: {nwin} ({nwin/n*100:.1f}%)\nProfit Factor : {pf:.2f}\nBelasting     : EUR{tdf['tax'].sum():,.2f}")
        print(f"Gem. houdduur : {tdf['days'].mean():.1f} dagen\n{'='*60}\n(Dit is de technische score zonder sentiment — zie docstring.)")
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
