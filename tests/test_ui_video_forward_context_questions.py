"""Generic forward-only unverified questions; never source acceptance."""
import copy
import importlib
import pytest
from task_server.services import ui_video_trajectory as vt, ui_video_runtime as runtime
from test_ui_video_trajectory import frames
from test_ui_video_audit_batches import Engine, partition


def helper():
    assert importlib.util.find_spec('task_server.services.ui_video_context_questions'), 'forward partition capability missing'
    return importlib.import_module('task_server.services.ui_video_context_questions')


def issue(owner='frame-033', kind='unresolved_evidence', question='Exact future evidence question'):
    return dict(kind=kind,owner_frame=owner,frame_refs=[owner],question=question)


def run(engine, **kw):
    return vt.observe_trajectory({'media':{'frames':frames(engine.count),'duration_ms':engine.count*100+1}},
        None,lambda:False,skill_runner=engine.model,audit_batcher=partition,
        record=lambda n,d:engine.records.append((n,copy.deepcopy(d))),
        business_timeline={},forward_context_issues=True,**kw)


def test_partition_exact_raw_and_current():
    h=helper();fs=frames(70);packets=vt.make_packets(fs,[]);before=copy.deepcopy(packets)
    ownership=h.freeze_ownership(fs,packets)
    raw=[issue(),issue('frame-002')];original=copy.deepcopy(raw)
    current,forward=h.partition_issues(raw,packets[0],ownership)
    assert current==[raw[1]] and forward==[dict(issue_index=0,target_packet_id='packet-002',issue=raw[0])]
    assert raw==original and packets==before
    with pytest.raises(ValueError):vt.validate_audit_issues(raw,packets[0])


@pytest.mark.parametrize('change',['past','unknown','unattached','missing_ref','blank','duplicate','forged','range','roles'])
def test_partition_rejects_invalid_metadata_or_issue(change):
    h=helper();fs=frames(100);packets=vt.make_packets(fs,[]);frozen=h.freeze_ownership(fs,packets)
    p=copy.deepcopy(packets[1]);items=[issue('frame-065')]
    if change=='past':items=[issue('frame-032')]
    if change=='unknown':items=[issue('unknown')]
    if change=='unattached':items=[issue('frame-099')]
    if change=='missing_ref':items[0]['frame_refs']=['frame-064']
    if change=='blank':items[0]['question']=' '
    if change=='duplicate':items[0]['frame_refs']*=2
    if change=='forged':p['owned_frame_ids'].append('frame-065');p['context_frame_ids'].remove('frame-065')
    if change=='range':p['owned_end_ms']=9000
    if change=='roles':p['context_frame_ids']=[]
    with pytest.raises(ValueError):h.partition_issues(items,p,frozen)


def test_delivery_supported_later_keeps_future_missing_action(monkeypatch):
    def mutate(stage,p,raw,calls):
        if stage=='audit' and p['packet_id']=='packet-001':raw['issues']=[issue(kind='missing_action')]
    engine=Engine(monkeypatch,count=70,mutate=mutate);result=run(engine)
    assert result['uncertainties']==[issue()['question']]
    assert result['trajectory_audit']['corrections_used']==0
    ledger=result['trajectory_audit']['forward_context_questions'];assert len(ledger)==1
    row=ledger[0];assert row['issue']==issue(kind='missing_action')
    assert row['classification']=='unverified_forward_context_question' and row['instruction_authority'] is False
    assert row['observer_calls'] and row['audit_calls']
    for stage,p,_ in engine.calls:
        if p.get('packet_id')=='packet-002' and stage in {'observe','audit'}:
            assert p['forward_context_questions'][0]['issue']==issue(kind='missing_action')
    assert next(d for n,d in engine.records if n.endswith('audit-result'))['result']['issues']==[issue(kind='missing_action')]


def test_runtime_schema_future_domain_explicit_only():
    p=vt.make_packets(frames(70),[])[0];p={**p,'owned_time_range_ms':[p['owned_start_ms'],p['owned_end_ms']],'trajectory':[]}
    before=copy.deepcopy(p)
    legacy=runtime._audit_schema(p)['properties']['issues']['items']['properties']['owner_frame']['enum']
    enabled=runtime._audit_schema(p,forward_context_issues=True)['properties']['issues']['items']['properties']['owner_frame']['enum']
    assert legacy==p['owned_frame_ids'] and enabled==legacy+p['context_frame_ids']
    assert p==before


