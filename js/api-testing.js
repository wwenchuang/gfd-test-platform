// API testing workspace: OpenAPI assets -> AI plan drafts -> native API execution -> reports.

let apiPlanPageRequestId = 0;
let apiPlanGenerationRequestId = 0;
let apiPlanGenerationController = null;
let apiPlanGenerationPollTimer = null;
let apiPlanGenerationCurrent = null;
let apiPlanLaunchNotice = null;
let apiPlanBindingContext = null;
const apiPlanGenerationExpandedKeys = new Set(JSON.parse(localStorage.getItem('api_plan_generation_expanded_keys') || '[]'));
const apiPlanGenerationScrollPositions = new Map();
let apiBusinessAuthEditing = false;
let apiBusinessAuthType = 'bearer';
let apiBusinessAuthSourceMode = 'login';
let apiExecutionPollRequestId = 0;
let apiExecutionPollController = null;
let apiExecutionBindingLookupRequestId = 0;
let apiExecutionBindingLookupController = null;
let apiExecutionBindingSaveRequestId = 0;
let apiExecutionBindingSaveController = null;
let apiExecutionBindingIntentId = 0;
let apiExecutionBindingIntent = null;
let apiCaseDebugStartingKey = '';
let apiReportRequestId = 0;
let apiReportRequestController = null;
let apiReportPollTimer = null;
let apiTestingReportContext = null;
let apiSelectedReportId = '';
let apiSelectedReportDetail = null;
let apiPlanCaseEditor = { planId: '', caseId: '', text: '' };
let apiEnvironmentSnapshotEditing = false;
const API_PLAN_MAX_ENDPOINTS = 60;
const API_PLAN_AI_BATCH_SIZE = 12;
let apiAssetBusinessLines = [];
let apiWorkbenchCurrent = null;
let apiApifoxCredential = {};
const apiPlanReviewStateByPlan = new Map();
const apiExecutionBindingClientSessionId = globalThis.crypto?.randomUUID?.()
  || `${Date.now()}-${Math.random().toString(16).slice(2)}`;

function currentApiExecutionSourceId() {
  return apiTestingProjectScope.sourceId || apiAssetSelectedSourceId || apiExecutionContext?.source_id || '';
}

function abortApiExecutionBindingRequests() {
  apiExecutionBindingLookupController?.abort();
  apiExecutionBindingSaveController?.abort();
  apiExecutionBindingLookupController = null;
  apiExecutionBindingSaveController = null;
  apiExecutionBindingLookupRequestId += 1;
  apiExecutionBindingSaveRequestId += 1;
  apiExecutionBindingIntent = null;
}

function abortApiReportRequests() {
  apiReportRequestController?.abort();
  apiReportRequestController = null;
  apiReportRequestId += 1;
  if (apiReportPollTimer) clearTimeout(apiReportPollTimer);
  apiReportPollTimer = null;
}

function beginApiExecutionBindingIntent(projectId, environmentId = '') {
  apiExecutionBindingLookupController?.abort();
  apiExecutionBindingSaveController?.abort();
  apiExecutionBindingLookupController = null;
  apiExecutionBindingSaveController = null;
  apiExecutionBindingLookupRequestId += 1;
  apiExecutionBindingSaveRequestId += 1;
  apiExecutionBindingIntent = {
    intentId: ++apiExecutionBindingIntentId,
    sourceId: currentApiExecutionSourceId(),
    scopeKey: apiProjectScopeKey(),
    projectId: String(projectId || ''),
    environmentId: String(environmentId || ''),
  };
  return apiExecutionBindingIntent;
}

function apiExecutionBindingIntentIsCurrent(intent) {
  return !!intent
    && intent === apiExecutionBindingIntent
    && intent.intentId === apiExecutionBindingIntentId
    && intent.sourceId === currentApiExecutionSourceId()
    && intent.scopeKey === apiProjectScopeKey();
}

function apiExecutionBindingResponseIsCurrent(controller, requestId, intent) {
  return controller === apiExecutionBindingSaveController
    && requestId === apiExecutionBindingSaveRequestId
    && activeWorkflow === 'api_execution'
    && apiExecutionBindingIntentIsCurrent(intent)
    && intent.projectId === String(apiExecutionBindingIntent?.projectId || '')
    && intent.environmentId === String(apiExecutionBindingIntent?.environmentId || '');
}

function apiReportResponseIsCurrent(controller, requestId, sourceId, scopeKey) {
  return controller === apiReportRequestController
    && requestId === apiReportRequestId
    && activeWorkflow === 'api_reports'
    && sourceId === currentApiExecutionSourceId()
    && scopeKey === apiProjectScopeKey();
}

function setApiTestingPage(workflow, title, help) {
  if (workflow !== 'api_execution') {
    stopApiExecutionPolling(true);
    abortApiExecutionBindingRequests();
  }
  if (workflow !== 'api_assets') stopApiAssetSyncPolling();
  if (!['api_plan', 'api_dashboard'].includes(workflow)) stopApiPlanGenerationPolling(true);
  if (workflow !== 'api_reports') abortApiReportRequests();
  activeWorkflow = workflow;
  renderWorkflowNav();
  updateWorkbenchPanelMode();
  resetYamlToolbarForManager();
  const area = document.getElementById('editor-area');
  if (area) area.className = 'editor-area api-testing-area';
  const path = document.getElementById('toolbar-path');
  if (path) path.innerHTML = `<span>API</span> ${escapeHtml(title)}`;
  const helper = document.getElementById('toolbar-help');
  if (helper) helper.textContent = help || '';
  const info = document.getElementById('file-info');
  if (info) info.textContent = title;
  updateToolbarState('接口测试');
  return area;
}

function apiStatusPill(text, cls = '') {
  return `<span class="status-pill ${escapeHtml(cls)}">${escapeHtml(text || '-')}</span>`;
}

function apiJsString(value) {
  return String(value || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n').replace(/\r/g, '');
}

function apiTestingEmpty(text) {
  return `<div class="report-empty">${escapeHtml(text)}</div>`;
}

function apiBusinessLineForEndpoint(endpoint = {}) {
  return apiEndpointModulePath(endpoint).split('/')[0] || '未分组';
}

function apiBusinessLineOptions(endpoints = apiTestingEndpoints, serverLines = apiAssetBusinessLines) {
  const counts = new Map();
  (serverLines || []).forEach(item => {
    const name = String(item?.name || '').trim();
    if (name) counts.set(name, Number(item.endpoint_count || 0));
  });
  if (!counts.size) {
    (endpoints || []).forEach(endpoint => {
      const name = apiBusinessLineForEndpoint(endpoint);
      counts.set(name, (counts.get(name) || 0) + 1);
    });
  }
  return Array.from(counts, ([name, endpointCount]) => ({name, endpointCount}))
    .sort((left, right) => left.name.localeCompare(right.name, 'zh-Hans-CN'));
}

function currentApiBusinessLine() {
  const state = apiModuleSelectionState();
  const options = apiBusinessLineOptions();
  if (state.businessLine && options.some(item => item.name === state.businessLine)) {
    return state.businessLine;
  }
  return options[0]?.name || '';
}

function selectApiBusinessLine(name) {
  const state = apiModuleSelectionState();
  state.businessLine = String(name || '');
  state.activeModulePath = '';
  state.search = '';
  state.method = '';
  state.endpointIds.clear();
  renderApiModuleWorkspace();
  updateApiWorkflowStepper();
}

function apiWorkflowNextAction(context = {}) {
  const source = context.source || selectedApiAssetSource() || {};
  const endpoints = context.endpoints || apiTestingEndpoints || [];
  const selectedCount = selectedApiPlanEndpointIds().length;
  const plans = context.plans || apiTestingPlans || [];
  const generation = context.generation || apiPlanGenerationCurrent || {};
  const execution = context.execution || (apiExecutionContext?.active_runs || [])[0] || (apiTestingReportContext?.active_runs || [])[0] || {};
  const reports = context.reports || apiTestingReports || [];
  if (!source.configured) return {step: 'assets', label: '选择项目', handler: 'showApiAssetsPage()'};
  if (!endpoints.length) return {step: 'assets', label: '检查接口资产', handler: 'showApiAssetsPage()'};
  if (!apiSourceEnvironmentSummary(source).baseUrl) return {step: 'environment', label: '配置环境', handler: 'showApiEnvironmentPage()'};
  if (!selectedCount && !plans.length) return {step: 'assets', label: '选择模块和接口', handler: 'showApiAssetsPage()'};
  if (['queued', 'running'].includes(generation.status)) return {step: 'plan', label: '查看生成进度', handler: 'showApiPlanPage()'};
  const draft = plans.find(plan => plan.status === 'draft');
  if (draft) return {step: 'plan', label: '审阅用例', handler: 'showApiPlanPage()'};
  const confirmed = plans.find(plan => plan.status === 'confirmed' && (plan.revision_state || {}).state !== 'stale');
  if (['queued', 'running'].includes(execution.status)) return {step: 'history', label: '查看执行记录', handler: 'showApiExecutionHistoryPage()'};
  if (reports.length) return {step: 'reports', label: '查看报告', handler: 'showApiReportsPage()'};
  if (confirmed) return {step: 'history', label: '执行测试', handler: 'showApiExecutionPage()'};
  return {step: 'plan', label: selectedCount ? '生成测试资产' : '选择接口', handler: selectedCount ? 'showApiPlanPage()' : 'showApiAssetsPage()'};
}

function renderApiWorkflowStepper(context = {}) {
  const workflow = context.workflow || activeWorkflow || 'api_assets';
  const source = context.source || selectedApiAssetSource() || {};
  const plans = context.plans || apiTestingPlans || [];
  const endpoints = context.endpoints || apiTestingEndpoints || [];
  let activeStep = ({
    api_dashboard: 'dashboard',
    api_assets: 'assets',
    api_environment: 'environment',
    api_plan: 'plan',
    api_baselines: 'plan',
    api_execution: 'history',
    api_execution_history: 'history',
    api_reports: 'reports',
  })[workflow] || 'dashboard';
  if (workflow === 'api_plan' && (apiTestingCurrentPlan || plans.some(plan => plan.status === 'draft'))) activeStep = 'plan';
  const steps = [
    {id: 'dashboard', label: '工作台', handler: 'showApiTestingDashboard()'},
    {id: 'assets', label: '项目资产', handler: 'showApiAssetsPage()'},
    {id: 'environment', label: '环境配置', handler: 'showApiEnvironmentPage()'},
    {id: 'plan', label: '测试设计', handler: 'showApiPlanPage()'},
    {id: 'history', label: '执行记录', handler: 'showApiExecutionHistoryPage()'},
    {id: 'reports', label: '测试报告', handler: 'showApiReportsPage()'},
  ];
  const action = apiWorkflowNextAction(context);
  const completedSteps = new Set();
  completedSteps.add('dashboard');
  if (source.configured) completedSteps.add('assets');
  if (apiSourceEnvironmentSummary(source).baseUrl) completedSteps.add('environment');
  if (plans.length || apiPlanGenerationCurrent?.status === 'succeeded') completedSteps.add('plan');
  if ((context.execution || {}).status || (apiExecutionContext?.active_runs || []).length) completedSteps.add('history');
  if ((context.reports || apiTestingReports || []).length) completedSteps.add('history');
  if ((context.reports || apiTestingReports || []).length) completedSteps.add('reports');
  const businessLine = currentApiBusinessLine();
  const revisionTime = context.revisionTime || context.snapshot?.created_at || '';
  const stepMarkup = steps.map((step, index) => {
    const isActive = step.id === activeStep;
    const isNext = step.id === action.step;
    const isDone = completedSteps.has(step.id);
    return `<li class="${isActive ? 'active' : ''} ${isNext ? 'next' : ''} ${isDone ? 'done' : ''}"><button type="button" onclick="${step.handler}"><span>${isDone ? '✓' : index + 1}</span><strong>${escapeHtml(step.label)}</strong></button></li>`;
  }).join('');
  const activeStepIndex = Math.max(0, steps.findIndex(step => step.id === activeStep));
  const activeStepLabel = steps[activeStepIndex]?.label || steps[0].label;
  return `
    <nav class="api-workflow-stepper" aria-label="API 测试流程">
      <div class="api-workflow-context">
        <div><span>当前测试</span><strong>${escapeHtml(apiSourceDisplayName(source) || '选择 Apifox 项目')}${businessLine ? ` · ${escapeHtml(businessLine)}` : ''}</strong></div>
        <small>${revisionTime ? `接口更新于 ${escapeHtml(revisionTime)}` : '按步骤完成一次接口回归'}</small>
      </div>
      <ol class="api-workflow-desktop-steps">${stepMarkup}</ol>
      <details class="api-workflow-mobile-steps">
        <summary><span>当前 ${activeStepIndex + 1} / ${steps.length}</span><strong>${escapeHtml(activeStepLabel)}</strong></summary>
        <ol>${stepMarkup}</ol>
      </details>
      <button type="button" class="btn-sm primary api-workflow-next" onclick="${action.handler}">${escapeHtml(action.label)}</button>
      <details class="api-workflow-tech-detail"><summary>技术详情</summary><div>Source ${escapeHtml(apiTestingProjectScope.sourceId || '-')} · Revision ${escapeHtml(apiTestingProjectScope.revisionId || '-')}</div></details>
    </nav>
  `;
}

function updateApiWorkflowStepper(context = {}) {
  const target = document.getElementById('api-workflow-stepper');
  if (target) target.innerHTML = renderApiWorkflowStepper({workflow: activeWorkflow, ...context});
}

function apiEndpointLabel(endpoint) {
  return `${endpoint.method || ''} ${endpoint.path || ''}`.trim();
}

function apiSelectedEndpointIds() {
  return Array.from(document.querySelectorAll('.api-endpoint-check:checked')).map(input => input.value);
}

function apiPlanStatusText(status) {
  const map = { draft: '草稿', confirmed: '已保存', pushed: '已推送' };
  return map[status] || status || '候选';
}

function apiCaseAssertionText(assertion) {
  if (typeof assertion === 'string') return assertion;
  if (!assertion || typeof assertion !== 'object') return '-';
  if (assertion.type === 'status') {
    const expected = Array.isArray(assertion.expected) ? assertion.expected.join(' / ') : '-';
    return `状态码 ${assertion.operator || 'in'} ${expected}`;
  }
  if (assertion.type === 'schema') return `响应结构 ${assertion.schema_ref || 'response schema'}`;
  return JSON.stringify(assertion);
}

function apiCaseRequestText(item) {
  const request = item?.request || {};
  const route = `${request.method || ''} ${request.path || ''}`.trim() || item?.endpoint || '-';
  const bindingCount = ['path_params', 'query', 'headers', 'body'].reduce((total, key) => {
    const value = request[key];
    if (!value || typeof value !== 'object') return total;
    return total + Object.keys(value).length;
  }, 0);
  return `${route} · ${bindingCount} 项数据${request.auth_ref ? ' · 环境鉴权' : ''}`;
}

function apiPlanReadinessReason(plan) {
  const readiness = plan?.execution_readiness || {};
  const revision = plan?.revision_state || {};
  if (revision.state === 'stale') return revision.reason || '接口版本已变化，请重新生成计划';
  if ((readiness.missing || []).length) return `待补：${readiness.missing[0]}`;
  if (!readiness.executable_case_count) return '当前计划没有可执行用例';
  if (plan?.status !== 'confirmed') return '保存为测试资产后可进入执行';
  return '';
}

async function loadApiTestingOverview() {
  const data = await apiRequest('/api-testing/overview');
  apiTestingOverview = data;
  apiTestingSnapshots = data.snapshots || [];
  apiTestingEndpoints = data.endpoints || [];
  apiTestingPlans = data.plans || [];
  apiTestingReports = data.reports || [];
  apiTestingCurrentSnapshotId = data.latest_snapshot_id || apiTestingCurrentSnapshotId;
  return data;
}

async function loadApiTestingWorkbench(sourceId = currentApiExecutionSourceId()) {
  const query = new URLSearchParams();
  if (sourceId) query.set('source_id', sourceId);
  const data = await apiRequest(`/api-testing/workbench${query.toString() ? `?${query}` : ''}`);
  const source = data.source || {};
  const snapshot = data.snapshot || {};
  const scope = data.scope || {};
  const cases = data.cases || {};
  apiTestingOverview = data;
  apiWorkbenchCurrent = data;
  apiApifoxCredential = data.apifox_credential || apiApifoxCredential || {};
  apiTestingSources = data.sources || [];
  apiTestingEndpoints = scope.endpoints || [];
  apiAssetBusinessLines = scope.business_lines || [];
  apiTestingPlans = [...(cases.drafts || []), ...(cases.baselines || [])];
  apiTestingReports = data.reports || [];
  apiTestingSyncs = data.syncs || [];
  apiExecutionContext = {
    ...(data.execution || {}),
    source_id: source.source_id || '',
    source,
    plans: cases.baselines || [],
  };
  apiAssetSelectedSourceId = source.source_id || apiAssetSelectedSourceId || '';
  apiTestingCurrentSnapshotId = snapshot.revision_id || snapshot.snapshot_id || '';
  apiTestingProjectScope = {sourceId: apiAssetSelectedSourceId, revisionId: apiTestingCurrentSnapshotId};
  return data;
}

function apiWorkbenchSourceOptions(sources = [], selectedId = '') {
  if (!sources.length) return '<option value="">尚未连接 Apifox 项目</option>';
  return sources.map(source => `
    <option value="${escapeHtml(source.source_id || '')}" ${String(source.source_id || '') === String(selectedId || '') ? 'selected' : ''}>
      ${escapeHtml(source.name || source.project_name || source.source_id || 'API 项目')}
    </option>
  `).join('');
}

function apiWorkbenchConnectionText(workbench = {}) {
  const source = workbench.source || {};
  const snapshot = workbench.snapshot || {};
  const execution = workbench.execution || {};
  const connection = execution.connection || {};
  if (!source.source_id) return {label: '未连接', tone: 'warn', detail: '先连接 Apifox 项目并同步接口资产'};
  if (snapshot.state !== 'ready') return {label: '待同步', tone: 'warn', detail: snapshot.message || '本地还没有接口快照'};
  if (connection.state !== 'connected') return {label: '环境待配置', tone: 'warn', detail: 'Apifox 环境缺少可执行 base_url'};
  return {label: '可执行', tone: 'success', detail: connection.base_url || '环境已就绪'};
}

function apiWorkbenchFlattenModules(nodes = [], result = []) {
  (nodes || []).forEach(node => {
    if (!node || typeof node !== 'object') return;
    result.push(node);
    apiWorkbenchFlattenModules(node.children || [], result);
  });
  return result;
}

function apiWorkbenchCandidateModules(workbench = {}) {
  const roots = workbench.scope?.modules?.roots || [];
  return apiWorkbenchFlattenModules(roots)
    .filter(item => Number(item.endpoint_count || 0) > 0)
    .sort((left, right) => {
      const leftDepth = Number(left.depth || String(left.path || '').split('/').length);
      const rightDepth = Number(right.depth || String(right.path || '').split('/').length);
      return leftDepth - rightDepth
        || Number(right.endpoint_count || 0) - Number(left.endpoint_count || 0)
        || String(left.path || '').localeCompare(String(right.path || ''), 'zh-Hans-CN');
    })
    .slice(0, 12);
}

function apiWorkbenchEndpointCountForModule(module = {}) {
  return Number(module.endpoint_count || 0);
}

function apiWorkbenchModuleByPath(modulePath, workbench = apiWorkbenchCurrent) {
  const normalized = apiNormalizeModulePath(modulePath);
  return apiWorkbenchFlattenModules(workbench?.scope?.modules?.roots || [])
    .find(item => apiNormalizeModulePath(item.path) === normalized) || null;
}

function apiWorkbenchEndpointIdsForModule(modulePath) {
  const module = apiWorkbenchModuleByPath(modulePath);
  const serverIds = (module?.endpoint_ids || []).map(String).filter(Boolean);
  if (serverIds.length) return serverIds.slice(0, API_PLAN_MAX_ENDPOINTS);
  const normalized = apiNormalizeModulePath(modulePath);
  return apiTestingEndpoints
    .filter(endpoint => apiModulePathMatches(apiEndpointModulePath(endpoint), normalized))
    .map(endpoint => String(endpoint.endpoint_id || ''))
    .filter(Boolean)
    .slice(0, API_PLAN_MAX_ENDPOINTS);
}

function apiWorkbenchSuggestedChildModules(module = {}) {
  return (module.children || [])
    .filter(item => Number(item.endpoint_count || 0) > 0)
    .sort((left, right) => Number(right.endpoint_count || 0) - Number(left.endpoint_count || 0))
    .slice(0, 3)
    .map(item => `${item.name || item.path}（${item.endpoint_count}）`);
}

function apiWorkbenchConfirmLargeModuleGeneration(module = {}, selectedCount = 0) {
  const total = apiWorkbenchEndpointCountForModule(module);
  if (total <= API_PLAN_MAX_ENDPOINTS) return true;
  const batchCount = Math.ceil(selectedCount / API_PLAN_AI_BATCH_SIZE);
  const suggestions = apiWorkbenchSuggestedChildModules(module);
  const suggestionText = suggestions.length
    ? `\n建议优先选择子模块：${suggestions.join('、')}`
    : '\n建议优先选择更小的子模块或在高级资产管理中搜索接口。';
  return confirm(
    `当前模块共有 ${total} 个接口，本次只会选择前 ${selectedCount} 个，预计拆成 ${batchCount} 个 AI 批次串行生成。${suggestionText}\n\n继续生成吗？`
  );
}

function renderApiWorkbenchModules(workbench = {}) {
  const modules = apiWorkbenchCandidateModules(workbench);
  if (!modules.length) return apiTestingEmpty('当前快照还没有可生成用例的模块。');
  return `
    <div class="api-workbench-module-grid">
      ${modules.map(module => {
        const count = apiWorkbenchEndpointCountForModule(module);
        const endpointIds = (module.endpoint_ids || []).filter(Boolean);
        const selectedCount = Math.min(count, endpointIds.length || API_PLAN_MAX_ENDPOINTS);
        const disabled = count <= 0 || selectedCount <= 0;
        const capped = count > API_PLAN_MAX_ENDPOINTS;
        const suggestions = apiWorkbenchSuggestedChildModules(module);
        return `
          <article class="api-workbench-module-card">
            <div><strong>${escapeHtml(module.name || module.path || '未分组')}</strong><span>${escapeHtml(module.path || '')}</span></div>
            <small>${escapeHtml(count)} 个接口${capped ? `，本次选择 ${selectedCount} 个 · ${Math.ceil(selectedCount / API_PLAN_AI_BATCH_SIZE)} 批` : ''}</small>
            ${capped && suggestions.length ? `<small>建议：${escapeHtml(suggestions.join('、'))}</small>` : ''}
            <button class="btn-sm ai" ${disabled ? 'disabled' : ''} onclick="apiWorkbenchGenerateModule(${jsArg(module.path)})">${capped ? '确认范围后生成' : '生成测试资产'}</button>
          </article>
        `;
      }).join('')}
    </div>
  `;
}

async function refreshCurrentApiNativePage() {
  if (activeWorkflow === 'api_environment') return showApiEnvironmentPage();
  if (activeWorkflow === 'api_assets') return refreshApiAssetWorkspace(true);
  if (activeWorkflow === 'api_execution_history') return showApiExecutionHistoryPage();
  return showApiTestingDashboard();
}

function renderApiWorkbenchMetrics(workbench = {}) {
  const metrics = workbench.metrics || {};
  const cards = [
    ['API接口总数', metrics.total_endpoints ?? 0, '本地快照接口'],
    ['已覆盖接口', metrics.covered_endpoints ?? 0, '已保存测试资产'],
    ['覆盖率', `${metrics.coverage_rate ?? 0}%`, '按接口去重'],
    ['待处理变化', metrics.pending_changes ?? 0, '来自最近同步差异'],
    ['今日执行', metrics.today_executions ?? 0, '本地执行记录'],
  ];
  return `
    <section class="api-workbench-metrics api-compact-metrics" aria-label="API 工作台指标">
      ${cards.map(([label, value, hint]) => `
        <article>
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
          <small>${escapeHtml(hint)}</small>
        </article>
      `).join('')}
    </section>
  `;
}

function renderApiWorkbenchPrimaryActions(workbench = {}) {
  const source = workbench.source || {};
  const snapshot = workbench.snapshot || {};
  const cases = workbench.cases || {};
  const hasSnapshot = Number(snapshot.endpoint_count || 0) > 0;
  const hasBaseline = !!cases.latest_baseline?.plan_id;
  return `
    <div class="api-primary-action-row">
      <button class="btn-sm primary" onclick="${source.source_id ? 'apiWorkbenchUpdateSnapshot()' : 'showApiAssetsPage()'}">
        ${source.source_id ? '更新资产' : '连接 Apifox'}
      </button>
      <button class="btn-sm ai" onclick="document.getElementById('api-workbench-module-section')?.scrollIntoView({behavior: 'smooth', block: 'start'})" ${hasSnapshot ? '' : 'disabled'}>
        AI 生成测试
      </button>
      <button class="btn-sm" onclick="showApiExecutionPage()" ${hasBaseline ? '' : 'disabled'}>
        开始测试
      </button>
    </div>
  `;
}

function renderApiWorkbenchNextActions(workbench = {}) {
  const source = workbench.source || {};
  const snapshot = workbench.snapshot || {};
  const cases = workbench.cases || {};
  const execution = workbench.execution || {};
  const activeRun = (execution.active_runs || [])[0] || {};
  const endpointCount = Number(snapshot.endpoint_count || 0);
  const actions = [];
  if (!source.source_id) {
    actions.push(['连接 Apifox 项目', '保存项目、环境和接口快照，后续同事可直接复用。', 'showApiAssetsPage()', '连接']);
  } else if (!endpointCount) {
    actions.push(['更新本地接口快照', '从 Apifox 拉取接口和环境配置，固化为平台本地资产。', 'apiWorkbenchUpdateSnapshot()', '更新']);
  } else if (!cases.latest_draft?.plan_id && !cases.latest_baseline?.plan_id) {
    actions.push(['选择模块生成测试', '从下方模块卡片选择范围，AI 会按接口合同生成测试资产草稿。', "document.getElementById('api-workbench-module-section')?.scrollIntoView({behavior: 'smooth', block: 'start'})", '选择模块']);
  } else if (!cases.latest_baseline?.plan_id) {
    actions.push(['审阅 AI 草稿', '确认请求入参、断言和可执行项后保存为测试资产。', `openApiWorkbenchPlan(${jsArg(cases.latest_draft?.plan_id || '')})`, '审阅']);
  } else if (activeRun.execution_id) {
    actions.push(['查看实时执行', '本地执行器正在跑接口，可查看请求、响应、断言和日志。', 'showApiExecutionHistoryPage()', '看日志']);
  } else {
    actions.push(['开始本地执行', '使用当前环境快照和业务 token 执行已保存测试资产。', 'showApiExecutionPage()', '执行']);
  }
  if ((workbench.pending_changes || []).length) {
    actions.push(['处理接口变化', '最近同步发现接口变化，建议先复核受影响测试资产。', 'showApiAssetsPage()', '查看变化']);
  }
  return `
    <aside class="api-next-actions">
      <div><span>下一步</span><strong>${escapeHtml(actions[0]?.[0] || '当前可直接执行')}</strong></div>
      ${actions.slice(0, 2).map(([title, detail, action, label]) => `
        <article>
          <p><b>${escapeHtml(title)}</b><span>${escapeHtml(detail)}</span></p>
          <button class="btn-sm" onclick="${action}">${escapeHtml(label)}</button>
        </article>
      `).join('')}
    </aside>
  `;
}

function renderApiWorkbenchSyncState(workbench = {}) {
  const state = workbench.sync_state || {};
  const source = workbench.source || {};
  const action = source.source_id
    ? `<button class="btn-sm primary" onclick="apiWorkbenchUpdateSnapshot()">检查更新</button>`
    : `<button class="btn-sm primary" onclick="showApiAssetsPage()">连接 Apifox</button>`;
  return `
    <section class="api-panel api-workbench-sync-state">
      <div class="api-section-heading">
        <div><span>Apifox 状态</span><h3>${escapeHtml(state.project || '未连接')}</h3></div>
        <div class="api-workbench-actions-row">${action}<button class="btn-sm" onclick="showApiTestingDashboard()">刷新页面</button></div>
      </div>
      <div class="api-workbench-sync-grid">
        <div><span>连接状态</span><strong>${escapeHtml(state.status || '未连接')}</strong><small>${escapeHtml(state.error || 'Apifox 只读，本地使用快照')}</small></div>
        <div><span>最后同步</span><strong>${escapeHtml(state.last_sync_at || '尚未同步')}</strong><small>${escapeHtml(state.sync_id || '-')}</small></div>
        <div><span>接口数量</span><strong>${escapeHtml(state.interface_count || 0)}</strong><small>当前本地可用版本</small></div>
        <div><span>影响测试资产</span><strong>${escapeHtml(state.affected_tests || 0)}</strong><small>变化后需复核</small></div>
      </div>
    </section>
  `;
}

function renderApiWorkbenchPendingChanges(workbench = {}) {
  const changes = workbench.pending_changes || [];
  return `
    <section class="api-panel api-workbench-pending">
      <div class="api-section-heading">
        <div><span>待处理变化</span><h3>今天需要关注什么</h3></div>
        <small>${escapeHtml(changes.length)} 类变化</small>
      </div>
      ${changes.length ? `
        <div class="api-pending-change-list">
          ${changes.map(item => `
            <article>
              <div><span>${escapeHtml(item.title || '接口变化')}</span><strong>${escapeHtml(item.count || 0)} 个接口</strong></div>
              <p>影响测试资产 ${escapeHtml(item.affected_tests || 0)} 条；AI 建议：${escapeHtml(item.ai_suggestion || '复核接口合同和测试资产')}</p>
              <button class="btn-sm" onclick="showApiAssetsPage()">查看接口</button>
            </article>
          `).join('')}
        </div>
      ` : apiTestingEmpty('暂无待处理变化。本地快照可直接用于测试设计和执行。')}
    </section>
  `;
}

function renderApiWorkbenchSourceCard(workbench = {}) {
  const source = workbench.source || {};
  const snapshot = workbench.snapshot || {};
  const status = apiWorkbenchConnectionText(workbench);
  const sourceEnv = apiSourceEnvironmentSummary(source);
  return `
    <section class="api-command-center">
      <div class="api-command-main">
        <div class="api-workbench-title">
          <span>API 工作台</span>
          <h2>${escapeHtml(source.project_name || source.name || '接口自动化')}</h2>
          <p>Apifox 提供接口和环境来源；平台保存本地快照，AI 生成测试资产，本地执行器输出实时日志和报告。</p>
        </div>
        <div class="api-workbench-controls">
          <label><span>当前项目</span><select onchange="apiWorkbenchSelectSource(this.value)">${apiWorkbenchSourceOptions(workbench.sources || [], source.source_id)}</select></label>
          ${renderApiWorkbenchPrimaryActions(workbench)}
        </div>
        <div class="api-compact-project-grid">
          <article>${apiStatusPill(status.label, status.tone)}<strong>${escapeHtml(status.detail)}</strong><span>连接状态</span></article>
          <article><strong>${escapeHtml(snapshot.endpoint_count || 0)}</strong><span>接口快照 · ${escapeHtml(snapshot.created_at || source.last_success_at || '尚未同步')}</span></article>
          <article><strong>${escapeHtml(source.environment_name || sourceEnv.environmentName || '未选择环境')}</strong><span>${escapeHtml(sourceEnv.baseUrl || 'base_url 待同步')}</span></article>
          <article><strong>${escapeHtml(source.environment_snapshot?.variable_count || 0)}</strong><span>环境变量 · 敏感 ${escapeHtml(source.environment_snapshot?.sensitive_variable_count || 0)}</span></article>
        </div>
        ${renderApiWorkbenchMetrics(workbench)}
      </div>
      ${renderApiWorkbenchNextActions(workbench)}
    </section>
  `;
}

function renderApiWorkbenchFlowCards(workbench = {}) {
  const source = workbench.source || {};
  const snapshot = workbench.snapshot || {};
  const cases = workbench.cases || {};
  const latestRun = ((workbench.execution || {}).active_runs || [])[0]
    || ((workbench.execution || {}).recent_runs || [])[0]
    || {};
  const latestReport = (workbench.reports || [])[0] || {};
  const cards = [
    {
      title: '连接 Apifox',
      state: source.source_id ? '已连接' : '待连接',
      detail: source.source_id ? `${source.project_name || source.name || 'API 项目'} · ${source.credential_configured ? '凭据已保存' : '凭据待配置'}` : '保存一次后，同事可直接使用本地项目。',
      action: source.source_id ? 'showApiAssetsPage()' : 'showApiAssetsPage()',
      label: source.source_id ? '查看资产' : '连接',
      tone: source.source_id ? 'ready' : 'todo',
    },
    {
      title: '固化资产',
      state: snapshot.endpoint_count ? `${snapshot.endpoint_count} 个接口` : '待同步',
      detail: snapshot.endpoint_count ? `本地版本 ${snapshot.created_at || '-'}` : '从 Apifox 拉取接口和环境后固化。',
      action: source.source_id ? 'apiWorkbenchUpdateSnapshot()' : 'showApiAssetsPage()',
      label: snapshot.endpoint_count ? '更新快照' : '同步',
      tone: snapshot.endpoint_count ? 'ready' : 'todo',
    },
    {
      title: 'AI 测试设计',
      state: cases.latest_baseline?.plan_id ? '已保存测试资产' : (cases.latest_draft?.plan_id ? '草稿待审阅' : '待生成'),
      detail: cases.latest_baseline?.plan_id
        ? `${cases.latest_baseline.case_count || 0} 条用例 · ${cases.latest_baseline.executable_case_count || 0} 可执行`
        : (cases.latest_draft?.plan_id ? '确认入参和断言后保存为测试资产。' : '从模块卡片选择范围后生成。'),
      action: cases.latest_draft?.plan_id
        ? `openApiWorkbenchPlan(${jsArg(cases.latest_draft.plan_id)})`
        : "document.getElementById('api-workbench-module-section')?.scrollIntoView({behavior: 'smooth', block: 'start'})",
      label: cases.latest_draft?.plan_id ? '审阅用例' : '选择模块',
      tone: cases.latest_baseline?.plan_id ? 'ready' : (cases.latest_draft?.plan_id ? 'warn' : 'todo'),
    },
    {
      title: '本地执行报告',
      state: latestRun.execution_id ? apiExecutionStateText(latestRun.status) : (latestReport.report_id ? apiExecutionStateText(latestReport.status) : '未执行'),
      detail: latestReport.report_id
        ? `${latestReport.passed || 0} 通过 · ${latestReport.failed || 0} 失败`
        : '执行时展示实时日志，结束后沉淀报告和失败分析。',
      action: latestRun.execution_id ? 'showApiExecutionHistoryPage()' : 'showApiExecutionPage()',
      label: latestRun.execution_id ? '看日志' : '执行测试',
      tone: latestReport.report_id || latestRun.execution_id ? 'ready' : 'todo',
    },
  ];
  return `
    <section class="api-workflow-strip" aria-label="API 自动化流程">
      ${cards.map((card, index) => `
        <article class="api-workflow-card ${card.tone}">
          <span>${String(index + 1).padStart(2, '0')}</span>
          <div><strong>${escapeHtml(card.title)}</strong><b>${escapeHtml(card.state)}</b><small>${escapeHtml(card.detail)}</small></div>
          <button class="btn-sm" onclick="${card.action}">${escapeHtml(card.label)}</button>
        </article>
      `).join('')}
    </section>
  `;
}

function renderApiWorkbenchCaseCard(workbench = {}) {
  const cases = workbench.cases || {};
  const draft = cases.latest_draft || {};
  const baseline = cases.latest_baseline || {};
  const activeRun = (workbench.execution?.active_runs || [])[0] || {};
  const latestRun = activeRun.execution_id ? activeRun : ((workbench.execution?.recent_runs || [])[0] || {});
  const latestReport = (workbench.reports || [])[0] || {};
  return `
    <section class="api-panel api-workbench-case-panel">
      <div class="api-section-heading">
        <div><span>测试资产</span><h3>从 AI 草稿到可执行资产</h3></div>
        <small>${escapeHtml(cases.draft_count || 0)} 个草稿 · ${escapeHtml(cases.baseline_count || 0)} 个已保存</small>
      </div>
      <div class="api-workbench-stage-grid">
        ${renderApiWorkbenchPlanTile('AI 草稿', draft, 'openApiWorkbenchPlan', '查看/编辑用例')}
        ${renderApiWorkbenchPlanTile('已保存测试资产', baseline, 'openApiWorkbenchPlan', baseline.plan_id ? '查看资产' : '暂无资产')}
        ${renderApiWorkbenchRunTile(latestRun)}
        ${renderApiWorkbenchReportTile(latestReport)}
      </div>
      <div id="api-plan-generation-region" class="api-workbench-generation">${renderApiPlanGeneration(apiPlanGenerationCurrent)}</div>
      <section id="api-plan-result" class="api-workbench-plan-detail">${apiTestingEmpty(draft.plan_id ? '点击草稿卡片查看 AI 生成的用例明细。' : '先从下方模块生成测试资产。')}</section>
    </section>
  `;
}

function renderApiWorkbenchPlanTile(title, plan = {}, handlerName, actionText) {
  const hasPlan = !!plan.plan_id;
  return `
    <article class="api-workbench-stage-card">
      <span>${escapeHtml(title)}</span>
      <strong>${hasPlan ? escapeHtml(plan.name || plan.plan_id) : '未生成'}</strong>
      <small>${hasPlan ? `${escapeHtml(plan.case_count || 0)} 条用例 · ${escapeHtml(plan.executable_case_count || 0)} 可执行` : '等待选择模块后生成'}</small>
      <button class="btn-sm ${title === 'AI 草稿' ? 'ai' : 'primary'}" ${hasPlan ? '' : 'disabled'} onclick="${handlerName}(${jsArg(plan.plan_id || '')})">${escapeHtml(actionText)}</button>
    </article>
  `;
}

function renderApiWorkbenchRunTile(run = {}) {
  const stats = run.stats || {};
  const running = run.execution_id && !apiExecutionTerminal(run);
  return `
    <article class="api-workbench-stage-card">
      <span>执行</span>
      <strong>${run.execution_id ? escapeHtml(apiExecutionStateText(run.status)) : '未执行'}</strong>
      <small>${run.execution_id ? `${escapeHtml(stats.completed || 0)}/${escapeHtml(stats.total || 0)} 完成 · ${escapeHtml(stats.failed || 0)} 失败` : '保存测试资产后可执行全量或单条调试'}</small>
      <button class="btn-sm primary" ${run.execution_id ? '' : 'disabled'} onclick="showApiExecutionPage()">${running ? '看实时日志' : '看执行详情'}</button>
    </article>
  `;
}

function renderApiWorkbenchReportTile(report = {}) {
  return `
    <article class="api-workbench-stage-card">
      <span>报告</span>
      <strong>${report.report_id ? escapeHtml(apiExecutionStateText(report.status)) : '未生成'}</strong>
      <small>${report.report_id ? `${escapeHtml(report.passed || 0)} 通过 · ${escapeHtml(report.failed || 0)} 失败` : '执行结束后自动生成失败分析'}</small>
      <button class="btn-sm" ${report.report_id ? '' : 'disabled'} onclick="apiSelectedReportId=${jsArg(report.report_id || '')}; showApiReportsPage()">查看报告</button>
    </article>
  `;
}

function renderApiWorkbenchAssetCard(workbench = {}) {
  const source = workbench.source || {};
  const snapshot = workbench.snapshot || {};
  const sync = (workbench.syncs || [])[0] || {};
  return `
    <section id="api-workbench-module-section" class="api-panel api-workbench-asset-panel api-clean-snapshot">
      <div class="api-section-heading">
        <div><span>接口快照</span><h3>选择模块生成测试资产</h3></div>
        <div class="api-workbench-actions-row">
          <button class="btn-sm primary" onclick="apiWorkbenchUpdateSnapshot()" ${source.source_id ? '' : 'disabled'}>更新本地快照</button>
          <button class="btn-sm" onclick="showApiAssetsPage()">高级资产管理</button>
        </div>
      </div>
      <div class="api-workbench-asset-facts">
        <div><span>来源</span><strong>${escapeHtml(source.project_name || source.name || '-')}</strong></div>
        <div><span>本地版本</span><strong>${escapeHtml(snapshot.created_at || '-')}</strong></div>
        <div><span>同步状态</span><strong>${escapeHtml(apiAssetSyncStatusText(sync.status || source.last_sync_status || ''))}</strong></div>
        <div><span>接口数</span><strong>${escapeHtml(snapshot.endpoint_count || 0)}</strong></div>
      </div>
      <div class="api-workbench-section-title"><span>业务模块</span><small>选择模块即可生成；超过 ${API_PLAN_MAX_ENDPOINTS} 个接口会自动截取当前模块前 ${API_PLAN_MAX_ENDPOINTS} 个</small></div>
      ${renderApiWorkbenchModules(workbench)}
      <details class="api-workbench-tech-detail">
        <summary>高级信息</summary>
        <div>
          Source ${escapeHtml(source.source_id || '-')} · Revision ${escapeHtml(snapshot.revision_id || snapshot.snapshot_id || '-')} ·
          Project ${escapeHtml(source.project_id || '-')} · Environment ${escapeHtml(source.environment_id || '-')}
        </div>
        ${renderApiWorkbenchSyncState(workbench)}
        ${renderApiWorkbenchPendingChanges(workbench)}
      </details>
    </section>
  `;
}

async function showApiTestingDashboard() {
  const area = setApiTestingPage('api_dashboard', 'API 工作台', 'Apifox 接口资产、AI 测试设计、本地执行和 API 报告闭环。');
  if (!area) return;
  area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty('正在读取 API 工作台...')}</div>`;
  try {
    const data = await loadApiTestingWorkbench();
    area.innerHTML = `
      <div class="api-testing-page api-workbench-page">
        ${renderApiWorkbenchSourceCard(data)}
        ${renderApiWorkbenchFlowCards(data)}
        ${renderApiWorkbenchAssetCard(data)}
      </div>
    `;
  } catch(e) {
    area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty(e.message || 'API 工作台读取失败')}</div>`;
  }
}

