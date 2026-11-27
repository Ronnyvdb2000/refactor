"""Notify paper-trial milestones without changing its frozen measurement code.

Claims commit BEFORE the HTTP request. Ambiguous requests are not retried;
confirmed API rejections may be retried. Telegram and PostgreSQL are not atomic.
"""
import hashlib
import math
import os

import requests
from psycopg2.extras import RealDictCursor

from analyses.paper_trial import TRIAL
LABELS = {'equal_universe': 'Gelijkgewogen kandidaten', 'benchmark': 'SPY',
          'a_par:a_par_model_v1': 'A Parameter-model'}


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Niet-eindig meetresultaat')
    return value


def milestones(protocol, batches, nav):
    if not batches:
        return []
    first = batches[0]
    snapshot = first['snapshot']
    text = ('🧪 Virtuele paperproef gestart\n'
            f"Proef: {TRIAL}\nHorizon: {protocol['horizon']} handelsdagen\n"
            f"Virtueel startkapitaal: ${protocol['initial_cash']:,.0f} per portefeuille\n"
            f"Geplande aankoop: {snapshot['sessions'][0]}\n"
            f"Geplande verkoop: {snapshot['sessions'][-1]}\n"
            f"Kandidaten: {len(snapshot['universe'])}\n"
            'Vergelijking: A Parameter-model, gelijkgewogen kandidaten en SPY.\n'
            'Er is nog geen gemeten rendement. Geen echte transacties.')
    events = [(f"started:{first['id']}", first['id'], text)]
    capital = {**{m['model']+':'+m['version']: float(protocol['initial_cash']) for m in protocol['models']},
               'equal_universe': float(protocol['initial_cash']), 'benchmark': float(protocol['initial_cash'])}
    for index, batch in enumerate(batches, 1):
        if batch['status'] != 'complete':
            break
        result = batch['result']
        if result.get('status') != 'complete' or not result.get('exit'):
            raise ValueError('Afgeronde batch mist bevestigd meetresultaat')
        ending = {book: number(result['ending'][book]) for book in capital}
        period = {book: 100*(ending[book]/capital[book]-1) for book in capital}
        lines = ['📊 Paperproef — afgeronde meetperiode',
                 f"{result['entry']} t/m {result['exit']} · {protocol['horizon']} handelsdagen",
                 f'Afgeronde perioden: {index}']
        for book in capital:
            peak, worst = float(protocol['initial_cash']), 0.0
            points = sorted([r for r in nav if r['book']==book and str(r['day'])<=result['exit']],
                            key=lambda r:str(r['day']))
            if not points or str(points[-1]['day']) != result['exit']:
                raise ValueError('Dagwaarderingen ontbreken; geen resultaatbericht')
            for row in points:
                equity = number(row['equity'])
                peak = max(peak, equity)
                worst = min(worst, 100*(equity/peak-1))
            allocation = result.get('allocations', {}).get(book)
            if allocation:
                lines.append(f"{LABELS.get(book,book)}: {allocation['purchased']}/{allocation['planned']} aandelen; belegd ${allocation['invested_budget']:,.0f}")
            lines.extend(['', LABELS.get(book, book),
                          f"Netto periode: {period[book]:+.2f}%",
                          f"Cumulatief: {100*(ending[book]/protocol['initial_cash']-1):+.2f}%",
                          f"Maximale terugval tot deze datum: {worst:.2f}%"])
            if book not in ('equal_universe', 'benchmark'):
                lines.extend([f"Verschil periode vs kandidaten: {period[book]-period['equal_universe']:+.2f} procentpunt",
                              f"Verschil periode vs SPY: {period[book]-period['benchmark']:+.2f} procentpunt"])
        if result.get('skipped_entries'):
            lines.append('Overgeslagen bij instap: '+', '.join(sorted(result['skipped_entries'])))
        lines.extend(['', f"Kosten per zijde: {protocol['commission_bps']} bp commissie + {protocol['slippage_bps']} bp koersafwijking.",
                      'Virtuele resultaten onder vaste aannames. Rendement is nog niet bewezen.'])
        events.append((f"closed:{batch['id']}", batch['id'], '\n'.join(lines)))
        capital = ending
    return events


