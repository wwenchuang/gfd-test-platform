"""Authentic v15 receipt replay; loopback images/replies are synthetic regression evidence."""
import copy
import json
from pathlib import Path

import pytest
from task_server.services import ai_skill_service as ai, ui_video_stage_policy as policy
from task_server.services.ui_video_stage_receipts import canonical_sha, expected_provider_options
from test_ui_video_stage_wire import capture, evidence, semantic_schema
from test_ui_video_model_jobs import Gateway, manager
from test_ui_video_business_runtime import harness
from task_server.services.ui_video_trajectory import observe_trajectory as REAL_OBSERVE

FIXTURE = Path(__file__).parent / 'fixtures' / 'ui_video_v15_audit_shape.json'


def audit_schema():
    return policy._provider_projection(ai.load_ai_skill_schema('ui_video_trajectory')['oneOf'][1])


def request():
    payload, assets = evidence()
    payload.update(phase='whole-trajectory', reasoning_mode='bounded', trajectory=[])
    return payload, dict(version='audit', video_stage='audit', image_assets=assets, output_schema=audit_schema())


def valid():
    return dict(checks=[], issues=[], multiple_scenarios=False, revisit=[])


def test_canonical_audit_stage_and_cross_stage_identity():
    schema = audit_schema()
    assert set(schema['properties']) == {'checks', 'issues', 'multiple_scenarios', 'revisit'}
    assert policy.validate_output_schema(schema, stage='audit') == schema
    for supplied, stage in [(semantic_schema(), 'audit'), (schema, 'grounding'), (schema, 'semantic')]:
        with pytest.raises(ValueError, match='video_output_schema_invalid'):
            policy.validate_output_schema(supplied, stage=stage)


@pytest.mark.parametrize('schema', [None, {}, semantic_schema()])
def test_wrong_audit_schema_rejected_without_transport(monkeypatch, schema):
    p, kw = request(); kw['output_schema'] = schema; calls = capture(monkeypatch)
    with pytest.raises(ValueError, match='video_output_schema_invalid'):
        ai.run_ai_skill('ui_video_trajectory', p, **kw)
    assert calls == []


def test_skill_strict_audit_wire_and_options(monkeypatch):
    p, kw = request(); calls = capture(monkeypatch, json.dumps(valid())); receipts = []
    assert ai.run_ai_skill('ui_video_trajectory', p, **kw, raw_receipt=receipts.append) == valid()
    body = calls[0]; schema = audit_schema()
    assert body['outputSchema'] == schema
    assert expected_provider_options(body)['response_format'] == dict(type='json_schema', json_schema=dict(name='video_audit', strict=True, schema=schema))
    assert receipts[0]['videoPolicy']['output_schema_sha256'] == canonical_sha(schema)
    assert receipts[0]['videoPolicy']['effective_options']['response_format'] == 'json_schema'
    assert body['model'] == 'qwen3.8-max' and body['fallbackModelConfig'] == {}


def test_authentic_captured_wrong_shape_typed_and_raw_preserved(monkeypatch):
    original = json.loads(FIXTURE.read_text())['receipt_entry']['value']
    entry = json.loads(FIXTURE.read_text())['receipt_entry']
    assert canonical_sha({k:v for k,v in entry.items() if k != 'sha256'}) == entry['sha256']
    assert original['videoPolicy']['effective_options']['response_format'] == 'json_object'
    assert original['videoPolicy']['output_schema_sha256'] is None
    raw = original['content']; assert set(json.loads(raw)) == {'reasoning', 'action', 'outcome'}
    calls = capture(monkeypatch, raw); receipts = []; p, kw = request()
    with pytest.raises(ai.SkillSchemaError) as caught:
        ai.run_ai_skill('ui_video_trajectory', p, **kw, raw_receipt=receipts.append)
    assert caught.value.schema_path == '$'
    assert len(calls) == len(receipts) == 1 and receipts[0]['content'] == raw


@pytest.mark.parametrize('raw', [{**valid(), 'action':'accept'}, {**valid(), 'checks':[{'event_id':'e', 'extra':True}]}])
def test_unexpected_output_properties_are_typed(monkeypatch, raw):
    capture(monkeypatch, json.dumps(raw)); p, kw = request()
    with pytest.raises(ai.SkillSchemaError):
        ai.run_ai_skill('ui_video_trajectory', p, **kw)


def test_legacy_audit_without_schema_unchanged(monkeypatch):
    p, kw = request(); kw.pop('output_schema'); calls = capture(monkeypatch, json.dumps(valid()))
    assert ai.run_ai_skill('ui_video_trajectory', p, **kw) == valid()
    assert 'outputSchema' not in calls[0]
    assert expected_provider_options(calls[0])['response_format'] == {'type':'json_object'}


