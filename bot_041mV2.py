"""
bot_041mV2.py  (v3.1)
=====================
MRA Nitro Filter Bot. Wekelijkse evaluatie van de bronlijsten 041a t/m 060a.

WIJZIGINGEN t.o.v. v3 (oude code staat telkens als commentaar bij de wijziging)
------------------------------------------------------------------------------
1. Ierse aandelen: tickers op ".I" worden ".IR" (Yahoo-suffix voor Euronext
   Dublin). Enkel voor lijst 041. ".IR" is toegevoegd aan BEURS_CONFIG.
2. Schuldenvrije bedrijven vallen niet meer af: debtToEquity == 0 telde als
   "ontbrekend" (0 or 9999 -> 9999). Nu wordt 0 als 0 behandeld. Ontbreekt de
   waarde, dan kijkt de bot naar totalDebt == 0. Is de schuld echt onbekend,
   dan wordt de ticker overgeslagen (geen telling, geen verwijdering).
3. Banken, verzekeraars, nutsbedrijven en vastgoed (Yahoo-sectoren
   "Financial Services", "Utilities", "Real Estate"): de Debt-eis geldt niet.
4. VOL_MIN Europa versoepeld van 30% naar 20%.
5. Delisted-cache (tickers_XXXd.txt) is niet meer permanent:
   - oude (ongedateerde) items worden terug in de a-lijst gezet en opnieuw
     gescand (eenmalige herstelactie bij de eerste run);
   - nieuwe items krijgen een datum en worden na DELISTED_RETRY_WEKEN weken
     opnieuw geprobeerd;
   - een mislukte download-batch (API-fout) telt niet meer als "delisted".
6. EMA200 is geen filter meer: de trend ("boven"/"onder") wordt enkel nog als
   kolom "Trend" in de masterlijst bewaard.
7. Tickers die op dit moment in de delisted-cache staan komen niet in de
   exportlijst (x.txt) terecht (voorheen bleven ze bevroren in x staan).
8. Telegram-samenvatting toont ook: aantal niet gevonden tickers, Ierse
   conversies en terug-gezette delisted tickers.

BESTANDSNAMEN
  tickers_041a.txt  bronlijst (alle tickers van de beurs)
  tickers_041m.txt  masterlijst (history + parameters per ticker)
  tickers_041x.txt  exportlijst (actieve selectie voor de triplex bot)
  tickers_041d.txt  delisted cache, formaat: "TICKER:YYYY-MM-DD, ..."
                    (oud formaat zonder datum wordt automatisch hersteld)
"""

from __future__ import annotations

import os
import time
import logging
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import date

# ── Logging ───────────────────────────────────────────────────────────────────
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def _escape_md(tekst: str) -> str:
    """Verwijder problematische Markdown tekens die 400-fouten veroorzaken."""
    # Zorg voor gebalanceerde backticks en sterretjes
    # Vervang losse underscores buiten ticker-context
    return tekst


def send_telegram(tekst: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  ⚠️  Telegram niet geconfigureerd")
        return
    for i in range(0, len(tekst), 4096):
        chunk = tekst[i:i + 4096]
        # Probeer eerst met Markdown
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:
            # Fallback: stuur zonder Markdown (vermijdt parse-fouten)
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": chunk,
                        "disable_web_page_preview": True,
                    },
                    timeout=15,
                )
                print(f"  ⚠️  Telegram Markdown fout, verstuurd zonder opmaak: {e}")
            except Exception as e2:
                print(f"  ⚠️  Telegram fout: {e2}")
        if i + 4096 < len(tekst):
            time.sleep(1)


