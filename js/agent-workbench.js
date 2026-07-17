// agent-workbench.js
// Extracted from task-manager.html (no logic changes).

// 记录用户手动展开的步骤索引，避免轮询刷新时收起
const expandedStepIndexes = new Set();
let agentCheckpointTraceOpen = false;
const DEFAULT_AGENT_APP_NAME = '智小白3D APP';
const DEFAULT_AGENT_APP_PACKAGE = 'com.kfb.model';

function agentDefaultApp() {
  return {
    name: DEFAULT_AGENT_APP_NAME,
    package: DEFAULT_AGENT_APP_PACKAGE,
    modules: []
  };
}

function agentAppsWithDefault(apps) {
  const list = Array.isArray(apps) ? apps.filter(Boolean) : [];
  const hasDefault = list.some(app => {
    const name = String(app.name || '').trim();
    const packageName = String(app.package || app.appPackage || app.app_package || '').trim();
    return name === DEFAULT_AGENT_APP_NAME || packageName === DEFAULT_AGENT_APP_PACKAGE;
  });
  return hasDefault ? list : [agentDefaultApp(), ...list];
}

function appendAgentAppOptions(select, apps, preferredValue) {
  if (!select) return;
  const currentValue = preferredValue || select.value || DEFAULT_AGENT_APP_NAME;
  select.innerHTML = '';
  agentAppsWithDefault(apps).forEach(app => {
    const opt = document.createElement('option');
    opt.value = app.name || app.package || '';
    opt.textContent = app.name || app.package || '未命名应用';
    opt.dataset.package = app.package || app.appPackage || app.app_package || '';
    opt.dataset.modules = JSON.stringify(app.modules || []);
    select.appendChild(opt);
  });
  const options = Array.from(select.options);
  const selectedOption = options.find(o => (
    o.value === currentValue ||
    o.dataset.package === currentValue
  )) || options.find(o => (
    o.value === DEFAULT_AGENT_APP_NAME ||
    o.dataset.package === DEFAULT_AGENT_APP_PACKAGE
  ));
  if (selectedOption) {
    select.value = selectedOption.value;
  }
}

function normalizeAgentProviderList(data) {
  const source = data?.providers;
  if (Array.isArray(source)) {
    return source.filter(Boolean).map(provider => ({
      id: String(provider.id || provider.providerId || '').trim(),
      name: String(provider.name || provider.id || provider.providerId || '').trim(),
      model: String(provider.model || '').trim(),
      configured: provider.configured !== false,
      type: provider.type || 'openai_compatible',
      temperatureLocked: Boolean(provider.temperatureLocked),
      fixedTemperature: provider.fixedTemperature,
      catalogSource: String(provider.catalogSource || '').trim()
    })).filter(provider => provider.id);
  }
  if (source && typeof source === 'object') {
    return Object.entries(source).map(([id, provider]) => ({
      id: String(id || '').trim(),
      name: String(provider?.name || id || '').trim(),
      model: String(provider?.model || '').trim(),
      configured: provider?.configured !== false,
      type: provider?.type || 'openai_compatible',
      temperatureLocked: Boolean(provider?.temperatureLocked),
      fixedTemperature: provider?.fixedTemperature,
      catalogSource: String(provider?.catalogSource || '').trim()
    })).filter(provider => provider.id);
  }
  return [];
}

function agentProviderDisplayText(provider) {
  if (!provider) return '';
  const model = provider.model ? ` · ${provider.model}` : '';
  const configured = provider.configured === false ? ' · 未配置 Key' : '';
  const locked = provider.temperatureLocked ? ' · 固定参数' : '';
  const source = provider.catalogSource === 'live'
    ? ' · 实时目录'
    : provider.catalogSource === 'configured_fallback'
      ? ' · 目录降级'
      : '';
  return `${provider.name || provider.id}${model}${source}${configured}${locked}`;
}

function addAgentModelOption(parent, option) {
  const opt = document.createElement('option');
  opt.value = option.value || '';
  opt.textContent = option.label || option.value || '';
  opt.dataset.kind = option.kind || '';
  opt.dataset.providerId = option.providerId || '';
  opt.dataset.model = option.model || '';
  if (option.disabled) opt.disabled = true;
  parent.appendChild(opt);
  return opt;
}

function selectedAgentModelInfo() {
  const select = document.getElementById('agent-model');
  const option = select?.selectedOptions?.[0];
  if (!select || !option) {
    return {kind: '', providerId: '', model: '', value: ''};
  }
  return {
    kind: option.dataset.kind || '',
    providerId: option.dataset.providerId || '',
    model: option.dataset.model || (option.dataset.kind === 'task-model' ? option.value : ''),
    value: select.value || ''
  };
}

function normalizeAgentRouterProviderId(routerData) {
  const router = routerData?.router || routerData || {};
  const value = router.agent_plan;
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'object') return value.providerId || value.provider || '';
  return '';
}

async function loadAgentModelOptions(preferredValue='') {
  const modelSel = document.getElementById('agent-model');
  if (!modelSel) return;
  const previous = preferredValue || modelSel.value || '';
  modelSel.innerHTML = '';
  const autoOpt = addAgentModelOption(modelSel, {
    value: '',
    label: '自动（按模型策略）',
    kind: 'auto'
  });

  const cachedProviders = (typeof AppState !== 'undefined' && Array.isArray(AppState.modelProviders)) ? AppState.modelProviders : [];
  const cachedRouterProviderId = normalizeAgentRouterProviderId((typeof AppState !== 'undefined' && AppState.modelRouter) ? AppState.modelRouter : {});
  if (cachedProviders.length) {
    const cachedProvider = cachedProviders.find(provider => provider.id === cachedRouterProviderId) || cachedProviders[0];
    if (cachedProvider) {
      autoOpt.textContent = `自动（按模型策略：${cachedProvider.name || cachedProvider.id}）`;
      autoOpt.dataset.providerId = cachedProvider.id;
      autoOpt.dataset.model = cachedProvider.model || '';
    }
  }

  let gatewayProviders = [];
  let routerProviderId = '';
  try {
    const [providersData, routerData] = await Promise.all([
      aiGatewayGet('/ai/providers'),
      aiGatewayGet('/ai/model-router')
    ]);
    gatewayProviders = normalizeAgentProviderList(providersData);
    routerProviderId = normalizeAgentRouterProviderId(routerData);
    aiProviders = gatewayProviders;
    aiModelRouter = routerData?.router || {};
    AppState.modelProviders = gatewayProviders;
    AppState.modelRouter = aiModelRouter;
    AppState.loaded.modelConfig = true;
  } catch (e) {
    gatewayProviders = [];
    routerProviderId = '';
  }

  if (gatewayProviders.length) {
    const selectedProvider = gatewayProviders.find(provider => provider.id === routerProviderId) || gatewayProviders[0];
    if (selectedProvider) {
      autoOpt.textContent = `自动（按模型策略：${selectedProvider.name || selectedProvider.id}）`;
      autoOpt.dataset.providerId = selectedProvider.id;
      autoOpt.dataset.model = selectedProvider.model || '';
    }
    const group = document.createElement('optgroup');
    group.label = 'AI Gateway Provider';
    for (const provider of gatewayProviders) {
      addAgentModelOption(group, {
        value: `provider:${provider.id}`,
        label: agentProviderDisplayText(provider),
        kind: 'ai-gateway-provider',
        providerId: provider.id,
        model: provider.model,
        disabled: provider.configured === false
      });
    }
    modelSel.appendChild(group);
  }

  try {
    const mData = await apiRequest('/models');
    const models = (Array.isArray(mData?.models) ? mData.models : [])
      .filter(model => !(gatewayProviders.length && model.group === 'AI Gateway'));
    if (!gatewayProviders.length) {
      const defaultModel = models.find(m => m.default);
      if (defaultModel) {
        autoOpt.textContent = `自动（${defaultModel.name || defaultModel.id}）`;
        autoOpt.dataset.model = defaultModel.id || '';
      }
    }
    const groups = {};
    for (const model of models) {
      const key = model.group || 'Task 服务端';
      if (!groups[key]) groups[key] = [];
      groups[key].push(model);
    }
    for (const [groupName, items] of Object.entries(groups)) {
      const optgroup = document.createElement('optgroup');
      optgroup.label = groupName;
      const seenInGroup = new Set();
      for (const item of items) {
        const id = String(item.id || '').trim();
        if (!id || seenInGroup.has(id)) continue;
        seenInGroup.add(id);
        addAgentModelOption(optgroup, {
          value: id,
          label: item.name || id,
          kind: 'task-model',
          model: id,
          providerId: item.providerId || ''
        });
      }
      if (optgroup.children.length) modelSel.appendChild(optgroup);
    }
  } catch (e) {}

  if (previous && Array.from(modelSel.options).some(option => option.value === previous && !option.disabled)) {
    modelSel.value = previous;
  }
}

function dashboardStats() {
  const rows = moduleFileRows();
  const yamlCount = rows.length;
  const baselineCount = rows.filter(row => ['baseline', 'active'].includes(row.meta.status || '')).length;
  const sonicSynced = rows.filter(row => sonicBadgeHtml(row.mod, row.file).includes('sonic-badge')).length;
  const failedJobs = latestJobs.filter(job => job.status === 'failed');
  const activeJobs = latestJobs.filter(job => ['pending', 'running'].includes(job.status));
  const needConfirm = rows.filter(row => ['draft', 'review', 'blocked', 'maintenance'].includes(row.meta.status || 'draft')).length;
  return {
    loaded: modulesLoaded,
    rows,
    yamlCount,
    moduleCount: Object.keys(modules || {}).length,
    baselineCount,
    sonicSynced,
    failedJobs,
    activeJobs,
    needConfirm
  };
}

function dashboardStatusPill(text, cls='') {
  return `<span class="dashboard-pill ${escapeHtml(cls)}">${escapeHtml(text)}</span>`;
}

function dashboardActionButton(text, fn) {
  return `<button class="dashboard-inline-action" type="button" onclick="${fn}">${escapeHtml(text)}</button>`;
}

function isGenerateBackgroundJob(job) {
  if (!job || job.kind !== 'background') return false;
  const result = job.result || {};
  const text = [
    job.type,
    job.step,
    job.message,
    result.case_set_id,
    result.file,
    result.module,
    result.summary?.case_set_id
  ].filter(Boolean).join(' ');
  return job.type === 'generate'
    || !!result.case_set_id
    || /生成|YAML|用例|case_set/i.test(text);
}

function generationJobs() {
  return latestJobs.filter(isGenerateBackgroundJob);
}

function dashboardNextItems(stats) {
  const items = [];
  if (!stats.loaded) {
    return [{
      cls: '',
      title: '正在加载平台资产',
      text: '读取 YAML、Sonic 和执行状态。',
      action: '<button class="btn-sm" onclick="loadModules()">重新加载</button>'
    }];
  }
  if (!stats.yamlCount) {
    items.push({
      cls: 'warn',
      title: '先生成一批用例',
      text: '上传需求和 UI，生成 YAML。',
      action: '<button class="btn-sm primary" onclick="showGenerateYaml()">新建自动化测试</button>'
    });
  }
  if (stats.needConfirm) {
    items.push({
      cls: 'warn',
      title: `${stats.needConfirm} 个 YAML 还在草稿/待评审状态`,
      text: '先调试确认，再入库同步。',
      action: '<button class="btn-sm" onclick="setLibraryView(\'recent\')">查看最近资产</button>'
    });
  }
  if (stats.failedJobs.length) {
    items.push({
      cls: 'error',
      title: `${stats.failedJobs.length} 个执行失败待归因`,
      text: '先看报告，再决定是否修复。',
      action: '<button class="btn-sm ai" onclick="setLibraryView(\'failed\')">查看失败</button>'
    });
  }
  if (stats.baselineCount && stats.sonicSynced < stats.baselineCount) {
    items.push({
      cls: 'warn',
      title: '有基线用例尚未同步至 Sonic 平台',
      text: '跑通的基线再进 Sonic。',
      action: '<button class="btn-sm success" onclick="activateWorkflow(\'baseline\')">处理基线</button>'
    });
  }
  if (!items.length) {
    items.push({
      cls: '',
      title: '主链路状态良好',
      text: '继续新增，或处理回归失败。',
      action: '<button class="btn-sm primary" onclick="showGenerateYaml()">继续新增</button>'
    });
  }
  return items.slice(0, 4);
}

function workflowDashboardHtml() {
  const stats = dashboardStats();
  const nextItems = dashboardNextItems(stats);
  const recentGenerateJobs = generationJobs().slice(0, 3);
  const metricText = value => stats.loaded ? value : '...';
  const runnerText = runnerDevices.length ? `${runnerDevices.length} 台在线` : '待加载';
  const sonicText = !stats.loaded ? '加载中' : (sonicCaseRows.length ? `${sonicCaseRows.length} 条桥接` : '待检查');
  const qwenText = '用环境体检确认';
  const shouldOpenSystem = !stats.loaded || !runnerDevices.length || !sonicCaseRows.length;
  const shouldOpenRecent = recentGenerateJobs.length > 0;
  return `
    <div class="dashboard-guide">
      <div class="dashboard-hero">
        <div class="dashboard-command">
          <div>
            <div class="workflow-kicker">自动化 Agent · 自动规划 / 执行测试 / 失败分析 / 安全重跑</div>
            <h2>全自动 Agent 工作台</h2>
            <p>输入测试目标后，Agent 自动完成用例选择、YAML 生成、Runner 执行、失败分析、修复草稿、重跑和报告沉淀；测试机业务风险只提醒，平台级写操作才人工确认。</p>
            <div class="agent-form-grid" style="margin-top:14px;">
              <label class="agent-field">
                <span>你想让 Agent 测什么</span>
                <textarea id="dashboard-agent-goal" rows="3" placeholder="例如：回归智小白3D APP 关节龙打印流程，自动生成执行计划，失败后自动分析并生成修复草稿"></textarea>
              </label>
              <div class="dashboard-agent-options">
                <label class="agent-field">
                  <span>最近失败任务</span>
                  <select id="dashboard-failed-job">
                    <option value="">自动选择最近失败任务</option>
                    ${stats.failedJobs.slice(0, 12).map(job => `<option value="${escapeHtml(job.job_id || '')}">${escapeHtml(job.file || job.task_name || job.job_id || '失败任务')}</option>`).join('')}
                  </select>
                </label>
                <div class="agent-form-grid" style="grid-template-columns:1fr 1fr;">
                  <label class="agent-field"><span>APP 名称</span><select id="dashboard-agent-app"><option value="智小白3D APP" data-package="com.kfb.model" selected>智小白3D APP</option></select></label>
                  <label class="agent-field"><span>平台</span><select id="dashboard-agent-platform"><option value="android">android</option><option value="ios">ios</option></select></label>
                </div>
              </div>
            </div>
            <div class="dashboard-actions">
              <button class="btn-sm primary" onclick="launchDashboardAgent()">启动全自动 Agent</button>
              <button class="btn-sm" onclick="previewDashboardAgentPlan()">预览执行计划</button>
              <button class="btn-sm ai" onclick="analyzeDashboardFailure()">只分析不执行</button>
            </div>
          </div>
          <div class="dashboard-command-card">
            <strong>当前策略：稳定省钱版</strong>
            <div class="path-rail" aria-label="生成用例 / 生成 YAML / 失败分析 / YAML 修复 / Agent 判断 / 飞书缺陷">
              <span class="path-node">用例：Qwen Plus</span>
              <span class="path-node">YAML：Qwen Plus</span>
              <span class="path-node">失败分析：Qwen Plus</span>
              <span class="path-node">修复：Qwen Plus</span>
              <span class="path-node">缺陷：Qwen Plus</span>
            </div>
            <div style="margin-top:12px;"><button class="btn-sm" onclick="showModelConfigCenter()">查看模型策略</button></div>
          </div>
        </div>
      </div>
      <div class="dashboard-grid">
        <div class="dashboard-panel dashboard-primary-panel">
          <h3>下一步建议</h3>
          <div class="dashboard-next-list">
            ${nextItems.map(item => `
              <div class="dashboard-next-card ${escapeHtml(item.cls || '')} ${item.action ? 'actionable' : ''}">
                <strong>${escapeHtml(item.title)}</strong>
                <p>${escapeHtml(item.text)}</p>
                <div>${item.action || ''}</div>
              </div>
            `).join('')}
          </div>
        </div>
        <div class="dashboard-stack">
          <details class="dashboard-panel dashboard-accordion" ${shouldOpenRecent ? 'open' : ''}>
            <summary><h3>最近生成任务</h3></summary>
            <div class="dashboard-accordion-body">
              <div class="dashboard-next-list">
                ${recentGenerateJobs.length ? recentGenerateJobs.map(job => {
                  const result = job.result || {};
                  const title = result.file || job.file || job.title || job.step || '生成任务';
                  const mod = result.module || job.module || '';
                  const caseSetId = result.case_set_id || result.summary?.case_set_id || job.case_set_id || '';
                  return `
                    <div class="dashboard-next-card ${escapeHtml(job.status === 'failed' ? 'error' : '')}">
                      <strong>${escapeHtml(title)}</strong>
                      <p>${escapeHtml([mod, jobStatusText(job.status), jobTimeText(job)].filter(Boolean).join(' · '))}</p>
                      <div>
                        ${caseSetId ? `<button class="btn-sm" onclick="showGenerationReviewByCaseSet(${jsArg(caseSetId)})">生成分析</button>` : ''}
                        ${mod && title ? `<button class="btn-sm primary" onclick="openFile(${jsArg(mod)}, ${jsArg(title)})">打开 YAML</button>` : ''}
                      </div>
                    </div>
                  `;
                }).join('') : `
                  <div class="dashboard-next-card">
                    <strong>暂无生成任务</strong>
                    <p>上传需求或 UI 稿后，这里会显示需求解析和 YAML 生成进度。</p>
                    <div><button class="btn-sm primary" onclick="showGenerateYaml()">新建自动化测试</button></div>
                  </div>
                `}
              </div>
              <div><button class="btn-sm" onclick="showGenerateJobsCenter()">查看全部生成记录</button></div>
            </div>
          </details>
          <details class="dashboard-panel dashboard-accordion" ${shouldOpenSystem ? 'open' : ''}>
            <summary><h3>平台运行状态</h3></summary>
            <div class="dashboard-accordion-body">
              <div class="dashboard-system-grid">
                <div class="dashboard-system-row"><strong>Task</strong><span>${stats.loaded ? `${stats.moduleCount} 个模块，${stats.yamlCount} 个 YAML` : '资产加载中'}</span>${dashboardStatusPill(stats.loaded ? (stats.yamlCount ? '可用' : '待建') : '加载中', stats.loaded ? (stats.yamlCount ? 'ok' : 'warn') : 'warn')}</div>
                <div class="dashboard-system-row"><strong>千问</strong><span>${qwenText}</span>${dashboardActionButton('检查', 'showPreflightDashboard()')}</div>
                <div class="dashboard-system-row"><strong>Midscene</strong><span>${runnerText}</span>${dashboardStatusPill(runnerDevices.length ? '在线' : '待检查', runnerDevices.length ? 'ok' : 'warn')}</div>
                <div class="dashboard-system-row"><strong>Sonic</strong><span>${sonicText}</span>${dashboardStatusPill(sonicCaseRows.length ? '已连接' : '待绑定', sonicCaseRows.length ? 'ok' : 'warn')}</div>
              </div>
              <div class="dashboard-accordion-note">AI 生成的草稿先在 Task 里调试；单条跑通后再入基线，需要长期回归的用例再同步至 Sonic 平台。</div>
            </div>
          </details>
        </div>
      </div>
    </div>
  `;
}

function dashboardSelectedFailedJob() {
  const selectedId = document.getElementById('dashboard-failed-job')?.value || '';
  const failed = dashboardStats().failedJobs || [];
  return failed.find(job => job.job_id === selectedId) || failed[0] || null;
}

async function analyzeDashboardFailure() {
  const job = dashboardSelectedFailedJob();
  if (!job) {
    showToast('暂无失败任务。可以先输入测试目标并启动 Agent。', 'warn');
    return;
  }
  await analyzeFailureFromJob(job);
}

function launchDashboardAgent() {
  const goal = document.getElementById('dashboard-agent-goal')?.value || '';
  const appName = document.getElementById('dashboard-agent-app')?.value || DEFAULT_AGENT_APP_NAME;
  const platform = document.getElementById('dashboard-agent-platform')?.value || 'android';
  activeWorkflow = 'agent';
  renderWorkflowNav();
  showAgentWorkbench();
  setTimeout(async () => {
    await loadAppList(appName);
    const goalInput = document.getElementById('agent-goal');
    const appInput = document.getElementById('agent-app-name');
    const platformInput = document.getElementById('agent-platform');
    const sourceInput = document.getElementById('agent-source-type');
    if (goalInput) goalInput.value = goal;
    if (appInput) appInput.value = appName;
    if (platformInput) platformInput.value = platform;
    if (sourceInput) sourceInput.value = 'manual';
    renderAgentSourcePanel();
    updateAgentRiskHint();
    if (typeof updateAgentRunnerDeviceHint === 'function') updateAgentRunnerDeviceHint();
  }, 0);
}


// Form field IDs used in the Agent workbench for value preservation across re-renders
const _AGENT_FORM_FIELD_IDS = [
  'agent-goal', 'agent-app-name', 'agent-platform', 'agent-scope',
  'agent-mode-select', 'agent-runner-device', 'agent-failed-job', 'agent-source-type',
  'agent-source-generate-job-id', 'agent-source-case-set-id',
  'agent-source-figma-url', 'agent-source-requirement-text',
  'agent-source-failed-job-id'
];

const AGENT_SOURCE_TYPES = [
  ['manual', '直接输入目标', '只根据当前测试目标和页面知识做分析'],
  ['requirement', '引用需求生成记录', '复用已上传需求、补充资料和生成批次'],
  ['figma', '引用 Figma/UI 资料', '优先结合设计稿、页面知识和当前目标'],
  ['failed_job', '引用失败任务', '只针对指定失败任务分析和修复']
];

async function openAgentAppInstall() {
  try {
    if (typeof setExecutionTab !== 'function') {
      showToast('安装包更新入口还没有加载完成，请稍后再试', 'warn');
      return;
    }
    if (typeof activateWorkflow === 'function' && activeWorkflow !== 'execute') {
      await activateWorkflow('execute');
    } else if (typeof setActiveWorkflow === 'function' && activeWorkflow !== 'execute') {
      setActiveWorkflow('execute');
    }
    setExecutionTab('install');
  } catch (e) {
    showToast('打开安装包更新失败：' + (e.message || e), 'error');
  }
}

