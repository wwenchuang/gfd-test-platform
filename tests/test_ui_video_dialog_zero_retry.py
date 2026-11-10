"""Evidence-preserving re-observation of a single unbound probe."""
import copy,pytest
from task_server.services import ui_video_touch_targets as targets, ui_video_windows as windows, ui_video_touch_detail as detail
from task_server.workers.ui_video_worker import _private_target_failure
from test_ui_video_dialog_target_v7 import row,QUALIFIED

FRAMES=[{'frame_id':'frame-118','timestamp_ms':43000},{'frame_id':'frame-120','timestamp_ms':44900},{'frame_id':'frame-121','timestamp_ms':45000}]
HINTS=[{'frame_id':'frame-118','target':'关闭'},{'frame_id':'frame-121','target':'确认'}]

def run(monkeypatch,tmp_path,second,*,first=None,hints=None,questions=None,frames=None):
    frames=frames or FRAMES;hints=copy.deepcopy(HINTS if hints is None else hints);calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:copy.deepcopy(hints))
    def model(skill,payload,**kw):
        calls.append(copy.deepcopy(payload))
        if len(calls)>2:raise AssertionError('exceeded retry limit')
        ev=copy.deepcopy(first if len(calls)==1 and first is not None else ([row()] if len(calls)==1 else second))
        return {'events':ev,'uncertainties':copy.deepcopy(questions or []) if len(calls)==1 else [],'multiple_scenarios':len(calls)==1}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    try:return windows.observe_windows(frames,50000,tmp_path,lambda:False),calls
    except ValueError as error:return error,calls


def test_zero_match_retries_and_keeps_first_observations_for_human_review(monkeypatch,tmp_path):
    first=row(uncertainties=['首答问题'])
    result,calls=run(monkeypatch,tmp_path,[row('关闭',['frame-118']),first],first=[first],questions=['首答根问题'])
    assert not isinstance(result,ValueError) and len(calls)==2
    final,_=result
    assert [e['target'] for e in final['events']]==['关闭',QUALIFIED]
    assert '首答根问题' in final['uncertainties'] and final['multiple_scenarios'] is True
    assert '首答问题' in final['events'][1]['uncertainties']
    assert any('frame-118' in q and '核对' in q for q in final['uncertainties'])
    assert any('frame-118' in q for q in final['events'][0]['uncertainties'])
    assert calls[0]['touch_targets']==calls[1]['touch_targets']


@pytest.mark.parametrize('second',[
 [row('关闭',['frame-118'])],
 [row('关闭',['frame-118']),row('篡改弹窗的确认按钮')],
 [row('关闭',['frame-118']),row(),row('无关新增',['frame-120'])],
 [row()],
])
def test_retry_cannot_erase_or_invent_unrelated_event(monkeypatch,tmp_path,second):
    result,calls=run(monkeypatch,tmp_path,second)
    assert isinstance(result,ValueError) and len(calls)==2


def test_multiple_matches_fails_without_retry_and_has_safe_reason(monkeypatch,tmp_path):
    result,calls=run(monkeypatch,tmp_path,[],first=[row('关闭',['frame-118']),row('关闭',['frame-118']),row()])
    assert isinstance(result,ValueError) and len(calls)==1
    assert result.dialog_guard_evidence['reason']=='other_probe_match_count' and result.dialog_guard_evidence['match_count']==2
    assert '关闭' not in repr(result.dialog_guard_evidence)


def test_retry_does_not_run_if_root_question_budget_is_insufficient(monkeypatch,tmp_path):
    result,calls=run(monkeypatch,tmp_path,[],questions=[f'问题{i}' for i in range(29)])
    assert isinstance(result,ValueError) and len(calls)==1


def test_failure_diagnostic_accepts_only_known_safe_guard_fields():
    error=ValueError('video_model_invalid');error.dialog_guard_evidence={'reason':'other_probe_match_count','dialog_frame':'frame-121','probe_frame':'frame-118','match_count':0}
    doc={'media':{'frames':FRAMES}}
    assert _private_target_failure(error,'video_model_invalid',doc)['dialog_guard_evidence']==error.dialog_guard_evidence
    error.dialog_guard_evidence['target']='PRIVATE_TARGET'
    assert 'dialog_guard_evidence' not in _private_target_failure(error,'video_model_invalid',doc)


def test_other_probe_cannot_be_attached_to_an_existing_event(monkeypatch,tmp_path):
    frames=[{'frame_id':'frame-117','timestamp_ms':42900},*FRAMES]
    first=[row('关闭',['frame-117']),row()]
    second=[row('关闭',['frame-117','frame-118']),row()]
    result,calls=run(monkeypatch,tmp_path,second,first=first,frames=frames)
    assert isinstance(result,ValueError) and len(calls)==2


