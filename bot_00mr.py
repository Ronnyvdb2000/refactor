#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00graham.py — BENJAMIN GRAHAM "DEFENSIVE INVESTOR" v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Geen eigen kopieën meer van safe_float, send_telegram, of de
Piotroski F-Score berekening.
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

GRAHAM_CFG = {
    "marktkap_min":        2_000_000_000.0,
    "current_ratio_min":   2.0,
    "winstgroei_min_pct":  10.0,
    "pe_max":              15.0,
    "pb_max":              1.5,
    "graham_multiple_max": 22.5,
    "piotroski_min":       7.0,
    "peg_max":             1.0,
    "min_score":           7,
    "top_n":               5,
    "strategie":           "bot_00graham",
    "throttle_sec":        0.15,
}

# ============================================================
# HULPFUNCTIES VOOR FUNDAMENTALS
# ============================================================

def _row(df, names: List[str]):
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None

def _net_income_series(financials) -> List[float]:
    row = _row(financials, ["Net Income", "Net Income Common Stockholders"])
    if row is None:
        return []
    vals = [safe_float(v) for v in row.tolist()]
    vals = [v for v in vals if not math.isnan(v)]
    return list(reversed(vals))

def _piotroski_f_score(tk, cashflow):
    try:
        bs  = tk.balance_sheet
        inc = tk.financials
    except Exception:
        return -1, "n.v.t. (data-fout)", {}

    if bs is None or bs.empty or inc is None or inc.empty:
        return -1, "n.v.t. (geen balans/resultaten)", {}
    if bs.shape[1] < 2 or inc.shape[1] < 2:
        return -1, "n.v.t. (< 2 jaar data)", {}

    def val(df, names, col):
        row = _row(df, names)
        if row is None or col >= len(row):
            return float("nan")
        return safe_float(row.iloc[col])

    ta0, ta1   = val(bs, ["Total Assets"], 0), val(bs, ["Total Assets"], 1)
    ca0, ca1   = val(bs, ["Current Assets", "Total Current Assets"], 0), val(bs, ["Current Assets", "Total Current Assets"], 1)
    cl0, cl1   = val(bs, ["Current Liabilities", "Total Current Liabilities"], 0), val(bs, ["Current Liabilities", "Total Current Liabilities"], 1)
    ltd0, ltd1 = val(bs, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], 0), val(bs, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], 1)
    sh0, sh1   = val(bs, ["Share Issued", "Ordinary Shares Number"], 0), val(bs, ["Share Issued", "Ordinary Shares Number"], 1)

    ni0, ni1   = val(inc, ["Net Income"], 0), val(inc, ["Net Income"], 1)
    rev0, rev1 = val(inc, ["Total Revenue"], 0), val(inc, ["Total Revenue"], 1)
    gp0, gp1   = val(inc, ["Gross Profit"], 0), val(inc, ["Gross Profit"], 1)

    ocf_row = _row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities", "Cash Flow From Continuing Operating Activities"])
    ocf0 = safe_float(ocf_row.iloc[0]) if ocf_row is not None and len(ocf_row) > 0 else float("nan")

    score, vlaggen = 0, {}

    roa0 = ni0 / ta0 if not math.isnan(ni0) and not math.isnan(ta0) and ta0 != 0 else float("nan")
    roa1 = ni1 / ta1 if not math.isnan(ni1) and not math.isnan(ta1) and ta1 != 0 else float("nan")
    vlaggen["roa_positief"] = (not math.isnan(roa0)) and roa0 > 0
    vlaggen["ocf_positief"] = (not math.isnan(ocf0)) and ocf0 > 0
    vlaggen["roa_gestegen"] = (not math.isnan(roa0)) and (not math.isnan(roa1)) and roa0 > roa1
    vlaggen["winstkwaliteit"] = (not math.isnan(ocf0)) and (not math.isnan(ni0)) and ocf0 > ni0

    lev0 = ltd0 / ta0 if not math.isnan(ltd0) and not math.isnan(ta0) and ta0 != 0 else float("nan")
    lev1 = ltd1 / ta1 if not math.isnan(ltd1) and not math.isnan(ta1) and ta1 != 0 else float("nan")
    vlaggen["hefboom_gedaald"] = (not math.isnan(lev0)) and (not math.isnan(lev1)) and lev0 < lev1

    cr0 = ca0 / cl0 if not math.isnan(ca0) and not math.isnan(cl0) and cl0 != 0 else float("nan")
    cr1 = ca1 / cl1 if not math.isnan(ca1) and not math.isnan(cl1) and cl1 != 0 else float("nan")
    vlaggen["liquiditeit_gestegen"] = (not math.isnan(cr0)) and (not math.isnan(cr1)) and cr0 > cr1

    vlaggen["geen_verwatering"] = (not math.isnan(sh0)) and (not math.isnan(sh1)) and sh0 <= sh1

    gm0 = gp0 / rev0 if not math.isnan(gp0) and not math.isnan(rev0) and rev0 != 0 else float("nan")
    gm1 = gp1 / rev1 if not math.isnan(gp1) and not math.isnan(rev1) and rev1 != 0 else float("nan")
    vlaggen["marge_gestegen"] = (not math.isnan(gm0)) and (not math.isnan(gm1)) and gm0 > gm1

    at0 = rev0 / ta0 if not math.isnan(rev0) and not math.isnan(ta0) and ta0 != 0 else float("nan")
    at1 = rev1 / ta1 if not math.isnan(rev1) and not math.isnan(ta1) and ta1 != 0 else float("nan")
    vlaggen["efficientie_gestegen"] = (not math.isnan(at0)) and (not math.isnan(at1)) and at0 > at1

    score = sum(1 for v in vlaggen.values() if v)
    kort = {
        "roa_positief": "ROA", "ocf_positief": "OCF", "roa_gestegen": "ΔROA",
        "winstkwaliteit": "Accruals", "hefboom_gedaald": "Hefboom", "liquiditeit_gestegen": "Liquid.",
        "geen_verwatering": "GeenDilutie", "marge_gestegen": "Marge", "efficientie_gestegen": "Efficiëntie",
    }
    label = f"{score}/9 (" + " ".join((("✓" if vlaggen[k] else "✗") + kort[k]) for k in kort) + ")"
    return score, label, vlaggen

