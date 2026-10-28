import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
const source=fs.readFileSync(new URL('../ai-gateway/server.js',import.meta.url),'utf8');
const fn=source.slice(source.indexOf('function imagePartsFromBody('),source.indexOf('\nfunction assistantOutputText'));
const imageParts=vm.runInNewContext(fn+';imagePartsFromBody');
const frames=Array.from({length:58},(_,i)=>({frame_id:i===57?'frame-end':`frame-${String(i+1).padStart(3,'0')}`,timestamp_ms:i*100}));
const assets=frames.map(f=>({name:f.frame_id,timestamp_ms:f.timestamp_ms,mime:'image/jpeg',base64:'YQ=='}));
const body={skillName:'ui_video_trajectory',payload:{phase:'whole-trajectory',image_frames:frames,context_frame_ids:['frame-end']},imageAssets:assets};
const parts=imageParts(body);
assert.equal(parts.filter(p=>p.type==='image_url').length,58);
assert.equal(parts[0].text,'frame-001 0ms [当前窗口证据，可引用]');
assert.equal(parts[114].text,'frame-end 5700ms [可引用为前后态或结果证据；不得作为本包新动作触发]');
for (const skillName of ['other','ui_video_trajectory_extra']) assert.equal(imageParts({...body,skillName}).length,6);
assert.equal(imageParts({...body,payload:{...body.payload,phase:'whole-observe'}}).length,6);
assert.throws(()=>imageParts({...body,imageAssets:assets.slice(1)}),/video_vision_limit/);
assert.throws(()=>imageParts({...body,imageAssets:[{...assets[0],base64:'a'.repeat(10*1024*1024+1)},...assets.slice(1)]}),/video_vision_limit/);
for (const change of [{name:'fake'},{timestamp_ms:1},{mime:'image/png'}]) assert.throws(()=>imageParts({...body,imageAssets:[{...assets[0],...change},...assets.slice(1)]}),/video_frames_invalid/);
assert.throws(()=>imageParts({...body,payload:{...body.payload,image_frames:[...frames].reverse()},imageAssets:[...assets].reverse()}),/video_frames_invalid/);
assert.throws(()=>imageParts({...body,payload:{...body.payload,detail_frames:[{frame_id:'frame-001-detail',source_frame_id:'frame-001',timestamp_ms:0}]},imageAssets:[...assets,{name:'frame-001-detail',timestamp_ms:0,mime:'image/jpeg',base64:'YQ=='}]}),/video_frames_invalid/);
console.log('trajectory transport: complete timeline, reference labels, exact scope and validation guards passed');

