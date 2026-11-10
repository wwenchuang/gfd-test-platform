const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('../api-testing-ui/node_modules/jsdom');
const wait=()=>new Promise(setImmediate);
const mockPracticeFetch=w=>{const handler=w.apiRequest;w.nativeFetch=async(path,options)=>({ok:true,status:200,json:async()=>({ok:true,data:await handler(path.slice(4),options)})});};
const mockCatalogFetch=(w,artifact)=>{w.nativeFetch=async(path,options={})=>{if(!path.startsWith('/api/ui-executions/web-check')||path.includes('/artifact'))return artifact(path,options);try{const value=await w.apiRequest(path.slice(4),{...options,body:options.body?JSON.parse(options.body):undefined});return {ok:true,status:200,json:async()=>value};}catch(error){return {ok:false,status:error.status||500};}};};
test('workspace submits each named catalog and start workflow and exposes its evidence',async t=>{
 const {w}=fixture(t),posts=[];w.canAccessWorkflow=()=>true;
 w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(options.method==='POST'){posts.push(options.body);return {ok:true,result:{id:'a'.repeat(32),scenario:options.body.scenario,status:'passed',total:0,artifacts:['trace']}};}return {ok:true,results:[]};};
 mockCatalogFetch(w);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');
 for(const scenario of ['ui_catalog_filter','ui_catalog_empty','ui_case_selection','ui_case_revision','ui_start_handoff','ui_start_switch','ui_start_unready','ui_catalog_paging','ui_catalog_module','ui_case_hidden_selection','ui_start_empty_guard']){const button=panel.querySelector(`[data-catalog-scenario="${scenario}"]`);assert.ok(button);button.click();await wait();await wait();assert.equal(posts.at(-1).scenario,scenario);assert.ok(panel.querySelector('[data-catalog-artifact="trace"]'));}
 assert.match(panel.textContent,/空.*搜索/);assert.match(panel.textContent,/不代表.*业务/);
});
test('workspace starts a fixed failure drill and shows its mismatched counts and evidence',async t=>{
 const {w}=fixture(t),posts=[];w.canAccessWorkflow=()=>true;
 const result={id:'a'.repeat(32),scenario:'ui_catalog_failure_drill',status:'failed',error_kind:'drill_assertion',total:3,expected_total:0,artifacts:['screenshot','trace']};
 w.apiRequest=async(path,options={})=>{
   if(path==='/ui-executions/availability')return {ok:true,enabled:true};
   if(options.method==='POST'){posts.push(options.body);return {ok:true,result};}
   return {ok:true,results:[result]};
 };
 mockCatalogFetch(w);await w.initUiSimpleAvailability({navigate:false});
 w.activeWorkflow='ai_practice';w.showAiPractice();
 const panel=w.document.querySelector('[data-catalog-check]');
 const drill=panel.querySelector('[data-catalog-scenario="ui_catalog_failure_drill"]');
 assert.ok(drill);drill.click();await wait();await wait();
 assert.deepEqual(JSON.parse(JSON.stringify(posts)),[{scenario:result.scenario}]);
 const row=panel.querySelector('.ui-week-probe-row');
 assert.match(row.textContent,/演练断言按预期失败/);assert.match(row.textContent,/预期 0 条，页面实际显示 3 条/);
 assert.ok(row.querySelector('[data-catalog-artifact="screenshot"]'));assert.ok(row.querySelector('[data-catalog-artifact="trace"]'));
});
test('catalog filter evidence identifies its real asset version for repeatability',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const asset={app_id:'real.app',case_id:'real-case',revision:'a'.repeat(64)};
 w.apiRequest=async(path,options={})=>path==='/ui-executions/availability'?{ok:true,enabled:true}:options.method==='POST'?{ok:true,result:{id:'f'.repeat(32),scenario:'ui_catalog_filter',status:'passed',total:1,asset,artifacts:[]}}:{ok:true,results:[]};
 mockCatalogFetch(w);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.querySelector('[data-catalog-scenario="ui_catalog_filter"]').click();await wait();await wait();assert.match(panel.textContent,/real.app/);assert.match(panel.textContent,/real-case/);assert.match(panel.textContent,new RegExp('a{64}'));
});
test('read-only catalog check is explicit and keeps the evidence distinct from business success',async t=>{
 const {w}=fixture(t),calls=[];w.canAccessWorkflow=()=>true;
 w.apiRequest=async(path,options={})=>{calls.push([path,options]);if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(path==='/ui-executions/web-check'&&options.method==='POST')return {ok:true,result:{id:'a'.repeat(32),scenario:'ui_catalog_count',status:'passed',total:7,created:1730000000,artifacts:['screenshot','trace']}};return {ok:true,results:[]};};
 mockCatalogFetch(w);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');assert.ok(panel);assert.match(panel.textContent,/页面.*数量/);assert.match(panel.textContent,/不代表.*业务/);assert.equal(calls.filter(([p])=>p==='/ui-executions/web-check').length,0);
 panel.querySelector('[data-catalog-run]').click();await wait();await wait();assert.equal(calls.filter(([p,o])=>p==='/ui-executions/web-check'&&o.method==='POST').length,1);assert.match(panel.textContent,/一致/);assert.match(panel.textContent,/7/);assert.ok(panel.querySelector('[data-catalog-artifact="screenshot"]'));
});
test('catalog check rejects stale account, token and scope responses and hides earlier evidence',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let resolve;const id='b'.repeat(32);
 w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(path==='/ui-executions/web-check'&&options.method==='POST')return new Promise(r=>resolve=r);return {ok:true,results:[]};};
 mockCatalogFetch(w);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();let panel=w.document.querySelector('[data-catalog-check]');panel.querySelector('[data-catalog-run]').click();w.currentAccessProfile={user_id:'u1',scope:{ui_apps:['other']}};resolve({ok:true,result:{id,scenario:'ui_catalog_count',status:'passed',total:99,artifacts:['screenshot']}});await wait();await wait();assert.doesNotMatch(panel.textContent,/99/);assert.equal(panel.querySelector('[data-catalog-artifact]'),null);
 w.resetUiSimpleWorkflow();assert.equal(w.document.querySelector('[data-catalog-check]'),null);
});
test('catalog screenshot is read with current authentication and rejects a changed session',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='c'.repeat(32);let resolve,request;
 w.apiRequest=async(path,options={})=>path==='/ui-executions/availability'?{ok:true,enabled:true}:options.method==='POST'?{ok:true,result:{id,scenario:'ui_catalog_count',status:'failed',artifacts:['screenshot']}}:{ok:true,results:[]};
 mockCatalogFetch(w,(path,options)=>{request={path,options};return new Promise(r=>resolve=r);});await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.querySelector('[data-catalog-run]').click();await wait();await wait();panel.querySelector('[data-catalog-artifact="screenshot"]').click();assert.equal(request.path,'/api/ui-executions/web-check/artifact?id='+id+'&item=screenshot');assert.ok(request.options.headers.has('Authorization'));w.sessionStorage.setItem('sessionToken','other');resolve({ok:true,headers:{get:()=> 'image/png'},blob:async()=>new w.Blob(['image'],{type:'image/png'})});await wait();await wait();assert.equal(panel.querySelector('[data-catalog-preview]'),null);
});
test('catalog check removes a returned result when the server rejects the follow-up history request',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='d'.repeat(32);
 w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(path==='/ui-executions/web-check'&&options.method==='POST')return {ok:true,result:{id,scenario:'ui_catalog_count',status:'passed',total:11,artifacts:['screenshot']}};throw Object.assign(Error('denied'),{status:403});};
 mockCatalogFetch(w);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.querySelector('[data-catalog-run]').click();await wait();await wait();assert.doesNotMatch(panel.textContent,/11|页面显示一致/);assert.equal(panel.querySelector('[data-catalog-artifact]'),null);assert.match(panel.textContent,/权限/);
});
test('catalog check clears visible results after evidence authorization is revoked',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='e'.repeat(32);
 w.apiRequest=async(path,options={})=>path==='/ui-executions/availability'?{ok:true,enabled:true}:options.method==='POST'?{ok:true,result:{id,scenario:'ui_catalog_count',status:'passed',total:13,artifacts:['screenshot']}}:{ok:true,results:[]};
 mockCatalogFetch(w,async()=>({ok:false,status:403,headers:{get:()=>null}}));await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.querySelector('[data-catalog-run]').click();await wait();await wait();panel.querySelector('[data-catalog-artifact="screenshot"]').click();await wait();await wait();assert.doesNotMatch(panel.textContent,/13|页面显示一致/);assert.equal(panel.querySelector('[data-catalog-artifact]'),null);assert.match(panel.textContent,/无权/);
});
test('catalog request uses a session-bound fetch and stale 401 cannot log out the next user',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let resolve,loggedOut=false;w.forceLogoutWithMessage=()=>loggedOut=true;
 w.apiRequest=async path=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};throw Error('must not use global request');};
 w.nativeFetch=(path,options)=>new Promise(r=>{resolve=()=>r({ok:false,status:401,headers:{get:()=>null},json:async()=>({})});});
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.querySelector('[data-catalog-run]').click();w.currentAccessProfile={user_id:'u2',scope:{ui_apps:'*'}};w.sessionStorage.setItem('sessionToken','new-session');resolve();await wait();await wait();assert.equal(loggedOut,false);assert.doesNotMatch(panel.textContent,/页面显示一致/);
});
test('catalog evidence 404 clears previously shown screenshot and results',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='f'.repeat(32);let deny=false;
 w.apiRequest=async(path,options={})=>path==='/ui-executions/availability'?{ok:true,enabled:true}:options.method==='POST'?{ok:true,result:{id,scenario:'ui_catalog_count',status:'passed',total:17,artifacts:['screenshot']}}:{ok:true,results:[]};
 mockCatalogFetch(w,async()=>deny?{ok:false,status:404,headers:{get:()=>null}}:{ok:true,status:200,headers:{get:key=>key==='content-type'?'image/png':key==='content-length'?'5':null},blob:async()=>new w.Blob(['image'],{type:'image/png'})});
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.querySelector('[data-catalog-run]').click();await wait();await wait();panel.querySelector('[data-catalog-artifact="screenshot"]').click();await wait();await wait();assert.ok(panel.querySelector('[data-catalog-preview]'));deny=true;panel.querySelector('[data-catalog-artifact="screenshot"]').click();await wait();await wait();assert.equal(panel.querySelector('[data-catalog-preview]'),null);assert.doesNotMatch(panel.textContent,/17|页面显示一致/);
});
test('catalog evidence without a stream or content length is rejected before reading',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='1'.repeat(32);let read=false;
 w.apiRequest=async(path,options={})=>path==='/ui-executions/availability'?{ok:true,enabled:true}:options.method==='POST'?{ok:true,result:{id,scenario:'ui_catalog_count',status:'passed',total:3,artifacts:['screenshot']}}:{ok:true,results:[]};
 mockCatalogFetch(w,async()=>({ok:true,status:200,headers:{get:key=>key==='content-type'?'image/png':null},blob:async()=>{read=true;return new w.Blob(['unsafe']);}}));
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.querySelector('[data-catalog-run]').click();await wait();await wait();panel.querySelector('[data-catalog-artifact="screenshot"]').click();await wait();await wait();assert.equal(read,false);assert.equal(panel.querySelector('[data-catalog-preview]'),null);
});
test('catalog evidence is cleared on returning to the page until authorization is refreshed',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='2'.repeat(32);
 w.apiRequest=async(path,options={})=>path==='/ui-executions/availability'?{ok:true,enabled:true}:options.method==='POST'?{ok:true,result:{id,scenario:'ui_catalog_count',status:'passed',total:4,artifacts:['screenshot']}}:{ok:true,results:[]};
 mockCatalogFetch(w,async()=>({ok:true,status:200,headers:{get:key=>key==='content-type'?'image/png':key==='content-length'?'5':null},blob:async()=>new w.Blob(['image'])}));
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.querySelector('[data-catalog-run]').click();await wait();await wait();panel.querySelector('[data-catalog-artifact="screenshot"]').click();await wait();await wait();assert.ok(panel.querySelector('[data-catalog-preview]'));w.dispatchEvent(new w.Event('focus'));assert.equal(panel.querySelector('[data-catalog-preview]'),null);assert.equal(panel.querySelector('[data-catalog-artifact]'),null);assert.match(panel.textContent,/刷新/);
});
test('catalog deletion requires explicit confirmation and removes only the selected record',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='3'.repeat(32),calls=[];let confirmed=false,removed=false;
 w.confirm=()=>confirmed;w.apiRequest=async(path,options={})=>{calls.push([path,options]);if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(options.method==='DELETE'){removed=true;return {ok:true,deleted:true};}if(options.method==='POST')return {ok:true,result:{id,scenario:'ui_catalog_count',status:'passed',total:8,artifacts:['trace']}};return {ok:true,results:removed?[]:[{id,scenario:'ui_catalog_count',status:'passed',total:8,artifacts:['trace']}]};};
 mockCatalogFetch(w);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.querySelector('[data-catalog-run]').click();await wait();await wait();const button=panel.querySelector('[data-catalog-delete]');assert.ok(button);button.click();await wait();assert.equal(removed,false);confirmed=true;button.click();await wait();await wait();const deletion=calls.find(([p,o])=>o.method==='DELETE');assert.equal(deletion[0],'/ui-executions/web-check?id='+id);assert.equal(deletion[1].body.confirm,true);assert.equal(panel.querySelector('[data-catalog-artifact]'),null);
});
test('catalog deletion pending hides trace and permits retry',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='4'.repeat(32);w.confirm=()=>true;let pending=false;
 w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(options.method==='DELETE'){pending=true;throw Object.assign(Error('pending'),{status:409});}return {ok:true,results:[{id,scenario:'ui_catalog_count',status:'passed',total:9,artifacts:pending?[]:['trace'],deletion_pending:pending}]};};
 mockCatalogFetch(w);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();panel.querySelector('[data-catalog-delete]').click();await wait();await wait();assert.equal(panel.querySelector('[data-catalog-artifact]'),null);assert.ok(panel.querySelector('[data-catalog-delete]'));assert.match(panel.textContent,/清理.*未完成|删除.*未完成/);
});
test('late catalog deletion response cannot affect a different signed-in user',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='5'.repeat(32);let resolve,deleteHeaders;w.confirm=()=>true;
 w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(options.method==='DELETE'){deleteHeaders=options.headers;return new Promise(r=>resolve=r);}return {ok:true,results:[{id,scenario:'ui_catalog_count',status:'passed',total:23,artifacts:['trace']}]};};
 mockCatalogFetch(w);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();panel.querySelector('[data-catalog-delete]').click();assert.equal(deleteHeaders.get('Authorization'),'Bearer fixture-session-token');w.currentAccessProfile={user_id:'different',scope:{ui_apps:'*'}};w.sessionStorage.setItem('sessionToken','new-session');w.resetUiSimpleWorkflow();resolve({ok:true,deleted:true});await wait();await wait();assert.equal(w.document.querySelector('[data-catalog-check]'),null);assert.doesNotMatch(w.document.body.textContent,/23|本条检查记录和证据已删除/);
});
test('scope changed history displays a safe placeholder with delete but no evidence',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='6'.repeat(32);
 w.apiRequest=async(path)=>path==='/ui-executions/availability'?{ok:true,enabled:true}:{ok:true,results:[{id,created:1730000000,scope_changed:true,artifacts:[]}]};
 mockCatalogFetch(w);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.match(panel.textContent,/权限范围已变化/);assert.ok(panel.querySelector('[data-catalog-delete]'));assert.equal(panel.querySelector('[data-catalog-artifact]'),null);
});
test('confirmed catalog deletion remains confirmed if history refresh fails',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='7'.repeat(32);let removed=false;w.confirm=()=>true;
 w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(options.method==='DELETE'){removed=true;return {ok:true,deleted:true};}if(removed)throw Object.assign(Error('offline'),{status:500});return {ok:true,results:[{id,scenario:'ui_catalog_count',status:'passed',total:3,artifacts:['trace']}]};};
 mockCatalogFetch(w);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();panel.querySelector('[data-catalog-delete]').click();await wait();await wait();assert.match(panel.textContent,/已删除.*刷新失败/);assert.equal(panel.querySelector('[data-catalog-artifact]'),null);
});
test('practice AI result follows the selected authorized task, not project latest',async t=>{
 const {w}=fixture(t),calls=[];w.canAccessWorkflow=()=>true;
 const task={id:'task-1',project_id:'project-1',environment_revision_id:'env-1',name:'反馈任务',selected_endpoint_ids:['ep-1'],latest_ai_job_id:'job-1'};
 w.apiRequest=async path=>{calls.push(path);if(path==='/api-testing/v1/projects')return {projects:[{id:'project-1',name:'业务'}]};if(path==='/api-testing/v1/tasks?project_id=project-1')return {tasks:[task]};if(path==='/api-testing/v1/ai-jobs/job-1')return {job:{id:'job-1',project_id:'project-1',environment_revision_id:'env-1',endpoint_ids:['ep-1'],state:'failed_gateway',batches:[{state:'failed_gateway',error_code:'gateway_timeout',generated_draft_ids:[]}]}};throw Error(path);};
 mockPracticeFetch(w);w.apiRequest=async()=>{throw Error('practice must use guarded fetch');};
 w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-practice-ai-results]');assert.ok(panel);panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 const project=panel.querySelector('[data-ai-project]');project.value='project-1';project.dispatchEvent(new w.Event('change'));await wait();await wait();const select=panel.querySelector('[data-ai-task]');select.value='task-1';select.dispatchEvent(new w.Event('change'));await wait();await wait();await wait();
 assert.match(panel.textContent,/超时/);assert.match(panel.textContent,/0\s*条/);assert.doesNotMatch(calls.join(' '),/ai-jobs\/latest/);assert.ok(calls.filter(x=>x.includes('/tasks?')).length>=2);
});
test('practice AI result discards an old account response',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let finish;const task={id:'task-1',project_id:'project-1',environment_revision_id:'env-1',name:'反馈任务',selected_endpoint_ids:['ep-1'],latest_ai_job_id:'job-1'};
 w.apiRequest=async path=>{if(path==='/api-testing/v1/projects')return {projects:[{id:'project-1',name:'业务'}]};if(path==='/api-testing/v1/tasks?project_id=project-1')return {tasks:[task]};if(path==='/api-testing/v1/ai-jobs/job-1')return new Promise(r=>finish=r);throw Error(path);};
 mockPracticeFetch(w);w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-practice-ai-results]');assert.ok(panel);panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();const project=panel.querySelector('[data-ai-project]');project.value='project-1';project.dispatchEvent(new w.Event('change'));await wait();await wait();const select=panel.querySelector('[data-ai-task]');select.value='task-1';select.dispatchEvent(new w.Event('change'));await wait();
 w.currentAccessProfile={user_id:'other',scope:{ui_apps:'*'}};finish({job:{id:'job-1',project_id:'project-1',environment_revision_id:'env-1',endpoint_ids:['ep-1'],state:'completed',batches:[]}});await wait();await wait();assert.doesNotMatch(panel.textContent,/生成完成/);
});
test('practice AI result refuses environment mismatch and a changed task association',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let mismatch=true,taskReads=0;const task={id:'task-1',project_id:'project-1',environment_revision_id:'env-1',name:'反馈任务',selected_endpoint_ids:['ep-1'],latest_ai_job_id:'job-1'};
 w.apiRequest=async path=>{if(path==='/api-testing/v1/projects')return {projects:[{id:'project-1',name:'业务'}]};if(path==='/api-testing/v1/tasks?project_id=project-1'){taskReads++;return {tasks:[!mismatch&&taskReads===2?{...task,latest_ai_job_id:'job-2'}:task]};}if(path==='/api-testing/v1/ai-jobs/job-1')return {job:{id:'job-1',project_id:'project-1',environment_revision_id:mismatch?'other-env':'env-1',endpoint_ids:['ep-1'],state:'completed',batches:[]}};throw Error(path);};
 mockPracticeFetch(w);w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-practice-ai-results]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();const project=panel.querySelector('[data-ai-project]');project.value='project-1';project.dispatchEvent(new w.Event('change'));await wait();await wait();const select=panel.querySelector('[data-ai-task]');select.value='task-1';select.dispatchEvent(new w.Event('change'));await wait();await wait();assert.doesNotMatch(panel.textContent,/生成完成/);assert.match(panel.textContent,/结果暂不可用/);
 mismatch=false;panel.querySelector('[data-ai-refresh]').click();await wait();await wait();project.value='project-1';project.dispatchEvent(new w.Event('change'));await wait();await wait();taskReads=0;select.value='task-1';select.dispatchEvent(new w.Event('change'));await wait();await wait();await wait();assert.doesNotMatch(panel.textContent,/生成完成/);assert.match(panel.textContent,/任务已变更/);
});
test('practice AI result is removed immediately on session reset',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-practice-ai-results]');panel.querySelector('[data-ai-result]').textContent='private result';w.resetUiSimpleWorkflow();assert.doesNotMatch(w.document.body.textContent,/private result/);
});
test('practice AI status hides old selections and raw error after authorization failure',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let denied=false,loggedOut=false;w.forceLogoutWithMessage=()=>loggedOut=true;
 w.nativeFetch=async()=>denied?{ok:false,status:401,json:async()=>({error:'private error'})}:{ok:true,status:200,json:async()=>({ok:true,data:{projects:[{id:'p1',name:'private project'}]}})};
 w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-practice-ai-results]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.match(panel.textContent,/private project/);denied=true;panel.querySelector('[data-ai-refresh]').click();await wait();await wait();assert.doesNotMatch(panel.textContent,/private project|private error/);assert.equal(loggedOut,false);
});
test('legacy records are absent from static page and loaded only after authorization',async t=>{
 const source=fs.readFileSync('js/ui-simple-entry.js','utf8');
 for(const id of ['0157b386-1475-4946-aae8-1664498ecc69','1eb0c640-d78a-41f9-b441-02785fd87e1c','43b09e5e-d879-47a2-bde5-2d462d48e988','6e5ae109-ba21-4434-9835-5d1e58d228ff','de803f8b-e18b-4e67-986d-b14ce737b8aa','0dfab6ed-b597-4c56-81bf-cc8d7dcb6342'])assert.doesNotMatch(source,new RegExp(id));
 assert.doesNotMatch(source,/GET \/print3d\/api\/v1\/feedback\/category\/first|The read operation timed out|30043 ms/);
 const {w}=fixture(t), id='11111111-1111-4111-8111-111111111111';let resolve;
 w.canAccessWorkflow=()=>true;w.apiRequest=path=>path==='/platform/practice-legacy-evidence'?new Promise(r=>{resolve=r;}):Promise.resolve({ok:true,results:[]});
 w.activeWorkflow='ai_practice';w.showAiPractice();const section=w.document.querySelector('[data-practice-legacy-evidence]');assert.ok(section);assert.doesNotMatch(section.textContent,/私有摘要/);
 section.open=true;section.dispatchEvent(new w.Event('toggle'));await wait();resolve({ok:true,records:[{key:'baseline',execution_id:id,summary:'私有摘要'}]});await wait();await wait();assert.match(section.textContent,/私有摘要/);const link=section.querySelector('a');assert.equal(link.getAttribute('href'),'/api-test/#/runs?executionId='+id);
 w.currentAccessProfile={user_id:'other',scope:{ui_apps:'*'}};assert.equal(link.dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true})),false);
 section.querySelector('button').click();w.currentAccessProfile={user_id:'third',scope:{ui_apps:'*'}};resolve({ok:true,records:[{key:'baseline',execution_id:id,summary:'旧账号摘要'}]});await wait();await wait();assert.doesNotMatch(section.textContent,/旧账号摘要|私有摘要/);
});
test('legacy evidence rejects same-account scope changes and clears on denied refresh',async t=>{
 const {w}=fixture(t),id='11111111-1111-4111-8111-111111111111';let resolve;
 w.canAccessWorkflow=()=>true;w.apiRequest=()=>new Promise(r=>{resolve=r;});w.activeWorkflow='ai_practice';w.showAiPractice();const section=w.document.querySelector('[data-practice-legacy-evidence]');section.open=true;section.dispatchEvent(new w.Event('toggle'));await wait();
 w.currentAccessProfile={user_id:'u1',scope:{ui_apps:'*',api_projects:['another']}};resolve({ok:true,records:[{key:'trace',execution_id:id,summary:'已撤销摘要'}]});await wait();await wait();assert.doesNotMatch(section.textContent,/已撤销摘要/);
 w.apiRequest=async()=>({ok:true,records:[{key:'trace',execution_id:id,summary:'受保护摘要'}]});section.querySelector('[data-legacy-refresh]').click();await wait();await wait();assert.match(section.textContent,/受保护摘要/);
 w.apiRequest=async()=>{throw Object.assign(Error('denied'),{status:403});};section.querySelector('[data-legacy-refresh]').click();await wait();await wait();assert.doesNotMatch(section.textContent,/受保护摘要/);assert.match(section.textContent,/无权|不可用/);
});
function fixture(t){
 const dom=new JSDOM('<div id="app"><details data-ui-advanced open></details><main id="editor-area"></main></div>',{url:'http://localhost/task-manager.html',runScripts:'dangerously',pretendToBeVisual:true});t.after(()=>dom.window.close());
 const w=dom.window,calls=[];w.currentAccessProfile={user_id:'u1',scope:{ui_apps:'*'}};w.activeWorkflow='dashboard';w.currentFile=null;
 w.apiRequest=async()=>({ok:true,enabled:true});w.hasPermission=()=>true;w.applyAccessNavigation=()=>{};w.toggleLibrary=()=>{};
 w.activateWorkflow=key=>{w.activeWorkflow=key;calls.push(key);};w.nativeFetch=async()=>({ok:true,headers:{get:()=> 'image/png'},blob:async()=>new w.Blob(['png'],{type:'image/png'})});w.Headers=Headers;w.sessionStorage.setItem('sessionToken','fixture-session-token');
 const utils=fs.readFileSync('js/utils.js','utf8');w.eval(utils.slice(utils.indexOf('function sessionToken()'),utils.indexOf('const WORKFLOW_SECTIONS')));
 w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>{};
 w.UiSimpleFlow={mount:(node,options)=>{calls.push(options);},reset:()=>calls.push('reset'),leave:()=>calls.push('leave'),refresh:()=>{}};
 w.eval(fs.readFileSync('js/ui-simple-entry.js','utf8'));
 return {w,calls};
}
test('practice workspace exposes real testing tasks without weekly plans or fake defect submission',async t=>{
 const {w,calls}=fixture(t);const requests=[];w.canAccessWorkflow=()=>true;w.apiRequest=async(path,options={})=>{requests.push([path,options.method||'GET']);return {ok:true,enabled:true,results:[]};};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();
 const page=w.document.querySelector('[data-practice-workbench]');assert.ok(page);assert.match(page.textContent,/开始 UI 测试/);assert.match(page.textContent,/API 测试/);assert.match(page.textContent,/我的测试/);assert.doesNotMatch(page.textContent,/第\s*[一二三123]\s*周|W0[123]|提单|提交缺陷/);assert.equal(page.querySelector('[data-practice-week]'),null);
 assert.equal(page.querySelector('[data-practice-api]').getAttribute('href'),'/api-test/');assert.ok(page.querySelector('[data-week2-real]'));assert.ok(page.querySelector('[data-week2-snapshot]'));assert.ok(page.querySelector('[data-week-probe]'));assert.ok(page.querySelector('[data-week-smoke]'));assert.equal(requests.filter(([path,method])=>path.startsWith('/platform/')&&method==='POST').length,0);
 page.querySelector('[data-practice-workflow="ui_start"]').click();assert.ok(calls.includes('ui_start'));
});

