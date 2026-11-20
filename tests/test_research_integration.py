"""Verify main owns the research launcher and its offline CI checks."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)


def test_daily_simulation_uses_integrated_main_and_existing_schedule():
    data = workflow('research_simulation.yml')
    assert data['on']['schedule'] == [{'cron': '17 7 * * *'}]
    assert 'workflow_dispatch' in data['on']
    assert data['permissions'] == {'contents': 'read'}
    steps = data['jobs']['simulate']['steps']
    checkout = next(s for s in steps if s.get('uses', '').startswith('actions/checkout@'))
    assert checkout['with']['ref'] == 'main'
    assert (ROOT / 'research/__main__.py').is_file()
    training = next(s for s in steps if s.get('run') == 'python -m research --supabase --telegram')
    assert set(training['env']) == {'SUPABASE_DB_URL', 'TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID'}
    assert 'feature/quantify-principles' not in str(data)
    assert any(s.get('if') == 'failure()' and 'ONVOLLEDIG' in s.get('run', '') for s in steps)


def test_research_checks_follow_main_and_keep_prs_secret_free():
    data = workflow('research_checks.yml')
    assert data['on']['push']['branches'] == ['main']
    assert data['permissions'] == {'contents': 'read'}
    steps = data['jobs']['offline']['steps']
    tests = next(s for s in steps if s.get('id') == 'tests')
    assert 'tests/test_research_integration.py' in tests['run']
    for step in steps:
        if any('secrets.' in str(v) for v in step.get('env', {}).values()):
            condition = step['if']
            assert "github.event_name != 'pull_request'" in condition or "github.event_name == 'workflow_dispatch'" in condition
        assert '${{ inputs.' not in step.get('run', '')


def test_workflow_inventory_points_at_real_research_module():
    from maintenance.audit_workflows import audit
    result = audit(ROOT)
    assert result['errors'] == []
    research = next(w for w in result['workflows'] if w['file'] == 'research_simulation.yml')
    assert research['scripts'] == ['research/__main__.py']
    assert research['external_entrypoints'] == []
