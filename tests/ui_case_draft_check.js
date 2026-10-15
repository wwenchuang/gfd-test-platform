const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('../api-testing-ui/node_modules/jsdom');
const wait=()=>new Promise(setImmediate);
const draft={draft_id:'d1',revision:1,app_id:'a',title:'导航',preconditions:[],steps:[{step_id:'s1',action:'aiTap',target:'我的',value:'',expected:'',review_state:'needs_review',source_ref:'text'}],questions:['请确认目标'],generation_status:'ready',yaml_content:'tasks: []',ready:false};
function fixture(t,override,permissions={}){const dom=new JSDOM('<main></main>',{url:'https://test',runScripts:'dangerously'});t.after(()=>dom.window.close());const w=dom.window,calls=[];let identity='a';w.UiBatchView={update:(n,b)=>n.textContent=b.status};const request=async(path,o={})=>{calls.push({path,o});const result=override?.(path,o);if(result!==undefined)return result;if(path.startsWith('/ui-executions/catalog'))return {ok:true,apps:[{app_id:'a',name:'应用'}],modules:['导航'],publication_modules:['导航']};if(path.startsWith('/ui-executions/devices'))return {ok:true,devices:[{name:'手机',connection_status:'ready',target:{runner_id:'r',device_id:'d',platform:'android'}}]};if(path==='/ui-case-drafts?app_id=a')return {ok:true,items:[draft]};if(path.endsWith('/validate'))return {ok:true,gate:{ready:true,blockers:[]}};if(path.endsWith('/update'))return {ok:true,draft:{...draft,...o.body,revision:2}};if(path.endsWith('/try'))return {ok:true,batch:{ui_batch_id:'b',status:'passed',execution_settled:true}};return {ok:true,draft};};w.eval(fs.readFileSync('js/ui-case-draft.js','utf8'));const node=w.document.querySelector('main');const editorOptions={request,identity:()=>identity,canEdit:()=>true,canExecute:()=>true,canPublish:()=>true,...permissions};const mounted=w.UiCaseDraft.mount(node,editorOptions);return {w,node,calls,mounted,remount:()=>w.UiCaseDraft.mount(node,editorOptions),setIdentity:v=>identity=v};}
async function open(f,appId='a'){await wait();const app=f.node.querySelector('[data-draft-app]');app.value=appId;app.dispatchEvent(new f.w.Event('change'));await wait();f.node.querySelector('[data-open-draft]').click();await wait();}
test('mount opens Chinese steps and dirty edits block execution',async t=>{const f=fixture(t);await open(f);assert.equal(f.node.querySelector('[data-draft-question]').value,'请确认目标');const title=f.node.querySelector('[data-draft-title]');title.value='修改';title.dispatchEvent(new f.w.Event('input',{bubbles:true}));assert.equal(f.node.querySelector('[data-draft-try]').disabled,true);f.node.querySelector('[data-draft-save]').click();await wait();assert.equal(f.calls.find(c=>c.path.endsWith('/update')).o.body.title,'修改');});
test('dispose aborts pending requests and ignores late responses',async t=>{let resolve;const f=fixture(t,p=>p.startsWith('/ui-executions/catalog')?new Promise(r=>resolve=r):undefined);f.mounted.dispose();resolve({ok:true,apps:[{app_id:'late',name:'迟到'}]});await wait();assert.equal(f.node.textContent,'');assert.equal(f.calls[0].o.signal.aborted,true);});
test('identity change clears contents on response',async t=>{let resolve;const f=fixture(t,p=>p.startsWith('/ui-executions/catalog')?new Promise(r=>resolve=r):undefined);f.setIdentity('b');resolve({ok:true,apps:[]});await wait();assert.equal(f.node.textContent,'');});
test('read only user cannot create or save',async t=>{const f=fixture(t,null,{canEdit:()=>false});await open(f);assert.equal(f.node.querySelector('[data-draft-create]').disabled,true);assert.equal(f.node.querySelector('[data-draft-save]').disabled,true);});
test('unknown submission retries same idempotency key and forbids publish',async t=>{let attempts=0;const f=fixture(t,p=>{if(p==='/ui-case-drafts/d1')return {ok:true,draft:{...draft,questions:[],steps:draft.steps.map(v=>({...v,review_state:'confirmed'}))}};if(p.endsWith('/try')&&++attempts===1)throw new Error('网络中断');});await open(f);f.node.querySelector('[data-draft-device]').value='0';f.node.querySelector('[data-draft-device]').dispatchEvent(new f.w.Event('change'));f.node.querySelector('[data-draft-try]').click();await wait();await wait();assert.equal(f.node.querySelector('[data-draft-publish]').disabled,true);f.node.querySelector('[data-draft-try]').click();await wait();const tries=f.calls.filter(c=>c.path.endsWith('/try'));assert.equal(tries.length,2);assert.equal(tries[0].o.headers['Idempotency-Key'],tries[1].o.headers['Idempotency-Key']);assert.deepEqual(tries[0].o.body,tries[1].o.body);});
test('questions are removable and saved; unresolved steps cannot execute',async t=>{const f=fixture(t);await open(f);assert.equal(f.node.querySelector('[data-draft-try]').disabled,true);f.node.querySelector('[data-resolve-question]').click();f.node.querySelector('[data-step-command="confirm"]').click();f.node.querySelector('[data-draft-save]').click();await wait();assert.equal(f.calls.find(c=>c.path.endsWith('/update')).o.body.questions.length,0);assert.equal(f.node.querySelector('[data-draft-execute]').hidden,false);});
test('publish remains unavailable unless server batch is settled and passed',async t=>{const f=fixture(t,p=>{if(p==='/ui-case-drafts/d1')return {ok:true,draft:{...draft,questions:[],steps:draft.steps.map(v=>({...v,review_state:'confirmed'}))}};if(p.endsWith('/try'))return {ok:true,batch:{ui_batch_id:'b',status:'passed',execution_settled:false}};});await open(f);const dev=f.node.querySelector('[data-draft-device]');dev.value='0';dev.dispatchEvent(new f.w.Event('change'));f.node.querySelector('[data-draft-module]').value='导航';f.node.querySelector('[data-draft-try]').click();await wait();await wait();assert.equal(f.node.querySelector('[data-draft-publish]').disabled,true);assert.equal(f.node.querySelector('[data-draft-publish-area]').hidden,true);});
test('changing application aborts older draft fetch and ignores its result',async t=>{let resolve;const f=fixture(t,p=>p==='/ui-case-drafts?app_id=a'?new Promise(r=>resolve=r):undefined);await wait();const app=f.node.querySelector('[data-draft-app]');app.value='a';app.dispatchEvent(new f.w.Event('change'));await wait();app.value='';app.dispatchEvent(new f.w.Event('change'));resolve({ok:true,items:[draft]});await wait();assert.equal(f.node.querySelector('[data-open-draft]'),null);});
test('saved edit invalidates successful execution and prevents publication',async t=>{const f=fixture(t,p=>p==='/ui-case-drafts/d1'?{ok:true,draft:{...draft,questions:[],steps:draft.steps.map(v=>({...v,review_state:'confirmed'}))}}:undefined);await open(f);const dev=f.node.querySelector('[data-draft-device]');dev.value='0';dev.dispatchEvent(new f.w.Event('change'));f.node.querySelector('[data-draft-module]').value='导航';f.node.querySelector('[data-draft-try]').click();await wait();await wait();assert.equal(f.node.querySelector('[data-draft-publish]').disabled,false);const title=f.node.querySelector('[data-draft-title]');title.value='新版';title.dispatchEvent(new f.w.Event('input',{bubbles:true}));assert.equal(f.node.querySelector('[data-draft-publish]').disabled,true);f.node.querySelector('[data-draft-save]').click();await wait();assert.equal(f.node.querySelector('[data-draft-publish]').disabled,true);});
test('accepted running batch cannot be submitted again',async t=>{const f=fixture(t,p=>{if(p==='/ui-case-drafts/d1')return {ok:true,draft:{...draft,questions:[],steps:draft.steps.map(v=>({...v,review_state:'confirmed'}))}};if(p.endsWith('/try'))return {ok:true,batch:{ui_batch_id:'b',status:'queued',execution_settled:false}};});await open(f);const dev=f.node.querySelector('[data-draft-device]');dev.value='0';dev.dispatchEvent(new f.w.Event('change'));f.node.querySelector('[data-draft-try]').click();await wait();await wait();assert.equal(f.node.querySelector('[data-draft-try]').disabled,true);f.node.querySelector('[data-draft-try]').dispatchEvent(new f.w.Event('click'));await wait();assert.equal(f.calls.filter(c=>c.path.endsWith('/try')).length,1);});
test('dispose preserves content another page put in the host',async t=>{const f=fixture(t);f.node.innerHTML='<p>下一页</p>';f.mounted.dispose();assert.equal(f.node.textContent,'下一页');});
test('historical failed draft restores source text for explicit regeneration',async t=>{const f=fixture(t,p=>p==='/ui-case-drafts/d1'?{ok:true,draft:{...draft,generation_status:'failed',source_text:'旧描述',error:'服务暂不可用'}}:undefined);await open(f);assert.equal(f.node.querySelector('[data-draft-text]').value,'旧描述');assert.equal(f.node.querySelector('[data-draft-create]').disabled,false);});
test('restoring sends the current revision and adopts server revision',async t=>{const f=fixture(t,p=>{if(p==='/ui-case-drafts/d1')return {ok:true,draft:{...draft,revision:3}};if(p.endsWith('/restore'))return {ok:true,draft:{...draft,revision:4,title:'恢复内容'}};});await open(f);f.node.querySelector('[data-draft-restore]').click();await wait();assert.equal(f.calls.find(c=>c.path.endsWith('/restore')).o.body.revision,3);assert.equal(f.node.querySelector('[data-draft-title]').value,'恢复内容');assert.match(f.node.querySelector('[data-draft-state]').textContent,/第 4 版/);});
test('editing title keeps selected target and module across save',async t=>{const f=fixture(t);await open(f);f.node.querySelector('[data-draft-device]').value='0';f.node.querySelector('[data-draft-module]').value='导航';const input=f.node.querySelector('[data-draft-title]');input.value='新名';input.dispatchEvent(new f.w.Event('input',{bubbles:true}));f.node.querySelector('[data-draft-save]').click();await wait();assert.equal(f.node.querySelector('[data-draft-device]').value,'0');assert.equal(f.node.querySelector('[data-draft-module]').value,'导航');});
const {execFileSync}=require('node:child_process');
function pythonContract(payload,model=false){const code=model?"import json,sys; from task_server.services.ui_case_draft_service import _model_proposal; from task_server.services.ui_case_draft_contract import build_draft_yaml; p=_model_proposal(json.loads(sys.argv[1]), 'com.kfb.model'); print(json.dumps({**p,**build_draft_yaml(p,'com.kfb.model')},ensure_ascii=False))":"import json,sys; from task_server.services.ui_case_draft_contract import normalize_steps,build_draft_yaml; p=normalize_steps(json.loads(sys.argv[1]), 'com.kfb.model'); print(json.dumps({**p,**build_draft_yaml(p,'com.kfb.model')},ensure_ascii=False))";return JSON.parse(execFileSync('python3',['-c',code,JSON.stringify(payload)],{encoding:'utf8'}));}
test('real model contract fixture can be corrected in editor, saved, and normalized again',async t=>{
 const proposal={title:'个人列表检查',preconditions:[],questions:[],steps:[{action:'aiTap',target:'我的页面的打印记录入口',value:'',expected:'',review_state:'needs_review',source_ref:'text'},{action:'aiScroll',target:'打印记录列表',value:{direction:'down',distance:300,scrollType:'singleAction'},expected:'',review_state:'needs_review',source_ref:'text'},{action:'aiAssert',target:'列表显示测试记录',value:'',expected:'列表显示测试记录',review_state:'needs_review',source_ref:'text'}]};
 const generated=pythonContract(proposal,true);let saved;
 const f=fixture(t,(p,o)=>{if(p.startsWith('/ui-executions/catalog'))return {ok:true,apps:[{app_id:'com.kfb.model',name:'智小白3D'}],modules:['导航'],publication_modules:['导航']};if(p==='/ui-case-drafts?app_id=com.kfb.model')return {ok:true,items:[{...draft,app_id:'com.kfb.model'}]};if(p==='/ui-case-drafts/d1')return {ok:true,draft:{...generated,draft_id:'d1',revision:2,app_id:'com.kfb.model',generation_status:'ready'}};if(p.endsWith('/update')){const payload={title:o.body.title,preconditions:o.body.preconditions,steps:o.body.steps,questions:o.body.questions};saved=pythonContract(payload);return {ok:true,draft:{...saved,draft_id:'d1',revision:3,app_id:'com.kfb.model',generation_status:'edited'}};}});
 await open(f,'com.kfb.model');
 const type=(el,text)=>{el.value=text;el.dispatchEvent(new f.w.Event('input',{bubbles:true}));};
 const rows=f.node.querySelectorAll('[data-step-index]');type(rows[0].querySelector('[data-step-field="expected"]'),'打开打印记录页面');type(rows[1].querySelector('[data-step-field="expected"]'),'下方测试记录可见');
 const scroll=rows[1].querySelector('[data-scroll-field="direction"]');scroll.value='up';scroll.dispatchEvent(new f.w.Event('change',{bubbles:true}));
 type(f.node.querySelector('[data-scroll-field="distance"]'),'240');
 while(f.node.querySelector('[data-resolve-question]'))f.node.querySelector('[data-resolve-question]').click();
 for(const button of [...f.node.querySelectorAll('[data-step-command="confirm"]')]){const i=button.closest('[data-step-index]').dataset.stepIndex;f.node.querySelector(`[data-step-index="${i}"] [data-step-command="confirm"]`)?.click();}
 f.node.querySelector('[data-draft-add]').click();const added=f.node.querySelector('[data-step-index="3"]');type(added.querySelector('[data-step-field="assertion"]'),'打印记录列表显示测试记录');added.querySelector('[data-step-command="confirm"]').click();
 f.node.querySelector('[data-draft-save]').click();await wait();
 assert.equal(saved.ready,true);assert.equal(saved.steps[1].value.direction,'up');assert.equal(saved.steps[1].value.distance,240);assert.equal(saved.steps[3].target,saved.steps[3].expected);assert.match(saved.steps[3].step_id,/^[a-f0-9-]{36}$/);assert.equal(f.node.querySelector('[data-draft-execute]').hidden,false);assert.match(f.node.querySelector('[data-draft-state]').textContent,/第 3 版/);
});
const confirmedDraft={...draft,questions:[],steps:draft.steps.map(v=>({...v,expected:'我的页面打开',review_state:'confirmed'}))};
test('publication choices ignore legacy catalog modules and use current application ownership',async t=>{
 const f=fixture(t,p=>p.startsWith('/ui-executions/catalog')?{ok:true,apps:[{app_id:'a',name:'应用'}],modules:['错误历史模块'],publication_modules:['本应用模块']}:undefined);await open(f);
 assert.deepEqual([...f.node.querySelector('[data-draft-module]').options].map(o=>o.value),['','本应用模块']);
});
test('editing a confirmed step immediately exposes review without replacing focused input',async t=>{
 const f=fixture(t,p=>p==='/ui-case-drafts/d1'?{ok:true,draft:confirmedDraft}:undefined);await open(f);
 const input=f.node.querySelector('[data-step-field="expected"]');input.focus();input.value='我的页面显示我的设备入口';input.dispatchEvent(new f.w.Event('input',{bubbles:true}));
 const row=input.closest('[data-step-index]');assert.equal(row.querySelector('small').textContent,'待确认');
 assert.ok(row.querySelector('[data-step-command="confirm"]'));
 assert.equal(f.w.document.activeElement,input);assert.equal(f.node.querySelector('[data-step-field="expected"]'),input);
 row.querySelector('[data-step-command="confirm"]').click();f.node.querySelector('[data-draft-save]').click();await wait();
 const saved=f.calls.find(c=>c.path.endsWith('/update')).o.body.steps[0];assert.equal(saved.review_state,'confirmed');assert.equal(saved.expected,'我的页面显示我的设备入口');
});
async function trial(f){const dev=f.node.querySelector('[data-draft-device]');dev.value='0';dev.dispatchEvent(new f.w.Event('change'));f.node.querySelector('[data-draft-try]').click();await wait();await wait();}
test('running trial exposes real cancel, stores batch, and never offers automatic rerun',async t=>{let actions;const f=fixture(t,p=>{if(p==='/ui-case-drafts/d1')return {ok:true,draft:confirmedDraft};if(p.endsWith('/try'))return {ok:true,batch:{ui_batch_id:'b',status:'running',execution_settled:false}};if(p.endsWith('/cancel'))return {ok:true,batch:{ui_batch_id:'b',status:'cancelled',execution_settled:true}};});f.w.UiBatchView.update=(n,b,a)=>{actions=a;n.textContent=b.status;};await open(f);await trial(f);assert.equal(typeof actions.cancel,'function');assert.equal(actions.rerun,undefined);await actions.cancel('b');assert.equal(f.calls.find(c=>c.path==='/ui-executions/b/cancel').o.method,'POST');assert.equal(f.node.querySelector('[data-draft-batch]').textContent,'cancelled');assert.equal(f.node.querySelector('[data-draft-publish]').disabled,true);const saved=JSON.parse(f.w.sessionStorage.getItem('ui-draft-progress:a:d1:1'));assert.equal(saved.batchId,'b');assert.equal(saved.source_text,undefined);});
test('unknown cancel preserves batch and blocks duplicate trial until real terminal state',async t=>{let actions;const f=fixture(t,p=>{if(p==='/ui-case-drafts/d1')return {ok:true,draft:confirmedDraft};if(p.endsWith('/try'))return {ok:true,batch:{ui_batch_id:'b',status:'running',execution_settled:false}};if(p.endsWith('/cancel'))throw new Error('网络中断');});f.w.UiBatchView.update=(n,b,a)=>{actions=a;n.textContent=b.status;};await open(f);await trial(f);await assert.rejects(actions.cancel('b'),/取消结果待核对/);assert.equal(f.node.querySelector('[data-draft-try]').disabled,true);assert.match(f.node.querySelector('[data-draft-notice]').textContent,/取消请求未确认/);assert.equal(f.node.querySelector('[data-draft-refresh]').hidden,false);});
test('reopening draft reloads real batch and recovers publication eligibility without a new trial',async t=>{const f=fixture(t,p=>{if(p==='/ui-case-drafts/d1')return {ok:true,draft:confirmedDraft};if(p==='/ui-executions/b')return {ok:true,batch:{ui_batch_id:'b',status:'passed',execution_settled:true}};});await open(f);await trial(f);f.mounted.dispose();f.remount();await open(f);await wait();f.node.querySelector('[data-draft-module]').value='导航';f.node.querySelector('[data-draft-module]').dispatchEvent(new f.w.Event('change'));assert.equal(f.node.querySelector('[data-draft-publish]').disabled,false);assert.equal(f.calls.filter(c=>c.path.endsWith('/try')).length,1);assert.equal(f.node.querySelector('[data-draft-device]').value,'0');});
test('unknown intent survives leaving and retries identical original request after reopening',async t=>{let tries=0;const f=fixture(t,p=>{if(p==='/ui-case-drafts/d1')return {ok:true,draft:confirmedDraft};if(p.endsWith('/try')&&++tries===1)throw new Error('网络中断');});await open(f);await trial(f);const first=f.calls.find(c=>c.path.endsWith('/try'));f.mounted.dispose();f.remount();await open(f);assert.equal(f.node.querySelector('[data-draft-try]').textContent,'重试核对上次试跑');f.node.querySelector('[data-draft-try]').click();await wait();assert.equal(f.calls.filter(c=>c.path.endsWith('/try')).length,2);const last=f.calls.filter(c=>c.path.endsWith('/try')).at(-1);assert.equal(last.o.headers['Idempotency-Key'],first.o.headers['Idempotency-Key']);assert.deepEqual(last.o.body,first.o.body);});
test('pending trial is persisted before leaving so a late acceptance cannot erase recovery',async t=>{let resolve;const f=fixture(t,p=>{if(p==='/ui-case-drafts/d1')return {ok:true,draft:confirmedDraft};if(p.endsWith('/try'))return new Promise(r=>resolve=r);});await open(f);await trial(f);assert.ok(JSON.parse(f.w.sessionStorage.getItem('ui-draft-progress:a:d1:1')).uncertain);f.mounted.dispose();resolve({ok:true,batch:{ui_batch_id:'b',status:'running',execution_settled:false}});await wait();assert.ok(JSON.parse(f.w.sessionStorage.getItem('ui-draft-progress:a:d1:1')).uncertain);});
test('real UiBatchView cancel button performs draft trial cancellation',async t=>{const batch={ui_batch_id:'b',status:'running',execution_settled:false,case_attempts:[],report_status:'not_requested',delivery_status:'not_requested'};const f=fixture(t,p=>{if(p==='/ui-case-drafts/d1')return {ok:true,draft:confirmedDraft};if(p.endsWith('/try'))return {ok:true,batch};if(p.endsWith('/cancel'))return {ok:true,batch:{...batch,status:'cancelled',execution_settled:true}};});f.w.eval(fs.readFileSync('js/ui-batch-view.js','utf8'));await open(f);await trial(f);const button=f.node.querySelector('[data-batch-action="cancel"]');assert.equal(button.disabled,false);assert.equal(f.node.querySelector('[data-batch-action="rerun"]').hidden,true);button.click();await wait();assert.equal(f.calls.filter(c=>c.path.endsWith('/cancel')).length,1);assert.equal(f.node.querySelector('[data-batch-action="cancel"]').hidden,true);});
test('stored progress never applies to a different signed in owner',async t=>{const f=fixture(t,p=>p==='/ui-case-drafts/d1'?{ok:true,draft:confirmedDraft}:undefined);await open(f);await trial(f);f.mounted.dispose();f.setIdentity('other');f.remount();await open(f);assert.equal(f.calls.some(c=>c.path==='/ui-executions/b'),false);assert.equal(f.node.querySelector('[data-draft-publish]').disabled,true);});
test('late state refresh cannot replace newer cancellation result',async t=>{let resolve,actions;const batch={ui_batch_id:'b',status:'running',execution_settled:false};const f=fixture(t,p=>{if(p==='/ui-case-drafts/d1')return {ok:true,draft:confirmedDraft};if(p.endsWith('/try'))return {ok:true,batch};if(p==='/ui-executions/b')return new Promise(r=>resolve=r);if(p.endsWith('/cancel'))return {ok:true,batch:{...batch,status:'cancelled',execution_settled:true}};});f.w.UiBatchView.update=(n,b,a)=>{actions=a;n.textContent=b.status;};await open(f);await trial(f);f.node.querySelector('[data-draft-refresh]').click();await wait();await actions.cancel('b');resolve({ok:true,batch});await wait();assert.equal(f.node.querySelector('[data-draft-batch]').textContent,'cancelled');});
test('new users receive a next action and can refresh phones without recreating the draft',async t=>{
 const f=fixture(t);await open(f);assert.match(f.node.querySelector('[data-draft-next]').textContent,/需要你确认/);
 const before=f.calls.filter(c=>c.path.startsWith('/ui-executions/devices')).length;
 f.node.querySelector('[data-draft-devices-refresh]').click();await wait();
 assert.equal(f.calls.filter(c=>c.path.startsWith('/ui-executions/devices')).length,before+1);
 assert.equal(f.calls.filter(c=>c.path==='/ui-case-drafts'&&c.o.method==='POST').length,0);
});
test('switching applications clears old drafts immediately while the next list is loading',async t=>{
 let resolve;const f=fixture(t,p=>{
  if(p.startsWith('/ui-executions/catalog'))return {ok:true,apps:[{app_id:'a',name:'应用甲'},{app_id:'b',name:'应用乙'}],modules:['导航'],publication_modules:['导航']};
  if(p==='/ui-case-drafts?app_id=b')return new Promise(r=>resolve=r);
 });await open(f);const app=f.node.querySelector('[data-draft-app]');app.value='b';app.dispatchEvent(new f.w.Event('change'));await wait();
 assert.equal(f.node.querySelector('[data-open-draft]'),null);resolve({ok:true,items:[]});await wait();
});
test('draft response for another application is not adopted into the current editor',async t=>{
 const f=fixture(t,p=>p==='/ui-case-drafts/d1'?{ok:true,draft:{...draft,app_id:'other'}}:undefined);await open(f);
 assert.equal(f.node.querySelector('[data-draft-editor]').hidden,true);
 assert.match(f.node.querySelector('[data-draft-notice]').textContent,/应用/);
});