# ============================================================
# SIGNAAL
# ============================================================

@dataclass
class GrahamSignaal:
    ticker:              str
    price:               float
    score:               int
    market_cap:          float
    current_ratio:       float
    eps_years:           int
    eps_all_positive:    bool
    div_yield:           float
    eps_growth_pct:      float
    pe_ratio:            float
    pb_ratio:            float
    graham_number_ok:    bool
    piotroski_score:     float
    piotroski_ok:        bool
    eps_cagr_pct:        float
    peg_ratio:           float
    peg_ok:              bool
    marktkap_label:      str
    current_ratio_label: str
    stabiliteit_label:   str
    dividend_label:      str
    groei_label:         str
    pe_label:            str
    pb_label:            str
    piotroski_label:     str
    peg_label:           str

def analyse_ticker(ticker: str, cfg: dict) -> Optional[GrahamSignaal]:
    try:
        tk   = yf.Ticker(ticker)
        info = tk.info or {}

        price = safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
        if math.isnan(price) or price <= 0:
            return None

        market_cap    = safe_float(info.get("marketCap"))
        current_ratio = safe_float(info.get("currentRatio"))
        div_yield     = safe_float(info.get("dividendYield"), 0.0)
        pe_ratio      = safe_float(info.get("trailingPE"))
        pb_ratio      = safe_float(info.get("priceToBook"))

        score = 0

        if not math.isnan(market_cap) and market_cap >= cfg["marktkap_min"]:
            score += 1
            marktkap_label = f"✓ {market_cap/1e9:.2f}B (>= {cfg['marktkap_min']/1e9:.0f}B)"
        else:
            marktkap_label = f"✗ {market_cap/1e9:.2f}B" if not math.isnan(market_cap) else "✗ onbekend"

        if not math.isnan(current_ratio) and current_ratio >= cfg["current_ratio_min"]:
            score += 1
            current_ratio_label = f"✓ {current_ratio:.2f} (>= {cfg['current_ratio_min']:.1f})"
        else:
            current_ratio_label = f"✗ {current_ratio:.2f}" if not math.isnan(current_ratio) else "✗ onbekend"

        try:
            financials = tk.financials
        except Exception:
            financials = None
        ni_series = _net_income_series(financials)
        eps_years = len(ni_series)

        if eps_years >= 2:
            eps_all_positive = all(v > 0 for v in ni_series)
            if eps_all_positive:
                score += 1
                stabiliteit_label = f"✓ {eps_years}/{eps_years} jaar winst positief"
            else:
                stabiliteit_label = f"✗ {sum(1 for v in ni_series if v > 0)}/{eps_years} jaar winst positief"

            oudste, nieuwste = ni_series[0], ni_series[-1]
            if oudste > 0:
                eps_growth_pct = (nieuwste - oudste) / oudste * 100
            else:
                eps_growth_pct = float("nan")

            if not math.isnan(eps_growth_pct) and eps_growth_pct >= cfg["winstgroei_min_pct"]:
                score += 1
                groei_label = f"✓ {eps_growth_pct:+.1f}% over {eps_years} jaar"
            else:
                groei_label = f"✗ {eps_growth_pct:+.1f}% over {eps_years} jaar" if not math.isnan(eps_growth_pct) else "✗ onbekend"

            if oudste > 0 and nieuwste > 0 and eps_years >= 2:
                eps_cagr_pct = ((nieuwste / oudste) ** (1 / (eps_years - 1)) - 1) * 100
            else:
                eps_cagr_pct = float("nan")
        else:
            eps_all_positive = False
            eps_growth_pct = float("nan")
            eps_cagr_pct = float("nan")
            stabiliteit_label = "✗ onbekend (< 2 jaar resultatenrekening)"
            groei_label = "✗ onbekend (< 2 jaar resultatenrekening)"

        if not math.isnan(div_yield) and div_yield > 0:
            score += 1
            dividend_label = f"✓ yield {div_yield:.2f}%"
        else:
            dividend_label = "✗ geen dividend"

        if not math.isnan(pe_ratio) and 0 < pe_ratio <= cfg["pe_max"]:
            score += 1
            pe_label = f"✓ {pe_ratio:.1f}x (<= {cfg['pe_max']:.0f}x)"
        else:
            pe_label = f"✗ {pe_ratio:.1f}x" if not math.isnan(pe_ratio) else "✗ onbekend"

        graham_multiple = pe_ratio * pb_ratio if not math.isnan(pe_ratio) and not math.isnan(pb_ratio) else float("nan")
        pb_ok = not math.isnan(pb_ratio) and 0 < pb_ratio <= cfg["pb_max"]
        multiple_ok = not math.isnan(graham_multiple) and graham_multiple <= cfg["graham_multiple_max"]
        graham_number_ok = pb_ok or multiple_ok
        if graham_number_ok:
            score += 1
            pb_label = f"✓ P/B {pb_ratio:.2f} | PExP/B {graham_multiple:.1f} (<= {cfg['graham_multiple_max']:.1f})"
        else:
            pb_label = f"✗ P/B {pb_ratio:.2f} | PExP/B {graham_multiple:.1f}" if not math.isnan(graham_multiple) else "✗ onbekend"

        try:
            cashflow = tk.cashflow
        except Exception:
            cashflow = None
        piotroski_score, piotroski_label, _ = _piotroski_f_score(tk, cashflow)
        piotroski_ok = piotroski_score >= cfg["piotroski_min"]
        if piotroski_ok:
            score += 1

        if not math.isnan(pe_ratio) and pe_ratio > 0 and not math.isnan(eps_cagr_pct) and eps_cagr_pct > 0:
            peg_ratio = pe_ratio / eps_cagr_pct
            peg_ok = peg_ratio < cfg["peg_max"]
            if peg_ok:
                score += 1
                peg_label = f"✓ {peg_ratio:.2f} (<{cfg['peg_max']:.1f}, CAGR {eps_cagr_pct:.1f}%/j)"
            else:
                peg_label = f"✗ {peg_ratio:.2f} (CAGR {eps_cagr_pct:.1f}%/j)"
        else:
            peg_ratio = float("nan")
            peg_ok = False
            peg_label = "✗ onbekend (geen positieve CAGR of P/E)"

        return GrahamSignaal(
            ticker=ticker, price=round(price, 2), score=score,
            market_cap=round(market_cap, 0) if not math.isnan(market_cap) else 0.0,
            current_ratio=round(current_ratio, 2) if not math.isnan(current_ratio) else 0.0,
            eps_years=eps_years, eps_all_positive=eps_all_positive,
            div_yield=round(div_yield, 2) if not math.isnan(div_yield) else 0.0,
            eps_growth_pct=round(eps_growth_pct, 1) if not math.isnan(eps_growth_pct) else 0.0,
            pe_ratio=round(pe_ratio, 1) if not math.isnan(pe_ratio) else 0.0,
            pb_ratio=round(pb_ratio, 2) if not math.isnan(pb_ratio) else 0.0,
            graham_number_ok=graham_number_ok,
            piotroski_score=piotroski_score, piotroski_ok=piotroski_ok,
            eps_cagr_pct=round(eps_cagr_pct, 1) if not math.isnan(eps_cagr_pct) else 0.0,
            peg_ratio=round(peg_ratio, 2) if not math.isnan(peg_ratio) else 0.0,
            peg_ok=peg_ok,
            marktkap_label=marktkap_label, current_ratio_label=current_ratio_label,
            stabiliteit_label=stabiliteit_label, dividend_label=dividend_label,
            groei_label=groei_label, pe_label=pe_label, pb_label=pb_label,
            piotroski_label=piotroski_label, peg_label=peg_label,
        )
    except Exception as e:
        print(f"[WARN] {ticker}: fout — {e}")
        return None

