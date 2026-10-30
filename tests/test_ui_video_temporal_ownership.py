"""Synthetic ordinary-projection constraints; no provider or source acceptance."""
import copy
import json
import pytest
from task_server.services import ui_video_trajectory as vt
from test_ui_video_trajectory import frames, event
from test_ui_video_audit_batches import Engine


def pair(conflict=True, optional=False):
    first=event(['frame-001','frame-006' if conflict else 'frame-003'])
    first.update(trigger_frame='frame-002')
    first['frame_refs'].insert(1,'frame-002')
    first['uncertainties']=['private event question '+('x'*1800)]
    if optional:first.update(action='aiWaitFor',trigger_kind='automatic')
    second=event(['frame-003','frame-004','frame-007'])
    second['trigger_frame']='frame-004'
    return [first,second]


def run(engine, **kw):
    return vt.observe_trajectory({'media':{'frames':frames(engine.count),'duration_ms':engine.count*100+1}},
        None,lambda:False,skill_runner=engine.model,record=lambda n,d:engine.records.append((n,copy.deepcopy(d))),
        business_timeline={'classification':'unverified_business_hypotheses'},**kw)


def test_business_corrects_before_audit_acquisition_and_preserves_questions(monkeypatch):
    attempts=[];acquired=[]
    def mutate(stage,p,raw,calls):
        if stage=='observe':
            attempts.append(copy.deepcopy(p));raw['events']=pair(len(attempts)==1)
            if len(attempts)==1:
                raw['uncertainties']=['private root question '+('y'*1800)]
                raw['revisit']=[{'first_frame':'frame-001','last_frame':'frame-003','question':'needs pixels'}]
    engine=Engine(monkeypatch,count=10,mutate=mutate)
    result=run(engine,revisit=lambda *a:(acquired.append(a) or []))
    assert len(attempts)==2 and not acquired
    assert [s for s,_,_ in engine.calls]==['overview','observe','observe','audit']
    assert 'ordinary_temporal_conflict' in attempts[1]['validation_feedback']
    assert 'private' not in attempts[1]['validation_feedback']
    constraints=attempts[1]['candidate_preservation_constraints']
    assert constraints['classification']=='unverified_candidate_preservation_constraints'
    assert len(constraints['signatures'])==2
    assert result['trajectory_audit']['corrections_used']==1
    assert len(result['events'])==2
    assert 'private root question '+('y'*1800) in result['uncertainties']
    assert 'private event question '+('x'*1800) in result['uncertainties']
    assert engine.records[3][1]['result']['events']==pair(True)


@pytest.mark.parametrize('repair',['repeat','delete','merge','change_kind'])
def test_invalid_correction_exhausts_original_budget_without_audit(monkeypatch,repair):
    attempts=[]
    def mutate(stage,p,raw,calls):
        if stage!='observe':return
        attempts.append(p);raw['events']=pair(True)
        if len(attempts)>1 and repair!='repeat':
            raw['events']=pair(False)
            if repair in {'delete','merge'}:raw['events']=raw['events'][:1]
            else:raw['events'][1]['trigger_kind']='pressed_control'
    engine=Engine(monkeypatch,count=10,mutate=mutate)
    with pytest.raises(ValueError,match='video_model_invalid'):run(engine)
    assert len(attempts)==3 and not engine.audits('packet-001')


def test_optional_conflict_is_derived_ledger_and_raw_observation_survives(monkeypatch):
    def mutate(stage,p,raw,calls):
        if stage=='observe':raw['events']=pair(True,optional=True)
    engine=Engine(monkeypatch,count=10,mutate=mutate);result=run(engine)
    assert len(result['events'])==2 and result['trajectory_audit']['corrections_used']==0
    ledger=result['trajectory_audit']['ordinary_export_conflicts']
    assert len(ledger)==1 and ledger[0]['required'] is False
    assert ledger[0]['interval']['upper_ms_inclusive']==300
    assert ledger[0]['next_interaction']['trigger']['timestamp_ms']==400
    assert ledger[0]['event']['fragments'][0]['fragment_id']=='packet-001:event-0:fragment-1'
    assert 'private' not in json.dumps(ledger)


