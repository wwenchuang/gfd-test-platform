const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('../api-testing-ui/node_modules/jsdom');
const wait=()=>new Promise(setImmediate);
const CASE={case_id:'c1',revision:'a'.repeat(64),task_name:'导航用例',app_id:'app.a',module:'导航',execution_eligible:true};
const DEVICE={target:{runner_id:'mac',device_id:'iphone',platform:'ios'},name:'iPhone 13',connection_status:'ready',app_version:'1.0',snapshot_url:''};
const catalogCalls=f=>f.calls.filter(c=>c.path.startsWith('/ui-executions/catalog'));
const catalogParams=f=>new URL(catalogCalls(f).at(-1).path,'http://test').searchParams;
function searchClock(f){
 const timers=new Map(),set=f.w.setTimeout.bind(f.w),clear=f.w.clearTimeout.bind(f.w);let id=-1;
 f.w.setTimeout=(fn,ms,...args)=>ms===300?(timers.set(--id,()=>fn(...args)),id):set(fn,ms,...args);
 f.w.clearTimeout=id=>{timers.delete(id);clear(id);};
 return ()=>{const tasks=[...timers.values()];timers.clear();for(const task of tasks)task();};
}
function fixture(t,override){
 const dom=new JSDOM('<main></main>',{url:'http://platform.test',runScripts:'dangerously',pretendToBeVisual:true});t.after(()=>dom.window.close());
 const w=dom.window, calls=[]; let identity='user-a', devices=[DEVICE];
 const request=async(path,options={})=>{calls.push({path,options});if(override){const r=override(path,options);if(r!==undefined)return r;}
 if(path.startsWith('/ui-executions/catalog'))return {ok:true,apps:[{app_id:'app.a',name:'应用甲'}],modules:['导航'],items:[CASE],groups:[],total:1,next_cursor:null};
 if(path.startsWith('/ui-executions/devices'))return {ok:true,devices};
 if(path==='/ui-executions/preflight')return {ok:true,ready:true,blockers:[]};
 if(path==='/ui-executions'&&options.method==='POST')return {ok:true,batch:{ui_batch_id:'uib_new',status:'queued',case_attempts:[]}};
 if(path.startsWith('/ui-executions/uib_'))return {ok:true,batch:{ui_batch_id:'uib_new',status:'passed',execution_settled:true,report_status:'pending',case_attempts:[]}};
 return {ok:true,items:[],next_cursor:null};};
 w.UiBatchView={update:(node,b)=>{node.textContent=b.ui_batch_id;}};
 w.eval(fs.readFileSync('js/ui-test-home.js','utf8'));
 const mount=(mode='start',extra={})=>w.UiSimpleFlow.mount(w.document.querySelector('main'),{mode,request,identity:()=>identity,canExecute:()=>true,canNotify:()=>true,...extra});
 return {w,node:w.document.querySelector('main'),calls,mount,setIdentity:v=>identity=v,setDevices:v=>devices=v};
}
async function select(t,f,extra={}){f.mount('start',extra);await wait();const app=f.node.querySelector('[name="ui-app"]');app.value='app.a';app.dispatchEvent(new f.w.Event('change'));await wait();f.node.querySelector('[data-case-id]').click();await wait();f.node.querySelector('[data-device-key]').click();await wait();}
for(const mode of ['start','library'])test(`${mode} searches published baselines by default`,async t=>{
 const f=fixture(t);f.mount(mode);await wait();assert.equal(catalogParams(f).get('scope'),'published');
 const toggle=f.node.querySelector('[data-ui-include-drafts]');
 if(mode==='start')assert.equal(toggle,null);
 else{assert.equal(toggle.checked,false);toggle.click();await wait();assert.equal(catalogParams(f).get('scope'),'all');toggle.click();await wait();assert.equal(catalogParams(f).get('scope'),'published');}
});
for(const action of ['enter','button'])test(`${action} searches immediately once and never starts execution`,async t=>{
 const f=fixture(t),tick=searchClock(f);await select(t,f);
 const query=f.node.querySelector('[name="ui-query"]'),before=catalogCalls(f).length;
 query.value='基线';query.dispatchEvent(new f.w.Event('input'));
 if(action==='enter'){const event=new f.w.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true});query.dispatchEvent(event);assert.equal(event.defaultPrevented,true);}
 else f.node.querySelector('[data-ui-search]').click();
 await wait();assert.equal(catalogCalls(f).length,before+1);assert.equal(catalogParams(f).get('query'),'基线');
 tick();await wait();assert.equal(catalogCalls(f).length,before+1);assert.equal(f.calls.some(c=>c.path==='/ui-executions/preflight'),false);assert.equal(creationPosts(f).length,0);
});
test('Chinese composition waits for completion and Enter does not search unfinished text',async t=>{
 const f=fixture(t),tick=searchClock(f);f.mount();await wait();const query=f.node.querySelector('[name="ui-query"]'),before=catalogCalls(f).length;
 query.dispatchEvent(new f.w.CompositionEvent('compositionstart'));query.value='ji';query.dispatchEvent(new f.w.InputEvent('input',{isComposing:true}));
 query.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'Enter',isComposing:true}));tick();await wait();assert.equal(catalogCalls(f).length,before);
 query.value='基线';query.dispatchEvent(new f.w.CompositionEvent('compositionend'));tick();await wait();assert.equal(catalogCalls(f).length,before+1);assert.equal(catalogParams(f).get('query'),'基线');
});
test('automatic search, clear and scope changes reset pagination without duplicate requests',async t=>{
 const f=fixture(t,path=>path.startsWith('/ui-executions/catalog')?{ok:true,apps:[{app_id:'app.a',name:'应用甲'}],modules:['导航'],items:[CASE],total:21,next_cursor:path.includes('cursor=')?null:'page-2'}:undefined),tick=searchClock(f);
 f.mount('library');await wait();const app=f.node.querySelector('[name="ui-app"]');app.value='app.a';app.dispatchEvent(new f.w.Event('change'));await wait();
 f.node.querySelector('[data-ui-next]').click();await wait();assert.equal(catalogParams(f).get('cursor'),'page-2');
 const query=f.node.querySelector('[name="ui-query"]');query.value='导航';query.dispatchEvent(new f.w.Event('input'));tick();await wait();
 assert.equal(catalogParams(f).has('cursor'),false);assert.equal(f.node.querySelector('[data-ui-prev]').disabled,true);
 f.node.querySelector('[data-ui-next]').click();await wait();query.value='';query.dispatchEvent(new f.w.Event('input'));await wait();assert.equal(catalogParams(f).get('query'),'');assert.equal(catalogParams(f).has('cursor'),false);
 query.value='基线';query.dispatchEvent(new f.w.Event('input'));f.node.querySelector('[data-ui-include-drafts]').click();await wait();const count=catalogCalls(f).length;tick();await wait();assert.equal(catalogCalls(f).length,count);assert.equal(catalogParams(f).get('scope'),'all');assert.equal(catalogParams(f).get('query'),'基线');
});
test('typing invalidates an older response before the debounce starts a new search',async t=>{
 let resolve;const f=fixture(t,path=>path.includes('query=old')?new Promise(r=>resolve=r):undefined),tick=searchClock(f);f.mount();await wait();
 const query=f.node.querySelector('[name="ui-query"]');query.value='old';query.dispatchEvent(new f.w.Event('input'));tick();await wait();const old=catalogCalls(f).at(-1);
 query.value='new';query.dispatchEvent(new f.w.Event('input'));assert.equal(old.options.signal.aborted,true);
 resolve({ok:true,apps:[],groups:[{name:'过期搜索结果',items:[CASE]}],total:1});await wait();assert.doesNotMatch(f.node.textContent,/过期搜索结果/);tick();await wait();assert.equal(catalogParams(f).get('query'),'new');
});
test('empty published results explain the baseline scope',async t=>{
 const f=fixture(t,path=>path.startsWith('/ui-executions/catalog')?{ok:true,apps:[{app_id:'app.a',name:'应用甲'}],modules:[],items:[],groups:[],total:0}:undefined);f.mount();await wait();
 const app=f.node.querySelector('[name="ui-app"]');app.value='app.a';app.dispatchEvent(new f.w.Event('change'));await wait();assert.match(f.node.querySelector('[data-ui-cases]').textContent,/没有匹配的已发布基线/);
});
test('hiding drafts clears draft-only module and saved unavailable modules recover to all modules',async t=>{
 const f=fixture(t,path=>path.startsWith('/ui-executions/catalog')?{ok:true,apps:[{app_id:'app.a',name:'应用甲'}],modules:path.includes('scope=all')?['导航','草稿模块']:['导航'],items:[CASE],total:1}:undefined);
 f.w.sessionStorage.setItem('ui-simple:user-a:selection',JSON.stringify({app:'app.a',module:'草稿模块'}));f.mount('library');await wait();await wait();
 assert.equal(catalogParams(f).get('module'),'');const module=f.node.querySelector('[name="ui-module"]');assert.equal(module.value,'');assert.equal(module.selectedIndex,0);
 f.node.querySelector('[data-ui-include-drafts]').click();await wait();module.value='草稿模块';module.dispatchEvent(new f.w.Event('change'));await wait();assert.equal(catalogParams(f).get('module'),'草稿模块');
 f.node.querySelector('[data-ui-include-drafts]').click();await wait();assert.equal(catalogParams(f).get('module'),'');assert.equal(module.value,'');assert.equal(module.selectedIndex,0);
 assert.equal(JSON.parse(f.w.sessionStorage.getItem('ui-simple:user-a:selection')).module,'');
});
for(const change of ['scope','query'])test(`${change} invalidates old pagination while new results are pending`,async t=>{
 let resolve,slow=false;const data={ok:true,apps:[{app_id:'app.a',name:'应用甲'}],modules:['导航'],items:[CASE],total:21,next_cursor:'old-cursor'};
 const f=fixture(t,path=>path.startsWith('/ui-executions/catalog')?(slow?new Promise(r=>resolve=r):data):undefined),tick=searchClock(f);f.mount('library');await wait();
 const app=f.node.querySelector('[name="ui-app"]');app.value='app.a';app.dispatchEvent(new f.w.Event('change'));await wait();slow=true;
 if(change==='scope')f.node.querySelector('[data-ui-include-drafts]').click();
 else{const q=f.node.querySelector('[name="ui-query"]');q.value='新基线';q.dispatchEvent(new f.w.Event('input'));}
 const next=f.node.querySelector('[data-ui-next]');assert.equal(next.disabled,true);assert.equal(f.node.querySelector('[data-ui-prev]').disabled,true);next.click();tick();await wait();
 assert.equal(catalogParams(f).has('cursor'),false);assert.equal(catalogCalls(f).at(-1).options.signal.aborted,false);
 resolve({...data,next_cursor:null});await wait();assert.equal(next.disabled,true);
});
test('explicit app case device produces one idempotent exact-target start',async t=>{
 const f=fixture(t);await select(t,f);const start=f.node.querySelector('[data-ui-start]');start.click();start.click();await wait();await wait();
 const posts=f.calls.filter(c=>c.path==='/ui-executions'&&c.options.method==='POST');assert.equal(posts.length,1);
 const b=posts[0].options.body;assert.equal(b.app_id,'app.a');assert.deepEqual(JSON.parse(JSON.stringify(b.targets)),[DEVICE.target]);
 assert.equal(b.cases[0].revision,CASE.revision);assert.ok(posts[0].options.headers['Idempotency-Key']);assert.equal(b.tester,undefined);
});
test('device refresh retains the explicit offline phone instead of selecting another',async t=>{
 const f=fixture(t);await select(t,f);const input=f.node.querySelector('[data-device-key]');input.focus();
 f.setDevices([{...DEVICE,connection_status:'offline'},{...DEVICE,target:{...DEVICE.target,device_id:'other'},name:'另一台'}]);
 await f.w.UiSimpleFlow.refresh();assert.equal(f.node.querySelector('[data-device-key]'),input);assert.equal(input.checked,true);assert.equal(input.disabled,true);
 assert.equal(f.node.querySelector('[data-ui-start]').disabled,true);assert.equal(f.w.document.activeElement,input);
});
test('moving the selected physical phone to another runner requires explicit reselection',async t=>{
 const f=fixture(t);await select(t,f);
 const old=f.node.querySelector('[data-device-key]');
 f.setDevices([{...DEVICE,target:{...DEVICE.target,runner_id:'mac-new'}}]);
 await f.w.UiSimpleFlow.refresh();
 const inputs=f.node.querySelectorAll('[data-device-key]');assert.equal(inputs.length,1);assert.equal(old.isConnected,false);
 assert.equal(inputs[0].checked,false);assert.equal(f.node.querySelector('[data-ui-start]').disabled,true);
 assert.match(f.node.querySelector('[data-ui-notice]').textContent,/连接已变化.*重新选择/);
 assert.equal(JSON.parse(f.w.sessionStorage.getItem('ui-simple:user-a:selection')).target,'');
 assert.equal(f.calls.some(c=>c.path==='/ui-executions/preflight'),false);
 inputs[0].click();await wait();f.node.querySelector('[data-ui-start]').click();await wait();await wait();
 const post=creationPosts(f)[0];assert.equal(post.options.body.targets[0].runner_id,'mac-new');
});
test('missing phone with a different serial replacement stays unavailable and retains the original selection',async t=>{
 const f=fixture(t);await select(t,f);const old=f.node.querySelector('[data-device-key]');
 f.setDevices([{...DEVICE,target:{...DEVICE.target,device_id:'different-serial'}}]);await f.w.UiSimpleFlow.refresh();
 assert.equal(old.isConnected,true);assert.equal(old.checked,true);assert.equal(old.disabled,true);
 assert.equal(f.node.querySelector('[data-ui-start]').disabled,true);assert.doesNotMatch(f.node.querySelector('[data-ui-notice]').textContent,/连接已变化/);
});
test('moving a phone cannot rewrite an uncertain submitted target or idempotency key',async t=>{
 let attempts=0;const f=fixture(t,(path,o)=>path==='/ui-executions'&&o.method==='POST'&&++attempts===1?Promise.reject(new Error('断线')):undefined);
 await select(t,f);f.node.querySelector('[data-ui-start]').click();await wait();await wait();
 const original=JSON.parse(f.w.sessionStorage.getItem('ui-simple:user-a:pending'));
 f.setDevices([{...DEVICE,target:{...DEVICE.target,runner_id:'new-host'}}]);await f.w.UiSimpleFlow.refresh();
 assert.deepEqual(JSON.parse(f.w.sessionStorage.getItem('ui-simple:user-a:pending')),original);
 f.node.querySelector('[data-ui-recover]').click();await wait();await wait();
 assert.equal(creationPosts(f)[1].options.body.targets[0].runner_id,'mac');
 assert.equal(creationPosts(f)[1].options.headers['Idempotency-Key'],creationPosts(f)[0].options.headers['Idempotency-Key']);
});
test('remembered target moved before page load is cleared without selecting its replacement',async t=>{
 const f=fixture(t);f.w.sessionStorage.setItem('ui-simple:user-a:selection',JSON.stringify({app:'app.a',target:JSON.stringify(['old-host','iphone','ios'])}));
 f.mount();await wait();await wait();
 assert.equal(f.node.querySelector('[data-device-key]').checked,false);
 assert.equal(JSON.parse(f.w.sessionStorage.getItem('ui-simple:user-a:selection')).target,'');
 assert.match(f.node.querySelector('[data-ui-notice]').textContent,/连接已变化.*重新选择/);
});
test('two online connections show runner identity and retain each exact target',async t=>{
 const f=fixture(t);f.setDevices([DEVICE,{...DEVICE,target:{...DEVICE.target,runner_id:'second-host'}}]);await select(t,f);
 const cards=f.node.querySelectorAll('.ui-simple-device');assert.equal(cards.length,2);
 assert.match(cards[0].textContent,/执行机器：mac/);assert.match(cards[1].textContent,/执行机器：second-host/);
 assert.match(cards[0].textContent,/多个在线连接/);
 cards[1].querySelector('input').click();await wait();f.node.querySelector('[data-ui-start]').click();await wait();await wait();
 assert.equal(creationPosts(f)[0].options.body.targets[0].runner_id,'second-host');
});
test('device refresh failure hides a previously visible image',async t=>{
 let fail=false;const f=fixture(t,path=>path.startsWith('/ui-executions/devices')&&fail?Promise.reject(new Error('权限已变化')):undefined);
 f.setDevices([{...DEVICE,app_id:'app.a',snapshot_url:'/api/runner/device-snapshot?runner_id=mac&device_id=iphone&app_id=app.a',snapshot_captured_at:'1'}]);
 await select(t,f);const img=f.node.querySelector('[data-ui-devices] img');img.src='blob:previous';img.hidden=false;
 img.dataset.pictureStamp='/api/runner/device-snapshot?runner_id=mac&device_id=iphone&app_id=app.a|1';
 fail=true;await f.w.UiSimpleFlow.refresh();assert.equal(img.hidden,true);assert.equal(img.hasAttribute('src'),false);
});
test('leaving or changing account drops a stale catalog response',async t=>{
 let resolve;const f=fixture(t,path=>path.startsWith('/ui-executions/catalog')?new Promise(r=>resolve=r):undefined);
 f.mount();f.setIdentity('user-b');f.w.UiSimpleFlow.reset();
 resolve({ok:true,apps:[],groups:[{name:'甲私密资料',items:[CASE]}],items:[]});await wait();assert.doesNotMatch(f.node.textContent,/甲私密资料|导航用例/);
});
test('mounting another page does not poll legacy jobs and hidden page does not refresh',async t=>{
 const f=fixture(t);await select(t,f);const count=f.calls.length;
 Object.defineProperty(f.w.document,'hidden',{value:true,configurable:true});await f.w.UiSimpleFlow.refresh();assert.equal(f.calls.length,count);
 assert.equal(f.calls.some(c=>c.path.startsWith('/jobs')||c.path.startsWith('/runners')),false);
});
test('preflight blocker stops creation and displays a reason',async t=>{
 const f=fixture(t,path=>path==='/ui-executions/preflight'?{ok:true,ready:false,blockers:[{code:'case_revision_changed',message:'用例已更新，请刷新后重选'}]}:undefined);
 await select(t,f);f.node.querySelector('[data-ui-start]').click();await wait();await wait();
 assert.equal(f.calls.filter(c=>c.path==='/ui-executions'&&c.options.method==='POST').length,0);assert.match(f.node.textContent,/用例已更新/);
});
test('uncertain start retry reuses the same idempotency key',async t=>{
 let attempts=0;const f=fixture(t,(path,o)=>{if(path==='/ui-executions'&&o.method==='POST'&&++attempts===1)return Promise.reject(new Error('网络断开'));});
 await select(t,f);f.node.querySelector('[data-ui-start]').click();await wait();await wait();f.node.querySelector('[data-ui-recover]').click();await wait();await wait();
 const posts=f.calls.filter(c=>c.path==='/ui-executions'&&c.options.method==='POST');assert.equal(posts.length,2);assert.equal(posts[0].options.headers['Idempotency-Key'],posts[1].options.headers['Idempotency-Key']);
});
test('uncertain submission survives reload and prevents selection edits',async t=>{
 let attempts=0;const f=fixture(t,(path,o)=>{if(path==='/ui-executions'&&o.method==='POST'&&++attempts===1)return Promise.reject(new Error('断线'));});
 await select(t,f);f.node.querySelector('[data-ui-start]').click();await wait();await wait();f.mount();await wait();
 assert.equal(f.node.querySelector('[data-ui-form]').disabled,true);assert.equal(f.node.querySelector('[data-ui-recover]').hidden,false);
 f.node.querySelector('[data-ui-recover]').click();await wait();await wait();
 const posts=f.calls.filter(c=>c.path==='/ui-executions'&&c.options.method==='POST');assert.equal(posts.length,2);assert.equal(posts[0].options.headers['Idempotency-Key'],posts[1].options.headers['Idempotency-Key']);
});
test('definite rejected POST clears pending intent through reload',async t=>{
 const f=fixture(t,(path,o)=>path==='/ui-executions'&&o.method==='POST'?Promise.reject(Object.assign(new Error('无权执行'),{status:403,code:'execution_forbidden'})):undefined);
 await select(t,f);f.node.querySelector('[data-ui-start]').click();await wait();await wait();
 assert.equal(f.node.querySelector('[data-ui-recover]').hidden,true);
 f.mount();await wait();assert.equal(f.node.querySelector('[data-ui-form]').disabled,false);
 assert.equal(f.node.querySelector('[data-ui-recover]').hidden,true);
});
test('HTTP 408 keeps uncertain submission for safe recovery',async t=>{
 const f=fixture(t,(path,o)=>path==='/ui-executions'&&o.method==='POST'?Promise.reject(Object.assign(new Error('超时'),{status:408,code:'request_timeout'})):undefined);
 await select(t,f);f.node.querySelector('[data-ui-start]').click();await wait();await wait();
 assert.equal(f.node.querySelector('[data-ui-recover]').hidden,false);
 f.mount();await wait();assert.equal(f.node.querySelector('[data-ui-form]').disabled,true);
});
test('changing app does not leave device cards detached from the page',async t=>{
 const f=fixture(t);await select(t,f);const app=f.node.querySelector('[name="ui-app"]');
 app.value='';app.dispatchEvent(new f.w.Event('change'));await wait();app.value='app.a';app.dispatchEvent(new f.w.Event('change'));await wait();
 assert.equal(f.node.querySelectorAll('[data-device-key]').length,1);
});
test('report list explicitly requests own scope',async t=>{
 const f=fixture(t);f.mount('reports');await wait();assert.ok(f.calls.some(c=>c.path.startsWith('/ui-executions?')&&c.path.includes('scope=own')));
});
test('report list displays frozen application names even when admission catalog is disabled',async t=>{
 const f=fixture(t,path=>path.startsWith('/ui-executions?')?{ok:true,items:[{ui_batch_id:'uib_1',app_id:'com.kfb.model',app_name:'智小白3D',status:'passed',report_status:'ready',created_ns:1000000}]}:undefined);
 f.mount('reports',{canAdmit:()=>false});await wait();
 assert.equal(f.node.querySelector('.ui-simple-report-row strong').textContent,'智小白3D');
 assert.equal(f.calls.some(c=>c.path.startsWith('/ui-executions/catalog')),false);
});
test('one report-row click opens a ready report without fetching progress',async t=>{
 const f=fixture(t,path=>path.startsWith('/ui-executions?')?{ok:true,items:[{ui_batch_id:'uib_1',app_id:'app.a',status:'passed',report_status:'ready',created_ns:1000000}]}:undefined);
 const opened=[];f.w.UiBatchReport={mount:(node,options)=>{opened.push(options.batchId);node.textContent='报告内容';return {dispose(){},refresh(){}};}};
 f.mount('reports',{download:async()=>{}});await wait();f.node.querySelector('.ui-simple-report-row').click();await wait();
 assert.deepEqual(opened,['uib_1']);assert.equal(f.calls.some(c=>c.path==='/ui-executions/uib_1'),false);
 assert.match(f.node.textContent,/报告内容/);
});
for(const targetStatus of ['not_requested','ready','pending'])test(`switching from ordinary evidence to ${targetStatus} batch removes prior artifacts`,async t=>{
 const rows=['a','b'].map((id,i)=>({ui_batch_id:'uib_'+id,app_id:'app.a',app_name:'测试'+id,status:'passed',execution_settled:true,report_status:i?targetStatus:'not_requested',delivery_status:'not_requested',case_attempts:[],created_ns:1000000}));
 const f=fixture(t,path=>{
  if(path.startsWith('/ui-executions?'))return {ok:true,items:rows};
  const row=rows.find(row=>path.startsWith('/ui-executions/'+row.ui_batch_id));if(!row)return;
  if(path.endsWith('/evidence'))return {ok:true,artifacts:[{artifact_id:(row===rows[0]?'a':'b').repeat(64),kind:'png'}]};
  if(path.endsWith('/report'))return {ok:true,report:{status:row.report_status,report_revision:row.report_status==='ready'?'b-revision':null,snapshot:row.report_status==='ready'?{summary:'测试b报告',artifacts:[{artifact_id:'b'.repeat(64),kind:'png'}]}:null}};
  return {ok:true,batch:row};
 });f.w.eval(fs.readFileSync('js/ui-batch-view.js','utf8'));f.w.eval(fs.readFileSync('js/ui-batch-report.js','utf8'));
 f.mount('reports',{download:async()=>{}});await wait();f.node.querySelectorAll('.ui-simple-report-row')[0].click();await wait();f.node.querySelector('[data-batch-action="report"]').click();await wait();
 assert.ok(f.node.querySelector('[data-evidence-preview="'+'a'.repeat(64)+'"]'));
 f.node.querySelectorAll('.ui-simple-report-row')[1].click();await wait();assert.equal(f.node.querySelector('[data-evidence-preview="'+'a'.repeat(64)+'"]'),null);
 if(targetStatus==='ready')assert.match(f.node.querySelector('[data-ui-report-preview]').textContent,/测试b报告/);
 else{assert.equal(f.node.querySelector('[data-ui-report-preview]').hidden,true);assert.match(f.node.querySelector('[data-ui-batch]').textContent,/测试b/);}
});
test('batch selection aborts an in-flight private image and discards its late bytes',async t=>{
 const rows=['a','b'].map(id=>({ui_batch_id:'uib_'+id,app_id:'app.a',status:'passed',execution_settled:true,report_status:'not_requested',delivery_status:'not_requested',case_attempts:[]}));let resolveImage,signal,created=0;
 const f=fixture(t,path=>path.startsWith('/ui-executions?')?{ok:true,items:rows}:path.endsWith('/evidence')?{ok:true,artifacts:[{artifact_id:'a'.repeat(64),kind:'png'}]}:path.endsWith('/report')?{ok:true,report:{status:'not_requested',snapshot:null}}:rows.some(row=>path==='/ui-executions/'+row.ui_batch_id)?{ok:true,batch:rows.find(row=>path.endsWith(row.ui_batch_id))}:undefined);
 f.w.eval(fs.readFileSync('js/ui-batch-view.js','utf8'));f.w.eval(fs.readFileSync('js/ui-batch-report.js','utf8'));
 f.w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};f.w.HTMLDialogElement.prototype.close=function(){this.open=false;};f.w.URL.createObjectURL=()=>{created++;return 'blob:old';};f.w.URL.revokeObjectURL=()=>{};
 f.mount('reports',{download:async()=>{},loadEvidenceImage:(path,kind,o)=>{signal=o.signal;return new Promise(r=>resolveImage=r);}});await wait();f.node.querySelectorAll('.ui-simple-report-row')[0].click();await wait();f.node.querySelector('[data-batch-action="report"]').click();await wait();f.node.querySelector('[data-evidence-preview]').click();
 f.node.querySelectorAll('.ui-simple-report-row')[1].click();assert.equal(signal.aborted,true);assert.equal(f.node.querySelector('dialog'),null);resolveImage(new Blob(['old']));await wait();assert.equal(created,0);assert.equal(f.node.querySelector('img[src="blob:old"]'),null);
});
test('selecting a linked test keeps its exact revision and choosing another test abandons the old link',async t=>{
 const rows=['a','b'].map(id=>({ui_batch_id:'uib_'+id,app_id:'app.a',status:'passed',report_status:id==='a'?'ready':'not_requested'})),opened=[],closed=[];
 const f=fixture(t,path=>path.startsWith('/ui-executions?')?{ok:true,items:rows}:undefined);
 f.w.UiBatchReport={mount:(node,o)=>{opened.push(o);return {refresh(){},dispose(){node.replaceChildren();}};}};
 const intent={batchId:'uib_a',revision:'f'.repeat(64)};f.mount('reports',{download:async()=>{},reportIntent:intent,onReportClose:value=>closed.push(value)});await wait();
 f.node.querySelectorAll('.ui-simple-report-row')[0].click();await wait();assert.equal(opened.at(-1).revision,intent.revision);assert.equal(closed.length,0);
 f.node.querySelectorAll('.ui-simple-report-row')[1].click();await wait();assert.deepEqual(JSON.parse(JSON.stringify(closed)),[intent]);
});
test('parent refresh discovers a newer ready report while settled progress stays idle',async t=>{
 let now=100000,revision='rev-1';
 const f=fixture(t,path=>{
  if(path.startsWith('/ui-executions?'))return {ok:true,items:[{ui_batch_id:'uib_1',app_id:'app.a',status:'passed',report_status:'ready',created_ns:1000000}]};
  if(path==='/ui-executions/uib_1/report')return {ok:true,report:{status:'ready',report_revision:revision,snapshot:{summary:revision,artifacts:revision==='rev-2'?[{artifact_id:'a'.repeat(64),kind:'png'}]:[]}}};
 });
 f.w.eval(fs.readFileSync('js/ui-batch-report.js','utf8'));f.w.Date.now=()=>now;
 f.mount('reports',{download:async()=>{}});await wait();f.node.querySelector('.ui-simple-report-row').click();await wait();
 await f.w.UiSimpleFlow.refresh();assert.equal(f.calls.filter(c=>c.path.endsWith('/report')).length,1);
 revision='rev-2';now+=16000;await f.w.UiSimpleFlow.refresh();
 assert.equal(f.calls.filter(c=>c.path.endsWith('/report')).length,2);assert.match(f.node.textContent,/执行证据（1 份）/);
 assert.equal(f.calls.some(c=>c.path==='/ui-executions/uib_1'),false);
 Object.defineProperty(f.w.document,'hidden',{value:true,configurable:true});now+=16000;await f.w.UiSimpleFlow.refresh();
 assert.equal(f.calls.filter(c=>c.path.endsWith('/report')).length,2);
 Object.defineProperty(f.w.document,'hidden',{value:false,configurable:true});f.node.querySelector('[data-ui-refresh]').click();await wait();
 assert.equal(f.calls.filter(c=>c.path.endsWith('/report')).length,3);
});
test('restricted phone preview explains its permission boundary',async t=>{
 const f=fixture(t);f.setDevices([{...DEVICE,snapshot_hint:'当前账号不允许查看整机画面'}]);await select(t,f);
 assert.match(f.node.querySelector('[data-picture-fallback]').textContent,/不允许查看整机画面/);
 assert.equal(f.node.querySelector('[data-ui-devices] img').hidden,true);
});
test('report polling preserves the focused report row',async t=>{
 const f=fixture(t,path=>path.startsWith('/ui-executions?')?{ok:true,items:[{ui_batch_id:'uib_1',app_id:'app.a',status:'running',created_ns:1000000}]}:undefined);
 f.mount('reports');await wait();const button=f.node.querySelector('.ui-simple-report-row');button.focus();await f.w.UiSimpleFlow.refresh();
 assert.equal(f.node.querySelector('.ui-simple-report-row'),button);assert.equal(f.w.document.activeElement,button);
});
test('new report order moves existing buttons while keeping focus',async t=>{
 let newest=false;const f=fixture(t,path=>path.startsWith('/ui-executions?')?{ok:true,items:newest?[
  {ui_batch_id:'uib_2',app_id:'app.a',status:'failed',report_status:'failed',created_ns:2000000},
  {ui_batch_id:'uib_1',app_id:'app.a',status:'passed',report_status:'ready',created_ns:1000000}]:[
  {ui_batch_id:'uib_1',app_id:'app.a',status:'passed',report_status:'ready',created_ns:1000000}]}:undefined);
 f.mount('reports');await wait();const old=f.node.querySelector('.ui-simple-report-row');old.focus();newest=true;await f.w.UiSimpleFlow.refresh();
 const rows=[...f.node.querySelectorAll('.ui-simple-report-row')];assert.equal(rows.length,2);assert.equal(rows[1],old);
 assert.equal(f.w.document.activeElement,old);assert.match(rows[0].textContent,/报告生成失败/);
});
test('selected cases stay named and removable when a search hides their rows',async t=>{
 let hidden=false;const f=fixture(t,path=>hidden&&path.startsWith('/ui-executions/catalog')?{ok:true,apps:[{app_id:'app.a',name:'应用甲'}],modules:[],items:[],groups:[],total:0}:undefined);
 await select(t,f);hidden=true;f.node.querySelector('[data-ui-refresh]').click();await wait();
 const chip=f.node.querySelector('[data-ui-selected] button');assert.ok(chip);assert.match(chip.textContent,/导航用例/);chip.click();
 assert.match(f.node.querySelector('[data-ui-selection-count]').textContent,/已选 0/);assert.equal(f.node.querySelector('[data-ui-start]').disabled,true);
});

