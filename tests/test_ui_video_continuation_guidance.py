"""Frozen original failures stay invalid; every successful repair here is synthetic."""
import copy
import json
import subprocess
from pathlib import Path

import pytest

from task_server.services import ai_skill_service as ai, ui_video_runtime as runtime
from task_server.services import ui_video_trajectory as vt, ui_video_windows
from task_server.services.ui_video_stage_receipts import canonical_sha

HERE = Path(__file__).parent


def fixture():
    return json.loads((HERE/'fixtures/ui_video_v18_continuation_conflicts.json').read_text())


def raw(n):
    return json.loads(next(e['value']['content'] for e in fixture()['entries'] if e['entry']==n))


def context():
    media=json.loads((HERE/'fixtures/ui_video_v16_contract_conflicts.json').read_text())['media']
    packets=vt.make_packets(media['frames'],raw(4)['states'])
    previous,tracks=vt.bind_packet_events(raw(6)['events'],packets[0],media['frames'],[],[])
    return media,packets,previous,tracks


def test_frozen_hashes_and_ownership():
    for row in fixture()['entries']:
        assert canonical_sha({k:v for k,v in row.items() if k!='sha256'})==row['sha256']
    media,packets,previous,tracks=context()
    assert len(media['frames'])==299
    assert packets[1]['owned_frame_ids']==[f'frame-{i:03d}' for i in range(33,65)]
    assert (tracks[-1]['page_before'],tracks[-1]['page_after'])==('p2','p3')


@pytest.mark.parametrize('number',[10,14])
def test_authentic_page_alias_rejected_with_precise_safe_feedback(number):
    media,ps,previous,tracks=context();candidate=raw(number)['events'];before=copy.deepcopy(candidate)
    with pytest.raises(ValueError) as caught:
        vt.bind_packet_events(candidate,ps[1],media['frames'],previous,tracks)
    assert caught.value.trajectory_reason=='input_continuation'
    feedback=caught.value.model_feedback
    assert feedback['event_index']==0
    assert feedback['failed_checks']==['page_after_matches_page_before']
    assert feedback['expected_identity']['page_before']=='prior_events[-1].page_after'
    assert feedback['expected_identity']['page_after']=='prior_events[-1].page_after'
    assert feedback['prior_eligible'] is True
    assert candidate==before


def test_foreign_trigger_still_rejected_at_real_typed_boundary(monkeypatch):
    from test_ui_video_stage_wire import capture
    media,ps,previous,tracks=context();p={**ps[1],'prior_events':[{**t,'event':e} for t,e in zip(tracks,previous)]}
    p.update(phase='whole-trajectory',reasoning_mode='bounded',image_frames=p['frames'])
    schema=runtime._semantic_schema(p);receipts=[]
    calls=capture(monkeypatch,json.dumps(raw(12)))
    assets=[dict(name=f['frame_id'],timestamp_ms=f['timestamp_ms'],mime='image/jpeg',base64='YQ==') for f in p['frames']]
    with pytest.raises(ai.SkillSchemaError) as caught:
        ai.run_ai_skill('ui_video_trajectory',p,version='observe',video_stage='grounding',image_assets=assets,
                        output_schema=schema,raw_receipt=receipts.append)
    assert caught.value.schema_path=='$.events[4].trigger_frame'
    assert len(calls)==len(receipts)==1
    assert json.loads(receipts[0]['content'])==raw(12)


@pytest.mark.parametrize('change,check',[
    ({'continuation_of':'stale'},'last_event_identity'),
    ({'target':'private target phrase'},'same_field'),
    ({'page_before':'private page phrase'},'page_before_matches_prior_end'),
    ({'edit_boundary':'submission'},'current_boundary_none'),
    ({'action':'aiTap','trigger_kind':'touch'},'current_action_input'),
])
def test_negative_controls_and_no_failed_model_prose_echo(change,check):
    media,ps,previous,tracks=context();e=raw(10)['events'][0]
    e['page_after']='p3';e.update(change)
    e['observation']='DO NOT ECHO THIS';e['uncertainties']=['PRIVATE QUESTION']
    with pytest.raises(ValueError) as caught:
        vt.bind_packet_events([e],ps[1],media['frames'],previous,tracks)
    assert check in caught.value.model_feedback['failed_checks']
    feedback=json.dumps(caught.value.model_feedback)
    assert all(s not in feedback for s in ['private target phrase','private page phrase','DO NOT ECHO THIS','PRIVATE QUESTION'])


