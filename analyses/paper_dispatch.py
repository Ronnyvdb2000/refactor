"""Operational date gate; the immutable paper execution and protocol stay intact."""
from datetime import datetime, timezone
import os


def waiting_for_entry(conn, trial, today):
    with conn.cursor() as cur:
        cur.execute("SELECT status,snapshot->'sessions'->>0 FROM public.paper_batches "
                    "WHERE trial_id=%s AND status<>'complete' ORDER BY created_at,id LIMIT 1", (trial,))
        row = cur.fetchone()
    return bool(row and row[0]=='pending' and row[1] and row[1]>=today.isoformat())


def follow_frozen_protocol(engine, stored):
    """Changed training guards must not rewrite or strand the existing paper trial.

    Only a training implementation change is allowed here. The paper execution
    engine and every investment assumption must still match the frozen protocol.
    Such a trial can finish existing baskets, but never start a new basket.
    """
    current = engine.PROTOCOL
    if stored == current:
        return False
    if {k:v for k,v in stored.items() if k != 'implementation_hash'} != {
            k:v for k,v in current.items() if k != 'implementation_hash'}:
        raise ValueError('Paper-uitvoering gewijzigd; afzonderlijke proefversie vereist')
    if not isinstance(stored.get('implementation_hash'), str) or len(stored['implementation_hash']) != 64:
        raise ValueError('Bevroren trainingsherkomst ontbreekt')
    engine.PROTOCOL = dict(stored)
    engine.freeze_batch = lambda *args, **kwargs: None
    print('Paperproef volgt bestaande mandjes met het bevroren protocol; nieuwe beslissingen vereisen een afzonderlijke proefversie.')
    return True


def main():
    import psycopg2
    from analyses import paper_trial as engine
    TRIAL, calculate = engine.TRIAL, engine.main
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT protocol FROM public.paper_trials WHERE id=%s', (TRIAL,))
            row = cur.fetchone()
        if not row:
            raise ValueError('Paperprotocol ontbreekt')
        follow_frozen_protocol(engine, row[0])
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
