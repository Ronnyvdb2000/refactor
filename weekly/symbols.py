"""Expliciete beurscodes met bron en ISIN in symbols.csv; geen fuzzy hernoemingen."""
import csv
from pathlib import Path


with Path(__file__).with_suffix('.csv').open(encoding='utf-8', newline='') as bestand:
    GEVERIFIEERD = {(rij['lijstcode'], rij['bron']): rij['doel']
                   for rij in csv.DictReader(bestand)}

LIJSTCODES = {f'tickers_{code}{variant}.txt': code
             for code in ('041', '051', '054', '059') for variant in ('a', 'x')}


def beurscode(naam, ticker):
    return GEVERIFIEERD.get((LIJSTCODES.get(naam), ticker), ticker)