// Exercise actual completion composition with actual image validation, without a client/API.
const context=vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function requestedCompletionTokens('),source.indexOf('async function appendAiLog('))+
  source.slice(source.indexOf('function buildUserMessage('),source.indexOf('\nfunction assistantOutputText')),context);
const route={model:'qwen3.8-max',baseUrl:'https://dashscope.aliyuncs.com/compatible-mode/v1',temperature:0.1};
function completion(input=body,selectedRoute=route) {
  context.body=input;context.route=selectedRoute;
  return JSON.parse(JSON.stringify(vm.runInContext('completionOptionsForRoute(route,"trajectory",body)',context)));
}
const fineBody={...body,jsonResponse:true,maxTokens:2048,timeoutMs:118000,payload:{...body.payload,perception_mode:'fine-detail'}};

test('fine detail adds only uniform fixed sibling pixels and preserves input',()=>{
  for(const host of ['dashscope.aliyuncs.com','dashscope-intl.aliyuncs.com','dashscope-us.aliyuncs.com']) {
    const officialRoute={...route,baseUrl:`https://${host}/compatible-mode/v1`};
    const original=JSON.stringify(fineBody);
    const base=completion({...fineBody,payload:body.payload},officialRoute);
    const fine=completion(fineBody,officialRoute);
    const images=fine.messages[1].content.filter(p=>p.type==='image_url');
    assert.equal(images.length,assets.length);
    for(const part of images) {assert.equal(part.min_pixels,1658880);delete part.min_pixels;}
    // The text retains the explicit mode; images, identities/roles and other options stay equal.
    fine.messages[1].content[0]=base.messages[1].content[0];
    assert.deepEqual(fine,base);
    assert.equal(JSON.stringify(fineBody),original);
    assert.notEqual(fine.enable_thinking,true);assert.equal(fine.thinking_budget,undefined);
    assert.equal(fine.max_tokens,2048);assert.equal(fine.timeout,undefined);assert.equal(fine.maxRetries,undefined);
  }
});

test('explicit invalid scoped modes fail rather than silently degrading',()=>{
  for(const mode of [null,true,false,1,1658880,'','fine','FINE-DETAIL',[],{}]) {
    assert.throws(()=>completion({...fineBody,payload:{...fineBody.payload,perception_mode:mode}}),/video_perception_mode_invalid/);
  }
});

test('fine detail cannot broaden route or protocol support',()=>{
  for(const change of [
    {model:'qwen3.8-flash'},{model:'qwen3.7-plus'},{model:'QWEN3.8-MAX'},
    {baseUrl:'http://dashscope.aliyuncs.com/v1'}, {baseUrl:'https://example.com/v1'},
    {baseUrl:'https://dashscope.aliyuncs.com.evil.example/v1'},
    {baseUrl:'https://dashscope.aliyuncs.com@evil.example/v1'},
  ]) {
    const unsupported={...route,...change};
    assert.throws(()=>completion(fineBody,unsupported),/video_perception_route_unsupported/);
    assert.ok(completion(body,unsupported).messages[1].content.every(p=>!('min_pixels' in p)));
  }
});

test('mode outside exact skill and phase leaves ordinary completion unchanged',()=>{
  for(const change of [{skillName:'other'},{skillName:'ui_video_trajectory_extra'},
    {payload:{...fineBody.payload,phase:'whole-observe'}},
    {skillName:'ui_video_observe',payload:{...fineBody.payload,phase:'whole-observe'}}]) {
    const input={...fineBody,...change};
    for(const mode of ['fine-detail',null,true]) {
      const result=completion({...input,payload:{...input.payload,perception_mode:mode}});
      assert.ok(result.messages[1].content.every(p=>!('min_pixels' in p)));
    }
  }
});

test('fine detail retains image guards before applying options and independent reasoning budget',()=>{
  assert.throws(()=>completion({...fineBody,imageAssets:assets.slice(1)}),/video_vision_limit/);
  assert.throws(()=>completion({...fineBody,imageAssets:[{...assets[0],name:'fake'},...assets.slice(1)]}),/video_frames_invalid/);
  const bounded=completion({...fineBody,payload:{...fineBody.payload,reasoning_mode:'bounded'}});
  assert.equal(bounded.enable_thinking,true);assert.equal(bounded.thinking_budget,1024);
  assert.equal(bounded.messages[1].content.filter(p=>p.type==='image_url').length,58);
  const nonJson=completion({...fineBody,jsonResponse:false});
  assert.equal(nonJson.enable_thinking,undefined);assert.equal(nonJson.thinking_budget,undefined);
  assert.ok(nonJson.messages[1].content.filter(p=>p.type==='image_url').every(p=>p.min_pixels===1658880));
});

const invalidSelections=[null,true,false,1,'frame-001',{},[],['frame-001','frame-001'],
  ['frame-unknown'],[1],[true],[null],[['frame-001']],assets.slice(0,13).map(a=>a.name)];

test('selective detail changes only named owned and context image siblings',()=>{
  for(const selected of [['frame-001','frame-end'],assets.slice(0,12).map(a=>a.name)]) {
    for(const reasoning of [undefined,'bounded']) {
      for(const jsonResponse of [true,false]) {
        const input={...fineBody,jsonResponse,payload:{...fineBody.payload,reasoning_mode:reasoning,perception_frame_ids:selected}};
        const original=JSON.stringify(input);
        const noMode={...input,payload:{...input.payload}};
        delete noMode.payload.perception_mode;delete noMode.payload.perception_frame_ids;
        const expected=completion(noMode);
        const result=completion(input);
        const normalized=JSON.parse(JSON.stringify(result));
        const images=normalized.messages[1].content.filter(p=>p.type==='image_url');
        assert.equal(images.length,assets.length);
        for(const [i,part] of images.entries()) {
          if(selected.includes(assets[i].name)) {assert.equal(part.min_pixels,1658880);delete part.min_pixels;}
          else assert.equal(Object.hasOwn(part,'min_pixels'),false);
        }
        normalized.messages[1].content[0]=expected.messages[1].content[0];
        assert.deepEqual(normalized,expected);
        assert.equal(JSON.stringify(input),original);
        assert.equal(result.enable_thinking,jsonResponse?(reasoning==='bounded'?true:false):undefined);
        assert.equal(result.thinking_budget,jsonResponse&&reasoning==='bounded'?1024:undefined);
      }
    }
  }
});

test('scoped selection rejects malformed, duplicate, unknown and unbounded values',()=>{
  for(const selected of invalidSelections) {
    assert.throws(()=>completion({...fineBody,payload:{...fineBody.payload,perception_frame_ids:selected}}),/video_perception_selection_invalid/);
  }
});

test('selection requires exact mode and image failures retain priority',()=>{
  for(const mode of [undefined,null,true,'fine','FINE-DETAIL']) {
    const input={...fineBody,payload:{...fineBody.payload,perception_frame_ids:['frame-001']}};
    if(mode===undefined) delete input.payload.perception_mode; else input.payload.perception_mode=mode;
    assert.throws(()=>completion(input),/video_perception_selection_invalid/);
  }
  const invalid={...fineBody,payload:{...fineBody.payload,perception_frame_ids:null}};
  assert.throws(()=>completion({...invalid,imageAssets:assets.slice(1)}),/video_vision_limit/);
  assert.throws(()=>completion({...invalid,imageAssets:[{...assets[0],name:'fake'},...assets.slice(1)]}),/video_frames_invalid/);
  assert.throws(()=>completion({...invalid,imageAssets:[{...assets[0],base64:'a'.repeat(10*1024*1024+1)},...assets.slice(1)]}),/video_vision_limit/);
});

test('selection outside exact scope is ignored for ordinary image options',()=>{
  for(const change of [{skillName:'other'},{skillName:'ui_video_trajectory_extra'},
    {payload:{...fineBody.payload,phase:'whole-observe'}},
    {skillName:'ui_video_observe',payload:{...fineBody.payload,phase:'whole-observe'}}]) {
    for(const selected of invalidSelections) {
      const input={...fineBody,...change};
      input.payload={...input.payload,perception_frame_ids:selected};
      const result=completion(input);
      assert.ok(result.messages[1].content.every(p=>!('min_pixels' in p)));
    }
  }
});

test('selective detail retains official route restrictions',()=>{
  const input={...fineBody,payload:{...fineBody.payload,perception_frame_ids:['frame-end']}};
  for(const host of ['dashscope.aliyuncs.com','dashscope-intl.aliyuncs.com','dashscope-us.aliyuncs.com']) {
    assert.equal(completion(input,{...route,baseUrl:`https://${host}/v1`}).messages[1].content.filter(p=>p.min_pixels).length,1);
  }
  for(const change of [{model:'qwen3.8-flash'},{model:'QWEN3.8-MAX'},
    {baseUrl:'http://dashscope.aliyuncs.com/v1'},{baseUrl:'https://example.com/v1'},
    {baseUrl:'https://dashscope.aliyuncs.com.evil.example/v1'},{baseUrl:'https://dashscope.aliyuncs.com@evil.example/v1'}]) {
    assert.throws(()=>completion(input,{...route,...change}),/video_perception_route_unsupported/);
  }
});

// Run the actual /ai/skill handler, route selection, completion builder and retry
// loop. Only configuration I/O, logging and provider clients are replaced locally.
function skillOrchestration({primaryFails=true,primaryChange={}}={}) {
  const calls=[];const logs=[];let handler;
  const providers={providers:{
    primary:{...route,...primaryChange,name:'primary'},
    backup:{...route,baseUrl:'https://dashscope-intl.aliyuncs.com/v1',name:'backup'},
  }};
  const router={generate_case:{providerId:'primary',fallbackProviderIds:['backup']}};
  const sandbox=vm.createContext({
    Buffer,DYNAMIC_PROVIDER_PREFIX:'dynamic:',ROUTER_FILE:'offline-router',
    AI_CALL_TIMEOUT_MS:120000,AI_CALL_FALLBACK_RESERVE_MS:10000,MOCK_ENABLED:false,
    SKILL_ACTION_MAP:{ui_video_trajectory:'generate_case'},
    readJson:async()=>router,readProviders:async()=>providers,
    uuidv4:()=> 'offline-call',appendAiLog:async entry=>logs.push(entry),
    sanitizeError:error=>String(error.message),preview:()=> 'offline',
    asyncRoute:fn=>fn,app:{post:(_path,fn)=>{handler=fn;}},
    clientForRoute:selectedRoute=>({chat:{completions:{create:async options=>{
      calls.push({providerId:selectedRoute.providerId,options});
      if(primaryFails&&selectedRoute.providerId==='primary') throw new Error('primary timeout');
      return {choices:[{message:{content:'{}'},finish_reason:'stop'}],usage:{}};
    }}}}),
  });
  for(const [start,end] of [
    ['function normalizeLegacyProviderId(', 'async function discoverProviderChannel('],
    ['function routeFromProviderConfig(', 'function clientForRoute('],
    ['function requestedCallTimeoutMs(', 'async function appendAiLog('],
    ['function stripMarkdownFence(', 'function mockAiOutput('],
    ["app.post('/ai/skill'", "app.post('/ai/analyze-failure'"],
  ]) vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end)),sandbox);
  return {calls,logs,async request(input) {
    let response;
    await handler({body:input},{json:value=>{response=value;}});
    return response;
  }};
}

