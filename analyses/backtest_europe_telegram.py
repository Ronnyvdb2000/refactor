"""EUR report using the existing durable, deduplicated notification transport."""
import os

from analyses.backtest_telegram import notify
from analyses.paper_telegram import number
from common.horizons import horizon_days

LABELS={'a_par:a_par_model_v1':'A Parameter-model Europa',
        'equal_universe':'Gelijkgewogen Europese kandidaten',
        'benchmark':'STOXX Europe 600 ETF (EXSA.DE)'}


def message(row):
    r,c=row['result'],row['config']
    lines=['📊 Europese backtest — parameter-model',f"Run: {row['run_key']}",f"Status: {row['status']}",
           f"Horizon: {c['horizon']} gemeenschappelijke Europese handelsdagen",
           f"Startkapitaal: €{c['initial_cash']:,.0f} per portefeuille",
           'Basis: EUR; 5 bp commissie + 10 bp slippage per zijde; niet-EUR daarnaast 5 bp FX-kosten.',
           f"Afgeronde perioden: {r.get('closed_batches',0)}/{r.get('planned_batches','?')}"]
    if r.get('valuation_start'):
        lines.append(f"Beleggingsperiode: {r['valuation_start']} t/m {r['valuation_end']}")
    lines.append('Aandelenkoersen uit bevroren bron; ETF en FX afzonderlijk vastgelegd. Dagkoersen zijn uitvoeringsproxies.')
    for book,b in r.get('books',{}).items():
        lines.extend(['',LABELS.get(book,book),
                      f"Netto rendement incl. cash: {number(b['net_return_pct']):+.2f}%",
                      f"Eindwaarde: €{number(b['ending_equity']):,.2f}",
                      f"Maximale terugval: {number(b['max_drawdown_pct']):.2f}%"])
    if r.get('periods'):
        last=r['periods'][-1]
        lines.extend(['',f"Laatste periode: {last['planned_entry']} t/m {last['planned_exit']} ({last['status']})"])
        for book,a in last.get('allocations',{}).items():
            invested=number(a['invested_budget']) if a['invested_budget'] else 0.
            cash=number(last['starting_capital'][book])-invested
            lines.append(f"{LABELS.get(book,book)}: {a['purchased']}/{a['planned']}; belegd €{invested:,.0f}; cash €{cash:,.0f}")
        missing=sorted({t for p in r['periods'] for t in p.get('skipped_entries',{})})
        lines.append(f'Overgeslagen wegens ontbrekende aandelen- of FX-koersen: {len(missing)} aandelen.')
        if missing:lines.append(', '.join(missing[:20])+(' …' if len(missing)>20 else ''))
    if r.get('source_uncovered_rows'):
        lines.append(f"Niet-ondersteunde Europese suffixen: {r['source_uncovered_rows']} bronrijen uitgesloten.")
    if not r.get('books'):lines.append('Nog geen geldige rendementsberekening.')
    if r.get('reason'):lines.append(r['reason'])
    if row['status']!='complete':lines.append('Onvolledig: eventuele rendementen zijn tussentijds.')
    lines.extend(['','Historische reconstructie, geen echte aankopen of bewezen rendement.',
                  'Historische invoer, korte historie en koersuitsluiting kunnen vertekenen. ETF is een indexproxy.'])
    text='\n'.join(lines)
    if len(text.encode('utf-16-le'))//2>3900:raise ValueError('Europees Telegrambericht te lang')
    return text


def main():
    import psycopg2
    horizon=horizon_days(os.getenv('BACKTEST_HORIZON','10'))
    key=f'a_par_europe_{horizon}d:'+os.environ['GITHUB_RUN_ID']
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=20) as conn:
        count=notify(conn,key,os.getenv('TELEGRAM_TOKEN'),os.getenv('TELEGRAM_CHAT_ID'),formatter=message)
    print(f'Europa: {count} nieuwe Telegramberichten bevestigd.')


if __name__=='__main__':main()