test('ordinary execution has no notification toggle and never opts into report delivery',async t=>{
 const f=fixture(t,path=>path.startsWith('/ui-executions/catalog')?{ok:true,apps:[{app_id:'app.a',name:'应用甲',notification_channel:{ready:true,target_label:'专属群'}}],modules:[],items:[CASE],total:1}:undefined);
 await select(t,f);assert.equal(f.node.querySelector('[data-ui-notify-options]').hidden,true);assert.equal(f.node.querySelector('[data-ui-release]').checked,false);
 f.node.querySelector('[data-ui-start]').click();await wait();await wait();
 assert.equal(f.calls.find(c=>c.path==='/ui-executions'&&c.options.method==='POST').options.body.notifications.enabled,false);
 assert.equal(f.calls.find(c=>c.path==='/ui-executions'&&c.options.method==='POST').options.body.release_report.enabled,false);
});
for(const notify of [false,true])test(`release purpose opts into generation and notification only when separately selected (${notify})`,async t=>{
 const f=fixture(t,path=>path.startsWith('/ui-executions/catalog')?{ok:true,apps:[{app_id:'app.a',name:'应用甲',notification_channel:{ready:true,target_label:'专属群'}}],modules:[],items:[CASE],total:1}:undefined);
 await select(t,f);const release=f.node.querySelector('[data-ui-release]');release.click();await wait();
 assert.equal(f.node.querySelector('[data-ui-notify-options]').hidden,false);assert.match(f.node.textContent,/专属群/);assert.match(f.node.textContent,/结束后自动生成/);
 const toggle=f.node.querySelector('[data-ui-notify]');assert.equal(toggle.checked,false);if(notify)toggle.click();
 f.node.querySelector('[data-ui-start]').click();await wait();await wait();
 for(const req of f.calls.filter(c=>c.path==='/ui-executions/preflight'||c.path==='/ui-executions')){assert.equal(req.options.body.release_report.enabled,true);assert.equal(req.options.body.notifications.enabled,notify);}
});
test('turning off release purpose clears notification and selecting it again stays opted out',async t=>{
 const f=fixture(t,path=>path.startsWith('/ui-executions/catalog')?{ok:true,apps:[{app_id:'app.a',name:'应用甲',notification_channel:{ready:true,target_label:'专属群'}}],modules:[],items:[CASE],total:1}:undefined);await select(t,f);
 const release=f.node.querySelector('[data-ui-release]'),notify=f.node.querySelector('[data-ui-notify]');release.click();notify.click();assert.equal(notify.checked,true);release.click();
 assert.equal(notify.checked,false);assert.equal(f.node.querySelector('[data-ui-notify-options]').hidden,true);release.click();assert.equal(notify.checked,false);
 release.click();f.node.querySelector('[data-ui-start]').click();await wait();await wait();const body=creationPosts(f)[0].options.body;assert.equal(body.release_report.enabled,false);assert.equal(body.notifications.enabled,false);
});
test('release generation remains available without notification permission and rechecks a revoked permission',async t=>{
 let allowed=true;const f=fixture(t,path=>path.startsWith('/ui-executions/catalog')?{ok:true,apps:[{app_id:'app.a',name:'应用甲',notification_channel:{ready:true,target_label:'专属群'}}],modules:[],items:[CASE],total:1}:undefined);await select(t,f,{canNotify:()=>allowed});
 f.node.querySelector('[data-ui-release]').click();f.node.querySelector('[data-ui-notify]').click();allowed=false;
 f.node.querySelector('[data-ui-start]').click();await wait();await wait();const body=creationPosts(f)[0].options.body;assert.equal(body.release_report.enabled,true);assert.equal(body.notifications.enabled,false);
});
test('uncertain release submission restores its frozen purpose and notification for an idempotent retry',async t=>{
 let fail=true;const f=fixture(t,(path,o)=>{
  if(path.startsWith('/ui-executions/catalog'))return {ok:true,apps:[{app_id:'app.a',name:'应用甲',notification_channel:{ready:true,target_label:'专属群'}}],modules:[],items:[CASE],total:1};
  if(path==='/ui-executions'&&o.method==='POST'&&fail)throw new Error('网络暂时断开');
 });await select(t,f);f.node.querySelector('[data-ui-release]').click();f.node.querySelector('[data-ui-notify]').click();f.node.querySelector('[data-ui-start]').click();await wait();await wait();
 const original=creationPosts(f)[0];f.mount();await wait();assert.equal(f.node.querySelector('[data-ui-release]').checked,true);assert.equal(f.node.querySelector('[data-ui-notify]').checked,true);assert.equal(f.node.querySelector('[data-ui-form]').disabled,true);
 const release=f.node.querySelector('[data-ui-release]'),notify=f.node.querySelector('[data-ui-notify]');
 release.click();notify.click();assert.equal(release.checked,true);assert.equal(notify.checked,true);assert.equal(release.disabled,true);assert.equal(notify.disabled,true);
 fail=false;f.node.querySelector('[data-ui-recover]').click();await wait();await wait();const recovered=creationPosts(f)[1];assert.equal(recovered.options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);assert.deepEqual(JSON.parse(JSON.stringify(recovered.options.body)),JSON.parse(JSON.stringify(original.options.body)));
});
test('notification deep link opens its cited report revision on the reports page',async t=>{
 const f=fixture(t),calls=[];f.w.UiBatchReport={mount:(node,o)=>{calls.push(o);return {refresh:()=>{},dispose:()=>{}};}};
 f.mount('reports',{download:()=>{},canNotify:()=>false,reportIntent:{batchId:'uib_link',revision:'f'.repeat(64)}});await wait();
 assert.equal(calls[0].batchId,'uib_link');assert.equal(calls[0].revision,'f'.repeat(64));assert.equal(calls[0].canNotify(),false);
});
test('ordinary settled batch stops progress polling and opens evidence only on explicit action',async t=>{
 const b={ui_batch_id:'uib_new',status:'passed',execution_settled:true,report_status:'not_requested',delivery_status:'not_requested',case_attempts:[]};
 const f=fixture(t,path=>path==='/ui-executions/uib_new'?{ok:true,batch:b}:undefined),opened=[];let actions;
 f.w.UiBatchView={update:(node,b,handlers)=>{actions=handlers;node.textContent='实际步骤结果';}};
 f.w.UiBatchReport={mount:(node,o)=>{opened.push(o);return {refresh:()=>{},dispose:()=>{}};}};
 f.w.sessionStorage.setItem('ui-simple:user-a:lastBatch',JSON.stringify('uib_new'));f.mount('start',{download:()=>{}});await wait();
 const before=f.calls.filter(c=>c.path==='/ui-executions/uib_new').length;await f.w.UiSimpleFlow.refresh();await f.w.UiSimpleFlow.refresh();
 assert.equal(f.calls.filter(c=>c.path==='/ui-executions/uib_new').length,before);assert.equal(opened.length,0);assert.equal(f.calls.some(c=>c.path.endsWith('/evidence')),false);
 await actions.report('uib_new');assert.equal(opened.length,1);assert.equal(actions.generateReport,undefined);
 assert.equal(f.calls.some(c=>c.path.endsWith('/report')&&c.options.method==='POST'),false);
});

