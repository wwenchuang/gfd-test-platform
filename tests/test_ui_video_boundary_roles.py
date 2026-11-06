"""Boundary context is an unverified model claim, never action evidence."""
import copy

import pytest
from test_ui_case_draft_service import env

from task_server.services.ui_video_reconcile import _extract_boundary_role_claims


def inputs():
    frames = [{'frame_id': f'frame-{i:03}', 'timestamp_ms': i * 100} for i in range(1, 6)]
    events = [{'action': 'aiInput', 'frame_refs': ['frame-001', 'frame-002']}]
    preserved = [{'action': 'dismissKeyboard', 'frame_refs': ['frame-003', 'frame-004']}]
    raw = {'boundary_frame_roles': [{'event_index': 0, 'context_frame_refs': ['frame-003']}]}
    return raw, events, preserved, frames


def test_context_claim_is_bounded_and_does_not_modify_action_evidence():
    raw, events, preserved, frames = inputs()
    before = copy.deepcopy(events)
    claims = _extract_boundary_role_claims(raw, events, preserved, {'frame-003', 'frame-004'}, frames)
    assert events == before
    assert claims == [{'action': 'aiInput', 'frame_refs': ['frame-001', 'frame-002'],
                       'context_frame_refs': ['frame-003'], 'status': 'unverified_model_claim'}]
    assert 'boundary_frame_roles' not in raw


@pytest.mark.parametrize('mutation', ['unknown', 'not_attached', 'not_boundary', 'not_first',
                                     'duplicate', 'reused_action_frame', 'bad_index', 'extra_field'])
def test_invalid_or_ambiguous_context_cannot_enter_audit(mutation):
    raw, events, preserved, frames = inputs()
    boundary = {'frame-003', 'frame-004'}
    claim = raw['boundary_frame_roles'][0]
    if mutation == 'unknown': claim['context_frame_refs'] = ['frame-999']
    if mutation == 'not_attached': frames = frames[:2]
    if mutation == 'not_boundary': boundary = {'frame-004'}
    if mutation == 'not_first': claim['context_frame_refs'] = ['frame-004']
    if mutation == 'duplicate': claim['context_frame_refs'] = ['frame-003', 'frame-003']
    if mutation == 'reused_action_frame': events[0]['frame_refs'].append('frame-003')
    if mutation == 'bad_index': claim['event_index'] = True
    if mutation == 'extra_field': claim['text'] = 'untrusted model prose'
    assert _extract_boundary_role_claims(raw, events, preserved, boundary, frames) == []


def test_legacy_output_without_claim_is_unchanged():
    raw, events, preserved, frames = inputs()
    raw.pop('boundary_frame_roles')
    assert _extract_boundary_role_claims(raw, events, preserved, {'frame-003'}, frames) == []


def test_claim_is_private_and_coverage_and_boundary_still_apply(monkeypatch, tmp_path):
    from test_ui_video_boundary_feedback import evidence, setup, reply
    from task_server.services import ui_video_reconcile as reconcile
    from task_server.services.ui_video_source_service import public_source

    frames, first, reviewed = evidence()
    second = reply([reviewed[0], first[1], reviewed[1]])
    second['boundary_frame_roles'] = [{'event_index': 2, 'context_frame_refs': ['frame-065']}]
    calls = setup(monkeypatch, [reply([first[0], first[2]]), second])
    result = reconcile._repair_coverage(first, {'events': reviewed, 'uncertainties': [], 'multiple_scenarios': False},
                                        frames, 195698, tmp_path, lambda: False, [], {'frame-047', 'frame-048', 'frame-049'})
    assert len(calls) == 2
    assert result['frame_role_audit'][0]['status'] == 'unverified_model_claim'
    assert result['frame_role_audit'][0]['context_frame_refs'] == ['frame-065']
    assert 'frame-065' not in result['events'][2]['frame_refs']
    reconcile.check_coverage(first, result['events'], 195698)
    reconcile.check_coverage(reviewed, result['events'], 195698)
    assert 'frame_role_audit' not in public_source(result)


@pytest.mark.parametrize('kind', ['missing_focus', 'overlapping_action'])
def test_role_claim_cannot_override_coverage_or_boundary(monkeypatch, tmp_path, kind):
    from test_ui_video_boundary_feedback import evidence, setup, reply
    from task_server.services import ui_video_reconcile as reconcile
    frames, first, reviewed = evidence()
    events = [reviewed[0], reviewed[1]] if kind == 'missing_focus' else [reviewed[0], first[1], first[2]]
    second = reply(events)
    second['boundary_frame_roles'] = [{'event_index': len(events)-1, 'context_frame_refs': ['frame-065']}]
    setup(monkeypatch, [reply([first[0], first[2]]), second])
    with pytest.raises(ValueError):
        reconcile._repair_coverage(first, {'events': reviewed, 'uncertainties': [], 'multiple_scenarios': False},
                                   frames, 195698, tmp_path, lambda: False, [], {'frame-047', 'frame-048', 'frame-049'})


