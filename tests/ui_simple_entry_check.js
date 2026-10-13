const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('../api-testing-ui/node_modules/jsdom');
const wait=()=>new Promise(setImmediate);
function fixture(t){
 const dom=new JSDOM('<div id="app"><details data-ui-advanced open></details><main id="editor-area"></main></div>',{url:'http://localhost/task-manager.html',runScripts:'dangerously'});t.after(()=>dom.window.close());
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
 assert.equal(w.location.search,'');assert.equal(w.uiSimpleReportIntent(),null);
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
