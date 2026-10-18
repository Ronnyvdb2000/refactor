#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bouw_forward_returns.py — FEATURE STORE v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Bevat split-correctie, eenheidsbreuk-detectie en dubbele-selectie logging.
"""

import os
import sys
import math
import time
import html
import smtplib
import traceback
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import yfinance as yf
import pandas as pd

from common import (
    today_str, safe_float, esc,
    send_telegram, send_email
)

# ============================================================
# CONFIG
# ============================================================

SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
MAX_TICKERS_PER_RUN = int(os.environ.get("MAX_TICKERS_PER_RUN", "400"))
HORIZONS = sorted(int(h) for h in os.environ.get("HORIZONS", "5,10,20,30,60").split(","))

EENHEID_RATIO_MIN = 70.0
EENHEID_RATIO_MAX = 150.0

DUBBELE_SELECTIE = os.environ.get("DUBBELE_SELECTIE", "eerste").strip().lower()
if DUBBELE_SELECTIE not in ("eerste", "laatste"):
    raise SystemExit(f"DUBBELE_SELECTIE moet 'eerste' of 'laatste' zijn, niet {DUBBELE_SELECTIE!r}")

WAARSCHUWINGEN: List[str] = []
MAX_WAARSCHUWINGEN_IN_BERICHT = 10

# ============================================================
# HELPERS
# ============================================================

def waarschuw(tekst: str) -> None:
    print(f"  [WARN] {tekst}")
    WAARSCHUWINGEN.append(tekst)

def split_factor_na(splits: pd.Series, prijsdatum: pd.Timestamp) -> float:
    if splits is None or splits.empty:
        return 1.0
    na = splits[splits.index > prijsdatum]
    if na.empty:
        return 1.0
    return float(na.prod())

def corrigeer_eenheid(fwd_close: float, entry_adj: float) -> Tuple[float, bool]:
    ratio = fwd_close / entry_adj
    if EENHEID_RATIO_MIN <= ratio <= EENHEID_RATIO_MAX:
        return fwd_close / 100.0, True
    if (1.0 / EENHEID_RATIO_MAX) <= ratio <= (1.0 / EENHEID_RATIO_MIN):
        return fwd_close * 100.0, True
    return fwd_close, False

# ============================================================
# DATABASE
# ============================================================

def haal_openstaande_rijen(conn) -> List[dict]:
    nu = datetime.now(timezone.utc)
    voorwaarden = []
    params: Dict[str, str] = {}
    for h in HORIZONS:
        kolom = f"fwd_ret_{h}d"
        param_naam = f"cutoff_{h}"
        params[param_naam] = (nu - timedelta(days=int(h * 1.6) + 3)).strftime("%Y-%m-%d")
        voorwaarden.append(f"(s.datum <= %({param_naam})s AND (f.ticker IS NULL OR f.{kolom} IS NULL))")
    horizon_filter = " OR ".join(voorwaarden)

    volgorde_dubbel = "created_at ASC NULLS LAST, id ASC" if DUBBELE_SELECTIE == "eerste" else "created_at DESC NULLS LAST, id DESC"

    query = f"""
        WITH s AS (
            SELECT DISTINCT ON (ticker, datum, strategie)
                   ticker, datum, strategie, beurs, koers
            FROM selecties
            WHERE koers IS NOT NULL
              AND ticker NOT LIKE '%%/%%'
            ORDER BY ticker, datum, strategie, {volgorde_dubbel}
        )
        SELECT s.ticker, s.datum, s.strategie, s.beurs, s.koers
        FROM s
        LEFT JOIN forward_returns f
          ON s.ticker = f.ticker AND s.datum = f.datum AND s.strategie = f.strategie
        WHERE ({horizon_filter})
        ORDER BY s.ticker, s.datum;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        return cur.fetchall()

def upsert_labels(conn, rijen: List[dict]) -> int:
    if not rijen:
        return 0
    kolommen = ["ticker", "datum", "strategie", "beurs", "entry_koers"]
    for h in HORIZONS:
        kolommen += [f"fwd_close_{h}d", f"fwd_ret_{h}d"]

    kolom_lijst = ", ".join(kolommen)
    placeholders = ", ".join(f"%({k})s" for k in kolommen)
    update_lijst = ", ".join(f"{k} = EXCLUDED.{k}" for k in kolommen if k not in ("ticker", "datum", "strategie"))
    update_lijst += ", bijgewerkt_op = now()"

    query = f"""
        INSERT INTO forward_returns ({kolom_lijst})
        VALUES ({placeholders})
        ON CONFLICT (ticker, datum, strategie)
        DO UPDATE SET {update_lijst};
    """
    with conn.cursor() as cur:
        for rij in rijen:
            volledige_rij = {k: rij.get(k) for k in kolommen}
            cur.execute(query, volledige_rij)
    conn.commit()
    return len(rijen)

# ============================================================
# BEREKENINGEN
# ============================================================

