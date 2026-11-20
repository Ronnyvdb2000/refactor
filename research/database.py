"""Read-only adapter for an adjusted-close USD selection basket."""
from datetime import date, timedelta
import os

from .signals import SelectionSignal

MARKET = "048 Nasdaq/NYSE"
STRATEGY = "bot_combi_volatiel"


def basket_from_rows(rows, *, today, max_age_days=7):
    if not 1 <= max_age_days <= 30:
        raise ValueError("max_age_days moet tussen 1 en 30 liggen")
    if not rows:
        raise ValueError("Geen selecties beschikbaar voor het USD-mandje")
    latest = max(date.fromisoformat(str(r['datum'])) for r in rows)
    if not today - timedelta(days=max_age_days) <= latest <= today:
        raise ValueError("Recentste selectiedatum ontbreekt, is toekomstig of is te oud")
    chosen = {}
    for r in sorted(rows, key=lambda r: int(r['id']), reverse=True):
        if date.fromisoformat(str(r['datum'])) != latest:
            continue
        if r['beurs'] != MARKET or r['strategie'] != STRATEGY:
            raise ValueError("Onverwachte markt of strategie")
        ticker = r['ticker']
        if ticker in chosen:
            continue
        params = r.get('parameters') or {}
        if not isinstance(params, dict):
            raise ValueError("Ongeldige signaalparameters")
        if params.get('price_basis') != 'yahoo_auto_adjusted_close':
            raise ValueError("Onbekende prijseenheid; geen allocatie berekend")
        if str(params.get('signal_date')) != latest.isoformat():
            raise ValueError("Koersdatum en signaaldatum verschillen")
        if params.get('currency', 'USD') != 'USD':
            raise ValueError("Selectie heeft geen USD-koers")
        s = SelectionSignal(ticker=ticker, strategy=STRATEGY,
                            signal_date=latest.isoformat(), reference_price=float(r['koers']),
                            atr_pct=float(params['atr_pct']), score=float(params['gewogen_score']))
        chosen[ticker] = s
    if not chosen:
        raise ValueError("Geen bruikbare signalen op de recentste selectiedatum")
    return {"currency": "USD", "data_provenance": {
        "source": "Supabase selecties", "market": MARKET, "strategy": STRATEGY,
        "signal_date": latest.isoformat(), "price_basis": "yahoo_auto_adjusted_close",
        "currency_basis": "USD market convention for 048 Nasdaq/NYSE; no FX conversion",
        "selection_ids": sorted(int(r['id']) for r in rows if date.fromisoformat(str(r['datum'])) == latest),
    }, "signals": [dict(ticker=s.ticker, strategy=s.strategy, signal_date=s.signal_date,
                        reference_price=s.reference_price, atr_pct=s.atr_pct, score=s.score)
                    for s in sorted(chosen.values(), key=lambda s: s.ticker)]}


def load_basket(*, today=None, max_age_days=7):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    today = today or date.today()
    if not 1 <= max_age_days <= 30:
        raise ValueError("max_age_days moet tussen 1 en 30 liggen")
    url = os.environ.get('SUPABASE_DB_URL')
    if not url:
        raise RuntimeError('SUPABASE_DB_URL ontbreekt')
    try:
        conn = psycopg2.connect(url, connect_timeout=15)
        try:
            conn.set_session(readonly=True, autocommit=False)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET LOCAL statement_timeout = '30s'")
                cur.execute("""SELECT id,ticker,beurs,strategie,datum,koers,parameters
                    FROM selecties WHERE beurs=%s AND strategie=%s
                    AND datum=(SELECT MAX(datum) FROM selecties
                               WHERE beurs=%s AND strategie=%s AND datum<=%s)
                    ORDER BY id DESC LIMIT 1001""",
                    (MARKET, STRATEGY, MARKET, STRATEGY, today.isoformat()))
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:
        code = getattr(exc, 'pgcode', None)
        safe_code = code if isinstance(code, str) and len(code) == 5 and code.isalnum() else 'connection'
        raise RuntimeError(f'Supabase lezen mislukt (code {safe_code}); controleer verbinding en tabelrechten') from None
    if len(rows) > 1000:
        raise ValueError('Meer dan 1000 rijen; mandje wordt niet stilzwijgend afgekapt')
    return basket_from_rows(rows, today=today, max_age_days=max_age_days)
