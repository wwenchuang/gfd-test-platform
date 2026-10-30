"""Authentic v16 captures remain invalid. Loopback images/corrected replies are synthetic."""
import copy
import json
from pathlib import Path
import pytest
from task_server.services import ui_video_runtime as runtime, ui_video_trajectory as vt
from task_server.services import ai_skill_service as ai, ui_video_stage_policy as policy
from task_server.services.ui_video_stage_receipts import canonical_sha
from test_ui_video_audit_structured_output import valid, request, audit_schema
from test_ui_video_stage_wire import capture
from test_ui_video_model_jobs import Gateway, manager
from test_ui_video_audit_batches import Engine
from test_ui_video_business_runtime import harness

FIXTURE = Path(__file__).parent / 'fixtures/ui_video_v16_contract_conflicts.json'


def fixture():
    return json.loads(FIXTURE.read_text())


def raw(number):
    return next(e['value']['content'] for e in fixture()['entries'] if e['entry'] == number)


def packets():
    f = fixture()
    return vt.make_packets(f['media']['frames'], json.loads(raw(4))['states'])


def payload():
    return {**packets()[0], 'image_frames':packets()[0]['frames'], 'trajectory':[{'track':{'event_id':'current'}}],
            'context_trajectory':[{'track':{'event_id':'context'}}]}


def test_fixture_hashes_and_original_packet_ownership():
    for entry in fixture()['entries']:
        assert canonical_sha({k:v for k,v in entry.items() if k!='sha256'}) == entry['sha256']
    assert [{k:v for k,v in row.items() if k!='frames'} for row in packets()]==fixture()['packets']
    assert packets()[0]['owned_frame_ids'] == [f'frame-{i:03d}' for i in range(1,48)]
    assert json.loads(raw(8))['issues'][0]['owner_frame'] == 'frame-048'


def test_audit_schema_domains_immutability_and_empty_claims():
    p=payload(); before=copy.deepcopy(p); canonical=audit_schema(); schema=runtime._audit_schema(p)
    assert p==before and audit_schema()==canonical
    props=schema['properties']; refs=[f['frame_id'] for f in p['frames']]
    assert props['issues']['items']['properties']['owner_frame']['enum']==p['owned_frame_ids']
    assert props['issues']['items']['properties']['frame_refs']['items']['enum']==refs
    for key in ['first_frame','last_frame']:
        assert props['revisit']['items']['properties'][key]['enum']==refs
    assert props['checks']['items']['properties']['event_id']['enum']==['current']
    assert policy.validate_output_schema(schema,stage='audit')==schema
    empty=runtime._audit_schema({**p,'trajectory':[]})
    assert 'enum' not in empty['properties']['checks']['items']['properties']['event_id']
    policy.validate_stage_output(valid(),empty)
    for stage in ['grounding','semantic']:
        with pytest.raises(ValueError):policy.validate_output_schema(schema,stage=stage)


@pytest.mark.parametrize('number',[8,10])
def test_captured_owner_refused_at_real_skill_boundary(monkeypatch,number):
    p=payload(); p.update(phase='whole-trajectory',reasoning_mode='bounded')
    # Captured checks must stay authentic; use their exact current claim IDs.
    p['trajectory']=[{'track':{'event_id':c['event_id']}} for c in json.loads(raw(number))['checks']]
    assets=[dict(name=f['frame_id'],timestamp_ms=f['timestamp_ms'],mime='image/jpeg',base64='YQ==') for f in p['frames']]
    calls=capture(monkeypatch,raw(number));receipts=[];schema=runtime._audit_schema(p)
    with pytest.raises(ai.SkillSchemaError) as caught:
        ai.run_ai_skill('ui_video_trajectory',p,version='audit',video_stage='audit',image_assets=assets,output_schema=schema,raw_receipt=receipts.append)
    assert caught.value.schema_path.startswith('$.issues[') and caught.value.schema_path.endswith('.owner_frame')
    assert len(calls)==len(receipts)==1 and receipts[0]['content']==raw(number)
    assert calls[0]['outputSchema']==schema
    assert receipts[0]['videoPolicy']['output_schema_sha256']==canonical_sha(schema)


