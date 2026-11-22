#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bouw_generieke_technicals.py
=============================

Bouwt per ticker/datum een rij met technische indicatoren in de tabel
generieke_technicals.

Scope: unieke tickers uit de x-lijsten 041–059.

Features (kolommen):
  ATR14, ATR14_pct, RSI14, IBS,
  MA50, MA200, pct_from_ma50, pct_from_ma200,
  vol_ratio_20d, high52w, pct_from_high52w,
  MACD, MACD_signaal, MACD_hist,
  BB_breedte, BB_percent_b,
  STOCH_K, STOCH_D, ADX14,
  REL_STERKTE_20D, HV20, HV60,
  DAGEN_SINDS_LOW52W, VOL_RATIO_50D,
  EMA8, EMA20, PCT_FROM_EMA8, PCT_FROM_EMA20, EMA8_MINUS_EMA20

Env:
  SUPABASE_DB_URL    verplicht
  BACKFILL_DATUM     optioneel; default = LAATSTE bar per ticker
                     (bar_datum, NIET vandaag). Zo kan de cron op elk
                     moment draaien zonder weekend-rijen te produceren.
  TELEGRAM_TOKEN / TELEGRAM_CHAT_ID (optioneel)
  EMAIL_USER / EMAIL_PASS / EMAIL_RECEIVER (optioneel)

Wijziging 2026-10-12:
  - datum_doel is nu optioneel.
Wijziging 2026-10-27:
  - Telegram en e-mail notificaties toegevoegd via common/ package.
Wijziging 2026-11-01:
  - De import uit common is nu veilig: bestaat het common/-pakket niet
    (zoals op main, waar common.py enkel een lege shim is), dan worden
    lokale notificatie-functies gebruikt. Voorheen crashte het script dan
    bij de import, nog VOOR er data werd verwerkt
    (ImportError: cannot import name 'send_telegram' from 'common').
  - De lokale send_telegram toont de echte foutmelding van Telegram.
