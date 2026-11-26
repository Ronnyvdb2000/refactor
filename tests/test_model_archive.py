from datetime import date
from unittest.mock import MagicMock,patch
import pandas as pd
import pytest
from common.model_archive import ranked_scores,parameter_rows,record
from analyses.evaluate_models import assess,compare,selection_outcome


def test_archive_ranks_before_outcomes_and_ties_are_deterministic():
    df=pd.DataFrame([dict(ticker=t,datum='2026-11-25',score=s,koers=100) for t,s in [('B',.2),('A',.2),('C',.1)]])
    scores=ranked_scores(df,2)
    assert [r['ticker'] for r in scores]==['A','B','C']
    assert [r['picked'] for r in scores]==[True,True,False]
    with pytest.raises(ValueError,match='dubbele'):
        ranked_scores(pd.concat([df,df]),2)
    df.loc[0,'score']=float('inf')
    with pytest.raises(ValueError):ranked_scores(df,2)


def test_signed_weights_retain_negative_direction_and_rejected_parameters():
    frame=pd.DataFrame([dict(parameter='a',ic=-.1,ic_raw=-.2,ic_ir=-2,n_datums=10,n_obs=200),
                        dict(parameter='b',ic=.05),dict(parameter='c',ic=.01)])
    rows=parameter_rows(frame,selected=['a','b'])
    assert rows[0]['weight']==pytest.approx(-2/3)
    assert rows[1]['weight']==pytest.approx(1/3)
    assert rows[2]['selected'] is False and rows[2]['weight'] is None
    assert rows[0]['ic_raw']==-.2


def test_importance_is_not_a_signed_coefficient():
    rows=parameter_rows(pd.DataFrame([dict(parameter='a',importance=9)]),kind='importance')
    assert rows[0]['weight']==9 and rows[0]['ic'] is None


def test_idempotent_run_rejects_changed_content():
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[None,('wrong-hash',)]
    frame=pd.DataFrame([dict(ticker='A',datum='2026-11-25',score=.3)])
    with pytest.raises(ValueError,match='niet overschreven'):
        record(conn,frame,model='test',version='v1',horizon=5,mode='prospective',target='rank',run_key='same')
    assert not conn.commit.called


def test_backtest_must_have_prior_training_cutoff():
    frame=pd.DataFrame([dict(ticker='A',datum='2026-11-25',score=.3)])
    for end in [None,'2026-11-25','2026-11-26']:
        with pytest.raises(ValueError,match='Train/test'):
            record(MagicMock(),frame,model='test',version='v1',horizon=5,mode='backtest',target='rank',train_end=end)


def cohort():
    return [dict(ticker=t,score=s,status='mature',gross_pct=v,entry_date='2026-11-01',exit_date='2026-11-10')
            for t,s,v in [('A',3,10),('B',2,-4),('C',1,3)]]


def test_missing_top_or_baseline_prevents_survivor_bias():
    rows=cohort();rows[-1]['status']='pending'
    result=assess(rows,2)
    assert result['status']=='incomplete' and result['n_mature']==2
    assert result['top_gross_pct'] is None


def test_equal_weight_return_cost_and_ranking_correlation():
    result=assess(cohort(),2,20)
    assert result['top_gross_pct']==3
    assert result['top_net_pct']==pytest.approx(2.8)
    assert result['baseline_pct']==3
    assert result['win_rate']==.5
    assert result['rank_ic']==.5


def test_comparison_uses_common_candidates_and_rejects_different_sessions():
    a=cohort();b=cohort();b[0]['score']=0
    result=compare(a,b,1)
    assert result['status']=='complete' and result['difference_pp']==14
    b[0]['entry_date']='2026-11-02'
    assert compare(a,b,1)['status']=='different_sessions'
    assert compare(a,b[:1],2)['status']=='incomplete'


def test_selection_returns_require_dated_labels_and_frozen_entry_consistency():
    row=dict(datum=date(2026,11,1),entry_close=100,selection_close=100,
             label_basis=50,label_close=55,label_return=10,label_date='2026-11-10')
    assert selection_outcome(row,date(2026,11,26))['gross_pct']==10
    assert selection_outcome({**row,'label_date':None},date(2026,11,26))['status']=='pending'
    assert selection_outcome({**row,'labels_review':'split'},date(2026,11,26))['status']=='review'
    assert selection_outcome({**row,'entry_close':101},date(2026,11,26))['status']=='entry_conflict'


