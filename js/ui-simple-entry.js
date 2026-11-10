// Adapter for the existing authenticated shell; the workflow itself has no legacy job polling.
let uiSimpleEnabled=false;
let uiSimpleEvidenceAvailable=false;
let uiSimpleEpoch=0;
let uiCaseCreationEpoch=0;
let uiCaseDraftHandle=null;
let uiSimpleStartSelection=null;
let uiStartCreationIntent=null;
const uiSimplePictures=new Map();
const uiSimplePictureElements=new WeakMap();
let uiSimplePictureElementSequence=0;
const uiSimplePictureRequests=new Set();
const uiSimpleDownloads=new Set();
let uiCatalogCleanup=null;
function isUiSimpleWorkflow(key){return ['ui_start','ui_library','ui_reports'].includes(key);}
function isUiSimpleAvailable(key){return key==='ui_reports'?uiSimpleEvidenceAvailable:uiSimpleEnabled;}
function uiSimpleIdentity(){return String(currentAccessProfile?.user_id||'');}
function confirmUiCaseDraftLeave(){
  if(!uiCaseDraftHandle?.hasUnsavedChanges?.())return true;
  return window.confirm('当前用例有未保存的修改，离开后这些修改可能丢失。确认离开吗？');
}
function leaveUiSimpleWorkflow({discardReportIntent=false}={}){
  uiCatalogCleanup?.();uiCatalogCleanup=null;
  uiCaseCreationEpoch++;uiCaseDraftHandle?.dispose();uiCaseDraftHandle=null;
  if(discardReportIntent)clearUiSimpleReportIntent();
  for(const progress of document.querySelectorAll('[data-week2-progress]'))progress.textContent='本人分析结果待核验。';
  document.querySelector('[data-week2-real-history]')?.replaceChildren();
  window.UiSimpleFlow?.leave();
  document.getElementById('app')?.classList.remove('ui-simple-active');
  for(const controller of uiSimplePictureRequests)controller.abort();
  uiSimplePictureRequests.clear();
  for(const controller of uiSimpleDownloads)controller.abort();
  uiSimpleDownloads.clear();
  for(const picture of uiSimplePictures.values())URL.revokeObjectURL(picture);
  uiSimplePictures.clear();
}
function resetUiSimpleWorkflow(){
  uiSimpleStartSelection=null;
  uiStartCreationIntent=null;
  uiSimpleEpoch++;uiSimpleEnabled=false;uiSimpleEvidenceAvailable=false;
  window.UiSimpleFlow?.reset();leaveUiSimpleWorkflow();
  document.querySelectorAll('[data-practice-workbench] [data-ui-week-one], [data-practice-workbench] [data-ui-week-two], [data-practice-workbench] [data-practice-ai-results], [data-practice-workbench] [data-catalog-check]').forEach(node=>node.remove());
}

