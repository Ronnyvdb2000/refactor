#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IBS/RSI and Opening Range Breakout advisory scans. No simulated fills,
portfolio mutations or broker execution; legacy state is read only for MR exits.
"""
import os
import sys
import json
import math
from pathlib import Path
from dataclasses import dataclass,asdict
from collections import Counter
from typing import List,Dict,Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf
from common import (today_str,safe_float,yahoo_link,download_history,
    load_tickers_from_file,bouw_bestandslijst,label_voor,
    compute_rsi_series,compute_ibs,compute_atr_series,send_email,send_telegram)
try:
    from db_logger import log_selectie
except ImportError as exc:
    print(f"[WARN] db_logger niet beschikbaar: {exc}")
    def log_selectie(*args,**kwargs):return False

PORTFOLIO_FILE="mr_portfolio.json"
CFG = {
    "ibs_entry":            0.20,
    "ibs_exit":             0.70,
    "rsi_period":           3,
    "rsi_entry":            10.0,
    "ma_trend":             200,
    "ma_exit":              20,
    "atr_period":           14,
    "orb_minutes":          15,
    "orb_vol_mult":         1.5,
    "orb_tp_mult":          2.0,
}

def load_portfolio():
    """Legacy simulation state is read, never reset, traded or saved by a scan."""
    if not Path(PORTFOLIO_FILE).exists():return {"positions":{}}
    state=json.loads(Path(PORTFOLIO_FILE).read_text())
    if not isinstance(state.get("positions"),dict) or any(not isinstance(pos,dict) for pos in state["positions"].values()):
        raise ValueError("Ongeldige positions in MR-state")
    return state


def clean_prices(frame,intraday=False,timezone=None):
    if frame is None or frame.empty:return pd.DataFrame()
    g=frame.copy()
    if isinstance(g.columns,pd.MultiIndex):
        for level in range(g.columns.nlevels):
            if "Close" in g.columns.get_level_values(level):
                g.columns=g.columns.get_level_values(level);break
    g=g.loc[:,~g.columns.duplicated()]
    if "Date" not in g:
        if "Datetime" in g:g=g.rename(columns={"Datetime":"Date"})
        elif isinstance(g.index,pd.DatetimeIndex):g=g.rename_axis("Date").reset_index()
    required=["Date","Open","High","Low","Close","Volume"]
    if any(c not in g for c in required):return pd.DataFrame()
    dates=pd.to_datetime(g.Date,errors="coerce")
    if intraday:
        if dates.dt.tz is None:
            if not timezone:raise ValueError("Intraday-tijdzone ontbreekt")
            dates=dates.dt.tz_localize(timezone,ambiguous="NaT",nonexistent="NaT")
        dates=dates.dt.tz_convert("UTC")
    else:
        if dates.dt.tz is not None:dates=dates.dt.tz_localize(None)
        dates=dates.dt.normalize()
    g["Date"]=dates
    for c in required[1:]:g[c]=pd.to_numeric(g[c],errors="coerce").replace([np.inf,-np.inf],np.nan)
    valid=g[required].notna().all(axis=1)&(g[["Open","High","Low","Close"]]>0).all(axis=1)
    valid &=(g.Volume>=0)&(g.High>=g[["Open","Low","Close"]].max(axis=1))
    valid &=g.Low<=g[["Open","Close"]].min(axis=1)
    return g.loc[valid].sort_values("Date").drop_duplicates("Date",keep="last").reset_index(drop=True)


def download_eod(tickers,period="2y"):
    return download_history(tickers,period=period)


def add_eod_indicators(df):
    parts=[]
    for ticker,group in df.groupby("Ticker",sort=False):
        g=group.sort_values("Date").copy()
        g["IBS"]=compute_ibs(g.High,g.Low,g.Close)
        g["RSI3"]=compute_rsi_series(g.Close,CFG["rsi_period"])
        g["MA20"]=g.Close.rolling(CFG["ma_exit"]).mean()
        g["MA200"]=g.Close.rolling(CFG["ma_trend"]).mean()
        g["ATR14"]=compute_atr_series(g.High,g.Low,g.Close,CFG["atr_period"])
        parts.append(g)
    return pd.concat(parts,ignore_index=True) if parts else df

@dataclass
class MRSignaal:
    ticker:    str
    price:     float
    ibs:       float
    rsi3:      float
    ma20:      float
    ma200:     float
    atr:       float
    stop:      float
    tp:        float
    rr:        float
    exchange:  str = ""
    signal_date: str = ""

@dataclass
class MRExitSignaal:
    ticker:    str
    price:     float
    ma20:      float
    ibs:       float
    reden:     str
    pnl_est:   float
    signal_date: str = ""

def generate_mr_signals(df: pd.DataFrame, exchange: str) -> Tuple[List[MRSignaal], List[MRExitSignaal]]:
    entries: List[MRSignaal]     = []
    exits:   List[MRExitSignaal] = []

    last_df = df.sort_values("Date").groupby("Ticker",sort=False).tail(1).reset_index(drop=True)

    for _, row in last_df.iterrows():
        ticker = row.get("Ticker")
        close  = safe_float(row.get("Close"))
        high   = safe_float(row.get("High"))
        low    = safe_float(row.get("Low"))
        ibs    = safe_float(row.get("IBS"))
        rsi3   = safe_float(row.get("RSI3"))
        ma20   = safe_float(row.get("MA20"))
        ma200  = safe_float(row.get("MA200"))
        atr    = safe_float(row.get("ATR14"))

        if any(not math.isfinite(x) for x in [close, ibs, rsi3, ma20, ma200]):
            continue
        if close <= 0 or atr <= 0 or not math.isfinite(atr):
            continue

        if (ibs < CFG["ibs_entry"] and rsi3 < CFG["rsi_entry"] and close > ma200):
            stop = low - 0.5 * atr
            tp   = ma20
            rr   = ((tp - close) / (close - stop)) if (close - stop) > 0 else 0

            if 0 < stop < close < tp and math.isfinite(rr) and rr > 0.5:
                entries.append(MRSignaal(
                    ticker=ticker, price=close,
                    ibs=ibs, rsi3=rsi3,
                    ma20=ma20, ma200=ma200,
                    atr=atr, stop=stop, tp=tp, rr=rr,
                    exchange=exchange, signal_date=pd.Timestamp(row["Date"]).date().isoformat(),
                ))

        if ibs > CFG["ibs_exit"]:
            exits.append(MRExitSignaal(ticker=ticker, price=close, ma20=ma20, ibs=ibs, reden=f"IBS={ibs:.2f} > {CFG['ibs_exit']}", pnl_est=0.0, signal_date=pd.Timestamp(row["Date"]).date().isoformat()))
        elif close > ma20:
            exits.append(MRExitSignaal(ticker=ticker, price=close, ma20=ma20, ibs=ibs, reden=f"Slot {close:.2f} > MA20 {ma20:.2f}", pnl_est=0.0, signal_date=pd.Timestamp(row["Date"]).date().isoformat()))

    entries.sort(key=lambda s: (s.ibs,s.ticker))
    return entries, exits


@dataclass
class ORBSignaal:
    ticker: str
    orb_high: float
    orb_low: float
    orb_range: float
    breakout: bool
    current: float
    stop: float
    tp: float
    vol_ratio: float
    exchange: str = ""
    signal_time: str = ""
    session_start: str = ""
    quote_currency: str = "native_quote_unit"


class ORBUnavailable(ValueError):pass


def utc_now():return pd.Timestamp.now(tz="UTC")


def session_timestamp(value):
    if isinstance(value,(int,float,np.integer,np.floating)):
        return pd.to_datetime(value,unit="s",utc=True)
    stamp=pd.Timestamp(value)
    if stamp.tzinfo is None:raise ORBUnavailable("Sessietijd zonder tijdzone")
    return stamp.tz_convert("UTC")


def orb_for_ticker(ticker,raw,metadata,exchange,asof):
    """The range must start at the provider's actual regular-session opening."""
    asof=pd.Timestamp(asof)
    if asof.tzinfo is None:raise ORBUnavailable("As-of moet tijdzone bevatten")
    asof=asof.tz_convert("UTC")
    try:
        timezone=metadata["exchangeTimezoneName"]
        zone=ZoneInfo(timezone)
        regular=metadata["currentTradingPeriod"]["regular"]
        opening=session_timestamp(regular["start"])
        closing=session_timestamp(regular["end"])
    except (KeyError,TypeError,ValueError) as exc:
        raise ORBUnavailable(f"Onvolledige sessiemetadata: {exc}") from exc
    if pd.isna(opening) or pd.isna(closing) or not pd.Timedelta(0)<closing-opening<=pd.Timedelta(days=1):
        raise ORBUnavailable("Ongeldige sessiegrenzen")
    if opening.tz_convert(zone).date()!=asof.tz_convert(zone).date() or not opening<=asof<closing:
        return None,("closed","geen lopende reguliere sessie")
    interval=pd.Timedelta(minutes=CFG["orb_minutes"])
    if asof<opening+2*interval:return None,("waiting","nog geen twee afgeronde 15-minutencandles")
    g=clean_prices(raw,intraday=True,timezone=timezone)
    if g.empty:raise ORBUnavailable("Geen geldige intraday OHLCV")
    g=g.loc[(g.Date>=opening)&(g.Date+interval<=asof)&(g.Date+interval<=closing)]
    if len(g)<2 or g.Date.iloc[0]!=opening:
        raise ORBUnavailable("Openingscandle ontbreekt; latere candle mag de openingsrange niet vervangen")
    offsets=(g.Date-opening)/interval
    if not np.isclose(offsets,np.round(offsets)).all():raise ORBUnavailable("Candles niet op reguliere 15-minutenrooster")
    if not (g.Date.diff().dropna()==interval).all():raise ORBUnavailable("Ontbrekende candles binnen de reguliere sessie")
    if asof-(g.Date.iloc[-1]+interval)>2*interval:raise ORBUnavailable("Laatste afgeronde candle is verouderd")
    first,last=g.iloc[0],g.iloc[-1]
    high,low=float(first.High),float(first.Low)
    if not high>low>0:raise ORBUnavailable("Openingsrange heeft geen positieve breedte")
    baseline=float(g.Volume.iloc[:-1].mean())
    if baseline<=0:raise ORBUnavailable("Geen vergelijkbaar eerder sessievolume")
    ratio=float(last.Volume)/baseline
    current=float(last.Close);target=high+CFG["orb_tp_mult"]*(high-low)
    breakout=current>high and ratio>=CFG["orb_vol_mult"]
    if not low<current<target:return None,("ok","prijs buiten bruikbare stop/doelrange")
    if breakout or current>high*.995:
        return ORBSignaal(ticker,high,low,high-low,breakout,current,low,target,ratio,
            exchange,last.Date.isoformat(),opening.isoformat(),metadata.get("currency","native_quote_unit")),("ok","signaal")
    return None,("ok","geen ORB-setup")