test('execution card keeps polling until final notification status arrives',async t=>{
 let delivery='pending';const f=fixture(t,path=>path==='/ui-executions/uib_new'?{ok:true,batch:{ui_batch_id:'uib_new',status:'passed',execution_settled:true,report_status:'ready',delivery_status:delivery,case_attempts:[]}}:undefined);
 await select(t,f);f.node.querySelector('[data-ui-start]').click();await wait();await wait();await f.w.UiSimpleFlow.refresh();
 const before=f.calls.filter(c=>c.path==='/ui-executions/uib_new').length;await f.w.UiSimpleFlow.refresh();assert.equal(f.calls.filter(c=>c.path==='/ui-executions/uib_new').length,before+1);
 delivery='sent';await f.w.UiSimpleFlow.refresh();const done=f.calls.filter(c=>c.path==='/ui-executions/uib_new').length;await f.w.UiSimpleFlow.refresh();assert.equal(f.calls.filter(c=>c.path==='/ui-executions/uib_new').length,done);
});
test('manual recovery uses exact payload, refreshes conflict, and suppresses account-switched responses',async t=>{
 let recoverAction,resolveRecovery,conflict=false;
 const interrupted={ui_batch_id:'uib_recover',status:'interrupted',execution_settled:false,report_status:'pending',case_attempts:[]};
 const f=fixture(t,(path,o)=>{
  if(path==='/ui-executions/uib_recover/confirm-stop')return conflict?Promise.reject(Object.assign(new Error('任务状态已变化'),{status:409})):new Promise(r=>resolveRecovery=r);
  if(path==='/ui-executions/uib_recover')return {ok:true,batch:interrupted};
 });
 f.w.sessionStorage.setItem('ui-simple:user-a:lastBatch',JSON.stringify('uib_recover'));
 f.w.UiBatchView={update:(node,b,actions)=>{node.textContent=b.owner_display_name||b.status;recoverAction=actions.recover;}};
 f.mount();await wait();assert.equal(typeof recoverAction,'function');
 const payload={attempt_id:'a1',expected_status:'interrupted',expected_event_sequence:3,expected_recovery_version:'f'.repeat(64),confirmed_stopped:true,reason:'已核对'};
 const pending=recoverAction('uib_recover',payload);await wait();
 const post=f.calls.find(c=>c.path.endsWith('/confirm-stop'));assert.deepEqual(post.options.body,payload);
 f.setIdentity('user-b');f.w.UiSimpleFlow.reset();resolveRecovery({ok:true,batch:{...interrupted,owner_display_name:'旧账号私密资料'}});await pending;
 assert.doesNotMatch(f.node.textContent,/旧账号私密资料/);
 f.setIdentity('user-a');f.mount();await wait();conflict=true;const before=f.calls.filter(c=>c.path==='/ui-executions/uib_recover').length;
 await assert.rejects(()=>recoverAction('uib_recover',payload),/任务状态已变化/);
 assert.equal(f.calls.filter(c=>c.path==='/ui-executions/uib_recover').length,before+1);
});


