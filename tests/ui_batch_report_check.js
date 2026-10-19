const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('../api-testing-ui/node_modules/jsdom');
const tick=()=>new Promise(setImmediate);
function setup(t,request,extra={}){
 const dom=new JSDOM('<main></main>',{url:'http://localhost',runScripts:'dangerously',pretendToBeVisual:true});t.after(()=>dom.window.close());const w=dom.window;
 w.eval(fs.readFileSync('js/ui-batch-report.js','utf8'));const calls=[];let identity='a';
 const node=w.document.querySelector('main');
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
 const view=w.UiBatchReport.mount(node,{batchId:'uib_example',identity:()=>identity,request:(path,o)=>path.split('?')[0].endsWith('/deliveries')?Promise.resolve({ok:true,policy:{enabled:false},items:[]}):request(path,o),download:async(path)=>{calls.push(path);return '本人测试报告.docx';},copy:async text=>calls.push(text),...extra});
 return {w,node,view,calls,setIdentity:value=>identity=value};
}
const SNAP={presentation:{title:'应用甲 发布测试报告',owner:'执行者乙',time_range:'2026年10月13日 10:00 至 10:02',conclusion:'本次未通过，失败 1 项。',conclusion_status:'failed',case_count:1,executed_cases:1,passed_count:0,failed_count:1,incomplete_count:0,coverage:[{case_name:'首页导航',scenario:'个人中心与首页切换',expected:'首页正常显示',result:'未通过',attempt_no:1}],environments:['苹果 13，应用版本 1.19.7'],concerns:[]},app_name:'应用甲',owner_display_name:'执行者乙',status:'failed',summary:'本次通过 1，失败 1。',counts:{passed:1,failed:1},attempts:[],artifacts:[]};
test('ordinary test opens evidence metadata once without report generation or image reads',async t=>{
 const requests=[];let images=0;const f=setup(t,async(path,o={})=>{requests.push({path,...o});return path.endsWith('/evidence')?{ok:true,artifacts:[{artifact_id:'a'.repeat(64),kind:'png'}]}:{ok:true,report:{status:'not_requested',snapshot:null}};},{loadEvidenceImage:async()=>{images++;}});await tick();
 assert.match(f.node.textContent,/普通测试，未申请发版报告/);assert.equal(f.node.querySelector('[data-report-generate]'),null);
 assert.equal(f.node.querySelectorAll('[data-evidence-preview]').length,1);assert.equal(images,0);
 f.w.Date.now=()=>Date.now()+20000;await f.view.refresh();await f.view.refresh();
 assert.equal(requests.filter(r=>r.path.endsWith('/evidence')).length,1);assert.equal(requests.filter(r=>r.method==='POST').length,0);
 assert.equal(f.node.querySelector('[data-report-download="docx"]').hidden,true);
});
test('ordinary screenshots reuse private preview and do not reload during parent refresh',async t=>{
 let imageReads=0,metadataReads=0;const f=setup(t,async path=>{
  if(path.endsWith('/evidence')){metadataReads++;return {ok:true,artifacts:[{artifact_id:'a'.repeat(64),kind:'jpg'}]};}
  return {ok:true,report:{status:'not_requested',snapshot:null}};
 },{loadEvidenceImage:async()=>{imageReads++;return new Blob(['jpeg'],{type:'image/jpeg'});}});
 f.w.URL.createObjectURL=()=> 'blob:ordinary';f.w.URL.revokeObjectURL=()=>{};await tick();f.node.querySelector('[data-evidence-preview]').click();await tick();
 const dialog=f.node.querySelector('dialog');assert.equal(dialog.open,true);assert.equal(imageReads,1);
 f.w.Date.now=()=>Date.now()+20000;await f.view.refresh();await f.view.refresh();assert.equal(f.node.querySelector('dialog'),dialog);assert.equal(metadataReads,1);assert.equal(imageReads,1);
 dialog.querySelector('[data-evidence-close]').click();f.node.querySelector('[data-report-evidence-refresh]').click();await tick();assert.equal(metadataReads,2);assert.equal(imageReads,1);
});
test('ordinary evidence metadata failure is retryable and revoked access removes private artifacts',async t=>{
 let failure=true,denied=false;const f=setup(t,async path=>{
  if(path.endsWith('/evidence')){if(denied)throw Object.assign(new Error('无权访问证据'),{status:403});if(failure)throw new Error('证据暂时不可用');return {ok:true,artifacts:[{artifact_id:'a'.repeat(64),kind:'png'}]};}
  return {ok:true,report:{status:'not_requested',snapshot:null}};
 });await tick();assert.match(f.node.textContent,/证据暂时不可用/);failure=false;f.node.querySelector('[data-report-evidence-refresh]').click();await tick();assert.equal(f.node.querySelectorAll('[data-evidence-preview]').length,1);
 denied=true;await f.view.refresh(true);assert.equal(f.node.querySelectorAll('[data-evidence-preview]').length,0);assert.match(f.node.textContent,/无法访问/);
});
test('ordinary evidence refresh retains expanded section and focused retained preview',async t=>{
 let artifacts=[{artifact_id:'a'.repeat(64),kind:'png'}];const f=setup(t,async path=>path.endsWith('/evidence')?{ok:true,artifacts}:{ok:true,report:{status:'not_requested',snapshot:null}});await tick();
 f.node.querySelector('details').open=true;f.node.querySelector('[data-evidence-preview]').focus();artifacts=[...artifacts,{artifact_id:'b'.repeat(64),kind:'png'}];await f.view.refresh(true);
 assert.equal(f.node.querySelector('details').open,true);assert.equal(f.w.document.activeElement,f.node.querySelector('[data-evidence-preview]'));
});
test('report renders frozen executor and real failure, escapes labels and downloads pinned revision',async t=>{
 const f=setup(t,async()=>({ok:true,report:{status:'ready',report_revision:'rev-1',snapshot:{...SNAP,app_name:'<img src=x onerror=alert(1)>'}}}));await tick();
 assert.match(f.node.textContent,/执行者乙/);assert.match(f.node.textContent,/失败 1/);assert.equal(f.node.querySelector('img'),null);
 f.node.querySelector('[data-report-download="docx"]').click();await tick();assert.equal(f.calls[0],'/ui-executions/uib_example/report/download?format=docx&revision=rev-1');
});
test('report retry only queues report generation and copy uses exact snapshot summary',async t=>{
 const calls=[];let ready=false;const f=setup(t,async(path,o={})=>{calls.push([path,o.method]);if(o.method==='POST')ready=true;return {ok:true,report:{status:ready?'ready':'failed',report_revision:'1',snapshot:ready?SNAP:null,error:'生成中断'}};});await tick();
 f.node.querySelector('[data-report-retry]').click();await tick();assert.ok(calls.some(([p,m])=>p.endsWith('/report')&&m==='POST'));assert.equal(calls.some(([p])=>p.endsWith('/rerun')),false);
 f.node.querySelector('[data-report-copy]').click();await tick();assert.equal(f.calls[0],SNAP.summary);
});
test('late report from previous account is discarded',async t=>{
 let resolve;const f=setup(t,()=>new Promise(r=>resolve=r));f.setIdentity('b');resolve({ok:true,report:{status:'ready',snapshot:SNAP}});await tick();assert.doesNotMatch(f.node.textContent,/执行者乙/);
});
test('ready report refresh is rate bounded and unchanged revision preserves DOM and focus',async t=>{
 let count=0;const f=setup(t,async()=>{count++;return {ok:true,report:{status:'ready',report_revision:'1',snapshot:SNAP}};});await tick();
 const button=f.node.querySelector('[data-report-copy]');button.focus();await f.view.refresh();assert.equal(count,1);assert.equal(f.w.document.activeElement,button);
 const table=f.node.querySelector('table');f.w.Date.now=()=>Date.now()+16000;await f.view.refresh();
 assert.equal(count,2);assert.equal(f.node.querySelector('table'),table);assert.equal(f.w.document.activeElement,button);
});

