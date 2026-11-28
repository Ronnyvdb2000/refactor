"""Central release cannot be inferred from an artifact or a completed provider loop."""
from copy import deepcopy
from datetime import date,datetime,timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from common.run_manifest import validate_release,manifest_hash,assert_ready,ready_input
from maintenance import refresh_sel_par as refresh

DAY='2026-11-25'


def context():
    return dict(schema_version=2,status='ready',data_ready=True,selection_cutoff=DAY,
        generated_at=datetime.now(timezone.utc).isoformat(),source_run_id='123',source_run_attempt='2',source_sha='a'*40,
        rows_written=8,rows_by_date={DAY:8},readiness=dict(minimum_coverage=.8,latest_selection_date=DAY,
        coverage_by_selection_date={DAY:dict(expected_tickers=10,valid_tickers=8,excluded_tickers=2,pending_tickers=0)},
        required_parts=dict(selection_seed=True,source_scans=True,feature_enrichment=True)))


def released(ctx=None):
    ctx=ctx or context()
    return dict(input_released=True,data_ready=True,enrichment=dict(complete_execution=True),
                run_manifest=ctx,manifest_hash=manifest_hash(ctx),bronruns=dict(missing=[],active=[],failed_sources=[]))


def connection(value, *, locked=True):
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[(locked,),(value,)];cur.rowcount=1
    return conn,cur


def test_confirmed_missing_stocks_can_be_excluded_at_eighty_percent():
    assert validate_release(context())['data_ready'] is True


@pytest.mark.parametrize('change',['parts','pending','coverage','counts','cutoff','hash','legacy','released','enrichment','missing','attempt','source','superseded'])
def test_incomplete_wrong_or_superseded_source_cannot_authorize_a_consumer(change):
    ctx=context();value=released(ctx)
    if change=='parts':ctx['readiness']['required_parts']['feature_enrichment']=False
    if change=='pending':ctx['readiness']['coverage_by_selection_date'][DAY].update(excluded_tickers=1,pending_tickers=1)
    if change=='coverage':ctx['readiness']['minimum_coverage']=.7
    if change=='counts':ctx['readiness']['coverage_by_selection_date'][DAY]['expected_tickers']=11
    if change=='cutoff':ctx['selection_cutoff']='2026-12-01'
    if change=='hash':value['manifest_hash']='wrong'
    if change=='legacy':ctx['schema_version']=1
    if change=='released':value['data_ready']=False
    if change=='enrichment':value['enrichment']['complete_execution']=False
    if change=='missing':value={}
    if change=='attempt':value['run_manifest']=dict(ctx,source_run_attempt='1')
    if change=='source':value['run_manifest']=dict(ctx,source_sha='b'*40)
    if change=='superseded':value['run_manifest']=dict(ctx,source_run_id='124')
    conn,cur=connection(value)
    child=MagicMock()
    with pytest.raises((ValueError,RuntimeError)):
        with ready_input(conn,ctx):child()
    child.assert_not_called()
    assert 'pg_advisory_unlock_shared' in cur.execute.call_args.args[0]


def test_shared_lock_covers_the_entire_child_and_releases_on_failure():
    ctx=context();conn,cur=connection(released(ctx))
    with pytest.raises(RuntimeError,match='child'):
        with ready_input(conn,ctx):
            assert not any('unlock' in call.args[0] for call in cur.execute.call_args_list)
            assert cur.execute.call_args.args[0].startswith('SELECT resultaat')
            raise RuntimeError('child')
    assert 'unlock_shared' in cur.execute.call_args.args[0]


def test_busy_writer_blocks_before_reading_or_starting_a_consumer():
    conn,cur=connection(released(),locked=False)
    with pytest.raises(RuntimeError,match='bijgewerkt'):
        with ready_input(conn,context()):raise AssertionError('must not execute')
    assert not any('SELECT resultaat' in call.args[0] for call in cur.execute.call_args_list)


