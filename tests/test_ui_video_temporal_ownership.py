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
