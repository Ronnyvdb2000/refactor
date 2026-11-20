#!/usr/bin/env python3
"""Vergelijk aangepaste en ruwe Yahoo-koersen; alleen op expliciete uitvoering.

Gebruik: python -m maintenance.diag_koersen MOL.WA AAPL --start 2026-08-01
"""
import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tickers', nargs='*', default=['MOL.WA', 'AAPL'])
    parser.add_argument('--start', default='2026-08-01', help='Begindatum (YYYY-MM-DD)')
    args = parser.parse_args(argv)
    from datetime import date
    date.fromisoformat(args.start)
    import pandas as pd
    import yfinance as yf

    for t in args.tickers:
        print("=" * 78)
        print(t)
        tk = yf.Ticker(t)
        h_adj = tk.history(start=args.start, auto_adjust=True)
        h_raw = tk.history(start=args.start, auto_adjust=False)

        df = pd.DataFrame({
            "close_adjust": h_adj["Close"],
            "close_raw": h_raw["Close"],
            "dividend": h_raw["Dividends"],
        })
        df.index = pd.to_datetime(df.index).tz_localize(None)

        print(df.tail(40).to_string())
        print()
        print("rijen:", len(df))
        print("NaN  close_adjust:", int(df["close_adjust"].isna().sum()),
              "| close_raw:", int(df["close_raw"].isna().sum()))
        print("<= 0 close_adjust:", int((df["close_adjust"] <= 0).sum()),
              "| close_raw:", int((df["close_raw"] <= 0).sum()))
        print("dividenden in venster:")
        print(df[df["dividend"] > 0][["close_raw", "dividend"]].to_string())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
