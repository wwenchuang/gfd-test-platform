import copy
import json
import pytest


def frames():
    return [{'frame_id':f'frame-{i:03d}' if i<8 else 'frame-end','timestamp_ms':i*100} for i in range(1,9)]


def observation():
    return {'events':[{'action':'aiTap','target':'我的','value':'','frame_refs':['frame-001','frame-002'],'observation':'我的页面显示'}], 'uncertainties':[],'multiple_scenarios':False}


def test_whole_evidence_binds_only_actual_ordered_frames():
    from task_server.services.ui_video_analysis import _bind_whole_evidence,validate_analysis
    raw=observation();before=copy.deepcopy(raw)
    bound=_bind_whole_evidence(raw,frames())
    event=validate_analysis(bound,frames(),1000)['events'][0]
    assert (event['start_ms'],event['end_ms'],event['evidence_kind'])==(100,200,'inferred')
    assert raw==before


@pytest.mark.parametrize('refs',[[],['forged'],['frame-002','frame-001'],['frame-001','frame-001']])
def test_whole_rejects_bad_refs(refs):
    from task_server.services.ui_video_analysis import _bind_whole_evidence
    raw=observation();raw['events'][0]['frame_refs']=refs
    with pytest.raises(ValueError):_bind_whole_evidence(raw,frames())


@pytest.mark.parametrize('key,value',[('start_ms',0),('end_ms',900),('evidence_kind','observed'),('uncertainties',None)])
def test_whole_rejects_source_overrides_and_bad_questions(key,value):
    from task_server.services.ui_video_analysis import _bind_whole_evidence
    raw=observation();raw['events'][0][key]=value
    with pytest.raises(ValueError):_bind_whole_evidence(raw,frames())


def test_transport_whole_frames_complete_and_labelled():
    from task_server.services.ai_skill_service import _video_context_assets,build_dashscope_chat_body
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':'YQ=='} for f in frames()]
    assert _video_context_assets('ui_video_observe',{'phase':'whole-observe','image_frames':frames()},assets)
    body=build_dashscope_chat_body('prompt',image_assets=assets,image_limit=60,chronological_images=True)
    content=body['messages'][1]['content']
    assert len([p for p in content if p['type']=='image_url'])==8
    assert content[1]['text']=='frame-001 100ms'
    assert content[-2]['text']=='frame-end 800ms'
    assert not _video_context_assets('other',{'phase':'whole-observe'},assets)


@pytest.mark.parametrize('change',['too_many','oversized','mismatch'])
def test_transport_rejects_incomplete_or_excessive_context(change):
    from task_server.services.ai_skill_service import _video_context_assets
    fs=frames();assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':'YQ=='} for f in fs]
    if change=='too_many':assets*=8
    if change=='oversized':assets[0]['base64']='a'*(10*1024*1024+1)
    if change=='mismatch':assets.pop()
    with pytest.raises(ValueError):_video_context_assets('ui_video_observe',{'phase':'whole-observe','image_frames':fs},assets)