def generate_orb_signals(tickers,exchange,diagnostics=None,cache=None,asof=None):
    diagnostics=diagnostics if diagnostics is not None else {}
    cache=cache if cache is not None else {}
    asof=utc_now() if asof is None else asof
    result=[]
    for ticker in sorted(set(tickers)):
        if ticker not in cache:
            try:
                tk=yf.Ticker(ticker)
                raw=tk.history(period="5d",interval="15m",auto_adjust=True,prepost=False,timeout=15)
                md=tk.get_history_metadata()
                sig,status=orb_for_ticker(ticker,raw,md,exchange,asof)
                cache[ticker]=(sig,status)
            except (ORBUnavailable,KeyError,ValueError) as exc:cache[ticker]=(None,("data",str(exc)))
            except Exception as exc:cache[ticker]=(None,("error",f"{type(exc).__name__}: {exc}"))
        sig,status=cache[ticker];diagnostics[ticker]=status
        if sig is not None:
            # The same ticker can legitimately appear in several exchange lists.
            item=ORBSignaal(**asdict(sig));item.exchange=exchange;result.append(item)
    return sorted(result,key=lambda s:(not s.breakout,-s.vol_ratio,s.ticker))


def atomic_json(path,payload):
    temp=path+".tmp"
    with open(temp,"w",encoding="utf-8") as f:json.dump(payload,f,ensure_ascii=False,allow_nan=False,indent=2)
    os.replace(temp,path)