def bereken_labels_voor_ticker(ticker: str, rijen: List[dict]) -> List[dict]:
    vroegste = min(r["datum"] for r in rijen)
    try:
        hist = yf.Ticker(ticker).history(start=vroegste, auto_adjust=False)
    except Exception as e:
        waarschuw(f"{ticker}: download mislukt ({e})")
        return []

    if hist is None or hist.empty or "Close" not in hist.columns:
        waarschuw(f"{ticker}: geen koersdata")
        return []

    closes = hist["Close"]
    closes.index = pd.to_datetime(closes.index).tz_localize(None)
    datums = closes.index

    splits = None
    if "Stock Splits" in hist.columns:
        sp = hist["Stock Splits"].copy()
        sp.index = pd.to_datetime(sp.index).tz_localize(None)
        splits = sp[sp > 0]

    n_split_gecorr = 0
    n_eenheid_gecorr = 0

    resultaten = []
    for r in rijen:
        try:
            doel = pd.Timestamp(r["datum"])
            pos_kandidaten = datums.searchsorted(doel)
            if pos_kandidaten >= len(datums):
                continue
            entry_koers = float(r["koers"])
            if not entry_koers or math.isnan(entry_koers) or entry_koers <= 0:
                continue

            pos_vorige = datums.searchsorted(doel, side="right") - 1
            prijsdatum = datums[pos_vorige] if pos_vorige >= 0 else doel

            factor = split_factor_na(splits, prijsdatum)
            entry_adj = entry_koers / factor
            if factor != 1.0:
                n_split_gecorr += 1

            rij = {"ticker": ticker, "datum": r["datum"], "strategie": r["strategie"], "beurs": r["beurs"], "entry_koers": entry_koers}
            for h in HORIZONS:
                idx = pos_kandidaten + h
                if idx < len(closes):
                    fwd_close = float(closes.iloc[idx])
                    if not math.isfinite(fwd_close) or fwd_close <= 0:
                        rij[f"fwd_close_{h}d"] = None
                        rij[f"fwd_ret_{h}d"] = None
                        continue
                    fwd_close, hersteld = corrigeer_eenheid(fwd_close, entry_adj)
                    if hersteld:
                        n_eenheid_gecorr += 1
                    rij[f"fwd_close_{h}d"] = fwd_close
                    rij[f"fwd_ret_{h}d"] = round((fwd_close / entry_adj - 1) * 100, 3)
                else:
                    rij[f"fwd_close_{h}d"] = None
                    rij[f"fwd_ret_{h}d"] = None
            resultaten.append(rij)
        except Exception as e:
            waarschuw(f"{ticker} {r['datum']}/{r['strategie']}: {e}")
            continue

    if n_split_gecorr or n_eenheid_gecorr:
        print(f"  [INFO] {ticker}: {n_split_gecorr} rijen split-gecorrigeerd, {n_eenheid_gecorr} koersen eenheid-gecorrigeerd")

    return resultaten

# ============================================================
# MAIN
# ============================================================

def run_build():
    if not SUPABASE_DB_URL:
        print("FOUT: SUPABASE_DB_URL ontbreekt.", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(SUPABASE_DB_URL)
    try:
        open_rijen = haal_openstaande_rijen(conn)
        print(f"{len(open_rijen)} (ticker, datum, strategie)-rijen wachten op een (bijgewerkt) label.")

        per_ticker: Dict[str, List[dict]] = {}
        for r in open_rijen:
            per_ticker.setdefault(r["ticker"], []).append(r)

        oudste_datum_per_ticker = {t: min(r["datum"] for r in rijen) for t, rijen in per_ticker.items()}
        alle_tickers_gesorteerd = sorted(per_ticker.keys(), key=lambda t: oudste_datum_per_ticker[t])
        tickers = alle_tickers_gesorteerd[:MAX_TICKERS_PER_RUN]
        overgeslagen = len(per_ticker) - len(tickers)
        print(f"{len(tickers)} unieke tickers te verwerken dit run" + (f" ({overgeslagen} tickers volgen in een volgend run)" if overgeslagen > 0 else ""))

        totaal_bijgewerkt = 0
        totaal_compleet = 0
        for i, ticker in enumerate(tickers, start=1):
            labels = bereken_labels_voor_ticker(ticker, per_ticker[ticker])
            aantal = upsert_labels(conn, labels)
            totaal_bijgewerkt += aantal
            totaal_compleet += sum(1 for l in labels if l.get(f"fwd_ret_{HORIZONS[-1]}d") is not None)
            if i % 25 == 0 or i == len(tickers):
                print(f"  {i}/{len(tickers)} tickers verwerkt...")
            time.sleep(0.1)

        nog_wachtend = len(open_rijen) - totaal_bijgewerkt

        # Telegram + Email samenvatting
        lijnen = [f"📊 <b>Forward-Returns Feature Store — {today_str()}</b>", ""]
        if len(open_rijen) == 0:
            lijnen.append("✅ Niets te doen: geen openstaande rijen om te labelen.")
        else:
            lijnen.append(f"🔄 <b>{totaal_bijgewerkt}</b> rijen bijgewerkt in forward_returns")
            lijnen.append(f"🏁 {totaal_compleet} daarvan nu volledig")
            lijnen.append(f"📥 {len(open_rijen)} rijen waren openstaand, {nog_wachtend} blijven nog wachten")
            lijnen.append(f"🧾 {len(tickers)} unieke tickers verwerkt dit run")

        if WAARSCHUWINGEN:
            lijnen.append("")
            lijnen.append(f"⚠️ <b>{len(WAARSCHUWINGEN)} waarschuwing(en)</b>")
            for w in WAARSCHUWINGEN[:MAX_WAARSCHUWINGEN_IN_BERICHT]:
                lijnen.append(f"• {esc(w)}")

        bericht = "\n".join(lijnen)
        send_telegram(bericht, parse_mode="HTML")
        send_email(f"Forward-Returns Store {today_str()}", bericht, html_body=True)

        print(f"\nKlaar. {totaal_bijgewerkt} rijen bijgewerkt.")
    finally:
        conn.close()

if __name__ == "__main__":
    try:
        run_build()
    except Exception:
        fout = traceback.format_exc()
        print(fout, file=sys.stderr)
        send_telegram(f"❌ Forward-Returns FOUT:\n<pre>{esc(fout[-1500:])}</pre>", parse_mode="HTML")
        send_email(f"❌ Forward-Returns FOUT — {today_str()}", f"<pre>{esc(fout)}</pre>", html_body=True)
        raise