def test_daily_evaluator_persists_matched_comparison_without_downloading_backtests(monkeypatch):
    from analyses import evaluate_models as evaluation
    rows=[]
    for ident,model in [('00000000-0000-0000-0000-000000000001','a_par'),
                        ('00000000-0000-0000-0000-000000000002','catboost')]:
        for i,r in enumerate(cohort(),1):
            rows.append(dict(id=ident,model=model,version='v1',target='rank',horizon=5,
                             mode='backtest',datum=date(2026,11,1),top_n=1,
                             ticker=r['ticker'],score=r['score'],entry_close=100,selection_close=100,
                             label_basis=100,label_close=100+r['gross_pct'],label_return=r['gross_pct'],
                             label_date='2026-11-10'))
    monkeypatch.setattr(evaluation,'load',lambda c:rows)
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value=[]
    fetch=MagicMock(side_effect=AssertionError('Backtests must not download future prices'))
    with patch.object(evaluation,'execute_values') as bulk:
        result=evaluation.run(conn,as_of=date(2026,11,26),fetch=fetch)
    assert result==dict(scores=6,cohorts=2,complete=2,comparisons=1,cost_bps=20)
    assert len(bulk.call_args.args[2])==6
    assert all(r[4]=='mature' for r in bulk.call_args.args[2])
    assert sum('INSERT INTO public.ml_eval' in c.args[0] for c in cur.execute.call_args_list)==2
    assert sum('INSERT INTO public.ml_compare' in c.args[0] for c in cur.execute.call_args_list)==1
    fetch.assert_not_called()


def test_recent_prospective_prediction_is_pending_without_provider_request(monkeypatch):
    from datetime import datetime,timezone
    from analyses import evaluate_models as evaluation
    row=dict(id='00000000-0000-0000-0000-000000000001',model='a_par',version='v1',target='rank',
             horizon=5,mode='prospective',recorded_at=datetime(2026,11,26,tzinfo=timezone.utc),
             datum=date(2026,11,25),top_n=10,ticker='TEST',score=.3)
    monkeypatch.setattr(evaluation,'load',lambda c:[row])
    conn=MagicMock();conn.cursor.return_value.__enter__.return_value.fetchall.return_value=[]
    fetch=MagicMock(side_effect=AssertionError('Too early'))
    with patch.object(evaluation,'execute_values') as bulk:
        result=evaluation.run(conn,as_of=date(2026,11,26),fetch=fetch)
    assert result['complete']==0 and result['cohorts']==2
    assert [r[4] for r in bulk.call_args.args[2]]==['pending','pending']
    fetch.assert_not_called()


def test_archive_workflow_names_and_guards_match_real_producers():
    from pathlib import Path
    import yaml
    root=Path('.github/workflows')
    data=yaml.load((root/'evaluate_models.yml').read_text(),Loader=yaml.BaseLoader)
    names={yaml.load(p.read_text(),Loader=yaml.BaseLoader).get('name') for p in root.glob('*.yml')}
    assert set(data['on']['workflow_run']['workflows'])<=names
    assert "conclusion == 'success'" in data['jobs']['evaluate']['if']
    assert 'head_repository.full_name == github.repository' in data['jobs']['evaluate']['if']
    assert data['concurrency']['queue']=='max'


def test_published_parameter_picks_match_archived_tie_ranking():
    from meta_models.a_par_model import score_voor_datum
    df=pd.DataFrame(dict(ticker=['B','A','C'],datum=pd.to_datetime(['2026-11-25']*3),
                         koers=[100]*3,rsi=[1,1,1]))
    weights=pd.DataFrame(dict(parameter=['rsi'],ic=[.1]))
    scored=score_voor_datum(df,pd.Timestamp('2026-11-25'),weights,3)
    assert list(scored.ticker)==['A','B','C']
    assert [r['ticker'] for r in ranked_scores(scored,2) if r['picked']]==list(scored.head(2).ticker)


def test_provider_budget_defers_without_discarding_archive_progress(monkeypatch):
    from datetime import datetime,timezone
    from analyses import evaluate_models as evaluation
    row=dict(id='00000000-0000-0000-0000-000000000001',model='a_par',version='v1',target='rank',
             horizon=5,mode='prospective',recorded_at=datetime(2026,11,1,tzinfo=timezone.utc),
             datum=date(2026,10,31),top_n=10,ticker='TEST',score=.3)
    monkeypatch.setattr(evaluation,'load',lambda c:[row])
    conn=MagicMock();conn.cursor.return_value.__enter__.return_value.fetchall.return_value=[]
    fetch=MagicMock(side_effect=AssertionError('Budget exhausted'))
    with patch.object(evaluation,'execute_values') as bulk:
        result=evaluation.run(conn,as_of=date(2026,11,26),fetch=fetch,budget_minutes=0)
    assert result['deferred']==1 and result['complete']==0
    assert [r[4] for r in bulk.call_args.args[2]]==['pending','deferred']
    fetch.assert_not_called()
