"""Expliciete beurscodes met bron en ISIN in symbols.json; geen fuzzy hernoemingen."""
import json
from pathlib import Path


with Path(__file__).with_suffix('.json').open(encoding='utf-8') as bestand:
    GEVERIFIEERD = {(rij['lijstcode'], rij['bron']): rij['doel']
                   for rij in json.load(bestand)}

LIJSTCODES = {f'tickers_{code}{variant}.txt': code
             for code in ('041', '051', '054', '059') for variant in ('a', 'x')}


def beurscode(naam, ticker):
    return GEVERIFIEERD.get((LIJSTCODES.get(naam), ticker), ticker)
