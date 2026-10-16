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