# ============================================================
# OUTPUT
# ============================================================

def _score_bar(score: int) -> str:
    return "█" * score + "░" * (9 - score) + f" {score}/9"

def format_bericht(exchange_name: str, signalen: List[GrahamSignaal], alle: List[GrahamSignaal], cfg: dict) -> Optional[str]:
    if not alle:
        return None

    nu     = today_str()
    top_n  = cfg["top_n"]
    top_tonen = sorted(alle, key=lambda s: (s.score, -s.pe_ratio if s.pe_ratio > 0 else -999), reverse=True)[:top_n]
    max_sc = max((s.score for s in signalen), default=0) if signalen else 0
    lbl    = {9: "⭐ PERFECTE SCORE (9/9)", 8: "🟡 STERK (8/9)", 7: "🟠 GOED (7/9)"}.get(max_sc, "📊")

    def sig_regel(s: GrahamSignaal, detail: bool = False) -> str:
        r = f"• `{s.ticker}` {_score_bar(s.score)} | EUR{s.price:.2f} | P/E:{s.pe_ratio:.1f} | P/B:{s.pb_ratio:.2f} | {yahoo_link(s.ticker)}"
        if detail:
            r += f"\n  {s.marktkap_label} | {s.current_ratio_label}\n  Winst: {s.stabiliteit_label} | Groei: {s.groei_label}\n  Dividend: {s.dividend_label} | Waardering: {s.pb_label}\n  Piotroski F-Score: {s.piotroski_label}\n  PEG (Lynch): {s.peg_label}"
        return r

    delen = [
        f"📜 *GRAHAM DEFENSIVE INVESTOR — {exchange_name}*",
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

    delen.append(f"⚙️ _marktkap>={cfg['marktkap_min']/1e9:.0f}B | current ratio>={cfg['current_ratio_min']:.1f} | winst positief alle jaren | dividend uitkerend | winstgroei>={cfg['winstgroei_min_pct']:.0f}% | P/E<={cfg['pe_max']:.0f}x | P/B<={cfg['pb_max']:.1f} of PExP/B<={cfg['graham_multiple_max']:.1f} | Piotroski F-Score>={cfg['piotroski_min']:.0f}/9 | PEG<{cfg['peg_max']:.1f}_")
    return "\n\n".join(delen)

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    cfg = GRAHAM_CFG
    print(f"{'='*60}\nGRAHAM DEFENSIVE INVESTOR — LIVE  {today_str()}\n{'='*60}")

    exchange_tickers: Dict[str, List[str]] = {}
    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist: continue
        ex_name = label_voor(f_name)
        exchange_tickers[ex_name] = tlist
        print(f"  {ex_name}: {len(tlist)} tickers")

    if not exchange_tickers:
        print("[ERROR] Geen ticker bestanden gevonden.")
        return

    email_delen: List[str] = []
    reeds_verwerkt: set = set()
    totaal_gedupliceerd = 0

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")

        alle: List[GrahamSignaal] = []
        for ticker in tlist:
            if ticker in reeds_verwerkt:
                totaal_gedupliceerd += 1
                continue
            reeds_verwerkt.add(ticker)
            sig = analyse_ticker(ticker, cfg)
            if sig is not None:
                alle.append(sig)
                if sig.score >= cfg["min_score"]:
                    print(f"  ✓ {ticker}: score {sig.score}/9 | P/E={sig.pe_ratio:.1f} | P/B={sig.pb_ratio:.2f}")
            time.sleep(cfg["throttle_sec"])

        kandidaten = [s for s in alle if s.score >= cfg["min_score"]]
        kandidaten.sort(key=lambda s: (s.score, -s.pe_ratio if s.pe_ratio > 0 else -999), reverse=True)
        signalen = kandidaten[:cfg["top_n"]]

        print(f"  → top {len(signalen)} van {len(kandidaten)} kandidaten (score >= {cfg['min_score']}) uit {len(alle)} geanalyseerd")

        for rank, s in enumerate(signalen, start=1):
            log_selectie(
                ticker=s.ticker,
                datum=today_str(),
                strategie=cfg["strategie"],
                beurs=ex_name,
                koers=s.price,
                parameters={
                    "score": s.score,
                    "rank": rank,
                    "market_cap": s.market_cap,
                    "current_ratio": s.current_ratio,
                    "eps_years": s.eps_years,
                    "eps_all_positive": s.eps_all_positive,
                    "div_yield": s.div_yield,
                    "eps_growth_pct": s.eps_growth_pct,
                    "pe_ratio": s.pe_ratio,
                    "pb_ratio": s.pb_ratio,
                    "graham_number_ok": s.graham_number_ok,
                    "piotroski_score": s.piotroski_score,
                    "piotroski_detail": s.piotroski_label,
                    "eps_cagr_pct": s.eps_cagr_pct,
                    "peg_ratio": s.peg_ratio,
                    "peg_ok": s.peg_ok,
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
        send_email(f"Graham Defensive Investor rapport {today_str()}", "\n\n" + ("=" * 40 + "\n\n").join(email_delen))

    if totaal_gedupliceerd:
        print(f"\nDeduplicatie: {totaal_gedupliceerd} dubbele ticker-occurrence(s) overgeslagen "
              f"(overlap tussen beursbestanden) — elke ticker enkel meegeteld in de eerst "
              f"gescande beurs waarin hij voorkomt.")

    print(f"\n{'='*60}\nKlaar.")

def run_backtest():
    print("Backtest wordt niet ondersteund voor bot_00graham: yfinance biedt geen betrouwbare historische reeks van fundamentele data.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
