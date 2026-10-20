import copy
import pytest

@pytest.mark.parametrize('cancel,confirm,end', [('1',2,3),(True,2,3),(2,1,3),(1,2,4),(1,2,2)])
def test_recovery_index_failure_has_safe_correction(cancel,confirm,end):
    from task_server.services.ui_video_print_recovery import prepare_group
    plan={'print_recovery':{'submit':0,'cancel':cancel,'confirm':confirm,'end':end}}
    before=copy.deepcopy(plan)
    with pytest.raises(ValueError,match='video_retry_invalid') as caught:
        prepare_group(plan,[{}]*4,[])
    assert 'candidate_index' in caught.value.model_feedback
    assert 'cancel < confirm < end' in caught.value.model_feedback
    assert plan==before

@pytest.mark.parametrize('action,target,selection',[('aiTap','查看生成记录',''),('aiTap','查看生成记录','任意记录'),('aiAssert','{{本次对象}}','任意记录')])
def test_selection_failure_has_specific_safe_correction(action,target,selection):
    from task_server.services.ui_video_analysis import validate_analysis
    event={'action':action,'target':target,'value':'','start_ms':0,'end_ms':0,'frame_refs':['f'],
           'observation':'页面显示','evidence_kind':'observed','uncertainties':[],'selection':selection}
    with pytest.raises(ValueError,match='video_selection_invalid') as caught:
        validate_analysis({'events':[event],'uncertainties':[],'multiple_scenarios':False},[{'frame_id':'f','timestamp_ms':0}],100)
    assert 'selection' in caught.value.model_feedback
    assert '省略' in caught.value.model_feedback

@pytest.mark.parametrize('observation', [
    '页面自动跳转至反馈页，显示取消原因选项列表，未见用户手动点击导航入口。',
    '本次任务卡片已消失；期间未观察到用户点击操作。',
])
def test_terminal_observation_allows_explicit_absence_of_user_input(observation):
    from task_server.services.ui_video_print_recovery import prepare_group
    candidates=[{'action':'aiTap','target':t} for t in ('确认打印','取消打印','确认')]
    candidates.append({'action':'aiAssert','target':'终态','observation':observation})
    plan={'events':[], 'print_recovery':{'submit':0,'cancel':1,'confirm':2,'end':3}}
    assert prepare_group(plan,candidates,[0,1,2])['print_recovery']=={'submit':0,'cancel':1,'confirm':2}

@pytest.mark.parametrize('observation', [
    '尚未进入取消原因页，未见用户手动点击导航入口。',
    '任务卡片没有消失，未见用户手动点击导航入口。',
    '可能进入取消原因页，未见用户手动点击导航入口。',
    '无法确认任务已消失，期间未观察到用户点击操作。',
    '取消原因页未显示，未见用户手动点击导航入口。',
])
def test_terminal_observation_still_rejects_uncertain_or_negated_result(observation):
    from task_server.services.ui_video_print_recovery import prepare_group
    candidates=[{'action':'aiTap','target':t} for t in ('确认打印','取消打印','确认')]
    candidates.append({'action':'aiAssert','target':'终态','observation':observation})
    with pytest.raises(ValueError,match='video_retry_invalid'):
        prepare_group({'events':[], 'print_recovery':{'submit':0,'cancel':1,'confirm':2,'end':3}},candidates,[0,1,2])


def test_wrong_submit_boundary_returns_specific_safe_feedback():
    from task_server.services.ui_video_print_recovery import bind_group
    candidates=[{'action':'aiTap','target':t} for t in ('确认按钮','检查无误，确认打印按钮','取消打印按钮','确认按钮')]
    plan={'events':[{'candidate_index':i} for i in range(4)],'print_recovery':{'submit':0,'cancel':2,'confirm':3}}
    with pytest.raises(ValueError,match='video_retry_invalid') as caught:
        bind_group(plan,candidates)
    assert '最终确认打印' in caught.value.model_feedback
    assert 'submit' in caught.value.model_feedback
