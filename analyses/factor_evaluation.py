"""Read-only factor diagnostics on frozen OOS scores; no fitting or price fetching.

Returns are percentages over the frozen horizon, never annualised or compounded
across overlapping observations. Quantiles are assigned before seeing outcomes.
"""
from common.costs import (SIGNAL_ROUND_TRIP_BPS, GROSS_SIGNAL_ROUND_TRIP_BPS, ROUND_TRIP_SENSITIVITY_BPS, net_signal_pct)
import hashlib
import argparse
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


MODES=('historical_ml_comparison','historical_europe_ml_comparison',
       'historical_elastic_net_comparison','historical_extra_trees_comparison')


def finite(value):
    try:return float(value) if math.isfinite(float(value)) else None
    except (TypeError,ValueError):return None


def diagnostics(rows, *, quantiles=5, costs=ROUND_TRIP_SENSITIVITY_BPS):
    if quantiles<2 or any(finite(c) is None or c<0 for c in costs):
        raise ValueError('Ongeldige kwantielen/kosten')
    frame=pd.DataFrame(rows)
    if frame.empty:return dict(status='no_scores',cohorts=[],summary={})
    if frame.duplicated(['date','ticker']).any():raise ValueError('Dubbele ticker/datum; pin één scoreversie')
    frame['date']=pd.to_datetime(frame.date).dt.normalize()
    if frame.date.isna().any() or frame.score.map(finite).isna().any():raise ValueError('Ongeldige score/datum')
    cohorts=[];previous=None;last_exit=None;blocks=0
    for day,group in frame.sort_values('date').groupby('date',sort=True):
        score=group.score.astype(float)
        record=dict(date=str(day.date()),n=len(group),n_mature=int((group.status=='mature').sum()),
                    status='incomplete',quantiles={},rank_ic=None,signal_turnover=None,
                    days_since_previous=None)
        try:
            q=pd.qcut(score,quantiles,labels=False,duplicates='drop')+1
            if set(q.dropna().astype(int))!=set(range(1,quantiles+1)):raise ValueError('ties')
        except (ValueError,TypeError):
            record['status']='insufficient_score_dispersion';cohorts.append(record);previous=None;continue
        group=group.assign(quantile=q)
        names=set(group.loc[group['quantile']==quantiles,'ticker'])
        if previous:
            old_day,old_names=previous
            record.update(signal_turnover=len(names-old_names)/len(names),days_since_previous=(day-old_day).days)
        previous=(day,names)
        complete=(group.status.eq('mature').all() and group.gross_pct.map(finite).notna().all())
        if complete:
            ends=pd.to_datetime(group.exit_date,errors='coerce')
            entries=pd.to_datetime(group.entry_date,errors='coerce')
            if ends.isna().any() or entries.isna().any() or (ends<entries).any() or (entries<day).any():
                record['status']='invalid_outcome_dates';cohorts.append(record);continue
            returns=group.gross_pct.astype(float)
            values=[float(group.loc[group['quantile']==i,'gross_pct'].astype(float).mean()) for i in range(1,quantiles+1)]
            ic=finite(spearmanr(score,returns).statistic) if returns.nunique()>1 else None
            record.update(status='complete',rank_ic=ic,quantiles={str(i+1):v for i,v in enumerate(values)},
                top_minus_bottom_pp=values[-1]-values[0],adjacent_monotonic_fraction=float((np.diff(values)>=0).mean()),
                exit_date=str(ends.max().date()),entry_date=str(entries.min().date()))
            # Conservative disjoint selection windows; no claim of independence.
            if last_exit is None or day>last_exit:
                blocks+=1;last_exit=ends.max()
        cohorts.append(record)
    complete=[r for r in cohorts if r['status']=='complete']
    ic=[r['rank_ic'] for r in complete if r['rank_ic'] is not None]
    turns=[r['signal_turnover'] for r in cohorts if r['signal_turnover'] is not None]
    means={str(q):float(np.mean([r['quantiles'][str(q)] for r in complete])) for q in range(1,quantiles+1)} if complete else {}
    vals=list(means.values())
    summary=dict(outcome_status_counts={str(k):int(v) for k,v in frame.status.value_counts().items()},n_dates=len(cohorts),complete_dates=len(complete),nonoverlapping_windows=blocks,
        mean_rank_ic=float(np.mean(ic)) if ic else None,positive_ic_fraction=float(np.mean(np.array(ic)>0)) if ic else None,
        mean_quantile_gross_pct=means,mean_top_minus_bottom_pp=vals[-1]-vals[0] if vals else None,
        quantile_monotonicity=finite(spearmanr(range(quantiles),vals).statistic) if vals and len(set(vals))>1 else None,
        mean_signal_turnover=float(np.mean(turns)) if turns else None,
        turnover_transitions=len(turns),
        top_quantile_net_sensitivity_pct={str(c):net_signal_pct(vals[-1],c) for c in costs} if vals else {})
    return dict(status='evaluated' if complete else 'awaiting_complete_outcomes',summary=summary,cohorts=cohorts,
        evidence='descriptive_only' if blocks>=12 else 'insufficient_disjoint_history',
        policy=dict(equal_date_weight=True,quantiles=quantiles,ties='never_split_equal_scores_by_ticker',
            incomplete='retain_full_scored_cohort; no return metrics until every outcome is valid',
            cost_basis='round-trip bps deducted per fully liquidated horizon basket; sensitivity proxy, not execution NAV',
            turnover='fraction of current top-quantile names absent in previous scored cohort; gaps reported',
            overlap='disjoint windows are not statistically independent',profitability_proven=False))


