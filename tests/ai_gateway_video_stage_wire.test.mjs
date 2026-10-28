import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import {createHash} from 'node:crypto';
import {EventEmitter, once} from 'node:events';
import http from 'node:http';
const source=fs.readFileSync(new URL('../ai-gateway/server.js',import.meta.url),'utf8');
const route={model:'qwen3.8-max',baseUrl:'https://dashscope.aliyuncs.com/compatible-mode/v1',temperature:.1};
const ctx=vm.createContext({Buffer,createHash});
vm.runInContext(source.slice(source.indexOf('function requestedCompletionTokens('),source.indexOf('async function appendAiLog('))+
 source.slice(source.indexOf('function buildUserMessage('),source.indexOf('\nfunction assistantOutputText')),ctx);
function complete(body,selected=route) {ctx.body=body;ctx.route=selected;return JSON.parse(JSON.stringify(vm.runInContext('completionOptionsForRoute(route,"pixels",body)',ctx)));}
const observerSchema=JSON.parse(fs.readFileSync(new URL('../ai_skills/schemas/ui_video_trajectory.schema.json',import.meta.url),'utf8')).oneOf[0];
function project(node, normalized=true) {
 if(node.oneOf)return {[normalized?'anyOf':'oneOf']:node.oneOf.map(child=>project(child,normalized))};
 const out=Object.fromEntries(Object.entries(node).filter(([k])=>['type','enum','additionalProperties'].includes(k)));
 if(out.enum&&normalized)out.type??='string';
 if(node.maxLength===0&&normalized)out.enum=[''];
 if(node.properties) {out.properties=Object.fromEntries(Object.entries(node.properties).map(([k,v])=>[k,project(v,normalized)]));out.required=Object.keys(out.properties);}
 if(node.items)out.items=project(node.items,normalized);
 return out;
}
function schema() {return project(observerSchema);}
function input(stage='perception',n=8) {
 const frames=Array.from({length:n},(_,i)=>({frame_id:`frame-${String(i).padStart(3,'0')}`,timestamp_ms:i*100}));
 const b={skillName:stage==='perception'?'ui_video_perception':'ui_video_trajectory',videoStage:stage,jsonResponse:true,
 skillVersion:stage==='perception'?'v1':stage==='semantic'?'observe':stage,videoPolicyVersion:'video-stage-policy-v1',videoCorrelation:'a'.repeat(32),timeoutMs:120000,videoDeadlineMs:Date.now()+120000,
 payload:{phase:stage==='perception'?'whole-perception':'whole-trajectory',packet_id:'w1',frames,image_frames:frames,
 owned_frame_ids:frames.slice(0,-1).map(f=>f.frame_id),context_frame_ids:[frames.at(-1).frame_id]},
 imageAssets:frames.map(f=>({name:f.frame_id,timestamp_ms:f.timestamp_ms,mime:'image/jpeg',base64:'YQ=='}))};
 if(stage==='semantic') {b.imageAssets=[];delete b.payload.image_frames;b.payload.reasoning_mode='bounded';b.outputSchema=schema();}
 if(stage==='audit')Object.assign(b.payload,{reasoning_mode:'bounded',perception_mode:'fine-detail',perception_frame_ids:frames.slice(0,Math.min(n,12)).map(f=>f.frame_id)});
 return b;
}
test('perception exact final wire preserves 2..12 originals, roles, byte/order and fixed policy',()=>{
 for(const n of [2,8,12]) {
  const b=input('perception',n),before=JSON.stringify(b),wire=complete(b),parts=wire.messages[1].content;
  assert.equal(wire.enable_thinking,false);assert.equal(wire.thinking_budget,undefined);
  assert.equal(wire.temperature,.1);assert.equal(wire.max_tokens,4000);assert.deepEqual(wire.response_format,{type:'json_object'});
  const images=parts.filter(p=>p.type==='image_url');assert.equal(images.length,n);
  images.forEach(p=>{assert.equal(p.min_pixels,1658880);assert.equal(p.image_url.url,'data:image/jpeg;base64,YQ==');});
  assert.match(parts.at(-2).text,/context/);assert.equal(JSON.stringify(b),before);
 }
});
test('semantic final wire is strict text with bounded thinking',()=>{
 const b=input('semantic'),wire=complete(b);
 assert.equal(typeof wire.messages[1].content,'string');assert.equal(wire.max_tokens,10000);
 assert.equal(wire.enable_thinking,true);assert.equal(wire.thinking_budget,1024);
 assert.deepEqual(wire.response_format,{type:'json_schema',json_schema:{name:'video_semantic',strict:true,schema:b.outputSchema}});
});
test('audit retains original images/json_object and only 12 fine images',()=>{
 const b=input('audit',20),wire=complete(b);assert.deepEqual(wire.response_format,{type:'json_object'});
 assert.equal(wire.messages[1].content.filter(p=>p.type==='image_url').length,20);
 assert.equal(wire.messages[1].content.filter(p=>p.min_pixels).length,12);
 assert.equal(wire.thinking_budget,1024);b.payload.perception_frame_ids.push('frame-012');
 assert.throws(()=>complete(b),/video_/);
});
test('invalid stage/scope/schema/role/image combinations fail before provider dispatch',()=>{
 const mutations=[b=>b.videoStage=null,b=>b.videoStage={},b=>b.videoStage='PERCEPTION',b=>b.skillName='other',
 b=>b.payload.phase='whole-trajectory',b=>b.outputSchema=schema(),b=>b.jsonResponse=false,
 b=>b.payload.context_frame_ids.push(b.payload.owned_frame_ids[0]),b=>b.payload.owned_frame_ids.reverse(),
 b=>b.imageAssets[0].name='forged',b=>b.payload.frames.reverse(),b=>b.imageAssets[0].base64='@@',
 b=>b.imageAssets[0].base64='a'.repeat(10*1024*1024+1),b=>b.payload.detail_frames=[]];
 for(const mutate of mutations) {const b=input();mutate(b);assert.throws(()=>complete(b),/video_/);}
 for(const n of [1,13])assert.throws(()=>complete(input('perception',n)),/video_/);
 for(const mutate of [b=>delete b.outputSchema,b=>b.imageAssets=input().imageAssets,b=>b.payload.reasoning_mode='x',
 b=>b.outputSchema.properties.events.items.enum=[{}],b=>b.outputSchema.extra=true,b=>b.outputSchema.properties.events.items.enum=['x'.repeat(33000)]]) {
  const b=input('semantic');mutate(b);assert.throws(()=>complete(b),/video_/);
 }
 const b=input();delete b.videoStage;b.outputSchema=schema();assert.throws(()=>complete(b),/video_/);
});
test('stages require official exact Max route',()=>{
 for(const stage of ['perception','semantic','audit']) for(const change of [{model:'qwen3.8-flash'},
 {baseUrl:'https://example.com/v1'},{baseUrl:'http://dashscope.aliyuncs.com/v1'},
 {baseUrl:'https://dashscope.aliyuncs.com@evil.example/v1'},{baseUrl:'https://dashscope.aliyuncs.com.evil.example/v1'}])
  assert.throws(()=>complete(input(stage),{...route,...change}),/video_.*route/);
});
function orchestration(fail=false, usage={prompt_tokens:1,completion_tokens:2,total_tokens:3},dispatch=null) {
 const calls=[],logs=[];let handler;
 const providers={providers:{primary:{...route,name:'primary'},backup:{...route,name:'backup'}}};
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
test('actual /ai/skill forwards capabilities, no fallback, and actual route/usage trace',async()=>{
 for(const stage of ['perception','semantic','audit']) {
  const b=input(stage);b.providerId='primary';b.fallbackModelConfig={providerId:'backup',model:'qwen3.8-max'};
  const run=orchestration(),response=await run.request(b);
  assert.equal(run.calls.length,1);assert.equal(response.model,'qwen3.8-max');assert.equal(response.providerId,'primary');
  assert.equal(response.fallbackUsed,false);assert.equal(response.finishReason,'stop');assert.equal(response.usage.totalTokens,3);
  assert.equal(run.calls[0].options.max_tokens,stage==='perception'?4000:10000);
  const failed=orchestration(true);await assert.rejects(failed.request(b),/video_stage_provider_failed/);assert.equal(failed.calls.length,1);
  const rejected=orchestration();b.payload.phase='invalid';await assert.rejects(rejected.request(b),/video_/);assert.equal(rejected.calls.length,0);
 }
});

test('new stage audit logs contain only completion metadata, no request or response preview',async()=>{
 const run=orchestration();await run.request(input());
 assert.equal(run.logs.length,1);
 assert.equal(run.logs[0].inputPreview,'[video stage content omitted]');
 assert.equal(run.logs[0].outputPreview,'[video stage content omitted]');
 assert.equal(run.logs[0].model,'qwen3.8-max');assert.equal(run.logs[0].finishReason,'stop');
});

test('new perception skill cannot silently use generic image truncation without stage',()=>{
 const b=input();delete b.videoStage;assert.throws(()=>complete(b),/video_stage_required/);
});

test('representative legacy final wire remains exactly unchanged',()=>{
 const b={skillName:'ordinary_skill',payload:{unchanged:true},jsonResponse:true,imageAssets:[],maxTokens:321};
 assert.deepEqual(complete(b),{model:'qwen3.8-max',messages:[{role:'system',content:'pixels'},
  {role:'user',content:`请根据以下 JSON 输入完成任务：\n${JSON.stringify({action:'chat',input:b},null,2)}`}],
  temperature:.1,max_tokens:321,response_format:{type:'json_object'},enable_thinking:false});
});

test('skill handler does not coerce malformed stage JSON/image options',async()=>{
 for(const field of ['jsonResponse','imageAssets']) for(const value of [null,0,'',false]) {
  const run=orchestration(),b=input('semantic');b[field]=value;
  await assert.rejects(run.request(b),/video_/);assert.equal(run.calls.length,0);
 }
 const b=input('semantic');b.outputSchema.properties.events.items.properties.action.enum=['aiTap','aiTap'];
 assert.throws(()=>complete(b),/video_output_schema_invalid/);
});

test('strict semantic provider subset retains the original full observer schema and scroll union',()=>{
 const b=input('semantic');b.outputSchema=project(observerSchema,false);
 assert.deepEqual(complete(b).response_format.json_schema.schema,schema());
 for(const variants of [[],[{}],Array.from({length:9},()=>({type:'string'}))]) {
  b.outputSchema=schema();b.outputSchema.properties.events.items.properties.value={anyOf:variants};
  assert.throws(()=>complete(b),/video_output_schema_invalid/);
 }
});

test('strict semantic schema rejects replacement event/state/enum scaffolds',()=>{
 for(const mutate of [s=>s.properties.events.items={type:'string'},s=>{
   const state=s.properties.states.items;delete state.properties.description;state.required=state.required.filter(k=>k!=='description');},
   s=>{const event=s.properties.events.items;event.properties.extra={type:'string'};event.required.push('extra');},
   s=>s.properties.events.items.properties.action.enum=['made_up']]) {
   const b=input('semantic');mutate(b.outputSchema);assert.throws(()=>complete(b),/video_output_schema_invalid/);
 }
});

test('empty-trajectory image audit scans all original images without fine selection',async()=>{
 const b=input('audit');delete b.payload.perception_mode;delete b.payload.perception_frame_ids;b.payload.trajectory=[];
 const wire=complete(b);assert.equal(wire.messages[1].content.filter(p=>p.type==='image_url').length,8);
 assert.equal(wire.messages[1].content.filter(p=>p.min_pixels).length,0);
 assert.deepEqual(wire.response_format,{type:'json_object'});assert.equal(wire.thinking_budget,1024);
 const run=orchestration();await run.request(b);assert.equal(run.calls.length,1);
});


test('nonthinking perception validates raw reasoning token types before coercion',async()=>{
 for(const value of [false,null,'0',-1,1]) for(const usage of [
  {completion_tokens_details:{reasoning_tokens:value}},{completionTokensDetails:{reasoningTokens:value}},
  {reasoningTokens:value},{reasoning_tokens:value}]) {
  const run=orchestration(false,usage);
  await assert.rejects(run.request(input()),/video_perception_reasoning_usage_invalid/);
  assert.equal(run.calls.length,1);assert.equal(run.logs[0].success,false);
 }
 for(const usage of [{},{completion_tokens_details:{reasoning_tokens:0}}]) {
  const run=orchestration(false,usage);const response=await run.request(input());assert.equal(response.usage.reasoningTokens,0);
 }
});


test('exact version and policy metadata cannot be inferred from phase',()=>{
 for(const field of ['skillVersion','videoPolicyVersion','videoCorrelation']) {
  const b=input();delete b[field];assert.throws(()=>complete(b),/video_stage_/);
 }
 const b=input('audit');b.skillVersion='observe';assert.throws(()=>complete(b),/video_stage_scope/);
});

test('seven exact stage policies attest actual canonical provider options',async()=>{
 const configs={overview:['ui_video_trajectory','overview','whole-trajectory',10000,'qwen3.8-flash'],
 consolidate:['ui_video_steps','consolidate','whole-consolidate',10000,'qwen3.8-max'],
 outcome:['ui_video_observe','outcome','whole-observe',4000,'qwen3.8-max'],
 inputs:['ui_video_observe','inputs','whole-observe',3000,'qwen3.8-max']};
 for(const [stage,[skill,version,phase,tokens,model]] of Object.entries(configs)) {
  const b=input('audit');delete b.payload.perception_mode;delete b.payload.perception_frame_ids;
  Object.assign(b,{videoStage:stage,skillName:skill,skillVersion:version,model});b.payload.phase=phase;
  const wire=complete(b,{...route,model});assert.equal(wire.max_tokens,tokens);assert.equal(wire.thinking_budget,1024);
  const run=orchestration(),r=await run.request(b);assert.equal(r.model,model);
  assert.equal(r.videoPolicy.stage,stage);assert.equal(r.videoPolicy.correlation,b.videoCorrelation);
  assert.equal(r.videoPolicy.effective_options.max_tokens,tokens);assert.match(r.videoPolicy.provider_options_sha256,/^[a-f0-9]{64}$/);
 }
});

for(const mode of ['disconnect','deadline'])test(`actual HTTP ${mode} aborts provider transport and forbids late response`,{timeout:5000},async()=>{
 let transportStarted,transportClosed;const started=new Promise(r=>transportStarted=r),closed=new Promise(r=>transportClosed=r);
 const provider=http.createServer((req,res)=>{req.resume();transportStarted();res.on('close',transportClosed);});
 provider.listen(0,'127.0.0.1');await once(provider,'listening');
 const run=orchestration(false,{},async(options,requestOptions)=>{
  assert.ok(requestOptions.signal instanceof AbortSignal);
  const response=await fetch(`http://127.0.0.1:${provider.address().port}`,{method:'POST',body:JSON.stringify(options),signal:requestOptions.signal});
  return response.json();
 });
 let completed;const done=new Promise(r=>completed=r);let writes=0;
 const gateway=http.createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;req.body=JSON.parse(raw);
  res.json=value=>{writes++;res.end(JSON.stringify(value));};
  try {await run.handler(req,res);} catch(error){if(!res.destroyed)res.end('failed');} finally{completed();}
 });
 gateway.listen(0,'127.0.0.1');await once(gateway,'listening');
 const controller=new AbortController();const b=input();b.prompt='pixels';
 if(mode==='deadline'){b.timeoutMs=150;b.videoDeadlineMs=Date.now()+150;}
 const response=fetch(`http://127.0.0.1:${gateway.address().port}`,{method:'POST',body:JSON.stringify(b),signal:controller.signal}).catch(()=>null);
 try {
  await started;if(mode==='disconnect')controller.abort();
  await closed;await done;await response;assert.equal(run.calls.length,1);assert.equal(writes,0);
 } finally {controller.abort();gateway.closeAllConnections();provider.closeAllConnections();gateway.close();provider.close();}
});