async function showAgentWorkbench() {
  const area = document.getElementById('editor-area');
  if (!area) return;

  const shouldHydrateRuns = typeof ensureAgentRunsLoaded === 'function'
    && (typeof AppState === 'undefined' || !AppState.loaded?.agentRuns);

  const run = currentAgentRun();

  // Preserve form values before innerHTML replacement (prevents textarea/input reset during polling)
  const savedFormState = {};
  _AGENT_FORM_FIELD_IDS.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      savedFormState[id] = {
        value: el.value,
        selectionStart: el.selectionStart,
        selectionEnd: el.selectionEnd
      };
    }
  });
  const savedActiveId = document.activeElement?.id || '';

  const riskLevel = classifyRiskLevel(savedFormState['agent-goal']?.value || document.getElementById('agent-goal')?.value || '');
  area.className = 'editor-area agent-workbench';
  area.innerHTML = `
    <div class="agent-shell">
      <div class="agent-hero">
        <div class="workflow-kicker">自动化 Agent · 自动规划 / 自动执行 / 安全确认 / 可追踪产物</div>
        <h2>全自动 Agent 工作台</h2>
        <p>输入测试目标，Agent 自动完成用例选择、YAML 生成、Sonic 执行、失败分析、修复草稿和报告沉淀。</p>
      </div>

      <!-- 主卡片：单一启动入口 -->
      <div class="agent-card agent-primary-card">
        <div class="agent-form-grid agent-start-layout">
          <section class="agent-form-section agent-goal-section">
            <div class="agent-section-head">
              <span>01</span>
              <div>
                <strong>测试目标</strong>
                <em>先写你要验证什么，Agent 再判断范围和执行方式</em>
              </div>
            </div>
            <div class="agent-field agent-goal-field">
              <label for="agent-goal">目标描述</label>
              <textarea id="agent-goal" rows="3" oninput="updateAgentRiskHint()" placeholder="例如：回归智小白3D APP 关节龙打印流程，重点检查搜索、切片进度、确认打印按钮是否正常。"></textarea>
              <div class="agent-risk" id="agent-risk-hint"></div>
            </div>
            <div class="agent-field">
              <label for="agent-source-type">输入来源</label>
              <select id="agent-source-type" onchange="renderAgentSourcePanel()">
                ${AGENT_SOURCE_TYPES.map(([value, label, desc]) => `<option value="${escapeHtml(value)}" title="${escapeHtml(desc)}">${escapeHtml(label)}</option>`).join('')}
              </select>
            </div>
            <div id="agent-source-panel" class="agent-source-panel"></div>
          </section>

          <section class="agent-form-section agent-config-section">
            <div class="agent-section-head">
              <span>02</span>
              <div>
                <strong>执行配置</strong>
                <em>应用、设备、模式和模型集中在这里选择</em>
              </div>
            </div>
            <div class="agent-compact-grid">
              <div class="agent-field">
                <label for="agent-app-name">应用</label>
                <select id="agent-app-name" onchange="refreshAgentRunnerDeviceByApp()">
                  <option value="智小白3D APP" data-package="com.kfb.model" selected>智小白3D APP</option>
                </select>
              </div>
              <div class="agent-field">
                <label for="agent-platform">平台</label>
                <select id="agent-platform">
                  <option value="android">Android</option>
                  <option value="ios">iOS</option>
                </select>
              </div>
              <div class="agent-field">
                <label for="agent-scope">执行范围</label>
                <select id="agent-scope" onchange="toggleFailedJobField()">
                  <option value="auto" selected>自动（AI 判断）</option>
                  <option value="smoke">冒烟</option>
                  <option value="regression">回归</option>
                  <option value="failed_rerun">失败重跑</option>
                  <option value="module">指定模块</option>
                </select>
              </div>
              <div class="agent-field">
                <label for="agent-mode-select">Agent 模式</label>
                <select id="agent-mode-select" onchange="syncAgentModeRadios()">
                  <option value="AUTO_SAFE" selected>安全自动（默认）</option>
                  <option value="FULL_AUTO">全自动</option>
                  <option value="ANALYZE_ONLY">只分析</option>
                </select>
              </div>
              <div class="agent-field agent-wide-field">
                <label for="agent-model">AI 模型</label>
                <select id="agent-model">
                  <option value="">自动（服务端默认）</option>
                </select>
              </div>
              <div class="agent-field agent-wide-field">
                <label for="agent-runner-device">执行机器 / 设备</label>
                <select id="agent-runner-device" onchange="updateAgentRunnerDeviceHint()">
                  <option value="__AUTO_DEVICE__">自动选择在线设备（推荐）</option>
                </select>
                <div class="form-hint agent-device-hint" id="agent-runner-device-hint">正在读取在线 Runner 和设备...</div>
              </div>
            </div>
            <div class="agent-preflight-strip">
              <div>
                <strong>App 安装/更新（可选）</strong>
                <span>默认不安装；验证测试包、蒲公英包或线上回归包时再创建 Runner 安装任务。</span>
              </div>
              <button class="btn-sm" type="button" onclick="openAgentAppInstall()">安装/更新 App</button>
            </div>
          </section>

          <section class="agent-source-materials">
            <div class="agent-source-material-head">
              <div>
                <h3>本次 Agent输入资料</h3>
                <p>Figma、需求说明、需求文档和截图会一起进入Agent 的资料整理步骤。</p>
              </div>
              <span class="agent-source-counter" id="agent-source-counter">${escapeHtml(agentSourceFileSummary())}</span>
            </div>
            <div class="agent-source-grid">
              <div class="agent-field">
                <label for="agent-source-figma-url">Figma / UI 设计稿链接</label>
                <textarea id="agent-source-figma-url" class="agent-url-input" rows="2" placeholder="可选：优先粘贴具体 Frame 链接；文件链接会按目标筛选相关页面"></textarea>
              </div>
              <div class="agent-field">
                <label for="agent-source-requirement-text">需求补充说明</label>
                <textarea id="agent-source-requirement-text" rows="3" placeholder="可选：补充业务规则、风险点、待确认事项、截图说明或验收口径。"></textarea>
              </div>
            </div>
            <div class="upload-zone agent-source-upload-zone"
                 onclick="document.getElementById('agent-source-file-input').click()"
                 ondragover="handleAgentSourceDragOver(event)"
                 ondragleave="handleAgentSourceDragLeave(event)"
                 ondrop="handleAgentSourceDrop(event)"
                 tabindex="0">
              <div class="agent-source-upload-copy">
                <strong>添加需求资料</strong>
                <span>支持 PDF / Word / Markdown / YAML / 截图；也可以拖拽或粘贴截图、文本。</span>
              </div>
              <button class="btn-sm primary" type="button" onclick="event.stopPropagation();document.getElementById('agent-source-file-input').click()">添加资料</button>
              <input type="file" id="agent-source-file-input" accept=".txt,.md,.json,.pdf,.doc,.docx,.mm,.yaml,.yml,.png,.jpg,.jpeg" multiple style="display:none" onchange="handleAgentSourceFiles(this)">
            </div>
            <div class="agent-source-file-list asset-list" id="agent-source-file-list"></div>
          </section>
          <div class="agent-field agent-failed-job-field" id="agent-failed-job-field" style="display:none;">
            <label for="agent-failed-job">最近失败任务</label>
            <select id="agent-failed-job">
              <option value="">自动选择最近失败任务</option>
            </select>
          </div>
        </div>
        <!-- 隐藏的策略复选框，保持原 payload 兼容性 -->
        <div style="display:none;">
          <input type="radio" name="agent-mode" value="AUTO_SAFE" checked>
          <input type="radio" name="agent-mode" value="FULL_AUTO">
          <input type="radio" name="agent-mode" value="ANALYZE_ONLY">
          <input type="checkbox" id="agent-policy-runSonic" checked>
          <input type="checkbox" id="agent-policy-autoRepair" checked>
          <input type="checkbox" id="agent-policy-bugDraft">
          <input type="checkbox" id="agent-policy-generateCase" checked>
          <input type="checkbox" id="agent-policy-validateYaml" checked>
          <input type="checkbox" id="agent-policy-safeRerun" checked>
        </div>
        <div class="agent-actions">
          <div class="agent-action-buttons">
            <button class="btn-sm primary agent-start-button" id="agent-start-btn" onclick="startAutoAgentRun()" ${agentBusy ? 'disabled' : ''}>启动 Agent</button>
            <button class="btn-sm" onclick="previewAgentPlan()" ${agentBusy ? 'disabled' : ''}>预览计划</button>
            <button class="btn-sm" onclick="activateWorkflow('agent_history')">查看历史</button>
          </div>
          <span class="generate-hint agent-risk-level-chip" id="agent-risk-level">当前风险：${agentRiskText(riskLevel)}</span>
        </div>
      </div>

      ${run ? `
      <div class="agent-card">
        <div class="agent-timeline-head">
          <div>
            <h3>Agent 执行阶段</h3>
            <p>先整理资料，再由 AI 形成业务计划；生成、执行和失败恢复按实际结果动态推进。</p>
          </div>
          <div class="agent-timeline-legend">
            <span class="legend-dot pending"></span>等待
            <span class="legend-dot running"></span>执行中
            <span class="legend-dot success"></span>成功
            <span class="legend-dot failed"></span>失败
            <span class="legend-dot partial"></span>部分失败
            <span class="legend-dot waiting"></span>待确认
            <span class="legend-dot skipped"></span>跳过
          </div>
        </div>
        <div id="agent-progress">${renderAgentTimeline(run)}</div>
      </div>
      <div class="agent-card agent-artifacts-card" id="agent-artifacts-card">
        ${renderAgentArtifactPanel(run)}
      </div>
      ` : `
      <div class="agent-card agent-empty-run-card">
        <div class="agent-timeline-head">
          <div>
            <h3>还没有选择运行记录</h3>
            <p>这里默认用于创建新的 Agent 任务。需要查看上次执行过程时，请进入“运行记录”选择对应 Run；启动新任务后，本次步骤时间线会显示在这里。</p>
          </div>
          <button class="btn-sm" onclick="activateWorkflow('agent_history')">查看运行记录</button>
        </div>
      </div>
      `}
    </div>
  `;
  // Populate failed jobs dropdown if scope is failed_rerun
  try {
    const stats = dashboardStats();
    const sel = document.getElementById('agent-failed-job');
    if (sel && stats.failedJobs.length) {
      stats.failedJobs.slice(0, 12).forEach(job => {
        const opt = document.createElement('option');
        opt.value = job.job_id || '';
        opt.textContent = job.file || job.task_name || job.job_id || '失败任务';
        sel.appendChild(opt);
      });
    }
  } catch(e) {}

  // Restore preserved form values after innerHTML replacement
  if (Object.keys(savedFormState).length) {
    _AGENT_FORM_FIELD_IDS.forEach(id => {
      const el = document.getElementById(id);
      const saved = savedFormState[id];
      if (el && saved) {
        el.value = saved.value;
        // Restore cursor position for textarea
        if (id === 'agent-goal' && typeof saved.selectionStart === 'number') {
          try { el.selectionStart = saved.selectionStart; el.selectionEnd = saved.selectionEnd; } catch(e) {}
        }
      }
    });
    // Restore focus to the previously active form field
    if (savedActiveId && _AGENT_FORM_FIELD_IDS.includes(savedActiveId)) {
      const el = document.getElementById(savedActiveId);
      if (el) el.focus();
    }
    // Restore failed-job field visibility
    toggleFailedJobField();
  }

  updateAgentRiskHint();
  if (savedFormState['agent-source-type']) {
    const sourceSelect = document.getElementById('agent-source-type');
    if (sourceSelect) sourceSelect.value = savedFormState['agent-source-type'].value;
  }
  renderAgentSourcePanel();
  _AGENT_FORM_FIELD_IDS.forEach(id => {
    const el = document.getElementById(id);
    const saved = savedFormState[id];
    if (el && saved) el.value = saved.value;
  });
  renderAgentSourceFileList();
  renderAgentCenter();
  updateToolbarState();
  loadAppList(savedFormState['agent-app-name']?.value);
  renderAgentRunnerDeviceOptions(savedFormState['agent-runner-device']?.value);
  if (!AppState.loaded.runners) {
    loadRunnerDevices({force: true, quiet: true}).then(() => {
      renderAgentRunnerDeviceOptions(savedFormState['agent-runner-device']?.value);
    }).catch(() => {
      renderAgentRunnerDeviceOptions(savedFormState['agent-runner-device']?.value);
    });
  }
  if (shouldHydrateRuns) {
    ensureAgentRunsLoaded({ limit: 10 }).then(() => {
      if (activeWorkflow !== 'agent' && activeWorkflow !== 'dashboard') return;
      if (typeof renderAgentCenter === 'function') renderAgentCenter();
      if (document.getElementById('agent-progress') && typeof updateAgentWorkbenchDynamic === 'function') {
        updateAgentWorkbenchDynamic();
      }
    }).catch(e => {
      console.warn('Agent 运行记录后台刷新失败', e);
    });
  }
  await loadAgentModelOptions(savedFormState['agent-model']?.value);
}

// Load available apps from /api/apps and populate select elements
async function loadAppList(preferredValue) {
  try {
    const data = await apiRequest('/apps');
    const apps = data.apps || [];

    // Populate workbench select
    const workbenchSelect = document.getElementById('agent-app-name');
    if (workbenchSelect) {
      appendAgentAppOptions(workbenchSelect, apps, preferredValue);
      if (typeof updateAgentRunnerDeviceHint === 'function') updateAgentRunnerDeviceHint();
    }

    // Populate dashboard select
    const dashboardSelect = document.getElementById('dashboard-agent-app');
    if (dashboardSelect) {
      appendAgentAppOptions(dashboardSelect, apps, preferredValue);
    }
  } catch (e) {
    console.error('加载应用列表失败', e);
  }
}

// Update only the dynamic parts of the agent workbench (timeline, artifacts, center panel)
// without replacing the form, so user input in textarea/inputs is not lost during polling.
function captureAgentArtifactViewState(card) {
  if (!card) return null;
  const layout = card.querySelector('.agent-artifact-layout');
  const box = card.querySelector('#agent-artifact-box');
  const nav = card.querySelector('.agent-artifact-nav');
  if (!layout || !box) return null;
  const detailStates = Array.from(box.querySelectorAll('details')).map((detail, index) => ({
    key: detail.dataset.agentDetailKey || `detail-${index}`,
    open: detail.open,
  }));
  return {
    runId: layout.dataset.agentRunId || '',
    tab: layout.dataset.agentTab || '',
    boxScrollTop: box.scrollTop,
    boxScrollLeft: box.scrollLeft,
    navScrollLeft: nav ? nav.scrollLeft : 0,
    detailStates,
  };
}

function restoreAgentArtifactViewState(card, run, state) {
  if (!card || !run || !state) return;
  const runId = String(run.runId || '');
  if (state.runId !== runId || state.tab !== agentActiveTab) return;
  const box = card.querySelector('#agent-artifact-box');
  const nav = card.querySelector('.agent-artifact-nav');
  if (!box) return;
  const detailStateByKey = new Map((state.detailStates || []).map(item => [item.key, item.open]));
  Array.from(box.querySelectorAll('details')).forEach((detail, index) => {
    const key = detail.dataset.agentDetailKey || `detail-${index}`;
    if (detailStateByKey.has(key)) detail.open = detailStateByKey.get(key);
  });
  box.scrollTop = Math.min(state.boxScrollTop || 0, Math.max(0, box.scrollHeight - box.clientHeight));
  box.scrollLeft = state.boxScrollLeft || 0;
  if (nav) nav.scrollLeft = state.navScrollLeft || 0;
}

function updateAgentWorkbenchDynamic() {
  const run = currentAgentRun();
  const progressEl = document.getElementById('agent-progress');
  const artifactsCard = document.getElementById('agent-artifacts-card');
  const riskLevelEl = document.getElementById('agent-risk-level');
  const artifactViewState = captureAgentArtifactViewState(artifactsCard);

  if (progressEl) {
    progressEl.innerHTML = run ? renderAgentTimeline(run) : '';
  } else {
    // agent-progress 不存在时，完整重渲染确保时间线可见
    showAgentWorkbench();
    return;
  }
  if (artifactsCard && run) {
    artifactsCard.innerHTML = renderAgentArtifactPanel(run);
    restoreAgentArtifactViewState(artifactsCard, run, artifactViewState);
  }
  if (riskLevelEl) {
    const goal = document.getElementById('agent-goal')?.value || '';
    riskLevelEl.textContent = `当前风险：${agentRiskText(classifyRiskLevel(goal))}`;
  }
  renderAgentCenter();
}

// 同步下拉模式到隐藏的 radio 控件，保持原 payload 兼容
function syncAgentModeRadios() {
  const select = document.getElementById('agent-mode-select');
  if (!select) return;
  const value = select.value || 'AUTO_SAFE';
  document.querySelectorAll('input[name="agent-mode"]').forEach(radio => {
    radio.checked = radio.value === value;
  });
}

function toggleStepExpanded(el, idx) {
  el.classList.toggle('step-expanded');
  if (el.classList.contains('step-expanded')) {
    expandedStepIndexes.add(idx);
  } else {
    expandedStepIndexes.delete(idx);
  }
}

// ===== Round 5: Agent 时间线（renderAgentTimeline） =====
const AGENT_TIMELINE_STEPS = [
  ['PREPARE_SOURCE', '整理输入来源'],
  ['PLAN', 'AI 理解与计划'],
  ['IMPACT_ANALYSIS', '影响分析'],
  ['CASE_RETRIEVAL', '检索用例'],
  ['MATCH_CASES', '匹配用例'],
  ['GENERATE_YAML', '生成 YAML'],
  ['VALIDATE_YAML', '校验 YAML'],
  ['RISK_REVIEW', '风险判断'],
  ['EXECUTION_PRECHECK', '执行前体检'],
  ['SYNC_SONIC', '同步至 Sonic 平台'],
  ['RUN_SONIC', '执行任务'],
  ['COLLECT_REPORT', '收集报告'],
  ['ANALYZE_FAILURE', '分析失败'],
  ['DIAGNOSE_FAILURE', '失败诊断'],
  ['GENERATE_REPAIR', '生成修复草稿'],
  ['GENERATE_BUG_DRAFT', '生成缺陷草稿'],
  ['RERUN', '安全重跑'],
  ['LEARN_FROM_RESULT', '沉淀学习'],
  ['GENERATE_SUMMARY', '生成总结'],
  ['DONE', '完成']
];

const AGENT_EXECUTION_PHASES = [
  {
    key: 'SOURCE',
    label: '资料准备',
    description: '需求、Figma、截图与来源证据',
    steps: ['PREPARE_SOURCE']
  },
  {
    key: 'PLAN',
    label: 'AI 计划',
    description: '理解需求、重排可信基线、决定业务分支',
    steps: ['PLAN', 'IMPACT_ANALYSIS', 'CASE_RETRIEVAL', 'MATCH_CASES']
  },
  {
    key: 'BUILD',
    label: '生成与门禁',
    description: '生成 YAML，完成覆盖、静态与风险检查',
    steps: ['GENERATE_YAML', 'VALIDATE_YAML', 'RISK_REVIEW']
  },
  {
    key: 'EXECUTE',
    label: '固定设备执行',
    description: '执行前体检、首批冒烟与 remaining 扩展',
    steps: ['EXECUTION_PRECHECK', 'SYNC_SONIC', 'RUN_SONIC', 'COLLECT_REPORT']
  },
  {
    key: 'RECOVER',
    label: '诊断与恢复',
    description: '仅在失败时归因、参考证据修复并安全重跑',
    conditional: true,
    steps: ['ANALYZE_FAILURE', 'DIAGNOSE_FAILURE', 'GENERATE_REPAIR', 'GENERATE_BUG_DRAFT', 'RERUN']
  },
  {
    key: 'LEARN',
    label: '总结沉淀',
    description: '保存结果、可复用经验与最终总结',
    steps: ['LEARN_FROM_RESULT', 'GENERATE_SUMMARY']
  }
];

const TIMELINE_STATUS_META = {
  pending:  { icon: '○', label: '等待中' },
  running:  { icon: '◐', label: '执行中' },
  success:  { icon: '✓', label: '成功' },
  failed:   { icon: '✕', label: '失败' },
  partial:  { icon: '◐', label: '部分失败' },
  waiting:  { icon: '!', label: '待确认' },
  skipped:  { icon: '—', label: '已跳过' }
};

function normalizeTimelineStatus(value) {
  const v = String(value || '').toUpperCase();
  if (v === 'SUCCESS' || v === 'DONE' || v === 'FINISH' || v === 'PASSED') return 'success';
  if (v === 'FAILED' || v === 'ERROR' || v === 'CANCELLED') return 'failed';
  if (v === 'PARTIAL_FAILED' || v === 'PARTIAL') return 'partial';
  if (v === 'RUNNING' || v === 'IN_PROGRESS') return 'running';
  if (v === 'WAIT_CONFIRM' || v === 'WAITING' || v.startsWith('WAIT_')) return 'waiting';
  if (v === 'SKIPPED') return 'skipped';
  return 'pending';
}

function resolvedTimelineStatus(stepKey, run, data = timelineStepData(stepKey, run) || {}) {
  const runStatus = String(run?.status || '').toUpperCase();
  const currentRaw = String(run?.currentStep || '').toUpperCase();
  const runDone = ['DONE', 'FINISH'].includes(runStatus);
  const runTerminal = runDone || ['FAILED', 'CANCELLED'].includes(runStatus);
  let status = normalizeTimelineStatus(data.status || data.state);
  if (stepKey === 'DONE' && runTerminal) {
    return runDone ? 'success' : (runStatus === 'CANCELLED' ? 'skipped' : 'failed');
  }
  if (status === 'pending') {
    const isCurrent = stepKey === currentRaw || (stepKey === 'RUN_SONIC' && currentRaw === 'RUN_TASK');
    if (isCurrent) status = runDone && stepKey === 'DONE' ? 'success' : 'running';
  }
  if (data.success === true && status === 'pending') status = 'success';
  if (data.success === false && status === 'pending') status = 'failed';
  if (String(data.status || data.state || '').toUpperCase() === 'NOT_IMPLEMENTED') status = 'skipped';
  return status;
}

function agentWaitPhaseKey(run) {
  if (!/WAIT_CONFIRM/.test(String(run?.status || '').toUpperCase())) return '';
  const confirmations = run?.pendingConfirmations || run?.confirmations || [];
  const type = String(confirmations.find(item => !item?.decision)?.type || '').toLowerCase();
  return /(failure|repair|bug)/.test(type) ? 'RECOVER' : 'EXECUTE';
}

function agentRecoveryPhaseUsed(run) {
  const phase = AGENT_EXECUTION_PHASES.find(item => item.key === 'RECOVER');
  const current = String(run?.currentStep || '').toUpperCase();
  if (phase.steps.includes(current) || agentWaitPhaseKey(run) === 'RECOVER') return true;
  return phase.steps.some(stepKey => {
    const raw = String((timelineStepData(stepKey, run) || {}).status || '').toUpperCase();
    return ['RUNNING', 'SUCCESS', 'FAILED', 'PARTIAL_FAILED', 'WAIT_CONFIRM'].includes(raw);
  });
}

function agentPhaseStatus(phase, run) {
  if (agentWaitPhaseKey(run) === phase.key) return 'waiting';
  const statuses = phase.steps.map(stepKey => resolvedTimelineStatus(stepKey, run));
  if (statuses.includes('running')) return 'running';
  if (statuses.includes('waiting')) return 'waiting';
  if (statuses.includes('failed')) return 'failed';
  if (statuses.includes('partial')) return 'partial';
  const settled = statuses.filter(status => status !== 'pending');
  if (!settled.length) return 'pending';
  if (settled.every(status => status === 'skipped')) return 'skipped';
  if (statuses.every(status => ['success', 'skipped'].includes(status))) return 'success';
  return 'running';
}

function agentPhaseSummary(phase, run, status) {
  if (status === 'waiting') {
    const confirmations = run?.pendingConfirmations || run?.confirmations || [];
    return String(confirmations.find(item => !item?.decision)?.message || '等待风险或失败处理确认').slice(0, 100);
  }
  const active = phase.steps
    .map(stepKey => [stepKey, timelineStepData(stepKey, run) || {}, resolvedTimelineStatus(stepKey, run)])
    .find(([, , stepStatus]) => stepStatus === 'running');
  if (active) return `当前：${agentStepLabel(active[0])}`;
  if (status === 'failed') {
    const failed = phase.steps
      .map(stepKey => [stepKey, timelineStepData(stepKey, run) || {}, resolvedTimelineStatus(stepKey, run)])
      .reverse()
      .find(([, , stepStatus]) => stepStatus === 'failed');
    if (failed) return `失败：${agentStepLabel(failed[0])}`;
  }
  if (status === 'skipped' && phase.conditional) return '本次未进入失败恢复';
  if (status === 'success') return '已完成';
  return phase.description;
}

function renderAgentPhaseOverview(run) {
  const phases = AGENT_EXECUTION_PHASES.filter(phase => !phase.conditional || agentRecoveryPhaseUsed(run));
  return `<ol class="agent-phase-list" aria-label="Agent 执行阶段">
    ${phases.map((phase, index) => {
      const status = agentPhaseStatus(phase, run);
      const meta = TIMELINE_STATUS_META[status] || TIMELINE_STATUS_META.pending;
      return `<li class="agent-phase-step status-${status}">
        <div class="agent-phase-title-row">
          <span class="agent-phase-order">${String(index + 1).padStart(2, '0')}</span>
          <strong>${escapeHtml(phase.label)}</strong>
          <span class="agent-phase-state">${escapeHtml(meta.label)}</span>
        </div>
        <p>${escapeHtml(agentPhaseSummary(phase, run, status))}</p>
      </li>`;
    }).join('')}
  </ol>`;
}

