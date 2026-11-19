#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bouw_forward_returns.py — FEATURE STORE v2.1

Gebruikt common/ voor gedeelde bouwstenen.
Bevat split-correctie, eenheidsbreuk-detectie en dubbele-selectie logging.

v2.2: Bestaande horizonlabels blijven behouden; alleen ontbrekende waarden
worden aangevuld. Een onvolledige nieuwe berekening wist nooit eerder opgeslagen
forward closes/returns. Een afwijkende instapkoers blokkeert een update,
zodat bestaande labels niet ongemerkt een andere basis krijgen.
Niet-NULL waarden worden niet opnieuw gelabeld. Dit voorkomt verlies of
onbedoelde herberekening van historische trainingslabels.
"""

import os
import sys
import math
import time
import traceback
import csv
from pathlib import Path
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

import psycopg2
import psycopg2.extras
import yfinance as yf
import pandas as pd

from common import today_str, esc, send_telegram, send_email
from horizon_utils import calendar_embargo, horizon_days

SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
MAX_TICKERS_PER_RUN = int(os.environ.get("MAX_TICKERS_PER_RUN", "400"))
HORIZONS = sorted(horizon_days(h) for h in os.environ.get("HORIZONS", "5,10,20,30,60").split(","))

EENHEID_RATIO_MIN = 70.0
EENHEID_RATIO_MAX = 150.0

DUBBELE_SELECTIE = os.environ.get("DUBBELE_SELECTIE", "eerste").strip().lower()
if DUBBELE_SELECTIE not in ("eerste", "laatste"):
    raise SystemExit(f"DUBBELE_SELECTIE moet 'eerste' of 'laatste' zijn, niet {DUBBELE_SELECTIE!r}")
if not HORIZONS or any(h <= 0 for h in HORIZONS) or len(set(HORIZONS)) != len(HORIZONS):
    raise SystemExit("HORIZONS moet unieke positieve gehele getallen bevatten")
if MAX_TICKERS_PER_RUN < 1:
    raise SystemExit("MAX_TICKERS_PER_RUN moet minimaal 1 zijn")

WAARSCHUWINGEN: List[str] = []
MAX_WAARSCHUWINGEN_IN_BERICHT = 10


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


def haal_openstaande_rijen(conn) -> List[dict]:
    nu = datetime.now(timezone.utc)
    voorwaarden = []
    params: Dict[str, str] = {}
    for h in HORIZONS:
        kolom = f"fwd_ret_{h}d"
        param_naam = f"cutoff_{h}"
        params[param_naam] = (nu - timedelta(days=calendar_embargo(h))).strftime("%Y-%m-%d")
        voorwaarden.append(f"(s.datum <= %({param_naam})s AND (f.ticker IS NULL OR f.{kolom} IS NULL))")
    horizon_filter = " OR ".join(voorwaarden)

    volgorde_dubbel = (
        "created_at ASC NULLS LAST, id ASC"
        if DUBBELE_SELECTIE == "eerste"
        else "created_at DESC NULLS LAST, id DESC"
    )

    query = f"""
        WITH s AS (
            SELECT DISTINCT ON (ticker, datum, strategie)
                   ticker, datum, strategie, beurs, koers
            FROM selecties
            WHERE koers IS NOT NULL
              AND ticker NOT LIKE '%%/%%'
            ORDER BY ticker, datum, strategie, {volgorde_dubbel}
        )
        SELECT s.ticker, s.datum, s.strategie, s.beurs, s.koers,
               f.entry_koers AS opgeslagen_instapkoers
        FROM s
        LEFT JOIN forward_returns f
          ON s.ticker = f.ticker AND s.datum = f.datum AND s.strategie = f.strategie
        WHERE ({horizon_filter})
        ORDER BY s.ticker, s.datum;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def bouw_upsert_query() -> str:
    """Vul ontbrekende velden aan; herlabel bestaande waarden nooit impliciet."""
    kolommen = ["ticker", "datum", "strategie", "beurs", "entry_koers"]
    for h in HORIZONS:
        kolommen.extend([f"fwd_close_{h}d", f"fwd_ret_{h}d"])

    kolom_lijst = ", ".join(kolommen)
    placeholders = ", ".join(f"%({k})s" for k in kolommen)
    updates = []
    for kolom in kolommen:
        if kolom not in ("ticker", "datum", "strategie"):
            updates.append(
                f"{kolom} = COALESCE(forward_returns.{kolom}, EXCLUDED.{kolom})"
            )
    updates.append("bijgewerkt_op = now()")
    update_lijst = ", ".join(updates)

    return f"""
        INSERT INTO forward_returns ({kolom_lijst})
        VALUES ({placeholders})
        ON CONFLICT (ticker, datum, strategie)
        DO UPDATE SET {update_lijst}
        WHERE forward_returns.entry_koers IS NULL
           OR abs(forward_returns.entry_koers - EXCLUDED.entry_koers)
              <= GREATEST(0.000001, abs(forward_returns.entry_koers) * 0.000001);
    """


