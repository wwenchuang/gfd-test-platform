"""Opt-in video stage contracts: offline real service transport and local runner."""
import copy
import io
import json
import pytest
from task_server.services import ai_skill_service as ai, ui_video_trajectory as vt, ui_video_windows


def evidence(n=3):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(n)]
    payload={'phase':'whole-perception','packet_id':'window-1','frames':frames,'image_frames':copy.deepcopy(frames),
             'owned_frame_ids':[f['frame_id'] for f in frames[:-1]],'context_frame_ids':[frames[-1]['frame_id']]}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':'YQ=='} for f in frames]
    return payload,assets


def semantic_schema():
    from task_server.services.ui_video_stage_policy import _provider_projection
    return _provider_projection(ai.load_ai_skill_schema('ui_video_trajectory')['oneOf'][0])


def capture(monkeypatch, content='{}'):
    calls=[]
    def urlopen(req,timeout):
        calls.append(json.loads(req.data))
        return io.BytesIO(json.dumps({'success':True,'content':content,'providerId':'official','model':'qwen3.8-max','fallbackUsed':False,'finishReason':'stop','usage':{'totalTokens':2}}).encode())
    monkeypatch.setattr(ai.urllib.request,'urlopen',urlopen)
    return calls


@pytest.mark.parametrize('n',[2,8,12])
def test_perception_preserves_complete_original_transport(monkeypatch,n):
    payload,assets=evidence(n);before=copy.deepcopy((payload,assets));calls=capture(monkeypatch)
    ai.ai_gateway_skill_content('ui_video_perception','pixels',payload=payload,image_assets=assets,video_stage='perception')
    body=calls[0]
    assert body['videoStage']=='perception' and 'outputSchema' not in body
    assert body['fallbackModelConfig']=={}
    assert [{k:a[k] for k in assets[0]} for a in body['imageAssets']]==assets
    assert body['payload']==payload and (payload,assets)==before


@pytest.mark.parametrize('change',['one','thirteen','order','asset','byte_limit','roles','metadata','detail','bad_base64'])
def test_perception_rejects_invalid_originals_before_transport(monkeypatch,change):
    p,a=evidence(1 if change=='one' else 13 if change=='thirteen' else 3);calls=capture(monkeypatch)
    if change=='order': p['frames'].reverse();p['image_frames'].reverse();a.reverse()
    if change=='asset': a[0]['name']='other'
    if change=='byte_limit': a[0]['base64']='a'*(10*1024*1024+1)
    if change=='roles': p['owned_frame_ids'].append(p['context_frame_ids'][0])
    if change=='metadata': p['image_frames'][0]['timestamp_ms']=1
    if change=='detail': p['detail_frames']=[{}]
    if change=='bad_base64': a[0]['base64']='@@'
    with pytest.raises(ValueError,match='video_'):
        ai.ai_gateway_skill_content('ui_video_perception','pixels',payload=p,image_assets=a,video_stage='perception')
    assert not calls


def test_semantic_zero_image_and_schema_forwarded(monkeypatch):
    p,_=evidence();p['phase']='whole-trajectory';p['reasoning_mode']='bounded';p.pop('image_frames');calls=capture(monkeypatch)
    ai.ai_gateway_skill_content('ui_video_trajectory','semantics',payload=p,image_assets=[],video_stage='semantic',output_schema=semantic_schema())
    assert calls[0]['imageAssets']==[] and calls[0]['outputSchema']==semantic_schema()
    assert calls[0]['videoStage']=='semantic'


@pytest.mark.parametrize('stage',['',None,False,1,[],{},'SEMANTIC','unknown'])
def test_invalid_explicit_stages_are_not_legacy(monkeypatch,stage):
    p,a=evidence();calls=capture(monkeypatch)
    with pytest.raises(ValueError,match='video_stage'):
        ai.ai_gateway_skill_content('ui_video_perception','pixels',payload=p,image_assets=a,video_stage=stage)
    assert not calls


