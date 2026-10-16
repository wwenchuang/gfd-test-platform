// Adapter for the existing authenticated shell; the workflow itself has no legacy job polling.
let uiSimpleEnabled=false;
let uiSimpleEvidenceAvailable=false;
let uiSimpleEpoch=0;
let uiCaseCreationEpoch=0;
let uiCaseDraftHandle=null;
let uiSimpleStartSelection=null;
const uiSimplePictures=new Map();
const uiSimplePictureRequests=new Set();
const uiSimpleDownloads=new Set();
function isUiSimpleWorkflow(key){return ['ui_start','ui_library','ui_reports'].includes(key);}
function isUiSimpleAvailable(key){return key==='ui_reports'?uiSimpleEvidenceAvailable:uiSimpleEnabled;}
function uiSimpleIdentity(){return String(currentAccessProfile?.user_id||'');}
function leaveUiSimpleWorkflow({discardReportIntent=false}={}){
  uiCaseCreationEpoch++;uiCaseDraftHandle?.dispose();uiCaseDraftHandle=null;
  if(discardReportIntent)clearUiSimpleReportIntent();
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
  uiSimpleEpoch++;uiSimpleEnabled=false;uiSimpleEvidenceAvailable=false;
  window.UiSimpleFlow?.reset();leaveUiSimpleWorkflow();
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
    if(advanced){advanced.classList.toggle('ui-advanced-enabled',uiSimpleEnabled);advanced.open=!uiSimpleEnabled||!isUiSimpleWorkflow(activeWorkflow)&&activeWorkflow!=='dashboard';}
    applyAccessNavigation();
    // Never take the user away from a page they opened while the feature check was in flight.
    if(navigate&&uiSimpleEnabled&&activeWorkflow===initialWorkflow&&!currentFile){
      const saved=sessionStorage.getItem('midscene_active_workflow');
      if(isUiSimpleWorkflow(saved)||activeWorkflow==='dashboard')await activateWorkflow(isUiSimpleWorkflow(saved)?saved:'ui_start');
    }
  }catch(_){/* Feature discovery can fail without breaking the existing workspace. */}
}
async function loadUiSimplePicture(img,device,isCurrent){
  const epoch=uiSimpleEpoch,identity=uiSimpleIdentity();
  const url=new URL(device.snapshot_url,window.location.origin);
  if(url.origin!==window.location.origin||url.pathname!=='/api/runner/device-snapshot'
    ||url.searchParams.get('runner_id')!==device.target.runner_id||url.searchParams.get('device_id')!==device.target.device_id
    ||url.searchParams.get('app_id')!==device.app_id)return;
  const key=identity+'|'+url.href+'|'+device.snapshot_captured_at;
  const attach=src=>{
    if(epoch!==uiSimpleEpoch||identity!==uiSimpleIdentity()||!isCurrent())return false;
    img.src=src;img.hidden=false;const fallback=img.parentElement?.querySelector('[data-picture-fallback]');if(fallback)fallback.hidden=true;
    return true;
  };
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
    request:apiRequest,identity:uiSimpleIdentity,canAdmit:()=>uiSimpleEnabled,canExecute:()=>hasPermission('ui.execute'),canNotify:()=>hasPermission('platform.notify'),reportIntent,loadPicture:loadUiSimplePicture,loadEvidenceImage:loadUiBatchImage,
    canRefreshPictures:()=>!!uiSimpleIdentity()&&hasPermission('ui.view')&&hasPermission('platform.configure')&&(currentAccessProfile.is_superuser===true||currentAccessProfile.scope?.ui_apps==='*'),
    onReportClose:intent=>{if(uiSimpleIdentity()===identity)clearUiSimpleReportIntent(intent);},
    download:downloadUiBatchFile,copy:async text=>{if(!await copyText(text))throw new Error('复制未完成，请重试');}
  });
  if(activeWorkflow==='ui_library')attachUiCaseCreation(page);
  // Keep the exact internal link through loading, errors, refresh and reauthentication.
  // Only an explicit close, selecting another test, or workflow departure abandons it.
  return true;
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
    back.onclick=()=>{if(current())showUiSimpleWorkflow();};
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
  const identity=uiSimpleIdentity(),pending=new Set(),urls=new Set(),mediaRequests=new WeakMap();let alive=true,epoch=0,timer=null,source=null,app='',polls=0,busy=false,renderedKey='',statusSequence=0,autoIntent=null;
  const autoImports=new Set();
  const q=s=>node.querySelector(s),escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const valid=()=>alive&&current()&&identity===uiSimpleIdentity()&&node.isConnected;
  function clearMedia(){for(const url of urls)URL.revokeObjectURL(url);urls.clear();}
  function invalidate(){epoch++;statusSequence++;autoIntent=null;renderedKey='';clearTimeout(timer);timer=null;for(const c of pending)c.abort();pending.clear();clearMedia();}
  function dispose(){alive=false;invalidate();clearInterval(guard);document.removeEventListener('visibilitychange',visibilityChanged);}
  function notice(text){if(valid())q('[data-video-notice]').textContent=text;}
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
    catch(e){if(latest())notice(e.message);}
    finally{pending.delete(c);}
  }
  node.classList.add('ui-video-source');node.dataset.videoPhase='upload';
  node.innerHTML='<h2>上传视频，生成测试用例</h2><ol class="ui-video-journey" data-video-journey><li><b>1</b>上传视频</li><li><b>2</b>生成用例</li><li><b>3</b>选机试跑</li></ol><p>上传包含完整操作的 MP4/MOV，生成后核对中文步骤，再选择手机试跑。最多200MB、10分钟。</p><div class="ui-video-upload-form"><label>测试应用<select data-video-app><option value="">选择应用</option></select></label><label>录屏文件<input data-video-file type="file" accept="video/mp4,video/quicktime,.mp4,.mov"></label><details data-video-goal-options><summary>补充测试目标（可选）</summary><label>业务目标或结束检查<input data-video-goal maxlength="2000" placeholder="已有明确目标时可补充，不必重写步骤"></label></details><button type="button" class="ui-video-primary" data-video-upload>生成测试用例</button></div><p data-video-notice role="status"></p><section data-video-detail></section><details data-video-history><summary>继续我的视频用例</summary><button type="button" data-video-refresh>刷新我的录屏</button><div data-video-list></div></details>';

  function compareSource(left,right){
    const version=(Number(left?.analysis_version)||0)-(Number(right?.analysis_version)||0);
    return version||((Number(left?.updated_at)||0)-(Number(right?.updated_at)||0));
  }
  function adoptSource(next){
    if(source?.source_id===next.source_id&&Number(next.analysis_version)>Number(source.analysis_version))polls=0;
    source=next;render();
  }
  async function list(){if(!app)return;try{const data=await request('/ui-video-sources?'+new URLSearchParams({app_id:app}));if(!data)return;
    const selected=(data.sources||[]).find(d=>d.source_id===source?.source_id);
    if(source?.status&&selected&&compareSource(selected,source)>0)adoptSource(selected);
    q('[data-video-list]').innerHTML=(data.sources||[]).map(d=>{const row=source?.status&&source.source_id===d.source_id&&compareSource(source,d)>=0?source:d;return `<button type="button" data-video-open="${escape(row.source_id)}">${escape(historyLabel(row))}</button>`;}).join('')||'<p>当前应用还没有上传录屏。</p>';}catch(e){notice(e.message);}}
  function statusText(status){return ({queued:'排队中',decoding:'解码中',analyzing:'分析中',ready:'待核对',review_needed:'需补充素材',failed:'分析失败',cancelled:'已取消',interrupted:'重启后已停止'})[status]||status;}
  function historyLabel(row){
    const timestamp=Number(row.created_at),date=new Date(timestamp*1000);
    const time=timestamp>0&&Number.isFinite(date.getTime())?date.toLocaleString('zh-CN',{hour12:false}):'';
    return [row.title||row.filename,row.draft_id?'已保存用例':statusText(row.status),time?`创建 ${time}`:'',row.analysis_version?`第 ${row.analysis_version} 次分析`:''].filter(Boolean).join(' · ');
  }
  function syncSourceStatus(){
    if(!valid()||!source?.status)return;
    for(const button of q('[data-video-list]').querySelectorAll('[data-video-open]'))if(button.dataset.videoOpen===source.source_id)button.textContent=historyLabel(source);
    const messages={queued:'上传完成，正在排队分析；离页后可从我的录屏恢复。',decoding:'正在解码录屏，尚未形成候选步骤。',analyzing:'正在分析录屏，尚未完成；不会操作手机。',ready:'分析完成，候选步骤待核对；请核对证据和业务预期。',review_needed:'需补充素材，请核对说明后重新选择连续片段。',failed:'分析失败，请核对说明后明确重试。',cancelled:'分析已取消；已有草稿与来源证据保留。',interrupted:'服务重启后分析已停止，请明确重试。'};
    notice(source.status==='review_needed'?'未生成用例，请上传包含实际操作的视频。':source.status==='failed'?'未生成用例。'+(source.error||'请重新生成。'):source.error||messages[source.status]||statusText(source.status));
  }
  function schedulePoll(){
    clearTimeout(timer);timer=null;
    if(!valid()||document.hidden||polls>=120||!['queued','decoding','analyzing'].includes(source?.status))return;
    const version=epoch,identifier=source.source_id;
    timer=setTimeout(()=>{timer=null;if(!valid()||document.hidden||version!==epoch||source?.source_id!==identifier)return;polls++;refresh();},5000);
  }
  function visibilityChanged(){if(!valid())return;schedulePoll();}
  async function refresh(){
    if(!source||!valid()||busy)return;
    const sequence=++statusSequence;
    try{const data=await request(`/ui-video-sources/${source.source_id}?`+new URLSearchParams({app_id:app}));if(data&&compareSource(data.source,source)>=0&&(sequence===statusSequence||compareSource(data.source,source)>0))adoptSource(data.source);}catch(e){if(sequence===statusSequence)notice(e.message);}
    finally{if(sequence===statusSequence)schedulePoll();}
  }
  function sourceKey(item){return item?.source_id+':'+item?.analysis_version;}
  function scheduleAutoImport(){
    const version=epoch,key=sourceKey(source);
    if(!autoIntent||autoIntent!==key||source?.status!=='ready'||autoImports.has(key))return;
    Promise.resolve().then(()=>{if(valid()&&version===epoch&&!busy&&autoIntent===key&&sourceKey(source)===key)openSourceCase(true);});
  }
  async function openSourceCase(automatic=false){
    if(!valid()||busy||!(source?.status==='ready'||source?.draft_id))return;
    const id=source.source_id,key=sourceKey(source);
    if(automatic){if(autoIntent!==key||autoImports.has(key))return;autoImports.add(key);}
    invalidate();busy=true;q('[data-video-app]').disabled=true;
    q('[data-video-detail]').querySelectorAll('[data-video-action]').forEach(button=>button.disabled=true);
    notice('正在打开生成的中文用例，下一步确认步骤并选机试跑…');
    try{
      const data=await request(`/ui-video-sources/${id}/import`,{method:'POST',body:{app_id:app}});if(!data)return;
      if(!data.draft||data.draft.app_id!==app||data.draft.source_snapshot?.source_id&&data.draft.source_snapshot.source_id!==id)throw new Error('用例关联未确认，请重新打开此视频');
      dispose();openDraft(data.draft);
    }catch(e){notice('用例打开未完成：'+e.message+'。请点击“查看用例并试跑”重试，已有编辑会保留。');}
    finally{busy=false;if(valid()){q('[data-video-app]').disabled=false;q('[data-video-detail]').querySelectorAll('[data-video-action]').forEach(button=>button.disabled=false);}}
  }
  function render(){
    if(!valid())return;syncSourceStatus();node.dataset.videoPhase=source.status==='ready'?'trial':'generate';

    const key=JSON.stringify([source.source_id,source.status,source.error,source.error_code,source.analysis_version,source.draft_id,source.title,source.media,source.candidate_steps,source.uncertainties]);
    if(key===renderedKey){schedulePoll();scheduleAutoImport();return;}renderedKey=key;
    clearTimeout(timer);clearMedia();const box=q('[data-video-detail]'),id=source.source_id;
    const active=['queued','decoding','analyzing'].includes(source.status),ready=source.status==='ready'||source.draft_id;
    const headline=ready?'用例已生成，下一步核对并试跑':active?'正在生成测试用例':source.status==='review_needed'||source.status==='failed'?'未生成用例':'尚未生成用例';
    const stage=['queued','decoding','analyzing'].indexOf(source.status);
    const progress=active?`<div class="ui-task-progress" data-video-progress role="status" aria-live="polite" aria-busy="true"><p class="ui-busy-inline" aria-busy="true">${['正在排队，等待分析资源','正在读取视频，提取操作画面','AI 正在识别操作并整理中文步骤'][stage]}</p><ol>${['排队','读取视频','AI 识别步骤','待核对'].map((label,i)=>`<li class="${i<stage?'is-done':i===stage?'is-current':''}" ${i===stage?'aria-current="step"':''}><span aria-hidden="true">${i<stage?'✓':i+1}</span>${label}</li>`).join('')}</ol><small>${autoIntent===sourceKey(source)?'完成后自动打开中文步骤':'完成后点击“查看用例并试跑”'}；可先处理其他工作，稍后从“继续我的视频用例”查看。</small></div>`:'';
    box.innerHTML=`<div class="ui-video-task-main"><h3>${headline}</h3>${progress}<p>${escape(source.title||source.filename)}</p>${source.title?`<small>原文件：${escape(source.filename)}</small>`:""}${source.status==='review_needed'?'<p>请上传包含实际操作的视频，保留操作前后页面和结束结果。</p><button type="button" class="ui-video-primary" data-video-reselect>重新选择视频</button>':ready?'<button type="button" class="ui-video-primary" data-video-action="import">查看用例并试跑</button>':['failed','cancelled','interrupted'].includes(source.status)?'<button type="button" class="ui-video-primary" data-video-action="retry">重新生成用例</button>':''}${active?'<p>完成后核对中文步骤；生成过程不会操作手机。</p><button type="button" data-video-action="cancel">取消生成</button>':''}</div><details data-video-technical><summary>查看原视频与分析详情（可选）</summary><p>任务状态：${escape(statusText(source.status))}</p><p>${escape(source.error||'观察和推断仅作为来源证据，不代表试跑通过。')}</p>${source.media?`<p>完整时间覆盖 0–${(source.media.duration_ms/1000).toFixed(2)} 秒 · ${source.media.frames.length} 张候选帧 · 识别版本 ${source.analysis_version}</p>`:''}<button type="button" data-video-status>刷新当前任务</button><button type="button" data-video-preview>预览私有原视频（需完整下载）</button><video controls preload="none" style="max-width:100%;max-height:320px" hidden></video><ul>${(source.uncertainties||[]).map(v=>`<li>${escape(v)}</li>`).join('')}</ul><ol>${(source.candidate_steps||[]).map(s=>`<li><b>${(s.start_ms/1000).toFixed(2)}–${(s.end_ms/1000).toFixed(2)}秒</b> · ${s.evidence_kind==='observed'?'直接观察候选':'推断操作'} · ${escape(({launch:'启动应用',aiTap:'点击',aiInput:'输入',aiScroll:'滑动',aiWaitFor:'等待',aiAssert:'检查'})[s.action]||'待核对操作')} ${escape(s.target)}<p>观察：${escape(s.observation)}；业务预期仍需核对。</p>${s.frame_refs.map(f=>`<button type="button" data-video-frame="${escape(f)}">查看帧 ${escape(f)}</button>`).join('')}${s.uncertainties.map(v=>`<p>${escape(v)}</p>`).join('')}</li>`).join('')}</ol><img data-video-frame-preview alt="所选录屏证据帧" style="max-width:100%;max-height:400px" hidden></details>`;
    const reselect=q('[data-video-reselect]');if(reselect)reselect.onclick=()=>{if(!valid()||busy)return;invalidate();source=null;node.dataset.videoPhase='upload';q('[data-video-detail]').replaceChildren();notice('请选择包含完整操作的视频，再点击“生成测试用例”。');const file=q('[data-video-file]');file.value='';file.focus();file.click();};

    q('[data-video-status]').onclick=()=>{polls=0;refresh();};
    q('[data-video-preview]').onclick=()=>{if(!valid())return;const video=box.querySelector('video');video.hidden=false;media(`/ui-video-sources/${id}/video?`+new URLSearchParams({app_id:app}),video);};
    for(const button of box.querySelectorAll('[data-video-frame]'))button.onclick=()=>{const image=q('[data-video-frame-preview]');image.hidden=false;media(`/ui-video-sources/${id}/frames/${encodeURIComponent(button.dataset.videoFrame)}?`+new URLSearchParams({app_id:app}),image);};
    for(const button of box.querySelectorAll('[data-video-action]'))button.onclick=async()=>{
      if(button.dataset.videoAction==='import')return openSourceCase();
      if(busy||!valid())return;invalidate();busy=true;button.disabled=true;button.setAttribute('aria-busy','true');
      try{const data=await request(`/ui-video-sources/${id}/${button.dataset.videoAction}`,{method:'POST',body:{app_id:app}});if(!data)return;
        if(data.draft){dispose();openDraft(data.draft);return;}source=data.source;polls=0;if(button.dataset.videoAction==='retry')autoIntent=sourceKey(source);render();list();
      }catch(e){notice(e.message);}finally{busy=false;if(button.isConnected){button.disabled=false;button.removeAttribute('aria-busy');}scheduleAutoImport();}
    };
    schedulePoll();scheduleAutoImport();
  }
  q('[data-video-list]').onclick=async e=>{const button=e.target.closest('[data-video-open]');if(!button||busy)return;invalidate();source={source_id:button.dataset.videoOpen};polls=0;notice('正在读取所选录屏状态…');await refresh();};
  q('[data-video-app]').onchange=()=>{if(busy)return;invalidate();app=q('[data-video-app]').value;source=null;node.dataset.videoPhase='upload';notice('');q('[data-video-list]').replaceChildren();q('[data-video-detail]').replaceChildren();list();};
  q('[data-video-refresh]').onclick=list;
  q('[data-video-upload]').onclick=async()=>{
    if(busy||!valid()||!app)return;const file=q('[data-video-file]').files[0];
    if(!file||!file.size||file.size>200*1024*1024||!/[.](mp4|mov)$/i.test(file.name)){notice('请选择不超过200MB的 MP4/MOV 文件');return;}
    invalidate();busy=true;const version=epoch,button=q('[data-video-upload]');button.disabled=true;button.setAttribute('aria-busy','true');button.textContent='正在上传视频…';q('[data-video-app]').disabled=true;notice('正在上传；上传完成后后台分析，离页可从“我的录屏”重新打开。');
    // Navigation can detach the view while the bounded upload finishes. The
    // response never updates a detached/changed account; own sources are durable.
    const c=new AbortController();const accountGuard=setInterval(()=>{if(identity!==uiSimpleIdentity())c.abort();},500);
    try{const response=await nativeFetch('/api/ui-video-sources/upload?'+new URLSearchParams({app_id:app,filename:file.name,goal:q('[data-video-goal]').value}),{method:'POST',headers:authHeaders({'Content-Type':'application/octet-stream'}),body:file,signal:c.signal});const data=await response.json();if(!valid()||version!==epoch)return;if(!response.ok||!data.ok)throw new Error(data.error||'上传未完成');source=data.source;polls=0;autoIntent=sourceKey(source);render();list();}
    catch(e){if(valid()&&version===epoch)notice('上传结果未确认：'+e.message+'。先刷新我的录屏核对，再决定重新上传。');}
    finally{clearInterval(accountGuard);busy=false;if(valid()){button.disabled=false;button.removeAttribute('aria-busy');button.textContent='生成测试用例';q('[data-video-app]').disabled=false;scheduleAutoImport();}}
  };
  const guard=setInterval(()=>{if(!valid())dispose();},1000);
  document.addEventListener('visibilitychange',visibilityChanged);
  request('/ui-executions/catalog').then(data=>{if(!data)return;q('[data-video-app]').innerHTML='<option value="">选择应用</option>'+(data.apps||[]).map(a=>`<option value="${escape(a.app_id)}">${escape(a.name)}</option>`).join('');if(initialSource&&(data.apps||[]).some(a=>a.app_id===initialSource.app_id)){app=initialSource.app_id;q('[data-video-app]').value=app;source={source_id:initialSource.source_id};list();refresh();}}).catch(e=>notice(e.message));
  return {dispose};
}
