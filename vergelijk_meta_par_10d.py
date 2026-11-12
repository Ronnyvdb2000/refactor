#!/usr/bin/env python3
"""Read-only 10d walk-forward comparison of existing a_par_model and meta-label approach.

No database writes, notifications, or production model updates. Requires the
repository's a_par_model.py and features.py in the same working directory.
"""
import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from scipy.stats import spearmanr
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

from a_par_model import bereken_ic_per_parameter, NIET_FACTOR
from features import MODEL_FEATURES

TECH = ["atr14", "atr14_pct", "rsi14", "ibs", "ma50", "ma200", "pct_from_ma50",
        "pct_from_ma200", "vol_ratio_20d", "high52w", "pct_from_high52w"]
STRATEGIES = ["bot_00xxxV2", "bot_00vcp", "bot_00mr", "bot_00dm", "bot_00ms"]


def load_data(conn):
    # A single row per selection; dedup forward_returns and technicals to avoid
    # accidental multiplication of observations.
    query = """
    WITH s AS (
      SELECT DISTINCT ON (ticker, datum, strategie)
        ticker, datum, strategie, score
      FROM selecties
      WHERE strategie = ANY(%s)
      ORDER BY ticker, datum, strategie
    ), f AS (
      SELECT DISTINCT ON (ticker, datum, strategie)
        ticker, datum, strategie, fwd_ret_10d
      FROM forward_returns
      WHERE fwd_ret_10d IS NOT NULL
      ORDER BY ticker, datum, strategie
    ), g AS (
      SELECT DISTINCT ON (ticker, datum)
        ticker, datum, atr14, atr14_pct, rsi14, ibs, ma50, ma200,
        pct_from_ma50, pct_from_ma200, vol_ratio_20d, high52w,
        pct_from_high52w
      FROM generieke_technicals
      ORDER BY ticker, datum
    )
    SELECT s.ticker, s.datum, s.strategie, s.score,
           f.fwd_ret_10d, g.atr14, g.atr14_pct, g.rsi14, g.ibs,
           g.ma50, g.ma200, g.pct_from_ma50, g.pct_from_ma200,
           g.vol_ratio_20d, g.high52w, g.pct_from_high52w
    FROM s JOIN f ON (s.ticker=f.ticker AND s.datum=f.datum AND s.strategie=f.strategie)
    LEFT JOIN g ON (s.ticker=g.ticker AND s.datum=g.datum)
    """
    return pd.read_sql_query(query, conn, params=(STRATEGIES,))


def normalize(df):
    df = df.copy()
    df["datum"] = pd.to_datetime(df["datum"], utc=True).dt.tz_localize(None).dt.normalize()
    for c in ["score", "fwd_ret_10d"] + TECH:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # For ranking, keep one ticker/date: strategy signals are separately evaluated
    # by the meta classifier, while parameter IC is per unique ticker/date.
    return df.dropna(subset=["datum", "fwd_ret_10d"]).sort_values("datum")


def par_scores(train, test, min_ic, min_dates):
    unique_train = train.drop_duplicates(["ticker", "datum"]).copy()
    weights = bereken_ic_per_parameter(
        unique_train, "fwd_ret_10d", unique_train["datum"].min(),
        unique_train["datum"].max(), min_dates, .15, min_tickers=10)
    if weights.empty:
        return None, 0
    weights = weights[weights["ic_abs"] >= min_ic]
    if weights.empty:
        return None, 0
    # Same scoring rule as a_par_model.py: centered cross-sectional ranks.
    day = test.drop_duplicates(["ticker", "datum"]).set_index("ticker")
    total = float(weights["ic"].abs().sum())
    if total <= 0:
        return None, 0
    scores = pd.Series(0.0, index=day.index)
    for _, row in weights.iterrows():
        p = row["parameter"]
        if p in day.columns:
            scores += float(row["ic"]) * (day[p].rank(pct=True).fillna(.5) - .5)
    return scores / total, len(weights)


def add_rank_features(df):
    df = df.copy()
    feats = list(MODEL_FEATURES)
    for f in feats:
        if f not in df:
            raise ValueError(f"Feature ontbreekt: {f}")
        df[f"F_{f}"] = df.groupby("datum")[f].rank(pct=True)
    return df, [f"F_{f}" for f in feats]


def summarize(rows, method):
    sub = rows[rows["methode"] == method]
    if sub.empty:
        return {"methode": method, "folds": 0, "n": 0}
    return {"methode": method, "folds": int(sub["datum"].nunique()),
            "n": int(sub["n"].sum()),
            "gem_top20_ret_pct": round(float(sub["top_ret"].mean()), 4),
            "gem_baseline_ret_pct": round(float(sub["baseline_ret"].mean()), 4),
            "gem_excess_pp": round(float(sub["excess"].mean()), 4),
            "win_fold_pct": round(float((sub["excess"] > 0).mean() * 100), 1)}


