"""Authentic captures are rejected; successful model repairs are synthetic only."""
import copy
import json
from pathlib import Path
import pytest
from task_server.services import ui_video_trajectory as vt
from task_server.services.ui_video_stage_receipts import canonical_sha


def fixture():
    return json.loads((Path(__file__).parent/'fixtures/ui_video_v17_navigation_conflicts.json').read_text())


def test_fresh_exact_contract():
    expected={'aiTap':['touch','pressed_control','navigation','edit','uncertain'],
              'aiScroll':['touch','pressed_control','navigation','edit','uncertain'],
              'aiInput':['edit'],'aiAssert':['automatic','uncertain'],'aiWaitFor':['automatic','uncertain']}
    contract=vt.action_contract()
    assert contract['allowed_trigger_kinds']==expected
    contract['allowed_trigger_kinds']['aiInput'].clear()
    assert vt.action_contract()['allowed_trigger_kinds']==expected


@pytest.mark.parametrize('number',[38,40])
def test_authentic_conflicts_remain_rejected_with_both_safe_indices(number):
    f=fixture()
    for row in f['entries']:
        assert canonical_sha({k:v for k,v in row.items() if k!='sha256'})==row['sha256']
    raw=lambda n:json.loads(next(e['value']['content'] for e in f['entries'] if e['entry']==n))
    media=json.loads((Path(__file__).parent/'fixtures/ui_video_v16_contract_conflicts.json').read_text())['media']
    packet=vt.make_packets(media['frames'],raw(4)['states'])[7]
    events=raw(number)['events'];before=copy.deepcopy(events)
    with pytest.raises(ValueError) as caught:
        vt.bind_packet_events(events,packet,media['frames'],[],[])
    assert caught.value.trajectory_reason=='interaction_as_state'
    conflicts=caught.value.model_feedback['action_conflicts']
    assert [c['event_index'] for c in conflicts]==[2,3]
    assert all(set(c)=={'event_index','action','trigger_kind','trigger'} for c in conflicts)
    assert events==before


@pytest.mark.parametrize('business',[True,False])
def test_independent_packet_allowance_and_cumulative_count(monkeypatch,business):
    from test_ui_video_audit_batches import Engine
    from test_ui_video_trajectory import frames
    attempts={}
    def mutate(stage,payload,result,calls):
        if stage=='observe':
            packet=payload['packet_id'];attempts[packet]=attempts.get(packet,0)+1
            if attempts[packet]==1:
                result['checks']=[{}]  # Synthetic malformed response in each packet.
    engine=Engine(monkeypatch,count=90,mutate=mutate)
    args=({'business_timeline':{}} if business else {})
    if business:
        result=vt.observe_trajectory({'media':{'frames':frames(90),'duration_ms':9001}},None,lambda:False,
                                     skill_runner=engine.model,audit_batcher=engine.batches,**args)
        assert result['trajectory_audit']['corrections_used']==3
    else:
        with pytest.raises(ValueError):
            vt.observe_trajectory({'media':{'frames':frames(90),'duration_ms':9001}},None,lambda:False,
                                  skill_runner=engine.model,audit_batcher=engine.batches,**args)

from test_ui_video_business_runtime import harness


def test_runtime_overwrites_spoofed_contract(harness,monkeypatch):
    h=harness;original=vt.observe_trajectory
    def spoof(doc,root,cancelled,progress,**kw):
        runner=kw['skill_runner']
        def wrapped(skill,p,**options):
            return runner(skill,{**p,'action_contract':{'version':'UNTRUSTED'}},**options)
        return original(doc,root,cancelled,progress,**{**kw,'skill_runner':wrapped})
    monkeypatch.setattr(vt,'observe_trajectory',spoof)
    h['run']()
    p,options=h['calls'][2]
    assert p['action_contract']==vt.action_contract()
    assert options.get('retry_count',0)==0
    assert p['image_frames']==h['original']


@pytest.mark.parametrize('action',['aiTap','aiScroll','aiInput','aiAssert','aiWaitFor'])
@pytest.mark.parametrize('kind',['touch','pressed_control','navigation','edit','automatic','uncertain'])
def test_pair_matrix(action,kind):
    from test_ui_video_trajectory import frames,event
    fs=frames(3);packet=vt.make_packets(fs,[])[0]
    e=event(['frame-001','frame-002'],action)
    e['trigger_kind']=kind;e['uncertainties']=['unresolved'] if kind=='uncertain' else []
    allowed=kind in vt.action_contract()['allowed_trigger_kinds'][action]
    try:
        vt.bind_packet_events([e],packet,fs,[],[])
    except ValueError as error:
        if allowed:assert getattr(error,'trajectory_reason',None) not in {'automatic_interaction','interaction_as_state','input_contract'}
        else:assert error.trajectory_reason in {'automatic_interaction','interaction_as_state','input_contract'}
    else:assert allowed


