// execution.js
// Extracted from task-manager.html (no logic changes).

// ===== FILE OPS =====
function setFileContextVisible(visible) {
  document.querySelectorAll('.action-group.file-only').forEach(group => {
    group.dataset.fileContext = visible ? '1' : '0';
  });
  updateWorkflowActionGroups();
}

function emptyEditorHtml() {
  return workflowGuideHtml(activeWorkflow);
}

/**
 * Reset editor-area to the current workflow's guide view.
 * For dashboard/agent workflows, uses showAgentWorkbench() so the form is always visible.
 * Returns true if the workbench handled the render, false if caller should use emptyEditorHtml().
 */
function resetEditorToWorkflowGuide() {
  if (activeWorkflow === 'dashboard' || activeWorkflow === 'agent') {
    showAgentWorkbench();
    return true;
  }
  const area = document.getElementById('editor-area');
  area.className = 'editor-area';
  area.innerHTML = emptyEditorHtml();
  return false;
}

// Round 5: 执行页 4 个子 tab (调试执行 / 同步至 Sonic 平台 / 失败重跑 / Runner状态)
let executionActiveTab = 'debug';
let debugTraceData = null;
let debugSnapshotData = null;
let selectedTraceSnapshots = [];

function setExecutionTab(tab) {
  executionActiveTab = tab;
  if (tab === 'trace') {
    showExecutionCenter();
    loadDebugTraces(true).then(() => loadDebugSnapshots(true)).then(() => showExecutionCenter()).catch(() => showExecutionCenter());
    return;
  }
  showExecutionCenter();
}

function showExecutionCenter() {
  const area = document.getElementById('editor-area');
  if (!area) return;
  activeWorkspaceMode = 'execution';
  area.className = 'editor-area';
  area.innerHTML = renderExecutionCenter();
  const path = document.getElementById('toolbar-path');
  if (path) path.innerHTML = '<span>▶</span> 执行中心';
  const help = document.getElementById('toolbar-help');
  if (help) help.textContent = '调试执行 / 同步至 Sonic 平台 / 失败重跑 / Runner 状态。';
  const info = document.getElementById('file-info');
  if (info) info.textContent = '执行中心';
  if (typeof updateToolbarState === 'function') updateToolbarState('执行中心');
}

function renderExecutionCenter() {
  const tabs = [
    ['debug', '调试执行'],
    ['sonic', '同步至 Sonic 平台'],
    ['rerun', '失败重跑'],
    ['runners', 'Runner 状态'],
    ['trace', 'Trace 回放']
  ];
  const tabsHtml = tabs.map(([key, label]) => `
    <button class="agent-tab ${executionActiveTab === key ? 'active' : ''}" onclick="setExecutionTab('${key}')">${label}</button>
  `).join('');
  let body = '';
  if (executionActiveTab === 'debug') body = renderExecutionTabDebug();
  else if (executionActiveTab === 'sonic') body = renderExecutionTabSonic();
  else if (executionActiveTab === 'rerun') body = renderExecutionTabRerun();
  else if (executionActiveTab === 'runners') body = renderExecutionTabRunners();
  else if (executionActiveTab === 'trace') body = renderExecutionTabTrace();
  return `
    <div class="review-page execution-page">
      <div class="review-head">
        <div>
          <div class="workflow-kicker">EXECUTE · 单条调试 / 整文件回归 / Sonic 套件</div>
          <h2>调试执行中心</h2>
          <p>单条/整文件由 Windows/Mac Runner 执行；Sonic 只负责已同步基线的测试套回归；Trace 用于回放、对比和定位链路问题。</p>
        </div>
        <div class="review-actions">
          <button class="btn-sm primary" onclick="loadJobs(true).then(()=>showExecutionCenter())">刷新任务</button>
          <button class="btn-sm" onclick="loadRunnerDevices && loadRunnerDevices({force:true}).then(()=>showExecutionCenter())">刷新 Runner</button>
          <button class="btn-sm" onclick="loadDebugTraces(true).then(()=>showExecutionCenter())">刷新 Trace</button>
        </div>
      </div>
      <div class="agent-tabs execution-tabs">${tabsHtml}</div>
      <div class="execution-tab-body">${body}</div>
    </div>
  `;
}

function executionYamlRows() {
  const selectedModule = currentModule && modules[currentModule] ? currentModule : '';
  const search = (document.getElementById('execution-yaml-search')?.value || '').trim().toLowerCase();
  let rows = [];
  Object.keys(modules || {}).sort((a, b) => a.localeCompare(b, 'zh-CN')).forEach(mod => {
    if (selectedModule && mod !== selectedModule) return;
    (modules[mod] || []).forEach(file => {
      if (!/\.ya?ml$/i.test(file || '')) return;
      const haystack = `${mod}/${file}`.toLowerCase();
      if (search && !haystack.includes(search)) return;
      rows.push({ mod, file });
    });
  });
  return rows;
}

function executionModuleOptionsHtml() {
  const selectedModule = currentModule && modules[currentModule] ? currentModule : '';
  const options = Object.keys(modules || {}).sort((a, b) => a.localeCompare(b, 'zh-CN'))
    .map(mod => `<option value="${escapeHtml(mod)}" ${mod === selectedModule ? 'selected' : ''}>${escapeHtml(mod)}（${(modules[mod] || []).length}）</option>`)
    .join('');
  return `<option value="">全部模块</option>${options}`;
}

function selectExecutionModule(value) {
  currentModule = value || null;
  currentFile = currentModule && modules[currentModule]?.includes(currentFile) ? currentFile : null;
  renderModules();
  showExecutionCenter();
}

function refreshExecutionYamlList() {
  showExecutionCenter();
}

function renderExecutionTabDebug() {
  const rows = executionYamlRows();
  const currentLabel = currentModule && currentFile ? `${currentModule} / ${currentFile}` : '未选择';
  const runnerCount = Array.isArray(runnerDevices) ? runnerDevices.length : 0;
  return `
    <div class="execution-debug-board">
      <div class="execution-debug-toolbar">
        <div>
          <h3>选择要调试的 YAML</h3>
          <p>当前选择：<strong>${escapeHtml(currentLabel)}</strong>。单条调试会先打开 YAML 并选择 task，整文件执行才会跑完整文件。</p>
        </div>
        <div class="execution-runner-summary">
          <strong>${runnerCount}</strong>
          <span>在线 Runner 设备</span>
        </div>
      </div>
      <div class="execution-filters">
        <select id="execution-module-select" onchange="selectExecutionModule(this.value)">
          ${executionModuleOptionsHtml()}
        </select>
        <input id="execution-yaml-search" value="${escapeHtml(document.getElementById('execution-yaml-search')?.value || '')}" placeholder="搜索 YAML 或模块..." onkeydown="if(event.key==='Enter') refreshExecutionYamlList()">
        <button class="btn-sm" onclick="refreshExecutionYamlList()">筛选</button>
        <button class="btn-sm" onclick="loadModules().then(()=>showExecutionCenter())">刷新 YAML</button>
        <button class="btn-sm" onclick="loadRunnerDevices && loadRunnerDevices({force:true}).then(()=>showExecutionCenter())">刷新 Runner</button>
      </div>
      ${rows.length ? `
        <div class="execution-yaml-list">
          <table class="assets-table execution-yaml-table">
            <thead><tr><th>YAML 文件</th><th>模块</th><th>用例</th><th>Sonic</th><th>操作</th></tr></thead>
            <tbody>
              ${rows.map(row => {
                const stats = typeof yamlStatsForFile === 'function' ? yamlStatsForFile(row.mod, row.file) : {};
                const sonic = typeof sonicFileSummary === 'function' ? sonicFileSummary(row.mod, row.file) : { cls: '', text: '未知', title: '' };
                const active = currentModule === row.mod && currentFile === row.file;
                return `
                  <tr class="${active ? 'active' : ''}">
                    <td>
                      <button class="asset-file-link" onclick="openFile(${jsArg(row.mod)}, ${jsArg(row.file)})">${escapeHtml(yamlDisplayName(row.file))}</button>
                      <div class="asset-file-path">${escapeHtml(row.file)}</div>
                    </td>
                    <td>${escapeHtml(row.mod)}</td>
                    <td>${typeof prioritySummaryHtml === 'function' ? prioritySummaryHtml(stats, true) : '-'}</td>
                    <td><span class="task-ext sonic ${escapeHtml(sonic.cls)}" title="${escapeHtml(sonic.title || '')}">${escapeHtml(sonic.text || '-')}</span></td>
                    <td class="asset-row-actions">
                      <button class="btn-sm" onclick="openFile(${jsArg(row.mod)}, ${jsArg(row.file)})">打开</button>
                      <button class="btn-sm primary" onclick="openFile(${jsArg(row.mod)}, ${jsArg(row.file)}).then(()=>showRunSelectedTask())">单条调试</button>
                      <button class="btn-sm success" onclick="openFile(${jsArg(row.mod)}, ${jsArg(row.file)}).then(()=>showRunCurrentFile())">整文件执行</button>
                    </td>
                  </tr>
                `;
              }).join('')}
            </tbody>
          </table>
        </div>
      ` : `<div class="job-empty">没有可调试的 YAML。请先在“用例资产”或“AI 生成用例”里创建用例。</div>`}
    </div>
  `;
}

