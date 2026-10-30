import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import {createHash} from 'node:crypto';
import {EventEmitter} from 'node:events';
import {spawnSync} from 'node:child_process';
const source=fs.readFileSync(new URL('../ai-gateway/server.js',import.meta.url),'utf8');
const ctx=vm.createContext({Buffer,createHash});
vm.runInContext(source.slice(source.indexOf('function requestedCompletionTokens('),source.indexOf('async function appendAiLog('))+
 source.slice(source.indexOf('function buildUserMessage('),source.indexOf('\nfunction assistantOutputText')),ctx);
const route={model:'qwen3.8-max',baseUrl:'https://dashscope.aliyuncs.com/compatible-mode/v1',temperature:.1};
const system='你是移动端测试平台 AI Skill 执行器。必须严格遵守用户输入的 Skill Prompt，只输出合法 JSON。';
function input(size=32) {
 const raw=Buffer.alloc(size);raw.write('ftypisom',4);
 return {skillName:'ui_video_timeline',skillVersion:'v1',videoStage:'timeline',jsonResponse:true,prompt:'business',
 timeoutMs:120000,videoDeadlineMs:Date.now()+120000,videoPolicyVersion:'video-stage-policy-v1',videoCorrelation:'a'.repeat(32),model:'qwen3.8-max',
 payload:{phase:'whole-timeline',video:{source_sha256:createHash('sha256').update(raw).digest('hex'),source_bytes:size,duration_ms:75380,fps:4,max_pixels:655360,total_pixels:268435456}},
 imageAssets:[{name:'source-video',mime:'video/mp4',base64:raw.toString('base64'),timestamp_ms:0,dataUrl:''}]};
}
function wire(b,r=route){ctx.body=b;ctx.route=r;ctx.system=system;return JSON.parse(JSON.stringify(vm.runInContext('completionOptionsForRoute(route,system,body,{userMessage:body.prompt})',ctx)));}
for(const size of [32,12249942,12*1024*1024])test(`native ${size} bytes real Gateway and independent Python attestation`,()=>{
 const b=input(size),options=wire(b),parts=options.messages[1].content;
 assert.equal(parts.filter(p=>p.type==='image_url').length,0);
 assert.deepEqual(parts.filter(p=>p.type==='video_url'),[{type:'video_url',video_url:{url:'data:video/mp4;base64,'+b.imageAssets[0].base64},fps:4,max_pixels:655360,total_pixels:268435456}]);
 ctx.options=options;const proof=JSON.parse(JSON.stringify(vm.runInContext('videoPolicyAttestation(body,options)',ctx)));
 const py=spawnSync('python3',['-c','import json,sys;from task_server.services.ui_video_stage_receipts import make_expected_attestation;print(json.dumps(make_expected_attestation(json.load(sys.stdin))))'],{input:JSON.stringify(b),maxBuffer:2*1024*1024});
 assert.equal(py.status,0,py.stderr.toString());assert.deepEqual(proof,JSON.parse(py.stdout));
 assert.equal(proof.effective_options.video_count,1);assert.equal(proof.effective_options.image_count,0);
});
test('native malformed media, metadata, scope, schema and route fail closed',()=>{
 const changes=[b=>b.payload.video.source_sha256='0'.repeat(64),b=>b.payload.video.source_bytes++,b=>b.payload.video.duration_ms=0,
 b=>b.payload.video.fps=5,b=>b.payload.video.max_pixels=1,b=>b.payload.video.total_pixels=1,b=>b.payload.extra={},
 b=>b.imageAssets[0].mime='image/jpeg',b=>b.imageAssets[0].name='forged',b=>b.imageAssets[0].timestamp_ms=true,
 b=>b.imageAssets[0].dataUrl='data:video/mp4;base64,AAAA',b=>b.imageAssets[0].base64+='\n',b=>b.imageAssets[0].base64='AB==',
 b=>b.payload.phase='whole-observe',b=>b.model='qwen3.8-flash',b=>b.outputSchema=null,b=>delete b.videoStage];
 for(const mutate of changes){const b=input();mutate(b);assert.throws(()=>wire(b),/video_/);}
 assert.throws(()=>wire(input(12*1024*1024+1)),/video_/);
 assert.throws(()=>wire(input(),{...route,model:'qwen3.8-flash'}),/video_/);
 assert.throws(()=>wire(input(),{...route,baseUrl:'https://evil.example'}),/video_/);
});

