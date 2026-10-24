import copy
import pytest
from task_server.services import ui_video_reconcile as vr


def event(i,action='aiTap'):
    return {'action':action,'target':'取消打印' if i==0 else '返回上一级','value':'',
            'frame_refs':[f'f{i}'], 'start_ms':i*100,'end_ms':i*100,
            'observation':'取消未完成' if i==0 else '页面变化','evidence_kind':'inferred','uncertainties':[]}


def test_review_rejects_silent_removal_but_allows_auto_transition_correction():
    original=[event(1),event(2)]
    corrected=copy.deepcopy(original);corrected[1]['action']='aiAssert';corrected[1]['observation']='页面自动加载，未见用户点击操作'
    vr.check_coverage(original,corrected)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr.check_coverage(original,corrected[:1])


def test_reconcile_uses_supplied_pixels_redacts_and_keeps_input_untouched(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(3)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    original=[event(0),event(1)];before=copy.deepcopy(original)
    def model(skill,payload,**kw):
        assert payload['review_mode']=='full_flow_after_recovery'
        assert [x['name'] for x in kw['image_assets']]==[f['frame_id'] for f in payload['frames']]
        return {'events':[{'action':e['action'],'target':e['target'],'value':'',
            'observation':'页面变化 secret','frame_refs':e['frame_refs']} for e in original],
            'multiple_scenarios':False,'uncertainties':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=vr.reconcile(original,fs,300,tmp_path,lambda:False,['secret'])
    assert 'secret' not in str(result) and original==before
    with pytest.raises(ValueError,match='video_cancelled'):
        vr.reconcile(original,fs,300,tmp_path,lambda:True,[])


def test_reconciliation_is_limited_to_long_cancel_recovery():
    assert vr.needs_reconciliation([event(0)],[{}]*61)
    assert not vr.needs_reconciliation([event(0)],[{}]*60)
    assert not vr.needs_reconciliation([event(1)],[{}]*61)

@pytest.mark.parametrize('action',['aiInput','aiScroll'])
def test_review_cannot_replace_prerequisite_with_observation(action):
    old=event(1,action);new=event(1,'aiAssert')
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr.check_coverage([old],[new])


def test_one_review_event_cannot_cover_multiple_original_actions():
    new=event(1,'aiAssert');new['frame_refs']=['f1','f2']
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr.check_coverage([event(1),event(2)],[new])


def test_review_all_frames_and_shared_secrets(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(90)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    original=[event(0,'aiInput')];seen=set();secrets=[]
    def model(skill,payload,**kw):
        seen.update(f['frame_id'] for f in payload['frames'])
        assert len(kw['image_assets'])<=60
        first=payload['window']['index']==1
        e={'action':'aiInput' if first else 'aiAssert','target':'文本' if first else '页面',
           'value':'NEW_SECRET' if first else '', 'observation':'页面 NEW_SECRET',
           'frame_refs':[payload['frames'][0]['frame_id']]}
        return {'events':[e],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=vr.reconcile(original,fs,9000,tmp_path,lambda:False,secrets)
    assert len(seen)==90 and 'NEW_SECRET' in secrets and 'NEW_SECRET' not in str(result)


def test_long_observation_budget_is_separate_from_final_case_budget(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as vw,ai_skill_service as ai
    from task_server.services.ui_video_analysis import validate_analysis
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(90)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    def model(skill,payload,**kw):
        return {'events':[{'action':'aiAssert','target':'当前任务','value':'','observation':'任务仍在',
                          'frame_refs':[f['frame_id']]} for f in payload['frames']],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    observed,_=vw.observe_windows(fs,9000,tmp_path,lambda:False)
    assert len(observed['events'])==90
    with pytest.raises(ValueError,match='video_model_invalid'):
        validate_analysis(observed,fs,9000)


def test_window_retries_malformed_output_once_without_copying_platform_fields(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as vw,ai_skill_service as ai
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(3)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[]
    def model(skill,payload,**kw):
        calls.append(copy.deepcopy(payload))
        e={'action':'aiAssert','target':'当前页面','value':'','observation':'首页可见','frame_refs':['frame-001']}
        if len(calls)==1:e['start_ms']=100
        return {'events':[e],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result,_=vw.observe_windows(fs,300,tmp_path,lambda:False)
    assert len(calls)==2 and calls[1]['validation_feedback']
    assert result['events'][0]['start_ms']==100


def test_invalid_window_stops_after_two_model_calls(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as vw,ai_skill_service as ai
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(3)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[]
    def model(*args,**kw):
        calls.append(1)
        return {'events':[{'action':'aiTap','target':'页面','value':'','observation':'点击','frame_refs':['forged']}],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_reference_invalid'):
        vw.observe_windows(fs,300,tmp_path,lambda:False)
    assert len(calls)==2


def test_review_candidates_are_local_and_do_not_offer_foreign_frame_refs(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as vw,ai_skill_service as ai
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(90)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    candidates=[{**event(i),'frame_refs':[fs[i]['frame_id']]} for i in (0,30,60,89)]
    def model(skill,payload,**kw):
        ids={f['frame_id'] for f in payload['frames']}
        assert all(set(e['frame_refs'])<=ids and 'start_ms' not in e for e in payload['candidate_events'])
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    vw.observe_windows(fs,9000,tmp_path,lambda:False,review_events=candidates)


def test_review_payload_never_copies_first_pass_semantic_claims(monkeypatch,tmp_path):
    import json
    from task_server.services import ui_video_windows as vw,ai_skill_service as ai
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(90)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    old=event(2);old.update(target='FIRST_PASS_POISON',observation='FIRST_PASS_POISON',
        uncertainties=['FIRST_PASS_POISON'],frame_refs=['frame-002'])
    before=copy.deepcopy(old)
    def model(skill,payload,**kw):
        assert 'FIRST_PASS_POISON' not in json.dumps(payload)
        assert all(set(e)=={'frame_refs'} for e in payload['candidate_events'])
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    vw.observe_windows(fs,9000,tmp_path,lambda:False,review_events=[old])
    assert old==before


def test_planning_prioritizes_each_interaction_result_before_repeated_observations():
    from task_server.services.ui_video_windows import planning_frames
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(120)]
    events=[]
    for i in range(119):
        e=event(i,'aiTap' if i in (0,10,20,90,110) else 'aiAssert')
        e.update(frame_refs=[fs[i]['frame_id']],target='确认打印' if i>20 else '导航')
        events.append(e)
    selected={f['frame_id'] for f in planning_frames(events,fs)}
    assert {'frame-009','frame-019','frame-089','frame-109','frame-119'}<=selected
    assert len(selected)<=60


def test_business_result_cannot_use_later_system_desktop():
    from task_server.services.ui_video_analysis import _validate_consolidated_evidence
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(8,12)]
    business=event(8);business['target']='返回首页'
    desktop=event(11,'aiAssert');desktop.update(target='手机系统桌面',observation='手机系统桌面可见')
    checked=copy.deepcopy(business)
    checked['outcome']={'observation':'系统桌面可见','frame_refs':['f11']}
    with pytest.raises(ValueError,match='video_reference_invalid'):
        _validate_consolidated_evidence({'events':[checked]},[business,desktop],[business,desktop],fs,fs,1200,[0])


def test_planning_keeps_shared_after_before_frame_for_adjacent_actions(monkeypatch):
    from task_server.services.ui_video_windows import planning_frames
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(120)]
    from task_server.services import ui_video_analysis as va
    monkeypatch.setattr(va,'_consolidation_images',lambda events,frames,limit: frames[20:20+limit])
    events=[]
    for i in range(110):
        e=event(i,'aiTap' if i in (10,11,60,61) else 'aiAssert')
        e.update(frame_refs=[fs[i]['frame_id']],target='确认打印')
        if i in (10,60):
            e.update(end_ms=(i+1)*100,frame_refs=[fs[i]['frame_id'],fs[i+1]['frame_id']])
        events.append(e)
    selected={f['frame_id'] for f in planning_frames(events,fs)}
    assert {'frame-011','frame-061'}<=selected


def gap_fixture(tmp_path,count=10):
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(count)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    original=[event(2),event(4,'aiScroll'),event(7)]
    original[1]['value']={'direction':'up','scrollType':'singleAction'}
    checked={'events':[original[0],original[2]],'uncertainties':[],'multiple_scenarios':False}
    return fs,original,copy.deepcopy(checked)


def test_missing_coverage_gets_one_pixel_review_and_audit(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as vw,ai_skill_service as ai
    fs,original,checked=gap_fixture(tmp_path);before=copy.deepcopy(checked);calls=[]
    monkeypatch.setattr(vw,'observe_windows',lambda *a,**kw:(checked,[]))
    def model(skill,payload,**kw):
        calls.append(payload)
        assert payload['candidate_events']==[{'frame_refs':['f4']}]
        assert payload['coverage_checks']=={'first_pass':[{'anchor_id':'first_pass:0','action':'aiScroll','frame_refs':['f4']}],'independent_review':[]}
        assert '静止事实' in payload['validation_feedback']
        assert kw['retry_count']==0
        return {'events':[{k:v for k,v in e.items() if k not in ('start_ms','end_ms','evidence_kind')} for e in original[1:2]],
            'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    actual=vr.reconcile(original,fs,1000,tmp_path,lambda:False,[])
    assert len(calls)==1 and checked==before
    assert [e['action'] for e in actual['events']]==['aiTap','aiScroll','aiTap']
    assert actual['reconciliation_audit']['before_events']==before['events']


@pytest.mark.parametrize('bad',['missing','foreign','cancel','provider'])
def test_local_gap_repair_has_bounded_correction_but_no_provider_or_cancel_retry(monkeypatch,tmp_path,bad):
    from task_server.services import ui_video_windows as vw,ai_skill_service as ai
    fs,original,checked=gap_fixture(tmp_path);calls=[];stop=[False]
    monkeypatch.setattr(vw,'observe_windows',lambda *a,**kw:(checked,[]))
    def model(skill,payload,**kw):
        calls.append(1)
        if bad=='provider':raise RuntimeError('provider unavailable')
        if bad=='cancel':stop[0]=True
        e={k:v for k,v in original[0].items() if k not in ('start_ms','end_ms','evidence_kind')}
        if bad=='foreign':e['frame_refs']=['f9']
        return {'events':[e],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises((ValueError,RuntimeError)):vr.reconcile(original,fs,1000,tmp_path,lambda:stop[0],[])
    assert len(calls)==(2 if bad in {'missing','foreign'} else 1)


def test_disjoint_gaps_exceeding_budget_are_not_silently_truncated(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs,original,checked=gap_fixture(tmp_path,80)
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:pytest.fail('unexpected call'))
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr._repair_coverage(original,checked,fs,8000,tmp_path,lambda:False,[],{f'f{i}' for i in range(0,80,5)})


def test_local_repair_cannot_drop_independently_discovered_input(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs,original,checked=gap_fixture(tmp_path)
    extra=event(5,'aiInput');extra['target']='姓名';extra['value']=''
    checked['events'].insert(1,extra)
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:{'events':[{k:v for k,v in e.items() if k not in ('start_ms','end_ms','evidence_kind')} for e in original[1:2]],'uncertainties':[],'multiple_scenarios':False})
    actual=vr._repair_coverage(original,checked,fs,1000,tmp_path,lambda:False,[],{'f4'})
    assert any(e['action']=='aiInput' and e['target']=='姓名' for e in actual['events'])


def test_local_audit_redacts_newly_discovered_input(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs,original,checked=gap_fixture(tmp_path)
    checked['events'][0]['observation']='页面 NEW_SECRET'
    extra=event(5,'aiInput');extra.update(target='姓名',value='NEW_SECRET')
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:{'events':[{k:v for k,v in e.items() if k not in ('start_ms','end_ms','evidence_kind')} for e in original[1:2]+[extra]],'uncertainties':[],'multiple_scenarios':False})
    actual=vr._repair_coverage(original,checked,fs,1000,tmp_path,lambda:False,[],{'f4','f5'})
    assert 'NEW_SECRET' not in str(actual)


def test_review_context_prefers_matching_previous_pages(tmp_path):
    from PIL import Image
    from task_server.services.ui_video_windows import context_frames
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*1000} for i in range(1,40)]
    root=tmp_path/'frames';root.mkdir()
    for i,f in enumerate(fs):Image.new('RGB',(32,64),('red' if i==4 or i>=36 else 'blue')).save(root/(f['frame_id']+'.jpg'))
    selected=context_frames(fs[-3:],fs,tmp_path,[],lambda:False)
    assert selected==[fs[4]]
    assert all(f['timestamp_ms']<fs[-3]['timestamp_ms'] for f in selected)


def test_matching_context_is_capped_and_cancelable(tmp_path):
    from PIL import Image
    from task_server.services.ui_video_windows import context_frames
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*1000} for i in range(1,81)]
    root=tmp_path/'frames';root.mkdir()
    for i,f in enumerate(fs):Image.new('RGB',(32,64),(i%30*8,0,0)).save(root/(f['frame_id']+'.jpg'))
    selected=context_frames(fs[-30:],fs,tmp_path,[],lambda:False)
    assert len(selected)<=16 and len({f['frame_id'] for f in selected})==len(selected)
    assert all(f['timestamp_ms']<fs[-30]['timestamp_ms']-2000 for f in selected)
    with pytest.raises(ValueError,match='video_cancelled'):context_frames(fs[-30:],fs,tmp_path,[],lambda:True)


def test_touch_gesture_candidates_require_independent_full_review():
    item=event(1);item['target']='屏幕右侧边缘返回手势区域'
    assert vr.needs_reconciliation([item],[{}]*30)


@pytest.mark.parametrize('description',['标签自动加载后内容填充，未见滚动操作','图片资源异步自动加载，非滚动操作'])
def test_explicit_pixel_based_auto_layout_correction_can_remove_false_scroll(description):
    old=event(1,'aiScroll');old['frame_refs']=['f1','f2']
    new=copy.deepcopy(old);new.update(action='aiAssert',value='',observation=description)
    vr.check_coverage([old],[new])
    assert new['uncertainties']
    vr.check_coverage([old],[new])
    assert len(new['uncertainties'])==1
    new['frame_refs']=['f2']
    with pytest.raises(ValueError,match='video_required_action_missing'):vr.check_coverage([old],[new])


def test_loading_failure_negation_does_not_erase_real_scroll():
    old=event(1,'aiScroll');old['frame_refs']=['f1','f2']
    new=copy.deepcopy(old);new.update(action='aiAssert',value='',observation='自动加载未完成，滚动后出现下方卡片')
    with pytest.raises(ValueError,match='video_required_action_missing'):vr.check_coverage([old],[new])


def test_focus_tap_can_be_absorbed_only_by_same_field_input():
    focus={**event(1),'target':'名称输入框','observation':'输入框获得焦点，弹出软键盘','end_ms':110,'frame_refs':['f1','f2']}
    typed={**event(2,'aiInput'),'target':'名称输入框','start_ms':110,'end_ms':300,'frame_refs':['f2','f3']}
    merged={**typed,'start_ms':100,'frame_refs':['f1','f2','f3']}
    vr.check_coverage([focus,typed],[merged])
    for changed in [dict(merged,target='其他输入框'),dict(merged,start_ms=120),dict(merged,action='aiTap')]:
        with pytest.raises(ValueError,match='video_required_action_missing'):vr.check_coverage([focus,typed],[changed])
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr.check_coverage([{**focus,'observation':'点击后打开详情页'},typed],[merged])
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr.check_coverage([focus,event(5),typed],[merged])


def test_navigation_cannot_be_reclassified_as_passive_assertion():
    old=event(1)
    new={**old,'action':'aiAssert','observation':'用户执行返回手势，详情页滑出，返回上一页'}
    with pytest.raises(ValueError,match='video_required_action_missing'):vr.check_coverage([old],[new])


def test_distant_local_gaps_use_one_bounded_image_budget():
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(80)]
    selected=vr._gap_frames(fs,[],{'f1','f70'})
    assert [f['frame_id'] for f in selected]==['f0','f1','f2','f3','f68','f69','f70','f71','f72']
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr._gap_frames(fs,[],{f'f{i}' for i in range(0,80,5)})


def test_gap_expands_whole_existing_observation_or_rejects_budget():
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(80)]
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr._gap_frames(fs,[{'frame_refs':['f1','f40']}],{'f1'})


def test_nested_generic_and_visible_return_anchors_are_one_navigation():
    old={**event(1),'target':'返回上一级','end_ms':300,'frame_refs':['f1','f2','f3']}
    duplicate={**event(2),'observation':old['observation'],'target':'左上角返回箭头','end_ms':300,'frame_refs':['f2','f3']}
    vr.check_coverage([old,duplicate],[old])
    with pytest.raises(ValueError):vr.check_coverage([old,{**duplicate,'frame_refs':['f3','f4']}],[old])
    with pytest.raises(ValueError):vr.check_coverage([old,{**duplicate,'target':'确认打印'}],[old])


def test_explicit_full_evidence_correction_from_false_scroll_to_tap():
    old={**event(1,'aiScroll'),'frame_refs':['f1','f2']}
    new={**old,'action':'aiTap','target':'确认打印','observation':'未见滚动，实际点击确认打印按钮，弹窗消失'}
    vr.check_coverage([old],[new])
    for replacement in [dict(new,frame_refs=['f2']),dict(new,observation='点击确认打印按钮')]:
        with pytest.raises(ValueError):vr.check_coverage([old],[replacement])


def test_same_touch_continued_with_outcome_does_not_require_second_submission():
    first={**event(1),'target':'确认打印按钮','end_ms':200,'frame_refs':['f1','f2'],'observation':'点击确认打印，f2可见触点。'}
    second={**event(2),'target':first['target'],'end_ms':400,'frame_refs':['f2','f3'],'observation':first['observation']+'随后弹窗消失。'}
    vr.check_coverage([first,second],[second])
    with pytest.raises(ValueError):vr.check_coverage([first,{**second,'observation':'再次点击确认打印'}],[second])


def test_continuation_must_not_absorb_explicit_retry_suffix():
    first={**event(1),'target':'确认打印','end_ms':200,'frame_refs':['f1','f2'],'observation':'点击确认打印，f2可见触点。'}
    second={**event(2),'target':first['target'],'end_ms':400,'frame_refs':['f2','f3'],'observation':first['observation']+'再次点击确认打印，随后弹窗消失。'}
    with pytest.raises(ValueError):vr.check_coverage([first,second],[second])


def test_nested_return_with_distinct_navigation_remains_required():
    first={**event(1),'target':'返回上一级','end_ms':300,'frame_refs':['f1','f2','f3'],'observation':'从详情返回列表'}
    second={**event(2),'target':'左上角返回箭头','end_ms':300,'frame_refs':['f2','f3'],'observation':'再次点击返回，从列表返回首页'}
    with pytest.raises(ValueError):vr.check_coverage([first,second],[first])


def test_duplicate_navigation_requires_explicit_review_and_retained_tap():
    old={**event(1),'target':'返回上一级','end_ms':300,'frame_refs':['f1','f2','f3']}
    correction={**old,'action':'aiAssert','observation':'未见新增点击，同一次返回的转场延续，页面自动稳定'}
    tap={**old,'target':'左上角返回箭头'}
    vr.check_coverage([old],[correction,tap])
    with pytest.raises(ValueError):vr.check_coverage([old],[dict(correction,observation='用户从列表返回首页')])


def test_gap_closes_chained_interactions_but_not_long_passive_loading():
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(30)]
    events=[{'action':'aiTap','frame_refs':['f7','f9']},
            {'action':'aiTap','frame_refs':['f9','f11']},
            {'action':'aiAssert','frame_refs':['f11','f29']}]
    selected=vr._gap_frames(fs,events,{'f5'})
    assert {f['frame_id'] for f in selected}=={f'f{i}' for i in range(3,12)}


def test_gap_without_duplicate_candidates_uses_observation_contract(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,4)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    old={**event(1),'frame_refs':['f1','f2','f3'],'end_ms':300}
    def model(skill,payload,**kw):
        assert kw['version']=='coverage-observe'
        assert 'coverage_resolution_contract' not in payload
        assert payload['coverage_checks']['first_pass'][0]['frame_refs']==old['frame_refs']
        return {'events':[{'action':'aiAssert','target':'转场','value':'','frame_refs':old['frame_refs'],
            'observation':'未见新的点击，实际为前序点击后的自动转场'}],
            'coverage_resolutions':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=vr._repair_window([old],{'events':[],'uncertainties':[],'multiple_scenarios':False},fs,1000,tmp_path,lambda:False,[],set(old['frame_refs']))
    assert result['events'][0]['action']=='aiAssert'


def test_observation_only_retry_does_not_reintroduce_merge_instructions(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,4)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    old={**event(1),'frame_refs':['f1','f2','f3'],'end_ms':300};calls=[]
    def model(skill,payload,**kw):
        calls.append(copy.deepcopy(payload))
        if len(calls)==1:return {'events':[],'coverage_resolutions':[],'uncertainties':[],'multiple_scenarios':False}
        assert '在coverage_resolutions中完整列入' not in payload['validation_feedback']
        assert 'coverage_resolutions必须为空' in payload['validation_feedback']
        return {'events':[{'action':'aiAssert','target':'转场','value':'','frame_refs':old['frame_refs'],
            'observation':'未见新的点击，实际为自动转场'}],'coverage_resolutions':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    vr._repair_window([old],{'events':[],'uncertainties':[],'multiple_scenarios':False},fs,1000,tmp_path,lambda:False,[],set(old['frame_refs']))
    assert len(calls)==2


def test_scroll_gap_keeps_existing_mapping_capable_prompt(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,4)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    old={**event(1,'aiScroll'),'value':{'direction':'down','scrollType':'singleAction'},'frame_refs':['f1','f2','f3'],'end_ms':300}
    def model(skill,payload,**kw):
        assert kw['version']=='coverage'
        assert 'coverage_resolution_contract' in payload
        return {'events':[{k:old[k] for k in ('action','target','value','frame_refs','observation','uncertainties')}],
            'coverage_resolutions':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    vr._repair_window([old],{'events':[],'uncertainties':[],'multiple_scenarios':False},fs,1000,tmp_path,lambda:False,[],set(old['frame_refs']))

@pytest.mark.parametrize('repeat_invalid',[False,True])
def test_unnecessary_invalid_merge_requests_observation_retry_without_bypassing_validation(monkeypatch,tmp_path,repeat_invalid):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,5)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    old=[{**event(1),'frame_refs':['f1','f2'],'end_ms':200},
         {**event(2),'frame_refs':['f2','f3'],'end_ms':300}]
    observations=[{'action':'aiTap','target':'确认调整','value':'','frame_refs':['f1','f2'],'observation':'点击确认调整，弹窗关闭'},
                  {'action':'aiAssert','target':'自动校验','value':'','frame_refs':['f2','f3','f4'],'observation':'未见新点击，实际为系统自动加载弹窗'}]
    calls=[]
    def model(skill,payload,**kw):
        calls.append(kw['version'])
        if len(calls)==2:
            assert kw['version']=='coverage-observe'
            assert 'coverage_resolution_contract' not in payload
        return {'events':observations,'coverage_resolutions':[{}] if len(calls)==1 or repeat_invalid else [],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    args=(old,{'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False},fs,1000,tmp_path,lambda:False,[],{'f1','f2','f3'})
    if repeat_invalid:
        with pytest.raises(ValueError,match='video_coverage_resolution_invalid'):vr._repair_window(*args)
    else:
        result=vr._repair_window(*args)
        assert len(result['events'])==2 and result['reconciliation_audit']['coverage_resolutions']==[]
    assert calls==['coverage','coverage-observe']


def test_missing_independent_interaction_does_not_switch_to_observation_only(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,5)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    old=[{**event(1),'frame_refs':['f1','f2'],'end_ms':200},
         {**event(2),'frame_refs':['f2','f3'],'end_ms':300}]
    calls=[]
    def model(skill,payload,**kw):
        calls.append(kw['version'])
        assert kw['version']=='coverage'
        return {'events':[{'action':'aiTap','target':'返回','value':'','frame_refs':['f1','f2','f3'],'observation':'点击返回'}],
                'coverage_resolutions':[{}],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_coverage_resolution_invalid'):
        vr._repair_window(old,{'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False},fs,1000,tmp_path,lambda:False,[],{'f1','f2','f3'})
    assert calls==['coverage','coverage']


def test_repair_window_includes_interaction_crossing_initial_gap_boundary(monkeypatch,tmp_path):
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,6)]
    old=[{**event(i),'frame_refs':[f'f{i}',f'f{i+1}'],'end_ms':(i+1)*100} for i in (1,2,3)]
    checked={'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False}
    calls=[]
    def repair(original,local,window,*args,**kwargs):
        calls.append([e['frame_refs'] for e in original])
        assert original==old
        assert args[4]=={'f1','f2','f3','f4'}
        return {**local,'reconciliation_audit':{'coverage_resolutions':[]}}
    monkeypatch.setattr(vr,'_repair_window',repair)
    result=vr._repair_coverage(old,checked,fs,1000,tmp_path,lambda:False,[],{'f4'})
    assert len(calls)==1 and len(result['events'])==3


def test_cross_window_return_gesture_rechecks_even_when_each_anchor_is_covered(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as vw
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(7)]
    first={**event(1),'target':'左上角返回箭头','start_ms':100,'end_ms':400,
           'frame_refs':['f1','f2','f4'],'observation':'返回手势开始，页面横移'}
    second={**first,'start_ms':300,'end_ms':600,'frame_refs':['f3','f4','f6'],
            'observation':'连续返回手势结束，进入上一级'}
    checked={'events':[first,second],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(vw,'observe_windows',lambda *a,**kw:(copy.deepcopy(checked),[]))
    calls=[]
    def repair(original,result,fs,duration,root,cancelled,secrets,refs):
        calls.append(refs)
        # Platform cannot merge by text; only invoke the existing pixel/evidence gate.
        return result
    monkeypatch.setattr(vr,'_repair_coverage',repair)
    result=vr.reconcile(checked['events'],frames,1000,tmp_path,lambda:False,[])
    assert calls==[{'f1','f2','f3','f4','f6'}]
    assert len(result['events'])==2


@pytest.mark.parametrize('change', ['separate_time','no_gesture','other_control','no_shared_frame'])
def test_unrelated_returns_do_not_trigger_overlap_repair(change):
    first={**event(1),'target':'左上角返回箭头','start_ms':100,'end_ms':400,
           'frame_refs':['f1','f2','f4'],'observation':'返回手势，页面横移'}
    second={**first,'start_ms':300,'end_ms':600,'frame_refs':['f3','f4','f6']}
    if change=='separate_time':second['start_ms']=500
    if change=='no_gesture':second['observation']='点击返回按钮'
    if change=='other_control':second['target']='关闭按钮'
    if change=='no_shared_frame':second['frame_refs']=['f3','f5','f6']
    assert not vr._overlapping_gesture_refs([first,second])


def test_intervening_interaction_separates_gesture_candidates():
    first={**event(1),'target':'左上角返回箭头','start_ms':100,'end_ms':400,
           'frame_refs':['f1','f2','f4'],'observation':'返回手势，页面横移'}
    second={**first,'start_ms':300,'end_ms':600,'frame_refs':['f3','f4','f6']}
    for action in ('aiInput','aiScroll'):
        assert not vr._overlapping_gesture_refs([first,event(2,action),second])


def test_missing_and_overlap_share_one_bounded_repair(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as vw
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(8)]
    first={**event(1),'target':'左上角返回箭头','start_ms':100,'end_ms':400,
           'frame_refs':['f1','f2','f4'],'observation':'返回手势，页面横移'}
    second={**first,'start_ms':300,'end_ms':600,'frame_refs':['f3','f4','f6']}
    old=[first,second,event(7)]
    checked={'events':[first,second],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(vw,'observe_windows',lambda *a,**kw:(copy.deepcopy(checked),[]))
    calls=[]
    def repair(original,result,frames,duration,root,cancelled,secrets,refs):
        calls.append(refs);return {**result,'events':copy.deepcopy(old)}
    monkeypatch.setattr(vr,'_repair_coverage',repair)
    vr.reconcile(old,fs,1000,tmp_path,lambda:False,[])
    assert calls==[{'f1','f2','f3','f4','f6','f7'}]


def test_unresolved_gesture_overlap_keeps_mapping_retry(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,5)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    old=[{**event(1),'target':'左上角返回箭头','frame_refs':['f1','f2','f3'],'end_ms':300,'observation':'返回手势开始'},
         {**event(2),'target':'左上角返回箭头','frame_refs':['f2','f3','f4'],'end_ms':400,'observation':'返回手势结束'}]
    calls=[]
    def model(skill,payload,**kw):
        calls.append(kw['version'])
        # Individually covering anchors cannot settle whether one gesture was
        # counted twice; retain the original mapping contract on retry.
        return {'events':[{k:e[k] for k in ('action','target','value','frame_refs','observation')} for e in old],
                'coverage_resolutions':[{}],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_coverage_resolution_invalid'):
        vr._repair_window(old,{'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False},fs,1000,tmp_path,lambda:False,[],{'f1','f2','f3','f4'})
    assert calls==['coverage','coverage']


def test_return_overlap_with_explicit_touch_displacement_requires_review():
    first={**event(1),'target':'左上角返回箭头','frame_refs':['f1','f2','f3'],'end_ms':300,'observation':'原片为返回手势，页面退回上一级'}
    second={**event(2),'target':'左上角返回箭头','frame_refs':['f2','f3','f4'],'end_ms':400,'observation':'可见触点及dX负值的向左位移轨迹，执行返回操作，页面滑出'}
    assert vr._overlapping_gesture_refs([first,second])=={'f1','f2','f3','f4'}


def test_two_return_displacement_descriptions_require_pixel_review():
    old=[{**event(1),'target':'左上角返回箭头','frame_refs':['f1','f2','f3'],'end_ms':300,'observation':'触点持续横向位移，返回上一级'},
         {**event(2),'target':'左上角返回箭头','frame_refs':['f2','f3','f4'],'end_ms':400,'observation':'触点及向左位移轨迹，页面退回'}]
    assert vr._overlapping_gesture_refs(old)


def test_static_loading_span_does_not_expand_two_local_interaction_repairs(monkeypatch,tmp_path):
    """A long loading observation may share a frame with a later return tap."""
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,101)]
    loading={**event(1,'aiAssert'),'target':'模型加载状态','frame_refs':['f1','f50'],'end_ms':5000}
    taps=[{**event(i),'target':'左上角返回箭头','frame_refs':[f'f{i}',f'f{i+1}'],'end_ms':(i+1)*100} for i in (50,80)]
    old=[loading,*taps]
    checked={'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False}
    calls=[]
    def repair(original,local,window,*args,**kwargs):
        calls.append([f['frame_id'] for f in window])
        assert all(e['action']=='aiTap' for e in original)
        return {**local,'reconciliation_audit':{'coverage_resolutions':[]}}
    monkeypatch.setattr(vr,'_repair_window',repair)
    result=vr._repair_coverage(old,checked,fs,11000,tmp_path,lambda:False,[],{'f50','f51','f80','f81'})
    assert calls==[['f50','f51'],['f80','f81']]
    assert result['events']==old
    assert checked['events']==old


@pytest.mark.parametrize('count,retry_first,expected_calls',[(3,False,3),(3,1,4),(3,2,4),(3,3,4),(4,False,4)])
def test_disjoint_repairs_share_four_call_budget(monkeypatch,tmp_path,count,retry_first,expected_calls):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,26)]
    old=[event(i) for i in (2,7,12,17)[:count]]
    checked={'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False}
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[]
    def model(skill,payload,**kw):
        calls.append(payload['frames'][0]['frame_id'])
        if retry_first and len(set(calls))==retry_first and calls.count(calls[-1])==1:return {'events':[], 'uncertainties':[], 'multiple_scenarios':False}
        e=next(e for e in old if e['frame_refs']==[payload['frames'][0]['frame_id']])
        return {'events':[{k:e[k] for k in ('action','target','value','frame_refs','observation')}],
                'coverage_resolutions':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    out=vr._repair_coverage(old,checked,fs,3000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in old})
    assert len(calls)==expected_calls
    assert len(out['events'])==count


def test_four_gaps_do_not_spend_reserved_calls_on_first_gap_retry(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,26)]
    old=[event(i) for i in (2,7,12,17)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[]
    def model(*args,**kw):
        calls.append(1);return {'events':[], 'uncertainties':[], 'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError):
        vr._repair_coverage(old,{'events':old,'uncertainties':[],'multiple_scenarios':False},fs,3000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in old})
    assert len(calls)==1


def test_return_overlap_with_motion_and_return_in_separate_clauses_requires_review():
    first={**event(1),'target':'左上角返回箭头','frame_refs':['f1','f2','f3'],'end_ms':300,
           'observation':'顶部数据显示dX:-246.0，表明发生从右向左的大幅度横向位移手势。页面向右移出，确认执行了返回操作。'}
    second={**event(2),'target':'左上角返回箭头','frame_refs':['f2','f3','f4'],'end_ms':400,
            'observation':'同一次返回手势，页面退回上一级'}
    assert vr._overlapping_gesture_refs([first,second])=={'f1','f2','f3','f4'}


@pytest.mark.parametrize('note', ['未见横向位移手势。确认执行返回操作。', '横向位移手势。没有执行返回操作。', '横向位移手势。页面内容左右滚动。'])
def test_motion_without_affirmative_return_does_not_authorize_return_mapping(note):
    from task_server.services.ui_video_coverage_resolution import has_return_gesture_evidence
    assert not has_return_gesture_evidence(note)


def test_scroll_repair_includes_cross_pass_shifted_evidence_despite_target_wording(monkeypatch,tmp_path):
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,7)]
    first={**event(1,'aiScroll'),'target':'页面内容区域','value':{'direction':'down','scrollType':'singleAction'},'frame_refs':['f1','f3'],'end_ms':300}
    reviewed={**first,'target':'页面列表','start_ms':200,'end_ms':400,'frame_refs':['f2','f4']}
    class Captured(Exception):pass
    def repair(original,local,window,*args,**kwargs):
        assert [f['frame_id'] for f in window]==['f1','f2','f3','f4']
        assert local['events']==[reviewed]
        assert args[5]==set()
        raise Captured()
    monkeypatch.setattr(vr,'_repair_window',repair)
    with pytest.raises(Captured):
        vr._repair_coverage([first],{'events':[reviewed]},fs,1000,tmp_path,lambda:False,[],{'f1','f3'})


@pytest.mark.parametrize('change',['direction','parameters','touching_time'])
def test_scroll_cross_pass_expansion_rejects_nonmatching_spans(monkeypatch,tmp_path,change):
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,7)]
    first={**event(1,'aiScroll'),'target':'内容区域','value':{'direction':'down','scrollType':'singleAction'},'frame_refs':['f1','f3'],'end_ms':300}
    reviewed={**first,'target':'列表','start_ms':200,'end_ms':400,'frame_refs':['f2','f4']}
    if change=='direction':reviewed['value']={'direction':'up','scrollType':'singleAction'}
    if change=='parameters':reviewed['value']={**reviewed['value'],'distance':200}
    if change=='touching_time':reviewed.update(start_ms=300,frame_refs=['f3','f4'])
    class Captured(Exception):pass
    def repair(original,local,window,*args,**kwargs):
        assert [f['frame_id'] for f in window]==['f1','f2','f3']
        assert local['events']==[]
        raise Captured()
    monkeypatch.setattr(vr,'_repair_window',repair)
    with pytest.raises(Captured):
        vr._repair_coverage([first],{'events':[reviewed]},fs,1000,tmp_path,lambda:False,[],{'f1'})
