import copy
import io
import json
import pytest
from task_server.services import ai_skill_service as ai


def evidence(count=8):
    frames = [{'frame_id': f'frame-{i:03d}', 'timestamp_ms': i * 100} for i in range(count)]
    return frames, [{'name': f['frame_id'], 'timestamp_ms': f['timestamp_ms'], 'mime': 'image/jpeg', 'base64': 'YQ=='} for f in frames]


def test_trajectory_complete_timeline_and_direct_reference_roles(monkeypatch):
    frames, assets = evidence()
    payload = {'phase': 'whole-trajectory', 'image_frames': frames, 'context_frame_ids': ['frame-007']}
    assert ai._video_context_assets('ui_video_trajectory', payload, assets)
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY', '0')
    monkeypatch.setattr(ai, 'render_ai_skill_prompt', lambda *a, **k: 'trajectory')
    monkeypatch.setattr(ai, 'validate_ai_skill_output', lambda *a, **k: {})
    calls = []
    monkeypatch.setattr(ai, 'dashscope_chat_content', lambda *a, **k: calls.append(k) or '{}')
    ai.run_ai_skill('ui_video_trajectory', payload, image_assets=assets)
    assert calls[0]['image_limit'] == 60
    assert calls[0]['chronological_images'] is True
    assert calls[0]['reference_frame_ids'] == ['frame-007']
    content = ai.build_dashscope_chat_body('trajectory', **{k: calls[0][k] for k in ('image_assets', 'image_limit', 'chronological_images', 'reference_frame_ids', 'trajectory_context')})['messages'][1]['content']
    assert len([p for p in content if p['type'] == 'image_url']) == 8
    assert content[1]['text'] == 'frame-000 0ms [当前窗口证据，可引用]'
    assert content[-2]['text'] == 'frame-007 700ms [可引用为前后态或结果证据；不得作为本包新动作触发]'


@pytest.mark.parametrize('change', ['one', 'too_many', 'oversized', 'identity', 'timestamp', 'mime', 'order', 'duplicate', 'detail'])
@pytest.mark.parametrize('fine_detail', [False, True])
def test_trajectory_retains_all_transport_guards(change, fine_detail):
    frames, assets = evidence(61 if change == 'too_many' else 1 if change == 'one' else 8)
    payload = {'phase': 'whole-trajectory', 'image_frames': frames}
    if fine_detail:
        payload['perception_mode'] = 'fine-detail'
    if change == 'oversized': assets[0]['base64'] = 'a' * (10 * 1024 * 1024 + 1)
    if change == 'identity': assets[0]['name'] = 'forged'
    if change == 'timestamp': assets[0]['timestamp_ms'] = 10
    if change == 'mime': assets[0]['mime'] = 'image/png'
    if change == 'order': frames.reverse(); assets.reverse()
    if change == 'duplicate': frames[1] = copy.deepcopy(frames[0]); assets[1] = copy.deepcopy(assets[0])
    if change == 'detail': payload['detail_frames'] = [{'frame_id': 'frame-000-detail'}]
    with pytest.raises(ValueError, match='video_(vision_limit|frames_invalid)'):
        ai._video_context_assets('ui_video_trajectory', payload, assets)


@pytest.mark.parametrize('skill,phase', [('ui_video_trajectory_extra', 'whole-trajectory'), ('ui_video_trajectory', 'whole-observe'), ('other', 'whole-trajectory')])
def test_trajectory_transport_exact_scope(skill, phase):
    assert not ai._video_context_assets(skill, {'phase': phase}, [])


@pytest.mark.parametrize('mode', [None, True, False, 1, 1658880, '', 'fine', 'FINE-DETAIL', [], {}])
def test_fine_detail_rejects_explicit_invalid_mode(mode):
    frames, assets = evidence()
    with pytest.raises(ValueError, match='video_perception_mode_invalid'):
        ai._video_context_assets('ui_video_trajectory', {'phase': 'whole-trajectory', 'image_frames': frames, 'perception_mode': mode}, assets)


@pytest.mark.parametrize('host', ['dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com', 'dashscope-us.aliyuncs.com'])
@pytest.mark.parametrize('json_response', [True, False])
def test_fine_detail_changes_only_fixed_sibling_pixels(monkeypatch, host, json_response):
    _, assets = evidence()
    original = copy.deepcopy(assets)
    monkeypatch.setattr(ai, 'dashscope_model_for_images', lambda _: 'qwen3.8-max')
    monkeypatch.setattr(ai, 'dashscope_base_url', lambda: f'https://{host}/compatible-mode/v1')
    options = dict(image_assets=assets, image_limit=60, chronological_images=True,
                   reference_frame_ids=['frame-007'], trajectory_context=True, max_tokens=2048,
                   json_response=json_response)
    base = ai.build_dashscope_chat_body('trajectory', **options)
    fine = ai.build_dashscope_chat_body('trajectory', **options, bounded_trajectory_detail=True)
    images = [part for part in fine['messages'][1]['content'] if part['type'] == 'image_url']
    assert len(images) == len(assets)
    assert all(part.pop('min_pixels') == 1658880 for part in images)
    assert fine == base
    assert assets == original
    assert fine.get('enable_thinking') is (False if json_response else None)
    assert 'thinking_budget' not in fine