def upsert_labels(conn, rijen: List[dict]) -> int:
    if not rijen:
        return 0
    kolommen = ["ticker", "datum", "strategie", "beurs", "entry_koers"]
    for h in HORIZONS:
        kolommen.extend([f"fwd_close_{h}d", f"fwd_ret_{h}d"])

    query = bouw_upsert_query()
    changed = 0
    with conn.cursor() as cur:
        for rij in rijen:
            volledige_rij = {k: rij.get(k) for k in kolommen}
            cur.execute(query, volledige_rij)
            changed += cur.rowcount
            if not cur.rowcount:
                waarschuw(f"{rij['ticker']} {rij['datum']}/{rij['strategie']}: andere instapkoers; bestaande labels behouden")
    conn.commit()
    return changed


def diagnose_sleutel(rij):
    return (rij['ticker'], str(rij['datum']), rij['strategie'])


def instap_blokkade(rij):
    koers = float(rij['koers'])
    if not math.isfinite(koers) or koers <= 0:
        return 'ongeldige_instapkoers'
    opgeslagen = rij.get('opgeslagen_instapkoers')
    if opgeslagen is None:
        return None
    opgeslagen = float(opgeslagen)
    if not math.isfinite(opgeslagen) or opgeslagen <= 0:
        return 'ongeldige_opgeslagen_instapkoers'
    if abs(opgeslagen-koers) > max(.000001, abs(opgeslagen)*.000001):
        return 'instapkoers_conflict'
    return None


def schrijf_diagnose(diagnose):
    pad = Path('results/forward_returns_diagnose.csv')
    pad.parent.mkdir(parents=True, exist_ok=True)
    with pad.open('w', encoding='utf-8', newline='') as bestand:
        writer = csv.DictWriter(bestand, fieldnames=[
            'ticker','datum','strategie','beurs','bronkoers','opgeslagen_instapkoers',
            'weekenddatum','status','reden','detail'])
        writer.writeheader()
        writer.writerows(diagnose.values())
    print(f'Diagnose: {pad}', flush=True)


