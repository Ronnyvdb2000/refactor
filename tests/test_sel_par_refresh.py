from datetime import date
from maintenance.refresh_sel_par import readiness, sources,wait_for_sources
import pytest


def run(path, created, status='completed', conclusion='success', event='schedule', branch='main', ident=1):
    return dict(path='.github/workflows/' + path, created_at=created,
                status=status, conclusion=conclusion, event=event,
                head_branch=branch, id=ident)


def test_missing_scheduled_run_blocks():
    report = readiness({'scan.yml': ['0 20 * * 1-5']}, [], date(2026, 11, 20))
    assert len(report['missing']) == 1


def test_weekend_does_not_require_weekday_scan():
    assert readiness({'scan.yml': ['0 20 * * 1-5']}, [], date(2026, 11, 21)) == {
        'missing': [], 'active': [], 'failed_sources': []}


def test_queued_run_blocks_and_failed_run_is_reported():
    config = {'scan.yml': ['0 20 * * *']}
    queued = run('scan.yml', '2026-11-20T20:05:00Z', 'queued', None)
    assert readiness(config, [queued], date(2026, 11, 20))['active']
    failed = run('scan.yml', '2026-11-20T20:05:00Z', conclusion='failure')
    report = readiness(config, [failed], date(2026, 11, 20))
    assert not report['active'] and not report['missing']
    assert report['failed_sources'][0]['conclusion'] == 'failure'


def test_previous_day_and_other_branch_do_not_satisfy_schedule():
    config = {'scan.yml': ['0 20 * * *']}
    rows = [run('scan.yml', '2026-11-19T20:05:00Z'),
            run('scan.yml', '2026-11-20T20:05:00Z', branch='experiment')]
    assert readiness(config, rows, date(2026, 11, 20))['missing']


def test_morning_run_does_not_satisfy_evening_run():
    config = {'scan.yml': ['0 10 * * *', '0 20 * * *']}
    rows = [run('scan.yml', '2026-11-20T10:05:00Z')]
    assert len(readiness(config, rows, date(2026, 11, 20))['missing']) == 1


def test_late_evening_run_after_midnight_counts():
    config = {'scan.yml': ['0 23 * * *']}
    rows = [run('scan.yml', '2026-11-21T00:15:00Z')]
    assert not readiness(config, rows, date(2026, 11, 20))['missing']


def test_source_delayed_until_after_refresh_start_can_still_complete():
    config = {'scan.yml': ['0 23 * * *']}
    rows = [run('scan.yml', '2026-11-21T05:15:00Z')]
    assert not readiness(config, rows, date(2026, 11, 20))['missing']


def test_bridge_and_legacy_builders_excluded_but_scans_included():
    config = sources()
    assert 'tradingagentsbridge.yml' not in config
    assert {'run_00vcp.yml', 'run_01hoogl_full.yml'} <= config.keys()
    assert not {'run_forward_returns.yml','run_generieke_technicals.yml',
                'run_generieke_fundamentals.yml'} & config.keys()


def test_manual_completed_source_can_satisfy_a_scheduled_slot():
    rows=[run('scan.yml','2026-11-20T20:05:00Z',event='workflow_dispatch')]
    assert not readiness({'scan.yml':['0 20 * * *']},rows,date(2026,11,20))['missing']


def test_next_days_morning_scan_does_not_block_previous_day():
    rows=[run('scan.yml','2026-11-20T06:35:00Z'),
          run('scan.yml','2026-11-21T06:35:00Z',status='in_progress')]
    report=readiness({'scan.yml':['30 6 * * *']},rows,date(2026,11,20))
    assert not report['active'] and not report['missing']


def test_missing_source_is_recorded_after_bounded_wait_but_not_silently_dropped(monkeypatch):
    import maintenance.refresh_sel_par as module
    monkeypatch.setattr(module,'sources',lambda:{'scan.yml':['0 20 * * *']})
    monkeypatch.setattr(module,'get_runs',lambda *a:[])
    report=wait_for_sources('repo','unused',date(2026,11,20),0)
    assert report['missing']==['scan.yml@2026-11-20T20:00:00+00:00']
    assert not report['active']


def test_active_source_still_blocks_transfer_after_timeout(monkeypatch):
    import maintenance.refresh_sel_par as module
    monkeypatch.setattr(module,'sources',lambda:{'scan.yml':['0 20 * * *']})
    monkeypatch.setattr(module,'get_runs',lambda *a:[run('scan.yml','2026-11-20T20:05:00Z',status='in_progress')])
    with pytest.raises(RuntimeError,match='nog bezig'):
        wait_for_sources('repo','unused',date(2026,11,20),0)
