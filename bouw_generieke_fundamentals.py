#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bouw_generieke_fundamentals.py — GEDEELDE FUNDAMENTELE PARAMETERS v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Vult de tabel generieke_fundamentals met FCF, Piotroski, P/E, etc.
Bevat rate-limit backoff en caching voor recente data.
"""

import os
import sys
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import yfinance as yf

from common import safe_float, today_str, alle_tickers

# ============================================================
# CONFIG
# ============================================================

SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
MAX_TICKERS_PER_RUN = int(os.environ.get("MAX_TICKERS_PER_RUN", "600"))
RECENTE_DAGEN_LIMIET = int(os.environ.get("RECENTE_DAGEN_LIMIET", "5"))
HERGEBRUIK_DAGEN = int(os.environ.get("HERGEBRUIK_DAGEN", "3"))

RL_DREMPEL = int(os.environ.get("RL_DREMPEL", "5"))
RL_PAUZE_SEC = float(os.environ.get("RL_PAUZE_SEC", "60"))
RL_MAX_PAUZES = int(os.environ.get("RL_MAX_PAUZES", "2"))

FCF_MIN_YEARS = 2

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
    bb = _row(cashflow, ["Repurchase Of Capital Stock", "Common Stock Repurchase", "Repurchase Of Common Stock"])
    total = 0.0
    if div is not None:
        total += abs(safe_float(div.iloc[0], 0.0))
    if bb is not None:
        total += abs(safe_float(bb.iloc[0], 0.0))
    return total

def _net_income_series(financials) -> List[float]:
    row = _row(financials, ["Net Income", "Net Income Common Stockholders"])
    if row is None:
        return []
    vals = [safe_float(v) for v in row.tolist()]
    vals = [v for v in vals if not math.isnan(v)]
    return list(reversed(vals))

def _piotroski_f_score(tk, cashflow) -> int:
    try:
        bs = tk.balance_sheet
        inc = tk.financials
    except Exception:
        return -1

    if bs is None or bs.empty or inc is None or inc.empty:
        return -1
    if bs.shape[1] < 2 or inc.shape[1] < 2:
        return -1

    def val(df, names, col):
        row = _row(df, names)
        if row is None or col >= len(row):
            return float("nan")
        return safe_float(row.iloc[col])

    ta0, ta1 = val(bs, ["Total Assets"], 0), val(bs, ["Total Assets"], 1)
    ca0, ca1 = val(bs, ["Current Assets", "Total Current Assets"], 0), val(bs, ["Current Assets", "Total Current Assets"], 1)
    cl0, cl1 = val(bs, ["Current Liabilities", "Total Current Liabilities"], 0), val(bs, ["Current Liabilities", "Total Current Liabilities"], 1)
    ltd0, ltd1 = val(bs, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], 0), val(bs, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], 1)
    sh0, sh1 = val(bs, ["Share Issued", "Ordinary Shares Number"], 0), val(bs, ["Share Issued", "Ordinary Shares Number"], 1)

    ni0, ni1 = val(inc, ["Net Income"], 0), val(inc, ["Net Income"], 1)
    rev0, rev1 = val(inc, ["Total Revenue"], 0), val(inc, ["Total Revenue"], 1)
    gp0, gp1 = val(inc, ["Gross Profit"], 0), val(inc, ["Gross Profit"], 1)

    ocf_row = _row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities", "Cash Flow From Continuing Operating Activities"])
    ocf0 = safe_float(ocf_row.iloc[0]) if ocf_row is not None and len(ocf_row) > 0 else float("nan")

    vlaggen = {}
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

    return sum(1 for v in vlaggen.values() if v)

# ============================================================
# DATA OPHALEN
# ============================================================

def haal_fundamentals_op(ticker: str) -> Tuple[Optional[dict], str]:
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
    except Exception as e:
        if "Too Many Requests" in str(e) or "Rate limited" in str(e):
            return None, "rate_limit"
        return None, "fout"

    market_cap = safe_float(info.get("marketCap"))
    if math.isnan(market_cap) or market_cap <= 0:
        return None, "geen_data"

    try:
        cashflow = tk.cashflow
    except Exception:
        cashflow = None
    try:
        financials = tk.financials
    except Exception:
        financials = None

    fcf_series = _fcf_series(cashflow)
    if len(fcf_series) >= FCF_MIN_YEARS:
        fcf_now = fcf_series[-1]
        fcf_yield = (fcf_now / market_cap) * 100 if market_cap > 0 else float("nan")
        if not math.isnan(fcf_yield) and abs(fcf_yield) > 100:
            fcf_yield = float("nan")
        fcf_growing = fcf_series[-1] > fcf_series[0]
        fcf_consistent = all(v > 0 for v in fcf_series)
        sh_return = _shareholder_return(cashflow)
        payout_pct = (sh_return / fcf_now * 100) if fcf_now > 0 else float("nan")
    else:
        fcf_yield = float("nan")
        fcf_growing = None
        fcf_consistent = None
        payout_pct = float("nan")

    total_debt = safe_float(info.get("totalDebt"), 0.0)
    total_cash = safe_float(info.get("totalCash"), 0.0)
    ebitda = safe_float(info.get("ebitda"))
    net_debt = total_debt - total_cash
    net_debt_ebitda = net_debt / ebitda if not math.isnan(ebitda) and ebitda > 0 else float("nan")

    ni_series = _net_income_series(financials)
    eps_growth_pct = float("nan")
    eps_cagr_pct = float("nan")
    pe_ratio = safe_float(info.get("trailingPE"))
    if len(ni_series) >= 2:
        oudste, nieuwste = ni_series[0], ni_series[-1]
        if oudste > 0:
            eps_growth_pct = (nieuwste - oudste) / oudste * 100
        if oudste > 0 and nieuwste > 0:
            eps_cagr_pct = ((nieuwste / oudste) ** (1 / (len(ni_series) - 1)) - 1) * 100
    peg_ratio = float("nan")
    if not math.isnan(pe_ratio) and pe_ratio > 0 and not math.isnan(eps_cagr_pct) and eps_cagr_pct > 0:
        peg_ratio = pe_ratio / eps_cagr_pct

    piotroski = _piotroski_f_score(tk, cashflow)
    revenue_growth_raw = safe_float(info.get("revenueGrowth"))

    def rond(v, decimalen=4):
        try:
            f = float(v)
            return None if math.isnan(f) else round(f, decimalen)
        except Exception:
            return None

    return {
        "market_cap": rond(market_cap, 0),
        "trailing_pe": rond(pe_ratio, 2),
        "price_to_book": rond(info.get("priceToBook"), 2),
        "dividend_yield": rond(info.get("dividendYield"), 2),
        "current_ratio": rond(info.get("currentRatio"), 2),
        "revenue_growth_pct": rond(revenue_growth_raw * 100, 2) if not math.isnan(revenue_growth_raw) else None,
        "fcf_yield": rond(fcf_yield, 2),
        "fcf_years": len(fcf_series) if fcf_series else None,
        "fcf_growing": fcf_growing,
        "fcf_consistent": fcf_consistent,
        "net_debt_ebitda": rond(net_debt_ebitda, 2),
        "payout_pct": rond(payout_pct, 1),
        "analisten_count": rond(info.get("numberOfAnalystOpinions"), 0),
        "piotroski_score": piotroski if piotroski >= 0 else None,
        "eps_growth_pct": rond(eps_growth_pct, 1),
        "eps_cagr_pct": rond(eps_cagr_pct, 1),
        "peg_ratio": rond(peg_ratio, 2),
    }, "ok"

# ============================================================
# DATABASE
# ============================================================

def haal_openstaande_paren(conn) -> List[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENTE_DAGEN_LIMIET)).strftime("%Y-%m-%d")
    query = """
        SELECT DISTINCT s.ticker, s.datum
        FROM selecties s
        LEFT JOIN generieke_fundamentals gf ON s.ticker = gf.ticker AND s.datum = gf.datum
        WHERE gf.ticker IS NULL
          AND s.ticker NOT LIKE '%%/%%'
          AND s.datum::date >= %(cutoff)s
        ORDER BY s.ticker, s.datum;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, {"cutoff": cutoff})
        return cur.fetchall()

