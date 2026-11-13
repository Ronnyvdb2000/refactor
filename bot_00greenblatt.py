#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00greenblatt.py — JOEL GREENBLATT "MAGIC FORMULA" v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Globale ranking op ROC en Earnings Yield, met deduplicatie en
notificaties via de centrale modules.
"""

import sys
import os
import math
import time
from collections import Counter
from dataclasses import dataclass
from typing import List, Dict, Optional

import yfinance as yf
import pandas as pd

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
    "max_fiscal_age_days": 550,
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
    price_currency:    str = "Onbekend"
    financial_currency: str = "Onbekend"
    fiscal_date:       str = ""
    market_cap_usd:    float = 0.0


class _SkipTicker(ValueError):
    def __init__(self, reason, category="data"):
        super().__init__(reason)
        self.category = category


def _money_currency(currency):
    # Yahoo noteert sommige aandelenkoersen in centen/pence, maar de totale
    # marktkapitalisatie en EV in de bijbehorende hoofdvaluta.
    return {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}.get(currency, currency)


def _usd_rate(currency, cache):
    if currency == "USD":
        return 1.0
    if currency not in cache:
        cache[currency] = None
        try:
            hist = yf.Ticker(f"{currency}USD=X").history(period="1mo", auto_adjust=False)
            close = pd.to_numeric(hist["Close"], errors="coerce")
            close = close.loc[close.map(lambda value: math.isfinite(value) and value > 0)]
            if not close.empty:
                last_date = pd.Timestamp(close.index[-1]).tz_localize(None).normalize()
                age = (pd.Timestamp(today_str()) - last_date).days
                if 0 <= age <= 7:
                    cache[currency] = float(close.iloc[-1])
        except Exception:
            pass
    if cache[currency] is None:
        raise _SkipTicker(f"geen recente wisselkoers {currency}/USD")
    return cache[currency]


def _statement_value(frame, names, fiscal_date):
    for name in names:
        if name in frame.index:
            value = safe_float(frame.loc[name, fiscal_date])
            if math.isfinite(value):
                return value
    raise _SkipTicker(f"ontbrekende balans/resultaatpost: {' / '.join(names)}")

def analyse_ticker(ticker: str, exchange: str, cfg: dict,
                   diagnostics=None, fx_cache=None) -> Optional[GreenblattSignaal]:
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        price = safe_float(info.get("currentPrice"))
        if not math.isfinite(price) or price <= 0:
            price = safe_float(info.get("regularMarketPrice"))
        if not math.isfinite(price) or price <= 0:
            raise _SkipTicker("geen geldige koers")

        sector = info.get("sector") or "Onbekend"
        sector_norm = str(sector).casefold()
        industry_norm = str(info.get("industry") or "").casefold()
        if (sector in UITGESLOTEN_SECTOREN or "utilit" in sector_norm
                or "financial" in sector_norm or "insurance" in sector_norm
                or "bank" in industry_norm):
            raise _SkipTicker("uitgesloten financiële sector/nutsbedrijf", "filter")
        if sector == "Onbekend":
            raise _SkipTicker("sector ontbreekt; uitsluitingsfilter niet controleerbaar")

        quote_currency = info.get("currency")
        financial_currency = info.get("financialCurrency")
        if not quote_currency or not financial_currency:
            raise _SkipTicker("koers- of verslagvaluta ontbreekt")
        valuation_currency = _money_currency(quote_currency)
        if valuation_currency != financial_currency:
            raise _SkipTicker(f"valutaverschil {valuation_currency}/{financial_currency}; EV en EBIT niet eenduidig vergelijkbaar")
        rates = fx_cache if fx_cache is not None else {}
        valuation_usd = _usd_rate(valuation_currency, rates)
        market_cap = safe_float(info.get("marketCap"))
        if not math.isfinite(market_cap) or market_cap <= 0:
            raise _SkipTicker("ongeldige marktkapitalisatie")
        market_cap_usd = market_cap * valuation_usd
        if market_cap_usd < cfg["marktkap_min"]:
            raise _SkipTicker("marktkapitalisatie onder USD-grens", "filter")

        inc, bs = tk.financials, tk.balance_sheet
        if inc is None or inc.empty or bs is None or bs.empty:
            raise _SkipTicker("jaarrekening of balans ontbreekt")
        inc, bs = inc.copy(), bs.copy()
        inc.columns = pd.to_datetime(inc.columns, errors="coerce")
        bs.columns = pd.to_datetime(bs.columns, errors="coerce")
        shared_dates = sorted(set(inc.columns.dropna()).intersection(bs.columns.dropna()), reverse=True)
        if not shared_dates:
            raise _SkipTicker("geen gemeenschappelijk boekjaar voor EBIT en balans")
        fiscal_date = shared_dates[0]
        age = (pd.Timestamp(today_str()) - fiscal_date.tz_localize(None)).days
        if age < 0 or age > cfg.get("max_fiscal_age_days", 550):
            raise _SkipTicker("boekjaar is toekomstig of ouder dan toegestane grens")
        ebit = _statement_value(inc, ["EBIT", "Operating Income"], fiscal_date)
        if ebit <= 0:
            raise _SkipTicker("EBIT niet positief", "filter")

        ca = _statement_value(bs, ["Current Assets", "Total Current Assets"], fiscal_date)
        cl = _statement_value(bs, ["Current Liabilities", "Total Current Liabilities"], fiscal_date)
        ppe = _statement_value(bs, ["Net PPE", "Property Plant And Equipment Net"], fiscal_date)
        if any(value < 0 for value in (ca, cl, ppe)):
            raise _SkipTicker("negatieve balanspost")
        # Bestaande benadering blijft expliciet: CA - CL + Net PPE.
        # Net Tangible Assets is géén synoniem van PPE en wordt niet gebruikt.
        invested_capital = ca - cl + ppe
        if invested_capital <= 0:
            raise _SkipTicker("geïnvesteerd kapitaal niet positief", "filter")
        roc = ebit / invested_capital
        if not math.isfinite(roc) or roc > cfg["ratio_plafond"]:
            raise _SkipTicker("ROC buiten datakwaliteitsgrens")

        ev = safe_float(info.get("enterpriseValue"))
        if not math.isfinite(ev) or ev <= 0:
            debt = safe_float(info.get("totalDebt"))
            cash = safe_float(info.get("totalCash"))
            if not all(math.isfinite(value) and value >= 0 for value in (debt, cash)):
                raise _SkipTicker("EV ontbreekt en schuld/cash zijn onvolledig")
            ev_financial = market_cap + debt - cash
        else:
            ev_financial = ev
        if not math.isfinite(ev_financial) or ev_financial <= 0:
            raise _SkipTicker("geen positieve ondernemingswaarde")
        earnings_yield = ebit / ev_financial
        if not math.isfinite(earnings_yield) or earnings_yield > cfg["ratio_plafond"]:
            raise _SkipTicker("earnings yield buiten datakwaliteitsgrens")

        if diagnostics is not None:
            diagnostics[ticker] = ("ok", "gekwalificeerd")
        return GreenblattSignaal(
            ticker=ticker, exchange=exchange, price=price, sector=sector,
            ebit=ebit, invested_capital=invested_capital, roc=roc,
            enterprise_value=ev_financial, earnings_yield=earnings_yield,
            price_currency=quote_currency, financial_currency=financial_currency,
            fiscal_date=fiscal_date.date().isoformat(), market_cap_usd=market_cap_usd,
        )
    except _SkipTicker as exc:
        if diagnostics is not None:
            diagnostics[ticker] = (exc.category, str(exc))
        print(f"[SKIP:{exc.category}] {ticker}: {exc}")
        return None
    except Exception as exc:
        if diagnostics is not None:
            diagnostics[ticker] = ("error", f"{type(exc).__name__}: {exc}")
        print(f"[WARN] {ticker}: fout — {type(exc).__name__}: {exc}")
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
    for metric, rank_field in (("roc", "roc_rank"), ("earnings_yield", "ey_rank")):
        previous = None
        rank = 0
        for position, sig in enumerate(sorted(alle, key=lambda s: (-getattr(s, metric), s.ticker)), start=1):
            value = getattr(sig, metric)
            if previous is None or value != previous:
                rank = position
            setattr(sig, rank_field, rank)
            previous = value

    for s in alle:
        s.combined_rank = s.roc_rank + s.ey_rank

    return sorted(alle, key=lambda s: (s.combined_rank, -s.roc, -s.earnings_yield, s.ticker))

# ============================================================
# OUTPUT
# ============================================================

def _sig_regel(s: GreenblattSignaal, rank: int) -> str:
    return (
        f"{rank}. `{s.ticker}` ({s.exchange}) | ROC:{s.roc*100:.1f}% (#{s.roc_rank}) | "
        f"EY:{s.earnings_yield*100:.1f}% (#{s.ey_rank}) | som:{s.combined_rank} | "
        f"{s.price_currency} {s.price:.2f} | boekjaar {s.fiscal_date} | {yahoo_link(s.ticker)}"
    )

def bouw_telegram_berichten(top: List[GreenblattSignaal], universum_grootte: int, cfg: dict) -> List[str]:
    nu = today_str()
    chunk = cfg["telegram_chunk"]
    blokken = [top[i:i + chunk] for i in range(0, len(top), chunk)]
    berichten = []
    for idx, blok in enumerate(blokken, start=1):
        regels = [
            f"🧙 *MAGIC FORMULA (Greenblatt) — GLOBALE TOP {len(top)}*  ({idx}/{len(blokken)})",
            f"_{nu} | {universum_grootte} unieke tickers gescreend over alle beurzen_",
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
                f"marketCap < USD {cfg['marktkap_min']/1e6:.0f}M, EBIT<=0 of Invested Capital<=0, "
                f"|ROC| of |EY| > {cfg['ratio_plafond']*100:.0f}% (databug-filter)._"
            )
            regels.append("ROC-benadering: EBIT / (Current Assets - Current Liabilities + Net PPE). Jaarcijfers; actuele EV. Gelijke ratio's krijgen dezelfde rang. Verschillende koers-/verslagvaluta worden overgeslagen.")
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
    missing_files = []
    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist:
            missing_files.append(f_name)
            print(f"Bestand {f_name} niet gevonden of leeg, overslaan.")
            continue
        ex_name = label_voor(f_name)
        exchange_tickers[ex_name] = tlist
        print(f"  {ex_name}: {len(tlist)} tickers")

    if not exchange_tickers:
        raise RuntimeError("Geen ticker bestanden gevonden.")

    alle: List[GreenblattSignaal] = []
    universum_grootte = 0
    analyse_cache: Dict[str, Optional[GreenblattSignaal]] = {}
    diagnostics = {}
    fx_cache = {}

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")
        gevonden_in_beurs = 0
        for ticker in tlist:
            if ticker not in analyse_cache:
                universum_grootte += 1
                analyse_cache[ticker] = analyse_ticker(ticker, ex_name, cfg, diagnostics=diagnostics, fx_cache=fx_cache)
                time.sleep(cfg["throttle_sec"])
                sig = analyse_cache[ticker]
                if sig is not None:
                    alle.append(sig)
            if analyse_cache[ticker] is not None:
                gevonden_in_beurs += 1
        print(f"  → {gevonden_in_beurs}/{len(tlist)} tickers voldoen aan de basisfilters (sector/marktkap/EBIT/IC)")

    counts = Counter(category for category, _ in diagnostics.values())
    dekking = (f"Datadekking: {universum_grootte} uniek gescreend | "
               f"{counts['ok']} gekwalificeerd | {counts['filter']} uitgesloten op criteria | "
               f"{counts['data']} onbruikbare/ontbrekende data | {counts['error']} fouten")
    print(dekking)

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

    db_ok = 0
    for rank, s in enumerate(top, start=1):
        saved = log_selectie(
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
                "price_currency": s.price_currency,
                "financial_currency": s.financial_currency,
                "fiscal_date": s.fiscal_date,
                "market_cap_usd": s.market_cap_usd,
                "grafiek": yahoo_link(s.ticker),
            },
        )
        db_ok += bool(saved)
    print(f"Database: {db_ok}/{len(top)} selecties opgeslagen")

    berichten = bouw_telegram_berichten(top, universum_grootte, cfg)
    if os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        for b in berichten:
            delivered = send_telegram(b, parse_mode="Markdown")
            print("Telegram verstuurd" if delivered else "[WARN] Telegram niet verstuurd")
    else:
        print("Telegram uitgeschakeld (geen configuratie)")

    details = "\n".join(f"{ticker}: {category} — {reason}" for ticker, (category, reason) in sorted(diagnostics.items()) if category != "ok")
    partial = counts['data'] + counts['error'] > 0 or db_ok < len(top) or bool(missing_files)
    delivered = send_email(
        f"{'[ONVOLLEDIG] ' if partial else ''}Magic Formula (Greenblatt) rapport {today_str()}",
        dekking + f"\nDatabase: {db_ok}/{len(top)} selecties opgeslagen\n\n"
        + ("Ontbrekende/lege tickerlijsten: " + ", ".join(missing_files) + "\n\n" if missing_files else "")
        + (("=" * 40 + "\n\n").join(berichten) if berichten else "Geen gekwalificeerde kandidaten; zie datadekking.")
        + "\n\nOVERGESLAGEN TICKERS:\n" + details,
    )
    if not delivered:
        raise RuntimeError("E-mail niet verstuurd; controleer e-mailconfiguratie en verzendfouten.")
    print("E-mail verstuurd")
    if counts['error'] or (not alle and counts['data']) or db_ok < len(top):
        raise RuntimeError("Scan/opslag onvolledig; zie datadekking en het verzonden rapport.")

    print(f"\n{'='*60}")
    print("Klaar.")

def run_backtest():
    print("Backtest wordt niet ondersteund voor bot_00greenblatt: yfinance biedt "
          "geen betrouwbare historische reeks van EBIT/balansposten per scandatum.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    elif mode == "live":
        run_live_engine()
    else:
        sys.exit("Gebruik: python bot_00greenblatt.py [live|backtest]")

