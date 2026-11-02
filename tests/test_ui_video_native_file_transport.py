"""Original-file capability and ephemeral attestation; all provider calls mocked."""
import base64
import hashlib
import json
import time
import pytest
from task_server.services import ui_video_native as native
from task_server.services import ui_video_stage_receipts as proof

TRANSPORT = 'dashscope-temporary-original-v1'


def source(size):
    return b'\0\0\0\x18ftypisom' + b'x' * (size - 12)


def body_for(raw, *, file=True):
    return {'videoStage':'timeline','skillName':'ui_video_timeline','skillVersion':'v1',
        'videoCorrelation':'a'*32,'videoDeadlineMs':int(time.time()*1000)+120000,'timeoutMs':120000,
        'prompt':'original timeline','imageAssets':[{'name':'source-video','mime':'video/mp4',
        'timestamp_ms':0,'base64':base64.b64encode(raw).decode()}],
        'payload':{'phase':'whole-timeline','video':{'source_sha256':hashlib.sha256(raw).hexdigest(),
        'source_bytes':len(raw),'duration_ms':75376,**native.NATIVE_OPTIONS,
        **({'transport':TRANSPORT} if file else {})}}}


def envelope(body):
    raw=base64.b64decode(body['imageAssets'][0]['base64']);uri='oss://dashscope-instant/account/path/'+body['videoCorrelation']+'.mp4'
    return {'version':TRANSPORT,'uri':uri,'source_sha256':hashlib.sha256(raw).hexdigest(),
        'source_bytes':len(raw),'source_md5':hashlib.md5(raw).hexdigest(),'model':'qwen3.8-max',
        'correlation':body['videoCorrelation'],'resource_reference_sha256':hashlib.sha256(uri.encode()).hexdigest(),
        'uploaded_at_ms':int(time.time()*1000),'documented_max_lifetime_seconds':172800,
        'private_acl':True,'forbid_overwrite':True,'resolve_header':'enable'}


def test_original_encoded_over_limit_selects_file_without_mutation(tmp_path):
    raw=source(12249942);path=tmp_path/'original.mp4';path.write_bytes(raw)
    meta,assets,unavailable=native.prepare_optional_native_video(path,75376)
    assert unavailable is None and meta['transport']==TRANSPORT
    assert base64.b64decode(assets[0]['base64'])==raw and len(assets[0]['base64'])==16333256
    native.validate_native_asset({'phase':'whole-timeline','video':meta},assets)


def test_small_legacy_and_explicit_file_scope():
    body=body_for(source(32),file=False)
    body.pop('timeoutMs')
    native.validate_native_asset(body['payload'],body['imageAssets'])
    assert 'native_transport' not in proof.make_expected_attestation(body)
    for mode in (TRANSPORT,'unknown'):
        body['payload']['video']['transport']=mode
        with pytest.raises(ValueError):native.validate_native_asset(body['payload'],body['imageAssets'])


def test_exact_file_wire_policy_and_no_uri_projection():
    body=body_for(source(7499998));resource=envelope(body)
    options=proof.expected_provider_options(body,native_resource=resource)
    assert options['messages'][1]['content'][0]['video_url']['url']==resource['uri']
    policy=proof.make_expected_attestation(body,native_resource=resource)
    assert policy['provider_options_sha256']==proof.canonical_sha(options)
    assert policy['native_transport']=={**{k:v for k,v in resource.items() if k!='uri'},
        'provider_headers_sha256':proof.canonical_sha({'X-DashScope-OssResourceResolve':'enable'})}
    proof.validate_attestation(policy,body,native_resource=resource)
    assert proof.safe_attestation({**policy,'uri':resource['uri']})==policy
    assert resource['uri'] not in json.dumps(policy)


@pytest.mark.parametrize('key,value',[
 ('uri','https://evil.example/a'),('uri','oss://dashscope-instant/a/../'+'a'*32+'.mp4'),
 ('uri','oss://dashscope-instant/a/'+'a'*32+'.mp4?secret=x'),('source_sha256','0'*64),
 ('source_bytes',True),('source_md5','0'*32),('model','qwen3.8-flash'),('correlation','b'*32),
 ('resource_reference_sha256','0'*64),('uploaded_at_ms',0),('uploaded_at_ms',True),
 ('documented_max_lifetime_seconds',172801),('private_acl',1),('forbid_overwrite',False),
 ('resolve_header','disabled'),('extra','secret')])
def test_forged_envelope_rejected(key,value):
    body=body_for(source(7499998));resource=envelope(body)
    policy=proof.make_expected_attestation(body,native_resource=resource)
    resource[key]=value
    with pytest.raises(RuntimeError,match='video_stage_attestation_invalid'):
        proof.validate_attestation(policy,body,native_resource=resource)


def test_missing_and_legacy_resource_rejected():
    body=body_for(source(7499998));resource=envelope(body)
    with pytest.raises((ValueError,RuntimeError)):proof.make_expected_attestation(body)
    legacy=body_for(source(32),file=False)
    with pytest.raises((ValueError,RuntimeError)):proof.make_expected_attestation(legacy,native_resource=resource)


