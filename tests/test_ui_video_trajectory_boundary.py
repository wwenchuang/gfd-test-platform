"""Frozen nearest-sample ownership and explicit bounded-context revisit protocol."""
import copy
import pytest
from task_server.services import ui_video_trajectory as vt
from test_ui_video_trajectory import frames,event


def test_original_temporal_cuts_use_ceil_midpoints_disjoint_without_moving_roles():
    fs=frames(65);fs[31]['timestamp_ms']=3201;fs[32]['timestamp_ms']=3302
    original=copy.deepcopy(fs);ps=vt.make_packets(fs,[])
    assert ps[0]['owned_start_ms']==100 and ps[0]['owned_end_ms']==3252
    assert ps[1]['owned_start_ms']==3252 and ps[1]['owned_end_ms']==6450
    assert ps[2]['owned_start_ms']==6450 and ps[2]['owned_end_ms']==6501
    assert [fid for p in ps for fid in p['owned_frame_ids']]==[f['frame_id']for f in fs]
    assert 'frame-033'in ps[0]['context_frame_ids'] and 'frame-032'in ps[1]['context_frame_ids']
    assert fs==original


@pytest.mark.parametrize('mutation',['duplicate_id','duplicate_time','decreasing','negative','bool_time','float_time'])
def test_bad_source_timeline_rejected_before_partition(mutation):
    fs=frames(40)
    if mutation=='duplicate_id':fs[1]['frame_id']=fs[0]['frame_id']
    else:fs[1]['timestamp_ms']={'duplicate_time':100,'decreasing':99,'negative':-1,'bool_time':True,'float_time':200.0}[mutation]
    with pytest.raises(ValueError):vt.make_packets(fs,[])


def actual_boundary_packet():
    fs=frames(160)
    # Source-independent test setup, reproducing the actual adjacent PTS gap.
    for i,f in enumerate(fs):f['timestamp_ms']=i*200
    fs[127]['timestamp_ms']=35967;fs[128]['timestamp_ms']=37000
    for i in range(129,len(fs)):fs[i]['timestamp_ms']=37000+(i-128)*200
    # Make the preceding chronology valid without changing the boundary values.
    for i in range(127):fs[i]['timestamp_ms']=i*280
    return fs,vt.make_packets(fs,[])[4]


def test_actual_context128_to_owned129_has_bounded_locator_and_legacy_rejects():
    fs,p=actual_boundary_packet();q={'first_frame':'frame-128','last_frame':'frame-129','question':'核对采样间隙'}
    assert p['owned_start_ms']==36484
    with pytest.raises(ValueError):vt.validate_revisit_requests([q],p)
    assert vt.validate_revisit_requests([q],p,allow_context_anchors=True)is None
    assert 'frame-128'in p['context_frame_ids'] and 'frame-129'in p['owned_frame_ids']


@pytest.mark.parametrize('change,reason',[
 ({'first_frame':'frame-127','last_frame':'frame-128'},'revisit_outside_owned_interval'),
 ({'first_frame':'frame-128','last_frame':'frame-128'},'revisit_span'),
 ({'first_frame':'unknown'},'revisit_reference'),
 ({'last_frame':'frame-161'},'revisit_reference'),
 ({'first_frame':'frame-129','last_frame':'frame-128'},'revisit_span'),
 ({'question':' '},'revisit_question')])
def test_bounded_revisit_still_rejects_context_only_unknown_future_and_invalid(change,reason):
    _,p=actual_boundary_packet();q={'first_frame':'frame-128','last_frame':'frame-129','question':'核对'};q.update(change)
    with pytest.raises(ValueError)as error:vt.validate_revisit_requests([q],p,allow_context_anchors=True)
    assert error.value.trajectory_reason==reason


def test_duplicate_bounded_requests_rejected_before_any_decode():
    _,p=actual_boundary_packet();q={'first_frame':'frame-128','last_frame':'frame-129','question':'核对'}
    with pytest.raises(ValueError):vt.validate_revisit_requests([q,q],p,allow_context_anchors=True)


