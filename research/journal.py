"""Append-only basket archive; repeat publications keep the first timestamp."""
import hashlib
import json
import os

DDL = '''CREATE TABLE IF NOT EXISTS public.research_basket_log (
    basket_id text PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    source_run text NOT NULL,
    payload jsonb NOT NULL
)'''


def basket_id(result):
    manifest = result['manifest']
    # Code revisions or repeated cron runs do not turn an unchanged basket into
    # another independent observation. Signals contain their source date.
    content = {k: manifest[k] for k in ('currency', 'signals', 'policy')}
    content['positions'] = result['simulation']['positions']
    raw = json.dumps(content, sort_keys=True, allow_nan=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def record(conn, result):
    key = basket_id(result)
    payload = json.dumps(result, allow_nan=False)
    with conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute('''INSERT INTO public.research_basket_log (basket_id,source_run,payload)
            VALUES (%s,%s,%s::jsonb) ON CONFLICT (basket_id) DO NOTHING
            RETURNING recorded_at''', (key, os.getenv('GITHUB_RUN_ID', 'local'), payload))
        inserted = cur.fetchone()
        # A separate statement sees the winner of a concurrent identical insert.
        cur.execute('SELECT recorded_at,payload FROM public.research_basket_log WHERE basket_id=%s', (key,))
        stamp, saved = cur.fetchone()
    return dict(basket_id=key, recorded_at=stamp.isoformat(), new=inserted is not None,
                original_run_id=saved['run_id'])


def archive(result):
    import psycopg2
    conn = None
    try:
        conn = psycopg2.connect(os.environ['SUPABASE_DB_URL'], connect_timeout=20)
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '30s'")
            saved = record(conn, result)
        return saved
    except Exception:
        raise RuntimeError('Mandje niet opgeslagen; controleer databaseverbinding en CREATE/INSERT/SELECT-rechten') from None
    finally:
        if conn is not None:
            conn.close()


def load(conn):
    """Read archived baskets without creating or modifying tables."""
    import psycopg2.extras
    conn.set_session(readonly=True, isolation_level='REPEATABLE READ')
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET LOCAL statement_timeout = '30s'")
        cur.execute("SELECT to_regclass('public.research_basket_log') AS name")
        if cur.fetchone()['name'] is None:
            return []
        cur.execute('SELECT basket_id,recorded_at,payload FROM public.research_basket_log ORDER BY recorded_at,basket_id')
        return [dict(r) for r in cur.fetchall()]
