from copy import deepcopy

import pandas as pd
import pytest

from analyses.factor_evaluation import diagnostics,research_rows


def test_readable_report_includes_actual_nav_and_date_diagnostics():
    from analyses.factor_evaluation import markdown
    report=dict(archive={},research={'frozen-run':dict(horizon=5,currency='USD',
        models={'parameter':diagnostics(rows(1))},
        existing_portfolio_metrics={'parameter':dict(net_return_pct=-1.79,
            max_drawdown_pct=-3.,ending_equity=98210.,valued_through='2024-01-08')},
        portfolio_context=dict(status='pending_final_period',closed_batches=1,planned_batches=2),
        portfolio_costs=dict(commission_bps=5,slippage_bps=10))})
    text=markdown(report)
    assert '-1.790' in text and '98210.000 USD' in text and '1/2' in text
    assert 'pending_final_period' in text and 'slippage_bps=10' in text
    assert '2024-01-01 | complete | 10/10 | 1.000' in text
    assert 'Q5-kostenscenario’s hierboven zijn geen NAV' in text


def test_report_handles_absent_portfolio_outcomes_without_inventing_zero():
    from analyses.factor_evaluation import markdown
    text=markdown(dict(archive={},research={'empty':dict(horizon=10,models={},existing_portfolio_metrics={})}))
    assert 'status onbekend' in text
    assert '0.000' not in text


def rows(days=3):
    return [dict(date=str(d.date()),ticker=f'T{i}',score=float(i),status='mature',gross_pct=float(i-5),
                 entry_date=str(d.date()),exit_date=str((d+pd.offsets.BDay(5)).date()))
            for d in pd.bdate_range('2024-01-01',periods=days) for i in range(10)]


def test_known_ranking_quantiles_costs_and_overlap():
    result=diagnostics(rows());s=result['summary']
    assert s['mean_rank_ic']==pytest.approx(1)
    assert s['mean_quantile_gross_pct']=={'1':-4.5,'2':-2.5,'3':-.5,'4':1.5,'5':3.5}
    assert s['quantile_monotonicity']==pytest.approx(1)
    assert s['mean_signal_turnover']==0
    assert s['top_quantile_net_sensitivity_pct']['20']==pytest.approx(3.3)
    assert s['nonoverlapping_windows']==1
    assert result['evidence']=='insufficient_disjoint_history'


def test_incomplete_cohort_does_not_drop_losing_stock_or_reform_quantiles():
    data=rows(1);data[0].update(status='pending',gross_pct=None)
    r=diagnostics(data)
    assert r['summary']['complete_dates']==0 and r['summary']['mean_quantile_gross_pct']=={}
    assert r['cohorts'][0]['n']==10


def test_flat_scores_are_not_split_alphabetically_into_fake_quantiles():
    data=rows(1)
    for r in data:r['score']=1.
    assert diagnostics(data)['cohorts'][0]['status']=='insufficient_score_dispersion'


def test_equal_date_weight_prevents_large_cohort_dominance():
    data=rows(2)
    for r in data:
        if r['date']=='2024-01-01':r['gross_pct']=100.
    extra=[dict(r,ticker='X'+r['ticker']) for r in data if r['date']=='2024-01-01']
    s=diagnostics(data+extra)['summary']
    assert s['mean_quantile_gross_pct']['5']==pytest.approx((100+3.5)/2)


def test_entry_and_exit_provenance_and_duplicates():
    data=rows(1);data[0]['entry_date']='2023-12-31'
    assert diagnostics(data)['cohorts'][0]['status']=='invalid_outcome_dates'
    with pytest.raises(ValueError,match='Dubbele'):diagnostics(rows(1)+rows(1))


def test_turnover_counts_new_names_and_reports_observation_gap():
    data=rows(2)
    for r in data:
        if r['date']=='2024-01-02' and r['ticker']=='T9':r['ticker']='NEW'
    r=diagnostics(data)
    assert r['summary']['mean_signal_turnover']==.5
    assert r['cohorts'][1]['days_since_previous']==1


