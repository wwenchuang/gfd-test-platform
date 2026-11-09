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
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    assert hints[0]['requires_review'] is True
    response['events'][0]['uncertainties']=[];response['events'][0]['frame_refs']=['frame-003']
    assert targets.probe(frames,[detail],assets,1000,lambda:False)==[]


def test_probe_preserves_parent_bound_navigate_back_as_review_hint(monkeypatch):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,5)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    response={'events':[{'action':'navigateBack','target':'上一页面','value':'','frame_refs':['frame-002','frame-003'],
                         'observation':'当前页面返回上一页面','uncertainties':[]}],
              'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:response)
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    assert hints==[{'frame_id':'frame-002','action':'navigateBack','target':'上一页面',
                    'observation':'当前页面返回上一页面','requires_review':False,'questions':[],
                    'multiple_scenarios':False}]


def test_uncertain_navigate_back_probe_is_not_discarded(monkeypatch):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,5)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    response={'events':[{'action':'navigateBack','target':'上一页面','value':'','frame_refs':['frame-002'],
                         'observation':'当前页面返回上一页面','uncertainties':['转场中，无法确认操作来源']}],
              'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:response)
    assert targets.probe(frames,[detail],assets,1000,lambda:False)==[
        {'frame_id':'frame-002','action':'navigateBack','target':'上一页面',
         'observation':'当前页面返回上一页面','requires_review':True,
         'questions':['转场中，无法确认操作来源'],'multiple_scenarios':False}]


def test_uncertain_tap_probe_carries_multiple_scenarios(monkeypatch):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,5)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    response={'events':[{'action':'aiTap','target':'确认按钮','value':'','frame_refs':['frame-002'],
                         'observation':'可能点击确认','uncertainties':['触点不清楚']}],
              'uncertainties':[],'multiple_scenarios':True}
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:response)
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    assert hints[0]['scope']=='probe' and hints[0]['multiple_scenarios'] is True
    assert hints[1]['target']=='确认按钮' and hints[1]['questions']==['触点不清楚']


def test_uncertain_tap_hint_propagates_multiple_scenarios_to_root():
    events=[{'action':'aiTap','target':'确认按钮','frame_refs':['f2'],'uncertainties':[]}]
    analysis={'multiple_scenarios':False,'uncertainties':[]}
    questions=targets.bind_uncertain_hints(events,[{'frame_id':'f2','target':'确认按钮',
        'requires_review':True,'questions':['触点不清楚'],'multiple_scenarios':True}],analysis)
    assert analysis['multiple_scenarios'] is True
    assert any('多个可能场景' in q for q in questions)
    assert any('触点目标' in q for q in events[0]['uncertainties'])


def test_confident_same_frame_navigate_back_agreement_is_not_blocked():
    events=[{'action':'navigateBack','target':'上一页面','frame_refs':['f2'],'uncertainties':[]}]
    hints=[{'frame_id':'f2','action':'navigateBack','target':'上一页面','requires_review':False,'questions':[]}]
    assert targets.bind_uncertain_hints(events,hints)==[]
    assert events[0]['uncertainties']==[]


def test_unmatched_confident_return_hint_becomes_source_frame_root_question():
    question=targets.bind_uncertain_hints([], [{'frame_id':'f2','action':'navigateBack',
        'target':'上一页面','requires_review':False,'questions':[]}])
    assert any('局部复核提示' in q and '动作归因疑问' in q for q in question)


def test_local_multiple_scenarios_sets_root_flag_and_keeps_generic_question():
    events=[{'action':'navigateBack','target':'上一页面','frame_refs':['f2'],'uncertainties':[]}]
    root={'multiple_scenarios':False,'uncertainties':[]}
    hint={'frame_id':'f2','action':'navigateBack','target':'上一页面','requires_review':True,
          'questions':[],'multiple_scenarios':True}
    question=targets.bind_uncertain_hints(events,[hint],root)
    assert root['multiple_scenarios'] is True
    assert question and any('场景' in q for q in question)
    assert any('场景' in q for q in events[0]['uncertainties'])


def test_conflict_and_unmatched_question_hints_are_both_preserved():
    events=[{'action':'aiTap','target':'确认按钮','frame_refs':['f1'],'uncertainties':[]}]
    hints=[{'frame_id':'f1','action':'navigateBack','target':'上一页面','requires_review':True,'questions':[]},
           {'frame_id':'f2','action':'navigateBack','target':'上一页面','requires_review':True,
            'questions':['第二帧过渡来源不确定']}]
    question=targets.bind_uncertain_hints(events,hints)
    assert any('局部复核提示' in q and '动作归因疑问' in q for q in question)
    assert any('第二帧过渡来源不确定' in q for q in question)
    assert any('动作判断冲突' in q for q in question)
    assert any('动作判断冲突' in q for q in events[0]['uncertainties'])


