#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bouw_forward_returns.py — FEATURE STORE: selecties -> forward-rendement v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Incrementeel, idempotent, met split-correctie en eenheidsbreuk-detectie.
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
import requests

from common import today_str, safe_float

# ============================================================
# CONFIG
# ============================================================

SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "")
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
# NOTIFICATIES
# ============================================================

def _esc(s) -> str:
    return html.escape(str(s))

def waarschuw(tekst: str) -> None:
    print(f"  [WARN] {tekst}")
    WAARSCHUWINGEN.append(tekst)

def send_telegram(tekst_html: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram-secrets ontbreken, overslaan.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": tekst_html, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.ok:
            print("Telegram verstuurd.")
        else:
            print(f"Telegram-fout: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Telegram-fout: {e}")

def send_email(onderwerp: str, html_body: str) -> None:
    if not EMAIL_USER or not EMAIL_PASS or not EMAIL_RECEIVER:
        print("Email-secrets ontbreken, overslaan.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = onderwerp
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_RECEIVER
    msg.attach(MIMEText(html_body, "html"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_RECEIVER, msg.as_string())
        print("Mail verstuurd.")
    except Exception as e:
        print(f"Mail-fout: {e}")

def maak_telegram_bericht(open_n: int, bijgewerkt: int, compleet: int, tickers_n: int, overgeslagen: int, nog_wachtend: int) -> str:
    lijnen = [f"📊 <b>Forward-Returns Feature Store — {today_str()}</b>", ""]
    if open_n == 0:
        lijnen.append("✅ Niets te doen: geen openstaande rijen om te labelen.")
    else:
        lijnen.append(f"🔄 <b>{bijgewerkt}</b> rijen bijgewerkt in forward_returns")
        lijnen.append(f"🏁 {compleet} daarvan nu volledig (alle {HORIZONS[-1]} handelsdagen ingevuld)")
        lijnen.append(f"📥 {open_n} rijen waren openstaand, {nog_wachtend} blijven nog wachten")
        lijnen.append(f"🧾 {tickers_n} unieke tickers verwerkt dit run" + (f" ({overgeslagen} nog te gaan)" if overgeslagen > 0 else ""))
    if WAARSCHUWINGEN:
        lijnen.append("")
        lijnen.append(f"⚠️ <b>{len(WAARSCHUWINGEN)} waarschuwing(en)</b>")
        for w in WAARSCHUWINGEN[:MAX_WAARSCHUWINGEN_IN_BERICHT]:
            lijnen.append(f"• {_esc(w)}")
        if len(WAARSCHUWINGEN) > MAX_WAARSCHUWINGEN_IN_BERICHT:
            lijnen.append(f"… en {len(WAARSCHUWINGEN) - MAX_WAARSCHUWINGEN_IN_BERICHT} meer (zie log)")
    return "\n".join(lijnen)

def maak_email_html(open_n: int, bijgewerkt: int, compleet: int, tickers_n: int, overgeslagen: int, nog_wachtend: int) -> str:
    if open_n == 0:
        kern = "<p>✅ Niets te doen: geen openstaande rijen om te labelen.</p>"
    else:
        kern = (
            f"<p>🔄 <b>{bijgewerkt}</b> rijen bijgewerkt in forward_returns</p>"
            f"<p>🏁 {compleet} daarvan nu volledig (alle {HORIZONS[-1]} handelsdagen ingevuld)</p>"
            f"<p>📥 {open_n} rijen waren openstaand, {nog_wachtend} blijven nog wachten</p>"
            f"<p>🧾 {tickers_n} unieke tickers verwerkt dit run" + (f" ({overgeslagen} nog te gaan)" if overgeslagen > 0 else "") + "</p>"
        )
    warn_html = ""
    if WAARSCHUWINGEN:
        items = "".join(f"<li>{_esc(w)}</li>" for w in WAARSCHUWINGEN)
        warn_html = f"<h3>⚠️ {len(WAARSCHUWINGEN)} waarschuwing(en)</h3><ul>{items}</ul>"
    return f"""
    <html><body style="font-family: Arial, sans-serif;">
        <div style="background:#e8f4fd; border:2px solid #2196f3; border-radius:10px; padding:20px;">
          <h2 style="margin-top:0;">📊 Forward-Returns Feature Store — {today_str()}</h2>
          {kern}
        </div>
        {warn_html}
    </body></html>
    """

def meld_fout(fout_tekst: str) -> None:
    tg = (f"❌ <b>Forward-Returns Feature Store — {today_str()}</b>\n\n"
          f"Het script is gecrasht:\n<pre>{_esc(fout_tekst[-1500:])}</pre>")
    mail = (f"<html><body style='font-family: Arial, sans-serif;'>"
            f"<h2>❌ Forward-Returns Feature Store — {today_str()}</h2>"
            f"<p>Het script is gecrasht:</p><pre>{_esc(fout_tekst)}</pre></body></html>")
    send_telegram(tg)
    send_email(f"❌ Forward-Returns FOUT — {today_str()}", mail)

# ============================================================
# KWALITEIT VAN HET LABEL
# ============================================================

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
# STAP 1: OPENSTAANDE RIJEN
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
            WHERE koers IS NOT NULL AND ticker NOT LIKE '%%/%%'
            ORDER BY ticker, datum, strategie, {volgorde_dubbel}
        )
        SELECT s.ticker, s.datum, s.strategie, s.beurs, s.koers
        FROM s
        LEFT JOIN forward_returns f ON s.ticker = f.ticker AND s.datum = f.datum AND s.strategie = f.strategie
        WHERE ({horizon_filter})
        ORDER BY s.ticker, s.datum;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        return cur.fetchall()

# ============================================================
# STAP 2: LABELS BEREKENEN
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

            rij = {"ticker": ticker, "datum": r["datum"], "strategie": r["strategie"], "beurs": r["beurs"], "entry_koers": entry_koers}
            for h in HORIZONS:
                idx = pos_kandidaten + h
                if idx < len(closes):
                    fwd_close = float(closes.iloc[idx])
                    if not math.isfinite(fwd_close) or fwd_close <= 0:
                        rij[f"fwd_close_{h}d"] = None
                        rij[f"fwd_ret_{h}d"] = None
                        continue

                    fwd_close, _ = corrigeer_eenheid(fwd_close, entry_adj)
                    rij[f"fwd_close_{h}d"] = fwd_close
                    rij[f"fwd_ret_{h}d"] = round((fwd_close / entry_adj - 1) * 100, 3)
                else:
                    rij[f"fwd_close_{h}d"] = None
                    rij[f"fwd_ret_{h}d"] = None
            resultaten.append(rij)
        except Exception as e:
            waarschuw(f"{ticker} {r['datum']}/{r['strategie']}: {e}")
            continue
    return resultaten

# ============================================================
# STAP 3: UPSERT
# ============================================================

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

    query = f"INSERT INTO forward_returns ({kolom_lijst}) VALUES ({placeholders}) ON CONFLICT (ticker, datum, strategie) DO UPDATE SET {update_lijst};"
    with conn.cursor() as cur:
        for rij in rijen:
            volledige_rij = {k: rij.get(k) for k in kolommen}
            cur.execute(query, volledige_rij)
    conn.commit()
    return len(rijen)

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
        
        print(f"{len(tickers)} unieke tickers te verwerken dit run" + (f" ({overgeslagen} volgen later)" if overgeslagen > 0 else ""))

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
        argumenten = (len(open_rijen), totaal_bijgewerkt, totaal_compleet, len(tickers), overgeslagen, nog_wachtend)
        send_telegram(maak_telegram_bericht(*argumenten))
        send_email(f"📊 Forward-Returns Feature Store — {today_str()}", maak_email_html(*argumenten))

        print(f"\nKlaar. {totaal_bijgewerkt} rijen bijgewerkt.")
    finally:
        conn.close()

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "build"
    try:
        run_build()
    except SystemExit:
        raise
    except Exception:
        fout = traceback.format_exc()
        print(fout, file=sys.stderr)
        meld_fout(fout)
        raise
