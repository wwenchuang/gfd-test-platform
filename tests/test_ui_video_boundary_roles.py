"""Boundary context is an unverified model claim, never action evidence."""
import copy

import pytest

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
    with pytest.raises(ValueError):
        _extract_boundary_role_claims(raw, events, preserved, boundary, frames)


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
    with pytest.raises(ValueError):
        _extract_boundary_role_claims(raw, events, preserved, {'frame-003', 'frame-004'}, frames)


def test_context_role_contract_is_present_only_for_conflict_retry():
    from task_server.services.ai_skill_service import render_ai_skill_prompt, load_ai_skill_schema
    baseline = render_ai_skill_prompt('ui_video_observe', {}, version='coverage-observe')
    retry = render_ai_skill_prompt('ui_video_observe', {'boundary_retry_context': {}}, version='coverage-observe')
    assert 'boundary_frame_roles' not in baseline
    assert 'boundary_frame_roles' in retry
    assert '不自动成为事件证据或结论' in retry
    assert load_ai_skill_schema('ui_video_observe')['properties']['boundary_frame_roles']['type'] == 'array'


def test_unsolicited_role_claim_is_rejected(monkeypatch, tmp_path):
    from test_ui_video_boundary_feedback import evidence, setup, reply
    from task_server.services import ui_video_reconcile as reconcile
    frames, first, reviewed = evidence()
    initial = reply([reviewed[0], first[1], reviewed[1]])
    initial['boundary_frame_roles'] = [{'event_index': 2, 'context_frame_refs': ['frame-065']}]
    setup(monkeypatch, [initial])
    with pytest.raises(ValueError, match='video_reference_invalid'):
        reconcile._repair_coverage(first, {'events': reviewed, 'uncertainties': [], 'multiple_scenarios': False},
                                   frames, 195698, tmp_path, lambda: False, [], {'frame-047', 'frame-048', 'frame-049'})
