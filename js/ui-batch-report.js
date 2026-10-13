// Report actions never create execution jobs. A report revision pins every export.
(() => {
  const make=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=String(text);return node;};
  function mount(node,options){
    const identity=options.identity(),base='/ui-executions/'+encodeURIComponent(options.batchId);
    let active=true,busy=false,revealed=false,status='',revision='',snapshot=null,controller=null,nextRefreshAt=0,evidenceOpen=false,evidenceFocus=null,reportFocus=null,deliverySignature='',refreshError='';
    const valid=()=>active&&node.isConnected&&options.identity()===identity;
    node.classList.add('ui-batch-report');
    node.innerHTML='<header><div><p>本人本次执行</p><h2 data-report-title>本次报告</h2></div><button type="button" class="btn-sm" data-report-close>收起</button></header><p data-report-owner></p><p data-report-state role="status">正在读取报告…</p><div class="ui-report-actions"><button type="button" class="btn-sm" data-report-retry hidden>重新生成报告</button><button type="button" class="btn-sm" data-report-download="docx" hidden>下载 Word</button><button type="button" class="btn-sm" data-report-download="html" hidden>下载 HTML</button><button type="button" class="btn-sm" data-report-download="md" hidden>下载 Markdown</button><button type="button" class="btn-sm" data-report-copy hidden>复制测试摘要</button></div><div data-report-notifications role="status"></div><pre data-report-summary></pre><div data-report-attempts></div><p data-report-message role="status"></p>';
    const text=(selector,value)=>{node.querySelector(selector).textContent=value;};
    async function action(button,callback){
      if(!valid()||button.disabled)return;button.disabled=true;refreshError='';text('[data-report-message]','');
      try{const message=await callback();if(valid()&&message)text('[data-report-message]',message);}
      catch(error){if(valid())text('[data-report-message]',error.message||'操作失败，请重试。');}
      finally{if(valid())button.disabled=false;}
    }
    function render(report){
      if(status==='ready'&&report.status==='ready'&&revision&&revision===String(report.report_revision||''))return;
      const focused=document.activeElement,previousEvidence=node.querySelector('details');
      if(node.contains(focused)){reportFocus=focused;evidenceFocus=focused===previousEvidence?.querySelector('summary')?'summary':focused.dataset.artifactId||null;}
      else if(focused!==document.body){reportFocus=null;evidenceFocus=null;}
      if(previousEvidence)evidenceOpen=previousEvidence.open;
      status=report.status;revision=String(report.report_revision||'');snapshot=report.snapshot;
      const ready=status==='ready'&&!!snapshot;
      text('[data-report-state]',ready?'报告已就绪':status==='failed'?'报告生成失败，可以单独重试。':status==='unavailable'?'当前无法访问这份报告。':'报告正在生成，完成后可下载。');
      node.querySelector('[data-report-retry]').hidden=status!=='failed'||!!options.revision;
      node.querySelectorAll('[data-report-download],[data-report-copy]').forEach(button=>{button.hidden=!ready;});
      if(!ready){text('[data-report-title]','本次报告');text('[data-report-summary]','');delete node.querySelector('[data-report-summary]').dataset.outcome;text('[data-report-owner]','');node.querySelector('[data-report-attempts]').replaceChildren();if(status==='unavailable'){node.querySelector('[data-report-notifications]').replaceChildren();deliverySignature='';reportFocus=null;evidenceFocus=null;evidenceOpen=false;}return;}
      const view=snapshot.presentation;
      text('[data-report-title]',view?.title||'发布测试报告');
      text('[data-report-owner]','测试版本：'+(view?.test_versions?.join('；')||'未获取')+'\n执行人：'+(view?.owner||snapshot.owner_display_name||'未记录')+(view?.time_range?'\n测试时间：'+view.time_range:''));
      text('[data-report-summary]',view?(view.conclusion_icon||'')+' '+view.conclusion:snapshot.summary||'');
      const host=node.querySelector('[data-report-attempts]');host.replaceChildren();
      if(view){
        node.querySelector('[data-report-summary]').dataset.outcome=view.conclusion_status;
        host.append(make('p',`本次共 ${view.case_count} 条用例，已执行 ${view.executed_cases} 条；通过 ${view.passed_count} 项，未通过 ${view.failed_count} 项，未完成 ${view.incomplete_count} 项。`));
        for(const environment of view.environments||[])host.append(make('p','测试环境：'+environment));
        const container=make('div'),table=make('table'),head=make('thead'),row=make('tr'),body=make('tbody');container.className='ui-report-coverage';
        for(const label of ['测试用例','覆盖场景与预期','结果'])row.append(make('th',label));head.append(row);table.append(head,body);
        for(const item of view.coverage||[]){const tr=make('tr');
          const target=item.target_label||({ios:'苹果系统',android:'安卓系统'})[item.platform]||'系统未记录';
          tr.append(make('td',item.case_name+`（${target}）`+(item.attempt_no>1?`（第 ${item.attempt_no} 次）`:'')),make('td',item.scenario+'；预期：'+item.expected),make('td',item.result));body.append(tr);
        }
        host.append(make('h3','🎯 覆盖场景及结果'));container.append(table);host.append(container);
        if(view.concerns?.length){host.append(make('h3','⚠️ 需关注事项'));const list=make('ul');for(const item of view.concerns)list.append(make('li',item));host.append(list);}
      }
      const count=(snapshot.artifacts||[]).length;
      if(count){
        const evidence=make('details');evidence.open=evidenceOpen;evidence.append(make('summary',`执行证据（${count} 份）`));
        for(const [index,item]of (snapshot.artifacts||[]).entries()){
          if(!/^[a-f0-9]{64}$/.test(item.artifact_id)||!['html','jpg','png'].includes(item.kind))continue;
          const button=make('button',`下载${item.kind==='html'?'原始报告静态内容':'截图'} ${index+1}`);button.type='button';button.className='btn-sm';button.dataset.artifactId=item.artifact_id;
          button.onclick=()=>action(button,()=>options.download(base+'/artifacts/'+item.artifact_id));evidence.append(button);
        }host.append(evidence);
      }
      if(document.activeElement===document.body){
        const focusTarget=reportFocus?.isConnected?reportFocus:evidenceFocus==='summary'?node.querySelector('details summary'):evidenceFocus?node.querySelector(`[data-artifact-id="${evidenceFocus}"]`):null;
        focusTarget?.focus({preventScroll:true});
      }
      if(!revealed){revealed=true;node.scrollIntoView?.({block:'start',behavior:'smooth'});}
    }
    async function refreshDeliveries(){
      const result=await options.request(base+'/deliveries'+(revision?'?'+new URLSearchParams({revision}):''),{signal:controller?.signal});if(!valid())return;
      if(!result?.ok||!Array.isArray(result.items))return;
      const signature=JSON.stringify([result,options.canNotify?.()===true]);if(signature===deliverySignature)return;deliverySignature=signature;
      const host=node.querySelector('[data-report-notifications]');host.replaceChildren();
      if(!result.policy?.enabled){host.append(make('p','本次未开启飞书通知。'));return;}
      host.append(make('p','飞书通知目标：'+(result.policy.target_label||'未配置')));
      if(!result.items.length)host.append(make('p','报告就绪后自动发送。'));
      for(const item of result.items.filter(item=>!revision||item.report_revision===revision)){
        host.append(make('p',item.message||'通知状态待确认'));
        if(options.canNotify?.()&&['failed','unknown'].includes(item.status)){
          const button=make('button',item.status==='unknown'?'核对后补发':'补发本次报告');button.type='button';button.className='btn-sm';button.dataset.deliveryRetry=item.delivery_key;
          button.onclick=()=>action(button,async()=>{
            if(!options.canNotify?.())throw new Error('当前账号没有补发权限');
            const uncertain=item.status==='unknown';
            if(uncertain&&!window.confirm('请先核对飞书群内消息。确认确实未收到后才能补发；补发仍可能产生重复消息。已核对未收到，继续补发？'))return;
            const response=await options.request(base+'/deliveries/'+encodeURIComponent(item.delivery_key)+'/retry',{method:'POST',body:{confirmed_not_received:uncertain}});
            if(!response?.ok)throw new Error('补发未完成，请核对权限与渠道配置');
            if(valid())await refreshDeliveries();return '已请求补发本次报告。';
          });host.append(button);
        }
      }
    }
    function showRefreshError(message){refreshError=message;text('[data-report-message]',message);}
    async function refresh(force=false,method='GET'){
      // The parent supplies the visible-page timer; settled reports still check for delayed evidence.
      if(!valid()||document.hidden||node.hidden||busy||(!force&&Date.now()<nextRefreshAt))return;
      busy=true;controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),30000);
      try{
        const data=await options.request(base+'/report'+(options.revision&&method==='GET'?'?'+new URLSearchParams({revision:options.revision}):''),{method,...(method==='POST'?{body:{}}:{}),signal:controller.signal});
        if(!valid())return;
        if(!data?.ok||!data.report)throw new Error(data?.error||'报告暂时不可用');
        if(options.revision&&data.report.report_revision!==options.revision)throw new Error('报告版本不一致，请重新打开通知中的原始链接');
        if(refreshError&&node.querySelector('[data-report-message]').textContent===refreshError)text('[data-report-message]','');
        refreshError='';
        render(data.report);
        try{await refreshDeliveries();}catch(error){if(valid())showRefreshError(error.message||'通知状态暂时不可用');}
      }catch(error){if(valid()){if([401,403,404].includes(error.status))render({status:'unavailable',snapshot:null});showRefreshError(error.message||'报告读取失败，请重试');}}
      finally{clearTimeout(timeout);busy=false;nextRefreshAt=Date.now()+(status==='ready'||status==='unavailable'?15000:5000);}
    }
    function dispose(){active=false;controller?.abort();node.replaceChildren();}
    node.querySelector('[data-report-close]').onclick=()=>{dispose();node.hidden=true;};
    node.querySelector('[data-report-retry]').onclick=event=>action(event.currentTarget,()=>refresh(true,'POST'));
    node.querySelectorAll('[data-report-download]').forEach(button=>{button.onclick=()=>action(button,async()=>{
      const filename=await options.download(base+'/report/download?'+new URLSearchParams({format:button.dataset.reportDownload,revision}));
      return `已发起下载：${filename}。请在浏览器下载记录中确认。`;
    });});
    node.querySelector('[data-report-copy]').onclick=event=>action(event.currentTarget,async()=>{await options.copy(snapshot.summary||'');return '测试摘要已复制。';});
    refresh();return {refresh,dispose};
  }
  window.UiBatchReport={mount};
})();
