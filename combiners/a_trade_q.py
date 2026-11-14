"""Vers Signaal: minstens twee verschillende strategiefamilies op de laatste datum per ticker."""

import argparse
import os
from datetime import date
from common import today_str, send_telegram, send_email, esc
from db_logger import log_selectie
from .shared import fetch_selections, group_signals, costs, report, check_delivery

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "3"))
BESCHIKBAAR_KAPITAAL = float(os.getenv("BESCHIKBAAR_KAPITAAL", "2500"))
TRANSACTIE_BEDRAG = float(os.getenv("TRANSACTIE_BEDRAG", "2500"))
TOP_N = AANTAL_PICKS = int(os.getenv("AANTAL_PICKS", "5"))
TOB_PCT = float(os.getenv("TOB_PCT", "0"))
DB_LOG_ENABLED = os.getenv("DB_LOG_ENABLED", "1") == "1"
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")


def haal_selecties_op(lookback_days):
    return fetch_selections(SUPABASE_DB_URL, lookback_days)


def bouw_ranking(rows):
    ranking = group_signals(rows, LOOKBACK_DAYS, fresh=True)
    ranking.sort(
        key=lambda r: (
            -r["overlap"],
            -r["overlap_totaal"],
            -r["avg_score"],
            -date.fromisoformat(r["laatste_datum"]).toordinal(),
            r["ticker"],
            r["beurs"] or "",
        )
    )
    return ranking


def bereken_kosten(bedrag):
    return costs(bedrag, TOB_PCT)


def maak_telegram_bericht(ranking, lookback_days):
    return report(
        ranking,
        "Vers Signaal — overlap op signaaldatum",
        lookback_days,
        TOP_N,
        BESCHIKBAAR_KAPITAAL,
        TRANSACTIE_BEDRAG,
        TOB_PCT,
    )


def maak_email_html(ranking, lookback_days):
    return (
        "<html><body>"
        + maak_telegram_bericht(ranking, lookback_days).replace("\n", "<br>")
        + "</body></html>"
    )


def log_picks(ranking):
    if not DB_LOG_ENABLED:
        return 0
    errors = []
    stored = 0
    for rank, r in enumerate(ranking[:TOP_N], 1):
        if r["koers"] is None:
            errors.append(f"{r['ticker']}: geen koers op signaaldatum; niet opgeslagen")
            continue
        try:
            ok = log_selectie(
                ticker=r["ticker"],
                datum=r["laatste_datum"],
                strategie="vers_signaal_1d",
                beurs=r["beurs"],
                koers=r["koers"],
                parameters=dict(
                    rank=rank,
                    overlap_vandaag=r["overlap"],
                    overlap_totaal=r["overlap_totaal"],
                    avg_score=r["avg_score"],
                    laatste_datum=r["laatste_datum"],
                    strategieen=", ".join(r["strategieen"]),
                    grafiek=r["grafiek"],
                ),
            )
            if not ok:
                raise RuntimeError("geen opslagbevestiging")
            stored += 1
        except Exception as exc:
            errors.append(f"{r['ticker']}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))
    print(
        f"Database: {stored}/{min(len(ranking), TOP_N)} picks opgeslagen of reeds aanwezig."
    )
    return stored


def main():
    ranking = bouw_ranking(haal_selecties_op(LOOKBACK_DAYS))
    text = maak_telegram_bericht(ranking, LOOKBACK_DAYS)
    print(text, flush=True)
    # Report even a database error, then leave the run visibly failed.
    error = None
    try:
        log_picks(ranking)
    except RuntimeError as exc:
        error = exc
        text += "\n[ONVOLLEDIG] " + esc(str(exc))
    tg = send_telegram(text, parse_mode="HTML")
    email = send_email(
        "Vers Signaal " + today_str(),
        "<html><body>" + text.replace("\n", "<br>") + "</body></html>",
        html_body=True,
    )
    check_delivery(tg, email)
    if error:
        raise error
    print(f"Klaar: {len(ranking)} verse overlap-kandidaten.")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    main()
