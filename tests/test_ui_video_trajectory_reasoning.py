import pytest
from task_server.services import ai_skill_service as ai


@pytest.mark.parametrize('model',['qwen3.8-max','qwen3.8-flash'])
def test_direct_bounded_trajectory_reasoning_uses_constant_budget(monkeypatch,model):
    monkeypatch.setattr(ai,'dashscope_model_for_images',lambda _:model)
    monkeypatch.setattr(ai,'dashscope_base_url',lambda:'https://dashscope.aliyuncs.com/compatible-mode/v1')
    body=ai.build_dashscope_chat_body('prompt',bounded_trajectory_reasoning=True)
    assert body['enable_thinking'] is True and body['thinking_budget']==1024


@pytest.mark.parametrize('model,base,flag,json_response',[
    ('qwen3.8-max','https://dashscope.aliyuncs.com/v1',False,True),
    ('qwen3.8-max','https://example.com/v1',True,True),
    ('qwen3.8-max','https://dashscope.aliyuncs.com.evil.example/v1',True,True),
    ('qwen3.7-plus','https://dashscope.aliyuncs.com/v1',True,True),
    ('qwen3.8-unknown','https://dashscope.aliyuncs.com/v1',True,True),
    ('qwen3.8-max','https://dashscope.aliyuncs.com/v1','true',True),
    ('qwen3.8-max','https://dashscope.aliyuncs.com/v1',True,False),
])
def test_direct_reasoning_scope_and_json_defaults(monkeypatch,model,base,flag,json_response):
    monkeypatch.setattr(ai,'dashscope_model_for_images',lambda _:model)
    monkeypatch.setattr(ai,'dashscope_base_url',lambda:base)
    body=ai.build_dashscope_chat_body('prompt',json_response=json_response,bounded_trajectory_reasoning=flag)
    assert body.get('enable_thinking') is not True and 'thinking_budget' not in body


@pytest.mark.parametrize('skill,phase,mode,enabled',[
    ('ui_video_trajectory','whole-trajectory','bounded',True),
    ('ui_video_trajectory','whole-trajectory','unknown',False),
    ('ui_video_trajectory','whole-observe','bounded',False),
    ('ui_video_observe','whole-observe','bounded',False),
])
def test_direct_skill_passes_only_exact_internal_reasoning_flag(monkeypatch,skill,phase,mode,enabled):
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY','0');calls=[]
    monkeypatch.setattr(ai,'render_ai_skill_prompt',lambda *a,**k:'prompt')
    monkeypatch.setattr(ai,'validate_ai_skill_output',lambda *a,**k:{})
    monkeypatch.setattr(ai,'dashscope_chat_content',lambda *a,**k:calls.append(k) or '{}')
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(2)]
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':'YQ=='} for f in frames]
    ai.run_ai_skill(skill,{'phase':phase,'reasoning_mode':mode,'thinking_budget':99999,'image_frames':frames},image_assets=assets)
    assert calls[0].get('bounded_trajectory_reasoning',False) is enabled
    assert 'thinking_budget' not in calls[0]


@pytest.mark.parametrize('invalid',[{'promptTokens':-1,'completionTokens':'12','reasoningTokens':float('nan'),'totalTokens':float('inf')},
                                     {'promptTokens':True,'completionTokens':None,'reasoningTokens':-float('inf'),'totalTokens':-0.5}])
def test_private_call_audit_keeps_only_finite_nonnegative_usage(monkeypatch,invalid):
    from task_server.services import ui_video_trajectory as vt,ui_video_windows
    from test_ui_video_trajectory import frames,event
    valid={'promptTokens':123,'completionTokens':45,'reasoningTokens':32,'totalTokens':168.0}
    recorded=[]
    def model(name,payload,**kw):
        trace=kw['runtime_trace'];stage=kw['version']
        trace.update({'providerId':'qwen_official','model':'qwen3.8-max','reasoning_content':'TOPSECRET'})
        if stage=='overview':trace['usage']={**valid,'arbitraryText':'TOPSECRET','reasoning_content':'TOPSECRET'}
        if stage=='observe':trace['usage']={**invalid,'arbitraryText':'TOPSECRET'}
        if stage=='audit':
            return dict(checks=[dict(event_id=p['track']['event_id'],trigger_verdict='supported',outcome_verdict='supported',question='') for p in payload['trajectory']],issues=[],multiple_scenarios=False,revisit=[])
        return dict(states=[],events=[event(['frame-001','frame-002'])] if stage=='observe' else [],checks=[],uncertainties=[],multiple_scenarios=False)
    monkeypatch.setattr(ai,'run_ai_skill',model);monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    result=vt.observe_trajectory({'media':dict(frames=frames(12),duration_ms=1201)},None,lambda:False,
                                 record=lambda name,value:recorded.append((name,value)))
    calls=result['trajectory_audit']['calls']
    assert calls[0]['usage']==valid and calls[0]['usage']['reasoningTokens']>0
    assert 'usage' not in calls[1] and 'usage' not in calls[2]
    assert all(c['requested_reasoning_mode']=='bounded' for c in calls)
    assert 'TOPSECRET' not in str(calls) and 'reasoning_content' not in str(calls)
    records=[v['runtime'] for n,v in recorded if n.endswith('-result')]
    assert records==calls
