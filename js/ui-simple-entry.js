// Adapter for the existing authenticated shell; the workflow itself has no legacy job polling.
let uiSimpleEnabled=false;
let uiSimpleEpoch=0;
const uiSimplePictures=new Map();
const uiSimplePictureRequests=new Set();
function isUiSimpleWorkflow(key){return ['ui_start','ui_library','ui_reports'].includes(key);}
function isUiSimpleAvailable(){return uiSimpleEnabled;}
function uiSimpleIdentity(){return String(currentAccessProfile?.user_id||'');}
function leaveUiSimpleWorkflow(){
  window.UiSimpleFlow?.leave();
  document.getElementById('app')?.classList.remove('ui-simple-active');
  for(const controller of uiSimplePictureRequests)controller.abort();
  uiSimplePictureRequests.clear();
  for(const picture of uiSimplePictures.values())URL.revokeObjectURL(picture);
  uiSimplePictures.clear();
}
function resetUiSimpleWorkflow(){
  uiSimpleEpoch++;uiSimpleEnabled=false;
  window.UiSimpleFlow?.reset();leaveUiSimpleWorkflow();
}
async function initUiSimpleAvailability({navigate=true}={}){
  const epoch=++uiSimpleEpoch,identity=uiSimpleIdentity(),initialWorkflow=activeWorkflow;
  if(!identity||!hasPermission('ui.view'))return;
  try{
    const data=await apiRequest('/ui-executions/availability',{timeoutMs:5000});
    if(epoch!==uiSimpleEpoch||identity!==uiSimpleIdentity())return;
    uiSimpleEnabled=data?.ok===true&&data.enabled===true;
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
    ||url.searchParams.get('runner_id')!==device.target.runner_id||url.searchParams.get('device_id')!==device.target.device_id)return;
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
function showUiSimpleWorkflow(){
  if(!uiSimpleEnabled||!isUiSimpleWorkflow(activeWorkflow)||!hasPermission('ui.view'))return false;
  leaveUiSimpleWorkflow();
  document.getElementById('app')?.classList.add('ui-simple-active');
  const area=document.getElementById('editor-area');area.classList.remove('editor-open');area.replaceChildren();
  const page=document.createElement('div');page.className='ui-simple-page';area.append(page);toggleLibrary(false);
  window.UiSimpleFlow.mount(page,{
    mode:{ui_start:'start',ui_library:'library',ui_reports:'reports'}[activeWorkflow],
    request:apiRequest,identity:uiSimpleIdentity,canExecute:()=>hasPermission('ui.execute'),loadPicture:loadUiSimplePicture
  });
  return true;
}
function refreshUiSimpleWorkflow(){return window.UiSimpleFlow?.refresh();}