@pytest.mark.parametrize('change',['missing','other_skill','phase','images','chronology','reasoning','schema_extra','schema_enum','schema_size'])
def test_semantic_invalid_combinations_rejected(monkeypatch,change):
    p,a=evidence();p.update(phase='whole-trajectory',reasoning_mode='bounded');p.pop('image_frames');s=semantic_schema();skill='ui_video_trajectory';assets=[];calls=capture(monkeypatch)
    if change=='missing':s=None
    if change=='other_skill':skill='other'
    if change=='phase':p['phase']='whole-perception'
    if change=='images':assets=a
    if change=='chronology':p['frames'].reverse()
    if change=='reasoning':p['reasoning_mode']='unbounded'
    if change=='schema_extra':s['description']='unsupported'
    if change=='schema_enum':s['properties']['events']['items']['enum']=[{}]
    if change=='schema_size':s['properties']['events']['items']['enum']=['x'*33000]
    with pytest.raises(ValueError,match='video_'):
        ai.ai_gateway_skill_content(skill,'semantics',payload=p,image_assets=assets,video_stage='semantic',output_schema=s)
    assert not calls


def test_stage_requires_gateway_and_no_repair(monkeypatch):
    p,a=evidence();monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY','0')
    with pytest.raises(ValueError,match='video_stage'):
        ai.run_ai_skill('ui_video_perception',p,image_assets=a,video_stage='perception')
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY','1')
    with pytest.raises(ValueError,match='video_stage'):
        ai.run_ai_skill('ui_video_perception',p,image_assets=a,video_stage='perception',repair_invalid_json=True)


def test_raw_receipt_precedes_invalid_json(monkeypatch):
    p,a=evidence();calls=capture(monkeypatch,content='not-json');receipts=[]
    with pytest.raises(RuntimeError):
        ai.run_ai_skill('ui_video_perception',p,image_assets=a,video_stage='perception',raw_receipt=receipts.append)
    assert len(calls)==1 and len(receipts)==1
    assert receipts[0]['content']=='not-json' and receipts[0]['finishReason']=='stop'
    assert set(receipts[0])=={'content','providerId','model','fallbackUsed','finishReason','usage','gateway_request_sha256'}
    assert len(receipts[0]['gateway_request_sha256'])==64


def test_perception_output_uses_strict_local_binding(monkeypatch):
    p,a=evidence();capture(monkeypatch,content=json.dumps({'packet_id':p['packet_id'],'frames':[],'uncertainties':[]}))
    with pytest.raises(RuntimeError,match='incomplete'):
        ai.run_ai_skill('ui_video_perception',p,image_assets=a,video_stage='perception')


def test_two_injected_runners_and_default_are_local(monkeypatch):
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *args:[])
    calls=[[],[],[]]
    def runner(i):
        def run(name,payload,**kwargs):
            calls[i].append((name,copy.deepcopy(payload),kwargs))
            if kwargs['version']=='audit':
                return {'checks':[{'event_id':item['track']['event_id'],'trigger_verdict':'supported',
                        'outcome_verdict':'supported','question':''} for item in payload['trajectory']],
                        'issues':[],'multiple_scenarios':False,'revisit':[]}
            events=[]
            if kwargs['version']=='observe':
                events=[{'action':'aiTap','target':'visible button','value':'','frame_refs':['frame-000','frame-001'],
                         'observation':'visible label','uncertainties':[],'trigger_frame':'frame-000','trigger_kind':'touch',
                         'page_before':'page-a','page_after':'page-a','continuation_of':'','edit_boundary':'none',
                         'outcome':{'frame_refs':['frame-001'],'observation':'visible outcome'}}]
            return {'states':[],'events':events,'checks':[],'uncertainties':[],'multiple_scenarios':False}
        return run
    default=runner(2);monkeypatch.setattr(ai,'run_ai_skill',default)
    p,_=evidence();doc={'media':{'frames':p['frames'],'duration_ms':300}}
    for i in (0,1):
        result=vt.observe_trajectory(doc,None,lambda:False,skill_runner=runner(i))
        assert len(result['events'])==1
        assert ai.run_ai_skill is default and not calls[2]
    result=vt.observe_trajectory(doc,None,lambda:False)
    assert len(result['events'])==1
    assert [len(c) for c in calls]==[3,3,3]
    for a,b in zip(calls[0],calls[2]):
        assert a[:2]==b[:2]
        assert {k:v for k,v in a[2].items() if k!='runtime_trace'}=={k:v for k,v in b[2].items() if k!='runtime_trace'}