test('no notification permission still allows ordinary execution without notification controls',async t=>{
 const f=fixture(t,path=>path.startsWith('/ui-executions/catalog')?{ok:true,apps:[{app_id:'app.a',name:'应用甲',notification_channel:{ready:true,target_label:'专属群'}}],modules:[],items:[CASE],total:1}:undefined);
 f.mount('start',{canNotify:()=>false});await wait();const app=f.node.querySelector('[name="ui-app"]');app.value='app.a';app.dispatchEvent(new f.w.Event('change'));await wait();
 f.node.querySelector('[data-case-id]').click();f.node.querySelector('[data-device-key]').click();await wait();
 assert.equal(f.node.querySelector('[data-ui-notify-options]').hidden,true);assert.equal(f.node.querySelector('[data-ui-release]').checked,false);
 f.node.querySelector('[data-ui-start]').click();await wait();await wait();
 assert.equal(f.calls.find(c=>c.path==='/ui-executions'&&c.options.method==='POST').options.body.notifications.enabled,false);
});

test('rollback reports retain cancel and recovery while removing rerun',async t=>{
 const f=fixture(t,path=>path.startsWith('/ui-executions?')?{ok:true,items:[{ui_batch_id:'uib_recover',status:'interrupted',report_status:'pending'}]}:undefined);
 let actions;f.w.UiBatchView={update:(node,b,a)=>{actions=a;}};
 f.mount('reports',{canAdmit:()=>false});await wait();f.node.querySelector('.ui-simple-report-row').click();await wait();
 assert.equal(typeof actions.cancel,'function');assert.equal(typeof actions.recover,'function');assert.equal(actions.rerun,undefined);
 await actions.cancel('uib_recover');assert.equal(f.calls.some(c=>c.path.endsWith('/cancel')),true);
});