test('release report shows business coverage but no action transcript or internal identifiers',async t=>{
 const f=setup(t,async()=>({ok:true,report:{status:'ready',report_revision:'1',snapshot:{...SNAP,attempts:[{case_id:'private-case-id',steps:[{label:'aiTap · 我的'}]}]}}}));await tick();
 assert.match(f.node.textContent,/发布测试报告/);assert.match(f.node.textContent,/测试时间/);assert.match(f.node.textContent,/个人中心与首页切换/);
 assert.doesNotMatch(f.node.textContent,/aiTap|private-case-id|具体步骤/);assert.equal(f.node.querySelectorAll('tbody tr').length,1);
});

test('report puts tested versions beside execution metadata and preserves unavailable state',async t=>{
 let denied=false;
 const f=setup(t,async()=>{if(denied)throw Object.assign(new Error('当前无权访问'),{status:403});
  return {ok:true,report:{status:'ready',report_revision:'1',snapshot:{...SNAP,presentation:{...SNAP.presentation,
   test_versions:['苹果端 1.19.7（构建号 70）','安卓端 2.0.0（构建号 51）']}}}};
 });await tick();
 assert.match(f.node.querySelector('[data-report-owner]').textContent,/测试版本：苹果端 1.19.7.*安卓端 2.0.0/);
 denied=true;await f.view.refresh(true);
 assert.match(f.node.querySelector('[data-report-state]').textContent,/无法访问/);
 assert.doesNotMatch(f.node.textContent,/1.19.7|个人中心与首页切换/);
 assert.equal(f.node.querySelector('[data-report-download="docx"]').hidden,true);
});

