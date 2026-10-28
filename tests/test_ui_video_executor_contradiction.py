"""Validated contradiction repair retains the exact supported business candidates."""
import copy
import json

import pytest

from task_server.services import ui_video_trajectory as vt
from test_ui_video_executor_repair import Session, CONTRACT, issue
from test_ui_video_trajectory import event, frames


def contradiction(raw, *, trigger='supported'):
    raw['checks'][0].update(trigger_verdict=trigger,outcome_verdict='contradicted',question='原图不支持当前结果谓词')


def test_supported_trigger_contradicted_outcome_reobserves_with_actual_claim_and_check_data(monkeypatch):
    original=[]
    def mutate(stage,payload,raw,s):
        if stage=='observe' and not payload.get('audit_feedback'):
            raw['events'][0]['outcome']['observation']='WRONG_OUTCOME_PREDICATE'
        if stage=='audit' and not original:
            contradiction(raw);original.append((copy.deepcopy(payload['trajectory']),copy.deepcopy(raw)))
        if stage=='observe' and payload.get('audit_feedback'):
            feedback=payload['audit_feedback']
            assert feedback['classification']=='unverified_audit_finding_data' and feedback['instruction_authority'] is False
            assert feedback['checks']==original[0][1]['checks']
            assert feedback['issues']==[] and feedback['audited_claims']==original[0][0]
            assert feedback['positive_trigger_locks']==[{k:original[0][0][0]['track'][k] for k in
                ('action','target','value','trigger_frame','trigger_kind','continuation_of')}]
            assert 'WRONG_OUTCOME_PREDICATE' in json.dumps(feedback)
            raw['events'][0]['outcome']['observation']='REOBSERVED_OUTCOME_PREDICATE'
    s=Session(monkeypatch,mutate,count=6);result=s.run()
    assert [v for v,p in s.calls]==['overview','observe','audit','observe','audit']
    assert result['trajectory_audit']['corrections_used']==1
    assert result['events'][0]['outcome']['observation']=='REOBSERVED_OUTCOME_PREDICATE'
    assert result['trajectory_audit']['calls'][2]['raw_response']==original[0][1]


@pytest.mark.parametrize('field',['trigger_verdict','outcome_verdict'])
def test_persistent_valid_contradiction_exhausts_same_two_corrections(monkeypatch,field):
    def mutate(stage,payload,raw,s):
        if stage=='audit':raw['checks'][0].update({field:'contradicted','question':'实际反证'})
    s=Session(monkeypatch,mutate,count=6)
    with pytest.raises(ValueError) as exc:s.run()
    assert exc.value.trajectory_reason=='audit_contradiction_exhausted'
    assert [v for v,p in s.calls]==['overview','observe','audit','observe','audit','observe','audit']
    assert not any(n=='trajectory-final' for n,_ in s.records)


@pytest.mark.parametrize('change',['delete','target','value','action','trigger_frame','trigger_kind','continuation_of'])
def test_supported_business_trigger_fixed_fields_cannot_change_during_outcome_repair(monkeypatch,change):
    def mutate(stage,payload,raw,s):
        if stage=='observe':
            if payload['packet_id']=='packet-001':
                e=event(['frame-026','frame-032'],'aiInput');raw['events']=[e]
            if payload['packet_id']=='packet-002':
                e=event(['frame-033','frame-034'],'aiTap' if change=='trigger_kind' else 'aiInput');raw['events']=[e]
                if payload.get('audit_feedback'):
                    if change=='delete':raw['events']=[]
                    if change=='target':e['target']='另一个输入目标'
                    if change=='value':e.update(action='aiScroll',value={'direction':'up','distance':200},trigger_kind='touch')
                    if change=='action':e.update(action='aiTap',trigger_kind='touch')
                    if change=='trigger_frame':e['trigger_frame']='frame-034'
                    if change=='trigger_kind':e['trigger_kind']='navigation'
                    if change=='continuation_of':e['continuation_of']='packet-001:event-0'
        if stage=='audit' and payload['packet_id']=='packet-002':contradiction(raw)
    s=Session(monkeypatch,mutate)
    with pytest.raises(ValueError) as exc:s.run()
    assert exc.value.trajectory_reason=='audit_positive_trigger_exhausted'
    second=[v for v,p in s.calls if p.get('packet_id')=='packet-002']
    assert second==['observe','audit','observe','observe']
    assert not any(n=='trajectory-final' for n,_ in s.records)