test('rollback reports skip gated catalog and use all-app own scope despite saved selection',async t=>{
 const f=fixture(t,path=>{
  if(path.startsWith('/ui-executions/catalog'))return Promise.reject(Object.assign(new Error('feature_disabled'),{status:404,code:'feature_disabled'}));
  if(path.startsWith('/ui-executions?'))return {ok:true,items:[{ui_batch_id:'uib_saved',app_id:'app.a',status:'failed',report_status:'ready'}]};
 });
 const saved=JSON.stringify({app:'app.a',module:'导航',target:'saved-phone'});f.w.sessionStorage.setItem('ui-simple:user-a:selection',saved);
 f.mount('reports',{canAdmit:()=>false});await wait();await f.w.UiSimpleFlow.refresh();f.node.querySelector('[data-ui-refresh]').click();await wait();
 assert.equal(f.calls.some(c=>c.path.startsWith('/ui-executions/catalog')),false);
 const lists=f.calls.filter(c=>c.path.startsWith('/ui-executions?'));assert.ok(lists.length>=2);
 for(const call of lists){const query=new URLSearchParams(call.path.split('?')[1]);assert.equal(query.get('scope'),'own');assert.equal(query.has('app_id'),false);}
 assert.equal(f.node.querySelector('[name="ui-app"]').value,'');assert.match(f.node.textContent,/失败/);
 assert.equal(f.node.querySelector('[data-ui-notice]').textContent,'');assert.doesNotMatch(f.node.textContent,/feature_disabled/);
 assert.equal(f.w.sessionStorage.getItem('ui-simple:user-a:selection'),saved);
});

