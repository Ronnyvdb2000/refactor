"""Databron-helpers: download, ticker-laden, bestandslijsten.

Eén implementatie van `download_history` en `_normalise`, gebruikt door
alle bots. De per-ticker fallback bij batch-falen blijft behouden.
"""
from __future__ import annotations
import os
import time
from typing import List, Optional
import numpy as np
import pandas as pd
import yfinance as yf

BEURS_NAMEN = {
    "tickers_041x.txt": "041 Benelux Ierland",
    "tickers_042x.txt": "042 Parijs",
    "tickers_043x.txt": "043 Frankfurt",
    "tickers_044x.txt": "044 Spanje/Portugal",
    "tickers_045x.txt": "045 Londen",
    "tickers_046x.txt": "046 Milaan",
    "tickers_047x.txt": "047 Toronto",
    "tickers_048x.txt": "048 Nasdaq/NYSE",
    "tickers_049x.txt": "049 Stockholm",
    "tickers_050x.txt": "050 Zurich",
    "tickers_051x.txt": "051 Warschau",
    "tickers_052x.txt": "052 Oslo",
    "tickers_053x.txt": "053 Kopenhagen",
    "tickers_054x.txt": "054 Helsinki",
    "tickers_055x.txt": "055 CBoe",
    "tickers_056x.txt": "056 NYSE int",
    "tickers_057x.txt": "057 NYSE",
    "tickers_058x.txt": "058 TSXV",
    "tickers_059x.txt": "059 Oostenrijk Slovenie Slovakije",
}


def bouw_bestandslijst(suffix: str = "x", start: int = 41, eind: int = 59) -> List[str]:
    return [f"tickers_{n:03d}{suffix}.txt" for n in range(start, eind + 1)]


def label_voor(f_name: str) -> str:
    getal = f_name.replace("tickers_", "")[:3]
    return BEURS_NAMEN.get(f"tickers_{getal}x.txt", f_name.replace(".txt", ""))


def load_tickers_from_file(path: str) -> List[str]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().replace(";", ",").replace(",", "\n").replace("$", "")
    result = []
    for line in raw.splitlines():
        t = line.strip().upper()
        if t and not t.startswith("#"):
            result.append(t)
    return sorted(set(result))


def alle_tickers(suffix: str = "x") -> List[str]:
    out = set()
    for f in bouw_bestandslijst(suffix):
        out.update(load_tickers_from_file(f))
    return sorted(out)


def _normalise(df_raw, ticker: str) -> Optional[pd.DataFrame]:
    if df_raw is None or not isinstance(df_raw, pd.DataFrame) or df_raw.empty:
        return None
    df = df_raw.copy().dropna(how="all")
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        # Ook de per-ticker download kan MultiIndex-kolommen teruggeven.
        for level in range(df.columns.nlevels):
            if "Close" in df.columns.get_level_values(level):
                df.columns = df.columns.get_level_values(level)
                break
    df = df.loc[:, ~df.columns.duplicated()]
    if "Date" not in df.columns and "Datetime" not in df.columns and (
        df.index.name in ("Date", "Datetime") or isinstance(df.index, pd.DatetimeIndex)
    ):
        df.index.name = "Date"
        df = df.reset_index()
    if "Datetime" in df.columns and "Date" not in df.columns:
        df = df.rename(columns={"Datetime": "Date"})
    if "Close" not in df.columns or "Date" not in df.columns:
        return None

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    if df["Date"].dt.tz is not None:
        # Behoud de handelsdatum; converteer lokale middernacht niet naar UTC.
        df["Date"] = df["Date"].dt.tz_localize(None)
    for col in ("Open", "High", "Low", "Close", "Adj Close", "Volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    # Batchdownloads delen een kalender. Een ontbrekende slotkoers mag nooit
    # als de laatste koers van een ticker bij de indicatoren terechtkomen.
    valid = df["Date"].notna() & df["Close"].notna() & (df["Close"] > 0)
    df = df.loc[valid].sort_values("Date").drop_duplicates("Date", keep="last").copy()
    if df.empty:
        return None
    df["Ticker"] = ticker
    return df


def download_history(tickers: List[str], period: str = "2y",
                     start: Optional[str] = None, end: Optional[str] = None,
                     auto_adjust: bool = True) -> pd.DataFrame:
    """Download OHLCV voor een lijst tickers. Valt terug op per-ticker
    downloads als de batch faalt. Retourneert een lange DataFrame met
    kolom 'Ticker'."""
    if not tickers:
        return pd.DataFrame()
    kwargs = dict(tickers=tickers, auto_adjust=auto_adjust,
                  group_by="ticker", progress=False, threads=True)
    if start and end:
        kwargs.update({"start": start, "end": end})
    else:
        kwargs["period"] = period

    frames = []
    try:
        data = yf.download(**kwargs)
    except Exception as e:
        print(f"[WARN] Batch mislukt ({e}), probeer 1-voor-1...")
        data = pd.DataFrame()

    if data is not None and not data.empty:
        if isinstance(data.columns, pd.MultiIndex):
            ticker_level = 1
            for lvl in range(data.columns.nlevels):
                if any(t in set(data.columns.get_level_values(lvl)) for t in tickers):
                    ticker_level = lvl
                    break
            available = set(data.columns.get_level_values(ticker_level))
            for t in tickers:
                if t not in available:
                    continue
                try:
                    norm = _normalise(data.xs(t, axis=1, level=ticker_level).copy(), t)
                    if norm is not None:
                        frames.append(norm)
                except Exception:
                    pass
        elif len(tickers) == 1:
            norm = _normalise(data, tickers[0])
            if norm is not None:
                frames.append(norm)

    # Herstel ook een gedeeltelijk geslaagde batch, niet alleen totaal falen.
    loaded = {frame["Ticker"].iloc[0] for frame in frames}
    missing = sorted(set(tickers) - loaded)
    if missing:
        print(f"[INFO] {len(missing)} tickers zonder bruikbare batchdata; probeer afzonderlijk.")
        for t in missing:
            try:
                kw = dict(tickers=t, auto_adjust=auto_adjust, progress=False, threads=False)
                if start and end:
                    kw.update({"start": start, "end": end})
                else:
                    kw["period"] = period
                raw = yf.download(**kw)
                if raw is None or (isinstance(raw, pd.DataFrame) and raw.empty):
                    continue
                norm = _normalise(raw, t)
                if norm is not None:
                    frames.append(norm)
                time.sleep(0.2)
            except Exception as e:
                print(f"[WARN] {t}: mislukt ({e})")

    loaded = {frame["Ticker"].iloc[0] for frame in frames}
    missing = sorted(set(tickers) - loaded)
    if missing:
        print(f"[WARN] Geen bruikbare koersdata voor {len(missing)} tickers: {', '.join(missing)}")
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"])
    df.sort_values(["Ticker", "Date"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

