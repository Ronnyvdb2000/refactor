#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01beurssign.py — BEURSSIGNALEN-METHODIEK v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Combineert VWAP-trend, Overhead Supply en Insider-transacties.
"""

import argparse
import sys
import math
import time
import warnings
from dataclasses import dataclass
from typing import List, Dict, Optional

import numpy as np
import pandas as pd
import yfinance as yf

from common import (
    today_str, safe_float, yahoo_link,
    load_tickers_from_file, bouw_bestandslijst, label_voor,
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

US_BEURS_NUMMERS = {"048", "055", "056", "057"}

MODUS_CFG = {
    "live": {
        "bestand_suffix":  "x",
        "vwap_max_boven":  0.08,
        "overhead_pctl":   75,
        "vol_ratio_min":   1.5,
        "min_score":       3,
        "top_n":           5,
        "strategie":       "bot_01beurssig",
        "label":           "BEURSSIGNALEN-METHODIEK",
        "throttle_sec":    0.15,
    },
    "full": {
        "bestand_suffix":  "a",
        "vwap_max_boven":  0.06,
        "overhead_pctl":   70,
        "vol_ratio_min":   1.8,
        "min_score":       3,
        "top_n":           3,
        "strategie":       "bot_01beurssig_full",
        "label":           "BEURSSIGNALEN-METHODIEK — WEEKLY FULL SCAN",
        "throttle_sec":    0.12,
    },
}

def is_us_beurs(f_name: str) -> bool:
    getal = f_name.replace("tickers_", "")[:3]
    return getal in US_BEURS_NUMMERS

# ============================================================
# ANALYSE HELPERS
# ============================================================

@dataclass
class BeursSigSignaal:
    ticker:              str
    price:               float
    score:               int
    max_score:           int
    is_us:               bool
    vwap20_pct:          float
    vwap50_pct:          float
    vol_ratio_20d:       float
    overhead_cleared:    bool
    insider_net_shares:  float
    vwap_label:          str
    overhead_label:      str
    volume_label:        str
    insider_label:       str
    signal_date:         str = ""
    data_note:           str = ""

def _vwap(closes: np.ndarray, volumes: np.ndarray, n: int) -> float:
    c, v = closes[-n:], volumes[-n:]
    if v.sum() <= 0:
        return float("nan")
    return float((c * v).sum() / v.sum())

def _overhead_cleared(closes: np.ndarray, volumes: np.ndarray, price: float, pctl: int) -> bool:
    if len(closes) < 20 or volumes.sum() <= 0:
        return False
    hist_vol, edges = np.histogram(closes, bins=20, weights=volumes)
    nonzero = hist_vol[hist_vol > 0]
    if len(nonzero) == 0:
        return False
    drempel = np.percentile(nonzero, pctl)
    significante_bins = np.where(hist_vol >= drempel)[0]
    for i in significante_bins:
        bin_boven = edges[i + 1]
        if bin_boven > price * 1.01:
            return False
    return True

class DataUnavailable(ValueError):
    """Expected provider-data limitation, reported separately from code errors."""


def prepare_history(hist: pd.DataFrame) -> pd.DataFrame:
    if hist is None or hist.empty or not {"Close", "Volume"}.issubset(hist.columns):
        raise DataUnavailable("geen Close/Volume-historie")
    if not isinstance(hist.index, pd.DatetimeIndex):
        raise DataUnavailable("geen koersdatums")
    hist = hist.copy()
    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)
    hist.index = hist.index.normalize()
    today = pd.Timestamp(today_str())
    hist = hist.loc[hist.index.notna() & (hist.index < today)]
    hist = hist.sort_index().loc[lambda x: ~x.index.duplicated(keep="last")]
    for col in ("Close", "Volume"):
        hist[col] = pd.to_numeric(hist[col], errors="coerce")
    valid = (np.isfinite(hist["Close"]) & (hist["Close"] > 0)
             & np.isfinite(hist["Volume"]) & (hist["Volume"] >= 0))
    # Do not silently replace an invalid latest observation by an older signal.
    if len(hist) and not valid.iloc[-1]:
        row = hist.iloc[-1]
        raise DataUnavailable(f"ongeldige laatste koers/volume op {hist.index[-1].date()}: "
                              f"Close={row['Close']}, Volume={row['Volume']}")
    hist = hist.loc[valid]
    if len(hist) < 55:
        raise DataUnavailable("minder dan 55 geldige afgeronde sessies")
    if (today - hist.index[-1]).days > 7:
        raise DataUnavailable("laatste koers ouder dan zeven dagen")
    if hist["Volume"].tail(20).sum() <= 0:
        raise DataUnavailable("geen recent handelsvolume")
    return hist


def _insider_net_shares(ticker: str, dagen: int = 90) -> float:
    try:
        df = yf.Ticker(ticker).insider_transactions
        if df is None or df.empty or not {"Start Date", "Shares"}.issubset(df.columns):
            return float("nan")
        text_col = "Text" if "Text" in df.columns else "Transaction"
        if text_col not in df.columns:
            return float("nan")
        dates = pd.to_datetime(df["Start Date"], errors="coerce", utc=True)
        now = pd.Timestamp.now(tz="UTC")
        recent = df.loc[(dates >= now - pd.Timedelta(days=dagen)) & (dates <= now)]
        text = recent[text_col].fillna("").astype(str).str.lower()
        buy = text.str.contains(r"\b(?:purchase|buy)\b", regex=True)
        sell = text.str.contains(r"\b(?:sale|sell)\b", regex=True)
        relevant = buy | sell
        if not relevant.any() or (buy & sell).any():
            return float("nan")
        shares = pd.to_numeric(recent["Shares"], errors="coerce").abs()
        if not np.isfinite(shares[relevant]).all():
            return float("nan")
        return float(shares[buy].sum() - shares[sell].sum())
    except Exception as exc:
        print(f"[DATA] {ticker}: insiderdata niet beschikbaar ({type(exc).__name__})")
        return float("nan")


def analyse_ticker(ticker: str, cfg: dict, us_beurs: bool) -> BeursSigSignaal:
    tk = yf.Ticker(ticker)
    raw = tk.history(period="6mo", interval="1d", auto_adjust=True)
    data_note = ""
    try:
        hist = prepare_history(raw)
    except DataUnavailable as exc:
        if not str(exc).startswith("ongeldige laatste koers/volume"):
            raise
        print(f"[DATA] {ticker}: {exc}; heraanvraag met Yahoo-repair", flush=True)
        repaired = tk.history(period="6mo", interval="1d", auto_adjust=True, repair=True)
        hist = prepare_history(repaired)
        # Repair must restore the same last session, never bypass a bad session
        # by returning only older data. Original timezone represents local dates.
        original_dates = raw.index
        if original_dates.tz is not None:
            original_dates = original_dates.tz_localize(None)
        original_dates = original_dates.normalize()
        eligible = original_dates[original_dates.notna() & (original_dates < pd.Timestamp(today_str()))]
        if hist.index[-1] != eligible.max():
            raise DataUnavailable("Yahoo-repair herstelde de laatste koersdag niet")
        data_note = f"Yahoo-repair gebruikt voor koersdag {hist.index[-1].date()}; providerherstel, niet onafhankelijk geverifieerd"
        print(f"[DATA] {ticker}: {data_note}", flush=True)
    closes = hist["Close"].to_numpy(dtype=float)
    volumes = hist["Volume"].to_numpy(dtype=float)
    price = float(closes[-1])

    score, max_score = 0, 3

    vwap20 = _vwap(closes, volumes, 20)
    vwap50 = _vwap(closes, volumes, 50)
    vwap20_pct = (price / vwap20 - 1) * 100 if not math.isnan(vwap20) and vwap20 > 0 else float("nan")
    vwap50_pct = (price / vwap50 - 1) * 100 if not math.isnan(vwap50) and vwap50 > 0 else float("nan")
    vwap_ok = (not math.isnan(vwap20_pct) and not math.isnan(vwap50_pct) and vwap20_pct > 0 and vwap50_pct > 0 and vwap20_pct <= cfg["vwap_max_boven"] * 100)
    if vwap_ok:
        score += 1
        vwap_label = f"✓ +{vwap20_pct:.1f}% boven VWAP20, +{vwap50_pct:.1f}% boven VWAP50"
    else:
        vwap_label = f"✗ VWAP20:{vwap20_pct:+.1f}% | VWAP50:{vwap50_pct:+.1f}%" if not math.isnan(vwap20_pct) else "✗ onbekend"

    overhead_cleared = _overhead_cleared(closes[-60:], volumes[-60:], price, cfg["overhead_pctl"])
    if overhead_cleared:
        score += 1
        overhead_label = "✓ opgeruimd"
    else:
        overhead_label = "✗ nog weerstandszone boven koers"

    basis_vol = volumes[-21:-1]
    vol_ratio_20d = float(volumes[-1] / basis_vol.mean()) if len(basis_vol) == 20 and basis_vol.mean() > 0 else float("nan")
    if not math.isfinite(vol_ratio_20d):
        raise DataUnavailable("geen bruikbaar 20-sessievolumegemiddelde")
    vol_ok = not math.isnan(vol_ratio_20d) and vol_ratio_20d >= cfg["vol_ratio_min"]
    if vol_ok:
        score += 1
        volume_label = f"✓ {vol_ratio_20d:.2f}x 20d-gemiddelde"
    else:
        volume_label = f"✗ {vol_ratio_20d:.2f}x" if not math.isnan(vol_ratio_20d) else "✗ onbekend"

    insider_net_shares = float("nan")
    insider_label = "— n.v.t. (geen betrouwbare insiderdata buiten VS)"
    if us_beurs:
        max_score = 4
        insider_net_shares = _insider_net_shares(ticker)
        if not math.isnan(insider_net_shares) and insider_net_shares > 0:
            score += 1
            insider_label = f"✓ netto {insider_net_shares:,.0f} aandelen gekocht"
        elif not math.isnan(insider_net_shares):
            insider_label = f"✗ netto {insider_net_shares:,.0f} aandelen"
        else:
            insider_label = "✗ insiderdata onbekend (geen scorepunt)"

    return BeursSigSignaal(
        ticker=ticker, price=price, score=score, max_score=max_score,
        is_us=us_beurs,
        vwap20_pct=vwap20_pct,
        vwap50_pct=vwap50_pct,
        vol_ratio_20d=vol_ratio_20d,
        overhead_cleared=overhead_cleared,
        insider_net_shares=insider_net_shares,
        vwap_label=vwap_label, overhead_label=overhead_label,
        volume_label=volume_label, insider_label=insider_label,
        signal_date=hist.index[-1].date().isoformat(), data_note=data_note,
    )
# ============================================================
# OUTPUT
# ============================================================

def _score_bar(score: int, max_score: int) -> str:
    return "█" * score + "░" * (max_score - score) + f" {score}/{max_score}"

def format_bericht(exchange_name: str, signalen: List[BeursSigSignaal], alle: List[BeursSigSignaal], cfg: dict) -> Optional[str]:
    if not alle:
        return None

    nu    = today_str()
    top_n = cfg["top_n"]
    top_tonen = sorted(alle, key=lambda s: (-s.score / s.max_score, -s.vol_ratio_20d, s.ticker))[:top_n]

    def sig_regel(s: BeursSigSignaal, detail: bool = False) -> str:
        r = f"• `{s.ticker}` {_score_bar(s.score, s.max_score)} | {s.price:.4f} bronkoerseenheden | koersdag {s.signal_date} | Vol:{s.vol_ratio_20d:.2f}x | {yahoo_link(s.ticker)}"
        if detail:
            r += f"\n  {s.vwap_label}\n  {s.overhead_label}\n  {s.volume_label}\n  {s.insider_label}"
        return r

    delen = [
        f"📊 *{cfg['label']} — {exchange_name}*",
        f"_{nu} | {len(alle)} geanalyseerd | {sum(s.score >= cfg['min_score'] for s in alle)} kandidaten, {len(signalen)} topselecties (score>={cfg['min_score']})_",
        "─────────────────────────────",
        f"🏆 *TOP {min(top_n, len(alle))} HOOGSTE SCORE (kan onder selectiedrempel liggen):*",
        "\n\n".join(sig_regel(s, detail=True) for s in top_tonen),
    ]

    overige = [s for s in signalen if s not in top_tonen]
    if overige:
        delen += ["─────────────────────────────", "*Overige kandidaten:*"]
        for s in overige:
            delen.append(sig_regel(s))

    delen.append(f"⚙️ _Eigen heuristiek, geen orders of gevalideerde strategie. VWAP is een slotkoers-volumeproxy op dagdata; overhead gebruikt koersbins. VWAP: koers boven VWAP20+VWAP50, max +{cfg['vwap_max_boven']*100:.0f}% erboven | Overhead Supply opgeruimd | Volume>={cfg['vol_ratio_min']:.1f}x 20d-gem. | Insiderscreening enkel op VS-beurzen_")
    return "\n\n".join(delen)

# ============================================================
# ENGINE
# ============================================================

def run_engine(modus: str):
    cfg = MODUS_CFG[modus]
    reports, issues, errors = [], [], []
    cache = {}
    requested = set()
    stored = selected = analysed = 0
    print(f"{cfg['label']} {today_str()} — Telegram uit", flush=True)
    for filename in bouw_bestandslijst(cfg["bestand_suffix"]):
        try:
            tickers = sorted(set(load_tickers_from_file(filename)))
        except Exception as exc:
            errors.append(f"{filename}: laden mislukt ({exc})")
            continue
        if not tickers:
            issues.append(f"{filename}: ontbrekende of lege tickerlijst")
            continue
        requested.update(tickers)
        us = is_us_beurs(filename)
        all_signals = []
        for ticker in tickers:
            key = (ticker, us)
            if key not in cache:
                try:
                    cache[key] = analyse_ticker(ticker, cfg, us)
                    analysed += 1
                    if cache[key].data_note:
                        issues.append(f"{ticker}: {cache[key].data_note}")
                    if us and not math.isfinite(cache[key].insider_net_shares):
                        issues.append(f"{ticker}: insiderdata onbekend; geen insiderpunt")
                except DataUnavailable as exc:
                    cache[key] = None
                    issues.append(f"{ticker}: {exc}")
                except Exception as exc:
                    cache[key] = None
                    errors.append(f"{ticker}: analyse mislukt ({type(exc).__name__}: {exc})")
                time.sleep(cfg["throttle_sec"])
            if cache[key] is not None:
                all_signals.append(cache[key])
        candidates = sorted((x for x in all_signals if x.score >= cfg["min_score"]),
                            key=lambda x: (-x.score / x.max_score, -x.vol_ratio_20d, x.ticker))
        signals = candidates[:cfg["top_n"]]
        selected += len(signals)
        exchange = label_voor(filename)
        print(f"{exchange}: {len(all_signals)}/{len(tickers)} geanalyseerd, "
              f"{len(candidates)} kandidaten, {len(signals)} topselecties", flush=True)
        for rank, sig in enumerate(signals, 1):
            try:
                ok = log_selectie(ticker=sig.ticker, datum=sig.signal_date,
                    strategie=cfg["strategie"], beurs=exchange, koers=sig.price,
                    parameters={"score": sig.score, "max_score": sig.max_score, "rank": rank,
                        "signal_date": sig.signal_date, "data_note": sig.data_note, "vwap20_pct": sig.vwap20_pct,
                        "vwap50_pct": sig.vwap50_pct, "vol_ratio_20d": sig.vol_ratio_20d,
                        "breakout": sig.overhead_cleared,
                        "insider_net_shares": sig.insider_net_shares if math.isfinite(sig.insider_net_shares) else None,
                        "grafiek": yahoo_link(sig.ticker)})
                if not ok:
                    raise RuntimeError("geen opslagbevestiging")
                stored += 1
            except Exception as exc:
                errors.append(f"{sig.ticker}: database mislukt ({exc})")
        reports.append(f"{exchange}: {len(all_signals)}/{len(tickers)} geanalyseerd, "
                       f"{len(candidates)} kandidaten, {len(signals)} topselecties.\n" +
                       (format_bericht(exchange, signals, all_signals, cfg) or "Geen bruikbare analyses."))
    if not analysed:
        errors.append("Geen bruikbare analyses in het universum")
    summary = (f"{len(requested)} unieke tickers gevraagd; {analysed} ticker/beurstype-analyses. "
               f"Database: {stored}/{selected} selecties opgeslagen. Telegram uit.")
    body = "\n\n".join([summary] + reports + ["Databeperkingen:"] + issues + ["Fouten:"] + errors)
    print(body, flush=True)
    subject = ("[ONVOLLEDIG] " if issues or errors else "") + cfg["label"] + " " + today_str()
    if not send_email(subject, body):
        errors.append("E-mail niet verzonden")
    if errors:
        raise RuntimeError("; ".join(errors))
    print("Klaar — e-mail verzonden.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", default="live", choices=("live", "full"))
    run_engine(parser.parse_args().mode)