test('rollback reports still surface a genuine list access error',async t=>{
 const f=fixture(t,path=>path.startsWith('/ui-executions?')?Promise.reject(Object.assign(new Error('当前账号无权读取报告'),{status:403,code:'permission_forbidden'})):undefined);
 f.mount('reports',{canAdmit:()=>false});await wait();
 assert.match(f.node.querySelector('[data-ui-notice]').textContent,/当前账号无权读取报告/);
 assert.equal(f.node.querySelector('[data-ui-notice]').getAttribute('role'),'alert');
});

// JSDOM exposes randomUUID even on HTTP; explicitly model the production capability boundary.
function useHttpCrypto(f){
 const crypto=f.w.crypto,fill=crypto.getRandomValues;let calls=0;
 Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true});
 crypto.getRandomValues=function(bytes){assert.equal(this,crypto);calls++;return fill.call(this,bytes);};
 f.w.Math.random=()=>{throw new Error('Insecure randomness must not be used');};
 return ()=>calls;
}
const uuidV4=/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const creationPosts=f=>f.calls.filter(c=>c.path==='/ui-executions'&&c.options.method==='POST');

test('HTTP start uses secure UUIDs, suppresses repeat clicks, and renews the key after success',async t=>{
 const f=fixture(t),entropyCalls=useHttpCrypto(f);await select(t,f);const start=f.node.querySelector('[data-ui-start]');
 await assert.doesNotReject(async()=>{const first=start.onclick();const second=start.onclick();await Promise.all([first,second]);});
 assert.equal(creationPosts(f).length,1);assert.equal(entropyCalls(),1);assert.equal(start.disabled,false);
 await start.onclick();const posts=creationPosts(f);assert.equal(posts.length,2);assert.equal(entropyCalls(),2);
 for(const post of posts)assert.match(post.options.headers['Idempotency-Key'],uuidV4);
 assert.notEqual(posts[0].options.headers['Idempotency-Key'],posts[1].options.headers['Idempotency-Key']);
 assert.deepEqual(JSON.parse(JSON.stringify(posts[0].options.body.targets)),[DEVICE.target]);
});