def bereken_labels_voor_ticker(ticker: str, rijen: List[dict], diagnose=None) -> List[dict]:
    def note(rij, status, reden, detail=''):
        if diagnose is not None:
            diagnose[diagnose_sleutel(rij)].update(status=status, reden=reden, detail=detail)

    vroegste = min(r["datum"] for r in rijen)
    try:
        hist = yf.Ticker(ticker).history(
            start=vroegste, end=today_str(), auto_adjust=False, timeout=15
        )
    except Exception as e:
        waarschuw(f"{ticker}: download mislukt ({e})")
        for r in rijen: note(r, 'niet_berekend', 'download_mislukt', type(e).__name__)
        return []

    if hist is None or hist.empty or "Close" not in hist.columns:
        waarschuw(f"{ticker}: geen koersdata")
        for r in rijen: note(r, 'niet_berekend', 'geen_koersdata')
        return []

    hist = hist.copy()
    hist.index = pd.to_datetime(hist.index).tz_localize(None).normalize()
    if hist.index.hasnans or hist.index.has_duplicates:
        waarschuw(f"{ticker}: ongeldige of dubbele koersdatums; geen labels")
        for r in rijen: note(r, 'beoordeling_nodig', 'ongeldige_of_dubbele_koersdatums')
        return []
    hist = hist.sort_index()
    hist = hist.loc[hist.index < pd.Timestamp(today_str())]
    closes = pd.to_numeric(hist["Close"], errors="coerce")
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
            if pos_kandidaten >= len(datums) or datums[pos_kandidaten] != doel:
                waarschuw(f"{ticker} {r['datum']}: geen koers op selectiedatum; geen verschoven label")
                note(r, 'beoordeling_nodig', 'geen_koers_op_selectiedatum')
                continue
            entry_koers = float(r["koers"])
            if not math.isfinite(entry_koers) or entry_koers <= 0:
                note(r, 'beoordeling_nodig', 'ongeldige_instapkoers')
                continue

            pos_vorige = datums.searchsorted(doel, side="right") - 1
            prijsdatum = datums[pos_vorige] if pos_vorige >= 0 else doel
            factor = split_factor_na(splits, prijsdatum)
            if not math.isfinite(factor) or factor <= 0:
                waarschuw(f"{ticker}: ongeldige splitfactor {factor}")
                note(r, 'beoordeling_nodig', 'ongeldige_splitfactor')
                continue
            entry_adj = entry_koers / factor
            if factor != 1.0:
                n_split_gecorr += 1

            rij = {
                "ticker": ticker, "datum": r["datum"],
                "strategie": r["strategie"], "beurs": r["beurs"],
                "entry_koers": entry_koers,
            }
            for h in HORIZONS:
                idx = pos_kandidaten + h
                if idx < len(closes):
                    window = closes.iloc[pos_kandidaten : idx + 1]
                    gaps = window.index.to_series().diff().dt.days
                    if (not window.map(lambda v: math.isfinite(v) and v > 0).all()
                            or gaps.gt(7).any()):
                        waarschuw(f"{ticker} {r['datum']}/{h}d: ongeldige koersen of historiegat")
                        rij[f"fwd_close_{h}d"] = None
                        rij[f"fwd_ret_{h}d"] = None
                        continue
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
            if any(rij.get(f"fwd_ret_{h}d") is not None for h in HORIZONS):
                resultaten.append(rij)
                open_horizons = [str(h) for h in HORIZONS if rij.get(f'fwd_ret_{h}d') is None]
                note(r, 'berekend', 'labels_berekend', 'Nog niet berekend: '+','.join(open_horizons) if open_horizons else '')
            else:
                note(r, 'niet_berekend', 'geen_bruikbare_horizon')
        except Exception as e:
            waarschuw(f"{ticker} {r['datum']}/{r['strategie']}: {e}")
            note(r, 'niet_berekend', 'berekenfout', type(e).__name__)
            continue

    if n_split_gecorr or n_eenheid_gecorr:
        print(
            f"  [INFO] {ticker}: {n_split_gecorr} rijen split-gecorrigeerd, "
            f"{n_eenheid_gecorr} koersen eenheid-gecorrigeerd"
        )
    return resultaten


