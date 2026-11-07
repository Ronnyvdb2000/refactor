#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_01xgboostMeta.py — XGBOOST FOREX (Twelve Data) v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Voorspelt de kans op een beweging > drempel voor forex-paren.
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
import requests
import xgboost as xgb
from sklearn.metrics import roc_auc_score, accuracy_score, precision_score

from common import (
    today_str, safe_float, yahoo_link,
    compute_rsi_series, compute_macd_series, compute_atr_series,
    send_telegram, send_email,
    trade_cost, SLIPPAGE_PCT
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

FX_START_CAPITAL = 50_000.0
SPREAD_PCT_MAJOR  = 0.0006
SPREAD_PCT_KRUIS  = 0.0015
TAX_RATE          = 0.0

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "")
TWELVE_DATA_URL     = "https://api.twelvedata.com/time_series"
TWELVE_DATA_SLEEP_SEC = 8.0

FOREX_GROEPEN: Dict[str, List[str]] = {
    "Majors": ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD", "AUD/USD", "NZD/USD"],
    "Kruispaaren": ["EUR/GBP", "EUR/JPY", "EUR/CHF", "EUR/AUD", "EUR/CAD", "GBP/JPY", "GBP/CHF", "GBP/AUD", "AUD/JPY", "AUD/NZD", "CAD/JPY", "CHF/JPY", "NZD/JPY"],
}

def alle_paren() -> List[str]:
    out = []
    for lijst in FOREX_GROEPEN.values(): out.extend(lijst)
    return out

def groep_van(paar: str) -> str:
    for naam, lijst in FOREX_GROEPEN.items():
        if paar in lijst: return naam
    return "Overig"

def spread_pct_van(paar: str) -> float:
    return SPREAD_PCT_MAJOR if groep_van(paar) == "Majors" else SPREAD_PCT_KRUIS

XGB_CFG = {
    "rsi_period": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
    "atr_period": 14, "sma_short_period": 50, "sma_trend_period": 200,
    "horizon_days": 10, "label_threshold_pct": 1.0,
    "n_estimators": 400, "max_depth": 4, "learning_rate": 0.05,
    "subsample": 0.8, "colsample_bytree": 0.8, "min_child_weight": 5,
    "early_stopping_rounds": 30,
    "is_window": 504, "rescan_days": 63, "min_train_rows": 500,
    "min_proba": 0.62, "top_n_per_groep": 5,
}

FEATURE_COLUMNS = [
    "rsi", "macd_hist", "macd_hist_slope",
    "dist_sma50_pct", "dist_sma200_pct", "atr_pct",
    "ret_5d", "ret_20d", "ret_60d",
]

MODEL_PATH      = "model_xgboostmeta.json"
MODEL_META_PATH = "model_xgboostmeta_meta.json"

BACKTEST_START = "2019-01-01"
BACKTEST_END   = today_str()

# ============================================================
# DOWNLOAD & THROTTLE
# ============================================================

_laatste_call_tijd: Optional[float] = None

def _throttle_twelve_data() -> None:
    global _laatste_call_tijd
    nu = time.monotonic()
    if _laatste_call_tijd is not None:
        verstreken = nu - _laatste_call_tijd
        if verstreken < TWELVE_DATA_SLEEP_SEC:
            time.sleep(TWELVE_DATA_SLEEP_SEC - verstreken)
    _laatste_call_tijd = time.monotonic()

