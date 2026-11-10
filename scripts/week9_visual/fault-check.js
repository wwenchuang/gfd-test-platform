'use strict';

// Controlled faults exercise the pinned SDK and the live runner's exact model/egress guards.
// No production session, endpoint traffic or paid model call is needed.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const {createAgent,modelEndpoint,LIMITS}=require('./model');
const {failure}=require('./run');
const {allowRequest}=require('../../task_server/fixtures/week5_web/policy');

async function main() {
  const output=process.argv[2];
  if (!path.isAbsolute(output||'')) throw Error('absolute_output_required');
  const browser=await chromium.launch({headless:true,args:['--disable-background-networking','--disable-dev-shm-usage']});
  const checks=[];
  try {
    const page=await browser.newPage({viewport:{width:600,height:400}});
    await page.route('**/*',route=>route.abort());
    await page.setContent('<main><button>Locate</button></main>');
    let received=0, mode='403';
    global.fetch=async(url,options)=>{
      assert.equal(url,'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions');
      assert.equal(options.method,'POST');assert.equal(options.redirect,'error');
      const body=JSON.parse(options.body);
      assert.ok(body.messages.some(m=>Array.isArray(m.content)&&m.content.some(c=>c.type==='image_url')));
      assert.equal(body.max_tokens,LIMITS.output_tokens_per_call);
      received++;
      if (mode==='403') return new Response(null,{status:403});
      if (mode==='timeout') return new Promise((_,reject)=>{
        const signal=options.signal;
        signal.addEventListener('abort',()=>reject(signal.reason),{once:true});
      });
      throw Error('unexpected_test_request');
    };
    const credentials={base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',model_key:'offline-placeholder'};
    async function exercise(id,title,run,otherRuns,step,expectedCount,check) {
      const prior=received,started=Date.now();
      const report={model:'qwen3.8-max',runs:[run,...otherRuns]};
      const agent=createAgent(page,credentials,run,report,()=>{});
      let caught;
      try {await agent.locate(step,'Locate the button');} catch (error) {caught=error;}
      finally {await agent.close();}
      assert.ok(caught);assert.equal(received-prior,expectedCount);
      const observed=check(caught);
      checks.push({id,title,passed:true,observed,simulated_requests:received-prior,elapsed_ms:Date.now()-started});
    }
    const newRun=()=>({model_calls:0,steps:[]});
    const newStep=run=>{const step={calls:[]};run.steps.push(step);return step;};
    const budget=error=>{
      assert.match(String(error.message),/model_budget/);
      return '预算在模型请求前拦截，外部调用 0';
    };
    const stepRun=newRun(),used=newStep(stepRun);
    used.calls.push({status:'received',tokens:8});
    await exercise('step_budget','单步重复请求',stepRun,[],used,0,budget);
    const runLimit=newRun();runLimit.model_calls=LIMITS.model_calls_per_run;
    await exercise('run_budget','单场景模型调用达到上限',runLimit,[],newStep(runLimit),0,budget);
    const batchLimit=newRun();
    await exercise('batch_budget','整批模型调用达到上限',batchLimit,[{model_calls:LIMITS.model_calls_total}],newStep(batchLimit),0,budget);
    const rejected=newRun();
    await exercise('authorization','模拟模型服务拒绝授权',rejected,[],newStep(rejected),1,error=>{
      assert.equal(failure(error,rejected).reason,'provider_http_403');return 'provider_http_403';
    });
    mode='timeout';const timed=newRun();
    await exercise('timeout','模拟模型服务连接后无回执',timed,[],newStep(timed),1,error=>{
      const call=timed.steps[0].calls[0];
      assert.equal(failure(error,timed).reason,'model_timeout');assert.equal(call.tokens,null);
      assert.ok(call.elapsed_ms>=LIMITS.model_timeout_ms-50);
      return 'model_timeout，usage 未知，未重试';
    });
    const origin='http://127.0.0.1:8088';
    assert.equal(allowRequest(origin,'POST',origin+'/api/ui-executions/catalog'),false);
    assert.equal(allowRequest(origin,'GET','https://example.org/task-manager.html'),false);
    assert.throws(()=>modelEndpoint('https://example.org/compatible-mode/v1'));
    checks.push({id:'egress',title:'工作台写入、外部页面和非配置模型地址',passed:true,
      observed:'三类越界请求均被规则拒绝',simulated_requests:0,elapsed_ms:0});
    const receipt={version:1,kind:'controlled_fault',generated_at:new Date().toISOString(),browser:browser.version(),
      midscene:'1.10.7',playwright:require('playwright/package.json').version,model_timeout_ms:LIMITS.model_timeout_ms,
      real_provider_calls:0,business_runs:0,checks};
    fs.mkdirSync(path.dirname(output),{recursive:true});
    fs.writeFileSync(output+'.tmp',JSON.stringify(receipt,null,2)+'\n',{mode:0o600});
    fs.renameSync(output+'.tmp',output);
    console.log(JSON.stringify({checks:checks.length,passed:checks.length,provider_calls:0}));
  } finally {await browser.close();}
}
main().catch(error=>{console.error(error.name, String(error.message).split('\n')[0].slice(0,130));process.exitCode=1;});
