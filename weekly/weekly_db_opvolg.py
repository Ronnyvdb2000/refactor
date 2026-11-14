"""Weekly databaseopslag met herhaalbare snapshots en ranks op historische peildatum."""

import os
import math
import logging
from decimal import Decimal
from numbers import Number
import psycopg2

logger = logging.getLogger(__name__)

_DB_URL_ENV = "SUPABASE_DB_URL"


def _get_connection():
    db_url = os.environ.get(_DB_URL_ENV, "")
    db_url = db_url.strip().strip('"').strip("'")
    if not db_url:
        raise RuntimeError(
            f"Omgevingsvariabele {_DB_URL_ENV} ontbreekt. "
            f"Voeg ze toe als GitHub Actions secret en geef ze door aan de workflow."
        )
    if not (db_url.startswith("postgresql://") or db_url.startswith("postgres://")):
        raise RuntimeError(
            f"{_DB_URL_ENV} lijkt geen geldige connectiestring: moet beginnen "
            f"met 'postgresql://' of 'postgres://'."
        )
    return psycopg2.connect(db_url, connect_timeout=15)


def _sanitize(value):
    """NaN/Inf zijn geen geldige Postgres NUMERIC-waarden -> NULL."""
    if isinstance(value, (Number, Decimal)) and not math.isfinite(value):
        return None
    return value


def log_weekly_toppers(rows: list) -> int:
    """Bewaar eerste snapshot per ticker/beurs/week; herhaalrun maakt geen nieuw type."""
    if not rows:
        return 0
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                for r in sorted(rows, key=lambda r: (r['ticker'], r.get('beurs') or '', str(r['week_startdatum']))):
                    if not r.get('beurs'):
                        raise ValueError('beurs ontbreekt')
                    key = f"weekly|{r['ticker']}|{r['beurs']}|{r['week_startdatum']}"
                    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (key,))
                    cur.execute("""
                        INSERT INTO weekly_toppers
                          (week_startdatum,lijst,ticker,beurs,type,rang,week_perf)
                        SELECT %s,%s,%s,%s,%s,%s,%s WHERE NOT EXISTS (
                          SELECT 1 FROM weekly_toppers WHERE ticker=%s AND beurs=%s AND week_startdatum=%s)
                        ON CONFLICT (ticker,beurs,week_startdatum,type) DO NOTHING
                    """, (r['week_startdatum'],r['lijst'],r['ticker'],r['beurs'],r['type'],
                          r.get('rang'),_sanitize(r['week_perf']),r['ticker'],r['beurs'],r['week_startdatum']))
        return len(rows)
    finally:
        conn.close()


# Kolommen van weekly_topper_parameters, exclusief de sleutel-/metakolommen
# (week_startdatum, ticker, beurs, datum, dag_index) die apart doorgegeven worden.
_PARAM_KOLOMMEN = [
    "close", "rsi", "macd_hist", "atr_pct", "dist_sma50_pct", "dist_sma200_pct",
    "support", "resistance", "stop",
    "pe_ratio", "forward_pe", "pb_ratio", "ps_ratio", "fcf_yield",
    "dividend_yield", "market_cap",
    "roe_pct", "current_ratio", "revenue_growth_pct", "eps_growth_pct",
    "debt_to_ebitda",
    "piotroski_score", "combined_rank", "roc_rank", "ey_rank",
    "vc2_score", "total_score",
]

_META_KOLOMMEN = ["week_startdatum", "ticker", "beurs", "datum", "dag_index"]


def log_weekly_topper_parameters(rows: list) -> int:
    """Vul alleen ontbrekende velden aan; een fout rolt de parameterbatch terug."""
    if not rows:
        return 0
    kolommen = _META_KOLOMMEN + _PARAM_KOLOMMEN
    updates = ', '.join(f'{k}=COALESCE(weekly_topper_parameters.{k},EXCLUDED.{k})'
                        for k in _PARAM_KOLOMMEN)
    query = f"""INSERT INTO weekly_topper_parameters ({','.join(kolommen)})
                VALUES ({','.join(['%s']*len(kolommen))})
                ON CONFLICT (ticker,beurs,datum) DO UPDATE SET {updates}
                WHERE weekly_topper_parameters.week_startdatum=EXCLUDED.week_startdatum"""
    conn = _get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                for row in rows:
                    if not row.get('beurs'):
                        raise ValueError('beurs ontbreekt')
                    cur.execute(query, [_sanitize(row.get(k)) for k in kolommen])
        return len(rows)
    finally:
        conn.close()


# De 6 rank/score-velden die haal_laatste_rank_scores() elk apart ophaalt.
_RANK_KOLOMMEN = [
    "combined_rank", "roc_rank", "ey_rank", "vc2_score",
    "total_score", "piotroski_score",
]


def haal_laatste_rank_scores(tickers, peildatum, beurs):
    """Laatste rank op dezelfde beurs, maximaal 30 dagen oud, toen reeds opgeslagen."""
    if not tickers:
        return {}
    conn = _get_connection()
    resultaat = {}
    try:
        with conn.cursor() as cur:
            velden = ','.join(f"'{k}',s.{k}" for k in _RANK_KOLOMMEN)
            cur.execute(f"""
                SELECT DISTINCT ON (s.ticker,kv.key) s.ticker,kv.key,(kv.value #>> '{{}}')::numeric
                FROM selecties s CROSS JOIN LATERAL jsonb_each(jsonb_build_object({velden})) kv
                WHERE s.ticker=ANY(%s) AND s.beurs=%s AND kv.value <> 'null'::jsonb
                  AND s.datum ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$'
                  AND s.datum <= %s AND s.datum >= (%s::date-30)::text
                  AND s.created_at < ((%s::date+1)::timestamp AT TIME ZONE 'UTC')
                ORDER BY s.ticker,kv.key,s.datum DESC,s.created_at DESC,s.id DESC
            """, (list(tickers), beurs, str(peildatum), str(peildatum), str(peildatum)))
            for ticker, kolom, waarde in cur.fetchall():
                resultaat.setdefault(ticker, {})[kolom] = waarde
    finally:
        conn.close()
    return resultaat
