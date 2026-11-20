#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
a_meta_model.py — META-MODEL OP PARAMETER-FEATURES v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Bouwt een Ridge-regressiemodel met expanding-window walk-forward validatie.
"""

from common.horizons import calendar_embargo
from model_inputs import canonical_selecties_sql, numeric_features, unique_snapshots, dates

import os
import sys
import argparse
from typing import List

import psycopg2
import psycopg2.extras
import pandas as pd
import numpy as np
from scipy import stats
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from common import (
    today_str, safe_float, esc,
    send_telegram, send_email
)

from features import MODEL_FEATURES, MODEL_TARGET, MODEL_HORIZON

SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")

GENERIEKE_TECHNICALS_KOLOMMEN = [
    "atr14", "atr14_pct", "rsi14", "ibs", "ma50", "ma200",
    "pct_from_ma50", "pct_from_ma200", "vol_ratio_20d", "high52w", "pct_from_high52w",
]

# ============================================================
# DATA OPHALEN
# ============================================================

def haal_data(conn, features: List[str]) -> pd.DataFrame:
    s_feats = [f for f in features if f not in GENERIEKE_TECHNICALS_KOLOMMEN]
    g_feats = [f for f in features if f in GENERIEKE_TECHNICALS_KOLOMMEN]

    s_lijst = ", ".join(f"s.{k}" for k in s_feats)
    g_lijst = ("," + ", ".join(f"g.{k}" for k in g_feats)) if g_feats else ""

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
        rows = cur.fetchall()
    return pd.DataFrame(rows)

# ============================================================
# FEATURE ENGINEERING
# ============================================================

def cross_sectionele_rank(df: pd.DataFrame, kolommen: List[str]) -> pd.DataFrame:
    out = df.copy()
    for k in kolommen:
        out[k] = out.groupby("datum")[k].rank(pct=True)
    return out

def walk_forward(df: pd.DataFrame, features: List[str],
                 min_train_datums: int = 10,
                 min_train_rijen: int = 200,
                 min_test_rijen: int = 20) -> pd.DataFrame:
    df = df.copy()
    df["datum"] = pd.to_datetime(df["datum"], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
    df = df.dropna(subset=["datum"]).sort_values("datum").reset_index(drop=True)
    datums = sorted(df["datum"].unique())

    resultaten = []
    for i in range(min_train_datums, len(datums)):
        test_datum = datums[i]
        cutoff = test_datum - pd.Timedelta(days=calendar_embargo(MODEL_HORIZON))
        train = df[df["datum"] < cutoff].dropna(subset=features + ["target"])
        if train["datum"].nunique() < min_train_datums:
            continue
        test = df[df["datum"] == test_datum].dropna(subset=features + ["target"])

        if len(train) < min_train_rijen or len(test) < min_test_rijen:
            continue

        scaler = StandardScaler()
        X_train = scaler.fit_transform(train[features].values)
        X_test = scaler.transform(test[features].values)

        model = Ridge(alpha=1.0)
        model.fit(X_train, train["target"].values)
        preds = model.predict(X_test)

        if len(np.unique(preds)) < 2:
            continue
        ic, _ = stats.spearmanr(preds, test["target"].values)
        if np.isnan(ic):
            continue

        n_top = max(1, int(len(test) * 0.10))
        top_idx = np.argsort(preds)[-n_top:]
        top_ret = test["target"].values[top_idx].mean()
        markt_ret = test["target"].values.mean()

        resultaten.append({
            "datum": test_datum,
            "n_train": len(train),
            "n_test": len(test),
            "ic": ic,
            "top_decile_ret": top_ret,
            "markt_ret": markt_ret,
            "excess_top": top_ret - markt_ret,
            "coefs": dict(zip(features, model.coef_.tolist())),
        })

    return pd.DataFrame(resultaten)

# ============================================================
# RAPPORTAGE
# ============================================================

def rapport_kern(res: pd.DataFrame) -> list[str]:
    """Shared facts for the log and Telegram, with explicit benchmark/units."""
    if res.empty:
        return ["Geen testdatum voldeed aan de voorwaarden; geen evaluatieresultaat."]
    ordered = res.sort_values("datum")
    first = pd.Timestamp(ordered["datum"].iloc[0]).strftime("%Y-%m-%d")
    last = pd.Timestamp(ordered["datum"].iloc[-1]).strftime("%Y-%m-%d")
    wins = int((ordered["excess_top"] > 0).sum())
    recent = ordered.tail(5)
    return [
        f"Testperiode: {first} t/m {last}",
        f"Testdatums (folds): {len(ordered)}; testwaarnemingen: {int(ordered['n_test'].sum())}",
        f"Gemiddelde IC: {ordered['ic'].mean():+.4f}",
        f"Top 10% beter dan selectiegemiddelde: {wins}/{len(ordered)} ({wins / len(ordered):.1%})",
        f"Gem. rendement top 10%: {ordered['top_decile_ret'].mean():+.2f}%; "
        f"selectiegemiddelde: {ordered['markt_ret'].mean():+.2f}%",
        f"Gem. verschil: {ordered['excess_top'].mean():+.2f} procentpunt",
        f"Laatste {len(recent)} testdatums: IC {recent['ic'].mean():+.4f}; "
        f"verschil {recent['excess_top'].mean():+.2f} procentpunt",
        "Gemiddelden wegen iedere testdatum even zwaar; geen portefeuillerendement.",
        "Historische evaluatie; geen bewijs van een stabiel voordeel.",
    ]


def print_rapport(res: pd.DataFrame, features: List[str]) -> str:
    if res.empty:
        tekst = rapport_kern(res)[0]
        print(tekst)
        return tekst

    res = res.sort_values("datum")
    ics = res["ic"].values
    gem_ic = ics.mean()
    std_ic = ics.std(ddof=1) if len(ics) > 1 else np.nan
    ir = gem_ic / std_ic if np.isfinite(std_ic) and std_ic > 0 else np.nan
    t_stat = ir * np.sqrt(len(ics)) if np.isfinite(ir) else np.nan

    regels = [
        f"\n{'=' * 110}",
        f"META-MODEL — walk-forward evaluatie op {MODEL_TARGET}",
        "=" * 110,
        f"Features ({len(features)}): {', '.join(features)}",
        f"Horizon: {MODEL_HORIZON} handelsdagen; trainingsbuffer: {calendar_embargo(MODEL_HORIZON)} kalenderdagen",
        *rapport_kern(res),
    ]
    if np.isfinite(std_ic):
        regels.append(f"Spreiding IC: {std_ic:.4f}; IR (IC/std): {ir:.3f}")
    if np.isfinite(t_stat):
        regels.append(f"Naïeve t-stat: {t_stat:.2f} (niet gecorrigeerd voor overlappende rendementshorizonten)")
    regels += [
        "", "Toelichting:",
        "- IC: rangcorrelatie tussen voorspelde score en later rendement; positief is de gewenste richting.",
        "- Referentie: gemiddelde van alle bruikbare selecties op dezelfde testdatum, geen beursindex.",
        "- Waarnemingen zijn ticker/datum/strategie-rijen; dezelfde ticker kan bij meerdere strategieën voorkomen.",
        "- Top 10% = max(1, afgerond naar beneden 10% van n_test); * markeert minder dan vijf toprijen.",
        "", "Per testdatum (rendementen in %, verschil in procentpunten):",
        f"{'datum':<12}{'n_train':>9}{'n_test':>8}{'n_top':>7}{'IC':>10}{'top 10%':>11}{'referentie':>12}{'verschil':>11}",
    ]
    for _, r in res.iterrows():
        datum = pd.Timestamp(r["datum"]).strftime("%Y-%m-%d")
        n_top = max(1, int(r["n_test"] * 0.10))
        marker = " *" if n_top < 5 else ""
        regels.append(f"{datum:<12}{int(r['n_train']):>9}{int(r['n_test']):>8}{n_top:>7}"
                      f"{r['ic']:>+10.4f}{r['top_decile_ret']:>+11.4f}"
                      f"{r['markt_ret']:>+12.4f}{r['excess_top']:>+11.4f}{marker}")

    coef_df = pd.DataFrame([r["coefs"] for _, r in res.iterrows()])
    gem_coefs = coef_df.mean().sort_values(key=lambda values: values.abs(), ascending=False)
    regels.append("\nGemiddelde coëfficiënten op geschaalde features (geen causale invloed):")
    for naam, c in gem_coefs.items():
        regels.append(f"  {naam:<24} {c:+.4f}")

    tekst = "\n".join(regels)
    print(tekst)
    return tekst

# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-train-datums", type=int, default=10)
    parser.add_argument("--min-train-rijen", type=int, default=200)
    parser.add_argument("--min-test-rijen", type=int, default=20)
    args = parser.parse_args()

    if not SUPABASE_DB_URL:
        print("FOUT: SUPABASE_DB_URL ontbreekt.", file=sys.stderr)
        sys.exit(1)

    features = list(MODEL_FEATURES)

    conn = psycopg2.connect(SUPABASE_DB_URL)
    try:
        df = haal_data(conn, features)
        print(f"{len(df)} rijen opgehaald voor {MODEL_TARGET}.")
        if df.empty:
            print("Geen data.")
            return

        df = numeric_features(df, [*features, "target"])
        df["datum"] = dates(df["datum"])
        df = unique_snapshots(df, ["ticker", "datum", "strategie"], [*features, "target"])
        df = cross_sectionele_rank(df, features)

        res = walk_forward(df, features,
                           min_train_datums=args.min_train_datums,
                           min_train_rijen=args.min_train_rijen,
                           min_test_rijen=args.min_test_rijen)

        tekst = print_rapport(res, features)

        if not res.empty:
            samenvatting = "\n".join([
                f"Meta-model — historische evaluatie {MODEL_TARGET}",
                *rapport_kern(res),
                "IC/t-stat zijn beschrijvend; opeenvolgende rendementshorizonten overlappen.",
                "Volledig rapport: GitHub-runlog en artifact meta-model-output.",
            ])
            send_telegram(samenvatting, parse_mode=None)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
