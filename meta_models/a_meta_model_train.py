#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
a_meta_model_train.py — TRAINT HET META-MODEL v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Haalt de data op, traint de Ridge-regressie en schrijft de scaler
en coëfficiënten naar Supabase (tabel meta_model_models).
"""

import os
import sys
import argparse
import uuid
from datetime import datetime, timezone
from typing import List

import psycopg2
import psycopg2.extras
import pandas as pd
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from common import (
    today_str, safe_float, esc,
    send_telegram, send_email
)

from common.features import MODEL_FEATURES, MODEL_TARGET, MODEL_HORIZON
from common.model_inputs import canonical_selecties_sql, InputUnavailable, numeric_features, dates, unique_snapshots
from common.horizons import calendar_embargo

SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")

GENERIEKE_TECHNICALS = [
    "atr14", "atr14_pct", "rsi14", "ibs", "ma50", "ma200",
    "pct_from_ma50", "pct_from_ma200", "vol_ratio_20d", "high52w", "pct_from_high52w",
]

# ============================================================
# DATA OPHALEN
# ============================================================

def haal_training_data(conn, features):
    s = [f for f in features if f not in GENERIEKE_TECHNICALS]
    g = [f for f in features if f in GENERIEKE_TECHNICALS]
    s_lijst = ", ".join(f"s.{k}" for k in s)
    g_lijst = ("," + ", ".join(f"g.{k}" for k in g)) if g else ""

    query = canonical_selecties_sql() + f"""
        SELECT s.ticker, s.datum, s.strategie, {s_lijst}{g_lijst},
               f.{MODEL_TARGET} AS target
        FROM s
        JOIN forward_returns f
          ON s.ticker = f.ticker AND s.datum = f.datum AND s.strategie = f.strategie
        LEFT JOIN generieke_technicals g
          ON s.ticker = g.ticker AND s.datum = g.datum
        WHERE f.{MODEL_TARGET} IS NOT NULL
          AND s.koers > 0 AND s.koers < 'Infinity'::real
          AND f.entry_koers > 0 AND f.entry_koers < 'Infinity'::float8
          AND abs(s.koers - f.entry_koers)
              <= GREATEST(0.000001, abs(f.entry_koers) * 0.000001);
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query)
        return pd.DataFrame(cur.fetchall())

def cross_sectionele_rank(df, kolommen):
    out = df.copy()
    for k in kolommen:
        out[k] = out.groupby("datum")[k].rank(pct=True)
    return out

# ============================================================
# MAIN
# ============================================================

def prepare_training(df, features, *, today=None):
    if df.empty:
        raise InputUnavailable("Geen trainingdata; bestaand model behouden")
    out = numeric_features(df, [*features, "target"])
    out["datum"] = dates(out["datum"])
    now = pd.Timestamp(today) if today is not None else pd.Timestamp(today_str())
    cutoff = now.normalize() - pd.Timedelta(days=calendar_embargo(MODEL_HORIZON))
    out = out.loc[out.datum.notna() & (out.datum < cutoff)].copy()
    out = unique_snapshots(out, ["ticker", "datum", "strategie"], [*features, "target"])
    out = cross_sectionele_rank(out, features).dropna(subset=[*features, "target"])
    if len(out) < 200 or out.datum.nunique() < 10:
        raise InputUnavailable(f"Onvoldoende gerijpte trainingdata: {len(out)} rijen / {out.datum.nunique()} datums; minimaal 200/10")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--alpha", type=float, default=1.0,
                        help="Ridge regularisatie")
    parser.add_argument("--notities", type=str, default="",
                        help="Vrije tekst om aan de model-versie te hangen")
    args = parser.parse_args()

    if not SUPABASE_DB_URL:
        print("FOUT: SUPABASE_DB_URL ontbreekt.", file=sys.stderr)
        sys.exit(1)

    features = list(MODEL_FEATURES)
    conn = psycopg2.connect(SUPABASE_DB_URL)
    try:
        df = haal_training_data(conn, features)
        print(f"{len(df)} rijen opgehaald.")
        if not np.isfinite(args.alpha) or args.alpha <= 0:
            raise ValueError("Ridge alpha moet positief en eindig zijn")
        df = prepare_training(df, features)
        print(f"{len(df)} rijen na dropna, {df['datum'].nunique()} unieke datums.")

        X = df[features].values
        y = df["target"].values

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        model = Ridge(alpha=args.alpha)
        model.fit(X_scaled, y)

        versie = f"ridge_a{args.alpha}_h{MODEL_HORIZON}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"

        if not (np.isfinite(model.coef_).all() and np.isfinite(model.intercept_).all() and np.isfinite(scaler.mean_).all()
                and np.isfinite(scaler.scale_).all() and (scaler.scale_ > 0).all()):
            raise InputUnavailable("Training gaf ongeldige modelwaarden; bestaand model behouden")
        coefs = [float(c) for c in model.coef_]
        mean = [float(m) for m in scaler.mean_]
        std = [float(s) for s in scaler.scale_]

        insert = """
            INSERT INTO meta_model_models
                (versie, horizon_dagen, features, coefs, scaler_mean, scaler_std,
                 ridge_alpha, n_train_rijen, n_train_datums, getraind_op, notities)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        with conn.cursor() as cur:
            cur.execute(insert, (
                versie, MODEL_HORIZON, features, coefs, mean, std,
                args.alpha, len(df), int(df["datum"].nunique()),
                datetime.now(timezone.utc), args.notities or None,
            ))
        conn.commit()

        print(f"\nModel opgeslagen: {versie}")
        print(f"Features: {', '.join(features)}")
        print("Coëfficiënten:")
        for f, c in zip(features, coefs):
            print(f"  {f:<24} {c:+.4f}")
        print(f"Intercept: {model.intercept_:+.4f}")
        return 0
    except Exception as exc:
        print(f"Meta-training ONVOLLEDIG: {exc}", file=sys.stderr)
        send_telegram(f"Meta-training ONVOLLEDIG: {str(exc)[:600]}\nBestaand model behouden.", parse_mode=None)
        return 1
    finally:
        conn.close()

if __name__ == "__main__":
    sys.exit(main())
