"""Frozen European universe rules, common sessions and historical EUR conversion."""
from datetime import date
import math

import pandas as pd

EU_CODES={'041','042','043','044','045','046','049','050','051','052','053','054','059'}
CALENDARS={'.AS':'XAMS','.BR':'XBRU','.IR':'XDUB','.I':'XDUB','.PA':'XPAR',
           '.DE':'XETR','.MC':'XMAD','.LS':'XLIS','.L':'XLON','.MI':'XMIL',
           '.ST':'XSTO','.SW':'XSWX','.WA':'XWAR','.OL':'XOSL','.CO':'XCSE',
           '.HE':'XHEL','.VI':'XWBO','.LJ':'XLJU'}
BENCHMARK='EXSA.DE'
FX={c:c+'EUR=X' for c in ('GBP','CHF','SEK','NOK','DKK','PLN','USD','CAD')}


def market(ticker):
    return next((calendar for suffix,calendar in CALENDARS.items() if ticker.endswith(suffix)),None)


def eligible(ticker,venue):
    return str(venue)[:3] in EU_CODES and market(ticker) is not None


class CommonCalendar:
    """Use only dates when every covered market is open, including the benchmark."""
    def __init__(self,start,end):
        import exchange_calendars as xcals
        shared=None
        for name in sorted(set(CALENDARS.values())):
            sessions=xcals.get_calendar(name,start=start,end=end).sessions
            shared=sessions if shared is None else shared.intersection(sessions)
        self.sessions=shared

    def sessions_in_range(self,start,end):
        return self.sessions[(self.sessions>=pd.Timestamp(start)) & (self.sessions<=pd.Timestamp(end))]


def valid_price(value):
    return isinstance(value,(float,int)) and math.isfinite(value) and value>0


def convert(ticker,value,fx,fx_fee_bps=5):
    """No FX forward-fill; GBp/GBX stock quotes must first become GBP units."""
    currency=value.get('currency')
    unit=.01 if currency in ('GBp','GBX') else 1.
    currency='GBP' if currency in ('GBp','GBX') else currency
    if currency!='EUR' and currency not in FX:
        return dict(currency='EUR',bars=[],error='unverified_or_unsupported_quote_currency')
    rates={}
    if currency!='EUR':
        rate=fx.get(FX[currency],{})
        if rate.get('currency')!='EUR':
            return dict(currency='EUR',bars=[],error='unverified_FX_quote_currency')
        if len({r['day'] for r in rate.get('bars',[])})!=len(rate.get('bars',[])):
            raise ValueError('Dubbele FX-koersdatum')
        rates={r['day']:r for r in rate.get('bars',[])}
    rows=[];missing=[]
    if len({r['day'] for r in value.get('bars',[])})!=len(value.get('bars',[])):
        raise ValueError('Dubbele aandelenkoersdatum')
    for row in value.get('bars',[]):
        rate={'open':1.,'close':1.} if currency=='EUR' else rates.get(row['day'],{})
        if not all(valid_price(source.get(k)) for source in (row,rate) for k in ('open','close')):
            missing.append(row['day']);continue
        prices={k:row[k]*unit*rate[k] for k in ('open','close')}
        if not all(valid_price(v) for v in prices.values()):
            missing.append(row['day']);continue
        rows.append(dict(day=row['day'],**prices))
    return dict(currency='EUR',original_currency=value.get('currency'),unit_factor=unit,
                fx_ticker=FX.get(currency),fx_fee_bps=fx_fee_bps if currency!='EUR' else 0,
                bars=rows,missing_conversion_days=missing)