def test_two_unmatched_probes_cannot_be_repaired_as_one(monkeypatch,tmp_path):
    frames=[FRAMES[0],{'frame_id':'frame-119','timestamp_ms':44000},*FRAMES[1:]]
    hints=[{'frame_id':'frame-118','target':'关闭'},{'frame_id':'frame-119','target':'更多'},{'frame_id':'frame-121','target':'确认'}]
    result,calls=run(monkeypatch,tmp_path,[row('关闭',['frame-118']),row()],hints=hints,frames=frames)
    assert isinstance(result,ValueError) and len(calls)==2


def test_changed_observation_preserves_first_and_requires_explicit_review(monkeypatch,tmp_path):
    changed=row();changed['observation']='被更改的观察'
    result,calls=run(monkeypatch,tmp_path,[row('关闭',['frame-118']),changed])
    assert not isinstance(result,ValueError) and len(calls)==2
    assert result[0]['events'][1]['observation']=='弹窗关闭'
    assert any('两次画面描述不同' in q and '核对' in q for q in result[0]['events'][1]['uncertainties'])
    assert any('两次画面描述不同' in q for q in result[0]['uncertainties'])


def test_retry_merges_existing_event_questions_if_model_omits_them(monkeypatch,tmp_path):
    original=row(uncertainties=['首答事件问题'])
    result,calls=run(monkeypatch,tmp_path,[row('关闭',['frame-118']),row()],first=[original])
    assert not isinstance(result,ValueError) and len(calls)==2
    assert '首答事件问题' in result[0]['events'][1]['uncertainties']


def test_synthetic_missing_probe_among_four_original_video_touches(monkeypatch,tmp_path):
    # The timestamps are from v10; the probe assignment and model outputs are
    # synthetic because the failed response and probes were not persisted.
    stamps=[(105,41000),(106,41033),(107,41367),(108,41900),(109,42367),(110,42600),
            (112,43333),(113,43400),(114,43433),(119,44000),(120,44900),(121,45000)]
    frames=[{'frame_id':f'frame-{i:03}','timestamp_ms':t} for i,t in stamps]
    a=row('步骤一',['frame-105','frame-106','frame-107'])
    b=row('步骤二',['frame-108','frame-109','frame-110'])
    c=row('步骤三',['frame-112','frame-113','frame-114'])
    d=row(QUALIFIED,['frame-119','frame-120','frame-121'])
    hints=[{'frame_id':'frame-113','target':'步骤三'},{'frame_id':'frame-120','target':'确认'}]
    result,calls=run(monkeypatch,tmp_path,[a,b,c,d],first=[a,b,d],hints=hints,frames=frames)
    assert not isinstance(result,ValueError) and len(calls)==2
    events=result[0]['events'];assert [e['target'] for e in events]==['步骤一','步骤二','步骤三',QUALIFIED]
    assert any('frame-113' in q and '核对' in q for q in result[0]['uncertainties'])


from test_ui_case_draft_execution import draft_env,trial
from test_ui_batch_dispatch import dispatch_env
from test_ui_execution_service import environment,ACTORS,TARGET

@pytest.mark.parametrize('review_kind',['probe','observation'])
def test_probe_review_survives_pipeline_and_default_draft_try_gate(draft_env,monkeypatch,tmp_path,review_kind):
    from task_server import identity
    from task_server.services import ui_video_analysis as analysis,ui_video_reconcile as rec,ui_video_delayed_response as delayed,ui_case_draft_service as service
    from task_server.services.operation_attribution import actor_context
    dialog=row()
    if review_kind=='observation':dialog['observation']='重答观察不同'
    result,_=run(monkeypatch,tmp_path,[row('关闭',['frame-118']),dialog])
    marker='frame-118' if review_kind=='probe' else '两次画面描述不同'
    observed=result[0];q=next(q for q in observed['uncertainties'] if marker in q)
    second=copy.deepcopy(observed);second['uncertainties']=[]
    for e in second['events']:e['uncertainties']=[]
    monkeypatch.setattr(windows,'observe_windows',lambda *a,**kw:(copy.deepcopy(observed),[]))
    monkeypatch.setattr(rec,'needs_reconciliation',lambda *a:True)
    monkeypatch.setattr(rec,'reconcile',lambda *a:copy.deepcopy(second))
    monkeypatch.setattr(delayed,'review_pending_responses',lambda observed,*a:observed)
    monkeypatch.setattr(analysis,'consolidate_observed_frames',lambda document,root,cancelled,observed,*a:observed)
    doc={'source_id':'uvs_'+'a'*32,'app_id':'com.kfb.model','analysis_version':10,'goal':'',
         'media':{'frames':FRAMES,'duration_ms':50000}}
    final=analysis.analyze_whole_frames(doc,tmp_path,lambda:False)
    assert q in final['uncertainties']
    proposal,_=analysis.draft_proposal({**doc,**final});q=next(item for item in proposal['questions'] if q in item)
    draft,_=draft_env;original=identity.get_access_profile
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'permissions':['ui.view','ui.execute','ui.edit']})
    body={k:copy.deepcopy(draft[k]) for k in ('title','preconditions','steps')};body.update(revision=draft['revision'],questions=[q])
    with actor_context(ACTORS['a']):
        saved=service.update_draft(ACTORS['a'],draft['draft_id'],body)
        reloaded=service.get_draft(ACTORS['a'],saved['draft_id'])
        assert not service.validate_draft(ACTORS['a'],reloaded['draft_id'],{'revision':reloaded['revision'],'target':TARGET})['ready']
    with pytest.raises(ValueError,match='draft_gate_required'):trial(reloaded,'probe-unreviewed')