def test_safe_failed_policy_projection_is_closed():
    body=body_for(source(7499998));resource=envelope(body)
    safe={k:v for k,v in resource.items() if k!='uri'}
    safe.update(uri=resource['uri'],signed_policy='secret',resolve_header=resource['uri'])
    projected=proof.safe_attestation({'native_transport':safe})
    assert projected['native_transport']['source_md5']==resource['source_md5']
    assert not {'uri','signed_policy','resolve_header'} & projected['native_transport'].keys()


@pytest.mark.parametrize('tampered',[False,True])
def test_maximum_original_crosses_real_owned_ipc_and_durable_receipt(tmp_path,tampered):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from task_server.services import ui_video_model_jobs as jobs
    from task_server.services.ui_video_records import RecordStore
    calls=[];resources=[];raw_reply='{"scenes":[],"questions":["still unverified"]}'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));calls.append(body)
            resource=envelope(body);resources.append(resource)
            policy=proof.make_expected_attestation(body,native_resource=resource)
            if tampered:resource['source_md5']='0'*32
            response={'success':True,'content':raw_reply,'providerId':'official','model':'qwen3.8-max',
                'fallbackUsed':False,'finishReason':'stop','usage':{},'videoPolicy':policy,'nativeResource':resource}
            encoded=json.dumps(response).encode();self.send_response(200)
            self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    raw=source(native.MAX_NATIVE_BYTES);body=body_for(raw)
    store=RecordStore(tmp_path/'records')
    try:
        with jobs.ModelJobs(store,deadline=time.monotonic()+25,reserve=lambda:None,check=lambda:None,
                gateway_url=f'http://127.0.0.1:{server.server_port}') as owner:
            def run():
                return owner.run('ui_video_timeline',body['payload'],image_assets=body['imageAssets'],
                    video_stage='timeline',retry_count=0,repair_invalid_json=False,timeout=20)
            if tampered:
                with pytest.raises(jobs.ModelJobError):run()
            else:assert run()==json.loads(raw_reply)
            row=owner.accounting[0]
            assert row['receipt_persisted'] and row['admitted'] is (not tampered) and row['cleanup']
            assert not owner.active_pids
            receipt=store.read(row['receipt_ref'])
            assert receipt['content']==raw_reply and receipt['videoPolicy']['native_transport']['source_bytes']==len(raw)
            assert resources[0]['uri'] not in json.dumps(receipt)
            assert 'nativeResource' not in json.dumps(receipt)
        assert len(calls)==1 and base64.b64decode(calls[0]['imageAssets'][0]['base64'])==raw
        assert len(json.dumps(calls[0]))>16*1024*1024
        assert jobs.MAX_REQUEST==20*1024*1024
    finally:
        store.close();server.shutdown();server.server_close();thread.join()


