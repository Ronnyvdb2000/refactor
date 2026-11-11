

    closes = hist["Close"]
    closes.index = pd.to_datetime(closes.index).tz_localize(None)
    datums = closes.index

    splits = None
    if "Stock Splits" in hist.columns:
        sp = hist["Stock Splits"].copy()
        sp.index = pd.to_datetime(sp.index).tz_localize(None)
        splits = sp[sp > 0]

    n_split_gecorr = 0
    n_eenheid_gecorr = 0
    resultaten = []
    for r in rijen:
        try:
            doel = pd.Timestamp(r["datum"])
            pos_kandidaten = datums.searchsorted(doel)
            if pos_kandidaten >= len(datums):
                continue
            entry_koers = float(r["koers"])
            if not math.isfinite(entry_koers) or entry_koers <= 0:
                continue

            pos_vorige = datums.searchsorted(doel, side="right") - 1
            prijsdatum = datums[pos_vorige] if pos_vorige >= 0 else doel
            factor = split_factor_na(splits, prijsdatum)
            if not math.isfinite(factor) or factor <= 0:
                waarschuw(f"{ticker}: ongeldige splitfactor {factor}")
                continue
            entry_adj = entry_koers / factor
            if factor != 1.0:
                n_split_gecorr += 1

            rij = {
                "ticker": ticker, "datum": r["datum"],
                "strategie": r["strategie"], "beurs": r["beurs"],
                "entry_koers": entry_koers,
            }
            for h in HORIZONS:
                idx = pos_kandidaten + h
                if idx < len(closes):
                    fwd_close = float(closes.iloc[idx])
                    if not math.isfinite(fwd_close) or fwd_close <= 0:
                        rij[f"fwd_close_{h}d"] = None
                        rij[f"fwd_ret_{h}d"] = None
                        continue
                    fwd_close, hersteld = corrigeer_eenheid(fwd_close, entry_adj)
                    if hersteld:
                        n_eenheid_gecorr += 1
                    rij[f"fwd_close_{h}d"] = fwd_close
                    rij[f"fwd_ret_{h}d"] = round((fwd_close / entry_adj - 1) * 100, 3)
                else:
                    rij[f"fwd_close_{h}d"] = None
                    rij[f"fwd_ret_{h}d"] = None
            resultaten.append(rij)
        except Exception as e:
            waarschuw(f"{ticker} {r['datum']}/{r['strategie']}: {e}")
            continue

    if n_split_gecorr or n_eenheid_gecorr:
        print(
            f"  [INFO] {ticker}: {n_split_gecorr} rijen split-gecorrigeerd, "
            f"{n_eenheid_gecorr} koersen eenheid-gecorrigeerd"
        )
    return resultaten


def run_build():
    if not SUPABASE_DB_URL:
        print("FOUT: SUPABASE_DB_URL ontbreekt.", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(SUPABASE_DB_URL)
    try:
        open_rijen = haal_openstaande_rijen(conn)
        print(f"{len(open_rijen)} (ticker, datum, strategie)-rijen wachten op een (bijgewerkt) label.")

        per_ticker: Dict[str, List[dict]] = {}
        for r in open_rijen:
            per_ticker.setdefault(r["ticker"], []).append(r)

        oudste_datum_per_ticker = {t: min(r["datum"] for r in rijen) for t, rijen in per_ticker.items()}
        alle_tickers_gesorteerd = sorted(per_ticker.keys(), key=lambda t: oudste_datum_per_ticker[t])
        tickers = alle_tickers_gesorteerd[:MAX_TICKERS_PER_RUN]
        overgeslagen = len(per_ticker) - len(tickers)
        print(
            f"{len(tickers)} unieke tickers te verwerken dit run"
            + (f" ({overgeslagen} tickers volgen in een volgend run)" if overgeslagen > 0 else "")
        )

        totaal_bijgewerkt = 0
        totaal_compleet = 0
        for i, ticker in enumerate(tickers, start=1):
            labels = bereken_labels_voor_ticker(ticker, per_ticker[ticker])
            aantal = upsert_labels(conn, labels)
            totaal_bijgewerkt += aantal
            totaal_compleet += sum(
                1 for l in labels if l.get(f"fwd_ret_{HORIZONS[-1]}d") is not None
            )
            if i % 25 == 0 or i == len(tickers):
                print(f"  {i}/{len(tickers)} tickers verwerkt...")
            time.sleep(0.1)

        nog_wachtend = len(open_rijen) - totaal_bijgewerkt
        lijnen = [f"📊 <b>Forward-Returns Feature Store — {today_str()}</b>", ""]
        if not open_rijen:
            lijnen.append("✅ Niets te doen: geen openstaande rijen om te labelen.")
        else:
            lijnen.append(f"🔄 <b>{totaal_bijgewerkt}</b> rijen bijgewerkt in forward_returns")
            lijnen.append(f"🏁 {totaal_compleet} daarvan nu volledig")
            lijnen.append(f"📥 {len(open_rijen)} rijen waren openstaand, {nog_wachtend} blijven nog wachten")
            lijnen.append(f"🧾 {len(tickers)} unieke tickers verwerkt dit run")

        if WAARSCHUWINGEN:
            lijnen.append("")
            lijnen.append(f"⚠️ <b>{len(WAARSCHUWINGEN)} waarschuwing(en)</b>")
            for w in WAARSCHUWINGEN[:MAX_WAARSCHUWINGEN_IN_BERICHT]:
                lijnen.append(f"• {esc(w)}")

        bericht = "\n".join(lijnen)
        send_telegram(bericht, parse_mode="HTML")
        send_email(f"Forward-Returns Store {today_str()}", bericht, html_body=True)
        print(f"\nKlaar. {totaal_bijgewerkt} rijen bijgewerkt.")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        run_build()
    except Exception:
        fout = traceback.format_exc()
        print(fout, file=sys.stderr)
        send_telegram(
            f"❌ Forward-Returns FOUT:\n<pre>{esc(fout[-1500:])}</pre>",
            parse_mode="HTML",
        )
        send_email(
            f"❌ Forward-Returns FOUT — {today_str()}",
            f"<pre>{esc(fout)}</pre>", html_body=True,
        )
        raise

