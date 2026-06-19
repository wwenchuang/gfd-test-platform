// agent-status.js
// Extracted from task-manager.html (no logic changes).

function agentStatusText(status) {
  const map = {
    START: '未开始',
    pending: '未开始',
    running: '执行中',
    success: '成功',
    failed: '失败',
    waiting: '等待人工确认',
    WAIT_CONFIRM: '等待人工确认',
    WAIT_CONFIRM_RUN: '等待人工确认',
    WAIT_CONFIRM_BUG: '等待人工确认',
    FAILED: '失败',
    CANCELLED: '已取消',
    DONE: '成功',
    FINISH: '成功'
  };
  return map[status] || status || '未开始';
}

function agentRunStatus(run) {
  return String(run?.status || '').toUpperCase();
}

function agentRunIsTerminal(run) {
  return ['DONE', 'FINISH', 'FAILED', 'CANCELLED'].includes(agentRunStatus(run));
}

function agentRunPanelTitle(run) {
  const status = agentRunStatus(run);
  if (status === 'DONE' || status === 'FINISH') return '最近完成';
  if (status === 'FAILED') return '最近失败';
  if (status === 'CANCELLED') return '已取消任务';
  if (/WAIT_CONFIRM/.test(status)) return '等待你确认';
  return '当前运行';
}

function parseAgentTime(value) {
  const text = String(value || '').trim();
  if (!text) return NaN;
  return Date.parse(text.replace(' ', 'T'));
}

function agentRunElapsedText(run) {
  if (!run?.createdAt) return '-';
  const start = parseAgentTime(run.createdAt);
  if (!Number.isFinite(start)) return '-';
  const status = agentRunStatus(run);
  const endCandidate = agentRunIsTerminal(run)
    ? (run.finishedAt || run.endedAt || run.updatedAt)
    : null;
  const end = endCandidate ? parseAgentTime(endCandidate) : Date.now();
  const seconds = Math.max(0, Math.round(((Number.isFinite(end) ? end : Date.now()) - start) / 1000));
  if (seconds >= 3600) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h${m}m`;
  }
  if (seconds >= 60) return `${Math.floor(seconds / 60)}m${seconds % 60}s`;
  return `${seconds}s`;
}

function agentRunProgressColor(run) {
  const status = agentRunStatus(run);
  if (status === 'DONE' || status === 'FINISH') return 'var(--success)';
  if (status === 'FAILED' || status === 'CANCELLED') return 'var(--danger)';
  if (/WAIT_CONFIRM/.test(status)) return 'var(--warn)';
  return 'var(--accent)';
}

function agentRunPillClass(run) {
  const status = agentRunStatus(run);
  if (status === 'DONE' || status === 'FINISH') return 'success';
  if (status === 'FAILED' || status === 'CANCELLED') return 'warn';
  if (/WAIT_CONFIRM/.test(status)) return 'waiting';
  return '';
}

function agentRunCardStatusClass(run) {
  const status = agentRunStatus(run);
  if (status === 'DONE' || status === 'FINISH') return 'success';
  if (status === 'FAILED') return 'failed';
  if (status === 'CANCELLED') return 'cancelled';
  if (/WAIT_CONFIRM/.test(status)) return 'waiting';
  if (status === 'RUNNING') return 'running';
  return 'pending';
}

function normalizeAgentRun(run) {
  if (!run) return null;
  const steps = Array.isArray(run.steps) ? run.steps : [];
  return {
    ...run,
    steps,
    artifacts: run.artifacts || {},
    confirmations: run.confirmations || [],
    options: run.options || {},
    status: run.status || 'START',
    currentStep: run.currentStep || 'START'
  };
}

function agentRunProgressPct(run) {
  if (!run) return 0;
  const explicit = Number(run.progress || 0);
  const status = agentRunStatus(run);
  if (status === 'DONE' || status === 'FINISH') return 100;

  const steps = Array.isArray(run.steps) ? run.steps : [];
  if (!steps.length) return Math.max(0, Math.min(100, Math.round(explicit || 0)));

  const doneStates = new Set(['SUCCESS', 'SKIPPED', 'PARTIAL_FAILED', 'FAILED', 'WAIT_CONFIRM']);
  let done = 0;
  let runningIndex = -1;
  steps.forEach((step, index) => {
    const state = String(step?.status || step?.state || '').toUpperCase();
    if (doneStates.has(state)) done += 1;
    if (state === 'RUNNING') runningIndex = index;
  });

  let derived = Math.round((done / Math.max(1, steps.length)) * 100);
  if (runningIndex >= 0) {
    derived = Math.max(derived, Math.round(((runningIndex + 0.35) / Math.max(1, steps.length)) * 100));
  }
  const merged = Math.max(explicit || 0, derived);
  if (status === 'FAILED' || status === 'CANCELLED') {
    return Math.max(1, Math.min(99, Math.round(merged || 1)));
  }
  return Math.max(0, Math.min(99, Math.round(merged)));
}

function currentAgentRun() {
  return normalizeAgentRun(agentCurrentRun || null);
}

function agentStepState(stepName, run = currentAgentRun()) {
  if (!run) return 'pending';
  const step = (run.steps || []).find(item => item.state === stepName || item.step === stepName);
  if (step?.success) return 'success';
  if (step && step.success === false) return 'failed';
  if (run.currentStep === stepName) {
    if (/WAIT_CONFIRM/.test(stepName) || /WAIT_CONFIRM/.test(run.status)) return 'waiting';
    return ['FAILED', 'CANCELLED', 'FINISH'].includes(run.status) ? run.status : 'running';
  }
  if ((run.confirmations || []).some(item => (
    stepName === 'WAIT_CONFIRM_RUN' && item.type === 'confirm_before_run'
  ) || (
    stepName === 'WAIT_CONFIRM_BUG' && /bug/i.test(item.type || '')
  ))) return 'waiting';
  return 'pending';
}

function agentProgressHtml(run = currentAgentRun()) {
  return `
    <div class="agent-steps">
      ${AGENT_STEPS.map(step => {
        const state = agentStepState(step, run);
        return `
          <div class="agent-step ${escapeHtml(state)}">
            <strong>${escapeHtml(agentStepLabel(step))}</strong>
            <span>${escapeHtml(agentStatusText(state))}</span>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

function agentArtifactText(tab, run = currentAgentRun()) {
  if (!run) return '暂无 Agent 产物。启动 Agent 后这里会展示生成结果。';
  const artifacts = run.artifacts || {};
  if (tab === 'plan') return stringifyArtifact(artifacts.plan || '暂无执行计划');
  if (tab === 'cases') return stringifyArtifact(artifacts.matchedCases || artifacts.caseDraft || artifacts.cases || '暂无测试用例产物');
  if (tab === 'yaml') return String(artifacts.generatedYaml || artifacts.yamlDraft || artifacts.yaml || '暂无 Midscene YAML');
  if (tab === 'validation') return stringifyArtifact(artifacts.yamlValidation || artifacts.validation || '暂无 YAML 校验结果');
  if (tab === 'logs') return stringifyArtifact(run.steps || []);
  if (tab === 'failure') return stringifyArtifact(artifacts.diagnosis || artifacts.failureAnalysis || run.failureAnalysis || run.error || '暂无失败分析');
  if (tab === 'repair') return stringifyArtifact(artifacts.repairDraft || artifacts.repairSuggestion || artifacts.repairedYaml || '暂无修复草稿');
  if (tab === 'bug') return stringifyArtifact(artifacts.bugDraft || artifacts.bug || '暂无缺陷草稿');
  if (tab === 'summary') {
    const s = artifacts.summary || {};
    if (s && typeof s === 'object') {
      return [
        `结论：${s.conclusion || '-'}`,
        `目标：${s.target || run.target || '-'}`,
        `步骤：${s.completed || 0}/${s.totalSteps || 0} 成功，失败 ${s.failed || 0}，跳过 ${s.skipped || 0}`,
        `用例：${s.matchedCount || 0}，执行报告：${s.reportCount || 0}，失败任务：${s.failedJobCount || 0}`,
        `下一步：${(s.nextActions || []).join('；') || '暂无'}`,
      ].join('\n');
    }
    return stringifyArtifact(s || '暂无总结报告');
  }
  if (tab === 'report') {
    const r = artifacts.report || {};
    if (r && typeof r === 'object') {
      const counts = normalizedAgentReportCounts(r);
      const statusCount = (r.jobStatuses || []).length;
      return [
        `状态：${r.status || '-'}`,
        `执行报告：${counts.reportCount} 个`,
        `任务状态：${statusCount} 个`,
        `执行 YAML：${counts.yamlCount} 个`,
        `摘要：${r.summary || '-'}`,
      ].join('\n');
    }
    return stringifyArtifact(r || artifacts.sonicJob || '暂无最终报告');
  }
  return '';
}

function agentDiagnosisHtml(run = currentAgentRun()) {
  const diagnosis = run?.artifacts?.diagnosis || run?.diagnosis;
  if (!diagnosis || typeof diagnosis !== 'object') return '';
  const actions = Array.isArray(diagnosis.nextActions) ? diagnosis.nextActions : [];
  return `
    <div class="agent-diagnosis">
      ${diagnosis.rootCause ? `<div><strong>原因：</strong>${escapeHtml(diagnosis.rootCause)}</div>` : ''}
      ${diagnosis.impact ? `<div><strong>影响：</strong>${escapeHtml(diagnosis.impact)}</div>` : ''}
      ${actions.length ? `<div><strong>建议：</strong>${actions.map(item => `<span class="timeline-chip">${escapeHtml(item)}</span>`).join('')}</div>` : ''}
    </div>
  `;
}

function stringifyArtifact(value) {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value ?? '', null, 2);
  } catch {
    return String(value ?? '');
  }
}

function agentReportIsHtml(item = {}) {
  const reportUrl = String(item.reportUrl || item.report_url || '').trim();
  const localPath = String(item.localPath || item.local_report_path || item.localReportPath || '').trim().toLowerCase();
  return Boolean(reportUrl) || localPath.endsWith('.html') || localPath.endsWith('.htm');
}

function agentReportLooksYaml(item = {}) {
  const file = String(item.file || item.name || item.path || '').trim().toLowerCase();
  const reportUrl = String(item.reportUrl || item.report_url || '').trim();
  return !reportUrl && (file.endsWith('.yaml') || file.endsWith('.yml'));
}

function normalizedAgentReportCounts(report = {}) {
  const rawReports = Array.isArray(report.executionReports)
    ? report.executionReports
    : (Array.isArray(report.reports) ? report.reports : []);
  const executionReports = rawReports.filter(item => agentReportIsHtml(item));
  const yamlFromReports = rawReports.filter(item => !agentReportIsHtml(item) && agentReportLooksYaml(item));
  const yamlRefs = Array.isArray(report.yamlExecutionRefs) ? report.yamlExecutionRefs : [];
  return {
    reportCount: executionReports.length,
    yamlCount: yamlRefs.length + yamlFromReports.length,
  };
}

function agentRiskHits(text) {
  const value = String(text || '');
  return AGENT_RISK_KEYWORDS.filter(keyword => value.includes(keyword));
}

function updateAgentRiskHint() {
  const input = document.getElementById('agent-goal');
  const hint = document.getElementById('agent-risk-hint');
  if (!input || !hint) return;
  const hits = agentRiskHits(input.value);
  hint.classList.toggle('show', hits.length > 0);
  hint.textContent = hits.length
    ? `命中高风险关键词：${hits.join('、')}。Agent 会在执行前进入人工确认。`
    : '';
}


async function loadAgentRuns(options = {}) {
  // round 4: 默认只拉最近 10 条 Agent Run，避免一次性渲染大量历史拖慢首屏
  const limit = Number(options.limit) > 0 ? Number(options.limit) : 10;
  try {
    const url = limit ? `/agent-runs?limit=${encodeURIComponent(limit)}` : '/agent-runs';
    const data = await apiRequest(url);
    let runs = (data.runs || []).map(normalizeAgentRun).filter(Boolean);
    // 后端如忽略 limit 参数，前端再兜底截取
    if (limit && runs.length > limit) runs = runs.slice(0, limit);
    agentRuns = runs;
    AppState.loaded.agentRuns = true;
    AppState.agentRuns = agentRuns;
    renderAgentCenter();
    // round 4: 加载完成后再决定是否开启 Agent 轮询
    if (typeof maybeAdjustAgentPolling === 'function') {
      maybeAdjustAgentPolling(activeWorkflow);
    }
  } catch(e) {
    // silently ignore
  }
}

// round 4: 进入 Agent 类页面时调用，避免重复请求
function ensureAgentRunsLoaded(options = {}) {
  if (AppState.loaded.agentRuns) return Promise.resolve();
  return loadAgentRuns(options);
}

// round 4: 模型配置按需加载，只在“配置→模型配置”进入时拉一次
function ensureModelConfigLoaded() {
  if (AppState.loaded.modelConfig) return Promise.resolve({ providers: aiProviders, router: aiModelRouter });
  if (typeof loadAiModelConfig !== 'function') return Promise.resolve();
  return loadAiModelConfig();
}

// round 4: 根据当前 Agent run 状态判断是否需要轮询。Agent 处于活跃状态才轮询；
// 进入 IDLE/DONE/FAILED/CANCELLED 等终态或离开 Agent 页时立即停止。
function maybeAdjustAgentPolling(sectionKey) {
  const isAgentSection = ['dashboard', 'agent', 'agent_history', 'agent_confirm'].includes(sectionKey);
  if (!isAgentSection) {
    if (typeof stopAgentPolling === 'function') stopAgentPolling();
    return;
  }
  const run = (typeof currentAgentRun === 'function' ? currentAgentRun() : agentCurrentRun) || agentCurrentRun;
  if (!run || !run.runId) {
    if (typeof stopAgentPolling === 'function') stopAgentPolling();
    return;
  }
  const TERMINAL = ['IDLE', 'DONE', 'FINISH', 'FAILED', 'CANCELLED', 'WAIT_CONFIRM', 'WAIT_CONFIRM_RUN', 'WAIT_CONFIRM_BUG'];
  if (TERMINAL.includes(run.status)) {
    if (typeof stopAgentPolling === 'function') stopAgentPolling();
  } else if (typeof startAgentPolling === 'function' && !AppState.polling.agentStatus) {
    startAgentPolling(run.runId);
  }
}

async function loadAgentRunsHistory() {
  // round 4: 历史页主动加载更多（最多 50 条），避免点击“查看历史”仍只看见 10 条
  await loadAgentRuns({ limit: 50, force: true });
  AppState.loaded.agentRuns = true;
  if (agentRuns.length) {
    showToast(`已加载 ${agentRuns.length} 条Agent 运行记录`, 'success');
  } else {
    showToast('暂无 Agent 运行记录', 'info');
  }
  renderAgentHistoryPage();
}

function agentRunCardHtml(run, options = {}) {
  const steps = run.steps || [];
  const confirmations = run.pendingConfirmations || run.confirmations || [];
  const lastStep = steps.slice().reverse().find(step => step.status && step.status !== 'PENDING') || {};
  const mode = agentModeText(run.mode || run.options?.mode || '-');
  const target = run.target || run.options?.goal || run.goal || '未命名任务';
  const status = agentStatusText(run.status);
  const pill = agentRunPillClass(run);
  const cardStatus = agentRunCardStatusClass(run);
  const progress = agentRunProgressPct(run);
  return `
    <div class="workflow-card agent-run-history-card ${escapeHtml(cardStatus)}">
      <div class="agent-run-card-head">
        <span class="status-pill ${escapeHtml(pill)}">${escapeHtml(status)}</span>
        <span class="muted mono">${escapeHtml((run.updatedAt || run.createdAt || '').replace('T', ' ').slice(0, 19))}</span>
      </div>
      <div class="agent-run-title">${escapeHtml(String(target).slice(0, 80))}</div>
      <div class="agent-run-meta">
        <span>运行编号：<b>${escapeHtml(run.runId || '-')}</b></span>
        <span>模式：${escapeHtml(mode)}</span>
        <span>当前步骤：${escapeHtml(agentStepLabel(run.currentStep))}</span>
      </div>
      <div class="agent-run-progress"><div style="width:${Math.max(0, Math.min(100, progress))}%;background:${escapeHtml(agentRunProgressColor(run))};"></div></div>
      <div class="agent-run-summary">${escapeHtml(lastStep.summary || run.summary || run.error || '暂无摘要')}</div>
      ${confirmations.length ? `<div class="generate-hint warn">待确认 ${confirmations.length} 项：${escapeHtml(confirmations.map(item => item.title || item.type || '确认项').join('、'))}</div>` : ''}
      <div class="workflow-card-actions">
        <button class="btn-sm" onclick="selectAgentRun(${jsArg(run.runId || '')});activateWorkflow('agent')">查看轨迹</button>
        ${options.confirm ? `<button class="btn-sm success" onclick="selectAgentRun(${jsArg(run.runId || '')});activateWorkflow('dashboard')">处理确认</button>` : ''}
        ${options.confirm ? `<button class="btn-sm danger" onclick="cancelAgentRunById(${jsArg(run.runId || '')})">取消运行</button>` : ''}
      </div>
    </div>
  `;
}

function renderAgentHistoryPage() {
  const area = document.getElementById('editor-area');
  if (!area) return;
  activeWorkspaceMode = 'agent-history';
  resetYamlToolbarForManager();
  document.getElementById('toolbar-path').innerHTML = '<span>⌂</span> Agent 运行记录';
  document.getElementById('toolbar-help').textContent = '查看Agent历史运行、状态、进度和最后一步摘要。';
  document.getElementById('file-info').textContent = `Agent 运行记录 ${agentRuns.length} 条`;
  area.className = 'editor-area';
  area.innerHTML = `
    <div class="workflow-guide">
      <div class="workflow-hero">
        <div class="workflow-kicker">Agent 运行记录 · 运行轨迹 / 产物 / 失败诊断</div>
        <h2>Agent 运行记录</h2>
        <p>这里集中查看最近 Agent 任务。点“查看轨迹”会把该任务载入右侧 Agent 状态和工作台时间线。</p>
        <div class="workflow-card-actions">
          <button class="btn-sm primary" onclick="loadAgentRunsHistory()">刷新历史</button>
          <button class="btn-sm" onclick="activateWorkflow('dashboard')">回Agent 工作台</button>
        </div>
      </div>
      <div class="workflow-grid history-grid">
        ${agentRuns.length ? agentRuns.map(run => agentRunCardHtml(run)).join('') : renderEmptyState('agent_history')}
      </div>
    </div>
  `;
}

async function renderAgentConfirmPage() {
  await loadAgentRuns({ limit: 50, force: true });
  const area = document.getElementById('editor-area');
  if (!area) return;
  const pending = agentRuns.filter(run => {
    const confirmations = run.pendingConfirmations || run.confirmations || [];
    return confirmations.length || /^WAIT_CONFIRM/.test(run.status || '');
  });
  activeWorkspaceMode = 'agent-confirm';
  resetYamlToolbarForManager();
  document.getElementById('toolbar-path').innerHTML = '<span>⌂</span> 待我确认';
  document.getElementById('toolbar-help').textContent = '高风险动作、YAML 草稿、执行 Sonic 和提交飞书缺陷都必须在这里人工确认。';
  document.getElementById('file-info').textContent = `待确认 ${pending.length} 项`;
  area.className = 'editor-area';
  area.innerHTML = `
    <div class="workflow-guide">
      <div class="workflow-hero">
        <div class="workflow-kicker">人工确认中心 · 草稿确认 / 高风险动作 / 缺陷提交</div>
        <h2>待我确认</h2>
        <p>Agent只有在这里获得明确确认后，才会继续同步至 Sonic 平台、执行高风险动作或提交缺陷草稿。</p>
        <div class="workflow-card-actions">
          <button class="btn-sm primary" onclick="renderAgentConfirmPage()">刷新确认项</button>
          <button class="btn-sm" onclick="activateWorkflow('dashboard')">回Agent 工作台</button>
        </div>
      </div>
      <div class="workflow-grid history-grid">
        ${pending.length ? pending.map(run => agentRunCardHtml(run, { confirm: true })).join('') : renderEmptyState('agent_confirm')}
      </div>
    </div>
  `;
}

function selectAgentRun(runId) {
  const run = agentRuns.find(item => item.runId === runId);
  if (!run) {
    showToast('未找到Agent 运行记录', 'error');
    return;
  }
  agentCurrentRun = normalizeAgentRun(run);
  AppState.currentAgentRun = agentCurrentRun;
  renderAgentCenter();
  showToast('已载入Agent轨迹', 'success');
}

async function confirmAgentStep(runId, confirmationId, decision='confirmed') {
  try {
    const data = await apiRequest(`/agent-runs/${encodeURIComponent(runId)}/confirm`, {
      method: 'POST',
      body: { confirmationId, decision, action: decision }
    });
    agentCurrentRun = normalizeAgentRun(data.run || data);
    if (agentCurrentRun) {
      agentRuns = [agentCurrentRun, ...agentRuns.filter(r => r.runId !== agentCurrentRun.runId)].slice(0, 20);
      showToast('✓ 已确认', 'success');
      stopAgentPollingIfDone(agentCurrentRun);
    }
    renderAgentCenter();
    showAgentWorkbench();
  } catch(e) {
    showToast(e.message || '确认失败', 'error');
  }
}

async function cancelAgentRunById(runId) {
  if (!runId) return;
  if (!confirm(`确认取消Agent 任务 ${runId}？`)) return;
  try {
    const data = await apiRequest(`/agent-runs/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST',
      body: { reason: 'manual' }
    });
    agentCurrentRun = normalizeAgentRun(data.run || data);
    if (agentCurrentRun) {
      agentRuns = [agentCurrentRun, ...agentRuns.filter(r => r.runId !== agentCurrentRun.runId)].slice(0, 20);
      showToast('✓ 已取消', 'success');
    }
    renderAgentCenter();
    showAgentWorkbench();
  } catch(e) {
    showToast(e.message || '取消失败', 'error');
  }
}

