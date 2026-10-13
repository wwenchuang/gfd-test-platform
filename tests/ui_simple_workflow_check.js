const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('../api-testing-ui/node_modules/jsdom');
const wait=()=>new Promise(setImmediate);
const CASE={case_id:'c1',revision:'a'.repeat(64),task_name:'导航用例',app_id:'app.a',module:'导航',execution_eligible:true};
const DEVICE={target:{runner_id:'mac',device_id:'iphone',platform:'ios'},name:'iPhone 13',connection_status:'ready',app_version:'1.0',snapshot_url:''};
function fixture(t,override){
 const dom=new JSDOM('<main></main>',{url:'http://localhost',runScripts:'dangerously',pretendToBeVisual:true});t.after(()=>dom.window.close());
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
 const mount=(mode='start',extra={})=>w.UiSimpleFlow.mount(w.document.querySelector('main'),{mode,request,identity:()=>identity,canExecute:()=>true,...extra});
 return {w,node:w.document.querySelector('main'),calls,mount,setIdentity:v=>identity=v,setDevices:v=>devices=v};
}
async function select(t,f){f.mount();await wait();const app=f.node.querySelector('[name="ui-app"]');app.value='app.a';app.dispatchEvent(new f.w.Event('change'));await wait();f.node.querySelector('[data-case-id]').click();await wait();f.node.querySelector('[data-device-key]').click();await wait();}
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
test('changing app does not leave device cards detached from the page',async t=>{
 const f=fixture(t);await select(t,f);const app=f.node.querySelector('[name="ui-app"]');
 app.value='';app.dispatchEvent(new f.w.Event('change'));await wait();app.value='app.a';app.dispatchEvent(new f.w.Event('change'));await wait();
 assert.equal(f.node.querySelectorAll('[data-device-key]').length,1);
});
test('report list explicitly requests own scope',async t=>{
 const f=fixture(t);f.mount('reports');await wait();assert.ok(f.calls.some(c=>c.path.startsWith('/ui-executions?')&&c.path.includes('scope=own')));
});
test('report polling preserves the focused report row',async t=>{
 const f=fixture(t,path=>path.startsWith('/ui-executions?')?{ok:true,items:[{ui_batch_id:'uib_1',app_id:'app.a',status:'running',created_ns:1000000}]}:undefined);
 f.mount('reports');await wait();const button=f.node.querySelector('.ui-simple-report-row');button.focus();await f.w.UiSimpleFlow.refresh();
 assert.equal(f.node.querySelector('.ui-simple-report-row'),button);assert.equal(f.w.document.activeElement,button);
});
test('selected cases stay named and removable when a search hides their rows',async t=>{
 let hidden=false;const f=fixture(t,path=>hidden&&path.startsWith('/ui-executions/catalog')?{ok:true,apps:[{app_id:'app.a',name:'应用甲'}],modules:[],items:[],groups:[],total:0}:undefined);
 await select(t,f);hidden=true;f.node.querySelector('[data-ui-refresh]').click();await wait();
 const chip=f.node.querySelector('[data-ui-selected] button');assert.ok(chip);assert.match(chip.textContent,/导航用例/);chip.click();
 assert.match(f.node.querySelector('[data-ui-selection-count]').textContent,/已选 0/);assert.equal(f.node.querySelector('[data-ui-start]').disabled,true);
});