function renderExecutionTabSonic() {
  const jobs = (Array.isArray(latestJobs) ? latestJobs : []).filter(j => j.module || j.file).slice(0, 60);
  return `
    <div class="review-panel">
      <div class="section-head">
        <div>
          <h3>同步至 Sonic 平台</h3>
          <p>把 YAML 用例同步至 Sonic 平台；可单条或批量同步。</p>
        </div>
        <div class="review-actions">
          <button class="btn-sm" onclick="document.getElementById('btn-sonic-status')?.click()" ${currentFile ? '' : 'disabled title="请先打开 YAML 文件"'}>查看同步状态</button>
          <button class="btn-sm primary" onclick="document.getElementById('btn-publish-sonic')?.click()" ${currentFile ? '' : 'disabled title="请先打开 YAML 文件"'}>同步当前文件</button>
        </div>
      </div>
      ${currentFile ? `<div class="generate-hint">当前文件：${escapeHtml(currentModule)}/${escapeHtml(currentFile)}</div>` : `<div class="generate-hint">从左侧用例树打开 YAML 后再进行 Sonic 操作。</div>`}
      <h3 style="margin-top:12px;">最近任务</h3>
      ${jobs.length ? `<table class="report-table">
        <thead><tr><th>任务</th><th>模块</th><th>状态</th><th>时间</th></tr></thead>
        <tbody>${jobs.map(j => `
          <tr>
            <td>${escapeHtml(j.target_task_name || j.file || j.job_id || '-')}</td>
            <td>${escapeHtml(j.module || '-')}</td>
            <td><span class="status-pill ${String(j.status || '').toLowerCase() === 'success' ? 'success' : (['failed','timeout','cancelled'].includes(String(j.status || '').toLowerCase()) ? 'warn' : '')}">${escapeHtml(jobStatusText(j.status || ''))}</span></td>
            <td class="report-cell-time">${escapeHtml((j.finished_at || j.updated_at || '').replace('T',' ').slice(0,19))}</td>
          </tr>
        `).join('')}</tbody>
      </table>` : `${renderEmptyState('reports')}`}
    </div>
  `;
}

function renderExecutionTabRerun() {
  const failed = (Array.isArray(latestJobs) ? latestJobs : []).filter(j => {
    const s = String(j.status || '').toLowerCase();
    return ['failed', 'timeout', 'cancelled', 'error'].includes(s);
  }).slice(0, 80);
  return `
    <div class="review-panel">
      <div class="section-head">
        <div>
          <h3>失败重跑</h3>
          <p>选择失败任务进行重跑或 AI 分析；高风险用例需人工确认后才能继续。</p>
        </div>
      </div>
      ${failed.length ? `<table class="report-table">
        <thead><tr><th>任务</th><th>模块</th><th>失败类型</th><th>时间</th><th>操作</th></tr></thead>
        <tbody>${failed.map(j => {
          const jobId = j.job_id || j.jobId || '';
          const ft = (typeof reportsFailureType === 'function') ? reportsFailureType(j) : '';
          return `
            <tr class="report-row failed">
              <td>${escapeHtml(j.target_task_name || j.file || jobId)}</td>
              <td>${escapeHtml(j.module || '-')}</td>
              <td>${ft ? `<span class="failure-type-chip failure-${ft.toLowerCase()}">${escapeHtml(ft)}</span>` : '<span class="report-muted">—</span>'}</td>
              <td class="report-cell-time">${escapeHtml((j.finished_at || j.updated_at || '').replace('T',' ').slice(0,19))}</td>
              <td class="report-cell-actions">
                <button class="btn-sm" onclick="analyzeFailureFromJob(${jsArg(jobId)}, {renderPage:true})">AI 分析</button>
                <button class="btn-sm primary" onclick="openAiRepairForJob(${jsArg(jobId)})">去 AI 修复</button>
                <button class="btn-sm" onclick="focusJob(${jsArg(jobId)})">定位</button>
              </td>
            </tr>
          `;
        }).join('')}</tbody>
      </table>` : `${renderEmptyState('failure_analysis')}`}
    </div>
  `;
}

function renderExecutionTabRunners() {
  const devices = Array.isArray(runnerDevices) ? runnerDevices : [];
  const all = devices;
  const online = devices.filter(d => d.runner_online && (d.status === 'online'));
  return `
    <div class="review-panel">
      <div class="section-head">
        <div>
          <h3>Runner 状态</h3>
          <p>在线 Runner / 设备状态 / 最近心跳。</p>
        </div>
        <div class="review-actions">
          <button class="btn-sm" onclick="loadRunnerDevices && loadRunnerDevices({force:true}).then(()=>showExecutionCenter())">刷新</button>
        </div>
      </div>
      <div class="review-stats" style="grid-template-columns:repeat(3,1fr);">
        <div class="review-stat"><strong>${online.length}</strong><span>在线 Runner</span></div>
        <div class="review-stat"><strong>${all.length}</strong><span>已登记设备</span></div>
        <div class="review-stat"><strong>${all.length - online.length}</strong><span>离线/未上报</span></div>
      </div>
      ${all.length ? `<table class="report-table" style="margin-top:12px;">
        <thead><tr><th>设备</th><th>Runner</th><th>状态</th><th>最近心跳</th></tr></thead>
        <tbody>${all.map(d => {
          const isOnline = d.runner_online && d.status === 'online';
          const hb = d.last_heartbeat || d.heartbeat_at || d.updated_at || '';
          return `
            <tr class="report-row ${isOnline ? 'success' : ''}">
              <td>${escapeHtml(d.label || d.device_id || '-')}</td>
              <td>${escapeHtml(d.runner_id || '-')}</td>
              <td><span class="status-pill ${isOnline ? 'success' : 'warn'}">${isOnline ? '在线' : (d.status || '离线')}</span></td>
              <td class="report-cell-time">${escapeHtml(String(hb).replace('T',' ').slice(0,19) || '-')}</td>
            </tr>
          `;
        }).join('')}</tbody>
      </table>` : `${renderEmptyState('reports', '暂无在线 Runner，请在系统设置页面运行预检脚本。')}`}
    </div>
  `;
}

async function loadDebugTraces(force = false) {
  if (debugTraceData && !force) return debugTraceData;
  debugTraceData = await apiRequest('/debug/traces?limit=40');
  return debugTraceData;
}

async function loadDebugSnapshots(force = false) {
  if (debugSnapshotData && !force) return debugSnapshotData;
  debugSnapshotData = await apiRequest('/debug/snapshots?limit=40');
  return debugSnapshotData;
}

function renderExecutionTabTrace() {
  const traces = (debugTraceData && Array.isArray(debugTraceData.traces)) ? debugTraceData.traces : [];
  const snapshots = (debugSnapshotData && Array.isArray(debugSnapshotData.snapshots)) ? debugSnapshotData.snapshots : [];
  return `
    <div class="review-panel">
      <div class="section-head">
        <div>
          <h3>Trace 回放与 Diff</h3>
          <p>基于真实Agent 运行、Runner 任务和 DAG Span 生成链路视图。可保存快照、回放计划、对比两次执行差异。</p>
        </div>
        <div class="review-actions">
          <button class="btn-sm primary" onclick="loadDebugTraces(true).then(()=>loadDebugSnapshots(true)).then(()=>showExecutionCenter())">刷新 Trace</button>
          <button class="btn-sm" onclick="window.open('/trace-viewer.html', '_blank')">打开 Viewer</button>
        </div>
      </div>
      <div class="review-stats" style="grid-template-columns:repeat(4,1fr);">
        <div class="review-stat"><strong>${traces.length}</strong><span>Trace</span></div>
        <div class="review-stat"><strong>${snapshots.length}</strong><span>快照</span></div>
        <div class="review-stat"><strong>${traces.filter(t => t.status === 'failed').length}</strong><span>失败链路</span></div>
        <div class="review-stat"><strong>${selectedTraceSnapshots.length}</strong><span>已选快照</span></div>
      </div>
      ${traces.length ? `<table class="report-table" style="margin-top:12px;">
        <thead><tr><th>Trace</th><th>来源</th><th>状态</th><th>节点</th><th>更新时间</th><th>操作</th></tr></thead>
        <tbody>${traces.map(trace => `
          <tr>
            <td>
              <strong>${escapeHtml(trace.title || trace.traceId || '-')}</strong>
              <div class="report-muted">${escapeHtml(trace.traceId || trace.id || '')}</div>
            </td>
            <td>${escapeHtml(trace.sourceType || '-')}</td>
            <td><span class="status-pill ${trace.status === 'success' ? 'success' : (trace.status === 'failed' ? 'warn' : '')}">${escapeHtml(trace.status || '-')}</span></td>
            <td>${escapeHtml(String((trace.summary && trace.summary.totalNodes) || 0))}</td>
            <td class="report-cell-time">${escapeHtml(String(trace.updatedAt || '').replace('T',' ').slice(0,19) || '-')}</td>
            <td class="report-cell-actions">
              <button class="btn-sm" onclick="openTraceViewer(${jsArg(trace.traceId || trace.id)})">查看</button>
              <button class="btn-sm primary" onclick="saveDebugSnapshot(${jsArg(trace.traceId || trace.id)})">保存快照</button>
            </td>
          </tr>
        `).join('')}</tbody>
      </table>` : `${renderEmptyState('reports', '暂无 Trace 数据。先执行Agent或 Runner 任务后再回来刷新。')}`}
      <h3 style="margin-top:16px;">执行快照</h3>
      ${snapshots.length ? `<table class="report-table" style="margin-top:12px;">
        <thead><tr><th>选择</th><th>快照</th><th>来源</th><th>创建时间</th><th>操作</th></tr></thead>
        <tbody>${snapshots.map(snapshot => {
          const id = snapshot.snapshotId || snapshot.id || '';
          const checked = selectedTraceSnapshots.includes(id) ? 'checked' : '';
          return `
            <tr>
              <td><input type="checkbox" ${checked} onchange="toggleTraceSnapshot(${jsArg(id)}, this.checked)"></td>
              <td><strong>${escapeHtml(id)}</strong></td>
              <td>${escapeHtml(snapshot.sourceId || '-')}</td>
              <td class="report-cell-time">${escapeHtml(snapshot.createdAt || '-')}</td>
              <td class="report-cell-actions">
                <button class="btn-sm" onclick="replayDebugSnapshot(${jsArg(id)}, true)">回放计划</button>
                <button class="btn-sm" onclick="openTraceViewer(${jsArg(snapshot.sourceId || '')})">查看 Trace</button>
              </td>
            </tr>
          `;
        }).join('')}</tbody>
      </table>
      <div class="review-actions" style="margin-top:10px;">
        <button class="btn-sm primary" onclick="diffSelectedTraceSnapshots()" ${selectedTraceSnapshots.length === 2 ? '' : 'disabled'}>对比已选 2 个快照</button>
        <button class="btn-sm" onclick="selectedTraceSnapshots=[]; showExecutionCenter()">清空选择</button>
      </div>` : `${renderEmptyState('reports', '暂无快照。点击上方 Trace 的“保存快照”后可用于回放和 Diff。')}`}
    </div>
  `;
}