def inventory(payload):
    return {'packet_id':payload['packet_id'],'frames':[{'frame_id':ref,'role':'owned','transient_marks':[],
        'persistent_ui':['literal visible text'],'field_state':'visible','keyboard_state':'hidden','changes':'same',
        'context_refs':[{'frame_id':payload['context_frame_ids'][0],'role':'context'}],'uncertainties':['unreadable small text']}
        for ref in payload['owned_frame_ids']],'uncertainties':['root question retained']}


def test_valid_inventory_returns_original_evidence_and_questions(monkeypatch):
    p,a=evidence();raw=inventory(p);capture(monkeypatch,content=json.dumps(raw));before=copy.deepcopy(raw)
    assert ai.run_ai_skill('ui_video_perception',p,image_assets=a,video_stage='perception')==before


@pytest.mark.parametrize('change',['owned_role','context_role','extra','schema_without_stage','perception_without_stage','defaults'])
def test_stage_never_silently_projects_roles_or_fills_output(monkeypatch,change):
    p,a=evidence();raw=inventory(p);kwargs={'video_stage':'perception'}
    if change=='owned_role':raw['frames'][0]['role']='context'
    if change=='context_role':raw['frames'][0]['context_refs'][0]['role']='owned'
    if change=='extra':raw['frames'][0]['business_action']='tap'
    if change=='schema_without_stage':kwargs={'output_schema':semantic_schema()}
    if change=='perception_without_stage':kwargs={}
    if change=='defaults':kwargs['output_defaults']={'made_up':'value'}
    capture(monkeypatch,content=json.dumps(raw))
    with pytest.raises((ValueError,RuntimeError)):
        ai.run_ai_skill('ui_video_perception',p,image_assets=a,**kwargs)


@pytest.mark.parametrize('invalid',[None,{},[],True,{'type':[]},{'type':'object','properties':[],'required':[],'additionalProperties':False}])
def test_malformed_schema_rejected_as_value_error(monkeypatch,invalid):
    p,_=evidence();p.update(phase='whole-trajectory',reasoning_mode='bounded');p.pop('image_frames');calls=capture(monkeypatch)
    with pytest.raises(ValueError,match='video_'):
        ai.ai_gateway_skill_content('ui_video_trajectory','semantic',payload=p,image_assets=[],video_stage='semantic',output_schema=invalid)
    assert not calls


def test_semantic_strict_output_checked_locally_before_defaults(monkeypatch):
    p,_=evidence();p.update(phase='whole-trajectory',reasoning_mode='bounded');p.pop('image_frames')
    raw={'states':[],'events':[],'checks':[],'uncertainties':[],'multiple_scenarios':False,'revisit':[]}
    calls=capture(monkeypatch,content=json.dumps(raw))
    assert ai.run_ai_skill('ui_video_trajectory',p,version='observe',image_assets=[],video_stage='semantic',output_schema=semantic_schema())==raw
    capture(monkeypatch,content=json.dumps({**raw,'extra':'forbidden'}))
    with pytest.raises(RuntimeError,match='video_semantic_output_invalid'):
        ai.run_ai_skill('ui_video_trajectory',p,version='observe',image_assets=[],video_stage='semantic',output_schema=semantic_schema())
    assert len(calls)==1