# ── Beursconfiguratie ─────────────────────────────────────────────────────────
# suffixen: [""] betekent "geen suffix" (Nasdaq/NYSE/CBoe-stijl, VS-notering).
# NB: 059 (Oostenrijk/Slovenie/Slowakije) is een gemengde lijst. Wenen (.VI)
# en Ljubljana (.LJ) zijn hier opgenomen; de Yahoo-suffix voor de Slowaakse
# beurs (Bratislava) is niet met voldoende zekerheid bekend en is bewust
# weggelaten — geef die door zodra bevestigd, in plaats van te gokken.
BEURS_CONFIG = {
    # WIJZIGING 1: ".IR" (Euronext Dublin) toegevoegd.
    # "041": {"naam": "Benelux", "suffixen": [".AS", ".BR", ".LU"]},   # oud
    "041": {"naam": "Benelux", "suffixen": [".AS", ".BR", ".LU", ".IR"]},
    "042": {"naam": "Parijs", "suffixen": [".PA"]},
    "043": {"naam": "Frankfurt", "suffixen": [".DE"]},
    "044": {"naam": "Spanje/Portugal", "suffixen": [".MC", ".LS"]},
    "045": {"naam": "Londen", "suffixen": [".L"]},
    "046": {"naam": "Milaan", "suffixen": [".MI"]},
    "047": {"naam": "Toronto", "suffixen": [".TO", ".V"]},
    "048": {"naam": "Nasdaq/NYSE", "suffixen": [""]},
    "049": {"naam": "Stockholm", "suffixen": [".ST"]},
    "050": {"naam": "Zurich", "suffixen": [".SW"]},
    "051": {"naam": "Warschau", "suffixen": [".WA"]},
    "052": {"naam": "Oslo", "suffixen": [".OL"]},
    "053": {"naam": "Kopenhagen", "suffixen": [".CO"]},
    "054": {"naam": "Helsinki", "suffixen": [".HE"]},
    "055": {"naam": "CSE", "suffixen": [".CN"]},
    "056": {"naam": "NYSE int", "suffixen": [""]},
    "057": {"naam": "NYSE", "suffixen": [""]},
    "058": {"naam": "TSXV", "suffixen": [".V"]},
    "059": {"naam": "Oostenrijk Slovenie Slovakije", "suffixen": [".VI", ".LJ"]},
}

ALLE_SUFFIXEN = set()
for _cfg in BEURS_CONFIG.values():
    for _s in _cfg["suffixen"]:
        if _s:
            ALLE_SUFFIXEN.add(_s)

# WIJZIGING 1: Ierse tickers in de bronlijst staan als ".I", Yahoo gebruikt ".IR".
IERSE_SUFFIX_OUD = ".I"
IERSE_SUFFIX_NIEUW = ".IR"

# WIJZIGING 3: Yahoo-sectoren waarvoor de Debt-eis niet geldt.
# "Financial Services" = banken, verzekeraars, vermogensbeheer.
SECTOREN_ZONDER_DEBT_EIS = {"Financial Services", "Utilities", "Real Estate"}

# ── Criteria per beurstype (Nitro Geoptimaliseerd) ───────────────────────────
EUROPA_BEURZEN = {
    "041", "042", "043", "044", "045", "046",
    "049", "050", "051", "052", "053", "054", "059",
}

CRITERIA = {
    "europa": {
        "ROE_MIN": 0.03,
        "DEBT_MAX": 150.0,
        "MARGE_MIN": 0.02,
        # WIJZIGING 4: vol-eis versoepeld.
        # "VOL_MIN": 0.30,   # oud
        "VOL_MIN": 0.20,
        "VOL_MAX": 0.80,
        "MIN_DAGOMZET": 500_000,
    },
    "noordamerika": {
        "ROE_MIN": 0.04,
        "DEBT_MAX": 140.0,
        "MARGE_MIN": 0.03,
        "VOL_MIN": 0.35,
        "VOL_MAX": 0.95,
        "MIN_DAGOMZET": 2_500_000,
    },
}


def get_criteria(getal: str) -> dict:
    return CRITERIA["europa"] if getal in EUROPA_BEURZEN else CRITERIA["noordamerika"]


# ── Constanten ────────────────────────────────────────────────────────────────
MAX_WEKEN_BUITEN = 3
BATCH_SIZE = 50
SLEEP_BATCH = 2.0
SLEEP_INFO = 0.3
REEKS_START = 41
REEKS_EINDE = 60

