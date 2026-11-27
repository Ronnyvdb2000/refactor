"""Re-evaluate frozen historical selections and prices without fetching or reranking."""
from psycopg2.extras import Json, RealDictCursor
import hashlib
from pathlib import Path


def run_cached(conn, key):
    from analyses.backtest_parameter import configuration, evaluate
    import os
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT pg_try_advisory_lock(741206,42) AS locked')
        if not cur.fetchone()['locked']:
            raise RuntimeError('Backtest is al bezig')
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM public.research_backtests WHERE config->>'mode'='historical_reconstruction' "
                        "AND jsonb_array_length(CASE WHEN jsonb_typeof(selection_snapshot->'plans')='array' "
                        "THEN selection_snapshot->'plans' ELSE '[]'::jsonb END)>0 ORDER BY created_at DESC LIMIT 1")
            parent = cur.fetchone()
            if not parent:
                raise ValueError('Geen vastgelegde historische selecties; kies een verse backtestrun')
            as_of = parent['config']['as_of']
            config = {**configuration(as_of), 'mode': 'historical_cached_replay',
                      'parent_run': parent['run_key'], 'selection_policy': 'unchanged_frozen_parent_ranking',
                      'replay_hash': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
            if parent['config']['quote_basis'] != config['quote_basis']:
                raise ValueError('Koersbasis wijkt af; opgeslagen koersen kunnen niet worden hergebruikt')
            if parent['config']['implementation_hash'] != config['implementation_hash']:
                raise ValueError('Model gewijzigd; kies een verse backtestrun')
            cur.execute('SELECT config,result FROM public.research_backtests WHERE run_key=%s', (key,))
            existing = cur.fetchone()
            if existing:
                if existing['config'] != config:
                    raise ValueError('Deze run verwijst naar een andere backtestdefinitie')
                return dict(existing['result'], run_key=key)
            cur.execute('SELECT ticker,data FROM public.research_backtest_cache WHERE run_key=%s', (parent['run_key'],))
            cache = {r['ticker']: r['data'] for r in cur.fetchall()}
        selection = parent['selection_snapshot']
        result = evaluate(selection['plans'], cache, as_of)
        result['evidence_status'] = result.pop('status')
        status = 'complete' if result['closed_batches'] == result['planned_batches'] else 'partial'
        if result['stopped_at'] in ('pending', 'open'):
            status = 'pending_final_period'
        result.update(status=status, parent_run=parent['run_key'], selection_changed=False,
                      quote_source='frozen_parent_cache_no_new_provider_fetch',
                      data_start=selection.get('data_start'), data_end=selection.get('data_end'),
                      rows=selection.get('rows'), dates=selection.get('dates'), quotes_cached=len(cache),
                      quote_errors={t:v['error'] for t,v in cache.items() if 'error' in v},
                      limitations=config['limitations'], quote_basis=config['quote_basis'])
        with conn.cursor() as cur:
            cur.execute('INSERT INTO public.research_backtests(run_key,source_sha,config,selection_snapshot,status,result) '
                        'VALUES(%s,%s,%s,%s,%s,%s)',
                        (key, os.getenv('GITHUB_SHA'), Json(config), Json(selection), status, Json(result)))
            cur.execute('INSERT INTO public.research_backtest_cache(run_key,ticker,data) '
                        'SELECT %s,ticker,data FROM public.research_backtest_cache WHERE run_key=%s',
                        (key, parent['run_key']))
        conn.commit()
        return dict(result, run_key=key)
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_unlock(741206,42)')
        conn.commit()
