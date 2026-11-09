"""Missing model questions stay visible and block a draft until reviewed."""
import copy
import json
import pytest
from task_server.services import ai_skill_service as ai,ui_video_windows as windows


def _response():
    return {'events':[{'action':'aiTap','target':'订单入口','value':'',
                       'frame_refs':['frame-001','frame-002'],'observation':'订单页面可见',
                       'uncertainties':['触点细节待核对']}], 'multiple_scenarios':False}


def _run_window(monkeypatch,tmp_path,response,version=None,calls=None):
    from task_server.services import ui_video_touch_targets as touch
    frames=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]
    (tmp_path/'frames').mkdir(exist_ok=True)
    for frame in frames:(tmp_path/'frames'/(frame['frame_id']+'.jpg')).write_bytes(b'image')
    monkeypatch.setattr(touch,'probe',lambda *args:[])
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY','1')
    calls=[] if calls is None else calls
    def gateway(*args,**kwargs):
        calls.append(1)
        return json.dumps(response(len(calls)) if callable(response) else response)
    monkeypatch.setattr(ai,'ai_gateway_skill_content',gateway)
    if version:
        assets=windows.load_assets(frames,tmp_path,lambda:False)
        return ai.run_ai_skill('ui_video_observe',{'phase':'whole-observe','frames':frames,'image_frames':frames},
                               image_assets=assets,version=version)
    return windows.observe_windows(frames,200,tmp_path,lambda:False)[0]


def test_missing_root_questions_become_frame_scoped_review_not_empty(monkeypatch,tmp_path):
    calls=[];result=_run_window(monkeypatch,tmp_path,_response(),calls=calls)
    assert len(calls)==2
    assert result['events'][0]['uncertainties']==['触点细节待核对']
    assert len(result['uncertainties'])==1
    question=result['uncertainties'][0]
    assert '未返回不确定项字段' in question and '无法判断' in question and '核对' in question
    assert 'frame-001' in question and 'frame-002' in question
    assert '0' in question and '0.1' in question


def test_explicit_root_questions_are_not_replaced(monkeypatch,tmp_path):
    response={**_response(),'uncertainties':['模型自述仍需核对']};calls=[]
    result=_run_window(monkeypatch,tmp_path,response,calls=calls)
    assert len(calls)==1 and result['uncertainties']==['模型自述仍需核对']


def test_complete_second_observation_is_used_without_fabricating_warning(monkeypatch,tmp_path):
    calls=[]
    def response(number):return _response() if number==1 else {**_response(),'uncertainties':[]}
    result=_run_window(monkeypatch,tmp_path,response,calls=calls)
    assert len(calls)==2 and result['uncertainties']==[]


@pytest.mark.parametrize('mutation',['null','wrong_type','missing_events','missing_multiple','touch_target'])
def test_only_absent_root_questions_in_normal_observation_may_be_marked_for_review(monkeypatch,tmp_path,mutation):
    response=_response()
    if mutation=='null':response['uncertainties']=None
    if mutation=='wrong_type':response['uncertainties']='unknown'
    if mutation=='missing_events':response.pop('events')
    if mutation=='missing_multiple':response.pop('multiple_scenarios')
    with pytest.raises(ValueError):_run_window(monkeypatch,tmp_path,response,'touch-target' if mutation=='touch_target' else None)


def test_cross_window_questions_over_limit_fail_instead_of_truncating(monkeypatch,tmp_path):
    from task_server.services import ui_video_touch_detail as detail,ui_video_touch_targets as touch
    frames=[{'frame_id':f'frame-{i:03}','timestamp_ms':i*100} for i in range(1,62)]
    monkeypatch.setattr(windows,'load_assets',lambda *args:[])
    monkeypatch.setattr(detail,'append_details',lambda *args:[])
    monkeypatch.setattr(touch,'probe',lambda *args:[])
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        return {'events':[],'uncertainties':[f'窗口{len(calls)}疑问{i}' for i in range(16)],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError) as caught:windows.observe_windows(frames,10000,tmp_path,lambda:False)
    assert getattr(caught.value,'analysis_reason',None)=='root_question_limit'


