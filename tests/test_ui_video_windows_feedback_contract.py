import copy

from task_server.services import ai_skill_service as ai
from task_server.services import ui_video_windows as windows


def test_second_observation_prompt_separates_root_contract_from_event_fields(monkeypatch, tmp_path):
    frames = [
        {'frame_id': 'frame-001', 'timestamp_ms': 0},
        {'frame_id': 'frame-002', 'timestamp_ms': 100},
    ]
    (tmp_path / 'frames').mkdir()
    for frame in frames:
        (tmp_path / 'frames' / f"{frame['frame_id']}.jpg").write_bytes(b'image')

    calls = []
    rendered = []

    def model(skill, payload, **kwargs):
        calls.append(copy.deepcopy(payload))
        rendered.append(ai.render_ai_skill_prompt(skill, payload, version=kwargs.get('version', 'v1')))
        if len(calls) == 1:
            # Missing root uncertainties and multiple_scenarios must remain invalid.
            return {
                'events': [{
                    'action': 'aiTap', 'target': '入口', 'value': '',
                    'frame_refs': ['frame-001', 'frame-002'],
                    'observation': '页面跳转', 'uncertainties': [],
                }]
            }
        return {
            'events': [{
                'action': 'aiTap', 'target': '入口', 'value': '',
                'frame_refs': ['frame-001', 'frame-002'],
                'observation': '页面跳转', 'uncertainties': [],
            }],
            'uncertainties': [],
            'multiple_scenarios': False,
        }

    monkeypatch.setattr(ai, 'run_ai_skill', model)
    result, _ = windows.observe_windows(frames, 100, tmp_path, lambda: False)

    assert len(calls) == 2
    assert calls[1]['validation_feedback']
    prompt = rendered[1]
    assert '根对象必须同时包含events数组、uncertainties数组和multiple_scenarios布尔值' in prompt
    assert 'multiple_scenarios必须根据画面判断' in prompt
    assert '不得照抄示例值' in prompt
    assert '每个事件仅包含action、target、value、frame_refs、observation及可选uncertainties' in prompt
    assert 'frame_refs只引用当前窗frames中的真实帧，按时间排序，完整保留当前窗操作' in prompt
    assert 'context_frame_ids' in prompt
    assert '不能作为事件证据' in prompt
    assert '事件不输出start_ms/end_ms/evidence_kind或其它字段' in prompt
    assert 'multiple_scenarios:false' not in calls[1]['validation_feedback']
    assert result['events'][0]['frame_refs'] == ['frame-001', 'frame-002']