def test_return_hint_with_same_frame_assertion_and_tap_blocks_only_tap():
    assertion={'action':'aiAssert','target':'页面仍在预览','frame_refs':['frame-332'],
               'uncertainties':[]}
    tap={'action':'aiTap','target':'确认按钮','frame_refs':['frame-332'],'uncertainties':[]}
    events=[assertion,tap]
    questions=targets.bind_uncertain_hints(events,[{'frame_id':'frame-332','action':'navigateBack',
        'target':'上一页面','requires_review':True,'questions':[]}])
    assert any('动作判断冲突' in q for q in questions)
    assert events[0] is assertion and assertion['uncertainties']==[]
    assert events[1] is tap and any('动作判断冲突' in q for q in tap['uncertainties'])


def test_probe_fails_closed_when_navigation_and_uncertain_tap_share_output(monkeypatch):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,5)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    response={'events':[
        {'action':'navigateBack','target':'上一页面','value':'','frame_refs':['frame-002'],
         'observation':'页面返回上一页','uncertainties':[]},
        {'action':'aiTap','target':'确认按钮','value':'','frame_refs':['frame-002'],
         'observation':'按钮可能被点击','uncertainties':['触点不确定']}],
        'uncertainties':[],'multiple_scenarios':True}
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:response)
    with pytest.raises(ValueError,match='video_model_invalid'):
        targets.probe(frames,[detail],assets,1000,lambda:False)


def test_probe_fails_closed_when_confident_navigation_and_tap_share_output(monkeypatch):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,5)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    response={'events':[
        {'action':'navigateBack','target':'上一页面','value':'','frame_refs':['frame-002'],
         'observation':'页面返回上一页','uncertainties':[]},
        {'action':'aiTap','target':'确认按钮','value':'','frame_refs':['frame-002'],
         'observation':'确认按钮被点击','uncertainties':[]}],
        'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:response)
    with pytest.raises(ValueError,match='video_model_invalid'):
        targets.probe(frames,[detail],assets,1000,lambda:False)


def test_same_frame_navigate_back_hint_blocks_click_without_rewriting_action():
    events=[{'action':'aiTap','target':'确认打印按钮','frame_refs':['frame-002'],'uncertainties':[]}]
    hints=[{'frame_id':'frame-002','action':'navigateBack','target':'上一页面',
            'observation':'当前页面返回上一页面','requires_review':True}]
    question=targets.bind_uncertain_hints(events,hints)
    assert question and any('动作判断' in q for q in question)
    assert events[0]['action']=='aiTap' and events[0]['target']=='确认打印按钮'
    assert any('动作判断' in item for item in events[0]['uncertainties'])


def test_navigate_back_hint_on_another_frame_is_global_not_bound_to_click():
    events=[{'action':'aiTap','target':'确认打印按钮','frame_refs':['frame-002'],'uncertainties':[]}]
    hints=[{'frame_id':'frame-003','action':'navigateBack','target':'上一页面',
            'observation':'当前页面返回上一页面','requires_review':True}]
    assert any('动作归因疑问' in q for q in targets.bind_uncertain_hints(events,hints))
    assert events[0]['action']=='aiTap' and events[0]['uncertainties']==[]


def test_action_hint_without_same_frame_tap_survives_as_root_question():
    question=targets.bind_uncertain_hints([], [{'frame_id':'frame-002','action':'navigateBack',
        'target':'上一页面','observation':'页面返回上一页','requires_review':True}])
    assert any('局部复核提示' in q and '动作归因疑问' in q for q in question)


def test_observe_window_surfaces_same_frame_return_vs_tap_as_root_blocker(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as detail
    frames=[{'frame_id':'frame-001','timestamp_ms':100},{'frame_id':'frame-002','timestamp_ms':200}]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-001','action':'navigateBack',
        'target':'上一页面','observation':'当前页面返回上一页面','requires_review':True}])
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:{'events':[{'action':'aiTap','target':'确认按钮',
        'value':'','frame_refs':['frame-001'],'observation':'确认按钮被点击','uncertainties':[]}],
        'uncertainties':[],'multiple_scenarios':False})
    result,_=windows.observe_windows(frames,1000,tmp_path,lambda:False)
    assert result['events'][0]['action']=='aiTap' and result['events'][0]['target']=='确认按钮'
    assert any('动作判断冲突' in q for q in result['events'][0]['uncertainties'])
    assert any('动作判断冲突' in q for q in result['uncertainties'])
    assert result['uncertainties'].count(next(q for q in result['uncertainties'] if '动作判断冲突' in q))==1


