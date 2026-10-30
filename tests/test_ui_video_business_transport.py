"""Offline native video admission, real renderer/transport and proof contracts."""
import base64
import copy
import hashlib
import json
import pytest
from task_server.services import ai_skill_service as ai
from task_server.services import ui_video_stage_policy as policy
from test_ui_video_stage_wire import capture, evidence, semantic_schema


def native(size=32):
    raw=b'\x00\x00\x00\x18ftypisom'+bytes(size-12)
    meta={'source_sha256':hashlib.sha256(raw).hexdigest(),'source_bytes':len(raw),
          'duration_ms':75380,'fps':4,'max_pixels':655360,'total_pixels':268435456}
    assets=[{'name':'source-video','mime':'video/mp4','timestamp_ms':0,'base64':base64.b64encode(raw).decode()}]
    return {'phase':'whole-timeline','video':meta},assets


def test_native_loader_original_bytes(tmp_path):
    from task_server.services.ui_video_native import load_native_video, validate_native_asset
    p,a=native(); path=tmp_path/'source.mp4'; path.write_bytes(base64.b64decode(a[0]['base64']))
    assert load_native_video(path,75380)==(p['video'],a)
    validate_native_asset(p,a)


@pytest.mark.parametrize('change',['hash','size','fps','mime','name','stamp','dataUrl','bad64','noncanonical','ftyp','extra','phase','duration','oversize'])
def test_native_rejection_before_transport(monkeypatch,change):
    p,a=native(); calls=capture(monkeypatch)
    if change=='hash':p['video']['source_sha256']='0'*64
    if change=='size':p['video']['source_bytes']+=1
    if change=='fps':p['video']['fps']=5
    if change=='mime':a[0]['mime']='image/jpeg'
    if change=='name':a[0]['name']='frame-000'
    if change=='stamp':a[0]['timestamp_ms']=True
    if change=='dataUrl':a[0]['dataUrl']='data:video/mp4;base64,AAAA'
    if change=='bad64':a[0]['base64']='@@'
    if change=='noncanonical':a[0]['base64']+='\n'
    if change=='ftyp':a[0]['base64']=base64.b64encode(b'not an mp4').decode()
    if change=='extra':p['answers']=['private']
    if change=='phase':p['phase']='whole-trajectory'
    if change=='duration':p['video']['duration_ms']=600001
    if change=='oversize':p,a=native(12*1024*1024+1)
    with pytest.raises(ValueError,match='video_'):
        ai.ai_gateway_skill_content('ui_video_timeline','business',payload=p,image_assets=a,video_stage='timeline')
    assert not calls


def test_supported_size_timeline_transport_and_renderer(monkeypatch):
    p,a=native(7499997); calls=capture(monkeypatch,content='{"scenes":[],"questions":[]}')
    assert policy.STAGES['timeline']==('ui_video_timeline','v1','whole-timeline','qwen3.8-max',10000)
    trace={}
    assert ai.run_ai_skill('ui_video_timeline',p,image_assets=a,video_stage='timeline',runtime_trace=trace)=={'scenes':[],'questions':[]}
    assert trace['imageCount']==0
    body=calls[0]; assert body['payload']==p and body['imageAssets'][0]['base64']==a[0]['base64']
    assert a[0]['base64'] not in body['prompt'] and '未经核实' in body['prompt']
    from task_server.services.ui_video_stage_receipts import make_expected_attestation
    proof=make_expected_attestation(body)
    assert proof['effective_options']['image_count']==0 and proof['effective_options']['video_count']==1


def test_grounding_exact_jpegs_with_narrow_schema(monkeypatch):
    p,a=evidence();p.update(phase='whole-trajectory',reasoning_mode='bounded');s=semantic_schema(); calls=capture(monkeypatch)
    from pathlib import Path
    jpeg=base64.b64encode((Path(__file__).resolve().parents[1]/'assets/brand/kongfubean.jpeg').read_bytes()).decode()
    for asset in a:asset['base64']=jpeg
    s['properties']['events']['items']['properties']['target']['enum']=['visible']
    ai.ai_gateway_skill_content('ui_video_trajectory','ground',payload=p,image_assets=a,video_stage='grounding',version='observe',output_schema=s)
    assert calls[0]['outputSchema']==s and calls[0]['model']=='qwen3.8-max'
    for mutate in [lambda p,a:p.update(perception_bank={}),lambda p,a:p.update(perception_mode='fine-detail'),lambda p,a:a[0].update(name='forged')]:
        badp,bada=copy.deepcopy((p,a));mutate(badp,bada)
        with pytest.raises(ValueError,match='video_'):
            ai.ai_gateway_skill_content('ui_video_trajectory','ground',payload=badp,image_assets=bada,video_stage='grounding',version='observe',output_schema=s)


@pytest.mark.parametrize('stage',['timeline','grounding'])
def test_new_stage_null_schema_and_legacy_rejection(stage):
    p,a=native() if stage=='timeline' else evidence()
    if stage=='grounding':p.update(phase='whole-trajectory',reasoning_mode='bounded')
    with pytest.raises(ValueError,match='video_'):
        policy.validate_video_stage('ui_video_timeline' if stage=='timeline' else 'ui_video_trajectory',p,a,stage,None,version='v1' if stage=='timeline' else 'observe')
    p,a=native()
    with pytest.raises(ValueError,match='video_'):
        policy.validate_video_stage('ui_video_timeline',p,a)