"""

import os
import sys
import math
import time
import datetime as dt
import warnings
import traceback
import csv
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
import yfinance as yf
from common.yahoo import stop_on_throttle, YahooRateLimited

# Importeer de notificatie-functies uit onze common package.
# oud: from common import send_telegram, send_email, today_str
#      -> crashte op main, waar common/ ontbreekt en common.py geen functies bevat.
try:
    from common import send_telegram, send_email, today_str
except ImportError as _import_fout:
    print(f"[WARN] common-pakket niet bruikbaar ({_import_fout}); "
          f"lokale notificatie-functies worden gebruikt.")

    import smtplib
    import requests
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    def today_str() -> str:
        """Datum van vandaag als YYYY-MM-DD (lokale fallback)."""
        return dt.date.today().isoformat()

    def send_telegram(text: str, *, parse_mode: "str | None" = "Markdown",
                      silent_on_missing: bool = True) -> bool:
        """Verstuurt een Telegram-bericht en logt de echte fout bij mislukking."""
        token = os.getenv("TELEGRAM_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            if not silent_on_missing:
                print(text)
            print("[Telegram] Secrets ontbreken, overslaan.")
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text,
                   "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 200:
                print("[Telegram] Bericht verzonden.")
                return True
            print(f"[Telegram] MISLUKT: HTTP {r.status_code} - {r.text[:300]}")
            if parse_mode:
                payload.pop("parse_mode", None)
                r2 = requests.post(url, json=payload, timeout=15)
                if r2.status_code == 200:
                    print("[Telegram] Bericht verzonden (zonder opmaak).")
                    return True
                print(f"[Telegram] Ook zonder opmaak mislukt: "
                      f"HTTP {r2.status_code} - {r2.text[:300]}")
            return False
        except Exception as e:
            print(f"[Telegram] Fout: {e}")
            return False

    def send_email(subject: str, body: str, *, html_body: bool = False,
                   silent_on_missing: bool = True) -> bool:
        """Verstuurt een e-mail via Gmail SMTP (lokale fallback)."""
        user = os.getenv("EMAIL_USER", "")
        pwd = os.getenv("EMAIL_PASS", "")
        ontvanger = os.getenv("EMAIL_RECEIVER", "")
        if not user or not pwd or not ontvanger:
            if not silent_on_missing:
                print(f"[email] {subject}\n{body}")
            print("[Email] Secrets ontbreken, overslaan.")
            return False
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = ontvanger
        msg.attach(MIMEText(body, "html" if html_body else "plain", "utf-8"))
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
                s.starttls()
                s.login(user, pwd)
                s.sendmail(user, ontvanger, msg.as_string())
            print("[Email] Bericht verzonden.")
            return True
        except Exception as e:
            print(f"[Email] Fout: {e}")
            return False

warnings.filterwarnings("ignore")

DB_URL = os.environ.get("SUPABASE_DB_URL")
LOOKBACK_BUFFER_DAGEN = 400
MAX_BAR_AFSTAND_DAGEN = 7
# Minimaal aandeel tickers dat succesvol geschreven moet worden.
# Een gedeeltelijk mislukte run mag geen groene GitHub Actions-status krijgen.
MIN_SUCCES_RATIO = float(os.environ.get("MIN_SUCCES_RATIO", "0.80"))
if not 0.0 <= MIN_SUCCES_RATIO <= 1.0:
    raise ValueError("MIN_SUCCES_RATIO moet tussen 0 en 1 liggen")
INDEX_TICKER = "^GSPC"


# --------------------------------------------------------------------------
# Bestandslijst tickers
# --------------------------------------------------------------------------
def bouw_bestandslijst():
    return [f"data/tickers_{n:03d}x.txt" for n in range(41, 60)]


def load_tickers_from_file(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().replace(";", ",").replace(",", "\n").replace("$", "")
    result = []
    for line in raw.splitlines():
        t = line.strip().upper()
        if t and not t.startswith("#"):
            result.append(t)
    return sorted(set(result))


def alle_tickers():
    alle = set()
    for f in bouw_bestandslijst():
        alle.update(load_tickers_from_file(f))
    return sorted(alle)


# --------------------------------------------------------------------------
# Indicatoren
# --------------------------------------------------------------------------
def _veilig(waarde, is_int=False):
    try:
        f = float(waarde)
        if math.isnan(f) or math.isinf(f):
            return None
        return int(round(f)) if is_int else round(f, 4)
    except Exception:
        return None


def download_index_returns(start_datum):
    try:
        with stop_on_throttle():
            hist = yf.Ticker(INDEX_TICKER).history(
                start=start_datum, auto_adjust=True, timeout=15
            )
        if hist is None or hist.empty:
            return None
        hist.index = pd.to_datetime(hist.index).tz_localize(None)
        # Geef de indexkoers terug: de 20-daagse referentie wordt
        # later berekend op dezelfde handelsdatums als het aandeel.
        return pd.to_numeric(hist["Close"], errors="coerce")
    except YahooRateLimited:
        raise
    except Exception as e:
        print(f"[index] download mislukt: {e}")
        return None


def bereken_indicatoren(hist, index_ret=None):
    """
    Berekent alle indicatoren voor één ticker.
    """
    if hist is None or hist.empty:
        return pd.DataFrame()

    df = hist.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
        df = df.loc[:, ~df.columns.duplicated()]

    if "Close" not in df.columns:
        return pd.DataFrame()
    if isinstance(df["Close"], pd.DataFrame):
        df["Close"] = df["Close"].iloc[:, 0]

    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df.sort_index()

    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in df.columns:
            if isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "High" not in df.columns:
        df["High"] = df["Close"]
    if "Low" not in df.columns:
        df["Low"] = df["Close"]
    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    out = pd.DataFrame(index=df.index)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    out["ATR14"] = atr14
    out["ATR14_PCT"] = (atr14 / close) * 100

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi14 = 100 - (100 / (1 + rs))
    out["RSI14"] = rsi14

    ibs = (close - low) / (high - low).replace(0, np.nan)
    out["IBS"] = ibs

    out["MA50"] = close.rolling(50).mean()
    out["MA200"] = close.rolling(200).mean()
    out["PCT_FROM_MA50"] = ((close - out["MA50"]) / out["MA50"]) * 100
    out["PCT_FROM_MA200"] = ((close - out["MA200"]) / out["MA200"]) * 100

    vol_avg_20 = volume.rolling(20).mean()
    out["VOL_RATIO_20D"] = volume / vol_avg_20

    high52w = high.rolling(252).max()
    out["HIGH52W"] = high52w
    out["PCT_FROM_HIGH52W"] = ((close - high52w) / high52w) * 100

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_sig = macd.ewm(span=9, adjust=False).mean()
    out["MACD"] = macd
    out["MACD_SIGNAAL"] = macd_sig
    out["MACD_HIST"] = macd - macd_sig

    ma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_upper = ma20 + 2 * std20
    bb_lower = ma20 - 2 * std20
    out["BB_BREEDTE"] = ((bb_upper - bb_lower) / ma20) * 100
    out["BB_PERCENT_B"] = (close - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)

    low14 = low.rolling(14).min()
    high14 = high.rolling(14).max()
    k_ruw = 100 * (close - low14) / (high14 - low14).replace(0, np.nan)
    k = k_ruw.rolling(3).mean()
    d = k.rolling(3).mean()
    out["STOCH_K"] = k
    out["STOCH_D"] = d

    high_diff = high.diff()
    low_diff = -low.diff()
    plus_dm = pd.Series(
        np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0),
        index=df.index,
    )
    min_dm = pd.Series(
        np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0),
        index=df.index,
    )
    atr_wilder = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/14, adjust=False, min_periods=14).mean() / atr_wilder
    min_di = 100 * min_dm.ewm(alpha=1/14, adjust=False, min_periods=14).mean() / atr_wilder
    dx = 100 * (plus_di - min_di).abs() / (plus_di + min_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    out["ADX14"] = adx

    if index_ret is not None:
        aandeel_ret_20 = close.pct_change(20)
        # Reindex met forward fill voor afwijkende beurskalenders.
        # Niet ontbrekende indexrendementen stilzwijgend als nul behandelen.
        index_close = index_ret.reindex(df.index, method="ffill")
        index_ret_20 = index_close.pct_change(20, fill_method=None)
        out["REL_STERKTE_20D"] = (aandeel_ret_20 - index_ret_20) * 100
    else:
        out["REL_STERKTE_20D"] = None

    log_ret = np.log(close / close.shift(1))
    out["HV20"] = log_ret.rolling(20).std() * np.sqrt(252) * 100
    out["HV60"] = log_ret.rolling(60).std() * np.sqrt(252) * 100

    low52w = low.rolling(252).min()
    dagen_low = []
    for i in range(len(df)):
        if i < 251:
            dagen_low.append(None)
            continue
        venster = low.iloc[i-251:i+1]
        idx_low = venster.idxmin()
        dagen = (df.index[i] - idx_low).days
        dagen_low.append(dagen)
    out["DAGEN_SINDS_LOW52W"] = dagen_low

    vol_avg_50 = volume.rolling(50).mean()
    out["VOL_RATIO_50D"] = volume / vol_avg_50

    try:
        ema8_reeks = close.ewm(span=8, adjust=False).mean()
        ema20_reeks = close.ewm(span=20, adjust=False).mean()

        out["EMA8"] = ema8_reeks
        out["EMA20"] = ema20_reeks
        out["PCT_FROM_EMA8"] = ((close - ema8_reeks) / ema8_reeks) * 100
        out["PCT_FROM_EMA20"] = ((close - ema20_reeks) / ema20_reeks) * 100
        out["EMA8_MINUS_EMA20"] = ema8_reeks - ema20_reeks
    except Exception:
        out["EMA8"] = None
        out["EMA20"] = None
        out["PCT_FROM_EMA8"] = None
        out["PCT_FROM_EMA20"] = None
        out["EMA8_MINUS_EMA20"] = None

    out["Open"] = df["Open"] if "Open" in df.columns else df["Close"]
    out["High"] = df["High"]
    out["Low"] = df["Low"]
    out["Close"] = df["Close"]
    out["Volume"] = df["Volume"]

    return out


# --------------------------------------------------------------------------
# DB-write
# --------------------------------------------------------------------------
INSERT_QUERY = """
INSERT INTO generieke_technicals (
    ticker, datum,
    atr14, atr14_pct, rsi14, ibs,
    ma50, ma200, pct_from_ma50, pct_from_ma200,
    vol_ratio_20d, high52w, pct_from_high52w,
    macd, macd_signaal, macd_hist,
    bb_breedte, bb_percent_b,
    stoch_k, stoch_d, adx14,
    rel_sterkte_20d, hv20, hv60,
    dagen_sinds_low52w, vol_ratio_50d,
    ema8, ema20, pct_from_ema8, pct_from_ema20, ema8_minus_ema20
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (ticker, datum) DO UPDATE SET
    atr14 = EXCLUDED.atr14,
    atr14_pct = EXCLUDED.atr14_pct,
    rsi14 = EXCLUDED.rsi14,
    ibs = EXCLUDED.ibs,
    ma50 = EXCLUDED.ma50,
    ma200 = EXCLUDED.ma200,
    pct_from_ma50 = EXCLUDED.pct_from_ma50,
    pct_from_ma200 = EXCLUDED.pct_from_ma200,
    vol_ratio_20d = EXCLUDED.vol_ratio_20d,
    high52w = EXCLUDED.high52w,
    pct_from_high52w = EXCLUDED.pct_from_high52w,
    macd = EXCLUDED.macd,
    macd_signaal = EXCLUDED.macd_signaal,
    macd_hist = EXCLUDED.macd_hist,
    bb_breedte = EXCLUDED.bb_breedte,
    bb_percent_b = EXCLUDED.bb_percent_b,
    stoch_k = EXCLUDED.stoch_k,
    stoch_d = EXCLUDED.stoch_d,
    adx14 = EXCLUDED.adx14,
    rel_sterkte_20d = EXCLUDED.rel_sterkte_20d,
    hv20 = EXCLUDED.hv20,
    hv60 = EXCLUDED.hv60,
    dagen_sinds_low52w = EXCLUDED.dagen_sinds_low52w,
    vol_ratio_50d = EXCLUDED.vol_ratio_50d,
    ema8 = EXCLUDED.ema8,
    ema20 = EXCLUDED.ema20,
    pct_from_ema8 = EXCLUDED.pct_from_ema8,
    pct_from_ema20 = EXCLUDED.pct_from_ema20,
    ema8_minus_ema20 = EXCLUDED.ema8_minus_ema20;
"""


def schrijf_rij(conn, ticker, datum, rij):
    waarden = [
        ticker, datum,
        _veilig(rij.get("ATR14")),
        _veilig(rij.get("ATR14_PCT")),
        _veilig(rij.get("RSI14")),
        _veilig(rij.get("IBS")),
        _veilig(rij.get("MA50")),
        _veilig(rij.get("MA200")),
        _veilig(rij.get("PCT_FROM_MA50")),
        _veilig(rij.get("PCT_FROM_MA200")),
        _veilig(rij.get("VOL_RATIO_20D")),
        _veilig(rij.get("HIGH52W")),
        _veilig(rij.get("PCT_FROM_HIGH52W")),
        _veilig(rij.get("MACD")),
        _veilig(rij.get("MACD_SIGNAAL")),
        _veilig(rij.get("MACD_HIST")),
        _veilig(rij.get("BB_BREEDTE")),
        _veilig(rij.get("BB_PERCENT_B")),
        _veilig(rij.get("STOCH_K")),
        _veilig(rij.get("STOCH_D")),
        _veilig(rij.get("ADX14")),
        _veilig(rij.get("REL_STERKTE_20D")),
        _veilig(rij.get("HV20")),
        _veilig(rij.get("HV60")),
        _veilig(rij.get("DAGEN_SINDS_LOW52W"), is_int=True),
        _veilig(rij.get("VOL_RATIO_50D")),
        _veilig(rij.get("EMA8")),
        _veilig(rij.get("EMA20")),
        _veilig(rij.get("PCT_FROM_EMA8")),
        _veilig(rij.get("PCT_FROM_EMA20")),
        _veilig(rij.get("EMA8_MINUS_EMA20")),
    ]
    with conn.cursor() as cur:
        cur.execute(INSERT_QUERY, waarden)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def herstel_koers(ticker, start, index_ret, koersdatum):
    """One provider repair; never replace the requested day by an older bar."""
    try:
        with stop_on_throttle():
            fixed = yf.Ticker(ticker).history(
                start=start, auto_adjust=True, repair=True, timeout=15)
        if fixed is None or fixed.empty:
            return None
        if not {'Open', 'High', 'Low', 'Close', 'Volume'}.issubset(fixed.columns):
            return None
        g = bereken_indicatoren(fixed, index_ret)
        if g.empty or g.index.has_duplicates or koersdatum not in g.index:
            return None
        row = g.loc[koersdatum]
        prices = [float(row.get(name)) for name in ('Open', 'High', 'Low', 'Close')]
        volume = float(row.get('Volume'))
        if any(not math.isfinite(value) or value <= 0 for value in prices) or not math.isfinite(volume) or volume <= 0:
            return None
        op, high, low, close = prices
        if high < max(op, low, close) or low > min(op, high, close):
            return None
        return row
    except YahooRateLimited:
        raise
    except Exception as exc:
        print(f'[repair] {ticker}: herstel mislukt ({type(exc).__name__})', flush=True)
        return None


def schrijf_diagnose(rijen):
    pad = Path('results/generieke_technicals_dekking.csv')
    pad.parent.mkdir(parents=True, exist_ok=True)
    with pad.open('w', encoding='utf-8', newline='') as bestand:
        writer = csv.DictWriter(bestand, fieldnames=[
            'ticker', 'status', 'reden', 'detail', 'historierijen', 'koersdatum'])
        writer.writeheader()
        writer.writerows(rijen)
    print(f'Diagnose: {pad}', flush=True)


def geselecteerde_tickers(conn, cutoff):
    """Include recent selected assets even when their source is not an x-list."""
    end = pd.Timestamp(cutoff).normalize()
    start = end - pd.Timedelta(days=3)
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT ticker FROM selecties
            WHERE datum::date BETWEEN %s::date AND %s::date
            AND ticker IS NOT NULL ORDER BY ticker""", (str(start.date()), str(end.date())))
        return sorted({str(row[0]).strip() for row in cur.fetchall() if str(row[0]).strip()})