test('cross-platform same case labels bind each result to its Chinese target',async t=>{
 const coverage=['苹果系统','安卓系统'].map((platform_label,i)=>({...SNAP.presentation.coverage[0],
  platform:i?'android':'ios',platform_label,target_label:platform_label,result:i?'通过':'未通过'}));
 const f=setup(t,async()=>({ok:true,report:{status:'ready',report_revision:'1',snapshot:{...SNAP,presentation:{...SNAP.presentation,coverage}}}}));await tick();
 const rows=[...f.node.querySelectorAll('tbody tr')];assert.equal(rows.length,2);
 assert.match(rows[0].textContent,/苹果系统.*未通过/);assert.match(rows[1].textContent,/安卓系统.*通过/);
 assert.equal(rows[0].children.length,3);
});

test('multiple phone rows keep compact target labels and bind full frozen versions in overview',async t=>{
 const coverage=[1,2].map(i=>({...SNAP.presentation.coverage[0],target_label:`苹果系统，设备 ${i}`,result:i===1?'通过':'未通过'}));
 const environments=['设备 1：苹果 13，苹果系统 26.6；应用版本 1.19.7（构建号 70）','设备 2：苹果 15，苹果系统 26.6；应用版本 1.20.0（构建号 71）'];
 const f=setup(t,async()=>({ok:true,report:{status:'ready',report_revision:'1',snapshot:{...SNAP,
  presentation:{...SNAP.presentation,coverage,environments,concerns:['首页导航（苹果系统，设备 2）：未通过。请复测。']},
  attempts:[{device_id:'private-device',runner_id:'private-runner'}]}}}));await tick();
 const rows=[...f.node.querySelectorAll('tbody tr')];assert.match(rows[0].textContent,/苹果系统，设备 1/);assert.match(rows[1].textContent,/苹果系统，设备 2/);
 for(const row of rows)assert.doesNotMatch(row.textContent,/应用版本|构建号|1\.19\.7|1\.20\.0|苹果 13|苹果 15/);
 for(const environment of environments)assert.ok(f.node.textContent.includes(environment));
 assert.doesNotMatch(f.node.textContent,/private-device|private-runner/);assert.equal(rows[0].children.length,3);
});

test('ready refresh discovers delayed evidence and downloads new revision without an interval',async t=>{
 let report={status:'ready',report_revision:'rev-1',snapshot:SNAP},count=0,now=100000;
 const f=setup(t,async()=>{count++;return {ok:true,report};});f.w.Date.now=()=>now;await tick();
 const button=f.node.querySelector('[data-report-copy]');button.focus();
 report={status:'pending',report_revision:'rev-1',snapshot:null};now+=16000;await f.view.refresh();assert.equal(count,2);
 report={status:'ready',report_revision:'rev-2',snapshot:{...SNAP,artifacts:[{artifact_id:'a'.repeat(64),kind:'png'}]}};
 now+=5000;await f.view.refresh();assert.equal(count,3);assert.match(f.node.textContent,/执行证据（1 份）/);
 assert.equal(f.w.document.activeElement,button);
 f.node.querySelector('[data-report-download="docx"]').click();await tick();assert.match(f.calls[0],/revision=rev-2$/);
 assert.doesNotMatch(fs.readFileSync('js/ui-batch-report.js','utf8'),/setInterval/);
});

