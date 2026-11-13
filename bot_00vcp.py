#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00vcp.py — VCP ENGINE v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
ATR komt uit common; voortschrijdende gemiddelden worden lokaal berekend.
Backtest: afzonderlijke simulaties per ticker, zonder samengestelde valutaportefeuille.

Volatility Contraction Pattern — Minervini's 'Narrowing' techniek.
Score systeem (0-8):
  1. Minimum 2 VCP contracties gedetecteerd
  2. Elke correctie kleiner dan vorige (%)
  3. Elke correctie korter in tijd dan vorige
  4. Volume daalt bij elke correctie
  5. Laatste contractie <= 10% diep
  6. Prijs binnen 10% van pivot high
  7. Stage 2 trend (Close > MA50 > MA150 > MA200)
  8. Breakout boven pivot op verhoogd volume
"""

import sys
import os
import math
import json
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from common import (
    today_str, safe_float, yahoo_link, esc,
    download_history, load_tickers_from_file, bouw_bestandslijst, label_voor,
    compute_atr_series,
    send_telegram, send_email,
    trade_cost,
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

START_CAPITAL        = 50_000.0
RISICO_PCT_PER_TRADE = 0.05
MAX_HOLD_DAYS        = 60

VCP_CFG = {
    "min_contracties":       2,
    "max_contracties":       5,
    "min_correctie_pct":     3.0,
    "max_correctie_pct":     50.0,
    "contractie_ratio":      0.80,
    "tijd_ratio":            0.90,
    "laatste_max_pct":       10.0,
    "pivot_proximity_pct":   10.0,
    "vol_ma_period":         50,
    "vol_droogval_ratio":    0.80,
    "breakout_vol_mult":     1.5,
    "ma_fast":               50,
    "ma_mid":                150,
    "ma_slow":               200,
    "atr_period":            14,
    "min_score":             4,
    "lookback_days":         120,
}

BACKTEST_START = "2021-01-01"
BACKTEST_END   = today_str()
VCP_SIGNALS_PATH = "vcp_signals.json"

# ============================================================
# VCP KERN: CONTRACTIE DETECTIE
# ============================================================

@dataclass
class Contractie:
    nummer:    int
    high:      float
    low:       float
    pct:       float
    duur:      int
    vol_gem:   float
    start_idx: int
    end_idx:   int

@dataclass
class VCPResultaat:
    contracties:   List[Contractie]
    n_contracties: int
    pct_krimpt:    bool
    tijd_krimpt:   bool
    vol_krimpt:    bool
    laatste_pct:   float
    pivot:         float
    laatste_low:   float
    breakout:      bool
    breakout_vol:  float
    near_pivot:    bool

def clean_history(frame):
    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if frame.empty or any(c not in frame for c in required):
        return pd.DataFrame()
    g = frame.copy()
    g["Date"] = pd.to_datetime(g["Date"], errors="coerce")
    if g["Date"].dt.tz is not None:
        g["Date"] = g["Date"].dt.tz_localize(None)
    g["Date"] = g["Date"].dt.normalize()
    for c in required[1:]:
        g[c] = pd.to_numeric(g[c], errors="coerce").replace([np.inf, -np.inf], np.nan)
    valid = g[required].notna().all(axis=1) & (g[["Open","High","Low","Close"]] > 0).all(axis=1)
    valid &= (g["Volume"] >= 0) & (g["High"] >= g[["Open","Low","Close"]].max(axis=1))
    valid &= g["Low"] <= g[["Open","Close"]].min(axis=1)
    return g.loc[valid].sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)


def extract_contracties(h, l, v, peaks, troughs):
    """Pair alternating, confirmed peaks/troughs without overlapping declines."""
    if not peaks:
        return []
    anchor = max(peaks, key=lambda i: (h[i], i))
    events = sorted([(i, "peak") for i in peaks if i >= anchor] + [(i, "trough") for i in troughs if i > anchor])
    pending, pairs = None, []
    for index, kind in events:
        if kind == "peak":
            if pending is None or h[index] >= h[pending]:
                pending = index
        elif pending is not None and index > pending:
            depth = (h[pending] - l[index]) / h[pending] * 100
            if depth < VCP_CFG["min_correctie_pct"]:
                continue
            if depth <= VCP_CFG["max_correctie_pct"]:
                pairs.append((pending, index))
            pending = None
    pairs = pairs[-VCP_CFG["max_contracties"]:]
    return [Contractie(number, float(h[start]), float(l[end]),
            float((h[start]-l[end])/h[start]*100), end-start,
            float(np.mean(v[start:end+1])), start, end)
            for number, (start,end) in enumerate(pairs, 1)]


def detect_vcp(close, high, low, volume) -> Optional[VCPResultaat]:
    if len(close) < VCP_CFG["lookback_days"] + 20:
        return None
    lb = VCP_CFG["lookback_days"]
    c,h,l,v = [np.asarray(series.iloc[-lb:], dtype=float) for series in (close,high,low,volume)]
    if any(len(x) != lb or not np.isfinite(x).all() for x in (c,h,l,v)):
        return None
    if (c <= 0).any() or (l <= 0).any() or (h < l).any() or (v < 0).any():
        return None
    swing = 5
    peaks, troughs = [], []
    for i in range(swing, lb-swing):
        # Strict against the left neighbour avoids repeated plateau extrema.
        if h[i] > h[i-1] and h[i] >= max(h[i-swing:i+swing+1]):
            peaks.append(i)
        if l[i] < l[i-1] and l[i] <= min(l[i-swing:i+swing+1]):
            troughs.append(i)
    contractions = extract_contracties(h,l,v,peaks,troughs)
    if len(contractions) < VCP_CFG["min_contracties"]:
        return None
    pct_shrinks = all(b.pct <= a.pct * VCP_CFG["contractie_ratio"] for a,b in zip(contractions,contractions[1:]))
    time_shrinks = all(b.duur <= a.duur * VCP_CFG["tijd_ratio"] for a,b in zip(contractions,contractions[1:]))
    volume_shrinks = all(b.vol_gem <= a.vol_gem * VCP_CFG["vol_droogval_ratio"] for a,b in zip(contractions,contractions[1:]))
    pivot = max(item.high for item in contractions)
    mean_volume = float(np.mean(v[-VCP_CFG["vol_ma_period"]-1:-1]))
    volume_ratio = float(v[-1]/mean_volume) if mean_volume > 0 else float("nan")
    current = float(c[-1])
    return VCPResultaat(contractions,len(contractions),pct_shrinks,time_shrinks,volume_shrinks,
        contractions[-1].pct,pivot,contractions[-1].low,current>pivot,volume_ratio,
        abs(pivot-current)/pivot*100 <= VCP_CFG["pivot_proximity_pct"])

# ============================================================
# STAGE 2 TREND CHECK
# ============================================================

def check_stage2(g: pd.DataFrame) -> Tuple[bool, str]:
    close = g["Close"]
    ma50  = close.rolling(VCP_CFG["ma_fast"]).mean()
    ma150 = close.rolling(VCP_CFG["ma_mid"]).mean()
    ma200 = close.rolling(VCP_CFG["ma_slow"]).mean()

    c    = safe_float(close.iloc[-1])
    m50  = safe_float(ma50.iloc[-1])
    m150 = safe_float(ma150.iloc[-1])
    m200 = safe_float(ma200.iloc[-1])

    if any(not math.isfinite(x) or x <= 0 for x in [c, m50, m150, m200]):
        return False, "onvoldoende data"

    ok = c > m50 > m150 > m200
    if ok:
        return True, f"✓ Close>MA{VCP_CFG['ma_fast']}>MA{VCP_CFG['ma_mid']}>MA{VCP_CFG['ma_slow']}"
    else:
        return False, f"✗ Stage 2 vereist Close>MA{VCP_CFG['ma_fast']}>MA{VCP_CFG['ma_mid']}>MA{VCP_CFG['ma_slow']}"

# ============================================================
# VCP SIGNAAL
# ============================================================

@dataclass
class VCPSignaal:
    ticker:       str
    price:        float
    score:        int
    score_labels: List[str]
    vcp:          VCPResultaat
    stage2:       bool
    stage2_label: str
    atr:          float
    stop:         float
    total_score:  float

def analyse_ticker(ticker: str, g: pd.DataFrame) -> Optional[VCPSignaal]:
    try:
        g = g.sort_values("Date").copy()
        if len(g) < VCP_CFG["ma_slow"] + VCP_CFG["lookback_days"]:
            return None

        close  = g["Close"]
        high   = g["High"]
        low    = g["Low"]
        volume = g["Volume"]

        current_price = safe_float(close.iloc[-1])
        if current_price <= 0 or not math.isfinite(current_price):
            return None

        atr = safe_float(compute_atr_series(high, low, close, VCP_CFG["atr_period"]).iloc[-1])
        if not math.isfinite(atr) or atr <= 0:
            return None

        vcp = detect_vcp(close, high, low, volume)
        if vcp is None:
            return None

        stage2, stage2_label = check_stage2(g)
        stop = vcp.laatste_low - (0.5 * atr)
        if not math.isfinite(stop) or not 0 < stop < current_price:
            return None

        score  = 0
        labels = []

        def chk(ok: bool, ok_msg: str, fail_msg: str):
            nonlocal score
            if ok:
                score += 1
                labels.append(f"✓ {ok_msg}")
            else:
                labels.append(f"✗ {fail_msg}")

        chk(vcp.n_contracties >= VCP_CFG["min_contracties"], f"{vcp.n_contracties} contracties (min {VCP_CFG['min_contracties']})", f"slechts {vcp.n_contracties} contractie(s)")
        chk(vcp.pct_krimpt, "correcties krimpen in %", "correcties krimpen NIET in %")
        chk(vcp.tijd_krimpt, "correcties krimpen in tijd", "correcties krimpen NIET in tijd")
        chk(vcp.vol_krimpt, "volume daalt bij elke contractie", "volume daalt NIET consistent")
        chk(vcp.laatste_pct <= VCP_CFG["laatste_max_pct"], f"laatste contractie {vcp.laatste_pct:.1f}% (<={VCP_CFG['laatste_max_pct']:.0f}%)", f"laatste contractie {vcp.laatste_pct:.1f}% (>{VCP_CFG['laatste_max_pct']:.0f}%)")
        chk(vcp.near_pivot or vcp.breakout, f"prijs nabij pivot ({vcp.pivot:.2f})", f"prijs ver van pivot ({vcp.pivot:.2f})")
        chk(stage2, stage2_label, stage2_label)
        chk(vcp.breakout and vcp.breakout_vol >= VCP_CFG["breakout_vol_mult"], f"BREAKOUT boven {vcp.pivot:.2f} op {vcp.breakout_vol:.1f}x volume", f"geen breakout (vol={vcp.breakout_vol:.1f}x)")

        if score < VCP_CFG["min_score"]:
            return None

        total_score = score * 10 + vcp.n_contracties * 5 + (20 if vcp.breakout else 0) + (10 if stage2 else 0) + (5 if vcp.vol_krimpt else 0)

        return VCPSignaal(
            ticker=ticker, price=current_price, score=score, score_labels=labels,
            vcp=vcp, stage2=stage2, stage2_label=stage2_label,
            atr=atr, stop=stop, total_score=total_score,
        )
    except Exception as e:
        raise RuntimeError(f"{ticker}: analysefout: {e}") from e

# ============================================================
# OUTPUT
# ============================================================

def _score_bar(score: int, max_score: int = 8) -> str:
    return "█" * score + "░" * (max_score - score) + f" {score}/{max_score}"

def format_bericht(exchange_name: str, signalen: List[VCPSignaal], portfolio_waarde: Optional[float] = None) -> Optional[str]:
    if not signalen:
        return None

    nu        = today_str()
    max_score = max(s.score for s in signalen)
    toon      = [s for s in signalen if s.score == max_score]
    top2      = sorted(signalen, key=lambda s: (-s.total_score, s.ticker))[:2]
    lbl       = {8: "🔥 PERFECT (8/8)", 7: "⭐ UITSTEKEND (7/8)", 6: "⚡ STERK (6/8)", 5: "📊 GOED (5/8)", 4: "📊 WATCHLIST (4/8)"}.get(max_score, "📊")

    delen = [
        f"🔻 *VCP — {esc(exchange_name)}*",
        f"_{nu} | {len(signalen)} kandidaten | {sum(s.vcp.breakout for s in signalen)} breakouts_",
        "─────────────────────────────",
        "🏆 *TOP 2:*",
    ]
    for s in top2:
        entry = s.price * (1 + SLIPPAGE_PCT)
        delen.append(
            f"• `{s.ticker}` {_score_bar(s.score)} {s.price:.4f} {yahoo_link(s.ticker)}\n"
            f"  {s.vcp.n_contracties} contracties | laatste -{s.vcp.laatste_pct:.1f}% | "
            f"{'BREAKOUT' if s.vcp.breakout else 'setup'}\n"
            f"  Referentie-entry: {entry:.4f} | Stop: {s.stop:.4f} | Pivot: {s.vcp.pivot:.4f}\n"
            "  Oorspronkelijke koerseenheden; geen valutaomrekening of positiegrootte."
        )

    delen += ["─────────────────────────────", f"*{lbl}:*"]
    extra = [s for s in toon if s not in top2]
    if extra:
        for s in extra:
            delen.append(f"• `{s.ticker}` {_score_bar(s.score)} {s.price:.4f} | {s.vcp.n_contracties} contracties | {'BREAKOUT' if s.vcp.breakout else 'setup'}")
    else:
        delen.append("_Zie top 2 hierboven_")

    delen.append(f"⚙️ _Min {VCP_CFG['min_contracties']} contracties | Breakout {VCP_CFG['breakout_vol_mult']}x vol | Eigen 8-puntsheuristiek; geen garantie op een uitbraak._")
    delen.append("Pivot is een uitbraakreferentie, geen koersdoel. Score 4/8 kan een setup zonder bevestigde breakout zijn.")
    return "\n\n".join(delen)

# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    print(f"VCP ENGINE — LIVE {today_str()}")
    exchanges, missing = {}, []
    for path in bouw_bestandslijst():
        tickers = load_tickers_from_file(path)
        if tickers:
            exchanges[label_voor(path)] = sorted(set(tickers))
        else:
            missing.append(path)
    tickers = sorted({ticker for group in exchanges.values() for ticker in group})
    if not tickers:
        raise RuntimeError("Geen tickerlijsten gevonden")
    df = download_history(tickers, period="2y")
    cache, skipped, errors = {}, {}, []
    signal_dates = {}
    today = pd.Timestamp(today_str())
    for ticker in tickers:
        g = clean_history(df.loc[df["Ticker"] == ticker]) if "Ticker" in df else pd.DataFrame()
        if not g.empty:
            g = g.loc[g["Date"] < today]
        if len(g) < VCP_CFG["ma_slow"] + VCP_CFG["lookback_days"]:
            skipped[ticker] = f"{len(g)} geldige afgeronde koersdagen; minimaal {VCP_CFG['ma_slow'] + VCP_CFG['lookback_days']} vereist"
        elif (today-g["Date"].iloc[-1]).days > 7:
            skipped[ticker] = "laatste afgeronde koers ouder dan 7 dagen"
        elif g["Volume"].tail(VCP_CFG["vol_ma_period"]).sum() <= 0:
            skipped[ticker] = "geen bruikbaar recent handelsvolume"
        else:
            try:
                cache[ticker] = analyse_ticker(ticker,g)
                signal_dates[ticker] = g["Date"].iloc[-1].date().isoformat()
            except Exception as exc:
                errors.append(str(exc))
    coverage = f"Datadekking: {len(tickers)} uniek gevraagd | {len(cache)} geanalyseerd | {len(skipped)} overgeslagen | {len(errors)} analysefouten"
    print(coverage)
    for ticker,reason in skipped.items(): print(f"[SKIP:data] {ticker}: {reason}")
    reports, rows = [], []
    saved = 0
    for exchange, members in exchanges.items():
        signals = [cache[t] for t in members if cache.get(t) is not None]
        signals.sort(key=lambda s: (-s.total_score,s.ticker))
        print(f"{exchange}: {sum(t in cache for t in members)}/{len(members)} geanalyseerd | {len(signals)} kandidaten")
        reports.append(format_bericht(exchange,signals) or f"{exchange}: geen kandidaten binnen de beschikbare data")
        for signal in signals:
            row = dict(ticker=signal.ticker, datum=today_str(), beurs=exchange, koers=signal.price,
                score=signal.score,total_score=signal.total_score,n_contracties=signal.vcp.n_contracties,
                pivot=signal.vcp.pivot,laatste_pct=signal.vcp.laatste_pct,breakout=signal.vcp.breakout,
                breakout_vol=signal.vcp.breakout_vol if math.isfinite(signal.vcp.breakout_vol) else None,
                stage2=signal.stage2,atr=signal.atr,stop=signal.stop,price_unit="native_quote_unit",
                signal_date=signal_dates[signal.ticker])
            rows.append(row)
            saved += bool(log_selectie(ticker=signal.ticker,datum=today_str(),strategie="bot_00vcp",
                beurs=exchange,koers=signal.price,parameters={k:v for k,v in row.items() if k not in ("ticker","datum","beurs","koers")}))
    partial = bool(skipped or missing or errors or saved < len(rows))
    payload = dict(tickers=sorted({r["ticker"] for r in rows}),datum=today_str(),
        complete=not partial,coverage=dict(requested=len(tickers),analysed=len(cache),skipped=skipped,errors=errors,missing_files=missing),
        database=dict(saved=saved,requested=len(rows)))
    columns = ["ticker","datum","beurs","koers","score","total_score","n_contracties","pivot","laatste_pct","breakout","breakout_vol","stage2","atr","stop","price_unit","signal_date"]
    csv_path = f"vcp_signalen_{today_str()}.csv"
    pd.DataFrame(rows,columns=columns).to_csv(csv_path,index=False)
    temp_path = VCP_SIGNALS_PATH + ".tmp"
    with open(temp_path,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,allow_nan=False)
    os.replace(temp_path,VCP_SIGNALS_PATH)
    body = coverage + f"\nDatabase: {saved}/{len(rows)} selecties opgeslagen\n\n" + "\n\n".join(reports)
    body += "\n\nOvergeslagen:\n" + "\n".join(f"{t}: {r}" for t,r in skipped.items())
    body += "\nOntbrekende/lege lijsten: " + (", ".join(missing) or "geen")
    body += "\nAnalysefouten: " + ("; ".join(errors) or "geen")
    if os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        for report in reports:
            if not send_telegram(report,parse_mode="Markdown"): print("[WARN] Telegram niet verstuurd")
    else:
        print("Telegram uitgeschakeld (geen configuratie)")
    print(f"Database: {saved}/{len(rows)} selecties opgeslagen")
    if not send_email(f"{'[ONVOLLEDIG] ' if partial else ''}VCP rapport {today_str()}",body):
        raise RuntimeError("E-mail niet verstuurd")
    print("E-mail verstuurd")
    if not cache or errors or saved < len(rows):
        raise RuntimeError("Scan/opslag onvolledig; zie rapport en artifacts")
    print("Klaar met gemelde databeperkingen." if partial else "Klaar.")

# ============================================================
# BACKTEST ENGINE
# ============================================================

def position_size(cash, entry, stop):
    """Bound both invested cash and loss at stop, including model fees/slippage."""
    if not all(math.isfinite(x) and x > 0 for x in (cash,entry,stop)) or stop >= entry:
        return 0
    lower,upper = 0,int(cash/entry)
    while lower < upper:
        size = (lower+upper+1)//2
        notional = entry*size
        proceeds = stop*(1-SLIPPAGE_PCT)*size
        loss = notional + trade_cost(notional) - proceeds + trade_cost(proceeds)
        if notional+trade_cost(notional) <= cash and loss <= cash*RISICO_PCT_PER_TRADE:
            lower=size
        else:
            upper=size-1
    return lower


def exit_fill(row, position):
    """Daily stop/target checks. Gaps use the open; ambiguous bars use stop first."""
    if row.Open <= position["stop"]:
        return float(row.Open),"SL-gap"
    if row.Open >= position["target"]:
        return float(row.Open),"TP-gap"
    if position["days"] >= MAX_HOLD_DAYS:
        return float(row.Open),"Time"
    if row.Low <= position["stop"]:
        return position["stop"],"SL"
    if row.High >= position["target"]:
        return position["target"],"TP"
    return None,None


def backtest_ticker(ticker, history, start=BACKTEST_START, end=None):
    """Independent native-unit account per ticker; no cross-currency portfolio."""
    end = pd.Timestamp(end or BACKTEST_END)
    start = pd.Timestamp(start)
    g = clean_history(history)
    g = g.loc[g.Date <= end].reset_index(drop=True)
    cash,position,pending = START_CAPITAL,None,None
    trades,equity = [],[]
    for index,row in g.iterrows():
        if row.Date < start:
            continue
        if pending is not None and position is None:
            # Do not execute a stale signal after a long data gap.
            if (row.Date-pending["date"]).days <= 7:
                entry = float(row.Open)*(1+SLIPPAGE_PCT)
                target = pending["pivot"]*1.20
                size = position_size(cash,entry,pending["stop"]) if target > entry else 0
                if size > 0:
                    cost=trade_cost(entry*size)
                    cash -= entry*size+cost
                    position=dict(entry_date=row.Date,entry=entry,size=size,cost=cost,
                        stop=pending["stop"],target=target,days=0)
            pending=None
        if position is not None:
            price,reason=exit_fill(row,position)
            if price is not None:
                exit_price=price*(1-SLIPPAGE_PCT)
                fee=trade_cost(exit_price*position["size"])
                proceeds=exit_price*position["size"]-fee
                pnl=proceeds-position["entry"]*position["size"]-position["cost"]
                cash+=proceeds
                trades.append(dict(ticker=ticker,entry_date=position["entry_date"].date().isoformat(),
                    exit_date=row.Date.date().isoformat(),entry_price=position["entry"],exit_price=exit_price,
                    size=position["size"],entry_cost=position["cost"],exit_cost=fee,net=pnl,
                    reason=reason,days=position["days"]+1))
                position=None
            else:
                position["days"]+=1
        value=cash+(position["size"]*float(row.Close) if position else 0)
        equity.append(dict(date=row.Date.date().isoformat(),value=value))
        if position is None and row.Date.weekday()==0 and index+1 >= VCP_CFG["ma_slow"]+VCP_CFG["lookback_days"]:
            sig=analyse_ticker(ticker,g.iloc[:index+1])
            if sig is not None and sig.vcp.breakout:
                pending=dict(date=row.Date,stop=sig.stop,pivot=sig.vcp.pivot)
    return dict(ticker=ticker,final_value=equity[-1]["value"] if equity else START_CAPITAL,
        return_pct=(equity[-1]["value"]/START_CAPITAL-1)*100 if equity else 0,
        trades=trades,equity=equity,open_position=position,
        evaluated_sessions=len(equity))


def run_backtest():
    tickers=sorted({t for path in bouw_bestandslijst() for t in load_tickers_from_file(path)})
    if not tickers: raise RuntimeError("Geen tickers gevonden")
    # Explicit dates, including warmup; the displayed start is now honoured.
    start=(pd.Timestamp(BACKTEST_START)-pd.Timedelta(days=730)).date().isoformat()
    end=(pd.Timestamp(BACKTEST_END)+pd.Timedelta(days=1)).date().isoformat()
    df=download_history(tickers,start=start,end=end)
    summaries,trades,skipped=[],[],[]
    for ticker in tickers:
        g=clean_history(df.loc[df.Ticker==ticker]) if "Ticker" in df else pd.DataFrame()
        if not g.empty:
            g = g.loc[g.Date < pd.Timestamp(today_str())]
        if g.empty or len(g.loc[g.Date < pd.Timestamp(BACKTEST_START)]) < VCP_CFG["ma_slow"] + VCP_CFG["lookback_days"]:
            skipped.append(ticker);continue
        result=backtest_ticker(ticker,g)
        if not result["evaluated_sessions"]:
            skipped.append(ticker);continue
        summaries.append(dict(ticker=ticker,final_value=result["final_value"],return_pct=result["return_pct"],
            closed_trades=len(result["trades"]),open_position=result["open_position"] is not None,
            evaluated_sessions=result["evaluated_sessions"],last_session=result["equity"][-1]["date"]))
        trades.extend(result["trades"])
    pd.DataFrame(summaries,columns=["ticker","final_value","return_pct","closed_trades","open_position","evaluated_sessions","last_session"]).to_csv("vcp_backtest_per_ticker.csv",index=False)
    pd.DataFrame(trades,columns=["ticker","entry_date","exit_date","entry_price","exit_price","size","entry_cost","exit_cost","net","reason","days"]).to_csv("vcp_backtest_trades.csv",index=False)
    print(f"Backtest {BACKTEST_START} t/m {BACKTEST_END}: {len(summaries)} tickers | {len(skipped)} overgeslagen: {', '.join(skipped)}")
    print("Afzonderlijke accounts van 50.000 oorspronkelijke koerseenheden per ticker; geen gezamenlijk portefeuille- of EUR-rendement. Geen belastingmodel. Open posities gewaardeerd tegen laatste slotkoers zonder fictieve verkoopkosten.")
    if not summaries: raise RuntimeError("Geen bruikbare backtesthistorie")

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
        sys.exit("Gebruik: python bot_00vcp.py [live|backtest]")