test('practice workspace keeps actual task links permission scoped and blocks stale identity navigation',async t=>{
 const {w,calls}=fixture(t);w.canAccessWorkflow=()=>false;w.hasPermission=p=>p==='platform.configure';w.activeWorkflow='ai_practice';w.showAiPractice();let page=w.document.querySelector('[data-practice-workbench]');assert.equal(page.querySelector('[data-practice-api]'),null);assert.ok(page.querySelector('[data-practice-workflow="ui_start"]').disabled);assert.ok(page.querySelector('[data-practice-workflow="ui_reports"]').disabled);
 w.hasPermission=()=>true;w.canAccessWorkflow=()=>true;w.showAiPractice();page=w.document.querySelector('[data-practice-workbench]');const link=page.querySelector('[data-practice-api]'),button=page.querySelector('[data-practice-workflow="ui_reports"]');w.currentAccessProfile={user_id:'other',scope:{ui_apps:'*'}};assert.equal(link.dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true})),false);button.click();assert.equal(calls.includes('ui_reports'),false);
});
test('practice evidence is fetched only on demand and does not execute business tests',async t=>{
 const {w}=fixture(t),requests=[];w.canAccessWorkflow=()=>true;const id='11111111-1111-4111-8111-111111111111';
 w.apiRequest=async(path,options={})=>{requests.push([path,options.method||'GET']);return path==='/platform/practice-legacy-evidence'?{ok:true,records:[{key:'cleanup',execution_id:id,summary:'已授权历史数据；关联资源与物理删除未验证。'}]}:{ok:true,enabled:true,results:[]};};
 w.activeWorkflow='ai_practice';w.showAiPractice();const snapshot=w.document.querySelector('[data-practice-acceptance]');assert.ok(snapshot);assert.equal(snapshot.open,false);assert.equal(requests.length,0);
 snapshot.open=true;snapshot.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.match(snapshot.textContent,/已授权历史数据/);assert.match(snapshot.textContent,/关联资源.*物理删除未验证/);assert.equal(snapshot.querySelector('a').getAttribute('href'),'/api-test/#/runs?executionId='+id);assert.deepEqual(requests,[['/platform/practice-legacy-evidence','GET']]);
});

test('practice evidence is hidden from limited users and stale links cannot be used',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='11111111-1111-4111-8111-111111111111';w.apiRequest=async()=>({ok:true,records:[{key:'cleanup',execution_id:id,summary:'受限记录'}]});w.activeWorkflow='ai_practice';w.showAiPractice();const snapshot=w.document.querySelector('[data-practice-acceptance]');snapshot.open=true;snapshot.dispatchEvent(new w.Event('toggle'));await wait();await wait();const link=snapshot.querySelector('a');assert.ok(link);
 w.currentAccessProfile={user_id:'other',scope:{ui_apps:'*'}};assert.equal(link.dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true})),false);
 w.currentAccessProfile={user_id:'u1',scope:{ui_apps:'*'}};w.hasPermission=p=>p!=='api.view';assert.equal(link.dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true})),false);w.hasPermission=()=>true;
 w.currentAccessProfile={user_id:'u1',scope:{ui_apps:['one']}};w.showAiPractice();assert.equal(w.document.querySelector('[data-practice-acceptance]'),null);
 w.currentAccessProfile={user_id:'u1',scope:{ui_apps:'*'}};w.hasPermission=p=>p==='api.view';w.showAiPractice();assert.equal(w.document.querySelector('[data-practice-acceptance]'),null);
});

test('practice cleanup evidence loads only authorized summary, links safely and makes no business request',async t=>{
 const {w}=fixture(t),requests=[];w.canAccessWorkflow=()=>true;
 const id='11111111-1111-4111-8111-111111111111';
 w.apiRequest=async(path,options={})=>{requests.push([path,options.method||'GET']);return path==='/platform/practice-cleanup-evidence'?{ok:true,evidence:{execution_id:id,observed_at:'历史时间',summary:'测试摘要 <img src=x>'}}:{ok:true,enabled:true,results:[]};};
 w.activeWorkflow='ai_practice';w.showAiPractice();const section=w.document.querySelector('[data-practice-cleanup-evidence]');assert.ok(section);assert.equal(section.open,false);assert.equal(requests.length,0);
 section.open=true;section.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.match(section.textContent,/测试摘要 <img src=x>/);assert.equal(section.querySelector('img'),null);
 const link=section.querySelector('a');assert.equal(link.getAttribute('href'),'/api-test/#/runs?executionId='+id);assert.deepEqual(requests.map(([p,m])=>[p,m]),[['/platform/practice-cleanup-evidence','GET']]);
 w.currentAccessProfile={user_id:'other',scope:{ui_apps:'*'}};assert.equal(link.dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true})),false);
});
test('practice cleanup evidence clears results on denied refresh and ignores responses after scope or identity changes',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let response={ok:true,evidence:{execution_id:'11111111-1111-4111-8111-111111111111',observed_at:'时间',summary:'私有摘要'}};
 w.apiRequest=async()=>response;w.activeWorkflow='ai_practice';w.showAiPractice();let section=w.document.querySelector('[data-practice-cleanup-evidence]');section.open=true;section.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.match(section.textContent,/私有摘要/);
 w.apiRequest=async()=>{throw Object.assign(Error('denied'),{status:403});};section.querySelector('button').click();await wait();await wait();assert.doesNotMatch(section.textContent,/私有摘要/);assert.match(section.textContent,/暂不可用|无权/);
 let resolve;w.apiRequest=()=>new Promise(r=>{resolve=r;});section.querySelector('button').click();w.currentAccessProfile={user_id:'u1',scope:{ui_apps:'*',api_projects:['other']}};resolve(response);await wait();await wait();assert.doesNotMatch(section.textContent,/私有摘要/);
 section.querySelector('button').click();w.currentAccessProfile={user_id:'other',scope:{ui_apps:'*'}};resolve(response);await wait();await wait();assert.doesNotMatch(section.textContent,/私有摘要/);
});
test('practice cleanup evidence gives a clear message when the source is absent',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;w.apiRequest=async()=>({ok:true,evidence:null});w.activeWorkflow='ai_practice';w.showAiPractice();const section=w.document.querySelector('[data-practice-cleanup-evidence]');section.open=true;section.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.match(section.textContent,/未配置|不可用/);assert.equal(section.querySelector('a'),null);
});
test('authentication refresh immediately removes a previously visible cleanup result',async t=>{
 const {w}=fixture(t);const auth=fs.readFileSync('js/auth.js','utf8');w.eval(auth.slice(auth.indexOf('function acceptAuthSession('),auth.indexOf('function requireUiEditPermission(')));w.eval(auth.slice(auth.indexOf('const WORKFLOW_PERMISSIONS'),auth.indexOf('function applyAccessNavigation')));
 const admin={user_id:'u1',status:'active',permissions:['platform.configure','api.view','ui.view'],scope:{ui_apps:'*',api_projects:'*',api_environments:'*'}};
 w.currentAccessProfile=admin;w.apiRequest=async path=>path==='/platform/practice-cleanup-evidence'?{ok:true,evidence:{execution_id:'11111111-1111-4111-8111-111111111111',summary:'受保护记录'}}:{ok:true,enabled:true};w.activeWorkflow='ai_practice';w.showAiPractice();const section=w.document.querySelector('[data-practice-cleanup-evidence]');section.open=true;section.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.match(section.textContent,/受保护记录/);
 w.acceptAuthSession({profile:{...admin,scope:{ui_apps:'*',api_projects:['another'],api_environments:[]}}});assert.doesNotMatch(w.document.querySelector('[data-practice-workbench]').textContent,/受保护记录/);
});
test('feedback analysis defers business cleanup evidence to authorized records',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;w.apiRequest=async path=>path==='/platform/week2-real'?{ok:true,results:[{id:'sample',status:'blocked',created_at:'2026-11-06',source:'platform_record',source_verification:'server_authorized_platform_read',business_requests:0}]}:{ok:true,enabled:true};
 w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week2-real]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.match(panel.textContent,/写入与清理结果.*当前账号.*执行记录/);assert.doesNotMatch(panel.textContent,/写入及清理仅完成单次验证/);
});

test('release testing page does not contain the learning guide or probe',async t=>{
 const {w}=fixture(t);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();
 assert.equal(w.document.querySelector('[data-ui-week-one]'),null);
 assert.equal(w.document.querySelector('[data-week-probe]'),null);
});
test('practice page is available without simple UI feature and denies missing platform permission',async t=>{
 const {w}=fixture(t);w.activeWorkflow='ai_practice';w.apiRequest=async()=>({ok:true,enabled:false});await w.initUiSimpleAvailability({navigate:false});
 assert.equal(w.showAiPractice(),true);assert.ok(w.document.querySelector('[data-ui-week-one]'));assert.ok(w.document.querySelector('[data-week-probe]'));
 w.hasPermission=()=>false;w.document.getElementById('editor-area').replaceChildren();assert.equal(w.showAiPractice(),false);assert.equal(w.document.querySelector('[data-ui-week-one]'),null);
});
test('practice navigation accepts view permissions even if simple UI is disabled',async t=>{
 const {w}=fixture(t);const auth=fs.readFileSync('js/auth.js','utf8');
 w.eval(auth.slice(auth.indexOf('const WORKFLOW_PERMISSIONS'),auth.indexOf('function applyAccessNavigation')));
 w.hasPermission=p=>p==='ui.view';assert.equal(w.isUiSimpleAvailable(),false);assert.equal(w.canAccessWorkflow('ai_practice'),true);
 w.hasPermission=p=>p==='platform.configure';assert.equal(w.canAccessWorkflow('ai_practice'),true);
});
test('restored practice workflow renders the independent guide',async t=>{
 const {w}=fixture(t);w.activeWorkflow='ai_practice';
 const shell=fs.readFileSync('js/agent-status.js','utf8');
 w.eval(shell.slice(shell.indexOf('function renderActiveWorkflowPage('),shell.indexOf('\nfunction updateWorkbenchPanelMode(',shell.indexOf('function renderActiveWorkflowPage('))));
 assert.equal(w.renderActiveWorkflowPage(),true);assert.ok(w.document.querySelector('[data-ui-week-one]'));
});
test('feature on exposes simple home while feature off preserves advanced navigation',async t=>{
 const {w,calls}=fixture(t);await w.initUiSimpleAvailability();assert.equal(w.isUiSimpleAvailable(),true);assert.ok(calls.includes('ui_start'));assert.equal(w.document.querySelector('details').open,false);
 w.resetUiSimpleWorkflow();w.apiRequest=async()=>({ok:true,enabled:false});await w.initUiSimpleAvailability();assert.equal(w.isUiSimpleAvailable(),false);assert.equal(w.document.querySelector('details').open,true);
});
test('availability response from previous account cannot expose new workflow',async t=>{
 const {w,calls}=fixture(t);let resolve;w.apiRequest=()=>new Promise(r=>resolve=r);const load=w.initUiSimpleAvailability();
 w.currentAccessProfile={user_id:'u2'};w.resetUiSimpleWorkflow();resolve({ok:true,enabled:true});await load;assert.equal(w.isUiSimpleAvailable(),false);assert.equal(calls.includes('ui_start'),false);
});
test('availability cannot navigate away from a workflow user opened while request was pending',async t=>{
 const {w,calls}=fixture(t);let resolve;w.apiRequest=()=>new Promise(r=>resolve=r);const load=w.initUiSimpleAvailability();w.activeWorkflow='config';resolve({ok:true,enabled:true});await load;assert.equal(calls.includes('ui_start'),false);
});
test('phone image from stale account is never attached and request goes only to trusted snapshot route',async t=>{
 const {w,calls}=fixture(t);await w.initUiSimpleAvailability();w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();const options=calls.find(x=>x?.loadPicture);
 let resolve;w.nativeFetch=()=>new Promise(r=>resolve=r);const img=w.document.createElement('img');w.document.body.append(img);
 const picture=options.loadPicture(img,{target:{runner_id:'r',device_id:'d'},snapshot_url:'https://untrusted.example/image',snapshot_captured_at:'1'},()=>true);await picture;assert.equal(img.hasAttribute('src'),false);
 const pending=options.loadPicture(img,{app_id:'app.a',target:{runner_id:'r',device_id:'d'},snapshot_url:'/api/runner/device-snapshot?runner_id=r&device_id=d&app_id=app.a',snapshot_captured_at:'1'},()=>true);
 w.currentAccessProfile={user_id:'u2'};w.resetUiSimpleWorkflow();resolve({ok:true,headers:{get:()=> 'image/png'},blob:async()=>new w.Blob(['png'],{type:'image/png'})});await pending;assert.equal(img.hasAttribute('src'),false);
});
test('phone image URL must carry the selected row app',async t=>{
 const {w,calls}=fixture(t);await w.initUiSimpleAvailability();w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();const options=calls.find(x=>x?.loadPicture);
 let fetched=0;w.nativeFetch=async()=>{fetched++;return {ok:true,headers:{get:()=> 'image/png'},blob:async()=>new w.Blob(['png'],{type:'image/png'})};};
 const img=w.document.createElement('img');w.document.body.append(img);
 await options.loadPicture(img,{app_id:'app.a',target:{runner_id:'r',device_id:'d'},snapshot_url:'/api/runner/device-snapshot?runner_id=r&device_id=d&app_id=app.b',snapshot_captured_at:'1'},()=>true);
 assert.equal(fetched,0);assert.equal(img.hasAttribute('src'),false);
});
test('forced phone picture refresh discards cached bytes and sends a new authorized request',async t=>{
 const {w,calls}=fixture(t);await w.initUiSimpleAvailability();w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();const options=calls.find(x=>x?.loadPicture);
 let fetched=0,created=0;const revoked=[];w.nativeFetch=async()=>{fetched++;return {ok:true,headers:{get:()=> 'image/png'},blob:async()=>new w.Blob(['png'],{type:'image/png'})};};
 w.URL.createObjectURL=()=>`blob:picture-${++created}`;w.URL.revokeObjectURL=value=>revoked.push(value);
 const img=w.document.createElement('img');w.document.body.append(img);const device={app_id:'app.a',target:{runner_id:'r',device_id:'d'},snapshot_url:'/api/runner/device-snapshot?runner_id=r&device_id=d&app_id=app.a',snapshot_captured_at:'1'};
 await options.loadPicture(img,device,()=>true);await options.loadPicture(img,device,()=>true);assert.equal(fetched,1);
 await options.loadPicture(img,device,()=>true,{forceFresh:true});assert.equal(fetched,2);assert.ok(revoked.includes('blob:picture-1'));assert.equal(img.src,'blob:picture-2');
});
test('refreshing one picture does not revoke an independently visible card for the same snapshot',async t=>{
 const {w,calls}=fixture(t);await w.initUiSimpleAvailability();w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();const loader=calls.find(x=>x?.loadPicture).loadPicture;
 let created=0;const revoked=[];w.URL.createObjectURL=()=>`blob:separate-${++created}`;w.URL.revokeObjectURL=value=>revoked.push(value);
 const first=w.document.createElement('img'),second=w.document.createElement('img');w.document.body.append(first,second);
 const device={app_id:'app.a',target:{runner_id:'r',device_id:'d'},snapshot_url:'/api/runner/device-snapshot?runner_id=r&device_id=d&app_id=app.a',snapshot_captured_at:'1'};
 await loader(first,device,()=>true);await loader(second,device,()=>true,{forceFresh:true});
 assert.notEqual(first.src,second.src);assert.equal(first.hidden,false);assert.equal(revoked.includes(first.src),false);
});
test('releasing a phone image leaves an unrelated practice screenshot available',async t=>{
 const {w,calls}=fixture(t);w.canAccessWorkflow=()=>true;const id='c'.repeat(32),revoked=[];let created=0;
 w.apiRequest=async(path,options={})=>path==='/ui-executions/availability'?{ok:true,enabled:true}:options.method==='POST'?{ok:true,result:{id,scenario:'ui_catalog_count',status:'passed',total:1,artifacts:['screenshot']}}:{ok:true,results:[]};
 mockCatalogFetch(w,async()=>({ok:true,status:200,headers:{get:key=>key==='content-type'?'image/png':key==='content-length'?'3':null},blob:async()=>new w.Blob(['png'])}));
 w.URL.createObjectURL=()=>`blob:separate-${++created}`;w.URL.revokeObjectURL=url=>revoked.push(url);
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();const options=calls.find(x=>x?.loadPicture);
 w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-catalog-check]');panel.querySelector('[data-catalog-run]').click();await wait();await wait();panel.querySelector('[data-catalog-artifact="screenshot"]').click();await wait();await wait();
 const practice=panel.querySelector('[data-catalog-preview]');assert.ok(practice);const practiceSrc=practice.src;
 const phone=w.document.createElement('img');w.document.body.append(phone);const device={app_id:'app.a',target:{runner_id:'r',device_id:'d'},snapshot_url:'/api/runner/device-snapshot?runner_id=r&device_id=d&app_id=app.a',snapshot_captured_at:'1'};
 await options.loadPicture(phone,device,()=>true);const phoneSrc=phone.src;options.releasePicture(phone);
 assert.ok(revoked.includes(phoneSrc));assert.equal(revoked.includes(practiceSrc),false);assert.equal(practice.src,practiceSrc);
});
test('unselected phone removed and restored must reauthorize its cached image without disturbing another phone',async t=>{
 const {w,calls}=fixture(t);await w.initUiSimpleAvailability();w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();const entry=calls.find(x=>x?.loadPicture),loader=entry.loadPicture;
 const makeDevice=id=>({app_id:'app.a',target:{runner_id:'r',device_id:id,platform:'android'},name:'手机',connection_status:'ready',snapshot_url:`/api/runner/device-snapshot?runner_id=r&device_id=${id}&app_id=app.a`,snapshot_captured_at:'1'});
 const a=makeDevice('a'),b=makeDevice('b');let devices=[a,b],denyA=false,created=0;const requests=[],revoked=[];
 w.nativeFetch=async path=>{requests.push(path);return denyA&&path.includes('device_id=a')?{ok:false,status:403,headers:{get:()=>null}}:{ok:true,headers:{get:()=> 'image/png'},blob:async()=>new w.Blob(['png'])};};
 w.URL.createObjectURL=()=>`blob:pic-${++created}`;w.URL.revokeObjectURL=url=>revoked.push(url);
 w.eval(fs.readFileSync('js/ui-test-home.js','utf8'));const host=w.document.getElementById('editor-area');
 const request=async path=>path.startsWith('/ui-executions/devices')?{ok:true,devices}:{ok:true,apps:[{app_id:'app.a',name:'应用甲'}],modules:[],items:[],groups:[],total:0};
 w.UiSimpleFlow.mount(host,{mode:'start',request,identity:()=> 'u1',canExecute:()=>true,loadPicture:loader,releasePicture:entry.releasePicture});await wait();const app=host.querySelector('[name="ui-app"]');app.value='app.a';app.dispatchEvent(new w.Event('change'));await wait();await wait();
 const cards=host.querySelectorAll('.ui-simple-device'),oldA=cards[0].querySelector('img'),oldASrc=oldA.src,other=cards[1].querySelector('img'),otherSrc=other.src;assert.equal(requests.length,2);assert.equal(oldA.hidden,false);
 devices=[b];await w.UiSimpleFlow.refresh();assert.equal(oldA.isConnected,false);assert.ok(revoked.includes(oldASrc));assert.equal(revoked.includes(otherSrc),false);
 denyA=true;devices=[a,b];await w.UiSimpleFlow.refresh();await wait();const newA=[...host.querySelectorAll('.ui-simple-device')].find(card=>card.textContent.includes('设备编号：a')).querySelector('img');
 assert.equal(requests.filter(p=>p.includes('device_id=a')).length,2);assert.equal(newA.hidden,true);assert.equal(newA.hasAttribute('src'),false);assert.equal(other.src,otherSrc);assert.equal(revoked.includes(otherSrc),false);
});
test('selected phone disappearance revokes only its image and rejects a denied restore',async t=>{
 const {w,calls}=fixture(t);await w.initUiSimpleAvailability();w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();const options=calls.find(x=>x?.loadPicture);
 const device={app_id:'app.a',target:{runner_id:'r',device_id:'d',platform:'android'},name:'手机',connection_status:'ready',snapshot_url:'/api/runner/device-snapshot?runner_id=r&device_id=d&app_id=app.a',snapshot_captured_at:'1'};
 let devices=[device],denied=false,requests=0;const revoked=[];w.URL.createObjectURL=()=> 'blob:selected-phone';w.URL.revokeObjectURL=url=>revoked.push(url);
 w.nativeFetch=async()=>{requests++;return denied?{ok:false,status:403,headers:{get:()=>null}}:{ok:true,headers:{get:()=> 'image/png'},blob:async()=>new w.Blob(['png'])};};
 w.eval(fs.readFileSync('js/ui-test-home.js','utf8'));const host=w.document.getElementById('editor-area');const request=async path=>path.startsWith('/ui-executions/devices')?{ok:true,devices}:{ok:true,apps:[{app_id:'app.a',name:'应用甲'}],modules:[],items:[],groups:[],total:0};
 w.UiSimpleFlow.mount(host,{mode:'start',request,identity:()=> 'u1',canExecute:()=>true,loadPicture:options.loadPicture,releasePicture:options.releasePicture});await wait();const app=host.querySelector('[name="ui-app"]');app.value='app.a';app.dispatchEvent(new w.Event('change'));await wait();await wait();
 const card=host.querySelector('.ui-simple-device'),img=card.querySelector('img');card.querySelector('input').click();assert.equal(img.src,'blob:selected-phone');
 devices=[];await w.UiSimpleFlow.refresh();assert.ok(revoked.includes('blob:selected-phone'));assert.equal(img.hidden,true);assert.equal(card.querySelector('input').checked,true);assert.equal(card.querySelector('input').disabled,true);
 denied=true;devices=[device];await w.UiSimpleFlow.refresh();await wait();assert.equal(requests,2);assert.equal(img.hidden,true);assert.equal(img.hasAttribute('src'),false);
});
test('snapshot URL withdrawal and application switch release the old image for the affected card',async t=>{
 const {w,calls}=fixture(t);await w.initUiSimpleAvailability();w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();const options=calls.find(x=>x?.loadPicture);
 const device={app_id:'app.a',target:{runner_id:'r',device_id:'d',platform:'android'},name:'手机',connection_status:'ready',snapshot_url:'/api/runner/device-snapshot?runner_id=r&device_id=d&app_id=app.a',snapshot_captured_at:'1'};
 let devices=[device],created=0;const revoked=[];w.URL.createObjectURL=()=>`blob:switch-${++created}`;w.URL.revokeObjectURL=url=>revoked.push(url);
 w.nativeFetch=async()=>({ok:true,headers:{get:()=> 'image/png'},blob:async()=>new w.Blob(['png'])});
 w.eval(fs.readFileSync('js/ui-test-home.js','utf8'));const host=w.document.getElementById('editor-area');const request=async path=>path.startsWith('/ui-executions/devices')?{ok:true,devices}:{ok:true,apps:[{app_id:'app.a',name:'应用甲'},{app_id:'app.b',name:'应用乙'}],modules:[],items:[],groups:[],total:0};
 w.UiSimpleFlow.mount(host,{mode:'start',request,identity:()=> 'u1',canExecute:()=>true,loadPicture:options.loadPicture,releasePicture:options.releasePicture});await wait();const app=host.querySelector('[name="ui-app"]');app.value='app.a';app.dispatchEvent(new w.Event('change'));await wait();await wait();
 const img=host.querySelector('.ui-simple-device img');assert.equal(img.src,'blob:switch-1');devices=[{...device,snapshot_url:''}];await w.UiSimpleFlow.refresh();assert.ok(revoked.includes('blob:switch-1'));assert.equal(img.hidden,true);
 devices=[device];await w.UiSimpleFlow.refresh();await wait();assert.equal(img.src,'blob:switch-2');devices=[];app.value='app.b';app.dispatchEvent(new w.Event('change'));await wait();assert.ok(revoked.includes('blob:switch-2'));assert.equal(img.isConnected,false);
});
test('old unselected picture response cannot attach after card removal and denied reauthorization',async t=>{
 const {w,calls}=fixture(t);await w.initUiSimpleAvailability();w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();const loader=calls.find(x=>x?.loadPicture).loadPicture;
 const device={app_id:'app.a',target:{runner_id:'r',device_id:'d',platform:'android'},name:'手机',connection_status:'ready',snapshot_url:'/api/runner/device-snapshot?runner_id=r&device_id=d&app_id=app.a',snapshot_captured_at:'1'};
 let devices=[device],requests=0,resolveOld,created=0;w.URL.createObjectURL=()=>`blob:pic-${++created}`;
 w.nativeFetch=async()=>{requests++;if(requests===1)return new Promise(r=>resolveOld=r);return {ok:false,status:403,headers:{get:()=>null}};};
 w.eval(fs.readFileSync('js/ui-test-home.js','utf8'));const host=w.document.getElementById('editor-area');const request=async path=>path.startsWith('/ui-executions/devices')?{ok:true,devices}:{ok:true,apps:[{app_id:'app.a',name:'应用甲'}],modules:[],items:[],groups:[],total:0};
 w.UiSimpleFlow.mount(host,{mode:'start',request,identity:()=> 'u1',canExecute:()=>true,loadPicture:loader});await wait();const app=host.querySelector('[name="ui-app"]');app.value='app.a';app.dispatchEvent(new w.Event('change'));await wait();assert.equal(requests,1);
 const old=host.querySelector('.ui-simple-device img');devices=[];await w.UiSimpleFlow.refresh();devices=[device];await w.UiSimpleFlow.refresh();await wait();assert.equal(requests,2);
 resolveOld({ok:true,headers:{get:()=> 'image/png'},blob:async()=>new w.Blob(['png'])});await wait();await wait();const current=host.querySelector('.ui-simple-device img');
 assert.equal(old.isConnected,false);assert.equal(current.hidden,true);assert.equal(current.hasAttribute('src'),false);assert.equal(created,0);
});