@pytest.mark.parametrize('selected',[None,[],['frame-000','frame-000'],['frame-999'],[1],list(f'frame-{i:03d}' for i in range(13))])
def test_audit_rejects_selection_before_transport(monkeypatch,selected):
    p,a=evidence();p.update(phase='whole-trajectory',reasoning_mode='bounded',perception_mode='fine-detail',perception_frame_ids=selected);calls=capture(monkeypatch)
    with pytest.raises(ValueError,match='video_'):
        ai.ai_gateway_skill_content('ui_video_trajectory','audit',payload=p,image_assets=a,video_stage='audit')
    assert not calls


def test_receipt_uses_only_safe_usage_fields(monkeypatch):
    p,a=evidence();receipts=[]
    def urlopen(req,timeout):
        return io.BytesIO(json.dumps({'success':True,'content':'{}','providerId':'official','model':'qwen3.8-max',
          'fallbackUsed':False,'finishReason':'stop','usage':{'totalTokens':3,'promptTokens':1,'completionTokens':2,'thoughts':'private','bad':{},'reasoningTokens':-1}}).encode())
    monkeypatch.setattr(ai.urllib.request,'urlopen',urlopen)
    ai.ai_gateway_skill_content('ui_video_perception','pixels',payload=p,image_assets=a,video_stage='perception',raw_receipt=receipts.append)
    assert receipts[0]['usage']=={'totalTokens':3,'promptTokens':1,'completionTokens':2}


def test_legacy_request_body_is_exact_representative_contract(monkeypatch):
    calls=capture(monkeypatch)
    ai.ai_gateway_skill_content('ordinary_skill','legacy',payload={'unchanged':True},model_config={'providerId':'p','model':'m'},timeout=120,max_tokens=321)
    assert calls==[{'skillName':'ordinary_skill','prompt':'legacy','payload':{'unchanged':True},'temperature':.1,
      'jsonResponse':True,'modelConfig':{'providerId':'p','model':'m'},'providerId':'p','model':'m','imageAssets':[],
      'fallbackModelConfig':{},'timeoutMs':118000,'maxTokens':321}]


def test_duplicate_enum_is_invalid_before_transport(monkeypatch):
    p,_=evidence();p.update(phase='whole-trajectory',reasoning_mode='bounded');p.pop('image_frames');s=semantic_schema()
    s['properties']['events']['items']['properties']['action']['enum']=['aiTap','aiTap'];calls=capture(monkeypatch)
    with pytest.raises(ValueError,match='video_output_schema_invalid'):
        ai.ai_gateway_skill_content('ui_video_trajectory','semantic',payload=p,image_assets=[],video_stage='semantic',output_schema=s)
    assert not calls


def test_semantic_accepts_original_scroll_value_union_with_strict_local_output(monkeypatch):
    def subset(node):
        if 'oneOf' in node:return {'anyOf':[subset(child) for child in node['oneOf']]}
        out={k:v for k,v in node.items() if k in {'type','enum','required','additionalProperties'}}
        if 'enum' in out:out.setdefault('type','string')
        if 'properties' in node:
            out['properties']={k:subset(v) for k,v in node['properties'].items()}
            out['required']=list(out['properties'])
        if 'items' in node:out['items']=subset(node['items'])
        return out
    s=subset(ai.load_ai_skill_schema('ui_video_trajectory')['oneOf'][0])
    p,_=evidence();p.update(phase='whole-trajectory',reasoning_mode='bounded');p.pop('image_frames')
    event={'action':'aiScroll','target':'visible list','value':{'direction':'down','scrollType':'singleAction'},
           'frame_refs':['frame-000'],'observation':'visible list moves','uncertainties':[],'trigger_frame':'frame-000',
           'trigger_kind':'touch','page_before':'page-a','page_after':'page-a','continuation_of':'','edit_boundary':'none',
           'outcome':{'frame_refs':['frame-001'],'observation':'list rows changed'}}
    raw={'states':[],'events':[event],'checks':[],'uncertainties':[],'multiple_scenarios':False,'revisit':[]}
    capture(monkeypatch,content=json.dumps(raw))
    assert ai.run_ai_skill('ui_video_trajectory',p,version='observe',image_assets=[],video_stage='semantic',output_schema=s)==raw


