#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seizoenseffecten: positieve historische dagrendementen, FDR per beurs/type.
Live historische check is beschrijvend, na selectie, geen onafhankelijke validatie.
Backtest traint uitsluitend op eerdere jaren; vaste afzonderlijke notional per trade.
"""

import sys
import math
import time
import warnings
import datetime as dt
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats

from common import (
    today_str, safe_float, yahoo_link, trade_cost,
    download_history, load_tickers_from_file, bouw_bestandslijst, label_voor,
    send_email
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

START_CAPITAL    = 50_000.0
SLIPPAGE_PCT     = 0.001
TRADE_COST_FIXED = 15.0
TRADE_COST_PCT   = 0.0035
TAX_RATE         = 0.0



SZ_CFG = {
    "lookback_years": 15,
    "min_jaren":      5,
    "fdr_alpha":      0.05,
    "top_n":          8,
}

BUCKET_TYPES = ("weekdag", "maand", "kwartaal")
WEEKDAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag"]
MAANDEN   = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]

BACKTEST_START      = "2021-01-01"
BACKTEST_END        = today_str()
BACKTEST_MIN_TRAIN  = 5

# ============================================================
# BENJAMINI-HOCHBERG FDR-CORRECTIE
# ============================================================

def bh_correctie(p_waarden: List[float], alpha: float) -> List[bool]:
    m = len(p_waarden)
    if m == 0:
        return []
    p_waarden = [p if math.isfinite(p) and 0 <= p <= 1 else 1.0 for p in p_waarden]
    volgorde = sorted(range(m), key=lambda i: p_waarden[i])
    laatste_significante_rank = -1
    for rang, idx in enumerate(volgorde, start=1):
        if p_waarden[idx] <= (rang / m) * alpha:
            laatste_significante_rank = rang
    mask = [False] * m
    if laatste_significante_rank >= 0:
        for rang, idx in enumerate(volgorde, start=1):
            if rang <= laatste_significante_rank:
                mask[idx] = True
    return mask

# ============================================================
# SEIZOENSANALYSE (live)
# ============================================================

@dataclass
class SeizoenKandidaat:
    ticker:     str
    price:      float
    weekdag:    Optional[dict]
    maand:      Optional[dict]
    kwartaal:   Optional[dict]
    jaren_data: float
    signal_date: str = ""
    target_date: str = ""

@dataclass
class SeizoenSignaal:
    ticker:               str
    price:                float
    score:                float
    weekdag:              Optional[dict]
    maand:                Optional[dict]
    kwartaal:             Optional[dict]
    significante_buckets: Set[str]
    jaren_data:           float
    checks:               Dict[str, dict] = field(default_factory=dict)
    signal_date: str = ""
    target_date: str = ""

def bereken_bucket_stats(rendementen: np.ndarray) -> Optional[dict]:
    rendementen = np.asarray(rendementen, dtype=float)
    rendementen = rendementen[np.isfinite(rendementen)]
    n = len(rendementen)
    if n < 5:
        return None
    gemiddelde = float(np.mean(rendementen))
    win_rate   = float(np.mean(rendementen > 0))
    if np.std(rendementen) <= np.finfo(float).eps:
        p_waarde = 1.0  # degenerate samples do not establish significance
    else:
        _, p_waarde = stats.ttest_1samp(rendementen, 0.0)
    return {"gemiddelde": gemiddelde, "win_rate": win_rate, "p_waarde": float(p_waarde), "n": n}

class DataUnavailable(ValueError):
    pass


def target_day():
    # Weekends advance to Monday; exchange holidays are not modelled.
    d = pd.Timestamp(today_str())
    while d.weekday() >= 5:
        d += pd.Timedelta(days=1)
    return d


def prepare_history(df, live=True):
    if df is None or df.empty or not {"Date", "Close"}.issubset(df.columns):
        raise DataUnavailable("geen gedateerde Close-historie")
    g = df.copy()
    dates = pd.to_datetime(g["Date"], errors="coerce")
    if dates.dt.tz is not None:
        dates = dates.dt.tz_localize(None)
    g["Date"] = dates.dt.normalize()
    g = g.loc[g.Date.notna() & (g.Date < pd.Timestamp(today_str()))]
    g = g.sort_values("Date").drop_duplicates("Date", keep="last").set_index("Date")
    g["Close"] = pd.to_numeric(g.Close, errors="coerce")
    valid = np.isfinite(g.Close) & (g.Close > 0)
    if g.empty or not valid.iloc[-1]:
        raise DataUnavailable("laatste slotkoers ontbreekt of ongeldig")
    # Preserve gaps as NaN: never compute a one-day return across a bad row.
    g.loc[~valid, "Close"] = np.nan
    if live and (pd.Timestamp(today_str()) - g.index[-1]).days > 7:
        raise DataUnavailable("laatste koers ouder dan zeven dagen")
    return g


def daily_returns(g):
    r = g.Close.pct_change(fill_method=None)
    return r.where(g.index.to_series().diff().dt.days <= 7).dropna()


def analyseer_ticker(ticker: str, df_ticker: pd.DataFrame) -> Optional[SeizoenKandidaat]:
    g = prepare_history(df_ticker)
    rendement = daily_returns(g)
    if len(rendement) < 250 * SZ_CFG["min_jaren"]:
        raise DataUnavailable("te weinig geldige rendementen")
    aantal_jaren = (rendement.index[-1] - rendement.index[0]).days / 365.25
    if aantal_jaren < SZ_CFG["min_jaren"]:
        raise DataUnavailable("minder dan vijf jaar historie")
    d = target_day()
    return SeizoenKandidaat(
        ticker=ticker, price=float(g.Close.iloc[-1]),
        weekdag=bereken_bucket_stats(rendement[rendement.index.weekday == d.weekday()].values),
        maand=bereken_bucket_stats(rendement[rendement.index.month == d.month].values),
        kwartaal=bereken_bucket_stats(rendement[rendement.index.quarter == d.quarter].values),
        jaren_data=aantal_jaren, signal_date=str(g.index[-1].date()), target_date=str(d.date()))

def pas_bh_toe_op_beurs(kandidaten: List[SeizoenKandidaat], alpha: float) -> List[SeizoenSignaal]:
    significant_per_kandidaat: Dict[int, Set[str]] = {}

    for bucket_key in BUCKET_TYPES:
        indices = [i for i, k in enumerate(kandidaten) if getattr(k, bucket_key) is not None]
        if not indices:
            continue
        p_waarden = [getattr(kandidaten[i], bucket_key)["p_waarde"] for i in indices]
        mask = bh_correctie(p_waarden, alpha)
        for pos, kandidaat_idx in enumerate(indices):
            if mask[pos] and getattr(kandidaten[kandidaat_idx], bucket_key)["gemiddelde"] > 0:
                significant_per_kandidaat.setdefault(kandidaat_idx, set()).add(bucket_key)

    resultaten: List[SeizoenSignaal] = []
    for i, k in enumerate(kandidaten):
        sig_keys = significant_per_kandidaat.get(i)
        if not sig_keys:
            continue
        score = sum(
            getattr(k, key)["gemiddelde"] * (1 - getattr(k, key)["p_waarde"])
            for key in sig_keys
        )
        resultaten.append(SeizoenSignaal(
            ticker=k.ticker, price=k.price, score=score,
            weekdag=k.weekdag, maand=k.maand, kwartaal=k.kwartaal,
            significante_buckets=sig_keys, jaren_data=k.jaren_data,
            signal_date=k.signal_date, target_date=k.target_date,
        ))
    return resultaten

# ============================================================
# OUTPUT
# ============================================================

def _bucket_regel(key: str, stat: Optional[dict], significante_buckets: Set[str], checks: Optional[Dict[str, dict]] = None) -> Optional[str]:
    if key not in significante_buckets or not stat:
        return None
    vandaag = target_day()
    if key == "weekdag":
        kort, label = "wk", WEEKDAGEN[vandaag.weekday()]
    elif key == "maand":
        kort, label = "mnd", MAANDEN[vandaag.month - 1]
    else:
        kort, label = "kw", f"Q{(vandaag.month - 1)//3 + 1}"
    regel = (f"{kort} {label}: {stat['gemiddelde']*100:+.2f}% "
             f"(winrate {stat['win_rate']*100:.0f}%, n={stat['n']}, p={stat['p_waarde']:.3f})")
    check = (checks or {}).get(key)
    if check:
        regel += (f" — beschrijvende check: {check['jaren_winst']}/{check['jaren_getest']}j positief, "
                  f"{check['n_trades']}x, PF " + (f"{check['pf']:.2f}" if check['pf'] is not None else "n.v.t. (geen verlies)"))
    else:
        regel += " — beschrijvende check: onvoldoende historische trades"
    return regel

def sig_regel(s: SeizoenSignaal) -> str:
    onderbouwing = [r for r in (
        _bucket_regel("weekdag", s.weekdag, s.significante_buckets, s.checks),
        _bucket_regel("maand", s.maand, s.significante_buckets, s.checks),
        _bucket_regel("kwartaal", s.kwartaal, s.significante_buckets, s.checks),
    ) if r]
    return (
        f"• `{s.ticker}` score {s.score*100:+.2f}% | {s.price:.4f} bronkoerseenheden | "
        f"koersdag {s.signal_date}, doelkalenderdag {s.target_date} | "
        f"{s.jaren_data:.0f}j data | {yahoo_link(s.ticker)}\n"
        f"  {' | '.join(onderbouwing)}"
    )

def format_bericht(exchange_name: str, top: List[SeizoenSignaal], alle: List[SeizoenKandidaat]) -> Optional[str]:
    if not alle:
        return None
    nu = today_str()
    delen = [
        f"📅 *SEIZOENSEFFECTEN — {exchange_name}*",
        f"_{nu} | {len(alle)} geanalyseerd | {len(top)} met FDR-significant seizoenspatroon_",
        "─────────────────────────────",
        "\n\n".join(sig_regel(s) for s in top),
        "─────────────────────────────",
        f"⚙️ _Weekdag/maand/kwartaal t.o.v. eigen historiek (max {SZ_CFG['lookback_years']}j) | "
        f"Benjamini-Hochberg FDR={SZ_CFG['fdr_alpha']:.2f} per beurs/buckettype_",
        "Statistieken zijn gemiddelde dagelijkse close-to-close rendementen binnen de bucket, "
        "geen maand-/kwartaalrendement of verwacht rendement na kosten. "
        "Historische check: achteraf gekozen patroon, geen onafhankelijke walk-forward-validatie. "
        "Eigen heuristiek; geen orders. Beursfeestdagen en afhankelijkheid tussen dagrendementen niet gemodelleerd.",
    ]
    return "\n\n".join(delen)

# ============================================================
# HISTORISCHE CHECK EN JAARLIJKS GETRAINDE BACKTEST
# Live: descriptieve check van achteraf gekozen buckets. Alleen run_backtest
# herberekent selectie/FDR met uitsluitend eerdere kalenderjaren.

def _train_bucket_stats_per_ticker(rendement: pd.Series, test_jaar: int, bucket_key: str) -> Dict[int, dict]:
    """Ruwe (ongecorrigeerde) bucket-stats op basis van de trainingsjaren."""
    train = rendement[rendement.index.year < test_jaar]
    if train.empty:
        return {}
    jaren_train = (train.index[-1] - train.index[0]).days / 365.25
    if jaren_train < BACKTEST_MIN_TRAIN:
        return {}

    if bucket_key == "maand":
        waarden, index_reeks = range(1, 13), train.index.month
    elif bucket_key == "kwartaal":
        waarden, index_reeks = range(1, 5), train.index.quarter
    else:  # weekdag
        waarden, index_reeks = range(0, 5), train.index.weekday

    resultaat = {}
    for waarde in waarden:
        stat = bereken_bucket_stats(train[index_reeks == waarde].values)
        if stat:
            resultaat[waarde] = stat
    return resultaat

def bucket_trades(bucket_key, waarde, g, test_jaar):
    """Dated close-to-close trades; month/quarter require a completed period.

    These hypothetical prior-close fills are calendar decisions known beforehand,
    not executions of the current intraperiod live report.
    """
    g = g.sort_index()
    result = []
    if bucket_key == "weekdag":
        for pos in range(1, len(g)):
            date, prev = g.index[pos], g.index[pos - 1]
            if date.year == test_jaar and prev.year >= test_jaar and date.weekday() == waarde and (date-prev).days <= 7:
                result.append((prev, date, g.Close.iloc[pos-1], g.Close.iloc[pos]))
    else:
        freq = "M" if bucket_key == "maand" else "Q"
        for period, data in g.groupby(g.index.to_period(freq)):
            val = period.month if bucket_key == "maand" else period.quarter
            if period.year != test_jaar or val != waarde:
                continue
            # Need observations around both calendar boundaries. No partial period exit.
            after = g.loc[g.index > period.end_time]
            before = g.loc[g.index < period.start_time]
            if after.empty or before.empty or len(data) < (5 if freq == "M" else 10):
                continue
            if data.Close.isna().any() or data.index.to_series().diff().dt.days.gt(7).any():
                continue
            prev, last = before.index[-1], data.index[-1]
            if ((data.index[0]-prev).days > 7 or (data.index[0]-period.start_time).days > 7
                    or (period.end_time.normalize()-last).days > 7 or (after.index[0]-last).days > 7):
                continue
            # Training uses prior calendar years: never enter before that year's model exists.
            if prev.year < test_jaar:
                continue
            result.append((prev, last, before.Close.iloc[-1], data.Close.iloc[-1]))
    return [(a,b,float(e)*(1+SLIPPAGE_PCT),float(x)*(1-SLIPPAGE_PCT))
            for a,b,e,x in result if np.isfinite(e) and np.isfinite(x) and e > 0 and x > 0]


def _trades_voor_bucket(bucket_key, waarde, g, test_jaar):
    return [(e,x) for _,_,e,x in bucket_trades(bucket_key, waarde, g, test_jaar)]

def _bereken_pnl(entry_price: float, exit_price: float) -> Dict:
    risico_bedrag = START_CAPITAL * 0.05
    if not all(math.isfinite(x) and x > 0 for x in (entry_price, exit_price)):
        raise ValueError("ongeldige uitvoeringskoers")
    aandelen = max(0, int(risico_bedrag / entry_price))
    while aandelen and entry_price * aandelen + trade_cost(entry_price * aandelen) > risico_bedrag:
        aandelen -= 1
    investering = entry_price * aandelen + trade_cost(entry_price * aandelen)
    gross = exit_price * aandelen
    cost  = trade_cost(gross)
    pnl   = gross - cost - investering
    tax   = pnl * TAX_RATE if pnl > 0 else 0.0
    net   = pnl - tax
    return {"size": aandelen, "pnl": pnl, "tax": tax, "net": net}

def _boek_trade(trades_list: List[Dict], ticker: str, jaar: int, bucket_key: str,
                 waarde: int, entry_price: float, exit_price: float, train_stat: dict, entry_date=None, exit_date=None, exchange="") -> None:
    r = _bereken_pnl(entry_price, exit_price)
    if not r["size"]:
        return
    trades_list.append({
        "beurs": exchange, "entry_date": entry_date, "exit_date": exit_date,
        "units": "synthetische indexeenheden (rebased 100)",
        "ticker": ticker, "jaar": jaar, "bucket_type": bucket_key, "bucket_waarde": waarde,
        "train_gemiddelde": train_stat["gemiddelde"], "train_p": train_stat["p_waarde"],
        "entry_price": entry_price, "exit_price": exit_price,
        "size": r["size"], "pnl": r["pnl"], "tax": r["tax"], "net": r["net"],
    })

def historische_check(bucket_key: str, waarde: int, g: pd.DataFrame) -> Optional[dict]:
    """Descriptive, post-selection history; no yearly selection/FDR in this check."""
    g = g.copy()
    valid = g.Close.dropna()
    if valid.empty:
        return None
    g["Close"] = g.Close / valid.iloc[0] * 100
    testjaren = range(pd.Timestamp(BACKTEST_START).year, pd.Timestamp(today_str()).year + 1)
    resultaten_per_jaar: Dict[int, float] = {}
    n_trades = 0
    netto_totaal = 0.0
    trade_nets = []

    for jaar in testjaren:
        netto_dit_jaar = 0.0
        gevonden = False
        for entry_price, exit_price in _trades_voor_bucket(bucket_key, waarde, g, jaar):
            r = _bereken_pnl(entry_price, exit_price)
            if not r["size"]:
                continue
            trade_nets.append(r["net"])
            netto_dit_jaar += r["net"]
            netto_totaal   += r["net"]
            n_trades += 1
            gevonden = True
        if gevonden:
            resultaten_per_jaar[jaar] = netto_dit_jaar

    if not resultaten_per_jaar:
        return None

    jaren_getest  = len(resultaten_per_jaar)
    jaren_winst   = sum(1 for v in resultaten_per_jaar.values() if v > 0)
    winst_totaal = sum(v for v in trade_nets if v > 0)
    verlies_totaal = abs(sum(v for v in trade_nets if v < 0))
    pf = winst_totaal / verlies_totaal if verlies_totaal else None

    return {
        "jaren_getest": jaren_getest, "jaren_winst": jaren_winst,
        "n_trades": n_trades, "netto_totaal": round(netto_totaal, 2), "pf": pf, "method": "beschrijvend na selectie; synthetische indexbasis 100",
    }

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    reports, issues, errors = [], [], []
    exchange_tickers, requested = {}, set()
    for filename in bouw_bestandslijst():
        try:
            tickers = sorted(set(load_tickers_from_file(filename)))
            if not tickers:
                issues.append(f"{filename}: ontbrekende of lege tickerlijst")
                continue
            exchange_tickers[label_voor(filename)] = tickers
            requested.update(tickers)
        except Exception as exc:
            errors.append(f"{filename}: laden mislukt ({type(exc).__name__})")
    frames = []
    for start in range(0, len(requested), 100):
        batch = sorted(requested)[start:start+100]
        try:
            frames.append(download_history(batch, period=f"{SZ_CFG['lookback_years']}y"))
        except Exception as exc:
            errors.append(f"batch {start//100+1}: download mislukt ({type(exc).__name__})")
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    groups = dict(tuple(df.groupby("Ticker"))) if "Ticker" in df else {}
    cache, histories = {}, {}
    for ticker in sorted(requested):
        try:
            raw = groups.get(ticker, pd.DataFrame())
            cache[ticker] = analyseer_ticker(ticker, raw)
            histories[ticker] = prepare_history(raw)
            if raw.empty or len(histories[ticker]) < len(raw):
                issues.append(f"{ticker}: dubbele, ongedateerde of onvoltooide rijen verwijderd")
            if histories[ticker].Close.isna().any():
                issues.append(f"{ticker}: ongeldige historische slotkoersen; rendementen rond gaten uitgesloten")
        except DataUnavailable as exc:
            issues.append(f"{ticker}: {exc}")
        except Exception as exc:
            errors.append(f"{ticker}: analyse mislukt ({type(exc).__name__}: {exc})")
    stored = selected = 0
    check_cache = {}
    for exchange, tickers in exchange_tickers.items():
        candidates = [cache[t] for t in tickers if cache.get(t) is not None]
        signals = sorted(pas_bh_toe_op_beurs(candidates, SZ_CFG["fdr_alpha"]),
                         key=lambda x: (-x.score, x.ticker))
        top = signals[:SZ_CFG["top_n"]]
        selected += len(top)
        coverage = f"{exchange}: {len(candidates)}/{len(tickers)} geanalyseerd, {len(signals)} kandidaten, {len(top)} topselecties."
        print(coverage, flush=True)
        for sig in top:
            d = pd.Timestamp(sig.target_date)
            for key in sorted(sig.significante_buckets):
                value = {"weekdag": d.weekday(), "maand": d.month, "kwartaal": d.quarter}[key]
                ck = (sig.ticker, key, value)
                if ck not in check_cache:
                    check_cache[ck] = historische_check(key, value, histories[sig.ticker])
                if check_cache[ck]:
                    sig.checks[key] = check_cache[ck]
            parameters = {"score": sig.score, "grafiek": yahoo_link(sig.ticker),
                "jaren_data": sig.jaren_data, "significante_buckets": sorted(sig.significante_buckets),
                "historische_check": sig.checks, "signal_date": sig.signal_date,
                "target_date": sig.target_date, "statistiek": "dagrendementen binnen kalenderbucket"}
            for key in BUCKET_TYPES:
                stat = getattr(sig, key)
                for label, fieldname in (("gemiddelde","gemiddelde"),("winrate","win_rate"),("p","p_waarde")):
                    parameters[f"{key}_{label}"] = stat[fieldname] if stat else None
            try:
                if not log_selectie(ticker=sig.ticker, datum=today_str(), strategie="bot_01repititief",
                                    beurs=exchange, koers=sig.price, parameters=parameters):
                    raise RuntimeError("geen opslagbevestiging")
                stored += 1
            except Exception as exc:
                errors.append(f"{sig.ticker}: database mislukt ({exc})")
        reports.append(coverage + "\n" + (format_bericht(exchange, top, candidates) or "Geen bruikbare analyses."))
    count = sum(v is not None for v in cache.values())
    if not count:
        errors.append("Geen bruikbare analyses")
    summary = f"{count}/{len(requested)} unieke tickers geanalyseerd. Database {stored}/{selected} selecties opgeslagen. Telegram uit."
    body = "\n\n".join([summary] + reports + ["Databeperkingen:"] + issues + ["Fouten:"] + errors)
    print(body, flush=True)
    if not send_email(("[ONVOLLEDIG] " if issues or errors else "") + "Seizoenseffecten " + today_str(), body):
        errors.append("E-mail niet verzonden")
    if errors:
        raise RuntimeError(f"{len(errors)} fout(en): " + "; ".join(errors[:5]))
    print("Klaar — e-mail verzonden.", flush=True)

# ============================================================
# BACKTEST ENGINE (hersteld uit v1.2/v1.3)
# ============================================================
# FDR per beurs, jaar, buckettype en waarde; positieve effecten na correctie.

TRADE_COLUMNS = ["beurs", "ticker", "jaar", "bucket_type", "bucket_waarde",
                 "entry_date", "exit_date", "units", "train_gemiddelde", "train_p",
                 "entry_price", "exit_price", "size", "pnl", "tax", "net"]


def run_backtest():
    exchanges = {}
    for filename in bouw_bestandslijst():
        tickers = sorted(set(load_tickers_from_file(filename)))
        if tickers:
            exchanges[label_voor(filename)] = tickers
    tickers = sorted({t for ts in exchanges.values() for t in ts})
    if not tickers:
        raise RuntimeError("Geen tickers voor backtest")
    # Explicit dates include warmup and keep the user's end date inclusive.
    end = min(pd.Timestamp(BACKTEST_END), pd.Timestamp(today_str()) - pd.Timedelta(days=1))
    begin = pd.Timestamp(BACKTEST_START)
    frames = [download_history(tickers[i:i+100],
              start=str((begin-pd.DateOffset(years=SZ_CFG['lookback_years'])).date()),
              end=str((end+pd.Timedelta(days=1)).date())) for i in range(0,len(tickers),100)]
    df = pd.concat(frames, ignore_index=True)
    if df.empty or "Ticker" not in df:
        raise RuntimeError("Geen backtestdata")
    series = {}
    for ticker, group in df.groupby("Ticker", sort=True):
        try:
            g = prepare_history(group, live=False).loc[:end].copy()
            if g.empty:
                continue
            # Basis is fixed before the first test year; all currencies become
            # synthetic indices, not a claimed FX conversion or actual share count.
            base = g.loc[g.index < begin, "Close"].dropna()
            if base.empty:
                continue
            g["Close"] = g.Close / base.iloc[-1] * 100
            series[ticker] = (daily_returns(g), g)
        except DataUnavailable as exc:
            print(f"[DATA] {ticker}: {exc}", flush=True)
    trades = []
    for exchange, members in exchanges.items():
        for year in range(begin.year, end.year + 1):
            for key in BUCKET_TYPES:
                trained = {t: _train_bucket_stats_per_ticker(series[t][0], year, key)
                           for t in members if t in series}
                values = {"weekdag": range(5), "maand": range(1,13), "kwartaal": range(1,5)}[key]
                for value in values:
                    tested = [t for t in sorted(trained) if value in trained[t]]
                    mask = bh_correctie([trained[t][value]["p_waarde"] for t in tested], SZ_CFG["fdr_alpha"])
                    for ticker, significant in zip(tested, mask):
                        stat = trained[ticker][value]
                        if not significant or stat["gemiddelde"] <= 0:
                            continue
                        for entry_date, exit_date, entry, exit_ in bucket_trades(key, value, series[ticker][1], year):
                            if entry_date < begin or exit_date > end:
                                continue
                            _boek_trade(trades, ticker, year, key, value, entry, exit_, stat,
                                        str(entry_date.date()), str(exit_date.date()), exchange)
        print(f"[BACKTEST] {exchange} afgerond", flush=True)
    tdf = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    tdf.to_csv("seizoen_backtest_trades.csv", index=False)
    print(f"{len(tdf)} trades opgeslagen: seizoen_backtest_trades.csv", flush=True)
    print("Afzonderlijke hypothetische posities, indexbasis 100 per ticker; 2500 synthetische "
          "eenheden inclusief aankoopkosten per trade; 0,1% slippage per kant, "
          "kosten max(15, 0,35% notional) per kant; geen belastingaanname. "
          "Geen gedeelde cash, compounding, portefeuillerendement of totale EUR-winst. "
          "Huidige tickerlijsten en aangepaste historische koersen kunnen survivorship/revision bias bevatten.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    elif mode == "live":
        run_live_engine()
    else:
        raise SystemExit("Gebruik live of backtest")
