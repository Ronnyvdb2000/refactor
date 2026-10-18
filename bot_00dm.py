
# ============================================================
# BACKTEST ENGINE
# ============================================================

@dataclass
class BTPosition:
    ticker:      str
    entry_date:  pd.Timestamp
    entry_price: float
    size:        int
    cost:        float
    sl:          Optional[float]
    tp:          Optional[float]
    atr:         float
    days:        int = 0

def run_backtest():
    print(f"{'='*60}")
    print(f"DUAL MOMENTUM BACKTEST  {BACKTEST_START} -> {BACKTEST_END}")
    print(f"NB: omzetgroei backtest gebruikt technische proxy (12m prijsprestatie)")
    print(f"{'='*60}")

    all_tickers: List[str] = []
    for f_name in bouw_bestandslijst():
        all_tickers.extend(load_tickers_from_file(f_name))
    all_tickers = sorted(set(all_tickers))

    if not all_tickers:
        print("[ERROR] Geen tickers gevonden.")
        return

    print(f"Tickers: {len(all_tickers)} | Data downloaden (5y)...")
    df = download_history(all_tickers, period="5y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    all_dates  = sorted(df["Date"].dt.date.unique())
    cash       = START_CAPITAL
    positions: Dict[str, BTPosition] = {}
    trades:    List[Dict]      = []
    price_map: Dict[str, float] = {}

    rs_ratings = compute_rs_ratings(df)
    rs_top20   = top_pct_filter(rs_ratings, DM_CFG["top_pct"])

    # Proxy voor omzetgroei in backtest: 12m prijsprestatie top 20%
    perf_proxy: Dict[str, float] = {}
    for ticker, group in df.groupby("Ticker", sort=False):
        g = group.sort_values("Date")
        if len(g) < 252:
            continue
        c_now  = safe_float(g["Close"].iloc[-1])
        c_12m  = safe_float(g["Close"].iloc[-252])
        if c_12m > 0:
            perf_proxy[ticker] = (c_now - c_12m) / c_12m * 100
    rev_top20_proxy = top_pct_filter(perf_proxy, DM_CFG["top_pct"])

    scan_dates = [d for d in all_dates if d.weekday() == 0]
    print(f"Scanmomenten: {len(scan_dates)} (wekelijks maandag)")

    for scan_date in scan_dates:
        df_hist = df[df["Date"] <= pd.Timestamp(scan_date)].copy()
        day_df  = df[df["Date"] == pd.Timestamp(scan_date)].copy()

        price_map = {}
        for _, row in day_df.iterrows():
            t = row.get("Ticker")
            c = safe_float(row.get("Close"))
            if t and not math.isnan(c):
                price_map[t] = c

        # Exits: slot onder MA50
        for ticker, pos in list(positions.items()):
            pos.days += 1
            if ticker not in price_map:
                continue
            g     = df_hist[df_hist["Ticker"] == ticker].sort_values("Date")
            close = g["Close"]
            ma50  = safe_float(close.rolling(DM_CFG["ma_exit"]).mean().iloc[-1])
            c_now = price_map[ticker]
            reason = None

            if not math.isnan(ma50) and c_now < ma50:
                reason = f"Slot<MA{DM_CFG['ma_exit']} ({c_now:.2f}<{ma50:.2f})"
            elif c_now <= pos.sl:
                reason = f"Hard SL ({pos.sl:.2f})"
            elif pos.days >= MAX_HOLD_DAYS:
                reason = f"Time ({pos.days}d)"

            if reason:
                exit_slip = c_now * (1 - SLIPPAGE_PCT)
                gross     = exit_slip * pos.size
                cost      = trade_cost(gross)
                pnl       = gross - cost - (pos.entry_price * pos.size + pos.cost)
                tax       = pnl * TAX_RATE if pnl > 0 else 0.0
                cash     += gross - cost - tax
                trades.append({
                    "entry_date":  pos.entry_date.isoformat(),
                    "exit_date":   scan_date.isoformat(),
                    "ticker":      ticker,
                    "entry_price": pos.entry_price,
                    "exit_price":  round(exit_slip, 4),
                    "size":        pos.size,
                    "pnl":         round(pnl, 2),
                    "tax":         round(tax, 2),
                    "net":         round(pnl - tax, 2),
                    "reason":      reason,
                    "days":        pos.days,
                })
                del positions[ticker]

        # Entries: pipeline filter + uitbraak
        for ticker, group in df_hist.groupby("Ticker", sort=False):
            if ticker in positions or len(positions) >= MAX_POSITIONS:
                continue
            if ticker not in rs_top20 or ticker not in rev_top20_proxy:
                continue
            sig = analyse_ticker(
                ticker, group, rs_ratings, perf_proxy, rs_top20, rev_top20_proxy
            )
            if not sig or not sig.breakout:
                continue

            entry      = sig.price * (1 + SLIPPAGE_PCT)
            total_waarde = cash + sum(price_map.get(t, p.entry_price) * p.size for t, p in positions.items())
            aandelen, _ = bereken_positie(total_waarde, entry, sig.stop)
            
            if aandelen <= 0:
                continue
            investering = entry * aandelen + trade_cost(entry * aandelen)
            if investering > cash:
                continue

            cash -= investering
            positions[ticker] = BTPosition(
                ticker=ticker, entry_date=scan_date, entry_price=round(entry, 4),
                size=aandelen, cost=trade_cost(investering),
                sl=sig.stop, tp=None,
                atr=0.0, days=0
            )

    if trades:
        tdf = pd.DataFrame(trades)
        tdf.to_csv("dm_backtest_trades.csv", index=False)
        n    = len(tdf)
        nwin = (tdf["net"] > 0).sum()
        pf   = abs(tdf.loc[tdf["net"] > 0, "net"].sum()) / max(abs(tdf.loc[tdf["net"] <= 0, "net"].sum()), 1e-9)
        final_val = cash + sum(price_map.get(t, p.entry_price) * p.size for t, p in positions.items())
        print(f"\n{'='*60}")
        print(f"Startkapitaal    : EUR{START_CAPITAL:>12,.2f}")
        print(f"Eindkapitaal     : EUR{final_val:>12,.2f}")
        print(f"Totaal rendement : {(final_val-START_CAPITAL)/START_CAPITAL*100:>+.1f}%")
        print(f"Trades           : {n} | Winnaars: {nwin} ({nwin/n*100:.1f}%)")
        print(f"Profit Factor    : {pf:.2f}")
        print(f"Belasting betaald: EUR{tdf['tax'].sum():,.2f}")
        print(f"Gem. houdduur    : {tdf['days'].mean():.1f} dagen")
        print(f"Opgeslagen       : dm_backtest_trades.csv")
        print(f"{'='*60}")
    else:
        print("Geen trades gegenereerd.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
      
# ============================================================
# BACKTEST ENGINE
# ============================================================

@dataclass
class BTPosition:
    ticker:      str
    entry_date:  pd.Timestamp
    entry_price: float
    size:        int
    cost:        float
    sl:          Optional[float]
    tp:          Optional[float]
    atr:         float
    days:        int = 0

def run_backtest():
    print(f"{'='*60}")
    print(f"DUAL MOMENTUM BACKTEST  {BACKTEST_START} -> {BACKTEST_END}")
    print(f"NB: omzetgroei backtest gebruikt technische proxy (12m prijsprestatie)")
    print(f"{'='*60}")

    all_tickers: List[str] = []
    for f_name in bouw_bestandslijst():
        all_tickers.extend(load_tickers_from_file(f_name))
    all_tickers = sorted(set(all_tickers))

    if not all_tickers:
        print("[ERROR] Geen tickers gevonden.")
        return

    print(f"Tickers: {len(all_tickers)} | Data downloaden (5y)...")
    df = download_history(all_tickers, period="5y")
    if df.empty:
        print("[ERROR] Geen data.")
        return

    all_dates  = sorted(df["Date"].dt.date.unique())
    cash       = START_CAPITAL
    positions: Dict[str, BTPosition] = {}
    trades:    List[Dict]      = []
    price_map: Dict[str, float] = {}

    rs_ratings = compute_rs_ratings(df)
    rs_top20   = top_pct_filter(rs_ratings, DM_CFG["top_pct"])

    # Proxy voor omzetgroei in backtest: 12m prijsprestatie top 20%
    perf_proxy: Dict[str, float] = {}
    for ticker, group in df.groupby("Ticker", sort=False):
        g = group.sort_values("Date")
        if len(g) < 252:
            continue
        c_now  = safe_float(g["Close"].iloc[-1])
        c_12m  = safe_float(g["Close"].iloc[-252])
        if c_12m > 0:
            perf_proxy[ticker] = (c_now - c_12m) / c_12m * 100
    rev_top20_proxy = top_pct_filter(perf_proxy, DM_CFG["top_pct"])

    scan_dates = [d for d in all_dates if d.weekday() == 0]
    print(f"Scanmomenten: {len(scan_dates)} (wekelijks maandag)")

    for scan_date in scan_dates:
        df_hist = df[df["Date"] <= pd.Timestamp(scan_date)].copy()
        day_df  = df[df["Date"] == pd.Timestamp(scan_date)].copy()

        price_map = {}
        for _, row in day_df.iterrows():
            t = row.get("Ticker")
            c = safe_float(row.get("Close"))
            if t and not math.isnan(c):
                price_map[t] = c

        # Exits: slot onder MA50
        for ticker, pos in list(positions.items()):
            pos.days += 1
            if ticker not in price_map:
                continue
            g     = df_hist[df_hist["Ticker"] == ticker].sort_values("Date")
            close = g["Close"]
            ma50  = safe_float(close.rolling(DM_CFG["ma_exit"]).mean().iloc[-1])
            c_now = price_map[ticker]
            reason = None

            if not math.isnan(ma50) and c_now < ma50:
                reason = f"Slot<MA{DM_CFG['ma_exit']} ({c_now:.2f}<{ma50:.2f})"
            elif c_now <= pos.sl:
                reason = f"Hard SL ({pos.sl:.2f})"
            elif pos.days >= MAX_HOLD_DAYS:
                reason = f"Time ({pos.days}d)"

            if reason:
                exit_slip = c_now * (1 - SLIPPAGE_PCT)
                gross     = exit_slip * pos.size
                cost      = trade_cost(gross)
                pnl       = gross - cost - (pos.entry_price * pos.size + pos.cost)
                tax       = pnl * TAX_RATE if pnl > 0 else 0.0
                cash     += gross - cost - tax
                trades.append({
                    "entry_date":  pos.entry_date.isoformat(),
                    "exit_date":   scan_date.isoformat(),
                    "ticker":      ticker,
                    "entry_price": pos.entry_price,
                    "exit_price":  round(exit_slip, 4),
                    "size":        pos.size,
                    "pnl":         round(pnl, 2),
                    "tax":         round(tax, 2),
                    "net":         round(pnl - tax, 2),
                    "reason":      reason,
                    "days":        pos.days,
                })
                del positions[ticker]

        # Entries: pipeline filter + uitbraak
        for ticker, group in df_hist.groupby("Ticker", sort=False):
            if ticker in positions or len(positions) >= MAX_POSITIONS:
                continue
            if ticker not in rs_top20 or ticker not in rev_top20_proxy:
                continue
            sig = analyse_ticker(
                ticker, group, rs_ratings, perf_proxy, rs_top20, rev_top20_proxy
            )
            if not sig or not sig.breakout:
                continue

            entry      = sig.price * (1 + SLIPPAGE_PCT)
            total_waarde = cash + sum(price_map.get(t, p.entry_price) * p.size for t, p in positions.items())
            aandelen, _ = bereken_positie(total_waarde, entry, sig.stop)
            
            if aandelen <= 0:
                continue
            investering = entry * aandelen + trade_cost(entry * aandelen)
            if investering > cash:
                continue

            cash -= investering
            positions[ticker] = BTPosition(
                ticker=ticker, entry_date=scan_date, entry_price=round(entry, 4),
                size=aandelen, cost=trade_cost(investering),
                sl=sig.stop, tp=None,
                atr=0.0, days=0
            )

    if trades:
        tdf = pd.DataFrame(trades)
        tdf.to_csv("dm_backtest_trades.csv", index=False)
        n    = len(tdf)
        nwin = (tdf["net"] > 0).sum()
        pf   = abs(tdf.loc[tdf["net"] > 0, "net"].sum()) / max(abs(tdf.loc[tdf["net"] <= 0, "net"].sum()), 1e-9)
        final_val = cash + sum(price_map.get(t, p.entry_price) * p.size for t, p in positions.items())
        print(f"\n{'='*60}")
        print(f"Startkapitaal    : EUR{START_CAPITAL:>12,.2f}")
        print(f"Eindkapitaal     : EUR{final_val:>12,.2f}")
        print(f"Totaal rendement : {(final_val-START_CAPITAL)/START_CAPITAL*100:>+.1f}%")
        print(f"Trades           : {n} | Winnaars: {nwin} ({nwin/n*100:.1f}%)")
        print(f"Profit Factor    : {pf:.2f}")
        print(f"Belasting betaald: EUR{tdf['tax'].sum():,.2f}")
        print(f"Gem. houdduur    : {tdf['days'].mean():.1f} dagen")
        print(f"Opgeslagen       : dm_backtest_trades.csv")
        print(f"{'='*60}")
    else:
        print("Geen trades gegenereerd.")

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "live"
    if mode == "backtest":
        run_backtest()
    else:
        run_live_engine()
