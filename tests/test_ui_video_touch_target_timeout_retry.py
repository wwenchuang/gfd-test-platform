import io
import json
import threading
import urllib.error
from email.message import Message

import pytest

from task_server.services import ai_skill_service as ai
from task_server.services import ui_video_touch_targets as targets


def _http_error(status, body):
    headers = Message()
    headers['Content-Type'] = 'application/json'
    return urllib.error.HTTPError('http://gateway/ai/skill', status, 'error', headers,
                                  io.BytesIO(json.dumps(body).encode()))


def _gateway_call(monkeypatch, *, status=500, error='Request timed out.', version='touch-target'):
    frames = [{'frame_id': 'frame-001', 'timestamp_ms': 0}, {'frame_id': 'frame-002', 'timestamp_ms': 100}]
    assets = [{'name': f['frame_id'], 'timestamp_ms': f['timestamp_ms'], 'mime': 'image/jpeg', 'base64': 'YQ=='} for f in frames]
    payload = {'phase': 'whole-observe', 'frames': frames, 'image_frames': frames,
               'detail_frames': [], 'duration_ms': 100}
    monkeypatch.setattr(ai.urllib.request, 'urlopen', lambda *args, **kwargs: (_ for _ in ()).throw(_http_error(status, {'error': error})))
    return lambda: ai.ai_gateway_skill_content('ui_video_observe', 'prompt', payload=payload,
                                               image_assets=assets, version=version, timeout=120)


def test_touch_target_exact_gateway_timeout_maps_to_typed_retryable_error(monkeypatch):
    with pytest.raises(ai.TouchTargetProviderTimeoutError):
        _gateway_call(monkeypatch)()


@pytest.mark.parametrize('status,error,version', [
    (502, 'Request timed out.', 'touch-target'),
    (500, 'request timed out.', 'touch-target'),
    (500, 'Request timed out. extra', 'touch-target'),
    (500, 'Request timed out.', 'v1'),
])
def test_unlisted_gateway_failures_do_not_map_to_retryable_error(monkeypatch, status, error, version):
    with pytest.raises(RuntimeError) as caught:
        _gateway_call(monkeypatch, status=status, error=error, version=version)()
    assert not isinstance(caught.value, ai.TouchTargetProviderTimeoutError)


def test_run_ai_skill_passes_prompt_version_for_narrow_gateway_classification(monkeypatch):
    frames = [{'frame_id': 'frame-001', 'timestamp_ms': 0}, {'frame_id': 'frame-002', 'timestamp_ms': 100}]
    assets = [{'name': f['frame_id'], 'timestamp_ms': f['timestamp_ms'], 'mime': 'image/jpeg', 'base64': 'YQ=='} for f in frames]
    payload = {'phase': 'whole-observe', 'frames': frames, 'image_frames': frames, 'detail_frames': [], 'duration_ms': 100}
    seen = []
    def gateway(*args, **kwargs):
        seen.append(kwargs.get('version'))
        raise RuntimeError('stop after dispatch')
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY', '1')
    monkeypatch.setattr(ai, 'ai_gateway_skill_content', gateway)
    with pytest.raises(RuntimeError, match='stop after dispatch'):
        ai.run_ai_skill('ui_video_observe', payload, version='touch-target', image_assets=assets)
    assert seen == ['touch-target']


def test_probe_retries_exact_typed_timeout_once_and_keeps_same_frames(monkeypatch):
    frames = [{'frame_id': f'frame-{i:03d}', 'timestamp_ms': i * 100} for i in range(1, 4)]
    details = [{'frame_id': 'frame-002-detail', 'source_frame_id': 'frame-002', 'timestamp_ms': 200}]
    assets = [{'name': f['frame_id'], 'timestamp_ms': f['timestamp_ms']} for f in frames + details]
    calls = []
    def model(skill, payload, **kwargs):
        calls.append((payload['frames'], kwargs['image_assets']))
        if len(calls) == 1:
            raise ai.TouchTargetProviderTimeoutError('gateway request timed out')
        return {'events': [], 'uncertainties': [], 'multiple_scenarios': False}
    monkeypatch.setattr(ai, 'run_ai_skill', model)
    assert targets.probe(frames, details, assets, 1000, lambda: False) == []
    assert len(calls) == 2
    assert calls[0] == calls[1]


