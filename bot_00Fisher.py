#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00Fisher.py — KENNETH FISHER PRICE-TO-SALES v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Screent op P/S-ratio, omzetgroei en balansgezondheid.
"""

import sys
import math
import time
from dataclasses import dataclass
from typing import List, Dict, Optional

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

FISHER_CFG = {
    "psr_max_tech":       3.0,
    "psr_max_default":    1.5,
    "marktkap_min":       200_000_000.0,
    "debt_equity_max":    150.0,
    "current_ratio_min":  1.0,
    "min_score":          4,
    "top_n":              5,
    "strategie":          "bot_00Fisher",
    "throttle_sec":       0.15,
}

# ============================================================
# HULPFUNCTIES
# ============================================================

def _row(df, names: List[str]):
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None

# ============================================================
# FISHER P/S-ANALYSE
# ============================================================

@dataclass
class FisherSignaal:
    ticker:              str
    price:               float
    score:               int
    sector:              str
    ps_ratio:            float
    market_cap:          float
    revenue_growth_pct:  float
    debt_to_equity:      float
    current_ratio:       float
    ps_label:            str
    marktkap_label:      str
    groei_label:         str
    schuld_label:        str
    current_ratio_label: str

def analyse_ticker(ticker: str, cfg: dict) -> Optional[FisherSignaal]:
    try:
        tk   = yf.Ticker(ticker)
        info = tk.info or {}

        price = safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
        if math.isnan(price) or price <= 0:
            return None

        sector     = info.get("sector") or "Onbekend"
        market_cap = safe_float(info.get("marketCap"))
        ps_ratio   = safe_float(info.get("priceToSalesTrailing12Months"))

        score = 0

        psr_drempel = cfg["psr_max_tech"] if sector == "Technology" else cfg["psr_max_default"]
        if not math.isnan(ps_ratio) and 0 < ps_ratio <= psr_drempel:
            score += 1
            ps_label = f"✓ {ps_ratio:.2f} (<= {psr_drempel:.1f}, sector: {sector})"
        else:
            ps_label = f"✗ {ps_ratio:.2f} (grens {psr_drempel:.1f}, sector: {sector})" if not math.isnan(ps_ratio) else "✗ onbekend"

        if not math.isnan(market_cap) and market_cap >= cfg["marktkap_min"]:
            score += 1
            marktkap_label = f"✓ {market_cap/1e6:.0f}M (>= {cfg['marktkap_min']/1e6:.0f}M)"
        else:
            marktkap_label = f"✗ {market_cap/1e6:.0f}M" if not math.isnan(market_cap) else "✗ onbekend"

        try:
            inc = tk.financials
        except Exception:
            inc = None
        rev_row = _row(inc, ["Total Revenue"]) if inc is not None else None
        if rev_row is not None and len(rev_row) >= 2:
            rev_nu, rev_vorig = safe_float(rev_row.iloc[0]), safe_float(rev_row.iloc[1])
            if not math.isnan(rev_nu) and not math.isnan(rev_vorig) and rev_vorig > 0:
                revenue_growth_pct = (rev_nu - rev_vorig) / rev_vorig * 100
                if revenue_growth_pct > 0:
                    score += 1
                    groei_label = f"✓ omzet {revenue_growth_pct:+.1f}%"
                else:
                    groei_label = f"✗ omzet {revenue_growth_pct:+.1f}%"
            else:
                revenue_growth_pct = float("nan")
                groei_label = "✗ onbekend"
        else:
            revenue_growth_pct = float("nan")
            groei_label = "✗ onbekend (< 2 jaar resultatenrekening)"

        debt_to_equity = safe_float(info.get("debtToEquity"))
        if not math.isnan(debt_to_equity) and debt_to_equity <= cfg["debt_equity_max"]:
            score += 1
            schuld_label = f"✓ D/E {debt_to_equity:.0f} (<= {cfg['debt_equity_max']:.0f})"
        else:
            schuld_label = f"✗ D/E {debt_to_equity:.0f}" if not math.isnan(debt_to_equity) else "✗ onbekend"

        current_ratio = safe_float(info.get("currentRatio"))
        if not math.isnan(current_ratio) and current_ratio >= cfg["current_ratio_min"]:
            score += 1
            current_ratio_label = f"✓ {current_ratio:.2f} (>= {cfg['current_ratio_min']:.1f})"
        else:
            current_ratio_label = f"✗ {current_ratio:.2f}" if not math.isnan(current_ratio) else "✗ onbekend"

        return FisherSignaal(
            ticker=ticker, price=round(price, 2), score=score, sector=sector,
            ps_ratio=round(ps_ratio, 2) if not math.isnan(ps_ratio) else 0.0,
            market_cap=round(market_cap, 0) if not math.isnan(market_cap) else 0.0,
            revenue_growth_pct=round(revenue_growth_pct, 1) if not math.isnan(revenue_growth_pct) else 0.0,
            debt_to_equity=round(debt_to_equity, 1) if not math.isnan(debt_to_equity) else 0.0,
            current_ratio=round(current_ratio, 2) if not math.isnan(current_ratio) else 0.0,
            ps_label=ps_label, marktkap_label=marktkap_label,
            groei_label=groei_label, schuld_label=schuld_label,
            current_ratio_label=current_ratio_label,
        )
    except Exception as e:
        print(f"[WARN] {ticker}: fout — {e}")
        return None

# ============================================================
# OUTPUT
# ============================================================

def _score_bar(score: int) -> str:
    return "█" * score + "░" * (5 - score) + f" {score}/5"

def format_bericht(exchange_name: str, signalen: List[FisherSignaal], alle: List[FisherSignaal], cfg: dict) -> Optional[str]:
    if not alle:
        return None

    nu = today_str()
    top_n = cfg["top_n"]
    top_tonen = sorted(alle, key=lambda s: (s.score, -s.ps_ratio if s.ps_ratio > 0 else -999), reverse=True)[:top_n]

    def sig_regel(s: FisherSignaal, detail: bool = False) -> str:
        r = (
            f"• `{s.ticker}` {_score_bar(s.score)} | EUR{s.price:.2f} | "
            f"P/S:{s.ps_ratio:.2f} | {s.sector} | {yahoo_link(s.ticker)}"
        )
        if detail:
            r += (
                f"\n  {s.ps_label} | {s.marktkap_label}"
                f"\n  Omzetgroei: {s.groei_label} | Schuld: {s.schuld_label}"
                f"\n  Liquiditeit: {s.current_ratio_label}"
            )
        return r

    delen = [
        f"📊 *FISHER PRICE-TO-SALES — {exchange_name}*",
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

    delen.append(
        f"⚙️ _P/S<={cfg['psr_max_tech']:.1f} (tech) of <={cfg['psr_max_default']:.1f} (overig) | "
        f"marktkap>={cfg['marktkap_min']/1e6:.0f}M | omzetgroei>0% | D/E<={cfg['debt_equity_max']:.0f} | "
        f"current ratio>={cfg['current_ratio_min']:.1f}. GEEN winstvereiste._"
    )
    return "\n\n".join(delen)

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    cfg = FISHER_CFG
    print(f"{'='*60}\nFISHER PRICE-TO-SALES — LIVE  {today_str()}\n{'='*60}")

    exchange_tickers: Dict[str, List[str]] = {}
    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist:
            continue
        ex_name = label_voor(f_name)
        exchange_tickers[ex_name] = tlist
        print(f"  {ex_name}: {len(tlist)} tickers")

    if not exchange_tickers:
        print("[ERROR] Geen ticker bestanden gevonden.")
        return

    email_delen: List[str] = []
    reeds_verwerkt: set = set()

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")

        alle: List[FisherSignaal] = []
        for ticker in tlist:
            if ticker in reeds_verwerkt:
                continue
            reeds_verwerkt.add(ticker)
            sig = analyse_ticker(ticker, cfg)
            if sig is not None:
                alle.append(sig)
            time.sleep(cfg["throttle_sec"])

        kandidaten = [s for s in alle if s.score >= cfg["min_score"]]
        kandidaten.sort(key=lambda s: (s.score, -s.ps_ratio if s.ps_ratio > 0 else -999), reverse=True)
        signalen = kandidaten[:cfg["top_n"]]

        print(f"  → top {len(signalen)} van {len(kandidaten)} kandidaten (score >= {cfg['min_score']}) uit {len(alle)} geanalyseerd")

        for rank, s in enumerate(signalen, start=1):
            log_selectie(
                ticker=s.ticker, datum=today_str(), strategie=cfg["strategie"], beurs=ex_name, koers=s.price,
                parameters={
                    "score": s.score, "rank": rank, "sector": s.sector,
                    "ps_ratio": s.ps_ratio, "market_cap": s.market_cap,
                    "revenue_growth_pct": s.revenue_growth_pct,
                    "debt_to_equity": s.debt_to_equity, "current_ratio": s.current_ratio,
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

    if email_delen:
        send_email(f"Fisher Price-to-Sales rapport {today_str()}", "\n\n" + ("=" * 40 + "\n\n").join(email_delen))

    print(f"\n{'='*60}\nKlaar.")

def run_backtest():
    print("Backtest wordt niet ondersteund voor bot_00Fisher.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