@pytest.mark.parametrize('mode',['duplicate','input'])
def test_overlapping_window_events_keep_new_event_questions(monkeypatch,tmp_path,mode):
    from task_server.services import ui_video_touch_detail as detail,ui_video_touch_targets as touch
    from task_server.services.ui_video_business_inputs import _MANUAL
    frames=[{'frame_id':f'frame-{i:03}','timestamp_ms':i*100} for i in range(1,62)]
    monkeypatch.setattr(windows,'load_assets',lambda *args:[])
    monkeypatch.setattr(detail,'append_details',lambda *args:[])
    monkeypatch.setattr(touch,'probe',lambda *args:[])
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        if len(calls)>2:return {'events':[],'uncertainties':[],'multiple_scenarios':False}
        refs=['frame-027','frame-028'] if mode=='duplicate' or len(calls)==2 else ['frame-026','frame-027']
        return {'events':[{'action':'aiTap' if mode=='duplicate' else 'aiInput','target':'名称输入框',
                           'value':'','frame_refs':refs,'observation':'页面显示',
                           'uncertainties':['第一窗问题' if len(calls)==1 else '第二窗新增问题']}],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result,_=windows.observe_windows(frames,10000,tmp_path,lambda:False)
    all_questions=result['uncertainties']+[q for e in result['events'] for q in e['uncertainties']]
    assert any('第一窗问题' in q for q in all_questions)
    assert any('第二窗新增问题' in q for q in all_questions)
    assert not any(_MANUAL in q for q in result['uncertainties'])
    if mode=='input':assert any(_MANUAL in q for e in result['events'] for q in e['uncertainties'])


@pytest.mark.parametrize('case',['preserve_event','overflow'])
def test_local_repair_preserves_review_questions_or_fails_closed(monkeypatch,tmp_path,case):
    from task_server.services import ui_video_reconcile as rec,ui_video_touch_detail as detail
    frames=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]
    old={'action':'aiTap','target':'订单入口','value':'','frame_refs':['frame-001','frame-002'],
         'start_ms':0,'end_ms':100,'observation':'订单页显示','uncertainties':['复核事件仍需确认'],
         'evidence_kind':'inferred'}
    checked={'events':[old],'uncertainties':[f'既有问题{i}' for i in range(20)] if case=='overflow' else [],
             'multiple_scenarios':False}
    monkeypatch.setattr(windows,'load_assets',lambda *args:[])
    monkeypatch.setattr(detail,'append_details',lambda *args:[])
    def model(*args,**kwargs):
        item={k:v for k,v in old.items() if k not in {'start_ms','end_ms','evidence_kind'}}
        item['uncertainties']=[]
        return {'events':[item],'uncertainties':[f'新问题{i}' for i in range(11)] if case=='overflow' else [],
                'multiple_scenarios':False,'coverage_resolutions':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    if case=='overflow':
        with pytest.raises(ValueError) as caught:
            rec._repair_window([old],checked,frames,200,tmp_path,lambda:False,[],{'frame-001','frame-002'})
        assert getattr(caught.value,'analysis_reason',None)=='root_question_limit'
    else:
        result=rec._repair_window([old],checked,frames,200,tmp_path,lambda:False,[],{'frame-001','frame-002'})
        assert any('复核事件仍需确认' in q for q in result['uncertainties'])


@pytest.mark.parametrize('repair_mode',['reconcile','direct'])
def test_first_pass_and_review_questions_reach_draft_gate(monkeypatch,tmp_path,repair_mode):
    from task_server.services import ui_video_analysis as analysis,ui_video_reconcile as rec,ui_video_delayed_response as delayed
    from task_server.services.ui_case_draft_contract import build_draft_yaml
    from task_server.services.ui_video_business_inputs import _MANUAL
    frames=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]
    event={'action':'aiAssert','target':'订单页可见','value':'','observation':'订单页可见','uncertainties':[],
           'start_ms':0,'end_ms':100,'frame_refs':['frame-001','frame-002'],'evidence_kind':'inferred'}
    first={'events':[{**event,'uncertainties':['首轮事件问题：是否点击正确',_MANUAL]}],
           'uncertainties':['首轮根问题：操作是否发生待核对'],'multiple_scenarios':False}
    review={'events':[event],'uncertainties':['复核根问题：画面是否更新待核对'],'multiple_scenarios':False}
    monkeypatch.setattr(windows,'observe_windows',lambda *args,**kwargs:(copy.deepcopy(first),[]))
    monkeypatch.setattr(rec,'needs_reconciliation',lambda *args:repair_mode=='reconcile')
    monkeypatch.setattr(rec,'reconcile',lambda *args:copy.deepcopy(review))
    if repair_mode=='direct':
        monkeypatch.setattr(rec,'_tap_scroll_refs',lambda *args:{'frame-001'})
        monkeypatch.setattr(rec,'_repair_coverage',lambda *args:copy.deepcopy(review))
    monkeypatch.setattr(delayed,'review_pending_responses',lambda observed,*args:observed)
    monkeypatch.setattr(analysis,'consolidate_observed_frames',lambda document,root,cancelled,observed,*args:observed)
    doc={'source_id':'uvs_'+'a'*32,'app_id':'com.kfb.model','analysis_version':5,'goal':'',
         'media':{'frames':frames,'duration_ms':200}}
    observed=analysis.analyze_whole_frames(doc,tmp_path,lambda:False)
    assert all(q in observed['uncertainties'] for q in first['uncertainties']+review['uncertainties'])
    assert any('首轮事件问题：是否点击正确' in q and '0' in q for q in observed['uncertainties'])
    assert not any(_MANUAL in q for q in observed['uncertainties'])
    proposal,_=analysis.draft_proposal({**doc,**observed})
    for step in proposal['steps']:step['review_state']='confirmed'
    gate=build_draft_yaml(proposal,'com.kfb.model')
    assert not gate['ready'] and all(q in gate['questions'] for q in observed['uncertainties'])


