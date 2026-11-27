"""Send a durable, deduplicated summary of one explicit historical backtest run."""
import hashlib
import os
from pathlib import Path

from psycopg2.extras import RealDictCursor

from analyses.paper_telegram import deliver, number, LABELS
from common.horizons import horizon_days


def message(row):
    result, config = row['result'], row['config']
    lines = ['📊 Backtest — historische reconstructie',
             f"Run: {row['run_key']}", f"Status: {row['status']}",
             f"Afgeronde perioden: {result.get('closed_batches', 0)}/{result.get('planned_batches', '?')}",
             f"Startkapitaal: ${number(config['initial_cash']):,.0f} per portefeuille",
             f"Horizon: {config['horizon']} handelsdagen; inclusief transactiekosten"]
    if result.get('data_start'):
        lines.append(f"Selectiedata: {result['data_start']} t/m {result.get('data_end', '?')}")
    if result.get('parent_run'):
        selection = ('Nieuwe walk-forwardselecties voor deze horizon op bewaarde brondata' if result.get('selection_changed')
                     else 'Herberekening van vastgelegde selecties')
        lines.append(f"{selection}; koersen t/m {config['as_of']}; geen nieuwe koersophaling.")
    if result.get('valuation_start'):
        lines.append(f"Werkelijke beleggingsperiode: {result['valuation_start']} t/m {result['valuation_end']}")
    for book, value in result.get('books', {}).items():
        lines.extend(['', LABELS.get(book, book),
                      f"Netto rendement incl. cash: {number(value['net_return_pct']):+.2f}%",
                      f"Eindwaarde: ${number(value['ending_equity']):,.2f}",
                      f"Maximale terugval: {number(value['max_drawdown_pct']):.2f}%",
                      f"Koersen verwerkt t/m: {value['valued_through']}"])
    periods = result.get('periods', [])
    if periods:
        last = periods[-1]
        lines.extend(['', f"Laatste periode: {last['planned_entry']} t/m {last['planned_exit']} ({last['status']})"])
        for book, allocation in last.get('allocations', {}).items():
            invested = number(allocation['invested_budget']) if allocation['invested_budget'] else 0.0
            cash = number(last['starting_capital'][book]) - invested
            lines.append(f"{LABELS.get(book, book)}: {allocation['purchased']}/{allocation['planned']} aandelen; "
                         f"belegd ${invested:,.0f}; cash bij instap ${cash:,.0f}")
        excluded = sorted({ticker for p in periods for ticker in p.get('skipped_entries', {})})
        lines.append(f"Overgeslagen wegens ontbrekende/ongeldige koersen: {len(excluded)} unieke aandelen")
        if excluded:
            lines.append(', '.join(excluded[:20]) + (' …' if len(excluded) > 20 else ''))
    if not result.get('books'):
        lines.append('Nog geen berekend rendement; deze melding bevestigt alleen de voortgang.')
    if row['status']=='insufficient_history':
        lines.append('Te weinig bruikbare trainingshistorie voor deze horizon; geen geldige backtestselecties.')
    if row['status'] != 'complete':
        lines.append('Onvolledige run: bovenstaande cijfers zijn tussentijds.')
    lines.extend(['', 'Historisch onderzoek; geen echte aankopen. Rendement is nog niet bewezen.',
                  'Uitsluiting op historische koersbeschikbaarheid kan de vergelijking vertekenen.'])
    text = '\n'.join(lines)
    if len(text.encode('utf-16-le')) // 2 > 3900:
        raise ValueError('Backtestbericht te lang; geen gedeeltelijke verzending')
    return text


def notify(conn, key, token, chat_id, sender=deliver):
    if not token or not chat_id:
        raise ValueError('Telegraminstellingen ontbreken; geen bericht geclaimd')
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT pg_try_advisory_lock(741206,43) AS locked')
        if not cur.fetchone()['locked']:
            raise RuntimeError('Backtestmeldingen worden al verwerkt')
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT run_key,status,config,result FROM public.research_backtests WHERE run_key=%s', (key,))
            row = cur.fetchone()
            if not row:
                raise ValueError('Deze backtestrun heeft nog geen opgeslagen resultaat')
            text = message(row)
            fingerprint = hashlib.sha256(text.encode()).hexdigest()
            cur.execute('''INSERT INTO public.research_backtest_notices(run_key,message_hash,message)
                VALUES(%s,%s,%s) ON CONFLICT(run_key,message_hash) DO NOTHING RETURNING status''',
                (key, fingerprint, text))
            if not cur.fetchone():
                cur.execute('SELECT status FROM public.research_backtest_notices WHERE run_key=%s AND message_hash=%s FOR UPDATE',
                            (key, fingerprint))
                previous = cur.fetchone()['status']
                if previous == 'sent':
                    return 0
                if previous != 'failed':
                    raise RuntimeError('Vorige bezorging onzeker; controleer Telegram voor een nieuwe poging')
                cur.execute("UPDATE public.research_backtest_notices SET status='claimed',updated_at=clock_timestamp() "
                            'WHERE run_key=%s AND message_hash=%s', (key, fingerprint))
        conn.commit()  # Never perform the HTTP side effect before the durable claim.
        status, ident = sender(text, token, chat_id)
        with conn.cursor() as cur:
            cur.execute('UPDATE public.research_backtest_notices SET status=%s,message_id=%s,updated_at=clock_timestamp() '
                        'WHERE run_key=%s AND message_hash=%s', (status, ident, key, fingerprint))
        conn.commit()
        if status != 'sent':
            raise RuntimeError('Telegrambezorging niet bevestigd; backtestresultaat blijft bewaard')
        return 1
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_unlock(741206,43)')
        conn.commit()


def main():
    import psycopg2
    horizon=horizon_days(os.getenv('BACKTEST_HORIZON','10'))
    key = os.getenv('BACKTEST_RUN_KEY') or f'a_par_{horizon}d:' + os.environ['GITHUB_RUN_ID']
    token, chat = os.getenv('TELEGRAM_TOKEN', ''), os.getenv('TELEGRAM_CHAT_ID', '')
    if not token or not chat:
        raise SystemExit('Telegraminstellingen ontbreken; backtestresultaten blijven in Supabase')
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'], connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute((Path(__file__).resolve().parents[1] / 'maintenance/sql/backtest_notices.sql').read_text())
        conn.commit()
        count = notify(conn, key, token, chat)
    print(f'Backtest: {count} nieuwe Telegramberichten bevestigd.')
    summary = os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as f:
            f.write(f'\nBacktest: {count} nieuwe Telegramberichten bevestigd.\n')


if __name__ == '__main__':
    main()