async function apiWorkbenchSelectSource(sourceId) {
  apiAssetSelectedSourceId = String(sourceId || '');
  apiTestingProjectScope = {sourceId: apiAssetSelectedSourceId, revisionId: ''};
  await refreshCurrentApiNativePage();
}

async function apiWorkbenchUpdateSnapshot() {
  const sourceId = apiTestingProjectScope.sourceId || apiAssetSelectedSourceId;
  if (!sourceId) {
    showToast('请先连接 Apifox 项目', 'error');
    return;
  }
  try {
    const data = await apiRequest('/api-testing/snapshots/update', {
      method: 'POST',
      body: {source_id: sourceId}
    });
    const sync = data.sync || {};
    showToast(sync.created ? '✓ Apifox 快照同步已排队' : '✓ Apifox 快照同步已存在', 'success');
    await refreshCurrentApiNativePage();
    if (sync.sync_id && !apiAssetSyncTerminal(sync)) {
      setTimeout(() => {
        if (['api_dashboard', 'api_assets', 'api_environment'].includes(activeWorkflow)) refreshCurrentApiNativePage();
      }, Math.max(1500, Number(sync.poll_after_ms || 2000)));
    }
  } catch (error) {
    showToast(error.message || 'Apifox 快照同步失败', 'error');
  }
}

function renderApiSyncCenter(workbench = {}) {
  const source = workbench.source || {};
  const snapshot = workbench.snapshot || {};
  const sync = (workbench.syncs || [])[0] || {};
  const summary = sync.summary || {};
  return `
    <section class="api-panel api-sync-center-panel">
      <div class="api-section-heading">
        <div><span>同步状态</span><h3>Apifox 只读同步到本地</h3></div>
        <div class="api-workbench-actions-row">
          <button class="btn-sm primary" onclick="apiWorkbenchUpdateSnapshot()" ${source.source_id ? '' : 'disabled'}>立即同步</button>
          <button class="btn-sm" onclick="showApiAssetsPage()">接口资产</button>
        </div>
      </div>
      <div class="api-sync-overview-grid">
        <div><span>项目</span><strong>${escapeHtml(apiSourceDisplayName(source) || '未连接')}</strong><small>${escapeHtml(source.project_id || '-')}</small></div>
        <div><span>最后同步</span><strong>${escapeHtml(source.last_success_at || snapshot.last_sync_at || '尚未同步')}</strong><small>${escapeHtml(apiAssetSyncStatusText(sync.status || source.last_sync_status || ''))}</small></div>
        <div><span>接口快照</span><strong>${escapeHtml(snapshot.endpoint_count || 0)} 个接口</strong><small>${escapeHtml(snapshot.created_at || '本地暂无版本')}</small></div>
        <div><span>影响计划</span><strong>${escapeHtml(summary.affected_plans || 0)} 个</strong><small>同步只影响本地执行，不回写 Apifox</small></div>
      </div>
      <div class="api-sync-diff-grid">
        <div><strong>${escapeHtml(summary.added || 0)}</strong><span>新增接口</span></div>
        <div><strong>${escapeHtml(summary.changed || 0)}</strong><span>修改接口</span></div>
        <div><strong>${escapeHtml(summary.removed || 0)}</strong><span>删除接口</span></div>
        <div><strong>${escapeHtml(summary.unchanged || 0)}</strong><span>未变化</span></div>
      </div>
      <div id="api-assets-sync">${sync.sync_id ? renderApiAssetSync(sync) : apiTestingEmpty('暂无同步记录。连接 Apifox 项目后点击“立即同步”。')}</div>
    </section>
  `;
}

async function showApiSyncCenterPage() {
  return showApiAssetsPage();
}

function renderApiEnvironmentCenter(workbench = {}) {
  const source = workbench.source || {};
  const execution = {
    ...(workbench.execution || {}),
    source_id: source.source_id || '',
    source,
  };
  apiExecutionContext = execution;
  return `
    <section class="api-panel api-environment-center-panel">
      <div class="api-section-heading">
        <div><span>环境配置</span><h3>Base URL、变量和业务鉴权</h3></div>
        <small>只影响本地执行，不回写 Apifox</small>
      </div>
      ${renderApiSourceEnvironmentCompact(source)}
      ${renderApiSourceEnvironmentSnapshot(source)}
      <div id="api-environment-auth-panel">${renderApiBusinessAuthPanel(execution)}</div>
    </section>
  `;
}

async function showApiEnvironmentPage() {
  const area = setApiTestingPage('api_environment', '环境配置', '维护本地执行环境、Base URL、变量和业务 token；不反写 Apifox。');
  if (!area) return;
  area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty('正在读取环境配置...')}</div>`;
  try {
    const data = await loadApiTestingWorkbench();
    area.innerHTML = `
      <div class="api-testing-page api-native-center-page">
        <div id="api-workflow-stepper">${renderApiWorkflowStepper({workflow: 'api_environment', source: data.source || {}, snapshot: data.snapshot || {}})}</div>
        ${renderSavedApiSourceShelf(data.sources || [], data.source?.source_id || '', 'workbench')}
        ${renderApiEnvironmentCenter(data)}
      </div>
    `;
  } catch (error) {
    area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty(error.message || '环境配置读取失败')}</div>`;
  }
}

function renderApiExecutionHistory(workbench = {}) {
  const execution = workbench.execution || {};
  const activeRuns = execution.active_runs || [];
  const recentRuns = execution.recent_runs || [];
  return `
    <section class="api-panel api-execution-history-panel">
      <div class="api-section-heading">
        <div><span>执行记录</span><h3>请求、响应、断言和报告入口</h3></div>
        <div class="api-workbench-actions-row">
          <button class="btn-sm primary" onclick="showApiExecutionPage()">执行测试</button>
          <button class="btn-sm" onclick="showApiReportsPage()">测试报告</button>
        </div>
      </div>
      ${activeRuns.length || recentRuns.length
        ? `${renderApiReportActiveRuns(activeRuns)}${renderApiReportRecentRuns(recentRuns)}`
        : apiTestingEmpty('暂无执行记录。保存测试资产后，可在这里发起执行并查看实时日志。')}
    </section>
  `;
}

async function showApiExecutionHistoryPage() {
  const area = setApiTestingPage('api_execution_history', '执行记录', '查看本地执行器的真实执行日志、请求响应和报告入口。');
  if (!area) return;
  area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty('正在读取执行记录...')}</div>`;
  try {
    const data = await loadApiTestingWorkbench();
    area.innerHTML = `
      <div class="api-testing-page api-native-center-page">
        <div id="api-workflow-stepper">${renderApiWorkflowStepper({workflow: 'api_execution_history', source: data.source || {}, snapshot: data.snapshot || {}, execution: (data.execution?.active_runs || [])[0] || {}})}</div>
        ${renderSavedApiSourceShelf(data.sources || [], data.source?.source_id || '', 'workbench')}
        ${renderApiExecutionHistory(data)}
      </div>
    `;
    if ((data.execution?.active_runs || []).length) {
      setTimeout(() => {
        if (activeWorkflow === 'api_execution_history') showApiExecutionHistoryPage();
      }, 5000);
    }
  } catch (error) {
    area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty(error.message || '执行记录读取失败')}</div>`;
  }
}

async function apiWorkbenchGenerateModule(modulePath) {
  const normalized = apiNormalizeModulePath(modulePath);
  const module = apiWorkbenchModuleByPath(normalized);
  const endpointIds = apiWorkbenchEndpointIdsForModule(normalized);
  const totalCount = apiWorkbenchEndpointCountForModule(module || {});
  if (!endpointIds.length) {
    showToast('该模块没有可生成的接口', 'error');
    return;
  }
  if (!apiWorkbenchConfirmLargeModuleGeneration(module || {path: normalized, endpoint_count: endpointIds.length}, endpointIds.length)) {
    return;
  }
  const state = apiModuleSelectionState();
  state.endpointIds.clear();
  endpointIds.forEach(endpointId => state.endpointIds.add(String(endpointId)));
  state.selectedModules.clear();
  state.selectedModules.add(normalized);
  state.activeModulePath = normalized;
  if (totalCount > API_PLAN_MAX_ENDPOINTS) {
    showToast(`已按平台上限选择 ${endpointIds.length} 个接口，建议后续按子模块补充`, 'warn');
  }
  await startApiPlanGeneration();
}

async function openApiWorkbenchPlan(planId) {
  const target = document.getElementById('api-plan-result');
  if (!target) {
    await showApiPlanPage();
    if (activeWorkflow === 'api_plan') await openApiTestPlan(planId);
    return;
  }
  await openApiTestPlan(planId);
}

function apiProjectScopeKey(sourceId = apiTestingProjectScope.sourceId, revisionId = apiTestingProjectScope.revisionId) {
  return `${sourceId}:${revisionId}`;
}

function apiModuleSelectionState(sourceId = apiTestingProjectScope.sourceId, revisionId = apiTestingProjectScope.revisionId) {
  const key = apiProjectScopeKey(sourceId, revisionId);
  if (!apiTestingSelectionByScope.has(key)) {
    apiTestingSelectionByScope.set(key, {
      endpointIds: new Set(),
      selectedModules: new Set(),
      activeModulePath: '',
      search: '',
      method: '',
      businessLine: ''
    });
  }
  return apiTestingSelectionByScope.get(key);
}

function apiNormalizeModulePath(value) {
  return String(value || '').replace(/\\/g, '/').split('/').map(part => part.trim()).filter(Boolean).join('/');
}

function apiEndpointModulePath(endpoint = {}) {
  return apiNormalizeModulePath(endpoint.module_path || endpoint.module || '未分组');
}

function apiModulePathMatches(modulePath, parentPath) {
  const module = apiNormalizeModulePath(modulePath);
  const parent = apiNormalizeModulePath(parentPath);
  return !!parent && (module === parent || module.startsWith(`${parent}/`));
}

function apiModuleRows(source = {}, endpoints = []) {
  const rows = new Map();
  const addPath = (value, count = 0) => {
    const path = apiNormalizeModulePath(value);
    if (!path) return;
    const parts = path.split('/');
    parts.forEach((_, index) => {
      const itemPath = parts.slice(0, index + 1).join('/');
      const prior = rows.get(itemPath) || { path: itemPath, parent: parts.slice(0, index).join('/'), depth: index, endpointCount: 0 };
      if (itemPath === path) prior.endpointCount = Math.max(prior.endpointCount, Number(count || 0));
      rows.set(itemPath, prior);
    });
  };
  (source.module_catalog || []).forEach(item => addPath(item.path, item.endpoint_count));
  endpoints.forEach(endpoint => addPath(apiEndpointModulePath(endpoint), 0));
  return Array.from(rows.values()).map(item => ({
    ...item,
    endpointCount: endpoints.filter(endpoint => apiModulePathMatches(apiEndpointModulePath(endpoint), item.path)).length || item.endpointCount
  })).sort((left, right) => left.path.localeCompare(right.path, 'zh-Hans-CN'));
}

function apiModuleCheckState(path, rows, selectedModules) {
  const descendants = rows.filter(row => apiModulePathMatches(row.path, path));
  const selected = descendants.filter(row => selectedModules.has(row.path)).length;
  return { checked: descendants.length > 0 && selected === descendants.length, indeterminate: selected > 0 && selected < descendants.length };
}

function renderApiAssetTable(endpoints, options = {}) {
  if (!endpoints.length) return apiTestingEmpty(options.emptyText || '暂无接口资产。');
  const state = apiModuleSelectionState();
  const allSelected = endpoints.every(endpoint => state.endpointIds.has(String(endpoint.endpoint_id || '')));
  return `
    <table class="assets-table api-endpoint-table">
      <thead><tr><th><input type="checkbox" aria-label="选择当前接口" data-api-endpoint-select-all="1" onchange="toggleApiEndpointSelection(this.checked)" ${allSelected ? 'checked' : ''}></th><th>接口</th><th>模块</th><th>名称</th><th>必填</th><th>Schema</th></tr></thead>
      <tbody>${endpoints.map(endpoint => {
        const endpointId = String(endpoint.endpoint_id || '');
        return `
          <tr>
            <td><input class="api-endpoint-check" type="checkbox" value="${escapeHtml(endpointId)}" ${state.endpointIds.has(endpointId) ? 'checked' : ''} onchange="toggleApiEndpointById(this.value, this.checked)"></td>
            <td><strong>${escapeHtml(apiEndpointLabel(endpoint))}</strong></td>
            <td>${escapeHtml(apiEndpointModulePath(endpoint) || '-')}</td>
            <td>${escapeHtml(endpoint.name || '-')}</td>
            <td>${escapeHtml((endpoint.required_fields || []).join('、') || '-')}</td>
            <td><code>${escapeHtml(endpoint.schema_hash || '-')}</code></td>
          </tr>
        `;
      }).join('')}</tbody>
    </table>
  `;
}

function toggleApiEndpointById(endpointId, checked) {
  const state = apiModuleSelectionState();
  if (checked && !state.endpointIds.has(String(endpointId)) && state.endpointIds.size >= API_PLAN_MAX_ENDPOINTS) {
    showToast(`单次最多选择 ${API_PLAN_MAX_ENDPOINTS} 个接口，请缩小模块或搜索范围`, 'error');
    renderApiModuleEndpointTable();
    return;
  }
  if (checked) state.endpointIds.add(String(endpointId));
  else state.endpointIds.delete(String(endpointId));
  syncApiEndpointCheckboxStates();
  refreshApiAssetActionPanel();
  updateApiWorkflowStepper();
}

function toggleApiEndpointSelection(checked) {
  const state = apiModuleSelectionState();
  const checks = Array.from(document.querySelectorAll('.api-endpoint-check'));
  const adding = checks.filter(input => !state.endpointIds.has(String(input.value))).length;
  if (checked && state.endpointIds.size + adding > API_PLAN_MAX_ENDPOINTS) {
    showToast(`当前范围有 ${checks.length} 个接口，单次最多生成 ${API_PLAN_MAX_ENDPOINTS} 个。请继续选择子模块或搜索接口。`, 'error');
    syncApiEndpointCheckboxStates();
    return;
  }
  document.querySelectorAll('.api-endpoint-check').forEach(input => {
    if (checked) state.endpointIds.add(String(input.value));
    else state.endpointIds.delete(String(input.value));
  });
  syncApiEndpointCheckboxStates();
  refreshApiAssetActionPanel();
  updateApiWorkflowStepper();
}

function syncApiEndpointCheckboxStates() {
  const checks = Array.from(document.querySelectorAll('.api-endpoint-check'));
  const selectAll = document.querySelector('[data-api-endpoint-select-all]');
  if (!selectAll) return;
  const selected = checks.filter(input => input.checked).length;
  selectAll.checked = checks.length > 0 && selected === checks.length;
  selectAll.indeterminate = selected > 0 && selected < checks.length;
}

async function showApiAssetsPage() {
  stopApiAssetSyncPolling();
  const area = setApiTestingPage('api_assets', '接口资产', '从 Apifox 同步 OpenAPI 版本，查看真实差异和受影响计划。');
  if (!area) return;
  area.innerHTML = `
    <div class="api-testing-page api-asset-console">
      <div id="api-workflow-stepper">${renderApiWorkflowStepper({workflow: 'api_assets'})}</div>
      <header class="api-asset-header">
        <div class="workflow-kicker">API ASSET · APIFOX / OPENAPI</div>
        <h2>接口资产</h2>
        <p>选择业务线和模块后生成测试用例。接口由服务端自动从 Apifox 更新。</p>
        <div id="api-source-summary">${apiTestingEmpty('正在读取 Apifox 来源...')}</div>
      </header>
      <section id="api-source-settings-panel" class="api-source-settings" hidden></section>
      <section id="api-assets-sync" class="api-asset-sync-region"></section>
      <div id="api-assets-body">${apiTestingEmpty('正在读取接口资产...')}</div>
      <details class="api-upload-fallback">
        <summary>备用：上传 OpenAPI JSON</summary>
        <div class="api-upload-row">
          <input id="api-openapi-name" placeholder="快照名称">
          <label class="btn-sm api-file-button">选择 JSON<input id="api-openapi-file" type="file" accept=".json,application/json" onchange="handleApiOpenApiFile(this)"></label>
        </div>
        <div id="api-assets-status" class="generate-status"></div>
      </details>
    </div>
  `;
  await refreshApiAssetWorkspace(true);
}

function apiAssetSyncTerminal(sync) {
  return ['succeeded', 'no_change', 'failed', 'cancelled'].includes(String(sync?.status || '').toLowerCase());
}

function apiAssetSyncStatusText(status) {
  return ({ queued: '排队中', running: '同步中', succeeded: '同步完成', no_change: '无变化', failed: '同步失败', cancelled: '已取消' })[status] || status || '未同步';
}

function apiAssetSyncStatusClass(status) {
  if (['succeeded', 'no_change'].includes(status)) return 'success';
  if (status === 'failed') return 'danger';
  return 'warn';
}

function apiAssetSyncPhaseText(phase) {
  return ({
    fetch_source: '读取 Apifox',
    parse_document: '解析 OpenAPI',
    persist_revision: '保存不可变版本',
    diff_revision: '计算版本差异',
    analyze_impact: '分析计划影响'
  })[phase] || phase || '等待同步';
}

function apiAssetSyncLogKey(syncId) {
  return `api-asset-sync::${syncId || 'none'}`;
}

function toggleApiAssetSyncLog(syncId, open) {
  const key = apiAssetSyncLogKey(syncId);
  if (open) apiAssetSyncExpandedKeys.add(key);
  else apiAssetSyncExpandedKeys.delete(key);
  localStorage.setItem('api_asset_sync_expanded_keys', JSON.stringify(Array.from(apiAssetSyncExpandedKeys)));
}

function captureApiAssetSyncViewState(root = document) {
  const area = document.getElementById('editor-area');
  if (area) apiAssetPageScrollTop = area.scrollTop;
  if (!root?.querySelectorAll) return;
  root.querySelectorAll('[data-api-asset-log-key]').forEach(detail => {
    const key = detail.dataset.apiAssetLogKey || '';
    if (detail.open) apiAssetSyncExpandedKeys.add(key);
    else apiAssetSyncExpandedKeys.delete(key);
    const content = detail.querySelector('.api-asset-sync-log');
    if (content) apiAssetSyncScrollPositions.set(key, content.scrollTop);
  });
}

function restoreApiAssetSyncViewState(root = document) {
  if (root?.querySelectorAll) {
    root.querySelectorAll('[data-api-asset-log-key]').forEach(detail => {
      const key = detail.dataset.apiAssetLogKey || '';
      detail.open = apiAssetSyncExpandedKeys.has(key);
      const content = detail.querySelector('.api-asset-sync-log');
      if (content) content.scrollTop = apiAssetSyncScrollPositions.get(key) || 0;
    });
  }
  const area = document.getElementById('editor-area');
  if (area) area.scrollTop = apiAssetPageScrollTop;
}

function stopApiAssetSyncPolling() {
  if (apiAssetSyncPollTimer) clearTimeout(apiAssetSyncPollTimer);
  apiAssetSyncPollTimer = null;
}

function apiAssetSyncWorkflowActive() {
  return ['api_dashboard', 'api_assets', 'api_environment'].includes(activeWorkflow);
}

function scheduleApiAssetSyncPoll(sync) {
  stopApiAssetSyncPolling();
  if (!sync?.sync_id || apiAssetSyncTerminal(sync) || !apiAssetSyncWorkflowActive()) return;
  const delay = Math.max(500, Number(sync.poll_after_ms || 1000));
  apiAssetSyncPollTimer = setTimeout(() => pollApiAssetSync(sync.sync_id), delay);
}

function selectedApiAssetSource() {
  return apiTestingSources.find(item => String(item.source_id || '') === String(apiAssetSelectedSourceId || '')) || apiTestingSources[0] || null;
}

function apiSourceDisplayName(source = {}) {
  const metadata = source.provider_metadata || {};
  return metadata.project_name || source.name || source.source_id || 'API 项目';
}

function apiSourceDiscoveryKey(source = {}) {
  if (apiTestingSourceDraftMode) return '__new_apifox_source__';
  return String(source.source_id || '__empty_apifox_source__');
}

function resetApiSourceDiscoveryState(source = {}) {
  apiSourceDiscoveryRequestId += 1;
  const metadata = source.provider_metadata || {};
  const projectId = String(source.project_id || '');
  const branchId = String(source.branch_id || '');
  const environmentId = String(source.environment_id || '');
  const project = projectId ? {
    id: projectId,
    name: metadata.project_name || source.name || '已配置项目',
    description: metadata.project_description || '',
    team: {
      id: metadata.team_id || '',
      name: metadata.team_name || ''
    }
  } : null;
  apiSourceDiscoveryState = {
    sourceKey: apiSourceDiscoveryKey(source),
    status: project ? 'ready' : 'idle',
    projects: [],
    project,
    branches: project ? [{
      id: branchId,
      name: metadata.branch_name || (branchId ? '已配置分支' : '主分支（默认）'),
      is_default: !branchId
    }] : [],
    environments: project ? [{
      id: environmentId,
      name: metadata.environment_name || (environmentId ? '已配置环境' : '不绑定环境'),
      is_default: !environmentId,
      environment_snapshot: source.environment_snapshot || {}
    }] : [],
    error: '',
    errorCode: '',
    manual: false,
    manualSuggested: false,
    search: '',
    retryTarget: 'projects',
    pendingProjectId: '',
    fresh: false
  };
}

function ensureApiSourceDiscoveryState(source = {}) {
  if (apiSourceDiscoveryState.sourceKey !== apiSourceDiscoveryKey(source)) {
    resetApiSourceDiscoveryState(source);
  }
}

function apiSourceDiscoveryBusy() {
  return ['loading_projects', 'loading_context'].includes(apiSourceDiscoveryState.status);
}

function apiSourceCanConfigure(source = {}) {
  return !!(
    source.source_id
    || apiSourceDiscoveryState.status === 'ready'
    || apiSourceDiscoveryState.manual
  );
}

function apiSourceNamedOptions(items, selectedId) {
  return (items || []).map(item => `
    <option value="${escapeHtml(item.id || '')}" ${String(item.id || '') === String(selectedId || '') ? 'selected' : ''}>
      ${escapeHtml(item.name || '未命名选项')}
    </option>
  `).join('');
}

function apiSourceSelectedDiscoveryEnvironment(source = {}) {
  const selectedId = String(document.getElementById('api-source-environment-select')?.value || source.environment_id || '');
  return (apiSourceDiscoveryState.environments || []).find(
    item => String(item.id || '') === selectedId
  ) || {};
}

function apiSourceEnvironmentSnapshot(source = {}) {
  const selectedEnvironment = apiSourceSelectedDiscoveryEnvironment(source);
  return selectedEnvironment.environment_snapshot || source.environment_snapshot || {};
}

function renderApiSourceEnvironmentSnapshot(source = {}) {
  if (apiEnvironmentSnapshotEditing) return renderApiEnvironmentSnapshotEditor(source);
  const snapshot = apiSourceEnvironmentSnapshot(source);
  const baseUrls = Array.isArray(snapshot.base_urls) ? snapshot.base_urls : [];
  const variables = Array.isArray(snapshot.variables) ? snapshot.variables : [];
  const variableCount = Number(snapshot.variable_count ?? variables.length ?? 0);
  const sensitiveCount = Number(snapshot.sensitive_variable_count ?? variables.filter(item => item?.sensitive).length ?? 0);
  const canSync = !!(source.source_id && (baseUrls.length || variables.length));
  const baseUrlRows = baseUrls.length ? baseUrls.map(item => `
    <li><span>${escapeHtml(item.name || 'default')}</span><code>${escapeHtml(item.url || '')}</code></li>
  `).join('') : '<li class="muted">未读取到环境服务地址</li>';
  const variableRows = variables.length ? variables.slice(0, 12).map(item => `
    <li>
      <span>${escapeHtml(item.name || '未命名变量')}</span>
      <code>${item.sensitive ? '敏感值未同步' : escapeHtml(item.value || '空值')}</code>
    </li>
  `).join('') : '<li class="muted">未读取到普通环境变量</li>';
  const hiddenCount = Math.max(0, variables.length - 12);
  return `
    <section id="api-source-environment-snapshot" class="api-source-environment-snapshot">
      <div class="api-source-environment-head">
        <div>
          <span>APIFOX 环境配置</span>
          <h4>Apifox 环境配置</h4>
          <small>${baseUrls.length} 个服务地址 · ${variableCount} 个变量 · ${sensitiveCount} 个敏感值未同步</small>
        </div>
        <div class="api-source-environment-actions">
          <button type="button" class="btn-sm" onclick="editApiEnvironmentSnapshot()" ${source.source_id ? '' : 'disabled'}>编辑本地快照</button>
          <button type="button" class="btn-sm primary" onclick="useApiSourceEnvironmentSnapshot()" ${canSync ? '' : 'disabled'}>使用该环境执行</button>
        </div>
      </div>
      <div class="api-source-environment-grid">
        <div><strong>服务地址</strong><ul>${baseUrlRows}</ul></div>
        <div><strong>环境变量</strong><ul>${variableRows}${hiddenCount ? `<li class="muted">另有 ${hiddenCount} 个变量未展开</li>` : ''}</ul></div>
      </div>
    </section>
  `;
}

