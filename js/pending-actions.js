// Selection belongs to the loaded pending records, not to the latest polling render.
const selectedPendingActions = new Set();
let pendingActionsVisibleLimit = 8;
let pendingBatchBusy = false;
let pendingBatchResult = null;

function allPendingActions() {
  return buildPendingActions(latestJobs.map(normalizeJob));
}

function canSelectPendingAction(action) {
  return Boolean(action.draftId || action.jobId) && hasPermission('ui.edit');
}

function togglePendingAction(id, checked) {
  if (pendingBatchBusy) return;
  const action = allPendingActions().find(item => item.id === id);
  if (!action || !canSelectPendingAction(action)) return;
  if (checked) selectedPendingActions.add(id);
  else selectedPendingActions.delete(id);
  renderJobs();
}

function selectAllPendingActions(checked) {
  if (pendingBatchBusy) return;
  selectedPendingActions.clear();
  if (checked) allPendingActions().filter(canSelectPendingAction).forEach(item => selectedPendingActions.add(item.id));
  renderJobs();
}

function showMorePendingActions() {
  pendingActionsVisibleLimit += 8;
  renderJobs();
}

function pendingBatchToolbarHtml(actions) {
  const ids = new Set(actions.filter(canSelectPendingAction).map(item => item.id));
  if (!pendingBatchBusy) selectedPendingActions.forEach(id => { if (!ids.has(id)) selectedPendingActions.delete(id); });
  const selected = actions.filter(item => selectedPendingActions.has(item.id) && ids.has(item.id));
  const drafts = selected.filter(item => item.draftId).length;
  const jobs = selected.filter(item => item.jobId).length;
  const disabled = count => pendingBatchBusy || !count ? 'disabled' : '';
  return `<div class="pending-batch-toolbar">
    <label><input type="checkbox" aria-label="全选待处理" onchange="selectAllPendingActions(this.checked)" ${ids.size && selected.length === ids.size ? 'checked' : ''} ${disabled(ids.size)}> 全选待处理</label>
    <span>待处理 ${actions.length} 项 · 已选 ${selected.length} 项</span>
    <div class="job-actions">
      <button class="btn-sm success" ${disabled(drafts)} onclick="showPendingBatchDialog('apply')">批量确认替换</button>
      <button class="btn-sm danger" ${disabled(drafts)} onclick="showPendingBatchDialog('reject')">批量拒绝</button>
      <button class="btn-sm" ${disabled(jobs)} onclick="showPendingBatchDialog('handled')">批量标记已处理</button>
      <button class="btn-sm" ${disabled(selected.length)} onclick="selectAllPendingActions(false)">取消选择</button>
    </div>
    <small>全选包含下方尚未展开的待处理项；失败任务仅包含本次已加载的历史范围。替换只更新 YAML，不自动执行或同步 Sonic。</small>
    ${!hasPermission('ui.edit') ? '<small>当前账号无用例编辑权限，不能处理草稿或标记任务。</small>' : ''}
    ${pendingBatchResult ? `<div role="status">${escapeHtml(pendingBatchResult.title)}</div>` : ''}
  </div>`;
}

function pendingBatchRowHtml(row, result = false) {
  const detail = result
    ? `<br>${escapeHtml(row.detail || '')}`
    : `${row.riskHits?.length ? `<br>风险：${escapeHtml(row.riskHits.join('、'))}` : ''}${row.skip ? `<br>跳过：${escapeHtml(row.skip)}` : ''}`;
  return `<li><strong>${escapeHtml(result ? `${row.status} · ${row.title}` : row.title)}</strong><br>${escapeHtml(row.target)}${detail}</li>`;
}