def download_forex_history(paar: str, outputsize: int = 5000, max_retries: int = 2) -> Optional[pd.DataFrame]:
    if not TWELVE_DATA_API_KEY:
        print("[ERROR] TWELVE_DATA_API_KEY ontbreekt.")
        return None

    params = {"symbol": paar, "interval": "1day", "outputsize": outputsize, "apikey": TWELVE_DATA_API_KEY, "order": "ASC"}
    for poging in range(max_retries + 1):
        _throttle_twelve_data()
        try:
            resp = requests.get(TWELVE_DATA_URL, params=params, timeout=20)
            data = resp.json()
        except Exception as e:
            print(f"[WARN] {paar}: request mislukt ({e})")
            return None

        if isinstance(data, dict) and data.get("status") == "error":
            bericht = str(data.get("message", ""))
            is_rate_limit = "api credits" in bericht.lower() or "run out" in bericht.lower()
            if is_rate_limit and poging < max_retries:
                print(f"[WARN] {paar}: rate limit bereikt, 65s wachten (poging {poging+1}/{max_retries})...")
                time.sleep(65)
                continue
            print(f"[WARN] {paar}: Twelve Data fout — {bericht}")
            return None

        values = data.get("values") if isinstance(data, dict) else None
        if not values:
            print(f"[WARN] {paar}: geen data ontvangen")
            return None

        df = pd.DataFrame(values)
        if not {"datetime", "open", "high", "low", "close"}.issubset(df.columns):
            return None

        df["Date"]  = pd.to_datetime(df["datetime"])
        for c in ["open", "high", "low", "close"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
        df["Paar"] = paar
        return df[["Date", "Open", "High", "Low", "Close", "Paar"]].dropna().sort_values("Date").reset_index(drop=True)
    return None

def download_history_alle_paren(paren: List[str], outputsize: int = 5000) -> pd.DataFrame:
    frames = []
    for paar in paren:
        df = download_forex_history(paar, outputsize=outputsize)
        if df is not None and not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)

# ============================================================
# FEATURE ENGINEERING
# ============================================================

def build_features(df_paar: pd.DataFrame) -> pd.DataFrame:
    df = df_paar.set_index("Date")
    close, high, low = df["Close"], df["High"], df["Low"]

    rsi = compute_rsi_series(close, XGB_CFG["rsi_period"])
    _, _, macd_hist = compute_macd_series(close, XGB_CFG["macd_fast"], XGB_CFG["macd_slow"], XGB_CFG["macd_signal"])
    atr = compute_atr_series(high, low, close, XGB_CFG["atr_period"])
    sma_short = close.rolling(XGB_CFG["sma_short_period"]).mean()
    sma_trend = close.rolling(XGB_CFG["sma_trend_period"]).mean()

    feat = pd.DataFrame(index=df.index)
    feat["rsi"]              = rsi
    feat["macd_hist"]        = macd_hist
    feat["macd_hist_slope"]  = macd_hist.diff(3)
    feat["dist_sma50_pct"]   = (close - sma_short) / sma_short * 100.0
    feat["dist_sma200_pct"]  = (close - sma_trend) / sma_trend * 100.0
    feat["atr_pct"]          = atr / close * 100.0
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
    for paar, g in hist.groupby("Paar"):
        g = g.sort_values("Date")
        if len(g) < XGB_CFG["sma_trend_period"] + XGB_CFG["horizon_days"] + 20: continue
        feat = build_features(g).reset_index()
        feat.insert(0, "Paar", paar)
        frames.append(feat)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

# ============================================================
# MODEL TRAIN / LOAD / SAVE
# ============================================================

def _make_model() -> xgb.XGBClassifier:
    return xgb.XGBClassifier(
        n_estimators=XGB_CFG["n_estimators"], max_depth=XGB_CFG["max_depth"],
        learning_rate=XGB_CFG["learning_rate"], subsample=XGB_CFG["subsample"],
        colsample_bytree=XGB_CFG["colsample_bytree"], min_child_weight=XGB_CFG["min_child_weight"],
        objective="binary:logistic", eval_metric="auc",
        early_stopping_rounds=XGB_CFG["early_stopping_rounds"], n_jobs=-1,
    )

def fit_model(train_df: pd.DataFrame, val_df: Optional[pd.DataFrame] = None) -> xgb.XGBClassifier:
    X_train, y_train = train_df[FEATURE_COLUMNS], train_df["label"]
    model = _make_model()
    if val_df is not None and len(val_df) > 0:
        model.fit(X_train, y_train, eval_set=[(val_df[FEATURE_COLUMNS], val_df["label"])], verbose=False)
    else:
        model.set_params(early_stopping_rounds=None)
        model.fit(X_train, y_train, verbose=False)
    return model