function renderApiEnvironmentBaseUrlRow(item = {}, index = 0) {
  return `
    <div class="api-env-form-row" data-api-env-row>
      <input type="text" data-api-env-name value="${escapeHtml(item.name || (index === 0 ? 'default' : ''))}" placeholder="例如：测试环境">
      <input type="text" data-api-env-url value="${escapeHtml(item.url || '')}" placeholder="https://api.example.com">
      <button type="button" class="btn-sm" onclick="removeApiEnvironmentSnapshotRow(this)">删除</button>
    </div>
  `;
}

function renderApiEnvironmentVariableRow(item = {}) {
  return `
    <div class="api-env-form-row" data-api-env-row>
      <input type="text" data-api-env-name value="${escapeHtml(item.name || '')}" placeholder="变量名，例如 tenantId">
      <input type="text" data-api-env-value value="${escapeHtml(item.sensitive ? '' : (item.value || ''))}" placeholder="${item.sensitive ? '敏感值不展示' : '变量值'}">
      <label class="api-env-sensitive"><input type="checkbox" data-api-env-sensitive ${item.sensitive ? 'checked' : ''}> 敏感</label>
      <button type="button" class="btn-sm" onclick="removeApiEnvironmentSnapshotRow(this)">删除</button>
    </div>
  `;
}

function renderApiEnvironmentSnapshotEditor(source = {}) {
  const snapshot = apiSourceEnvironmentSnapshot(source);
  const baseUrls = Array.isArray(snapshot.base_urls) && snapshot.base_urls.length ? snapshot.base_urls : [{name: 'default', url: ''}];
  const variables = Array.isArray(snapshot.variables) && snapshot.variables.length ? snapshot.variables : [{name: '', value: '', sensitive: false}];
  return `
    <section id="api-source-environment-snapshot" class="api-source-environment-snapshot api-env-snapshot-editor">
      <div class="api-source-environment-head">
        <div>
          <span>本地环境快照</span>
          <h4>编辑执行环境</h4>
          <small>从 Apifox 拉下来的环境可在平台本地改，用于执行测试；保存后不反写 Apifox。</small>
        </div>
        <div class="api-source-environment-actions">
          <button type="button" class="btn-sm" onclick="cancelApiEnvironmentSnapshotEdit()">取消</button>
          <button type="button" class="btn-sm primary" onclick="saveApiEnvironmentSnapshotEdit()">保存本地环境</button>
        </div>
      </div>
      <div class="api-env-editor-grid">
        <div class="api-env-form-table">
          <header><strong>服务地址</strong><button type="button" class="btn-sm" onclick="addApiEnvironmentBaseUrlRow()">添加服务地址</button></header>
          <div class="api-env-form-head"><span>名称</span><span>Base URL</span><span>操作</span></div>
          <div data-api-env-base-url-list>${baseUrls.map((item, index) => renderApiEnvironmentBaseUrlRow(item, index)).join('')}</div>
        </div>
        <div class="api-env-form-table">
          <header><strong>环境变量</strong><button type="button" class="btn-sm" onclick="addApiEnvironmentVariableRow()">添加变量</button></header>
          <div class="api-env-form-head"><span>变量名</span><span>变量值</span><span>敏感</span><span>操作</span></div>
          <div data-api-env-variable-list>${variables.map(item => renderApiEnvironmentVariableRow(item)).join('')}</div>
        </div>
      </div>
      <p class="api-env-editor-hint">敏感变量名如 Authorization、token、password 会由服务端脱敏保存；业务登录 token 请继续在“环境公共鉴权”里配置。</p>
    </section>
  `;
}

function editApiEnvironmentSnapshot() {
  apiEnvironmentSnapshotEditing = true;
  const region = document.getElementById('api-source-environment-snapshot');
  if (region) region.outerHTML = renderApiEnvironmentSnapshotEditor(selectedApiAssetSource() || {});
}

function cancelApiEnvironmentSnapshotEdit() {
  apiEnvironmentSnapshotEditing = false;
  const region = document.getElementById('api-source-environment-snapshot');
  if (region) region.outerHTML = renderApiSourceEnvironmentSnapshot(selectedApiAssetSource() || {});
}

function addApiEnvironmentBaseUrlRow() {
  document.querySelector('[data-api-env-base-url-list]')
    ?.insertAdjacentHTML('beforeend', renderApiEnvironmentBaseUrlRow({name: '', url: ''}));
}

function addApiEnvironmentVariableRow() {
  document.querySelector('[data-api-env-variable-list]')
    ?.insertAdjacentHTML('beforeend', renderApiEnvironmentVariableRow({name: '', value: '', sensitive: false}));
}

function removeApiEnvironmentSnapshotRow(button) {
  button?.closest('[data-api-env-row]')?.remove();
}

function readApiEnvironmentSnapshotRows() {
  const baseUrls = Array.from(document.querySelectorAll('[data-api-env-base-url-list] [data-api-env-row]'))
    .map(row => ({
      name: row.querySelector('[data-api-env-name]')?.value.trim() || 'default',
      url: row.querySelector('[data-api-env-url]')?.value.trim() || ''
    }))
    .filter(item => item.url);
  const variables = Array.from(document.querySelectorAll('[data-api-env-variable-list] [data-api-env-row]'))
    .map(row => ({
      name: row.querySelector('[data-api-env-name]')?.value.trim() || '',
      value: row.querySelector('[data-api-env-value]')?.value.trim() || '',
      sensitive: !!row.querySelector('[data-api-env-sensitive]')?.checked,
      scope: 'environment'
    }))
    .filter(item => item.name);
  if (!baseUrls.length) throw new Error('至少填写一个 Base URL');
  return {base_urls: baseUrls, variables};
}

async function saveApiEnvironmentSnapshotEdit() {
  const source = selectedApiAssetSource() || {};
  if (!source.source_id) {
    showToast('请先保存 Apifox 来源', 'error');
    return;
  }
  let environmentSnapshot;
  try {
    environmentSnapshot = readApiEnvironmentSnapshotRows();
  } catch (error) {
    showToast(`环境配置不完整：${error.message || error}`, 'error');
    return;
  }
  try {
    const data = await apiRequest(`/api-testing/sources/${encodeURIComponent(source.source_id)}/environment-snapshot`, {
      method: 'POST',
      body: {
        environment_snapshot: environmentSnapshot
      }
    });
    apiEnvironmentSnapshotEditing = false;
    if (data.source?.source_id) apiAssetSelectedSourceId = data.source.source_id;
    showToast(data.message || '✓ 本地环境快照已保存', 'success');
    if (activeWorkflow === 'api_environment') await showApiEnvironmentPage();
    else await refreshApiAssetWorkspace(true);
  } catch (error) {
    showToast(error.message || '本地环境快照保存失败', 'error');
  }
}

function apiSourceEnvironmentSummary(source = {}) {
  const snapshot = source.environment_snapshot || {};
  const baseUrls = Array.isArray(snapshot.base_urls) ? snapshot.base_urls : [];
  const variables = Array.isArray(snapshot.variables) ? snapshot.variables : [];
  const firstBaseUrl = baseUrls.find(item => String(item?.url || '').trim()) || {};
  return {
    baseUrls,
    variables,
    baseUrl: String(firstBaseUrl.url || '').trim(),
    baseUrlName: String(firstBaseUrl.name || '').trim(),
    variableCount: Number(snapshot.variable_count ?? variables.length ?? 0),
    sensitiveCount: Number(snapshot.sensitive_variable_count ?? variables.filter(item => item?.sensitive).length ?? 0),
  };
}

function renderApiSourceEnvironmentCompact(source = {}) {
  const metadata = source.provider_metadata || {};
  const summary = apiSourceEnvironmentSummary(source);
  return `
    <div class="api-source-env-compact">
      <div>
        <span>Apifox 项目</span>
        <strong>${escapeHtml(apiSourceDisplayName(source))}</strong>
        <small>${escapeHtml(metadata.team_name || source.project_id || '-')}</small>
      </div>
      <div>
        <span>当前环境</span>
        <strong>${escapeHtml(metadata.environment_name || source.environment_id || '未选择环境')}</strong>
        <small>${escapeHtml(summary.baseUrlName || 'baseUrl')}</small>
      </div>
      <div class="${summary.baseUrl ? '' : 'warn'}">
        <span>服务地址</span>
        <strong>${escapeHtml(summary.baseUrl || '未读取到 base_url')}</strong>
        <small>${summary.baseUrl ? '执行时使用该地址' : '请重新读取 Apifox 环境'}</small>
      </div>
      <div>
        <span>环境变量</span>
        <strong>${escapeHtml(summary.variableCount)} 个</strong>
        <small>${escapeHtml(summary.sensitiveCount)} 个敏感值不展示</small>
      </div>
    </div>
  `;
}

function refreshApiSourceEnvironmentSnapshotPreview(source = currentApiSourceSettingsSource()) {
  const node = document.getElementById('api-source-environment-snapshot');
  if (node) node.outerHTML = renderApiSourceEnvironmentSnapshot(source);
}

function apiSourceProjectResultsHtml() {
  const query = String(apiSourceDiscoveryState.search || '').trim().toLocaleLowerCase();
  const projects = (apiSourceDiscoveryState.projects || []).filter(project => {
    if (!query) return true;
    return [
      project.name,
      project.description,
      project.team?.name
    ].some(value => String(value || '').toLocaleLowerCase().includes(query));
  });
  if (!projects.length) {
    return '<div class="api-source-discovery-state empty"><strong>未找到匹配项目</strong></div>';
  }
  return projects.map(project => `
    <button type="button" class="api-source-project-option" onclick="selectApiSourceDiscoveredProject(${jsArg(project.id)})">
      <span><strong>${escapeHtml(project.name || '未命名项目')}</strong><small>${escapeHtml(project.team?.name || '未标注团队')}</small></span>
      ${project.description ? `<em>${escapeHtml(project.description)}</em>` : ''}
      <b>选择</b>
    </button>
  `).join('');
}

function renderApiSourceDiscoveryState(source = {}) {
  const state = apiSourceDiscoveryState;
  if (state.status === 'loading_projects') {
    return '<div class="api-source-discovery-state loading"><span class="spinner"></span><strong>正在读取项目列表</strong></div>';
  }
  if (state.status === 'loading_context') {
    return '<div class="api-source-discovery-state loading"><span class="spinner"></span><strong>正在读取分支和环境</strong></div>';
  }
  if (state.status === 'error') {
    return `
      <div class="api-source-discovery-state error">
        <div><strong>读取失败</strong><span>${escapeHtml(state.error || 'Apifox 资产读取失败')}</span></div>
        <div class="api-source-discovery-state-actions">
          <button type="button" class="btn-sm" onclick="retryApiSourceDiscovery()">重试</button>
          <button type="button" class="btn-sm" onclick="openApiSourceManualFallback()">手动连接</button>
        </div>
      </div>
    `;
  }
  if (state.status === 'project_selection') {
    return `
      <div class="api-source-project-picker">
        <label><span>选择项目</span><input id="api-source-project-search" type="search" value="${escapeHtml(state.search || '')}" placeholder="搜索项目或团队" oninput="filterApiSourceProjects(this.value)"></label>
        <div id="api-source-project-results" class="api-source-project-results">${apiSourceProjectResultsHtml()}</div>
      </div>
    `;
  }
  if (state.status === 'ready' && state.project) {
    const branchId = String(source.branch_id || state.branches?.[0]?.id || '');
    const environmentId = String(source.environment_id || state.environments?.[0]?.id || '');
    return `
      <div class="api-source-selected-project">
        <div><span>项目</span><strong>${escapeHtml(state.project.name || '未命名项目')}</strong><small>${escapeHtml(state.project.team?.name || '未标注团队')}</small></div>
        ${state.projects.length ? '<button type="button" class="btn-sm" onclick="showApiSourceProjectSelection()">更换项目</button>' : ''}
      </div>
      <div class="api-source-context-grid">
        <label><span>分支</span><select id="api-source-branch-select">${apiSourceNamedOptions(state.branches, branchId)}</select></label>
        <label><span>环境</span><select id="api-source-environment-select" onchange="refreshApiSourceEnvironmentSnapshotPreview()">${apiSourceNamedOptions(state.environments, environmentId)}</select></label>
      </div>
    `;
  }
  return '<div class="api-source-discovery-state idle"><strong>等待读取 Apifox 项目</strong></div>';
}

function renderApiSourceCredentialControl(source = {}) {
  const globalCredential = !source.source_id && apiApifoxCredential?.credential_configured === true;
  const credentialConfigured = source.credential_configured === true || globalCredential;
  const credentialEditorOpen = !credentialConfigured || apiSourceCredentialEditing;
  const savedTitle = globalCredential ? '平台令牌已保存' : '已安全保存';
  const savedHint = globalCredential ? '新增项目会自动复用服务端令牌' : '密钥仅保存在服务端';
  return `
    <div class="api-source-field api-source-token-field">
      <span>访问令牌</span>
      <div id="api-source-credential-saved" class="api-source-credential-saved" ${credentialEditorOpen ? 'hidden' : ''}>
        <div class="api-source-credential-state"><span aria-hidden="true">✓</span><div><strong>${escapeHtml(savedTitle)}</strong><small>${escapeHtml(savedHint)}</small></div></div>
        <button type="button" class="btn-sm" aria-label="更换 Apifox 访问令牌" onclick="editApiSourceCredential()">更换</button>
      </div>
      <div id="api-source-token-editor" class="api-source-token-editor" ${credentialEditorOpen ? '' : 'hidden'}>
        <input id="api-source-token" type="password" value="" autocomplete="new-password" aria-label="Apifox 访问令牌" placeholder="${credentialConfigured ? '输入新的 Apifox Access Token' : '输入 Apifox Access Token'}" oninput="handleApiSourceTokenInput()">
        <button type="button" class="btn-sm" onclick="saveApiGlobalApifoxCredential()">单独保存令牌</button>
        ${credentialConfigured ? '<button type="button" class="btn-sm" aria-label="取消更换 Apifox 访问令牌" onclick="cancelApiSourceCredentialEdit()">取消</button>' : ''}
      </div>
    </div>
  `;
}

function renderApiSourceManualFallback(source = {}) {
  const metadata = source.provider_metadata || {};
  return `
    <details id="api-source-manual-fallback" class="api-source-manual-fallback" ${apiSourceDiscoveryState.manual ? 'open' : ''} ontoggle="toggleApiSourceManualFallback(this.open)">
      <summary><span>无法读取？手动连接</span><small>技术兜底</small></summary>
      <div id="api-source-manual-fields" class="api-source-manual-fields">
        <label><span>来源名称</span><input id="api-source-name" value="${escapeHtml(source.name || metadata.project_name || 'Apifox 接口')}" placeholder="例如：3D 接口"></label>
        <label><span>项目 ID</span><input id="api-source-project-id" value="${escapeHtml(source.project_id || '')}" inputmode="numeric" placeholder="Project ID"></label>
        <label><span>分支 ID（可选）</span><input id="api-source-branch-id" value="${escapeHtml(source.branch_id || '')}" placeholder="默认主分支"></label>
        <label><span>环境 ID（可选）</span><input id="api-source-environment-id" value="${escapeHtml(source.environment_id || '')}" inputmode="numeric" placeholder="Environment ID"></label>
      </div>
    </details>
  `;
}

function renderApiProjectSelector(sources, selectedId, context = 'assets') {
  const options = (sources || []).map(source => {
    const label = apiSourceDisplayName(source);
    return `<option value="${escapeHtml(source.source_id || '')}" ${String(source.source_id || '') === String(selectedId || '') ? 'selected' : ''}>${escapeHtml(label)}</option>`;
  }).join('');
  const changeHandler = context === 'baselines' ? 'selectApiBaselineSource' : 'selectApiAssetSource';
  const addButton = context === 'baselines'
    ? ''
    : '<button class="btn-sm icon-only api-project-add" type="button" title="新增 Apifox 项目" aria-label="新增 Apifox 项目" onclick="startApiSourceDraft()">＋</button>';
  return `<div class="api-project-switcher"><select class="api-project-select" aria-label="选择 Apifox 项目" onchange="${changeHandler}(this.value)">${options}</select>${addButton}</div>`;
}

function renderSavedApiSourceShelf(sources = [], selectedId = '', context = 'assets') {
  const rows = (sources || []).filter(source => source && source.source_id);
  const selectHandler = context === 'execution'
    ? 'selectApiExecutionSource'
    : (context === 'workbench' ? 'apiWorkbenchSelectSource' : 'selectApiAssetSource');
  if (!rows.length) {
    return `
      <section class="api-saved-source-shelf">
        <div><span>已保存 Apifox 项目</span><strong>暂无本地项目</strong><small>连接一次 Apifox 后会保存项目、环境和接口快照。</small></div>
      </section>
    `;
  }
  return `
    <section class="api-saved-source-shelf">
      <div class="api-saved-source-head">
        <div><span>已保存 Apifox 项目</span><strong>本地项目快照</strong><small>同事可直接切换已有项目；需要更新时再手动重新读取 Apifox。</small></div>
        <button class="btn-sm" type="button" onclick="startApiSourceDraft()">新增项目</button>
      </div>
      <div class="api-saved-source-list">
        ${rows.map(source => {
          const active = String(source.source_id || '') === String(selectedId || '');
          const summary = apiSourceEnvironmentSummary(source);
          const meta = source.provider_metadata || {};
          return `
            <button type="button" class="api-saved-source-card ${active ? 'active' : ''}" onclick="${selectHandler}(${jsArg(source.source_id)})">
              <span>${escapeHtml(apiSourceDisplayName(source))}</span>
              <strong>${escapeHtml(meta.environment_name || source.environment_id || '未选择环境')}</strong>
              <small>${escapeHtml(summary.baseUrl || '未读取到 base_url')}</small>
            </button>
          `;
        }).join('')}
      </div>
    </section>
  `;
}

function renderApiSourceSummary(source, latestSync, snapshot = {}) {
  const configured = source?.configured === true;
  const status = latestSync?.status || source?.last_sync_status || '';
  const schedule = source?.sync_schedule || {};
  const syncDisabled = !configured || ['queued', 'running'].includes(status);
  const running = ['queued', 'running'].includes(status);
  const primaryLabel = status === 'failed' ? '重试读取' : '重新读取 Apifox 资产';
  return `
    ${renderSavedApiSourceShelf(apiTestingSources, source?.source_id)}
    <div class="api-asset-context-bar">
      <div class="api-source-status-row">
        <div class="api-source-identity">
          ${renderApiProjectSelector(apiTestingSources, source?.source_id)}
          ${apiStatusPill(configured ? '已连接' : '待配置', configured ? 'success' : 'warn')}
          <span>${source?.credential_configured ? '访问凭据已安全保存；重新读取会同步接口、环境和 base_url' : '需要配置 Apifox 项目和访问令牌'}</span>
        </div>
        <div class="api-source-actions">
          <button class="btn-sm primary" onclick="startApiAssetSync()" ${syncDisabled ? 'disabled' : ''}>${escapeHtml(running ? '正在读取' : primaryLabel)}</button>
          <button class="btn-sm icon-only" title="刷新接口资产" aria-label="刷新接口资产" onclick="refreshApiAssetWorkspace(true)">↻</button>
          <button class="btn-sm icon-only" title="Apifox 来源设置" aria-label="Apifox 来源设置" onclick="toggleApiSourceSettings()">⚙</button>
        </div>
      </div>
      ${renderApiSourceEnvironmentCompact(source || {})}
      <div class="api-source-facts">
        <span><small>自动同步</small><strong>${schedule.mode === 'automatic' ? '已开启' : '未开启'}</strong></span>
        <span><small>最近成功</small><strong>${escapeHtml(schedule.last_success_at || source?.last_success_at || '等待首次同步')}</strong></span>
        <span><small>下次检查</small><strong>${escapeHtml(schedule.next_check_at || '手动同步')}</strong></span>
        <span><small>当前状态</small><strong>${escapeHtml(apiAssetSyncStatusText(status))}</strong></span>
      </div>
    </div>
    ${source?.last_error ? `<div class="api-inline-error">${escapeHtml(source.last_error)}</div>` : ''}
  `;
}

function renderApiSourceSettings(source = {}) {
  ensureApiSourceDiscoveryState(source);
  const scope = source.sync_scope || { mode: 'all', module_paths: [] };
  const scopeState = apiModuleSelectionState();
  const selectedModules = apiTestingSourceDraftMode ? [] : (scopeState.selectedModules.size ? Array.from(scopeState.selectedModules) : (scope.module_paths || []));
  const selectedSummary = selectedModules.length ? selectedModules.join('、') : '尚未选择模块';
  const canConfigure = apiSourceCanConfigure(source);
  const discoveryActionLabel = source.source_id ? '重新读取 Apifox 资产' : '读取 Apifox 资产';
  return `
    <div class="api-source-settings-head"><div><span>APIFOX SOURCE</span><h3>${apiTestingSourceDraftMode ? '新增 Apifox 项目' : '只读同步设置'}</h3></div><button class="btn-sm icon-only" title="${apiTestingSourceDraftMode ? '取消新增 Apifox 项目' : '关闭设置'}" aria-label="${apiTestingSourceDraftMode ? '取消新增 Apifox 项目' : '关闭设置'}" onclick="${apiTestingSourceDraftMode ? 'cancelApiSourceDraft()' : 'toggleApiSourceSettings(false)'}">×</button></div>
    <div class="api-source-discovery">
      <div class="api-source-discovery-action">
        ${renderApiSourceCredentialControl(source)}
        <button id="api-source-discovery-button" type="button" class="btn-sm primary" onclick="discoverApiSourceProjects()" ${apiSourceDiscoveryBusy() ? 'disabled' : ''}>${escapeHtml(discoveryActionLabel)}</button>
      </div>
      <div id="api-source-discovery-state">${renderApiSourceDiscoveryState(source)}</div>
    </div>
    ${renderApiSourceManualFallback(source)}
    ${renderApiSourceEnvironmentSnapshot(source)}
    <div id="api-source-sync-configuration" class="api-source-sync-configuration ${canConfigure ? '' : 'hidden'}">
      <div class="api-source-settings-grid">
        <label><span>同步周期（分钟）</span><input id="api-source-interval" type="number" min="15" max="1440" step="15" value="${escapeHtml(source.sync_interval_minutes || 60)}"></label>
        <label class="api-source-toggle"><input id="api-source-sync-enabled" type="checkbox" ${source.sync_enabled !== false ? 'checked' : ''}><span>启用定时同步</span></label>
      </div>
      <div class="api-source-scope" data-api-source-scope>
        <span>同步范围</span>
        <div class="api-segmented-control" role="group" aria-label="Apifox 同步范围">
          <button type="button" class="${scope.mode !== 'selected' ? 'active' : ''}" data-sync-scope="all" onclick="setApiSourceSyncScopeMode('all')">全部模块</button>
          <button type="button" class="${scope.mode === 'selected' ? 'active' : ''}" data-sync-scope="selected" onclick="setApiSourceSyncScopeMode('selected')">已选模块</button>
        </div>
        <small id="api-source-selected-modules">${escapeHtml(selectedSummary)}</small>
      </div>
      <div class="api-source-settings-actions">
        ${source.credential_configured ? '<button class="btn-sm danger" onclick="clearApiSourceCredential()">清除当前令牌</button>' : ''}
        <button id="api-source-save-button" class="btn-sm primary" onclick="saveApiSourceConfig()" ${canConfigure ? '' : 'disabled'}>保存设置</button>
      </div>
    </div>
  `;
}

function currentApiSourceSettingsSource() {
  return apiTestingSourceDraftMode ? {} : (selectedApiAssetSource() || {});
}

function refreshApiSourceDiscoveryUi(source = currentApiSourceSettingsSource()) {
  const stateRegion = document.getElementById('api-source-discovery-state');
  if (stateRegion) stateRegion.innerHTML = renderApiSourceDiscoveryState(source);
  const discoveryButton = document.getElementById('api-source-discovery-button');
  if (discoveryButton) {
    discoveryButton.disabled = apiSourceDiscoveryBusy();
    discoveryButton.textContent = apiSourceDiscoveryBusy()
      ? '正在读取'
      : (source.source_id ? '重新读取 Apifox 资产' : '读取 Apifox 资产');
  }
  const canConfigure = apiSourceCanConfigure(source);
  document.getElementById('api-source-sync-configuration')?.classList.toggle('hidden', !canConfigure);
  const saveButton = document.getElementById('api-source-save-button');
  if (saveButton) saveButton.disabled = !canConfigure;
  refreshApiSourceEnvironmentSnapshotPreview(source);
}

function apiSourceDiscoveryCredentialPayload(source = currentApiSourceSettingsSource()) {
  const token = document.getElementById('api-source-token')?.value.trim() || '';
  if (token) return { access_token: token };
  if (source.source_id) return { source_id: source.source_id };
  if (apiApifoxCredential?.credential_configured) return {};
  throw new Error('请输入 Apifox 访问令牌');
}

function apiSourceHasReusableCredential(source = currentApiSourceSettingsSource()) {
  return source.credential_configured === true || apiApifoxCredential?.credential_configured === true;
}

async function saveApiGlobalApifoxCredential() {
  const input = document.getElementById('api-source-token');
  const token = input?.value.trim() || '';
  if (!token) {
    showToast('请输入 Apifox 访问令牌', 'error');
    input?.focus();
    return;
  }
  try {
    const data = await apiRequest('/api-testing/apifox/credential', {
      method: 'POST',
      body: { access_token: token }
    });
    apiApifoxCredential = data.credential || {};
    if (input) input.value = '';
    apiSourceCredentialEditing = false;
    const source = currentApiSourceSettingsSource();
    resetApiSourceDiscoveryState(source);
    const panel = document.getElementById('api-source-settings-panel');
    if (panel) panel.innerHTML = renderApiSourceSettings(source);
    refreshApiSourceDiscoveryUi(source);
    showToast('✓ Apifox 令牌已单独保存', 'success');
  } catch (error) {
    showToast(error.message || 'Apifox 令牌保存失败', 'error');
  }
}

function setApiSourceDiscoveryError(error, retryTarget, projectId = '') {
  apiSourceDiscoveryState.status = 'error';
  apiSourceDiscoveryState.error = error?.message || 'Apifox 资产读取失败';
  apiSourceDiscoveryState.errorCode = error?.code || '';
  apiSourceDiscoveryState.manualSuggested = true;
  apiSourceDiscoveryState.retryTarget = retryTarget || 'projects';
  apiSourceDiscoveryState.pendingProjectId = projectId || '';
  apiSourceDiscoveryState.fresh = false;
  refreshApiSourceDiscoveryUi();
}

function handleApiSourceTokenInput() {
  const source = currentApiSourceSettingsSource();
  const token = document.getElementById('api-source-token')?.value || '';
  const manual = apiSourceDiscoveryState.manual;
  if (!token.trim() && apiSourceHasReusableCredential(source)) {
    resetApiSourceDiscoveryState(source);
  } else {
    resetApiSourceDiscoveryState({});
    apiSourceDiscoveryState.sourceKey = apiSourceDiscoveryKey(source);
  }
  apiSourceDiscoveryState.manual = manual;
  refreshApiSourceDiscoveryUi(source);
}

function filterApiSourceProjects(value) {
  apiSourceDiscoveryState.search = String(value || '');
  const results = document.getElementById('api-source-project-results');
  if (results) results.innerHTML = apiSourceProjectResultsHtml();
}

function showApiSourceProjectSelection() {
  apiSourceDiscoveryState.status = 'project_selection';
  apiSourceDiscoveryState.project = null;
  apiSourceDiscoveryState.branches = [];
  apiSourceDiscoveryState.environments = [];
  apiSourceDiscoveryState.fresh = false;
  refreshApiSourceDiscoveryUi();
  document.getElementById('api-source-project-search')?.focus();
}

function toggleApiSourceManualFallback(open) {
  apiSourceDiscoveryState.manual = !!open;
  const source = currentApiSourceSettingsSource();
  const canConfigure = apiSourceCanConfigure(source);
  document.getElementById('api-source-sync-configuration')?.classList.toggle('hidden', !canConfigure);
  const saveButton = document.getElementById('api-source-save-button');
  if (saveButton) saveButton.disabled = !canConfigure;
}

function openApiSourceManualFallback() {
  const details = document.getElementById('api-source-manual-fallback');
  apiSourceDiscoveryState.manual = true;
  if (details) details.open = true;
  toggleApiSourceManualFallback(true);
  document.getElementById('api-source-project-id')?.focus();
}

async function discoverApiSourceProjects() {
  const source = currentApiSourceSettingsSource();
  let credentials;
  try {
    credentials = apiSourceDiscoveryCredentialPayload(source);
  } catch (error) {
    showToast(error.message, 'error');
    document.getElementById('api-source-token')?.focus();
    return;
  }
  const requestId = ++apiSourceDiscoveryRequestId;
  apiSourceDiscoveryState.status = 'loading_projects';
  apiSourceDiscoveryState.projects = [];
  apiSourceDiscoveryState.project = null;
  apiSourceDiscoveryState.branches = [];
  apiSourceDiscoveryState.environments = [];
  apiSourceDiscoveryState.error = '';
  apiSourceDiscoveryState.errorCode = '';
  apiSourceDiscoveryState.retryTarget = 'projects';
  apiSourceDiscoveryState.pendingProjectId = '';
  apiSourceDiscoveryState.fresh = false;
  refreshApiSourceDiscoveryUi(source);
  try {
    const data = await apiRequest('/api-testing/apifox/discovery/projects', {
      method: 'POST',
      body: credentials,
      timeoutMs: 30000
    });
    if (requestId !== apiSourceDiscoveryRequestId || apiSourceDiscoveryState.sourceKey !== apiSourceDiscoveryKey(source)) return;
    apiSourceDiscoveryState.status = 'project_selection';
    apiSourceDiscoveryState.projects = data.projects || [];
    apiSourceDiscoveryState.search = '';
    refreshApiSourceDiscoveryUi(source);
    document.getElementById('api-source-project-search')?.focus();
  } catch (error) {
    if (requestId !== apiSourceDiscoveryRequestId) return;
    setApiSourceDiscoveryError(error, 'projects');
  }
}

async function loadApiSourceProjectContext(projectId) {
  const source = currentApiSourceSettingsSource();
  const selectedProject = (apiSourceDiscoveryState.projects || []).find(
    item => String(item.id || '') === String(projectId || '')
  ) || apiSourceDiscoveryState.project;
  let credentials;
  try {
    credentials = apiSourceDiscoveryCredentialPayload(source);
  } catch (error) {
    setApiSourceDiscoveryError(error, 'context', projectId);
    return;
  }
  const requestId = ++apiSourceDiscoveryRequestId;
  apiSourceDiscoveryState.status = 'loading_context';
  apiSourceDiscoveryState.project = selectedProject || null;
  apiSourceDiscoveryState.error = '';
  apiSourceDiscoveryState.errorCode = '';
  apiSourceDiscoveryState.retryTarget = 'context';
  apiSourceDiscoveryState.pendingProjectId = String(projectId || '');
  apiSourceDiscoveryState.fresh = false;
  refreshApiSourceDiscoveryUi(source);
  try {
    const data = await apiRequest('/api-testing/apifox/discovery/project-context', {
      method: 'POST',
      body: {
        ...credentials,
        project_id: String(projectId || ''),
        environment_id: source.environment_id || ''
      },
      timeoutMs: 35000
    });
    if (requestId !== apiSourceDiscoveryRequestId || apiSourceDiscoveryState.sourceKey !== apiSourceDiscoveryKey(source)) return;
    apiSourceDiscoveryState.status = 'ready';
    apiSourceDiscoveryState.project = data.project || selectedProject || null;
    apiSourceDiscoveryState.branches = data.branches || [];
    apiSourceDiscoveryState.environments = data.environments || [];
    apiSourceDiscoveryState.error = '';
    apiSourceDiscoveryState.errorCode = '';
    apiSourceDiscoveryState.pendingProjectId = '';
    apiSourceDiscoveryState.fresh = true;
    refreshApiSourceDiscoveryUi(source);
  } catch (error) {
    if (requestId !== apiSourceDiscoveryRequestId) return;
    apiSourceDiscoveryState.project = selectedProject || null;
    setApiSourceDiscoveryError(error, 'context', projectId);
  }
}

async function selectApiSourceDiscoveredProject(projectId) {
  await loadApiSourceProjectContext(projectId);
}

async function retryApiSourceDiscovery() {
  if (
    apiSourceDiscoveryState.retryTarget === 'context'
    && apiSourceDiscoveryState.pendingProjectId
  ) {
    await loadApiSourceProjectContext(apiSourceDiscoveryState.pendingProjectId);
    return;
  }
  await discoverApiSourceProjects();
}

function startApiSourceDraft() {
  apiTestingSourceDraftMode = true;
  apiAssetSettingsOpen = true;
  apiSourceCredentialEditing = false;
  resetApiSourceDiscoveryState({});
  const panel = document.getElementById('api-source-settings-panel');
  if (panel) {
    panel.innerHTML = renderApiSourceSettings({ sync_scope: { mode: 'all', module_paths: [] } });
    panel.hidden = false;
  }
}

function cancelApiSourceDraft() {
  apiTestingSourceDraftMode = false;
  apiSourceCredentialEditing = false;
  resetApiSourceDiscoveryState({});
  toggleApiSourceSettings(false);
}