def test_cross_packet_fails_without_current_correction_or_prior_mutation(monkeypatch):
    prior=[];diagnostics=[]
    def mutate(stage,p,raw,calls):
        if stage=='observe' and p['packet_id']=='packet-001':
            raw['events']=[event(['frame-030','frame-038'])]
            prior.append(copy.deepcopy(raw))
        if stage=='observe' and p['packet_id']=='packet-002':
            raw['events']=[event(['frame-033','frame-034'])]
    engine=Engine(monkeypatch,count=60,mutate=mutate)
    with pytest.raises(ValueError) as caught:run(engine,diagnostics=diagnostics)
    assert caught.value.trajectory_reason=='ordinary_temporal_cross_packet'
    conflict=caught.value.model_feedback[0]
    assert conflict['event']['packet_id']=='packet-001'
    assert conflict['next_interaction']['packet_id']=='packet-002'
    assert [s for s,p,_ in engine.calls if p.get('packet_id')=='packet-002']==['observe']
    assert diagnostics[-1]['corrections']==0
    assert next(d for n,d in engine.records if n.endswith('observe-result'))['result']==prior[0]


def test_pure_domain_shared_boundary_and_system_exclusion():
    from task_server.services.ui_video_temporal_ownership import ordinary_outcome_domain
    candidates=[{'action':'aiTap','start_ms':100,'end_ms':300},
                {'action':'aiTap','start_ms':300,'end_ms':500}]
    assert ordinary_outcome_domain(candidates,0,1000)=={
        'lower_ms_inclusive':300,'upper_ms_inclusive':300,'system_start_ms_exclusive':1001,'empty':False}


def test_domain_keeps_exclusive_system_boundary_and_no_input_mutation():
    from task_server.services.ui_video_temporal_ownership import ordinary_outcome_domain,ordinary_outcome_frames
    candidates=[{'action':'aiTap','start_ms':100,'end_ms':300},
                {'action':'aiAssert','target':'系统桌面','start_ms':900,'end_ms':900}]
    before=copy.deepcopy(candidates)
    assert ordinary_outcome_domain(candidates,0,1000)['system_start_ms_exclusive']==900
    assert ordinary_outcome_frames(candidates,0,frames(10),1000)==[f'frame-{i:03d}' for i in range(3,9)]
    assert candidates==before


def test_valid_business_continuation_folds_before_ownership(monkeypatch):
    def mutate(stage,p,raw,calls):
        if stage!='observe':return
        raw['events']=[]
        if p['packet_id']=='packet-001':raw['events']=[event(['frame-026','frame-032'],'aiInput')]
        if p['packet_id']=='packet-002':
            continuation=event(['frame-032','frame-033','frame-036'],'aiInput')
            continuation.update(trigger_frame='frame-033',continuation_of='packet-001:event-0')
            raw['events']=[continuation,event(['frame-040','frame-042'])]
    engine=Engine(monkeypatch,count=60,mutate=mutate);result=run(engine)
    assert len(result['events'])==2
    assert len(result['trajectory_audit']['tracks'][0]['fragments'])==2
    assert result['trajectory_audit']['ordinary_export_conflicts']==[]


def test_same_signature_multiplicity_preserved_across_temporal_corrections(monkeypatch):
    attempts=[]
    def mutate(stage,p,raw,calls):
        if stage!='observe':return
        attempts.append(p)
        first=event(['frame-001','frame-006'])
        duplicate=copy.deepcopy(first)
        raw['events']=[first,duplicate,event(['frame-003','frame-007'])]
        if len(attempts)>1:
            raw['events']=[event(['frame-001']),event(['frame-003','frame-007'])]
    engine=Engine(monkeypatch,count=10,mutate=mutate)
    with pytest.raises(ValueError) as caught:run(engine)
    assert caught.value.trajectory_reason=='ordinary_candidate_preservation'
    signature=attempts[1]['candidate_preservation_constraints']['signatures'][0]
    assert signature['multiplicity']==2 and len(attempts)==3


