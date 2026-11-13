#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLOBAL ENGINE: six strategy advice scanner, independent-ticker backtests,
and an explicitly invoked local manual ledger. No broker execution.
Live mode leaves local portfolio state untouched.
"""

import os
import sys
import math
import csv
import json
import hashlib
import copy
import datetime as dt
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

from common import (
    today_str, safe_float, yahoo_link,
    download_history, load_tickers_from_file, bouw_bestandslijst, label_voor,
    compute_rsi_series, compute_atr_series, compute_ibs, compute_adx_series,
    send_telegram, send_email, trade_cost, SLIPPAGE_PCT,
)

try:
    from db_logger import log_selectie
except ImportError as exc:
    print(f"[WARN] db_logger niet beschikbaar: {exc}")
    def log_selectie(*args, **kwargs):
        return False

# ============================================================
# CONFIG
# ============================================================

START_CAPITAL        = 50_000.0
MAX_POSITIONS        = 10
MIN_CASH_RATIO       = 0.10
RISICO_PCT_PER_TRADE = 0.05
ATR_STOP_MULT        = 2.0
TAX_RATE             = 0.0  # Geen belastingmodel; geen fiscale aanname.
MAX_HOLD_DAYS        = 20

LIVE_STATE_FILE = "live_state.json"
LIVE_TRADES_FILE    = "trades_live.csv"
LIVE_POSITIONS_FILE = "positions_live.csv"
LIVE_PORTFOLIO_FILE = "portfolio_live.csv"

BACKTEST_START = "2019-01-01"
BACKTEST_END   = dt.date.today().isoformat()

STRAT_CONFIG = {
    "Traag":       {"sl_mult": 2.0, "tp_mult": 4.0},
    "Snel":        {"sl_mult": 2.0, "tp_mult": 3.0},
    "Hyper Trend": {"sl_mult": 2.5, "tp_mult": 5.0},
    "Hyper Scalp": {"sl_mult": 1.5, "tp_mult": 2.5},
    "MRA Snel":    {"sl_mult": 2.0, "tp_mult": 3.0},
    "MRA Traag":   {"sl_mult": 2.5, "tp_mult": 4.0},
}

# ============================================================
# INDICATOREN
# ============================================================

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for ticker, group in df.groupby("Ticker", sort=False):
        g     = group.sort_values("Date").copy()
        close = g["Close"]
        high  = g["High"]
        low   = g["Low"]

        g["MA20"]  = close.rolling(20).mean()
        g["MA50"]  = close.rolling(50).mean()
        g["MA200"] = close.rolling(200).mean()

        g["RSI14"] = compute_rsi_series(close, 14)
        g["ATR14"] = compute_atr_series(high, low, close, 14)
        g["IBS"]   = compute_ibs(high, low, close)
        g["ADX14"] = compute_adx_series(high, low, close, 14)

        g["Ticker"] = ticker
        parts.append(g)

    if not parts:
        return df
    return pd.concat(parts).sort_values(["Ticker", "Date"]).reset_index(drop=True)

# ============================================================
# SIGNALEN
# ============================================================

@dataclass
class Signal:
    ticker:    str
    date:      dt.date
    strategy:  str
    direction: str
    reason:    str
    price:     float
    atr:       float
    sl:        Optional[float] = None
    tp:        Optional[float] = None
    rr_ratio:  float = 0.0
    next_open: Optional[float] = None

def _calc_rr(price, sl, tp) -> float:
    if sl is None or tp is None:
        return 0.0
    risk   = abs(price - sl)
    reward = abs(tp - price)
    return (reward / risk) if risk > 1e-9 else 0.0

def generate_signals_for_day(df: pd.DataFrame, date: dt.date) -> List[Signal]:
    signals: List[Signal] = []
    day_df = df[df["Date"] == pd.Timestamp(date)].copy()
    if day_df.empty:
        return signals

    for _, row in day_df.iterrows():
        t     = row.get("Ticker")
        if not t:
            continue
        close     = safe_float(row.get("Close"))
        ma20      = safe_float(row.get("MA20"))
        ma50      = safe_float(row.get("MA50"))
        ma200     = safe_float(row.get("MA200"))
        rsi       = safe_float(row.get("RSI14"))
        ibs       = safe_float(row.get("IBS"))
        atr       = safe_float(row.get("ATR14"))
        adx       = safe_float(row.get("ADX14"))
        next_open = None  # Uitvoering wordt pas op de volgende sessie bepaald.

        if not math.isfinite(close) or close <= 0 or not math.isfinite(atr) or atr <= 0:
            continue

        def make(strategy: str, reason: str) -> Signal:
            cfg = STRAT_CONFIG.get(strategy, {"sl_mult": 2.0, "tp_mult": 3.0})
            sl  = close - cfg["sl_mult"] * atr
            tp  = close + cfg["tp_mult"] * atr
            return Signal(ticker=t, date=date, strategy=strategy, direction="BUY",
                          reason=reason, price=close, atr=atr, sl=sl, tp=tp,
                          rr_ratio=_calc_rr(close, sl, tp), next_open=next_open)

        if all(math.isfinite(x) for x in (ma50,ma200,adx)):
            if ma50 > ma200 and close > ma50 and adx > 15:
                signals.append(make("Traag", "MA50>MA200 & Close>MA50 & ADX>15"))

        if all(math.isfinite(x) for x in (ma20,ma50,adx)):
            if ma20 > ma50 and close > ma20 and adx > 15:
                signals.append(make("Snel", "MA20>MA50 & Close>MA20 & ADX>15"))

        if all(math.isfinite(x) for x in (ma50,ma200,adx,rsi)):
            if ma50 > ma200 and close > ma50 and adx > 20 and rsi > 55:
                signals.append(make("Hyper Trend", "ADX>20 & RSI>55 & MA50>MA200"))

        if all(math.isfinite(x) for x in (rsi,ibs)):
            if rsi < 30 and ibs < 0.2:
                signals.append(make("Hyper Scalp", "RSI<30 & IBS<0.2"))
            if rsi < 35 and ibs < 0.3:
                signals.append(make("MRA Snel", "RSI<35 & IBS<0.3"))
            if rsi < 40 and ibs < 0.4:
                signals.append(make("MRA Traag", "RSI<40 & IBS<0.4"))

    signals = [s for s in signals if all(math.isfinite(v) for v in (s.sl,s.tp,s.rr_ratio)) and 0 < s.sl < s.price < s.tp]
    signals.sort(key=lambda s: (-s.rr_ratio, s.ticker, s.strategy))
    return signals

# ============================================================
# HISTORY AND SIMULATED ACCOUNT
# ============================================================

def clean_history(frame):
    required = ["Date","Open","High","Low","Close","Volume"]
    if frame.empty or any(c not in frame for c in required):
        return pd.DataFrame()
    g=frame.copy()
    g["Date"]=pd.to_datetime(g["Date"],errors="coerce")
    if g["Date"].dt.tz is not None: g["Date"]=g["Date"].dt.tz_localize(None)
    g["Date"]=g["Date"].dt.normalize()
    for c in required[1:]:
        g[c]=pd.to_numeric(g[c],errors="coerce").replace([np.inf,-np.inf],np.nan)
    valid=g[required].notna().all(axis=1) & (g[["Open","High","Low","Close"]]>0).all(axis=1)
    valid &= (g.Volume>=0) & (g.High>=g[["Open","Close","Low"]].max(axis=1))
    valid &= g.Low<=g[["Open","Close"]].min(axis=1)
    return g.loc[valid].sort_values("Date").drop_duplicates("Date",keep="last").reset_index(drop=True)


def atomic_json(path, payload):
    temp=str(path)+".tmp"
    with open(temp,"w",encoding="utf-8") as handle:
        json.dump(payload,handle,ensure_ascii=False,allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp,path)


@dataclass
class LivePosition:
    ticker: str
    strategy: str
    entry_date: str
    entry_price: float
    size: int
    cost: float
    sl: Optional[float]
    tp: Optional[float]
    atr: float = 0.0
    days_open: int = 0


class LivePortfolio:
    """Local simulation ledger, never connected to an order-execution API."""
    def __init__(self,start_capital,load=True,currency=None):
        self.cash=float(start_capital)
        self.currency=currency or os.getenv("PORTFOLIO_CURRENCY","USD")
        self.positions={}
        self.trades=[]
        self.applied_batches=[]
        if load:self.load_state()

    def load_state(self):
        if Path(LIVE_STATE_FILE).exists():
            state=json.loads(Path(LIVE_STATE_FILE).read_text())
            if state["currency"] != self.currency:
                raise ValueError("Accountvaluta verschilt van opgeslagen state")
            self.cash=float(state["cash"])
            self.positions={p["ticker"]:LivePosition(**p) for p in state["positions"]}
            self.trades=state["trades"]
            self.applied_batches=state.get("applied_batches",[])
            self.validate()
        elif any(Path(p).exists() for p in (LIVE_PORTFOLIO_FILE,LIVE_POSITIONS_FILE,LIVE_TRADES_FILE)):
            raise ValueError("Legacy CSV-state aanwezig: expliciete valutacontrole en migratie naar live_state.json vereist")

    def validate(self):
        if not math.isfinite(self.cash) or self.cash<0: raise ValueError("Ongeldig kassaldo")
        if len(self.positions)>MAX_POSITIONS:raise ValueError("Te veel posities")
        for p in self.positions.values():
            if not isinstance(p.size,int) or p.size<=0 or not math.isfinite(p.entry_price) or p.entry_price<=0:
                raise ValueError("Ongeldige positie")
            if not math.isfinite(p.cost) or p.cost<0:raise ValueError("Ongeldige instapkosten")
            if p.sl is not None and (not math.isfinite(p.sl) or p.sl<=0):raise ValueError("Ongeldige stop")
            if p.tp is not None and (not math.isfinite(p.tp) or p.tp<=0):raise ValueError("Ongeldig doel")
            dt.date.fromisoformat(p.entry_date)

    def save_state(self,date,prices):
        self.validate()
        # One authoritative transaction contains cash, holdings, trades and replay IDs.
        atomic_json(LIVE_STATE_FILE,dict(version=1,currency=self.currency,cash=self.cash,
            positions=[asdict(p) for p in self.positions.values()],trades=self.trades,
            applied_batches=self.applied_batches,last_update=date))
        # CSVs are replaceable projections; JSON remains authoritative after a crash.
        for path,frame in ((LIVE_POSITIONS_FILE,pd.DataFrame([asdict(p) for p in self.positions.values()],columns=list(LivePosition.__dataclass_fields__))),
                           (LIVE_TRADES_FILE,pd.DataFrame(self.trades,columns=["date","ticker","strategy","side","price","size","cost","pnl","tax","net","reason"]))):
            temp=path+".tmp";frame.to_csv(temp,index=False);os.replace(temp,path)
        value=self.current_total_value(prices) if all(t in prices for t in self.positions) else None
        temp=LIVE_PORTFOLIO_FILE+".tmp"
        pd.DataFrame([dict(date=date,currency=self.currency,cash=self.cash,total=value)]).to_csv(temp,index=False)
        os.replace(temp,LIVE_PORTFOLIO_FILE)

    def current_total_value(self,prices):
        if any(t not in prices or not math.isfinite(prices[t]) or prices[t]<=0 for t in self.positions):
            raise ValueError("Waarderingskoers ontbreekt voor bestaande positie")
        return self.cash+sum(prices[t]*p.size for t,p in self.positions.items())

    def log_trade(self,date,ticker,strategy,side,price,size,cost,pnl,tax,net,reason=""):
        self.trades.append(dict(date=date,ticker=ticker,strategy=strategy,side=side,price=price,
            size=size,cost=cost,pnl=pnl,tax=tax,net=net,reason=reason))

    def close_position(self,ticker,date,exit_price,reason,size=None,slippage=True):
        p=self.positions[ticker]
        size=p.size if size is None else size
        if not isinstance(size,int) or not 0<size<=p.size:raise ValueError("Ongeldig verkoopaantal")
        if not math.isfinite(exit_price) or exit_price<=0:raise ValueError("Ongeldige verkoopprijs")
        fill=exit_price*(1-SLIPPAGE_PCT) if slippage else exit_price
        gross=fill*size;cost=trade_cost(gross)
        allocated_cost=p.cost*size/p.size
        pnl=gross-cost-p.entry_price*size-allocated_cost
        self.cash+=gross-cost
        self.log_trade(str(date),ticker,p.strategy,"SELL",fill,size,cost,pnl,0.,pnl,reason)
        p.cost-=allocated_cost;p.size-=size
        if p.size==0:del self.positions[ticker]


# ============================================================
# MANUAL COMMANDS: VALIDATE AND COMMIT A COMPLETE BATCH
# ============================================================

def apply_telegram_commands(portfolio,commands_file):
    path=Path(commands_file)
    if not path.exists() or not path.read_text().strip():return
    if Path(LIVE_STATE_FILE).exists():portfolio.load_state()
    original=path.read_text()
    batch_id=hashlib.sha256(original.encode()).hexdigest()
    if batch_id in portfolio.applied_batches:
        portfolio.save_state(today_str(),{})
        path.write_text("")
        print("Eerder verwerkt commandobestand leeggemaakt; geen heruitvoering")
        return
    work=copy.deepcopy(portfolio)
    currencies={}
    for ticker in work.positions:
        currencies[ticker]=(yf.Ticker(ticker).info or {}).get("currency")
        if currencies[ticker]!=work.currency:raise ValueError(f"Accountvaluta niet bevestigd voor {ticker}")
    for line in original.splitlines():
        line=line.strip()
        if not line or line.startswith("#"):continue
        parts=line.split()
        if len(parts)!=4 or parts[0].lower() not in ("/buy","/sell"):
            raise ValueError(f"Ongeldig commando: {line}")
        command,ticker=parts[0].lower(),parts[1].upper()
        price,size=float(parts[2]),int(parts[3])
        if not math.isfinite(price) or price<=0 or size<=0:raise ValueError("Prijs/aantal moet positief en eindig zijn")
        if ticker not in currencies:currencies[ticker]=(yf.Ticker(ticker).info or {}).get("currency")
        if currencies[ticker]!=work.currency:raise ValueError(f"{ticker}: koerseenheid verschilt van account {work.currency}")
        if command=="/buy":
            if ticker in work.positions:raise ValueError("Dubbele koop: gebruik een afzonderlijk gevalideerde positie")
            cost=trade_cost(price*size)
            if len(work.positions)>=MAX_POSITIONS or price*size+cost>work.cash:raise ValueError("Onvoldoende cash of positieruimte")
            work.cash-=price*size+cost
            work.positions[ticker]=LivePosition(ticker,"MANUAL",today_str(),price,size,cost,None,None)
            work.log_trade(today_str(),ticker,"MANUAL","BUY",price,size,cost,0.,0.,0.,"Manual")
        else:
            # A manually supplied price is the execution price, not another slippage estimate.
            work.close_position(ticker,today_str(),price,"Manual /sell",size=size,slippage=False)
    work.applied_batches.append(batch_id)
    work.save_state(today_str(),{})
    portfolio.__dict__.update(work.__dict__)
    # Only remove the input after the authoritative ledger has committed.
    path.write_text("")
    print(f"Handmatige batch opgeslagen in {work.currency}; geen brokerorders verstuurd")


# ============================================================
# LIVE: ADVISORY SCAN WITHOUT PORTFOLIO MUTATIONS
# ============================================================

def format_signal(s):
    return (f"{s.ticker} | {s.strategy} | koersdag {s.date} | koers {s.price:.4f} | "
        f"ATR {s.atr:.4f} | referentie-SL {s.sl:.4f} | referentie-TP {s.tp:.4f} | R/R {s.rr_ratio:.2f} | {yahoo_link(s.ticker)}")


def generate_exit_signals(portfolio,df,date=None):
    """Read-only exit advice; holding time comes from distinct observed sessions."""
    result=[]
    for ticker,original in portfolio.positions.items():
        g=df.loc[df.Ticker==ticker].sort_values("Date")
        if date is not None:g=g.loc[g.Date<=pd.Timestamp(date)]
        if g.empty:continue
        row=g.iloc[-1]
        if row.Date.date()<dt.date.fromisoformat(original.entry_date):continue
        pos=copy.copy(original)
        pos.days_open=len(g.loc[g.Date>=pd.Timestamp(pos.entry_date)])
        fill,reason=intraday_exit(row,pos)
        if reason is None:
            reason=close_exit_reason(row,pos)
            fill=float(row.Close)
        if reason:
            result.append(Signal(ticker,row.Date.date(),pos.strategy,"SELL",
                "Advies, geen uitvoering: "+reason,fill,pos.atr))
    return result


def run_live_engine():
    print(f"GLOBAL ENGINE — ADVIES {today_str()}")
    exchanges,missing={},[]
    for path in bouw_bestandslijst():
        members=load_tickers_from_file(path)
        if members:exchanges[label_voor(path)]=sorted(set(members))
        else:missing.append(path)
    tickers=sorted({t for members in exchanges.values() for t in members})
    if not tickers:raise RuntimeError("Geen tickerlijsten; geen verborgen fallbackuniversum")
    local_portfolio,portfolio_error=None,None
    if any(Path(p).exists() for p in (LIVE_STATE_FILE,LIVE_PORTFOLIO_FILE,LIVE_POSITIONS_FILE,LIVE_TRADES_FILE)):
        try:local_portfolio=LivePortfolio(START_CAPITAL)
        except Exception as exc:portfolio_error=f"Lokale portefeuille niet leesbaar: {exc}"
    if local_portfolio is not None:tickers=sorted(set(tickers)|set(local_portfolio.positions))
    df=download_history(tickers,period="2y")
    cache,skipped,errors={}, {}, []
    if portfolio_error:errors.append(portfolio_error)
    exit_histories=[]
    today=pd.Timestamp(today_str())
    for ticker in tickers:
        g=clean_history(df.loc[df.Ticker==ticker]) if "Ticker" in df else pd.DataFrame()
        if not g.empty:g=g.loc[g.Date<today]
        if len(g)<200:skipped[ticker]=f"{len(g)} geldige afgeronde sessies; minimaal 200 vereist"
        elif (today-g.Date.iloc[-1]).days>7:skipped[ticker]="laatste koers ouder dan 7 dagen"
        elif g.Volume.tail(20).sum()<=0:skipped[ticker]="geen recent handelsvolume"
        else:
            try:
                enriched=add_indicators(g)
                cache[ticker]=generate_signals_for_day(enriched,g.Date.iloc[-1].date())
                if local_portfolio is not None and ticker in local_portfolio.positions:exit_histories.append(enriched)
            except Exception as exc:errors.append(f"{ticker}: {type(exc).__name__}: {exc}")
    coverage=f"Datadekking: {len(tickers)} uniek gevraagd | {len(cache)} geanalyseerd | {len(skipped)} overgeslagen | {len(errors)} fouten"
    print(coverage)
    for t,reason in skipped.items():print(f"[SKIP:data] {t}: {reason}")
    rows,reports=[],[];saved=0
    for exchange,members in exchanges.items():
        signals=sorted([s for t in members for s in cache.get(t,[])],key=lambda s:(-s.rr_ratio,s.ticker,s.strategy))
        # Show the existing ten-entry report limit without claiming cash availability.
        selected=signals[:MAX_POSITIONS]
        reports.append(f"{exchange}: {sum(t in cache for t in members)}/{len(members)} geanalyseerd | {len(signals)} strategie-signalen | {len(selected)} getoond\n"+"\n".join(format_signal(s) for s in selected))
        for rank,sig in enumerate(selected,1):
            row=dict(ticker=sig.ticker,datum=today_str(),signal_date=sig.date.isoformat(),beurs=exchange,
                rank=rank,substrategie=sig.strategy,richting=sig.direction,reden=sig.reason,koers=sig.price,
                atr=sig.atr,stop=sig.sl,target=sig.tp,rr_ratio=sig.rr_ratio,price_unit="native_quote_unit")
            rows.append(row)
            saved+=bool(log_selectie(ticker=sig.ticker,datum=today_str(),strategie="bot_00xxxV2",
                beurs=exchange,koers=sig.price,parameters={k:v for k,v in row.items() if k not in ("ticker","datum","beurs","koers")}))
    exit_advice=[]
    if local_portfolio is not None and exit_histories:
        try:exit_advice=generate_exit_signals(local_portfolio,pd.concat(exit_histories,ignore_index=True))
        except Exception as exc:errors.append(f"Verkoopadvies: {exc}")
    partial=bool(skipped or missing or errors or saved<len(rows))
    columns=["ticker","datum","signal_date","beurs","rank","substrategie","richting","reden","koers","atr","stop","target","rr_ratio","price_unit"]
    pd.DataFrame(rows,columns=columns).to_csv(f"xxxV2_signalen_{today_str()}.csv",index=False)
    atomic_json("xxxV2_scan.json",dict(date=today_str(),complete=not partial,requested=len(tickers),analysed=len(cache),
        skipped=skipped,errors=errors,missing_files=missing,database=dict(saved=saved,requested=len(rows)),signals=rows,
        exit_advice=[dict(ticker=s.ticker,signal_date=s.date.isoformat(),reason=s.reason,reference_price=s.price) for s in exit_advice]))
    body=coverage+f"\nDatabase: {saved}/{len(rows)} selecties opgeslagen\n\n"+"\n\n".join(reports)
    if local_portfolio is not None:
        body+="\n\nLokale portefeuille ("+local_portfolio.currency+"): "+str(len(local_portfolio.positions))+" posities. Geen automatische uitvoering.\n"
        body+="\n".join(f"{s.ticker}: {s.reason} | koersdag {s.date} | referentie {s.price:.4f}" for s in exit_advice) or "Geen verkoopadviezen"
    body+="\n\nOorspronkelijke koerseenheden, geen valutaomrekening/positiegrootte. Onafhankelijke adviezen per strategie; dezelfde ticker kan meerdere signalen geven. Referentieniveaus op slotkoers, werkelijke volgende opening onbekend. Live scan voert geen transacties uit en wijzigt geen portefeuille."
    body+="\nOvergeslagen: "+json.dumps(skipped,ensure_ascii=False)+"\nOntbrekende/lege lijsten: "+(", ".join(missing) or "geen")
    body+="\nAnalysefouten: "+("; ".join(errors) or "geen")
    if os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        for report in reports:
            if not send_telegram(report,parse_mode=None):print("[WARN] Telegram niet verstuurd")
    else:print("Telegram uitgeschakeld (geen configuratie)")
    print(f"Database: {saved}/{len(rows)} selecties opgeslagen")
    if not send_email(f"{'[ONVOLLEDIG] ' if partial else ''}xxx V2 rapport {today_str()}",body):raise RuntimeError("E-mail niet verstuurd")
    print("E-mail verstuurd")
    if not cache or errors or saved<len(rows):raise RuntimeError("Scan/opslag onvolledig; zie rapport")
    print("Klaar met gemelde databeperkingen." if partial else "Klaar.")


# ============================================================
# BACKTEST: ONE INDEPENDENT NATIVE-UNIT ACCOUNT PER TICKER
# ============================================================

def affordable_size(cash,total,entry,stop):
    if not all(math.isfinite(v) and v>0 for v in (cash,total,entry,stop)) or stop>=entry:return 0
    reserve=total*MIN_CASH_RATIO
    low,high=0,max(0,int((cash-reserve)/entry))
    while low<high:
        size=(low+high+1)//2
        notional=entry*size;proceeds=stop*(1-SLIPPAGE_PCT)*size
        loss=notional+trade_cost(notional)-proceeds+trade_cost(proceeds)
        if notional+trade_cost(notional)<=cash-reserve and loss<=total*RISICO_PCT_PER_TRADE:low=size
        else:high=size-1
    return low


class BacktestPortfolio(LivePortfolio):
    def __init__(self,start_capital):
        super().__init__(start_capital,load=False,currency="native_quote_unit")
        self.closed_trades=[];self.daily_snapshots=[]

    def open_position(self,sig,prices):
        if sig.ticker in self.positions or len(self.positions)>=MAX_POSITIONS:return False
        opening=safe_float(sig.next_open)
        if not math.isfinite(opening) or opening<=0:return False
        entry=opening*(1+SLIPPAGE_PCT)
        cfg=STRAT_CONFIG[sig.strategy]
        stop=entry-cfg["sl_mult"]*sig.atr;target=entry+cfg["tp_mult"]*sig.atr
        total=self.current_total_value(prices)
        size=affordable_size(self.cash,total,entry,stop)
        if not size:return False
        cost=trade_cost(entry*size);self.cash-=entry*size+cost
        self.positions[sig.ticker]=LivePosition(sig.ticker,sig.strategy,str(sig.date),entry,size,cost,stop,target,sig.atr)
        return True

    def close_position(self,ticker,date,exit_price,reason):
        pos=copy.copy(self.positions[ticker]);before=self.cash
        super().close_position(ticker,str(date),exit_price,reason)
        trade=self.trades[-1]
        self.closed_trades.append(dict(entry_date=pos.entry_date,exit_date=str(date),ticker=ticker,strategy=pos.strategy,
            entry_price=pos.entry_price,exit_price=trade["price"],size=pos.size,entry_cost=pos.cost,
            exit_cost=trade["cost"],net=self.cash-before-pos.entry_price*pos.size-pos.cost,
            reason=reason,days_open=pos.days_open))

    def snapshot(self,date,prices):
        total=self.current_total_value(prices)
        self.daily_snapshots.append(dict(date=str(date),cash=self.cash,positions_value=total-self.cash,total=total,n_positions=len(self.positions)))


def intraday_exit(row,pos):
    if pos.sl is not None and row.Open<=pos.sl:return float(row.Open),"SL-gap"
    if pos.tp is not None and row.Open>=pos.tp:return float(row.Open),"TP-gap"
    if pos.sl is not None and row.Low<=pos.sl:return pos.sl,"SL"
    if pos.tp is not None and row.High>=pos.tp:return pos.tp,"TP"
    return None,None


def close_exit_reason(row,pos):
    if pos.strategy in ("Traag","Snel","Hyper Trend") and math.isfinite(row.MA20) and row.Close<row.MA20:return "Trend exit MA20"
    if pos.strategy in ("MRA Snel","MRA Traag","Hyper Scalp") and math.isfinite(row.RSI14) and row.RSI14>70:return "RSI exit"
    if pos.days_open>=MAX_HOLD_DAYS:return "Time exit"
    return None


def backtest_ticker(ticker,history,start=BACKTEST_START,end=None):
    g=clean_history(history)
    if g.empty:return BacktestPortfolio(START_CAPITAL)
    g=g.loc[g.Date<=pd.Timestamp(end or BACKTEST_END)].reset_index(drop=True)
    g=add_indicators(g)
    bt=BacktestPortfolio(START_CAPITAL)
    pending_buy,pending_sell=None,None
    for index,row in g.iterrows():
        if row.Date<pd.Timestamp(start):continue
        date=row.Date.date();opening={ticker:float(row.Open)}
        if pending_sell and ticker in bt.positions:
            bt.close_position(ticker,date,float(row.Open),pending_sell);pending_sell=None
        if pending_buy is not None:
            if ticker not in bt.positions and (row.Date-pd.Timestamp(pending_buy.date)).days<=7:
                sig=copy.copy(pending_buy);sig.next_open=float(row.Open);sig.date=date
                bt.open_position(sig,opening)
            pending_buy=None
        if ticker in bt.positions:
            pos=bt.positions[ticker];pos.days_open+=1
            fill,reason=intraday_exit(row,pos)
            if fill is not None:bt.close_position(ticker,date,fill,reason)
            else:pending_sell=close_exit_reason(row,pos)
        bt.snapshot(date,{ticker:float(row.Close)})
        if ticker not in bt.positions and index>=199:
            signals=generate_signals_for_day(g.iloc[[index]],date)
            if signals:pending_buy=signals[0]
    return bt


def account_stats(bt):
    snapshots=pd.DataFrame(bt.daily_snapshots)
    if snapshots.empty:return {}
    values=snapshots.total.astype(float)
    end=float(values.iloc[-1]);returns=values.pct_change(fill_method=None)
    returns.iloc[0]=values.iloc[0]/START_CAPITAL-1
    peaks=values.cummax().clip(lower=START_CAPITAL)
    days=(pd.Timestamp(snapshots.date.iloc[-1])-pd.Timestamp(snapshots.date.iloc[0])).days
    cagr=None
    if days>=365 and end>0:
        exponent=math.log(end/START_CAPITAL)/(days/365.25)
        cagr=math.expm1(exponent)*100 if exponent<700 else None
    std=returns.std()
    wins=sum(t["net"] for t in bt.closed_trades if t["net"]>0)
    losses=-sum(t["net"] for t in bt.closed_trades if t["net"]<0)
    return dict(final_value=end,return_pct=(end/START_CAPITAL-1)*100,cagr_pct=cagr,
        max_drawdown_pct=float(((values-peaks)/peaks*100).min()),
        sharpe=float(returns.mean()/std*math.sqrt(252)) if len(returns)>1 and math.isfinite(std) and std>1e-12 else None,
        profit_factor=wins/losses if losses>0 else None,closed_trades=len(bt.closed_trades),
        n_open=len(bt.positions),first_session=snapshots.date.iloc[0],last_session=snapshots.date.iloc[-1])


def run_backtest():
    tickers=sorted({t for path in bouw_bestandslijst() for t in load_tickers_from_file(path)})
    if not tickers:raise RuntimeError("Geen tickerlijsten")
    start=(pd.Timestamp(BACKTEST_START)-pd.Timedelta(days=730)).date().isoformat()
    end=(pd.Timestamp(BACKTEST_END)+pd.Timedelta(days=1)).date().isoformat()
    df=download_history(tickers,start=start,end=end)
    summaries,trades,snapshots,strategy_rows,skipped=[],[],[],[],[]
    for ticker in tickers:
        g=clean_history(df.loc[df.Ticker==ticker]) if "Ticker" in df else pd.DataFrame()
        if not g.empty:g=g.loc[g.Date<pd.Timestamp(today_str())]
        if g.empty or len(g.loc[g.Date<pd.Timestamp(BACKTEST_START)])<200:
            skipped.append(ticker);continue
        bt=backtest_ticker(ticker,g)
        stats=account_stats(bt)
        if not stats:skipped.append(ticker);continue
        summaries.append(dict(ticker=ticker,**stats))
        trades.extend(bt.closed_trades)
        snapshots.extend(dict(ticker=ticker,**row) for row in bt.daily_snapshots)
        for strategy in STRAT_CONFIG:
            closed=[r for r in bt.closed_trades if r["strategy"]==strategy]
            strategy_rows.append(dict(ticker=ticker,strategy=strategy,trades=len(closed),net_closed=sum(r["net"] for r in closed)))
    pd.DataFrame(summaries,columns=["ticker","final_value","return_pct","cagr_pct","max_drawdown_pct","sharpe","profit_factor","closed_trades","n_open","first_session","last_session"]).to_csv("backtest_summary_per_ticker.csv",index=False)
    pd.DataFrame(trades,columns=["entry_date","exit_date","ticker","strategy","entry_price","exit_price","size","entry_cost","exit_cost","net","reason","days_open"]).to_csv("backtest_trades.csv",index=False)
    pd.DataFrame(snapshots,columns=["ticker","date","cash","positions_value","total","n_positions"]).to_csv("backtest_portfolio.csv",index=False)
    pd.DataFrame(strategy_rows,columns=["ticker","strategy","trades","net_closed"]).to_csv("backtest_strategy_per_ticker.csv",index=False)
    print(f"Backtest: {len(summaries)} accounts | {len(skipped)} overgeslagen: {', '.join(skipped)}")
    print("Elk account: 50.000 oorspronkelijke koerseenheden per ticker. Geen gezamenlijk EUR-portefeuillerendement. Geen belastingmodel. Kosten/slippage zijn modelaannames, geen uitvoeringsgarantie.")
    if not summaries:raise RuntimeError("Geen bruikbare backtesthistorie")


if __name__ == "__main__":
    mode=sys.argv[1].lower() if len(sys.argv)>1 else "live"
    if mode=="live":run_live_engine()
    elif mode=="backtest":run_backtest()
    elif mode=="apply":apply_telegram_commands(LivePortfolio(START_CAPITAL),"commands.txt")
    else:sys.exit("Gebruik: python bot_00xxxV2.py [live|backtest|apply]")
