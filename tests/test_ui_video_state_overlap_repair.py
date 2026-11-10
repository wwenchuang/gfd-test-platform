"""Bounded re-observation must retain every conflicting hypothesis."""
import copy
import pytest
from task_server.services import ui_video_reconcile as r


def event(action, target, refs, question=None):
    times=[int(x[1:])*100 for x in refs]
    return {'action':action,'target':target,'value':'','frame_refs':refs,'start_ms':min(times),'end_ms':max(times),
            'observation':target+'可见','uncertainties':[question] if question else [],'evidence_kind':'inferred'}


def sample():
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(12)]
    tap=event('aiTap','按钮',['f5'])
    old=event('aiAssert','页面状态',['f4','f5'],'原状态待核对')
    new=event('aiWaitFor','加载状态',['f5'],'新状态待核对')
    return frames,tap,old,new


def reply(events,questions=(),multiple=False,attempts=1):
    return {'events':copy.deepcopy(events),'uncertainties':list(questions),'multiple_scenarios':multiple,
            'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':attempts}}


def test_overlap_reobserves_expanded_frames_and_keeps_both_hypotheses_and_questions(monkeypatch,tmp_path):
    frames,tap,old,new=sample();calls=[]
    def fake(original,local,group,*args,**kw):
        calls.append(([f['frame_id'] for f in group],kw['attempt_limit']))
        if len(calls)==1:return reply([tap,new],['首次局部问题'],True)
        return reply([old,tap,new],['第二次问题'])
    monkeypatch.setattr(r,'_repair_window',fake)
    result=r._repair_coverage([tap],{'events':[old,tap],'uncertainties':[],'multiple_scenarios':False},
                              frames,1200,tmp_path,lambda:False,[],{'f5'})
    assert len(calls)==2 and set(calls[0][0])<set(calls[1][0]) and calls[1][1]<=2
    assert {'首次局部问题','第二次问题'}<=set(result['uncertainties'])
    assert any('状态' in q and '核对' in q for q in result['uncertainties'])
    assert result['multiple_scenarios'] is True
    assert all(e in result['events'] for e in [old,tap,new])


def test_reobservation_cannot_drop_first_answer_event(monkeypatch,tmp_path):
    frames,tap,old,new=sample();calls=[]
    def fake(original,local,group,*args,**kw):
        calls.append(1)
        return reply([tap,new] if len(calls)==1 else [old,tap])
    monkeypatch.setattr(r,'_repair_window',fake)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        r._repair_coverage([tap],{'events':[old,tap],'uncertainties':[],'multiple_scenarios':False},frames,1200,tmp_path,lambda:False,[],{'f5'})
    assert len(calls)==2


@pytest.mark.parametrize('mutation',['question','observation','target','action','frame','time'])
def test_reobservation_must_preserve_exact_first_evidence(monkeypatch,tmp_path,mutation):
    frames,tap,old,new=sample();calls=[]
    def fake(original,local,group,*args,**kw):
        calls.append(1)
        if len(calls)==1:return reply([tap,new])
        changed=copy.deepcopy(new)
        if mutation=='question':changed['uncertainties']=[]
        if mutation=='observation':changed['observation']='另一结果'
        if mutation=='target':changed['target']='另一状态'
        if mutation=='action':changed['action']='aiAssert'
        if mutation=='frame':changed['frame_refs']=['f4'];changed['start_ms']=400;changed['end_ms']=400
        if mutation=='time':changed['start_ms']=400
        return reply([old,tap,changed])
    monkeypatch.setattr(r,'_repair_window',fake)
    with pytest.raises(ValueError):
        r._repair_coverage([tap],{'events':[old,tap],'uncertainties':[],'multiple_scenarios':False},frames,1200,tmp_path,lambda:False,[],{'f5'})
    assert len(calls)==2


def test_overlapping_first_events_must_remain_distinct():
    _,_,_,new=sample()
    assert not r._overlap_retained([new,new],[new])
    assert r._overlap_retained([new,new],[new,new])


def test_cancellation_before_reobservation_stops_model(monkeypatch,tmp_path):
    frames,tap,old,new=sample();calls=[]
    def fake(*args,**kw):calls.append(1);return reply([tap,new])
    monkeypatch.setattr(r,'_repair_window',fake)
    with pytest.raises(ValueError,match='video_cancelled'):
        r._repair_coverage([tap],{'events':[old,tap],'uncertainties':[],'multiple_scenarios':False},frames,1200,tmp_path,lambda:bool(calls),[],{'f5'})
    assert len(calls)==1


def test_failed_multigroup_replan_does_not_discard_first_diagnostic(monkeypatch,tmp_path):
    frames,tap,old,new=sample();later=event('aiTap','后续',['f10']);calls=[]
    def fake(*args,**kw):calls.append(1);return reply([tap,new])
    monkeypatch.setattr(r,'_repair_window',fake)
    with pytest.raises(ValueError,match='video_required_action_missing') as caught:
        r._repair_coverage([tap,later],{'events':[old,tap,later],'uncertainties':[],'multiple_scenarios':False},frames,1200,tmp_path,lambda:False,[],{'f5','f10'})
    assert len(calls)==2 and caught.value.state_overlap_evidence['group_count']==2