async function initUiSimpleAvailability({navigate=true}={}){
  const epoch=++uiSimpleEpoch,identity=uiSimpleIdentity(),initialWorkflow=activeWorkflow;
  if(!identity||!hasPermission('ui.view'))return;
  try{
    const data=await apiRequest('/ui-executions/availability',{timeoutMs:5000});
    if(epoch!==uiSimpleEpoch||identity!==uiSimpleIdentity())return;
    uiSimpleEnabled=data?.ok===true&&data.enabled===true;
    uiSimpleEvidenceAvailable=data?.ok===true&&(data.evidence_available===true||uiSimpleEnabled);
    const advanced=document.querySelector('[data-ui-advanced]');
    if(advanced){advanced.classList.toggle('ui-advanced-enabled',uiSimpleEnabled);if(activeWorkflow!=='ai_practice')advanced.open=!uiSimpleEnabled||!isUiSimpleWorkflow(activeWorkflow)&&activeWorkflow!=='dashboard';}
    applyAccessNavigation();
    // Never take the user away from a page they opened while the feature check was in flight.
    if(navigate&&uiSimpleEnabled&&activeWorkflow===initialWorkflow&&!currentFile){
      const saved=sessionStorage.getItem('midscene_active_workflow');
      if(isUiSimpleWorkflow(saved)||activeWorkflow==='dashboard')await activateWorkflow(isUiSimpleWorkflow(saved)?saved:'ui_start');
    }
  }catch(_){/* Feature discovery can fail without breaking the existing workspace. */}
}
async function loadUiSimplePicture(img,device,isCurrent,{forceFresh=false}={}){
  const epoch=uiSimpleEpoch,identity=uiSimpleIdentity();
  const url=new URL(device.snapshot_url,window.location.origin);
  if(url.origin!==window.location.origin||url.pathname!=='/api/runner/device-snapshot'
    ||url.searchParams.get('runner_id')!==device.target.runner_id||url.searchParams.get('device_id')!==device.target.device_id
    ||url.searchParams.get('app_id')!==device.app_id)return;
  if(!uiSimplePictureElements.has(img))uiSimplePictureElements.set(img,++uiSimplePictureElementSequence);
  const key='ui-device-picture|'+uiSimplePictureElements.get(img)+'|'+identity+'|'+url.href+'|'+device.snapshot_captured_at;
  const attach=src=>{
    if(epoch!==uiSimpleEpoch||identity!==uiSimpleIdentity()||!isCurrent())return false;
    img.src=src;img.hidden=false;const fallback=img.parentElement?.querySelector('[data-picture-fallback]');if(fallback)fallback.hidden=true;
    return true;
  };
  if(forceFresh&&uiSimplePictures.has(key)){URL.revokeObjectURL(uiSimplePictures.get(key));uiSimplePictures.delete(key);}
  if(uiSimplePictures.has(key))return attach(uiSimplePictures.get(key));
  const controller=new AbortController();uiSimplePictureRequests.add(controller);
  const timeout=setTimeout(()=>controller.abort(),12000);
  try{
    const response=await nativeFetch(url.pathname+url.search,{headers:authHeaders(),signal:controller.signal});
    if(!response.ok||!(response.headers.get('content-type')||'').toLowerCase().startsWith('image/png'))return;
    const blob=await response.blob();
    if(blob.size>8*1024*1024||epoch!==uiSimpleEpoch||identity!==uiSimpleIdentity()||!isCurrent())return;
    const src=URL.createObjectURL(blob);uiSimplePictures.set(key,src);attach(src);
    while(uiSimplePictures.size>30){const oldest=uiSimplePictures.keys().next().value;URL.revokeObjectURL(uiSimplePictures.get(oldest));uiSimplePictures.delete(oldest);}
    return true;
  }catch(_){/* Keep an explicit no-picture state; device metadata still renders. */}
  finally{clearTimeout(timeout);uiSimplePictureRequests.delete(controller);}
}
function releaseUiSimplePicture(img){
  const id=uiSimplePictureElements.get(img);if(!id)return;
  const prefix='ui-device-picture|'+id+'|';
  for(const [key,src] of uiSimplePictures){if(key.startsWith(prefix)){URL.revokeObjectURL(src);uiSimplePictures.delete(key);}}
}
function uiSimpleReportIntent(){
  const query=new URLSearchParams(window.location.search);
  if(query.getAll('ui_batch').length!==1||query.getAll('report_revision').length!==1)return null;
  const batchId=query.get('ui_batch'),revision=query.get('report_revision');
  return /^uib_[a-f0-9]{32}$/.test(batchId||'')&&/^[a-f0-9]{64}$/.test(revision||'')?{batchId,revision}:null;
}
function clearUiSimpleReportIntent(expected){
  const current=uiSimpleReportIntent();
  if(!current||(expected&&(expected.batchId!==current.batchId||expected.revision!==current.revision)))return;
  const url=new URL(window.location.href);url.searchParams.delete('ui_batch');url.searchParams.delete('report_revision');
  window.history.replaceState(window.history.state,'',url.pathname+url.search+url.hash);
}
function preferUiSimpleReport(){
  if(!isUiSimpleAvailable('ui_reports')||!hasPermission('ui.view')||!uiSimpleReportIntent())return false;
  activeWorkflow='ui_reports';return true;
}
function showUiSimpleWorkflow(){
  if(!isUiSimpleAvailable(activeWorkflow)||!isUiSimpleWorkflow(activeWorkflow)||!hasPermission('ui.view'))return false;
  const intent=uiStartCreationIntent;
  const openCreation=activeWorkflow==='ui_library'&&intent?.page.isConnected&&intent.identity===uiSimpleIdentity()
    &&intent.epoch===uiSimpleEpoch&&uiSimpleEnabled&&hasPermission('ui.edit');
  if(openCreation)uiStartCreationIntent=null;
  leaveUiSimpleWorkflow();
  document.getElementById('app')?.classList.add('ui-simple-active');
  const area=document.getElementById('editor-area');area.classList.remove('editor-open');area.replaceChildren();
  const page=document.createElement('div');page.className='ui-simple-page';area.append(page);toggleLibrary(false);
  const reportIntent=activeWorkflow==='ui_reports'?uiSimpleReportIntent():null,identity=uiSimpleIdentity();
  const entrySelection=activeWorkflow==='ui_start'&&uiSimpleStartSelection?.identity===identity?uiSimpleStartSelection.selection:null;
  uiSimpleStartSelection=null;
  window.UiSimpleFlow.mount(page,{
    mode:{ui_start:'start',ui_library:'library',ui_reports:'reports'}[activeWorkflow],
    entrySelection,openStartTest:selection=>{
      if(identity!==uiSimpleIdentity()||activeWorkflow!=='ui_library'||!page.isConnected||!hasPermission('ui.execute'))return;
      uiSimpleStartSelection={identity,selection};activateWorkflow('ui_start');
    },
    request:apiRequest,identity:uiSimpleIdentity,canAdmit:()=>uiSimpleEnabled,canExecute:()=>hasPermission('ui.execute'),canNotify:()=>hasPermission('platform.notify'),reportIntent,loadPicture:loadUiSimplePicture,releasePicture:releaseUiSimplePicture,loadEvidenceImage:loadUiBatchImage,
    canRefreshPictures:()=>!!uiSimpleIdentity()&&hasPermission('ui.view')&&hasPermission('platform.configure')&&(currentAccessProfile.is_superuser===true||currentAccessProfile.scope?.ui_apps==='*'),
    onReportClose:intent=>{if(uiSimpleIdentity()===identity)clearUiSimpleReportIntent(intent);},
    download:downloadUiBatchFile,copy:async text=>{if(!await copyText(text))throw new Error('复制未完成，请重试');}
  });
  if(activeWorkflow==='ui_library')attachUiCaseCreation(page);
  if(activeWorkflow==='ui_start')attachUiStartCreation(page);
  if(openCreation)page.querySelector('[data-ui-create-case]')?.click();
  // Keep the exact internal link through loading, errors, refresh and reauthentication.
  // Only an explicit close, selecting another test, or workflow departure abandons it.
  return true;
}
function canUseTaskWorkbench(){return hasPermission('ui.view')||hasPermission('api.view')||hasPermission('platform.configure');}
async function refreshTaskWorkbenchAfterSession(){
  const page=document.querySelector('[data-practice-workbench]'),identity=uiSimpleIdentity();
  if(!page||activeWorkflow!=='ai_practice')return;
  page.replaceChildren();page.textContent='正在更新账号权限和可用入口…';
  const pending=initUiSimpleAvailability({navigate:false}),epoch=uiSimpleEpoch;
  await pending;
  if(!page.isConnected||activeWorkflow!=='ai_practice'||identity!==uiSimpleIdentity()||epoch!==uiSimpleEpoch)return;
  applyAccessNavigation();
  if(!showAiPractice())page.textContent='当前账号没有测试工作台访问权限，请联系管理员。';
}
function showAiPractice(){
  if(activeWorkflow!=='ai_practice'||!uiSimpleIdentity()||!canUseTaskWorkbench())return false;
  leaveUiSimpleWorkflow();
  document.getElementById('app')?.classList.add('ui-simple-active');
  const area=document.getElementById('editor-area');area.classList.remove('editor-open');area.replaceChildren();
  const page=document.createElement('div');page.className='ui-simple-page ui-practice-page';page.dataset.practiceWorkbench='';
  page.innerHTML='<div class="ui-simple-heading"><div><div class="workflow-kicker">测试工作台</div><h1>从这里开始测试</h1><p>选择测试方式，查看已有结果与证据。</p></div></div>';
  area.append(page);toggleLibrary(false);attachTaskWorkbench(page);return true;
}
function attachTaskWorkbench(page){
  const identity=uiSimpleIdentity(),epoch=uiSimpleEpoch;
  const current=()=>page.isConnected&&activeWorkflow==='ai_practice'&&identity===uiSimpleIdentity()&&epoch===uiSimpleEpoch&&canUseTaskWorkbench();
  const canOpen=key=>typeof canAccessWorkflow==='function'&&canAccessWorkflow(key);
  const cards=document.createElement('section');cards.className='ui-practice-tasks';cards.setAttribute('aria-label','选择测试任务');
  cards.innerHTML='<article><h2>开始 UI 测试</h2><p>选择应用、用例和手机，可用于发版回归。</p><button type="button" data-practice-workflow="ui_start">选择用例和手机</button></article><article><h2>API 测试</h2><p>管理接口资产、用例、执行结果和基线。</p><span data-practice-api-slot></span></article><article><h2>我的测试</h2><p>查看自己发起的测试结果、步骤、截图和报告。</p><button type="button" data-practice-workflow="ui_reports">查看测试结果</button></article>';
  page.append(cards);
  for(const button of cards.querySelectorAll('[data-practice-workflow]')){const key=button.dataset.practiceWorkflow;button.disabled=!canOpen(key);if(button.disabled)button.title='当前账号无权使用或服务暂不可用';button.onclick=()=>{if(current()&&canOpen(key))activateWorkflow(key);};}
  const apiSlot=cards.querySelector('[data-practice-api-slot]');
  if(hasPermission('api.view')){const link=document.createElement('a');link.dataset.practiceApi='';link.href='/api-test/';link.textContent='打开 API 测试';link.onclick=event=>{if(!current()||!hasPermission('api.view'))event.preventDefault();};apiSlot.append(link);}
  else apiSlot.textContent='需要 API 查看权限，请联系管理员。';
  if(hasPermission('api.view'))attachPracticeAiResults(page,current);
  if(hasPermission('ui.view'))attachCatalogWebCheck(page,current);
  if(!hasPermission('platform.configure')||(currentAccessProfile?.is_superuser!==true&&currentAccessProfile?.scope?.ui_apps!=='*'))return;
  attachUiFirstWeekGuide(page);
  const previousEnvironment=page.querySelector('[data-ui-week-one]');
  const environment=document.createElement('details');environment.dataset.uiWeekOne='';environment.className='ui-case-source-picker ui-practice-panel';environment.innerHTML='<summary>环境与模型检查</summary><p>检查服务与模型的已有结果；真实模型调用可能产生费用。</p>';
  environment.append(previousEnvironment.querySelector('[data-week-probe]'),previousEnvironment.querySelector('[data-week-smoke]'));
  const firstSnapshot=previousEnvironment.querySelector('[data-week-snapshot]');firstSnapshot.querySelector('summary').textContent='查看历史检查证据';
  firstSnapshot.querySelector('p').textContent='历史结果请在本人检查记录中按账号权限查看，当前情况以实际检查为准。';environment.append(firstSnapshot);previousEnvironment.remove();page.append(environment);
  if(!hasPermission('api.view'))return;
  attachUiWeekTwoGuide(page);
  const previousEvidence=page.querySelector('[data-ui-week-two]'),panel=previousEvidence.querySelector('[data-week2-real]');
  const evidence=document.createElement('section');evidence.dataset.uiWeekTwo='';evidence.className='ui-practice-evidence';
  panel.querySelector('summary').textContent='已有接口分析与证据';
  const progress=previousEvidence.querySelector('[data-week2-progress]');if(progress)panel.append(progress);
  const snapshot=previousEvidence.querySelector('[data-week2-snapshot]');snapshot.querySelector('summary').textContent='查看历史执行证据';
  const walker=document.createTreeWalker(snapshot,NodeFilter.SHOW_TEXT);while(walker.nextNode())walker.currentNode.nodeValue=walker.currentNode.nodeValue.replace(/W02\s*/g,'').replace('本记录不代表整周验收或所有业务已通过','本记录不代表所有业务已通过');
  panel.append(snapshot,previousEvidence.querySelector('[data-week2-howto]'));evidence.append(panel);previousEvidence.remove();page.append(evidence);
  const acceptance=document.createElement('details');acceptance.dataset.practiceAcceptance='';acceptance.className='ui-practice-details';
  acceptance.dataset.practiceLegacyEvidence='';
  acceptance.innerHTML='<summary>查看接口数据与清理实战记录</summary><p>历史记录按账号的项目和环境权限读取；无权访问或来源不可用的记录不会显示。不代表当前实时状态，也不会重新执行测试。</p><button type="button" data-legacy-refresh>重新读取</button><div data-legacy-result aria-live="polite"></div>';
  const allowed=()=>current()&&hasPermission('platform.configure')&&hasPermission('api.view')&&(currentAccessProfile?.is_superuser===true||currentAccessProfile?.scope?.ui_apps==='*');
  const legacyResult=acceptance.querySelector('[data-legacy-result]');let legacySerial=0,legacyLoaded=false;
  const legacyProfile=()=>JSON.stringify([currentAccessProfile?.user_id,currentAccessProfile?.is_superuser,currentAccessProfile?.scope,currentAccessProfile?.permissions]);
  const loadLegacy=async()=>{
    const request=++legacySerial,profile=legacyProfile();legacyLoaded=true;legacyResult.replaceChildren();const checks=page.querySelector('[data-week2-checks]');checks?.replaceChildren();
    if(!allowed()){legacyResult.textContent='当前无权查看。';return;}
    legacyResult.textContent='正在读取授权记录…';
    try{
      const data=await apiRequest('/platform/practice-legacy-evidence',{timeoutMs:20000});
      if(request!==legacySerial||!allowed()||legacyProfile()!==profile){if(request===legacySerial)legacyResult.replaceChildren();return;}
      const records=data?.ok===true&&Array.isArray(data.records)?data.records:[];legacyResult.replaceChildren();
      for(const item of records){
        if(!['cleanup','baseline','failure','trace','feedback','timeout'].includes(item?.key)||typeof item.summary!=='string'||item.summary.length>2000||typeof item.execution_id!=='string'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(item.execution_id))continue;
        const row=document.createElement('p');row.textContent=item.summary+' ';const link=document.createElement('a');link.className='ui-week-link';link.href='/api-test/#/runs?executionId='+encodeURIComponent(item.execution_id);link.textContent='查看原执行记录';link.onclick=event=>{if(!allowed()||legacyProfile()!==profile)event.preventDefault();};row.append(link);legacyResult.append(row);
        if(item.key==='feedback'&&checks&&Array.isArray(item.checks)&&item.checks.length<=30){for(const label of item.checks){if(typeof label!=='string'||label.length>100)continue;const li=document.createElement('li');li.dataset.week2Check='';li.textContent=label;checks.append(li);}}
      }
      if(!legacyResult.childNodes.length)legacyResult.textContent='暂无可查看的记录，或当前账号无权查看。';
    }catch(_){if(request===legacySerial){legacyResult.replaceChildren();if(allowed()&&legacyProfile()===profile)legacyResult.textContent='记录暂不可用或当前账号无权查看，请稍后重新读取。';}}
  };
  acceptance.querySelector('[data-legacy-refresh]').onclick=loadLegacy;acceptance.addEventListener('toggle',()=>{if(acceptance.open&&!legacyLoaded)loadLegacy();});
  const scheduled=document.createElement('details');scheduled.dataset.practiceCleanupEvidence='';scheduled.className='ui-practice-details';
  scheduled.innerHTML='<summary>查看定时清理执行记录</summary><p>仅显示当前账号有权查看的历史记录；这不是实时测试结果。</p><button type="button">重新读取</button><div data-cleanup-result aria-live="polite"></div>';
  const result=scheduled.querySelector('[data-cleanup-result]'),button=scheduled.querySelector('button');let sequence=0,loaded=false;
  const profileKey=()=>JSON.stringify([currentAccessProfile?.user_id,currentAccessProfile?.is_superuser,currentAccessProfile?.scope,currentAccessProfile?.permissions]);
  const load=async()=>{
    const request=++sequence,profile=profileKey();loaded=true;result.replaceChildren();
    if(!allowed()){result.textContent='当前无权查看。';return;}
    result.textContent='正在读取授权记录…';
    try{
      const data=await apiRequest('/platform/practice-cleanup-evidence',{timeoutMs:20000});
      if(request!==sequence||!allowed()||profileKey()!==profile){if(request===sequence)result.replaceChildren();return;}
      const item=data?.ok===true?data.evidence:null;
      if(!item||typeof item.summary!=='string'||item.summary.length>2000||typeof item.execution_id!=='string'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(item.execution_id)){result.textContent='记录未配置、不可用或当前账号无权查看。';return;}
      result.replaceChildren();const description=document.createElement('p');description.textContent=(typeof item.observed_at==='string'&&item.observed_at.length<=80?'记录时间：'+item.observed_at+'。':'')+item.summary;result.append(description);
      const link=document.createElement('a');link.className='ui-week-link';link.href='/api-test/#/runs?executionId='+encodeURIComponent(item.execution_id);link.textContent='查看原执行记录';link.onclick=event=>{if(!allowed()||profileKey()!==profile)event.preventDefault();};result.append(link);
    }catch(_){if(request===sequence){result.replaceChildren();if(allowed()&&profileKey()===profile)result.textContent='记录暂不可用或当前账号无权查看，请稍后重新读取。';}}
  };
  button.onclick=load;scheduled.addEventListener('toggle',()=>{if(scheduled.open&&!loaded)load();});acceptance.append(scheduled);
  panel.append(acceptance);
}
function attachPracticeAiResults(page,current){
  const panel=document.createElement('details');panel.dataset.practiceAiResults='';panel.className='ui-case-source-picker ui-practice-panel ui-practice-ai';
  panel.innerHTML='<summary>查看任务的 AI 生成状态</summary><p>选择项目和测试任务，查看读取时的生成状态。草稿详情请到 API 测试查看，任务列表最多显示最近 100 条。这里不会发起生成或执行业务。</p><div class="ui-practice-ai-controls"><label>项目<select data-ai-project><option value="">请选择项目</option></select></label><label>测试任务<select data-ai-task disabled><option value="">请选择任务</option></select></label><button type="button" data-ai-refresh>刷新状态</button></div><p data-ai-result role="status" aria-live="polite"></p>';
  page.append(panel);
  const project=panel.querySelector('[data-ai-project]'),taskSelect=panel.querySelector('[data-ai-task]'),result=panel.querySelector('[data-ai-result]');
  const profile=()=>JSON.stringify([sessionToken(),currentAccessProfile?.user_id,currentAccessProfile?.is_superuser,currentAccessProfile?.scope,currentAccessProfile?.permissions]);
  const permitted=()=>current()&&hasPermission('api.view');let seq=0,loaded=false;
  const valid=(id,key)=>id===seq&&permitted()&&profile()===key;
  const request=async path=>{const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);try{const response=await nativeFetch('/api'+path,{headers:authHeaders(),cache:'no-store',signal:controller.signal});if(!response.ok)throw Error('unavailable');const payload=await response.json();if(payload?.ok!==true||!payload.data||typeof payload.data!=='object')throw Error('unavailable');return payload.data;}finally{clearTimeout(timer);}};
  const options=(select,items,label)=>{select.replaceChildren(new Option(label,''));for(const item of items){if(typeof item?.id!=='string'||typeof item?.name!=='string')continue;const option=document.createElement('option');option.value=item.id;option.textContent=item.name;select.append(option);}};
  const clearSelections=()=>{options(project,[],'请选择项目');options(taskSelect,[],'请选择任务');taskSelect.disabled=true;};
  const loadProjects=async()=>{const id=++seq,key=profile();clearSelections();result.textContent='正在读取项目…';try{const data=await request('/api-testing/v1/projects');if(!valid(id,key))return;options(project,Array.isArray(data?.projects)?data.projects:[],'请选择项目');result.textContent='请选择项目。';}catch(_){if(valid(id,key)){clearSelections();result.textContent='项目暂不可用或无权查看，请稍后重试。';}}};
  const loadTasks=async()=>{const id=++seq,key=profile(),selected=project.value;options(taskSelect,[],'请选择任务');taskSelect.disabled=true;result.textContent='';if(!selected)return;result.textContent='正在读取任务…';try{const data=await request('/api-testing/v1/tasks?project_id='+encodeURIComponent(selected));if(!valid(id,key)||project.value!==selected)return;options(taskSelect,Array.isArray(data?.tasks)?data.tasks.filter(x=>x.project_id===selected):[],'请选择任务');taskSelect.disabled=false;result.textContent='请选择测试任务。';}catch(_){if(valid(id,key)){clearSelections();result.textContent='任务暂不可用或无权查看，请稍后重试。';}}};
  const loadResult=async()=>{const id=++seq,key=profile(),projectId=project.value,taskId=taskSelect.value;result.textContent='';if(!projectId||!taskId)return;result.textContent='正在读取任务结果…';const taskPath='/api-testing/v1/tasks?project_id='+encodeURIComponent(projectId);
    try{
      const first=await request(taskPath);if(!valid(id,key))return;const task=Array.isArray(first?.tasks)?first.tasks.find(x=>x.id===taskId&&x.project_id===projectId):null;
      if(!task)throw Error('unavailable');if(!task.latest_ai_job_id){result.textContent='该任务没有可查看的 AI 生成记录。';return;}
      const response=await request('/api-testing/v1/ai-jobs/'+encodeURIComponent(task.latest_ai_job_id));if(!valid(id,key))return;const job=response?.job;
      if(!job||job.id!==task.latest_ai_job_id||job.project_id!==projectId||job.environment_revision_id!==task.environment_revision_id||!Array.isArray(job.endpoint_ids)||!Array.isArray(task.selected_endpoint_ids)||!job.endpoint_ids.every(x=>task.selected_endpoint_ids.includes(x)))throw Error('mismatch');
      const second=await request(taskPath);if(!valid(id,key))return;const again=Array.isArray(second?.tasks)?second.tasks.find(x=>x.id===taskId&&x.project_id===projectId):null;
      if(!again||again.latest_ai_job_id!==task.latest_ai_job_id||again.environment_revision_id!==task.environment_revision_id||JSON.stringify(again.selected_endpoint_ids)!==JSON.stringify(task.selected_endpoint_ids))throw Error('changed');
      const batches=Array.isArray(job.batches)?job.batches:[];const count=batches.reduce((n,b)=>n+(Array.isArray(b.generated_draft_ids)?b.generated_draft_ids.length:0),0);
      const state=({queued:'等待生成',running:'正在生成',completed:'生成完成',partial:'部分生成',failed:'生成失败',failed_gateway:'生成失败',failed_validation:'生成校验失败'})[job.state]||'状态待核验';
      const timeout=batches.length>0&&batches.every(b=>b.state==='failed_gateway'&&b.error_code==='gateway_timeout');
      result.textContent=state+'；生成草稿 '+count+' 条。'+(timeout?'模型服务或连接等待超时，具体原因待核查。':'')+' 草稿需要审查后才能使用。';
    }catch(_){if(valid(id,key)){clearSelections();result.textContent='结果暂不可用、任务已变更或当前账号无权查看，请刷新重试。';}}
  };
  project.onchange=loadTasks;taskSelect.onchange=loadResult;panel.querySelector('[data-ai-refresh]').onclick=()=>{if(!project.value)loadProjects();else if(!taskSelect.value)loadTasks();else loadResult();};
  panel.addEventListener('toggle',()=>{if(panel.open&&!loaded){loaded=true;loadProjects();}});
}
function attachUiFirstWeekGuide(page){
  const identity=uiSimpleIdentity(),epoch=uiSimpleEpoch;
  const current=()=>page.isConnected&&activeWorkflow==='ai_practice'&&identity===uiSimpleIdentity()&&epoch===uiSimpleEpoch&&hasPermission('platform.configure');
  const canOpen=key=>typeof canAccessWorkflow==='function'&&canAccessWorkflow(key);
  const guide=document.createElement('details');guide.dataset.uiWeekOne='';guide.className='ui-case-source-picker';
  guide.innerHTML='<summary>第 1 周入门步骤</summary><ol><li>先做快速体检，检查服务、执行环境和模型配置。 <button type="button" data-week-workflow="system_config">打开环境体检</button></li><li>检查模型配置，再运行真实文本和固定图视觉探针。 <button type="button" data-week-workflow="config">打开模型配置</button><span data-week-probe-slot></span></li><li>选择只读业务接口，执行并核对业务断言。 <span data-week-api-slot></span><span data-week-smoke-slot></span></li><li>进入“开始测试”创建或选择用例，选择设备后执行；在“我的测试”查看实际结果。 <button type="button" data-week-workflow="ui_reports">查看我的测试</button></li><li>保存执行证据。人工耗时记录暂缓；网页暂不提供周验收和耗时记录，请另行留档。</li></ol>';
  const snapshot=document.createElement('details');snapshot.dataset.weekSnapshot='';
  snapshot.innerHTML='<summary>查看历史检查说明</summary><p>历史结果请在本人检查记录中查看；服务器配置、截图和 trace 按账号权限访问。人工确认仍待完成。</p>';
  guide.append(snapshot);
  for(const button of guide.querySelectorAll('[data-week-workflow]')){
    const target=button.dataset.weekWorkflow;button.disabled=!canOpen(target);
    if(button.disabled){button.title='当前账号无权使用，请联系管理员';button.insertAdjacentText('afterend','（需要相应权限）');}
    button.onclick=()=>{if(current()&&canOpen(target))activateWorkflow(target);};
  }
  attachUiWeekOneProbe(guide.querySelector('[data-week-probe-slot]'),current);
  attachUiWeekOneSmoke(guide.querySelector('[data-week-smoke-slot]'),current);
  const slot=guide.querySelector('[data-week-api-slot]');
  if(hasPermission('api.view')){
    for(const [key,label,path] of [['weekApi','打开 API 测试','/api-test/'],['weekApiRuns','查看接口执行记录','/api-test/#/runs']]){
      const link=document.createElement('a');link.dataset[key]='';link.className='ui-week-link';link.href=path;link.textContent=label;
      link.onclick=event=>{if(!current()||!hasPermission('api.view'))event.preventDefault();};slot.append(link,document.createTextNode(' '));
    }
  }else slot.textContent='（API 测试需要相应权限）';
  const heading=page.querySelector('.ui-simple-heading');
  if(heading)heading.after(guide);else page.prepend(guide);
}

