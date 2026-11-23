import subprocess

from test_mac_runner_runtime_config import load_runner


def test_missing_data_json_requeries_only_the_same_boolean_observation():
    runner = load_runner()
    js = runner.RUNNER_ZXB_OBSERVATION_RETRY + r"""
const assert = require('node:assert/strict');
const config = {openaiBaseURL:'https://dashscope.aliyuncs.com/compatible-mode/v1',modelName:'qwen3.7-plus',
                modelFamily:'qwen3',retryCount:0,timeout:1000};
const missing = () => Object.assign(new Error('XML parse error: Missing required field: data-json'), {
  name:'ServiceError', dump:{type:'extract',data:null,taskInfo:{rawResponse:'纯文字'}}});
const answer = value => ({data:{StatementIsTruthy:value}});
const demand={StatementIsTruthy:'original'}, ctx={screenshot:{base64:'fixture'}};
function setup(replies, {cfg=config, enabled=true, query=demand, pollTwice=false}={}) {
  process.env.MIDSCENE_ZXB_MODEL_CONNECT_RETRY = enabled?'1':'0';
  const calls=[];
  const service={async extract(...args) {
    calls.push(args);const r=replies.shift(); if (typeof r==='function') return r(...args);
    if(r instanceof Error) throw r; return r;
  }};
  const run=()=>service.extract(query,{config:cfg},{screenshotIncluded:true},'',undefined,ctx);
  const agent={service,aiAssert:run,taskExecutor:{waitFor:async()=>{
    const result=await run();
    return pollTwice && !result.data.StatementIsTruthy ? run() : result;
  }}};
  installZxbObservationRetry(agent);
  installZxbObservationRetry(agent);
  return {calls,run:()=>agent.taskExecutor.waitFor('condition',{timeoutMs:50}),assert:()=>agent.aiAssert('condition'),
          bare:run, wait:(...args)=>agent.taskExecutor.waitFor(...args)};
}
(async()=>{
  for(const method of ['run','assert']){
    const expected=answer(true); const sample=setup([missing(),expected]);
    assert.equal(await sample[method](),expected);assert.equal(sample.calls.length,2);
    for(let i=0;i<6;i++) assert.equal(sample.calls[0][i],sample.calls[1][i]);
    assert.ok(sample.calls[1][6].abortSignal instanceof AbortSignal);
  }
  for(const options of [{enabled:false},{cfg:{...config,retryCount:1}},{cfg:{...config,modelName:'different'}},
                        {cfg:{...config,openaiBaseURL:'https://other.example'}},{query:{String:'business'}}]){
    const first=missing();const s=setup([first],options);await assert.rejects(s.run,e=>e===first);assert.equal(s.calls.length,1);
  }
  const ordinary=setup([missing()]);await assert.rejects(ordinary.bare);assert.equal(ordinary.calls.length,1);
  for(const options of [undefined,{}, {timeoutMs:0}, {timeoutMs:'500'}]){
    const s=setup([missing()]);await assert.rejects(()=>s.wait('x',options),e=>e.name==='ServiceError');assert.equal(s.calls.length,1);
  }
  const no=answer(false);const normal=setup([no]);assert.equal(await normal.run(),no);assert.equal(normal.calls.length,1);
  const afterFalse=setup([missing(),no]);assert.equal(await afterFalse.run(),no);assert.equal(afterFalse.calls.length,2);
  const polling=setup([missing(),no,missing()],{pollTwice:true});await assert.rejects(polling.run);assert.equal(polling.calls.length,3);
  for(const bad of [missing(),answer('false'),answer('模型高亮'),{data:'yes'}]){
    const s=setup([missing(),bad]);await assert.rejects(s.run);assert.equal(s.calls.length,2);
  }
  const other=setup([new Error('401')]);await assert.rejects(other.run,/401/);assert.equal(other.calls.length,1);
  const controller=new AbortController();const aborted=setup([()=>{controller.abort();throw missing();}]);
  await assert.rejects(()=>aborted.wait('x',{timeoutMs:100,abortSignal:controller.signal}));assert.equal(aborted.calls.length,1);
  const slow=setup([async()=>{await new Promise(r=>setTimeout(r,15));throw missing();}]);
  await assert.rejects(()=>slow.wait('x',{timeoutMs:5}));assert.equal(slow.calls.length,1);
  const inflight=setup([missing(),async(...args)=>{await new Promise(r=>setTimeout(r,20));return answer(true);}]);
  await assert.rejects(()=>inflight.wait('x',{timeoutMs:10}));assert.equal(inflight.calls.length,2);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    subprocess.run(["node", "-e", js], check=True, capture_output=True, text=True, timeout=6)
