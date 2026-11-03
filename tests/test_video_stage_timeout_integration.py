"""Deadline boundaries through real envelopes, HTTP wire, and retry admission."""
import json
import time
import pytest
from task_server.services import ui_video_model_jobs as jm
from task_server.services.ui_video_records import RecordStore
from test_ui_video_business_jobs import scripted, invoke
from test_ui_video_stage_runtime import request

@pytest.mark.parametrize('stage,requested,outer,expected',[
 ('audit',999,1000,180),('audit',35,1000,35),('audit',180,55,54.5),
 ('perception',180,1000,120),('semantic',180,1000,120)])
def test_actual_parent_envelope_clips_stage_request_and_outer(tmp_path,monkeypatch,stage,requested,outer,expected):
    with RecordStore(tmp_path/'records') as records:
        owner=jm.ModelJobs(records,deadline=100+outer,reserve=lambda:None,check=lambda:True)
        monkeypatch.setattr(jm.time,'monotonic',lambda:100)
        monkeypatch.setattr(owner,'_launch',lambda:(None,None))
        skill,kw=request(stage);payload=kw.pop('payload');kw['timeout']=requested
        owner._start(lambda:(skill,payload,kw),0)
        job=owner._active.pop()
        envelope=json.loads(bytes(job['send'])[4:])
        assert job['deadline']==100+expected
        assert envelope['deadline']==job['deadline']
        assert envelope['kwargs']['timeout']==expected
        assert envelope['kwargs']['retry_count']==0

@pytest.mark.parametrize('stage,headroom,attempts',[
 ('audit',120.5,1),('audit',180.499,1),('audit',180.5,2),
 ('grounding',180.499,1),('grounding',180.5,2),('overview',120.5,2)])
def test_business_retry_admits_actual_stage_budget_without_extending_clock(tmp_path,stage,headroom,attempts):
    records,budget,jobs,owners,calls=scripted(tmp_path,['503','ok'],headroom=headroom)
    try:
        with jobs:
            if attempts==1:
                with pytest.raises(jm.ModelJobError):invoke(jobs,stage=stage)
            else:invoke(jobs,stage=stage)
        assert len(calls)==budget.reserved==attempts
        assert budget.deadline==headroom
        assert all(o._closed for o in owners)
    finally:records.close()

def test_business_two_replacements_never_third_even_with_180_stage(tmp_path):
    records,budget,jobs,owners,calls=scripted(tmp_path,['503','ok','deadline','ok','503','ok'])
    original=budget.deadline
    try:
        with jobs:
            invoke(jobs,stage='audit');invoke(jobs,stage='grounding')
            with pytest.raises(jm.ModelJobError):invoke(jobs,stage='audit')
        assert len(jobs.retries)==2 and len(calls)==budget.reserved==5
        assert budget.deadline==original and all(o._closed for o in owners)
    finally:records.close()

@pytest.mark.parametrize('stage,requested,cap',[('audit',240,180),('audit',25,25),('perception',240,120)])
def test_ai_inner_wire_timeout_and_attestation_remain_consistent(monkeypatch,stage,requested,cap):
    import io
    from task_server.services import ai_skill_service as ai
    from task_server.services.ui_video_stage_receipts import make_expected_attestation
    skill,kw=request(stage);bodies=[];receipts=[]
    def http(req,timeout):
        body=json.loads(req.data);bodies.append(body)
        assert cap-.1 < timeout <= cap
        assert cap*1000-100 <= body['timeoutMs'] <= cap*1000
        attestation=make_expected_attestation(body)
        return io.BytesIO(json.dumps({'success':True,'content':'raw','model':body['model'],
            'fallbackUsed':False,'finishReason':'stop','videoPolicy':attestation}).encode())
    monkeypatch.setattr(ai.urllib.request,'urlopen',http)
    assert ai.ai_gateway_skill_content(skill,'pixels',**kw,timeout=requested,raw_receipt=receipts.append)=='raw'
    assert len(bodies)==len(receipts)==1
    assert receipts[0]['videoPolicy']==make_expected_attestation(bodies[0])

@pytest.mark.parametrize('stage',['grounding','audit'])
def test_real_store_owned_child_180_http_receipt_and_cleanup(tmp_path,stage):
    from test_ui_video_model_jobs import Gateway
    from test_ui_video_stage_wire import semantic_schema
    skill,kw=request('audit');payload=kw.pop('payload')
    if stage=='grounding':
        kw.update(video_stage=stage,version='observe',output_schema=semantic_schema())
        content={'states':[],'events':[],'checks':[],'uncertainties':[],'multiple_scenarios':False,'revisit':[]}
    else:content={'checks':[],'issues':[],'multiple_scenarios':False,'revisit':[]}
    gateway=Gateway(json.dumps(content));reserved=[]
    try:
        with RecordStore(tmp_path/'records') as records:
            with jm.ModelJobs(records,deadline=time.monotonic()+300,reserve=lambda:reserved.append(1),
                    check=lambda:True,gateway_url=gateway.url) as owner:
                owner.run(skill,payload,**kw,timeout=180)
                assert len(gateway.calls)==len(reserved)==1
                row=owner.accounting[0]
                proof=records.read(row['proof_ref']);receipt=records.read(row['receipt_ref'])
                assert 120000<proof['timeout_ms']<=180000
                assert gateway.calls[0]['timeoutMs']==proof['timeout_ms']
                assert receipt['content']==json.dumps(content)
                assert all(row[k] for k in ('proof_persisted','dispatch_intent','receipt_persisted','admitted','cleanup'))
                assert not owner.active_pids
    finally:gateway.close()

@pytest.mark.parametrize('stage,timeout',[('perception',120001),('overview',120001),('semantic',120001),('timeline',120001),('grounding',180001),('audit',180001)])
def test_real_store_rejects_stage_timeout_broadening(tmp_path,stage,timeout):
    from task_server.services.ui_video_records import RecordError
    from task_server.services.ui_video_stage_policy import STAGES
    proof=dict(stage=stage,skill=STAGES[stage][0],skill_version=STAGES[stage][1],
        correlation='a'*32,gateway_request_sha256='a'*64,timeout_ms=timeout)
    with RecordStore(tmp_path/'records') as records:
        with pytest.raises(RecordError):records.append('proof',proof)