def test_observe_window_adds_hint_questions_once_and_enforces_root_budget(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as detail
    frames=[{'frame_id':'frame-001','timestamp_ms':100},{'frame_id':'frame-002','timestamp_ms':200}]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    source_question='局部来源问题'
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-001','action':'navigateBack',
        'target':'上一页面','requires_review':True,'questions':[source_question]}])
    def model(*a,**kw):
        return {'events':[{'action':'aiTap','target':'按钮','value':'','frame_refs':['frame-001'],
            'observation':'点击按钮','uncertainties':[]}], 'uncertainties':[source_question], 'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    result,_=windows.observe_windows(frames,1000,tmp_path,lambda:False)
    assert result['uncertainties'].count(source_question)==1
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:{'events':[{'action':'aiTap','target':'按钮',
        'value':'','frame_refs':['frame-001'],'observation':'点击按钮','uncertainties':[]}],
        'uncertainties':[f'问题{i}' for i in range(30)],'multiple_scenarios':False})
    with pytest.raises(ValueError,match='video_model_invalid'):
        windows.observe_windows(frames,1000,tmp_path,lambda:False)


def test_observe_window_preserves_local_multiple_scenarios_with_matching_return(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as detail
    frames=[{'frame_id':'frame-001','timestamp_ms':100},{'frame_id':'frame-002','timestamp_ms':200}]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-001','action':'navigateBack',
        'target':'上一页面','observation':'页面返回上一页','requires_review':True,'questions':[],
        'multiple_scenarios':True}])
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:{'events':[{'action':'navigateBack','target':'上一页面',
        'value':'','frame_refs':['frame-001'],'observation':'页面返回上一页','uncertainties':[]}],
        'uncertainties':[],'multiple_scenarios':False})
    result,_=windows.observe_windows(frames,1000,tmp_path,lambda:False)
    assert result['multiple_scenarios'] is True
    assert any('场景' in q for q in result['uncertainties'])
    assert any('场景' in q for q in result['events'][0]['uncertainties'])


def test_action_hint_root_question_reaches_draft_execution_gate():
    from task_server.services.ui_video_analysis import draft_proposal
    from task_server.services.ui_case_draft_contract import build_draft_yaml
    blocker='；'.join(targets.bind_uncertain_hints([], [{'frame_id':'f2','action':'navigateBack','target':'上一页面',
        'observation':'转场来源不确定','requires_review':True,'questions':['转场来源不确定']}])
    )
    document={'app_id':'com.example.app','source_id':'uvs_'+'0'*32,'analysis_version':1,
        'goal':'','title':'页面操作','media':{'frames':[{'frame_id':'f2','timestamp_ms':100}], 'duration_ms':100},
        'events':[{'action':'aiTap','target':'确认按钮','value':'','frame_refs':['f2'],'start_ms':100,'end_ms':100,
                   'evidence_kind':'inferred','observation':'确认按钮','uncertainties':[]}],
        'uncertainties':[blocker]}
    proposal,_=draft_proposal(document)
    assert any('转场来源不确定' in question for question in proposal['questions'])
    assert not build_draft_yaml(proposal,document['app_id'])['ready']


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
    assert targets.probe(frames,[detail],assets,1000,lambda:False)==[{
        'frame_id':'f1','frame_refs':['f1','f2'],'scope':'probe','kind':'unresolved_observation','questions':['没有可靠点击证据'],'multiple_scenarios':False}]

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


@pytest.mark.parametrize('left,right',[
    ('第二条记录区域的删除图标','删除'),
    ('删除','第二条记录区域的删除图标'),
    ('模型管理区域的打印记录图标','打印记录'),
    ('打印记录','模型管理区域的打印记录图标'),
])
def test_same_frame_bare_icon_without_unique_region_evidence_still_conflicts(left,right):
    frames=[{'frame_id':'f1','timestamp_ms':100}]
    events=[{'action':'aiTap','target':left,'frame_refs':['f1']}]
    assert targets.conflict(events,[{'frame_id':'f1','target':right}],frames)


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


