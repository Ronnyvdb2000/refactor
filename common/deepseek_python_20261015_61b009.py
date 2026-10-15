"""Positiegrootte, transactiekosten en sizing-tekst.

Centrale constanten en functies die in alle bots terugkwamen. Eén versie,
zodat een wijziging aan het kostenmodel overal tegelijk doorkomt.
"""
import math

TRADE_COST_FIXED = 15.0
TRADE_COST_PCT = 0.0035
SLIPPAGE_PCT = 0.001
RISICO_PCT_DEFAULT = 0.05


def trade_cost(amount: float) -> float:
    """Totale transactiekost voor een bepaald bedrag."""
    return TRADE_COST_FIXED + amount * TRADE_COST_PCT


def bereken_positie(portfolio_waarde: float, entry_prijs: float,
                    stop_prijs: float,
                    risico_pct: float = RISICO_PCT_DEFAULT):
    """Aantal aandelen op basis van risico-per-trade en stopafstand.

    Retourneert (aandelen, max_verlies). Geeft (0, 0.0) terug als de
    stopafstand ongeldig is.
    """
    risico_eur = portfolio_waarde * risico_pct
    stop_afstand = entry_prijs - stop_prijs
    if stop_afstand <= 0 or entry_prijs <= 0:
        return 0, 0.0
    aandelen = max(1, int(risico_eur / stop_afstand))
    max_verlies = round(stop_afstand * aandelen, 2)
    return aandelen, max_verlies


def bereken_atr_positie(portfolio_waarde: float, entry_prijs: float,
                        atr: float, sl_mult: float = 2.0,
                        risico_pct: float = RISICO_PCT_DEFAULT):
    """Zoals bereken_positie, maar stop = entry - sl_mult * atr.

    Retourneert (aandelen, stop_loss, max_verlies).
    """
    if math.isnan(atr) or atr <= 0 or entry_prijs <= 0:
        return 0, entry_prijs, 0.0
    stop_afstand = sl_mult * atr
    stop_loss = entry_prijs - stop_afstand
    risico_eur = portfolio_waarde * risico_pct
    aandelen = max(1, int(risico_eur / stop_afstand))
    max_verlies = round(stop_afstand * aandelen, 2)
    return aandelen, stop_loss, max_verlies


def sizing_tekst(entry: float, stop: float, tp: float,
                 portfolio_waarde: float,
                 risico_pct: float = RISICO_PCT_DEFAULT) -> str:
    """Kant-en-klare tekst voor Telegram/email met sizing-details."""
    aandelen, max_verlies = bereken_positie(portfolio_waarde, entry, stop,
                                             risico_pct)
    investering = round(entry * aandelen, 2)
    kosten = round(trade_cost(investering), 2)
    rr = ((tp - entry) / (entry - stop)) if (entry - stop) > 0 else 0
    return (f"  Entry: {entry:.2f} | Stop: {stop:.2f} | TP: {tp:.2f}\n"
            f"  R/R: {rr:.1f}:1 | {aandelen} stuks | {investering:,.2f}\n"
            f"  Max verlies: {max_verlies:,.2f} | Kosten: {kosten:.2f}")