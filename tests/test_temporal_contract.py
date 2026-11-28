"""Seam regressions: unknown outcomes never authorize training or promotion."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import zipfile

import pandas as pd
import pytest
import yaml

from common import temporal_contract as temporal
from common.model_inputs import InputUnavailable
from maintenance import resolve_feature_manifest as resolver
from analyses.paper_dispatch import follow_frozen_protocol


@pytest.fixture(autouse=True)
def no_context(monkeypatch):
    for name in ['FEATURE_BUILD_MANIFEST','FEATURE_BUILD_REQUIRED','FEATURE_EXPECTED_RUN_ID',
                 'FEATURE_EXPECTED_RUN_ATTEMPT','FEATURE_EXPECTED_SHA']:
        monkeypatch.delenv(name,raising=False)


def intervals():
    return pd.DataFrame(dict(datum=pd.to_datetime(['2026-01-01']*7),
        label_end_10d=['2026-01-20','2026-01-21','2026-01-22',None,'invalid','2026-01-01','2026-01-19'],
        labels_review=[None]*6+['split_review'],ticker=list('ABCDEFG')))


def test_actual_end_and_strict_boundary_override_even_a_large_calendar_gap():
    frame=intervals()
    assert temporal.purge_before(frame,10,'2026-01-21').ticker.tolist()==['A']
    assert temporal.purge_before(frame,10,'2026-01-22').ticker.tolist()==['A','B']
    with pytest.raises(InputUnavailable,match='einddatums'):
        temporal.purge_before(frame.drop(columns='label_end_10d'),10,'2026-01-21')


def test_future_or_unconfirmed_labels_never_enter_excess_target_training():
    from models.xLightgbmV2_gecontroleerd import split,FEATURES
    frame=pd.DataFrame([dict(ticker=f'T{i}',datum=d,target=i%2,
        label_end_10d=d+pd.offsets.BDay(10),**{f:float(i) for f in FEATURES})
        for d in pd.bdate_range('2025-01-01',periods=100) for i in range(12)])
    frame.loc[0,'label_end_10d']='2027-01-01'
    train,test,start,_=split(frame,'10d')
    assert 0 not in train.index
    assert pd.to_datetime(train.label_end_10d).max()<start
    assert train.index.intersection(test.index).empty


def test_daily_test_counts_are_not_independent_windows():
    frame=pd.DataFrame(dict(datum=pd.bdate_range('2026-01-01',periods=12)))
    frame['label_end_10d']=frame.datum+pd.offsets.BDay(10)
    report=temporal.overlap_report(frame,10)
    assert report['dates']==12 and report['nonoverlapping_blocks']==2
    assert 'not a proof' in report['note']
    assert report['unknown_intervals']==0


def test_feature_cutoff_also_bounds_known_outcomes(monkeypatch):
    monkeypatch.setattr(temporal,'load_feature_context',lambda:dict(selection_cutoff='2026-01-19'))
    assert temporal.purge_before(intervals(),10,'2026-01-25').empty


def test_fingerprint_changes_with_actual_input_or_label_metadata():
    frame=intervals()
    first=temporal.input_fingerprint(frame)
    assert temporal.input_fingerprint(frame[list(reversed(frame.columns))])==first
    frame.loc[0,'label_end_10d']='2026-01-18'
    assert temporal.input_fingerprint(frame)!=first


def session(payload=None, *, run_changes=None, archive_name='feature_build.json'):
    run=dict(id=123,workflow_id=77,conclusion='success',status='completed',head_branch='main',
             head_repository=dict(full_name='owner/repo'),run_attempt=2,head_sha='a'*40)
    run.update(run_changes or {})
    data=payload or dict(source_run_id='123',source_run_attempt='2',source_sha='a'*40)
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:archive.writestr(archive_name,json.dumps(data))
    mock=MagicMock()
    def get(url,**kwargs):
        response=MagicMock()
        if url.endswith('/actions/workflows/refresh_sel_par.yml'):response.json.return_value=dict(id=77)
        elif '/artifacts?' in url:response.json.return_value=dict(artifacts=[dict(name='feature-build-manifest',id=9,expired=False,size_in_bytes=100)])
        elif url.endswith('/zip'):response.iter_content.return_value=[stream.getvalue()]
        elif '/runs?' in url:response.json.return_value=dict(workflow_runs=[run])
        elif url.endswith('/runs/123'):response.json.return_value=run
        else:raise AssertionError(url)
        return response
    mock.get.side_effect=get
    return mock


def test_manual_resolves_one_concrete_successful_main_run():
    payload,expected=resolver.resolve(session(),'owner/repo')
    assert json.loads(payload)['source_run_attempt']=='2'
    assert expected==dict(source_run_id='123',source_run_attempt='2',source_sha='a'*40)


@pytest.mark.parametrize('changes',[dict(conclusion='failure'),dict(status='in_progress'),
    dict(head_branch='other'),dict(head_repository=dict(full_name='fork/repo')),dict(workflow_id=88)])
def test_latest_failed_or_untrusted_run_never_falls_back(changes):
    mock=session(run_changes=changes)
    with pytest.raises(ValueError,match='vertrouwde'):resolver.resolve(mock,'owner/repo')
    assert not any('/artifacts' in call.args[0] for call in mock.get.call_args_list)


@pytest.mark.parametrize('bad',[dict(source_run_id='124',source_run_attempt='2',source_sha='a'*40),
    dict(source_run_id='123',source_run_attempt='1',source_sha='a'*40)])
def test_manifest_from_other_run_or_previous_attempt_is_rejected(bad):
    with pytest.raises(ValueError,match='verschillen'):resolver.resolve(session(bad),'owner/repo','123')


def test_automatic_trigger_attempt_cannot_be_silently_replaced(monkeypatch):
    monkeypatch.setenv('FEATURE_EXPECTED_RUN_ATTEMPT','1')
    with pytest.raises(ValueError,match='trigger'):resolver.resolve(session(),'owner/repo','123')


def test_manifest_zip_cannot_extract_any_other_file():
    with pytest.raises(ValueError,match='inhoud'):
        resolver.resolve(session(archive_name='../feature_build.json'),'owner/repo')


def test_frozen_paper_finishes_but_does_not_adopt_new_training_protocol():
    engine=SimpleNamespace(PROTOCOL=dict(implementation_hash='b'*64,engine_hash='same',top_n=10),freeze_batch=lambda:1)
    frozen=dict(engine.PROTOCOL,implementation_hash='a'*64)
    assert follow_frozen_protocol(engine,frozen)
    assert engine.PROTOCOL==frozen and engine.freeze_batch() is None
    with pytest.raises(ValueError,match='uitvoering'):
        follow_frozen_protocol(engine,dict(frozen,top_n=9))


def test_all_model_workflow_routes_have_required_readiness_before_first_command():
    root=Path(__file__).resolve().parents[1]
    routes=json.loads((root/'docs/pipeline_routes.json').read_text())
    for filename in routes['manifest_workflows']:
        workflow=yaml.load((root/'.github/workflows'/filename).read_text(),Loader=yaml.BaseLoader)
        for job in workflow['jobs'].values():
            steps=job.get('steps',[])
            resolvers=[i for i,s in enumerate(steps) if 'maintenance.resolve_feature_manifest' in s.get('run','')]
            if not resolvers:continue
            env={**workflow.get('env',{}),**job.get('env',{})}
            assert env['FEATURE_BUILD_REQUIRED']=='1' and env['FEATURE_BUILD_MANIFEST']=='feature-build/feature_build.json'
            commands=[i for i,s in enumerate(steps) if any(x in s.get('run','') for x in ['-m maintenance.run_ready','-m maintenance.score_once'])]
            assert commands and resolvers[0]<min(commands)
            assert steps[resolvers[0]]['env']['SUPABASE_DB_URL']=='${{ secrets.SUPABASE_DB_URL }}'
            assert not any('-m models.' in s.get('run','') or '-m meta_models.' in s.get('run','') for s in steps)


def test_unknown_return_cannot_influence_relative_training_targets():
    frame=pd.DataFrame(dict(datum=pd.Timestamp('2026-01-01'),
        fwd_ret_10d=list(range(10))+[10000.], label_end_10d=['2026-01-16']*10+['2026-02-01']))
    # With the late label included, the old median was 5; the known median is 4.5.
    frame['target']=(frame.fwd_ret_10d>frame.fwd_ret_10d.median()).astype(int)
    assert frame.loc[5,'target']==0
    train=temporal.purged_training(frame,10,'2026-01-20',target_mode='excess')
    assert len(train)==10 and train.loc[5,'target']==1
    assert train.target.sum()==5


@pytest.mark.parametrize('horizon',[5,10,20,30,60])
def test_local_calendar_helpers_agree_with_common(horizon):
    from common.horizons import calendar_embargo
    from models import xLightgbmV2, xLightgbmV2_gecontroleerd, xgboostV3_gecontroleerd, xgboostV4
    key=f'{horizon}d'
    for helper in [xLightgbmV2.embargo_kalenderdagen,xLightgbmV2_gecontroleerd.embargo_days,
                   xgboostV3_gecontroleerd.embargo_dagen,xgboostV4.embargo_kalenderdagen]:
        assert helper(key)==calendar_embargo(horizon)


def test_live_push_routes_do_not_run_from_work_branches():
    root=Path(__file__).resolve().parents[1]
    for filename in ['run_02xgboostV4.yml','tradingagentsbridge.yml']:
        workflow=yaml.load((root/'.github/workflows'/filename).read_text(),Loader=yaml.BaseLoader)
        assert workflow['on']['push']['branches']==['main']