@pytest.mark.parametrize('change',['fake_events','missing_state_field','extra_event_field','unknown_action'])
def test_semantic_schema_cannot_replace_the_original_scaffold(monkeypatch,change):
    p,_=evidence();p.update(phase='whole-trajectory',reasoning_mode='bounded');p.pop('image_frames');s=semantic_schema()
    if change=='fake_events':s['properties']['events']['items']={'type':'string'}
    if change=='missing_state_field':
        state=s['properties']['states']['items'];del state['properties']['description'];state['required'].remove('description')
    if change=='extra_event_field':
        event=s['properties']['events']['items'];event['properties']['made_up']={'type':'string'};event['required'].append('made_up')
    if change=='unknown_action':s['properties']['events']['items']['properties']['action']['enum']=['made_up']
    calls=capture(monkeypatch)
    with pytest.raises(ValueError,match='video_output_schema_invalid'):
        ai.ai_gateway_skill_content('ui_video_trajectory','semantic',payload=p,image_assets=[],video_stage='semantic',output_schema=s)
    assert not calls


@pytest.mark.parametrize('value',['secret input',{'direction':'diagonal','scrollType':'singleAction'},
                                  {'direction':'down','scrollType':'singleAction','extra':True}])
def test_full_original_semantic_output_rejects_wrong_value(monkeypatch,value):
    p,_=evidence();p.update(phase='whole-trajectory',reasoning_mode='bounded');p.pop('image_frames')
    event={'action':'aiScroll','target':'visible list','value':value,'frame_refs':['frame-000'],
           'observation':'visible list moves','uncertainties':[],'trigger_frame':'frame-000','trigger_kind':'touch',
           'page_before':'page-a','page_after':'page-a','continuation_of':'','edit_boundary':'none',
           'outcome':{'frame_refs':['frame-001'],'observation':'list rows changed'}}
    raw={'states':[],'events':[event],'checks':[],'uncertainties':[],'multiple_scenarios':False,'revisit':[]}
    capture(monkeypatch,content=json.dumps(raw))
    with pytest.raises(RuntimeError,match='video_semantic_output_invalid'):
        ai.run_ai_skill('ui_video_trajectory',p,version='observe',image_assets=[],video_stage='semantic',output_schema=semantic_schema())


def test_image_audit_with_empty_trajectory_and_no_fine_selection_is_valid(monkeypatch):
    p,a=evidence();p.update(phase='whole-trajectory',reasoning_mode='bounded',trajectory=[]);calls=capture(monkeypatch)
    assert ai.ai_gateway_skill_content('ui_video_trajectory','audit',payload=p,image_assets=a,video_stage='audit')=='{}'
    assert calls[0]['imageAssets'] and 'perception_frame_ids' not in calls[0]['payload']


@pytest.mark.parametrize('change',[{'model':'qwen3.8-flash'},{'fallbackUsed':True},{'finishReason':'length'},
                                  {'finishReason':'content_filter'},{'finishReason':''}])
def test_stage_reply_guard_runs_after_safe_raw_receipt(monkeypatch,change):
    p,a=evidence();receipts=[]
    response={'success':True,'content':'unparsed raw','providerId':'official','model':'qwen3.8-max',
              'fallbackUsed':False,'finishReason':'stop',**change}
    monkeypatch.setattr(ai.urllib.request,'urlopen',lambda *args,**kw:io.BytesIO(json.dumps(response).encode()))
    with pytest.raises(RuntimeError,match='video_stage_reply_invalid'):
        ai.ai_gateway_skill_content('ui_video_perception','pixels',payload=p,image_assets=a,video_stage='perception',raw_receipt=receipts.append)
    assert len(receipts)==1 and receipts[0]['content']=='unparsed raw'