function timelineStepData(stepKey, run) {
  const steps = (run && run.steps) || [];
  const direct = steps.find(s => (s.step || s.state) === stepKey);
  if (direct) return direct;
  // 兼容旧别名
  if (stepKey === 'RUN_SONIC') return steps.find(s => (s.step || s.state) === 'RUN_TASK') || null;
  if (stepKey === 'WAIT_CONFIRM') return steps.find(s => /WAIT_CONFIRM/.test(s.step || s.state || '')) || null;
  return null;
}

function fmtDuration(ms) {
  const num = Number(ms);
  if (!isFinite(num) || num <= 0) return '';
  if (num < 1000) return `${Math.round(num)}ms`;
  if (num < 60000) return `${(num / 1000).toFixed(1)}s`;
  const m = Math.floor(num / 60000);
  const s = Math.round((num % 60000) / 1000);
  return `${m}m${s}s`;
}

function timelineDurationText(step) {
  if (!step) return '';
  if (step.durationMs) return fmtDuration(step.durationMs);
  if (step.startedAt && step.endedAt) {
    try {
      const ms = Date.parse(String(step.endedAt).replace(' ', 'T'))
        - Date.parse(String(step.startedAt).replace(' ', 'T'));
      return fmtDuration(ms);
    } catch (e) { return ''; }
  }
  return '';
}

function timelineArtifactLinks(step) {
  const refs = (step && step.artifactRefs) || step?.artifacts || [];
  if (!Array.isArray(refs) || !refs.length) return '';
  return refs.slice(0, 4).map(ref => {
    if (typeof ref === 'string') {
      return `<button class="timeline-artifact-link" type="button" onclick="agentActiveTab=${jsArg(ref)};showAgentWorkbench();">${escapeHtml(ref)}</button>`;
    }
    const key = ref.tab || ref.key || ref.type || '';
    const label = ref.label || ref.name || key || '产物';
    if (ref.url) {
      return `<a class="timeline-artifact-link" href="${escapeHtml(ref.url)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`;
    }
    if (key) {
      return `<button class="timeline-artifact-link" type="button" onclick="agentActiveTab=${jsArg(key)};showAgentWorkbench();">${escapeHtml(label)}</button>`;
    }
    return `<span class="timeline-artifact-link">${escapeHtml(label)}</span>`;
  }).join('');
}

function timelineToolCallChips(step) {
  const calls = (step && step.toolCalls) || [];
  if (!Array.isArray(calls) || !calls.length) return '';
  return `<div class="timeline-tool-chips">${calls.slice(0, 5).map(call => {
    const name = typeof call === 'string' ? call : (call.toolName || call.tool || call.name || 'tool');
    return `<span class="timeline-chip">${escapeHtml(agentToolNameText(name))}</span>`;
  }).join('')}</div>`;
}

function timelineToolCallsDetail(step) {
  const calls = (step && step.toolCalls) || [];
  if (!Array.isArray(calls) || !calls.length) return '';
  const items = calls.map(call => {
    if (typeof call === 'string') {
      return `<div class="tool-call-item"><span class="tool-call-name">${escapeHtml(agentToolNameText(call))}</span></div>`;
    }
    const toolName = call.toolName || call.tool || call.name || 'tool';
    const callStatus = call.status || '';
    const duration = call.durationMs ? `${call.durationMs}ms` : '';
    const outputSummary = call.outputSummary || '';
    const artifactRefs = Array.isArray(call.artifactRefs) && call.artifactRefs.length
      ? call.artifactRefs.join(', ') : '';
    const error = call.error || '';
    const diagnosisHtml = renderDiagnosisDetail(call.diagnosis);
    return `
      <div class="tool-call-item tool-call-${escapeHtml(callStatus)}">
        <span class="tool-call-name">${escapeHtml(agentToolNameText(toolName))}</span>
        ${callStatus ? `<span class="tool-call-status">${escapeHtml(agentToolStatusText(callStatus))}</span>` : ''}
        ${duration ? `<span class="tool-call-duration">${escapeHtml(duration)}</span>` : ''}
        ${outputSummary ? `<div class="tool-call-summary">${escapeHtml(String(outputSummary).slice(0, 300))}</div>` : ''}
        ${artifactRefs ? `<div class="tool-call-artifacts">产物: ${escapeHtml(artifactRefs)}</div>` : ''}
        ${error ? `<div class="tool-call-error">错误: ${escapeHtml(String(error).slice(0, 200))}</div>` : ''}
        ${diagnosisHtml}
      </div>
    `;
  }).join('');
  return `<div class="step-tool-calls">${items}</div>`;
}

function timelineLiveTraceDetail(step) {
  const rows = (step && (step.liveTrace || step.trace)) || [];
  if (!Array.isArray(rows) || !rows.length) return '';
  return `
    <div class="step-live-trace">
      <div class="failure-title">实时轨迹</div>
      ${rows.slice(-12).map(row => `
        <div class="step-live-trace-row status-${escapeHtml(String(row.status || '').toLowerCase())}">
          <span>${escapeHtml(formatDisplayTime(row.time))}</span>
          <strong>${escapeHtml(agentTraceMessageText(row.message || ''))}</strong>
        </div>
      `).join('')}
    </div>
  `;
}

function agentTraceMessageText(value) {
  const text = String(value || '');
  return text
    .replace(/准备调用工具[：:]\s*_tool_rerun/gi, '正在准备失败任务重跑')
    .replace(/调用工具[：:]\s*_tool_rerun/gi, '已启动失败任务重跑')
    .replace(/_tool_rerun/gi, '失败任务重跑');
}

function renderDiagnosisDetail(diagnosis) {
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

// ===== Step 状态→CSS类映射 =====
function stepStatusClass(status) {
  switch ((status || '').toUpperCase()) {
    case 'SUCCESS': return 'step-success';
    case 'FAILED': return 'step-failed';
    case 'PARTIAL_FAILED': case 'PARTIAL': return 'step-partial';
    case 'SKIPPED': return 'step-skipped';
    case 'RUNNING': case 'IN_PROGRESS': return 'step-running';
    default: return 'step-pending';
  }
}

// ===== 同步至 Sonic 平台失败详情 =====
function renderSonicSyncDetail(step, artifacts) {
  const sync = (artifacts || {}).sonicSync || {};
  const failed = sync.failed || [];
  let html = `<div class="sync-summary">同步 ${sync.syncedCount || 0}/${sync.total || 0} 成功`;
  if (sync.failedCount > 0) {
    html += `，<span class="text-danger">${sync.failedCount} 失败</span>`;
  }
  html += '</div>';
  if (failed.length > 0) {
    html += '<div class="sync-failures">';
    html += '<div class="failure-title">失败详情：</div>';
    html += '<ul class="failure-list">';
    for (const f of failed) {
      html += `<li class="failure-item">
        <span class="failure-file">${escapeHtml(f.module || '')}/${escapeHtml(f.file || '')}</span>
        <span class="failure-error">${escapeHtml(f.error || '未知错误')}</span>
      </li>`;
    }
    html += '</ul></div>';
  }
  const diag = step.diagnosis || (step.toolCalls && step.toolCalls[0] && step.toolCalls[0].diagnosis);
  html += renderDiagnosisDetail(diag);
  return html;
}

// ===== RUN_TASK 执行进度详情 =====
function agentRunnerProgressMetrics(progress = {}) {
  const jobs = Array.isArray(progress.jobs) ? progress.jobs : [];
  const jobStatuses = jobs.map(job => String(job?.status || '').toLowerCase());
  const timeoutFromJobs = jobs.filter(job => (
    job && (job.agent_wait_timeout || String(job.status || '').toLowerCase() === 'timeout')
  )).length;
  const completed = Number(progress.completed ?? progress.successCount ?? progress.completedCount ?? jobs.filter(job => String(job.status || '').toLowerCase() === 'success').length ?? 0);
  const failed = Number(progress.failed ?? progress.failedCount ?? jobs.filter(job => ['failed', 'error', 'cancelled'].includes(String(job.status || '').toLowerCase())).length ?? 0);
  const timeout = Number(progress.timeoutCount ?? timeoutFromJobs ?? 0);
  const runningFromJobs = jobStatuses.filter(value => ['running', 'assigned'].includes(value)).length;
  const pendingFromJobs = jobStatuses.filter(value => ['pending', 'queued', 'waiting', 'created', 'creating'].includes(value)).length;
  const runningRaw = Number(progress.executing ?? (jobs.length ? runningFromJobs : (progress.running ?? progress.runningCount)) ?? 0);
  const pending = Number((jobs.length ? pendingFromJobs : (progress.pending ?? progress.pendingCount ?? progress.queuedCount ?? progress.queued)) ?? 0);
  const timeoutSeconds = Number(progress.timeoutSeconds ?? (!progress.agentWaitTimeout ? progress.timeout : 0) ?? 0);
  return {
    total: Number(progress.total ?? jobs.length ?? 0),
    completed,
    failed,
    timeout,
    running: Math.max(0, jobs.length ? runningRaw : runningRaw - timeout),
    pending: Math.max(0, pending),
    timeoutSeconds,
  };
}

function renderRunTaskDetail(step, artifacts) {
  const progress = (artifacts || {}).jobProgress || {};
  const progressMetrics = agentRunnerProgressMetrics(progress);
  const phaseRows = Object.entries((artifacts || {}).jobProgressByPhase || {}).map(([key, value]) => {
    const phase = value && typeof value === 'object' ? value : {};
    const label = phase.phase || key;
    return {
      label,
      dryRun: /dry[-_\s]?run/i.test(String(label)),
      metrics: agentRunnerProgressMetrics(phase),
    };
  });
  const actualPhaseRows = phaseRows.filter(item => !item.dryRun);
  const cumulative = actualPhaseRows.reduce((sum, item) => ({
    total: sum.total + item.metrics.total,
    completed: sum.completed + item.metrics.completed,
    failed: sum.failed + item.metrics.failed,
    timeout: sum.timeout + item.metrics.timeout,
    running: sum.running + item.metrics.running,
    pending: sum.pending + item.metrics.pending,
  }), {total: 0, completed: 0, failed: 0, timeout: 0, running: 0, pending: 0});
  const result = (artifacts || {}).jobResult || {};
  const runnerDryRun = (artifacts || {}).runnerDryRun || {};
  let html = '<div class="job-progress">';
  if (runnerDryRun && (runnerDryRun.checked != null || runnerDryRun.createdCount != null)) {
    html += agentInfoGrid([
      { label: '执行前 dry-run', value: runnerDryRun.mode || 'mock_dry_run' },
      { label: '检查 YAML', value: runnerDryRun.checked ?? 0 },
      { label: '拦截', value: runnerDryRun.blockedCount ?? 0 },
      { label: '真实 dry-run Job', value: (runnerDryRun.runnerJobIds || []).length || '-' },
    ]);
    if (runnerDryRun.reason) {
      html += `<section class="agent-readable-panel"><strong>dry-run 说明</strong><p>${escapeHtml(runnerDryRun.reason)}</p></section>`;
    }
    if (Array.isArray(runnerDryRun.blocked) && runnerDryRun.blocked.length) {
      html += agentReadableList('dry-run 拦截明细', runnerDryRun.blocked.slice(0, 12), item => (
        `<b>${escapeHtml(item.file || item.path || '')}</b><span>${escapeHtml(item.reason || '')}${item.job_id ? ' · Job ' + escapeHtml(item.job_id) : ''}${item.errors ? ' · ' + escapeHtml((item.errors || []).join('；')) : ''}</span>`
      ));
    }
  }
  if (actualPhaseRows.length) {
    html += `<section class="agent-readable-panel"><strong>Runner 真实执行累计</strong>${agentInfoGrid([
      {label: '执行尝试', value: cumulative.total},
      {label: '成功', value: cumulative.completed},
      {label: '失败', value: cumulative.failed},
      {label: '超时', value: cumulative.timeout},
      {label: '执行中', value: cumulative.running},
      {label: '排队中', value: cumulative.pending},
    ])}${agentReadableList('执行阶段', actualPhaseRows, item => `<b>${escapeHtml(item.label)}</b><span>成功 ${escapeHtml(item.metrics.completed)} · 失败 ${escapeHtml(item.metrics.failed)} · 超时 ${escapeHtml(item.metrics.timeout)} · 执行中 ${escapeHtml(item.metrics.running)} · 排队中 ${escapeHtml(item.metrics.pending)}</span>`)}</section>`;
  }
  if (progressMetrics.total > 0) {
    const timeoutText = progressMetrics.timeoutSeconds
      ? ` / 上限 ${escapeHtml(String(progressMetrics.timeoutSeconds))}s`
      : '';
    html += `<div class="job-progress-summary">
      <span class="timeline-chip">当前阶段 ${escapeHtml(String(progressMetrics.total))} 个任务</span>
      <span class="timeline-chip text-success">✓ ${escapeHtml(String(progressMetrics.completed))} 成功</span>
      <span class="timeline-chip text-danger">✗ ${escapeHtml(String(progressMetrics.failed))} 失败</span>
      <span class="timeline-chip text-info">⟳ ${escapeHtml(String(progressMetrics.running))} 执行中</span>
      <span class="timeline-chip">${escapeHtml(String(progressMetrics.pending))} 排队中</span>
      ${progressMetrics.timeout ? `<span class="timeline-chip text-warning">! ${escapeHtml(String(progressMetrics.timeout))} 超时</span>` : ''}
      ${progress.elapsed != null ? `<span class="timeline-chip">已等待 ${escapeHtml(String(progress.elapsed))}s${timeoutText}</span>` : ''}
    </div>`;
    const progressJobs = Array.isArray(progress.jobs) ? progress.jobs : [];
    if (progressJobs.length) {
      html += '<div class="job-progress-grid">';
      for (const job of progressJobs.slice(0, 12)) {
        const status = String(job.status || '').toLowerCase();
        const taskName = job.target_task_name || job.current_task_name || job.file || job.job_id || '';
        const jobLabel = `${job.module || ''}/${job.file || ''}`.replace(/^\/+/, '');
        const reportUrl = job.report_url || job.reportUrl || '';
        html += `<div class="job-progress-row status-${escapeHtml(status)}">
          <span class="job-progress-status">${escapeHtml(agentJobStatusText(status))}</span>
          <span class="job-progress-title">${escapeHtml(taskName)}</span>
          <span class="job-progress-file">${escapeHtml(jobLabel)}</span>
          ${reportUrl ? `<a class="job-progress-link" href="${escapeHtml(reportUrl)}" target="_blank">报告</a>` : ''}
          ${job.error ? `<span class="job-progress-error">${escapeHtml(String(job.error).slice(0, 120))}</span>` : ''}
        </div>`;
      }
      html += '</div>';
    }
  }
  if (result.failedCount > 0) {
    html += '<div class="failed-jobs">';
    html += `<div class="failure-title">${result.failedCount} 个任务失败：</div>`;
    html += '<ul class="failure-list">';
    for (const fj of (result.failed || []).slice(0, 10)) {
      html += `<li>${escapeHtml(fj.module || '')}/${escapeHtml(fj.file || '')} - ${escapeHtml(fj.error || fj.status || '')}</li>`;
    }
    html += '</ul></div>';
  }
  if (result.timeoutCount > 0) {
    html += `<div class="timeout-jobs text-warning">${result.timeoutCount} 个任务超时</div>`;
  }
  html += '</div>';
  return html;
}

function agentInfoGrid(items = []) {
  const visible = items.filter(item => item && item.label);
  if (!visible.length) return '';
  return `<div class="agent-info-grid">${visible.map(item => `
    <div>
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value ?? '-')}</strong>
    </div>
  `).join('')}</div>`;
}

function agentReadableList(title, items = [], renderItem) {
  const visible = items.filter(Boolean);
  if (!visible.length) return '';
  return `
    <section class="agent-readable-panel">
      <strong>${escapeHtml(title)}</strong>
      <div class="agent-readable-list">
        ${visible.map((item, index) => `<div>${renderItem ? renderItem(item, index) : escapeHtml(String(item))}</div>`).join('')}
      </div>
    </section>
  `;
}

function isAgentHtmlReport(item = {}) {
  const reportUrl = String(item.reportUrl || item.report_url || '').trim();
  const localPath = String(item.localPath || item.local_report_path || item.localReportPath || '').trim().toLowerCase();
  return Boolean(reportUrl) || localPath.endsWith('.html') || localPath.endsWith('.htm');
}

function isAgentYamlRef(item = {}) {
  const file = String(item.file || item.name || item.path || '').trim().toLowerCase();
  const url = String(item.reportUrl || item.report_url || '').trim();
  return !url && (file.endsWith('.yaml') || file.endsWith('.yml'));
}

function normalizeAgentReportArtifacts(report = {}) {
  const rawReports = Array.isArray(report.executionReports)
    ? report.executionReports
    : (Array.isArray(report.reports) ? report.reports : []);
  const executionReports = rawReports.filter(item => isAgentHtmlReport(item));
  const yamlFromReports = rawReports
    .filter(item => !isAgentHtmlReport(item) && isAgentYamlRef(item))
    .map(item => ({
      jobId: item.jobId || item.job_id || '',
      module: item.module || '',
      file: item.file || item.name || item.path || '',
      taskName: item.taskName || item.task_name || item.target_task_name || item.current_task_name || item.currentTaskName || '',
      status: item.status || '',
    }));
  const yamlExecutionRefs = [
    ...(Array.isArray(report.yamlExecutionRefs) ? report.yamlExecutionRefs : []),
    ...yamlFromReports,
  ];
  return { executionReports, yamlExecutionRefs };
}

function agentCaseLabel(item = {}) {
  const task = String(
    item.taskName || item.task_name || item.target_task_name || item.targetTaskName ||
    item.current_task_name || item.currentTaskName || ''
  ).trim();
  const file = String(item.file || item.name || item.path || '').trim();
  const jobId = String(item.jobId || item.job_id || '').trim();
  return task || file || jobId || '未命名用例';
}

function agentCaseSubLabel(item = {}) {
  return [
    item.module || '',
    item.file || item.name || item.path || '',
    item.jobId || item.job_id ? `Job ${item.jobId || item.job_id}` : '',
  ].filter(Boolean).join(' · ');
}

function agentArtifactsOf(runOrArtifacts = {}) {
  const value = runOrArtifacts || {};
  return value.artifacts && typeof value.artifacts === 'object' ? value.artifacts : value;
}

function agentMindmapInfo(runOrArtifacts = {}) {
  const artifacts = agentArtifactsOf(runOrArtifacts);
  const pipeline = artifacts.generationPipeline || {};
  const summary = artifacts.generationSummary || {};
  const summaryFiles = pipeline.summaryFiles || summary.summaryFiles || {};
  const caseSetId = String(
    pipeline.caseSetId || pipeline.case_set_id ||
    summary.caseSetId || summary.case_set_id ||
    artifacts.caseSetId || artifacts.case_set_id || ''
  ).trim();
  const path = String(
    summaryFiles.mindmap || summaryFiles.mm ||
    artifacts.mindmapPath || artifacts.mindmap_path || ''
  ).trim();
  const base = typeof API_BASE !== 'undefined' ? API_BASE : '/api';
  const url = caseSetId ? `${base}/cases/mindmap?case_set_id=${encodeURIComponent(caseSetId)}` : '';
  return { caseSetId, path, url };
}

function agentMindmapDownloadUrl(runOrArtifacts = {}) {
  return agentMindmapInfo(runOrArtifacts).url;
}

function agentYamlReferenceExamples(runOrArtifacts = {}) {
  const artifacts = agentArtifactsOf(runOrArtifacts);
  const reviews = [
    artifacts.generationPipeline?.review,
    artifacts.generationSummary?.review,
    artifacts.generatedCases?.review,
  ].filter(item => item && typeof item === 'object');
  for (const review of reviews) {
    if (Array.isArray(review.yaml_reference_examples) && review.yaml_reference_examples.length) {
      return review.yaml_reference_examples;
    }
  }
  return [];
}

function renderYamlReferenceExamples(examples = []) {
  const visible = examples.filter(Boolean);
  if (!visible.length) return '';
  return `
    <section class="final-report-panel final-report-wide yaml-reference-panel">
      <strong>生成时参考的历史步骤</strong>
      <p>Agent 会从现有 YAML 用例库学习可执行步骤写法，只复用相关动作结构，本次需求和 Figma 仍是主依据。</p>
      <div class="yaml-reference-list">
        ${visible.slice(0, 6).map(item => `
          <div>
            <b>${escapeHtml(item.title || item.file || '参考用例')}</b>
            <span>${escapeHtml(item.file || '')}</span>
            ${item.baseline_path ? `<em>${escapeHtml(item.baseline_path)}</em>` : ''}
            <small>${escapeHtml([...(item.matched_terms || []), ...(item.actions || [])].slice(0, 10).join(' · ') || '按模块和步骤结构匹配')}</small>
          </div>
        `).join('')}
      </div>
      ${visible.length > 6 ? `<p>还有 ${escapeHtml(visible.length - 6)} 条参考已记录在生成摘要中。</p>` : ''}
    </section>
  `;
}

function downloadAgentMindmap() {
  const info = agentMindmapInfo(currentAgentRun());
  if (!info.url) {
    showToast('当前 Agent 还没有可下载的脑图文件', 'warn');
    return;
  }
  window.open(info.url, '_blank', 'noopener');
}

function agentUiDesignImageUrl(caseSetId, item = {}) {
  const base = typeof API_BASE !== 'undefined' ? API_BASE : '/api';
  const assetId = item.asset_id || item.assetId || '';
  const filename = item.filename || item.screenshot || item.image_name || '';
  if (!caseSetId || (!assetId && !filename)) return '';
  return `${base}/cases/ui-design-image?case_set_id=${encodeURIComponent(caseSetId)}&asset_id=${encodeURIComponent(assetId)}&filename=${encodeURIComponent(filename)}`;
}

function agentFigmaPreviewItems(source = {}, artifacts = {}) {
  const pipeline = artifacts.generationPipeline || {};
  const summary = artifacts.generationSummary || {};
  const caseSetId = String(
    pipeline.caseSetId || pipeline.case_set_id ||
    summary.caseSetId || summary.case_set_id ||
    artifacts.caseSetId || artifacts.case_set_id || ''
  ).trim();
  const byKey = new Map();
  const addItem = (raw, origin) => {
    if (!raw || typeof raw !== 'object') return;
    const figma = raw.figma || {};
    const title = raw.page_name || raw.pageName || raw.title || raw.name || raw.filename || raw.screenshot || raw.image_name || 'Figma 图片';
    const nodeId = figma.node_id || figma.nodeId || raw.node_id || raw.nodeId || raw.page_id || raw.pageId || '';
    const filename = raw.filename || raw.screenshot || raw.image_name || raw.name || '';
    const imageUrl = agentUiDesignImageUrl(caseSetId, raw) || raw.image_url || raw.imageUrl || raw.preview_url || raw.previewUrl || raw.thumbnail_url || raw.thumbnailUrl || '';
    const score = raw.relevance_score ?? raw.score ?? figma.relevance_score ?? figma.rechecked_relevance_score ?? '';
    const reason = raw.relevance_reason || raw.reason || figma.relevance_reason || raw.description || raw.route || '';
    const key = String(raw.asset_id || raw.assetId || filename || nodeId || title);
    const item = {
      title,
      nodeId,
      filename,
      imageUrl,
      score,
      reason,
      origin,
      route: raw.route || '',
    };
    const current = byKey.get(key);
    if (!current || (!current.imageUrl && item.imageUrl)) {
      byKey.set(key, {...current, ...item});
    }
  };
  (source.uiDesignAssets || []).forEach(item => addItem(item, '已保存参考图'));
  (source.figmaUsedPages || source.uiDesigns || []).forEach(item => addItem(item, '采用页面'));
  (source.figmaImageAssets || []).forEach(item => addItem(item, '解析图片'));
  return Array.from(byKey.values());
}

