#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00supervar.py — SUPERBOT v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Combineert Trend Template, VCP en RS Rating met een volatiliteitsfilter.
"""

import os
import sys
import math
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from common import (
    today_str, safe_float, yahoo_link,
    download_history, load_tickers_from_file,
    compute_atr_series,
    send_telegram, send_email,
    SLIPPAGE_PCT
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


BEURS_NAMEN = {
    "041": "Benelux",         "042": "Parijs",       "043": "Frankfurt",
    "044": "Spanje/Portugal", "045": "Londen",       "046": "Milaan",
    "047": "Toronto",         "048": "Nasdaq/NYSE",  "049": "Stockholm",
    "050": "Zurich",          "051": "Warschau",     "052": "Oslo",
    "053": "Kopenhagen",      "054": "Helsinki",     "055": "CBoe",
    "056": "NYSE int",        "057": "NYSE",         "058": "TSXV",
    "059": "Oostenrijk Slovenie Slovakije",
}
REEKS_START = 41
REEKS_EINDE = 59

NOORD_AMERIKA_BEURZEN = {"047", "048", "055", "056", "057", "058"}

SUPER_CFG = {
    "ma_fast":             50,
    "ma_mid":              150,
    "ma_slow":             200,
    "max_from_high_pct":   25.0,
    "min_from_low_pct":    30.0,
    "rs_min":              70,
    "vcp_lookback":        60,
    "vol_contraction_pct": 20.0,
    "volume_dry_pct":      30.0,
    "pivot_lookback":      20,
    "pivot_breakout_vol":  1.5,
    "stop_pct":            8.0,
    "min_vcp_score":       2,
    "volpct_lookback":     20,
}

def pad_export(g: str) -> str:
    return f"tickers_{g}x.txt"

# ============================================================
# INDICATOREN & TREND TEMPLATE
# ============================================================

def prepare_universe(df, tickers):
    """Accept only usable daily OHLCV histories; retain explicit skip reasons."""
    frames, skipped = [], {}
    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    today = pd.Timestamp(today_str())
    for ticker in sorted(set(tickers)):
        g = df.loc[df["Ticker"] == ticker].copy() if "Ticker" in df else pd.DataFrame()
        if g.empty or any(c not in g for c in required):
            skipped[ticker] = "geen volledige OHLCV-data"
            continue
        g["Date"] = pd.to_datetime(g["Date"], errors="coerce")
        if g["Date"].dt.tz is not None:
            g["Date"] = g["Date"].dt.tz_localize(None)
        g["Date"] = g["Date"].dt.normalize()
        for c in required[1:]:
            g[c] = pd.to_numeric(g[c], errors="coerce").replace([np.inf, -np.inf], np.nan)
        valid = g[required].notna().all(axis=1) & (g[["Open", "High", "Low", "Close"]] > 0).all(axis=1)
        valid &= (g["Volume"] >= 0) & (g["High"] >= g[["Open", "Close", "Low"]].max(axis=1))
        valid &= (g["Low"] <= g[["Open", "Close"]].min(axis=1)) & (g["Date"] < today)
        g = g.loc[valid].sort_values("Date").drop_duplicates("Date", keep="last")
        if len(g) < 253:
            skipped[ticker] = f"{len(g)} geldige afgeronde koersdagen; minimaal 253 vereist"
        elif (today - g["Date"].iloc[-1]).days > 7:
            skipped[ticker] = "laatste afgeronde koers ouder dan 7 dagen"
        elif g["Volume"].tail(20).sum() <= 0:
            skipped[ticker] = "geen bruikbaar recent handelsvolume"
        else:
            frames.append(g)
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), skipped

def add_indicators(df: pd.DataFrame, universe_rs: Dict[str, float]) -> pd.DataFrame:
    parts = []
    for ticker, group in df.groupby("Ticker", sort=False):
        g     = group.sort_values("Date").copy()
        close = g["Close"]
        high  = g["High"]
        low   = g["Low"]
        vol   = g["Volume"]

        g["MA50"]        = close.rolling(SUPER_CFG["ma_fast"]).mean()
        g["MA150"]       = close.rolling(SUPER_CFG["ma_mid"]).mean()
        g["MA200"]       = close.rolling(SUPER_CFG["ma_slow"]).mean()
        g["MA200_slope"] = g["MA200"].diff(20)

        g["ATR14"]   = compute_atr_series(high, low, close, 14)
        g["VolMA20"] = vol.shift(1).rolling(20).mean()
        g["High52w"] = high.rolling(252).max()
        g["Low52w"]  = low.rolling(252).min()
        g["RS"]      = universe_rs.get(ticker, float("nan"))

        dagrendement = close.pct_change()
        g["VolPct"] = (
            dagrendement.rolling(SUPER_CFG["volpct_lookback"], min_periods=10).std()
            * math.sqrt(252) * 100
        )
        g["Ticker"] = ticker
        parts.append(g)

    if not parts:
        return df
    return pd.concat(parts).sort_values(["Ticker", "Date"]).reset_index(drop=True)

def compute_rs_ratings(df: pd.DataFrame) -> Dict[str, float]:
    perf = {}
    for ticker, group in df.groupby("Ticker", sort=False):
        g = group.sort_values("Date")
        if len(g) < 253:
            continue
        prices = [safe_float(g["Close"].iloc[i]) for i in (-1, -64, -253)]
        if not all(math.isfinite(v) and v > 0 for v in prices):
            continue
        now, quarter, year = prices
        perf[ticker] = .4 * (now / quarter - 1) + .6 * (now / year - 1)
    if not perf:
        return {}
    values = pd.Series(perf)
    if len(values) == 1:
        return {values.index[0]: 50.0}
    return (1 + 98 * (values.rank(method="average") - 1) / (len(values) - 1)).to_dict()

def check_trend_template(row: pd.Series) -> Tuple[bool, List[str]]:
    close  = safe_float(row.get("Close"))
    ma50   = safe_float(row.get("MA50"))
    ma150  = safe_float(row.get("MA150"))
    ma200  = safe_float(row.get("MA200"))
    ma200s = safe_float(row.get("MA200_slope"))
    h52    = safe_float(row.get("High52w"))
    l52    = safe_float(row.get("Low52w"))
    rs     = safe_float(row.get("RS"), 0.0)

    if not all(math.isfinite(v) and v > 0 for v in (close, ma50, ma150, ma200, h52, l52)) or not all(math.isfinite(v) for v in (ma200s, rs)):
        return False, ["✗ ongeldige/onvolledige trendindicatoren"]

    checks, passed = [], True

    def chk(condition, ok_msg, fail_msg):
        nonlocal passed
        if condition:
            checks.append(f"✓ {ok_msg}")
        else:
            checks.append(f"✗ {fail_msg}")
            passed = False

    chk(not math.isnan(close) and not math.isnan(ma150) and not math.isnan(ma200) and close > ma150 and close > ma200, "Close>MA150/MA200", "Close niet boven MA150/MA200")
    chk(not math.isnan(ma150) and not math.isnan(ma200) and ma150 > ma200, "MA150>MA200", "MA150 niet boven MA200")
    chk(not math.isnan(ma200s) and ma200s > 0, "MA200 stijgt", "MA200 daalt/vlak")
    chk(not math.isnan(ma50) and not math.isnan(ma150) and not math.isnan(ma200) and ma50 > ma150 and ma50 > ma200, "MA50>MA150/MA200", "MA50 niet boven MA150/MA200")
    chk(not math.isnan(close) and not math.isnan(ma50) and close > ma50, "Close>MA50", "Close niet boven MA50")

    if not math.isnan(h52) and h52 > 0:
        pct_from_high = (h52 - close) / h52 * 100
        chk(pct_from_high <= SUPER_CFG["max_from_high_pct"], f"{pct_from_high:.1f}% onder 52w high", f"{pct_from_high:.1f}% onder 52w high (>25%)")
    else:
        checks.append("✗ geen 52w high data")
        passed = False

    if not math.isnan(l52) and l52 > 0:
        pct_from_low = (close - l52) / l52 * 100
        chk(pct_from_low >= SUPER_CFG["min_from_low_pct"], f"{pct_from_low:.1f}% boven 52w low", f"{pct_from_low:.1f}% boven 52w low (<30%)")
    else:
        checks.append("✗ geen 52w low data")
        passed = False

    chk(rs >= SUPER_CFG["rs_min"], f"RS={rs:.0f} (>=70)", f"RS={rs:.0f} (<70)")
    return passed, checks

# ============================================================
# VCP DETECTIE
# ============================================================

def detect_vcp(g: pd.DataFrame) -> Tuple[int, List[str], float, float]:
    score, details = 0, []
    close, volume, atr, vol_ma = g["Close"], g["Volume"], g["ATR14"], g["VolMA20"]

    if len(g) < SUPER_CFG["vcp_lookback"] + 10:
        return 0, ["onvoldoende data"], float("nan"), float("nan")

    recent      = g.iloc[-SUPER_CFG["vcp_lookback"]:]
    very_recent = g.iloc[-SUPER_CFG["pivot_lookback"]-1:-1]

    atr_now   = safe_float(atr.iloc[-1])
    atr_start = safe_float(atr.iloc[-SUPER_CFG["vcp_lookback"]])
    if not math.isnan(atr_now) and not math.isnan(atr_start) and atr_start > 0:
        contraction = (atr_start - atr_now) / atr_start * 100
        if contraction >= SUPER_CFG["vol_contraction_pct"]:
            score += 1
            details.append(f"✓ ATR contractie {contraction:.1f}%")
        else:
            details.append(f"✗ ATR contractie {contraction:.1f}%")
    else:
        details.append("✗ ATR data onvoldoende")

    vol_now, vol_mean = safe_float(volume.iloc[-1]), safe_float(vol_ma.iloc[-1])
    if not math.isnan(vol_now) and not math.isnan(vol_mean) and vol_mean > 0:
        vol_ratio = vol_now / vol_mean * 100
        if vol_ratio <= (100 - SUPER_CFG["volume_dry_pct"]):
            score += 1
            details.append(f"✓ Volume droogvalt {vol_ratio:.0f}% van gem.")
        else:
            details.append(f"✗ Volume {vol_ratio:.0f}% van gem.")
    else:
        details.append("✗ Volume data onvoldoende")

    n = len(recent)
    third = n // 3
    if third >= 5:
        r1, r2, r3 = recent.iloc[:third]["Close"], recent.iloc[third:2*third]["Close"], recent.iloc[2*third:]["Close"]
        range1, range2, range3 = float(r1.max()-r1.min()), float(r2.max()-r2.min()), float(r3.max()-r3.min())
        if range1 > 0 and range2 < range1 and range3 < range2:
            score += 1
            details.append(f"✓ Pullbacks krimpen: {range1:.2f}->{range2:.2f}->{range3:.2f}")
        else:
            details.append(f"✗ Pullbacks krimpen niet")
    else:
        details.append("✗ Onvoldoende data voor pullback analyse")

    pivot_high      = float(very_recent["High"].max())
    current         = safe_float(close.iloc[-1])
    vol_recent_mean = safe_float(vol_ma.iloc[-1])
    vol_today       = safe_float(volume.iloc[-1])

    breakout = (not math.isnan(current) and current > pivot_high and not math.isnan(vol_today) and not math.isnan(vol_recent_mean) and vol_recent_mean > 0 and vol_today >= vol_recent_mean * SUPER_CFG["pivot_breakout_vol"])
    if breakout:
        score += 1
        details.append(f"✓ Pivot breakout boven {pivot_high:.2f}")
    else:
        details.append(f"✗ Geen pivot breakout (pivot={pivot_high:.2f})")

    stop_prijs  = max(float(very_recent["Low"].min()), current * (1 - SUPER_CFG["stop_pct"] / 100))
    pivot_prijs = pivot_high
    return score, details, pivot_prijs, stop_prijs

# ============================================================
# SIGNAAL
# ============================================================

@dataclass
class SuperSignaal:
    ticker:        str
    price:         float
    vcp_score:     int
    pivot:         float
    stop:          float
    rs:            float
    pct_from_high: float
    vol_pct:       float
    total_score:   float

def analyse_ticker(ticker: str, g: pd.DataFrame, vol_drempel: float) -> Optional[SuperSignaal]:
    try:
        g = g.sort_values("Date").copy()
        if len(g) < 253:
            return None

        last = g.iloc[-1]
        current_price = safe_float(last.get("Close"))
        if current_price <= 0 or not math.isfinite(current_price):
            return None

        trend_passed, _ = check_trend_template(last)
        if not trend_passed:
            return None

        vcp_score, _, pivot, stop = detect_vcp(g)
        if vcp_score < SUPER_CFG["min_vcp_score"]:
            return None

        vol_pct = safe_float(last.get("VolPct"))
        if not math.isfinite(vol_pct) or not math.isfinite(vol_drempel) or vol_pct < vol_drempel:
            return None

        if not all(math.isfinite(v) for v in (pivot, stop)) or not 0 < stop < current_price:
            return None

        h52 = safe_float(last.get("High52w"))
        rs  = safe_float(last.get("RS"), 0.0)
        pct_from_high = ((h52 - current_price) / h52 * 100) if h52 > 0 else 0.0

        total_score = (rs * 0.4 + vcp_score * 15 + (25 - pct_from_high) * 0.5 + min(vol_pct, 100) * 0.3)

        return SuperSignaal(
            ticker=ticker, price=current_price, vcp_score=vcp_score,
            pivot=pivot, stop=stop, rs=rs,
            pct_from_high=pct_from_high, vol_pct=vol_pct,
            total_score=total_score,
        )
    except Exception as e:
        raise RuntimeError(f"{ticker}: analyse mislukt: {e}") from e

# ============================================================
# OUTPUT
# ============================================================

def _vcp_bar(score: int) -> str:
    return "█" * score + "░" * (4 - score) + f" {score}/4"

def format_bericht(beurs_naam: str, s: SuperSignaal, portfolio_waarde: Optional[float] = None) -> str:
    nu = today_str()
    return "\n\n".join([
        f"🏆 *SUPERBOT — {beurs_naam}*",
        f"_{nu} | Trend Template + VCP + Volatiliteitsfilter_",
        "─────────────────────────────",
        f"• `{s.ticker}` VCP:{_vcp_bar(s.vcp_score)} RS:{s.rs:.0f} Vol:{s.vol_pct:.0f}%\n"
        f"  Koers {s.price:.4f} {yahoo_link(s.ticker)}\n"
        f"  {s.pct_from_high:.1f}% onder 52w high | Score:{s.total_score:.2f}\n"
        f"  Referentie-entry: {s.price * (1 + SLIPPAGE_PCT):.4f} | Stop: {s.stop:.4f} | Pivot: {s.pivot:.4f}\n"
        "  Prijzen in oorspronkelijke koerseenheid; geen valutaomrekening of positiegrootte. "
        "Pivot is een uitbraakreferentie, geen koersdoel.",
        f"⚙️ _Stop max {SUPER_CFG['stop_pct']:.0f}% | RS>={SUPER_CFG['rs_min']} | VCP>={SUPER_CFG['min_vcp_score']}/4 | Vol boven beursmediaan_",
    ])

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine(regio: str = "alle"):
    if regio not in ("alle", "europa", "namerika"):
        raise ValueError("Regio moet alle, europa of namerika zijn")
    print(f"SUPERBOT — {today_str()} — {regio}")
    exchanges, missing = {}, []
    for nr in range(REEKS_START, REEKS_EINDE + 1):
        code = f"{nr:03d}"
        is_na = code in NOORD_AMERIKA_BEURZEN
        if (regio == "europa" and is_na) or (regio == "namerika" and not is_na):
            continue
        path = pad_export(code)
        tickers = load_tickers_from_file(path)
        if tickers:
            exchanges[f"{code} {BEURS_NAMEN.get(code, code)}"] = sorted(set(tickers))
        else:
            missing.append(path)
    tickers = sorted({t for group in exchanges.values() for t in group})
    if not tickers:
        raise RuntimeError("Geen tickerlijsten beschikbaar voor deze regio")
    data, skipped = prepare_universe(download_history(tickers, period="2y"), tickers)
    coverage = f"Datadekking: {len(tickers)} gevraagd | {len(tickers)-len(skipped)} analyseerbaar | {len(skipped)} overgeslagen"
    print(coverage)
    for t, reason in skipped.items():
        print(f"[SKIP:data] {t}: {reason}")
    for path in missing:
        print(f"[WARN] ontbrekende/lege lijst: {path}")
    if not data.empty:
        data = add_indicators(data, compute_rs_ratings(data))
    reports, errors = [], []
    saved, selected = 0, 0
    for exchange, group_tickers in exchanges.items():
        group = data.loc[data["Ticker"].isin(group_tickers)] if not data.empty else data
        if group.empty:
            reports.append(f"{exchange}: geen analyseerbare data")
            continue
        last = group.sort_values("Date").groupby("Ticker").tail(1)
        vols = last["VolPct"].replace([np.inf, -np.inf], np.nan).dropna()
        if vols.empty:
            errors.append(f"{exchange}: geen volatiliteitsdata")
            continue
        threshold = float(vols.median())
        candidates = []
        for ticker, history in group.groupby("Ticker", sort=False):
            try:
                signal = analyse_ticker(ticker, history, threshold)
                if signal is not None:
                    candidates.append(signal)
            except Exception as exc:
                errors.append(str(exc))
        print(f"{exchange}: {len(last)}/{len(group_tickers)} geanalyseerd | {len(candidates)} kandidaten | volatiliteitsmediaan {threshold:.2f}%")
        if not candidates:
            reports.append(f"{exchange}: geen kandidaat binnen de beschikbare data")
            continue
        winner = sorted(candidates, key=lambda item: (-item.total_score, item.ticker))[0]
        selected += 1
        saved += bool(log_selectie(ticker=winner.ticker, datum=today_str(), strategie="bot_00supervar",
            beurs=exchange, koers=winner.price, parameters={
                "total_score": winner.total_score, "pivot": winner.pivot, "stop": winner.stop,
                "rs": winner.rs, "pct_from_high": winner.pct_from_high, "vol_pct": winner.vol_pct,
                "vol_drempel_beurs": threshold, "grafiek": yahoo_link(winner.ticker),
                "price_unit": "native_quote_unit", "signal_date": str(last.set_index("Ticker").loc[winner.ticker, "Date"].date()),
                "rs_universe": regio}))
        reports.append(format_bericht(exchange, winner))
    partial = bool(skipped or missing or errors or saved < selected)
    body = coverage + f"\nDatabase: {saved}/{selected} selecties opgeslagen\n\n" + "\n\n".join(reports)
    body += "\n\nDataproblemen:\n" + "\n".join(f"{t}: {r}" for t,r in skipped.items())
    body += "\nOntbrekende/lege lijsten: " + (", ".join(missing) or "geen")
    body += "\nAnalysefouten: " + ("; ".join(errors) or "geen")
    body += "\nModel: eigen RS-score (40% 63 sessies, 60% 252 sessies), rang binnen deze regio. "
    body += "VCP is een vierdelige heuristiek; volatiliteit op of boven beursmediaan. "
    body += "Alleen koersdagen vóór vandaag; geen garantie van een uitvoerbare entry."
    if os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        for report in reports:
            if not send_telegram(report, parse_mode="Markdown"):
                print("[WARN] Telegram-verzending mislukt")
    else:
        print("Telegram uitgeschakeld (geen configuratie)")
    print(f"Database: {saved}/{selected} selecties opgeslagen")
    if not send_email(f"{'[ONVOLLEDIG] ' if partial else ''}Superbot {regio} {today_str()} — {selected} picks", body):
        raise RuntimeError("E-mail niet verstuurd")
    print("E-mail verstuurd")
    if data.empty or errors or saved < selected:
        raise RuntimeError("Scan/opslag onvolledig; zie rapport")
    print("Klaar met gemelde databeperkingen." if partial else "Klaar.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "alle"
    if arg not in ("europa", "namerika", "alle"):
        sys.exit("Gebruik: python bot_00supervar.py [alle|europa|namerika]")
    run_live_engine(arg)

