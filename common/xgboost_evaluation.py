"""Immutable prediction journal; caller commits it together with accepted scores."""
import hashlib
import os
from pathlib import Path
import uuid

DDL = '''CREATE TABLE IF NOT EXISTS public.xgboost_prediction_log (
    run_key text NOT NULL,
    ticker text NOT NULL,
    selection_date date NOT NULL,
    horizon text NOT NULL CHECK (horizon IN ('10d','30d','60d')),
    score double precision NOT NULL,
    model_sha256 text NOT NULL,
    source_sha text NOT NULL,
    predicted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (run_key, ticker, selection_date, horizon)
)'''
INSERT = '''INSERT INTO public.xgboost_prediction_log
    (run_key,ticker,selection_date,horizon,score,model_sha256,source_sha)
    VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING'''


def prepare_journal(model_files):
    """Hash the very files loaded by the scorer, without changing model_versie."""
    run_id = os.environ.get('GITHUB_RUN_ID')
    key = f"{run_id}:{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}" if run_id else str(uuid.uuid4())
    return dict(run_key=key, source_sha=os.environ.get('GITHUB_SHA', 'local'),
                models={h: hashlib.sha256(Path(p).read_bytes()).hexdigest()
                        for h, p in model_files.items()})


def journal_record(cur, record, journal):
    # No UPDATE route: rerunning an identical source run cannot rewrite a prediction.
    cur.execute(INSERT, (journal['run_key'], record['ticker'], record['datum'],
                        record['horizon'], record['score'],
                        journal['models'][record['horizon']], journal['source_sha']))
