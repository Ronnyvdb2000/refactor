#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01beurssign.py — BEURSSIGNALEN-METHODIEK v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Combineert VWAP-trend, Overhead Supply en Insider-transacties.
"""

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

def _insider_net_shares(ticker: str, dagen: int = 90) -> float:
    try:
        tk = yf.Ticker(ticker)
        df = tk.insider_transactions
        if df is None or df.empty or "Start Date" not in df.columns:
            return float("nan")
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=dagen)
        df = df.copy()
        df["Start Date"] = pd.to_datetime(df["Start Date"], errors="coerce")
        recent = df[df["Start Date"] >= cutoff]
        if recent.empty:
            return float("nan")
        tekst = recent["Text"].astype(str).str.lower() if "Text" in recent.columns else recent.get("Transaction", "").astype(str).str.lower()
        shares = recent["Shares"].apply(safe_float) if "Shares" in recent.columns else recent.get("Value", 0).apply(safe_float)
        koop = shares[tekst.str.contains("purchase|buy", na=False)].sum()
        verkoop = shares[tekst.str.contains("sale|sell", na=False)].sum()
        return float(koop - verkoop)
    except Exception:
        return float("nan")

def analyse_ticker(ticker: str, cfg: dict, us_beurs: bool) -> Optional[BeursSigSignaal]:
    try:
        hist = yf.Ticker(ticker).history(period="6mo", interval="1d", auto_adjust=True)
        if hist is None or len(hist) < 55 or "Close" not in hist.columns:
            return None

        closes  = hist["Close"].to_numpy()
        volumes = hist["Volume"].to_numpy()
        price   = safe_float(closes[-1])
        if math.isnan(price) or price <= 0:
            return None

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
                insider_label = "✗ geen insidertransacties gevonden"

        return BeursSigSignaal(
            ticker=ticker, price=round(price, 2), score=score, max_score=max_score,
            is_us=us_beurs,
            vwap20_pct=round(vwap20_pct, 1) if not math.isnan(vwap20_pct) else 0.0,
            vwap50_pct=round(vwap50_pct, 1) if not math.isnan(vwap50_pct) else 0.0,
            vol_ratio_20d=round(vol_ratio_20d, 2) if not math.isnan(vol_ratio_20d) else 0.0,
            overhead_cleared=overhead_cleared,
            insider_net_shares=insider_net_shares,
            vwap_label=vwap_label, overhead_label=overhead_label,
            volume_label=volume_label, insider_label=insider_label,
        )
    except Exception as e:
        print(f"[WARN] {ticker}: fout — {e}")
        return None

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
    top_tonen = sorted(alle, key=lambda s: (s.score / s.max_score, s.vol_ratio_20d), reverse=True)[:top_n]

    def sig_regel(s: BeursSigSignaal, detail: bool = False) -> str:
        r = f"• `{s.ticker}` {_score_bar(s.score, s.max_score)} | {s.price:.2f} | Vol:{s.vol_ratio_20d:.2f}x | {yahoo_link(s.ticker)}"
        if detail:
            r += f"\n  {s.vwap_label}\n  {s.overhead_label}\n  {s.volume_label}\n  {s.insider_label}"
        return r

    delen = [
        f"📊 *{cfg['label']} — {exchange_name}*",
        f"_{nu} | {len(alle)} geanalyseerd | {len(signalen)} kandidaten (score>={cfg['min_score']})_",
        "─────────────────────────────",
        f"🏆 *TOP {top_n} HOOGSTE SCORE:*",
        "\n\n".join(sig_regel(s, detail=True) for s in top_tonen),
    ]

    overige = [s for s in signalen if s not in top_tonen]
    if overige:
        delen += ["─────────────────────────────", "*Overige kandidaten:*"]
        for s in overige:
            delen.append(sig_regel(s))

    delen.append(f"⚙️ _VWAP: koers boven VWAP20+VWAP50, max +{cfg['vwap_max_boven']*100:.0f}% erboven | Overhead Supply opgeruimd | Volume>={cfg['vol_ratio_min']:.1f}x 20d-gem. | Insiderscreening enkel op VS-beurzen_")
    return "\n\n".join(delen)

# ============================================================
# ENGINE
# ============================================================

def run_engine(modus: str):
    cfg = MODUS_CFG[modus]
    print(f"{'='*60}\n{cfg['label']}  {today_str()}  [suffix: {cfg['bestand_suffix']}]\n{'='*60}")

    bestanden = bouw_bestandslijst(cfg["bestand_suffix"])
    email_delen: List[str] = []

    for f_name in bestanden:
        tlist = load_tickers_from_file(f_name)
        if not tlist:
            continue
        ex_name  = label_voor(f_name)
        us_beurs = is_us_beurs(f_name)
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers, VS-beurs: {us_beurs})...")

        alle: List[BeursSigSignaal] = []
        for ticker in tlist:
            sig = analyse_ticker(ticker, cfg, us_beurs)
            if sig is not None:
                alle.append(sig)
            time.sleep(cfg["throttle_sec"])

        kandidaten = [s for s in alle if s.score >= cfg["min_score"]]
        kandidaten.sort(key=lambda s: (s.score / s.max_score, s.vol_ratio_20d), reverse=True)
        signalen = kandidaten[:cfg["top_n"]]

        print(f"  → top {len(signalen)} van {len(kandidaten)} kandidaten uit {len(alle)} geanalyseerd")

        for rank, s in enumerate(signalen, start=1):
            log_selectie(
                ticker=s.ticker, datum=today_str(), strategie=cfg["strategie"], beurs=ex_name, koers=s.price,
                parameters={
                    "score": s.score, "rank": rank, "vwap20_pct": s.vwap20_pct,
                    "vwap50_pct": s.vwap50_pct, "vol_ratio_20d": s.vol_ratio_20d,
                    "breakout": s.overhead_cleared,
                    "insider_net_shares": None if math.isnan(s.insider_net_shares) else s.insider_net_shares,
                    "grafiek": yahoo_link(s.ticker),
                },
            )

        bericht = format_bericht(ex_name, signalen, alle, cfg)
        if bericht:
            send_telegram(bericht, parse_mode="Markdown")
            email_delen.append(bericht)
            print("  → Telegram verstuurd")
        else:
            print(f"  → Overgeslagen: {ex_name}")

    if email_delen:
        send_email(f"{cfg['label']} {today_str()}", "\n\n" + ("=" * 40 + "\n\n").join(email_delen))

    print(f"\n{'='*60}\nKlaar.")

def run_backtest():
    print("Backtest wordt niet ondersteund voor bot_01beurssig.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    elif mode == "full":
        run_engine("full")
    else:
        run_engine("live")