function openTraceViewer(traceId) {
  const suffix = traceId ? `?id=${encodeURIComponent(traceId)}` : '';
  window.open(`/trace-viewer.html${suffix}`, '_blank');
}

async function saveDebugSnapshot(traceId) {
  try {
    const data = await apiRequest('/debug/snapshots', {method: 'POST', body: {traceId}});
    if (!data.ok) throw new Error(data.error || '保存快照失败');
    showToast('快照已保存，可用于回放和 Diff', 'success');
    await loadDebugSnapshots(true);
    showExecutionCenter();
  } catch (e) {
    showToast(e.message || '保存快照失败', 'error');
  }
}

function toggleTraceSnapshot(snapshotId, checked) {
  snapshotId = String(snapshotId || '');
  if (!snapshotId) return;
  selectedTraceSnapshots = selectedTraceSnapshots.filter(id => id !== snapshotId);
  if (checked) selectedTraceSnapshots.push(snapshotId);
  selectedTraceSnapshots = selectedTraceSnapshots.slice(-2);
  showExecutionCenter();
}

async function replayDebugSnapshot(snapshotId, dryRun = true) {
  try {
    const data = await apiRequest('/debug/replay', {method: 'POST', body: {snapshotId, dryRun}});
    if (!data.ok) throw new Error(data.error || '回放失败');
    const message = dryRun ? '已生成回放计划' : '已创建 Runner 回放任务';
    showToast(message, 'success');
    if (data.plan) console.log('Replay plan', data.plan);
  } catch (e) {
    showToast(e.message || '回放失败', 'error');
  }
}

async function diffSelectedTraceSnapshots() {
  if (selectedTraceSnapshots.length !== 2) {
    showToast('请选择 2 个快照再对比', 'warning');
    return;
  }
  try {
    const data = await apiRequest('/debug/diff', {method: 'POST', body: {a: selectedTraceSnapshots[0], b: selectedTraceSnapshots[1]}});
    if (!data.ok) throw new Error(data.error || 'Diff 失败');
    const summary = data.summary || {};
    showToast(`Diff 完成：新增 ${summary.added || 0}，移除 ${summary.removed || 0}，变化 ${summary.changed || 0}`, 'success');
    console.log('Execution diff', data);
  } catch (e) {
    showToast(e.message || 'Diff 失败', 'error');
  }
}

async function openFile(mod, file) {
  if (!canLeaveEditor()) return;
  activeWorkspaceMode = '';
  currentModule = mod;
  currentFile = file;
  sonicStatusData = null;
  if (!['assets', 'generate', 'yaml_edit', 'execute', 'repair', 'baseline'].includes(activeWorkflow)) setActiveWorkflow('generate');
  document.getElementById('toolbar-path').innerHTML = `<span>📁</span> ${mod} / <span>${file}</span>`;
  document.getElementById('toolbar-help').textContent = '编辑后先保存；整文件用于回归，单条用例用于调试，AI 修复优先处理当前光标所在用例。';
  setFileContextVisible(true);
  document.getElementById('btn-save').style.display = 'flex';
  document.getElementById('btn-copy-file').style.display = 'flex';
  document.getElementById('btn-move-file').style.display = 'flex';
  document.getElementById('btn-rename-file').style.display = 'flex';
  document.getElementById('btn-history-file').style.display = 'flex';
  document.getElementById('btn-baseline-refs').style.display = 'flex';
  document.getElementById('btn-generation-review').style.display = fileMeta(mod, file).last_case_set_id ? 'flex' : 'none';
  document.getElementById('btn-sonic-status').style.display = 'flex';
  document.getElementById('btn-publish-sonic').style.display = 'flex';
  const statusSelect = document.getElementById('file-status-select');
  statusSelect.style.display = 'block';
  statusSelect.value = fileMeta(mod, file).status || 'draft';
  document.getElementById('btn-run-file').style.display = 'flex';
  document.getElementById('btn-run-task').style.display = 'flex';
  document.getElementById('btn-repair-task').style.display = 'flex';
  document.getElementById('btn-repair-file').style.display = 'flex';
  document.getElementById('toggle-refs-panel').style.display = 'flex';
  document.getElementById('toggle-case-panel').style.display = 'flex';

  try {
    const content = await apiTextRequest(`/file?module=${encodeURIComponent(mod)}&file=${encodeURIComponent(file)}`);
    if (/^\s*</.test(content) || !content.includes('tasks:')) {
      throw new Error('服务器返回内容不是有效 YAML');
    }
    showEditor(content);
  } catch(e) {
    const demo = `android:\n  deviceId: UQG0220513008845\n\ntasks:\n  - name: ${yamlDisplayName(file)}\n    flow:\n      - sleep: 1000\n      - ai: 首页有弹窗就点击右上角关闭按钮，没有就跳过\n`;
    showEditor(demo);
    showToast(e.message || '读取 YAML 失败，已加载兜底模板', 'error');
  }
  renderModules();
  document.getElementById('file-info').textContent = `${mod}/${file}`;
  updateToolbarState();
}

function showEditor(content) {
  editorInitialContent = content || '';
  editorDirty = false;
  const area = document.getElementById('editor-area');
  area.className = `editor-area editor-open${isPanelCollapsed('refs') ? ' refs-collapsed' : ''}`;
  area.innerHTML = `
    <div class="baseline-preview" id="baseline-preview">
      <div class="baseline-preview-head">
        <div>
          <div class="baseline-preview-title">基线参考截图</div>
          <div class="baseline-preview-sub" id="baseline-preview-sub">正在读取当前用例关联...</div>
        </div>
        <div class="baseline-preview-actions">
          <button class="btn-sm" onclick="toggleRefsPanel()">隐藏</button>
          <button class="btn-sm" onclick="refreshBaselinePreview(true)">刷新</button>
          <button class="btn-sm" onclick="showBaselineRefsForCurrentTask()">从页面知识库添加</button>
        </div>
      </div>
      <div class="baseline-preview-list" id="baseline-preview-list">
        <div class="generate-knowledge-empty">正在加载...</div>
      </div>
      <div class="sonic-preview">
        <div class="sonic-preview-head">
          <div>
            <div class="sonic-preview-title">同步至 Sonic 平台</div>
            <div class="baseline-preview-sub" id="sonic-preview-sub">正在读取同步状态...</div>
          </div>
          <div class="sonic-preview-actions">
            <button class="btn-sm" onclick="scanLegacySonicCases('all')" title="扫描全部应用的 Sonic 旧模板和重复步骤，避免旧脚本绕过汇总通知">套件体检</button>
            <button class="btn-sm" onclick="refreshSonicPreview(true)">刷新</button>
          </div>
        </div>
        <div class="sonic-preview-list" id="sonic-preview-list">
          <div class="generate-knowledge-empty">正在加载...</div>
        </div>
      </div>
    </div>
    <div class="layout-resizer refs-resizer" data-resize="refs" role="separator" aria-label="调整基线参考截图宽度"></div>
    <div class="editor-wrap${isPanelCollapsed('caseNav') ? ' case-nav-collapsed' : ''}">
      <div class="editor-context-bar" id="editor-context-bar"></div>
      <aside class="yaml-task-nav" id="yaml-task-nav">
        <div class="yaml-task-nav-head">
          <div class="yaml-task-nav-title">当前用例</div>
          <div style="display:flex;align-items:center;gap:6px;">
            <div class="yaml-task-nav-count" id="yaml-task-nav-count">0</div>
            <select class="task-nav-bulk-select" title="批量修改当前 YAML 全部用例等级" onchange="changeAllTaskPriorities(this.value); this.value=''">
              <option value="">批量等级</option>
              <option value="P0">全部 P0</option>
              <option value="P1">全部 P1</option>
              <option value="P2">全部 P2</option>
              <option value="P3">全部 P3</option>
            </select>
            <button class="panel-mini-action" onclick="toggleCaseNavPanel()">隐藏</button>
          </div>
        </div>
        <div class="yaml-task-nav-list" id="yaml-task-nav-list"></div>
      </aside>
      <div class="layout-resizer case-nav-resizer" data-resize="caseNav" role="separator" aria-label="调整当前用例列表宽度"></div>
      <div class="line-nums" id="line-nums"></div>
      <textarea class="editor" id="editor" spellcheck="false" wrap="off" oninput="updateLines();markEditorDirty()">${escHtml(content)}</textarea>
    </div>
  `;
  updateLines();
  const editor = document.getElementById('editor');
  editor.addEventListener('keydown', handleTab);
  ['keyup', 'click', 'input'].forEach(eventName => editor.addEventListener(eventName, () => {
    scheduleBaselinePreviewRefresh();
    renderYamlTaskNav();
  }));
  refreshBaselinePreview();
  refreshSonicPreview();
  renderYamlTaskNav();
  renderEditorContextBar();
  applyEditorPanelState();
  updateToolbarState();
}