# WIJZIGING 5: na zoveel weken wordt een delisted-ticker opnieuw geprobeerd.
DELISTED_RETRY_WEKEN = 4

# Marker voor "download mislukt door API-fout" (verschilt van "geen data").
DL_FOUT = "FOUT"


# ── Bestandsnamen ─────────────────────────────────────────────────────────────
def pad_bron(g): return f"tickers_{g}a.txt"
def pad_master(g): return f"tickers_{g}m.txt"
def pad_export(g): return f"tickers_{g}x.txt"
def pad_delisted(g): return f"tickers_{g}d.txt"


# ── Ticker-normalisatie (Ierland) ─────────────────────────────────────────────
def normaliseer_ticker(getal: str, ticker: str) -> str:
    """WIJZIGING 1: enkel voor lijst 041 wordt '.I' omgezet naar '.IR'."""
    if getal == "041" and ticker.endswith(IERSE_SUFFIX_OUD):
        return ticker[:-len(IERSE_SUFFIX_OUD)] + IERSE_SUFFIX_NIEUW
    return ticker


# ── Suffix-correctie ──────────────────────────────────────────────────────────
def heeft_geldig_suffix(ticker: str, suffixen) -> bool:
    # Onbekende/niet-geconfigureerde beurs (suffixen=None): ticker niet
    # aanraken i.p.v. hem als "niet gevonden" af te keuren.
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


# ── Bronlijst lezen / aanvullen ───────────────────────────────────────────────
def _parse_tickers(tekst: str) -> list:
    inhoud = tekst.replace("\n", ",").replace(";", ",").replace("$", "")
    return [t.strip().upper() for t in inhoud.split(",") if t.strip()]


def voeg_toe_aan_bron(g: str, tickers) -> int:
    """
    WIJZIGING 5: zet tickers terug in de a-lijst als ze daar niet (meer) in
    staan. Geeft het aantal effectief toegevoegde tickers terug.
    LET OP: zorg dat tickers_*a.txt in je workflow mee gecommit wordt.
    """
    pad = pad_bron(g)
    if not os.path.exists(pad):
        return 0
    with open(pad, encoding="utf-8") as f:
        tekst = f.read()
    aanwezig = {normaliseer_ticker(g, t) for t in _parse_tickers(tekst)}
    nieuw = sorted({normaliseer_ticker(g, t.upper()) for t in tickers} - aanwezig)
    if not nieuw:
        return 0
    basis = tekst.rstrip().rstrip(",")
    with open(pad, "w", encoding="utf-8") as f:
        f.write((basis + ", " if basis else "") + ", ".join(nieuw))
    return len(nieuw)


# ── Delisted cache (met datum, niet meer permanent) ───────────────────────────
def laad_delisted(g: str) -> dict:
    """
    Leest tickers_XXXd.txt. Geeft {ticker: iso-datum of None} terug.
    Oud formaat ("A, B, C") geeft datum None; nieuw formaat is "A:2026-10-31".
    """
    pad = pad_delisted(g)
    if not os.path.exists(pad):
        return {}
    with open(pad, encoding="utf-8") as f:
        tekst = f.read()
    resultaat = {}
    for deel in tekst.split(","):
        deel = deel.strip()
        if not deel:
            continue
        if ":" in deel:
            t, d = deel.split(":", 1)
            resultaat[t.strip().upper()] = d.strip() or None
        else:
            resultaat[deel.upper()] = None
    return resultaat


def sla_delisted_op(g: str, delisted: dict) -> None:
    vandaag = date.today().isoformat()
    with open(pad_delisted(g), "w", encoding="utf-8") as f:
        f.write(", ".join(f"{t}:{delisted[t] or vandaag}" for t in sorted(delisted)))