def save_model(model, trained_until, n_rows, val_metrics):
    model.save_model(MODEL_PATH)
    meta = {"trained_at": today_str(), "trained_until": trained_until, "n_rows": n_rows, "feature_columns": FEATURE_COLUMNS, "config": XGB_CFG, "val_metrics": val_metrics}
    with open(MODEL_META_PATH, "w", encoding="utf-8") as f: json.dump(meta, f, indent=2)

def load_model():
    if not os.path.exists(MODEL_PATH) or not os.path.exists(MODEL_META_PATH): return None, None
    model = xgb.XGBClassifier()
    model.load_model(MODEL_PATH)
    with open(MODEL_META_PATH, "r", encoding="utf-8") as f: meta = json.load(f)
    return model, meta

# ============================================================
# MODES: TRAIN / BACKTEST / LIVE
# ============================================================

def run_train():
    print(f"{'='*60}\nXGBOOSTMETA (FOREX) TRAINING  {today_str()}\n{'='*60}")
    paren = alle_paren()
    print(f"Universum: {len(paren)} forex-paren")
    hist = download_history_alle_paren(paren, outputsize=5000)
    if hist.empty: return
    print(f"Historische data: {len(hist)} rijen over {hist['Paar'].nunique()} paren")

    dataset = build_dataset(hist).dropna(subset=FEATURE_COLUMNS + ["label"])
    if len(dataset) < XGB_CFG["min_train_rows"]: return
    dataset = dataset.sort_values("Date")
    print(f"Trainingsdataset: {len(dataset)} rijen | Klassebalans: {dataset['label'].mean()*100:.1f}% positief")

    split_idx = int(len(dataset) * 0.85)
    split_date = dataset.iloc[split_idx]["Date"]
    train_df, val_df = dataset[dataset["Date"] < split_date], dataset[dataset["Date"] >= split_date]
    print(f"Train: {len(train_df)} rijen tot {split_date.date()} | Validatie: {len(val_df)} rijen erna")

    model = fit_model(train_df, val_df)
    val_proba = model.predict_proba(val_df[FEATURE_COLUMNS])[:, 1]
    val_pred  = (val_proba >= XGB_CFG["min_proba"]).astype(int)
    metrics = {"auc": float(roc_auc_score(val_df["label"], val_proba)), "precision": float(precision_score(val_df["label"], val_pred, zero_division=0))}
    print(f"Validatie AUC: {metrics['auc']:.3f} | Precisie: {metrics['precision']:.3f}")

    save_model(model, trained_until=str(dataset["Date"].max().date()), n_rows=len(dataset), val_metrics=metrics)

def run_backtest():
    print(f"{'='*60}\nXGBOOSTMETA (FOREX) BACKTEST  {BACKTEST_START} -> {BACKTEST_END}\n{'='*60}")
    paren = alle_paren()
    hist = download_history_alle_paren(paren, outputsize=5000)
    if hist.empty: return

    dataset = build_dataset(hist)
    dataset = dataset[dataset["Date"] >= pd.Timestamp(BACKTEST_START) - pd.Timedelta(days=XGB_CFG["is_window"] * 2)]
    if dataset.empty: return

    alle_datums = sorted(dataset["Date"].unique())
    is_window, rescan_days, horizon = XGB_CFG["is_window"], XGB_CFG["rescan_days"], XGB_CFG["horizon_days"]
    
    start_idx = next((i for i, d in enumerate(alle_datums) if d >= pd.Timestamp(BACKTEST_START)), None)
    if start_idx is None or start_idx < is_window: start_idx = is_window
    if start_idx >= len(alle_datums): return

    kapitaal, alle_trades, fold_metrics = FX_START_CAPITAL, [], []
    idx, model = start_idx, None

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
                top = dagrijen[dagrijen["proba"] >= XGB_CFG["min_proba"]].sort_values("proba", ascending=False).head(XGB_CFG["top_n_per_groep"] * len(FOREX_GROEPEN))
                if not top.empty:
                    bedrag = kapitaal / len(top)
                    for _, row in top.iterrows():
                        exit_rij = dataset[(dataset["Paar"] == row["Paar"]) & (dataset["Date"] > periode_datum)].sort_values("Date")
                        if len(exit_rij) < horizon: continue
                        exit_row = exit_rij.iloc[horizon - 1]
                        aantal = bedrag / row["Close"]
                        bruto_pnl = aantal * (exit_row["Close"] - row["Close"])
                        kosten = bedrag * spread_pct_van(row["Paar"])
                        netto = bruto_pnl - kosten
                        if netto > 0: netto *= (1 - TAX_RATE)
                        kapitaal += netto
                        alle_trades.append({"datum": periode_datum, "paar": row["Paar"], "pnl": netto})

        if fold_auc is not None:
            fold_metrics.append({"periode_start": str(alle_datums[idx].date()), "auc": fold_auc})
        if kapitaal <= 0:
            print(f"[STOP] Kapitaal uitgeput op {alle_datums[idx].date()}.")
            break
        idx = test_end_idx if test_end_idx > idx else idx + rescan_days

    print(f"\n{'='*60}\n📊 RESULTATEN — bot_01xgboostMeta\n{'='*60}")
    if not alle_trades:
        print("Geen enkele trade uitgevoerd.")
        return
    print(f"Aantal trades: {len(alle_trades)}")
    print(f"Eindkapitaal:  EUR {kapitaal:,.2f}")
    print(f"Rendement:     {(kapitaal-FX_START_CAPITAL)/FX_START_CAPITAL*100:+.1f}%")
    if fold_metrics:
        print(f"Gem. out-of-sample AUC: {np.mean([f['auc'] for f in fold_metrics]):.3f}")

