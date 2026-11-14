"""Weekgrenzen, gedeelde batchkoersen en rapportage voor weekly bots."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import math

import pandas as pd

from common.io import BEURS_NAMEN, download_history, load_tickers_from_file
from common.notify import send_email, send_telegram

ROOT = Path(__file__).resolve().parents[1]
SECTOREN = {'01': 'Hoogland', '02': 'Macrotrends', '03': 'Beursbrink',
            '04': 'Benelux', '05': 'Parijs', '06': 'Power & AI',
            '07': 'Metalen', '08': 'Defensie', '09': 'Varia'}


def weekgrenzen(nu=None):
    """Laatste volledige week; vrijdag pas vanaf 23:00 UTC beschikbaar."""
    nu = nu or datetime.now(timezone.utc)
    if nu.tzinfo is None:
        raise ValueError('Gebruik een tijdstip met tijdzone')
    nu = nu.astimezone(timezone.utc)
    vrijdag = nu.date() - timedelta(days=(nu.weekday() - 4) % 7)
    if nu.weekday() == 4 and nu.hour < 23:
        vrijdag -= timedelta(days=7)
    return vrijdag - timedelta(days=4), vrijdag


def bestandslijst(suffix='a'):
    return [f'tickers_{n:02}.txt' for n in range(1, 10)] + [
        f'tickers_{n:03}{suffix}.txt' for n in range(41, 60)]


def label_voor(naam):
    sector = naam.removeprefix('tickers_').removesuffix('.txt')
    return f'{sector} {SECTOREN[sector]}' if sector in SECTOREN else BEURS_NAMEN.get(
        naam.replace('a.txt', 'x.txt'), naam.removesuffix('.txt'))


class WeekKoersen:
    """Download elke unieke ticker eenmaal per proces, in batches van 100."""
    def __init__(self, maandag, vrijdag, historie=False):
        self.maandag, self.vrijdag = maandag, vrijdag
        self.historie = historie
        self.frames = {}

    def laad(self, tickers):
        ontbrekend = sorted(set(tickers) - self.frames.keys())
        start = self.maandag - timedelta(days=400 if self.historie else 14)
        for i in range(0, len(ontbrekend), 100):
            batch = ontbrekend[i:i+100]
            print(f'[weekly] Download {i+1}..{i+len(batch)}/{len(ontbrekend)}', flush=True)
            data = download_history(batch, start=str(start), end=str(self.vrijdag+timedelta(days=1)),
                                    auto_adjust=True, request_timeout=15)
            for ticker in batch:
                frame = data.loc[data.Ticker == ticker].copy() if not data.empty else pd.DataFrame()
                if not frame.empty:
                    frame = frame.set_index('Date').drop(columns='Ticker')
                    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
                    frame = frame.loc[frame.index <= pd.Timestamp(self.vrijdag)]
                self.frames[ticker] = frame

    def performance(self, tickers):
        self.laad(tickers)
        resultaten = []
        for ticker in sorted(set(tickers)):
            df = self.frames[ticker]
            if df.empty:
                continue
            basis = df.loc[df.index < pd.Timestamp(self.maandag)]
            week = df.loc[df.index >= pd.Timestamp(self.maandag)]
            if basis.empty or week.empty:
                continue
            # Geen rendement van een onvolledige of stilgevallen notering.
            if (pd.Timestamp(self.maandag)-basis.index[-1]).days > 7 or (
                    pd.Timestamp(self.vrijdag)-week.index[-1]).days > 3:
                continue
            begin, eind = float(basis.Close.iloc[-1]), float(week.Close.iloc[-1])
            if not all(math.isfinite(p) and p > 0 for p in (begin, eind)):
                continue
            resultaten.append({'ticker': ticker, 'perf': (eind / begin - 1)*100,
                               'basisdatum': str(basis.index[-1].date()),
                               'einddatum': str(week.index[-1].date())})
        return resultaten


def selecteer(data):
    df = pd.DataFrame(data).sort_values(['perf', 'ticker'], ascending=[False, True])
    top = df.loc[df.perf > 0].head(5)
    bottom = df.loc[df.perf < 0].sort_values(['perf', 'ticker']).head(5)
    rest = df.drop(top.index.union(bottom.index))
    return top, bottom, rest


def meld(tekst):
    """Telegram behouden; fout wordt zichtbaar in het Actions-log."""
    print(tekst)
    # Zonder parse_mode kunnen namen/tickers de berichtopmaak niet breken.
    for i in range(0, len(tekst), 3900):
        if not send_telegram(tekst[i:i+3900], parse_mode=None):
            print('[WARN] Telegram niet verzonden; controleer configuratie/verzendlog.')


def laad_lijsten(suffix='a'):
    return {naam: load_tickers_from_file(str(ROOT / naam)) for naam in bestandslijst(suffix)
            if (ROOT / naam).exists()}


def maak_rapport(suffix='a'):
    maandag, vrijdag = weekgrenzen()
    lijsten = laad_lijsten(suffix)
    koersen = WeekKoersen(maandag, vrijdag)
    koersen.laad([t for ts in lijsten.values() for t in ts])
    regels = [f'Weekrapport {suffix}: {maandag} t/m {vrijdag}',
              'Rendement: laatste slot vóór maandag tot laatste slot van de week (aangepaste koersen).']
    toppers = []
    bruikbaar = 0
    for naam, tickers in lijsten.items():
        data = koersen.performance(tickers)
        print(f'{naam}: {len(data)}/{len(tickers)} tickers met bruikbare weekdata')
        if not data:
            continue
        bruikbaar += len(data)
        top, bottom, _ = selecteer(data)
        regels.append('\n'+label_voor(naam))
        for titel, selectie in [('Stijgers', top), ('Dalers', bottom)]:
            regels.append(titel+':')
            for r in selectie.itertuples():
                regels.append(f'{r.ticker}: {r.perf:+.2f}%')
                if titel == 'Stijgers':
                    toppers.append(dict(beurs=label_voor(naam), ticker=r.ticker, week_perf=round(r.perf, 2),
                                        week_startdatum=str(maandag), week_einddatum=str(vrijdag),
                                        basisdatum=r.basisdatum, einddatum=r.einddatum, data_version=2))
    # Altijd vervangen: een lege scan mag geen oude winnaars laten staan.
    pad = ROOT / ('laatste_toppers_x.json' if suffix == 'x' else 'laatste_toppers.json')
    tijdelijk = pad.with_suffix('.tmp')
    tijdelijk.write_text(json.dumps(toppers, ensure_ascii=False, indent=2), encoding='utf-8')
    tijdelijk.replace(pad)
    tekst = '\n'.join(regels)
    (ROOT / f'weekly_rapport_{suffix}.txt').write_text(tekst, encoding='utf-8')
    meld(tekst)
    send_email(f'Weekrapport {suffix} {vrijdag}', tekst)
    if not bruikbaar:
        raise RuntimeError('Geen koersdata; weekrapport mislukt')
