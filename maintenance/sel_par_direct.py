"""Enrich sel_par directly; no reads/writes of the three legacy feature stores."""
from datetime import date, datetime, timedelta, timezone
import math
import time

import pandas as pd

HORIZONS = (5, 10, 20, 30, 60)
TECH = ('atr14 atr14_pct rsi14 ibs ma50 ma200 pct_from_ma50 pct_from_ma200 '
        'vol_ratio_20d high52w pct_from_high52w macd macd_signaal macd_hist '
        'bb_breedte bb_percent_b stoch_k stoch_d adx14 rel_sterkte_20d hv20 hv60 '
        'dagen_sinds_low52w vol_ratio_50d ema8 ema20 pct_from_ema8 pct_from_ema20 '
        'ema8_minus_ema20').split()
REQUIRED = TECH[:11]


def finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def fundamental_values(data, selection_day, observed):
    values = {'fund_'+key: value if isinstance(value,bool) else finite(value)
              for key,value in data.items()}
    values.update(fund_observatiedatum=observed.date(),fund_bijgewerkt_op=observed,
                  fundamentals_aanwezig=True,
                  fundamentals_tijdig_bekend=observed.date()<=date.fromisoformat(selection_day))
    return values


def closed_history(history, cutoff):
    out = history.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    if out.index.has_duplicates or out.index.hasnans:
        raise ValueError('Dubbele of ongeldige koersdatums')
    out = out.sort_index()
    return out.loc[out.index <= pd.Timestamp(cutoff)]


def labels(history, row, cutoff):
    """Count observed sessions, preserve split basis, record actual target dates."""
    hist = closed_history(history, cutoff)
    target = pd.Timestamp(row['datum'])
    pos = hist.index.searchsorted(target, side='right') - 1
    if pos < 0 or (target-hist.index[pos]).days > 7:
        return {}, {}
    entry = finite(row.get('koers_selectiedatum'))
    if entry is None or entry <= 0:
        return {}, {}
    # Yahoo Close history is split adjusted. Use the existing repository's split
    # convention and stop on suspect unit changes rather than guess a correction.
    splits = hist.get('Stock Splits', pd.Series(0.0, index=hist.index))
    subsequent = pd.to_numeric(splits.iloc[pos+1:], errors='coerce')
    factor = subsequent.loc[subsequent > 0].prod()
    if not math.isfinite(factor) or factor <= 0:
        return {}, {}
    basis = entry / factor
    existing_basis = finite(row.get('rendement_basis_koers'))
    if existing_basis is None and factor != 1 and any(row.get(f'koers_{h}d') is not None for h in HORIZONS):
        raise ValueError('Gekopieerde labels met onbekende splitbasis vereisen controle')
    # A later split changes the scale of downloaded history. Never mix that new
    # scale with earlier stored targets; flag for explicit review instead.
    if existing_basis and not math.isclose(existing_basis, basis, rel_tol=1e-6):
        raise ValueError('Splitbasis gewijzigd; bestaande labels vereisen controle')
    values, dates = {}, {}
    for h in HORIZONS:
        if row.get(f'koers_{h}d') is not None and row.get(f'rendement_{h}d_pct') is not None:
            continue
        end = pos+h
        if end >= len(hist):
            continue
        window = pd.to_numeric(hist.Close.iloc[pos:end+1], errors='coerce')
        if (not window.map(lambda x: math.isfinite(x) and x > 0).all()
                or window.index.to_series().diff().dt.days.gt(7).any()):
            continue
        close = float(window.iloc[-1])
        ratio = close/basis
        if not .02 < ratio < 50:
            continue
        # Existing copied values are retained. Do not fill a missing return from
        # a different freshly downloaded close without checking consistency.
        stored = finite(row.get(f'koers_{h}d'))
        if stored is not None and not math.isclose(stored, close, rel_tol=1e-6):
            continue
        values.update({f'koers_{h}d': close, f'rendement_{h}d_pct': (ratio-1)*100,
                       f'status_{h}d': 'rechtstreeks_berekend'})
        dates[str(h)] = hist.index[end].date().isoformat()
    if values:
        values['rendement_basis_koers'] = basis
    return values, dates


def technical_values(indicators, selection_day):
    target = pd.Timestamp(selection_day)
    pos = indicators.index.searchsorted(target, side='right')-1
    if pos < 251 or (target-indicators.index[pos]).days > 7:
        return {}
    row = indicators.iloc[pos]
    values = {f'tech_{name}': finite(row.get(name.upper())) for name in TECH}
    if any(values['tech_'+name] is None for name in REQUIRED):
        return {}
    values['tech_dagen_sinds_low52w'] = (int(values['tech_dagen_sinds_low52w'])
                                       if values['tech_dagen_sinds_low52w'] is not None else None)
    values['tech_koersdatum'] = indicators.index[pos].date()
    values['tech_bijgewerkt_op'] = datetime.now(timezone.utc).replace(tzinfo=None)
    values['technicals_aanwezig'] = True
    return values