function orchestration(fail=false, usage={prompt_tokens:1,completion_tokens:2,total_tokens:3},dispatch=null,primaryRoute=route) {
 const calls=[],logs=[];let handler;
 const official=JSON.parse(fs.readFileSync(new URL('../ai-gateway/config/providers.json',import.meta.url),'utf8')).providers.qwen_plus;
 const providers={providers:{primary:{...primaryRoute,name:'primary'},backup:{...route,name:'backup'},qwen_plus:official}};
 const sandbox=vm.createContext({Buffer,createHash,AbortController,setTimeout,clearTimeout,DYNAMIC_PROVIDER_PREFIX:'dynamic:',ROUTER_FILE:'offline',AI_CALL_TIMEOUT_MS:120000,
 AI_CALL_FALLBACK_RESERVE_MS:10000,MOCK_ENABLED:false,SKILL_ACTION_MAP:{},readJson:async()=>({generate_case:{providerId:'primary',fallbackProviderIds:['backup']}}),
 readProviders:async()=>providers,uuidv4:()=> 'offline',appendAiLog:async e=>logs.push(e),sanitizeError:e=>String(e.message),preview:()=>'',
 asyncRoute:fn=>fn,app:{post:(_p,fn)=>{handler=fn;}},clientForRoute:r=>({chat:{completions:{create:async (options,requestOptions)=>{
  calls.push({providerId:r.providerId,options});if(dispatch)return dispatch(options,requestOptions);if(fail)throw new Error('primary timeout');
  return {model:r.model,choices:[{message:{content:'{}'},finish_reason:'stop'}],usage};
 }}}})});
 for(const [start,end] of [['function normalizeLegacyProviderId(','async function discoverProviderChannel('],
 ['function routeFromProviderConfig(','function clientForRoute('],['function requestedCallTimeoutMs(','async function appendAiLog('],
 ['function stripMarkdownFence(','function mockAiOutput('],["app.post('/ai/skill'","app.post('/ai/analyze-failure'"]])
 vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end)),sandbox);
 return {calls,logs,get handler(){return handler;},async request(body) {let response;const req=Object.assign(new EventEmitter(),{body:{...body,prompt:'pixels'}});const res=Object.assign(new EventEmitter(),{json:r=>response=r});await handler(req,res);return response;}};
}

test('actual native /ai/skill handler preserves options, proof and redacts logs',async()=>{
 const b=input(12249942),run=orchestration();
 const response=await run.request(b);
 assert.equal(run.calls.length,1);assert.equal(response.videoPolicy.effective_options.video_count,1);
 assert.equal(run.calls[0].options.messages[1].content.filter(p=>p.type==='video_url').length,1);
 assert.equal(run.logs.length,1);assert.equal(run.logs[0].inputPreview,'[video stage content omitted]');
 assert.equal(run.logs[0].outputPreview,'[video stage content omitted]');
 assert.equal(JSON.stringify(response.videoPolicy).includes(b.imageAssets[0].base64),false);
 const rejected=orchestration();b.payload.video.source_sha256='0'.repeat(64);
 await assert.rejects(rejected.request(b),/video_/);assert.equal(rejected.calls.length,0);
});
function grounding() {
 const b=input();Object.assign(b,{skillName:'ui_video_trajectory',skillVersion:'observe',videoStage:'grounding'});
 const frames=[{frame_id:'frame-000',timestamp_ms:0},{frame_id:'frame-001',timestamp_ms:100}];
 b.payload={packet_id:'w1',phase:'whole-trajectory',reasoning_mode:'bounded',frames,image_frames:frames,owned_frame_ids:['frame-000'],context_frame_ids:['frame-001']};
 const jpeg=fs.readFileSync(new URL('../assets/brand/kongfubean.jpeg',import.meta.url)).toString('base64');
 b.imageAssets=frames.map(f=>({name:f.frame_id,timestamp_ms:f.timestamp_ms,mime:'image/jpeg',base64:jpeg}));
 b.outputSchema=JSON.parse(JSON.stringify(vm.runInContext('videoSemanticContract()',ctx)));
 b.outputSchema.properties.events.items.properties.target.enum=['visible button'];
 return b;
}
test('actual grounding /ai/skill schema narrowing, exact JPEGs and cross-language proof',async()=>{
 const b=grounding(),run=orchestration(),response=await run.request(b),options=run.calls[0].options;
 assert.equal(options.model,'qwen3.8-max');assert.equal(options.response_format.type,'json_schema');
 assert.deepEqual(Array.from(options.response_format.json_schema.schema.properties.events.items.properties.target.enum),['visible button']);
 assert.equal(options.messages[1].content.filter(p=>p.type==='image_url').length,2);
 assert.equal(options.messages[1].content.some(p=>p.min_pixels),false);
 const py=spawnSync('python3',['-c','import json,sys;from task_server.services.ui_video_stage_receipts import make_expected_attestation;print(json.dumps(make_expected_attestation(json.load(sys.stdin))))'],{input:JSON.stringify({...b,prompt:'pixels'}),maxBuffer:2*1024*1024});
 assert.equal(py.status,0,py.stderr.toString());assert.deepEqual(JSON.parse(JSON.stringify(response.videoPolicy)),JSON.parse(py.stdout));
 for(const mutate of [b=>b.outputSchema=null,b=>b.payload.perception_bank={},b=>b.payload.perception_mode='fine-detail',b=>b.imageAssets=[],b=>b.imageAssets[0].name='forged',b=>b.payload.context_frame_ids.push('frame-000'),b=>delete b.payload.owned_frame_ids,b=>b.payload.frames=[...b.payload.frames].reverse()]) {
  const bad=grounding(),rejected=orchestration();mutate(bad);await assert.rejects(rejected.request(bad),/video_/);assert.equal(rejected.calls.length,0);
 }
});

