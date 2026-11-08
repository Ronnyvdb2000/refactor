#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01repititief.py — SEIZOENSEFFECTEN ENGINE v2.1

v2.1 t.o.v. v2.0 (herstel van functionaliteit die bij de refactor verloren ging):
  - Automatische historische check hersteld (v1.3): meteen na de live
    FDR-selectie wordt elk gevonden ticker/buckettype walk-forward gecheckt
    over alle beschikbare jaren; het resultaat (bv. "4/5j positief, PF 1.6")
    staat in het bericht en in Supabase. Er wordt nergens een tickerlijst
    vastgelegd.
  - Walk-forward backtest hersteld (v1.2): alle drie de buckettypes
    (weekdag, maand, kwartaal), BH-gecorrigeerd, resultaat in
    seizoen_backtest_trades.csv.
  - Supabase-parameters weer volledig: historische_check plus gemiddelde,
    winrate en p-waarde per buckettype.
  - De common/-refactor van v2.0 blijft behouden (download, tickerlijsten,
    notificaties, kosten via common).

v2.0: gebruikt common/ voor alle gedeelde bouwstenen.
Combineert weekdag-, maand- en kwartaaleffecten met BH-correctie.

Gebruik:
  python bot_01repititief.py live      # live rapport
  python bot_01repititief.py backtest  # walk-forward backtest (alle 3 buckettypes)
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
    send_telegram, send_email
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
TAX_RATE         = 0.10

TELEGRAM_THROTTLE_SEC = 1.2
TELEGRAM_MAX_POGINGEN = 4

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

def bereken_bucket_stats(rendementen: np.ndarray) -> Optional[dict]:
    n = len(rendementen)
    if n < 5:
        return None
    gemiddelde = float(np.mean(rendementen))
    win_rate   = float(np.mean(rendementen > 0))
    _, p_waarde = stats.ttest_1samp(rendementen, 0.0)
    return {"gemiddelde": gemiddelde, "win_rate": win_rate, "p_waarde": float(p_waarde), "n": n}

def analyseer_ticker(ticker: str, df_ticker: pd.DataFrame) -> Optional[SeizoenKandidaat]:
    try:
        g = df_ticker.sort_values("Date").copy()
        if len(g) < 250 * SZ_CFG["min_jaren"]:
            return None

        g = g.set_index("Date")
        rendement = g["Close"].pct_change().dropna()
        aantal_jaren = (rendement.index[-1] - rendement.index[0]).days / 365.25
        if aantal_jaren < SZ_CFG["min_jaren"]:
            return None

        current_price = safe_float(g["Close"].iloc[-1])
        if current_price <= 0 or math.isnan(current_price):
            return None

        vandaag = dt.datetime.now(dt.timezone.utc)
        huidige_weekdag = vandaag.weekday()
        huidige_maand   = vandaag.month
        huidig_kwartaal = (vandaag.month - 1) // 3 + 1

        weekdag_stats  = bereken_bucket_stats(rendement[rendement.index.weekday == huidige_weekdag].values)
        maand_stats    = bereken_bucket_stats(rendement[rendement.index.month == huidige_maand].values)
        kwartaal_stats = bereken_bucket_stats(rendement[rendement.index.quarter == huidig_kwartaal].values)

        if not (weekdag_stats or maand_stats or kwartaal_stats):
            return None

        return SeizoenKandidaat(
            ticker=ticker, price=round(current_price, 2),
            weekdag=weekdag_stats, maand=maand_stats, kwartaal=kwartaal_stats,
            jaren_data=round(aantal_jaren, 1),
        )
    except Exception as e:
        print(f"[WARN] {ticker}: fout — {e}")
        return None

def pas_bh_toe_op_beurs(kandidaten: List[SeizoenKandidaat], alpha: float) -> List[SeizoenSignaal]:
    significant_per_kandidaat: Dict[int, Set[str]] = {}

    for bucket_key in BUCKET_TYPES:
        indices = [i for i, k in enumerate(kandidaten) if getattr(k, bucket_key) is not None]
        if not indices:
            continue
        p_waarden = [getattr(kandidaten[i], bucket_key)["p_waarde"] for i in indices]
        mask = bh_correctie(p_waarden, alpha)
        for pos, kandidaat_idx in enumerate(indices):
            if mask[pos]:
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
        ))
    return resultaten