def test_duplicate_positive_candidates_cannot_be_collapsed(monkeypatch):
    def mutate(stage,payload,raw,s):
        if stage=='observe':
            candidate=event(['frame-001','frame-002'])
            raw['events']=[candidate] if payload.get('audit_feedback') else [candidate,copy.deepcopy(candidate)]
        if stage=='audit':contradiction(raw)
    s=Session(monkeypatch,mutate,count=6)
    with pytest.raises(ValueError) as exc:s.run()
    assert exc.value.trajectory_reason=='audit_positive_trigger_exhausted'
    assert [v for v,p in s.calls]==['overview','observe','audit','observe','observe']


def test_typed_scroll_value_lock_is_exact_and_cannot_use_prior_fragment_as_replacement(monkeypatch):
    def mutate(stage,payload,raw,s):
        if stage=='observe':
            raw['events']=[event(['frame-033','frame-034'],'aiScroll')] if payload['packet_id']=='packet-002' else [event(['frame-001','frame-002'],'aiScroll')]
            for e in raw['events']:e['value']={'direction':'up','scrollType':'singleAction','distance':200}
            if payload.get('audit_feedback'):raw['events'][0]['value']['distance']=201
        if stage=='audit' and payload['packet_id']=='packet-002':contradiction(raw)
    s=Session(monkeypatch,mutate,count=64)
    with pytest.raises(ValueError) as exc:s.run()
    assert exc.value.trajectory_reason=='audit_positive_trigger_exhausted'


def test_earlier_inserted_action_shifts_event_ids_without_breaking_source_lock(monkeypatch):
    def mutate(stage,payload,raw,s):
        if stage=='observe':
            raw['events']=[event(['frame-003','frame-004'])]
            if payload.get('audit_feedback'):raw['events'].insert(0,event(['frame-001','frame-002']))
        if stage=='audit' and sum(v=='audit' for v,p in s.calls)==1:contradiction(raw)
    s=Session(monkeypatch,mutate,count=6);result=s.run()
    assert [v for v,p in s.calls]==['overview','observe','audit','observe','audit']
    assert result['trajectory_audit']['checks'][1]['event_id']=='packet-001:event-1'
    assert result['trajectory_audit']['tracks'][1]['trigger_frame']=='frame-003'


def test_contradicted_trigger_candidate_can_be_refuted_and_replaced(monkeypatch):
    def mutate(stage,payload,raw,s):
        if stage=='observe' and payload.get('audit_feedback'):
            raw['events']=[event(['frame-003','frame-004'])];raw['events'][0]['target']='重新观察的目标'
        if stage=='audit' and sum(v=='audit' for v,p in s.calls)==1:contradiction(raw,trigger='contradicted')
    s=Session(monkeypatch,mutate,count=6);result=s.run()
    assert len(s.calls)==5 and result['events'][0]['target']=='重新观察的目标'
    assert result['trajectory_audit']['repair_feedback'][0]['positive_trigger_locks']==[]


def test_late_shard_contradiction_reruns_all_shards_and_locks_earlier_supported_candidates(monkeypatch):
    contradicted=[]
    def mutate(stage,payload,raw,s):
        if stage=='audit' and payload['packet_id']=='packet-002' and len(payload['trajectory'])==1 and not contradicted:
            contradiction(raw);contradicted.append(True)
        if stage=='observe' and payload.get('audit_feedback'):
            assert len(payload['audit_feedback']['positive_trigger_locks'])==7
            assert len(payload['audit_feedback']['checks'])==7
            assert len(payload['audit_feedback']['audited_claims'])==7
    s=Session(monkeypatch,mutate);result=s.run()
    assert [v for v,p in s.calls if p.get('packet_id')=='packet-002']==['observe','audit','audit','observe','audit','audit']
    assert result['trajectory_audit']['corrections_used']==1


def test_combined_missing_action_and_contradiction_spend_only_one_correction(monkeypatch):
    def mutate(stage,payload,raw,s):
        if stage=='audit' and sum(v=='audit' for v,p in s.calls)==1:
            contradiction(raw);raw['issues']=[issue(owner='frame-001')]
    s=Session(monkeypatch,mutate,count=6);result=s.run()
    assert len(s.calls)==5 and result['trajectory_audit']['corrections_used']==1