test('hidden or disposed report never requests background refresh',async t=>{
 let count=0;const f=setup(t,async()=>{count++;return {ok:true,report:{status:'ready',report_revision:'1',snapshot:SNAP}};});await tick();
 f.w.Date.now=()=>Date.now()+16000;Object.defineProperty(f.w.document,'hidden',{value:true,configurable:true});await f.view.refresh(true);assert.equal(count,1);
 Object.defineProperty(f.w.document,'hidden',{value:false,configurable:true});f.node.hidden=true;await f.view.refresh(true);assert.equal(count,1);
 f.node.hidden=false;f.view.dispose();await f.view.refresh(true);assert.equal(count,1);
});

for(const status of [401,403,404])test(`HTTP ${status} clears private title outcome and content`,async t=>{
 let denied=false;const f=setup(t,async()=>{if(denied)throw Object.assign(new Error('无法访问'),{status});return {ok:true,report:{status:'ready',report_revision:'1',snapshot:SNAP}};});await tick();
 denied=true;await f.view.refresh(true);
 assert.equal(f.node.querySelector('[data-report-title]').textContent,'本次报告');
 assert.equal(f.node.querySelector('[data-report-summary]').dataset.outcome,undefined);
 assert.doesNotMatch(f.node.textContent,/应用甲|执行者乙|个人中心/);
});

test('new revision preserves focus on retained evidence and its expanded section',async t=>{
 const original={artifact_id:'a'.repeat(64),kind:'png'};let revision='1',artifacts=[original];
 const f=setup(t,async()=>({ok:true,report:{status:'ready',report_revision:revision,snapshot:{...SNAP,artifacts}}}));await tick();
 const details=f.node.querySelector('details');details.open=true;details.querySelector('button').focus();
 revision='2';artifacts=[original,{artifact_id:'b'.repeat(64),kind:'png'}];await f.view.refresh(true);
 assert.equal(f.node.querySelector('details').open,true);
 assert.equal(f.w.document.activeElement,f.node.querySelector('details button'));
});

test('pending update restores a hidden control focus without taking it from another control',async t=>{
 let report={status:'ready',report_revision:'1',snapshot:SNAP};const f=setup(t,async()=>({ok:true,report}));await tick();
 const copy=f.node.querySelector('[data-report-copy]');copy.focus();report={status:'pending',snapshot:null};await f.view.refresh(true);
 // Browsers blur a control when it is hidden; jsdom keeps it focused.
 copy.blur();report={status:'ready',report_revision:'2',snapshot:SNAP};await f.view.refresh(true);
 assert.equal(f.w.document.activeElement,copy);
 report={status:'pending',snapshot:null};await f.view.refresh(true);copy.blur();const close=f.node.querySelector('[data-report-close]');close.focus();
 report={status:'ready',report_revision:'3',snapshot:SNAP};await f.view.refresh(true);assert.equal(f.w.document.activeElement,close);
});

test('evidence summary keyboard focus survives revision and pending updates without stealing focus',async t=>{
 const artifacts=[{artifact_id:'a'.repeat(64),kind:'png'}];let report={status:'ready',report_revision:'1',snapshot:{...SNAP,artifacts}};
 const f=setup(t,async()=>({ok:true,report}));await tick();
 f.node.querySelector('details').open=true;f.node.querySelector('details summary').focus();
 report={...report,report_revision:'2'};await f.view.refresh(true);
 assert.equal(f.w.document.activeElement,f.node.querySelector('details summary'));assert.equal(f.node.querySelector('details').open,true);
 const snapshot=report.snapshot;report={status:'pending',snapshot:null};await f.view.refresh(true);
 report={status:'ready',report_revision:'3',snapshot};await f.view.refresh(true);
 assert.equal(f.w.document.activeElement,f.node.querySelector('details summary'));
 report={status:'pending',snapshot:null};await f.view.refresh(true);const copy=f.node.querySelector('[data-report-close]');copy.focus();
 report={status:'ready',report_revision:'4',snapshot};await f.view.refresh(true);assert.equal(f.w.document.activeElement,copy);
});

