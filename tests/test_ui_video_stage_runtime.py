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


# Safe receipts are a positive metadata projection, never an arbitrary string log.
UNSAFE_ATTESTATION_FIELDS = [
 ('version', 'PRIVATE_POLICY'), ('stage', 'PRIVATE_STAGE'), ('skill', 'PRIVATE_SKILL'),
 ('skill_version', 'PRIVATE_VERSION'), ('model', 'PRIVATE_MODEL'),
 ('correlation', 'PRIVATE_CORRELATION'), ('correlation', 'a'*31),
 ('provider_options_sha256', 'PRIVATE_PROVIDER_BODY'), ('provider_options_sha256', None),
 ('output_schema_sha256', 'PRIVATE_SCHEMA'), ('output_schema_sha256', 'a'*63),
 ('effective_options.model', 'PRIVATE_MODEL'), ('effective_options.temperature', 'PRIVATE_TEMPERATURE'),
 ('effective_options.temperature', True), ('effective_options.temperature', float('inf')),
 ('effective_options.temperature', float('nan')), ('effective_options.temperature', 10**400),
 ('effective_options.temperature', .2), ('effective_options.max_tokens', True),
 ('effective_options.max_tokens', 4000.0), ('effective_options.max_tokens', 10**400),
 ('effective_options.max_tokens', 999), ('effective_options.enable_thinking', 'PRIVATE_REASONING'),
 ('effective_options.enable_thinking', 1), ('effective_options.thinking_budget', True),
 ('effective_options.thinking_budget', 1024.0), ('effective_options.thinking_budget', 1025),
 ('effective_options.response_format', 'PRIVATE_FORMAT'), ('effective_options.response_format', {}),
 ('effective_options.image_count', True), ('effective_options.image_count', 1),
 ('effective_options.image_count', -1), ('effective_options.image_count', 61),
 ('effective_options.image_count', 3.0), ('effective_options.fine_frame_ids', ['PRIVATE_FRAME']),
 ('effective_options.fine_frame_ids', ['frame-000-detail']),
 ('effective_options.fine_frame_ids', ['frame-00']),
 ('effective_options.fine_frame_ids', ['frame-000','frame-000']),
 ('effective_options.fine_frame_ids', [f'frame-{i:03d}' for i in range(13)]),
 ('effective_options.fine_frame_ids', [True]), ('effective_options.PRIVATE_KEY', 'PRIVATE_BODY'),
 ('PRIVATE_KEY', {'nested':'PRIVATE_BODY'}),
]

@pytest.mark.parametrize('path,value', UNSAFE_ATTESTATION_FIELDS)
def test_safe_attestation_omits_invalid_field_without_mutation(path,value):
 from task_server.services.ui_video_stage_receipts import safe_attestation
 root, _, leaf = path.partition('.')
 original = {root:{leaf:value}} if leaf else {root:value}
 before = copy.deepcopy(original)
 projected = safe_attestation(original)
 assert (leaf not in projected.get(root, {}) if leaf else root not in projected)
 assert 'PRIVATE' not in json.dumps(projected, allow_nan=False)
 # JSON comparison also handles deliberately nonfinite malicious numbers.
 assert json.dumps(original, sort_keys=True) == json.dumps(before, sort_keys=True)

@pytest.mark.parametrize('stage', STAGES)
def test_valid_attestation_projection_is_exact_and_detached(monkeypatch,stage):
 from task_server.services.ui_video_stage_receipts import make_expected_attestation, safe_attestation
 skill, kw = request(stage); originals=[]; receipts=[]
 def http(req,timeout):
  attestation = make_expected_attestation(json.loads(req.data)); originals.append(attestation)
  assert safe_attestation(attestation) == attestation
  return io.BytesIO(json.dumps({'success':True,'content':'raw 中文 😀\n\t\\',
   'model':policy.STAGES[stage][3], 'fallbackUsed':False, 'finishReason':'stop',
   'videoPolicy':attestation}).encode())
 monkeypatch.setattr(ai.urllib.request, 'urlopen', http)
 assert ai.ai_gateway_skill_content(skill, '中文 😀\n\t\\', **kw, raw_receipt=receipts.append) == 'raw 中文 😀\n\t\\'
 assert receipts[0]['videoPolicy'] == originals[0]
 projected = safe_attestation(originals[0]); projected['effective_options']['fine_frame_ids'].append('frame-end')
 assert originals[0] == receipts[0]['videoPolicy']

