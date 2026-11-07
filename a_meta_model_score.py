#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
a_meta_model_score.py — BEREKENT META-MODEL SCORES v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Leest het laatste model, scoort de laatste selectiedatum en schrijft
naar meta_model_scores. Stuurt een Telegram-bericht met de top 10.
"""

import os
import sys
import argparse

import psycopg2
import psycopg2.extras
import pandas as pd
import numpy as np

from common import (
    today_str, safe_float, esc, yahoo_link,
    send_telegram, send_email
)

from features import MODEL_HORIZON

SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")

GENERIEKE_TECHNICALS = [
    "atr14", "atr14_pct", "rsi14", "ibs", "ma50", "ma200",
    "pct_from_ma50", "pct_from_ma200", "vol_ratio_20d", "high52w", "pct_from_high52w",
]

MIN_COMPLETE_RIJEN = 10

# ============================================================
# TELEGRAM
# ============================================================

def _format_koers(koers) -> str:
    try:
        if koers is None or pd.isna(koers):
            return "-"
        return f"{float(koers):.2f}"
    except Exception:
        return "-"

def bouw_telegram_bericht(df: pd.DataFrame, datum: str, versie: str) -> str:
    scores = df["score"].values
    mediaan = float(np.median(scores))
    laagste = float(scores.min())
    hoogste = float(scores.max())
    std = float(scores.std(ddof=1)) if len(scores) > 1 else 0.0

    if "koers" not in df.columns:
        df["koers"] = np.nan

    top10 = df.nlargest(10, "score")[["ticker", "strategie", "score", "koers"]]

    regels = [
        "📊 <b>Meta-model scores</b>",
        "",
        f"Datum: <b>{esc(datum)}</b>",
        f"Model: <code>{esc(versie)}</code>",
        f"Totaal: <b>{len(df)}</b> scores",
        "",
        "<b>Top 10:</b>",
    ]

    for i, (_, r) in enumerate(top10.iterrows(), start=1):
        ticker = str(r["ticker"])
        ticker_url = ticker.replace(" ", "")
        yahoo_url = f"https://finance.yahoo.com/quote/{ticker_url}"
        koers_str = _format_koers(r.get("koers"))
        score_val = float(r["score"])

        regels.append(
            f"{i:>2}. <b>{esc(ticker)}</b> — "
            f"{esc(r['strategie'])} — "
            f"score <b>{score_val:+.3f}</b> — "
            f"koers {koers_str} — "
            f'<a href="{yahoo_url}">📈 Grafiek</a>'
        )

    regels += [
        "",
        "<b>Verdeling:</b>",
        f"  hoogste   {hoogste:+.3f}",
        f"  mediaan   {mediaan:+.3f}",
        f"  laagste   {laagste:+.3f}",
        f"  std       {std:.3f}",
        f"  spreiding {hoogste - laagste:.3f}",
    ]
    return "\n".join(regels)

# ============================================================
# DATABASE
# ============================================================

def haal_laatste_model(conn):
    query = """
        SELECT versie, horizon_dagen, features, coefs, scaler_mean, scaler_std, ridge_alpha
        FROM meta_model_models
        ORDER BY getraind_op DESC
        LIMIT 1;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query)
        row = cur.fetchone()
    return row

