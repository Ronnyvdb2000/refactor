from datetime import date, datetime, timezone
from pathlib import Path
import hashlib
import json
import pandas as pd
import pytest
from analyses.evaluate_xgboost import outcome, summarize, build_report, render, load_predictions
from common.xgboost_evaluation import prepare_journal, journal_record, DDL


def bars(n=10):
    return pd.DataFrame({'Open': [100.] * n, 'Close': [110.] * n},
                        index=pd.bdate_range('2026-11-23', periods=n))


def prediction(ticker='A', score=.9, ret=10, status='mature', **extra):
    return dict(run_key='123:1', ticker=ticker, selection_date='2026-11-19',
                predicted_at='2026-11-20T16:00:00+00:00', horizon='10d',
                model_sha256='a'*64, score=score,
                outcome=dict(status=status, gross_return_pct=ret), **extra)


def test_entry_after_publication_not_selection_or_same_day():
    data = pd.concat([pd.DataFrame({'Open': [1.], 'Close': [1000.]}, index=pd.to_datetime(['2026-11-20'])), bars()])
    result = outcome(data, '2026-11-20T16:00:00Z', 10, date(2026, 12, 6))
    assert result['entry_date'] == '2026-11-23'
    assert result['exit_date'] == '2026-12-04'
    assert result['gross_return_pct'] == pytest.approx(10)


def test_current_day_excluded_and_shorter_horizon_never_substituted():
    assert outcome(bars(), '2026-11-20T16:00:00Z', 10, date(2026, 12, 4))['status'] == 'pending'
    assert outcome(bars(), '2026-11-20T16:00:00Z', 30, date(2026, 12, 6))['status'] == 'pending'


@pytest.mark.parametrize('change', ['nan', 'zero', 'duplicate', 'reverse', 'gap'])
def test_invalid_price_history_not_silently_scored(change):
    data = bars()
    if change == 'nan': data.iloc[2, 1] = float('nan')
    if change == 'zero': data.iloc[2, 0] = 0
    if change == 'duplicate': data.index = [data.index[0]] + list(data.index[:-1])
    if change == 'reverse': data = data.iloc[::-1]
    if change == 'gap': data.index = data.index + pd.Timedelta(days=10)
    assert outcome(data, '2026-11-20T16:00:00Z', 10, date(2027, 1, 1))['status'] in {'invalid_prices','price_gap'}


def test_rank_before_outcome_and_do_not_drop_missing_losers():
    report = summarize([prediction(), prediction('B', .5, -20, 'provider_error')], top_n=1)
    assert report['cohorts'][0]['status'] == 'incomplete'
    assert 'top' not in report['cohorts'][0]
    assert report['horizons'][0]['complete_cohorts'] == 0


def test_latest_selection_unique_ticker_baseline_and_costs():
    older = prediction(score=.99, ret=-100)
    older['selection_date'] = '2026-11-18'
    report = summarize([older, prediction(score=.8, ret=10), prediction('B', .9, -10)], top_n=1, cost_bps=50)
    c = report['cohorts'][0]
    assert c['n'] == 2 and c['top']['mean_gross_pct'] == -10
    assert c['baseline']['mean_gross_pct'] == 0
    assert c['top']['mean_net_pct'] == -10.5
    assert c['excess_pct_points'] == -10


def test_horizons_model_fingerprints_and_reruns_separate():
    a = prediction()
    b = prediction(); b['horizon'] = '30d'
    c = prediction(); c['model_sha256'] = 'b'*64; c['run_key'] = '124:1'
    report = summarize([a, b, c])
    assert len(report['cohorts']) == 3
    assert report['horizons'][0]['publication_days'] == 1


def test_short_cohort_reports_actual_top_count():
    assert summarize([prediction()], top_n=10)['cohorts'][0]['top_n'] == 1


