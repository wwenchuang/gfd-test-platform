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

function renderWorkflowNav() {
  document.querySelectorAll('.workflow-step').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.workflow === activeWorkflow);
  });
  updateWorkflowActionGroups();
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
          <button class="btn-sm ${escapeHtml(action.cls || '')}" onclick="${action.fn}">${escapeHtml(action.label)}</button>
        `).join('')}
      </div>
    </div>
  `).join('');
  const checklist = (section.checklist || []).map(item => `✓ ${escapeHtml(item)}`).join('<br>');
  return `
    <div class="workflow-guide">
      <div class="workflow-hero">
        <div class="workflow-kicker">STEP ${escapeHtml(section.index)} · ${escapeHtml(section.title)}</div>
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
  if (sectionKey === 'assets' && typeof showAssetsCenter === 'function') {
    showAssetsCenter();
    return;
  }
  area.className = 'editor-area';
  area.innerHTML = workflowGuideHtml(sectionKey);
  updateToolbarState();
}