function renderFigmaPreviewGrid(items = []) {
  const visible = items.filter(Boolean);
  if (!visible.length) return '';
  return `
    <section class="agent-readable-panel">
      <strong>Figma 解析图片</strong>
      <p>下面是 Agent 本次解析、保存或采用的 Figma 页面/图片，先看这里判断是否命中了正确设计稿。</p>
      <div class="agent-figma-grid">
        ${visible.slice(0, 12).map(item => {
          const meta = [item.origin, item.nodeId ? `节点 ${item.nodeId}` : '', item.score !== '' && item.score !== undefined ? `匹配 ${item.score}` : ''].filter(Boolean).join(' · ');
          const media = item.imageUrl
            ? `<a class="agent-figma-thumb" href="${escapeHtml(item.imageUrl)}" target="_blank" rel="noopener"><img src="${escapeHtml(item.imageUrl)}" alt="${escapeHtml(item.title)}" loading="lazy"></a>`
            : `<div class="agent-figma-thumb empty"><span>${escapeHtml(item.filename || '暂无图片预览')}</span></div>`;
          return `
            <div class="agent-figma-card">
              ${media}
              <div class="agent-figma-card-body">
                <b>${escapeHtml(item.title)}</b>
                <span>${escapeHtml(meta || 'Figma 参考')}</span>
                ${item.reason ? `<em>${escapeHtml(item.reason)}</em>` : ''}
              </div>
            </div>
          `;
        }).join('')}
      </div>
      ${visible.length > 12 ? `<p>已展示前 12 个，剩余 ${escapeHtml(visible.length - 12)} 个可在生成批次的 UI 设计稿中继续查看。</p>` : ''}
    </section>
  `;
}

// ===== COLLECT_REPORT 报告详情 =====
function renderReportDetail(step, artifacts) {
  const report = (artifacts || {}).report || {};
  const normalizedReport = normalizeAgentReportArtifacts(report);
  const reports = normalizedReport.executionReports;
  const yamlRefs = normalizedReport.yamlExecutionRefs;
  const jobStatuses = report.jobStatuses || [];
  const failedJobs = report.failedJobs || [];
  const mindmap = agentMindmapInfo(artifacts);
  const status = report.status || 'unknown';
  let html = '<div class="report-detail rich-report">';
  html += `
    <div class="report-summary-grid">
      <div><span>状态</span><strong>${escapeHtml(agentJobStatusText(status))}</strong></div>
      <div><span>执行报告</span><strong>${reports.length}</strong></div>
      <div><span>任务状态</span><strong>${jobStatuses.length}</strong></div>
      <div><span>失败</span><strong>${failedJobs.length}</strong></div>
    </div>
  `;
  if (reports.length > 0) {
    html += '<div class="report-links">';
    html += '<div class="section-title">执行报告</div>';
    for (const r of reports.slice(0, 10)) {
      const label = agentCaseLabel(r);
      const subLabel = agentCaseSubLabel(r);
      if (r.reportUrl) {
        html += `<a href="${escapeHtml(r.reportUrl)}" target="_blank" class="report-link report-case-link"><b>${escapeHtml(label)}</b><span>${escapeHtml(subLabel || '打开执行报告')}</span></a>`;
      } else {
        html += `<span class="report-local report-case-link"><b>${escapeHtml(label)}</b><span>${escapeHtml(subLabel || r.localPath || '-')}</span></span>`;
      }
    }
    html += '</div>';
  } else {
    html += '<div class="report-empty">当前没有 Runner 回传的 HTML 报告链接；下方仅展示已执行的 YAML/任务状态。</div>';
  }
  if (yamlRefs.length > 0) {
    html += '<div class="report-links">';
    html += '<div class="section-title">执行 YAML</div>';
    html += yamlRefs.slice(0, 10).map(item => `<span class="report-local report-case-link"><b>${escapeHtml(agentCaseLabel(item))}</b><span>${escapeHtml(agentCaseSubLabel(item))}</span></span>`).join('');
    html += '</div>';
  }
  if (mindmap.url || mindmap.path) {
    html += '<div class="report-links">';
    html += '<div class="section-title">脑图文件</div>';
    html += mindmap.url
      ? `<a href="${escapeHtml(mindmap.url)}" target="_blank" class="report-link">下载同步生成的 .mm 脑图</a>`
      : `<span class="report-local">${escapeHtml(mindmap.path)}</span>`;
    if (mindmap.caseSetId) {
      html += `<span class="report-local">生成批次：${escapeHtml(mindmap.caseSetId)}</span>`;
    }
    html += '</div>';
  }
  if (failedJobs.length > 0) {
    html += '<div class="failed-summary">';
    html += `<div class="failure-title">${failedJobs.length} 个任务失败：</div>`;
    html += '<ul class="failure-list">';
    for (const fj of failedJobs.slice(0, 5)) {
      html += `<li><strong>${escapeHtml(agentCaseLabel(fj))}</strong><span>${escapeHtml(agentCaseSubLabel(fj))}</span>：${escapeHtml(fj.error || '未知')}</li>`;
    }
    html += '</ul></div>';
  }
  html += '</div>';
  return html;
}

function renderAgentReportArtifact(run) {
  const artifacts = (run && run.artifacts) || {};
  return renderReportDetail(null, artifacts);
}

function renderVisualReferenceReport(report) {
  if (!report || typeof report !== 'object' || !Object.keys(report).length) return '';
  const notes = Array.isArray(report.usageNotes) ? report.usageNotes : [];
  const conflicts = Array.isArray(report.conflictNotes) ? report.conflictNotes : [];
  const images = Array.isArray(report.uploadedImages) ? report.uploadedImages : [];
  const sources = Array.isArray(report.referenceSources) ? report.referenceSources : [];
  return `
    <section class="agent-readable-panel">
      <strong>图片参考</strong>
      <p>${escapeHtml(report.rule || '上传截图作为辅助参考，不作为硬门禁。')}</p>
      <div class="report-summary-grid final-report-metrics">
        <div><span>上传截图</span><strong>${escapeHtml(report.uploadedImageCount ?? images.length)}</strong></div>
        <div><span>Figma 页面</span><strong>${escapeHtml(report.figmaPageCount ?? 0)}</strong></div>
        <div><span>Figma UI 图</span><strong>${escapeHtml(report.figmaImageCount ?? 0)}</strong></div>
        <div><span>硬门禁</span><strong>${report.hardGate ? '是' : '否'}</strong></div>
        <div><span>AI 判断</span><strong>${report.aiJudgementRequired ? (report.sentToAiForJudgement ? '已参与' : '待复核') : '无需'}</strong></div>
      </div>
      ${sources.length ? `<p>参考来源：${escapeHtml(sources.join(' / '))}</p>` : ''}
      ${report.visualRefineSkipped ? `<p>视觉校准：${escapeHtml(report.visualRefineSkipped)}</p>` : ''}
      ${notes.length ? `<div class="agent-quality-notes">${notes.map(item => `<div>${escapeHtml(item)}</div>`).join('')}</div>` : ''}
      ${conflicts.length ? `<div class="agent-quality-notes">${conflicts.map(item => `<div class="warn">提醒：${escapeHtml(item)}</div>`).join('')}</div>` : ''}
      ${images.length ? agentReadableList('上传截图文件', images.slice(0, 8), image => `<b>${escapeHtml(image.name || '未命名截图')}</b><span>${escapeHtml(image.type || image.kindLabel || '截图')}</span>`) : ''}
      <p>${escapeHtml(report.conflictPolicy || '')}</p>
    </section>
  `;
}

function renderAgentQualityArtifact(run) {
  const artifacts = (run && run.artifacts) || {};
  const quality = artifacts.qualityReport || {};
  if (!quality || typeof quality !== 'object' || !Object.keys(quality).length) {
    return `<div class="report-empty">暂无质量检查结果。新需求走完整生成主链后，会展示完整用例、自动化 YAML、人工用例和 Figma 解析情况。</div>`;
  }
  const status = String(quality.status || '').toLowerCase();
  const tone = status === 'blocked' ? 'danger' : (status === 'warn' ? 'warn' : 'success');
  const blockers = Array.isArray(quality.blockers) ? quality.blockers : [];
  const warnings = Array.isArray(quality.warnings) ? quality.warnings : [];
  const layers = Array.isArray(quality.layers) ? quality.layers : [];
  const coverage = quality.coverage || {};
  const artifactFiles = quality.artifacts || {};
  const mindmap = agentMindmapInfo(artifacts);
  let html = `
    <div class="agent-quality-report">
      <div class="final-report-hero ${tone}">
        <div>
          <span class="final-report-kicker">Agent 质量检查</span>
          <h3>${escapeHtml(quality.statusText || '质量检查')}</h3>
          <p>把本次生成结果按“完整用例、自动化 YAML、人工用例、Figma 图片”拆开看，方便判断覆盖是否足够。</p>
        </div>
        <strong class="final-report-conclusion ${tone}">${escapeHtml(quality.statusText || '-')}</strong>
      </div>
      <div class="report-summary-grid final-report-metrics">
        <div><span>需求点</span><strong>${escapeHtml(quality.requirementPointCount ?? 0)}</strong></div>
        <div><span>业务场景</span><strong>${escapeHtml(quality.scenarioCount ?? 0)}</strong></div>
        <div><span>完整用例</span><strong>${escapeHtml(quality.totalCaseCount ?? 0)}</strong></div>
        <div><span>YAML 文件</span><strong>${escapeHtml(quality.yamlFileCount ?? 0)}</strong></div>
      </div>
      <div class="agent-quality-layers">
        ${layers.map(layer => `
          <div class="${layer.ready ? 'ready' : 'not-ready'}">
            <span>${escapeHtml(layer.name || '-')}</span>
            <strong>${escapeHtml(layer.count ?? 0)}</strong>
            <em>${layer.ready ? '已产出' : '未产出'}</em>
          </div>
        `).join('')}
      </div>
  `;
  if (blockers.length || warnings.length) {
    html += `
      <section class="agent-readable-panel">
        <strong>需要关注</strong>
        <div class="agent-quality-notes">
          ${blockers.map(item => `<div class="danger">阻断：${escapeHtml(item)}</div>`).join('')}
          ${warnings.map(item => `<div class="warn">提醒：${escapeHtml(item)}</div>`).join('')}
        </div>
      </section>
    `;
  }
  const missingCasePoints = Array.isArray(coverage.missingCasePoints) ? coverage.missingCasePoints : [];
  const missingScenarioPoints = Array.isArray(coverage.missingScenarioPoints) ? coverage.missingScenarioPoints : [];
  const genericAssertionCases = Array.isArray(coverage.genericAssertionCases) ? coverage.genericAssertionCases : [];
  if (missingCasePoints.length || missingScenarioPoints.length || genericAssertionCases.length) {
    html += `
      <div class="final-report-layout">
        <section class="final-report-panel">
          <strong>覆盖缺口</strong>
          ${missingCasePoints.length ? `<p>未覆盖需求点：${escapeHtml(missingCasePoints.slice(0, 8).join('；'))}</p>` : '<p>需求点已进入用例或人工项。</p>'}
          ${missingScenarioPoints.length ? `<p>未映射场景：${escapeHtml(missingScenarioPoints.slice(0, 8).join('；'))}</p>` : ''}
        </section>
        <section class="final-report-panel">
          <strong>断言质量</strong>
          ${genericAssertionCases.length ? `<p>断言偏泛：${escapeHtml(genericAssertionCases.slice(0, 8).join('；'))}</p>` : '<p>未发现明显空泛断言。</p>'}
        </section>
      </div>
    `;
  }
  html += renderVisualReferenceReport(quality.visualReferenceReport || artifacts.visualReferenceReport);
  const links = [
    mindmap.url ? { label: '下载完整 .mm 脑图', url: mindmap.url } : null,
    artifactFiles.markdown ? { label: '生成摘要文件', text: artifactFiles.markdown } : null,
    artifactFiles.json ? { label: '结构化用例 JSON', text: artifactFiles.json } : null,
  ].filter(Boolean);
  if (links.length) {
    html += `
      <section class="final-report-panel final-report-wide">
        <strong>生成产物</strong>
        <div class="final-report-links">
          ${links.map(item => item.url
            ? `<a href="${escapeHtml(item.url)}" target="_blank">${escapeHtml(item.label)}</a>`
            : `<span>${escapeHtml(item.label)}：${escapeHtml(item.text || '')}</span>`
          ).join('')}
        </div>
      </section>
    `;
  }
  html += renderFigmaPreviewGrid(agentFigmaPreviewItems(artifacts.sourceContext || {}, artifacts));
  html += '</div>';
  return html;
}

function agentGeneratedCaseGroups(artifacts = {}) {
  const direct = artifacts.generatedCaseGroups || {};
  const validation = artifacts.yamlValidation || {};
  const fromValidation = validation.executionGroups || {};
  const generated = artifacts.generatedCases || {};
  const pick = key => {
    const value = direct[key] || fromValidation[key] || generated[key] || [];
    return Array.isArray(value) ? value : [];
  };
  return {
    executable_cases: pick('executable_cases'),
    needs_review_cases: pick('needs_review_cases'),
    draft_cases: pick('draft_cases'),
    manual_cases: pick('manual_cases'),
  };
}

function agentGeneratedSmokeTargets(artifacts = {}) {
  const pipeline = artifacts.generationPipeline || {};
  const summary = artifacts.generationSummary || {};
  const generated = artifacts.generatedCases || {};
  const gate = artifacts.runnerExecutionGate || artifacts.runnerSmokeGate || {};
  const reviews = [
    gate,
    pipeline.review,
    summary.review,
    generated.review,
  ];
  for (const review of reviews) {
    if (!review || typeof review !== 'object') continue;
    const coverageAudit = review.coverage_audit || review.coverageAudit || {};
    const candidates = [
      review.generation_targets,
      review.generationTargets,
      coverageAudit.generation_targets,
      coverageAudit.generationTargets,
    ];
    const found = candidates.find(item => item && typeof item === 'object');
    if (found) return found;
  }
  return {};
}

function agentGeneratedSmokeRerunLimit(artifacts = {}, totalCount = 0) {
  const total = Math.max(0, Number(totalCount) || 0);
  const gate = artifacts.runnerExecutionGate || artifacts.runnerSmokeGate || {};
  const gateLimit = Number(gate.limit || gate.smokeLimit || 0);
  const targets = agentGeneratedSmokeTargets(artifacts);
  const fallbackUpper = typeof GENERATED_SMOKE_RERUN_DEFAULT_LIMIT !== 'undefined' ? GENERATED_SMOKE_RERUN_DEFAULT_LIMIT : 3;
  const upper = Math.max(1, Math.min(
    fallbackUpper,
    Number(targets.smoke_max_cases || targets.smokeMaxCases || fallbackUpper) || fallbackUpper
  ));
  const targetLimit = Number(targets.smoke_cases || targets.smokeCases || 0);
  const base = gateLimit > 0 ? gateLimit : (targetLimit > 0 ? targetLimit : upper);
  const limit = Math.max(1, Math.min(upper, base));
  return total ? Math.max(1, Math.min(limit, total)) : limit;
}

function agentGeneratedCaseIsSmoke(item = {}) {
  if (!item || typeof item !== 'object') return false;
  if (item.smoke === true || item.is_smoke === true || item.isSmoke === true) return true;
  if (item.smokeCandidate === true || item.runnerCandidate === true) return true;
  const tokens = [];
  ['flag', 'flags', 'tags'].forEach(key => {
    const value = item[key];
    if (Array.isArray(value)) tokens.push(...value.map(String));
    else if (value !== undefined && value !== null) tokens.push(String(value));
  });
  return /冒烟|smoke/i.test(tokens.join(' '));
}

function renderGeneratedExecutionLevelSummary(artifacts = {}) {
  const groups = agentGeneratedCaseGroups(artifacts);
  const mindmap = agentMindmapInfo(artifacts);
  const smokeExecutableCount = (groups.executable_cases || []).filter(agentGeneratedCaseIsSmoke).length;
  const remainingExecutableCount = Math.max(0, (groups.executable_cases || []).length - smokeExecutableCount);
  const generatedCount = ['executable_cases', 'needs_review_cases', 'draft_cases', 'manual_cases']
    .reduce((sum, key) => sum + ((groups[key] || []).length), 0);
  const smokeLimit = agentGeneratedSmokeRerunLimit(artifacts, smokeExecutableCount);
  const remainingLimit = agentGeneratedSmokeRerunLimit(artifacts, remainingExecutableCount || (groups.executable_cases || []).length);
  const labels = [
    ['executable_cases', '可执行', '可自动进入 Runner 首批冒烟'],
    ['needs_review_cases', '需确认', '需要人工看原因后再决定是否执行'],
    ['draft_cases', '草稿', '只是测试设计或 YAML 草稿，不自动执行'],
    ['manual_cases', '人工', '需要人工检查或不适合自动化'],
  ];
  const total = labels.reduce((sum, [key]) => sum + groups[key].length, 0);
  if (!total) return '';
  const listHtml = labels.map(([key, label, desc]) => {
    const rows = groups[key] || [];
    return `
      <section class="final-report-panel">
        <strong>${label} ${rows.length}</strong>
        <p>${desc}</p>
        ${rows.length ? `<div class="final-report-file-list compact">${rows.slice(0, 6).map(item => {
          const name = item.name || item.file || item.title || item.case_name || '未命名用例';
          const score = item.score || item.executableScore?.score || 0;
          const reason = Array.isArray(item.reasons) ? item.reasons[0] : (item.reason || '');
          const mod = item.module || item.mod || '';
          const file = item.file || '';
          return `<span>
            <b>${escapeHtml(name)}</b>
            <em>${score ? `评分 ${escapeHtml(score)}` : escapeHtml(item.executionLevel || item.level || label)}${reason ? ` · ${escapeHtml(reason)}` : ''}</em>
            ${mod && file ? `<button class="btn-sm" onclick="openFile(${jsArg(mod)}, ${jsArg(file)})">编辑 YAML</button>` : ''}
          </span>`;
        }).join('')}</div>${rows.length > 6 ? `<p>还有 ${escapeHtml(rows.length - 6)} 条未展开。</p>` : ''}` : '<p>暂无。</p>'}
      </section>
    `;
  }).join('');
  return `
    <section class="final-report-panel final-report-wide generated-execution-levels">
      <div class="section-head">
        <div>
          <strong>生成结果执行分层</strong>
          <p>平台会先下发“可执行”里的首批冒烟用例；首批用于确认 YAML 能下发、能运行、能产生日志。脚本/YAML/定位/超时类问题会暂停扩展，产品结果失败会记录为测试结果。</p>
        </div>
        ${mindmap.caseSetId && (smokeExecutableCount || remainingExecutableCount || generatedCount) ? `
          <div class="review-actions">
            ${smokeExecutableCount ? `<button class="btn-sm success" onclick="rerunGenerationSmokeCases(${jsArg(mindmap.caseSetId)}, '', ${smokeLimit}, false, ${smokeExecutableCount})">重跑首批冒烟 ${escapeHtml(smokeLimit)}/${escapeHtml(smokeExecutableCount)}</button>` : ''}
            ${smokeExecutableCount > smokeLimit ? `<button class="btn-sm" onclick="rerunGenerationSmokeCases(${jsArg(mindmap.caseSetId)}, '', 0, true, ${smokeExecutableCount})">重跑全部冒烟 ${escapeHtml(smokeExecutableCount)}</button>` : ''}
            <button class="btn-sm" onclick="rerunGenerationSmokeCases(${jsArg(mindmap.caseSetId)}, '', ${remainingLimit}, false, ${Math.max(remainingExecutableCount, (groups.executable_cases || []).length)}, 'remaining_executable')">继续下一批可执行 ${escapeHtml(remainingLimit)}/${escapeHtml(Math.max(remainingExecutableCount, (groups.executable_cases || []).length))}</button>
            <button class="btn-sm" onclick="rerunGenerationSmokeCases(${jsArg(mindmap.caseSetId)}, '', 0, true, ${generatedCount}, 'all_executable')">执行全部当前可执行</button>
          </div>
        ` : ''}
      </div>
      ${mindmap.caseSetId ? '<p>重跑首批冒烟默认最多 3 条；继续执行也默认按小批次下发。执行全部可执行需要手动触发，不会重新上传资料或重新分析需求。</p>' : ''}
      <div class="report-summary-grid final-report-metrics">
        ${labels.map(([key, label]) => `<div><span>${label}</span><strong>${escapeHtml(groups[key].length)}</strong></div>`).join('')}
      </div>
      <div class="final-report-layout">${listHtml}</div>
    </section>
  `;
}

function renderRunnerExecutionGateSummary(artifacts = {}) {
  const gate = artifacts.runnerExecutionGate || artifacts.runnerSmokeGate || {};
  if (!gate || typeof gate !== 'object' || !gate.enabled) return '';
  const plan = gate.executionPlan || artifacts.generatedYamlExecutionPlan || {};
  const planReadiness = (plan && typeof plan === 'object' && plan.readiness && typeof plan.readiness === 'object') ? plan.readiness : {};
  const planCounts = (plan && typeof plan === 'object' && plan.counts && typeof plan.counts === 'object') ? plan.counts : {};
  const planLimits = (plan && typeof plan === 'object' && plan.limits && typeof plan.limits === 'object') ? plan.limits : {};
  const blockingReasons = Array.isArray(planReadiness.blockingReasons) ? planReadiness.blockingReasons : [];
  let stop = '首批冒烟准入已启用';
  if (gate.stopFurtherExecution) {
    stop = `已停止后续批量执行：${gate.expandedStopReason || gate.reason || '首批冒烟不可执行或未产出有效结果'}`;
  } else if (gate.expandedExecution) {
    stop = `首批冒烟已完成执行准入，已扩展执行 ${gate.expandedCreatedCount ?? gate.expandedPlannedCount ?? 0} 条`;
  }
  const readinessText = planReadiness.stopFurtherExecution || gate.stopFurtherExecution
    ? '已阻断扩展'
    : (planReadiness.canDispatch === false ? '未达到下发准入' : '可下发/可继续');
  const policyText = gate.smokeFailurePolicy || planReadiness.smokeFailurePolicy || '冒烟用于验证 YAML 能下发、能运行、能产生日志；产品断言失败记录为测试结果。';
  const expandedBatches = Array.isArray(gate.expandedBatches) ? gate.expandedBatches : [];
  const expandedBatchHtml = expandedBatches.length ? `
    <div class="final-report-file-list compact">
      ${expandedBatches.map(batch => `
        <span>
          <b>第 ${escapeHtml(batch.batch || '-')} 批：计划 ${escapeHtml(batch.plannedCount ?? 0)}，下发 ${escapeHtml(batch.createdCount ?? 0)}</b>
          <em>成功 ${escapeHtml(batch.completedCount ?? 0)} · 失败 ${escapeHtml(batch.failedCount ?? 0)} · 超时 ${escapeHtml(batch.timeoutCount ?? 0)} · 拦截 ${escapeHtml(batch.blockedCount ?? 0)}</em>
        </span>
      `).join('')}
    </div>
  ` : '';
  return `
    <section class="final-report-panel final-report-wide">
      <strong>Runner 自动执行准入</strong>
      <p>${escapeHtml(stop)}</p>
      <p><b>准入状态：</b>${escapeHtml(readinessText)}；<b>策略：</b>${escapeHtml(policyText)}</p>
      <div class="report-summary-grid final-report-metrics">
        <div><span>首批上限</span><strong>${escapeHtml(gate.limit ?? planLimits.smokeLimit ?? '-')}</strong></div>
        <div><span>已选择</span><strong>${escapeHtml(gate.selectedCount ?? gate.smokeExecutedCount ?? 0)}</strong></div>
        <div><span>扩展执行</span><strong>${escapeHtml(gate.expandedCreatedCount ?? 0)}</strong></div>
        <div><span>剩余待跑</span><strong>${escapeHtml(gate.remainingDeferredCount ?? gate.deferredCount ?? 0)}</strong></div>
        <div><span>被拦截</span><strong>${escapeHtml(gate.blockingCount ?? gate.blockedCount ?? 0)}</strong></div>
        <div><span>可执行总数</span><strong>${escapeHtml(planCounts.executable ?? gate.executableCount ?? 0)}</strong></div>
      </div>
      ${gate.smokeFailureRate ? `<p>首批失败率：${escapeHtml(Math.round(Number(gate.smokeFailureRate || 0) * 100))}%</p>` : ''}
      ${blockingReasons.length ? `
        <div class="final-report-file-list compact">
          ${blockingReasons.slice(0, 5).map(reason => `<span><b>准入阻断</b><em>${escapeHtml(reason)}</em></span>`).join('')}
        </div>
      ` : ''}
      ${expandedBatchHtml}
    </section>
  `;
}

