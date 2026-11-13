#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01cointegr.py — STATISTISCHE ARBITRAGE (Pairs Trading) v2.1

v2.1 t.o.v. v2.0 (herstel van functionaliteit die bij de refactor verloren ging):
  - Walk-forward backtest hersteld (v1.1): selectie op een in-sample venster
    (IS_WINDOW), dan RESCAN_DAYS out-of-sample verhandelen; per beurs
    gesegmenteerd, met echte marge-reservering. Uitvoer:
    backtest_results/backtest_cointegr.csv.
  - Download met logging en retry-meldingen hersteld (v2.0 slikte elke fout
    stil in) en de tak voor een batch met een enkele ticker.
  - laad_exchange_tickers() en BACKTEST_START/END terug.
  - De common/-refactor van v2.0 blijft behouden.

v2.0: gebruikt common/ voor alle gedeelde bouwstenen.
Bevat Engle-Granger co-integratie, walk-forward backtest en FDR-correctie.

Gebruik:  python bot_01cointegr.py live | backtest
"""

import os
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
    send_telegram, send_email,
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
TAX_RATE             = 0.10

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
    geindexeerd = sorted(enumerate(p_waarden), key=lambda x: x[1])
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

def _normalise(df_raw, ticker: str) -> Optional[pd.DataFrame]:
    if df_raw is None or not isinstance(df_raw, pd.DataFrame) or df_raw.empty:
        return None
    df = df_raw.copy().dropna(how="all")
    if df.empty:
        return None
    if df.index.name in ("Date", "Datetime") or isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index()
    if "Date" in df.columns:
        df = df.loc[:, ~df.columns.duplicated()]
    if "Datetime" in df.columns and "Date" not in df.columns:
        df = df.rename(columns={"Datetime": "Date"})
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if "Close" not in df.columns:
        return None
    df["Ticker"] = ticker
    return df

def _download_batch(batch: List[str], period: str) -> List[pd.DataFrame]:
    """Download 1 batch tickers via yf.download(); retourneert lijst van
    genormaliseerde per-ticker DataFrames. Retryt met backoff bij rate-limit."""
    frames: List[pd.DataFrame] = []
    kwargs = dict(tickers=batch, auto_adjust=True, group_by="ticker",
                  progress=False, threads=True, period=period)

    data = None
    for poging in range(DOWNLOAD_BATCH_RETRIES + 1):
        try:
            data = yf.download(**kwargs)
            break
        except Exception as e:
            wacht = DOWNLOAD_BATCH_PAUSE * (2 ** poging)
            print(f"[WARN] Batch mislukt ({e}), retry over {wacht:.0f}s "
                  f"(poging {poging + 1}/{DOWNLOAD_BATCH_RETRIES + 1})...")
            time.sleep(wacht)
            data = None

    if data is not None and not data.empty:
        if isinstance(data.columns, pd.MultiIndex):
            ticker_level = 1
            for lvl in range(data.columns.nlevels):
                if any(t in set(data.columns.get_level_values(lvl)) for t in batch):
                    ticker_level = lvl
                    break
            available = set(data.columns.get_level_values(ticker_level))
            for t in batch:
                if t not in available:
                    continue
                try:
                    norm = _normalise(data.xs(t, axis=1, level=ticker_level).copy(), t)
                    if norm is not None:
                        frames.append(norm)
                except Exception:
                    continue
        else:
            norm = _normalise(data, batch[0])
            if norm is not None:
                frames.append(norm)

    return frames

def download_history(tickers: List[str], period: str = "2y") -> pd.DataFrame:
    """Download historische koersdata in batches van DOWNLOAD_BATCH_SIZE
    tickers, met een pauze ertussen — voorkomt dat Yahoo Finance de hele
    aanvraag rate-limit't zoals bij één grote call met duizenden tickers."""
    if not tickers:
        return pd.DataFrame()

    frames: List[pd.DataFrame] = []
    batches = [tickers[i:i + DOWNLOAD_BATCH_SIZE]
               for i in range(0, len(tickers), DOWNLOAD_BATCH_SIZE)]

    for i, batch in enumerate(batches):
        frames.extend(_download_batch(batch, period))
        if i < len(batches) - 1:
            time.sleep(DOWNLOAD_BATCH_PAUSE)
        if (i + 1) % 10 == 0 or i == len(batches) - 1:
            print(f"  → batch {i + 1}/{len(batches)} verwerkt "
                  f"({len(frames)} tickers met data tot nu toe)")

    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"])
    df.sort_values(["Ticker", "Date"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

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

def analyseer_pair_ruw(ticker_a: str, ticker_b: str, data: pd.DataFrame, lookback: int = LOOKBACK) -> Optional[PairSignaal]:
    try:
        if ticker_a not in data.columns or ticker_b not in data.columns:
            return None
        if len(data[ticker_a].dropna()) < lookback or len(data[ticker_b].dropna()) < lookback:
            return None

        spread = data[ticker_a] / data[ticker_b]
        spread_mean = spread.rolling(lookback).mean()
        spread_std = spread.rolling(lookback).std()

        current_spread = spread.iloc[-1]
        current_mean = spread_mean.iloc[-1]
        current_std = spread_std.iloc[-1]

        if math.isnan(current_std) or current_std <= 0:
            return None

        z_score = (current_spread - current_mean) / current_std
        _, p_value, _ = coint(data[ticker_a], data[ticker_b])
        half_life = schat_half_life(spread)
        
        if half_life > MAX_HALF_LIFE or half_life <= 0:
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
            p_value=float(round(p_value, 4)), half_life=float(round(half_life, 1)),
            current_z=float(round(z_score, 2)), spread=float(round(current_spread, 4)),
            spread_mean=float(round(current_mean, 4)), spread_std=float(round(current_std, 4)),
            price_a=float(round(data[ticker_a].iloc[-1], 2)),
            price_b=float(round(data[ticker_b].iloc[-1], 2)),
            signal=signal, score=float(score_val),
        )
    except Exception:
        return None

def selecteer_gecointegreerde_paren(tickers: List[str], data: pd.DataFrame, corr_prefilter: float = CORR_PREFILTER, fdr_alpha: float = FDR_ALPHA, lookback: int = LOOKBACK) -> List[PairSignaal]:
    beschikbaar = [t for t in tickers if t in data.columns]
    if len(beschikbaar) < 2:
        return []

    sub = data[beschikbaar].dropna()
    if sub.shape[1] < 2 or len(sub) < lookback:
        return []

    corr_matrix = sub.corr().abs()
    kandidaten = [(a, b) for a, b in combinations(beschikbaar, 2) if a in corr_matrix.index and b in corr_matrix.columns and corr_matrix.loc[a, b] >= corr_prefilter]
    if not kandidaten:
        return []

    ruw: List[PairSignaal] = []
    for a, b in kandidaten:
        sig = analyseer_pair_ruw(a, b, sub, lookback)
        if sig is not None:
            ruw.append(sig)
    if not ruw:
        return []

    mask = fdr_correctie([s.p_value for s in ruw], fdr_alpha)
    significant = [s for s, keep in zip(ruw, mask) if keep]
    significant.sort(key=lambda s: s.score, reverse=True)
    return significant

def snel_zscore(ticker_a: str, ticker_b: str, data: pd.DataFrame, lookback: int = LOOKBACK) -> Optional[Tuple[float, float, float, float]]:
    if ticker_a not in data.columns or ticker_b not in data.columns:
        return None
    spread = data[ticker_a] / data[ticker_b]
    spread_mean = spread.rolling(lookback).mean()
    spread_std = spread.rolling(lookback).std()
    current_spread = spread.iloc[-1]
    current_mean = spread_mean.iloc[-1]
    current_std = spread_std.iloc[-1]
    if math.isnan(current_std) or current_std <= 0:
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

    top = signalen[:3]
    delen = [
        f"📊 *PAIRS TRADING — {exchange_name}*",
        f"_{nu} | {len(signalen)} actieve signals_",
        "─────────────────────────────",
        "🏆 *TOP SIGNALEN:*",
    ]

    for s in top:
        delen.append(f"• {signal_emoji(s.signal)} `{s.pair}`\n  Z-score: {s.current_z:.2f} | p={s.p_value:.4f} | Half-life: {s.half_life:.1f}d\n  Spread: {s.spread:.4f} (mean={s.spread_mean:.4f}, σ={s.spread_std:.4f})\n  {yahoo_link(s.ticker_a)} / {yahoo_link(s.ticker_b)} | Koers: {s.price_a:.2f} / {s.price_b:.2f}")

    if len(signalen) > 3:
        extra = signalen[3:]
        delen.append("─────────────────────────────\n📋 *Overige signalen:*")
        for s in extra:
            emoji = "🟢" if s.signal == "LONG" else "🔴" if s.signal == "SHORT" else "⚪"
            delen.append(f"  {emoji} `{s.pair}` | z={s.current_z:.2f} | p={s.p_value:.4f}")

    delen.append(f"\n⚙️ _Entry: ±{ENTRY_Z}σ | Exit: ±{EXIT_Z}σ | Lookback: {LOOKBACK}d | FDR α: {FDR_ALPHA}_")
    return "\n\n".join(delen)

def run_live_engine():
    print(f"{'='*60}\nPAIRS TRADING — LIVE  {today_str()}\n{'='*60}")

    exchange_tickers = {}
    all_tickers = []
    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist: continue
        ex_name = label_voor(f_name)
        exchange_tickers[ex_name] = tlist
        all_tickers.extend(tlist)

    all_tickers = sorted(set(all_tickers))
    if not all_tickers:
        print("[ERROR] Geen tickers.")
        return

    print(f"Totaal: {len(all_tickers)} unieke tickers | Data downloaden (2 jaar)...")
    df = download_history(all_tickers, period="2y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    pivot = df.pivot(index="Date", columns="Ticker", values="Close").dropna(axis=1, how='all')
    print(f"Data geladen: {len(pivot.columns)} tickers met voldoende data")

    email_delen: List[str] = []

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")
        signalen = selecteer_gecointegreerde_paren(tlist, pivot)
        top_signalen = signalen[:MAX_PAIRS]
        actieve_signalen = [s for s in top_signalen if s.signal != "NEUTRAL"]

        print(f"  → {len(signalen)} co-geintegreerde paren (na FDR-correctie)")
        print(f"  → {len(actieve_signalen)} actieve signalen")

        if actieve_signalen:
            for s in actieve_signalen[:3]:
                log_selectie(
                    ticker=s.pair, datum=today_str(), strategie="bot_01cointegr", beurs=ex_name, koers=s.spread,
                    parameters={
                        "signal": s.signal, "z_score": s.current_z, "p_value": s.p_value,
                        "half_life": s.half_life, "spread_mean": s.spread_mean, "spread_std": s.spread_std,
                        "price_a": s.price_a, "price_b": s.price_b, "score": s.score,
                        "grafiek": f"https://finance.yahoo.com/quote/{s.ticker_a}/chart?p={s.ticker_a}",
                    },
                )

            bericht = format_pairs_bericht(ex_name, actieve_signalen)
            if bericht:
                send_telegram(bericht, parse_mode="Markdown")
                email_delen.append(bericht)
                print(f"  → Telegram verstuurd: {ex_name}")

    if email_delen:
        send_email(f"PAIRS TRADING rapport {today_str()}", "\n\n" + ("=" * 40 + "\n\n").join(email_delen))

    print(f"\n{'='*60}\nKlaar.")

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
        print("[ERROR] Geen tickers gevonden.")
        return

    print(f"Tickers: {len(all_tickers)} over {len(exchange_tickers)} beurzen | Data downloaden (5y)...")
    df = download_history(all_tickers, period="5y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    pivot = df.pivot(index="Date", columns="Ticker", values="Close")
    pivot = pivot.dropna(axis=1, how='all')
    print(f"Data: {len(pivot.columns)} tickers | {len(pivot)} dagen")

    cash = START_CAPITAL
    positions: Dict[str, Dict] = {}
    trades: List[Dict] = []
    equity_curve: List[Dict] = []
    pair_cache: Dict[str, List[PairSignaal]] = {}
    laatste_selectie_idx = -(RESCAN_DAYS + 1)

    dates = sorted(pivot.index)
    start_idx = max(LOOKBACK, IS_WINDOW)
    print(f"Backtest van {dates[start_idx].date()} tot {dates[-1].date()}")

    for i, date in enumerate(dates):
        if i < start_idx:
            continue

        data_till = pivot.iloc[:i + 1]
        current_prices = pivot.iloc[i]

        # --- Exits (goedkope z-score-check, geen coint-hertest) ---
        for pair_key, pos in list(positions.items()):
            a, b = pair_key.split('/')
            if a not in current_prices or b not in current_prices:
                continue
            z_info = snel_zscore(a, b, data_till)
            if z_info is None:
                continue
            z = z_info[0]

            exit_signal = abs(z) < EXIT_Z or pos['days'] >= 60
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
            else:
                positions[pair_key]['days'] += 1

        # --- Walk-forward herselectie: enkel met data tot nu (in-sample venster) ---
        if i - laatste_selectie_idx >= RESCAN_DAYS:
            is_data = data_till.tail(IS_WINDOW)
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

                    z_info = snel_zscore(a, b, data_till)
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
            if (a in current_prices and b in current_prices
                    and math.isfinite(current_prices[a]) and math.isfinite(current_prices[b])
                    and current_prices[a] > 0 and current_prices[b] > 0):
                sign_a = 1 if pos['direction'] == 'LONG' else -1
                sign_b = -sign_a
                exit_a = float(current_prices[a]) * (1 - sign_a * SLIPPAGE_PCT)
                exit_b = float(current_prices[b]) * (1 - sign_b * SLIPPAGE_PCT)
                unrealized = (sign_a * (exit_a - pos['entry_a']) * pos['size_a']
                              + sign_b * (exit_b - pos['entry_b']) * pos['size_b'])
                estimated_exit_fees = (trade_cost(pos['size_a'] * exit_a)
                                       + trade_cost(pos['size_b'] * exit_b))
                portfolio_value += unrealized - estimated_exit_fees

        equity_curve.append({'date': date, 'value': portfolio_value})

    # --- Resultaten ---
    if trades:
        tdf = pd.DataFrame(trades)
        final_value = equity_curve[-1]['value'] if equity_curve else cash
        total_return = (final_value - START_CAPITAL) / START_CAPITAL * 100
        n_win = (tdf['net_pnl'] > 0).sum()
        win_rate = n_win / len(tdf) * 100

        gross_profit = tdf[tdf['net_pnl'] > 0]['net_pnl'].sum()
        gross_loss = abs(tdf[tdf['net_pnl'] <= 0]['net_pnl'].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        equity_df = pd.DataFrame(equity_curve)
        equity_df['peak'] = equity_df['value'].cummax()
        equity_df['drawdown'] = (equity_df['peak'] - equity_df['value']) / equity_df['peak']
        max_dd = equity_df['drawdown'].max() * 100

        os.makedirs('backtest_results', exist_ok=True)
        tdf.to_csv('backtest_results/backtest_cointegr.csv', index=False)

        print(f"\n{'='*60}")
        print(f"📊 BACKTEST RESULTATEN — bot_01cointegr (walk-forward)")
        print(f"{'='*60}")
        print(f"Startkapitaal     : EUR{START_CAPITAL:>12,.2f}")
        print(f"Eindkapitaal      : EUR{final_value:>12,.2f}")
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
            print(f"  {pair}: EUR{pnl:,.2f}")

        print("\n💾 Resultaten opgeslagen in backtest_results/backtest_cointegr.csv")
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