test('report download cannot save a stale account response or fetch outside private batch routes',async t=>{
 const {w}=fixture(t);w.responseAttachmentFilename=()=> '智小白3D发布测试报告.docx';
 let resolve;w.nativeFetch=()=>new Promise(r=>resolve=r);const downloads=[];w.HTMLAnchorElement.prototype.click=function(){downloads.push(this.download);};
 await assert.rejects(()=>w.downloadUiBatchFile('/reports/public.html'),/下载地址/);
 const pending=w.downloadUiBatchFile('/ui-executions/uib_example/report/download?format=docx&revision='+'a'.repeat(64));
 w.currentAccessProfile={user_id:'u2'};w.resetUiSimpleWorkflow();resolve({ok:true,status:200,headers:{get:key=>key==='content-type'?'application/vnd.openxmlformats-officedocument.wordprocessingml.document':null},blob:async()=>new w.Blob(['word'])});
 await assert.rejects(pending,/取消/);assert.deepEqual(downloads,[]);
});

test('private Word export keeps server Chinese filename and real extension',async t=>{
 const {w}=fixture(t);w.responseAttachmentFilename=()=> '智小白3D发布测试报告.docx';const downloads=[];w.HTMLAnchorElement.prototype.click=function(){downloads.push(this.download);};
 w.nativeFetch=async()=>({ok:true,status:200,headers:{get:key=>key==='content-type'?'application/vnd.openxmlformats-officedocument.wordprocessingml.document':null},blob:async()=>new w.Blob(['word'])});
 assert.equal(await w.downloadUiBatchFile('/ui-executions/uib_example/report/download?format=docx&revision='+'a'.repeat(64)),'智小白3D发布测试报告.docx');
 assert.deepEqual(downloads,['智小白3D发布测试报告.docx']);
});

test('internal report link survives anonymous availability and opens exact revision after login',async t=>{
 const {w,calls}=fixture(t),batch='uib_'+'b'.repeat(32),revision='c'.repeat(64);
 w.history.replaceState({},'',`/task-manager.html?ui_batch=${batch}&report_revision=${revision}`);
 w.currentAccessProfile=null;await w.initUiSimpleAvailability({navigate:false});assert.equal(w.uiSimpleReportIntent()?.batchId,batch);
 w.currentAccessProfile={user_id:'u1'};await w.initUiSimpleAvailability({navigate:false});
 assert.equal(w.preferUiSimpleReport(),true);assert.equal(w.activeWorkflow,'ui_reports');w.showUiSimpleWorkflow();
 const options=calls.find(x=>x?.reportIntent);assert.equal(options.reportIntent.batchId,batch);assert.equal(options.reportIntent.revision,revision);
 assert.equal(w.uiSimpleReportIntent().batchId,batch);assert.equal(w.uiSimpleReportIntent().revision,revision);
 assert.equal(options.canNotify(),true);
});
test('report return accepts only one exact internal batch revision and never a generic external redirect',async t=>{
 const {w}=fixture(t);for(const search of ['?ui_batch=https://evil.example&report_revision='+'a'.repeat(64),'?return_to=https://evil.example','?ui_batch=uib_'+'a'.repeat(32)+'&report_revision=latest','?ui_batch=uib_'+'a'.repeat(32)+'&ui_batch=uib_'+'b'.repeat(32)+'&report_revision='+'a'.repeat(64)]){
  w.history.replaceState({},'','/task-manager.html'+search);assert.equal(w.uiSimpleReportIntent(),null);
 }
});

test('authenticated shell prioritizes exact notification report over saved workflow',async t=>{
 const {w,calls}=fixture(t),batch='uib_'+'b'.repeat(32),revision='c'.repeat(64);
 w.history.replaceState({},'',`/task-manager.html?ui_batch=${batch}&report_revision=${revision}`);
 for(const id of ['login-screen','toolbar-help']){const node=w.document.createElement('div');node.id=id;w.document.body.append(node);}
 w.clearTransientAuthFeedback=()=>{};w.restoreWorkflowPreference=()=>{w.activeWorkflow='config';};w.canAccessWorkflow=()=>true;
 w.WORKFLOW_SECTIONS={ui_reports:{help:'本人报告'}};w.renderWorkflowNav=()=>{};w.updateContextToolbar=()=>{};
 w.renderActiveWorkflowPage=()=>w.showUiSimpleWorkflow();
 const auth=fs.readFileSync('js/auth.js','utf8');w.eval(auth.slice(auth.indexOf('async function showAuthedApp()'),auth.indexOf('function loginReturnToPath()')));
 await w.showAuthedApp();assert.equal(w.activeWorkflow,'ui_reports');assert.equal(w.document.getElementById('app').style.display,'flex');
 assert.equal(calls.find(x=>x?.reportIntent).reportIntent.revision,revision);
});


for(const failure of [401,403,404])test(`exact report link survives asynchronous HTTP ${failure} then owner sign-in`,async t=>{
 const {w}=fixture(t),batch='uib_'+'d'.repeat(32),revision='e'.repeat(64),search=`?ui_batch=${batch}&report_revision=${revision}&keep=1#retained`;
 w.history.replaceState({},'','/task-manager.html'+search);
 w.UiBatchView={update:()=>{}};w.eval(fs.readFileSync('js/ui-batch-report.js','utf8'));w.eval(fs.readFileSync('js/ui-test-home.js','utf8'));
 let rejectReport,resolveOwner;const reportRequests=[];
 w.apiRequest=async(path)=>{
  if(path==='/ui-executions/availability')return {ok:true,enabled:true};
  if(path.includes('/report?')){reportRequests.push({identity:w.currentAccessProfile.user_id,path});return new Promise((resolve,reject)=>{if(w.currentAccessProfile.user_id==='u1')rejectReport=reject;else resolveOwner=resolve;});}
  if(path.includes('/deliveries'))return {ok:true,policy:{enabled:false},items:[]};
  return {ok:true,apps:[],modules:[],items:[],next_cursor:null};
 };
 await w.initUiSimpleAvailability({navigate:false});w.preferUiSimpleReport();w.showUiSimpleWorkflow();
 assert.equal(reportRequests.length,1);assert.equal(w.location.search+w.location.hash,search);
 rejectReport(Object.assign(new Error('会话或账号不能读取'),{status:failure}));await wait();await wait();
 assert.match(w.document.querySelector('[data-report-state]').textContent,/无法访问/);assert.equal(w.location.search+w.location.hash,search);
 w.resetUiSimpleWorkflow();w.currentAccessProfile=null;
 await w.initUiSimpleAvailability({navigate:false});assert.equal(w.uiSimpleReportIntent().batchId,batch);
 w.currentAccessProfile={user_id:'owner'};await w.initUiSimpleAvailability({navigate:false});assert.equal(w.preferUiSimpleReport(),true);w.showUiSimpleWorkflow();
 assert.equal(reportRequests[1].identity,'owner');assert.ok(reportRequests[1].path.endsWith('/report?revision='+revision));
 resolveOwner({ok:true,report:{status:'ready',report_revision:revision,snapshot:{owner_display_name:'所属账号',summary:'历史版本摘要',artifacts:[]}}});await wait();await wait();
 assert.match(w.document.querySelector('[data-report-state]').textContent,/已就绪/);assert.match(w.document.querySelector('[data-report-owner]').textContent,/所属账号/);
 assert.equal(w.location.search+w.location.hash,search);
 // Same-account refresh remount also reopens this exact report. A mount is not authorization.
 w.showUiSimpleWorkflow();assert.equal(reportRequests.length,3);assert.equal(w.uiSimpleReportIntent().revision,revision);
 resolveOwner({ok:true,report:{status:'ready',report_revision:revision,snapshot:{owner_display_name:'所属账号',summary:'历史版本摘要',artifacts:[]}}});await wait();await wait();
 w.document.querySelector('[data-report-close]').click();assert.equal(w.uiSimpleReportIntent(),null);assert.equal(w.location.search+w.location.hash,'?keep=1#retained');
});

test('account switch during a pending report cannot erase the retained intent or paint stale content',async t=>{
 const {w}=fixture(t),batch='uib_'+'a'.repeat(32),revision='b'.repeat(64);
 w.history.replaceState({},'',`/task-manager.html?ui_batch=${batch}&report_revision=${revision}`);
 w.UiBatchView={update:()=>{}};w.eval(fs.readFileSync('js/ui-batch-report.js','utf8'));w.eval(fs.readFileSync('js/ui-test-home.js','utf8'));
 const pending=[];w.apiRequest=async path=>path==='/ui-executions/availability'?{ok:true,enabled:true}:path.includes('/report?')?new Promise(resolve=>pending.push(resolve)):{ok:true,apps:[],modules:[],items:[],policy:{enabled:false}};
 await w.initUiSimpleAvailability({navigate:false});w.preferUiSimpleReport();w.showUiSimpleWorkflow();
 const staleClose=w.document.querySelector('[data-report-close]');
 w.resetUiSimpleWorkflow();w.currentAccessProfile={user_id:'u2'};await w.initUiSimpleAvailability({navigate:false});assert.equal(w.preferUiSimpleReport(),true);w.showUiSimpleWorkflow();
 staleClose.click();assert.equal(w.uiSimpleReportIntent().revision,revision);
 pending[0]({ok:true,report:{status:'ready',report_revision:revision,snapshot:{owner_display_name:'旧账号私有内容',summary:'old',artifacts:[]}}});await wait();
 assert.doesNotMatch(w.document.getElementById('editor-area').textContent,/旧账号私有内容/);assert.equal(w.uiSimpleReportIntent().revision,revision);
 pending[1]({ok:true,report:{status:'ready',report_revision:revision,snapshot:{owner_display_name:'新账号',summary:'new',artifacts:[]}}});await wait();await wait();
 assert.equal(w.uiSimpleReportIntent().revision,revision);
});

test('explicit workflow departure clears only internal report intent while auth reset retains it',async t=>{
 const {w}=fixture(t),batch='uib_'+'f'.repeat(32),revision='a'.repeat(64);
 w.history.replaceState({},'',`/task-manager.html?ui_batch=${batch}&report_revision=${revision}&keep=1#section`);
 await w.initUiSimpleAvailability({navigate:false});w.preferUiSimpleReport();w.showUiSimpleWorkflow();w.resetUiSimpleWorkflow();assert.equal(w.uiSimpleReportIntent().batchId,batch);
 await w.initUiSimpleAvailability({navigate:false});w.preferUiSimpleReport();w.showUiSimpleWorkflow();
 const shell=fs.readFileSync('js/agent-status.js','utf8');
 w.clearManagementSearchTimers=()=>{};w.normalizeWorkflowKey=v=>v;w.WORKFLOW_SECTIONS={ui_reports:{help:'报告'},ui_start:{help:'开始'}};w.resetWorkflowScrollPosition=()=>{};w.updateWorkbenchPanelMode=()=>{};w.renderWorkflowNav=()=>{};w.updateContextToolbar=()=>{};
 w.eval(shell.slice(shell.indexOf('function setActiveWorkflow('),shell.indexOf('function clearManagementSearchTimers(')));
 w.setActiveWorkflow('ui_reports');assert.equal(w.uiSimpleReportIntent().revision,revision);
 w.setActiveWorkflow('ui_start');assert.equal(w.uiSimpleReportIntent(),null);assert.equal(w.location.search+w.location.hash,'?keep=1#section');
});


test('rollback keeps exact report links and authenticated My Reports reachable with admission closed',async t=>{
 const {w,calls}=fixture(t),batch='uib_'+'a'.repeat(32),revision='b'.repeat(64);
 w.history.replaceState({},'',`/task-manager.html?ui_batch=${batch}&report_revision=${revision}`);
 w.apiRequest=async()=>({ok:true,enabled:false,evidence_available:true,recovery_available:true});
 await w.initUiSimpleAvailability({navigate:false});assert.equal(w.isUiSimpleAvailable('ui_reports'),true);
 assert.equal(w.isUiSimpleAvailable('ui_start'),false);assert.equal(w.preferUiSimpleReport(),true);assert.equal(w.showUiSimpleWorkflow(),true);
 const options=calls.find(x=>x?.reportIntent);assert.equal(options.reportIntent.revision,revision);assert.equal(options.canAdmit(),false);assert.equal(options.canExecute(),true);
 w.resetUiSimpleWorkflow();assert.equal(w.uiSimpleReportIntent().revision,revision);
 const auth=fs.readFileSync('js/auth.js','utf8');w.canOperateAgent=()=>true;w.canUseSharedUiAi=()=>true;
 w.eval(auth.slice(auth.indexOf('const WORKFLOW_PERMISSIONS'),auth.indexOf('function applyAccessNavigation')));
 await w.initUiSimpleAvailability({navigate:false});assert.equal(w.canAccessWorkflow('ui_reports'),true);assert.equal(w.canAccessWorkflow('ui_start'),false);
});

test('picture refresh permission matches global runner refresh access including superuser rules',async t=>{
 const {w,calls}=fixture(t);await w.initUiSimpleAvailability();w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();const options=calls.find(x=>x?.loadPicture);
 assert.equal(typeof options.canRefreshPictures,'function');
 const auth=fs.readFileSync('js/auth.js','utf8');w.eval(auth.slice(auth.indexOf('function hasPermission('),auth.indexOf('function requireUiEditPermission(')));
 const allowed={user_id:'u1',permissions:['ui.view','platform.configure'],scope:{ui_apps:'*'}};
 for(const [profile,expected]of [[allowed,true],[{...allowed,scope:{ui_apps:['app.a']}},false],[{...allowed,scope:{ui_apps:['*']}},false],
  [{...allowed,permissions:['ui.view']},false],[{...allowed,permissions:['platform.configure']},false],
  [{...allowed,status:'disabled'},false],[{...allowed,must_change_password:true},false],
  [{user_id:'admin',is_superuser:true,permissions:[],scope:{ui_apps:[]}},true],[{user_id:'admin',is_superuser:true,must_change_password:true},false],[null,false]]){
  w.currentAccessProfile=profile;assert.equal(options.canRefreshPictures(),expected,JSON.stringify(profile));
 }
});

const privateImagePath='/ui-executions/uib_example/artifacts/'+'a'.repeat(64);
function imageResponse(w,{mime='image/png',size=3,length=null,status=200}={}){
 return {ok:status===200,status,headers:{get:key=>key==='content-type'?mime:key==='content-length'?length:null},blob:async()=>new w.Blob([new Uint8Array(size)],{type:mime})};
}
test('report image transport is exposed to the authenticated workflow and returns only a matching image Blob',async t=>{
 const {w,calls}=fixture(t);await w.initUiSimpleAvailability();w.activeWorkflow='ui_reports';w.showUiSimpleWorkflow();
 const options=calls.find(x=>x?.mode==='reports');assert.equal(typeof options.loadEvidenceImage,'function');
 const requests=[];w.authHeaders=()=>({'X-Test-Auth':'test-auth'});w.nativeFetch=async(path,opts)=>{requests.push({path,opts});return imageResponse(w);};
 const blob=await options.loadEvidenceImage(privateImagePath,'png');assert.equal(blob.type,'image/png');assert.equal(blob.size,3);
 assert.equal(requests.length,1);assert.equal(requests[0].path,'/api'+privateImagePath);assert.equal(requests[0].opts.headers['X-Test-Auth'],'test-auth');assert.equal(requests[0].opts.redirect,'error');
});
test('report image transport rejects external, nonartifact, query and hash paths before requesting',async t=>{
 const {w}=fixture(t);let requests=0;w.nativeFetch=async()=>{requests++;return imageResponse(w);};
 for(const path of ['https://outside.example'+privateImagePath,'/reports/a.png',privateImagePath+'?download=1',privateImagePath+'#preview',privateImagePath+'?',privateImagePath+'#',privateImagePath.replace('uib_example','other'),privateImagePath.replace(/a$/,'g')]){
  await assert.rejects(()=>w.loadUiBatchImage(path,'png'),/截图地址/);
 }
 for(const kind of ['html','jpeg','svg','','toString','constructor'])await assert.rejects(()=>w.loadUiBatchImage(privateImagePath,kind),/截图格式/);
 assert.equal(requests,0);
});
test('report image transport requires an authenticated account',async t=>{
 const {w}=fixture(t);w.currentAccessProfile=null;let requests=0;w.nativeFetch=async()=>{requests++;return imageResponse(w);};
 await assert.rejects(()=>w.loadUiBatchImage(privateImagePath,'png'),/登录/);assert.equal(requests,0);
});
test('report image transport rejects HTML and image MIME that differs from the artifact kind',async t=>{
 const {w}=fixture(t);
 for(const [mime,kind]of [['text/html','png'],['image/jpeg','png'],['image/png','jpg'],['image/svg+xml','png'],['image/png-extra','png']]){
  w.nativeFetch=async()=>imageResponse(w,{mime});await assert.rejects(()=>w.loadUiBatchImage(privateImagePath,kind),/截图格式/);
 }
 w.nativeFetch=async()=>imageResponse(w,{mime:'image/jpeg'});assert.equal((await w.loadUiBatchImage(privateImagePath,'jpg')).type,'image/jpeg');
});
test('report image transport rejects declared and actual bodies larger than 8 MiB',async t=>{
 const {w}=fixture(t);let bodyRead=false;
 w.nativeFetch=async()=>({...imageResponse(w,{length:String(8*1024*1024+1)}),blob:async()=>{bodyRead=true;throw new Error('must not read');}});
 await assert.rejects(()=>w.loadUiBatchImage(privateImagePath,'png'),/过大/);assert.equal(bodyRead,false);
 w.nativeFetch=async()=>imageResponse(w,{size:8*1024*1024+1});await assert.rejects(()=>w.loadUiBatchImage(privateImagePath,'png'),/过大/);
 w.nativeFetch=async()=>imageResponse(w,{size:0});await assert.rejects(()=>w.loadUiBatchImage(privateImagePath,'png'),/为空/);
});
test('report image stream is cancelled as soon as the body exceeds the limit',async t=>{
 const {w}=fixture(t);let reads=0,cancelled=false;
 w.nativeFetch=async()=>({...imageResponse(w),body:{getReader:()=>({read:async()=>{reads++;return {value:new Uint8Array(5*1024*1024),done:false};},cancel:async()=>{cancelled=true;},releaseLock:()=>{}})}});
 await assert.rejects(()=>w.loadUiBatchImage(privateImagePath,'png'),/过大/);assert.equal(reads,2);assert.equal(cancelled,true);
});
test('report image stream returns the complete bounded Blob and releases its reader',async t=>{
 const {w}=fixture(t);const chunks=[new Uint8Array([1,2]),new Uint8Array([3])];let released=false;
 w.nativeFetch=async()=>({...imageResponse(w),body:{getReader:()=>({read:async()=>chunks.length?{value:chunks.shift(),done:false}:{done:true},cancel:async()=>{},releaseLock:()=>{released=true;}})}});
 const blob=await w.loadUiBatchImage(privateImagePath,'png');assert.equal(blob.size,3);assert.equal(blob.type,'image/png');assert.equal(released,true);
});
test('invalid screenshot metadata stops the network body immediately',async t=>{
 const {w}=fixture(t);let requestSignal;w.nativeFetch=async(path,options)=>{requestSignal=options.signal;return imageResponse(w,{mime:'text/html'});};
 await assert.rejects(()=>w.loadUiBatchImage(privateImagePath,'png'),/截图格式/);assert.equal(requestSignal.aborted,true);
});
test('caller cancellation before loading sends no request',async t=>{
 const {w}=fixture(t);const controller=new w.AbortController();controller.abort();let requests=0;w.nativeFetch=async()=>{requests++;return imageResponse(w);};
 await assert.rejects(()=>w.loadUiBatchImage(privateImagePath,'png',{signal:controller.signal}),error=>error.name==='AbortError');assert.equal(requests,0);
});
for(const reason of ['caller','leave','logout','account-switch'])test(`report image transport aborts on ${reason} and rejects even a late response`,async t=>{
 const {w}=fixture(t);let resolve,requestSignal;const controller=new w.AbortController();
 w.nativeFetch=(path,options)=>{requestSignal=options.signal;return new Promise(r=>resolve=r);};
 const pending=w.loadUiBatchImage(privateImagePath,'png',{signal:controller.signal});
 if(reason==='caller')controller.abort();else if(reason==='leave')w.leaveUiSimpleWorkflow();else if(reason==='logout'){w.currentAccessProfile=null;w.resetUiSimpleWorkflow();}else w.currentAccessProfile={user_id:'u2'};
 if(reason!=='account-switch')assert.equal(requestSignal.aborted,true);
 resolve(imageResponse(w));await assert.rejects(pending,error=>error.name==='AbortError');
});
test('report image transport checks identity again after the image body finishes',async t=>{
 const {w}=fixture(t);let resolveBody;w.nativeFetch=async()=>({...imageResponse(w),blob:()=>new Promise(r=>resolveBody=r)});
 const pending=w.loadUiBatchImage(privateImagePath,'png');await wait();w.currentAccessProfile={user_id:'u2'};
 resolveBody(new w.Blob(['png'],{type:'image/png'}));await assert.rejects(pending,error=>error.name==='AbortError');
});
test('report image timeout aborts the network request at 15 seconds and gives a retry message',async t=>{
 const {w}=fixture(t);let timeout,timeoutMs,requestSignal;w.setTimeout=(callback,ms)=>{timeout=callback;timeoutMs=ms;return 123;};w.clearTimeout=()=>{};
 w.nativeFetch=(path,options)=>{requestSignal=options.signal;return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new w.DOMException('Aborted','AbortError'))));};
 const pending=w.loadUiBatchImage(privateImagePath,'png');assert.equal(timeoutMs,15000);timeout();
 await assert.rejects(pending,/超时/);assert.equal(requestSignal.aborted,true);
});
test('private image HTTP errors retain status for the report UI',async t=>{
 const {w}=fixture(t);for(const status of [401,403,404,500]){w.nativeFetch=async()=>imageResponse(w,{status});await assert.rejects(()=>w.loadUiBatchImage(privateImagePath,'png'),error=>error.status===status);}
});