function renderAgentSummaryArtifact(run) {
  const artifacts = (run && run.artifacts) || {};
  const summary = artifacts.summary || {};
  if (!summary || typeof summary !== 'object') {
    return `<pre class="agent-artifact-pre">${escapeHtml(typeof agentArtifactText === 'function' ? agentArtifactText('summary', run) : '暂无总结报告')}</pre>`;
  }
  const report = artifacts.report || {};
  const failure = artifacts.failureAnalysis || {};
  const normalizedReport = normalizeAgentReportArtifacts(report);
  const reports = normalizedReport.executionReports;
  const failedJobs = report.failedJobs || [];
  const timeoutJobs = report.timeoutJobs || [];
  const yamlRefs = normalizedReport.yamlExecutionRefs;
  const jobStatuses = report.jobStatuses || [];
  const steps = Array.isArray(run?.steps) ? run.steps : [];
  const visibleSteps = steps.filter(step => ['FAILED', 'PARTIAL_FAILED', 'SUCCESS', 'WAIT_CONFIRM'].includes(String(step.status || '').toUpperCase())).slice(-6);
  const nextActions = Array.isArray(summary.nextActions) ? summary.nextActions : [];
  const mindmap = agentMindmapInfo(artifacts);
  const referenceExamples = agentYamlReferenceExamples(artifacts);
  const execution = (summary.execution && typeof summary.execution === 'object') ? summary.execution : {};
  const orchestration = (summary.orchestration && typeof summary.orchestration === 'object') ? summary.orchestration : {};
  const gate = (artifacts.runnerExecutionGate && typeof artifacts.runnerExecutionGate === 'object') ? artifacts.runnerExecutionGate : {};
  const timeoutIds = new Set(timeoutJobs.map(item => String(item.jobId || item.job_id || '')).filter(Boolean));
  const reportFailedOnly = failedJobs.filter(item => {
    const id = String(item.jobId || item.job_id || '');
    const status = String(item.status || '').toLowerCase();
    return status !== 'timeout' && (!id || !timeoutIds.has(id));
  }).length;
  const passedCount = Number(execution.passedCount ?? summary.passedJobCount ?? report.successJobs?.length ?? ((gate.smokePassedCount || 0) + (gate.expandedCompletedCount || 0))) || 0;
  const failedCount = Number(execution.failedCount ?? (failedJobs.length ? reportFailedOnly : summary.failedJobCount) ?? 0) || 0;
  const timeoutCount = Number(execution.timeoutCount ?? (timeoutJobs.length ? timeoutJobs.length : summary.timeoutJobCount) ?? 0) || 0;
  const runningCount = Number(execution.runningCount ?? (report.runningJobs?.length || summary.runningJobCount) ?? 0) || 0;
  const reportFailureTypes = failedJobs.reduce((counts, item) => {
    const type = String(item.failureType || item.failure_type || '').toUpperCase();
    if (type === 'PRODUCT_BUG') counts.product += 1;
    else if (['SCRIPT_ISSUE', 'ENV_ISSUE'].includes(type)) counts.broken += 1;
    else if (String(item.status || '').toLowerCase() !== 'timeout') counts.unknown += 1;
    return counts;
  }, {product: 0, broken: 0, unknown: 0});
  const productFailedCount = Number(execution.productFailedCount ?? summary.productFailedJobCount ?? reportFailureTypes.product) || 0;
  const brokenCount = Number(execution.brokenCount ?? summary.brokenJobCount ?? reportFailureTypes.broken) || 0;
  const unknownFailedCount = Number(execution.unknownFailedCount ?? summary.unknownFailedJobCount ?? reportFailureTypes.unknown) || 0;
  const attemptedCount = Number(execution.attemptedCount) || (passedCount + failedCount + timeoutCount + runningCount);
  const failedStepCount = Number(orchestration.failedStepCount ?? steps.filter(step => ['FAILED', 'PARTIAL_FAILED'].includes(String(step.status || '').toUpperCase())).length) || 0;
  const runStatus = String(run?.status || orchestration.runStatus || '').toUpperCase();
  const orchestrationBlocked = orchestration.state === 'blocked' || orchestration.state === 'cancelled' || failedStepCount > 0 || ['FAILED', 'CANCELLED'].includes(runStatus);
  const orchestrationLabel = orchestration.label || (runStatus === 'CANCELLED' ? '编排已取消' : (orchestrationBlocked ? '编排阻断' : '编排完成'));
  let resultLabel = execution.label || summary.conclusion || '-';
  if (!execution.label) {
    if (!attemptedCount && orchestrationBlocked) resultLabel = '未执行';
    else if (passedCount && (failedCount || timeoutCount || orchestrationBlocked)) resultLabel = '部分通过';
    else if (passedCount && !failedCount && !timeoutCount) resultLabel = '通过';
    else if (runningCount) resultLabel = '执行中';
    else if (failedCount || timeoutCount) resultLabel = '未通过';
  } else if (execution.outcome === 'passed' && orchestrationBlocked) {
    resultLabel = '部分通过';
  }
  const conclusionClass = resultLabel === '通过' ? 'success' : (['部分通过', '执行中', '未执行', '报告缺失'].includes(resultLabel) ? 'warn' : 'danger');
  const orchestrationClass = orchestrationBlocked ? 'danger' : 'success';
  const target = summary.target || run?.target || '-';
  const generatedAt = String(summary.generatedAt || run?.updatedAt || '').replace('T', ' ').slice(0, 19);
  return `
    <div class="agent-final-report">
      <div class="final-report-hero ${conclusionClass}">
        <div>
          <span class="final-report-kicker">Agent 最终报告</span>
          <h3>${escapeHtml(summary.title || 'Agent执行总结')}</h3>
          <p>${escapeHtml(target)}</p>
        </div>
        <div class="final-report-outcomes">
          <strong class="final-report-conclusion ${conclusionClass}">${escapeHtml(resultLabel)}</strong>
          <span class="final-report-orchestration ${orchestrationClass}">${escapeHtml(orchestrationLabel)}</span>
        </div>
      </div>
      <div class="final-report-meta">
        <span>${escapeHtml(agentModeText(summary.mode || run?.mode || '-'))}</span>
        <span>风险 ${escapeHtml(agentRiskText(summary.riskLevel || run?.riskLevel || '-'))}</span>
        <span>${escapeHtml(generatedAt || '-')}</span>
      </div>
      <div class="report-summary-grid final-report-metrics">
        <div><span>Runner 通过</span><strong>${escapeHtml(passedCount)}</strong></div>
        <div><span>产品断言失败</span><strong>${escapeHtml(productFailedCount)}</strong></div>
        <div><span>脚本 / 环境 / 待归因</span><strong>${escapeHtml(brokenCount + unknownFailedCount)}</strong></div>
        <div><span>超时 / 运行中</span><strong>${escapeHtml(timeoutCount + runningCount)}</strong></div>
      </div>
      ${renderGeneratedExecutionLevelSummary(artifacts)}
      ${renderRunnerExecutionGateSummary(artifacts)}
      <div class="final-report-layout">
        <section class="final-report-panel">
          <strong>执行概览</strong>
          <dl>
            <div><dt>报告状态</dt><dd>${escapeHtml(summary.reportStatus || report.status || '-')}</dd></div>
            <div><dt>Runner 结果</dt><dd>${escapeHtml(`${resultLabel}（${attemptedCount} 次真实执行）`)}</dd></div>
            <div><dt>Agent 编排</dt><dd>${escapeHtml(orchestrationLabel)}</dd></div>
            <div><dt>Agent 步骤</dt><dd>${escapeHtml(`${summary.completed || 0}/${summary.totalSteps || 0}`)}</dd></div>
            <div><dt>执行 YAML</dt><dd>${escapeHtml(yamlRefs.length || 0)} 个</dd></div>
            <div><dt>任务状态</dt><dd>${escapeHtml(jobStatuses.length || 0)} 个</dd></div>
            <div><dt>失败类型</dt><dd>${escapeHtml(summary.failureType || failure.failureType || 'NONE')}</dd></div>
          </dl>
        </section>
        <section class="final-report-panel">
          <strong>执行说明</strong>
          <p>${escapeHtml(summary.message || summary.aiSummary || failure.conclusion || '暂无说明')}</p>
        </section>
      </div>
      <div class="final-report-layout">
        <section class="final-report-panel">
          <strong>报告链接</strong>
          ${reports.length ? `<div class="final-report-links">${reports.slice(0, 6).map(item => {
            const label = agentCaseLabel(item);
            const subLabel = agentCaseSubLabel(item);
            return item.reportUrl
              ? `<a href="${escapeHtml(item.reportUrl)}" target="_blank" class="report-case-link"><b>${escapeHtml(label)}</b><span>${escapeHtml(subLabel || '打开执行报告')}</span></a>`
              : `<span class="report-case-link"><b>${escapeHtml(label)}</b><span>${escapeHtml(subLabel || item.localPath || '-')}</span></span>`;
          }).join('')}</div>${reports.length > 6 ? `<p>还有 ${escapeHtml(reports.length - 6)} 份报告，可在执行报告页继续查看。</p>` : ''}` : '<p>暂无 HTML 报告链接。</p>'}
        </section>
        <section class="final-report-panel">
          <strong>下一步建议</strong>
          ${nextActions.length ? `<ul>${nextActions.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '<p>暂无建议。</p>'}
        </section>
      </div>
      ${(mindmap.url || mindmap.path) ? `
        <section class="final-report-panel final-report-wide">
          <strong>脑图文件</strong>
          <div class="final-report-links">
            ${mindmap.url
              ? `<a href="${escapeHtml(mindmap.url)}" target="_blank">下载同步生成的 .mm 脑图</a>`
              : `<span>${escapeHtml(mindmap.path)}</span>`}
            ${mindmap.caseSetId ? `<span>生成批次：${escapeHtml(mindmap.caseSetId)}</span>` : ''}
          </div>
        </section>
      ` : ''}
      ${yamlRefs.length ? `
        <section class="final-report-panel final-report-wide">
          <strong>执行 YAML</strong>
          <div class="final-report-file-list">
            ${yamlRefs.slice(0, 18).map(item => `<span><b>${escapeHtml(agentCaseLabel(item))}</b><em>${escapeHtml(agentCaseSubLabel(item))}</em></span>`).join('')}
          </div>
          ${yamlRefs.length > 18 ? `<p>已展示前 18 个，剩余 ${escapeHtml(yamlRefs.length - 18)} 个可在执行任务列表中查看。</p>` : ''}
        </section>
      ` : ''}
      ${renderYamlReferenceExamples(referenceExamples)}
      ${failedJobs.length ? `
        <section class="final-report-panel final-report-wide">
          <strong>失败摘要</strong>
          <div class="final-report-failures">
            ${failedJobs.slice(0, 8).map(job => {
              const reason = job.failureReason || job.reason || job.error || job.stderrTail || job.stdoutTail || job.status || '未知失败';
              const type = job.failureType ? `失败类型：${job.failureType}` : agentCaseSubLabel(job);
              return `<div><b>${escapeHtml(agentCaseLabel(job))}</b><em>${escapeHtml(type)}</em><span>${escapeHtml(reason)}</span></div>`;
            }).join('')}
          </div>
          ${failedJobs.length > 8 ? `<p>只展示前 8 个失败，剩余 ${escapeHtml(failedJobs.length - 8)} 个请到执行报告或失败分析页查看。</p>` : ''}
        </section>
      ` : ''}
      ${visibleSteps.length ? `
        <section class="final-report-panel final-report-wide">
          <strong>关键步骤</strong>
          <div class="final-report-steps">
            ${visibleSteps.map(step => `<div><span>${escapeHtml(step.step || '')}</span><b>${escapeHtml(step.status || '')}</b><em>${escapeHtml(step.summary || '')}</em></div>`).join('')}
          </div>
        </section>
      ` : ''}
    </div>
  `;
}

const AGENT_ARTIFACT_STEP_MAP = {
  plan: ['PLAN'],
  cases: ['IMPACT_ANALYSIS', 'CASE_RETRIEVAL', 'MATCH_CASES'],
  quality: ['GENERATE_YAML', 'VALIDATE_YAML', 'RISK_REVIEW'],
  yaml: ['GENERATE_YAML'],
  validation: ['VALIDATE_YAML', 'RISK_REVIEW', 'EXECUTION_PRECHECK'],
  logs: AUTO_AGENT_STEPS,
  report: ['RUN_SONIC', 'COLLECT_REPORT'],
  failure: ['ANALYZE_FAILURE', 'DIAGNOSE_FAILURE'],
  repair: ['GENERATE_REPAIR', 'APPLY_SAFE_REPAIR', 'RERUN'],
  bug: ['GENERATE_BUG_DRAFT'],
  summary: ['LEARN_FROM_RESULT', 'GENERATE_SUMMARY', 'DONE']
};
const AGENT_RECOVERY_ARTIFACTS = new Set(['failure', 'repair', 'bug']);

function agentArtifactDefinition(tab) {
  for (const group of AGENT_ARTIFACT_GROUPS) {
    const item = group.items.find(candidate => candidate.key === tab);
    if (item) return item;
  }
  return { key: tab, label: tab, title: tab, description: '' };
}

function agentArtifactValuePresent(value) {
  if (typeof artifactHasValue === 'function') return artifactHasValue(value);
  if (value === undefined || value === null) return false;
  if (typeof value === 'string') return Boolean(value.trim());
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  return true;
}

function agentArtifactAvailable(tab, run) {
  const artifacts = (run && run.artifacts) || {};
  const any = (...values) => values.some(agentArtifactValuePresent);
  if (tab === 'plan') return any(artifacts.plan);
  if (tab === 'cases') return any(
    artifacts.generatedCases,
    artifacts.generatedCaseGroups,
    artifacts.caseDraft,
    artifacts.cases,
    artifacts.matchedCases
  );
  if (tab === 'quality') return any(artifacts.qualityReport);
  if (tab === 'yaml') return any(
    artifacts.generatedYaml,
    artifacts.yamlDraft,
    artifacts.yaml,
    artifacts.yamlRefs,
    artifacts.generatedYamlPaths,
    artifacts.generationPipeline?.yamlFiles
  );
  if (tab === 'validation') return any(
    artifacts.yamlValidation,
    artifacts.validation,
    artifacts.runnerExecutionGate
  );
  if (tab === 'logs') return Array.isArray(run?.steps) && run.steps.length > 0;
  if (tab === 'report') return any(
    artifacts.report,
    artifacts.sonicJob,
    artifacts.jobProgress,
    artifacts.jobResult
  );
  if (tab === 'failure') return any(
    artifacts.failureAnalysis,
    artifacts.failedExecutionItems,
    artifacts.diagnosis,
    run?.failureAnalysis,
    run?.error
  );
  if (tab === 'repair') return any(
    artifacts.repairSummary,
    artifacts.repairDrafts,
    artifacts.repairDraft,
    artifacts.rerunProgress,
    artifacts.rerunResult
  );
  if (tab === 'bug') return any(artifacts.bugDraft, artifacts.bug);
  if (tab === 'summary') return any(artifacts.summary);
  return false;
}

function agentArtifactState(tab, run) {
  if (agentArtifactAvailable(tab, run)) return 'ready';
  const relatedSteps = AGENT_ARTIFACT_STEP_MAP[tab] || [];
  const currentStep = String(run?.currentStep || '').toUpperCase();
  const stepRows = Array.isArray(run?.steps) ? run.steps : [];
  const matchingRows = stepRows.filter(step => relatedSteps.includes(String(step?.state || step?.step || '').toUpperCase()));
  const isRunning = relatedSteps.includes(currentStep) || matchingRows.some(step => (
    String(step?.status || step?.stateStatus || '').toUpperCase() === 'RUNNING'
  ));
  if (isRunning) return 'running';

  const runStatus = String(run?.status || '').toUpperCase();
  const failureSeen = runStatus === 'FAILED' || matchingRows.some(step => (
    step?.success === false || ['FAILED', 'PARTIAL_FAILED'].includes(String(step?.status || '').toUpperCase())
  ));
  if (AGENT_RECOVERY_ARTIFACTS.has(tab) && !failureSeen) return 'optional';
  if (['DONE', 'FINISH', 'FAILED', 'CANCELLED'].includes(runStatus)) return 'missing';
  if (matchingRows.some(step => step?.success === true || ['SUCCESS', 'SKIPPED'].includes(String(step?.status || '').toUpperCase()))) {
    return 'missing';
  }

  const currentIndex = AUTO_AGENT_STEPS.indexOf(currentStep);
  const lastRelatedIndex = Math.max(...relatedSteps.map(step => AUTO_AGENT_STEPS.indexOf(step)));
  if (currentIndex >= 0 && lastRelatedIndex >= 0 && currentIndex > lastRelatedIndex) return 'missing';
  return 'pending';
}

function agentArtifactStateLabel(state) {
  return {
    ready: '已产出',
    running: '生成中',
    pending: '等待',
    optional: '按需',
    missing: '未产出'
  }[state] || state;
}

function renderAgentArtifactEmpty(tab, state) {
  const meta = agentArtifactDefinition(tab);
  const copy = {
    running: [`正在生成${meta.title}`, 'Agent 完成当前阶段后会自动刷新，无需手动重载。'],
    pending: ['等待前序阶段', `${meta.title}将在相关阶段开始后显示。`],
    optional: [`当前无需${meta.title}`, '失败恢复类产物只在出现可分析的执行失败时生成。'],
    missing: [`本次未生成${meta.title}`, '该阶段已结束或任务已终止，平台没有收到对应产物。']
  }[state] || ['暂无产物', '当前没有可展示的内容。'];
  return `
    <div class="agent-artifact-empty" data-empty-state="${escapeHtml(state)}">
      <span>${escapeHtml(agentArtifactStateLabel(state))}</span>
      <strong>${escapeHtml(copy[0])}</strong>
      <p>${escapeHtml(copy[1])}</p>
    </div>
  `;
}

function renderAgentArtifactContent(tab, run, state = agentArtifactState(tab, run)) {
  if (state !== 'ready') return renderAgentArtifactEmpty(tab, state);
  const artifacts = (run && run.artifacts) || {};
  if (tab === 'plan') return renderPlanDetail({}, (run && run.artifacts) || {});
  if (tab === 'quality') return renderAgentQualityArtifact(run);
  if (tab === 'validation') return renderValidateYamlDetail({}, (run && run.artifacts) || {});
  if (tab === 'report') return renderAgentReportArtifact(run);
  if (tab === 'failure') return renderAnalysisDetail({}, artifacts, run);
  if (tab === 'repair') return renderRepairDraftDetail({}, artifacts);
  if (tab === 'summary') return renderAgentSummaryArtifact(run);
  const artifactText = typeof agentArtifactText === 'function' ? agentArtifactText(tab, run) : '';
  return `<pre class="agent-artifact-pre">${escapeHtml(artifactText)}</pre>`;
}

function renderAgentArtifactPanel(run) {
  const allItems = AGENT_ARTIFACT_GROUPS.flatMap(group => group.items);
  if (!allItems.some(item => item.key === agentActiveTab)) agentActiveTab = 'plan';
  const activeMeta = agentArtifactDefinition(agentActiveTab);
  const activeState = agentArtifactState(agentActiveTab, run);
  const readyCount = allItems.filter(item => agentArtifactAvailable(item.key, run)).length;
  const yamlReady = agentArtifactAvailable('yaml', run);
  const mindmapReady = Boolean(agentMindmapDownloadUrl(run));
  const actions = [
    activeState === 'ready'
      ? '<button class="btn-sm" type="button" onclick="copyAgentArtifact()">复制当前产物</button>'
      : '',
    yamlReady
      ? '<button class="btn-sm" type="button" onclick="downloadAgentYaml()">下载 YAML</button>'
      : '',
    mindmapReady
      ? '<button class="btn-sm" type="button" onclick="downloadAgentMindmap()">下载脑图</button>'
      : ''
  ].filter(Boolean);
  const rich = ['plan', 'quality', 'validation', 'report', 'failure', 'repair', 'summary'].includes(agentActiveTab);
  return `
    <div class="agent-artifact-head">
      <div>
        <h3>Agent 产物</h3>
        <p>已生成 ${escapeHtml(readyCount)}/${escapeHtml(allItems.length)} · 当前阶段 ${escapeHtml(agentStepLabel(run?.currentStep || run?.status || '-'))}</p>
      </div>
      ${actions.length ? `<div class="agent-artifact-actions">${actions.join('')}</div>` : ''}
    </div>
    <div class="agent-artifact-layout"
         data-agent-run-id="${escapeHtml(run?.runId || '')}"
         data-agent-tab="${escapeHtml(agentActiveTab)}">
      <nav class="agent-artifact-nav" aria-label="Agent 产物导航">
        ${AGENT_ARTIFACT_GROUPS.map(group => `
          <section class="agent-artifact-nav-group">
            <strong>${escapeHtml(group.label)}</strong>
            <div>
              ${group.items.map(item => {
                const state = agentArtifactState(item.key, run);
                const label = agentArtifactStateLabel(state);
                return `
                  <button type="button"
                          class="agent-artifact-nav-item ${agentActiveTab === item.key ? 'active' : ''} state-${escapeHtml(state)}"
                          data-tab="${escapeHtml(item.key)}"
                          onclick="setAgentTab(${jsArg(item.key)})"
                          title="${escapeHtml(item.title)} · ${escapeHtml(label)}"
                          aria-current="${agentActiveTab === item.key ? 'page' : 'false'}">
                    <span class="agent-artifact-status-dot" aria-hidden="true"></span>
                    <span class="agent-artifact-nav-label">${escapeHtml(item.label)}</span>
                    <span class="agent-artifact-nav-state">${escapeHtml(label)}</span>
                  </button>
                `;
              }).join('')}
            </div>
          </section>
        `).join('')}
      </nav>
      <section class="agent-artifact-view">
        <header class="agent-artifact-view-head">
          <div>
            <h4>${escapeHtml(activeMeta.title)}</h4>
            <p>${escapeHtml(activeMeta.description)}</p>
          </div>
          <span class="agent-artifact-state state-${escapeHtml(activeState)}">${escapeHtml(agentArtifactStateLabel(activeState))}</span>
        </header>
        <div class="agent-artifact-box ${rich ? 'rich' : ''} ${activeState === 'ready' ? '' : 'is-empty'}" id="agent-artifact-box">
          ${renderAgentArtifactContent(agentActiveTab, run, activeState)}
        </div>
      </section>
    </div>
  `;
}

function renderGenerateYamlDetail(step, artifacts) {
  const pipeline = (artifacts || {}).generationPipeline || {};
  const validation = (artifacts || {}).yamlValidation || {};
  const summary = (artifacts || {}).generationSummary || {};
  const summaryFiles = pipeline.summaryFiles || summary.summaryFiles || {};
  const yamlFiles = Array.isArray(pipeline.yamlFiles) ? pipeline.yamlFiles.filter(Boolean) : [];
  const validationResults = Array.isArray(validation.results) ? validation.results : [];
  const fallback = validationResults.find(item => item && item.type === 'fallback') || {};
  const executable = pipeline.yamlExecutability || {};
  const sourceLabel = {
    ui_yaml_pipeline: '完整 YAML 生成主链',
    mindmap_pipeline: '旧脑图管线',
  }[pipeline.source] || pipeline.source || '未记录';
  let html = '<div class="match-detail agent-readable-detail">';
  html += agentInfoGrid([
    { label: '生成链路', value: sourceLabel },
    { label: 'YAML 文件', value: pipeline.yamlFileCount ?? yamlFiles.length ?? 0 },
    { label: '用例', value: pipeline.caseCount ?? '-' },
    { label: '可执行任务', value: executable.taskCount ?? fallback.taskCount ?? '-' },
  ]);
  if ((artifacts || {}).qualityReport) {
    html += renderAgentQualityArtifact({artifacts});
  }
  html += renderGeneratedExecutionLevelSummary(artifacts || {});
  html += renderRunnerExecutionGateSummary(artifacts || {});
  if (pipeline.error) {
    html += `<section class="agent-readable-panel"><strong>主链错误</strong><p>${escapeHtml(pipeline.error)}</p></section>`;
  }
  if (Array.isArray(validation.issues) && validation.issues.length) {
    html += agentReadableList('校验结果', validation.issues.slice(0, 8));
  }
  if (validation.fallbackOk) {
    html += `<section class="agent-readable-panel"><strong>兜底草稿</strong><p>完整生成链路没有产出可执行 YAML，系统已生成 ${escapeHtml(fallback.taskCount ?? '-')} 条待确认草稿。</p></section>`;
  }
  if (yamlFiles.length) {
    html += agentReadableList('生成的 YAML', yamlFiles.slice(0, 20), file => `<b>${escapeHtml(file)}</b><span>已按单用例拆分，校验通过后会自动进入后续执行。</span>`);
  }
  html += renderYamlReferenceExamples(agentYamlReferenceExamples(artifacts || {}));
  const generatedArtifacts = [
    summaryFiles.mindmap ? { label: '脑图 .mm', path: summaryFiles.mindmap } : null,
    summaryFiles.markdown ? { label: '生成摘要', path: summaryFiles.markdown } : null,
    summaryFiles.json ? { label: '结构化用例', path: summaryFiles.json } : null,
  ].filter(Boolean);
  if (generatedArtifacts.length) {
    html += agentReadableList('同步生成产物', generatedArtifacts, item => `<b>${escapeHtml(item.label)}</b><span>${escapeHtml(item.path)}</span>`);
  }
  html += renderDiagnosisDetail((step || {}).diagnosis);
  html += '</div>';
  return html;
}

function renderValidateYamlDetail(step, artifacts) {
  const validation = (artifacts || {}).yamlValidation || {};
  const quarantined = Array.isArray((artifacts || {}).quarantinedYamlRefs)
    ? (artifacts || {}).quarantinedYamlRefs
    : (Array.isArray(validation.quarantinedRefs) ? validation.quarantinedRefs : []);
  const results = Array.isArray(validation.results) ? validation.results : [];
  const issues = Array.isArray(validation.issues) ? validation.issues : [];
  const autoRepairs = Array.isArray(validation.autoRepairs) ? validation.autoRepairs.filter(Boolean) : [];
  const passedCount = validation.passedCount ?? results.filter(item => item && item.ok).length;
  const failedCount = validation.failedCount ?? results.filter(item => item && !item.ok).length;
  let html = '<div class="match-detail agent-readable-detail">';
  html += agentInfoGrid([
    { label: '校验 YAML', value: results.length || (passedCount + failedCount) || '-' },
    { label: '通过', value: passedCount || 0 },
    { label: '已隔离', value: failedCount || quarantined.length || 0 },
    { label: '自动修复', value: validation.autoRepairedCount ?? autoRepairs.length ?? 0 },
    { label: '后续执行', value: quarantined.length || failedCount ? '只下发通过项' : '全部可继续' },
  ]);
  if (validation.partialOk) {
    html += `<section class="agent-readable-panel"><strong>部分通过</strong><p>未通过的 YAML 已隔离为需人工复核，不会创建 Runner 任务；通过的 YAML 会继续进入执行前体检。</p></section>`;
  }
  if (autoRepairs.length) {
    html += agentReadableList('自动修复', autoRepairs.slice(0, 8), item => {
      const after = item.after || {};
      const changes = Array.isArray(item.changes) ? item.changes.length : 0;
      return `<b>${escapeHtml(item.type || '结构修复')}</b><span>补齐 ${escapeHtml(changes)} 处等待/终态判断；修复后 ${after.dryRunOk ? 'dry-run 通过' : '仍需复核'} · ${escapeHtml(after.executionLevel || '-')}</span>`;
    });
  }
  if (quarantined.length) {
    html += agentReadableList('已隔离 YAML', quarantined.slice(0, 12), item => {
      const issuesText = Array.isArray(item.issues) && item.issues.length
        ? item.issues.slice(0, 2).join('；')
        : (item.reason || '未通过可执行性准入');
      return `<b>${escapeHtml(item.file || item.path || '未命名 YAML')}</b><span>${escapeHtml(issuesText)}</span>`;
    });
  }
  if (issues.length) {
    html += agentReadableList('校验问题', issues.slice(0, 8));
  }
  if (results.length) {
    html += agentReadableList('校验明细', results.slice(0, 20), item => {
      const status = item.ok ? '通过' : '隔离';
      const reason = Array.isArray(item.issues) && item.issues.length ? item.issues[0] : (item.reason || '');
      return `<b>${escapeHtml(item.file || item.path || item.type || 'YAML')}</b><span>${escapeHtml(status)} · ${escapeHtml(item.executionLevel || '-')}${reason ? ' · ' + escapeHtml(reason) : ''}</span>`;
    });
  }
  html += '</div>';
  return html;
}

// ===== ANALYZE_FAILURE 分析详情 =====
function agentCompactDisplayText(value, maxLength = 420) {
  if (value == null) return '';
  const source = typeof value === 'string' ? value : JSON.stringify(value);
  const text = String(source || '').replace(/\s+/g, ' ').trim();
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength).trim()}...`;
}

