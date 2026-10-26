"""Sequential result history must not become one current-screen assertion."""
import pytest
from task_server.services.ui_video_result_quality import needs_result_review, review_requirements


def event(text):
    return {'action':'aiTap','target':'提交按钮','outcome':{'observation':text}}


@pytest.mark.parametrize('text',[
    '页面显示正在提交打印及正在传输数据状态，随后进入任务传输中页面，包含取消打印与继续打印按钮。',
    '显示上传中，然后进入文件详情页面，包含预览按钮。',
    '页面显示处理中，最终出现受理结果页。',
])
def test_sequential_process_states_request_pixels(text):
    assert needs_result_review(event(text),text)


@pytest.mark.parametrize('text',[
    '任务传输中页面可见，包含取消打印与继续打印按钮。',
    '文件详情页面可见，包含预览按钮。',
    '页面显示处理中，包含取消按钮。',
])
def test_single_observed_state_remains_usable(text):
    assert not needs_result_review(event(text),text)


def test_history_in_audit_does_not_reject_final_state():
    assert not needs_result_review(event('先显示上传中，然后进入文件详情页面。'),'文件详情页面可见，包含预览按钮。')


def test_temporal_review_only_adds_boolean_requirement():
    r=review_requirements(event('私人任务123显示上传中，然后进入文件详情页面。'))
    assert r['check_final_visible_state_only'] is True
    assert '私人' not in str(r)


@pytest.mark.parametrize('observation',['任务传输中页面可见','提交表单保持显示','弹窗可见，包含确认和取消按钮'])
def test_result_review_always_requests_last_visible_state_even_without_prior_sequence(observation):
    assert review_requirements(event(observation))['check_final_visible_state_only'] is True