async function refreshAgentRun(runId) {
  if (!runId) return null;
  // If this polling callback is for an old run that's no longer current, stop it
  const currentRunId = agentCurrentRun?.runId;
  if (currentRunId && runId !== currentRunId) {
    return null;
  }
  try {
    const data = await apiRequest(`/agent-runs/${encodeURIComponent(runId)}`);
    const run = normalizeAgentRun(data.run || data);
    if (run) {
      agentCurrentRun = run;
      agentRuns = [run, ...agentRuns.filter(item => item.runId !== run.runId)].slice(0, 20);
      stopAgentPollingIfDone(run);
      if (activeWorkflow === 'agent' || activeWorkflow === 'dashboard') {
        // Use lightweight dynamic update when the workbench form is already rendered,
        // so user input in textarea/inputs is not lost during polling.
        if (document.getElementById('agent-goal')) {
          if (typeof updateAgentWorkbenchDynamic === 'function') updateAgentWorkbenchDynamic();
          else showAgentWorkbench();
        } else {
          showAgentWorkbench();
        }
      } else {
        renderJobs();
      }
      renderAgentCenter();
    }
    return run;
  } catch(e) {
    if (activeWorkflow === 'agent' || activeWorkflow === 'dashboard') showToast(e.message || '刷新 Agent 状态失败', 'error');
    return null;
  }
}

async function refreshAgentRuns(showMessage=false) {
  try {
    const data = await apiRequest('/agent-runs');
    agentRuns = (data.runs || []).map(normalizeAgentRun).filter(Boolean);
    if (showMessage) showToast('✓ Agent历史已刷新', 'success');
    if (activeWorkflow === 'agent' || activeWorkflow === 'dashboard') {
      if (document.getElementById('agent-goal') && typeof updateAgentWorkbenchDynamic === 'function') updateAgentWorkbenchDynamic();
      else showAgentWorkbench();
    } else renderJobs();
  } catch(e) {
    if (showMessage) showToast(e.message || '读取Agent历史失败，请确认后端接口已部署', 'error');
  }
}

async function confirmAgentRun(action='CONTINUE', confirmationId='', extra={}) {
  const run = currentAgentRun();
  if (!run?.runId) return;
  try {
    const data = await apiRequest(`/agent-runs/${encodeURIComponent(run.runId)}/confirm`, {
      method: 'POST',
      body: {confirmationId, action, decision: action, comment: action, ...(extra || {})}
    });
    agentCurrentRun = normalizeAgentRun(data.run || data);
    showToast('✓ 已记录确认', 'success');
    if (activeWorkflow === 'agent') showAgentWorkbench();
    else renderJobs();
  } catch(e) {
    showToast(e.message || '确认失败', 'error');
  }
}

async function cancelAgentRun() {
  const run = currentAgentRun();
  if (!run?.runId) return;
  if (!confirm(`确认取消Agent 任务 ${run.runId}？`)) return;
  try {
    const data = await apiRequest(`/agent-runs/${encodeURIComponent(run.runId)}/cancel`, {
      method: 'POST',
      body: {reason: 'manual'}
    });
    agentCurrentRun = normalizeAgentRun(data.run || data);
    showToast('✓ Agent 已取消', 'success');
    if (activeWorkflow === 'agent' || activeWorkflow === 'dashboard') showAgentWorkbench();
    else renderJobs();
  } catch(e) {
    showToast(e.message || '取消失败', 'error');
  }
}

async function copyAgentArtifact() {
  const text = agentArtifactText(agentActiveTab, currentAgentRun());
  try {
    await copyText(text);
    showToast('✓ 当前 Agent 产物已复制', 'success');
  } catch(e) {
    showToast('复制失败，请手动选择文本', 'error');
  }
}