function setApiSourceSyncScopeMode(mode) {
  document.querySelectorAll('[data-sync-scope]').forEach(button => button.classList.toggle('active', button.dataset.syncScope === mode));
  const summary = document.getElementById('api-source-selected-modules');
  if (summary && mode === 'selected' && !apiModuleSelectionState().selectedModules.size) summary.textContent = '请先在模块树中选择模块';
}

function apiSourceSelectedModulePaths(source = {}) {
  if (apiTestingSourceDraftMode) return [];
  const selected = Array.from(apiModuleSelectionState().selectedModules);
  return selected.length ? selected : ((source.sync_scope || {}).module_paths || []).map(apiNormalizeModulePath).filter(Boolean);
}

function updateApiSourceScopePreview() {
  const summary = document.getElementById('api-source-selected-modules');
  if (!summary) return;
  const paths = Array.from(apiModuleSelectionState().selectedModules);
  summary.textContent = paths.length ? paths.join('、') : '尚未选择模块';
}

function editApiSourceCredential() {
  apiSourceCredentialEditing = true;
  const saved = document.getElementById('api-source-credential-saved');
  const editor = document.getElementById('api-source-token-editor');
  if (saved) saved.hidden = true;
  if (editor) editor.hidden = false;
  document.getElementById('api-source-token')?.focus();
}

function cancelApiSourceCredentialEdit() {
  apiSourceCredentialEditing = false;
  const input = document.getElementById('api-source-token');
  const saved = document.getElementById('api-source-credential-saved');
  const editor = document.getElementById('api-source-token-editor');
  if (input) input.value = '';
  if (saved) saved.hidden = false;
  if (editor) editor.hidden = true;
  const source = currentApiSourceSettingsSource();
  resetApiSourceDiscoveryState(source);
  refreshApiSourceDiscoveryUi(source);
}

function renderApiAssetSync(sync) {
  if (!sync?.sync_id) return '';
  const summary = sync.summary || {};
  const events = sync.events || [];
  const key = apiAssetSyncLogKey(sync.sync_id);
  return `
    <div class="api-sync-strip status-${escapeHtml(sync.status || 'queued')}">
      <div class="api-sync-main">
        ${apiStatusPill(apiAssetSyncStatusText(sync.status), apiAssetSyncStatusClass(sync.status))}
        <strong title="${escapeHtml(sync.phase || '')}">${escapeHtml(apiAssetSyncPhaseText(sync.phase))}</strong>
        <span>${escapeHtml(sync.started_at || sync.created_at || '-')} · ${escapeHtml(sync.finished_at ? `完成于 ${sync.finished_at}` : '正在等待真实结果')}</span>
      </div>
      <div class="api-sync-metrics">
        <span><strong>${escapeHtml(summary.added || 0)}</strong>新增</span>
        <span><strong>${escapeHtml(summary.changed || 0)}</strong>变更</span>
        <span><strong>${escapeHtml(summary.removed || 0)}</strong>删除</span>
        <span><strong>${escapeHtml(summary.unchanged || 0)}</strong>未变</span>
        <span><strong>${escapeHtml(summary.affected_plans || 0)}</strong>受影响计划</span>
      </div>
      ${sync.error ? `<div class="api-inline-error">${escapeHtml(sync.error)}</div>` : ''}
      <details class="api-sync-log-detail" data-api-asset-log-key="${escapeHtml(key)}" ontoggle="toggleApiAssetSyncLog(${jsArg(sync.sync_id)}, this.open)">
        <summary><span>技术日志</span><small>${escapeHtml(events.length)} 条真实事件</small></summary>
        <div class="api-asset-sync-log">${events.length ? events.map(event => `
          <div><time>${escapeHtml(event.at || '-')}</time><strong title="${escapeHtml(event.phase || '')}">${escapeHtml(apiAssetSyncPhaseText(event.phase))}</strong><span>${escapeHtml(event.message || '')}</span></div>
        `).join('') : apiTestingEmpty('暂无同步事件')}</div>
      </details>
    </div>
  `;
}

function renderApiModuleTree(source, endpoints) {
  const businessLine = currentApiBusinessLine();
  const scopedEndpoints = businessLine
    ? endpoints.filter(endpoint => apiBusinessLineForEndpoint(endpoint) === businessLine)
    : endpoints;
  const rows = apiModuleRows(source, scopedEndpoints).filter(
    row => !businessLine || row.path === businessLine || row.path.startsWith(`${businessLine}/`)
  );
  const state = apiModuleSelectionState();
  if (!rows.length) return apiTestingEmpty('当前版本没有可选择的业务模块。');
  return `<div class="api-module-tree" role="tree">${rows.map(row => {
    const checkState = apiModuleCheckState(row.path, rows, state.selectedModules);
    const active = state.activeModulePath === row.path;
    return `<div class="api-module-tree-row ${active ? 'active' : ''}" style="padding-left:${10 + Math.min(row.depth, 5) * 14}px">
      <input type="checkbox" data-module-path="${escapeHtml(row.path)}" ${checkState.checked ? 'checked' : ''} data-indeterminate="${checkState.indeterminate ? 'true' : 'false'}" aria-label="选择模块 ${escapeHtml(row.path)}" onchange="toggleApiModuleSelection(this.dataset.modulePath, this.checked)">
      <button type="button" data-module-path="${escapeHtml(row.path)}" onclick="selectApiAssetModule(this.dataset.modulePath)"><span>${escapeHtml(row.path.split('/').pop())}</span><small>${escapeHtml(row.endpointCount)}</small></button>
    </div>`;
  }).join('')}</div>`;
}

function syncApiModuleCheckboxStates() {
  document.querySelectorAll('.api-module-tree input[data-module-path]').forEach(input => {
    input.indeterminate = input.dataset.indeterminate === 'true';
  });
}

function toggleApiModuleSelection(path, checked) {
  const source = selectedApiAssetSource() || {};
  const businessLine = currentApiBusinessLine();
  const rows = apiModuleRows(
    source,
    apiTestingEndpoints.filter(endpoint => !businessLine || apiBusinessLineForEndpoint(endpoint) === businessLine)
  );
  const state = apiModuleSelectionState();
  rows.filter(row => apiModulePathMatches(row.path, path)).forEach(row => {
    if (checked) state.selectedModules.add(row.path);
    else state.selectedModules.delete(row.path);
  });
  renderApiModuleWorkspace();
  updateApiSourceScopePreview();
}

function selectApiAssetModule(path) {
  const state = apiModuleSelectionState();
  state.activeModulePath = apiNormalizeModulePath(path);
  renderApiModuleWorkspace();
}

function apiActiveModuleEndpoints() {
  const state = apiModuleSelectionState();
  if (!state.activeModulePath) return [];
  const businessLine = currentApiBusinessLine();
  return apiTestingEndpoints.filter(endpoint => (
    (!businessLine || apiBusinessLineForEndpoint(endpoint) === businessLine)
    && apiModulePathMatches(apiEndpointModulePath(endpoint), state.activeModulePath)
  ));
}

function apiFilteredModuleEndpoints() {
  const state = apiModuleSelectionState();
  const query = state.search.trim().toLowerCase();
  return apiActiveModuleEndpoints().filter(endpoint => {
    if (state.method && endpoint.method !== state.method) return false;
    if (!query) return true;
    return [apiEndpointLabel(endpoint), endpoint.name, apiEndpointModulePath(endpoint)].join(' ').toLowerCase().includes(query);
  });
}

function setApiModuleSearch(value) {
  apiModuleSelectionState().search = String(value || '');
  renderApiModuleEndpointTable();
}

function setApiModuleMethodFilter(value) {
  apiModuleSelectionState().method = String(value || '');
  renderApiModuleEndpointTable();
}

function selectCurrentApiModule() {
  const state = apiModuleSelectionState();
  const endpoints = apiFilteredModuleEndpoints();
  const newIds = endpoints.filter(endpoint => !state.endpointIds.has(String(endpoint.endpoint_id || '')));
  if (state.endpointIds.size + newIds.length > API_PLAN_MAX_ENDPOINTS) {
    showToast(`当前范围有 ${endpoints.length} 个接口，单次最多生成 ${API_PLAN_MAX_ENDPOINTS} 个。请继续选择子模块或搜索接口。`, 'error');
    return;
  }
  endpoints.forEach(endpoint => state.endpointIds.add(String(endpoint.endpoint_id || '')));
  renderApiModuleEndpointTable();
  updateApiWorkflowStepper();
}

function renderApiModuleEndpointTable() {
  const container = document.getElementById('api-module-endpoint-table');
  if (!container) return;
  const state = apiModuleSelectionState();
  const endpoints = apiFilteredModuleEndpoints();
  container.innerHTML = state.activeModulePath
    ? renderApiAssetTable(endpoints, { emptyText: '当前模块没有符合筛选条件的接口。' })
    : apiTestingEmpty('请从左侧选择一个模块，再查看接口。');
  syncApiEndpointCheckboxStates();
  refreshApiAssetActionPanel();
}

function renderApiAssetActionPanelContent(source = selectedApiAssetSource() || {}) {
  const state = apiModuleSelectionState();
  const selectedCount = selectedApiPlanEndpointIds().length;
  const activeCount = apiActiveModuleEndpoints().length;
  const moduleName = state.activeModulePath ? state.activeModulePath.split('/').pop() : '未选择模块';
  const selectedModules = Array.from(state.selectedModules || []);
  return `
    <div class="api-asset-action-head">
      <span>下一步</span>
      <strong>资产 -> 测试设计 -> 执行 -> 报告</strong>
    </div>
    <div class="api-asset-action-scope">
      <span>当前范围</span>
      <strong>${escapeHtml(moduleName)}</strong>
      <small>${escapeHtml(state.activeModulePath || '请先从左侧选择一个模块')}</small>
    </div>
    <div class="api-asset-action-metrics">
      <span><strong>${escapeHtml(activeCount)}</strong><small>范围接口</small></span>
      <span><strong>${escapeHtml(selectedCount)}</strong><small>已选接口</small></span>
      <span><strong>${escapeHtml(API_PLAN_MAX_ENDPOINTS)}</strong><small>单次上限</small></span>
    </div>
    <div class="api-asset-action-buttons">
      <button class="btn-sm ai" onclick="launchApiPlanGenerationFromAssets()" ${selectedCount ? '' : 'disabled'}>进入测试设计</button>
      <button class="btn-sm" onclick="showApiExecutionHistoryPage()">执行记录</button>
      <button class="btn-sm" onclick="showApiReportsPage()">测试报告</button>
    </div>
    <div class="api-asset-generation-feedback">
      <strong>生成任务尚未开始</strong>
      <span>${selectedCount ? '已选接口会带入测试设计；点击“生成测试资产”后才会调用 AI。' : '先勾选接口，再进入测试设计发起 AI 生成。'}</span>
    </div>
    <div class="api-asset-action-note">
      <strong>${escapeHtml(apiSourceDisplayName(source) || '未选择 Apifox 项目')}</strong>
      <span>${selectedCount ? '下一页可以查看生成进度、AI 批次和测试资产草稿。' : '先在中间列表勾选接口，再进入测试设计。'}</span>
    </div>
    <details class="api-asset-action-detail">
      <summary>已选模块范围</summary>
      <div>${selectedModules.length ? selectedModules.map(item => `<code>${escapeHtml(item)}</code>`).join('') : '<span>尚未保存模块范围</span>'}</div>
    </details>
  `;
}

function renderApiAssetActionPanel(source = selectedApiAssetSource() || {}) {
  return `<aside id="api-asset-action-panel" class="api-asset-action-panel">${renderApiAssetActionPanelContent(source)}</aside>`;
}

function refreshApiAssetActionPanel() {
  const panel = document.getElementById('api-asset-action-panel');
  if (panel) panel.innerHTML = renderApiAssetActionPanelContent();
}

async function launchApiPlanGenerationFromAssets() {
  const endpointCount = selectedApiPlanEndpointIds().length;
  if (!endpointCount) {
    showToast('请先选择接口，再进入测试设计', 'error');
    return;
  }
  apiPlanLaunchNotice = { endpointCount, createdAt: new Date().toLocaleTimeString() };
  showToast('已带入当前接口范围，进入测试设计后可发起生成', 'success');
  await showApiPlanPage();
}

function renderApiModuleWorkspace() {
  const root = document.getElementById('api-module-workspace');
  if (!root) return;
  const source = selectedApiAssetSource() || {};
  const state = apiModuleSelectionState();
  const businessLines = apiBusinessLineOptions();
  const businessLine = currentApiBusinessLine();
  if (!state.businessLine && businessLine) state.businessLine = businessLine;
  const businessEndpoints = apiTestingEndpoints.filter(
    endpoint => !businessLine || apiBusinessLineForEndpoint(endpoint) === businessLine
  );
  const methods = Array.from(new Set(apiActiveModuleEndpoints().map(endpoint => endpoint.method).filter(Boolean))).sort();
  root.innerHTML = `
    <div class="api-business-line-switcher">
      <div><span>业务线</span><strong>先缩小业务范围，再选择模块和接口</strong></div>
      <select aria-label="选择接口业务线" onchange="selectApiBusinessLine(this.value)">
        ${businessLines.map(item => `<option value="${escapeHtml(item.name)}" ${item.name === businessLine ? 'selected' : ''}>${escapeHtml(item.name)} · ${escapeHtml(item.endpointCount)} 个接口</option>`).join('')}
      </select>
    </div>
    <div class="api-module-workspace api-asset-workbench-grid">
      <section class="api-module-pane">
        <div class="api-module-pane-head"><strong>${escapeHtml(businessLine || '业务')}模块</strong><span>${escapeHtml(apiModuleRows(source, businessEndpoints).length)} 个</span></div>
        <div class="api-module-tree-scroll">${renderApiModuleTree(source, businessEndpoints)}</div>
      </section>
      <section class="api-module-endpoints">
        <div class="api-module-pane-head"><div><strong>${escapeHtml(state.activeModulePath ? state.activeModulePath.split('/').pop() : '选择一个模块')}</strong><span>${state.activeModulePath ? `${apiActiveModuleEndpoints().length} 个接口 · 已选 ${selectedApiPlanEndpointIds().length}` : `单次最多 ${API_PLAN_MAX_ENDPOINTS} 个`}</span></div><button class="btn-sm api-module-select-current" type="button" ${state.activeModulePath ? '' : 'disabled'} onclick="selectCurrentApiModule()">选择当前结果</button></div>
        <div class="api-module-filters">
          <input id="api-module-search" type="search" value="${escapeHtml(state.search)}" placeholder="搜索当前模块接口" oninput="setApiModuleSearch(this.value)">
          <select id="api-module-method-filter" aria-label="接口方法筛选" onchange="setApiModuleMethodFilter(this.value)"><option value="">全部方法</option>${methods.map(method => `<option value="${escapeHtml(method)}" ${state.method === method ? 'selected' : ''}>${escapeHtml(method)}</option>`).join('')}</select>
        </div>
        <div id="api-module-endpoint-table" class="api-module-endpoint-scroll"></div>
      </section>
      ${renderApiAssetActionPanel(source)}
    </div>`;
  renderApiModuleEndpointTable();
  syncApiModuleCheckboxStates();
}

function renderApiAssetWorkspaceBody(data) {
  const asset = data.asset || {};
  const snapshot = data.snapshot || {};
  const revisions = data.revisions || [];
  const endpoints = data.endpoints || [];
  const selectedRevisionId = snapshot.revision_id || snapshot.snapshot_id || '';
  const revisionOptions = revisions.length ? revisions : (selectedRevisionId ? [{
    revision_id: selectedRevisionId,
    endpoint_count: endpoints.length,
    created_at: snapshot.created_at || ''
  }] : []);
  return `
    <section class="api-asset-revision-bar">
      <div><span>当前接口</span><strong>${selectedRevisionId ? '最新可用版本' : '尚未生成'}</strong></div>
      <div class="api-asset-revision-picker"><span>查看版本</span><select aria-label="查看接口版本" onchange="selectApiAssetRevision(this.value)">${revisionOptions.map(revision => {
        const revisionId = revision.revision_id || revision.snapshot_id || '';
        const active = revisionId && revisionId === asset.active_revision_id ? ' · 活动' : '';
        return `<option value="${escapeHtml(revisionId)}" ${revisionId === selectedRevisionId ? 'selected' : ''}>${escapeHtml(revision.created_at || revisionId || '接口版本')} · ${escapeHtml(revision.endpoint_count || 0)} 接口${active}</option>`;
      }).join('')}</select></div>
      <div><span>OpenAPI</span><strong>${escapeHtml(asset.schema_version || snapshot.openapi_version || '-')}</strong></div>
      <div><span>接口</span><strong>${escapeHtml(endpoints.length)}</strong></div>
      <div><span>历史版本</span><strong>${escapeHtml(revisions.length || (snapshot.snapshot_id ? 1 : 0))}</strong></div>
    </section>
    <div id="api-module-workspace"></div>
  `;
}

async function refreshApiAssetWorkspace(force = false, requestedRevisionId = null) {
  const body = document.getElementById('api-assets-body');
  if (!body) return;
  if (apiAssetRequestController) apiAssetRequestController.abort();
  const controller = new AbortController();
  apiAssetRequestController = controller;
  const requestId = ++apiAssetContextRequestId;
  captureApiAssetSyncViewState(document.getElementById('editor-area'));
  try {
    const sourceData = await apiRequest(`/api-testing/sources${force ? '?limit=20' : ''}`, { signal: controller.signal });
    if (requestId !== apiAssetContextRequestId || controller !== apiAssetRequestController || activeWorkflow !== 'api_assets') return;
    apiTestingSources = sourceData.sources || [];
    apiTestingSyncs = sourceData.syncs || [];
    if (!apiAssetSelectedSourceId || !apiTestingSources.some(item => item.source_id === apiAssetSelectedSourceId)) {
      apiAssetSelectedSourceId = (apiTestingSources[0] || {}).source_id || '';
    }
    const source = selectedApiAssetSource();
    const revisionId = requestedRevisionId === null
      ? (apiAssetRevisionPinned ? apiAssetSelectedRevisionId : '')
      : String(requestedRevisionId || '');
    const assetQuery = revisionId
      ? `?source_id=${encodeURIComponent(source?.source_id || '')}&snapshot_id=${encodeURIComponent(revisionId)}`
      : (source?.source_id ? `?source_id=${encodeURIComponent(source.source_id)}` : '');
    const assetData = await apiRequest(`/api-testing/assets${assetQuery}`, { signal: controller.signal });
    if (requestId !== apiAssetContextRequestId || controller !== apiAssetRequestController || activeWorkflow !== 'api_assets') return;
    if (!source && !apiAssetSettingsOpen) apiAssetSettingsOpen = true;
    apiTestingSnapshots = assetData.snapshots || [];
    apiTestingEndpoints = assetData.endpoints || [];
    apiAssetBusinessLines = assetData.business_lines || [];
    apiAssetSelectedRevisionId = (assetData.snapshot || {}).revision_id || (assetData.snapshot || {}).snapshot_id || '';
    apiTestingCurrentSnapshotId = apiAssetSelectedRevisionId || apiTestingCurrentSnapshotId || (apiTestingSnapshots[0] || {}).snapshot_id || '';
    apiTestingProjectScope = { sourceId: source?.source_id || assetData.source_id || '', revisionId: apiAssetSelectedRevisionId };
    const moduleState = apiModuleSelectionState();
    if (source?.sync_scope?.mode === 'selected' && !moduleState.selectedModules.size) {
      (source.sync_scope.module_paths || []).map(apiNormalizeModulePath).filter(Boolean).forEach(path => moduleState.selectedModules.add(path));
    }
    const latestSync = apiTestingSyncs.find(item => item.source_id === source?.source_id && ['queued', 'running'].includes(item.status))
      || apiTestingSyncs.find(item => item.source_id === source?.source_id)
      || null;
    if (latestSync?.sync_id) apiAssetActiveSyncId = latestSync.sync_id;
    const summary = document.getElementById('api-source-summary');
    const settings = document.getElementById('api-source-settings-panel');
    const syncRegion = document.getElementById('api-assets-sync');
    if (summary) summary.innerHTML = renderApiSourceSummary(source, latestSync, assetData.snapshot || {});
    if (settings) {
      settings.innerHTML = renderApiSourceSettings(source || {});
      settings.hidden = !apiAssetSettingsOpen;
    }
    if (syncRegion) syncRegion.innerHTML = renderApiAssetSync(latestSync);
    body.innerHTML = renderApiAssetWorkspaceBody(assetData);
    renderApiModuleWorkspace();
    updateApiWorkflowStepper({
      source,
      endpoints: apiTestingEndpoints,
      snapshot: assetData.snapshot || {},
      revisionTime: (assetData.snapshot || {}).created_at || '',
    });
    restoreApiAssetSyncViewState(document.getElementById('editor-area'));
    scheduleApiAssetSyncPoll(latestSync);
  } catch(e) {
    if (e?.name === 'AbortError') return;
    body.innerHTML = apiTestingEmpty(e.message || '接口资产读取失败');
  } finally {
    if (controller === apiAssetRequestController) apiAssetRequestController = null;
  }
}

async function refreshApiAssetsBody() {
  return refreshApiAssetWorkspace(true);
}

function toggleApiSourceSettings(open = null) {
  apiAssetSettingsOpen = open === null ? !apiAssetSettingsOpen : !!open;
  if (!apiAssetSettingsOpen) {
    apiSourceCredentialEditing = false;
    apiTestingSourceDraftMode = false;
    resetApiSourceDiscoveryState({});
  }
  const panel = document.getElementById('api-source-settings-panel');
  if (panel) {
    if (apiAssetSettingsOpen) panel.innerHTML = renderApiSourceSettings(selectedApiAssetSource() || {});
    panel.hidden = !apiAssetSettingsOpen;
  }
}

async function selectApiAssetSource(sourceId) {
  abortApiProjectScopeRequests();
  apiAssetSelectedSourceId = sourceId || '';
  apiSourceCredentialEditing = false;
  apiTestingSourceDraftMode = false;
  resetApiSourceDiscoveryState(selectedApiAssetSource() || {});
  apiAssetSelectedRevisionId = '';
  apiAssetRevisionPinned = false;
  apiAssetActiveSyncId = '';
  apiTestingProjectScope = { sourceId: apiAssetSelectedSourceId, revisionId: '' };
  apiTestingSelectionByScope.delete(apiProjectScopeKey());
  await refreshApiAssetWorkspace(true);
}

async function selectApiExecutionSource(sourceId) {
  abortApiProjectScopeRequests();
  apiAssetSelectedSourceId = sourceId || '';
  apiExecutionActiveId = '';
  apiTestingProjectScope = { sourceId: apiAssetSelectedSourceId, revisionId: '' };
  apiTestingSelectionByScope.delete(apiProjectScopeKey());
  await refreshApiExecutionContext(true);
}

async function selectApiAssetRevision(revisionId) {
  abortApiProjectScopeRequests();
  apiAssetSelectedRevisionId = revisionId || '';
  apiAssetRevisionPinned = !!apiAssetSelectedRevisionId;
  apiTestingProjectScope = { sourceId: apiAssetSelectedSourceId, revisionId: apiAssetSelectedRevisionId };
  apiTestingSelectionByScope.delete(apiProjectScopeKey());
  await refreshApiAssetWorkspace(true, apiAssetSelectedRevisionId);
}

function abortApiProjectScopeRequests() {
  [apiAssetRequestController, apiPlanRequestController, apiExecutionRequestController, apiPlanGenerationController, apiExecutionPollController].forEach(controller => controller?.abort());
  apiAssetRequestController = null;
  apiPlanRequestController = null;
  apiExecutionRequestController = null;
  apiPlanGenerationController = null;
  apiExecutionPollController = null;
  apiAssetContextRequestId += 1;
  apiPlanPageRequestId += 1;
  apiPlanGenerationRequestId += 1;
  apiExecutionContextRequestId += 1;
  apiExecutionActiveId = '';
  apiExecutionContext = null;
  abortApiExecutionBindingRequests();
  abortApiReportRequests();
  stopApiAssetSyncPolling();
  stopApiPlanGenerationPolling();
  stopApiExecutionPolling(true);
}

async function saveApiSourceConfig(clearCredentials = false) {
  const source = apiTestingSourceDraftMode ? {} : (selectedApiAssetSource() || {});
  const token = document.getElementById('api-source-token')?.value.trim() || '';
  const manualFallbackOpen = document.getElementById('api-source-manual-fallback')?.open === true;
  const discoveredProject = apiSourceDiscoveryState.project || null;
  const useDiscoveredSelection = !!(apiSourceDiscoveryState.fresh && discoveredProject);
  const manual = !useDiscoveredSelection && manualFallbackOpen;
  const scopeMode = document.querySelector('[data-sync-scope].active')?.dataset.syncScope || 'all';
  const selectedModules = apiSourceSelectedModulePaths(source);
  if (scopeMode === 'selected' && !selectedModules.length) {
    showToast('请选择至少一个同步模块', 'error');
    return;
  }
  if (!clearCredentials && !apiSourceHasReusableCredential(source) && !token) {
    showToast('请输入 Apifox 访问令牌', 'error');
    document.getElementById('api-source-token')?.focus();
    return;
  }
  if (!clearCredentials && token && !manual && !apiSourceDiscoveryState.fresh) {
    showToast('更换令牌后请先读取 Apifox 资产', 'error');
    return;
  }
  const branchSelect = document.getElementById('api-source-branch-select');
  const environmentSelect = document.getElementById('api-source-environment-select');
  const projectId = useDiscoveredSelection
    ? String(discoveredProject?.id || source.project_id || '')
    : (manual ? (document.getElementById('api-source-project-id')?.value.trim() || '') : String(source.project_id || ''));
  const branchId = useDiscoveredSelection
    ? String(branchSelect ? branchSelect.value : (source.branch_id || ''))
    : (manual ? (document.getElementById('api-source-branch-id')?.value.trim() || '') : String(source.branch_id || ''));
  const environmentId = useDiscoveredSelection
    ? String(environmentSelect ? environmentSelect.value : (source.environment_id || ''))
    : (manual ? (document.getElementById('api-source-environment-id')?.value.trim() || '') : String(source.environment_id || ''));
  if (!projectId) {
    showToast(manual ? '请填写 Apifox 项目 ID' : '请先选择 Apifox 项目', 'error');
    return;
  }
  const sourceName = manual
    ? (document.getElementById('api-source-name')?.value.trim() || 'Apifox 接口')
    : (discoveredProject?.name || apiSourceDisplayName(source));
  const payload = {
    source_id: source.source_id || undefined,
    source_type: 'apifox',
    name: sourceName,
    project_id: projectId,
    branch_id: branchId,
    environment_id: environmentId,
    sync_interval_minutes: Number(document.getElementById('api-source-interval')?.value || 60),
    sync_enabled: !!document.getElementById('api-source-sync-enabled')?.checked,
    sync_scope: { mode: scopeMode, module_paths: scopeMode === 'selected' ? selectedModules : [] },
    selected_modules: scopeMode === 'selected' ? selectedModules : [],
    clear_credentials: !!clearCredentials
  };
  if (token) payload.access_token = token;
  if (useDiscoveredSelection) {
    const selectedBranch = (apiSourceDiscoveryState.branches || []).find(
      item => String(item.id || '') === branchId
    );
    const selectedEnvironment = (apiSourceDiscoveryState.environments || []).find(
      item => String(item.id || '') === environmentId
    );
    payload.provider_metadata = {
      project_name: discoveredProject.name || '',
      project_description: discoveredProject.description || '',
      team_id: discoveredProject.team?.id || '',
      team_name: discoveredProject.team?.name || '',
      branch_name: selectedBranch?.name || '主分支（默认）',
      environment_name: selectedEnvironment?.name || '不绑定环境',
      discovered_at: new Date().toISOString(),
      discovery_source: 'apifox_cli'
    };
    payload.environment_snapshot = selectedEnvironment?.environment_snapshot || {};
  } else {
    payload.environment_snapshot = source.environment_snapshot || {};
  }
  try {
    const data = await apiRequest('/api-testing/sources', { method: 'POST', body: payload });
    apiAssetSelectedSourceId = data.source?.source_id || apiAssetSelectedSourceId;
    apiTestingSourceDraftMode = false;
    apiSourceCredentialEditing = false;
    resetApiSourceDiscoveryState(data.source || {});
    if (!source.source_id) {
      apiAssetSelectedRevisionId = '';
      apiAssetRevisionPinned = false;
    }
    const tokenInput = document.getElementById('api-source-token');
    if (tokenInput) tokenInput.value = '';
    if (data.sync?.sync_id) {
      apiAssetActiveSyncId = data.sync.sync_id;
      showToast('✓ 设置已保存，接口正在自动同步', 'success');
    } else if (data.sync_error) {
      showToast(data.sync_error, 'error');
    } else {
      showToast('✓ Apifox 来源设置已保存', 'success');
    }
    await refreshApiAssetWorkspace(true);
  } catch (e) {
    showToast(e.message || 'Apifox 来源设置保存失败', 'error');
  }
}

async function clearApiSourceCredential() {
  if (!confirm('确认清除服务端保存的 Apifox 令牌？清除后同步会停止。')) return;
  await saveApiSourceConfig(true);
}

async function useApiSourceEnvironmentSnapshot() {
  const source = selectedApiAssetSource() || {};
  const sourceId = source.source_id || '';
  if (!sourceId) {
    showToast('请先保存 Apifox 来源', 'error');
    return;
  }
  try {
    const data = await apiRequest(`/api-testing/sources/${encodeURIComponent(sourceId)}/environment-sync`, {
      method: 'POST'
    });
    const sync = data.sync || {};
    showToast(sync.message || '✓ API 执行将直接使用当前 Apifox 环境快照', 'success');
    if (activeWorkflow === 'api_environment') await showApiEnvironmentPage();
    else await refreshApiAssetWorkspace(true);
  } catch (error) {
    showToast(error.message || 'Apifox 环境读取失败', 'error');
  }
}

async function startApiAssetSync() {
  const source = selectedApiAssetSource();
  if (!source?.source_id) {
    toggleApiSourceSettings(true);
    showToast('请先保存 Apifox 来源设置', 'error');
    return;
  }
  stopApiAssetSyncPolling();
  try {
    const data = await apiRequest(`/api-testing/sources/${encodeURIComponent(source.source_id)}/sync`, { method: 'POST', body: {} });
    const sync = data.sync || {};
    apiAssetActiveSyncId = sync.sync_id || '';
    const region = document.getElementById('api-assets-sync');
    if (region) region.innerHTML = renderApiAssetSync(sync);
    restoreApiAssetSyncViewState(region);
    showToast(sync.created === false ? '同步已在进行中' : 'Apifox 同步已启动', 'success');
    scheduleApiAssetSyncPoll(sync);
  } catch (e) {
    showToast(e.message || 'Apifox 同步启动失败', 'error');
  }
}

async function pollApiAssetSync(syncId) {
  if (!syncId || !apiAssetSyncWorkflowActive()) return;
  try {
    const data = await apiRequest(`/api-testing/syncs/${encodeURIComponent(syncId)}`);
    if (!apiAssetSyncWorkflowActive() || syncId !== apiAssetActiveSyncId) return;
    const sync = data.sync || {};
    captureApiAssetSyncViewState(document.getElementById('api-assets-sync'));
    const region = document.getElementById('api-assets-sync');
    if (region) region.innerHTML = renderApiAssetSync(sync);
    restoreApiAssetSyncViewState(region);
    if (apiAssetSyncTerminal(sync)) {
      stopApiAssetSyncPolling();
      await refreshCurrentApiNativePage();
    } else {
      scheduleApiAssetSyncPoll(sync);
    }
  } catch (e) {
    const region = document.getElementById('api-assets-sync');
    if (region) {
      let error = region.querySelector('.api-sync-poll-error');
      if (!error) {
        error = document.createElement('div');
        error.className = 'api-inline-error api-sync-poll-error';
        region.appendChild(error);
      }
      error.textContent = `${e.message || '同步状态读取失败'}，3 秒后重试`;
    }
    stopApiAssetSyncPolling();
    if (apiAssetSyncWorkflowActive() && syncId === apiAssetActiveSyncId) {
      apiAssetSyncPollTimer = setTimeout(() => pollApiAssetSync(syncId), 3000);
    }
  }
}

async function handleApiOpenApiFile(input) {
  const status = document.getElementById('api-assets-status');
  const file = (input.files || [])[0];
  if (!file) return;
  try {
    const text = await file.text();
    const documentJson = JSON.parse(text);
    if (status) {
      status.className = 'generate-status show busy';
      status.textContent = '正在导入 OpenAPI...';
    }
    const name = document.getElementById('api-openapi-name')?.value.trim() || file.name.replace(/\.json$/i, '');
    const data = await apiRequest('/api-testing/openapi/import', { method: 'POST', body: { name, filename: file.name, document: documentJson } });
    apiTestingCurrentSnapshotId = (data.snapshot || {}).snapshot_id || '';
    apiAssetSelectedRevisionId = apiTestingCurrentSnapshotId;
    apiAssetRevisionPinned = !!apiAssetSelectedRevisionId;
    if (status) {
      status.className = 'generate-status show success';
      status.textContent = `已导入 ${(data.endpoints || []).length} 个接口`;
    }
    showToast('✓ OpenAPI 已导入', 'success');
    await refreshApiAssetWorkspace(true, apiAssetSelectedRevisionId);
  } catch(e) {
    if (status) {
      status.className = 'generate-status show error';
      status.textContent = e.message || 'OpenAPI 导入失败';
    }
    showToast(e.message || 'OpenAPI 导入失败', 'error');
  } finally {
    input.value = '';
  }
}

function apiPlanGenerationScopeKey() {
  return apiProjectScopeKey();
}

function apiPlanGenerationTerminal(generation) {
  return ['succeeded', 'partial', 'failed', 'cancelled'].includes(String(generation?.status || '').toLowerCase());
}