function agentFailureTypeMeta(value) {
  const key = String(value || 'UNKNOWN').toUpperCase();
  const meta = {
    NONE: {label: '无失败', className: 'failure-none'},
    PRODUCT_BUG: {label: '产品问题', className: 'failure-product_bug'},
    SCRIPT_ISSUE: {label: '脚本问题', className: 'failure-script_issue'},
    ENV_ISSUE: {label: '环境问题', className: 'failure-env_issue'},
    CONFIG_ISSUE: {label: '配置问题', className: 'failure-config_issue'},
    UNKNOWN: {label: '待归因', className: 'failure-unknown'},
  }[key] || {label: key, className: 'failure-unknown'};
  return {...meta, key};
}

function renderAnalysisDetail(step, artifacts, run = null) {
  const storedAnalysis = (artifacts || {}).failureAnalysis || (run && run.failureAnalysis) || null;
  const analysis = storedAnalysis && typeof storedAnalysis === 'object'
    ? storedAnalysis
    : (storedAnalysis ? {conclusion: String(storedAnalysis)} : {});
  const failedItems = (artifacts || {}).failedExecutionItems || [];
  const diagnosis = (artifacts || {}).diagnosis || (step && step.diagnosis) || {};
  const runError = agentCompactDisplayText(run && run.error, 420);
  const evidence = analysis.evidence && typeof analysis.evidence === 'object' ? analysis.evidence : {};
  const keyframes = Array.isArray(evidence.reportKeyframes) ? evidence.reportKeyframes : [];
  const keyframeCount = Number(evidence.reportKeyframeCount ?? keyframes.length) || 0;
  const baselines = Array.isArray(evidence.baselineExamples) ? evidence.baselineExamples : [];
  const aiEvidence = Array.isArray(analysis.aiEvidence) ? analysis.aiEvidence : [];
  const nextActions = Array.isArray(diagnosis.nextActions) ? diagnosis.nextActions.filter(Boolean) : [];
  const typeMeta = agentFailureTypeMeta(analysis.failureType);
  const conclusion = agentCompactDisplayText(
    analysis.conclusion || diagnosis.rootCause || runError || analysis.summary || '尚未形成明确根因。'
  );
  const impact = agentCompactDisplayText(
    diagnosis.impact || (failedItems.length
      ? `${failedItems.length} 个 Runner 任务未通过；其他已通过任务继续保留真实结果。`
      : '当前 Agent 阶段受阻，尚无 Runner 失败任务明细。')
  );
  const recommendation = agentCompactDisplayText(
    analysis.recommendation || (!nextActions.length ? '尚无可执行修复建议，需继续补充失败证据。' : '')
  );
  const repairLabel = Object.prototype.hasOwnProperty.call(analysis, 'canAutoRepair')
    ? (analysis.canAutoRepair ? '可自动修复' : '不可直接修复')
    : '待判断';
  let html = '<div class="analysis-detail agent-readable-detail agent-failure-detail">';
  html += `
    <section class="agent-failure-overview" data-failure-type="${escapeHtml(typeMeta.key)}">
      <div class="agent-failure-overview-copy">
        <span class="failure-type-chip ${escapeHtml(typeMeta.className)}">${escapeHtml(typeMeta.label)}</span>
        <div>
          <strong>失败归因已生成</strong>
          <span>执行结果与编排状态分开统计，当前仅解释未通过任务。</span>
        </div>
      </div>
      <div class="agent-failure-metrics">
        <div><span>失败任务</span><b>${escapeHtml(failedItems.length)}</b></div>
        <div><span>Runner 关键帧</span><b>${escapeHtml(keyframeCount)}</b></div>
        <div><span>成功基线</span><b>${escapeHtml(baselines.length)}</b></div>
        <div><span>修复判断</span><b>${escapeHtml(repairLabel)}</b></div>
      </div>
    </section>
    <div class="agent-failure-card-grid">
      <article class="agent-failure-card is-cause">
        <span>根因判断</span>
        <p>${escapeHtml(conclusion)}</p>
      </article>
      <article class="agent-failure-card is-impact">
        <span>影响范围</span>
        <p>${escapeHtml(impact)}</p>
      </article>
      <article class="agent-failure-card is-action">
        <span>建议动作</span>
        ${recommendation ? `<p>${escapeHtml(recommendation)}</p>` : ''}
        ${nextActions.length ? `<ul>${nextActions.slice(0, 5).map(item => `<li>${escapeHtml(agentCompactDisplayText(item, 180))}</li>`).join('')}</ul>` : ''}
      </article>
    </div>
  `;
  if (failedItems.length) {
    html += `
      <section class="agent-failure-task-section">
        <header class="agent-failure-section-head">
          <strong>失败任务</strong>
          <span>${escapeHtml(failedItems.length)} 项</span>
        </header>
        <div class="agent-failure-task-list">
          ${failedItems.slice(0, 15).map((item, index) => {
            const itemType = agentFailureTypeMeta(item.failureType || analysis.failureType);
            const file = String(item.file || '');
            const fileName = file.split(/[\\/]/).pop() || file;
            const reason = agentCompactDisplayText(
              item.failureReason || item.error || item.summaryText || 'Runner 未返回明确失败原因。',
              320
            );
            return `
              <article class="agent-failure-task">
                <header>
                  <div>
                    <span class="agent-failure-task-index">${String(index + 1).padStart(2, '0')}</span>
                    <strong>${escapeHtml(item.taskName || fileName || item.jobId || '失败任务')}</strong>
                  </div>
                  <span class="failure-type-chip ${escapeHtml(itemType.className)}">${escapeHtml(itemType.label)}</span>
                </header>
                <p>${escapeHtml(reason)}</p>
                <div class="agent-failure-task-meta">
                  ${fileName ? `<span><b>脚本</b><code title="${escapeHtml(file)}">${escapeHtml(fileName)}</code></span>` : ''}
                  ${item.jobId ? `<span><b>任务</b><code>${escapeHtml(item.jobId)}</code></span>` : ''}
                  ${item.status ? `<span><b>状态</b><em>${escapeHtml(item.status)}</em></span>` : ''}
                  ${item.reportUrl ? `<a href="${escapeHtml(item.reportUrl)}" target="_blank" rel="noopener">查看 Runner 报告</a>` : ''}
                </div>
              </article>
            `;
          }).join('')}
        </div>
      </section>
    `;
  }
  if (baselines.length || aiEvidence.length || keyframeCount) {
    html += `
      <section class="agent-failure-evidence-section">
        <header class="agent-failure-section-head">
          <strong>AI 判断依据</strong>
          <span>${escapeHtml(keyframeCount + baselines.length + aiEvidence.length)} 条证据</span>
        </header>
        <div class="agent-failure-evidence-list">
          ${keyframeCount ? `<div><b>Runner 关键帧</b><span>已提供 ${escapeHtml(keyframeCount)} 张失败现场图用于页面状态判断</span></div>` : ''}
          ${baselines.slice(0, 6).map(item => {
            const path = String(item.provenancePath || '');
            const name = path.split(/[\\/]/).pop() || item.id || '成功基线';
            return `<div><b>成功基线 · ${escapeHtml(name)}</b><span>${escapeHtml(agentCompactDisplayText(item.businessPath || path || item.id || '', 220))}</span></div>`;
          }).join('')}
          ${aiEvidence.slice(0, 5).map(item => `<div><b>AI 证据</b><span>${escapeHtml(agentCompactDisplayText(item, 260))}</span></div>`).join('')}
        </div>
      </section>
    `;
  }
  const rawPayload = {
    analysis,
    diagnosis: Object.keys(diagnosis).length ? diagnosis : null,
    failedExecutionItems: failedItems,
    runError: runError || null,
  };
  html += `
    <details class="agent-failure-technical" data-agent-detail-key="failure-technical">
      <summary>
        <span>技术详情</span>
        <small>原始分析数据、完整路径与 Runner 字段</small>
      </summary>
      <pre class="agent-artifact-pre">${escapeHtml(JSON.stringify(rawPayload, null, 2))}</pre>
    </details>
  `;
  html += '</div>';
  return html;
}

function renderRepairDraftDetail(step, artifacts) {
  const draft = (artifacts || {}).repairDraft || {};
  const drafts = (artifacts || {}).repairDrafts || (draft && Object.keys(draft).length ? [draft] : []);
  const summary = (artifacts || {}).repairSummary || {};
  const calls = (step && step.toolCalls) || [];
  const call = calls.find(item => item && typeof item === 'object') || {};
  const validation = summary.yamlValidation || draft.validation || call.yamlValidation || {};
  const changes = summary.changes || draft.changes || [];
  const source = draft.repairSource || call.repairSource || 'unknown';
  const sourceText = {
    ai_gateway: '已调用 AI，根据失败日志生成 YAML 草稿',
    diagnosis_only: '仅保存诊断草稿，未生成可应用 YAML',
    not_started: '未开始生成修复',
  }[source] || source;
  let html = '<div class="match-detail agent-readable-detail">';
  html += agentInfoGrid([
    { label: '修复方式', value: sourceText },
    { label: '失败任务', value: summary.failedTaskCount ?? call.failedTaskCount ?? '-' },
    { label: '修复目标', value: summary.repairTargetCount ?? call.repairTargetCount ?? drafts.length ?? 0 },
    { label: '草稿数量', value: summary.draftCount ?? drafts.length ?? 0 },
    { label: 'AI 返回 YAML', value: summary.aiUsedCount ?? (summary.aiUsed || call.aiUsed ? 1 : 0) },
    { label: 'YAML 校验', value: validation && Object.keys(validation).length ? (validation.ok ? '通过' : '未通过') : '未校验' },
  ]);
  const items = Array.isArray(summary.items) ? summary.items : [];
  if (items.length) {
    html += agentReadableList('修复覆盖的失败任务', items.slice(0, 15), item => {
      const status = item.aiUsed ? '已返回 YAML' : (item.blockedReason ? `未生成：${item.blockedReason}` : '已保存诊断');
      return `<b>${escapeHtml(item.targetTaskName || item.file || item.targetJobId || '失败任务')}</b><span>${escapeHtml(status)} · ${escapeHtml(item.failureReason || '')}</span>`;
    });
  }
  if (draft.analysis || draft.suggestion) {
    html += `<section class="agent-readable-panel"><strong>修复依据</strong><p>${escapeHtml(draft.analysis || '')}</p>${draft.suggestion ? `<p>${escapeHtml(draft.suggestion)}</p>` : ''}</section>`;
  }
  if (drafts.length) {
    html += agentReadableList('修复草稿文件', drafts.slice(0, 20), item => {
      const target = item.targetTaskName || item.taskName || item.file || item.targetJobId || '修复草稿';
      const status = item.fixedYaml || item.fixed_yaml ? '已生成 YAML 草稿' : (item.analysis ? '仅生成诊断' : '未生成');
      return `<b>${escapeHtml(target)}</b><span>${escapeHtml(status)}${item.path ? ' · ' + escapeHtml(item.path) : ''}</span>`;
    });
  }
  if (draft.evidence) {
    html += `<section class="agent-readable-panel"><strong>使用的失败日志</strong><pre class="agent-artifact-pre">${escapeHtml(String(draft.evidence).slice(0, 1600))}</pre></section>`;
  }
  if (Array.isArray(changes) && changes.length) {
    html += agentReadableList('修复变化', changes.slice(0, 8), item => `<span>${escapeHtml(typeof item === 'string' ? item : JSON.stringify(item))}</span>`);
  }
  if (validation && Array.isArray(validation.issues) && validation.issues.length) {
    html += agentReadableList('校验问题', validation.issues.slice(0, 8));
  }
  if (draft.fixedYaml || draft.fixed_yaml) {
    html += `<section class="agent-readable-panel"><strong>草稿状态</strong><p>已生成可查看/确认的 YAML 修复草稿，不会自动覆盖原 YAML。</p></section>`;
  } else {
    html += `<section class="agent-readable-panel"><strong>草稿状态</strong><p>没有生成可应用 YAML，只记录了失败证据；需要重新分析日志或人工修正。</p></section>`;
  }
  html += '</div>';
  return html;
}

function agentRerunStatusMeta(status) {
  const value = String(status || 'pending').toLowerCase();
  if (value === 'success') return {key: 'success', label: '成功'};
  if (['failed', 'error'].includes(value)) return {key: 'failed', label: '失败'};
  if (value === 'timeout') return {key: 'timeout', label: '超时'};
  if (value === 'cancelled') return {key: 'cancelled', label: '已取消'};
  if (value === 'skipped') return {key: 'skipped', label: '未重跑'};
  if (['created', 'queued', 'assigned', 'waiting', 'running', 'creating'].includes(value)) {
    return {key: 'running', label: value === 'creating' ? '正在创建' : '执行中'};
  }
  return {key: 'pending', label: '等待执行'};
}

function agentRerunFallbackItems(progress, sources, result, repairSummary) {
  const completedIds = new Set((result.completed || []).map(item => item.job_id || item.jobId));
  const failedById = new Map((result.failed || []).map(item => [item.job_id || item.jobId, item]));
  const timeoutById = new Map((result.timeout || []).map(item => [item.job_id || item.jobId, item]));
  const repairByDraft = new Map(((repairSummary || {}).items || []).map(item => [item.draftId || '', item]));
  return (sources || []).map(item => {
    const jobId = item.newJobId || '';
    const failed = failedById.get(jobId) || timeoutById.get(jobId) || {};
    const repair = repairByDraft.get(item.draftId || '') || {};
    return {
      ...item,
      sourceFile: item.sourceFile || item.file || '',
      repairFile: item.source === 'repair_draft' ? item.file : '',
      repairChanges: repair.changes || [],
      repairSource: item.source || '',
      status: completedIds.has(jobId) ? 'success' : (timeoutById.has(jobId) ? 'timeout' : (failedById.has(jobId) ? 'failed' : 'running')),
      reportUrl: failed.report_url || failed.reportUrl || '',
      resultReason: failed.error || '',
    };
  });
}

function agentRerunCycleMetrics(progress, items) {
  const statuses = (items || []).map(item => String(item.status || 'pending').toLowerCase());
  const hasItemStatuses = statuses.some(Boolean);
  const success = Number(progress.successCount ?? statuses.filter(value => value === 'success').length);
  const failed = Number(progress.failedCount ?? statuses.filter(value => ['failed', 'error', 'cancelled'].includes(value)).length);
  const timeout = Number(progress.timeoutCount ?? statuses.filter(value => value === 'timeout').length);
  const skipped = Number(progress.skippedCount ?? statuses.filter(value => value === 'skipped').length);
  const running = Number(hasItemStatuses
    ? statuses.filter(value => ['running', 'assigned'].includes(value)).length
    : (progress.runningCount ?? 0));
  const pending = Number(hasItemStatuses
    ? statuses.filter(value => ['pending', 'created', 'queued', 'waiting', 'creating'].includes(value)).length
    : (progress.pendingCount ?? 0));
  const total = Number(progress.total ?? progress.sourceFailedCount ?? items.length ?? 0);
  const completed = Number(progress.completedCount ?? (success + failed + timeout + skipped));
  return {total, completed, success, failed, timeout, skipped, running, pending};
}

function agentRerunCycleTitle(progress, cycleIndex) {
  const attempt = cycleIndex + 1;
  if (progress.source === 'mixed') return `第 ${attempt} 次：AI 修复与环境重试`;
  if (progress.source === 'diagnosis_only') return `第 ${attempt} 次：仅保留失败诊断`;
  if (progress.usesRepairDraft || progress.source === 'repair_draft') return `第 ${attempt} 次：AI 修复脚本验证`;
  if (progress.source === 'original_yaml') return `第 ${attempt} 次：原脚本证据重试`;
  return `第 ${attempt} 次：失败任务重跑`;
}

function renderRerunCycle(progress, items, cycleIndex, cycleCount) {
  const metrics = agentRerunCycleMetrics(progress, items);
  const cycleTitle = `<div class="agent-rerun-cycle-title">${escapeHtml(agentRerunCycleTitle(progress, cycleIndex))}<span>${metrics.completed}/${metrics.total || items.length} 已结束 · ${metrics.running} 执行中 · ${metrics.pending} 排队中</span></div>`;
  return `${cycleTitle}<div class="agent-rerun-list">${items.map((item, index) => {
    const meta = agentRerunStatusMeta(item.status);
    const changes = Array.isArray(item.repairChanges) ? item.repairChanges : [];
    const repairText = changes.length
      ? changes.map(change => typeof change === 'string' ? change : JSON.stringify(change)).join('；')
      : (item.repairSource === 'original_yaml' ? '未使用 AI 修复，按原 YAML 重跑' : (item.repairSource === 'diagnosis_only' ? 'AI 未产出可执行修复' : '使用 AI 修复草稿'));
    const strategyLabel = item.repairSource === 'repair_draft'
      ? 'AI 修复'
      : (item.repairSource === 'original_yaml' ? '原脚本重试' : (item.repairSource === 'diagnosis_only' ? '诊断处理' : '本轮策略'));
    const device = [item.runnerId || progress.runnerId, item.deviceId || progress.deviceId].filter(Boolean).join(' / ') || '设备信息待回传';
    const originalJob = item.sourceJobId || '未记录';
    const newJob = item.newJobId || (meta.key === 'skipped' ? '未创建' : '待创建');
    const reportLink = item.reportUrl
      ? `<a class="agent-rerun-report" href="${escapeHtml(item.reportUrl)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">查看 Runner 报告</a>`
      : '';
    return `<div class="agent-rerun-item status-${meta.key}">
      <div class="agent-rerun-item-head">
        <span class="agent-rerun-index">${String(index + 1).padStart(2, '0')}</span>
        <strong>${escapeHtml(item.targetTaskName || item.sourceFile || item.sourceJobId || '重跑任务')}</strong>
        <span class="agent-rerun-status status-${meta.key}">${escapeHtml(meta.label)}</span>
      </div>
      <div class="agent-rerun-job-chain"><span>原任务 ${escapeHtml(originalJob)}</span><b>→</b><span>重跑任务 ${escapeHtml(newJob)}</span></div>
      <div class="agent-rerun-evidence">
        <div><small>重跑触发</small><p>${escapeHtml(item.failureReason || '未记录失败原因')}</p></div>
        <div><small>${strategyLabel}</small><p>${escapeHtml(repairText)}</p>${item.repairFile ? `<code>修复文件：${escapeHtml(item.repairFile)}</code>` : ''}</div>
        <div><small>固定设备重跑</small><p>${escapeHtml(device)} · ${escapeHtml(meta.label)}</p>${item.resultReason ? `<p class="agent-rerun-result-reason">${escapeHtml(item.resultReason)}</p>` : ''}${reportLink}</div>
      </div>
    </div>`;
  }).join('')}</div>`;
}