@pytest.mark.parametrize('malformed',['owner','check_identity','verdict'])
def test_invalid_negative_audit_never_reaches_repair_feedback(monkeypatch,malformed):
    def mutate(stage,payload,raw,s):
        if stage=='audit':
            contradiction(raw)
            if malformed=='owner':raw['issues']=[issue(owner='frame-090')]
            if malformed=='check_identity':raw['checks'][0]['event_id']='unknown'
            if malformed=='verdict':raw['checks'][0]['outcome_verdict']='negative'
    s=Session(monkeypatch,mutate,count=6)
    with pytest.raises(ValueError):s.run()
    assert not any(p.get('audit_feedback') for v,p in s.calls)
    assert len(s.calls)==5


def test_legacy_contradiction_stays_review_blocking_without_reobservation(monkeypatch):
    def mutate(stage,payload,raw,s):
        if stage=='audit':contradiction(raw)
    s=Session(monkeypatch,mutate,count=6,mode=None);result=s.run()
    assert len(s.calls)==3 and result['trajectory_audit']['checks'][0]['status']=='needs_review'


def test_guard_rejection_uses_remaining_shared_pool_then_can_recover(monkeypatch):
    observations=0
    def mutate(stage,payload,raw,s):
        nonlocal observations
        if stage=='observe':
            observations+=1
            if observations==2:raw['events'][0]['target']='被守卫拒绝的目标'
        if stage=='audit' and sum(v=='audit' for v,p in s.calls)==1:contradiction(raw)
    s=Session(monkeypatch,mutate,count=6);result=s.run()
    assert result['trajectory_audit']['corrections_used']==2
    assert [v for v,p in s.calls]==['overview','observe','audit','observe','observe','audit']
    assert 'audit_positive_trigger_changed' in s.calls[-2][1]['validation_feedback']


def test_contradiction_repairs_do_not_allow_twenty_fifth_dispatch(monkeypatch):
    def mutate(stage,payload,raw,s):
        if stage=='audit' and sum(v=='audit' for v,p in s.calls)==1:contradiction(raw)
    s=Session(monkeypatch,mutate,count=300,batcher=lambda cs:[[c] for c in cs] if cs else [[]])
    with pytest.raises(ValueError,match='video_vision_limit'):s.run()
    assert len(s.calls)==24


def test_outcome_references_and_page_after_can_change_without_changing_supported_trigger(monkeypatch):
    def mutate(stage,payload,raw,s):
        if stage=='observe' and payload.get('audit_feedback'):
            raw['events'][0].update(frame_refs=['frame-001','frame-003'],page_after='new-visible-page',
                                    outcome={'frame_refs':['frame-003'],'observation':'重新观察的终态'})
        if stage=='audit' and sum(v=='audit' for v,p in s.calls)==1:contradiction(raw)
    s=Session(monkeypatch,mutate,count=6);result=s.run()
    assert len(s.calls)==5
    assert result['trajectory_audit']['tracks'][0]['page_after']=='new-visible-page'
    assert result['events'][0]['outcome']['frame_refs']==['frame-003']


def test_model_cannot_rewrite_feedback_locks_or_original_actual_history(monkeypatch):
    original=[]
    def mutate(stage,payload,raw,s):
        if stage=='audit' and not original:
            contradiction(raw);original.append(copy.deepcopy(raw))
        if stage=='observe' and payload.get('audit_feedback'):
            payload['audit_feedback']['positive_trigger_locks'][0]['target']='伪造的保留目标'
            payload['audit_feedback']['audited_claims'][0]['track']['target']='伪造的来源目标'
            raw['events'][0]['target']='伪造的保留目标'
    s=Session(monkeypatch,mutate,count=6)
    with pytest.raises(ValueError) as exc:s.run()
    assert exc.value.trajectory_reason=='audit_positive_trigger_exhausted'
    audit_records=[value['result'] for name,value in s.records if name.endswith('audit-result')]
    assert audit_records==original
    feedback=next(value for name,value in s.records if name.endswith('audit-repair-feedback'))
    assert feedback['positive_trigger_locks'][0]['target']=='进入按钮'
    assert feedback['audited_claims'][0]['track']['target']=='进入按钮'


def test_past_fragments_cannot_satisfy_current_owned_locks_and_guard_is_pure():
    fs=frames(70);packets=vt.make_packets(fs,[])
    raw=event(['frame-001','frame-002'])
    _,past=vt.bind_packet_events([raw],packets[0],fs,[],[])
    claim={'track':{**copy.deepcopy(raw),'event_id':'claim-source-1'}}
    checks=[dict(event_id='claim-source-1',trigger_verdict='supported',outcome_verdict='contradicted',question='bad outcome')]
    locks=vt._supported_trigger_locks([claim],checks);before=copy.deepcopy((locks,past))
    with pytest.raises(ValueError) as exc:vt._validate_positive_trigger_locks(locks,past,packets[1])
    assert exc.value.trajectory_reason=='audit_positive_trigger_changed'
    assert (locks,past)==before


