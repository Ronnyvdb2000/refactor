#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00greenblatt.py — JOEL GREENBLATT "MAGIC FORMULA" v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Globale ranking op ROC en Earnings Yield, met deduplicatie en
notificaties via de centrale modules.
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

UITGESLOTEN_SECTOREN = {
    "Financial Services", "Financials", "Insurance",
    "Utilities", "Utilities—Regulated Electric", "Utilities—Regulated Gas",
}

GREENBLATT_CFG = {
    "marktkap_min":   50_000_000.0,
    "top_n_global":   30,
    "ratio_plafond":  3.0,
    "telegram_chunk": 15,
    "strategie":      "bot_00greenblatt",
    "throttle_sec":   0.15,
}

# ============================================================
# DATA & ROW HELPERS
# ============================================================

def _row(df, names: List[str]):
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None

# ============================================================
# MAGIC FORMULA — EBIT, ROC, EARNINGS YIELD
# ============================================================

@dataclass
class GreenblattSignaal:
    ticker:            str
    exchange:          str
    price:             float
    sector:            str
    ebit:              float
    invested_capital:  float
    roc:               float
    enterprise_value:  float
    earnings_yield:    float
    roc_rank:          int = 0
    ey_rank:           int = 0
    combined_rank:     int = 0

def analyse_ticker(ticker: str, exchange: str, cfg: dict) -> Optional[GreenblattSignaal]:
    try:
        tk   = yf.Ticker(ticker)
        info = tk.info or {}

        price = safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
        if math.isnan(price) or price <= 0:
            return None

        sector = info.get("sector") or "Onbekend"
        if sector in UITGESLOTEN_SECTOREN:
            return None

        market_cap = safe_float(info.get("marketCap"))
        if math.isnan(market_cap) or market_cap < cfg["marktkap_min"]:
            return None

        try:
            inc = tk.financials
            bs  = tk.balance_sheet
        except Exception:
            return None
        if inc is None or inc.empty or bs is None or bs.empty:
            return None

        ebit_row = _row(inc, ["EBIT", "Operating Income"])
        if ebit_row is None or len(ebit_row) == 0:
            return None
        ebit = safe_float(ebit_row.iloc[0])
        if math.isnan(ebit) or ebit <= 0:
            return None

        ca_row  = _row(bs, ["Current Assets", "Total Current Assets"])
        cl_row  = _row(bs, ["Current Liabilities", "Total Current Liabilities"])
        ppe_row = _row(bs, ["Net PPE", "Property Plant And Equipment Net", "Net Tangible Assets"])
        if ca_row is None or cl_row is None or ppe_row is None:
            return None
        ca  = safe_float(ca_row.iloc[0])
        cl  = safe_float(cl_row.iloc[0])
        ppe = safe_float(ppe_row.iloc[0])
        if any(math.isnan(v) for v in (ca, cl, ppe)):
            return None

        nwc = ca - cl
        invested_capital = nwc + ppe
        if invested_capital <= 0:
            return None

        roc = ebit / invested_capital
        if abs(roc) > cfg["ratio_plafond"]:
            return None

        ev = safe_float(info.get("enterpriseValue"))
        if math.isnan(ev) or ev <= 0:
            total_debt = safe_float(info.get("totalDebt"), 0.0)
            total_cash = safe_float(info.get("totalCash"), 0.0)
            ev = market_cap + total_debt - total_cash
        if math.isnan(ev) or ev <= 0:
            return None

        earnings_yield = ebit / ev
        if abs(earnings_yield) > cfg["ratio_plafond"]:
            return None

        return GreenblattSignaal(
            ticker=ticker, exchange=exchange, price=round(price, 2), sector=sector,
            ebit=round(ebit, 0), invested_capital=round(invested_capital, 0),
            roc=round(roc, 4), enterprise_value=round(ev, 0),
            earnings_yield=round(earnings_yield, 4),
        )
    except Exception as e:
        print(f"[WARN] {ticker}: fout — {e}")
        return None

def dedupliceer_op_ticker(alle: List[GreenblattSignaal]) -> List[GreenblattSignaal]:
    gezien = set()
    resultaat = []
    for s in alle:
        if s.ticker in gezien:
            continue
        gezien.add(s.ticker)
        resultaat.append(s)
    return resultaat

def rangschik_globaal(alle: List[GreenblattSignaal]) -> List[GreenblattSignaal]:
    op_roc = sorted(alle, key=lambda s: s.roc, reverse=True)
    for i, s in enumerate(op_roc, start=1):
        s.roc_rank = i

    op_ey = sorted(alle, key=lambda s: s.earnings_yield, reverse=True)
    for i, s in enumerate(op_ey, start=1):
        s.ey_rank = i

    for s in alle:
        s.combined_rank = s.roc_rank + s.ey_rank

    return sorted(alle, key=lambda s: s.combined_rank)

