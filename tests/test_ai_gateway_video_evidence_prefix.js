// Offline actual Gateway slices: no provider, listener or application import.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const helpers = import('./ai_gateway_video_test_helpers.mjs');
const system = '你是移动端测试平台 AI Skill 执行器。必须严格遵守用户输入的 Skill Prompt，只输出合法 JSON。';
async function bodyFor(stage) {
 const {input,schema} = await helpers;
 if(stage==='timeline') {
  const raw=Buffer.alloc(32);raw.write('ftypisom',4);
  return {...input(),skillName:'ui_video_timeline',skillVersion:'v1',videoStage:stage,
   payload:{phase:'whole-timeline',video:{source_sha256:require('crypto').createHash('sha256').update(raw).digest('hex'),source_bytes:32,duration_ms:1000,fps:4,max_pixels:655360,total_pixels:268435456}},
   imageAssets:[{name:'source-video',mime:'video/mp4',base64:raw.toString('base64'),timestamp_ms:0}]};
 }
 const configs={overview:['ui_video_trajectory','overview','whole-trajectory'],consolidate:['ui_video_steps','consolidate','whole-consolidate'],outcome:['ui_video_observe','outcome','whole-observe'],inputs:['ui_video_observe','inputs','whole-observe'],grounding:['ui_video_trajectory','observe','whole-trajectory']};
 const b=input(configs[stage]?'audit':stage,3);
 if(configs[stage]) {
  [b.skillName,b.skillVersion,b.payload.phase]=configs[stage];b.videoStage=stage;
  delete b.payload.perception_mode;delete b.payload.perception_frame_ids;
  if(stage==='grounding')b.outputSchema=schema();
  if(stage==='overview')b.model='qwen3.8-flash';
 }
 b.imageAssets.forEach((a,i)=>a.base64=Buffer.from(`original-${i}`).toString('base64'));
 return b;
}
for(const stage of ['timeline','perception','overview','semantic','grounding','audit','consolidate','outcome','inputs']) test(`${stage}: actual skill wire has evidence before full dynamic text and Python proof agrees`,async()=>{
 const {orchestration}=await helpers,b=await bodyFor(stage);
 b.prompt='完整 Skill 指令 😀\n'+JSON.stringify({payload:b.payload,claims:['claim-1'],feedback:'preserve exact feedback'});
 const before=structuredClone(b);
 const run=orchestration(),response=await run.request(b,b.prompt),options=run.calls[0].options;
 assert.deepEqual(b,before);assert.equal(options.messages[0].content,system);
 const content=options.messages[1].content;
 if(stage==='semantic')assert.equal(content,b.prompt);
 else {
  assert.deepEqual(JSON.parse(JSON.stringify(content.at(-1))),{type:'text',text:b.prompt});
  assert.equal(content[0].type,stage==='timeline'?'video_url':'text');
  if(stage!=='timeline')for(let i=0;i<b.imageAssets.length;i++) {
   assert.ok(content[2*i].text.startsWith(`${b.imageAssets[i].name} ${b.imageAssets[i].timestamp_ms}ms`));
   assert.equal(content[2*i+1].image_url.url,'data:image/jpeg;base64,'+b.imageAssets[i].base64);
  }
 }
 assert.equal(JSON.stringify(options).includes('cache_control'),false);
 const py=spawnSync('python3',['-c','import sys,json;from task_server.services.ui_video_stage_receipts import expected_provider_options,make_expected_attestation;b=json.load(sys.stdin);print(json.dumps([expected_provider_options(b),make_expected_attestation(b)]))'],{input:JSON.stringify(b),encoding:'utf8'});
 assert.equal(py.status,0,py.stderr);const [expected,proof]=JSON.parse(py.stdout);
 assert.deepEqual(JSON.parse(JSON.stringify(options)),expected);assert.deepEqual(JSON.parse(JSON.stringify(response.videoPolicy)),proof);
});
test('grounding and independent audit share only identical model and labeled evidence prefix',async()=>{
 const {orchestration}=await helpers;
 async function options(b,text) {
  // Invoke actual handler without the helper's fixed prompt wrapper.
  const run=orchestration();let response;
  const {EventEmitter}=require('events');
  await run.handler(Object.assign(new EventEmitter(),{body:{...b,prompt:text}}),Object.assign(new EventEmitter(),{json:r=>response=r}));
  assert.equal(response.success,true);return JSON.parse(JSON.stringify(run.calls[0].options));
 }
 const observer=await bodyFor('grounding'),audit=await bodyFor('audit');delete audit.payload.perception_mode;delete audit.payload.perception_frame_ids;
 const first=await options(observer,'observer narrative PRIVATE'),second=await options(audit,'claims + feedback');
 const prefix=o=>[o.model,o.messages[0],o.messages[1].content.slice(0,-1)];
 assert.deepEqual(prefix(first),prefix(second));assert.equal(JSON.stringify(second).includes('PRIVATE'),false);
 for(const mutate of [b=>b.imageAssets[0].base64='Yg==',b=>{b.imageAssets.pop();b.payload.frames=b.payload.frames.slice(0,-1);b.payload.image_frames=b.payload.frames;b.payload.context_frame_ids=[];},b=>{b.payload.frames[2].frame_id='frame-end';b.imageAssets[2].name='frame-end';b.payload.context_frame_ids=['frame-end'];},b=>{b.payload.frames[0].timestamp_ms=1;b.imageAssets[0].timestamp_ms=1;},b=>{b.payload.context_frame_ids=[];b.payload.owned_frame_ids.push('frame-002');},b=>{b.payload.perception_mode='fine-detail';b.payload.perception_frame_ids=['frame-000'];}]) {
  const changed=structuredClone(audit);mutate(changed);assert.notDeepEqual(prefix(await options(changed,'claims')),prefix(first));
 }
 const differentModel=await options(await bodyFor('overview'),'observer');assert.notEqual(differentModel.model,first.model);
 const reordered=structuredClone(audit);reordered.imageAssets.reverse();await assert.rejects(options(reordered,'claims'),/video_/);
});
for(const value of [undefined,0,7,-1,true,'7',1.5,11,null])test(`cached token report ${String(value)} survives actual SDK and normalized projections only if strict`,async()=>{
 const {orchestration}=await helpers;
 const usage={prompt_tokens:10,completion_tokens:2,total_tokens:12,...(value===undefined?{}:{prompt_tokens_details:{cached_tokens:value}})};
 const run=orchestration(false,usage),response=await run.request(await bodyFor('audit'));
 const expected=Number.isInteger(value)&&value>=0&&value<=10&&typeof value==='number'?{cachedTokens:value}:{};
 for(const u of [response.usage,run.logs[0].usage])assert.deepEqual(Object.fromEntries(Object.entries(u).filter(([k])=>k==='cachedTokens')),expected);
});