// The week-two guide links to existing real-project workspaces; it does not execute tests.
function attachUiWeekTwoGuide(page){
  const identity=uiSimpleIdentity(),epoch=uiSimpleEpoch;
  const current=()=>page.isConnected&&activeWorkflow==='ai_practice'&&identity===uiSimpleIdentity()&&epoch===uiSimpleEpoch&&hasPermission('platform.configure')&&hasPermission('api.view');
  const guide=document.createElement('details');guide.dataset.uiWeekTwo='';guide.className='ui-case-source-picker';
  guide.innerHTML='<summary>第 2 周｜真实项目接口实战</summary><p>围绕现有 APP 和后台的真实接口，练习选择环境、检查用例、执行与复盘。这里提供操作入口，不会自动发起测试。</p><ol><li><strong>确认项目与环境：</strong>进入接口资产，选择现有真实业务项目、接口版本和执行环境；核对目标地址和账号权限。<span data-week2-slot="assets"></span></li><li><strong>检查用例和数据：</strong>在用例管理中选择已有接口与用例，先检查请求、业务断言和前后置步骤。确认数据写入、副作用、归属及清理方式；不明确时先停下核实。<span data-week2-slot="cases"></span></li><li><strong>逐条调试与复盘：</strong>确认范围后，在原平台执行选定用例；在执行记录查看实际状态、业务断言、失败信息，以及本次数据清理证据。仅有 HTTP 成功不能证明业务通过。<span data-week2-slot="runs"></span></li><li><strong>整理可复用基线：</strong>查看已有基线与调试结果，通过审核后再按平台流程维护；不要把未执行或未审核的用例计为通过。<span data-week2-slot="baselines"></span></li></ol><p>人工基线整理与审核仍待处理；本周是否完成应以实际执行结果及可核对的证据为准。</p>';
  const acceptance=document.createElement('section');
  acceptance.innerHTML='<h3>操作准备</h3><p>从已授权执行记录核对业务结果；数据处理及业务规则以本人最新记录为准。</p><p data-week2-task="W02-01"></p><p data-week2-task="W02-05"></p><details data-week2-snapshot><summary>查看历史执行证据</summary><p>展开下方接口数据与清理实战记录，按当前账号权限查看。</p></details>';
  const howto=document.createElement('details');howto.dataset.week2Howto='';
  howto.innerHTML='<summary>查看具体操作、17 项检查与复现命令</summary><p>先打开上面的原始执行记录，核对真实反馈分类接口和原断言；再从可信渠道准备该记录的最小脱敏证据文件。本地数据处理只读取证据文件，不发起业务请求；对真实记录的副本构造边界输入，不是 17 次真实接口调用。文件模式未独立核验文件来源，应与原始记录交叉核对；字段规则来自历史观察，不是经业务审核的正式业务合同。</p><h4>数据处理检查内容</h4><ol data-week2-checks></ol><h4>新建独立环境重跑</h4><p>在项目根目录进入练习目录；以下证据路径、执行 ID 和子用例 ID 均需替换为实际值。文件只保留必要的脱敏字段，不要把凭据写入命令或文件。</p><pre data-week2-commands></pre><p>unittest 使用合成数据检查工具本身；run.py 读取真实执行证据后才产生该记录的本地处理结果。此过程没有创建真实业务数据，不能作为真实写入或清理通过的证据。</p>';
  howto.querySelector('h4:last-of-type').textContent='新建 venv 独立环境重跑（示例目录需事先不存在）';
  const checksNotice=document.createElement('p');checksNotice.textContent='展开历史执行记录后，按权限显示对应的数据检查内容。';howto.querySelector('[data-week2-checks]').before(checksNotice);
  howto.querySelector('[data-week2-commands]').textContent='cd task_server/fixtures/week2_real\npython3 -m venv /tmp/week2-real-venv\n/tmp/week2-real-venv/bin/python -m unittest discover -s tests -v\n/tmp/week2-real-venv/bin/python run.py --evidence /安全路径/证据.json --execution-id 真实执行ID --case-id 真实子用例ID';
  const stepDetails=document.createElement('details');stepDetails.className='ui-practice-details';stepDetails.innerHTML='<summary>查看操作步骤与平台入口</summary>';
  const originalSteps=guide.querySelector(':scope > ol');originalSteps.replaceWith(stepDetails);stepDetails.append(originalSteps);
  const acceptanceDetails=document.createElement('details');acceptanceDetails.className='ui-practice-details';acceptanceDetails.innerHTML='<summary>查看六项验收与历史执行快照</summary>';acceptanceDetails.append(acceptance);
  guide.append(acceptanceDetails,howto);
  page.append(guide);
  attachUiWeekTwoReal(guide,page,identity,epoch);
  if(!hasPermission('api.view')){
    const message=document.createElement('p');message.textContent='打开 API 测试需要相应权限，请联系管理员。';guide.append(message);return;
  }
  for(const [key,label,path] of [['assets','打开接口资产','/api-test/#/assets'],['cases','打开用例管理','/api-test/#/cases'],['runs','查看执行记录','/api-test/#/runs'],['baselines','查看基线','/api-test/#/baselines']]){
    const link=document.createElement('a');link.dataset.week2Link=key;link.className='ui-week-link';link.href=path;link.textContent=label;
    link.onclick=event=>{if(!current())event.preventDefault();};guide.querySelector('[data-week2-slot="'+key+'"]').append(' ',link);
  }

}

// This fixed check reads the signed-in user's platform catalog; it does not run
// device tests or prove the correctness of the underlying business data.
function attachCatalogWebCheck(page,current){
  const scope=()=>JSON.stringify({token:sessionToken(),user:uiSimpleIdentity(),scope:currentAccessProfile?.scope,permissions:currentAccessProfile?.permissions,superuser:currentAccessProfile?.is_superuser,status:currentAccessProfile?.status});
  const snapshot=scope();
  const labels={ui_catalog_count:'页面数量',ui_catalog_filter:'应用与名称筛选',ui_catalog_empty:'空搜索结果',ui_catalog_failure_drill:'数量断言失败演练',ui_case_selection:'选择与取消已发布用例',ui_case_revision:'刷新后核对用例版本',ui_start_handoff:'带入已选用例开始测试',ui_start_switch:'切换应用清空已选用例',ui_start_unready:'无就绪手机时阻止执行',ui_catalog_paging:'核对分页与返回',ui_catalog_module:'按模块筛选并重置',ui_case_hidden_selection:'筛选后保留已选用例',ui_start_empty_guard:'移除最后一条用例后阻止执行'};
  const panel=document.createElement('details');panel.dataset.catalogCheck='';panel.className='ui-case-source-picker ui-practice-panel';
  panel.innerHTML='<summary>用例库页面检查</summary><p>服务器只读打开用例库和开始测试页，核对当前账号可见的用例、版本和手机状态。筛选检查使用已有应用和用例，空搜索使用随机词；不操作手机，不执行用例，显示一致不代表业务数据正确或业务通过。</p><div class="ui-week-probe-actions"><button type="button" data-catalog-run data-catalog-scenario="ui_catalog_count">检查页面数量</button><button type="button" data-catalog-scenario="ui_catalog_filter">检查应用与名称筛选</button><button type="button" data-catalog-scenario="ui_catalog_empty">检查空搜索结果</button><button type="button" data-catalog-scenario="ui_catalog_failure_drill">演练数量断言失败</button><button type="button" data-catalog-scenario="ui_case_selection">检查选择与取消</button><button type="button" data-catalog-scenario="ui_case_revision">刷新核对用例版本</button><button type="button" data-catalog-scenario="ui_start_handoff">检查开始测试交接</button><button type="button" data-catalog-scenario="ui_start_switch">检查切换应用</button><button type="button" data-catalog-scenario="ui_start_unready">检查未就绪阻止执行</button><button type="button" data-catalog-scenario="ui_catalog_paging">核对分页与返回</button><button type="button" data-catalog-scenario="ui_catalog_module">按模块筛选并重置</button><button type="button" data-catalog-scenario="ui_case_hidden_selection">筛选后保留已选用例</button><button type="button" data-catalog-scenario="ui_start_empty_guard">移除最后一条用例后阻止执行</button><button type="button" data-catalog-refresh>刷新检查记录</button></div><p data-catalog-notice role="status" aria-live="polite">展开后可查看本人已有的检查记录。</p><div data-catalog-history></div>';
  page.append(panel);
  const notice=panel.querySelector('[data-catalog-notice]'),history=panel.querySelector('[data-catalog-history]');
  let busy=false,loaded=false,serial=0,previewUrl=null;const artifactRequests=new Set();
  const clear=()=>{history.replaceChildren();if(previewUrl){URL.revokeObjectURL(previewUrl);previewUrl=null;}};
  const valid=()=>{
    const live=current()&&hasPermission('ui.view')&&scope()===snapshot;
    if(!live){serial++;clear();clearMeasurement();notice.textContent='账号或权限已变化，请重新打开此页面。';}
    return live;
  };
  const stability=document.createElement('details');
  stability.className='ui-practice-details';
  stability.innerHTML='<summary>Web 回归稳定性</summary><p>同一版本下，10 个核心流程各执行 20 次。每个位置只记录首跑。</p><button type="button" data-stability-refresh>查看最新测量</button> <span aria-live="polite" data-stability-state></span><div data-stability-report></div><details data-smoke-example><summary>流水线和视觉演练记录</summary><p>锁定环境下，正向冒烟通过，故意写错登录标题后退出码为 1。登录标识间距增加 12 像素时，视觉比对检出 63,574 个差异像素。人工基线审核待办。</p><button type="button" data-smoke-download>下载复现说明与证据</button><span data-example-state aria-live="polite"></span></details>';
  page.append(stability);
  const summary=stability.querySelector('[data-stability-report]'),state=stability.querySelector('[data-stability-state]');
  const refresh=stability.querySelector('button');
  let generation=0;
  const clearMeasurement=()=>{generation++;summary.replaceChildren();state.textContent='';};
  refresh.onclick=async()=>{
    if(!valid())return;
    clearMeasurement();const ticket=generation;
    const controller=new AbortController();uiSimpleDownloads.add(controller);
    const timer=setTimeout(()=>controller.abort(),15000);
    refresh.disabled=true;state.textContent='正在读取…';
    try{
      const response=await nativeFetch('/api/ui-executions/web-check?view=stability',{headers:authHeaders(),signal:controller.signal,redirect:'error'});
      if(!response.ok)throw Error();
      const data=await response.json();
      if(!valid()||generation!==ticket)return;
      if(data.ok!==true)throw Error();
      if(!data.report){state.textContent='当前账号暂无稳定性测量。';return;}
      const r=data.report;
      if(!Array.isArray(r.rows)||r.rows.length!==r.attempted||r.planned!==200||r.attempted>200)throw Error();
      const percent=n=>r.attempted?(100*n/r.attempted).toFixed(1)+'%':'—';
      state.textContent=(r.state==='interrupted'?'已中断':r.unrun?'进行中':r.passed/r.planned>=.95?'已结束 · 达到 95% 目标':'已结束 · 低于 95% 目标')+' · '+r.attempted+' / '+r.planned+' · '+uiPracticeTime(r.updated*1000);
      const line=document.createElement('p');
      line.textContent='首跑通过 '+r.passed+'（'+percent(r.passed)+'），失败 '+r.failed+'，阻塞 '+r.blocked+'（'+percent(r.blocked)+'），未执行 '+r.unrun+'。';
      summary.append(line);
      const slow=Object.entries(r.median_ms||{}).sort((a,b)=>b[1]-a[1]).slice(0,3);
      if(slow.length){const timing=document.createElement('p');timing.textContent='中位耗时最长的流程：'+slow.map(([id,ms])=>(labels[id]||id)+' '+(ms/1000).toFixed(2)+' 秒').join('、');summary.append(timing);}
      const exportButton=document.createElement('button');exportButton.textContent='下载首跑明细 CSV';
      exportButton.onclick=()=>{
        if(!valid()||generation!==ticket)return;
        const keys=['round','scenario','status','reason','duration_ms','run_id'];
        const csv=[keys,...r.rows.map(row=>keys.map(k=>String(row[k]??'').replace(/[^A-Za-z0-9_-]/g,'')))].map(row=>row.join(',')).join('\r\n');
        const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
        const a=document.createElement('a');a.href=url;a.download='web-stability.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      };
      summary.append(exportButton);
      const samples=r.rows.filter(row=>/^[a-f0-9]{32}$/.test(row.run_id||''));
      if(samples.length&&/^[a-f0-9]{32}$/.test(r.id)){
        const evidence=document.createElement('section'),choice=document.createElement('select');
        choice.setAttribute('aria-label','选择要查看证据的首跑');
        for(const row of samples){
          const option=document.createElement('option');
          option.textContent='第 '+row.round+' 轮 · '+(labels[row.scenario]||row.scenario)+' · '+({passed:'通过',blocked:'阻塞',failed:'失败'}[row.status]||'');
          choice.append(option);
        }
        evidence.append(choice);
        let sample=document.createElement('div');
        choice.onchange=()=>{const replacement=document.createElement('div');sample.replaceWith(replacement);sample=replacement;};
        for(const [key,title] of [['screenshot','查看该次截图'],['trace','下载该次 trace']]){
          const button=document.createElement('button');button.type='button';button.textContent=title;
          button.onclick=()=>{const row=samples[choice.selectedIndex];if(row&&valid()&&ticket===generation)void openArtifact(sample,row.run_id,key,{measurement:r.id,round:row.round},state);};
          evidence.append(button);
        }
        evidence.append(sample);summary.append(evidence);
      }
    }catch(_){if(valid()&&generation===ticket)state.textContent='读取失败，请核对登录状态后重试。';}
    finally{clearTimeout(timer);uiSimpleDownloads.delete(controller);refresh.disabled=false;}
  };
  const changed=()=>{if(panel.isConnected){clearMeasurement();}if(panel.isConnected&&valid()){serial++;clear();loaded=false;notice.textContent='返回页面后，请刷新记录以重新核对查看权限。';}};window.addEventListener('focus',changed);document.addEventListener('visibilitychange',changed);
  uiCatalogCleanup=()=>{serial++;clear();window.removeEventListener('focus',changed);document.removeEventListener('visibilitychange',changed);};
  const setBusy=value=>{busy=value;panel.querySelectorAll('button').forEach(button=>{button.disabled=value;});};
  const errorText=error=>error?.status===401?'登录已失效，请重新登录。':error?.status===403?'当前账号没有查看权限。':error?.status===429?'已有检查正在运行，请稍后刷新记录。':'检查未完成，请先刷新结果；如仍无记录，请检查服务和浏览器依赖。';
  const request=async(method,timeoutMs,id,scenario)=>{
    if(!valid())throw Error('session changed');
    if(method==='DELETE'&&!/^[a-f0-9]{32}$/.test(id||''))throw Error('invalid id');
    const sequence=serial,controller=new AbortController();uiSimpleDownloads.add(controller);const timer=setTimeout(()=>controller.abort(),timeoutMs);
    try{
      const body=method==='POST'?JSON.stringify({scenario}):method==='DELETE'?'{"confirm":true}':null;
      const response=await nativeFetch('/api/ui-executions/web-check'+(method==='DELETE'?'?id='+id:''),{method,headers:authHeaders(body?{'Content-Type':'application/json'}:{}),...(body?{body}:{}),signal:controller.signal,redirect:'error'});
      if(!valid()||controller.signal.aborted||sequence!==serial)throw Error('session changed');
      if(!response.ok)throw Object.assign(Error('request failed'),{status:response.status});
      const data=await response.json();if(!valid()||controller.signal.aborted||sequence!==serial)throw Error('session changed');return data;
    }finally{clearTimeout(timer);uiSimpleDownloads.delete(controller);}
  };
  const openArtifact=async(row,id,item,measurement=null,feedback=notice)=>{
    if(!valid()||busy||!row.isConnected||(item!=='example'&&(!/^[a-f0-9]{32}$/.test(id)||!['screenshot','trace'].includes(item))))return;
    setBusy(true);const controller=new AbortController();uiSimpleDownloads.add(controller);artifactRequests.add(controller);const timer=setTimeout(()=>controller.abort(),30000);
    const image=item==='screenshot',mime=image?'image/png':'application/zip',limit=(image?8:30)*1024*1024;
    try{
      const qs=item==='example'?new URLSearchParams({example:'smoke'}):new URLSearchParams({id,item,...(measurement||{})});
      const response=await nativeFetch('/api/ui-executions/web-check/artifact?'+qs,{headers:authHeaders(),signal:controller.signal,redirect:'error'});
      if(!valid()||controller.signal.aborted||!row.isConnected)return;
      if(!response.ok)throw Object.assign(Error('unavailable'),{status:response.status});
      if((response.headers.get('content-type')||'').split(';')[0].trim().toLowerCase()!==mime||Number(response.headers.get('content-length'))>limit)throw Error('invalid evidence');
      let blob;
      if(response.body?.getReader){const reader=response.body.getReader(),chunks=[];let size=0;try{while(true){const part=await reader.read();if(!valid()||controller.signal.aborted||!row.isConnected){await reader.cancel();return;}if(part.done)break;size+=part.value.byteLength;if(size>limit)throw Error('too large');chunks.push(part.value);}blob=new Blob(chunks,{type:mime});}catch(error){await reader.cancel().catch(()=>{});throw error;}finally{reader.releaseLock();}}
      else {const length=response.headers.get('content-length');if(!length||!/^\d+$/.test(length)||Number(length)<1||Number(length)>limit)throw Error('invalid evidence length');blob=await response.blob();}
      if(!valid()||controller.signal.aborted||!row.isConnected||!blob.size||blob.size>limit)return;
      const src=URL.createObjectURL(blob);
      if(image){if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=src;page.querySelector('[data-catalog-preview]')?.remove();const img=document.createElement('img');img.dataset.catalogPreview='';img.alt='用例库页面检查截图';img.style.maxWidth='100%';img.style.maxHeight='320px';img.src=src;row.append(img);feedback.textContent='已加载本次页面截图。';}
      else {const link=document.createElement('a');link.href=src;link.download=item==='example'?'流水线与视觉演练.zip':'用例库页面检查-trace.zip';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(src),60000);feedback.textContent='已发起证据下载，请查看浏览器下载记录。';}
    }catch(error){if(valid()&&row.isConnected){if([401,403,404].includes(error?.status)){clear();if(measurement)clearMeasurement();}feedback.textContent=error?.status===401?'登录已失效，请重新登录。':error?.status===403||error?.status===404?'证据不存在或当前账号无权查看。':'证据读取失败，请稍后重试。';}}
    finally{clearTimeout(timer);uiSimpleDownloads.delete(controller);artifactRequests.delete(controller);if(valid())setBusy(false);}
  };
  const example=stability.querySelector('[data-smoke-example]');
  example.querySelector('button').onclick=()=>void openArtifact(example,'','example',null,example.querySelector('[data-example-state]'));
  const render=items=>{
    clear();if(!items.length){history.textContent='暂无检查记录。';return;}
    for(const item of items){if(!Object.hasOwn(labels,item?.scenario)&&!item?.scope_changed)continue;const row=document.createElement('section');row.className='ui-week-probe-row';
      const label=labels[item.scenario]||'';
      const drillFailed=item.scenario==='ui_catalog_failure_drill'&&item.status==='failed'&&item.error_kind==='drill_assertion';
      const heading=document.createElement('strong');heading.textContent=item.deletion_pending?'删除未完成，可再次尝试':item.scope_changed?'权限范围已变化，历史内容不可查看；该记录仍占用存储，可删除':label+' · '+(drillFailed?'演练断言按预期失败':({passed:'页面显示一致',failed:'页面检查未通过',blocked:'检查阻塞'})[item.status]||'状态未知');row.append(heading);
      const meta=document.createElement('p');meta.textContent=(Number.isFinite(item.created)?'时间：'+uiPracticeTime(item.created*1000)+'；':'')+(Number.isSafeInteger(item.total)?'执行时接口返回数量：'+item.total+'。':'')+(({'authentication':'登录状态或权限不可用。','environment':'页面、服务或浏览器依赖不可用。','business':'页面显示与接口返回不一致或页面必要元素缺失。','evidence':'检查证据未能保存。'})[item.error_kind]||'');row.append(meta);
      if(drillFailed){const comparison=document.createElement('p');comparison.textContent='演练预期 '+item.expected_total+' 条，页面实际显示 '+item.total+' 条。打开 trace 可定位失败步骤。';row.append(comparison);}
      if(item.scenario==='ui_catalog_paging'&&item.status==='passed'){
        const pages=document.createElement('p');pages.textContent=item.pages_checked===2?'已核对第一页、第二页及返回第一页的列表和按钮。':'当前应用只有一页，已核对列表及上下页按钮禁用。';row.append(pages);
      }
      if(item.scenario==='ui_start_switch'&&item.switched_app_id){const app=document.createElement('p');app.textContent='切换到 '+item.switched_app_id+' 后已清空选中用例。';row.append(app);}
      if(item.scenario==='ui_start_unready'&&Number.isSafeInteger(item.device_count)){const devices=document.createElement('p');devices.textContent=item.device_count+' 个设备连接均不可选，开始测试按钮已禁用。';row.append(devices);}
      if(item.error_kind==='precondition'){
        const reasons={no_case_module:'当前用例没有可筛选的模块。',catalog_changed:'目录数据已变化，请重新检查。',execute_permission_required:'当前账号需要 UI 执行权限才能勾选用例。',no_published_case:'当前应用目录没有可选的已发布用例。',no_second_app:'此检查需要另一个已授权应用。',case_version_changed:'执行时目录中的用例或版本已变更，请重新检查。',no_devices:'当前应用没有可核对状态的手机。',ready_device_present:'当前有就绪手机，本次未覆盖全部手机均不可用的条件。'};
        const reason=document.createElement('p');reason.textContent=reasons[item.detail]||'缺少所需的现场条件。';row.append(reason);
      }
      if(item.asset&&typeof item.asset.app_id==='string'&&typeof item.asset.case_id==='string'&&/^[a-f0-9]{64}$/.test(item.asset.revision||'')){const asset=document.createElement('p');asset.textContent='执行时资产：'+item.asset.app_id+' / '+item.asset.case_id+'；版本：'+item.asset.revision+'。重复检查时请比较资产及版本。';row.append(asset);}
      if(/^[a-f0-9]{32}$/.test(item.id||'')){
        if(!item.deletion_pending&&!item.scope_changed&&Array.isArray(item.artifacts)){for(const [key,label] of [['screenshot','查看截图'],['trace','下载 trace']])if(item.artifacts.includes(key)){const button=document.createElement('button');button.type='button';button.dataset.catalogArtifact=key;button.textContent=label;button.onclick=()=>void openArtifact(row,item.id,key);row.append(button);}}
        const remove=document.createElement('button');remove.type='button';remove.dataset.catalogDelete='';remove.textContent=item.deletion_pending?'重试删除':'删除本条记录';remove.onclick=async()=>{
          if(!valid()||busy||!row.isConnected)return;
          if(!window.confirm('将永久删除本条检查记录及其截图和 trace，无法恢复；不会影响其他记录。确认删除吗？'))return;
          if(!valid()||!row.isConnected)return;for(const controller of artifactRequests)controller.abort();clear();setBusy(true);notice.textContent='正在删除本条检查记录…';
          let confirmed=false;
          try{const data=await request('DELETE',30000,item.id);if(!valid())return;if(data?.ok!==true||data.deleted!==true)throw Error('invalid deletion');confirmed=true;clear();await load();if(valid())notice.textContent='本条检查记录和证据已删除。';}
          catch(error){if(valid()){clear();if(confirmed)notice.textContent='本条记录已删除，但记录刷新失败，请稍后刷新。';else if(error?.status===409){try{await load();}catch(_){}if(valid())notice.textContent='删除未完成，请检查记录并重试。';}else if(error?.status===401||error?.status===403)notice.textContent=errorText(error);else notice.textContent='删除结果尚未确认，请先刷新记录再决定是否重试。';}}
          finally{if(valid())setBusy(false);}
        };row.append(remove);
      }
      history.append(row);
    }
  };
  const load=async(fresh)=>{const sequence=++serial,data=await request('GET',10000);if(!valid()||sequence!==serial)return;if(data?.ok!==true||!Array.isArray(data.results))throw Error('invalid history');const items=[...data.results];if(fresh?.id&&!items.some(item=>item.id===fresh.id))items.unshift(fresh);render(items);loaded=true;notice.textContent='显示当前账号可查看的检查记录。';};
  panel.addEventListener('toggle',async()=>{if(!panel.open||loaded||busy||!valid())return;setBusy(true);try{await load();}catch(error){if(valid())notice.textContent=errorText(error);}finally{if(valid())setBusy(false);}});
  panel.querySelector('[data-catalog-refresh]').onclick=async()=>{if(!valid()||busy)return;setBusy(true);try{await load();}catch(error){if(valid()){clear();notice.textContent=errorText(error);}}finally{if(valid())setBusy(false);}};
  for(const button of panel.querySelectorAll('[data-catalog-scenario]'))button.onclick=async()=>{const scenario=button.dataset.catalogScenario;if(!valid()||busy||!Object.hasOwn(labels,scenario))return;setBusy(true);notice.textContent='服务器正在只读检查页面，请勿重复点击。';let complete=false;try{const data=await request('POST',65000,null,scenario);if(!valid())return;if(data?.ok!==true||!data.result||data.result.scenario!==scenario)throw Error('invalid result');render([data.result]);complete=true;if(data.result.id)await load(data.result);else notice.textContent='检查未生成保存记录，请查看阻塞原因。';}catch(error){if(valid()){if(error?.status===401||error?.status===403){clear();notice.textContent=errorText(error);}else notice.textContent=complete?'本次结果已返回，但历史刷新失败，请稍后刷新。':errorText(error);}}finally{if(valid())setBusy(false);}};
}