def test_planner_receives_exact_candidate_domain_without_private_ledger(monkeypatch):
    from task_server.services import ui_video_analysis as analysis
    from task_server.services.ui_video_temporal_ownership import ordinary_outcome_frames
    engine=Engine(monkeypatch,count=10)
    fs=frames(10);ps=vt.make_packets(fs,[])
    events,tracks=vt.bind_packet_events(pair(False),ps[0],fs,[],[])
    observed={'events':events,'multiple_scenarios':False,'uncertainties':[]}
    captured=[]
    def planner(skill,payload,**kw):
        captured.append(copy.deepcopy(payload));raise RuntimeError('capture only')
    with pytest.raises(RuntimeError,match='capture only'):
        analysis.consolidate_observed_frames({'app_id':'synthetic','goal':'generic','media':{'frames':fs,'duration_ms':1001}},
            None,lambda:False,observed,[],[],skill_runner=planner)
    candidate=captured[0]['candidate_events'][0]
    assert candidate['ordinary_outcome_frame_ids']==ordinary_outcome_frames(events,0,fs,1001)==['frame-003']
    assert candidate['ordinary_outcome_domain']['upper_ms_inclusive']==300


def test_empty_domain_and_wait_repairs_make_no_provider_call(monkeypatch,tmp_path):
    from task_server.services.ui_video_outcomes import repair_outcomes
    from test_ui_video_outcomes import fixture
    fs,candidates,plan=fixture(tmp_path)
    candidates[0]['end_ms']=600
    def forbidden(*a,**kw):pytest.fail('empty domain dispatch')
    with pytest.raises(ValueError,match='video_expected_missing'):
        repair_outcomes(plan,candidates,fs,800,tmp_path,[0],lambda:False,[],skill_runner=forbidden)
    candidates[0]['end_ms']=200;plan['events'][0]['action']='aiWaitFor'
    with pytest.raises(ValueError,match='video_expected_missing'):
        repair_outcomes(plan,candidates,fs,800,tmp_path,[0],lambda:False,[],skill_runner=forbidden)


def test_terminal_outside_nonempty_domain_gets_same_pre_audit_correction(monkeypatch):
    attempts=[]
    def mutate(stage,p,raw,calls):
        if stage!='observe':return
        attempts.append(p);raw['events']=pair(False)
        raw['events'][0]['frame_refs']=['frame-001','frame-002']
        raw['events'][0]['outcome']['frame_refs']=['frame-006' if len(attempts)==1 else 'frame-003']
    engine=Engine(monkeypatch,count=10,mutate=mutate);result=run(engine)
    assert result['trajectory_audit']['corrections_used']==1
    assert 'terminal_outside_ordinary_interval' in attempts[1]['validation_feedback']
    assert len(engine.audits('packet-001'))==1


def test_legacy_does_not_activate_mandatory_preflight(monkeypatch):
    def mutate(stage,p,raw,calls):
        if stage=='observe':raw['events']=pair(True)
    engine=Engine(monkeypatch,count=10,mutate=mutate);result=engine.run()
    assert len(result['events'])==2
    assert 'ordinary_export_conflicts' not in result['trajectory_audit']
    assert result['trajectory_audit']['corrections_used']==0


def test_rejected_envelope_keeps_full_questions_after_success(monkeypatch):
    attempts=[]
    def mutate(stage,p,raw,calls):
        if stage!='observe':return
        attempts.append(p);raw['events']=pair(False)
        if len(attempts)==1:
            raw['uncertainties']=['original schema-rejected root question']
            raw['unknown_field']=True
    engine=Engine(monkeypatch,count=10,mutate=mutate);result=run(engine)
    assert result['trajectory_audit']['corrections_used']==1
    assert 'original schema-rejected root question' in result['uncertainties']


@pytest.mark.parametrize('location',['root','event'])
@pytest.mark.parametrize('malformed',['z'*2001,'','   '],ids=['oversized','empty','whitespace'])
def test_malformed_rejected_question_stays_raw_without_poisoning_corrected_result(monkeypatch,location,malformed):
    attempts=[];original=[];valid='v'*2000
    def mutate(stage,p,raw,calls):
        if stage!='observe':return
        attempts.append(p);raw['events']=pair(False)
        if len(attempts)==1:
            questions=[malformed,valid]
            if location=='root':raw['uncertainties']=questions
            else:raw['events'][0]['uncertainties']=questions
            if location=='event' and malformed.isspace():raw['unknown_field']=True
            original.append(copy.deepcopy(raw))
    engine=Engine(monkeypatch,count=10,mutate=mutate);result=run(engine)
    assert len(attempts)==2 and result['trajectory_audit']['corrections_used']==1
    assert len(engine.audits('packet-001'))==1
    assert valid in result['uncertainties'] and malformed not in result['uncertainties']
    recorded=next(d['result'] for n,d in engine.records if n.endswith('observe-result'))
    assert recorded==original[0]
    raw_questions=recorded['uncertainties'] if location=='root' else recorded['events'][0]['uncertainties']
    assert raw_questions==[malformed,valid]


