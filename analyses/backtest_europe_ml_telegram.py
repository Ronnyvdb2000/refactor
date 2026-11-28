"""Compact eight-portfolio EUR summary through durable shared Telegram transport."""
import os

from analyses.backtest_telegram import notify
from analyses.paper_telegram import number
from common.horizons import horizon_days

LABELS={'a_par:a_par_model_v1':'Parameter-model', 'xgboostV3:walk_forward_abs':'XGBoost V3',
        'lightgbmV2:walk_forward_abs':'LightGBM V2','catboostV1:walk_forward_abs':'CatBoost V1',
        'elastic_net:walk_forward_return_v1':'Elastic Net','extra_trees:walk_forward_return_v1':'Extra Trees',
        'equal_universe':'Gelijkgewogen kandidaten','benchmark':'STOXX Europe 600 ETF'}


def message(row):
    r,c=row['result'],row['config']
    lines=['📊 Europa — gematchte ML-vergelijking',f"Run: {row['run_key']}",f"Status: {row['status']}",
           f"Horizon: {c['horizon']} gemeenschappelijke Europese handelsdagen; €{c['initial_cash']:,.0f} per portefeuille.",
           'Netto in EUR: 5 bp commissie + 10 bp slippage per zijde; niet-EUR daarnaast 5 bp FX-kosten.',
           f"Afgeronde perioden: {r.get('closed_batches',0)}/{r.get('planned_batches','?')}"]
    if r.get('valuation_start'):lines.append(f"Beleggingsperiode: {r['valuation_start']} t/m {r['valuation_end']}")
    lines.extend(['Zelfde kandidaten met 11 volledige technische kenmerken; parameter-model behoudt eigen factoren.',
                  'ML opnieuw getraind op toen bekende Europese invoer. Eerdere parameteruitslag blijft apart. Geen nieuwe koersen.'])
    for name,b in r.get('books',{}).items():
        lines.append(f"{LABELS.get(name,name)}: {number(b['net_return_pct']):+.2f}%; "
                     f"terugval {number(b['max_drawdown_pct']):.2f}%; eind €{number(b['ending_equity']):,.0f}.")
    if r.get('periods'):
        last=r['periods'][-1]
        lines.extend(['',f"Laatste periode: {last['planned_entry']} t/m {last['planned_exit']} ({last['status']})"])
        for name,a in last.get('allocations',{}).items():
            invested=number(a['invested_budget']) if a['invested_budget'] else 0.
            cash=number(last['starting_capital'][name])-invested
            lines.append(f"{LABELS.get(name,name)}: {a['purchased']}/{a['planned']}; belegd €{invested:,.0f}; cash €{cash:,.0f}.")
        missing=sorted({t for p in r['periods'] for t in p.get('skipped_entries',{})})
        lines.append(f'Overgeslagen wegens aandelen-/FX-koersen: {len(missing)} aandelen.')
        if missing:lines.append(', '.join(missing[:15])+(' …' if len(missing)>15 else ''))
    if r.get('skipped_cohorts'):lines.append(f"Overgeslagen gezamenlijke instapmomenten: {len(r['skipped_cohorts'])}.")
    if not r.get('books'):lines.append('Nog geen geldige rendementsvergelijking.')
    if r.get('reason'):lines.append(r['reason'])
    if row['status']!='complete':lines.append('Onvolledig: rendementen zijn tussentijds.')
    lines.extend(['','Korte historische reconstructie, geen bewezen winst of echte aankopen.',
                  'Historische kenmerken, koersuitsluiting en dagkoersproxies kunnen vertekenen. ETF is een indexproxy.'])
    text='\n'.join(lines)
    if len(text.encode('utf-16-le'))//2>3900:raise ValueError('Europese ML-melding te lang')
    return text


def main():
    import psycopg2
    horizon=horizon_days(os.getenv('BACKTEST_HORIZON','10'))
    key=f'europe_ml_{horizon}d:'+os.environ['GITHUB_RUN_ID']
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=20) as conn:
        count=notify(conn,key,os.getenv('TELEGRAM_TOKEN'),os.getenv('TELEGRAM_CHAT_ID'),formatter=message)
    print(f'Europese ML: {count} nieuwe Telegramberichten bevestigd.')


if __name__=='__main__':main()