test('case creation lives under library and mounts Chinese editor with separate permissions',async t=>{const {w,calls}=fixture(t);let draftOptions,disposed=0;w.UiCaseDraft={mount:(node,options)=>{draftOptions=options;return {dispose:()=>disposed++};}};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_library';w.showUiSimpleWorkflow();w.document.querySelector('[data-ui-create-case]').click();assert.match(w.document.getElementById('editor-area').textContent,/大白话描述/);w.document.querySelector('[data-ui-case-source="text"]').click();assert.equal(draftOptions.canEdit(),true);assert.equal(draftOptions.canPublish(),true);w.document.querySelector('[data-ui-case-back]').click();assert.equal(disposed,1);assert.equal(calls.at(-1).mode,'library');});
test('viewer cannot open creation and stale creation button cannot change next page',async t=>{const {w}=fixture(t);w.UiCaseDraft={mount:()=>({dispose(){}})};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_library';w.hasPermission=p=>p==='ui.view';w.showUiSimpleWorkflow();assert.equal(w.document.querySelector('[data-ui-create-case]').disabled,true);w.hasPermission=()=>true;w.showUiSimpleWorkflow();const stale=w.document.querySelector('[data-ui-create-case]');w.leaveUiSimpleWorkflow();w.activeWorkflow='ui_reports';w.document.getElementById('editor-area').textContent='其他页面';stale.click();assert.equal(w.document.getElementById('editor-area').textContent,'其他页面');});
test('source chooser reuses authorized recording and requirement workflows',async t=>{const {w,calls}=fixture(t);w.UiCaseDraft={mount:()=>({dispose(){}})};w.canAccessWorkflow=key=>key!=='generate';await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_library';w.showUiSimpleWorkflow();w.document.querySelector('[data-ui-create-case]').click();assert.equal(w.document.querySelector('[data-ui-case-source="materials"]').disabled,true);w.document.querySelector('[data-ui-case-source="recording"]').click();assert.equal(calls.at(-1),'device_recorder');});
test('source chooser cannot mount an editor after identity changes',async t=>{const {w}=fixture(t);let mounts=0;w.UiCaseDraft={mount:()=>{mounts++;return {dispose(){}};}};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_library';w.showUiSimpleWorkflow();w.document.querySelector('[data-ui-create-case]').click();w.currentAccessProfile={user_id:'u2'};w.document.querySelector('[data-ui-case-source="text"]').click();assert.equal(mounts,0);});

test('library handoff is one-use and identity-scoped',async t=>{
 const {w,calls}=fixture(t);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_library';w.showUiSimpleWorkflow();
 const library=calls.filter(c=>c?.mode==='library').at(-1),selection={app:'app.a',cases:[{case_id:'c1',revision:'r1'}]};
 library.openStartTest(selection);assert.equal(w.activeWorkflow,'ui_start');w.showUiSimpleWorkflow();
 assert.equal(calls.filter(c=>c?.mode==='start').at(-1).entrySelection.app,'app.a');
 w.showUiSimpleWorkflow();assert.equal(calls.filter(c=>c?.mode==='start').at(-1).entrySelection,null);
 w.activeWorkflow='ui_library';w.showUiSimpleWorkflow();const stale=calls.filter(c=>c?.mode==='library').at(-1);
 w.currentAccessProfile={user_id:'u2'};stale.openStartTest(selection);assert.equal(w.activeWorkflow,'ui_library');
});
test('resume drafts opens the private editor directly without generating or dispatching',async t=>{
 const {w,calls}=fixture(t);let mounts=0;
 w.UiCaseDraft={mount:()=>{mounts++;return {dispose(){}};}};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_library';w.showUiSimpleWorkflow();
 w.document.querySelector('[data-ui-resume-drafts]').click();assert.equal(mounts,1);
 assert.ok(w.document.querySelector('[data-ui-case-back]'));assert.equal(calls.includes('ui_start'),false);
});

test('recording handoff opens library editor once with the imported draft',async t=>{
 const {w}=fixture(t);await w.initUiSimpleAvailability();const mounted=[];w.UiCaseDraft={mount:(n,o)=>{mounted.push(o);return {dispose(){}};}};
 const draft={draft_id:'uid_x',app_id:'com.kfb.model'};w.openImportedUiDraft(draft);w.showUiSimpleWorkflow();
 assert.equal(mounted.length,1);assert.equal(mounted[0].initialDraft,draft);
 w.showUiSimpleWorkflow();assert.equal(mounted.length,1);
});

async function videoFixture(t){
 const f=fixture(t),{w}=f;w.UiCaseDraft={mount:()=>({dispose(){}})};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_library';w.showUiSimpleWorkflow();
 const calls=[];let respond=null;
 w.apiRequest=async(path,options={})=>{calls.push({path,options});if(respond){const result=respond(path,options);if(result!==undefined)return result;}return path.startsWith('/ui-executions/catalog')?{ok:true,apps:[{app_id:'a',name:'应用A'},{app_id:'b',name:'应用B'}]}:{ok:true,sources:[]};};
 w.document.querySelector('[data-ui-create-case]').click();assert.ok(w.document.querySelector('[data-ui-case-source="video"]'));w.document.querySelector('[data-ui-case-source="video"]').click();await wait();
 const q=s=>w.document.querySelector(s);function app(value){q('[data-video-app]').value=value;q('[data-video-app]').dispatchEvent(new w.Event('change'));}
 return {...f,calls,q,app,respond:fn=>respond=fn};
}
test('library opens saved video history directly without upload or execution',async t=>{
 const {w}=fixture(t);w.UiCaseDraft={mount:()=>({dispose(){}})};await w.initUiSimpleAvailability({navigate:false});
 w.activeWorkflow='ui_library';w.showUiSimpleWorkflow();const calls=[];
 w.apiRequest=async path=>{calls.push(path);return {ok:true,apps:[]};};
 w.document.querySelector('[data-ui-video-history]').click();await wait();
 assert.equal(w.document.querySelector('[data-video-history]').open,true);
 assert.deepEqual(calls,['/ui-executions/catalog']);
});
test('saved video history uses readable escaped names and distinguishes repeated analyses',async t=>{
 const f=await videoFixture(t);
 f.respond(path=>path.startsWith('/ui-video-sources?')?{ok:true,sources:[{source_id:'s1',
  filename:'opaque-source.mp4',title:'删除打印记录 <img src=x>',status:'ready',draft_id:'uid_1',
  created_at:1792114455,analysis_version:2}]}:undefined);
 f.app('a');await wait();const list=f.q('[data-video-list]');
 assert.match(list.textContent,/删除打印记录/);assert.match(list.textContent,/已生成草稿/);
 assert.match(list.textContent,/2026/);assert.match(list.textContent,/第 2 次分析/);
 assert.equal(list.querySelector('img'),null);
});
test('video source picker lists durable own tasks and ignores late previous-app lists',async t=>{
 const f=await videoFixture(t);let resolve;f.respond(path=>path==='/ui-video-sources?app_id=a&view=library'?new Promise(r=>resolve=r):undefined);
 f.app('a');await wait();f.app('b');await wait();resolve({ok:true,sources:[{source_id:'old',filename:'旧应用素材',status:'ready'}]});await wait();
 assert.doesNotMatch(f.q('[data-video-list]').textContent,/旧应用素材/);
 assert.equal(f.calls.filter(c=>c.path.includes('/ui-executions/devices')).length,0);
});
test('video status cancellation wins over an older in-flight refresh',async t=>{
 const f=await videoFixture(t),source={source_id:'uvs_'+'a'.repeat(32),filename:'clip.mp4',status:'analyzing',analysis_version:1};let resolve,reads=0;
 f.respond(path=>{if(path.startsWith('/ui-video-sources?'))return {ok:true,sources:[source]};if(path.endsWith('/cancel'))return {ok:true,source:{...source,status:'cancelled'}};if(path.includes(source.source_id)){if(++reads===1)return {ok:true,source};return new Promise(r=>resolve=r);}});
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();f.q('[data-video-status]').click();await wait();f.q('[data-video-action="cancel"]').click();await wait();
 resolve({ok:true,source});await wait();assert.match(f.q('[data-video-detail]').textContent,/已取消/);assert.equal(f.q('[data-video-action="cancel"]'),null);
});
test('video import opens existing Chinese editor without dispatch and revokes media on departure',async t=>{
 const f=await videoFixture(t),source={source_id:'uvs_'+'b'.repeat(32),filename:'clip.mov',status:'ready',analysis_version:1,candidate_steps:[],uncertainties:[]};let adopted,revoked=0;
 f.w.URL.revokeObjectURL=()=>revoked++;f.w.UiCaseDraft.mount=(n,o)=>{adopted=o.initialDraft;return {dispose(){}};};
 f.respond(path=>path.endsWith('/import')?{ok:true,draft:{draft_id:'draft-video',app_id:'a'}}:path.includes(source.source_id)?{ok:true,source}:path.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:undefined);
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();f.q('[data-video-preview]').click();await wait();assert.equal(f.q('video').src,'blob:test');
 f.q('[data-video-action="import"]').click();await wait();assert.equal(adopted.draft_id,'draft-video');assert.ok(revoked>0);assert.equal(f.calls.some(c=>/\/try$/.test(c.path)),false);
});

test('latest clicked video frame wins even when superseded network and body finish late',async t=>{
 const f=await videoFixture(t),source={source_id:'uvs_'+'c'.repeat(32),filename:'clip.mp4',status:'ready',analysis_version:1,candidate_steps:[{start_ms:0,end_ms:1000,evidence_kind:'inferred',action:'aiTap',target:'我的入口',observation:'个人页面出现',frame_refs:['frame-001','frame-002'],uncertainties:[]}],uncertainties:[]};
 f.respond(path=>path.includes(source.source_id)?{ok:true,source}:path.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:undefined);
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();
 const bodies={},signals={},urls=[],revoked=[];f.w.URL.createObjectURL=blob=>{const url='blob:'+blob.kind;urls.push(url);return url;};f.w.URL.revokeObjectURL=url=>revoked.push(url);
 f.w.nativeFetch=async(path,options)=>{const frame=path.includes('frame-001')?'A':'B';signals[frame]=options.signal;return {ok:true,blob:()=>new Promise(resolve=>bodies[frame]=resolve)};};
 f.q('[data-video-frame="frame-001"]').click();await wait();f.q('[data-video-frame="frame-002"]').click();await wait();
 assert.equal(signals.A.aborted,true);bodies.B({kind:'B'});await wait();assert.equal(f.q('[data-video-frame-preview]').getAttribute('src'),'blob:B');bodies.A({kind:'A'});await wait();assert.equal(f.q('[data-video-frame-preview]').getAttribute('src'),'blob:B');assert.deepEqual(urls,['blob:B']);
 f.q('[data-ui-case-back]').click();assert.ok(revoked.includes('blob:B'));
});

test('video candidate actions are presented in Chinese while import keeps original action data',async t=>{
 const f=await videoFixture(t),source={source_id:'uvs_'+'d'.repeat(32),filename:'clip.mp4',status:'ready',analysis_version:1,candidate_steps:[{start_ms:0,end_ms:1000,evidence_kind:'inferred',action:'aiTap',target:'我的入口',observation:'个人页面出现',frame_refs:['frame-001'],uncertainties:[]}],uncertainties:[]};
 f.respond(path=>path.includes(source.source_id)?{ok:true,source}:path.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:undefined);
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();assert.match(f.q('[data-video-detail]').textContent,/点击 我的入口/);assert.doesNotMatch(f.q('[data-video-detail]').textContent,/aiTap/);assert.equal(source.candidate_steps[0].action,'aiTap');
});


test('video upload without an application explains the missing field and focuses it',async t=>{
 const f=await videoFixture(t);let uploads=0;f.w.nativeFetch=async()=>{uploads++;};
 Object.defineProperty(f.q('[data-video-file]'),'files',{value:[new f.w.File(['video'],'clip.mp4')]});
 f.q('[data-video-upload]').click();await wait();
 assert.match(f.q('[data-video-notice]').textContent,/请先选择测试应用/);
 assert.equal(f.w.document.activeElement,f.q('[data-video-app]'));assert.equal(uploads,0);
});
test('video upload without a file focuses the chooser and sends no request',async t=>{
 const f=await videoFixture(t);f.app('a');await wait();let uploads=0;f.w.nativeFetch=async()=>{uploads++;};
 f.q('[data-video-upload]').click();await wait();
 assert.match(f.q('[data-video-notice]').textContent,/请选择.*MP4\/MOV/);
 assert.equal(f.w.document.activeElement,f.q('[data-video-file]'));assert.equal(uploads,0);
});
test('late history failure cannot overwrite a newer video upload status',async t=>{
 const f=await videoFixture(t);let rejectHistory;
 f.respond(path=>path.startsWith('/ui-video-sources?')&&!rejectHistory?new Promise((_,reject)=>rejectHistory=reject):undefined);
 f.app('a');await wait();
 Object.defineProperty(f.q('[data-video-file]'),'files',{value:[new f.w.File(['video'],'clip.mp4')]});
 f.w.nativeFetch=async()=>({ok:true,json:async()=>({ok:true,source:{source_id:'uvs_'+'b'.repeat(32),filename:'clip.mp4',status:'queued',analysis_version:1}})});
 f.q('[data-video-upload]').click();await wait();assert.match(f.q('[data-video-notice]').textContent,/排队/);
 rejectHistory(new Error('旧列表请求超时'));await wait();
 assert.match(f.q('[data-video-notice]').textContent,/排队/);
});
test('binary video upload retains bearer authentication with the real Headers helper',async t=>{
 const f=await videoFixture(t);f.app('a');await wait();
 const file=new f.w.File(['binary-fixture'],'recording.mp4',{type:'video/mp4'});
 Object.defineProperty(f.q('[data-video-file]'),'files',{value:[file]});
 let upload;f.w.nativeFetch=async(path,options)=>{upload={path,options};return {ok:true,json:async()=>({ok:true,source:{source_id:'uvs_'+'e'.repeat(32),filename:file.name,status:'queued',analysis_version:1}})};};
 f.q('[data-video-upload]').click();await wait();await wait();
 assert.ok(upload);assert.equal(upload.options.method,'POST');assert.equal(upload.options.body,file);
 assert.equal(new Headers(upload.options.headers).get('Authorization'),'Bearer fixture-session-token');
 assert.equal(new Headers(upload.options.headers).get('Content-Type'),'application/octet-stream');
 assert.ok(upload.options.headers instanceof Headers);
 assert.match(f.q('[data-video-notice]').textContent,/排队/);
});

test('visible video task status updates the list and notice including after a stale list response',async t=>{
 const f=await videoFixture(t),initial={source_id:'uvs_'+'1'.repeat(32),filename:'clip.mp4',status:'decoding',analysis_version:1};let detail=initial;
 f.respond(path=>path.includes(initial.source_id)?{ok:true,source:detail}:path.startsWith('/ui-video-sources?')?{ok:true,sources:[initial]}:undefined);
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();
 f.q('[data-video-notice]').textContent='上传完成，后台分析中。';detail={...initial,status:'failed',error:'分析未返回有效步骤，请重试'};
 f.q('[data-video-status]').click();await wait();assert.match(f.q('[data-video-detail]').textContent,/分析失败/);assert.match(f.q('[data-video-list]').textContent,/分析失败/);assert.match(f.q('[data-video-notice]').textContent,/分析未返回有效步骤/);assert.doesNotMatch(f.q('[data-video-notice]').textContent,/后台分析中/);
 f.q('[data-video-refresh]').click();await wait();assert.match(f.q('[data-video-list]').textContent,/分析失败/);
});

test('unchanged video polling preserves selected media elements and object URLs',async t=>{
 const f=await videoFixture(t),source={source_id:'uvs_'+'2'.repeat(32),filename:'clip.mp4',status:'analyzing',analysis_version:1,media:{duration_ms:1000,frames:[{frame_id:'f0',timestamp_ms:0}]},candidate_steps:[{start_ms:0,end_ms:0,evidence_kind:'inferred',action:'aiTap',target:'我的入口',observation:'个人页面出现',frame_refs:['f0'],uncertainties:[]}],uncertainties:[]};let updated=1,revoked=0;
 f.respond(path=>path.includes(source.source_id)?{ok:true,source:{...source,updated_at:updated++}}:path.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:undefined);
 f.w.URL.revokeObjectURL=()=>revoked++;
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();f.q('[data-video-preview]').click();await wait();const video=f.q('video');video.currentTime=0.5;f.q('[data-video-frame]').click();await wait();const image=f.q('[data-video-frame-preview]');
 f.q('[data-video-status]').click();await wait();assert.equal(f.q('video'),video);assert.equal(video.getAttribute('src'),'blob:test');assert.equal(video.currentTime,0.5);assert.equal(f.q('[data-video-frame-preview]'),image);assert.equal(image.getAttribute('src'),'blob:test');assert.equal(revoked,0);
 f.q('[data-ui-case-back]').click();assert.ok(revoked>0);
});

function videoClock(f){
 let hidden=false,next=0;const timers=new Map();
 Object.defineProperty(f.w.document,'hidden',{configurable:true,get:()=>hidden});
 f.w.setTimeout=(fn,ms)=>{assert.equal(ms,5000);timers.set(++next,fn);return next;};f.w.clearTimeout=id=>timers.delete(id);
 return {timers,visibility(value){hidden=value;f.w.document.dispatchEvent(new f.w.Event('visibilitychange'));},async tick(){const entry=timers.entries().next().value;assert.ok(entry,'periodic timer exists');timers.delete(entry[0]);entry[1]();await wait();},async exhaust(){for(let i=0;i<360;i++)await this.tick();assert.equal(timers.size,0);}};
}
async function activeVideo(t){
 const f=await videoFixture(t),clock=videoClock(f);let source={source_id:'status-refresh',filename:'clip.mp4',status:'analyzing',analysis_version:1,updated_at:1,analysis_progress:{phase:'observing',completed:6,total:9},uncertainties:['保留原始疑问']};
 f.respond(p=>p.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:p.includes('/status-refresh?')?{ok:true,source}:undefined);
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();
 return {...f,clock,get source(){return source;},set source(next){source=next;},reads:()=>f.calls.filter(c=>c.path.includes('/status-refresh?')).length};
}
test('video status refresh reads terminal immediately after capped hidden return without event floods',async t=>{
 const f=await activeVideo(t);await f.clock.exhaust();const before=f.reads();
 f.clock.visibility(true);await wait();assert.equal(f.reads(),before);assert.equal(f.clock.timers.size,0);
 f.source={...f.source,status:'failed',error:'真实服务端失败',updated_at:2};f.clock.visibility(false);
 assert.equal(f.reads(),before+1,'return must fetch before a timer fires');
 for(let i=0;i<5;i++)f.clock.visibility(false);await wait();
 assert.equal(f.reads(),before+1);assert.equal(f.clock.timers.size,0);assert.match(f.q('[data-video-detail]').textContent,/真实服务端失败/);
 assert.equal(f.q('[data-video-progress]'),null);assert.equal(f.calls.some(c=>c.options.method==='POST'),false);
 let removed=0;const remove=f.w.document.removeEventListener.bind(f.w.document);f.w.document.removeEventListener=(name,...args)=>{if(name==='visibilitychange')removed++;return remove(name,...args);};
 f.q('[data-ui-case-back]').click();f.clock.visibility(true);f.clock.visibility(false);await wait();assert.equal(removed,1);assert.equal(f.reads(),before+1);
});
test('video status refresh exposes paused notice without replacing media and manual GET resets cap',async t=>{
 const f=await activeVideo(t);f.q('[data-video-preview]').click();await wait();const video=f.q('video');video.currentTime=0.5;let revoked=0;f.w.URL.revokeObjectURL=()=>revoked++;
 const button=f.q('.ui-video-task-main [data-video-progress-refresh]');assert.ok(button,'main progress refresh outside details');assert.equal(button.textContent,'刷新进度');button.focus();
 await f.clock.exhaust();const paused=f.q('[data-video-poll-paused]');assert.ok(paused&&!paused.hidden);assert.match(paused.textContent,/自动更新已暂停.*仍可能.*刷新进度/);
 assert.match(f.q('[data-video-progress]').textContent,/6 \/ 9/);assert.match(f.q('[data-video-technical]').textContent,/保留原始疑问/);assert.equal(f.q('video'),video);assert.equal(f.w.document.activeElement,button);assert.equal(video.currentTime,0.5);assert.equal(revoked,0);
 const before=f.reads();f.clock.visibility(true);f.clock.visibility(false);await wait();assert.equal(f.reads(),before+1);assert.equal(f.clock.timers.size,0);assert.equal(paused.hidden,false);
 button.click();assert.equal(paused.hidden,true,'manual reset removes notice immediately');await wait();assert.equal(f.reads(),before+2);assert.equal(f.clock.timers.size,1);
 f.source={...f.source,status:'ready',updated_at:2};button.click();await wait();assert.equal(f.q('[data-video-technical]').open,false);assert.match(f.q('[data-video-detail]').textContent,/开始核对/);assert.equal(f.q('[data-video-poll-paused]'),null);assert.equal(f.clock.timers.size,0);assert.equal(f.calls.some(c=>c.options.method==='POST'),false);
});
test('video status refresh clears exhausted notice on newer analysis version',async t=>{
 const f=await activeVideo(t);await f.clock.exhaust();assert.equal(f.q('[data-video-poll-paused]')?.hidden,false);
 f.source={...f.source,analysis_version:2,updated_at:2};f.clock.visibility(true);f.clock.visibility(false);await wait();assert.equal(f.q('[data-video-poll-paused]').hidden,true);assert.equal(f.clock.timers.size,1);
});
test('video status refresh pauses hidden periodic timer and resumes immediately with one bounded read',async t=>{
 const f=await activeVideo(t),before=f.reads();assert.equal(f.clock.timers.size,1);f.clock.visibility(true);assert.equal(f.clock.timers.size,0);assert.equal(f.reads(),before);
 f.clock.visibility(false);assert.equal(f.reads(),before+1);await wait();assert.equal(f.clock.timers.size,1);for(let i=0;i<3;i++)f.clock.visibility(false);assert.equal(f.reads(),before+1);
 await f.clock.exhaust();assert.equal(f.reads(),before+361);
});
for(const change of ['teardown','account','app','source','version','newer status'])test(`video status refresh rejects stale visibility response after ${change}`,async t=>{
 const f=await activeVideo(t);let resolve;const source=f.source;
 f.respond(p=>p.startsWith('/ui-video-sources?')?{ok:true,sources:[f.source]}:p.includes('/status-refresh?')?new Promise(r=>resolve=r):p.includes('/other?')?{ok:true,source:{...source,source_id:'other',filename:'other.mp4'}}:undefined);
 f.clock.visibility(true);f.clock.visibility(false);assert.ok(resolve,'visibility starts guarded read');for(let i=0;i<3;i++)f.clock.visibility(false);assert.equal(f.reads(),2);
 if(change==='teardown')f.q('[data-ui-case-back]').click();
 if(change==='account')f.w.currentAccessProfile={user_id:'other'};
 if(change==='app')f.app('b');
 if(change==='source'){f.source={...source,source_id:'other',filename:'other.mp4'};f.q('[data-video-refresh]').click();await wait();f.q('[data-video-open]').click();await wait();}
 if(change==='version'){f.source={...source,analysis_version:2,updated_at:2};f.q('[data-video-refresh]').click();await wait();}
 if(change==='newer status'){f.respond(p=>p.includes('/status-refresh?')?{ok:true,source:{...source,status:'ready',updated_at:3}}:undefined);f.q('[data-video-status]').click();await wait();}
 resolve({ok:true,source:{...source,status:'failed',updated_at:2,error:'STALE VISIBILITY'}});await wait();
 assert.doesNotMatch(f.q('[data-video-detail]')?.textContent||'',/STALE VISIBILITY/);
 if(change==='version')assert.match(f.q('[data-video-detail]').textContent,/第 2 次分析|分析中/);
 if(change==='newer status')assert.match(f.q('[data-video-detail]').textContent,/开始核对/);
 if(['teardown','account','app'].includes(change)){f.clock.visibility(true);f.clock.visibility(false);await wait();assert.equal(f.clock.timers.size,0);}
});

test('refreshing my videos adopts a newer retry from another tab and rejects older detail and list responses',async t=>{
 const f=await videoFixture(t),failed={source_id:'uvs_'+'4'.repeat(32),filename:'clip.mp4',status:'failed',error:'旧分析失败',analysis_version:1,updated_at:100};
 let listing=failed,detail=failed,oldDetail,oldList,deferDetail=false,deferList=false,next=0;const timers=new Map();
 f.w.setTimeout=(fn,ms)=>{assert.equal(ms,5000);timers.set(++next,fn);return next;};f.w.clearTimeout=id=>timers.delete(id);
 f.respond(path=>{if(path.startsWith('/ui-video-sources?')){if(deferList){deferList=false;return new Promise(r=>oldList=r);}return {ok:true,sources:[listing]};}if(path.includes(failed.source_id)){if(deferDetail){deferDetail=false;return new Promise(r=>oldDetail=r);}return {ok:true,source:detail};}});
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();assert.match(f.q('[data-video-detail]').textContent,/分析失败/);assert.equal(timers.size,0);
 // Another tab retries this terminal source while a previous detail read is in flight.
 deferDetail=true;f.q('[data-video-status]').click();await wait();listing={...failed,status:'queued',error:'',analysis_version:2,updated_at:200};
 f.q('[data-video-refresh]').click();await wait();assert.match(f.q('[data-video-list]').textContent,/排队中/);assert.match(f.q('[data-video-detail]').textContent,/排队中/);assert.match(f.q('[data-video-notice]').textContent,/排队/);assert.equal(timers.size,1);
 oldDetail({ok:true,source:failed});await wait();assert.match(f.q('[data-video-detail]').textContent,/排队中/);assert.equal(timers.size,1);
 // A later detail progresses within the new version before an old list response arrives.
 deferList=true;f.q('[data-video-refresh]').click();await wait();detail={...listing,status:'analyzing',updated_at:300};f.q('[data-video-status]').click();await wait();
 oldList({ok:true,sources:[listing]});await wait();assert.match(f.q('[data-video-list]').textContent,/分析中/);assert.match(f.q('[data-video-detail]').textContent,/分析中/);assert.equal(timers.size,1);
 listing={...detail,status:'failed',error:'新版本失败',updated_at:400};f.q('[data-video-refresh]').click();await wait();assert.match(f.q('[data-video-detail]').textContent,/新版本失败/);assert.equal(timers.size,0);
 listing={...failed,updated_at:900};f.q('[data-video-refresh]').click();await wait();assert.match(f.q('[data-video-detail]').textContent,/新版本失败/);assert.match(f.q('[data-video-notice]').textContent,/新版本失败/);assert.equal(timers.size,0);
});

test('video main journey shows four stages and folds technical details for historical sources',async t=>{
 const f=await videoFixture(t),source={source_id:'uvs_'+'5'.repeat(32),filename:'clip.mp4',status:'ready',analysis_version:1,updated_at:100,candidate_steps:[{action:'aiTap',target:'我的入口',start_ms:0,end_ms:1000,frame_refs:['f0'],observation:'个人页面出现',evidence_kind:'inferred',uncertainties:[]}],uncertainties:[]};
 f.respond(path=>path.includes(source.source_id)?{ok:true,source}:path.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:undefined);
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();
 assert.match(f.q('[data-video-journey]').textContent,/上传视频.*生成用例.*核对步骤.*手机试跑/);assert.equal(f.q('[data-video-action="import"]').textContent,'开始核对');
 assert.equal(f.q('[data-video-technical]').open,false);assert.ok(f.q('[data-video-frame]').closest('[data-video-technical]'));assert.equal(f.calls.some(c=>c.path.endsWith('/import')),false);
});
test('a video without usable interactions explicitly offers another upload instead of a generated case',async t=>{
 const f=await videoFixture(t),source={source_id:'uvs_'+'6'.repeat(32),filename:'carousel.mp4',status:'review_needed',analysis_version:1,error:'仅自然变化',uncertainties:['没有实际操作']};
 f.respond(path=>path.includes(source.source_id)?{ok:true,source}:path.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:undefined);f.app('a');await wait();f.q('[data-video-open]').click();await wait();
 assert.match(f.q('[data-video-detail]').textContent,/未生成用例.*请上传包含实际操作的视频/);assert.ok(f.q('[data-video-reselect]'));assert.equal(f.q('[data-video-action="import"]'),null);
});
test('new upload reaching ready waits for explicit review before importing once',async t=>{
 const f=await videoFixture(t),queued={source_id:'uvs_'+'7'.repeat(32),filename:'clip.mp4',status:'queued',analysis_version:1,updated_at:100};let detail=queued,resolveImport,adopted;
 f.w.UiCaseDraft.mount=(n,o)=>{adopted=o.initialDraft;return {dispose(){}};};
 f.respond(path=>path.endsWith('/import')?new Promise(r=>resolveImport=r):path.includes(queued.source_id)?{ok:true,source:detail}:path.startsWith('/ui-video-sources?')?{ok:true,sources:[queued]}:undefined);
 f.app('a');await wait();Object.defineProperty(f.q('[data-video-file]'),'files',{value:[new f.w.File(['video'],'clip.mp4')]});f.w.nativeFetch=async()=>({ok:true,json:async()=>({ok:true,source:queued})});f.q('[data-video-upload]').click();await wait();
 detail={...queued,status:'ready',updated_at:200};f.q('[data-video-status]').click();await wait();await wait();assert.equal(f.calls.filter(c=>c.path.endsWith('/import')).length,0);
 f.q('[data-video-action="import"]').click();await wait();
 f.q('[data-video-status]')?.click();await wait();assert.equal(f.calls.filter(c=>c.path.endsWith('/import')).length,1);
 resolveImport({ok:true,draft:{draft_id:'generated',app_id:'a'}});await wait();assert.equal(adopted.draft_id,'generated');assert.equal(f.calls.some(c=>c.path.endsWith('/try')),false);
});
test('late explicit video import never opens a draft for a different signed in identity',async t=>{
 const f=await videoFixture(t),source={source_id:'uvs_'+'8'.repeat(32),filename:'clip.mp4',status:'ready',analysis_version:1};let resolveImport,mounts=0;
 f.w.UiCaseDraft.mount=()=>{mounts++;return {dispose(){}};};f.respond(path=>path.endsWith('/import')?new Promise(r=>resolveImport=r):undefined);
 f.app('a');await wait();Object.defineProperty(f.q('[data-video-file]'),'files',{value:[new f.w.File(['video'],'clip.mp4')]});f.w.nativeFetch=async()=>({ok:true,json:async()=>({ok:true,source})});f.q('[data-video-upload]').click();await wait();await wait();assert.equal(resolveImport,undefined);f.q('[data-video-action="import"]').click();await wait();assert.equal(typeof resolveImport,'function');
 f.w.currentAccessProfile={user_id:'other'};resolveImport({ok:true,draft:{draft_id:'wrong-owner',app_id:'a'}});await wait();assert.equal(mounts,0);
});

test('explicit retry completion waits for review before opening its new version',async t=>{
 const f=await videoFixture(t),failed={source_id:'uvs_'+'9'.repeat(32),filename:'clip.mp4',status:'failed',analysis_version:1,updated_at:100};let detail=failed,adopted;
 f.w.UiCaseDraft.mount=(n,o)=>{adopted=o.initialDraft;return {dispose(){}};};
 f.respond(path=>path.endsWith('/retry')?{ok:true,source:{...failed,status:'queued',analysis_version:2,updated_at:200}}:path.endsWith('/import')?{ok:true,draft:{draft_id:'retry-generated',revision:3,app_id:'a'}}:path.includes(failed.source_id)?{ok:true,source:detail}:path.startsWith('/ui-video-sources?')?{ok:true,sources:[failed]}:undefined);
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();f.q('[data-video-action="retry"]').click();await wait();detail={...failed,status:'ready',analysis_version:2,updated_at:300};f.q('[data-video-status]').click();await wait();await wait();
 assert.equal(f.calls.filter(c=>c.path.endsWith('/import')).length,0);f.q('[data-video-action="import"]').click();await wait();assert.equal(adopted.draft_id,'retry-generated');assert.equal(f.calls.some(c=>c.path.endsWith('/try')),false);
});
test('explicit import failure stays manually recoverable without repeated ready polling imports',async t=>{
 const f=await videoFixture(t),source={source_id:'uvs_'+'a'.repeat(32),filename:'clip.mp4',status:'ready',analysis_version:1,updated_at:100};let tries=0,adopted;
 f.w.UiCaseDraft.mount=(n,o)=>{adopted=o.initialDraft;return {dispose(){}};};
 f.respond(path=>path.endsWith('/import')?(++tries===1?Promise.reject(new Error('temporary failure')):{ok:true,draft:{draft_id:'preserved',revision:5,app_id:'a'}}):path.includes(source.source_id)?{ok:true,source}:undefined);
 f.app('a');await wait();Object.defineProperty(f.q('[data-video-file]'),'files',{value:[new f.w.File(['video'],'clip.mp4')]});f.w.nativeFetch=async()=>({ok:true,json:async()=>({ok:true,source})});f.q('[data-video-upload]').click();await wait();await wait();assert.equal(tries,0);f.q('[data-video-action="import"]').click();await wait();assert.equal(tries,1);
 f.q('[data-video-status]').click();await wait();await wait();assert.equal(tries,1);assert.ok(f.q('[data-video-action="import"]'));
 f.q('[data-video-action="import"]').click();await wait();assert.equal(tries,2);assert.equal(adopted.revision,5);
});
test('leaving the upload application preserves explicit navigation and ignores late ready data',async t=>{
 const f=await videoFixture(t),source={source_id:'uvs_'+'b'.repeat(32),filename:'clip.mp4',status:'queued',analysis_version:1};let resolveReady,mounts=0;
 f.w.UiCaseDraft.mount=()=>{mounts++;return {dispose(){}};};f.respond(path=>path.includes(source.source_id)?new Promise(r=>resolveReady=r):undefined);
 f.app('a');await wait();Object.defineProperty(f.q('[data-video-file]'),'files',{value:[new f.w.File(['video'],'clip.mp4')]});f.w.nativeFetch=async()=>({ok:true,json:async()=>({ok:true,source})});f.q('[data-video-upload]').click();await wait();f.q('[data-video-status]').click();await wait();
 f.app('b');await wait();resolveReady({ok:true,source:{...source,status:'ready'}});await wait();await wait();assert.equal(mounts,0);assert.equal(f.calls.some(c=>c.path.endsWith('/import')),false);assert.equal(f.q('[data-video-detail]').textContent,'');
});

test('video feedback shows actual stages, no invented percentage, and stops on failure',async t=>{
 const f=await videoFixture(t);let source={source_id:'uvs_'+'c'.repeat(32),filename:'clip.mp4',status:'decoding',analysis_version:1,updated_at:1};
 f.respond(path=>path.includes(source.source_id)?{ok:true,source}:path.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:undefined);
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();
 assert.match(f.q('[data-video-progress]').textContent,/读取视频/);assert.equal(f.q('[data-video-progress]').getAttribute('aria-busy'),'true');assert.equal(f.q('[data-video-progress]').textContent.includes('%'),false);
 source={...source,status:'analyzing',updated_at:2};f.q('[data-video-status]').click();await wait();assert.match(f.q('[data-video-progress]').textContent,/AI 正在识别/);
 source={...source,status:'failed',error:'模型超时',updated_at:3};f.q('[data-video-status]').click();await wait();assert.equal(f.q('[data-video-progress]'),null);assert.ok(f.q('[data-video-action="retry"]'));
});


test('long video progress updates without resetting private media',async t=>{
 const f=await videoFixture(t),source={source_id:'uvs_'+'a'.repeat(32),filename:'clip.mp4',status:'analyzing',analysis_version:1};let progress={phase:'observing',completed:0,total:10};
 f.respond(path=>path.includes(source.source_id)?{ok:true,source:{...source,analysis_progress:progress}}:path.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:undefined);
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();
 assert.match(f.q('[data-video-progress]').textContent,/已完成 0 \/ 10 段/);const video=f.q('video');
 progress={phase:'planning',completed:10,total:10};f.q('[data-video-status]').click();await wait();
 assert.match(f.q('[data-video-progress]').textContent,/视频已识别完/);assert.equal(f.q('video'),video);
});

test('video list filters, loads more and keeps exclusive views with truthful draft state',async t=>{
 const f=await videoFixture(t),sources=Array.from({length:12},(_,i)=>({source_id:'source'+i,title:'任务'+i,filename:'clip'+i+'.mp4',status:i===11?'failed':'ready',draft_id:i===0?'draft':'',updated_at:100-i}));
 f.respond(p=>p.startsWith('/ui-video-sources?')?{ok:true,sources}:p.includes('source0')?{ok:true,source:sources[0]}:undefined);f.app('a');await wait();
 assert.equal(f.q('[data-video-history]').hidden,false);assert.equal(f.q('[data-video-upload-view]').hidden,true);assert.equal(f.q('[data-video-detail-view]').hidden,true);assert.equal(f.q('[data-video-list]').querySelectorAll('[data-video-open]').length,10);assert.match(f.q('[data-video-list]').textContent,/已生成草稿/);assert.doesNotMatch(f.q('[data-video-list]').textContent,/已保存用例|试跑通过/);
 f.q('[data-video-more]').click();assert.equal(f.q('[data-video-list]').querySelectorAll('[data-video-open]').length,12);
 f.q('[data-video-query]').value='clip11';f.q('[data-video-query]').dispatchEvent(new f.w.Event('input'));assert.equal(f.q('[data-video-list]').querySelectorAll('[data-video-open]').length,1);
 f.q('[data-video-filter]').value='ready';f.q('[data-video-filter]').dispatchEvent(new f.w.Event('change'));assert.equal(f.q('[data-video-list]').querySelectorAll('[data-video-open]').length,0);
 f.q('[data-video-query]').value='';f.q('[data-video-query]').dispatchEvent(new f.w.Event('input'));f.q('[data-video-open]').click();await wait();assert.equal(f.q('[data-video-detail-view]').hidden,false);assert.equal(f.q('[data-video-history]').hidden,true);
 f.q('[data-video-back]').click();assert.equal(f.q('[data-video-history]').hidden,false);f.q('[data-video-new]').click();assert.equal(f.q('[data-video-upload-view]').hidden,false);assert.equal(f.q('[data-video-history]').hidden,true);
});
test('returning to list keeps active task and never navigates when ready arrives',async t=>{
 const f=await videoFixture(t);let source={source_id:'active',filename:'clip.mp4',status:'analyzing',analysis_version:1,updated_at:1};f.respond(p=>p.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:p.includes('/active?')?{ok:true,source}:undefined);f.app('a');await wait();f.q('[data-video-open]').click();await wait();f.q('[data-video-back]').click();source={...source,status:'ready',updated_at:2};f.q('[data-video-status]').click();await wait();assert.equal(f.q('[data-video-history]').hidden,false);assert.equal(f.q('[data-video-detail-view]').hidden,true);assert.equal(f.calls.some(c=>c.path.endsWith('/import')),false);assert.match(f.q('[data-video-list-summary]').textContent,/待核对 1/);assert.match(f.q('[data-video-list-summary]').textContent,/1 个视频/);
});
test('upload view resets journey and hidden task refresh cannot overwrite upload feedback',async t=>{
 const f=await videoFixture(t);let source={source_id:'hidden',filename:'old.mp4',status:'analyzing',analysis_version:1,updated_at:1};f.respond(p=>p.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:p.includes('/hidden?')?{ok:true,source}:undefined);f.app('a');await wait();f.q('[data-video-open]').click();await wait();f.q('[data-video-back]').click();f.q('[data-video-new]').click();assert.equal(f.q('[data-video-app]').closest('.ui-video-source').dataset.videoPhase,'upload');f.q('[data-video-notice]').textContent='正在选择新原片';source={...source,status:'failed',error:'旧任务失败',updated_at:2};f.q('[data-video-status]').click();await wait();assert.equal(f.q('[data-video-notice]').textContent,'正在选择新原片');assert.equal(f.q('[data-video-app]').closest('.ui-video-source').dataset.videoPhase,'upload');f.q('[data-video-upload-back]').click();assert.match(f.q('[data-video-list]').textContent,/分析失败/);f.q('[data-video-open]').click();await wait();assert.match(f.q('[data-video-notice]').textContent,/旧任务失败/);
});
test('ready filter excludes generated drafts consistently with summary categories',async t=>{const f=await videoFixture(t);f.respond(p=>p.startsWith('/ui-video-sources?')?{ok:true,sources:[{source_id:'one',status:'ready',filename:'one.mp4'},{source_id:'two',status:'ready',filename:'two.mp4',draft_id:'draft'}]}:undefined);f.app('a');await wait();f.q('[data-video-filter]').value='ready';f.q('[data-video-filter]').dispatchEvent(new f.w.Event('change'));assert.equal(f.q('[data-video-list]').querySelectorAll('[data-video-open]').length,1);assert.match(f.q('[data-video-list-summary]').textContent,/待核对 1.*已生成草稿 1/);});
test('entering upload clears selected task notice and hidden request errors preserve upload feedback',async t=>{const f=await videoFixture(t);let reject=false;const source={source_id:'notice',status:'analyzing',filename:'old.mp4',analysis_version:1};f.respond(p=>p.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:p.includes('/notice?')?(reject?Promise.reject(new Error('hidden failure')):{ok:true,source}):undefined);f.app('a');await wait();f.q('[data-video-open]').click();await wait();assert.match(f.q('[data-video-notice]').textContent,/分析/);f.q('[data-video-back]').click();f.q('[data-video-new]').click();assert.equal(f.q('[data-video-notice]').textContent,'');f.q('[data-video-notice]').textContent='新视频待上传';reject=true;f.q('[data-video-status]').click();await wait();assert.equal(f.q('[data-video-notice]').textContent,'新视频待上传');});
test('list polling preserves focused task action across unchanged and updated source status',async t=>{const f=await videoFixture(t);let source={source_id:'focus',status:'analyzing',filename:'clip.mp4',analysis_version:1,updated_at:1};f.respond(p=>p.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:p.includes('/focus?')?{ok:true,source}:undefined);f.app('a');await wait();f.q('[data-video-open]').click();await wait();f.q('[data-video-back]').click();const button=f.q('[data-video-open]');button.focus();f.q('[data-video-status]').click();await wait();assert.equal(f.w.document.activeElement,button);source={...source,status:'ready',updated_at:2};f.q('[data-video-status]').click();await wait();assert.equal(f.w.document.activeElement,f.q('[data-video-open]'));assert.equal(f.q('[data-video-history]').hidden,false);assert.match(f.q('[data-video-list]').textContent,/待核对/);});
test('leaving upload clears file validation notice from task list',async t=>{const f=await videoFixture(t);f.app('a');await wait();f.q('[data-video-new]').click();f.q('[data-video-upload]').click();assert.match(f.q('[data-video-notice]').textContent,/MP4/);f.q('[data-video-upload-back]').click();assert.equal(f.q('[data-video-notice]').textContent,'');});

test('video bank and input recovery progress labels preserve active review state',async t=>{
 const f=await videoFixture(t);let source={source_id:'runtime-progress',status:'analyzing',filename:'clip.mp4',analysis_version:1,analysis_progress:{phase:'perceiving',completed:7,total:38}};
 f.respond(p=>p.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:p.includes('/runtime-progress?')?{ok:true,source}:undefined);
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();
 assert.match(f.q('[data-video-progress]').textContent,/画面核对已完成 7 \/ 38/);
 assert.equal(f.q('[data-video-progress]').getAttribute('aria-busy'),'true');
 source={...source,analysis_progress:{phase:'input_recovery',completed:0,total:1}};
 f.q('[data-video-status]').click();await wait();
 assert.match(f.q('[data-video-progress]').textContent,/正在核对输入文字/);
 assert.doesNotMatch(f.q('[data-video-progress]').textContent,/已生成草稿/);
});

test('video status refresh shows cap notice while final periodic status GET is still pending',async t=>{
 const f=await activeVideo(t);for(let i=0;i<359;i++)await f.clock.tick();let resolve;
 f.respond(p=>p.includes('/status-refresh?')?new Promise(r=>resolve=r):undefined);
 await f.clock.tick();assert.ok(resolve);assert.equal(f.clock.timers.size,0);assert.equal(f.q('[data-video-poll-paused]').hidden,false);
 resolve({ok:true,source:f.source});await wait();assert.equal(f.q('[data-video-poll-paused]').hidden,false);
});
test('video status refresh coalesces returning edges during a pending read and escapes GET failures',async t=>{
 const f=await activeVideo(t);let reject;f.respond(p=>p.includes('/status-refresh?')?new Promise((_,r)=>reject=r):undefined);
 f.clock.visibility(true);f.clock.visibility(false);const reads=f.reads();
 f.clock.visibility(true);f.clock.visibility(false);f.clock.visibility(false);assert.equal(f.reads(),reads);
 reject(new Error('<img src=x> status fetch failed'));await wait();assert.match(f.q('[data-video-notice]').textContent,/status fetch failed/);assert.equal(f.q('[data-video-notice] img'),null);assert.match(f.q('[data-video-detail]').textContent,/6 \/ 9/);assert.equal(f.calls.some(c=>c.options.method==='POST'),false);
 f.clock.visibility(true);f.clock.visibility(false);assert.equal(f.reads(),reads+1);f.q('[data-ui-case-back]').click();reject(new Error('late error'));await wait();
});

test('video native understanding describes whole video without frame group counts',async t=>{
 const f=await videoFixture(t);const source={source_id:'native-progress',status:'analyzing',filename:'clip.mp4',analysis_version:1,analysis_progress:{phase:'understanding',completed:0,total:1}};
 f.respond(p=>p.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:p.includes('/native-progress?')?{ok:true,source}:undefined);
 f.app('a');await wait();f.q('[data-video-open]').click();await wait();
 assert.match(f.q('[data-video-detail]').textContent,/正在通读完整视频，梳理业务流程/);
 assert.doesNotMatch(f.q('[data-video-detail]').textContent,/0 \/ 1 组|38 组|预计|%/);
});

test('video history deletes only selected terminal records and preserves linked content',async t=>{
 const f=await videoFixture(t);const a={source_id:'uvs_'+ 'a'.repeat(32),filename:'a.mp4',status:'ready',draft_id:'draft'},b={source_id:'uvs_'+ 'b'.repeat(32),filename:'b.mp4',status:'analyzing'};
 let sources=[a,b],confirmation='';f.w.confirm=message=>{confirmation=message;return true;};
 f.respond((path,options)=>{if(path.endsWith('/history-remove')){sources=sources.filter(s=>!options.body.source_ids.includes(s.source_id));return {ok:true,removed:options.body.source_ids.length};}if(path.startsWith('/ui-video-sources?'))return {ok:true,sources};});
 f.app('a');await wait();const checks=f.w.document.querySelectorAll('[data-video-select]');assert.equal(checks.length,2);assert.equal(checks[1].disabled,true);checks[0].click();f.q('[data-video-delete-selected]').click();await wait();await wait();
 assert.match(confirmation,/保留.*草稿/);assert.equal(f.calls.filter(c=>c.path.endsWith('/history-remove')).length,1);assert.equal(f.q('[data-video-list]').textContent.includes('a.mp4'),false);assert.equal(f.q('[data-video-list]').textContent.includes('b.mp4'),true);
});

test('video library renames the display name without changing the source file',async t=>{
 const f=await videoFixture(t);let source={source_id:'uvs_'+ 'a'.repeat(32),filename:'original.mp4',status:'failed',updated_at:1};
 f.w.prompt=()=> '新的名称';
 f.respond((path,options)=>{if(path.endsWith('/rename')){source={...source,display_name:options.body.name,updated_at:2};return {ok:true,source};}if(path.startsWith('/ui-video-sources?'))return {ok:true,sources:[source]};});
 f.app('a');await wait();f.q('[data-video-rename]').click();await wait();await wait();
 assert.match(f.q('[data-video-list]').textContent,/新的名称/);assert.match(f.q('[data-video-list]').textContent,/original.mp4/);
 assert.ok(f.calls.some(c=>c.path.endsWith('/rename')&&c.options.body.name==='新的名称'));
});

test('video library opens the selected private original in a visible player',async t=>{
 const f=await videoFixture(t),source={source_id:'uvs_'+ 'a'.repeat(32),filename:'clip.mp4',status:'failed',analysis_version:1};let path;
 f.respond(p=>p.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:p.includes(source.source_id)?{ok:true,source}:undefined);
 f.w.URL.createObjectURL=()=> 'blob:video';f.w.URL.revokeObjectURL=()=>{};
 f.w.nativeFetch=async p=>{path=p;return {ok:true,blob:async()=>({})};};
 f.app('a');await wait();f.q('[data-video-play]').click();await wait();await wait();
 assert.match(path,/\/video\?app_id=a/);assert.equal(f.q('video').hidden,false);assert.equal(f.q('video').getAttribute('src'),'blob:video');
 assert.equal(f.q('[data-video-technical]').contains(f.q('video')),false);
});

test('start page leads into the existing creation chooser for text, recording, materials and video',async t=>{
 const {w}=fixture(t);w.UiCaseDraft={mount:()=>({dispose(){}})};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();
 const entry=w.document.querySelector('[data-ui-start-create]');assert.ok(entry);assert.match(w.document.querySelector('[data-ui-start-create-help]').textContent,/需求.*文字.*视频.*录制/);
 w.activateWorkflow=async key=>{w.activeWorkflow=key;w.showUiSimpleWorkflow();};entry.click();await wait();
 assert.equal(w.activeWorkflow,'ui_library');assert.deepEqual([...w.document.querySelectorAll('[data-ui-case-source]')].map(node=>node.dataset.uiCaseSource),['text','recording','materials','video']);
});

test('start creation entry respects edit permission and a rejected navigation',async t=>{
 const {w}=fixture(t);w.UiCaseDraft={mount:()=>({dispose(){}})};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_start';
 w.hasPermission=key=>key==='ui.view';w.showUiSimpleWorkflow();assert.equal(w.document.querySelector('[data-ui-start-create]').disabled,true);assert.match(w.document.querySelector('[data-ui-start-create-help]').textContent,/联系管理员.*编辑权限/);
 w.hasPermission=()=>true;w.showUiSimpleWorkflow();const entry=w.document.querySelector('[data-ui-start-create]');let calls=0;
 w.activateWorkflow=async()=>{calls++;};entry.click();await wait();assert.equal(calls,1);assert.equal(w.activeWorkflow,'ui_start');assert.equal(w.document.querySelector('[data-ui-case-source]'),null);
});

test('start creation continuation cannot affect another identity or workflow',async t=>{
 const {w}=fixture(t);w.UiCaseDraft={mount:()=>({dispose(){}})};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();
 let done;w.activateWorkflow=()=>new Promise(resolve=>{done=resolve;});w.document.querySelector('[data-ui-start-create]').click();
 w.resetUiSimpleWorkflow();w.currentAccessProfile={user_id:'u2'};w.activeWorkflow='ui_reports';w.document.getElementById('editor-area').textContent='其他账号报告';done();await wait();
 assert.equal(w.document.getElementById('editor-area').textContent,'其他账号报告');
});

test('a delayed creation navigation cannot reopen a chooser after leaving the original page',async t=>{
 const {w}=fixture(t);w.UiCaseDraft={mount:()=>({dispose(){}})};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();
 let done;w.activateWorkflow=()=>new Promise(resolve=>{done=resolve;});w.document.querySelector('[data-ui-start-create]').click();
 w.activeWorkflow='ui_reports';w.showUiSimpleWorkflow();w.activeWorkflow='ui_library';w.showUiSimpleWorkflow();done();await wait();
 assert.ok(w.document.querySelector('[data-ui-create-case]'));assert.equal(w.document.querySelector('[data-ui-case-source]'),null);
});

test('return from dirty creation asks once and cancellation preserves the editor',async t=>{
 const {w}=fixture(t);let disposed=0,confirmations=0,allow=false;
 w.UiCaseDraft={mount:(node)=>{node.textContent='未保存草稿';return {dispose:()=>disposed++,hasUnsavedChanges:()=>true};}};
 w.confirm=()=>{confirmations++;return allow;};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_library';w.showUiSimpleWorkflow();
 w.document.querySelector('[data-ui-create-case]').click();w.document.querySelector('[data-ui-case-source="text"]').click();
 const back=w.document.querySelector('[data-ui-case-back]');back.click();assert.equal(confirmations,1);assert.equal(disposed,0);assert.match(w.document.getElementById('editor-area').textContent,/未保存草稿/);
 allow=true;back.click();assert.equal(confirmations,2);assert.equal(disposed,1);assert.ok(w.document.querySelector('[data-ui-create-case]'));
});

test('workflow navigation consults creation guard before saving or disposing',async t=>{
 const {w}=fixture(t);let saveCalls=0,confirmed=0;w.UiCaseDraft={mount:node=>{node.textContent='未保存草稿';return {dispose(){},hasUnsavedChanges:()=>true};}};
 w.confirm=()=>{confirmed++;return false;};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_library';w.showUiSimpleWorkflow();w.document.querySelector('[data-ui-create-case]').click();w.document.querySelector('[data-ui-case-source="text"]').click();
 w.normalizeWorkflowKey=x=>x;w.canAccessWorkflow=()=>true;w.WORKFLOW_SECTIONS={ui_reports:{title:'我的测试'},dashboard:{title:'首页'}};w.saveEditorBeforeNavigation=async()=>{saveCalls++;return true;};
 const shell=fs.readFileSync('js/agent-status.js','utf8');w.eval(shell.slice(shell.indexOf('async function activateWorkflow('),shell.indexOf('\nfunction ',shell.indexOf('async function activateWorkflow(')+1)));
 await w.activateWorkflow('ui_reports');assert.equal(confirmed,1);assert.equal(saveCalls,0);assert.equal(w.activeWorkflow,'ui_library');assert.match(w.document.getElementById('editor-area').textContent,/未保存草稿/);
});

test('a slower earlier workflow navigation cannot replace the later page',async t=>{
 const {w}=fixture(t);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();
 const pending=[];w.normalizeWorkflowKey=x=>x;w.canAccessWorkflow=()=>true;w.WORKFLOW_SECTIONS={ui_library:{title:'用例库'},ui_reports:{title:'我的测试'},dashboard:{title:'首页'}};
 w.saveEditorBeforeNavigation=()=>new Promise(resolve=>pending.push(resolve));w.hasOpenEditor=()=>false;w.closeTransientUiForNavigation=()=>{};w.setActiveWorkflow=key=>{w.activeWorkflow=key;};w.resetYamlToolbarForManager=()=>{};w.applyLazyLoadForSection=()=>{};
 const shell=fs.readFileSync('js/agent-status.js','utf8');w.eval(shell.slice(shell.indexOf('async function activateWorkflow('),shell.indexOf('\nfunction ',shell.indexOf('async function activateWorkflow(')+1)));
 const first=w.activateWorkflow('ui_library'),second=w.activateWorkflow('ui_reports');pending[1](true);await second;assert.equal(w.activeWorkflow,'ui_reports');
 pending[0](true);await first;assert.equal(w.activeWorkflow,'ui_reports');
});

test('a pending workflow navigation cannot cross an account change',async t=>{
 const {w}=fixture(t);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();
 let done;w.normalizeWorkflowKey=x=>x;w.canAccessWorkflow=()=>true;w.WORKFLOW_SECTIONS={ui_library:{title:'用例库'},dashboard:{title:'首页'}};
 w.saveEditorBeforeNavigation=()=>new Promise(resolve=>{done=resolve;});w.hasOpenEditor=()=>false;w.closeTransientUiForNavigation=()=>{};w.setActiveWorkflow=key=>{w.activeWorkflow=key;};w.resetYamlToolbarForManager=()=>{};w.applyLazyLoadForSection=()=>{};
 const shell=fs.readFileSync('js/agent-status.js','utf8');w.eval(shell.slice(shell.indexOf('async function activateWorkflow('),shell.indexOf('\nfunction ',shell.indexOf('async function activateWorkflow(')+1)));
 const pending=w.activateWorkflow('ui_library');w.currentAccessProfile={user_id:'u2'};done(true);await pending;assert.equal(w.activeWorkflow,'ui_start');
});

test('late Agent updates keep simple workflow editors intact and refresh navigation badges',async t=>{
 const {w}=fixture(t);let disposed=0,centers=0,badges=0,renders=0;
 w.UiCaseDraft={mount:node=>{node.textContent='用户未保存的编辑';return {dispose:()=>disposed++,hasUnsavedChanges:()=>true};}};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_library';w.showUiSimpleWorkflow();w.document.querySelector('[data-ui-create-case]').click();w.document.querySelector('[data-ui-case-source="text"]').click();
 w.renderAgentCenter=()=>centers++;w.updateNavigationBadges=()=>badges++;w.renderActiveWorkflowPage=()=>renders++;
 const shell=fs.readFileSync('js/agent-status.js','utf8');w.eval(shell.slice(shell.indexOf('function renderAgentPageAfterRunUpdate('),shell.indexOf('\nasync function confirmAgentStep(',shell.indexOf('function renderAgentPageAfterRunUpdate('))));
 w.renderAgentPageAfterRunUpdate();assert.equal(centers,1);assert.equal(badges,1);assert.equal(renders,0);assert.equal(disposed,0);assert.match(w.document.getElementById('editor-area').textContent,/用户未保存的编辑/);
});

test('Agent updates preserve an open practice guide and an in-flight paid probe',async t=>{
 const {w}=fixture(t);let resolve,posts=0,badges=0,renders=0;
 w.canAccessWorkflow=()=>true;w.activeWorkflow='ai_practice';
 w.apiRequest=async(path,options={})=>{if(options.method==='POST'){posts++;return new Promise(r=>resolve=r);}return {ok:true,results:[]};};
 w.showAiPractice();const guide=w.document.querySelector('[data-ui-week-one]');guide.open=true;
 const panel=guide.querySelector('[data-week-probe]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 const button=panel.querySelector('[data-probe-run="text"]');button.click();assert.equal(posts,1);assert.equal(button.disabled,true);
 w.renderAgentCenter=()=>{};w.updateNavigationBadges=()=>badges++;w.renderActiveWorkflowPage=()=>{renders++;w.showAiPractice();};
 const shell=fs.readFileSync('js/agent-status.js','utf8');w.eval(shell.slice(shell.indexOf('function renderAgentPageAfterRunUpdate('),shell.indexOf('\nasync function confirmAgentStep(',shell.indexOf('function renderAgentPageAfterRunUpdate('))));
 w.renderAgentPageAfterRunUpdate();assert.equal(renders,0);assert.equal(badges,1);assert.equal(w.document.querySelector('[data-week-probe]'),panel);assert.equal(guide.open,true);assert.equal(button.disabled,true);button.click();assert.equal(posts,1);
 resolve({ok:true});await wait();await wait();assert.equal(button.disabled,false);
});

test('cancelling a later navigation also invalidates an older pending one',async t=>{
 const {w}=fixture(t);await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_start';w.showUiSimpleWorkflow();
 let done;w.normalizeWorkflowKey=x=>x;w.canAccessWorkflow=()=>true;w.WORKFLOW_SECTIONS={ui_library:{title:'用例库'},ui_reports:{title:'我的测试'},dashboard:{title:'首页'}};
 w.saveEditorBeforeNavigation=()=>new Promise(resolve=>{done=resolve;});w.hasOpenEditor=()=>false;w.closeTransientUiForNavigation=()=>{};w.setActiveWorkflow=key=>{w.activeWorkflow=key;};w.resetYamlToolbarForManager=()=>{};w.applyLazyLoadForSection=()=>{};
 const shell=fs.readFileSync('js/agent-status.js','utf8');w.eval(shell.slice(shell.indexOf('async function activateWorkflow('),shell.indexOf('\nfunction ',shell.indexOf('async function activateWorkflow(')+1)));
 const first=w.activateWorkflow('ui_library');w.confirmUiCaseDraftLeave=()=>false;await w.activateWorkflow('ui_reports');done(true);await first;assert.equal(w.activeWorkflow,'ui_start');
});

test('explicit logout asks before discarding unsaved draft and cancellation sends no request',async t=>{
 const {w}=fixture(t);let requests=0,disposes=0,confirmations=0,allow=false;
 w.UiCaseDraft={mount:()=>({dispose:()=>disposes++,hasUnsavedChanges:()=>true})};w.confirm=()=>{confirmations++;return allow;};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ui_library';w.showUiSimpleWorkflow();w.document.querySelector('[data-ui-create-case]').click();w.document.querySelector('[data-ui-case-source="text"]').click();
 w.apiRequest=async()=>{requests++;return {ok:true};};w.clearAuthSession=()=>{disposes++;};const source=fs.readFileSync('js/auth.js','utf8');w.eval(source.slice(source.indexOf('async function doLogout()')));
 await w.doLogout();assert.equal(confirmations,1);assert.equal(requests,0);assert.equal(disposes,0);
 allow=true;await w.doLogout();assert.equal(confirmations,2);assert.equal(requests,1);assert.equal(disposes,1);
});

test('a forced 401 logout is not blocked by the unsaved draft confirmation',async t=>{
 const {w}=fixture(t);let cleared=0,confirmed=0;w.clearAuthSession=()=>{cleared++;};w.confirmUiCaseDraftLeave=()=>{confirmed++;return false;};w.showToast=()=>{};
 const source=fs.readFileSync('js/utils.js','utf8');w.eval(source.slice(source.indexOf('function forceLogoutWithMessage('),source.indexOf('\nasync function aiRequest(',source.indexOf('function forceLogoutWithMessage('))));
 assert.throws(()=>w.forceLogoutWithMessage('会话失效'),error=>error.status===401);assert.equal(cleared,1);assert.equal(confirmed,0);
});

test('environment section preserves real model and server checks behind one disclosure',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();
 const section=w.document.querySelector('[data-ui-week-one]');assert.ok(section);assert.equal(section.open,false);assert.match(section.querySelector('summary').textContent,/环境与模型检查/);assert.ok(section.querySelector('[data-week-probe]'));assert.ok(section.querySelector('[data-week-smoke]'));assert.ok(section.querySelector('[data-week-snapshot]'));assert.match(section.textContent,/消耗/);
});

test('environment section explains unavailable permissions',async t=>{
 const {w}=fixture(t);w.UiCaseDraft={mount:()=>({dispose(){}})};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';
 w.currentAccessProfile={user_id:'limited',scope:{ui_apps:['app']}};w.hasPermission=p=>p==='platform.configure';w.canAccessWorkflow=()=>false;w.showAiPractice();const guide=w.document.querySelector('[data-ui-week-one]');
 assert.equal(guide,null);assert.equal(w.document.querySelector('[data-ui-week-two]'),null);
});

test('first-week snapshot directs users to their authorized check history',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const snapshot=w.document.querySelector('[data-week-snapshot]');assert.ok(snapshot);assert.match(snapshot.textContent,/本人检查记录/);assert.match(snapshot.textContent,/按账号权限/);assert.doesNotMatch(snapshot.textContent,/已认证.*业务.*200/);
});

test('API task link remains keyboard reachable with visible focus styling',async t=>{
 const {w}=fixture(t);w.UiCaseDraft={mount:()=>({dispose(){}})};w.canAccessWorkflow=()=>true;
 const style=w.document.createElement('style');style.textContent=fs.readFileSync('css/ui-case-draft.css','utf8');w.document.head.append(style);
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();
 const link=w.document.querySelector('[data-practice-api]');assert.equal(link.tagName,'A');assert.equal(link.getAttribute('href'),'/api-test/');link.focus();assert.equal(w.document.activeElement,link);
 assert.match(style.textContent,/\.ui-practice-tasks a:focus-visible/);
});


test('week one model probe loads persisted evidence and keeps vision review separate from request success',async t=>{
 const {w}=fixture(t);const calls=[];let reviewed=false;w.canAccessWorkflow=()=>true;
 w.apiRequest=async(path,options={})=>{calls.push([path,options]);if(path==='/ui-executions/availability')return {ok:true,enabled:true};
 if(path==='/platform/week1-probes'&&!options.method)return {ok:true,results:[{id:'probe_1',mode:'vision',created_at:'2026-11-05',model:'vision-model',source:'platform_direct',status:'success',answer:'左红矩形，右蓝圆',manual_check:reviewed?'passed':'pending'}]};
 if(path==='/platform/week1-probes/confirm'){reviewed=true;return {ok:true};}return {ok:true,result:{id:'text_1',mode:'text',status:'success',source:'platform_direct',model:'text-model'}};};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();
 const guide=w.document.querySelector('[data-ui-week-one]');const panel=guide.querySelector('[data-week-probe]');assert.ok(panel);assert.match(panel.textContent,/消耗/);
 panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 assert.match(panel.textContent,/vision-model/);assert.match(panel.textContent,/待人工核对/);assert.match(panel.textContent,/左.*红.*矩形.*右.*蓝.*圆/);
 panel.querySelector('[data-probe-review="true"]').click();await wait();await wait();
 assert.equal(calls.find(([path])=>path==='/platform/week1-probes/confirm')[1].body.correct,true);assert.match(panel.textContent,/人工核对通过/);
});

test('week one probe denies unprivileged execution and ignores stale responses',async t=>{
 const {w}=fixture(t);let resolve,gets=0;w.canAccessWorkflow=()=>true;w.hasPermission=p=>p==='ui.view';
 w.apiRequest=async(path)=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};gets++;return new Promise(r=>resolve=r);};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';assert.equal(w.showAiPractice(),true);let panel=w.document.querySelector('[data-week-probe]');assert.equal(panel,null);assert.equal(gets,0);
 w.hasPermission=()=>true;w.showAiPractice();panel=w.document.querySelector('[data-week-probe]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();
 w.currentAccessProfile={user_id:'u2'};resolve({ok:true,results:[{id:'x',mode:'text',status:'success',answer:'previous-account-secret'}]});await wait();assert.doesNotMatch(panel.textContent,/previous-account-secret/);
});

test('model probe starts only one real request while busy and displays saved failure evidence',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let resolve,runCount=0,failed=false;
 w.apiRequest=async(path,options={})=>{
  if(path==='/ui-executions/availability')return {ok:true,enabled:true};
  if(path==='/platform/week1-probes'&&options.method==='POST'){runCount++;return new Promise(r=>resolve=r);}
  if(path==='/platform/week1-probes')return {ok:true,results:failed?[{id:'failure',mode:'text',status:'failed',model:'qwen-plus',source:'platform_direct',error_code:'missing_config'}]:[]};
  throw Error('unexpected');
 };
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week-probe]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 const button=panel.querySelector('[data-probe-run="text"]');button.click();button.click();assert.equal(runCount,1);assert.equal(button.disabled,true);
 failed=true;resolve({ok:true});await wait();await wait();assert.match(panel.textContent,/调用失败/);assert.match(panel.textContent,/配置/);assert.equal(button.disabled,false);
});

test('fixed probe picture uses authenticated same-origin request and does not attach after account change',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let resolve,request;
 w.nativeFetch=(path,options)=>{request={path,options};return new Promise(r=>resolve=r);};
 w.apiRequest=async(path)=>path==='/ui-executions/availability'?{ok:true,enabled:true}:{ok:true,results:[]};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week-probe]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();
 assert.equal(request.path,'/api/platform/week1-probes/fixture');assert.ok(request.options.headers.has('Authorization'));
 w.currentAccessProfile={user_id:'other'};resolve({ok:true,headers:{get:()=> 'image/png'},blob:async()=>new w.Blob(['image'],{type:'image/png'})});await wait();assert.equal(panel.querySelector('[data-probe-fixture]').hasAttribute('src'),false);
});

test('text probe distinguishes a received answer from matching CONNECTED and explains missing key',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;
 w.apiRequest=async(path)=>path==='/ui-executions/availability'?{ok:true,enabled:true}:{ok:true,results:[{id:'t',mode:'text',status:'success',model:'qwen-plus',source:'platform_direct',answer:'hello',text_check:'failed'},{id:'f',mode:'vision',status:'failed',error_code:'missing_key'}]};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week-probe]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 assert.match(panel.textContent,/文本 · 调用成功/);assert.match(panel.textContent,/文本结果.*不匹配/);assert.match(panel.textContent,/未配置.*Key/);assert.match(panel.textContent,/missing_key/);
});

