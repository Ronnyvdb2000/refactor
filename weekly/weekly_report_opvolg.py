"""Weekly opvolg: weekselecties en causale dagparameters naar Supabase.
Actuele fundamentals worden niet als historische waarnemingen opgeslagen.
"""

import pandas as pd
import math
import random
import hashlib

from . import weekly_db_opvolg as weekly_db
from .shared import (
    WeekKoersen, weekgrenzen, laad_lijsten, label_voor, selecteer, meld,
    controleer_dekking, schrijf_dekking, schrijf_afgebroken_dekking,
)
from .download import DownloadOnderbroken

NEUTRAAL_STEEKPROEF_GROOTTE = 10

def _veilig(x):
    """Zet een waarde om naar een echte Python-float, of None bij NaN/Inf/
    onbruikbare invoer (zelfde reden als _sanitize in db_logger.py/weekly_db.py)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def haal_technische_parameters(hist, maandag, vrijdag):
    """
    Berekent voor de gevraagde week een standaard set
    technische indicatoren (RSI14, MACD-histogram, ATR14%, afstand tot
    SMA50/SMA200, 20-daagse support/resistance, een ATR-gebaseerde stop).

    Dit zijn ONAFHANKELIJKE, standaardformules -- geen kopie van de exacte
    (soms bot-specifieke) berekening in bijvoorbeeld bot_00kr.py of
    bot_01xgboost.py. Voor deze correlatie-analyse maakt dat niet uit
    (consistentie tussen de 5 dagen is wat telt), maar de absolute waardes
    hoeven dus niet 1-op-1 overeen te komen met wat elders in het project
    gelogd wordt.
    """
    df = hist.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df.sort_index().loc[lambda f: f.index <= pd.Timestamp(vrijdag)]
    if df.index.has_duplicates:
        raise ValueError("Dubbele koersdatums in weekly historie")
    if df.empty or len(df) < 60:
        return []
    vereiste = ['Open', 'High', 'Low', 'Close', 'Volume']
    if any(k not in df for k in vereiste):
        return []
    df[vereiste] = df[vereiste].apply(pd.to_numeric, errors='coerce')
    geldig = df[vereiste].notna().all(axis=1)
    geldig &= df[vereiste].apply(lambda col: col.map(math.isfinite)).all(axis=1)
    geldig &= (df[['Open', 'High', 'Low', 'Close']] > 0).all(axis=1)
    geldig &= (df.High >= df[['Open', 'Low', 'Close']].max(axis=1))
    geldig &= (df.Low <= df[['Open', 'High', 'Close']].min(axis=1)) & (df.Volume >= 0)
    # Geen indicatoren over gaten/ongeldige balken heen doorrekenen.
    breuk = (~geldig) | (df.index.to_series().diff().dt.days > 7)
    if breuk.any():
        df = df.loc[df.index > breuk[breuk].index[-1]]
    if len(df) < 60:
        return []
    close = df['Close']
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signaallijn = macd.ewm(span=9, adjust=False).mean()
    df['macd_hist'] = macd - signaallijn

    vorige_close = close.shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - vorige_close).abs(),
        (df['Low'] - vorige_close).abs(),
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    df['atr_pct'] = df['atr'] / close * 100

    df['sma50'] = close.rolling(50).mean()
    df['sma200'] = close.rolling(200).mean()
    df['dist_sma50_pct'] = (close - df['sma50']) / df['sma50'] * 100
    df['dist_sma200_pct'] = (close - df['sma200']) / df['sma200'] * 100

    df['support'] = df['Low'].rolling(20).min()
    df['resistance'] = df['High'].rolling(20).max()
    # ATR-stop: gangbare conventie (2x ATR onder de slotkoers), niet
    # per se identiek aan de stop-formule van een specifieke bot.
    df['stop'] = close - 2 * df['atr']

    # EWM-opwarming expliciet; een kort beschikbare notering is geen nul-MACD.
    df.loc[df.index[:129], 'macd_hist'] = float('nan')
    laatste = df.loc[(df.index >= pd.Timestamp(maandag)) & (df.index <= pd.Timestamp(vrijdag))]
    resultaat = []
    for datum, row in laatste.iterrows():
        resultaat.append({
            "datum": datum.date(),
            "close": _veilig(row['Close']),
            "rsi": _veilig(row['rsi']),
            "macd_hist": _veilig(row['macd_hist']),
            "atr_pct": _veilig(row['atr_pct']),
            "dist_sma50_pct": _veilig(row['dist_sma50_pct']),
            "dist_sma200_pct": _veilig(row['dist_sma200_pct']),
            "support": _veilig(row['support']),
            "resistance": _veilig(row['resistance']),
            "stop": _veilig(row['stop']),
        })
    return resultaat


def verwerk_weekly_db_export(alle_entries, maandag, vrijdag, koersen):
    if not alle_entries:
        raise RuntimeError('Geen weekly-selecties om op te slaan')
    historie = WeekKoersen(maandag, vrijdag, historie=True)
    historie.laad([e['ticker'] for e in alle_entries])
    aantal = weekly_db.log_weekly_toppers([
        {'week_startdatum': maandag, **e} for e in alle_entries])
    print(f'{aantal} toppers opgeslagen of reeds aanwezig.')
    parameter_rijen = []
    for ticker, beurs in sorted({(e['ticker'], e['beurs']) for e in alle_entries}):
        technisch = haal_technische_parameters(historie.frames[ticker], maandag, vrijdag)
        for dag in technisch:
            parameter_rijen.append({
                'week_startdatum': maandag, 'ticker': ticker, 'beurs': beurs,
                'dag_index': dag['datum'].weekday()+1, **dag})
    groepen = {}
    for rij in parameter_rijen:
        groepen.setdefault((rij['beurs'], rij['datum']), []).append(rij)
    for (beurs, datum), rijen in groepen.items():
        ranks = weekly_db.haal_laatste_rank_scores([r['ticker'] for r in rijen], datum, beurs)
        for rij in rijen:
            rij.update(ranks.get(rij['ticker'], {}))
    aantal_params = weekly_db.log_weekly_topper_parameters(parameter_rijen)
    print(f'{aantal_params} dagparameters opgeslagen of reeds aanwezig.')
    if not parameter_rijen:
        raise RuntimeError('Geen bruikbare historische dagparameters')


def main():
    maandag, vrijdag = weekgrenzen()
    lijsten = laad_lijsten('a')
    koersen = WeekKoersen(maandag, vrijdag)
    try:
        koersen.laad([t for ts in lijsten.values() for t in ts])
    except DownloadOnderbroken as exc:
        schrijf_afgebroken_dekking('opvolg', lijsten, koersen)
        meld(f'Weekly opvolg ONVOLLEDIG: {exc}. Geen database-export.')
        raise
    entries = []
    onvolledig = []
    resultaten = {}
    for naam, tickers in lijsten.items():
        data = koersen.performance(tickers)
        resultaten[naam] = data
        if not controleer_dekking(naam, tickers, data):
            onvolledig.append(naam)
            continue
        if not data:
            continue
        top, bottom, rest = selecteer(data)
        for soort, selectie in [('stijger', top), ('daler', bottom)]:
            for rang, r in enumerate(selectie.itertuples(), 1):
                entries.append(dict(lijst=naam, ticker=r.ticker, beurs=label_voor(naam),
                                    type=soort, rang=rang, week_perf=r.perf))
        seed = int(hashlib.sha256(f'{maandag}|{naam}'.encode()).hexdigest(), 16)
        indices = random.Random(seed).sample(list(rest.index), min(NEUTRAAL_STEEKPROEF_GROOTTE, len(rest)))
        for idx in indices:
            r = rest.loc[idx]
            entries.append(dict(lijst=naam, ticker=r['ticker'], beurs=label_voor(naam),
                                type='neutraal', rang=None, week_perf=float(r['perf'])))
    schrijf_dekking('opvolg', lijsten, resultaten)
    if onvolledig:
        meld('Weekly opvolg ONVOLLEDIG: onvoldoende bruikbare data in '+', '.join(onvolledig)+'. Geen database-export.')
        raise RuntimeError('Onvoldoende weekly-dekking')
    try:
        verwerk_weekly_db_export(entries, maandag, vrijdag, koersen)
    except Exception:
        meld('Weekly opvolg mislukt tijdens historie/opslag; controleer het Actions-log.')
        raise
    meld(f'Weekly opvolg {maandag} t/m {vrijdag}: {len(entries)} selecties verwerkt. '
         'Historische technische parameters opgeslagen; actuele fundamentals niet teruggedateerd.')


if __name__ == '__main__':
    main()