@pytest.mark.parametrize('always_invalid',[False,True])
def test_probe_schema_correction_is_bounded_and_keeps_pixels(monkeypatch,always_invalid):
    import copy
    frames=[{'frame_id':'frame-001','timestamp_ms':100},{'frame_id':'frame-002','timestamp_ms':200}]
    detail={'frame_id':'frame-001-detail','source_frame_id':'frame-001','timestamp_ms':100}
    assets=[{'name':f['frame_id']} for f in frames+[detail]]
    calls=[]
    def call(*args,**kwargs):
        calls.append(copy.deepcopy((args[1],kwargs['image_assets'])))
        if len(calls)==1 or always_invalid:raise targets.ai.SkillSchemaError('$.events','required')
        return {'events':[],'uncertainties':['无可靠点击'],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',call)
    if always_invalid:
        with pytest.raises(targets.ai.SkillSchemaError):targets.probe(frames,[detail],assets,1000,lambda:False)
    else:
        hints=targets.probe(frames,[detail],assets,1000,lambda:False)
        assert len(hints)==1 and hints[0]['kind']=='unresolved_observation'
        assert '无可靠点击' in hints[0]['questions']
        assert any('首轮' in q and '遗漏' in q for q in hints[0]['questions'])
    assert len(calls)==2 and calls[0][1]==calls[1][1]
    assert calls[0][0]['frames']==calls[1][0]['frames']
    assert 'events' in calls[1][0]['validation_feedback']


@pytest.mark.parametrize('error',[RuntimeError('provider'),TimeoutError('timeout')])
def test_probe_service_errors_are_not_schema_retries(monkeypatch,error):
    calls=[]
    def call(*args,**kw):calls.append(1);raise error
    monkeypatch.setattr(targets.ai,'run_ai_skill',call)
    frames=[{'frame_id':'f1','timestamp_ms':100},{'frame_id':'f2','timestamp_ms':200}]
    detail={'frame_id':'detail','source_frame_id':'f1'}
    with pytest.raises(type(error)):targets.probe(frames,[detail],[{'name':f['frame_id']} for f in frames+[detail]],1000,lambda:False)
    assert len(calls)==1


def test_probe_schema_budget_shared_across_details(monkeypatch):
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,4)]
    details=[{'frame_id':f'd{i}','source_frame_id':f'f{i}'} for i in (1,2)]
    calls=[]
    def call(*args,**kw):
        calls.append(1)
        if len(calls)!=2:raise targets.ai.SkillSchemaError('$.events','required')
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',call)
    with pytest.raises(targets.ai.SkillSchemaError):
        targets.probe(frames,details,[{'name':f['frame_id']} for f in frames+details],1000,lambda:False)
    assert len(calls)==3


def test_probe_cancel_after_schema_failure_does_not_retry(monkeypatch):
    calls=[]
    def call(*args,**kw):
        calls.append(1)
        raise targets.ai.SkillSchemaError('$.events','required')
    monkeypatch.setattr(targets.ai,'run_ai_skill',call)
    frames=[{'frame_id':'f1','timestamp_ms':100},{'frame_id':'f2','timestamp_ms':200}]
    detail={'frame_id':'detail','source_frame_id':'f1'}
    with pytest.raises(ValueError,match='video_cancelled'):
        targets.probe(frames,[detail],[{'name':f['frame_id']} for f in frames+[detail]],1000,lambda:bool(calls))
    assert len(calls)==1


@pytest.mark.parametrize('left,right,matched', [
    ('测试名称文本输入框','测试名称',True),
    ('测试名称','测试名称输入框',True),
    ('“测试名称”文本输入框','测试名称',True),
    ('文本输入框','',False),
    ('测试名称文本输入框','测试名称按钮',False),
    ('测试名称文本输入框','测试名称入口',False),
    ('测试名称文本输入框','其他名称',False),
    ('上方测试名称文本输入框','测试名称',False),
    ('测试名称文本输入框','测试名称二文本输入框',False),
])
def test_same_explicit_touch_accepts_input_type_only_with_exact_bare_label(left,right,matched):
    frames=[{'frame_id':'f1','timestamp_ms':100}]
    events=[{'action':'aiTap','target':left,'frame_refs':['f1']}]
    assert (not targets.conflict(events,[{'frame_id':'f1','target':right}],frames))==matched