@pytest.mark.parametrize('value',[1,None,'true',[],{}])
def test_bad_capability_before_calls(monkeypatch,value):
    engine=Engine(monkeypatch,count=70)
    with pytest.raises(ValueError,match='video_forward_context_invalid'):
        vt.observe_trajectory({},None,lambda:False,forward_context_issues=value)
    assert not engine.calls


def test_enabled_requires_business_timeline():
    with pytest.raises(ValueError,match='video_forward_context_invalid'):
        vt.observe_trajectory({},None,lambda:False,forward_context_issues=True)


def test_legacy_still_rejects_future_and_enabled_empty_target_audited(monkeypatch):
    def mutate(stage,p,raw,calls):
        if stage=='audit' and p['packet_id']=='packet-001':raw['issues']=[issue()]
        if stage=='observe' and p['packet_id']=='packet-002':raw['events']=[]
    engine=Engine(monkeypatch,count=70,mutate=mutate)
    with pytest.raises(ValueError) as caught:engine.run()
    assert caught.value.trajectory_reason=='audit_issue_owner'
    engine=Engine(monkeypatch,count=70,mutate=mutate);result=run(engine)
    assert engine.audits() and engine.audits()[0][0]['trajectory']==[]
    assert result['uncertainties']==[issue()['question']]


def test_current_repair_feedback_excludes_forward_and_locks_survive(monkeypatch):
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    seen=[]
    def mutate(stage,p,raw,calls):
        if stage=='audit' and p['packet_id']=='packet-001' and not seen:
            raw['issues']=[issue(kind='missing_action'),issue('frame-002','missing_action','current question')];seen.append(True)
    engine=Engine(monkeypatch,count=70,mutate=mutate);result=run(engine,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1)
    feedback=result['trajectory_audit']['repair_feedback'][0]
    assert feedback['issues']==[issue('frame-002','missing_action','current question')]
    assert feedback['positive_trigger_locks'] and result['trajectory_audit']['corrections_used']==1
    assert set(result['uncertainties'])=={issue()['question'],'current question'}


def test_handoff_retained_before_acquisition_replaces_audit(monkeypatch):
    requested=[]
    def mutate(stage,p,raw,calls):
        if stage=='audit' and p['packet_id']=='packet-001' and not requested:
            requested.append(True);raw['issues']=[issue()]
            raw['revisit']=[dict(first_frame='frame-001',last_frame='frame-002',question='acquire evidence')]
    engine=Engine(monkeypatch,count=70,mutate=mutate)
    def acquire(*args,**kw):return [{'frame_id':'new-original','timestamp_ms':150}]
    result=run(engine,revisit=acquire)
    assert len(result['trajectory_audit']['forward_context_questions'])==1
    assert issue()['question'] in result['uncertainties']
    assert len(engine.audits('packet-001'))==2
    assert result['trajectory_audit']['packets'][0]['audit']['issues']==[]


def test_no_new_frames_future_missing_does_not_repair_current(monkeypatch):
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    requested=[]
    def mutate(stage,p,raw,calls):
        if stage=='audit' and p['packet_id']=='packet-001' and not requested:
            requested.append(True);raw['issues']=[issue(kind='missing_action')]
            raw['revisit']=[dict(first_frame='frame-001',last_frame='frame-002',question='acquire evidence')]
    engine=Engine(monkeypatch,count=70,mutate=mutate)
    result=run(engine,revisit=lambda *a:[],execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1)
    assert result['trajectory_audit']['corrections_used']==0 and len(engine.audits('packet-001'))==2
    assert issue()['question'] in result['uncertainties']


def test_invalid_checks_cannot_admit_forward_and_failed_target_keeps_origin(monkeypatch):
    diagnostics=[];origins=[]
    def mutate(stage,p,raw,calls):
        if stage=='audit' and p['packet_id']=='packet-001':raw['issues']=[issue()]
        if stage=='observe' and p['packet_id']=='packet-002':raise ValueError('video_cancelled')
    engine=Engine(monkeypatch,count=70,mutate=mutate)
    model=engine.model
    def traced(skill,p,**kw):
        kw['runtime_trace']['video_record_refs']={'proof_ref':{'path':'generic-proof'},'receipt_ref':{'path':'generic-receipt'}}
        return model(skill,p,**kw)
    engine.model=traced
    with pytest.raises(ValueError,match='video_cancelled'):run(engine,diagnostics=diagnostics)
    diagnostic=next(d for d in diagnostics if d['kind']=='forward_context_questions')
    row=diagnostic['findings'][0]
    assert row['target_packet_id']=='packet-002' and row['issue_index']==0 and row['audit_call']==3
    assert row['origin_refs']['receipt_ref']=={'path':'generic-receipt'} and diagnostic['status']=='pending'
    assert 'Exact future' not in str(row)
    def bad(stage,p,raw,calls):
        if stage=='audit':raw['issues']=[issue()];raw['checks']=[]
    engine=Engine(monkeypatch,count=70,mutate=bad);diagnostics=[]
    with pytest.raises(ValueError):run(engine,diagnostics=diagnostics)
    assert not any(d['kind']=='forward_context_questions' for d in diagnostics)


