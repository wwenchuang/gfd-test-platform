// A batch view renders confirmed server evidence only. It does not dispatch or infer results.
(function () {
  const states = {
    queued:['○','等待执行'], pending:['○','未执行'], preparing:['●','准备中'], running:['●','执行中'],
    passed:['✓','通过'], success:['✓','通过'], failed:['×','失败'], timeout:['×','超时'],
    blocked:['!','暂不可执行'], cancelling:['…','正在取消'], cancelled:['−','已取消'],
    interrupted:['!','已中断'], skipped:['−','未执行'], unknown:['○','状态待确认']
  };
  const terminal = new Set(['passed','success','failed','timeout','cancelled','interrupted','skipped']);
  const views = new WeakMap();
  const settled = value => typeof value.execution_settled === 'boolean' ? value.execution_settled : terminal.has(value.status);
  const status = value => Object.hasOwn(states,value) ? value : 'unknown';
  const label = value => states[status(value)][1];
  const text = (parent, selector, value) => { parent.querySelector(selector).textContent = String(value ?? ''); };
  function make(tag, className, content) {
    const node=document.createElement(tag); node.className=className;
    if (content != null) node.textContent=String(content);
    return node;
  }
  function badge(value) {
    const s=status(value), node=make('span','ui-state ui-state-'+s,states[s][0]);
    node.setAttribute('aria-label',label(s)); node.title=label(s);return node;
  }
  function init(node,batchId) {
    node.replaceChildren();node.classList.add('ui-batch-view');
    node.innerHTML='<header class="ui-batch-head"><div><p class="ui-eyebrow">本次测试</p><h2 data-batch-title></h2><p data-batch-app></p><p data-batch-owner></p></div><strong data-batch-state></strong></header><div class="ui-batch-totals"><span data-batch-count></span><span data-batch-results></span></div><progress data-batch-progress aria-label="已结束的用例执行"></progress><p data-batch-hint role="status"></p><div class="ui-batch-actions"></div><p data-batch-action-error role="alert" hidden></p><div class="ui-batch-attempts"></div><footer class="ui-batch-foot"><span data-batch-report></span><span data-batch-delivery></span><small data-batch-id></small></footer>';
    const state={batchId,rows:new Map(),actions:{},busy:new Set(),batch:null};views.set(node,state);
    for (const [key,title] of [['cancel','取消本次测试'],['rerun','重跑未通过项'],['report','查看本次报告'],['copy','复制测试摘要']]) {
      const button=make('button','btn-sm',title);button.type='button';button.dataset.batchAction=key;
      button.onclick=async()=>{
        if (views.get(node)!==state || state.busy.has(key) || typeof state.actions[key]!=='function') return;
        state.busy.add(key);button.disabled=true;
        const error=node.querySelector('[data-batch-action-error]');error.hidden=true;
        try { await state.actions[key](state.batchId); }
        catch (e) { if(views.get(node)===state){error.textContent=e?.message || '操作未完成，请重试';error.hidden=false;} }
        finally { if(views.get(node)===state){state.busy.delete(key);updateActions(node,state);} }
      };
      node.querySelector('.ui-batch-actions').append(button);
    }
    return state;
  }
  function updateActions(node,state) {
    const b=state.batch, attempts=b.case_attempts || [];
    const available={cancel:!settled(b)&&b.status!=='cancelling',
      rerun:settled(b)&&attempts.some(a=>!['passed','success'].includes(a.status)),
      report:settled(b)&&['pending','generating','ready','failed'].includes(b.report_status),copy:b.report_status==='ready'};
    node.querySelectorAll('[data-batch-action]').forEach(button=>{
      const key=button.dataset.batchAction;
      button.hidden=!available[key] || typeof state.actions[key]!=='function';
      button.disabled=state.busy.has(key);
    });
  }
  function rowFor(state, attempt) {
    let row=state.rows.get(attempt.attempt_id);
    if (!row) {
      row=make('details','ui-batch-attempt');row.dataset.attemptId=attempt.attempt_id;
      row.innerHTML='<summary><span data-attempt-icon></span><span class="ui-attempt-name"><strong data-attempt-title></strong><small data-attempt-device></small></span><span data-attempt-state></span></summary><div class="ui-attempt-body"><p data-attempt-hint></p><ol class="ui-batch-steps"></ol></div>';
      state.rows.set(attempt.attempt_id,row);
    }
    return row;
  }
  function update(node,batch,actions={}) {
    if (!node || !batch?.ui_batch_id) return;
    let state=views.get(node);
    if(!state || state.batchId!==batch.ui_batch_id) state=init(node,batch.ui_batch_id);
    state.batch=batch;state.actions=actions;
    const attempts=Array.isArray(batch.case_attempts)?batch.case_attempts:[];
    const done=attempts.filter(settled).length;
    const passed=attempts.filter(a=>['passed','success'].includes(a.status)).length;
    const failed=attempts.filter(a=>['failed','timeout'].includes(a.status)).length;
    text(node,'[data-batch-title]',batch.suite_name || batch.app_name || batch.app_id || 'UI 测试');
    text(node,'[data-batch-app]',batch.app_name || batch.app_id || '应用待确认');
    text(node,'[data-batch-owner]',`执行者：${batch.owner_display_name || batch.owner_username || '账号信息待确认'}`);
    text(node,'[data-batch-state]',label(batch.status));
    node.querySelector('[data-batch-state]').className='ui-state-text-'+status(batch.status);
    text(node,'[data-batch-count]',`${done} / ${attempts.length} 项已结束`);
    text(node,'[data-batch-results]',`通过 ${passed} · 失败 ${failed} · 其余 ${attempts.length-passed-failed}`);
    const progress=node.querySelector('[data-batch-progress]');progress.max=Math.max(1,attempts.length);progress.value=done;
    text(node,'[data-batch-hint]',batch.message || (batch.stop_pending?'正在等待手机确认停止；确认前不会释放设备。':batch.status==='queued'?'等待所选手机接单；可取消本次测试。':batch.status==='cancelling'?'已请求取消，正在等待手机确认停止。':batch.status==='running'?'按手机真实上报更新步骤。':label(batch.status)));
    const reportLabels={pending:'报告等待生成',generating:'报告生成中',ready:'本次报告已就绪',failed:'报告生成失败，可补生成'};
    const deliveryLabels={not_requested:'未启用飞书通知',pending:'飞书通知待发送',sending:'飞书通知发送中',sent:'飞书通知已发送',failed:'飞书通知发送失败',unknown:'飞书送达待核对'};
    text(node,'[data-batch-report]',reportLabels[batch.report_status] || '报告状态待确认');
    text(node,'[data-batch-delivery]',deliveryLabels[batch.delivery_status] || '通知状态待确认');
    text(node,'[data-batch-id]',batch.ui_batch_id);
    const titles=new Map((batch.snapshots||[]).map(s=>[s.case_id,s.metadata?.task_name || s.case_id]));
    const rank=s=>s==='running'?0:['preparing','cancelling'].includes(s)?1:['queued','pending','blocked'].includes(s)?2:3;
    const list=node.querySelector('.ui-batch-attempts'), ids=new Set();
    const focused=node.ownerDocument.activeElement;
    const ordered=attempts.map((a,i)=>({a,i})).sort((x,y)=>rank(x.a.status)-rank(y.a.status)||x.i-y.i);
    ordered.forEach(({a},index)=>{
      ids.add(a.attempt_id);const row=rowFor(state,a);
      text(row,'[data-attempt-title]',titles.get(a.case_id)||a.case_id||'测试用例');
      const device=a.target_resolution?.device_snapshot || {};
      const platform=a.platform==='ios'?'iOS':a.platform==='android'?'Android':'平台待确认';
      text(row,'[data-attempt-device]',`${device.label || device.model || a.device_id || '所选手机'} · ${platform} · 第 ${a.attempt_no || 1} 次`);
      text(row,'[data-attempt-state]',label(a.status));
      row.querySelector('[data-attempt-icon]').replaceChildren(badge(a.status));
      const known=a.step_progress_known===true && Array.isArray(a.steps);
      text(row,'[data-attempt-hint]',a.block_message || a.message || (known?(a.step_progress_truncated?'当前仅展示部分步骤，完整结果请查看报告。':'步骤状态来自本次手机执行。'):'尚无步骤证据，仅用例级进度。'));
      const signature=JSON.stringify(known?a.steps:[]);
      if(row._stepsSignature!==signature){
        row._stepsSignature=signature;const steps=row.querySelector('ol');steps.replaceChildren();
        if(known) for(const step of a.steps){
          const li=make('li','ui-batch-step');li.dataset.stepState=status(step.status);li.dataset.stepId=String(step.step_id || '');
          li.append(badge(step.status),make('span','ui-step-label',step.label || '操作'),make('small','ui-step-state',label(step.status)));steps.append(li);
        }
      }
      // Only move nodes when order changes; normal progress refresh preserves keyboard focus.
      if(list.children[index]!==row) list.insertBefore(row,list.children[index] || null);
    });
    for(const [id,row] of state.rows){if(!ids.has(id)){row.remove();state.rows.delete(id);}}
    if(focused && list.contains(focused) && node.ownerDocument.activeElement!==focused) focused.focus({preventScroll:true});
    updateActions(node,state);
  }
  window.UiBatchView={update};
})();
