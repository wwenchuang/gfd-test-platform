'use strict';

const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {chromium}=require('playwright');
const {catalogTarget}=require('../../task_server/fixtures/week5_web/policy');
const {openPage,digest,origin}=require('./guard');
const {createAgent,modelEndpoint,LIMITS}=require('./model');
const {projectUsage,failure}=require('./run');

const viewport={width:1440,height:1000};
const scenario={id:'ui_catalog_search_restore',title:'真实目录的名称搜索与清除',campaign:'supplemental',
  assertion:'填写完整用例名并回车后，页面 ID 顺序与服务端一致，目标版本存在；清空后恢复初始清单。'};
const files=['search-run.js','run.js','model.js','guard.js','package-lock.json',
  '../../task_server/fixtures/week5_web/workflows.js','../../task_server/fixtures/week5_web/policy.js','../../task_server/fixtures/week5_web/proxy.js'];
const protocol=digest(Buffer.concat(files.map(f=>fs.readFileSync(path.resolve(__dirname,f)))));
const catalogPath='/api/ui-executions/catalog';
const query=(app,name='')=>'?'+new URLSearchParams({app_id:app,module:'',query:name,scope:'published',limit:'20'});
function atomic(file,bytes){fs.writeFileSync(file+'.tmp',bytes,{mode:0o600});fs.renameSync(file+'.tmp',file);}
async function capture(page,dir,run,label) {
  if (page.url()!==origin+'/task-manager.html') throw Error('unexpected_page');
  const pane=page.locator('[data-ui-mode="library"]:visible');
  const box=await pane.boundingBox();
  if (!box) throw Error('evidence_unavailable');
  const x=Math.max(0,Math.ceil(box.x)), y=Math.max(0,Math.ceil(box.y));
  const width=Math.floor(Math.min(viewport.width,box.x+box.width))-x;
  const height=Math.floor(Math.min(viewport.height,box.y+box.height))-y;
  if (width<1 || height<1) throw Error('evidence_unavailable');
  const bytes=await page.screenshot({clip:{x,y,width,height},timeout:5000});
  const sha256=digest(bytes), file=sha256+'.png', dest=path.join(dir,file);
  if (!fs.existsSync(dest)) atomic(dest,bytes);
  run.screenshots.push({file,sha256,label});
}
async function trial(browser,conf,report,run,dir,save) {
  const started=Date.now();
  const ctx=await browser.newContext({serviceWorkers:'block',viewport,deviceScaleFactor:1,locale:'zh-CN',timezoneId:'Asia/Shanghai'});
  let agent, prepared, timer, timedOut=false;
  const work=async()=>{
    prepared=await openPage(ctx,conf,scenario.id);
    const {page,state,catalogQueries}=prepared;
    page.setDefaultTimeout(7000);
    const target=catalogTarget(state.catalog);
    if (!target) throw Error('no_published_case');
    const search=page.locator('[name="ui-query"]');
    async function after(q,action) {
      catalogQueries.add(q);
      const [response]=await Promise.all([page.waitForResponse(r=>r.url()===origin+catalogPath+q,{timeout:8000}),action()]);
      if (response.status()!==200) throw Error('catalog_response');
      await page.waitForFunction(v=>{
        const host=document.querySelector('[data-ui-cases]');
        return host?.dataset.uiCatalogQuery===v && !host.hasAttribute('aria-busy');
      },q.slice(1),{timeout:6000});
      const data=state.catalogs.get(q);
      if (!data?.ok || !Array.isArray(data.items) || !Number.isSafeInteger(data.total)) throw Error('catalog_response');
      const actual=await page.locator('[data-ui-cases] [data-case-id]').evaluateAll(ns=>ns.map(n=>n.dataset.caseId));
      if (JSON.stringify(actual)!==JSON.stringify(data.items.map(i=>i.case_id)) ||
          await page.locator('[data-ui-case-count]').innerText()!==data.total+' 条匹配基线') throw Error('workflow_assertion');
      return data;
    }
    const initial=query(target.app), filtered=query(target.app,target.name);
    const initialData=await after(initial,()=>page.locator('[name="ui-app"]').selectOption(target.app));
    const versions=d=>d.items.map(i=>[i.case_id,i.revision]);
    const original=JSON.stringify(versions(initialData));
    const assets=[...state.hashes].filter(([name])=>name==='/task-manager.html'||/\/ui-(simple-entry|test-home)(\.|$)/.test(name)).sort();
    if (assets.length<3 || !initialData.items.some(i=>i.case_id===target.caseId && i.revision===target.revision)) throw Error('case_version_changed');
    const signature=digest(JSON.stringify({target,original,assets,total:initialData.total}));
    if (report.condition_sha256 && signature!==report.condition_sha256) throw Error('frozen_context_changed');
    report.condition_sha256=signature;run.condition_sha256=signature;
    await search.scrollIntoViewIfNeeded();
    await capture(page,dir,run,'填写前的用例目录');
    const step={index:1,action:'focus_search',result:'pending',calls:[]};run.steps.push(step);
    if (run.arm==='ai') {
      agent=createAgent(page,conf,run,report,save);
      let center;
      try {center=await agent.locate(step,'用例库内用于搜索已发布基线名称的文字输入框，定位到输入框内部。');}
      catch (error) {if (step.calls.at(-1)?.status==='received') throw Error('model_no_location');throw error;}
      const [x,y]=center;
      const matches=await search.evaluate((el,point)=>document.elementFromPoint(...point)===el,[x,y]);
      step.target_matched=matches;
      if (!matches) throw Error('model_mislocated');
      if (timedOut) throw Error('run_timeout');
      await page.mouse.click(x,y);
    } else await search.click();
    if (!await search.evaluate(el=>el===document.activeElement)) throw Error('workflow_assertion');
    step.result='focused';run.actions++;save();
    const action=(name)=>{const row={index:run.steps.length+1,action:name,calls:[],result:'pending'};run.steps.push(row);return row;};
    const fill=action('fill');
    catalogQueries.add(filtered); // typing is debounced; both the automatic and explicit queries are safe.
    await page.keyboard.insertText(target.name);
    if (await search.inputValue()!==target.name) throw Error('workflow_assertion');
    fill.result='filled';run.actions++;
    await capture(page,dir,run,'已填写真实基线名称');
    const submit=action('query');
    const data=await after(filtered,()=>page.keyboard.press('Enter'));
    if (data.total<1 || !data.items.some(i=>i.app_id===target.app && i.case_id===target.caseId && i.revision===target.revision)) throw Error('workflow_assertion');
    submit.result='verified';run.actions++;
    await capture(page,dir,run,'回车查询后，清单和目标版本已核对');
    const restore=action('clear');
    const reset=await after(initial,async()=>{await page.keyboard.press('ControlOrMeta+a');await page.keyboard.press('Backspace');});
    if (await search.inputValue()!=='' || original!==JSON.stringify(versions(reset)) || reset.total!==initialData.total) throw Error('workflow_assertion');
    restore.result='verified';run.actions++;
    await capture(page,dir,run,'清空后恢复原清单');
    if (state.routeError || state.writes) throw Error('network_guard');
    if (run.arm==='ai' && run.model_calls!==1) throw Error('missing_interaction');
    run.status='passed';run.reason='';
  };
  try {await Promise.race([work(),new Promise((_,reject)=>{timer=setTimeout(()=>{timedOut=true;reject(Error('run_timeout'));},LIMITS.run_timeout_ms);})]);}
  catch (error) {
    Object.assign(run,failure(error,run));
    if (prepared && !prepared.state.routeError && !prepared.state.writes) await capture(prepared.page,dir,run,'中止时的页面').catch(()=>{});
  } finally {
    clearTimeout(timer);timedOut=true;await ctx.close().catch(()=>{});if (agent) await agent.close().catch(()=>{});
    const pending=run.steps.find(s=>s.result==='pending');
    if (pending) pending.result=run.reason||'interrupted';
    run.elapsed_ms=Date.now()-started;save();
  }
}
async function main() {
  process.umask(0o077);
  const [out,countRaw='20']=process.argv.slice(2), cap=Number(countRaw);
  if (!out || !path.isAbsolute(out) || !Number.isInteger(cap) || cap<1 || cap>20) throw Error('usage');
  const conf=JSON.parse(fs.readFileSync(0,'utf8'));
  if (!/^[A-Za-z0-9_-]{43}$/.test(conf.token||'') || typeof conf.userId!=='string'||!conf.userId||!conf.model_key||
      !/^[a-zA-Z0-9_.-]{1,64}$/.test(conf.model||'')) throw Error('credentials_input');
  const endpoint=modelEndpoint(conf.base_url);
  fs.mkdirSync(out,{recursive:true,mode:0o700});
  const dir=path.join(out,'visual-artifacts');fs.mkdirSync(dir,{recursive:true,mode:0o700});
  const lock=path.join(out,'.run-lock'), file=path.join(out,'w9-report.json');
  fs.writeFileSync(lock,String(process.pid),{flag:'wx',mode:0o600});
  let browser;
  try {
    browser=await chromium.launch({headless:true,args:['--disable-background-networking','--disable-dev-shm-usage']});
    const versions={midscene:'1.10.7',playwright:require('playwright/package.json').version,browser:browser.version(),node:process.version};
    const fingerprint=digest(JSON.stringify({protocol,versions,endpoint,viewport,model:conf.model,owner:digest(conf.userId)}));
    const report=fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')):{
      version:1,batch_id:crypto.randomUUID(),campaign:'supplemental',model:conf.model,family:'qwen3',versions,viewport,fingerprint,
      limits:{...LIMITS,model_calls_per_run:1,model_calls_total:10},scenarios:[scenario],runs:[],started_at:new Date().toISOString()};
    if (report.fingerprint!==fingerprint) throw Error('batch_configuration_changed');
    function save() {
      report.generated_at=new Date().toISOString();
      for(const row of report.runs) projectUsage(row);
      report.completed=report.runs.length===20 && report.runs.every(r=>r.reason!=='in_flight');
      const calls=report.runs.flatMap(r=>r.steps.flatMap(s=>s.calls));
      report.usage={calls:calls.length,reported_tokens:calls.reduce((n,c)=>n+(c.tokens||0),0),unreported_calls:calls.filter(c=>c.tokens===null).length};
      atomic(file,JSON.stringify(report,null,2)+'\n');
    }
    for(const row of report.runs) if (row.reason==='in_flight') row.reason='interrupted';
    save();
    let added=0;
    for (let round=1;round<=10;round++) for(const arm of round%2?['control','ai']:['ai','control']) {
      if (report.runs.some(r=>r.round===round && r.arm===arm)) continue;
      const run={scenario_id:scenario.id,arm,round,campaign:'supplemental',status:'blocked',reason:'in_flight',elapsed_ms:0,
        assertion:scenario.assertion,actions:0,model_calls:0,tokens:0,screenshots:[],steps:[],started_at:new Date().toISOString()};
      report.runs.push(run);save();await trial(browser,conf,report,run,dir,save);
      console.log(JSON.stringify({round,arm,status:run.status,reason:run.reason}));
      if (++added>=cap || ['authentication','page_unavailable','unsafe_or_unavailable','frozen_context_changed','case_version_changed'].includes(run.reason) ||
          /^provider_http_(401|403|404|429)$/.test(run.reason)) return;
    }
  } finally {if (browser) await browser.close().catch(()=>{});fs.unlinkSync(lock);}
}
main().catch(error=>{const message=String(error?.message||'');console.error(/^[a-z0-9_]+$/.test(message)?message:'search_runner_error');process.exitCode=1;});