@pytest.mark.parametrize('verdict',['contradicted','unclear','outcome_only'])
def test_exact_audit_trigger_refutation_can_retire_temporal_signature(monkeypatch,verdict):
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    attempts=[];audits=[]
    def mutate(stage,p,raw,calls):
        if stage=='observe':
            attempts.append(copy.deepcopy(p));raw['events']=pair(len(attempts)==1)
            if len(attempts)==3:raw['events'][0]['trigger_frame']='frame-001'
        if stage=='audit':
            audits.append(copy.deepcopy(p))
            if len(audits)==1:
                check=next(c for c in raw['checks'] if c['event_id'].endswith('event-0:fragment-1'))
                check.update(trigger_verdict='supported' if verdict=='outcome_only' else verdict,
                             outcome_verdict='supported' if verdict=='contradicted' else 'contradicted',question='independent audit question')
    engine=Engine(monkeypatch,count=10,mutate=mutate)
    if verdict=='contradicted':
        result=run(engine,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1)
        assert result['trajectory_audit']['corrections_used']==2 and len(audits)==2
        assert result['trajectory_audit']['tracks'][0]['trigger_frame']=='frame-001'
        assert [s for s,_,_ in engine.calls]==['overview','observe','observe','audit','observe','audit']
        signatures=attempts[2]['candidate_preservation_constraints']['signatures']
        assert [s['trigger_frame'] for s in signatures]==['frame-004']
        assert len(attempts[2]['audit_feedback']['positive_trigger_locks'])==1
    else:
        with pytest.raises(ValueError) as caught:run(engine,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1)
        assert caught.value.trajectory_reason==('audit_positive_trigger_exhausted' if verdict=='outcome_only' else 'ordinary_candidate_preservation')
        assert len(audits)==1
    assert len(attempts)==3


def test_exact_refuted_fragment_retires_only_one_signature_multiplicity(monkeypatch):
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    attempts=[];audits=[]
    def mutate(stage,p,raw,calls):
        if stage=='observe':
            attempts.append(copy.deepcopy(p))
            first=event(['frame-001','frame-002','frame-006'] if len(attempts)==1 else ['frame-002'])
            first['trigger_frame']='frame-002'
            raw['events']=[first,event(['frame-002']),event(['frame-004','frame-007'])]
            if len(attempts)==3:
                raw['events'][0]=event(['frame-001'])
        if stage=='audit':
            audits.append(p)
            if len(audits)==1:
                check=next(c for c in raw['checks'] if c['event_id'].endswith('event-0:fragment-1'))
                check.update(trigger_verdict='contradicted',question='only first fragment refuted')
    engine=Engine(monkeypatch,count=10,mutate=mutate)
    result=run(engine,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1)
    assert len(audits)==2 and result['trajectory_audit']['corrections_used']==2
    constraints=attempts[2]['candidate_preservation_constraints']['signatures']
    same=next(s for s in constraints if s['trigger_frame']=='frame-002')
    assert same['multiplicity']==1
    assert len(result['events'])==3


