#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
a_trade_combi.py — GEWOGEN KWALITEITSSCORE x VOLATILITEIT v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Heuristische gewichten uit gemiddelde/spreiding van forward returns per selectiedatum.
"""

from common.sel_par_inputs import sel_par_query

import os
import argparse
import time
import math
import logging
import re
import pandas as pd
from typing import Dict, List, Set, Tuple

import psycopg2
import psycopg2.extras

from common import (
    today_str,
    download_history,
    load_tickers_from_file,
    bouw_bestandslijst,
    label_voor,
    send_telegram,
    send_email,
    compute_atr_series,
)

from strats import bot_01kasstr as kasstr
from strats import bot_00Fisher as fisher
from strats import bot_01repititief as repititief
from strats import bot_00vcp as vcp
from strats import bot_01hoogl as hoogl
from common import db_logger
from .shared import finite
from research.signals import SelectionSignal

# ============================================================
# CONFIG
# ============================================================

MIN_ATR_PCT = float(os.getenv("MIN_ATR_PCT", "4.0"))
MAX_ATR_PCT = float(os.getenv("MAX_ATR_PCT", "25.0"))
TOP_N = int(os.getenv("TOP_N", "10"))

RETRY_POGINGEN = int(os.getenv("RETRY_POGINGEN", "3"))
RETRY_BASIS_WACHT = float(os.getenv("RETRY_BASIS_WACHT", "8"))

WARMUP_POGINGEN = int(os.getenv("WARMUP_POGINGEN", "2"))
WARMUP_WACHT = float(os.getenv("WARMUP_WACHT", "15"))

GEWICHTEN_FALLBACK = {
    "kasstr": 0.85,
    "fisher": 0.55,
    "vcp": 0.55,
    "hoogl": 0.50,
    "repititief": 0.15,
}

FORCE_STATIC_GEWICHTEN = os.getenv("FORCE_STATIC_GEWICHTEN", "0") == "1"
MIN_OBSERVATIES = int(os.getenv("MIN_OBSERVATIES", "30"))
GEWICHT_HORIZON = os.getenv("GEWICHT_HORIZON", "10d")
GEWICHTEN_MIN = 0.05
GEWICHTEN_MAX = 1.00

STRATEGIE_LABELS = {
    "kasstr": "bot_01kasstr",
    "fisher": "bot_00Fisher",
    "vcp": "bot_00vcp",
    "hoogl": "bot_01hoogl",
    "repititief": "bot_01repititief",
}

STRATEGIE_DB_NAAM = {
    "kasstr": "bot_01kasstr",
    "fisher": "bot_00Fisher",
    "vcp": "bot_00vcp",
    "hoogl": "bot_01hoogl",
    "repititief": "bot_01repititief",
}

# ============================================================
# HULPFUNCTIES
# ============================================================


def bouw_exchange_tickers() -> Tuple[Dict[str, List[str]], List[str]]:
    exchange_tickers: Dict[str, List[str]] = {}
    all_tickers: List[str] = []
    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist:
            continue
        ex_name = label_voor(f_name)
        exchange_tickers[ex_name] = tlist
        all_tickers.extend(tlist)
    all_tickers = sorted(set(all_tickers))
    return exchange_tickers, all_tickers


def _yahoo_link(ticker: str) -> str:
    return f"https://finance.yahoo.com/quote/{ticker}"


class YahooSourceUnavailable(RuntimeError):
    """Provider failure, including errors swallowed by a strategy or yfinance."""


def _provider_failure(message):
    text = str(message).casefold()
    if "invalid crumb" in text or re.search(r"\b(401|403)\b", text):
        return "Yahoo weigert toegang tot fundamentals (401/403/Invalid Crumb)"
    if any(term in text for term in ("too many requests", "rate limit", "ratelimit")) or re.search(r"\b429\b", text):
        return "Yahoo beperkt fundamentals-verzoeken (429/rate limit)"
    return None


class _YahooErrors(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.failure = None

    def emit(self, record):
        self.failure = self.failure or _provider_failure(record.getMessage())


def fundamental_call(fn):
    # yfinance can log HTTP failures and return incomplete info instead of raising.
    # Inspect these errors around the call, even if the strategy catches exceptions.
    logger = logging.getLogger("yfinance")
    handler = _YahooErrors()
    old_level = logger.level
    if logger.getEffectiveLevel() > logging.ERROR:
        logger.setLevel(logging.ERROR)
    logger.addHandler(handler)
    try:
        try:
            result = fn()
        except Exception as exc:
            failure = handler.failure or _provider_failure(exc)
            if failure:
                raise YahooSourceUnavailable(failure) from exc
            raise
        if handler.failure:
            raise YahooSourceUnavailable(handler.failure)
        return result
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


def met_retry(fn, ticker: str, label: str):
    for poging in range(RETRY_POGINGEN):
        try:
            return fundamental_call(fn)
        except Exception as e:
            if isinstance(e, (kasstr.DataUnavailable, repititief.DataUnavailable)):
                print(f"  [DATA] {label} {ticker}: {e}", flush=True)
                return None
            is_rate_limit = any(
                part in str(e).casefold()
                for part in ("too many requests", "rate limit", "429")
            )
            laatste_poging = poging == RETRY_POGINGEN - 1
            if is_rate_limit and not laatste_poging:
                wacht = RETRY_BASIS_WACHT * (2**poging)
                print(
                    f"  [retry] {label} {ticker}: rate limited, wacht {wacht:.0f}s (poging {poging + 1}/{RETRY_POGINGEN})..."
                )
                time.sleep(wacht)
                continue
            raise RuntimeError(
                f"{label} {ticker}: aanvraag mislukt na herpogingen ({e})"
            ) from e
    return None


def warm_up_yfinance() -> bool:
    for poging in range(WARMUP_POGINGEN):
        try:
            test = download_history(["AAPL"], period="5d")
            if test is not None and not test.empty:
                print(
                    f"[opwarm] Yahoo-sessie actief na poging {poging + 1}/{WARMUP_POGINGEN}"
                )
                return True
        except Exception as e:
            print(f"[opwarm] poging {poging + 1}/{WARMUP_POGINGEN} gaf fout: {e}")
        if poging < WARMUP_POGINGEN - 1:
            print(
                f"[opwarm] geen data, wacht {WARMUP_WACHT:.0f}s voor volgende poging..."
            )
            time.sleep(WARMUP_WACHT)
    print("[opwarm] Yahoo blijft ontoegankelijk na alle opwarm-pogingen.")
    return False


# ============================================================
# DYNAMISCHE GEWICHTEN
# ============================================================


def herbereken_gewichten() -> Dict[str, float]:
    if FORCE_STATIC_GEWICHTEN:
        print("[gewichten] FORCE_STATIC_GEWICHTEN=1 — statische fallback gebruikt.")
        return dict(GEWICHTEN_FALLBACK)

    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print("[gewichten] SUPABASE_DB_URL ontbreekt — statische fallback gebruikt.")
        return dict(GEWICHTEN_FALLBACK)

    if GEWICHT_HORIZON not in {"5d", "10d", "20d", "30d", "60d"}:
        raise ValueError("Ongeldige GEWICHT_HORIZON")
    target_kolom = f"fwd_ret_{GEWICHT_HORIZON}"
    cutoff = (
        (
            pd.Timestamp(today_str())
            - pd.Timedelta(days=math.ceil(int(GEWICHT_HORIZON[:-1]) * 7 / 5) + 2)
        )
        .date()
        .isoformat()
    )
    db_namen = list(STRATEGIE_DB_NAAM.values())

    query = f"""
        WITH per_day AS (
            SELECT strategie,datum,AVG({target_kolom}) AS day_return
            FROM forward_returns
            WHERE datum <= %(cutoff)s AND {target_kolom} > '-Infinity'::float8
              AND {target_kolom} < 'Infinity'::float8
              AND strategie = ANY(%(strats)s)
            GROUP BY strategie,datum
        )
        SELECT strategie,COUNT(*) AS n,AVG(day_return) AS gem,STDDEV(day_return) AS std
        FROM per_day GROUP BY strategie
    """

    try:
        with psycopg2.connect(db_url, connect_timeout=15) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sel_par_query(query), {"strats": db_namen, "cutoff": cutoff})
                rows = cur.fetchall()
    except Exception as e:
        print(f"[gewichten] DB-query faalde: {e} — statische fallback gebruikt.")
        return dict(GEWICHTEN_FALLBACK)

    db_naar_key = {v: k for k, v in STRATEGIE_DB_NAAM.items()}
    ratio_per_key: Dict[str, float] = {}
    n_per_key: Dict[str, int] = {}

    for r in rows:
        db_naam = r["strategie"]
        key = db_naar_key.get(db_naam)
        if key is None:
            continue
        n = int(r["n"] or 0)
        n_per_key[key] = n
        if n < MIN_OBSERVATIES:
            continue
        gem = float(r["gem"]) if r["gem"] is not None else 0.0
        std = float(r["std"]) if r["std"] is not None else 0.0
        if not math.isfinite(gem) or not math.isfinite(std) or std <= 1e-9:
            continue
        ratio_per_key[key] = gem / std

    print("\n" + "=" * 70)
    print(
        f"DYNAMISCHE GEWICHTEN (horizon={GEWICHT_HORIZON}, min_datums={MIN_OBSERVATIES})"
    )
    print("=" * 70)
    print(f"  {'Strategie':<14} {'n':>7} {'Gem/std':>9} {'Gewicht':>9}  Status")
    print("  " + "-" * 60)

    if not ratio_per_key:
        print("  Geen enkele strategie met voldoende observaties — fallback.")
        for key, gewicht in GEWICHTEN_FALLBACK.items():
            n = n_per_key.get(key, 0)
            print(f"  {key:<14} {n:>7} {'-':>9} {gewicht:>9.3f}  fallback")
        print("=" * 70)
        return dict(GEWICHTEN_FALLBACK)

    ratio_waarden = [value for value in ratio_per_key.values() if value > 0]
    min_s = 0.0
    max_s = max(ratio_waarden, default=0.0)
    bereik = max_s - min_s

    gewichten: Dict[str, float] = {}
    for key, ratio in ratio_per_key.items():
        if ratio <= 0:
            gewichten[key] = 0.0
            continue
        if bereik < 1e-9:
            gewicht = (GEWICHTEN_MIN + GEWICHTEN_MAX) / 2
        else:
            genorm = (ratio - min_s) / bereik
            gewicht = GEWICHTEN_MIN + genorm * (GEWICHTEN_MAX - GEWICHTEN_MIN)
        gewichten[key] = round(gewicht, 3)

    for key, fallback in GEWICHTEN_FALLBACK.items():
        if key not in gewichten:
            gewichten[key] = fallback

    for key in GEWICHTEN_FALLBACK:
        n = n_per_key.get(key, 0)
        ratio_str = f"{ratio_per_key[key]:.4f}" if key in ratio_per_key else "-"
        status = "dynamisch" if key in ratio_per_key else "fallback"
        print(f"  {key:<14} {n:>7} {ratio_str:>9} {gewichten[key]:>9.3f}  {status}")

    print("=" * 70)
    return gewichten


# ============================================================
# SELECTIES
# ============================================================


def download_universe(tickers, period):
    frames = []
    ordered = sorted(set(tickers))
    for start in range(0, len(ordered), 100):
        batch = ordered[start : start + 100]
        print(
            f"[DOWNLOAD] {period}: {start + 1}..{start + len(batch)}/{len(ordered)}",
            flush=True,
        )
        frames.append(download_history(batch, period=period, request_timeout=15))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def prepare_daily(group):
    required = ["Open", "High", "Low", "Close", "Volume"]
    if not {"Date", *required}.issubset(group.columns):
        raise ValueError("OHLCV-velden ontbreken")
    g = group.copy()
    dates = pd.to_datetime(g.Date, errors="coerce")
    if dates.dt.tz is not None:
        dates = dates.dt.tz_localize(None)
    g["Date"] = dates.dt.normalize()
    g = g.loc[g.Date.notna() & (g.Date < pd.Timestamp(today_str()))]
    g = g.sort_values("Date").drop_duplicates("Date", keep="last")
    if g.empty or (pd.Timestamp(today_str()) - g.Date.iloc[-1]).days > 7:
        raise ValueError("geen recente afgeronde koersdag")
    g[required] = g[required].apply(pd.to_numeric, errors="coerce")
    valid = (
        g[required]
        .apply(lambda col: col.map(lambda x: finite(x) is not None))
        .all(axis=1)
    )
    valid &= (g[required[:4]] > 0).all(axis=1) & (g.Volume >= 0)
    valid &= (g.High >= g[["Open", "Low", "Close"]].max(axis=1)) & (
        g.Low <= g[["Open", "High", "Close"]].min(axis=1)
    )
    if not valid.iloc[-1]:
        raise ValueError("laatste OHLCV ongeldig")
    broken = (~valid) | g.Date.diff().dt.days.gt(7)
    if broken.any():
        last = int(broken.to_numpy().nonzero()[0][-1])
        g = g.iloc[last + (0 if valid.iloc[last] else 1) :]
    return g.reset_index(drop=True)


def compute_atr_pct(exchange_tickers, all_tickers, quotes=None, data=None):
    df = download_universe(all_tickers, "2y") if data is None else data
    if df.empty:
        return {}
    result = {}
    quotes = quotes if quotes is not None else {}
    for ticker, raw in df.groupby("Ticker", sort=True):
        try:
            g = prepare_daily(raw)
            if len(g) < 15:
                raise ValueError("minder dan 15 geldige koersdagen")
            atr = finite(compute_atr_series(g.High, g.Low, g.Close, 14).iloc[-1])
            price = float(g.Close.iloc[-1])
            if atr is None or atr <= 0:
                raise ValueError("ATR14 onbruikbaar")
            result[ticker] = atr / price * 100
            quotes[ticker] = (price, g.Date.iloc[-1].date().isoformat())
        except ValueError as exc:
            print(f"[DATA] ATR {ticker}: {exc}", flush=True)
    return result


def selecties_repititief(exchange_tickers, all_tickers) -> Dict[str, Set[str]]:
    lookback = repititief.SZ_CFG["lookback_years"]
    print(f"[repititief] Koersdata ({lookback}y)...")
    df = download_universe(all_tickers, period=f"{lookback}y")
    if df.empty:
        return {}
    wanted = {ticker for members in exchange_tickers.values() for ticker in members}
    analyses = {}
    for ticker, group in df.groupby("Ticker", sort=False):
        if ticker not in wanted:
            continue
        try:
            analyses[ticker] = repititief.analyseer_ticker(ticker, group)
        except repititief.DataUnavailable as exc:
            print(f"[DATA] seizoenseffecten {ticker}: {exc}", flush=True)
            analyses[ticker] = None
    result: Dict[str, Set[str]] = {}
    for ex_name, tlist in exchange_tickers.items():
        members = set(tlist)
        kandidaten = [candidate for ticker, candidate in analyses.items()
                      if ticker in members and candidate is not None]
        if not kandidaten:
            result[ex_name] = set()
            continue
        fdr_significant = repititief.pas_bh_toe_op_beurs(
            kandidaten, repititief.SZ_CFG["fdr_alpha"]
        )
        result[ex_name] = {s.ticker for s in fdr_significant}
    return result


def selecties_vcp(exchange_tickers, all_tickers, data=None) -> Dict[str, Set[str]]:
    print("[vcp] Koersdata (2y)...")
    df = download_universe(all_tickers, period="2y") if data is None else data
    if df.empty:
        return {}
    wanted = {ticker for members in exchange_tickers.values() for ticker in members}
    geselecteerd = set()
    for ticker, group in df.groupby("Ticker", sort=True):
        if ticker not in wanted:
            continue
        try:
            clean = prepare_daily(group)
        except ValueError as exc:
            print(f"[DATA] VCP {ticker}: {exc}", flush=True)
            continue
        if vcp.analyse_ticker(ticker, clean):
            geselecteerd.add(ticker)
    return {exchange: geselecteerd.intersection(tickers)
            for exchange, tickers in exchange_tickers.items()}


def selecties_kasstr(shortlist_per_beurs: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    totaal = sum(len(s) for s in shortlist_per_beurs.values())
    print(
        f"[kasstr] Fundamentals per ticker (live yfinance-calls) — {totaal} tickers op shortlist..."
    )
    result: Dict[str, Set[str]] = {}
    cache = {}
    fx_cache = {}
    for ex_name, shortlist in shortlist_per_beurs.items():
        geselecteerd = set()
        for ticker in sorted(shortlist):
            if ticker not in cache:
                cache[ticker] = met_retry(
                    lambda t=ticker: kasstr.analyse_ticker(t, fx_cache=fx_cache),
                    ticker,
                    "kasstr",
                )
                time.sleep(0.05)
            sig = cache[ticker]
            if sig is not None and sig.score >= kasstr.FCF_CFG["min_score"]:
                geselecteerd.add(ticker)
        result[ex_name] = geselecteerd
    if cache and not any(sig is not None for sig in cache.values()):
        raise RuntimeError("kasstr: geen bruikbare fundamentele analyses; scan onvolledig")
    return result


def selecties_fisher(shortlist_per_beurs: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    totaal = sum(len(s) for s in shortlist_per_beurs.values())
    print(
        f"[fisher] Fundamentals per ticker (live yfinance-calls) — {totaal} tickers op shortlist..."
    )
    cfg = fisher.FISHER_CFG
    result: Dict[str, Set[str]] = {}
    cache = {}
    for ex_name, shortlist in shortlist_per_beurs.items():
        geselecteerd = set()
        for ticker in sorted(shortlist):
            if ticker not in cache:
                cache[ticker] = met_retry(
                    lambda t=ticker: fisher.analyse_ticker(t, cfg), ticker, "fisher"
                )
                time.sleep(cfg["throttle_sec"])
            sig = cache[ticker]
            if sig is not None and sig.score >= cfg["min_score"]:
                geselecteerd.add(ticker)
        result[ex_name] = geselecteerd
    if cache and not any(sig is not None for sig in cache.values()):
        raise RuntimeError("fisher: geen bruikbare fundamentele analyses; scan onvolledig")
    return result


def _hoogl_analyse_met_retry(ticker: str, cfg: dict, fx_cache=None):
    for poging in range(RETRY_POGINGEN):
        sig, was_rate_limited = fundamental_call(
            lambda: hoogl.analyse_ticker(ticker, cfg, fx_cache=fx_cache)
        )
        if not was_rate_limited:
            return sig
        if poging < RETRY_POGINGEN - 1:
            wacht = RETRY_BASIS_WACHT * (2**poging)
            print(
                f"  [retry] hoogl {ticker}: rate limited, wacht {wacht:.0f}s (poging {poging + 1}/{RETRY_POGINGEN})..."
            )
            time.sleep(wacht)
    raise RuntimeError(f"Hoogland {ticker}: Yahoo-limiet na herpogingen")


def selecties_hoogl(shortlist_per_beurs: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    totaal = sum(len(s) for s in shortlist_per_beurs.values())
    print(
        f"[hoogl] Fundamentals per ticker (live yfinance-calls) — {totaal} tickers op shortlist..."
    )
    cfg = hoogl.MODUS_CFG["live"]
    result: Dict[str, Set[str]] = {}
    cache = {}
    fx_cache = {}
    for ex_name, shortlist in shortlist_per_beurs.items():
        geselecteerd = set()
        for ticker in sorted(shortlist):
            if ticker not in cache:
                cache[ticker] = _hoogl_analyse_met_retry(ticker, cfg, fx_cache=fx_cache)
                time.sleep(cfg["throttle_sec"])
            sig = cache[ticker]
            if sig is not None and sig.score >= cfg["min_score"]:
                geselecteerd.add(ticker)
        result[ex_name] = geselecteerd
    if cache and not any(sig is not None for sig in cache.values()):
        raise RuntimeError("hoogl: geen bruikbare fundamentele analyses; scan onvolledig")
    return result


# ============================================================
# MAIN
# ============================================================


def _run_live_engine():
    print(f"{'=' * 60}")
    print(f"COMBI-SELECTIE VOLATIEL v2.0 (dynamische gewichten)  {today_str()}")
    print(f"  ATR% tussen {MIN_ATR_PCT} en {MAX_ATR_PCT}")
    print(f"{'=' * 60}")

    if not (
        math.isfinite(MIN_ATR_PCT)
        and math.isfinite(MAX_ATR_PCT)
        and 0 < MIN_ATR_PCT <= MAX_ATR_PCT
        and TOP_N > 0
        and RETRY_POGINGEN > 0
        and WARMUP_POGINGEN > 0
        and MIN_OBSERVATIES >= 2
        and math.isfinite(RETRY_BASIS_WACHT)
        and RETRY_BASIS_WACHT >= 0
        and math.isfinite(WARMUP_WACHT)
        and WARMUP_WACHT >= 0
    ):
        raise ValueError("Ongeldige ATR-, retry- of TOP_N-configuratie")
    if not os.getenv("SUPABASE_DB_URL"):
        raise RuntimeError("SUPABASE_DB_URL ontbreekt; scan niet gestart")
    gewichten = herbereken_gewichten()
    print(f"Gewichten: {gewichten}\n")

    exchange_tickers, all_tickers = bouw_exchange_tickers()
    if not all_tickers:
        raise RuntimeError("Geen tickerbestanden gevonden")
    print(
        f"Totaal universum: {len(all_tickers)} unieke tickers over {len(exchange_tickers)} beurzen\n"
    )

    if not warm_up_yfinance():
        raise RuntimeError("Yahoo niet beschikbaar; scan gestopt")
    technical = download_universe(all_tickers, "2y")
    quotes = {}
    atr_pct = compute_atr_pct(
        exchange_tickers, all_tickers, quotes=quotes, data=technical
    )
    if not atr_pct:
        raise RuntimeError("Geen bruikbare ATR-analyses")
    storage_errors = []

    per_strategie: Dict[str, Dict[str, Set[str]]] = {}
    per_strategie["repititief"] = selecties_repititief(exchange_tickers, all_tickers)
    per_strategie["vcp"] = selecties_vcp(exchange_tickers, all_tickers, data=technical)

    shortlist_per_beurs: Dict[str, Set[str]] = {}
    for ex_name in exchange_tickers:
        shortlist = set()
        for strat_key in ("repititief", "vcp"):
            shortlist |= per_strategie[strat_key].get(ex_name, set())
        shortlist_per_beurs[ex_name] = shortlist
    totaal_shortlist = sum(len(s) for s in shortlist_per_beurs.values())
    print(
        f"\n[trechter] {totaal_shortlist} tickers op de shortlist (van {len(all_tickers)} in het volledige universum)\n"
    )

    for key, selector in (
        ("kasstr", selecties_kasstr),
        ("fisher", selecties_fisher),
        ("hoogl", selecties_hoogl),
    ):
        if gewichten.get(key, 0) > 0 and totaal_shortlist:
            per_strategie[key] = selector(shortlist_per_beurs)
        else:
            print(f"[{key}] overgeslagen: gewicht nul of lege shortlist", flush=True)
            per_strategie[key] = {}

    email_delen: List[str] = []

    for ex_name, tlist in exchange_tickers.items():
        gewogen_score: Dict[str, float] = {}
        bijdragen: Dict[str, List[str]] = {}
        for strat_key, per_ex in per_strategie.items():
            geselecteerd = per_ex.get(ex_name, set())
            gewicht = gewichten.get(strat_key, 0.0)
            if not math.isfinite(gewicht) or gewicht <= 0:
                continue
            for ticker in geselecteerd:
                gewogen_score[ticker] = gewogen_score.get(ticker, 0.0) + gewicht
                bijdragen.setdefault(ticker, []).append(STRATEGIE_LABELS[strat_key])

        kandidaten = []
        for ticker, score in gewogen_score.items():
            atr = atr_pct.get(ticker)
            if atr is None or not (MIN_ATR_PCT <= atr <= MAX_ATR_PCT):
                continue
            kandidaten.append((ticker, score, atr, bijdragen[ticker]))

        kandidaten.sort(key=lambda x: (-x[1], -x[2], x[0]))
        top = kandidaten[:TOP_N]

        print(
            f"\n{ex_name}: {len(gewogen_score)} tickers met >=1 selectie, {len(kandidaten)} na volatiliteitsfilter, top {len(top)} gerapporteerd"
        )

        for ticker, score, atr, strategieen in top:
            price, quote_date = quotes[ticker]
            stored = db_logger.log_selectie(
                ticker=ticker,
                datum=quote_date,
                strategie="bot_combi_volatiel",
                beurs=ex_name,
                koers=price,
                parameters={
                    "research_signal": SelectionSignal(
                        ticker=ticker, strategy="bot_combi_volatiel",
                        signal_date=quote_date, reference_price=price,
                        atr_pct=atr, score=score,
                    ).payload(),
                    "signal_date": quote_date,
                    "price_basis": "yahoo_auto_adjusted_close",
                    "gewogen_score": round(score, 2),
                    "strategieen": ", ".join(sorted(strategieen)),
                    "atr_pct": atr,
                    "grafiek": _yahoo_link(ticker),
                },
            )
            if not stored:
                storage_errors.append(f"{ticker}/{ex_name}: databaseopslag mislukt")

        if not top:
            continue

        gewicht_str = ", ".join(f"{k}={v:.2f}" for k, v in gewichten.items())
        delen = [
            f"🎯 *Combi-Selectie Volatiel — {ex_name}*",
            f"_{today_str()} | gewichten: {gewicht_str} | ATR% {MIN_ATR_PCT}-{MAX_ATR_PCT}%_",
            "Gewichten: historische gemiddelde/spreiding met mogelijke statische fallback; geen gevalideerde Sharpe-ratio.",
            "Trechter: VCP/seizoenseffecten → fundamentals → ATR-filter; geen orders.",
            f"Datadekking: {len(atr_pct)}/{len(all_tickers)} unieke tickers met bruikbare ATR; onbruikbare data wordt overgeslagen.",
            "─────────────────────────────",
        ]
        for ticker, score, atr, strategieen in top:
            price, quote_date = quotes[ticker]
            delen.append(
                f"• `{ticker}` — score {score:.2f} | ATR {atr:.1f}% | koers {price:.8g} ({quote_date}, bronkoerseenheden) {_yahoo_link(ticker)}\n  {', '.join(sorted(strategieen))}"
            )

        bericht = "\n\n".join(delen)
        ok = send_telegram(bericht, parse_mode="Markdown")
        if os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID") and not ok:
            storage_errors.append(f"{ex_name}: Telegram niet verzonden")
        email_delen.append(bericht)
        print(
            f"  → Telegram {'verzonden' if ok else 'niet verzonden'}: {ex_name}",
            flush=True,
        )

    if not email_delen:
        empty_report = (
            f"Combi-Selectie Volatiel — {today_str()}\n"
            f"Geen kandidaten na combinatiescore en ATR-filter; "
            f"{len(atr_pct)}/{len(all_tickers)} tickers met bruikbare ATR.\n"
            f"Shortlist: {totaal_shortlist} ticker/beurs-combinaties."
        )
        ok = send_telegram(empty_report, parse_mode=None)
        print(f"  → Telegram {'verzonden' if ok else 'niet verzonden'}: geen kandidaten", flush=True)
        if os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID") and not ok:
            storage_errors.append("Telegram leeg rapport niet verzonden")
        email_delen.append(empty_report)
    if email_delen:
        email_ok = send_email(
            subject=f"Combi-Selectie Volatiel rapport {today_str()}",
            body="\n\n" + ("=" * 40 + "\n\n").join(email_delen),
        )

    if (
        all(os.getenv(k) for k in ("EMAIL_USER", "EMAIL_PASS", "EMAIL_RECEIVER"))
        and not email_ok
    ):
        storage_errors.append("E-mail niet verzonden")
    if storage_errors:
        raise RuntimeError("; ".join(storage_errors))
    print(f"\n{'=' * 60}\nKlaar.")


def run_live_engine():
    try:
        _run_live_engine()
    except Exception as exc:
        # Keep the run failed; notification failure must not hide the scan error.
        bericht = (
            f"Combi-Selectie Volatiel ONVOLLEDIG — {today_str()}\n"
            "Scan of rapportage afgebroken; geen volledig rapport beschikbaar.\n"
            "Eventuele deelresultaten niet als volledige selectie gebruiken.\n"
            f"Reden: {str(exc)[:1000]}"
        )
        print(bericht, flush=True)
        for channel, send in (
            ("Telegram", lambda: send_telegram(bericht, parse_mode=None)),
            ("E-mail", lambda: send_email(
                subject=f"Combi-Selectie Volatiel ONVOLLEDIG {today_str()}", body=bericht
            )),
        ):
            try:
                ok = send()
                print(f"[melding] {channel}: {'verzonden' if ok else 'niet verzonden'}", flush=True)
            except Exception:
                print(f"[melding] {channel}: verzenden mislukt", flush=True)
        raise


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    run_live_engine()
