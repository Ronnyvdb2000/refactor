"""Frozen feature-run provenance and unchanged per-date scoring quality gates."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import yaml
from common import feature_pipeline as pipeline
from common.model_inputs import scoring_inputs, InputUnavailable

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)
DAY = pipeline.latest_closed_feature_day(NOW).isoformat()
BASE = date.fromisoformat(DAY)
SHA = 'a' * 40


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in ['FEATURE_BUILD_MANIFEST', 'FEATURE_BUILD_REQUIRED', 'FEATURE_EXPECTED_RUN_ID',
                'FEATURE_EXPECTED_SHA', 'FEATURE_EXPECTED_RUN_ATTEMPT', 'FEATURE_SELECTION_CUTOFF']:
        monkeypatch.delenv(key, raising=False)


def context(**changes):
    return dict(schema_version=1, status='ready', selection_cutoff=DAY,
                generated_at=NOW.isoformat(), source_run_id='123', source_run_attempt='1', source_sha=SHA,
                rows_written=20, rows_by_date={DAY: 20}, **changes)


def install(tmp_path, monkeypatch, value):
    path = tmp_path / 'feature_build.json'
    path.write_text(json.dumps(value))
    monkeypatch.setenv('FEATURE_BUILD_MANIFEST', str(path))
    monkeypatch.setenv('FEATURE_EXPECTED_RUN_ID', '123')
    monkeypatch.setenv('FEATURE_EXPECTED_RUN_ATTEMPT', '1')
    monkeypatch.setenv('FEATURE_EXPECTED_SHA', SHA)
    return path


def test_manual_mode_remains_optional_and_required_mode_fails_closed(monkeypatch):
    assert pipeline.load_feature_context() is None
    monkeypatch.setenv('FEATURE_BUILD_REQUIRED', '1')
    with pytest.raises(ValueError, match='verplicht'):
        pipeline.load_feature_context()


def test_successful_build_records_only_committed_rows(tmp_path, monkeypatch):
    path = tmp_path / 'manifest.json'
    monkeypatch.setenv('FEATURE_BUILD_MANIFEST', str(path))
    monkeypatch.setenv('FEATURE_SELECTION_CUTOFF', DAY)
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    monkeypatch.setenv('GITHUB_SHA', SHA)
    diagnosis = [dict(ticker=str(i), status='geschreven', koersdatum=DAY) for i in range(8)]
    diagnosis += [dict(ticker='missing', status='overgeslagen', koersdatum='') for _ in range(2)]
    pipeline.write_feature_manifest(diagnosis, .8)
    result = pipeline.load_feature_context()
    assert result['rows_written'] == 8 and result['rows_by_date'] == {DAY: 8}
    path.unlink()
    with pytest.raises(ValueError):
        pipeline.write_feature_manifest(diagnosis, .9)
    assert not path.exists()


@pytest.mark.parametrize('key,value', [('status', 'failed'), ('schema_version', 2),
    ('source_run_id', '999'), ('source_run_attempt', '2'), ('source_sha', 'b'*40), ('rows_written', 999),
    ('rows_by_date', {}), ('generated_at', (NOW-timedelta(hours=31)).isoformat()),
    ('generated_at', (NOW+timedelta(hours=1)).isoformat()),
    ('selection_cutoff', (NOW+timedelta(days=1)).date().isoformat())])
def test_wrong_stale_or_failed_upstream_cannot_authorize_scores(tmp_path, monkeypatch, key, value):
    data = context(); data[key] = value
    install(tmp_path, monkeypatch, data)
    with pytest.raises(ValueError):
        pipeline.load_feature_context(now=NOW)


def rows():
    return pd.DataFrame([dict(ticker=f'A{i}', datum=day,
                             technische_datum=pipeline_day(day), x=1.)
                         for day in [BASE-timedelta(days=1), BASE]
                         for i in range(20)])


def pipeline_day(day):
    from common.model_inputs import technical_day
    return technical_day(day)


def test_future_cohort_is_explicitly_deferred_without_relabelling_old_rows(capsys):
    raw = rows()
    newer = raw.tail(20).copy()
    newer['datum'] = BASE+timedelta(days=1)
    raw = pd.concat([raw, newer], ignore_index=True)
    kept = pipeline.restrict_to_feature_cutoff(raw, 'datum', context())
    ready, coverage = scoring_inputs(kept, ['x'], date_column='datum', today=DAY)
    assert ready.datum.max().date().isoformat() == DAY
    assert coverage[-1]['total'] == 20
    assert '20 nieuwere rijen wachten' in capsys.readouterr().out


def test_latest_eligible_day_still_blocks_when_incomplete():
    raw = rows()
    raw.loc[raw.datum == BASE, 'x'] = None
    kept = pipeline.restrict_to_feature_cutoff(raw, 'datum', context())
    with pytest.raises(InputUnavailable, match='0/20'):
        scoring_inputs(kept, ['x'], date_column='datum', today=DAY)


def test_no_eligible_cohort_is_not_a_successful_empty_publication():
    raw = rows().tail(20).copy()
    raw['datum'] = BASE+timedelta(days=1)
    with pytest.raises(InputUnavailable):
        pipeline.restrict_to_feature_cutoff(raw, 'datum', context())


def test_meta_selects_latest_date_only_within_confirmed_cutoff(tmp_path, monkeypatch):
    from meta_models import a_meta_model_score as meta
    install(tmp_path, monkeypatch, context())
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = [DAY]
    cursor.fetchall.return_value = []
    meta.haal_selecties(conn, meta.MODEL_FEATURES)
    first = cursor.execute.call_args_list[0]
    assert 'datum::date <= %s::date' in first.args[0]
    assert first.args[1] == (DAY,)
    with pytest.raises(InputUnavailable):
        meta.haal_selecties(conn, meta.MODEL_FEATURES, (NOW+timedelta(days=1)).date().isoformat())


def test_builder_includes_selected_symbols_outside_x_lists():
    from scripts import bouw_generieke_technicals as builder
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [('OUTSIDE_X',), ('A',), ('A',)]
    assert builder.geselecteerde_tickers(conn, DAY) == ['A', 'OUTSIDE_X']
    assert cursor.execute.call_args.args[1][1] == DAY


def test_consumers_follow_successful_trusted_build_and_pin_its_manifest():
    producer = yaml.load((ROOT/'.github/workflows/run_generieke_technicals.yml').read_text(), Loader=yaml.BaseLoader)
    for filename in ['score_xgboost.yml', 'a__meta_model_production.yml']:
        data = yaml.load((ROOT/'.github/workflows'/filename).read_text(), Loader=yaml.BaseLoader)
        assert data['on']['workflow_run']['workflows'] == [producer['name']]
        job = next(iter(data['jobs'].values()))
        assert "conclusion == 'success'" in job['if'] and "head_branch == 'main'" in job['if']
        assert 'head_repository.full_name == github.repository' in job['if']
        assert 'workflow_run.head_sha' in job['steps'][0]['with']['ref']
        artifact = next(s for s in job['steps'] if s.get('uses') == 'actions/download-artifact@v4')
        assert artifact['with']['run-id'] == '${{ github.event.workflow_run.id }}'
        assert job['env']['FEATURE_BUILD_REQUIRED']
    assert 'schedule' not in yaml.load((ROOT/'.github/workflows/score_xgboost.yml').read_text(), Loader=yaml.BaseLoader)['on']


def test_cutover_never_uses_today_before_daily_close_and_handles_midnight():
    assert pipeline.latest_closed_feature_day(datetime(2026, 11, 20, 2, tzinfo=timezone.utc)) == date(2026, 11, 19)
    assert pipeline.latest_closed_feature_day(datetime(2026, 11, 20, 22, 14, tzinfo=timezone.utc)) == date(2026, 11, 19)
    assert pipeline.latest_closed_feature_day(datetime(2026, 11, 20, 22, 15, tzinfo=timezone.utc)) == date(2026, 11, 20)
    assert pipeline.latest_closed_feature_day(datetime(2026, 11, 21, 1, tzinfo=timezone.utc)) == date(2026, 11, 20)


def test_xgboost_checks_the_frozen_cohort_before_any_scores(tmp_path, monkeypatch):
    from models import score_xgboost as scorer
    install(tmp_path, monkeypatch, context())
    raw = pd.DataFrame([dict(ticker=f'A{i}', selectie_datum=day,
                            technische_datum=pipeline_day(day), strategie='bot',
                            selectie_koers=100., beurs='X', **{f: 1. for f in scorer.VERWACHTE_FEATURES})
                        for day in [BASE, BASE+timedelta(days=1)] for i in range(20)])
    original = scorer.scoring_inputs
    with patch.object(scorer.pd, 'read_sql_query', return_value=raw), \
         patch.object(scorer, 'scoring_inputs', side_effect=lambda *a, **k: original(*a, **k, today=DAY)):
        ready = scorer.haal_nieuwe_selecties_op(MagicMock())
    assert len(ready) == 20
    assert ready.selectie_datum.max().date() == BASE
    assert ready.attrs['feature_context']['source_run_id'] == '123'


def test_wrong_manifest_stops_xgboost_before_even_reading_selection_data(tmp_path, monkeypatch):
    from models import score_xgboost as scorer
    data = context(); data['source_sha'] = 'b'*40
    install(tmp_path, monkeypatch, data)
    with patch.object(scorer.pd, 'read_sql_query') as read, pytest.raises(ValueError):
        scorer.haal_nieuwe_selecties_op(MagicMock())
    read.assert_not_called()
