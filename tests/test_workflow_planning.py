"""Read-only workflow configuration and scheduling regression checks."""
from pathlib import Path

import pytest
import yaml

from maintenance.audit_workflows import audit, entrypoints, weekly_slots

ROOT = Path(__file__).resolve().parents[1]


def test_all_workflows_in_scope_have_existing_scripts_and_bounded_runs():
    report = audit(ROOT)
    assert report['workflows']
    assert report['errors'] == []
    assert report['warnings'] == []


def test_python_command_inventory_ignores_pip_and_inline_code():
    source = '''python -m pip install requests
python -c "import bot"
python -u one.py --mode live
python -m maintenance.example \\
  --dry-run
'''
    assert entrypoints(source) == ['one.py', 'maintenance/example.py']


def test_cron_expansion_preserves_weekdays_and_sunday_alias():
    weekdays = weekly_slots('05 20 * * 1-5')
    assert weekdays == {(d,20,5) for d in range(1,6)}
    assert weekly_slots('0 5 * * 0,7') == {(0,5,0)}
    assert len(weekly_slots('*/15 1 * * 1')) == 4


@pytest.mark.parametrize('cron', ['61 0 * * *', '0 25 * * *', '0 1 * * 8', '*/0 1 * * *', '0 1 5 * *'])
def test_invalid_or_calendar_specific_schedule_is_not_misreported_as_a_week(cron):
    with pytest.raises(ValueError):
        weekly_slots(cron)


def test_audit_detects_missing_guard_timeout_script_and_stale_push_path(tmp_path):
    directory = tmp_path / '.github/workflows'
    directory.mkdir(parents=True)
    (directory / 'demo.yml').write_text('''name: demo
on:
  schedule:
    - cron: '0 5 * * 1-5'
  push:
    paths:
      - .github/workflows/removed.yml
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - run: python removed.py
''')
    result = audit(tmp_path)
    assert len(result['errors']) == 3
    assert any('missing entrypoint removed.py' in s for s in result['errors'])
    assert any('push path does not exist' in s for s in result['warnings'])
    assert result['workflows'][0]['crons'] == ['0 5 * * 1-5']


def test_weekly_scope_does_not_accidentally_claim_guards_are_fixed(tmp_path):
    directory = tmp_path / '.github/workflows'
    directory.mkdir(parents=True)
    (directory / 'weekly_test.yml').write_text('''name: weekly
on:
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-latest
    steps: []
''')
    result = audit(tmp_path)
    assert result['errors'] == []
    assert result['workflows'][0]['scope'] == 'weekly-apart'


def test_paused_workflows_and_disabled_bridge_remain_paused():
    for name in ['run_02xgboost.yml','run_02xgboostMeta.yml']:
        d = yaml.load((ROOT/'.github/workflows'/name).read_text(),Loader=yaml.BaseLoader)
        assert 'schedule' not in d['on']
    d = yaml.load((ROOT/'.github/workflows/run_00vcp.yml').read_text(),Loader=yaml.BaseLoader)
    assert d['jobs']['tradingagents-analyse']['if'] == 'false'


def test_simultaneous_start_inventory_groups_same_jobs_across_days(tmp_path):
    directory = tmp_path/'.github/workflows'
    directory.mkdir(parents=True)
    for name in ['one','two']:
        (directory/(name+'.yml')).write_text('''on:
  schedule:
    - cron: '50 19 * * 1-5'
jobs: {}
''')
    result = audit(tmp_path)
    assert result['simultaneous_starts'] == [dict(days=['ma','di','wo','do','vr'],utc='19:50',workflows=['one.yml','two.yml'])]


def test_external_checkout_is_explicitly_reported_and_not_checked_against_main(tmp_path):
    directory = tmp_path / '.github/workflows'
    directory.mkdir(parents=True)
    (directory / 'research.yml').write_text('''on:
  workflow_dispatch:
concurrency:
  group: research
  queue: max
  cancel-in-progress: false
jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
        with:
          ref: feature/research
      - run: python -m research --supabase
''')
    result = audit(tmp_path)
    assert result['errors'] == []
    assert result['workflows'][0]['external_entrypoints'] == [
        dict(script='research.py', ref='feature/research', job='run')]
    from maintenance.audit_workflows import markdown
    assert 'niet tegen de lokale hoofdbranch gevalideerd' in markdown(result)


def test_nested_checkout_does_not_hide_missing_root_script(tmp_path):
    directory = tmp_path / '.github/workflows'
    directory.mkdir(parents=True)
    (directory / 'demo.yml').write_text('''on:
  workflow_dispatch:
jobs:
  run:
    steps:
      - uses: actions/checkout@v4
        with:
          ref: feature/external
          path: vendor
      - run: python missing.py
''')
    result = audit(tmp_path)
    assert any('missing entrypoint missing.py' in error for error in result['errors'])
