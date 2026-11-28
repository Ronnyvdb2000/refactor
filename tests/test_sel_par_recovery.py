from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
import json
from test_run_manifest import context as release_context, released

import pytest
from maintenance import refresh_sel_par as refresh
from maintenance import score_once

DAY = date(2026, 11, 25)


@pytest.fixture(autouse=True)
def ready_context_file(tmp_path,monkeypatch):
    path=tmp_path/'feature_build.json'
    path.write_text(json.dumps(release_context()))
    monkeypatch.setenv('FEATURE_BUILD_MANIFEST',str(path))
    for key in ['FEATURE_EXPECTED_RUN_ID','FEATURE_EXPECTED_RUN_ATTEMPT','FEATURE_EXPECTED_SHA']:
        monkeypatch.delenv(key,raising=False)


def db(result, locked=True):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    value=released(score_once.load_feature_context());value.update(result)
    cur.fetchone.side_effect = [(locked,), (True,), (value,)]
    cur.rowcount = 1
    return conn, cur


def test_success_is_recorded_and_repeated_day_has_no_side_effects():
    conn, cur = db({'input_released': True})
    runner = MagicMock(return_value=SimpleNamespace(returncode=0))
    assert score_once.score(conn, DAY, 'a_par', ['command'], runner) == 0
    runner.assert_called_once_with(['command'], check=False)
    updates = [(args[0], args[1]) for args, _ in cur.execute.call_args_list if args[0].startswith('UPDATE')]
    assert [params[0].adapted['a_par']['status'] for _, params in updates] == ['running', 'completed']
    assert all(params[1] == DAY for _, params in updates)
    assert 'pg_advisory_unlock' in cur.execute.call_args.args[0]

    conn, cur = db({'input_released': True, 'scoring': {'a_par': {'status': 'completed'}}})
    runner.reset_mock()
    assert score_once.score(conn, DAY, 'a_par', ['command'], runner) == 0
    runner.assert_not_called()
    assert not any(args[0].startswith('UPDATE') for args, _ in cur.execute.call_args_list)


@pytest.mark.parametrize('state', ['running', 'needs_review'])
def test_uncertain_attempt_requires_review_instead_of_duplicate_message(state):
    conn, cur = db({'input_released': True, 'scoring': {'a_par': {'status': state}}})
    runner = MagicMock()
    with pytest.raises(RuntimeError, match='handmatig herstel'):
        score_once.score(conn, DAY, 'a_par', ['command'], runner)
    runner.assert_not_called()
    assert 'pg_advisory_unlock' in cur.execute.call_args.args[0]


def test_failed_child_is_not_recorded_as_completed():
    conn, cur = db({'input_released': True})
    assert score_once.score(conn, DAY, 'xgboost', ['cmd'],
                            MagicMock(return_value=SimpleNamespace(returncode=2))) == 2
    update = [args for args, _ in cur.execute.call_args_list if args[0].startswith('UPDATE')][-1]
    assert update[1][0].adapted['xgboost']['status'] == 'needs_review'


def test_unreleased_input_and_concurrent_consumer_cannot_run():
    runner = MagicMock()
    conn, _ = db({'input_released': False})
    with pytest.raises(RuntimeError, match='vrijgegeven'):
        score_once.score(conn, DAY, 'a_meta', ['cmd'], runner)
    conn, _ = db({}, locked=False)
    with pytest.raises(RuntimeError, match='al bezig'):
        score_once.score(conn, DAY, 'a_meta', ['cmd'], runner)
    runner.assert_not_called()


@pytest.mark.parametrize('result,ready', [
    ({}, False), ({'enrichment': {'complete_execution': False}}, False),
    ({'enrichment': {'complete_execution': True}}, False),
    ({'enrichment': {'complete_execution': True}, 'input_released': True}, False),
    (released(), True),
])
def test_seed_or_enrichment_alone_is_not_success(result, ready):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (result,)
    assert bool(refresh.completed_refresh(conn, DAY)) is ready


def test_completed_day_only_revalidates_and_publishes_manifest(monkeypatch):
    import psycopg2
    monkeypatch.setattr('sys.argv', ['refresh', '--wait-minutes', '0', '--require-sources'])
    monkeypatch.setenv('SUPABASE_DB_URL', 'unused')
    monkeypatch.setenv('REFRESH_STARTED_AT', '2026-11-26T08:15:00Z')
    monkeypatch.setattr(psycopg2, 'connect', MagicMock())
    monkeypatch.setattr(refresh, 'completed_refresh', lambda *a: {'ready': True})
    release = MagicMock()
    sources = MagicMock(side_effect=AssertionError('geen bron- of providerwerk bij herhaling'))
    monkeypatch.setattr(refresh, 'release_input', release)
    monkeypatch.setattr(refresh, 'wait_for_sources', sources)
    refresh.main()
    assert release.call_args.args[1] == DAY
    sources.assert_not_called()


