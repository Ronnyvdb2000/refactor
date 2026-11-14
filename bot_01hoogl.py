#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01hoogl.py — GARP ONDERWAARDERING v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Bevat rate-limit-backoff en ondersteunt live/full modus.
"""

import argparse
import sys
import math
import time
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import pandas as pd
import yfinance as yf

from common import (
    today_str, safe_float, yahoo_link,
    load_tickers_from_file, bouw_bestandslijst, label_voor,
    send_email
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
    return any(part in tekst.casefold() for part in ("too many requests", "rate limit", "429"))

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
    price_currency:           str = "Onbekend"
    market_cap_usd:           float = float("nan")
    quote_date:               str = "Onbekend"
    data_notes:               str = ""

def finite(value):
    value = safe_float(value)
    return value if math.isfinite(value) else float("nan")


def money_currency(currency):
    return {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}.get(currency, currency)


def usd_rate(currency, cache):
    if currency == "USD":
        return 1.0
    if currency not in cache:
        cache[currency] = float("nan")
        try:
            hist = yf.Ticker(f"{currency}USD=X").history(period="1mo", auto_adjust=False)
            if hist is not None and not hist.empty:
                hist = hist.sort_index()
                date = pd.Timestamp(hist.index[-1]).tz_localize(None).normalize()
                value = finite(hist.Close.iloc[-1])
                if 0 <= (pd.Timestamp(today_str()) - date).days <= 7 and value > 0:
                    cache[currency] = value
        except Exception as exc:
            if is_rate_limit_fout(exc):
                del cache[currency]
                raise
    return cache[currency]


def analyse_ticker(ticker: str, cfg: dict, fx_cache=None) -> Tuple[Optional[HooglSignaal], bool]:
    try:
        info = yf.Ticker(ticker).info or {}
        price = finite(info.get("currentPrice"))
        if not price > 0:
            price = finite(info.get("regularMarketPrice"))
        if not price > 0:
            return None, False
        currency = info.get("currency")
        if not currency:
            return None, False
        notes = []
        timestamp = finite(info.get("regularMarketTime"))
        quote_date = "Onbekend"
        if math.isfinite(timestamp):
            moment = pd.to_datetime(timestamp, unit="s", utc=True, errors="coerce")
            if pd.isna(moment):
                return None, False
            age = (pd.Timestamp(today_str()) - moment.tz_localize(None).normalize()).days
            if not 0 <= age <= 7:
                return None, False
            quote_date = moment.date().isoformat()
        else:
            notes.append("koersdatum ontbreekt; versheid niet controleerbaar")

        book = finite(info.get("bookValue"))
        eps = finite(info.get("trailingEps"))
        forward_eps = finite(info.get("forwardEps"))
        roe_pct = finite(finite(info.get("returnOnEquity")) * 100)
        fwd_pe = finite(info.get("forwardPE"))
        market_cap = finite(info.get("marketCap"))
        analyst = finite(info.get("numberOfAnalystOpinions"))
        if not (math.isfinite(analyst) and analyst >= 0 and analyst.is_integer()):
            analyst = float("nan")
        # EPS/bookValue are only compared when reporting and quote base currency match.
        comparable = (info.get("financialCurrency") == currency
                      and currency not in ("GBp", "GBX", "ZAc", "ILA"))
        payback = growth = float("nan")
        if comparable:
            if math.isfinite(book) and math.isfinite(eps) and eps > 0:
                payback = finite(max(0.0, price - book) / eps)
        else:
            notes.append("koers-/verslageenheden verschillen of zijn onbekend; terugverdientijd niet vergelijkbaar")
        # Relative EPS growth does not combine the price with accounting units.
        if math.isfinite(eps) and math.isfinite(forward_eps) and eps > 0:
            growth = finite((forward_eps / eps - 1) * 100)
        cap_usd = float("nan")
        if math.isfinite(market_cap) and market_cap > 0:
            cap_usd = finite(market_cap * usd_rate(money_currency(currency), fx_cache if fx_cache is not None else {}))
        if not cap_usd > 0:
            notes.append("marktkapitalisatie in USD onbekend (waarde of recente FX ontbreekt)")
        checks = [
            (roe_pct, roe_pct >= cfg["roe_min"], "RoE", "%"),
            (payback, payback <= cfg["terugverdien_max_jaar"], "terugverdientijd", " jaar"),
            (fwd_pe, 0 < fwd_pe <= cfg["fwd_pe_max"], "forward P/E", "x"),
            (growth, growth > 0, "EPS-groei", "%"),
            (cap_usd, 0 < cap_usd <= cfg["marktkap_max"], "marktkapitalisatie USD", ""),
            (analyst, analyst <= cfg["analisten_max"], "analistencoverage", ""),
        ]
        score, labels = 0, []
        for value, passed, name, suffix in checks:
            if not math.isfinite(value):
                labels.append("✗ onbekend")
                notes.append(f"{name} onbekend; geen scorepunt")
            else:
                score += int(passed)
                shown = f"USD {value/1e9:.3f}B" if name == "marktkapitalisatie USD" else f"{value:.2f}{suffix}"
                labels.append(("✓ " if passed else "✗ ") + shown)
        return HooglSignaal(ticker=ticker, price=price, score=score, roe_pct=roe_pct,
            terugverdienperiode=payback, forward_pe=fwd_pe, verwachte_winstgroei_pct=growth,
            div_yield=finite(info.get("dividendYield")), market_cap=market_cap,
            analisten_count=analyst, roe_label=labels[0], terugverdien_label=labels[1],
            fwd_pe_label=labels[2], groei_label=labels[3], marktkap_label=labels[4],
            analisten_label=labels[5], price_currency=currency, market_cap_usd=cap_usd,
            quote_date=quote_date, data_notes="; ".join(notes)), False
    except Exception as exc:
        if is_rate_limit_fout(exc):
            return None, True
        raise


def ranking(sig):
    return (-sig.score, sig.terugverdienperiode if math.isfinite(sig.terugverdienperiode) else float("inf"), sig.ticker)

# ============================================================
# OUTPUT
# ============================================================

def fmt_value(value, digits=1):
    return f"{value:.{digits}f}" if math.isfinite(value) else "onbekend"


def format_bericht(exchange_name: str, signalen: List[HooglSignaal], alle: List[HooglSignaal], cfg: dict) -> Optional[str]:
    if not alle:
        return None

    nu        = today_str()
    top_n     = cfg["top_n"]
    top_tonen = sorted(alle, key=ranking)[:top_n]
    max_sc    = max((s.score for s in signalen), default=0) if signalen else 0
    lbl       = {6: "⭐ PERFECTE SCORE (6/6)", 5: "🟡 STERK (5/6)", 4: "🟠 GOED (4/6)"}.get(max_sc, "📊")

    def sig_regel(s: HooglSignaal, detail: bool = False) -> str:
        r = f"• `{s.ticker}` {_score_bar(s.score)} | {s.price_currency} {s.price:.4f} (koersdatum {s.quote_date}) | RoE:{fmt_value(s.roe_pct)}% | Terugverdien:{fmt_value(s.terugverdienperiode)}j | {yahoo_link(s.ticker)}"
        if detail:
            r += f"\n  {s.roe_label} | {s.terugverdien_label}\n  Fwd P/E: {s.fwd_pe_label} | Verw. winstgroei: {s.groei_label}\n  Marktkap: {s.marktkap_label} | Coverage: {s.analisten_label}"
        return r

    delen = [
        f"📈 *{cfg['label']} — {exchange_name}*",
        f"_{nu} | {len(alle)} geanalyseerd | {sum(s.score >= cfg['min_score'] for s in alle)} kandidaten, {len(signalen)} topselecties (score>={cfg['min_score']})_",
        "─────────────────────────────",
        f"🏆 *TOP {min(top_n, len(alle))} HOOGSTE SCORE (kan onder selectiedrempel liggen):*",
        "\n\n".join(sig_regel(s, detail=True) for s in top_tonen),
    ]

    overige = [s for s in signalen if s not in top_tonen]
    if overige:
        delen += ["─────────────────────────────", f"*{lbl} — overige kandidaten:*"]
        for s in overige:
            delen.append(sig_regel(s))

    delen.append("Eigen GARP-heuristiek; geen orders of gevalideerde rendementsclaim. Terugverdientijd = max(0, koers - boekwaarde) / trailing EPS bij constante winst; forward EPS is een onzekere analistenraming. Yahoo-snapshot, geen afstemming van historische boekjaren.")
    delen.append(f"⚙️ _RoE>={cfg['roe_min']:.0f}% | terugverdienperiode<={cfg['terugverdien_max_jaar']:.0f}j | forward P/E<={cfg['fwd_pe_max']:.0f}x | verwachte winstgroei>0% | marktkap<=USD {cfg['marktkap_max']/1e9:.0f}B | analisten<={cfg['analisten_max']}_")
    return "\n\n".join(delen)

# ============================================================
# ENGINE
# ============================================================

def run_engine(modus: str):
    cfg = MODUS_CFG[modus]
    reports, issues, errors = [], [], []
    exchanges = {}
    for filename in bouw_bestandslijst(cfg["bestand_suffix"]):
        tickers = sorted(set(load_tickers_from_file(filename)))
        if tickers:
            exchanges[label_voor(filename)] = tickers
        else:
            issues.append(f"{filename}: ontbrekende of lege tickerlijst")
    cache, rates = {}, {}
    consecutive = pauses = rate_errors = 0
    stopped = False
    stored = selected = 0
    throttle = cfg["throttle_sec"]
    for exchange, tickers in exchanges.items():
        for ticker in tickers:
            if ticker in cache or stopped:
                continue
            try:
                sig, limited = analyse_ticker(ticker, cfg, fx_cache=rates)
                if limited:
                    rate_errors += 1
                    consecutive += 1
                    issues.append(f"{ticker}: rate limiting; niet geanalyseerd")
                else:
                    consecutive = 0
                    if sig is None:
                        issues.append(f"{ticker}: onbruikbare koers/valuta of verouderde koersdatum")
                cache[ticker] = sig
                if sig is not None and sig.data_notes:
                    issues.append(f"{ticker}: {sig.data_notes}")
            except Exception as exc:
                cache[ticker] = None
                errors.append(f"{ticker}: analyse mislukt ({type(exc).__name__}: {exc})")
            if consecutive >= RATE_LIMIT_CONSECUTIVE_DREMPEL:
                if pauses >= RATE_LIMIT_MAX_PAUZES:
                    stopped = True
                    errors.append(f"Run gestopt: rate-limitpauzebudget uitgeput ({rate_errors} fouten)")
                else:
                    pauses += 1
                    throttle += RATE_LIMIT_THROTTLE_VERHOGING
                    print(f"Rate-limitpauze {pauses}/{RATE_LIMIT_MAX_PAUZES}", flush=True)
                    remaining = RATE_LIMIT_PAUZE_SEC
                    while remaining > 0:
                        time.sleep(min(60, remaining))
                        remaining -= min(60, remaining)
                    consecutive = 0
            time.sleep(throttle)
        all_signals = [cache[t] for t in tickers if cache.get(t) is not None]
        candidates = sorted((s for s in all_signals if s.score >= cfg["min_score"]), key=ranking)
        signals = candidates[:cfg["top_n"]]
        selected += len(signals)
        for rank, sig in enumerate(signals, 1):
            try:
                ok = log_selectie(ticker=sig.ticker, datum=today_str(), strategie=cfg["strategie"],
                    beurs=exchange, koers=sig.price,
                    parameters={"score": sig.score, "rank": rank, "roe_pct": sig.roe_pct,
                        "terugverdienperiode": sig.terugverdienperiode, "forward_pe": sig.forward_pe,
                        "verwachte_winstgroei_pct": sig.verwachte_winstgroei_pct,
                        "dividend_yield_provider_raw": sig.div_yield,
                        "market_cap": sig.market_cap, "market_cap_usd": sig.market_cap_usd,
                        "analisten_count": sig.analisten_count, "price_currency": sig.price_currency,
                        "quote_date": sig.quote_date, "data_notes": sig.data_notes,
                        "grafiek": yahoo_link(sig.ticker)})
                if not ok:
                    raise RuntimeError("geen opslagbevestiging")
                stored += 1
            except Exception as exc:
                errors.append(f"{sig.ticker}: database mislukt ({exc})")
        report = (f"{exchange}: {len(all_signals)}/{len(tickers)} geanalyseerd, "
                  f"{len(candidates)} kandidaten, {len(signals)} topselecties.\n" +
                  (format_bericht(exchange, signals, all_signals, cfg) or "Geen bruikbare analyses."))
        reports.append(report)
        print(report, flush=True)
    requested = {t for tickers in exchanges.values() for t in tickers}
    skipped = sorted(requested - cache.keys())
    if skipped:
        issues.append("Niet verwerkt na stop: " + ", ".join(skipped))
    analysed = sum(sig is not None for sig in cache.values())
    if not analysed:
        errors.append("Geen bruikbare analyses")
    summary = (f"{analysed}/{len(requested)} unieke tickers geanalyseerd. "
               f"Database: {stored}/{selected} selecties opgeslagen. Telegram uit.")
    body = "\n\n".join([summary]+reports+["Databeperkingen:"]+issues+["Fouten:"]+errors)
    print(body, flush=True)
    subject = ("[ONVOLLEDIG] " if issues or errors else "") + cfg["label"] + " " + today_str()
    if not send_email(subject, body):
        errors.append("E-mail niet verzonden")
    if errors:
        raise RuntimeError("; ".join(errors))
    print("Klaar — e-mail verzonden.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("live", "full"), default="live")
    run_engine(parser.parse_args().mode)