def research_rows(source,cache,*,as_of):
    """Adapt a pinned matched reconstruction without rerunning its model recipes."""
    cfg=source['config'];h=int(cfg['horizon']);europe=cfg['mode']=='historical_europe_ml_comparison'
    if cfg['mode'] not in MODES:
        raise ValueError('Alleen gematchte, bevroren ML-vergelijkingen')
    snapshot=source['selection_snapshot'];plans=snapshot.get('plans',[])
    if not snapshot.get('comparison_plans_frozen') and europe:raise ValueError('Rangschikkingen nog niet bevroren')
    members={t for p in plans for t in p['universe']}
    if europe:
        from analyses.europe_market import convert
        prices={t:convert(t,cache.get(t,{}),cache) for t in members}
    else:prices=cache
    if any(len(v.get('bars',[]))!=len({r['day'] for r in v.get('bars',[])}) for v in prices.values()):
        raise ValueError('Dubbele koersdatum in bevroren bron')
    bars={t:{r['day']:r for r in v.get('bars',[])} for t,v in prices.items()}
    output={}
    for p in plans:
        day=p['selection_date'];decision=p['decided_at'][:10];training=p.get('training',{})
        if not training.get('latest_label') or not training['latest_label']<decision or not training.get('end','9999')<day:
            raise ValueError('Geen aantoonbare temporele OOS-grens')
        sessions=p['sessions']
        if len(sessions)!=h or sessions!=sorted(set(sessions)) or sessions[0]<=decision:
            raise ValueError('Ongeldige bevroren uitvoeringskalender')
        outcomes={}
        for t in p['universe']:
            valid=(sessions[-1]<as_of and prices.get(t,{}).get('currency')==('EUR' if europe else 'USD') and
                   all(s in bars.get(t,{}) and all(finite(bars[t][s].get(k)) is not None and bars[t][s][k]>0 for k in ('open','close')) for s in sessions))
            gross=100*(bars[t][sessions[-1]]['close']/bars[t][sessions[0]]['open']-1) if valid else None
            outcomes[t]=dict(status='mature' if valid else 'pending_or_missing',gross_pct=gross,
                             entry_date=sessions[0],exit_date=sessions[-1])
        for model,candidates in p['candidates'].items():
            if len(candidates)!=len(outcomes) or {r['ticker'] for r in candidates}!=set(outcomes):
                raise ValueError('Geen gelijk volledig kandidaatuniversum')
            output.setdefault(model,[]).extend(dict(date=day,ticker=r['ticker'],score=r['score'],**outcomes[r['ticker']]) for r in candidates)
    return output