def haal_recente_rijen(conn, tickers: List[str]) -> Dict[str, dict]:
    if not tickers:
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HERGEBRUIK_DAGEN)).strftime("%Y-%m-%d")
    query = """
        SELECT DISTINCT ON (ticker) * FROM generieke_fundamentals
        WHERE ticker = ANY(%(tickers)s) AND datum::date >= %(cutoff)s
        ORDER BY ticker, datum DESC;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, {"tickers": tickers, "cutoff": cutoff})
        return {r["ticker"]: dict(r) for r in cur.fetchall()}

KOLOMMEN = [
    "ticker", "datum", "market_cap", "trailing_pe", "price_to_book",
    "dividend_yield", "current_ratio", "revenue_growth_pct", "fcf_yield",
    "fcf_years", "fcf_growing", "fcf_consistent", "net_debt_ebitda",
    "payout_pct", "analisten_count", "piotroski_score", "eps_growth_pct",
    "eps_cagr_pct", "peg_ratio",
]

def upsert_rijen(conn, rijen: List[dict]) -> int:
    if not rijen:
        return 0
    kolom_lijst = ", ".join(KOLOMMEN)
    placeholders = ", ".join(f"%({k})s" for k in KOLOMMEN)
    update_lijst = ", ".join(f"{k} = EXCLUDED.{k}" for k in KOLOMMEN if k not in ("ticker", "datum"))
    update_lijst += ", bijgewerkt_op = now()"

    query = f"INSERT INTO generieke_fundamentals ({kolom_lijst}) VALUES ({placeholders}) ON CONFLICT (ticker, datum) DO UPDATE SET {update_lijst};"
    with conn.cursor() as cur:
        for rij in rijen:
            cur.execute(query, {k: rij.get(k) for k in KOLOMMEN})
    conn.commit()
    return len(rijen)

# ============================================================
# MAIN
# ============================================================

def run_build():
    if not SUPABASE_DB_URL:
        print("FOUT: SUPABASE_DB_URL ontbreekt.", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(SUPABASE_DB_URL)
    try:
        open_paren = haal_openstaande_paren(conn)
        print(f"{len(open_paren)} recente (ticker, datum)-paren nog niet aanwezig (venster: laatste {RECENTE_DAGEN_LIMIET} dagen).")

        if not open_paren:
            print("Niets te doen.")
            return

        per_ticker: Dict[str, List[str]] = {}
        for r in open_paren:
            per_ticker.setdefault(r["ticker"], []).append(r["datum"])

        volgorde = sorted(per_ticker.keys(), key=lambda t: (min(per_ticker[t]), t))

        recent = haal_recente_rijen(conn, volgorde)
        hergebruikte_rijen = 0
        hergebruikte_tickers = 0
        te_fetchen: List[str] = []
        for t in volgorde:
            if t in recent:
                basis = {k: recent[t].get(k) for k in KOLOMMEN if k not in ("ticker", "datum")}
                rijen = [{"ticker": t, "datum": d, **basis} for d in per_ticker[t]]
                hergebruikte_rijen += upsert_rijen(conn, rijen)
                hergebruikte_tickers += 1
            else:
                te_fetchen.append(t)

        tickers = te_fetchen[:MAX_TICKERS_PER_RUN]
        print(f"{hergebruikte_tickers} tickers hergebruikt van een recente rij, {len(tickers)} tickers worden nu echt opgehaald ({len(te_fetchen) - len(tickers)} blijven over).")

        opgehaald_ok = 0
        totaal_bijgewerkt = hergebruikte_rijen
        geen_data = 0
        fouten = 0
        rate_limit_fouten = 0
        rl_achtereen = 0
        pauzes = 0
        vroegtijdig_gestopt = False
        geprobeerd = 0

        for i, ticker in enumerate(tickers, start=1):
            fundamentals, reden = haal_fundamentals_op(ticker)
            geprobeerd += 1

            if reden == "ok":
                rijen = [{"ticker": ticker, "datum": d, **fundamentals} for d in per_ticker[ticker]]
                totaal_bijgewerkt += upsert_rijen(conn, rijen)
                opgehaald_ok += 1
                rl_achtereen = 0
            elif reden == "rate_limit":
                rate_limit_fouten += 1
                rl_achtereen += 1
            elif reden == "geen_data":
                geen_data += 1
                rl_achtereen = 0
            else:
                fouten += 1
                rl_achtereen = 0

            if rl_achtereen >= RL_DREMPEL:
                if pauzes >= RL_MAX_PAUZES:
                    print(f"[RATE LIMIT] Pauzebudget uitgeput na {rate_limit_fouten} fouten -- run stopt.")
                    vroegtijdig_gestopt = True
                    break
                pauzes += 1
                print(f"[RATE LIMIT] {rl_achtereen} fouten (laatste: {ticker}) -- pauze {RL_PAUZE_SEC:.0f}s ({pauzes}/{RL_MAX_PAUZES})")
                time.sleep(RL_PAUZE_SEC)
                rl_achtereen = 0

            if i % 25 == 0 or i == len(tickers):
                print(f"  {i}/{len(tickers)} tickers geprobeerd...")
            time.sleep(0.15)

        mislukt = geen_data + fouten + rate_limit_fouten
        nog_te_gaan = len(te_fetchen) - geprobeerd

        print("\n" + "=" * 60)
        print(f"{totaal_bijgewerkt} rijen bijgewerkt.")
        print(f"{opgehaald_ok} tickers opgehaald, {hergebruikte_tickers} hergebruikt.")
        print(f"{mislukt} mislukt (geen data {geen_data}, rate limit {rate_limit_fouten}, overig {fouten}).")
        if nog_te_gaan > 0:
            print(f"{nog_te_gaan} tickers nog te gaan.")
        if vroegtijdig_gestopt:
            print("⚠️ Vroegtijdig gestopt wegens Yahoo rate limiting.")
        print("=" * 60)

    finally:
        conn.close()

if __name__ == "__main__":
    run_build()
