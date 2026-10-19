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
