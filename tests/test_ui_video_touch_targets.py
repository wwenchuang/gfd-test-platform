import pytest
from task_server.services import ui_video_touch_targets as targets


def test_target_conflict_near_pointer_requires_correction():
    events=[{'action':'aiTap','target':'底部首页','frame_refs':['frame-003']}]
    frames=[{'frame_id':'frame-002','timestamp_ms':100},{'frame_id':'frame-003','timestamp_ms':133}]
    hints=[{'frame_id':'frame-002','target':'查看生成记录'}]
    assert '查看生成记录' in targets.conflict(events,hints,frames)
    events[0]['target']='查看生成记录'
    assert targets.conflict(events,hints,frames)==''


def test_pointer_hint_does_not_bind_unrelated_later_click():
    events=[{'action':'aiTap','target':'首页','frame_refs':['frame-003']}]
    frames=[{'frame_id':'frame-002','timestamp_ms':100},{'frame_id':'frame-003','timestamp_ms':2000}]
    assert targets.conflict(events,[{'frame_id':'frame-002','target':'查看生成记录'}],frames)==''


def test_detail_probe_requires_parent_reference_and_single_certain_tap(monkeypatch):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,7)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':'YQ=='} for f in frames+[detail]]
    response={'events':[{'action':'aiTap','target':'查看生成记录','value':'','frame_refs':['frame-002','frame-003'],'observation':'记录页可见','uncertainties':[]}],'uncertainties':[],'multiple_scenarios':False}
    def call(skill,payload,**kw):
        assert payload['detail_frames']==[detail]
        assert len(kw['image_assets'])<=6
        assert kw['version']=='touch-target'
        return response
    monkeypatch.setattr(targets.ai,'run_ai_skill',call)
    assert targets.probe(frames,[detail],assets,1000,lambda:False)==[{'frame_id':'frame-002','target':'查看生成记录','observation':'记录页可见'}]
    response['events'][0]['uncertainties']=['触点不清楚']
    assert targets.probe(frames,[detail],assets,1000,lambda:False)==[]
    response['events'][0]['uncertainties']=[];response['events'][0]['frame_refs']=['frame-003']
    assert targets.probe(frames,[detail],assets,1000,lambda:False)==[]


def test_cancelled_probe_does_not_call_model(monkeypatch):
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:pytest.fail('cancelled model call'))
    with pytest.raises(ValueError,match='video_cancelled'):
        targets.probe([], [{}], [], 1000, lambda:True)


def test_pointer_reference_binds_even_with_earlier_before_frame():
    frames=[{'frame_id':'f1','timestamp_ms':100},{'frame_id':'f2','timestamp_ms':800}]
    events=[{'action':'aiTap','target':'底部首页','frame_refs':['f1','f2']}]
    assert '查看生成记录' in targets.conflict(events,[{'frame_id':'f2','target':'查看生成记录'}],frames)