def test_context_claim_cannot_skip_intervening_preserved_action():
    raw, events, preserved, frames = inputs()
    preserved.append({'action': 'aiTap', 'frame_refs': ['frame-003', 'frame-004']})
    assert _extract_boundary_role_claims(raw, events, preserved, {'frame-003', 'frame-004'}, frames) == []


def test_context_role_contract_is_present_only_for_conflict_retry():
    from task_server.services.ai_skill_service import render_ai_skill_prompt, load_ai_skill_schema
    baseline = render_ai_skill_prompt('ui_video_observe', {}, version='coverage-observe')
    retry = render_ai_skill_prompt('ui_video_observe', {'boundary_retry_context': {}}, version='coverage-observe')
    assert 'boundary_frame_roles' not in baseline
    assert 'boundary_frame_roles' in retry
    assert '不自动成为事件证据或结论' in retry
    assert 'boundary_frame_roles' in load_ai_skill_schema('ui_video_observe')['properties']


def test_unsolicited_role_claim_is_ignored_without_losing_valid_events(monkeypatch, tmp_path):
    from test_ui_video_boundary_feedback import evidence, setup, reply
    from task_server.services import ui_video_reconcile as reconcile
    frames, first, reviewed = evidence()
    initial = reply([reviewed[0], first[1], reviewed[1]])
    initial['boundary_frame_roles'] = [{'event_index': 2, 'context_frame_refs': ['frame-065']}]
    setup(monkeypatch, [initial])
    result = reconcile._repair_coverage(first, {'events': reviewed, 'uncertainties': [], 'multiple_scenarios': False},
                                       frames, 195698, tmp_path, lambda: False, [], {'frame-047', 'frame-048', 'frame-049'})
    assert 'frame_role_audit' not in result
    assert [e['action'] for e in result['events']] == ['aiTap', 'aiTap', 'aiInput', 'dismissKeyboard']


def test_long_preserved_interaction_needs_only_an_attached_later_frame():
    raw, events, preserved, frames = inputs()
    preserved[0]['frame_refs'].append('frame-008')
    all_frames = frames + [{'frame_id': 'frame-008', 'timestamp_ms': 800}]
    claims = _extract_boundary_role_claims(raw, events, preserved, {'frame-003', 'frame-004', 'frame-008'}, frames[:4], all_frames)
    assert claims[0]['context_frame_refs'] == ['frame-003']


def test_normal_observation_ignores_optional_roles():
    from task_server.services.ui_video_analysis import _bind_whole_evidence
    frames = [{'frame_id': 'frame-001', 'timestamp_ms': 100}]
    result = {'events': [{'action': 'aiTap', 'target': '按钮', 'value': '', 'frame_refs': ['frame-001'],
                          'observation': '点击', 'uncertainties': []}],
              'uncertainties': [], 'multiple_scenarios': False, 'boundary_frame_roles': []}
    assert 'boundary_frame_roles' not in _bind_whole_evidence(result, frames)