function stopApiPlanGenerationPolling(abortRequest = false) {
  if (apiPlanGenerationPollTimer) clearTimeout(apiPlanGenerationPollTimer);
  apiPlanGenerationPollTimer = null;
  if (abortRequest && apiPlanGenerationController) {
    apiPlanGenerationController.abort();
    apiPlanGenerationController = null;
    apiPlanGenerationRequestId += 1;
  }
}

function apiPlanGenerationStatusText(status) {
  return ({
    queued: '排队中',
    running: '生成中',
    succeeded: '生成完成',
    partial: '部分完成',
    failed: '生成失败',
    cancelled: '已取消'
  })[status] || status || '等待生成';
}

function apiPlanGenerationStatusClass(status) {
  if (status === 'succeeded') return 'success';
  if (['partial', 'failed', 'cancelled'].includes(status)) return status === 'partial' ? 'warn' : 'danger';
  return 'warn';
}

function apiPlanBatchStatusText(status) {
  return ({
    queued: '等待',
    running: 'AI 生成中',
    succeeded: '已完成',
    failed: '失败',
    cancelled: '已取消'
  })[status] || status || '等待';
}

function selectedApiPlanEndpointIds() {
  const available = new Set(apiTestingEndpoints.map(endpoint => String(endpoint.endpoint_id || '')).filter(Boolean));
  return Array.from(apiModuleSelectionState().endpointIds).filter(endpointId => available.has(endpointId));
}

function selectedApiPlanModulePaths(endpointIds = selectedApiPlanEndpointIds()) {
  const endpointIdSet = new Set(endpointIds.map(String));
  const endpointPaths = Array.from(new Set(apiTestingEndpoints
    .filter(endpoint => endpointIdSet.has(String(endpoint.endpoint_id || '')))
    .map(apiEndpointModulePath)
    .filter(Boolean)));
  const selectedPaths = Array.from(apiModuleSelectionState().selectedModules)
    .map(apiNormalizeModulePath)
    .filter(path => endpointPaths.some(endpointPath => apiModulePathMatches(endpointPath, path)));
  const uncoveredPaths = endpointPaths.filter(endpointPath => !selectedPaths.some(path => apiModulePathMatches(endpointPath, path)));
  return [...selectedPaths, ...uncoveredPaths]
    .sort((left, right) => left.split('/').length - right.split('/').length || left.localeCompare(right))
    .filter((path, index, rows) => !rows.slice(0, index).some(parent => apiModulePathMatches(path, parent)));
}

function apiPlanGenerationLogKey(generationId) {
  return `${apiPlanGenerationScopeKey()}::generation::${generationId || 'none'}`;
}

function toggleApiPlanGenerationLog(generationId, open) {
  const key = apiPlanGenerationLogKey(generationId);
  if (open) apiPlanGenerationExpandedKeys.add(key);
  else apiPlanGenerationExpandedKeys.delete(key);
  localStorage.setItem('api_plan_generation_expanded_keys', JSON.stringify(Array.from(apiPlanGenerationExpandedKeys)));
}

function rememberApiPlanGenerationLogScroll(key, scrollTop) {
  apiPlanGenerationScrollPositions.set(String(key || ''), Number(scrollTop || 0));
}

function captureApiPlanGenerationLogViewState(root = document) {
  if (!root?.querySelectorAll) return;
  root.querySelectorAll('[data-api-generation-log-key]').forEach(detail => {
    const key = detail.dataset.apiGenerationLogKey || '';
    if (detail.open) apiPlanGenerationExpandedKeys.add(key);
    else apiPlanGenerationExpandedKeys.delete(key);
    const content = detail.querySelector('.api-generation-log-content');
    if (content) apiPlanGenerationScrollPositions.set(key, content.scrollTop);
  });
}

function restoreApiPlanGenerationLogViewState(root = document) {
  if (!root?.querySelectorAll) return;
  root.querySelectorAll('[data-api-generation-log-key]').forEach(detail => {
    const key = detail.dataset.apiGenerationLogKey || '';
    detail.open = apiPlanGenerationExpandedKeys.has(key);
    const content = detail.querySelector('.api-generation-log-content');
    if (content) content.scrollTop = apiPlanGenerationScrollPositions.get(key) || 0;
  });
}

function renderApiPlanGeneration(generation) {
  if (!generation?.generation_id) return apiTestingEmpty('选择接口后，点击“生成测试资产”开始。');
  const batches = generation.batches || [];
  const events = generation.events || [];
  const generatedBatches = batches.filter(batch => String(batch.plan_id || '').trim());
  const failedCount = Number(generation.failed_batches || batches.filter(batch => batch.status === 'failed').length);
  const retryable = ['partial', 'failed'].includes(generation.status) && failedCount > 0;
  const logKey = apiPlanGenerationLogKey(generation.generation_id);
  const logOpen = apiPlanGenerationExpandedKeys.has(logKey);
  const completed = Number(generation.completed_batches || 0);
  const stageIndex = generation.status === 'queued'
    ? 0
    : generation.status === 'running'
      ? 1
      : ['succeeded', 'partial'].includes(generation.status)
        ? 3
        : 2;
  const stages = ['校验接口范围', 'AI 分批设计', '平台可执行性校验', '草稿已生成'];
  return `
    <article class="api-plan-generation" data-status="${escapeHtml(generation.status || '')}">
      <header class="api-plan-generation-head">
        <div>
          <span>本次生成</span>
          <h3>${escapeHtml(apiPlanGenerationStatusText(generation.status))}</h3>
          <small>${escapeHtml((generation.selected_endpoint_keys || []).length)} 个接口 · ${escapeHtml(generation.batch_count || batches.length)} 个 AI 批次</small>
        </div>
        <div class="api-plan-generation-progress">
          ${apiStatusPill(apiPlanGenerationStatusText(generation.status), apiPlanGenerationStatusClass(generation.status))}
          <strong>${escapeHtml(completed)} / ${escapeHtml(generation.batch_count || batches.length)} 批</strong>
        </div>
      </header>
      <ol class="api-generation-stages">${stages.map((stage, index) => `
        <li class="${index < stageIndex ? 'done' : index === stageIndex ? 'active' : ''}">
          <span>${index < stageIndex ? '✓' : index + 1}</span><strong>${escapeHtml(stage)}</strong>
        </li>
      `).join('')}</ol>
      ${generatedBatches.length ? `
        <div class="api-plan-generated-summary">
          <div><strong>AI 生成结果</strong><span>${escapeHtml(generatedBatches.length)} 个草稿计划已生成，先审阅用例明细，再保存为测试资产。</span></div>
          <div>${generatedBatches.map((batch, index) => `<button class="btn-sm ai" onclick="openGeneratedApiPlan(${jsArg(batch.plan_id)})">查看生成用例 ${escapeHtml(batch.batch_index || index + 1)}</button>`).join('')}</div>
        </div>
      ` : ''}
      ${generation.error ? `<div class="api-inline-error">${escapeHtml(generation.error)}</div>` : ''}
      ${retryable ? `<div class="generation-record-actions"><button class="btn-sm ai" onclick="retryApiPlanGeneration(${jsArg(generation.generation_id)})">重试失败批次</button><span>已成功内容保持不变。</span></div>` : ''}
      <details class="api-plan-tech-detail api-generation-log-detail" data-api-generation-log-key="${escapeHtml(logKey)}" ${logOpen ? 'open' : ''} ontoggle="toggleApiPlanGenerationLog(${jsArg(generation.generation_id)}, this.open)">
        <summary><strong>技术详情</strong><span>${batches.length} 个批次 · ${events.length} 条事件</span></summary>
        <div class="api-plan-generation-scope">
          <span>Generation <code>${escapeHtml(generation.generation_id)}</code></span>
          <span>Source <code>${escapeHtml(generation.source_id || '-')}</code></span>
          <span>Revision <code>${escapeHtml(generation.asset_revision_id || '-')}</code></span>
          <span>模块 ${escapeHtml((generation.module_paths || []).join('、') || '-')}</span>
        </div>
        <div class="api-plan-batch-list">${batches.map((batch, index) => {
          const batchNumber = Number(batch.batch_index || index + 1);
          return `
            <div class="api-plan-batch-row status-${escapeHtml(batch.status || 'queued')}">
              <span class="api-plan-batch-index">${String(batchNumber).padStart(2, '0')}</span>
              <div><strong>批次 ${escapeHtml(batchNumber)}</strong><small>第 ${escapeHtml(batch.attempts || 0)} 次尝试</small></div>
              <strong class="api-plan-batch-count">${escapeHtml(batch.endpoint_count || 0)}</strong>
              ${apiStatusPill(apiPlanBatchStatusText(batch.status), apiPlanGenerationStatusClass(batch.status))}
              <div class="api-plan-batch-result">${batch.plan_id
                ? `<code>${escapeHtml(batch.plan_id)}</code><button class="btn-sm ghost" onclick="openGeneratedApiPlan(${jsArg(batch.plan_id)})">查看生成用例</button>`
                : `<span>${escapeHtml(batch.error || (batch.status === 'running' ? '等待 AI 返回' : '尚未生成计划'))}</span>`}</div>
            </div>
          `;
        }).join('')}</div>
        <div class="api-generation-log-content" onscroll="rememberApiPlanGenerationLogScroll(${jsArg(logKey)}, this.scrollTop)">${events.length ? events.map(event => {
          const detail = event.detail == null ? '' : (typeof event.detail === 'string' ? event.detail : JSON.stringify(event.detail, null, 2));
          return `<div><time>${escapeHtml(event.at || event.timestamp || '-')}</time><strong>${escapeHtml(event.message || event.summary || event.status || '生成事件')}</strong><small>${escapeHtml(event.status || event.phase || '')}</small>${detail ? `<pre>${escapeHtml(detail)}</pre>` : ''}</div>`;
        }).join('') : apiTestingEmpty('暂无生成日志')}</div>
      </details>
    </article>
  `;
}

function openGeneratedApiPlan(planId) {
  return openApiTestPlan(planId);
}

function updateApiPlanGeneration(generation) {
  const target = document.getElementById('api-plan-generation-region');
  captureApiPlanGenerationLogViewState(target);
  apiPlanGenerationCurrent = generation || null;
  if (target) target.innerHTML = renderApiPlanGeneration(apiPlanGenerationCurrent);
  restoreApiPlanGenerationLogViewState(target);
}

function apiPlanResponseIsCurrent(controller, requestId, capturedScopeKey) {
  return controller === apiPlanGenerationController
    && requestId === apiPlanGenerationRequestId
    && capturedScopeKey === apiPlanGenerationScopeKey()
    && ['api_plan', 'api_dashboard'].includes(activeWorkflow);
}

function scheduleApiPlanGenerationPoll(generation, requestId = apiPlanGenerationRequestId, capturedScopeKey = apiPlanGenerationScopeKey()) {
  stopApiPlanGenerationPolling();
  if (!generation?.generation_id || apiPlanGenerationTerminal(generation) || !['api_plan', 'api_dashboard'].includes(activeWorkflow)) return;
  const delay = Math.max(50, Number(generation.poll_after_ms || 1000));
  apiPlanGenerationPollTimer = setTimeout(
    () => pollApiPlanGeneration(generation.generation_id, requestId, capturedScopeKey),
    delay
  );
}

async function pollApiPlanGeneration(generationId, requestId = apiPlanGenerationRequestId, capturedScopeKey = apiPlanGenerationScopeKey()) {
  if (!['api_plan', 'api_dashboard'].includes(activeWorkflow) || requestId !== apiPlanGenerationRequestId || capturedScopeKey !== apiPlanGenerationScopeKey()) return;
  if (apiPlanGenerationController) apiPlanGenerationController.abort();
  const controller = new AbortController();
  apiPlanGenerationController = controller;
  const sourceId = apiTestingProjectScope.sourceId;
  try {
    const query = sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : '';
    const data = await apiRequest(`/api-testing/plan-generations/${encodeURIComponent(generationId)}${query}`, { signal: controller.signal });
    if (!apiPlanResponseIsCurrent(controller, requestId, capturedScopeKey)) return;
    const generation = data.generation || {};
    updateApiPlanGeneration(generation);
    if (apiPlanGenerationTerminal(generation)) {
      if (['succeeded', 'partial'].includes(generation.status)) {
        await refreshApiPlanCards(capturedScopeKey);
        const generatedPlanIds = (generation.batches || [])
          .map(batch => String(batch.plan_id || ''))
          .filter(Boolean);
        const latestGeneratedPlanId = generatedPlanIds[generatedPlanIds.length - 1] || '';
        const latestGeneratedPlan = apiTestingPlans.find(
          plan => String(plan.plan_id || '') === latestGeneratedPlanId
        );
        if (latestGeneratedPlan) await openApiTestPlan(latestGeneratedPlan.plan_id);
      }
    } else {
      scheduleApiPlanGenerationPoll(generation, requestId, capturedScopeKey);
    }
  } catch (error) {
    if (!apiPlanResponseIsCurrent(controller, requestId, capturedScopeKey)) return;
    const target = document.getElementById('api-plan-generation-region');
    if (target) target.insertAdjacentHTML('beforeend', `<div class="api-inline-error">${escapeHtml(error.message || '计划生成状态读取失败')}</div>`);
  } finally {
    if (controller === apiPlanGenerationController) apiPlanGenerationController = null;
  }
}

async function startApiPlanGeneration() {
  const sourceId = apiTestingProjectScope.sourceId || apiAssetSelectedSourceId;
  const revisionId = apiTestingProjectScope.revisionId || apiTestingCurrentSnapshotId;
  const endpointIds = selectedApiPlanEndpointIds();
  const modulePaths = selectedApiPlanModulePaths(endpointIds);
  if (!sourceId || !revisionId) {
    showToast('请先选择 API 项目和活动版本', 'error');
    return;
  }
  if (!endpointIds.length || endpointIds.length > 60) {
    showToast('请选择 1-60 个接口生成计划', 'error');
    return;
  }
  apiPlanLaunchNotice = null;
  const launchNotice = document.querySelector('.api-plan-launch-notice');
  if (launchNotice) launchNotice.remove();
  stopApiPlanGenerationPolling(true);
  const requestId = ++apiPlanGenerationRequestId;
  const capturedScopeKey = apiPlanGenerationScopeKey();
  const controller = new AbortController();
  apiPlanGenerationController = controller;
  const target = document.getElementById('api-plan-generation-region');
  if (target) target.innerHTML = apiTestingEmpty('正在创建服务端 AI 生成任务...');
  try {
    const data = await apiRequest('/api-testing/plan-generations', {
      method: 'POST',
      signal: controller.signal,
      body: {
        source_id: sourceId,
        revision_id: revisionId,
        endpoint_ids: endpointIds,
        module_paths: modulePaths
      }
    });
    if (!apiPlanResponseIsCurrent(controller, requestId, capturedScopeKey)) return;
    const generation = data.generation || {};
    updateApiPlanGeneration(generation);
    showToast('✓ AI 计划生成已排队', 'success');
    scheduleApiPlanGenerationPoll(generation, requestId, capturedScopeKey);
  } catch (error) {
    if (!apiPlanResponseIsCurrent(controller, requestId, capturedScopeKey)) return;
    if (target) target.innerHTML = `<div class="api-inline-error">${escapeHtml(error.message || '计划生成启动失败')}</div>`;
    showToast(error.message || '计划生成启动失败', 'error');
  } finally {
    if (controller === apiPlanGenerationController) apiPlanGenerationController = null;
  }
}

async function retryApiPlanGeneration(generationId) {
  stopApiPlanGenerationPolling(true);
  const requestId = ++apiPlanGenerationRequestId;
  const capturedScopeKey = apiPlanGenerationScopeKey();
  const controller = new AbortController();
  apiPlanGenerationController = controller;
  try {
    const data = await apiRequest(`/api-testing/plan-generations/${encodeURIComponent(generationId)}/retry`, {
      method: 'POST',
      signal: controller.signal,
      body: {}
    });
    if (!apiPlanResponseIsCurrent(controller, requestId, capturedScopeKey)) return;
    const generation = data.generation || {};
    updateApiPlanGeneration(generation);
    showToast('✓ 失败批次已重新排队', 'success');
    scheduleApiPlanGenerationPoll(generation, requestId, capturedScopeKey);
  } catch (error) {
    if (!apiPlanResponseIsCurrent(controller, requestId, capturedScopeKey)) return;
    showToast(error.message || '失败批次重试失败', 'error');
  } finally {
    if (controller === apiPlanGenerationController) apiPlanGenerationController = null;
  }
}

async function loadApiPlanDetails(planSummaries, sourceId, controller) {
  return Promise.all((planSummaries || []).map(async summary => {
    try {
      const query = sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : '';
      const data = await apiRequest(`/api-testing/plans/${encodeURIComponent(summary.plan_id)}${query}`, { signal: controller.signal });
      return data.plan || summary;
    } catch (error) {
      if (controller.signal.aborted) throw error;
      return summary;
    }
  }));
}

function renderApiPlanScopeSummary() {
  const endpointIds = new Set(selectedApiPlanEndpointIds().map(String));
  const selected = apiTestingEndpoints.filter(endpoint => endpointIds.has(String(endpoint.endpoint_id || '')));
  const modules = Array.from(new Set(selected.map(apiEndpointModulePath).filter(Boolean)));
  const methods = Array.from(new Set(selected.map(endpoint => endpoint.method).filter(Boolean))).sort();
  const businessLine = currentApiBusinessLine();
  return `
    <div class="api-plan-scope-summary">
      <div class="api-plan-scope-primary">
        <span>本次范围</span>
        <strong>${escapeHtml(businessLine || '尚未选择业务线')}</strong>
        <small>${selected.length ? `${selected.length} 个接口，预计分 ${Math.ceil(selected.length / 12)} 个 AI 批次` : '请返回接口资产选择模块和接口'}</small>
      </div>
      <div class="api-plan-scope-metrics">
        <div><strong>${escapeHtml(selected.length)}</strong><span>已选接口</span></div>
        <div><strong>${escapeHtml(modules.length)}</strong><span>涉及模块</span></div>
        <div><strong>${escapeHtml(methods.length)}</strong><span>请求方法</span></div>
      </div>
      <div class="api-plan-scope-modules">
        <span>模块</span>
        <div>${modules.length
          ? modules.slice(0, 8).map(module => `<strong>${escapeHtml(module)}</strong>`).join('')
          : '<small>尚未选择</small>'}</div>
        ${modules.length > 8 ? `<small>另有 ${escapeHtml(modules.length - 8)} 个模块</small>` : ''}
      </div>
      <details class="api-plan-tech-detail">
        <summary>查看技术范围</summary>
        <div>Source ${escapeHtml(apiTestingProjectScope.sourceId || '-')} · Revision ${escapeHtml(apiTestingCurrentSnapshotId || '-')} · Methods ${escapeHtml(methods.join(', ') || '-')}</div>
      </details>
    </div>
  `;
}

function apiCandidatePlans(plans = apiTestingPlans) {
  return (plans || []).filter(plan => plan.status !== 'confirmed');
}

async function showApiPlanPage() {
  const area = setApiTestingPage('api_plan', '测试设计', '从已选接口生成 AI 草稿，通过平台校验后保存为测试资产。');
  if (!area) return;
  if (apiPlanRequestController) apiPlanRequestController.abort();
  const controller = new AbortController();
  const requestId = ++apiPlanPageRequestId;
  apiPlanRequestController = controller;
  const sourceId = apiTestingProjectScope.sourceId || apiAssetSelectedSourceId;
  const revisionId = apiTestingProjectScope.revisionId || apiTestingCurrentSnapshotId;
  const capturedScopeKey = apiProjectScopeKey(sourceId, revisionId);
  area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty('正在读取当前范围的接口资产、计划与执行绑定...')}</div>`;
  try {
    const assetQuery = new URLSearchParams();
    if (sourceId) assetQuery.set('source_id', sourceId);
    if (revisionId) assetQuery.set('snapshot_id', revisionId);
    const [assets, planResponse, bindingResponse] = await Promise.all([
      apiRequest(`/api-testing/assets${assetQuery.toString() ? `?${assetQuery}` : ''}`, { signal: controller.signal }),
      apiRequest(`/api-testing/plans${sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : ''}`, { signal: controller.signal }),
      sourceId
        ? apiRequest(`/api-testing/sources/${encodeURIComponent(sourceId)}/execution-binding`, { signal: controller.signal })
        : Promise.resolve({binding: {}, context: {}})
    ]);
    if (controller !== apiPlanRequestController || requestId !== apiPlanPageRequestId || activeWorkflow !== 'api_plan' || capturedScopeKey !== apiProjectScopeKey(sourceId, revisionId)) return;
    apiTestingEndpoints = assets.endpoints || [];
    apiAssetBusinessLines = assets.business_lines || [];
    apiTestingCurrentSnapshotId = (assets.snapshot || {}).revision_id || (assets.snapshot || {}).snapshot_id || apiTestingCurrentSnapshotId || ((assets.snapshots || [])[0] || {}).snapshot_id || '';
    apiTestingProjectScope = {sourceId, revisionId: apiTestingCurrentSnapshotId};
    const resolvedScopeKey = apiProjectScopeKey(sourceId, apiTestingCurrentSnapshotId);
    apiPlanBindingContext = bindingResponse.context || {binding: bindingResponse.binding || {}};
    if (!apiPlanBindingContext.binding) apiPlanBindingContext.binding = bindingResponse.binding || {};
    apiTestingPlans = await loadApiPlanDetails(planResponse.plans || [], sourceId, controller);
    if (controller !== apiPlanRequestController || requestId !== apiPlanPageRequestId || activeWorkflow !== 'api_plan' || resolvedScopeKey !== apiPlanGenerationScopeKey()) return;
    if (apiPlanGenerationCurrent && (
      apiPlanGenerationCurrent.source_id !== sourceId
      || apiPlanGenerationCurrent.asset_revision_id !== apiTestingCurrentSnapshotId
    )) apiPlanGenerationCurrent = null;
    const source = selectedApiAssetSource() || {};
    const candidatePlans = apiCandidatePlans();
    area.innerHTML = `
      <div class="api-testing-page api-plan-workspace">
        <div id="api-workflow-stepper">${renderApiWorkflowStepper({workflow: 'api_plan', source, snapshot: assets.snapshot})}</div>
        <div class="generation-record-head">
          <div class="workflow-kicker">AI TEST DESIGN · API CASES</div>
          <h2>AI测试设计</h2>
          <p>AI业务理解、风险点和测试建议会沉淀成草稿；平台校验接口合同、环境数据和可执行性，确认后保存为测试资产。</p>
          <div class="generation-record-actions">
            <button class="btn-sm ai api-plan-generate-action" onclick="generateApiTestPlan()" ${selectedApiPlanEndpointIds().length ? '' : 'disabled'}>生成测试资产</button>
            <button class="btn-sm" onclick="showApiAssetsPage()">调整接口范围</button>
          </div>
        </div>
        <div class="api-plan-layout">
          <section class="api-panel api-plan-selection-panel">
            <div class="api-section-heading"><div><span>生成范围</span><h3>这次要测什么</h3></div><strong>${selectedApiPlanEndpointIds().length} / ${API_PLAN_MAX_ENDPOINTS}</strong></div>
            ${renderApiPlanScopeSummary()}
          </section>
          <div class="api-plan-review-column">
            ${renderApiPlanLaunchNotice()}
            <section class="api-panel" id="api-plan-generation-region">${renderApiPlanGeneration(apiPlanGenerationCurrent)}</section>
            <section class="api-panel" id="api-plan-list-region">
              <div class="api-section-heading"><div><span>待处理</span><h3>AI 草稿</h3></div><small>${candidatePlans.length} 个计划</small></div>
              ${renderApiPlanList(candidatePlans)}
            </section>
            <section class="api-panel" id="api-plan-result">${apiTestingEmpty('选择一个草稿，按接口审阅用例。')}</section>
          </div>
        </div>
      </div>
    `;
    restoreApiPlanGenerationLogViewState(document.getElementById('api-plan-generation-region'));
  } catch(error) {
    if (controller !== apiPlanRequestController || requestId !== apiPlanPageRequestId || activeWorkflow !== 'api_plan') return;
    area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty(error.message || 'API 用例计划读取失败')}</div>`;
  } finally {
    if (controller === apiPlanRequestController) apiPlanRequestController = null;
  }
}

function apiPlanAiTrace(plan) {
  const ai = plan.ai || {};
  const trace = plan.ai_trace || ai.trace || {};
  return {
    label: trace.model || ai.model || (plan.source === 'ai' ? 'AI 已使用' : plan.source || '未记录'),
    detail: trace.provider || trace.trace_id || ai.fallback_reason || plan.generation_id || '-'
  };
}

function apiPlanBindingFact(plan) {
  const explicit = plan.execution_binding || {};
  const current = apiPlanBindingContext?.binding || {};
  const matchesCurrent = plan.execution_binding_id && String(plan.execution_binding_id) === String(current.binding_id || '');
  const binding = Object.keys(explicit).length ? explicit : (matchesCurrent ? current : {});
  return {
    label: binding.project_name || binding.project_id || plan.execution_binding_id || '未绑定',
    detail: binding.environment_name || binding.environment_id || plan.binding_fingerprint || '-'
  };
}

function apiPlanAuthFact(plan) {
  const auth = plan.auth_binding || {};
  return {
    label: auth.configured ? (auth.auth_type === 'api_key' ? 'API Key' : 'Bearer') : '未配置',
    detail: auth.variable_name || auth.auth_ref || '-'
  };
}

function apiPlanCurrentBindingFact() {
  const context = apiPlanBindingContext || {};
  const binding = context.binding || {};
  const auth = context.auth_binding || binding.auth_binding || {};
  return {binding, auth};
}

function renderApiPlanBindingDriftPanel(plan = {}) {
  const drift = plan.binding_drift || [];
  if (!drift.includes('workspace_binding_drift') && !drift.includes('auth_binding_drift')) return '';
  const plannedBinding = apiPlanBindingFact(plan);
  const {binding: currentBinding, auth: currentAuth} = apiPlanCurrentBindingFact();
  const currentProject = currentBinding.project_name || currentBinding.project_id || '未绑定';
  const currentEnvironment = currentBinding.environment_name || currentBinding.environment_id || '-';
  const authConfigured = currentAuth.configured === true;
  const authLabel = authConfigured ? (currentAuth.variable_name || currentAuth.auth_ref || '已配置') : '当前环境未配置业务 token';
  const authDetail = authConfigured
    ? `${currentAuth.header_name || 'Authorization'} · ${currentAuth.auth_ref || '服务端引用'}`
    : '请先在环境配置里保存业务用户登录 token';
  return `
    <section class="api-plan-binding-drift-panel">
      <div>
        <span>计划生成时绑定</span>
        <strong>${escapeHtml(plannedBinding.label)}</strong>
        <small>${escapeHtml(plannedBinding.detail)}</small>
      </div>
      <div>
        <span>当前执行绑定</span>
        <strong>${escapeHtml(currentProject)}</strong>
        <small>${escapeHtml(currentEnvironment)}</small>
      </div>
      <div>
        <span>当前业务 token</span>
        <strong>${escapeHtml(authLabel)}</strong>
        <small>${escapeHtml(authDetail)}</small>
      </div>
      <button type="button" class="btn-sm ai" onclick="regenerateApiPlan(${jsArg(plan.plan_id)})">按当前绑定重新生成</button>
      <div class="api-plan-drift-guide">
        <strong>下一步：重新生成，不需要逐条编辑</strong>
        <span>这个候选是在旧绑定下生成的；按当前绑定重新生成后，平台会重新校验业务 token、环境变量和可执行数据。</span>
      </div>
    </section>
  `;
}

function renderApiPlanLaunchNotice() {
  if (!apiPlanLaunchNotice) return '';
  return `
    <section class="api-plan-launch-notice">
      <strong>已带入 ${escapeHtml(apiPlanLaunchNotice.endpointCount || 0)} 个接口</strong>
      <span>生成任务尚未开始；点击“生成测试资产”后才会调用 AI，并在这里显示排队、批次、日志和生成结果。</span>
      <small>${escapeHtml(apiPlanLaunchNotice.createdAt || '')}</small>
    </section>
  `;
}

function renderApiPlanReviewGuide(plan = {}) {
  if (plan.status === 'confirmed') return '';
  return `
    <section class="api-plan-review-guide">
      <div>
        <span>审阅目标</span>
        <strong>把 AI draft 变成可执行测试资产</strong>
      </div>
      <ul>
        <li>确认请求方法、路径、入参、鉴权变量和响应断言是否符合业务接口合同。</li>
        <li>待补数据不是失败；可以编辑 draft 用例补齐参数、Body 或断言，也可以先调试单条可执行用例。</li>
        <li>可执行项满足本次范围后确认后保存为测试资产，再进入平台 API 回归执行。</li>
      </ul>
    </section>
  `;
}

function renderApiPlanFacts(plan) {
  const revision = plan.asset_revision_id || plan.revision_id || plan.snapshot_id || (plan.revision_state || {}).planned_revision_id || '-';
  const aiTrace = apiPlanAiTrace(plan);
  const binding = apiPlanBindingFact(plan);
  const auth = apiPlanAuthFact(plan);
  const source = plan.source_name || (String(plan.source_id || '') === String(selectedApiAssetSource()?.source_id || '') ? selectedApiAssetSource()?.name : '') || plan.source_id || '-';
  return `
    <div class="api-plan-fact-grid">
      <div><span>来源</span><strong>${escapeHtml(source)}</strong><small>${escapeHtml(plan.source_id || '-')}</small></div>
      <div><span>接口版本</span><strong>${escapeHtml(revision)}</strong><small>${escapeHtml((plan.module_paths || []).join('、') || '未记录模块')}</small></div>
      <div><span>AI 轨迹</span><strong>${escapeHtml(aiTrace.label)}</strong><small>${escapeHtml(aiTrace.detail)}</small></div>
      <div><span>执行绑定</span><strong>${escapeHtml(binding.label)}</strong><small>${escapeHtml(binding.detail)}</small></div>
      <div><span>业务鉴权</span><strong>${escapeHtml(auth.label)}</strong><small>${escapeHtml(auth.detail)}</small></div>
      <div><span>就绪状态</span><strong>${escapeHtml((plan.execution_readiness || {}).state || '-')}</strong><small>可执行 ${escapeHtml(plan.executable_case_count || 0)} / 待补 ${escapeHtml(plan.needs_review_case_count || 0)}</small></div>
    </div>
  `;
}

function renderApiPlanList(plans) {
  if (!plans.length) return apiTestingEmpty('当前来源暂无待处理候选。');
  return `<div class="api-plan-card-list">${plans.map(plan => {
    const stale = (plan.revision_state || {}).state === 'stale';
    const needsReview = Number(plan.needs_review_case_count || 0);
    const executable = Number(plan.executable_case_count || 0);
    return `
      <article class="api-plan-card ${stale ? 'is-stale' : ''}">
        <header>
          <button type="button" class="api-plan-list-button" data-plan-id="${escapeHtml(plan.plan_id || '')}" onclick="openApiTestPlan(${jsArg(plan.plan_id)})"><strong>${escapeHtml(plan.name || plan.plan_id)}</strong><small>${escapeHtml(plan.created_at || '')}</small></button>
          <div>${apiStatusPill(apiPlanStatusText(plan.status), plan.status === 'confirmed' ? 'success' : 'warn')}${stale ? apiStatusPill('接口已变化', 'danger') : ''}</div>
        </header>
        <div class="api-plan-card-summary">
          <span><strong>${escapeHtml(plan.endpoint_count || 0)}</strong> 个接口</span>
          <span><strong>${escapeHtml(executable)}</strong> 条可执行</span>
          <span class="${needsReview ? 'warn' : ''}"><strong>${escapeHtml(needsReview)}</strong> 条待补</span>
        </div>
        <button type="button" class="btn-sm ghost api-plan-review-action" onclick="openApiTestPlan(${jsArg(plan.plan_id)})">审阅候选</button>
        <details class="api-plan-tech-detail"><summary>技术详情</summary>${renderApiPlanFacts(plan)}</details>
      </article>
    `;
  }).join('')}</div>`;
}

async function refreshApiPlanCards(capturedScopeKey = apiPlanGenerationScopeKey()) {
  if (!['api_plan', 'api_dashboard'].includes(activeWorkflow) || capturedScopeKey !== apiPlanGenerationScopeKey()) return;
  const sourceId = apiTestingProjectScope.sourceId;
  const controller = new AbortController();
  try {
    const response = await apiRequest(`/api-testing/plans${sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : ''}`, { signal: controller.signal });
    if (!['api_plan', 'api_dashboard'].includes(activeWorkflow) || capturedScopeKey !== apiPlanGenerationScopeKey()) return;
    apiTestingPlans = await loadApiPlanDetails(response.plans || [], sourceId, controller);
    if (!['api_plan', 'api_dashboard'].includes(activeWorkflow) || capturedScopeKey !== apiPlanGenerationScopeKey()) return;
    const target = document.getElementById('api-plan-list-region');
    const candidatePlans = apiCandidatePlans();
    if (target) target.innerHTML = `<div class="api-section-heading"><div><span>待处理</span><h3>候选计划</h3></div><small>${candidatePlans.length} 个计划</small></div>${renderApiPlanList(candidatePlans)}`;
  } catch (_) {
    // Generation remains visible even when the secondary plan-list refresh fails.
  }
}