def test_missing_history_is_safe_and_synthetic_consistent_identity_passes():
    media,ps,previous,tracks=context();e=raw(10)['events'][0];e['page_after']='p3'
    with pytest.raises(ValueError) as caught:
        vt.bind_packet_events([e],ps[1],media['frames'],[],[])
    assert caught.value.model_feedback['prior_eligible'] is False
    merged,history=vt.bind_packet_events([e],ps[1],media['frames'],previous,tracks)
    assert len(merged)==len(previous)
    assert len(history[-1]['fragments'])==2
    assert merged[-1]['uncertainties']==list(dict.fromkeys(previous[-1]['uncertainties']+e['uncertainties']))


@pytest.mark.parametrize('case,check',[
    ('intervening','prior_action_input'),('boundary','prior_boundary_none'),
    ('trigger','trigger_moves_forward'),('start','evidence_not_before_prior_start'),
    ('end','end_not_before_prior_end'),('outcome','outcome_not_before_prior_outcome'),
    ('gap','gap_at_most_5000ms'),
])
def test_existing_history_and_chronology_guards_unchanged(case,check):
    media,ps,previous,tracks=context();e=raw(10)['events'][0];e['page_after']='p3'
    if case=='intervening':previous[-1]['action']='aiTap'
    if case=='boundary':tracks[-1]['edit_boundary']='navigation'
    if case=='trigger':tracks[-1]['fragments'][-1]['trigger_frame']='frame-050'
    if case=='start':previous[-1]['start_ms']=6000
    if case=='end':previous[-1]['end_ms']=100000
    if case=='outcome':tracks[-1]['outcome']['frame_refs']=['frame-070']
    if case=='gap':
        previous[-1]['end_ms']=0
        e.update(trigger_frame='frame-064',frame_refs=['frame-064'])
        e['outcome']['frame_refs']=['frame-064']
    before=copy.deepcopy((e,previous,tracks))
    with pytest.raises(ValueError) as caught:
        vt.bind_packet_events([e],ps[1],media['frames'],previous,tracks)
    assert caught.value.trajectory_reason=='input_continuation'
    assert check in caught.value.model_feedback['failed_checks']
    assert (e,previous,tracks)==before


def replay(monkeypatch,repair):
    media,ps,previous,tracks=context();calls=[];diagnostics=[]
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    attempts=0
    def model(name,p,**kw):
        nonlocal attempts
        stage=kw['version'];calls.append((stage,copy.deepcopy(p)))
        if stage=='overview':return raw(4)
        if stage=='audit':
            return dict(checks=[dict(event_id=x['track']['event_id'],trigger_verdict='supported',outcome_verdict='supported',question='') for x in p['trajectory']],issues=[],multiple_scenarios=False,revisit=[])
        if p['packet_id']=='packet-001':return raw(6)
        if p['packet_id']=='packet-002':
            attempts+=1;r=raw({1:10,2:12,3:14}[attempts])
            if attempts==3 and repair:
                r=raw(10);r['events'][0]['page_after']='p3' # Explicit synthetic model reply.
            # Use actual skill output validation and its typed schema-error translation.
            from test_ui_video_stage_wire import capture
            capture(monkeypatch,json.dumps(r))
            assets=[dict(name=f['frame_id'],timestamp_ms=f['timestamp_ms'],mime='image/jpeg',base64='YQ==') for f in p['frames']]
            return ai.run_ai_skill(name,p,version='observe',video_stage='grounding',image_assets=assets,
                                   output_schema=runtime._semantic_schema(p),retry_count=0,repair_invalid_json=False)
        return dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
    result=None;error=None
    try:
        result=vt.observe_trajectory({'media':media},None,lambda:False,skill_runner=model,business_timeline={},diagnostics=diagnostics)
    except ValueError as caught:error=caught
    return result,error,calls,diagnostics


