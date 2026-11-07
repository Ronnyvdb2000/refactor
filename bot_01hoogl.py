#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01hoogl.py — GARP ONDERWAARDERING v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Bevat rate-limit-backoff en ondersteunt live/full modus.
"""

import sys
import math
import time
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

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

# ============================================================
# CONFIG
# ============================================================

RATE_LIMIT_CONSECUTIVE_DREMPEL = 5
RATE_LIMIT_PAUZE_SEC           = 300
RATE_LIMIT_MAX_PAUZES          = 6
RATE_LIMIT_THROTTLE_VERHOGING  = 0.5

MODUS_CFG = {
    "live": {
        "bestand_suffix":       "x",
        "roe_min":              15.0,
        "terugverdien_max_jaar": 15.0,
        "fwd_pe_max":           15.0,
        "marktkap_max":         5_000_000_000.0,
        "analisten_max":        5,
        "min_score":            4,
        "top_n":                5,
        "strategie":            "bot_01hoogl",
        "label":                "GARP ONDERWAARDERING",
        "throttle_sec":         0.15,
    },
    "full": {
        "bestand_suffix":       "a",
        "roe_min":              18.0,
        "terugverdien_max_jaar": 10.0,
        "fwd_pe_max":           12.0,
        "marktkap_max":         5_000_000_000.0,
        "analisten_max":        5,
        "min_score":            6,
        "top_n":                3,
        "strategie":            "bot_01hoogl_full",
        "label":                "GARP ONDERWAARDERING — WEEKLY FULL SCAN",
        "throttle_sec":         0.12,
    },
}

# ============================================================
# HULPFUNCTIES
# ============================================================

def is_rate_limit_fout(e: Exception) -> bool:
    tekst = str(e)
    return "Too Many Requests" in tekst or "Rate limited" in tekst

def _score_bar(score: int) -> str:
    return "█" * score + "░" * (6 - score) + f" {score}/6"

# ============================================================
# FUNDAMENTELE DATA
# ============================================================

@dataclass
class HooglSignaal:
    ticker:                   str
    price:                    float
    score:                    int
    roe_pct:                  float
    terugverdienperiode:      float
    forward_pe:               float
    verwachte_winstgroei_pct: float
    div_yield:                float
    market_cap:               float
    analisten_count:          float
    roe_label:                str
    terugverdien_label:       str
    fwd_pe_label:             str
    groei_label:              str
    marktkap_label:           str
    analisten_label:          str

def analyse_ticker(ticker: str, cfg: dict) -> Tuple[Optional[HooglSignaal], bool]:
    try:
        tk   = yf.Ticker(ticker)
        info = tk.info or {}

        price = safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
        if math.isnan(price) or price <= 0:
            return None, False

        boekwaarde   = safe_float(info.get("bookValue"))
        winst_nu     = safe_float(info.get("trailingEps"))
        winst_verw   = safe_float(info.get("forwardEps"))
        roe          = safe_float(info.get("returnOnEquity"))
        fwd_pe       = safe_float(info.get("forwardPE"))
        div_yield    = safe_float(info.get("dividendYield"), 0.0)
        market_cap   = safe_float(info.get("marketCap"))

        score = 0

        roe_pct = roe * 100 if not math.isnan(roe) else float("nan")
        if not math.isnan(roe_pct) and roe_pct >= cfg["roe_min"]:
            score += 1
            roe_label = f"✓ {roe_pct:.1f}% (>= {cfg['roe_min']:.0f}%)"
        else:
            roe_label = f"✗ {roe_pct:.1f}%" if not math.isnan(roe_pct) else "✗ onbekend"

        if not math.isnan(boekwaarde) and not math.isnan(winst_nu) and winst_nu > 0:
            terug_te_verdienen = price - boekwaarde
            terugverdienperiode = terug_te_verdienen / winst_nu
            if terugverdienperiode <= cfg["terugverdien_max_jaar"]:
                score += 1
                terugverdien_label = f"✓ {terugverdienperiode:.1f} jaar"
            else:
                terugverdien_label = f"✗ {terugverdienperiode:.1f} jaar"
        else:
            terugverdienperiode = float("nan")
            terugverdien_label = "✗ onbekend (geen winst/boekwaarde)"

        if not math.isnan(fwd_pe) and 0 < fwd_pe <= cfg["fwd_pe_max"]:
            score += 1
            fwd_pe_label = f"✓ {fwd_pe:.1f}x (<= {cfg['fwd_pe_max']:.0f}x)"
        else:
            fwd_pe_label = f"✗ {fwd_pe:.1f}x" if not math.isnan(fwd_pe) else "✗ onbekend"

        if not math.isnan(winst_nu) and not math.isnan(winst_verw) and winst_nu > 0:
            verwachte_groei_pct = (winst_verw - winst_nu) / winst_nu * 100
            if verwachte_groei_pct > 0:
                score += 1
                groei_label = f"✓ {verwachte_groei_pct:+.1f}%"
            else:
                groei_label = f"✗ {verwachte_groei_pct:+.1f}%"
        else:
            verwachte_groei_pct = float("nan")
            groei_label = "✗ onbekend"

        if not math.isnan(market_cap) and market_cap > 0 and market_cap <= cfg["marktkap_max"]:
            score += 1
            marktkap_label = f"✓ {market_cap/1e9:.2f}B (<= {cfg['marktkap_max']/1e9:.0f}B)"
        else:
            marktkap_label = f"✗ {market_cap/1e9:.2f}B" if not math.isnan(market_cap) else "✗ onbekend"

        analisten_raw = safe_float(info.get("numberOfAnalystOpinions"))
        if not math.isnan(analisten_raw):
            analisten_count = analisten_raw
            if analisten_count <= cfg["analisten_max"]:
                score += 1
                analisten_label = f"✓ {analisten_count:.0f} analisten (<= {cfg['analisten_max']})"
            else:
                analisten_label = f"✗ {analisten_count:.0f} analisten"
        else:
            analisten_count = float("nan")
            analisten_label = "✗ coverage onbekend"

        signaal = HooglSignaal(
            ticker=ticker, price=round(price, 2), score=score,
            roe_pct=round(roe_pct, 1) if not math.isnan(roe_pct) else 0.0,
            terugverdienperiode=round(terugverdienperiode, 1) if not math.isnan(terugverdienperiode) else 0.0,
            forward_pe=round(fwd_pe, 1) if not math.isnan(fwd_pe) else 0.0,
            verwachte_winstgroei_pct=round(verwachte_groei_pct, 1) if not math.isnan(verwachte_groei_pct) else 0.0,
            div_yield=round(div_yield, 2) if not math.isnan(div_yield) else 0.0,
            market_cap=round(market_cap, 0) if not math.isnan(market_cap) else 0.0,
            analisten_count=round(analisten_count, 0) if not math.isnan(analisten_count) else -1.0,
            roe_label=roe_label, terugverdien_label=terugverdien_label,
            fwd_pe_label=fwd_pe_label, groei_label=groei_label,
            marktkap_label=marktkap_label, analisten_label=analisten_label,
        )
        return signaal, False
    except Exception as e:
        if is_rate_limit_fout(e):
            return None, True
        print(f"[WARN] {ticker}: fout — {e}")
        return None, False

# ============================================================
# OUTPUT
# ============================================================

def format_bericht(exchange_name: str, signalen: List[HooglSignaal], alle: List[HooglSignaal], cfg: dict) -> Optional[str]:
    if not alle:
        return None

    nu        = today_str()
    top_n     = cfg["top_n"]
    top_tonen = sorted(alle, key=lambda s: (s.score, -s.terugverdienperiode), reverse=True)[:top_n]
    max_sc    = max((s.score for s in signalen), default=0) if signalen else 0
    lbl       = {6: "⭐ PERFECTE SCORE (6/6)", 5: "🟡 STERK (5/6)", 4: "🟠 GOED (4/6)"}.get(max_sc, "📊")

    def sig_regel(s: HooglSignaal, detail: bool = False) -> str:
        r = f"• `{s.ticker}` {_score_bar(s.score)} | EUR{s.price:.2f} | RoE:{s.roe_pct:.1f}% | Terugverdien:{s.terugverdienperiode:.1f}j | {yahoo_link(s.ticker)}"
        if detail:
            r += f"\n  {s.roe_label} | {s.terugverdien_label}\n  Fwd P/E: {s.fwd_pe_label} | Verw. winstgroei: {s.groei_label}\n  Marktkap: {s.marktkap_label} | Coverage: {s.analisten_label}"
        return r

    delen = [
        f"📈 *{cfg['label']} — {exchange_name}*",
        f"_{nu} | {len(alle)} geanalyseerd | {len(signalen)} kandidaten (score>={cfg['min_score']})_",
        "─────────────────────────────",
        f"🏆 *TOP {top_n} HOOGSTE SCORE:*",
        "\n\n".join(sig_regel(s, detail=True) for s in top_tonen),
    ]

    overige = [s for s in signalen if s not in top_tonen]
    if overige:
        delen += ["─────────────────────────────", f"*{lbl} — overige kandidaten:*"]
        for s in overige:
            delen.append(sig_regel(s))

    delen.append(f"⚙️ _RoE>={cfg['roe_min']:.0f}% | terugverdienperiode<={cfg['terugverdien_max_jaar']:.0f}j | forward P/E<={cfg['fwd_pe_max']:.0f}x | verwachte winstgroei>0% | marktkap<={cfg['marktkap_max']/1e9:.0f}B | analisten<={cfg['analisten_max']}_")
    return "\n\n".join(delen)

# ============================================================
# ENGINE
# ============================================================

def run_engine(modus: str):
    cfg = MODUS_CFG[modus]
    print(f"{'='*60}\n{cfg['label']}  {today_str()}  [suffix: {cfg['bestand_suffix']}]\n{'='*60}")

    exchange_tickers: Dict[str, List[str]] = {}
    for f_name in bouw_bestandslijst(cfg["bestand_suffix"]):
        tlist = load_tickers_from_file(f_name)
        if not tlist:
            print(f"Bestand {f_name} niet gevonden of leeg, overslaan.")
            continue
        ex_name = label_voor(f_name)
        exchange_tickers[ex_name] = tlist
        print(f"  {ex_name}: {len(tlist)} tickers")

    if not exchange_tickers:
        print("[ERROR] Geen ticker bestanden gevonden.")
        return

    email_delen: List[str] = []

    opeenvolgende_rate_limits = 0
    aantal_pauzes = 0
    huidige_throttle = cfg["throttle_sec"]
    totaal_rate_limit_fouten = 0
    vroegtijdig_gestopt = False

    for ex_name, tlist in exchange_tickers.items():
        if vroegtijdig_gestopt:
            break

        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")

        alle: List[HooglSignaal] = []
        for ticker in tlist:
            sig, was_rate_limited = analyse_ticker(ticker, cfg)

            if was_rate_limited:
                opeenvolgende_rate_limits += 1
                totaal_rate_limit_fouten += 1
            else:
                opeenvolgende_rate_limits = 0

            if sig is not None:
                alle.append(sig)
                if sig.score >= cfg["min_score"]:
                    print(f"  ✓ {ticker}: score {sig.score}/6 | RoE={sig.roe_pct:.1f}%")

            if opeenvolgende_rate_limits >= RATE_LIMIT_CONSECUTIVE_DREMPEL:
                if aantal_pauzes >= RATE_LIMIT_MAX_PAUZES:
                    print(f"[RATE LIMIT] Pauzebudget uitgeput na {totaal_rate_limit_fouten} fouten -- run stopt.")
                    vroegtijdig_gestopt = True
                    break
                aantal_pauzes += 1
                huidige_throttle += RATE_LIMIT_THROTTLE_VERHOGING
                print(f"[RATE LIMIT] Pauze {RATE_LIMIT_PAUZE_SEC}s ({aantal_pauzes}/{RATE_LIMIT_MAX_PAUZES})")
                time.sleep(RATE_LIMIT_PAUZE_SEC)
                opeenvolgende_rate_limits = 0

            time.sleep(huidige_throttle)

        kandidaten = [s for s in alle if s.score >= cfg["min_score"]]
        kandidaten.sort(key=lambda s: (s.score, -s.terugverdienperiode), reverse=True)
        signalen = kandidaten[:cfg["top_n"]]

        print(f"  → top {len(signalen)} van {len(kandidaten)} kandidaten (score >= {cfg['min_score']}) uit {len(alle)} geanalyseerd")

        for rank, s in enumerate(signalen, start=1):
            log_selectie(
                ticker=s.ticker, datum=today_str(), strategie=cfg["strategie"], beurs=ex_name, koers=s.price,
                parameters={
                    "score": s.score, "rank": rank, "roe_pct": s.roe_pct,
                    "terugverdienperiode": s.terugverdienperiode, "forward_pe": s.forward_pe,
                    "verwachte_winstgroei_pct": s.verwachte_winstgroei_pct, "div_yield": s.div_yield,
                    "market_cap": s.market_cap, "analisten_count": s.analisten_count,
                    "grafiek": yahoo_link(s.ticker),
                },
            )

        bericht = format_bericht(ex_name, signalen, alle, cfg)
        if bericht:
            send_telegram(bericht, parse_mode="Markdown")
            email_delen.append(bericht)
            print(f"  → Telegram verstuurd")
        else:
            print(f"  → Overgeslagen: {ex_name}")

    if vroegtijdig_gestopt:
        waarschuwing = f"⚠️ Run vroegtijdig gestopt wegens rate limiting ({totaal_rate_limit_fouten} fouten)."
        send_telegram(waarschuwing)
        email_delen.append(waarschuwing)

    if email_delen:
        send_email(f"{cfg['label']} {today_str()}", "\n\n" + ("=" * 40 + "\n\n").join(email_delen))

    print(f"\n{'='*60}\nKlaar.")

def run_backtest():
    print("Backtest wordt niet ondersteund voor bot_01hoogl.")

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
