// Read-only, published evidence in the existing test workbench.
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
  function attach(page, current, context) {
    const attached=context(), active=new Set(), panels=[];
    let disposed=false;
    function permitted() {return !disposed && current() && attached===context();}
    function dispose() {
      if(disposed) return;
      disposed=true; clearInterval(guard);
      for(const controller of active) controller.abort();
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
    panelFor('replay','接口回放证据');panelFor('visual','视觉巡检对照');
    return dispose;
  }
  return {attach};
})();