def archive_rows(conn,as_of):
    """Keep archive versions, targets, modes and price bases separate; first recording wins.

    Later reruns of an identical model/date do not add observations or replace
    outcomes after inspection. Unknown OOS training provenance stays excluded.
    """
    from psycopg2.extras import RealDictCursor
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT r.id,r.model,r.version,r.mode,r.horizon,r.target,r.recorded_at,r.train_end,r.config,
            s.datum,s.ticker,s.score,o.basis,o.status,o.gross_pct,o.entry_date,o.exit_date
            FROM public.ml_runs r JOIN public.ml_scores s ON s.run_id=r.id
            LEFT JOIN public.ml_outcomes o ON o.run_id=s.run_id AND o.datum=s.datum AND o.ticker=s.ticker
            ORDER BY r.recorded_at,r.id,s.datum,s.ticker''')
        rows=[dict(r) for r in cur.fetchall()]
    output={};chosen={};excluded=0
    for r in rows:
        cfg=r['config'];day=r['datum'];basis=r['basis'] or ('next_open' if r['mode']=='prospective' else 'selection_close')
        if (r['mode'] not in ('prospective','backtest') or not r['train_end'] or r['train_end']>=day
                or cfg.get('comparison_eligible') is False or cfg.get('outcome_conditioned') is True):
            excluded+=1;continue
        group=f"{r['model']}:{r['version']}|{r['target']}|{r['mode']}|{r['horizon']}d|{basis}"
        cohort=(group,str(day));chosen.setdefault(cohort,str(r['id']))
        if chosen[cohort]!=str(r['id']):continue
        status=r['status'] or 'pending'
        if not r['exit_date'] or str(r['exit_date'])>=as_of:status='pending'
        if basis=='next_open' and (not r['entry_date'] or r['entry_date']<=r['recorded_at'].date()):status='invalid_entry_provenance'
        latest=cfg.get('last_train_label_end') or cfg.get('latest_label')
        if not latest or str(latest)[:10]>=str(day):status='unverified_training_label_boundary'
        output.setdefault(group,[]).append(dict(date=str(day),ticker=r['ticker'],score=r['score'],
            status=status,gross_pct=r['gross_pct'],entry_date=r['entry_date'],exit_date=r['exit_date']))
    return output,excluded


def build_report(conn,*,source_keys=(),as_of=None):
    from psycopg2.extras import RealDictCursor
    as_of=as_of or datetime.now(timezone.utc).date().isoformat()
    archived,excluded=archive_rows(conn,as_of)
    results={name:diagnostics(rows) for name,rows in archived.items()}
    research={}
    for key in source_keys:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT run_key,config,jsonb_build_object('plans',selection_snapshot->'plans',"
                "'comparison_plans_frozen',selection_snapshot->'comparison_plans_frozen') AS selection_snapshot,"
                "result,status FROM public.research_backtests WHERE run_key=%s",(key,))
            source=cur.fetchone()
            if not source or source['status'] not in ('complete','pending_final_period','insufficient_history'):raise ValueError('Bron ontbreekt of is nog niet bevroren')
            days=sorted({d for p in source['selection_snapshot'].get('plans',[]) for d in p['sessions']})
            requested={t for p in source['selection_snapshot'].get('plans',[]) for t in p['universe']}
            if source['config']['mode']=='historical_europe_ml_comparison':
                from analyses.europe_market import FX
                requested.update(FX.values())
            cur.execute("SELECT ticker,data || jsonb_build_object('bars',coalesce((SELECT jsonb_agg(b) "
                "FROM jsonb_array_elements(coalesce(data->'bars','[]'::jsonb)) b WHERE b->>'day'=ANY(%s)),"
                "'[]'::jsonb)) AS data FROM public.research_backtest_cache WHERE run_key=%s AND ticker=ANY(%s)",(days,key,sorted(requested)))
            cache={r['ticker']:r['data'] for r in cur.fetchall()}
        research[key]=dict(horizon=source['config']['horizon'],currency=source['config'].get('currency','USD'),
            models={name:diagnostics(rows) for name,rows in research_rows(source,cache,as_of=as_of).items()},
            existing_portfolio_metrics=source['result'].get('books',{}),
            portfolio_context={k:source['result'].get(k) for k in
                ('status','closed_batches','planned_batches','valuation_start','valuation_end','stopped_at')},
            portfolio_costs={k:source['config'].get(k) for k in
                ('commission_bps','slippage_bps','fx_fee_bps_each_side')},
            basis='cached adjusted next-open to horizon-close proxy; historical FX conversion; no new prices',
            caveat='historical features may be backfilled; not prospective proof; original portfolio NAV unchanged')
    return dict(schema_version=1,as_of=as_of,analysis_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),archive=results,research=research,excluded_archive_rows=excluded,
                profitability_proven=False,new_price_fetches=0,new_model_fits=0)


def markdown(report):
    lines=['# Gestandaardiseerde OOS-scoreanalyse','',
        'Beschrijvend onderzoek. Geen nieuwe training of koersen; bestaande backtestresultaten blijven ongewijzigd.','',
        '| Bron/model | Datums compleet/totaal | IC | Q5−Q1 (pp) | Monotonie | Signaalturnover |',
        '|---|---:|---:|---:|---:|---:|']
    def fmt(v):return '—' if v is None else f'{v:.3f}'
    groups=dict(report['archive'])
    for key,r in report['research'].items():groups.update({key+' / '+name:x for name,x in r['models'].items()})
    for name,r in groups.items():
        s=r['summary']
        if not s:continue
        lines.append(f"| {name.replace('|',' / ')} | {s['complete_dates']}/{s['n_dates']} | {fmt(s['mean_rank_ic'])} | {fmt(s['mean_top_minus_bottom_pp'])} | {fmt(s['quantile_monotonicity'])} | {fmt(s['mean_signal_turnover'])} |")
    lines.extend(['','| Bron/model | Q1 % | Q2 % | Q3 % | Q4 % | Q5 % | Q5 na 20 bp % | Niet-overlappende vensters |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|'])
    for name,r in groups.items():
        s=r['summary']
        if not s:continue
        values=[fmt(s['mean_quantile_gross_pct'].get(str(i))) for i in range(1,6)]
        lines.append('| '+name.replace('|',' / ')+' | '+' | '.join(values)+' | '+fmt(s['top_quantile_net_sensitivity_pct'].get('20'))+' | '+str(s['nonoverlapping_windows'])+' |')
    lines.extend(['','Turnover is een fractie (0,80 = 80% nieuwe namen). Q5−Q1 is een bruto rendementsverschil, geen verhandelbare long/short-portefeuille.',
        'Kosten in JSON: vaste round-trip scenario’s per gesloten horizonbasket; geen besparing door alleen naamoverlap verondersteld.',
        'Kwantielen krijgen gelijke datumweging. Onvolledige cohorten tellen niet mee in rendement/IC; selectie gebeurt vóór uitkomstcontrole.',
        'JSON bevat de vijf kwantielrendementen, kostenanalyse, dekking per cohort en niet-overlappende vensters. Geen significantieclaim.'])
    lines.extend(['','## Bestaande portefeuillebacktests', '',
        'Netto NAV omvat de kosten van de oorspronkelijke backtest. Dit is een afzonderlijke top-10-portefeuille; Q5-kostenscenario’s hierboven zijn geen NAV.', '',
        '| Bron | Horizon | Portefeuille | Netto % | Max. drawdown % | Eindwaarde | Gewaardeerd t/m | Gesloten/gepland |',
        '|---|---:|---|---:|---:|---:|---|---|'])
    for key, research in report['research'].items():
        context=research.get('portfolio_context',{})
        for name, book in research.get('existing_portfolio_metrics',{}).items():
            lines.append('| '+' | '.join([
                key.replace('|',' / '),str(research['horizon'])+'d',name.replace('|',' / '),
                fmt(book.get('net_return_pct')),fmt(book.get('max_drawdown_pct')),
                fmt(book.get('ending_equity'))+' '+research.get('currency','USD'),
                str(book.get('valued_through') or '—'),
                str(context.get('closed_batches','—'))+'/'+str(context.get('planned_batches','—'))])+' |')
        costs=research.get('portfolio_costs',{})
        lines.extend(['',f"Bron {key}: status {context.get('status') or 'onbekend'}; kostenconfiguratie (bp): "+
            ', '.join(f'{k}={v if v is not None else "onbekend"}' for k,v in costs.items())+'.'])
    lines.extend(['','## IC en monotonie per selectiedatum','',
        '| Bron/model | Datum | Status | Aantal/rijp | IC | Oplopende kwantielstappen | Signaalturnover | Tussenruimte (dagen) |',
        '|---|---|---|---:|---:|---:|---:|---:|'])
    for name, result in groups.items():
        for cohort in result['cohorts']:
            lines.append('| '+' | '.join([name.replace('|',' / '),cohort['date'],cohort['status'],
                str(cohort['n'])+'/'+str(cohort['n_mature']),fmt(cohort['rank_ic']),
                fmt(cohort.get('adjacent_monotonic_fraction')),fmt(cohort['signal_turnover']),
                str(cohort['days_since_previous']) if cohort['days_since_previous'] is not None else '—'])+' |')
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--research-run',action='append',default=[])
    parser.add_argument('--latest-research',action='store_true',help='Pin latest frozen matched run per region/horizon at report start')
    parser.add_argument('--output',default='results/factor_evaluation')
    args=parser.parse_args()
    import psycopg2
    conn=psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=30)
    try:
        conn.set_session(readonly=True,isolation_level='REPEATABLE READ')
        keys=list(args.research_run)
        if args.latest_research:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT ON (config->>'mode',config->>'horizon') run_key FROM public.research_backtests "
                    "WHERE config->>'mode'=ANY(%s) "
                    "AND status IN ('complete','pending_final_period','insufficient_history') "
                    "ORDER BY config->>'mode',config->>'horizon',created_at DESC,run_key DESC",(list(MODES),))
                keys.extend(r[0] for r in cur.fetchall())
        report=build_report(conn,source_keys=list(dict.fromkeys(keys)))
    finally:conn.close()
    path=Path(args.output);path.mkdir(parents=True,exist_ok=True)
    (path/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    (path/'report.md').write_text(markdown(report))
    print(markdown(report))


if __name__=='__main__':main()


