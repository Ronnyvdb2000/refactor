"""Operational date gate; the immutable paper execution and protocol stay intact."""
from datetime import datetime, timezone
import os


def waiting_for_entry(conn, trial, today):
    with conn.cursor() as cur:
        cur.execute("SELECT status,snapshot->'sessions'->>0 FROM public.paper_batches "
                    "WHERE trial_id=%s AND status<>'complete' ORDER BY created_at,id LIMIT 1", (trial,))
        row = cur.fetchone()
    return bool(row and row[0]=='pending' and row[1] and row[1]>=today.isoformat())


def main():
    import psycopg2
    from analyses.paper_trial import TRIAL, main as calculate
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=20) as conn:
        waiting = waiting_for_entry(conn, TRIAL, datetime.now(timezone.utc).date())
    if waiting:
        text='Paperproef wacht op een afgesloten instapsessie; nog geen uitvoeringskoersen nodig.'
        print(text)
        summary=os.getenv('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary,'a') as f:
                f.write('## Paperproef wacht op instap\n\n'+text+'\n')
        return
    calculate()


if __name__=='__main__':
    main()