def run_live_engine():
    print(f"{'='*60}\nXGBOOSTMETA (FOREX) — LIVE  {today_str()}\n{'='*60}")
    model, meta = load_model()
    if model is None:
        print(f"[ERROR] Geen model gevonden ({MODEL_PATH}). Voer eerst 'train' uit.")
        return
    print(f"Model: {meta.get('trained_at')} | AUC: {meta.get('val_metrics', {}).get('auc', '?')}")

    for groep, paren in FOREX_GROEPEN.items():
        print(f"\nAnalyseren: {groep} ({len(paren)} paren)...")
        hist = download_history_alle_paren(paren, outputsize=800)
        if hist.empty: continue

        signalen = []
        ds = build_dataset(hist)
        if not ds.empty:
            laatste = ds.sort_values("Date").groupby("Paar").tail(1).dropna(subset=FEATURE_COLUMNS)
            if not laatste.empty:
                proba_arr = model.predict_proba(laatste[FEATURE_COLUMNS])[:, 1]
                laatste = laatste.assign(proba=proba_arr)
                for _, row in laatste.iterrows():
                    proba = float(row["proba"])
                    if proba >= XGB_CFG["min_proba"]:
                        signalen.append({"paar": row["Paar"], "price": safe_float(row["Close"]), "proba": proba, "rsi": safe_float(row["rsi"]), "dist_sma200": safe_float(row["dist_sma200_pct"])})

        signalen.sort(key=lambda s: s["proba"], reverse=True)
        top = signalen[:XGB_CFG["top_n_per_groep"]]
        print(f"  → {len(top)} signalen (proba>={XGB_CFG['min_proba']:.0%}) uit {len(laatste)} paren")

        for rank, s in enumerate(top, start=1):
            log_selectie(ticker=s["paar"], datum=today_str(), strategie="bot_01xgboostMeta", beurs=groep, koers=s["price"],
                parameters={"rank": rank, "proba": round(s["proba"], 4), "rsi": round(s["rsi"], 1), "dist_sma200_pct": round(s["dist_sma200"], 2), "horizon_days": XGB_CFG["horizon_days"]})

        if top:
            bericht = f"🤖 *XGBOOST FOREX — {groep}*\n"
            for s in top: bericht += f"• `{s['paar']}` proba:{s['proba']*100:.0f}% | {s['price']:.5f} | RSI:{s['rsi']:.0f}\n"
            send_telegram(bericht, parse_mode="Markdown")

    print(f"\n{'='*60}\nKlaar.")

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "train": run_train()
    elif mode == "backtest": run_backtest()
    else: run_live_engine()