test('model probe requires complete app scope even with platform configure permission',async t=>{
 const {w}=fixture(t);w.currentAccessProfile={user_id:'restricted',scope:{ui_apps:['app-one']}};w.canAccessWorkflow=()=>true;w.hasPermission=()=>true;let calls=0;
 w.apiRequest=async(path)=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};calls++;return {ok:true,results:[]};};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week-probe]');assert.equal(panel,null);assert.equal(calls,0);
});

test('reviewed vision result can be corrected and displays the confirmation time',async t=>{
 const {w}=fixture(t);w.currentAccessProfile={user_id:'u1',scope:{ui_apps:'*'}};w.canAccessWorkflow=()=>true;let state='passed',confirmed;
 w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(path.endsWith('/confirm')){confirmed=options.body;state='failed';return {ok:true};}return {ok:true,results:[{id:'vision',mode:'vision',status:'success',model:'qwen',source:'platform_direct',answer:'红矩形，蓝圆',manual_check:state,confirmed_at:'2026-11-05T03:00:00Z'}]};};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week-probe]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 assert.match(panel.textContent,/核对时间/);assert.match(panel.textContent,/2026/);panel.querySelector('[data-probe-review="false"]').click();await wait();await wait();assert.equal(confirmed.correct,false);assert.match(panel.textContent,/人工核对不通过/);
});

