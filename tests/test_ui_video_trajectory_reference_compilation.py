"""Raw observer evidence remains complete while ordinary events keep eight refs."""
import copy

import pytest
from task_server.services import ui_video_trajectory as vt
from test_ui_video_trajectory import frames,event


@pytest.mark.parametrize('count',[11,10,10])
def test_dense_raw_input_compiles_with_exact_times_and_complete_private_evidence(count):
    fs=frames(120);packet=vt.make_packets(fs,[])[2]
    refs=[f'frame-{i:03d}' for i in [81,82,83,85,88,93,99,103,107,109,110]]
    if count==10:refs.remove('frame-088')
    raw=event(refs,'aiInput');raw['trigger_frame']='frame-082'
    # Extend the local packet in the same way a valid revisit can supply images.
    packet={**packet,'frames':[f for f in fs if 65<=int(f['frame_id'][-3:])<=116]}
    before=copy.deepcopy((raw,fs,packet))
    out,tracks=vt.bind_packet_events([raw],packet,fs,[],[])
    assert len(out[0]['frame_refs'])==8
    assert {refs[0],refs[-1],'frame-082'}<=set(out[0]['frame_refs'])<=set(refs)
    assert (out[0]['start_ms'],out[0]['end_ms'])==(8100,int(refs[-1][-3:])*100)
    assert tracks[0]['frame_refs']==refs and tracks[0]['fragments']==[raw]
    audit=tracks[0]['reference_compilation']
    assert audit['action']['raw_frame_refs']==refs
    assert audit['action']['selected_frame_refs']==out[0]['frame_refs']
    assert audit['action']['required_anchors']==[refs[0],'frame-082',refs[-1]]
    assert vt.bind_packet_events([raw],packet,fs,[],[])[0]==out
    assert (raw,fs,packet)==before


@pytest.mark.parametrize('bad',['unknown','duplicate','order','outside','too_many'])
def test_invalid_long_raw_refs_are_rejected_before_selection(bad):
    fs=frames(90);packet=vt.make_packets(fs,[])[0];refs=[f['frame_id'] for f in fs[:11]]
    if bad=='unknown':refs[5]='unknown'
    if bad=='duplicate':refs[5]=refs[4]
    if bad=='order':refs[4],refs[5]=refs[5],refs[4]
    if bad=='outside':refs[-1]='frame-080'
    if bad=='too_many':refs=[f['frame_id'] for f in fs[:61]]
    raw=event(refs,'aiInput')
    with pytest.raises(ValueError):vt.bind_packet_events([raw],packet,fs,[],[])


def test_dense_outcome_binds_terminal_and_rejects_pre_trigger_time():
    fs=frames(35);packet=vt.make_packets(fs,[])[0]
    raw=event(['frame-001','frame-002']);raw['trigger_frame']='frame-002'
    raw['outcome']['frame_refs']=[f['frame_id'] for f in fs[1:15]]
    out,tracks=vt.bind_packet_events([raw],packet,fs,[],[])
    assert out[0]['outcome']['frame_refs']==['frame-015']
    audit=tracks[0]['reference_compilation']['outcome']
    assert audit['method']=='terminal_outcome_anchor' and audit['version']==1
    assert audit['raw_frame_refs']==raw['outcome']['frame_refs']
    assert audit['selected_frame_refs']==audit['required_anchors']==['frame-015']
    assert tracks[0]['outcome']==raw['outcome']
    bad=copy.deepcopy(raw);bad['outcome']['frame_refs'].insert(0,'frame-001')
    with pytest.raises(ValueError):vt.bind_packet_events([bad],packet,fs,[],[])


def test_short_reference_lists_are_preserved_exactly():
    fs=frames(30);p=vt.make_packets(fs,[])[0];raw=event(['frame-001','frame-003','frame-004'])
    out,_=vt.bind_packet_events([raw],p,fs,[],[])
    assert out[0]['frame_refs']==raw['frame_refs'] and out[0]['outcome']==raw['outcome']


def test_dense_continuation_keeps_every_raw_fragment_and_anchor_budget():
    fs=frames(90);packets=vt.make_packets(fs,[])
    first=event([f'frame-{i:03d}' for i in range(22,33)],'aiInput')
    first['outcome']['frame_refs']=['frame-030','frame-032']
    old,tracks=vt.bind_packet_events([first],packets[0],fs,[],[])
    second=event([f'frame-{i:03d}' for i in range(33,48)],'aiInput')
    second['outcome']['frame_refs']=['frame-045','frame-047']
    second['continuation_of']=tracks[0]['event_id']
    before=copy.deepcopy((first,second,old,tracks))
    joined,history=vt.bind_packet_events([second],packets[1],fs,old,tracks)
    assert len(joined)==1 and len(joined[0]['frame_refs'])==8
    assert {'frame-022','frame-032','frame-033','frame-047'}<=set(joined[0]['frame_refs'])
    assert history[0]['fragments']==[first,second]
    assert history[0]['frame_refs']==[f'frame-{i:03d}' for i in range(22,48)]
    assert len(history[0]['reference_compilation']['fragments'])==2
    assert joined[0]['outcome']=={**second['outcome'],'frame_refs':['frame-047']}
    from task_server.services.ui_video_analysis import validate_analysis
    assert validate_analysis(dict(events=joined,uncertainties=[],multiple_scenarios=False),fs,9001)['events']
    assert (first,second,old,tracks)==before
    # The old multi-fragment mandatory anchor ceiling remains a hard gate.
    history[0]['fragments'] += [event([f'frame-{i:03d}'],'aiInput') for i in (23,24,25,26,27)]
    third=event(['frame-065','frame-066'],'aiInput');third['continuation_of']=history[0]['event_id']
    with pytest.raises(ValueError) as exc:vt.bind_packet_events([third],packets[2],fs,joined,history)
    assert exc.value.trajectory_reason=='input_evidence_budget'