@pytest.mark.parametrize('repair',[False,True])
def test_real_nested_loop_retains_parent_and_same_attempt_accounting(monkeypatch,repair):
    result,error,calls,diagnostics=replay(monkeypatch,repair)
    tries=[p for stage,p in calls if stage=='observe' and p['packet_id']=='packet-002']
    assert len(tries)==3
    assert 'input_continuation' in tries[1]['validation_feedback']
    assert 'input_continuation' in tries[2]['parent_validation_feedback']
    assert 'page_after_matches_page_before' in tries[2]['parent_validation_feedback']
    assert '$.events[4].trigger_frame' in tries[2]['validation_feedback']
    assert 'parent_validation_feedback' not in tries[1]
    if repair:
        assert error is None and result['trajectory_audit']['corrections_used']==2
        clean=next(p for stage,p in calls if stage=='observe' and p['packet_id']=='packet-003')
        assert 'parent_validation_feedback' not in clean and clean['validation_feedback']==''
    else:
        assert error.trajectory_reason=='input_continuation'
        assert diagnostics[-1]['correction_scope_used']==2


def test_bounded_slots_replace_latest_without_recursion():
    c={};parent='PARENT'+('x'*13000)
    vt._correction_feedback(c,parent,'FIRST'+('y'*13000))
    assert len(c['parent_validation_feedback'])==12000 and len(c['validation_feedback'])==12000
    assert c['parent_validation_feedback_truncated'] and c['validation_feedback_truncated']
    vt._correction_feedback(c,parent,'SECOND')
    assert c['validation_feedback']=='SECOND' and 'validation_feedback_truncated' not in c
    assert c['parent_validation_feedback'].count('PARENT')==1
    vt._correction_feedback(c,'','THIRD')
    assert c=={'validation_feedback':'THIRD'}


def test_two_inner_errors_keep_latest_only_and_empty_parent(monkeypatch):
    from test_ui_video_audit_batches import Engine
    from test_ui_video_trajectory import frames
    attempts=[]
    def mutate(stage,p,r,calls):
        if stage=='observe' and p['packet_id']=='packet-001':
            attempts.append(copy.deepcopy(p))
            if len(attempts)==1:r['checks']=[{}]
            if len(attempts)==2:r['states']=[{}]
    engine=Engine(monkeypatch,mutate=mutate)
    result=vt.observe_trajectory({'media':{'frames':frames(90),'duration_ms':9001}},None,
                                lambda:False,skill_runner=engine.model,business_timeline={})
    assert result['trajectory_audit']['corrections_used']==2
    assert len(attempts)==3
    assert all('parent_validation_feedback' not in p for p in attempts)
    assert 'state_id' in attempts[2]['validation_feedback']
    assert 'checks' not in attempts[2]['validation_feedback']


def test_actual_skill_and_gateway_wire_preserve_both_constraints(monkeypatch):
    from test_ui_video_stage_wire import capture
    _,_,calls,_=replay(monkeypatch,True)
    p=next(p for stage,p in reversed(calls) if stage=='observe' and p.get('parent_validation_feedback'))
    calls=capture(monkeypatch,json.dumps(dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])))
    assets=[dict(name=f['frame_id'],timestamp_ms=f['timestamp_ms'],mime='image/jpeg',base64='YQ==') for f in p['frames']]
    ai.run_ai_skill('ui_video_trajectory',p,version='observe',image_assets=assets,retry_count=0,repair_invalid_json=False)
    assert len(calls)==1;body=calls[0]
    assert body['payload']['parent_validation_feedback']==p['parent_validation_feedback']
    assert body['payload']['validation_feedback']==p['validation_feedback']
    assert [{k:a[k] for k in assets[0]} for a in body['imageAssets']]==assets
    script="""const fs=require('fs'),vm=require('vm');const s=fs.readFileSync('ai-gateway/server.js','utf8');
const fn=s.slice(s.indexOf('function buildUserMessage('),s.indexOf('function imagePartsFromBody('));
const build=vm.runInNewContext(fn+';buildUserMessage');process.stdout.write(build('ui_video_trajectory',JSON.parse(fs.readFileSync(0,'utf8'))));"""
    wire=subprocess.run(['node','-e',script],input=json.dumps(body),capture_output=True,text=True,check=True).stdout
    assert all(t in wire for t in ['parent_validation_feedback','page_after_matches_page_before','$.events[4].trigger_frame','frame-034'])


def test_prompt_uses_predecessor_end_page_for_both_fields():
    prompt=Path('ai_skills/prompts/ui_video_trajectory.observe.md').read_text()
    assert 'page_before和page_after都逐字复用该项page_after' in prompt
    assert 'parent_validation_feedback' in prompt and 'validation_feedback' in prompt
    assert '编辑区间的state_id不能' in prompt