def test_timeline_local_bounds():
    from task_server.services.ui_video_native import validate_timeline_output
    good={'scenes':[{'kind':'input','start_ms':0,'end_ms':10,'description':'field entry'}],'questions':[]}
    assert validate_timeline_output(good,10)==good
    for change in ['end','kind','extra','count']:
        bad=copy.deepcopy(good)
        if change=='end':bad['scenes'][0]['end_ms']=11
        if change=='kind':bad['scenes'][0]['kind']='aiTap'
        if change=='extra':bad['scenes'][0]['value']='private'
        if change=='count':bad['scenes']*=61
        with pytest.raises(ValueError):validate_timeline_output(bad,10)


def test_global_ipc_limit_stays_before_dispatch():
    from task_server.services.ui_video_model_jobs import _encode, MAX_REQUEST, ModelJobError
    p,a=native(12*1024*1024)
    with pytest.raises(ModelJobError,match='video_job_message_limit'):
        _encode({'payload':p,'kwargs':{'image_assets':a}},MAX_REQUEST)


@pytest.mark.parametrize('size',[32,12249942,12*1024*1024])
def test_native_modeljobs_owned_child_and_global_admission(tmp_path,size):
    from test_ui_video_model_jobs import Gateway, manager
    from task_server.services.ui_video_model_jobs import ModelJobError
    gateway=Gateway(content='{"scenes":[],"questions":[]}')
    p,a=native(size)
    try:
        with manager(tmp_path,gateway) as owner:
            if size==12*1024*1024:
                with pytest.raises(ModelJobError,match='video_job_message_limit'):
                    owner.run('ui_video_timeline',p,image_assets=a,video_stage='timeline')
                assert not gateway.calls and not owner.active_pids
            elif size==12249942:
                with pytest.raises(ModelJobError,match='video_job_failed'):
                    owner.run('ui_video_timeline',p,image_assets=a,video_stage='timeline')
                assert not gateway.calls and not owner.active_pids
            else:
                assert owner.run('ui_video_timeline',p,image_assets=a,video_stage='timeline')=={'scenes':[],'questions':[]}
                assert len(gateway.calls)==1 and gateway.calls[0]['payload']==p
                assert owner.accounting[0]['receipt_persisted'] and owner.accounting[0]['admitted']
                assert owner.accounting[0]['cleanup'] and not owner.active_pids
                assert owner.accounting[0]['receipt_ref']
    finally:
        gateway.close()


@pytest.mark.parametrize('change',['video_count','image_count','model','digest'])
def test_native_attestation_drift_rejected(change):
    from task_server.services.ui_video_stage_receipts import make_expected_attestation, validate_attestation
    p,a=native()
    body={'videoStage':'timeline','payload':p,'imageAssets':a,'prompt':'business','skillName':'ui_video_timeline','skillVersion':'v1','videoCorrelation':'a'*32}
    proof=make_expected_attestation(body)
    if change in {'video_count','image_count'}:proof['effective_options'][change]=0 if change=='video_count' else 1
    if change=='model':proof['model']='qwen3.8-flash'
    if change=='digest':proof['provider_options_sha256']='0'*64
    with pytest.raises(RuntimeError,match='video_stage_attestation_invalid'):validate_attestation(proof,body)


@pytest.mark.parametrize('mutate',[
    lambda x:x['scenes'][0].update(start_ms=True),
    lambda x:x['scenes'][0].update(end_ms=1.5),
    lambda x:x['scenes'][0].update(description='x'*2001),
    lambda x:x.update(questions=['?']*41),
    lambda x:x.update(questions=[{}]),
    lambda x:x.update(frame_ids=[]),
])
def test_timeline_strict_bounds_and_types(mutate):
    from task_server.services.ui_video_native import validate_timeline_output
    result={'scenes':[{'kind':'input','start_ms':0,'end_ms':10,'description':'entry'}],'questions':[]}
    mutate(result)
    with pytest.raises(ValueError,match='video_timeline_output_invalid'):validate_timeline_output(result,100)


def test_existing_bank_stage_resolution_remains_semantic():
    # Existing default runner resolves its stage by the first matching skill/version.
    assert next(k for k,v in policy.STAGES.items() if v[:2]==('ui_video_trajectory','observe'))=='semantic'


@pytest.mark.parametrize('change',['missing','overlap','unknown','order','metadata'])
def test_grounding_ownership_closed_before_transport(monkeypatch,change):
    p,a=evidence();p.update(phase='whole-trajectory',reasoning_mode='bounded');calls=capture(monkeypatch)
    if change=='missing':p.pop('owned_frame_ids')
    if change=='overlap':p['context_frame_ids'].append(p['owned_frame_ids'][0])
    if change=='unknown':p['owned_frame_ids'][0]='frame-999'
    if change=='order':p['owned_frame_ids'].reverse()
    if change=='metadata':p['frames'][0]['timestamp_ms']=1
    with pytest.raises(ValueError,match='video_'):
        ai.ai_gateway_skill_content('ui_video_trajectory','ground',payload=p,image_assets=a,video_stage='grounding',version='observe',output_schema=semantic_schema())
    assert not calls