test('missing actual provider model never substitutes selected model; arbitrary provider error stays private',async()=>{
 for(const model of [undefined,'qwen3.8-flash']) {
  const run=orchestration(false,{},async()=>({model,choices:[{message:{content:'raw'},finish_reason:'stop'}]}));
  await assert.rejects(run.request(input()),/video_stage_actual_model_invalid/);assert.equal(run.calls.length,1);
 }
 const run=orchestration(false,{},async()=>{throw new Error('private key or provider body');});
 await assert.rejects(run.request(input()),/video_stage_provider_failed/);
 assert.equal(JSON.stringify(run.logs).includes('private key'),false);
});

test('normal completed response removes cancellation listeners without aborting it',async()=>{
 let signal;const run=orchestration(false,{},async(o,r)=>{signal=r.signal;return {model:o.model,choices:[{message:{content:'{}'},finish_reason:'stop'}]};});
 const req=Object.assign(new EventEmitter(),{body:{...input(),prompt:'pixels'}});
 const res=Object.assign(new EventEmitter(),{json(){this.writableEnded=true;this.emit('close');}});
 await run.handler(req,res);assert.equal(signal.aborted,false);
 assert.equal(req.listenerCount('aborted'),0);assert.equal(res.listenerCount('close'),0);
 const badReq=Object.assign(new EventEmitter(),{body:{...input(),timeoutMs:-1,prompt:'pixels'}});
 const badRes=Object.assign(new EventEmitter(),{json(){}});
 await assert.rejects(run.handler(badReq,badRes),/video_stage_deadline/);
 assert.equal(badReq.listenerCount('aborted'),0);assert.equal(badRes.listenerCount('close'),0);
});
