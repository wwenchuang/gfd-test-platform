// navigation.js
// Extracted from task-manager.html (no logic changes).

// ===== RESIZABLE LAYOUT =====
const RESIZE_RULES = {
  sidebar: { cssVar: '--sidebar-width', min: 220, max: 420 },
  refs: { cssVar: '--refs-width', min: 220, max: 560 },
  caseNav: { cssVar: '--case-nav-width', min: 140, max: 320 },
  jobs: { cssVar: '--jobs-width', min: 280, max: 620 }
};

function clampNumber(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function setLayoutVar(name, value, persist = true) {
  const rule = RESIZE_RULES[name];
  if (!rule) return;
  const width = window.innerWidth || 1440;
  const compactMax = width <= 1380
    ? ({ sidebar: 260, refs: 300, caseNav: 180, jobs: 320 }[name] || rule.max)
    : width <= 1600
      ? ({ sidebar: 280, refs: 320, caseNav: 200, jobs: 360 }[name] || rule.max)
      : width <= 1900
        ? ({ sidebar: 320, refs: 360, caseNav: 220, jobs: 420 }[name] || rule.max)
        : rule.max;
  const next = clampNumber(Math.round(value), rule.min, compactMax);
  document.querySelector('.layout')?.style.setProperty(rule.cssVar, `${next}px`);
  if (persist) {
    layoutPrefs[name] = next;
    localStorage.setItem('midscene_layout_prefs', JSON.stringify(layoutPrefs));
  }
}

function applyLayoutPrefs() {
  Object.keys(RESIZE_RULES).forEach(name => {
    if (Number(layoutPrefs[name])) setLayoutVar(name, Number(layoutPrefs[name]), false);
  });
}

function resizeValueFromPointer(name, event) {
  if (name === 'sidebar') {
    const rect = document.querySelector('.layout')?.getBoundingClientRect();
    return rect ? event.clientX - rect.left : RESIZE_RULES.sidebar.min;
  }
  if (name === 'jobs') {
    const rect = document.querySelector('.workbench')?.getBoundingClientRect();
    return rect ? rect.right - event.clientX : RESIZE_RULES.jobs.min;
  }
  if (name === 'refs') {
    const rect = document.getElementById('editor-area')?.getBoundingClientRect();
    return rect ? event.clientX - rect.left : RESIZE_RULES.refs.min;
  }
  if (name === 'caseNav') {
    const rect = document.querySelector('.editor-wrap')?.getBoundingClientRect();
    return rect ? event.clientX - rect.left : RESIZE_RULES.caseNav.min;
  }
  return 0;
}

function initResizableLayout() {
  applyLayoutPrefs();
  document.addEventListener('pointerdown', event => {
    const handle = event.target.closest('[data-resize]');
    if (!handle) return;
    const name = handle.dataset.resize;
    if (!RESIZE_RULES[name]) return;
    event.preventDefault();
    handle.classList.add('active');
    document.body.classList.add('is-resizing');

    const onMove = moveEvent => {
      setLayoutVar(name, resizeValueFromPointer(name, moveEvent));
    };
    const onUp = () => {
      handle.classList.remove('active');
      document.body.classList.remove('is-resizing');
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  });
}

function isPanelCollapsed(name) {
  return !!layoutPrefs[`${name}Collapsed`];
}

function setPanelCollapsed(name, collapsed) {
  layoutPrefs[`${name}Collapsed`] = !!collapsed;
  localStorage.setItem('midscene_layout_prefs', JSON.stringify(layoutPrefs));
  applyEditorPanelState();
}

function toggleRefsPanel() {
  setPanelCollapsed('refs', !isPanelCollapsed('refs'));
}

function toggleCaseNavPanel() {
  setPanelCollapsed('caseNav', !isPanelCollapsed('caseNav'));
}

function applyEditorPanelState() {
  const area = document.getElementById('editor-area');
  const wrap = document.querySelector('.editor-wrap');
  if (area) area.classList.toggle('refs-collapsed', isPanelCollapsed('refs'));
  if (wrap) wrap.classList.toggle('case-nav-collapsed', isPanelCollapsed('caseNav'));
  const refsToggle = document.getElementById('toggle-refs-panel');
  if (refsToggle) refsToggle.textContent = isPanelCollapsed('refs') ? '显示截图' : '隐藏截图';
  const caseToggle = document.getElementById('toggle-case-panel');
  if (caseToggle) caseToggle.textContent = isPanelCollapsed('caseNav') ? '显示用例' : '隐藏用例';
}


function toolbarStateChip(text, cls='') {
  return `<span class="toolbar-state-chip ${escapeHtml(cls)}">${escapeHtml(text)}</span>`;
}

function updateToolbarState(message='') {
  const el = document.getElementById('toolbar-state');
  if (!el) return;
  const saveButton = document.getElementById('btn-save');
  if (saveButton && currentFile) {
    saveButton.disabled = !editorDirty;
    saveButton.title = editorDirty
      ? '保存当前编辑器中的 YAML'
      : '当前内容没有修改，无需保存';
  }
  const chips = [];
  if (currentModule) {
    const appPackage = moduleAppPackage(currentModule);
    chips.push(toolbarStateChip(appPackage ? appDisplayLabel(appPackage) : currentModule, appPackage ? 'info' : ''));
  }
  if (currentFile) {
    chips.push(toolbarStateChip(editorDirty ? '未保存修改' : '已保存', editorDirty ? 'warn' : 'success'));
    const taskName = detectSelectedTaskName();
    if (taskName) chips.push(toolbarStateChip(`当前用例：${taskName}`));
    const job = latestJobForFile(currentModule, currentFile);
    if (job?.status) {
      const cls = job.status === 'failed' ? 'danger' : (['success', 'passed'].includes(job.status) ? 'success' : 'info');
      chips.push(toolbarStateChip(`最近执行：${jobStatusText(job.status)}`, cls));
    }
  } else if (!currentModule) {
    chips.push(toolbarStateChip('未选择 YAML'));
  } else {
    chips.push(toolbarStateChip('模块目录'));
  }
  if (message) chips.push(toolbarStateChip(message, 'info'));
  el.innerHTML = chips.join('');
}

function lifecycleText(status) {
  const map = {
    draft: '草稿',
    review: '待评审',
    active: '已入库',
    baseline: '基线',
    maintenance: '需维护',
    blocked: '阻塞',
    deprecated: '废弃'
  };
  return map[status] || '草稿';
}

function hasOpenEditor() {
  return !!document.getElementById('editor');
}

function isFileWorkflow(sectionKey = activeWorkflow) {
  return ['generate', 'execute', 'repair', 'baseline'].includes(sectionKey);
}

// Keep optional guidance outside editor-area: page loaders replace its content.
const WORKFLOW_NAVIGATION_HELP = Object.freeze({
  dashboard: '按目标组织自动化测试。先选择应用、范围和模式，启动后查看进度；日常执行已有用例可到“开始测试”。',
  agent: '按目标组织自动化测试。先选择应用、范围和模式，启动后查看进度和待确认事项。',
  agent_history: '查看自动化任务的运行过程。先筛选记录，再打开详情查看产物；遇到失败先查看失败原因。',
  agent_confirm: '处理需要你确认的变更。先核对变更内容和证据，再确认或拒绝；确认后任务才继续。',
  assets: '整理可复用的测试资料与脚本。先按应用找到用例，再打开编辑或调试；执行已发布用例可到“开始测试”。',
  generate: '从需求生成测试用例。先提供需求与应用信息，生成后检查分析和覆盖范围，再调试脚本。',
  yaml_edit: '修改测试脚本。先从“用例资产”打开文件，修改后保存并调试；保存后仍需验证执行结果。',
  execute: '验证新建或修改的脚本。先打开文件并选择用例，调试后查看报告、截图和失败原因。',
  baseline: '把已验证的脚本同步到 Sonic。先确认执行通过和应用归属，再同步；后续回归使用稳定基线。',
  reports: '查找历史执行结果。先筛选任务，再打开报告或证据；你在“开始测试”发起的记录可到“我的测试”查看。',
  failure_analysis: '判断失败原因并安排处理。先看截图和日志；脚本问题可生成修复草稿，设备或环境异常先恢复环境。',
  repair: '根据失败证据修复脚本。先确认属于脚本问题，再生成并审核修复草稿，应用后重新调试。',
  bug_drafts: '整理准备提交的缺陷。先核对描述与证据，确认后再提交到飞书；草稿阶段尚未提交。',
  identity: '管理成员可用的功能。先选择成员并核对职责，再调整权限；缺少管理权限时联系管理员。',
  config: '设置各项 AI 能力使用的模型。需要调整时先核对当前配置，保存后测试服务；服务密钥由管理员维护。',
  app_config: '维护测试资源所属的应用。先核对应用信息和模块归属，再配置执行平台绑定与通知目标。',
  sonic_config: '检查手机与执行环境是否就绪。先查看在线状态和应用绑定，异常时运行体检并联系管理员处理。',
  feishu_config: '设置每个应用接收通知的群。先选择对应应用并核对通知目标，再维护配置；发版通知还需在开始测试时勾选。',
  system_config: '检查服务、设备与模型是否可用。先查看体检结果，再按异常项处理；恢复后重新检查并回到测试。',
  knowledge: '保存可复用的页面截图与说明。先选择应用并补充入口和关键元素，后续生成与修复会参考这些资料。',
  operation_history: '查看自己的业务操作记录。先按时间或操作类型筛选，再打开详情核对操作结果。'
});

function updateWorkflowNavigationHelp() {
  const details = document.getElementById('workflow-navigation-help');
  const text = document.getElementById('workflow-navigation-help-text');
  if (!details || !text) return;
  const guidance = WORKFLOW_NAVIGATION_HELP[activeWorkflow] || '';
  if (details.dataset.helpWorkflow !== activeWorkflow) details.open = false;
  details.dataset.helpWorkflow = activeWorkflow;
  details.hidden = !guidance;
  text.textContent = guidance;
}

function renderWorkflowNav() {
  updateWorkflowNavigationHelp();
  document.querySelectorAll('.workflow-step').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.workflow === activeWorkflow);
  });
  const activeButton = document.querySelector(`.workflow-step[data-workflow="${activeWorkflow}"]`);
  const activeGroup = activeButton?.closest('.nav-group');
  if (activeGroup) activeGroup.open = true;
  updateWorkflowActionGroups();
  if (typeof updateNavigationBadges === 'function') updateNavigationBadges();
}

