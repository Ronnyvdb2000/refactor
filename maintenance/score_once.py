"""Guard automatic sel_par consumers using the existing daily JSON report.

No DDL. A session advisory lock spans the child process. Uncertain attempts are
NOT automatically repeated: external notifications cannot be transactionally
committed with PostgreSQL. Manual runs use the same readiness and once-per-source checks.
"""
import argparse
from datetime import datetime, timezone
import os
import subprocess
import sys

from common.feature_pipeline import load_feature_context

MODELS = {'a_par': 'meta_models.a_par_model',
          'a_meta': 'meta_models.a_meta_model_score',
          'xgboost': 'models.score_xgboost'}


def score(conn, cutoff, model, command, runner=subprocess.run):
    from psycopg2.extras import Json
    lock = sorted(MODELS).index(model) + 20
    with conn.cursor() as cur:
        cur.execute('SELECT pg_try_advisory_lock(741206,%s)', (lock,))
        if not cur.fetchone()[0]:
            raise RuntimeError('Scoring voor dit model is al bezig; probeer later opnieuw')
    shared = False
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock_shared(741206,11)')
            shared = cur.fetchone()[0]
            if not shared:
                raise RuntimeError('sel_par wordt nog bijgewerkt; probeer later opnieuw')
            cur.execute('SELECT resultaat FROM public.sel_par_runs WHERE selecties_tot=%s', (cutoff,))
            row = cur.fetchone()
        if not row or row[0].get('input_released') is not True:
            raise RuntimeError('Selectiedag is nog niet vrijgegeven')
        previous = row[0].get('scoring', {}).get(model, {})
        if previous.get('status') == 'completed':
            print(f'{model}: {cutoff} al gescoord; geen nieuwe scores of berichten', flush=True)
            return 0
        if previous:
            raise RuntimeError('Vorige scoring is niet bevestigd afgerond; controleer resultaten en berichten voor handmatig herstel')

        def checkpoint(status):
            payload = dict(status=status, run_id=os.getenv('GITHUB_RUN_ID'),
                           updated_at=datetime.now(timezone.utc).isoformat())
            with conn.cursor() as cur:
                cur.execute("UPDATE public.sel_par_runs SET resultaat=jsonb_set(resultaat, "
                            "'{scoring}', coalesce(resultaat->'scoring','{}'::jsonb) || %s, true) "
                            "WHERE selecties_tot=%s", (Json({model: payload}), cutoff))
                if cur.rowcount != 1:
                    raise RuntimeError('Dagregistratie verdwenen; scoring gestopt')
            conn.commit()

        checkpoint('running')
        result = runner(command, check=False)
        checkpoint('completed' if result.returncode == 0 else 'needs_review')
        return result.returncode
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            if shared:
                cur.execute('SELECT pg_advisory_unlock_shared(741206,11)')
            cur.execute('SELECT pg_advisory_unlock(741206,%s)', (lock,))
        conn.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', choices=MODELS)
    args, forwarded = parser.parse_known_args()
    command = [sys.executable, '-m', MODELS[args.model], *forwarded]
    context = load_feature_context()
    if context is None:
        raise RuntimeError('Scoring vereist een bevestigd manifest')
    import psycopg2
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'], connect_timeout=30) as conn:
        return score(conn, context['selection_cutoff'], args.model, command)


if __name__ == '__main__':
    sys.exit(main())