def haal_selecties(conn, features, datum=None):
    s = [f for f in features if f not in GENERIEKE_TECHNICALS]
    g = [f for f in features if f in GENERIEKE_TECHNICALS]

    select_delen = ["s.ticker", "s.datum", "s.strategie", "s.koers"]
    for k in s:
        select_delen.append(f"s.{k}")
    for k in g:
        select_delen.append(f"g.{k}")
    select_lijst = ",\n            ".join(select_delen)

    if g:
        g_select = ",\n                ".join(f"gt.{k}" for k in g)
        lateral = f"""
        LEFT JOIN LATERAL (
            SELECT
                {g_select}
            FROM generieke_technicals gt
            WHERE gt.ticker = s.ticker
              AND gt.datum::timestamptz <= s.datum::timestamptz
            ORDER BY gt.datum::timestamptz DESC
            LIMIT 1
        ) g ON TRUE
        """
    else:
        lateral = ""

    if datum is None:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(datum) FROM selecties;")
            datum = cur.fetchone()[0]
    print(f"Scoren voor datum: {datum}")

    query = f"""
        SELECT
            {select_lijst}
        FROM selecties s
        {lateral}
        WHERE s.datum = %s;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (datum,))
        return pd.DataFrame(cur.fetchall()), datum

# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--datum", type=str, default=None)
    args = parser.parse_args()

    if not SUPABASE_DB_URL:
        print("FOUT: SUPABASE_DB_URL ontbreekt.", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(SUPABASE_DB_URL)
    try:
        model_row = haal_laatste_model(conn)
        if model_row is None:
            print("Geen model gevonden. Draai eerst a_meta_model_train.py.")
            send_telegram("⚠️ <b>Meta-model score</b>\n\nGeen model gevonden.")
            sys.exit(1)

        versie = model_row["versie"]
        features = list(model_row["features"])
        coefs = np.array(model_row["coefs"], dtype=float)
        mean = np.array(model_row["scaler_mean"], dtype=float)
        std = np.array(model_row["scaler_std"], dtype=float)
        std = np.where(std == 0, 1.0, std)

        print(f"Model: {versie}")
        print(f"Horizon: {model_row['horizon_dagen']}d, features: {len(features)}")

        df, datum = haal_selecties(conn, features, args.datum)
        print(f"{len(df)} selecties opgehaald.")

        if df.empty:
            print("Geen selecties voor die datum.")
            send_telegram(f"📊 <b>Meta-model scores</b>\n\nDatum: <b>{datum}</b>\n<i>Geen selecties voor deze datum.</i>")
            return

        voor_dropna = df.copy()
        for k in features:
            df[k] = df[k].rank(pct=True)

        df = df.dropna(subset=features)
        gedropt = voor_dropna.loc[~voor_dropna.index.isin(df.index), "strategie"]
        if len(gedropt) > 0:
            print("Niet gescoord (feature ontbreekt), per strategie:")
            for strat, n in gedropt.value_counts().items():
                print(f"  {strat:<24} {n}")

        n_totaal = len(voor_dropna)
        n_gescoord = len(df)
        print(f"{n_gescoord} rijen met complete features.")

        if n_gescoord == 0:
            print("Geen complete rijen — alleen waarschuwing sturen.")
            send_telegram(
                f"⚠️ <b>Meta-model scores — geen complete data</b>\n\n"
                f"Datum: <b>{datum}</b>\n"
                f"Alle <b>{n_totaal}</b> selecties missen minstens één feature.\n"
                f"<i>Waarschijnlijk ontbreekt technische data voor deze datum.</i>"
            )
            return

        # Scores berekenen
        X = (df[features].values - mean) / std
        scores = X @ coefs
        df["score"] = scores

        insert = """
            INSERT INTO meta_model_scores
                (datum, ticker, strategie, score, model_versie, horizon_dagen)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (datum, ticker, strategie, model_versie, horizon_dagen)
            DO UPDATE SET score = EXCLUDED.score, created_at = now();
        """
        rijen = [
            (datum, r["ticker"], r["strategie"], float(r["score"]), versie, MODEL_HORIZON)
            for _, r in df.iterrows()
        ]
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, insert, rijen, page_size=500)
        conn.commit()
        print(f"{len(rijen)} scores opgeslagen in meta_model_scores.")

        top = df.nlargest(10, "score")[["ticker", "strategie", "score", "koers"]]
        print("\nTop 10 scores:")
        print(top.to_string(index=False))

        bericht = bouw_telegram_bericht(df, datum, versie)
        if n_gescoord < MIN_COMPLETE_RIJEN:
            waarschuwing = (
                f"⚠️ <i>Slechts {n_gescoord} van de {n_totaal} "
                f"selecties hadden complete features voor deze datum.</i>\n\n"
            )
            bericht = waarschuwing + bericht

        send_telegram(bericht, parse_mode="HTML")

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        print(err)
        send_telegram(f"⚠️ <b>Meta-model score FOUT</b>\n\n<code>{esc(str(e)[:400])}</code>", parse_mode="HTML")
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
   