def test_image_budget_remains_bounded():
    from task_server.services.ui_video_stage_policy import validate_video_stage
    from test_ui_video_stage_runtime import request
    skill,kwargs=request('overview');kwargs['image_assets'][0]['base64']='YQ=='*(10*1024*1024//4+1)
    with pytest.raises(ValueError):
        validate_video_stage(skill,kwargs['payload'],kwargs['image_assets'],'overview',version='overview')


@pytest.mark.parametrize('mutation',['null_mode','unknown_mode','other_stage','header_digest','policy_extra','typed_flag'])
def test_mode_scope_and_full_header_attestation_fail_closed(mutation):
    body=body_for(source(7499998));resource=envelope(body)
    policy=proof.make_expected_attestation(body,native_resource=resource)
    if mutation=='null_mode':
        body['payload']['video']['transport']=None;resource=None
    elif mutation=='unknown_mode':body['payload']['video']['transport']='unknown'
    elif mutation=='other_stage':body['videoStage']='overview'
    elif mutation=='header_digest':policy['native_transport']['provider_headers_sha256']='0'*64
    elif mutation=='policy_extra':policy['native_transport']['uri']=resource['uri']
    else:policy['native_transport']['private_acl']=1
    with pytest.raises(RuntimeError,match='video_stage_attestation_invalid'):
        proof.validate_attestation(policy,body,native_resource=resource)


@pytest.mark.parametrize('offset',[-121001,1])
def test_upload_time_outside_request_window_rejected(offset):
    body=body_for(source(7499998));resource=envelope(body)
    policy=proof.make_expected_attestation(body,native_resource=resource)
    resource['uploaded_at_ms']=body['videoDeadlineMs']+offset
    with pytest.raises(RuntimeError,match='video_stage_attestation_invalid'):
        proof.validate_attestation(policy,body,native_resource=resource)


def test_original_maximum_does_not_relax_raw_bound(tmp_path):
    path=tmp_path/'too-large.mp4';path.write_bytes(source(native.MAX_NATIVE_BYTES+1))
    with pytest.raises(ValueError,match='video_native_asset_invalid'):
        native.prepare_optional_native_video(path,75376)


def test_non_timeline_incidental_video_metadata_preserves_legacy_policy():
    from test_ui_video_stage_runtime import request
    skill,kwargs=request('overview');kwargs['payload']['video']='metadata-string'
    body={'videoStage':'overview','skillName':skill,'skillVersion':'overview','videoCorrelation':'a'*32,
        'payload':kwargs['payload'],'imageAssets':kwargs['image_assets'],'prompt':'pixels'}
    policy=proof.make_expected_attestation(body)
    proof.validate_attestation(policy,body)
    assert 'native_transport' not in policy
    with pytest.raises(RuntimeError,match='video_stage_attestation_invalid'):
        proof.validate_attestation(policy,body,native_resource={})


def test_upload_future_time_is_not_accepted_within_deadline():
    body=body_for(source(7499998));resource=envelope(body)
    policy=proof.make_expected_attestation(body,native_resource=resource)
    resource['uploaded_at_ms']=int(time.time()*1000)+30000
    policy['native_transport']['uploaded_at_ms']=resource['uploaded_at_ms']
    with pytest.raises(RuntimeError,match='video_stage_attestation_invalid'):
        proof.validate_attestation(policy,body,native_resource=resource)


@pytest.mark.parametrize('timeout_ms',[1,20000,120000])
def test_fresh_upload_matches_actual_request_timeout(monkeypatch,timeout_ms):
    now_ms=1800000000000;monkeypatch.setattr(proof.time,'time',lambda:now_ms/1000)
    body=body_for(source(7499998));resource=envelope(body)
    body.update(timeoutMs=timeout_ms,videoDeadlineMs=now_ms+timeout_ms)
    policy=proof.make_expected_attestation(body,native_resource=resource)
    proof.validate_attestation(policy,body,native_resource=resource)


def test_short_request_rejects_matched_upload_predating_request(monkeypatch):
    now_ms=1800000000000;monkeypatch.setattr(proof.time,'time',lambda:now_ms/1000)
    body=body_for(source(7499998));resource=envelope(body)
    body.update(timeoutMs=20000,videoDeadlineMs=now_ms+20000)
    policy=proof.make_expected_attestation(body,native_resource=resource)
    resource['uploaded_at_ms']=now_ms-90000
    policy['native_transport']['uploaded_at_ms']=resource['uploaded_at_ms']
    with pytest.raises(RuntimeError,match='video_stage_attestation_invalid'):
        proof.validate_attestation(policy,body,native_resource=resource)


@pytest.mark.parametrize('offset',[-1000,-1001])
def test_request_start_clock_tolerance_boundary(monkeypatch,offset):
    now_ms=1800000000000;monkeypatch.setattr(proof.time,'time',lambda:now_ms/1000)
    body=body_for(source(7499998));resource=envelope(body)
    body.update(timeoutMs=20000,videoDeadlineMs=now_ms+20000)
    policy=proof.make_expected_attestation(body,native_resource=resource)
    resource['uploaded_at_ms']=now_ms+offset
    policy['native_transport']['uploaded_at_ms']=resource['uploaded_at_ms']
    if offset==-1000:
        proof.validate_attestation(policy,body,native_resource=resource)
    else:
        with pytest.raises(RuntimeError,match='video_stage_attestation_invalid'):
            proof.validate_attestation(policy,body,native_resource=resource)


@pytest.mark.parametrize('timeout_ms',[None,True,False,20000.0,'20000',0,-1,120001])
def test_native_file_timeout_requires_bounded_integer(timeout_ms):
    body=body_for(source(7499998));resource=envelope(body)
    policy=proof.make_expected_attestation(body,native_resource=resource)
    body['timeoutMs']=timeout_ms
    with pytest.raises(RuntimeError,match='video_stage_attestation_invalid'):
        proof.validate_attestation(policy,body,native_resource=resource)


def test_native_file_timeout_is_required():
    body=body_for(source(7499998));resource=envelope(body)
    policy=proof.make_expected_attestation(body,native_resource=resource)
    body.pop('timeoutMs')
    with pytest.raises(RuntimeError,match='video_stage_attestation_invalid'):
        proof.validate_attestation(policy,body,native_resource=resource)


@pytest.mark.parametrize('offset',[-121000,-121001])
def test_upload_absolute_age_bound_with_older_request_deadline(monkeypatch,offset):
    now_ms=1800000000000;monkeypatch.setattr(proof.time,'time',lambda:now_ms/1000)
    body=body_for(source(7499998));resource=envelope(body)
    policy=proof.make_expected_attestation(body,native_resource=resource)
    body['videoDeadlineMs']=now_ms-2000
    resource['uploaded_at_ms']=now_ms+offset
    policy['native_transport']['uploaded_at_ms']=resource['uploaded_at_ms']
    if offset==-121000:
        proof.validate_attestation(policy,body,native_resource=resource)
    else:
        with pytest.raises(RuntimeError,match='video_stage_attestation_invalid'):
            proof.validate_attestation(policy,body,native_resource=resource)