test('week one smoke history explains expected failures and preserves real result details',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let calls=[];
 w.apiRequest=async(path,options={})=>{calls.push([path,options]);if(path==='/ui-executions/availability')return {ok:true,enabled:true};
 if(path==='/platform/week1-smoke')return {ok:true,results:[{id:'s1',kind:'api',status:'passed',created_at:'2026-11-05',checks:[{name:'匿名正常预期',expected:4009,actual:4009,exit_code:0,status:'passed'},{name:'故意失败',expected:999,actual:4009,exit_code:1,status:'passed'}]},{id:'s2',kind:'ui',status:'blocked',created_at:'2026-11-05',checks:[]},{id:'s3',kind:'api',status:'running',created_at:'2026-11-05',checks:[]}]};
 return {ok:true,results:[]};};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week-smoke]');assert.ok(panel);assert.match(panel.textContent,/匿名.*4009/);assert.match(panel.textContent,/认证.*成功/);assert.match(panel.textContent,/故意失败.*预期/);
 panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 assert.match(panel.textContent,/API/);assert.match(panel.textContent,/UI/);assert.match(panel.textContent,/2026-11-05/);assert.match(panel.textContent,/4009/);assert.match(panel.textContent,/999/);assert.match(panel.textContent,/退出码.*1/);assert.match(panel.textContent,/阻塞/);assert.match(panel.textContent,/进行中/);
 assert.equal(calls.filter(([path])=>path==='/platform/week1-smoke').length,1);
});

