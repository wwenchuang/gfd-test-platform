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