def context_engine(monkeypatch,mode='safe',floor=3400):
    """Generic two packets; accepted response lies in current pre-context."""
    attempts=[];prior=[];acquired=[]
    terminal='frame-035' if floor==3500 else 'frame-036' if floor==3501 else 'frame-034'
    def mutate(stage,p,raw,calls):
        if stage=='observe' and p['packet_id']=='packet-001':
            raw['events']=[event(['frame-030',terminal],'aiInput')]
            raw['uncertainties']=['accepted prior question'];prior.append(copy.deepcopy(raw))
        if stage=='observe' and p['packet_id']=='packet-002':
            attempts.append(copy.deepcopy(p));fixed=len(attempts)>1 and mode!='repeat'
            raw['events']=[event(['frame-035','frame-038'] if fixed else ['frame-032','frame-035','frame-038'])]
            raw['events'][0]['trigger_frame']='frame-035'
            if len(attempts)==1:
                raw['uncertainties']=['rejected current root question']
                raw['events'][0]['uncertainties']=['rejected current event question']
                raw['revisit']=[dict(first_frame='frame-033',last_frame='frame-034',question='pixel question')]
            if fixed and mode=='change_trigger':raw['events'][0]['trigger_frame']='frame-038'
            if fixed and mode=='change_kind':raw['events'][0]['trigger_kind']='pressed_control'
            if fixed and mode=='delete':raw['events']=[]
    engine=Engine(monkeypatch,count=60,mutate=mutate)
    fs=frames(60);next(f for f in fs if f['frame_id']==terminal)['timestamp_ms']=floor
    fs.sort(key=lambda f:f['timestamp_ms'])
    def execute(**kw):
        return vt.observe_trajectory({'media':{'frames':fs,'duration_ms':6001}},None,lambda:False,
            skill_runner=engine.model,business_timeline={'classification':'unverified_business_hypotheses'},
            revisit=lambda *a:(acquired.append(a) or []),
            record=lambda n,d:engine.records.append((n,copy.deepcopy(d))),**kw)
    return engine,attempts,prior,acquired,execute


@pytest.mark.parametrize('floor',[3400,3500])
def test_safe_cross_packet_context_reobserves_only_current_with_immutable_history(monkeypatch,floor):
    engine,attempts,prior,acquired,execute=context_engine(monkeypatch,floor=floor)
    result=execute()
    assert len(attempts)==2 and not acquired and result['trajectory_audit']['corrections_used']==1
    assert [s for s,_,_ in engine.calls]==['overview','observe','audit','observe','observe','audit']
    assert result['events'][0]['end_ms']==floor
    assert result['events'][1]['start_ms']==3500
    assert result['trajectory_audit']['packets'][0]['raw']==prior[0]
    assert attempts[0]['prior_events']==attempts[1]['prior_events']
    assert 'rejected current root question' in result['uncertainties']
    assert 'rejected current event question' in result['uncertainties']
    assert 'accepted prior question' in result['uncertainties']
    assert result['trajectory_audit']['tracks'][0]['fragments']==prior[0]['events']
    accepted_audit=next(d['result'] for n,d in engine.records if n.endswith('audit-result'))
    assert result['trajectory_audit']['packets'][0]['audit']==accepted_audit
    assert 'rejected current' not in attempts[1]['validation_feedback']


@pytest.mark.parametrize('mode',['repeat','change_trigger','change_kind','delete'])
def test_eligible_cross_packet_context_still_exhausts_and_preserves_signatures(monkeypatch,mode):
    engine,attempts,prior,acquired,execute=context_engine(monkeypatch,mode=mode)
    with pytest.raises(ValueError) as caught:execute()
    assert len(attempts)==3 and not acquired
    assert not engine.audits('packet-002')
    assert caught.value.trajectory_reason==('ordinary_temporal_conflict' if mode=='repeat' else 'ordinary_candidate_preservation')


def test_one_unit_unsafe_immutable_floor_fails_before_spending(monkeypatch):
    engine,attempts,prior,acquired,execute=context_engine(monkeypatch,floor=3501)
    with pytest.raises(ValueError) as caught:execute()
    assert caught.value.trajectory_reason=='ordinary_temporal_cross_packet'
    assert len(attempts)==1 and not acquired and not engine.audits('packet-002')


