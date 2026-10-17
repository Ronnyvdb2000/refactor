"""Gedeelde bouwstenen voor alle Fantasie-bots.

Importeer nooit rechtstreeks uit de submodules in bots; gebruik:
    from common import safe_float, download_history, send_telegram, ...
"""
from .text import today_str, safe_float, esc, yahoo_link, fmt_koers
from .indicators import (
    wilder_smooth,
    compute_rsi_series, compute_atr_series, compute_adx_series,
    compute_macd_series, compute_bb, compute_stoch, compute_ibs,
    compute_hv, compute_ema, bereken_alle_indicatoren,
)
from .io import (
    bouw_bestandslijst, label_voor, load_tickers_from_file, alle_tickers,
    download_history, BEURS_NAMEN,
)
from .notify import send_telegram, send_telegram_message, send_email
from .sizing import (
    TRADE_COST_FIXED, TRADE_COST_PCT, SLIPPAGE_PCT, RISICO_PCT_DEFAULT,
    trade_cost, bereken_positie, bereken_atr_positie, sizing_tekst,
)

__all__ = [
    "today_str", "safe_float", "esc", "yahoo_link", "fmt_koers",
    "wilder_smooth", "compute_rsi_series", "compute_atr_series",
    "compute_adx_series", "compute_macd_series", "compute_bb",
    "compute_stoch", "compute_ibs", "compute_hv", "compute_ema",
    "bereken_alle_indicatoren",
    "bouw_bestandslijst", "label_voor", "load_tickers_from_file",
    "alle_tickers", "download_history", "BEURS_NAMEN",
    "send_telegram", "send_telegram_message", "send_email",
    "TRADE_COST_FIXED", "TRADE_COST_PCT", "SLIPPAGE_PCT",
    "RISICO_PCT_DEFAULT", "trade_cost", "bereken_positie",
    "bereken_atr_positie", "sizing_tekst",
]
