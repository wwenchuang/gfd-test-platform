"""Business-only bounded retries: real owned subprocesses and durable controls."""
import copy
import importlib
import json
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from task_server.services import ui_video_model_jobs as jm
from task_server.services.ui_video_runtime import RuntimeBudget
from task_server.services.ui_video_records import RecordStore
from task_server.services.ui_video_stage_policy import STAGES
from task_server.services.ui_video_stage_receipts import make_expected_attestation
from test_ui_video_stage_runtime import request


def session_class():
    from task_server.services import __path__
    from pathlib import Path
    assert (Path(list(__path__)[0])/'ui_video_business_jobs.py').exists(), 'missing bounded business owner session'
    return importlib.import_module('task_server.services.ui_video_business_jobs').BusinessJobs


class Gateway:
    def __init__(self,failure):
        self.calls=[];self.closed=threading.Event();outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));outer.calls.append(body)
                if len(outer.calls)==1 and failure=='deadline':
                    while not select.select([self.connection],[],[],.05)[0]:pass
                    if not self.connection.recv(1,socket.MSG_PEEK):outer.closed.set()
                    return
                if len(outer.calls)==1 or failure=='twice' and len(outer.calls)==2:
                    status=503;value={'error':'video_stage_provider_failed'}
                else:
                    status=200
                    from test_ui_video_trajectory import event
                    raw=({'checks':[{'event_id':c['track']['event_id'],'trigger_verdict':'supported','outcome_verdict':'supported','question':''} for c in body['payload'].get('trajectory',[])],'issues':[],'multiple_scenarios':False,'revisit':[]} if body['videoStage']=='audit' else
                         {'states':[],'events':[event(['frame-001','frame-002'])] if body['videoStage']=='grounding' else [],'checks':[],'uncertainties':[],'multiple_scenarios':False,'revisit':[]})
                    value={'success':True,'content':json.dumps(raw),'providerId':'official','model':body['model'],'fallbackUsed':False,'finishReason':'stop','usage':{},'videoPolicy':make_expected_attestation(body)}
                data=json.dumps(value).encode()
                try:
                    self.send_response(status);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()


def call_options(stage='overview'):
    skill,kw=request(stage);payload=kw.pop('payload');kw['model_config']={'providerId':'qwen_plus','model':STAGES[stage][3]}
    return skill,payload,kw


@pytest.mark.parametrize('failure',['503','deadline','twice'])
def test_real_failed_owner_replacement_then_independent_audit(tmp_path,failure):
    Session=session_class();gateway=Gateway(failure);owners=[];budget=RuntimeBudget(0,business=True);trace={}
    class Owner(jm.ModelJobs):
        def __init__(self,*a,**kw):
            assert all(o._closed and not o.active_pids for o in owners)
            super().__init__(*a,**kw,gateway_url=f'http://127.0.0.1:{gateway.server.server_port}');owners.append(self)
        def _step(self):
            # Expire the actual parent clock after durable proof; avoid a race
            # against the child's own urllib timeout while retaining real IPC.
            if failure=='deadline' and len(owners)==1:
                for job in self._active:
                    if job['row']['dispatch_intent'] and gateway.calls:job['deadline']=time.monotonic()-.001
            return super()._step()
    try:
        with RecordStore(tmp_path/'records') as records:
            with Session(records,budget=budget,check=budget.check,owner_factory=Owner) as jobs:
                skill,payload,kw=call_options();original=copy.deepcopy((payload,kw))
                if failure=='twice':
                    with pytest.raises(jm.ModelJobError):jobs.run(skill,payload,**kw,runtime_trace=trace)
                    assert len(gateway.calls)==2 and len(owners)==2
                    assert trace['video_record_refs']['proof_ref']==jobs.accounting[-1]['proof_ref']
                else:
                    jobs.run(skill,payload,**kw,runtime_trace=trace)
                    assert records.read(trace['video_record_refs']['proof_ref'])['stage']=='overview'
                    assert trace['video_record_refs']['proof_ref']==jobs.accounting[-1]['proof_ref']
                    assert 'error' not in trace
                    skill,audit,opts=call_options('audit');jobs.run(skill,audit,**opts)
                    assert [c['videoStage'] for c in gateway.calls]==['overview','overview','audit']
                    a,b=gateway.calls[:2]
                    assert all(a[k]==b[k] for k in ('payload','imageAssets','model','outputSchema') if k in a)
                    assert a['videoCorrelation']!=b['videoCorrelation']
                assert (payload,kw)==original
                assert budget.reserved==len(gateway.calls)
                assert owners[0]._closed and owners[0]._latched and not owners[0].active_pids
                with pytest.raises(jm.ModelJobError,match='latched'):owners[0]._guard()
            assert all(o._closed and not o.active_pids for o in owners)
            assert all(row['terminal'] and row['cleanup'] for row in jobs.accounting)
            assert len(jobs.retries)==1 and jobs.retries[0]['reason'] in ('local_job_deadline','temporary_provider_failure')
            refs=[records.append('status',{**row,'index':i}) for i,row in enumerate(jobs.accounting)]
            assert len(set(r['entry'] for r in refs))==len(jobs.accounting)
            assert all(records.read(row['proof_ref']) for row in jobs.accounting)
    finally:gateway.close()


