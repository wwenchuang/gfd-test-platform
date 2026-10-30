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