def test_first_answer_with_resolution_is_not_retried(monkeypatch,tmp_path):
    frames,tap,old,new=sample();calls=[]
    def fake(*args,**kw):
        calls.append(1);result=reply([tap,new]);result['reconciliation_audit']['coverage_resolutions']=[{'unverified':'claim'}];return result
    monkeypatch.setattr(r,'_repair_window',fake)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        r._repair_coverage([tap],{'events':[old,tap],'uncertainties':[],'multiple_scenarios':False},frames,1200,tmp_path,lambda:False,[],{'f5'})
    assert len(calls)==1


def test_oversized_conflict_closure_never_reobserves(monkeypatch,tmp_path):
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(60)]
    tap=event('aiTap','按钮',['f45']);old=event('aiAssert','长状态',['f0','f45']);new=event('aiWaitFor','新状态',['f45']);calls=[]
    def fake(*args,**kw):calls.append(1);return reply([tap,new])
    monkeypatch.setattr(r,'_repair_window',fake)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        r._repair_coverage([tap],{'events':[old,tap],'uncertainties':[],'multiple_scenarios':False},frames,6000,tmp_path,lambda:False,[],{'f45'})
    assert len(calls)==1


def test_real_repair_window_reobserves_both_states_with_same_shared_budget(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as details,ai_skill_service as ai
    frames,tap,old,new=sample();calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda frames,*a:[{'name':f['frame_id']} for f in frames])
    monkeypatch.setattr(details,'append_details',lambda *a,**kw:[])
    def model(skill,payload,**kw):
        calls.append(payload)
        current=[tap,new] if len(calls)==1 else [old,tap,new]
        return {'events':[{k:v for k,v in e.items() if k not in {'start_ms','end_ms','evidence_kind'}} for e in current],
                'uncertainties':['首次疑问'] if len(calls)==1 else [],'multiple_scenarios':False,'coverage_resolutions':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=r._repair_coverage([tap],{'events':[old,tap],'uncertainties':[],'multiple_scenarios':False},frames,1200,tmp_path,lambda:False,[],{'f5'})
    assert len(calls)==2
    assert {'f4','f5'}<=set(f['frame_id'] for f in calls[1]['frames'])
    assert '首次疑问' in result['uncertainties']
    assert any('局部状态证据存在重叠' in q for q in result['uncertainties'])
    assert result['reconciliation_audit']['state_overlap_first_repair']['events']


from test_ui_case_draft_execution import draft_env,trial
from test_ui_batch_dispatch import dispatch_env
from test_ui_execution_service import environment,ACTORS,TARGET


def test_overlap_review_blocks_real_draft_validation_and_trial(draft_env,monkeypatch,tmp_path):
    from task_server import identity
    from task_server.services import ui_video_analysis as analysis,ui_case_draft_service as service
    from task_server.services.operation_attribution import actor_context
    frames,tap,old,new=sample();calls=[]
    def fake(*args,**kwargs):
        calls.append(1)
        return reply([tap,new] if len(calls)==1 else [old,tap,new])
    monkeypatch.setattr(r,'_repair_window',fake)
    result=r._repair_coverage([tap],{'events':[old,tap],'uncertainties':[],'multiple_scenarios':False},frames,1200,tmp_path,lambda:False,[],{'f5'})
    doc={'source_id':'uvs_'+'a'*32,'app_id':'com.kfb.model','analysis_version':1,'goal':'',
         'media':{'frames':frames,'duration_ms':1200},**result}
    proposal,_=analysis.draft_proposal(doc)
    question=next(q for q in proposal['questions'] if '局部状态证据存在重叠' in q)
    draft,_=draft_env;original=identity.get_access_profile
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'permissions':['ui.view','ui.execute','ui.edit']})
    body={k:copy.deepcopy(draft[k]) for k in ('title','preconditions','steps')};body.update(revision=draft['revision'],questions=[question])
    with actor_context(ACTORS['a']):
        saved=service.update_draft(ACTORS['a'],draft['draft_id'],body)
        reloaded=service.get_draft(ACTORS['a'],saved['draft_id'])
        assert not service.validate_draft(ACTORS['a'],reloaded['draft_id'],{'revision':reloaded['revision'],'target':TARGET})['ready']
    with pytest.raises(ValueError,match='draft_gate_required'):trial(reloaded,'overlap-unreviewed')


def test_first_answer_order_cannot_reverse_at_same_timestamp():
    _,tap,_,new=sample()
    assert r._overlap_retained([tap,new],[new,tap]) is False