def deliver(mode,rows,reports,diagnostics,missing,errors):
    counts=Counter(category for category,_ in diagnostics.values())
    selected=len(rows);saved=0
    for row in rows:
        saved+=bool(log_selectie(ticker=row["ticker"],datum=today_str(),
            strategie="bot_00mr_ibsrsi" if mode=="eod" else "bot_00mr_orb",
            beurs=row["exchange"],koers=row.get("price",row.get("current")),parameters=row))
    partial=bool(missing or errors or counts["data"] or counts["error"] or saved<selected)
    coverage=(f"Datadekking: {len(diagnostics)} uniek gevraagd | {counts['ok']} geanalyseerd | "
        f"{counts['closed']} buiten sessie | {counts['waiting']} wachten op openingscandles | "
        f"{counts['data']} ontbrekende/onbruikbare data | {counts['error']} fouten")
    print(coverage)
    for ticker,(category,reason) in sorted(diagnostics.items()):
        if category!="ok":print(f"[{category.upper()}] {ticker}: {reason}")
    columns=list(MRSignaal.__dataclass_fields__) if mode=="eod" else list(ORBSignaal.__dataclass_fields__)
    pd.DataFrame(rows,columns=columns).to_csv(f"mr_{mode}_signalen_{today_str()}.csv",index=False)
    atomic_json(f"mr_{mode}_scan.json",dict(mode=mode,date=today_str(),complete=not partial,
        coverage=dict(counts),diagnostics=diagnostics,missing_files=missing,errors=errors,
        database=dict(saved=saved,requested=selected),signals=rows))
    body=coverage+f"\nDatabase: {saved}/{selected} adviezen opgeslagen\n\n"+"\n\n".join(reports)
    body+="\n\nKoersen in oorspronkelijke koerseenheden; geen valutaomrekening, europortefeuille, positieomvang of uitvoeringsgarantie. Geen transacties geboekt; bestaande portefeuillestate blijft intact."
    body+="\nOntbrekende/lege lijsten: "+(", ".join(missing) or "geen")
    body+="\nDatadetails: "+json.dumps(diagnostics,ensure_ascii=False)
    body+="\nOverige fouten: "+("; ".join(errors) or "geen")
    if os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        for report in reports:
            if not send_telegram(report,parse_mode=None):print("[WARN] Telegram niet verstuurd")
    else:print("Telegram uitgeschakeld (geen configuratie)")
    print(f"Database: {saved}/{selected} adviezen opgeslagen")
    if not send_email(f"{'[ONVOLLEDIG] ' if partial else ''}MR {mode.upper()} rapport {today_str()}",body):raise RuntimeError("E-mail niet verstuurd")
    print("E-mail verstuurd")
    normal=counts["ok"]+counts["closed"]+counts["waiting"]
    if not normal or counts["error"] or errors or saved<selected:raise RuntimeError("Scan/opslag onvolledig; zie rapport")
    print("Klaar met gemelde databeperkingen." if partial else "Klaar.")


