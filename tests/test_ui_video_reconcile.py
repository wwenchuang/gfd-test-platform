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


def test_input_focus_can_use_previous_frame_as_context_without_erasing_font_click():
    font={**event(1),'target':'篆体选项','frame_refs':['f1','f2','f3'],'start_ms':100,'end_ms':300}
    focus={**event(3),'target':'文字输入框','frame_refs':['f3','f4','f5'],'start_ms':300,'end_ms':500,
           'observation':'点击文字输入框，随后键盘弹出'}
    typed={**event(5,'aiInput'),'target':'文字输入框','frame_refs':['f5','f6'],'start_ms':500,'end_ms':600,'value':'姓名'}
    combined={**typed,'frame_refs':['f4','f5','f6'],'start_ms':400,
              'observation':'点击文字输入框，键盘弹出后输入文字'}
    vr.check_coverage([font,focus,typed],[copy.deepcopy(font),combined])
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr.check_coverage([font,focus,typed],[combined])
    for bad in ({**combined,'observation':'键盘显示，输入文字'},
                {**combined,'frame_refs':['f5','f6'],'start_ms':500},
                {**combined,'target':'其他输入框'}):
        with pytest.raises(ValueError,match='video_required_action_missing'):
            vr.check_coverage([font,focus,typed],[font,bad])


def test_focus_context_does_not_propose_font_and_focus_as_one_click():
    font={**event(1),'target':'字体','frame_refs':['f1','f2','f3'],'start_ms':100,'end_ms':300}
    focus={**event(3),'target':'文字输入框','frame_refs':['f3','f4','f5'],'start_ms':300,'end_ms':500,
           'observation':'点击输入框，随后键盘弹出'}
    typed={**event(5,'aiInput'),'target':'文字输入框','frame_refs':['f5','f6'],'start_ms':500,'end_ms':600}
    sources={'first_pass':[font,focus,typed],'independent_review':[copy.deepcopy(font)]}
    from task_server.services.ui_video_coverage_resolution import eligible_groups
    groups=eligible_groups(sources,{'f1','f2','f3','f4','f5','f6'})
    assert groups
    assert vr._exclude_focus_context_groups(groups,sources)==[]
    changed=copy.deepcopy(sources)
    changed['first_pass'][2]['target']='其他字段'
    assert vr._exclude_focus_context_groups(groups,changed)==groups


def test_unnecessary_one_to_one_scroll_correction_can_be_checked_independently():
    old={**event(1,'aiScroll'),'target':'弹窗','frame_refs':['f1','f2'],'start_ms':100,'end_ms':200,
         'value':{'direction':'down','scrollType':'singleAction'}}
    observed={**old,'action':'aiAssert','value':'','observation':'图片自动加载，未见独立用户滚动'}
    sources={'first_pass':[old],'independent_review':[copy.deepcopy(old)]}
    claim={'source_anchor_ids':['first_pass:0','independent_review:0'],
           'resolution':'same_interaction_with_action_correction','reviewed_event_index':0,'evidence':{}}
    kept,removed=vr._separate_redundant_assert_claims([claim],sources,[observed])
    assert kept==[] and len(removed)==1 and 'evidence' not in removed[0]
    observed['observation']='全程未见用户触点或滚动操作，图片自动加载。'
    kept,removed=vr._separate_redundant_assert_claims([claim],sources,[observed])
    assert kept==[] and len(removed)==1 and 'evidence' not in removed[0]
    for bad in ({**observed,'observation':'用户手动滚动'}, {**observed,'frame_refs':['f1']}):
        assert vr._separate_redundant_assert_claims([claim],sources,[bad])==([claim],[])