def test_shards_retain_each_occurrence_and_deliver_to_later_owner(monkeypatch):
    def mutate(stage,p,raw,calls):
        if stage=='audit' and p['packet_id']=='packet-002':raw['issues']=[issue('frame-065')]
    engine=Engine(monkeypatch,count=90,mutate=mutate);result=run(engine)
    ledger=result['trajectory_audit']['forward_context_questions']
    assert len(ledger)==2 and ledger[0]['audit_call']!=ledger[1]['audit_call']
    assert all(row['observer_calls'] and row['audit_calls'] for row in ledger)
    assert result['uncertainties']==[issue()['question']]


def test_final_128_question_cap_still_fails_closed(monkeypatch):
    diagnostics=[]
    def mutate(stage,p,raw,calls):
        if stage=='audit' and p['context_frame_ids']:
            future=[f['frame_id'] for f in p['frames'] if f['timestamp_ms']>=p['owned_time_range_ms'][1]]
            if future:raw['issues']=[issue(future[0],question=f"question {p['packet_id']} {i}") for i in range(30)]
    engine=Engine(monkeypatch,count=180,mutate=mutate)
    with pytest.raises(ValueError) as caught:run(engine,diagnostics=diagnostics)
    assert caught.value.analysis_reason=='root_question_limit' and caught.value.question_limit==128
    assert any(d.get('reason')=='trajectory_questions' and d['question_count']>128 for d in diagnostics)


def test_frozen_source_rejects_duplicate_originals_and_unknown_context():
    h=helper();fs=frames(70);ps=vt.make_packets(fs,[])
    with pytest.raises(ValueError):h.freeze_ownership(fs+[fs[0]],ps)
    frozen=h.freeze_ownership(fs,ps);p=copy.deepcopy(ps[0])
    p['frames'].append(dict(frame_id='unknown-context',timestamp_ms=9999))
    p['context_frame_ids'].append('unknown-context')
    with pytest.raises(ValueError):h.partition_issues([],p,frozen)


@pytest.mark.parametrize('change',['count','question','refs','kind','order'])
def test_unchanged_issue_bounds(change):
    h=helper();fs=frames(70);ps=vt.make_packets(fs,[]);frozen=h.freeze_ownership(fs,ps)
    items=[issue()]
    if change=='count':items*=31
    if change=='question':items[0]['question']='q'*2001
    if change=='refs':items[0]['frame_refs']=[f'frame-{i:03d}' for i in range(25,34)]
    if change=='kind':items[0]['kind']='confirmed'
    if change=='order':items[0]['frame_refs']=['frame-034','frame-033']
    with pytest.raises(ValueError):h.partition_issues(items,ps[0],frozen)


def test_enabled_current_no_new_missing_action_still_repairs(monkeypatch):
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    seen=[]
    def mutate(stage,p,raw,calls):
        if stage=='audit' and p['packet_id']=='packet-001' and not seen:
            seen.append(True);raw['issues']=[issue('frame-002','missing_action','current question'),issue()]
            raw['revisit']=[dict(first_frame='frame-001',last_frame='frame-002',question='need evidence')]
    engine=Engine(monkeypatch,count=70,mutate=mutate)
    result=run(engine,revisit=lambda *a:[],execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1)
    assert result['trajectory_audit']['corrections_used']==1
    assert result['trajectory_audit']['repair_feedback'][0]['issues']==[issue('frame-002','missing_action','current question')]
    assert {'current question',issue()['question'],'need evidence'}<=set(result['uncertainties'])


def test_conflicting_half_open_cuts_fail_closed():
    h=helper();p=vt.make_packets(frames(70),[])[0]
    p['owned_time_range_ms']=[p['owned_start_ms'],p['owned_end_ms']+1]
    with pytest.raises(ValueError):h.audit_domains(p)


def test_frozen_packet_timestamp_must_match_original_source():
    h=helper();fs=frames(70);ps=vt.make_packets(fs,[])
    ps[0]['frames']=copy.deepcopy(ps[0]['frames']);ps[0]['frames'][1]['timestamp_ms']+=1
    with pytest.raises(ValueError):h.freeze_ownership(fs,ps)