@pytest.mark.parametrize('unsafe',[False,True])
def test_current_left_input_continuation_respects_immutable_prefix_floor(monkeypatch,unsafe):
    attempts=[];prior=[]
    def mutate(stage,p,raw,calls):
        if stage!='observe':return
        if p['packet_id']=='packet-001':
            raw['events']=[event(['frame-030','frame-038' if unsafe else 'frame-034'],'aiInput')]
            raw['events'][0]['uncertainties']=['accepted prefix question'];prior.append(copy.deepcopy(raw))
        else:
            attempts.append(p)
            continuation=event(['frame-034','frame-036','frame-040'],'aiInput')
            continuation.update(trigger_frame='frame-036',continuation_of='packet-001:event-0')
            tap=event(['frame-036','frame-037','frame-042'] if unsafe else
                      ['frame-041','frame-042'] if len(attempts)>1 else ['frame-039','frame-041','frame-042'])
            tap['trigger_frame']='frame-037' if unsafe else 'frame-041'
            raw['events']=[continuation,tap]
    engine=Engine(monkeypatch,count=60,mutate=mutate)
    if unsafe:
        with pytest.raises(ValueError) as caught:run(engine)
        assert caught.value.trajectory_reason=='ordinary_temporal_cross_packet' and len(attempts)==1
    else:
        result=run(engine)
        assert len(attempts)==2 and result['trajectory_audit']['corrections_used']==1
        fragments=result['trajectory_audit']['tracks'][0]['fragments']
        assert fragments[0]==prior[0]['events'][0] and len(fragments)==2
        assert 'accepted prefix question' in result['events'][0]['uncertainties']


def test_all_required_conflicts_classified_before_any_partial_correction(monkeypatch):
    attempts=[]
    def mutate(stage,p,raw,calls):
        if stage=='observe' and p['packet_id']=='packet-001':raw['events']=[event(['frame-030','frame-038'],'aiInput')]
        if stage=='observe' and p['packet_id']=='packet-002':
            attempts.append(p)
            raw['events']=[event(['frame-032','frame-035','frame-038']),event(['frame-036','frame-040','frame-042'])]
            raw['events'][0]['trigger_frame']='frame-035';raw['events'][1]['trigger_frame']='frame-040'
    engine=Engine(monkeypatch,count=60,mutate=mutate)
    with pytest.raises(ValueError) as caught:run(engine)
    assert caught.value.trajectory_reason=='ordinary_temporal_cross_packet' and len(attempts)==1
    assert len(caught.value.model_feedback)==2


def test_multiple_safe_required_conflicts_use_one_whole_current_correction(monkeypatch):
    attempts=[]
    def mutate(stage,p,raw,calls):
        if stage=='observe' and p['packet_id']=='packet-001':raw['events']=[event(['frame-030','frame-034'],'aiInput')]
        if stage=='observe' and p['packet_id']=='packet-002':
            attempts.append(p);fixed=len(attempts)>1
            raw['events']=[event(['frame-035','frame-038'] if fixed else ['frame-032','frame-035','frame-038']),
                           event(['frame-040','frame-042'] if fixed else ['frame-036','frame-040','frame-042'])]
            raw['events'][0]['trigger_frame']='frame-035';raw['events'][1]['trigger_frame']='frame-040'
    engine=Engine(monkeypatch,count=60,mutate=mutate);result=run(engine)
    assert len(result['events'])==3 and len(attempts)==2 and result['trajectory_audit']['corrections_used']==1


def test_system_conflict_is_immediate_even_for_current_only_left(monkeypatch):
    attempts=[]
    def mutate(stage,p,raw,calls):
        if stage!='observe':return
        attempts.append(p);raw['events']=[event(['frame-001','frame-010']),event(['frame-009'],'aiAssert')]
        raw['events'][1].update(target='系统桌面',trigger_kind='automatic')
    engine=Engine(monkeypatch,count=10,mutate=mutate)
    with pytest.raises(ValueError) as caught:run(engine)
    assert caught.value.trajectory_reason=='ordinary_temporal_cross_packet' and len(attempts)==1


