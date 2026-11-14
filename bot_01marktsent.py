#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Marktsentiment: dagelijkse technische score plus optioneel Engels VADER-nieuws.

Live genereert adviezen, geen orders. Backtest simuleert uitsluitend de
technische component per ticker in oorspronkelijke koerseenheden, zonder FX.
"""
import argparse
import os
import math
import time
from dataclasses import dataclass, replace
from typing import Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from common import (
    today_str, safe_float, yahoo_link, download_history,
    load_tickers_from_file, bouw_bestandslijst, label_voor,
    compute_rsi_series, compute_macd_series, compute_atr_series,
    send_email, trade_cost, SLIPPAGE_PCT,
)
try:
    from db_logger import log_selectie
except ImportError:
    def log_selectie(*args, **kwargs):
        return False

START_CAPITAL = 50_000.0
RISICO_PCT_PER_TRADE = .05
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
NEWSAPI_DAILY_BUDGET = 95  # Per procesrun, geen gedeeld dagquotum.
_newsapi_calls_used = 0
_newsapi_rate_limited = False
_newsapi_blocked_reason = ""
_news_status = {}
_vader = SentimentIntensityAnalyzer()
_company_name_cache = {}
MS_CFG = {
    "rsi_period":         14,
    "rsi_oversold":       35.0,
    "rsi_momentum_low":   55.0,
    "rsi_momentum_high":  70.0,
    "macd_fast":          12,
    "macd_slow":          26,
    "macd_signal":        9,
    "macd_cross_lookback": 5,
    "sma_trend_period":   200,
    "sma_short_period":   50,
    "max_pct_above_sma_short": 15.0,
    "atr_period":         14,
    "sentiment_lookback_days": 7,
    "sentiment_min_articles":  2,
    "min_score":        4.0,
}

BACKTEST_START = "2021-01-01"
BACKTEST_END   = today_str()


class DataUnavailable(ValueError):
    """Expected provider-data limitation, reported separately from code errors."""


def resolve_company_name(ticker):
    if ticker not in _company_name_cache:
        name = ticker.split('.')[0]
        try:
            info = yf.Ticker(ticker).get_info() or {}
            name = info.get('shortName') or info.get('longName') or name
        except Exception:
            pass
        _company_name_cache[ticker] = name
    return _company_name_cache[ticker]


def fetch_news_sentiment(ticker, company_hint='', asof=None):
    global _newsapi_calls_used, _newsapi_rate_limited, _newsapi_blocked_reason
    if not NEWSAPI_KEY:
        _news_status[ticker] = 'NEWSAPI_KEY ontbreekt; alleen technisch'
        return None, 0
    if _newsapi_blocked_reason:
        _news_status[ticker] = _newsapi_blocked_reason
        return None, 0
    if _newsapi_rate_limited:
        _news_status[ticker] = 'NewsAPI geblokkeerd na 429'
        return None, 0
    if _newsapi_calls_used >= NEWSAPI_DAILY_BUDGET:
        _news_status[ticker] = 'nieuwsbudget van deze run uitgeput'
        return None, 0
    now = pd.Timestamp.now(tz='UTC') if asof is None else pd.Timestamp(asof)
    if now.tzinfo is None:
        now = now.tz_localize('UTC')
    else:
        now = now.tz_convert('UTC')
    since = now - pd.Timedelta(days=MS_CFG['sentiment_lookback_days'])
    params = {'q': company_hint or ticker, 'from': since.isoformat(), 'to': now.isoformat(),
              'sortBy':'publishedAt', 'language':'en', 'pageSize':20}
    try:
        _newsapi_calls_used += 1
        response = requests.get('https://newsapi.org/v2/everything', params=params,
                                headers={'X-Api-Key':NEWSAPI_KEY}, timeout=10)
        if response.status_code == 429:
            _newsapi_rate_limited = True
        if response.status_code != 200:
            if response.status_code in (401,403,426):
                _newsapi_blocked_reason = f'NewsAPI HTTP {response.status_code}; verdere requests overgeslagen'
            _news_status[ticker] = f'NewsAPI HTTP {response.status_code}; geen sentiment'
            return None, 0
        payload = response.json()
        if not isinstance(payload, dict) or payload.get('status') != 'ok' or not isinstance(payload.get('articles'), list):
            _news_status[ticker] = 'ongeldig NewsAPI-antwoord'
            return None, 0
    except (requests.RequestException, ValueError):
        # Do not log raw request errors: they can contain credentials or URLs.
        _news_status[ticker] = 'NewsAPI niet beschikbaar of antwoord onleesbaar'
        return None, 0
    scores, seen_urls, seen_titles = [], set(), set()
    for article in payload['articles']:
        if not isinstance(article, dict):
            continue
        title, desc = article.get('title'), article.get('description')
        title = title.strip() if isinstance(title,str) else ''
        desc = desc.strip() if isinstance(desc,str) else ''
        if title.casefold() == '[removed]' or desc.casefold() == '[removed]':
            continue
        text = ' '.join(x for x in (title,desc) if x)
        date = pd.to_datetime(article.get('publishedAt'), errors='coerce', utc=True)
        if not text or pd.isna(date) or not since <= date <= now:
            continue
        url = article.get('url')
        url = url.strip() if isinstance(url,str) else ''
        key = title.casefold() or text.casefold()
        if key in seen_titles or (url and url in seen_urls):
            continue
        score = safe_float(_vader.polarity_scores(text).get('compound'))
        if not math.isfinite(score) or not -1 <= score <= 1:
            continue
        seen_titles.add(key)
        if url:
            seen_urls.add(url)
        scores.append(score)
    if len(scores) < MS_CFG['sentiment_min_articles']:
        _news_status[ticker] = f'onvoldoende bruikbare unieke artikelen ({len(scores)})'
        return None, len(scores)
    _news_status[ticker] = 'Engels VADER-nieuws beschikbaar'
    return float(np.mean(scores)), len(scores)


def sentiment_bonus(score):
    if score is None or not math.isfinite(score):
        return 0.0
    if score >= .15:
        return min(1.0,score)
    if score <= -.15:
        return max(-1.0,score)
    return 0.0


def prepare_history(raw, live=True):
    required = ['Date','Open','High','Low','Close','Volume']
    if raw is None or raw.empty or not set(required).issubset(raw.columns):
        raise DataUnavailable('ontbrekende OHLCV-historie')
    frame = raw.copy()
    frame['Date'] = pd.to_datetime(frame.Date,errors='coerce')
    if frame.Date.dt.tz is not None:
        frame['Date'] = frame.Date.dt.tz_localize(None)
    frame['Date'] = frame.Date.dt.normalize()
    frame = frame.loc[frame.Date.notna() & (frame.Date < pd.Timestamp(today_str()))]
    frame = frame.sort_values('Date').drop_duplicates('Date',keep='last')
    for col in required[1:]:
        frame[col] = pd.to_numeric(frame[col],errors='coerce')
    price_cols = ['Open','High','Low','Close']
    valid = (np.isfinite(frame[required[1:]]).all(axis=1) & (frame[price_cols]>0).all(axis=1)
             & (frame.Volume>=0) & (frame.High>=frame[['Open','Close','Low']].max(axis=1))
             & (frame.Low<=frame[['Open','Close','High']].min(axis=1)))
    if frame.empty or (live and not valid.iloc[-1]):
        raise DataUnavailable('ontbrekende/ongeldige laatste OHLCV-rij')
    discarded = int((~valid).sum())
    frame = frame.loc[valid].reset_index(drop=True)
    if len(frame)<210:
        raise DataUnavailable('minder dan 210 geldige afgeronde sessies')
    if live:
        if (pd.Timestamp(today_str())-frame.Date.iloc[-1]).days>7:
            raise DataUnavailable('laatste koers ouder dan zeven dagen')
        if frame.Volume.tail(20).sum()<=0:
            raise DataUnavailable('geen recent handelsvolume')
    frame.attrs['discarded_rows'] = discarded
    return frame


@dataclass
class MSSignaal:
    ticker: str
    price: float
    rsi_monthly: float  # Databasecompatibiliteit: werkelijk RSI op dagelijkse bars.
    macd_label: str
    tech_score: float
    sentiment_score: Optional[float]
    sentiment_n: int
    score: float
    atr: float
    stop: float
    signal_date: str = ''
    news_status: str = 'nieuws niet gevraagd'


def add_sentiment(signal, asof=None):
    if NEWSAPI_KEY and not _newsapi_rate_limited and not _newsapi_blocked_reason and _newsapi_calls_used < NEWSAPI_DAILY_BUDGET:
        name = resolve_company_name(signal.ticker)
    else:
        name = ''
    score, n = fetch_news_sentiment(signal.ticker, company_hint=name, asof=asof)
    return replace(signal,sentiment_score=score,sentiment_n=n,
                   score=signal.tech_score+sentiment_bonus(score),news_status=_news_status[signal.ticker])


def analyse_ticker(ticker, df_ticker, met_sentiment=False):
    # Callers validate once; the backtest supplies a chronological historical slice.
    df = df_ticker.sort_values('Date').reset_index(drop=True)
    if len(df)<210:
        return None
    close, high, low = df.Close, df.High, df.Low
    rsi_series = compute_rsi_series(close,MS_CFG['rsi_period'])
    macd_line, signal_line, _ = compute_macd_series(close,MS_CFG['macd_fast'],MS_CFG['macd_slow'],MS_CFG['macd_signal'])
    atr_series = compute_atr_series(high,low,close,MS_CFG['atr_period'])
    rsi,macd,sig,atr,price = [safe_float(x.iloc[-1]) for x in (rsi_series,macd_line,signal_line,atr_series,close)]
    sma = safe_float(close.tail(MS_CFG['sma_trend_period']).mean())
    sma_short = safe_float(close.tail(MS_CFG['sma_short_period']).mean())
    if not all(math.isfinite(x) for x in (rsi,macd,sig,atr,price,sma,sma_short)) or min(price,atr,sma,sma_short)<=0:
        raise DataUnavailable('ongeldige technische indicatoren')
    tech = float(rsi<=MS_CFG['rsi_oversold'] or MS_CFG['rsi_momentum_low']<=rsi<=MS_CFG['rsi_momentum_high'])
    diff = (macd_line-signal_line).tail(MS_CFG['macd_cross_lookback']+1)
    if not np.isfinite(diff).all():
        raise DataUnavailable('ongeldige MACD-historie')
    cross = bool(((diff.shift(1)<=0) & (diff>0)).any())
    tech += float(cross)+float(price>sma)
    # Original rule only caps extension above SMA50; a price below it can pass.
    tech += float((price/sma_short-1)*100<=MS_CFG['max_pct_above_sma_short'])
    stop = price-2*atr
    if not 0<stop<price:
        raise DataUnavailable('ongeldige ATR-stop')
    out = MSSignaal(ticker,price,rsi,'Verse crossover' if cross else 'Bullish' if macd>sig else 'Bearish',
                    tech,None,0,tech,atr,stop,pd.Timestamp(df.Date.iloc[-1]).date().isoformat())
    return add_sentiment(out) if met_sentiment else out


def format_bericht(exchange,signals):
    lines = [f'MARKTSENTIMENT — {exchange} ({today_str()})']
    if not signals:
        lines.append('Geen kandidaten boven de scoredrempel.')
    for sig in signals:
        news = f'{sig.sentiment_score:+.3f} ({sig.sentiment_n} artikelen)' if sig.sentiment_score is not None else f'onbekend ({sig.sentiment_n} artikelen)'
        lines.append(f'• {sig.ticker}: score {sig.score:.3f} (technisch {sig.tech_score:.0f}/4) | RSI({MS_CFG["rsi_period"]}) dagdata {sig.rsi_monthly:.1f} | '
                     f'MACD {sig.macd_label} | nieuws {news} | {sig.price:.4f} bronkoerseenheden | '
                     f'koersdag {sig.signal_date} | referentiestop {sig.stop:.4f} | {sig.news_status} | {yahoo_link(sig.ticker)}')
    lines.append('Eigen heuristiek, geen orders. Koersen kunnen subeenheden zoals pence zijn. '
                 'VADER beoordeelt Engelse tekst, geen gecontroleerde relevantie of koersvoorspelling. '
                 'Onbekend nieuws geeft bonus 0; nieuws is van het scanmoment, technische data van de vermelde koersdag.')
    return '\n'.join(lines)


def run_live_engine():
    global _newsapi_calls_used,_newsapi_rate_limited,_newsapi_blocked_reason
    _newsapi_calls_used = 0
    _newsapi_rate_limited = False
    _newsapi_blocked_reason = ""
    _news_status.clear()
    _company_name_cache.clear()
    asof = pd.Timestamp.now(tz='UTC')
    exchanges,issues,errors = {},[],[]
    for filename in bouw_bestandslijst():
        tickers = sorted(set(load_tickers_from_file(filename)))
        if tickers:
            exchanges[label_voor(filename)] = tickers
        else:
            issues.append(f'{filename}: ontbrekende of lege tickerlijst')
    tickers = sorted({t for values in exchanges.values() for t in values})
    raw = download_history(tickers,period='2y')
    technical = {}
    if not NEWSAPI_KEY:
        issues.append('NEWSAPI_KEY ontbreekt: alleen technische selectie')
    for ticker in tickers:
        try:
            group = raw.loc[raw.Ticker==ticker] if 'Ticker' in raw else pd.DataFrame()
            history = prepare_history(group)
            if history.attrs['discarded_rows']:
                issues.append(f'{ticker}: {history.attrs["discarded_rows"]} ongeldige oudere OHLCV-rijen verwijderd')
            signal = analyse_ticker(ticker,history)
            if signal is None:
                raise DataUnavailable('geen berekenbare technische score')
            technical[ticker] = signal
        except DataUnavailable as exc:
            issues.append(f'{ticker}: {exc}')
        except Exception as exc:
            errors.append(f'{ticker}: technische analyse mislukt ({type(exc).__name__})')
    # Scores below min_score - maximum bonus cannot qualify; don't spend requests.
    possible = [sig for sig in technical.values() if sig.tech_score>=MS_CFG['min_score']-1]
    possible.sort(key=lambda sig:(sig.tech_score>=MS_CFG['min_score'], -sig.tech_score,sig.ticker))
    enriched = {}
    for sig in possible:
        try:
            updated = add_sentiment(sig,asof=asof)
            enriched[sig.ticker] = updated
            if updated.sentiment_score is None:
                issues.append(f'{sig.ticker}: {updated.news_status}')
        except Exception as exc:
            errors.append(f'{sig.ticker}: nieuwsanalyse mislukt ({type(exc).__name__})')
    reports = []
    selected = stored = 0
    for exchange,universe in exchanges.items():
        candidates = sorted((enriched[t] for t in universe if t in enriched and enriched[t].score>=MS_CFG['min_score']),
                            key=lambda sig:(-sig.score,sig.ticker))
        signals = candidates[:15]
        selected += len(signals)
        dates = sorted(technical[t].signal_date for t in universe if t in technical)
        coverage = (f'{exchange}: {sum(t in technical for t in universe)}/{len(universe)} geanalyseerd, '
                    f'{len(candidates)} kandidaten, {len(signals)} topselecties; '
                    f'gebruikte koersdagen {dates[0]}..{dates[-1]}.' if dates else f'{exchange}: geen bruikbare analyses.')
        reports.append(coverage+'\n'+format_bericht(exchange,signals))
        print(coverage,flush=True)
        for rank,sig in enumerate(signals,1):
            try:
                ok = log_selectie(ticker=sig.ticker,datum=sig.signal_date,strategie='bot_01marktsent',beurs=exchange,koers=sig.price,
                    parameters={'score':sig.score,'tech_score':sig.tech_score,'rank':rank,
                        'rsi_monthly':sig.rsi_monthly,'rsi_daily':sig.rsi_monthly,'macd_label':sig.macd_label,
                        'stop':sig.stop,'atr':sig.atr,'sentiment_score':sig.sentiment_score,
                        'sentiment_n':sig.sentiment_n,'news_status':sig.news_status,'signal_date':sig.signal_date,
                        'news_asof_utc':asof.isoformat(),'grafiek':yahoo_link(sig.ticker)})
                if not ok:
                    raise RuntimeError('geen opslagbevestiging')
                stored += 1
            except Exception as exc:
                errors.append(f'{sig.ticker}: database mislukt ({type(exc).__name__}: {exc})')
    if not technical:
        errors.append('Geen bruikbare technische analyses')
    summary = (f'Datadekking {len(technical)}/{len(tickers)} unieke tickers. '
               f'NewsAPI {_newsapi_calls_used}/{NEWSAPI_DAILY_BUDGET} calls in deze run. '
               f'Database {stored}/{selected} selecties opgeslagen. Telegram uit.')
    body = '\n\n'.join([summary]+reports+['Databeperkingen:']+issues+['Fouten:']+errors)
    print(body,flush=True)
    subject = ('[ONVOLLEDIG] ' if issues or errors else '')+'Marktsentiment rapport '+today_str()
    if not send_email(subject,body):
        errors.append('E-mail niet verzonden')
    if errors:
        raise RuntimeError('; '.join(errors))
    print('Klaar — e-mail verzonden.',flush=True)


def position_size(cash,entry,stop):
    """Cash and model stop loss inclusive of entry/exit fees; gaps can exceed it."""
    stop_exit = stop*(1-SLIPPAGE_PCT)
    if not all(math.isfinite(v) and v>0 for v in (cash,entry,stop_exit)) or stop_exit>=entry:
        return 0
    left,right = 0,int(cash/entry)
    while left<right:
        n = (left+right+1)//2
        entry_fees,exit_fees = trade_cost(n*entry),trade_cost(n*stop_exit)
        cost = n*entry+entry_fees
        risk = n*(entry-stop_exit)+entry_fees+exit_fees
        if cost<=cash and risk<=cash*RISICO_PCT_PER_TRADE:
            left = n
        else:
            right = n-1
    return left


def backtest_ticker(ticker,history,start,end):
    """Independent account. Monthly closing signals, next-opening entries.

    Stops checked each bar, including entry day. A time exit after 60 complete
    observed sessions is executed on the following available opening.
    """
    start,end = pd.Timestamp(start),pd.Timestamp(end)
    history = history.loc[history.Date<=end].reset_index(drop=True)
    if int((history.Date<start).sum())<210:
        raise DataUnavailable('minder dan 210 geldige opwarmsessies vóór startdatum')
    cash,position,pending = START_CAPITAL,None,None
    trades,equity = [],[{'ticker':ticker,'date':(start-pd.Timedelta(days=1)).date().isoformat(),'value':START_CAPITAL}]
    last_month = None
    def close_position(reference,date,index,reason):
        nonlocal cash,position
        exit_price = reference*(1-SLIPPAGE_PCT)
        exit_fee = trade_cost(exit_price*position['size'])
        proceeds = exit_price*position['size']-exit_fee
        pnl = proceeds-(position['entry_price']*position['size']+position['entry_fee'])
        cash += proceeds
        trades.append({'ticker':ticker,'entry_date':position['entry_date'],
            'exit_date':date.date().isoformat(),'entry_price':position['entry_price'],'exit_price':exit_price,
            'size':position['size'],'entry_fee':position['entry_fee'],'exit_fee':exit_fee,
            'pnl':pnl,'sessions_held':index-position['entry_index']+1,'reason':reason})
        position = None
    for i,row in history.iterrows():
        date = row.Date
        if date<start:
            continue
        if position is not None and position['time_exit']:
            close_position(float(row.Open),date,i,'Time: 60 afgeronde sessies, volgende opening')
        if position is None and pending is not None:
            if 0<(date-pd.Timestamp(pending.signal_date)).days<=7 and row.Open>pending.stop:
                entry = float(row.Open)*(1+SLIPPAGE_PCT)
                n = position_size(cash,entry,pending.stop)
                if n:
                    fee = trade_cost(entry*n)
                    cash -= entry*n+fee
                    position = {'entry_date':date.date().isoformat(),'entry_index':i,
                                'entry_price':entry,'entry_fee':fee,'size':n,'stop':pending.stop,'time_exit':False}
            pending = None
        if position is not None:
            if row.Open<=position['stop']:
                close_position(float(row.Open),date,i,'Gap onder stop')
            elif row.Low<=position['stop']:
                close_position(position['stop'],date,i,'Stop intraday')
            elif i-position['entry_index']+1>=60:
                position['time_exit'] = True
        month = (date.year,date.month)
        if month!=last_month:
            last_month = month
            if position is None:
                signal = analyse_ticker(ticker,history.iloc[:i+1],met_sentiment=False)
                if signal is not None and signal.tech_score>=MS_CFG['min_score']:
                    pending = signal
        value = cash+(position['size']*float(row.Close) if position is not None else 0)
        equity.append({'ticker':ticker,'date':date.date().isoformat(),'value':value})
    if len(equity)==1:
        raise DataUnavailable('geen koerssessies binnen testperiode')
    final = equity[-1]['value']
    values = pd.Series([x['value'] for x in equity])
    peak = values.cummax()
    losses = sum(-t['pnl'] for t in trades if t['pnl']<0)
    gains = sum(t['pnl'] for t in trades if t['pnl']>0)
    summary = {'ticker':ticker,'start_value':START_CAPITAL,'final_value':final,
               'return_pct':(final/START_CAPITAL-1)*100,'trades':len(trades),
               'win_rate_pct':sum(t['pnl']>0 for t in trades)/len(trades)*100 if trades else float('nan'),
               'profit_factor':gains/losses if losses else float('nan'),
               'max_drawdown_pct':float(((peak-values)/peak).max()*100),
               'last_quote_date':equity[-1]['date'],'open_positions':int(position is not None)}
    if position is not None:
        summary.update(open_entry_date=position['entry_date'],open_entry_price=position['entry_price'],
                       open_size=position['size'],open_stop=position['stop'],
                       open_entry_fee=position['entry_fee'])
    return summary,trades,equity


def run_backtest():
    start,end = pd.Timestamp(BACKTEST_START),pd.Timestamp(BACKTEST_END)
    if end<start:
        raise ValueError('Einddatum vóór startdatum')
    tickers = sorted({t for filename in bouw_bestandslijst() for t in load_tickers_from_file(filename)})
    raw = download_history(tickers,start=(start-pd.DateOffset(years=2)).date().isoformat(),
                           end=(end+pd.Timedelta(days=1)).date().isoformat())
    summaries,trades,equity,issues,errors = [],[],[],[],[]
    for ticker in tickers:
        try:
            group = raw.loc[raw.Ticker==ticker] if 'Ticker' in raw else pd.DataFrame()
            clean = prepare_history(group,live=False)
            if clean.attrs['discarded_rows']:
                issues.append(f'{ticker}: {clean.attrs["discarded_rows"]} ongeldige koersrijen verwijderd')
            summary,ts,curve = backtest_ticker(ticker,clean,start,end)
            summaries.append(summary);trades.extend(ts);equity.extend(curve)
        except DataUnavailable as exc:
            issues.append(f'{ticker}: {exc}')
        except Exception as exc:
            errors.append(f'{ticker}: {type(exc).__name__}: {exc}')
    os.makedirs('backtest_results',exist_ok=True)
    pd.DataFrame(summaries,columns=['ticker','start_value','final_value','return_pct','trades','win_rate_pct',
        'profit_factor','max_drawdown_pct','last_quote_date','open_positions','open_entry_date',
        'open_entry_price','open_size','open_stop','open_entry_fee']).to_csv('backtest_results/marktsent_summary_per_ticker.csv',index=False)
    pd.DataFrame(trades,columns=['ticker','entry_date','exit_date','entry_price','exit_price','size',
        'entry_fee','exit_fee','pnl','sessions_held','reason']).to_csv('backtest_results/marktsent_trades.csv',index=False)
    pd.DataFrame(equity,columns=['ticker','date','value']).to_csv('backtest_results/marktsent_equity.csv',index=False)
    print('TECHNISCHE BACKTEST — onafhankelijke accounts in oorspronkelijke koerseenheden; geen EUR-portefeuille, geen sentiment, geen belastingmodel.')
    print(f'{len(summaries)}/{len(tickers)} tickers gesimuleerd; {len(trades)} gesloten trades.')
    print('\n'.join(issues+errors))
    if errors or not summaries:
        raise RuntimeError('; '.join(errors) or 'Geen simuleerbare tickers')
    return summaries


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',nargs='?',choices=('live','backtest'),default='live')
    args = parser.parse_args()
    if args.mode=='backtest':
        run_backtest()
    else:
        run_live_engine()