test('HTTP uncertain start recovers its frozen key and body after remount without fresh entropy',async t=>{
 let attempts=0;const f=fixture(t,(path,o)=>path==='/ui-executions'&&o.method==='POST'&&++attempts===1?Promise.reject(new Error('网络断开')):undefined);
 const entropyCalls=useHttpCrypto(f);await select(t,f);await assert.doesNotReject(()=>f.node.querySelector('[data-ui-start]').onclick());
 assert.equal(f.node.querySelector('[data-ui-recover]').hidden,false);assert.equal(entropyCalls(),1);
 const pending=JSON.parse(f.w.sessionStorage.getItem('ui-simple:user-a:pending'));assert.match(pending.key,uuidV4);
 Object.defineProperty(f.w,'crypto',{value:undefined,configurable:true});f.mount();await wait();
 assert.equal(f.node.querySelector('[data-ui-form]').disabled,true);
 await assert.doesNotReject(()=>f.node.querySelector('[data-ui-recover]').onclick());
 const posts=creationPosts(f);assert.equal(posts.length,2);assert.equal(posts[1].options.headers['Idempotency-Key'],pending.key);
 assert.deepEqual(JSON.parse(JSON.stringify(posts[1].options.body)),pending.body);
 assert.equal(f.node.querySelector('[data-ui-recover]').hidden,true);assert.equal(f.w.sessionStorage.getItem('ui-simple:user-a:pending'),'null');
});

for(const failure of ['missing','throws','absent'])test(`HTTP start catches ${failure} secure entropy and keeps the form retryable`,async t=>{
 const f=fixture(t);useHttpCrypto(f);
 if(failure==='absent')Object.defineProperty(f.w,'crypto',{value:undefined,configurable:true});
 else f.w.crypto.getRandomValues=failure==='missing'?undefined:()=>{throw new Error('entropy unavailable');};
 await select(t,f);const start=f.node.querySelector('[data-ui-start]');await assert.doesNotReject(()=>start.onclick());
 assert.equal(f.calls.some(c=>c.path==='/ui-executions/preflight'),false);assert.equal(creationPosts(f).length,0);
 const notice=f.node.querySelector('[data-ui-notice]');assert.equal(notice.getAttribute('role'),'alert');assert.match(notice.textContent,/无法生成.*请求标识/);
 assert.equal(start.disabled,false);assert.equal(f.node.querySelector('[data-ui-form]').disabled,false);assert.equal(f.node.querySelector('[data-ui-recover]').hidden,true);
 assert.equal(f.w.sessionStorage.getItem('ui-simple:user-a:pending'),'null');
});

function mountFailedBatch(f){
 f.w.eval(fs.readFileSync('js/ui-batch-view.js','utf8'));
 f.w.sessionStorage.setItem('ui-simple:user-a:lastBatch',JSON.stringify('uib_failed'));f.mount();
}
const failedBatch={ui_batch_id:'uib_failed',status:'failed',execution_settled:true,report_status:'ready',case_attempts:[{attempt_id:'a1',case_id:'c1',status:'failed'}]};

test('HTTP rerun suppresses repeat clicks and preserves its key across an uncertain response',async t=>{
 let rejectFirst,attempts=0;const f=fixture(t,path=>{
  if(path.endsWith('/rerun'))return ++attempts===1?new Promise((_,reject)=>rejectFirst=reject):{ok:true,batch:{...failedBatch,ui_batch_id:'uib_rerun'}};
  if(path==='/ui-executions/uib_failed')return {ok:true,batch:failedBatch};
 });
 const entropyCalls=useHttpCrypto(f);mountFailedBatch(f);await wait();const rerun=f.node.querySelector('[data-batch-action="rerun"]');
 const first=rerun.onclick();await rerun.onclick();assert.equal(attempts,1);assert.equal(rerun.disabled,true);
 rejectFirst(new Error('网络断开'));await first;assert.match(f.node.querySelector('[data-batch-action-error]').textContent,/网络断开/);assert.equal(rerun.disabled,false);
 await rerun.onclick();const posts=f.calls.filter(c=>c.path.endsWith('/rerun'));assert.equal(posts.length,2);assert.equal(entropyCalls(),1);
 assert.match(posts[0].options.headers['Idempotency-Key'],uuidV4);assert.equal(posts[0].options.headers['Idempotency-Key'],posts[1].options.headers['Idempotency-Key']);
 await f.node.querySelector('[data-batch-action="rerun"]').onclick();const latest=f.calls.filter(c=>c.path.endsWith('/rerun'));assert.equal(latest.length,3);assert.equal(entropyCalls(),2);
 assert.notEqual(latest[2].options.headers['Idempotency-Key'],posts[0].options.headers['Idempotency-Key']);
});

test('HTTP rerun shows secure entropy failure without a request and allows retry',async t=>{
 const f=fixture(t,path=>path==='/ui-executions/uib_failed'?{ok:true,batch:failedBatch}:undefined);useHttpCrypto(f);f.w.crypto.getRandomValues=undefined;
 mountFailedBatch(f);await wait();const rerun=f.node.querySelector('[data-batch-action="rerun"]');await assert.doesNotReject(()=>rerun.onclick());
 const error=f.node.querySelector('[data-batch-action-error]');assert.equal(error.hidden,false);assert.match(error.textContent,/无法生成.*请求标识/);
 assert.equal(f.calls.some(c=>c.path.endsWith('/rerun')),false);assert.equal(rerun.disabled,false);
});

