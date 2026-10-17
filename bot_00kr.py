#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_00db.py — DARVAS BOX ENGINE v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Alle indicatoren (ATR, MA's) komen uit common.indicators.
Backtest-functionaliteit is volledig behouden.
"""

import sys
import math
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from common import (
    today_str, safe_float, yahoo_link, esc,
    download_history, load_tickers_from_file, bouw_bestandslijst, label_voor,
    compute_atr_series, compute_ema,
    send_telegram, send_email,
    bereken_positie, trade_cost,
    SLIPPAGE_PCT, TRADE_COST_FIXED, TRADE_COST_PCT, RISICO_PCT_DEFAULT
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
MAX_POSITIONS        = 10
RISICO_PCT_PER_TRADE = 0.05
TAX_RATE             = 0.10
MAX_HOLD_DAYS        = 60

DB_CFG = {
    "min_box_days":          3,
    "max_box_days":          15,
    "max_box_pct":           12.0,
    "min_boxes":             2,
    "max_boxes":             5,
    "vol_ma_period":         50,
    "vol_droogval_ratio":    0.85,
    "breakout_vol_mult":     1.5,
    "pivot_proximity_pct":   10.0,
    "ma_fast":               50,
    "ma_mid":                150,
    "ma_slow":               200,
    "atr_period":            14,
    "min_score":             5,
    "lookback_days":         150,
}

BACKTEST_START = "2021-01-01"
BACKTEST_END   = today_str()


# ============================================================
# DARVAS BOX KERN: DOOS-DETECTIE
# ============================================================

@dataclass
class DarvasBox:
    nummer:    int
    top:       float
    bottom:    float
    pct:       float
    duur:      int
    vol_gem:   float
    start_idx: int
    end_idx:   int

@dataclass
class DarvasResultaat:
    boxes:        List[DarvasBox]
    n_boxes:      int
    pct_smal_genoeg: bool
    vol_krimpt:   bool
    staircase_omhoog: bool
    huidige_box:  DarvasBox
    breakout:     bool
    breakout_vol: float
    near_top:     bool

def detect_darvas_boxes(
    close:  pd.Series,
    high:   pd.Series,
    low:    pd.Series,
    volume: pd.Series,
) -> Optional[DarvasResultaat]:
    n = len(close)
    if n < DB_CFG["lookback_days"] + 20:
        return None

    lb = DB_CFG["lookback_days"]
    c  = close.values[-lb:]
    h  = high.values[-lb:]
    l  = low.values[-lb:]
    v  = volume.values[-lb:]
    n_lb = len(c)

    vol_ma = pd.Series(v).rolling(DB_CFG["vol_ma_period"]).mean().values
    min_days = DB_CFG["min_box_days"]
    max_days = DB_CFG["max_box_days"]

    boxes: List[DarvasBox] = []
    i = 0
    while i < n_lb - min_days and len(boxes) < DB_CFG["max_boxes"]:
        best_end = None
        best_top = h[i]
        best_bottom = l[i]
        for span in range(min_days, min(max_days, n_lb - i) + 1):
            window_h = h[i:i + span]
            window_l = l[i:i + span]
            top    = float(window_h.max())
            bottom = float(window_l.min())
            spread = (top - bottom) / top * 100 if top > 0 else 999
            if spread <= DB_CFG["max_box_pct"]:
                best_end = i + span - 1
                best_top = top
                best_bottom = bottom
            else:
                break
        if best_end is not None:
            spread = (best_top - best_bottom) / best_top * 100
            vol_gem = float(np.mean(v[i:best_end + 1]))
            boxes.append(DarvasBox(
                nummer=len(boxes) + 1,
                top=round(best_top, 4), bottom=round(best_bottom, 4),
                pct=round(spread, 2), duur=best_end - i + 1,
                vol_gem=round(vol_gem, 0),
                start_idx=i, end_idx=best_end,
            ))
            i = best_end + 1
        else:
            i += 1

    if not boxes:
        return None

    recente_boxes = boxes[-DB_CFG["max_boxes"]:]

    pct_smal_genoeg = all(b.pct <= DB_CFG["max_box_pct"] for b in recente_boxes)
    vol_krimpt = True
    for idx in range(1, len(recente_boxes)):
        eerdere_vol = recente_boxes[idx - 1].vol_gem
        if eerdere_vol <= 0 or recente_boxes[idx].vol_gem > eerdere_vol * DB_CFG["vol_droogval_ratio"]:
            vol_krimpt = False
            break

    staircase_omhoog = len(recente_boxes) >= DB_CFG["min_boxes"] and all(
        recente_boxes[idx].bottom > recente_boxes[idx - 1].bottom
        for idx in range(1, len(recente_boxes))
    )

    huidige_box = boxes[-1]
    current_price = float(c[-1])
    current_vol   = float(v[-1])
    vol_ma_now    = safe_float(vol_ma[-1], 1.0)

    breakout     = current_price > huidige_box.top
    breakout_vol = (current_vol / vol_ma_now) if vol_ma_now > 0 else 0.0
    near_top     = ((huidige_box.top - current_price) / huidige_box.top * 100) <= DB_CFG["pivot_proximity_pct"]

    return DarvasResultaat(
        boxes=boxes, n_boxes=len(boxes),
        pct_smal_genoeg=pct_smal_genoeg, vol_krimpt=vol_krimpt,
        staircase_omhoog=staircase_omhoog, huidige_box=huidige_box,
        breakout=breakout, breakout_vol=round(breakout_vol, 2),
        near_top=near_top or breakout,
    )


# ============================================================
# STAGE 2 TREND CHECK
# ============================================================

def check_stage2(g: pd.DataFrame) -> Tuple[bool, str]:
    close = g["Close"]
    ma50  = close.rolling(DB_CFG["ma_fast"]).mean()
    ma150 = close.rolling(DB_CFG["ma_mid"]).mean()
    ma200 = close.rolling(DB_CFG["ma_slow"]).mean()

    c    = safe_float(close.iloc[-1])
    m50  = safe_float(ma50.iloc[-1])
    m150 = safe_float(ma150.iloc[-1])
    m200 = safe_float(ma200.iloc[-1])

    if any(math.isnan(x) for x in [c, m50, m150, m200]):
        return False, "onvoldoende data"

    ok = c > m50 > m150 > m200
    if ok:
        return True, f"✓ Close>MA{DB_CFG['ma_fast']}>MA{DB_CFG['ma_mid']}>MA{DB_CFG['ma_slow']}"
    else:
        return False, f"✗ Stage 2 vereist Close>MA{DB_CFG['ma_fast']}>MA{DB_CFG['ma_mid']}>MA{DB_CFG['ma_slow']}"


# ============================================================
# DARVAS SIGNAAL
# ============================================================

@dataclass
class DarvasSignaal:
    ticker:       str
    price:        float
    score:        int
    score_labels: List[str]
    db:           DarvasResultaat
    stage2:       bool
    stage2_label: str
    atr:          float
    stop:         float
    total_score:  float

def analyse_ticker(ticker: str, g: pd.DataFrame) -> Optional[DarvasSignaal]:
    try:
        g = g.sort_values("Date").copy()
        if len(g) < DB_CFG["ma_slow"] + DB_CFG["lookback_days"]:
            return None

        close  = g["Close"]
        high   = g["High"]
        low    = g["Low"]
        volume = g["Volume"]

        current_price = safe_float(close.iloc[-1])
        if current_price <= 0 or math.isnan(current_price):
            return None

        atr = safe_float(compute_atr_series(high, low, close, DB_CFG["atr_period"]).iloc[-1], current_price * 0.02)

        db = detect_darvas_boxes(close, high, low, volume)
        if db is None:
            return None

        stage2, stage2_label = check_stage2(g)
        stop = db.huidige_box.bottom - (0.5 * atr)

        score  = 0
        labels = []

        def chk(ok: bool, ok_msg: str, fail_msg: str):
            nonlocal score
            if ok:
                score += 1
                labels.append(f"✓ {ok_msg}")
            else:
                labels.append(f"✗ {fail_msg}")

        chk(db.n_boxes >= 1, f"{db.n_boxes} doos/dozen gedetecteerd", "geen geldige doos gevonden")
        chk(db.pct_smal_genoeg, f"doos smal genoeg (<={DB_CFG['max_box_pct']:.0f}%)", "doos te breed")
        chk(db.vol_krimpt, "volume droogt op binnen de doos", "volume droogt NIET op")
        chk(db.n_boxes >= DB_CFG["min_boxes"], f"{db.n_boxes} opeenvolgende dozen (staircase, min {DB_CFG['min_boxes']})", f"slechts {db.n_boxes} doos/dozen")
        chk(db.staircase_omhoog, "elke doos hoger dan de vorige (staircase omhoog)", "geen oplopende staircase")
        chk(db.near_top, f"prijs nabij box top ({db.huidige_box.top:.2f})", f"prijs ver van box top ({db.huidige_box.top:.2f})")
        chk(stage2, stage2_label, stage2_label)
        chk(db.breakout and db.breakout_vol >= DB_CFG["breakout_vol_mult"], f"BREAKOUT boven {db.huidige_box.top:.2f} op {db.breakout_vol:.1f}x volume", f"geen breakout (vol={db.breakout_vol:.1f}x)")

        if score < DB_CFG["min_score"]:
            return None

        total_score = score * 10 + db.n_boxes * 5 + (20 if db.breakout else 0) + (10 if stage2 else 0) + (5 if db.vol_krimpt else 0) + (5 if db.staircase_omhoog else 0)

        return DarvasSignaal(
            ticker=ticker, price=round(current_price, 2), score=score, score_labels=labels,
            db=db, stage2=stage2, stage2_label=stage2_label,
            atr=round(atr, 4), stop=round(stop, 2), total_score=round(total_score, 1),
        )
    except Exception as e:
        print(f"[WARN] {ticker}: fout — {e}")
        return None


# ============================================================
# OUTPUT
# ============================================================

def _score_bar(score: int, max_score: int = 8) -> str:
    return "█" * score + "░" * (max_score - score) + f" {score}/{max_score}"

def format_bericht(exchange_name: str, signalen: List[DarvasSignaal], portfolio_waarde: float) -> Optional[str]:
    if not signalen:
        return None

    nu        = today_str()
    max_score = max(s.score for s in signalen)
    toon      = [s for s in signalen if s.score == max_score]
    top2      = signalen[:2]
    lbl       = {8: "🔥 PERFECT (8/8)", 7: "⭐ UITSTEKEND (7/8)", 6: "⚡ STERK (6/8)", 5: "📊 GOED (5/8)"}.get(max_score, "📊")

    delen = [
        f"📦 *Darvas Box — {esc(exchange_name)}*",
        f"_{nu} | {len(signalen)} kandidaten | {sum(s.db.breakout for s in signalen)} breakouts_",
        "─────────────────────────────",
        "🏆 *TOP 2:*",
    ]
    for s in top2:
        entry = s.price * (1 + SLIPPAGE_PCT)
        aandelen, max_loss = bereken_positie(portfolio_waarde, entry, s.stop)
        investering = round(entry * aandelen, 2)
        rr = ((s.db.huidige_box.top - entry) / (entry - s.stop)) if (entry - s.stop) > 0 else 0
        delen.append(
            f"• `{s.ticker}` {_score_bar(s.score)} EUR{s.price:.2f} {yahoo_link(s.ticker)}\n"
            f"  {s.db.n_boxes} doos/dozen | box {s.db.huidige_box.pct:.1f}% breed | "
            f"{'BREAKOUT' if s.db.breakout else 'setup'}\n"
            f"  Entry: EUR{entry:.2f} | Stop: EUR{s.stop:.2f} | Box top: EUR{s.db.huidige_box.top:.2f}\n"
            f"  R/R: {rr:.1f}:1 | {aandelen} stuks | EUR{investering:,.2f} | Max verlies: EUR{max_loss:,.2f}"
        )

    delen += ["─────────────────────────────", f"*{lbl}:*"]
    extra = [s for s in toon if s not in top2]
    if extra:
        for s in extra:
            delen.append(f"• `{s.ticker}` {_score_bar(s.score)} EUR{s.price:.2f} | {s.db.n_boxes} doos/dozen | {'BREAKOUT' if s.db.breakout else 'setup'}")
    else:
        delen.append("_Zie top 2 hierboven_")

    delen.append(f"⚙️ _Min {DB_CFG['min_boxes']} dozen | Breakout {DB_CFG['breakout_vol_mult']}x vol | Risico {RISICO_PCT_PER_TRADE*100:.0f}%_")
    return "\n\n".join(delen)


# ============================================================
# LIVE ENGINE
# ============================================================

def run_live_engine():
    print(f"{'='*60}")
    print(f"DARVAS BOX ENGINE — LIVE  {today_str()}")
    print(f"{'='*60}")

    exchange_tickers: Dict[str, List[str]] = {}
    all_tickers: List[str] = []

    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist:
            continue
        ex_name = label_voor(f_name)
        exchange_tickers[ex_name] = tlist
        all_tickers.extend(tlist)
        print(f"  {ex_name}: {len(tlist)} tickers")

    all_tickers = sorted(set(all_tickers))
    if not all_tickers:
        print("[ERROR] Geen ticker bestanden gevonden.")
        return

    print(f"\nTotaal: {len(all_tickers)} unieke tickers")
    print("Data downloaden (2 jaar)...")
    df = download_history(all_tickers, period="2y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    print(f"Data geladen: {df['Ticker'].nunique()} tickers")
    portfolio_waarde = START_CAPITAL
    email_delen: List[str] = []

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")
        df_ex = df[df["Ticker"].isin(tlist)].copy()

        signalen: List[DarvasSignaal] = []
        for ticker, group in df_ex.groupby("Ticker", sort=False):
            sig = analyse_ticker(ticker, group)
            if sig:
                signalen.append(sig)
                print(f"  ✓ {ticker}: {sig.score}/8 | {sig.db.n_boxes} doos/dozen | box {sig.db.huidige_box.pct:.1f}% breed | {'BREAKOUT' if sig.db.breakout else 'setup'}")

        signalen.sort(key=lambda s: s.total_score, reverse=True)
        print(f"  → {len(signalen)} Darvas kandidaten | {sum(s.db.breakout for s in signalen)} breakouts")

        for s in signalen:
            log_selectie(
                ticker=s.ticker, datum=today_str(), strategie="bot_00db",
                beurs=ex_name, koers=s.price,
                parameters={
                    "score": s.score, "total_score": s.total_score,
                    "n_boxes": s.db.n_boxes,
                    "box_top": s.db.huidige_box.top, "box_bottom": s.db.huidige_box.bottom,
                    "box_pct": s.db.huidige_box.pct, "breakout": s.db.breakout,
                    "breakout_vol": s.db.breakout_vol, "stage2": s.stage2,
                    "atr": s.atr, "stop": s.stop, "grafiek": yahoo_link(s.ticker),
                },
            )

        bericht = format_bericht(ex_name, signalen, portfolio_waarde)
        if bericht:
            send_telegram(bericht, parse_mode="Markdown")
            email_delen.append(bericht)
            print(f"  → Telegram verstuurd: {ex_name}")
        else:
            print(f"  → Overgeslagen (geen signalen): {ex_name}")

    if email_delen:
        send_email(
            subject=f"Darvas Box rapport {today_str()}",
            body="\n\n" + ("=" * 40 + "\n\n").join(email_delen),
        )

    print(f"\n{'='*60}")
    print("Klaar.")


# ============================================================
# BACKTEST ENGINE
# ============================================================

@dataclass
class BTPosition:
    ticker:      str
    strategy:    str
    entry_date:  pd.Timestamp
    entry_price: float
    size:        int
    cost:        float
    sl:          Optional[float]
    tp:          Optional[float]
    atr:         float
    days:        int = 0

def run_backtest():
    print(f"{'='*60}")
    print(f"DARVAS BOX BACKTEST  {BACKTEST_START} -> {BACKTEST_END}")
    print(f"{'='*60}")

    all_tickers: List[str] = []
    for f_name in bouw_bestandslijst():
        all_tickers.extend(load_tickers_from_file(f_name))
    all_tickers = sorted(set(all_tickers))

    if not all_tickers:
        print("[ERROR] Geen tickers gevonden.")
        return

    print(f"Tickers: {len(all_tickers)} | Data downloaden (5y)...")
    df = download_history(all_tickers, period="5y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    all_dates  = sorted(df["Date"].dt.date.unique())
    cash       = START_CAPITAL
    positions: Dict[str, BTPosition] = {}
    trades:    List[Dict]      = []
    
    scan_dates = [d for d in all_dates if d.weekday() == 0]
    print(f"Scanmomenten: {len(scan_dates)} (wekelijks maandag)")

    for scan_date in scan_dates:
        df_hist = df[df["Date"] <= pd.Timestamp(scan_date)].copy()
        day_df  = df[df["Date"] == pd.Timestamp(scan_date)].copy()

        price_map = {}
        for _, row in day_df.iterrows():
            t = row.get("Ticker")
            c = safe_float(row.get("Close"))
            if t and not math.isnan(c):
                price_map[t] = c

        for ticker, pos in list(positions.items()):
            pos.days += 1
            if ticker not in price_map:
                continue
            close  = price_map[ticker]
            reason = None
            if pos.sl is not None and close <= pos.sl:
                reason = f"SL ({pos.sl:.2f})"
            elif pos.tp is not None and close >= pos.tp:
                reason = f"TP ({pos.tp:.2f})"
            elif pos.days >= MAX_HOLD_DAYS:
                reason = f"Time ({pos.days}d)"
            
            if reason:
                exit_slip = close * (1 - SLIPPAGE_PCT)
                gross     = exit_slip * pos.size
                cost      = trade_cost(gross)
                pnl       = gross - cost - (pos.entry_price * pos.size + pos.cost)
                tax       = pnl * TAX_RATE if pnl > 0 else 0.0
                cash     += gross - cost - tax
                trades.append({
                    "entry_date":  pos.entry_date.isoformat(),
                    "exit_date":   scan_date.isoformat(),
                    "ticker":      ticker,
                    "score":       pos.strategy,
                    "n_boxes":     pos.strategy,
                    "entry_price": pos.entry_price,
                    "exit_price":  round(exit_slip, 4),
                    "size":        pos.size,
                    "pnl":         round(pnl, 2),
                    "tax":         round(tax, 2),
                    "net":         round(pnl - tax, 2),
                    "reason":      reason,
                    "days":        pos.days,
                })
                del positions[ticker]

        for ticker, group in df_hist.groupby("Ticker", sort=False):
            if ticker in positions or len(positions) >= MAX_POSITIONS:
                continue
            sig = analyse_ticker(ticker, group)
            if not sig or not sig.db.breakout:
                continue
            
            entry = sig.price * (1 + SLIPPAGE_PCT)
            # Bereken positie op basis van cash
            total_waarde = cash + sum(price_map.get(t, p.entry_price) * p.size for t, p in positions.items())
            aandelen, _ = bereken_positie(total_waarde, entry, sig.stop)
            
            if aandelen <= 0:
                continue
            investering = entry * aandelen + trade_cost(entry * aandelen)
            if investering > cash:
                continue
            cash -= investering
            positions[ticker] = BTPosition(
                ticker=ticker, strategy=str(sig.score),
                entry_date=scan_date, entry_price=round(entry, 4),
                size=aandelen, cost=trade_cost(investering),
                sl=sig.stop, tp=sig.db.huidige_box.top * 1.20,
                atr=sig.atr, days=0
            )

    if trades:
        tdf  = pd.DataFrame(trades)
        n    = len(tdf)
        nwin = (tdf["net"] > 0).sum()
        pf   = abs(tdf.loc[tdf["net"] > 0, "net"].sum()) / max(abs(tdf.loc[tdf["net"] <= 0, "net"].sum()), 1e-9)
        final_val = cash + sum(price_map.get(t, p.entry_price) * p.size for t, p in positions.items())
        print(f"\n{'='*60}")
        print(f"Startkapitaal    : EUR{START_CAPITAL:>12,.2f}")
        print(f"Eindkapitaal     : EUR{final_val:>12,.2f}")
        print(f"Totaal rendement : {(final_val-START_CAPITAL)/START_CAPITAL*100:>+.1f}%")
        print(f"Trades           : {n} | Winnaars: {nwin} ({nwin/n*100:.1f}%)")
        print(f"Profit Factor    : {pf:.2f}")
        print(f"Belasting betaald: EUR{tdf['tax'].sum():,.2f}")
        print(f"Gem. houdduur    : {tdf['days'].mean():.1f} dagen")
        print(f"{'='*60}")
    else:
        print("Geen trades gegenereerd.")


# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
      if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