def test_release_updates_ready_only_after_manifest_and_row_validation(monkeypatch):
    ctx=context();value=released(ctx);value.update(input_released=False,data_ready=False)
    conn,cur=connection(value)
    def publisher(*args,**kwargs):
        assert kwargs['required_parts']==dict(selection_seed=True,source_scans=True,feature_enrichment=True)
        assert not any(call.args[0].startswith('UPDATE') for call in cur.execute.call_args_list)
        return ctx
    monkeypatch.setattr('maintenance.sel_par_direct.publish_input_manifest',publisher)
    refresh.release_input(conn,date.fromisoformat(DAY))
    updates=[call for call in cur.execute.call_args_list if call.args[0].startswith('UPDATE')]
    assert len(updates)==2 and 'public.sel_par_runs' in updates[-1].args[0]
    payload=updates[-1].args[1][0].adapted
    assert payload['data_ready'] is True and payload['manifest_hash']==manifest_hash(ctx)
    conn.commit.assert_not_called()  # Commit belongs to the outer transaction.


@pytest.mark.parametrize('part',['missing','active','failed_sources'])
def test_failed_or_unfinished_required_scan_never_releases_partial_selections(monkeypatch,part):
    value=released();value['bronruns'][part]=['source']
    conn,cur=connection(value)
    publish=MagicMock();monkeypatch.setattr('maintenance.sel_par_direct.publish_input_manifest',publish)
    with pytest.raises(RuntimeError,match='Brononderdelen'):refresh.release_input(conn,date.fromisoformat(DAY))
    publish.assert_not_called()
    assert not any(call.args[0].startswith('UPDATE') for call in cur.execute.call_args_list)


def test_waiting_features_are_not_reported_as_confirmed_exclusions(tmp_path,monkeypatch):
    from maintenance import sel_par_direct as direct
    monkeypatch.setenv('FEATURE_BUILD_MANIFEST',str(tmp_path/'manifest.json'))
    monkeypatch.setenv('GITHUB_RUN_ID','123');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','2');monkeypatch.setenv('GITHUB_SHA','a'*40)
    rows=[dict(ticker=f'T{i}',datum=DAY,bar_datum=DAY,selection_seeded=True,koers_selectiedatum=100.,verrijking_status={},
               **{'tech_'+f:1. for f in direct.REQUIRED}) for i in range(10)]
    rows[-1]['tech_'+direct.REQUIRED[0]]=None
    conn=MagicMock();conn.cursor.return_value.__enter__.return_value.fetchall.return_value=rows
    parts=dict(selection_seed=True,source_scans=True,feature_enrichment=True)
    with pytest.raises(ValueError,match='Supabase-vrijgave'):
        direct.publish_input_manifest(conn,date.fromisoformat(DAY),required_parts=parts)
    rows[-1]['verrijking_status']={'data_review':'missing quote'}
    # Fix time for the existing freshness check; this is a synthetic historical build.
    monkeypatch.setattr('common.feature_pipeline.load_feature_context',lambda:None)
    result=direct.publish_input_manifest(conn,date.fromisoformat(DAY),required_parts=parts)
    assert result['readiness']['coverage_by_selection_date'][DAY]['excluded_tickers']==1
    assert result['rows_written']==9


def test_recent_but_wrong_weekday_cohort_cannot_claim_current_peildatum():
    ctx=context();ctx['selection_cutoff']='2026-11-26'
    with pytest.raises(ValueError,match='peildatum'):validate_release(ctx)


def test_resumed_day_does_not_skip_revalidation_on_legacy_release():
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value=({'enrichment':{'complete_execution':True},'input_released':True},)
    assert refresh.completed_refresh(conn,date.fromisoformat(DAY)) is None


def test_launcher_passes_arguments_only_inside_a_confirmed_central_release(monkeypatch):
    import psycopg2
    from maintenance import run_ready
    ctx=context();conn,cur=connection(released(ctx))
    monkeypatch.setattr(run_ready,'load_feature_context',lambda:ctx)
    monkeypatch.setenv('SUPABASE_DB_URL','offline')
    monkeypatch.setattr(psycopg2,'connect',lambda *args,**kwargs:conn)
    monkeypatch.setattr('sys.argv',['run_ready','meta_models.a_par_model','--geen-notify'])
    def run(command,check):
        assert not any('unlock' in c.args[0] for c in cur.execute.call_args_list)
        assert command[-3:]==['-m','meta_models.a_par_model','--geen-notify']
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(run_ready.subprocess,'run',run)
    assert run_ready.main()==7
    conn.close.assert_called_once()