def test_private_role_audit_survives_later_failure(env, monkeypatch, tmp_path):
    import io
    from test_ui_case_draft_service import A, APP
    from task_server.services import ui_video_source_service as source, ui_video_analysis as analysis
    from task_server.services import ui_video_reconcile as reconcile, ui_video_windows as windows
    from task_server.services import ui_video_delayed_response as delayed
    from task_server.workers import ui_video_worker as worker
    fixture=__import__('test_ui_video_boundary_feedback')
    frames, first, reviewed = fixture.evidence()
    store=source.VideoStore(tmp_path/'videos')
    doc=store.upload(A,APP,'clip.mp4',io.BytesIO(b'video'),5,actor=A)
    monkeypatch.setattr(worker,'decode_video',lambda *a:{'black':False,'static':False,'frames':frames,'duration_ms':195698})
    monkeypatch.setattr(windows,'observe_windows',lambda *a:({'events':first,'uncertainties':[],'multiple_scenarios':False},[]))
    monkeypatch.setattr(reconcile,'needs_reconciliation',lambda *a:True)
    final_reply=fixture.reply([reviewed[0],first[1],reviewed[1]])
    final_reply['boundary_frame_roles']=[{'event_index':2,'context_frame_refs':['frame-065']}]
    fixture.setup(monkeypatch,[fixture.reply([first[0],first[2]]),final_reply])
    def real_repair(*args):
        return reconcile._repair_coverage(first,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},
                    frames,195698,tmp_path,lambda:False,[],{'frame-047','frame-048','frame-049'})
    monkeypatch.setattr(reconcile,'reconcile',real_repair)
    def fail_later(*args):raise ValueError('video_expected_missing')
    monkeypatch.setattr(delayed,'review_pending_responses',fail_later)
    transitions=[]
    original=store.transition
    def transition(*a,**kw):
        if 'frame_role_audit' in kw:transitions.append(copy.deepcopy(kw))
        return original(*a,**kw)
    monkeypatch.setattr(store,'transition',transition)
    worker.analyze_video_source(store,doc['source_id'])
    final=store.get(doc['source_id'],A['user_id'],APP)
    assert final['status']=='failed'
    assert final['frame_role_audit'][0]['context_frame_refs']==['frame-065']
    assert final['frame_role_audit'][0]['status']=='unverified_model_claim'
    assert transitions[0]['expected_owner']==A['user_id'] and transitions[0]['expected_app']==APP
    assert transitions[0]['expected_version']==doc['analysis_version']
    assert 'frame_role_audit' not in source.public_source(final)


def test_failed_audit_write_does_not_fail_video_analysis(monkeypatch, tmp_path):
    from task_server.services import ui_video_analysis as analysis, ui_video_reconcile as reconcile
    from task_server.services import ui_video_windows as windows, ui_video_delayed_response as delayed
    frames, first, reviewed = __import__('test_ui_video_boundary_feedback').evidence()
    monkeypatch.setattr(windows,'observe_windows',lambda *a:({'events':first,'uncertainties':[],'multiple_scenarios':False},[]))
    monkeypatch.setattr(reconcile,'needs_reconciliation',lambda *a:True)
    audit=[{'action':'aiInput','frame_refs':['frame-064'],'context_frame_refs':['frame-065'],'status':'unverified_model_claim'}]
    monkeypatch.setattr(reconcile,'reconcile',lambda *a:{'events':reviewed,'uncertainties':[],
                        'multiple_scenarios':False,'frame_role_audit':audit})
    monkeypatch.setattr(delayed,'review_pending_responses',lambda observed,*a:observed)
    monkeypatch.setattr(analysis,'consolidate_observed_frames',lambda *a:{'accepted':'sentinel'})
    def progress(*args):pass
    def broken_sink(*args):raise OSError('private audit unavailable')
    progress.frame_role_audit_sink=broken_sink
    assert analysis.analyze_whole_frames({'media':{'frames':frames,'duration_ms':195698}},tmp_path,lambda:False,progress)=={'accepted':'sentinel'}


def test_audit_does_not_attach_to_replaced_source_version(env, monkeypatch, tmp_path):
    import io
    from test_ui_case_draft_service import A, APP
    from task_server.services import ui_video_source_service as source
    from task_server.services import ui_video_reconcile as reconcile, ui_video_windows as windows
    from task_server.workers import ui_video_worker as worker
    frames, first, reviewed = __import__('test_ui_video_boundary_feedback').evidence()
    store=source.VideoStore(tmp_path/'videos')
    doc=store.upload(A,APP,'clip.mp4',io.BytesIO(b'video'),5,actor=A)
    monkeypatch.setattr(worker,'decode_video',lambda *a:{'black':False,'static':False,'frames':frames,'duration_ms':195698})
    monkeypatch.setattr(windows,'observe_windows',lambda *a:({'events':first,'uncertainties':[],'multiple_scenarios':False},[]))
    monkeypatch.setattr(reconcile,'needs_reconciliation',lambda *a:True)
    audit=[{'action':'aiInput','frame_refs':['frame-064'],'context_frame_refs':['frame-065'],'status':'unverified_model_claim'}]
    def replaced(*a):
        store.transition(doc['source_id'],{'analyzing'},'analyzing',analysis_version=doc['analysis_version']+1)
        return {'events':reviewed,'uncertainties':[],'multiple_scenarios':False,'frame_role_audit':audit}
    monkeypatch.setattr(reconcile,'reconcile',replaced)
    worker.analyze_video_source(store,doc['source_id'])
    final=store.get(doc['source_id'],A['user_id'],APP)
    assert final['analysis_version']==doc['analysis_version']+1
    assert 'frame_role_audit' not in final
