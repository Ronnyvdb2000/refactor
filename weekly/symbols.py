"""Expliciete beurscodes met bron en ISIN in symbols.csv; geen fuzzy hernoemingen."""
import csv
from pathlib import Path


with Path(__file__).with_suffix('.csv').open(encoding='utf-8', newline='') as bestand:
    GEVERIFIEERD = {(rij['lijstcode'], rij['bron']): rij['doel']
                   for rij in csv.DictReader(bestand)}

LIJSTCODES = {'tickers_051a.txt': '051', 'tickers_051x.txt': '051',
             'tickers_054a.txt': '054', 'tickers_054x.txt': '054'}


def beurscode(naam, ticker):
    return GEVERIFIEERD.get((LIJSTCODES.get(naam), ticker), ticker)