function uiPracticeTime(value){
  if(!value)return '未记录';const date=new Date(value);if(Number.isNaN(date.getTime()))return String(value);
  const pad=n=>String(n).padStart(2,'0');return date.getFullYear()+'-'+pad(date.getMonth()+1)+'-'+pad(date.getDate())+' '+pad(date.getHours())+':'+pad(date.getMinutes());
}
function attachUiWeekTwoReal(guide,page,identity,epoch){
  const canRun=()=>hasPermission('platform.configure')&&hasPermission('api.view')&&(currentAccessProfile?.is_superuser===true||currentAccessProfile?.scope?.ui_apps==='*');
  const valid=()=>page.isConnected&&activeWorkflow==='ai_practice'&&identity===uiSimpleIdentity()&&epoch===uiSimpleEpoch&&canRun();
  const panel=document.createElement('details');panel.dataset.week2Real='';panel.className='ui-week-probe';
  panel.innerHTML='<summary>查看和分析已有反馈记录</summary><p>对真实反馈分类的已有记录进行确定性数据检查；不调用 AI，不重新调用业务接口，也不创建业务数据。原始断言和本次检查分开展示，写入与清理结果请查看当前账号有权访问的执行记录。</p>';
  const progress=['W02-01','W02-05'].map(id=>{const line=document.createElement('p');line.dataset.week2Progress='';line.textContent='本人分析结果待核验。';guide.querySelector('[data-week2-task="'+id+'"]').append(line);return line;});
  const scope=document.createElement('p');scope.textContent='可分析默认反馈记录，也可以指定其他真实反馈执行记录；记录详情按当前账号权限核验。';panel.append(scope);
  guide.insertBefore(panel,guide.querySelector('.ui-practice-details'));
  if(!canRun()){const message=document.createElement('p');message.textContent='需要 API 查看与平台配置权限及完整 UI 应用数据范围；还需有对应项目和环境权限。请联系管理员。';panel.append(message);return;}
  panel.insertAdjacentHTML('beforeend','<details><summary>使用其他真实反馈执行记录</summary><p>粘贴本平台执行记录链接或执行编号；只有一条子用例时会自动选择。多条子用例时需要填写子用例编号；两个都留空使用默认记录。服务器会核对账号权限和反馈分类接口，不会发起业务回归。</p><label>执行编号或记录链接 <input type="text" data-week2-execution-id autocomplete="off" placeholder="执行记录链接或 UUID"></label><label>子用例编号（可选） <input type="text" data-week2-case-id autocomplete="off" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"></label></details><div class="ui-week-probe-actions"><button type="button" data-week2-real-run>分析真实反馈执行证据</button><button type="button" data-week2-real-refresh>刷新分析记录</button></div><p data-week2-real-notice role="status" aria-live="polite">展开后读取本人的分析记录。</p><div data-week2-real-history></div>');
  const notice=panel.querySelector('[data-week2-real-notice]'),history=panel.querySelector('[data-week2-real-history]');let busy=false,loaded=false,serial=0;
  const setBusy=value=>{busy=value;panel.querySelectorAll('button,input').forEach(control=>{control.disabled=value;});};
  const setProgressText=text=>{for(const line of progress)line.textContent=text;};
  const fail=error=>{setProgressText('本人分析记录读取失败，数据处理状态待核验；业务规则审核仍待处理。');if(error?.status===401||error?.status===403)history.replaceChildren();notice.textContent=error?.status===401?'登录已失效，请重新登录。':error?.status===403?'没有分析所需权限，请联系管理员。':error?.status===404?'分析服务暂不可用，请稍后刷新或联系管理员。':error?.status===429?'已有分析正在执行，请稍后刷新记录。':'操作未完成，请先刷新记录；如果仍未完成，请检查服务器配置与项目权限。';};
  const status=value=>({passed:'通过',failed:'失败',blocked:'阻塞',running:'进行中'})[value]||'未知';
  const field=value=>value==null?'未记录':String(value);
  const labels={http_status:'HTTP 状态检查',code_integer:'业务码为整数',code_zero_observed:'业务码为观察到的 0',data_list:'data 为列表',item_object:'每项为对象',required_fields:'包含所需字段',sn_string:'sn 为非空字符串',name_string:'name 为非空字符串',children_list:'children 为列表',order_related_boolean:'order_related 为布尔值',extract_count:'提取数量与原数据一致',extract_preserves_input:'提取不修改原输入',invalid_sn_rejected:'拒绝无效 sn',invalid_name_rejected:'拒绝空 name',invalid_children_rejected:'拒绝无效 children',invalid_order_related_rejected:'拒绝无效 order_related',missing_field_rejected:'拒绝缺少必需字段'};
  const isGood=item=>{const assertions=Array.isArray(item.original_assertions)?item.original_assertions:[],checks=Array.isArray(item.checks)?item.checks:[];return item.status==='passed'&&item.source==='platform_record'&&item.source_verification==='server_authorized_platform_read'&&item.business_requests===0&&assertions.length>=3&&assertions.every(a=>a?.passed===true)&&['status_code','json_path','schema'].every(type=>assertions.some(a=>a?.type===type))&&Number.isInteger(item.total)&&item.total>=12&&checks.length===item.total&&checks.every(c=>c?.status==='passed');};
  const render=items=>{
    history.replaceChildren();if(!Array.isArray(items)||!items.length){history.textContent='暂无分析记录。';return;}
    for(const item of items){if(!item||typeof item!=='object')continue;
      const assertions=Array.isArray(item.original_assertions)?item.original_assertions:[],checks=Array.isArray(item.checks)?item.checks:[];
      const good=isGood(item);
      const row=document.createElement('details');row.className='ui-week-probe-row';
      const title=document.createElement('summary');title.textContent='服务器本次分析 · '+(item.status==='passed'&&!good?'结果待核实':status(item.status))+' · '+field(item.total)+' 项 · '+uiPracticeTime(item.created_at);row.append(title);
      const meta=document.createElement('p');meta.textContent='原业务执行编号：'+field(item.execution_id)+'；子用例编号：'+field(item.execution_case_id)+'；分析代码指纹：'+field(item.code_sha256);row.append(meta);
      const source=document.createElement('p');source.textContent='来源：'+(item.source==='platform_record'?'服务器读取已保存的平台执行记录':'未知')+'；来源核验：'+(item.source_verification==='server_authorized_platform_read'?'服务器授权读取':'未核实')+'；本次分析业务请求：'+field(item.business_requests)+'。写入与清理结果请查看当前账号有权访问的执行记录。';row.append(source);
      const original=document.createElement('p');original.textContent='原始平台断言：'+(assertions.length?assertions.map(a=>field(a.type)+' '+(a.passed===true?'通过':a.passed===false?'失败':'未知')).join('；'):'未记录');row.append(original);
      const contract=document.createElement('section');contract.dataset.week2ContractCheck='';
      const contractIntro=document.createElement('p');contractIntro.textContent='历史响应根字段检查：结果基于分析时的合同和脱敏记录快照，仅检查根级必填字段是否存在，不代表完整合同校验，也不代表接口实时状态。删除负控只作用于内存副本，不修改历史记录。';contract.append(contractIntro);
      const contractCheck=item.contract_check,contractFields=Array.isArray(contractCheck?.fields)?contractCheck.fields:[];
      const expectedFields=['code','msg','data'];
      const comparable=item.source==='platform_record'&&item.source_verification==='server_authorized_platform_read'&&contractCheck?.status==='checked'&&contractCheck.scope==='root_required_only'&&contractFields.length===expectedFields.length&&expectedFields.every(name=>contractFields.filter(value=>value?.name===name).length===1)&&contractFields.every(value=>expectedFields.includes(value?.name)&&typeof value.present==='boolean'&&(value.negative_control_rejected===null||typeof value.negative_control_rejected==='boolean'))&&(contractFields.every(value=>value.present)||contractFields.every(value=>value.negative_control_rejected===null));
      if(comparable){const contractList=document.createElement('ul');for(const name of expectedFields){const value=contractFields.find(entry=>entry.name===name),line=document.createElement('li');line.textContent=name+'：'+(value.present?'脱敏记录中存在':'脱敏记录中未见')+'；删除负控：'+(value.negative_control_rejected===true?'删除后拦截':value.negative_control_rejected===false?'删除后未拦截':'未进行');contractList.append(line);}contract.append(contractList);}
      else{const unavailable=document.createElement('p');unavailable.textContent='无法比较：此记录没有可核实的根字段检查结果，可重新分析已有执行记录。';contract.append(unavailable);}row.append(contract);
      const summary=document.createElement('p');summary.textContent='本次数据处理检查：'+field(item.total)+' 项。';row.append(summary);
      const list=document.createElement('ol');for(const check of checks){const line=document.createElement('li');line.textContent=(labels[check?.id]||field(check?.id))+'：'+status(check?.status);list.append(line);}if(list.childElementCount)row.append(list);
      if(item.message){const message=document.createElement('p');message.textContent=field(item.message);row.append(message);}history.append(row);
    }
  };
  const updateProgress=items=>{
    if(!items.length){setProgressText('暂无本人分析记录，数据处理待验证；业务规则审核仍待处理。');return;}
    const item=items[0],row=history.querySelector('details');
    if(!item||item.source!=='platform_record'||!item.execution_id||!item.id||!item.created_at||!row){setProgressText('最新分析记录不可核验，数据处理状态待核验；业务规则审核仍待处理。');return;}
    const checks=Array.isArray(item.checks)?item.checks:[];
    let result='待核验';if(isGood(item))result='通过';else if(item.status==='blocked')result='阻塞';else if(item.status==='failed'&&item.source_verification==='server_authorized_platform_read'&&checks.some(check=>check?.status==='failed'))result='失败';
    for(const line of progress){line.textContent='最新本人数据处理：'+result+'；分析时间：'+uiPracticeTime(item.created_at)+'。业务规则审核仍待处理。 ';const link=document.createElement('button');link.type='button';link.textContent='查看本次分析证据';link.onclick=()=>{if(!valid()||!row.isConnected)return;panel.open=true;row.open=true;row.scrollIntoView?.({block:'nearest'});row.querySelector('summary')?.focus();};line.append(link);}
  };
  const load=async fresh=>{const request=++serial,data=await apiRequest('/platform/week2-real',{timeoutMs:10000});if(!valid()||request!==serial)return;if(data?.ok!==true||!Array.isArray(data.results))throw Error('invalid history');const items=[...data.results];if(fresh?.id&&!items.some(item=>item.id===fresh.id))items.unshift(fresh);render(items);updateProgress(items);loaded=true;notice.textContent='显示已保存的本人分析记录。';};
  const readHistory=async()=>{if(loaded||busy||!valid())return;setBusy(true);notice.textContent='正在读取分析记录…';try{await load();}catch(error){if(valid())fail(error);}finally{if(valid())setBusy(false);}};
  panel.addEventListener('toggle',()=>{if(panel.open)void readHistory();});
  guide.addEventListener('toggle',event=>{if(event.target===guide&&guide.open)void readHistory();});
  panel.querySelector('[data-week2-real-refresh]').onclick=async()=>{if(!valid()||busy)return;setBusy(true);notice.textContent='正在读取分析记录…';try{await load();}catch(error){if(valid())fail(error);}finally{if(valid())setBusy(false);}};
  panel.querySelector('[data-week2-real-run]').onclick=async()=>{if(!valid()||busy)return;const input=panel.querySelector('[data-week2-execution-id]').value.trim(),caseId=panel.querySelector('[data-week2-case-id]').value.trim(),uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;let executionId=input;if(input&&!uuid.test(input)){try{const url=new URL(input,window.location.origin),params=new URLSearchParams(url.hash.startsWith('#/runs?')?url.hash.slice(7):'');executionId=url.origin===window.location.origin&&url.pathname==='/api-test/'&&!url.search&&params.getAll('executionId').length===1?params.get('executionId'):'';}catch(_){executionId='';}if(!uuid.test(executionId)){notice.textContent='请填写有效的执行编号（UUID）或本平台执行记录链接。';return;}}if(caseId&&!executionId){notice.textContent='填写子用例编号时，也需要填写执行编号。';return;}if(caseId&&!uuid.test(caseId)){notice.textContent='子用例编号格式不正确，请填写有效 UUID。';return;}const body=executionId?{execution_id:executionId,...(caseId?{case_id:caseId}:{})}:{};setBusy(true);notice.textContent='服务器正在分析已有证据，请等待，不要重复点击。';let completed=false;try{const data=await apiRequest('/platform/week2-real',{method:'POST',body,timeoutMs:120000});if(!valid())return;if(data?.ok!==true||!data.result)throw Error('invalid result');completed=true;render([data.result]);await load(data.result);}catch(error){if(valid()){fail(error);if(completed&&![401,403,404].includes(error?.status))notice.textContent='本次结果已返回，但历史刷新失败；可稍后刷新记录。';}}finally{if(valid())setBusy(false);}};
}

