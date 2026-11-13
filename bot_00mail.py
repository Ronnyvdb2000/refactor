#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00mail.py — DIGEST MAILER v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Draait de Traag/Snel/Hyper backtests en verstuurt een samenvatting per mail.
"""

import os
import sys
import time
import logging
import numpy as np
import pandas as pd
from datetime import datetime

from common import (
    today_str, safe_float, esc,
    download_history, load_tickers_from_file,
    send_email,
    trade_cost
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def prepare_history(frame):
    """Gebruik uitsluitend echte, complete dagkoersen voor de berekeningen."""
    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise ValueError(f"Ontbrekende koerskolommen: {', '.join(missing)}")
    data = frame[required].copy()
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    if data["Date"].dt.tz is not None:
        data["Date"] = data["Date"].dt.tz_localize(None)
    for col in required[1:]:
        data[col] = pd.to_numeric(data[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    data = data.dropna(subset=required)
    data = data.loc[(data[["Open", "High", "Low", "Close"]] > 0).all(axis=1) & (data["Volume"] >= 0)]
    data = data.loc[data["High"] >= data["Low"]]
    return data.sort_values("Date").drop_duplicates("Date", keep="last").set_index("Date")


def cached_history(tickers, cache):
    """Deel downloads, inclusief ontbrekende tickers, tussen sectoren."""
    missing = [ticker for ticker in tickers if ticker not in cache]
    if missing:
        raw = download_history(missing, period="5y")
        for ticker in missing:
            cache[ticker] = raw.loc[raw["Ticker"] == ticker].copy() if not raw.empty else pd.DataFrame()
    frames = [cache[ticker] for ticker in tickers if not cache[ticker].empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

def bereken_indicatoren_vectorized(df, s, t, use_trend_filter, is_hyper):
    p = df['Close']
    h = df['High']
    l = df['Low']
    v = df['Volume']

    f_line = p.rolling(window=s).mean() if s >= 20 else p.ewm(span=s, adjust=False).mean()
    s_line = p.rolling(window=t).mean() if t >= 50 else p.ewm(span=t, adjust=False).mean()
    ema200 = p.ewm(span=200, adjust=False).mean()
    vol_ma = v.rolling(window=20).mean()

    delta = p.diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rsi_val = 100 - (100 / (1 + (gain / (loss + 1e-10))))

    if is_hyper:
        rsi3_gain = (delta.where(delta > 0, 0)).rolling(3).mean()
        rsi3_loss = (-delta.where(delta < 0, 0)).rolling(3).mean()
        rsi3 = 100 - (100 / (1 + (rsi3_gain / (rsi3_loss + 1e-10))))
        
        change = np.sign(delta)
        streak = change.groupby((change != change.shift()).cumsum()).cumsum()
        s_delta = streak.diff()
        s_gain = (s_delta.where(s_delta > 0, 0)).rolling(2).mean()
        s_loss = (-s_delta.where(s_delta < 0, 0)).rolling(2).mean()
        streak_rsi = 100 - (100 / (1 + (s_gain / (s_loss + 1e-10))))
        
        p_rank = delta.rolling(100).apply(lambda x: (x < x.iloc[-1]).sum() / 99.0 * 100, raw=False)
        rsi_val = (rsi3 + streak_rsi + p_rank) / 3

    tr = pd.concat([h-l, abs(h-p.shift()), abs(l-p.shift())], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    up, down = h.diff().clip(lower=0), (-l.diff()).clip(lower=0)
    tr14 = tr.rolling(14).sum()
    plus_di = 100 * (up.rolling(14).sum() / (tr14 + 1e-10))
    minus_di = 100 * (down.rolling(14).sum() / (tr14 + 1e-10))
    adx = (100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)).rolling(14).mean()

    return p, f_line, s_line, ema200, vol_ma, rsi_val, atr, adx, v

def voer_lijst_uit(bestandsnaam, label, naam_sector, download_cache=None, status=None):
    if status is None:
        status = {"analyses": 0, "fouten": 0, "overgeslagen": 0}
    if not os.path.exists(bestandsnaam):
        raise FileNotFoundError(f"Tickerlijst ontbreekt: {bestandsnaam}")
    nu = datetime.now().strftime("%d/%m/%Y %H:%M")
    tickers = load_tickers_from_file(bestandsnaam)
    if not tickers:
        raise ValueError(f"Tickerlijst leeg: {bestandsnaam}")
    cache = download_cache if download_cache is not None else {}
    raw_df = cached_history(tickers, cache)
    histories = {}
    diagnostics = []
    for ticker in tickers:
        try:
            frame = raw_df.loc[raw_df["Ticker"] == ticker] if not raw_df.empty else pd.DataFrame()
            if frame.empty:
                reason = "geen koersdata na download en eventuele herpoging"
            else:
                data = prepare_history(frame)
                if len(data) < 252:
                    reason = f"{len(data)} geldige koersrijen; minimaal 252 vereist"
                elif data.index[-1].normalize() < pd.Timestamp(today_str()) - pd.Timedelta(days=7):
                    reason = f"verouderde koersdata, laatste datum {data.index[-1].date()}"
                else:
                    histories[ticker] = data
                    continue
            status["overgeslagen"] += 1
            diagnostics.append(f"{ticker}: {reason}")
            logger.warning("%s / %s: %s", naam_sector, ticker, reason)
        except Exception as exc:
            status["fouten"] += 1
            diagnostics.append(f"{ticker}: gegevensfout ({type(exc).__name__}: {exc})")
            logger.exception("Gegevensfout in %s / %s", naam_sector, ticker)

    inzet = 2500.0
    res = {"T": 0, "S": 0, "HT": 0, "HS": 0}
    num_trades = {"T": 0, "S": 0, "HT": 0, "HS": 0}
    sig = {"T": [], "S": [], "HT": [], "HS": []}
    analysed = {key: 0 for key in res}
    failures = {key: 0 for key in res}

    for strat_key, (s_per, t_per, use_tr, is_hyp) in [
        ("T",(50,200,True,False)), ("S",(20,50,True,False)), 
        ("HT",(9,21,True,True)), ("HS",(9,21,False,True))
    ]:
        for t, t_data in histories.items():
            entries_before = num_trades[strat_key]
            signals_before = len(sig[strat_key])
            try:
                p, f, s_line, e200, v_ma, rsi, atr, adx, vol = bereken_indicatoren_vectorized(t_data, s_per, t_per, use_tr, is_hyp)
                
                p_bt, f_bt, s_bt = p.iloc[-252:], f.iloc[-252:], s_line.iloc[-252:]
                e_bt, v_bt, v_ma_bt = e200.iloc[-252:], vol.iloc[-252:], v_ma.iloc[-252:]
                atr_bt, adx_bt = atr.iloc[-252:], adx.iloc[-252:]
                
                profit, pos, instap, high_p, sl_val = 0.0, False, 0.0, 0.0, 0.0
                # Signals use the previous completed candle; orders execute
                # at the next session's open, never at the signal close.
                opens = t_data['Open'].reindex(p_bt.index)
                for i in range(1, len(p_bt)):
                    j = i - 1
                    fill = safe_float(opens.iloc[i])
                    if not np.isfinite(fill) or fill <= 0:
                        continue
                    if not pos:
                        crossed = f_bt.iloc[j] > s_bt.iloc[j] and f_bt.iloc[j-1] <= s_bt.iloc[j-1] if j >= 1 else False
                        if crossed and adx_bt.iloc[j] > 15 and v_bt.iloc[j] > v_ma_bt.iloc[j] * 0.6:
                            if not use_tr or p_bt.iloc[j] > e_bt.iloc[j]:
                                instap, high_p = fill, fill
                                sl_val = fill - 2 * atr_bt.iloc[j]
                                if not np.isfinite(sl_val):
                                    continue
                                pos = True
                                profit -= trade_cost(inzet)
                                num_trades[strat_key] += 1
                    else:
                        # Yesterday's closing stop/trend condition determines
                        # today's open exit; no intraday stop fill is assumed.
                        should_exit = p_bt.iloc[j] < sl_val or f_bt.iloc[j] < s_bt.iloc[j]
                        if should_exit:
                            gross = inzet * fill / instap
                            profit += gross - inzet - trade_cost(gross)
                            pos = False
                        else:
                            high_p = max(high_p, p_bt.iloc[j])
                            if np.isfinite(atr_bt.iloc[j]):
                                sl_val = max(sl_val, high_p - 2 * atr_bt.iloc[j])

                # Include unrealized P/L of a still-open trade at the last close.
                if pos and instap > 0:
                    profit += inzet * (p_bt.iloc[-1] / instap - 1)

                cp, catr, crsi = p.iloc[-1], atr.iloc[-1], rsi.iloc[-1]
                l_rsi = "💎 CRSI" if is_hyp else "📊 RSI"
                y_l = f"[Grafiek](https://finance.yahoo.com/quote/{t})"

                if f.iloc[-1] > s_line.iloc[-1] and f.iloc[-2] <= s_line.iloc[-2]:
                    if adx.iloc[-1] > 15 and vol.iloc[-1] > (v_ma.iloc[-1] * 0.6):
                        if not use_tr or cp > e200.iloc[-1]:
                            sig[strat_key].append(f"• `{t}`: 🟢 *KOOP* | €{cp:.2f} | ⚡ ATR: {catr:.2f} | {l_rsi}: {crsi:.1f} | 🛡️ SL: €{cp-(2*catr):.2f} | {y_l}")
                elif f.iloc[-1] < s_line.iloc[-1] and f.iloc[-2] >= s_line.iloc[-2]:
                    sig[strat_key].append(f"• `{t}`: 🔴 *VERKOOP* | €{cp:.2f} | ⚡ ATR: {catr:.2f} | {l_rsi}: {crsi:.1f} | 🛡️ SL: €{cp-(2*catr):.2f} | {y_l}")

                if not np.isfinite(profit):
                    raise ValueError("Niet-eindige P/L berekend")
                res[strat_key] += profit
                analysed[strat_key] += 1
                status["analyses"] += 1
            except Exception as exc:
                num_trades[strat_key] = entries_before
                del sig[strat_key][signals_before:]
                failures[strat_key] += 1
                status["fouten"] += 1
                diagnostics.append(f"{t} / {strat_key}: analysefout ({type(exc).__name__}: {exc})")
                logger.exception("Analysefout in %s / %s / %s", naam_sector, t, strat_key)

    if not histories:
        status["fouten"] += 1
        diagnostics.append("Deze sector heeft geen analyseerbare tickers; er is geen betrouwbare P/L of signaaluitkomst.")
    coverage = (
        f"Datadekking: {len(tickers)} gevraagd | {len(histories)} analyseerbaar | "
        f"{len(tickers) - len(histories)} overgeslagen. "
        + " | ".join(f"{key}: {analysed[key]} analyses, {failures[key]} fouten" for key in analysed)
    )
    logger.info("%s: %s", naam_sector, coverage)

    def get_s(key):
        text = "\n".join(sig[key]) if sig[key] else ("Geen actie bij de geanalyseerde tickers" if analysed[key] else "Geen betrouwbare uitkomst: nul geslaagde analyses")
        return text + ("\nLET OP: onvolledige analyse, zie datadekking." if failures[key] else "")

    def result_line(key):
        return f"€{res[key]:+,.0f} ({num_trades[key]} entries)" if analysed[key] else "niet beschikbaar"
    rapport_lijst = [
        f"📊 *{label} {naam_sector} RAPPORT mail*", f"_{nu}_", "----------------------------------",
        coverage,
        "STATUS: ONVOLLEDIG — zie overgeslagen tickers en fouten" if diagnostics else "STATUS: VOLLEDIG",
        f"🐢 *Traag (50/200) P/L:* {result_line('T')}",
        f"⚡ *Snel (20/50) P/L:* {result_line('S')}",
        f"🚀 *Hyper Trend P/L:* {result_line('HT')}",
        f"🔥 *Hyper Scalp P/L:* {result_line('HS')}",
        "_P/L = som van afzonderlijke €2.500-posities; geen portefeuillewaarde. Open posities tegen laatste slotkoers gewaardeerd._",
        "", "🛡️ *SIGNALEN TRAAG (RSI):*", get_s("T"),
        "", "🎯 *SIGNALEN SNEL (RSI):*", get_s("S"),
        "", "📈 *SIGNALEN HYPER TREND (CRSI):*", get_s("HT"),
        "", "⚡ *SIGNALEN HYPER SCALP (CRSI):*", get_s("HS"),
        "", "💡 _ATR %: <2% laag, >5% hoog. RSI: >70 overbought, <30 oversold. CRSI: >90 overbought, <10 oversold_"
    ]
    if diagnostics:
        rapport_lijst.extend(["", "OVERGESLAGEN TICKERS EN FOUTEN:", *diagnostics])
    volledige_tekst = "\n".join(rapport_lijst)
    return volledige_tekst + "\n\n" + "="*40 + "\n\n"

def main():
    sectoren = {
        "01":"Hoogland", "02":"Macrotrends", "03":"Beursbrink",
        "04":"Benelux", "05":"Parijs", "06":"Power & AI",
        "07":"Metalen", "08":"Defensie", "09":"Varia"
    }

    verzamel_rapport = "🚀 *FULL GLOBAL SCAN COLLECTIVE REPORT (BOT 00)*\n" + "="*30 + "\n\n"
    logger.info("Start globale scan...")
    cache = {}
    status = {"analyses": 0, "fouten": 0, "overgeslagen": 0}
    
    for nr, naam in sectoren.items():
        try: 
            logger.info(f"Verwerken sector: {naam}")
            sector_bericht = voer_lijst_uit(f"tickers_{nr}.txt", nr, naam, download_cache=cache, status=status)
            verzamel_rapport += sector_bericht
        except Exception as e:
            status["fouten"] += 1
            logger.exception("Fout in sector %s", naam)
            verzamel_rapport += f"\nSECTOR {naam}: MISLUKT ({type(e).__name__}: {e})\n"
        time.sleep(2)

    datum_vandaag = datetime.now().strftime("%d-%m-%Y")
    incomplete = status["fouten"] > 0 or status["overgeslagen"] > 0 or status["analyses"] == 0
    subject = f"{'[ONVOLLEDIG] ' if incomplete else ''}Trading Rapport Bot 00: {datum_vandaag}"
    verzamel_rapport += f"\nTotaal: {status['analyses']} geslaagde ticker/strategie-analyses, {status['fouten']} fouten, {status['overgeslagen']} overgeslagen ticker/sector-combinaties.\n"
    delivered = send_email(subject, verzamel_rapport, html_body=False)
    if not delivered:
        logger.error("E-mail NIET verstuurd; controleer e-mailsecrets en verzendfouten.")
        return 1
    logger.info("E-mail succesvol verstuurd. Telegram staat uit.")
    if status["fouten"] or not status["analyses"]:
        logger.error("Scan onvolledig door analyse- of sectorfouten; zie het verzonden rapport.")
        return 1
    logger.info("Scan voltooid%s.", " met gemelde ontbrekende data" if incomplete else "")
    return 0

if __name__ == "__main__":
    sys.exit(main())