def test_nearby_touch_does_not_equate_input_type_with_bare_label():
    frames=[{'frame_id':'f1','timestamp_ms':100},{'frame_id':'f2','timestamp_ms':200}]
    events=[{'action':'aiTap','target':'测试名称文本输入框','frame_refs':['f2']}]
    assert targets.conflict(events,[{'frame_id':'f1','target':'测试名称'}],frames)


def test_same_frame_accepts_exact_print_label_with_panel_location_only():
    frames=[{'frame_id':'frame-166','timestamp_ms':100}]
    events=[{'action':'aiTap','target':'打印设置面板底部的确认打印按钮','frame_refs':['frame-166']}]
    assert not targets.conflict(events,[{'frame_id':'frame-166','target':'确认打印'}],frames)
    assert targets.conflict(events,[{'frame_id':'frame-166','target':'取消打印'}],frames)
    assert targets.conflict(events,[{'frame_id':'frame-166','target':'打印设置面板顶部的确认打印按钮'}],frames)
    nearby=[{'frame_id':'frame-165','timestamp_ms':50},*frames]
    assert targets.conflict(events,[{'frame_id':'frame-165','target':'确认打印'}],nearby)


@pytest.mark.parametrize('uncertainty_path',['event','root'])
def test_probe_does_not_discard_uncertain_tap_target(monkeypatch,uncertainty_path):
    frames=[{'frame_id':'f1','timestamp_ms':100},{'frame_id':'f2','timestamp_ms':200}]
    detail={'frame_id':'detail','source_frame_id':'f1','timestamp_ms':100}
    assets=[{'name':f['frame_id']} for f in frames+[detail]]
    response={'events':[{'action':'aiTap','target':'删除','value':'','frame_refs':['f1','f2'],
                         'observation':'点击记录中的删除','uncertainties':[]}],
              'uncertainties':[],'multiple_scenarios':False}
    (response['events'][0] if uncertainty_path=='event' else response)['uncertainties']=['无法确认是哪条记录']
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:response)
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    assert any(h.get('requires_review') is True for h in hints)
    events=[{'action':'aiTap','target':'删除','frame_refs':['f1','f2'],'uncertainties':[]}]
    assert targets.conflict(events,hints,frames)==''
    targets.bind_uncertain_hints(events,hints)
    assert any('核对' in q for q in events[0]['uncertainties'])


def test_uncertain_probe_cannot_disappear_or_bind_to_nearby_event():
    hints=[{'frame_id':'f1','target':'删除','requires_review':True}]
    for events in ([],[{'action':'aiTap','target':'删除','frame_refs':['f2'],'uncertainties':[]}],
                   [{'action':'aiTap','target':'删除','frame_refs':['f1'],'uncertainties':[]},
                    {'action':'aiTap','target':'删除','frame_refs':['f1'],'uncertainties':[]}]):
        with pytest.raises(ValueError,match='video_model_invalid'):targets.bind_uncertain_hints(events,hints)


def test_observe_window_retains_uncertain_probe_as_review_blocker(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as detail
    frames=[{'frame_id':'frame-001','timestamp_ms':100},{'frame_id':'frame-002','timestamp_ms':200}]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-001','target':'应用入口','requires_review':True}])
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:{'events':[{'action':'aiTap','target':'应用入口',
        'value':'','frame_refs':['frame-001','frame-002'],'observation':'页面发生变化','uncertainties':[]}],
        'uncertainties':[],'multiple_scenarios':False})
    result,_=windows.observe_windows(frames,1000,tmp_path,lambda:False)
    assert any('触点' in question for question in result['uncertainties'])
    assert any('触点' in question for question in result['events'][0]['uncertainties'])


@pytest.mark.parametrize('qualified',['第二条记录的删除按钮','第二条打印记录的删除按钮','第二条生成记录的删除按钮'])
def test_ambiguous_bare_probe_cannot_downgrade_a_qualified_target(monkeypatch,tmp_path,qualified):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as detail
    frames=[{'frame_id':'frame-001','timestamp_ms':100},{'frame_id':'frame-002','timestamp_ms':200}]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-001','target':'删除'}])
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        target=qualified if len(calls)==1 else '删除'
        return {'events':[{'action':'aiTap','target':target,'value':'','frame_refs':['frame-001','frame-002'],
                           'observation':'点击删除','uncertainties':[]}], 'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_invalid'):
        windows.observe_windows(frames,1000,tmp_path,lambda:False)
    assert len(calls)==1


