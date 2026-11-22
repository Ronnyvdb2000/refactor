#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
a_par_model.py — lineair multi-factor model op basis van rolling IC-gewichten
================================================================================

Werkwijze:
  1. Voor elke horizon (5d, 10d, 20d, 30d, 60d): bereken per parameter de IC
     over de laatste ROLLING_DAGEN kalenderdagen tot
     EFFECTIEVE_SCORE_DATUM - EMBARGO_DAGEN.
     Bij backtesting (--datum) wordt de scorebasis voor het embargo begrensd op
     min(score_datum, laatste datum met target-data) zodat er niet over de
     beschikbare target-data heen wordt gekeken.
     Fallback naar FALLBACK_DAGEN / 180d / 365d als er te weinig datums zijn.
  2. Winsorize de IC's op ±IC_MAX_ABS.
  3. Filter op |IC| >= MIN_IC_ABS. Parameters met minder dan MIN_DATUMS_IC
     datums dekking worden overgeslagen.
  4. Voor elke (ticker, score_datum): rangschik elke parameter cross-
     sectioneel (0..1), centreer rond 0 (rang - 0.5) en bereken
     score = Σ (IC_i × (rang_i - 0.5)) / Σ |IC_i|.
  5. Top-N per horizon = picks.
  6. Output: JSON/CSV per horizon + Telegram-bericht + optioneel schrijven naar
     selecties (strategie a_par_model_v1) + metrics naar model_metrics.

Env:
  SUPABASE_DB_URL          verplicht
  TELEGRAM_TOKEN           optioneel
  TELEGRAM_CHAT_ID         optioneel

WIJZIGINGEN:
  v1.7 (performance & timeout-fix):
    - Datumfilter in `get_data` op basis van benodigd trainingsvenster + embargo
      + buffer, zodat `sel_par` niet over de volledige historie wordt uitgepakt.
    - Dure `LATERAL JOIN` op `selecties` vervangen door eenmalige `DISTINCT ON`
      CTE (`sel`) voor een snellere hash/merge join.
    - Sessie `statement_timeout` verhoogd (default 10 min, instelbaar via
      `--statement-timeout`).
  v1.6 (refactor):
    - Vectoriële scoring in `score_voor_datum` i.p.v. `df.apply(..., axis=1)`
      voor snellere berekening bij grotere universums met behoud van neutrale
      behandeling van ontbrekende waarden.
    - Configuratie gebundeld in `ModelConfig` met `parse_args(argv=None)` voor
      beter hergebruik en testbaarheid.
    - Veilige afhandeling van `conn` in `finally` bij mislukte DB-verbinding en
      opschoning van spaties in Yahoo Finance-URL's bij DB-writes.
  v1.5 (2026-11-15):
    - Kalenderembargo met handelsdagconversie: max(EMBARGO, ceil(horizon*7/5)+2).
    - Training begrensd vanaf de echte scoredatum; targets worden niet tweemaal teruggeschoven.
  v1.1 (2026-10-11):
    - Koers meegenomen per pick (uit selecties via join).
    - Telegram toont koers + klikbare Yahoo-grafiek-link.
  v1.2 (2026-10-11):
    - Effectieve score_datum per horizon = min(score_datum, laatste target-datum).
    - Horizon-afhankelijke embargo (max(EMBARGO, horizon + 5)).
    - Extra fallback-windows (180d, 365d) voor trage horizons.
    - Uitgebreide logging per horizon in de console.
  v1.3 (2026-10-12):
    - Metrics per horizon worden weggeschreven naar tabel `model_metrics`
      (run_datum, horizon_dagen, window_label, train_start/einde,
       n_params_totaal/gefilterd, top_param_*, gem_ic/abs/ir, max_ic_abs,
       n_picks). UNIQUE op (run_datum, model_versie, horizon_dagen) —
      herhaalde runs op dezelfde dag updaten dezelfde rij.
  v1.4 (2026-11-01):
    - FIX scoreberekening: rangen worden gecentreerd (rang - 0.5). Voorheen
      kreeg een ticker met een ontbrekende parameter met negatieve IC een
      bonus en met een ontbrekende parameter met positieve IC een straf,
      omdat ontbrekende waarden als 0 telden terwijl de gemiddelde rang 0.5
      is. Nu is een ontbrekende waarde neutraal. Tickers met volledige data
      behouden hun onderlinge volgorde (alle scores schuiven met dezelfde
      constante); de absolute scorewaarden zijn dus niet vergelijkbaar met
      die van oudere runs.
    - NIEUW versheidscontrole: is de nieuwste datum in de data ouder dan
      --max-data-leeftijd dagen (default 5), dan stopt het script met een
      Telegram-waarschuwing i.p.v. picks op oude data te sturen. Niet van
      toepassing bij een expliciete --datum (backtest). 0 = uitgeschakeld.
