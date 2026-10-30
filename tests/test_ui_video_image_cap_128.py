"""Closed image-count admission and real spawned transport; no provider calls."""
import base64
import copy
import json
import pytest
from task_server.services import ai_skill_service as ai
from task_server.services import ui_video_stage_policy as policy
from task_server.services import ui_video_runtime as runtime
from task_server.services.ui_video_stage_receipts import safe_attestation, make_expected_attestation
from test_ui_video_stage_wire import evidence, capture


def invocation(stage,n=128):
    p,a=evidence(n);p.update(phase='whole-trajectory',reasoning_mode='bounded')
    for i,asset in enumerate(a):asset['base64']=base64.b64encode(f'fixture-image-{i}'.encode()).decode()
    kw={'version':'observe' if stage=='grounding' else 'audit','video_stage':stage,'image_assets':a}
    if stage=='grounding':kw['output_schema']=runtime._semantic_schema(p)
    return p,kw


@pytest.mark.parametrize('stage',['grounding','audit'])
def test_complete_128_python_transport_schema_and_receipt(monkeypatch,stage):
    p,kw=invocation(stage);before=copy.deepcopy((p,kw));calls=capture(monkeypatch);trace={}
    ai.ai_gateway_skill_content('ui_video_trajectory','pixels',payload=p,runtime_trace=trace,**kw)
    assert len(calls)==1
    body=calls[0]
    assert len(body['imageAssets'])==128 and body['payload']==p
    for i in range(128):
        assert all(body['imageAssets'][i][k]==v for k,v in kw['image_assets'][i].items())
    proof=make_expected_attestation(body)
    assert safe_attestation(proof)['effective_options']['image_count']==128
    assert (p,kw)==before
    if stage=='grounding':
        assert len(body['outputSchema']['properties']['events']['items']['properties']['frame_refs']['items']['enum'])==128


