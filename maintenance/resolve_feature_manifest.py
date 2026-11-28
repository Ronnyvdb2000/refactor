"""Resolve one trusted successful feature build, including manual/scheduled runs.

Automatic consumers stay pinned to the triggering run and attempt. Manual callers
select the latest completed build; a failed/latest stale build never falls back.
Only the small JSON manifest is extracted; archive paths are never written.
"""
import io
import json
import os
from pathlib import Path
import zipfile

import requests

from common.feature_pipeline import load_feature_context, context_summary

WORKFLOW = 'refresh_sel_par.yml'
LIMIT = 1024 * 1024


def resolve(session, repository, run_id=None):
    if not repository or len(repository.split('/')) != 2:
        raise ValueError('Repository ontbreekt')
    base = f'https://api.github.com/repos/{repository}'

    def get(path):
        response = session.get(base + path, timeout=30)
        response.raise_for_status()
        return response.json()

    workflow = get(f'/actions/workflows/{WORKFLOW}')
    if run_id:
        run = get(f'/actions/runs/{int(run_id)}')
    else:
        runs = get(f"/actions/workflows/{WORKFLOW}/runs?branch=main&status=completed&per_page=1")['workflow_runs']
        if not runs:
            raise ValueError('Geen afgeronde featurebouw beschikbaar')
        run = runs[0]
    if (run['workflow_id'] != workflow['id'] or run['conclusion'] != 'success'
            or run['status'] != 'completed' or run['head_branch'] != 'main'
            or run['head_repository']['full_name'] != repository):
        raise ValueError('Featurebouw niet geslaagd of niet uit vertrouwde main')
    artifacts = get(f"/actions/runs/{run['id']}/artifacts?per_page=100")['artifacts']
    matches = [a for a in artifacts if a['name'] == 'feature-build-manifest' and not a['expired']]
    if len(matches) != 1 or matches[0]['size_in_bytes'] > LIMIT:
        raise ValueError('Eenduidig featuremanifest ontbreekt of is te groot')
    response = session.get(base + f"/actions/artifacts/{matches[0]['id']}/zip", timeout=30, stream=True)
    response.raise_for_status()
    chunks = []; size = 0
    for chunk in response.iter_content(65536):
        size += len(chunk)
        if size > LIMIT:
            raise ValueError('Manifestarchief te groot')
        chunks.append(chunk)
    with zipfile.ZipFile(io.BytesIO(b''.join(chunks))) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or entries[0].filename != 'feature_build.json' or entries[0].file_size > LIMIT:
            raise ValueError('Onverwachte inhoud featuremanifest')
        payload = archive.read(entries[0])
    context = json.loads(payload)
    expected = dict(source_run_id=str(run['id']), source_run_attempt=str(run['run_attempt']), source_sha=run['head_sha'])
    if any(str(context.get(key)) != value for key, value in expected.items()):
        raise ValueError('Manifest en bronrun verschillen')
    for key, env in [('source_run_id','FEATURE_EXPECTED_RUN_ID'), ('source_run_attempt','FEATURE_EXPECTED_RUN_ATTEMPT'), ('source_sha','FEATURE_EXPECTED_SHA')]:
        if os.getenv(env) and os.environ[env] != expected[key]:
            raise ValueError('Bronrun verschilt van de verwachte workflow-trigger')
    return payload, expected


def main():
    with requests.Session() as session:
        session.headers.update(Authorization=f"Bearer {os.environ['GITHUB_TOKEN']}", Accept='application/vnd.github+json')
        payload, expected = resolve(session, os.environ['GITHUB_REPOSITORY'], os.getenv('FEATURE_EXPECTED_RUN_ID'))
    path = Path(os.environ.get('FEATURE_BUILD_MANIFEST') or 'feature-build/feature_build.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    os.environ['FEATURE_BUILD_MANIFEST'] = str(path)
    context = load_feature_context()
    if context is None:
        raise ValueError('Geen bevestigd manifest')
    from common.run_manifest import ready_input
    import psycopg2
    conn=psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=30)
    try:
        with ready_input(conn,context):
            pass
    finally:
        conn.close()
    if os.getenv('GITHUB_ENV'):
        with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as output:
            for key, env in [('source_run_id','FEATURE_EXPECTED_RUN_ID'), ('source_run_attempt','FEATURE_EXPECTED_RUN_ATTEMPT'), ('source_sha','FEATURE_EXPECTED_SHA')]:
                output.write(f'{env}={expected[key]}\n')
    print(context_summary(context), flush=True)


if __name__ == '__main__':
    main()
