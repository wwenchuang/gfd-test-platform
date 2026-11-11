// Practice runs and published evidence in the existing test workbench.
window.UiPracticeResults = (() => {
  const base = '/api/platform/requirement-design/practice-results/';
  const statusNames = {complete:'完整回放',partial:'部分回放',missing:'缺少证据',verified:'已验证',
    passed:'通过',failed:'失败',blocked:'受阻',pending:'待人工审核'};
  const stageNames = {main:'主断言',pre:'前置',cleanup:'清理'};
  const methods = {ai:'AI 视觉',control:'确定性定位',deterministic:'确定性定位',baseline:'确定性定位',playwright:'确定性定位'};
  const reasons = {model_mislocated:'模型命中了其他控件',invalid_location:'模型返回的位置无效',model_no_location:'模型未找到控件',
    baseline_assertion:'页面基线断言失败',workflow_assertion:'业务断言失败',model_timeout:'模型请求超时',run_timeout:'本轮执行超时',
    interrupted:'本轮被中断',in_flight:'正在执行',authentication:'登录状态不可用',network_guard:'请求超出只读范围',
    model_budget:'达到模型调用上限',frozen_context_changed:'页面或用例版本已变更',target_unavailable:'目标控件不可用',
    page_assets_unavailable:'未取得页面版本证据',evidence_unavailable:'未取得结果截图',provider_transport:'模型服务连接失败'};
  function add(parent, tag, text) {
    const el=document.createElement(tag);
    if(text!==undefined) el.textContent=String(text);
    parent.append(el); return el;
  }
  function detail(parent,title) {
    const section=add(parent,'details'); add(section,'summary',title); return section;
  }
  function formatTime(value) {
    const date=new Date(value);
    return value && Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN',{hour12:false}) : '未记录';
  }
  function replay(body,report) {
    add(body,'p','完整回放 '+report.fully_replayed+' / '+report.total+' 条 · 待人工审核 '+report.manual_pending+' 条 · 已生成脚本 '+report.generated+' 条');
    if(Number.isInteger(report.replayed_assertions)) add(body,'p','复用历史响应核对 '+report.replayed_assertions+' 个断言，反向验证 '+report.negative_controls+' 个。');
    add(body,'small','证据核对时间 '+formatTime(report.checked_at));
    for(const row of report.cases) {
      const item=detail(body,row.id+' · '+row.title+' · '+(statusNames[row.status]||'待核实'));
      add(item,'p','人工审核 '+(row.review==='pending'?'待处理':'待核实')+' · 原执行 '+(row.execution_id||'未留存'));
      if(Number.isInteger(row.replayed_assertions)) add(item,'p','主断言 '+row.main_assertions+' 个，前置 '+row.setup_assertions+
        ' 个，清理 '+row.cleanup_assertions+' 个。本次回放 '+row.replayed_assertions+' 个，反向验证 '+row.negative_controls+' 个。');
      if(row.case_version_id) add(item,'small','用例版本 '+row.case_version_id);
      for(const check of row.checks) {
        const line=add(item,'p');
        add(line,'strong',(stageNames[check.stage]||'断言')+' · '+(statusNames[check.status]||'待核实')+'　');
        line.append(document.createTextNode(check.title+(check.evidence?'。'+check.evidence:'')));
      }
      for(const gap of row.gaps) add(item,'p','待补证据 · '+gap);
    }
  }
  function visual(body,report,onDownload) {
    const counts=runs=>runs.filter(x=>x.status==='passed').length+' / '+runs.length+' 轮通过';
    add(body,'p',(report.model||'模型未记录')+' · '+report.scenarios.length+' 个场景 · '+report.runs.length+' 条对照记录');
    add(body,'small','更新时间 '+formatTime(report.generated_at)+(report.batch_id?' · 批次 '+report.batch_id:''));
    const target=report.limits?.rounds,usage=report.usage,limits=report.limits;
    if(target) add(body,'p','计划每种方式每个场景 '+target+' 轮。当前批次'+(report.completed?'已执行完毕。':'尚未跑完。'));
    if(usage) add(body,'p','模型调用 '+usage.calls+' 次，已返回用量 '+usage.reported_tokens+' Token，另有 '+usage.unreported_calls+' 次未返回用量。费用以账户账单为准。');
    if(limits) add(body,'small','调用上限 · 每轮 '+limits.model_calls_per_run+' 次，整批 '+limits.model_calls_total+' 次，单次输出最多 '+limits.output_tokens_per_call+' Token。');
    if(report.versions) add(body,'small','Midscene '+report.versions.midscene+' · Playwright '+report.versions.playwright+' · Chromium '+report.versions.browser);
    for(const scenario of report.scenarios) {
      const runs=report.runs.filter(r=>r.scenario_id===scenario.id);
      const section=detail(body,scenario.title+' · '+(runs.length?counts(runs):'尚无结果'));
      if(scenario.assertion) add(section,'p',scenario.assertion);
      const finished=arm=>runs.filter(r=>r.arm===arm&&r.reason!=='in_flight');
      const successful=group=>group.filter(r=>r.status==='passed').length;
      const meanTime=group=>group.length&&group.every(r=>Number.isFinite(r.elapsed_ms)) ?
        group.reduce((sum,r)=>sum+r.elapsed_ms,0)/group.length : null;
      for(const arm of ['control','ai']) {
        const group=finished(arm);
        if(!group.length) continue;
        const mean=meanTime(group),mislocated=group.filter(r=>r.reason==='model_mislocated').length;
        add(section,'p',methods[arm]+' · '+counts(group)+'（'+(successful(group)*100/group.length).toFixed(0)+'%），失败 '+
          group.filter(r=>r.status==='failed').length+' 轮，受阻 '+group.filter(r=>r.status==='blocked').length+' 轮。平均耗时 '+
          (mean===null?'未记录':(mean/1000).toFixed(2)+' 秒')+(arm==='ai'?'，拦截误定位 '+mislocated+' 轮':'')+'。');
      }
      const ai=finished('ai'),control=finished('control');
      if(!target||ai.length<target||control.length<target) add(section,'p','适用范围待定，两种方式的计划轮次尚未齐全。');
      else if(successful(ai)<ai.length) add(section,'p','保留确定性回归。AI 定位仍有失败或受阻，可展开对应轮次排查。');
      else if(successful(control)<control.length||(meanTime(ai)!==null&&meanTime(control)!==null&&meanTime(ai)<meanTime(control)))
        add(section,'p','可试用于该只读巡检。AI 全部通过，并在本批次的通过率或平均耗时上有改善。');
      else add(section,'p','稳定回归沿用确定性定位。本批次中，AI 的通过率与平均耗时未体现优势。');
      for(const run of runs) {
        const item=detail(section,(methods[run.arm]||run.arm)+' · 第 '+run.round+' 轮 · '+(run.reason==='in_flight'?'正在执行':statusNames[run.status]||'待核实'));
        if(run.assertion) add(item,'p','验证内容 · '+run.assertion);
        if(run.reason) add(item,'p','执行说明 · '+(reasons[run.reason]||run.reason));
        const elapsed=Number.isFinite(run.elapsed_ms)?(run.elapsed_ms/1000).toFixed(2)+' 秒':'未记录';
        add(item,'small','耗时 '+elapsed+' · 操作 '+(run.actions??'未记录')+' 次 · 模型调用 '+(run.model_calls??'未记录')+
          ' 次 · Token '+(run.tokens??'未返回'));
        for(const pic of run.screenshots) {
          const button=add(item,'button','下载'+(pic.label||'执行截图')); button.type='button';
          button.onclick=()=>onDownload(button,pic.sha256);
        }
      }
    }
  }
  function attachTools(page, permitted) {
    const panel=detail(page,'工具执行');
    panel.className='ui-case-source-picker ui-practice-panel ui-requirement-design';
    const suiteLabel=add(panel,'p','检查本平台已发布用例在网页中的显示，并保存执行截图和 trace。');
    const actions=add(panel,'div'); actions.className='ui-design-actions';
    function button(parent,label,action) {
      const element=add(parent,'button',label);
      element.type='button'; element.onclick=action;
      return element;
    }
    const start=button(actions,'开始检查',()=>launch());
    const refresh=button(actions,'刷新记录',()=>load());
    const notice=add(panel,'p'); notice.setAttribute('role','status');
    const result=add(panel,'section');
    const history=detail(panel,'我的运行记录'), rows=add(history,'div');
    const trace=detail(panel,'本次网页工具调用'), calls=add(trace,'ol');
    const negative=button(trace,'验证环境限制',()=>checkEnvironment());
    const checkNote=add(trace,'p');checkNote.setAttribute('role','status');
    const receipt=detail(panel,'MCP 客户端验收'), report=add(receipt,'div');
    const status={queued:'排队中',running:'执行中',passed:'通过',failed:'失败',blocked:'受阻',timed_out:'超时',interrupted:'中断'};
    const key='practice-mcp-request:'+currentAccessProfile.user_id;
    const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
    let pending=sessionStorage.getItem(key),selected='',suite=null,controller=null,timer=null,imageUrl=null;
    function clearImage() {
      if(imageUrl) URL.revokeObjectURL(imageUrl);
      imageUrl=null;
    }
    function cancel() {
      clearTimeout(timer); controller?.abort(); controller=null; clearImage();
    }
    function clearPrivate() {
      clearImage();
      for(const box of [result,rows,calls,report,checkNote]) box.replaceChildren();
      suite=null; selected='';
    }
    function controls(busy) {
      start.textContent=pending?'重试同一请求':selected?'再运行一次':'开始检查';
      start.disabled=busy||!suite; negative.disabled=busy||!suite; refresh.disabled=busy;
      rows.querySelectorAll('button').forEach(b=>{b.disabled=busy;});
    }
    function begin() {
      cancel(); controller=new AbortController(); controls(true);
      return controller.signal;
    }
    function current(signal) {
      return !signal.aborted && panel.open && permitted() && controller?.signal===signal;
    }
    async function fetchData(path,signal,body) {
      const options={headers:authHeaders(body?{'Content-Type':'application/json'}:{}),cache:'no-store',redirect:'error',
        signal:AbortSignal.any([signal,AbortSignal.timeout(20000)])};
      if(body) {options.method='POST';options.body=JSON.stringify(body);}
      const response=await nativeFetch(path,options),data=await response.json();
      if(!response.ok||data.ok!==true) throw Object.assign(Error('request'),{status:response.status,code:data.code});
      return data;
    }
    function log(tool,message) {
      if(calls.children.length>=30) calls.firstElementChild.remove();
      add(calls,'li',tool+' · '+message);
    }
    async function call(name,args,signal) {
      try {
        const {result:data}=await fetchData('/api/platform/mcp/call',signal,{name,arguments:args});
        if(current(signal)) log(name,data.run_id?'运行 '+data.run_id+' · '+(status[data.status]||'状态未知'):'已读取套件');
        return data;
      } catch(error) {
        if(current(signal)) log(name,error.code||'未收到有效回执');
        throw error;
      }
    }
    function handle(error,signal) {
      if(!current(signal)) return;
      if([401,403].includes(error.status)) {
        clearPrivate();notice.textContent='登录状态或权限已变化，请刷新页面。';
      } else if(error.code==='check_busy') {
        notice.textContent='已有目录检查正在执行，请稍后重试同一请求。';
      } else if(error.code==='execution_disabled') {
        notice.textContent='服务器尚未启用工具执行。可刷新记录查看已有结果。';
      } else {
        notice.textContent=pending?'尚未确认提交结果，请重试同一请求。':'读取失败，请刷新记录后重试。';
      }
    }
    async function checkEnvironment() {
      const signal=controller?.signal;
      if(!signal||!current(signal)||negative.disabled) return;
      negative.disabled=true;checkNote.textContent='正在验证环境参数…';
      try {
        const run=await call('run_suite',{suite_id:suite.suite_id,environment:'unlisted',request_id:makeRequestId()},signal);
        if(current(signal)) checkNote.textContent='验证未通过，服务接受了未列出的环境。运行编号 '+(run.run_id||'未返回')+'。';
      } catch(error) {
        if(!current(signal)) return;
        if(error.status===400&&error.code==='invalid_arguments') checkNote.textContent='已拒绝未列出的环境（invalid_arguments）。';
        else if([401,403].includes(error.status)) {handle(error,signal);controls(false);}
        else checkNote.textContent='未取得预期的参数校验结果，请重试。';
      } finally {if(current(signal)) negative.disabled=!suite;}
    }
    function showReceipt(data) {
      report.replaceChildren();
      if(!data) {add(report,'p','暂无当前账号可查看的客户端验收记录。');return;}
      add(report,'small',formatTime(data.checked_at*1000)+' · 运行 '+data.run_id);
      for(const row of data.checks) add(report,'p',row.name+' · '+(status[row.status]||'未知')+' · '+row.detail);
    }
    function showRows(items) {
      rows.replaceChildren();
      if(!items.length) {add(rows,'p','暂无运行记录。');return;}
      for(const row of items) {
        if(!uuid.test(row.run_id)) continue;
        button(rows,formatTime(row.created_at*1000)+' · '+(status[row.status]||'未知')+' · '+row.run_id,()=>load(row.run_id));
      }
    }
    async function openArtifact(btn,run,item,signal) {
      const expected='/api/ui-executions/web-check/artifact?id='+run.runner_id+'&item='+item;
      if(!current(signal)||run.evidence?.[item]!==expected||!/^[a-f0-9]{32}$/.test(run.runner_id)) return;
      btn.disabled=true;
      try {
        const response=await nativeFetch(expected,{headers:authHeaders(),cache:'no-store',redirect:'error',
          signal:AbortSignal.any([signal,AbortSignal.timeout(30000)])});
        if(!response.ok) throw Object.assign(Error('artifact'),{status:response.status});
        const image=item==='screenshot',type=image?'image/png':'application/zip',limit=(image?8:30)*1024*1024;
        if(response.headers.get('content-type')?.split(';')[0]!==type || Number(response.headers.get('content-length'))>limit) throw Error('format');
        const blob=await response.blob(); if(!blob.size||blob.size>limit) throw Error('size');
        if(!current(signal)||!btn.isConnected) return;
        const url=URL.createObjectURL(blob);
        if(image) {
          clearImage(); imageUrl=url; result.querySelector('img')?.remove();
          const pic=add(result,'img'); pic.src=url;pic.alt='本次用例库检查截图';
        } else {
          const anchor=add(result,'a'); anchor.href=url; anchor.download='工具执行-'+run.run_id+'-trace.zip';
          anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
          notice.textContent='已发起 trace 下载，请查看浏览器下载记录。';
        }
      } catch(error) {
        if([401,403].includes(error.status)) {handle(error,signal);controls(false);}
        else if(current(signal)) notice.textContent='证据读取失败，请刷新记录后重试。';
      } finally {btn.disabled=false;}
    }
    async function readRun(id,signal) {
      const run=await call('get_run',{run_id:id},signal);
      if(!current(signal)) return;
      if(run.run_id!==id) throw Error('run_id');
      selected=id; clearImage();result.replaceChildren();
      add(result,'h3','当前运行 · '+(status[run.status]||'状态未知'));
      add(result,'small','运行编号 '+id);
      add(result,'small','请求编号 '+run.request_id);
      if(run.created_at) add(result,'p','提交 '+formatTime(run.created_at*1000)+(run.finished_at?' · 结束 '+formatTime(run.finished_at*1000):''));
      if(Number.isInteger(run.total)) add(result,'p','接口返回 '+run.total+' 条已发布用例。');
      if(run.runner_id) add(result,'small','浏览器检查编号 '+run.runner_id);
      if(run.error_kind) {
        const reasons={authentication:'账户登录或权限已变化',environment:'浏览器或服务环境不可用',evidence:'执行证据未能保存',
          business:'页面显示与预期不符',browser_timeout:'浏览器检查超时',worker_timeout:'执行超过时限',
          heavy_workload_busy:'平台正在执行其他任务',check_busy:'已有目录检查正在执行',worker_unavailable:'执行线程未启动',
          service_restarted:'执行进程已停止',execution_disabled:'平台已暂停工具执行',invalid_runner_result:'执行器回执不完整'};
        add(result,'p','执行原因 · '+(reasons[run.error_kind]||run.error_kind));
      }
      const links=add(result,'div');links.className='ui-design-actions';
      for(const [item,label] of [['screenshot','查看截图'],['trace','下载 trace']]) {
        if(run.evidence?.[item]) {const btn=button(links,label,()=>openArtifact(btn,run,item,signal));}
      }
      if(['queued','running'].includes(run.status)) timer=setTimeout(()=>{if(current(signal)) load(id);},4000);
    }
    async function load(id) {
      if(!panel.open||!permitted()) return;
      const signal=begin();
      notice.textContent='正在读取套件和运行记录…';
      try {
        const [catalog,list,acceptance]=await Promise.all([
          call('list_suites',{},signal),fetchData('/api/platform/mcp/runs',signal),
          fetchData('/api/platform/mcp/acceptance',signal).catch(error=>{
            if([401,403].includes(error.status)) throw error;
            return null;
          })]);
        if(!current(signal)) return;
        suite=catalog.suites.find(x=>x.suite_id==='published-catalog'&&x.environment==='platform');
        suiteLabel.textContent=suite?suite.title+' · 在平台环境核对页面与接口的已发布用例数量。':'当前账号没有可用套件。';
        showRows(list.runs);
        if(acceptance) showReceipt(acceptance.report);
        else report.textContent='客户端验收记录暂时无法读取，请刷新记录。';
        const target=id||selected||list.runs[0]?.run_id;
        result.replaceChildren();
        if(target&&uuid.test(target)) await readRun(target,signal);
        if(current(signal)) notice.textContent=pending?'上次提交尚待确认，可重试同一请求。':'';
      } catch(error) {if(current(signal)){clearPrivate(); handle(error,signal);}}
      finally {if(current(signal)) controls(false);}
    }
    function makeRequestId() {
      const bytes=crypto.getRandomValues(new Uint8Array(16));
      bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
      const hex=Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');
      return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
    }
    async function launch() {
      if(start.disabled||!permitted()||!suite) return;
      const signal=begin();
      try {
        if(!uuid.test(pending)) {pending=makeRequestId();sessionStorage.setItem(key,pending);}
        notice.textContent='正在提交检查…';
        const run=await call('run_suite',{suite_id:suite.suite_id,environment:suite.environment,request_id:pending},signal);
        if(!current(signal)) return;
        if(!uuid.test(run.run_id)||run.request_id!==pending) throw Error('receipt');
        pending=null;sessionStorage.removeItem(key);
        await load(run.run_id);
      } catch(error) {
        if(current(signal)&&error.status===404&&error.code==='run_not_found') {
          pending=null;sessionStorage.removeItem(key);
          clearPrivate();await load();
          if(panel.open&&permitted()&&suite) notice.textContent='当前权限范围无法读取该请求。可重新发起检查。';
        } else handle(error,signal);
      } finally {if(current(signal)) controls(false);}
    }
    panel.ontoggle=()=>{if(panel.open) load();else {cancel();clearPrivate();notice.textContent='';}};
    controls(false);
    return ()=>{cancel();panel.remove();};
  }
  function attach(page, current, context) {
    const attached=context(), active=new Set(), panels=[];
    let disposed=false;
    function permitted() {return !disposed && current() && attached===context();}
    function dispose() {
      if(disposed) return;
      disposed=true; clearInterval(guard);
      for(const controller of active) controller.abort();
      disposeTools();
      for(const panel of panels) panel.remove();
    }
    async function request(path, binary) {
      const controller=new AbortController(); active.add(controller);
      const timeout=setTimeout(()=>controller.abort(),20000);
      try {
        const res=await nativeFetch(base+path,{headers:authHeaders(),signal:controller.signal,cache:'no-store',redirect:'error'});
        if(!res.ok) {const err=Error('request'); err.status=res.status; throw err;}
        if(binary) {
          if(res.headers.get('Content-Type')?.split(';')[0]!=='image/png') throw Error('format');
          const image=await res.blob();
          if(image.size>8*1024*1024||!image.size) throw Error('size');
          return image;
        }
        const data=await res.json();
        if(!data.ok||!data.report) throw Error('format');
        return data.report;
      } finally {clearTimeout(timeout); active.delete(controller);}
    }
    function message(error) {
      if(error.status===401||error.status===403) return '登录状态或权限已变化，请刷新页面。';
      if(error.status===404) return '暂时没有可查看的已发布证据。';
      return '证据暂时无法读取，请稍后刷新。';
    }
    function panelFor(kind,title) {
      const panel=detail(page,title);panels.push(panel);
      panel.className='ui-case-source-picker ui-practice-panel ui-requirement-design';
      const actions=add(panel,'div');actions.className='ui-design-actions';
      const refresh=add(actions,'button','刷新结果');refresh.type='button';
      const notice=add(panel,'p');notice.setAttribute('role','status');
      const body=add(panel,'div');let revision=0;
      function clear() {revision++;body.replaceChildren();}
      async function download(button,id) {
        if(!permitted()||!/^[0-9a-f]{64}$/.test(id)) return;
        const version=revision;button.disabled=true;
        try {
          const blob=await request('screenshot?id='+id,true);
          if(!permitted()||revision!==version||!panel.open) return;
          const url=URL.createObjectURL(blob),anchor=document.createElement('a');
          anchor.href=url; anchor.download='视觉对照-'+id.slice(0,12)+'.png';anchor.click();
          setTimeout(()=>URL.revokeObjectURL(url),0);
        } catch(error) {
          if(permitted()&&revision===version) {clear();notice.textContent=message(error);}
        } finally {button.disabled=false;}
      }
      async function load() {
        if(!permitted()) {dispose();return;}
        clear();const version=revision;refresh.disabled=true;notice.textContent='正在读取已发布结果…';
        try {
          const report=await request(kind,false);
          if(!permitted()||version!==revision||!panel.open) return;
          if(kind==='replay') replay(body,report); else visual(body,report,download);
          notice.textContent='';
        } catch(error) {
          if(permitted()&&version===revision) {body.replaceChildren();notice.textContent=message(error);}
        } finally {if(version===revision) refresh.disabled=false;}
      }
      panel.ontoggle=()=>{if(panel.open) load(); else {clear();refresh.disabled=false;notice.textContent='';}};
      refresh.onclick=load;
    }
    const guard=setInterval(()=>{if(!permitted()) dispose();},500);
    const disposeTools=attachTools(page,()=>permitted()&&hasPermission('ui.view'));
    if(hasPermission('platform.configure') && hasPermission('api.view') &&
       (currentAccessProfile?.is_superuser===true || currentAccessProfile?.scope?.ui_apps==='*')) {
      panelFor('replay','接口回放证据');panelFor('visual','视觉巡检对照');
    }
    return dispose;
  }
  return {attach};
})();
