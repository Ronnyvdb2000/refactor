"""Beste Signaal: unieke strategiefamilies in een recent venster, met coherente XGBoost-snapshot."""

import argparse
import os
from datetime import date, timedelta
import psycopg2
import psycopg2.extras
from common import today_str, send_telegram, send_email
from .shared import (
    fetch_selections,
    group_signals,
    finite,
    costs,
    report,
    check_delivery,
)

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "3"))
BESCHIKBAAR_KAPITAAL = float(os.getenv("BESCHIKBAAR_KAPITAAL", "2500"))
TRANSACTIE_BEDRAG = float(os.getenv("TRANSACTIE_BEDRAG", "2500"))
TOP_N = AANTAL_PICKS = int(os.getenv("AANTAL_PICKS", "5"))
TOB_PCT = float(os.getenv("TOB_PCT", "0"))
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
XGB_HORIZON = os.getenv("XGB_HORIZON", "10d")
XGB_MODEL_VERSIE = os.getenv("XGB_MODEL_VERSIE", "")


def haal_selecties_op(lookback_days):
    return fetch_selections(SUPABASE_DB_URL, lookback_days)


def haal_xgboost_scores_op(lookback_days):
    if not SUPABASE_DB_URL:
        return {}
    end = date.fromisoformat(today_str())
    start = end - timedelta(days=lookback_days - 1)
    # Persisted ranks may refer to distinct scoring batches. Recompute ranks
    # within one model/date snapshot; never combine historical MAX and MIN.
    query = """WITH eligible AS (
        SELECT ticker,datum,model_versie,score,gescoord_op FROM xgboost_scores
        WHERE datum >= %s AND datum <= %s AND horizon=%s
          AND (%s='' OR model_versie=%s)
          AND score > '-Infinity'::float8 AND score < 'Infinity'::float8
    ), snapshot AS (
        SELECT datum,model_versie FROM eligible
        ORDER BY datum DESC,gescoord_op DESC NULLS LAST,model_versie DESC LIMIT 1
    )
    SELECT e.ticker,e.score,e.datum,e.model_versie,
           row_number() OVER(ORDER BY e.score DESC,e.ticker) AS beste_rang
    FROM eligible e JOIN snapshot s USING(datum,model_versie)"""
    with psycopg2.connect(SUPABASE_DB_URL, connect_timeout=15) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                query,
                (
                    start.isoformat(),
                    end.isoformat(),
                    XGB_HORIZON,
                    XGB_MODEL_VERSIE,
                    XGB_MODEL_VERSIE,
                ),
            )
            return {
                r["ticker"]: dict(
                    score=float(r["score"]),
                    beste_rang=int(r["beste_rang"]),
                    datum=r["datum"],
                    model_versie=r["model_versie"],
                    n_datums=1,
                )
                for r in cur.fetchall()
            }


def bouw_ranking(rows, xgb_scores=None):
    ranking = group_signals(rows, LOOKBACK_DAYS)
    for r in ranking:
        x = (xgb_scores or {}).get(r["ticker"], {})
        usable = (
            finite(x.get("score")) is not None
            and finite(x.get("beste_rang")) is not None
            and x.get("datum") == r["laatste_datum"]
            and x["beste_rang"] >= 1
        )
        r.update(
            xgb_rang=x["beste_rang"] if usable else 999999999,
            xgb_score=x["score"] if usable else 0.0,
            xgb_datums=1 if usable else 0,
            heeft_xgb=usable,
            is_kasstr="bot_01kasstr" in r["strategieen"],
            xgb_datum=x.get("datum"),
            xgb_model=x.get("model_versie", ""),
        )
    ranking.sort(
        key=lambda r: (
            -r["overlap"],
            -int(r["heeft_xgb"]),
            r["xgb_rang"],
            -int(r["is_kasstr"]),
            -r["avg_score"],
            -date.fromisoformat(r["laatste_datum"]).toordinal(),
            r["ticker"],
            r["beurs"] or "",
        )
    )
    return ranking


def bereken_kosten(bedrag):
    return costs(bedrag, TOB_PCT)


def maak_telegram_bericht(ranking, lookback_days, totaal_strategieen=0):
    return report(
        ranking,
        "Beste Signaal",
        lookback_days,
        TOP_N,
        BESCHIKBAAR_KAPITAAL,
        TRANSACTIE_BEDRAG,
        TOB_PCT,
        xgb=True,
    )


def maak_email_html(ranking, lookback_days, totaal_strategieen=0):
    return (
        "<html><body>"
        + maak_telegram_bericht(ranking, lookback_days).replace("\n", "<br>")
        + "</body></html>"
    )


def main():
    rows = haal_selecties_op(LOOKBACK_DAYS)
    ranking = bouw_ranking(rows, haal_xgboost_scores_op(LOOKBACK_DAYS))
    text = maak_telegram_bericht(ranking, LOOKBACK_DAYS)
    print(text, flush=True)
    tg = send_telegram(text, parse_mode="HTML")
    email = send_email(
        "Beste Signaal " + today_str(),
        maak_email_html(ranking, LOOKBACK_DAYS),
        html_body=True,
    )
    check_delivery(tg, email)
    print(f"Klaar: {len(ranking)} unieke ticker/beurs-combinaties.")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    main()