test('week one smoke runs one fixed kind at a time and shows returned evidence',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let resolve,posts=[];
 w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(path==='/platform/week1-smoke'&&options.method==='POST'){posts.push(options.body);return new Promise(r=>resolve=r);}return {ok:true,results:[]};};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week-smoke]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 const api=panel.querySelector('[data-smoke-run="api"]'),ui=panel.querySelector('[data-smoke-run="ui"]');api.click();ui.click();assert.equal(posts.length,1);assert.equal(posts[0].kind,'api');assert.deepEqual(Object.keys(posts[0]),['kind']);assert.equal(api.disabled,true);assert.equal(ui.disabled,true);
 resolve({ok:true,result:{id:'new',kind:'api',status:'blocked',created_at:'now',checks:[{name:'依赖检查',expected:'可用',actual:'缺少依赖',exit_code:2,status:'blocked'}]}});await wait();await wait();assert.match(panel.textContent,/阻塞/);assert.match(panel.textContent,/缺少依赖/);assert.equal(api.disabled,false);
});

test('week one smoke hides controls without complete scope and ignores old account results',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;w.currentAccessProfile={user_id:'limited',scope:{ui_apps:['x']}};let resolve,calls=0;
 w.apiRequest=async(path)=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};calls++;return new Promise(r=>resolve=r);};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();let panel=w.document.querySelector('[data-week-smoke]');assert.equal(panel,null);assert.equal(calls,0);
 w.currentAccessProfile={user_id:'u1',scope:{ui_apps:'*'}};w.showAiPractice();panel=w.document.querySelector('[data-week-smoke]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();assert.equal(calls,1);
 w.currentAccessProfile={user_id:'other',scope:{ui_apps:'*'}};resolve({ok:true,results:[{kind:'api',status:'passed',created_at:'previous-account-secret'}]});await wait();assert.doesNotMatch(panel.textContent,/previous-account-secret/);
});

test('week one smoke retains a returned result when refreshing history fails, and renders data as text',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let gets=0;
 w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(path==='/platform/week1-smoke'&&options.method==='POST')return {ok:true,result:{id:'new',kind:'ui',status:'passed',created_at:'now',artifacts:'saved',checks:[{name:'<img src=x onerror=alert(1)>',expected:'正确',actual:'正确',exit_code:0,status:'passed'}]}};
 if(path==='/platform/week1-smoke'&&++gets>1)throw Error('network');return {ok:true,results:[]};};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week-smoke]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();panel.querySelector('[data-smoke-run="ui"]').click();await wait();await wait();
 assert.match(panel.textContent,/UI 冒烟 · 通过/);assert.match(panel.textContent,/已保存在服务器/);assert.match(panel.textContent,/本次结果已返回.*历史刷新失败/);assert.equal(panel.querySelector('img'),null);assert.match(panel.textContent,/<img/);
});

test('week one environment preflight uses only the fixed kind and distinguishes endpoint reachability',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let body;
 w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(path==='/platform/week1-smoke'&&options.method==='POST'){body=options.body;return {ok:true,result:{id:'env1',kind:'environment',status:'passed',created_at:'now',checks:[{name:'千问端点网络',expected:'有响应',actual:'HTTP 404',exit_code:0,status:'passed'}]}};}return {ok:true,results:[]};};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week-smoke]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 assert.match(panel.textContent,/网络.*模型认证/);panel.querySelector('[data-smoke-run="environment"]').click();await wait();await wait();assert.equal(body.kind,'environment');assert.deepEqual(Object.keys(body),['kind']);assert.match(panel.textContent,/环境体检 · 通过/);assert.match(panel.textContent,/HTTP 404/);
});

test('week one UI artifact uses authenticated fixed endpoints for image preview and trace download',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='a'.repeat(32),requests=[],downloads=[];
 w.apiRequest=async(path)=>path==='/ui-executions/availability'?{ok:true,enabled:true}:{ok:true,results:[{id,kind:'ui',status:'passed',created_at:'now',artifacts:'saved',checks:[]}]};
 w.nativeFetch=async(path,options)=>{requests.push([path,options]);let zip=path.includes('negative-trace');return {ok:true,status:200,headers:{get:key=>key==='content-type'?(zip?'application/zip':'image/png'):null},blob:async()=>new w.Blob(['content'],{type:zip?'application/zip':'image/png'})};};
 w.HTMLAnchorElement.prototype.click=function(){downloads.push(this.download);};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week-smoke]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 panel.querySelector('[data-smoke-artifact="positive-screenshot"]').click();await wait();await wait();const img=panel.querySelector('[data-smoke-preview]');assert.ok(img);assert.equal(img.src,'blob:test');assert.equal(requests[0][0],'/api/platform/week1-smoke/artifact?id='+id+'&item=positive-screenshot');assert.ok(requests[0][1].headers.has('Authorization'));assert.equal(requests[0][1].redirect,'error');
 panel.querySelector('[data-smoke-artifact="negative-trace"]').click();await wait();await wait();assert.deepEqual(downloads,['第1周-故意失败-trace.zip']);assert.match(requests[1][0],/negative-trace/);
 assert.match(panel.textContent,/截图可预览/);assert.match(panel.textContent,/HTTP.*浏览器.*拦截/);assert.match(panel.textContent,/HTTPS.*联系管理员/);assert.match(panel.textContent,/已发起.*查看浏览器下载记录/);assert.doesNotMatch(panel.textContent,/下载成功/);
});

test('week one private artifacts reject invalid IDs and stale account responses',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='b'.repeat(32);let resolve,requests=0,released=[];
 w.apiRequest=async(path)=>path==='/ui-executions/availability'?{ok:true,enabled:true}:{ok:true,results:[{id,kind:'ui',status:'passed',created_at:'now',artifacts:'partial',checks:[]},{id:'https://evil.example',kind:'ui',status:'passed',artifacts:'saved',checks:[]}]};
 w.nativeFetch=()=>{requests++;return new Promise(r=>resolve=r);};w.URL.revokeObjectURL=url=>released.push(url);
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week-smoke]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 assert.equal(panel.querySelectorAll('[data-smoke-artifact="positive-screenshot"]').length,1);panel.querySelector('[data-smoke-artifact="positive-screenshot"]').click();assert.equal(requests,1);
 w.currentAccessProfile={user_id:'u2',scope:{ui_apps:'*'}};w.resetUiSimpleWorkflow();resolve({ok:true,status:200,headers:{get:key=>key==='content-type'?'image/png':null},blob:async()=>new w.Blob(['image'],{type:'image/png'})});await wait();await wait();assert.equal(panel.querySelector('[data-smoke-preview]'),null);assert.deepEqual(released,[]);
});

test('week one screenshots release preview URLs when history is refreshed or page is left',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='c'.repeat(32),revoked=[];let count=0;
 w.apiRequest=async(path)=>path==='/ui-executions/availability'?{ok:true,enabled:true}:{ok:true,results:[{id,kind:'ui',status:'passed',artifacts:'saved',checks:[]}]};
 w.nativeFetch=async()=>({ok:true,status:200,headers:{get:key=>key==='content-type'?'image/png':null},blob:async()=>new w.Blob(['image'],{type:'image/png'})});
 w.URL.createObjectURL=()=>`blob:preview-${++count}`;w.URL.revokeObjectURL=url=>revoked.push(url);
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week-smoke]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 panel.querySelector('[data-smoke-artifact="positive-screenshot"]').click();await wait();await wait();panel.querySelector('[data-smoke-artifact="negative-screenshot"]').click();await wait();await wait();assert.ok(revoked.includes('blob:preview-1'));
 panel.querySelector('[data-smoke-refresh]').click();await wait();await wait();assert.ok(revoked.includes('blob:preview-2'));
 panel.querySelector('[data-smoke-artifact="positive-screenshot"]').click();await wait();await wait();w.resetUiSimpleWorkflow();assert.ok(revoked.includes('blob:preview-3'));
});

test('week one artifact timeout reports an actionable failure and re-enables controls',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const id='d'.repeat(32);let timer;
 w.apiRequest=async(path)=>path==='/ui-executions/availability'?{ok:true,enabled:true}:{ok:true,results:[{id,kind:'ui',status:'passed',artifacts:'saved',checks:[]}]};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week-smoke]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 w.setTimeout=fn=>{timer=fn;return 1;};w.clearTimeout=()=>{};w.nativeFetch=(_,options)=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Error('timeout'))));
 const button=panel.querySelector('[data-smoke-artifact="positive-screenshot"]');button.click();assert.equal(button.disabled,true);timer();await wait();await wait();assert.match(panel.textContent,/读取证据超时/);assert.equal(button.disabled,false);
});

test('practice results use local readable time and keep detailed checks collapsed by default',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const result={id:'r',created_at:'2026-11-05T10:00:00Z',status:'passed',source:'platform_record',source_verification:'server_authorized_platform_read',execution_id:'source',execution_case_id:'case',business_requests:0,total:12,original_assertions:['status_code','json_path','schema'].map(type=>({type,passed:true})),checks:Array.from({length:12},(_,i)=>({id:'check'+i,status:'passed'}))};
 w.apiRequest=async path=>path==='/ui-executions/availability'?{ok:true,enabled:true}:{ok:true,results:[result]};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week2-real]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();const row=panel.querySelector('[data-week2-real-history] details');assert.equal(row.open,false);assert.match(row.querySelector('summary').textContent,/12 项/);assert.doesNotMatch(row.querySelector('summary').textContent,/T10:00:00Z/);assert.match(row.textContent,/check0/);assert.match(panel.textContent,/不.*AI/);
});

test('historical root contract check shows field presence and controls without implying a complete contract',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;
 const result={id:'r',status:'passed',source:'platform_record',source_verification:'server_authorized_platform_read',business_requests:0,contract_check:{status:'checked',scope:'root_required_only',fields:[{name:'code',present:true,negative_control_rejected:true},{name:'msg',present:true,negative_control_rejected:true},{name:'data',present:true,negative_control_rejected:false}]}};
 w.apiRequest=async path=>path==='/ui-executions/availability'?{ok:true,enabled:true}:{ok:true,results:[result]};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week2-real]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 const check=panel.querySelector('[data-week2-contract-check]');assert.ok(check);assert.match(check.textContent,/历史响应根字段/);assert.match(check.textContent,/code.*脱敏记录中存在.*删除.*拦截/);assert.match(check.textContent,/data.*脱敏记录中存在.*删除.*未拦截/);assert.match(check.textContent,/仅.*根级.*存在/);assert.match(check.textContent,/分析时的合同和脱敏记录快照/);assert.match(check.textContent,/不代表完整合同/);assert.match(check.textContent,/不代表.*实时/);assert.match(check.textContent,/内存副本.*不修改.*记录/);
 result.contract_check.fields.forEach(value=>value.negative_control_rejected=null);result.contract_check.fields[1].present=false;panel.querySelector('[data-week2-real-refresh]').click();await wait();await wait();assert.match(panel.querySelector('[data-week2-contract-check]').textContent,/msg.*脱敏记录中未见.*未进行/);
});

test('historical root contract check fails closed for old and malformed results',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let results=[{id:'old'}, {id:'unavailable',contract_check:{status:'unavailable',scope:'root_required_only',fields:[]}}, {id:'malformed',contract_check:{status:'checked',scope:'root_required_only',fields:[{name:'<secret>',present:true,negative_control_rejected:true}]}}, {id:'contradictory',source:'platform_record',source_verification:'server_authorized_platform_read',contract_check:{status:'checked',scope:'root_required_only',fields:[{name:'code',present:true,negative_control_rejected:true},{name:'msg',present:false,negative_control_rejected:true},{name:'data',present:true,negative_control_rejected:true}]}}];
 w.apiRequest=async path=>path==='/ui-executions/availability'?{ok:true,enabled:true}:{ok:true,results};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week2-real]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();
 const checks=[...panel.querySelectorAll('[data-week2-contract-check]')];assert.equal(checks.length,4);for(const check of checks){assert.match(check.textContent,/无法比较/);assert.doesNotMatch(check.textContent,/<secret>|删除后拦截/);}assert.equal(panel.querySelector('img'),null);
});

test('historical timeout is rendered only after server returns an authorized record',async t=>{
 const {w}=fixture(t),id='11111111-1111-4111-8111-111111111111';w.canAccessWorkflow=()=>true;w.apiRequest=async path=>path==='/platform/practice-legacy-evidence'?{ok:true,records:[{key:'timeout',execution_id:id,summary:'历史回归中文字建模聊天超时，不是反馈接口超时。'}]}:{ok:true,results:[]};
 w.activeWorkflow='ai_practice';w.showAiPractice();const section=w.document.querySelector('[data-practice-legacy-evidence]');assert.doesNotMatch(section.textContent,/文字建模聊天/);section.open=true;section.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.match(section.textContent,/文字建模聊天.*不是反馈接口超时/);assert.equal(section.querySelector('a').getAttribute('href'),'/api-test/#/runs?executionId='+id);
});

test('existing evidence requires API permission and prevents stale account record links',async t=>{
 const {w}=fixture(t),id='11111111-1111-4111-8111-111111111111';w.canAccessWorkflow=()=>true;w.hasPermission=p=>p==='platform.configure';await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();assert.equal(w.document.querySelector('[data-ui-week-two]'),null);
 w.hasPermission=()=>true;w.apiRequest=async()=>({ok:true,records:[{key:'trace',execution_id:id,summary:'授权追踪'}]});w.showAiPractice();const section=w.document.querySelector('[data-practice-legacy-evidence]');section.open=true;section.dispatchEvent(new w.Event('toggle'));await wait();await wait();const link=section.querySelector('a');assert.ok(link);w.currentAccessProfile={user_id:'other',scope:{ui_apps:'*'}};assert.equal(link.dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true})),false);
});

test('week two provides reproducible steps and loads checks only with authorized feedback evidence',async t=>{
 const {w}=fixture(t),id='11111111-1111-4111-8111-111111111111';w.canAccessWorkflow=()=>true;w.apiRequest=async path=>path==='/platform/practice-legacy-evidence'?{ok:true,records:[{key:'feedback',execution_id:id,summary:'已授权',checks:Array.from({length:17},(_,i)=>'检查 '+i)}]}:{ok:true,results:[]};w.activeWorkflow='ai_practice';w.showAiPractice();const guide=w.document.querySelector('[data-ui-week-two]'),detail=guide.querySelector('[data-week2-howto]');assert.ok(detail);assert.equal(detail.querySelectorAll('[data-week2-check]').length,0);
 const section=guide.querySelector('[data-practice-legacy-evidence]');section.open=true;section.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.equal(detail.querySelectorAll('[data-week2-check]').length,17);assert.match(detail.textContent,/原始执行记录/);assert.match(detail.textContent,/本地数据处理/);assert.match(detail.textContent,/不.*业务请求/);assert.match(detail.textContent,/新.*venv/);assert.match(detail.textContent,/unittest discover/);assert.match(detail.textContent,/--evidence/);assert.match(detail.textContent,/--execution-id/);assert.match(detail.textContent,/--case-id/);assert.match(detail.textContent,/未独立核验.*来源/);assert.match(detail.textContent,/不是.*业务.*合同/);
 w.apiRequest=async()=>({ok:true,records:[]});section.querySelector('[data-legacy-refresh]').click();await wait();await wait();assert.equal(detail.querySelectorAll('[data-week2-check]').length,0);
});

test('week two real evidence exercise runs once, renders returned check detail and preserves result if history fails',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let resolve,posts=0,gets=0;
 w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(path==='/platform/week2-real'&&options.method==='POST'){posts++;assert.deepEqual(Object.keys(options.body),[]);return new Promise(r=>resolve=r);}if(path==='/platform/week2-real'&&++gets>1)throw Error('network');return {ok:true,results:[]};};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week2-real]');assert.ok(panel);panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.match(panel.textContent,/默认反馈记录/);assert.match(panel.textContent,/其他真实反馈执行记录/);const button=panel.querySelector('[data-week2-real-run]');button.click();button.click();assert.equal(posts,1);assert.equal(button.disabled,true);
 resolve({ok:true,result:{id:'new',status:'failed',source:'platform_record',source_verification:'server_authorized_platform_read',execution_id:'run-one',total:1,business_requests:0,original_assertions:[{type:'json_path',passed:true}],checks:[{id:'<img src=x>',status:'failed'}],cleanup:'not_applicable_no_business_data_created'}});await wait();await wait();assert.match(panel.textContent,/run-one/);assert.match(panel.textContent,/失败/);assert.match(panel.textContent,/历史.*失败/);assert.match(w.document.querySelector('[data-week2-progress]').textContent,/待核验/);assert.match(panel.textContent,/原始.*断言/);assert.match(panel.textContent,/写入与清理结果.*执行记录/);assert.match(panel.textContent,/<img src=x>/);assert.equal(panel.querySelector('img'),null);assert.equal(button.disabled,false);
});
test('week two real exercise controls require full UI scope and ignore stale account results',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;w.currentAccessProfile={user_id:'limited',scope:{ui_apps:['app']}};let calls=0,resolve;
 w.apiRequest=async path=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};calls++;return new Promise(r=>resolve=r);};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();let panel=w.document.querySelector('[data-week2-real]');assert.equal(panel,null);assert.equal(calls,0);
 w.currentAccessProfile={user_id:'u1',scope:{ui_apps:'*'}};w.showAiPractice();panel=w.document.querySelector('[data-week2-real]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();w.currentAccessProfile={user_id:'other',scope:{ui_apps:'*'}};resolve({ok:true,results:[{id:'secret',execution_id:'previous-secret'}]});await wait();assert.doesNotMatch(panel.textContent,/previous-secret/);
});

test('week two evidence analysis shows success only when source, original assertions and all checks are verified',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const result={id:'r',created_at:'now',status:'passed',code_sha256:'abc123',source:'platform_record',source_verification:'server_authorized_platform_read',execution_id:'source-id',execution_case_id:'case-id',business_requests:0,total:17,original_assertions:['status_code','json_path','schema','json_path'].map(type=>({type,passed:true})),checks:Array.from({length:17},(_,i)=>({id:'check'+i,status:'passed'}))};
 w.apiRequest=async path=>path==='/ui-executions/availability'?{ok:true,enabled:true}:{ok:true,results:[result]};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week2-real]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.match(panel.textContent,/本次分析 · 通过/);assert.match(panel.textContent,/abc123/);assert.match(panel.textContent,/source-id/);assert.match(panel.textContent,/17 项/);
 result.original_assertions=result.original_assertions.filter(item=>item.type!=='schema');panel.querySelector('[data-week2-real-refresh]').click();await wait();await wait();assert.match(panel.textContent,/结果待核实/);assert.doesNotMatch(panel.textContent,/本次分析 · 通过/);
 result.original_assertions.push({type:'schema',passed:false});panel.querySelector('[data-week2-real-refresh]').click();await wait();await wait();assert.match(panel.textContent,/结果待核实/);assert.doesNotMatch(panel.textContent,/本次分析 · 通过/);
});

test('week two lets colleagues select another authorized real record only with two valid UUIDs',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let bodies=[];w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(path==='/platform/week2-real'&&options.method==='POST'){bodies.push(options.body);return {ok:true,result:{id:'new',status:'blocked'}};}return {ok:true,results:[]};};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week2-real]'),execution=panel.querySelector('[data-week2-execution-id]'),caseId=panel.querySelector('[data-week2-case-id]'),button=panel.querySelector('[data-week2-real-run]');execution.value='invalid';button.click();assert.equal(bodies.length,0);assert.match(panel.textContent,/执行.*编号.*UUID/);execution.value='de803f8b-e18b-4e67-986d-b14ce737b8aa';caseId.value='d8f37256-f90b-4f22-950f-5be70a744c4a';button.click();await wait();await wait();assert.equal(bodies.length,1);assert.equal(bodies[0].execution_id,execution.value);assert.equal(bodies[0].case_id,caseId.value);assert.deepEqual(Object.keys(bodies[0]).sort(),['case_id','execution_id']);
});

test('week two can use an execution ID or same-origin run link without a case ID',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;const bodies=[];w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(path==='/platform/week2-real'&&options.method==='POST'){bodies.push(options.body);return {ok:true,result:{id:'n',status:'blocked'}};}return {ok:true,results:[]};};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const panel=w.document.querySelector('[data-week2-real]'),execution=panel.querySelector('[data-week2-execution-id]'),caseId=panel.querySelector('[data-week2-case-id]'),button=panel.querySelector('[data-week2-real-run]');
 const id='de803f8b-e18b-4e67-986d-b14ce737b8aa';execution.value=`http://localhost/api-test/#/runs?executionId=${id}`;button.click();await wait();await wait();assert.equal(bodies.length,1);assert.equal(bodies[0].execution_id,id);assert.deepEqual(Object.keys(bodies[0]),['execution_id']);
 execution.value=`http://untrusted.example/api-test/#/runs?executionId=${id}`;button.click();assert.equal(bodies.length,1);assert.match(panel.textContent,/执行.*编号.*链接/);execution.value='';caseId.value='d8f37256-f90b-4f22-950f-5be70a744c4a';button.click();assert.equal(bodies.length,1);assert.match(panel.textContent,/子用例.*执行/);
});

test('trace evidence is displayed only when returned by server authorization',async t=>{
 const {w}=fixture(t),id='11111111-1111-4111-8111-111111111111';w.canAccessWorkflow=()=>true;w.apiRequest=async()=>({ok:true,records:[{key:'trace',execution_id:id,summary:'历史追踪记录，请求头已发送，日志关联未验证。'}]});w.activeWorkflow='ai_practice';w.showAiPractice();const section=w.document.querySelector('[data-practice-legacy-evidence]');section.open=true;section.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.match(section.textContent,/日志关联未验证/);const link=section.querySelector('a');assert.equal(link.getAttribute('href'),'/api-test/#/runs?executionId='+id);w.currentAccessProfile={user_id:'other',scope:{ui_apps:'*'}};assert.equal(link.dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true})),false);
});

