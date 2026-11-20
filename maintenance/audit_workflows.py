"""Read-only workflow inventory and scheduling checks. Requires PyYAML.

Run: python -m maintenance.audit_workflows --format markdown
No GitHub calls, workflow dispatches, notifications or database access.
"""
import argparse
import itertools
import json
import re
from pathlib import Path

import yaml

PYTHON_COMMAND = re.compile(
    r'(?m)^\s*python(?:3)?(?:\s+-u)?\s+(?:-m\s+([\w.]+)|([\w./-]+\.py))(?:\s|$)'
)
WEEKDAYS = ('zo', 'ma', 'di', 'wo', 'do', 'vr', 'za')


def weekly_owned(path, source):
    return path.name.startswith('weekly_') or 'weekly.' in source


def entrypoints(source):
    result = []
    for module, script in PYTHON_COMMAND.findall(source.replace('\\\n', ' ')):
        if module == 'pip':
            continue
        value = module.replace('.', '/') + '.py' if module else script
        if value not in result:
            result.append(value)
    return result


def cron_field(field, low, high):
    values = set()
    for part in field.split(','):
        base, _, step = part.partition('/')
        stride = int(step) if step else 1
        if stride <= 0:
            raise ValueError('cron step must be positive')
        if base == '*':
            start, end = low, high
        elif '-' in base:
            start, end = map(int, base.split('-'))
        else:
            start = int(base)
            end = high if step else start
        if not low <= start <= end <= high:
            raise ValueError('cron field outside range')
        values.update(range(start, end + 1, stride))
    return values


def weekly_slots(cron):
    minute, hour, day, month, weekday = cron.split()
    if day != '*' or month != '*':
        raise ValueError('calendar-specific cron cannot be expanded as a generic week')
    return set(itertools.product(
        {v % 7 for v in cron_field(weekday, 0, 7)},
        cron_field(hour, 0, 23), cron_field(minute, 0, 59)))


def valid_guard(guard):
    return (isinstance(guard, dict) and bool(guard.get('group'))
            and guard.get('cancel-in-progress', 'false') == 'false'
            and guard.get('queue') == 'max')


def audit(root):
    root = Path(root)
    workflows, errors, warnings, slots = [], [], [], {}
    for path in sorted((root / '.github/workflows').glob('*.y*ml')):
        source = path.read_text(encoding='utf-8')
        data = yaml.load(source, Loader=yaml.BaseLoader)
        scope = 'weekly-apart' if weekly_owned(path, source) else 'repo-planning'
        triggers = data.get('on', {})
        triggers = triggers if isinstance(triggers, dict) else {str(triggers): {}}
        crons = [item['cron'] for item in triggers.get('schedule', [])]
        row = dict(file=path.name, name=data.get('name', path.stem), scope=scope,
                   crons=crons, scripts=[], jobs=[], group=None, external_entrypoints=[])
        guard = data.get('concurrency')
        if isinstance(guard, dict):
            row['group'] = guard.get('group')
        for job_id, job in data.get('jobs', {}).items():
            job_guard = job.get('concurrency')
            row['jobs'].append(dict(id=job_id, timeout=job.get('timeout-minutes'),
                                    group=(job_guard or {}).get('group'),
                                    needs=job.get('needs'), condition=job.get('if')))
            if scope == 'repo-planning':
                if not (valid_guard(guard) or valid_guard(job_guard)):
                    errors.append(f'{path.name}/{job_id}: run guard or multi-pending queue missing')
                timeout = job.get('timeout-minutes', '')
                if not str(timeout).isdigit() or not 1 <= int(timeout) <= 360:
                    errors.append(f'{path.name}/{job_id}: bounded job timeout missing')
            checkout_ref = None
            for step in job.get('steps', []):
                if str(step.get('uses', '')).startswith('actions/checkout@'):
                    settings = step.get('with') or {}
                    # Only a checkout into the working root changes command sources.
                    if settings.get('path', '.') in ('.', ''):
                        checkout_ref = settings.get('ref')
                        if settings.get('repository'):
                            checkout_ref = str(settings['repository']) + '@' + str(checkout_ref or 'default')
                for script in entrypoints(step.get('run', '')):
                    if script not in row['scripts']:
                        row['scripts'].append(script)
                    if checkout_ref and checkout_ref not in ('main', '${{ github.ref }}', '${{ github.sha }}'):
                        external = dict(script=script, ref=checkout_ref, job=job_id)
                        if external not in row['external_entrypoints']:
                            row['external_entrypoints'].append(external)
                        continue
                    if not (root / script).is_file() and not (root / script[:-3] / '__init__.py').is_file():
                        (errors if scope == 'repo-planning' else warnings).append(f'{path.name}: missing entrypoint {script}')
        push = triggers.get('push') or {}
        if isinstance(push, dict):
            for pattern in push.get('paths', []):
                if pattern.startswith('.github/workflows/') and not any(c in pattern for c in '*?![') and not (root / pattern).is_file():
                    warnings.append(f'{path.name}: push path does not exist: {pattern}')
        for cron in crons:
            try:
                for slot in weekly_slots(cron):
                    slots.setdefault(slot, set()).add(path.name)
            except ValueError as exc:
                warnings.append(f'{path.name}: {exc}: {cron}')
        workflows.append(row)
    collisions = {}
    for (day, hour, minute), names in slots.items():
        if len(names) > 1:
            collisions.setdefault((hour, minute, tuple(sorted(names))), []).append(day)
    coincidences = [dict(days=[WEEKDAYS[d] for d in sorted(days)], utc=f'{hour:02}:{minute:02}', workflows=list(names))
                    for (hour, minute, names), days in sorted(collisions.items())]
    if (root / 'maintenance/yahoo_lanes.py').is_file():
        from .yahoo_lanes import check_budget
        errors.extend(check_budget(root))
    return dict(workflows=workflows, simultaneous_starts=coincidences,
                errors=sorted(set(errors)), warnings=sorted(set(warnings)))


