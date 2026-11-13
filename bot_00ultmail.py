#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00ultmail.py — DIGEST MAILER (ULT) v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Draait de Traag/Snel/Hyper/MRA backtests en verstuurt een samenvatting.
"""

import os
import sys
import time
import logging
import numpy as np
import pandas as pd
from datetime import datetime
from bot_00mail import prepare_history, cached_history

from common import (
    today_str, safe_float, esc,
    download_history, load_tickers_from_file,
    send_email,
    trade_cost
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

MRA_BB_STD      = 2.2
MRA_IBS_MAX     = 0.30
MRA_SNEL_WINST  = 1.12
MRA_SNEL_MA     = 5
MRA_TRAAG_WINST = 1.25
MRA_TRAAG_MA    = 10
MRA_TRAAG_HOLD  = 5

def bereken_indicatoren_vectorized(df, s, t, use_trend_filter, is_hyper):
    p = df['Close']
    h = df['High']
    l = df['Low']
    v = df['Volume']

    f_line = p.rolling(window=s).mean() if s >= 20 else p.ewm(span=s, adjust=False).mean()
    s_line = p.rolling(window=t).mean() if t >= 50 else p.ewm(span=t, adjust=False).mean()
    ema100 = p.ewm(span=100, adjust=False).mean()
    vol_ma = v.rolling(window=20).mean()

    delta = p.diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/14, adjust=False).mean()
    rsi_val = 100 - (100 / (1 + gain / (loss + 1e-10)))

    ma20  = p.rolling(20).mean()
    std20 = p.rolling(20).std()
    lower_bb = ma20 - (MRA_BB_STD * std20)
    ibs   = (p - l) / (h - l + 1e-10)
    ma5   = p.rolling(MRA_SNEL_MA).mean()

    if is_hyper:
        change = np.sign(delta).fillna(0)
        streak = change.groupby((change != change.shift()).cumsum()).cumsum()
        s_delta = streak.diff().fillna(0)
        s_gain = s_delta.where(s_delta > 0, 0.0).rolling(2).mean()
        s_loss = (-s_delta.where(s_delta < 0, 0.0)).rolling(2).mean()
        streak_rsi = 100 - (100 / (1 + s_gain / (s_loss + 1e-10))).fillna(50)
        rsi3_gain = delta.where(delta > 0, 0.0).ewm(alpha=1/3, adjust=False).mean()
        rsi3_loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/3, adjust=False).mean()
        rsi3 = 100 - (100 / (1 + rsi3_gain / (rsi3_loss + 1e-10)))
        p_rank = delta.rolling(100).apply(lambda x: (x[:-1] < x[-1]).sum() / 99.0 * 100 if len(x) > 0 else 50, raw=True)
        rsi_val = (rsi3 + streak_rsi + p_rank) / 3

    tr = pd.concat([h - l, (h - p.shift()).abs(), (l - p.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    up   = h.diff().clip(lower=0)
    down = (-l.diff()).clip(lower=0)
    plus_di  = 100 * (up.where((up > down) & (up > 0), 0.0).ewm(alpha=1/14, adjust=False).mean() / (atr + 1e-10))
    minus_di = 100 * (down.where((down > up) & (down > 0), 0.0).ewm(alpha=1/14, adjust=False).mean() / (atr + 1e-10))
    adx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)).ewm(alpha=1/14, adjust=False).mean()

    return p, f_line, s_line, ema100, vol_ma, rsi_val, atr, adx, v, ibs, lower_bb, ma5

def simuleer_trend(data, indicators, use_trend, inzet=2500.0):
    """Beslissen na slot j, uitvoeren op opening j+1. Laatste 252 sessies."""
    close, fast, slow, trend, vol_ma, _, atr, adx, volume, *_ = indicators
    pnl, entry, high, stop, entries = 0.0, None, None, None, 0
    start = max(1, len(data) - 252)
    for i in range(start, len(data)):
        j = i - 1
        fill = float(data["Open"].iloc[i])
        if entry is None:
            crossed = j >= 1 and fast.iloc[j] > slow.iloc[j] and fast.iloc[j-1] <= slow.iloc[j-1]
            if (crossed and adx.iloc[j] > 15 and volume.iloc[j] > vol_ma.iloc[j] * 0.6
                    and (not use_trend or close.iloc[j] > trend.iloc[j])
                    and np.isfinite(atr.iloc[j]) and atr.iloc[j] > 0):
                entry, high = fill, fill
                stop = fill - 2 * atr.iloc[j]
                entries += 1
                pnl -= trade_cost(inzet)
        else:
            # De reeds geldende stop wordt eerst getoetst; pas daarna kan de
            # vorige slotkoers de stop verhogen voor de volgende beslisdag.
            if close.iloc[j] < stop or fast.iloc[j] < slow.iloc[j]:
                gross = inzet * fill / entry
                pnl += gross - inzet - trade_cost(gross)
                entry = None
            else:
                high = max(high, close.iloc[j])
                if np.isfinite(atr.iloc[j]) and atr.iloc[j] > 0:
                    stop = max(stop, high - 2 * atr.iloc[j])
    if entry is not None:
        pnl += inzet * (close.iloc[-1] / entry - 1)
    return pnl, entries


def simuleer_mra(data, close, lower_bb, ibs, ma, target, min_hold=0, inzet=2500.0):
    """Zelfde volgende-opening uitvoering, zonder intraday-verkoop te verzinnen."""
    pnl, entry, entry_i, entries = 0.0, None, None, 0
    for i in range(max(1, len(data) - 252), len(data)):
        j = i - 1
        fill = float(data["Open"].iloc[i])
        if entry is None:
            if close.iloc[j] < lower_bb.iloc[j] and ibs.iloc[j] < MRA_IBS_MAX:
                entry, entry_i = fill, i
                entries += 1
                pnl -= trade_cost(inzet)
        elif (i - entry_i >= min_hold
                and (close.iloc[j] > ma.iloc[j] or close.iloc[j] > entry * target)):
            gross = inzet * fill / entry
            pnl += gross - inzet - trade_cost(gross)
            entry = None
    if entry is not None:
        pnl += inzet * (close.iloc[-1] / entry - 1)
    return pnl, entries


def voer_lijst_uit(bestandsnaam, label, naam_sector, download_cache=None, status=None):
    if status is None:
        status = {"analyses": 0, "fouten": 0, "overgeslagen": 0}
    if not os.path.exists(bestandsnaam):
        raise FileNotFoundError(f"Tickerlijst ontbreekt: {bestandsnaam}")
    tickers = load_tickers_from_file(bestandsnaam)
    if not tickers:
        raise ValueError(f"Tickerlijst leeg: {bestandsnaam}")
    cache = download_cache if download_cache is not None else {}
    raw = cached_history(tickers, cache)
    keys = ("T", "S", "HT", "HS", "MRAS", "MRAT")
    results = {key: 0.0 for key in keys}
    trades = {key: 0 for key in keys}
    signals = {key: [] for key in keys}
    diagnostics, success, failed, skipped = [], 0, 0, 0
    inzet = 2500.0
    logger.info("Start analyse voor %s met %s tickers.", naam_sector, len(tickers))
    for ticker in tickers:
        try:
            frame = raw.loc[raw["Ticker"] == ticker] if not raw.empty else pd.DataFrame()
            reason = None
            if frame.empty:
                reason = "geen koersdata na download en eventuele herpoging"
            else:
                data = prepare_history(frame)
                if len(data) < 252:
                    reason = f"{len(data)} geldige koersrijen; minimaal 252 vereist"
                elif data.index[-1].normalize() < pd.Timestamp(today_str()) - pd.Timedelta(days=7):
                    reason = f"verouderde koersdata, laatste datum {data.index[-1].date()}"
            if reason:
                skipped += 1
                status["overgeslagen"] += 1
                diagnostics.append(f"{ticker}: {reason}")
                logger.warning("%s / %s: %s", naam_sector, ticker, reason)
                continue

            # Pas na een volledig geslaagde ticker worden P/L en signalen aan
            # het rapport toegevoegd. Een fout laat geen halve resultaten na.
            local_results, local_trades = {}, {}
            local_signals = {key: [] for key in keys}
            for key, fast, slow, use_trend, hyper in (
                ("T", 50, 200, True, False), ("S", 20, 50, True, False),
                ("HT", 9, 21, True, True), ("HS", 9, 21, False, True),
            ):
                indicators = bereken_indicatoren_vectorized(data, fast, slow, use_trend, hyper)
                local_results[key], local_trades[key] = simuleer_trend(data, indicators, use_trend, inzet)
                close, f, s, trend, vma, rsi, atr, adx, volume, *_ = indicators
                price = close.iloc[-1]
                if (f.iloc[-1] > s.iloc[-1] and f.iloc[-2] <= s.iloc[-2]
                        and adx.iloc[-1] > 15 and volume.iloc[-1] > vma.iloc[-1] * 0.6
                        and (not use_trend or price > trend.iloc[-1])):
                    action = "KOOP"
                elif f.iloc[-1] < s.iloc[-1] and f.iloc[-2] >= s.iloc[-2]:
                    action = "VERKOOP"
                else:
                    action = None
                if action:
                    local_signals[key].append(
                        f"• {ticker}: {action} | koers {price:.2f} (noteringsvaluta) | "
                        f"ATR {atr.iloc[-1]:.2f} | {'CRSI' if hyper else 'RSI'} {rsi.iloc[-1]:.1f} | "
                        f"SL {price - 2 * atr.iloc[-1]:.2f} | https://finance.yahoo.com/quote/{ticker}"
                    )
            indicators = bereken_indicatoren_vectorized(data, 50, 200, True, False)
            close, _, _, _, _, rsi, _, _, _, ibs, lower_bb, ma_fast = indicators
            local_results["MRAS"], local_trades["MRAS"] = simuleer_mra(
                data, close, lower_bb, ibs, ma_fast, MRA_SNEL_WINST, inzet=inzet)
            local_results["MRAT"], local_trades["MRAT"] = simuleer_mra(
                data, close, lower_bb, ibs, close.rolling(MRA_TRAAG_MA).mean(),
                MRA_TRAAG_WINST, min_hold=MRA_TRAAG_HOLD, inzet=inzet)
            if close.iloc[-1] < lower_bb.iloc[-1] and ibs.iloc[-1] < MRA_IBS_MAX:
                for key in ("MRAS", "MRAT"):
                    local_signals[key].append(
                        f"• {ticker}: MRA instapsignaal | koers {close.iloc[-1]:.2f} "
                        f"(noteringsvaluta) | RSI {rsi.iloc[-1]:.1f} | https://finance.yahoo.com/quote/{ticker}")
            if not all(np.isfinite(value) for value in local_results.values()):
                raise ValueError("Niet-eindige P/L berekend")
            for key in keys:
                results[key] += local_results[key]
                trades[key] += local_trades[key]
                signals[key].extend(local_signals[key])
            success += 1
            status["analyses"] += len(keys)
        except Exception as exc:
            failed += 1
            status["fouten"] += 1
            diagnostics.append(f"{ticker}: analysefout ({type(exc).__name__}: {exc})")
            logger.exception("Analysefout in %s / %s", naam_sector, ticker)
    if success == 0:
        status["fouten"] += 1
        diagnostics.append("Geen geslaagde analyses in deze sector; geen betrouwbare P/L of signaaluitkomst.")
    coverage = (f"Datadekking: {len(tickers)} gevraagd | {success} geanalyseerd "
                f"(elk 6 strategieën) | {skipped} overgeslagen | {failed} analysefouten")
    logger.info("%s: %s", naam_sector, coverage)
    lines = [f"{label} {naam_sector} RAPPORT ULT", datetime.now().strftime("%d/%m/%Y %H:%M"),
             coverage, "STATUS: ONVOLLEDIG" if diagnostics else "STATUS: VOLLEDIG",
             "P/L: som van afzonderlijke posities met 2.500 rekeneenheden inzet; geen portefeuillewaarde.",
             "Alle zes backtests: laatste 252 sessies, signaal na slot, uitvoering volgende opening.",
             "Open posities gewaardeerd tegen laatste slot; geen fictieve sluitingskosten.",
             "Geen valutaomrekening of FX-rendement. Rekeneenheden zijn geen werkelijk eurovermogen."]
    names = {"T": "Traag (50/200)", "S": "Snel (20/50)", "HT": "Hyper Trend",
             "HS": "Hyper Scalp", "MRAS": "MRA Snel", "MRAT": "MRA Traag"}
    for key in keys:
        result = f"{results[key]:+,.2f} rekeneenheden ({trades[key]} entries)" if success else "niet beschikbaar"
        lines.extend([f"{names[key]} P/L: {result}", f"SIGNALEN {names[key]}:"])
        lines.extend(signals[key] or (["Geen actie bij de geanalyseerde tickers"] if success else ["Geen betrouwbare uitkomst"]))
    lines.extend(["PARAMETERS:", "Trend: ADX>15 | Vol>0.6x MA20 | EMA100-filter | trailing stop 2x ATR",
                  f"MRA instap: BB {MRA_BB_STD}σ | IBS<{MRA_IBS_MAX}",
                  f"MRA Snel: MA{MRA_SNEL_MA} of +{(MRA_SNEL_WINST-1)*100:.0f}%",
                  f"MRA Traag: minimaal {MRA_TRAAG_HOLD} handelsintervallen; MA{MRA_TRAAG_MA} of +{(MRA_TRAAG_WINST-1)*100:.0f}%",
                  "Kosten: gedeeld kostenmodel, berekend op werkelijke instap-/uitstapnotional."])
    if diagnostics:
        lines.extend(["OVERGESLAGEN TICKERS EN FOUTEN:", *diagnostics])
    return "\n".join(lines) + "\n\n" + "=" * 40 + "\n\n"


def main():
    sectoren = {"01": "Hoogland", "02": "Macrotrends", "03": "Beursbrink",
                "04": "Benelux", "05": "Parijs", "06": "Power & AI",
                "07": "Metalen", "08": "Defensie", "09": "Varia"}
    report = "FULL GLOBAL SCAN COLLECTIVE REPORT (BOT 00 ULT)\n\n"
    status = {"analyses": 0, "fouten": 0, "overgeslagen": 0}
    cache = {}
    logger.info("Start globale scan...")
    for nr, name in sectoren.items():
        try:
            logger.info("Verwerken sector: %s", name)
            report += voer_lijst_uit(f"tickers_{nr}.txt", nr, name, download_cache=cache, status=status)
        except Exception as exc:
            status["fouten"] += 1
            logger.exception("Fout in sector %s", name)
            report += f"\nSECTOR {name}: MISLUKT ({type(exc).__name__}: {exc})\n"
        time.sleep(2)
    partial = status["fouten"] > 0 or status["overgeslagen"] > 0 or status["analyses"] == 0
    report += (f"\nTotaal: {status['analyses']} geslaagde ticker/strategie-analyses, "
               f"{status['fouten']} fouten, {status['overgeslagen']} overgeslagen ticker/sector-combinaties.\n")
    subject = f"{'[ONVOLLEDIG] ' if partial else ''}Trading Rapport Bot 00 Ult: {today_str()}"
    if not send_email(subject, report, html_body=False):
        logger.error("E-mail NIET verstuurd; controleer e-mailsecrets en verzendfouten.")
        return 1
    logger.info("E-mail succesvol verstuurd. Telegram staat uit.")
    if status["fouten"] or not status["analyses"]:
        logger.error("Scan onvolledig door analyse- of sectorfouten; zie het verzonden rapport.")
        return 1
    logger.info("Scan voltooid%s.", " met gemelde ontbrekende data" if partial else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
