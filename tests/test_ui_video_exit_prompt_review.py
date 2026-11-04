from task_server.services.ui_video_analysis import _exit_prompt_review


def event(action,note):
    return {'action':action,'observation':note}


def test_unexplained_exit_prompt_requires_review():
    events=[event('navigateBack','从记录页回到首页'),
            event('aiAssert','首页显示“再试一次退出智小白3DAPP”的提示'),
            event('navigateBack','应用退出到桌面')]
    assert _exit_prompt_review(events)


def test_same_outcome_frame_does_not_prove_trigger():
    events=[{**event('navigateBack','用户返回'), 'outcome':{'observation':'显示再按一次退出的提示','frame_refs':['f2']}},
            {**event('aiAssert','页面显示再按一次退出提示'),'frame_refs':['f2']}]
    assert _exit_prompt_review(events)


def test_exit_prompt_in_target_or_other_action_still_requires_review():
    assert _exit_prompt_review([{'action':'aiAssert','target':'再按一次退出提示','observation':'提示可见'}])
    assert _exit_prompt_review([event('aiTap','屏幕出现再次点击退出提示')])
    assert _exit_prompt_review([event('aiAssert','页面显示再按一次返回键关闭应用')])


def test_unrelated_wait_or_exit_text_does_not_create_review():
    assert not _exit_prompt_review([event('aiAssert','页面显示任务传输中')])
    assert not _exit_prompt_review([event('aiAssert','应用已经退出至系统桌面')])
