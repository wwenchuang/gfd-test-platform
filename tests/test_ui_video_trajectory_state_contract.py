"""State metadata corrections use the real observer loop without losing evidence."""
import copy

import pytest

from task_server.services import ai_skill_service as ai, ui_video_trajectory as vt, ui_video_windows
from test_ui_video_trajectory import event, frames


REJECTED_TEXT = 'rejected-model-instruction-do-not-repeat'
SOURCE_SECRET = 'private-input-not-for-feedback'


def state(**changes):
    return dict(state_id='page-a', first_frame='frame-033', last_frame='frame-044',
                kind='input', description='备注编辑区间', **changes)


def invalid_state(reason):
    item = state()
    item['description'] = REJECTED_TEXT
    if reason == 'state_shape':
        item = {k: item[k] for k in ('state_id', 'description')}
    elif reason == 'state_reference':
        item['first_frame'] = SOURCE_SECRET
    elif reason == 'state_kind':
        item['kind'] = REJECTED_TEXT
    return item


def observer_fixture(monkeypatch, failures, persistent=False):
    doc = {'media': {'frames': frames(64), 'duration_ms': 6401}}
    initial_state = state()
    initial_state.update(first_frame='frame-001', last_frame='frame-002', kind='page', description='备注页面')
    first = event([f'frame-{n:03d}' for n in range(26, 33)], 'aiInput')
    first['uncertainties'] = ['初始编辑仍有疑问']
    continued = event([f'frame-{n:03d}' for n in range(33, 45)], 'aiInput')
    continued.update(continuation_of='packet-001:event-0', uncertainties=['末段候选状态待核实'])
    continued['outcome']['frame_refs'] = ['frame-043', 'frame-044']
    calls, loaded, records, replies = [], [], [], []
    attempts = 0

    def model(name, payload, **kwargs):
        nonlocal attempts
        calls.append((kwargs['version'], copy.deepcopy(payload), copy.deepcopy(kwargs)))
        if kwargs['version'] == 'audit':
            return dict(checks=[dict(event_id=item['track']['event_id'], trigger_verdict='supported',
                                     outcome_verdict='supported', question='') for item in payload['trajectory']],
                        issues=[], multiple_scenarios=False, revisit=[])
        raw = dict(states=[], events=[], checks=[], uncertainties=[], multiple_scenarios=False, revisit=[])
        if kwargs['version'] == 'overview':
            raw.update(states=[copy.deepcopy(initial_state)], uncertainties=['概览关系待观察'])
        elif payload['packet_id'] == 'packet-001':
            raw['events'] = [copy.deepcopy(first)]
        else:
            attempts += 1
            raw.update(states=[state()], events=[copy.deepcopy(continued)], uncertainties=['本包页面关系待核实'])
            if persistent or attempts <= len(failures):
                reason = failures[min(attempts - 1, len(failures) - 1)]
                raw['states'] = [invalid_state(reason)]
                raw['events'][0].update(value=SOURCE_SECRET, observation=REJECTED_TEXT)
        replies.append(raw)
        return raw

    def assets(images, root, cancelled):
        loaded.append(copy.deepcopy(images))
        return [{'frame_id': f['frame_id']} for f in images]

    monkeypatch.setattr(ai, 'run_ai_skill', model)
    monkeypatch.setattr(ui_video_windows, 'load_assets', assets)
    run = lambda: vt.observe_trajectory(doc, None, lambda: False,
                                       record=lambda key, value: records.append((key, copy.deepcopy(value))))
    return run, doc, initial_state, first, continued, calls, loaded, records, replies


def assert_state_feedback(feedback):
    # Assert actionable corrective payload, not broad prompt wording or model answers.
    for field in ('state_id', 'first_frame', 'last_frame', 'kind', 'description'):
        assert field in feedback
    for kind in ('page', 'input', 'wait', 'transition'):
        assert kind in feedback
    assert '[]' in feedback
    assert REJECTED_TEXT not in feedback and SOURCE_SECRET not in feedback