"""

from __future__ import annotations

import argparse
import html
import math
import os
import sys
import traceback
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd
import psycopg2
import requests
from scipy.stats import spearmanr

from common import db_logger
from common.fundamentals import KNOWN_FUNDAMENTALS_SQL
from common.horizons import calendar_embargo
from common.reports import write_csv
from common.sel_par_inputs import sel_par_query


# ============================================================
# CONFIGURATIE
# ============================================================

HORIZONS_DEFAULT = "5d,10d,20d,30d,60d"
TOP_N_DEFAULT = 10
MIN_IC_DEFAULT = 0.02
ROLLING_DAGEN_DEFAULT = 28
FALLBACK_DAGEN_DEFAULT = 90
EMBARGO_DAGEN_DEFAULT = 25
IC_MAX_ABS_DEFAULT = 0.15
MIN_DATUMS_IC_DEFAULT = 10
MIN_TICKERS_PER_DATUM = 20
MIN_PARAMS_VOOR_MODEL = 5
MAX_DATA_LEEFTIJD_DEFAULT = 5       # v1.4: max leeftijd (dagen) van de nieuwste data
STATEMENT_TIMEOUT_MS_DEFAULT = 600_000  # v1.7: 10 minuten DB query-timeout

MODEL_VERSIE = "a_par_model_v1"

EXTRA_FALLBACK_STAPPEN = [180, 365]

RESULTS_DIR = os.environ.get("RESULTS_DIR", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Kolommen die metadata, prijsniveaus, forward returns of outputvelden bevatten
# en daarom niet als losse cross-sectionele factor mogen worden meegenomen.
NIET_FACTOR = {
    "ticker", "datum", "beurs", "strategie", "bijgewerkt_op", "id",
    "entry_koers", "high52w", "ma50", "ma200", "market_cap",
    "ema8", "ema20",
    "fwd_ret_5d", "fwd_ret_10d", "fwd_ret_20d", "fwd_ret_30d", "fwd_ret_60d",
    "fwd_close_5d", "fwd_close_10d", "fwd_close_20d",
    "fwd_close_30d", "fwd_close_60d",
    "koers", "score",
}


@dataclass
class ModelConfig:
    """Gebundelde model- en runparameters, afgeleid van CLI-argumenten."""

    horizons: list[str] = field(default_factory=lambda: HORIZONS_DEFAULT.split(","))
    top_n: int = TOP_N_DEFAULT
    min_ic: float = MIN_IC_DEFAULT
    rolling_dagen: int = ROLLING_DAGEN_DEFAULT
    fallback_dagen: int = FALLBACK_DAGEN_DEFAULT
    embargo_dagen: int = EMBARGO_DAGEN_DEFAULT
    ic_max_abs: float = IC_MAX_ABS_DEFAULT
    min_datums_ic: int = MIN_DATUMS_IC_DEFAULT
    max_data_leeftijd: int = MAX_DATA_LEEFTIJD_DEFAULT
    statement_timeout_ms: int = STATEMENT_TIMEOUT_MS_DEFAULT
    datum: str | None = None
    geen_db_write: bool = False
    geen_notify: bool = False


# ============================================================
# HULPFUNCTIES
# ============================================================

def _esc(value) -> str:
    return html.escape(str(value))


def _clean_ticker(ticker) -> str:
    return str(ticker).replace(" ", "").strip()


def _yahoo_quote_url(ticker) -> str:
    return f"https://finance.yahoo.com/quote/{_clean_ticker(ticker)}"


def _format_koers(koers) -> str:
    try:
        if koers is None or pd.isna(koers):
            return "-"
        return f"{float(koers):.2f}"
    except (TypeError, ValueError):
        return "-"


def _horizon_naar_dagen(horizon: str) -> int:
    try:
        return int(str(horizon).strip().lower().rstrip("d"))
    except ValueError:
        return 0


def _bereken_benodigde_startdatum(cfg: ModelConfig) -> pd.Timestamp:
    """
    Berekent de vroegste datum die nodig is voor IC-training over alle
    gevraagde horizons en fallback-windows, inclusief een ruime veiligheidsmarge.
    Zo hoeft PostgreSQL niet de volledige meerjarige historie te verwerken.
    """
    anker = pd.Timestamp(cfg.datum) if cfg.datum else pd.Timestamp.now().normalize()
    max_horizon = max((_horizon_naar_dagen(h) for h in cfg.horizons), default=60)
    max_embargo = calendar_embargo(max_horizon, minimum=cfg.embargo_dagen)
    max_window = max([cfg.rolling_dagen, cfg.fallback_dagen, *EXTRA_FALLBACK_STAPPEN])
    buffer_dagen = 30
    totaal_terug = max_embargo + max_window + buffer_dagen
    return (anker - pd.Timedelta(days=totaal_terug)).normalize()


def _set_session_timeout(conn, timeout_ms: int) -> None:
    """Verhoogt de statement_timeout op de actieve database-sessie."""
    if timeout_ms <= 0:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s;", (int(timeout_ms),))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"[DB] Waarschuwing: kon statement_timeout niet aanpassen ({exc}).")


def data_is_vers(
    score_datum: pd.Timestamp,
    max_leeftijd_dagen: int,
    vandaag: pd.Timestamp | None = None,
) -> tuple[bool, int]:
    """
    v1.4: controleert of de nieuwste datum in de data recent genoeg is.
    Geeft (is_vers, leeftijd_in_dagen) terug.
    max_leeftijd_dagen <= 0 (of None) schakelt de controle uit.
    """
    vandaag_ts = (vandaag if vandaag is not None else pd.Timestamp.now()).normalize()
    leeftijd = (vandaag_ts - pd.Timestamp(score_datum).normalize()).days
    if max_leeftijd_dagen is None or max_leeftijd_dagen <= 0:
        return True, leeftijd
    return leeftijd <= max_leeftijd_dagen, leeftijd


def send_telegram(tekst: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] Secrets ontbreken — bericht NIET verstuurd.")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": tekst,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"[Telegram] FOUT {resp.status_code}: {resp.text[:200]}")
            return False
        print("[Telegram] Bericht verzonden.")
        return True
    except requests.RequestException as exc:
        print(f"[Telegram] Exception: {exc}")
        return False


# ============================================================
# DATA OPHALEN
# ============================================================

def get_data(
    conn,
    min_datum: pd.Timestamp | None = None,
    max_datum: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """
    Haalt technische indicatoren, fundamentele data, forward returns en de
    meest recente geldige koers per (ticker, datum) op uit de database.

    v1.7:
    - Filtert op [min_datum, max_datum] om onnodige historie over te slaan.
    - Vervangt de rij-voor-rij `LATERAL JOIN` door een eenmalige `DISTINCT ON`
      CTE (`sel`) om statement timeouts op grote datasets te voorkomen.
    """
    filters_tech = []
    filters_fund = [f"({KNOWN_FUNDAMENTALS_SQL})"]
    filters_fr = []
    filters_sel = ["koers > 0", "koers < 'Infinity'::real"]

    if min_datum is not None:
        min_str = min_datum.strftime("%Y-%m-%d")
        filters_tech.append(f"datum::date >= DATE '{min_str}'")
        filters_fund.append(f"datum::date >= DATE '{min_str}'")
        filters_fr.append(f"datum::date >= DATE '{min_str}'")
        filters_sel.append(f"datum::date >= DATE '{min_str}'")

    if max_datum is not None:
        max_str = max_datum.strftime("%Y-%m-%d")
        filters_tech.append(f"datum::date <= DATE '{max_str}'")
        filters_fund.append(f"datum::date <= DATE '{max_str}'")
        filters_fr.append(f"datum::date <= DATE '{max_str}'")
        filters_sel.append(f"datum::date <= DATE '{max_str}'")

    where_tech = f"WHERE {' AND '.join(filters_tech)}" if filters_tech else ""
    where_fund = f"WHERE {' AND '.join(filters_fund)}" if filters_fund else ""
    where_fr = f"WHERE {' AND '.join(filters_fr)}" if filters_fr else ""
    where_sel = f"WHERE {' AND '.join(filters_sel)}" if filters_sel else ""

    query = f"""
    WITH tech AS (
        SELECT DISTINCT ON (ticker, datum)
            ticker, datum, atr14, atr14_pct, rsi14, ibs,
            ma50, ma200, pct_from_ma50, pct_from_ma200,
            vol_ratio_20d, high52w, pct_from_high52w,
            macd, macd_signaal, macd_hist, bb_breedte, bb_percent_b,
            stoch_k, stoch_d, adx14, rel_sterkte_20d, hv20, hv60,
            dagen_sinds_low52w, vol_ratio_50d,
            ema8, ema20, pct_from_ema8, pct_from_ema20, ema8_minus_ema20
        FROM generieke_technicals
        {where_tech}
        ORDER BY ticker, datum
    ),
    fund AS (
        SELECT DISTINCT ON (ticker, datum)
            ticker, datum, market_cap, trailing_pe, price_to_book,
            dividend_yield, current_ratio, revenue_growth_pct, fcf_yield,
            net_debt_ebitda, payout_pct, analisten_count,
            piotroski_score, eps_growth_pct, eps_cagr_pct, peg_ratio
        FROM generieke_fundamentals
        {where_fund}
        ORDER BY ticker, datum DESC
    ),
    fr AS (
        SELECT DISTINCT ON (ticker, datum)
            ticker, datum,
            fwd_ret_5d, fwd_ret_10d, fwd_ret_20d, fwd_ret_30d, fwd_ret_60d
        FROM forward_returns
        {where_fr}
        ORDER BY ticker, datum
    ),
    sel AS (
        SELECT DISTINCT ON (ticker, datum)
            ticker, datum, koers
        FROM selecties
        {where_sel}
        ORDER BY ticker, datum, created_at ASC NULLS LAST, id ASC
    )
    SELECT
        t.ticker, t.datum,
        t.atr14, t.atr14_pct, t.rsi14, t.ibs,
        t.ma50, t.ma200, t.pct_from_ma50, t.pct_from_ma200,
        t.vol_ratio_20d, t.high52w, t.pct_from_high52w,
        t.macd, t.macd_signaal, t.macd_hist, t.bb_breedte, t.bb_percent_b,
        t.stoch_k, t.stoch_d, t.adx14, t.rel_sterkte_20d, t.hv20, t.hv60,
        t.dagen_sinds_low52w, t.vol_ratio_50d,
        t.ema8, t.ema20, t.pct_from_ema8, t.pct_from_ema20, t.ema8_minus_ema20,
        f.market_cap, f.trailing_pe, f.price_to_book, f.dividend_yield,
        f.current_ratio, f.revenue_growth_pct, f.fcf_yield,
        f.net_debt_ebitda, f.payout_pct, f.analisten_count,
        f.piotroski_score, f.eps_growth_pct, f.eps_cagr_pct, f.peg_ratio,
        fr.fwd_ret_5d, fr.fwd_ret_10d, fr.fwd_ret_20d,
        fr.fwd_ret_30d, fr.fwd_ret_60d,
        sel.koers
    FROM tech t
    LEFT JOIN fund f ON f.ticker = t.ticker AND f.datum = t.datum
    LEFT JOIN fr ON fr.ticker = t.ticker AND fr.datum = t.datum
    LEFT JOIN sel ON sel.ticker = t.ticker AND sel.datum = t.datum;
    """
    sql = sel_par_query(query)
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]

    df = pd.DataFrame(rows, columns=columns)
    if df.empty:
        return df

    df["datum"] = pd.to_datetime(df["datum"], errors="coerce")
    return df.dropna(subset=["datum"])


# ============================================================
# IC-BEREKENING
# ============================================================

def bereken_ic_per_parameter(
    df: pd.DataFrame,
    target_kolom: str,
    train_start: pd.Timestamp,
    train_einde: pd.Timestamp,
    min_datums: int,
    ic_max_abs: float,
    min_tickers: int = MIN_TICKERS_PER_DATUM,
) -> pd.DataFrame:
    """
    Berekent per factor de gemiddelde dagelijkse Spearman rank-correlatie (IC)
    met het toekomstige rendement binnen [train_start, train_einde], inclusief
    winsorizing op ±ic_max_abs en Information Ratio (IC / std(IC)).
    """
    train = df[(df["datum"] >= train_start) & (df["datum"] <= train_einde)]
    if len(train) < min_tickers:
        return pd.DataFrame()

    params = [
        c for c in df.columns
        if c not in NIET_FACTOR
        and c != target_kolom
        and pd.api.types.is_numeric_dtype(df[c])
    ]

    rijen = []
    for param in params:
        sub = train[["datum", param, target_kolom]].dropna()
        if len(sub) < min_tickers:
            continue

        ics = []
        for _, groep in sub.groupby("datum", sort=False):
            if len(groep) < min_tickers:
                continue
            rho, _ = spearmanr(groep[param], groep[target_kolom])
            if not np.isnan(rho):
                ics.append(float(rho))

        if len(ics) < min_datums:
            continue

        ics_arr = np.asarray(ics, dtype=float)
        ic_gem = float(np.mean(ics_arr))
        # Aftoppen op ±ic_max_abs voorkomt dat één extreem venster de weging domineert.
        ic_gem_wins = float(np.clip(ic_gem, -ic_max_abs, ic_max_abs))
        ic_std = float(np.std(ics_arr))
        ic_ir = ic_gem / ic_std if ic_std > 1e-9 else 0.0

        rijen.append({
            "parameter": param,
            "ic": round(ic_gem_wins, 4),
            "ic_raw": round(ic_gem, 4),
            "ic_abs": round(abs(ic_gem_wins), 4),
            "ic_ir": round(ic_ir, 3),
            "n_datums": len(ics_arr),
            "n_obs": len(sub),
        })

    if not rijen:
        return pd.DataFrame()
    return pd.DataFrame(rijen).sort_values("ic_abs", ascending=False)


def ic_met_fallback(
    df: pd.DataFrame,
    target_kolom: str,
    effectieve_score_datum: pd.Timestamp,
    embargo_dagen: int,
    rolling_dagen: int,
    fallback_dagen: int,
    min_datums: int,
    ic_max_abs: float,
) -> tuple[pd.DataFrame, str, pd.Timestamp, pd.Timestamp]:
    """
    Zoekt een trainingsvenster vanaf `effectieve_score_datum - embargo_dagen`
    en breidt stapsgewijs uit naar langere windows als er te weinig parameters
    voldoen aan de minimale datumeis.
    """
    train_einde = effectieve_score_datum - pd.Timedelta(days=embargo_dagen)

    stappen = [
        (rolling_dagen, f"rolling {rolling_dagen}d"),
        (fallback_dagen, f"fallback {fallback_dagen}d"),
    ]
    for extra in EXTRA_FALLBACK_STAPPEN:
        if extra > fallback_dagen:
            stappen.append((extra, f"extended {extra}d"))

    laatste_poging = pd.DataFrame()
    laatste_start = train_einde
    laatste_label = "geen"

    for dagen, label in stappen:
        train_start = train_einde - pd.Timedelta(days=dagen)
        gewichten = bereken_ic_per_parameter(
            df,
            target_kolom,
            train_start,
            train_einde,
            min_datums,
            ic_max_abs,
        )
        if not gewichten.empty:
            laatste_poging = gewichten
            laatste_start = train_start
            laatste_label = label
        if not gewichten.empty and len(gewichten) >= MIN_PARAMS_VOOR_MODEL:
            return gewichten, label, train_start, train_einde

    if laatste_poging.empty:
        return pd.DataFrame(), "geen", train_einde, train_einde
    return laatste_poging, laatste_label, laatste_start, train_einde


# ============================================================
# SCORING
# ============================================================

def score_voor_datum(
    df: pd.DataFrame,
    score_datum: pd.Timestamp,
    gewichten: pd.DataFrame,
    top_n: int,
) -> pd.DataFrame:
    """
    Berekent op `score_datum` per ticker de gewogen som van gecentreerde
    cross-sectionele percentielrangen, genormaliseerd door Σ |IC_i|.

    v1.4/v1.6: rangen worden gecentreerd rond 0 (rang - 0.5) en ontbrekende
    waarden krijgen bijdrage 0.0, zodat missing data neutraal blijft en de
    berekening vectorieel via NumPy kan verlopen.
    """
    dag = df[df["datum"] == score_datum].copy()
    if dag.empty or gewichten.empty:
        return pd.DataFrame()

    params = [p for p in gewichten["parameter"] if p in dag.columns]
    if not params:
        return pd.DataFrame()

    gewicht_map = dict(zip(gewichten["parameter"], gewichten["ic"]))
    gewichten_vec = np.array([float(gewicht_map[p]) for p in params], dtype=float)
    totaal_gewicht = float(np.sum(np.abs(gewichten_vec)))
    if totaal_gewicht < 1e-9:
        return pd.DataFrame()

    bijdragen = np.zeros((len(dag), len(params)), dtype=float)
    for idx, param in enumerate(params):
        rang = dag[param].rank(pct=True, na_option="keep") - 0.5
        bijdragen[:, idx] = rang.fillna(0.0).to_numpy(dtype=float) * gewichten_vec[idx]

    dag["score"] = np.sum(bijdragen, axis=1) / totaal_gewicht

    kolommen = ["ticker", "datum", "score"]
    if "koers" in dag.columns:
        kolommen.append("koers")

    return dag.dropna(subset=["score"]).nlargest(top_n, "score")[kolommen]


# ============================================================
# TELEGRAM & RAPPORTEN
# ============================================================

def bouw_telegram_bericht(
    picks_per_horizon: dict[str, pd.DataFrame],
    gewichten_per_horizon: dict[str, pd.DataFrame],
    window_labels: dict[str, str],
    score_datum: pd.Timestamp,
    horizons: Iterable[str] | None = None,
) -> str:
    volgorde = list(horizons) if horizons is not None else ["5d", "10d", "20d", "30d", "60d"]
    regels = [
        "🎯 <b>A Parameter-model — top picks</b>",
        "",
        f"Datum: <b>{_esc(score_datum.date())}</b>",
        "",
    ]

    for horizon in volgorde:
        picks = picks_per_horizon.get(horizon)
        gewichten = gewichten_per_horizon.get(horizon)
        window = window_labels.get(horizon, "?")

        if picks is None or picks.empty:
            regels.append(f"<b>Horizon {horizon}</b> — geen picks")
            regels.append("")
            continue

        n_params = len(gewichten) if gewichten is not None else 0
        regels.append(f"<b>Horizon {horizon}</b> ({_esc(window)}, {n_params} params)")

        for rang, (_, row) in enumerate(picks.head(10).iterrows(), start=1):
            ticker = _clean_ticker(row["ticker"])
            yahoo_url = _yahoo_quote_url(ticker)
            score = float(row["score"])
            koers_str = _format_koers(row.get("koers"))

            regels.append(
                f"  {rang:>2}. <b>{_esc(ticker)}</b> "
                f"<code>{score:+.3f}</code> 📈 — "
                f"koers {koers_str} — "
                f'<a href="{yahoo_url}">📊 Grafiek</a>'
            )
        regels.append("")

    return "\n".join(regels)


def schrijf_rapporten(picks: pd.DataFrame, gewichten: pd.DataFrame, horizon: str) -> None:
    """Schrijft picks en factorgewichten weg als zowel JSON als CSV naar RESULTS_DIR."""
    picks_csv = os.path.join(RESULTS_DIR, f"a_par_model_{horizon}_picks.csv")
    picks.to_json(
        picks_csv.replace(".csv", ".json"),
        orient="records",
        date_format="iso",
        force_ascii=False,
    )
    write_csv(picks, picks_csv, index=False)

    weights_csv = os.path.join(RESULTS_DIR, f"a_par_model_{horizon}_weights.csv")
    gewichten.to_json(
        weights_csv.replace(".csv", ".json"),
        orient="records",
        date_format="iso",
        force_ascii=False,
    )
    write_csv(gewichten, weights_csv, index=False)


# ============================================================
# DB-WRITE
# ============================================================

def schrijf_naar_selecties(
    conn,
    picks_per_horizon: dict[str, pd.DataFrame],
    score_datum: pd.Timestamp,
) -> int:
    """
    Slaat picks met een geldige positieve koers op in `selecties` via bulk-insert,
    waarbij bestaande picks behouden blijven.
    """
    rows = []
    datum_str = score_datum.strftime("%Y-%m-%d")

    for horizon, picks in picks_per_horizon.items():
        if picks is None or picks.empty:
            continue
        for _, row in picks.iterrows():
            ticker = _clean_ticker(row["ticker"])
            try:
                price = float(row.get("koers"))
                if not math.isfinite(price) or price <= 0:
                    raise ValueError("geen positieve eindige koers")
            except (TypeError, ValueError):
                print(f"[DB] {ticker} ({horizon}) niet opgeslagen: koers op scoredatum ontbreekt.")
                continue

            rows.append(
                dict(
                    ticker=ticker,
                    datum=datum_str,
                    strategie=MODEL_VERSIE,
                    beurs=None,
                    koers=price,
                    parameters={
                        "score": float(row["score"]),
                        "model": MODEL_VERSIE,
                        "horizon": horizon,
                        "grafiek": _yahoo_quote_url(ticker),
                    },
                )
            )

    count = db_logger.log_selecties_bulk(rows, connection=conn)
    print(f"[DB] {count} picks nieuw opgeslagen of aangevuld; bestaande picks behouden.")
    return count


def schrijf_model_metrics(
    conn,
    run_datum,
    horizon: str,
    window_label: str,
    train_start: pd.Timestamp | None,
    train_einde: pd.Timestamp | None,
    embargo_dagen: int,
    gewichten_totaal_df: pd.DataFrame | None,
    gewichten_gefilterd_df: pd.DataFrame | None,
    n_picks: int,
) -> None:
    """
    v1.3: schrijft diagnostische en IC-statistieken per horizon weg naar
    `model_metrics` met upsert op (run_datum, model_versie, horizon_dagen).
    """
    insert = """
        INSERT INTO model_metrics (
            run_datum, model_versie, horizon_dagen,
            window_label, train_start, train_einde, embargo_dagen,
            n_params_totaal, n_params_gefilterd,
            top_param_naam, top_param_ic, top_param_ir,
            gem_ic, gem_ic_abs, gem_ir, max_ic_abs,
            n_picks
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s
        )
        ON CONFLICT (run_datum, model_versie, horizon_dagen)
        DO UPDATE SET
            run_tijdstip       = NOW(),
            window_label       = EXCLUDED.window_label,
            train_start        = EXCLUDED.train_start,
            train_einde        = EXCLUDED.train_einde,
            embargo_dagen      = EXCLUDED.embargo_dagen,
            n_params_totaal    = EXCLUDED.n_params_totaal,
            n_params_gefilterd = EXCLUDED.n_params_gefilterd,
            top_param_naam     = EXCLUDED.top_param_naam,
            top_param_ic       = EXCLUDED.top_param_ic,
            top_param_ir       = EXCLUDED.top_param_ir,
            gem_ic             = EXCLUDED.gem_ic,
            gem_ic_abs         = EXCLUDED.gem_ic_abs,
            gem_ir             = EXCLUDED.gem_ir,
            max_ic_abs         = EXCLUDED.max_ic_abs,
            n_picks            = EXCLUDED.n_picks;
    """

    n_totaal = int(len(gewichten_totaal_df)) if gewichten_totaal_df is not None else 0
    n_gefilterd = int(len(gewichten_gefilterd_df)) if gewichten_gefilterd_df is not None else 0

    if gewichten_gefilterd_df is None or gewichten_gefilterd_df.empty:
        top_param_naam = None
        top_param_ic = None
        top_param_ir = None
        gem_ic = None
        gem_ic_abs = None
        gem_ir = None
        max_ic_abs = None
    else:
        top = gewichten_gefilterd_df.iloc[0]
        top_param_naam = str(top["parameter"])
        top_param_ic = float(top["ic"])
        top_param_ir = float(top["ic_ir"])
        gem_ic = float(gewichten_gefilterd_df["ic"].mean())
        gem_ic_abs = float(gewichten_gefilterd_df["ic_abs"].mean())
        gem_ir = float(gewichten_gefilterd_df["ic_ir"].mean())
        max_ic_abs = float(gewichten_gefilterd_df["ic_abs"].max())

    try:
        with conn.cursor() as cur:
            cur.execute(
                sel_par_query(insert),
                [
                    run_datum,
                    MODEL_VERSIE,
                    _horizon_naar_dagen(horizon),
                    window_label,
                    train_start.date() if train_start is not None else None,
                    train_einde.date() if train_einde is not None else None,
                    int(embargo_dagen),
                    n_totaal,
                    n_gefilterd,
                    top_param_naam,
                    top_param_ic,
                    top_param_ir,
                    gem_ic,
                    gem_ic_abs,
                    gem_ir,
                    max_ic_abs,
                    int(n_picks),
                ],
            )
        conn.commit()
        print(
            f"[DB] metrics opgeslagen voor horizon {horizon} "
            f"(run_datum={run_datum}, n_params={n_gefilterd}, n_picks={n_picks})."
        )
    except Exception as exc:
        conn.rollback()
        print(f"[DB] Metrics wegschrijven mislukt voor horizon {horizon}: {exc}")


# ============================================================
# CLI & MAIN
# ============================================================

def parse_args(argv: list[str] | None = None) -> ModelConfig:
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizons", default=HORIZONS_DEFAULT)
    parser.add_argument("--top-n", type=int, default=TOP_N_DEFAULT)
    parser.add_argument("--min-ic", type=float, default=MIN_IC_DEFAULT)
    parser.add_argument("--rolling-dagen", type=int, default=ROLLING_DAGEN_DEFAULT)
    parser.add_argument("--fallback-dagen", type=int, default=FALLBACK_DAGEN_DEFAULT)
    parser.add_argument("--embargo-dagen", type=int, default=EMBARGO_DAGEN_DEFAULT)
    parser.add_argument("--ic-max-abs", type=float, default=IC_MAX_ABS_DEFAULT)
    parser.add_argument("--min-datums-ic", type=int, default=MIN_DATUMS_IC_DEFAULT)
    parser.add_argument(
        "--max-data-leeftijd",
        type=int,
        default=MAX_DATA_LEEFTIJD_DEFAULT,
        help=(
            "Stop als de nieuwste datum in de data ouder is dan dit aantal dagen "
            "(0 = controle uit). Niet van toepassing bij een expliciete --datum."
        ),
    )
    parser.add_argument(
        "--statement-timeout",
        type=int,
        default=STATEMENT_TIMEOUT_MS_DEFAULT,
        help="PostgreSQL statement_timeout in milliseconden (default 600000 = 10 min).",
    )
    parser.add_argument("--datum", default=None)
    parser.add_argument("--geen-db-write", action="store_true")
    parser.add_argument("--geen-notify", action="store_true")

    raw = parser.parse_args(argv)
    horizons = [h.strip() for h in raw.horizons.split(",") if h.strip()]

    return ModelConfig(
        horizons=horizons,
        top_n=raw.top_n,
        min_ic=raw.min_ic,
        rolling_dagen=raw.rolling_dagen,
        fallback_dagen=raw.fallback_dagen,
        embargo_dagen=raw.embargo_dagen,
        ic_max_abs=raw.ic_max_abs,
        min_datums_ic=raw.min_datums_ic,
        max_data_leeftijd=raw.max_data_leeftijd,
        statement_timeout_ms=raw.statement_timeout,
        datum=raw.datum,
        geen_db_write=raw.geen_db_write,
        geen_notify=raw.geen_notify,
    )


def _print_config(cfg: ModelConfig) -> None:
    print()
    print("=" * 70)
    print("A PARAMETER-MODEL")
    print("=" * 70)
    print(f"Horizons         : {', '.join(cfg.horizons)}")
    print(f"Top-N            : {cfg.top_n}")
    print(f"Min |IC|         : {cfg.min_ic}")
    print(
        f"Rolling window   : {cfg.rolling_dagen}d "
        f"(fallback {cfg.fallback_dagen}d, extended {EXTRA_FALLBACK_STAPPEN})"
    )
    print(
        f"Embargo          : {cfg.embargo_dagen}d "
        f"(horizon in handelsdagen; kalenderembargo met buffer)"
    )
    print(f"Winsorize        : ±{cfg.ic_max_abs}")
    print(f"Min datums IC    : {cfg.min_datums_ic}")
    leeftijd_label = f"{cfg.max_data_leeftijd} dagen" if cfg.max_data_leeftijd > 0 else "uit"
    print(f"Max data-leeftijd: {leeftijd_label}")


def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv)

    if not SUPABASE_DB_URL:
        print("FOUT: SUPABASE_DB_URL ontbreekt.", file=sys.stderr)
        return 1

    _print_config(cfg)

    conn = None
    try:
        conn = psycopg2.connect(SUPABASE_DB_URL)
        _set_session_timeout(conn, cfg.statement_timeout_ms)

        min_datum = _bereken_benodigde_startdatum(cfg)
        max_datum = pd.Timestamp(cfg.datum).normalize() if cfg.datum else None

        print(f"\nData ophalen (vanaf {min_datum.date()})...")
        df = get_data(conn, min_datum=min_datum, max_datum=max_datum)
        if df.empty:
            print("FOUT: Geen data teruggekregen uit de database.")
            return 1

        print(
            f"{len(df):,} rijen | {df['datum'].nunique()} datums | "
            f"{df['ticker'].nunique()} tickers"
        )

        if "koers" in df.columns and len(df) > 0:
            n_koers = int(df["koers"].notna().sum())
            print(f"Koers beschikbaar voor {n_koers:,} rijen ({100 * n_koers / len(df):.1f}%).")

        score_datum = pd.Timestamp(cfg.datum) if cfg.datum else df["datum"].max()
        print(f"\nScore-datum (picks): {score_datum.date()}")

        # v1.4: versheidscontrole. Niet bij een expliciete --datum (backtest).
        if not cfg.datum:
            vers, leeftijd = data_is_vers(score_datum, cfg.max_data_leeftijd)
            if not vers:
                print(
                    f"\n⛔ Data is te oud: nieuwste datum {score_datum.date()} "
                    f"is {leeftijd} dagen geleden (max {cfg.max_data_leeftijd}). "
                    f"Gestopt zonder picks. Controleer de job bouw_generieke_technicals."
                )
                if not cfg.geen_notify:
                    send_telegram(
                        "⚠️ <b>A Parameter-model gestopt</b>\n\n"
                        f"Nieuwste data: <b>{_esc(score_datum.date())}</b> "
                        f"({leeftijd} dagen geleden, max {cfg.max_data_leeftijd}).\n"
                        "Er zijn geen picks verstuurd. Controleer de job "
                        "<code>bouw_generieke_technicals</code>."
                    )
                return 1

        picks_per_horizon: dict[str, pd.DataFrame] = {}
        gewichten_per_horizon: dict[str, pd.DataFrame] = {}
        window_labels: dict[str, str] = {}

        for horizon in cfg.horizons:
            target_kolom = f"fwd_ret_{horizon}"
            print(f"\n{'=' * 70}")
            print(f"[{horizon}] START")
            print(f"{'=' * 70}")

            if target_kolom not in df.columns:
                print(f"[{horizon}] kolom {target_kolom} ontbreekt — overgeslagen")
                continue

            target_series = df[["datum", target_kolom]].dropna()
            if target_series.empty:
                print(f"[{horizon}] geen niet-lege {target_kolom} in DB — overgeslagen")
                continue

            laatste_target_datum = target_series["datum"].max()
            # Bij een expliciete backtestdatum begrenzen we de embargo-basis op de
            # laatste beschikbare target-datum zodat toekomstige returns niet lekken.
            effectieve_score = min(score_datum, laatste_target_datum) if cfg.datum else score_datum

            horizon_dagen = _horizon_naar_dagen(horizon)
            effectieve_embargo = calendar_embargo(horizon_dagen, minimum=cfg.embargo_dagen)

            print(f"[{horizon}] laatste target-datum : {laatste_target_datum.date()}")
            print(f"[{horizon}] scorebasis embargo   : {effectieve_score.date()}")
            print(f"[{horizon}] embargo (effectief)  : {effectieve_embargo}d")

            gewichten, window, train_start, train_einde = ic_met_fallback(
                df,
                target_kolom,
                effectieve_score,
                effectieve_embargo,
                cfg.rolling_dagen,
                cfg.fallback_dagen,
                cfg.min_datums_ic,
                cfg.ic_max_abs,
            )

            if gewichten.empty:
                print(f"[{horizon}] geen parameters met genoeg data — overgeslagen")
                print(
                    f"[{horizon}] (geprobeerd tot {train_einde.date()}, "
                    f"minste start {train_start.date()})"
                )
                schrijf_model_metrics(
                    conn,
                    run_datum=score_datum.date(),
                    horizon=horizon,
                    window_label=window,
                    train_start=train_start,
                    train_einde=train_einde,
                    embargo_dagen=effectieve_embargo,
                    gewichten_totaal_df=pd.DataFrame(),
                    gewichten_gefilterd_df=pd.DataFrame(),
                    n_picks=0,
                )
                continue

            print(f"[{horizon}] window: {window}")
            print(f"[{horizon}] train: {train_start.date()} → {train_einde.date()}")
            print(f"[{horizon}] {len(gewichten)} params vóór |IC|-filter")

            gewichten_ruw = gewichten.copy()
            gewichten = gewichten_ruw[gewichten_ruw["ic_abs"] >= cfg.min_ic].copy()

            if gewichten.empty:
                print(
                    f"[{horizon}] geen params met |IC| >= {cfg.min_ic} "
                    f"(hoogste was {gewichten_ruw['ic_abs'].max():.4f})"
                )
                schrijf_model_metrics(
                    conn,
                    run_datum=score_datum.date(),
                    horizon=horizon,
                    window_label=window,
                    train_start=train_start,
                    train_einde=train_einde,
                    embargo_dagen=effectieve_embargo,
                    gewichten_totaal_df=gewichten_ruw,
                    gewichten_gefilterd_df=pd.DataFrame(),
                    n_picks=0,
                )
                continue

            print(f"[{horizon}] {len(gewichten)} params na |IC|-filter")
            for _, row in gewichten.head(5).iterrows():
                print(
                    f"    {row['parameter']:<24} "
                    f"IC={row['ic']:+.4f} (raw {row['ic_raw']:+.4f}) "
                    f"IR={row['ic_ir']:+.2f} n={row['n_datums']}"
                )

            picks = score_voor_datum(df, score_datum, gewichten, cfg.top_n)
            if picks.empty:
                print(
                    f"[{horizon}] geen picks op score_datum {score_datum.date()} "
                    f"(geen data voor die datum?)"
                )
                schrijf_model_metrics(
                    conn,
                    run_datum=score_datum.date(),
                    horizon=horizon,
                    window_label=window,
                    train_start=train_start,
                    train_einde=train_einde,
                    embargo_dagen=effectieve_embargo,
                    gewichten_totaal_df=gewichten_ruw,
                    gewichten_gefilterd_df=gewichten,
                    n_picks=0,
                )
                continue

            picks_per_horizon[horizon] = picks
            gewichten_per_horizon[horizon] = gewichten
            window_labels[horizon] = window

            print(f"[{horizon}] Top {len(picks)}:")
            for rank, (_, row) in enumerate(picks.iterrows(), start=1):
                print(
                    f"    {rank:>2}. {row['ticker']:<12} "
                    f"score={row['score']:+.4f}  koers={_format_koers(row.get('koers'))}"
                )

            schrijf_rapporten(picks, gewichten, horizon)

            # === Metrics wegschrijven ===
            schrijf_model_metrics(
                conn,
                run_datum=score_datum.date(),
                horizon=horizon,
                window_label=window,
                train_start=train_start,
                train_einde=train_einde,
                embargo_dagen=effectieve_embargo,
                gewichten_totaal_df=gewichten_ruw,
                gewichten_gefilterd_df=gewichten,
                n_picks=len(picks),
            )

        print(f"\n{'=' * 70}")
        if not picks_per_horizon:
            print("Geen enkele horizon leverde picks op.")
            return 1

        if not cfg.geen_db_write:
            schrijf_naar_selecties(conn, picks_per_horizon, score_datum)

        if not cfg.geen_notify:
            bericht = bouw_telegram_bericht(
                picks_per_horizon,
                gewichten_per_horizon,
                window_labels,
                score_datum,
                horizons=cfg.horizons,
            )
            send_telegram(bericht)

        print("\n✅ Klaar.")
        return 0

    except Exception:
        traceback.print_exc()
        return 1
    finally:
        # Alleen sluiten als de verbinding daadwerkelijk tot stand is gekomen.
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
