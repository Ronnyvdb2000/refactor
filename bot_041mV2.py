#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_041mV2.py — MRA NITRO FILTER BOT v3.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Filtert de a-lijsten met strengere criteria (Nitro) en een EMA200-filter.
"""

import os
import sys
import time
import logging
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import date

from common import (
    today_str, safe_float, esc,
    download_history, send_telegram, send_email
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)

# ── Beursconfiguratie ─────────────────────────────────────────────────────────
BEURS_CONFIG = {
    "041": {"naam": "Benelux",         "suffixen": [".AS", ".BR", ".LU"]},
    "042": {"naam": "Parijs",          "suffixen": [".PA"]},
    "043": {"naam": "Frankfurt",       "suffixen": [".DE"]},
    "044": {"naam": "Spanje/Portugal", "suffixen": [".MC", ".LS"]},
    "045": {"naam": "Londen",          "suffixen": [".L"]},
    "046": {"naam": "Milaan",          "suffixen": [".MI"]},
    "047": {"naam": "Toronto",         "suffixen": [".TO", ".V"]},
    "048": {"naam": "Nasdaq/NYSE",     "suffixen": [""]},
    "049": {"naam": "Stockholm",       "suffixen": [".ST"]},
    "050": {"naam": "Zurich",          "suffixen": [".SW"]},
    "051": {"naam": "Warschau",        "suffixen": [".WA"]},
    "052": {"naam": "Oslo",            "suffixen": [".OL"]},
    "053": {"naam": "Kopenhagen",      "suffixen": [".CO"]},
    "054": {"naam": "Helsinki",        "suffixen": [".HE"]},
    "055": {"naam": "CSE",             "suffixen": [".CN"]},
    "056": {"naam": "NYSE int",        "suffixen": [""]},
    "057": {"naam": "NYSE",            "suffixen": [""]},
    "058": {"naam": "TSXV",            "suffixen": [".V"]},
    "059": {"naam": "Oostenrijk Slovenie Slovakije", "suffixen": [".VI", ".LJ"]},
}

ALLE_SUFFIXEN = set()
for _cfg in BEURS_CONFIG.values():
    for _s in _cfg["suffixen"]:
        if _s:
            ALLE_SUFFIXEN.add(_s)

# ── Criteria per beurstype (Nitro Geoptimaliseerd) ───────────────────────────
EUROPA_BEURZEN = {"041", "042", "043", "044", "045", "046", "049", "050", "051", "052", "053", "054", "059"}

CRITERIA = {
    "europa": {
        "ROE_MIN":      0.03,
        "DEBT_MAX":     150.0,
        "MARGE_MIN":    0.02,
        "VOL_MIN":      0.30,
        "VOL_MAX":      0.80,
        "MIN_DAGOMZET": 500_000,
    },
    "noordamerika": {
        "ROE_MIN":      0.04,
        "DEBT_MAX":     140.0,
        "MARGE_MIN":    0.03,
        "VOL_MIN":      0.35,
        "VOL_MAX":      0.95,
        "MIN_DAGOMZET": 2_500_000,
    },
}

def get_criteria(getal: str) -> dict:
    return CRITERIA["europa"] if getal in EUROPA_BEURZEN else CRITERIA["noordamerika"]

# ── Constanten ────────────────────────────────────────────────────────────────
MAX_WEKEN_BUITEN = 3
BATCH_SIZE       = 50
SLEEP_BATCH      = 2.0
SLEEP_INFO       = 0.3
REEKS_START      = 41
REEKS_EINDE      = 60

def pad_bron(g):     return f"tickers_{g}a.txt"
def pad_master(g):   return f"tickers_{g}m.txt"
def pad_export(g):   return f"tickers_{g}x.txt"
def pad_delisted(g): return f"tickers_{g}d.txt"

# ── Suffix-correctie ──────────────────────────────────────────────────────────
def heeft_geldig_suffix(ticker: str, suffixen) -> bool:
    if suffixen is None:
        return True
    if suffixen == [""]:
        return not any(ticker.endswith(s) for s in ALLE_SUFFIXEN if s)
    return any(ticker.endswith(s) for s in suffixen)

def strip_suffix(ticker: str) -> str:
    for s in sorted(ALLE_SUFFIXEN, key=len, reverse=True):
        if ticker.endswith(s):
            return ticker[:-len(s)]
    return ticker

def corrigeer_suffix(ticker: str, suffixen) -> tuple:
    if heeft_geldig_suffix(ticker, suffixen):
        return ticker, False, ""
    basis = strip_suffix(ticker)
    if suffixen == [""]:
        try:
            fi = yf.Ticker(basis).fast_info
            if getattr(fi, "last_price", None):
                return basis, ticker != basis, ""
        except Exception:
            pass
        return ticker, False, "niet gevonden Nasdaq/NYSE"
    for suffix in (suffixen or []):
        kandidaat = basis + suffix
        try:
            fi = yf.Ticker(kandidaat).fast_info
            if getattr(fi, "last_price", None):
                return kandidaat, True, ""
        except Exception:
            pass
        time.sleep(0.2)
    return ticker, False, f"niet gevonden met {suffixen}"

# ── Delisted cache ────────────────────────────────────────────────────────────
def laad_delisted(g: str) -> set:
    pad = pad_delisted(g)
    if not os.path.exists(pad):
        return set()
    with open(pad, encoding="utf-8") as f:
        return set(t.strip().upper() for t in f.read().split(",") if t.strip())

def sla_delisted_op(g: str, delisted: set) -> None:
    with open(pad_delisted(g), "w", encoding="utf-8") as f:
        f.write(", ".join(sorted(delisted)))

# ── Batch OHLCV download ──────────────────────────────────────────────────────
def batch_download(tickers: list) -> dict:
    resultaat = {}
    batches = [tickers[i:i+BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    print(f"  📥 OHLCV: {len(tickers)} tickers in {len(batches)} batches...")
    for i, batch in enumerate(batches):
        print(f"     Batch {i+1}/{len(batches)}...", end="", flush=True)
        try:
            df_batch = download_history(batch, period="1y")
            for t in batch:
                sub = df_batch[df_batch["Ticker"] == t].drop(columns=["Ticker"])
                resultaat[t] = sub if len(sub) >= 50 else None
            print(" ✅")
        except Exception as e:
            print(f" ❌ {e}")
            for t in batch:
                resultaat[t] = None
        if i < len(batches) - 1:
            time.sleep(SLEEP_BATCH)
    return resultaat

# ── Fundamentele check ────────────────────────────────────────────────────────
def check_fundamenteel(ticker: str, crit: dict) -> tuple:
    try:
        t_obj = yf.Ticker(ticker)
        info = t_obj.info
        if not info or "returnOnEquity" not in info:
            return False, {}, "geen_fundamentele_data"

        roe    = info.get("returnOnEquity", 0)    or 0
        growth = info.get("revenueGrowth", 0)     or 0
        debt   = info.get("debtToEquity",   9999) or 9999
        marge  = info.get("profitMargins",  0)    or 0
        if 0 < debt < 2:
            debt *= 100

        metrics = {"ROE": f"{roe:.1%}", "Debt": f"{debt:.1f}", "Marge": f"{marge:.1%}"}
        falen = []
        if roe < crit["ROE_MIN"] and growth < 0.15:
            falen.append("ROE/Growth te laag")
        if debt  > crit["DEBT_MAX"]:
            falen.append(f"Debt {debt:.0f}>{crit['DEBT_MAX']:.0f}")
        if marge < crit["MARGE_MIN"]:
            falen.append(f"Marge {marge:.1%}<{crit['MARGE_MIN']:.0%}")
        return (not falen), metrics, " | ".join(falen)
    except Exception as e:
        return False, {}, f"api_fout:{e}"

# ── Volatiliteit + liquiditeit check ─────────────────────────────────────────
def check_vol_liq(ticker: str, ohlcv: dict, crit: dict) -> tuple:
    df = ohlcv.get(ticker)
    if df is None or len(df) < 50:
        return False, "?", "?", "te_weinig_data"
    try:
        p      = df["Close"].ffill()
        vol    = float(p.pct_change().dropna().std() * np.sqrt(252))
        omzet  = float((p * df["Volume"]).mean())
        ema200 = p.ewm(span=200, adjust=False).mean().iloc[-1]
        last_p = p.iloc[-1]

        vol_str, omzet_str = f"{vol:.1%}", f"{omzet:,.0f}"
        falen = []
        if vol < crit["VOL_MIN"]:
            falen.append(f"Saai:{vol:.1%}")
        elif vol > crit["VOL_MAX"]:
            falen.append(f"Wild:{vol:.1%}")
        if omzet < crit["MIN_DAGOMZET"]:
            falen.append(f"Omzet {omzet:,.0f}<{crit['MIN_DAGOMZET']:,.0f}")
        if last_p < ema200:
            falen.append("Onder EMA200")
        return (not falen), vol_str, omzet_str, " | ".join(falen)
    except Exception as e:
        return False, "?", "?", f"vol_fout:{e}"

# ── Masterlijst lezen/schrijven ───────────────────────────────────────────────
def laad_master(g: str) -> dict:
    master = {}
    pad = pad_master(g)
    if not os.path.exists(pad):
        return master
    with open(pad, encoding="utf-8") as f:
        for regel in f:
            regel = regel.strip()
            if not regel or regel.startswith("#"):
                continue
            delen = [d.strip() for d in regel.split("|")]
            if not delen:
                continue
            ticker = delen[0].strip().upper()
            entry  = {"ticker": ticker}
            for deel in delen[1:]:
                if ":" in deel:
                    k, v = deel.split(":", 1)
                    entry[k.strip()] = v.strip()
                else:
                    entry["status"] = deel.strip()
            entry["weken_buiten"] = int(entry.get("weken_buiten", 0))
            master[ticker] = entry
    return master

def sla_master_op(g: str, master: dict, naam: str) -> None:
    vandaag = date.today().strftime("%d/%m/%Y")
    crit    = get_criteria(g)
    regels  = [
        f"# MASTERLIJST {g} — {naam}",
        f"# Laatste update: {vandaag}",
        f"# Nitro Criteria: ROE>{crit['ROE_MIN']:.0%} | Debt<{crit['DEBT_MAX']:.0f} | Marge>{crit['MARGE_MIN']:.0%} | Vol {crit['VOL_MIN']:.0%}-{crit['VOL_MAX']:.0%} | Omzet>€{crit['MIN_DAGOMZET']:,.0f}",
        f"# Kolommen: ticker | opname | ROE | Debt | Marge | Vol | Omzet | status [| weken_buiten] [| verwijderd]",
        "# " + "-" * 80,
    ]
    volgorde   = {"actief": 0, "zwakker": 1, "verwijderd": 2}
    gesorteerd = sorted(master.values(), key=lambda e: (volgorde.get(e.get("status", "verwijderd"), 3), e["ticker"]))
    
    for e in gesorteerd:
        t      = e["ticker"]
        status = e.get("status", "?")
        opname = e.get("opname", "?")
        if status == "verwijderd":
            regels.append(f"{t:<16} | opname:{opname} | ROE:{e.get('ROE','?')} | Debt:{e.get('Debt','?')} | Marge:{e.get('Marge','?')} | Vol:{e.get('Vol','?')} | Omzet:{e.get('Omzet','?')} | verwijderd | verwijderd:{e.get('verwijderd', date.today().isoformat())}")
        else:
            regel = f"{t:<16} | opname:{opname} | ROE:{e.get('ROE','?')} | Debt:{e.get('Debt','?')} | Marge:{e.get('Marge','?')} | Vol:{e.get('Vol','?')} | Omzet:{e.get('Omzet','?')} | {status}"
            if status == "zwakker":
                regel += f" | weken_buiten:{e.get('weken_buiten', 1)}"
            regels.append(regel)
            
    with open(pad_master(g), "w", encoding="utf-8") as f:
        f.write("\n".join(regels) + "\n")

def sla_export_op(g: str, master: dict) -> list:
    export = sorted(t for t, e in master.items() if e.get("status") in ("actief", "zwakker"))
    with open(pad_export(g), "w", encoding="utf-8") as f:
        f.write(", ".join(export))
    return export

def update_master(master: dict, ticker: str, door_filter: bool, metrics: dict) -> str:
    vandaag = date.today().isoformat()
    if ticker not in master:
        if door_filter:
            master[ticker] = {"ticker": ticker, "status": "actief", "opname": vandaag, "weken_buiten": 0, **metrics}
            return "nieuw"
        return "onbekend"
    entry = master[ticker]
    for k, v in metrics.items():
        if v and v != "?":
            entry[k] = v
    if door_filter:
        entry["status"] = "actief"
        entry["weken_buiten"] = 0
        return "actief"
    else:
        if entry.get("status") == "verwijderd":
            return "verwijderd"
        entry["weken_buiten"] = entry.get("weken_buiten", 0) + 1
        if entry["weken_buiten"] >= MAX_WEKEN_BUITEN:
            entry["status"] = "verwijderd"
            entry["verwijderd"] = vandaag
            return "verwijderd"
        entry["status"] = "zwakker"
        return "zwakker"

# ── Scan één lijst ────────────────────────────────────────────────────────────
def scan_lijst(getal: str) -> dict:
    config   = BEURS_CONFIG.get(getal, {"naam": f"Lijst {getal}", "suffixen": None})
    naam, suffixen, crit, bron = config["naam"], config["suffixen"], get_criteria(getal), pad_bron(getal)

    print(f"\n{'='*65}\n  📋 LIJST {getal} — {naam}\n{'='*65}")

    with open(bron, encoding="utf-8") as f:
        inhoud = f.read().replace("\n", ",").replace(";", ",").replace("$", "")
    ruwe_tickers = sorted(set(t.strip().upper() for t in inhoud.split(",") if t.strip()))

    tickers, correcties, niet_gevonden = [], [], []
    for ticker in ruwe_tickers:
        if heeft_geldig_suffix(ticker, suffixen):
            tickers.append(ticker)
        else:
            gecorr, gewijzigd, reden = corrigeer_suffix(ticker, suffixen)
            if gewijzigd:
                tickers.append(gecorr)
                correcties.append((ticker, gecorr))
            elif not reden:
                tickers.append(gecorr)
            else:
                niet_gevonden.append((ticker, reden))

    delisted, te_scannen = laad_delisted(getal), [t for t in tickers if t not in delisted]
    master = laad_master(getal)
    ohlcv = batch_download(te_scannen)

    nieuw_delisted = {t for t, df in ohlcv.items() if df is None}
    delisted.update(nieuw_delisted)
    sla_delisted_op(getal, delisted)

    actief = [t for t in te_scannen if t not in nieuw_delisted]
    tellers = {"nieuw": [], "actief": [], "zwakker": [], "verwijderd": [], "geen_data": []}

    for ticker in actief:
        print(f"  {ticker:<16} ", end="", flush=True)
        fund_ok, fund_metrics, fund_reden = check_fundamenteel(ticker, crit)
        time.sleep(SLEEP_INFO)

        if "geen_fundamentele_data" in fund_reden:
            print("❓ Geen data")
            tellers["geen_data"].append(ticker)
            continue

        vol_ok, vol_str, omzet_str, vol_reden = check_vol_liq(ticker, ohlcv, crit)
        door_filter = fund_ok and vol_ok
        status = update_master(master, ticker, door_filter, {**fund_metrics, "Vol": vol_str, "Omzet": omzet_str})

        if door_filter:
            print(f"✅ ROE:{fund_metrics.get('ROE','')} Debt:{fund_metrics.get('Debt','')} Vol:{vol_str} → {status.upper()}")
        else:
            print(f"❌ {fund_reden or vol_reden} → {status}")
        tellers.get(status, tellers["geen_data"]).append(ticker)

    sla_master_op(getal, master, naam)
    export = sla_export_op(getal, master)

    return {"getal": getal, "naam": naam, "tellers": tellers, "export": export, "correcties": correcties, "niet_gevonden": niet_gevonden}

# ── Hoofd scan ────────────────────────────────────────────────────────────────
def scan_alle():
    start, vandaag, verwerkt, resultaten = time.time(), date.today().strftime("%d/%m/%Y"), [], {}
    print(f"\n{'='*65}\n  🤖 MRA NITRO FILTER BOT v3.0\n{'='*65}")

    for nr in range(REEKS_START, REEKS_EINDE + 1):
        getal = f"0{nr}"
        if os.path.exists(pad_bron(getal)):
            verwerkt.append(getal)
            resultaten[getal] = scan_lijst(getal)
            time.sleep(2)

    elapsed = time.time() - start
    m, s    = int(elapsed // 60), int(elapsed % 60)

    regels = [f"MRA Nitro Filter Bot v3.0", f"{vandaag}", f"Tijd: {m}m {s}s | {len(verwerkt)} lijsten", "━" * 25, ""]
    for getal, res in resultaten.items():
        t, naam, exp = res["tellers"], res["naam"], res["export"]
        regels.append(f"{getal} — {naam} → {len(exp)} tickers")
        if t["nieuw"]: regels.append(f"  Nieuw:      {', '.join(t['nieuw'])}")
        if t["verwijderd"]: regels.append(f"  Verwijderd: {', '.join(t['verwijderd'])}")
        if t["zwakker"]: regels.append(f"  Zwakker:    {', '.join(t['zwakker'])}")
        regels.append("")

    tg = "\n".join(regels)
    send_telegram(tg, parse_mode="Markdown")

if __name__ == "__main__":
    scan_alle()
