import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import {createHash} from 'node:crypto';
import {EventEmitter} from 'node:events';
import {spawnSync} from 'node:child_process';
const source=fs.readFileSync(new URL('../ai-gateway/server.js',import.meta.url),'utf8');
const model='qwen3.8-max';
const route={model,baseUrl:'https://dashscope.aliyuncs.com/compatible-mode/v1',temperature:.1};
const system='你是移动端测试平台 AI Skill 执行器。必须严格遵守用户输入的 Skill Prompt，只输出合法 JSON。';
function input(size=7500000) {
 const raw=Buffer.alloc(size);raw.write('ftypisom',4);
 return {skillName:'ui_video_timeline',skillVersion:'v1',videoStage:'timeline',jsonResponse:true,prompt:'business',timeoutMs:120000,videoDeadlineMs:Date.now()+120000,videoPolicyVersion:'video-stage-policy-v1',videoCorrelation:'a'.repeat(32),model,
 payload:{phase:'whole-timeline',video:{transport:'dashscope-temporary-original-v1',source_sha256:createHash('sha256').update(raw).digest('hex'),source_bytes:size,duration_ms:75380,fps:4,max_pixels:655360,total_pixels:268435456}},imageAssets:[{name:'source-video',mime:'video/mp4',base64:raw.toString('base64'),timestamp_ms:0,dataUrl:''}]};
}
function envelope(body) {
 const uri=`oss://dashscope-instant/test/account/${body.videoCorrelation}.mp4`;
 return {version:'dashscope-temporary-original-v1',uri,source_sha256:body.payload.video.source_sha256,source_bytes:body.payload.video.source_bytes,source_md5:createHash('md5').update(Buffer.from(body.imageAssets[0].base64,'base64')).digest('hex'),model,correlation:body.videoCorrelation,resource_reference_sha256:createHash('sha256').update(uri).digest('hex'),uploaded_at_ms:Date.now(),documented_max_lifetime_seconds:172800,private_acl:true,forbid_overwrite:true,resolve_header:'enable'};
}
function sandbox({uploadError,abortAfterUpload=false}={}) {
 const uploads=[],calls=[],logs=[];let handler;
 const provider={...route,apiKeyEnv:'QWEN_TEST_MEMORY_ONLY',name:'primary',type:'openai_compatible'};
 const context=vm.createContext({Buffer,createHash,AbortController,setTimeout,clearTimeout,Date,
  DYNAMIC_PROVIDER_PREFIX:'dynamic:',ROUTER_FILE:'offline',AI_CALL_TIMEOUT_MS:120000,AI_CALL_FALLBACK_RESERVE_MS:10000,MOCK_ENABLED:false,SKILL_ACTION_MAP:{},
  process:{env:{QWEN_TEST_MEMORY_ONLY:'sentinel-never-log'}},
  readJson:async()=>({generate_case:{providerId:'primary',fallbackProviderIds:[]}}),readProviders:async()=>({providers:{primary:provider}}),uuidv4:()=> 'offline',appendAiLog:async e=>logs.push(e),sanitizeError:e=>String(e.message),preview:()=>'',asyncRoute:fn=>fn,app:{post:(_p,fn)=>{handler=fn;}},
  uploadNativeVideo:async options=>{uploads.push(options);if(uploadError)throw new Error(uploadError);const body=context.currentBody;const value=envelope(body);if(abortAfterUpload)context.activeResponse.emit('close');return value;},
  clientForRoute:()=>({chat:{completions:{create:async (options,requestOptions)=>{calls.push({options,requestOptions});return {model,choices:[{message:{content:'{"scenes":[],"questions":[]}'},finish_reason:'stop'}],usage:{prompt_tokens:1,completion_tokens:2,total_tokens:3}};}}}}),
 });
 for(const [start,end] of [['function normalizeLegacyProviderId(','async function discoverProviderChannel('],['function routeFromProviderConfig(','function clientForRoute('],['function requestedCallTimeoutMs(','async function appendAiLog('],['function stripMarkdownFence(','function mockAiOutput('],["app.post('/ai/skill'","app.post('/ai/analyze-failure'"]])vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end)),context);
 return {context,uploads,calls,logs,async request(body){context.currentBody=body;const req=Object.assign(new EventEmitter(),{body});let response;const res=Object.assign(new EventEmitter(),{json:v=>{response=v;}});context.activeResponse=res;await handler(req,res);return response;}};
}
test('native file capability admits canonical oversized original without weakening inline bound',()=>{
 const run=sandbox();run.context.body=input(7500000);run.context.route=route;run.context.system=system;
 assert.doesNotThrow(()=>vm.runInContext('completionOptionsForRoute(route,system,body,{userMessage:body.prompt})',run.context));
 delete run.context.body.payload.video.transport;
 assert.throws(()=>vm.runInContext('completionOptionsForRoute(route,system,body,{userMessage:body.prompt})',run.context),/video_native_asset_invalid/);
});
test('native file handler uploads original once, resolves resource and attests actual URI options',async()=>{
 const run=sandbox(),body=input();const result=await run.request(body);
 assert.equal(run.uploads.length,1);assert.equal(run.calls.length,1);
 const upload=run.uploads[0],call=run.calls[0];assert.deepEqual(Buffer.from(upload.source),Buffer.from(body.imageAssets[0].base64,'base64'));assert.equal(upload.sourceSha256,body.payload.video.source_sha256);assert.equal(upload.model,model);assert.equal(upload.correlation,body.videoCorrelation);assert.equal(upload.apiKey,'sentinel-never-log');assert.ok(upload.deadlineMs<=body.videoDeadlineMs);assert.equal(upload.signal,call.requestOptions.signal);
 assert.equal(call.options.messages[1].content[0].video_url.url,result.nativeResource.uri);
 assert.equal(call.requestOptions.headers['X-DashScope-OssResourceResolve'],'enable');assert.equal(call.requestOptions.maxRetries,0);
 assert.ok(call.requestOptions.timeout<=body.videoDeadlineMs-Date.now()+1000);
 assert.equal(result.videoPolicy.native_transport.resource_reference_sha256,result.nativeResource.resource_reference_sha256);
 assert.equal(JSON.stringify(result.videoPolicy).includes('oss://'),false);
 const python=spawnSync('python3',['-c',"import json,sys;from task_server.services.ui_video_stage_receipts import make_expected_attestation;v=json.load(sys.stdin);print(json.dumps(make_expected_attestation(v['body'],native_resource=v['resource'])))"],{input:JSON.stringify({body,resource:result.nativeResource}),maxBuffer:1024*1024});assert.equal(python.status,0,python.stderr.toString());assert.deepEqual(JSON.parse(JSON.stringify(result.videoPolicy)),JSON.parse(python.stdout));
 const log=JSON.stringify(run.logs);assert.equal(log.includes('oss://'),false);assert.equal(log.includes('sentinel-never-log'),false);assert.equal(log.includes(body.imageAssets[0].base64),false);
});
test('failed upload and cancellation after upload cannot dispatch model or retry',async()=>{
 for(const options of [{uploadError:'video_native_upload_failed'},{abortAfterUpload:true}]){
  const run=sandbox(options);await assert.rejects(run.request(input()),/video_/);assert.equal(run.uploads.length,1);assert.equal(run.calls.length,0);
 }
});
test('caller resource envelope cannot supply the uploaded resource or provider headers',async()=>{
 const run=sandbox(),body=input();
 body.nativeResource={uri:'https://untrusted.example/source.mp4',resolve_header:'disabled'};
 const result=await run.request(body);
 assert.equal(run.uploads.length,1);assert.equal(run.calls.length,1);
 assert.equal(run.calls[0].options.messages[1].content[0].video_url.url,result.nativeResource.uri);
 assert.notEqual(result.nativeResource.uri,body.nativeResource.uri);
 assert.equal(run.calls[0].requestOptions.headers['X-DashScope-OssResourceResolve'],'enable');
});
test('malformed file source and other-stage capability fail before upload',async()=>{
 for(const change of [b=>b.payload.video.source_sha256='0'.repeat(64),b=>b.payload.video.transport='unknown',b=>b.payload.video.source_bytes++,b=>b.imageAssets[0].dataUrl='oss://untrusted/path',b=>b.videoStage='audit']){
  const run=sandbox(),body=input();change(body);await assert.rejects(run.request(body),/video_/);assert.equal(run.uploads.length,0);assert.equal(run.calls.length,0);
 }
 const small=sandbox();await assert.rejects(small.request(input(32)),/video_native_asset_invalid/);assert.equal(small.uploads.length,0);
});
