"""Gedeelde functies met uitgestelde imports.

Bestaande imports zoals ``from common import download_history`` blijven werken.
Alleen de module van de gevraagde functie wordt geladen. Domeinhelpers zijn ook
rechtstreeks beschikbaar via common.fundamentals, common.horizons en common.reports.
"""
from importlib import import_module

_EXPORTS = {
    'download_workers': ('yahoo', 'download_workers'),
    'YahooRateLimited': ('yahoo', 'YahooRateLimited'),
    'today_str': ('text', 'today_str'),
    'safe_float': ('text', 'safe_float'),
    'esc': ('text', 'esc'),
    'yahoo_link': ('text', 'yahoo_link'),
    'fmt_koers': ('text', 'fmt_koers'),
    'wilder_smooth': ('indicators', 'wilder_smooth'),
    'compute_rsi_series': ('indicators', 'compute_rsi_series'),
    'compute_atr_series': ('indicators', 'compute_atr_series'),
    'compute_adx_series': ('indicators', 'compute_adx_series'),
    'compute_macd_series': ('indicators', 'compute_macd_series'),
    'compute_bb': ('indicators', 'compute_bb'),
    'compute_stoch': ('indicators', 'compute_stoch'),
    'compute_ibs': ('indicators', 'compute_ibs'),
    'compute_hv': ('indicators', 'compute_hv'),
    'compute_ema': ('indicators', 'compute_ema'),
    'bereken_alle_indicatoren': ('indicators', 'bereken_alle_indicatoren'),
    'bouw_bestandslijst': ('io', 'bouw_bestandslijst'),
    'label_voor': ('io', 'label_voor'),
    'load_tickers_from_file': ('io', 'load_tickers_from_file'),
    'alle_tickers': ('io', 'alle_tickers'),
    'download_history': ('io', 'download_history'),
    'BEURS_NAMEN': ('io', 'BEURS_NAMEN'),
    'send_telegram': ('notify', 'send_telegram'),
    'send_telegram_message': ('notify', 'send_telegram_message'),
    'send_email': ('notify', 'send_email'),
    'TRADE_COST_FIXED': ('sizing', 'TRADE_COST_FIXED'),
    'TRADE_COST_PCT': ('sizing', 'TRADE_COST_PCT'),
    'SLIPPAGE_PCT': ('sizing', 'SLIPPAGE_PCT'),
    'RISICO_PCT_DEFAULT': ('sizing', 'RISICO_PCT_DEFAULT'),
    'trade_cost': ('sizing', 'trade_cost'),
    'bereken_positie': ('sizing', 'bereken_positie'),
    'bereken_atr_positie': ('sizing', 'bereken_atr_positie'),
    'sizing_tekst': ('sizing', 'sizing_tekst'),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    try:
        module, attribute = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(f".{module}", __name__), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
