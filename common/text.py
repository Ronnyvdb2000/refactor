"""Kleine tekst- en getalhulpmiddelen, gedeeld door alle bots."""
import math
import html
import datetime as dt


def today_str() -> str:
    return dt.date.today().strftime("%Y-%m-%d")


def safe_float(val, default: float = float("nan")) -> float:
    try:
        f = float(val)
        return default if math.isnan(f) else f
    except Exception:
        return default


def esc(s) -> str:
    return html.escape(str(s))


def yahoo_link(ticker: str) -> str:
    return f"https://finance.yahoo.com/quote/{ticker}"


def fmt_koers(koers) -> str:
    try:
        if koers is None or math.isnan(float(koers)):
            return "-"
        return f"{float(koers):.2f}"
    except Exception:
        return "-"