def herstel_delisted(g: str) -> tuple:
    """
    WIJZIGING 5. Geeft (blijft_geblokkeerd: dict, aantal_terug_gezet: int) terug.
      - ongedateerde (oude) items  -> terug naar a-lijst + opnieuw scannen
      - gedateerde items ouder dan DELISTED_RETRY_WEKEN weken -> opnieuw scannen
      - recente gedateerde items   -> blijven geblokkeerd
    """
    raw = laad_delisted(g)
    if not raw:
        return {}, 0
    vandaag = date.today()
    behouden, terug = {}, []
    for t, d_str in raw.items():
        if d_str is None:
            terug.append(t)
            continue
        try:
            leeftijd = (vandaag - date.fromisoformat(d_str)).days
        except ValueError:
            terug.append(t)
            continue
        if leeftijd >= DELISTED_RETRY_WEKEN * 7:
            terug.append(t)
        else:
            behouden[t] = d_str
    if terug:
        voeg_toe_aan_bron(g, terug)
    sla_delisted_op(g, behouden)
    return behouden, len(terug)


# ── Batch OHLCV download ──────────────────────────────────────────────────────
def batch_download(tickers: list) -> dict:
    resultaat = {}
    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    print(f"  📥 OHLCV: {len(tickers)} tickers in {len(batches)} batches...")
    for i, batch in enumerate(batches):
        print(f"     Batch {i+1}/{len(batches)}...", end="", flush=True)
        try:
            raw = yf.download(batch, period="1y", progress=False,
                              auto_adjust=True, multi_level_index=True)
            for t in batch:
                try:
                    if len(batch) == 1:
                        df = raw.copy()
                        if isinstance(df.columns[0], tuple):
                            df.columns = [c[0] for c in df.columns]
                    else:
                        df = raw.xs(t, axis=1, level=1).dropna(how="all")
                    resultaat[t] = df if len(df) >= 50 else None
                except Exception:
                    resultaat[t] = None
            print(" ✅")
        except Exception as e:
            print(f" ❌ {e}")
            for t in batch:
                # WIJZIGING 5: API-fout is geen bewijs van delisting.
                # resultaat[t] = None   # oud: zette de hele batch op delisted
                resultaat[t] = DL_FOUT
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
        roe = info.get("returnOnEquity", 0) or 0
        growth = info.get("revenueGrowth", 0) or 0
        marge = info.get("profitMargins", 0) or 0

        # WIJZIGING 3: sector zonder Debt-eis (banken, verzekeraars,
        # nutsbedrijven, vastgoed).
        sector = info.get("sector") or ""
        geen_debt_eis = sector in SECTOREN_ZONDER_DEBT_EIS

        # WIJZIGING 2: schuldenvrije bedrijven.
        # oud: debt = info.get("debtToEquity", 9999) or 9999
        #      -> 0 (geen schuld) werd 9999 en viel dus altijd af.
        debt = info.get("debtToEquity")
        if debt is None:
            totaal_schuld = info.get("totalDebt")
            if totaal_schuld is not None and totaal_schuld == 0:
                debt = 0.0
        if debt is None and not geen_debt_eis:
            # Schuld echt onbekend: niet beoordelen (geen telling, geen
            # verwijdering) i.p.v. gokken.
            return False, {}, "geen_fundamentele_data"
        if debt is not None:
            debt = float(debt)
            if 0 < debt < 2:
                debt *= 100

        metrics = {
            "ROE": f"{roe:.1%}",
            "Debt": f"{debt:.1f}" if debt is not None else "n.v.t.",
            "Marge": f"{marge:.1%}",
        }
        falen = []
        if roe < crit["ROE_MIN"] and growth < 0.15:
            falen.append("ROE/Growth te laag")
        if not geen_debt_eis and debt > crit["DEBT_MAX"]:
            falen.append(f"Debt {debt:.0f}>{crit['DEBT_MAX']:.0f}")
        if marge < crit["MARGE_MIN"]:
            falen.append(f"Marge {marge:.1%}<{crit['MARGE_MIN']:.0%}")
        return (not falen), metrics, " | ".join(falen)
    except Exception as e:
        return False, {}, f"api_fout:{e}"