def test_empty_probe_question_reaches_root_and_blocks_draft(monkeypatch):
    from task_server.services.ui_video_analysis import draft_proposal
    from task_server.services.ui_case_draft_contract import build_draft_yaml
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,4)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:{'events':[],
        'uncertainties':['无法判断是点击还是返回'],'multiple_scenarios':False})
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    assert hints==[{'frame_id':'frame-002','frame_refs':['frame-001','frame-002','frame-003'],'scope':'probe','kind':'unresolved_observation',
                    'questions':['无法判断是点击还是返回'],'multiple_scenarios':False}]
    events=[{'action':'aiTap','target':'确认按钮','value':'','frame_refs':['frame-002'],
             'start_ms':200,'end_ms':200,'evidence_kind':'inferred','observation':'确认按钮可见','uncertainties':[]}]
    before=[dict(events[0])]
    assert targets.conflict(events,hints,frames)==''
    root=targets.bind_uncertain_hints(events,hints)
    assert events==before and any('局部复核待核对' in q and '无法判断' in q for q in root)
    document={'app_id':'com.example.app','source_id':'uvs_'+'0'*32,'analysis_version':1,
        'goal':'','title':'页面操作','media':{'frames':frames,'duration_ms':1000},
        'events':events,'uncertainties':root}
    proposal,_=draft_proposal(document)
    assert any('无法判断' in q for q in proposal['questions'])
    assert not build_draft_yaml(proposal,document['app_id'])['ready']


def test_empty_probe_unresolved_multiple_scenarios_is_frame_bound_without_action(monkeypatch):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,4)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:{'events':[],
        'uncertainties':[],'multiple_scenarios':True})
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    analysis={'multiple_scenarios':False}
    questions=targets.bind_uncertain_hints([],hints,analysis)
    assert analysis['multiple_scenarios'] is True
    assert any('场景' in q for q in questions)


def test_probe_rejects_detail_not_bound_to_real_frame():
    frames=[{'frame_id':'frame-001','timestamp_ms':100},{'frame_id':'frame-002','timestamp_ms':200}]
    detail={'frame_id':'frame-999-detail','source_frame_id':'frame-999','timestamp_ms':200}
    with pytest.raises(ValueError,match='video_reference_invalid'):
        targets.probe(frames,[detail],[],1000,lambda:False)


def test_unbound_event_root_uncertainty_remains_bound_to_probe_parent(monkeypatch):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,4)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:{'events':[{'action':'aiTap','target':'入口',
        'value':'','frame_refs':['frame-003'],'observation':'页面显示','uncertainties':[]}],
        'uncertainties':['不确定父帧动作'],'multiple_scenarios':True})
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    assert hints==[{'frame_id':'frame-002','frame_refs':['frame-001','frame-002','frame-003'],'scope':'probe','kind':'unresolved_observation',
                    'questions':['不确定父帧动作'],'multiple_scenarios':True}]
    analysis={'multiple_scenarios':False}
    questions=targets.bind_uncertain_hints([],hints,analysis)
    assert analysis['multiple_scenarios'] is True
    assert any('局部复核待核对' in q and '不确定父帧动作' in q for q in questions)


def test_unbound_event_uncertainties_keep_actual_event_frames(monkeypatch):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,5)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    events=[{'action':'aiTap','target':'入口','value':'','frame_refs':['frame-001'],'observation':'页面显示','uncertainties':['点击是否发生']},
            {'action':'aiAssert','target':'列表','value':'','frame_refs':['frame-003'],'observation':'列表显示','uncertainties':['结果是否稳定']}]
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:{'events':events,'uncertainties':[],'multiple_scenarios':False})
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    assert hints==[{'frame_id':'frame-001','frame_refs':['frame-001'],'scope':'event','kind':'unresolved_observation','questions':['点击是否发生'],'multiple_scenarios':False},
                   {'frame_id':'frame-003','frame_refs':['frame-003'],'scope':'event','kind':'unresolved_observation','questions':['结果是否稳定'],'multiple_scenarios':False}]
    questions=targets.bind_uncertain_hints([],hints)
    assert any('局部复核待核对' in q and '点击是否发生' in q for q in questions)
    assert any('局部复核待核对' in q and '结果是否稳定' in q for q in questions)
    assert not any('frame-002' in q for q in questions)


def test_unbound_unknown_action_fails_closed(monkeypatch):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,4)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    event={'action':'fakeAction','target':'入口','value':'','frame_refs':['frame-003'],'observation':'页面','uncertainties':['不确定']}
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:{'events':[event],'uncertainties':[],'multiple_scenarios':False})
    with pytest.raises(ValueError,match='video_action_invalid'):
        targets.probe(frames,[detail],assets,1000,lambda:False)


