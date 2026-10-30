"""Offline receipt replay; synthetic correction is not original-source acceptance."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from task_server.services import ai_skill_service as ai, ui_video_stage_policy as policy
from task_server.services import ui_video_runtime as runtime, ui_video_model_jobs as jobs
from task_server.services import ui_video_trajectory as trajectory, ui_video_windows
from test_ui_video_stage_wire import capture
from test_ui_video_model_jobs import Gateway, manager
from test_ui_video_trajectory import frames, event

FIXTURE = Path(__file__).parent / 'fixtures' / 'ui_video_v14_trigger_owner.json'
PATH = '$.events[2].trigger_frame'


def replay():
    fixture = json.loads(FIXTURE.read_text())
    payload = dict(copy.deepcopy(fixture['packet']), phase='whole-trajectory', reasoning_mode='bounded')
    payload['image_frames'] = copy.deepcopy(payload['frames'])
    assets = [dict(name=f['frame_id'], timestamp_ms=f['timestamp_ms'], mime='image/jpeg', base64='YQ==')
              for f in payload['frames']]
    return fixture, payload, assets, runtime._semantic_schema(payload)


def invoke(payload, assets, schema, **kwargs):
    return ai.run_ai_skill('ui_video_trajectory', payload, version='observe', video_stage='grounding',
                           image_assets=assets, output_schema=schema, **kwargs)


def test_authentic_schema_hash_and_rejected_owner():
    fixture, payload, _, schema = replay()
    digest = hashlib.sha256(json.dumps(schema, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    assert digest == fixture['provenance']['output_schema_sha256']
    assert fixture['response']['events'][2]['trigger_frame'] in payload['context_frame_ids']
    with pytest.raises(ValueError, match=r'\$\.events\[2\]\.trigger_frame'):
        policy.validate_stage_output(fixture['response'], schema)


def test_real_skill_translates_captured_reply_to_schema_error(monkeypatch):
    fixture, payload, assets, schema = replay()
    calls = capture(monkeypatch, json.dumps(fixture['response'], ensure_ascii=False))
    with pytest.raises(ai.SkillSchemaError) as caught:
        invoke(payload, assets, schema)
    assert caught.value.schema_path == PATH
    assert 'frame-163' not in caught.value.model_feedback
    assert len(calls) == 1


def test_real_child_persists_receipt_and_safe_indexed_path(tmp_path):
    fixture, payload, assets, schema = replay()
    gateway = Gateway(json.dumps(fixture['response'], ensure_ascii=False))
    try:
        with manager(tmp_path, gateway) as owner:
            with pytest.raises(ai.SkillSchemaError) as caught:
                owner.run('ui_video_trajectory', payload, version='observe', video_stage='grounding',
                          image_assets=assets, output_schema=schema)
            assert caught.value.schema_path == PATH
            assert len(gateway.calls) == len(owner.accounting) == 1
            row = owner.accounting[0]
            assert row['proof_persisted'] and row['receipt_persisted'] and row['terminal'] and row['cleanup']
            assert not row['admitted'] and not owner.active_pids
            assert 'schema' in row['events']
            proof = owner.records.read(row['proof_ref'])
            receipt = owner.records.read(row['receipt_ref'])
            assert receipt['content'] == json.dumps(fixture['response'], ensure_ascii=False)
            assert receipt['gateway_request_sha256'] == proof['gateway_request_sha256']
            assert receipt['videoPolicy']['output_schema_sha256'] == fixture['provenance']['output_schema_sha256']
    finally:
        gateway.close()


@pytest.mark.parametrize('path', ['$.events[2].trigger_frame', '$.events[0].outcome.frame_refs[7]', '$.states[1].description'])
def test_ipc_schema_owned_indices_survive(path):
    assert jobs._schema_path(path, 'ui_video_trajectory') == path


@pytest.mark.parametrize('path', ['$.events[-1].trigger_frame', '$.events[01].trigger_frame',
    '$.events[999999999999999999].trigger_frame', '$.events[2].SECRET', '$.states.description',
    '$.events[2][0]', '$.events[2].trigger_frame.SECRET', '$.' + 'x'*513,
    '$.events[50].trigger_frame', '$.events[2].frame_refs[60]', '$.events[2.0].trigger_frame'])
def test_ipc_rejects_unowned_or_invalid_paths(path):
    assert jobs._schema_path(path, 'ui_video_trajectory') == '$'


@pytest.mark.parametrize('prior_correction,invalid_count', [(False,1), (False,2), (False,3), (True,1), (True,2)])
def test_actual_trajectory_loop_uses_typed_skill_rejection_and_two_corrections(monkeypatch, tmp_path, prior_correction, invalid_count):
    fixture, payload, assets, schema = replay()
    captured = json.dumps(fixture['response'], ensure_ascii=False)
    monkeypatch.setattr(ui_video_windows, 'load_assets', lambda *args: [])
    gateway = Gateway(captured)
    requests = []
    observations = []
    overview_attempts = []
    remaining_attempts = 3 - int(prior_correction)
    exhausted = invalid_count >= remaining_attempts
    empty = dict(states=[], events=[], checks=[], uncertainties=[], multiple_scenarios=False, revisit=[])
    def runner(skill, current, **kwargs):
        requests.append((kwargs['version'], copy.deepcopy(current)))
        if kwargs['version'] == 'overview' and prior_correction:
            overview_attempts.append(current)
            if len(overview_attempts) == 1:
                # Spend one existing correction on a distinct prior overview contract rejection.
                return {**copy.deepcopy(empty), 'events': [event(['frame-001', 'frame-002'])]}
        if kwargs['version'] == 'observe':
            observations.append(current)
            if len(observations) <= invalid_count:
                # Replay authentic validation, never replace the validator with a fake error.
                return owner.run('ui_video_trajectory', payload, version='observe', video_stage='grounding',
                                 image_assets=assets, output_schema=schema)
            raw = copy.deepcopy(empty)
            raw['events'] = [event(['frame-001', 'frame-002'])]
            # Synthetic corrected reply passes the real child/skill/schema too.
            gateway.content = json.dumps(raw, ensure_ascii=False)
            current_assets = [dict(name=f['frame_id'], timestamp_ms=f['timestamp_ms'],
                                   mime='image/jpeg', base64='YQ==') for f in current['frames']]
            return owner.run(skill, current, version='observe', video_stage='grounding',
                             image_assets=current_assets, output_schema=runtime._semantic_schema(current))
        if kwargs['version'] == 'audit':
            return dict(checks=[dict(event_id=x['track']['event_id'], trigger_verdict='supported',
                        outcome_verdict='supported', question='') for x in current['trajectory']],
                        issues=[], multiple_scenarios=False, revisit=[])
        return copy.deepcopy(empty)
    document = {'media': {'frames': frames(3), 'duration_ms': 401}}
    try:
        with manager(tmp_path, gateway) as owner:
            if exhausted:
                with pytest.raises(ai.SkillSchemaError) as caught:
                    trajectory.observe_trajectory(document, None, lambda: False, skill_runner=runner)
                assert caught.value.schema_path == PATH
            else:
                result = trajectory.observe_trajectory(document, None, lambda: False, skill_runner=runner)
                assert len(result['events']) == 1
            assert all(r['proof_persisted'] and r['receipt_persisted'] and r['cleanup']
                       for r in owner.accounting)
            assert sum(r['admitted'] for r in owner.accounting) == (0 if exhausted else 1)
            assert not owner.active_pids
        assert len(observations) == min(invalid_count + 1, remaining_attempts)
        assert len(gateway.calls) == min(invalid_count + 1, remaining_attempts)
    finally:
        gateway.close()
    for current in observations[1:]:
        feedback = current['validation_feedback']
        assert PATH in feedback and 'owned_frame_ids' in feedback and 'context' in feedback
        assert 'frame-163' not in feedback and fixture['response']['events'][2]['target'] not in feedback


def test_configuration_error_not_reclassified(monkeypatch):
    _, payload, assets, schema = replay()
    schema['unexpected'] = True
    calls = capture(monkeypatch)
    with pytest.raises(ValueError, match='video_output_schema_invalid') as caught:
        invoke(payload, assets, schema)
    assert not isinstance(caught.value, ai.SkillSchemaError) and not calls


@pytest.mark.parametrize('failure', [TimeoutError('transport-timeout'), OSError('transport-private')])
def test_transport_errors_do_not_enter_schema_correction(monkeypatch, failure):
    _, payload, assets, schema = replay()
    monkeypatch.setattr(ai.urllib.request, 'urlopen', lambda *args, **kwargs: (_ for _ in ()).throw(failure))
    with pytest.raises((TimeoutError, RuntimeError)) as caught:
        invoke(payload, assets, schema)
    assert not isinstance(caught.value, ai.SkillSchemaError)


def test_output_error_is_typed_value_compatible_and_has_only_safe_path():
    fixture, _, _, schema = replay()
    with pytest.raises(ValueError) as caught:
        policy.validate_stage_output(fixture['response'], schema)
    assert type(caught.value).__name__ == 'VideoStageOutputError'
    assert caught.value.schema_path == PATH
    assert str(caught.value) == 'video_semantic_output_invalid:' + PATH


def test_validator_union_does_not_swallow_malformed_schema_errors():
    with pytest.raises(KeyError):
        policy.validate_stage_output('x', {'anyOf': [{'type': 'unsupported'}, {'type': 'string'}]})
