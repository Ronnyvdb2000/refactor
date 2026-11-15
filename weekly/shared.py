"""Weekgrenzen, gedeelde batchkoersen en rapportage voor weekly bots."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import math
import time

import pandas as pd

from common.io import BEURS_NAMEN, load_tickers_from_file
from common.notify import send_email, send_telegram
from .download import batch_download as download_history, DownloadOnderbroken
from .symbols import beurscode
from .checkpoint import KoersCheckpoint
from .coverage import deelmarkt, marktdekking, voldoende

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
    if naam in ('tickers_055a.txt', 'tickers_055x.txt'):
        return '055 CSE'
    sector = naam.removeprefix('tickers_').removesuffix('.txt')
    return f'{sector} {SECTOREN[sector]}' if sector in SECTOREN else BEURS_NAMEN.get(
        naam.replace('a.txt', 'x.txt'), naam.removesuffix('.txt'))


class WeekKoersen:
    """Download unieke tickers in kleine batches; hervat uit optionele weekcache."""
    def __init__(self, maandag, vrijdag, historie=False):
        self.maandag, self.vrijdag = maandag, vrijdag
        self.historie = historie
        self.frames = {}
        self.aangevraagd = set()

    def laad(self, tickers):
        tickers = set(tickers)
        start = self.maandag - timedelta(days=400 if self.historie else 14)
        checkpoint = KoersCheckpoint(start, self.vrijdag)
        hersteld = checkpoint.laad(tickers - self.frames.keys())
        self.frames.update(hersteld)
        if hersteld:
            print(f'[weekly] Hervat met {len(hersteld)} bewaarde koersreeksen.', flush=True)
        ontbrekend = sorted(tickers - self.frames.keys())
        for i in range(0, len(ontbrekend), 20):
            batch = ontbrekend[i:i+20]
            print(f'[weekly] Download {i+1}..{i+len(batch)}/{len(ontbrekend)}', flush=True)
            # Ook tussen opeenvolgende laad()-calls pauzeren: een batch is
            # bij Yahoo een reeks tickerrequests, geen enkel HTTP-verzoek.
            time.sleep(2)
            self.aangevraagd.update(batch)
            data = download_history(batch, start=str(start), end=str(self.vrijdag+timedelta(days=1)),
                                    auto_adjust=True, request_timeout=15)
            for ticker in batch:
                frame = data.loc[data.Ticker == ticker].copy() if not data.empty else pd.DataFrame()
                if not frame.empty:
                    frame = frame.set_index('Date').drop(columns='Ticker')
                    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
                    frame = frame.loc[frame.index <= pd.Timestamp(self.vrijdag)]
                self.frames[ticker] = frame
            geldig = {r['ticker'] for r in self.beschikbare_performance(batch)}
            checkpoint.bewaar({t: self.frames[t] for t in geldig})

    def performance(self, tickers):
        self.laad(tickers)
        return self.beschikbare_performance(tickers)

    def beschikbare_performance(self, tickers):
        """Beoordeel uitsluitend de cache, ook na een afgebroken download."""
        resultaten = []
        for ticker in sorted(set(tickers)):
            df = self.frames.get(ticker)
            if df is None or df.empty:
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
    delen = [tekst[i:i+3900] for i in range(0, len(tekst), 3900)]
    for nummer, deel in enumerate(delen, 1):
        if send_telegram(deel, parse_mode=None):
            print(f'[weekly] Telegram deel {nummer}/{len(delen)} geaccepteerd door Telegram.')
        else:
            print(f'::warning::Telegram deel {nummer}/{len(delen)} niet verzonden; controleer configuratie/verzendlog.')


def normaliseer_lijstticker(naam, ticker):
    # Gebruik expliciet de bronbeurs. Een kale AMD uit 043 is geen US-notering.
    if naam in ('tickers_043a.txt', 'tickers_043x.txt') and '.' not in ticker:
        return ticker + '.DE'
    if naam in ('tickers_041a.txt', 'tickers_041x.txt') and ticker.endswith('.I'):
        return ticker[:-2]+'.IR'
    return beurscode(naam, ticker)


def laad_lijsten(suffix='a'):
    return {naam: sorted({normaliseer_lijstticker(naam, t) for t in load_tickers_from_file(str(ROOT / naam))})
            for naam in bestandslijst(suffix) if (ROOT / naam).exists()}


def controleer_dekking(naam, tickers, data):
    verhouding = len(data)/len(tickers) if tickers else 0
    print(f'{naam}: {len(data)}/{len(tickers)} tickers met bruikbare weekdata ({verhouding:.1%})')
    return voldoende(len(tickers), len(data))


def schrijf_dekking(suffix, lijsten, resultaten, *, koersen=None):
    rows = []
    for naam, tickers in lijsten.items():
        goed = {r['ticker'] for r in resultaten.get(naam, [])}
        for ticker in tickers:
            bruikbaar = ticker in goed
            status = 'bruikbaar' if bruikbaar else 'geen_bruikbare_weekdata'
            if koersen is not None and ticker not in koersen.frames:
                bruikbaar = None  # Niet gemeten is geen bewezen onbruikbaar symbool.
                status = 'download_afgebroken' if ticker in koersen.aangevraagd else 'niet_opgevraagd'
            rows.append(dict(lijst=naam, ticker=ticker, bruikbaar=bruikbaar, status=status,
                             deelmarkt=deelmarkt(naam, ticker)))
    pd.DataFrame(rows, columns=['lijst', 'ticker', 'bruikbaar', 'status', 'deelmarkt']).to_csv(
        ROOT / f'weekly_dekking_{suffix}.csv', index=False)
    pd.DataFrame(marktdekking(rows), columns=[
        'lijst', 'deelmarkt', 'aantal', 'bruikbaar', 'onbekend', 'dekking', 'status',
    ]).to_csv(ROOT / f'weekly_marktdekking_{suffix}.csv', index=False)


def publicatiegroepen(lijsten, resultaten):
    """Beoordeel 059 per beurs; bronlijsten en volledige diagnose blijven intact."""
    groepen = []
    for naam, tickers in lijsten.items():
        markten = {}
        for ticker in tickers:
            markten.setdefault(deelmarkt(naam, ticker), []).append(ticker)
        for markt, selectie in markten.items():
            symbols = set(selectie)
            data = [r for r in resultaten.get(naam, []) if r['ticker'] in symbols]
            label = label_voor(naam) if markt == 'Gehele lijst' else f'059 {markt}'
            groepen.append(dict(lijst=naam, label=label, tickers=selectie, data=data,
                                voldoende=voldoende(len(selectie), len(data))))
    return groepen


def publicatiemelding(groepen):
    goed = [g for g in groepen if g['voldoende'] and g['data']]
    titel = ('GEDEELTELIJK WEEKRAPPORT — voldoende gedekte beurzen gepubliceerd.' if goed else
             'ONVOLLEDIG WEEKRAPPORT — geen nieuwe toppers gepubliceerd.')
    regels = [titel, f'{len(goed)}/{len(groepen)} beurs-/themagroepen voldoende.']
    for groep in groepen:
        aantal, bruikbaar = len(groep['tickers']), len(groep['data'])
        status = 'gepubliceerd' if groep['voldoende'] else 'overgeslagen; geen ranglijst'
        # Bij gedeeltelijke publicatie ook de geslaagde deelbeurzen expliciet tonen.
        if not groep['voldoende'] or groep['label'].startswith('059 '):
            regels.append(f"{groep['label']}: {bruikbaar}/{aantal} "
                          f"({bruikbaar/aantal:.1%}); {status}.")
    regels.append('Volledig rapport en ontbrekende tickers: zie workflow-artifacts.')
    return '\n'.join(regels)


def schrijf_afgebroken_dekking(suffix, lijsten, koersen):
    resultaten = {naam: koersen.beschikbare_performance(tickers) for naam, tickers in lijsten.items()}
    schrijf_dekking(suffix, lijsten, resultaten, koersen=koersen)


def maak_rapport(suffix='a'):
    maandag, vrijdag = weekgrenzen()
    lijsten = laad_lijsten(suffix)
    koersen = WeekKoersen(maandag, vrijdag)
    try:
        koersen.laad([t for ts in lijsten.values() for t in ts])
    except DownloadOnderbroken as exc:
        schrijf_afgebroken_dekking(suffix, lijsten, koersen)
        tekst = f'Weekrapport {suffix} ONVOLLEDIG: {exc}. Geen nieuwe toppers gepubliceerd.'
        (ROOT / f'weekly_rapport_{suffix}.txt').write_text(tekst, encoding='utf-8')
        meld(tekst)
        raise
    regels = [f'Weekrapport {suffix}: {maandag} t/m {vrijdag}',
              'Rendement: laatste slot vóór maandag tot laatste slot van de week (aangepaste koersen).']
    resultaten = {naam: koersen.performance(tickers) for naam, tickers in lijsten.items()}
    groepen = publicatiegroepen(lijsten, resultaten)
    toppers = []
    bruikbaar = 0
    onvolledig = [g for g in groepen if not g['voldoende']]
    for groep in groepen:
        tickers, data = groep['tickers'], groep['data']
        label = groep['label']
        controleer_dekking(label, tickers, data)
        if not groep['voldoende']:
            regels.append(f'\n{label}: OVERGESLAGEN ({len(data)}/{len(tickers)}); geen ranglijst.')
            continue
        if not data:
            continue
        bruikbaar += len(data)
        top, bottom, _ = selecteer(data)
        regels.append(f'\n{label} — {len(data)}/{len(tickers)} bruikbaar')
        for titel, selectie in [('Stijgers', top), ('Dalers', bottom)]:
            regels.append(titel+':')
            for r in selectie.itertuples():
                regels.append(f'{r.ticker}: {r.perf:+.2f}%')
                if titel == 'Stijgers':
                    toppers.append(dict(beurs=label, ticker=r.ticker, week_perf=round(r.perf, 2),
                                        week_startdatum=str(maandag), week_einddatum=str(vrijdag),
                                        basisdatum=r.basisdatum, einddatum=r.einddatum, data_version=3,
                                        aantal_bruikbaar=len(data), aantal_universum=len(tickers),
                                        rapport_status='gedeeltelijk' if onvolledig else 'volledig'))
    schrijf_dekking(suffix, lijsten, resultaten)
    diagnose = publicatiemelding(groepen) if onvolledig or not bruikbaar else ''
    tekst = (diagnose + '\n\n' if diagnose else '') + '\n'.join(regels)
    (ROOT / f'weekly_rapport_{suffix}.txt').write_text(tekst, encoding='utf-8')
    if not bruikbaar:
        meld(diagnose)
        send_email(f'Weekrapport {suffix} onvolledig {vrijdag}', tekst)
        raise RuntimeError('Onvoldoende weekdata: geen beurs haalt de 80%-norm')
    # Voldoende data zonder stijgers schrijft []; oude winnaars verdwijnen ook dan.
    pad = ROOT / ('laatste_toppers_x.json' if suffix == 'x' else 'laatste_toppers.json')
    tijdelijk = pad.with_suffix('.tmp')
    tijdelijk.write_text(json.dumps(toppers, ensure_ascii=False, indent=2), encoding='utf-8')
    tijdelijk.replace(pad)
    meld(tekst)
    status = ' gedeeltelijk' if onvolledig else ''
    send_email(f'Weekrapport {suffix}{status} {vrijdag}', tekst)
