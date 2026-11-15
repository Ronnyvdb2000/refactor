"""Local download limits. Cross-workflow concurrency is handled by Actions lanes."""
from contextlib import contextmanager
import logging
import os
import re


class YahooRateLimited(RuntimeError):
    """Do not turn provider throttling into individual fallback requests."""


def download_workers():
    value = os.getenv('YAHOO_DOWNLOAD_WORKERS', '4')
    try:
        workers = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('YAHOO_DOWNLOAD_WORKERS moet tussen 1 en 4 liggen') from exc
    if not 1 <= workers <= 4:
        raise ValueError('YAHOO_DOWNLOAD_WORKERS moet tussen 1 en 4 liggen')
    return workers


def is_rate_limit(message):
    text = str(message).casefold()
    return any(term in text for term in (
        'too many requests', 'rate limit', 'yfratelimit', 'http error 429',
        '429 client error', 'status code 429',
    )) or bool(re.fullmatch(r'\s*429\s*', text))


class _ThrottleLog(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.throttled = False

    def emit(self, record):
        self.throttled |= is_rate_limit(record.getMessage())


@contextmanager
def stop_on_throttle():
    # yfinance may log a 429 and return an empty/partial frame instead of raising.
    logger = logging.getLogger('yfinance')
    handler = _ThrottleLog()
    old_level = logger.level
    if logger.getEffectiveLevel() > logging.ERROR:
        logger.setLevel(logging.ERROR)
    logger.addHandler(handler)
    try:
        try:
            yield
        except Exception as exc:
            if handler.throttled or is_rate_limit(exc):
                raise YahooRateLimited('Yahoo rate limit (429); individuele fallback gestopt') from exc
            raise
        if handler.throttled:
            raise YahooRateLimited('Yahoo rate limit (429); individuele fallback gestopt')
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