def test_full_raw_audit_and_issue_remain_blocking_after_compilation(monkeypatch):
    from task_server.services import ai_skill_service as ai,ui_video_windows
    fs=frames(35);raw=event([f'frame-{i:03d}' for i in range(1,12)],'aiInput')
    original=copy.deepcopy(raw);doc={'media':{'frames':fs,'duration_ms':3501}};before=copy.deepcopy(doc)
    recorded=[];audits=[]
    def model(name,payload,**kw):
        stage=kw['version']
        if stage=='audit':
            audits.append(copy.deepcopy(payload))
            checks=[];issues=[]
            for item in payload['trajectory']:
                t=item['track'];assert t['frame_refs']==original['frame_refs']
                checks.append(dict(event_id=t['event_id'],trigger_verdict='supported',outcome_verdict='contradicted',question='最后结果帧不支持原结果描述'))
                issues=[dict(kind='unresolved_evidence',owner_frame='frame-005',frame_refs=['frame-005'],question='完整原图第五帧仍有未决证据')]
            return dict(checks=checks,issues=issues,multiple_scenarios=False,revisit=[])
        return dict(states=[],events=[copy.deepcopy(raw)] if stage=='observe' and payload['packet_id']=='packet-001' else [],checks=[],uncertainties=[],multiple_scenarios=False)
    monkeypatch.setattr(ai,'run_ai_skill',model);monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    result=vt.observe_trajectory(doc,None,lambda:False,record=lambda name,value:recorded.append((name,copy.deepcopy(value))))
    assert len(result['events'][0]['frame_refs'])==8
    assert result['trajectory_audit']['checks'][0]['status']=='needs_review'
    assert any('最后结果帧不支持' in q for q in result['events'][0]['uncertainties'])
    assert '完整原图第五帧仍有未决证据' in result['uncertainties']
    assert next(value['result']['events'][0] for name,value in recorded if name.endswith('observe-result') and value['result']['events'])==original
    assert audits[0]['frames']==vt.make_packets(fs,[])[0]['frames']
    assert raw==original and doc==before


def test_only_observer_raw_schema_expands_and_final_validator_stays_eight():
    import json
    from pathlib import Path
    from task_server.services.ui_video_analysis import validate_analysis
    schema=json.loads((Path(__file__).resolve().parents[1]/'ai_skills/schemas/ui_video_trajectory.schema.json').read_text())
    props=schema['oneOf'][0]['properties']['events']['items']['properties']
    assert props['frame_refs']['maxItems']==60
    assert props['outcome']['properties']['frame_refs']['maxItems']==60
    assert schema['oneOf'][1]['properties']['issues']['items']['properties']['frame_refs']['maxItems']==8
    fs=frames(35);raw=event([f'frame-{i:03d}' for i in range(1,12)],'aiInput')
    out,_=vt.bind_packet_events([raw],vt.make_packets(fs,[])[0],fs,[],[])
    checked=validate_analysis(dict(events=out,uncertainties=[],multiple_scenarios=False),fs,3501)
    assert len(checked['events'][0]['frame_refs'])==8
    bad=copy.deepcopy(out);bad[0]['frame_refs']=raw['frame_refs']
    with pytest.raises(ValueError):validate_analysis(dict(events=bad,uncertainties=[],multiple_scenarios=False),fs,3501)


def test_overlapping_action_context_and_outcome_keep_original_trigger_rule():
    fs=frames(70);packet=vt.make_packets(fs,[])[1]
    raw=event(['frame-048','frame-050','frame-052','frame-054'])
    raw['trigger_frame']='frame-050';raw['outcome']['frame_refs']=['frame-052','frame-054']
    out,tracks=vt.bind_packet_events([raw],packet,fs,[],[])
    assert out[0]['outcome']=={**raw['outcome'],'frame_refs':['frame-054']}
    from task_server.services.ui_video_analysis import validate_analysis
    assert validate_analysis(dict(events=out,uncertainties=[],multiple_scenarios=False),fs,7001)['events'][0]['outcome']['frame_refs']==['frame-054']
    assert out[0]['start_ms']==4800 and out[0]['end_ms']==5400
    assert tracks[0]['fragments']==[raw]


def test_raw_outcome_terminal_cannot_precede_action_last_reference():
    fs=frames(20);packet=vt.make_packets(fs,[])[0]
    raw=event(['frame-001','frame-005','frame-011']);raw['trigger_frame']='frame-005'
    raw['outcome']['frame_refs']=['frame-005','frame-010']
    with pytest.raises(ValueError) as exc:vt.bind_packet_events([raw],packet,fs,[],[])
    assert exc.value.trajectory_reason=='outcome_reference'