def test_child_owner_rejection_keeps_raw_hash_cleanup_without_retry(tmp_path):
    p=payload(); p.update(phase='whole-trajectory',reasoning_mode='bounded')
    p['trajectory']=[{'track':{'event_id':c['event_id']}} for c in json.loads(raw(8))['checks']]
    assets=[dict(name=f['frame_id'],timestamp_ms=f['timestamp_ms'],mime='image/jpeg',base64='YQ==') for f in p['frames']]
    schema=runtime._audit_schema(p); p['reference_contract']=reference_contract(p); gateway=Gateway(raw(8))
    try:
        with manager(tmp_path,gateway) as owner:
            with pytest.raises(ai.SkillSchemaError):
                owner.run('ui_video_trajectory',p,version='audit',video_stage='audit',image_assets=assets,output_schema=schema)
            assert len(gateway.calls)==len(owner.accounting)==1
            assert gateway.calls[0]['payload']['reference_contract']==reference_contract(p)
            assert 'packet-audit-reference-v1' in gateway.calls[0]['prompt']
            row=owner.accounting[0]
            assert row['proof_persisted'] and row['receipt_persisted'] and row['cleanup'] and row['terminal'] and not row['admitted']
            receipt=owner.records.read(row['receipt_ref']);proof=owner.records.read(row['proof_ref'])
            assert receipt['content']==raw(8) and receipt['gateway_request_sha256']==proof['gateway_request_sha256']
            assert receipt['videoPolicy']['output_schema_sha256']==canonical_sha(schema)
            assert not owner.active_pids
    finally:gateway.close()


def temporal_failure(events=None):
    f=fixture(); ps=packets(); previous=[];tracks=[]
    for packet,number in zip(ps,[6,14,18,22,26]):
        previous,tracks=vt.bind_packet_events(json.loads(raw(number))['events'],packet,f['media']['frames'],previous,tracks)
    with pytest.raises(ValueError,match='video_model_invalid') as caught:
        vt.bind_packet_events(events if events is not None else json.loads(raw(30))['events'],ps[5],f['media']['frames'],previous,tracks)
    assert caught.value.trajectory_reason=='outcome_reference'
    return caught.value


def test_authentic_temporal_rejection_safe_indexed_metadata_no_capture_rewrite():
    before=FIXTURE.read_bytes();error=temporal_failure()
    assert error.model_feedback=={'event_index':1,'trigger':{'frame_id':'frame-185','timestamp_ms':50733},
        'evidence_last':{'frame_id':'frame-187','timestamp_ms':51433},
        'outcome_first':{'frame_id':'frame-186','timestamp_ms':50867},
        'outcome_last':{'frame_id':'frame-186','timestamp_ms':50867}}
    assert FIXTURE.read_bytes()==before
    ev=json.loads(raw(30))['events'];ev[1].update(target='SECRET',observation='SECRET',uncertainties=['SECRET'])
    assert 'SECRET' not in json.dumps(temporal_failure(ev).model_feedback)
    ev[1]['outcome']['frame_refs']=['frame-001']
    assert not hasattr(temporal_failure(ev),'model_feedback')


@pytest.mark.parametrize('invalid_count',[1,3])
def test_temporal_feedback_real_correction_shared_budget(monkeypatch,invalid_count):
    # Synthetic controlled replies only, never source acceptance.
    calls=[]
    def mutate(stage,p,result,_):
        if stage=='observe' and p['packet_id']=='packet-001':
            calls.append(copy.deepcopy(p))
            if len(calls)<=invalid_count:
                result['events'][0]['frame_refs']=['frame-001','frame-002','frame-003']
                result['events'][0]['outcome']['frame_refs']=['frame-002']
    engine=Engine(monkeypatch,mutate=mutate)
    if invalid_count==3:
        with pytest.raises(ValueError) as caught:engine.run()
        assert caught.value.trajectory_reason=='outcome_reference' and len(calls)==3
    else:
        assert engine.run()['events'] and len(calls)==2
    feedback=calls[1]['validation_feedback']
    assert '"event_index":0' in feedback and '"timestamp_ms":300' in feedback
    assert '重新判断本动作自身的证据范围及实际响应' in feedback
    assert '后续独立动作' in feedback and '不能仅延长outcome或删去证据' in feedback