def test_late_source_stops_attempt_before_transfer_and_can_recover(monkeypatch):
    import psycopg2
    monkeypatch.setattr('sys.argv', ['refresh', '--wait-minutes', '0', '--require-sources'])
    monkeypatch.setenv('SUPABASE_DB_URL', 'unused')
    monkeypatch.setenv('REFRESH_STARTED_AT', '2026-11-26T08:15:00Z')
    monkeypatch.setenv('GITHUB_REPOSITORY', 'Ronnyvdb2000/refactor')
    monkeypatch.setenv('GH_TOKEN', 'unused')
    connect = MagicMock()
    monkeypatch.setattr(psycopg2, 'connect', connect)
    monkeypatch.setattr(refresh, 'completed_refresh', lambda *a: None)
    monkeypatch.setattr(refresh, 'wait_for_sources', lambda *a: {'missing': ['scan'], 'active': [], 'failed_sources': []})
    with pytest.raises(RuntimeError, match='Bronruns ontbreken'):
        refresh.main()
    assert connect.call_count == 1  # only the completion check, no transfer


def test_manual_model_run_requires_readiness_before_child_process(monkeypatch):
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'workflow_dispatch')
    monkeypatch.delenv('FEATURE_BUILD_MANIFEST', raising=False)
    monkeypatch.setattr('sys.argv', ['score_once', 'a_par', '--geen-notify'])
    runner = MagicMock()
    monkeypatch.setattr(score_once.subprocess, 'run', runner)
    with pytest.raises(RuntimeError, match='manifest'):
        score_once.main()
    runner.assert_not_called()


def test_partial_enrichment_can_resume_then_release(monkeypatch, tmp_path):
    import psycopg2
    import maintenance.sel_par_direct as direct
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['refresh', '--wait-minutes', '0', '--require-sources'])
    for key, value in {'SUPABASE_DB_URL': 'unused', 'GITHUB_REPOSITORY': 'Ronnyvdb2000/refactor',
                       'GH_TOKEN': 'unused', 'REFRESH_STARTED_AT': '2026-11-26T12:15:00Z'}.items():
        monkeypatch.setenv(key, value)
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = conn.cursor.return_value.__enter__.return_value
    def execute(sql, params=None):
        if 'SELECT resultaat' in sql:
            cur.fetchone.return_value = ({'scoring': {}},)
        elif 'zaai_sel_par' in sql:
            cur.fetchone.return_value = ({'status': 'al_ververst'},)
        elif 'pg_try_advisory' in sql:
            cur.fetchone.return_value = (True,)
    cur.execute.side_effect = execute
    monkeypatch.setattr(psycopg2, 'connect', lambda *a, **kw: conn)
    monkeypatch.setattr(refresh, 'completed_refresh', lambda *a: None)
    monkeypatch.setattr(refresh, 'wait_for_sources', lambda *a: {'missing': [], 'active': [], 'failed_sources': []})
    enrich = MagicMock(side_effect=[{'complete_execution': False}, {'complete_execution': True}])
    release = MagicMock()
    monkeypatch.setattr(direct, 'enrich', enrich)
    monkeypatch.setattr(refresh, 'release_input', release)
    with pytest.raises(SystemExit, match='Onvolledige verrijking'):
        refresh.main()
    release.assert_not_called()
    refresh.main()
    assert enrich.call_count == 2
    release.assert_called_once_with(conn.__enter__.return_value, DAY)


def test_refresh_and_scoring_cannot_overlap():
    conn, cur = db({'input_released': True})
    cur.fetchone.side_effect = [(True,), (False,)]
    runner = MagicMock()
    with pytest.raises(RuntimeError, match='wordt nog bijgewerkt'):
        score_once.score(conn, DAY, 'a_par', ['cmd'], runner)
    runner.assert_not_called()
    assert 'pg_advisory_unlock' in cur.execute.call_args.args[0]


def test_ready_manual_model_run_keeps_arguments_and_uses_once_guard(monkeypatch):
    import psycopg2
    monkeypatch.setenv('GITHUB_EVENT_NAME','workflow_dispatch')
    monkeypatch.setenv('SUPABASE_DB_URL','offline')
    monkeypatch.setattr('sys.argv',['score_once','a_par','--geen-notify'])
    monkeypatch.setattr(score_once,'load_feature_context',lambda:dict(selection_cutoff='2026-11-20'))
    monkeypatch.setattr(psycopg2,'connect',MagicMock())
    guarded=MagicMock(return_value=0)
    monkeypatch.setattr(score_once,'score',guarded)
    assert score_once.main()==0
    assert guarded.call_args.args[1:3]==('2026-11-20','a_par')
    assert guarded.call_args.args[3][-3:]==['-m','meta_models.a_par_model','--geen-notify']