# ============================================================
# OUTPUT
# ============================================================

def _bucket_regel(key: str, stat: Optional[dict], significante_buckets: Set[str], checks: Optional[Dict[str, dict]] = None) -> Optional[str]:
    if key not in significante_buckets or not stat:
        return None
    vandaag = dt.datetime.now(dt.timezone.utc)
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
        regel += (f" — check: {check['jaren_winst']}/{check['jaren_getest']}j positief, "
                  f"{check['n_trades']}x, PF {check['pf']:.2f}")
    else:
        regel += " — check: onvoldoende historische trades"
    return regel

def sig_regel(s: SeizoenSignaal) -> str:
    onderbouwing = [r for r in (
        _bucket_regel("weekdag", s.weekdag, s.significante_buckets, s.checks),
        _bucket_regel("maand", s.maand, s.significante_buckets, s.checks),
        _bucket_regel("kwartaal", s.kwartaal, s.significante_buckets, s.checks),
    ) if r]
    return (
        f"• `{s.ticker}` score {s.score*100:+.2f}% | EUR{s.price:.2f} | "
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
        f"Benjamini-Hochberg FDR={SZ_CFG['fdr_alpha']:.2f} per buckettype_",
    ]
    return "\n\n".join(delen)

# ============================================================
# HISTORISCHE CHECK + BACKTEST-BOUWSTENEN  (hersteld uit v1.3)
# ============================================================
# Voor elk buckettype (weekdag/maand/kwartaal) en elk testjaar wordt de
# bucket-statistiek herberekend op basis van UITSLUITEND de jaren die vóór
# dat testjaar liggen (expanding window, geen look-ahead).
#
# Maand/kwartaal: één positie, aangehouden van de eerste tot de laatste
# handelsdag van die maand/dat kwartaal in het testjaar.
# Weekdag: elk voorkomen van de doelweekdag in het testjaar wordt apart
# verhandeld: kopen op de vorige handelsdag-close, verkopen op de close van
# de doelweekdag zelf (1-dagstrade, telkens met kosten/slippage/taks).

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

def _trades_voor_bucket(bucket_key: str, waarde: int, g: pd.DataFrame, test_jaar: int) -> List[Tuple[float, float]]:
    """Geeft (entry_price, exit_price)-paren terug voor deze ticker/waarde/jaar."""
    if bucket_key == "maand":
        data = g[(g.index.year == test_jaar) & (g.index.month == waarde)]
        if len(data) < 5:
            return []
        entry = safe_float(data["Close"].iloc[0]) * (1 + SLIPPAGE_PCT)
        exit_ = safe_float(data["Close"].iloc[-1]) * (1 - SLIPPAGE_PCT)
        return [(entry, exit_)] if entry > 0 and not math.isnan(entry) and not math.isnan(exit_) else []

    if bucket_key == "kwartaal":
        data = g[(g.index.year == test_jaar) & (g.index.quarter == waarde)]
        if len(data) < 10:
            return []
        entry = safe_float(data["Close"].iloc[0]) * (1 + SLIPPAGE_PCT)
        exit_ = safe_float(data["Close"].iloc[-1]) * (1 - SLIPPAGE_PCT)
        return [(entry, exit_)] if entry > 0 and not math.isnan(entry) and not math.isnan(exit_) else []

    # weekdag: elk voorkomen apart, 1-dagstrade (vorige close -> close van de doeldag)
    jaar_data = g[g.index.year == test_jaar]
    if jaar_data.empty:
        return []
    posities = np.where(jaar_data.index.weekday == waarde)[0]
    trades = []
    for pos in posities:
        if pos == 0:
            continue  # geen vorige handelsdag binnen dit testjaar beschikbaar
        entry = safe_float(jaar_data["Close"].iloc[pos - 1]) * (1 + SLIPPAGE_PCT)
        exit_ = safe_float(jaar_data["Close"].iloc[pos]) * (1 - SLIPPAGE_PCT)
        if entry > 0 and not math.isnan(entry) and not math.isnan(exit_):
            trades.append((entry, exit_))
    return trades

