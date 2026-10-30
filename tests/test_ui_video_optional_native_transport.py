"""Closed optional transport disposition; no provider access or original mutation."""
import copy
import json
from pathlib import Path
import pytest
from task_server.services import ui_video_runtime as runtime
from task_server.services.ui_video_records import RecordStore
from task_server.services.ui_video_model_jobs import ModelJobError


def test_immutable_original_failure_receipt_is_optional_only_after_durable_cleanup(tmp_path):
    classify = getattr(runtime, '_native_transport_unavailable', None)
    assert callable(classify), 'missing verified optional native disposition'
    original=json.loads((Path(__file__).parent/'fixtures/video_optional_native_transport.json').read_text())['22']
    with RecordStore(tmp_path/'records') as store:
        proof=store.append('proof',original[0]['value']);receipt=store.append('receipt',original[1]['value'])
        row={**original[2]['value'],'proof_ref':proof,'receipt_ref':receipt}
        class Owner:
            accounting=[row];cleanup_failures=[];active_pids=[]
        out=classify(ModelJobError('video_job_failed'),Owner(),store)
        assert out['reason']=='temporary_provider_failure' and 'timeline' not in out
        assert store.read(out['receipt_ref'])['http_status']==500
        for field,value in [('cleanup',False),('admitted',True),('dispatch_intent',False),('proof_persisted',False)]:
            old=row[field];row[field]=value
            assert classify(ModelJobError('video_job_failed'),Owner(),store) is None
            row[field]=old
        assert classify(TimeoutError('video_job_deadline'),Owner(),store) is None

@pytest.mark.parametrize('size,allowed',[(7499997,True),(7499998,False),(12249942,False)])
def test_native_encoded_transport_limit_is_strict(tmp_path,size,allowed):
    from task_server.services.ui_video_native import load_native_video
    source=tmp_path/'source.mp4';source.write_bytes(b'\0\0\0\x18ftypisom'+b'x'*(size-12))
    if allowed:
        metadata,assets=load_native_video(source,1000)
        assert len(assets[0]['base64'])<10000000
    else:
        with pytest.raises(ValueError,match='video_native_asset_invalid'):load_native_video(source,1000)

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from task_server.services import ui_video_model_jobs as jm
from task_server.services import ui_video_trajectory as trajectory, ui_video_windows as windows
from task_server.services import ui_video_analysis as analysis, ui_video_business_inputs as inputs
from task_server.services.ui_video_stage_receipts import make_expected_attestation
from test_ui_video_trajectory import frames, event