def test_single_packet_schema_and_bind_share_two(monkeypatch):
    from test_ui_video_audit_batches import Engine
    from test_ui_video_trajectory import frames
    attempts=[]
    def mutate(stage,p,result,calls):
        if stage=='observe':
            attempts.append(p)
            if len(attempts)==1:result['checks']=[{}]
            else:result['events'][0]['trigger_kind']='automatic'
    engine=Engine(monkeypatch,count=90,mutate=mutate)
    with pytest.raises(ValueError) as caught:
        vt.observe_trajectory({'media':{'frames':frames(90),'duration_ms':9001}},None,lambda:False,
                             skill_runner=engine.model,business_timeline={})
    assert caught.value.trajectory_reason=='automatic_interaction'
    assert len(attempts)==3
    assert 'action_conflicts' in attempts[2]['validation_feedback']


def run_business(engine,**kw):
    from test_ui_video_trajectory import frames
    return vt.observe_trajectory({'media':{'frames':frames(engine.count),'duration_ms':engine.count*100+1}},
        None,lambda:False,skill_runner=engine.model,audit_batcher=engine.batches,
        revisit=engine.revisit,business_timeline={},**kw)


def test_overview_two_are_independent_of_packet_two(monkeypatch):
    from test_ui_video_audit_batches import Engine
    counts={}
    def mutate(stage,p,r,calls):
        key=stage,p.get('packet_id');counts[key]=counts.get(key,0)+1
        if stage=='overview' and counts[key]<=2:r['checks']=[{}]
        if stage=='observe' and counts[key]<=2:r['checks']=[{}]
    result=run_business(Engine(monkeypatch,count=64,mutate=mutate))
    assert result['trajectory_audit']['corrections_used']==6
    assert counts[('overview',None)]==3


@pytest.mark.parametrize('revisit',[False,True])
def test_shards_and_reobservation_cannot_reset_packet_allowance(monkeypatch,revisit):
    from test_ui_video_audit_batches import Engine
    spent=[];observations=[];requested=[]
    def mutate(stage,p,r,calls):
        if p.get('packet_id')!='packet-002':return
        if stage=='observe':
            observations.append(p)
            if len(observations)==1:r['checks']=[{}];spent.append('schema')
        if stage=='audit':
            if len(p['trajectory'])==6 and len(spent)==1:
                r['checks']=[];spent.append('first-shard')
            elif len(p['trajectory'])==1:
                if revisit and not requested:
                    r['revisit']=[dict(first_frame='frame-033',last_frame='frame-034',question='source gap')]
                    requested.append(True)
                else:r['checks']=[];spent.append('terminal-shard')
    engine=Engine(monkeypatch,mutate=mutate,revisit=lambda req,limit:[dict(frame_id='frame-301',timestamp_ms=3350)])
    with pytest.raises(ValueError) as caught:run_business(engine)
    assert caught.value.trajectory_reason=='audit_missing_event'
    assert spent==['schema','first-shard','terminal-shard']
    assert len(observations)==(3 if revisit else 2)


def test_business_global_calls_and_deadline_still_refuse(monkeypatch):
    from test_ui_video_audit_batches import Engine
    engine=Engine(monkeypatch,count=300)
    def many(claims):return [[c] for c in claims] if claims else [[]]
    engine.batches=many
    # Force independent local repairs throughout; local allowance cannot buy engine calls.
    seen=set()
    def mutate(stage,p,r,calls):
        if stage=='observe' and p['packet_id'] not in seen:
            seen.add(p['packet_id']);r['checks']=[{}]
    engine.mutate=mutate
    with pytest.raises(ValueError,match='video_vision_limit'):run_business(engine)
    assert len(engine.calls)==24
    engine=Engine(monkeypatch)
    ticks=iter([0,1201])
    monkeypatch.setattr(vt.time,'monotonic',lambda:next(ticks))
    with pytest.raises(ValueError,match='video_vision_limit'):run_business(engine)
    assert not engine.calls