def test_parent_bound_unhandled_assertion_keeps_event_question(monkeypatch):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,4)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    event={'action':'aiAssert','target':'加载中','value':'','frame_refs':['frame-002'],
           'observation':'页面有加载图标','uncertainties':['是否发生点击无法判断']}
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:{'events':[event],'uncertainties':[],'multiple_scenarios':False})
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    assert hints==[{'frame_id':'frame-002','frame_refs':['frame-002'],'scope':'event','kind':'unresolved_observation',
                    'questions':['是否发生点击无法判断'],'multiple_scenarios':False}]


def test_unbound_input_question_redacts_value_without_damaging_frame(monkeypatch):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,4)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    event={'action':'aiInput','target':'名称输入框','value':'1','frame_refs':['frame-003'],
           'observation':'输入1','uncertainties':['输入1是否正确']}
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:{'events':[event],'uncertainties':[],'multiple_scenarios':False})
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    assert hints[0]['frame_id']=='frame-003' and '1' not in ''.join(hints[0]['questions'])


def test_probe_root_and_event_questions_keep_full_trusted_refs_when_redacted(monkeypatch):
    from task_server.services.ui_video_analysis import validate_analysis
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,5)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    event={'action':'aiTap','target':'入口','value':'','frame_refs':['frame-001','frame-003'],
           'observation':'页面显示','uncertainties':['事件1不确定']}
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:{'events':[event],
        'uncertainties':['整体1不确定'],'multiple_scenarios':False})
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    questions=targets.bind_uncertain_hints([],hints)
    checked=validate_analysis({'events':[],'uncertainties':questions,'multiple_scenarios':False},frames,1000,['1'])
    assert hints[0]['frame_refs']==['frame-001','frame-003'] and hints[0]['scope']=='event'
    assert hints[1]['frame_refs']==['frame-001','frame-002','frame-003'] and hints[1]['scope']=='probe'
    assert any('事件[输入内容未保存]不确定' in q for q in checked['uncertainties'])
    assert any('整体[输入内容未保存]不确定' in q for q in checked['uncertainties'])
    assert all('frame-' not in q for q in checked['uncertainties'])