def run_build():
    if not SUPABASE_DB_URL:
        print("FOUT: SUPABASE_DB_URL ontbreekt.", file=sys.stderr)
        sys.exit(1)

    WAARSCHUWINGEN.clear()
    conn = psycopg2.connect(SUPABASE_DB_URL, connect_timeout=15)
    diagnose = {}
    try:
        open_rijen = haal_openstaande_rijen(conn)
        print(f"{len(open_rijen)} (ticker, datum, strategie)-rijen wachten op een (bijgewerkt) label.")

        per_ticker: Dict[str, List[dict]] = {}
        for r in open_rijen:
            reden = instap_blokkade(r)
            diagnose[diagnose_sleutel(r)] = dict(
                ticker=r['ticker'], datum=str(r['datum']), strategie=r['strategie'],
                beurs=r.get('beurs'), bronkoers=r['koers'],
                opgeslagen_instapkoers=r.get('opgeslagen_instapkoers'),
                weekenddatum=pd.Timestamp(r['datum']).weekday() >= 5,
                status='beoordeling_nodig' if reden else 'niet_opgevraagd',
                reden=reden or 'runlimiet', detail='')
            if reden:
                continue
            per_ticker.setdefault(r["ticker"], []).append(r)
        geblokkeerd = len(open_rijen) - sum(len(rows) for rows in per_ticker.values())
        print(f'{geblokkeerd} rijen geblokkeerd door instapcontrole; geen nieuwe koersaanvraag voor deze rijen.', flush=True)

        oudste_datum_per_ticker = {t: min(r["datum"] for r in rijen) for t, rijen in per_ticker.items()}
        alle_tickers_gesorteerd = sorted(per_ticker.keys(), key=lambda t: oudste_datum_per_ticker[t])
        tickers = alle_tickers_gesorteerd[:MAX_TICKERS_PER_RUN]
        overgeslagen = len(per_ticker) - len(tickers)
        print(
            f"{len(tickers)} unieke tickers te verwerken dit run"
            + (f" ({overgeslagen} tickers volgen in een volgend run)" if overgeslagen > 0 else "")
        )

        totaal_bijgewerkt = 0
        totaal_compleet = 0
        for i, ticker in enumerate(tickers, start=1):
            labels = bereken_labels_voor_ticker(ticker, per_ticker[ticker], diagnose=diagnose)
            aantal = upsert_labels(conn, labels)
            totaal_bijgewerkt += aantal
            totaal_compleet += sum(
                1 for l in labels if all(l.get(f"fwd_ret_{h}d") is not None for h in HORIZONS)
            )
            if i % 25 == 0 or i == len(tickers):
                print(f"  {i}/{len(tickers)} tickers verwerkt...")
            time.sleep(0.1)

        # An updated row can still lack a longer horizon. Count actual open rows.
        nog_wachtend = len(haal_openstaande_rijen(conn))
        opgelost = max(0, len(open_rijen) - nog_wachtend)
        lijnen = [f"📊 <b>Forward-Returns Feature Store — {today_str()}</b>", ""]
        if not open_rijen:
            lijnen.append("✅ Niets te doen: geen openstaande rijen om te labelen.")
        else:
            lijnen.append(f"🔄 <b>{totaal_bijgewerkt}</b> rijen bijgewerkt in forward_returns")
            lijnen.append(f"🏁 {opgelost} openstaande rijen afgerond; {totaal_compleet} berekeningen bevatten alle aangevraagde horizons")
            lijnen.append(f"📥 {len(open_rijen)} rijen waren openstaand, {nog_wachtend} blijven nog wachten")
            lijnen.append(f"🧾 {len(tickers)} unieke tickers verwerkt dit run")

        if WAARSCHUWINGEN:
            lijnen.append("")
            lijnen.append(f"⚠️ <b>{len(WAARSCHUWINGEN)} waarschuwing(en)</b>")
            for w in WAARSCHUWINGEN[:MAX_WAARSCHUWINGEN_IN_BERICHT]:
                lijnen.append(f"• {esc(w)}")

        lijnen.append(f'🔎 {geblokkeerd} rijen geblokkeerd door instapcontrole; bestaande labels behouden.')
        redenen = Counter(r['reden'] for r in diagnose.values())
        print('Diagnosetellingen: '+', '.join(f'{key}={value}' for key,value in sorted(redenen.items())), flush=True)
        if redenen.get('geen_koers_op_selectiedatum'):
            lijnen.append(f"📅 {redenen['geen_koers_op_selectiedatum']} rijen zonder koers op selectiedatum; geen datum verschoven.")

        bericht = "\n".join(lijnen)
        print(bericht, flush=True)
        send_telegram(bericht, parse_mode="HTML")
        send_email(f"Forward-Returns Store {today_str()}", bericht, html_body=True)
        print(f"\nKlaar. {totaal_bijgewerkt} rijen bijgewerkt.")
    finally:
        conn.close()
        schrijf_diagnose(diagnose)


if __name__ == "__main__":
    try:
        run_build()
    except Exception:
        fout = traceback.format_exc()
        print(fout, file=sys.stderr)
        send_telegram(
            f"❌ Forward-Returns FOUT:\n<pre>{esc(fout[-1500:])}</pre>",
            parse_mode="HTML",
        )
        send_email(
            f"❌ Forward-Returns FOUT — {today_str()}",
            f"<pre>{esc(fout)}</pre>", html_body=True,
        )
        raise