def main():
    if not DB_URL:
        sys.exit("SUPABASE_DB_URL ontbreekt")

    manifest_path = os.environ.get("FEATURE_BUILD_MANIFEST")
    if manifest_path:
        from pathlib import Path
        Path(manifest_path).unlink(missing_ok=True)

    datum_doel = os.environ.get("BACKFILL_DATUM")
    datum_doel_ts = pd.Timestamp(datum_doel).normalize() if datum_doel else None

    tickers = alle_tickers()
    if not tickers:
        sys.exit("Geen tickers gevonden in data/tickers_*.txt")

    print(f"Tickers: {len(tickers)}", flush=True)
    if datum_doel_ts is not None:
        print(f"Doeldatum (override): {datum_doel_ts.date()}")
    else:
        print("Doeldatum: (laatste bar per ticker)")

    ref_start_ts = (
        datum_doel_ts if datum_doel_ts is not None
        else pd.Timestamp.now().normalize()
    )
    start = (ref_start_ts - pd.Timedelta(days=LOOKBACK_BUFFER_DAGEN)).strftime("%Y-%m-%d")
    blokkade = ''
    try:
        with stop_on_throttle():
            index_ret = download_index_returns(start)
    except YahooRateLimited:
        index_ret = None
        blokkade = 'Yahoo-rate-limit bij indexdownload; verdere aanvragen gestopt'

    conn = psycopg2.connect(DB_URL)
    n_ok = 0
    n_skip = 0
    fouten = []
    diagnose = []
    # Vereiste model-features: schrijf geen onvolledige trainingsrij.
    verplichte_features = (
        "ATR14", "ATR14_PCT", "RSI14", "IBS", "MA50", "MA200",
        "PCT_FROM_MA50", "PCT_FROM_MA200", "VOL_RATIO_20D",
        "HIGH52W", "PCT_FROM_HIGH52W",
    )

    try:
        selected = geselecteerde_tickers(conn, ref_start_ts)
        tickers = sorted(set(tickers) | set(selected))
        print(f"[invoer] {len(selected)} recente geselecteerde tickers; totaal universum {len(tickers)}", flush=True)
        for i, ticker in enumerate(tickers, 1):
            uitkomst = dict(ticker=ticker, status='overgeslagen', reden='',
                            detail='', historierijen=0, koersdatum='')
            diagnose.append(uitkomst)
            try:
                if blokkade:
                    uitkomst.update(status='niet_opgevraagd', reden='providerstop', detail=blokkade)
                    n_skip += 1
                    continue
                if "/" in ticker:
                    uitkomst['reden'] = 'ongeldig_symbool'
                    n_skip += 1
                    continue
                with stop_on_throttle():
                    hist = yf.Ticker(ticker).history(start=start, auto_adjust=True, timeout=15)
                if hist is None or hist.empty:
                    uitkomst['reden'] = 'geen_historie'
                    n_skip += 1
                    continue

                hist.index = pd.to_datetime(hist.index).tz_localize(None)
                uitkomst['historierijen'] = len(hist)
                g = bereken_indicatoren(hist, index_ret)
                if g.empty:
                    uitkomst['reden'] = 'geen_indicatorhistorie'
                    n_skip += 1
                    continue

                if datum_doel_ts is not None:
                    pos = g.index.searchsorted(datum_doel_ts, side="right") - 1
                else:
                    pos = len(g) - 1

                if pos < 0:
                    uitkomst['reden'] = 'geen_koers_op_of_voor_doeldatum'
                    n_skip += 1
                    continue

                bar_datum_ts = g.index[pos]
                uitkomst['koersdatum'] = str(bar_datum_ts.date())

                referentie_ts = (
                    datum_doel_ts if datum_doel_ts is not None
                    else pd.Timestamp.now().normalize()
                )
                if (referentie_ts - bar_datum_ts).days > MAX_BAR_AFSTAND_DAGEN:
                    uitkomst['reden'] = 'verouderde_koers'
                    n_skip += 1
                    continue

                rij = g.iloc[pos]

                if (
                    pd.isna(rij.get("Close"))
                    or pd.isna(rij.get("High"))
                    or pd.isna(rij.get("Low"))
                ):
                    hersteld = herstel_koers(ticker, start, index_ret, bar_datum_ts)
                    if hersteld is None:
                        uitkomst.update(reden='ongeldige_koers', detail='Een Yahoo-repair zonder bruikbaar herstel van dezelfde koersdag')
                        n_skip += 1
                        continue
                    rij = hersteld
                    uitkomst['detail'] = 'Yahoo-repair op dezelfde koersdag; providerherstel, niet onafhankelijk geverifieerd'
                    print(f'[repair] {ticker}: koersdag {bar_datum_ts.date()} hersteld via Yahoo', flush=True)

                ontbrekend = [feature for feature in verplichte_features
                              if _veilig(rij.get(feature)) is None]
                if ontbrekend:
                    uitkomst.update(reden='ontbrekende_features', detail=','.join(ontbrekend))
                    n_skip += 1
                    continue

                # Eén transactie per ticker. Pas na een geslaagde COMMIT
                # telt de rij als geschreven; fouten kunnen geen volgende
                # ticker besmetten met een afgebroken PostgreSQL-transactie.
                schrijf_rij(conn, ticker, bar_datum_ts.date(), rij)
                conn.commit()
                n_ok += 1
                uitkomst.update(status='geschreven', reden='ok')
                time.sleep(0.15)

            except YahooRateLimited:
                blokkade = 'Yahoo-rate-limit; verdere aanvragen gestopt'
                uitkomst.update(status='afgebroken', reden='providerstop', detail=blokkade)
                n_skip += 1
            except Exception as e:
                uitkomst.update(status='fout', reden='verwerkingsfout', detail=f'{type(e).__name__}: {e}')
                # Ook na een mislukte COMMIT de verbinding expliciet
                # terugzetten. Als rollback zelf faalt, stoppen: de
                # toestand van de databaseverbinding is dan onzeker.
                try:
                    conn.rollback()
                except Exception as rollback_fout:
                    raise RuntimeError(
                        f"Databaseverbinding niet herstelbaar na fout bij {ticker}: "
                        f"{rollback_fout} (oorspronkelijke fout: {e})"
                    ) from rollback_fout
                fouten.append((ticker, str(e)))
            finally:
                if i % 100 == 0 or i == len(tickers):
                    print(f"  [{i}/{len(tickers)}] {n_ok} geschreven; {n_skip} overgeslagen; "
                          f"{len(fouten)} fouten", flush=True)

    finally:
        conn.close()
        schrijf_diagnose(diagnose)

    print()
    print("=" * 60)
    print("SAMENVATTING")
    print("=" * 60)
    print(f"Tickers verwerkt : {len(tickers)}")
    print(f"Rijen geschreven : {n_ok}")
    print(f"Overgeslagen     : {n_skip}")
    succes_ratio = n_ok / len(tickers)
    onvoldoende = succes_ratio < MIN_SUCCES_RATIO
    print(f"Succespercentage : {succes_ratio:.1%} (minimum {MIN_SUCCES_RATIO:.1%})")
    n_hersteld = sum(r['status'] == 'geschreven' and r['detail'].startswith('Yahoo-repair') for r in diagnose)
    print(f"Via Yahoo-repair opgeslagen: {n_hersteld}", flush=True)
    redenen = Counter(r['reden'] for r in diagnose if r['status'] != 'geschreven')
    diagnose_tekst = '\n'.join(f'{reden}: {aantal}' for reden, aantal in sorted(redenen.items()))
    feature_redenen = Counter(feature for r in diagnose if r['reden'] == 'ontbrekende_features'
                              for feature in r['detail'].split(','))
    if feature_redenen:
        diagnose_tekst += '\nOntbrekende features: ' + ', '.join(
            f'{feature}={aantal}' for feature, aantal in sorted(feature_redenen.items()))
    if diagnose_tekst:
        print('Redenen niet opgeslagen:\n' + diagnose_tekst, flush=True)
    if fouten:
        print(f"Fouten           : {len(fouten)}")
        for t, e in fouten[:10]:
            print(f"  {t}: {e}")

    # ============================================================
    # Telegram en e-mail notificatie
    # ============================================================
    datum_str = today_str()

    # Bouw het Telegram-bericht (Markdown)
    tg_bericht = (
        f"📊 *Generieke Technicals Update*\n"
        f"_{datum_str}_\n\n"
        f"✅ *Rijen geschreven:* {n_ok}\n"
        f"⏭️ *Overgeslagen:* {n_skip}\n"
        f"🛠️ *Via Yahoo-repair opgeslagen:* {n_hersteld}\n"
    )
    if fouten:
        tg_bericht += f"⚠️ *Fouten:* {len(fouten)}\n"

    if onvoldoende:
        tg_bericht += f"❌ *Onvoldoende dekking:* {succes_ratio:.1%} (minimum {MIN_SUCCES_RATIO:.1%})\n"
    if diagnose_tekst:
        tg_bericht += '\nRedenen niet opgeslagen:\n' + diagnose_tekst.replace('_', ' ')
    if blokkade:
        tg_bericht += '\n' + blokkade

    # Stuur Telegram (als de secrets bestaan)
    send_telegram(tg_bericht, parse_mode="Markdown")

    # Bouw en stuur e-mail
    email_onderwerp = f"Generieke Technicals Update - {datum_str}"
    email_body = (
        f"Generieke Technicals Update\n"
        f"Datum: {datum_str}\n\n"
        f"Tickers verwerkt: {len(tickers)}\n"
        f"Rijen geschreven: {n_ok}\n"
        f"Overgeslagen: {n_skip}\n"
        f"Via Yahoo-repair opgeslagen: {n_hersteld}\n"
    )
    if fouten:
        email_body += f"Aantal fouten: {len(fouten)}\n"
        for t, e in fouten[:10]:
            email_body += f"  {t}: {e}\n"

    if onvoldoende:
        email_body += f"\nFOUT: onvoldoende dekking ({succes_ratio:.1%} < {MIN_SUCCES_RATIO:.1%}).\n"
    email_body += '\nRedenen niet opgeslagen:\n' + diagnose_tekst
    send_email(email_onderwerp, email_body)

    if blokkade:
        raise RuntimeError(blokkade)
    if onvoldoende:
        raise RuntimeError(
            f"Slechts {n_ok}/{len(tickers)} tickers geschreven "
            f"({succes_ratio:.1%}); vereist minimaal {MIN_SUCCES_RATIO:.1%}."
        )

    from common.feature_pipeline import write_feature_manifest
    write_feature_manifest(diagnose, MIN_SUCCES_RATIO)


if __name__ == "__main__":
    import runpy
    runpy.run_module("maintenance.retired_feature_builder",run_name="__main__")
    main()
