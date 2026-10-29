"""Offline prompt/contract checks; never evidence of model or source acceptance."""
import copy
import json
from pathlib import Path

from task_server.services import ai_skill_service as ai
from task_server.services.ui_video_perception_binding import bind_inventory

ROOT = Path(__file__).resolve().parents[1]


def test_compact_prompt_retains_full_independent_rows_and_unchanged_payload(monkeypatch):
    template = (ROOT / 'ai_skills/prompts/ui_video_perception.v1.md').read_text()
    monkeypatch.setattr(ai, 'load_ai_skill_prompt', lambda *args: template)
    payload = {'packet_id': 'generic', 'owned_frame_ids': ['frame-a'],
               'context_frame_ids': ['frame-b'], 'frames': [
                   {'frame_id': 'frame-a', 'timestamp_ms': 0},
                   {'frame_id': 'frame-b', 'timestamp_ms': 100}]}
    before = copy.deepcopy(payload)
    rendered = ai.render_ai_skill_prompt('ui_video_perception', payload)
    assert json.loads(rendered.split('输入：\n', 1)[1]) == before == payload
    assert '4000' not in rendered
    for required in ['8192', '300', '这个目标不是事实数量或单行硬上限', '按区域', '短字符串', '不得用“同上”',
                     '不得为预算省略', '逐帧独立', '顶栏/主体/底部', '光标/选区',
                     '出现、消失和再次出现', 'context_refs', 'uncertainties']:
        assert required in rendered
    schema = ai.load_ai_skill_schema('ui_video_perception')
    for field in schema['properties']['frames']['items']['required']:
        assert field in rendered


def test_region_strings_keep_all_fields_marks_context_and_questions_losslessly():
    packet = {'packet_id': 'generic', 'frames': [
        {'frame_id': 'frame-000', 'timestamp_ms': 0},
        *[{'frame_id': f'frame-{i:03d}', 'timestamp_ms': i * 100} for i in range(1, 9)]],
        'context_frame_ids': ['frame-000'],
        'owned_frame_ids': [f'frame-{i:03d}' for i in range(1, 9)]}
    rows = [{'frame_id': fid, 'role': 'owned',
             'persistent_ui': ['顶栏：白底黑字“示例”；左箭头', '主体：灰框“待辨□”；底部：蓝色“继续”'],
             'transient_marks': [{'shape': '淡圆', 'color': '灰', 'position': '右下',
                                  'nearest_visible_control': '继续', 'uncertainty': '来源未知'}],
             'field_state': '灰框文字“待辨□”；光标末尾；无选区', 'keyboard_state': '下半屏可见',
             'changes': '较frame-000：右下淡圆出现',
             'context_refs': [{'frame_id': 'frame-000', 'role': 'context'}],
             'uncertainties': ['□不可辨']} for fid in packet['owned_frame_ids']]
    raw = {'packet_id': 'generic', 'frames': rows, 'uncertainties': ['键盘底部遮挡']}
    before = copy.deepcopy(raw)
    ai.validate_ai_skill_output('ui_video_perception', raw)
    bank = bind_inventory(raw, packet)
    assert raw == before and bank['uncertainties'] == raw['uncertainties']
    assert len(bank['frames']) == 8
    for source, bound in zip(raw['frames'], bank['frames']):
        assert {k: v for k, v in bound.items() if k != 'evidence_refs'} == {
            k: v for k, v in source.items() if k != 'context_refs'}
        assert bound['evidence_refs'] == source['context_refs']
    assert bank['binding_audit']['qualified'] is False