def test_shape_then_contradiction_uses_only_remaining_single_correction(monkeypatch):
    def mutate(stage,payload,raw,s):
        if stage=='observe' and sum(v=='observe' for v,p in s.calls)==1:raw['checks']=[{}]
        if stage=='audit':contradiction(raw)
    s=Session(monkeypatch,mutate,count=6)
    with pytest.raises(ValueError) as exc:s.run()
    assert exc.value.trajectory_reason=='audit_contradiction_exhausted'
    assert [v for v,p in s.calls]==['overview','observe','observe','audit','observe','audit']
    assert len([n for n,_ in s.records if n.endswith('audit-repair-feedback')])==1


def test_unclear_trigger_is_unlocked_but_remains_review_blocking_after_fresh_audit(monkeypatch):
    def mutate(stage,payload,raw,s):
        if stage=='audit':
            raw['checks'][0].update(trigger_verdict='unclear',outcome_verdict='contradicted' if sum(v=='audit' for v,p in s.calls)==1 else 'supported',question='触发仍未核实')
        if stage=='observe' and payload.get('audit_feedback'):
            assert payload['audit_feedback']['positive_trigger_locks']==[]
            raw['events'][0]['target']='独立重新观察的目标'
    s=Session(monkeypatch,mutate,count=6);result=s.run()
    assert len(s.calls)==5 and result['trajectory_audit']['checks'][0]['status']=='needs_review'
    assert result['events'][0]['uncertainties']==['独立证据复核：触发仍未核实']


def test_second_partial_reaudit_cannot_drop_supported_lock_from_skipped_late_shard(monkeypatch):
    observations=0;audits=0
    def mutate(stage,payload,raw,s):
        nonlocal observations,audits
        if payload.get('packet_id')!='packet-002':return
        if stage=='observe':
            observations+=1
            if observations==3:raw['events'].pop()
        if stage=='audit':
            audits+=1
            if audits in (2,3):contradiction(raw)
    s=Session(monkeypatch,mutate)
    with pytest.raises(ValueError) as exc:s.run()
    assert exc.value.trajectory_reason=='audit_positive_trigger_exhausted'
    feedback=[v for n,v in s.records if n.endswith('audit-repair-feedback')]
    assert [len(f['positive_trigger_locks']) for f in feedback]==[7,7]
    assert not any(n=='trajectory-final' for n,_ in s.records)


def test_existing_supported_lock_is_released_only_by_exact_validated_trigger_contradiction(monkeypatch):
    audits=0
    def mutate(stage,payload,raw,s):
        nonlocal audits
        if stage=='audit':
            audits+=1
            if audits==1:contradiction(raw)
            if audits==2:contradiction(raw,trigger='contradicted')
        if stage=='observe' and payload.get('audit_feedback') and audits==2:
            assert payload['audit_feedback']['positive_trigger_locks']==[]
            raw['events'][0]['target']='触发明确被反证后的新候选'
    s=Session(monkeypatch,mutate,count=6);result=s.run()
    assert len(s.calls)==7 and result['trajectory_audit']['corrections_used']==2
    assert result['events'][0]['target']=='触发明确被反证后的新候选'


def test_second_audit_unclear_trigger_cannot_erase_an_existing_supported_lock(monkeypatch):
    audits=0
    def mutate(stage,payload,raw,s):
        nonlocal audits
        if stage=='audit':
            audits+=1
            if audits==1:contradiction(raw)
            if audits==2:raw['checks'][0].update(trigger_verdict='unclear',outcome_verdict='contradicted',question='结果反证但触发未确定')
        if stage=='observe' and audits==2:raw['events'][0]['target']='不能用unclear解锁的目标'
    s=Session(monkeypatch,mutate,count=6)
    with pytest.raises(ValueError) as exc:s.run()
    assert exc.value.trajectory_reason=='audit_positive_trigger_exhausted'


def test_retention_merge_does_not_double_count_repeated_supported_audits(monkeypatch):
    audits=0
    def mutate(stage,payload,raw,s):
        nonlocal audits
        if stage=='audit':
            audits+=1
            if audits<=2:contradiction(raw)
    s=Session(monkeypatch,mutate,count=6);result=s.run()
    feedback=result['trajectory_audit']['repair_feedback']
    assert [len(f['positive_trigger_locks']) for f in feedback]==[1,1]
    assert len(result['events'])==1