def _bereken_pnl(entry_price: float, exit_price: float) -> Dict:
    risico_bedrag = START_CAPITAL * 0.05
    aandelen = max(1, int(risico_bedrag / entry_price))
    investering = entry_price * aandelen + trade_cost(entry_price * aandelen)
    gross = exit_price * aandelen
    cost  = trade_cost(gross)
    pnl   = gross - cost - investering
    tax   = pnl * TAX_RATE if pnl > 0 else 0.0
    net   = pnl - tax
    return {"size": aandelen, "pnl": round(pnl, 2), "tax": round(tax, 2), "net": round(net, 2)}

def _boek_trade(trades_list: List[Dict], ticker: str, jaar: int, bucket_key: str,
                 waarde: int, entry_price: float, exit_price: float, train_stat: dict) -> None:
    r = _bereken_pnl(entry_price, exit_price)
    trades_list.append({
        "ticker": ticker, "jaar": jaar, "bucket_type": bucket_key, "bucket_waarde": waarde,
        "train_gemiddelde": train_stat["gemiddelde"], "train_p": train_stat["p_waarde"],
        "entry_price": round(entry_price, 4), "exit_price": round(exit_price, 4),
        "size": r["size"], "pnl": r["pnl"], "tax": r["tax"], "net": r["net"],
    })