def test_closure_brackets_event_times_between_frames():
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(8)]
    e=event('aiAssert','状态',['f3']);e['start_ms']=250;e['end_ms']=350
    assert r._overlap_closure(frames,[frames[3]],[e],lambda:False)=={'f2','f3','f4'}


def test_second_answer_cannot_add_unobserved_interaction(monkeypatch,tmp_path):
    frames,tap,old,new=sample();calls=[]
    inserted=event('aiTap','未知点击',['f5'])
    def fake(*args,**kw):
        calls.append(1);return reply([tap,new] if len(calls)==1 else [old,tap,new,inserted])
    monkeypatch.setattr(r,'_repair_window',fake)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        r._repair_coverage([tap],{'events':[old,tap],'uncertainties':[],'multiple_scenarios':False},frames,1200,tmp_path,lambda:False,[],{'f5'})
    assert len(calls)==2


def test_first_answer_root_questions_redacted_again_after_new_secret(monkeypatch,tmp_path):
    frames,tap,old,new=sample();calls=[];secret='SENSITIVE-12345'
    def fake(original,local,group,*args,**kw):
        calls.append(1)
        if len(calls)==1:return reply([tap,new],['首次看到 '+secret])
        args[3].append(secret)
        return reply([old,tap,new])
    monkeypatch.setattr(r,'_repair_window',fake)
    result=r._repair_coverage([tap],{'events':[old,tap],'uncertainties':[],'multiple_scenarios':False},frames,1200,tmp_path,lambda:False,[],{'f5'})
    assert secret not in repr(result)


def test_real_repair_window_redacts_first_answer_after_second_identifies_input(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as details,ai_skill_service as ai
    frames,tap,old,new=sample();calls=[];secret='SENSITIVE-12345'
    typed=event('aiInput','输入框',['f5'])
    monkeypatch.setattr(windows,'load_assets',lambda frames,*a:[{'name':f['frame_id']} for f in frames])
    monkeypatch.setattr(details,'append_details',lambda *a,**kw:[])
    def model(skill,payload,**kw):
        calls.append(1)
        actual=copy.deepcopy(typed)
        if len(calls)>1:actual['value']=secret
        events=[tap,actual,new] if len(calls)==1 else [old,tap,actual,new]
        return {'events':[{k:v for k,v in e.items() if k not in {'start_ms','end_ms','evidence_kind'}} for e in events],
                'uncertainties':['首次看到 '+secret] if len(calls)==1 else [],'multiple_scenarios':False,'coverage_resolutions':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=r._repair_coverage([tap],{'events':[old,tap],'uncertainties':[],'multiple_scenarios':False},frames,1200,tmp_path,lambda:False,[],{'f5'})
    assert len(calls)==2 and secret not in repr(result)


@pytest.mark.parametrize('field,value',[
    ('selection','选择第一项'),('reusable_expected','页面显示所选对象'),
    ('outcome',{'observation':'结果可见','frame_refs':['f5']}),
])
def test_extra_business_evidence_cannot_be_omitted(field,value):
    _,tap,_,_=sample();old=copy.deepcopy(tap);old[field]=value
    assert not r._overlap_retained([old],[tap])


def test_one_prior_action_cannot_justify_two_overlapping_outputs(monkeypatch,tmp_path):
    frames,_,_,_=sample();tap=event('aiTap','按钮',['f4','f6']);old=event('aiAssert','状态',['f3','f6']);new=event('aiWaitFor','新状态',['f6']);extra=copy.deepcopy(tap);extra['frame_refs']=['f4','f5','f6'];calls=[]
    def fake(*args,**kw):
        calls.append(1)
        return reply([tap,new] if len(calls)==1 else [old,tap,extra,new])
    monkeypatch.setattr(r,'_repair_window',fake)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        r._repair_coverage([tap],{'events':[old,tap],'uncertainties':[],'multiple_scenarios':False},frames,1200,tmp_path,lambda:False,[],{'f4'})
    assert len(calls)==2


def test_real_window_rejects_duplicate_action_with_extra_frame(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as details,ai_skill_service as ai
    frames,_,_,_=sample();tap=event('aiTap','按钮',['f4','f6']);old=event('aiAssert','状态',['f3','f6']);new=event('aiWaitFor','新状态',['f6']);extra=copy.deepcopy(tap);extra['frame_refs']=['f4','f5','f6'];calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda frames,*a:[{'name':f['frame_id']} for f in frames])
    monkeypatch.setattr(details,'append_details',lambda *a,**kw:[])
    def model(skill,payload,**kw):
        calls.append(1);events=[tap,new] if len(calls)==1 else [old,tap,extra,new]
        return {'events':[{k:v for k,v in e.items() if k not in {'start_ms','end_ms','evidence_kind'}} for e in events],
                'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        r._repair_coverage([tap],{'events':[old,tap],'uncertainties':[],'multiple_scenarios':False},frames,1200,tmp_path,lambda:False,[],{'f4'})
    assert len(calls)==2
