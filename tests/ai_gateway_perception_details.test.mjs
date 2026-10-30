import assert from 'node:assert/strict';
import test from 'node:test';
import {execFileSync} from 'node:child_process';
import {complete as oldComplete,input,ctx,route,orchestration} from './ai_gateway_video_test_helpers.mjs';
import vm from 'node:vm';
function complete(body) {
 ctx.body=body;ctx.route=route;
 return JSON.parse(JSON.stringify(vm.runInContext("completionOptionsForRoute(route, '你是移动端测试平台 AI Skill 执行器。必须严格遵守用户输入的 Skill Prompt，只输出合法 JSON。', body, {userMessage:'pixels'})",ctx)));
}
function details(n=3) {
 const b=input('perception',n);b.payload.detail_frames=[];
 for(const index of [0,n-1])for(let i=0;i<3;i++) {
  const f=b.payload.frames[index],d={frame_id:`${f.frame_id}-detail-${i}`,source_frame_id:f.frame_id,timestamp_ms:f.timestamp_ms,source_size:[120,302],box:[0,Math.floor(302*i/3),120,Math.floor(302*(i+1)/3)]};
  b.payload.detail_frames.push(d);b.imageAssets.push({name:d.frame_id,timestamp_ms:d.timestamp_ms,mime:'image/jpeg',base64:'YQ=='});
 }return b;
}
test('detail wire labels, fine IDs and exact independent Python proof match',()=>{
 const b=details(),before=JSON.stringify(b),wire=complete(b),parts=wire.messages[1].content;
 assert.equal(parts.filter(p=>p.type==='image_url').length,9);
 assert.equal(parts[6].text,'frame-000-detail-0 0ms [source_frame_id=frame-000 box=[0,0,120,100] role=owned; same-frame visual assistance; no separate time/event/action or ownership]');
 assert.match(parts[12].text,/role=context;/);assert.equal(JSON.stringify(b),before);
 const expected=JSON.parse(execFileSync('python3',['-c',"import json,sys;from task_server.services.ui_video_stage_receipts import expected_provider_options,make_expected_attestation; b=json.load(sys.stdin); print(json.dumps([expected_provider_options(b),make_expected_attestation(b)]))"],{input:JSON.stringify({...b,prompt:'pixels'})}));
 assert.deepEqual(wire,expected[0]);
 ctx.body=b;ctx.options=wire;
 const proof=JSON.parse(JSON.stringify(vm.runInContext('videoPolicyAttestation(body, options)',ctx)));
 assert.deepEqual(proof,expected[1]);
});
test('empty optional details preserve exact old wire',()=>{
 const b=input();const old=complete(b);b.payload.detail_frames=[];assert.deepEqual(complete(b),old);
});
test('closed metadata, geometry, complete thirds, order and unchanged limits reject',()=>{
 for(const mutate of [b=>b.payload.detail_frames.pop(),b=>b.payload.detail_frames.reverse(),
 b=>b.payload.detail_frames[0].source_frame_id='frame-999',b=>b.payload.detail_frames[0].extra=true,
 b=>b.payload.detail_frames[0].box[0]=false,b=>b.payload.detail_frames[0].box[3]++,
 b=>b.payload.detail_frames[0].source_size[0]=99,b=>b.payload.detail_frames[0].timestamp_ms++,
 b=>b.payload.detail_frames[0].frame_id='frame-000-detail',b=>b.payload.detail_frames[1]=b.payload.detail_frames[0],
 b=>b.imageAssets[3].timestamp_ms=false,b=>b.imageAssets.reverse(),b=>b.imageAssets.push(b.imageAssets.at(-1)),b=>b.imageAssets.at(-1).base64='YQ=='.repeat(10*1024*1024/4)]) {
 const b=details();mutate(b);assert.throws(()=>complete(b),/video_/);
 }
 assert.throws(()=>complete(details(7)),/video_vision_limit/);
 for(const stage of ['semantic','audit']) {const b=input(stage);b.payload.detail_frames=[];assert.throws(()=>complete(b),/video_/);}
});

test('actual perception detail handler validates all assets and roles before dispatch',async()=>{
 const run=orchestration();await run.request(details());
 assert.equal(run.calls.length,1);
 assert.equal(run.calls[0].options.messages[1].content.filter(p=>p.type==='image_url').length,9);
 for(const mutate of [b=>b.imageAssets[3].name='frame-999-detail-0',
 b=>b.imageAssets[3].base64='@@',b=>b.payload.context_frame_ids.push(b.payload.owned_frame_ids[0]),
 b=>b.payload.detail_frames.pop(),b=>b.imageAssets[3].timestamp_ms=false]) {
  const b=details();mutate(b);const rejected=orchestration();
  await assert.rejects(rejected.request(b),/video_/);assert.equal(rejected.calls.length,0);
 }
});