@pytest.mark.parametrize('model,base', [
    ('qwen3.8-flash', 'https://dashscope.aliyuncs.com/v1'),
    ('qwen3.7-plus', 'https://dashscope.aliyuncs.com/v1'),
    ('QWEN3.8-MAX', 'https://dashscope.aliyuncs.com/v1'),
    ('qwen3.8-max', 'http://dashscope.aliyuncs.com/v1'),
    ('qwen3.8-max', 'https://example.com/v1'),
    ('qwen3.8-max', 'https://dashscope.aliyuncs.com.evil.example/v1'),
    ('qwen3.8-max', 'https://dashscope.aliyuncs.com@evil.example/v1'),
])
def test_fine_detail_rejects_unsupported_direct_route_only_when_requested(monkeypatch, model, base):
    _, assets = evidence()
    monkeypatch.setattr(ai, 'dashscope_model_for_images', lambda _: model)
    monkeypatch.setattr(ai, 'dashscope_base_url', lambda: base)
    assert all('min_pixels' not in p for p in ai.build_dashscope_chat_body('base', image_assets=assets)['messages'][1]['content'])
    with pytest.raises(ValueError, match='video_perception_route_unsupported'):
        ai.build_dashscope_chat_body('fine', image_assets=assets, bounded_trajectory_detail=True)


@pytest.mark.parametrize('flag', [False, None, 1, 'true'])
def test_direct_detail_requires_boolean_true(monkeypatch, flag):
    _, assets = evidence()
    monkeypatch.setattr(ai, 'dashscope_model_for_images', lambda _: 'other')
    monkeypatch.setattr(ai, 'dashscope_base_url', lambda: 'https://example.com/v1')
    base = ai.build_dashscope_chat_body('base', image_assets=assets)
    assert ai.build_dashscope_chat_body('base', image_assets=assets, bounded_trajectory_detail=flag) == base


@pytest.mark.parametrize('skill,phase,mode,enabled', [
    ('ui_video_trajectory', 'whole-trajectory', 'fine-detail', True),
    ('ui_video_trajectory', 'whole-trajectory', 'absent', False),
    ('ui_video_trajectory', 'whole-observe', 'fine-detail', False),
    ('ui_video_trajectory_extra', 'whole-trajectory', None, False),
    ('ui_video_observe', 'whole-observe', True, False),
])
def test_run_skill_passes_only_exact_detail_capability(monkeypatch, skill, phase, mode, enabled):
    frames, assets = evidence()
    payload = {'phase': phase, 'image_frames': frames}
    if mode != 'absent':
        payload['perception_mode'] = mode
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY', '0')
    monkeypatch.setattr(ai, 'render_ai_skill_prompt', lambda *a, **k: 'trajectory')
    monkeypatch.setattr(ai, 'validate_ai_skill_output', lambda *a, **k: {})
    calls = []
    monkeypatch.setattr(ai, 'dashscope_chat_content', lambda *a, **k: calls.append(k) or '{}')
    ai.run_ai_skill(skill, payload, image_assets=assets, timeout=120, retry_count=0, max_tokens=2048)
    assert calls[0].get('bounded_trajectory_detail', False) is enabled
    assert calls[0].get('bounded_trajectory_reasoning', False) is False
    assert (calls[0]['timeout'], calls[0]['retry_count'], calls[0]['max_tokens']) == (120, 0, 2048)


def test_direct_chat_forwards_detail_to_wire_without_changing_call_limits(monkeypatch):
    _, assets = evidence()
    monkeypatch.setattr(ai, 'dashscope_model_for_images', lambda _: 'qwen3.8-max')
    monkeypatch.setattr(ai, 'dashscope_base_url', lambda: 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    monkeypatch.setattr(ai, 'dashscope_api_key', lambda: 'offline-test')
    calls = []
    def urlopen(request, timeout):
        calls.append((json.loads(request.data), timeout))
        return io.BytesIO(b'{"choices":[{"message":{"content":"{}"}}]}')
    monkeypatch.setattr(ai.urllib.request, 'urlopen', urlopen)
    ai.dashscope_chat_content('trajectory', image_assets=assets, image_limit=60, timeout=120,
                             respect_global_timeout=False, retry_count=0, max_tokens=2048,
                             bounded_trajectory_detail=True)
    assert len(calls) == 1 and calls[0][1] == 120
    body = calls[0][0]
    assert body['max_tokens'] == 2048 and body['enable_thinking'] is False
    assert all(p['min_pixels'] == 1658880 for p in body['messages'][1]['content'] if p['type'] == 'image_url')


def test_gateway_skill_retains_fine_detail_payload_and_frame_identity(monkeypatch):
    frames, assets = evidence()
    payload = {'phase': 'whole-trajectory', 'image_frames': frames, 'perception_mode': 'fine-detail'}
    original = copy.deepcopy((payload, assets))
    calls = []
    def urlopen(request, timeout):
        calls.append((json.loads(request.data), timeout))
        return io.BytesIO(b'{"success":true,"content":"{}"}')
    monkeypatch.setattr(ai.urllib.request, 'urlopen', urlopen)
    ai.ai_gateway_skill_content('ui_video_trajectory', 'trajectory', payload=payload,
                                image_assets=assets, timeout=120, max_tokens=2048)
    body, timeout = calls[0]
    assert body['payload'] == payload
    assert [{k: a[k] for k in assets[0]} for a in body['imageAssets']] == assets
    assert body['timeoutMs'] == 118000 and timeout == 120 and body['maxTokens'] == 2048
    assert (payload, assets) == original
