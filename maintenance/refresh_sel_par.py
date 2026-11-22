"""Refresh sel_par once per closed UTC selection day, after upstream workflows.

Only the research tables are written; TradingAgents Bridge is excluded.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import yaml

from maintenance.audit_workflows import weekly_slots


ROOT = Path(__file__).resolve().parents[1]
EXTRA_SOURCES = {
    'weekly_run.yml', 'weekly_run_x.yml', 'a_trade.yml', 'a_trade_q.yml',
}


def sources(root=ROOT):
    result = {}
    for path in sorted((root / '.github/workflows').glob('*.y*ml')):
        if not (path.name.startswith(('run_00', 'run_01')) or path.name in EXTRA_SOURCES):
            continue
        data = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        triggers = data.get('on') or {}
        result[path.name] = [x['cron'] for x in triggers.get('schedule', [])]
    return result


def expected_starts(crons, cutoff):
    """All scheduled starts on the closed selection day, including morning scans."""
    # Include all scheduled strategies on the selection day, including morning scans.
    weekday = (cutoff.weekday() + 1) % 7
    return sorted({datetime(cutoff.year, cutoff.month, cutoff.day, hour, minute,
                            tzinfo=timezone.utc)
                   for cron in crons for day, hour, minute in weekly_slots(cron)
                   if day == weekday})


def readiness(config, runs, cutoff):
    missing, active, failures = [], [], []
    day_start = datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    for name, crons in config.items():
        rows = [r for r in runs if r.get('path', '').split('/')[-1] == name
                and r.get('head_branch') == 'main']
        for due in expected_starts(crons, cutoff):
            # A late schedule can start after midnight. Do not mistake a morning run
            # for the later scheduled run of a workflow with multiple daily schedules.
            following = [x for x in expected_starts(crons, cutoff) if x > due]
            next_day_starts = expected_starts(crons, cutoff + timedelta(days=1))
            until = min(following) if following else (
                min(next_day_starts) if next_day_starts else day_end + timedelta(days=1))
            candidates = [r for r in rows if r.get('event') == 'schedule'
                          and due <= datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')) < until]
            if not candidates:
                missing.append(f'{name}@{due.isoformat()}')
        for run in rows:
            created = datetime.fromisoformat(run['created_at'].replace('Z', '+00:00'))
            if created < day_start:
                # An older still-active source also blocks; completed old runs don't.
                if run['status'] == 'completed':
                    continue
            if run['status'] != 'completed':
                active.append({'workflow': name, 'id': run['id'], 'status': run['status']})
            elif day_start <= created and run.get('conclusion') != 'success':
                failures.append({'workflow': name, 'id': run['id'], 'conclusion': run.get('conclusion')})
    return {'missing': missing, 'active': active, 'failed_sources': failures}


def get_runs(repository, token, cutoff):
    rows = []
    # Include an extra day to catch older queued source runs. Paginate, never silently
    # declare readiness based on only the first 100 runs.
    since = (cutoff - timedelta(days=1)).isoformat()
    for page in range(1, 31):
        query = urlencode({'branch': 'main', 'created': '>=' + since,
                           'per_page': 100, 'page': page})
        url = f'https://api.github.com/repos/{quote(repository, safe="/")}/actions/runs?{query}'
        request = Request(url, headers={'Authorization': 'Bearer ' + token,
                                      'Accept': 'application/vnd.github+json',
                                      'X-GitHub-Api-Version': '2022-11-28'})
        with urlopen(request, timeout=30) as response:
            batch = json.load(response)['workflow_runs']
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise RuntimeError('Meer dan 3000 runs; broncontrole is niet volledig')


def wait_for_sources(repository, token, cutoff, wait_minutes):
    deadline = time.monotonic() + wait_minutes * 60
    config = sources()
    while True:
        report = readiness(config, get_runs(repository, token, cutoff), cutoff)
        if not report['missing'] and not report['active']:
            return report
        print(json.dumps(report), flush=True)
        if time.monotonic() >= deadline:
            raise RuntimeError('Bronruns ontbreken of zijn nog bezig; sel_par niet gewijzigd')
        time.sleep(30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--wait-minutes', type=int, default=120)
    parser.add_argument('--max-tickers', type=int, default=2000)
    args = parser.parse_args()
    # Workflow-created time is fixed so waiting across midnight cannot change cutoff.
    started = os.environ.get('REFRESH_STARTED_AT')
    now = datetime.fromisoformat(started.replace('Z', '+00:00')) if started else datetime.now(timezone.utc)
    cutoff = now.astimezone(timezone.utc).date() - timedelta(days=1)
    report = wait_for_sources(os.environ['GITHUB_REPOSITORY'], os.environ['GH_TOKEN'],
                              cutoff, args.wait_minutes)
    # A completed but failed strategy can have stored partial selections. Preserve
    # those; record its failure rather than claiming the dataset is complete.
    for failure in report['failed_sources']:
        print('::warning::Bronrun niet geslaagd: ' + json.dumps(failure), flush=True)
    import psycopg2
    from psycopg2.extras import Json
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'], connect_timeout=30) as conn:
        conn.set_session(isolation_level='REPEATABLE READ')
        with conn.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '15min'")
            cursor.execute("SET LOCAL lock_timeout = '30s'")
            cursor.execute('SELECT public.zaai_sel_par(%s,%s,%s)',
                           (cutoff, args.force, Json(report)))
            result = cursor.fetchone()[0]
            # Preserve strategy-specific inputs inside sel_par for all readers.
            # This is ordinary row enrichment; no production function/schema change.
            snapshot_sql = ROOT / 'maintenance/sql/20261122_sel_par_selection_snapshots.sql'
            cursor.execute(snapshot_sql.read_text())
    from maintenance.sel_par_direct import enrich
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'], connect_timeout=30) as conn:
        # A session-level lock spans per-ticker commits and all provider calls.
        with conn.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(741206,11)')
            if not cursor.fetchone()[0]:
                raise RuntimeError('Een directe sel_par-verrijking is al bezig')
        try:
            result['enrichment'] = enrich(conn, cutoff, max_tickers=args.max_tickers)
            result['pipeline_version'] = 2
            with conn.cursor() as cursor:
                cursor.execute('UPDATE public.sel_par_runs SET voltooid_op=clock_timestamp(), '
                               'resultaat=%s WHERE selecties_tot=%s', (Json(result),cutoff))
            conn.commit()
        finally:
            conn.rollback()
            with conn.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(741206,11)')
            conn.commit()
    output = Path('results/sel_par_refresh.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps(result, default=str), flush=True)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as stream:
            stream.write('### sel_par\n\n```json\n' + json.dumps(result, indent=2, default=str) + '\n```\n')
    if not result['enrichment']['complete_execution']:
        raise SystemExit('Onvolledige verrijking; opgeslagen voortgang blijft behouden, zie rapport')
    from maintenance.sel_par_direct import publish_input_manifest
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'], connect_timeout=30) as conn:
        publish_input_manifest(conn,cutoff)
        with conn.cursor() as cursor:
            cursor.execute("UPDATE public.sel_par SET verrijking_status=verrijking_status || "
                           "'{\"invoer_vrijgegeven\":true}'::jsonb WHERE selectiedatum<=%s "
                           "AND coalesce(verrijking_status->>'invoer_vrijgegeven','false')<>'true'",
                           (cutoff,))


if __name__ == '__main__':
    main()