@pytest.mark.parametrize('failures', [
    ['state_shape'], ['state_reference'], ['state_kind'], ['state_shape', 'state_kind'],
])
def test_observer_repairs_state_metadata_preserving_context_history_and_evidence(monkeypatch, failures):
    run, doc, initial, first, continued, calls, loaded, records, replies = observer_fixture(monkeypatch, failures)
    before = copy.deepcopy(doc)
    result = run()
    observations = [(p, kw) for stage, p, kw in calls if stage == 'observe' and p['packet_id'] == 'packet-002']
    assert len(observations) == len(failures) + 1
    original = observations[0][0]
    assert original['prior_events'][0]['fragments'] == [first]
    assert original['states'] == [initial] and original['overview_hypotheses'] == ['概览关系待观察']
    for payload, kwargs in observations[1:]:
        assert_state_feedback(payload['validation_feedback'])
        assert {k: v for k, v in payload.items() if k != 'validation_feedback'} == {
            k: v for k, v in original.items() if k != 'validation_feedback'}
        assert kwargs['image_assets'] == observations[0][1]['image_assets']
    assert loaded == [payload['frames'] for _, payload, _ in calls]
    assert len(calls) == 5 + len(failures) == len(result['trajectory_audit']['calls'])
    assert all(kw['retry_count'] == 0 and kw['repair_invalid_json'] is False for _, _, kw in calls)
    assert result['trajectory_audit']['states'] == [initial, state()]
    assert result['trajectory_audit']['tracks'][0]['fragments'] == [first, continued]
    assert result['trajectory_audit']['tracks'][0]['outcome'] == continued['outcome']
    assert result['trajectory_audit']['tracks'][0]['frame_refs'] == first['frame_refs'] + continued['frame_refs']
    assert len(result['events']) == 1 and result['events'][0]['value'] == ''
    questions = result['events'][0]['uncertainties']
    assert [q for q in questions if q in first['uncertainties'] + continued['uncertainties']] == first['uncertainties'] + continued['uncertainties']
    assert '本包页面关系待核实' in result['uncertainties']
    audits = [p for stage, p, _ in calls if stage == 'audit']
    assert len(audits) == 2 and audits[1]['trajectory'][0]['track']['continuation_of'] == 'packet-001:event-0'
    assert result['trajectory_audit']['checks'][0]['status'] == 'confirmed'
    # Invalid data was rejected, not edited to make it valid; recording still redacts input values.
    rejected = [r for r in replies if r['events'] and r['events'][0]['value'] == SOURCE_SECRET]
    assert len(rejected) == len(failures) and all(r['states'][0]['description'] == REJECTED_TEXT for r in rejected)
    assert SOURCE_SECRET not in repr(records)
    assert doc == before


@pytest.mark.parametrize('reason', ['state_shape', 'state_reference', 'state_kind'])
def test_persistently_invalid_states_stop_after_existing_two_corrections(monkeypatch, reason):
    run, _, _, _, _, calls, _, records, _ = observer_fixture(monkeypatch, [reason], persistent=True)
    with pytest.raises(ValueError, match='video_model_invalid') as exc:
        run()
    assert exc.value.trajectory_reason == reason
    observations = [p for stage, p, _ in calls if stage == 'observe' and p['packet_id'] == 'packet-002']
    assert len(observations) == 3
    for payload in observations[1:]:
        assert_state_feedback(payload['validation_feedback'])
    assert len(calls) == 6 and sum(stage == 'audit' for stage, _, _ in calls) == 1
    assert all(key != 'trajectory-final' for key, _ in records)


@pytest.mark.parametrize('change,reason', [
    ({'first_frame': None}, 'state_reference'),
    ({'first_frame': 'not-provided'}, 'state_reference'),
    ({'last_frame': 'frame-032'}, 'state_reference'),
    ({'kind': 'click'}, 'state_kind'),
    ({'extra': 'no extra fields'}, 'state_shape'),
])
def test_state_validator_keeps_rejecting_bad_metadata(change, reason):
    item = {**state(), **change}
    with pytest.raises(ValueError) as exc:
        vt.validate_states([item], frames(64))
    assert exc.value.trajectory_reason == reason


@pytest.mark.parametrize('field', ['first_frame', 'last_frame', 'kind'])
def test_state_validator_keeps_rejecting_missing_metadata(field):
    item = state()
    del item[field]
    with pytest.raises(ValueError) as exc:
        vt.validate_states([item], frames(64))
    assert exc.value.trajectory_reason == 'state_shape'