test('fine-detail skill forces one provider even when caller permits official Max fallback',async()=>{
  for(const explicitProvider of [false,true]) {
    for(const selection of [undefined,['frame-001','frame-end']]) {
      const input={...fineBody,prompt:'trajectory',disableFallback:false,
        ...(explicitProvider?{providerId:'primary',model:'qwen3.8-max'}:{}),
        fallbackModelConfig:{providerId:'backup',model:'qwen3.8-max'},
        payload:{...fineBody.payload,...(selection?{perception_frame_ids:selection}:{})}};
      const original=JSON.stringify(input);
      const run=skillOrchestration();
      await assert.rejects(run.request(input),/primary timeout/);
      assert.deepEqual(run.calls.map(call=>call.providerId),['primary']);
      assert.equal(run.logs.length,1);assert.equal(run.logs[0].fallbackTotal,1);
      assert.equal(JSON.stringify(input),original);
    }
  }
});

test('default and unrelated skill calls retain configured official Max fallback',async()=>{
  const withoutMode={...fineBody.payload};delete withoutMode.perception_mode;
  for(const change of [
    {payload:withoutMode},{skillName:'other'},{skillName:'ui_video_trajectory_extra'},
    {payload:{...fineBody.payload,phase:'whole-observe'}},
  ]) {
    const run=skillOrchestration();
    const response=await run.request({...fineBody,prompt:'trajectory',disableFallback:false,
      providerId:'primary',fallbackModelConfig:{providerId:'backup',model:'qwen3.8-max'},...change});
    assert.deepEqual(run.calls.map(call=>call.providerId),['primary','backup']);
    assert.equal(response.fallbackUsed,true);assert.equal(response.fallbackIndex,1);
    assert.equal(response.providerId,'backup');assert.equal(run.logs[0].fallbackTotal,2);
  }
});