test('notification-pinned report cannot drift to current head and exports cited revision',async t=>{
 const paths=[],dom=new JSDOM('<main></main>',{url:'http://localhost',runScripts:'dangerously',pretendToBeVisual:true});t.after(()=>dom.window.close());const w=dom.window;w.eval(fs.readFileSync('js/ui-batch-report.js','utf8'));
 const revision='a'.repeat(64),node=w.document.querySelector('main');const view=w.UiBatchReport.mount(node,{batchId:'uib_link',revision,identity:()=> 'a',request:async(path)=>{paths.push(path);return {ok:true,report:{status:'ready',report_revision:revision,snapshot:SNAP}};},download:async p=>paths.push(p),copy:()=>{},canNotify:()=>false});await tick();
 assert.ok(paths.includes('/ui-executions/uib_link/report?revision='+revision));node.querySelector('[data-report-download="docx"]').click();await tick();assert.ok(paths.includes('/ui-executions/uib_link/report/download?format=docx&revision='+revision));view.dispose();
});
test('notification retry is permission gated and unknown requires explicit receipt confirmation',async t=>{
 const dom=new JSDOM('<main></main>',{url:'http://localhost',runScripts:'dangerously',pretendToBeVisual:true});t.after(()=>dom.window.close());const w=dom.window;w.eval(fs.readFileSync('js/ui-batch-report.js','utf8'));const node=w.document.querySelector('main'),calls=[];let permission=false,confirmed=false;
 w.confirm=()=>confirmed;
 const view=w.UiBatchReport.mount(node,{batchId:'uib_link',identity:()=> 'a',canNotify:()=>permission,request:async(path,options={})=>{calls.push({path,options});return path.split('?')[0].endsWith('/deliveries')?{ok:true,policy:{enabled:true,target_label:'专属群'},items:[{delivery_key:'d1',report_revision:'1',status:'unknown',message:'送达未知，请核对'}]}:{ok:true,report:{status:'ready',report_revision:'1',snapshot:SNAP}};},download:()=>{},copy:()=>{}});await tick();await tick();
 assert.equal(node.querySelector('[data-delivery-retry]'),null);permission=true;await view.refresh(true);await tick();
 node.querySelector('[data-delivery-retry]').click();await tick();assert.equal(calls.filter(c=>c.options.method==='POST').length,0);
 permission=false;confirmed=true;node.querySelector('[data-delivery-retry]').click();await tick();assert.equal(calls.filter(c=>c.options.method==='POST').length,0);
 await view.refresh(true);await tick();assert.equal(node.querySelector('[data-delivery-retry]'),null);
 permission=true;await view.refresh(true);await tick();node.querySelector('[data-delivery-retry]').click();await tick();assert.equal(calls.filter(c=>c.options.method==='POST')[0].options.body.confirmed_not_received,true);view.dispose();
});

test('notification view refuses a mismatched server revision',async t=>{
 const dom=new JSDOM('<main></main>',{url:'http://localhost',runScripts:'dangerously',pretendToBeVisual:true});t.after(()=>dom.window.close());const w=dom.window;w.eval(fs.readFileSync('js/ui-batch-report.js','utf8'));const node=w.document.querySelector('main');
 const view=w.UiBatchReport.mount(node,{batchId:'uib_link',revision:'a'.repeat(64),identity:()=> 'a',request:async()=>({ok:true,report:{status:'ready',report_revision:'b'.repeat(64),snapshot:SNAP}}),download:()=>{},copy:()=>{}});await tick();
 assert.doesNotMatch(node.textContent,/执行者乙/);assert.match(node.textContent,/版本/);view.dispose();
});

