#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01cointegr.py — STATISTISCHE ARBITRAGE (Pairs Trading) v2.1

Pairwise Engle-Granger-selectie met BH/FDR na correlatievoorfilter.
Live adviesrapport met e-mail/database; Telegram uit.
Backtest: synthetische genormaliseerde indexeenheden, geen EUR-portefeuille.

Gebruik:  python bot_01cointegr.py live | backtest
"""

import os
import argparse
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
import yfinance as yf
import statsmodels.api as sm
from statsmodels.tsa.stattools import coint

from common import (
    today_str, safe_float, yahoo_link,
    load_tickers_from_file, bouw_bestandslijst, label_voor,
    send_email, download_history as common_download_history,
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
TAX_RATE             = 0.0  # Geen willekeurige belastinginhouding

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

BACKTEST_START = "2021-01-01"
BACKTEST_END   = dt.date.today().isoformat()

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
    geindexeerd = sorted(enumerate([p if math.isfinite(p) and 0 <= p <= 1 else 1.0 for p in p_waarden]), key=lambda x: x[1])
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

def download_history(tickers: List[str], period: str = "2y", start=None, end=None) -> pd.DataFrame:
    """Bounded batches with common's individual retry for missing tickers."""
    tickers = sorted(set(tickers))
    frames = []
    for offset in range(0, len(tickers), DOWNLOAD_BATCH_SIZE):
        batch = tickers[offset:offset + DOWNLOAD_BATCH_SIZE]
        frame = common_download_history(batch, period=period, start=start, end=end)
        if not frame.empty:
            frames.append(frame)
        print(f"Download: {min(offset + len(batch), len(tickers))}/{len(tickers)}", flush=True)
        if offset + len(batch) < len(tickers):
            time.sleep(DOWNLOAD_BATCH_PAUSE)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def prepare_prices(df: pd.DataFrame, tickers: List[str], live=True):
    issues = {}
    series = {}
    today = pd.Timestamp(today_str())
    for ticker in sorted(set(tickers)):
        if df.empty or not {"Date", "Ticker", "Close"}.issubset(df.columns):
            issues[ticker] = "geen koersdata"
            continue
        g = df.loc[df.Ticker == ticker, ["Date", "Close"]].copy()
        g["Date"] = pd.to_datetime(g.Date, errors="coerce")
        if g.Date.dt.tz is not None:
            g["Date"] = g.Date.dt.tz_localize(None)
        g["Date"] = g.Date.dt.normalize()
        g = g.loc[g.Date.notna() & (g.Date < today)].sort_values("Date").drop_duplicates("Date", keep="last")
        g["Close"] = pd.to_numeric(g.Close, errors="coerce")
        valid = np.isfinite(g.Close) & (g.Close > 0)
        if g.empty or (live and not valid.iloc[-1]):
            issues[ticker] = "ontbrekende/ongeldige laatste koers"
            continue
        g = g.loc[valid]
        if len(g) < LOOKBACK or (live and (today - g.Date.iloc[-1]).days > 7):
            issues[ticker] = "te korte of verouderde koershistorie"
            continue
        series[ticker] = g.set_index("Date").Close
    return pd.DataFrame(series).sort_index(), issues


def pair_history(a, b, data, lookback=LOOKBACK):
    if a not in data or b not in data:
        return pd.DataFrame()
    pair = data[[a, b]].replace([np.inf, -np.inf], np.nan)
    pair = pair.where(pair > 0).dropna().sort_index()
    pair = pair.loc[~pair.index.duplicated(keep="last")]
    if len(pair) < lookback:
        return pd.DataFrame()
    # Both legs must have a quote on their own latest available session.
    if any(pair.index[-1] != data[t].dropna().index[-1] for t in (a,b)):
        return pd.DataFrame()
    return pair

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
    signal_date:   str = ""