# ── Volatiliteit + liquiditeit check ─────────────────────────────────────────
def check_vol_liq(ticker: str, ohlcv: dict, crit: dict) -> tuple:
    """Geeft (geslaagd, vol_str, omzet_str, reden, trend) terug."""
    df = ohlcv.get(ticker)
    if df is None or isinstance(df, str) or len(df) < 50:
        return False, "?", "?", "te_weinig_data", "?"
    try:
        p = df["Close"].ffill()
        vol = float(p.pct_change().dropna().std() * np.sqrt(252))
        omzet = float((p * df["Volume"]).mean()) if "Volume" in df.columns else 0.0
        ema200 = p.ewm(span=200, adjust=False).mean().iloc[-1]
        last_p = p.iloc[-1]

        vol_str = f"{vol:.1%}"
        omzet_str = f"{omzet:,.0f}"
        # WIJZIGING 6: trend enkel als info bewaren, niet meer filteren.
        trend = "boven" if last_p >= ema200 else "onder"

        falen = []
        if vol < crit["VOL_MIN"]:
            falen.append(f"Saai:{vol:.1%}")
        elif vol > crit["VOL_MAX"]:
            falen.append(f"Wild:{vol:.1%}")
        if omzet < crit["MIN_DAGOMZET"]:
            falen.append(f"Omzet {omzet:,.0f}<{crit['MIN_DAGOMZET']:,.0f}")
        # if last_p < ema200:                 # oud: EMA200 als filter
        #     falen.append("Onder EMA200")
        return (not falen), vol_str, omzet_str, " | ".join(falen), trend
    except Exception as e:
        return False, "?", "?", f"vol_fout:{e}", "?"


# ── Masterlijst lezen ─────────────────────────────────────────────────────────
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
            entry = {"ticker": ticker}
            for deel in delen[1:]:
                if ":" in deel:
                    k, v = deel.split(":", 1)
                    entry[k.strip()] = v.strip()
                else:
                    entry["status"] = deel.strip()
            entry["weken_buiten"] = int(entry.get("weken_buiten", 0))
            master[ticker] = entry
    return master


# ── Masterlijst schrijven ─────────────────────────────────────────────────────
def sla_master_op(g: str, master: dict, naam: str) -> None:
    vandaag = date.today().strftime("%d/%m/%Y")
    crit = get_criteria(g)
    regels = [
        f"# MASTERLIJST {g} — {naam}",
        f"# Laatste update: {vandaag}",
        f"# Nitro Criteria: ROE>{crit['ROE_MIN']:.0%} | Debt<{crit['DEBT_MAX']:.0f} "
        f"(niet voor financials/nutsbedrijven/vastgoed) | Marge>{crit['MARGE_MIN']:.0%} | "
        f"Vol {crit['VOL_MIN']:.0%}-{crit['VOL_MAX']:.0%} | Omzet>€{crit['MIN_DAGOMZET']:,.0f}",
        "# Trend (EMA200) is enkel informatief en geen filter.",
        "# Kolommen: ticker | opname | ROE | Debt | Marge | Vol | Omzet | Trend | "
        "status [| weken_buiten] [| verwijderd]",
        "# " + "-" * 80,
    ]

    volgorde = {"actief": 0, "zwakker": 1, "verwijderd": 2}
    gesorteerd = sorted(
        master.values(),
        key=lambda e: (volgorde.get(e.get("status", "verwijderd"), 3), e["ticker"]),
    )

    for e in gesorteerd:
        t = e["ticker"]
        status = e.get("status", "?")
        opname = e.get("opname", "?")

        basis = (
            f"{t:<16} | opname:{opname} | ROE:{e.get('ROE','?')} | Debt:{e.get('Debt','?')} | "
            f"Marge:{e.get('Marge','?')} | Vol:{e.get('Vol','?')} | Omzet:{e.get('Omzet','?')} | "
            f"Trend:{e.get('Trend','?')}"
        )
        if status == "verwijderd":
            regels.append(
                f"{basis} | verwijderd | "
                f"verwijderd:{e.get('verwijderd', date.today().isoformat())}"
            )
        else:
            regel = f"{basis} | {status}"
            if status == "zwakker":
                regel += f" | weken_buiten:{e.get('weken_buiten', 1)}"
            regels.append(regel)

    with open(pad_master(g), "w", encoding="utf-8") as f:
        f.write("\n".join(regels) + "\n")