@pytest.mark.parametrize('bounded',[False,True])
def test_observer_advertises_only_actual_callback_capability_and_preserves_ranges(monkeypatch,bounded):
    from task_server.services import ai_skill_service as ai,ui_video_windows
    original=frames(65);seen=[];loads=[];records=[];requested=False
    def model(name,payload,**kw):
        nonlocal requested
        seen.append((kw['version'],copy.deepcopy(payload)))
        raw=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
        if kw['version']=='observe':
            if payload['packet_id']=='packet-001':raw['events']=[event(['frame-001','frame-002'])]
            if payload['packet_id']=='packet-002':
                if not requested:
                    requested=True;raw['revisit']=[dict(first_frame='frame-032' if bounded else 'frame-033',last_frame='frame-033' if bounded else 'frame-034',question='核对当前区间')]
                else:raw['events']=[event(['frame-091','frame-033' if bounded else 'frame-034'])]
        if kw['version']=='audit':raw=dict(checks=[dict(event_id=x['track']['event_id'],trigger_verdict='supported',outcome_verdict='supported',question='')for x in payload['trajectory']],issues=[],multiple_scenarios=False,revisit=[])
        return raw
    monkeypatch.setattr(ai,'run_ai_skill',model);monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    def legacy(q,capacity):loads.append((copy.deepcopy(q),capacity,None));return [dict(frame_id='frame-091',timestamp_ms=3350)]
    def clipped(q,capacity,*,owned_time_range_ms):loads.append((copy.deepcopy(q),capacity,owned_time_range_ms));return [dict(frame_id='frame-091',timestamp_ms=3275)]
    kwargs={'revisit_bounded':clipped}if bounded else{'revisit':legacy}
    result=vt.observe_trajectory({'media':dict(frames=original,duration_ms=6501)},None,lambda:False,record=lambda k,v:records.append((k,copy.deepcopy(v))),**kwargs)
    assert len(loads)==1
    assert all(p['revisit_context_anchors']is bounded for stage,p in seen if stage in {'observe','audit'})
    attempt=result['trajectory_audit']['revisit_attempts'][0]
    assert attempt['request']==loads[0][0]
    assert attempt['owned_time_range_ms']==[3250,6450]
    if bounded:
        assert loads[0][2]==(3250,6450)
        assert attempt['requested_time_range_ms']==[3200,3300]and attempt['decoded_time_range_ms']==[3250,3300]
    else:assert loads[0][2]is None and attempt['decoded_time_range_ms']==[3300,3400]
    p=result['trajectory_audit']['packets'][1]['packet']
    assert p['owned_start_ms']==3250 and p['owned_end_ms']==6450 and 'frame-032'in p['context_frame_ids']
    assert 'frame-091'in p['owned_frame_ids']
    assert original==frames(65)


@pytest.mark.parametrize('value',[False,None,1,True])
@pytest.mark.parametrize('version',['observe','audit'])
def test_rendered_range_contract_matches_explicit_capability(monkeypatch,value,version):
    from pathlib import Path
    from task_server.services import ai_skill_service as ai
    prompts=Path(__file__).resolve().parents[1]/'ai_skills/prompts'
    monkeypatch.setattr(ai,'load_ai_skill_prompt',lambda name,ver:(prompts/f'{name}.{ver}.md').read_text())
    prompt=ai.render_ai_skill_prompt('ui_video_trajectory',{'revisit_context_anchors':value},version=version)
    assert '{{revisit_contract}}'not in prompt
    if value is True:assert 'context端点仅作定位'in prompt and '至少一个端点'in prompt
    else:assert '整段时间必须位于'in prompt and 'context端点仅作定位'not in prompt


def test_original_locator_span_cannot_hide_oversize_by_clipping():
    _,p=actual_boundary_packet()
    q=dict(first_frame='frame-123',last_frame='frame-130',question='原始跨度过大')
    with pytest.raises(ValueError)as error:vt.validate_revisit_requests([q],p,allow_context_anchors=True)
    assert error.value.trajectory_reason=='revisit_span'


def test_bounded_callback_typeerror_is_not_retried_as_legacy(monkeypatch):
    from task_server.services import ai_skill_service as ai,ui_video_windows
    calls=[];loaded=[];q=dict(first_frame='frame-001',last_frame='frame-003',question='核对')
    def model(name,payload,**kw):
        calls.append(kw['version'])
        return dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[q]if kw['version']=='observe'else[])
    monkeypatch.setattr(ai,'run_ai_skill',model);monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    def bounded(request,capacity,*,owned_time_range_ms):loaded.append(copy.deepcopy(request));raise TypeError('bounded decoder internal failure')
    with pytest.raises(TypeError,match='bounded decoder internal failure'):
        vt.observe_trajectory({'media':dict(frames=frames(),duration_ms=9001)},None,lambda:False,
                              revisit=lambda *a:pytest.fail('legacy fallback called'),revisit_bounded=bounded)
    assert calls==['overview','observe'] and loaded==[q]


@pytest.mark.parametrize('stamp',[3250,3350,100])
def test_bounded_callback_cannot_return_future_or_outside_current_clip(monkeypatch,stamp):
    from task_server.services import ai_skill_service as ai,ui_video_windows
    fs=frames();before=copy.deepcopy(fs)
    def model(name,payload,**kw):return dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,
        revisit=[dict(first_frame='frame-032',last_frame='frame-033',question='当前包边界')]if kw['version']=='observe'else[])
    monkeypatch.setattr(ai,'run_ai_skill',model);monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    with pytest.raises(ValueError)as error:
        vt.observe_trajectory({'media':dict(frames=fs,duration_ms=9001)},None,lambda:False,
                              revisit_bounded=lambda *a,**kw:[dict(frame_id='frame-091',timestamp_ms=stamp)])
    assert error.value.trajectory_reason=='revisit_result'and fs==before


def test_prompt_contract_substitution_does_not_rewrite_raw_payload_placeholder(monkeypatch):
    from task_server.services import ai_skill_service as ai
    monkeypatch.setattr(ai,'load_ai_skill_prompt',lambda *a:'{{revisit_contract}}\n{{payload}}')
    raw={'revisit_context_anchors':True,'question':'literal {{revisit_contract}} stays raw'}
    rendered=ai.render_ai_skill_prompt('ui_video_trajectory',raw,version='observe')
    assert rendered.count('{{revisit_contract}}')==1 and raw['question']in rendered