function markEditorDirty() {
  const editor = document.getElementById('editor');
  editorDirty = !!editor && editor.value !== editorInitialContent;
  updateToolbarState();
}

function canLeaveEditor() {
  if (!editorDirty || !hasOpenEditor()) return true;
  return confirm('当前 YAML 有未保存修改，切换后会丢失这些修改。确认继续？');
}

async function saveEditorBeforeNavigation(successMessage = '已自动保存当前 YAML，并切换到目录页') {
  if (!editorDirty || !hasOpenEditor()) return true;
  try {
    const saved = await saveFile({showSuccess:false});
    if (!saved) throw new Error('当前文件为空或未打开，无法自动保存');
    showToast(successMessage, 'success');
    return true;
  } catch (e) {
    showToast(`自动保存失败，已停留在当前编辑页：${e.message || e}`, 'error');
    return false;
  }
}

function escHtml(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function updateLines() {
  const ta = document.getElementById('editor');
  if (!ta) return;
  const lines = ta.value.split('\n').length;
  document.getElementById('line-nums').innerHTML = Array.from({length:lines},(_,i)=>i+1).join('<br>');
}

function renderYamlTaskNav() {
  const ta = document.getElementById('editor');
  const list = document.getElementById('yaml-task-nav-list');
  const count = document.getElementById('yaml-task-nav-count');
  if (!ta || !list || !count) return;
  const tasks = parseYamlTasks(ta.value);
  const selected = detectSelectedTaskName();
  const p0 = tasks.filter(task => task.priority === 'P0').length;
  const p1 = tasks.filter(task => task.priority === 'P1').length;
  const smoke = tasks.filter(task => task.smoke).length;
  const hot = [p0 ? `P0 ${p0}` : '', p1 ? `P1 ${p1}` : '', smoke ? `冒烟 ${smoke}` : ''].filter(Boolean).join(' · ');
  count.textContent = hot ? `${tasks.length} 条 · ${hot}` : (tasks.length <= 1 ? '单用例' : `${tasks.length} 条`);
  if (!tasks.length) {
    list.innerHTML = '<div class="job-empty">没有解析到 tasks[].name</div>';
    return;
  }
  if (tasks.length === 1) {
    const task = tasks[0];
    const priority = task.priority || 'P2';
    const job = jobForTaskName(task.name) || {};
    const status = job.status ? ` · ${jobStatusText(job.status)}` : '';
    list.innerHTML = `
      <div class="job-empty">
        <strong>${escapeHtml(task.name)}</strong><br>
        顶部上下文条可直接修改 ${escapeHtml(priority)}、执行和修复${escapeHtml(status)}。
      </div>
    `;
    updateToolbarState();
    renderEditorContextBar();
    return;
  }
  list.innerHTML = tasks.map((task, index) => {
    const job = latestJobs.find(item => item.module === currentModule && item.file === currentFile && item.target_task_name === task.name) || {};
    const status = job.status ? ` · ${jobStatusText(job.status)}` : '';
    const priority = task.priority || 'P2';
    const priorityHtml = `
      <select class="priority-select ${escapeHtml(priority.toLowerCase())}" title="修改用例等级" onclick="event.stopPropagation()" onchange="changeTaskPriority(${index}, this.value)">
        ${['P0','P1','P2','P3'].map(p => `<option value="${p}" ${p === priority ? 'selected' : ''}>${p}</option>`).join('')}
      </select>
    `;
    const smokeHtml = task.smoke ? '<span class="smoke-badge" title="核心冒烟用例">冒烟</span>' : '';
    return `
      <div class="yaml-task-nav-item ${task.name === selected ? 'active' : ''}" onclick="jumpToTask(${index})">
        <div class="yaml-task-nav-name">${escapeHtml(task.name)}</div>
        <div class="yaml-task-nav-meta">line ${task.line}${escapeHtml(status)}</div>
        <div class="yaml-task-nav-badges">${priorityHtml}${smokeHtml}</div>
        <div class="yaml-task-nav-actions">
          <button onclick="event.stopPropagation();runTaskFromNav(${index})">执行</button>
          <button onclick="event.stopPropagation();repairTaskFromNav(${index})">修复</button>
        </div>
      </div>
    `;
  }).join('');
  updateToolbarState();
  renderEditorContextBar();
}

function selectedTaskInfo() {
  const ta = document.getElementById('editor');
  if (!ta) return { tasks: [], index: -1, task: null };
  const tasks = parseYamlTasks(ta.value);
  if (!tasks.length) return { tasks, index: -1, task: null };
  const line = currentCursorLine(ta);
  let index = 0;
  for (let i = 0; i < tasks.length; i++) {
    if (tasks[i].line <= line) index = i;
    else break;
  }
  return { tasks, index, task: tasks[index] };
}

function jobForTaskName(taskName) {
  return latestJobs.find(item => item.module === currentModule && item.file === currentFile && item.target_task_name === taskName) || null;
}

function renderEditorContextBar() {
  const bar = document.getElementById('editor-context-bar');
  if (!bar) return;
  const { tasks, index, task } = selectedTaskInfo();
  if (!task) {
    bar.innerHTML = `
      <div class="editor-context-main">
        <span class="editor-context-title">${escapeHtml(currentFile || '未选择 YAML')}</span>
        <span class="editor-context-sub">未解析到 tasks[].name</span>
      </div>
      <div class="editor-context-actions">
        <button class="btn-sm" onclick="renderYamlTaskNav()">刷新解析</button>
      </div>
    `;
    return;
  }
  const priority = task.priority || 'P2';
  const job = jobForTaskName(task.name);
  const status = job ? (job.status || '') : '';
  const statusText = status ? jobStatusText(status) : '未执行';
  const smokeHtml = task.smoke ? '<span class="smoke-badge">冒烟</span>' : '';
  bar.innerHTML = `
    <div class="editor-context-main">
      <span class="editor-context-title" title="${escapeHtml(task.name)}">${escapeHtml(task.name)}</span>
      <span class="editor-context-sub">${index + 1}/${tasks.length} · line ${task.line}</span>
      <select class="priority-select ${escapeHtml(priority.toLowerCase())}" title="修改当前用例等级" onchange="changeTaskPriority(${index}, this.value)">
        ${['P0','P1','P2','P3'].map(p => `<option value="${p}" ${p === priority ? 'selected' : ''}>${p}</option>`).join('')}
      </select>
      ${smokeHtml}
      <span class="context-status-badge ${escapeHtml(status)}">${escapeHtml(statusText)}</span>
    </div>
    <div class="editor-context-actions">
      <button class="btn-sm success" onclick="runTaskFromNav(${index})">执行当前</button>
      <button class="btn-sm" onclick="repairTaskFromNav(${index})">修复当前</button>
      <button class="btn-sm" onclick="toggleCaseNavPanel()">${isPanelCollapsed('caseNav') ? '显示用例列表' : '隐藏用例列表'}</button>
    </div>
  `;
}

function jumpToTask(index) {
  const ta = document.getElementById('editor');
  if (!ta) return;
  const tasks = parseYamlTasks(ta.value);
  const task = tasks[index];
  if (!task) return;
  const lines = ta.value.split('\n');
  const pos = lines.slice(0, Math.max(0, task.line - 1)).join('\n').length + (task.line > 1 ? 1 : 0);
  ta.focus();
  ta.selectionStart = ta.selectionEnd = pos;
  const lineHeight = parseFloat(getComputedStyle(ta).lineHeight) || 22;
  ta.scrollTop = Math.max(0, (task.line - 3) * lineHeight);
  renderYamlTaskNav();
  renderEditorContextBar();
  refreshBaselinePreview(true);
}

function runTaskFromNav(index) {
  jumpToTask(index);
  showRunSelectedTask();
}

function repairTaskFromNav(index) {
  const tasks = parseYamlTasks(document.getElementById('editor')?.value || '');
  const task = tasks[index];
  if (!task) return;
  jumpToTask(index);
  repairTaskByName(task.name);
}

function setTaskPriorityInLines(lines, task, nextTask, priority) {
  const start = task.line - 1;
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^\s*-\s+name:\s*/.test(lines[i])) {
      end = i;
      break;
    }
  }
  let changed = false;
  for (let i = start + 1; i < end; i++) {
    if (/#\s*baseline\.priority\s*:/.test(lines[i])) {
      const indent = (lines[i].match(/^\s*/) || [''])[0];
      lines[i] = `${indent}# baseline.priority: ${priority}`;
      changed = true;
      break;
    }
  }
  if (!changed) {
    const nameIndent = (lines[start].match(/^\s*/) || [''])[0];
    lines.splice(start + 1, 0, `${nameIndent}  # baseline.priority: ${priority}`);
  }
}