function renderRerunDetail(step, artifacts) {
  const result = (artifacts || {}).rerunResult || {};
  const sources = (artifacts || {}).rerunSources || [];
  const skipped = (artifacts || {}).rerunSkippedJobs || [];
  const progress = (artifacts || {}).rerunProgress || (artifacts || {}).jobProgress || {};
  const progressHistory = Array.isArray((artifacts || {}).rerunProgressHistory) ? (artifacts || {}).rerunProgressHistory : [];
  const repairSummary = (artifacts || {}).repairSummary || {};
  const autonomy = (artifacts || {}).postRerunAutonomy || {};
  const sourceText = progress.source === 'mixed'
    ? 'AI 修复 + 环境原脚本重试'
    : (progress.source === 'diagnosis_only' ? '仅保留诊断' : (progress.usesRepairDraft ? '修复草稿' : (progress.source === 'original_yaml' ? '原始 YAML' : '未记录')));
  const currentItems = Array.isArray(progress.items) && progress.items.length
    ? progress.items
    : agentRerunFallbackItems(progress, sources, result, repairSummary);
  const cycles = progressHistory.concat([progress]).filter(item => item && typeof item === 'object');
  const cycleRows = cycles.map((cycle, index) => {
    const items = Array.isArray(cycle.items) && cycle.items.length
      ? cycle.items
      : (index === cycles.length - 1 ? currentItems : []);
    return {cycle, items, metrics: agentRerunCycleMetrics(cycle, items)};
  });
  const cumulative = cycleRows.reduce((sum, row) => ({
    total: sum.total + row.metrics.total,
    completed: sum.completed + row.metrics.completed,
    success: sum.success + row.metrics.success,
    failed: sum.failed + row.metrics.failed,
    timeout: sum.timeout + row.metrics.timeout,
    running: sum.running + row.metrics.running,
    pending: sum.pending + row.metrics.pending,
  }), {total: 0, completed: 0, success: 0, failed: 0, timeout: 0, running: 0, pending: 0});
  const percent = cumulative.total > 0 ? Math.max(0, Math.min(100, Math.round(cumulative.completed / cumulative.total * 100))) : 0;
  const fixedSerial = cycleRows.length > 0 && cycleRows.every(row => row.cycle.serialSameDevice);
  let html = '<div class="match-detail agent-readable-detail">';
  html += `<section class="agent-rerun-overview">
    <div class="agent-rerun-overview-head"><strong>${cycleRows.length > 1 ? '失败恢复执行链' : `${escapeHtml(sourceText)}重跑`}</strong><span>${cumulative.completed}/${cumulative.total || 0} 已结束</span></div>
    <div class="agent-rerun-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><span style="width:${percent}%"></span></div>
    <div class="agent-rerun-metrics">
      <span>重跑来源：${escapeHtml(sourceText)}</span>
      <span>尝试 ${cycleRows.length} 轮</span>
      <span class="text-success">成功 ${cumulative.success}</span>
      <span class="text-danger">失败 ${cumulative.failed}</span>
      <span class="text-warning">超时 ${cumulative.timeout}</span>
      <span>执行中 ${cumulative.running}</span>
      <span>排队中 ${cumulative.pending}</span>
      <span>${fixedSerial ? '固定设备串行' : '按执行策略下发'}</span>
      <span>${escapeHtml([progress.runnerId, progress.deviceId].filter(Boolean).join(' / ') || '设备待回传')}</span>
    </div>
  </section>`;
  cycleRows.forEach((row, index) => {
    if (row.items.length) html += renderRerunCycle(row.cycle, row.items, index, cycleRows.length);
  });
  if (!currentItems.length) {
    html += `<section class="agent-readable-panel"><strong>重跑任务</strong><p>尚未创建可执行重跑任务。</p></section>`;
  }
  if (skipped.length && !currentItems.some(item => String(item.status || '').toLowerCase() === 'skipped')) {
    html += agentReadableList('跳过的任务', skipped.slice(0, 15), item => `<b>${escapeHtml(item.taskName || item.jobId || '')}</b><span>${escapeHtml(item.status || '')}${item.reason ? ' · ' + escapeHtml(item.reason) : ''}</span>`);
  }
  if (autonomy.analyzed) {
    html += `<section class="agent-readable-panel"><strong>重跑后 AI 闭环</strong><p>${escapeHtml(autonomy.reason || '')}</p><p>最新归因：${escapeHtml(autonomy.failureType || 'UNKNOWN')} · 修复草稿：${autonomy.repairGenerated ? '已生成' : '未生成'} · 同设备验证：${autonomy.followupExecuted ? escapeHtml(autonomy.followupStatus || '已执行') : '未执行'}</p></section>`;
  }
  html += '</div>';
  return html;
}

function renderLearningDetail(step, artifacts) {
  const summary = (artifacts || {}).learningSummary || {};
  const calls = (step && step.toolCalls) || [];
  const call = calls.find(item => item && typeof item === 'object') || {};
  const callSummary = call.learningSummary || {};
  const merged = {...summary, ...callSummary};
  let html = '<div class="match-detail agent-readable-detail">';
  html += agentInfoGrid([
    { label: '匹配用例', value: merged.matchedCases ?? 0 },
    { label: 'YAML 引用', value: merged.yamlRefs ?? 0 },
    { label: '诊断结果', value: merged.hasDiagnosis ? '已沉淀' : '无' },
    { label: '执行结果', value: merged.hasJobResult ? '已沉淀' : '无' },
  ]);
  html += `<section class="agent-readable-panel"><strong>沉淀内容</strong><p>写入 Agent 历史学习库，用于后续检索相似目标、参考历史用例、复用失败诊断和执行结果。不会覆盖当前 YAML。</p></section>`;
  const learningItems = [];
  if (merged.matchedCases) learningItems.push(`相似用例：${merged.matchedCases} 条`);
  if (merged.yamlRefs) learningItems.push(`YAML 写法引用：${merged.yamlRefs} 条`);
  if (merged.hasDiagnosis) learningItems.push('失败诊断：已记录失败类型、原因和建议动作');
  if (merged.hasJobResult) learningItems.push('执行结果：已记录成功/失败/超时统计和报告链接');
  const failureReasons = (artifacts || {}).jobFailureReasons || (artifacts || {}).failureReasons || [];
  if (Array.isArray(failureReasons) && failureReasons.length) {
    learningItems.push(`失败原因样本：${failureReasons.length} 条`);
  }
  html += agentReadableList('学习明细', learningItems);
  html += '</div>';
  return html;
}

// ===== 匹配原因详情 =====
function renderMatchDetail(step, artifacts) {
  const reason = (artifacts || {}).matchReason || '';
  const count = (artifacts || {}).matchedCount || 0;
  const skipped = (artifacts || {}).skippedCases || [];
  const impact = (artifacts || {}).impactAnalysis || {};
  const retrieval = (artifacts || {}).caseRetrieval || {};
  const candidates = retrieval.candidates || [];
  const candidateDetails = retrieval.candidateDetails || [];
  const detailByPath = new Map(candidateDetails.map(item => [item.rel_path || '', item]));
  const keywords = retrieval.matchedKeywords || retrieval.keywords || impact.keywords || [];
  let html = '<div class="match-detail agent-readable-detail">';
  html += agentInfoGrid([
    { label: '匹配数量', value: `${count} 个` },
    { label: '推荐动作', value: retrieval.decision || '-' },
    { label: '置信度', value: retrieval.confidence ?? '-' },
    { label: '来源', value: retrieval.confidenceSource || retrieval.aiSource || '规则/AI' },
  ]);
  if (reason) html += `<section class="agent-readable-panel"><strong>匹配策略</strong><p>${escapeHtml(reason)}</p></section>`;
  if (keywords.length > 0) {
    html += `<div class="match-keywords"><strong>匹配关键词：</strong>${keywords.slice(0, 10).map(kw => `<span class="tag">${escapeHtml(kw)}</span>`).join(' ')}</div>`;
  }
  if (candidates.length > 0) {
    html += agentReadableList('候选用例', candidates.slice(0, 5), c => {
      const detail = detailByPath.get(c.rel_path || '') || c;
      const reasons = detail.reasons || [];
      const reasonText = reasons.length ? `｜${reasons.slice(0, 3).join('；')}` : '';
      return `<b>${escapeHtml(c.dir_name || '')}/${escapeHtml(c.file_name || '')}</b><span>${escapeHtml(String(c.confidence ?? '-'))}${escapeHtml(reasonText)}</span>`;
    });
  }
  if (skipped.length > 0) {
    html += `<section class="agent-readable-panel"><strong>跳过项</strong><p>跳过 ${escapeHtml(skipped.length)} 个：${escapeHtml(skipped.slice(0, 3).join(', '))}</p></section>`;
  }
  html += '</div>';
  return html;
}

function renderExecutionPrecheckDetail(step, artifacts) {
  const precheck = (artifacts || {}).executionPrecheck || {};
  const firstCall = step && step.toolCalls && step.toolCalls[0] ? step.toolCalls[0] : {};
  const checks = precheck.checks || firstCall.checks || [];
  const blockers = precheck.blockers || firstCall.blockers || [];
  const warnings = precheck.warnings || firstCall.warnings || [];
  const riskDetail = precheck.riskReview || (artifacts || {}).riskReview || firstCall.riskDetail || {};
  const yamlDryRun = (artifacts || {}).yamlDryRun || {};
  const runnerDryRun = (artifacts || {}).runnerDryRun || {};
  let html = '<div class="match-detail agent-readable-detail">';
  html += agentInfoGrid([
    { label: '体检项', value: checks.length },
    { label: '阻断', value: blockers.length },
    { label: '提醒', value: warnings.length },
    { label: 'YAML dry-run', value: yamlDryRun.checked != null ? `${yamlDryRun.passed || 0}/${yamlDryRun.checked || 0}` : '-' },
    { label: 'Runner dry-run', value: runnerDryRun.checked != null ? `${(runnerDryRun.checked || 0) - (runnerDryRun.blockedCount || 0)}/${runnerDryRun.checked || 0}` : '-' },
  ]);
  if (yamlDryRun && Array.isArray(yamlDryRun.results) && yamlDryRun.results.length) {
    html += agentReadableList('YAML dry-run 结果', yamlDryRun.results.slice(0, 12), item => (
      `<b>${escapeHtml(item.file || item.path || '')}</b><span>${item.ok ? '通过' : '未通过'}${item.errors ? ' · ' + escapeHtml((item.errors || []).join('；')) : ''}</span>`
    ));
  }
  if (riskDetail.keyword && typeof agentRiskDetailHtml === 'function') {
    html += agentRiskDetailHtml(riskDetail);
  }
  if (blockers.length) {
    html += agentReadableList('阻断项', blockers, c => `<b>${escapeHtml(c.name || '')}</b><span>${escapeHtml(c.detail || '')}</span>`);
  }
  if (warnings.length) {
    html += agentReadableList('提醒项', warnings, c => `<b>${escapeHtml(c.name || '')}</b><span>${escapeHtml(c.detail || '')}</span>`);
  }
  if (checks.length) {
    html += agentReadableList('体检项', checks, c => {
      const mark = c.ok ? '✓' : (c.severity === 'warning' ? '!' : '✕');
      return `<b>${escapeHtml(mark)} ${escapeHtml(c.name || '')}</b><span>${escapeHtml(c.detail || '')}</span>`;
    });
  }
  html += renderDiagnosisDetail(precheck.diagnosis || step.diagnosis || firstCall.diagnosis);
  html += '</div>';
  return html;
}

function renderSourceContextDetail(step, artifacts) {
  const source = (artifacts || {}).sourceContext || {};
  const impact = (artifacts || {}).impactAnalysis || {};
  const files = source.uploadedFiles || [];
  const images = source.uploadedImages || [];
  const keywords = source.keywords || impact.keywords || [];
  let html = '<div class="match-detail agent-readable-detail">';
  html += agentInfoGrid([
    { label: '关键词', value: keywords.length },
    { label: '上传资料', value: files.length },
    { label: '截图', value: images.length },
    { label: 'Figma', value: source.figmaUrl ? '已提供' : '无' },
  ]);
  html += `<section class="agent-readable-panel"><strong>输入摘要</strong><p>${escapeHtml(source.sourceSummary || impact.sourceSummary || '未上传额外资料')}</p></section>`;
  html += renderVisualReferenceReport((artifacts || {}).visualReferenceReport || source.visualReferenceReport);
  if (source.figmaUrl) {
    const usedPages = source.figmaUsedPages || source.uiDesigns || [];
    const ignoredPages = source.figmaIgnoredPages || [];
    const previewItems = agentFigmaPreviewItems(source, artifacts || {});
    const extractState = source.figmaExtracted ? '已按需求提取页面' : (source.figmaExtractError ? '提取失败/降级为链接参考' : '待提取');
    html += `<section class="agent-readable-panel"><strong>Figma</strong><p>${escapeHtml(source.figmaUrl)}</p><p>${escapeHtml(extractState)} · 使用 ${usedPages.length} 页 · 忽略 ${ignoredPages.length} 页 · 图片 ${Number(source.figmaImageCount || 0)} 张</p></section>`;
    if (source.figmaExtractError) {
      html += agentReadableList('Figma 提醒', [source.figmaExtractError]);
    }
    html += renderFigmaPreviewGrid(previewItems);
    if (usedPages.length) {
      html += agentReadableList('解析采用的 Figma 页面', usedPages.slice(0, 8), page => {
        const figma = page.figma || {};
        const imageName = page.screenshot || page.image_name ? ` · 图片 ${page.screenshot || page.image_name}` : '';
        return `<b>${escapeHtml(page.page_name || page.pageName || figma.page_name || 'Figma 页面')}</b><span>分数 ${escapeHtml(String(page.relevance_score ?? figma.relevance_score ?? ''))}${escapeHtml(imageName)} · ${escapeHtml(page.relevance_reason || figma.relevance_reason || '')}</span>`;
      });
    }
    if (ignoredPages.length) {
      html += agentReadableList('忽略的 Figma 页面', ignoredPages.slice(0, 6), page => {
        const figma = page.figma || {};
        return `<b>${escapeHtml(page.page_name || page.pageName || figma.page_name || 'Figma 页面')}</b><span>分数 ${escapeHtml(String(figma.relevance_score ?? page.relevance_score ?? ''))} · ${escapeHtml(figma.relevance_reason || page.relevance_reason || '低匹配，未进入本次参考')}</span>`;
      });
    }
  }
  if (keywords.length) {
    html += `<div class="match-keywords"><strong>提取关键词：</strong>${keywords.slice(0, 12).map(kw => `<span class="tag">${escapeHtml(kw)}</span>`).join(' ')}</div>`;
  }
  if (files.length) {
    html += agentReadableList('上传资料', files.slice(0, 8), file => {
      const kind = file.kind === 'screenshot' ? '截图' : (file.kind === 'requirement_text' ? '文本' : '文件');
      return `<b>${escapeHtml(file.name || '未命名资料')}</b><span>${escapeHtml(kind)}${file.hasText ? ' · 已提取文本' : ''}${file.skippedContent ? ' · 仅保留元信息' : ''}</span>`;
    });
  }
  if (images.length && !files.some(item => item.kind === 'screenshot')) {
    html += `<section class="agent-readable-panel"><strong>截图</strong><p>${escapeHtml(images.length)} 张已进入输入资料</p></section>`;
  }
  html += '</div>';
  return html;
}

function renderPlanDetail(step, artifacts) {
  const plan = (artifacts || {}).plan || {};
  const flows = Array.isArray(plan.businessFlows) ? plan.businessFlows : [];
  const lifecycle = Array.isArray(plan.platformLifecycle) ? plan.platformLifecycle : [];
  if (!flows.length && !plan.objective && plan.status !== 'failed') return '';
  const sourceLabel = plan.aiGenerated
    ? `平台 MM AI${plan.model ? ` · ${plan.model}` : ''}`
    : (plan.status === 'failed' ? 'AI 规划失败' : '未验证需求候选');
  const mindmapTrace = plan.mindmapTrace || {};
  const visualReference = plan.visualReference || {};
  let html = '<div class="agent-readable-stack">';
  html += agentInfoGrid([
    { label: '计划来源', value: sourceLabel },
    { label: '业务分支', value: flows.length },
    { label: '覆盖门禁', value: plan.status === 'failed' || plan.qualityGate?.passed === false ? '未通过' : '通过' },
    { label: 'MM Skills', value: mindmapTrace.skillPipeline || '-' },
    { label: 'Figma 软参考', value: `${Number(visualReference.figmaPageCount || 0)} 页 / ${Number(visualReference.figmaImageCount || 0)} 图` },
    { label: '视觉送 AI', value: visualReference.aiJudgementCompleted ? '已完成' : (visualReference.sentToAiForJudgement ? '已送入 / 未完成' : '未送入') },
  ]);
  if (plan.objective) {
    html += `<section class="agent-readable-panel"><strong>验收目标</strong><p>${escapeHtml(plan.objective)}</p></section>`;
  }
  if (Array.isArray(plan.issues) && plan.issues.length) {
    html += `<section class="agent-readable-panel"><strong>规划失败原因</strong><p>${plan.issues.map(item => escapeHtml(item)).join('<br>')}</p></section>`;
  }
  for (const flow of flows) {
    const steps = Array.isArray(flow.steps) ? flow.steps : [];
    const checks = Array.isArray(flow.checks) ? flow.checks : [];
    html += `<section class="agent-readable-panel">
      <strong>${escapeHtml(flow.name || flow.branch || flow.id || '业务分支')}</strong>
      ${steps.length ? `<p>${steps.map((item, index) => `${index + 1}. ${escapeHtml(item)}`).join('<br>')}</p>` : ''}
      ${checks.length ? `<div class="failure-title">可见验收点</div><p>${checks.map(item => escapeHtml(item)).join('<br>')}</p>` : ''}
    </section>`;
  }
  if (Array.isArray(plan.unknowns) && plan.unknowns.length) {
    html += `<section class="agent-readable-panel"><strong>待证据消解</strong><p>${plan.unknowns.map(item => escapeHtml(item)).join('<br>')}</p></section>`;
  }
  if (plan.fallbackReason) {
    html += `<section class="agent-readable-panel"><strong>兜底原因</strong><p>${escapeHtml(plan.fallbackReason)}</p></section>`;
  }
  if (lifecycle.length) {
    html += `<details class="agent-readable-panel"><summary>平台执行与门禁</summary><p>${lifecycle.map((item, index) => `${index + 1}. ${escapeHtml(item)}`).join('<br>')}</p></details>`;
  }
  html += '</div>';
  return html;
}

// ===== Step 详情分发函数 =====
function renderStepDetail(step, run) {
  const toolName = (step.toolCalls && step.toolCalls[0] && step.toolCalls[0].toolName) || step.toolName || '';
  const stepName = String(step.step || step.state || '').toUpperCase();
  const artifacts = (run && run.artifacts) || {};
  if (stepName === 'RERUN') return renderRerunDetail(step, artifacts);
  switch (toolName) {
    case 'analyze_goal': return renderPlanDetail(step, artifacts);
    case 'prepare_source': return renderSourceContextDetail(step, artifacts);
    case 'impact_analysis': return renderSourceContextDetail(step, artifacts);
    case 'case_retrieval': return renderMatchDetail(step, artifacts);
    case 'list_cases': return renderMatchDetail(step, artifacts);
    case 'generate_yaml': return renderGenerateYamlDetail(step, artifacts);
    case 'validate_yaml': return renderValidateYamlDetail(step, artifacts);
    case 'execution_precheck': return renderExecutionPrecheckDetail(step, artifacts);
    case 'sonic_sync_case': return renderSonicSyncDetail(step, artifacts);
    case 'create_runner_job': return renderRunTaskDetail(step, artifacts);
    case 'read_report': return renderReportDetail(step, artifacts);
    case 'analyze_failure': return renderAnalysisDetail(step, artifacts);
    case 'diagnose_failure': return renderDiagnosisDetail((artifacts || {}).diagnosis || step.diagnosis);
    case 'generate_repair_draft': return renderRepairDraftDetail(step, artifacts);
    case 'retry_failed_job': return renderRerunDetail(step, artifacts);
    case 'learn_from_result': return renderLearningDetail(step, artifacts);
    default: return '';
  }
}

function renderAgentTimeline(run) {
  if (!run) {
    return renderEmptyState('agent_history');
  }
  const runStatus = String(run.status || '').toUpperCase();
  const runDone = ['DONE', 'FINISH'].includes(runStatus);
  const runTerminal = runDone || ['FAILED', 'CANCELLED'].includes(runStatus);
  const items = AGENT_TIMELINE_STEPS.map(([key, label], idx) => {
    const data = timelineStepData(key, run) || {};
    let status = resolvedTimelineStatus(key, run, data);

    // NOT_IMPLEMENTED / SKIPPED steps show grey badge
    const rawStatus = String(data.status || data.state || '').toUpperCase();
    const isNotImplemented = rawStatus === 'NOT_IMPLEMENTED';
    const isSkipped = rawStatus === 'SKIPPED' || status === 'skipped';
    if (isNotImplemented) status = 'skipped';

    const meta = TIMELINE_STATUS_META[status] || TIMELINE_STATUS_META.pending;
    let summary = data.summary || data.message || '';
    if (key === 'DONE' && runTerminal && !summary) {
      if (runDone) {
        summary = 'Agent 流程已完成';
      } else if (runStatus === 'CANCELLED') {
        summary = '任务已取消，未进入完成态';
      } else {
        summary = '前序步骤失败，Agent 流程未进入完成态';
      }
    }
    let errorText = (status === 'failed' && (data.error || data.errorMessage || data.failureReason))
      ? (data.error || data.errorMessage || data.failureReason) : '';
    if (key === 'DONE' && runStatus === 'FAILED') {
      errorText = errorText || run.error || run.errorMessage || run.message || '前序步骤失败';
    }
    const dur = timelineDurationText(data);
    const artifacts = timelineArtifactLinks(data);
    const toolChips = timelineToolCallChips(data);
    const toolCallsDetail = timelineToolCallsDetail(data);
    const liveTraceDetail = timelineLiveTraceDetail(data);
    const technicalTraceDetail = (liveTraceDetail || toolCallsDetail)
      ? `<details class="agent-technical-trace" onclick="event.stopPropagation()"><summary>技术日志<span>${liveTraceDetail ? '执行轨迹' : ''}${liveTraceDetail && toolCallsDetail ? ' · ' : ''}${toolCallsDetail ? '工具调用' : ''}</span></summary>${liveTraceDetail || ''}${toolCallsDetail || ''}</details>`
      : '';
    const stepDetailHtml = renderStepDetail(data, run);
    const diagnosisHtml = renderDiagnosisDetail(data.diagnosis);
    const isActive = (status === 'running' || status === 'waiting');

    // Mock warning: if summary contains "模拟"
    const isMock = /模拟/.test(summary);
    // Not-implemented badge
    const notImplBadge = isNotImplemented
      ? '<span class="step-badge-skipped">待接入</span>' : '';
    const skippedBadge = isSkipped && !isNotImplemented
      ? '<span class="step-badge-skipped">已跳过</span>' : '';
    const mockWarning = isMock
      ? '<span class="step-mock-warning">⚠ 该步骤为演示数据，未调用真实服务</span>' : '';

    const isExpanded = expandedStepIndexes.has(idx);
    return `
      <li class="agent-timeline-step status-${status}${isActive ? ' is-active' : ''}${isExpanded ? ' step-expanded' : ''}" onclick="toggleStepExpanded(this, ${idx})">
        <div class="agent-timeline-marker">
          <span class="agent-timeline-icon">${meta.icon}</span>
          <span class="agent-timeline-rail" aria-hidden="true"></span>
        </div>
        <div class="agent-timeline-body">
          <div class="agent-timeline-row">
            <span class="agent-timeline-index">${String(idx + 1).padStart(2, '0')}</span>
            <strong class="agent-timeline-name">${escapeHtml(label)}</strong>
            <span class="agent-timeline-status">${escapeHtml(meta.label)}</span>
            ${notImplBadge}${skippedBadge}
            ${dur ? `<span class="agent-timeline-duration">${escapeHtml(dur)}</span>` : ''}
          </div>
          ${summary ? `<div class="agent-timeline-summary">${escapeHtml(String(summary).slice(0, 240))}</div>` : ''}
          ${mockWarning}
          ${errorText ? `<div class="agent-timeline-error">${escapeHtml(String(errorText).slice(0, 320))}</div>` : ''}
          ${diagnosisHtml}
          ${toolChips}
          ${artifacts ? `<div class="agent-timeline-artifacts">${artifacts}</div>` : ''}
          ${stepDetailHtml ? `<div class="step-tool-calls-wrap">${stepDetailHtml}</div>` : ''}
          ${technicalTraceDetail ? `<div class="step-tool-calls-wrap">${technicalTraceDetail}</div>` : ''}
        </div>
      </li>
    `;
  }).join('');
  const settledCount = AGENT_TIMELINE_STEPS.filter(([key]) => (
    ['success', 'failed', 'partial', 'skipped'].includes(resolvedTimelineStatus(key, run))
  )).length;
  const currentLabel = agentStepLabel(run.currentStep || '');
  return `
    ${renderAgentPhaseOverview(run)}
    <details class="agent-checkpoint-trace" ${agentCheckpointTraceOpen ? 'open' : ''} ontoggle="agentCheckpointTraceOpen=this.open">
      <summary>
        <span>内部执行轨迹</span>
        <small>${settledCount}/${AGENT_TIMELINE_STEPS.length} 个检查点已结束${currentLabel && !runTerminal ? `，当前 ${escapeHtml(currentLabel)}` : ''}</small>
      </summary>
      <ol class="agent-timeline-list">${items}</ol>
    </details>`;
}