test('compact steps show readable summaries and expand one editor without losing edits',async t=>{
 const two={...confirmedDraft,steps:[confirmedDraft.steps[0],{...confirmedDraft.steps[0],step_id:'s2',target:'首页',expected:'首页显示模型推荐'}]};
 const f=fixture(t,p=>p==='/ui-case-drafts/d1'?{ok:true,draft:two}:undefined);await open(f);
 const rows=()=>[...f.node.querySelectorAll('[data-step-index]')];
 assert.ok(rows().every(row=>row.querySelector('[data-step-editor]').hidden));
 assert.match(rows()[0].querySelector('[data-step-summary]').textContent,/我的/);
 rows()[0].querySelector('[data-step-toggle]').click();assert.equal(rows()[0].querySelector('[data-step-editor]').hidden,false);
 const input=rows()[0].querySelector('[data-step-field="expected"]');input.value='我的设备入口可见';input.dispatchEvent(new f.w.Event('input',{bubbles:true}));
 rows()[1].querySelector('[data-step-toggle]').click();assert.equal(rows()[0].querySelector('[data-step-editor]').hidden,true);
 assert.equal(rows()[1].querySelector('[data-step-editor]').hidden,false);
 rows()[0].querySelector('[data-step-toggle]').click();assert.equal(rows()[0].querySelector('[data-step-field="expected"]').value,'我的设备入口可见');
 rows()[0].querySelector('[data-step-command="confirm"]').click();assert.equal(rows()[0].querySelector('[data-step-editor]').hidden,true);
 assert.match(rows()[0].querySelector('[data-step-summary]').textContent,/我的设备入口可见/);
 assert.equal(f.node.querySelector('[data-draft-try]').disabled,true);
});
test('selected phone preview remains visible during review and ignores late pictures on switch or disposal',async t=>{
 const pictures=[],phones=['安卓测试机','苹果测试机'].map((name,i)=>({name,connection_status:'ready',app_id:'a',os_version:i?'26':'15',app_version:i?'1.19.7 (70)':'2.0.0 (51)',snapshot_url:'/private/'+i,snapshot_captured_at:'2026-10-15 12:00:00',target:{runner_id:'r',device_id:'d'+i,platform:i?'ios':'android'}}));
 const f=fixture(t,p=>p.startsWith('/ui-executions/devices')?{ok:true,devices:phones}:undefined,{loadPicture:(img,device,current)=>{pictures.push({img,device,current});return Promise.resolve(false);}});await open(f);
 assert.equal(f.node.querySelector('[data-draft-execute]').hidden,false);
 const select=f.node.querySelector('[data-draft-device]');select.value='0';select.dispatchEvent(new f.w.Event('change'));await wait();
 assert.equal(pictures.length,1);assert.match(f.node.querySelector('[data-draft-phone]').textContent,/安卓测试机/);assert.match(f.node.querySelector('[data-draft-phone]').textContent,/2.0.0/);
 assert.equal(f.node.querySelector('[data-draft-try]').disabled,true);
 select.value='1';select.dispatchEvent(new f.w.Event('change'));await wait();assert.equal(pictures[0].current(),false);assert.equal(pictures[1].current(),true);
 assert.match(f.node.querySelector('[data-draft-phone]').textContent,/苹果测试机/);assert.doesNotMatch(f.node.querySelector('[data-draft-phone]').textContent,/2.0.0/);
 f.mounted.dispose();assert.equal(pictures[1].current(),false);
});