@pytest.mark.parametrize('stage',['grounding','audit'])
@pytest.mark.parametrize('bad',['129','bytes'])
def test_over_limit_before_python_dispatch(monkeypatch,stage,bad):
    # 129 frames exceed the wire cap, so construct schema at128 first.
    p,kw=invocation(stage);calls=capture(monkeypatch)
    if bad=='129':
        p2,a2=evidence(129);p.update(p2,phase='whole-trajectory',reasoning_mode='bounded');kw['image_assets']=a2
    else:kw['image_assets'][0]['base64']='YQ=='*(10*1024*1024//4+1)
    with pytest.raises(ValueError,match='video_vision_limit'):
        ai.ai_gateway_skill_content('ui_video_trajectory','pixels',payload=p,**kw)
    assert not calls


@pytest.mark.parametrize('stage,limit',[('overview',60),('consolidate',60),('outcome',60),('inputs',60),('perception',12)])
def test_other_closed_stage_limits_unchanged(monkeypatch,stage,limit):
    skill,version,phase,_,_=policy.STAGES[stage];p,a=evidence(limit);p.update(phase=phase,reasoning_mode='bounded')
    calls=capture(monkeypatch)
    ai.ai_gateway_skill_content(skill,'pixels',payload=p,image_assets=a,video_stage=stage,version=version)
    assert len(calls[0]['imageAssets'])==limit
    p,a=evidence(limit+1);p.update(phase=phase,reasoning_mode='bounded')
    with pytest.raises(ValueError,match='video_'):
        ai.ai_gateway_skill_content(skill,'pixels',payload=p,image_assets=a,video_stage=stage,version=version)
    assert len(calls)==1


def test_legacy_61_stays_rejected(monkeypatch):
    p,a=evidence(61);p.update(phase='whole-trajectory',reasoning_mode='bounded');calls=capture(monkeypatch)
    with pytest.raises(ValueError,match='video_vision_limit'):
        ai.ai_gateway_skill_content('ui_video_trajectory','pixels',payload=p,image_assets=a,version='observe')
    assert not calls


@pytest.mark.parametrize('stage',['grounding','audit'])
def test_modeljobs_128_images_proof_receipt_admission_and_cleanup(tmp_path,stage):
    from test_ui_video_model_jobs import Gateway, manager
    content={'states':[],'events':[],'checks':[],'uncertainties':[],'multiple_scenarios':False,'revisit':[]} if stage=='grounding' else {'checks':[],'issues':[],'multiple_scenarios':False,'revisit':[]}
    gateway=Gateway(content=json.dumps(content));p,kw=invocation(stage)
    try:
        with manager(tmp_path,gateway) as owner:
            trace={}
            assert owner.run('ui_video_trajectory',p,runtime_trace=trace,**kw)==content
            assert trace['imageCount']==128
            assert len(gateway.calls)==1 and len(gateway.calls[0]['imageAssets'])==128
            row=owner.accounting[0]
            assert row['proof_persisted'] and row['receipt_persisted'] and row['admitted'] and row['cleanup']
            receipt=owner.records.read(row['receipt_ref'])
            assert receipt['videoPolicy']['effective_options']['image_count']==128
            assert not owner.active_pids
    finally:gateway.close()


@pytest.mark.parametrize('business',[False,True])
def test_captured_v12_44_plus16_then_pending2_real_expansion(monkeypatch,business):
    from pathlib import Path
    from task_server.services import ui_video_trajectory as vt, ui_video_windows as windows
    frozen=json.loads((Path(__file__).parent/'fixtures/ui_video_v12_capacity.json').read_text())
    originals=[f for f in frozen['frames'] if int(f['frame_id'].split('-')[1])<=299];added=[f for f in frozen['frames'] if int(f['frame_id'].split('-')[1])>299]
    packet=vt.make_packets(originals,frozen['overview_states'])[3]
    assert len(packet['frames'])==44 and len(added)==16
    # Isolate actual failed packet, retaining source ownership/PTS and raw locators.
    monkeypatch.setattr(vt,'make_packets',lambda *a:[copy.deepcopy(packet)])
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    loads=[];observations=[];diagnostics=[]
    def runner(skill,payload,**kw):
        raw=dict(states=[],events=[],checks=[],uncertainties=['unverified locator'],multiple_scenarios=False,revisit=[])
        if kw['version']=='audit':return dict(checks=[],issues=[],multiple_scenarios=False,revisit=[])
        if kw['version']=='observe':
            observations.append(copy.deepcopy(payload))
            if business and len(observations)==3:raise Decoded()
            if len(observations)<=2:raw['revisit']=copy.deepcopy(frozen['requests'][str(18 if len(observations)==1 else 20)])
        return raw
    def loader(request,capacity,*,owned_time_range_ms):
        loads.append((copy.deepcopy(request),capacity))
        if len(loads)<=2:
            return copy.deepcopy(added[:8] if len(loads)==1 else added[8:])
        # Exhausted decode is still a real acquisition attempt, never clears questions.
        return []
    args={'business_timeline':{'classification':'unverified_business_hypotheses'}} if business else {}
    if not business:
        with pytest.raises(ValueError,match='video_vision_limit'):
            vt.observe_trajectory({'media':{'frames':originals,'duration_ms':75380}},None,lambda:False,skill_runner=runner,revisit_bounded=loader,diagnostics=diagnostics,**args)
        assert len(loads)==2 and diagnostics[-1]['packet_frames']==60 and diagnostics[-1]['capacity']==0
    else:
        # Avoid later coverage gate; the regression stops after both pending requests decode.
        class Decoded(Exception):pass
        with pytest.raises(Decoded):
            vt.observe_trajectory({'media':{'frames':originals,'duration_ms':75380}},None,lambda:False,skill_runner=runner,revisit_bounded=loader,diagnostics=diagnostics,**args)
        assert len(observations[1]['frames'])==60
        assert observations[1]['acquisition_budget']=={'remaining_revisit_attempts':2,'remaining_new_frames':48,'packet_image_slots':68,'max_requests_per_response':2,'max_frames_per_request':20,'max_request_span_ms':3000}
        assert [cap for _,cap in loads]==[20,20,20,20]
        assert diagnostics[-1]['revisits']==4 and diagnostics[-1]['acquired_frames']==16
        assert not any(d['kind']=='capacity_refusal' for d in diagnostics)


def test_full128_owned_refs_use_existing_schema_bound_without_empty_trigger():
    p,_=evidence(128);p['owned_frame_ids']=[f['frame_id'] for f in p['frames']];p['context_frame_ids']=[]
    schema=runtime._semantic_schema(p)
    trigger=schema['properties']['events']['items']['properties']['trigger_frame']['enum']
    assert trigger==p['owned_frame_ids'] and len(trigger)==128 and '' not in trigger


def test_safe_receipt_count_is_stage_scoped():
    for stage,accepted,rejected in [('grounding',128,129),('audit',128,129),('overview',60,61),('perception',12,13),('semantic',0,2)]:
        assert safe_attestation({'stage':stage,'effective_options':{'image_count':accepted}})['effective_options']['image_count']==accepted
        assert 'image_count' not in safe_attestation({'stage':stage,'effective_options':{'image_count':rejected}})['effective_options']


@pytest.mark.parametrize('stage',['grounding','audit'])
def test_modeljobs129_refused_before_http(tmp_path,stage):
    from test_ui_video_model_jobs import Gateway, manager
    from task_server.services.ui_video_model_jobs import ModelJobError
    gateway=Gateway();p,kw=invocation(stage)
    extra,assets=evidence(129);p.update(extra,phase='whole-trajectory',reasoning_mode='bounded');kw['image_assets']=assets
    try:
        with manager(tmp_path,gateway) as owner:
            with pytest.raises(ModelJobError,match='video_job_failed'):
                owner.run('ui_video_trajectory',p,**kw)
            assert not gateway.calls and not owner.active_pids
            assert owner.accounting[0]['cleanup'] and not owner.accounting[0]['admitted']
    finally:gateway.close()