async function openApiTestPlan(planId) {
  const target = document.getElementById('api-plan-result');
  const requestId = ++apiPlanPageRequestId;
  const capturedScopeKey = apiPlanGenerationScopeKey();
  const sourceId = apiTestingProjectScope.sourceId;
  if (target) target.innerHTML = `<h3>计划详情</h3>${apiTestingEmpty('正在读取计划合同...')}`;
  try {
    const query = sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : '';
    const data = await apiRequest(`/api-testing/plans/${encodeURIComponent(planId)}${query}`);
    if (requestId !== apiPlanPageRequestId || capturedScopeKey !== apiPlanGenerationScopeKey() || !['api_plan', 'api_dashboard'].includes(activeWorkflow)) return;
    apiTestingCurrentPlan = data.plan || null;
    if (target) target.innerHTML = renderApiPlanDetail(apiTestingCurrentPlan || {});
  } catch (error) {
    if (requestId !== apiPlanPageRequestId || capturedScopeKey !== apiPlanGenerationScopeKey() || !['api_plan', 'api_dashboard'].includes(activeWorkflow)) return;
    if (target) target.innerHTML = `<h3>读取失败</h3>${apiTestingEmpty(error.message || '计划详情读取失败')}`;
    showToast(error.message || '计划详情读取失败', 'error');
  }
}

function apiPlanCaseEndpointKey(item = {}) {
  const request = item.request || {};
  return String(item.endpoint_key || item.endpoint_id || item.asset_endpoint_id || `${request.method || ''} ${request.path || item.endpoint || ''}`).trim();
}

function apiPlanCaseMissingCategory(value) {
  const path = String(value || '');
  if (path.startsWith('request.body')) return '请求体数据';
  if (path.startsWith('request.query')) return '查询参数';
  if (path.startsWith('request.path_params')) return '路径参数';
  if (path.startsWith('request.headers') || /auth|token|cookie/i.test(path)) return '环境鉴权';
  if (path.startsWith('assertions')) return '响应断言';
  return '其他数据';
}

function groupApiPlanCasesByEndpoint(cases = [], plan = {}) {
  const endpointByKey = new Map();
  apiTestingEndpoints.forEach(endpoint => {
    const requestKey = `${endpoint.method || ''} ${endpoint.path || ''}`.trim();
    [endpoint.endpoint_key, endpoint.endpoint_id, requestKey].filter(Boolean).forEach(key => endpointByKey.set(String(key), endpoint));
  });
  const changedKeys = new Set([
    ...(plan.changed_endpoint_keys || []),
    ...((plan.revision_state || {}).changed_endpoint_keys || []),
    ...((plan.revision_state || {}).affected_endpoint_keys || []),
  ].map(String));
  const affectedCaseIds = new Set(((plan.revision_state || {}).affected_case_ids || []).map(String));
  const groups = new Map();
  (cases || []).forEach(item => {
    const key = apiPlanCaseEndpointKey(item) || `case:${item.case_id || groups.size}`;
    const endpoint = endpointByKey.get(key)
      || endpointByKey.get(`${item.request?.method || ''} ${item.request?.path || ''}`.trim())
      || {};
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        endpoint,
        method: endpoint.method || item.request?.method || '',
        path: endpoint.path || item.request?.path || item.endpoint || '',
        name: endpoint.name || item.endpoint_name || '',
        module: apiEndpointModulePath(endpoint),
        cases: [],
        executableCount: 0,
        needsReviewCount: 0,
        missing: new Set(),
        changed: changedKeys.has(key) || changedKeys.has(String(endpoint.endpoint_key || '')),
      });
    }
    const group = groups.get(key);
    group.cases.push(item);
    if (affectedCaseIds.has(String(item.case_id || ''))) group.changed = true;
    const executable = (item.readiness || {}).state === 'executable';
    if (executable) group.executableCount += 1;
    else group.needsReviewCount += 1;
    ((item.readiness || {}).missing || []).forEach(value => group.missing.add(String(value)));
  });
  return Array.from(groups.values()).sort((left, right) => (
    left.module.localeCompare(right.module, 'zh-Hans-CN')
    || left.path.localeCompare(right.path)
  ));
}

function apiPlanFilteredEndpointGroups(plan) {
  const state = apiPlanReviewState(plan);
  const query = state.search.trim().toLowerCase();
  return groupApiPlanCasesByEndpoint(plan.cases || [], plan).filter(group => {
    if (state.apiPlanReviewFilter === 'executable' && !group.executableCount) return false;
    if (state.apiPlanReviewFilter === 'needs_review' && !group.needsReviewCount) return false;
    if (state.apiPlanReviewFilter === 'changed' && !group.changed) return false;
    if (
      state.missingCategory
      && !Array.from(group.missing).some(
        value => apiPlanCaseMissingCategory(value) === state.missingCategory
      )
    ) return false;
    if (!query) return true;
    return [
      group.method,
      group.path,
      group.name,
      group.module,
      ...group.cases.map(item => item.name || ''),
    ].join(' ').toLowerCase().includes(query);
  });
}

function apiPlanReviewState(plan = apiTestingCurrentPlan || {}) {
  const planId = String(plan?.plan_id || '__unselected__');
  if (!apiPlanReviewStateByPlan.has(planId)) {
    apiPlanReviewStateByPlan.set(planId, {
      apiPlanReviewFilter: 'all',
      search: '',
      page: 1,
      missingCategory: '',
      expandedGroups: new Set(),
      initialized: false,
    });
  }
  return apiPlanReviewStateByPlan.get(planId);
}

function toggleApiPlanReviewGroup(groupKey, open) {
  const state = apiPlanReviewState();
  if (open) state.expandedGroups.add(String(groupKey));
  else state.expandedGroups.delete(String(groupKey));
  state.initialized = true;
}

function setApiPlanReviewFilter(value) {
  const state = apiPlanReviewState();
  state.apiPlanReviewFilter = String(value || 'all');
  state.page = 1;
  rerenderApiPlanReview();
}

function setApiPlanReviewSearch(value) {
  const state = apiPlanReviewState();
  state.search = String(value || '');
  state.page = 1;
  rerenderApiPlanReview();
}

function setApiPlanReviewPage(page) {
  apiPlanReviewState().page = Math.max(1, Number(page || 1));
  rerenderApiPlanReview();
}

function setApiPlanMissingCategory(category) {
  const state = apiPlanReviewState();
  const selected = String(category || '');
  state.missingCategory = state.missingCategory === selected ? '' : selected;
  state.page = 1;
  rerenderApiPlanReview();
}

function rerenderApiPlanReview() {
  const target = document.getElementById('api-plan-endpoint-groups');
  if (target && apiTestingCurrentPlan) target.innerHTML = renderApiPlanEndpointGroups(apiTestingCurrentPlan);
}

function apiPlanCaseKey(item = {}) {
  return String(item.case_id || item.id || item.name || '').trim();
}

function editApiPlanCase(planId, caseId) {
  if (!apiTestingCurrentPlan || String(apiTestingCurrentPlan.plan_id || '') !== String(planId || '')) return;
  const target = (apiTestingCurrentPlan.cases || []).find(item => apiPlanCaseKey(item) === String(caseId || ''));
  if (!target) {
    showToast('未找到要编辑的用例', 'error');
    return;
  }
  apiPlanCaseEditor = {
    planId: String(planId || ''),
    caseId: String(caseId || ''),
    text: JSON.stringify(target, null, 2),
  };
  rerenderApiPlanReview();
}

function cancelApiPlanCaseEdit() {
  apiPlanCaseEditor = { planId: '', caseId: '', text: '' };
  rerenderApiPlanReview();
}

function updateApiPlanCaseEditText(value) {
  apiPlanCaseEditor.text = String(value || '');
}

function apiPlanCaseEditorObject() {
  try {
    const parsed = JSON.parse(apiPlanCaseEditor.text || '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch (_) {
    return {};
  }
}

function apiPlanCaseEditorJson(value) {
  return JSON.stringify(value && typeof value === 'object' ? value : {}, null, 2);
}

function apiPlanCaseEditorLines(value) {
  if (Array.isArray(value)) return value.map(item => (typeof item === 'string' ? item : apiCaseAssertionText(item))).join('\n');
  if (typeof value === 'string') return value;
  if (value == null) return '';
  return String(value);
}

function apiPlanCaseEditorStatusText(assertions = []) {
  const status = Array.isArray(assertions) ? assertions.find(item => item && typeof item === 'object' && item.type === 'status') : null;
  const expected = status?.expected;
  if (Array.isArray(expected)) return expected.join(', ');
  if (expected == null) return '';
  return String(expected);
}

function apiPlanCaseEditorStatusValues(value) {
  return String(value || '')
    .split(/[\s,，/]+/)
    .map(item => item.trim())
    .filter(Boolean)
    .map(item => (/^\d+$/.test(item) ? Number(item) : item));
}

function apiPlanCaseEditorReadJson(root, selector, fallback = {}) {
  const input = root.querySelector(selector);
  const text = String(input?.value || '').trim();
  if (!text) return fallback && typeof fallback === 'object' ? fallback : {};
  return JSON.parse(text);
}

function apiPlanCaseEditorReadLines(root, selector) {
  const input = root.querySelector(selector);
  return String(input?.value || '')
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean);
}

function buildApiPlanCaseFromEditorForm(root) {
  const current = apiPlanCaseEditorObject();
  const request = current.request && typeof current.request === 'object' ? {...current.request} : {};
  const value = selector => String(root.querySelector(selector)?.value || '').trim();
  const assertionLines = apiPlanCaseEditorReadLines(root, '[data-case-field="assertion_texts"]');
  const statusValues = apiPlanCaseEditorStatusValues(value('[data-case-field="assertions.status"]'));
  const assertions = (Array.isArray(current.assertions) ? current.assertions : [])
    .filter(item => !(item && typeof item === 'object' && item.type === 'status'));
  if (statusValues.length) assertions.unshift({type: 'status', operator: 'in', expected: statusValues});
  const edited = {
    ...current,
    name: value('[data-case-field="name"]') || current.name,
    type: value('[data-case-field="type"]') || current.type || 'positive',
    priority: value('[data-case-field="priority"]') || current.priority || 'P0',
    steps: apiPlanCaseEditorReadLines(root, '[data-case-field="steps"]'),
    assertion_texts: assertionLines,
    request: {
      ...request,
      method: value('[data-case-field="request.method"]') || request.method,
      path: value('[data-case-field="request.path"]') || request.path,
      path_params: apiPlanCaseEditorReadJson(root, '[data-case-json-field="request.path_params"]', request.path_params || {}),
      query: apiPlanCaseEditorReadJson(root, '[data-case-json-field="request.query"]', request.query || {}),
      headers: apiPlanCaseEditorReadJson(root, '[data-case-json-field="request.headers"]', request.headers || {}),
      body: apiPlanCaseEditorReadJson(root, '[data-case-json-field="request.body"]', request.body || {}),
    },
  };
  edited.assertions = assertions.length ? assertions : assertionLines;
  return edited;
}

function updateApiPlanCaseEditorFromForm(root) {
  if (!root) return false;
  const error = root.querySelector('[data-case-editor-error]');
  const raw = root.querySelector('[data-case-raw-json]');
  try {
    const edited = buildApiPlanCaseFromEditorForm(root);
    apiPlanCaseEditor.text = JSON.stringify(edited, null, 2);
    if (raw) raw.value = apiPlanCaseEditor.text;
    if (error) {
      error.hidden = true;
      error.textContent = '';
    }
    return true;
  } catch (e) {
    if (error) {
      error.hidden = false;
      error.textContent = `请求入参 JSON 格式不正确：${e.message || e}`;
    }
    return false;
  }
}

async function saveApiPlanCaseEdit(planId, caseId) {
  if (!apiTestingCurrentPlan || String(apiTestingCurrentPlan.plan_id || '') !== String(planId || '')) return;
  const editorRoot = Array.from(document.querySelectorAll('.api-plan-case-editor')).find(root => (
    root.dataset.planId === String(planId || '') && root.dataset.caseId === String(caseId || '')
  ));
  if (editorRoot && !updateApiPlanCaseEditorFromForm(editorRoot)) return;
  let edited;
  try {
    edited = JSON.parse(apiPlanCaseEditor.text || '{}');
  } catch (e) {
    showToast('用例 JSON 格式不正确', 'error');
    return;
  }
  const cases = (apiTestingCurrentPlan.cases || []).map(item => (
    apiPlanCaseKey(item) === String(caseId || '') ? edited : item
  ));
  try {
    const data = await apiRequest(`/api-testing/plans/${encodeURIComponent(planId)}/cases`, {
      method: 'POST',
      body: { source_id: currentApiExecutionSourceId(), cases },
    });
    apiTestingCurrentPlan = data.plan || null;
    apiPlanCaseEditor = { planId: '', caseId: '', text: '' };
    showToast('✓ 已保存 AI draft 用例修改，并重新校验可执行性', 'success');
    const target = document.getElementById('api-plan-result');
    if (target && apiTestingCurrentPlan) target.innerHTML = renderApiPlanDetail(apiTestingCurrentPlan);
  } catch (e) {
    showToast(e.message || '保存用例失败', 'error');
  }
}

function renderApiPlanCaseEditor(plan, item) {
  const caseId = apiPlanCaseKey(item);
  if (
    plan?.status !== 'draft'
    || apiPlanCaseEditor.planId !== String(plan?.plan_id || '')
    || apiPlanCaseEditor.caseId !== caseId
  ) return '';
  const current = apiPlanCaseEditorObject();
  const request = current.request && typeof current.request === 'object' ? current.request : {};
  const formInput = 'oninput="updateApiPlanCaseEditorFromForm(this.closest(\'.api-plan-case-editor\'))"';
  const assertionText = current.assertion_texts?.length ? apiPlanCaseEditorLines(current.assertion_texts) : apiPlanCaseEditorLines(current.assertions || []);
  const statusText = apiPlanCaseEditorStatusText(current.assertions || []);
  return `
    <div class="api-plan-case-editor" data-plan-id="${escapeHtml(plan.plan_id || '')}" data-case-id="${escapeHtml(caseId)}">
      <div class="api-plan-case-editor-head">
        <div><strong>编辑 AI 生成用例</strong><span>按计划、入参和校验修改；保存后平台会重新校验可执行性。</span></div>
        ${apiStatusPill('Draft', 'warn')}
      </div>
      <div class="api-plan-case-form-grid">
        <label><span>用例名称</span><input data-case-field="name" value="${escapeHtml(current.name || '')}" ${formInput}></label>
        <label><span>优先级</span><input data-case-field="priority" value="${escapeHtml(current.priority || 'P0')}" ${formInput}></label>
        <label><span>类型</span><input data-case-field="type" value="${escapeHtml(current.type || 'positive')}" ${formInput}></label>
        <label><span>请求方法</span><input data-case-field="request.method" value="${escapeHtml(request.method || '')}" ${formInput}></label>
        <label><span>HTTP 状态码</span><input data-case-field="assertions.status" value="${escapeHtml(statusText || '200')}" placeholder="200, 201" ${formInput}></label>
        <label class="wide"><span>请求路径</span><input data-case-field="request.path" value="${escapeHtml(request.path || current.endpoint || '')}" ${formInput}></label>
        <label class="wide"><span>执行计划</span><textarea data-case-field="steps" ${formInput} spellcheck="false">${escapeHtml(apiPlanCaseEditorLines(current.steps || []))}</textarea></label>
        <label><span>路径参数 JSON</span><textarea data-case-json-field="request.path_params" ${formInput} spellcheck="false">${escapeHtml(apiPlanCaseEditorJson(request.path_params))}</textarea></label>
        <label><span>Query JSON</span><textarea data-case-json-field="request.query" ${formInput} spellcheck="false">${escapeHtml(apiPlanCaseEditorJson(request.query))}</textarea></label>
        <label><span>Header JSON</span><textarea data-case-json-field="request.headers" ${formInput} spellcheck="false">${escapeHtml(apiPlanCaseEditorJson(request.headers))}</textarea></label>
        <label class="wide"><span>请求入参 Body JSON</span><textarea data-case-json-field="request.body" ${formInput} spellcheck="false">${escapeHtml(apiPlanCaseEditorJson(request.body))}</textarea></label>
        <label class="wide"><span>校验断言</span><textarea data-case-field="assertion_texts" ${formInput} spellcheck="false">${escapeHtml(assertionText)}</textarea></label>
      </div>
      <p class="api-plan-case-editor-error" data-case-editor-error hidden></p>
      <details class="api-plan-case-raw-json">
        <summary>高级：原始 JSON</summary>
        <textarea data-case-raw-json oninput="updateApiPlanCaseEditText(this.value)" spellcheck="false">${escapeHtml(apiPlanCaseEditor.text || '')}</textarea>
      </details>
      <div class="generation-record-actions">
        <button type="button" class="btn-sm" onclick="cancelApiPlanCaseEdit()">取消</button>
        <button type="button" class="btn-sm primary" onclick="saveApiPlanCaseEdit(${jsArg(plan.plan_id)}, ${jsArg(caseId)})">保存并重新校验</button>
      </div>
    </div>
  `;
}

function renderApiPlanCaseRow(item, plan = {}) {
  const executable = (item.readiness || {}).state === 'executable';
  const missing = (item.readiness || {}).missing || [];
  const caseId = apiPlanCaseKey(item);
  const canEdit = plan.status === 'draft';
  const debugKey = `${plan.plan_id || ''}::${caseId}`;
  const debugStarting = apiCaseDebugStartingKey === debugKey;
  const canDebug = executable
    && plan.status === 'draft'
    && (plan.revision_state || {}).state !== 'stale'
    && !(plan.binding_drift || []).length;
  return `
    <article class="api-case-row">
      <div class="api-case-row-name">
        <strong>${escapeHtml(item.name || item.case_id || '未命名用例')}</strong>
        <span>${escapeHtml(item.type || 'case')} · ${escapeHtml(item.priority || '-')}</span>
      </div>
      <div class="api-case-row-request"><span>请求</span><strong>${escapeHtml(apiCaseRequestText(item))}</strong></div>
      <div class="api-case-row-assertions"><span>校验</span><strong>${escapeHtml((item.assertions || []).map(apiCaseAssertionText).join('；') || '尚未配置')}</strong></div>
      <div class="api-case-row-state">
        ${apiStatusPill(executable ? '可执行' : '待补数据', executable ? 'success' : 'warn')}
        ${missing.length ? `<small>${escapeHtml(missing.join('；'))}</small>` : ''}
        ${canDebug ? `<button type="button" class="btn-sm primary api-case-debug-action" onclick="debugApiPlanCase(${jsArg(plan.plan_id)}, ${jsArg(caseId)})" ${debugStarting ? 'disabled' : ''}>${debugStarting ? '调试提交中' : '调试单条'}</button>` : ''}
        ${canEdit ? `<button type="button" class="btn-sm ghost" onclick="editApiPlanCase(${jsArg(plan.plan_id)}, ${jsArg(caseId)})">编辑</button>` : ''}
      </div>
      ${renderApiPlanCaseEditor(plan, item)}
    </article>
  `;
}

async function debugApiPlanCase(planId, caseId) {
  const debugKey = `${planId || ''}::${caseId || ''}`;
  if (apiCaseDebugStartingKey) {
    showToast('正在创建单条调试，请勿重复提交', 'warn');
    return;
  }
  apiCaseDebugStartingKey = debugKey;
  rerenderApiPlanReview();
  try {
    const data = await apiRequest('/api-testing/cases/debug', {
      method: 'POST',
      body: {source_id: apiTestingProjectScope.sourceId || apiAssetSelectedSourceId, plan_id: planId, case_id: caseId}
    });
    const execution = data.execution || {};
    showToast('✓ 单条调试已排队', 'success');
    apiExecutionActiveId = execution.execution_id || '';
    await showApiExecutionPage();
  } catch (e) {
    showToast(e.message || '单条调试启动失败', 'error');
    if (['api_plan', 'api_dashboard'].includes(activeWorkflow)) rerenderApiPlanReview();
  } finally {
    apiCaseDebugStartingKey = '';
  }
}

function renderApiPlanEndpointGroups(plan) {
  const state = apiPlanReviewState(plan);
  const allGroups = groupApiPlanCasesByEndpoint(plan.cases || [], plan);
  const groups = apiPlanFilteredEndpointGroups(plan);
  const pageSize = 20;
  const pageCount = Math.max(1, Math.ceil(groups.length / pageSize));
  state.page = Math.min(state.page, pageCount);
  const pageGroups = groups.slice((state.page - 1) * pageSize, state.page * pageSize);
  const missingCategories = new Map();
  allGroups.forEach(group => group.missing.forEach(value => {
    const category = apiPlanCaseMissingCategory(value);
    missingCategories.set(category, (missingCategories.get(category) || 0) + 1);
  }));
  const allowDefaultOpen = !state.initialized;
  const html = `
    <div class="api-plan-review-toolbar">
      <div class="api-plan-review-filters" role="group" aria-label="用例筛选">
        ${[
          ['all', '全部', allGroups.length],
          ['executable', '可执行', allGroups.filter(group => group.executableCount).length],
          ['needs_review', '待补数据', allGroups.filter(group => group.needsReviewCount).length],
          ['changed', '本版变更', allGroups.filter(group => group.changed).length],
        ].map(([value, label, count]) => `<button type="button" class="${state.apiPlanReviewFilter === value ? 'active' : ''}" onclick="setApiPlanReviewFilter(${jsArg(value)})">${escapeHtml(label)} <span>${escapeHtml(count)}</span></button>`).join('')}
      </div>
      <input type="search" value="${escapeHtml(state.search)}" placeholder="搜索接口或用例" aria-label="搜索接口或用例" oninput="setApiPlanReviewSearch(this.value)">
    </div>
    ${missingCategories.size ? `<div class="api-plan-missing-summary"><strong>待补数据集中在</strong>${Array.from(missingCategories, ([name, count]) => `<button type="button" class="${state.missingCategory === name ? 'active' : ''}" onclick="setApiPlanMissingCategory(${jsArg(name)})">${escapeHtml(name)} ${escapeHtml(count)}</button>`).join('')}</div>` : ''}
    <div class="api-case-group-list">${pageGroups.length ? pageGroups.map((group, index) => {
      const open = state.expandedGroups.has(group.key) || (
        allowDefaultOpen
        && state.page === 1
        && index === 0
      );
      return `
        <details class="api-case-group" data-endpoint-key="${escapeHtml(group.key)}" ${open ? 'open' : ''} ontoggle="toggleApiPlanReviewGroup(${jsArg(group.key)}, this.open)">
          <summary onclick="toggleApiPlanReviewGroup(${jsArg(group.key)}, !this.parentElement.open)">
            <div class="api-case-group-route"><span class="api-method ${escapeHtml((group.method || '').toLowerCase())}">${escapeHtml(group.method || '-')}</span><strong>${escapeHtml(group.path || group.key)}</strong><small>${escapeHtml(group.name || group.module || '未命名接口')}</small></div>
            <div class="api-case-group-counts"><span>${escapeHtml(group.cases.length)} 条用例</span><strong>${escapeHtml(group.executableCount)} 可执行</strong>${group.needsReviewCount ? `<em>${escapeHtml(group.needsReviewCount)} 待补</em>` : ''}</div>
          </summary>
          <div class="api-case-group-meta"><span>${escapeHtml(group.module || '未分组')}</span>${group.changed ? apiStatusPill('本版变更', 'warn') : ''}</div>
          <div class="api-case-group-cases">${group.cases.map(item => renderApiPlanCaseRow(item, plan)).join('')}</div>
        </details>
      `;
    }).join('') : apiTestingEmpty('当前筛选没有匹配的接口用例。')}</div>
    ${pageCount > 1 ? `<div class="api-plan-review-pagination"><button class="btn-sm" onclick="setApiPlanReviewPage(${state.page - 1})" ${state.page <= 1 ? 'disabled' : ''}>上一页</button><span>${state.page} / ${pageCount}</span><button class="btn-sm" onclick="setApiPlanReviewPage(${state.page + 1})" ${state.page >= pageCount ? 'disabled' : ''}>下一页</button></div>` : ''}
  `;
  state.initialized = true;
  return html;
}

function renderApiPlanDetail(plan) {
  const cases = plan.cases || [];
  const readiness = plan.execution_readiness || {};
  const revision = plan.revision_state || {};
  const isStale = revision.state === 'stale';
  const bindingDrift = plan.binding_drift || [];
  const canConfirm = plan.status === 'draft' && readiness.can_confirm === true && !isStale && !bindingDrift.length;
  const canExecute = readiness.can_execute === true && !isStale && !bindingDrift.length;
  const actionReason = bindingDrift[0] || apiPlanReadinessReason(plan);
  const missing = readiness.missing || [];
  const missingSummary = new Map();
  missing.forEach(item => {
    const category = apiPlanCaseMissingCategory(item);
    missingSummary.set(category, (missingSummary.get(category) || 0) + 1);
  });
  const sourceText = plan.source === 'ai' ? 'AI 设计，平台校验' : (plan.source === 'local_fallback' ? '规则兜底，平台校验' : '规则生成');
  let primaryAction = '';
  if (isStale) {
    primaryAction = `<button class="btn-sm ai" onclick="regenerateApiPlan(${jsArg(plan.plan_id)})">按最新接口重新生成</button>`;
  } else if (bindingDrift.length) {
    primaryAction = `<button class="btn-sm ai" onclick="regenerateApiPlan(${jsArg(plan.plan_id)})">按当前绑定重新生成</button>`;
  } else if (plan.status === 'draft' && canConfirm) {
    primaryAction = `<button class="btn-sm success" onclick="confirmApiTestPlan(${jsArg(plan.plan_id)})">保存为测试资产</button>`;
  } else if (plan.status === 'draft') {
    primaryAction = `<button class="btn-sm primary" onclick="setApiPlanReviewFilter('needs_review')">查看待补数据</button>`;
  } else if (canExecute) {
    primaryAction = '<button class="btn-sm primary" onclick="showApiExecutionPage()">进入执行</button>';
  } else {
    primaryAction = `<button class="btn-sm" disabled title="${escapeHtml(actionReason)}">${escapeHtml(actionReason || '当前不可执行')}</button>`;
  }
  return `
    <div class="api-plan-detail-head"><div><span>${plan.status === 'confirmed' ? '已保存测试资产' : 'AI 草稿审阅'}</span><h3>${escapeHtml(plan.name || 'API 测试计划')}</h3></div>${isStale ? apiStatusPill('接口已变化', 'danger') : apiStatusPill(apiPlanStatusText(plan.status), plan.status === 'confirmed' ? 'success' : 'warn')}</div>
    <div class="api-plan-case-origin-banner" data-source="${escapeHtml(plan.source || '')}">
      <strong>${plan.status === 'confirmed' ? '已保存测试资产' : 'AI 生成结果'}</strong>
      <span>${plan.status === 'confirmed' ? '该计划已保存为测试资产，可进入平台 API 执行。' : '这是 AI 生成的 draft 草稿，请先按接口审阅用例明细；确认后点“保存为测试资产”。'}</span>
      <small>${escapeHtml(sourceText)} · 业务鉴权 ${plan.auth_binding?.configured ? '已绑定平台安全 profile' : '未配置业务用户登录 token'}</small>
    </div>
    ${renderApiPlanReviewGuide(plan)}
    <div class="review-stats compact api-plan-readiness">
      <div class="review-stat"><strong>${escapeHtml(plan.endpoint_count || 0)}</strong><span>接口</span></div>
      <div class="review-stat"><strong>${escapeHtml(plan.case_count || cases.length)}</strong><span>用例</span></div>
      <div class="review-stat"><strong>${escapeHtml(plan.executable_case_count || 0)}</strong><span>可执行</span></div>
      <div class="review-stat"><strong>${escapeHtml(plan.needs_review_case_count || 0)}</strong><span>待补数据</span></div>
    </div>
    <div class="api-plan-generation-method">
      <strong>${escapeHtml(sourceText)}</strong>
      <ol><li>读取接口合同</li><li>设计正常与异常场景</li><li>校验请求和断言</li><li>标记可执行项</li></ol>
    </div>
    ${missing.length ? `<div class="api-readiness-missing api-plan-missing-actions"><strong>仍需补充：</strong>${Array.from(missingSummary, ([name, count]) => `<button type="button" onclick="setApiPlanMissingCategory(${jsArg(name)})">${escapeHtml(name)} ${escapeHtml(count)} 项</button>`).join('')}</div>` : ''}
    ${bindingDrift.length ? `<div class="api-stale-warning">执行绑定已变化：${escapeHtml(bindingDrift.join('、'))}</div>` : ''}
    ${renderApiPlanBindingDriftPanel(plan)}
    ${isStale ? `<div class="api-stale-warning">${escapeHtml(actionReason)}</div>` : ''}
    <div class="generation-record-actions api-plan-primary-action">${primaryAction}</div>
    <details class="api-plan-tech-detail api-plan-facts-detail"><summary>来源、AI 与执行绑定</summary>${renderApiPlanFacts(plan)}<div class="api-plan-scope-facts"><span>Plan <code>${escapeHtml(plan.plan_id || '-')}</code></span></div></details>
    <section class="api-plan-endpoint-review">
      <div class="api-section-heading"><div><span>按接口审阅</span><h3>用例明细</h3></div><small>${escapeHtml(groupApiPlanCasesByEndpoint(cases, plan).length)} 个接口组</small></div>
      <div id="api-plan-endpoint-groups">${renderApiPlanEndpointGroups(plan)}</div>
    </section>
  `;
}

async function regenerateApiPlan(planId) {
  const plan = String(apiTestingCurrentPlan?.plan_id || '') === String(planId || '') ? apiTestingCurrentPlan : null;
  if (plan) {
    const state = apiModuleSelectionState();
    const storedKeys = plan.selected_endpoint_keys || [];
    const selectedKeys = new Set((storedKeys.length ? storedKeys : (plan.endpoints || []).map(endpoint => endpoint.endpoint_key)).map(String).filter(Boolean));
    state.endpointIds.clear();
    apiTestingEndpoints.forEach(endpoint => {
      if (selectedKeys.has(String(endpoint.endpoint_key || '')) && endpoint.endpoint_id) state.endpointIds.add(String(endpoint.endpoint_id));
    });
    state.selectedModules.clear();
    (plan.module_paths || []).forEach(path => state.selectedModules.add(apiNormalizeModulePath(path)));
  }
  await startApiPlanGeneration();
}

async function generateApiTestPlan() {
  return startApiPlanGeneration();
}

async function confirmApiTestPlan(planId) {
  try {
    const data = await apiRequest('/api-testing/plans/confirm', { method: 'POST', body: { plan_id: planId } });
    apiTestingCurrentPlan = data.plan || null;
    showToast('✓ 已保存为测试资产', 'success');
    if (activeWorkflow === 'api_dashboard') await showApiTestingDashboard();
    else await showApiPlanPage();
  } catch(e) {
    showToast(e.message || '保存测试资产失败', 'error');
  }
}

function confirmedApiBaselines(plans = apiTestingPlans) {
  return (plans || []).filter(plan => plan.status === 'confirmed');
}

function renderApiBaselineList(plans) {
  if (!plans.length) {
    return `<div class="api-execution-empty"><strong>当前项目还没有测试资产</strong><button class="btn-sm ai" onclick="showApiPlanPage()">生成 AI 草稿</button></div>`;
  }
  return `<div class="api-baseline-list">${plans.map(plan => {
    const revision = plan.revision_state || {};
    const stale = revision.state === 'stale';
    const modules = (plan.module_paths || []).join('、') || '未记录模块';
    const executable = Number(plan.executable_case_count || 0);
    const needsReview = Number(plan.needs_review_case_count || 0);
    return `
      <article class="api-baseline-row ${stale ? 'is-stale' : ''}" data-api-baseline-plan-id="${escapeHtml(plan.plan_id || '')}">
        <div class="api-baseline-identity">
          <div><strong>${escapeHtml(plan.name || plan.plan_id)}</strong>${apiStatusPill(stale ? '待更新' : '可执行', stale ? 'danger' : 'success')}</div>
          <span>${escapeHtml(modules)}</span>
          <small>保存于 ${escapeHtml(plan.confirmed_at || plan.created_at || '-')}</small>
        </div>
        <div class="api-baseline-metrics">
          <span><strong>${escapeHtml(String(Number(plan.endpoint_count || 0)))}</strong>接口</span>
          <span><strong>${escapeHtml(String(executable))}</strong>可执行用例</span>
          <span><strong>${escapeHtml(String(needsReview))}</strong>待补</span>
        </div>
        <div class="api-baseline-actions">
          <button class="btn-sm" onclick="openApiBaselinePlan(${jsArg(plan.plan_id)})">查看用例</button>
          ${stale
            ? `<button class="btn-sm ai" onclick="regenerateApiBaseline(${jsArg(plan.plan_id)})">按最新接口重新生成</button>`
            : `<button class="btn-sm primary" onclick="openApiBaselineExecution(${jsArg(plan.plan_id)})">进入执行</button>`}
        </div>
        <details class="api-plan-tech-detail">
          <summary>版本与影响</summary>
          <div class="api-baseline-tech">
            <span>Revision <code>${escapeHtml(revision.planned_revision_id || plan.asset_revision_id || plan.snapshot_id || '-')}</code></span>
            <span>${stale ? escapeHtml(revision.reason || '接口版本已变化') : '当前接口版本有效'}</span>
            ${stale ? `<span>受影响 ${escapeHtml((revision.affected_case_ids || []).length)} 条用例</span>` : ''}
          </div>
        </details>
      </article>
    `;
  }).join('')}</div>`;
}

async function showApiBaselinesPage() {
  return showApiPlanPage();
}

async function selectApiBaselineSource(sourceId) {
  abortApiProjectScopeRequests();
  apiAssetSelectedSourceId = String(sourceId || '');
  apiTestingProjectScope = { sourceId: apiAssetSelectedSourceId, revisionId: '' };
  await showApiBaselinesPage();
}

async function openApiBaselinePlan(planId) {
  await showApiPlanPage();
  if (activeWorkflow === 'api_plan') await openApiTestPlan(planId);
}

