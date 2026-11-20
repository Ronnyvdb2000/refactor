"""Provenance and date boundary for scoring after a successful feature build.

No database or provider access. The manifest does not replace per-cohort input checks.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re


def latest_closed_feature_day(now=None):
    """Conservative UTC cutover for the covered European/North-American daily data.

    Freeze yesterday before 22:15 UTC; exact market holidays remain subject to
    the existing per-selection date and coverage guards, never an as-of fallback.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError('Timezone ontbreekt bij feature-peildatum')
    current = current.astimezone(timezone.utc)
    return current.date() - timedelta(days=(current.hour, current.minute) < (22, 15))


def load_feature_context(*, now=None):
    path = os.environ.get('FEATURE_BUILD_MANIFEST', '').strip()
    required = os.environ.get('FEATURE_BUILD_REQUIRED') == '1'
    if not path:
        if required:
            raise ValueError('Feature-buildmanifest verplicht; scoring niet gestart')
        return None
    try:
        context = json.loads(Path(path).read_text(encoding='utf-8'))
        if context['schema_version'] != 1 or context['status'] != 'ready':
            raise ValueError()
        cutoff = date.fromisoformat(context['selection_cutoff'])
        stamp = datetime.fromisoformat(context['generated_at'])
        current = now or datetime.now(timezone.utc)
        if stamp.tzinfo is None or not timedelta(0) <= current - stamp <= timedelta(hours=30):
            raise ValueError()
        if cutoff > latest_closed_feature_day(current) or (current.date() - cutoff).days > 7:
            raise ValueError()
        if not re.fullmatch(r'[0-9a-f]{40}', context['source_sha']):
            raise ValueError()
        if not str(context['source_run_id']).isdigit() or int(context['source_run_id']) < 1:
            raise ValueError()
        if not str(context['source_run_attempt']).isdigit() or int(context['source_run_attempt']) < 1:
            raise ValueError()
        if type(context['rows_written']) is not int or context['rows_written'] < 1:
            raise ValueError()
        if not context['rows_by_date'] or sum(context['rows_by_date'].values()) != context['rows_written']:
            raise ValueError()
        for day, count in context['rows_by_date'].items():
            if date.fromisoformat(day) > cutoff or type(count) is not int or count < 1:
                raise ValueError()
        for env, key in [('FEATURE_EXPECTED_RUN_ID', 'source_run_id'), ('FEATURE_EXPECTED_SHA', 'source_sha'), ('FEATURE_EXPECTED_RUN_ATTEMPT', 'source_run_attempt')]:
            expected = os.environ.get(env)
            if expected and str(context[key]) != expected:
                raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('Feature-buildmanifest ongeldig, verouderd of niet van de verwachte bronrun') from None
    return context


def context_summary(context):
    return (f"Featurebron: run {context['source_run_id']} poging {context['source_run_attempt']}, commit {context['source_sha'][:12]}, "
            f"selecties t/m {context['selection_cutoff']}")


def restrict_to_feature_cutoff(frame, column, context):
    if context is None or frame.empty:
        return frame
    from .model_inputs import dates, InputUnavailable
    days = dates(frame[column])
    if days.isna().any():
        raise InputUnavailable('Ongeldige selectiedatum in feature-pipeline')
    import pandas as pd
    pending = days > pd.Timestamp(context['selection_cutoff'])
    print(f"[pipeline] {context_summary(context)}; {int(pending.sum())} nieuwere rijen wachten op een volgende bouwrun", flush=True)
    result = frame.loc[~pending].copy()
    if result.empty:
        raise InputUnavailable('Nog geen selecties binnen de bevestigde feature-peildatum; geen scores gepubliceerd')
    return result


def write_feature_manifest(diagnosis, minimum_ratio):
    path = os.environ.get('FEATURE_BUILD_MANIFEST', '').strip()
    if not path:
        return
    target = Path(path)
    written = [r for r in diagnosis if r['status'] == 'geschreven']
    if not diagnosis or not written or len(written) / len(diagnosis) < minimum_ratio:
        raise ValueError('Geen succesvol, voldoende gedekt featuremanifest')
    cutoff = os.environ.get('FEATURE_SELECTION_CUTOFF', date.today().isoformat())
    date.fromisoformat(cutoff)
    context = dict(schema_version=1, status='ready', selection_cutoff=cutoff,
                   generated_at=datetime.now(timezone.utc).isoformat(),
                   source_run_id=os.environ.get('GITHUB_RUN_ID', ''),
                   source_run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT', '1'),
                   source_sha=os.environ.get('GITHUB_SHA', ''), rows_written=len(written),
                   rows_by_date=dict(sorted(Counter(r['koersdatum'] for r in written).items())))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(context, indent=2), encoding='utf-8')
    temporary.replace(target)
    # Fail the build rather than distributing invalid provenance.
    load_feature_context()
    print('[pipeline] ' + context_summary(context), flush=True)