function changeTaskPriority(index, priority) {
  const ta = document.getElementById('editor');
  if (!ta) return;
  const allowed = ['P0', 'P1', 'P2', 'P3'];
  priority = String(priority || '').toUpperCase();
  if (!allowed.includes(priority)) priority = 'P2';
  const tasks = parseYamlTasks(ta.value);
  const task = tasks[index];
  if (!task) return;
  const lines = ta.value.split('\n');
  setTaskPriorityInLines(lines, task, tasks[index + 1], priority);
  ta.value = lines.join('\n');
  markEditorDirty();
  updateLines();
  renderYamlTaskNav();
  showToast(`已将「${task.name}」等级改为 ${priority}，记得保存 YAML`, 'success');
}

function changeAllTaskPriorities(priority) {
  const ta = document.getElementById('editor');
  if (!ta || !priority) return;
  const allowed = ['P0', 'P1', 'P2', 'P3'];
  priority = String(priority || '').toUpperCase();
  if (!allowed.includes(priority)) return;
  const tasks = parseYamlTasks(ta.value);
  if (!tasks.length) {
    showToast('当前 YAML 没有可修改等级的用例', 'error');
    return;
  }
  if (!confirm(`确认将当前 YAML 的 ${tasks.length} 条用例全部标记为 ${priority}？`)) return;
  const lines = ta.value.split('\n');
  for (let index = tasks.length - 1; index >= 0; index--) {
    setTaskPriorityInLines(lines, tasks[index], tasks[index + 1], priority);
  }
  ta.value = lines.join('\n');
  markEditorDirty();
  updateLines();
  renderYamlTaskNav();
  showToast(`已将当前 YAML 的 ${tasks.length} 条用例全部改为 ${priority}，记得保存`, 'success');
}

function handleTab(e) {
  if (e.key === 'Tab') {
    e.preventDefault();
    const ta = e.target;
    const s = ta.selectionStart;
    ta.value = ta.value.substring(0,s) + '  ' + ta.value.substring(ta.selectionEnd);
    ta.selectionStart = ta.selectionEnd = s + 2;
  }
}

async function saveFile(options = {}) {
  const showSuccess = options.showSuccess !== false;
  const content = document.getElementById('editor')?.value;
  if (!content || !currentFile) return false;
  try {
    const data = await apiRequest('/file', {
      method: 'POST',
      body: JSON.stringify({ module: currentModule, file: currentFile, content })
    });
    if (showSuccess) showToast('✓ 保存成功', 'success');
    editorInitialContent = content;
    editorDirty = false;
    updateToolbarState();
    return true;
  } catch(e) {
    if (showSuccess) showToast(e.message || '保存失败', 'error');
    throw e;
  }
}

async function updateCurrentFileStatus() {
  const status = document.getElementById('file-status-select').value;
  if (!status) return;
  const selectedItems = Array.from(selectedFiles).map(key => {
    const index = key.indexOf('::');
    return { mod: key.slice(0, index), file: key.slice(index + 2) };
  });

  if (selectedItems.length > 0) {
    if (!confirm(`确认把已选 ${selectedItems.length} 个 YAML 标记为「${lifecycleText(status)}」？`)) {
      if (currentModule && currentFile) document.getElementById('file-status-select').value = fileMeta(currentModule, currentFile).status || 'draft';
      return;
    }
    let updated = 0;
    for (const item of selectedItems) {
      try {
        const data = await apiRequest('/file/status', {
          method: 'POST',
          body: JSON.stringify({ module: item.mod, file: item.file, status })
        });
        taskMeta[metaKey(item.mod, item.file)] = data.meta;
        updated += 1;
      } catch(e) {}
    }
    renderModules();
    document.getElementById('file-info').textContent = `已批量标记 ${updated}/${selectedItems.length} 个文件为「${lifecycleText(status)}」`;
    showToast(`✓ 已批量标记 ${updated} 个文件`, 'success');
    return;
  }

  if (!currentModule || !currentFile) return;
  try {
    const data = await apiRequest('/file/status', {
      method: 'POST',
      body: JSON.stringify({ module: currentModule, file: currentFile, status })
    });
    taskMeta[metaKey(currentModule, currentFile)] = data.meta;
    renderModules();
    showToast(`✓ 状态已更新为：${lifecycleText(status)}`, 'success');
  } catch(e) {
    showToast(e.message || '状态更新失败', 'error');
  }
}

function showFileOp(op) {
  if (!currentModule || !currentFile) {
    showToast('请先选择一个 YAML 文件', 'error');
    return;
  }
  const titleMap = { copy: '复制 YAML', move: '移动 YAML', rename: '重命名 YAML' };
  document.getElementById('file-op-type').value = op;
  document.getElementById('file-op-title').textContent = titleMap[op] || '文件操作';
  document.getElementById('file-op-source').textContent = `源文件：${currentModule}/${currentFile}`;
  document.getElementById('file-op-module').innerHTML = moduleOptionsHtml(currentModule);
  document.getElementById('file-op-module').disabled = op === 'rename';
  document.getElementById('file-op-name').value = op === 'copy' ? currentFile.replace(/\.ya?ml$/i, '-副本.yaml') : currentFile;
  document.getElementById('file-op-overwrite').checked = false;
  document.getElementById('modal-file-op').classList.add('show');
}

async function submitFileOp() {
  const op = document.getElementById('file-op-type').value || 'copy';
  const targetModule = op === 'rename' ? currentModule : document.getElementById('file-op-module').value;
  const targetFile = document.getElementById('file-op-name').value.trim();
  const overwrite = document.getElementById('file-op-overwrite').checked;
  if (!targetModule || !targetFile) {
    showToast('请选择目标模块并填写文件名', 'error');
    return;
  }
  try {
    const data = await apiRequest('/file/op', {
      method: 'POST',
      body: JSON.stringify({
        op,
        module: currentModule,
        file: currentFile,
        targetModule,
        targetFile,
        overwrite
      })
    });
    closeModal('modal-file-op');
    await loadModules();
    await openFile(data.module, data.file);
    const actionText = op === 'copy' ? '复制' : op === 'rename' ? '重命名' : '移动';
    showToast(`✓ 已${actionText}到 ${data.module}/${data.file}`, 'success');
  } catch(e) {
    showToast(e.message || '文件操作失败', 'error');
  }
}

function selectedFileItems() {
  return Array.from(selectedFiles).map(key => {
    const index = key.indexOf('::');
    return { module: key.slice(0, index), file: key.slice(index + 2) };
  });
}

function showBatchMove() {
  const items = selectedFileItems();
  if (!items.length) {
    showToast('请先勾选要移动的 YAML 文件', 'error');
    return;
  }
  document.getElementById('batch-move-count').textContent = `已选择 ${items.length} 个 YAML 文件`;
  document.getElementById('batch-move-module').innerHTML = moduleOptionsHtml(currentModule || '');
  document.getElementById('batch-move-overwrite').checked = false;
  document.getElementById('modal-batch-move').classList.add('show');
}

async function submitBatchMove() {
  const targetModule = document.getElementById('batch-move-module').value;
  const overwrite = document.getElementById('batch-move-overwrite').checked;
  const items = selectedFileItems();
  if (!targetModule || !items.length) {
    showToast('请选择目标模块和文件', 'error');
    return;
  }
  try {
    const data = await apiRequest('/files/op', {
      method: 'POST',
      body: JSON.stringify({ op: 'move', targetModule, overwrite, items })
    });
    selectedFiles.clear();
    closeModal('modal-batch-move');
    await loadModules();
    showToast(`✓ 已移动 ${data.results?.length || 0} 个文件`, 'success');
  } catch(e) {
    showToast(e.message || '批量移动失败', 'error');
  }
}

async function showFileHistory() {
  if (!currentModule || !currentFile) {
    showToast('请先选择一个 YAML 文件', 'error');
    return;
  }
  document.getElementById('history-source').textContent = `${currentModule}/${currentFile}`;
  const list = document.getElementById('history-list');
  list.innerHTML = '<div class="job-empty">正在加载历史版本...</div>';
  document.getElementById('modal-history').classList.add('show');
  try {
    const data = await apiRequest(`/file/history?module=${encodeURIComponent(currentModule)}&file=${encodeURIComponent(currentFile)}`);
    const versions = data.versions || [];
    if (!versions.length) {
      list.innerHTML = '<div class="job-empty">暂无历史版本。保存、AI 修复、覆盖、移动前会自动生成历史。</div>';
      return;
    }
    list.innerHTML = versions.map(v => `
      <div class="app-row">
        <div class="app-row-main">
          <div class="app-row-name">${escapeHtml(v.created_at || v.id)}</div>
          <div class="app-row-sub">${escapeHtml(v.reason || '')} · ${formatBytes(v.size || 0)} · ${escapeHtml(v.id || '')}</div>
        </div>
        <button class="btn-sm" onclick="previewFileVersion('${escapeHtml(v.id)}')">预览</button>
        <button class="btn-sm danger" onclick="restoreFileVersion('${escapeHtml(v.id)}')">回滚</button>
      </div>
    `).join('');
  } catch(e) {
    list.innerHTML = `<div class="job-empty">${escapeHtml(e.message || '读取历史失败')}</div>`;
  }
}

async function previewFileVersion(versionId) {
  try {
    const data = await apiRequest(`/file/version?module=${encodeURIComponent(currentModule)}&file=${encodeURIComponent(currentFile)}&version=${encodeURIComponent(versionId)}`);
    showEditor(data.content || '');
    showToast('已加载历史版本到编辑器，确认后可手动保存', 'success');
    closeModal('modal-history');
  } catch(e) {
    showToast(e.message || '预览版本失败', 'error');
  }
}

