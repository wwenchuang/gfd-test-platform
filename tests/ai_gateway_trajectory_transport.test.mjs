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