def historische_check(bucket_key: str, waarde: int, g: pd.DataFrame) -> Optional[dict]:
    """
    Automatische walk-forward-check: past het exact zelfde handelsmechanisme
    toe als de backtest (_trades_voor_bucket), maar dan enkel op deze ene
    ticker/bucket/waarde-combinatie -- meteen na de live FDR-selectie, zonder
    dat er ooit een tickerlijst wordt vastgelegd. "waarde" is de bucketwaarde
    van VANDAAG (de weekdag/maand/kwartaal die net significant bleek); hier
    wordt gekeken hoe die zich in elk beschikbaar historisch jaar gedroeg.
    """
    testjaren = range(pd.Timestamp(BACKTEST_START).year, dt.date.today().year + 1)
    resultaten_per_jaar: Dict[int, float] = {}
    n_trades = 0
    netto_totaal = 0.0

    for jaar in testjaren:
        netto_dit_jaar = 0.0
        gevonden = False
        for entry_price, exit_price in _trades_voor_bucket(bucket_key, waarde, g, jaar):
            r = _bereken_pnl(entry_price, exit_price)
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
    winst_totaal  = sum(v for v in resultaten_per_jaar.values() if v > 0)
    verlies_totaal = abs(sum(v for v in resultaten_per_jaar.values() if v <= 0))
    pf = winst_totaal / max(verlies_totaal, 1e-9)

    return {
        "jaren_getest": jaren_getest, "jaren_winst": jaren_winst,
        "n_trades": n_trades, "netto_totaal": round(netto_totaal, 2), "pf": round(pf, 2),
    }

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    print(f"{'='*60}\nSEIZOENSEFFECTEN — LIVE  {today_str()}\n{'='*60}")

    exchange_tickers: Dict[str, List[str]] = {}
    all_tickers: List[str] = []

    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist:
            continue
        ex_name = label_voor(f_name)
        exchange_tickers[ex_name] = tlist
        all_tickers.extend(tlist)
        print(f"  {ex_name}: {len(tlist)} tickers")

    all_tickers = sorted(set(all_tickers))
    if not all_tickers:
        print("[ERROR] Geen ticker bestanden gevonden.")
        return

    print(f"\nTotaal: {len(all_tickers)} unieke tickers")
    print(f"Data downloaden ({SZ_CFG['lookback_years']} jaar)...")
    df = download_history(all_tickers, period=f"{SZ_CFG['lookback_years']}y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    print(f"Data geladen: {df['Ticker'].nunique()} tickers, {len(df)} rijen")
    email_delen: List[str] = []

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")
        df_ex = df[df["Ticker"].isin(tlist)].copy()

        kandidaten: List[SeizoenKandidaat] = []
        for ticker, group in df_ex.groupby("Ticker", sort=False):
            k = analyseer_ticker(ticker, group)
            if k is not None:
                kandidaten.append(k)

        if not kandidaten:
            print(f"  → Overgeslagen: {ex_name} (te weinig data)")
            continue

        top_alles = pas_bh_toe_op_beurs(kandidaten, SZ_CFG["fdr_alpha"])
        print(f"  {len(kandidaten)} geanalyseerd, {len(top_alles)} FDR-significant")

        if not top_alles:
            print(f"  → Overgeslagen: {ex_name} (niets overleeft de FDR-correctie)")
            continue

        top_alles.sort(key=lambda s: s.score, reverse=True)
        top = top_alles[:SZ_CFG["top_n"]]

        # Automatische historische check (hersteld uit v1.3): exact de
        # tickers/buckets die de FDR-selectie hierboven net (dynamisch, dit
        # run) heeft gevonden -- nooit een vooraf vastgelegde lijst.
        vandaag = dt.datetime.now(dt.timezone.utc)
        huidige_bucketwaarde = {
            "weekdag":  vandaag.weekday(),
            "maand":    vandaag.month,
            "kwartaal": (vandaag.month - 1) // 3 + 1,
        }
        for s in top:
            ticker_data = df_ex[df_ex["Ticker"] == s.ticker].sort_values("Date").set_index("Date")
            for bucket_key in s.significante_buckets:
                resultaat = historische_check(bucket_key, huidige_bucketwaarde[bucket_key], ticker_data)
                if resultaat:
                    s.checks[bucket_key] = resultaat
            if s.checks:
                check_str = " | ".join(
                    f"{k}: {v['jaren_winst']}/{v['jaren_getest']}j" for k, v in s.checks.items()
                )
                print(f"    check {s.ticker}: {check_str}")

        for s in top:
            log_selectie(
                ticker=s.ticker, datum=today_str(), strategie="bot_01repititief", beurs=ex_name, koers=s.price,
                parameters={
                    "score": s.score, "grafiek": yahoo_link(s.ticker),
                    "jaren_data": s.jaren_data, "significante_buckets": sorted(s.significante_buckets),
                    "historische_check": s.checks,
                    "weekdag_gemiddelde": s.weekdag["gemiddelde"] if s.weekdag else None,
                    "weekdag_winrate":    s.weekdag["win_rate"] if s.weekdag else None,
                    "weekdag_p":          s.weekdag["p_waarde"] if s.weekdag else None,
                    "maand_gemiddelde":   s.maand["gemiddelde"] if s.maand else None,
                    "maand_winrate":      s.maand["win_rate"] if s.maand else None,
                    "maand_p":            s.maand["p_waarde"] if s.maand else None,
                    "kwartaal_gemiddelde": s.kwartaal["gemiddelde"] if s.kwartaal else None,
                    "kwartaal_winrate":    s.kwartaal["win_rate"] if s.kwartaal else None,
                    "kwartaal_p":          s.kwartaal["p_waarde"] if s.kwartaal else None,
                },
            )

        bericht = format_bericht(ex_name, top, kandidaten)
        if bericht:
            send_telegram(bericht, parse_mode="Markdown")
            email_delen.append(bericht)
            print(f"  → Telegram verstuurd")
            time.sleep(TELEGRAM_THROTTLE_SEC)

    if email_delen:
        send_email(f"Seizoenseffecten rapport {today_str()}", "\n\n" + ("=" * 40 + "\n\n").join(email_delen))

    print(f"\n{'='*60}\nKlaar.")

# ============================================================
# BACKTEST ENGINE (hersteld uit v1.2/v1.3)
# ============================================================
# Per buckettype en testjaar worden de getrainde p-waarden over ALLE tickers
# gepoold en BH-gecorrigeerd (zelfde principe als in de live-engine); enkel
# wie na correctie significant én positief is, wordt verhandeld.

def run_backtest():
    print(f"{'='*60}")
    print(f"SEIZOENSEFFECTEN BACKTEST (weekdag+maand+kwartaal, BH-gecorrigeerd)  {BACKTEST_START} -> {BACKTEST_END}")
    print(f"{'='*60}")

    all_tickers: List[str] = []
    for f_name in bouw_bestandslijst():
        all_tickers.extend(load_tickers_from_file(f_name))
    all_tickers = sorted(set(all_tickers))

    if not all_tickers:
        print("[ERROR] Geen tickers gevonden.")
        return

    print(f"Tickers: {len(all_tickers)} | Data downloaden ({SZ_CFG['lookback_years']}y)...")
    df = download_history(all_tickers, period=f"{SZ_CFG['lookback_years']}y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    # Per ticker de rendementenreeks + geïndexeerde koersen klaarzetten
    reeksen: Dict[str, Tuple[pd.Series, pd.DataFrame]] = {}
    for ticker, group in df.groupby("Ticker", sort=False):
        g = group.sort_values("Date").set_index("Date")
        rendement = g["Close"].pct_change().dropna()
        if not rendement.empty:
            reeksen[ticker] = (rendement, g)

    trades: List[Dict] = []
    testjaren = list(range(pd.Timestamp(BACKTEST_START).year, pd.Timestamp(BACKTEST_END).year + 1))

    for bucket_key in BUCKET_TYPES:
        mogelijke_waarden = {
            "maand":    range(1, 13),
            "kwartaal": range(1, 5),
            "weekdag":  range(0, 5),
        }[bucket_key]

        for test_jaar in testjaren:
            # 1) per ticker de ruwe getrainde bucket-stats ophalen (geen look-ahead)
            getraind: Dict[str, Dict[int, dict]] = {}
            for ticker, (rendement, _g) in reeksen.items():
                stats_per_waarde = _train_bucket_stats_per_ticker(rendement, test_jaar, bucket_key)
                if stats_per_waarde:
                    getraind[ticker] = stats_per_waarde
            if not getraind:
                continue

            # 2) BH-correctie per bucketwaarde, gepoold over alle tickers dit testjaar
            significant_dit_jaar: Dict[str, Set[int]] = {}
            for waarde in mogelijke_waarden:
                tickers_met_waarde = [t for t, sm in getraind.items() if waarde in sm]
                if not tickers_met_waarde:
                    continue
                p_waarden = [getraind[t][waarde]["p_waarde"] for t in tickers_met_waarde]
                mask = bh_correctie(p_waarden, SZ_CFG["fdr_alpha"])
                for t, sig in zip(tickers_met_waarde, mask):
                    if sig and getraind[t][waarde]["gemiddelde"] > 0:
                        significant_dit_jaar.setdefault(t, set()).add(waarde)

            # 3) enkel de significante ticker/waarde-combinaties effectief verhandelen
            for ticker, waarden in significant_dit_jaar.items():
                _rendement, g = reeksen[ticker]
                for waarde in waarden:
                    for entry_price, exit_price in _trades_voor_bucket(bucket_key, waarde, g, test_jaar):
                        _boek_trade(trades, ticker, test_jaar, bucket_key, waarde,
                                    entry_price, exit_price, getraind[ticker][waarde])

    if not trades:
        print("Geen trades gegenereerd (na BH-correctie bleef geen enkele combinatie over).")
        return

    tdf = pd.DataFrame(trades)
    tdf.to_csv("seizoen_backtest_trades.csv", index=False)

    def _print_samenvatting(naam: str, sub: pd.DataFrame) -> None:
        if sub.empty:
            print(f"{naam:>10}: geen trades")
            return
        n    = len(sub)
        nwin = (sub["net"] > 0).sum()
        pf   = abs(sub.loc[sub["net"] > 0, "net"].sum()) / max(
               abs(sub.loc[sub["net"] <= 0, "net"].sum()), 1e-9)
        print(f"{naam:>10}: {n:>5} trades | winrate {nwin/n*100:5.1f}% | "
              f"PF {pf:5.2f} | netto EUR{sub['net'].sum():>12,.2f} | belasting EUR{sub['tax'].sum():>10,.2f}")

    print(f"\n{'='*60}")
    for bucket_key in BUCKET_TYPES:
        _print_samenvatting(bucket_key, tdf[tdf["bucket_type"] == bucket_key])
    print("-" * 60)
    _print_samenvatting("TOTAAL", tdf)
    print(f"{'='*60}")
    print("Opgeslagen: seizoen_backtest_trades.csv (kolom bucket_type onderscheidt weekdag/maand/kwartaal)")
    print("Let op: 'netto' is de som over alle posities t.o.v. eenzelfde vast risicobudget per trade,")
    print("geen samengesteld kapitaal — vergelijk de PF/winrate per buckettype, niet de absolute EUR-bedragen 1-op-1.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