test('week two task progress follows latest authorized analysis and opens its exact evidence',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let records=[];
 w.apiRequest=async path=>path==='/ui-executions/availability'?{ok:true,enabled:true}:{ok:true,results:records};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const guide=w.document.querySelector('[data-ui-week-two]'),panel=guide.querySelector('[data-week2-real]'),progress=()=>[...guide.querySelectorAll('[data-week2-progress]')];
 panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.equal(progress().length,1);assert.match(progress()[0].textContent,/暂无.*待验证/);
 const checkIds=Array.from({length:17},(_,i)=>({id:'check'+i,status:'passed'}));const base={id:'result1',created_at:'2026-11-05T10:00:00Z',status:'passed',source:'platform_record',source_verification:'server_authorized_platform_read',execution_id:'source-one',business_requests:0,total:17,original_assertions:['status_code','json_path','schema'].map(type=>({type,passed:true})),checks:checkIds};
 records=[base];panel.querySelector('[data-week2-real-refresh]').click();await wait();await wait();for(const p of progress()){assert.match(p.textContent,/数据处理.*通过/);assert.match(p.textContent,/2026-11-05/);assert.match(p.textContent,/业务.*审核.*待/);assert.ok(p.querySelector('button'));}panel.open=false;progress()[0].querySelector('button').click();assert.equal(panel.open,true);assert.equal(panel.querySelector('[data-week2-real-history] details').open,true);
 records=[{...base,id:'result2',status:'failed',checks:checkIds.map((v,i)=>i===0?{...v,status:'failed'}:v)}];panel.querySelector('[data-week2-real-refresh]').click();await wait();await wait();assert.match(progress()[0].textContent,/数据处理.*失败/);
 records=[{...base,id:'result3',status:'blocked',source_verification:'pending',checks:[],total:0}];panel.querySelector('[data-week2-real-refresh]').click();await wait();await wait();assert.match(progress()[0].textContent,/数据处理.*阻塞/);
 records=[{id:'result4',status:'blocked',created_at:'2026-11-05',message:'权限不足'}];panel.querySelector('[data-week2-real-refresh]').click();await wait();await wait();assert.match(progress()[0].textContent,/待核验/);assert.doesNotMatch(progress()[0].textContent,/result4|2026-11-05/);assert.equal(progress()[0].querySelector('button'),null);
});
test('week two progress becomes unverified on history errors and clears on access reset',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let reject=false;const record={id:'result',created_at:'now',status:'passed',source:'platform_record',source_verification:'server_authorized_platform_read',execution_id:'a',business_requests:0,total:12,original_assertions:['status_code','json_path','schema'].map(type=>({type,passed:true})),checks:Array.from({length:12},(_,i)=>({id:'c'+i,status:'passed'}))};w.apiRequest=async path=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(reject)throw Error('history failure');return {ok:true,results:[record]};};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const guide=w.document.querySelector('[data-ui-week-two]'),panel=guide.querySelector('[data-week2-real]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();const progress=guide.querySelector('[data-week2-progress]');assert.match(progress.textContent,/通过/);reject=true;panel.querySelector('[data-week2-real-refresh]').click();await wait();await wait();assert.match(progress.textContent,/待核验/);assert.doesNotMatch(progress.textContent,/通过/);reject=false;panel.querySelector('[data-week2-real-refresh]').click();await wait();await wait();assert.match(progress.textContent,/通过/);w.currentAccessProfile={user_id:'other',scope:{ui_apps:'*'}};w.resetUiSimpleWorkflow();assert.doesNotMatch(progress.textContent,/通过|result/);assert.doesNotMatch(panel.querySelector('[data-week2-real-history]').textContent,/source-one|now/);
});

test('week two explains when the analysis service is not deployed',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;w.apiRequest=async path=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};throw Object.assign(Error('missing'),{status:404});};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const guide=w.document.querySelector('[data-ui-week-two]'),panel=guide.querySelector('[data-week2-real]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();assert.match(panel.textContent,/分析服务暂不可用.*刷新.*管理员/);assert.match(guide.querySelector('[data-week2-progress]').textContent,/待核验/);assert.doesNotMatch(guide.querySelector('[data-week2-progress]').textContent,/通过/);
});

test('week two clears returned evidence when history denies authorization after analysis',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;let gets=0;w.apiRequest=async(path,options={})=>{if(path==='/ui-executions/availability')return {ok:true,enabled:true};if(path==='/platform/week2-real'&&options.method==='POST')return {ok:true,result:{id:'private',status:'passed',execution_id:'private-source'}};if(path==='/platform/week2-real'&&++gets>1)throw Object.assign(Error('denied'),{status:403});return {ok:true,results:[]};};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const guide=w.document.querySelector('[data-ui-week-two]'),panel=guide.querySelector('[data-week2-real]');panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();panel.querySelector('[data-week2-real-run]').click();await wait();await wait();assert.doesNotMatch(panel.querySelector('[data-week2-real-history]').textContent,/private-source/);assert.match(guide.querySelector('[data-week2-progress]').textContent,/待核验/);assert.match(panel.textContent,/权限/);
});

test('colleague access to the workspace follows UI or API view without exposing admin evidence',async t=>{
 const {w,calls}=fixture(t);const auth=fs.readFileSync('js/auth.js','utf8');w.eval(auth.slice(auth.indexOf('const WORKFLOW_PERMISSIONS'),auth.indexOf('function applyAccessNavigation')));
 let permissions=new Set(['ui.view']);w.hasPermission=p=>permissions.has(p);const requests=[];w.apiRequest=async(path,opts={})=>{requests.push([path,opts.method||'GET']);return {ok:true,enabled:true};};
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';assert.equal(w.canAccessWorkflow('ai_practice'),true);assert.equal(w.showAiPractice(),true);
 let page=w.document.querySelector('[data-practice-workbench]');assert.ok(page);assert.equal(page.querySelector('[data-practice-api]'),null);assert.equal(page.querySelector('[data-practice-workflow="ui_start"]').disabled,false);assert.equal(page.querySelector('[data-practice-workflow="ui_reports"]').disabled,false);assert.equal(page.querySelector('[data-ui-week-one]'),null);assert.equal(page.querySelector('[data-ui-week-two]'),null);assert.equal(requests.filter(([path])=>path.startsWith('/platform/')).length,0);
 const button=page.querySelector('[data-practice-workflow="ui_start"]');permissions.clear();button.click();assert.equal(calls.includes('ui_start'),false);
 permissions=new Set(['api.view']);w.resetUiSimpleWorkflow();w.activeWorkflow='ai_practice';assert.equal(w.canAccessWorkflow('ai_practice'),true);assert.equal(w.showAiPractice(),true);page=w.document.querySelector('[data-practice-workbench]');assert.equal(page.querySelector('[data-practice-api]').getAttribute('href'),'/api-test/');assert.equal(page.querySelector('[data-practice-workflow="ui_start"]').disabled,true);assert.equal(page.querySelector('[data-practice-workflow="ui_reports"]').disabled,true);assert.equal(page.querySelector('[data-ui-week-one]'),null);assert.equal(page.querySelector('[data-ui-week-two]'),null);const link=page.querySelector('[data-practice-api]');permissions.clear();assert.equal(link.dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true})),false);assert.equal(w.canAccessWorkflow('ai_practice'),false);
});

test('workbench administration requires full app scope and feedback evidence also needs API view',async t=>{
 const {w}=fixture(t);const auth=fs.readFileSync('js/auth.js','utf8');w.eval(auth.slice(auth.indexOf('const WORKFLOW_PERMISSIONS'),auth.indexOf('function applyAccessNavigation')));
 let permissions=new Set(['platform.configure']);w.hasPermission=p=>permissions.has(p);w.currentAccessProfile={user_id:'limited',scope:{ui_apps:['one']}};w.activeWorkflow='ai_practice';assert.equal(w.showAiPractice(),true);let page=w.document.querySelector('[data-practice-workbench]');assert.equal(page.querySelector('[data-ui-week-one]'),null);assert.equal(page.querySelector('[data-ui-week-two]'),null);
 w.currentAccessProfile={user_id:'admin',scope:{ui_apps:'*'}};w.showAiPractice();page=w.document.querySelector('[data-practice-workbench]');assert.ok(page.querySelector('[data-ui-week-one]'));assert.equal(page.querySelector('[data-ui-week-two]'),null);
 permissions.add('api.view');w.showAiPractice();page=w.document.querySelector('[data-practice-workbench]');assert.ok(page.querySelector('[data-ui-week-two]'));
});

test('authentication refresh removes visible administrator evidence immediately',async t=>{
 const {w}=fixture(t);w.canAccessWorkflow=()=>true;w.activeWorkflow='ai_practice';w.showAiPractice();assert.ok(w.document.querySelector('[data-ui-week-one]'));assert.ok(w.document.querySelector('[data-ui-week-two]'));
 w.currentAccessProfile={user_id:'u1',scope:{ui_apps:['app']}};w.hasPermission=p=>p==='ui.view';w.resetUiSimpleWorkflow();assert.equal(w.document.querySelector('[data-ui-week-one]'),null);assert.equal(w.document.querySelector('[data-ui-week-two]'),null);
});

test('refreshing the same identity restores working UI and API workspace links',async t=>{
 const {w,calls}=fixture(t);const auth=fs.readFileSync('js/auth.js','utf8');w.eval(auth.slice(auth.indexOf('function acceptAuthSession('),auth.indexOf('function requireUiEditPermission(')));w.eval(auth.slice(auth.indexOf('const WORKFLOW_PERMISSIONS'),auth.indexOf('function applyAccessNavigation')));
 const profile={user_id:'u1',status:'active',permissions:['ui.view','api.view'],scope:{ui_apps:['app']}};w.currentAccessProfile=profile;await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();
 const original=w.document.querySelector('[data-practice-workbench]');w.acceptAuthSession({profile});await wait();await wait();const page=w.document.querySelector('[data-practice-workbench]');assert.ok(page);assert.notEqual(page,original);assert.equal(page.querySelector('[data-practice-workflow="ui_start"]').disabled,false);assert.equal(page.querySelector('[data-practice-workflow="ui_reports"]').disabled,false);assert.equal(page.querySelector('[data-practice-api]').dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true})),true);page.querySelector('[data-practice-workflow="ui_start"]').click();assert.ok(calls.includes('ui_start'));
});

test('identity refresh removes privileged evidence and restores only remaining permitted actions',async t=>{
 const {w}=fixture(t);const auth=fs.readFileSync('js/auth.js','utf8');w.eval(auth.slice(auth.indexOf('function acceptAuthSession('),auth.indexOf('function requireUiEditPermission(')));w.eval(auth.slice(auth.indexOf('const WORKFLOW_PERMISSIONS'),auth.indexOf('function applyAccessNavigation')));
 w.currentAccessProfile={user_id:'u1',status:'active',permissions:['platform.configure','api.view','ui.view'],scope:{ui_apps:'*'}};await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();const oldPage=w.document.querySelector('[data-practice-workbench]');assert.ok(oldPage.querySelector('[data-ui-week-two]'));
 w.acceptAuthSession({profile:{user_id:'u1',status:'active',permissions:['api.view'],scope:{ui_apps:['app']}}});assert.equal(w.document.querySelector('[data-ui-week-one]'),null);assert.equal(w.document.querySelector('[data-ui-week-two]'),null);await wait();await wait();const page=w.document.querySelector('[data-practice-workbench]');assert.ok(page.querySelector('[data-practice-api]'));assert.equal(page.querySelector('[data-practice-workflow="ui_start"]').disabled,true);assert.equal(page.querySelector('[data-ui-week-one]'),null);assert.equal(page.querySelector('[data-ui-week-two]'),null);
});

test('out of order identity refresh cannot restore an older workspace or private evidence',async t=>{
 const {w}=fixture(t);const auth=fs.readFileSync('js/auth.js','utf8');w.eval(auth.slice(auth.indexOf('function acceptAuthSession('),auth.indexOf('function requireUiEditPermission(')));w.eval(auth.slice(auth.indexOf('const WORKFLOW_PERMISSIONS'),auth.indexOf('function applyAccessNavigation')));
 const admin={user_id:'admin',status:'active',permissions:['ui.view','api.view','platform.configure'],scope:{ui_apps:'*'}};w.currentAccessProfile=admin;await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();let resolve;w.apiRequest=()=>new Promise(r=>{resolve=r;});w.acceptAuthSession({profile:admin});
 w.acceptAuthSession({profile:{user_id:'other',status:'active',permissions:['api.view'],scope:{ui_apps:['one']}}});await wait();await wait();resolve({ok:true,enabled:true});await wait();await wait();const page=w.document.querySelector('[data-practice-workbench]');assert.ok(page.querySelector('[data-practice-api]'));assert.equal(page.querySelector('[data-practice-workflow="ui_start"]').disabled,true);assert.equal(page.querySelector('[data-ui-week-one]'),null);assert.equal(page.querySelector('[data-ui-week-two]'),null);
});

test('workspace availability refresh preserves advanced tools disclosure',async t=>{
 const {w}=fixture(t);w.activeWorkflow='ai_practice';const advanced=w.document.querySelector('[data-ui-advanced]');advanced.open=false;
 await w.initUiSimpleAvailability({navigate:false});assert.equal(advanced.open,false);
 advanced.open=true;await w.initUiSimpleAvailability({navigate:false});assert.equal(advanced.open,true);
 advanced.open=false;w.apiRequest=async()=>({ok:true,enabled:false});await w.initUiSimpleAvailability({navigate:false});assert.equal(advanced.open,false);
});

test('availability refresh still opens advanced tools for advanced workflows',async t=>{
 const {w}=fixture(t);w.activeWorkflow='config';const advanced=w.document.querySelector('[data-ui-advanced]');advanced.open=false;
 await w.initUiSimpleAvailability({navigate:false});assert.equal(advanced.open,true);
});
test('stability summary keeps partial results and binds screenshot to its round and session',async t=>{
 const {w}=fixture(t),id='a'.repeat(32),measurement='b'.repeat(32);
 w.canAccessWorkflow=()=>true;
 w.apiRequest=async path=>path==='/ui-executions/availability'?{ok:true,enabled:true}:{ok:true};
 let request,resolveArtifact;
 w.nativeFetch=async(path,options)=>{
   if(path.includes('/artifact?')){request={path,options};return new Promise(done=>{resolveArtifact=done;});}
   return {ok:true,json:async()=>({ok:true,report:{id:measurement,planned:200,attempted:1,unrun:199,passed:1,failed:0,blocked:0,updated:1000,state:'interrupted',median_ms:{ui_catalog_count:1000},rows:[{round:1,scenario:'ui_catalog_count',status:'passed',duration_ms:1000,run_id:id}]}})};
 };
 await w.initUiSimpleAvailability({navigate:false});w.activeWorkflow='ai_practice';w.showAiPractice();
 w.document.querySelector('[data-stability-refresh]').click();await wait();await wait();
 const report=w.document.querySelector('[data-stability-report]');
 assert.match(report.textContent,/未执行 199/);assert.match(w.document.querySelector('[data-stability-state]').textContent,/已中断/);
 Array.from(report.querySelectorAll('button')).find(b=>b.textContent==='查看该次截图').click();
 assert.ok(request.options.headers.has('Authorization'));
 const url=new URL(request.path,'http://localhost');
 assert.deepEqual(Object.fromEntries(url.searchParams),{id,item:'screenshot',measurement,round:'1'});
 w.sessionStorage.setItem('sessionToken','another-session');resolveArtifact({ok:false,status:403});await wait();await wait();
 assert.equal(report.children.length,0);
});

const week7Id='a'.repeat(64), ownWeek7Id='b'.repeat(64);
const week7Source=(id=week7Id)=>({id,title:id===week7Id?'示范搜索':'我的搜索',app:'应用',version:'v2',prd_file_sha256:'c'.repeat(64),clauses:[{id:'R1',text:'返回搜索页'}],prototypes:[{id:'V1',caption:'搜索原型',sha256:'d'.repeat(64)}]});
const week7Data=(id=week7Id,runs=[])=>({ok:true,source:week7Source(id),sources:[week7Id,ownWeek7Id].map(id=>({id,title:week7Source(id).title,version:'v2'})),runs});
const week7Json=(value,status=200)=>({ok:status===200,status,json:async()=>value});
async function openWeek7(w){
 w.activeWorkflow='ai_practice';w.showAiPractice();
 const panel=w.document.querySelector('[data-requirement-design]');assert.ok(panel);
 panel.open=true;panel.dispatchEvent(new w.Event('toggle'));await wait();await wait();return panel;
}
test('requirement design checks full UI and configure access without hiding ordinary entries',async t=>{
 for(const [scope,denied] of [['*','platform.configure'],[['limited'],''],['*','ui.view']]){
  const {w}=fixture(t);w.currentAccessProfile.scope.ui_apps=scope;w.hasPermission=p=>p!==denied;w.activeWorkflow='ai_practice';w.showAiPractice();
  assert.equal(w.document.querySelector('[data-requirement-design]'),null);assert.ok(w.document.querySelector('[data-practice-workflow="ui_start"]'));
 }
 const {w}=fixture(t);let gets=0;w.nativeFetch=async()=>{gets++;return week7Json(week7Data());};
 w.activeWorkflow='ai_practice';w.showAiPractice();assert.ok(w.document.querySelector('[data-requirement-design]'));await wait();assert.equal(gets,0);
});
test('requirement design binds generation to source, retries same request and exports the matched run',async t=>{
 const {w}=fixture(t),posts=[];let generated=false,exported,downloaded;
 const run={id:'x1',status:'completed',created:1730000000,result:{points:[{id:'P1',title:'校验返回',expected:'首页可见',refs:['R1','V1']}],cases:[{id:'C1',title:'返回首页',precondition:'已搜索',steps:['轻触返回'],expected:'进入首页',point_ids:['P1']}],questions:['需要跨端吗？'],evidence:{model:'model-one',request_id:'req-123',usage:{total_tokens:123}},checks:{unreferenced_source_ids:[],uncovered_point_ids:[],review_required:true}}};
 w.nativeFetch=async(path,options={})=>{if(options.method==='POST'){posts.push(JSON.parse(options.body));if(posts.length===1)throw Error('network');generated=true;return week7Json({ok:true,run});}return week7Json(week7Data(week7Id,generated?[run]:[]));};
 w.URL.createObjectURL=blob=>{exported=blob;return 'blob:test';};w.HTMLAnchorElement.prototype.click=function(){downloaded=this.download;};
 const panel=await openWeek7(w);assert.match(panel.textContent,/返回搜索页/);const gen=panel.querySelector('[data-design-generate]');gen.click();await wait();await wait();gen.click();await wait();await wait();
 assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);assert.equal(posts[0].source_id,week7Id);assert.match(posts[0].request_id,/^[a-f0-9]{32}$/);
 for(const text of ['R1','V1','P1','C1','校验返回','轻触返回','进入首页','需要跨端吗','model-one','req-123','人工复核'])assert.ok(panel.textContent.includes(text),text);
 panel.querySelector('[data-design-export]').click();assert.match(downloaded,/\.json$/);const reader=new w.FileReader();const saved=new Promise(resolve=>reader.onload=()=>resolve(JSON.parse(reader.result)));reader.readAsText(exported);const result=await saved;assert.equal(result.run.id,'x1');assert.equal(result.source.id,week7Id);
 assert.equal(panel.querySelector('[data-design-execute]'),null);
});
test('requirement design clears stale source and preview after switching, denial or navigation',async t=>{
 const {w}=fixture(t);let releaseImage,deny=false,signal,imgPath;
 w.nativeFetch=async(path,options)=>{
  if(path.includes('/prototype?')){imgPath=path;signal=options.signal;return new Promise(resolve=>releaseImage=()=>resolve({ok:true,headers:{get:()=> 'image/png'},blob:async()=>new w.Blob(['image'],{type:'image/png'})}));}
  return deny?week7Json({error:'private error'},403):week7Json(week7Data(path.includes(ownWeek7Id)?ownWeek7Id:week7Id));
 };
 const panel=await openWeek7(w);panel.querySelector('[data-design-preview]').click();assert.ok(imgPath.includes('source_id='+week7Id));assert.ok(imgPath.includes('id=V1'));const select=panel.querySelector('[data-design-select]');select.value=ownWeek7Id;select.dispatchEvent(new w.Event('change'));releaseImage();await wait();await wait();
 assert.equal(signal.aborted,true);assert.equal(panel.querySelector('img'),null);assert.equal(select.value,ownWeek7Id);deny=true;panel.querySelector('[data-design-refresh]').click();await wait();await wait();assert.doesNotMatch(panel.textContent,/我的搜索|private error|返回搜索页/);
 w.leaveUiSimpleWorkflow();assert.equal(w.document.querySelector('[data-requirement-design]'),null);
});
test('requirement design uploads PNG with trimmed clauses and selects server response',async t=>{
 const {w}=fixture(t);let body;const paths=[];
 w.nativeFetch=async(path,opts)=>{paths.push(path);if(path.endsWith('/source')){body=JSON.parse(opts.body);return week7Json({ok:true,source_id:ownWeek7Id});}return week7Json(week7Data(path.includes(ownWeek7Id)?ownWeek7Id:week7Id));};
 const panel=await openWeek7(w),form=panel.querySelector('form');form.elements.title.value='设计 A';form.elements.app.value='应用';form.elements.version.value='v1';form.elements.clauses.value='第一条\n\n 第二条 ';
 const png=new w.File(['PNG'],'search.png',{type:'image/png'});Object.defineProperty(form.elements.prototype,'files',{value:[png]});form.dispatchEvent(new w.Event('submit',{cancelable:true}));
 for(let i=0;i<50&&!paths.some(p=>p.includes(ownWeek7Id));i++)await new Promise(r=>setTimeout(r,5));
 assert.deepEqual(body,{title:'设计 A',app:'应用',version:'v1',clauses:['第一条','第二条'],image_base64:'UE5H'});assert.equal(panel.querySelector('[data-design-select]').value,ownWeek7Id);
});
test('requirement design can import the first source and labels partial output honestly',async t=>{
 const {w}=fixture(t);let populated=false;
 w.nativeFetch=async()=>week7Json(populated?week7Data(week7Id,[{id:'partial-1',status:'completed',created:1730000000,result:{points:[],cases:[],questions:['如何返回？'],checks:{partial:true},evidence:{}}}]):{ok:true,source:null,sources:[],runs:[]});
 const panel=await openWeek7(w);assert.match(panel.textContent,/尚无需求材料/);assert.equal(panel.querySelector('form').closest('details').hidden,false);assert.equal(panel.querySelector('[data-design-generate]').disabled,true);
 populated=true;panel.querySelector('[data-design-refresh]').click();await wait();await wait();assert.match(panel.textContent,/草稿不完整/);
});
test('requirement design ignores late results and clears rendered source on same-user permission narrowing',async t=>{
 const {w}=fixture(t);let finish;
 w.nativeFetch=async()=>week7Json(week7Data());
 const panel=await openWeek7(w);w.nativeFetch=()=>new Promise(resolve=>{finish=resolve;});panel.querySelector('[data-design-refresh]').click();
 w.currentAccessProfile.scope.ui_apps=['restricted'];finish(week7Json(week7Data()));await wait();await wait();assert.equal(w.document.querySelector('[data-requirement-design]'),null);
});

test('requirement evaluation discards a late result after the caller changes session',async t=>{
 const {w}=fixture(t);let finish;
 w.nativeFetch=async()=>week7Json(week7Data());
 const panel=await openWeek7(w),assessment=panel.querySelector('[data-design-assessment]');
 w.nativeFetch=()=>new Promise(resolve=>{finish=resolve;});
 assessment.open=true;assessment.dispatchEvent(new w.Event('toggle'));await wait();
 w.sessionStorage.setItem('sessionToken','different-session');
 finish(week7Json({ok:true,report:{groups:[],candidates:[]}}));await wait();await wait();
 assert.equal(w.document.querySelector('[data-requirement-design]'),null);
});
