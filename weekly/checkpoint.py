"""Optionele hervatcache met gevalideerde CSV's; bevat uitsluitend koersdata."""
import hashlib
import os
from pathlib import Path

import pandas as pd

from common.io import _normalise


class KoersCheckpoint:
    def __init__(self, start, vrijdag):
        root = os.environ.get('WEEKLY_CHECKPOINT_DIR')
        self.start, self.vrijdag = pd.Timestamp(start), pd.Timestamp(vrijdag)
        self.pad = Path(root) / f'v1_{start}_{vrijdag}' if root else None

    def bestand(self, ticker):
        # Tickers komen nooit rechtstreeks in bestandsnamen terecht.
        return self.pad / (hashlib.sha256(ticker.encode('utf-8')).hexdigest() + '.csv')

    def laad(self, tickers):
        frames = {}
        if self.pad is None:
            return frames
        for ticker in sorted(tickers):
            try:
                raw = pd.read_csv(self.bestand(ticker))
                if 'Ticker' not in raw or not raw.Ticker.eq(ticker).all():
                    continue
                frame = _normalise(raw, ticker)
                if frame is None or not frame.Date.between(self.start, self.vrijdag).all():
                    continue
                frames[ticker] = frame.set_index('Date').drop(columns='Ticker')
            except FileNotFoundError:
                pass
            except (OSError, ValueError, KeyError, TypeError):
                print(f'[weekly] Onleesbare hervatcache voor {ticker}; opnieuw downloaden.')
        return frames

    def bewaar(self, frames):
        if self.pad is None:
            return
        self.pad.mkdir(parents=True, exist_ok=True)
        for ticker, frame in frames.items():
            # Ontbrekende historie wordt bij een nieuwe run opnieuw geprobeerd.
            if frame.empty:
                continue
            pad = self.bestand(ticker)
            tijdelijk = pad.with_suffix('.tmp')
            frame.rename_axis('Date').reset_index().assign(Ticker=ticker).to_csv(tijdelijk, index=False)
            tijdelijk.replace(pad)