# ============================================================
# OUTPUT
# ============================================================

def _sig_regel(s: GreenblattSignaal, rank: int) -> str:
    return (
        f"{rank}. `{s.ticker}` ({s.exchange}) | ROC:{s.roc*100:.1f}% (#{s.roc_rank}) | "
        f"EY:{s.earnings_yield*100:.1f}% (#{s.ey_rank}) | som:{s.combined_rank} | "
        f"EUR{s.price:.2f} | {yahoo_link(s.ticker)}"
    )

def bouw_telegram_berichten(top: List[GreenblattSignaal], universum_grootte: int, cfg: dict) -> List[str]:
    nu = today_str()
    chunk = cfg["telegram_chunk"]
    blokken = [top[i:i + chunk] for i in range(0, len(top), chunk)]
    berichten = []
    for idx, blok in enumerate(blokken, start=1):
        regels = [
            f"🧙 *MAGIC FORMULA (Greenblatt) — GLOBALE TOP {len(top)}*  ({idx}/{len(blokken)})",
            f"_{nu} | {universum_grootte} tickers gescreend over alle beurzen_",
            "─────────────────────────────",
        ]
        start_rank = (idx - 1) * chunk + 1
        for offset, s in enumerate(blok):
            regels.append(_sig_regel(s, start_rank + offset))
        if idx == len(blokken):
            regels.append("─────────────────────────────")
            regels.append(
                "⚙️ _Rang = optelsom van ROC-rang + Earnings Yield-rang binnen het volledige "
                "universum (laagste som = beste). Uitgesloten: financiële sector, nutsbedrijven, "
                f"marketCap < {cfg['marktkap_min']/1e6:.0f}M, EBIT<=0 of Invested Capital<=0, "
                f"|ROC| of |EY| > {cfg['ratio_plafond']*100:.0f}% (databug-filter)._"
            )
        berichten.append("\n".join(regels))
    return berichten

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    cfg = GREENBLATT_CFG
    print(f"{'='*60}")
    print(f"GREENBLATT MAGIC FORMULA — LIVE  {today_str()}")
    print(f"{'='*60}")

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

    alle: List[GreenblattSignaal] = []
    universum_grootte = 0

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")
        gevonden_in_beurs = 0
        for ticker in tlist:
            universum_grootte += 1
            sig = analyse_ticker(ticker, ex_name, cfg)
            if sig is not None:
                alle.append(sig)
                gevonden_in_beurs += 1
            time.sleep(cfg["throttle_sec"])
        print(f"  → {gevonden_in_beurs}/{len(tlist)} tickers voldoen aan de basisfilters (sector/marktkap/EBIT/IC)")

    if not alle:
        print("[ERROR] Geen enkele ticker voldoet aan de Magic Formula-basisfilters.")
        return

    aantal_voor_dedup = len(alle)
    alle = dedupliceer_op_ticker(alle)
    if len(alle) < aantal_voor_dedup:
        print(f"Deduplicatie: {aantal_voor_dedup - len(alle)} dubbele ticker(s) verwijderd "
              f"(overlap tussen beursbestanden) — {len(alle)} unieke tickers over.")

    gerangschikt = rangschik_globaal(alle)
    top = gerangschikt[: cfg["top_n_global"]]

    print(f"\nGlobale ranking klaar: {len(alle)} gekwalificeerde tickers over {len(exchange_tickers)} beurzen")
    print(f"Top {len(top)} (laagste combined_rank):")
    for rank, s in enumerate(top, start=1):
        print(f"  {rank}. {s.ticker} ({s.exchange}) ROC={s.roc*100:.1f}% EY={s.earnings_yield*100:.1f}% som={s.combined_rank}")

    for rank, s in enumerate(top, start=1):
        log_selectie(
            ticker=s.ticker,
            datum=today_str(),
            strategie=cfg["strategie"],
            beurs=s.exchange,
            koers=s.price,
            parameters={
                "rank": rank,
                "roc": s.roc,
                "earnings_yield": s.earnings_yield,
                "roc_rank": s.roc_rank,
                "ey_rank": s.ey_rank,
                "combined_rank": s.combined_rank,
                "ebit": s.ebit,
                "invested_capital": s.invested_capital,
                "enterprise_value": s.enterprise_value,
                "sector": s.sector,
                "grafiek": yahoo_link(s.ticker),
            },
        )

    berichten = bouw_telegram_berichten(top, universum_grootte, cfg)
    for b in berichten:
        send_telegram(b, parse_mode="Markdown")

    send_email(
        f"Magic Formula (Greenblatt) rapport {today_str()}",
        "\n\n" + ("=" * 40 + "\n\n").join(berichten),
    )

    print(f"\n{'='*60}")
    print("Klaar.")

def run_backtest():
    print("Backtest wordt niet ondersteund voor bot_00greenblatt.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