def test_real_child_rejection_has_proof_raw_receipt_cleanup_and_no_retry(tmp_path):
    raw = json.loads(FIXTURE.read_text())['receipt_entry']['value']['content']
    gateway = Gateway(raw)
    try:
        with manager(tmp_path, gateway) as owner:
            p, kw = request()
            with pytest.raises(ai.SkillSchemaError) as caught:
                owner.run('ui_video_trajectory', p, **kw)
            assert caught.value.schema_path == '$'
            assert len(gateway.calls) == len(owner.accounting) == 1
            row = owner.accounting[0]
            assert row['proof_persisted'] and row['receipt_persisted'] and row['cleanup'] and row['terminal']
            assert not row['admitted'] and not owner.active_pids and 'schema' in row['events']
            proof = owner.records.read(row['proof_ref']); receipt = owner.records.read(row['receipt_ref'])
            assert receipt['content'] == raw
            assert receipt['gateway_request_sha256'] == proof['gateway_request_sha256']
            assert receipt['videoPolicy']['output_schema_sha256'] == canonical_sha(audit_schema())
            assert receipt['videoPolicy']['effective_options']['response_format'] == 'json_schema'
    finally:
        gateway.close()


def test_business_runner_supplies_audit_schema(harness):
    h = harness; h['run']()
    from task_server.services.ui_video_runtime import _audit_schema
    audits = [(p, kw) for p, kw in h['calls'] if kw['video_stage'] == 'audit']
    assert audits and all(kw['output_schema'] == _audit_schema(p,forward_context_issues=True) for p, kw in audits)


def test_business_real_trajectory_shards_and_shared_correction_keep_strict_schema(harness, monkeypatch):
    from task_server.services import ui_video_trajectory as vt
    from test_ui_video_audit_batches import Engine
    from test_ui_video_trajectory import frames
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    h = harness
    # Recover the actual engine function, saved before the harness replaces its module entry.
    engine = Engine(monkeypatch)
    h['original'][:] = frames(90)
    h['doc']['media']['duration_ms'] = 9001
    original_job = h['Jobs'].run
    rejected = []
    def job(self, skill, payload, **kw):
        result = original_job(self, skill, payload, **kw)
        if kw['video_stage'] == 'timeline':
            return result
        if kw['video_stage'] in {'overview', 'grounding', 'audit'}:
            if (kw['video_stage'] == 'audit' and payload['packet_id'] == 'packet-002' and
                    len(payload['trajectory']) == 1 and not rejected):
                rejected.append(True)
                raise ai.SkillSchemaError('$', '输出不符合当前阶段schema')
            return engine.model(skill, payload, **kw)
        return result
    monkeypatch.setattr(h['Jobs'], 'run', job)
    def actual(doc, root, cancelled, progress, **kw):
        kw['execution_contract'] = VIDEO_EXPLICIT_KEYBOARD_V1
        return REAL_OBSERVE(doc, root, cancelled, progress, **kw)
    monkeypatch.setattr(vt, 'observe_trajectory', actual)
    monkeypatch.setattr(h['analysis'], 'consolidate_observed_frames',
        lambda *a, **kw: dict(events=[], multiple_scenarios=False, uncertainties=[]))
    h['run']()
    audits = [(p, kw) for p, kw in h['calls'] if kw['video_stage'] == 'audit']
    second = [(p, kw) for p, kw in audits if p['packet_id'] == 'packet-002']
    assert [len(p['trajectory']) for p, _ in second] == [6, 1, 1]
    assert 'validation_feedback' in second[-1][0] and rejected == [True]
    from task_server.services.ui_video_runtime import _audit_schema
    assert all(kw['output_schema'] == _audit_schema(p,forward_context_issues=True) for p, kw in audits)
    assert all(kw['output_schema']['properties']['checks']['items']['properties']['event_id'].get('enum', []) ==
               [c['track']['event_id'] for c in p['trajectory']] for p, kw in audits)
    for p, kw in audits:
        assert p['reference_contract'] == dict(version='packet-audit-reference-v2',
            check_event_ids=[c['track']['event_id'] for c in p['trajectory']],
            issue_owner_frame_ids=p['owned_frame_ids'],
            future_issue_owner_frame_ids=[f['frame_id'] for f in p['frames'] if f['timestamp_ms']>=p['owned_time_range_ms'][1]],image_frame_ids=[f['frame_id'] for f in p['frames']])
    assert all('business_timeline' not in p for p, _ in audits)