# ── Exportlijst schrijven ─────────────────────────────────────────────────────
def sla_export_op(g: str, master: dict, geblokkeerd=None) -> list:
    """
    WIJZIGING 7: tickers die nu in de delisted-cache staan komen niet in x.txt.
    """
    geblokkeerd = geblokkeerd or set()
    # export = sorted(t for t, e in master.items() if e.get("status") in ("actief", "zwakker"))  # oud
    export = sorted(
        t for t, e in master.items()
        if e.get("status") in ("actief", "zwakker") and t not in geblokkeerd
    )
    with open(pad_export(g), "w", encoding="utf-8") as f:
        f.write(", ".join(export))
    return export


# ── Master entry bijwerken ────────────────────────────────────────────────────
def update_master(master: dict, ticker: str, door_filter: bool, metrics: dict) -> str:
    vandaag = date.today().isoformat()
    if ticker not in master:
        if door_filter:
            master[ticker] = {
                "ticker": ticker, "status": "actief",
                "opname": vandaag, "weken_buiten": 0, **metrics
            }
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
    config = BEURS_CONFIG.get(getal, {"naam": f"Lijst {getal}", "suffixen": None})
    naam = config["naam"]
    suffixen = config["suffixen"]
    crit = get_criteria(getal)
    bron = pad_bron(getal)

    print(f"\n{'='*65}\n 📋 LIJST {getal} — {naam}\n{'='*65}")

    # ── 0. Delisted-cache: oude items terug naar a-lijst, verlopen items opnieuw ──
    delisted, n_terug = herstel_delisted(getal)
    if n_terug:
        print(f"  ♻️  {n_terug} tickers uit de delisted-cache terug in de scan")

    # ── 1. Bronlijst laden ────────────────────────────────────────────────────
    with open(bron, encoding="utf-8") as f:
        inhoud = f.read().replace("\n", ",").replace(";", ",").replace("$", "")
    ruwe_tickers = sorted(set(t.strip().upper() for t in inhoud.split(",") if t.strip()))

    # WIJZIGING 1: Ierse tickers .I -> .IR (enkel lijst 041)
    n_ierland = 0
    if getal == "041":
        n_ierland = sum(1 for t in ruwe_tickers if t.endswith(IERSE_SUFFIX_OUD))
        ruwe_tickers = sorted({normaliseer_ticker(getal, t) for t in ruwe_tickers})
        if n_ierland:
            print(f"  🇮🇪 {n_ierland} Ierse tickers omgezet van .I naar .IR")

    # ── 2. Suffix-correctie ───────────────────────────────────────────────────
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
    if niet_gevonden:
        print(f"  ❌ {len(niet_gevonden)} tickers niet te corrigeren: "
              f"{', '.join(t for t, _ in niet_gevonden[:15])}"
              f"{' ...' if len(niet_gevonden) > 15 else ''}")

    # ── 3. Scanlijst + master + OHLCV ─────────────────────────────────────────
    te_scannen = [t for t in tickers if t not in delisted]
    master = laad_master(getal)
    ohlcv = batch_download(te_scannen)

    # WIJZIGING 5: enkel "geen data" (None) gaat de delisted-cache in, met datum.
    # API-fouten (DL_FOUT) worden deze run overgeslagen en niet bewaard.
    vandaag_iso = date.today().isoformat()
    nieuw_delisted = {t for t, df in ohlcv.items() if df is None}
    fout_tickers = {t for t, df in ohlcv.items() if isinstance(df, str)}
    for t in nieuw_delisted:
        delisted[t] = vandaag_iso
    sla_delisted_op(getal, delisted)
    if fout_tickers:
        print(f"  ⚠️  {len(fout_tickers)} tickers overgeslagen door download-fout")

    actief = [t for t in te_scannen
              if t not in nieuw_delisted and t not in fout_tickers]

    # ── 4. Evaluatie per ticker ───────────────────────────────────────────────
    tellers = {"nieuw": [], "actief": [], "zwakker": [], "verwijderd": [], "geen_data": []}

    for ticker in actief:
        print(f"  {ticker:<16} ", end="", flush=True)

        fund_ok, fund_metrics, fund_reden = check_fundamenteel(ticker, crit)
        time.sleep(SLEEP_INFO)

        if "geen_fundamentele_data" in fund_reden:
            print("❓ Geen data")
            tellers["geen_data"].append(ticker)
            continue

        vol_ok, vol_str, omzet_str, vol_reden, trend = check_vol_liq(ticker, ohlcv, crit)

        door_filter = fund_ok and vol_ok
        status = update_master(
            master, ticker, door_filter,
            {**fund_metrics, "Vol": vol_str, "Omzet": omzet_str, "Trend": trend}
        )

        if door_filter:
            print(f"✅ ROE:{fund_metrics.get('ROE','')} Debt:{fund_metrics.get('Debt','')} "
                  f"Vol:{vol_str} Trend:{trend} → {status.upper()}")
        else:
            print(f"❌ {fund_reden or vol_reden} → {status}")

        tellers.get(status, tellers["geen_data"]).append(ticker)

    # ── 5. Opslaan ────────────────────────────────────────────────────────────
    sla_master_op(getal, master, naam)
    export = sla_export_op(getal, master, set(delisted))

    return {
        "getal": getal, "naam": naam, "tellers": tellers, "export": export,
        "correcties": correcties, "niet_gevonden": niet_gevonden,
        "n_terug": n_terug, "n_ierland": n_ierland,
    }


