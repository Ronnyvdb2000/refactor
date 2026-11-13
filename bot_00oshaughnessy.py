#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00oshaughnessy.py — O'SHAUGHNESSY "TRENDING VALUE" v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Berekent een eigen VC2-variant met zeven factoren en rangschikt op momentum.
"""

import sys
import os
import math
import time
from collections import Counter
from dataclasses import dataclass, asdict
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
    "max_fiscal_age_days":  550,
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
    price_currency:    str = "Onbekend"
    financial_currency: str = "Onbekend"
    fiscal_date:       str = ""


class _SkipTicker(ValueError):
    def __init__(self, reason, category="data"):
        super().__init__(reason)
        self.category = category


def _positive(value):
    value = safe_float(value)
    return value if math.isfinite(value) and value > 0 else float("nan")


def _value(frame, names, fiscal_date):
    for name in names:
        if name in frame.index:
            value = safe_float(frame.loc[name, fiscal_date])
            if math.isfinite(value):
                return value
    return float("nan")


def _usd_rate(currency, cache):
    if currency == "USD":
        return 1.0
    if currency not in cache:
        cache[currency] = None
        try:
            frame = yf.Ticker(f"{currency}USD=X").history(period="1mo", auto_adjust=False)
            close = pd.to_numeric(frame["Close"], errors="coerce")
            close = close.loc[close.map(lambda x: math.isfinite(x) and x > 0)]
            if not close.empty:
                age = (pd.Timestamp(today_str()) - pd.Timestamp(close.index[-1]).tz_localize(None).normalize()).days
                if 0 <= age <= 7:
                    cache[currency] = float(close.iloc[-1])
        except Exception:
            pass
    if cache[currency] is None:
        raise _SkipTicker(f"geen recente wisselkoers {currency}/USD")
    return cache[currency]

def analyse_ticker_ruw(ticker: str, exchange: str, cfg: dict,
                       diagnostics=None, fx_cache=None) -> Optional[RuweSignaal]:
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        price = _positive(info.get("currentPrice"))
        if not math.isfinite(price):
            price = _positive(info.get("regularMarketPrice"))
        if not math.isfinite(price):
            raise _SkipTicker("geen geldige koers")
        quote_currency, financial_currency = info.get("currency"), info.get("financialCurrency")
        if not quote_currency or not financial_currency:
            raise _SkipTicker("koers- of verslagvaluta ontbreekt")
        valuation_currency = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}.get(quote_currency, quote_currency)
        if valuation_currency != financial_currency:
            raise _SkipTicker(f"valutaverschil {valuation_currency}/{financial_currency}; bedragen niet eenduidig vergelijkbaar")
        market_cap = _positive(info.get("marketCap"))
        if not math.isfinite(market_cap):
            raise _SkipTicker("ongeldige marktkapitalisatie")
        rate = _usd_rate(valuation_currency, fx_cache if fx_cache is not None else {})
        if market_cap * rate < cfg["marktkap_min"]:
            raise _SkipTicker("marktkapitalisatie onder USD-grens", "filter")

        inc, bs, cf = tk.financials, tk.balance_sheet, tk.cashflow
        if any(frame is None or frame.empty for frame in (inc, bs, cf)):
            raise _SkipTicker("jaarrekening, balans of cashflow ontbreekt")
        inc, bs, cf = inc.copy(), bs.copy(), cf.copy()
        for frame in (inc, bs, cf):
            frame.columns = pd.to_datetime(frame.columns, errors="coerce")
        dates = set(inc.columns.dropna()).intersection(bs.columns.dropna(), cf.columns.dropna())
        if not dates:
            raise _SkipTicker("geen gemeenschappelijk boekjaar")
        fiscal = max(dates)
        age = (pd.Timestamp(today_str()) - fiscal.tz_localize(None)).days
        if not 0 <= age <= cfg.get("max_fiscal_age_days", 550):
            raise _SkipTicker("boekjaar toekomstig of te oud")

        pe = _positive(info.get("trailingPE"))
        pb = _positive(info.get("priceToBook"))
        ps = _positive(info.get("priceToSalesTrailing12Months"))
        if not math.isfinite(ps):
            revenue = _positive(_value(inc, ["Total Revenue"], fiscal))
            ps = market_cap / revenue if math.isfinite(revenue) else float("nan")
        ev = _positive(info.get("enterpriseValue"))
        if not math.isfinite(ev):
            debt, cash = safe_float(info.get("totalDebt")), safe_float(info.get("totalCash"))
            if all(math.isfinite(v) and v >= 0 for v in (debt, cash)):
                ev = _positive(market_cap + debt - cash)
        ev_ebitda = _positive(info.get("enterpriseToEbitda"))
        if not math.isfinite(ev_ebitda):
            ebitda = _positive(_value(inc, ["EBITDA"], fiscal))
            if not math.isfinite(ebitda):
                ebit = _value(inc, ["EBIT", "Operating Income"], fiscal)
                da = _value(cf, ["Depreciation And Amortization", "Depreciation Amortization Depletion"], fiscal)
                ebitda = _positive(ebit + da)
            ev_ebitda = ev / ebitda if all(math.isfinite(v) for v in (ev, ebitda)) else float("nan")
        ocf = _positive(_value(cf, ["Operating Cash Flow", "Total Cash From Operating Activities", "Cash Flow From Continuing Operating Activities"], fiscal))
        capex = _value(cf, ["Capital Expenditure", "Capital Expenditures"], fiscal)
        fcf = _positive(ocf - abs(capex))
        pcf = market_cap / ocf if math.isfinite(ocf) else float("nan")
        ev_fcf = ev / fcf if all(math.isfinite(v) for v in (ev, fcf)) else float("nan")

        # Cash-based shareholder yield in één boekjaar en één valuta. Geen
        # mix van Yahoo dividendYield-eenheden en splitgevoelige aandelenaantallen.
        dividends = _value(cf, ["Common Stock Dividend Paid", "Common Stock Dividend Payments", "Cash Dividends Paid"], fiscal)
        net_issuance = _value(cf, ["Net Common Stock Issuance"], fiscal)
        if not math.isfinite(net_issuance):
            repurchase = _value(cf, ["Repurchase Of Capital Stock", "Repurchase Of Common Stock"], fiscal)
            issuance = _value(cf, ["Issuance Of Capital Stock", "Common Stock Issuance"], fiscal)
            if all(math.isfinite(v) for v in (repurchase, issuance)):
                net_issuance = abs(issuance) - abs(repurchase)
        shareholder_yield = ((abs(dividends) - net_issuance) / market_cap * 100
                             if all(math.isfinite(v) for v in (dividends, net_issuance)) else float("nan"))
        metrics = (pe, pb, ps, pcf, ev_ebitda, ev_fcf, shareholder_yield)
        if sum(math.isfinite(v) for v in metrics) < cfg["min_vc2_ratios"]:
            raise _SkipTicker("onvoldoende werkelijk beschikbare VC2-factoren")
        if diagnostics is not None:
            diagnostics[ticker] = ("ok", "factoren beschikbaar")
        return RuweSignaal(ticker, exchange, price, pe, pb, ps, pcf, ev_ebitda, ev_fcf,
                           shareholder_yield, quote_currency, financial_currency, fiscal.date().isoformat())
    except _SkipTicker as exc:
        if diagnostics is not None:
            diagnostics[ticker] = (exc.category, str(exc))
        print(f"[SKIP:{exc.category}] {ticker}: {exc}")
        return None
    except Exception as exc:
        if diagnostics is not None:
            diagnostics[ticker] = ("error", f"{type(exc).__name__}: {exc}")
        print(f"[WARN] {ticker}: {type(exc).__name__}: {exc}")
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
    columns = ["pe", "pb", "ps", "pcf", "ev_ebitda", "ev_fcf", "shareholder_yield"]
    records = [asdict(item) for item in dedupliceer_op_ticker(alle)]
    df = pd.DataFrame(records, columns=list(RuweSignaal.__dataclass_fields__))
    for column in columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
        df[column] = df[column].where(df[column].map(math.isfinite))
        if column != "shareholder_yield":
            df[column] = df[column].where(df[column] > 0)
    # Eerst de rankingpopulatie bepalen. Afgekeurde records mogen geen
    # percentielgrenzen voor de overblijvende kandidaten beïnvloeden.
    df["vc2_geldige_ratios"] = df[columns].notna().sum(axis=1)
    df = df.loc[df["vc2_geldige_ratios"] >= cfg["min_vc2_ratios"]].copy()
    for column in columns:
        df[f"pct_{column}"] = df[column].rank(
            method="average", pct=True, ascending=(column == "shareholder_yield")) * 100
    df["vc2_score"] = df[[f"pct_{column}" for column in columns]].mean(axis=1)
    return df

# ============================================================
# STAP 2 — MOMENTUM
# ============================================================

def bereken_momentum_pct(ticker: str, maanden: int) -> float:
    try:
        if not isinstance(maanden, int) or maanden <= 0:
            return float("nan")
        today = pd.Timestamp(today_str())
        anchor = today - pd.DateOffset(months=maanden)
        hist = yf.Ticker(ticker).history(
            start=(anchor - pd.Timedelta(days=14)).date().isoformat(),
            end=(today + pd.Timedelta(days=1)).date().isoformat(), auto_adjust=True)
        if hist is None or hist.empty or "Close" not in hist:
            return float("nan")
        close = pd.to_numeric(hist["Close"], errors="coerce").copy()
        index = pd.to_datetime(close.index, errors="coerce")
        if index.tz is not None:
            index = index.tz_localize(None)
        close.index = index.normalize()
        close = close.loc[close.index.notna() & close.map(lambda v: math.isfinite(v) and v > 0)]
        close = close.sort_index()
        close = close.loc[~close.index.duplicated(keep="last") & (close.index <= today)]
        if len(close) < 2 or (today - close.index[-1]).days > 7:
            return float("nan")
        start_quotes = close.loc[close.index <= anchor]
        if start_quotes.empty or (anchor - start_quotes.index[-1]).days > 7:
            return float("nan")
        start, end = float(start_quotes.iloc[-1]), float(close.iloc[-1])
        return (end / start - 1) * 100
    except Exception as exc:
        print(f"[WARN] {ticker}: momentum niet beschikbaar ({type(exc).__name__}: {exc})")
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
    price_currency:    str = "Onbekend"
    financial_currency: str = "Onbekend"
    fiscal_date:       str = ""

# ============================================================
# OUTPUT
# ============================================================

def _sig_regel(s: TrendingValueSignaal, rank: int) -> str:
    def fmt(v):
        return f"{v:.1f}" if math.isfinite(v) else "n/b"
    return (
        f"{rank}. `{s.ticker}` ({s.exchange}) | VC2-variant:{s.vc2_score:.0f} | Momentum:{s.momentum_pct:+.1f}% | "
        f"PE:{fmt(s.pe)} PB:{fmt(s.pb)} PS:{fmt(s.ps)} PCF:{fmt(s.pcf)} EV/EBITDA:{fmt(s.ev_ebitda)} EV/FCF:{fmt(s.ev_fcf)} "
        f"SY:{fmt(s.shareholder_yield)}% | {s.price_currency} {s.price:.2f} | boekjaar {s.fiscal_date} | {yahoo_link(s.ticker)}"
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
                "⚙️ _Eigen VC2-variant (7 factoren) = gemiddeld percentiel over P/E, P/B, P/S, P/CF, EV/EBITDA, EV/FCF (lager=beter) "
                "en Shareholder Yield (hoger=beter), 100=beste. Enkel de goedkoopste "
                f"{100 - cfg['value_percentile_min']:.0f}% VAN DEZE RUN (relatieve rang, geen absolute "
                f"VC2-drempel) gaat door naar de {cfg['momentum_maanden']}-maands momentumranking. "
                f"marketCap>=USD {cfg['marktkap_min']/1e6:.0f}M; minimaal {cfg['min_vc2_ratios']}/7 factoren._"
            )
            regels.append("SY = (jaarlijkse cashdividenden - netto aandelenuitgifte) / actuele marktkapitalisatie. Ontbrekende factoren tellen niet als nul. Jaarlijkse cashflowratio's naast actuele/TTM-providerverhoudingen; geen exacte reproductie van de officiële strategie.")
        berichten.append("\n".join(regels))
    return berichten

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    cfg = OSHAUGHNESSY_CFG
    print(f"{'='*60}\nO'SHAUGHNESSY TRENDING VALUE — LIVE  {today_str()}\n{'='*60}")

    exchange_tickers: Dict[str, List[str]] = {}
    missing_files = []
    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist:
            missing_files.append(f_name)
            print(f"Bestand {f_name} ontbreekt of is leeg.")
            continue
        ex_name = label_voor(f_name)
        exchange_tickers[ex_name] = tlist
        print(f"  {ex_name}: {len(tlist)} tickers")

    if not exchange_tickers:
        raise RuntimeError("Geen ticker bestanden gevonden.")

    alle_ruw: List[RuweSignaal] = []
    universum_grootte = 0
    cache, diagnostics, fx_cache = {}, {}, {}
    for ex_name, tlist in exchange_tickers.items():
        print(f"\nStap 1 — ratio's ophalen: {ex_name} ({len(tlist)} tickers)...")
        gevonden = 0
        for ticker in tlist:
            if ticker not in cache:
                universum_grootte += 1
                cache[ticker] = analyse_ticker_ruw(ticker, ex_name, cfg, diagnostics=diagnostics, fx_cache=fx_cache)
                if cache[ticker] is not None:
                    alle_ruw.append(cache[ticker])
                time.sleep(cfg["throttle_sec"])
            sig = cache[ticker]
            if sig is not None:
                gevonden += 1
        print(f"  → {gevonden}/{len(tlist)} tickers met voldoende waarderingsdata")

    counts = Counter(category for category, _ in diagnostics.values())
    coverage = (f"Datadekking: {universum_grootte} uniek gescreend | {counts['ok']} met factoren | "
                f"{counts['filter']} uitgesloten op criteria | {counts['data']} onbruikbare/ontbrekende data | {counts['error']} fouten")
    print(coverage)

    aantal_voor_dedup = len(alle_ruw)
    alle_ruw = dedupliceer_op_ticker(alle_ruw)
    if len(alle_ruw) < aantal_voor_dedup:
        print(f"Deduplicatie: {aantal_voor_dedup - len(alle_ruw)} dubbele ticker(s) verwijderd.")

    df = bereken_vc2(alle_ruw, cfg)
    print(f"\nVC2 berekend voor {len(df)}/{len(alle_ruw)} tickers (>= {cfg['min_vc2_ratios']}/7 ratio's geldig)")

    drempel = df["vc2_score"].quantile(cfg["value_percentile_min"] / 100.0) if not df.empty else float("nan")
    waarde_decile = df.loc[df["vc2_score"] >= drempel].copy()

    print(f"Goedkoopste VC2-decile: {len(waarde_decile)} tickers → momentum ophalen...")

    trending: List[TrendingValueSignaal] = []
    momentum_missing = []
    for _, rij in waarde_decile.iterrows():
        mom = bereken_momentum_pct(rij["ticker"], cfg["momentum_maanden"])
        if not math.isfinite(mom):
            momentum_missing.append(rij["ticker"])
            print(f"[SKIP:data] {rij['ticker']}: geen volledige/recent bijgewerkte momentumhistorie")
            continue
        trending.append(TrendingValueSignaal(
            ticker=rij["ticker"], exchange=rij["exchange"], price=rij["price"],
            pe=rij["pe"], pb=rij["pb"], ps=rij["ps"], pcf=rij["pcf"],
            ev_ebitda=rij["ev_ebitda"], ev_fcf=rij["ev_fcf"],
            shareholder_yield=rij["shareholder_yield"], vc2_score=rij["vc2_score"],
            momentum_pct=mom, price_currency=rij["price_currency"],
            financial_currency=rij["financial_currency"], fiscal_date=rij["fiscal_date"],
        ))
        time.sleep(cfg["throttle_sec"])

    trending.sort(key=lambda s: (-s.momentum_pct, -s.vc2_score, s.ticker))
    top = trending[: cfg["top_n_global"]]

    print(f"\nTop {len(top)} (hoogste {cfg['momentum_maanden']}-maands momentum binnen goedkoopste VC2-decile):")
    for rank, s in enumerate(top, start=1):
        print(f"  {rank}. {s.ticker} ({s.exchange}) VC2={s.vc2_score:.0f} Mom={s.momentum_pct:+.1f}%")

    db_ok = 0
    for rank, s in enumerate(top, start=1):
        saved = log_selectie(
            ticker=s.ticker, datum=today_str(), strategie=cfg["strategie"], beurs=s.exchange, koers=s.price,
            parameters={
                "rank": rank, "vc2_score": s.vc2_score, "momentum_6m_pct": s.momentum_pct,
                "pe_ratio": None if math.isnan(s.pe) else s.pe,
                "pb_ratio": None if math.isnan(s.pb) else s.pb,
                "ps_ratio": None if math.isnan(s.ps) else s.ps,
                "pcf_ratio": None if math.isnan(s.pcf) else s.pcf,
                "ev_ebitda": None if math.isnan(s.ev_ebitda) else s.ev_ebitda,
                "ev_fcf": None if math.isnan(s.ev_fcf) else s.ev_fcf,
                "shareholder_yield": s.shareholder_yield if math.isfinite(s.shareholder_yield) else None, "grafiek": yahoo_link(s.ticker),
                "price_currency": s.price_currency, "financial_currency": s.financial_currency,
                "fiscal_date": s.fiscal_date, "momentum_months": cfg["momentum_maanden"],
                "momentum_pct": s.momentum_pct, "score_variant": "VC2-custom-7",
            },
        )
        db_ok += bool(saved)
    print(f"Database: {db_ok}/{len(top)} selecties opgeslagen")

    berichten = bouw_telegram_berichten(top, universum_grootte, len(waarde_decile), cfg)
    if os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        for b in berichten:
            delivered = send_telegram(b, parse_mode="Markdown")
            print("Telegram verstuurd" if delivered else "[WARN] Telegram niet verstuurd")
    else:
        print("Telegram uitgeschakeld (geen configuratie)")

    partial = counts['data'] + counts['error'] > 0 or bool(momentum_missing) or bool(missing_files) or db_ok < len(top)
    details = "\n".join(f"{ticker}: {category} — {reason}" for ticker, (category, reason) in sorted(diagnostics.items()) if category != "ok")
    body = (coverage + f"\nVC2-score beschikbaar: {len(df)} | waardeselectie: {len(waarde_decile)} | momentum beschikbaar: {len(trending)}"
            + f"\nDatabase: {db_ok}/{len(top)} selecties opgeslagen\n\n"
            + (("=" * 40 + "\n\n").join(berichten) if berichten else "Geen kandidaten; zie datadekking.")
            + "\n\nOVERGESLAGEN TICKERS:\n" + details
            + "\nMomentum ontbreekt: " + (", ".join(momentum_missing) or "geen")
            + "\nOntbrekende/lege tickerlijsten: " + (", ".join(missing_files) or "geen"))
    delivered = send_email(f"{'[ONVOLLEDIG] ' if partial else ''}Trending Value (O'Shaughnessy) rapport {today_str()}", body)
    if not delivered:
        raise RuntimeError("E-mail niet verstuurd; controleer e-mailconfiguratie en verzendfouten.")
    print("E-mail verstuurd")
    if counts['error'] or db_ok < len(top) or (not trending and (counts['data'] or momentum_missing)):
        raise RuntimeError("Scan/opslag onvolledig; zie het verzonden rapport.")

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
    elif mode == "live":
        run_live_engine()
    else:
        sys.exit("Gebruik: python bot_00oshaughnessy.py [live|backtest]")