@pytest.mark.parametrize('change',['owner','issue_ref','first','last','context_claim','scaffold','empty_checks'])
def test_narrowed_domains_reject_foreign_values_and_keep_binder_gate(change):
    schema=runtime._audit_schema(payload());reply=valid()
    if change in {'owner','issue_ref'}:
        reply['issues']=[dict(kind='missing_action',owner_frame='frame-001',frame_refs=['frame-001'],question='pending')]
        if change=='owner':reply['issues'][0]['owner_frame']='frame-048'
        else:reply['issues'][0]['frame_refs']=['frame-299']
    elif change in {'first','last'}:
        reply['revisit']=[dict(first_frame='frame-001',last_frame='frame-002',question='pending')]
        reply['revisit'][0]['first_frame' if change=='first' else 'last_frame']='frame-299'
    elif change=='context_claim':
        reply['checks']=[dict(event_id='context',trigger_verdict='supported',outcome_verdict='supported',question='')]
    elif change=='scaffold':
        schema['properties']['issues']['items']['additionalProperties']=True
        with pytest.raises(ValueError):policy.validate_output_schema(schema,stage='audit')
        return
    else:
        schema=runtime._audit_schema({**payload(),'trajectory':[]})
        reply['checks']=[dict(event_id='fabricated',trigger_verdict='supported',outcome_verdict='supported',question='')]
        policy.validate_stage_output(reply,schema)
        with pytest.raises(ValueError) as caught:vt.bind_audit_checks(reply['checks'],[],payload()['frames'])
        assert caught.value.trajectory_reason=='audit_missing_event'
        return
    with pytest.raises(policy.VideoStageOutputError):policy.validate_stage_output(reply,schema)


def reference_contract(p):
    return dict(version='packet-audit-reference-v1',check_event_ids=[c['track']['event_id'] for c in p['trajectory']],
                issue_owner_frame_ids=p['owned_frame_ids'],image_frame_ids=[f['frame_id'] for f in p['frames']])


def test_business_reference_metadata_actual_dispatch_overwrites_input(harness,monkeypatch):
    from task_server.services import ui_video_trajectory as trajectory
    h=harness; original=trajectory.observe_trajectory
    def spoof(doc,root,cancelled,progress,**kw):
        runner=kw['skill_runner']
        def wrapped(skill,p,**options):
            if options.get('version')=='audit':p={**p,'reference_contract':{'version':'UNTRUSTED'}}
            return runner(skill,p,**options)
        return original(doc,root,cancelled,progress,**{**kw,'skill_runner':wrapped})
    monkeypatch.setattr(trajectory,'observe_trajectory',spoof)
    h['run']()
    audits=[p for p,kw in h['calls'] if kw['video_stage']=='audit']
    from task_server.services.ui_video_context_questions import audit_domains
    assert audits and all(p['reference_contract']=={**reference_contract(p),'version':'packet-audit-reference-v2',
        'future_issue_owner_frame_ids':audit_domains(p)[1]} for p in audits)


def test_audit_schema_error_correction_feedback_uses_issue_owner_not_trigger(monkeypatch):
    calls=[]
    def mutate(stage,p,result,_):
        if stage=='audit' and p['packet_id']=='packet-001':
            calls.append(copy.deepcopy(p))
            if len(calls)==1:raise ai.SkillSchemaError('$.issues[0].owner_frame','输出不符合当前阶段schema')
    Engine(monkeypatch,mutate=mutate).run()
    feedback=calls[1]['validation_feedback']
    assert '$.issues[0].owner_frame' in feedback and 'owner_frame必须属于当前owned_frame_ids' in feedback
    assert 'trigger_frame必须属于owned_frame_ids' not in feedback
    assert 'trajectory' in feedback and 'frame_refs' in feedback and 'revisit' in feedback