def test_http_error_crosses_hard_timeout_wrapper_and_retries_in_probe(monkeypatch):
    frames = [{'frame_id': f'frame-{i:03d}', 'timestamp_ms': i * 100} for i in range(1, 4)]
    detail = {'frame_id': 'frame-002-detail', 'source_frame_id': 'frame-002', 'timestamp_ms': 200}
    assets = [{'name': f['frame_id'], 'timestamp_ms': f['timestamp_ms'], 'mime': 'image/jpeg', 'base64': 'YQ=='}
              for f in frames + [detail]]
    requests = []
    calls = 0
    class Response(io.BytesIO):
        status = 200
        headers = Message()
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
    def urlopen(request, **kwargs):
        nonlocal calls
        calls += 1
        body = json.loads(request.data)
        requests.append(body)
        if calls == 1:
            raise _http_error(500, {'error': 'Request timed out.'})
        response_body = {'success': True, 'content': json.dumps({
            'events': [], 'uncertainties': [], 'multiple_scenarios': False,
        })}
        return Response(json.dumps(response_body).encode())
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY', '1')
    monkeypatch.setattr(ai.urllib.request, 'urlopen', urlopen)
    assert targets.probe(frames, [detail], assets, 1000, lambda: False) == []
    assert calls == 2
    assert all(call['skillName'] == 'ui_video_observe' for call in requests)
    assert all(call['timeoutMs'] == requests[0]['timeoutMs'] for call in requests)
    assert [asset['name'] for asset in requests[0]['imageAssets']] == [asset['name'] for asset in requests[1]['imageAssets']]


def test_probe_does_not_retry_ordinary_local_timeout(monkeypatch):
    frames = [{'frame_id': f'frame-{i:03d}', 'timestamp_ms': i * 100} for i in range(1, 4)]
    details = [{'frame_id': 'frame-002-detail', 'source_frame_id': 'frame-002', 'timestamp_ms': 200}]
    assets = [{'name': f['frame_id'], 'timestamp_ms': f['timestamp_ms']} for f in frames + details]
    calls = []
    unfinished = threading.Event()
    worker_done = threading.Event()
    worker = threading.Thread(target=lambda: (unfinished.wait(), worker_done.set()), daemon=True)
    def model(*args, **kwargs):
        calls.append(1)
        worker.start()
        raise TimeoutError('local hard timeout')
    monkeypatch.setattr(ai, 'run_ai_skill', model)
    with pytest.raises(TimeoutError, match='local hard timeout'):
        targets.probe(frames, details, assets, 1000, lambda: False)
    assert len(calls) == 1
    assert worker.is_alive()
    unfinished.set()
    assert worker_done.wait(1)


def test_probe_timeout_retry_budget_is_shared_across_invocations(monkeypatch):
    frames = [{'frame_id': f'frame-{i:03d}', 'timestamp_ms': i * 100} for i in range(1, 4)]
    details = [{'frame_id': 'frame-002-detail', 'source_frame_id': 'frame-002', 'timestamp_ms': 200}]
    assets = [{'name': f['frame_id'], 'timestamp_ms': f['timestamp_ms']} for f in frames + details]
    budget = {'used': False}
    calls = 0
    def model(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise ai.TouchTargetProviderTimeoutError('gateway request timed out')
    monkeypatch.setattr(ai, 'run_ai_skill', model)
    with pytest.raises(ai.TouchTargetProviderTimeoutError):
        targets.probe(frames, details, assets, 1000, lambda: False, timeout_retry_budget=budget)
    assert calls == 2
    with pytest.raises(ai.TouchTargetProviderTimeoutError):
        targets.probe(frames, details, assets, 1000, lambda: False, timeout_retry_budget=budget)
    assert calls == 3


def test_observation_windows_share_one_probe_timeout_retry_budget(monkeypatch, tmp_path):
    from task_server.services import ui_video_windows as windows
    frames = [{'frame_id': f'frame-{i:03d}', 'timestamp_ms': i * 100} for i in range(1, 66)]
    (tmp_path / 'frames').mkdir()
    for frame in frames:
        (tmp_path / 'frames' / f"{frame['frame_id']}.jpg").write_bytes(b'image')
    monkeypatch.setattr(windows, 'temporal_context_frames', lambda *args: [])
    from task_server.services import ui_video_touch_detail as detail
    monkeypatch.setattr(detail, 'append_details', lambda *args, **kwargs: [])
    seen = []
    def probe(*args):
        budget = args[5]
        seen.append(budget)
        if len(seen) == 1:
            budget['used'] = True
        return []
    monkeypatch.setattr(targets, 'probe', probe)
    monkeypatch.setattr(ai, 'run_ai_skill', lambda *args, **kwargs: {
        'events': [], 'uncertainties': [], 'multiple_scenarios': False,
    })
    windows.observe_windows(frames, 6500, tmp_path, lambda: False)
    assert len(seen) > 1
    assert all(budget is seen[0] for budget in seen)
    assert all(budget['used'] for budget in seen)