function downloadAgentYaml() {
  const yaml = agentArtifactText('yaml', currentAgentRun());
  if (!yaml || yaml.includes('暂无 Midscene YAML')) {
    showToast('暂无可下载 YAML', 'error');
    return;
  }
  const blob = new Blob([yaml], {type: 'text/yaml;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${safeFilename(currentAgentRun()?.options?.goal || 'agent-yaml')}.yaml`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function safeFilename(text) {
  return String(text || 'agent-yaml').replace(/[\\/:*?"<>|\s]+/g, '-').replace(/-+/g, '-').slice(0, 80) || 'agent-yaml';
}

function openAgentYamlTab() {
  agentActiveTab = 'yaml';
  if (activeWorkflow !== 'agent') activateWorkflow('agent');
  else showAgentWorkbench();
}

function openAgentBugTab() {
  agentActiveTab = 'bug';
  if (activeWorkflow !== 'agent') activateWorkflow('agent');
  else showAgentWorkbench();
}

function renderArtifactItem(label, valueText, data, tabKey) {
  const hasData = !!(data || valueText);
  const displayValue = hasData ? (valueText || '已生成') : '未生成';
  // 只要有 tabKey 且有数据（displayValue 不是 '未生成'），就可点击
  const isLinkable = tabKey && displayValue !== '未生成';
  return `
    <div class="artifact-item">
      <span>${label}</span>
      ${isLinkable ? `<a href="#" onclick="event.preventDefault();agentActiveTab='${tabKey}';showAgentWorkbench();">${escapeHtml(String(displayValue).slice(0, 60))}</a>` : `<span>${escapeHtml(String(displayValue).slice(0, 60))}</span>`}
    </div>
  `;
}

const CONFIRM_CARD_META = {
  case_retrieval_confirm: { label: '确认复用用例', icon: '🔎', highRisk: false, viewTab: 'cases' },
  case_match_uncertain: { label: '确认用例来源', icon: '❔', highRisk: false, viewTab: 'cases' },
  generated_yaml_draft: { label: '确认 YAML 草稿', icon: '📝', highRisk: false, viewTab: 'yaml' },
  high_risk_action: { label: '高风险动作', icon: '⚠️', highRisk: true, viewTab: 'plan' },
  apply_repair: { label: 'YAML 修复草稿', icon: '🛠', highRisk: false, viewTab: 'repair' },
  confirm_apply_yaml: { label: 'YAML 修复草稿', icon: '🛠', highRisk: false, viewTab: 'repair' },
  confirm_baseline_update: { label: '覆盖基线', icon: '📌', highRisk: true, viewTab: 'yaml' },
  bug_draft: { label: '飞书缺陷', icon: '🐞', highRisk: false, viewTab: 'bug' },
  confirm_before_run: { label: '人工复核', icon: '👀', highRisk: false, viewTab: 'plan' }
};

function confirmCandidateListHtml(run, pc) {
  const retrieval = (run && run.artifacts && run.artifacts.caseRetrieval) || {};
  const candidate = (pc && pc.candidate) || {};
  const pcId = pc && pc.id ? String(pc.id) : '';
  const candidates = Array.isArray(retrieval.candidates) && retrieval.candidates.length
    ? retrieval.candidates
    : (Array.isArray(pc && pc.candidates) && pc.candidates.length ? pc.candidates
    : (candidate.file_name || candidate.rel_path ? [candidate] : []));
  const keywords = []
    .concat(retrieval.matchedKeywords || [])
    .concat(candidate.matchedKeywords || candidate.matched_keywords || []);
  const uniqueKeywords = [...new Set(keywords.filter(Boolean).map(String))];
  const keywordHtml = uniqueKeywords.length
    ? `<div class="confirm-keywords"><b>命中关键词</b>${uniqueKeywords.slice(0, 8).map(k => `<span>${escapeHtml(k)}</span>`).join('')}</div>`
    : '<div class="confirm-keywords muted"><b>命中关键词</b><span>未命中明确关键词，建议先查看候选用例</span></div>';
  const candidateHtml = candidates.slice(0, 20).map((item, index) => {
    const path = item.rel_path || [item.dir_name, item.file_name].filter(Boolean).join('/');
    const confidence = item.confidence != null ? `置信度 ${item.confidence}` : '';
    const reasons = item.reasons || item.reason || [];
    const reasonText = Array.isArray(reasons) ? reasons.join('；') : String(reasons || '');
    const inputId = `confirm-candidate-${pcId}-${index}`;
    return `
      <li>
        <label for="${escapeHtml(inputId)}">
          <input id="${escapeHtml(inputId)}" type="checkbox" data-confirm-candidate="${escapeHtml(pcId)}" value="${escapeHtml(path || '')}" checked>
          <span class="confirm-candidate-main">
            <strong>${index + 1}. ${escapeHtml(path || '候选用例')}</strong>
            ${confidence ? `<span>${escapeHtml(confidence)}</span>` : ''}
            ${reasonText ? `<em>${escapeHtml(reasonText.slice(0, 160))}</em>` : ''}
          </span>
        </label>
      </li>
    `;
  }).join('');
  const decision = retrieval.decision || pc.action || '-';
  const confidence = retrieval.confidence != null ? retrieval.confidence : (candidate.confidence != null ? candidate.confidence : '-');
  return `
    <div class="confirm-decision-box">
      <div><b>需要你确认的是：</b>是否复用下面匹配到的已有 YAML，而不是重新生成新脚本。</div>
      <div class="confirm-mini-grid">
        <span>推荐动作</span><strong>${escapeHtml(decision)}</strong>
        <span>置信度</span><strong>${escapeHtml(String(confidence))}</strong>
        <span>执行范围</span><strong>${escapeHtml(String(retrieval.scope || candidate.scope || run?.scope || '-'))}</strong>
      </div>
      ${keywordHtml}
      ${candidateHtml ? `<div class="confirm-select-actions">
        <button class="btn-xs" type="button" onclick="setConfirmCandidateSelection('${escapeHtml(pcId)}', true)">全选</button>
        <button class="btn-xs" type="button" onclick="setConfirmCandidateSelection('${escapeHtml(pcId)}', false)">清空</button>
        <span>只勾选这次要回归的用例</span>
      </div>` : ''}
      ${candidateHtml ? `<ul class="confirm-candidates">${candidateHtml}</ul>` : ''}
    </div>
  `;
}

function confirmCandidateSelector(pcId) {
  const raw = String(pcId || '');
  const escaped = window.CSS && CSS.escape
    ? CSS.escape(raw)
    : raw.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  return `input[data-confirm-candidate="${escaped}"]`;
}

function selectedConfirmCandidatePaths(pcId) {
  return Array.from(document.querySelectorAll(confirmCandidateSelector(pcId)))
    .filter(item => item.checked)
    .map(item => item.value)
    .filter(Boolean);
}

function setConfirmCandidateSelection(pcId, checked) {
  document.querySelectorAll(confirmCandidateSelector(pcId)).forEach(item => {
    item.checked = !!checked;
  });
}

function renderConfirmCard(run, pc) {
  const type = pc && pc.type ? String(pc.type) : 'confirm_before_run';
  const meta = CONFIRM_CARD_META[type] || { label: type, icon: '❓', highRisk: false, viewTab: 'plan' };
  const isHighRisk = !!meta.highRisk || (run && run.riskLevel === 'high');
  const message = pc && pc.message ? String(pc.message) : '';
  const createdAt = pc && pc.createdAt ? String(pc.createdAt).replace('T', ' ').slice(0, 19) : '';
  const pcId = pc && pc.id ? String(pc.id) : '';
  const viewTab = meta.viewTab || 'plan';
  const skipAction = type === 'apply_repair' || type === 'confirm_apply_yaml' ? 'SKIP_REPAIR'
    : (type === 'bug_draft' ? 'SKIP_BUG'
    : (type === 'case_retrieval_confirm' || type === 'case_match_uncertain' ? 'GENERATE_YAML_DRAFT'
    : 'SKIP'));
  const okAction = type === 'apply_repair' || type === 'confirm_apply_yaml' ? 'APPLY_REPAIR_AND_RERUN'
    : (type === 'bug_draft' ? 'CONFIRM_BUG_DRAFT'
    : (type === 'confirm_baseline_update' ? 'APPLY_BASELINE'
    : (type === 'case_retrieval_confirm' || type === 'case_match_uncertain' ? 'CONFIRM_CASE_REUSE'
    : (type === 'generated_yaml_draft' ? 'CONFIRM_YAML_DRAFT'
    : 'CONTINUE'))));

  // Risk details: pending action, risk keyword, impact
  const pendingAction = pc && pc.pendingAction ? pc.pendingAction
    : (pc && pc.summary ? pc.summary : '');
  const riskKeyword = pc && pc.riskKeyword ? pc.riskKeyword
    : (run && run.riskKeyword ? run.riskKeyword
    : (run && run.riskHits && run.riskHits.length ? run.riskHits.join('、') : ''));
  const impactDescription = pc && pc.impactDescription ? pc.impactDescription : '';

  return `
    <div class="confirm-card${isHighRisk ? ' confirm-card-high-risk' : ''}">
      <div class="confirm-card-head">
        <span class="confirm-card-icon">${meta.icon}</span>
        <span class="confirm-card-label">${escapeHtml(meta.label)}</span>
        ${isHighRisk ? '<span class="confirm-card-tag-risk">高风险</span>' : ''}
        ${createdAt ? `<span class="confirm-card-time">${escapeHtml(createdAt)}</span>` : ''}
      </div>
      ${pendingAction ? `<div class="confirm-action">Agent 想要：${escapeHtml(String(pendingAction).slice(0, 200))}</div>` : ''}
      ${riskKeyword ? `<div class="confirm-risk">风险原因：命中关键词 "${escapeHtml(riskKeyword)}"</div>` : ''}
      ${impactDescription ? `<div class="confirm-impact">影响：${escapeHtml(String(impactDescription).slice(0, 200))}</div>` : ''}
      ${message ? `<div class="confirm-card-msg">${escapeHtml(message.slice(0, 240))}</div>` : ''}
      ${(type === 'case_retrieval_confirm' || type === 'case_match_uncertain') ? confirmCandidateListHtml(run, pc) : ''}
      <div class="confirm-card-actions">
        <button class="btn-sm" onclick="agentActiveTab='${viewTab}';showAgentWorkbench();">查看</button>
        <button class="btn-sm success" onclick="confirmAgentRunAction('${escapeHtml(okAction)}','${escapeHtml(pcId)}','${escapeHtml(type)}',${isHighRisk ? 'true' : 'false'})">${type === 'case_retrieval_confirm' || type === 'case_match_uncertain' ? '复用此用例继续' : (type === 'generated_yaml_draft' ? '确认草稿继续' : '确认执行')}</button>
        <button class="btn-sm" onclick="confirmAgentRunAction('${escapeHtml(skipAction)}','${escapeHtml(pcId)}','${escapeHtml(type)}',false)">${type === 'case_retrieval_confirm' || type === 'case_match_uncertain' ? '不复用，生成新 YAML' : '跳过'}</button>
        <button class="btn-sm danger" onclick="cancelAgentRun()">取消运行</button>
      </div>
    </div>
  `;
}

function confirmAgentRunAction(action, pcId, type, highRisk) {
  const isCaseReuse = type === 'case_retrieval_confirm' || type === 'case_match_uncertain';
  const extra = {};
  if (isCaseReuse) {
    extra.selectedCases = selectedConfirmCandidatePaths(pcId);
    if (action === 'CONFIRM_CASE_REUSE' && !extra.selectedCases.length) {
      showToast('请至少选择一条要回归的用例', 'warn');
      return;
    }
  }
  if (highRisk) {
    if (typeof openRiskConfirmModal === 'function') {
      openRiskConfirmModal({
        action: action,
        message: type ? `即将执行：${type}` : '请确认执行高风险动作',
        onConfirm: () => {
          if (typeof confirmAgentRun === 'function') confirmAgentRun(action, pcId, extra);
        }
      });
      return;
    }
    const typed = window.prompt('此操作为高风险，请输入"确认"以继续：');
    if (typed !== '确认') return;
  }
  if (typeof confirmAgentRun === 'function') confirmAgentRun(action, pcId, extra);
}

// Round 5: 高风险二次确认 modal（必须输入"确认"才允许执行）
let riskConfirmCallback = null;

function openRiskConfirmModal(opts = {}) {
  const action = opts.action || '高风险动作';
  const message = opts.message || '该操作可能会影响线上数据/真实环境，请仔细确认。';
  const keywords = Array.isArray(opts.keywords) && opts.keywords.length
    ? opts.keywords
    : (typeof agentRiskHits === 'function' ? agentRiskHits([action, message].join('\n')) : []);
  riskConfirmCallback = typeof opts.onConfirm === 'function' ? opts.onConfirm : null;
  const actionEl = document.getElementById('risk-modal-action');
  const messageEl = document.getElementById('risk-modal-message');
  const keywordsEl = document.getElementById('risk-modal-keywords');
  const input = document.getElementById('risk-modal-input');
  const okBtn = document.getElementById('risk-modal-confirm');
  if (actionEl) actionEl.textContent = action;
  if (messageEl) {
    let highlighted = escapeHtml(message);
    keywords.forEach(kw => {
      const re = new RegExp(escapeRegExp(String(kw)), 'g');
      highlighted = highlighted.replace(re, `<mark class="risk-keyword">${escapeHtml(String(kw))}</mark>`);
    });
    messageEl.innerHTML = highlighted;
  }
  if (keywordsEl) {
    keywordsEl.innerHTML = keywords.length
      ? '命中风险关键词：' + keywords.map(k => `<span class="risk-keyword-chip">${escapeHtml(String(k))}</span>`).join('')
      : '';
  }
  if (input) {
    input.value = '';
    input.oninput = () => {
      if (okBtn) okBtn.disabled = (input.value || '').trim() !== '确认';
    };
  }
  if (okBtn) okBtn.disabled = true;
  const overlay = document.getElementById('modal-risk-confirm');
  if (overlay) overlay.classList.add('show');
  setTimeout(() => input && input.focus(), 50);
}

function closeRiskConfirmModal(confirmed) {
  const overlay = document.getElementById('modal-risk-confirm');
  const input = document.getElementById('risk-modal-input');
  if (confirmed) {
    if (!input || (input.value || '').trim() !== '确认') {
      showToast('请先输入"确认"', 'error');
      return;
    }
  }
  if (overlay) overlay.classList.remove('show');
  const cb = riskConfirmCallback;
  riskConfirmCallback = null;
  if (confirmed && typeof cb === 'function') {
    try { cb(); } catch(e) { showToast('执行失败：' + (e.message || e), 'error'); }
  }
}

function escapeRegExp(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function renderAgentCenter() {
  const title = document.getElementById('jobs-title');
  const count = document.getElementById('jobs-count');
  const list = document.getElementById('jobs-list');
  if (!title || !count || !list) return;
  title.textContent = 'Agent 状态';
  const run = currentAgentRun();
  const elapsedText = agentRunElapsedText(run);
  const progressPct = agentRunProgressPct(run);

  count.textContent = run ? `${agentModeText(run.mode)} · ${agentStatusText(run.status)}${agentRunIsTerminal(run) ? ' · 最近记录' : ''}` : '暂无 Agent 任务';

  if (!run) {
    list.innerHTML = `
      <div class="agent-side">
        <!-- Block 1: 当前运行（空） -->
        <div class="agent-side-card">
          <div class="agent-side-title" style="font-size:14px;font-weight:600;">当前运行</div>
          ${renderEmptyState('agent_history')}
        </div>
        <!-- Block 2: 待我确认（空） -->
        <div class="agent-side-card">
          <div class="agent-side-title" style="font-size:14px;font-weight:600;color:var(--warn);">待我确认</div>
          ${renderEmptyState('agent_confirm')}
        </div>
        <!-- Block 3: 本次产物（空） -->
        <div class="agent-side-card">
          <div class="agent-side-title" style="font-size:14px;font-weight:600;">本次产物</div>
          <div class="artifacts-section">
            ${[
              ['匹配用例', 'cases'],
              ['生成YAML', 'yaml'],
              ['同步至 Sonic 平台', 'sonic'],
              ['执行任务', 'execution'],
              ['报告', 'report'],
              ['失败分析', 'failure'],
              ['修复草稿', 'repair'],
              ['缺陷草稿', 'bug'],
              ['总结', 'summary']
            ].map(([label, key]) => renderArtifactItem(label, '', null, key)).join('')}
          </div>
        </div>
        <!-- Block 4: 最近运行 -->
        <div class="agent-side-card">
          <div class="agent-side-title" style="font-size:14px;font-weight:600;">最近运行</div>
          <div class="agent-timeline">
            ${agentRuns.slice(0, 10).map(r => `
              <div class="agent-timeline-item ${r.status === 'DONE' ? 'success' : (r.status === 'CANCELLED' || r.status === 'FAILED' ? 'failed' : (r.status === 'WAIT_CONFIRM' ? 'waiting' : 'running'))}">
                <strong>${escapeHtml((r.runId || '').slice(0, 20))}</strong>
                <div>${escapeHtml(agentStatusText(r.status))} · ${escapeHtml(agentModeText(r.mode))}</div>
                <div style="font-size:11px;color:var(--text3);">${escapeHtml((r.updatedAt || '').replace('T', ' ').slice(0, 16))}</div>
              </div>
            `).join('') || `${renderEmptyState('agent_history')}`}
          </div>
        </div>
      </div>
    `;
    return;
  }

  const steps = run.steps || [];
  const confirmations = run.pendingConfirmations || [];
  const artifacts = run.artifacts || {};
  const failure = artifacts.failureAnalysis || run.error || '';

  // Build step timeline
  const stepLabels = AUTO_AGENT_STEP_LABELS;
  const visibleSteps = steps.filter(s => s.status !== 'PENDING' || s.step === run.currentStep);

  list.innerHTML = `
    <div class="agent-side">
      <!-- Block 1: 当前 Agent Run -->
      <div class="agent-side-card">
        <div class="agent-side-title" style="font-size:14px;font-weight:600;">${escapeHtml(agentRunPanelTitle(run))}</div>
        <div class="agent-kv">
          <strong>运行编号</strong><span style="font-family:var(--mono);font-size:12px;">${escapeHtml((run.runId || '').slice(0, 24))}</span>
          <strong>模式</strong><span>${escapeHtml(agentModeText(run.mode))}</span>
          <strong>当前步骤</strong><span>${escapeHtml(agentStepLabel(run.currentStep))}</span>
          <strong>目标</strong><span>${escapeHtml((run.target || run.options?.goal || '').slice(0, 60))}</span>
          <strong>进度</strong><span>${progressPct}%</span>
          <strong>已耗时</strong><span>${elapsedText}</span>
          <strong>状态</strong><span class="status-pill ${escapeHtml(agentRunPillClass(run))}">${escapeHtml(agentStatusText(run.status))}</span>
          <strong>风险</strong><span>${escapeHtml(agentRiskText(run.riskLevel))}${run.riskHits?.length ? ' · ' + run.riskHits.join('、') : ''}</span>
        </div>
        <div style="margin-top:8px;height:6px;background:var(--surface3);border-radius:3px;overflow:hidden;">
          <div style="height:100%;width:${progressPct}%;background:${agentRunProgressColor(run)};border-radius:3px;transition:width 0.3s;"></div>
        </div>
      </div>

      <!-- Block 2: 待我确认 -->
      ${confirmations.length > 0 || run.status === 'WAIT_CONFIRM' || run.status === 'WAIT_CONFIRM_RUN' || run.status === 'WAIT_CONFIRM_BUG' ? `
      <div class="agent-side-card agent-confirm">
        <div class="agent-side-title" style="font-size:14px;font-weight:600;color:var(--warn);">
          待我确认 <span class="confirm-badge">${confirmations.length || 1}</span>
        </div>
        ${confirmations.map(pc => renderConfirmCard(run, pc)).join('')}
        ${!confirmations.length && (run.status === 'WAIT_CONFIRM' || run.status === 'WAIT_CONFIRM_RUN') ? renderConfirmCard(run, {
          id: '',
          type: 'confirm_before_run',
          message: 'Agent 等待人工确认，请检查产物后决定是否继续。',
          createdAt: run.updatedAt
        }) : ''}
        ${!confirmations.length && run.status === 'WAIT_CONFIRM_BUG' ? renderConfirmCard(run, {
          id: '',
          type: 'bug_draft',
          message: 'Agent 已生成缺陷草稿，是否提交飞书？',
          createdAt: run.updatedAt
        }) : ''}
      </div>
      ` : ''}

      ${run.status === 'FAILED' ? `
      <div class="agent-side-card">
        <div class="agent-side-title" style="color:var(--danger);">失败处理</div>
        <div class="agent-kv">
          <strong>失败原因</strong><span>${escapeHtml(String(failure || '暂无').slice(0, 200))}</span>
        </div>
        ${agentDiagnosisHtml(run)}
        <div class="agent-actions" style="margin-top:8px;">
          <button class="btn-sm ai" onclick="confirmAgentRun('APPLY_REPAIR_AND_RERUN')">应用修复并重跑</button>
          <button class="btn-sm" onclick="confirmAgentRun('GENERATE_BUG_DRAFT')">生成缺陷草稿</button>
          <button class="btn-sm danger" onclick="cancelAgentRun()">人工处理</button>
        </div>
      </div>
      ` : ''}

      <!-- Block 3: 本次产物 -->
      <div class="agent-side-card">
        <div class="agent-side-title" style="font-size:14px;font-weight:600;">本次产物</div>
        <div class="artifacts-section">
          ${(() => {
            const mc = artifacts.matchedCases || artifacts.cases;
            const mcText = mc ? `${Array.isArray(mc) ? mc.length : 1}个` : (artifacts.matchedCount ? `${artifacts.matchedCount}个` : '');
            return renderArtifactItem('匹配用例', mcText, mc || artifacts.matchedCount, 'cases');
          })()}
          ${renderArtifactItem('生成YAML', (artifacts.generatedYaml || artifacts.yamlDraft) ? '已生成' : '', '', artifacts.generatedYaml || artifacts.yamlDraft, 'yaml')}
          ${renderArtifactItem('同步至 Sonic 平台', artifacts.sonicSync ? (agentToolStatusText(artifacts.sonicSync.status) || '已完成') : '', artifacts.sonicSync, 'sonic')}
          ${renderArtifactItem('执行任务', artifacts.jobId || artifacts.sonicJob || '', '', artifacts.jobId || artifacts.sonicJob, 'execution')}
          ${renderArtifactItem('报告', artifacts.reportId || (artifacts.report ? '已生成' : '') || '', '', artifacts.reportId || artifacts.report, 'report')}
          ${renderArtifactItem('失败分析', artifacts.failureAnalysis ? failureTypeText(artifacts.failureAnalysis.failureType || '已分析') : '', artifacts.failureAnalysis, 'failure')}
          ${renderArtifactItem('修复草稿', artifacts.repairDraftId || (artifacts.repairDraft ? '已生成' : '') || '', '', artifacts.repairDraftId || artifacts.repairDraft, 'repair')}
          ${renderArtifactItem('缺陷草稿', artifacts.bugDraftId || (artifacts.bugDraft ? '已生成' : '') || '', '', artifacts.bugDraftId || artifacts.bugDraft, 'bug')}
          ${renderArtifactItem('总结', artifacts.summary ? '已生成' : '', artifacts.summary, 'summary')}
        </div>
      </div>

      <!-- Block 4: 运行轨迹 -->
      <div class="agent-side-card">
        <div class="agent-side-title" style="font-size:14px;font-weight:600;">运行轨迹</div>
        <div class="agent-timeline">
          ${visibleSteps.length ? visibleSteps.map(step => {
            const stepStatus = step.status || 'PENDING';
            const cls = stepStatus === 'SUCCESS' ? 'success' : (stepStatus === 'FAILED' ? 'failed' : (stepStatus === 'WAIT_CONFIRM' ? 'waiting' : 'running'));
            const icon = cls === 'success' ? '✓' : (cls === 'failed' ? '✗' : (cls === 'waiting' ? '⏸' : '●'));
            return `
              <div class="agent-timeline-item ${cls}">
                <strong>${icon} ${escapeHtml(agentStepLabel(step.step))}</strong>
                <div>${escapeHtml(step.summary || '')}</div>
                <div style="font-size:11px;color:var(--text3);">${step.startedAt ? escapeHtml(step.startedAt.replace('T',' ').slice(11,19)) : ''}${step.endedAt && step.startedAt ? ' · ' + Math.round((Date.parse(step.endedAt)-Date.parse(step.startedAt))/1000) + 's' : ''}</div>
              </div>
            `;
          }).join('') : '<div class="job-empty">等待 Agent开始执行</div>'}
        </div>
      </div>

      <!-- Block 5: 最近运行 -->
      <div class="agent-side-card">
        <div class="agent-side-title" style="font-size:14px;font-weight:600;">最近运行</div>
        <div class="agent-timeline">
          ${agentRuns.slice(0, 10).map(r => `
            <div class="agent-timeline-item ${r.status === 'DONE' ? 'success' : (r.status === 'CANCELLED' || r.status === 'FAILED' ? 'failed' : (r.status === 'WAIT_CONFIRM' ? 'waiting' : 'running'))}">
              <strong>${escapeHtml((r.runId || '').slice(0, 20))}</strong>
              <div>${escapeHtml(agentStatusText(r.status))} · ${escapeHtml(agentModeText(r.mode))}</div>
              <div style="font-size:11px;color:var(--text3);">${escapeHtml((r.updatedAt || '').replace('T', ' ').slice(0, 16))}</div>
            </div>
          `).join('') || `${renderEmptyState('agent_history')}`}
        </div>
      </div>
    </div>
  `;
}

function knowledgeManagerHtml() {
  return `
    <div class="knowledge-manager">
      <div class="knowledge-manager-head">
        <div class="knowledge-manager-title">
          <h2>页面知识库</h2>
          <p>按应用、模块、链路和测试/基线库维护页面资产。生成和修复会按 APP 包名隔离引用。</p>
        </div>
        <div class="knowledge-manager-actions">
          <button class="btn-sm" onclick="refreshKnowledgeManager()">刷新</button>
          <button class="btn-sm success" onclick="openKnowledgeQuickCreate()">新建页面知识</button>
          <button class="btn-sm primary" onclick="openKnowledgeFigmaImport()">导入 Figma</button>
        </div>
      </div>
      <div class="knowledge-manager-filters">
        <select id="km-app" onchange="refreshKnowledgeManagerPages()"></select>
        <select id="km-module" onchange="syncKnowledgeManagerAppFromModule(); refreshKnowledgeManagerPages()"></select>
        <select id="km-tier" onchange="refreshKnowledgeManagerPages()">
          <option value="all">全部知识</option>
          <option value="test">测试库</option>
          <option value="baseline">基线库</option>
        </select>
        <input id="km-search" type="text" placeholder="搜索页面名 / 到达路径 / 关键元素" oninput="renderKnowledgeManagerCards()">
      </div>
      <div class="knowledge-manager-list" id="km-list">
        <div class="generate-knowledge-empty">正在加载页面知识...</div>
      </div>
    </div>
  `;
}

function preflightStatusText(status, ok) {
  if (status === 'error') return '异常';
  if (status === 'warn') return '待处理';
  return ok ? '正常' : '待处理';
}

function preflightDashboardHtml() {
  return `
    <div class="preflight-dashboard">
      <div class="preflight-head">
        <div class="preflight-title">
          <h2>环境体检工作台</h2>
          <p>先体检，再生成、同步和执行。这里会把 Sonic、Runner、模型和旧/重复步骤清理状态集中展示。</p>
        </div>
        <div class="preflight-actions">
          <button class="btn-sm" onclick="loadPreflightDashboard(false)">快速体检</button>
          <button class="btn-sm primary" onclick="loadPreflightDashboard(true)">深度体检</button>
          <button class="btn-sm success" onclick="showGenerateYaml()">开始生成</button>
        </div>
      </div>
      <div class="preflight-next" id="preflight-next">正在加载体检结果...</div>
      <div class="preflight-grid" id="preflight-grid">
        <div class="job-empty">正在体检...</div>
      </div>
    </div>
  `;
}

async function showPreflightDashboard(live=false) {
  if (!(await saveEditorBeforeNavigation('已进入环境体检工作台'))) return;
  resetYamlToolbarForManager();
  if (activeWorkflow !== 'system_config') {
    setActiveWorkflow('system_config');
  }
  document.getElementById('toolbar-path').innerHTML = '<span>📁</span> 环境体检';
  document.getElementById('toolbar-help').textContent = '按体检结果处理异常，全部关键项正常后再同步至 Sonic 平台。';
  document.getElementById('file-info').textContent = '环境体检';
  const area = document.getElementById('editor-area');
  area.className = 'editor-area';
  area.innerHTML = preflightDashboardHtml();
  await loadPreflightDashboard(live);
}

function renderPreflightDashboard(data) {
  const grid = document.getElementById('preflight-grid');
  const next = document.getElementById('preflight-next');
  if (!grid || !next) return;
  const checks = data.checks || [];
  const criticalBad = checks.filter(item => ['task_service', 'dashscope', 'sonic', 'bridge'].includes(item.key) && !item.ok);
  const warn = checks.filter(item => item.status === 'warn' || (!item.ok && !criticalBad.includes(item)));
  if (criticalBad.length) {
    next.textContent = `下一步：先处理 ${criticalBad.map(item => item.title).join('、')}，否则同步和 Sonic 执行会不稳定。`;
  } else if (warn.length) {
    next.textContent = `下一步：核心链路可用，但建议处理 ${warn.map(item => item.title).join('、')}。`;
  } else {
    next.textContent = '下一步：环境正常，可以上传需求新建自动化测试，或把已入库/基线用例同步至 Sonic 平台。';
  }
  grid.innerHTML = checks.map(item => `
    <div class="preflight-card ${escapeHtml(item.status || (item.ok ? 'normal' : 'warn'))}">
      <div class="preflight-card-title">
        <span>${escapeHtml(item.title)}</span>
        <span class="preflight-badge ${escapeHtml(item.status || (item.ok ? 'normal' : 'warn'))}">${escapeHtml(preflightStatusText(item.status, item.ok))}</span>
      </div>
      <div class="preflight-detail">${escapeHtml(item.detail || '-')}</div>
      ${item.action && !item.ok ? `<div class="preflight-detail">建议：${escapeHtml(item.action)}</div>` : ''}
    </div>
  `).join('');
}

async function loadPreflightDashboard(live=false) {
  const grid = document.getElementById('preflight-grid');
  const next = document.getElementById('preflight-next');
  if (grid) grid.innerHTML = '<div class="job-empty">正在体检...</div>';
  if (next) next.textContent = live ? '正在深度体检，包含 Sonic 旧/重复步骤扫描...' : '正在快速体检...';
  try {
    const data = await apiRequest(`/preflight/dashboard${live ? '?live=1' : ''}`);
    renderPreflightDashboard(data);
    showToast(data.ok ? '✓ 环境体检通过' : '环境体检发现待处理项', data.ok ? 'success' : 'error');
  } catch(e) {
    if (next) next.textContent = e.message || '体检失败';
    if (grid) grid.innerHTML = '';
    showToast(e.message || '环境体检失败', 'error');
  }
}

function setActiveWorkflow(sectionKey, options = {}) {
  activeWorkflow = WORKFLOW_SECTIONS[sectionKey] ? sectionKey : 'dashboard';
  updateWorkbenchPanelMode();
  renderWorkflowNav();
  const section = WORKFLOW_SECTIONS[activeWorkflow];
  const help = document.getElementById('toolbar-help');
  if (help) help.textContent = section.help;
  updateContextToolbar();
  if (options.renderGuide && !hasOpenEditor()) showWorkflowGuide(activeWorkflow);
}

function updateWorkbenchPanelMode() {
  const workbench = document.querySelector('.workbench');
  if (!workbench) return;
  const rightPanelWorkflows = new Set([
    'dashboard',
    'agent',
    'agent_history',
    'agent_confirm',
    'execute',
    'baseline',
    'repair'
  ]);
  workbench.classList.toggle('hide-jobs', !rightPanelWorkflows.has(activeWorkflow));
}

// 上下文工具栏：根据当前模块动态展示标题/按钮
const CONTEXT_TOOLBAR_MAP = {
  // Agent模块
  dashboard:        { module: 'agent',    icon: '⌂', title: 'Agent 控制', refreshLabel: '刷新状态', refreshFn: 'loadJobs(true)' },
  agent_history:    { module: 'agent',    icon: '⌂', title: 'Agent 控制', refreshLabel: '刷新状态', refreshFn: 'loadAgentRunsHistory()' },
  agent_confirm:    { module: 'agent',    icon: '⌂', title: 'Agent 控制', refreshLabel: '刷新状态', refreshFn: 'renderAgentCenter()' },
  // 用例 模块
  assets:           { module: 'cases',    icon: '📁', title: '用例操作', refreshLabel: '刷新用例', refreshFn: 'loadModules()' },
  generate:         { module: 'cases',    icon: '✦', title: '用例操作', refreshLabel: '刷新用例', refreshFn: 'loadModules()', showAddYaml: true },
  yaml_edit:        { module: 'cases',    icon: '✎', title: '用例操作', refreshLabel: '刷新用例', refreshFn: 'loadModules()', showAddYaml: true },
  // 执行 模块
  execute:          { module: 'run',      icon: '▶', title: '执行操作', refreshLabel: '刷新任务', refreshFn: 'loadJobs(true)' },
  baseline:         { module: 'run',      icon: '⇄', title: '执行操作', refreshLabel: '刷新任务', refreshFn: 'loadJobs(true)' },
  repair:           { module: 'run',      icon: '🔁', title: '执行操作', refreshLabel: '刷新任务', refreshFn: 'loadJobs(true)' },
  // 报告 模块
  reports:          { module: 'report',   icon: '📊', title: '报告', refreshLabel: '刷新报告', refreshFn: 'loadJobs(true)' },
  failure_analysis: { module: 'report',   icon: '🔍', title: '报告', refreshLabel: '刷新报告', refreshFn: 'loadJobs(true)' },
  bug_drafts:       { module: 'report',   icon: '📝', title: '报告', refreshLabel: '刷新报告', refreshFn: 'loadJobs(true)' },
  // 配置 模块
  config:           { module: 'settings', icon: '⚙', title: '配置', refreshLabel: '刷新配置', refreshFn: 'showModelConfigCenter()' },
  app_config:       { module: 'settings', icon: '⚙', title: '配置', refreshLabel: '刷新配置', refreshFn: 'showTaskApps()' },
  sonic_config:     { module: 'settings', icon: '⚙', title: '配置', refreshLabel: '刷新维护项', refreshFn: 'scanLegacySonicCases("all")' },
  feishu_config:    { module: 'settings', icon: '⚙', title: '配置', refreshLabel: '刷新配置', refreshFn: 'showTaskApps()' },
  system_config:    { module: 'settings', icon: '⚙', title: '配置', refreshLabel: '刷新配置', refreshFn: 'showPreflightDashboard()' }
};

function updateContextToolbar() {
  const ctx = CONTEXT_TOOLBAR_MAP[activeWorkflow] || CONTEXT_TOOLBAR_MAP.dashboard;
  const section = WORKFLOW_SECTIONS[activeWorkflow] || WORKFLOW_SECTIONS.dashboard;
  // 文件被打开时，path 由 openFile 接管，这里不覆盖
  if (!currentFile) {
    const path = document.getElementById('toolbar-path');
    if (path) {
      path.innerHTML = `<span id="toolbar-context-icon">${ctx.icon}</span> <span id="toolbar-context-title">${section.title}</span>`;
    }
  }
  const label = document.getElementById('context-action-label');
  if (label) label.textContent = ctx.title;
  const refreshBtn = document.getElementById('context-action-refresh');
  if (refreshBtn) {
    refreshBtn.textContent = ctx.refreshLabel;
    refreshBtn.dataset.fn = ctx.refreshFn || '';
  }
  const addYamlBtn = document.getElementById('context-action-add-yaml');
  if (addYamlBtn) addYamlBtn.style.display = ctx.showAddYaml ? 'flex' : 'none';
}

function refreshActiveWorkflow() {
  const ctx = CONTEXT_TOOLBAR_MAP[activeWorkflow] || CONTEXT_TOOLBAR_MAP.dashboard;
  const fn = ctx.refreshFn || 'loadJobs(true)';
  try {
    // eslint-disable-next-line no-new-func
    new Function(fn)();
  } catch(e) {
    showToast('刷新失败：' + (e.message || e), 'error');
  }
}

async function activateWorkflow(sectionKey) {
  activeWorkspaceMode = '';
  setActiveWorkflow(sectionKey);
  const section = WORKFLOW_SECTIONS[activeWorkflow];
  if (!(await saveEditorBeforeNavigation(`已进入「${section.title}」流程页`))) return;
  resetYamlToolbarForManager();
  // 用例相关页面显示操作入口，其他页面隐藏
  const assetsHeader = document.getElementById('sidebar-header-assets');
  if (assetsHeader) assetsHeader.style.display = ['assets', 'generate', 'yaml_edit'].includes(activeWorkflow) ? '' : 'none';
  // round 4: 切换页面前，按目标页面动态决定数据加载与轮询
  applyLazyLoadForSection(activeWorkflow);
  if (activeWorkflow === 'repair') {
    showAiRepairCenter();
    // 失败重跑不强制打开左侧目录，避免干扰
    toggleLibrary(false);
    return;
  }
  // Handle config sub-pages
  if (activeWorkflow === 'config') {
    showModelConfigCenter();
    toggleLibrary(false);
    return;
  }
  if (activeWorkflow === 'app_config') {
    showWorkflowGuide('app_config');
    showTaskApps();
    toggleLibrary(false);
    return;
  }
  if (activeWorkflow === 'sonic_config') {
    showWorkflowGuide('sonic_config');
    scanLegacySonicCases('all');
    toggleLibrary(false);
    return;
  }
  if (activeWorkflow === 'feishu_config') {
    showWorkflowGuide('feishu_config');
    toggleLibrary(false);
    return;
  }
  if (activeWorkflow === 'system_config') {
    showPreflightDashboard();
    toggleLibrary(false);
    return;
  }
  if (activeWorkflow === 'knowledge') {
    showKnowledgeManager();
    toggleLibrary(false);
    return;
  }
  // Agent sub-pages
  if (activeWorkflow === 'agent_history') {
    loadAgentRunsHistory();
    toggleLibrary(false);
    return;
  }
  if (activeWorkflow === 'agent_confirm') {
    await renderAgentConfirmPage();
    toggleLibrary(false);
    return;
  }
  // Report sub-pages
  if (activeWorkflow === 'reports') {
    if (typeof showReportsCenter === 'function') showReportsCenter();
    else showWorkflowGuide('reports');
    toggleLibrary(false);
    return;
  }
  if (activeWorkflow === 'failure_analysis') {
    showAiRepairCenter();
    toggleLibrary(false);
    return;
  }
  if (activeWorkflow === 'bug_drafts') {
    showWorkflowGuide('bug_drafts');
    toggleLibrary(false);
    return;
  }
  // YAML edit - show library + workflow guide
  if (activeWorkflow === 'yaml_edit') {
    showWorkflowGuide('yaml_edit');
    toggleLibrary(true);
    return;
  }
  // Toggle module-list visibility based on workflow
  const showLibrary = ['generate', 'execute', 'assets'].includes(activeWorkflow);
  toggleLibrary(showLibrary);
  document.getElementById('toolbar-path').innerHTML = `<span>📁</span> ${escapeHtml(section.title)}`;
  document.getElementById('toolbar-help').textContent = section.help;
  document.getElementById('file-info').textContent = section.title;
  if (activeWorkflow === 'execute' && !hasOpenEditor() && typeof showExecutionCenter === 'function') {
    showExecutionCenter();
    return;
  }
  if (activeWorkflow === 'assets' && !hasOpenEditor() && typeof showAssetsCenter === 'function') {
    showAssetsCenter();
    return;
  }
  if (activeWorkflow === 'dashboard') {
    showAgentWorkbench();
    return;
  }
  showWorkflowGuide(activeWorkflow);
}

// round 4: 集中处理“进入页面才拉数据 + 离开页面停轮询”
function applyLazyLoadForSection(sectionKey) {
  const NEEDS_MODULES = new Set([
    'assets', 'generate', 'execute', 'baseline', 'repair', 'yaml_edit', 'app_config'
  ]);
  const NEEDS_JOBS_POLLING = new Set([
    'execute', 'baseline', 'repair'
  ]);
  const NEEDS_RUNNERS = new Set([
    'execute', 'generate', 'baseline', 'repair'
  ]);

  if (NEEDS_MODULES.has(sectionKey) && typeof ensureModulesLoaded === 'function') {
    ensureModulesLoaded().then(() => {
      if (sectionKey === 'execute' && activeWorkflow === 'execute' && !hasOpenEditor() && typeof showExecutionCenter === 'function') {
        showExecutionCenter();
      }
      if (sectionKey === 'assets' && activeWorkflow === 'assets' && !hasOpenEditor() && typeof showAssetsCenter === 'function') {
        showAssetsCenter();
      }
    }).catch(() => {});
  }

  if (NEEDS_RUNNERS.has(sectionKey) && typeof ensureRunnersLoaded === 'function') {
    ensureRunnersLoaded().then(() => {
      if (sectionKey === 'execute' && activeWorkflow === 'execute' && !hasOpenEditor() && typeof showExecutionCenter === 'function') {
        showExecutionCenter();
      }
    }).catch(() => {});
  }

  if (NEEDS_JOBS_POLLING.has(sectionKey)) {
    if (typeof ensureJobsLoaded === 'function') {
      ensureJobsLoaded().catch(() => {});
    }
    if (typeof startJobsAutoRefresh === 'function' && !AppState.polling.jobs) {
      startJobsAutoRefresh();
    }
  } else if (typeof stopJobsAutoRefresh === 'function') {
    // 离开执行/报告类页面时停止 jobs 轮询，避免无谓后台请求
    stopJobsAutoRefresh();
  }

  // Agent 类页面进入时只补一次最近 10 条 runs
  const isAgentSection = ['dashboard', 'agent', 'agent_history', 'agent_confirm'].includes(sectionKey);
  if (isAgentSection && typeof ensureAgentRunsLoaded === 'function') {
    ensureAgentRunsLoaded({ limit: 10 }).then(() => {
      // 加载完成后，如果有活跃 run 且轮询未启动，自动恢复轮询
      if (typeof maybeAdjustAgentPolling === 'function') maybeAdjustAgentPolling(sectionKey);
    }).catch(() => {});
  }

  // 模型配置只在“配置→模型配置”按需加载
  if (sectionKey === 'config' && typeof ensureModelConfigLoaded === 'function') {
    ensureModelConfigLoaded().catch(() => {});
  }

  // 离开 Agent 页且 run 已结束时停止 Agent 轮询，进入活跃 run 的 Agent 页时按需开启
  if (typeof maybeAdjustAgentPolling === 'function') {
    maybeAdjustAgentPolling(sectionKey);
  }
}

function toggleLibrary(show) {
  const moduleList = document.getElementById('module-list');
  const libraryHead = document.querySelector('.library-head');
  const sidebarSearch = document.querySelector('.sidebar-search');
  // 用例目录已搬到各工作区中间区域，左侧只保留主导航。
  if (moduleList) moduleList.style.display = 'none';
  if (libraryHead) libraryHead.style.display = 'none';
  if (sidebarSearch) sidebarSearch.style.display = 'none';
}

function showFigmaPlan() {
  showToast('Figma 接入建议：先保存 Figma 文件链接和页面节点，服务端用 API 拉取节点文案、截图和组件层级后进入生成流程。');
}

function resetYamlToolbarForManager() {
  currentFile = null;
  editorDirty = false;
  editorInitialContent = '';
  const hideIds = [
    'btn-save',
    'btn-copy-file',
    'btn-move-file',
    'btn-rename-file',
    'btn-history-file',
    'btn-baseline-refs',
    'btn-generation-review',
    'btn-sonic-status',
    'btn-publish-sonic',
    'file-status-select',
    'btn-run-file',
    'btn-run-task',
    'btn-repair-task',
    'btn-repair-file',
    'toggle-refs-panel',
    'toggle-case-panel'
  ];
  hideIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  setFileContextVisible(false);
  updateToolbarState();
}

function resetYamlToolbarForDirectory() {
  currentFile = null;
  editorDirty = false;
  editorInitialContent = '';
  const hideIds = [
    'btn-save',
    'btn-copy-file',
    'btn-move-file',
    'btn-rename-file',
    'btn-history-file',
    'btn-baseline-refs',
    'btn-generation-review',
    'btn-sonic-status',
    'btn-publish-sonic',
    'file-status-select',
    'btn-run-file',
    'btn-run-task',
    'btn-repair-task',
    'btn-repair-file',
    'toggle-refs-panel',
    'toggle-case-panel'
  ];
  hideIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  setFileContextVisible(false);
  updateToolbarState();
}

function moduleDirectoryHtml(mod) {
  const files = modules[mod] || [];
  const app = moduleApp(mod);
  const appText = app ? `${app.name || app.package} · ${app.package}` : '未绑定应用，可在应用分组里维护包名和中文名';
  const moduleStats = mergeYamlStats(files.map(file => yamlStatsForFile(mod, file)));
  const loadedInModule = files.filter(file => yamlStatsForFile(mod, file).loaded).length;
  const statsText = loadedInModule >= files.length
    ? '等级统计已完成'
    : `等级统计加载中 ${loadedInModule}/${files.length}`;
  const visibleFiles = files
    .filter(file => yamlStatsMatchesFilter(yamlStatsForFile(mod, file), modulePriorityFilter))
    .sort((a, b) => yamlStatsRank(yamlStatsForFile(mod, b)) - yamlStatsRank(yamlStatsForFile(mod, a)) || a.localeCompare(b, 'zh-CN'));
  const pageInfo = pagedItems(visibleFiles, moduleDirectoryPage, MODULE_DIRECTORY_PAGE_SIZE);
  moduleDirectoryPage = pageInfo.page;
  const cards = pageInfo.items.map(file => {
    const meta = fileMeta(mod, file);
    const job = latestJobForFile(mod, file) || {};
    const status = job.status || '';
    const stats = yamlStatsForFile(mod, file);
    const hotClass = stats.p0 ? 'hot-p0' : (stats.p1 ? 'hot-p1' : (stats.smoke ? 'hot-smoke' : ''));
    return `
      <div class="module-file-card ${hotClass}">
        <div class="module-file-name">${escapeHtml(file)}</div>
        <div class="module-file-priority">${prioritySummaryHtml(stats, true)}</div>
        <div class="module-file-meta">
          状态：${escapeHtml(lifecycleText(meta.status))}${status ? ` · 最近执行：${escapeHtml(jobStatusText(status))}` : ''}<br>
          ${job.finished_at || job.updated_at ? `时间：${escapeHtml(job.finished_at || job.updated_at)}` : '尚无执行记录'}
        </div>
        <div class="module-file-actions">
          <button class="btn-sm primary" onclick="openFile(${jsArg(mod)}, ${jsArg(file)})">编辑</button>
          <button class="btn-sm success" onclick="openFile(${jsArg(mod)}, ${jsArg(file)}).then(()=>showRunCurrentFile())">执行</button>
          <button class="btn-sm" onclick="openFile(${jsArg(mod)}, ${jsArg(file)}).then(()=>showFileHistory())">历史</button>
        </div>
      </div>
    `;
  }).join('');
  return `
    <div class="module-directory">
      <div class="module-directory-head">
        <div class="module-directory-title">
          <h2>${escapeHtml(mod)}</h2>
          <p>${escapeHtml(appText)}<br>共 ${files.length} 个 YAML，筛选后 ${visibleFiles.length} 个，本页显示 ${pageInfo.total ? pageInfo.start + 1 : 0}-${pageInfo.end} 个。${escapeHtml(statsText)}。</p>
          <div class="module-priority-summary">${prioritySummaryHtml(moduleStats)}</div>
          <div class="module-priority-tools">
            ${['all','hot','p0','p1','smoke'].map(filter => `<button class="priority-filter-btn ${modulePriorityFilter === filter ? 'active' : ''}" onclick="setModulePriorityFilter(${jsArg(filter)})">${modulePriorityFilterLabel(filter)}</button>`).join('')}
          </div>
        </div>
        <div class="module-directory-actions">
          <button class="btn-sm" onclick="selectCurrentModuleFiles()">全选模块文件</button>
          <button class="btn-sm success" onclick="publishCurrentModuleToSonic()">同步当前模块至 Sonic 平台</button>
          <button class="btn-sm" onclick="showAddTask()">新建 YAML</button>
          <button class="btn-sm" onclick="showUpload()">上传 YAML</button>
          <button class="btn-sm danger" onclick="deleteCurrentModule()">删除模块</button>
        </div>
      </div>
      ${files.length ? (visibleFiles.length ? `<div class="module-file-grid">${cards}</div>${paginationHtml(pageInfo, 'setModuleDirectoryPage')}` : `<div class="workflow-guide"><div class="workflow-hero"><h2>没有匹配 ${escapeHtml(modulePriorityFilterLabel(modulePriorityFilter))} 的 YAML</h2><p>可以切回全部，或者先在 YAML 编辑页调整用例等级。</p></div></div>`) : '<div class="workflow-guide"><div class="workflow-hero"><h2>这个模块还没有 YAML</h2><p>可以新建、上传，或者从需求/设计稿生成一个可执行 YAML。</p></div></div>'}
    </div>
  `;
}

async function showModuleDirectory(mod) {
  if (!mod || !modules[mod]) return;
  if (!(await saveEditorBeforeNavigation())) return;
  activeWorkspaceMode = '';
  if (currentModule !== mod) resetModuleDirectoryPage();
  currentModule = mod;
  resetYamlToolbarForDirectory();
  if (activeWorkflow === 'assets') {
    renderWorkflowNav();
    renderModules();
    showAssetsCenter();
    refreshModuleDirectoryStats(mod).catch(() => {});
    return;
  }
  if (!['generate', 'execute', 'repair', 'baseline'].includes(activeWorkflow)) {
    setActiveWorkflow('baseline');
  } else {
    renderWorkflowNav();
  }
  document.getElementById('toolbar-path').innerHTML = `<span>📁</span> ${escapeHtml(mod)}`;
  document.getElementById('toolbar-help').textContent = '当前是模块目录页；点击 YAML 文件进入编辑，或在这里进行模块级批量管理。';
  document.getElementById('file-info').textContent = `${mod} · ${modules[mod].length} 个 YAML`;
  const area = document.getElementById('editor-area');
  area.className = 'editor-area';
  area.innerHTML = moduleDirectoryHtml(mod);
  updateToolbarState();
  renderModules();
  refreshModuleDirectoryStats(mod).catch(() => {});
}

async function showKnowledgeManager() {
  setActiveWorkflow('assets');
  resetYamlToolbarForManager();
  currentModule = currentModule || Object.keys(modules)[0] || null;
  document.getElementById('toolbar-path').innerHTML = '<span>📚</span> 页面知识库';
  document.getElementById('toolbar-help').textContent = '集中维护 APP 页面、Figma 设计稿、测试库和基线库。';
  document.getElementById('file-info').textContent = '页面知识库';
  const area = document.getElementById('editor-area');
  area.className = 'editor-area';
  area.innerHTML = knowledgeManagerHtml();
  updateToolbarState('页面知识库');
  renderModules();
  await refreshKnowledgeManager();
}

async function refreshKnowledgeManager() {
  if (!managerKnowledgeAppsLoaded) {
    await loadKnowledgeApps();
    managerKnowledgeAppsLoaded = true;
  } else {
    await loadKnowledgeApps();
  }
  renderKnowledgeManagerFilters();
  await refreshKnowledgeManagerPages();
}

async function refreshKnowledgeManagerIfVisible() {
  if (!document.querySelector('.knowledge-manager')) return;
  await refreshKnowledgeManager();
}

function renderKnowledgeManagerFilters() {
  const appSelect = document.getElementById('km-app');
  const moduleSelect = document.getElementById('km-module');
  if (!appSelect || !moduleSelect) return;
  const currentApp = currentModuleAppPackage() || document.getElementById('knowledge-app-package')?.value.trim() || 'com.kfb.model';
  const apps = allAppPackages();
  appSelect.innerHTML = apps.map(app => `<option value="${escapeHtml(app)}">${escapeHtml(appDisplayLabel(app))}</option>`).join('');
  if (apps.includes(currentApp)) appSelect.value = currentApp;
  moduleSelect.innerHTML = '<option value="">全部模块</option>' + Object.keys(modules).sort((a,b)=>a.localeCompare(b,'zh-CN')).map(mod => `<option value="${escapeHtml(mod)}">${escapeHtml(mod)}</option>`).join('');
  if (currentModule && modules[currentModule]) moduleSelect.value = currentModule;
}

function syncKnowledgeManagerAppFromModule() {
  const mod = document.getElementById('km-module')?.value || '';
  const pkg = moduleAppPackage(mod);
  if (pkg) document.getElementById('km-app').value = pkg;
}

async function refreshKnowledgeManagerPages() {
  const appPackage = document.getElementById('km-app')?.value || currentModuleAppPackage() || 'com.kfb.model';
  const tier = document.getElementById('km-tier')?.value || 'all';
  const list = document.getElementById('km-list');
  if (list) list.innerHTML = '<div class="generate-knowledge-empty">正在加载页面知识...</div>';
  try {
    const data = await apiRequest(`/knowledge/pages?app_package=${encodeURIComponent(appPackage)}&tier=${encodeURIComponent(tier)}`);
    managerKnowledgePages = data.pages || [];
    renderKnowledgeManagerCards();
  } catch(e) {
    managerKnowledgePages = [];
    if (list) list.innerHTML = `<div class="generate-knowledge-empty">${escapeHtml(e.message || '读取页面知识失败')}</div>`;
  }
}

function renderKnowledgeManagerCards() {
  const list = document.getElementById('km-list');
  if (!list) return;
  const moduleFilter = document.getElementById('km-module')?.value || '';
  const keyword = (document.getElementById('km-search')?.value || '').trim().toLowerCase();
  const moduleText = moduleFilter ? `${moduleFilter} ` : '';
  const pages = managerKnowledgePages.filter(page => {
    const haystack = [
      moduleText,
      page.page_name,
      page.page_id,
      page.route,
      page.description,
      (page.key_elements || []).join(' '),
      (page.common_assertions || []).join(' '),
      page.tier
    ].join(' / ').toLowerCase();
    return !keyword || haystack.includes(keyword);
  });
  if (!pages.length) {
    list.innerHTML = '<div class="generate-knowledge-empty">没有匹配的页面知识。可以新建页面或导入 Figma。</div>';
    return;
  }
  list.innerHTML = pages.map(page => `
    <div class="knowledge-card">
      <div class="knowledge-card-title">
        <div class="knowledge-card-name">${escapeHtml(page.page_name || page.page_id)}</div>
        <span class="knowledge-badge ${page.tier === 'baseline' ? 'baseline' : ''}">${page.tier === 'baseline' ? '基线库' : '测试库'}</span>
      </div>
      <div class="knowledge-card-meta">${escapeHtml(page.route || page.page_id || '无路径说明')}</div>
      <div class="knowledge-card-desc">${escapeHtml(page.description || (page.key_elements || []).slice(0, 6).join('、') || '未维护页面说明')}</div>
      <div class="knowledge-card-actions">
        <button class="btn-sm" onclick="openKnowledgePageEditor(${jsArg(page.page_id)})">编辑</button>
        <button class="btn-sm" onclick="toggleManagerKnowledgeTier(${jsArg(page.page_id)})">${page.tier === 'baseline' ? '移回测试库' : '标记基线'}</button>
        <button class="btn-sm danger" onclick="deleteManagerKnowledgePage(${jsArg(page.app_package)}, ${jsArg(page.page_id)})">删除</button>
      </div>
    </div>
  `).join('');
}

async function openKnowledgeQuickCreate() {
  const app = document.getElementById('km-app')?.value || currentModuleAppPackage() || 'com.kfb.model';
  document.getElementById('knowledge-app-package').value = app;
  await showKnowledge();
  clearKnowledgeForm();
}

async function openKnowledgeFigmaImport() {
  const app = document.getElementById('km-app')?.value || currentModuleAppPackage() || 'com.kfb.model';
  document.getElementById('knowledge-app-package').value = app;
  await showKnowledge();
  document.getElementById('figma-url')?.focus();
}

async function openKnowledgePageEditor(pageId) {
  const app = document.getElementById('km-app')?.value || 'com.kfb.model';
  document.getElementById('knowledge-app-package').value = app;
  await showKnowledge();
  editKnowledgePage(pageId);
}

async function toggleManagerKnowledgeTier(pageId) {
  const page = managerKnowledgePages.find(item => item.page_id === pageId);
  if (!page) return;
  const nextTier = page.tier === 'baseline' ? 'test' : 'baseline';
  try {
    const payload = {...page, tier: nextTier, screenshot: null};
    const data = await apiRequest('/knowledge/page', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    await refreshKnowledgeManager();
    showToast(nextTier === 'baseline' ? '✓ 已标记为基线库' : '✓ 已移回测试库', 'success');
  } catch(e) {
    showToast(e.message || '切换知识库类型失败', 'error');
  }
}

async function deleteManagerKnowledgePage(appPackage, pageId) {
  if (!confirm(`删除页面知识「${pageId}」？`)) return;
  try {
    await apiRequest(`/knowledge/page?app_package=${encodeURIComponent(appPackage)}&page_id=${encodeURIComponent(pageId)}`, { method: 'DELETE' });
    await refreshKnowledgeManager();
    showToast('✓ 已删除页面知识', 'success');
  } catch(e) {
    showToast(e.message || '删除失败', 'error');
  }
}

function showIntegrationPlan() {
  showToast('外部平台建议：先稳定 Runner 回传，再接 Figma 设计源和飞书缺陷平台。');
}

function requireCurrentYaml(actionName='操作') {
  if (currentModule && currentFile) return true;
  showToast(`请先从左侧选择一个 YAML 文件，再进行${actionName}`, 'error');
  return false;
}

function safeRunCurrentFile() {
  if (!requireCurrentYaml('整文件执行')) return;
  showRunCurrentFile();
}

function safeRunSelectedTask() {
  if (!requireCurrentYaml('单条执行')) return;
  showRunSelectedTask();
}

function safeRepairCurrentFile() {
  if (!requireCurrentYaml('文件修复')) return;
  repairCurrentFile();
}

function safeRepairSelectedTask() {
  if (!requireCurrentYaml('单条修复')) return;
  repairSelectedTask();
}

function updateRepairProgress(button, progress, message) {
  const safeProgress = Math.max(0, Math.min(100, Math.round(Number(progress || 0))));
  if (button) button.textContent = `修复中 ${safeProgress}%`;
  const info = document.getElementById('file-info');
  if (info) info.textContent = `${message || '正在修复'} · ${safeProgress}%`;
  updateToolbarState(`修复 ${safeProgress}%`);
}

function repairResultText(data) {
  const parts = [];
  if (data.mode) parts.push(`模式：${data.mode === 'static' ? '静态体检' : data.mode}`);
  if (data.module || data.file) parts.push(`文件：${data.module || currentModule}/${data.file || currentFile}`);
  if (data.taskName || data.task_name) parts.push(`用例：${data.taskName || data.task_name}`);
  if (typeof data.changed_line_count !== 'undefined') parts.push(`改动行数：${data.changed_line_count}`);
  if (data.before_version?.id) parts.push(`修复前版本：${data.before_version.id}`);
  if (data.repair_dir) parts.push(`修复目录：${data.repair_dir}`);
  return parts.join('\n');
}

function showRepairResult(data, title='AI 修复结果') {
  const modal = document.getElementById('modal-repair-result');
  const titleEl = document.getElementById('repair-result-title');
  const body = document.getElementById('repair-result-summary');
  if (!modal || !titleEl || !body) return;
  const changes = Array.isArray(data.changes) ? data.changes.filter(Boolean) : [];
  const warnings = Array.isArray(data.warnings) ? data.warnings.filter(Boolean) : [];
  const yamlWarnings = Array.isArray(data.yamlCheck?.warnings) ? data.yamlCheck.warnings.filter(Boolean) : [];
  const safetyWarnings = Array.isArray(data.safetyCheck?.warnings) ? data.safetyCheck.warnings.filter(Boolean) : [];
  const business = data.businessFlowCheck || {};
  const businessIssues = []
    .concat(business.missing || [])
    .concat(business.inversions || [])
    .concat(business.warnings || [])
    .filter(Boolean);
  const nextJob = data.next_job || {};
  const nextJobId = nextJob.job_id || data.next_job_id || '';
  const summaryText = repairResultText(data) || '已完成修复并重新打开当前 YAML。';
  const changeHtml = changes.length
    ? `<div class="repair-change-list">${changes.map(item => `<div class="repair-change-item">${escapeHtml(item)}</div>`).join('')}</div>`
    : '<div class="repair-result-box">后端未返回具体变更点；请结合历史版本或当前 YAML 内容复核。</div>';
  const checkTips = warnings.concat(yamlWarnings, safetyWarnings, businessIssues).slice(0, 8);
  const warningHtml = checkTips.length
    ? `<div class="repair-result-box"><strong>检查提示</strong>${escapeHtml(checkTips.join('\n'))}</div>`
    : '';
  const diffHtml = data.diff_summary
    ? `<div class="repair-result-box"><strong>Diff 摘要</strong><pre>${escapeHtml(data.diff_summary)}</pre></div>`
    : '';
  const nextJobHtml = nextJobId
    ? `<button class="btn-sm success" onclick="focusJob(${jsArg(nextJobId)});closeModal('modal-repair-result')">查看重跑任务</button>`
    : '';
  titleEl.textContent = title;
  body.innerHTML = `
    <div class="repair-result-box"><strong>结果摘要</strong>${escapeHtml(summaryText)}</div>
    <div class="repair-result-box"><strong>主要变更</strong>${changeHtml}</div>
    ${warningHtml}
    ${diffHtml}
    <div class="repair-result-actions">
      ${nextJobHtml}
      <button class="btn-sm" onclick="closeModal('modal-repair-result');showFileHistory()">历史版本</button>
    </div>
  `;
  modal.classList.add('show');
}

function safeShowBaselineRefs() {
  if (!requireCurrentYaml('基线辅助截图绑定')) return;
  showBaselineRefs();
}

function safeShowFileHistory() {
  if (!requireCurrentYaml('历史版本查看')) return;
  showFileHistory();
}

function renderAppFilter() {
  const filter = document.getElementById('app-filter');
  if (!filter) return;
  const previous = filter.value;
  filter.innerHTML = '<option value="">全部应用</option>';
  taskApps.forEach(app => {
    const opt = document.createElement('option');
    opt.value = app.package;
    opt.textContent = `${app.name || app.package} (${(app.modules || []).length})`;
    filter.appendChild(opt);
  });
  if (previous && taskApps.some(app => app.package === previous)) filter.value = previous;
}

function latestJobForFile(mod, file) {
  for (let i = latestJobs.length - 1; i >= 0; i--) {
    const job = latestJobs[i];
    if (job.module === mod && job.file === file) return job;
  }
  return null;
}

function setLibraryView(view) {
  libraryView = view || 'module';
  resetAssetListPage();
  resetModuleDirectoryPage();
  document.querySelectorAll('.library-view-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.libraryView === libraryView);
  });
  renderModules();
}

function moduleFileRows() {
  const rows = [];
  Object.entries(modules).forEach(([mod, files]) => {
    files.forEach(file => {
      const job = latestJobForFile(mod, file) || {};
      rows.push({
        mod,
        file,
        job,
        meta: fileMeta(mod, file),
        time: Date.parse((job.finished_at || job.updated_at || job.started_at || '').replace(' ', 'T')) || 0
      });
    });
  });
  return rows;
}

function clampListPage(page, total, pageSize) {
  const totalPages = Math.max(1, Math.ceil(Number(total || 0) / Number(pageSize || 1)));
  return Math.min(Math.max(1, Number(page || 1)), totalPages);
}

function pagedItems(items, page, pageSize) {
  const all = Array.isArray(items) ? items : [];
  const size = Math.max(1, Number(pageSize || 1));
  const current = clampListPage(page, all.length, size);
  const start = (current - 1) * size;
  const end = Math.min(all.length, start + size);
  return {
    items: all.slice(start, end),
    page: current,
    pageSize: size,
    total: all.length,
    totalPages: Math.max(1, Math.ceil(all.length / size)),
    start,
    end,
  };
}

function paginationHtml(info, setterName) {
  if (!info || info.total <= info.pageSize) return '';
  const from = info.total ? info.start + 1 : 0;
  return `
    <div class="list-pagination">
      <span>显示 ${from}-${info.end} / ${info.total}</span>
      <div>
        <button class="btn-sm" onclick="${setterName}(${info.page - 1})" ${info.page <= 1 ? 'disabled' : ''}>上一页</button>
        <strong>${info.page} / ${info.totalPages}</strong>
        <button class="btn-sm" onclick="${setterName}(${info.page + 1})" ${info.page >= info.totalPages ? 'disabled' : ''}>下一页</button>
      </div>
    </div>
  `;
}

function resetAssetListPage() {
  assetListPage = 1;
}

function setAssetListPage(page) {
  assetListPage = page;
  showAssetsCenter();
}

function resetModuleDirectoryPage() {
  moduleDirectoryPage = 1;
}

function setModuleDirectoryPage(page) {
  moduleDirectoryPage = page;
  if (currentModule && modules[currentModule] && !hasOpenEditor()) {
    const area = document.getElementById('editor-area');
    if (area) area.innerHTML = moduleDirectoryHtml(currentModule);
  }
}

function assetRowsForCurrentFilters() {
  const keyword = (document.getElementById('asset-search')?.value || document.getElementById('task-search')?.value || '').trim().toLowerCase();
  const appFilter = document.getElementById('asset-app-filter')?.value || document.getElementById('app-filter')?.value || '';
  const selectedApp = taskApps.find(app => app.package === appFilter);
  const allowedModules = selectedApp ? new Set(selectedApp.modules || []) : null;
  let rows = moduleFileRows();
  if (allowedModules) rows = rows.filter(row => allowedModules.has(row.mod));
  if (currentModule && modules[currentModule]) rows = rows.filter(row => row.mod === currentModule);
  if (libraryView === 'recent') rows = rows.filter(row => row.time > 0).sort((a, b) => b.time - a.time);
  else if (libraryView === 'failed') rows = rows.filter(row => row.job.status === 'failed').sort((a, b) => b.time - a.time);
  else if (libraryView === 'baseline') rows = rows.filter(row => ['baseline', 'active'].includes(row.meta.status || '')).sort((a, b) => a.mod.localeCompare(b.mod, 'zh-CN'));
  else rows = rows.sort((a, b) => a.mod.localeCompare(b.mod, 'zh-CN') || a.file.localeCompare(b.file, 'zh-CN'));
  if (keyword) {
    rows = rows.filter(row => `${row.mod}/${row.file}/${lifecycleText(row.meta.status)}/${jobStatusText(row.job.status || '')}`.toLowerCase().includes(keyword));
  }
  return rows;
}

function showAllAssetModules() {
  currentModule = null;
  currentFile = null;
  resetAssetListPage();
  resetYamlToolbarForManager();
  renderModules();
  showAssetsCenter();
}

function selectCurrentAssetRows() {
  const rows = assetRowsForCurrentFilters();
  rows.forEach(row => selectedFiles.add(fileKey(row.mod, row.file)));
  renderModules();
  showAssetsCenter();
  showToast(`已选择当前列表 ${rows.length} 个 YAML`, 'success');
}

function toggleCurrentAssetRows(checked) {
  const rows = assetRowsForCurrentFilters();
  rows.forEach(row => {
    const key = fileKey(row.mod, row.file);
    if (checked) selectedFiles.add(key);
    else selectedFiles.delete(key);
  });
  renderModules();
  showAssetsCenter();
  showToast(checked ? `已选择当前列表 ${rows.length} 个 YAML` : '已取消当前列表选择', 'success');
}

function clearAssetSelection() {
  clearSelectedFiles();
  showAssetsCenter();
}

function assetFileOp(mod, file, op) {
  currentModule = mod;
  currentFile = file;
  renderModules();
  updateToolbarState();
  showFileOp(op);
}

async function deleteAssetFile(mod, file) {
  await deleteFile(mod, file);
  if (activeWorkflow === 'assets') showAssetsCenter();
}

function showAssetsCenter() {
  const area = document.getElementById('editor-area');
  if (!area) return;
  const appValue = document.getElementById('asset-app-filter')?.value || document.getElementById('app-filter')?.value || '';
  const searchValue = document.getElementById('asset-search')?.value || document.getElementById('task-search')?.value || '';
  const filterKey = `${appValue}::${searchValue}::${currentModule || ''}::${libraryView}`;
  if (lastAssetFilterKey && lastAssetFilterKey !== filterKey) resetAssetListPage();
  lastAssetFilterKey = filterKey;
  const rows = assetRowsForCurrentFilters();
  const selectedInRows = selectedAssetRowsForCurrentFilters(rows);
  const selectedTotal = selectedSonicFiles().length;
  const pageInfo = pagedItems(rows, assetListPage, ASSET_PAGE_SIZE);
  assetListPage = pageInfo.page;
  const pageRows = pageInfo.items;
  const allRowsSelected = rows.length > 0 && rows.every(row => selectedFiles.has(fileKey(row.mod, row.file)));
  const summary = {
    total: rows.length,
    failed: rows.filter(row => row.job.status === 'failed').length,
    baseline: rows.filter(row => ['baseline', 'active'].includes(row.meta.status || '')).length,
    selected: selectedInRows.length,
    selectedTotal
  };
  const moduleCount = currentModule ? 1 : Object.keys(modules).length;
  area.className = 'editor-area assets-center';
  area.innerHTML = `
    <div class="assets-page">
      <div class="assets-hero">
        <div>
          <div class="workflow-kicker">用例资产 · YAML 文件 / 状态 / 最近执行</div>
          <h2>用例资产</h2>
          <p>左侧用于快速筛选模块；这里集中查看、打开、批量选择和维护 YAML，不需要在长目录里一直下滑。</p>
        </div>
        <div class="assets-actions">
          <button class="btn-sm primary" onclick="showAddTask()">新建 YAML</button>
          <button class="btn-sm" onclick="showUpload()">上传 YAML</button>
          <button class="btn-sm" onclick="showAllAssetModules()">查看全部模块</button>
        </div>
      </div>
      <div class="assets-summary">
        <div><strong>${summary.total}</strong><span>当前 YAML</span></div>
        <div><strong>${moduleCount}</strong><span>${currentModule ? '当前模块' : '模块数'}</span></div>
        <div><strong>${summary.baseline}</strong><span>已入库/基线</span></div>
        <div><strong>${summary.failed}</strong><span>最近失败</span></div>
        <div><strong>${summary.selected}</strong><span>当前已选</span></div>
        ${summary.selectedTotal !== summary.selected ? `<div><strong>${summary.selectedTotal}</strong><span>全部已选</span></div>` : ''}
      </div>
      <div class="assets-browser">
        <div class="assets-filter-panel">
          <div class="assets-filter-head">
            <div>
              <strong>用例目录</strong>
              <span>按应用、模块和关键词筛选</span>
            </div>
            <button class="btn-sm" onclick="loadModules({force:true})">刷新</button>
          </div>
          <div class="assets-filter-controls">
            <select id="asset-app-filter" onchange="syncAssetFiltersToSidebar();resetAssetListPage();showAssetsCenter();">
              <option value="">全部应用</option>
              ${taskApps.map(app => `<option value="${escapeHtml(app.package)}" ${app.package === appValue ? 'selected' : ''}>${escapeHtml(app.name || app.package)}</option>`).join('')}
            </select>
            <input id="asset-search" type="text" value="${escapeHtml(searchValue)}" placeholder="搜索 YAML、模块、状态..." oninput="syncAssetFiltersToSidebar();resetAssetListPage();showAssetsCenter();">
          </div>
          <div class="library-view-row assets-view-row">
            ${[
              ['module', '模块'],
              ['recent', '最近'],
              ['failed', '失败'],
              ['baseline', '基线']
            ].map(([key, label]) => `<button class="library-view-btn ${libraryView === key ? 'active' : ''}" data-library-view="${key}" onclick="setLibraryView(${jsArg(key)})">${label}</button>`).join('')}
          </div>
          <div class="assets-module-list">
            ${assetModuleListHtml()}
          </div>
        </div>
        <div class="assets-table-panel">
          <div class="assets-table-head">
            <div>
              <strong>${currentModule ? escapeHtml(currentModule) : '全部模块'}</strong>
              <span>${escapeHtml(libraryView === 'module' ? '按模块' : ({recent: '最近', failed: '失败', baseline: '基线'}[libraryView] || libraryView))}</span>
            </div>
            <div class="assets-actions">
              <button class="btn-sm" onclick="selectCurrentAssetRows()">选择当前列表</button>
              ${currentModule ? `<button class="btn-sm" onclick="selectCurrentModuleFiles();showAssetsCenter()">全选当前模块</button>` : ''}
              <button class="btn-sm" onclick="clearAssetSelection()">清空选择</button>
              <button class="btn-sm success" onclick="publishSelectedFilesToSonic()" ${summary.selected ? '' : 'disabled'}>同步当前已选至 Sonic 平台</button>
              ${currentModule ? `<button class="btn-sm success" onclick="publishCurrentModuleToSonic()">同步当前模块至 Sonic 平台</button>` : ''}
              <button class="btn-sm" onclick="showBatchMove()" ${summary.selected ? '' : 'disabled'}>批量移动</button>
              <button class="btn-sm danger" onclick="deleteSelectedFiles()" ${summary.selected ? '' : 'disabled'}>批量删除</button>
              ${currentModule ? `<button class="btn-sm danger" onclick="deleteCurrentModule()">删除当前模块</button>` : ''}
            </div>
          </div>
          <div class="assets-table-wrap">
            ${rows.length ? `
              <table class="assets-table asset-library-table">
                <colgroup>
                  <col class="asset-col-select">
                  <col class="asset-col-file">
                  <col class="asset-col-module">
                  <col class="asset-col-status">
                  <col class="asset-col-run">
                  <col class="asset-col-sonic">
                  <col class="asset-col-cases">
                  <col class="asset-col-actions">
                </colgroup>
                <thead><tr>
                  <th class="assets-select-cell"><input class="task-check" type="checkbox" title="全选当前列表" ${allRowsSelected ? 'checked' : ''} onchange="toggleCurrentAssetRows(this.checked)"></th>
                  <th>YAML 文件</th><th>模块</th><th>状态</th><th>最近执行</th><th>Sonic</th><th>用例</th><th>操作</th>
                </tr></thead>
                <tbody>
                  ${pageRows.map(row => {
                    const key = fileKey(row.mod, row.file);
                    const stats = yamlStatsForFile(row.mod, row.file);
                    const sonic = sonicFileSummary(row.mod, row.file);
                    return `
                      <tr class="${currentFile === row.file && currentModule === row.mod ? 'active' : ''}">
                        <td><input class="task-check" type="checkbox" ${selectedFiles.has(key) ? 'checked' : ''} onclick="toggleFileSelected(${jsArg(row.mod)},${jsArg(row.file)},this.checked);showAssetsCenter();"></td>
                        <td>
                          <button class="asset-file-link" onclick="openFile(${jsArg(row.mod)},${jsArg(row.file)})">${escapeHtml(yamlDisplayName(row.file))}</button>
                          <div class="asset-file-path">${escapeHtml(row.file)}</div>
                        </td>
                        <td>${escapeHtml(row.mod)}</td>
                        <td><span class="asset-pill">${escapeHtml(lifecycleText(row.meta.status))}</span></td>
                        <td><span class="status-pill ${escapeHtml(row.job.status || '')}">${escapeHtml(jobStatusText(row.job.status || ''))}</span></td>
                        <td><span class="task-ext sonic ${escapeHtml(sonic.cls)}" title="${escapeHtml(sonic.title)}">${escapeHtml(sonic.text)}</span></td>
                        <td>${prioritySummaryHtml(stats, true)}</td>
                        <td class="asset-row-actions">
                          <button class="btn-sm" onclick="openFile(${jsArg(row.mod)},${jsArg(row.file)})">打开</button>
                          <button class="btn-sm" onclick="openFile(${jsArg(row.mod)},${jsArg(row.file)}).then(()=>showRunCurrentFile())">执行</button>
                          <button class="btn-sm" onclick="assetFileOp(${jsArg(row.mod)},${jsArg(row.file)},'rename')">重命名</button>
                          <button class="btn-sm" onclick="assetFileOp(${jsArg(row.mod)},${jsArg(row.file)},'move')">移动</button>
                          <button class="btn-sm danger" onclick="deleteAssetFile(${jsArg(row.mod)},${jsArg(row.file)})">删除</button>
                        </td>
                      </tr>
                    `;
                  }).join('')}
                </tbody>
              </table>
            ` : `<div class="job-empty">没有匹配的 YAML。可以调整搜索/筛选，或新建一个 YAML。</div>`}
          </div>
          ${paginationHtml(pageInfo, 'setAssetListPage')}
        </div>
      </div>
    </div>
  `;
  document.getElementById('toolbar-path').innerHTML = '<span>📁</span> 用例资产';
  document.getElementById('toolbar-help').textContent = '集中管理 YAML 文件、模块、状态和最近执行结果；右侧执行面板已隐藏，资产页使用完整宽度。';
  document.getElementById('file-info').textContent = currentModule ? `用例资产 / ${currentModule}` : '用例资产';
  updateToolbarState();
}

function syncAssetFiltersToSidebar() {
  const assetApp = document.getElementById('asset-app-filter');
  const sidebarApp = document.getElementById('app-filter');
  if (assetApp && sidebarApp) sidebarApp.value = assetApp.value;
  const assetSearch = document.getElementById('asset-search');
  const sidebarSearch = document.getElementById('task-search');
  if (assetSearch && sidebarSearch) sidebarSearch.value = assetSearch.value;
}

function selectAssetModule(mod='') {
  currentModule = mod || null;
  currentFile = null;
  resetAssetListPage();
  resetYamlToolbarForManager();
  renderModules();
  showAssetsCenter();
}

function assetModuleListHtml() {
  const appFilter = document.getElementById('asset-app-filter')?.value || document.getElementById('app-filter')?.value || '';
  const selectedApp = taskApps.find(app => app.package === appFilter);
  const allowedModules = selectedApp ? new Set(selectedApp.modules || []) : null;
  const keyword = (document.getElementById('asset-search')?.value || document.getElementById('task-search')?.value || '').trim().toLowerCase();
  const moduleRows = Object.entries(modules)
    .filter(([mod]) => !allowedModules || allowedModules.has(mod))
    .map(([mod, files]) => {
      const visibleCount = keyword ? files.filter(file => `${mod}/${file}`.toLowerCase().includes(keyword)).length : files.length;
      const failedCount = files.filter(file => latestJobForFile(mod, file)?.status === 'failed').length;
      const stats = mergeYamlStats(files.map(file => yamlStatsForFile(mod, file)));
      return { mod, files, visibleCount, failedCount, stats };
    })
    .filter(row => !keyword || row.visibleCount > 0)
    .sort((a, b) => a.mod.localeCompare(b.mod, 'zh-CN'));
  return `
    <button class="asset-module-item ${!currentModule ? 'active' : ''}" onclick="selectAssetModule('')">
      <span>全部模块</span>
      <strong>${moduleRows.reduce((sum, row) => sum + row.files.length, 0)}</strong>
    </button>
    ${moduleRows.map(row => `
      <button class="asset-module-item ${currentModule === row.mod ? 'active' : ''}" onclick="selectAssetModule(${jsArg(row.mod)})">
        <span>${escapeHtml(row.mod)}</span>
        <strong>${row.visibleCount}/${row.files.length}</strong>
        <em>${escapeHtml(prioritySummaryText(row.stats, true))}${row.failedCount ? ` · 失败 ${row.failedCount}` : ''}</em>
      </button>
    `).join('') || '<div class="job-empty">没有匹配模块</div>'}
  `;
}

function sonicFileSummary(mod, file) {
  const rows = sonicCaseRows.filter(row => row.module === mod && row.file === file && !row.error);
  if (!rows.length) return { text: '未同步', cls: 'missing', title: '未解析到 Sonic 可同步用例，或尚未同步' };
  const published = rows.filter(row => row.step_state === 'bridge' || (row.sonic || {}).step_state === 'bridge' || (row.sonic || {}).status === 'published').length;
  const failed = rows.filter(row => row.step_state === 'failed' || (row.sonic || {}).status === 'failed').length;
  const legacy = rows.filter(row => row.step_state === 'legacy' || (row.sonic || {}).step_state === 'legacy' || (row.sonic || {}).status === 'legacy').length;
  const mixed = rows.filter(row => row.step_state === 'mixed' || (row.sonic || {}).step_state === 'mixed' || (row.sonic || {}).status === 'mixed').length;
  if (failed) return { text: '同步失败', cls: 'failed', title: `${failed}/${rows.length} 条同步至 Sonic 平台失败` };
  if (mixed) return { text: '待清理', cls: 'mixed', title: `${mixed}/${rows.length} 条存在旧/重复执行步骤，请重新同步清理` };
  if (legacy) return { text: '旧模板', cls: 'legacy', title: `${legacy}/${rows.length} 条仍是 Sonic 旧模板` };
  if (published === rows.length) return { text: '已同步', cls: 'published', title: `${published}/${rows.length} 条已同步至 Sonic 平台` };
  if (published > 0) return { text: `同步 ${published}/${rows.length}`, cls: 'partial', title: `${published}/${rows.length} 条已同步至 Sonic 平台` };
  return { text: '未同步', cls: 'missing', title: `0/${rows.length} 条同步至 Sonic 平台` };
}

function sonicBadgeHtml(mod, file) {
  const summary = sonicFileSummary(mod, file);
  return `<span class="task-ext sonic ${escapeHtml(summary.cls)}" title="${escapeHtml(summary.title)}">${escapeHtml(summary.text)}</span>`;
}

function yamlStatsKey(mod, file) {
  return `${mod}::${file}`;
}

function emptyYamlStats() {
  return { total: 0, p0: 0, p1: 0, p2: 0, p3: 0, smoke: 0, loaded: false };
}

function statsFromYamlContent(content) {
  const stats = emptyYamlStats();
  const tasks = parseYamlTasks(content || '');
  stats.total = tasks.length;
  tasks.forEach(task => {
    const p = (task.priority || 'P2').toLowerCase();
    if (Object.prototype.hasOwnProperty.call(stats, p)) stats[p] += 1;
    else stats.p2 += 1;
    if (task.smoke) stats.smoke += 1;
  });
  stats.loaded = true;
  return stats;
}

function normalizeYamlStats(raw) {
  const stats = emptyYamlStats();
  const source = raw || {};
  ['total', 'p0', 'p1', 'p2', 'p3', 'smoke'].forEach(key => {
    stats[key] = Number(source[key] || 0);
  });
  stats.loaded = source.loaded !== false;
  if (source.error) stats.error = String(source.error);
  return stats;
}

function yamlStatsForFile(mod, file) {
  return yamlStatsCache[yamlStatsKey(mod, file)] || emptyYamlStats();
}

function mergeYamlStats(statsList) {
  const merged = emptyYamlStats();
  merged.loaded = statsList.some(item => item.loaded);
  statsList.forEach(item => {
    merged.total += item.total || 0;
    merged.p0 += item.p0 || 0;
    merged.p1 += item.p1 || 0;
    merged.p2 += item.p2 || 0;
    merged.p3 += item.p3 || 0;
    merged.smoke += item.smoke || 0;
  });
  return merged;
}

function prioritySummaryHtml(stats, compact=false) {
  if (!stats || !stats.loaded) return `<span class="task-ext" title="打开模块后加载等级统计">等级待加载</span>`;
  const badges = [];
  [['P0', stats.p0], ['P1', stats.p1], ['P2', stats.p2], ['P3', stats.p3]].forEach(([p, count]) => {
    if (!count) return;
    if (compact && p === 'P2' && !stats.p0 && !stats.p1 && !stats.p3 && !stats.smoke) return;
    badges.push(`<span class="priority-badge ${p.toLowerCase()}">${compact && count === 1 ? p : `${p} ${count}`}</span>`);
  });
  if (stats.smoke) badges.push(`<span class="smoke-badge">冒烟 ${stats.smoke}</span>`);
  if (badges.length) return badges.join('');
  if (compact && stats.total) return `<span class="task-ext" title="普通优先级用例">${stats.total} 用例</span>`;
  return '<span class="task-ext">无用例</span>';
}

function prioritySummaryText(stats, compact=false) {
  if (!stats || !stats.loaded) return '等级待加载';
  const parts = [];
  [['P0', stats.p0], ['P1', stats.p1], ['P2', stats.p2], ['P3', stats.p3]].forEach(([p, count]) => {
    if (!count) return;
    if (compact && p === 'P2' && !stats.p0 && !stats.p1 && !stats.p3 && !stats.smoke) return;
    parts.push(compact && count === 1 ? p : `${p} ${count}`);
  });
  if (stats.smoke) parts.push(`冒烟 ${stats.smoke}`);
  if (parts.length) return parts.join(' · ');
  if (compact && stats.total) return `${stats.total} 用例`;
  return '无用例';
}

function moduleStatsLoadedCount() {
  return Object.values(yamlStatsCache).filter(item => item && item.loaded).length;
}

async function loadYamlStatsForModule(mod, {refresh=false}={}) {
  const files = modules[mod] || [];
  const pending = files.filter(file => refresh || !yamlStatsCache[yamlStatsKey(mod, file)]);
  if (!pending.length) return;
  try {
    const data = await apiRequest(`/yaml-stats?module=${encodeURIComponent(mod)}`);
    const moduleStats = ((data && data.stats) || {})[mod] || {};
    pending.forEach(file => {
      yamlStatsCache[yamlStatsKey(mod, file)] = normalizeYamlStats(moduleStats[file]);
    });
  } catch(e) {
    pending.forEach(file => {
      yamlStatsCache[yamlStatsKey(mod, file)] = {...emptyYamlStats(), loaded: true, error: e.message || String(e)};
    });
  }
}

async function refreshModuleDirectoryStats(mod, {refresh=false}={}) {
  await loadYamlStatsForModule(mod, {refresh});
  if (currentModule !== mod || hasOpenEditor()) return;
  const area = document.getElementById('editor-area');
  if (area && area.querySelector('.module-directory')) {
    area.innerHTML = moduleDirectoryHtml(mod);
  }
  renderModules();
}

async function warmupYamlStats() {
  if (yamlStatsWarmupStarted) return;
  yamlStatsWarmupStarted = true;
  const moduleNames = Object.keys(modules);
  for (const mod of moduleNames) {
    await loadYamlStatsForModule(mod).catch(() => {});
    renderModules();
    if (currentModule === mod && !hasOpenEditor()) {
      const area = document.getElementById('editor-area');
      if (area && area.querySelector('.module-directory')) area.innerHTML = moduleDirectoryHtml(mod);
    }
    await sleepMs(120);
  }
}

function modulePriorityFilterLabel(filter) {
  return {
    all: '全部',
    p0: '只看 P0',
    p1: '只看 P1',
    hot: 'P0/P1',
    smoke: '冒烟',
  }[filter] || '全部';
}

function yamlStatsMatchesFilter(stats, filter) {
  if (!filter || filter === 'all') return true;
  if (!stats || !stats.loaded) return true;
  if (filter === 'p0') return (stats.p0 || 0) > 0;
  if (filter === 'p1') return (stats.p1 || 0) > 0;
  if (filter === 'hot') return (stats.p0 || 0) + (stats.p1 || 0) > 0;
  if (filter === 'smoke') return (stats.smoke || 0) > 0;
  return true;
}

function yamlStatsRank(stats) {
  if (!stats || !stats.loaded) return 0;
  return (stats.p0 || 0) * 1000 + (stats.p1 || 0) * 100 + (stats.smoke || 0) * 20 + (stats.p2 || 0);
}

function setModulePriorityFilter(filter) {
  modulePriorityFilter = filter || 'all';
  resetModuleDirectoryPage();
  if (currentModule && modules[currentModule] && !hasOpenEditor()) {
    const area = document.getElementById('editor-area');
    if (area && area.querySelector('.module-directory')) area.innerHTML = moduleDirectoryHtml(currentModule);
  }
}

function taskNamesInFile(content) {
  return parseYamlTasks(content).map(item => item.name);
}

function yamlDisplayName(file) {
  const raw = String(file || '').trim();
  const base = raw.replace(/\.(ya?ml)$/i, '').replace(/^\._/, '').replace(/^\.+/, '').trim();
  return base || '未命名 YAML';
}

function fillModuleSelect(select, placeholder, selectedValue='') {
  if (!select) return;
  const previous = selectedValue || select.value || '';
  select.innerHTML = `<option value="">${placeholder}</option>`;
  Object.keys(modules).sort((a, b) => a.localeCompare(b, 'zh-CN')).forEach(mod => {
    const opt = document.createElement('option');
    opt.value = mod;
    opt.textContent = moduleApp(mod) ? `${mod} / ${moduleApp(mod).name || moduleApp(mod).package}` : mod;
    select.appendChild(opt);
  });
  if (previous && Object.prototype.hasOwnProperty.call(modules, previous)) {
    select.value = previous;
  }
}

function renderModuleSelects() {
  fillModuleSelect(document.getElementById('new-task-module'), '选择所属模块', currentModule || '');
  fillModuleSelect(document.getElementById('upload-module'), '选择目标模块', currentModule || '');
  fillModuleSelect(document.getElementById('generate-module'), '选择目标模块', currentModule || '');
}

function renderModules() {
  const list = document.getElementById('module-list');
  const keyword = (document.getElementById('task-search')?.value || '').trim().toLowerCase();
  const appFilter = document.getElementById('app-filter')?.value || '';
  const selectedApp = taskApps.find(app => app.package === appFilter);
  const allowedModules = selectedApp ? new Set(selectedApp.modules || []) : null;
  renderAppFilter();
  renderModuleSelects();
  document.querySelectorAll('.library-view-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.libraryView === libraryView);
  });
  list.innerHTML = '';

  if (libraryView !== 'module') {
    let rows = moduleFileRows();
    if (allowedModules) rows = rows.filter(row => allowedModules.has(row.mod));
    if (libraryView === 'recent') rows = rows.filter(row => row.time > 0).sort((a, b) => b.time - a.time).slice(0, 40);
    if (libraryView === 'failed') rows = rows.filter(row => row.job.status === 'failed').sort((a, b) => b.time - a.time).slice(0, 60);
    if (libraryView === 'baseline') rows = rows.filter(row => ['baseline', 'active'].includes(row.meta.status || '')).sort((a, b) => a.mod.localeCompare(b.mod, 'zh-CN'));
    if (keyword) {
      rows = rows.filter(row => `${row.mod}/${row.file}/${lifecycleText(row.meta.status)}`.toLowerCase().includes(keyword));
    }
    if (!rows.length) {
      list.innerHTML = '<div style="padding:16px;color:var(--text2);font-family:var(--mono);font-size:12px;">没有匹配的 YAML 文件</div>';
      if (activeWorkflow === 'assets' && !hasOpenEditor()) showAssetsCenter();
      return;
    }
    list.innerHTML = rows.map(row => `
      <div class="task-item ${currentFile===row.file&&currentModule===row.mod?'active':''}" onclick="openFile(${jsArg(row.mod)},${jsArg(row.file)})">
        <input class="task-check" type="checkbox" ${selectedFiles.has(fileKey(row.mod, row.file)) ? 'checked' : ''} onclick="event.stopPropagation();toggleFileSelected(${jsArg(row.mod)},${jsArg(row.file)},this.checked)">
        <span class="task-status ${(row.job.status || '')}" title="最近执行：${jobStatusText(row.job.status || '')}"></span>
        <span class="task-name" title="${escapeHtml(row.mod + '/' + row.file)}">${escapeHtml(yamlDisplayName(row.file))}</span>
        ${sonicBadgeHtml(row.mod, row.file)}
        <span class="task-ext">${escapeHtml(row.mod)}</span>
      </div>
    `).join('');
    if (activeWorkflow === 'assets' && !hasOpenEditor()) showAssetsCenter();
    return;
  }

  Object.entries(modules).forEach(([mod, files]) => {
    if (allowedModules && !allowedModules.has(mod)) return;
    const visibleFiles = keyword
      ? files.filter(f => {
          const meta = fileMeta(mod, f);
          return `${mod}/${f}/${lifecycleText(meta.status)}`.toLowerCase().includes(keyword);
        })
      : files;
    if (keyword && visibleFiles.length === 0) return;
    const isOpen = mod === currentModule;
    const div = document.createElement('div');
    div.className = `module-item ${(isOpen || keyword) ? 'open active' : ''}`;
    div.innerHTML = `
      <div class="module-header" onclick="toggleModule(${jsArg(mod)}, this)">
        <span class="module-icon">📁</span>
        <span class="module-name">${mod}</span>
        ${moduleApp(mod) ? `<span class="task-ext" title="${moduleApp(mod).package}">${escapeHtml(moduleApp(mod).name || moduleApp(mod).package)}</span>` : ''}
        <span class="task-ext" title="模块用例等级统计">${escapeHtml(prioritySummaryText(mergeYamlStats(files.map(file => yamlStatsForFile(mod, file)))) )}</span>
        <span class="module-count">${visibleFiles.length}/${files.length}</span>
        <span class="module-arrow">▶</span>
      </div>
      <div class="task-list">
        ${visibleFiles.map(f => `
          <div class="task-item ${currentFile===f&&currentModule===mod?'active':''}" onclick="openFile(${jsArg(mod)},${jsArg(f)})">
            <input class="task-check" type="checkbox" ${selectedFiles.has(fileKey(mod, f)) ? 'checked' : ''} onclick="event.stopPropagation();toggleFileSelected(${jsArg(mod)},${jsArg(f)},this.checked)">
            <span class="task-status ${(latestJobForFile(mod, f)?.status || '')}" title="最近执行：${jobStatusText(latestJobForFile(mod, f)?.status || '')}"></span>
            <span class="task-name" title="${escapeHtml(f)}">${escapeHtml(yamlDisplayName(f))}</span>
            ${sonicBadgeHtml(mod, f)}
            <span class="task-ext" title="用例等级统计">${escapeHtml(prioritySummaryText(yamlStatsForFile(mod, f), true))}</span>
            <span class="task-ext">${lifecycleText(fileMeta(mod, f).status)}</span>
            <span class="task-ext">${f.endsWith('.yml') ? 'yml' : 'yaml'}</span>
            <span class="task-del" onclick="event.stopPropagation();deleteFile(${jsArg(mod)},${jsArg(f)})">✕</span>
          </div>
        `).join('')}
      </div>
    `;
    list.appendChild(div);
  });
  if (!list.innerHTML) {
    list.innerHTML = '<div style="padding:16px;color:var(--text2);font-family:var(--mono);font-size:12px;">没有匹配的 YAML 文件</div>';
  }
  if (activeWorkflow === 'assets' && !hasOpenEditor()) showAssetsCenter();
}

function toggleModule(mod, el) {
  const item = el.parentElement;
  if (currentModule === mod && !currentFile && item.classList.contains('open')) {
    currentModule = null;
    currentFile = null;
    item.classList.remove('open', 'active');
    resetYamlToolbarForDirectory();
    showWorkflowGuide(activeWorkflow);
    document.getElementById('toolbar-path').innerHTML = '<span>📁</span> 选择左侧文件开始编辑';
    document.getElementById('toolbar-help').textContent = WORKFLOW_SECTIONS[activeWorkflow]?.help || '从左侧模块选择 YAML，或先用需求/设计稿生成可执行用例。';
    document.getElementById('file-info').textContent = '就绪';
    renderModules();
    return;
  }
  showModuleDirectory(mod);
}

function fileKey(mod, file) {
  return `${mod}::${file}`;
}

function toggleFileSelected(mod, file, checked) {
  const key = fileKey(mod, file);
  if (checked) selectedFiles.add(key);
  else selectedFiles.delete(key);
  const statusSelect = document.getElementById('file-status-select');
  if (statusSelect && selectedFiles.size > 0) {
    statusSelect.title = `批量标记 ${selectedFiles.size} 个已选 YAML`;
    statusSelect.value = '';
  }
  document.getElementById('file-info').textContent = selectedFiles.size ? `已选择 ${selectedFiles.size} 个文件，可批量标记状态` : '就绪';
}

function selectCurrentModuleFiles() {
  if (!currentModule || !modules[currentModule]) {
    showToast('请先展开一个模块', 'error');
    return;
  }
  modules[currentModule].forEach(file => selectedFiles.add(fileKey(currentModule, file)));
  const statusSelect = document.getElementById('file-status-select');
  if (statusSelect) {
    statusSelect.title = `批量标记 ${selectedFiles.size} 个已选 YAML`;
    statusSelect.value = '';
  }
  renderModules();
  document.getElementById('file-info').textContent = `已选择 ${modules[currentModule].length} 个文件，可批量标记状态`;
  showToast(`已选择 ${modules[currentModule].length} 个文件`, 'success');
}

function clearSelectedFiles() {
  selectedFiles.clear();
  const statusSelect = document.getElementById('file-status-select');
  if (statusSelect && currentModule && currentFile) {
    statusSelect.value = fileMeta(currentModule, currentFile).status || 'draft';
    statusSelect.title = '用例状态';
  }
  renderModules();
  document.getElementById('file-info').textContent = currentModule && currentFile ? `${currentModule}/${currentFile}` : '就绪';
  showToast('已清空选择', 'success');
}

function moduleOptionsHtml(selected='') {
  return Object.keys(modules).sort().map(mod => `<option value="${escapeHtml(mod)}" ${mod === selected ? 'selected' : ''}>${escapeHtml(mod)}</option>`).join('');
}

function showTaskApps() {
  if (!['app_config', 'feishu_config'].includes(activeWorkflow)) {
    setActiveWorkflow('app_config');
  }
  renderTaskAppModal();
  document.getElementById('modal-task-apps').classList.add('show');
  FormSteps.init('#modal-task-apps');
}

function renderTaskAppModal() {
  document.getElementById('task-app-modules').innerHTML = Object.keys(modules).sort().map(mod => `
    <label title="${escapeHtml(mod)}">
      <input type="checkbox" class="task-app-module-check" value="${escapeHtml(mod)}">
      <span>${escapeHtml(mod)}</span>
    </label>
  `).join('');
  renderTaskAppList();
}

function clearTaskAppForm() {
  document.getElementById('task-app-name').value = '';
  document.getElementById('task-app-package').value = '';
  document.getElementById('task-app-sonic-project-name').value = '';
  document.getElementById('task-app-sonic-project-id').value = '';
  document.getElementById('task-app-sonic-suite-name').value = '';
  document.getElementById('task-app-sonic-suite-id').value = '';
  document.getElementById('task-app-feishu-webhook').value = '';
  document.querySelectorAll('.task-app-module-check').forEach(input => input.checked = false);
}

function editTaskApp(packageName) {
  const app = taskApps.find(item => item.package === packageName);
  if (!app) return;
  document.getElementById('task-app-name').value = app.name || '';
  document.getElementById('task-app-package').value = app.package || '';
  document.getElementById('task-app-sonic-project-name').value = app.sonic_project_name || '';
  document.getElementById('task-app-sonic-project-id').value = app.sonic_project_id || '';
  document.getElementById('task-app-sonic-suite-name').value = app.sonic_suite_name || '';
  document.getElementById('task-app-sonic-suite-id').value = app.sonic_suite_id || '';
  document.getElementById('task-app-feishu-webhook').value = app.feishu_webhook || '';
  const selected = new Set(app.modules || []);
  document.querySelectorAll('.task-app-module-check').forEach(input => input.checked = selected.has(input.value));
}

function taskAppFeishuLabel(app) {
  if (app.feishu_webhook) return '飞书：已配置';
  if (['com.kfb.model', 'com.xbxxhz.box'].includes(app.package)) return '飞书：默认群';
  return '飞书：未配置';
}

function renderTaskAppList() {
  const list = document.getElementById('task-app-list');
  if (!list) return;
  if (!taskApps.length) {
    list.innerHTML = '<div class="job-empty">暂无应用分组</div>';
    return;
  }
  list.innerHTML = taskApps.map(app => `
    <div class="app-row">
      <div class="app-row-main" onclick="editTaskApp('${escapeHtml(app.package)}')">
        <div class="app-row-name">${escapeHtml(app.name || app.package)}</div>
        <div class="app-row-sub">${escapeHtml(app.package)} · 项目：${escapeHtml(app.sonic_project_name || app.sonic_project_id || '未绑定')} · 测试套：${escapeHtml(app.sonic_suite_name || app.sonic_suite_id || '未绑定')} · ${escapeHtml(taskAppFeishuLabel(app))} · ${(app.modules || []).length} 个模块：${escapeHtml((app.modules || []).join('、'))}</div>
      </div>
      <button class="btn-sm" onclick="editTaskApp('${escapeHtml(app.package)}')">编辑</button>
      <button class="btn-sm danger" onclick="deleteTaskApp('${escapeHtml(app.package)}')">删除</button>
    </div>
  `).join('');
}

async function saveTaskApp() {
  const name = document.getElementById('task-app-name').value.trim();
  const packageName = document.getElementById('task-app-package').value.trim();
  const sonicProjectName = document.getElementById('task-app-sonic-project-name').value.trim();
  const sonicProjectId = document.getElementById('task-app-sonic-project-id').value.trim();
  const sonicSuiteName = document.getElementById('task-app-sonic-suite-name').value.trim();
  const sonicSuiteId = document.getElementById('task-app-sonic-suite-id').value.trim();
  const feishuWebhook = document.getElementById('task-app-feishu-webhook').value.trim();
  const selectedModules = Array.from(document.querySelectorAll('.task-app-module-check:checked')).map(input => input.value);
  if (!name || !packageName) {
    showToast('请填写应用中文名和包名', 'error');
    return;
  }
  try {
    const data = await apiRequest('/task-app', {
      method: 'POST',
      body: JSON.stringify({
        name,
        package: packageName,
        modules: selectedModules,
        sonic_project_name: sonicProjectName,
        sonic_project_id: sonicProjectId,
        sonic_suite_name: sonicSuiteName,
        sonic_suite_id: sonicSuiteId,
        feishu_webhook: feishuWebhook
      })
    });
    taskApps = taskApps.filter(app => app.package !== data.app.package);
    taskApps.push(data.app);
    taskApps.sort((a, b) => (a.name || a.package).localeCompare(b.name || b.package, 'zh-Hans-CN'));
    clearTaskAppForm();
    renderTaskAppModal();
    renderModules();
    showToast(`应用已保存${data.app.sonic_suite_id ? '，Sonic 测试套已绑定' : ''}`, 'success');
  } catch(e) {
    showToast(e.message || '保存应用分组失败', 'error');
  }
}

async function diagnoseSonic() {
  const target = document.getElementById('sonic-diagnose-result');
  try {
    const data = await apiRequest('/sonic/diagnose', {
      method: 'POST',
      body: JSON.stringify({})
    });
    const matched = (data.apps || []).filter(item => item.matched).length;
    const recommendations = data.recommendations || [];
    const auth = data.auth || {};
    const authSource = auth.active_source === 'static_token_fallback'
      ? '账号密码登录失败，正使用兼容 Token'
      : (auth.preferred_source === 'login' ? '账号密码自动登录' : (auth.preferred_source === 'static_token' ? '兼容 Token' : '未配置'));
    const authState = auth.login_configured
      ? (auth.login_ok === true ? '登录成功' : (auth.login_ok === false ? `登录失败：${auth.login_error || '未知错误'}` : '待验证'))
      : '未配置账号密码';
    const apps = (data.apps || []).map(item => {
      const suite = item.suite || {};
      const suiteState = suite.matched ? `${suite.name || suite.id}（${suite.case_count || 0} 条）` : (suite.configured ? `绑定异常：${suite.error || '未匹配'}` : '未绑定测试套');
      return `${item.name || item.package}：项目${item.matched ? '已匹配' : '未匹配'}，${suiteState}`;
    });
    if (target) {
      target.style.display = 'block';
      target.innerHTML = `<strong>Sonic 接入诊断</strong><br>认证方式：${escapeHtml(authSource)}；${escapeHtml(authState)}<br>项目 ${data.projects.length} 个，应用匹配 ${matched}/${(data.apps || []).length}<br>${apps.map(escapeHtml).join('<br>')}<br><br><strong>下一步</strong><br>${recommendations.map(escapeHtml).join('<br>')}`;
    }
    showToast(`Sonic 诊断完成：应用匹配 ${matched}/${(data.apps || []).length}`, 'success');
    console.log('Sonic diagnose', data);
  } catch(e) {
    if (target) {
      target.style.display = 'block';
      target.textContent = e.message || 'Sonic 诊断失败，请检查服务端配置';
    }
    showToast(e.message || 'Sonic 诊断失败，请检查 Sonic 地址和自动登录配置', 'error');
  }
}

function sonicStateText(state) {
  const map = {
    bridge: '已同步',
    legacy: '旧模板待迁移',
    mixed: '旧/重复步骤待清理',
    not_published: '未同步',
    missing: '缺少脚本步骤',
    project_missing: '未绑定项目'
  };
  return map[state] || state || '未知';
}

function renderSonicStatusRows(rows, containerId) {
  const list = document.getElementById(containerId);
  if (!list) return;
  if (!rows || !rows.length) {
    list.innerHTML = '<div class="job-empty">暂无需要维护的 Sonic 数据。日常同步请到「用例资产」操作。</div>';
    return;
  }
  const actionLabels = {
    migrate: '可自动清理',
    manual: '需人工处理',
    skip: '无需处理',
    bridge: '已托管',
    legacy: '旧模板',
    mixed: '重复步骤',
    project_missing: '项目未绑定'
  };
  list.innerHTML = rows.map(row => {
    const action = row.action || row.step_state || '';
    const matched = row.matched_case || {};
    const title = row.sonic_case_name || row.task_name || matched.task_name || '未命名用例';
    const state = row.step_label || sonicStateText(row.step_state);
    const mod = matched.module || row.module || '';
    const file = matched.file || row.file || '';
    const taskName = row.task_name || row.taskName || matched.task_name || row.sonic_case_name || '';
    const caseInfo = matched.task_name
      ? `${matched.module}/${matched.file} · ${matched.task_name}`
      : `${mod}/${file} ${taskName || ''}`.trim();
    const canTaskRun = mod && file && taskName && !['manual', 'legacy', 'mixed', 'project_missing'].includes(action);
    const runActions = canTaskRun ? `
      <div class="sonic-status-actions">
        <button class="btn-sm success" onclick="openFileAndRunTask(${jsArg(mod)}, ${jsArg(file)}, ${jsArg(taskName)})">Runner 单条调试</button>
      </div>
    ` : '';
    const matchText = matched.task_name
      ? `匹配方式：${escapeHtml(row.match_type || '名称规则')}`
      : '匹配方式：未找到对应 YAML';
    const reasonText = row.reason || (row.case_id || matched.case_id ? `case_id：${row.case_id || matched.case_id}` : '');
    return `
      <div class="sonic-status-row ${escapeHtml(action)}">
        <div class="sonic-status-title">
          <span>${escapeHtml(title)}</span>
          <b>${escapeHtml(actionLabels[action] || state || '状态未知')}</b>
        </div>
        <div class="sonic-status-meta">
          Sonic：${escapeHtml(row.project_name || row.sonic_project_name || row.project_id || '-')} / case ${escapeHtml(row.sonic_case_id || '-')} / ${escapeHtml(state)}<br>
          Task：${escapeHtml(caseInfo || '未匹配')}<br>
          ${matchText}<br>
          ${reasonText ? `处理建议：${escapeHtml(reasonText)}` : ''}
        </div>
        ${runActions}
      </div>
    `;
  }).join('');
}

function setSonicStatusActionMode(mode='scan') {
  const rescan = document.getElementById('sonic-action-rescan');
  const migrate = document.getElementById('sonic-action-migrate');
  const refresh = document.getElementById('sonic-action-refresh');
  if (rescan) rescan.style.display = mode === 'scan' ? '' : 'none';
  if (migrate) migrate.style.display = mode === 'scan' ? '' : 'none';
  if (refresh) refresh.style.display = mode === 'refresh' ? 'none' : '';
}

async function openFileAndRunTask(mod, file, taskName='') {
  await openFile(mod, file);
  const ta = document.getElementById('editor');
  const tasks = parseYamlTasks(ta?.value || '');
  const index = tasks.findIndex(task => task.name === taskName);
  if (index >= 0) jumpToTask(index);
  showRunSelectedTask();
}

async function refreshSonicPreview(force=false) {
  const list = document.getElementById('sonic-preview-list');
  const sub = document.getElementById('sonic-preview-sub');
  if (!list || !sub || !currentModule || !currentFile) return;
  if (!force && sonicStatusData?.module === currentModule && sonicStatusData?.file === currentFile) {
    renderSonicPreview();
    return;
  }
  sub.textContent = '正在读取同步状态...';
  try {
    const data = await apiRequest(`/sonic/status?module=${encodeURIComponent(currentModule)}&file=${encodeURIComponent(currentFile)}`);
    sonicStatusData = { module: currentModule, file: currentFile, ...data };
    renderSonicPreview();
  } catch(e) {
    sub.textContent = '同步至 Sonic 平台检查读取失败';
    list.innerHTML = `<div class="generate-knowledge-empty">${escapeHtml(e.message || '读取失败')}</div>`;
  }
}

function renderSonicPreview() {
  const list = document.getElementById('sonic-preview-list');
  const sub = document.getElementById('sonic-preview-sub');
  if (!list || !sub) return;
  const rows = sonicStatusData?.cases || [];
  const summary = sonicStatusData?.summary || {};
  const total = summary.total || rows.length || 0;
  sub.textContent = `共 ${total} 条，已同步 ${summary.bridge || 0}，旧模板 ${summary.legacy || 0}，待清理 ${summary.mixed || 0}，未同步 ${summary.missing || 0}${total > 3 ? '，下方可滚动查看全部' : ''}`;
  if (!rows.length) {
    list.innerHTML = '<div class="generate-knowledge-empty">当前 YAML 还没有可同步的用例。</div>';
    return;
  }
  const legacyRisk = (summary.legacy || 0) + (summary.mixed || 0);
  const warning = legacyRisk
    ? `<div class="sonic-risk-alert">发现 ${legacyRisk} 条旧或重复脚本。它们在 Sonic 执行时可能绕过 Task 汇总通知，继续发送旧报告或乱码消息。运行测试套前请先点击“套件体检”清理。</div>`
    : '';
  list.innerHTML = warning + rows.map(row => `
    <div class="sonic-sync-row ${escapeHtml(row.step_state || '')}">
      <div class="sonic-sync-name">${escapeHtml(row.task_name || row.sonic_case_name || '未命名用例')}</div>
      <div class="sonic-sync-meta">
        ${escapeHtml(sonicStateText(row.step_state))}${row.sonic_case_id ? ` · Sonic case ${escapeHtml(row.sonic_case_id)}` : ''}<br>
        ${escapeHtml(row.case_id || '')}
      </div>
    </div>
  `).join('');
  updateToolbarState(summary.mixed ? `Sonic 有 ${summary.mixed} 条旧/重复步骤待清理` : (summary.legacy ? `Sonic 有 ${summary.legacy} 条旧模板待迁移` : ''));
}

async function showCurrentFileSonicStatus() {
  if (!requireCurrentYaml('查看同步至 Sonic 平台检查')) return;
  setSonicStatusActionMode('status');
  document.getElementById('sonic-status-title').textContent = '当前 YAML 的同步至 Sonic 平台状态';
  document.getElementById('sonic-status-summary').textContent = '正在读取当前文件同步状态...';
  document.getElementById('sonic-status-list').innerHTML = '<div class="job-empty">正在加载...</div>';
  openModal('modal-sonic-status');
  try {
    const data = await apiRequest(`/sonic/status?module=${encodeURIComponent(currentModule)}&file=${encodeURIComponent(currentFile)}`);
    sonicStatusData = { module: currentModule, file: currentFile, ...data };
    const s = data.summary || {};
    const risk = (s.legacy || 0) + (s.mixed || 0);
    const warning = risk ? ` 风险：${risk} 条旧/重复脚本仍可能单独发送旧报告或乱码消息，请清理后再执行 Sonic 测试套。` : '';
    document.getElementById('sonic-status-summary').textContent = `当前文件：${currentModule}/${currentFile}。共 ${s.total || 0} 条，已同步 ${s.bridge || 0}，旧模板 ${s.legacy || 0}，待清理 ${s.mixed || 0}，未同步 ${s.missing || 0}。${warning}`;
    renderSonicStatusRows(data.cases || [], 'sonic-status-list');
    renderSonicPreview();
  } catch(e) {
    document.getElementById('sonic-status-summary').textContent = e.message || '读取同步至 Sonic 平台检查失败';
    document.getElementById('sonic-status-list').innerHTML = '';
  }
}

async function scanLegacySonicCases(scope='auto') {
  const payload = scope === 'all' ? {} : (currentFile ? { module: currentModule, file: currentFile } : {});
  lastSonicScanPayload = payload;
  setSonicStatusActionMode('scan');
  document.getElementById('sonic-status-title').textContent = payload.file ? '当前 YAML 的 Sonic 维护检查' : 'Sonic 旧步骤维护检查';
  document.getElementById('sonic-status-summary').textContent = '正在扫描 Sonic 中的历史旧模板、重复 Midscene 步骤，并尝试匹配 Task YAML 用例...';
  document.getElementById('sonic-status-list').innerHTML = '<div class="job-empty">正在扫描...</div>';
  openModal('modal-sonic-status');
  try {
    const data = await apiRequest('/sonic/scan-legacy', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    const risk = (data.legacy || 0) + (data.mixed || 0);
    const warning = risk ? ' 未清理前可能绕开汇总通知，单独发送旧报告或乱码消息。' : ' 当前未发现旧 Midscene 步骤。';
    document.getElementById('sonic-status-summary').textContent = `维护扫描完成：旧模板 ${data.legacy || 0} 条，重复残留 ${data.mixed || 0} 条，可自动清理 ${data.migratable || 0} 条，需要人工处理 ${data.manual || 0} 条。${warning} 新 YAML 同步请在「用例资产」里操作。`;
    renderSonicStatusRows(data.rows || [], 'sonic-status-list');
  } catch(e) {
    document.getElementById('sonic-status-summary').textContent = e.message || '扫描失败';
    document.getElementById('sonic-status-list').innerHTML = '';
  }
}

async function rescanLegacySonicCases() {
  const scope = lastSonicScanPayload && Object.keys(lastSonicScanPayload).length === 0 ? 'all' : 'auto';
  return scanLegacySonicCases(scope);
}

async function migrateLegacySonicCases() {
  const payload = lastSonicScanPayload || (currentFile ? { module: currentModule, file: currentFile } : {});
  if (!confirm('确认清理可自动匹配的 Sonic 旧/重复 Midscene 步骤？\n\n系统会按 Task YAML 的 case_id 补齐桥接脚本，并删除残留旧步骤；不会修改 YAML、不触发执行。')) return;
  document.getElementById('sonic-status-summary').textContent = '正在按 Task YAML 匹配结果清理旧/重复步骤...';
  try {
    const data = await apiRequest('/sonic/migrate-legacy', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    document.getElementById('sonic-status-summary').textContent = `处理完成：已清理 ${data.migrated || 0}/${data.migratable || 0} 条，需要人工处理 ${data.manual || 0} 条，正在复检是否仍有旧步骤...`;
    await refreshSonicPreview(true);
    const migrated = Number(data.migrated || 0);
    const manual = Number(data.manual || 0);
    showToast(migrated ? `✓ Sonic 旧/重复步骤已清理：${migrated} 条` : `没有可自动清理项，仍需人工处理 ${manual} 条`, migrated ? 'success' : 'warn');
    await rescanLegacySonicCases();
  } catch(e) {
    showToast(e.message || '迁移失败', 'error');
    document.getElementById('sonic-status-summary').textContent = e.message || '迁移失败';
  }
}

async function refreshSonicBridgeScripts(scope='auto') {
  const payload = scope === 'all' ? {} : (lastSonicScanPayload || (currentFile ? { module: currentModule, file: currentFile } : {}));
  const isAll = Object.keys(payload || {}).length === 0;
  const scopeText = isAll ? '全部已托管 Sonic 用例' : `当前范围 ${payload.module || ''}${payload.file ? `/${payload.file}` : ''}`;
  if (!confirm(`确认刷新 ${scopeText} 的桥接脚本？\n\n这个动作只更新 Sonic 中保存的 Groovy 引导脚本和当前 runner token，不修改 YAML、不改基线、不触发执行。`)) return;
  setSonicStatusActionMode('refresh');
  document.getElementById('sonic-status-title').textContent = isAll ? '刷新全部 Sonic 桥接脚本' : '刷新当前范围桥接脚本';
  document.getElementById('sonic-status-summary').textContent = '正在刷新 Sonic 中已托管用例的桥接脚本...';
  document.getElementById('sonic-status-list').innerHTML = '<div class="job-empty">正在刷新...</div>';
  openModal('modal-sonic-status');
  try {
    const data = await apiRequest('/sonic/refresh-bridges', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    const failed = Number(data.failed || 0);
    const skipped = Number(data.skipped || 0);
    document.getElementById('sonic-status-summary').textContent =
      `刷新完成：已刷新 ${data.refreshed || 0}/${data.matched || 0} 条，失败 ${failed} 条，未匹配跳过 ${skipped} 条。这个动作只更新 Sonic Groovy 桥接脚本，不修改 YAML。`;
    renderSonicStatusRows((data.results || []).map(item => ({
      ...item,
      action: item.status === 'failed' ? 'manual' : (item.refreshed ? 'migrate' : 'skip'),
      reason: item.error || item.reason || (item.refreshed ? '已刷新为当前桥接脚本和 runner token' : '已跳过'),
      step_label: item.status === 'failed' ? '刷新失败' : (item.refreshed ? '已刷新' : item.step_label)
    })), 'sonic-status-list');
    showToast(failed ? `桥接脚本刷新完成，但 ${failed} 条失败` : `✓ 桥接脚本刷新完成：${data.refreshed || 0} 条`, failed ? 'error' : 'success');
    await refreshSonicPreview(true);
  } catch(e) {
    showToast(e.message || '刷新桥接脚本失败', 'error');
    document.getElementById('sonic-status-summary').textContent = e.message || '刷新桥接脚本失败';
  }
}

function renderPublishCheckResult(data) {
  setSonicStatusActionMode('status');
  const rows = [];
  (data.blockers || []).forEach(text => rows.push({ action: 'manual', sonic_case_name: '阻断项', reason: text, step_label: '不可同步' }));
  (data.warnings || []).forEach(text => rows.push({ action: 'migrate', sonic_case_name: '提醒', reason: text, step_label: '同步前提醒' }));
  (data.fixes || []).forEach(text => rows.push({ action: 'skip', sonic_case_name: '自动处理', reason: text, step_label: '同步时处理' }));
  (data.sonic || []).forEach(row => rows.push({ ...row, action: ['legacy', 'mixed'].includes(row.step_state) ? 'migrate' : 'skip', reason: row.step_label }));
  document.getElementById('sonic-status-title').textContent = '同步前检查';
  document.getElementById('sonic-status-summary').textContent = data.canPublish
    ? `检查通过：${data.app_name || data.app_package || ''}，${(data.cases || []).length} 条用例可同步。`
    : `检查未通过：${(data.blockers || []).length} 个阻断项需要先处理。`;
  renderSonicStatusRows(rows, 'sonic-status-list');
  openModal('modal-sonic-status');
}

function renderSonicPublishResult(data, title='同步至 Sonic 平台结果') {
  const results = data.results || [];
  const rows = [];
  results.forEach(item => {
    const caseRows = (item.result || {}).results || [];
    if (caseRows.length) {
      caseRows.forEach(caseRow => rows.push({
        action: caseRow.status === 'failed' ? 'manual' : 'bridge',
        sonic_case_name: caseRow.task_name || caseRow.sonic_case_name || item.file,
        module: caseRow.module || item.module,
        file: caseRow.file || item.file,
        task_name: caseRow.task_name || '',
        case_id: caseRow.case_id || '',
        sonic_case_id: caseRow.sonic_case_id || '',
        project_name: caseRow.project_name || caseRow.sonic_project_name || '',
        step_label: caseRow.status === 'failed' ? '同步失败' : '已同步',
        reason: caseRow.error || caseRow.message || item.message || '',
      }));
    } else {
      rows.push({
        action: item.status === 'failed' || !item.ok ? 'manual' : 'bridge',
        sonic_case_name: item.file || '同步结果',
        module: item.module || '',
        file: item.file || '',
        step_label: item.status === 'failed' || !item.ok ? '同步失败' : '已同步',
        reason: item.error || item.message || '',
      });
    }
  });
  const totalFiles = data.total_files ?? data.total ?? results.length;
  const totalCases = data.total_cases ?? results.reduce((sum, item) => sum + (item.case_count || ((item.result || {}).results || []).length || 0), 0);
  const syncedCases = data.synced_cases ?? rows.filter(row => row.action !== 'manual').length;
  const failedFiles = data.failed || results.filter(item => item.status === 'failed' || !item.ok).length;
  document.getElementById('sonic-status-title').textContent = title;
  document.getElementById('sonic-status-summary').textContent =
    failedFiles
      ? `同步完成但有失败：文件 ${totalFiles} 个，用例 ${totalCases} 条，成功 ${syncedCases} 条，失败文件 ${failedFiles} 个。请查看下方每条结果。`
      : `同步成功：文件 ${totalFiles} 个，用例 ${totalCases} 条，成功 ${syncedCases} 条。下方可查看 Sonic case 和 Task case_id。`;
  renderSonicStatusRows(rows, 'sonic-status-list');
  openModal('modal-sonic-status');
}

function selectedSonicFiles() {
  return [...selectedFiles].map(key => {
    const parts = String(key || '').split('::');
    const mod = parts.shift() || '';
    const file = parts.join('::');
    return { module: mod, file };
  }).filter(item => item.module && item.file && modules[item.module]?.includes(item.file) && /\.ya?ml$/i.test(item.file));
}

function selectedAssetRowsForCurrentFilters(rows=assetRowsForCurrentFilters()) {
  return (rows || []).filter(row => selectedFiles.has(fileKey(row.mod, row.file)));
}

function selectedSonicFilesForCurrentFilters() {
  return selectedAssetRowsForCurrentFilters().map(row => ({ module: row.mod, file: row.file }));
}

function sonicBatchItemNeedsForce(item) {
  const status = fileMeta(item.module, item.file).status || 'draft';
  return !['active', 'baseline'].includes(status);
}

async function publishSonicBatchItems(items, options={}) {
  const targetItems = (items || [])
    .filter(item => item && item.module && item.file && /\.ya?ml$/i.test(item.file))
    .map(item => ({ module: item.module, file: item.file }));
  if (!targetItems.length) {
    showToast('请先选择要同步的 YAML 文件', 'error');
    return;
  }
  const uniqueMap = new Map();
  targetItems.forEach(item => uniqueMap.set(fileKey(item.module, item.file), item));
  const uniqueItems = [...uniqueMap.values()];
  const forceCount = uniqueItems.filter(sonicBatchItemNeedsForce).length;
  const scopeText = options.scopeText || `${uniqueItems.length} 个 YAML`;
  const warning = forceCount ? `\n\n注意：其中 ${forceCount} 个文件还不是“已入库/基线”，会按强制同步处理。` : '';
  if (!confirm(`确认同步 ${scopeText} 至 Sonic 平台？${warning}\n\n同步后 Sonic 用例会通过 case_id 回 Task 平台拉取最新版 YAML。`)) return;
  try {
    showToast('正在同步至 Sonic 平台，请稍候...', 'success');
    const data = await apiRequest('/sonic/publish-batch', {
      method: 'POST',
      body: JSON.stringify({
        items: uniqueItems.map(item => ({
          ...item,
          force: sonicBatchItemNeedsForce(item),
        })),
      })
    });
    const failed = Number(data.failed || 0);
    const summary = `文件 ${data.total_files || 0} 个，用例 ${data.total_cases || 0} 条`;
    showToast(failed ? `同步至 Sonic 平台完成：${summary}，失败 ${failed} 个文件` : `✓ 已同步至 Sonic 平台：${summary}`, failed ? 'error' : 'success');
    await refreshSonicPreview(true);
    await loadModules({force: true});
    renderSonicPublishResult(data, options.title || '批量同步结果');
  } catch(e) {
    showToast(e.message || '同步至 Sonic 平台失败', 'error');
  }
}

async function publishSelectedFilesToSonic() {
  const items = selectedSonicFilesForCurrentFilters();
  const hiddenSelectedCount = Math.max(0, selectedSonicFiles().length - items.length);
  await publishSonicBatchItems(items, {
    scopeText: `当前列表已选 ${items.length} 个 YAML${hiddenSelectedCount ? `（另有 ${hiddenSelectedCount} 个非当前列表选择不会同步）` : ''}`,
    title: '已选 YAML 同步结果',
  });
}

async function runSonicPublishCheck(taskName) {
  const data = await apiRequest('/sonic/publish-check', {
    method: 'POST',
    body: JSON.stringify({ module: currentModule, file: currentFile, taskName: taskName || '' })
  });
  if (!data.canPublish) {
    renderPublishCheckResult(data);
    throw new Error('同步前检查未通过');
  }
  if ((data.warnings || []).length || (data.fixes || []).length) {
    renderPublishCheckResult(data);
    if (!confirm(`同步前检查通过，但有 ${(data.warnings || []).length + (data.fixes || []).length} 条提醒。确认继续同步吗？`)) {
      throw new Error('已取消同步');
    }
  }
  return data;
}

async function deleteTaskApp(packageName) {
  if (!confirm(`确认删除应用分组「${packageName}」？不会删除 YAML 文件。`)) return;
  try {
    await apiRequest(`/task-app?package=${encodeURIComponent(packageName)}`, { method: 'DELETE' });
    taskApps = taskApps.filter(app => app.package !== packageName);
    renderTaskAppModal();
    renderModules();
    showToast('✓ 应用分组已删除', 'success');
  } catch(e) {
    showToast(e.message || '删除应用分组失败', 'error');
  }
}

async function publishCurrentFileToSonic() {
  if (!requireCurrentYaml('同步至 Sonic 平台')) return;
  const meta = fileMeta(currentModule, currentFile);
  const status = meta.status || 'draft';
  const force = status !== 'active' && status !== 'baseline';
  if (force && !confirm('当前文件还不是“已入库/基线”状态。为了避免把草稿同步至 Sonic 平台，建议先标记状态。确认仍要强制同步吗？')) {
    return;
  }
  const taskName = detectSelectedTaskName() || '';
  const scopeText = taskName ? `当前选中用例「${taskName}」` : '当前 YAML 文件中的全部用例';
  if (!confirm(`确认同步 ${scopeText} 至 Sonic 平台？\n\n同步后 Sonic 用例会通过 case_id 回 Task 平台拉取最新版 YAML。`)) return;
  const btn = document.getElementById('btn-publish-sonic');
  const oldText = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '同步中...';
  }
  try {
    const saved = await saveFile({showSuccess:false});
    if (!saved) throw new Error('当前文件为空，无法同步');
    await runSonicPublishCheck(taskName);
    const data = await apiRequest('/sonic/publish', {
      method: 'POST',
      body: JSON.stringify({
        module: currentModule,
        file: currentFile,
        taskName,
        force
      })
    });
    const count = (data.results || []).length;
    const failed = (data.results || []).filter(item => item.status === 'failed').length;
    const pinned = (data.caseIdChanges || []).length;
    showToast(failed ? `同步至 Sonic 平台完成，但 ${failed}/${count} 个失败` : `✓ 已同步至 Sonic 平台：${count} 条${pinned ? `，已固化 ${pinned} 个 case_id` : ''}`, failed ? 'error' : 'success');
    renderSonicPublishResult({
      ...data,
      total_files: 1,
      total_cases: count,
      synced_cases: Math.max(0, count - failed),
      failed: failed ? 1 : 0,
      results: [{
        module: currentModule,
        file: currentFile,
        status: failed ? 'failed' : 'success',
        ok: !failed,
        message: failed ? `失败 ${failed}/${count} 条` : `同步 ${count}/${count} 条用例`,
        result: data,
      }],
    }, taskName ? '单条用例同步结果' : '当前 YAML 同步结果');
    if (pinned) {
      showEditor(await apiTextRequest(`/file?module=${encodeURIComponent(currentModule)}&file=${encodeURIComponent(currentFile)}`));
    }
    await refreshSonicPreview(true);
    await loadModules({force: true});
  } catch(e) {
    showToast(e.message || '同步至 Sonic 平台失败', 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = oldText || '同步至 Sonic 平台';
    }
  }
}

async function publishCurrentModuleToSonic() {
  if (!currentModule || !modules[currentModule]) {
    showToast('请先选择一个模块', 'error');
    return;
  }
  const files = [...(modules[currentModule] || [])].filter(file => file.endsWith('.yaml') || file.endsWith('.yml'));
  if (!files.length) {
    showToast('当前模块没有 YAML 文件', 'error');
    return;
  }
  const selectedInModule = [...selectedFiles]
    .map(key => key.split('::'))
    .filter(([mod, file]) => mod === currentModule && files.includes(file))
    .map(([, file]) => file);
  const targetFiles = selectedInModule.length ? selectedInModule : files;
  await publishSonicBatchItems(targetFiles.map(file => ({ module: currentModule, file })), {
    scopeText: selectedInModule.length
      ? `模块「${currentModule}」中已选 ${selectedInModule.length} 个 YAML`
      : `模块「${currentModule}」全部 ${targetFiles.length} 个 YAML`,
    title: selectedInModule.length ? '已选 YAML 同步结果' : '模块同步结果',
  });
}