def test_whole_pipeline_preserves_context_and_one_repair(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    fs=frames();(tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[]
    def model(skill,payload,**kwargs):
        calls.append((skill,payload))
        assert len(kwargs['image_assets'])==8
        if skill=='ui_video_observe':return observation()
        item={'candidate_index':0,'action':'aiTap','target':'我的','value':'','observation':'我的页面显示','uncertainties':[]}
        if not payload.get('validation_feedback'):item['candidate_index']=99
        return {'events':[item],'uncertainties':[],'multiple_scenarios':False,'title':'查看我的页面'}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    doc={'app_id':'com.example.app','goal':'','media':{'frames':fs,'duration_ms':1000},'baseline_examples':[{'title':'sample'}]}
    result=va.analyze_whole_frames(doc,tmp_path,lambda:False)
    assert [c[0] for c in calls]==['ui_video_observe','ui_video_steps','ui_video_steps']
    assert 'baseline_examples' not in calls[0][1]
    assert calls[1][1]['baseline_examples']==doc['baseline_examples']
    assert result['events'][0]['frame_refs']==['frame-001','frame-002']
    with pytest.raises(ValueError,match='video_cancelled'):va.analyze_whole_frames(doc,tmp_path,lambda:True)


@pytest.mark.parametrize('repair_succeeds',[True,False])
def test_whole_pipeline_repairs_missing_scroll_expectation_once(monkeypatch,tmp_path,repair_succeeds):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    fs=frames();(tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[]
    def model(skill,payload,**kwargs):
        calls.append((skill,payload))
        event={'action':'aiScroll','target':'个人中心页面','value':{'direction':'down','scrollType':'singleAction'},
               'observation':'页面内容上移','uncertainties':[]}
        if skill=='ui_video_observe':
            return {'events':[{**event,'frame_refs':['frame-001','frame-002']}],'uncertainties':[],'multiple_scenarios':False}
        event['candidate_index']=0
        if payload.get('validation_feedback') and repair_succeeds:
            event['outcome']={'observation':'个人中心页面显示打印记录入口','frame_refs':['frame-002']}
            event['reusable_expected']='打印记录入口可见'
        return {'events':[event],'uncertainties':[],'multiple_scenarios':False,'title':'查看打印记录入口'}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    doc={'app_id':'com.example.app','goal':'','media':{'frames':fs,'duration_ms':1000}}
    if repair_succeeds:
        result=va.analyze_whole_frames(doc,tmp_path,lambda:False)
        assert result['events'][0]['reusable_expected']=='打印记录入口可见'
    else:
        with pytest.raises(ValueError,match='video_expected_missing'):
            va.analyze_whole_frames(doc,tmp_path,lambda:False)
    assert [c[0] for c in calls]==['ui_video_observe','ui_video_steps','ui_video_steps']


def test_whole_schema_failure_never_calls_hidden_direct_fallback(monkeypatch):
    from task_server.services import ai_skill_service as ai
    fs=frames();assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':'YQ=='} for f in fs]
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY','1')
    monkeypatch.setattr(ai,'AI_SKILLS_STRICT_MODEL',False)
    monkeypatch.setattr(ai,'ai_gateway_skill_content',lambda *a,**k:'{"events":[]}')
    def forbidden(*a,**k):pytest.fail('unexpected hidden model call')
    monkeypatch.setattr(ai,'dashscope_chat_content',forbidden)
    with pytest.raises(RuntimeError):ai.run_ai_skill('ui_video_observe',{'phase':'whole-observe','image_frames':fs},image_assets=assets)


def test_direct_video_disabled_gateway_keeps_all_labels(monkeypatch):
    from task_server.services import ai_skill_service as ai
    fs=frames();assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':'YQ=='} for f in fs]
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY','0');calls=[]
    def direct(*args,**kwargs):calls.append(kwargs);return json.dumps(observation())
    monkeypatch.setattr(ai,'dashscope_chat_content',direct)
    ai.run_ai_skill('ui_video_observe',{'phase':'whole-observe','image_frames':fs},image_assets=assets)
    assert calls[0]['chronological_images'] is True and calls[0]['image_limit']==60
    assert len(calls[0]['image_assets'])==8


def test_whole_correction_retains_multiscenario_and_input_redaction(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    fs=frames();(tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[]
    def model(skill,payload,**kwargs):
        calls.append(skill)
        if skill=='ui_video_observe':
            raw=observation();raw['events'][0].update(action='aiInput',value='private-value',observation='输入private-value后页面显示')
            return raw
        assert 'private-value' not in str(payload)
        bad=not payload.get('validation_feedback')
        return {'title':'检查private-value输入','events':[{'candidate_index':99 if bad else 0,'action':'aiInput','target':'输入框','value':'', 'observation':'页面显示','uncertainties':[]}], 'uncertainties':[],'multiple_scenarios':bad}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=va.analyze_whole_frames({'app_id':'com.example.app','goal':'','media':{'frames':fs,'duration_ms':1000}},tmp_path,lambda:False)
    assert len(calls)==3 and result['multiple_scenarios'] is True
    assert 'private-value' not in str(result)


def test_whole_cancel_after_observation_stops_before_planning(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    fs=frames();(tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    stopped=[False];calls=[]
    def model(skill,*a,**k):calls.append(skill);stopped[0]=True;return observation()
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_cancelled'):
        va.analyze_whole_frames({'app_id':'com.example.app','goal':'','media':{'frames':fs,'duration_ms':1000}},tmp_path,lambda:stopped[0])
    assert calls==['ui_video_observe']


def test_long_video_observes_all_frames_in_bounded_overlapping_windows(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*1000} for i in range(1,121)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[];seen=set()
    def model(skill,payload,**kw):
        calls.append((skill,payload))
        assert 2<=len(kw['image_assets'])<=60
        assert [a['name'] for a in kw['image_assets']]==[f['frame_id'] for f in payload['image_frames']]
        if skill=='ui_video_observe':
            window=payload['image_frames'];seen.update(f['frame_id'] for f in window)
            # A repeated observation in overlap must not become a second click.
            refs=['frame-027','frame-028'] if window[0]['timestamp_ms']<=27000<=window[-1]['timestamp_ms'] else [window[-2]['frame_id'],window[-1]['frame_id']]
            return {'events':[{'action':'aiTap','target':'入口','value':'','frame_refs':refs,'observation':'入口页面显示'}],'uncertainties':[],'multiple_scenarios':False}
        return {'events':[{'candidate_index':i,'action':e['action'],'target':e['target'],'value':'','observation':e['observation'],'uncertainties':[]} for i,e in enumerate(payload['candidate_events'])], 'uncertainties':[],'multiple_scenarios':False,'title':'长视频完整导航验证'}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=va.analyze_whole_frames({'app_id':'com.example.app','goal':'','media':{'frames':fs,'duration_ms':121000}},tmp_path,lambda:False)
    assert seen=={f['frame_id'] for f in fs}
    assert len([c for c in calls if c[0]=='ui_video_observe'])==5
    assert len(result['events'])==4
    assert result['events'][-1]['start_ms']>=110000


def test_long_video_cancel_between_windows_does_not_plan(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*1000} for i in range(1,81)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[];stop=[False]
    def model(skill,payload,**kw):
        calls.append(skill);stop[0]=True
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_cancelled'):
        va.analyze_whole_frames({'app_id':'com.example.app','goal':'','media':{'frames':fs,'duration_ms':81000}},tmp_path,lambda:stop[0])
    assert calls==['ui_video_observe']


def test_retry_reduction_requires_matching_retained_action_and_failure_evidence():
    from task_server.services.ui_video_recovery import review_retries
    raw=[]
    for i,target in enumerate(['取消打印','确认取消','取消打印','确认取消']):
        raw.append({'action':'aiTap','target':target,'start_ms':i*1000,'end_ms':i*1000+200,
                    'frame_refs':[f'frame-{i:03d}'],'observation':'取消流程未完成，请稍后重试' if i==1 else '按钮点击后页面显示'})
    plan={'events':[{'candidate_index':2},{'candidate_index':3}],
          'omitted_retries':[{'candidate_index':0,'repeated_by':2,'reason':'同一任务取消失败后重复点击'},
                             {'candidate_index':1,'repeated_by':3,'reason':'同一取消确认失败后重复操作'}]}
    cleaned,required,audit=review_retries(plan,raw,[0,1,2,3])
    assert required==[2,3] and len(audit)==2 and 'omitted_retries' not in cleaned
    assert 'omitted_retries' in plan
    bad=copy.deepcopy(raw);bad[1]['observation']='取消完成'
    with pytest.raises(ValueError,match='video_retry_invalid'):review_retries(plan,bad,[0,1,2,3])
    bad=copy.deepcopy(raw);bad[0]['target']='确认打印'
    with pytest.raises(ValueError,match='video_retry_invalid'):review_retries(plan,bad,[0,1,2,3])
    bad=copy.deepcopy(plan);bad['events'].append({'candidate_index':0})
    with pytest.raises(ValueError,match='video_retry_invalid'):review_retries(bad,raw,[0,1,2,3])


def test_retry_reduction_cannot_skip_intervening_new_business_or_input():
    from task_server.services.ui_video_recovery import review_retries
    raw=[{'action':'aiTap','target':'取消','observation':'取消失败','start_ms':0,'end_ms':1,'frame_refs':['frame-001']},
         {'action':'aiInput','target':'新订单','observation':'输入','start_ms':2,'end_ms':3,'frame_refs':['frame-002']},
         {'action':'aiTap','target':'取消','observation':'取消完成','start_ms':4,'end_ms':5,'frame_refs':['frame-003']}]
    plan={'events':[{'candidate_index':1},{'candidate_index':2}], 'omitted_retries':[{'candidate_index':0,'repeated_by':2,'reason':'重试'}]}
    with pytest.raises(ValueError,match='video_retry_invalid'):review_retries(plan,raw,[0,1,2])


def test_long_video_does_not_drop_new_action_only_because_it_is_in_overlap(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*1000} for i in range(1,81)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[0]
    def model(skill,payload,**kw):
        if skill=='ui_video_observe':
            calls[0]+=1
            refs=['frame-027','frame-028'] if calls[0]==1 else ['frame-029','frame-030']
            if calls[0]>2:return {'events':[],'uncertainties':[],'multiple_scenarios':False}
            return {'events':[{'action':'aiTap','target':'同一按钮','value':'','frame_refs':refs,'observation':'目标页面显示'}],'uncertainties':[],'multiple_scenarios':False}
        return {'events':[{'candidate_index':i,'action':e['action'],'target':e['target'],'value':'','observation':e['observation'],'uncertainties':[]} for i,e in enumerate(payload['candidate_events'])], 'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=va.analyze_whole_frames({'app_id':'com.example.app','goal':'','media':{'frames':fs,'duration_ms':81000}},tmp_path,lambda:False)
    assert len(result['events'])==2


def test_retry_reduction_can_retain_original_confirmation_and_audit_later_retries():
    from task_server.services.ui_video_recovery import review_retries
    raw=[]
    for i,target in enumerate(['取消打印','确认取消','取消打印','确认取消','取消打印']):
        raw.append({'action':'aiTap','target':target,'start_ms':i*1000,'end_ms':i*1000+200,
                    'frame_refs':[f'frame-{i:03d}'],'observation':'取消流程未完成，请稍后重试' if i in {1,3} else '取消页面显示'})
    plan={'events':[{'candidate_index':0},{'candidate_index':1}],
          'omitted_retries':[{'candidate_index':2,'repeated_by':0,'reason':'重复取消'},
                             {'candidate_index':3,'repeated_by':1,'reason':'重复确认'},
                             {'candidate_index':4,'repeated_by':0,'reason':'重试后终于结束'}]}
    _,required,audit=review_retries(plan,raw,list(range(5)))
    assert required==[0,1] and len(audit)==3
    bad=copy.deepcopy(raw);bad[1]['observation']=bad[3]['observation']='没有失败，取消完成'
    with pytest.raises(ValueError,match='video_retry_invalid'):review_retries(plan,bad,list(range(5)))


@pytest.mark.parametrize("first_refs,start", [(["frame-025","frame-028"],25000),(["frame-028","frame-030"],27000)])
def test_long_video_merges_only_overlapping_typing_and_reports_checked_progress(monkeypatch,tmp_path,first_refs,start):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*1000} for i in range(1,81)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    windows=[0];progress=[]
    def model(skill,payload,**kw):
        if skill=='ui_video_observe':
            windows[0]+=1
            refs=first_refs if windows[0]==1 else ['frame-027','frame-032']
            events=[] if windows[0]>2 else [{'action':'aiInput','target':'文字输入框','value':'','frame_refs':refs,'observation':'输入后内容显示'}]
            return {'events':events,'uncertainties':[],'multiple_scenarios':False}
        return {'events':[{'candidate_index':i,'action':e['action'],'target':e['target'],'value':'','observation':e['observation'],'uncertainties':[]} for i,e in enumerate(payload['candidate_events'])],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=va.analyze_whole_frames({'app_id':'com.example.app','goal':'','media':{'frames':fs,'duration_ms':81000}},tmp_path,lambda:False,lambda *args:progress.append(copy.deepcopy(args)))
    assert len(result['events'])==1
    assert result['events'][0]['start_ms']==start and result['events'][0]['end_ms']==32000
    assert progress[-1][0]=='planning' and progress[-1][1]==progress[-1][2]==3


def test_only_verified_retry_omissions_extend_result_evidence_until_next_business():
    from task_server.services.ui_video_analysis import _validate_consolidated_evidence
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*1000} for i in range(8)]
    raw=[{'action':'aiTap','target':'按钮','start_ms':i*1000,'end_ms':i*1000,'frame_refs':[f'f{i}'],'evidence_kind':'inferred'} for i in [0,1,2,3,4,6]]
    output=copy.deepcopy([raw[0],raw[1],raw[5]])
    output[1]['outcome']={'observation':'业务终态','frame_refs':['f5']}
    args=(dict(events=output),raw,raw,fs,fs,8000,[0,1,5])
    with pytest.raises(ValueError,match='video_reference_invalid'):_validate_consolidated_evidence(*args)
    _validate_consolidated_evidence(*args,{2,3,4})
    output[1]['outcome']['frame_refs']=['f7']
    with pytest.raises(ValueError,match='video_reference_invalid'):_validate_consolidated_evidence(*args,{2,3,4})