@pytest.mark.parametrize('native_status,later_failure',[(429,False),(500,False),(503,True),('deadline',False),('oversize',False)])
def test_real_owned_native_failure_then_actual_independent_audit(tmp_path,monkeypatch,native_status,later_failure):
    calls=[];owners=[];published=[]
    (tmp_path/'frames').mkdir()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));stage=body['videoStage'];calls.append(body)
            if stage=='timeline' or (stage=='audit' and later_failure):
                if native_status=='deadline':
                    time.sleep(4);return
                status=native_status if stage=='timeline' else 503
                data={'error':'video_stage_provider_failed'}
            else:
                status=200
                if stage=='audit':
                    raw={'checks':[{'event_id':c['track']['event_id'],'trigger_verdict':'supported','outcome_verdict':'supported','question':''} for c in body['payload']['trajectory']], 'issues':[],'multiple_scenarios':False,'revisit':[]}
                else:
                    raw={'states':[],'events':[event(['frame-001','frame-002'])] if stage=='grounding' and 'frame-001' in body['payload']['owned_frame_ids'] else [],'checks':[], 'uncertainties':[],'multiple_scenarios':False,'revisit':[]}
                data={'success':True,'content':json.dumps(raw),'providerId':'official','model':body['model'],'fallbackUsed':False,'finishReason':'stop','usage':{},'videoPolicy':make_expected_attestation(body)}
            encoded=json.dumps(data).encode()
            try:
                self.send_response(status);self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    real=jm.ModelJobs
    class Owner(real):
        def __init__(self,*a,**kw):
            super().__init__(*a,**kw,gateway_url=f'http://127.0.0.1:{server.server_port}');owners.append(self)
        def run(self,skill,payload=None,**kw):
            if native_status=='deadline' and kw.get('video_stage')=='timeline':kw['timeout']=3
            return super().run(skill,payload,**kw)
    monkeypatch.setattr(jm,'ModelJobs',Owner)
    monkeypatch.setattr(windows,'load_assets',lambda fs,*a:[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':'YQ=='} for f in fs])
    monkeypatch.setattr(analysis,'consolidate_observed_frames',lambda doc,root,cancelled,observed,*a,**kw:observed)
    monkeypatch.setattr(inputs,'recover_business_inputs',lambda result,*a,**kw:result)
    path=tmp_path/'source.mp4';size=12249942 if native_status=='oversize' else 32
    path.write_bytes(b'\0\0\0\x18ftypisom'+b'x'*(size-12));doc={'media':{'frames':frames(299 if native_status=='oversize' else 3),'duration_ms':75376 if native_status=='oversize' else 1000}}
    budget=runtime.RuntimeBudget(0)
    try:
        def run():return runtime.run_runtime(doc,tmp_path,path,lambda:None,lambda **kw:published.append(copy.deepcopy(kw)),budget=budget,mode=runtime.BUSINESS_MODE)
        if later_failure:
            with pytest.raises(Exception):run()
            result=published[-1]
        else:result=run()
        stages=[b['videoStage'] for b in calls]
        assert stages==(['timeline','overview','grounding','audit'] if native_status!='oversize' else ['overview']+['grounding','audit']*10)
        assert all(not o.active_pids and o._closed for o in owners)
        if native_status!='oversize':
            native=owners[1];assert native._latched
            with pytest.raises(jm.ModelJobError,match='latched'):native._guard()
            assert native.accounting[0]['cleanup'] and not native.accounting[0]['admitted']
        marker=result['video_runtime_private']['business_timeline']
        assert marker['classification']=='native_hypotheses_unavailable' and 'timeline' not in marker
        assert all(b['payload']['business_timeline']==marker for b in calls if b['videoStage'] in ('overview','grounding'))
        assert 'business_timeline' not in calls[-1]['payload']
        assert budget.reserved==len(calls)
        with RecordStore.open_snapshot(tmp_path/'model-records',result['video_runtime_private']['records']) as records:
            rows=[records.read(ref) for ref in records.read(result['video_runtime_private']['accounting_ref'])['refs']]
            assert len(rows)==len(calls) and all(r['terminal'] and r['cleanup'] for r in rows)
            assert len({r['index'] for r in rows})==len(rows)
        if native_status=='oversize':
            assert result['media']['frames']==doc['media']['frames'] and result['media']['duration_ms']==75376
            assert marker['source_bytes']==12249942 and marker['encoded_characters']==16333256
            assert not {'proof_ref','receipt_ref'} & marker.keys()
    finally:server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('mutation', ['status','rejection','hash','stage','proof','receipt','cleanup','active','model','policy','cancel','revocation','source_deadline','schema'])
def test_durable_failure_integrity_and_security_do_not_qualify(tmp_path,mutation):
    original=json.loads((Path(__file__).parent/'fixtures/video_optional_native_transport.json').read_text())['22']
    proof=copy.deepcopy(original[0]['value']);receipt=copy.deepcopy(original[1]['value'])
    if mutation=='status':receipt['http_status']=400
    if mutation=='rejection':receipt['rejection_code']='video_stage_actual_model_invalid'
    if mutation=='hash':receipt['gateway_request_sha256']='0'*64
    if mutation=='stage':proof.update(stage='overview',skill='ui_video_trajectory',skill_version='overview')
    if mutation=='model':receipt['model']='qwen3.8-flash'
    if mutation=='policy':receipt['videoPolicy']={'stage':'timeline'}
    with RecordStore(tmp_path/'records') as store:
        row={**original[2]['value'],'proof_ref':store.append('proof',proof),'receipt_ref':store.append('receipt',receipt)}
        class Owner:
            accounting=[row];active_pids=[];cleanup_failures=[]
        if mutation=='proof':row.pop('proof_ref')
        if mutation=='receipt':row.pop('receipt_ref')
        if mutation=='cleanup':Owner.cleanup_failures=[{'index':0,'operations':['wait']}]
        if mutation=='active':Owner.active_pids=[123]
        error={'cancel':jm.ModelJobCancelled('video_job_cancelled'),'revocation':PermissionError('revoked'),
               'source_deadline':TimeoutError('video_runtime_deadline'),'schema':ValueError('schema')}.get(mutation,ModelJobError('video_job_failed'))
        assert runtime._native_transport_unavailable(error,Owner(),store) is None


def test_original23_requires_actual_local_deadline_and_no_receipt(tmp_path):
    original=json.loads((Path(__file__).parent/'fixtures/video_optional_native_transport.json').read_text())['23']
    with RecordStore(tmp_path/'records') as store:
        row={**original[1]['value'],'proof_ref':store.append('proof',original[0]['value'])}
        class Owner:
            accounting=[row];active_pids=[];cleanup_failures=[]
        assert runtime._native_transport_unavailable(TimeoutError('video_job_deadline'),Owner(),store) is None
        assert runtime._native_transport_unavailable(jm.LocalModelJobDeadline('video_job_deadline'),Owner(),store)['reason']=='local_job_deadline'
        row['raw_received']=True
        assert runtime._native_transport_unavailable(jm.LocalModelJobDeadline('video_job_deadline'),Owner(),store) is None


def test_oversize_malformed_original_cannot_use_preflight_skip(tmp_path):
    from task_server.services.ui_video_native import prepare_optional_native_video
    source=tmp_path/'source.mp4';source.write_bytes(b'x'*12249942)
    with pytest.raises(ValueError,match='video_native_asset_invalid'):
        prepare_optional_native_video(source,75376)