def markdown(report):
    lines = ['# Workflowplanning refactor', '',
             'Alle tijden zijn UTC. Dit is broncode-inventarisatie; een cron bewijst geen ingeschakelde workflow of succesvolle live run.',
             'Workflowguards begrenzen herstarts; de afzonderlijke Yahoo-jobgroepen delen dagelijkse, lange, feature- en tijdgebonden taken. Weekly valt buiten deze herstelronde.', '',
             '| Workflow | Cron (UTC) | Python-entrypoints | Joblimiet (min) | Run-groep | Yahoo-jobgroep | Scope |',
             '| --- | --- | --- | --- | --- | --- | --- |']
    for row in report['workflows']:
        lines.append('| ' + ' | '.join([
            f"[{row['file']}](../.github/workflows/{row['file'].replace(' ', '%20')})",
            '<br>'.join(f'`{c}`' for c in row['crons']) or 'Handmatig/event',
            ', '.join(f'`{s}`' for s in row['scripts']) or 'Shell/inline Python',
            ', '.join(str(j['timeout'] or 'ontbreekt') for j in row['jobs']),
            str(row['group'] or ', '.join(j['group'] or 'ontbreekt' for j in row['jobs'])),
            ', '.join(j['group'] for j in row['jobs'] if j['group'] and 'refactor-yahoo-' in j['group']) or 'Niet toegewezen', row['scope']]) + ' |')
    external = [(row['file'], item) for row in report['workflows'] for item in row.get('external_entrypoints', [])]
    if external:
        lines += ['', '## Entry points uit andere checkouts', '',
                  'Deze paden worden uit een andere ref/repository uitgevoerd en zijn niet tegen de lokale hoofdbranch gevalideerd.', '']
        lines += [f"- `{name}` / `{item['job']}`: `{item['script']}` uit `{item['ref']}`." for name, item in external]
    lines += ['', '## Gelijke geplande startminuten', '',
              'Dit zijn mogelijke pieken, geen bewijs dat runs werkelijk tegelijk zijn gestart. Looptijden kunnen ook tussen verschillende startminuten overlappen.', '',
              '| Dagen | UTC | Workflows |', '| --- | --- | --- |']
    for row in report['simultaneous_starts']:
        lines.append('| ' + ' | '.join([', '.join(row['days']), row['utc'], ', '.join(row['workflows'])]) + ' |')
    lines += ['', '## Controlepunten', '']
    lines += [f'- FOUT: {s}' for s in report['errors']]
    lines += [f'- Open: {s}' for s in report['warnings']]
    if not report['errors']:
        lines.append('- Alle workflows binnen scope hebben een runbeveiliging, meerdere wachtplaatsen en begrensde jobduur; hun lokaal gecontroleerde entrypoints bestaan. Afwijkende checkouts staan afzonderlijk vermeld.')
    return '\n'.join(lines).rstrip() + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--format', choices=['json', 'markdown'], default='json')
    args = parser.parse_args()
    report = audit(args.root)
    print(markdown(report) if args.format == 'markdown' else json.dumps(report, ensure_ascii=False, indent=2),
          end='' if args.format == 'markdown' else '\n')
    return int(bool(report['errors']))


if __name__ == '__main__':
    raise SystemExit(main())
