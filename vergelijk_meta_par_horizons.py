#!/usr/bin/env python3
"""Read-only walk-forward: strategy-meta vs parameter IC vs MA50, per horizon.
No DB writes. Each horizon trains only on mature, embargoed outcomes.
"""
import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from scipy.stats import spearmanr

HORIZONS = (5, 10, 20, 30, 60)
STRATEGIES = ['bot_00xxxV2', 'bot_00vcp', 'bot_00mr', 'bot_00dm', 'bot_00ms']
FACTORS = ['atr14_pct', 'rsi14', 'ibs', 'pct_from_ma50', 'pct_from_ma200',
           'vol_ratio_20d', 'pct_from_high52w']
# Absolute price/MA levels excluded to avoid ticker fingerprints.
OUT = Path('results/meta_par_horizons')


def load(conn):
    targets = ', '.join(f'fwd_ret_{h}d' for h in HORIZONS)
    gcols = ', '.join(FACTORS)
    query = f'''
    WITH s AS (
      SELECT DISTINCT ON (ticker, datum, strategie)
        ticker, datum, strategie, score FROM selecties
      WHERE strategie = ANY(%s)
      ORDER BY ticker, datum, strategie
    ), f AS (
      SELECT DISTINCT ON (ticker, datum, strategie)
        ticker, datum, strategie, {targets}
      FROM forward_returns ORDER BY ticker, datum, strategie
    ), g AS (
      SELECT DISTINCT ON (ticker, datum)
        ticker, datum, {gcols} FROM generieke_technicals
      ORDER BY ticker, datum
    )
    SELECT s.ticker, s.datum, s.strategie, s.score,
           {', '.join('f.'+f'fwd_ret_{h}d' for h in HORIZONS)},
           {', '.join('g.'+c for c in FACTORS)}
    FROM s JOIN f ON s.ticker=f.ticker AND s.datum=f.datum AND s.strategie=f.strategie
    LEFT JOIN g ON s.ticker=g.ticker AND s.datum=g.datum
    '''
    with conn.cursor() as cur:
        cur.execute(query, (STRATEGIES,))
        cols = [d[0] for d in cur.description]
        records = cur.fetchall()
    df = pd.DataFrame.from_records(records, columns=cols)
    if df.empty:
        return df
    df['datum'] = pd.to_datetime(df['datum'], utc=True).dt.tz_localize(None).dt.normalize()
    for c in ['score'] + FACTORS + [f'fwd_ret_{h}d' for h in HORIZONS]:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def ic_weights(train, target, min_dates, min_tickers, min_factors):
    # Strict whitelist: never pass labels, targets or engineered future columns as factors.
    unique = train.drop_duplicates(['ticker', 'datum'])
    daily = {f: [] for f in FACTORS}
    for _, day in unique.groupby('datum'):
        if len(day) < min_tickers:
            continue
        for factor in FACTORS:
            z = day[[factor, target]].dropna()
            if len(z) < min_tickers or z[factor].nunique() < 3 or z[target].nunique() < 3:
                continue
            ic = spearmanr(z[factor], z[target]).statistic
            if np.isfinite(ic):
                daily[factor].append(float(ic))
    weights = {}
    for f, vals in daily.items():
        if len(vals) >= min_dates:
            ic = float(np.clip(np.mean(vals), -.15, .15))
            if abs(ic) >= .02:
                weights[f] = ic
    return weights if len(weights) >= min_factors else {}


def parameter_scores(test, weights):
    unique = test.drop_duplicates(['ticker', 'datum']).set_index('ticker')
    result = pd.Series(0.0, index=unique.index)
    denom = sum(abs(v) for v in weights.values())
    for factor, weight in weights.items():
        result += weight * (unique[factor].rank(pct=True).fillna(.5) - .5)
    return result / denom


def meta_strategy_scores(train, test, target, min_strategy_obs):
    """Rank strategies by historically achieved excess return, with shrinkage.

    Strategy quality uses only embargoed realized returns; stock within a strategy
    is ranked by that strategy's own selection score (if available).
    """
    daily_market = train.drop_duplicates(['ticker', 'datum']).groupby('datum')[target].mean()
    hist = train[['datum', 'strategie', target]].copy()
    hist['market'] = hist['datum'].map(daily_market)
    hist['excess'] = hist[target] - hist['market']
    perf = hist.groupby('strategie')['excess'].agg(['mean', 'count'])
    perf = perf[perf['count'] >= min_strategy_obs]
    if len(perf) < 2:
        return None, {}
    # Empirical shrinkage toward 0; prevent tiny strategy samples dominating.
    quality = (perf['mean'] * perf['count'] / (perf['count'] + 100)).to_dict()
    scored = test.copy()
    scored['strategy_quality'] = scored['strategie'].map(quality)
    scored['within_rank'] = scored.groupby('strategie')['score'].rank(pct=True).fillna(.5)
    # Strategy priority dominates, bot score breaks ties within strategy.
    # Rank strategy quality to avoid mixing return units with arbitrary bot scores.
    qrank = pd.Series(quality).rank(pct=True).to_dict()
    scored['meta_score'] = scored['strategie'].map(qrank) + .01 * scored['within_rank']
    scored = scored.dropna(subset=['meta_score'])
    if scored.empty:
        return None, quality
    return scored.sort_values('meta_score', ascending=False).drop_duplicates('ticker').set_index('ticker')['meta_score'], quality


