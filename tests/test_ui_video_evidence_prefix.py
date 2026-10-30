"""Strict optional measured cache usage through real service and spawned child IPC."""
import io
import json
import pytest
from task_server.services import ai_skill_service as ai
from task_server.services.ui_video_stage_receipts import make_expected_attestation
from task_server.services.ui_video_records import safe_record
from task_server.services.ui_video_model_jobs import _safe_trace
from test_ui_video_stage_runtime import request
from test_ui_video_model_jobs import Gateway, manager, invocation

VALUES = [None, 0, 7, -1, True, '7', 1.5, 11]

@pytest.mark.parametrize('value', VALUES)
def test_actual_skill_raw_receipt_record_and_child_trace_keep_only_strict_report(monkeypatch, value):
    skill, kw = request('overview'); payload = kw.pop('payload'); receipts = []; trace = {}
    usage = {'promptTokens': 10, 'completionTokens': 2, 'totalTokens': 12}
    if value is not None: usage['cachedTokens'] = value
    def http(req, timeout):
        body = json.loads(req.data)
        return io.BytesIO(json.dumps({'success': True, 'content': '{}', 'model': body['model'],
            'providerId': 'official', 'fallbackUsed': False, 'finishReason': 'stop',
            'usage': usage, 'videoPolicy': make_expected_attestation(body)}).encode())
    monkeypatch.setattr(ai.urllib.request, 'urlopen', http)
    ai.run_ai_skill(skill, payload, **kw, retry_count=0, runtime_trace=trace, raw_receipt=receipts.append)
    expected = {'cachedTokens': value} if type(value) is int and 0 <= value <= 10 else {}
    for u in (trace['usage'], receipts[0]['usage'], safe_record('receipt', receipts[0])['usage'], _safe_trace(trace)['usage']):
        assert {k: v for k, v in u.items() if k == 'cachedTokens'} == expected
        assert u['promptTokens'] == 10

@pytest.mark.parametrize('prompt', [None, True, '10', -1, 1.5])
def test_cache_report_requires_strict_valid_prompt_count(prompt):
    trace = {'usage': {'promptTokens': prompt, 'cachedTokens': 0}}
    assert 'cachedTokens' not in _safe_trace(trace)['usage']

@pytest.mark.parametrize('value', [None, 0, 7, -1, True, '7', 1.5, 11])
def test_actual_spawned_model_jobs_persists_report_and_trace(tmp_path, value):
    usage = {'promptTokens': 10, 'totalTokens': 12}
    if value is not None: usage['cachedTokens'] = value
    gateway = Gateway(usage=usage)
    try:
        trace = {}; receipts = []
        with manager(tmp_path, gateway) as owner:
            skill, payload, kw = invocation(trace); kw['raw_receipt'] = receipts.append
            owner.run(skill, payload, **kw)
            stored = owner.records.read(owner.accounting[0]['receipt_ref'])
            expected = {'cachedTokens': value} if type(value) is int and 0 <= value <= 10 else {}
            for u in (trace['usage'], receipts[0]['usage'], stored['usage']):
                assert {k: v for k, v in u.items() if k == 'cachedTokens'} == expected
            assert owner.accounting[0]['admitted'] and owner.accounting[0]['cleanup']
            assert not owner.active_pids
    finally:
        gateway.close()

@pytest.mark.parametrize('value', VALUES)
def test_trajectory_audit_projection_preserves_valid_optional_count(monkeypatch, value):
    from task_server.services import ui_video_trajectory as vt, ui_video_windows
    from test_ui_video_trajectory import frames, event
    monkeypatch.setattr(ui_video_windows, 'load_assets', lambda *a: [])
    def runner(name, payload, **kw):
        usage = {'promptTokens': 10, 'totalTokens': 12}
        if value is not None: usage['cachedTokens'] = value
        kw['runtime_trace']['usage'] = usage
        if kw['version'] == 'audit':
            return {'checks': [{'event_id': p['track']['event_id'], 'trigger_verdict': 'supported',
                'outcome_verdict': 'supported', 'question': ''} for p in payload['trajectory']],
                'issues': [], 'multiple_scenarios': False, 'revisit': []}
        return dict(states=[], events=[event(['frame-001','frame-002'])] if kw['version']=='observe' else [],
                    checks=[], uncertainties=[], multiple_scenarios=False)
    result = vt.observe_trajectory({'media': {'frames': frames(3), 'duration_ms': 301}}, None, lambda: False, skill_runner=runner)
    expected = {'cachedTokens': value} if type(value) is int and 0 <= value <= 10 else {}
    for entry in result['trajectory_audit']['calls']:
        assert {k:v for k,v in entry['usage'].items() if k=='cachedTokens'} == expected