def test_missing_root_warning_survives_subsequent_review_and_draft_gate(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as analysis,ui_video_reconcile as rec,ui_video_delayed_response as delayed
    from task_server.services.ui_case_draft_contract import build_draft_yaml
    first=_run_window(monkeypatch,tmp_path,_response())
    warning=first['uncertainties'][0]
    review={'events':copy.deepcopy(first['events']),'uncertainties':[],'multiple_scenarios':False}
    review['events'][0]['uncertainties']=[]
    monkeypatch.setattr(windows,'observe_windows',lambda *args,**kwargs:(copy.deepcopy(first),[]))
    monkeypatch.setattr(rec,'needs_reconciliation',lambda *args:True)
    monkeypatch.setattr(rec,'reconcile',lambda *args:copy.deepcopy(review))
    monkeypatch.setattr(delayed,'review_pending_responses',lambda observed,*args:observed)
    monkeypatch.setattr(analysis,'consolidate_observed_frames',lambda document,root,cancelled,observed,*args:observed)
    frames=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]
    doc={'source_id':'uvs_'+'a'*32,'app_id':'com.kfb.model','analysis_version':5,'goal':'',
         'media':{'frames':frames,'duration_ms':200}}
    observed=analysis.analyze_whole_frames(doc,tmp_path,lambda:False)
    assert warning in observed['uncertainties']
    assert any('触点细节待核对' in q for q in observed['uncertainties'])
    proposal,_=analysis.draft_proposal({**doc,**observed})
    for step in proposal['steps']:step['review_state']='confirmed'
    gate=build_draft_yaml(proposal,'com.kfb.model')
    assert not gate['ready'] and warning in gate['questions']