async function restoreFileVersion(versionId) {
  if (!confirm('确认回滚到这个历史版本？当前内容会先自动保存为历史。')) return;
  try {
    await apiRequest('/file/restore', {
      method: 'POST',
      body: JSON.stringify({ module: currentModule, file: currentFile, version: versionId })
    });
    await openFile(currentModule, currentFile);
    closeModal('modal-history');
    showToast('✓ 已回滚到历史版本', 'success');
  } catch(e) {
    showToast(e.message || '回滚失败', 'error');
  }
}

async function showBaselineRefs(initialTaskName='') {
  setActiveWorkflow('baseline');
  if (!currentModule || !currentFile) {
    showToast('请先选择一个 YAML 文件', 'error');
    return;
  }
  const ta = document.getElementById('editor');
  const tasks = parseYamlTasks(ta?.value || '');
  const select = document.getElementById('baseline-ref-task');
  select.innerHTML = '<option value="">整个 YAML 文件</option>' + tasks.map(task => `<option value="${escapeHtml(task.name)}">${escapeHtml(task.name)}</option>`).join('');
  if (initialTaskName && tasks.some(task => task.name === initialTaskName)) select.value = initialTaskName;
  document.getElementById('baseline-ref-source').textContent = `${currentModule}/${currentFile} · 应用：${appDisplayLabel(currentModuleAppPackage())}`;
  document.getElementById('baseline-ref-status').textContent = '';
  document.getElementById('baseline-ref-status').className = 'generate-status';
  document.getElementById('baseline-ref-pages').innerHTML = '<div class="generate-knowledge-empty">正在加载页面知识...</div>';
  document.getElementById('modal-baseline-refs').classList.add('show');
  await loadBaselineRefs();
}

async function loadBaselineRefs() {
  if (!currentModule || !currentFile) return;
  const appPackage = currentModuleAppPackage();
  const taskName = document.getElementById('baseline-ref-task')?.value || '';
  const list = document.getElementById('baseline-ref-pages');
  const status = document.getElementById('baseline-ref-status');
  list.innerHTML = '<div class="generate-knowledge-empty">正在加载页面知识...</div>';
  try {
    const qs = new URLSearchParams({
      app_package: appPackage,
      module: currentModule,
      file: currentFile,
      taskName
    });
    const data = await apiRequest(`/baseline/page-refs?${qs.toString()}`);
    baselineRefPages = data.pages || [];
    const selected = new Set(taskName ? (data.task_page_ids || []) : (data.file_page_ids || []));
    if (!baselineRefPages.length) {
      list.innerHTML = '<div class="generate-knowledge-empty">当前 APP 还没有页面知识。先到 APP 页面知识库保存首页、我的页、核心业务页截图。</div>';
      return;
    }
    list.innerHTML = baselineRefPages.map(page => `
      <label class="generate-knowledge-item" title="${escapeHtml(page.route || page.description || '')}">
        <input type="checkbox" class="baseline-ref-check" value="${escapeHtml(page.page_id)}" ${selected.has(page.page_id) ? 'checked' : ''}>
        <div class="generate-knowledge-main">
          <div class="generate-knowledge-name">${escapeHtml(page.page_name || page.page_id)} · ${page.tier === 'baseline' ? '基线库' : '测试库'}</div>
          <div class="generate-knowledge-sub">${escapeHtml(page.route || page.description || '无路径说明')}</div>
        </div>
      </label>
    `).join('');
    const inherited = taskName && (data.file_page_ids || []).length ? `；已继承文件级 ${data.file_page_ids.length} 个页面` : '';
    status.textContent = `已加载 ${baselineRefPages.length} 个页面知识${inherited}`;
    status.className = 'generate-status show success';
  } catch(e) {
    list.innerHTML = `<div class="generate-knowledge-empty">${escapeHtml(e.message || '读取失败')}</div>`;
    status.textContent = e.message || '读取基线辅助截图失败';
    status.className = 'generate-status show error';
  }
}

async function saveBaselineRefs() {
  if (!currentModule || !currentFile) return;
  const appPackage = currentModuleAppPackage();
  const taskName = document.getElementById('baseline-ref-task')?.value || '';
  const pageIds = Array.from(document.querySelectorAll('.baseline-ref-check:checked')).map(input => input.value);
  const status = document.getElementById('baseline-ref-status');
  try {
    await apiRequest('/baseline/page-refs', {
      method: 'POST',
      body: JSON.stringify({
        app_package: appPackage,
        module: currentModule,
        file: currentFile,
        taskName,
        page_ids: pageIds
      })
    });
    status.textContent = taskName ? `已为用例「${taskName}」绑定 ${pageIds.length} 个辅助页面` : `已为整个 YAML 绑定 ${pageIds.length} 个辅助页面`;
    status.className = 'generate-status show success';
    baselinePreviewTaskName = '';
    await refreshBaselinePreview(true);
    showToast('✓ 基线辅助截图绑定已保存', 'success');
  } catch(e) {
    status.textContent = e.message || '保存失败';
    status.className = 'generate-status show error';
  }
}

async function repairCurrentFile() {
  setActiveWorkflow('repair');
  if (!currentModule || !currentFile) {
    showToast('请先选择一个 YAML 文件', 'error');
    return;
  }
  if (!confirm(`为「${currentModule}/${currentFile}」生成 AI 修复草稿？\n\n平台会先分析失败日志、页面知识和 YAML 结构，默认不直接覆盖基线；修复草稿仍需要你复核后再应用。`)) return;
  const button = document.getElementById('btn-repair-file');
  await LoadingManager.withLoading(async () => {
    try {
      const saved = await saveFile({showSuccess:false});
      if (!saved) throw new Error('当前文件为空，无法修复');
      showToast('修复草稿任务已创建，正在后台分析日志和页面知识...', 'success');
      const created = await apiRequest('/file/repair-latest-async', {
        method: 'POST',
        body: JSON.stringify({ module: currentModule, file: currentFile, createJob: true, forceRepair: true })
      });
      const data = await pollGenericJob(created.job_id, job => {
        const progress = Number(job.progress || 0);
        const msg = job.message || job.step || '正在修复';
        updateRepairProgress(button, progress, msg);
      });
      await openFile(currentModule, currentFile);
      loadJobs();
      const changes = (data.changes || []).slice(0, 2).join('；');
      showToast(`✓ 已${data.mode === 'static' ? '静态体检并' : ''}修复${data.next_job ? '，已创建执行任务' : ''}${changes ? `：${changes}` : ''}`, 'success');
      showRepairResult({...data, module: currentModule, file: currentFile}, 'AI 分析并生成修复草稿');
    } catch(e) {
      showToast(e.message || '修复失败', 'error');
    }
  }, { btn: button, btnLabel: '分析中...', overlay: 'AI 正在分析并生成修复草稿...' });
}

