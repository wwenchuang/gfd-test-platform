import copy
import pytest
from task_server.services.ui_video_analysis import _required_interactions


def terminal():
    return [
        {'action':'aiTap','target':'列表页左上角返回箭头','observation':'返回应用首页','start_ms':14000,'end_ms':15000},
        {'action':'aiTap','target':'系统返回手势','observation':'从屏幕边缘返回，应用窗口缩小，最终退出应用并回到手机桌面','start_ms':16000,'end_ms':17200},
        {'action':'aiAssert','target':'手机桌面静止状态','observation':'应用已退出，手机桌面保持静止','start_ms':17200,'end_ms':19900},
    ]


def test_terminal_system_exit_is_cleanup_but_page_return_is_required():
    assert _required_interactions(terminal(),20000)==[0]


@pytest.mark.parametrize('change', ['page_return','uncertain','later_business','early','input','no_desktop','exit_account'])
def test_do_not_drop_business_or_unproven_exit(change):
    events=copy.deepcopy(terminal())
    if change=='page_return':events[1].update(target='返回按钮',observation='返回应用首页')
    if change=='uncertain':events[1]['observation']='可能退出应用并回到手机桌面，无法确认'
    if change=='later_business':events.append({'action':'aiTap','target':'应用图标','observation':'再次打开应用','start_ms':19000,'end_ms':19500})
    if change=='early':events[1].update(start_ms=6000,end_ms=7000)
    if change=='input':events[1]['action']='aiInput'
    if change=='no_desktop':events[1]['observation']='触发系统返回手势，应用显示退出提示';events.pop()
    if change=='exit_account':events[1].update(target='退出登录按钮',observation='退出账号后显示登录页')
    assert 1 in _required_interactions(events,20000)


@pytest.mark.parametrize('observation', [
    '未能退出应用，未显示手机桌面',
    '并未完全退出应用，显示手机桌面预览',
    '退出应用，未显示手机桌面',
    '退出应用，无法确认手机桌面是否显示',
])
def test_negative_exit_evidence_remains_required(observation):
    events=terminal();events[1]['observation']=observation
    assert 1 in _required_interactions(events,20000)


def test_uncertain_exit_evidence_remains_required():
    events=terminal();events[1]['uncertainties']=['无法确认是否退出应用']
    assert 1 in _required_interactions(events,20000)
