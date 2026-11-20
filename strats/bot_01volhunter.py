#!/usr/bin/env python3
"""Weekly high-ATR dip scan of a-lists. Reports signals only; no order/exit engine."""
import argparse
import os
import math
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError
from common import (today_str, yahoo_link, load_tickers_from_file, label_voor,
                    compute_rsi_series, compute_atr_series, compute_ibs, send_email, send_telegram)
from common.yahoo import stop_on_throttle, YahooRateLimited

try:
    from common.db_logger import log_selectie
except Exception as exc:
    print(f"[WARN] db_logger niet beschikbaar ({type(exc).__name__})")
    def log_selectie(*args, **kwargs):
        return False

MIN_ATR_PCT = float(os.getenv("MIN_ATR_PCT", "6.0"))
MAX_ATR_PCT = float(os.getenv("MAX_ATR_PCT", "60.0"))
IBS_MAX = float(os.getenv("IBS_MAX", "0.25"))
RSI_MAX = float(os.getenv("RSI_MAX", "15.0"))
VEREIST_UPTREND = os.getenv("VEREIST_UPTREND", "1") == "1"
TOP_N = int(os.getenv("TOP_N", "3"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
BATCH_PAUSE_SEC = float(os.getenv("BATCH_PAUSE_SEC", "5"))
RSI_PERIOD, ATR_PERIOD, MA_TREND = 3, 14, 100
FIELDS = ["Open", "High", "Low", "Close", "Volume"]


class DataUnavailable(ValueError):
    pass


class InvalidLatest(DataUnavailable):
    pass


class ProviderStop(RuntimeError):
    pass


@dataclass
class RequestGate:
    blocked_reason: str = ""
    deadline: Optional[float] = None


def validate_config():
    if not (all(math.isfinite(x) for x in [MIN_ATR_PCT, MAX_ATR_PCT, IBS_MAX, RSI_MAX])
            and 0 < MIN_ATR_PCT <= MAX_ATR_PCT and 0 <= IBS_MAX <= 1 and 0 <= RSI_MAX <= 100
            and TOP_N > 0 and 1 <= BATCH_SIZE <= 150
            and math.isfinite(BATCH_PAUSE_SEC) and 0 <= BATCH_PAUSE_SEC <= 30):
        raise ValueError("Ongeldige ATR/IBS/RSI/TOP_N/BATCH_SIZE/BATCH_PAUSE_SEC configuratie")


def pause(seconds, gate):
    while seconds > 0:
        check_gate(gate)
        part = min(30, seconds)
        time.sleep(part)
        seconds -= part


def check_gate(gate):
    if gate.deadline is not None and time.monotonic() >= gate.deadline:
        gate.blocked_reason = "scanbudget van 225 minuten bereikt; resterende aanvragen gestopt"
    if gate.blocked_reason:
        raise ProviderStop(gate.blocked_reason)


def yahoo_request(fn, gate):
    for attempt in range(2):
        check_gate(gate)
        try:
            with stop_on_throttle():
                return fn()
        except (YFRateLimitError, YahooRateLimited):
            if attempt == 1:
                gate.blocked_reason = "Yahoo-limiet blijft na één pauze/herpoging; verdere aanvragen gestopt"
                raise ProviderStop(gate.blocked_reason)
            print("[RATE] Yahoo-limiet: 60s rust; enige herpoging", flush=True)
            pause(60, gate)


def bouw_bestandslijst_a():
    return [f"data/tickers_{n:03d}a.txt" for n in range(41, 60)]


def _normalise(df, ticker):
    """Retain invalid observed rows so they cannot become an older signal silently."""
    if df is None or df.empty:
        return None
    g = df.copy()
    if isinstance(g.columns, pd.MultiIndex):
        levels = [i for i in range(g.columns.nlevels) if ticker in g.columns.get_level_values(i)]
        if len(levels) != 1:
            raise DataUnavailable("ambigue tickerkolommen")
        g = g.xs(ticker, axis=1, level=levels[0]).copy()
    if "Date" not in g and "Datetime" not in g:
        if not isinstance(g.index, pd.DatetimeIndex):
            raise DataUnavailable("koersdatums ontbreken")
        g.index.name = "Date"
        g = g.reset_index()
    g = g.rename(columns={"Datetime": "Date"})
    if not set(FIELDS + ["Date"]).issubset(g.columns) or g.columns.duplicated().any():
        raise DataUnavailable("OHLCV-velden ontbreken of zijn dubbel")
    # A batch union calendar has all-NaN rows for sessions on other exchanges.
    g = g.loc[~g[FIELDS].isna().all(axis=1)].copy()
    g["Ticker"] = ticker
    return g


def prepare_history(df):
    if df is None or df.empty or not set(FIELDS + ["Date"]).issubset(df.columns):
        raise DataUnavailable("geen gedateerde OHLCV-historie")
    g = df.copy()
    dates = pd.to_datetime(g.Date, errors="coerce")
    if dates.dt.tz is not None:
        dates = dates.dt.tz_localize(None)
    g["Date"] = dates.dt.normalize()
    removed = int(g.Date.isna().sum() + g.Date.ge(pd.Timestamp(today_str())).sum())
    g = g.loc[g.Date.notna() & (g.Date < pd.Timestamp(today_str()))]
    removed += int(g.Date.duplicated(keep="last").sum())
    g = g.sort_values("Date").drop_duplicates("Date",keep="last").set_index("Date")
    g[FIELDS] = g[FIELDS].apply(pd.to_numeric, errors="coerce")
    valid = (np.isfinite(g[FIELDS]).all(axis=1) & (g[FIELDS[:4]] > 0).all(axis=1)
             & (g.Volume >= 0) & (g.High >= g[["Open","Low","Close"]].max(axis=1))
             & (g.Low <= g[["Open","High","Close"]].min(axis=1)))
    if g.empty:
        raise DataUnavailable("geen afgeronde koersdagen")
    if not valid.iloc[-1]:
        raise InvalidLatest(f"ongeldige laatste OHLCV op {g.index[-1].date()}")
    if (pd.Timestamp(today_str())-g.index[-1]).days > 7:
        raise DataUnavailable("laatste koers ouder dan zeven dagen")
    # A contiguous usable tail prevents indicators bridging invalid bars/long gaps.
    broken = (~valid).to_numpy() | g.index.to_series().diff().dt.days.gt(7).to_numpy()
    if broken.any():
        pos = np.flatnonzero(broken)[-1]
        start = pos + (0 if valid.iloc[pos] else 1)
        removed += start
        g = g.iloc[start:]
    if len(g) < MA_TREND:
        raise DataUnavailable("minder dan 100 opeenvolgende geldige sessies")
    if g.Volume.iloc[-1] <= 0 or g.Volume.tail(20).sum() <= 0:
        raise DataUnavailable("geen volume op laatste koersdag of in recent venster")
    g.attrs["data_note"] = f"{removed} onbruikbare/dubbele/onvoltooide rijen buiten bruikbare staart" if removed else ""
    return g


def download_batch(tickers, period="8mo", issues=None, gate=None):
    issues = issues if issues is not None else []
    gate = gate if gate is not None else RequestGate()
    tickers = sorted(set(tickers))
    out, raw = {}, {}
    if not tickers:
        return out
    try:
        data = yahoo_request(lambda: yf.download(tickers, auto_adjust=True, group_by="ticker",
                              progress=False, threads=False, period=period, timeout=15), gate)
        if data is not None and not data.empty:
            for ticker in tickers:
                try:
                    source = data if isinstance(data.columns,pd.MultiIndex) or len(tickers)==1 else None
                    raw[ticker] = _normalise(source, ticker)
                except DataUnavailable as exc:
                    issues.append(f"{ticker}: batchdata {exc}")
    except ProviderStop:
        return out
    except Exception as exc:
        issues.append(f"batch mislukt ({type(exc).__name__}); individuele heraanvraag")
    for ticker in tickers:
        try:
            check_gate(gate)
            g = raw.get(ticker)
            if g is None or g.empty:
                g = _normalise(yahoo_request(lambda: yf.Ticker(ticker).history(
                    period=period, interval="1d", auto_adjust=True, keepna=True, timeout=15), gate), ticker)
                pause(.5, gate)
            try:
                hist = prepare_history(g)
            except InvalidLatest:
                # Repair must restore the same observed latest completed session.
                dates = pd.to_datetime(g.Date, errors="coerce")
                if dates.dt.tz is not None:
                    dates = dates.dt.tz_localize(None)
                expected = dates.loc[dates < pd.Timestamp(today_str())].max().normalize()
                fixed = _normalise(yahoo_request(lambda: yf.Ticker(ticker).history(
                    period=period, interval="1d", auto_adjust=True, repair=True, keepna=True, timeout=15), gate), ticker)
                hist = prepare_history(fixed)
                if hist.index[-1] != expected:
                    raise DataUnavailable("Yahoo-repair herstelde laatste koersdag niet")
                note = f"Yahoo-repair voor {expected.date()}; providerherstel, niet onafhankelijk geverifieerd"
                hist.attrs["data_note"] = "; ".join(filter(None,[hist.attrs.get("data_note"),note]))
                print(f"[DATA] {ticker}: {note}",flush=True)
                pause(.5, gate)
            out[ticker] = hist
        except ProviderStop:
            break
        except DataUnavailable as exc:
            issues.append(f"{ticker}: {exc}")
        except Exception as exc:
            issues.append(f"{ticker}: downloaden/herstel mislukt ({type(exc).__name__}: {exc})")
    return out


@dataclass
class VolSignaal:
    ticker: str
    price: float
    atr: float
    atr_pct: float
    ibs: float
    rsi3: float
    ma100: float
    pct_from_20d_high: float
    stop: float
    tp: float
    score: float
    signal_date: str = ""
    data_note: str = ""


def evaluate_history(ticker, g):
    close, high, low = g.Close, g.High, g.Low
    atr = float(compute_atr_series(high,low,close,ATR_PERIOD).iloc[-1])
    price = float(close.iloc[-1])
    atrp = atr/price*100
    ma = float(close.rolling(MA_TREND).mean().iloc[-1])
    high20 = float(high.tail(20).max())
    if not all(math.isfinite(x) and x > 0 for x in (atr,price,atrp,ma,high20)):
        raise DataUnavailable("ongeldige ATR/koers/MA100/20d-high")
    high_atr = MIN_ATR_PCT <= atrp <= MAX_ATR_PCT
    ibs = float(compute_ibs(high,low,close).iloc[-1])
    rsi = float(compute_rsi_series(close,RSI_PERIOD).iloc[-1])
    if not (math.isfinite(ibs) and 0 <= ibs <= 1 and math.isfinite(rsi) and 0 <= rsi <= 100):
        raise DataUnavailable("IBS/RSI3 onbruikbaar (bijvoorbeeld vlakke dagrange)")
    stop, tp = price-1.5*atr, price+2.5*atr
    if not high_atr or ibs > IBS_MAX or rsi > RSI_MAX or (VEREIST_UPTREND and price <= ma):
        return high_atr, None
    if not (math.isfinite(stop) and 0 < stop < price < tp and math.isfinite(tp)):
        raise DataUnavailable("geen positieve geldige ATR-referentiestop/doel")
    return high_atr, VolSignaal(ticker,price,atr,atrp,ibs,rsi,ma,(price/high20-1)*100,
                stop,tp,atrp*(1-ibs),str(g.index[-1].date()),g.attrs.get("data_note",""))


def analyse_ticker_df(ticker, df):
    """Public helper accepts raw OHLCV and applies the same validation as live."""
    return evaluate_history(ticker,prepare_history(df))[1]


def format_bericht(exchange_name, signalen, universum_grootte, hoge_atr_grootte, analysed=None, candidates=None):
    lines=[f"VOLATIELE DIP-SCAN — {exchange_name} ({today_str()})",
           f"{analysed if analysed is not None else universum_grootte}/{universum_grootte} geanalyseerd | "
           f"{hoge_atr_grootte} binnen ATR-band | {candidates if candidates is not None else len(signalen)} kandidaten | {len(signalen)} topselecties"]
    for rank,s in enumerate(signalen,1):
        lines.append(f"{rank}. {s.ticker}: {s.price:.6g} bronkoerseenheden | koersdag {s.signal_date} | "
            f"ATR {s.atr:.6g} ({s.atr_pct:.2f}%) | IBS {s.ibs:.3f} | RSI3 {s.rsi3:.2f} | "
            f"vs 20d-high {s.pct_from_20d_high:+.2f}% | referentiestop {s.stop:.6g} | "
            f"referentiedoel {s.tp:.6g} | {yahoo_link(s.ticker)}" + (f" | {s.data_note}" if s.data_note else ""))
    if not signalen:
        lines.append("Geen kandidaten geselecteerd.")
    lines.append(f"Eigen heuristiek, geen orders of gevalideerde strategie. ATR14 {MIN_ATR_PCT:g}–{MAX_ATR_PCT:g}%, "
        f"IBS <= {IBS_MAX:g}, RSI3 <= {RSI_MAX:g}" + (", koers > MA100." if VEREIST_UPTREND else ".") +
        " Dagdata; koersvaluta/subeenheid niet omgerekend. Stop/doel zijn referenties op de slotkoers; "
        "geen uitvoeringsprijs, sizing, backtest of exit-tracking.")
    return "\n\n".join(lines)


def notify_telegram(body):
    """Send plain-text report parts within Telegram's UTF-16 length limit."""
    parts, current, units = [], [], 0
    for char in body:
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > 3900:
            parts.append(''.join(current))
            current, units = [], 0
        current.append(char)
        units += width
    if current:
        parts.append(''.join(current))
    success = True
    for number, part in enumerate(parts, 1):
        if not send_telegram(part, parse_mode=None):
            print(f"::warning::Dip-Scan Telegram deel {number}/{len(parts)} niet verzonden", flush=True)
            success = False
    return success


def run_live():
    validate_config()
    gate = RequestGate(deadline=time.monotonic()+225*60)
    exchanges, requested = {}, set()
    issues, errors, reports = [], [], []
    for filename in bouw_bestandslijst_a():
        try:
            members = sorted(set(load_tickers_from_file(filename)))
            if not members:
                issues.append(f"{filename}: ontbrekende of lege tickerlijst")
                continue
            exchanges[label_voor(filename)] = members
            requested.update(members)
        except Exception as exc:
            errors.append(f"{filename}: laden mislukt ({exc})")
    cache, downloaded = {}, set()
    tickers = sorted(requested)
    for start in range(0,len(tickers),BATCH_SIZE):
        if gate.blocked_reason:
            break
        batch = tickers[start:start+BATCH_SIZE]
        data = download_batch(batch, issues=issues, gate=gate)
        downloaded.update(data)
        for ticker,g in data.items():
            try:
                note = g.attrs.get("data_note", "")
                if note:
                    issues.append(f"{ticker}: {note}")
                cache[ticker] = evaluate_history(ticker,g)
            except DataUnavailable as exc:
                issues.append(f"{ticker}: {exc}")
            except Exception as exc:
                errors.append(f"{ticker}: analyse mislukt ({type(exc).__name__}: {exc})")
        print(f"[VOORTGANG] batch {start//BATCH_SIZE+1}/{math.ceil(len(tickers)/BATCH_SIZE)}; "
              f"{len(cache)} analyses; {len(data)}/{len(batch)} histories bruikbaar",flush=True)
        if start+BATCH_SIZE < len(tickers) and not gate.blocked_reason:
            print(f"[PAUZE] {BATCH_PAUSE_SEC:g}s rust voor volgende batch",flush=True)
            try:
                pause(BATCH_PAUSE_SEC,gate)
            except ProviderStop:
                break
    if gate.blocked_reason:
        errors.append(f"{gate.blocked_reason}; {len(requested-downloaded)} tickers zonder bruikbare download")
    stored = selected = 0
    for exchange,members in exchanges.items():
        analysed = [cache[t] for t in members if t in cache]
        high_count = sum(high for high,_ in analysed)
        candidates = sorted([sig for _,sig in analysed if sig],key=lambda s:(-s.score,s.ticker))
        top = candidates[:TOP_N]
        selected += len(top)
        for rank,sig in enumerate(top,1):
            try:
                params = dict(vars(sig)); params.update(rank=rank,grafiek=yahoo_link(sig.ticker),pct_from_high=sig.pct_from_20d_high)
                if not log_selectie(ticker=sig.ticker,datum=sig.signal_date,strategie="bot_01volhunter",
                                    beurs=exchange,koers=sig.price,parameters=params):
                    raise RuntimeError("geen opslagbevestiging")
                stored += 1
            except Exception as exc:
                errors.append(f"{sig.ticker}: database mislukt ({exc})")
        report = format_bericht(exchange,top,len(members),high_count,len(analysed),len(candidates))
        reports.append(report)
        print(report,flush=True)
    if not cache:
        errors.append("Geen bruikbare analyses")
    summary = f"{len(cache)}/{len(requested)} unieke tickers geanalyseerd. Database {stored}/{selected} selecties opgeslagen."
    body = "\n\n".join([summary]+reports+["Databeperkingen:"]+issues+["Fouten:"]+errors)
    print(body,flush=True)
    if not notify_telegram(body):
        errors.append("Telegram niet volledig verzonden")
    if not send_email(("[ONVOLLEDIG] " if issues or errors else "")+"Volatiele Dip-Scan "+today_str(),body):
        errors.append("E-mail niet verzonden")
    if errors:
        raise RuntimeError(f"{len(errors)} fout(en): "+"; ".join(errors[:5]))
    print("Klaar — Telegram en e-mail verzonden.",flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",nargs="?",choices=["live"],default="live")
    parser.parse_args()
    run_live()
