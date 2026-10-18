#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01kasstr.py — KASSTROOM ONDERWAARDERING v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
FCF-analyse, balans-kwaliteit, Piotroski F-Score en notificaties
zijn gecentraliseerd.
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

FCF_CFG = {
    "fcf_yield_min":       5.0,
    "net_debt_ebitda_max": 3.0,
    "payout_max_pct":      100.0,
    "min_years_required":  2,
    "marktkap_max":        5_000_000_000.0,
    "analisten_max":       5,
    "min_score":           5,
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

def _fcf_series(cashflow) -> List[float]:
    if cashflow is None or cashflow.empty:
        return []
    fcf_row = _row(cashflow, ["Free Cash Flow"])
    if fcf_row is not None:
        vals = [safe_float(v) for v in fcf_row.tolist()]
    else:
        ocf = _row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities", "Cash Flow From Continuing Operating Activities"])
        capex = _row(cashflow, ["Capital Expenditure", "Capital Expenditures", "Purchase Of PPE"])
        if ocf is None or capex is None:
            return []
        vals = [safe_float(o) - abs(safe_float(c)) for o, c in zip(ocf.tolist(), capex.tolist())]
    vals = [v for v in vals if not math.isnan(v)]
    return list(reversed(vals))

def _shareholder_return(cashflow) -> float:
    if cashflow is None or cashflow.empty:
        return 0.0
    div = _row(cashflow, ["Cash Dividends Paid", "Common Stock Dividend Paid", "Payment Of Dividends"])
    bb  = _row(cashflow, ["Repurchase Of Capital Stock", "Common Stock Repurchase", "Repurchase Of Common Stock"])
    total = 0.0
    if div is not None:
        total += abs(safe_float(div.iloc[0], 0.0))
    if bb is not None:
        total += abs(safe_float(bb.iloc[0], 0.0))
    return total

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
class FCFSignaal:
    ticker:          str
    price:           float
    score:           int
    market_cap:      float
    fcf_now:         float
    fcf_yield:       float
    fcf_years:       int
    fcf_growing:     bool
    fcf_consistent:  bool
    rev_growth_pct:  float
    net_debt_ebitda: float
    payout_pct:      float
    div_yield:       float
    analisten_count: float
    yield_label:     str
    trend_label:     str
    consist_label:   str
    growth_label:    str
    debt_label:      str
    payout_label:    str
    marktkap_label:  str
    analisten_label: str
    piotroski_score: int
    piotroski_label: str

def analyse_ticker(ticker: str) -> Optional[FCFSignaal]:
    try:
        tk   = yf.Ticker(ticker)
        info = tk.info or {}

        price = safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
        market_cap = safe_float(info.get("marketCap"))
        if math.isnan(price) or price <= 0 or math.isnan(market_cap) or market_cap <= 0:
            return None

        cashflow = tk.cashflow
        fcf_series = _fcf_series(cashflow)
        if len(fcf_series) < FCF_CFG["min_years_required"]:
            return None
        fcf_now = fcf_series[-1]

        _fcf_yield_raw = (fcf_now / market_cap) * 100 if market_cap > 0 else float("nan")
        if not math.isnan(_fcf_yield_raw) and abs(_fcf_yield_raw) > 100:
            print(f"[WARN] {ticker}: FCF yield implausibel ({_fcf_yield_raw:.0f}%) — data-mismatch, overgeslagen")
            return None

        score = 0

        fcf_yield = (fcf_now / market_cap) * 100 if market_cap > 0 else float("nan")
        if not math.isnan(fcf_yield) and fcf_yield >= FCF_CFG["fcf_yield_min"]:
            score += 1
            yield_label = f"✓ {fcf_yield:.1f}% (>= {FCF_CFG['fcf_yield_min']:.0f}%)"
        else:
            yield_label = f"✗ {fcf_yield:.1f}%" if not math.isnan(fcf_yield) else "✗ onbekend"

        fcf_growing = fcf_series[-1] > fcf_series[0]
        if fcf_growing:
            score += 1
            trend_label = f"✓ groeiend ({fcf_series[0]:,.0f} -> {fcf_series[-1]:,.0f})"
        else:
            trend_label = f"✗ dalend ({fcf_series[0]:,.0f} -> {fcf_series[-1]:,.0f})"

        fcf_consistent = all(v > 0 for v in fcf_series)
        if fcf_consistent:
            score += 1
            consist_label = f"✓ positief in {len(fcf_series)}/{len(fcf_series)} jaar"
        else:
            n_pos = sum(1 for v in fcf_series if v > 0)
            consist_label = f"✗ positief in {n_pos}/{len(fcf_series)} jaar"

        rev_growth = safe_float(info.get("revenueGrowth"))
        rev_growth_pct = rev_growth * 100 if not math.isnan(rev_growth) else float("nan")
        if not math.isnan(rev_growth_pct) and rev_growth_pct > 0:
            score += 1
            growth_label = f"✓ {rev_growth_pct:+.1f}%"
        else:
            growth_label = f"✗ {rev_growth_pct:+.1f}%" if not math.isnan(rev_growth_pct) else "✗ onbekend"

        total_debt = safe_float(info.get("totalDebt"), 0.0)
        total_cash = safe_float(info.get("totalCash"), 0.0)
        ebitda     = safe_float(info.get("ebitda"))
        net_debt   = total_debt - total_cash
        if not math.isnan(ebitda) and ebitda > 0:
            net_debt_ebitda = net_debt / ebitda
            if net_debt_ebitda <= FCF_CFG["net_debt_ebitda_max"]:
                score += 1
                debt_label = f"✓ {net_debt_ebitda:.1f}x EBITDA"
            else:
                debt_label = f"✗ {net_debt_ebitda:.1f}x EBITDA"
        else:
            net_debt_ebitda = float("nan")
            debt_label = "✗ EBITDA onbekend"

        sh_return  = _shareholder_return(cashflow)
        payout_pct = (sh_return / fcf_now * 100) if fcf_now > 0 else float("nan")
        div_yield  = safe_float(info.get("dividendYield"), 0.0)
        if not math.isnan(payout_pct) and 0 < payout_pct <= FCF_CFG["payout_max_pct"]:
            score += 1
            payout_label = f"✓ {payout_pct:.0f}% van FCF"
        elif not math.isnan(payout_pct) and payout_pct > FCF_CFG["payout_max_pct"]:
            payout_label = f"✗ {payout_pct:.0f}% van FCF (onhoudbaar)"
        else:
            payout_label = "✗ geen dividend/buyback"

        if market_cap <= FCF_CFG["marktkap_max"]:
            score += 1
            marktkap_label = f"✓ {market_cap/1e9:.2f}B (<= {FCF_CFG['marktkap_max']/1e9:.0f}B)"
        else:
            marktkap_label = f"✗ {market_cap/1e9:.2f}B"

        analisten_raw = safe_float(info.get("numberOfAnalystOpinions"))
        if not math.isnan(analisten_raw):
            analisten_count = analisten_raw
            if analisten_count <= FCF_CFG["analisten_max"]:
                score += 1
                analisten_label = f"✓ {analisten_count:.0f} analisten (<= {FCF_CFG['analisten_max']})"
            else:
                analisten_label = f"✗ {analisten_count:.0f} analisten"
        else:
            analisten_count = float("nan")
            analisten_label = "✗ coverage onbekend"

        piotroski_score, piotroski_label, _ = _piotroski_f_score(tk, cashflow)

        return FCFSignaal(
            ticker=ticker, price=round(price, 2), score=score,
            market_cap=market_cap, fcf_now=fcf_now,
            fcf_yield=round(fcf_yield, 2) if not math.isnan(fcf_yield) else 0.0,
            fcf_years=len(fcf_series), fcf_growing=fcf_growing, fcf_consistent=fcf_consistent,
            rev_growth_pct=round(rev_growth_pct, 2) if not math.isnan(rev_growth_pct) else 0.0,
            net_debt_ebitda=round(net_debt_ebitda, 2) if not math.isnan(net_debt_ebitda) else 0.0,
            payout_pct=round(payout_pct, 1) if not math.isnan(payout_pct) else 0.0,
            div_yield=round(div_yield, 2) if not math.isnan(div_yield) else 0.0,
            analisten_count=round(analisten_count, 0) if not math.isnan(analisten_count) else -1.0,
            yield_label=yield_label, trend_label=trend_label, consist_label=consist_label,
            growth_label=growth_label, debt_label=debt_label, payout_label=payout_label,
            marktkap_label=marktkap_label, analisten_label=analisten_label,
            piotroski_score=piotroski_score, piotroski_label=piotroski_label,
        )
    except Exception as e:
        print(f"[WARN] {ticker}: fout — {e}")
        return None

# ============================================================
# OUTPUT
# ============================================================

def _score_bar(score: int) -> str:
    return "█" * score + "░" * (8 - score) + f" {score}/8"

def format_bericht(exchange_name: str, signalen: List[FCFSignaal], alle: List[FCFSignaal]) -> Optional[str]:
    if not alle:
        return None

    nu     = today_str()
    top3   = sorted(alle, key=lambda s: (s.score, s.piotroski_score, s.fcf_yield), reverse=True)[:3]
    max_sc = max((s.score for s in signalen), default=0) if signalen else 0
    lbl    = {8: "⭐ PERFECTE SCORE (8/8)", 7: "🟡 STERK (7/8)", 6: "🟠 GOED (6/8)", 5: "🟠 WATCHLIST (5/8)"}.get(max_sc, "📊")

    def sig_regel(s: FCFSignaal, detail: bool = False) -> str:
        r = f"• `{s.ticker}` {_score_bar(s.score)} | EUR{s.price:.2f} | FCF yield:{s.fcf_yield:.1f}% | {yahoo_link(s.ticker)}"
        if detail:
            r += f"\n  {s.trend_label} | {s.consist_label}\n  Omzet: {s.growth_label} | Schuld: {s.debt_label} | Payout: {s.payout_label}\n  Marktkap: {s.marktkap_label} | Coverage: {s.analisten_label}\n  Piotroski F-Score: {s.piotroski_label}"
        return r

    delen = [
        f"💰 *KASSTROOM ONDERWAARDERING — {exchange_name}*",
        f"_{nu} | {len(alle)} geanalyseerd | {len(signalen)} kandidaten (score>={FCF_CFG['min_score']})_",
        "─────────────────────────────",
        f"🏆 *TOP 3 HOOGSTE SCORE:*",
        "\n\n".join(sig_regel(s, detail=True) for s in top3),
    ]

    overige = [s for s in signalen if s not in top3]
    if overige:
        delen += ["─────────────────────────────", f"*{lbl} — overige kandidaten:*"]
        for s in overige:
            delen.append(sig_regel(s))

    delen.append(f"⚙️ _FCF yield>={FCF_CFG['fcf_yield_min']:.0f}% | groeiende & consistente FCF | omzetgroei>0 | NetDebt/EBITDA<={FCF_CFG['net_debt_ebitda_max']:.0f}x | payout<=100% FCF | marktkap<={FCF_CFG['marktkap_max']/1e9:.0f}B | analisten<={FCF_CFG['analisten_max']}_")
    return "\n\n".join(delen)

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    print(f"{'='*60}\nKASSTROOM ONDERWAARDERING — LIVE  {today_str()}\n{'='*60}")

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

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")

        alle: List[FCFSignaal] = []
        for ticker in tlist:
            sig = analyse_ticker(ticker)
            if sig is not None:
                alle.append(sig)
                if sig.score >= FCF_CFG["min_score"]:
                    print(f"  ✓ {ticker}: score {sig.score}/8 | FCF yield={sig.fcf_yield:.1f}%")
            time.sleep(0.15)

        kandidaten = [s for s in alle if s.score >= FCF_CFG["min_score"]]
        kandidaten.sort(key=lambda s: (s.score, s.piotroski_score, s.fcf_yield), reverse=True)
        signalen = kandidaten[:5]

        print(f"  → top {len(signalen)} van {len(kandidaten)} kandidaten (score >= {FCF_CFG['min_score']}) uit {len(alle)} geanalyseerd")

        for rank, s in enumerate(signalen, start=1):
            log_selectie(
                ticker=s.ticker, datum=today_str(), strategie="bot_01kasstr", beurs=ex_name, koers=s.price,
                parameters={
                    "score": s.score, "rank": rank, "fcf_yield": s.fcf_yield,
                    "fcf_years": s.fcf_years, "fcf_growing": s.fcf_growing, "fcf_consistent": s.fcf_consistent,
                    "rev_growth_pct": s.rev_growth_pct, "net_debt_ebitda": s.net_debt_ebitda,
                    "payout_pct": s.payout_pct, "div_yield": s.div_yield, "market_cap": s.market_cap,
                    "analisten_count": s.analisten_count, "piotroski_score": s.piotroski_score,
                    "piotroski_detail": s.piotroski_label, "grafiek": yahoo_link(s.ticker),
                },
            )

        bericht = format_bericht(ex_name, signalen, alle)
        if bericht:
            send_telegram(bericht, parse_mode="Markdown")
            email_delen.append(bericht)
            print(f"  → Telegram verstuurd")
        else:
            print(f"  → Overgeslagen: {ex_name}")

    if email_delen:
        send_email(f"Kasstroom Onderwaardering rapport {today_str()}", "\n\n" + ("=" * 40 + "\n\n").join(email_delen))

    print(f"\n{'='*60}\nKlaar.")

def run_backtest():
    print("Backtest wordt niet ondersteund voor bot_01kasstr.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