def scripted(tmp_path,script,*,mutation=None,headroom=None,revoked=False):
    records=RecordStore(tmp_path/'records');budget=RuntimeBudget(0,business=True,clock=lambda:0.)
    if headroom is not None:budget.deadline=headroom
    owners=[];calls=[]
    class Owner:
        def __init__(self,records,**kw):
            assert all(o._closed for o in owners)
            self.records,self.kw=records,kw;self.accounting=[];self._closed=False;self._latched=False
            self.active_pids=[];self.cleanup_failures=[];self.pending_receipts=[];owners.append(self)
        def __enter__(self):return self
        def __exit__(self,*a):self._closed=True
        def run(self,skill,payload,**kw):
            self.kw['check']();self.kw['reserve']();calls.append(copy.deepcopy((skill,payload,kw)))
            outcome=script.pop(0);stage=kw['video_stage'];sha='a'*64
            proof={'stage':stage,'skill':skill,'skill_version':STAGES[stage][1],'correlation':'a'*32,'gateway_request_sha256':sha,'timeout_ms':120000}
            if mutation=='stage':proof.update(stage='audit',skill='ui_video_trajectory',skill_version='audit')
            row={'index':len(self.accounting),'reserved':True,'proof_persisted':True,'dispatch_intent':True,
                 'raw_received':False,'receipt_persisted':False,'admitted':False,'terminal':True,'cleanup':True,
                 'transport_status':'unknown','events':['reserved','proof_persisted','dispatch_intent','cleanup'],
                 'proof_ref':records.append('proof',proof)}
            self.accounting.append(row)
            if outcome in ('ok','503'):
                receipt={'content':'' if outcome=='503' else '{}','gateway_request_sha256':sha}
                if outcome=='503':receipt.update(http_status=503,rejection_code='video_stage_provider_failed')
                if mutation=='status':receipt['http_status']=400
                if mutation=='rejection':receipt['rejection_code']='video_stage_actual_model_invalid'
                if mutation=='hash':receipt['gateway_request_sha256']='b'*64
                if mutation=='model':receipt['model']='qwen3.8-max'
                if mutation=='policy':receipt['videoPolicy']={'stage':stage}
                if mutation=='content':receipt['content']='failed model content'
                row.update(raw_received=True,receipt_persisted=True,transport_status='receipt_received',receipt_ref=records.append('receipt',receipt))
            if mutation=='proof':row.pop('proof_ref')
            if mutation=='cleanup':self.cleanup_failures.append({'index':0,'operations':['wait']})
            if mutation=='active':self.active_pids.append(12)
            if mutation=='two_rows':self.accounting.append(copy.deepcopy(row))
            if mutation=='raw':row['raw_received']=True
            if mutation=='pending':self.pending_receipts.append({})
            if 'raw_receipt' in kw:kw['raw_receipt']({})
            payload['mutated_by_failed_attempt']=True
            if outcome=='ok':
                row['admitted']=True;kw['runtime_trace'].update(model=STAGES[stage][3]);return {'ok':True}
            kw['runtime_trace']['error']='video_job_failed';self._latched=True
            if outcome=='deadline':raise jm.LocalModelJobDeadline('video_job_deadline')
            if outcome=='child':raise TimeoutError('video_job_deadline')
            if outcome=='cancel':raise jm.ModelJobCancelled('video_job_cancelled')
            if outcome=='schema':
                from task_server.services.ai_skill_service import SkillSchemaError
                self._latched=False;raise SkillSchemaError('$','invalid')
            if outcome=='json':raise ValueError('invalid json')
            if outcome=='unknown':raise RuntimeError('video_job_failed')
            raise jm.ModelJobError('video_job_failed')
    def check():
        budget.check()
        if revoked and any(o._closed for o in owners):
            if revoked=='false':return False
            raise PermissionError('revoked')
    jobs=session_class()(records,budget=budget,check=check,owner_factory=Owner)
    return records,budget,jobs,owners,calls


def invoke(jobs,trace=None,stage='overview',**extra):
    return jobs.run(STAGES[stage][0],{'unchanged':['original']},version=STAGES[stage][1],video_stage=stage,
        image_assets=[{'jpeg':'original'}],output_schema={'original':True},
        model_config={'providerId':'qwen_plus','model':STAGES[stage][3]},retry_count=0,runtime_trace=trace if trace is not None else {},**extra)