def test_actual_skill_gateway_serialization_contract_and_fixed_feedback(monkeypatch):
    import subprocess
    from task_server.services import ai_skill_service as ai
    from test_ui_video_stage_wire import capture
    from test_ui_video_trajectory import frames
    fs=frames(3)
    p={'frames':fs,'image_frames':fs,'owned_frame_ids':[f['frame_id'] for f in fs],
       'action_contract':vt.action_contract(),'validation_feedback':json.dumps({'action_conflicts':[
           {'event_index':2,'action':'aiWaitFor','trigger_kind':'navigation',
            'trigger':{'frame_id':'frame-001','timestamp_ms':100}}]})}
    calls=capture(monkeypatch,json.dumps(dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])))
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':'YQ=='} for f in fs]
    ai.run_ai_skill('ui_video_trajectory',p,version='observe',image_assets=assets,retry_count=0,repair_invalid_json=False)
    assert len(calls)==1
    body=calls[0]
    assert [a['name'] for a in body['imageAssets']]==[f['frame_id'] for f in fs]
    assert all(a['base64']=='YQ==' for a in body['imageAssets'])
    assert body['payload']['image_frames']==fs
    assert 'action-trigger-v1' in body['prompt'] and 'action_conflicts' in body['prompt']
    script="""const fs=require('fs'),vm=require('vm');const s=fs.readFileSync('ai-gateway/server.js','utf8');
const fn=s.slice(s.indexOf('function buildUserMessage('),s.indexOf('function imagePartsFromBody('));
const build=vm.runInNewContext(fn+';buildUserMessage');process.stdout.write(build('ui_video_trajectory',JSON.parse(fs.readFileSync(0,'utf8'))));"""
    result=subprocess.run(['node','-e',script],input=json.dumps(body),text=True,capture_output=True,check=True)
    assert all(text in result.stdout for text in ['action-trigger-v1','action_conflicts','aiWaitFor','navigation','frame-001'])


@pytest.mark.parametrize('synthetic_repair',[False,True])
def test_authentic_envelopes_replay_real_business_engine_rejected(monkeypatch,synthetic_repair):
    from task_server.services import ui_video_windows
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    f=fixture();receipts=iter(f['replay_entries']);seen=[]
    last=None
    def model(skill,p,**kw):
        nonlocal last
        try:last=next(receipts)
        except StopIteration:
            if synthetic_repair:
                seen.append(('SYNTHETIC',copy.deepcopy(p)))
                if kw['version']=='audit':
                    return dict(checks=[dict(event_id=c['track']['event_id'],trigger_verdict='supported',outcome_verdict='supported',question='') for c in p['trajectory']],issues=[],multiple_scenarios=False,revisit=[])
                if p['packet_id']=='packet-008':
                    response=json.loads(last['value']['content'])
                    for index in [2,3]:response['events'][index]['action']='aiTap'
                    response['revisit']=[]
                    return response
                return dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
            # Persist authentic invalid40; never rewrite it.
        seen.append((last['entry'],copy.deepcopy(p)))
        assert canonical_sha({k:v for k,v in last.items() if k!='sha256'})==last['sha256']
        return json.loads(last['value']['content'])
    media=json.loads((Path(__file__).parent/'fixtures/ui_video_v16_contract_conflicts.json').read_text())['media']
    def run():
        return vt.observe_trajectory({'media':media},None,lambda:False,skill_runner=model,business_timeline={},
                              audit_batcher=__import__('task_server.services.ui_video_audit_batches',fromlist=['partition_audit_claims']).partition_audit_claims)
    if synthetic_repair:
        assert run()['events']
        assert any(n=='SYNTHETIC' for n,p in seen)
    else:
        with pytest.raises(ValueError) as caught:run()
        assert caught.value.trajectory_reason=='interaction_as_state'
        assert [n for n,p in seen][-3:]==[38,40,40]
        assert all('"event_index":2' in p['validation_feedback'] and '"event_index":3' in p['validation_feedback'] for n,p in seen[-2:])


def test_feedback_never_leaks_prose_or_foreign_anchor():
    f=fixture();raw=lambda n:json.loads(next(e['value']['content'] for e in f['entries'] if e['entry']==n))
    media=json.loads((Path(__file__).parent/'fixtures/ui_video_v16_contract_conflicts.json').read_text())['media']
    packet=vt.make_packets(media['frames'],raw(4)['states'])[7]
    events=raw(38)['events']
    for event in events:
        event.update(target='PRIVATE_TARGET',observation='PRIVATE_OBSERVATION',uncertainties=['PRIVATE_QUESTION'])
    events[3]['trigger_frame']='FOREIGN_FRAME'
    before=copy.deepcopy(events)
    with pytest.raises(ValueError) as caught:vt.bind_packet_events(events,packet,media['frames'],[],[])
    assert caught.value.trajectory_reason=='interaction_as_state'
    feedback=json.dumps(caught.value.model_feedback)
    assert all(secret not in feedback for secret in ['PRIVATE','FOREIGN_FRAME'])
    assert [c['event_index'] for c in caught.value.model_feedback['action_conflicts']]==[2]
    assert events==before
