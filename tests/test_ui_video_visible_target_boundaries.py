"""Offline rendered guidance contracts, not model recognition or source acceptance."""
import copy
import json
from pathlib import Path
import re

import pytest

from task_server.services import ai_skill_service as ai
from task_server.services import ui_video_trajectory as vt
from test_ui_video_business_episode_contract import EXAMPLES, example


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=[(version, domain, bounded)
                       for version, domain in [('observe', None), ('audit', 'v1'), ('audit', 'v2')]
                       for bounded in (False, True)])
def prompt(request, monkeypatch):
    version, domain, bounded = request.param
    # Load the actual repository template through the production loader/renderer.
    monkeypatch.setattr(ai, 'AI_SKILLS_DIR', str(ROOT / 'ai_skills'))
    payload = {
        'owned_frame_ids': ['owned-original'], 'context_frame_ids': ['context-original'],
        'revisit_context_anchors': bounded, 'action_contract': vt.action_contract(),
        'uncertainties': ['UNVERIFIED_TARGET_QUESTION'],
        'audit_feedback': {'positive_trigger_locks': [{
            'action': 'aiTap', 'target': '合成区域', 'value': '',
            'trigger_frame': 'owned-original', 'trigger_kind': 'touch', 'continuation_of': '',
        }], 'issues': []},
    }
    if domain:
        payload['reference_contract'] = {
            'version': f'packet-audit-reference-{domain}', 'check_event_ids': [],
            'issue_owner_frame_ids': ['owned-original'],
            'image_frame_ids': ['owned-original', 'context-original'],
        }
        if domain == 'v2':
            payload['reference_contract']['future_issue_owner_frame_ids'] = ['context-original']
            payload['forward_context_questions'] = [{'question': 'UNVERIFIED_FORWARD_QUESTION'}]
    before = copy.deepcopy(payload)
    text = ai.render_ai_skill_prompt('ui_video_trajectory', payload, version=version)
    assert payload == before
    return version, domain, bounded, payload, text


def test_visible_unlabelled_region_requires_own_original_trigger_and_local_response(prompt):
    text = prompt[-1]
    paragraphs = text.split('\n\n')
    region = next((p for p in paragraphs if '语义定位' in p and '空白区域' in p), '')
    assert region, 'visible semantic region guidance missing from actual prompt'
    assert re.search(r'可见控件.*可见目标.*包括.*无文字标签', region)
    assert re.search(r'自身.*原帧触发证据.*时间.*因果.*局部响应', region)
    assert re.search(r'空白.*本身.*键盘消失.*无关灰.*不足', region)
    assert re.search(r'目标.*模态.*次数.*时序.*疑问.*保留', region)


def test_equivalent_navigation_cannot_manufacture_forward_click_from_prior_async_response(prompt):
    text = prompt[-1]
    paragraph = next((p for p in text.split('\n\n') if '前进按钮' in p), '')
    assert paragraph, 'forward navigation causal limitation missing from actual prompt'
    assert re.search(r'唯一.*有序.*已.*证据.*导航.*等价可见控件', paragraph)
    assert re.search(r'前一.*交互.*持续异步响应.*不能.*下一页面.*前进按钮.*点击', paragraph)
    assert re.search(r'触发.*归属.*疑问.*保留', paragraph)


def test_residual_feedback_compares_identity_trajectory_and_onset_before_classification(prompt):
    text = prompt[-1]
    paragraph = next((p for p in text.split('\n\n') if '判为残留反馈前' in p), '')
    assert paragraph, 'residual classification comparison missing from actual prompt'
    assert re.search(r'页面.*对象.*控件.*身份.*反馈.*轨迹', paragraph)
    assert re.search(r'独立.*业务边界.*保留', paragraph)
    assert re.search(r'起始.*前后原帧.*不能.*尾段', paragraph)
    assert re.search(r'不能.*覆盖标记.*物理点击次数.*按压时长', paragraph)


def test_payload_examples_ownership_locks_and_unverified_gates_remain(prompt):
    version, domain, bounded, payload, text = prompt
    start = text.index('\n{') + 1
    assert json.JSONDecoder().raw_decode(text[start:])[0] == payload
    assert example(text) == EXAMPLES[version]
    assert '{{revisit_contract}}' not in text
    assert ('context端点仅作定位' in text) is bounded
    for required in ('不能自动清空历史疑问', 'owned_frame_ids', 'context_frame_ids',
                     '原始', '候选仍须独立审核', 'no_new_frames', 'UNVERIFIED_TARGET_QUESTION'):
        assert required in text
    if version == 'observe':
        for required in ('trigger_frame必须在owned_frame_ids', '不是结果支持',
                         'action、target、value、trigger_frame、trigger_kind、continuation_of',
                         '逐字复制', '次数', '不能借后次结果'):
            assert required in text
    else:
        for required in ('先做完整性复核', 'unresolved_evidence', 'check_event_ids',
                         'issue_owner_frame_ids', '不得清除观察者已有的真实事件疑问'):
            assert required in text
        if domain == 'v2':
            assert 'instruction_authority=false' in text
            assert '当前checks全supported或保持沉默也不能清除' in text
    # New guidance contains no literal IDs, times, numeric counts or quoted answers.
    guidance = [p for p in text.split('\n\n')
                if any(marker in p for marker in ('语义定位', '前进按钮', '判为残留反馈前'))]
    assert len(guidance) == 3
    for paragraph in guidance:
        assert not re.search(r'\d|frame[-_]|timestamp|["“”]', paragraph)
