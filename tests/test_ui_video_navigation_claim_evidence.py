"""Rendered evidence contract only; no model/pixel or whole-source acceptance."""
import re

import pytest

from test_ui_video_visible_target_boundaries import prompt  # six production renderings


def test_navigation_modality_requires_independent_evidence_in_every_definition(prompt):
    text = prompt[-1]
    assert 'navigation可由唯一且有序的页面/控件路径支持' not in text
    definition = next(line for line in text.splitlines() if 'touch须有支持的触点' in line)
    assert re.search(r'navigation须有.*独立导航证据.*路径仅.*等价.*控件', definition)
    causal = next(p for p in text.split('\n\n') if '前进按钮' in p)
    assert re.search(r'页面变化.*按钮可见.*稳定中间帧.*转场方向.*加载消失.*均不能单独', causal)
    assert re.search(r'不能.*排除.*前一.*持续异步响应', causal)
    assert re.search(r'没有可见按压.*不能.*自动归因', causal)


def test_navigation_preservation_is_scoped_to_genuinely_independent_paths(prompt):
    text = prompt[-1]
    assert '没有明确触点也不能抹掉有唯一页面路径支持的真实导航' not in text
    assert '不得凭空猜控件、把导航改为 automatic/uncertain、删除导航来满足合同' not in text
    assert re.search(r'独立.*有序返回路径.*连续转场.*等价返回控件', text)
    assert re.search(r'不要求.*每次导航.*新触点', text)
    if prompt[0] == 'observe':
        assert '具有独立目标、路径或结果的新业务动作' not in text
        assert re.search(r'不得.*把已有独立证据支持的导航改为 automatic/uncertain', text)
        assert '边缘返回手势如果操作前可见等价返回箭头' in text
    assert re.search(r'物理次数或时长未知本身.*不.*否定.*功能交互', text)


def test_audit_unresolved_trigger_applies_to_its_own_claim_independently_of_outcome(prompt):
    if prompt[0] != 'audit':
        pytest.skip('claim verdicts are audit-only')
    text = prompt[-1]
    paragraph = next((p for p in text.split('\n\n') if '该主张的触发模态' in p), '')
    assert paragraph, 'claim-level trigger evidence guidance missing'
    assert re.search(r'该主张的触发模态、目标或因果独立性仍未决.*trigger_verdict.*unclear.*question', paragraph)
    assert re.search(r'contradicted.*实际反证', paragraph)
    assert re.search(r'outcome_verdict.*独立.*supported', paragraph)
    assert re.search(r'issues.*同一主张.*不能.*trigger_verdict.*supported', paragraph)
    assert re.search(r'无关.*问题.*不.*降级.*支持.*主张', paragraph)
    assert re.search(r'物理次数或时长未知本身.*不.*否定.*功能交互', text)
    assert '不得清除观察者已有的真实事件疑问' in text