def analyseer_pair_ruw(ticker_a: str, ticker_b: str, data: pd.DataFrame, lookback: int = LOOKBACK, filter_half_life: bool = True) -> Optional[PairSignaal]:
    try:
        data = pair_history(ticker_a, ticker_b, data, lookback)
        if data.empty:
            return None

        spread = data[ticker_a] / data[ticker_b]
        spread_mean = spread.rolling(lookback).mean()
        spread_std = spread.rolling(lookback).std()

        current_spread = spread.iloc[-1]
        current_mean = spread_mean.iloc[-1]
        current_std = spread_std.iloc[-1]

        if not all(math.isfinite(x) for x in (current_spread, current_mean, current_std)) or current_std <= 0:
            return None

        z_score = (current_spread - current_mean) / current_std
        _, p_value, _ = coint(data[ticker_a], data[ticker_b])
        half_life = schat_half_life(spread)
        
        if not math.isfinite(p_value) or not 0 <= p_value <= 1:
            return None
        if filter_half_life and (not math.isfinite(half_life) or half_life > MAX_HALF_LIFE or half_life <= 0):
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
            p_value=float(p_value), half_life=float(half_life),
            current_z=float(z_score), spread=float(current_spread),
            spread_mean=float(current_mean), spread_std=float(current_std),
            price_a=float(data[ticker_a].iloc[-1]),
            price_b=float(data[ticker_b].iloc[-1]),
            signal=signal, score=float(score_val), signal_date=pd.Timestamp(data.index[-1]).date().isoformat(),
        )
    except (ValueError, np.linalg.LinAlgError) as exc:
        print(f"[DATA] {ticker_a}/{ticker_b}: statistische toets mislukt ({exc})")
        return None

def selecteer_gecointegreerde_paren(tickers: List[str], data: pd.DataFrame, corr_prefilter: float = CORR_PREFILTER, fdr_alpha: float = FDR_ALPHA, lookback: int = LOOKBACK) -> List[PairSignaal]:
    available = sorted(set(tickers) & set(data.columns))
    raw, pvalues = [], []
    clean = data[available].replace([np.inf, -np.inf], np.nan)
    clean = clean.where(clean > 0)
    correlations = clean.corr(min_periods=lookback).abs()
    for a, b in combinations(available, 2):
        corr = correlations.loc[a, b]
        if not math.isfinite(corr) or corr < corr_prefilter:
            continue
        pair = pair_history(a, b, clean, lookback)
        if pair.empty:
            continue
        sig = analyseer_pair_ruw(a, b, pair, lookback, filter_half_life=False)
        raw.append(sig)
        pvalues.append(sig.p_value if sig is not None else 1.0)
    mask = fdr_correctie(pvalues, fdr_alpha)
    significant = [sig for sig, keep in zip(raw, mask) if keep and sig is not None
                   and math.isfinite(sig.half_life) and 0 < sig.half_life <= MAX_HALF_LIFE]
    significant.sort(key=lambda sig: (-sig.score, sig.pair))
    return significant

def snel_zscore(ticker_a: str, ticker_b: str, data: pd.DataFrame, lookback: int = LOOKBACK) -> Optional[Tuple[float, float, float, float]]:
    data = pair_history(ticker_a, ticker_b, data, lookback)
    if data.empty:
        return None
    spread = data[ticker_a] / data[ticker_b]
    spread_mean = spread.rolling(lookback).mean()
    spread_std = spread.rolling(lookback).std()
    current_spread = spread.iloc[-1]
    current_mean = spread_mean.iloc[-1]
    current_std = spread_std.iloc[-1]
    if not all(math.isfinite(x) for x in (current_spread, current_mean, current_std)) or current_std <= 0:
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

    top = signalen[:MAX_PAIRS]
    delen = [
        f"📊 *PAIRS TRADING — {exchange_name}*",
        f"_{nu} | {len(signalen)} actieve signals_",
        "─────────────────────────────",
        "🏆 *TOP SIGNALEN:*",
    ]

    for s in top:
        delen.append(f"• {signal_emoji(s.signal)} `{s.pair}`\n  Z-score: {s.current_z:.2f} | p={s.p_value:.4f} | Half-life: {s.half_life:.1f}d\n  Spread: {s.spread:.4f} (mean={s.spread_mean:.4f}, σ={s.spread_std:.4f})\n  {yahoo_link(s.ticker_a)} / {yahoo_link(s.ticker_b)} | Koers (bronkoerseenheden): {s.price_a:.4f} / {s.price_b:.4f} | koersdag {s.signal_date}")

    if len(signalen) > MAX_PAIRS:
        extra = signalen[MAX_PAIRS:]
        delen.append("─────────────────────────────\n📋 *Overige signalen:*")
        for s in extra:
            emoji = "🟢" if s.signal == "LONG" else "🔴" if s.signal == "SHORT" else "⚪"
            delen.append(f"  {emoji} `{s.pair}` | z={s.current_z:.2f} | p={s.p_value:.4f}")

    delen.append("LONG = long A / short B; SHORT = short A / long B. Ratio-spread, geen geschatte hedge-ratio. Advies, geen orders; shortbaarheid, FX en leenkosten niet getoetst.")
    delen.append(f"\n⚙️ _Entry: ±{ENTRY_Z}σ | Exit: ±{EXIT_Z}σ | Lookback: {LOOKBACK}d | FDR α: {FDR_ALPHA}_")
    return "\n\n".join(delen)