function pendingBatchDetailsHtml(rows, {result = false} = {}) {
  const previewLimit = 10;
  const primary = rows.slice(0, previewLimit);
  const remaining = rows.slice(previewLimit);
  const skipGroups = new Map();
  if (!result) rows.filter(row => row.skip).forEach(row => skipGroups.set(row.skip, (skipGroups.get(row.skip) || 0) + 1));
  const skipSummary = skipGroups.size
    ? `<div class="pending-batch-skip-summary"><strong>跳过原因</strong>${Array.from(skipGroups.entries()).map(([reason, count]) => `<span>${escapeHtml(reason)} · ${count}</span>`).join('')}</div>`
    : '';
  return `${skipSummary}
    <ul class="pending-batch-details pending-batch-details-primary">${primary.map(row => pendingBatchRowHtml(row, result)).join('')}</ul>
    ${remaining.length ? `<details class="pending-batch-more"><summary>展开剩余 ${remaining.length} 项</summary><ul class="pending-batch-details">${remaining.map(row => pendingBatchRowHtml(row, result)).join('')}</ul></details>` : ''}`;
}

function showPendingBatchDialog(operation, actionIds = null) {
  if (pendingBatchBusy || !hasPermission('ui.edit') || !['apply', 'reject', 'handled'].includes(operation)) return;
  const ids = actionIds ? new Set(actionIds) : selectedPendingActions;
  const selected = allPendingActions().filter(item => ids.has(item.id) && canSelectPendingAction(item));
  if (!selected.length) return;
  const targets = new Map();
  if (operation === 'apply') selected.filter(item => item.draftId).forEach(item => {
    const draft = repairDraftById(item.draftId);
    const key = `${draft?.module}/${draft?.file}`;
    targets.set(key, (targets.get(key) || 0) + 1);
  });
  const rows = selected.map(item => {
    const draft = item.draftId ? repairDraftById(item.draftId) : null;
    const target = draft ? `${draft.module}/${draft.file}` : item.title;
    let skip = operation === 'handled' ? (item.jobId ? '' : '此操作仅适用于失败任务') : (draft ? '' : '此操作仅适用于修复草稿');
    if (!skip && operation === 'apply' && targets.get(target) > 1) skip = '同一 YAML 选中了多个草稿，请取消重复选择后逐一确认';
    if (!skip && operation === 'apply' && hasOpenEditor() && draft?.module === currentModule && draft?.file === currentFile
        && (editorDirty || document.getElementById('editor')?.value !== editorInitialContent)) {
      skip = '当前编辑器有未保存修改，请先保存并重新生成草稿，或撤销修改后再处理';
    }
    return {...item, target, module: draft?.module, file: draft?.file, riskHits: draft?.riskHits || [], skip};
  });
  const eligible = rows.filter(row => !row.skip);
  const title = {apply: '批量确认替换', reject: '批量拒绝', handled: '批量标记已处理'}[operation];
  document.getElementById('pending-batch-dialog')?.remove();
  const dialog = document.createElement('dialog');
  dialog.id = 'pending-batch-dialog';
  dialog.className = 'pending-batch-dialog';
  dialog.setAttribute('aria-label', title);
  dialog.innerHTML = `<h3>${title}</h3>
    <p>已选 ${rows.length} 项，本次处理 ${eligible.length} 项，跳过 ${rows.length - eligible.length} 项。</p>
    <p>${operation === 'apply' ? '请检查下面的文件和风险。系统逐项校验、核对原版本并备份，再替换正式 YAML；不会自动执行。' : operation === 'reject' ? '拒绝后草稿将退出待处理列表，不修改正式 YAML。' : '标记后失败任务退出待处理列表，不改变原执行结果，也不自动重跑。'}</p>
    ${pendingBatchDetailsHtml(rows)}
    ${operation === 'apply' ? '<label><input id="pending-batch-ack" type="checkbox"> 已逐项检查所选草稿及风险，确认替换并备份</label>' : ''}
    ${operation === 'reject' ? '<label>拒绝原因（可选）<textarea id="pending-batch-reason" rows="2" maxlength="1000"></textarea></label>' : ''}
    <div class="job-actions"><button class="btn-sm" data-cancel>取消</button><button class="btn-sm success" data-confirm ${!eligible.length || operation === 'apply' ? 'disabled' : ''}>确认处理</button></div>`;
  document.body.appendChild(dialog);
  const confirmButton = dialog.querySelector('[data-confirm]');
  dialog.querySelector('#pending-batch-ack')?.addEventListener('change', event => { confirmButton.disabled = !eligible.length || !event.target.checked; });
  dialog.querySelector('[data-cancel]').onclick = () => dialog.close();
  dialog.addEventListener('cancel', event => { if (pendingBatchBusy) event.preventDefault(); });
  dialog.addEventListener('close', () => dialog.remove());
  confirmButton.onclick = () => executePendingBatch(operation, rows, dialog);
  dialog.showModal();
}