def update_row(conn, row, values, dates=None, status=None):
    from psycopg2 import sql
    from psycopg2.extras import Json
    if not values and not dates and not status:
        return
    assignments, args = [], []
    # Technical rows form one coherent snapshot; partial old rows must not be
    # silently mixed with adjusted values from a different observation.
    for key, value in values.items():
        if key.startswith('koers_') or key.startswith('rendement_'):
            assignments.append(sql.SQL('{0}=COALESCE({0},%s)').format(sql.Identifier(key)))
        else:
            assignments.append(sql.SQL('{}=%s').format(sql.Identifier(key)))
        args.append(value)
    if dates:
        assignments.append(sql.SQL('koersdatums=koersdatums || %s'))
        args.append(Json(dates))
    if status:
        assignments.append(sql.SQL('verrijking_status=verrijking_status || %s'))
        args.append(Json(status))
    args.extend([row['ticker'], row['datum']])
    with conn.cursor() as cur:
        cur.execute(sql.SQL('UPDATE public.sel_par SET {} WHERE ticker=%s AND datum=%s')
                    .format(sql.SQL(',').join(assignments)), args)


def enrich(conn, cutoff, max_tickers=2000, budget_minutes=150):
    import yfinance as yf
    from psycopg2.extras import RealDictCursor
    from common.yahoo import stop_on_throttle, YahooRateLimited
    from scripts.bouw_generieke_technicals import bereken_indicatoren
    from scripts.bouw_generieke_fundamentals import haal_fundamentals_op
    clauses = ["NOT coalesce(technicals_aanwezig,false)",
               "NOT coalesce(fundamentals_aanwezig,false)"]
    clauses.extend('tech_'+name+' IS NULL' for name in REQUIRED)
    for h in HORIZONS:
        clauses.append(f"(selectiedatum <= %s::date - {math.ceil(h*7/5)} "
                       f"AND koers_selectiedatum>0 AND (koers_{h}d IS NULL OR rendement_{h}d_pct IS NULL))")
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT * FROM public.sel_par WHERE selectiedatum<=%s AND ('+
                    ' OR '.join(clauses)+') ORDER BY '
                    "verrijking_status->>'geprobeerd_op' NULLS FIRST,ticker,datum", [cutoff]*6)
        rows = cur.fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault(row['ticker'], []).append(dict(row))
    report = dict(tickers_pending=len(grouped), tickers_processed=0, technical_rows=0,
                  fundamental_rows=0, label_rows=0, errors=[], deferred=0)
    if not grouped:
        report['complete_execution'] = True
        return report
    deadline = time.monotonic()+budget_minutes*60
    index = None
    earliest = min((r['datum'] for r in rows), default=cutoff.isoformat())
    start = (date.fromisoformat(earliest)-timedelta(days=550)).isoformat()
    end = (cutoff+timedelta(days=1)).isoformat()
    # Include the same benchmark used by the existing indicator calculator.
    with stop_on_throttle():
        benchmark = yf.Ticker('^GSPC').history(start=start,end=end,auto_adjust=True,timeout=20)
    if benchmark is not None and not benchmark.empty:
        index = closed_history(benchmark, cutoff).Close
    for ticker, selections in grouped.items():
        if report['tickers_processed'] >= max_tickers or time.monotonic() >= deadline:
            break
        report['tickers_processed'] += 1
        changed = dict(technical_rows=0, fundamental_rows=0, label_rows=0)
        try:
            if '/' in ticker:
                raise ValueError('Ongeldig symbool')
            with stop_on_throttle():
                hist = yf.Ticker(ticker).history(start=start,end=end,auto_adjust=False,timeout=20)
            if hist is None or hist.empty:
                raise ValueError('Geen koershistorie')
            hist = closed_history(hist, cutoff)
            adjusted = hist.copy()
            if 'Adj Close' in hist:
                factor = hist['Adj Close']/hist['Close']
                for column in ('Open','High','Low','Close'):
                    adjusted[column] = hist[column]*factor
            indicators = bereken_indicatoren(adjusted, index)
            fund, fund_status = None, 'niet_nodig'
            if any(not r.get('fundamentals_aanwezig') for r in selections):
                with stop_on_throttle():
                    fund, fund_status = haal_fundamentals_op(ticker)
                if fund_status == 'rate_limit':
                    raise YahooRateLimited('Fundamentals providerstop')
            for row in selections:
                values, status = {}, {'geprobeerd_op':datetime.now(timezone.utc).isoformat()}
                if not row.get('technicals_aanwezig') or any(row.get('tech_'+k) is None for k in REQUIRED):
                    tech = technical_values(indicators, row['datum'])
                    values.update(tech)
                    status['technicals'] = 'aangevuld' if tech else 'onvoldoende_historie_of_features'
                    changed['technical_rows'] += bool(tech)
                if not row.get('fundamentals_aanwezig'):
                    if fund:
                        observed = datetime.now(timezone.utc)
                        values.update(fundamental_values(fund,row['datum'],observed))
                        changed['fundamental_rows'] += 1
                    status['fundamentals'] = fund_status
                newlabels, dates = labels(hist, row, cutoff)
                values.update(newlabels)
                changed['label_rows'] += bool(newlabels)
                update_row(conn,row,values,dates,status)
            conn.commit()
            for key, value in changed.items():
                report[key] += value
        except YahooRateLimited:
            conn.rollback()
            report['errors'].append({'ticker':ticker,'reason':'providerstop'})
            break
        except Exception as exc:
            conn.rollback()
            report['errors'].append({'ticker':ticker,'reason':str(exc)[:180]})
        time.sleep(1)
    report['deferred'] = max(0,len(grouped)-report['tickers_processed'])
    report['complete_execution'] = not report['errors'] and not report['deferred']
    return report
