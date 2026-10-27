import copy
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
def test_trajectory_retains_all_transport_guards(change):
    frames, assets = evidence(61 if change == 'too_many' else 1 if change == 'one' else 8)
    payload = {'phase': 'whole-trajectory', 'image_frames': frames}
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