def run_eod():
    print(f"MR EOD — ADVIES {today_str()}")
    exchanges,missing={},[]
    for path in bouw_bestandslijst():
        tickers=load_tickers_from_file(path)
        if tickers:exchanges[label_voor(path)]=sorted(set(tickers))
        else:missing.append(path)
    all_tickers=sorted({t for members in exchanges.values() for t in members})
    if not all_tickers:raise RuntimeError("Geen tickerlijsten")
    errors=[]
    try:portfolio=load_portfolio()
    except Exception as exc:
        errors.append(f"Legacy state niet leesbaar: {exc}");portfolio={"positions":{}}
    all_tickers=sorted(set(all_tickers)|set(portfolio["positions"]))
    df=download_eod(all_tickers,period="2y")
    diagnostics,cache={},{}
    today=pd.Timestamp(today_str())
    for ticker in all_tickers:
        g=clean_prices(df.loc[df.Ticker==ticker]) if "Ticker" in df else pd.DataFrame()
        if not g.empty:g=g.loc[g.Date<today]
        if len(g)<CFG["ma_trend"]:diagnostics[ticker]=("data",f"{len(g)} afgeronde koersdagen; minimaal {CFG['ma_trend']} vereist")
        elif (today-g.Date.iloc[-1]).days>7:diagnostics[ticker]=("data","laatste koers ouder dan 7 dagen")
        elif g.Volume.tail(20).sum()<=0:diagnostics[ticker]=("data","geen recent handelsvolume")
        else:
            try:
                enriched=add_eod_indicators(g)
                last=enriched.iloc[-1]
                if not all(math.isfinite(safe_float(last.get(c))) for c in ("IBS","RSI3","ATR14","MA20","MA200")):
                    diagnostics[ticker]=("data","onvolledige laatste indicatoren")
                else:
                    cache[ticker]=generate_mr_signals(enriched,"")
                    diagnostics[ticker]=("ok","geanalyseerd")
            except Exception as exc:diagnostics[ticker]=("error",f"{type(exc).__name__}: {exc}")
    reports,rows=[],[]
    for exchange,members in exchanges.items():
        entries=[s for t in members for s in cache.get(t,([],[]))[0]]
        entries.sort(key=lambda s:(s.ibs,s.ticker))
        lines=[f"{exchange}: {sum(t in cache for t in members)}/{len(members)} geanalyseerd | {len(entries)} MR-kandidaten"]
        for s in entries:
            s=MRSignaal(**asdict(s));s.exchange=exchange;rows.append(asdict(s))
            lines.append(f"{s.ticker}: koersdag {s.signal_date} | IBS {s.ibs:.3f} | RSI3 {s.rsi3:.2f} | koers {s.price:.4f} | stop {s.stop:.4f} | MA20-doel {s.tp:.4f} | R/R {s.rr:.2f} | {yahoo_link(s.ticker)}")
        reports.append("\n".join(lines))
    exit_lines=[]
    for ticker,pos in portfolio["positions"].items():
        if pos.get("systeem")!="MR":continue
        for s in cache.get(ticker,([],[]))[1]:exit_lines.append(f"{s.ticker}: koersdag {s.signal_date} | exitvoorwaarde {s.reden} | referentie {s.price:.4f}; geen uitvoering")
    reports.append("Exitvoorwaarden voor bestaande MR-posities:\n"+("\n".join(exit_lines) or "geen bruikbare exitvoorwaarden"))
    reports.append(f"Model: IBS<{CFG['ibs_entry']}, RSI({CFG['rsi_period']})<{CFG['rsi_entry']}, Close>MA{CFG['ma_trend']}, R/R>0,5. Dit zijn setupadviezen; de volgende openingskoers is onbekend.")
    deliver("eod",rows,reports,diagnostics,missing,errors)


