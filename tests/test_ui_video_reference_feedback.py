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


@pytest.mark.parametrize('review',[False,True])
def test_outside_window_retry_resends_only_local_pixels(monkeypatch,tmp_path,review):
    from task_server.services import ui_video_windows as windows,ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(65)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    monkeypatch.setattr(windows,'context_frames',lambda *args:[])
    calls=[]
    def model(skill,payload,**kwargs):
        calls.append((copy.deepcopy(payload),copy.deepcopy(kwargs['image_assets'])))
        if len(calls)==1:
            assert payload['context_frame_ids']
            return result([payload['context_frame_ids'][-1]])
        assert payload['context_frame_ids']==[]
        assert payload['temporal_context_frame_ids']==[]
        assert payload['image_frames']==payload['frames']
        assert [a['name'] for a in kwargs['image_assets']]==[f['frame_id'] for f in payload['frames']]
        raise RuntimeError('stop-after-retry')
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(RuntimeError,match='stop-after-retry'):
        windows.observe_windows(fs,6500,tmp_path,lambda:False,review_events=[] if review else None)
    assert len(calls)==2
    assert calls[0][0]['frames']==calls[1][0]['frames']


@pytest.mark.parametrize('refs,issue',[(None,'not_array'),('private-data','not_array'),([], 'empty'),(['private-data']*9,'too_many')])
def test_worker_logs_reference_shape_without_model_content(caplog,refs,issue):
    import json
    from task_server.workers.ui_video_worker import _log_failure
    with pytest.raises(ValueError) as caught:
        _bind_whole_evidence(result(refs),[{'frame_id':'a','timestamp_ms':10}])
    _log_failure('uvs_'+'a'*32,'analysis',caught.value,'video_reference_invalid')
    detail=json.loads(caplog.records[-1].getMessage().split(' ',1)[1])
    assert detail['reference_reason']=='count'
    assert detail['reference_shape']==issue
    assert 'private-data' not in caplog.text


def test_worker_does_not_log_arbitrary_reference_attributes(caplog):
    from task_server.workers.ui_video_worker import _log_failure
    error=ValueError('video_reference_invalid')
    error.reference_reason='private-data'
    error.reference_shape='private-data'
    _log_failure('uvs_'+'a'*32,'analysis',error,'video_reference_invalid')
    assert 'private-data' not in caplog.text


@pytest.mark.parametrize('frame,binding,logged',[
    ('frame-099','explicit',True),('private-data','explicit',False),
    ('frame-099',['private-data'],False),
])
def test_worker_target_diagnostic_is_content_free(caplog,frame,binding,logged):
    import json
    from task_server.workers.ui_video_worker import _log_failure
    error=ValueError('video_model_invalid')
    error.target_diagnostic={'frame_id':frame,'binding':binding,'comparison':'mismatch','target':'private-data'}
    _log_failure('uvs_'+'a'*32,'analysis',error,'video_model_invalid')
    detail=json.loads(caplog.records[-1].getMessage().split(' ',1)[1])
    assert ('target_diagnostic' in detail)==logged
    assert 'private-data' not in caplog.text