def test_two_source_retries_exact_payload_and_no_refunds(tmp_path):
    records,budget,jobs,owners,calls=scripted(tmp_path,['ok','503','ok','deadline','ok','503'])
    try:
        with jobs:
            invoke(jobs);trace={};invoke(jobs,trace);invoke(jobs)
            assert len(jobs.retries)==2 and len(owners)==3
            assert jobs.retries[0]['failed_refs']['proof_ref']==jobs.accounting[1]['proof_ref']
            assert jobs.retries[1]['failed_refs']['proof_ref']==jobs.accounting[3]['proof_ref']
            assert trace['video_record_refs']=={k:jobs.accounting[2][k] for k in ('proof_ref','receipt_ref')}
            assert 'error' not in trace
            with pytest.raises(jm.ModelJobError):invoke(jobs)
        assert budget.reserved==6 and len(jobs.accounting)==6 and not jobs.accounting[1]['admitted']
        assert all(c[1]=={'unchanged':['original']} for c in calls)
        assert all(c[2]['output_schema']=={'original':True} and c[2]['retry_count']==0 for c in calls)
    finally:records.close()


@pytest.mark.parametrize('mutation',['status','rejection','hash','model','policy','content','stage','proof','cleanup','active','two_rows','pending'])
def test_wrong_or_incomplete_failure_evidence_cannot_retry(tmp_path,mutation):
    records,budget,jobs,owners,calls=scripted(tmp_path,['503','ok'],mutation=mutation)
    try:
        with jobs,pytest.raises(jm.ModelJobError):invoke(jobs)
        assert len(calls)==1 and len(owners)==1 and not jobs.retries
    finally:records.close()


@pytest.mark.parametrize('outcome',['child','cancel','schema','json','unknown'])
def test_non_transport_failure_never_retries(tmp_path,outcome):
    records,budget,jobs,owners,calls=scripted(tmp_path,[outcome,'ok'])
    try:
        from task_server.services.ai_skill_service import SkillSchemaError
        expected={'child':TimeoutError,'cancel':jm.ModelJobCancelled,'schema':SkillSchemaError,'json':ValueError,'unknown':RuntimeError}[outcome]
        with jobs,pytest.raises(expected):invoke(jobs)
        assert len(calls)==1 and not jobs.retries
    finally:records.close()


@pytest.mark.parametrize('headroom,attempts',[(120.499,1),(120.5,2)])
def test_retry_headroom_boundary_is_never_extended(tmp_path,headroom,attempts):
    records,budget,jobs,owners,calls=scripted(tmp_path,['503','ok'],headroom=headroom)
    try:
        with jobs:
            if attempts==1:
                with pytest.raises(jm.ModelJobError):invoke(jobs)
            else:invoke(jobs)
        assert len(calls)==attempts and budget.deadline==headroom and budget.reserved==attempts
    finally:records.close()


@pytest.mark.parametrize('control',['revocation','cap','hook','native','perception','engine','source','store'])
def test_retry_additional_authorization_and_budget_gates(tmp_path,control,monkeypatch):
    records,budget,jobs,owners,calls=scripted(tmp_path,['503','ok'],revoked=control=='revocation')
    if control=='cap':budget.limit=1
    if control=='engine':budget._engine_deadline=120.49
    if control=='source':budget.deadline=120.49
    if control=='store':
        from task_server.services.ui_video_records import RecordError
        monkeypatch.setattr(records,'read',lambda ref:(_ for _ in ()).throw(RecordError('failed')))
    seen=[];extra={'raw_receipt':lambda value:seen.append(value)} if control=='hook' else {}
    stage='timeline' if control=='native' else 'perception' if control=='perception' else 'overview'
    try:
        from task_server.services.ui_video_records import RecordError
        expected=PermissionError if control=='revocation' else RecordError if control=='store' else jm.ModelJobError
        with jobs,pytest.raises(expected):invoke(jobs,stage=stage,**extra)
        assert len(calls)==1 and len(owners)==1 and not jobs.retries
        assert len(seen)==(control=='hook')
    finally:records.close()


def test_schema_correction_can_continue_on_original_unlatched_owner(tmp_path):
    from task_server.services.ai_skill_service import SkillSchemaError
    records,budget,jobs,owners,calls=scripted(tmp_path,['schema','ok'])
    try:
        with jobs:
            with pytest.raises(SkillSchemaError):invoke(jobs)
            invoke(jobs)
        assert len(owners)==1 and not jobs.retries and budget.reserved==2
    finally:records.close()