def run(df, args):
    df, feature_cols = add_rank_features(df)
    df["label"] = np.where(df["fwd_ret_10d"] > args.threshold, 1,
                           np.where(df["fwd_ret_10d"] < -args.threshold, 0, np.nan))
    dates = sorted(df["datum"].unique())
    cutoff = pd.Timestamp(dates[int(len(dates) * .7)]) if len(dates) >= 4 else None
    if cutoff is None:
        raise ValueError("Te weinig unieke datums")
    rows = []
    predictions = []
    for day in dates:
        day = pd.Timestamp(day)
        if day <= cutoff:
            continue
        # Strict calendar embargo: training date must precede test by >15 days.
        train = df[df["datum"] < day - pd.Timedelta(days=args.embargo)]
        test = df[df["datum"] == day].copy()
        if train["datum"].nunique() < args.min_train_dates or len(test) < args.min_test:
            continue
        # Compare on the SAME eligible rows per day; skip day if either method unavailable.
        p_scores, n_weights = par_scores(train, test, args.min_ic, args.min_ic_dates)
        labeled = train.dropna(subset=["label"])
        if p_scores is None or len(labeled) < args.min_train or labeled["label"].nunique() < 2:
            continue
        X_train = pd.get_dummies(labeled[feature_cols + ["strategie"]], columns=["strategie"])
        X_test = pd.get_dummies(test[feature_cols + ["strategie"]], columns=["strategie"])
        X_test = X_test.reindex(columns=X_train.columns, fill_value=0)
        model = make_pipeline(SimpleImputer(strategy="median", add_indicator=False),
                              StandardScaler(),
                              LogisticRegression(max_iter=2000, class_weight="balanced"))
        model.fit(X_train, labeled["label"].astype(int))
        test["meta_score"] = model.predict_proba(X_test)[:, 1]
        test["par_score"] = test["ticker"].map(p_scores)
        test["ma50_score"] = -test["pct_from_ma50"]
        test = test.dropna(subset=["meta_score", "par_score", "ma50_score"])
        if len(test) < args.min_test:
            continue
        # Per-date top fraction; baseline = all original strategy selections.
        k = max(1, int(np.ceil(len(test) * args.top_fraction)))
        baseline = float(test["fwd_ret_10d"].mean())
        for method, score_col in [("a_par_model", "par_score"),
                                  ("meta_label", "meta_score"),
                                  ("ma50", "ma50_score")]:
            top = test.nlargest(k, score_col)
            ret = float(top["fwd_ret_10d"].mean())
            rows.append({"datum": day.date().isoformat(), "methode": method,
                         "n": len(test), "n_top": k, "n_par_gewichten": n_weights,
                         "baseline_ret": baseline, "top_ret": ret, "excess": ret-baseline})
            for _, r in top.iterrows():
                predictions.append({"datum": day.date().isoformat(), "methode": method,
                                    "ticker": r["ticker"], "strategie": r["strategie"],
                                    "score": float(r[score_col]),
                                    "fwd_ret_10d": float(r["fwd_ret_10d"])})
    return pd.DataFrame(rows), pd.DataFrame(predictions), cutoff


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--embargo", type=int, default=15)
    ap.add_argument("--min-train", type=int, default=200)
    ap.add_argument("--min-train-dates", type=int, default=10)
    ap.add_argument("--min-test", type=int, default=20)
    ap.add_argument("--min-ic-dates", type=int, default=5)
    ap.add_argument("--min-ic", type=float, default=.02)
    ap.add_argument("--threshold", type=float, default=3.0)
    ap.add_argument("--top-fraction", type=float, default=.20)
    ap.add_argument("--out", default="results/meta_par_10d")
    args = ap.parse_args()
    if not (0 < args.top_fraction <= 1):
        ap.error("--top-fraction moet tussen 0 en 1 liggen")
    if args.embargo < 15:
        ap.error("Embargo moet minstens 15 kalenderdagen zijn voor 10d")
    db_url = os.getenv("SUPABASE_DB_URL")
    if not db_url:
        ap.error("SUPABASE_DB_URL ontbreekt")
    with psycopg2.connect(db_url) as conn:
        conn.set_session(readonly=True)
        df = normalize(load_data(conn))
    print(f"Dataset: {len(df)} selecties, {df['datum'].nunique()} datums")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, picks, cutoff = run(df, args)
    summary = {"status": "ok" if not rows.empty else "insufficient_data",
               "cutoff_70pct": cutoff.date().isoformat(),
               "embargo_calendar_days": args.embargo,
               "notes": "Experimentele vergelijking; geen productievoorspellingen.",
               "results": [summarize(rows, m) for m in ["a_par_model", "meta_label", "ma50"]]}
    (out / "samenvatting.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if not rows.empty:
        rows.to_csv(out / "folds.csv", index=False)
        picks.to_csv(out / "top_selecties.csv", index=False)
    print(json.dumps(summary, indent=2))
    if rows.empty:
        print("Geen vergelijkbare folds: verzamel meer data of bekijk de drempels.")


if __name__ == "__main__":
    main()