@pytest.mark.parametrize('change',['none','action_correction','target','empty_target','frames','action','unknown_source','out_of_range','uncertain_old','uncertain_new','duplicate_id','conflict','duplicate_source','duplicate_result','neighbor_tap'])
def test_redundant_tap_claim_requires_unambiguous_exact_correspondence(change):
    tap={**event(1),'target':'确认打印','frame_refs':['f1','f2','f3'],'start_ms':100,'end_ms':300}
    sources={'first_pass':[copy.deepcopy(tap)],'independent_review':[copy.deepcopy(tap)]}
    reviewed=[copy.deepcopy(tap)]
    claim={'source_anchor_ids':['first_pass:0','independent_review:0'],
           'resolution':'same_interaction','reviewed_event_index':0,'evidence':{}}
    claims=[claim]
    if change=='action_correction':claim['resolution']='same_interaction_with_action_correction'
    if change=='target':reviewed[0]['target']='取消打印'
    if change=='empty_target':reviewed[0]['target']='  '
    if change=='frames':reviewed[0]['frame_refs']=['f1','f2']
    if change=='action':sources['first_pass'][0]['action']='navigateBack'
    if change=='unknown_source':claim['source_anchor_ids'][1]='unknown:0'
    if change=='out_of_range':claim['source_anchor_ids'][1]='independent_review:999'
    if change=='uncertain_old':sources['first_pass'][0]['uncertainties']=['触点不确定']
    if change=='uncertain_new':reviewed[0]['uncertainties']=['触点不确定']
    if change=='duplicate_id':claim['source_anchor_ids'][1]='first_pass:0'
    if change=='conflict':claim['resolution']='tap_return_conflict'
    if change=='duplicate_source':claims.append({**copy.deepcopy(claim),'reviewed_event_index':1})
    if change=='duplicate_result':claims.append({**copy.deepcopy(claim),'source_anchor_ids':['first_pass:1','independent_review:1']})
    if change=='neighbor_tap':sources['first_pass'].append(copy.deepcopy(tap))
    kept,removed=vr._separate_redundant_tap_claims(claims,sources,reviewed)
    if change in {'none','action_correction'}:
        assert kept==[] and len(removed)==1
        assert removed[0]['status']=='unverified_model_claim'
        assert removed[0]['reviewed_event_index_scope']=='local_repair'
        assert removed[0]['reviewed_event_frame_refs']==tap['frame_refs']
    else:assert claim in kept and claim not in removed


@pytest.mark.parametrize('mode',['assert','tap'])
@pytest.mark.parametrize('case',['sensitive','own_alias','other_alias','bad_other'])
def test_redundant_claim_audit_is_minimal_and_aliases_do_not_bypass_conflicts(mode,case):
    old={**event(1,'aiScroll' if mode=='assert' else 'aiTap'),'target':'目标',
         'value':{'direction':'down','scrollType':'singleAction'} if mode=='assert' else '',
         'frame_refs':['f1','f2','f3'],'start_ms':100,'end_ms':300}
    new={**copy.deepcopy(old),'action':'aiAssert' if mode=='assert' else 'aiTap','value':'',
         'observation':'图片自动加载，未见独立用户滚动' if mode=='assert' else '点击目标'}
    sources={'first_pass':[copy.deepcopy(old)],'independent_review':[copy.deepcopy(old)]}
    claim={'source_anchor_ids':['first_pass:0','independent_review:0'],
           'resolution':'same_interaction_with_action_correction' if mode=='assert' else 'same_interaction',
           'reviewed_event_index':0,'evidence':{'interaction':'unlisted-sensitive-private-value'}}
    claims=[claim]
    if case=='own_alias':claim['source_anchor_ids'][0]='first_pass:00'
    if case=='other_alias':claims.append({**copy.deepcopy(claim),'source_anchor_ids':['first_pass:00','independent_review:1'],'reviewed_event_index':1})
    if case=='bad_other':claims.append({**copy.deepcopy(claim),'source_anchor_ids':['unparsable'],'reviewed_event_index':1})
    fn=vr._separate_redundant_assert_claims if mode=='assert' else vr._separate_redundant_tap_claims
    kept,removed=fn(claims,sources,[new])
    if case=='sensitive':
        assert kept==[] and len(removed)==1
        assert set(removed[0])=={'source_anchor_ids','resolution','reviewed_event_index','status','reviewed_event_index_scope','reviewed_event_frame_refs'}
        assert 'unlisted-sensitive-private-value' not in repr(removed)
    else:assert claim in kept and claim not in removed


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


