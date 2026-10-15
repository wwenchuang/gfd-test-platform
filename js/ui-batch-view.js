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
    node.innerHTML='<div class="ui-batch-layout"><div class="ui-batch-overview"><header class="ui-batch-head"><div><p class="ui-eyebrow">执行结果</p><h2 data-batch-title></h2><div class="ui-batch-meta"><span data-batch-app></span><span data-batch-owner></span></div></div><strong data-batch-state></strong></header><div class="ui-batch-totals"><span data-batch-count></span><span data-batch-results></span></div><progress data-batch-progress aria-label="已结束的用例执行"></progress><p data-batch-hint role="status"></p><div class="ui-batch-actions"></div></div><div class="ui-batch-detail"><h3 class="ui-batch-detail-title">用例结果</h3><p data-batch-action-error role="alert" hidden></p><div class="ui-batch-attempts"></div></div></div><footer class="ui-batch-foot"><span data-batch-report></span><span data-batch-delivery></span><details class="ui-batch-identifier"><summary>执行编号</summary><small data-batch-id></small></details></footer>';
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
      report:settled(b)&&['not_requested','pending','generating','ready','failed'].includes(b.report_status),copy:b.report_status==='ready'};
    node.querySelectorAll('[data-attempt-recover]').forEach(button=>{button.disabled=state.busy.has('recover');});
    node.querySelectorAll('[data-batch-action]').forEach(button=>{
      const key=button.dataset.batchAction;
      if(key==='report')button.textContent=b.report_status==='not_requested'?'查看执行截图':'查看本次报告';
      button.hidden=!available[key] || typeof state.actions[key]!=='function';
      button.disabled=state.busy.has(key);
    });
  }
  function rowFor(state, attempt) {
    let row=state.rows.get(attempt.attempt_id);
    if (!row) {
      row=make('details','ui-batch-attempt');row.dataset.attemptId=attempt.attempt_id;
      row.innerHTML='<summary><span data-attempt-icon></span><span class="ui-attempt-name"><strong data-attempt-title></strong><small data-attempt-device></small></span><span data-attempt-state></span></summary><div class="ui-attempt-body"><p data-attempt-hint></p><p data-attempt-recovery-note hidden></p><button type="button" class="btn-sm" data-attempt-recover hidden>核对停止状态</button><ol class="ui-batch-steps"></ol></div>';
      state.rows.set(attempt.attempt_id,row);
    }
    return row;
  }
  function recoveryIdentity(attempt) {
    return JSON.stringify([attempt.attempt_id,attempt.status,attempt.event_sequence,attempt.manual_stop_recovery_version]);
  }
  function recoveryCurrent(node,state) {
    return views.get(node)===state && state.actions.isCurrent?.()!==false && typeof state.actions.recover==='function';
  }
  function closeRecovery(node,state,restore=true) {
    const panel=node.querySelector('[data-stop-confirmation]');
    const focusedInside=panel?.contains(node.ownerDocument.activeElement);if(panel)panel.hidden=true;
    const button=state.confirmation?.button;state.confirmation=null;
    if(restore&&focusedInside&&button?.isConnected){
      const attempt=state.batch.case_attempts?.find(a=>a.attempt_id===button.closest('[data-attempt-id]').dataset.attemptId);
      const target=attempt?.manual_stop_recovery_eligible&&!button.hidden?button:button.closest('details').querySelector('summary');target?.focus({preventScroll:true});
    }
  }
  function openRecovery(node,state,attempt,button) {
    if(!recoveryCurrent(node,state)||!attempt.manual_stop_recovery_eligible||state.busy.has('recover'))return;
    let panel=node.querySelector('[data-stop-confirmation]');
    if(!panel){
      panel=make('form','ui-stop-confirmation');panel.dataset.stopConfirmation='';panel.hidden=true;
      panel.innerHTML='<p data-stop-target></p><p>关闭浏览器不会停止执行。请到执行机核对该任务及其子进程已结束，再确认释放此手机。</p><label><input type="checkbox" required> 已在执行机确认该任务及其子进程已结束</label><div><button type="submit" class="btn-sm" data-stop-submit disabled>确认已停止</button><button type="button" class="btn-sm" data-stop-dismiss>取消</button></div>';
      panel.querySelector('[data-stop-dismiss]').onclick=()=>closeRecovery(node,state);
      panel.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();closeRecovery(node,state);}};
      panel.querySelector('input').onchange=()=>{panel.querySelector('[data-stop-submit]').disabled=!panel.querySelector('input').checked||state.busy.has('recover');};
      panel.onsubmit=async e=>{
        e.preventDefault();const c=state.confirmation;
        if(!c||!recoveryCurrent(node,state)||!panel.querySelector('input').checked||state.busy.has('recover'))return;
        const current=state.batch.case_attempts.find(a=>a.attempt_id===c.payload.attempt_id);
        if(!current?.manual_stop_recovery_eligible||recoveryIdentity(current)!==c.signature){closeRecovery(node,state);return;}
        state.busy.add('recover');updateActions(node,state);panel.querySelector('[data-stop-submit]').disabled=true;
        const error=node.querySelector('[data-batch-action-error]');error.hidden=true;
        try{await state.actions.recover(state.batchId,c.payload);if(recoveryCurrent(node,state))closeRecovery(node,state);}
        catch(e){if(recoveryCurrent(node,state)){closeRecovery(node,state);error.textContent=e?.message||'确认未完成，请刷新后重新核对';error.hidden=false;}}
        finally{if(views.get(node)===state){state.busy.delete('recover');updateActions(node,state);}}
      };
      node.querySelector('.ui-batch-actions').after(panel);
    }
    state.confirmation={button,signature:recoveryIdentity(attempt),payload:{attempt_id:attempt.attempt_id,
      expected_status:attempt.status,expected_event_sequence:attempt.event_sequence,expected_recovery_version:attempt.manual_stop_recovery_version,
      confirmed_stopped:true,reason:'已在执行机确认该任务及其子进程已结束'}};
    const device=attempt.target_resolution?.device_snapshot||{};
    text(panel,'[data-stop-target]',`${device.label||device.model||attempt.device_id} · ${state.batch.app_name||state.batch.app_id||'应用'} · ${attempt.target_resolution?.target_package||'所选应用'}`);
    panel.querySelector('input').checked=false;panel.querySelector('[data-stop-submit]').disabled=true;panel.hidden=false;panel.querySelector('input').focus();
  }
  function update(node,batch,actions={}) {
    if (!node || !batch?.ui_batch_id) return;
    let state=views.get(node);
    if(!state || state.batchId!==batch.ui_batch_id) state=init(node,batch.ui_batch_id);
    state.batch=batch;state.actions=actions;
    if(state.confirmation){const current=(batch.case_attempts||[]).find(a=>a.attempt_id===state.confirmation.payload.attempt_id);
      if(!recoveryCurrent(node,state)||!current?.manual_stop_recovery_eligible||recoveryIdentity(current)!==state.confirmation.signature)closeRecovery(node,state);
    }
    const attempts=Array.isArray(batch.case_attempts)?batch.case_attempts:[];
    const done=attempts.filter(settled).length;
    const passed=attempts.filter(a=>['passed','success'].includes(a.status)).length;
    const failed=attempts.filter(a=>['failed','timeout'].includes(a.status)).length;
    const snapshots=batch.snapshots || [];
    const appName=snapshots.find(s=>s.metadata?.app_name)?.metadata.app_name || batch.app_name || batch.app_id || '应用待确认';
    text(node,'[data-batch-title]',batch.suite_name || (snapshots.length===1 && snapshots[0].metadata?.task_name) || 'UI 测试');
    text(node,'[data-batch-app]',appName);
    text(node,'[data-batch-owner]',`执行者：${batch.owner_display_name || batch.owner_username || '账号信息待确认'}`);
    text(node,'[data-batch-state]',label(batch.status));
    node.querySelector('[data-batch-state]').className='ui-state-text-'+status(batch.status);
    text(node,'[data-batch-count]',`${done} / ${attempts.length} 项已结束`);
    text(node,'[data-batch-results]',`通过 ${passed} · 失败 ${failed} · 其他状态 ${attempts.length-passed-failed}`);
    const progress=node.querySelector('[data-batch-progress]');progress.max=Math.max(1,attempts.length);progress.value=done;
    text(node,'[data-batch-hint]',batch.message || (batch.stop_pending?'正在等待手机确认停止；确认前不会释放设备。':batch.status==='queued'?'等待所选手机接单；可取消本次测试。':batch.status==='cancelling'?'已请求取消，正在等待手机确认停止。':batch.status==='running'?'按手机真实上报更新步骤。':label(batch.status)));
    const reportLabels={not_requested:'普通测试，未申请发版报告；结果与截图已保留',pending:'发版报告等待生成',generating:'发版报告生成中',ready:'本次报告已就绪',failed:'报告生成失败，请查看报告详情'};
    const deliveryLabels={not_requested:'未启用飞书通知',pending:'飞书通知待发送',queued:'飞书通知已入队',retry_wait:'飞书通知等待重试',sending:'飞书通知发送中',sent:'飞书通知已发送',failed:'飞书通知发送失败',unknown:'飞书送达待核对'};
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
      const recoveryButton=row.querySelector('[data-attempt-recover]');
      recoveryButton.hidden=!a.manual_stop_recovery_eligible||!recoveryCurrent(node,state);
      recoveryButton.disabled=state.busy.has('recover');recoveryButton.onclick=()=>openRecovery(node,state,a,recoveryButton);
      const recoveryNote=row.querySelector('[data-attempt-recovery-note]');recoveryNote.hidden=!a.manual_stop_recovery;
      if(a.manual_stop_recovery)recoveryNote.textContent=`人工确认停止 · ${a.manual_stop_recovery.actor_display_name||a.manual_stop_recovery.actor_username||'操作人'} · ${new Date(a.manual_stop_recovery.confirmed_ns/1e6).toLocaleString()} · ${a.manual_stop_recovery.reason}。测试结果仍按中断或取消保留。`;
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