def test_eligibility_refuses_historical_next_prefix_and_absent_next_without_mutation():
    from task_server.services.ui_video_temporal_ownership import unsafe_current_correction_conflicts
    engine_frames=frames(10)
    left={'event_id':'old-left','fragment_owner_ids':['old','current'],'last':{'timestamp_ms':500},'terminal':{'timestamp_ms':500}}
    nxt={'event_id':'old-next','packet_id':'old','fragment_owner_ids':['old','current'],'trigger':{'timestamp_ms':800}}
    conflict={'required':True,'event':left,'next_interaction':nxt,'interval':{'system_start_ms_exclusive':1001}}
    accepted=[{'end_ms':300,'outcome':{'frame_refs':['frame-003']}},{'end_ms':800,'outcome':{'frame_refs':['frame-008']}}]
    tracks=[{'event_id':'old-left'},{'event_id':'old-next'}];before=copy.deepcopy((conflict,accepted,tracks))
    assert unsafe_current_correction_conflicts([conflict],accepted,tracks,engine_frames,'current')==[conflict]
    assert (conflict,accepted,tracks)==before
    conflict['next_interaction']=None
    assert unsafe_current_correction_conflicts([conflict],accepted,tracks,engine_frames,'current')==[conflict]


@pytest.mark.parametrize('change_supported_target',[False,True])
def test_positive_current_lock_remains_authoritative_during_context_correction(monkeypatch,change_supported_target):
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    attempts=[];audits=[]
    def mutate(stage,p,raw,calls):
        if stage=='observe' and p['packet_id']=='packet-001':raw['events']=[event(['frame-030','frame-034'],'aiInput')]
        if stage=='observe' and p['packet_id']=='packet-002':
            attempts.append(p)
            raw['events']=[event(['frame-032','frame-035','frame-038'] if len(attempts)==2 else ['frame-035','frame-038'])]
            raw['events'][0]['trigger_frame']='frame-035'
            if len(attempts)==3 and change_supported_target:raw['events'][0]['target']='different candidate target'
        if stage=='audit' and p['packet_id']=='packet-002':
            audits.append(p)
            if len(audits)==1:raw['issues']=[dict(kind='missing_action',owner_frame='frame-035',frame_refs=['frame-035'],question='audit missing action question')]
    engine=Engine(monkeypatch,count=60,mutate=mutate)
    if change_supported_target:
        with pytest.raises(ValueError) as caught:run(engine,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1)
        assert caught.value.trajectory_reason=='audit_positive_trigger_exhausted' and len(audits)==1
    else:
        result=run(engine,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1)
        assert result['trajectory_audit']['corrections_used']==2 and len(audits)==2
    assert len(attempts)==3


def test_final_global_preflight_is_still_a_fail_closed_safety_net(monkeypatch):
    from task_server.services import ui_video_temporal_ownership as ownership
    engine,attempts,prior,acquired,execute=context_engine(monkeypatch)
    original=ownership.ordinary_export_conflicts
    def residual(events,tracks,fs,duration,packets):
        if engine.audits('packet-002'):
            copied=copy.deepcopy(events)
            copied[1]['start_ms']=3200
            return original(copied,tracks,fs,duration,packets)
        return original(events,tracks,fs,duration,packets)
    monkeypatch.setattr(ownership,'ordinary_export_conflicts',residual)
    with pytest.raises(ValueError) as caught:execute()
    assert caught.value.trajectory_reason=='ordinary_temporal_final'
    assert len(attempts)==2 and len(engine.audits('packet-002'))==1


@pytest.mark.parametrize('drop_duplicate',[False,True])
def test_cross_context_keeps_independent_signature_multiplicity(monkeypatch,drop_duplicate):
    attempts=[]
    def mutate(stage,p,raw,calls):
        if stage=='observe' and p['packet_id']=='packet-001':raw['events']=[event(['frame-030','frame-034'],'aiInput')]
        if stage=='observe' and p['packet_id']=='packet-002':
            attempts.append(p)
            first=event(['frame-032','frame-035','frame-038'] if len(attempts)==1 else ['frame-035'])
            first['trigger_frame']='frame-035'
            raw['events']=[first,event(['frame-035'])]
            if len(attempts)>1 and drop_duplicate:raw['events']=raw['events'][:1]
    engine=Engine(monkeypatch,count=60,mutate=mutate)
    if drop_duplicate:
        with pytest.raises(ValueError) as caught:run(engine)
        assert caught.value.trajectory_reason=='ordinary_candidate_preservation' and len(attempts)==3
    else:
        result=run(engine)
        assert len(result['events'])==3 and len(attempts)==2
    signatures=attempts[1]['candidate_preservation_constraints']['signatures']
    assert len(signatures)==1 and signatures[0]['multiplicity']==2