ORB_EXCHANGES={"048 Nasdaq/NYSE":"tickers_048x.txt","047 Toronto":"tickers_047x.txt",
    "045 Londen":"tickers_045x.txt","041 Benelux Ierland":"tickers_041x.txt"}


def run_orb():
    print(f"MR ORB — ADVIES {utc_now().isoformat()}")
    diagnostics,cache,rows,reports,missing={},{},[],[],[]
    asof=utc_now()
    for exchange,path in ORB_EXCHANGES.items():
        tickers=load_tickers_from_file(path)
        if not tickers:missing.append(path);continue
        signals=generate_orb_signals(tickers,exchange,diagnostics=diagnostics,cache=cache,asof=asof)
        lines=[f"{exchange}: {len(signals)} ORB-adviezen"]
        for s in signals:
            rows.append(asdict(s))
            lines.append(f"{s.ticker}: {'BREAKOUT' if s.breakout else 'nabij pivot, onbevestigd'} | candle start {s.signal_time} | sessieopening {s.session_start} | {s.quote_currency} {s.current:.4f} | range {s.orb_low:.4f}-{s.orb_high:.4f} | stop {s.stop:.4f} | doel {s.tp:.4f} | volume {s.vol_ratio:.2f}x | {yahoo_link(s.ticker)}")
        reports.append("\n".join(lines))
    if not diagnostics:raise RuntimeError("Geen ORB-tickerlijsten")
    reports.append("ORB: 15-minutenrange vanaf bevestigde reguliere sessieopening, alleen afgeronde candles. Volume vergeleken met eerdere candles van dezelfde sessie, zonder de signaalcandle. Dit is vertraagde signalering, geen realtime orderbewaking of intraday exitservice.")
    deliver("orb",rows,reports,diagnostics,missing,[])


if __name__=="__main__":
    mode=sys.argv[1].lower() if len(sys.argv)>1 else "eod"
    if mode in ("eod","live"):run_eod()
    elif mode=="orb":run_orb()
    else:sys.exit("Gebruik: python bot_00mr.py [eod|live|orb]; backtest/status niet geïmplementeerd")