@pytest.mark.parametrize('action,invalid_target,expected_target',[
    ('navigateBack','返回上一页','上一页面'),
    ('dismissKeyboard','键盘','当前页面的软键盘'),
])
def test_repair_retry_explains_exact_native_action_contract(monkeypatch,tmp_path,action,invalid_target,expected_target):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,4)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    old={**event(1),'action':action,'target':expected_target,'value':'','frame_refs':['f1','f2','f3'],'end_ms':300}
    calls=[]
    def model(skill,payload,**kw):
        calls.append(copy.deepcopy(payload))
        target=invalid_target if len(calls)==1 else expected_target
        if len(calls)==2:
            assert action in payload['validation_feedback']
            assert expected_target in payload['validation_feedback']
            assert 'value' in payload['validation_feedback']
        return {'events':[{'action':action,'target':target,'value':'','frame_refs':old['frame_refs'],
                'observation':'原帧显示用户操作和结果','uncertainties':[]}],
                'coverage_resolutions':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=vr._repair_window([old],{'events':[],'uncertainties':[],'multiple_scenarios':False},fs,1000,tmp_path,lambda:False,[],set(old['frame_refs']))
    assert len(calls)==2 and result['events'][0]['target']==expected_target


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


def test_single_scroll_gap_needs_observation_not_merge_declaration(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,4)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    old={**event(1,'aiScroll'),'value':{'direction':'down','scrollType':'singleAction'},'frame_refs':['f1','f2','f3'],'end_ms':300}
    def model(skill,payload,**kw):
        assert kw['version']=='coverage-observe'
        assert 'coverage_resolution_contract' not in payload
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


def test_native_return_retry_does_not_require_visible_arrow(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,5)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    old=[{**event(1,'navigateBack'),'target':'上一页面','frame_refs':['f1','f2','f3'],'end_ms':300,'observation':'返回手势开始'},
         {**event(2,'navigateBack'),'target':'上一页面','frame_refs':['f2','f3','f4'],'end_ms':400,'observation':'返回手势结束'}]
    calls=[]
    def model(skill,payload,**kw):
        calls.append(copy.deepcopy(payload))
        if len(calls)==2:
            assert '原生navigateBack无需可见返回箭头' in payload['validation_feedback']
            assert '无可见控件则不能映射' not in payload['validation_feedback']
        return {'events':[{k:e[k] for k in ('action','target','value','frame_refs','observation')} for e in old],
                'coverage_resolutions':[{}],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_coverage_resolution_invalid'):
        vr._repair_window(old,{'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False},fs,1000,tmp_path,lambda:False,[],{'f1','f2','f3','f4'})
    assert len(calls)==2


def test_return_overlap_with_explicit_touch_displacement_requires_review():
    first={**event(1),'target':'左上角返回箭头','frame_refs':['f1','f2','f3'],'end_ms':300,'observation':'原片为返回手势，页面退回上一级'}
    second={**event(2),'target':'左上角返回箭头','frame_refs':['f2','f3','f4'],'end_ms':400,'observation':'可见触点及dX负值的向左位移轨迹，执行返回操作，页面滑出'}
    assert vr._overlapping_gesture_refs([first,second])=={'f1','f2','f3','f4'}


def test_overlapping_native_returns_require_visual_review_and_cannot_disappear():
    first={**event(1,'navigateBack'),'target':'上一页面','frame_refs':['f1','f2','f3'],'end_ms':300,'observation':'原片为返回手势，页面退回上一级'}
    second={**event(2,'navigateBack'),'target':'上一页面','frame_refs':['f2','f3','f4'],'end_ms':400,'observation':'连续返回手势，页面横移'}
    assert vr._overlapping_gesture_refs([first,second])=={'f1','f2','f3','f4'}
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr.check_coverage([first,second],[first])
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr.check_coverage([first],[{**first,'action':'aiAssert'}])


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


@pytest.mark.parametrize('count,retry_first,expected_calls',[(3,False,3),(3,1,4),(3,2,4),(3,3,4),(4,False,4),(5,False,5)])
def test_disjoint_repairs_share_bounded_call_budget(monkeypatch,tmp_path,count,retry_first,expected_calls):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,26)]
    old=[event(i) for i in (2,7,12,17,22)[:count]]
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


@pytest.mark.parametrize('mutation', ['overlap','disjoint','different_target','intervening_input','one_shared_frame','one_shared_overlap','one_shared_overlap_same_end'])
def test_non_return_overlapping_taps_require_pixel_review_without_merging(mutation):
    first={**event(0),'target':'文字输入框','frame_refs':['f0','f1','f2'],'start_ms':0,'end_ms':200}
    second={**event(1),'target':'文字输入框','frame_refs':['f1','f2','f3'],'start_ms':100,'end_ms':300}
    events=[first,second]
    if mutation=='disjoint':second.update(frame_refs=['f3','f4'],start_ms=300,end_ms=400)
    if mutation=='different_target':second['target']='提交按钮'
    if mutation=='intervening_input':events.insert(1,{**event(1,'aiInput'),'target':'文字输入框'})
    if mutation=='one_shared_frame':second.update(frame_refs=['f2','f3'],start_ms=200)
    if mutation=='one_shared_overlap':second.update(frame_refs=['f1','f3'],start_ms=100)
    if mutation=='one_shared_overlap_same_end':
        first.update(frame_refs=['f0','f3'],end_ms=300)
        second.update(frame_refs=['f1','f2','f3'],start_ms=100,end_ms=300)
    before=copy.deepcopy(events)
    assert vr._overlapping_tap_refs(events)==({'f0','f1','f2','f3'} if mutation in {'overlap','one_shared_overlap','one_shared_overlap_same_end'} else set())
    assert events==before


@pytest.mark.parametrize('repair_bad_claim',[False,True,'invalid_merge'])
def test_sparse_overlapping_taps_can_review_shared_hold_then_following_state(monkeypatch,tmp_path,repair_bad_claim):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(9)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    first={**event(1),'target':'确认按钮','frame_refs':['f1','f4'],'end_ms':400}
    second={**first,'start_ms':300,'frame_refs':['f3','f4']}
    old=[first,second];checked={'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False}
    calls=[]
    def model(skill,payload,**kw):
        calls.append(payload)
        assert 'f5' in {f['frame_id'] for f in payload['frames']}
        if len(calls)>1:
            assert 'invalid_resolution_candidate' not in payload
            diagnostic=payload['resolution_diagnostics']['result_evidence_to_recheck'][0]
            if repair_bad_claim is True:
                assert diagnostic['shared_interaction_frame_candidates']==['f4']
            else:
                assert diagnostic['reason']=='invalid_merge_evidence'
                assert '优先处理resolution_diagnostics中的拒绝原因' not in payload['validation_feedback']
        response={'events':[{'action':'aiTap','target':'确认按钮','value':'','frame_refs':['f1','f4','f5'],
            'observation':'同一持续按压，弹窗关闭后触点仍保持，随后显示模型页面','uncertainties':[]}],
            'coverage_resolutions':[{'source_anchor_ids':['first_pass:0','first_pass:1','independent_review:0','independent_review:1'],
                'resolution':'same_interaction','reviewed_event_index':0,'evidence':{
                    'before_frame':'f1','interaction_frame':'f4','after_frame':'f5',
                    'examined_frame_refs':['f1','f3','f4','f5'],'before_state':'确认弹窗可见',
                    'interaction':'同一次持续按压，弹窗消失时触点仍保持',
                    'after_state':'模型页面稳定显示','why_not_separate_actions':'原帧触点连续保持，未见第二次按下'}}],
            'uncertainties':[],'multiple_scenarios':False}
        if repair_bad_claim and len(calls)==1:
            response['events'][0]['frame_refs']=['f1','f3','f5']
            response['coverage_resolutions'][0]['evidence']['interaction_frame']='f3'
            if repair_bad_claim=='invalid_merge':response['coverage_resolutions'][0]['resolution']='unknown'
        return response
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=vr._repair_coverage(old,checked,fs,1000,tmp_path,lambda:False,[],{'f1','f3','f4'})
    assert len(calls)==(2 if repair_bad_claim else 1) and len(result['events'])==1
    assert result['events'][0]['frame_refs']==['f1','f4','f5']
    assert len(result['reconciliation_audit']['coverage_resolutions'])==1
    assert old==[first,second] and checked['events']==old


@pytest.mark.parametrize('boundary',['next_interaction','video_end','frame_budget','endpoint_only'])
def test_terminal_overlap_context_does_not_cross_actions_or_budgets(monkeypatch,tmp_path,boundary):
    count=45 if boundary=='frame_budget' else 9
    tail=40 if boundary=='frame_budget' else 4
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(count)]
    first={**event(1),'target':'确认按钮','frame_refs':['f1',f'f{tail}'],'end_ms':tail*100}
    second={**first,'start_ms':(tail-1)*100,'frame_refs':[f'f{tail-1}',f'f{tail}']}
    if boundary=='endpoint_only':second.update(start_ms=tail*100,frame_refs=[f'f{tail}'])
    old=[first,second]
    if boundary=='next_interaction':old.append({**event(tail+1),'target':'独立业务按钮'})
    if boundary=='video_end':fs=fs[:tail+1]
    captured=[]
    def inspect(*args,**kw):
        captured.extend(f['frame_id'] for f in args[2]);raise LookupError('captured')
    monkeypatch.setattr(vr,'_repair_window',inspect)
    with pytest.raises(LookupError,match='captured'):
        vr._repair_coverage(old,{'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False},
            fs,count*100,tmp_path,lambda:False,[],{'f1',f'f{tail-1}',f'f{tail}'})
    assert f'f{tail+1}' not in captured
    assert len(captured)<=40


def test_five_disjoint_repairs_share_one_extra_return_only_call(monkeypatch,tmp_path):
    # Real f834 group sizes (3, 4, 2, 3, 6): all evidence must survive.
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(80)]
    groups=[(4,6),(20,23),(30,31),(50,52),(70,75)]
    old=[{**event(start),'frame_refs':[f'f{start}',f'f{end}'],'end_ms':end*100} for start,end in groups]
    checked={'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False}
    calls=[]
    def repair(original,local,window,*args,**kw):
        calls.append((len(window),kw['attempt_limit'],kw.get('retry_return_gesture_only')))
        return {**local,'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':2 if len(calls)==2 else 1}}
    monkeypatch.setattr(vr,'_repair_window',repair)
    result=vr._repair_coverage(old,checked,fs,8000,tmp_path,lambda:False,[],{r for e in old for r in e['frame_refs']})
    assert calls==[(3,2,True),(4,2,True),(2,1,True),(3,1,True),(6,1,True)]
    assert result['events']==old


def test_five_gaps_fail_without_retry_if_first_model_response_invalid(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai,ui_video_windows as windows
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(30)]
    old=[event(i) for i in (2,7,12,17,22)]
    checked={'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False}
    calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    def model(*args,**kw):
        calls.append(1)
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError):
        vr._repair_coverage(old,checked,fs,3000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in old})
    assert len(calls)==1


@pytest.mark.parametrize('groups',[
    [(0,40)],
    [(0,32),(35,66),(69,100),(103,134),(137,168)],
])
def test_expanded_group_and_total_frame_limits_fail_before_model_call(monkeypatch,tmp_path,groups):
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(180)]
    old=[{**event(start),'frame_refs':[f'f{start}',f'f{end}'],'end_ms':end*100} for start,end in groups]
    checked={'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(vr,'_repair_window',lambda *a,**kw:pytest.fail('over-budget model call'))
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr._repair_coverage(old,checked,fs,18000,tmp_path,lambda:False,[],{r for e in old for r in e['frame_refs']})