function updateWorkflowActionGroups() {
  const actions = document.querySelector('.toolbar-actions');
  let visibleCount = 0;
  document.querySelectorAll('.workflow-action').forEach(group => {
    const workflows = (group.dataset.workflows || '').split(/\s+/).filter(Boolean);
    const needsFile = group.classList.contains('file-only');
    const hasFileContext = !!currentFile && group.dataset.fileContext !== '0';
    const blockedByMode = activeWorkspaceMode === 'mindmap' && group.id === 'generate-action-group';
    const show = workflows.includes(activeWorkflow) && (!needsFile || hasFileContext) && !blockedByMode;
    group.style.display = show ? 'flex' : 'none';
    if (show) visibleCount += 1;
  });
  if (actions) actions.classList.toggle('is-empty', visibleCount === 0);
}


function workflowGuideHtml(sectionKey = activeWorkflow) {
  if (sectionKey === 'dashboard') return workflowDashboardHtml();
  const section = WORKFLOW_SECTIONS[sectionKey] || WORKFLOW_SECTIONS.assets;
  const cards = (section.cards || []).map(card => `
    <div class="workflow-card">
      <h3>${escapeHtml(card.title)}</h3>
      <p>${escapeHtml(card.text)}</p>
      <div class="workflow-card-actions">
        ${(card.actions || []).map(action => `
          <button class="btn-sm ${escapeHtml(action.cls || '')}" onclick="${escapeHtml(action.fn || '')}">${escapeHtml(action.label)}</button>
        `).join('')}
      </div>
    </div>
  `).join('');
  const checklist = (section.checklist || []).map(item => `✓ ${escapeHtml(item)}`).join('<br>');
  return `
    <div class="workflow-guide">
      <div class="workflow-hero">
        <div class="workflow-kicker">第 ${escapeHtml(section.index)} 步 · ${escapeHtml(section.title)}</div>
        <h2>${escapeHtml(section.subtitle)}</h2>
        <p>${escapeHtml(section.help)}</p>
      </div>
      <div class="workflow-grid">${cards}</div>
      <div class="workflow-checklist">${checklist}</div>
    </div>
  `;
}

function showWorkflowGuide(sectionKey = activeWorkflow) {
  const area = document.getElementById('editor-area');
  if (!area) return;
  if (sectionKey === 'agent' || sectionKey === 'dashboard') {
    showAgentWorkbench();
    return;
  }
  if (sectionKey === 'agent_history' && typeof renderAgentHistoryPage === 'function') {
    renderAgentHistoryPage();
    return;
  }
  if (sectionKey === 'agent_confirm' && typeof renderAgentConfirmPage === 'function') {
    renderAgentConfirmPage({ refresh: false });
    return;
  }
  if (sectionKey === 'assets' && typeof showAssetsCenter === 'function') {
    showAssetsCenter();
    return;
  }
  area.className = 'editor-area';
  area.innerHTML = workflowGuideHtml(sectionKey);
  updateToolbarState();
}
