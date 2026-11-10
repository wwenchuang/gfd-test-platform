"""A bare probe label is an unresolved candidate, never proof of identity."""
import copy
import pytest
from task_server.services import ui_video_touch_targets as targets
from task_server.services import ui_video_windows as windows, ui_video_touch_detail as detail
from task_server.services.ui_video_analysis import draft_proposal
from task_server.services.ui_case_draft_contract import build_draft_yaml

FRAMES=[{'frame_id':'frame-120','timestamp_ms':44900},{'frame_id':'frame-121','timestamp_ms':45000}]
QUALIFIED='耗材颜色确认弹窗的确认按钮'

def row(target=QUALIFIED,refs=None,uncertainties=None):
    return {'action':'aiTap','target':target,'value':'','frame_refs':refs or ['frame-120','frame-121'],
            'observation':'弹窗关闭','uncertainties':uncertainties or []}

def run(monkeypatch,tmp_path,*,event=None,hints=None,root_questions=None,prior_secrets=()):
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a: hints if hints is not None else [{'frame_id':'frame-121','target':'确认'}])
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        return {'events':copy.deepcopy(event if isinstance(event,list) else [event or row()]),
                'uncertainties':copy.deepcopy(root_questions or []),'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    result,secrets=windows.observe_windows(FRAMES,50000,tmp_path,lambda:False,prior_secrets=prior_secrets)
    return result,calls,secrets

def test_same_frame_dialog_description_preserves_both_and_blocks_trial(monkeypatch,tmp_path):
    out,calls,_=run(monkeypatch,tmp_path)
    assert len(calls)==1 and len(out['events'])==1
    assert out['events'][0]['target']==QUALIFIED
    assert out['events'][0]['frame_refs']==['frame-120','frame-121']
    question=next(q for q in out['uncertainties'] if 'frame-121' in q)
    assert QUALIFIED in question and '确认' in question and '核对' in question
    assert question in out['events'][0]['uncertainties']
    doc={'app_id':'com.kfb.model','source_id':'uvs_'+'0'*32,'analysis_version':7,'goal':'',
         'media':{'frames':FRAMES,'duration_ms':50000},**out}
    proposal,_=draft_proposal(doc)
    for step in proposal['steps']:step['review_state']='confirmed'
    assert not build_draft_yaml(proposal,doc['app_id'])['ready']

@pytest.mark.parametrize('left,right',[
 ('耗材颜色确认弹窗的确认按钮','取消'),('耗材颜色确认弹窗的确认按钮','确认打印'),
 ('耗材颜色确认弹窗的确认按钮','打印设置弹窗的确认按钮'),('订单区域的确认按钮','确认'),
 ('耗材颜色确认弹窗的确认输入框','确认'),('耗材颜色确认弹窗的确认按钮',''),
])
def test_different_labels_contexts_or_types_still_fail(monkeypatch,tmp_path,left,right):
    with pytest.raises(ValueError,match='video_model_invalid'):
        run(monkeypatch,tmp_path,event=row(left),hints=[{'frame_id':'frame-121','target':right}])

@pytest.mark.parametrize('case',['nearby','two_events','two_hints','other_conflict'])
def test_ambiguous_binding_remains_failure(monkeypatch,tmp_path,case):
    event=row()
    hints=[{'frame_id':'frame-121','target':'确认'}]
    if case=='nearby':event=row(refs=['frame-120'])
    elif case=='two_events':event=[row(),row('其他弹窗的确认按钮')]
    elif case=='two_hints':hints.append({'frame_id':'frame-121','target':'确认'})
    else:hints.append({'frame_id':'frame-120','target':'取消'})
    with pytest.raises(ValueError,match='video_model_invalid'):
        run(monkeypatch,tmp_path,event=event,hints=hints)

@pytest.mark.parametrize('capacity',['event','root'])
def test_review_budget_never_truncated(monkeypatch,tmp_path,capacity):
    event=row(uncertainties=[f'需要确认事项{i}' for i in range(10)]) if capacity=='event' else row()
    root=[f'根部问题{i}' for i in range(30)] if capacity=='root' else []
    with pytest.raises(ValueError,match='video_model_invalid'):
        run(monkeypatch,tmp_path,event=event,root_questions=root)

def test_redacts_known_input_from_both_descriptions_and_question(monkeypatch,tmp_path):
    secret='私密测试输入'
    out,_,_=run(monkeypatch,tmp_path,event=row('耗材颜色确认弹窗的确认按钮'),prior_secrets=[secret],
        hints=[{'frame_id':'frame-121','target':'确认','observation':'内容为'+secret}])
    assert secret not in repr(out)

def test_nearby_real_frame_is_not_silent_evidence():
    events=[row()]
    hints=[{'frame_id':'frame-121','target':'确认'}]
    assert targets.conflict(events,hints,FRAMES)

@pytest.mark.parametrize('mode',['reconcile','direct'])
def test_dialog_question_survives_subsequent_review_and_planning(monkeypatch,tmp_path,mode):
    from task_server.services import ui_video_analysis as analysis,ui_video_reconcile as rec,ui_video_delayed_response as delayed
    first,_,_=run(monkeypatch,tmp_path)
    first_question=next(q for q in first['uncertainties'] if 'frame-121' in q)
    second=copy.deepcopy(first)
    second['uncertainties']=[]
    for event in second['events']:event['uncertainties']=[]
    monkeypatch.setattr(windows,'observe_windows',lambda *a,**kw:(copy.deepcopy(first),[]))
    monkeypatch.setattr(rec,'needs_reconciliation',lambda *a:mode=='reconcile')
    monkeypatch.setattr(rec,'reconcile',lambda *a:copy.deepcopy(second))
    if mode=='direct':
        monkeypatch.setattr(rec,'_tap_scroll_refs',lambda *a:{'frame-121'})
        monkeypatch.setattr(rec,'_repair_coverage',lambda *a:copy.deepcopy(second))
    monkeypatch.setattr(delayed,'review_pending_responses',lambda observed,*a:observed)
    monkeypatch.setattr(analysis,'consolidate_observed_frames',lambda document,root,cancelled,observed,*a:observed)
    doc={'source_id':'uvs_'+'a'*32,'app_id':'com.kfb.model','analysis_version':7,'goal':'',
         'media':{'frames':FRAMES,'duration_ms':50000}}
    final=analysis.analyze_whole_frames(doc,tmp_path,lambda:False)
    assert first_question in final['uncertainties']
    proposal,_=analysis.draft_proposal({**doc,**final})
    for step in proposal['steps']:step['review_state']='confirmed'
    gate=build_draft_yaml(proposal,doc['app_id'])
    assert not gate['ready'] and any(first_question in q for q in gate['questions'])

def test_known_input_in_qualified_target_is_redacted(monkeypatch,tmp_path):
    secret='私密测试输入'
    out,_,_=run(monkeypatch,tmp_path,event=row(secret+'弹窗的确认按钮'),prior_secrets=[secret])
    assert secret not in repr(out)
    assert '[输入内容未保存]' in repr(out)

@pytest.mark.parametrize('action',['aiInput','navigateBack','aiAssert'])
def test_non_tap_probe_is_not_reinterpreted_as_equivalent(monkeypatch,tmp_path,action):
    # For native-action hints, other existing logic governs behavior; they must
    # never enter this dialog target exception.
    diag={'frame_id':'frame-121','binding':'explicit','comparison':'mismatch'}
    hint={'frame_id':'frame-121','target':'确认','action':action}
    assert targets.dialog_target_review([row()],[hint],FRAMES,diag) is None

@pytest.mark.parametrize('capacity',['event','root'])
def test_existing_dialog_question_at_capacity_does_not_duplicate_or_fail(monkeypatch,tmp_path,capacity):
    question=f'触点 frame-121 的目标有两种描述：主观察“{QUALIFIED}”，局部复核“确认”；请对照原视频核对是否实际点击、是否同一次操作及具体控件。'
    questions=[question]+[f'问题{i}' for i in range(9 if capacity=='event' else 29)]
    event=row(uncertainties=questions if capacity=='event' else [])
    root=questions if capacity=='root' else []
    out,_,_=run(monkeypatch,tmp_path,event=event,root_questions=root)
    assert out['uncertainties'].count(question)==1
    assert out['events'][0]['uncertainties'].count(question)==1

@pytest.mark.parametrize('scenario',['multiple_events','multiple_hints','other_conflict'])
def test_ambiguous_first_result_cannot_be_erased_by_retry(monkeypatch,tmp_path,scenario):
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    hints=[{'frame_id':'frame-121','target':'确认'}]
    if scenario=='multiple_hints':hints.append({'frame_id':'frame-121','target':'确认'})
    if scenario=='other_conflict':hints.append({'frame_id':'frame-120','target':'取消'})
    monkeypatch.setattr(targets,'probe',lambda *a:hints)
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        events=[row(),row('另一个弹窗的确认按钮')] if scenario=='multiple_events' and len(calls)==1 else [row()]
        return {'events':events,'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_invalid'):
        windows.observe_windows(FRAMES,50000,tmp_path,lambda:False)
    assert len(calls)==1

def test_dialog_exception_never_accepts_second_answer_after_other_failure(monkeypatch,tmp_path):
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-121','target':'确认'}])
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        if len(calls)==1:
            return {'events':[row('错误按钮')],'uncertainties':[],'multiple_scenarios':False}
        return {'events':[row()],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_invalid'):
        windows.observe_windows(FRAMES,50000,tmp_path,lambda:False)
    assert len(calls)==2

@pytest.mark.parametrize('capacity',['event','root'])
def test_dialog_budget_exhaustion_cannot_be_erased_by_retry(monkeypatch,tmp_path,capacity):
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-121','target':'确认'}])
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        if len(calls)==1:
            return {'events':[row(uncertainties=[f'事项{i}' for i in range(10)] if capacity=='event' else [])],
                    'uncertainties':[f'问题{i}' for i in range(30)] if capacity=='root' else [],'multiple_scenarios':False}
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_invalid'):
        windows.observe_windows(FRAMES,50000,tmp_path,lambda:False)
    assert len(calls)==1

def test_post_candidate_hint_limit_does_not_allow_retry_to_erase_action(monkeypatch,tmp_path):
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-121','target':'确认','requires_review':True}])
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        return {'events':[row(uncertainties=[f'问题{i}' for i in range(9)])] if len(calls)==1 else [],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_invalid'):
        windows.observe_windows(FRAMES,50000,tmp_path,lambda:False)
    assert len(calls)==1

def test_nearby_dialog_conflict_cannot_be_erased_by_empty_retry(monkeypatch,tmp_path):
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-121','target':'确认'}])
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        return {'events':[row(refs=['frame-120'])] if len(calls)==1 else [],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_invalid'):
        windows.observe_windows(FRAMES,50000,tmp_path,lambda:False)
    assert len(calls)==1

def test_other_probe_with_multiple_nearby_actions_blocks_dialog_exception(monkeypatch,tmp_path):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,7)]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    hints=[{'frame_id':'frame-002','target':'确认'},{'frame_id':'frame-005','target':'退款'}]
    monkeypatch.setattr(targets,'probe',lambda *a:hints)
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        return {'events':[row(QUALIFIED,['frame-001','frame-002']),row('删除',['frame-003']),row('保存',['frame-004'])],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_invalid'):
        windows.observe_windows(frames,1000,tmp_path,lambda:False)
    assert len(calls)==2

def test_other_unmatched_probe_cannot_disappear_while_dialog_exception_applies(monkeypatch,tmp_path):
    frames=[{'frame_id':'frame-118','timestamp_ms':43000},*FRAMES]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-118','target':'关闭'}, {'frame_id':'frame-121','target':'确认'}])
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        return {'events':[row()],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_invalid'):
        windows.observe_windows(frames,50000,tmp_path,lambda:False)
    assert len(calls)==2

def test_other_same_label_nearby_probe_requires_its_own_explicit_evidence(monkeypatch,tmp_path):
    frames=[{'frame_id':'frame-119','timestamp_ms':44800},*FRAMES]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    hints=[{'frame_id':'frame-119','target':QUALIFIED}, {'frame_id':'frame-121','target':'确认'}]
    monkeypatch.setattr(targets,'probe',lambda *a:hints)
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        return {'events':[row()],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_invalid'):
        windows.observe_windows(frames,50000,tmp_path,lambda:False)
    assert len(calls)==2

def test_distinct_frame_other_probe_cannot_be_swallowed_by_same_event(monkeypatch,tmp_path):
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    hints=[{'frame_id':'frame-120','target':QUALIFIED}, {'frame_id':'frame-121','target':'确认'}]
    monkeypatch.setattr(targets,'probe',lambda *a:hints)
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        return {'events':[row()],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_invalid'):
        windows.observe_windows(FRAMES,50000,tmp_path,lambda:False)
    assert len(calls)==1

def test_another_probe_with_own_distinct_explicit_event_is_preserved(monkeypatch,tmp_path):
    frames=[{'frame_id':'frame-118','timestamp_ms':43000},*FRAMES]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-118','target':'关闭'}, {'frame_id':'frame-121','target':'确认'}])
    def model(*args,**kwargs):
        return {'events':[row('关闭',['frame-118']),row()], 'uncertainties':[], 'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    result,_=windows.observe_windows(frames,50000,tmp_path,lambda:False)
    assert [(e['target'],e['frame_refs']) for e in result['events']]==[('关闭',['frame-118']),(QUALIFIED,['frame-120','frame-121'])]
    assert any('frame-121' in q for q in result['uncertainties'])

def test_other_two_probes_cannot_share_another_single_action(monkeypatch,tmp_path):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,6)]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    hints=[{'frame_id':'frame-002','target':'确认'}, {'frame_id':'frame-003','target':'保存'}, {'frame_id':'frame-004','target':'保存'}]
    monkeypatch.setattr(targets,'probe',lambda *a:hints)
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        return {'events':[row(QUALIFIED,['frame-001','frame-002']),row('保存',['frame-003','frame-004'])],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_invalid'):
        windows.observe_windows(frames,1000,tmp_path,lambda:False)
    assert len(calls)==1

# Real draft store and service/try gates; model and device are isolated fixtures.
from test_ui_case_draft_execution import draft_env, trial
from test_ui_batch_dispatch import dispatch_env
from test_ui_execution_service import environment, ACTORS, TARGET
from task_server.services.operation_attribution import actor_context

def test_actual_draft_save_reload_validate_and_try_default_review_gate(draft_env,monkeypatch,tmp_path):
    from task_server import identity
    from task_server.services import ui_case_draft_service as service
    observed,_,_=run(monkeypatch,tmp_path)
    question=next(q for q in observed['uncertainties'] if 'frame-121' in q)
    draft,_=draft_env
    original_profile=identity.get_access_profile
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original_profile(name),'permissions':['ui.view','ui.execute','ui.edit']})
    body={k:copy.deepcopy(draft[k]) for k in ('title','preconditions','steps')}
    body.update(revision=draft['revision'],questions=[question])
    with actor_context(ACTORS['a']):
        saved=service.update_draft(ACTORS['a'],draft['draft_id'],body)
        reloaded=service.get_draft(ACTORS['a'],saved['draft_id'])
        assert reloaded['questions']==[question]
        gate=service.validate_draft(ACTORS['a'],reloaded['draft_id'],{'revision':reloaded['revision'],'target':TARGET})
        assert not gate['ready'] and any(question in item.get('message','') for item in gate['blockers'])
    with pytest.raises(ValueError,match='draft_gate_required'):
        trial(reloaded,'dialog-unreviewed')
    body.update(revision=reloaded['revision'],questions=[])
    with actor_context(ACTORS['a']):
        explicitly_cleared=service.update_draft(ACTORS['a'],reloaded['draft_id'],body)
    assert explicitly_cleared['questions']==[]
    assert trial(explicitly_cleared,'dialog-explicitly-cleared')['ui_batch_id']
