import pytest


@pytest.mark.parametrize('version', ['coverage', 'coverage-observe'])
def test_repair_prompt_keeps_unconfirmed_gesture_as_return(version):
    from task_server.services.ai_skill_service import render_ai_skill_prompt
    prompt=render_ai_skill_prompt('ui_video_observe', {'frames':[]}, version=version)
    assert '没有实际点击证据' in prompt
    assert 'navigateBack' in prompt and '上一页面' in prompt
    assert '跨端等价动作' not in prompt
    assert '跨端等价返回' not in prompt
    assert 'events用aiTap该可见箭头' not in prompt
    assert '用aiTap该可见箭头' not in prompt


def test_coverage_prompt_does_not_claim_back_from_tap_is_same_action():
    from task_server.services.ai_skill_service import render_ai_skill_prompt
    prompt=render_ai_skill_prompt('ui_video_observe', {'frames':[]}, version='coverage')
    assert 'same_return_gesture只适合同动作类型' in prompt
    assert '跨动作类型不得通过same_return_gesture合并' in prompt


@pytest.mark.parametrize('skill,version', [
    ('ui_video_observe','touch-target'),
    ('ui_video_steps','batch'),
    ('ui_video_steps','v1'),
])
def test_touch_and_planning_prompts_do_not_invent_arrow_click(skill,version):
    from task_server.services.ai_skill_service import render_ai_skill_prompt
    prompt=render_ai_skill_prompt(skill,{'frames':[]},version=version)
    assert '没有实际点击证据' in prompt
    assert 'navigateBack' in prompt and '上一页面' in prompt
    assert '跨端等价动作' not in prompt
    assert '表达等价返回' not in prompt
    if version=='touch-target':
        assert '只输出一次有证据的aiTap或navigateBack' in prompt
        assert '实际点击' in prompt
    if version=='batch':
        assert 'action仅aiTap/aiInput/aiScroll/aiWaitFor/aiAssert/navigateBack' in prompt


@pytest.mark.parametrize('version', ['batch','v1'])
def test_steps_action_allowlists_include_native_semantic_actions(version):
    from task_server.services.ai_skill_service import render_ai_skill_prompt
    prompt=render_ai_skill_prompt('ui_video_steps',{},version=version)
    assert 'aiTap/aiInput/aiScroll/aiWaitFor/aiAssert/navigateBack/dismissKeyboard' in prompt


def test_invalid_gesture_target_feedback_does_not_invent_arrow_click():
    from task_server.services.ui_video_analysis import _bind_candidate_evidence
    with pytest.raises(ValueError) as caught:
        _bind_candidate_evidence({'events':[{'action':'aiTap','target':'屏幕边缘返回手势'}]},[])
    feedback=caught.value.model_feedback
    assert '没有实际点击证据' in feedback and 'navigateBack' in feedback
    assert '箭头可见不等于点击' in feedback and '表达等价返回' not in feedback


@pytest.mark.parametrize('version',['batch','v1'])
def test_steps_keyboard_target_contract_is_explicit(version):
    from task_server.services.ai_skill_service import render_ai_skill_prompt
    prompt=render_ai_skill_prompt('ui_video_steps',{},version=version)
    assert '使用dismissKeyboard，target为当前页面的软键盘、value为空' in prompt


def test_observe_template_distinguishes_unresolved_and_target_hints():
    from task_server.services.ai_skill_service import render_ai_skill_prompt
    hint={'frame_id':'frame-002','kind':'unresolved_observation','questions':['触点尚不确定'],'multiple_scenarios':False}
    prompt=render_ai_skill_prompt('ui_video_observe',{'touch_targets':[hint]},version='v1')
    assert 'unresolved_observation' in prompt and '没有target' in prompt
    assert '不能作为动作或点击证据' in prompt and '触点尚不确定' in prompt
    assert '这些是独立小范围复核在对应原帧确认的触点控件名' not in prompt


def test_touch_probe_contract_allows_only_evidenced_return():
    from task_server.services.ai_skill_service import render_ai_skill_prompt
    prompt=render_ai_skill_prompt('ui_video_observe',{},version='touch-target')
    assert '页面变化本身不证明主动返回' in prompt
    assert '本探针仅提示应用业务控件点击' not in prompt


def test_whole_observation_does_not_hardcode_nameplate_return_path():
    from task_server.services.ai_skill_service import render_ai_skill_prompt
    prompt=render_ai_skill_prompt('ui_video_observe',{'review_mode':'full_flow_after_recovery'},version='v1')
    assert '不套用特定应用路径' in prompt
    assert '预览→3D摆放→文字字体编辑→详情→列表是返回路径' not in prompt
