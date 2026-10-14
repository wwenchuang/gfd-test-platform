// Simple UI workflow state is scoped to the signed-in identity and mounted page.
(() => {
  const modes={start:'开始测试',library:'用例库',reports:'我的报告'};
  const labels={ready:'连接就绪 · 开始前检查占用',offline:'手机或执行器已离线',upgrade_required:'执行器需要更新',mapping_missing:'尚未配置 iOS 应用',not_ready:'未就绪，请检查手机连接与解锁'};
  const e=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const keyOf=target=>JSON.stringify([target.runner_id,target.device_id,target.platform]);
  function requestId(){
    const random=globalThis.crypto;
    try{
      if(typeof random?.randomUUID==='function')return random.randomUUID();
      // randomUUID requires a secure context; getRandomValues also works on the HTTP deployment.
      if(typeof random?.getRandomValues==='function'){
        const bytes=new Uint8Array(16);random.getRandomValues(bytes);
        bytes[6]=(bytes[6]&0x0f)|0x40;bytes[8]=(bytes[8]&0x3f)|0x80;
        const hex=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
        return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
      }
    }catch(_){/* Never replace unavailable secure randomness with a predictable request key. */}
    throw new Error('当前浏览器无法生成安全的请求标识，请更换浏览器后重试。');
  }
  let state=null;
  function valid(s){return state===s && s.node.isConnected && s.identity===s.options.identity();}
  function store(s,key,value){try{sessionStorage.setItem(`ui-simple:${s.identity}:${key}`,JSON.stringify(value));}catch(_){/* Storage can be disabled; current-page behavior still works. */}}
  function read(s,key){try{return JSON.parse(sessionStorage.getItem(`ui-simple:${s.identity}:${key}`)||'null');}catch(_){return null;}}
  function notice(s,message,error=false){if(!valid(s))return;const n=s.node.querySelector('[data-ui-notice]');n.textContent=message;n.setAttribute('role',error?'alert':'status');n.classList.toggle('ui-simple-error',error);}
  function leave(){if(!state)return;state.reportView?.dispose();clearInterval(state.timer);clearTimeout(state.searchTimer);clearTimeout(state.pictureTimer);for(const c of state.pending.values())c.abort();state.pending.clear();state=null;}
  function reset(){const node=state?.node;leave();node?.replaceChildren();}
  async function request(s,slot,path,options={}){
    s.pending.get(slot)?.abort();const controller=new AbortController();s.pending.set(slot,controller);
    const timeout=setTimeout(()=>controller.abort(),30000);
    try{
      const data=await s.options.request(path,{...options,signal:controller.signal});
      if(!valid(s)||s.pending.get(slot)!==controller)return null;
      if(data?.ok!==true)throw Object.assign(new Error(data?.error||'请求未完成，请重试'),{code:data?.code,definitive:typeof data?.code==='string'&&data.code!=='batch_store_unavailable'});
      return data;
    }catch(error){if(!valid(s)||s.pending.get(slot)!==controller)return null;
      const status=Number(error?.status);
      if(status===408)error.definitive=false;
      else if([400,401,403,404,409,413,422,429].includes(status)
        ||(status===200&&typeof error?.code==='string'&&error.code!=='batch_store_unavailable'))error.definitive=true;
      throw error;}
    finally{clearTimeout(timeout);if(s.pending.get(slot)===controller)s.pending.delete(slot);}
  }
  function options(select,values,placeholder){
    const current=select.value;select.replaceChildren(new Option(placeholder,''));
    for(const [value,label] of values)select.add(new Option(label,value));
    select.value=values.some(([value])=>value===current)?current:'';
  }
  function filters(s){return {app_id:s.app,module:s.module,query:s.query};}
  function buttonState(s){
    if(!valid(s))return;
    pictureButtonState(s);
    s.node.querySelector('[data-ui-form]').disabled=s.busy||s.uncertain;
    const button=s.node.querySelector('[data-ui-start]');if(!button)return;
    const device=s.devices.find(d=>keyOf(d.target)===s.target);
    button.disabled=s.busy||s.uncertain||!s.options.canAdmit()||!s.options.canExecute()||!s.app||!s.selected.size||!device||device.connection_status!=='ready';
    button.textContent=s.busy?'正在检查并提交…':'开始测试';
    s.node.querySelector('[data-ui-selection-count]').textContent=`已选 ${s.selected.size} 条用例${device?' · '+device.name:''}`;
    const selected=s.node.querySelector('[data-ui-selected]'),signature=JSON.stringify([...s.selected]);
    if(selected.dataset.signature!==signature){
      selected.dataset.signature=signature;selected.replaceChildren();
      for(const[id,item]of s.selected){
        const chip=document.createElement('button');chip.type='button';chip.className='btn-sm';chip.textContent=(item.task_name||id)+' ×';chip.setAttribute('aria-label','取消选择 '+(item.task_name||id));
        chip.onclick=()=>{if(s.busy||s.uncertain)return;s.selected.delete(id);s.node.querySelectorAll('[data-case-id]').forEach(input=>{if(input.dataset.caseId===id)input.checked=false;});invalidateStart(s);};selected.append(chip);
      }
    }
  }
  function remember(s){store(s,'selection',{app:s.app,module:s.module,target:s.target});}
  function invalidateStart(s){if(!s.uncertain)s.startIntent=null;buttonState(s);}
  function renderCases(s,data){
    const host=s.node.querySelector('[data-ui-cases]');host.replaceChildren();
    const groups=s.app?[{name:'',items:data.items||[]}]:data.groups||[];
    for(const group of groups){
      const section=document.createElement('section');section.className='ui-case-group';
      if(group.name){const h=document.createElement('h3');h.textContent=group.name;section.append(h);}
      for(const item of group.items||[]){
        const label=document.createElement('label');label.className='ui-case-row';
        label.innerHTML=`<input type="checkbox" data-case-id="${e(item.case_id)}"><span><strong>${e(item.task_name||item.case_id)}</strong><small>${e(item.module||'未分组')} · ${item.execution_eligible?'待选机检查兼容性':'尚未发布为可执行基线'}</small></span>`;
        const input=label.querySelector('input');input.disabled=!item.execution_eligible||!s.options.canExecute();input.checked=s.selected.has(item.case_id);
        input.onchange=()=>{
          if(s.busy||s.uncertain)return;
          if(item.app_id!==s.app){s.app=item.app_id;s.module='';s.cursor='';s.previous=[];s.selected.clear();s.node.querySelector('[name="ui-app"]').value=s.app;loadCatalog(s);loadDevices(s);}
          if(input.checked)s.selected.set(item.case_id,{case_id:item.case_id,revision:item.revision,task_name:item.task_name});else s.selected.delete(item.case_id);
          invalidateStart(s);remember(s);
        };
        section.append(label);
      }
      if(group.has_more){const more=document.createElement('button');more.type='button';more.className='btn-sm';more.textContent=`查看全部 ${group.total} 条`;more.onclick=()=>changeApp(s,group.app_id);section.append(more);}
      host.append(section);
    }
    if(!host.childElementCount)host.textContent='没有匹配用例，试试其他应用或关键词。';
    s.node.querySelector('[data-ui-case-count]').textContent=`${data.total||0} 条匹配用例${s.app?'':' · 每个应用先显示 3 条'}`;
    s.next=data.next_cursor||'';
    s.node.querySelector('[data-ui-prev]').disabled=!s.previous.length;
    s.node.querySelector('[data-ui-next]').disabled=!s.next;
    buttonState(s);
  }
  async function loadCatalog(s){
    const query=new URLSearchParams({...filters(s),limit:'20'});if(s.cursor)query.set('cursor',s.cursor);
    try{
      const data=await request(s,'catalog','/ui-executions/catalog?'+query);if(!data)return;
      const app=s.node.querySelector('[name="ui-app"]'),module=s.node.querySelector('[name="ui-module"]');
      options(app,(data.apps||[]).map(a=>[a.app_id,a.name]),'全部应用');app.value=s.app;
      options(module,(data.modules||[]).map(v=>[v,v]),'全部模块');module.value=s.module;
      renderNotificationPolicy(s,data.apps||[]);
      renderCases(s,data);
    }catch(error){
      if(s.app&&['app_forbidden','app_unavailable'].includes(error.code)&&!s.uncertain){changeApp(s,'');notice(s,'之前选择的应用已不可用，请重新选择。',true);}
      else notice(s,error.message,true);
    }
  }
  function changeApp(s,app){
    if(s.busy||s.uncertain)return;
    s.app=app;s.module='';s.cursor='';s.previous=[];s.selected.clear();s.devices=[];s.target='';s.notifyEnabled=false;s.notificationPolicyKey='';
    renderNotificationPolicy(s,[]);
    s.node.querySelector('[name="ui-app"]').value=app;
    s.pending.get('devices')?.abort();s.pending.delete('devices');s.deviceNodes.clear();
    s.node.querySelector('[data-ui-devices]').replaceChildren();invalidateStart(s);remember(s);loadCatalog(s);loadDevices(s);
  }
  function renderDevices(s){
    const host=s.node.querySelector('[data-ui-devices]'),keep=new Set();
    for(const d of s.devices){
      const key=keyOf(d.target);keep.add(key);let card=s.deviceNodes.get(key);
      if(!card){
        card=document.createElement('label');card.className='ui-simple-device';
        card.innerHTML='<input type="radio" name="ui-device"><span class="ui-simple-preview"><img hidden><small data-picture-fallback>暂无截图</small></span><span class="ui-simple-device-info"><strong data-phone-name></strong><span data-phone-version></span><small data-phone-state></small><small data-phone-captured></small></span>';
        const input=card.querySelector('input');input.dataset.deviceKey=key;input.onchange=()=>{if(s.busy||s.uncertain)return;s.target=key;invalidateStart(s);remember(s);};
        s.deviceNodes.set(key,card);host.append(card);
      }
      const input=card.querySelector('input');input.checked=s.target===key;input.disabled=d.connection_status!=='ready';
      card.querySelector('[data-phone-name]').textContent=d.name;
      card.querySelector('[data-phone-version]').textContent=`${d.target.platform==='ios'?'iOS':'Android'} ${d.os_version||''} · 应用 ${d.app_version||'版本未上报'}`;
      card.querySelector('[data-phone-state]').textContent=labels[d.connection_status]||'设备状态待确认';
      card.querySelector('[data-phone-captured]').textContent=d.snapshot_captured_at?`截图：${d.snapshot_captured_at}`:'尚未采集截图';
      card.classList.toggle('ui-device-unavailable',d.connection_status!=='ready');
      const img=card.querySelector('img');img.alt=`${d.name} 最近一次手机画面`;
      card.querySelector('[data-picture-fallback]').textContent=d.snapshot_hint||'暂无截图';
      if(!d.snapshot_url||img.dataset.pictureStamp!==(d.snapshot_url+'|'+d.snapshot_captured_at)){
        img.hidden=true;img.removeAttribute('src');card.querySelector('[data-picture-fallback]').hidden=false;
        if(!d.snapshot_url)delete img.dataset.pictureStamp;
      }
      if(d.snapshot_url&&s.options.loadPicture){
        const stamp=d.snapshot_url+'|'+d.snapshot_captured_at;
        if(img.dataset.pictureStamp!==stamp){
          img.dataset.pictureStamp=stamp;
          Promise.resolve(s.options.loadPicture(img,d,()=>valid(s)&&img.isConnected&&img.dataset.pictureStamp===stamp))
            .then(loaded=>{if(!loaded&&valid(s)&&img.dataset.pictureStamp===stamp)delete img.dataset.pictureStamp;})
            .catch(()=>{if(valid(s)&&img.dataset.pictureStamp===stamp)delete img.dataset.pictureStamp;});
        }
      }
    }
    for(const [key,card]of s.deviceNodes){if(!keep.has(key)){
      // Keep an explicit selection visible if the registry no longer contains it.
      if(s.target===key){card.querySelector('input').disabled=true;card.querySelector('[data-phone-state]').textContent='所选手机已断开，请连接后刷新或明确改选';}
      else{card.remove();s.deviceNodes.delete(key);}
    }}
    const hint=s.node.querySelector('[data-ui-device-hint]');hint.textContent=s.app?'每 5 秒同步连接状态；执行前核对占用与用例兼容性。':'先选择应用，再选择手机。';
    buttonState(s);
  }
  async function loadDevices(s){
    if(!s.app||s.pending.has('devices'))return;
    const app=s.app;
    try{const data=await request(s,'devices','/ui-executions/devices?'+new URLSearchParams({app_id:app}));if(!data||s.app!==app)return;s.devices=data.devices||[];renderDevices(s);}
    catch(error){notice(s,'手机状态刷新失败：'+error.message,true);s.devices=s.devices.map(d=>({...d,connection_status:'unknown',snapshot_url:''}));renderDevices(s);}
  }
  function canRefreshPictures(s){return valid(s)&&s.options.mode!=='reports'&&s.options.canAdmit()&&s.options.canRefreshPictures()===true;}
  function pictureButtonState(s){
    if(!valid(s))return;const button=s.node.querySelector('[data-ui-pictures-refresh]');if(!button)return;
    const allowed=canRefreshPictures(s),pending=s.pending.has('pictures');button.hidden=!allowed;
    button.disabled=!allowed||!s.app||s.busy||s.uncertain||pending||Date.now()<s.pictureRefreshAfter;
    button.textContent=pending?'正在请求截图…':'刷新手机截图';
    if(!allowed){const hint=s.node.querySelector('[data-ui-picture-notice]');hint.hidden=true;hint.textContent='';}
  }
  function pictureNotice(s,message,error=false){
    if(!canRefreshPictures(s))return;const hint=s.node.querySelector('[data-ui-picture-notice]');
    hint.hidden=false;hint.textContent=message;hint.setAttribute('role',error?'alert':'status');hint.classList.toggle('ui-simple-error',error);
  }
  async function refreshPictures(s){
    if(!valid(s)||s.options.mode==='reports')return;pictureButtonState(s);
    if(s.node.querySelector('[data-ui-pictures-refresh]').disabled)return;
    s.pictureRefreshAfter=Date.now()+10000;pictureNotice(s,'正在请求在线空闲手机采集截图…');
    try{
      const pending=request(s,'pictures','/runners/refresh',{method:'POST',body:{}});pictureButtonState(s);
      const data=await pending;if(!data||!canRefreshPictures(s))return;
      const count=Number(data.requested);
      pictureNotice(s,count===0?'本次没有截图采集请求排队；请检查手机连接，或等待正在执行的任务结束后重试。':
        `${Number.isInteger(count)&&count>0?count+' 台手机的截图采集已排队':'截图采集请求已受理'}，等待手机回传；忙碌手机会跳过，已有截图仍保留。`);
      await loadDevices(s);
    }catch(error){pictureNotice(s,'截图刷新请求失败：'+error.message+'；请稍后重试。',true);}
    finally{if(valid(s)){pictureButtonState(s);clearTimeout(s.pictureTimer);s.pictureTimer=setTimeout(()=>pictureButtonState(s),Math.max(0,s.pictureRefreshAfter-Date.now()));}}
  }
  function renderNotificationPolicy(s,apps){
    const toggle=s.node.querySelector('[data-ui-notify]');if(!toggle)return;
    const channel=apps.find(a=>a.app_id===s.app)?.notification_channel;
    const ready=channel?.ready===true,canNotify=s.options.canNotify()===true,key=s.app+'|'+ready+'|'+canNotify+'|'+(channel?.channel_id||'');
    // Device polling never resets an explicit opt-out. An app/channel change creates a fresh default.
    if(s.notificationPolicyKey!==key){s.notifyEnabled=ready&&canNotify;s.notificationPolicyKey=key;}
    if(!canNotify)s.notifyEnabled=false;
    toggle.checked=ready&&canNotify&&s.notifyEnabled;toggle.disabled=!ready||!canNotify;
    s.node.querySelector('[data-ui-notify-target]').textContent=s.app?('飞书通知：'+(channel?.target_label||'未配置')+(!canNotify?' · 当前账号没有发送通知权限，可正常执行不通知':ready?' · 本次报告完成后自动发送':' · 可在应用配置中设置')):'先选择应用以查看通知目标';
  }
  function payload(s){return {app_id:s.app,cases:[...s.selected.values()].map(({case_id,revision})=>({case_id,revision})),targets:[s.devices.find(d=>keyOf(d.target)===s.target).target],notifications:{enabled:s.options.canNotify()===true&&s.notifyEnabled===true}};}
  async function start(s,retry=false){
    if(!valid(s)||s.busy||!s.options.canAdmit()||!s.options.canExecute())return;
    if(!retry&&s.node.querySelector('[data-ui-start]').disabled)return;
    if(s.uncertain&&!retry){notice(s,'上次提交结果待确认，请先核对上次提交。',true);return;}
    s.busy=true;buttonState(s);notice(s,retry?'正在核对上次提交…':'正在检查所选用例和手机…');
    let submitted=false;
    try{
      const body=retry?s.startIntent?.body:payload(s);if(!body)return;
      if(!s.startIntent||JSON.stringify(s.startIntent.body)!==JSON.stringify(body))s.startIntent={key:requestId(),body};
      if(!retry){const preflight=await request(s,'preflight','/ui-executions/preflight',{method:'POST',body});if(!preflight)return;
        if(!preflight.ready && !(preflight.blockers||[]).length)throw new Error('尚未通过执行检查，请刷新后重试');
        const blocking=(preflight.blockers||[]).filter(b=>b.code!=='device_busy');
        if(blocking.length)throw new Error(blocking.map(b=>b.message||b.code||'执行条件未满足').join('；'));
      }
      store(s,'pending',s.startIntent);submitted=true;
      const data=await request(s,'start','/ui-executions',{method:'POST',headers:{'Idempotency-Key':s.startIntent.key},body});if(!data)return;
      s.uncertain=false;store(s,'pending',null);s.startIntent=null;s.batchId=data.batch.ui_batch_id;store(s,'lastBatch',s.batchId);
      notice(s,'已创建本次测试。');showBatch(s,data.batch);
    }catch(error){if(valid(s)){s.uncertain=submitted&&!error.definitive;if(!s.uncertain){store(s,'pending',null);s.startIntent=null;}if(s.uncertain)notice(s,'提交结果待确认，可安全重试核对：'+error.message,true);else notice(s,error.message,true);}}
    finally{if(valid(s)){s.busy=false;buttonState(s);s.node.querySelector('[data-ui-recover]').hidden=!s.uncertain;}}
  }
  function showBatch(s,batch){
    const host=s.node.querySelector('[data-ui-batch]');host.hidden=false;
    const actions={isCurrent:()=>valid(s)&&s.options.canExecute(),recover:async(id,body)=>{
      if(!valid(s)||!s.options.canExecute()||s.batchId!==id)return;
      try{const data=await request(s,'action',`/ui-executions/${encodeURIComponent(id)}/confirm-stop`,{method:'POST',body});if(data&&s.batchId===id)showBatch(s,data.batch);}
      catch(error){if(valid(s)&&s.batchId===id){await loadBatch(s);throw error;}}
    },cancel:async id=>{const data=await request(s,'action',`/ui-executions/${encodeURIComponent(id)}/cancel`,{method:'POST',body:{}});if(data)showBatch(s,data.batch);},
      rerun:async id=>{if(!valid(s)||!s.options.canAdmit()||!s.options.canExecute())return;s.rerunKeys[id] ||= requestId();const data=await request(s,'action',`/ui-executions/${encodeURIComponent(id)}/rerun`,{method:'POST',headers:{'Idempotency-Key':s.rerunKeys[id]},body:{scope:'failed'}});if(data){delete s.rerunKeys[id];s.batchId=data.batch.ui_batch_id;store(s,'lastBatch',s.batchId);showBatch(s,data.batch);}}};
    if(!s.options.canAdmit())delete actions.rerun;
    if(!s.options.canExecute()){delete actions.cancel;delete actions.rerun;delete actions.recover;}
    if(s.options.download&&window.UiBatchReport)actions.report=id=>openReport(s,id);
    if(s.options.copy)actions.copy=async id=>{const data=await request(s,'summary',`/ui-executions/${encodeURIComponent(id)}/summary`);if(data&&data.status==='ready')await s.options.copy(data.summary);else if(data)throw new Error('报告尚未就绪');};
    window.UiBatchView.update(host,batch,actions);
    s.batchSettled=batch.execution_settled===true&&['ready','failed'].includes(batch.report_status)&&!['pending','queued','retry_wait','sending'].includes(batch.delivery_status);
    if(s.reportId===batch.ui_batch_id)s.reportView?.refresh(batch.report_status==='pending');
  }
  function openReport(s,id,revision){
    if(!valid(s))return;s.reportView?.dispose();s.reportId=id;
    let host=s.node.querySelector('[data-ui-report-preview]');if(!host){host=document.createElement('section');host.dataset.uiReportPreview='';s.node.append(host);}
    host.hidden=false;s.reportView=window.UiBatchReport.mount(host,{batchId:id,revision,identity:s.options.identity,canNotify:s.options.canNotify,request:s.options.request,download:s.options.download,copy:s.options.copy,onClose:()=>{if(valid(s)&&revision)s.options.onReportClose?.({batchId:id,revision});}});
    host.scrollIntoView?.({block:'start',behavior:'smooth'});
  }
  async function loadBatch(s){
    if(!s.batchId||s.pending.has('batch'))return;
    const id=s.batchId;try{const data=await request(s,'batch','/ui-executions/'+encodeURIComponent(id));if(data&&id===s.batchId)showBatch(s,data.batch);}
    catch(error){notice(s,'本次执行读取失败：'+error.message,true);}
  }
  async function loadReports(s){
    if(s.pending.has('reports'))return;
    const query=new URLSearchParams({scope:'own',limit:'20'});if(s.app)query.set('app_id',s.app);if(s.cursor)query.set('cursor',s.cursor);
    try{const data=await request(s,'reports','/ui-executions?'+query);if(!data)return;
      const list=s.node.querySelector('[data-ui-reports]'),keep=new Set(),focused=document.activeElement,scrollTop=list.scrollTop;
      list.querySelector('[data-ui-empty-reports]')?.remove();
      let index=0;
      for(const b of data.items||[]){
        keep.add(b.ui_batch_id);let button=s.reportNodes.get(b.ui_batch_id);
        if(!button){button=document.createElement('button');button.type='button';button.className='ui-simple-report-row';button.innerHTML='<strong></strong><span></span><small></small>';s.reportNodes.set(b.ui_batch_id,button);}
        if(list.children[index]!==button)list.insertBefore(button,list.children[index]||null);index++;
        const date=new Date(Number(b.created_ns)/1e6);button.querySelector('strong').textContent=b.app_name||b.app_id;
        button.querySelector('span').textContent=`${b.owner_display_name||''} · ${Number.isFinite(date.getTime())?date.toLocaleString():''}`;
        button.querySelector('small').textContent=`${({queued:'排队中',blocked:'等待条件满足',running:'执行中',passed:'通过',failed:'失败',cancelled:'已取消',cancelling:'正在停止',interrupted:'已中断'})[b.status]||'状态待确认'} · ${({ready:'报告已就绪',failed:'报告生成失败'})[b.report_status]||'报告尚未就绪'}`;
        button.onclick=()=>{s.batchId=b.ui_batch_id;s.batchSettled=['ready','failed'].includes(b.report_status);
          if(s.batchSettled&&s.options.download&&window.UiBatchReport){s.node.querySelector('[data-ui-batch]').hidden=true;openReport(s,b.ui_batch_id);}else loadBatch(s);};
      }
      for(const[id,button]of s.reportNodes)if(!keep.has(id)){button.remove();s.reportNodes.delete(id);}
      if(focused?.isConnected&&focused.classList?.contains('ui-simple-report-row')&&document.activeElement!==focused)focused.focus({preventScroll:true});
      list.scrollTop=scrollTop;
      if(!list.childElementCount){const empty=document.createElement('p');empty.dataset.uiEmptyReports='';empty.textContent='还没有你发起的测试。';list.append(empty);}
      s.next=data.next_cursor||'';s.node.querySelector('[data-ui-prev]').disabled=!s.previous.length;s.node.querySelector('[data-ui-next]').disabled=!s.next;
    }catch(error){notice(s,error.message,true);}
  }
  async function refresh(){
    const s=state;if(!s||!valid(s)||document.hidden)return;
    pictureButtonState(s);
    const tasks=[];if(s.options.mode!=='reports'&&!s.busy)tasks.push(loadDevices(s));
    if(s.batchId&&!s.batchSettled)tasks.push(loadBatch(s));
    if(s.options.mode==='reports'&&!s.cursor)tasks.push(loadReports(s));
    if(s.reportView)tasks.push(s.reportView.refresh());
    await Promise.all(tasks);
  }
  function mount(node,options){
    leave();const mode=modes[options.mode]?options.mode:'start';
    const s={node,options:{canExecute:()=>false,canNotify:()=>false,canRefreshPictures:()=>false,canAdmit:()=>true,...options,mode},identity:options.identity(),pending:new Map(),deviceNodes:new Map(),reportNodes:new Map(),devices:[],selected:new Map(),app:'',module:'',query:'',cursor:'',previous:[],next:'',target:'',batchId:'',batchSettled:false,busy:false,startIntent:null,uncertain:false,rerunKeys:{},notifyEnabled:false,notificationPolicyKey:'',pictureRefreshAfter:0};
    state=s;const saved=read(s,'selection');if(saved&&typeof saved.app==='string'){s.app=saved.app;s.module=typeof saved.module==='string'?saved.module:'';s.target=typeof saved.target==='string'?saved.target:'';}
    s.startIntent=read(s,'pending');s.uncertain=!!s.startIntent;
    node.classList.add('ui-simple');node.innerHTML=`<header class="ui-simple-heading"><div><p>UI 自动化</p><h1>${modes[mode]}</h1></div><button type="button" class="btn-sm" data-ui-refresh>刷新</button></header><p class="ui-simple-intro">${mode==='reports'?'这里记录你发起的每次测试，报告保留当次用例、手机和结果。':'选好用例和手机，开始后自动记录本次执行结果。'}</p><p data-ui-notice role="status"></p><button type="button" class="btn-sm" data-ui-recover hidden>核对上次提交</button><fieldset data-ui-form><div class="ui-simple-filters"><label>应用<select name="ui-app"><option value="">全部应用</option></select></label>${mode!=='reports'?'<label>模块<select name="ui-module"><option value="">全部模块</option></select></label><label>搜索用例<input type="search" name="ui-query" maxlength="200" placeholder="名称或操作步骤"></label>':''}</div><div class="ui-simple-selection" data-ui-selected aria-label="已选择的用例"></div><div class="ui-simple-content"><section class="ui-simple-case-panel" aria-label="用例">${mode!=='reports'?'<p data-ui-case-count></p><div data-ui-cases></div>':'<div data-ui-reports></div>'}<div class="ui-simple-pages"><button type="button" class="btn-sm" data-ui-prev disabled>上一页</button><button type="button" class="btn-sm" data-ui-next disabled>下一页</button></div></section>${mode!=='reports'?'<section class="ui-simple-device-panel"><div class="ui-simple-heading"><h2>选择手机</h2><button type="button" class="btn-sm" data-ui-pictures-refresh hidden title="请求在线空闲手机采集截图，忙碌手机会跳过">刷新手机截图</button></div><p data-ui-picture-notice role="status" hidden></p><p data-ui-notify-target></p><label><input type="checkbox" data-ui-notify disabled> 自动发送本次报告到已配置飞书群</label><p data-ui-device-hint></p><div class="ui-simple-devices" data-ui-devices></div></section>':''}</div></fieldset>${mode!=='reports'?'<div class="ui-simple-submit"><span data-ui-selection-count></span><button type="button" class="btn-primary" data-ui-start disabled>开始测试</button></div>':''}<section data-ui-batch hidden aria-label="本次测试"></section>`;
    s.node.querySelector('[data-ui-recover]').hidden=!s.uncertain;
    s.node.querySelector('[data-ui-recover]').onclick=()=>start(s,true);
    if(s.uncertain)notice(s,'上次提交结果待确认，请先核对，避免重复执行。',true);
    const app=s.node.querySelector('[name="ui-app"]');app.onchange=()=>mode==='reports'?(s.app=app.value,s.cursor='',s.previous=[],loadReports(s)):changeApp(s,app.value);
    const load=()=>mode==='reports'?loadReports(s):loadCatalog(s);
    s.node.querySelector('[data-ui-refresh]').onclick=()=>{load();if(mode!=='reports')loadDevices(s);if(s.batchId&&!s.batchSettled)loadBatch(s);s.reportView?.refresh(true);};
    s.node.querySelector('[data-ui-prev]').onclick=()=>{if(!s.previous.length)return;s.cursor=s.previous.pop();load();};
    s.node.querySelector('[data-ui-next]').onclick=()=>{if(!s.next)return;s.previous.push(s.cursor);s.cursor=s.next;load();};
    if(mode!=='reports'){
      s.node.querySelector('[data-ui-start]').onclick=()=>start(s);
      s.node.querySelector('[data-ui-pictures-refresh]').onclick=()=>refreshPictures(s);
      s.node.querySelector('[data-ui-notify]').onchange=event=>{s.notifyEnabled=event.target.checked;invalidateStart(s);};
      s.node.querySelector('[name="ui-module"]').onchange=event=>{s.module=event.target.value;s.cursor='';s.previous=[];remember(s);loadCatalog(s);};
      s.node.querySelector('[name="ui-query"]').oninput=event=>{s.query=event.target.value;s.cursor='';s.previous=[];clearTimeout(s.searchTimer);s.searchTimer=setTimeout(()=>loadCatalog(s),300);};
      loadCatalog(s);if(s.app)loadDevices(s);else renderDevices(s);
      const last=read(s,'lastBatch');if(typeof last==='string'&&/^uib_[a-zA-Z0-9_]+$/.test(last)){s.batchId=last;loadBatch(s);}
    }else{
      // The execution catalog is admission-gated; rollback reads all currently scoped reports.
      if(s.options.canAdmit())request(s,'apps','/ui-executions/catalog').then(data=>{if(data){const values=(data.apps||[]).map(a=>[a.app_id,a.name]);app.replaceChildren(new Option('全部应用',''));for(const[v,l]of values)app.add(new Option(l,v));app.value=s.app;}}).catch(error=>notice(s,error.message,true));
      else s.app='';
      loadReports(s);
    }
    if(mode==='reports'&&options.reportIntent)openReport(s,options.reportIntent.batchId,options.reportIntent.revision);
    s.timer=setInterval(refresh,5000);
    return s;
  }
  window.UiSimpleFlow={mount,leave,reset,refresh};
})();