def fixture():
    candidates=[dict(ticker=f'T{i}',score=i) for i in range(10)]
    p=dict(selection_date='2024-01-01',decided_at='2024-01-01T23:00:00Z',sessions=['2024-01-02']*0+['2024-01-02','2024-01-03','2024-01-04','2024-01-05','2024-01-08'],
           universe=[r['ticker'] for r in candidates],candidates={'a':candidates,'b':list(reversed(candidates))},
           training=dict(end='2023-12-01',latest_label='2023-12-29'))
    source=dict(config=dict(mode='historical_ml_comparison',horizon=5),selection_snapshot=dict(plans=[p]))
    cache={r['ticker']:dict(currency='USD',bars=[dict(day=d,open=100.,close=105.) for d in p['sessions']]) for r in candidates}
    return source,cache


def test_frozen_research_adapter_uses_same_outcomes_for_every_model():
    source,cache=fixture();before=deepcopy(source)
    result=research_rows(source,cache,as_of='2024-01-09')
    assert source==before and len(result['a'])==10
    assert result['a'][0]['gross_pct']==pytest.approx(5.)
    assert diagnostics(result['a'])['summary']==diagnostics(result['b'])['summary']


@pytest.mark.parametrize('bad',['training','calendar','universe','price','pending'])
def test_research_cannot_invent_mature_outcomes_or_ignore_future_training(bad):
    source,cache=fixture();p=source['selection_snapshot']['plans'][0]
    if bad=='training':p['training']['latest_label']='2024-01-01'
    if bad=='calendar':p['sessions'][0]='2024-01-01'
    if bad=='universe':p['candidates']['b'].pop()
    if bad in ('training','calendar','universe'):
        with pytest.raises(ValueError):research_rows(source,cache,as_of='2024-01-09')
    else:
        if bad=='price':cache['T0']['bars'].pop()
        r=research_rows(source,cache,as_of='2024-01-08' if bad=='pending' else '2024-01-09')
        assert diagnostics(r['a'])['summary']['complete_dates']==0


def test_training_provenance_is_observed_never_calendar_inferred():
    from common.model_archive import training_label_end
    frame=pd.DataFrame(dict(datum=['2023-01-01'],label_end_10d=['2023-01-20']))
    assert training_label_end(frame,10)=='2023-01-20'
    assert training_label_end(frame.drop(columns='label_end_10d'),10) is None
    frame['label_end_10d']='bad'
    assert training_label_end(frame,10) is None


def test_archived_reruns_do_not_replace_first_scores_and_unknown_labels_block_metrics():
    from unittest.mock import MagicMock
    from datetime import date,datetime,timezone
    from analyses.factor_evaluation import archive_rows
    base=dict(model='m',version='v',mode='prospective',horizon=5,target='return',
        train_end=date(2023,12,1),config={'last_train_label_end':'2023-12-29'},datum=date(2024,1,1),
        basis='next_open',status='mature',entry_date=date(2024,1,2),exit_date=date(2024,1,8))
    data=[dict(base,id='first',recorded_at=datetime(2024,1,1,tzinfo=timezone.utc),ticker=f'T{i}',score=i,gross_pct=i) for i in range(10)]
    data += [dict(r,id='rerun',score=-r['score']) for r in data]
    conn=MagicMock();conn.cursor.return_value.__enter__.return_value.fetchall.return_value=data
    groups,_=archive_rows(conn,'2024-01-10')
    assert len(groups)==1 and len(next(iter(groups.values())))==10
    assert diagnostics(next(iter(groups.values())))['summary']['mean_rank_ic']==pytest.approx(1)
    for r in data:r['config']={}
    groups,_=archive_rows(conn,'2024-01-10')
    assert diagnostics(next(iter(groups.values())))['summary']['complete_dates']==0


def test_training_date_keeps_local_market_day_and_duplicate_quotes_reject():
    from common.model_archive import training_label_end
    frame=pd.DataFrame(dict(label_end_10d=[pd.Timestamp('2024-01-02',tz='Europe/Brussels')]))
    assert training_label_end(frame,10)=='2024-01-02'
    source,cache=fixture();cache['T0']['bars'].append(cache['T0']['bars'][0])
    with pytest.raises(ValueError,match='Dubbele koersdatum'):research_rows(source,cache,as_of='2024-01-09')