@pytest.mark.parametrize('invalid_field',['target','value'])
def test_probe_retries_only_navigation_contract_with_sticky_questions(monkeypatch,invalid_field):
    import copy
    frames=[{'frame_id':f'frame-00{i}','timestamp_ms':i*100} for i in (1,2,3)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    nav={'action':'navigateBack','target':'姓名牌' if invalid_field=='target' else '上一页面',
         'value':'secret-value' if invalid_field=='value' else '','frame_refs':['frame-002'],
         'observation':'页面返回','uncertainties':['用户输入secret-value需要核对']}
    typed={'action':'aiInput','target':'名称','value':'secret-value','frame_refs':['frame-003'],
           'observation':'输入','uncertainties':[]}
    calls=[]
    def model(skill,payload,**kw):
        calls.append(copy.deepcopy((payload,kw['image_assets'])))
        if len(calls)==1:return {'events':[nav,typed],'uncertainties':['根问题secret-value'],'multiple_scenarios':True}
        assert '姓名牌' not in repr(payload) and 'secret-value' not in repr(payload)
        assert 'navigateBack' in payload['validation_feedback'] and '上一页面' in payload['validation_feedback']
        corrected={**nav,'target':'上一页面','value':'','uncertainties':[]}
        return {'events':[corrected],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    assert len(calls)==2 and calls[0][1]==calls[1][1] and calls[0][0]['frames']==calls[1][0]['frames']
    assert 'secret-value' not in repr(hints)
    unresolved=[h for h in hints if h.get('kind')=='unresolved_observation']
    assert unresolved and unresolved[0]['multiple_scenarios'] is True
    assert any('根问题' in q for q in unresolved[0]['questions'])
    assert any('用户输入' in q for q in unresolved[0]['questions'])
    assert any(h.get('action')=='navigateBack' for h in hints)


@pytest.mark.parametrize('mutation',['other_action','dismiss_target','bad_refs'])
def test_probe_does_not_retry_unrelated_errors(monkeypatch,mutation):
    frames=[{'frame_id':f'frame-00{i}','timestamp_ms':i*100} for i in (1,2,3)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id']} for f in frames+[detail]];calls=[]
    event={'action':'navigateBack','target':'上一页面','value':'','frame_refs':['frame-002'],
           'observation':'返回页面','uncertainties':[]}
    if mutation=='other_action':event['action']='fakeAction'
    if mutation=='dismiss_target':event.update(action='dismissKeyboard',target='名称')
    if mutation=='bad_refs':event['frame_refs']=['unknown']
    def model(*a,**kw):calls.append(1);return {'events':[event],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError):targets.probe(frames,[detail],assets,1000,lambda:False)
    assert len(calls)==1


def test_probe_navigation_contract_retry_fails_closed_after_second_invalid(monkeypatch):
    frames=[{'frame_id':f'frame-00{i}','timestamp_ms':i*100} for i in (1,2,3)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id']} for f in frames+[detail]];calls=[]
    event={'action':'navigateBack','target':'错误目标','value':'','frame_refs':['frame-002'],'observation':'返回','uncertainties':[]}
    def model(*a,**kw):calls.append(1);return {'events':[event],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_action_invalid'):targets.probe(frames,[detail],assets,1000,lambda:False)
    assert len(calls)==2


def test_probe_navigation_retry_uses_shared_schema_budget(monkeypatch):
    frames=[{'frame_id':f'frame-00{i}','timestamp_ms':i*100} for i in (1,2,3)]
    details=[{'frame_id':'frame-001-detail','source_frame_id':'frame-001'},
             {'frame_id':'frame-002-detail','source_frame_id':'frame-002'}]
    assets=[{'name':f['frame_id']} for f in frames+details];calls=[]
    event={'action':'navigateBack','target':'错误目标','value':'','frame_refs':['frame-002'],'observation':'返回','uncertainties':[]}
    def model(*a,**kw):
        calls.append(1)
        if len(calls)==1:raise targets.ai.SkillSchemaError('$.events','required')
        if len(calls)==2:return {'events':[],'uncertainties':[],'multiple_scenarios':False}
        return {'events':[event],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_action_invalid'):targets.probe(frames,details,assets,1000,lambda:False)
    assert len(calls)==3


def test_probe_invalid_navigation_value_redacts_prior_question(monkeypatch):
    frames=[{'frame_id':f'frame-00{i}','timestamp_ms':i*100} for i in (1,2,3)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id']} for f in frames+[detail]];calls=[]
    def model(*a,**kw):
        calls.append(1)
        event={'action':'navigateBack','target':'上一页面','value':'secret-value' if len(calls)==1 else '',
               'frame_refs':['frame-002'],'observation':'返回','uncertainties':[]}
        return {'events':[event],'uncertainties':['确认secret-value'] if len(calls)==1 else [],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    assert len(calls)==2 and 'secret-value' not in repr(hints)
    assert '[输入内容未保存]' in repr(hints)


def test_probe_cancelled_after_invalid_response_does_not_retry(monkeypatch):
    frames=[{'frame_id':f'frame-00{i}','timestamp_ms':i*100} for i in (1,2,3)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id']} for f in frames+[detail]];state={'cancelled':False,'calls':0}
    def model(*a,**kw):
        state.update(cancelled=True,calls=state['calls']+1)
        return {'events':[{'action':'navigateBack','target':'错误目标','value':'','frame_refs':['frame-002'],
                           'observation':'返回','uncertainties':[]}], 'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_cancelled'):
        targets.probe(frames,[detail],assets,1000,lambda:state['cancelled'])
    assert state['calls']==1


@pytest.mark.parametrize('prior_click',[False,True])
def test_probe_invalid_navigation_retry_cannot_silently_drop_initial_candidates(monkeypatch,prior_click):
    frames=[{'frame_id':f'frame-00{i}','timestamp_ms':i*100} for i in (1,2,3)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id']} for f in frames+[detail]];calls=[]
    click={'action':'aiTap','target':'private target','value':'','frame_refs':['frame-001'],
           'observation':'private observation','uncertainties':[]}
    bad={'action':'navigateBack','target':'private name','value':'','frame_refs':['frame-002'],
         'observation':'private return','uncertainties':[]}
    def model(*a,**kw):
        calls.append(1)
        return {'events':([click] if prior_click else [])+[bad] if len(calls)==1 else [],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    assert len(calls)==2 and len(hints)==1 and hints[0]['kind']=='unresolved_observation'
    assert 'private' not in repr(hints)
    questions=targets.bind_uncertain_hints([],hints)
    assert any('首轮' in q and '遗漏' in q and '核对' in q for q in questions)