test('skill orchestration preserves image guard priority, selection and exact route rejection',async()=>{
  const invalidSelection={...fineBody,prompt:'trajectory',payload:{...fineBody.payload,perception_frame_ids:null}};
  for(const [input,primaryChange,error] of [
    [{...invalidSelection,imageAssets:assets.slice(1)},{model:'qwen3.8-flash'},/video_vision_limit/],
    [{...invalidSelection,imageAssets:[{...assets[0],name:'fake'},...assets.slice(1)]},{},/video_frames_invalid/],
    [invalidSelection,{},/video_perception_selection_invalid/],
    [{...fineBody,prompt:'trajectory'},{model:'qwen3.8-flash'},/video_perception_route_unsupported/],
    [{...fineBody,prompt:'trajectory'},{baseUrl:'https://dashscope.aliyuncs.com.evil.example/v1'},/video_perception_route_unsupported/],
  ]) {
    const run=skillOrchestration({primaryChange});
    await assert.rejects(run.request(input),error);
    assert.equal(run.calls.length,0);
  }
});

test('successful fine-detail skill reports primary route with independent bounded reasoning',async()=>{
  const run=skillOrchestration({primaryFails:false});
  const response=await run.request({...fineBody,prompt:'trajectory',disableFallback:false,
    payload:{...fineBody.payload,reasoning_mode:'bounded',perception_frame_ids:['frame-end']}});
  assert.equal(response.fallbackUsed,false);assert.equal(response.fallbackIndex,0);
  assert.equal(response.providerId,'primary');assert.equal(run.logs[0].fallbackTotal,1);
  assert.equal(run.calls[0].options.thinking_budget,1024);
  assert.equal(run.calls[0].options.messages[1].content.filter(part=>part.min_pixels).length,1);
});
