"""Original21 stays failed; successful continuations here are synthetic controls."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from task_server.services import ui_video_trajectory as vt, ui_video_runtime as runtime, ui_video_windows
from task_server.services.ui_video_stage_receipts import canonical_sha
from test_ui_video_runtime_evidence_reuse import BusinessEngine, request


def fixture():
    return json.loads((Path(__file__).parent/'fixtures/ui_video_v21_unavailable_evidence.json').read_text())


def raw(n):
    return json.loads(next(e['value']['content'] for e in fixture()['entries'] if e['entry']==n))


def test_exact_frozen_original21():
    f=fixture();assert len(f['entries'])==43
    assert len(f['source']['media']['frames'])==359
    assert f['source']['status']=='failed' and f['source']['draft_id']==''
    for e in f['entries']:assert canonical_sha({k:v for k,v in e.items() if k!='sha256'})==e['sha256']
    p=Path(f['provenance']['export'])
    if not p.exists():pytest.skip('private export unavailable')
    assert json.loads((p/'source.json').read_text())==f['source']
    assert hashlib.sha256((p/'source.json').read_bytes()).hexdigest()==f['provenance']['source_sha256']
    entries=[]
    for m in f['provenance']['manifest']:
        b=(p/m['path']).read_bytes();assert len(b)==m['bytes'] and hashlib.sha256(b).hexdigest()==m['sha256'];entries+=json.loads(b)['entries']
    assert entries==f['entries']


@pytest.mark.parametrize('n',[24,26])
def test_authentic_reversed_states_safe_indexed_feedback(n):
    fs=fixture()['source']['media']['frames'];r=raw(n);before=copy.deepcopy(r)
    with pytest.raises(ValueError) as e:vt.validate_states(r['states'],fs)
    assert e.value.trajectory_reason=='state_reference'
    fb=e.value.model_feedback
    assert 'first_not_after_last' in fb['failed_checks']
    s=r['states'][fb['state_index']]
    assert fb['first_anchor']==next(f for f in fs if f['frame_id']==s['first_frame'])
    assert r==before
    assert vt.validate_states(raw(28)['states'],fs)==raw(28)['states']


@pytest.mark.parametrize('kind',['missing','ordering','hostile'])
def test_state_feedback_only_validated_metadata(kind):
    fs=[dict(frame_id='frame-999',timestamp_ms=1),dict(frame_id='frame-001',timestamp_ms=2)]
    s=dict(state_id='DO NOT ECHO',description='PRIVATE',kind='page',first_frame='frame-999',last_frame='frame-001')
    assert vt.validate_states([s],fs)==[s] # numeric IDs are not chronology
    if kind=='ordering':states=[dict(s,first_frame='frame-001'),s]
    else:states=[dict(s,first_frame='HOSTILE' if kind=='hostile' else 'missing')]
    with pytest.raises(ValueError) as e:vt.validate_states(states,fs)
    fb=e.value.model_feedback
    assert ('start_not_before_previous' if kind=='ordering' else 'first_anchor_available') in fb['failed_checks']
    assert not any(x in json.dumps(fb) for x in ['HOSTILE','PRIVATE','DO NOT ECHO','missing'])


@pytest.mark.parametrize('business',[False,True])
def test_runtime_provider_exact_allowance_and_freeze(business):
    b=runtime.RuntimeBudget(0,clock=lambda:0);b.freeze_packets(9,business=business)
    assert b.limit==(38 if business else 30)
    for _ in range(b.limit):b.reserve()
    b.freeze_packets(1,business=not business)
    with pytest.raises(ValueError,match='video_vision_limit'):b.reserve()


@pytest.mark.parametrize('stage',['observe','audit'])
def test_business_image_capacity_records_not_acquired_and_independent_audit(monkeypatch,stage):
    q='Q'*2000
    def mutate(s,p,r,c):
        if s==stage and p.get('packet_id')=='packet-002':r['revisit']=[dict(request(),question=q)]
    monkeypatch.setattr(vt,'video_stage_image_limit',lambda *a:44)
    engine=BusinessEngine(monkeypatch,mutate=mutate,revisit=lambda *a:pytest.fail('budget denial must not decode'))
    result=engine.run();a=result['trajectory_audit']
    assert a['revisit_attempts']==[]
    assert a['deferred_revisit_requests'][0]['status']=='not_acquired'
    assert a['deferred_revisit_requests'][0]['reason']=='request_image_capacity'
    assert q in result['uncertainties']
    assert sum(s=='observe' and p.get('packet_id')=='packet-002' for s,p,_ in engine.calls)==1
    assert len(engine.audits())==2
    assert all(c['status']=='confirmed' for c in a['checks']) # independent verdict doesn't clear global blocker


def test_authentic21_no_prefix_decode_and_same_candidate_audit(monkeypatch):
    f=fixture();media=f['source']['media'];fs=media['frames'];originals=[x for x in fs if int(x['frame_id'][6:])<=299]
    rows={e['entry']:e for e in f['entries']};numbers=iter(range(4,30,2));calls=[];loads=[]
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    class ReachedAudit(Exception):pass
    def model(name,p,**kw):
        calls.append((kw['version'],copy.deepcopy(p)))
        if len(calls)==14:
            assert kw['version']=='audit' and p['packet_id']=='packet-004'
            assert len(loads)==3 and len(p['frames'])==84
            assert [x['request'] for x in p['deferred_revisit_requests']]==raw(28)['revisit']
            assert all(x['status']=='not_acquired' and x['reason']=='revisit_attempts' for x in p['deferred_revisit_requests'])
            assert all(x['proof_ref']==dict(entry=27,sha256=rows[27]['sha256']) and x['receipt_ref']==dict(entry=28,sha256=rows[28]['sha256']) for x in p['deferred_revisit_requests'])
            assert 'first_not_after_last' in calls[-3][1]['validation_feedback']
            raise ReachedAudit()
        n=next(numbers);r=json.loads(rows[n]['value']['content'])
        kw['runtime_trace']['video_record_refs']=dict(proof_ref=dict(entry=n-1,sha256=rows[n-1]['sha256']),receipt_ref=dict(entry=n,sha256=rows[n]['sha256']))
        from test_ui_video_stage_wire import capture
        from task_server.services import ai_skill_service as ai
        capture(monkeypatch,json.dumps(r))
        assets=[dict(name=x['frame_id'],timestamp_ms=x['timestamp_ms'],mime='image/jpeg',base64='YQ==') for x in p['frames']]
        options={}
        if kw['version']=='observe':options=dict(video_stage='grounding',output_schema=runtime._semantic_schema(p))
        return ai.run_ai_skill(name,p,version=kw['version'],image_assets=assets,retry_count=0,repair_invalid_json=False,**options)
    times={x['frame_id']:x['timestamp_ms'] for x in fs}
    def load(req,capacity,**kw):
        loads.append(req);left=times[req['first_frame']];right=times[req['last_frame']]
        return [x for x in fs if 300+(len(loads)-2)*20<=int(x['frame_id'][6:])<320+(len(loads)-2)*20 and left<=x['timestamp_ms']<=right] if len(loads)>1 else []
    with pytest.raises(ReachedAudit):
        vt.observe_trajectory({'media':{**media,'frames':originals}},None,lambda:False,
            skill_runner=model,revisit_bounded=load,business_timeline={})


def test_prompt_epistemic_press_boundary():
    for name in ('observe','audit'):
        text=Path(f'ai_skills/prompts/ui_video_trajectory.{name}.md').read_text()
        assert '无需手指在画面中出现' in text
        assert '装饰性标记' in text and 'unclear' in text


@pytest.mark.parametrize('stage',['observe','audit'])
@pytest.mark.parametrize('finding',['missing_action','contradicted'])
def test_deferral_keeps_audit_repairs_positive_locks_and_shards(monkeypatch,stage,finding):
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    seen=[]
    def mutate(s,p,r,c):
        if s==stage and p.get('packet_id')=='packet-002':r['revisit']=[request()]
        if s=='audit' and p.get('packet_id')=='packet-002' and not seen:
            seen.append(True)
            if finding=='missing_action':r['issues']=[dict(kind=finding,owner_frame='frame-034',frame_refs=['frame-034'],question='real missing evidence')]
            else:r['checks'][0].update(outcome_verdict=finding,question='real contradiction')
    monkeypatch.setattr(vt,'video_stage_image_limit',lambda *a:44)
    engine=BusinessEngine(monkeypatch,mutate=mutate,revisit=lambda *a:pytest.fail('no decode'));result=engine.run(execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1)
    observations=[p for s,p,_ in engine.calls if s=='observe' and p.get('packet_id')=='packet-002']
    assert len(observations)==2 and observations[1]['audit_feedback']['positive_trigger_locks']
    assert result['trajectory_audit']['corrections_used']==1
    assert request()['question'] in result['uncertainties']
    assert result['trajectory_audit']['revisit_attempts']==[]


@pytest.mark.parametrize('bad',['foreign','decode','cancel'])
def test_actual_loader_errors_never_defer(monkeypatch,bad):
    from task_server.services import ui_video_trajectory
    def mutate(s,p,r,c):
        if s=='observe' and p.get('packet_id')=='packet-001':r['revisit']=[request('frame-001','frame-002')]
    def load(*args):
        if bad=='decode':raise ValueError('video_revisit_limit')
        if bad=='cancel':raise ValueError('video_cancelled')
        return [dict(frame_id='foreign',timestamp_ms=50000)]
    engine=BusinessEngine(monkeypatch,mutate=mutate,revisit=load);diagnostics=[]
    with pytest.raises(ValueError) as caught:engine.run(diagnostics=diagnostics)
    assert str(caught.value)==('video_revisit_limit' if bad=='decode' else 'video_cancelled' if bad=='cancel' else 'video_model_invalid')
    assert not any(x['kind']=='acquisition_deferred' for x in diagnostics)


def test_global_frame_preflight_fair_allocation_and_exhaustion(monkeypatch):
    loads=[]
    def mutate(s,p,r,c):
        if s=='observe' and p.get('packet_id') in ('packet-001','packet-002') and not p.get('deferred_revisit_requests'):
            if not loads and p['packet_id']=='packet-001':r['revisit']=[request('frame-001','frame-002'),request('frame-003','frame-004')]
            elif len(loads)==2 and p['packet_id']=='packet-002':r['revisit']=[request(),request('frame-036','frame-037')]
    def load(req,cap):
        loads.append(cap);start=int(req['first_frame'][6:])*100
        return [dict(frame_id=f'new-{len(loads)}-{i}',timestamp_ms=start+i+1) for i in range(cap)]
    engine=BusinessEngine(monkeypatch,mutate=mutate,revisit=load);result=engine.run()
    assert loads==[20,20,12,12]
    assert sum(len(x['new_frames']) for x in result['trajectory_audit']['revisit_attempts'])==64


def test_more_than_thirty_full_questions_fail_existing_gate(monkeypatch):
    def mutate(s,p,r,c):
        if s=='observe':
            r['uncertainties']=[f'{p["packet_id"]} question {i}' for i in range(10)]
            if p['packet_id']=='packet-002':r['revisit']=[request()]
    monkeypatch.setattr(vt,'video_stage_image_limit',lambda *a:44)
    engine=BusinessEngine(monkeypatch,mutate=mutate,revisit=lambda *a:pytest.fail('no decode'))
    with pytest.raises(ValueError,match='video_model_invalid'):engine.run()


def test_real_skill_gateway_nested_parent_latest_state_feedback(monkeypatch):
    import subprocess
    from task_server.services import ai_skill_service as ai
    from test_ui_video_stage_wire import capture
    actual=ai.run_ai_skill
    attempts=[];bodies=[]
    def mutate(stage,p,r,c):
        if stage=='observe' and p['packet_id']=='packet-001':
            attempts.append(copy.deepcopy(p))
            if len(attempts)==1:r['states']=[dict(state_id='PRIVATE',description='DO NOT ECHO',kind='page',first_frame='frame-002',last_frame='frame-001')]
            if len(attempts)==2:r['states']=[dict(state_id='schema failure')]
    engine=BusinessEngine(monkeypatch,mutate=mutate)
    generate=engine.model
    def run(name,p,**kw):
        r=generate(name,p,**kw);r.setdefault('revisit',[]);captured=capture(monkeypatch,json.dumps(r));
        try:return actual(name,p,version=kw['version'],image_assets=kw['image_assets'],retry_count=0,repair_invalid_json=False,**(dict(video_stage='grounding',output_schema=runtime._semantic_schema(p)) if kw['version']=='observe' else {}))
        finally:bodies.extend(captured)
    engine.model=run;result=engine.run()
    assert len(attempts)==3 and result['trajectory_audit']['corrections_used']==2
    assert 'first_not_after_last' in attempts[1]['validation_feedback']
    assert 'first_not_after_last' in attempts[2]['parent_validation_feedback']
    assert '$.states[0]' in attempts[2]['validation_feedback']
    assert 'DO NOT ECHO' not in attempts[2]['parent_validation_feedback']
    body=next(b for b in bodies if b.get('payload',{}).get('parent_validation_feedback'))
    script="""const fs=require('fs'),vm=require('vm');const s=fs.readFileSync('ai-gateway/server.js','utf8');
