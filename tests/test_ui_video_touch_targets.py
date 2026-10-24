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


@pytest.mark.parametrize('target,match',[
    ('查看生成记录按钮',True),('“查看生成记录”',True),('查看生成记录入口',True),
    ('底部查看生成记录',False),('查看打印记录',False),('查看记录',False),
])
def test_target_comparison_ignores_only_control_suffix(target,match):
    frames=[{'frame_id':'f1','timestamp_ms':100}]
    events=[{'action':'aiTap','target':target,'frame_refs':['f1']}]
    diagnostic={}
    result=targets.conflict(events,[{'frame_id':'f1','target':'查看生成记录'}],frames,diagnostic)
    assert (not result)==match
    assert diagnostic=={'frame_id':'f1','binding':'explicit','comparison':'normalized_match' if match else 'mismatch'}


def test_empty_control_label_is_not_normalized_to_other_empty_label():
    frames=[{'frame_id':'f1','timestamp_ms':100}]
    assert targets.conflict([{'action':'aiTap','target':'按钮','frame_refs':['f1']}],
                            [{'frame_id':'f1','target':'入口'}],frames)


def test_probe_allows_structured_response_beyond_old_truncation_limit(monkeypatch):
    frames=[{'frame_id':'f1','timestamp_ms':100},{'frame_id':'f2','timestamp_ms':200}]
    detail={'frame_id':'f1-detail','source_frame_id':'f1','timestamp_ms':100}
    assets=[{'name':f['frame_id']} for f in frames+[detail]]
    def call(*args,**kw):
        if kw['max_tokens']<4000:raise RuntimeError('Structured output truncated: finish_reason=length')
        assert kw['retry_count']==0 and kw['max_tokens']<=5000
        return {'events':[],'uncertainties':['没有可靠点击证据'],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',call)
    assert targets.probe(frames,[detail],assets,1000,lambda:False)==[]

@pytest.mark.parametrize('left,right,matched', [
    ('我的作品区域右侧的查看生成记录文字链接','查看生成记录',True),
    ('我的作品区域上方的查看生成记录入口','查看生成记录',True),
    ('我的作品区域上方的查看生成记录文字链接','查看生成记录',True),
    ('我的作品区域上方的查看生成记录入口','我的作品区域下方的查看生成记录入口',False),
    ('查看生成记录','我的作品区域右侧的查看生成记录文字链接',True),
    ('我的作品区域右侧的查看生成记录文字链接','查看打印记录',False),
    ('我的作品区域右侧的查看生成记录文字链接','收藏区域右侧的查看生成记录文字链接',False),
    ('我的作品区域左侧的查看生成记录文字链接','我的作品区域右侧的查看生成记录文字链接',False),
    ('查看生成记录中的删除按钮','查看生成记录',False),
])
def test_same_explicit_touch_accepts_one_qualified_exact_control_label(left,right,matched):
    frames=[{'frame_id':'f1','timestamp_ms':100}]
    events=[{'action':'aiTap','target':left,'frame_refs':['f1']}]
    assert (not targets.conflict(events,[{'frame_id':'f1','target':right}],frames))==matched


def test_nearby_frame_does_not_waive_qualified_target_difference():
    frames=[{'frame_id':'f1','timestamp_ms':100},{'frame_id':'f2','timestamp_ms':200}]
    events=[{'action':'aiTap','target':'我的作品区域右侧的查看生成记录文字链接','frame_refs':['f2']}]
    assert targets.conflict(events,[{'frame_id':'f1','target':'查看生成记录'}],frames)


def test_observe_window_keeps_qualified_target_without_spurious_retry(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as detail
    frames=[{'frame_id':'frame-001','timestamp_ms':100},{'frame_id':'frame-002','timestamp_ms':200}]
    qualified='我的作品区域右侧的查看生成记录文字链接'
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-001','target':'查看生成记录'}])
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        return {'events':[{'action':'aiTap','target':qualified,'value':'','frame_refs':['frame-001','frame-002'],
                           'observation':'记录列表可见','uncertainties':[]}], 'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    result,_=windows.observe_windows(frames,1000,tmp_path,lambda:False)
    assert len(calls)==1
    assert result['events'][0]['target']==qualified
    assert result['events'][0]['frame_refs']==['frame-001','frame-002']


@pytest.mark.parametrize('qualified',[
    '订单区域上方的查看详情链接','订单区域右上方的查看详情按钮',
    '订单区域左下方的查看详情入口','订单区域右下角的查看详情文字链接',
])
def test_explicit_same_frame_qualified_labels_support_diagonal_positions(qualified):
    frames=[{'frame_id':'f1','timestamp_ms':100}]
    events=[{'action':'aiTap','target':qualified,'frame_refs':['f1']}]
    assert not targets.conflict(events,[{'frame_id':'f1','target':'查看详情'}],frames)


def test_diagonal_qualifications_do_not_equate_conflicting_regions():
    frames=[{'frame_id':'f1','timestamp_ms':100}]
    events=[{'action':'aiTap','target':'订单区域右上方的查看详情按钮','frame_refs':['f1']}]
    assert targets.conflict(events,[{'frame_id':'f1','target':'订单区域左下方的查看详情按钮'}],frames)
