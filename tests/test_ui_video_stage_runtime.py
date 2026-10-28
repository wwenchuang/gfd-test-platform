"""Normal runtime wire: scoped stage admission and acknowledged receipts."""
import copy
import hashlib
import io
import json
import pytest
from task_server.services import ai_skill_service as ai
from task_server.services import ui_video_stage_policy as policy
from test_ui_video_stage_wire import evidence, semantic_schema

STAGES = {
 'perception': ('ui_video_perception','v1','whole-perception',4000),
 'overview': ('ui_video_trajectory','overview','whole-trajectory',10000),
 'semantic': ('ui_video_trajectory','observe','whole-trajectory',10000),
 'audit': ('ui_video_trajectory','audit','whole-trajectory',10000),
 'consolidate': ('ui_video_steps','consolidate','whole-consolidate',10000),
 'outcome': ('ui_video_observe','outcome','whole-observe',4000),
 'inputs': ('ui_video_observe','inputs','whole-observe',3000),
}

def request(stage):
 p,a=evidence();skill,version,phase,tokens=STAGES[stage];p['phase']=phase
 if stage!='perception':p['reasoning_mode']='bounded'
 kw=dict(payload=p,image_assets=a,video_stage=stage,version=version,max_tokens=tokens)
 if stage=='semantic':p.pop('image_frames');kw.update(image_assets=[],output_schema=semantic_schema())
 return skill,kw

@pytest.mark.parametrize('stage',STAGES)
def test_exact_stage_version_required_before_http(monkeypatch,stage):
 skill,kw=request(stage);kw['version']='wrong';calls=[]
 monkeypatch.setattr(ai.urllib.request,'urlopen',lambda *a,**k:calls.append(a))
 with pytest.raises(ValueError,match='video_stage_scope_invalid'):ai.ai_gateway_skill_content(skill,'pixels',**kw)
 assert calls==[]

@pytest.mark.parametrize('stage',STAGES)
def test_all_stage_proof_then_receipt_then_admission(monkeypatch,stage):
 from task_server.services.ui_video_stage_receipts import make_expected_attestation
 skill,kw=request(stage);order=[];bodies=[]
 def http(req,timeout):
  order.append('http');body=json.loads(req.data);bodies.append(body)
  assert 0<timeout<=1.5 and 0<body['timeoutMs']<=1500
  return io.BytesIO(json.dumps({'success':True,'content':'raw content','model': 'qwen3.8-flash' if stage=='overview' else 'qwen3.8-max',
   'providerId':'official','fallbackUsed':False,'finishReason':'stop','videoPolicy':make_expected_attestation(body)}).encode())
 monkeypatch.setattr(ai.urllib.request,'urlopen',http)
 proofs=[];receipts=[]
 def proof(v):order.append('proof');proofs.append(v)
 def raw(v):order.append('raw');receipts.append(v)
 assert ai.ai_gateway_skill_content(skill,'pixels',**kw,timeout=1.5,request_proof_hook=proof,raw_receipt=raw)=='raw content'
 assert order==['proof','http','raw'] and proofs[0]['gateway_request_sha256']==receipts[0]['gateway_request_sha256']
 assert bodies[0]['skillVersion']==STAGES[stage][1]
 assert receipts[0]['videoPolicy']['correlation']==proofs[0]['correlation']
 assert 'pixels' not in json.dumps(proofs) and 'base64' not in json.dumps(receipts)

@pytest.mark.parametrize('change',['missing','version','stage','correlation','model','options','hash','schema','fine'])
def test_old_or_wrong_gateway_preserves_raw_then_rejects(monkeypatch,change):
 from task_server.services.ui_video_stage_receipts import make_expected_attestation
 skill,kw=request('semantic');receipts=[]
 def http(req,timeout):
  body=json.loads(req.data);a=make_expected_attestation(body)
  if change in {'version','stage','correlation','model'}:a[change]='wrong'
  if change=='options':a['effective_options']['max_tokens']=777
  if change=='hash':a['provider_options_sha256']='0'*64
  if change=='schema':a['output_schema_sha256']='0'*64
  if change=='fine':a['effective_options']['fine_frame_ids']=['frame-000']
  return io.BytesIO(json.dumps({'success':True,'content':'unaltered invalid JSON','model':'qwen3.8-max','fallbackUsed':False,'finishReason':'stop',
   **({} if change=='missing' else {'videoPolicy':a})}).encode())
 monkeypatch.setattr(ai.urllib.request,'urlopen',http)
 with pytest.raises(RuntimeError,match='video_stage_attestation_invalid'):
  ai.ai_gateway_skill_content(skill,'pixels',**kw,raw_receipt=receipts.append)
 assert receipts[0]['content']=='unaltered invalid JSON'


