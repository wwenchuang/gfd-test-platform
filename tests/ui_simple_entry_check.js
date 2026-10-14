const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('../api-testing-ui/node_modules/jsdom');
const wait=()=>new Promise(setImmediate);
function fixture(t){
 const dom=new JSDOM('<div id="app"><details data-ui-advanced open></details><main id="editor-area"></main></div>',{url:'http://localhost/task-manager.html',runScripts:'dangerously',pretendToBeVisual:true});t.after(()=>dom.window.close());
 const w=dom.window,calls=[];w.currentAccessProfile={user_id:'u1'};w.activeWorkflow='dashboard';w.currentFile=null;
 w.apiRequest=async()=>({ok:true,enabled:true});w.hasPermission=()=>true;w.applyAccessNavigation=()=>{};w.toggleLibrary=()=>{};
 w.activateWorkflow=key=>{w.activeWorkflow=key;calls.push(key);};w.nativeFetch=async()=>({ok:true,headers:{get:()=> 'image/png'},blob:async()=>new w.Blob(['png'],{type:'image/png'})});w.authHeaders=()=>({});
 w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>{};
 w.UiSimpleFlow={mount:(node,options)=>{calls.push(options);},reset:()=>calls.push('reset'),leave:()=>calls.push('leave'),refresh:()=>{}};
 w.eval(fs.readFileSync('js/ui-simple-entry.js','utf8'));
 return {w,calls};
}
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