def run_horizon(df, horizon, args):
    target = f'fwd_ret_{horizon}d'
    df = df.dropna(subset=[target]).sort_values('datum').copy()
    dates = sorted(df['datum'].unique())
    summary = {'horizon': f'{horizon}d', 'status': 'insufficient_data',
               'observaties': len(df), 'datums': len(dates),
               'embargo_calendar_days': max(25, horizon + 5),
               'folds': 0, 'results': []}
    if len(dates) < args.min_train_dates + 4:
        summary['reden'] = 'Te weinig datums met ingevulde forward returns'
        return summary, [], []
    cutoff = pd.Timestamp(dates[int(len(dates) * .7)])
    summary['cutoff_70pct'] = cutoff.date().isoformat()
    embargo = max(25, horizon + 5)
    rows, picks = [], []
    for day in dates:
        day = pd.Timestamp(day)
        if day <= cutoff:
            continue
        train = df[df['datum'] < day - pd.Timedelta(days=embargo)]
        test = df[df['datum'] == day]
        if train['datum'].nunique() < args.min_train_dates or len(train) < args.min_train or len(test) < args.min_test:
            continue
        weights = ic_weights(train, target, args.min_ic_dates, args.min_tickers, args.min_factors)
        if not weights:
            continue
        par = parameter_scores(test, weights)
        meta, quality = meta_strategy_scores(train, test, target, args.min_strategy_obs)
        if meta is None:
            continue
        # Equal universe: unique ticker/date, irrespective of strategy duplicates.
        universe = test.sort_values('strategie').drop_duplicates('ticker').set_index('ticker')
        universe['par'] = par
        universe['meta'] = meta
        universe['ma50'] = -universe['pct_from_ma50']
        # Compare on common eligible tickers; avoid unequal coverage bias.
        universe = universe.dropna(subset=['par', 'meta', 'ma50', target])
        if len(universe) < args.min_test:
            continue
        baseline = float(universe[target].mean())
        k = max(1, int(np.ceil(len(universe) * args.top_fraction)))
        for method in ('meta', 'a_par_model', 'ma50'):
            scorecol = {'meta': 'meta', 'a_par_model': 'par', 'ma50': 'ma50'}[method]
            top = universe.nlargest(k, scorecol)
            ret = float(top[target].mean())
            rows.append({'horizon': f'{horizon}d', 'datum': day.date().isoformat(),
                         'methode': method, 'n': len(universe), 'n_top': k,
                         'top_ret': ret, 'baseline_ret': baseline,
                         'excess': ret-baseline, 'n_factors': len(weights),
                         'n_strategies': len(quality)})
            for ticker, r in top.iterrows():
                picks.append({'horizon': f'{horizon}d', 'datum': day.date().isoformat(),
                              'methode': method, 'ticker': ticker,
                              'score': float(r[scorecol]), 'forward_return': float(r[target])})
    if not rows:
        summary['reden'] = 'Geen folds die voldoen aan alle minimale datavereisten'
        return summary, rows, picks
    result = pd.DataFrame(rows)
    summary['status'] = 'ok'
    summary['folds'] = int(result['datum'].nunique())
    for method in ('meta', 'a_par_model', 'ma50'):
        sub = result[result['methode'] == method]
        summary['results'].append({'methode': method, 'folds': len(sub),
            'gem_top20_ret_pct': round(float(sub['top_ret'].mean()), 4),
            'gem_baseline_ret_pct': round(float(sub['baseline_ret'].mean()), 4),
            'gem_excess_pp': round(float(sub['excess'].mean()), 4),
            'win_fold_pct': round(float((sub['excess'] > 0).mean() * 100), 1)})
    return summary, rows, picks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-train', type=int, default=200)
    ap.add_argument('--min-train-dates', type=int, default=10)
    ap.add_argument('--min-test', type=int, default=20)
    ap.add_argument('--min-ic-dates', type=int, default=5)
    ap.add_argument('--min-tickers', type=int, default=10)
    ap.add_argument('--min-factors', type=int, default=3)
    ap.add_argument('--min-strategy-obs', type=int, default=50)
    ap.add_argument('--top-fraction', type=float, default=.2)
    args = ap.parse_args()
    if not 0 < args.top_fraction <= 1:
        ap.error('top-fraction moet tussen 0 en 1 liggen')
    url = os.getenv('SUPABASE_DB_URL')
    if not url:
        ap.error('SUPABASE_DB_URL ontbreekt')
    OUT.mkdir(parents=True, exist_ok=True)
    with psycopg2.connect(url) as conn:
        conn.set_session(readonly=True)
        df = load(conn)
    print(f'Opgehaald: {len(df)} signalen')
    summaries, rows, picks = [], [], []
    for h in HORIZONS:
        summary, hr, hp = run_horizon(df, h, args) if not df.empty else (
            {'horizon': f'{h}d', 'status': 'insufficient_data', 'reden': 'Geen data'}, [], [])
        summaries.append(summary)
        rows.extend(hr)
        picks.extend(hp)
        print(f"{h}d: {summary['status']} ({summary.get('folds', 0)} folds)")
    report = {'status': 'ok' if any(s['status']=='ok' for s in summaries) else 'insufficient_data',
              'notes': 'Read-only onderzoeksvergelijking; geen productievoorspellingen. '
                       'Meta = historische strategie-excess; parameter = historische factor-IC; '
                       'MA50 = eenvoudige referentie. Embargo >= max(25, horizon+5) kalenderdagen.',
              'horizons': summaries}
    (OUT / 'samenvatting.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    if rows:
        pd.DataFrame(rows).to_csv(OUT / 'folds.csv', index=False)
        pd.DataFrame(picks).to_csv(OUT / 'top_selecties.csv', index=False)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