def run_live_engine():
    reports, issues, errors = [], [], []
    exchange_tickers = {}
    for filename in bouw_bestandslijst():
        tickers = load_tickers_from_file(filename)
        if tickers:
            exchange_tickers[label_voor(filename)] = sorted(set(tickers))
        else:
            issues.append(f"{filename}: ontbrekende of lege lijst")
    tickers = sorted({t for values in exchange_tickers.values() for t in values})
    df = download_history(tickers, period="2y")
    prices, bad = prepare_prices(df, tickers)
    issues += [f"{ticker}: {reason}" for ticker, reason in bad.items()]
    if len(prices.columns) < 2:
        errors.append("Minder dan twee bruikbare tickers")
    stored = selected = tested_exchanges = 0
    for exchange, universe in exchange_tickers.items():
        available = [t for t in universe if t in prices]
        if len(available) < 2:
            issues.append(f"{exchange}: minder dan twee bruikbare tickers")
            continue
        try:
            signals = selecteer_gecointegreerde_paren(available, prices)
            tested_exchanges += 1
        except Exception as exc:
            errors.append(f"{exchange}: analyse mislukt ({exc})")
            continue
        active = [s for s in signals if s.signal != "NEUTRAL"][:MAX_PAIRS]
        selected += len(active)
        for sig in active:
            try:
                ok = log_selectie(ticker=sig.pair, datum=sig.signal_date,
                    strategie="bot_01cointegr", beurs=exchange, koers=sig.spread,
                    parameters={"signal": sig.signal, "z_score": sig.current_z, "p_value": sig.p_value,
                        "half_life": sig.half_life, "spread_mean": sig.spread_mean,
                        "spread_std": sig.spread_std, "price_a": sig.price_a, "price_b": sig.price_b,
                        "score": sig.score, "signal_date": sig.signal_date,
                        "grafiek": yahoo_link(sig.ticker_a)})
                if not ok:
                    raise RuntimeError("geen opslagbevestiging")
                stored += 1
            except Exception as exc:
                errors.append(f"{sig.pair}: database mislukt ({exc})")
        reports.append(f"{exchange}: {len(available)}/{len(universe)} tickers; "
                       f"{len(signals)} paren na FDR; {len(active)} actieve topselecties.\n" +
                       (format_pairs_bericht(exchange, active) or "Geen actieve signalen."))
        print(reports[-1], flush=True)
    if not tested_exchanges:
        errors.append("Geen beurs met bruikbaar analyse-universum")
    summary = f"Datadekking: {len(prices.columns)}/{len(tickers)} unieke tickers. Database: {stored}/{selected} selecties. Telegram uit."
    body = "\n\n".join([summary] + reports + ["Databeperkingen:"] + issues + ["Fouten:"] + errors)
    print(body, flush=True)
    subject = ("[ONVOLLEDIG] " if issues or errors else "") + "PAIRS TRADING rapport " + today_str()
    if not send_email(subject, body):
        errors.append("E-mail niet verzonden")
    if errors:
        raise RuntimeError("; ".join(errors))
    print("Klaar — e-mail verzonden.", flush=True)

def laad_exchange_tickers() -> Dict[str, List[str]]:
    """Bouwt {beursnaam: [tickers]} - identiek gebruikt door live en backtest,
    zodat beide op dezelfde per-beurs segmentatie draaien (hersteld uit v1.1)."""
    exchange_tickers: Dict[str, List[str]] = {}
    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist:
            continue
        exchange_tickers[label_voor(f_name)] = tlist
    return exchange_tickers