def test_launcher_refuses_artifact_when_database_release_disappears(monkeypatch):
    import psycopg2
    from maintenance import run_ready
    ctx=context();conn,cur=connection({})
    monkeypatch.setattr(run_ready,'load_feature_context',lambda:ctx)
    monkeypatch.setenv('SUPABASE_DB_URL','offline')
    monkeypatch.setattr(psycopg2,'connect',lambda *args,**kwargs:conn)
    monkeypatch.setattr('sys.argv',['run_ready','models.xgboostV4'])
    child=MagicMock();monkeypatch.setattr(run_ready.subprocess,'run',child)
    with pytest.raises(RuntimeError,match='vrijgegeven'):run_ready.main()
    child.assert_not_called();conn.close.assert_called_once()


def test_two_missing_quotes_are_confirmed_exclusions_without_blocking_eighty_percent(tmp_path,monkeypatch):
    from maintenance import sel_par_direct as direct
    from common.feature_pipeline import load_feature_context
    path=tmp_path/'manifest.json'
    monkeypatch.setenv('FEATURE_BUILD_MANIFEST',str(path))
    monkeypatch.setenv('GITHUB_RUN_ID','123');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','2');monkeypatch.setenv('GITHUB_SHA','a'*40)
    for name in ['FEATURE_EXPECTED_RUN_ID','FEATURE_EXPECTED_RUN_ATTEMPT','FEATURE_EXPECTED_SHA']:
        monkeypatch.delenv(name,raising=False)
    # Use the current closed weekday to exercise the real manifest validator.
    from common.feature_pipeline import latest_closed_feature_day
    from common.model_inputs import technical_day
    day=latest_closed_feature_day();bar=str(technical_day(day).date())
    rows=[dict(ticker=f'T{i}',datum=day.isoformat(),bar_datum=bar,selection_seeded=True,koers_selectiedatum=100. if i<8 else None,
               verrijking_status={},**{'tech_'+f:1. for f in direct.REQUIRED}) for i in range(10)]
    conn=MagicMock();conn.cursor.return_value.__enter__.return_value.fetchall.return_value=rows
    manifest=direct.publish_input_manifest(conn,day,required_parts=dict(selection_seed=True,source_scans=True,feature_enrichment=True))
    assert manifest['schema_version']==2 and load_feature_context()==manifest
    counts=manifest['readiness']['coverage_by_selection_date'][day.isoformat()]
    assert counts==dict(expected_tickers=10,valid_tickers=8,excluded_tickers=2,pending_tickers=0)


@pytest.mark.parametrize('report',[{},dict(missing=None,active=[],failed_sources=[])])
def test_missing_or_malformed_source_confirmation_is_not_ready(report):
    from common.run_manifest import sources_ready
    assert sources_ready(report) is False


def test_missing_seed_row_is_pending_even_when_ninety_percent_are_valid(tmp_path,monkeypatch):
    from maintenance import sel_par_direct as direct
    monkeypatch.setenv('FEATURE_BUILD_MANIFEST',str(tmp_path/'manifest.json'))
    rows=[dict(ticker=f'T{i}',datum=DAY,bar_datum=DAY,selection_seeded=True,koers_selectiedatum=100.,verrijking_status={},
               **{'tech_'+f:1. for f in direct.REQUIRED}) for i in range(10)]
    rows[-1].update(selection_seeded=False,koers_selectiedatum=None,bar_datum=None)
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value;cur.fetchall.return_value=rows
    with pytest.raises(ValueError,match='Supabase-vrijgave'):
        direct.publish_input_manifest(conn,date.fromisoformat(DAY),required_parts=dict(selection_seed=True,source_scans=True,feature_enrichment=True))
    sql=cur.execute.call_args.args[0]
    assert 'FROM public.selecties' in sql and 'LEFT JOIN public.sel_par' in sql