@pytest.mark.parametrize('path,value', [
 ('provider_options_sha256','PRIVATE_PROVIDER_BODY'),
 ('effective_options.enable_thinking','PRIVATE_REASONING'),
 ('effective_options.max_tokens',True),
 ('effective_options.fine_frame_ids',['PRIVATE_FRAME']),
])
def test_run_skill_preserves_raw_content_but_rejects_private_attestation(monkeypatch,path,value):
 from task_server.services.ui_video_stage_receipts import make_expected_attestation
 skill,kw=request('inputs'); payload=kw.pop('payload');receipts=[];trace={};originals=[]
 def http(req,timeout):
  attestation=make_expected_attestation(json.loads(req.data));root,_,leaf=path.partition('.')
  if leaf:attestation[root][leaf]=value
  else:attestation[root]=value
  originals.append(copy.deepcopy(attestation))
  return io.BytesIO(json.dumps({'success':True,'content':'ORIGINAL provider content 中文',
   'model':'qwen3.8-max','fallbackUsed':False,'finishReason':'stop','videoPolicy':attestation}).encode())
 monkeypatch.setattr(ai.urllib.request,'urlopen',http)
 with pytest.raises(RuntimeError) as error:
  ai.run_ai_skill(skill,payload,**kw,retry_count=0,runtime_trace=trace,raw_receipt=receipts.append)
 assert 'video_stage_attestation_invalid' in str(error.value) or str(error.value)=='video_stage_call_failed'
 assert len(receipts)==1 and receipts[0]['content']=='ORIGINAL provider content 中文'
 root,_,leaf=path.partition('.')
 assert (leaf not in receipts[0]['videoPolicy'].get(root,{}) if leaf else root not in receipts[0]['videoPolicy'])
 assert 'PRIVATE' not in json.dumps([receipts,trace,str(error.value)],allow_nan=False)
 assert (originals[0][root][leaf] if leaf else originals[0][root]) == value

@pytest.mark.parametrize('entry',['adapter','run_skill'])
@pytest.mark.parametrize('raw',[
 b'<html>PRIVATE_GATEWAY_BODY</html>', b'', b'"PRIVATE_GATEWAY_BODY"',
 b'["PRIVATE_GATEWAY_BODY"]', b'{"error":"PRIVATE_GATEWAY_BODY"}',
 b'{"success":true,"content":{"PRIVATE_GATEWAY_BODY":1}}',
 b'{"success":"PRIVATE_GATEWAY_BODY","content":""}',
 b'\xffPRIVATE_GATEWAY_BODY',
])
def test_stage_malformed_http_envelope_has_only_safe_error(monkeypatch,entry,raw):
 import traceback
 skill,kw=request('inputs');trace={};receipts=[];proofs=[]
 def http(req,timeout):
  response=io.BytesIO(raw)
  response.headers={'Content-Type':'PRIVATE_HEADER','Content-Length':'PRIVATE_HEADER'}
  return response
 monkeypatch.setattr(ai.urllib.request,'urlopen',http)
 with pytest.raises(RuntimeError) as error:
  if entry=='adapter':
   ai.ai_gateway_skill_content(skill,'pixels',**kw,runtime_trace=trace,raw_receipt=receipts.append,request_proof_hook=proofs.append)
  else:
   payload=kw.pop('payload')
   ai.run_ai_skill(skill,payload,**kw,retry_count=0,runtime_trace=trace,raw_receipt=receipts.append,request_proof_hook=proofs.append)
 assert str(error.value) in {'video_stage_gateway_response_invalid','video_stage_call_failed'}
 assert len(proofs)==1 and receipts==[]  # HTTP bytes are not provider message.content.
 assert 'PRIVATE' not in json.dumps([trace,receipts,proofs,''.join(traceback.format_exception(type(error.value), error.value, error.value.__traceback__))])