test('legacy actual skill visual text-first and no-image options remain exact',async()=>{
 const {orchestration}=await helpers;
 const base={skillName:'ordinary_skill',payload:{unchanged:true},jsonResponse:true,maxTokens:321};
 const expected={model:'qwen3.8-max',temperature:.1,max_tokens:321,response_format:{type:'json_object'},enable_thinking:false};
 for(const imageAssets of [[],[{name:'legacy',timestamp_ms:0,mime:'image/jpeg',base64:'YQ=='}]]) {
  const b={...base,imageAssets},run=orchestration();await run.request(b,'complete legacy prompt');
  const content=imageAssets.length?[{type:'text',text:'complete legacy prompt'},{type:'image_url',image_url:{url:'data:image/jpeg;base64,YQ=='}}]:'complete legacy prompt';
  assert.deepEqual(JSON.parse(JSON.stringify(run.calls[0].options)),{...expected,messages:[{role:'system',content:system},{role:'user',content}]});
 }
});
test('raw nested cached zero wins over a normalized nonzero report; invalid raw does not fall back',async()=>{
 const {orchestration}=await helpers;
 for(const cached of [0,-1,null]) {
  const run=orchestration(false,{prompt_tokens:10,prompt_tokens_details:{cached_tokens:cached},cachedTokens:7});
  const response=await run.request(await bodyFor('audit'));
  for(const usage of [response.usage,run.logs[0].usage])assert.deepEqual(Object.fromEntries(Object.entries(usage).filter(([k])=>k==='cachedTokens')),cached===0?{cachedTokens:0}:{});
 }
});
for(const prompt of [undefined,null,true,'10',-1,1.5,NaN,Infinity,2**53])test(`SDK cache counter rejects invalid raw prompt ${String(prompt)}`,async()=>{
 const {orchestration}=await helpers;
 const run=orchestration(false,{prompt_tokens:prompt,prompt_tokens_details:{cached_tokens:0}});
 const response=await run.request(await bodyFor('audit'));
 for(const usage of [response.usage,run.logs[0].usage])assert.equal(Object.hasOwn(usage,'cachedTokens'),false);
});