# ── Hoofd scan ────────────────────────────────────────────────────────────────
def scan_alle() -> None:
    start, vandaag, verwerkt, resultaten = time.time(), date.today().strftime("%d/%m/%Y"), [], {}

    print(f"\n{'='*65}\n 🤖 MRA NITRO FILTER BOT v3.1\n{'='*65}")

    for nr in range(REEKS_START, REEKS_EINDE + 1):
        getal = f"0{nr}"
        if os.path.exists(pad_bron(getal)):
            verwerkt.append(getal)
            resultaten[getal] = scan_lijst(getal)
            time.sleep(2)

    elapsed = time.time() - start
    m, s = int(elapsed // 60), int(elapsed % 60)

    # Samenvatting zonder * rondom tickers (veroorzaakte 400)
    regels = [
        "MRA Nitro Filter Bot v3.1",
        f"{vandaag}",
        f"Tijd: {m}m {s}s | {len(verwerkt)} lijsten",
        "━" * 25,
        "",
    ]

    for getal, res in resultaten.items():
        t = res["tellers"]
        naam = res["naam"]
        exp = res["export"]
        regels.append(f"{getal} — {naam} → {len(exp)} tickers")
        if t["nieuw"]:
            regels.append(f"  Nieuw: {', '.join(t['nieuw'])}")
        if t["verwijderd"]:
            regels.append(f"  Verwijderd: {', '.join(t['verwijderd'])}")
        if t["zwakker"]:
            regels.append(f"  Zwakker: {', '.join(t['zwakker'])}")
        if res["n_terug"]:
            regels.append(f"  Delisted terug in scan: {res['n_terug']}")
        if res["n_ierland"]:
            regels.append(f"  Ierse tickers .I naar .IR: {res['n_ierland']}")
        if res["niet_gevonden"]:
            regels.append(f"  Niet gevonden: {len(res['niet_gevonden'])}")
        regels.append("")

    tg = "\n".join(regels)
    send_telegram(tg)


# ── Start ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    scan_alle()