// These are fixed server-side checks, never client-provided URLs or shell commands.
function attachUiWeekOneSmoke(slot,current){
  const panel=document.createElement('details');panel.dataset.weekSmoke='';panel.className='ui-week-probe';
  const summary=document.createElement('summary');summary.textContent='服务器体检、API / UI 冒烟与结果';panel.append(summary);slot.append(panel);
  const canRun=()=>hasPermission('platform.configure')&&(currentAccessProfile?.is_superuser===true||currentAccessProfile?.scope?.ui_apps==='*');
  if(!canRun()){
    const message=document.createElement('p');message.textContent='运行和查看服务器冒烟需要平台配置权限及完整 UI 应用数据范围，请联系管理员。';panel.append(message);return;
  }
  panel.insertAdjacentHTML('beforeend','<p>选择一项后，服务器执行固定的只读检查，不需要填写地址或凭据。环境体检的网络响应不代表模型认证成功，文本和视觉模型需在上方单独验证。</p><p>API 检查匿名访问返回 4009，表示未登录被拒绝，不是已认证业务成功；UI 检查匿名登录页，不提交登录或操作手机。</p><p>API 和 UI 各包含正常断言和故意失败：故意失败是预期演练，要核对实际值与退出码 1；依赖缺失或运行错误会显示阻塞或失败。</p><div class="ui-week-probe-actions"><button type="button" data-smoke-run="environment">运行环境体检</button><button type="button" data-smoke-run="api">运行 API 冒烟</button><button type="button" data-smoke-run="ui">运行 UI 冒烟</button><button type="button" data-smoke-refresh>刷新结果</button></div><p data-smoke-notice role="status" aria-live="polite"></p><div data-smoke-history></div>');
  const notice=panel.querySelector('[data-smoke-notice]'),history=panel.querySelector('[data-smoke-history]');
  let busy=false,loaded=false,serial=0;
  const valid=()=>current()&&canRun();
  const setBusy=value=>{busy=value;panel.querySelectorAll('button').forEach(button=>{button.disabled=value;});};
  const fail=error=>{notice.textContent=error?.status===403?'没有所需权限或完整应用范围，请联系管理员。':error?.status===401?'登录已失效，请重新登录。':error?.status===429?'已有服务器冒烟正在执行，请稍后刷新结果。':'操作未完成；请求可能仍在服务器执行，请先刷新结果，再检查服务和依赖。';};
  const status=value=>({passed:'通过',failed:'失败',blocked:'阻塞',running:'进行中'})[value]||'状态未知';
  const field=value=>value==null?'未记录':typeof value==='object'?JSON.stringify(value):String(value);
  const openArtifact=async(row,id,item,measurement=null,feedback=notice)=>{
    if(!valid()||busy||!row.isConnected||!/^[a-f0-9]{32}$/.test(id)||!['positive-screenshot','negative-screenshot','positive-trace','negative-trace'].includes(item))return;
    setBusy(true);notice.textContent='正在读取本人的受控证据…';
    const controller=new AbortController();uiSimpleDownloads.add(controller);
    const live=()=>!controller.signal.aborted&&valid()&&row.isConnected;
    const ensureLive=()=>{if(!live()){controller.abort();throw new DOMException('已取消','AbortError');}};
    let timedOut=false;const timeout=setTimeout(()=>{timedOut=true;controller.abort();},30000),screenshot=item.endsWith('-screenshot'),mime=screenshot?'image/png':'application/zip',limit=screenshot?8*1024*1024:25*1024*1024;
    try{
      const response=await nativeFetch('/api/platform/week1-smoke/artifact?id='+id+'&item='+item,{headers:authHeaders(),signal:controller.signal,redirect:'error'});
      ensureLive();
      if(!response.ok)throw Object.assign(new Error('artifact unavailable'),{status:response.status});
      if((response.headers.get('content-type')||'').split(';')[0].trim().toLowerCase()!==mime||Number(response.headers.get('content-length'))>limit)throw Error('invalid artifact');
      let blob;
      if(response.body?.getReader){
        const reader=response.body.getReader(),chunks=[];let size=0;
        try{while(true){const part=await reader.read();ensureLive();if(part.done)break;size+=part.value.byteLength;if(size>limit)throw Error('artifact too large');chunks.push(part.value);}blob=new Blob(chunks,{type:mime});}
        catch(error){await reader.cancel().catch(()=>{});throw error;}finally{reader.releaseLock();}
      }else blob=await response.blob();
      ensureLive();if(!blob.size||blob.size>limit)throw Error('invalid artifact');
      const key=(screenshot?'week-one-smoke-image|':'week-one-smoke-download|')+uiSimpleIdentity()+'|'+id+'|'+item,old=uiSimplePictures.get(key);if(old)URL.revokeObjectURL(old);
      if(screenshot&&row.dataset.smokePreviewKey&&row.dataset.smokePreviewKey!==key){const previous=uiSimplePictures.get(row.dataset.smokePreviewKey);if(previous)URL.revokeObjectURL(previous);uiSimplePictures.delete(row.dataset.smokePreviewKey);}
      const src=URL.createObjectURL(blob);uiSimplePictures.set(key,src);
      if(screenshot){
        const img=document.createElement('img');img.dataset.smokePreview='';img.alt=item.startsWith('positive')?'正常断言截图':'故意失败截图';img.style.maxWidth='100%';img.style.maxHeight='320px';img.style.display='block';img.src=src;
        row.dataset.smokePreviewKey=key;
        row.querySelector('[data-smoke-preview]')?.remove();row.append(img);notice.textContent='截图已加载；这是本次服务器运行的证据。';
      }else{
        const link=document.createElement('a');link.href=src;link.download='第1周-'+(item.startsWith('positive')?'正常断言':'故意失败')+'-trace.zip';document.body.append(link);link.click();link.remove();
        setTimeout(()=>{if(uiSimplePictures.get(key)===src){URL.revokeObjectURL(src);uiSimplePictures.delete(key);}},60000);notice.textContent='trace 下载已发起，请查看浏览器下载记录确认结果。';
      }
    }catch(error){if(valid()&&row.isConnected&&(timedOut||!controller.signal.aborted))notice.textContent=timedOut?'读取证据超时，请稍后重试。':error?.status===401?'登录已失效，请重新登录。':error?.status===403?'当前账号没有查看证据的权限。':error?.status===404?'证据不存在或当前账号无法查看。':'读取证据失败，请检查网络或稍后重试。';}
    finally{clearTimeout(timeout);uiSimpleDownloads.delete(controller);if(valid())setBusy(false);}
  };
  const render=items=>{
    for(const [key,src] of uiSimplePictures){if(key.startsWith('week-one-smoke-image|')){URL.revokeObjectURL(src);uiSimplePictures.delete(key);}}
    history.replaceChildren();
    if(!Array.isArray(items)||!items.length){history.textContent='暂无服务器冒烟记录。';return;}
    for(const item of items){
      const row=document.createElement('section');row.className='ui-week-probe-row';
      const title=document.createElement('strong');title.textContent=(item.kind==='environment'?'环境体检':item.kind==='api'?'API 冒烟':item.kind==='ui'?'UI 冒烟':'未知类型')+' · '+status(item.status);row.append(title);
      const meta=document.createElement('p');meta.textContent='时间：'+field(item.created_at);row.append(meta);
      for(const check of Array.isArray(item.checks)?item.checks:[]){
        const line=document.createElement('p');line.textContent=field(check.name)+'：'+status(check.status)+'；预期：'+field(check.expected)+'；实际：'+field(check.actual)+'；退出码：'+field(check.exit_code);row.append(line);
      }
      if(item.kind==='ui'&&item.artifacts){
        const evidence=document.createElement('p');evidence.textContent='截图与 trace：'+(({saved:'已保存在服务器受控目录，截图可预览；HTTP 站点的 trace 下载可能被浏览器拦截，请使用 HTTPS 或联系管理员。',partial:'部分保存；截图可预览，缺失项无法查看或下载。HTTP 站点的 trace 下载可能被浏览器拦截，请使用 HTTPS 或联系管理员。',unavailable:'未保存'})[item.artifacts]||'状态未知');row.append(evidence);
        if(['saved','partial'].includes(item.artifacts)&&/^[a-f0-9]{32}$/.test(item.id||'')){
          for(const [name,label] of [['positive-screenshot','查看正常截图'],['negative-screenshot','查看故意失败截图'],['positive-trace','下载正常 trace'],['negative-trace','下载故意失败 trace']]){
            const button=document.createElement('button');button.type='button';button.disabled=busy;button.dataset.smokeArtifact=name;button.textContent=label;button.onclick=()=>void openArtifact(row,item.id,name);row.append(button);
          }
        }
      }
      if(item.message){const message=document.createElement('p');message.textContent=String(item.message);row.append(message);}
      history.append(row);
    }
  };
  const load=async(fresh)=>{
    const request=++serial,data=await apiRequest('/platform/week1-smoke',{timeoutMs:10000});
    if(!valid()||request!==serial)return;
    if(data?.ok!==true||!Array.isArray(data.results))throw Error('invalid history');
    const items=[...data.results];
    if(fresh?.id&&!items.some(item=>item.id===fresh.id))items.unshift(fresh);
    render(items);loaded=true;notice.textContent='显示已保存的服务器冒烟结果。';
  };
  panel.addEventListener('toggle',async()=>{if(!panel.open||loaded||busy||!valid())return;setBusy(true);notice.textContent='正在读取历史结果…';try{await load();}catch(error){if(valid())fail(error);}finally{if(valid())setBusy(false);}});
  panel.querySelector('[data-smoke-refresh]').onclick=async()=>{if(!valid()||busy)return;setBusy(true);notice.textContent='正在读取历史结果…';try{await load();}catch(error){if(valid())fail(error);}finally{if(valid())setBusy(false);}};
  for(const button of panel.querySelectorAll('[data-smoke-run]'))button.onclick=async()=>{
    if(!valid()||busy)return;const kind=button.dataset.smokeRun;if(!['environment','api','ui'].includes(kind))return;setBusy(true);notice.textContent='服务器正在执行，请等待，不要重复点击。';
    let completed=false;
    try{const data=await apiRequest('/platform/week1-smoke',{method:'POST',body:{kind},timeoutMs:120000});if(!valid())return;if(data?.ok!==true||!data.result)throw Error('invalid result');completed=true;render([data.result]);await load(data.result);}
    catch(error){if(valid()){if(completed)notice.textContent='本次结果已返回，但历史刷新失败；可稍后刷新结果。';else fail(error);}}finally{if(valid())setBusy(false);}
  };
}