for(const stage of ['grounding','audit']) test(`closed ${stage} actual128 provider parts and independent proof;129 denied`,async()=>{
 const b=grounding();b.videoStage=stage;b.skillVersion=stage==='grounding'?'observe':'audit';
 const jpeg='YQ==';const frames=Array.from({length:128},(_,i)=>({frame_id:`frame-${String(i).padStart(3,'0')}`,timestamp_ms:i*100}));
 b.payload.frames=frames;b.payload.image_frames=frames;b.payload.owned_frame_ids=frames.slice(0,127).map(f=>f.frame_id);b.payload.context_frame_ids=['frame-127'];
 b.imageAssets=frames.map((f,i)=>({name:f.frame_id,timestamp_ms:f.timestamp_ms,mime:'image/jpeg',base64:Buffer.from(`fixture-image-${i}`).toString('base64')}));
 if(stage==='audit')delete b.outputSchema;
 else b.outputSchema.properties.events.items.properties.frame_refs.items.enum=frames.map(f=>f.frame_id);
 const before=structuredClone(b),run=orchestration(),response=await run.request(b);
 assert.deepEqual(b,before);assert.equal(run.calls.length,1);
 const parts=run.calls[0].options.messages[1].content;
 const images=parts.filter(p=>p.type==='image_url');assert.equal(images.length,128);
 for(let i=0;i<128;i++){
  assert.equal(images[i].image_url.url,'data:image/jpeg;base64,'+b.imageAssets[i].base64);
  assert.equal(parts[2*i].text.startsWith(`${frames[i].frame_id} ${frames[i].timestamp_ms}ms`),true);
 }
 assert.match(parts[254].text,/不得作为本包新动作触发/);
 assert.equal(response.videoPolicy.effective_options.image_count,128);
 const py=spawnSync('python3',['-c','import json,sys;from task_server.services.ui_video_stage_receipts import make_expected_attestation;print(json.dumps(make_expected_attestation(json.load(sys.stdin))))'],{input:JSON.stringify({...b,prompt:'pixels'}),maxBuffer:2*1024*1024});
 assert.equal(py.status,0,py.stderr.toString());assert.deepEqual(JSON.parse(JSON.stringify(response.videoPolicy)),JSON.parse(py.stdout));
 for(const bad of ['129','bytes','legacy']){
  const rejected=structuredClone(b),runner=orchestration();
  if(bad==='129'){const f={frame_id:'frame-128',timestamp_ms:12800};rejected.payload.frames.push(f);rejected.payload.owned_frame_ids.push(f.frame_id);rejected.imageAssets.push({name:f.frame_id,timestamp_ms:f.timestamp_ms,mime:'image/jpeg',base64:jpeg});}
  if(bad==='bytes')rejected.imageAssets[0].base64='YQ=='.repeat(10*1024*1024/4+1);
  if(bad==='legacy'){delete rejected.videoStage;delete rejected.outputSchema;}
  await assert.rejects(runner.request(rejected),/video_/);assert.equal(runner.calls.length,0);
 }
});