def test_context_only_unmatched_probe_never_retries(monkeypatch,tmp_path):
    monkeypatch.setattr(windows,'observation_windows',lambda frames:[FRAMES[1:],FRAMES[1:]])
    monkeypatch.setattr(windows,'temporal_context_frames',lambda window,frames:[FRAMES[0]])
    result,calls=run(monkeypatch,tmp_path,[],frames=FRAMES)
    assert isinstance(result,ValueError) and len(calls)==1


def test_new_secret_redaction_cannot_change_probe_during_retry(monkeypatch,tmp_path):
    hints=[{'frame_id':'frame-118','target':'PRIVATE_INPUT'},{'frame_id':'frame-121','target':'确认'}]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[]);monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:copy.deepcopy(hints));calls=[]
    def model(*a,**kw):
        calls.append(1)
        typed={'action':'aiInput','target':'字段','value':'PRIVATE_INPUT','frame_refs':['frame-118'],'observation':'输入','uncertainties':[]}
        return {'events':[typed,row()],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets.ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_invalid'):windows.observe_windows(FRAMES,50000,tmp_path,lambda:False)
    assert len(calls)==1


@pytest.mark.parametrize('case,reason',[
 ('nearby','nearby_dialog'),('multi_event','ambiguous_dialog'),('multi_hint','ambiguous_dialog'),
 ('unknown','probe_frame_unknown'),('zero','other_probe_match_count'),('multi_other','other_probe_match_count'),
 ('reuse','probe_event_reused'),('other_conflict','other_probe_conflict')])
def test_every_guard_rejection_has_only_validated_categorical_diagnostic(case,reason):
    events=[row()];hints=copy.deepcopy(HINTS)
    if case=='nearby':events=[row(refs=['frame-120'])];hints=[{'frame_id':'frame-121','target':'确认'}]
    elif case=='multi_event':events=[row(),row()];hints=[{'frame_id':'frame-121','target':'确认'}]
    elif case=='multi_hint':hints=[{'frame_id':'frame-121','target':'确认'},{'frame_id':'frame-121','target':'确认'}]
    elif case=='unknown':hints=[{'frame_id':'frame-999','target':'PRIVATE'},{'frame_id':'frame-121','target':'确认'}]
    elif case=='multi_other':events=[row('关闭',['frame-118']),row('关闭',['frame-118']),row()]
    elif case=='reuse':hints=[{'frame_id':'frame-120','target':QUALIFIED},{'frame_id':'frame-121','target':'确认'}]
    elif case=='other_conflict':events=[row('不同目标',['frame-118']),row()]
    with pytest.raises(ValueError) as caught:targets.guard_ambiguous_dialog_hints(events,hints,FRAMES)
    evidence=caught.value.dialog_guard_evidence
    assert evidence['reason']==reason and set(evidence)<={'reason','dialog_frame','probe_frame','match_count'}
    assert 'PRIVATE' not in repr(evidence) and QUALIFIED not in repr(evidence)
    public=_private_target_failure(caught.value,'video_model_invalid',{'media':{'frames':FRAMES}})
    assert public['dialog_guard_evidence']==evidence


def test_strict_retention_uses_complete_matching_not_greedy_order():
    old=[{'action':'aiTap','target':'按钮','value':'','frame_refs':['frame-118'],'start_ms':43000,'end_ms':43000,'observation':'A','uncertainties':[]},
         {'action':'aiTap','target':'按钮','value':'','frame_refs':['frame-118','frame-120'],'start_ms':43000,'end_ms':44900,'observation':'B','uncertainties':[]}]
    new=[copy.deepcopy(old[1]),copy.deepcopy(old[0])]
    mapping=windows._retained_dialog_events(old,new)
    assert mapping=={0:1,1:0}
    assert windows._retained_dialog_events(old,[new[0]]) is None


def test_retry_refuses_to_change_stored_outcome_or_target(monkeypatch,tmp_path):
    first=row();first['outcome']={'observation':'原有结果','frame_refs':['frame-121']}
    changed=copy.deepcopy(first);changed['outcome']['observation']='其他结果'
    result,calls=run(monkeypatch,tmp_path,[row('关闭',['frame-118']),changed],first=[first])
    assert isinstance(result,ValueError) and len(calls)==2