function renderAgentStepsPlan(run) {
  return renderAgentPhaseOverview(run);
}

function toggleFailedJobField() {
  const scope = document.getElementById('agent-scope')?.value;
  const field = document.getElementById('agent-failed-job-field');
  if (field) field.style.display = scope === 'failed_rerun' ? '' : 'none';
}

const AGENT_SOURCE_ALLOWED_RE = /\.(txt|md|json|pdf|docx?|mm|ya?ml|png|jpe?g)$/i;
const AGENT_SOURCE_MAX_FILES = 10;
const AGENT_SOURCE_MAX_BYTES = 4 * 1024 * 1024;
const AGENT_SOURCE_TOTAL_MAX_BYTES = 12 * 1024 * 1024;
const AGENT_SOURCE_TEXT_LIMIT = 900 * 1024;

function formatAgentBytes(size) {
  if (typeof formatBytes === 'function') return formatBytes(size || 0);
  const num = Number(size || 0);
  if (num >= 1024 * 1024) return `${(num / 1024 / 1024).toFixed(1)} MB`;
  if (num >= 1024) return `${Math.round(num / 1024)} KB`;
  return `${num} B`;
}

function agentSourceKind(file) {
  const name = String(file?.name || '');
  const type = String(file?.type || '');
  if (/^image\//.test(type) || /\.(png|jpe?g)$/i.test(name)) return 'screenshot';
  if (/\.(txt|md|json|mm|ya?ml)$/i.test(name)) return 'requirement_text';
  return 'requirement_file';
}

function agentSourceKindLabel(kind) {
  return {
    screenshot: '截图',
    requirement_text: '需求文本',
    requirement_file: '需求文件'
  }[kind] || '资料';
}

function agentSourceFileSummary() {
  const total = agentSourceFiles.reduce((sum, item) => sum + Number(item.size || 0), 0);
  const screenshots = agentSourceFiles.filter(item => item.kind === 'screenshot').length;
  const docs = agentSourceFiles.length - screenshots;
  if (!agentSourceFiles.length) return '未添加资料';
  return `${agentSourceFiles.length} 个文件 · 文档 ${docs} · 截图 ${screenshots} · ${formatAgentBytes(total)}`;
}

function renderAgentSourceFileList() {
  const counter = document.getElementById('agent-source-counter');
  if (counter) counter.textContent = agentSourceFileSummary();
  const list = document.getElementById('agent-source-file-list');
  if (!list) return;
  if (!agentSourceFiles.length) {
    list.innerHTML = '<div class="agent-source-empty">尚未添加文件；Agent 会先根据测试目标和页面知识检索用例。</div>';
    return;
  }
  list.innerHTML = agentSourceFiles.map((file, index) => `
    <div class="asset-row agent-source-file-row">
      <span class="asset-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
      <span class="asset-meta">${escapeHtml(agentSourceKindLabel(file.kind))} · ${escapeHtml(formatAgentBytes(file.size || 0))}</span>
      <button class="asset-remove" type="button" onclick="removeAgentSourceFile(${index})" title="移除">×</button>
    </div>
  `).join('');
}

function removeAgentSourceFile(index) {
  agentSourceFiles.splice(index, 1);
  renderAgentSourceFileList();
}

async function readAgentSourceFile(file) {
  const kind = agentSourceKind(file);
  const base = {
    name: file.name || `agent-source-${Date.now()}`,
    size: file.size || 0,
    type: file.type || file.name.split('.').pop().toLowerCase(),
    kind,
    source: 'agent-workbench'
  };
  if (file.size > AGENT_SOURCE_MAX_BYTES) {
    return {
      ...base,
      skippedContent: true,
      note: `文件超过 ${formatAgentBytes(AGENT_SOURCE_MAX_BYTES)}，仅保留文件名和大小`
    };
  }
  if (kind === 'requirement_text' && file.size <= AGENT_SOURCE_TEXT_LIMIT) {
    return {
      ...base,
      content: await file.text()
    };
  }
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
  return {
    ...base,
    contentBase64: dataUrl.split(',')[1] || ''
  };
}

async function addAgentSourceFiles(files, source='选择') {
  const selected = Array.from(files || []).filter(Boolean);
  if (!selected.length) return;
  const invalid = selected.find(file => !AGENT_SOURCE_ALLOWED_RE.test(file.name || ''));
  if (invalid) {
    showToast(`不支持的 Agent 资料类型：${invalid.name}`, 'error');
    return;
  }
  const freeSlots = Math.max(0, AGENT_SOURCE_MAX_FILES - agentSourceFiles.length);
  if (!freeSlots) {
    showToast(`Agent 最多保留 ${AGENT_SOURCE_MAX_FILES} 个输入文件`, 'warn');
    return;
  }
  const currentTotal = agentSourceFiles.reduce((sum, item) => sum + Number(item.size || 0), 0);
  let plannedTotal = currentTotal;
  const targetFiles = [];
  for (const file of selected.slice(0, freeSlots)) {
    const size = Number(file.size || 0);
    if (plannedTotal + size > AGENT_SOURCE_TOTAL_MAX_BYTES) continue;
    targetFiles.push(file);
    plannedTotal += size;
  }
  if (!targetFiles.length) {
    showToast(`Agent 输入资料总大小不能超过 ${formatAgentBytes(AGENT_SOURCE_TOTAL_MAX_BYTES)}`, 'warn');
    return;
  }
  try {
    for (const file of targetFiles) {
      const asset = await readAgentSourceFile(file);
      const exists = agentSourceFiles.findIndex(item => item.name === asset.name);
      if (exists >= 0) agentSourceFiles[exists] = asset;
      else agentSourceFiles.push(asset);
    }
  } catch(e) {
    showToast(e.message || 'Agent 输入资料读取失败', 'error');
    return;
  }
  renderAgentSourceFileList();
  const suffix = selected.length > targetFiles.length ? `，已按数量/大小上限保留 ${targetFiles.length} 个` : '';
  showToast(`已${source} ${targetFiles.length} 个 Agent 输入资料${suffix}`, 'success');
}

async function handleAgentSourceFiles(input) {
  await addAgentSourceFiles(input.files || [], '选择');
  input.value = '';
}

function handleAgentSourceDragOver(event) {
  event.preventDefault();
  event.currentTarget?.classList.add('paste-active');
}

function handleAgentSourceDragLeave(event) {
  event.currentTarget?.classList.remove('paste-active');
}

async function handleAgentSourceDrop(event) {
  event.preventDefault();
  event.currentTarget?.classList.remove('paste-active');
  await addAgentSourceFiles(event.dataTransfer?.files || [], '拖入');
}

async function handleAgentSourcePaste(event) {
  if (typeof isEditablePasteTarget === 'function' && isEditablePasteTarget(event.target)) return false;
  const items = Array.from(event.clipboardData?.items || []);
  const pastedFiles = [];
  for (let i = 0; i < items.length; i++) {
    if (items[i].kind === 'file' && typeof fileFromClipboardItem === 'function') {
      const file = await fileFromClipboardItem(items[i], i);
      if (file) pastedFiles.push(file);
    }
  }
  if (pastedFiles.length) {
    event.preventDefault();
    await addAgentSourceFiles(pastedFiles, '粘贴');
    return true;
  }
  const text = event.clipboardData?.getData('text/plain') || '';
  if (text.trim()) {
    event.preventDefault();
    const file = new File([text], `agent-requirement-${Date.now()}.txt`, { type: 'text/plain' });
    await addAgentSourceFiles([file], '粘贴');
    return true;
  }
  return false;
}

function renderAgentSourcePanel() {
  const panel = document.getElementById('agent-source-panel');
  const sourceType = document.getElementById('agent-source-type')?.value || 'manual';
  if (!panel) return;
  const existing = {};
  ['agent-source-generate-job-id', 'agent-source-case-set-id', 'agent-source-failed-job-id'].forEach(id => {
    const el = document.getElementById(id);
    if (el) existing[id] = el.value;
  });
  const input = (id, label, placeholder) => `
    <div class="agent-field">
      <label for="${id}">${label}</label>
      <input id="${id}" value="${escapeHtml(existing[id] || '')}" placeholder="${escapeHtml(placeholder)}">
    </div>`;
  if (sourceType === 'requirement') {
    panel.innerHTML = `
      <div class="agent-source-grid">
        ${input('agent-source-generate-job-id', '生成记录 Job ID', '可选：例如 gen_1780...；留空则只用当前目标')}
        ${input('agent-source-case-set-id', '用例批次 ID', '可选：例如 cs_1780...')}
      </div>
      <p class="form-hint">Agent 会读取该批次的需求、补充资料、已保存 UI 稿和生成摘要，再决定匹配旧用例还是生成草稿。</p>`;
  } else if (sourceType === 'figma') {
    panel.innerHTML = `
      <div class="agent-source-grid">
        ${input('agent-source-case-set-id', '关联用例批次 ID', '可选：读取本批次保存的 UI 设计稿')}
      </div>
      <p class="form-hint">Figma 链接在下方“本次 Agent输入资料”里填写；Agent 会结合设计稿、截图和需求文档做影响分析。</p>`;
  } else if (sourceType === 'failed_job') {
    const failedJobValue = existing['agent-source-failed-job-id'] || document.getElementById('agent-failed-job')?.value || '';
    panel.innerHTML = `
      <div class="agent-source-grid">
        <div class="agent-field">
          <label for="agent-source-failed-job-id">失败任务 Job ID</label>
          <input id="agent-source-failed-job-id" value="${escapeHtml(failedJobValue)}" placeholder="只分析和处理这个失败任务">
        </div>
      </div>
      <p class="form-hint">失败任务来源会精确锁定对应 YAML，不会扩展到整个模块或测试套。</p>`;
  } else {
    panel.innerHTML = `<p class="form-hint">直接输入目标适合临时调试。目标不明确时Agent 会停在人工确认，不会自动扩大到全部用例。</p>`;
  }
  renderAgentSourceFileList();
}

function collectAgentSourceRefs() {
  let sourceType = document.getElementById('agent-source-type')?.value || 'manual';
  const refs = {};
  const put = (key, id) => {
    const value = document.getElementById(id)?.value?.trim();
    if (value) refs[key] = value;
  };
  put('generateJobId', 'agent-source-generate-job-id');
  put('caseSetId', 'agent-source-case-set-id');
  put('figmaUrl', 'agent-source-figma-url');
  put('failedJobId', 'agent-source-failed-job-id');
  if (sourceType === 'failed_job' && !refs.failedJobId) {
    const selected = document.getElementById('agent-failed-job')?.value?.trim();
    if (selected) refs.failedJobId = selected;
  }
  if (sourceType === 'manual') {
    const hasFiles = agentSourceFiles.some(item => item.kind !== 'screenshot');
    if (hasFiles) sourceType = 'requirement';
    else if (refs.figmaUrl) sourceType = 'figma';
  }
  return { sourceType, sourceRefs: refs };
}

function collectAgentSourceMaterials() {
  const figmaUrl = document.getElementById('agent-source-figma-url')?.value?.trim() || '';
  const requirementText = document.getElementById('agent-source-requirement-text')?.value?.trim() || '';
  const files = agentSourceFiles.map(item => ({...item}));
  const images = files.filter(item => item.kind === 'screenshot');
  const requirementFiles = files.filter(item => item.kind !== 'screenshot');
  return {
    figmaUrl,
    requirementText,
    files,
    images,
    requirementFiles,
    summary: {
      fileCount: files.length,
      imageCount: images.length,
      requirementFileCount: requirementFiles.length
    }
  };
}

function setAgentTab(tab) {
  agentActiveTab = tab;
  const run = currentAgentRun();
  const card = document.getElementById('agent-artifacts-card');
  if (card && run) {
    card.innerHTML = renderAgentArtifactPanel(run);
    return;
  }
  showAgentWorkbench();
}

function agentPayloadFromForm(options={}) {
  let goal = document.getElementById('agent-goal')?.value.trim() || '';
  const appName = document.getElementById('agent-app-name')?.value.trim() || DEFAULT_AGENT_APP_NAME;
  const platform = document.getElementById('agent-platform')?.value || 'android';
  const scope = document.getElementById('agent-scope')?.value || 'auto';
  const modelInfo = selectedAgentModelInfo();
  const model = modelInfo.kind === 'task-model' ? modelInfo.model : '';
  const sourceMaterials = collectAgentSourceMaterials();
  if (!goal && sourceMaterials.requirementText) {
    goal = sourceMaterials.requirementText.slice(0, 120);
  } else if (!goal && sourceMaterials.files.length) {
    goal = `根据 ${sourceMaterials.files[0].name} 分析自动化测试`;
  } else if (!goal && sourceMaterials.figmaUrl) {
    goal = '基于 Figma 设计稿分析自动化测试';
  }
  const selectedMode = document.querySelector('input[name="agent-mode"]:checked')?.value || 'AUTO_SAFE';
  const analyzeOnly = selectedMode === 'ANALYZE_ONLY';
  const mode = analyzeOnly ? 'AUTO_SAFE' : (options.yamlOnly ? 'SEMI_AUTO' : selectedMode);
  const riskHits = agentRiskHits(goal);
  const platformRisk = riskHits.some(hit => ['覆盖基线', '批量同步', '批量执行'].includes(hit));
  const autoRunEnabled = !options.yamlOnly && !!document.getElementById('agent-policy-runSonic')?.checked && !platformRisk;
  const autoRepairEnabled = !options.yamlOnly && !!document.getElementById('agent-policy-autoRepair')?.checked && !platformRisk;
  const source = collectAgentSourceRefs();
  if (sourceMaterials.figmaUrl && !source.sourceRefs.figmaUrl) {
    source.sourceRefs.figmaUrl = sourceMaterials.figmaUrl;
  }
  const runnerSelection = selectedRunnerDevice('agent-runner-device');
  const appPackage = selectedAgentAppPackage();
  return {
    mode,
    goal,
    target: goal,
    requirement: sourceMaterials.requirementText || goal,
    requirementText: sourceMaterials.requirementText,
    figmaUrl: sourceMaterials.figmaUrl,
    files: sourceMaterials.files,
    images: sourceMaterials.images,
    sourceInputs: sourceMaterials,
    appName,
    appPackage,
    app_package: appPackage,
    platform,
    scope,
    executionMode: 'RUNNER_JOB',
    runnerId: runnerSelection.runner_id,
    deviceId: runnerSelection.device_id,
    deviceStrategy: runnerSelection.device_strategy,
    model,
    modelProviderId: modelInfo.providerId || '',
    aiProviderId: modelInfo.providerId || '',
    aiModel: modelInfo.model || '',
    sourceType: source.sourceType,
    sourceRefs: source.sourceRefs,
    failedJobId: source.sourceRefs.failedJobId || document.getElementById('agent-failed-job')?.value?.trim() || '',
    testCase: goal,
    autoRun: autoRunEnabled,
    autoRepair: autoRepairEnabled,
    autoCreateBug: mode === 'FULL_AUTO' && !!document.getElementById('agent-policy-bugDraft')?.checked && !platformRisk,
    autoOverwriteBaseline: false,
    maxRetries: 2,
    requireConfirmBeforePrint: riskHits.includes('确认打印') || riskHits.includes('开始打印'),
    riskHits,
    strategy: {
      generateCase: !!document.getElementById('agent-policy-generateCase')?.checked,
      generateYaml: true,
      validateYaml: !!document.getElementById('agent-policy-validateYaml')?.checked,
      runSonic: autoRunEnabled,
      safeRerun: !!document.getElementById('agent-policy-safeRerun')?.checked && !platformRisk,
      bugDraftOnly: !!document.getElementById('agent-policy-bugDraft')?.checked,
      yamlOnly: Boolean(options.yamlOnly)
    }
  };
}

async function previewAgentPlan() {
  const payload = agentPayloadFromForm({preview: true});
  const previewBtn = document.querySelector('.agent-actions .btn-sm:not(.primary):not([onclick*="agent_history"])');
  await LoadingManager.withLoading(async () => {
    try {
      const data = await apiRequest('/agent-runs/preview', {
        method: 'POST',
        body: payload
      });
      const plan = data.plan || data;
      const hits = agentRiskHits(payload.goal);
      const runnerLine = payload.deviceStrategy === 'fixed'
        ? `执行设备：${payload.deviceId || '未指定设备'} / ${payload.runnerId || '任意 Runner'}`
        : (payload.deviceStrategy === 'auto'
          ? `执行设备：自动选择在线设备（当前 ${runnerDevices.length} 台在线）`
          : '执行设备：暂无在线设备，执行前体检会阻断');
      const candidateLines = (plan.requirementCandidates || []).map((candidate, index) =>
        `${index + 1}. ${candidate.branch || candidate.name || candidate.id || '需求候选'}`
      );
      const platformLines = (plan.platformLifecycle || []).map((item, index) => `${index + 1}. ${item}`);
      const lines = [
        'Agent 启动前预览：',
        `模式：${agentModeText(plan.mode || payload.mode)}`,
        `应用：${plan.appName || payload.appName} / ${plan.platform || payload.platform}`,
        `范围：${plan.scope || payload.scope}`,
        runnerLine,
        `输入来源：${payload.sourceType || 'manual'}`,
        `输入资料：Figma ${payload.figmaUrl ? '1' : '0'} 个，文件 ${payload.files?.length || 0} 个，截图 ${payload.images?.length || 0} 张`,
        `风险：${hits.length ? hits.join('、') : '未命中高风险关键词'}`,
        '',
        '需求显式候选（非业务路径）：',
        ...(candidateLines.length ? candidateLines : ['未从输入中提取到候选；这不会阻止 AI 读取完整需求。']),
        'AI 业务计划：尚未执行；任务启动后会先整理资料，再由平台 MM skills 判断业务分支、层级和路径。',
        '',
        '平台执行与门禁：',
        ...platformLines,
        '',
        plan.note || '任务启动后由所选模型补全业务步骤并接受平台门禁。'
      ];
      alert(lines.join('\n'));
    } catch(e) {
      showToast(e.message || '获取执行计划失败', 'error');
    }
  }, { btn: previewBtn, btnLabel: '生成中...', overlay: 'AI 正在规划...' });
}

function previewDashboardAgentPlan() {
  activateWorkflow('agent');
  setTimeout(previewAgentPlan, 0);
}


async function startAgentRun(options={}) {
  if (agentBusy) return;
  expandedStepIndexes.clear();
  agentCheckpointTraceOpen = false;
  const payload = agentPayloadFromForm(options);
  if (!payload.goal) {
    showToast('请先输入测试目标', 'error');
    return;
  }
  const riskHits = agentRiskHits(payload.goal);
  if (riskHits.length) {
    showToast(`已识别风险词：${riskHits.join('、')}，Runner 测试机执行只提醒不阻断；平台级写操作仍需确认`, 'warn');
  }

  // Disable button during request to prevent double-click
  const startBtn = document.getElementById('agent-start-btn');
  agentBusy = true;
  if (startBtn) startBtn.disabled = true;
  await showAgentWorkbench();

  await LoadingManager.withLoading(async () => {
    try {
      const data = await apiRequest('/agent-runs/start', {
        method: 'POST',
        body: payload
      });
      agentCurrentRun = normalizeAgentRun(data.run || data);
      if (agentCurrentRun && agentCurrentRun.runId) {
        // Sync to AppState
        AppState.currentAgentRun = agentCurrentRun;

        // Insert into run list (dedup by runId)
        agentRuns = [agentCurrentRun, ...agentRuns.filter(run => run.runId !== agentCurrentRun.runId)].slice(0, 20);

        // Immediately clear old timeline and render new run's initial state
        const progressEl = document.getElementById('agent-progress');
        if (progressEl) {
          progressEl.innerHTML = renderAgentTimeline(agentCurrentRun);
        } else {
          await showAgentWorkbench();
        }

        // Restart polling for the new runId (clears old interval first)
        startAgentPolling(agentCurrentRun.runId);

        // Refresh Agent center panel
        if (typeof renderAgentCenter === 'function') renderAgentCenter();

        showToast(options.analyzeOnly ? '✓ Agent 分析已启动' : '✓ Agent 已启动', 'success');
      } else {
        showToast('启动失败: ' + (data?.error || '未返回有效的 runId'), 'error');
      }
    } catch(e) {
      showToast(e.message || 'Agent 启动失败', 'error');
    } finally {
      agentBusy = false;
      if (startBtn) startBtn.disabled = false;
      if (activeWorkflow === 'agent' || activeWorkflow === 'dashboard') showAgentWorkbench();
    }
  }, { btn: startBtn, btnLabel: '启动中...', overlay: 'Agent 启动中，请稍候...' });
}

function startAgentPolling(runId) {
  // round 4: Agent 状态轮询只在 Agent 处于活跃状态时开启，统一记录到 AppState.polling
  if (agentRefreshTimer) clearInterval(agentRefreshTimer);
  agentRefreshTimer = setInterval(() => refreshAgentRun(runId), 3000);
  AppState.polling.agentStatus = agentRefreshTimer;
}

function stopAgentPolling() {
  if (agentRefreshTimer) {
    clearInterval(agentRefreshTimer);
    agentRefreshTimer = null;
  }
  AppState.polling.agentStatus = null;
}

function stopAgentPollingIfDone(run) {
  if (!run || !['FINISH', 'DONE', 'FAILED', 'CANCELLED', 'WAIT_CONFIRM', 'WAIT_CONFIRM_RUN', 'WAIT_CONFIRM_BUG'].includes(run.status)) return;
  stopAgentPolling();
}

// ===== Auto Agent Run API (Task Server) =====

async function startAutoAgentRun() {
  const selectedMode = document.querySelector('input[name="agent-mode"]:checked')?.value || 'AUTO_SAFE';
  await startAgentRun({ analyzeOnly: selectedMode === 'ANALYZE_ONLY' });
}