async function regenerateApiBaseline(planId) {
  await showApiPlanPage();
  if (activeWorkflow !== 'api_plan') return;
  await openApiTestPlan(planId);
  if (String(apiTestingCurrentPlan?.plan_id || '') === String(planId || '')) {
    await regenerateApiPlan(planId);
  }
}

async function openApiBaselineExecution(planId) {
  await showApiExecutionPage();
  const row = document.querySelector(`[data-api-execution-plan-id="${CSS.escape(String(planId || ''))}"]`);
  if (row) {
    row.classList.add('is-focused');
    row.scrollIntoView({behavior: 'smooth', block: 'center'});
  }
}

function stopApiExecutionPolling(abortRequest = false) {
  if (apiExecutionPollTimer) clearTimeout(apiExecutionPollTimer);
  apiExecutionPollTimer = null;
  if (abortRequest) {
    apiExecutionPollController?.abort();
    apiExecutionPollController = null;
    apiExecutionPollRequestId += 1;
  }
}

function apiConnectionText(state) {
  return ({ connected: '连接正常', disconnected: '连接异常', not_configured: '未配置' })[state] || '状态未知';
}

function apiReadinessText(state) {
  return ({
    not_configured: '等待配置连接',
    disconnected: '连接检查失败',
    connected_needs_setup: '执行能力待配置',
    ready_no_plan: '等待已确认计划',
    ready_no_executable_plan: '计划待补测试数据',
    ready: '可以执行',
    running: '正在执行',
    failed: '最近执行失败'
  })[state] || '等待检查';
}

function apiExecutionStateText(state) {
  return ({ queued: '排队中', running: '执行中', succeeded: '已完成', failed: '失败', cancelled: '已取消' })[state] || state || '-';
}

function apiPhaseStateText(state) {
  return ({ waiting: '等待', running: '进行中', succeeded: '完成', failed: '失败', skipped: '跳过' })[state] || state || '等待';
}

function apiDurationText(value) {
  const total = Math.max(0, Number(value || 0));
  if (!Number.isFinite(total)) return '-';
  const seconds = Math.floor(total);
  if (seconds < 60) return `${seconds}秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}分${seconds % 60}秒`;
  const hours = Math.floor(minutes / 60);
  return `${hours}小时${minutes % 60}分`;
}

function apiSelectOptions(items, selectedId, emptyText) {
  const options = (items || []).map(item => `
    <option value="${escapeHtml(item.id || '')}" ${String(item.id || '') === String(selectedId || '') ? 'selected' : ''} ${item.enabled === false ? 'disabled' : ''}>
      ${escapeHtml(item.name || item.id || '-')}
    </option>
  `).join('');
  return `<option value="">${escapeHtml(emptyText)}</option>${options}`;
}

async function showApiExecutionPage() {
  stopApiExecutionPolling(true);
  apiBusinessAuthEditing = false;
  const area = setApiTestingPage('api_execution', '执行测试', '平台直接执行已保存测试资产，实时查看日志和报告。');
  if (!area) return;
  area.innerHTML = `
    <div class="api-testing-page api-execution-console">
      <div id="api-workflow-stepper">${renderApiWorkflowStepper({workflow: 'api_execution'})}</div>
      <section id="api-execution-header" class="api-execution-header">${apiTestingEmpty('正在检查 API 执行环境...')}</section>
      <section id="api-active-run" class="api-active-run" hidden></section>
      <section class="api-execution-plans-section">
        <div class="api-section-heading"><div><span>执行测试资产</span><h2>已保存测试资产</h2></div><small id="api-plan-count">0 个计划</small></div>
        <div id="api-execution-plans">${apiTestingEmpty('正在读取测试资产...')}</div>
      </section>
    </div>
  `;
  await refreshApiExecutionContext(true);
}

async function refreshApiExecutionContext(force = false) {
  if (apiExecutionRequestController) apiExecutionRequestController.abort();
  const controller = new AbortController();
  apiExecutionRequestController = controller;
  const requestId = ++apiExecutionContextRequestId;
  const sourceId = apiTestingProjectScope.sourceId || apiAssetSelectedSourceId;
  const capturedScopeKey = apiProjectScopeKey(sourceId, apiTestingProjectScope.revisionId);
  try {
    const query = new URLSearchParams();
    if (force) query.set('force', '1');
    if (sourceId) query.set('source_id', sourceId);
    const [data, sourceData] = await Promise.all([
      apiRequest(`/api-testing/execution-context${query.toString() ? `?${query}` : ''}`, { signal: controller.signal }),
      apiRequest('/api-testing/sources?limit=20', { signal: controller.signal }),
    ]);
    if (requestId !== apiExecutionContextRequestId || controller !== apiExecutionRequestController || activeWorkflow !== 'api_execution' || capturedScopeKey !== apiProjectScopeKey()) return;
    stopApiExecutionPolling(true);
    apiTestingSources = sourceData.sources || [];
    apiTestingSyncs = sourceData.syncs || [];
    apiExecutionContext = data;
    apiAssetSelectedSourceId = data.source_id || sourceId || apiAssetSelectedSourceId || '';
    apiTestingProjectScope = { sourceId: apiAssetSelectedSourceId, revisionId: apiTestingProjectScope.revisionId || '' };
    const effectiveScopeKey = apiProjectScopeKey();
    apiTestingPlans = data.plans || [];
    const active = (data.active_runs || [])[0] || null;
    apiExecutionActiveId = active?.execution_id || '';
    renderApiExecutionDynamic(data, active);
    if (active && !apiExecutionTerminal(active)) {
      scheduleApiExecutionPoll(active, apiExecutionPollRequestId, effectiveScopeKey);
    }
    else stopApiExecutionPolling();
  } catch (e) {
    if (controller !== apiExecutionRequestController || activeWorkflow !== 'api_execution' || capturedScopeKey !== apiProjectScopeKey()) return;
    const header = document.getElementById('api-execution-header');
    if (header) header.innerHTML = `<div class="api-inline-error">${escapeHtml(e.message || 'API 执行上下文读取失败')}</div>`;
  } finally {
    if (controller === apiExecutionRequestController) apiExecutionRequestController = null;
  }
}

function renderApiExecutionDynamic(context, activeRun) {
  const header = document.getElementById('api-execution-header');
  const plans = document.getElementById('api-execution-plans');
  const active = document.getElementById('api-active-run');
  const count = document.getElementById('api-plan-count');
  captureApiExecutionLogViewState(active);
  if (header) header.innerHTML = renderApiExecutionHeader(context);
  updateApiWorkflowStepper({execution: activeRun || {}, plans: context.plans || []});
  if (plans) plans.innerHTML = renderApiExecutionPlans(context.plans || [], context);
  if (count) count.textContent = `${(context.plans || []).length} 个计划`;
  if (active) {
    active.hidden = !activeRun;
    active.innerHTML = activeRun ? renderApiActiveRun(activeRun) : '';
  }
  restoreApiExecutionLogViewState(active);
  apiExecutionSettingsOpen = false;
}

function renderApiExecutionHeader(context) {
  const connection = context.connection || {};
  const readiness = context.readiness || {};
  const metadata = context.metadata || {};
  const selection = context.selection || {};
  const source = context.source || {};
  const sourceEnv = apiSourceEnvironmentSummary(source);
  const selectedEnvironments = (context.environments || []).filter(item => !selection.project_id || !item.project_id || String(item.project_id) === String(selection.project_id));
  const connectionClass = connection.state === 'connected' ? 'success' : (connection.state === 'disconnected' ? 'danger' : 'warn');
  const missing = readiness.missing || [];
  const missingBaseUrl = missing.includes('base_url');
  return `
    <div class="api-execution-status-row">
      <div class="api-connection-summary">
        ${apiStatusPill(apiConnectionText(connection.state), connectionClass)}
        <strong>${escapeHtml(apiReadinessText(readiness.state))}</strong>
        <span>检查于 ${escapeHtml(connection.checked_at || '-')} · ${escapeHtml(connection.latency_ms || 0)}ms</span>
      </div>
      <div class="api-icon-actions">
        <button class="btn-sm icon-only" title="刷新执行数据" aria-label="刷新执行数据" onclick="refreshApiExecutionContext(true)">↻</button>
      </div>
    </div>
    ${renderSavedApiSourceShelf(apiTestingSources, source.source_id || context.source_id || apiAssetSelectedSourceId, 'execution')}
    <div class="api-execution-env-card">
      <div class="api-execution-env-main">
        <div>
          <span>执行环境</span>
          <strong>${escapeHtml(apiBusinessAuthEnvironmentName(context, context.auth_binding || {}))}</strong>
          <small>${escapeHtml(connection.base_url || sourceEnv.baseUrl || (missingBaseUrl ? 'Apifox 环境未返回 base_url' : '-'))}</small>
        </div>
        <div class="api-execution-env-actions">
          <button class="btn-sm" onclick="showApiAssetsPage()">重新读取 Apifox 环境</button>
          <button class="btn-sm primary" onclick="refreshApiExecutionContext(true)">刷新状态</button>
        </div>
      </div>
      <div class="api-execution-env-facts">
        <div><span>业务</span><strong>${escapeHtml(apiBusinessAuthProjectName(context, context.auth_binding || {}))}</strong></div>
        <div><span>Base URL</span><strong>${escapeHtml(connection.base_url || sourceEnv.baseUrl || '缺失')}</strong></div>
        <div><span>变量</span><strong>${escapeHtml(sourceEnv.variableCount)} 个</strong><small>${escapeHtml(sourceEnv.sensitiveCount)} 个敏感</small></div>
        <div><span>执行状态</span><strong>${escapeHtml(readiness.primary_action || '-')}</strong></div>
      </div>
      ${missingBaseUrl ? `<div class="api-env-action-required"><strong>需要先拉取 Apifox 环境</strong><span>当前环境没有可执行的 base_url。请回到“接口资产”，选择 Apifox 环境并点击“重新读取 Apifox 资产”。</span></div>` : ''}
    </div>
    <details class="api-execution-selector-detail">
      <summary>切换业务或环境</summary>
      <div class="api-execution-selectors">
        <label><span>业务</span><select class="api-execution-project-select" onchange="changeApiExecutionProject(this.value)">${apiSelectOptions(context.businesses, selection.project_id, '选择业务')}</select></label>
        <label><span>环境</span><select class="api-execution-environment-select" onchange="changeApiExecutionEnvironment(this.value)" ${selection.project_id ? '' : 'disabled'}>${apiSelectOptions(selectedEnvironments, selection.environment_id, '选择环境')}</select></label>
        <div class="api-readiness-fact">
          <span>${metadata.stale ? '过期缓存，仅供查看' : '实时数据'}</span>
          <strong>${escapeHtml(readiness.primary_action || '-')}</strong>
        </div>
      </div>
    </details>
    ${missing.length && !missingBaseUrl ? `<div class="api-readiness-missing"><strong>还缺：</strong>${missing.map(item => `<span>${escapeHtml(item)}</span>`).join('')}</div>` : ''}
    ${metadata.stale ? `<div class="api-stale-warning">业务或环境来自过期缓存。完成一次实时校验前，执行按钮保持禁用。</div>` : ''}
    ${renderApiBusinessAuthPanel(context)}
  `;
}

function apiBusinessAuthMetadata(context = apiExecutionContext || {}) {
  return context.auth_binding || context.binding?.auth_binding || {};
}

function apiBusinessAuthExpectedState(context = apiExecutionContext || {}) {
  const binding = context.binding || {};
  const auth = apiBusinessAuthMetadata(context);
  return {
    expected_project_id: String(binding.project_id || context.selection?.project_id || ''),
    expected_environment_id: String(binding.environment_id || context.selection?.environment_id || ''),
    expected_binding_version: String(binding.binding_version || binding.config_fingerprint || ''),
    expected_profile_version: String(auth.profile_version || '')
  };
}

function apiBusinessAuthContextMatches(sourceId, expected) {
  const currentSourceId = apiExecutionContext?.source_id || apiTestingProjectScope.sourceId;
  if (String(currentSourceId || '') !== String(sourceId || '')) return false;
  const current = apiBusinessAuthExpectedState();
  return (
    current.expected_project_id === expected.expected_project_id
    && current.expected_environment_id === expected.expected_environment_id
    && current.expected_binding_version === expected.expected_binding_version
  );
}

function apiBusinessAuthEnvironmentName(context, auth) {
  const environment = (context.environments || []).find(item => String(item.id || '') === String(auth.environment_id || context.selection?.environment_id || ''));
  return environment?.name || context.binding?.environment_name || auth.environment_name || auth.environment_id || '未选择环境';
}

function apiBusinessAuthProjectName(context = {}, auth = {}) {
  const projectId = auth.project_id || context.binding?.project_id || context.selection?.project_id || '';
  const project = (context.businesses || []).find(item => String(item.id || '') === String(projectId || ''));
  return project?.name || context.binding?.project_name || auth.project_name || projectId || '未选择业务';
}

function renderApiBusinessAuthTarget(context = {}, auth = {}) {
  const projectName = apiBusinessAuthProjectName(context, auth);
  const environmentName = apiBusinessAuthEnvironmentName(context, auth);
  return `
    <div class="api-business-auth-target">
      <div><span>绑定业务</span><strong>${escapeHtml(projectName)}</strong><small>${escapeHtml(auth.project_id || context.binding?.project_id || context.selection?.project_id || '-')}</small></div>
      <div><span>绑定环境</span><strong>${escapeHtml(environmentName)}</strong><small>${escapeHtml(auth.environment_id || context.binding?.environment_id || context.selection?.environment_id || '-')}</small></div>
      <div><span>Token 标记</span><strong>${escapeHtml(auth.variable_name || '保存后生成环境变量')}</strong><small>${escapeHtml(auth.auth_ref || '按业务和环境自动匹配')}</small></div>
    </div>
  `;
}

function renderApiBusinessAuthPanel(context = {}) {
  const auth = apiBusinessAuthMetadata(context);
  const configured = auth.configured === true;
  const selectedEnvironmentId = context.selection?.environment_id || context.binding?.environment_id || '';
  const canEdit = !!context.source_id && !!selectedEnvironmentId;
  if (configured && !apiBusinessAuthEditing) {
    const usageCount = Math.max(1, Number(auth.usage_count || 1));
    const reused = auth.reused === true || usageCount > 1;
    return `
      <section class="api-business-auth-panel" data-configured="true">
        <div class="api-auth-summary">
          <div class="api-business-auth-head">
            <div><span>环境公共鉴权</span><h3>${escapeHtml(apiBusinessAuthEnvironmentName(context, auth))}</h3></div>
            ${apiStatusPill(`${auth.auth_type === 'api_key' ? 'API Key' : 'Bearer'} 已配置`, 'success')}
          </div>
          <p>${reused ? `该环境已复用此鉴权，当前覆盖 ${usageCount} 个业务来源。` : '该环境下的接口执行会自动复用，无需每次提交。'} token 保存为平台安全 profile，前端只显示变量名和指纹。</p>
        </div>
        ${renderApiBusinessAuthTarget(context, auth)}
        <details class="api-plan-tech-detail api-auth-detail">
          <summary>管理公共鉴权</summary>
          <div class="api-business-auth-facts">
            <div><span>环境</span><strong>${escapeHtml(apiBusinessAuthEnvironmentName(context, auth))}</strong><small>${escapeHtml(auth.environment_id || '-')}</small></div>
            <div><span>变量</span><strong>${escapeHtml(auth.variable_name || '-')}</strong><small>${escapeHtml(auth.auth_ref || '服务端引用')}</small></div>
            <div><span>请求头</span><strong>${escapeHtml(auth.header_name || (auth.auth_type === 'bearer' ? 'Authorization' : '-'))}</strong><small>${escapeHtml(auth.updated_at || auth.configured_at || '-')}</small></div>
          </div>
          <div class="api-business-auth-actions">
            <button class="btn-sm" aria-label="更换业务鉴权" onclick="editApiBusinessAuth()">更新鉴权</button>
            <button class="btn-sm danger ghost" onclick="clearApiBusinessAuth()">清除公共鉴权</button>
          </div>
        </details>
      </section>
    `;
  }
  if (!apiBusinessAuthEditing) {
    return `
      <section class="api-business-auth-panel" data-configured="false">
        <div class="api-business-auth-head"><div><span>环境公共鉴权</span><h3>当前环境尚未配置</h3></div>${apiStatusPill('执行前必需', 'warn')}</div>
        <p>优先通过业务系统的用户登录接口获取 token，再保存为平台安全 profile；前端只显示变量名和指纹。</p>
        ${renderApiBusinessAuthTarget(context, auth)}
        <button class="btn-sm primary" aria-label="配置业务鉴权" onclick="editApiBusinessAuth()">配置登录接口</button>
        ${canEdit ? '' : `<small class="api-business-auth-hint">请先选择当前来源的业务和环境。</small>`}
      </section>
    `;
  }
  return `
    <section class="api-business-auth-panel is-editing" data-configured="${configured ? 'true' : 'false'}">
      <div class="api-business-auth-head"><div><span>环境公共鉴权</span><h3>${configured ? '更新业务用户登录 token' : '配置业务用户登录 token'}</h3></div><small>${escapeHtml(apiBusinessAuthEnvironmentName(context, auth))}</small></div>
      <p>推荐从用户登录接口实时获取 token。登录账号密码只用于本次请求，返回 token 只保存到平台后端 profile。</p>
      ${renderApiBusinessAuthTarget(context, auth)}
      <div class="api-auth-segmented" role="group" aria-label="业务 token 获取方式">
        <button type="button" data-auth-source-mode="login" class="${apiBusinessAuthSourceMode === 'login' ? 'active' : ''}" onclick="setApiBusinessAuthSourceMode('login')">登录接口获取</button>
        <button type="button" data-auth-source-mode="manual" class="${apiBusinessAuthSourceMode === 'manual' ? 'active' : ''}" onclick="setApiBusinessAuthSourceMode('manual')">手动粘贴兜底</button>
      </div>
      <div class="api-auth-segmented" role="group" aria-label="业务鉴权类型">
        <button type="button" data-auth-type="bearer" class="${apiBusinessAuthType === 'bearer' ? 'active' : ''}" onclick="setApiBusinessAuthType('bearer')">Bearer</button>
        <button type="button" data-auth-type="api_key" class="${apiBusinessAuthType === 'api_key' ? 'active' : ''}" onclick="setApiBusinessAuthType('api_key')">API Key</button>
      </div>
      <div class="api-business-auth-form">
        ${apiBusinessAuthType === 'api_key' ? `<label><span>API Key Header</span><input id="api-business-auth-header" autocomplete="off" value="${escapeHtml(auth.auth_type === 'api_key' ? auth.header_name || '' : '')}" placeholder="X-API-Key"></label>` : ''}
        ${apiBusinessAuthSourceMode === 'login' ? `
          <label><span>用户登录接口</span><input id="api-business-auth-login-url" autocomplete="off" value="" placeholder="https://3d.example.com/api/user/login"></label>
          <label><span>Token JSON 路径</span><input id="api-business-auth-token-path" autocomplete="off" value="data.token" placeholder="data.token"></label>
          <label class="api-business-auth-body"><span>登录请求体 JSON</span><textarea id="api-business-auth-login-body" autocomplete="off" spellcheck="false" placeholder='{"username":"test","password":"******"}'></textarea></label>
        ` : `
          <label><span>业务 token</span><input id="api-business-auth-secret" type="password" autocomplete="new-password" value="" placeholder="输入后仅发送一次"></label>
        `}
      </div>
      <div class="api-business-auth-actions">
        <button class="btn-sm" aria-label="取消更换业务鉴权" onclick="cancelApiBusinessAuthEdit()">取消</button>
        <button class="btn-sm primary" onclick="saveApiBusinessAuth()">保存公共鉴权</button>
      </div>
    </section>
  `;
}

function renderApiBusinessAuthInHeader() {
  const header = document.getElementById('api-execution-header');
  if (header && apiExecutionContext) header.innerHTML = renderApiExecutionHeader(apiExecutionContext);
  const environmentPanel = document.getElementById('api-environment-auth-panel');
  if (environmentPanel && apiExecutionContext) environmentPanel.innerHTML = renderApiBusinessAuthPanel(apiExecutionContext);
}

function editApiBusinessAuth() {
  const sourceId = apiExecutionContext?.source_id || apiTestingProjectScope.sourceId;
  const selectedEnvironmentId = apiExecutionContext?.selection?.environment_id || apiExecutionContext?.binding?.environment_id || '';
  if (!sourceId || !selectedEnvironmentId) {
    showToast('请先选择当前来源的业务和环境', 'error');
    return;
  }
  const auth = apiBusinessAuthMetadata();
  apiBusinessAuthEditing = true;
  apiBusinessAuthType = auth.auth_type === 'api_key' ? 'api_key' : 'bearer';
  apiBusinessAuthSourceMode = 'login';
  renderApiBusinessAuthInHeader();
}

function cancelApiBusinessAuthEdit() {
  apiBusinessAuthEditing = false;
  renderApiBusinessAuthInHeader();
}

function setApiBusinessAuthType(authType) {
  apiBusinessAuthType = authType === 'api_key' ? 'api_key' : 'bearer';
  renderApiBusinessAuthInHeader();
}

function setApiBusinessAuthSourceMode(mode) {
  apiBusinessAuthSourceMode = mode === 'manual' ? 'manual' : 'login';
  renderApiBusinessAuthInHeader();
}

async function saveApiBusinessAuth() {
  const sourceId = apiExecutionContext?.source_id || apiTestingProjectScope.sourceId;
  const expected = apiBusinessAuthExpectedState();
  const secretInput = document.getElementById('api-business-auth-secret');
  const secret = secretInput?.value || '';
  const headerName = apiBusinessAuthType === 'api_key'
    ? document.getElementById('api-business-auth-header')?.value.trim() || ''
    : 'Authorization';
  const loginUrl = document.getElementById('api-business-auth-login-url')?.value.trim() || '';
  const tokenPath = document.getElementById('api-business-auth-token-path')?.value.trim() || 'data.token';
  const loginBodyText = document.getElementById('api-business-auth-login-body')?.value.trim() || '{}';
  if (!sourceId) {
    showToast('请先选择 API 来源', 'error');
    return;
  }
  if (apiBusinessAuthSourceMode === 'manual' && !secret) {
    showToast('请输入业务用户登录 token', 'error');
    return;
  }
  if (apiBusinessAuthSourceMode === 'login' && !loginUrl) {
    showToast('请填写用户登录接口 URL', 'error');
    return;
  }
  let loginBody = {};
  if (apiBusinessAuthSourceMode === 'login') {
    try {
      loginBody = JSON.parse(loginBodyText || '{}');
    } catch (_) {
      showToast('登录请求体必须是合法 JSON', 'error');
      return;
    }
  }
  try {
    const path = apiBusinessAuthSourceMode === 'login'
      ? `/api-testing/sources/${encodeURIComponent(sourceId)}/auth-binding/from-login`
      : `/api-testing/sources/${encodeURIComponent(sourceId)}/auth-binding`;
    const body = apiBusinessAuthSourceMode === 'login'
      ? {
        login_url: loginUrl,
        method: 'POST',
        body: loginBody,
        token_path: tokenPath,
        auth_type: apiBusinessAuthType,
        header_name: headerName,
        ...expected
      }
      : {
        auth_type: apiBusinessAuthType,
        header_name: headerName,
        secret,
        ...expected
      };
    const data = await apiRequest(path, {
      method: 'POST',
      body
    });
    if (secretInput) secretInput.value = '';
    const loginBodyInput = document.getElementById('api-business-auth-login-body');
    if (loginBodyInput) loginBodyInput.value = '';
    if (!apiBusinessAuthContextMatches(sourceId, expected)) {
      await refreshApiExecutionContext(true);
      return;
    }
    apiBusinessAuthEditing = false;
    const binding = data.binding || {};
    apiExecutionContext = {
      ...(apiExecutionContext || {}),
      auth_binding: binding,
      binding: {...(apiExecutionContext?.binding || {}), auth_binding: binding}
    };
    if (activeWorkflow === 'api_environment') {
      renderApiBusinessAuthInHeader();
      showToast(apiBusinessAuthSourceMode === 'login' ? '✓ 已通过登录接口获取 token 并保存到平台' : '✓ 公共鉴权已保存到平台', 'success');
      await showApiEnvironmentPage();
      return;
    }
    renderApiExecutionDynamic(apiExecutionContext, (apiExecutionContext.active_runs || [])[0] || null);
    showToast(apiBusinessAuthSourceMode === 'login' ? '✓ 已通过登录接口获取 token 并保存到平台' : '✓ 公共鉴权已保存到平台', 'success');
    await refreshApiExecutionContext(true);
  } catch (error) {
    if (secretInput) secretInput.value = '';
    showToast(error.message || '业务鉴权保存失败', 'error');
  }
}

async function clearApiBusinessAuth() {
  const sourceId = apiExecutionContext?.source_id || apiTestingProjectScope.sourceId;
  const expected = apiBusinessAuthExpectedState();
  const usageCount = Math.max(1, Number(apiBusinessAuthMetadata().usage_count || 1));
  if (!sourceId || !confirm(`确认清除当前 API 环境的公共鉴权？这会影响 ${usageCount} 个业务来源，相关计划将不可执行。`)) return;
  try {
    const data = await apiRequest(`/api-testing/sources/${encodeURIComponent(sourceId)}/auth-binding`, {
      method: 'DELETE',
      body: expected
    });
    if (!apiBusinessAuthContextMatches(sourceId, expected)) {
      await refreshApiExecutionContext(true);
      return;
    }
    const binding = data.binding || {configured: false};
    apiBusinessAuthEditing = false;
    apiExecutionContext = {
      ...(apiExecutionContext || {}),
      auth_binding: binding,
      binding: {...(apiExecutionContext?.binding || {}), auth_binding: binding}
    };
    if (activeWorkflow === 'api_environment') {
      renderApiBusinessAuthInHeader();
      showToast('✓ 当前业务鉴权已清除', 'success');
      await showApiEnvironmentPage();
      return;
    }
    renderApiExecutionDynamic(apiExecutionContext, (apiExecutionContext.active_runs || [])[0] || null);
    showToast('✓ 当前业务鉴权已清除', 'success');
    await refreshApiExecutionContext(true);
  } catch (error) {
    showToast(error.message || '业务鉴权清除失败', 'error');
  }
}

function apiExecutionEmptyAction(context) {
  const reason = context.empty_reason || '';
  if (reason === 'no_assets') return { text: '尚未导入接口', action: '去导入接口', handler: 'showApiAssetsPage()' };
  if (reason === 'no_plans') return { text: '尚未生成 API 测试资产', action: '去测试设计', handler: 'showApiPlanPage()' };
  if (reason === 'unconfirmed_plans') return { text: 'AI 草稿尚未保存为测试资产', action: '去审阅草稿', handler: 'showApiPlanPage()' };
  if (reason === 'no_executable_plans') return { text: '测试资产仍缺测试数据', action: '查看测试设计', handler: 'showApiPlanPage()' };
  return { text: 'API 执行环境尚未满足条件', action: '检查环境与 token', handler: 'refreshApiExecutionContext(true)' };
}

function renderApiExecutionPlans(plans, context = {}) {
  if (!(plans || []).length) {
    const empty = apiExecutionEmptyAction(context);
    return `<div class="api-execution-empty"><strong>${escapeHtml(empty.text)}</strong><button class="btn-sm primary" onclick="${empty.handler}">${escapeHtml(empty.action)}</button></div>`;
  }
  const readiness = context.readiness || {};
  const metadata = context.metadata || {};
  return `<div class="api-execution-plan-list">${plans.map(plan => {
    const latest = plan.latest_run || {};
    const planReadiness = plan.execution_readiness || {};
    const revision = plan.revision_state || {};
    const starting = String(apiExecutionStartingPlanId || '') === String(plan.plan_id || '');
    const disabled = starting || metadata.stale || revision.state === 'stale' || readiness.can_execute !== true || planReadiness.can_execute !== true || plan.can_execute !== true;
    const passRate = latest.stats?.total ? `${Math.round((latest.stats.passed || 0) * 100 / latest.stats.total)}%` : '-';
    const disabledReason = starting ? '正在创建执行' : (metadata.stale ? '元数据已过期' : (revision.state === 'stale' ? '接口版本已变化，请重新生成计划' : ((planReadiness.missing || readiness.missing || [])[0] || (plan.active_run ? '当前计划正在执行' : '暂不可执行'))));
    return `
      <article class="api-execution-plan-row" data-api-execution-plan-id="${escapeHtml(plan.plan_id || '')}">
        <div class="api-plan-identity">
          <strong>${escapeHtml(plan.name || plan.plan_id)}</strong>
          <span>${escapeHtml(plan.endpoint_count || 0)} 个接口 · 可执行 ${escapeHtml(plan.executable_case_count || 0)} / 待补 ${escapeHtml(plan.needs_review_case_count || 0)} · 保存于 ${escapeHtml(plan.confirmed_at || '-')}</span>
        </div>
        <div class="api-plan-binding"><span>执行器</span><strong>平台本地执行器</strong></div>
        <div class="api-plan-latest"><span>最近运行</span><strong>${escapeHtml(apiExecutionStateText(latest.status))} · 通过率 ${escapeHtml(passRate)}</strong><small>${escapeHtml(latest.started_at || latest.created_at || '暂无历史')} · 耗时 ${escapeHtml(apiDurationText(latest.duration_seconds))}</small></div>
        <div class="api-plan-actions">
          <button class="btn-sm primary" onclick="startApiExecution(${jsArg(plan.plan_id)})" ${disabled ? 'disabled' : ''} title="${escapeHtml(disabled ? disabledReason : '执行测试资产')}">执行测试</button>
          <details class="api-plan-menu"><summary title="更多操作" aria-label="更多操作">⋯</summary><div>
            <button onclick="startApiExecution(${jsArg(plan.plan_id)})" ${disabled ? 'disabled' : ''}>重新执行</button>
            <button onclick="showApiReportsPage()">查看历史</button>
          </div></details>
        </div>
        ${disabled ? `<div class="api-plan-disabled-reason">${escapeHtml(disabledReason)}</div>` : ''}
      </article>
    `;
  }).join('')}</div>`;
}

function apiExecutionTerminal(execution) {
  return ['succeeded', 'failed', 'cancelled'].includes(String(execution?.status || '').toLowerCase());
}

function renderApiActiveRun(execution) {
  const phases = execution.phases || [];
  const runMode = execution.run_mode === 'debug_case' ? '单条调试' : '测试资产回归';
  return `
    <div class="api-active-run-head">
      <div><span>${escapeHtml(runMode)}</span><h2>${escapeHtml(execution.plan_name || execution.plan_id || 'API 执行')}</h2></div>
      ${apiStatusPill(apiExecutionStateText(execution.status), execution.status === 'failed' ? 'danger' : (execution.status === 'succeeded' ? 'success' : 'warn'))}
    </div>
    <div class="api-run-meta"><span>execution_id <code>${escapeHtml(execution.execution_id || '-')}</code></span><span>run_id <code>${escapeHtml(execution.run_id || '等待触发')}</code></span><span>已运行 ${escapeHtml(apiDurationText(execution.duration_seconds))}</span><span>最后更新 ${escapeHtml(execution.updated_at || '-')}</span></div>
    <ol class="api-run-phases">${phases.map((phase, index) => `
      <li class="status-${escapeHtml(phase.state || 'waiting')}">
        <span class="api-phase-index">0${index + 1}</span><strong>${escapeHtml(phase.title || phase.id)}</strong><em>${escapeHtml(apiPhaseStateText(phase.state))}</em><small>${escapeHtml(phase.summary || phase.updated_at || '')}${phase.started_at ? ` · 耗时 ${escapeHtml(apiDurationText(phase.duration_seconds))}` : ''}</small>
      </li>
    `).join('')}</ol>
    ${execution.error ? `<div class="api-inline-error">${escapeHtml(execution.error)}</div>` : ''}
    ${renderApiExecutionLiveLogPanel(execution)}
  `;
}

function renderApiExecutionLiveLogPanel(execution = {}) {
  return `
    <section class="api-execution-live-log">
      <div class="api-tech-log-head">
        <div><span>实时执行日志</span><h3>接口请求过程</h3></div>
        <small>发送请求 · 收到响应 · 断言结果 · 生成报告</small>
      </div>
      ${renderApiExecutionLogRows(execution.events || [], execution.run_id || execution.execution_id, {embedded: true})}
    </section>
  `;
}

function apiExecutionLogKey(runId, eventId) {
  // Stable key uses source scope + runId + eventId so polling refresh keeps expanded logs open.
  return `${apiProjectScopeKey()}::execution::${runId || 'run'}::${eventId || 'event'}`;
}

function toggleApiExecutionLog(runId, eventId, open) {
  const key = apiExecutionLogKey(runId, eventId);
  if (open) apiLogExpandedKeys.add(key);
  else apiLogExpandedKeys.delete(key);
  localStorage.setItem('api_log_expanded_keys', JSON.stringify(Array.from(apiLogExpandedKeys)));
}

function rememberApiExecutionLogScroll(key, scrollTop) {
  apiLogScrollPositions.set(String(key || ''), Number(scrollTop || 0));
}

function captureApiExecutionLogViewState(root = document) {
  if (!root?.querySelectorAll) return;
  root.querySelectorAll('[data-api-log-key]').forEach(detail => {
    const key = detail.dataset.apiLogKey || '';
    if (detail.open) apiLogExpandedKeys.add(key);
    else apiLogExpandedKeys.delete(key);
    const body = detail.querySelector('.api-log-content');
    if (body) apiLogScrollPositions.set(key, body.scrollTop);
  });
}

