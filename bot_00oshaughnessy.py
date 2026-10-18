#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00oshaughnessy.py — O'SHAUGHNESSY "TRENDING VALUE" v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Berekent de Value Composite Two (VC2) en rangschikt op momentum.
"""

import sys
import math
import time
from dataclasses import dataclass
from typing import List, Dict, Optional

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

# ============================================================
# CONFIG
# ============================================================

OSHAUGHNESSY_CFG = {
    "marktkap_min":         200_000_000.0,
    "min_vc2_ratios":       5,
    "value_percentile_min": 90.0,
    "momentum_maanden":     6,
    "top_n_global":         25,
    "telegram_chunk":       15,
    "strategie":            "bot_00oshaughnessy",
    "throttle_sec":         0.15,
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

# ============================================================
# STAP 1A — RUWE RATIO'S
# ============================================================

@dataclass
class RuweSignaal:
    ticker:            str
    exchange:          str
    price:             float
    pe:                float
    pb:                float
    ps:                float
    pcf:               float
    ev_ebitda:         float
    ev_fcf:            float
    shareholder_yield: float

def analyse_ticker_ruw(ticker: str, exchange: str, cfg: dict) -> Optional[RuweSignaal]:
    try:
        tk   = yf.Ticker(ticker)
        info = tk.info or {}

        price = safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
        if math.isnan(price) or price <= 0:
            return None

        market_cap = safe_float(info.get("marketCap"))
        if math.isnan(market_cap) or market_cap < cfg["marktkap_min"]:
            return None

        pe = safe_float(info.get("trailingPE"))
        pb = safe_float(info.get("priceToBook"))
        ps = safe_float(info.get("priceToSalesTrailing12Months"))

        ev = safe_float(info.get("enterpriseValue"))
        if math.isnan(ev) or ev <= 0:
            total_debt = safe_float(info.get("totalDebt"), 0.0)
            total_cash = safe_float(info.get("totalCash"), 0.0)
            ev = market_cap + total_debt - total_cash

        try:
            inc = tk.financials
            bs  = tk.balance_sheet
            cf  = tk.cashflow
        except Exception:
            inc, bs, cf = None, None, None

        ev_ebitda = safe_float(info.get("enterpriseToEbitda"))
        if math.isnan(ev_ebitda) or ev_ebitda <= 0:
            ebitda_row = _row(inc, ["EBITDA"]) if inc is not None else None
            ebitda = safe_float(ebitda_row.iloc[0]) if ebitda_row is not None and len(ebitda_row) > 0 else float("nan")
            if math.isnan(ebitda):
                ebit_row = _row(inc, ["EBIT", "Operating Income"]) if inc is not None else None
                da_row   = _row(cf, ["Depreciation And Amortization", "Depreciation Amortization Depletion"]) if cf is not None else None
                ebit = safe_float(ebit_row.iloc[0]) if ebit_row is not None and len(ebit_row) > 0 else float("nan")
                da   = safe_float(da_row.iloc[0]) if da_row is not None and len(da_row) > 0 else float("nan")
                ebitda = ebit + da if not math.isnan(ebit) and not math.isnan(da) else float("nan")
            ev_ebitda = ev / ebitda if not math.isnan(ebitda) and ebitda > 0 and not math.isnan(ev) and ev > 0 else float("nan")

        ocf_row   = _row(cf, ["Operating Cash Flow", "Total Cash From Operating Activities", "Cash Flow From Continuing Operating Activities"]) if cf is not None else None
        capex_row = _row(cf, ["Capital Expenditure", "Capital Expenditures"]) if cf is not None else None
        ocf   = safe_float(ocf_row.iloc[0]) if ocf_row is not None and len(ocf_row) > 0 else float("nan")
        capex = safe_float(capex_row.iloc[0]) if capex_row is not None and len(capex_row) > 0 else float("nan")
        if not math.isnan(ocf) and not math.isnan(capex):
            fcf = ocf - abs(capex)
            ev_fcf = ev / fcf if fcf > 0 and not math.isnan(ev) and ev > 0 else float("nan")
        else:
            ev_fcf = float("nan")

        pcf = market_cap / ocf if not math.isnan(ocf) and ocf > 0 and not math.isnan(market_cap) else float("nan")

        if math.isnan(ps) or ps <= 0:
            rev_row = _row(inc, ["Total Revenue"]) if inc is not None else None
            rev = safe_float(rev_row.iloc[0]) if rev_row is not None and len(rev_row) > 0 else float("nan")
            ps = market_cap / rev if not math.isnan(rev) and rev > 0 else float("nan")

        div_yield = safe_float(info.get("dividendYield"), 0.0)
        sh_row = _row(bs, ["Share Issued", "Ordinary Shares Number"]) if bs is not None else None
        if sh_row is not None and len(sh_row) >= 2:
            sh_nu, sh_vorig = safe_float(sh_row.iloc[0]), safe_float(sh_row.iloc[1])
            buyback_yield = (sh_vorig - sh_nu) / sh_vorig * 100 if sh_vorig > 0 else 0.0
        else:
            buyback_yield = 0.0
        shareholder_yield = div_yield + buyback_yield

        return RuweSignaal(
            ticker=ticker, exchange=exchange, price=round(price, 2),
            pe=pe if not math.isnan(pe) and pe > 0 else float("nan"),
            pb=pb if not math.isnan(pb) and pb > 0 else float("nan"),
            ps=ps if not math.isnan(ps) and ps > 0 else float("nan"),
            pcf=pcf if not math.isnan(pcf) and pcf > 0 else float("nan"),
            ev_ebitda=ev_ebitda if not math.isnan(ev_ebitda) and ev_ebitda > 0 else float("nan"),
            ev_fcf=ev_fcf if not math.isnan(ev_fcf) and ev_fcf > 0 else float("nan"),
            shareholder_yield=round(shareholder_yield, 2),
        )
    except Exception as e:
        print(f"[WARN] {ticker}: fout — {e}")
        return None

# ============================================================
# STAP 1B — VC2 PERCENTIELEN
# ============================================================

def dedupliceer_op_ticker(alle: List[RuweSignaal]) -> List[RuweSignaal]:
    gezien = set()
    resultaat = []
    for s in alle:
        if s.ticker in gezien:
            continue
        gezien.add(s.ticker)
        resultaat.append(s)
    return resultaat

def bereken_vc2(alle: List[RuweSignaal], cfg: dict) -> pd.DataFrame:
    df = pd.DataFrame([{
        "ticker": s.ticker, "exchange": s.exchange, "price": s.price,
        "pe": s.pe, "pb": s.pb, "ps": s.ps, "pcf": s.pcf, "ev_ebitda": s.ev_ebitda,
        "ev_fcf": s.ev_fcf, "shareholder_yield": s.shareholder_yield,
    } for s in alle])

    goedkoop_lager_is_beter = ["pe", "pb", "ps", "pcf", "ev_ebitda", "ev_fcf"]
    for kol in goedkoop_lager_is_beter:
        df[f"pct_{kol}"] = df[kol].rank(pct=True, ascending=False) * 100
    df["pct_shareholder_yield"] = df["shareholder_yield"].rank(pct=True, ascending=True) * 100

    pct_kolommen = [f"pct_{k}" for k in goedkoop_lager_is_beter] + ["pct_shareholder_yield"]
    df["vc2_geldige_ratios"] = df[pct_kolommen].notna().sum(axis=1)
    df["vc2_score"] = df[pct_kolommen].mean(axis=1, skipna=True)

    df = df[df["vc2_geldige_ratios"] >= cfg["min_vc2_ratios"]].copy()
    return df

# ============================================================
# STAP 2 — MOMENTUM
# ============================================================

def bereken_momentum_pct(ticker: str, maanden: int) -> float:
    try:
        hist = yf.Ticker(ticker).history(period=f"{maanden}mo")
        if hist is None or hist.empty or len(hist) < 2:
            return float("nan")
        start, eind = safe_float(hist["Close"].iloc[0]), safe_float(hist["Close"].iloc[-1])
        if math.isnan(start) or start <= 0:
            return float("nan")
        return (eind - start) / start * 100
    except Exception:
        return float("nan")

@dataclass
class TrendingValueSignaal:
    ticker:            str
    exchange:          str
    price:             float
    pe:                float
    pb:                float
    ps:                float
    pcf:               float
    ev_ebitda:         float
    ev_fcf:            float
    shareholder_yield: float
    vc2_score:         float
    momentum_pct:      float

# ============================================================
# OUTPUT
# ============================================================

def _sig_regel(s: TrendingValueSignaal, rank: int) -> str:
    def fmt(v):
        return f"{v:.1f}" if not math.isnan(v) else "n/b"
    return (
        f"{rank}. `{s.ticker}` ({s.exchange}) | VC2:{s.vc2_score:.0f} | Mom6m:{s.momentum_pct:+.1f}% | "
        f"PE:{fmt(s.pe)} PB:{fmt(s.pb)} PS:{fmt(s.ps)} PCF:{fmt(s.pcf)} EV/EBITDA:{fmt(s.ev_ebitda)} EV/FCF:{fmt(s.ev_fcf)} "
        f"SY:{s.shareholder_yield:.1f}% | EUR{s.price:.2f} | {yahoo_link(s.ticker)}"
    )

def bouw_telegram_berichten(top: List[TrendingValueSignaal], universum_grootte: int, waarde_decile_grootte: int, cfg: dict) -> List[str]:
    nu = today_str()
    chunk = cfg["telegram_chunk"]
    blokken = [top[i:i + chunk] for i in range(0, len(top), chunk)]
    berichten = []
    for idx, blok in enumerate(blokken, start=1):
        regels = [
            f"📈 *TRENDING VALUE (O'Shaughnessy) — GLOBALE TOP {len(top)}*  ({idx}/{len(blokken)})",
            f"_{nu} | {universum_grootte} tickers gescreend | {waarde_decile_grootte} in goedkoopste VC2-decile_",
            "─────────────────────────────",
        ]
        start_rank = (idx - 1) * chunk + 1
        for offset, s in enumerate(blok):
            regels.append(_sig_regel(s, start_rank + offset))
        if idx == len(blokken):
            regels.append("─────────────────────────────")
            regels.append(
                "⚙️ _VC2 = gemiddeld percentiel over P/E, P/B, P/S, P/CF, EV/EBITDA, EV/FCF (lager=beter) "
                "en Shareholder Yield (hoger=beter), 100=beste. Enkel de goedkoopste "
                f"{100 - cfg['value_percentile_min']:.0f}% VAN DEZE RUN (relatieve rang, geen absolute "
                f"VC2-drempel) gaat door naar de {cfg['momentum_maanden']}-maands momentumranking. "
                f"marketCap>={cfg['marktkap_min']/1e6:.0f}M._"
            )
        berichten.append("\n".join(regels))
    return berichten

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    cfg = OSHAUGHNESSY_CFG
    print(f"{'='*60}\nO'SHAUGHNESSY TRENDING VALUE — LIVE  {today_str()}\n{'='*60}")

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

    alle_ruw: List[RuweSignaal] = []
    universum_grootte = 0
    for ex_name, tlist in exchange_tickers.items():
        print(f"\nStap 1 — ratio's ophalen: {ex_name} ({len(tlist)} tickers)...")
        gevonden = 0
        for ticker in tlist:
            universum_grootte += 1
            sig = analyse_ticker_ruw(ticker, ex_name, cfg)
            if sig is not None:
                alle_ruw.append(sig)
                gevonden += 1
            time.sleep(cfg["throttle_sec"])
        print(f"  → {gevonden}/{len(tlist)} tickers met marketCap/prijsdata OK")

    if not alle_ruw:
        print("[ERROR] Geen enkele ticker doorstond de basisfilters.")
        return

    aantal_voor_dedup = len(alle_ruw)
    alle_ruw = dedupliceer_op_ticker(alle_ruw)
    if len(alle_ruw) < aantal_voor_dedup:
        print(f"Deduplicatie: {aantal_voor_dedup - len(alle_ruw)} dubbele ticker(s) verwijderd.")

    df = bereken_vc2(alle_ruw, cfg)
    print(f"\nVC2 berekend voor {len(df)}/{len(alle_ruw)} tickers (>= {cfg['min_vc2_ratios']}/7 ratio's geldig)")

    drempel = df["vc2_score"].quantile(cfg["value_percentile_min"] / 100.0)
    waarde_decile = df[df["vc2_score"] >= drempel].copy()
    if waarde_decile.empty:
        waarde_decile = df.sort_values("vc2_score", ascending=False).head(max(int(len(df) * 0.1), cfg["top_n_global"]))

    print(f"Goedkoopste VC2-decile: {len(waarde_decile)} tickers → momentum ophalen...")

    trending: List[TrendingValueSignaal] = []
    for _, rij in waarde_decile.iterrows():
        mom = bereken_momentum_pct(rij["ticker"], cfg["momentum_maanden"])
        if math.isnan(mom):
            continue
        trending.append(TrendingValueSignaal(
            ticker=rij["ticker"], exchange=rij["exchange"], price=rij["price"],
            pe=rij["pe"], pb=rij["pb"], ps=rij["ps"], pcf=rij["pcf"],
            ev_ebitda=rij["ev_ebitda"], ev_fcf=rij["ev_fcf"],
            shareholder_yield=rij["shareholder_yield"], vc2_score=round(rij["vc2_score"], 1),
            momentum_pct=round(mom, 1),
        ))
        time.sleep(cfg["throttle_sec"])

    if not trending:
        print("[ERROR] Geen enkele ticker in de VC2-decile had bruikbare koershistoriek voor momentum.")
        return

    trending.sort(key=lambda s: s.momentum_pct, reverse=True)
    top = trending[: cfg["top_n_global"]]

    print(f"\nTop {len(top)} (hoogste {cfg['momentum_maanden']}-maands momentum binnen goedkoopste VC2-decile):")
    for rank, s in enumerate(top, start=1):
        print(f"  {rank}. {s.ticker} ({s.exchange}) VC2={s.vc2_score:.0f} Mom={s.momentum_pct:+.1f}%")

    for rank, s in enumerate(top, start=1):
        log_selectie(
            ticker=s.ticker, datum=today_str(), strategie=cfg["strategie"], beurs=s.exchange, koers=s.price,
            parameters={
                "rank": rank, "vc2_score": s.vc2_score, "momentum_6m_pct": s.momentum_pct,
                "pe_ratio": None if math.isnan(s.pe) else s.pe,
                "pb_ratio": None if math.isnan(s.pb) else s.pb,
                "ps_ratio": None if math.isnan(s.ps) else s.ps,
                "pcf_ratio": None if math.isnan(s.pcf) else s.pcf,
                "ev_ebitda": None if math.isnan(s.ev_ebitda) else s.ev_ebitda,
                "ev_fcf": None if math.isnan(s.ev_fcf) else s.ev_fcf,
                "shareholder_yield": s.shareholder_yield, "grafiek": yahoo_link(s.ticker),
            },
        )

    berichten = bouw_telegram_berichten(top, universum_grootte, len(waarde_decile), cfg)
    for b in berichten:
        send_telegram(b, parse_mode="Markdown")

    send_email(f"Trending Value (O'Shaughnessy) rapport {today_str()}", "\n\n" + ("=" * 40 + "\n\n").join(berichten))

    print(f"\n{'='*60}\nKlaar.")

def run_backtest():
    print("Backtest wordt niet ondersteund voor bot_00oshaughnessy.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