test('read-only users can expand steps while mutation controls stay disabled',async t=>{
 const f=fixture(t,p=>p==='/ui-case-drafts/d1'?{ok:true,draft:confirmedDraft}:undefined,{canEdit:()=>false});await open(f);
 const row=f.node.querySelector('[data-step-index]');row.querySelector('[data-step-toggle]').click();
 assert.equal(row.querySelector('[data-step-editor]').hidden,false);
 assert.equal(row.querySelector('[data-step-field="expected"]').disabled,true);
 assert.equal(row.querySelector('[data-step-command="delete"]').disabled,true);
});

test('imported draft mounts directly and retains its immutable source note',async t=>{
 const f=fixture(t,undefined,{initialDraft:{...draft,source_snapshot:{session_id:'abc',snapshot_id:'hash',evidence_note:'原截图可能已变更或不可用'}}});await wait();
 assert.equal(f.node.querySelector('[data-draft-title]').value,'导航');
 assert.match(f.node.textContent,/原截图可能已变更或不可用/);
 assert.equal(f.calls.filter(c=>c.o.method==='POST').length,0);
});

test('recording editor separates provenance from text generation and hides technical IDs',async t=>{
 const f=fixture(t,undefined,{initialDraft:{...draft,source_text:'来源：已结束的操作录制',source_snapshot:{session_id:'a'.repeat(32),snapshot_id:'b'.repeat(64),actions:[{}],evidence_note:'原截图可能已变更或不可用'}}});await wait();
 assert.equal(f.node.querySelector('h2').textContent,'整理录制用例');
 assert.equal(f.node.querySelector('[data-draft-text]').value,'');
 assert.equal(f.node.querySelector('[data-draft-create]').disabled,true);
 assert.doesNotMatch(f.node.querySelector('[data-draft-source]').textContent,/[a]{32}/);
});
test('advanced candidate is collapsed, explicit, and blocks conflicting changes until cancelled',async t=>{
 const f=fixture(t,p=>p==='/ui-case-drafts/d1'?{ok:true,draft:confirmedDraft}:undefined);await open(f);
 const input=f.node.querySelector('[data-draft-yaml-input]');assert.ok(input);assert.equal(input.closest('details').open,false);
 input.value='tasks: [';input.dispatchEvent(new f.w.Event('input',{bubbles:true}));
 assert.equal(f.node.querySelector('[data-draft-title]').disabled,true);assert.equal(f.node.querySelector('[data-draft-restore]').disabled,true);assert.equal(f.node.querySelector('[data-draft-try]').disabled,true);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/yaml-candidate')).length,0);
 f.node.querySelector('[data-draft-yaml-cancel]').click();assert.equal(input.value,confirmedDraft.yaml_content);assert.equal(f.node.querySelector('[data-draft-title]').disabled,false);
});
test('advanced candidate raw survives departure and explicit apply adopts blocked revision',async t=>{
 const f=fixture(t,(p,o)=>{if(p.endsWith('/yaml-candidate'))return {ok:true,draft:{...draft,revision:2,yaml_candidate:{raw:o.body.yaml_content,status:'invalid',error:'YAML 格式错误'}}};});await open(f);
 let input=f.node.querySelector('[data-draft-yaml-input]');input.value='tasks: [';input.dispatchEvent(new f.w.Event('input',{bubbles:true}));
 f.mounted.dispose();f.remount();await open(f);input=f.node.querySelector('[data-draft-yaml-input]');assert.equal(input.value,'tasks: [');
 f.node.querySelector('[data-draft-yaml-apply]').click();await wait();assert.deepEqual(JSON.parse(JSON.stringify(f.calls.find(c=>c.path.endsWith('/yaml-candidate')).o.body)),{revision:1,yaml_content:'tasks: ['});
 assert.equal(f.node.querySelector('[data-draft-title]').disabled,true);assert.equal(f.node.querySelector('[data-draft-restore]').disabled,false);assert.match(f.node.textContent,/YAML 格式错误/);assert.equal(f.calls.filter(c=>/\/(try|publish)$/.test(c.path)).length,0);
});
test('converted raw never overrides latest Chinese-generated YAML',async t=>{
 const f=fixture(t,p=>p==='/ui-case-drafts/d1'?{ok:true,draft:{...draft,yaml_content:'最新中文步骤脚本',yaml_candidate:{raw:'原始脚本',status:'converted',error:''}}}:undefined);await open(f);
 assert.equal(f.node.querySelector('[data-draft-yaml-input]').value,'最新中文步骤脚本');
});
test('YAML buffer is isolated by revision and restored buffer blocks create and app changes',async t=>{
 const f=fixture(t);await open(f);const input=f.node.querySelector('[data-draft-yaml-input]');input.value='未提交脚本';input.dispatchEvent(new f.w.Event('input',{bubbles:true}));
 assert.equal(f.node.querySelector('[data-draft-app]').disabled,true);assert.equal(f.node.querySelector('[data-open-draft]').disabled,true);
 f.node.querySelector('[data-draft-text]').value='新的描述';f.node.querySelector('[data-draft-create]').dispatchEvent(new f.w.Event('click'));await wait();assert.equal(f.calls.filter(c=>c.path==='/ui-case-drafts').length,0);
 const e=new f.w.Event('beforeunload',{cancelable:true});f.w.dispatchEvent(e);assert.equal(e.defaultPrevented,true);
});
test('YAML-derived recording steps are presented as manual script, retaining audit origin',async t=>{
 const manual={...draft,source_snapshot:{actions:[{}],evidence_note:'原始证据'},steps:draft.steps.map(s=>({...s,source_ref:'manual_yaml'}))};
 const f=fixture(t,p=>p==='/ui-case-drafts/d1'?{ok:true,draft:manual}:undefined);await open(f);
 assert.match(f.node.querySelector('[data-draft-source]').textContent,/手工编辑脚本/);
 assert.match(f.node.querySelector('[data-draft-source]').textContent,/历史来源/);
});
test('generation polling fills advanced YAML without reopening the newly created draft',async t=>{
 let polls=0,poll;const generated='android: {}\ntasks: [{name: 新用例, flow: [{aiAssert: 首页显示标题}]}]';
 const f=fixture(t,p=>{if(p==='/ui-case-drafts/d1')return {ok:true,draft:++polls===1?{...draft,generation_status:'queued',steps:[],yaml_content:''}:{...draft,revision:2,generation_status:'ready',yaml_content:generated}};});
 const timeout=f.w.setTimeout.bind(f.w);f.w.setTimeout=(fn,ms,...args)=>ms===2000?(poll=fn,12345):timeout(fn,ms,...args);
 await open(f);assert.equal(f.node.querySelector('[data-draft-yaml-input]').value,'');assert.equal(typeof poll,'function');await poll();
 assert.equal(f.node.querySelector('[data-draft-yaml-input]').value,generated);assert.equal(f.node.querySelector('[data-draft-yaml-apply]').disabled,true);
});
test('generation refresh does not overwrite a recovered unsubmitted YAML buffer',async t=>{
 let polls=0,poll;const f=fixture(t,p=>p==='/ui-case-drafts/d1'?{ok:true,draft:{...draft,generation_status:++polls===1?'queued':'ready',yaml_content:polls===1?'':'生成后的脚本'}}:undefined);
 f.w.sessionStorage.setItem('ui-draft-yaml:a:d1:1','需要保留的未提交脚本');
 const timeout=f.w.setTimeout.bind(f.w);f.w.setTimeout=(fn,ms,...args)=>ms===2000?(poll=fn,12345):timeout(fn,ms,...args);
 await open(f);await poll();assert.equal(f.node.querySelector('[data-draft-yaml-input]').value,'需要保留的未提交脚本');assert.equal(f.node.querySelector('[data-draft-yaml-apply]').disabled,false);
 assert.equal(f.w.sessionStorage.getItem('ui-draft-yaml:a:d1:1'),'需要保留的未提交脚本');
});
