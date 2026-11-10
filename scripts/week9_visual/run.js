'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {checkWorkflow} = require('../../task_server/fixtures/week5_web/workflows');
const {openPage, digest, origin} = require('./guard');
const {createAgent, modelEndpoint, LIMITS} = require('./model');
const scenarios = require('./scenarios');

const viewport={width:1440,height:1000};
const protocolFiles=['run.js','model.js','guard.js','scenarios.js','package-lock.json',
  '../../task_server/fixtures/week5_web/workflows.js','../../task_server/fixtures/week5_web/policy.js','../../task_server/fixtures/week5_web/proxy.js'];
const protocol=digest(Buffer.concat(protocolFiles.map(f=>fs.readFileSync(path.join(__dirname,f)))));
function atomic(file,data) {
  fs.writeFileSync(file+'.tmp',data,{mode:0o600});fs.renameSync(file+'.tmp',file);
}
function projectUsage(run) {
  const calls=run.steps.flatMap(step=>step.calls);
  run.tokens=calls.every(call=>Number.isSafeInteger(call.tokens)) ? calls.reduce((sum,call)=>sum+call.tokens,0):null;
}
function failure(error,run) {
  const call=run.steps.flatMap(step=>step.calls).at(-1);
  if (call && !['received','in_flight'].includes(call.status)) return {status:'blocked',reason:call.status};
  const code=String(error?.message||'').split('\n')[0];
  if (['model_mislocated','invalid_location','baseline_assertion','workflow_assertion','model_no_location'].includes(code)) return {status:'failed',reason:code};
  return {status:'blocked',reason:/^[a-z0-9_]{1,64}$/.test(code)?code:'execution_error'};
}
async function capture(page,dir,label,run) {
  if (page.url()!==origin+'/task-manager.html') throw Error('unexpected_page');
  const pane=page.locator('[data-ui-mode="library"]:visible,[data-ui-mode="start"]:visible');
  if (await pane.count()!==1) throw Error('unexpected_pane');
  const box=await pane.boundingBox();
  if (!box) throw Error('evidence_unavailable');
  const x=Math.max(0,Math.ceil(box.x)), y=Math.max(0,Math.ceil(box.y));
  const width=Math.floor(Math.min(viewport.width,box.x+box.width))-x;
  const height=Math.floor(Math.min(viewport.height,box.y+box.height))-y;
  if (width<1 || height<1) throw Error('evidence_unavailable');
  // page.screenshot without fullPage uses viewport CSS pixels, including after a scroll.
  const bytes=await page.screenshot({clip:{x,y,width,height},timeout:5000});
  const sha256=digest(bytes), file=sha256+'.png';
  const dest=path.join(dir,file);
  if (!fs.existsSync(dest)) atomic(dest,bytes);
  run.screenshots.push({file,sha256,label});
}
async function runOne(browser,conf,report,run,dir,save) {
  const context=await browser.newContext({serviceWorkers:'block',viewport,deviceScaleFactor:1,locale:'zh-CN',timezoneId:'Asia/Shanghai'});
  let prepared, agent, timer, finished=false;
  const execute=async()=>{
    prepared=await openPage(context,conf,run.scenario_id);
    const {state,page}=prepared;
    page.setDefaultTimeout(7000);
    if (run.arm==='ai') agent=createAgent(page,conf,run,report,save);
    const experiment={
      actionTimeoutMs:LIMITS.model_timeout_ms+10000,
      async freeze(asset) {
        const pageAssets=[...state.hashes].filter(([name])=>name==='/task-manager.html'||/\/ui-(simple-entry|test-home)(\.|$)/.test(name)).sort();
        if (pageAssets.length<3) throw Error('page_assets_unavailable');
        const signature=digest(JSON.stringify({asset,pageAssets,browser:browser.version()}));
        const frozen=report.conditions[run.scenario_id];
        if (frozen && frozen!==signature) throw Error('frozen_context_changed');
        report.conditions[run.scenario_id]=signature;
        run.condition_sha256=signature;
        await capture(page,dir,'操作前',run);
        save();
      },
      async act(locator,instruction,method) {
        if (finished || run.steps.length>=LIMITS.model_calls_per_run || await locator.count()!==1 ||
            !await locator.isEnabled() || !await locator.isVisible()) throw Error('target_unavailable');
        await locator.scrollIntoViewIfNeeded();
        const step={index:run.steps.length+1,action:method,calls:[],result:'pending'};run.steps.push(step);
        if (run.arm==='control') await locator[method]();
        else {
          let center;
          try {center=await agent.locate(step,instruction);}
          catch (error) {
            if (!step.calls.length || step.calls.at(-1).status!=='received') throw error;
            throw Error('model_no_location');
          }
          if (finished) throw Error('run_timeout');
          const [x,y]=center;
          const matches=await locator.evaluate((el,{x,y})=>{
            const hit=document.elementFromPoint(x,y);
            return !!hit && (el.contains(hit) || hit.closest('label')?.control===el);
          },{x,y});
          step.target_matched=matches;
          if (!matches) throw Error('model_mislocated');
          // Only the model's original point is dispatched. Never correct or click a different target.
          await page.mouse.click(x,y);
        }
        run.actions++;step.result='clicked';save();
      }
    };
    const outcome=await checkWorkflow({...prepared,scenario:run.scenario_id,experiment});
    if (state.routeError || state.writes) throw Error('network_guard');
    if (outcome.status==='failed') throw Error('workflow_assertion');
    if (outcome.status!=='passed') throw Error(outcome.detail||'precondition');
    if (!run.actions || (run.arm==='ai' && run.model_calls!==run.actions)) throw Error('missing_interaction');
    run.status='passed';run.reason='';
  };
  const started=Date.now();
  try {
    await Promise.race([execute(),new Promise((_,reject)=>{timer=setTimeout(()=>{finished=true;reject(Error('run_timeout'));},LIMITS.run_timeout_ms);})]);
  } catch (error) {
    Object.assign(run,failure(error,run));
  } finally {
    clearTimeout(timer);finished=true;
    if (prepared?.state.auth && !prepared.state.routeError && !prepared.state.writes) {
      try {await capture(prepared.page,dir,'操作后',run);} catch (_) {if (run.status==='passed'){run.status='blocked';run.reason='evidence_unavailable';}}
    } else if (prepared) {run.status='blocked';run.reason='network_guard';}
    const pending=run.steps.find(step=>step.result==='pending');
    if (pending) pending.result=run.reason||'interrupted';
    run.elapsed_ms=Date.now()-started;
    await context.close().catch(()=>{});
    if (agent) await agent.close().catch(()=>{});
    projectUsage(run);save();
  }
}
function schedule(arm) {
  const result=[];
  for(let round=1;round<=LIMITS.rounds;round++) for(const scenario of scenarios)
    for(const method of round%2?['control','ai']:['ai','control']) {
      if (!arm || arm===method) result.push({round,arm:method,scenario_id:scenario.id});
    }
  return result;
}
async function main() {
  process.umask(0o077);
  const [output,capArg='100',arm='']=process.argv.slice(2);
  const cap=Number(capArg);
  if (!output || !path.isAbsolute(output) || !Number.isInteger(cap) || cap<1 || cap>100 || !['','ai','control'].includes(arm)) throw Error('usage');
  const input=fs.readFileSync(0,'utf8');
  if (input.length>8192) throw Error('credentials_input');
  const conf=JSON.parse(input);
  if (!/^[A-Za-z0-9_-]{43}$/.test(conf.token||'') || typeof conf.userId!=='string' || !conf.userId || !conf.model_key ||
      !/^[a-zA-Z0-9_.-]{1,64}$/.test(conf.model||'')) throw Error('credentials_input');
  const endpoint=modelEndpoint(conf.base_url);
  fs.mkdirSync(output,{recursive:true,mode:0o700});
  const dir=path.join(output,'visual-artifacts');fs.mkdirSync(dir,{recursive:true,mode:0o700});
  const reportFile=path.join(output,'w9-report.json');
  const lock=path.join(output,'.run-lock');
  const fd=fs.openSync(lock,'wx',0o600);
  fs.writeFileSync(fd,String(process.pid));fs.closeSync(fd);
  let browser;
  try {
    browser=await chromium.launch({headless:true,args:['--disable-background-networking','--disable-dev-shm-usage']});
    const versions={midscene:'1.10.7',playwright:require('playwright/package.json').version,browser:browser.version(),node:process.version};
    const fingerprint=digest(JSON.stringify({protocol,versions,model:conf.model,endpoint,viewport,owner:digest(conf.userId)}));
    const report=fs.existsSync(reportFile)?JSON.parse(fs.readFileSync(reportFile,'utf8')):{
      version:1,batch_id:require('node:crypto').randomUUID(),fingerprint,model:conf.model,family:'qwen3',versions,viewport,
      scenarios,limits:LIMITS,conditions:{},runs:[],started_at:new Date().toISOString()
    };
    if (report.fingerprint!==fingerprint) throw Error('batch_configuration_changed');
    const save=()=>{
      report.generated_at=new Date().toISOString();
      for (const row of report.runs) projectUsage(row);
      report.completed=report.runs.length===100 && report.runs.every(row=>row.reason!=='in_flight');
      const known=report.runs.flatMap(r=>r.steps.flatMap(s=>s.calls));
      report.usage={calls:known.length,reported_tokens:known.reduce((s,c)=>s+(c.tokens||0),0),
        unreported_calls:known.filter(c=>c.tokens===null).length};
      atomic(reportFile,JSON.stringify(report,null,2)+'\n');
    };
    for (const row of report.runs) if (row.reason==='in_flight') {row.reason='interrupted';row.status='blocked';}
    save();
    let count=0;
    for (const slot of schedule(arm)) {
      if (report.runs.some(r=>r.arm===slot.arm && r.scenario_id===slot.scenario_id && r.round===slot.round)) continue;
      const scenario=scenarios.find(s=>s.id===slot.scenario_id);
      const row={...slot,status:'blocked',reason:'in_flight',assertion:scenario.assertion,actions:0,model_calls:0,tokens:0,
        started_at:new Date().toISOString(),elapsed_ms:0,steps:[],screenshots:[]};
      report.runs.push(row); save();
      await runOne(browser,conf,report,row,dir,save);
      process.stdout.write(JSON.stringify({...slot,status:row.status,reason:row.reason,model_calls:row.model_calls})+'\n');
      count++;
      if (count>=cap || ['authentication','unsafe_or_unavailable','page_unavailable','frozen_context_changed','page_assets_unavailable'].includes(row.reason) ||
          /^provider_http_(401|403|404|429)$/.test(row.reason)) break;
    }
  } finally {
    if (browser) await browser.close().catch(()=>{});
    fs.unlinkSync(lock);
  }
}
if(require.main===module) main().catch(error=>{
  const code=String(error?.message||'').split('\n')[0];
  console.error(/^[a-z0-9_]+$/.test(code)?code:error.code==='EEXIST'?'batch_locked':'runner_error');process.exitCode=1;
});
module.exports={schedule,projectUsage,failure};
