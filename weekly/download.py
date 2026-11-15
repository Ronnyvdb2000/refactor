"""Begrensde weekly downloads; geen duizenden afzonderlijke fallback-calls."""
from contextlib import contextmanager
import logging
import time

import pandas as pd
import yfinance as yf
from common.io import _normalise


class DownloadOnderbroken(RuntimeError):
    pass


class ProviderLog(logging.Handler):
    def __init__(self):
        super().__init__()
        self.rate_limit = False
        self.cache_lock = False
        self.errors = 0

    def emit(self, record):
        tekst = record.getMessage().lower()
        self.errors += 1
        self.rate_limit |= any(t in tekst for t in ('yfratelimiterror', 'too many requests', 'rate limited', 'http error 429'))
        self.cache_lock |= 'database is locked' in tekst


@contextmanager
def provider_log():
    logger = logging.getLogger('yfinance')
    oud = logger.handlers[:], logger.propagate, logger.level
    capture = ProviderLog()
    logger.handlers, logger.propagate = [capture], False
    logger.setLevel(logging.ERROR)
    try:
        yield capture
    finally:
        logger.handlers, logger.propagate, level = oud
        logger.setLevel(level)


def batch_download(tickers, *, start, end, **_):
    """Maximaal twee batchrequests, twee workers; één herstelpauze bij throttling."""
    tickers = sorted(set(tickers))
    if not tickers:
        return pd.DataFrame()
    frames = {}
    missing = tickers
    for poging in range(2):
        with provider_log() as log:
            try:
                data = yf.download(tickers=missing, start=start, end=end, auto_adjust=True,
                                   group_by='ticker', progress=False, threads=2, timeout=15)
            except Exception as exc:
                tekst = str(exc).lower()
                if any(t in tekst for t in ('rate limit', 'too many requests', '429')):
                    log.rate_limit = True
                elif 'database is locked' in tekst:
                    log.cache_lock = True
                else:
                    raise DownloadOnderbroken(f'Yahoo-request mislukt ({type(exc).__name__})') from exc
                data = pd.DataFrame()
        if data is not None and not data.empty:
            for ticker in missing:
                if isinstance(data.columns, pd.MultiIndex):
                    level = next((n for n in range(data.columns.nlevels)
                                  if ticker in data.columns.get_level_values(n)), None)
                    if level is None:
                        continue
                    raw = data.xs(ticker, axis=1, level=level)
                elif len(missing) == 1:
                    raw = data
                else:
                    continue
                norm = _normalise(raw, ticker)
                if norm is not None:
                    frames[ticker] = norm
        missing = [t for t in tickers if t not in frames]
        print(f'[weekly] Batch poging {poging+1}: {len(frames)}/{len(tickers)} met koersdata; '
              f'providerlog={log.errors}, rate_limit={log.rate_limit}, cache_lock={log.cache_lock}')
        # Een rate-limited batch is geen betrouwbare basis om door te blijven scannen.
        if log.rate_limit:
            if poging == 1:
                raise DownloadOnderbroken('Yahoo blijft verzoeken beperken; scan gestopt, probeer later opnieuw')
            time.sleep(15)
            frames.clear()
            missing = tickers  # volledige batch na een rate-limit opnieuw controleren
            continue
        if not missing:
            break
        if poging == 0:
            time.sleep(1)
    if missing:
        print(f'[weekly] Geen bruikbare batchdata voor {len(missing)} tickers; geen losse retries.')
    return pd.concat(frames.values(), ignore_index=True) if frames else pd.DataFrame()
