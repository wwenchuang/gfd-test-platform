const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('../api-testing-ui/node_modules/jsdom');
const wait=()=>new Promise(setImmediate);
function fixture(t){
 const dom=new JSDOM('<div id="app"><details data-ui-advanced open></details><main id="editor-area"></main></div>',{url:'http://localhost/task-manager.html',runScripts:'dangerously',pretendToBeVisual:true});t.after(()=>dom.window.close());
 const w=dom.window,calls=[];w.currentAccessProfile={user_id:'u1'};w.activeWorkflow='dashboard';w.currentFile=null;
 w.apiRequest=async()=>({ok:true,enabled:true});w.hasPermission=()=>true;w.applyAccessNavigation=()=>{};w.toggleLibrary=()=>{};
 w.activateWorkflow=key=>{w.activeWorkflow=key;calls.push(key);};w.nativeFetch=async()=>({ok:true,headers:{get:()=> 'image/png'},blob:async()=>new w.Blob(['png'],{type:'image/png'})});w.Headers=Headers;w.sessionStorage.setItem('sessionToken','fixture-session-token');
 const utils=fs.readFileSync('js/utils.js','utf8');w.eval(utils.slice(utils.indexOf('function sessionToken()'),utils.indexOf('const WORKFLOW_SECTIONS')));
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
 const f=await videoFixture(t);let resolve;f.respond(path=>path==='/ui-video-sources?app_id=a'?new Promise(r=>resolve=r):undefined);
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
 const f=await videoFixture(t);let source={source_id:'active',filename:'clip.mp4',status:'analyzing',analysis_version:1,updated_at:1};f.respond(p=>p.startsWith('/ui-video-sources?')?{ok:true,sources:[source]}:p.includes('/active?')?{ok:true,source}:undefined);f.app('a');await wait();f.q('[data-video-open]').click();await wait();f.q('[data-video-back]').click();source={...source,status:'ready',updated_at:2};f.q('[data-video-status]').click();await wait();assert.equal(f.q('[data-video-history]').hidden,false);assert.equal(f.q('[data-video-detail-view]').hidden,true);assert.equal(f.calls.some(c=>c.path.endsWith('/import')),false);assert.match(f.q('[data-video-list-summary]').textContent,/待核对 1/);assert.match(f.q('[data-video-list-summary]').textContent,/最近.*30/);
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