def test_proof_writer_failure_prevents_http(monkeypatch):
 skill,kw=request('perception');calls=[]
 monkeypatch.setattr(ai.urllib.request,'urlopen',lambda *a,**k:calls.append(a))
 def broken(proof):raise OSError('writer failed')
 with pytest.raises(OSError):ai.ai_gateway_skill_content(skill,'pixels',**kw,request_proof_hook=broken)
 assert calls==[]

@pytest.mark.parametrize('change',[{'temperature':.2},{'max_tokens':999},{'model_config':{'model':'other'}},{'timeout':0}])
def test_caller_policy_overrides_fail_before_dispatch(monkeypatch,change):
 skill,kw=request('inputs');kw.update(change);calls=[]
 monkeypatch.setattr(ai.urllib.request,'urlopen',lambda *a,**k:calls.append(a))
 with pytest.raises(ValueError,match='video_'):ai.ai_gateway_skill_content(skill,'pixels',**kw)
 assert calls==[]

@pytest.mark.parametrize('stage',STAGES)
def test_python_matches_actual_gateway_canonical_options_with_unicode(monkeypatch,stage):
 import subprocess
 from pathlib import Path
 from task_server.services.ui_video_stage_receipts import make_expected_attestation
 skill,kw=request(stage);bodies=[]
 def http(req,timeout):
  body=json.loads(req.data);bodies.append(body)
  return io.BytesIO(json.dumps({'success':True,'content':'{}','model': 'qwen3.8-flash' if stage=='overview' else 'qwen3.8-max',
   'fallbackUsed':False,'finishReason':'stop','videoPolicy':make_expected_attestation(body)}).encode())
 monkeypatch.setattr(ai.urllib.request,'urlopen',http)
 ai.ai_gateway_skill_content(skill,'中文“原文” 😀\nline\t\\ end',**kw)
 script="""
 const fs=require('fs'),vm=require('vm'),{createHash}=require('crypto');
 const source=fs.readFileSync('ai-gateway/server.js','utf8'),body=JSON.parse(fs.readFileSync(0,'utf8'));
 const ctx=vm.createContext({Buffer,createHash,body});
 vm.runInContext(source.slice(source.indexOf('function requestedCompletionTokens('),source.indexOf('async function appendAiLog('))+
 source.slice(source.indexOf('function buildUserMessage('),source.indexOf('function assistantOutputText')),ctx);
 const attestation=vm.runInContext(`videoPolicyAttestation(body,completionOptionsForRoute({model:videoStageConfig(body.videoStage)[3],baseUrl:'https://dashscope.aliyuncs.com/compatible-mode/v1'},
 '你是移动端测试平台 AI Skill 执行器。必须严格遵守用户输入的 Skill Prompt，只输出合法 JSON。',body,{userMessage:body.prompt}))`,ctx);
 process.stdout.write(JSON.stringify(attestation));
 """
 result=subprocess.run(['node','-e',script],input=json.dumps(bodies[0]),text=True,capture_output=True,check=True,cwd=Path(__file__).resolve().parents[1])
 assert json.loads(result.stdout)==make_expected_attestation(bodies[0])


def test_request_ack_time_is_inside_deadline(monkeypatch):
 import time
 skill,kw=request('perception');calls=[]
 monkeypatch.setattr(ai.urllib.request,'urlopen',lambda *a,**k:calls.append(a))
 with pytest.raises(TimeoutError,match='video_stage_deadline'):
  ai.ai_gateway_skill_content(skill,'pixels',**kw,timeout=.005,request_proof_hook=lambda _:time.sleep(.01))
 assert calls==[]


def test_stage_receipt_never_substitutes_selected_model(monkeypatch):
 from task_server.services.ui_video_stage_receipts import make_expected_attestation
 skill,kw=request('inputs');receipts=[]
 def http(req,timeout):
  return io.BytesIO(json.dumps({'success':True,'content':'raw','fallbackUsed':False,'finishReason':'stop',
   'videoPolicy':make_expected_attestation(json.loads(req.data))}).encode())
 monkeypatch.setattr(ai.urllib.request,'urlopen',http)
 with pytest.raises(RuntimeError):ai.ai_gateway_skill_content(skill,'pixels',**kw,raw_receipt=receipts.append)
 assert receipts[0]['model'] is None


