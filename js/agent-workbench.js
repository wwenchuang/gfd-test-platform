// agent-workbench.js
// Extracted from task-manager.html (no logic changes).

// 记录用户手动展开的步骤索引，避免轮询刷新时收起
const expandedStepIndexes = new Set();
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
      fixedTemperature: provider.fixedTemperature
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
      fixedTemperature: provider?.fixedTemperature
    })).filter(provider => provider.id);
  }
  return [];
}

function agentProviderDisplayText(provider) {
  if (!provider) return '';
  const model = provider.model ? ` · ${provider.model}` : '';
  const configured = provider.configured === false ? ' · 未配置 Key' : '';
  const locked = provider.temperatureLocked ? ' · 固定参数' : '';
  return `${provider.name || provider.id}${model}${configured}${locked}`;
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
    const models = Array.isArray(mData?.models) ? mData.models : [];
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
            <p>输入测试目标后，Agent 自动完成用例选择、YAML 生成、Sonic 执行、失败分析、修复草稿、重跑和报告沉淀；高风险动作进入人工确认。</p>
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
  const mindmapUrl = agentMindmapDownloadUrl(run);

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
            <h3>Agent 步骤时间线</h3>
            <p>${AGENT_TIMELINE_STEPS.length} 个处理步骤：展示每一步在做什么、是否成功、用了多久，以及生成了哪些产物；需要你确认的动作会同步到右侧。</p>
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
      <div class="agent-card">
        <div class="agent-artifact-head">
          <h3>Agent 产物</h3>
          <div>
            <button class="btn-sm" onclick="copyAgentArtifact()">复制当前产物</button>
            <button class="btn-sm" onclick="downloadAgentYaml()">下载 YAML</button>
            <button class="btn-sm" onclick="downloadAgentMindmap()" ${mindmapUrl ? '' : 'disabled'}>下载脑图</button>
          </div>
        </div>
        <div class="agent-tabs">
          ${AGENT_ARTIFACT_TABS.map(([key, label]) => `
            <button class="agent-tab ${agentActiveTab === key ? 'active' : ''}" onclick="setAgentTab(${jsArg(key)})">${escapeHtml(label)}</button>
          `).join('')}
        </div>
        <div class="agent-artifact-box ${['quality', 'report', 'summary'].includes(agentActiveTab) ? 'rich' : ''}" id="agent-artifact-box">${renderAgentArtifactContent(agentActiveTab, run)}</div>
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
function updateAgentWorkbenchDynamic() {
  const run = currentAgentRun();
  const progressEl = document.getElementById('agent-progress');
  const artifactBox = document.getElementById('agent-artifact-box');
  const riskLevelEl = document.getElementById('agent-risk-level');

  if (progressEl) {
    progressEl.innerHTML = run ? renderAgentTimeline(run) : '';
  } else {
    // agent-progress 不存在时，完整重渲染确保时间线可见
    showAgentWorkbench();
    return;
  }
  if (artifactBox && run) {
    artifactBox.classList.toggle('rich', ['quality', 'report', 'summary'].includes(agentActiveTab));
    artifactBox.innerHTML = renderAgentArtifactContent(agentActiveTab, run);
  }
  if (riskLevelEl) {
    const goal = document.getElementById('agent-goal')?.value || '';
    riskLevelEl.textContent = `当前风险：${agentRiskText(classifyRiskLevel(goal))}`;
  }
  // Update tab active states
  document.querySelectorAll('.agent-tab').forEach(tab => {
    const tabKey = tab.getAttribute('data-tab') || tab.textContent.trim();
    tab.classList.toggle('active', tabKey === agentActiveTab);
  });
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
  ['PLAN', '理解目标'],
  ['PREPARE_SOURCE', '整理输入来源'],
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
  ['WAIT_CONFIRM', '等待确认'],
  ['RERUN', '安全重跑'],
  ['LEARN_FROM_RESULT', '沉淀学习'],
  ['GENERATE_SUMMARY', '生成总结'],
  ['DONE', '完成']
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
          <span>${escapeHtml(row.time || '')}</span>
          <strong>${escapeHtml(row.message || '')}</strong>
        </div>
      `).join('')}
    </div>
  `;
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
function renderRunTaskDetail(step, artifacts) {
  const progress = (artifacts || {}).jobProgress || {};
  const result = (artifacts || {}).jobResult || {};
  let html = '<div class="job-progress">';
  if (progress.total > 0) {
    const timeoutText = progress.timeoutSeconds || progress.timeout
      ? ` / 上限 ${escapeHtml(String(progress.timeoutSeconds || progress.timeout))}s`
      : '';
    html += `<div class="job-progress-summary">
      <span class="timeline-chip">总计 ${escapeHtml(String(progress.total))} 个任务</span>
      <span class="timeline-chip text-success">✓ ${escapeHtml(String(progress.completed || 0))} 成功</span>
      <span class="timeline-chip text-danger">✗ ${escapeHtml(String(progress.failed || 0))} 失败</span>
      <span class="timeline-chip text-info">⟳ ${escapeHtml(String(progress.running || 0))} 运行中</span>
      ${progress.timeout ? `<span class="timeline-chip text-warning">! ${escapeHtml(String(progress.timeout))} 超时</span>` : ''}
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
      status: item.status || '',
    }));
  const yamlExecutionRefs = [
    ...(Array.isArray(report.yamlExecutionRefs) ? report.yamlExecutionRefs : []),
    ...yamlFromReports,
  ];
  return { executionReports, yamlExecutionRefs };
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
      const label = `${r.module || ''}/${r.file || ''}`;
      if (r.reportUrl) {
        html += `<a href="${escapeHtml(r.reportUrl)}" target="_blank" class="report-link">${escapeHtml(label)}</a>`;
      } else {
        html += `<span class="report-local">${escapeHtml(label)}${r.localPath ? ` · ${escapeHtml(r.localPath)}` : ''}</span>`;
      }
    }
    html += '</div>';
  } else {
    html += '<div class="report-empty">当前没有 Runner 回传的 HTML 报告链接；下方仅展示已执行的 YAML/任务状态。</div>';
  }
  if (yamlRefs.length > 0) {
    html += '<div class="report-links">';
    html += '<div class="section-title">执行 YAML</div>';
    html += yamlRefs.slice(0, 10).map(item => `<span class="report-local">${escapeHtml(item.module || '')}/${escapeHtml(item.file || '')}</span>`).join('');
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
      html += `<li><strong>${escapeHtml(fj.module || '')}/${escapeHtml(fj.file || '')}</strong>：${escapeHtml(fj.error || '未知')}</li>`;
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
  const conclusionClass = summary.conclusion === '通过' ? 'success' : (summary.conclusion === '执行中' ? 'warn' : 'danger');
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
        <strong class="final-report-conclusion ${conclusionClass}">${escapeHtml(summary.conclusion || '-')}</strong>
      </div>
      <div class="final-report-meta">
        <span>${escapeHtml(agentModeText(summary.mode || run?.mode || '-'))}</span>
        <span>风险 ${escapeHtml(agentRiskText(summary.riskLevel || run?.riskLevel || '-'))}</span>
        <span>${escapeHtml(generatedAt || '-')}</span>
      </div>
      <div class="report-summary-grid final-report-metrics">
        <div><span>步骤成功</span><strong>${escapeHtml(summary.completed || 0)}/${escapeHtml(summary.totalSteps || 0)}</strong></div>
        <div><span>匹配用例</span><strong>${escapeHtml(summary.matchedCount || 0)}</strong></div>
        <div><span>HTML 报告</span><strong>${escapeHtml(reports.length || summary.reportCount || 0)}</strong></div>
        <div><span>失败/超时</span><strong>${escapeHtml((failedJobs.length || summary.failedJobCount || 0) + (timeoutJobs.length || summary.timeoutJobCount || 0))}</strong></div>
      </div>
      <div class="final-report-layout">
        <section class="final-report-panel">
          <strong>执行概览</strong>
          <dl>
            <div><dt>报告状态</dt><dd>${escapeHtml(summary.reportStatus || report.status || '-')}</dd></div>
            <div><dt>执行 YAML</dt><dd>${escapeHtml(yamlRefs.length || 0)} 个</dd></div>
            <div><dt>任务状态</dt><dd>${escapeHtml(jobStatuses.length || 0)} 个</dd></div>
            <div><dt>失败类型</dt><dd>${escapeHtml(summary.failureType || failure.failureType || 'NONE')}</dd></div>
          </dl>
        </section>
        <section class="final-report-panel">
          <strong>执行说明</strong>
          <p>${escapeHtml(summary.aiSummary || failure.conclusion || summary.message || '暂无说明')}</p>
        </section>
      </div>
      <div class="final-report-layout">
        <section class="final-report-panel">
          <strong>报告链接</strong>
          ${reports.length ? `<div class="final-report-links">${reports.slice(0, 6).map(item => {
            const label = `${item.module || ''}/${item.file || ''}`;
            return item.reportUrl
              ? `<a href="${escapeHtml(item.reportUrl)}" target="_blank">${escapeHtml(label)}</a>`
              : `<span>${escapeHtml(label || item.localPath || '-')}</span>`;
          }).join('')}</div>` : '<p>暂无 HTML 报告链接。</p>'}
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
      ${failedJobs.length ? `
        <section class="final-report-panel final-report-wide">
          <strong>失败摘要</strong>
          <div class="final-report-failures">
            ${failedJobs.slice(0, 5).map(job => `<div><b>${escapeHtml(job.module || '')}/${escapeHtml(job.file || '')}</b><span>${escapeHtml(job.error || job.status || '未知失败')}</span></div>`).join('')}
          </div>
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

function renderAgentArtifactContent(tab, run) {
  if (tab === 'quality') return renderAgentQualityArtifact(run);
  if (tab === 'report') return renderAgentReportArtifact(run);
  if (tab === 'summary') return renderAgentSummaryArtifact(run);
  const text = typeof agentArtifactText === 'function' ? agentArtifactText(tab, run) : '';
  return `<pre class="agent-artifact-pre">${escapeHtml(text)}</pre>`;
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

// ===== ANALYZE_FAILURE 分析详情 =====
function renderAnalysisDetail(step, artifacts) {
  const analysis = (artifacts || {}).failureAnalysis || {};
  let html = '<div class="analysis-detail agent-readable-detail">';
  const typeLabel = {
    'NONE': '无失败',
    'ENV_ISSUE': '环境问题',
    'CONFIG_ISSUE': '配置问题',
    'SCRIPT_ISSUE': '脚本问题',
  }[analysis.failureType] || analysis.failureType || '未分析';
  html += agentInfoGrid([
    { label: '失败类型', value: typeLabel },
    { label: '结论', value: analysis.conclusion || '暂无' },
    { label: '建议', value: analysis.recommendation || '暂无' },
  ]);
  if (analysis.conclusion) {
    html += `<section class="agent-readable-panel"><strong>分析结论</strong><p>${escapeHtml(analysis.conclusion)}</p></section>`;
  }
  if (analysis.summary && analysis.failureType !== 'NONE') {
    html += `<section class="agent-readable-panel"><strong>失败上下文</strong><pre class="agent-artifact-pre">${escapeHtml(typeof analysis.summary === 'string' ? analysis.summary : JSON.stringify(analysis.summary, null, 2))}</pre></section>`;
  }
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
  let html = '<div class="match-detail agent-readable-detail">';
  html += agentInfoGrid([
    { label: '体检项', value: checks.length },
    { label: '阻断', value: blockers.length },
    { label: '提醒', value: warnings.length },
  ]);
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

// ===== Step 详情分发函数 =====
function renderStepDetail(step, run) {
  const toolName = (step.toolCalls && step.toolCalls[0] && step.toolCalls[0].toolName) || step.toolName || '';
  const artifacts = (run && run.artifacts) || {};
  switch (toolName) {
    case 'prepare_source': return renderSourceContextDetail(step, artifacts);
    case 'impact_analysis': return renderSourceContextDetail(step, artifacts);
    case 'case_retrieval': return renderMatchDetail(step, artifacts);
    case 'list_cases': return renderMatchDetail(step, artifacts);
    case 'generate_yaml': return renderGenerateYamlDetail(step, artifacts);
    case 'execution_precheck': return renderExecutionPrecheckDetail(step, artifacts);
    case 'sonic_sync_case': return renderSonicSyncDetail(step, artifacts);
    case 'create_runner_job': return renderRunTaskDetail(step, artifacts);
    case 'read_report': return renderReportDetail(step, artifacts);
    case 'analyze_failure': return renderAnalysisDetail(step, artifacts);
    case 'diagnose_failure': return renderDiagnosisDetail((artifacts || {}).diagnosis || step.diagnosis);
    default: return '';
  }
}

function renderAgentTimeline(run) {
  if (!run) {
    return renderEmptyState('agent_history');
  }
  const currentRaw = String(run.currentStep || '').toUpperCase();
  const runStatus = String(run.status || '').toUpperCase();
  const runDone = ['DONE', 'FINISH'].includes(runStatus);
  const runTerminal = runDone || ['FAILED', 'CANCELLED'].includes(runStatus);
  const pendingConfirmations = run.pendingConfirmations || run.confirmations || [];
  const items = AGENT_TIMELINE_STEPS.map(([key, label], idx) => {
    const data = timelineStepData(key, run) || {};
    let status = normalizeTimelineStatus(data.status || data.state);
    if (runDone && key === 'DONE') {
      status = 'success';
    }
    if (runTerminal && key === 'WAIT_CONFIRM' && !pendingConfirmations.length && status === 'pending') {
      status = 'skipped';
    }
    if (status === 'pending') {
      const isCurrent = key === currentRaw
        || (key === 'RUN_SONIC' && currentRaw === 'RUN_TASK')
        || (key === 'WAIT_CONFIRM' && /WAIT_CONFIRM/.test(currentRaw));
      if (isCurrent) {
        status = runDone && key === 'DONE'
          ? 'success'
          : (/WAIT_CONFIRM/.test(currentRaw) ? 'waiting' : 'running');
      }
    }
    if (run.status === 'WAIT_CONFIRM' && key === 'WAIT_CONFIRM') status = 'waiting';
    if (data.success === true && status === 'pending') status = 'success';
    if (data.success === false && status === 'pending') status = 'failed';

    // NOT_IMPLEMENTED / SKIPPED steps show grey badge
    const rawStatus = String(data.status || data.state || '').toUpperCase();
    const isNotImplemented = rawStatus === 'NOT_IMPLEMENTED';
    const isSkipped = rawStatus === 'SKIPPED' || status === 'skipped';
    if (isNotImplemented) status = 'skipped';

    const meta = TIMELINE_STATUS_META[status] || TIMELINE_STATUS_META.pending;
    const summary = data.summary || data.message || '';
    const errorText = (status === 'failed' && (data.error || data.errorMessage || data.failureReason))
      ? (data.error || data.errorMessage || data.failureReason) : '';
    const dur = timelineDurationText(data);
    const artifacts = timelineArtifactLinks(data);
    const toolChips = timelineToolCallChips(data);
    const toolCallsDetail = timelineToolCallsDetail(data);
    const liveTraceDetail = timelineLiveTraceDetail(data);
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
          ${liveTraceDetail ? `<div class="step-tool-calls-wrap">${liveTraceDetail}</div>` : ''}
          ${toolCallsDetail ? `<div class="step-tool-calls-wrap">${toolCallsDetail}</div>` : ''}
          ${stepDetailHtml ? `<div class="step-tool-calls-wrap">${stepDetailHtml}</div>` : ''}
        </div>
      </li>
    `;
  }).join('');
  return `<ol class="agent-timeline-list">${items}</ol>`;
}

function renderAgentStepsPlan(run) {
  const steps = [
    ['UNDERSTAND_GOAL', '理解测试目标'],
    ['MATCH_CASES', '匹配已有用例'],
    ['GENERATE_YAML', '生成或补全 YAML'],
    ['VALIDATE_YAML', '校验 YAML'],
    ['SYNC_SONIC', '同步至 Sonic 平台'],
    ['RUN_TESTS', '执行测试'],
    ['COLLECT_REPORT', '收集报告'],
    ['ANALYZE_FAILURE', '分析失败'],
    ['GENERATE_REPAIR', '生成修复草稿'],
    ['SAFE_RERUN', '安全重跑'],
    ['GENERATE_REPORT', '生成报告和缺陷草稿']
  ];
  return steps.map(([key, label]) => {
    let state = 'pending';
    if (run) {
      const stepData = (run.steps || []).find(s => s.step === key);
      if (stepData) state = stepData.status === 'SUCCESS' ? 'done' : (stepData.status === 'FAILED' ? 'failed' : (stepData.status === 'WAIT_CONFIRM' ? 'confirm' : 'running'));
      else if (run.currentStep === key) state = 'running';
    }
    const icon = state === 'done' ? '✓' : (state === 'failed' ? '✗' : (state === 'running' ? '●' : (state === 'confirm' ? '⏸' : '○')));
    const stateLabel = state === 'done' ? '成功' : (state === 'failed' ? '失败' : (state === 'running' ? '执行中' : (state === 'confirm' ? '待确认' : '等待中')));
    return `<div class="agent-plan-step ${state}"><span class="step-icon">${icon}</span><span class="step-label">${label}</span><span class="step-state">${stateLabel}</span></div>`;
  }).join('');
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
  const highRisk = riskHits.some(hit => ['支付', '删除', '覆盖基线', '格式化', '解绑', '重置'].includes(hit));
  const autoRunEnabled = !options.yamlOnly && !!document.getElementById('agent-policy-runSonic')?.checked && !highRisk;
  const autoRepairEnabled = !options.yamlOnly && !!document.getElementById('agent-policy-autoRepair')?.checked && !highRisk;
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
    autoCreateBug: mode === 'FULL_AUTO' && !!document.getElementById('agent-policy-bugDraft')?.checked && !highRisk,
    autoOverwriteBaseline: false,
    maxRetries: 2,
    requireConfirmBeforePrint: riskHits.includes('确认打印') || riskHits.includes('开始打印'),
    riskHits,
    strategy: {
      generateCase: !!document.getElementById('agent-policy-generateCase')?.checked,
      generateYaml: true,
      validateYaml: !!document.getElementById('agent-policy-validateYaml')?.checked,
      runSonic: autoRunEnabled,
      safeRerun: !!document.getElementById('agent-policy-safeRerun')?.checked && !highRisk,
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
  const lines = [
        '全自动 Agent执行计划：',
        `模式：${agentModeText(plan.mode || payload.mode)}`,
        `应用：${plan.appName || payload.appName} / ${plan.platform || payload.platform}`,
        `范围：${plan.scope || payload.scope}`,
        runnerLine,
        `输入来源：${payload.sourceType || 'manual'}`,
        `输入资料：Figma ${payload.figmaUrl ? '1' : '0'} 个，文件 ${payload.files?.length || 0} 个，截图 ${payload.images?.length || 0} 张`,
        `风险：${hits.length ? hits.join('、') : '未命中高风险关键词'}`,
        '',
        ...(plan.steps || [
          '1. 分析测试目标',
          '2. 整理输入来源',
          '3. 匹配已有用例或生成新用例',
          '4. 生成并校验 Midscene YAML',
          '5. 通过 Windows/Mac Runner 执行已确认 YAML',
          '6. 收集报告并分析失败',
          '7. SCRIPT_ISSUE 生成修复草稿；PRODUCT_BUG 生成缺陷草稿',
          '8. 高风险或不确定动作进入待确认',
          '9. 生成总结报告'
        ])
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
  const payload = agentPayloadFromForm(options);
  if (!payload.goal) {
    showToast('请先输入测试目标', 'error');
    return;
  }
  const riskHits = agentRiskHits(payload.goal);
  if (riskHits.length) {
    showToast(`已识别风险词：${riskHits.join('、')}，Agent 会进入人工确认节点`, 'warn');
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
