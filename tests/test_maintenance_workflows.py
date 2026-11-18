"""Exercise relocated CLI imports and actual YAML shell branches without DB access."""
import os
from pathlib import Path
import subprocess
import sys
import yaml
import pytest

ROOT=Path(__file__).resolve().parents[1]


def workflow_step(path,name):
    workflow=yaml.safe_load((ROOT/path).read_text())
    for job in workflow['jobs'].values():
        for step in job['steps']:
            if step.get('name')==name:return step['run']
    raise AssertionError(name)


def stub_python(tmp_path,monkeypatch,status=0):
    executable=tmp_path/'python'
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$BACKFILL_TEST_CAPTURE"\nexit '+str(status)+'\n')
    executable.chmod(0o755)
    capture=tmp_path/'command.txt'
    monkeypatch.setenv('BACKFILL_TEST_CAPTURE',str(capture))
    monkeypatch.setenv('PATH',str(tmp_path)+os.pathsep+os.environ['PATH'])
    return capture


@pytest.mark.parametrize('module',['backfill_technische_features','backfill_rank_kolommen'])
def test_relocated_cli_help_is_safe_and_resolves_from_repo_root(module):
    env=dict(os.environ);env.pop('SUPABASE_DB_URL',None)
    r=subprocess.run([sys.executable,'-m','maintenance.'+module,'--help'],cwd=ROOT,
                     env=env,capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stderr
    assert not (ROOT/(module+'.py')).exists()


@pytest.mark.parametrize('apply',['true','false'])
def test_rank_workflow_routes_apply_and_dry_run(tmp_path,monkeypatch,apply):
    command=workflow_step('.github/workflows/backfill_rank_kolommen.yml','Draai maintenance.backfill_rank_kolommen')
    command=command.replace('${{ inputs.apply }}',apply)
    capture=stub_python(tmp_path,monkeypatch)
    r=subprocess.run(['bash','-e','-c',command],cwd=tmp_path,capture_output=True,text=True,timeout=10)
    assert r.returncode==0,r.stderr
    assert capture.read_text().strip()==('-m maintenance.backfill_rank_kolommen'+(' --apply' if apply=='true' else ''))


@pytest.mark.parametrize('status',[0,7])
def test_technical_workflow_uses_module_and_preserves_exit_status(tmp_path,monkeypatch,status):
    command=workflow_step('.github/workflows/backfill.yml','Run backfill')
    capture=stub_python(tmp_path,monkeypatch,status)
    r=subprocess.run(['bash','-e','-c',command],cwd=tmp_path,capture_output=True,text=True,timeout=10)
    assert r.returncode==status
    assert capture.read_text().strip()=='-m maintenance.backfill_technische_features'
    assert (tmp_path/'results/backfill_log.txt').exists()


@pytest.mark.parametrize('setting,expected',[(None,'True'),('true','True'),('false','False'),('unclear','True')])
def test_technical_dry_run_defaults_remain_safe(setting,expected):
    env=dict(os.environ);env.pop('SUPABASE_DB_URL',None)
    if setting is None:env.pop('BACKFILL_DRY_RUN',None)
    else:env['BACKFILL_DRY_RUN']=setting
    result=subprocess.run([sys.executable,'-c',
        'from maintenance.backfill_technische_features import DRY_RUN; print(DRY_RUN)'],
        cwd=ROOT,env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip().splitlines()[-1]==expected