def test_real_runtime_replacement_trace_and_owner_manifest(tmp_path,monkeypatch):
    from task_server.services import ui_video_runtime as runtime, ui_video_windows as windows
    from task_server.services import ui_video_analysis as analysis, ui_video_business_inputs as inputs
    from test_ui_video_trajectory import frames
    gateway=Gateway('503');owners=[];published=[]
    class Owner(jm.ModelJobs):
        def __init__(self,*a,**kw):
            assert all(o._closed and not o.active_pids for o in owners)
            super().__init__(*a,**kw,gateway_url=f'http://127.0.0.1:{gateway.server.server_port}');owners.append(self)
    monkeypatch.setattr(jm,'ModelJobs',Owner)
    monkeypatch.setattr(windows,'load_assets',lambda fs,*a:[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':'YQ=='} for f in fs])
    monkeypatch.setattr(analysis,'consolidate_observed_frames',lambda doc,root,cancelled,observed,*a,**kw:observed)
    monkeypatch.setattr(inputs,'recover_business_inputs',lambda result,*a,**kw:result)
    (tmp_path/'frames').mkdir();source=tmp_path/'source.mp4';source.write_bytes(b'\0\0\0\x18ftypisom'+b'x'*(12249942-12))
    budget=RuntimeBudget(0,business=True)
    try:
        result=runtime.run_runtime({'media':{'frames':frames(3),'duration_ms':1000}},tmp_path,source,lambda:None,lambda **kw:published.append(kw),budget=budget,mode=runtime.BUSINESS_MODE)
        assert [b['videoStage'] for b in gateway.calls]==['overview','overview','grounding','audit']
        private=result['video_runtime_private'];assert len(private['job_owners'])==2 and len(private['transport_retries'])==1
        retry=private['transport_retries'][0]
        assert retry['replacement_refs']['proof_ref']==owners[1].accounting[0]['proof_ref']
        assert any(d.get('proof_ref')==retry['replacement_refs']['proof_ref'] for d in private['trajectory_diagnostics'])
        with RecordStore.open_snapshot(tmp_path/'model-records',private['records']) as records:
            status_refs=records.read(private['accounting_ref'])['refs'];rows=[records.read(ref) for ref in status_refs]
            assert [r['index'] for r in rows]==list(range(4)) and budget.reserved==4
            assert not rows[0]['admitted'] and all(r['admitted'] for r in rows[1:])
            assert private['job_owners'][0]['status_refs']==status_refs[:1]
            assert private['job_owners'][1]['status_refs']==status_refs[1:]
        assert all(o._closed and not o.active_pids for o in owners)
    finally:gateway.close()


def test_false_authorization_after_owner_close_cancels_without_replacement(tmp_path):
    records,budget,jobs,owners,calls=scripted(tmp_path,['503','ok'],revoked='false')
    try:
        with jobs,pytest.raises(jm.ModelJobCancelled):invoke(jobs)
        assert len(calls)==1 and len(owners)==1 and not jobs.retries
    finally:records.close()


def test_bounded_consolidate_groups_share_facade_replacement_budget_and_proof_links(tmp_path):
    from test_ui_video_planning_groups import payload
    from task_server.services.ui_video_planning_groups import partition
    records,budget,jobs,owners,calls=scripted(tmp_path,['503','ok','ok'])
    groups=partition(payload());before=copy.deepcopy(groups)
    try:
        with jobs:
            budget.freeze_packets(9,business=True)
            budget.preflight_planning(len(groups))
            for group in groups:
                jobs.run('ui_video_steps',group,version='consolidate',video_stage='consolidate',
                         image_assets=[],retry_count=0,model_config={'providerId':'qwen_plus','model':STAGES['consolidate'][3]})
        assert len(calls)==3 and budget.reserved==3 and budget.limit==38
        assert calls[0][1]==calls[1][1] and calls[2][1]['planning_scope']['group_index']==1
        assert groups==before and len(owners)==2 and len(jobs.retries)==1
        assert len({row['proof_ref']['entry'] for row in jobs.accounting})==3
        assert jobs.retries[0]['failed_refs']['proof_ref']==jobs.accounting[0]['proof_ref']
        assert jobs.retries[0]['replacement_refs']['proof_ref']==jobs.accounting[1]['proof_ref']
    finally:records.close()


@pytest.mark.parametrize('failure',['503','deadline'])
def test_partial_recheck_is_one_actual_attempt(tmp_path,failure):
    records,budget,jobs,owners,calls=scripted(tmp_path,[failure,'ok'])
    try:
        with jobs:
            with pytest.raises((jm.ModelJobError,jm.LocalModelJobDeadline)):
                invoke(jobs,stage='audit',allow_transport_retry=False)
        assert budget.reserved==1 and len(calls)==len(jobs.accounting)==len(owners)==1
        assert jobs.retries==[] and owners[0]._closed
        assert jobs.accounting[0]['terminal'] and jobs.accounting[0]['cleanup']
        assert 'allow_transport_retry' not in calls[0][2]
    finally:records.close()