def test_actual_audit_prompt_and_gateway_text_carry_reference_contract(monkeypatch):
    import subprocess
    p,kw=request();p['reference_contract']=reference_contract({**p,'trajectory':[]})
    calls=capture(monkeypatch,json.dumps(valid()))
    ai.run_ai_skill('ui_video_trajectory',p,**kw)
    body=calls[0]
    assert 'reference_contract' in body['prompt'] and 'check_event_ids' in body['prompt']
    assert '若提供reference_contract' in body['prompt']
    script="""const fs=require('fs'),vm=require('vm');const s=fs.readFileSync('ai-gateway/server.js','utf8');
const fn=s.slice(s.indexOf('function buildUserMessage('),s.indexOf('function imagePartsFromBody('));
const build=vm.runInNewContext(fn+';buildUserMessage');process.stdout.write(build('ui_video_trajectory',JSON.parse(fs.readFileSync(0,'utf8'))));"""
    result=subprocess.run(['node','-e',script],input=json.dumps(body),text=True,capture_output=True,check=True)
    assert 'packet-audit-reference-v1' in result.stdout and 'issue_owner_frame_ids' in result.stdout


@pytest.mark.parametrize('spent',[0,1,2])
def test_captured_temporal_conflict_real_engine_reaches_correction_or_exhaustion(monkeypatch,spent):
    from task_server.services import ui_video_windows
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    observations=[];overview_attempts=[]
    empty=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
    by_packet=dict(zip([p['packet_id'] for p in packets()],[6,14,18,22,26,30]))
    def model(skill,p,**kw):
        version=kw['version']
        if version=='overview':
            overview_attempts.append(1)
            if len(overview_attempts)<=spent:
                return {**copy.deepcopy(empty),'events':json.loads(raw(6))['events']}
            return json.loads(raw(4))
        if version=='audit':
            return dict(checks=[dict(event_id=c['track']['event_id'],trigger_verdict='supported',
                       outcome_verdict='supported',question='') for c in p['trajectory']],issues=[],revisit=[],multiple_scenarios=False)
        number=by_packet.get(p['packet_id'])
        response=json.loads(raw(number)) if number else copy.deepcopy(empty)
        if number==30:
            observations.append(copy.deepcopy(p))
            if len(observations)>1:
                # Deliberately synthetic regression correction after re-observation;
                # platform must never perform this edit on the captured response.
                response['events'][1]['frame_refs']=['frame-185','frame-186']
        return response
    doc={'media':fixture()['media']}
    if spent==2:
        with pytest.raises(ValueError) as caught:vt.observe_trajectory(doc,None,lambda:False,skill_runner=model,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1)
        assert caught.value.trajectory_reason=='outcome_reference' and len(observations)==1
    else:
        result=vt.observe_trajectory(doc,None,lambda:False,skill_runner=model,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1)
        assert result['events'] and len(observations)==2
        feedback=observations[1]['validation_feedback']
        assert '"event_index":1' in feedback and '"frame_id":"frame-187","timestamp_ms":51433' in feedback
        assert '后续独立动作' in feedback
    assert json.loads(raw(30))['events'][1]['frame_refs']==['frame-185','frame-186','frame-187']


def test_shared_response_and_next_trigger_boundary_remains_valid():
    from test_ui_video_trajectory import frames,event
    fs=frames(4);packet=vt.make_packets(fs,[])[0]
    events=[event(['frame-001','frame-002']),event(['frame-002','frame-003'])]
    bound,tracks=vt.bind_packet_events(events,packet,fs,[],[])
    assert len(bound)==len(tracks)==2
    assert tracks[0]['outcome']['frame_refs']==[tracks[1]['trigger_frame']]
    prompt=ai.load_ai_skill_prompt('ui_video_trajectory','observe')
    assert '共享边界帧' in prompt and '分别保留两个独立事件' in prompt