test('old pinned report shows and resends only its own revision notification',async t=>{
 const dom=new JSDOM('<main></main>',{url:'http://localhost',runScripts:'dangerously',pretendToBeVisual:true});t.after(()=>dom.window.close());const w=dom.window;w.eval(fs.readFileSync('js/ui-batch-report.js','utf8'));const node=w.document.querySelector('main'),calls=[],old='a'.repeat(64),latest='b'.repeat(64);
 const view=w.UiBatchReport.mount(node,{batchId:'uib_link',revision:old,identity:()=> 'a',canNotify:()=>true,request:async(path,options={})=>{calls.push({path,options});return path.includes('/deliveries')?{ok:true,policy:{enabled:true,target_label:'专属群'},items:[{delivery_key:'old',report_revision:old,status:'failed',message:'历史报告发送失败'},{delivery_key:'latest',report_revision:latest,status:'sent',message:'新版已发送'}]}:{ok:true,report:{status:'ready',report_revision:old,snapshot:SNAP}};},download:()=>{},copy:()=>{}});await tick();await tick();
 assert.ok(calls.some(c=>c.path.endsWith('/deliveries?revision='+old)));assert.doesNotMatch(node.textContent,/新版已发送/);assert.equal(node.querySelectorAll('[data-delivery-retry]').length,1);
 node.querySelector('[data-delivery-retry]').click();await tick();assert.ok(calls.some(c=>c.path.endsWith('/deliveries/old/retry')));assert.equal(calls.some(c=>c.path.endsWith('/deliveries/latest/retry')),false);view.dispose();
});

test('successful refresh clears its prior error while preserving action feedback',async t=>{
 let failed=true;const f=setup(t,async()=>{if(failed)throw new Error('本轮临时读取失败');return {ok:true,report:{status:'ready',report_revision:'1',snapshot:SNAP}};});await tick();
 assert.equal(f.node.querySelector('[data-report-message]').textContent,'本轮临时读取失败');failed=false;await f.view.refresh(true);
 assert.equal(f.node.querySelector('[data-report-message]').textContent,'');
 f.node.querySelector('[data-report-copy]').click();await tick();assert.equal(f.node.querySelector('[data-report-message]').textContent,'测试摘要已复制。');
 await f.view.refresh(true);assert.equal(f.node.querySelector('[data-report-message]').textContent,'测试摘要已复制。');
});

test('evidence separates images from HTML, loads only clicked preview and releases on close',async t=>{
 const artifacts=[{artifact_id:'a'.repeat(64),kind:'jpg'},{artifact_id:'b'.repeat(64),kind:'html'}],loads=[],revoked=[];
 const f=setup(t,async()=>({ok:true,report:{status:'ready',report_revision:'1',snapshot:{...SNAP,artifacts}}}),{loadEvidenceImage:async(...args)=>{loads.push(args);return new Blob(['jpeg'],{type:'image/jpeg'});}});
 f.w.URL.createObjectURL=()=> 'blob:private-image';f.w.URL.revokeObjectURL=url=>revoked.push(url);await tick();
 assert.equal(loads.length,0);assert.match(f.node.textContent,/报告附件/);assert.match(f.node.textContent,/网页文件/);
 assert.equal(f.node.querySelectorAll('[data-evidence-preview]').length,1);
 const trigger=f.node.querySelector('[data-evidence-preview]');trigger.click();await tick();
 assert.equal(loads.length,1);assert.equal(loads[0][0],'/ui-executions/uib_example/artifacts/'+'a'.repeat(64));assert.equal(loads[0][1],'jpg');
 const dialog=f.node.querySelector('dialog'),img=dialog.querySelector('img');assert.equal(dialog.open,true);assert.equal(img.src,'blob:private-image');
 Object.defineProperty(img,'naturalWidth',{value:1170});Object.defineProperty(img,'naturalHeight',{value:2532});img.dispatchEvent(new f.w.Event('load'));
 assert.equal(img.hidden,false);assert.match(dialog.textContent,/1170 × 2532/);
 dialog.querySelector('[data-evidence-zoom]').click();assert.equal(dialog.querySelector('[data-evidence-viewport]').classList.contains('is-zoomed'),true);
 await f.view.refresh(true);assert.equal(loads.length,1);assert.equal(f.node.querySelector('dialog'),dialog);
 dialog.querySelector('[data-evidence-close]').click();assert.equal(f.node.querySelector('dialog'),null);assert.deepEqual(revoked,['blob:private-image']);assert.equal(loads[0][2].signal.aborted,true);
});