def run_backtest():
    print(f"{'='*60}")
    print(f"PAIRS TRADING BACKTEST (walk-forward)  {BACKTEST_START} -> {BACKTEST_END}")
    print(f"{'='*60}")

    exchange_tickers = laad_exchange_tickers()
    all_tickers = sorted(set(t for tlist in exchange_tickers.values() for t in tlist))
    if not all_tickers:
        raise RuntimeError("Geen tickers gevonden")

    print(f"Tickers: {len(all_tickers)} over {len(exchange_tickers)} beurzen | Data downloaden (5y)...")
    df = download_history(all_tickers, start=(pd.Timestamp(BACKTEST_START) - pd.DateOffset(years=2)).date().isoformat(),
                          end=(pd.Timestamp(BACKTEST_END) + pd.Timedelta(days=1)).date().isoformat())
    if df.empty:
        raise RuntimeError("Geen backtestdata")

    pivot, limitations = prepare_prices(df, all_tickers, live=False)
    print(f"Databeperkingen: {limitations}")
    # Common synthetic units: each leg rebased to 100 using pre-test data only.
    for ticker in list(pivot):
        prior = pivot.loc[pivot.index < pd.Timestamp(BACKTEST_START), ticker].dropna()
        if len(prior) < IS_WINDOW:
            pivot = pivot.drop(columns=ticker)
        else:
            pivot[ticker] = 100 * pivot[ticker] / prior.iloc[0]
    print("Synthetische indexeenheden; geen EUR-rendement, FX-, borrow- of margincallmodel.")
    print(f"Data: {len(pivot.columns)} tickers | {len(pivot)} dagen")

    cash = START_CAPITAL
    positions: Dict[str, Dict] = {}
    trades: List[Dict] = []
    equity_curve: List[Dict] = []
    pair_cache: Dict[str, List[PairSignaal]] = {}
    laatste_selectie_idx = -(RESCAN_DAYS + 1)

    # Respecteer de ingestelde backtestperiode, maar behoud historische
    # koersen vóór BACKTEST_START als in-sample opwarmvenster.
    pivot = pivot.loc[pivot.index <= pd.Timestamp(BACKTEST_END)]
    dates = sorted(pivot.index)
    warmup = max(LOOKBACK, IS_WINDOW)
    if len(dates) <= warmup:
        raise RuntimeError(f"Onvoldoende handelsdagen: {len(dates)}; minimaal {warmup + 1}")
    requested_start = pd.Timestamp(BACKTEST_START)
    first_test_idx = next((i for i, day in enumerate(dates) if day >= requested_start), None)
    if first_test_idx is None:
        raise RuntimeError("Geen handelsdagen binnen de backtestperiode")
    start_idx = max(warmup, first_test_idx)
    print(f"Backtest van {dates[start_idx].date()} tot {dates[-1].date()}")

    valuation_table = pivot.ffill()
    for i, date in enumerate(dates):
        if i < start_idx:
            continue

        # Signalen en pairselectie uitsluitend met gegevens tot de vorige
        # handelsdag. Uitvoering gebeurt pas tegen de huidige slotkoers.
        # Zo wordt niet op dezelfde slotkoers gehandeld die het signaal vormde.
        signal_data = pivot.iloc[:i]
        current_prices = pivot.iloc[i]
        valuation_prices = valuation_table.iloc[i]

        # --- Exits (goedkope z-score-check, geen coint-hertest) ---
        for pair_key, pos in list(positions.items()):
            a, b = pair_key.split('/')
            if a not in current_prices or b not in current_prices:
                continue
            pos['days'] = max(0, len(pivot.loc[pos['entry_date']:date, [a,b]].dropna()) - 1)
            z_info = snel_zscore(a, b, signal_data)
            z = z_info[0] if z_info is not None else None
            # De maximale houdperiode geldt ook wanneer de z-score
            # tijdelijk niet berekend kan worden.
            exit_signal = pos['days'] >= 60 or (z is not None and abs(z) < EXIT_Z)
            if z is None and not exit_signal:
                continue
            if exit_signal:
                price_a, price_b = float(current_prices[a]), float(current_prices[b])
                if not (math.isfinite(price_a) and math.isfinite(price_b) and price_a > 0 and price_b > 0):
                    continue
                # LONG = long A / short B; SHORT = short A / long B.
                sign_a = 1 if pos['direction'] == 'LONG' else -1
                sign_b = -sign_a
                exit_a = price_a * (1 - sign_a * SLIPPAGE_PCT)
                exit_b = price_b * (1 - sign_b * SLIPPAGE_PCT)
                gross_pnl = (sign_a * (exit_a - pos['entry_a']) * pos['size_a']
                             + sign_b * (exit_b - pos['entry_b']) * pos['size_b'])
                exit_fees = (trade_cost(pos['size_a'] * exit_a)
                             + trade_cost(pos['size_b'] * exit_b))
                cost = pos['entry_fees'] + exit_fees
                taxable_profit = gross_pnl - cost
                tax = max(0.0, taxable_profit) * TAX_RATE
                net_pnl = taxable_profit - tax

                # Instapkosten zijn al bij opening van cash afgetrokken.
                cash += pos['reserved'] + gross_pnl - exit_fees - tax

                trades.append({
                    'entry_date': pos['entry_date'].strftime('%Y-%m-%d'),
                    'exit_date': date.strftime('%Y-%m-%d'),
                    'pair': pair_key,
                    'direction': pos['direction'],
                    'entry_z': pos['entry_z'],
                    'exit_z': z,
                    'gross_pnl': round(gross_pnl, 2),
                    'net_pnl': round(net_pnl, 2),
                    'days': pos['days'],
                })
                del positions[pair_key]

        # --- Walk-forward herselectie: alleen data tot gisteren ---
        if i - laatste_selectie_idx >= RESCAN_DAYS:
            is_data = signal_data.tail(IS_WINDOW)
            totaal_paren = 0
            for ex_name, tlist in exchange_tickers.items():
                geselecteerd = selecteer_gecointegreerde_paren(tlist, is_data)
                pair_cache[ex_name] = geselecteerd[:MAX_PAIRS]
                totaal_paren += len(pair_cache[ex_name])
            laatste_selectie_idx = i
            print(f"  [{date.date()}] walk-forward herselectie: {totaal_paren} paren over alle beurzen")

        # --- Nieuwe posities openen (dagelijks, enkel op de cache — geen nieuwe coint-tests) ---
        if len(positions) < MAX_TOTAL_PAIRS:
            for ex_name, kandidaten in pair_cache.items():
                for kand in kandidaten:
                    if len(positions) >= MAX_TOTAL_PAIRS:
                        break
                    a, b = kand.ticker_a, kand.ticker_b
                    pair_key = f"{a}/{b}"
                    if pair_key in positions:
                        continue
                    if a not in current_prices or b not in current_prices:
                        continue
                    if (not math.isfinite(current_prices[a]) or not math.isfinite(current_prices[b])
                            or current_prices[a] <= 0 or current_prices[b] <= 0):
                        continue

                    z_info = snel_zscore(a, b, signal_data)
                    if z_info is None:
                        continue
                    z = z_info[0]
                    if abs(z) <= ENTRY_Z:
                        continue
                    richting = "SHORT" if z > ENTRY_Z else "LONG"

                    investering_per_pair = cash * ALLOC_PCT
                    if investering_per_pair <= 0 or investering_per_pair > cash:
                        continue
                    size_a = int(investering_per_pair / current_prices[a] * 0.5)
                    size_b = int(investering_per_pair / current_prices[b] * 0.5)
                    if size_a <= 0 or size_b <= 0:
                        continue

                    sign_a = 1 if richting == "LONG" else -1
                    sign_b = -sign_a
                    entry_a = float(current_prices[a]) * (1 + sign_a * SLIPPAGE_PCT)
                    entry_b = float(current_prices[b]) * (1 + sign_b * SLIPPAGE_PCT)
                    entry_fees = (trade_cost(size_a * entry_a)
                                  + trade_cost(size_b * entry_b))
                    if investering_per_pair + entry_fees > cash:
                        continue
                    cash -= investering_per_pair + entry_fees  # marge + twee instaptransacties
                    positions[pair_key] = {
                        'entry_date': date,
                        'entry_a': entry_a,
                        'entry_b': entry_b,
                        'entry_fees': entry_fees,
                        'size_a': size_a,
                        'size_b': size_b,
                        'direction': richting,
                        'entry_z': z,
                        'days': 0,
                        'reserved': investering_per_pair,
                    }

        # --- Equity curve: vrij cash + gereserveerde marge + unrealized pnl ---
        portfolio_value = cash
        for pair_key, pos in positions.items():
            a, b = pair_key.split('/')
            # Ook bij ontbrekende koers blijft de gereserveerde marge meetellen.
            portfolio_value += pos['reserved']
            if (a in valuation_prices and b in valuation_prices
                    and math.isfinite(valuation_prices[a]) and math.isfinite(valuation_prices[b])):
                sign_a = 1 if pos['direction'] == 'LONG' else -1
                sign_b = -sign_a
                exit_a = float(valuation_prices[a]) * (1 - sign_a * SLIPPAGE_PCT)
                exit_b = float(valuation_prices[b]) * (1 - sign_b * SLIPPAGE_PCT)
                unrealized = (sign_a * (exit_a - pos['entry_a']) * pos['size_a']
                              + sign_b * (exit_b - pos['entry_b']) * pos['size_b'])
                estimated_exit_fees = (trade_cost(pos['size_a'] * exit_a)
                                       + trade_cost(pos['size_b'] * exit_b))
                portfolio_value += unrealized - estimated_exit_fees

        equity_curve.append({'date': date, 'value': portfolio_value})

    # Always expose empty trades, open positions and valuation history.
    os.makedirs('backtest_results', exist_ok=True)
    pd.DataFrame(trades, columns=['entry_date','exit_date','pair','direction','entry_z',
        'exit_z','gross_pnl','net_pnl','days']).to_csv('backtest_results/backtest_cointegr.csv', index=False)
    pd.DataFrame(equity_curve, columns=['date','value']).to_csv('backtest_results/cointegr_equity.csv', index=False)
    pd.DataFrame([dict(pair=k, **v) for k,v in positions.items()],
        columns=['pair','entry_date','entry_a','entry_b','entry_fees','size_a','size_b',
                 'direction','entry_z','days','reserved']).to_csv('backtest_results/cointegr_open.csv', index=False)
    # --- Resultaten ---
    if trades:
        tdf = pd.DataFrame(trades)
        final_value = equity_curve[-1]['value'] if equity_curve else cash
        total_return = (final_value - START_CAPITAL) / START_CAPITAL * 100
        n_win = (tdf['net_pnl'] > 0).sum()
        win_rate = n_win / len(tdf) * 100

        gross_profit = tdf[tdf['net_pnl'] > 0]['net_pnl'].sum()
        gross_loss = abs(tdf[tdf['net_pnl'] <= 0]['net_pnl'].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('nan')

        equity_df = pd.DataFrame(equity_curve)
        equity_df['peak'] = equity_df['value'].cummax().clip(lower=START_CAPITAL)
        equity_df['drawdown'] = (equity_df['peak'] - equity_df['value']) / equity_df['peak']
        max_dd = equity_df['drawdown'].max() * 100

        os.makedirs('backtest_results', exist_ok=True)
        tdf.to_csv('backtest_results/backtest_cointegr.csv', index=False)

        print(f"\n{'='*60}")
        print(f"📊 BACKTEST RESULTATEN — bot_01cointegr (walk-forward)")
        print(f"{'='*60}")
        print(f"Startkapitaal     : indexeenheden {START_CAPITAL:>12,.2f}")
        print(f"Eindkapitaal      : indexeenheden {final_value:>12,.2f}")
        print(f"Totaal rendement  : {total_return:>+11.1f}%")
        print(f"Trades            : {len(tdf):>12}")
        print(f"Winnaars          : {n_win:>12} ({win_rate:.1f}%)")
        print(f"Profit Factor     : {profit_factor:>12.2f}")
        print(f"Max Drawdown      : {max_dd:>11.1f}%")
        print(f"Gem. houdduur     : {tdf['days'].mean():>11.1f} dagen")
        print(f"{'='*60}")

        top_pairs = tdf.groupby('pair')['net_pnl'].sum().sort_values(ascending=False).head(5)
        print("\n🏆 Beste paren:")
        for pair, pnl in top_pairs.items():
            print(f"  {pair}: indexeenheden {pnl:,.2f}")

        print("\n💾 Resultaten opgeslagen in backtest_results/backtest_cointegr.csv")
    else:
        print("Geen trades gegenereerd.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("live", "backtest"), default="live")
    mode = parser.parse_args().mode
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