const fn=s.slice(s.indexOf('function buildUserMessage('),s.indexOf('function imagePartsFromBody('));
const build=vm.runInNewContext(fn+';buildUserMessage');process.stdout.write(build('ui_video_trajectory',JSON.parse(fs.readFileSync(0,'utf8'))));"""
    wire=subprocess.run(['node','-e',script],input=json.dumps(body),capture_output=True,text=True,check=True).stdout
    assert 'first_not_after_last' in wire and 'parent_validation_feedback' in wire and 'validation_feedback' in wire


def test_real_child_transports_state_feedback_and_deferral_data(tmp_path):
    from test_ui_video_model_jobs import Gateway,manager
    from test_ui_video_stage_runtime import request as stage_request
    from task_server.services import ai_skill_service as ai
    skill,kw=stage_request('overview');payload=kw.pop('payload')
    payload.update(parent_validation_feedback='state_reference {"state_index":0,"failed_checks":["first_not_after_last"]}',
                   validation_feedback='current typed schema repair',
                   deferred_revisit_requests=[dict(status='not_acquired',reason='revisit_attempts',request=request())])
    gateway=Gateway(json.dumps(dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)))
    try:
        with manager(tmp_path,gateway) as owner:
            result=owner.run(skill,payload,**kw)
            assert result['states']==[] and len(gateway.calls)==1 and len(owner.accounting)==1
            p=gateway.calls[0]['payload']
            for key in ('parent_validation_feedback','validation_feedback','deferred_revisit_requests'):assert p[key]==payload[key]
            row=owner.accounting[0]
            assert row['proof_persisted'] and row['receipt_persisted'] and row['cleanup'] and row['terminal'] and row['admitted']
            assert not owner.active_pids
    finally:gateway.close()


from test_ui_video_consolidation_entry import consolidation
from test_ui_case_draft_service import env


def test_full_unfulfilled_question_survives_consolidation_proposal_and_draft_gate(monkeypatch,consolidation,env):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    from task_server.services.ui_case_draft_contract import build_draft_yaml
    from test_ui_case_draft_service import create,confirm,A,TARGET,service
    from task_server.services.operation_attribution import actor_context
    doc,root,observed=consolidation;question='Q'*2000
    observed['uncertainties']=[question,'补帧尚未执行，证据问题未解决，需人工复核。']
    observed['trajectory_audit']={'checks':[],'deferred_revisit_requests':[dict(status='not_acquired',request=dict(request(),question=question))]}
    def model(*args,**kwargs):
        return dict(title='test',events=[dict(candidate_index=i,action=e['action'],target=e['target'],value='',observation=e['observation'],uncertainties=[]) for i,e in enumerate(observed['events'])],uncertainties=[],multiple_scenarios=False)
    monkeypatch.setattr(ai,'run_ai_skill',model)
    checked=va.consolidate_observed_frames(doc,root,lambda:False,observed,copy.deepcopy(observed['events']),[])
    assert question in checked['uncertainties']
    proposal,_=va.draft_proposal({**doc,**checked,'source_id':'uvs_'+'0'*32,'analysis_version':1})
    assert question in proposal['questions']
    # Fully reviewed runnable navigation isolates the global unresolved-question gate.
    from test_ui_case_draft_service import proposal as navigation
    runnable=navigation();runnable['questions']=proposal['questions']
    compiled=build_draft_yaml(runnable,'com.kfb.model')
    assert not compiled['ready'] and question in compiled['questions']
    import io
    from task_server.services import ui_video_source_service as video
    store=video.VideoStore(root/'private-synthetic-sources');monkeypatch.setattr(video,'service_store',lambda:store)
    source=store.upload(A,'com.kfb.model','synthetic.mp4',io.BytesIO(b'mp4'),3,actor=A)
    store.transition(source['source_id'],{'queued'},'ready',media=doc['media'],events=checked['events'],uncertainties=checked['uncertainties'])
    with actor_context(A):
        draft=video.import_video_draft(A,source['source_id'],'com.kfb.model')
        assert question in draft['questions']
        edited=service.update_draft(A,draft['draft_id'],confirm(draft))
        gate=service.validate_draft(A,draft['draft_id'],dict(revision=edited['revision'],target=TARGET))
    assert not gate['ready'] and question in [x['message'] if isinstance(x,dict) else x for x in gate['blockers']]


@pytest.mark.parametrize('business',[False,True])
def test_absent_loader_is_wiring_failure_not_budget_data(monkeypatch,business):
    from test_ui_video_audit_batches import Engine
    from test_ui_video_trajectory import frames
    def mutate(s,p,r,c):
        if s=='observe' and p['packet_id']=='packet-001':r['revisit']=[request('frame-001','frame-002')]
    engine=Engine(monkeypatch,mutate=mutate)
    with pytest.raises(ValueError) as caught:
        vt.observe_trajectory({'media':{'frames':frames(90),'duration_ms':9001}},None,lambda:False,
            skill_runner=engine.model,**({'business_timeline':{}} if business else {}))
    assert caught.value.trajectory_reason=='revisit_unavailable'


@pytest.mark.parametrize('business,limit',[(False,64),(True,128)])
def test_diagnostic_exact_limit_does_not_reset(monkeypatch,business,limit):
    from test_ui_video_audit_batches import Engine
    from test_ui_video_trajectory import frames
    engine=Engine(monkeypatch);diagnostics=[dict(kind='existing') for _ in range(limit-1)]
    vt.observe_trajectory({'media':{'frames':frames(90),'duration_ms':9001}},None,lambda:False,
        skill_runner=engine.model,diagnostics=diagnostics,**({'business_timeline':{}} if business else {}))
    assert len(diagnostics)==limit and diagnostics[-1]['kind']=='response'