def test_http_rejection_retains_only_safe_receipt_metadata(monkeypatch):
 skill,kw=request('inputs');receipts=[]
 def http(req,timeout):
  raise ai.urllib.error.HTTPError(req.full_url,500,'error',{},io.BytesIO(json.dumps({
   'error':'video_stage_actual_model_invalid','content':'private rejected body','reasoning_content':'secret'}).encode()))
 monkeypatch.setattr(ai.urllib.request,'urlopen',http)
 with pytest.raises(RuntimeError,match='video_stage_gateway_rejected'):
  ai.ai_gateway_skill_content(skill,'pixels',**kw,raw_receipt=receipts.append)
 assert len(receipts)==1 and receipts[0]['http_status']==500
 assert receipts[0]['rejection_code']=='video_stage_actual_model_invalid'
 assert 'private' not in json.dumps(receipts) and 'secret' not in json.dumps(receipts)

@pytest.mark.parametrize('stage',STAGES)
def test_normal_skill_call_uses_hooks_before_json_without_thread_or_fallback(monkeypatch,stage):
 from task_server.services.ui_video_stage_receipts import make_expected_attestation
 skill,kw=request(stage);events=[];kw.pop('payload');payload=request(stage)[1]['payload']
 def forbidden(*a,**k):pytest.fail('stage escaped synchronous owned transport')
 monkeypatch.setattr(ai,'_run_ai_skill_call_with_hard_timeout',forbidden)
 monkeypatch.setattr(ai,'dashscope_chat_content',forbidden)
 def http(req,timeout):
  events.append('http');body=json.loads(req.data)
  return io.BytesIO(json.dumps({'success':True,'content':'INVALID JSON','model': 'qwen3.8-flash' if stage=='overview' else 'qwen3.8-max',
   'fallbackUsed':False,'finishReason':'stop','videoPolicy':make_expected_attestation(body)}).encode())
 monkeypatch.setattr(ai.urllib.request,'urlopen',http)
 with pytest.raises(RuntimeError):
  ai.run_ai_skill(skill,payload,**kw,retry_count=0,request_proof_hook=lambda _:events.append('proof'),raw_receipt=lambda _:events.append('raw'))
 assert events==['proof','http','raw']


def test_raw_ack_cannot_admit_after_request_deadline(monkeypatch):
 import time
 from task_server.services.ui_video_stage_receipts import make_expected_attestation
 skill,kw=request('inputs');receipts=[]
 def http(req,timeout):
  return io.BytesIO(json.dumps({'success':True,'content':'raw','model':'qwen3.8-max','fallbackUsed':False,'finishReason':'stop',
   'videoPolicy':make_expected_attestation(json.loads(req.data))}).encode())
 monkeypatch.setattr(ai.urllib.request,'urlopen',http)
 def ack(value):receipts.append(value);time.sleep(.02)
 with pytest.raises(TimeoutError,match='video_stage_deadline'):
  ai.ai_gateway_skill_content(skill,'pixels',**kw,timeout=.01,raw_receipt=ack)
 assert len(receipts)==1

@pytest.mark.parametrize('metadata',[{'finishReason':{}},{'model':{}},{'usage':{'totalTokens':10**400}},
 {'videoPolicy':{'effective_options':{'temperature':float('nan')},'arbitrary':'private'}}])
def test_malformed_gateway_metadata_still_retains_content_safely(monkeypatch,metadata):
 from task_server.services.ui_video_stage_receipts import make_expected_attestation
 skill,kw=request('inputs');receipts=[]
 def http(req,timeout):
  return io.BytesIO(json.dumps({'success':True,'content':'raw','model':'qwen3.8-max','fallbackUsed':False,'finishReason':'stop',
   'videoPolicy':make_expected_attestation(json.loads(req.data)),**metadata}).encode())
 monkeypatch.setattr(ai.urllib.request,'urlopen',http)
 if 'usage' in metadata:ai.ai_gateway_skill_content(skill,'pixels',**kw,raw_receipt=receipts.append)
 else:
  with pytest.raises(RuntimeError):ai.ai_gateway_skill_content(skill,'pixels',**kw,raw_receipt=receipts.append)
 assert receipts[0]['content']=='raw'
 assert 'private' not in json.dumps(receipts,allow_nan=False)


def test_stage_runtime_trace_excludes_arbitrary_provider_fields(monkeypatch):
 from task_server.services.ui_video_stage_receipts import make_expected_attestation
 skill,kw=request('inputs');trace={}
 def http(req,timeout):
  return io.BytesIO(json.dumps({'success':True,'content':'raw','model':'qwen3.8-max','fallbackUsed':False,'finishReason':'stop',
   'fallbackReason':'private provider body','usage':{'thoughts':'private reasoning','totalTokens':4},
   'videoPolicy':make_expected_attestation(json.loads(req.data))}).encode())
 monkeypatch.setattr(ai.urllib.request,'urlopen',http)
 ai.ai_gateway_skill_content(skill,'pixels',**kw,runtime_trace=trace)
 assert 'private' not in json.dumps(trace) and trace['usage']=={'totalTokens':4}
