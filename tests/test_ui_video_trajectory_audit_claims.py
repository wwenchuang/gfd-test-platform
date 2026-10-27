"""Independent audit receives claims without the observer's causal narrative."""
import copy

from task_server.services import ui_video_trajectory as vt,ai_skill_service as ai,ui_video_windows
from test_ui_video_trajectory import frames,event


def test_audit_claim_copy_removes_exactly_three_narrative_locations():
    fragment=event(['frame-001','frame-002']);fragment['uncertainties']=['触点待核对']
    trajectory=[{'track':{**copy.deepcopy(fragment),'event_id':'p1:f1','canonical_event_id':'p1'}}]
    prior=[{'event_id':'p0','fragments':[copy.deepcopy(fragment)],'event':copy.deepcopy(fragment),
            'custom':{'observation':'非观察者事件叙述不应删除'}}]
    before=copy.deepcopy((trajectory,prior));expected=copy.deepcopy(before)
    expected[0][0]['track'].pop('observation')
    expected[1][0]['event'].pop('observation')
    expected[1][0]['fragments'][0].pop('observation')
    actual=vt.audit_claim_context(trajectory,prior)
    assert actual==expected and (trajectory,prior)==before
    actual[0][0]['track']['outcome']['observation']='changed'
    assert (trajectory,prior)==before


def test_observe_and_private_raw_remain_complete_while_audit_uses_claim_copy(monkeypatch):
    calls=[];recorded=[];raw=event(['frame-001','frame-002']);raw['uncertainties']=['真实触点仍有疑问']
    original=copy.deepcopy(raw)
    def model(name,payload,**kw):
        stage=kw['version'];calls.append((stage,copy.deepcopy(payload)))
        if stage=='audit':
            for item in payload['trajectory']:
                t=item['track']
                assert 'observation' not in t
                assert t['outcome']==raw['outcome'] and t['uncertainties']==raw['uncertainties']
            for item in payload['prior_events']:
                assert 'observation' not in item['event']
                assert all('observation' not in f for f in item['fragments'])
                assert item['event']['outcome']['observation']==raw['outcome']['observation']
            return dict(checks=[dict(event_id=p['track']['event_id'],trigger_verdict='supported',outcome_verdict='supported',question='') for p in payload['trajectory']],
                        issues=[dict(kind='missing_action',owner_frame='frame-080',frame_refs=['frame-080'],question='空包完整原图存在遗漏')] if payload['packet_id']=='packet-003' else [],multiple_scenarios=False,revisit=[])
        if stage=='observe' and payload['prior_events']:
            assert payload['prior_events'][0]['event']['observation']==raw['observation']
            assert payload['prior_events'][0]['fragments'][0]['observation']==raw['observation']
        return dict(states=[],events=[copy.deepcopy(raw)] if stage=='observe' and payload['packet_id']=='packet-001' else [],checks=[],uncertainties=[],multiple_scenarios=False)
    monkeypatch.setattr(ai,'run_ai_skill',model);monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    result=vt.observe_trajectory({'media':dict(frames=frames(90),duration_ms=9001)},None,lambda:False,
                                 record=lambda name,value:recorded.append((name,copy.deepcopy(value))))
    assert raw==original
    audit=result['trajectory_audit']
    assert audit['audit_input_mode']=='claims_without_observer_narrative'
    assert audit['tracks'][0]['fragments']==[original]
    assert audit['packets'][0]['raw']['events']==[original]
    observed=next(v['result']['events'][0] for n,v in recorded if n.endswith('observe-result') and v['result']['events'])
    assert observed==original and result['events'][0]['observation']==original['observation']
    assert result['events'][0]['uncertainties']==original['uncertainties']
    assert '空包完整原图存在遗漏' in result['uncertainties']
    audits=[p for stage,p in calls if stage=='audit']
    assert len(calls)==7 and len(audits)==3 and audits[-1]['trajectory']==[]
    for request,packet in zip(audits,vt.make_packets(frames(90),[])):
        assert request['frames']==packet['frames']
