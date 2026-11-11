#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only diagnose van LightGBM V2 splits, zonder training of modelwijzigingen.

Gebruik: python lightgbm_v2_preflight.py --csv trainingsdata.csv
    of: python lightgbm_v2_preflight.py --database

CSV vereist: ticker, datum, 11 FEATURE_COLUMNS en fwd_ret_10d/30d/60d.
--database leest dezelfde technische indicatoren en gededupliceerde forward
returns als xLightgbmV2.py. Geen schrijfacties naar Supabase.
"""
import argparse
import json
import math
import os
import sys
from datetime import timedelta

FEATURES = ["atr14", "atr14_pct", "rsi14", "ibs", "ma50", "ma200",
            "pct_from_ma50", "pct_from_ma200", "vol_ratio_20d", "high52w",
            "pct_from_high52w"]
HORIZONS = ("10d", "30d", "60d")
SQL = """
WITH fr AS (
 SELECT DISTINCT ON (ticker, datum) ticker, datum,
        fwd_ret_10d, fwd_ret_30d, fwd_ret_60d
 FROM forward_returns ORDER BY ticker, datum
)
SELECT gt.ticker, gt.datum, gt.atr14, gt.atr14_pct, gt.rsi14, gt.ibs,
       gt.ma50, gt.ma200, gt.pct_from_ma50, gt.pct_from_ma200,
       gt.vol_ratio_20d, gt.high52w, gt.pct_from_high52w,
       fr.fwd_ret_10d, fr.fwd_ret_30d, fr.fwd_ret_60d
FROM generieke_technicals gt JOIN fr
 ON fr.ticker=gt.ticker AND fr.datum=gt.datum
ORDER BY gt.datum, gt.ticker
"""


def diagnose(df, horizon, target_mode="excess"):
    import pandas as pd
    target = "fwd_ret_" + horizon
    required = ["ticker", "datum", target, *FEATURES]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return {"horizon": horizon, "status": "MISSING_COLUMNS", "missing": missing}
    work = df[required].copy()
    work["datum"] = pd.to_datetime(work["datum"], errors="coerce")
    work = work.drop_duplicates(["ticker", "datum"], keep="first")
    for c in [target, *FEATURES]:
        work[c] = pd.to_numeric(work[c], errors="coerce")
    work = work.dropna(subset=["datum", target, *FEATURES])
    if target_mode == "excess":
        sizes = work.groupby("datum")[target].transform("size")
        work = work.loc[sizes >= 10].copy()
        if not work.empty:
            median = work.groupby("datum")[target].transform("median")
            work["target"] = (work[target] > median).astype(int)
    elif not work.empty:
        work["target"] = (work[target] > 0).astype(int)
    dates = sorted(work["datum"].unique())
    embargo_days = math.ceil(int(horizon[:-1]) * 7 / 5) + 2
    result = {"horizon": horizon, "target_mode": target_mode,
              "usable_rows": len(work), "usable_dates": len(dates),
              "embargo_calendar_days": embargo_days,
              "train_rows": 0, "test_rows": 0, "train_dates": 0, "test_dates": 0}
    if len(dates) < 20 or len(work) < 130:
        result.update(status="WAIT_DATA", reason="Minder dan 20 datums of 130 rijen")
        return result
    idx = max(0, int(len(dates) * .8) - 1)
    split = pd.Timestamp(dates[idx])
    cutoff = split - pd.Timedelta(days=embargo_days)
    train = work.loc[work["datum"] <= cutoff]
    test = work.loc[work["datum"] > split]
    result.update(split_date=split.date().isoformat(),
                  train_cutoff=cutoff.date().isoformat(),
                  train_rows=len(train), test_rows=len(test),
                  train_dates=train["datum"].nunique(),
                  test_dates=test["datum"].nunique())
    if len(train) < 100 or len(test) < 30:
        result.update(status="WAIT_DATA", reason="Te weinig rijen na chronologisch embargo")
    elif train["target"].nunique() < 2:
        result.update(status="WAIT_DATA", reason="Training heeft slechts een targetklasse")
    else:
        result.update(status="READY", reason="Buitenste split is uitvoerbaar; interne validatie wordt apart gecontroleerd")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv")
    source.add_argument("--database", action="store_true")
    parser.add_argument("--target", choices=("abs", "excess"), default="excess")
    parser.add_argument("--output", help="Optioneel JSON-rapport")
    args = parser.parse_args()
    import pandas as pd
    if args.csv:
        df = pd.read_csv(args.csv)
    else:
        import psycopg2
        url = os.environ.get("SUPABASE_DB_URL")
        if not url:
            parser.error("SUPABASE_DB_URL ontbreekt")
        with psycopg2.connect(url) as conn:
            df = pd.read_sql_query(SQL, conn)
    report = {"source_rows": len(df), "horizons": [diagnose(df, h, args.target) for h in HORIZONS]}
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output + "\n")
    return 0  # ontbrekende historische labels zijn geen pipelinefout


if __name__ == "__main__":
    sys.exit(main())
