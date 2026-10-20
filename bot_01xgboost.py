#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01xgboost.py — XGBOOST RICHTINGSVOORSPELLING v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Voorspelt de kans op een stijging > drempel over HORIZON_DAYS.
"""

import os
import sys
import json
import math
import time
import warnings
import datetime as dt
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
import xgboost as xgb
from sklearn.metrics import roc_auc_score, accuracy_score, precision_score

from common import (
    today_str, safe_float, yahoo_link,
    download_history, load_tickers_from_file, bouw_bestandslijst, label_voor,
    compute_rsi_series, compute_macd_series, compute_atr_series,
    send_telegram, send_email
)

try:
    from db_logger import log_selectie
except Exception as _e:
    print(f"[WARN] db_logger niet beschikbaar ({_e}) — DB-logging wordt overgeslagen")
    def log_selectie(*args, **kwargs):
        return False

warnings.filterwarnings("ignore", category=FutureWarning)

# ============================================================
# CONFIG
# ============================================================

START_CAPITAL        = 50_000.0
TRADE_COST_FIXED     = 15.0
TRADE_COST_PCT       = 0.0035
TAX_RATE             = 0.10
SLIPPAGE_PCT         = 0.001

XGB_CFG = {
    "rsi_period":          14,
    "macd_fast":           12,
    "macd_slow":           26,
    "macd_signal":         9,
    "atr_period":          14,
    "sma_short_period":    50,
    "sma_trend_period":    200,
    "vol_avg_period":      20,
    "horizon_days":        10,
    "label_threshold_pct": 3.0,
    "n_estimators":        400,
    "max_depth":           4,
    "learning_rate":       0.05,
    "subsample":           0.8,
    "colsample_bytree":    0.8,
    "min_child_weight":    5,
    "early_stopping_rounds": 30,
    "is_window":           504,
    "rescan_days":         63,
    "min_train_rows":      500,
    "min_proba":           0.62,
    "top_n_per_beurs":     5,
}

FEATURE_COLUMNS = [
    "rsi", "macd_hist", "macd_hist_slope",
    "dist_sma50_pct", "dist_sma200_pct",
    "atr_pct", "vol_ratio_20d",
    "ret_5d", "ret_20d", "ret_60d",
]

MODEL_PATH      = "model_xgboost.json"
MODEL_META_PATH = "model_xgboost_meta.json"

BACKTEST_START = "2019-01-01"
BACKTEST_END   = today_str()

# ============================================================
# FEATURE ENGINEERING
# ============================================================

def build_features(df_ticker: pd.DataFrame) -> pd.DataFrame:
    df = df_ticker.set_index("Date")
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]

    rsi = compute_rsi_series(close, XGB_CFG["rsi_period"])
    _, _, macd_hist = compute_macd_series(close, XGB_CFG["macd_fast"], XGB_CFG["macd_slow"], XGB_CFG["macd_signal"])
    atr = compute_atr_series(high, low, close, XGB_CFG["atr_period"])
    sma_short = close.rolling(XGB_CFG["sma_short_period"]).mean()
    sma_trend = close.rolling(XGB_CFG["sma_trend_period"]).mean()
    vol_avg   = vol.rolling(XGB_CFG["vol_avg_period"]).mean()

    feat = pd.DataFrame(index=df.index)
    feat["rsi"]              = rsi
    feat["macd_hist"]        = macd_hist
    feat["macd_hist_slope"]  = macd_hist.diff(3)
    feat["dist_sma50_pct"]   = (close - sma_short) / sma_short * 100.0
    feat["dist_sma200_pct"]  = (close - sma_trend) / sma_trend * 100.0
    feat["atr_pct"]          = atr / close * 100.0
    feat["vol_ratio_20d"]    = vol / (vol_avg + 1e-9)
    feat["ret_5d"]           = close.pct_change(5) * 100.0
    feat["ret_20d"]          = close.pct_change(20) * 100.0
    feat["ret_60d"]          = close.pct_change(60) * 100.0

    horizon = XGB_CFG["horizon_days"]
    thr     = XGB_CFG["label_threshold_pct"]
    fwd_return_pct = (close.shift(-horizon) / close - 1.0) * 100.0

    label = pd.Series(np.nan, index=df.index)
    label[fwd_return_pct > thr]  = 1.0
    label[fwd_return_pct < -thr] = 0.0

    feat["fwd_return_pct"] = fwd_return_pct
    feat["label"] = label
    feat["Close"] = close
    return feat

def build_dataset(hist: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for ticker, g in hist.groupby("Ticker"):
        g = g.sort_values("Date")
        if len(g) < XGB_CFG["sma_trend_period"] + XGB_CFG["horizon_days"] + 20:
            continue
        feat = build_features(g)
        feat = feat.reset_index()
        feat.insert(0, "Ticker", ticker)
        frames.append(feat)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)

# ============================================================
# MODEL TRAIN / LOAD / SAVE
# ============================================================

def _make_model() -> xgb.XGBClassifier:
    return xgb.XGBClassifier(
        n_estimators=XGB_CFG["n_estimators"],
        max_depth=XGB_CFG["max_depth"],
        learning_rate=XGB_CFG["learning_rate"],
        subsample=XGB_CFG["subsample"],
        colsample_bytree=XGB_CFG["colsample_bytree"],
        min_child_weight=XGB_CFG["min_child_weight"],
        objective="binary:logistic",
        eval_metric="auc",
        early_stopping_rounds=XGB_CFG["early_stopping_rounds"],
        n_jobs=-1,
    )

def fit_model(train_df: pd.DataFrame, val_df: Optional[pd.DataFrame] = None) -> xgb.XGBClassifier:
    X_train = train_df[FEATURE_COLUMNS]
    y_train = train_df["label"]
    model = _make_model()
    if val_df is not None and len(val_df) > 0:
        model.fit(X_train, y_train, eval_set=[(val_df[FEATURE_COLUMNS], val_df["label"])], verbose=False)
    else:
        model.set_params(early_stopping_rounds=None)
        model.fit(X_train, y_train, verbose=False)
    return model

def save_model(model: xgb.XGBClassifier, trained_until: str, n_rows: int, val_metrics: Dict) -> None:
    model.save_model(MODEL_PATH)
    meta = {
        "trained_at": today_str(),
        "trained_until": trained_until,
        "n_rows": n_rows,
        "feature_columns": FEATURE_COLUMNS,
        "config": XGB_CFG,
        "val_metrics": val_metrics,
    }
    with open(MODEL_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"Model opgeslagen: {MODEL_PATH} + {MODEL_META_PATH}")

def load_model() -> Tuple[Optional[xgb.XGBClassifier], Optional[Dict]]:
    if not os.path.exists(MODEL_PATH) or not os.path.exists(MODEL_META_PATH):
        return None, None
    model = xgb.XGBClassifier()
    model.load_model(MODEL_PATH)
    with open(MODEL_META_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return model, meta

# ============================================================
# MODES: TRAIN / BACKTEST / LIVE
# ============================================================

def run_train():
    print(f"{'='*60}\nXGBOOST TRAINING  {today_str()}\n{'='*60}")

    universum = set()
    for f_name in bouw_bestandslijst():
        universum.update(load_tickers_from_file(f_name))
    universum = sorted(universum)
    if not universum:
        print("[ERROR] Geen ticker bestanden gevonden.")
        return
    print(f"Universum: {len(universum)} tickers over de x-lijsten (041-059)")

    hist = download_history(universum, period="10y")
    if hist.empty:
        print("[ERROR] Geen historische data opgehaald.")
        return
    print(f"Historische data: {len(hist)} rijen over {hist['Ticker'].nunique()} tickers")

    dataset = build_dataset(hist)
    dataset = dataset.dropna(subset=FEATURE_COLUMNS + ["label"])
    if len(dataset) < XGB_CFG["min_train_rows"]:
        print(f"[ERROR] Te weinig trainingsrijen ({len(dataset)}), stop.")
        return

    dataset = dataset.sort_values("Date")
    print(f"Trainingsdataset: {len(dataset)} rijen")
    print(f"Klassebalans: {dataset['label'].mean()*100:.1f}% positief (label=1)")

    split_idx = int(len(dataset) * 0.85)
    split_date = dataset.iloc[split_idx]["Date"]
    train_df = dataset[dataset["Date"] < split_date]
    val_df   = dataset[dataset["Date"] >= split_date]
    print(f"Train: {len(train_df)} rijen tot {split_date.date()} | Validatie: {len(val_df)} rijen erna")

    model = fit_model(train_df, val_df)

    val_proba = model.predict_proba(val_df[FEATURE_COLUMNS])[:, 1]
    val_pred  = (val_proba >= XGB_CFG["min_proba"]).astype(int)
    metrics = {
        "auc": float(roc_auc_score(val_df["label"], val_proba)),
        "accuracy_at_threshold": float(accuracy_score(val_df["label"], val_pred)) if val_pred.sum() > 0 else None,
        "precision_at_threshold": float(precision_score(val_df["label"], val_pred, zero_division=0)),
        "n_signals_at_threshold": int(val_pred.sum()),
        "n_val_rows": len(val_df),
    }
    print(f"\nValidatie (out-of-sample, {split_date.date()} -> nu):")
    print(f"  AUC:                    {metrics['auc']:.3f}  (0.5 = toeval, 1.0 = perfect)")
    print(f"  Precisie bij proba>={XGB_CFG['min_proba']}: {metrics['precision_at_threshold']:.3f}  over {metrics['n_signals_at_threshold']} signalen")

    save_model(model, trained_until=str(dataset["Date"].max().date()), n_rows=len(dataset), val_metrics=metrics)
    print(f"\n{'='*60}\nKlaar. Gebruik 'backtest' voor een volledige walk-forward-validatie.")

def run_backtest():
    print(f"{'='*60}\nXGBOOST WALK-FORWARD BACKTEST  {BACKTEST_START} -> {BACKTEST_END}\n{'='*60}")
    
    universum = set()
    for f_name in bouw_bestandslijst():
        universum.update(load_tickers_from_file(f_name))
    universum = sorted(universum)
    if not universum:
        print("[ERROR] Geen ticker bestanden gevonden.")
        return

    hist = download_history(universum, period="10y")
    if hist.empty:
        print("[ERROR] Geen historische data opgehaald.")
        return

    dataset = build_dataset(hist)
    dataset = dataset[dataset["Date"] >= pd.Timestamp(BACKTEST_START) - pd.Timedelta(days=XGB_CFG["is_window"] * 2)]
    if dataset.empty:
        print("[ERROR] Onvoldoende data voor backtestvenster.")
        return

    alle_datums = sorted(dataset["Date"].unique())
    is_window   = XGB_CFG["is_window"]
    rescan_days = XGB_CFG["rescan_days"]
    horizon     = XGB_CFG["horizon_days"]

    start_idx = next((i for i, d in enumerate(alle_datums) if d >= pd.Timestamp(BACKTEST_START)), None)
    if start_idx is None or start_idx < is_window:
        start_idx = is_window
    if start_idx >= len(alle_datums):
        print("[ERROR] Backtestperiode te kort.")
        return

    kapitaal = START_CAPITAL
    equity_curve, alle_trades, baseline_returns, random_returns, fold_metrics = [], [], [], [], []

    idx = start_idx
    model = None
    while idx < len(alle_datums) - horizon:
        train_start = alle_datums[max(0, idx - is_window)]
        train_end   = alle_datums[idx]
        train_fold = dataset[(dataset["Date"] >= train_start) & (dataset["Date"] < train_end)].dropna(subset=FEATURE_COLUMNS + ["label"])

        fold_auc = None
        if len(train_fold) >= XGB_CFG["min_train_rows"]:
            split_i = int(len(train_fold) * 0.85)
            tf = train_fold.sort_values("Date")
            val_fold = tf.iloc[split_i:]
            fit_fold = tf.iloc[:split_i]
            model = fit_model(fit_fold, val_fold if len(val_fold) > 20 else None)
            if len(val_fold) > 20 and val_fold["label"].nunique() == 2:
                proba_v = model.predict_proba(val_fold[FEATURE_COLUMNS])[:, 1]
                fold_auc = float(roc_auc_score(val_fold["label"], proba_v))

        test_end_idx = min(idx + rescan_days, len(alle_datums) - horizon - 1)

        if model is not None and kapitaal > 0:
            periode_datum = alle_datums[idx]
            dagrijen = dataset[(dataset["Date"] == periode_datum)].dropna(subset=FEATURE_COLUMNS)
            if not dagrijen.empty:
                proba = model.predict_proba(dagrijen[FEATURE_COLUMNS])[:, 1]
                dagrijen = dagrijen.assign(proba=proba)
                kandidaten = dagrijen[dagrijen["proba"] >= XGB_CFG["min_proba"]].sort_values("proba", ascending=False)
                top = kandidaten.head(XGB_CFG["top_n_per_beurs"])
                if not top.empty:
                    bedrag_per_positie = kapitaal / len(top)
                    for _, row in top.iterrows():
                        exit_rij = dataset[(dataset["Ticker"] == row["Ticker"]) & (dataset["Date"] > periode_datum)].sort_values("Date")
                        if len(exit_rij) < horizon: continue
                        exit_row = exit_rij.iloc[horizon - 1]
                        aantal = bedrag_per_positie / row["Close"]
                        bruto_pnl = aantal * (exit_row["Close"] - row["Close"])
                        kosten = trade_cost(bedrag_per_positie) + trade_cost(aantal * exit_row["Close"])
                        netto_voor_belasting = bruto_pnl - kosten
                        if netto_voor_belasting > 0: netto_voor_belasting *= (1 - TAX_RATE)
                        kapitaal += netto_voor_belasting
                        alle_trades.append({"datum": periode_datum, "ticker": row["Ticker"], "pnl": netto_voor_belasting})

        periode_data = dataset[(dataset["Date"] >= alle_datums[idx]) & (dataset["Date"] < alle_datums[test_end_idx])]
        if not periode_data.empty:
            gem_fwd = periode_data["fwd_return_pct"].mean()
            if not math.isnan(gem_fwd): baseline_returns.append(gem_fwd)
            steekproef = periode_data.sample(min(XGB_CFG["top_n_per_beurs"], len(periode_data)), random_state=idx)
            rnd_fwd = steekproef["fwd_return_pct"].mean()
            if not math.isnan(rnd_fwd): random_returns.append(rnd_fwd)

        if fold_auc is not None:
            fold_metrics.append({"periode_start": str(alle_datums[idx].date()), "auc": fold_auc, "n_train": len(train_fold)})

        equity_curve.append({"date": alle_datums[test_end_idx - 1] if test_end_idx > 0 else alle_datums[idx], "kapitaal": kapitaal})
        if kapitaal <= 0:
            print(f"[STOP] Kapitaal uitgeput op {alle_datums[idx].date()} — backtest gestopt.")
            break

        idx = test_end_idx if test_end_idx > idx else idx + rescan_days

    print(f"\n{'='*60}\n📊 BACKTEST RESULTATEN — bot_01xgboost (walk-forward)\n{'='*60}")
    if not alle_trades:
        print("Geen enkele trade uitgevoerd.")
        return

    eq_series = pd.Series([e["kapitaal"] for e in equity_curve], index=[e["date"] for e in equity_curve])
    eq_returns = eq_series.pct_change().dropna()
    sharpe = 0.0
    if len(eq_returns) > 1 and eq_returns.std() > 0:
        periodes_per_jaar = 252 / rescan_days
        sharpe = (eq_returns.mean() / eq_returns.std()) * math.sqrt(periodes_per_jaar)
    cum_max = eq_series.cummax()
    drawdown = (eq_series - cum_max) / cum_max
    max_dd_pct = drawdown.min() * 100

    print(f"Periode:              {alle_datums[start_idx].date()} -> {alle_datums[-1].date()}")
    print(f"Aantal trades:        {len(alle_trades)}")
    print(f"Eindkapitaal:         EUR {kapitaal:,.2f}")
    print(f"Totaal rendement:     {(kapitaal-START_CAPITAL)/START_CAPITAL*100:+.1f}%")
    print(f"Sharpe (per periode): {sharpe:.2f}")
    print(f"Max drawdown:         {max_dd_pct:.1f}%")

    if fold_metrics:
        gem_auc = np.mean([f["auc"] for f in fold_metrics])
        print(f"\nGemiddelde out-of-sample AUC over {len(fold_metrics)} walk-forward folds: {gem_auc:.3f}")

def run_live_engine():
    print(f"{'='*60}\nXGBOOST RICHTINGSVOORSPELLING — LIVE  {today_str()}\n{'='*60}")

    model, meta = load_model()
    if model is None:
        print(f"[ERROR] Geen opgeslagen model gevonden ({MODEL_PATH}). Voer eerst 'python bot_01xgboost.py train' uit.")
        return
    print(f"Model geladen — getraind op {meta.get('trained_at')}, data tot {meta.get('trained_until')}, AUC {meta.get('val_metrics', {}).get('auc', '?')}")

    exchange_tickers: Dict[str, List[str]] = {}
    for f_name in bouw_bestandslijst():
        tlist = load_tickers_from_file(f_name)
        if not tlist: continue
        exchange_tickers[label_voor(f_name)] = tlist

    if not exchange_tickers:
        print("[ERROR] Geen ticker bestanden gevonden.")
        return

    email_delen: List[str] = []

    for ex_name, tlist in exchange_tickers.items():
        print(f"\nAnalyseren: {ex_name} ({len(tlist)} tickers)...")
        hist = download_history(tlist, period="2y")
        if hist.empty:
            print("  → geen data, overslaan")
            continue

        signalen = []
        ds = build_dataset(hist)
        if not ds.empty:
            laatste_per_ticker = ds.sort_values("Date").groupby("Ticker").tail(1).dropna(subset=FEATURE_COLUMNS)
            if not laatste_per_ticker.empty:
                proba_arr = model.predict_proba(laatste_per_ticker[FEATURE_COLUMNS])[:, 1]
                laatste_per_ticker = laatste_per_ticker.assign(proba=proba_arr)
                for _, row in laatste_per_ticker.iterrows():
                    proba = float(row["proba"])
                    if proba >= XGB_CFG["min_proba"]:
                        signalen.append({
                            "ticker": row["Ticker"], "price": safe_float(row["Close"]), "proba": proba,
                            "rsi": safe_float(row["rsi"]), "dist_sma200": safe_float(row["dist_sma200_pct"]),
                        })

        signalen.sort(key=lambda s: s["proba"], reverse=True)
        top = signalen[:XGB_CFG["top_n_per_beurs"]]
        print(f"  → {len(top)} signalen (proba>={XGB_CFG['min_proba']:.0%}) uit {len(laatste_per_ticker)} geanalyseerd")

        for rank, s in enumerate(top, start=1):
            log_selectie(
                ticker=s["ticker"], datum=today_str(), strategie="bot_01xgboost", beurs=ex_name, koers=s["price"],
                parameters={
                    "rank": rank, "proba": round(s["proba"], 4),
                    "rsi": round(s["rsi"], 1), "dist_sma200_pct": round(s["dist_sma200"], 2),
                    "horizon_days": XGB_CFG["horizon_days"], "grafiek": yahoo_link(s["ticker"]),
                },
            )

        if top:
            bericht = f"🤖 *XGBOOST — {ex_name}*\n"
            for s in top:
                bericht += f"• `{s['ticker']}` proba:{s['proba']*100:.0f}% | EUR{s['price']:.2f} | RSI:{s['rsi']:.0f} | {yahoo_link(s['ticker'])}\n"
            send_telegram(bericht, parse_mode="Markdown")
            email_delen.append(bericht)

    if email_delen:
        send_email(f"XGBoost rapport {today_str()}", "\n\n" + ("=" * 40 + "\n\n").join(email_delen))

    print(f"\n{'='*60}\nKlaar.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "train":
        run_train()
    elif mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
