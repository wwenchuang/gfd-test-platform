import copy
import pytest
from task_server.services.ui_video_analysis import _bind_whole_evidence


def result(refs):
    return {'events':[{'action':'aiTap','target':'入口','value':'','frame_refs':refs,'observation':'页面跳转'}]}


@pytest.mark.parametrize('refs,reason',[
    ([], 'count'),(['a']*9,'count'),(['outside-private-name'],'outside_window'),
    (['a','a'],'duplicate'),(['b','a'],'order'),
])
def test_invalid_references_have_fixed_feedback_without_echoing_model_text(refs,reason):
    raw=result(refs);before=copy.deepcopy(raw)
    with pytest.raises(ValueError,match='video_reference_invalid') as caught:
        _bind_whole_evidence(raw,[{'frame_id':'a','timestamp_ms':10},{'frame_id':'b','timestamp_ms':20}])
    assert caught.value.reference_reason==reason
    assert caught.value.model_feedback
    assert 'outside-private-name' not in caught.value.model_feedback
    assert raw==before


def test_event_order_feedback_does_not_silently_reorder_actions():
    raw=result(['b']);raw['events']+=result(['a'])['events']
    with pytest.raises(ValueError,match='video_reference_invalid') as caught:
        _bind_whole_evidence(raw,[{'frame_id':'a','timestamp_ms':10},{'frame_id':'b','timestamp_ms':20}])
    assert caught.value.reference_reason=='event_order'


def test_observation_repair_receives_specific_feedback_and_keeps_retry_budget(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows,ai_skill_service as ai
    fs=[{'frame_id':'a','timestamp_ms':10},{'frame_id':'b','timestamp_ms':20}]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[]
    def model(skill,payload,**kwargs):
        calls.append(copy.deepcopy(payload))
        return result(['outside-private-name'])
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_reference_invalid'):
        windows.observe_windows(fs,30,tmp_path,lambda:False)
    assert len(calls)==2
    assert '本窗之外' in calls[1]['validation_feedback']
    assert 'outside-private-name' not in calls[1]['validation_feedback']