const picturePosts=f=>f.calls.filter(c=>c.path==='/runners/refresh');
const pictureButton=f=>f.node.querySelector('[data-ui-pictures-refresh]');
const pictureNotice=f=>f.node.querySelector('[data-ui-picture-notice]');
function assertSelected(f){
 assert.equal(f.node.querySelector('[name="ui-app"]').value,'app.a');
 assert.equal(f.node.querySelector('[data-case-id]').checked,true);assert.equal(f.node.querySelector('[data-device-key]').checked,true);
 assert.match(f.node.querySelector('[data-ui-selection-count]').textContent,/已选 1/);
}
test('picture refresh is explicit and mounting, polling, or ordinary refresh never queues capture',async t=>{
 const f=fixture(t);await select(t,f,{canRefreshPictures:()=>true});assert.ok(pictureButton(f));assert.equal(pictureButton(f).hidden,false);
 await f.w.UiSimpleFlow.refresh();await f.w.UiSimpleFlow.refresh();f.node.querySelector('[data-ui-refresh]').click();await wait();await wait();
 assert.equal(picturePosts(f).length,0);assert.equal(f.calls.some(c=>c.path==='/runners'),false);assertSelected(f);
});
test('picture refresh coalesces clicks, reports queued count, and cools down without changing selections',async t=>{
 let resolve,now=1000;const f=fixture(t,path=>path==='/runners/refresh'?new Promise(r=>resolve=r):undefined);f.w.Date.now=()=>now;
 await select(t,f,{canRefreshPictures:()=>true});const button=pictureButton(f);assert.ok(button);const before=f.calls.filter(c=>c.path.startsWith('/ui-executions/devices')).length;
 const pending=button.onclick();await button.onclick();assert.equal(picturePosts(f).length,1);assert.equal(button.disabled,true);
 assert.equal(picturePosts(f)[0].options.method,'POST');assert.deepEqual(JSON.parse(JSON.stringify(picturePosts(f)[0].options.body)),{});
 resolve({ok:true,requested:2,requests:[{request_id:'a'},{request_id:'b'}]});await pending;
 assert.match(pictureNotice(f).textContent,/2.*排队.*等待/);assert.doesNotMatch(pictureNotice(f).textContent,/截图已更新|采集完成/);
 assert.equal(f.calls.filter(c=>c.path.startsWith('/ui-executions/devices')).length,before+1);assert.equal(f.calls.some(c=>c.path==='/runners'),false);assertSelected(f);
 await button.onclick();assert.equal(picturePosts(f).length,1);assert.equal(button.disabled,true);
 now+=10000;await f.w.UiSimpleFlow.refresh();assert.equal(button.disabled,false);
 const next=button.onclick();assert.equal(picturePosts(f).length,2);resolve({ok:true,requested:0,requests:[]});await next;
});
test('picture refresh is unavailable without permission and absent in reports',async t=>{
 const f=fixture(t);await select(t,f);assert.ok(pictureButton(f));assert.equal(pictureButton(f).hidden,true);assert.equal(pictureButton(f).disabled,true);
 await pictureButton(f).onclick();assert.equal(picturePosts(f).length,0);
 f.mount('reports',{canRefreshPictures:()=>true});await wait();assert.equal(pictureButton(f),null);assert.equal(picturePosts(f).length,0);
});
test('picture refresh rechecks revoked permissions and old identity before posting',async t=>{
 let allowed=true;const f=fixture(t);await select(t,f,{canRefreshPictures:()=>allowed});const button=pictureButton(f);assert.ok(button);
 allowed=false;await button.onclick();assert.equal(picturePosts(f).length,0);assert.equal(button.hidden,true);assert.equal(button.disabled,true);
 allowed=true;f.setIdentity('user-b');await button.onclick();assert.equal(picturePosts(f).length,0);
});
for(const response of ['success','error'])test(`picture refresh drops a stale ${response} after account replacement`,async t=>{
 let resolve,reject;const f=fixture(t,path=>path==='/runners/refresh'?new Promise((r,j)=>{resolve=r;reject=j;}):undefined);
 await select(t,f,{canRefreshPictures:()=>true});assert.ok(pictureButton(f));const pending=pictureButton(f).onclick();const signal=picturePosts(f)[0].options.signal;
 f.setIdentity('user-b');f.w.UiSimpleFlow.reset();f.mount('start',{canRefreshPictures:()=>true});await wait();assert.equal(signal.aborted,true);
 const before=f.calls.length;if(response==='success')resolve({ok:true,requested:7});else reject(new Error('旧账号请求失败'));
 await pending;assert.equal(pictureNotice(f).textContent,'');assert.equal(f.calls.length,before);assert.doesNotMatch(f.node.textContent,/7.*排队|旧账号请求失败/);
});
test('picture refresh hides pending feedback if permission is revoked before response',async t=>{
 let resolve,allowed=true;const f=fixture(t,path=>path==='/runners/refresh'?new Promise(r=>resolve=r):undefined);
 await select(t,f,{canRefreshPictures:()=>allowed});assert.ok(pictureButton(f));const pending=pictureButton(f).onclick(),before=f.calls.length;
 allowed=false;resolve({ok:true,requested:7});await pending;
 assert.equal(pictureButton(f).hidden,true);assert.equal(pictureNotice(f).hidden,true);assert.doesNotMatch(pictureNotice(f).textContent,/7/);assert.equal(f.calls.length,before);
});
test('picture refresh errors explain retry and preserve selected app cases and phone',async t=>{
 const f=fixture(t,path=>path==='/runners/refresh'?Promise.reject(new Error('执行器服务暂不可用')):undefined);
 await select(t,f,{canRefreshPictures:()=>true});assert.ok(pictureButton(f));await assert.doesNotReject(()=>pictureButton(f).onclick());
 assert.equal(pictureNotice(f).getAttribute('role'),'alert');assert.match(pictureNotice(f).textContent,/执行器服务暂不可用.*重试/);assertSelected(f);
 assert.equal(f.node.querySelector('[data-ui-start]').disabled,false);assert.equal(creationPosts(f).length,0);
});
test('picture refresh with zero requests does not claim fresh pictures or a captured result',async t=>{
 const f=fixture(t,path=>path==='/runners/refresh'?{ok:true,requested:0,requests:[]}:undefined);
 await select(t,f,{canRefreshPictures:()=>true});assert.ok(pictureButton(f));await pictureButton(f).onclick();
 assert.match(pictureNotice(f).textContent,/没有.*采集请求.*检查.*连接.*任务结束.*重试/);assert.doesNotMatch(pictureNotice(f).textContent,/已更新|已完成|已采集/);assertSelected(f);
});

test('start guidance follows missing selections and keeps execution gates',async t=>{
 const f=fixture(t);f.mount();await wait();
 const hint=()=>f.node.querySelector('[data-ui-start-hint]').textContent;
 assert.match(hint(),/选择.*应用/);
 const app=f.node.querySelector('[name="ui-app"]');app.value='app.a';app.dispatchEvent(new f.w.Event('change'));await wait();
 assert.match(hint(),/勾选/);f.node.querySelector('[data-case-id]').click();await wait();
 assert.match(hint(),/选择.*手机/);f.node.querySelector('[data-device-key]').click();await wait();
 assert.equal(f.node.querySelector('[data-ui-start]').disabled,false);assert.match(hint(),/已准备好/);
 assert.equal(f.calls.some(c=>c.path==='/ui-executions/preflight'),false);
});
test('view-only users see why starting is unavailable',async t=>{
 const f=fixture(t);await select(t,f,{canExecute:()=>false});
 assert.equal(f.node.querySelector('[data-ui-start]').disabled,true);
 assert.match(f.node.querySelector('[data-ui-start-hint]').textContent,/没有执行权限/);
});