// A bounded probe uses only server-controlled inputs; a successful vision response
// remains pending until a person checks the answer against the fixed fixture.
function attachUiWeekOneProbe(slot,current){
  const panel=document.createElement('details');panel.dataset.weekProbe='';panel.className='ui-week-probe';
  const summary=document.createElement('summary');summary.textContent='千问真实调用与历史证据';panel.append(summary);slot.append(panel);
  const canProbe=()=>hasPermission('platform.configure')&&(currentAccessProfile?.is_superuser===true||currentAccessProfile?.scope?.ui_apps==='*');
  if(!canProbe()){
    const message=document.createElement('p');message.textContent='运行和查看模型探针需要平台配置权限及完整 UI 应用数据范围，请联系管理员。';panel.append(message);return;
  }
  panel.insertAdjacentHTML('beforeend','<p>使用 Task Server 服务器环境配置直连千问，不经过模型配置页面的网关策略；配置有问题请联系管理员检查服务器设置。真实调用可能产生模型消耗，每次点击执行一次，请先确认账号额度。</p><p>视觉测试使用固定图：左侧红色矩形，右侧蓝色圆。收到回答后请人工核对，调用成功不等于识别正确。</p><div class="ui-week-probe-actions"><button type="button" data-probe-run="text">运行文本探针</button><button type="button" data-probe-run="vision">运行视觉探针</button><button type="button" data-probe-refresh>刷新历史记录</button></div><p data-probe-notice role="status" aria-live="polite"></p><div data-probe-history></div>');
  const notice=panel.querySelector('[data-probe-notice]'),history=panel.querySelector('[data-probe-history]');
  const img=document.createElement('img');img.dataset.probeFixture='';img.hidden=true;img.alt='视觉测试固定图片：左红色矩形，右蓝色圆';
  const imageNote=document.createElement('p');imageNote.textContent='固定图片加载中；如未显示，请依据文字说明核对。';
  panel.querySelector('.ui-week-probe-actions').before(img,imageNote);
  let busy=false,loaded=false,serial=0;
  const valid=()=>current()&&canProbe();
  const loadFixture=async()=>{
    const key='week-one-fixture|'+uiSimpleIdentity();
    if(uiSimplePictures.has(key)){img.src=uiSimplePictures.get(key);img.hidden=false;imageNote.hidden=true;return;}
    const controller=new AbortController();uiSimplePictureRequests.add(controller);
    try{
      const response=await nativeFetch('/api/platform/week1-probes/fixture',{headers:authHeaders(),signal:controller.signal});
      if(!response.ok||!(response.headers.get('content-type')||'').toLowerCase().startsWith('image/png'))throw new Error('image unavailable');
      const blob=await response.blob();if(!valid()||blob.size>1024*1024)return;
      const src=URL.createObjectURL(blob);uiSimplePictures.set(key,src);img.src=src;img.hidden=false;imageNote.hidden=true;
    }catch(_){if(valid())imageNote.textContent='固定图片暂时无法显示，请按文字说明核对模型回答。';}
    finally{uiSimplePictureRequests.delete(controller);}
  };
  const setBusy=value=>{busy=value;panel.querySelectorAll('button').forEach(button=>{button.disabled=value;});};
  const fail=error=>{notice.textContent=error?.status===403?'当前账号没有平台配置权限，请联系管理员。':error?.status===401?'登录已失效，请重新登录后查看记录。':error?.status===429?'已有探针正在执行或刚执行完，请稍等后刷新历史记录。':'操作未完成，请检查服务与模型配置后重试；不要连续重复点击。';};
  const errorHelp=code=>({missing_key:'未配置千问 Key，请联系管理员在服务器配置。',unsupported_endpoint:'接口地址不匹配，请检查千问地域和地址配置。',invalid_model:'模型名称无效，请检查配置。',model_http_401:'千问认证失败，请检查 Key 和所属地域。',model_http_403:'千问拒绝访问，请检查模型权限和账户状态。',model_http_429:'千问限流或额度不足，请检查账户额度后稍等重试。',model_request_failed:'网络错误或请求超时，请检查服务器网络与服务状态。',invalid_response:'模型响应格式无法识别，请检查模型及接口配置。',response_too_large:'模型响应超出限制，请检查模型配置。',configuration_or_response_error:'模型配置或响应不正确，请检查配置。'})[code]||'请检查模型配置、账号权限及服务状态。';
  const render=items=>{
    history.replaceChildren();
    if(!Array.isArray(items)||!items.length){history.textContent='暂无探针记录。';return;}
    for(const item of items){
      const row=document.createElement('section');row.className='ui-week-probe-row';
      const title=document.createElement('strong');title.textContent=(item.mode==='vision'?'视觉':'文本')+' · '+(item.status==='success'?'调用成功':'调用失败');row.append(title);
      const meta=document.createElement('p');meta.textContent='时间：'+String(item.created_at||'未记录')+'；请求模型：'+String(item.model||'未记录')+'；调用链：'+(item.source==='platform_direct'?'平台服务直连':'未记录');row.append(meta);
      if(Number.isFinite(item.elapsed_ms)){const elapsed=document.createElement('p');elapsed.textContent='耗时：'+item.elapsed_ms+' ms';row.append(elapsed);}
      if(item.answer){const answer=document.createElement('p');answer.textContent='模型回答：'+String(item.answer);row.append(answer);}
      if(item.mode==='text'&&item.status==='success'){const check=document.createElement('p');check.textContent='文本结果：'+(item.text_check==='passed'?'与 CONNECTED 匹配':item.text_check==='failed'?'与 CONNECTED 不匹配':'未记录核对结果')+'。';row.append(check);}
      if(item.error_code){const error=document.createElement('p');error.textContent=errorHelp(item.error_code)+' 结果代码：'+String(item.error_code);row.append(error);}
      if(item.usage){const usage=document.createElement('p');usage.textContent='用量：'+JSON.stringify(item.usage);row.append(usage);}
      if(item.mode==='vision'&&item.status==='success'){
        const review=document.createElement('p');const state=item.manual_check;review.textContent='人工核对：'+(state==='passed'?'人工核对通过':state==='failed'?'人工核对不通过':'待人工核对')+'。对照：左侧红色矩形，右侧蓝色圆。';row.append(review);
        if(item.confirmed_at){const confirmed=document.createElement('p');confirmed.textContent='核对时间：'+String(item.confirmed_at);row.append(confirmed);}
        for(const [correct,label] of [[true,state==='pending'?'识别正确':'重新核对：识别正确'],[false,state==='pending'?'识别不正确':'重新核对：识别不正确']]){
          const button=document.createElement('button');button.type='button';button.dataset.probeReview=String(correct);button.textContent=label;
          button.onclick=async()=>{if(!valid()||busy)return;setBusy(true);notice.textContent='正在保存人工核对结果…';try{await apiRequest('/platform/week1-probes/confirm',{method:'POST',body:{id:item.id,correct}});if(!valid())return;await load();}catch(error){if(valid())fail(error);}finally{if(valid())setBusy(false);}};row.append(button);
        }
      }
      history.append(row);
    }
  };
  const load=async()=>{
    const request=++serial;const data=await apiRequest('/platform/week1-probes',{timeoutMs:10000});
    if(!valid()||request!==serial)return;render(data?.results);loaded=true;notice.textContent='显示已保存的最近探针记录。';
  };
  panel.addEventListener('toggle',async()=>{if(!panel.open||loaded||busy||!valid())return;void loadFixture();setBusy(true);notice.textContent='正在读取历史记录…';try{await load();}catch(error){if(valid())fail(error);}finally{if(valid())setBusy(false);}});
  panel.querySelector('[data-probe-refresh]').onclick=async()=>{if(!valid()||busy)return;setBusy(true);notice.textContent='正在读取历史记录…';try{await load();}catch(error){if(valid())fail(error);}finally{if(valid())setBusy(false);}};
  for(const button of panel.querySelectorAll('[data-probe-run]'))button.onclick=async()=>{
    if(!valid()||busy)return;const mode=button.dataset.probeRun;if(!['text','vision'].includes(mode))return;setBusy(true);notice.textContent='正在真实调用，请等待结果；请勿重复操作。';
    try{await apiRequest('/platform/week1-probes',{method:'POST',body:{mode},timeoutMs:120000});if(!valid())return;await load();}catch(error){if(valid())fail(error);}finally{if(valid())setBusy(false);}
  };
}
// Use the existing library creation flow so drafts and navigation guards stay shared.
function attachUiStartCreation(page){
  const identity=uiSimpleIdentity(),epoch=uiSimpleEpoch;
  const toolbar=document.createElement('div');toolbar.className='ui-case-creation-toolbar';
  toolbar.style.flexWrap='wrap';
  const help=document.createElement('span');help.dataset.uiStartCreateHelp='';help.textContent='没有现成用例？可从需求材料、文字、视频或操作录制创建。';
  help.style.flex='1 1 260px';
  const create=document.createElement('button');create.type='button';create.className='btn-sm';create.dataset.uiStartCreate='';create.textContent='创建用例';
  create.disabled=!hasPermission('ui.edit')||!window.UiCaseDraft;
  if(!hasPermission('ui.edit')){create.title='创建用例需要用例编辑权限';help.textContent='需要创建用例？请联系管理员开通用例编辑权限。';}
  else if(!window.UiCaseDraft){create.title='创建用例功能暂不可用，请刷新后重试';help.textContent='创建用例功能暂不可用，请刷新页面后重试。';}
  create.onclick=async()=>{
    if(!page.isConnected||activeWorkflow!=='ui_start'||identity!==uiSimpleIdentity()||epoch!==uiSimpleEpoch||!uiSimpleEnabled||!hasPermission('ui.edit')||!window.UiCaseDraft)return;
    create.disabled=true;
    const intent={page,identity,epoch};uiStartCreationIntent=intent;
    try{
      await activateWorkflow('ui_library');
    }catch(error){
      if(page.isConnected&&identity===uiSimpleIdentity()&&epoch===uiSimpleEpoch)showToast(error?.message||'无法打开创建用例，请重试','error');
    }finally{
      if(uiStartCreationIntent===intent)uiStartCreationIntent=null;
      if(page.isConnected)create.disabled=!hasPermission('ui.edit')||!window.UiCaseDraft;
    }
  };
  toolbar.append(help,create);page.prepend(toolbar);
}
// Creation is a child of the library; existing recording/material workflows remain available.
function attachUiCaseCreation(page){
  const epoch=uiCaseCreationEpoch,identity=uiSimpleIdentity();
  let imported=uiImportedDraft?.identity===identity?uiImportedDraft.draft:null,videoResume=null;uiImportedDraft=null;
  const current=()=>epoch===uiCaseCreationEpoch&&identity===uiSimpleIdentity()&&activeWorkflow==='ui_library'&&page.isConnected;
  const canEdit=()=>uiSimpleEnabled&&hasPermission('ui.edit');
  const toolbar=document.createElement('div');toolbar.className='ui-case-creation-toolbar';
  const create=document.createElement('button');create.type='button';create.className='btn-sm';create.dataset.uiCreateCase='';create.textContent='创建用例';create.disabled=!canEdit()||!window.UiCaseDraft;
  if(!canEdit())create.title='创建用例需要用例编辑权限';
  const drafts=document.createElement('button');drafts.type='button';drafts.className='btn-sm';drafts.dataset.uiResumeDrafts='';drafts.textContent='继续我的草稿';drafts.disabled=create.disabled;
  drafts.onclick=()=>{if(!current()||!canEdit())return;create.click();page.querySelector('[data-ui-case-source="text"]')?.click();};
  const videoHistory=document.createElement('button');videoHistory.type='button';videoHistory.className='btn-sm';videoHistory.dataset.uiVideoHistory='';videoHistory.textContent='视频生成记录';videoHistory.disabled=create.disabled;
  videoHistory.onclick=()=>{if(!current()||!canEdit())return;create.click();page.querySelector('[data-ui-case-source="video"]')?.click();const history=page.querySelector('[data-video-history]');if(history)history.open=true;page.querySelector('[data-video-app]')?.focus();};
  toolbar.append(drafts,videoHistory,create);page.prepend(toolbar);
  create.onclick=()=>{
    if(!current()||!canEdit()||!window.UiCaseDraft)return;
    window.UiSimpleFlow?.leave();page.replaceChildren();
    const back=document.createElement('button');back.type='button';back.className='btn-sm';back.dataset.uiCaseBack='';back.textContent='返回用例库';
    back.onclick=()=>{if(current()&&confirmUiCaseDraftLeave())showUiSimpleWorkflow();};
    const heading=document.createElement('div');heading.className='ui-case-creation-toolbar';heading.append(back);page.append(heading);
    const host=document.createElement('section');host.className='ui-case-source-picker';page.append(host);
    host.innerHTML='<h2>从哪里开始创建用例</h2><p>选择你手上已有的内容，生成后先核对步骤，再试跑。</p><div class="ui-case-source-options"><button type="button" data-ui-case-source="text"><strong>大白话描述</strong><span>写下要做的操作和要检查的结果。</span></button><button type="button" data-ui-case-source="recording"><strong>操作录制</strong><span>连接手机，在平台远控中操作并记录步骤。</span></button><button type="button" data-ui-case-source="materials"><strong>需求材料</strong><span>进入现有需求生成流程，使用需求文档或截图。</span></button><button type="button" data-ui-case-source="video"><strong>上传录屏文件</strong><span>上传 MP4/MOV，按视频时间核对中文步骤。</span></button></div>';
    const access=workflow=>typeof canAccessWorkflow==='function'?canAccessWorkflow(workflow):canEdit();
    for(const [source,workflow]of [['recording','device_recorder'],['materials','generate']]){
      const button=host.querySelector(`[data-ui-case-source="${source}"]`);button.disabled=!access(workflow);if(button.disabled)button.title='当前账号暂不能使用该创建方式';
      button.onclick=()=>{if(current()&&canEdit()&&access(workflow))activateWorkflow(workflow);};
    }
    host.querySelector('[data-ui-case-source="video"]').onclick=()=>{
      if(!current()||!canEdit())return;
      host.className='ui-case-source-picker';host.replaceChildren();
      uiCaseDraftHandle=mountUiVideoSource(host,{current,initialSource:videoResume,openDraft:draft=>{
        if(!current())return;videoResume=null;imported=draft;create.click();page.querySelector('[data-ui-case-source="text"]')?.click();
      }});
    };
    host.querySelector('[data-ui-case-source="text"]').onclick=()=>{
      if(!current()||!canEdit()||!window.UiCaseDraft)return;
      host.className='';host.replaceChildren();
      uiCaseDraftHandle=window.UiCaseDraft.mount(host,{initialDraft:imported,request:apiRequest,identity:uiSimpleIdentity,loadPicture:loadUiSimplePicture,
        canEdit:()=>current()&&canEdit(),canExecute:()=>current()&&uiSimpleEnabled&&hasPermission('ui.execute'),
        canPublish:()=>current()&&canEdit()&&hasPermission('ui.baseline'),
        openVideoSource:source=>{if(!current())return;uiCaseDraftHandle?.dispose();videoResume={source_id:source.source_id,app_id:imported?.app_id||source.app_id};imported=null;create.click();page.querySelector('[data-ui-case-source="video"]')?.click();},
        openMyTests:()=>{if(current())activateWorkflow('ui_reports');}});
    };
  };
  if(imported){create.click();page.querySelector('[data-ui-case-source="text"]')?.click();}
}
async function loadUiBatchImage(path,kind,{signal}={}){
  const epoch=uiSimpleEpoch,identity=uiSimpleIdentity();
  if(!identity)throw new Error('请先登录后查看截图');
  if(!['jpg','png'].includes(kind))throw new Error('截图格式无效');
  const mime=kind==='jpg'?'image/jpeg':'image/png';
  let url;try{url=new URL(path,window.location.origin);}catch(_){throw new Error('截图地址无效');}
  if(url.origin!==window.location.origin||url.href!==url.origin+url.pathname||!/^\/ui-executions\/uib_[A-Za-z0-9_]+\/artifacts\/[a-f0-9]{64}$/.test(url.pathname))throw new Error('截图地址无效');
  const controller=new AbortController(),abort=()=>controller.abort();
  const valid=()=>!controller.signal.aborted&&epoch===uiSimpleEpoch&&identity===uiSimpleIdentity();
  const ensureCurrent=()=>{if(!valid()){abort();throw new DOMException('截图加载已取消','AbortError');}};
  if(signal?.aborted)abort();
  ensureCurrent();signal?.addEventListener('abort',abort,{once:true});uiSimplePictureRequests.add(controller);
  let timedOut=false;const timeout=setTimeout(()=>{timedOut=true;abort();},15000),limit=8*1024*1024;
  try{
    const response=await nativeFetch('/api'+url.pathname,{headers:authHeaders(),signal:controller.signal,redirect:'error'});
    ensureCurrent();
    if(!response.ok)throw Object.assign(new Error(response.status===403?'当前账号无权查看这张截图':`截图加载失败，请重试（${response.status}）`),{status:response.status});
    if((response.headers.get('content-type')||'').split(';')[0].trim().toLowerCase()!==mime)throw new Error('截图格式异常，请重试');
    if(Number(response.headers.get('content-length'))>limit)throw new Error('截图过大，请下载后查看');
    let blob;
    if(response.body?.getReader){
      const reader=response.body.getReader(),chunks=[];let size=0;
      try{
        while(true){
          const {done,value}=await reader.read();ensureCurrent();if(done)break;
          size+=value.byteLength;if(size>limit)throw new Error('截图过大，请下载后查看');chunks.push(value);
        }
        blob=new Blob(chunks,{type:mime});
      }catch(error){await reader.cancel().catch(()=>{});throw error;}
      finally{reader.releaseLock();}
    }else blob=await response.blob();
    ensureCurrent();
    if(blob.size>limit)throw new Error('截图过大，请下载后查看');
    if(!blob.size)throw new Error('截图内容为空，请重试');
    return blob;
  }catch(error){
    if(timedOut)throw new Error('截图加载超时，请重试');
    ensureCurrent();abort();throw error;
  }finally{clearTimeout(timeout);signal?.removeEventListener('abort',abort);uiSimplePictureRequests.delete(controller);}
}
async function downloadUiBatchFile(path){
  const epoch=uiSimpleEpoch,identity=uiSimpleIdentity(),url=new URL(path,window.location.origin);
  const match=url.pathname.match(/^\/ui-executions\/uib_[A-Za-z0-9_]+\/(report\/download|artifacts\/[a-f0-9]{64})$/);
  const format=url.searchParams.get('format');
  if(url.origin!==window.location.origin||!match||(match[1]==='report/download'&&(!['docx','html','md'].includes(format)||!/^[a-f0-9]{64}$/.test(url.searchParams.get('revision')||''))))throw new Error('下载地址无效');
  const controller=new AbortController();uiSimpleDownloads.add(controller);
  const valid=()=>!controller.signal.aborted&&epoch===uiSimpleEpoch&&identity===uiSimpleIdentity();
  const timeout=setTimeout(()=>controller.abort(),60000);
  try{
    const response=await nativeFetch('/api'+url.pathname+url.search,{headers:authHeaders(),signal:controller.signal});
    if(!valid())throw new Error('下载已取消');
    if(!response.ok)throw Object.assign(new Error(response.status===403?'当前账号无权下载这份报告':`下载失败，请重试（${response.status}）`),{status:response.status});
    const mime=(response.headers.get('content-type')||'').split(';')[0].trim();
    const expected={docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',html:'text/html',md:'text/markdown'};
    if(match[1]==='report/download'?mime!==expected[format]:!['text/html','image/png','image/jpeg'].includes(mime))throw new Error('下载文件格式异常，请重试');
    if(Number(response.headers.get('content-length'))>64*1024*1024)throw new Error('文件过大，请联系管理员导出');
    const blob=await response.blob();if(!valid())throw new Error('下载已取消');
    if(blob.size>64*1024*1024)throw new Error('文件过大，请联系管理员导出');
    const filename=responseAttachmentFilename(response,'测试报告.'+(format||({ 'image/png':'png','image/jpeg':'jpg' }[mime]||'html')));
    const src=URL.createObjectURL(blob),link=document.createElement('a');link.href=src;link.download=filename;document.body.append(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(src),60000);return filename;
  }finally{clearTimeout(timeout);uiSimpleDownloads.delete(controller);}
}
function refreshUiSimpleWorkflow(){return window.UiSimpleFlow?.refresh();}


// Consumed synchronously by the library mount; never survives identity/navigation changes.
let uiImportedDraft = null;
function openImportedUiDraft(draft){
  if(!uiSimpleEnabled||!hasPermission('ui.edit')||!draft?.draft_id)return;
  uiImportedDraft={draft,identity:uiSimpleIdentity()};
  activateWorkflow('ui_library');
}

// One visible source task owns polling. Leaving this view never starts card timers.
function mountUiVideoSource(node,{current,openDraft,initialSource=null}){
  const identity=uiSimpleIdentity(),pending=new Set(),urls=new Set(),mediaRequests=new WeakMap();let alive=true,epoch=0,timer=null,source=null,app='',polls=0,busy=false,renderedKey='',statusSequence=0,rows=[],limit=10,listSequence=0,wasHidden=document.hidden,visibilityRead=null;
  const selectedVideos=new Set();let lastListHtml='';
  const q=s=>node.querySelector(s),escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const valid=()=>alive&&current()&&identity===uiSimpleIdentity()&&node.isConnected;
  function clearMedia(){for(const url of urls)URL.revokeObjectURL(url);urls.clear();}
  function invalidate(){epoch++;statusSequence++;renderedKey='';clearTimeout(timer);timer=null;for(const c of pending)c.abort();pending.clear();clearMedia();}
  function dispose(){alive=false;invalidate();clearInterval(guard);document.removeEventListener('visibilitychange',visibilityChanged);}
  function notice(text){if(valid())q('[data-video-notice]').textContent=text;}
  function detailNotice(text){if(node.dataset.videoView==='detail')notice(text);}
  async function request(path,extra={}){
    const c=new AbortController(),version=epoch;pending.add(c);
    try{const data=await apiRequest(path,{...extra,signal:c.signal});if(!valid()||version!==epoch)return null;if(!data?.ok)throw new Error(data?.error||'请求未完成');return data;}
    finally{pending.delete(c);}
  }
  async function media(path,element){
    const previous=mediaRequests.get(element);previous?.controller.abort();
    if(previous?.url){URL.revokeObjectURL(previous.url);urls.delete(previous.url);}
    const c=new AbortController(),version=epoch,entry={controller:c,url:null,sequence:(previous?.sequence||0)+1};
    mediaRequests.set(element,entry);element.removeAttribute('src');pending.add(c);
    const latest=()=>valid()&&version===epoch&&element.isConnected&&mediaRequests.get(element)===entry&&!c.signal.aborted;
    try{const response=await nativeFetch('/api'+path,{headers:authHeaders(),signal:c.signal});if(!latest())return;if(!response.ok)throw new Error('证据读取失败');const blob=await response.blob();if(!latest())return;const url=URL.createObjectURL(blob);entry.url=url;urls.add(url);element.src=url;}
    catch(e){if(latest())detailNotice(e.message);}
    finally{pending.delete(c);}
  }
  node.classList.add('ui-video-source');node.dataset.videoPhase='upload';
  node.innerHTML='<h2>我的视频资料</h2><ol class="ui-video-journey" data-video-journey><li><b>1</b>上传视频</li><li><b>2</b>生成用例</li><li><b>3</b>核对步骤</li><li><b>4</b>手机试跑</li></ol><label>测试应用<select data-video-app><option value="">选择应用</option></select></label><p data-video-notice role="status"></p><section data-video-history data-video-view="list"><button type="button" class="ui-video-primary" data-video-new>上传视频</button><button type="button" data-video-refresh>刷新列表</button><p class="ui-video-library-hint">视频保留至手动删除；空间不足时请清理不需要的记录。关联草稿的原视频作为证据保留。</p><div class="ui-video-list-controls"><label>搜索视频<input type="search" data-video-query placeholder="视频名称或原文件"></label><label>状态<select data-video-filter><option value="">全部状态</option><option value="active">生成中</option><option value="ready">待核对</option><option value="draft">已生成草稿</option><option value="failed">分析失败</option><option value="review_needed">需补充素材</option><option value="cancelled">已取消</option><option value="interrupted">已停止</option></select></label></div><p data-video-list-summary></p><div class="ui-video-selection"><label><input type="checkbox" data-video-select-all> 选择当前显示的记录</label><span data-video-selected-count>已选择 0 条</span><button type="button" data-video-delete-selected disabled>批量删除</button></div><div data-video-list><p>请先选择测试应用，查看自己的视频任务。</p></div><button type="button" data-video-more hidden>加载更多</button></section><section data-video-upload-view data-video-view="upload" hidden><button type="button" data-video-upload-back>返回任务列表</button><h3>上传视频</h3><p>上传包含完整操作的 MP4/MOV，最多200MB、10分钟。</p><div class="ui-video-upload-form"><label>录屏文件<input data-video-file type="file" accept="video/mp4,video/quicktime,.mp4,.mov"></label><details data-video-goal-options><summary>补充测试目标（可选）</summary><label>业务目标或结束检查<input data-video-goal maxlength="2000" placeholder="已有明确目标时可补充，不必重写步骤"></label></details><button type="button" class="ui-video-primary" data-video-upload>生成测试用例</button></div></section><section data-video-detail-view data-video-view="detail" hidden><button type="button" data-video-back>返回任务列表</button><section data-video-detail></section></section>';
  function syncJourney(){node.dataset.videoPhase=node.dataset.videoView==='detail'?(source?.status==='ready'||source?.draft_id?'review':'generate'):'upload';}
  function view(name){if(node.dataset.videoView!==name&&!busy)notice('');node.dataset.videoView=name;syncJourney();for(const panel of node.querySelectorAll('[data-video-view]'))panel.hidden=panel.dataset.videoView!==name;}
  function renderList(){
    const term=q('[data-video-query]').value.trim().toLocaleLowerCase(),filter=q('[data-video-filter]').value;
    const filtered=rows.map(row=>source?.status&&source.source_id===row.source_id&&compareSource(source,row)>=0?source:row).filter(row=>(!term||[row.display_name,row.title,row.filename].some(v=>String(v||'').toLocaleLowerCase().includes(term)))&&(!filter||(filter==='draft'?!!row.draft_id:filter==='active'?['queued','decoding','analyzing'].includes(row.status):filter==='ready'?row.status==='ready'&&!row.draft_id:row.status===filter))).sort((a,b)=>(Number(b.updated_at)||Number(b.created_at)||0)-(Number(a.updated_at)||Number(a.created_at)||0));
    q('[data-video-list-summary]').textContent=`当前应用 ${rows.length} 个视频 · 生成中 ${rows.filter(r=>['queued','decoding','analyzing'].includes((source?.source_id===r.source_id?source:r).status)).length} · 待核对 ${rows.filter(r=>{const item=source?.source_id===r.source_id?source:r;return item.status==='ready'&&!item.draft_id;}).length} · 已生成草稿 ${rows.filter(r=>(source?.source_id===r.source_id?source:r).draft_id).length}`;
    const host=q('[data-video-list]'),focused=document.activeElement,focusId=host.contains(focused)?focused.closest('[data-video-open]')?.dataset.videoOpen:null,focusSelect=host.contains(focused)?focused.closest('[data-video-select]')?.dataset.videoSelect:null;
    const visible=filtered.slice(0,limit),eligible=visible.filter(row=>!['queued','decoding','analyzing'].includes(row.status));
    const html=visible.map(row=>`<article class="ui-video-task-row"><label class="ui-video-select-label"><input type="checkbox" data-video-select="${escape(row.source_id)}" aria-label="选择 ${escape(row.display_name||row.title||row.filename)}" ${selectedVideos.has(row.source_id)?'checked':''} ${['queued','decoding','analyzing'].includes(row.status)?'disabled':''}></label><div><strong title="${escape(row.display_name||row.title||row.filename)}">${escape(row.display_name||row.title||row.filename)}</strong><small title="${escape(row.filename)}">原文件：${escape(row.filename)}</small></div><span>${escape(row.draft_id?'已生成草稿':statusText(row.status))}</span><time>${escape(historyTime(row))}${row.analysis_version?`<small>第 ${escape(row.analysis_version)} 次分析</small>`:''}</time><button type="button" data-video-open="${escape(row.source_id)}">${row.status==='ready'||row.draft_id?'查看并核对':'查看任务'}</button><button type="button" data-video-play="${escape(row.source_id)}">查看视频</button><button type="button" data-video-rename="${escape(row.source_id)}">重命名</button><button type="button" data-video-delete="${escape(row.source_id)}" ${['queued','decoding','analyzing'].includes(row.status)?'disabled title="生成中的任务不能删除"':''}>删除</button></article>`).join('')||'<p>没有符合条件的视频任务。</p>';
    if(lastListHtml!==html){lastListHtml=html;host.innerHTML=html;if(node.dataset.videoView==='list'){const action=focusId?Array.from(host.querySelectorAll('[data-video-open]')).find(button=>button.dataset.videoOpen===focusId):focusSelect?Array.from(host.querySelectorAll('[data-video-select]')).find(input=>input.dataset.videoSelect===focusSelect):null;action?.focus({preventScroll:true});}}
    q('[data-video-more]').hidden=filtered.length<=limit;
    const all=q('[data-video-select-all]');all.disabled=!eligible.length||busy;all.checked=eligible.length>0&&eligible.every(row=>selectedVideos.has(row.source_id));all.indeterminate=eligible.some(row=>selectedVideos.has(row.source_id))&&!all.checked;
    q('[data-video-selected-count]').textContent=`已选择 ${selectedVideos.size} 条`;q('[data-video-delete-selected]').disabled=!selectedVideos.size||busy;
  }
  async function removeVideos(ids){
    if(!valid()||busy||!ids.length||!hasPermission('ui.edit'))return;
    if(!window.confirm(`确定删除 ${ids.length} 条视频生成记录？没有关联草稿、也没有保留证据的记录及原视频将被删除；关联草稿、保留证据或已取消的记录从列表移除，保留原视频及草稿。生成中的任务不能删除。`))return;
    busy=true;q('[data-video-app]').disabled=true;listSequence++;renderList();
    try{const data=await request('/ui-video-sources/history-remove',{method:'POST',body:{app_id:app,source_ids:ids}});if(!data)return;ids.forEach(id=>selectedVideos.delete(id));if(ids.includes(source?.source_id))source=null;rows=rows.filter(row=>!ids.includes(row.source_id));renderList();notice(`已删除 ${data.removed} 条记录；关联草稿及其证据已保留。`);await list();}
    catch(e){notice('删除未完成：'+e.message);}
    finally{busy=false;if(valid()){q('[data-video-app]').disabled=false;renderList();}}
  }
  async function renameVideo(id){
    if(!valid()||busy||!hasPermission('ui.edit'))return;
    const row=rows.find(item=>item.source_id===id);if(!row)return;
    const name=window.prompt('请输入视频名称（最多 100 个字符）',row.display_name||row.title||row.filename);
    if(name===null)return;const trimmed=name.trim();if(!trimmed||trimmed.length>100||/[\x00-\x1f\x7f]/.test(name)){notice('名称请输入 1 至 100 个字符，不能包含换行或控制字符。');return;}
    busy=true;listSequence++;q('[data-video-app]').disabled=true;renderList();
    try{const data=await request(`/ui-video-sources/${id}/rename`,{method:'POST',body:{app_id:app,name:trimmed}});if(!data)return;rows=rows.map(item=>item.source_id===id?data.source:item);if(source?.source_id===id)source=data.source;notice('视频名称已更新。');renderList();await list();}
    catch(e){notice('重命名未完成：'+e.message);}
    finally{busy=false;if(valid()){q('[data-video-app]').disabled=false;renderList();}}
  }
  q('[data-video-list]').onchange=e=>{const input=e.target.closest('[data-video-select]');if(!input)return;if(input.checked&&selectedVideos.size>=30){input.checked=false;notice('每次最多删除 30 条，请分批操作。');}else input.checked?selectedVideos.add(input.dataset.videoSelect):selectedVideos.delete(input.dataset.videoSelect);renderList();};
  q('[data-video-list]').addEventListener('click',e=>{const rename=e.target.closest('[data-video-rename]');if(rename)return void renameVideo(rename.dataset.videoRename);const button=e.target.closest('[data-video-delete]');if(button&&!button.disabled)removeVideos([button.dataset.videoDelete]);});
  q('[data-video-select-all]').onchange=e=>{let capped=false;for(const input of q('[data-video-list]').querySelectorAll('[data-video-select]:not(:disabled)')){if(!e.target.checked)selectedVideos.delete(input.dataset.videoSelect);else if(selectedVideos.size<30||selectedVideos.has(input.dataset.videoSelect))selectedVideos.add(input.dataset.videoSelect);else capped=true;}if(capped)notice('每次最多删除 30 条，请分批操作。');renderList();};
  q('[data-video-delete-selected]').onclick=()=>removeVideos([...selectedVideos]);
  q('[data-video-new]').onclick=()=>{if(valid()&&!busy)view('upload');};
  q('[data-video-back]').onclick=q('[data-video-upload-back]').onclick=()=>{if(valid()&&!busy)view('list');};
  q('[data-video-query]').oninput=q('[data-video-filter]').onchange=()=>{limit=10;renderList();};
  q('[data-video-more]').onclick=()=>{limit+=10;renderList();};
  view('list');

  function compareSource(left,right){
    const version=(Number(left?.analysis_version)||0)-(Number(right?.analysis_version)||0);
    return version||((Number(left?.updated_at)||0)-(Number(right?.updated_at)||0));
  }
  function adoptSource(next){
    if(source?.source_id===next.source_id&&Number(next.analysis_version)>Number(source.analysis_version))polls=0;
    source=next;render();
  }
  async function list(){if(!app)return;const version=epoch,sequence=++listSequence;try{const data=await request('/ui-video-sources?'+new URLSearchParams({app_id:app,view:'library'}));if(!data||sequence!==listSequence)return;
    rows=data.sources||[];for(const id of selectedVideos){const row=rows.find(item=>item.source_id===id);if(!row||['queued','decoding','analyzing'].includes(row.status))selectedVideos.delete(id);}const selected=rows.find(d=>d.source_id===source?.source_id);
    if(source?.status&&selected&&compareSource(selected,source)>0){
      if(Number(selected.analysis_version)>Number(source.analysis_version)){adoptSource(selected);refresh();}
      else adoptSource({...source,...selected});
    }
    renderList();}catch(e){if(version===epoch&&node.dataset.videoView==='list')notice(e.message);}}
  function statusText(status){return ({queued:'排队中',decoding:'解码中',analyzing:'分析中',ready:'待核对',review_needed:'需补充素材',failed:'分析失败',cancelled:'已取消',interrupted:'重启后已停止'})[status]||status;}
  function historyTime(row){const stamp=Number(row.updated_at)||Number(row.created_at),date=new Date(stamp*1000);return stamp>0&&Number.isFinite(date.getTime())?date.toLocaleString('zh-CN',{hour12:false}):'时间未提供';}
  function syncSourceStatus(){
    if(!valid()||!source?.status)return;
    renderList();
    const messages={queued:'上传完成，正在排队分析；离页后可从我的录屏恢复。',decoding:'正在解码录屏，尚未形成候选步骤。',analyzing:'正在分析录屏，尚未完成；不会操作手机。',ready:'分析完成，候选步骤待核对；请核对证据和业务预期。',review_needed:'需补充素材，请核对说明后重新选择连续片段。',failed:'分析失败，请核对说明后明确重试。',cancelled:'分析已取消；已有草稿与来源证据保留。',interrupted:'服务重启后分析已停止，请明确重试。'};
    if(node.dataset.videoView!=='detail')return;
    notice(source.status==='review_needed'?'未生成用例，请上传包含实际操作的视频。':source.status==='failed'?'未生成用例。'+(source.error||'请重新生成。'):source.error||messages[source.status]||statusText(source.status));
  }
  function syncPollingNotice(){
    if(!valid())return;
    const paused=q('[data-video-poll-paused]');if(paused)paused.hidden=polls<360||!['queued','decoding','analyzing'].includes(source?.status);
  }
  function schedulePoll(){
    clearTimeout(timer);timer=null;syncPollingNotice();
    if(!valid()||document.hidden||polls>=360||!['queued','decoding','analyzing'].includes(source?.status))return;
    const version=epoch,identifier=source.source_id;
    timer=setTimeout(()=>{timer=null;if(!valid()||document.hidden||version!==epoch||source?.source_id!==identifier)return;polls++;syncPollingNotice();refresh();},5000);
  }
  function visibilityChanged(){
    const returned=wasHidden&&!document.hidden;wasHidden=document.hidden;
    if(!valid())return;
    if(document.hidden){clearTimeout(timer);timer=null;return;}
    if(!returned||busy||!['queued','decoding','analyzing'].includes(source?.status))return;
    if(visibilityRead?.epoch===epoch)return;
    clearTimeout(timer);timer=null;
    const read={epoch};visibilityRead=read;
    refresh().finally(()=>{if(visibilityRead===read)visibilityRead=null;});
  }
  async function refresh(){
    if(!source||!valid()||busy)return;
    const sequence=++statusSequence;
    try{const data=await request(`/ui-video-sources/${source.source_id}?`+new URLSearchParams({app_id:app}));if(data&&compareSource(data.source,source)>=0&&(sequence===statusSequence||compareSource(data.source,source)>0))adoptSource(data.source);}catch(e){if(sequence===statusSequence)detailNotice(e.message);}
    finally{if(sequence===statusSequence)schedulePoll();}
  }
  async function openSourceCase(){
    if(!valid()||busy||!(source?.status==='ready'||source?.draft_id))return;
    const id=source.source_id;
    invalidate();busy=true;q('[data-video-app]').disabled=true;
    q('[data-video-detail]').querySelectorAll('[data-video-action]').forEach(button=>button.disabled=true);
    detailNotice('正在打开生成的中文用例，下一步确认步骤并选机试跑…');
    try{
      const data=await request(`/ui-video-sources/${id}/import`,{method:'POST',body:{app_id:app}});if(!data)return;
      if(!data.draft||data.draft.app_id!==app||data.draft.source_snapshot?.source_id&&data.draft.source_snapshot.source_id!==id)throw new Error('用例关联未确认，请重新打开此视频');
      dispose();openDraft(data.draft);
    }catch(e){detailNotice('用例打开未完成：'+e.message+'。请点击“开始核对”重试，已有编辑会保留。');}
    finally{busy=false;if(valid()){q('[data-video-app]').disabled=false;q('[data-video-detail]').querySelectorAll('[data-video-action]').forEach(button=>button.disabled=false);}}
  }
  function render(){
    if(!valid())return;syncSourceStatus();syncJourney();

    const analysis=source.analysis_progress,detail=analysis?.phase==='understanding'?'正在通读完整视频，梳理业务流程':analysis?.phase==='perceiving'?`画面核对已完成 ${Number(analysis.completed)||0} / ${Number(analysis.total)||0} 组`:analysis?.phase==='input_recovery'?'正在核对输入文字':analysis?.phase==='reviewing'?`复核已完成 ${Number(analysis.completed)||0} / ${Number(analysis.total)||0} 段`:analysis?.phase==='reconciling'?'正在复核取消恢复及返回路径':analysis?.phase==='planning'?'视频已识别完，正在整理可执行用例':analysis?.phase==='observing'?`识别已完成 ${Number(analysis.completed)||0} / ${Number(analysis.total)||0} 段`:'';
    const key=JSON.stringify([source.source_id,source.status,source.error,source.error_code,source.analysis_version,source.draft_id,source.display_name,source.title,source.media,source.candidate_steps,source.uncertainties]);
    if(key===renderedKey){const label=q('[data-video-progress] .ui-busy-inline');if(label&&detail)label.textContent=detail;schedulePoll();return;}renderedKey=key;
    clearTimeout(timer);clearMedia();const box=q('[data-video-detail]'),id=source.source_id;
    const active=['queued','decoding','analyzing'].includes(source.status),ready=source.status==='ready'||source.draft_id;
    const headline=ready?'已生成草稿，下一步核对步骤':active?'正在生成测试用例':source.status==='review_needed'||source.status==='failed'?'未生成用例':'尚未生成用例';
    const stage=['queued','decoding','analyzing'].indexOf(source.status);
    const progress=active?`<div class="ui-task-progress" data-video-progress role="status" aria-live="polite" aria-busy="true"><p class="ui-busy-inline" aria-busy="true">${escape(detail||['正在排队，等待分析资源','正在读取视频，提取操作画面','AI 正在识别操作并整理中文步骤'][stage])}</p><small>完成后点击“开始核对”；可先处理其他工作，稍后从“我的视频任务”查看。</small></div>`:'';
    box.innerHTML=`<div class="ui-video-task-main"><h3>${headline}</h3>${progress}<p>${escape(source.display_name||source.title||source.filename)}</p>${source.display_name||source.title?`<small>原文件：${escape(source.filename)}</small>`:""}${source.status==='review_needed'?'<p>请上传包含实际操作的视频，保留操作前后页面和结束结果。</p><button type="button" class="ui-video-primary" data-video-reselect>重新选择视频</button>':ready?'<button type="button" class="ui-video-primary" data-video-action="import">开始核对</button>':['failed','cancelled','interrupted'].includes(source.status)?'<button type="button" class="ui-video-primary" data-video-action="retry">重新生成用例</button>':''}${active?'<p data-video-poll-paused hidden role="status">自动更新已暂停，任务仍可能在运行；请点击“刷新进度”查看最新状态。</p><button type="button" data-video-progress-refresh>刷新进度</button><p>完成后核对中文步骤；生成过程不会操作手机。</p><button type="button" data-video-action="cancel">取消生成</button>':''}</div><div class="ui-video-player"><button type="button" data-video-preview>加载原视频</button><small>首次播放需加载完整视频</small><video controls preload="none" style="max-width:100%;max-height:320px" hidden></video></div><details data-video-technical><summary>查看分析详情（可选）</summary><p>任务状态：${escape(statusText(source.status))}</p><p>${escape(source.error||'观察和推断仅作为来源证据，不代表试跑通过。')}</p>${source.media?`<p>完整时间覆盖 0–${(source.media.duration_ms/1000).toFixed(2)} 秒 · ${source.media.frames.length} 张候选帧 · 第 ${source.analysis_version} 次分析</p>`:''}<button type="button" data-video-status>刷新当前任务</button><ul>${(source.uncertainties||[]).map(v=>`<li>${escape(v)}</li>`).join('')}</ul><ol>${(source.candidate_steps||[]).map(s=>`<li><b>${(s.start_ms/1000).toFixed(2)}–${(s.end_ms/1000).toFixed(2)}秒</b> · ${s.evidence_kind==='observed'?'直接观察候选':'推断操作'} · ${escape(({launch:'启动应用',aiTap:'点击',aiInput:'输入',aiScroll:'滑动',aiWaitFor:'等待',aiAssert:'检查'})[s.action]||'待核对操作')} ${escape(s.target)}<p>观察：${escape(s.observation)}；业务预期仍需核对。</p>${s.frame_refs.map(f=>`<button type="button" data-video-frame="${escape(f)}">查看帧 ${escape(f)}</button>`).join('')}${s.uncertainties.map(v=>`<p>${escape(v)}</p>`).join('')}</li>`).join('')}</ol><img data-video-frame-preview alt="所选录屏证据帧" style="max-width:100%;max-height:400px" hidden></details>`;
    const reselect=q('[data-video-reselect]');if(reselect)reselect.onclick=()=>{if(!valid()||busy)return;invalidate();source=null;view('upload');node.dataset.videoPhase='upload';q('[data-video-detail]').replaceChildren();notice('请选择包含完整操作的视频，再点击“生成测试用例”。');const file=q('[data-video-file]');file.value='';file.focus();file.click();};

    const manualRefresh=()=>{if(!valid()||busy)return;polls=0;syncPollingNotice();refresh();};
    q('[data-video-status]').onclick=manualRefresh;
    const progressRefresh=q('[data-video-progress-refresh]');if(progressRefresh)progressRefresh.onclick=manualRefresh;
    q('[data-video-preview]').onclick=()=>{if(!valid())return;const video=box.querySelector('video');video.hidden=false;media(`/ui-video-sources/${id}/video?`+new URLSearchParams({app_id:app}),video);};
    for(const button of box.querySelectorAll('[data-video-frame]'))button.onclick=()=>{const image=q('[data-video-frame-preview]');image.hidden=false;media(`/ui-video-sources/${id}/frames/${encodeURIComponent(button.dataset.videoFrame)}?`+new URLSearchParams({app_id:app}),image);};
    for(const button of box.querySelectorAll('[data-video-action]'))button.onclick=async()=>{
      if(button.dataset.videoAction==='import')return openSourceCase();
      if(busy||!valid())return;invalidate();busy=true;button.disabled=true;button.setAttribute('aria-busy','true');
      try{const data=await request(`/ui-video-sources/${id}/${button.dataset.videoAction}`,{method:'POST',body:{app_id:app}});if(!data)return;
        if(data.draft){dispose();openDraft(data.draft);return;}source=data.source;polls=0;view('detail');render();list();
      }catch(e){detailNotice(e.message);}finally{busy=false;if(button.isConnected){button.disabled=false;button.removeAttribute('aria-busy');}}
    };
    schedulePoll();
  }
  q('[data-video-list]').onclick=async e=>{const button=e.target.closest('[data-video-open],[data-video-play]');if(!button||busy)return;const play=!!button.dataset.videoPlay;invalidate();source={source_id:button.dataset.videoOpen||button.dataset.videoPlay};view('detail');polls=0;notice('正在读取所选录屏状态…');await refresh();if(play&&valid()&&node.dataset.videoView==='detail')q('[data-video-preview]')?.click();};
  q('[data-video-app]').onchange=()=>{if(busy)return;invalidate();app=q('[data-video-app]').value;source=null;rows=[];selectedVideos.clear();lastListHtml='';limit=10;q('[data-video-query]').value='';q('[data-video-filter]').value='';view('list');node.dataset.videoPhase='upload';notice('');q('[data-video-list]').replaceChildren();q('[data-video-detail]').replaceChildren();q('[data-video-list-summary]').textContent='';if(app)list();else q('[data-video-list]').textContent='请先选择测试应用，查看自己的视频任务。';};
  q('[data-video-refresh]').onclick=list;
  q('[data-video-upload]').onclick=async()=>{
    if(busy||!valid())return;
    if(!app){notice('请先选择测试应用，再生成测试用例。');q('[data-video-app]').focus();return;}
    const file=q('[data-video-file]').files[0];
    if(!file||!file.size||file.size>200*1024*1024||!/[.](mp4|mov)$/i.test(file.name)){notice('请选择不超过200MB的 MP4/MOV 文件');q('[data-video-file]').focus();return;}
    invalidate();busy=true;const version=epoch,button=q('[data-video-upload]');button.disabled=true;button.setAttribute('aria-busy','true');button.textContent='正在上传视频…';q('[data-video-app]').disabled=true;notice('正在上传；上传完成后后台分析，离页可从“我的录屏”重新打开。');
    // Navigation can detach the view while the bounded upload finishes. The
    // response never updates a detached/changed account; own sources are durable.
    const c=new AbortController();const accountGuard=setInterval(()=>{if(identity!==uiSimpleIdentity())c.abort();},500);
    try{const response=await nativeFetch('/api/ui-video-sources/upload?'+new URLSearchParams({app_id:app,filename:file.name,goal:q('[data-video-goal]').value}),{method:'POST',headers:authHeaders({'Content-Type':'application/octet-stream'}),body:file,signal:c.signal});const data=await response.json();if(!valid()||version!==epoch)return;if(!response.ok||!data.ok)throw new Error(data.error||'上传未完成');source=data.source;polls=0;view('detail');render();list();}
    catch(e){if(valid()&&version===epoch)notice('上传结果未确认：'+e.message+'。先刷新我的录屏核对，再决定重新上传。');}
    finally{clearInterval(accountGuard);busy=false;if(valid()){button.disabled=false;button.removeAttribute('aria-busy');button.textContent='生成测试用例';q('[data-video-app]').disabled=false;}}
  };
  const guard=setInterval(()=>{if(!valid())dispose();},1000);
  document.addEventListener('visibilitychange',visibilityChanged);
  request('/ui-executions/catalog').then(data=>{if(!data)return;q('[data-video-app]').innerHTML='<option value="">选择应用</option>'+(data.apps||[]).map(a=>`<option value="${escape(a.app_id)}">${escape(a.name)}</option>`).join('');if(initialSource&&(data.apps||[]).some(a=>a.app_id===initialSource.app_id)){app=initialSource.app_id;q('[data-video-app]').value=app;source={source_id:initialSource.source_id};view('detail');list();refresh();}}).catch(e=>notice(e.message));
  return {dispose};
}