async function executePendingBatch(operation, rows, dialog) {
  if (pendingBatchBusy || !hasPermission('ui.edit')) return;
  pendingBatchBusy = true;
  const reason = dialog.querySelector('#pending-batch-reason')?.value.trim() || '';
  dialog.querySelectorAll('button, input, textarea').forEach(node => { node.disabled = true; });
  const progress = document.createElement('p');
  progress.setAttribute('role', 'status');
  dialog.appendChild(progress);
  const results = [];
  renderJobs();
  try {
    for (const row of rows) {
      progress.textContent = `正在处理 ${results.length + 1}/${rows.length}：${row.title}`;
      if (row.skip) { results.push({...row, status: '跳过', detail: row.skip}); continue; }
      try {
        if (!hasPermission('ui.edit')) throw new Error('权限已变化，请刷新后联系管理员');
        const data = await apiRequest(row.draftId ? `/repair-drafts/${operation}` : `/jobs/${encodeURIComponent(row.jobId)}/review`, {
          method: 'POST',
          body: JSON.stringify(row.draftId ? {draftId: row.draftId, confirmApply: operation === 'apply', confirmRisk: operation === 'apply', reason} : {
            category: 'unknown', reason: '已人工确认，无需继续处理', suggested_action: 'manual_done',
          }),
        });
        if (data.ok === false) throw new Error(data.error || '服务端未完成处理');
        if (row.draftId) {
          const expected = operation === 'apply' ? 'APPLIED' : 'REJECTED';
          if (data.draft?.status !== expected) throw new Error('未收到草稿最终状态，请刷新核实，勿重复提交');
          await upsertRepairDraft(data.draft, {persist: false});
          if (operation === 'apply' && row.module === currentModule && row.file === currentFile && hasOpenEditor()) {
            const content = data.draft.fixedYaml || data.draft.fixed_yaml;
            if (typeof content !== 'string' || !content.trim()) {
              // Never leave a stale editor able to overwrite a successful apply.
              resetYamlToolbarForManager();
              showWorkflowGuide('yaml_edit');
              throw new Error('替换已完成，但缺少最新内容；已关闭旧编辑器，请重新打开文件核实，勿重复提交');
            }
            showEditor(content);
            updateToolbarState();
          }
        } else if (data.job?.job_id !== row.jobId || !jobReviewConfirmed(data.job)) {
          throw new Error('未收到任务已处理状态，请刷新核实，勿重复提交');
        }
        selectedPendingActions.delete(row.id);
        results.push({...row, status: '成功', detail: operation === 'apply' ? '已备份并替换 YAML' : operation === 'reject' ? '已拒绝草稿' : '已标记处理'});
      } catch (error) {
        results.push({...row, status: '失败', detail: error.message || '处理失败，请刷新核实'});
      }
    }
  } finally {
    pendingBatchBusy = false;
    const title = ['成功', '失败', '跳过'].map(status => `${status} ${results.filter(row => row.status === status).length}`).join(' · ');
    pendingBatchResult = {title, results};
    dialog.innerHTML = `<h3>批量处理结果</h3><p role="status">${escapeHtml(title)}</p>
      <p>成功项已取消勾选；失败及跳过项保留选择。请按具体原因处理后再重试。</p>
      ${pendingBatchDetailsHtml(results, {result: true})}
      <button class="btn-sm" onclick="document.getElementById('pending-batch-dialog').close()">关闭</button>`;
    renderJobs();
    await loadJobs(false, true);
  }
}