function restoreApiExecutionLogViewState(root = document) {
  if (!root?.querySelectorAll) return;
  root.querySelectorAll('[data-api-log-key]').forEach(detail => {
    const key = detail.dataset.apiLogKey || '';
    detail.open = apiLogExpandedKeys.has(key);
    const body = detail.querySelector('.api-log-content');
    if (body) body.scrollTop = apiLogScrollPositions.get(key) || 0;
  });
}

function apiExecutionLogPhaseTitle(phaseId) {
  if (phaseId === 'prepare') return '准备环境';
  if (phaseId === 'execute') return '请求响应';
  if (phaseId === 'assert') return '断言结果';
  if (phaseId === 'report') return '生成报告';
  return phaseId || '执行事件';
}

function renderApiExecutionLogRows(rows, runId = '', options = {}) {
  const embedded = options.embedded === true;
  if (!(rows || []).length) return `<div class="api-tech-log ${embedded ? 'embedded' : ''}">${embedded ? '' : '<div class="api-tech-log-head"><h3>技术日志</h3></div>'}${apiTestingEmpty('暂无执行日志')}</div>`;
  return `<div class="api-tech-log ${embedded ? 'embedded' : ''}">${embedded ? '' : `<div class="api-tech-log-head"><h3>技术日志</h3><span>${rows.length} 条真实事件</span></div>`}${rows.map(row => {
    const eventId = row.event_id || '';
    const eventRunId = row.run_id || row.execution_id || runId;
    const key = apiExecutionLogKey(eventRunId, eventId);
    const open = apiLogExpandedKeys.has(key);
    const detail = row.detail == null ? row.summary : (typeof row.detail === 'string' ? row.detail : JSON.stringify(row.detail, null, 2));
    return `
      <details class="api-log-detail" data-api-log-key="${escapeHtml(key)}" ${open ? 'open' : ''} ontoggle="toggleApiExecutionLog(${jsArg(eventRunId)}, ${jsArg(eventId)}, this.open)">
        <summary><time>${escapeHtml(row.timestamp || '-')}</time><strong>${escapeHtml(row.summary || row.phase_id || '执行事件')}</strong><small>${escapeHtml(apiExecutionLogPhaseTitle(row.phase_id))}</small></summary>
        <div class="api-log-content" onscroll="rememberApiExecutionLogScroll(${jsArg(key)}, this.scrollTop)"><pre>${escapeHtml(detail || '无更多详情')}</pre></div>
      </details>
    `;
  }).join('')}</div>`;
}

function scheduleApiExecutionPoll(execution, requestId = apiExecutionPollRequestId, capturedScopeKey = apiProjectScopeKey()) {
  stopApiExecutionPolling();
  if (!execution?.execution_id || apiExecutionTerminal(execution) || activeWorkflow !== 'api_execution') return;
  const delay = Math.max(1000, Number(execution.poll_after_ms || 3000));
  apiExecutionPollTimer = setTimeout(() => pollApiExecution(execution.execution_id, requestId, capturedScopeKey), delay);
}

async function pollApiExecution(executionId, requestId = apiExecutionPollRequestId, capturedScopeKey = apiProjectScopeKey()) {
  if (activeWorkflow !== 'api_execution' || executionId !== apiExecutionActiveId || requestId !== apiExecutionPollRequestId || capturedScopeKey !== apiProjectScopeKey()) return;
  if (apiExecutionPollController) apiExecutionPollController.abort();
  const controller = new AbortController();
  apiExecutionPollController = controller;
  try {
    const data = await apiRequest(`/api-testing/executions/${encodeURIComponent(executionId)}`, {signal: controller.signal});
    if (controller !== apiExecutionPollController || executionId !== apiExecutionActiveId || requestId !== apiExecutionPollRequestId || capturedScopeKey !== apiProjectScopeKey() || activeWorkflow !== 'api_execution') return;
    const execution = data.execution || {};
    const active = document.getElementById('api-active-run');
    captureApiExecutionLogViewState(active);
    if (active) {
      active.hidden = false;
      active.innerHTML = renderApiActiveRun(execution);
    }
    restoreApiExecutionLogViewState(active);
    if (apiExecutionTerminal(execution)) await refreshApiExecutionContext(true);
    else scheduleApiExecutionPoll(execution, requestId, capturedScopeKey);
  } catch (e) {
    if (controller !== apiExecutionPollController || executionId !== apiExecutionActiveId || requestId !== apiExecutionPollRequestId || capturedScopeKey !== apiProjectScopeKey() || activeWorkflow !== 'api_execution') return;
    apiExecutionPollTimer = setTimeout(() => pollApiExecution(executionId, requestId, capturedScopeKey), 5000);
  } finally {
    if (controller === apiExecutionPollController) apiExecutionPollController = null;
  }
}

async function startApiExecution(planId) {
  if (apiExecutionStartingPlanId) {
    showToast('正在创建执行，请勿重复提交', 'warn');
    return;
  }
  apiExecutionStartingPlanId = String(planId || '');
  const planRoot = document.getElementById('api-execution-plans');
  if (planRoot && apiExecutionContext) {
    planRoot.innerHTML = renderApiExecutionPlans(apiExecutionContext.plans || [], apiExecutionContext);
  }
  try {
    const data = await apiRequest('/api-testing/executions', { method: 'POST', body: { plan_id: planId } });
    const execution = data.execution || {};
    apiExecutionStartingPlanId = '';
    stopApiExecutionPolling(true);
    apiExecutionActiveId = execution.execution_id || '';
    if (apiExecutionContext) {
      apiExecutionContext = {
        ...apiExecutionContext,
        readiness: {...(apiExecutionContext.readiness || {}), state: 'running', primary_action: '查看实时进度'},
        active_runs: [
          execution,
          ...(apiExecutionContext.active_runs || []).filter(item => item.execution_id !== execution.execution_id),
        ],
        plans: (apiExecutionContext.plans || []).map(plan => String(plan.plan_id || '') === String(planId || '')
          ? {...plan, can_execute: false, active_run: execution, latest_run: execution}
          : plan),
      };
      renderApiExecutionDynamic(apiExecutionContext, execution);
    } else {
      const active = document.getElementById('api-active-run');
      if (active) {
        active.hidden = false;
        active.innerHTML = renderApiActiveRun(execution);
      }
    }
    showToast('✓ API 执行已排队', 'success');
    scheduleApiExecutionPoll(execution, apiExecutionPollRequestId, apiProjectScopeKey());
  } catch (e) {
    apiExecutionStartingPlanId = '';
    showToast(e.message || 'API 执行启动失败', 'error');
    await refreshApiExecutionContext(true);
  }
}

async function loadApiExecutionProjectEnvironments(projectId, intent = null) {
  const sourceId = currentApiExecutionSourceId();
  if (!sourceId || !projectId) return [];
  const bindingIntent = intent || beginApiExecutionBindingIntent(projectId);
  if (!apiExecutionBindingIntentIsCurrent(bindingIntent) || bindingIntent.projectId !== String(projectId)) return null;
  apiExecutionBindingLookupController?.abort();
  const controller = new AbortController();
  const requestId = ++apiExecutionBindingLookupRequestId;
  apiExecutionBindingLookupController = controller;
  try {
    const data = await apiRequest(
      `/api-testing/sources/${encodeURIComponent(sourceId)}/execution-binding?project_id=${encodeURIComponent(projectId)}&force=true`,
      {signal: controller.signal}
    );
    if (
      controller !== apiExecutionBindingLookupController
      || requestId !== apiExecutionBindingLookupRequestId
      || !apiExecutionBindingIntentIsCurrent(bindingIntent)
      || bindingIntent.projectId !== String(projectId)
    ) return null;
    const environments = (data.environments || []).filter(
      item => String(item.project_id || projectId) === String(projectId) && item.enabled !== false
    );
    apiExecutionContext = {
      ...(apiExecutionContext || {}),
      businesses: data.projects || apiExecutionContext?.businesses || [],
      environments,
      selection: {project_id: projectId, environment_id: ''},
    };
    return environments;
  } finally {
    if (controller === apiExecutionBindingLookupController) apiExecutionBindingLookupController = null;
  }
}

async function changeApiExecutionProject(projectId) {
  if (!projectId) {
    showToast('请选择业务', 'error');
    renderApiBusinessAuthInHeader();
    return;
  }
  const intent = beginApiExecutionBindingIntent(projectId);
  try {
    const environments = await loadApiExecutionProjectEnvironments(projectId, intent);
    if (!environments || !apiExecutionBindingIntentIsCurrent(intent)) return;
    const environmentId = (environments[0] || {}).id || '';
    if (!environmentId) {
      showToast('当前业务没有可用环境', 'error');
      renderApiBusinessAuthInHeader();
      return;
    }
    intent.environmentId = String(environmentId);
    await saveApiSourceExecutionBinding(projectId, environmentId, intent);
  } catch (error) {
    if (!apiExecutionBindingIntentIsCurrent(intent)) return;
    showToast(error.message || '业务环境读取失败', 'error');
    renderApiBusinessAuthInHeader();
  }
}

async function changeApiExecutionEnvironment(environmentId) {
  const projectId = document.querySelector('.api-execution-project-select')?.value || apiExecutionContext?.selection?.project_id || '';
  const intent = beginApiExecutionBindingIntent(projectId, environmentId);
  await saveApiSourceExecutionBinding(projectId, environmentId, intent);
}

async function updateApiExecutionSelection(selection) {
  const current = apiExecutionContext?.selection || {};
  const projectId = selection.project_id || current.project_id || '';
  const environmentId = selection.environment_id || current.environment_id || '';
  const intent = beginApiExecutionBindingIntent(projectId, environmentId);
  return saveApiSourceExecutionBinding(
    projectId,
    environmentId,
    intent
  );
}

async function reloadApiExecutionBindingAfterConflict(intent, controller, requestId) {
  const sourceId = intent.sourceId;
  const [bindingData, projectData] = await Promise.all([
    apiRequest(`/api-testing/sources/${encodeURIComponent(sourceId)}/execution-binding`, {signal: controller.signal}),
    apiRequest(
      `/api-testing/sources/${encodeURIComponent(sourceId)}/execution-binding?project_id=${encodeURIComponent(intent.projectId)}&force=true`,
      {signal: controller.signal}
    ),
  ]);
  if (!apiExecutionBindingResponseIsCurrent(controller, requestId, intent)) return;
  const binding = bindingData.binding || {};
  const environments = (projectData.environments || []).filter(
    item => String(item.project_id || intent.projectId) === intent.projectId && item.enabled !== false
  );
  apiExecutionContext = {
    ...(apiExecutionContext || {}),
    binding,
    auth_binding: binding.auth_binding || apiExecutionContext?.auth_binding || {},
    businesses: projectData.projects || apiExecutionContext?.businesses || [],
    environments,
    selection: {project_id: intent.projectId, environment_id: intent.environmentId},
  };
  renderApiExecutionDynamic(apiExecutionContext, (apiExecutionContext.active_runs || [])[0] || null);
}

async function saveApiSourceExecutionBinding(projectId, environmentId, intent = null) {
  const sourceId = currentApiExecutionSourceId();
  if (!sourceId || !projectId || !environmentId) {
    showToast('请选择当前来源的业务和环境', 'error');
    return;
  }
  const bindingIntent = intent || beginApiExecutionBindingIntent(projectId, environmentId);
  bindingIntent.environmentId = String(environmentId);
  if (
    !apiExecutionBindingIntentIsCurrent(bindingIntent)
    || bindingIntent.sourceId !== sourceId
    || bindingIntent.projectId !== String(projectId)
  ) return;
  apiExecutionBindingSaveController?.abort();
  const controller = new AbortController();
  const requestId = ++apiExecutionBindingSaveRequestId;
  apiExecutionBindingSaveController = controller;
  const expectedBindingFingerprint = apiExecutionContext?.binding?.binding_version
    || apiExecutionContext?.binding?.config_fingerprint
    || apiExecutionContext?.binding?.binding_fingerprint
    || apiExecutionContext?.binding?.version
    || '';
  try {
    const data = await apiRequest(`/api-testing/sources/${encodeURIComponent(sourceId)}/execution-binding`, {
      method: 'POST',
      signal: controller.signal,
      body: {
        project_id: projectId,
        environment_id: environmentId,
        expected_binding_fingerprint: expectedBindingFingerprint,
        client_session_id: apiExecutionBindingClientSessionId,
        client_intent_id: bindingIntent.intentId,
      }
    });
    if (!apiExecutionBindingResponseIsCurrent(controller, requestId, bindingIntent)) return;
    const binding = data.binding || {};
    apiBusinessAuthEditing = false;
    apiExecutionContext = {
      ...(apiExecutionContext || {}),
      binding,
      auth_binding: binding.auth_binding || {},
      selection: {project_id: binding.project_id || projectId, environment_id: binding.environment_id || environmentId}
    };
    renderApiExecutionDynamic(apiExecutionContext, (apiExecutionContext.active_runs || [])[0] || null);
    showToast('✓ 当前来源的执行业务与环境已保存', 'success');
  } catch (error) {
    if (!apiExecutionBindingResponseIsCurrent(controller, requestId, bindingIntent)) return;
    try {
      await reloadApiExecutionBindingAfterConflict(bindingIntent, controller, requestId);
    } catch (reloadError) {
      if (!apiExecutionBindingResponseIsCurrent(controller, requestId, bindingIntent)) return;
      renderApiBusinessAuthInHeader();
    }
    if (apiExecutionBindingResponseIsCurrent(controller, requestId, bindingIntent)) {
      showToast(error.message || '业务或环境保存冲突，已重新读取当前绑定', 'error');
    }
  } finally {
    if (controller === apiExecutionBindingSaveController) apiExecutionBindingSaveController = null;
  }
}

async function showApiReportsPage() {
  const area = setApiTestingPage('api_reports', 'API 报告', '查看平台 API 执行结果和接口失败归因。');
  if (!area) return;
  apiReportRequestController?.abort();
  if (apiReportPollTimer) clearTimeout(apiReportPollTimer);
  apiReportPollTimer = null;
  const controller = new AbortController();
  const requestId = ++apiReportRequestId;
  apiReportRequestController = controller;
  const sourceId = currentApiExecutionSourceId();
  const scopeKey = apiProjectScopeKey();
  area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty('正在读取 API 报告...')}</div>`;
  if (!sourceId) {
    area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty('请先选择 API 项目，再查看对应报告。')}</div>`;
    if (controller === apiReportRequestController) apiReportRequestController = null;
    return;
  }
  try {
    const query = new URLSearchParams();
    query.set('source_id', sourceId);
    const businessLine = String(apiModuleSelectionState().businessLine || '').trim();
    if (businessLine) query.set('business_line', businessLine);
    const data = await apiRequest(`/api-testing/reports?${query.toString()}`, {signal: controller.signal});
    if (!apiReportResponseIsCurrent(controller, requestId, sourceId, scopeKey)) return;
    apiTestingReportContext = { reports: data.reports || [], active_runs: data.active_runs || [], recent_runs: data.recent_runs || [] };
    apiTestingReports = apiTestingReportContext.reports;
    if (apiSelectedReportId && !apiTestingReports.some(row => row.report_id === apiSelectedReportId)) apiSelectedReportId = '';
    if (!apiSelectedReportId && apiTestingReports[0]?.report_id) apiSelectedReportId = apiTestingReports[0].report_id;
    apiSelectedReportDetail = apiSelectedReportId ? await loadApiReportDetail(apiSelectedReportId, sourceId) : null;
    if (!apiReportResponseIsCurrent(controller, requestId, sourceId, scopeKey)) return;
    area.innerHTML = `
      <div class="api-testing-page">
        <div id="api-workflow-stepper">${renderApiWorkflowStepper({workflow: 'api_reports', reports: apiTestingReports, execution: (apiTestingReportContext.active_runs || [])[0] || {}})}</div>
        <div class="generation-record-head">
          <div class="workflow-kicker">REPORT · API</div>
          <h2>API 报告</h2>
          <p>${escapeHtml(businessLine || '当前业务')}的平台 API 执行结果和失败归因。</p>
          <div class="generation-record-actions">
            <button class="btn-sm" onclick="showApiReportsPage()">刷新报告</button>
            <button class="btn-sm" onclick="showApiExecutionPage()">执行测试</button>
          </div>
        </div>
        ${renderApiReportActiveRuns(apiTestingReportContext.active_runs || [])}
        ${renderApiReportRecentRuns(apiTestingReportContext.recent_runs || [])}
        <section class="api-panel">
          <div class="api-section-heading"><div><span>历史报告</span><h3>已完成执行</h3></div><small>${escapeHtml(apiTestingReports.length)} 份报告</small></div>
          ${apiTestingReports.length ? `<table class="report-table api-report-history-table"><thead><tr><th>报告</th><th>状态</th><th>总数</th><th>通过</th><th>失败</th><th>时间</th></tr></thead><tbody>${apiTestingReports.map(renderApiReportRow).join('')}</tbody></table>` : apiTestingEmpty((apiTestingReportContext.active_runs || []).length ? '当前执行尚未生成最终报告。' : '暂无 API 报告。')}
        </section>
        ${renderApiReportDetail(apiSelectedReportDetail)}
      </div>
    `;
    scheduleApiReportPoll(apiTestingReportContext.active_runs || []);
  } catch(e) {
    if (!apiReportResponseIsCurrent(controller, requestId, sourceId, scopeKey)) return;
    area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty(e.message || 'API 报告读取失败')}</div>`;
  } finally {
    if (controller === apiReportRequestController) apiReportRequestController = null;
  }
}

async function loadApiReportDetail(reportId, sourceId = currentApiExecutionSourceId()) {
  const selectedReportId = String(reportId || '').trim();
  if (!selectedReportId) return null;
  const query = new URLSearchParams();
  if (sourceId) query.set('source_id', sourceId);
  try {
    const data = await apiRequest(`/api-testing/reports/${encodeURIComponent(selectedReportId)}?${query.toString()}`);
    return data.report || null;
  } catch (e) {
    return { report_id: selectedReportId, load_error: e.message || '报告详情读取失败' };
  }
}

async function selectApiReport(reportId) {
  apiSelectedReportId = String(reportId || '').trim();
  await showApiReportsPage();
}

function apiReportStatusTone(status) {
  const normalized = String(status || '').toLowerCase();
  if (['passed', 'succeeded', 'success'].includes(normalized)) return 'success';
  if (['failed', 'failure', 'error', 'cancelled', 'canceled'].includes(normalized)) return 'danger';
  return 'warn';
}

function renderApiReportRow(row = {}) {
  const reportId = row.report_id || row.run_id || '';
  const selectedClass = reportId && reportId === apiSelectedReportId ? 'is-selected' : '';
  return `
    <tr class="${escapeHtml(selectedClass)}" onclick="selectApiReport('${apiJsString(reportId)}')">
      <td>${escapeHtml(row.report_id || row.run_id || '-')}</td>
      <td>${apiStatusPill(apiExecutionStateText(row.status), apiReportStatusTone(row.status))}</td>
      <td>${escapeHtml(row.total || 0)}</td>
      <td>${escapeHtml(row.passed || 0)}</td>
      <td>${escapeHtml(row.failed || 0)}</td>
      <td>${escapeHtml(row.created_at || '-')}</td>
    </tr>
  `;
}

function apiReportSummaryValue(summary = {}, key, fallback = 0) {
  return summary && summary[key] !== undefined && summary[key] !== null ? summary[key] : fallback;
}

function apiReportNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function apiReportPassRate(summary = {}, results = []) {
  if (summary.pass_rate !== undefined && summary.pass_rate !== null && summary.pass_rate !== '') {
    return apiReportNumber(summary.pass_rate, 0);
  }
  const total = apiReportNumber(summary.total, results.length);
  const passed = apiReportNumber(summary.passed, results.filter(item => item.status === 'passed').length);
  return total > 0 ? Math.round((passed / total) * 1000) / 10 : 0;
}

function apiReportHasValue(value) {
  if (value === undefined || value === null || value === '') return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  return true;
}

function apiReportHasRichDetails(report = {}) {
  const environment = report.environment || {};
  const results = Array.isArray(report.results) ? report.results : [];
  return (
    apiReportHasValue(environment.base_url)
    || apiReportHasValue(environment.auth_variable)
    || results.some(item => (
      apiReportHasValue(item.request)
      || apiReportHasValue(item.response)
      || apiReportHasValue(item.assertions)
      || apiReportHasValue(item.analysis)
    ))
  );
}

function apiReportStatusText(status) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'passed' || normalized === 'success') return '通过';
  if (normalized === 'failed' || normalized === 'failure' || normalized === 'error') return '失败';
  if (normalized === 'skipped' || normalized === 'skip') return '跳过';
  if (normalized === 'running') return '执行中';
  if (normalized === 'pending' || normalized === 'queued') return '等待中';
  return status || '未知';
}

function renderApiReportDetail(report) {
  if (!report) return '';
  if (report.load_error) {
    return `<section class="api-panel api-report-detail-panel">${apiTestingEmpty(report.load_error)}</section>`;
  }
  const summary = report.summary || {};
  const environment = report.environment || {};
  const results = Array.isArray(report.results) ? report.results : [];
  const failedResults = results.filter(item => item.status !== 'passed');
  const totalCount = apiReportSummaryValue(summary, 'total', results.length);
  const passedCount = apiReportSummaryValue(summary, 'passed', results.filter(item => item.status === 'passed').length);
  const failedCount = apiReportSummaryValue(summary, 'failed', failedResults.length);
  const skippedCount = apiReportSummaryValue(summary, 'skipped', results.filter(item => item.status === 'skipped').length);
  const passRate = apiReportPassRate(summary, results);
  return `
    <section class="api-panel api-report-detail-panel">
      <div class="api-section-heading">
        <div><span>API 报告</span><h3>${escapeHtml(report.report_id || '接口测试报告')}</h3></div>
        <small>${escapeHtml(report.created_at || '-')}</small>
      </div>
      <div class="api-report-summary-cards">
        ${renderApiReportMetric('总用例', totalCount, '')}
        ${renderApiReportMetric('通过', passedCount, 'success')}
        ${renderApiReportMetric('失败', failedCount, 'danger')}
        ${renderApiReportMetric('跳过', skippedCount, 'warn')}
        ${renderApiReportMetric('通过率', `${passRate}%`, '')}
      </div>
      <div class="api-report-environment-grid">
        ${renderApiReportEnvItem('服务地址', environment.base_url || '-')}
        ${renderApiReportEnvItem('业务', environment.project_name || environment.project_id || '-')}
        ${renderApiReportEnvItem('环境', environment.environment_name || environment.environment_id || '-')}
        ${renderApiReportEnvItem('鉴权', environment.auth_variable || '未配置')}
        ${renderApiReportEnvItem('耗时', apiDurationText(summary.duration_seconds || 0))}
      </div>
      ${apiReportHasRichDetails(report) ? '' : renderApiReportLegacyNotice(report, results)}
      ${failedResults.length ? renderApiReportFailureAnalysis(report.failure_analysis || {}, failedResults) : ''}
      <div class="api-section-heading compact"><div><span>测试明细</span><h3>请求、响应和断言</h3></div><small>${escapeHtml(results.length)} 条</small></div>
      <div class="api-report-case-list">${results.map(renderApiReportCase).join('') || apiTestingEmpty('报告中暂无用例明细')}</div>
    </section>
  `;
}

function renderApiReportMetric(label, value, tone = '') {
  return `
    <div class="api-report-metric ${escapeHtml(tone)}">
      <strong>${escapeHtml(value)}</strong>
      <span>${escapeHtml(label)}</span>
    </div>
  `;
}

function renderApiReportEnvItem(label, value) {
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value || '-')}</strong></div>`;
}

function renderApiReportLegacyNotice(report = {}, results = []) {
  const remote = report.remote || report.remote_execution || {};
  const remoteStatus = remote.exec_status || remote.status || report.remote_status || '';
  const reason = report.failure_reason || remote.failure_reason || '';
  return `
    <div class="api-report-legacy-notice">
      <strong>历史报告数据不足</strong>
      <p>这份报告只保存了摘要和 ${escapeHtml(results.length)} 条用例状态，未记录请求、响应、断言和环境详情。新执行会按下方结构保存完整明细。</p>
      ${remoteStatus || reason ? `<small>原始远端状态：${escapeHtml(remoteStatus || '未知')}${reason ? ` · ${escapeHtml(reason)}` : ''}</small>` : ''}
    </div>
  `;
}

function renderApiReportFailureAnalysis(analysis = {}, failedResults = []) {
  const byType = analysis.by_type || {};
  const groups = Object.keys(byType).length
    ? Object.entries(byType).map(([type, count]) => ({type, count, items: failedResults.filter(item => String(item.analysis?.failure_type || item.failure_type || '未分类') === String(type))}))
    : [{type: '未分类失败', count: failedResults.length, items: failedResults}];
  return `
    <div class="api-report-failure-box api-report-failure-groups">
      <div><span>失败分析</span><strong>${escapeHtml(failedResults.length)} 条失败，按原因聚合</strong></div>
      <div class="api-report-failure-group-list">
        ${groups.map(group => `
          <article class="api-report-failure-group">
            <div><strong>${escapeHtml(group.type)}</strong><span>${escapeHtml(group.count)} 条</span></div>
            <ul>${(group.items.length ? group.items : failedResults).slice(0, 4).map(item => {
              const failure = item.analysis || {};
              return `<li><strong>${escapeHtml(item.name || item.case_id || '-')}</strong><span>${escapeHtml(failure.summary || item.error || failure.failure_type || '接口校验失败')}</span></li>`;
            }).join('')}</ul>
          </article>
        `).join('')}
      </div>
    </div>
  `;
}

function renderApiReportCase(item = {}) {
  const request = item.request || {};
  const response = item.response || {};
  const analysis = item.analysis || {};
  const statusTone = apiReportStatusTone(item.status);
  const openAttr = item.status === 'passed' ? '' : ' open';
  const assertionFailures = (item.assertions || []).filter(row => row && row.passed === false);
  const suggestions = Array.isArray(analysis.suggestions) ? analysis.suggestions : [];
  const evidence = Array.isArray(analysis.evidence) ? analysis.evidence : [];
  const assertions = Array.isArray(item.assertions) ? item.assertions : [];
  return `
    <details class="api-report-case api-report-case-detail ${escapeHtml(statusTone)}"${openAttr}>
      <summary>
        <div class="api-report-case-main">
          <div><span>用例编号 ${escapeHtml(item.case_id || '-')}</span><strong>${escapeHtml(item.name || '-')}</strong><small>${escapeHtml(item.endpoint || request.path || '')}</small></div>
          <div>${apiStatusPill(apiReportStatusText(item.status), statusTone)}<small>${escapeHtml(item.duration_ms || 0)} ms</small></div>
          <div><span>接口请求</span><strong>${escapeHtml(request.method || item.method || '-')} ${escapeHtml(apiReportShortUrl(request.url || request.path || item.endpoint || ''))}</strong></div>
          <div><span>响应状态</span><strong>${escapeHtml(response.status_code || response.status || item.http_status || '-')}</strong></div>
        </div>
      </summary>
      <div class="api-report-case-detail-body">
        ${renderApiReportDetailBlock('接口请求', apiReportRequestText(request, item), '历史报告未记录请求详情')}
        ${renderApiReportDetailBlock('响应结果', apiReportResponseText(response, item), '历史报告未记录响应详情')}
        ${renderApiReportDetailBlock('断言校验', assertions.length ? assertions.map(row => `${row.passed === false ? '失败' : '通过'} · ${row.message || row.type || row.path || '断言'}`).join('\n') : '', '历史报告未记录断言详情')}
        ${item.status === 'passed' ? '' : `
          <div class="api-report-case-analysis">
            <div><span>失败类型</span><strong>${escapeHtml(analysis.failure_type || item.failure_type || '未分类')}</strong></div>
            <div><span>证据</span>${evidence.length ? evidence.map(value => `<code>${escapeHtml(value)}</code>`).join('') : `<code>${escapeHtml(item.error || analysis.summary || '无错误文本')}</code>`}</div>
            <div><span>断言失败</span>${assertionFailures.length ? assertionFailures.map(row => `<code>${escapeHtml(row.message || row.type || '断言失败')}</code>`).join('') : '<code>未记录失败断言</code>'}</div>
            <div><span>处理建议</span>${suggestions.map(value => `<p>${escapeHtml(value)}</p>`).join('') || '<p>检查接口响应、鉴权变量和测试数据。</p>'}</div>
          </div>
        `}
      </div>
    </details>
  `;
}

function renderApiReportDetailBlock(title, text, emptyText) {
  const value = String(text || '').trim();
  return `
    <div class="api-report-detail-block">
      <span>${escapeHtml(title)}</span>
      <pre>${escapeHtml(value || emptyText)}</pre>
    </div>
  `;
}

function apiReportRequestText(request = {}, item = {}) {
  if (!apiReportHasValue(request)) return '';
  const lines = [];
  const method = request.method || item.method || '';
  const url = request.url || request.path || item.endpoint || '';
  if (method || url) lines.push(`${method || '-'} ${url || '-'}`);
  if (apiReportHasValue(request.headers)) lines.push(`请求头\n${apiReportPrettyJson(request.headers)}`);
  if (apiReportHasValue(request.query)) lines.push(`查询参数\n${apiReportPrettyJson(request.query)}`);
  if (apiReportHasValue(request.body)) lines.push(`请求体\n${apiReportPrettyJson(request.body)}`);
  return lines.join('\n\n');
}

function apiReportResponseText(response = {}, item = {}) {
  if (!apiReportHasValue(response) && !item.error) return '';
  const lines = [];
  const status = response.status_code || response.status || item.http_status || '';
  if (status) lines.push(`HTTP ${status}`);
  if (apiReportHasValue(response.headers)) lines.push(`响应头\n${apiReportPrettyJson(response.headers)}`);
  if (apiReportHasValue(response.body)) lines.push(`响应体\n${apiReportPrettyJson(response.body)}`);
  if (item.error) lines.push(`错误信息\n${item.error}`);
  return lines.join('\n\n');
}

function apiReportPrettyJson(value) {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch (e) {
    return String(value);
  }
}

function apiReportShortUrl(url) {
  try {
    const parsed = new URL(String(url || ''));
    return `${parsed.pathname}${parsed.search}`;
  } catch (e) {
    return String(url || '');
  }
}

function renderApiReportActiveRuns(activeRuns = []) {
  if (!activeRuns.length) return '';
  return `
    <section class="api-panel api-report-active-runs">
      <div class="api-section-heading">
        <div><span>实时执行</span><h3>本地执行器正在执行</h3></div>
        <button class="btn-sm primary" onclick="showApiExecutionPage()">查看执行日志</button>
      </div>
      <div class="api-report-run-list">${activeRuns.map(run => {
        const stats = run.stats || {};
        return `
          <article class="api-report-run-card">
            <div><span>计划</span><strong>${escapeHtml(run.plan_name || run.plan_id || run.execution_id || 'API 执行')}</strong><small>execution_id ${escapeHtml(run.execution_id || '-')}</small></div>
            <div><span>状态</span><strong>${escapeHtml(apiExecutionStateText(run.status))}</strong><small>${escapeHtml(run.current_phase || '-')} · 已运行 ${escapeHtml(apiDurationText(run.duration_seconds))}</small></div>
            <div><span>执行统计</span><strong>${escapeHtml(stats.total || 0)} / ${escapeHtml(stats.passed || 0)} / ${escapeHtml(stats.failed || 0)}</strong><small>总数 / 通过 / 失败</small></div>
            <div><span>报告</span><strong>${escapeHtml(run.report_status || '等待生成')}</strong><small>最后更新 ${escapeHtml(run.updated_at || '-')}</small></div>
          </article>
        `;
      }).join('')}</div>
    </section>
  `;
}

function renderApiReportRecentRuns(recentRuns = []) {
  if (!recentRuns.length) return '';
  return `
    <section class="api-panel api-report-recent-runs">
      <div class="api-section-heading">
        <div><span>最近执行记录</span><h3>编排状态与接口报告分开看</h3></div>
        <small>${escapeHtml(recentRuns.length)} 条</small>
      </div>
      <div class="api-report-run-list">${recentRuns.slice(0, 8).map(run => {
        const stats = run.stats || {};
        const reportTone = apiReportStatusTone(run.report_status);
        const runTone = apiReportStatusTone(run.status);
        const reportText = run.report_id
          ? `报告 ${run.report_id}`
          : (run.report_status || '未生成报告');
        return `
          <article class="api-report-run-card terminal">
            <div><span>计划</span><strong>${escapeHtml(run.plan_name || run.plan_id || run.execution_id || 'API 执行')}</strong><small>execution_id ${escapeHtml(run.execution_id || '-')}</small></div>
            <div><span>执行编排</span><strong>${apiStatusPill(apiExecutionStateText(run.status), runTone)}</strong><small>${escapeHtml(run.error || '无错误摘要')}</small></div>
            <div><span>接口结果</span><strong>${escapeHtml(stats.total || 0)} / ${escapeHtml(stats.passed || 0)} / ${escapeHtml(stats.failed || 0)}</strong><small>总数 / 通过 / 失败</small></div>
            <div><span>报告同步</span><strong>${apiStatusPill(apiExecutionStateText(run.report_status), reportTone)}</strong><small>${escapeHtml(reportText)} · ${escapeHtml(run.updated_at || '-')}</small></div>
          </article>
        `;
      }).join('')}</div>
    </section>
  `;
}

function scheduleApiReportPoll(activeRuns = []) {
  if (apiReportPollTimer) clearTimeout(apiReportPollTimer);
  apiReportPollTimer = null;
  if (!activeRuns.length || activeWorkflow !== 'api_reports') return;
  apiReportPollTimer = setTimeout(() => showApiReportsPage(), 5000);
}