@pytest.mark.parametrize('entry',['adapter','run_skill'])
@pytest.mark.parametrize('kind',['url','read','timeout','oversized_header'])
def test_stage_transport_errors_cannot_export_body_or_headers(monkeypatch,entry,kind):
 import traceback
 skill,kw=request('inputs');trace={};receipts=[]
 def http(req,timeout):
  if kind=='url':raise ai.urllib.error.URLError('PRIVATE_TRANSPORT_BODY')
  if kind=='timeout':raise TimeoutError('PRIVATE_TIMEOUT_BODY')
  class Response(io.BytesIO):
   def read(self,*args):
    if kind=='read':raise OSError('PRIVATE_READ_BODY')
    return super().read(*args)
  response=Response(b'PRIVATE_GATEWAY_BODY')
  response.headers={'Content-Length':'9'*400 if kind=='oversized_header' else 'PRIVATE_HEADER'}
  return response
 monkeypatch.setattr(ai.urllib.request,'urlopen',http)
 with pytest.raises((RuntimeError,TimeoutError)) as error:
  if entry=='adapter':ai.ai_gateway_skill_content(skill,'pixels',**kw,runtime_trace=trace,raw_receipt=receipts.append)
  else:
   payload=kw.pop('payload');ai.run_ai_skill(skill,payload,**kw,retry_count=0,runtime_trace=trace,raw_receipt=receipts.append)
 assert str(error.value) in {'video_stage_gateway_transport_failed','video_stage_deadline','video_stage_call_failed'}
 rendered=json.dumps([trace,receipts,''.join(traceback.format_exception(type(error.value), error.value, error.value.__traceback__))])
 assert 'PRIVATE' not in rendered and '9'*400 not in rendered and receipts==[]


def test_legacy_gateway_decoder_retains_existing_diagnostics():
 with pytest.raises(RuntimeError,match='PRIVATE_LEGACY_BODY') as error:
  ai._decode_ai_gateway_json_response(b'PRIVATE_LEGACY_BODY',content_type='PRIVATE_LEGACY_HEADER')
 assert 'PRIVATE_LEGACY_HEADER' in str(error.value)


@pytest.mark.parametrize('entry',['adapter','run_skill'])
@pytest.mark.parametrize('stage,field,value',[
 ('inputs','enable_thinking',1), ('perception','enable_thinking',0),
 ('inputs','max_tokens',3000.0), ('semantic','max_tokens',10000.0),
 ('inputs','thinking_budget',1024.0), ('inputs','image_count',3.0),
 ('inputs','max_tokens',True), ('inputs','image_count',float('nan')),
 ('inputs','thinking_budget',float('inf')), ('inputs','image_count',10**400),
])
def test_full_attestation_rejects_wrong_numeric_types_after_raw_ack(monkeypatch,entry,stage,field,value):
 from task_server.services.ui_video_stage_receipts import make_expected_attestation
 skill,kw=request(stage);receipts=[];trace={};order=[]
 def http(req,timeout):
  order.append('http');attestation=make_expected_attestation(json.loads(req.data))
  attestation['effective_options'][field]=value
  return io.BytesIO(json.dumps({'success':True,'content':'ORIGINAL untouched provider content 中文',
   'model':policy.STAGES[stage][3], 'fallbackUsed':False,'finishReason':'stop',
   'videoPolicy':attestation}).encode())
 def receipt(value):order.append('raw_ack');receipts.append(value)
 monkeypatch.setattr(ai.urllib.request,'urlopen',http)
 with pytest.raises(RuntimeError) as error:
  if entry=='adapter':ai.ai_gateway_skill_content(skill,'pixels',**kw,runtime_trace=trace,raw_receipt=receipt)
  else:
   payload=kw.pop('payload');ai.run_ai_skill(skill,payload,**kw,retry_count=0,runtime_trace=trace,raw_receipt=receipt)
 assert str(error.value)==('video_stage_attestation_invalid' if entry=='adapter' else 'video_stage_call_failed')
 assert order==['http','raw_ack'] and len(receipts)==1
 assert receipts[0]['content']=='ORIGINAL untouched provider content 中文'
 assert field not in receipts[0]['videoPolicy']['effective_options']


@pytest.mark.parametrize('value',[{'unexpected':object()}, {'unexpected':{1,2}}, {'unexpected':float('nan')}])
def test_nonserializable_full_attestation_has_static_rejection(value):
 from task_server.services.ui_video_stage_receipts import validate_attestation
 skill,kw=request('inputs')
 body={'videoStage':'inputs','payload':kw['payload'],'imageAssets':kw['image_assets'],
       'prompt':'pixels','skillName':skill,'skillVersion':kw['version'],'videoCorrelation':'a'*32}
 with pytest.raises(RuntimeError,match='^video_stage_attestation_invalid$'):
  validate_attestation(value,body)