for(const leave of ['close','dispose','account','revision'])test(`late private image cannot appear after ${leave}`,async t=>{
 let resolve,signal,version='1',created=0;
 const f=setup(t,async()=>({ok:true,report:{status:'ready',report_revision:version,snapshot:{...SNAP,artifacts:[{artifact_id:'a'.repeat(64),kind:'png'}]}}}),{loadEvidenceImage:(path,kind,options)=>{signal=options.signal;return new Promise(r=>resolve=r);}});
 f.w.URL.createObjectURL=()=>{created++;return 'blob:private';};f.w.URL.revokeObjectURL=()=>{};await tick();f.node.querySelector('[data-evidence-preview]').click();
 if(leave==='close')f.node.querySelector('[data-evidence-close]').click();
 if(leave==='dispose')f.view.dispose();
 if(leave==='account')f.setIdentity('b');
 if(leave==='revision'){version='2';await f.view.refresh(true);}
 resolve(new Blob(['png'],{type:'image/png'}));await tick();assert.equal(created,0);assert.equal(f.node.querySelector('img[src]'),null);
 if(leave!=='account')assert.equal(signal.aborted,true);
});

test('failed image preview offers retry and never treats an HTML attachment as an image',async t=>{
 let failed=true;const f=setup(t,async()=>({ok:true,report:{status:'ready',report_revision:'1',snapshot:{...SNAP,artifacts:[{artifact_id:'a'.repeat(64),kind:'png'}]}}}),{loadEvidenceImage:async()=>{if(failed)throw new Error('图片暂时无法读取');return new Blob(['bad png']);}});
 const revoked=[];f.w.URL.createObjectURL=()=> 'blob:invalid';f.w.URL.revokeObjectURL=url=>revoked.push(url);await tick();f.node.querySelector('[data-evidence-preview]').click();await tick();
 assert.match(f.node.querySelector('dialog').textContent,/图片暂时无法读取/);failed=false;f.node.querySelector('[data-evidence-retry]').click();await tick();
 const img=f.node.querySelector('dialog img');img.dispatchEvent(new f.w.Event('error'));assert.equal(img.hidden,true);assert.match(f.node.querySelector('dialog').textContent,/无法解码/);assert.deepEqual(revoked,['blob:invalid']);
});

test('preview download feedback remains inside the active dialog for success and failure',async t=>{
 let failed=true;const f=setup(t,async()=>({ok:true,report:{status:'ready',report_revision:'1',snapshot:{...SNAP,artifacts:[{artifact_id:'a'.repeat(64),kind:'png'}]}}}),{loadEvidenceImage:async()=>new Blob(['png']),download:async()=>{if(failed)throw new Error('当前账号无权下载');return '执行截图-123.png';}});
 f.w.URL.createObjectURL=()=> 'blob:image';f.w.URL.revokeObjectURL=()=>{};await tick();f.node.querySelector('[data-evidence-preview]').click();await tick();
 const dialog=f.node.querySelector('dialog'),button=[...dialog.querySelectorAll('button')].find(b=>b.textContent==='下载图片');button.click();await tick();
 assert.equal(dialog.querySelector('[data-evidence-message]').textContent,'当前账号无权下载');assert.equal(button.disabled,false);
 failed=false;button.click();await tick();assert.match(dialog.querySelector('[data-evidence-message]').textContent,/已发起下载：执行截图-123.png/);assert.equal(f.node.querySelector('[data-report-message]').textContent,'');
});

test('preview button keyboard focus survives a new report revision for the same image',async t=>{
 let revision='1';const f=setup(t,async()=>({ok:true,report:{status:'ready',report_revision:revision,snapshot:{...SNAP,artifacts:[{artifact_id:'a'.repeat(64),kind:'jpg'}]}}}));await tick();
 f.node.querySelector('details').open=true;f.node.querySelector('[data-evidence-preview]').focus();revision='2';await f.view.refresh(true);
 assert.equal(f.w.document.activeElement,f.node.querySelector('[data-evidence-preview]'));
});
test('revoked report access closes replay and invalidates late replay loads',async t=>{
 let denied=false,opened=null,closed=0;
 const f=setup(t,async path=>{if(denied)throw Object.assign(new Error('无权访问'),{status:403});return {ok:true,report:{status:'ready',snapshot:SNAP}};});
 f.w.UiExecutionReplay={open:options=>{opened=options;},close:()=>{closed++;}};
 await tick();f.node.querySelector('[data-report-replay]').click();assert.equal(opened.isCurrent(),true);
 await f.view.refresh(true);assert.equal(closed,0);
 denied=true;await f.view.refresh(true);assert.equal(closed,1);assert.equal(opened.isCurrent(),false);assert.equal(f.node.querySelector('[data-report-replay]').hidden,true);
});
