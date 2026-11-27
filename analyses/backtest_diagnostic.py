"""A limited model-vs-SPY view of FROZEN historical decisions.

Keeps frozen rankings. Unavailable historical stocks retain their slots as cash;
complete-case availability bias is explicitly reported by the evaluator.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from psycopg2.extras import Json, RealDictCursor
from analyses.backtest_parameter import evaluate


def selected_market_view(plans, cache, as_of, horizon=10):
    limited=deepcopy(plans)
    for p in limited:
        p['books']={k:v for k,v in p['books'].items() if k!='equal_universe'}
    result=evaluate(limited,cache,as_of,horizon)
    result['evidence_status']=result.pop('status')
    result.update(comparison_scope='historical_model_vs_SPY_only',
                  equal_universe_comparison='unavailable_in_this_view',
                  full_comparison_available=False,
                  selection_changed=False,profitability_proven=False)
    return result


def run(conn, key=None):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        query="SELECT run_key,config,selection_snapshot->'plans' AS plans FROM public.research_backtests"
        if key:
            cur.execute(query+' WHERE run_key=%s',(key,))
        else:
            cur.execute(query+" WHERE config->>'horizon'='10' ORDER BY created_at DESC LIMIT 1")
        row=cur.fetchone()
        if not row or not row['plans']:
            raise ValueError('Nog geen vastgelegde historische selecties')
        tickers=sorted({t for p in row['plans'] for k,book in p['books'].items() if k!='equal_universe' for t in book})
        cur.execute('SELECT ticker,data FROM public.research_backtest_cache WHERE run_key=%s AND ticker=ANY(%s)', (row['run_key'],tickers))
        cache={r['ticker']:r['data'] for r in cur.fetchall()}
    result=selected_market_view(row['plans'],cache,row['config']['as_of'],row['config']['horizon'])
    result.update(parent_run=row['run_key'],source_sha=os.getenv('GITHUB_SHA'),
                  diagnostic_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  limitations=row['config']['limitations'])
    with conn.cursor() as cur:
        cur.execute("UPDATE public.research_backtests SET result=jsonb_set(result,'{diagnostic_model_vs_SPY}',%s,true), "
                    "updated_at=clock_timestamp() WHERE run_key=%s", (Json(result),row['run_key']))
    conn.commit()
    return result


def main():
    import psycopg2
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=20) as conn:
        report=run(conn,os.getenv('BACKTEST_RUN_KEY') or None)
    print(json.dumps(report,sort_keys=True,allow_nan=False))
    summary=os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a') as f:
            f.write('## Beperkte historische vergelijking: model tegenover SPY\n\n'
                    'De volledige kandidatenvergelijking wordt hiermee niet hersteld.\n\n```json\n'+json.dumps(report,indent=2)+'\n```\n')
    if report['stopped_at'] in ('missing_prices','corporate_action_review'):
        raise SystemExit('Ook deze vergelijking is onvolledig; geen periodes weggelaten')


if __name__=='__main__':
    main()