def deliver(text, token, chat_id, post=requests.post):
    """One request only; never expose token-bearing URLs in exceptions/logs."""
    try:
        response = post(f'https://api.telegram.org/bot{token}/sendMessage',
                        json=dict(chat_id=chat_id, text=text, disable_web_page_preview=True), timeout=15)
        payload = response.json()
    except (requests.RequestException, ValueError):
        return 'uncertain', None
    if not isinstance(payload, dict):
        return 'uncertain', None
    message_id = payload.get('result', {}).get('message_id') if isinstance(payload.get('result'), dict) else None
    if response.status_code==200 and payload.get('ok') is True and isinstance(message_id,int) and message_id>0:
        return 'sent', message_id
    if payload.get('ok') is False and 400<=response.status_code<500:
        return 'failed', None
    return 'uncertain', None


def notify(conn, token, chat_id, sender=deliver):
    if not token or not chat_id:
        raise ValueError('Telegraminstellingen ontbreken; geen bericht geclaimd')
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT pg_try_advisory_lock(741206,41) AS locked')
        if not cur.fetchone()['locked']:
            raise RuntimeError('Paperberichten worden al verwerkt')
    sent = 0
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT protocol FROM public.paper_trials WHERE id=%s', (TRIAL,))
            protocol = cur.fetchone()['protocol']
            cur.execute('SELECT id,snapshot,status,result FROM public.paper_batches WHERE trial_id=%s ORDER BY created_at,id', (TRIAL,))
            batches = [dict(r) for r in cur.fetchall()]
            cur.execute('SELECT n.book,n.day,n.equity FROM public.paper_nav n JOIN public.paper_batches b ON b.id=n.batch_id WHERE b.trial_id=%s ORDER BY n.day,n.book', (TRIAL,))
            nav = [dict(r) for r in cur.fetchall()]
        for key, batch, text in milestones(protocol,batches,nav):
            if len(text.encode('utf-16-le'))//2 > 3900:
                raise ValueError('Resultaatbericht te lang; niet gedeeltelijk verzonden')
            fingerprint = hashlib.sha256(text.encode()).hexdigest()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('''INSERT INTO public.paper_notices(trial_id,event_key,batch_id,message,message_hash)
                    VALUES(%s,%s,%s,%s,%s) ON CONFLICT(trial_id,event_key) DO NOTHING RETURNING status''',
                    (TRIAL,key,batch,text,fingerprint))
                inserted = cur.fetchone()
                if not inserted:
                    cur.execute('SELECT status,message_hash FROM public.paper_notices WHERE trial_id=%s AND event_key=%s FOR UPDATE', (TRIAL,key))
                    previous = cur.fetchone()
                    if previous['status']=='sent':
                        continue
                    if previous['message_hash'] != fingerprint:
                        raise RuntimeError('Berichtinhoud gewijzigd; controleer registratie')
                    if previous['status']!='failed':
                        raise RuntimeError('Vorige bezorging onzeker; controleer Telegram vóór handmatig herstel')
                    cur.execute("UPDATE public.paper_notices SET status='claimed',updated_at=clock_timestamp() WHERE trial_id=%s AND event_key=%s", (TRIAL,key))
            conn.commit()  # Durable claim before the external side effect.
            status, ident = sender(text,token,chat_id)
            with conn.cursor() as cur:
                cur.execute('UPDATE public.paper_notices SET status=%s,message_id=%s,updated_at=clock_timestamp() WHERE trial_id=%s AND event_key=%s',
                            (status,ident,TRIAL,key))
            conn.commit()
            if status!='sent':
                raise RuntimeError('Telegrambericht niet bevestigd; resultaten blijven bewaard')
            sent += 1
        return sent
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_unlock(741206,41)')
        conn.commit()


def main():
    import psycopg2
    token, chat = os.getenv('TELEGRAM_TOKEN',''),os.getenv('TELEGRAM_CHAT_ID','')
    if not token or not chat:
        raise SystemExit('Telegraminstellingen ontbreken; resultaten staan in Supabase')
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=20) as conn:
        count = notify(conn,token,chat)
    print(f'Paperproef: {count} nieuwe Telegramberichten bevestigd.')


if __name__=='__main__':
    main()