def test_old_scores_do_not_get_retroactive_model_identity(tmp_path, monkeypatch):
    path = tmp_path / 'model.pkl'; path.write_bytes(b'fixed model')
    monkeypatch.setenv('GITHUB_RUN_ID','123'); monkeypatch.setenv('GITHUB_RUN_ATTEMPT','2')
    monkeypatch.setenv('GITHUB_SHA','b'*40)
    context = prepare_journal({'10d': path})
    assert context['run_key'] == '123:2'
    assert context['models']['10d'] == hashlib.sha256(b'fixed model').hexdigest()
    calls = []
    class Cursor:
        def execute(self, sql, params): calls.append((sql, params))
    journal_record(Cursor(), dict(ticker='A', datum='2026-11-19', horizon='10d',score=.9), context)
    assert 'UPDATE' not in calls[0][0]
    assert 'ON CONFLICT DO NOTHING' in calls[0][0]
    assert 'clock_timestamp()' in DDL
    assert calls[0][1][-2] == context['models']['10d']


def test_empty_journal_is_pending_not_successful_performance():
    report = build_report([], as_of=date(2026, 11, 20))
    assert report['predictions'] == 0
    assert all(h['status'] == 'pending' for h in report['horizons'])
    assert 'nog geen volledig' in render(report)
    json.dumps(report, allow_nan=False)


def test_provider_error_reported_not_zero_return():
    def fail(*a): raise RuntimeError('provider')
    report = build_report([prediction()], as_of=date(2026, 12, 20), fetch=fail)
    assert report['cohorts'][0]['states'] == {'provider_error': 1}
    assert report['horizons'][0]['status'] == 'pending'


def test_build_report_uses_publication_time_and_provider_adjusted_bars():
    calls = []
    def fetch(*args): calls.append(args); return bars()
    report = build_report([prediction()], as_of=date(2026, 12, 6), fetch=fetch)
    assert calls == [('A','2026-11-21','2026-12-06')]
    assert report['horizons'][0]['top_mean_net_pct'] == pytest.approx(10)


def test_recent_prediction_needs_no_provider_request():
    def fail(*a): pytest.fail('should wait')
    result = build_report([prediction()], as_of=date(2026, 11, 21), fetch=fail)
    assert result['cohorts'][0]['states'] == {'pending': 1}


def test_db_connection_is_readonly_and_missing_journal_allowed():
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self, query,*args): assert not query.startswith(('INSERT','CREATE','UPDATE'))
        def fetchone(self):
            return responses.pop(0)
    class Conn:
        def set_session(self, **kw): assert kw == dict(readonly=True,isolation_level='REPEATABLE READ')
        def cursor(self, **kw): return Cursor()
    responses = [{'n':3816},{'name':None}]
    assert load_predictions(Conn(), date(2026,11,20)) == ([],3816)


def test_nonfinite_inputs_and_missing_timestamp_fail():
    with pytest.raises(ValueError): summarize([], cost_bps=float('nan'))
    r = prediction(); r['predicted_at'] = '2026-11-20'
    with pytest.raises(ValueError): build_report([r], as_of=date(2026,12,20))


@pytest.mark.parametrize('fail_journal,accepted', [(False,True),(True,True),(False,False)])
def test_scorer_commits_journal_with_only_accepted_scores(monkeypatch, tmp_path, fail_journal, accepted):
    from models import score_xgboost as scorer
    import common.xgboost_evaluation as journal
    path = tmp_path/'model.pkl'; path.write_bytes(b'model')
    monkeypatch.setattr(scorer,'MODEL_FILES',{'10d':str(path)})
    calls = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def execute(self, sql, params=None):
            calls.append(sql)
            if 'INSERT INTO public.xgboost_prediction_log' in sql and fail_journal:
                raise RuntimeError('journal failed')
        def fetchone(self): return (1,) if accepted else None
    class Conn:
        def cursor(self): return Cursor()
        def commit(self): calls.append('COMMIT')
        def rollback(self): calls.append('ROLLBACK')
    record = dict(ticker='A', datum='2026-11-19', horizon='10d',score=.9,
                  model_versie='xgboostV3',strategieen='test')
    schema={'xgboost_scores':['ticker','datum','horizon','score','model_versie','strategieen','rang','n_gescoord']}
    if fail_journal:
        with pytest.raises(RuntimeError): scorer.sla_scores_op(Conn(),[record],schema)
        assert 'ROLLBACK' in calls and 'COMMIT' not in calls
    else:
        assert scorer.sla_scores_op(Conn(),[record],schema) == int(accepted)
        assert 'COMMIT' in calls
    assert any('INSERT INTO public.xgboost_prediction_log' in q for q in calls) == accepted