function cleanYamlTaskName(raw) {
  let name = (raw || '').trim();
  if ((name.startsWith('"') && name.endsWith('"')) || (name.startsWith("'") && name.endsWith("'"))) {
    name = name.slice(1, -1);
  }
  return name.replace(/\\"/g, '"').trim();
}

function parseYamlTasks(content) {
  const lines = (content || '').split('\n');
  const tasks = [];
  const re = /^(\s*)-\s+name:\s*(.+?)\s*$/;
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(re);
    if (!m) continue;
    const task = { name: cleanYamlTaskName(m[2]), line: i + 1, indent: m[1].length, priority: 'P2', smoke: false };
    for (let j = i + 1; j < Math.min(lines.length, i + 18); j++) {
      if (/^\s*-\s+name:\s*/.test(lines[j])) break;
      const priorityMatch = lines[j].match(/#\s*baseline\.priority\s*:\s*(P[0-3])/i);
      if (priorityMatch) task.priority = priorityMatch[1].toUpperCase();
      const smokeMatch = lines[j].match(/#\s*baseline\.smoke\s*:\s*(.+?)\s*$/i);
      if (smokeMatch) task.smoke = /true|1|yes|是|冒烟|smoke/i.test(smokeMatch[1]);
      const tagsMatch = lines[j].match(/#\s*baseline\.tags\s*:\s*(.+?)\s*$/i);
      if (tagsMatch && /冒烟|smoke/i.test(tagsMatch[1])) task.smoke = true;
    }
    tasks.push(task);
  }
  return tasks;
}

function currentCursorLine(textarea) {
  return textarea.value.slice(0, textarea.selectionStart).split('\n').length;
}

function detectSelectedTaskName() {
  const ta = document.getElementById('editor');
  if (!ta) return '';
  const tasks = parseYamlTasks(ta.value);
  if (!tasks.length) return '';
  const line = currentCursorLine(ta);
  let selected = tasks[0];
  for (const task of tasks) {
    if (task.line <= line) selected = task;
    else break;
  }
  return selected.name;
}

function scheduleBaselinePreviewRefresh() {
  clearTimeout(baselinePreviewTimer);
  baselinePreviewTimer = setTimeout(() => refreshBaselinePreview(), 260);
}

function baselinePageCard(page, appPackage) {
  const hasShot = !!page.screenshot;
  const imgUrl = hasShot
    ? `${API_BASE}/knowledge/screenshot?app_package=${encodeURIComponent(appPackage)}&page_id=${encodeURIComponent(page.page_id)}`
    : '';
  const meta = page.route || page.description || page.page_id || '';
  const sourceText = page.ref_source === 'task' ? '用例级' : (page.ref_source === 'both' ? '文件+用例' : '文件级');
  const removeTitle = page.ref_source === 'task' ? '移除当前用例关联' : (page.ref_source === 'both' ? '移除当前用例关联，仍会继承文件级' : '移除整个文件关联');
  const image = hasShot
    ? `<img src="${imgUrl}" alt="${escapeHtml(page.page_name || page.page_id)}">`
    : `<div class="generate-knowledge-empty" style="height:92px;display:flex;align-items:center;">未保存截图</div>`;
  return `
    <div class="baseline-preview-card" title="${escapeHtml(meta)}">
      <a href="${hasShot ? imgUrl : '#'}" target="${hasShot ? '_blank' : ''}" style="color:inherit;text-decoration:none;">
      ${image}
      <div>
        <strong>${escapeHtml(page.page_name || page.page_id)}</strong>
        <span>${escapeHtml(sourceText)} · ${escapeHtml(meta || (page.tier === 'baseline' ? '基线库' : '页面知识'))}</span>
      </div>
      </a>
      <div class="baseline-preview-card-actions">
        <button type="button" onclick="openKnowledgePageForEdit(${jsArg(appPackage)},${jsArg(page.page_id)})">编辑</button>
        <button type="button" class="danger" title="${escapeHtml(removeTitle)}" onclick="removeBaselinePreviewRef(${jsArg(page.page_id)},${jsArg(page.ref_source || 'file')})">移除</button>
      </div>
    </div>
  `;
}

async function refreshBaselinePreview(force=false) {
  if (!currentModule || !currentFile) return;
  const list = document.getElementById('baseline-preview-list');
  const sub = document.getElementById('baseline-preview-sub');
  if (!list || !sub) return;
  const appPackage = currentModuleAppPackage();
  const taskName = detectSelectedTaskName();
  if (!force && taskName === baselinePreviewTaskName && list.dataset.loaded === '1') return;
  baselinePreviewTaskName = taskName;
  sub.textContent = taskName ? `当前用例：${taskName}` : '整个 YAML 文件';
  list.dataset.loaded = '0';
  list.innerHTML = '<div class="generate-knowledge-empty">正在读取关联截图...</div>';
  try {
    const qs = new URLSearchParams({
      app_package: appPackage,
      module: currentModule,
      file: currentFile,
      taskName
    });
    const data = await apiRequest(`/baseline/page-refs?${qs.toString()}`);
    baselinePreviewData = data;
    const merged = new Set(data.merged_page_ids || []);
    const pages = data.selected_pages || (data.pages || []).filter(page => merged.has(page.page_id));
    const inherited = taskName && (data.file_page_ids || []).length ? `，继承文件级 ${data.file_page_ids.length} 个` : '';
    sub.textContent = taskName
      ? `当前用例：${taskName} · 已关联 ${pages.length} 个页面${inherited}`
      : `整个 YAML 文件 · 已关联 ${pages.length} 个页面`;
    list.dataset.loaded = '1';
    if (!pages.length) {
      list.innerHTML = `<div class="generate-knowledge-empty">当前${taskName ? '用例' : '文件'}还没有绑定基线截图。点击“维护关联”从页面知识库选择。</div>`;
      return;
    }
    list.innerHTML = pages.map(page => baselinePageCard(page, appPackage)).join('');
  } catch(e) {
    list.dataset.loaded = '1';
    list.innerHTML = `<div class="generate-knowledge-empty">${escapeHtml(e.message || '读取基线参考截图失败')}</div>`;
  }
}

async function showBaselineRefsForCurrentTask() {
  await showBaselineRefs(detectSelectedTaskName());
}

async function openKnowledgePageForEdit(appPackage, pageId) {
  await showKnowledge();
  document.getElementById('knowledge-app-package').value = appPackage;
  await loadKnowledgePages();
  editKnowledgePage(pageId);
}

async function removeBaselinePreviewRef(pageId, source) {
  if (!currentModule || !currentFile || !baselinePreviewData) return;
  const taskName = detectSelectedTaskName();
  const removeTaskLevel = source === 'task' || source === 'both';
  const targetTaskName = removeTaskLevel ? taskName : '';
  const currentIds = removeTaskLevel
    ? (baselinePreviewData.task_page_ids || [])
    : (baselinePreviewData.file_page_ids || []);
  if (!currentIds.includes(pageId)) {
    showToast('这个截图来自继承关系，请到文件级关联里移除', 'error');
    return;
  }
  const scopeText = removeTaskLevel ? `当前用例「${taskName || '未命名'}」` : '整个 YAML 文件';
  if (!confirm(`确认从${scopeText}移除这张参考截图？`)) return;
  const nextIds = currentIds.filter(id => id !== pageId);
  try {
    await apiRequest('/baseline/page-refs', {
      method: 'POST',
      body: JSON.stringify({
        app_package: currentModuleAppPackage(),
        module: currentModule,
        file: currentFile,
        taskName: targetTaskName,
        page_ids: nextIds
      })
    });
    baselinePreviewTaskName = '';
    await refreshBaselinePreview(true);
    showToast('✓ 已移除参考截图关联', 'success');
  } catch(e) {
    showToast(e.message || '移除失败', 'error');
  }
}

async function repairSelectedTask() {
  setActiveWorkflow('repair');
  if (!currentModule || !currentFile) {
    showToast('请先选择一个 YAML 文件', 'error');
    return;
  }
  const detected = detectSelectedTaskName();
  const taskName = prompt('输入要修复的用例 name。光标放在某条用例里会自动带出：', detected || '');
  if (!taskName) return;
  await repairTaskByName(taskName);
}

async function repairTaskByName(taskName) {
  setActiveWorkflow('repair');
  if (!currentModule || !currentFile || !taskName) {
    showToast('请先选择一个 YAML 文件和用例', 'error');
    return;
  }
  if (!confirm(`修复用例「${taskName}」？\n\n人工修复会强制进入脚本修复流程；如果失败本身是产品 Bug，修复结果仍需要你复核，避免把真实缺陷改没。`)) return;

  const button = document.getElementById('btn-repair-task');
  await LoadingManager.withLoading(async () => {
    try {
      const saved = await saveFile({showSuccess:false});
      if (!saved) throw new Error('当前文件为空，无法修复');
      showToast('单条修复任务已创建，正在后台分析日志和页面知识...', 'success');
      const created = await apiRequest('/file/repair-task-latest-async', {
        method: 'POST',
        body: JSON.stringify({ module: currentModule, file: currentFile, taskName, createJob: true, forceRepair: true })
      });
      const data = await pollGenericJob(created.job_id, job => {
        const progress = Number(job.progress || 0);
        const msg = job.message || job.step || '正在修复';
        updateRepairProgress(button, progress, msg);
      });
      await openFile(currentModule, currentFile);
      loadJobs();
      const changes = (data.changes || []).slice(0, 2).join('；');
      showToast(`✓ 已${data.mode === 'static' ? '静态体检并' : ''}修复用例「${taskName}」${data.next_job ? '，已创建执行任务' : ''}${changes ? `：${changes}` : ''}`, 'success');
      showRepairResult({...data, module: currentModule, file: currentFile, taskName}, 'AI 修复单条用例');
    } catch(e) {
      showToast(e.message || '修复失败', 'error');
    }
  }, { btn: button, btnLabel: '修复中...', overlay: 'AI 正在修复用例...' });
}

function showRunCurrentFile() {
  setActiveWorkflow('execute');
  if (!currentModule || !currentFile) {
    showToast('请先选择一个 YAML 文件', 'error');
    return;
  }
  document.getElementById('run-file-name').textContent = `将执行：${currentModule} / ${currentFile}`;
  document.getElementById('run-file-mode').value = 'test';
  document.getElementById('run-file-auto-optimize').checked = false;
  syncRunFileModeControls();
  document.getElementById('run-file-status').textContent = '';
  document.getElementById('run-file-status').className = 'generate-status';
  loadRunnerDevices();
  document.getElementById('modal-run-file').classList.add('show');
}

async function runCurrentFile() {
  if (!currentModule || !currentFile) {
    showToast('请先选择一个 YAML 文件', 'error');
    return;
  }
  const button = document.getElementById('btn-run-file-confirm');
  const status = document.getElementById('run-file-status');
  const runMode = document.getElementById('run-file-mode').value || 'test';
  const autoOptimize = document.getElementById('run-file-auto-optimize').checked;
  const selectedDevice = requireRunnerDevice('run-file-device', 'run-file-status', '创建整文件执行任务');
  if (!selectedDevice) return;
  status.textContent = '正在保存 YAML 并创建整文件执行任务...';
  status.className = 'generate-status show busy';
  await LoadingManager.withLoading(async () => {
    try {
      const saved = await saveFile({showSuccess:false});
      if (!saved) throw new Error('当前文件为空，无法执行');
      const data = await apiRequest('/run-request', {
        method: 'POST',
        body: JSON.stringify({
          module: currentModule,
          file: currentFile,
          run_mode: runMode,
          autoOptimize,
          runner_id: selectedDevice.runner_id,
          device_id: selectedDevice.device_id,
          device_strategy: selectedDevice.device_strategy
        })
      });
      loadJobs();
      status.textContent = `已创建任务：${data.job?.job_id || ''}`;
      status.className = 'generate-status show success';
      showToast(`✓ 已创建整文件执行任务：${currentFile}`, 'success');
      setTimeout(() => closeModal('modal-run-file'), 700);
    } catch(e) {
      status.textContent = e.message || '创建执行任务失败';
      status.className = 'generate-status show error';
      showToast(e.message || '创建执行任务失败', 'error');
    }
  }, { btn: button, btnLabel: '创建中...' });
}

function showRunSelectedTask() {
  setActiveWorkflow('execute');
  if (!currentModule || !currentFile) {
    showToast('请先选择一个 YAML 文件', 'error');
    return;
  }
  const ta = document.getElementById('editor');
  const tasks = parseYamlTasks(ta?.value || '');
  if (!tasks.length) {
    showToast('当前 YAML 没有解析到 tasks[].name', 'error');
    return;
  }
  const selectedName = detectSelectedTaskName();
  const select = document.getElementById('run-task-name');
  select.innerHTML = tasks.map(task => `<option value="${escapeHtml(task.name)}">${escapeHtml(task.name)}</option>`).join('');
  if (selectedName && tasks.some(task => task.name === selectedName)) {
    select.value = selectedName;
  } else if (select.options.length) {
    select.options[0].selected = true;
  }
  document.getElementById('run-task-mode').value = 'test';
  document.getElementById('run-task-auto-optimize').checked = false;
  syncRunTaskModeControls();
  document.getElementById('run-task-status').textContent = '';
  document.getElementById('run-task-status').className = 'generate-status';
  loadRunnerDevices();
  document.getElementById('modal-run-task').classList.add('show');
}

async function runSelectedTask() {
  const select = document.getElementById('run-task-name');
  const taskNames = Array.from(select?.selectedOptions || []).map(option => option.value).filter(Boolean);
  if (!currentModule || !currentFile || !taskNames.length) {
    showToast('请选择要调试的一条或多条用例', 'error');
    return;
  }
  const button = document.getElementById('btn-run-task-confirm');
  const status = document.getElementById('run-task-status');
  const runMode = document.getElementById('run-task-mode').value || 'test';
  const autoOptimize = document.getElementById('run-task-auto-optimize').checked;
  const selectedDevice = requireRunnerDevice('run-task-device', 'run-task-status', '创建单条/多条调试任务');
  if (!selectedDevice) return;
  status.textContent = `正在保存 YAML 并创建 ${taskNames.length} 个单条调试任务...`;
  status.className = 'generate-status show busy';
  await LoadingManager.withLoading(async () => {
    try {
      const saved = await saveFile({showSuccess:false});
      if (!saved) throw new Error('当前文件为空，无法执行');
      const created = [];
      for (const taskName of taskNames) {
        const data = await apiRequest('/run-request', {
          method: 'POST',
          body: JSON.stringify({
            module: currentModule,
            file: currentFile,
            target_task_name: taskName,
            run_mode: runMode,
            autoOptimize,
            runner_id: selectedDevice.runner_id,
            device_id: selectedDevice.device_id,
            device_strategy: selectedDevice.device_strategy
          })
        });
        if (data.job) created.push(data.job);
      }
      loadJobs();
      status.textContent = `已创建 ${created.length} 个单条调试任务`;
      status.className = 'generate-status show success';
      showToast(`✓ 已创建 ${created.length} 个单条调试任务`, 'success');
      setTimeout(() => closeModal('modal-run-task'), 700);
    } catch(e) {
      status.textContent = e.message || '创建执行任务失败';
      status.className = 'generate-status show error';
      showToast(e.message || '创建执行任务失败', 'error');
    }
  }, { btn: button, btnLabel: '创建中...' });
}

async function deleteFile(mod, file) {
  if (!confirm(`确认删除 ${mod}/${file}？`)) return;
  try {
    await apiRequest(`/file?module=${encodeURIComponent(mod)}&file=${encodeURIComponent(file)}`, { method: 'DELETE' });
  } catch(e) {}
  if (modules[mod]) modules[mod] = modules[mod].filter(f => f !== file);
  selectedFiles.delete(fileKey(mod, file));
  if (currentFile === file && currentModule === mod) {
    currentFile = null;
    if (!resetEditorToWorkflowGuide()) {
      /* already handled */
    }
    document.getElementById('btn-save').style.display = 'none';
    document.getElementById('btn-copy-file').style.display = 'none';
    document.getElementById('btn-move-file').style.display = 'none';
    document.getElementById('btn-rename-file').style.display = 'none';
    document.getElementById('btn-history-file').style.display = 'none';
    document.getElementById('btn-baseline-refs').style.display = 'none';
    document.getElementById('btn-publish-sonic').style.display = 'none';
    document.getElementById('file-status-select').style.display = 'none';
    document.getElementById('btn-run-file').style.display = 'none';
    document.getElementById('btn-run-task').style.display = 'none';
    document.getElementById('btn-repair-task').style.display = 'none';
    document.getElementById('btn-repair-file').style.display = 'none';
    setFileContextVisible(false);
    document.getElementById('toolbar-path').innerHTML = '<span>📁</span> 选择左侧文件开始编辑';
    document.getElementById('toolbar-help').textContent = '从左侧模块选择 YAML，或先用需求/设计稿生成可执行用例。';
  }
  renderModules();
  showToast('✓ 已删除', 'success');
}

async function deleteSelectedFiles() {
  const items = Array.from(selectedFiles).map(key => {
    const index = key.indexOf('::');
    return { mod: key.slice(0, index), file: key.slice(index + 2) };
  });
  if (items.length === 0) {
    showToast('请先勾选要删除的文件', 'error');
    return;
  }
  if (!confirm(`确认批量删除 ${items.length} 个 YAML 文件？`)) return;

  let deleted = 0;
  for (const item of items) {
    try {
      await apiRequest(`/file?module=${encodeURIComponent(item.mod)}&file=${encodeURIComponent(item.file)}`, { method: 'DELETE' });
      deleted += 1;
    } catch(e) {}
    if (modules[item.mod]) modules[item.mod] = modules[item.mod].filter(f => f !== item.file);
    if (currentModule === item.mod && currentFile === item.file) {
      currentFile = null;
    }
  }

  selectedFiles.clear();
  if (!currentFile) {
    if (!resetEditorToWorkflowGuide()) {
      /* already handled */
    }
    document.getElementById('btn-save').style.display = 'none';
    document.getElementById('btn-copy-file').style.display = 'none';
    document.getElementById('btn-move-file').style.display = 'none';
    document.getElementById('btn-rename-file').style.display = 'none';
    document.getElementById('btn-history-file').style.display = 'none';
    document.getElementById('btn-baseline-refs').style.display = 'none';
    document.getElementById('btn-publish-sonic').style.display = 'none';
    document.getElementById('file-status-select').style.display = 'none';
    document.getElementById('btn-run-file').style.display = 'none';
    document.getElementById('btn-run-task').style.display = 'none';
    document.getElementById('btn-repair-task').style.display = 'none';
    document.getElementById('btn-repair-file').style.display = 'none';
    setFileContextVisible(false);
    document.getElementById('toolbar-path').innerHTML = '<span>📁</span> 选择左侧文件开始编辑';
    document.getElementById('toolbar-help').textContent = '从左侧模块选择 YAML，或先用需求/设计稿生成可执行用例。';
  }
  renderModules();
  if (activeWorkflow === 'assets' && typeof showAssetsCenter === 'function') showAssetsCenter();
  document.getElementById('file-info').textContent = `已删除 ${deleted} 个文件`;
  showToast(`✓ 已删除 ${deleted} 个文件`, 'success');
}

async function deleteCurrentModule() {
  if (!currentModule) {
    showToast('请先展开要删除的模块', 'error');
    return;
  }
  const count = modules[currentModule]?.length || 0;
  if (!confirm(`确认删除模块「${currentModule}」及其中 ${count} 个 YAML 文件？`)) return;

  const deletingModule = currentModule;
  try {
    await apiRequest(`/module?module=${encodeURIComponent(deletingModule)}`, { method: 'DELETE' });
  } catch(e) {
    showToast(`删除模块失败：${e.message || e}`, 'error');
    return;
  }

  delete modules[deletingModule];
  selectedFiles = new Set(Array.from(selectedFiles).filter(key => !key.startsWith(`${deletingModule}::`)));
  if (currentModule === deletingModule) {
    currentModule = null;
    currentFile = null;
    if (!resetEditorToWorkflowGuide()) {
      /* already handled */
    }
    document.getElementById('btn-save').style.display = 'none';
    document.getElementById('btn-copy-file').style.display = 'none';
    document.getElementById('btn-move-file').style.display = 'none';
    document.getElementById('btn-rename-file').style.display = 'none';
    document.getElementById('btn-history-file').style.display = 'none';
    document.getElementById('btn-baseline-refs').style.display = 'none';
    document.getElementById('btn-publish-sonic').style.display = 'none';
    document.getElementById('file-status-select').style.display = 'none';
    document.getElementById('btn-run-file').style.display = 'none';
    document.getElementById('btn-run-task').style.display = 'none';
    document.getElementById('btn-repair-task').style.display = 'none';
    document.getElementById('btn-repair-file').style.display = 'none';
    setFileContextVisible(false);
    document.getElementById('toolbar-path').innerHTML = '<span>📁</span> 选择左侧文件开始编辑';
    document.getElementById('toolbar-help').textContent = '从左侧模块选择 YAML，或先用需求/设计稿生成可执行用例。';
  }
  renderModules();
  if (activeWorkflow === 'assets' && typeof showAssetsCenter === 'function') showAssetsCenter();
  document.getElementById('file-info').textContent = '就绪';
  showToast(`✓ 模块「${deletingModule}」已删除`, 'success');
}
