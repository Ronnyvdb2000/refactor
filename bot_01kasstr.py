#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01kasstr.py — KASSTROOM ONDERWAARDERING v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
FCF-analyse, balans-kwaliteit, Piotroski F-Score en notificaties
zijn gecentraliseerd.
"""

import argparse
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

class DataUnavailable(ValueError):
    """Expected missing, inconsistent or stale provider data."""


def finite(value):
    value = safe_float(value)
    return value if math.isfinite(value) else float("nan")


def _annual_statement(frame):
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = frame.copy()
    def local_date(value):
        date = pd.to_datetime(value, errors="coerce")
        return pd.NaT if pd.isna(date) else pd.Timestamp(date).tz_localize(None).normalize()
    frame.columns = pd.DatetimeIndex([local_date(value) for value in frame.columns])
    frame = frame.loc[:, frame.columns.notna()]
    frame = frame.loc[:, ~frame.columns.duplicated(keep="first")]
    return frame.reindex(sorted(frame.columns, reverse=True), axis=1)


def _value(frame, names, fiscal):
    if frame is None or frame.empty or fiscal not in frame.columns:
        return float("nan")
    for name in names:
        if name in frame.index:
            value = finite(frame.loc[name, fiscal])
            if math.isfinite(value):
                return value
    return float("nan")


OCF_NAMES = ["Operating Cash Flow", "Total Cash From Operating Activities", "Cash Flow From Continuing Operating Activities"]
CAPEX_NAMES = ["Capital Expenditure", "Capital Expenditures", "Purchase Of PPE"]
DIV_NAMES = ["Cash Dividends Paid", "Common Stock Dividend Paid", "Common Stock Dividend Payments", "Payment Of Dividends"]
BUYBACK_NAMES = ["Repurchase Of Capital Stock", "Common Stock Repurchase", "Repurchase Of Common Stock"]


def _fcf_series(cashflow):
    """Dated annual values, oldest first; missing years are never removed."""
    cashflow = _annual_statement(cashflow)
    values = {}
    for fiscal in reversed(cashflow.columns):
        value = _value(cashflow, ["Free Cash Flow"], fiscal)
        if not math.isfinite(value):
            ocf = _value(cashflow, OCF_NAMES, fiscal)
            capex = _value(cashflow, CAPEX_NAMES, fiscal)
            value = finite(ocf - abs(capex))
        values[fiscal] = value
    return pd.Series(values, dtype=float)


def _shareholder_return(cashflow, fiscal):
    dividends = _value(cashflow, DIV_NAMES, fiscal)
    buybacks = _value(cashflow, BUYBACK_NAMES, fiscal)
    if not all(math.isfinite(v) for v in (dividends, buybacks)):
        return float("nan")
    return finite(abs(dividends) + abs(buybacks))


def _piotroski_f_score(tk, cashflow, fiscal=None):
    """Informational approximation using matched annual end-balance ratios.

    Missing criteria have value None. A full /9 score requires all nine.
    """
    try:
        bs_raw, inc_raw = tk.balance_sheet, tk.financials
    except Exception as exc:
        return None, f"onbekend (providerbalans/resultaten niet beschikbaar: {type(exc).__name__})", {}
    bs, inc, cf = (_annual_statement(x) for x in (bs_raw, inc_raw, cashflow))
    flags = {}
    if any(x.empty for x in (bs, inc, cf)):
        return None, "onbekend (balans/resultaten/cashflow ontbreekt)", flags
    fiscal = fiscal if fiscal is not None else cf.columns[0]
    dates = sorted(set(bs.columns) & set(inc.columns) & set(cf.columns), reverse=True)
    if fiscal not in dates:
        return None, "onbekend (geen afstemming op FCF-boekjaar)", flags
    prior = [d for d in dates if d < fiscal and 300 <= (fiscal-d).days <= 430]
    age = (pd.Timestamp(today_str()) - fiscal).days
    if not prior or not 0 <= age <= 550:
        return None, "onbekend (geen twee recente opeenvolgende gemeenschappelijke boekjaren)", flags
    previous = max(prior)
    def value(frame, names, date):
        return _value(frame, names, date)
    def ratio(a, denominator):
        return finite(a / denominator) if math.isfinite(a) and math.isfinite(denominator) and denominator > 0 else float("nan")
    def compare(name, vals, condition):
        flags[name] = bool(condition(*vals)) if all(math.isfinite(v) for v in vals) else None
    ta = [value(bs,["Total Assets"],d) for d in (fiscal,previous)]
    ni = [value(inc,["Net Income"],d) for d in (fiscal,previous)]
    rev = [value(inc,["Total Revenue"],d) for d in (fiscal,previous)]
    gp = [value(inc,["Gross Profit"],d) for d in (fiscal,previous)]
    ca = [value(bs,["Current Assets","Total Current Assets"],d) for d in (fiscal,previous)]
    cl = [value(bs,["Current Liabilities","Total Current Liabilities"],d) for d in (fiscal,previous)]
    debt = [value(bs,["Long Term Debt","Long Term Debt And Capital Lease Obligation"],d) for d in (fiscal,previous)]
    shares = [value(bs,["Ordinary Shares Number","Share Issued"],d) for d in (fiscal,previous)]
    # Negative debt/share/current-asset values are not valid accounting inputs.
    for seq in (debt,shares,ca,cl):
        for i,v in enumerate(seq):
            if v < 0:seq[i] = float("nan")
    shares = [v if v>0 else float("nan") for v in shares]
    roa = [ratio(ni[i],ta[i]) for i in range(2)]
    ocf = value(cf,OCF_NAMES,fiscal)
    compare("roa_positief",[roa[0]],lambda x:x>0)
    compare("ocf_positief",[ocf],lambda x:x>0)
    compare("roa_gestegen",roa,lambda x,y:x>y)
    compare("winstkwaliteit",[ocf,ni[0]],lambda x,y:x>y)
    compare("hefboom_gedaald",[ratio(debt[i],ta[i]) for i in range(2)],lambda x,y:x<y)
    compare("liquiditeit_gestegen",[ratio(ca[i],cl[i]) for i in range(2)],lambda x,y:x>y)
    compare("geen_verwatering",shares,lambda x,y:x>0 and y>0 and x<=y)
    compare("marge_gestegen",[ratio(gp[i],rev[i]) for i in range(2)],lambda x,y:x>y)
    compare("efficientie_gestegen",[ratio(rev[i],ta[i]) for i in range(2)],lambda x,y:x>y)
    known = sum(v is not None for v in flags.values())
    passed = sum(v is True for v in flags.values())
    score = passed if known == 9 else None
    prefix = f"{score}/9" if score is not None else f"onbekend ({passed}/{known} beschikbare criteria positief)"
    label = prefix + " [eindbalans-benadering] " + " ".join(
        ("?" if v is None else "✓" if v else "✗") + k for k,v in flags.items())
    return score, label, flags


def _money_currency(currency):
    return {"GBp":"GBP", "GBX":"GBP", "ZAc":"ZAR", "ILA":"ILS"}.get(currency,currency)


def _usd_rate(currency, cache):
    if currency == "USD":
        return 1.0
    if currency not in cache:
        cache[currency] = float("nan")
        hist = yf.Ticker(f"{currency}USD=X").history(period="1mo", auto_adjust=False)
        if hist is not None and not hist.empty:
            hist = hist.sort_index()
            date = pd.Timestamp(hist.index[-1]).tz_localize(None).normalize()
            rate = finite(hist.Close.iloc[-1])
            if 0 <= (pd.Timestamp(today_str())-date).days <= 7 and rate>0:
                cache[currency] = rate
    return cache[currency]

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
    piotroski_score: Optional[int]
    piotroski_label: str
    price_currency: str = "Onbekend"
    financial_currency: str = "Onbekend"
    market_cap_usd: float = float("nan")
    fiscal_date: str = ""
    first_fiscal_date: str = ""
    quote_date: str = "Onbekend"
    data_notes: str = ""
    piotroski_detail: Optional[dict] = None

def analyse_ticker(ticker: str, fx_cache=None) -> FCFSignaal:
    tk = yf.Ticker(ticker)
    info = tk.info or {}
    price = finite(info.get("currentPrice"))
    if not price>0:
        price = finite(info.get("regularMarketPrice"))
    cap = finite(info.get("marketCap"))
    if not price>0 or not cap>0:
        raise DataUnavailable("ongeldige koers of marktkapitalisatie")
    currency, financial = info.get("currency"), info.get("financialCurrency")
    if not currency or not financial or _money_currency(currency) != financial:
        raise DataUnavailable("ontbrekende/verschillende koers- en verslagvaluta; FCF/marktkap niet vergelijkbaar")
    notes = []
    quote_date = "Onbekend"
    timestamp = finite(info.get("regularMarketTime"))
    if math.isfinite(timestamp):
        moment = pd.to_datetime(timestamp,unit="s",utc=True,errors="coerce")
        if pd.isna(moment) or not 0 <= (pd.Timestamp(today_str())-moment.tz_localize(None).normalize()).days <= 7:
            raise DataUnavailable("ongeldige, toekomstige of verouderde koersdatum")
        quote_date = moment.date().isoformat()
    else:
        notes.append("koersdatum ontbreekt; versheid niet controleerbaar")
    cf = _annual_statement(tk.cashflow)
    fcf = _fcf_series(cf)
    if fcf.empty:
        raise DataUnavailable("geen jaarlijkse cashflow")
    fiscal = fcf.index[-1]
    if not 0 <= (pd.Timestamp(today_str())-fiscal).days <= 550:
        raise DataUnavailable("laatste cashflowboekjaar toekomstig of ouder dan 550 dagen")
    if not math.isfinite(fcf.iloc[-1]):
        raise DataUnavailable("FCF laatste boekjaar ontbreekt; oudere FCF niet als actuele FCF gebruiken")
    # Only contiguous known annual observations ending at the latest fiscal year.
    dates = [fiscal]
    for date in reversed(fcf.index[:-1]):
        if not math.isfinite(fcf.loc[date]) or not 300 <= (dates[-1]-date).days <= 430:
            notes.append("FCF-historie afgebroken bij ontbrekend of niet-aansluitend boekjaar")
            break
        dates.append(date)
    if len(dates)<FCF_CFG["min_years_required"]:
        raise DataUnavailable("minder dan twee opeenvolgende beschikbare FCF-boekjaren")
    values = fcf.loc[sorted(dates)]
    fcf_now = float(values.iloc[-1])
    fcf_yield = finite(fcf_now/cap*100)
    if not math.isfinite(fcf_yield) or abs(fcf_yield)>100:
        raise DataUnavailable("FCF yield ongeldig of absolute waarde boven 100%; mogelijke datamismatch")
    cap_usd = finite(cap*_usd_rate(financial,fx_cache if fx_cache is not None else {}))
    if not cap_usd>0:
        notes.append("geen recente USD-wisselkoers; marktkapcriterium onbekend")
    growing = bool(values.iloc[-1] > values.iloc[0])
    consistent = bool((values>0).all())
    rev_growth = finite(finite(info.get("revenueGrowth"))*100)
    debt, cash, ebitda = [finite(info.get(k)) for k in ("totalDebt","totalCash","ebitda")]
    leverage = finite((debt-cash)/ebitda) if all(math.isfinite(v) for v in (debt,cash,ebitda)) and debt>=0 and cash>=0 and ebitda>0 else float("nan")
    shareholder = _shareholder_return(cf,fiscal)
    payout = finite(shareholder/fcf_now*100) if fcf_now>0 else float("nan")
    analyst = finite(info.get("numberOfAnalystOpinions"))
    if not (math.isfinite(analyst) and analyst>=0 and analyst.is_integer()):
        analyst = float("nan")
    checks = [
        (fcf_yield,fcf_yield>=FCF_CFG["fcf_yield_min"],"FCF yield","%"),
        (float(growing),growing,"FCF-groei",""),
        (float(consistent),consistent,"FCF-consistentie",""),
        (rev_growth,rev_growth>0,"provideromzetgroei","%"),
        (leverage,leverage<=FCF_CFG["net_debt_ebitda_max"],"netto schuld/EBITDA","x"),
        (payout,0<payout<=FCF_CFG["payout_max_pct"],"payout (dividend + buybacks)","% van FCF"),
        (cap_usd,0<cap_usd<=FCF_CFG["marktkap_max"],"marktkap USD",""),
        (analyst,analyst<=FCF_CFG["analisten_max"],"analistencoverage",""),
    ]
    score, labels = 0, []
    for value, passed, name, suffix in checks:
        if not math.isfinite(value):
            if name == "payout (dividend + buybacks)" and fcf_now <= 0 and math.isfinite(shareholder):
                labels.append("✗ n.v.t. (FCF niet positief)")
            elif name == "netto schuld/EBITDA" and all(math.isfinite(v) and v >= 0 for v in (debt,cash)) and math.isfinite(ebitda) and ebitda <= 0:
                labels.append("✗ n.v.t. (EBITDA niet positief)")
            else:
                labels.append("✗ onbekend")
                notes.append(f"{name} onbekend; geen scorepunt")
        else:
            score += int(passed)
            display = f"USD {value/1e9:.3f}B" if name=="marktkap USD" else f"{value:.2f}{suffix}"
            labels.append(("✓ " if passed else "✗ ")+display)
    labels[1] = ("✓" if growing else "✗") + f" FCF {values.iloc[0]:,.2f} -> {fcf_now:,.2f} {financial}"
    labels[2] = ("✓" if consistent else "✗") + f" {sum(values>0)}/{len(values)} positieve opeenvolgende boekjaren"
    piotroski, pi_label, pi_detail = _piotroski_f_score(tk,cf,fiscal)
    if piotroski is None:
        notes.append("Piotroski-benadering onvolledig; geen volledige /9-score")
    return FCFSignaal(ticker=ticker,price=price,score=score,market_cap=cap,fcf_now=fcf_now,
        fcf_yield=fcf_yield,fcf_years=len(values),fcf_growing=growing,fcf_consistent=consistent,
        rev_growth_pct=rev_growth,net_debt_ebitda=leverage,payout_pct=payout,
        div_yield=finite(info.get("dividendYield")),analisten_count=analyst,
        yield_label=labels[0],trend_label=labels[1],consist_label=labels[2],growth_label=labels[3],
        debt_label=labels[4],payout_label=labels[5],marktkap_label=labels[6],analisten_label=labels[7],
        piotroski_score=piotroski,piotroski_label=pi_label,price_currency=currency,financial_currency=financial,
        market_cap_usd=cap_usd,fiscal_date=fiscal.date().isoformat(),first_fiscal_date=values.index[0].date().isoformat(),
        quote_date=quote_date,data_notes="; ".join(notes),piotroski_detail=pi_detail)


def ranking(sig):
    return (-sig.score, -(sig.piotroski_score if sig.piotroski_score is not None else -1), -sig.fcf_yield, sig.ticker)

# ============================================================
# OUTPUT
# ============================================================

def _score_bar(score: int) -> str:
    return "█" * score + "░" * (8 - score) + f" {score}/8"

def format_bericht(exchange_name: str, signalen: List[FCFSignaal], alle: List[FCFSignaal]) -> Optional[str]:
    if not alle:
        return None

    nu     = today_str()
    top3   = sorted(alle, key=ranking)[:3]
    max_sc = max((s.score for s in signalen), default=0) if signalen else 0
    lbl    = {8: "⭐ PERFECTE SCORE (8/8)", 7: "🟡 STERK (7/8)", 6: "🟠 GOED (6/8)", 5: "🟠 WATCHLIST (5/8)"}.get(max_sc, "📊")

    def sig_regel(s: FCFSignaal, detail: bool = False) -> str:
        r = f"• `{s.ticker}` {_score_bar(s.score)} | {s.price_currency} {s.price:.4f} (koersdag {s.quote_date}, FCF-boekjaar {s.fiscal_date}) | FCF yield:{s.fcf_yield:.1f}% | {yahoo_link(s.ticker)}"
        if detail:
            r += f"\n  {s.trend_label} | {s.consist_label}\n  Omzet: {s.growth_label} | Schuld: {s.debt_label} | Payout: {s.payout_label}\n  Marktkap: {s.marktkap_label} | Coverage: {s.analisten_label}\n  Piotroski F-Score: {s.piotroski_label}"
        return r

    delen = [
        f"💰 *KASSTROOM ONDERWAARDERING — {exchange_name}*",
        f"_{nu} | {len(alle)} geanalyseerd | {sum(s.score >= FCF_CFG['min_score'] for s in alle)} kandidaten, {len(signalen)} topselecties (score>={FCF_CFG['min_score']})_",
        "─────────────────────────────",
        f"🏆 *TOP {min(3, len(alle))} HOOGSTE SCORE (kan onder selectiedrempel liggen):*",
        "\n\n".join(sig_regel(s, detail=True) for s in top3),
    ]

    overige = [s for s in signalen if s not in top3]
    if overige:
        delen += ["─────────────────────────────", f"*{lbl} — overige kandidaten:*"]
        for s in overige:
            delen.append(sig_regel(s))

    delen.append("Eigen kasstroomheuristiek, geen orders of gevalideerde rendementsclaim. Jaarlijkse FCF / actuele marktkapitalisatie; provideromzetgroei en netto schuld/EBITDA kunnen andere verslagperioden gebruiken. Piotroski is informatief en gebruikt eindbalansratio’s. Payout omvat dividend plus buybacks en moet positief zijn voor een punt.")
    delen.append(f"⚙️ _FCF yield>={FCF_CFG['fcf_yield_min']:.0f}% | groeiende & consistente FCF | omzetgroei>0 | NetDebt/EBITDA<={FCF_CFG['net_debt_ebitda_max']:.0f}x | payout<=100% FCF | marktkap<=USD {FCF_CFG['marktkap_max']/1e9:.0f}B | analisten<={FCF_CFG['analisten_max']}_")
    return "\n\n".join(delen)

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    exchanges, issues, errors = {}, [], []
    for filename in bouw_bestandslijst():
        tickers = sorted(set(load_tickers_from_file(filename)))
        if tickers:
            exchanges[label_voor(filename)] = tickers
        else:
            issues.append(f"{filename}: ontbrekende of lege tickerlijst")
    cache, rates = {}, {}
    reports = []
    selected = stored = 0
    for exchange,tickers in exchanges.items():
        for ticker in tickers:
            if ticker in cache:
                continue
            try:
                cache[ticker] = analyse_ticker(ticker,fx_cache=rates)
                if cache[ticker].data_notes:
                    issues.append(f"{ticker}: {cache[ticker].data_notes}")
            except DataUnavailable as exc:
                cache[ticker] = None
                issues.append(f"{ticker}: {exc}")
            except Exception as exc:
                cache[ticker] = None
                errors.append(f"{ticker}: analyse mislukt ({type(exc).__name__}: {exc})")
            time.sleep(.15)
        all_signals = [cache[t] for t in tickers if cache[t] is not None]
        candidates = sorted((s for s in all_signals if s.score>=FCF_CFG["min_score"]),key=ranking)
        signals = candidates[:5]
        selected += len(signals)
        for rank,sig in enumerate(signals,1):
            try:
                ok = log_selectie(ticker=sig.ticker,datum=today_str(),strategie="bot_01kasstr",beurs=exchange,koers=sig.price,
                    parameters={"score":sig.score,"rank":rank,"fcf_yield":sig.fcf_yield,
                        "fcf_now":sig.fcf_now,"fcf_years":sig.fcf_years,"fcf_growing":sig.fcf_growing,
                        "fcf_consistent":sig.fcf_consistent,"rev_growth_pct":sig.rev_growth_pct,
                        "net_debt_ebitda":sig.net_debt_ebitda,"payout_pct":sig.payout_pct,
                        "dividend_yield_provider_raw":sig.div_yield,"market_cap":sig.market_cap,
                        "market_cap_usd":sig.market_cap_usd,"analisten_count":sig.analisten_count,
                        "piotroski_score":sig.piotroski_score,"piotroski_detail":sig.piotroski_detail,
                        "price_currency":sig.price_currency,"financial_currency":sig.financial_currency,
                        "fiscal_date":sig.fiscal_date,"first_fiscal_date":sig.first_fiscal_date,
                        "quote_date":sig.quote_date,"data_notes":sig.data_notes,"grafiek":yahoo_link(sig.ticker)})
                if not ok:
                    raise RuntimeError("geen opslagbevestiging")
                stored += 1
            except Exception as exc:
                errors.append(f"{sig.ticker}: database mislukt ({exc})")
        report = (f"{exchange}: {len(all_signals)}/{len(tickers)} geanalyseerd, {len(candidates)} kandidaten, {len(signals)} topselecties.\n" +
                  (format_bericht(exchange,signals,all_signals) or "Geen bruikbare analyses."))
        reports.append(report)
        print(report,flush=True)
    total = len({t for values in exchanges.values() for t in values})
    analysed = sum(sig is not None for sig in cache.values())
    if not analysed:
        errors.append("Geen bruikbare analyses")
    summary = f"{analysed}/{total} unieke tickers geanalyseerd. Database: {stored}/{selected} selecties opgeslagen. Telegram uit."
    body = "\n\n".join([summary]+reports+["Databeperkingen:"]+issues+["Fouten:"]+errors)
    print(body,flush=True)
    subject = ("[ONVOLLEDIG] " if issues or errors else "") + "Kasstroom Onderwaardering rapport " + today_str()
    if not send_email(subject,body):
        errors.append("E-mail niet verzonden")
    if errors:
        raise RuntimeError("; ".join(errors))
    print("Klaar — e-mail verzonden.",flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",nargs="?",choices=("live",),default="live")
    parser.parse_args()
    run_live_engine()
