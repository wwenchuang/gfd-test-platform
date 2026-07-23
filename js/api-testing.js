// API testing workspace: OpenAPI assets -> AI plan drafts -> MeterSphere execution -> reports.

let apiPlanPageRequestId = 0;
let apiPlanGenerationRequestId = 0;
let apiPlanGenerationController = null;
let apiPlanGenerationPollTimer = null;
let apiPlanGenerationCurrent = null;
let apiPlanBindingContext = null;
const apiPlanGenerationExpandedKeys = new Set(JSON.parse(localStorage.getItem('api_plan_generation_expanded_keys') || '[]'));
const apiPlanGenerationScrollPositions = new Map();
let apiBusinessAuthEditing = false;
let apiBusinessAuthType = 'bearer';
let apiExecutionPollRequestId = 0;
let apiExecutionPollController = null;
let apiExecutionBindingLookupRequestId = 0;
let apiExecutionBindingLookupController = null;
let apiExecutionBindingSaveRequestId = 0;
let apiExecutionBindingSaveController = null;
let apiExecutionBindingIntentId = 0;
let apiExecutionBindingIntent = null;
let apiReportRequestId = 0;
let apiReportRequestController = null;
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
  if (workflow !== 'api_plan') stopApiPlanGenerationPolling(true);
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

function apiTestingEmpty(text) {
  return `<div class="report-empty">${escapeHtml(text)}</div>`;
}

function apiEndpointLabel(endpoint) {
  return `${endpoint.method || ''} ${endpoint.path || ''}`.trim();
}

function apiSelectedEndpointIds() {
  return Array.from(document.querySelectorAll('.api-endpoint-check:checked')).map(input => input.value);
}

function apiPlanStatusText(status) {
  const map = { draft: '草稿', confirmed: '已确认', pushed: '已推送' };
  return map[status] || status || '草稿';
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
  if (plan?.status !== 'confirmed') return '确认计划后可进入执行';
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

async function showApiTestingDashboard() {
  const area = setApiTestingPage('api_dashboard', 'API 工作台', 'OpenAPI 导入、AI 用例计划、MeterSphere 执行和 API 报告闭环。');
  if (!area) return;
  area.innerHTML = `<div class="generation-records">${apiTestingEmpty('正在读取 API 测试状态...')}</div>`;
  try {
    const data = await loadApiTestingOverview();
    const summary = data.summary || {};
    const ms = data.metersphere || {};
    area.innerHTML = `
      <div class="api-testing-page">
        <div class="generation-record-head">
          <div class="workflow-kicker">API TESTING · OpenAPI / MeterSphere</div>
          <h2>API 工作台</h2>
          <p>从 Apifox 只读同步 OpenAPI 版本和差异，确认用例计划后再进入 MeterSphere。</p>
          <div class="generation-record-actions">
            <button class="btn-sm primary" onclick="showApiAssetsPage()">接口资产</button>
            <button class="btn-sm ai" onclick="showApiPlanPage()">AI 用例计划</button>
            <button class="btn-sm" onclick="showApiExecutionPage()">MeterSphere 执行</button>
            <button class="btn-sm" onclick="showApiReportsPage()">API 报告</button>
          </div>
        </div>
        <div class="review-stats compact api-stat-grid">
          <div class="review-stat"><strong>${summary.snapshot_count || 0}</strong><span>接口快照</span></div>
          <div class="review-stat"><strong>${summary.endpoint_count || 0}</strong><span>接口数</span></div>
          <div class="review-stat"><strong>${summary.plan_count || 0}</strong><span>用例计划</span></div>
          <div class="review-stat"><strong>${ms.configured ? '已配置' : '未配置'}</strong><span>MeterSphere</span></div>
        </div>
        <div class="api-two-column">
          <section class="api-panel">
            <h3>最近接口资产</h3>
            ${apiTestingSnapshots.length ? `<table class="assets-table"><thead><tr><th>快照</th><th>接口</th><th>时间</th></tr></thead><tbody>${apiTestingSnapshots.map(row => `
              <tr><td>${escapeHtml(row.title || row.name || '-')}</td><td>${escapeHtml(row.endpoint_count || 0)}</td><td>${escapeHtml(row.created_at || '-')}</td></tr>
            `).join('')}</tbody></table>` : apiTestingEmpty('暂无 OpenAPI 快照。')}
          </section>
          <section class="api-panel">
            <h3>最近计划 / 报告</h3>
            ${apiTestingPlans.length ? `<div class="api-list">${apiTestingPlans.map(plan => `
              <div class="api-list-row"><strong>${escapeHtml(plan.name || plan.plan_id)}</strong><span>${apiStatusPill(apiPlanStatusText(plan.status), plan.status === 'confirmed' ? 'success' : 'warn')} ${escapeHtml(plan.case_count || 0)} 条</span></div>
            `).join('')}</div>` : apiTestingEmpty('暂无 API 用例计划。')}
          </section>
        </div>
      </div>
    `;
  } catch(e) {
    area.innerHTML = `<div class="generation-records">${apiTestingEmpty(e.message || 'API 工作台读取失败')}</div>`;
  }
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
      method: ''
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
  if (checked) state.endpointIds.add(String(endpointId));
  else state.endpointIds.delete(String(endpointId));
  syncApiEndpointCheckboxStates();
}

function toggleApiEndpointSelection(checked) {
  const state = apiModuleSelectionState();
  document.querySelectorAll('.api-endpoint-check').forEach(input => {
    if (checked) state.endpointIds.add(String(input.value));
    else state.endpointIds.delete(String(input.value));
  });
  syncApiEndpointCheckboxStates();
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
      <header class="api-asset-header">
        <div class="workflow-kicker">API ASSET · APIFOX / OPENAPI</div>
        <h2>接口资产</h2>
        <p>服务端只读同步 Apifox，内容变化才生成新版本。同步失败时继续保留上一活动版本。</p>
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

function scheduleApiAssetSyncPoll(sync) {
  stopApiAssetSyncPolling();
  if (!sync?.sync_id || apiAssetSyncTerminal(sync) || activeWorkflow !== 'api_assets') return;
  const delay = Math.max(500, Number(sync.poll_after_ms || 1000));
  apiAssetSyncPollTimer = setTimeout(() => pollApiAssetSync(sync.sync_id), delay);
}

function selectedApiAssetSource() {
  return apiTestingSources.find(item => String(item.source_id || '') === String(apiAssetSelectedSourceId || '')) || apiTestingSources[0] || null;
}

function renderApiProjectSelector(sources, selectedId) {
  const options = (sources || []).map(source => {
    const label = `${source.name || source.source_id || 'API 项目'} · ${source.project_id || '未配置项目 ID'}`;
    return `<option value="${escapeHtml(source.source_id || '')}" ${String(source.source_id || '') === String(selectedId || '') ? 'selected' : ''}>${escapeHtml(label)}</option>`;
  }).join('');
  return `<div class="api-project-switcher"><select class="api-project-select" aria-label="选择 Apifox 项目" onchange="selectApiAssetSource(this.value)">${options}</select><button class="btn-sm icon-only api-project-add" type="button" title="新增 Apifox 项目" aria-label="新增 Apifox 项目" onclick="startApiSourceDraft()">＋</button></div>`;
}

function renderApiSourceSummary(source, latestSync, snapshot = {}) {
  const configured = source?.configured === true;
  const status = latestSync?.status || source?.last_sync_status || '';
  const syncDisabled = !configured || ['queued', 'running'].includes(status);
  return `
    <div class="api-source-status-row">
      <div class="api-source-identity">
        ${apiStatusPill(configured ? '连接已配置' : '待配置', configured ? 'success' : 'warn')}
        ${renderApiProjectSelector(apiTestingSources, source?.source_id)}
        <span>${source?.project_id ? `项目 ${escapeHtml(source.project_id)}` : '尚未填写项目 ID'} · ${source?.credential_configured ? '令牌已配置' : '令牌未配置'}</span>
      </div>
      <div class="api-source-actions">
        <button class="btn-sm primary" onclick="startApiAssetSync()" ${syncDisabled ? 'disabled' : ''}>同步 Apifox</button>
        <button class="btn-sm icon-only" title="刷新接口资产" aria-label="刷新接口资产" onclick="refreshApiAssetWorkspace(true)">↻</button>
        <button class="btn-sm icon-only" title="Apifox 来源设置" aria-label="Apifox 来源设置" onclick="toggleApiSourceSettings()">⚙</button>
      </div>
    </div>
    <div class="api-source-facts">
      <span><small>最近成功</small><strong>${escapeHtml(source?.last_success_at || '尚未同步')}</strong></span>
      <span><small>文档版本</small><strong>${escapeHtml(snapshot.version || '等待同步')}</strong></span>
      <span><small>同步周期</small><strong>${escapeHtml(source?.sync_interval_minutes || 60)} 分钟</strong></span>
      <span><small>来源状态</small><strong>${escapeHtml(apiAssetSyncStatusText(status))}</strong></span>
    </div>
    ${source?.last_error ? `<div class="api-inline-error">${escapeHtml(source.last_error)}</div>` : ''}
  `;
}

function renderApiSourceSettings(source = {}) {
  const credentialConfigured = source.credential_configured === true;
  const credentialEditorOpen = !credentialConfigured || apiSourceCredentialEditing;
  const scope = source.sync_scope || { mode: 'all', module_paths: [] };
  const scopeState = apiModuleSelectionState();
  const selectedModules = apiTestingSourceDraftMode ? [] : (scopeState.selectedModules.size ? Array.from(scopeState.selectedModules) : (scope.module_paths || []));
  const selectedSummary = selectedModules.length ? selectedModules.join('、') : '尚未选择模块';
  return `
    <div class="api-source-settings-head"><div><span>APIFOX SOURCE</span><h3>${apiTestingSourceDraftMode ? '新增 Apifox 项目' : '只读同步设置'}</h3></div><button class="btn-sm icon-only" title="${apiTestingSourceDraftMode ? '取消新增 Apifox 项目' : '关闭设置'}" aria-label="${apiTestingSourceDraftMode ? '取消新增 Apifox 项目' : '关闭设置'}" onclick="${apiTestingSourceDraftMode ? 'cancelApiSourceDraft()' : 'toggleApiSourceSettings(false)'}">×</button></div>
    <div class="api-source-settings-grid">
      <label><span>来源名称</span><input id="api-source-name" value="${escapeHtml(source.name || 'Apifox 接口')}" placeholder="例如：3D 接口"></label>
      <label><span>项目 ID</span><input id="api-source-project-id" value="${escapeHtml(source.project_id || '')}" inputmode="numeric" placeholder="Apifox 项目设置中的 Project ID"></label>
      <label><span>分支 ID（可选）</span><input id="api-source-branch-id" value="${escapeHtml(source.branch_id || '')}" placeholder="默认主分支"></label>
      <label><span>环境 ID（可选）</span><input id="api-source-environment-id" value="${escapeHtml(source.environment_id || '')}" inputmode="numeric" placeholder="导出指定环境的服务地址"></label>
      <label><span>同步周期（分钟）</span><input id="api-source-interval" type="number" min="15" max="1440" step="15" value="${escapeHtml(source.sync_interval_minutes || 60)}"></label>
      <div class="api-source-field api-source-token-field">
        <span>访问令牌</span>
        <div id="api-source-credential-saved" class="api-source-credential-saved" ${credentialEditorOpen ? 'hidden' : ''}>
          <div class="api-source-credential-state"><span aria-hidden="true">✓</span><div><strong>已安全保存</strong><small>密钥仅保存在服务端</small></div></div>
          <button type="button" class="btn-sm" aria-label="更换 Apifox 访问令牌" onclick="editApiSourceCredential()">更换</button>
        </div>
        <div id="api-source-token-editor" class="api-source-token-editor" ${credentialEditorOpen ? '' : 'hidden'}>
          <input id="api-source-token" type="password" value="" autocomplete="new-password" aria-label="Apifox 访问令牌" placeholder="${credentialConfigured ? '输入新的 Apifox Access Token' : '输入 Apifox Access Token'}">
          ${credentialConfigured ? '<button type="button" class="btn-sm" aria-label="取消更换 Apifox 访问令牌" onclick="cancelApiSourceCredentialEdit()">取消</button>' : ''}
        </div>
      </div>
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
      <button class="btn-sm primary" onclick="saveApiSourceConfig()">保存设置</button>
    </div>
  `;
}

function startApiSourceDraft() {
  apiTestingSourceDraftMode = true;
  apiAssetSettingsOpen = true;
  apiSourceCredentialEditing = false;
  const panel = document.getElementById('api-source-settings-panel');
  if (panel) {
    panel.innerHTML = renderApiSourceSettings({ sync_scope: { mode: 'all', module_paths: [] } });
    panel.hidden = false;
  }
}

function cancelApiSourceDraft() {
  apiTestingSourceDraftMode = false;
  apiSourceCredentialEditing = false;
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
      <details class="api-sync-log-detail" data-api-asset-log-key="${escapeHtml(key)}" onchange="toggleApiAssetSyncLog(${jsArg(sync.sync_id)}, this.open)">
        <summary><span>技术日志</span><small>${escapeHtml(events.length)} 条真实事件</small></summary>
        <div class="api-asset-sync-log">${events.length ? events.map(event => `
          <div><time>${escapeHtml(event.at || '-')}</time><strong title="${escapeHtml(event.phase || '')}">${escapeHtml(apiAssetSyncPhaseText(event.phase))}</strong><span>${escapeHtml(event.message || '')}</span></div>
        `).join('') : apiTestingEmpty('暂无同步事件')}</div>
      </details>
    </div>
  `;
}

function renderApiModuleTree(source, endpoints) {
  const rows = apiModuleRows(source, endpoints);
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
  const rows = apiModuleRows(source, apiTestingEndpoints);
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
  return apiTestingEndpoints.filter(endpoint => apiModulePathMatches(apiEndpointModulePath(endpoint), state.activeModulePath));
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
  apiActiveModuleEndpoints().forEach(endpoint => state.endpointIds.add(String(endpoint.endpoint_id || '')));
  renderApiModuleEndpointTable();
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
}

function renderApiModuleWorkspace() {
  const root = document.getElementById('api-module-workspace');
  if (!root) return;
  const source = selectedApiAssetSource() || {};
  const state = apiModuleSelectionState();
  const methods = Array.from(new Set(apiActiveModuleEndpoints().map(endpoint => endpoint.method).filter(Boolean))).sort();
  root.innerHTML = `
    <div class="api-module-workspace">
      <section class="api-module-pane">
        <div class="api-module-pane-head"><strong>业务模块</strong><span>${escapeHtml(apiModuleRows(source, apiTestingEndpoints).length)} 个</span></div>
        <div class="api-module-tree-scroll">${renderApiModuleTree(source, apiTestingEndpoints)}</div>
      </section>
      <section class="api-module-endpoints">
        <div class="api-module-pane-head"><div><strong>${escapeHtml(state.activeModulePath || '当前模块')}</strong><span>${state.activeModulePath ? `${apiActiveModuleEndpoints().length} 个接口` : '未选择'}</span></div><button class="btn-sm api-module-select-current" type="button" ${state.activeModulePath ? '' : 'disabled'} onclick="selectCurrentApiModule()">选择当前模块</button></div>
        <div class="api-module-filters">
          <input id="api-module-search" type="search" value="${escapeHtml(state.search)}" placeholder="搜索当前模块接口" oninput="setApiModuleSearch(this.value)">
          <select id="api-module-method-filter" aria-label="接口方法筛选" onchange="setApiModuleMethodFilter(this.value)"><option value="">全部方法</option>${methods.map(method => `<option value="${escapeHtml(method)}" ${state.method === method ? 'selected' : ''}>${escapeHtml(method)}</option>`).join('')}</select>
        </div>
        <div id="api-module-endpoint-table" class="api-module-endpoint-scroll"></div>
      </section>
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
      <div><span>活动版本</span><strong>${escapeHtml(asset.active_revision_id || snapshot.snapshot_id || '尚未生成')}</strong></div>
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
  apiAssetSelectedRevisionId = '';
  apiAssetRevisionPinned = false;
  apiAssetActiveSyncId = '';
  apiTestingProjectScope = { sourceId: apiAssetSelectedSourceId, revisionId: '' };
  apiTestingSelectionByScope.delete(apiProjectScopeKey());
  await refreshApiAssetWorkspace(true);
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
  const scopeMode = document.querySelector('[data-sync-scope].active')?.dataset.syncScope || 'all';
  const selectedModules = apiSourceSelectedModulePaths(source);
  if (scopeMode === 'selected' && !selectedModules.length) {
    showToast('请选择至少一个同步模块', 'error');
    return;
  }
  const payload = {
    source_id: source.source_id || undefined,
    source_type: 'apifox',
    name: document.getElementById('api-source-name')?.value.trim() || 'Apifox 接口',
    project_id: document.getElementById('api-source-project-id')?.value.trim() || '',
    branch_id: document.getElementById('api-source-branch-id')?.value.trim() || '',
    environment_id: document.getElementById('api-source-environment-id')?.value.trim() || '',
    sync_interval_minutes: Number(document.getElementById('api-source-interval')?.value || 60),
    sync_enabled: !!document.getElementById('api-source-sync-enabled')?.checked,
    sync_scope: { mode: scopeMode, module_paths: scopeMode === 'selected' ? selectedModules : [] },
    selected_modules: scopeMode === 'selected' ? selectedModules : [],
    clear_credentials: !!clearCredentials
  };
  if (token) payload.access_token = token;
  try {
    const data = await apiRequest('/api-testing/sources', { method: 'POST', body: payload });
    apiAssetSelectedSourceId = data.source?.source_id || apiAssetSelectedSourceId;
    apiTestingSourceDraftMode = false;
    apiSourceCredentialEditing = false;
    if (!source.source_id) {
      apiAssetSelectedRevisionId = '';
      apiAssetRevisionPinned = false;
    }
    const tokenInput = document.getElementById('api-source-token');
    if (tokenInput) tokenInput.value = '';
    showToast('✓ Apifox 来源设置已保存', 'success');
    await refreshApiAssetWorkspace(true);
  } catch (e) {
    showToast(e.message || 'Apifox 来源设置保存失败', 'error');
  }
}

async function clearApiSourceCredential() {
  if (!confirm('确认清除服务端保存的 Apifox 令牌？清除后同步会停止。')) return;
  await saveApiSourceConfig(true);
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
  if (!syncId || activeWorkflow !== 'api_assets') return;
  try {
    const data = await apiRequest(`/api-testing/syncs/${encodeURIComponent(syncId)}`);
    if (activeWorkflow !== 'api_assets' || syncId !== apiAssetActiveSyncId) return;
    const sync = data.sync || {};
    captureApiAssetSyncViewState(document.getElementById('api-assets-sync'));
    const region = document.getElementById('api-assets-sync');
    if (region) region.innerHTML = renderApiAssetSync(sync);
    restoreApiAssetSyncViewState(region);
    if (apiAssetSyncTerminal(sync)) {
      stopApiAssetSyncPolling();
      await refreshApiAssetWorkspace(true);
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
    if (activeWorkflow === 'api_assets' && syncId === apiAssetActiveSyncId) {
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
  if (!generation?.generation_id) return apiTestingEmpty('尚未发起本范围的 AI 计划生成。');
  const batches = generation.batches || [];
  const events = generation.events || [];
  const failedCount = Number(generation.failed_batches || batches.filter(batch => batch.status === 'failed').length);
  const retryable = ['partial', 'failed'].includes(generation.status) && failedCount > 0;
  const logKey = apiPlanGenerationLogKey(generation.generation_id);
  const logOpen = apiPlanGenerationExpandedKeys.has(logKey);
  return `
    <article class="api-plan-generation" data-status="${escapeHtml(generation.status || '')}">
      <header class="api-plan-generation-head">
        <div>
          <span>本次生成</span>
          <h3>${escapeHtml(apiPlanGenerationStatusText(generation.status))}</h3>
          <code>${escapeHtml(generation.generation_id)}</code>
        </div>
        <div class="api-plan-generation-progress">
          ${apiStatusPill(apiPlanGenerationStatusText(generation.status), apiPlanGenerationStatusClass(generation.status))}
          <strong>${escapeHtml(generation.completed_batches || 0)} / ${escapeHtml(generation.batch_count || batches.length)} 批</strong>
        </div>
      </header>
      <div class="api-plan-generation-scope">
        <span>来源 <code>${escapeHtml(generation.source_id || '-')}</code></span>
        <span>版本 <code>${escapeHtml(generation.asset_revision_id || '-')}</code></span>
        <span>模块 ${escapeHtml((generation.module_paths || []).join('、') || '-')}</span>
        <span>接口 ${escapeHtml((generation.selected_endpoint_keys || []).length)}</span>
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
              ? `<code>${escapeHtml(batch.plan_id)}</code><button class="btn-sm ghost" onclick="openApiTestPlan(${jsArg(batch.plan_id)})">查看计划</button>`
              : `<span>${escapeHtml(batch.error || (batch.status === 'running' ? '等待 AI 返回' : '尚未生成计划'))}</span>`}</div>
          </div>
        `;
      }).join('')}</div>
      ${generation.error ? `<div class="api-inline-error">${escapeHtml(generation.error)}</div>` : ''}
      ${retryable ? `<div class="generation-record-actions"><button class="btn-sm ai" onclick="retryApiPlanGeneration(${jsArg(generation.generation_id)})">重试失败批次</button><span>仅重试 ${failedCount} 个失败批次，已成功计划保持不变。</span></div>` : ''}
      <details class="api-generation-log-detail" data-api-generation-log-key="${escapeHtml(logKey)}" ${logOpen ? 'open' : ''} ontoggle="toggleApiPlanGenerationLog(${jsArg(generation.generation_id)}, this.open)">
        <summary><strong>技术日志</strong><span>${events.length} 条服务端事件</span></summary>
        <div class="api-generation-log-content" onscroll="rememberApiPlanGenerationLogScroll(${jsArg(logKey)}, this.scrollTop)">${events.length ? events.map(event => {
          const detail = event.detail == null ? '' : (typeof event.detail === 'string' ? event.detail : JSON.stringify(event.detail, null, 2));
          return `<div><time>${escapeHtml(event.at || event.timestamp || '-')}</time><strong>${escapeHtml(event.message || event.summary || event.status || '生成事件')}</strong><small>${escapeHtml(event.status || event.phase || '')}</small>${detail ? `<pre>${escapeHtml(detail)}</pre>` : ''}</div>`;
        }).join('') : apiTestingEmpty('暂无生成日志')}</div>
      </details>
    </article>
  `;
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
    && activeWorkflow === 'api_plan';
}

function scheduleApiPlanGenerationPoll(generation, requestId = apiPlanGenerationRequestId, capturedScopeKey = apiPlanGenerationScopeKey()) {
  stopApiPlanGenerationPolling();
  if (!generation?.generation_id || apiPlanGenerationTerminal(generation) || activeWorkflow !== 'api_plan') return;
  const delay = Math.max(50, Number(generation.poll_after_ms || 1000));
  apiPlanGenerationPollTimer = setTimeout(
    () => pollApiPlanGeneration(generation.generation_id, requestId, capturedScopeKey),
    delay
  );
}

async function pollApiPlanGeneration(generationId, requestId = apiPlanGenerationRequestId, capturedScopeKey = apiPlanGenerationScopeKey()) {
  if (activeWorkflow !== 'api_plan' || requestId !== apiPlanGenerationRequestId || capturedScopeKey !== apiPlanGenerationScopeKey()) return;
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
      if (generation.status === 'succeeded') await refreshApiPlanCards(capturedScopeKey);
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

async function showApiPlanPage() {
  const area = setApiTestingPage('api_plan', 'AI 用例计划', '按来源、版本和模块生成计划；确认前复核 AI 轨迹、绑定与鉴权。');
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
    apiTestingCurrentSnapshotId = (assets.snapshot || {}).revision_id || (assets.snapshot || {}).snapshot_id || apiTestingCurrentSnapshotId || ((assets.snapshots || [])[0] || {}).snapshot_id || '';
    apiTestingProjectScope = {sourceId, revisionId: apiTestingCurrentSnapshotId};
    apiPlanBindingContext = bindingResponse.context || {binding: bindingResponse.binding || {}};
    if (!apiPlanBindingContext.binding) apiPlanBindingContext.binding = bindingResponse.binding || {};
    apiTestingPlans = await loadApiPlanDetails(planResponse.plans || [], sourceId, controller);
    if (controller !== apiPlanRequestController || requestId !== apiPlanPageRequestId || activeWorkflow !== 'api_plan' || capturedScopeKey !== apiPlanGenerationScopeKey()) return;
    if (apiPlanGenerationCurrent && (
      apiPlanGenerationCurrent.source_id !== sourceId
      || apiPlanGenerationCurrent.asset_revision_id !== apiTestingCurrentSnapshotId
    )) apiPlanGenerationCurrent = null;
    const source = selectedApiAssetSource() || {};
    area.innerHTML = `
      <div class="api-testing-page api-plan-workspace">
        <div class="generation-record-head">
          <div class="workflow-kicker">AI PLAN · API CASES</div>
          <h2>AI 用例计划</h2>
          <p>当前范围固定为 ${escapeHtml(source.name || sourceId || '未选择来源')} / ${escapeHtml(apiTestingCurrentSnapshotId || '未选择版本')}，服务端按 12 个接口一批顺序生成。</p>
          <div class="generation-record-actions">
            <button class="btn-sm ai api-plan-generate-action" onclick="generateApiTestPlan()">生成计划草稿</button>
            <button class="btn-sm" onclick="showApiAssetsPage()">调整接口范围</button>
          </div>
        </div>
        <div class="api-plan-layout">
          <section class="api-panel api-plan-selection-panel">
            <div class="api-section-heading"><div><span>生成范围</span><h3>已选接口</h3></div><strong>${selectedApiPlanEndpointIds().length} / ${apiTestingEndpoints.length}</strong></div>
            <div class="api-plan-scope-facts"><span>来源 <code>${escapeHtml(sourceId || '-')}</code></span><span>版本 <code>${escapeHtml(apiTestingCurrentSnapshotId || '-')}</code></span><span>模块 ${escapeHtml(selectedApiPlanModulePaths().join('、') || '尚未选择')}</span></div>
            <div class="api-endpoint-scroll">${renderApiAssetTable(apiTestingEndpoints)}</div>
          </section>
          <div class="api-plan-review-column">
            <section class="api-panel" id="api-plan-generation-region">${renderApiPlanGeneration(apiPlanGenerationCurrent)}</section>
            <section class="api-panel" id="api-plan-list-region">
              <div class="api-section-heading"><div><span>服务端事实</span><h3>计划审阅</h3></div><small>${apiTestingPlans.length} 个计划</small></div>
              ${renderApiPlanList(apiTestingPlans)}
            </section>
            <section class="api-panel" id="api-plan-result">${apiTestingEmpty('选择一个计划查看完整用例合同。')}</section>
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
  if (!plans.length) return apiTestingEmpty('当前来源暂无计划草稿。');
  return `<div class="api-plan-card-list">${plans.map(plan => {
    const stale = (plan.revision_state || {}).state === 'stale';
    return `
      <article class="api-plan-card ${stale ? 'is-stale' : ''}">
        <header><button type="button" class="api-plan-list-button" data-plan-id="${escapeHtml(plan.plan_id || '')}" onclick="openApiTestPlan(${jsArg(plan.plan_id)})"><strong>${escapeHtml(plan.name || plan.plan_id)}</strong><small>${escapeHtml(plan.created_at || '')}</small></button><div>${apiStatusPill(apiPlanStatusText(plan.status), plan.status === 'confirmed' ? 'success' : 'warn')}${stale ? apiStatusPill('已过期', 'danger') : ''}</div></header>
        ${renderApiPlanFacts(plan)}
      </article>
    `;
  }).join('')}</div>`;
}

async function refreshApiPlanCards(capturedScopeKey = apiPlanGenerationScopeKey()) {
  if (activeWorkflow !== 'api_plan' || capturedScopeKey !== apiPlanGenerationScopeKey()) return;
  const sourceId = apiTestingProjectScope.sourceId;
  const controller = new AbortController();
  try {
    const response = await apiRequest(`/api-testing/plans${sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : ''}`, { signal: controller.signal });
    if (activeWorkflow !== 'api_plan' || capturedScopeKey !== apiPlanGenerationScopeKey()) return;
    apiTestingPlans = await loadApiPlanDetails(response.plans || [], sourceId, controller);
    if (activeWorkflow !== 'api_plan' || capturedScopeKey !== apiPlanGenerationScopeKey()) return;
    const target = document.getElementById('api-plan-list-region');
    if (target) target.innerHTML = `<div class="api-section-heading"><div><span>服务端事实</span><h3>计划审阅</h3></div><small>${apiTestingPlans.length} 个计划</small></div>${renderApiPlanList(apiTestingPlans)}`;
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
    if (requestId !== apiPlanPageRequestId || capturedScopeKey !== apiPlanGenerationScopeKey() || activeWorkflow !== 'api_plan') return;
    apiTestingCurrentPlan = data.plan || null;
    if (target) target.innerHTML = renderApiPlanDetail(apiTestingCurrentPlan || {});
  } catch (error) {
    if (requestId !== apiPlanPageRequestId || capturedScopeKey !== apiPlanGenerationScopeKey() || activeWorkflow !== 'api_plan') return;
    if (target) target.innerHTML = `<h3>读取失败</h3>${apiTestingEmpty(error.message || '计划详情读取失败')}`;
    showToast(error.message || '计划详情读取失败', 'error');
  }
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
  return `
    <div class="api-plan-detail-head"><div><span>plan_id <code>${escapeHtml(plan.plan_id || '-')}</code></span><h3>${escapeHtml(plan.name || 'API 用例计划')}</h3></div>${isStale ? apiStatusPill('版本已过期', 'danger') : apiStatusPill('版本最新', 'success')}</div>
    ${renderApiPlanFacts(plan)}
    <div class="review-stats compact api-plan-readiness">
      <div class="review-stat"><strong>${escapeHtml(plan.endpoint_count || 0)}</strong><span>接口</span></div>
      <div class="review-stat"><strong>${escapeHtml(plan.case_count || cases.length)}</strong><span>用例</span></div>
      <div class="review-stat"><strong>${escapeHtml(plan.executable_case_count || 0)}</strong><span>可执行</span></div>
      <div class="review-stat"><strong>${escapeHtml(plan.needs_review_case_count || 0)}</strong><span>待补数据</span></div>
    </div>
    <div class="api-plan-state-line">
      ${apiStatusPill(apiPlanStatusText(plan.status), plan.status === 'confirmed' ? 'success' : 'warn')}
      <span>${escapeHtml(plan.source === 'ai' ? 'AI 生成并经平台校验' : (plan.source === 'local_fallback' ? 'AI 失败，规则兜底' : '规则生成'))}</span>
    </div>
    ${missing.length ? `<div class="api-readiness-missing"><strong>待补数据：</strong>${missing.map(item => `<span>${escapeHtml(item)}</span>`).join('')}</div>` : ''}
    ${bindingDrift.length ? `<div class="api-stale-warning">执行绑定已变化：${escapeHtml(bindingDrift.join('、'))}</div>` : ''}
    ${isStale ? `<div class="api-stale-warning">${escapeHtml(actionReason)}</div>` : ''}
    <div class="generation-record-actions">
      <button class="btn-sm success" onclick="confirmApiTestPlan(${jsArg(plan.plan_id)})" ${canConfirm ? '' : 'disabled'} title="${escapeHtml(canConfirm ? '确认可执行用例' : actionReason)}">${plan.status === 'confirmed' ? '已确认' : '确认计划'}</button>
      <button class="btn-sm" onclick="showApiExecutionPage()" ${canExecute ? '' : 'disabled'} title="${escapeHtml(canExecute ? '进入 MeterSphere 执行' : actionReason)}">去执行</button>
      ${isStale ? `<button class="btn-sm ai" onclick="regenerateApiPlan(${jsArg(plan.plan_id)})">重新生成</button>` : ''}
    </div>
    <div class="api-case-scroll"><table class="assets-table api-case-table">
      <thead><tr><th>用例</th><th>类型</th><th>请求</th><th>断言</th><th>执行状态</th></tr></thead>
      <tbody>${cases.map(item => `
        <tr>
          <td><strong>${escapeHtml(item.name || '-')}</strong><small>${escapeHtml(item.priority || '-')}</small></td>
          <td>${escapeHtml(item.type || '-')}</td>
          <td>${escapeHtml(apiCaseRequestText(item))}</td>
          <td>${escapeHtml((item.assertions || []).map(apiCaseAssertionText).join('；') || '-')}</td>
          <td>${(item.readiness || {}).state === 'executable'
            ? apiStatusPill('可执行', 'success')
            : `${apiStatusPill('待补数据', 'warn')}<small>${escapeHtml(((item.readiness || {}).missing || []).join('；') || '-')}</small>`}</td>
        </tr>
      `).join('')}</tbody>
    </table></div>
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
    const target = document.getElementById('api-plan-result');
    if (target) target.innerHTML = renderApiPlanDetail(apiTestingCurrentPlan || {});
    showToast('✓ API 用例计划已确认', 'success');
  } catch(e) {
    showToast(e.message || '确认失败', 'error');
  }
}

function stopApiExecutionPolling(abortRequest = false) {
  if (apiExecutionPollTimer) clearTimeout(apiExecutionPollTimer);
  apiExecutionPollTimer = null;
  if (abortRequest && apiExecutionPollController) {
    apiExecutionPollController.abort();
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
  const area = setApiTestingPage('api_execution', 'MeterSphere 执行', '从已确认计划推送用例，跟踪 MeterSphere 真实运行并同步报告。');
  if (!area) return;
  area.innerHTML = `
    <div class="api-testing-page api-execution-console">
      <section id="api-execution-header" class="api-execution-header">${apiTestingEmpty('正在检查 MeterSphere...')}</section>
      <section id="api-active-run" class="api-active-run" hidden></section>
      <section class="api-execution-plans-section">
        <div class="api-section-heading"><div><span>日常执行</span><h2>已确认计划</h2></div><small id="api-plan-count">0 个计划</small></div>
        <div id="api-execution-plans">${apiTestingEmpty('正在读取已确认计划...')}</div>
      </section>
      <div id="api-ms-settings-backdrop" class="api-settings-backdrop" onclick="closeApiMeterSphereSettings()" hidden></div>
      <aside id="api-ms-settings-drawer" class="api-settings-drawer" aria-label="MeterSphere 设置" aria-hidden="true">
        <div id="api-ms-settings-content"></div>
      </aside>
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
    const data = await apiRequest(`/api-testing/metersphere/execution-context${query.toString() ? `?${query}` : ''}`, { signal: controller.signal });
    if (requestId !== apiExecutionContextRequestId || controller !== apiExecutionRequestController || activeWorkflow !== 'api_execution' || capturedScopeKey !== apiProjectScopeKey()) return;
    apiExecutionContext = data;
    apiTestingPlans = data.plans || [];
    const active = (data.active_runs || [])[0] || null;
    apiExecutionActiveId = active?.execution_id || '';
    renderApiExecutionDynamic(data, active);
    if (active && !apiExecutionTerminal(active)) {
      apiExecutionPollRequestId += 1;
      scheduleApiExecutionPoll(active, apiExecutionPollRequestId, capturedScopeKey);
    }
    else stopApiExecutionPolling();
  } catch (e) {
    if (controller !== apiExecutionRequestController || activeWorkflow !== 'api_execution' || capturedScopeKey !== apiProjectScopeKey()) return;
    const header = document.getElementById('api-execution-header');
    if (header) header.innerHTML = `<div class="api-inline-error">${escapeHtml(e.message || 'MeterSphere 执行上下文读取失败')}</div>`;
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
  if (plans) plans.innerHTML = renderApiExecutionPlans(context.plans || [], context);
  if (count) count.textContent = `${(context.plans || []).length} 个计划`;
  if (active) {
    active.hidden = !activeRun;
    active.innerHTML = activeRun ? renderApiActiveRun(activeRun) : '';
  }
  restoreApiExecutionLogViewState(active);
  if (apiExecutionSettingsOpen) renderApiMeterSphereSettings(context);
}

function renderApiExecutionHeader(context) {
  const connection = context.connection || {};
  const readiness = context.readiness || {};
  const metadata = context.metadata || {};
  const selection = context.selection || {};
  const selectedEnvironments = (context.environments || []).filter(item => !selection.project_id || !item.project_id || String(item.project_id) === String(selection.project_id));
  const connectionClass = connection.state === 'connected' ? 'success' : (connection.state === 'disconnected' ? 'danger' : 'warn');
  const missing = readiness.missing || [];
  return `
    <div class="api-execution-status-row">
      <div class="api-connection-summary">
        ${apiStatusPill(apiConnectionText(connection.state), connectionClass)}
        <strong>${escapeHtml(apiReadinessText(readiness.state))}</strong>
        <span>检查于 ${escapeHtml(connection.checked_at || '-')} · ${escapeHtml(connection.latency_ms || 0)}ms</span>
      </div>
      <div class="api-icon-actions">
        <button class="btn-sm icon-only" title="刷新执行数据" aria-label="刷新执行数据" onclick="refreshApiExecutionContext(true)">↻</button>
        <button class="btn-sm icon-only" title="MeterSphere 设置" aria-label="MeterSphere 设置" onclick="openApiMeterSphereSettings()">⚙</button>
      </div>
    </div>
    <div class="api-execution-selectors">
      <label><span>业务</span><select class="api-execution-project-select" onchange="changeApiMeterSphereProject(this.value)">${apiSelectOptions(context.businesses, selection.project_id, '选择业务')}</select></label>
      <label><span>环境</span><select class="api-execution-environment-select" onchange="changeApiMeterSphereEnvironment(this.value)" ${selection.project_id ? '' : 'disabled'}>${apiSelectOptions(selectedEnvironments, selection.environment_id, '选择环境')}</select></label>
      <div class="api-readiness-fact">
        <span>${metadata.stale ? '过期缓存，仅供查看' : '实时数据'}</span>
        <strong>${escapeHtml(readiness.primary_action || '-')}</strong>
      </div>
    </div>
    ${missing.length ? `<div class="api-readiness-missing"><strong>还缺：</strong>${missing.map(item => `<span>${escapeHtml(item)}</span>`).join('')}</div>` : ''}
    ${metadata.stale ? `<div class="api-stale-warning">业务或环境来自过期缓存。完成一次实时校验前，执行按钮保持禁用。</div>` : ''}
    ${renderApiBusinessAuthPanel(context)}
  `;
}

function apiBusinessAuthMetadata(context = apiExecutionContext || {}) {
  return context.auth_binding || context.binding?.auth_binding || {};
}

function apiBusinessAuthEnvironmentName(context, auth) {
  const environment = (context.environments || []).find(item => String(item.id || '') === String(auth.environment_id || context.selection?.environment_id || ''));
  return environment?.name || context.binding?.environment_name || auth.environment_name || auth.environment_id || '未选择环境';
}

function renderApiBusinessAuthPanel(context = {}) {
  const auth = apiBusinessAuthMetadata(context);
  const configured = auth.configured === true;
  const selectedEnvironmentId = context.selection?.environment_id || context.binding?.environment_id || '';
  const canEdit = !!context.source_id && !!selectedEnvironmentId;
  if (configured && !apiBusinessAuthEditing) {
    return `
      <section class="api-business-auth-panel" data-configured="true">
        <div class="api-business-auth-head"><div><span>业务鉴权</span><h3>${escapeHtml(auth.auth_type === 'api_key' ? 'API Key' : 'Bearer')} 已配置</h3></div>${apiStatusPill('安全保存在 MeterSphere 环境', 'success')}</div>
        <div class="api-business-auth-facts">
          <div><span>环境</span><strong>${escapeHtml(apiBusinessAuthEnvironmentName(context, auth))}</strong><small>${escapeHtml(auth.environment_id || '-')}</small></div>
          <div><span>变量</span><strong>${escapeHtml(auth.variable_name || '-')}</strong><small>${escapeHtml(auth.auth_ref || '服务端引用')}</small></div>
          <div><span>请求头</span><strong>${escapeHtml(auth.header_name || (auth.auth_type === 'bearer' ? 'Authorization' : '-'))}</strong><small>${escapeHtml(auth.updated_at || auth.configured_at || '-')}</small></div>
        </div>
        <div class="api-business-auth-actions">
          <button class="btn-sm" aria-label="更换业务鉴权" onclick="editApiBusinessAuth()">更换</button>
          <button class="btn-sm danger ghost" onclick="clearApiBusinessAuth()">明确清除</button>
        </div>
      </section>
    `;
  }
  if (!apiBusinessAuthEditing) {
    return `
      <section class="api-business-auth-panel" data-configured="false">
        <div class="api-business-auth-head"><div><span>业务鉴权</span><h3>尚未配置</h3></div>${apiStatusPill('执行前必需', 'warn')}</div>
        <p>密钥只写入当前来源绑定的 MeterSphere 环境变量，平台不回填明文。</p>
        <button class="btn-sm primary" aria-label="配置业务鉴权" onclick="editApiBusinessAuth()" ${canEdit ? '' : 'disabled'}>配置鉴权</button>
      </section>
    `;
  }
  return `
    <section class="api-business-auth-panel is-editing" data-configured="${configured ? 'true' : 'false'}">
      <div class="api-business-auth-head"><div><span>业务鉴权</span><h3>${configured ? '更换密钥' : '配置密钥'}</h3></div><small>${escapeHtml(apiBusinessAuthEnvironmentName(context, auth))}</small></div>
      <div class="api-auth-segmented" role="group" aria-label="业务鉴权类型">
        <button type="button" data-auth-type="bearer" class="${apiBusinessAuthType === 'bearer' ? 'active' : ''}" onclick="setApiBusinessAuthType('bearer')">Bearer</button>
        <button type="button" data-auth-type="api_key" class="${apiBusinessAuthType === 'api_key' ? 'active' : ''}" onclick="setApiBusinessAuthType('api_key')">API Key</button>
      </div>
      <div class="api-business-auth-form">
        ${apiBusinessAuthType === 'api_key' ? `<label><span>API Key Header</span><input id="api-business-auth-header" autocomplete="off" value="${escapeHtml(auth.auth_type === 'api_key' ? auth.header_name || '' : '')}" placeholder="X-API-Key"></label>` : ''}
        <label><span>新密钥</span><input id="api-business-auth-secret" type="password" autocomplete="new-password" value="" placeholder="输入后仅发送一次"></label>
      </div>
      <div class="api-business-auth-actions">
        <button class="btn-sm" aria-label="取消更换业务鉴权" onclick="cancelApiBusinessAuthEdit()">取消</button>
        <button class="btn-sm primary" onclick="saveApiBusinessAuth()">保存业务鉴权</button>
      </div>
    </section>
  `;
}

function renderApiBusinessAuthInHeader() {
  const header = document.getElementById('api-execution-header');
  if (header && apiExecutionContext) header.innerHTML = renderApiExecutionHeader(apiExecutionContext);
}

function editApiBusinessAuth() {
  const auth = apiBusinessAuthMetadata();
  apiBusinessAuthEditing = true;
  apiBusinessAuthType = auth.auth_type === 'api_key' ? 'api_key' : 'bearer';
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

async function saveApiBusinessAuth() {
  const sourceId = apiExecutionContext?.source_id || apiTestingProjectScope.sourceId;
  const secretInput = document.getElementById('api-business-auth-secret');
  const secret = secretInput?.value || '';
  const headerName = apiBusinessAuthType === 'api_key'
    ? document.getElementById('api-business-auth-header')?.value.trim() || ''
    : 'Authorization';
  if (!sourceId || !secret) {
    showToast(!sourceId ? '请先选择 API 来源' : '请输入新的业务鉴权密钥', 'error');
    return;
  }
  try {
    const data = await apiRequest(`/api-testing/sources/${encodeURIComponent(sourceId)}/auth-binding`, {
      method: 'POST',
      body: {auth_type: apiBusinessAuthType, header_name: headerName, secret}
    });
    if (secretInput) secretInput.value = '';
    apiBusinessAuthEditing = false;
    const binding = data.binding || {};
    apiExecutionContext = {
      ...(apiExecutionContext || {}),
      auth_binding: binding,
      binding: {...(apiExecutionContext?.binding || {}), auth_binding: binding}
    };
    renderApiExecutionDynamic(apiExecutionContext, (apiExecutionContext.active_runs || [])[0] || null);
    showToast('✓ 业务鉴权已写入当前 MeterSphere 环境', 'success');
    await refreshApiExecutionContext(true);
  } catch (error) {
    if (secretInput) secretInput.value = '';
    showToast(error.message || '业务鉴权保存失败', 'error');
  }
}

async function clearApiBusinessAuth() {
  const sourceId = apiExecutionContext?.source_id || apiTestingProjectScope.sourceId;
  if (!sourceId || !confirm('确认清除当前来源在 MeterSphere 环境中的业务鉴权变量？清除后相关计划将不可执行。')) return;
  try {
    const data = await apiRequest(`/api-testing/sources/${encodeURIComponent(sourceId)}/auth-binding`, {method: 'DELETE'});
    const binding = data.binding || {configured: false};
    apiBusinessAuthEditing = false;
    apiExecutionContext = {
      ...(apiExecutionContext || {}),
      auth_binding: binding,
      binding: {...(apiExecutionContext?.binding || {}), auth_binding: binding}
    };
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
  if (reason === 'no_plans') return { text: '尚未生成 API 用例计划', action: '去生成计划', handler: 'showApiPlanPage()' };
  if (reason === 'unconfirmed_plans') return { text: '有待确认计划', action: '去确认计划', handler: 'showApiPlanPage()' };
  if (reason === 'no_executable_plans') return { text: '已确认计划仍缺测试数据', action: '查看计划', handler: 'showApiPlanPage()' };
  return { text: 'MeterSphere 尚未满足执行条件', action: '完成 MeterSphere 配置', handler: 'openApiMeterSphereSettings()' };
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
      <article class="api-execution-plan-row">
        <div class="api-plan-identity">
          <strong>${escapeHtml(plan.name || plan.plan_id)}</strong>
          <span>${escapeHtml(plan.endpoint_count || 0)} 个接口 · 可执行 ${escapeHtml(plan.executable_case_count || 0)} / 待补 ${escapeHtml(plan.needs_review_case_count || 0)} · 确认于 ${escapeHtml(plan.confirmed_at || '-')}</span>
        </div>
        <div class="api-plan-binding"><span>MeterSphere 计划</span><strong>${escapeHtml(plan.test_plan_name || plan.test_plan_id || '首次执行时创建或选择')}</strong></div>
        <div class="api-plan-latest"><span>最近运行</span><strong>${escapeHtml(apiExecutionStateText(latest.status))} · 通过率 ${escapeHtml(passRate)}</strong><small>${escapeHtml(latest.started_at || latest.created_at || '暂无历史')} · 耗时 ${escapeHtml(apiDurationText(latest.duration_seconds))}</small></div>
        <div class="api-plan-actions">
          <button class="btn-sm primary" onclick="startApiMeterSphereExecution(${jsArg(plan.plan_id)})" ${disabled ? 'disabled' : ''} title="${escapeHtml(disabled ? disabledReason : '推送确认用例并执行')}">推送并执行</button>
          <details class="api-plan-menu"><summary title="更多操作" aria-label="更多操作">⋯</summary><div>
            <button onclick="pushApiPlanToMeterSphere(${jsArg(plan.plan_id)})" ${disabled ? 'disabled' : ''}>仅推送</button>
            <button onclick="startApiMeterSphereExecution(${jsArg(plan.plan_id)})" ${disabled ? 'disabled' : ''}>重新执行</button>
            <button onclick="showApiReportsPage()">查看历史</button>
            <button onclick="openMeterSphereFromContext()">打开 MeterSphere</button>
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
  return `
    <div class="api-active-run-head">
      <div><span>当前运行</span><h2>${escapeHtml(execution.plan_name || execution.plan_id || 'MeterSphere 执行')}</h2></div>
      ${apiStatusPill(apiExecutionStateText(execution.status), execution.status === 'failed' ? 'danger' : (execution.status === 'succeeded' ? 'success' : 'warn'))}
    </div>
    <div class="api-run-meta"><span>execution_id <code>${escapeHtml(execution.execution_id || '-')}</code></span><span>run_id <code>${escapeHtml(execution.run_id || '等待触发')}</code></span><span>已运行 ${escapeHtml(apiDurationText(execution.duration_seconds))}</span><span>最后更新 ${escapeHtml(execution.updated_at || '-')}</span></div>
    <ol class="api-run-phases">${phases.map((phase, index) => `
      <li class="status-${escapeHtml(phase.state || 'waiting')}">
        <span class="api-phase-index">0${index + 1}</span><strong>${escapeHtml(phase.title || phase.id)}</strong><em>${escapeHtml(apiPhaseStateText(phase.state))}</em><small>${escapeHtml(phase.summary || phase.updated_at || '')}${phase.started_at ? ` · 耗时 ${escapeHtml(apiDurationText(phase.duration_seconds))}` : ''}</small>
      </li>
    `).join('')}</ol>
    ${execution.error ? `<div class="api-inline-error">${escapeHtml(execution.error)}</div>` : ''}
    ${renderApiExecutionLogRows(execution.events || [], execution.run_id || execution.execution_id)}
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

function renderApiExecutionLogRows(rows, runId = '') {
  if (!(rows || []).length) return `<div class="api-tech-log"><div class="api-tech-log-head"><h3>技术日志</h3></div>${apiTestingEmpty('暂无执行日志')}</div>`;
  return `<div class="api-tech-log"><div class="api-tech-log-head"><h3>技术日志</h3><span>${rows.length} 条真实事件</span></div>${rows.map(row => {
    const eventId = row.event_id || '';
    const eventRunId = row.run_id || row.execution_id || runId;
    const key = apiExecutionLogKey(eventRunId, eventId);
    const open = apiLogExpandedKeys.has(key);
    const detail = row.detail == null ? row.summary : (typeof row.detail === 'string' ? row.detail : JSON.stringify(row.detail, null, 2));
    return `
      <details class="api-log-detail" data-api-log-key="${escapeHtml(key)}" ${open ? 'open' : ''} ontoggle="toggleApiExecutionLog(${jsArg(eventRunId)}, ${jsArg(eventId)}, this.open)">
        <summary><time>${escapeHtml(row.timestamp || '-')}</time><strong>${escapeHtml(row.summary || row.phase_id || '执行事件')}</strong><small>${escapeHtml(row.phase_id || '')}</small></summary>
        <div class="api-log-content" onscroll="rememberApiExecutionLogScroll(${jsArg(key)}, this.scrollTop)"><pre>${escapeHtml(detail || '无更多详情')}</pre></div>
      </details>
    `;
  }).join('')}</div>`;
}

function scheduleApiExecutionPoll(execution, requestId = apiExecutionPollRequestId, capturedScopeKey = apiProjectScopeKey()) {
  stopApiExecutionPolling();
  if (!execution?.execution_id || apiExecutionTerminal(execution) || activeWorkflow !== 'api_execution') return;
  const delay = Math.max(1000, Number(execution.poll_after_ms || 3000));
  apiExecutionPollTimer = setTimeout(() => pollApiMeterSphereExecution(execution.execution_id, requestId, capturedScopeKey), delay);
}

async function pollApiMeterSphereExecution(executionId, requestId = apiExecutionPollRequestId, capturedScopeKey = apiProjectScopeKey()) {
  if (activeWorkflow !== 'api_execution' || executionId !== apiExecutionActiveId || requestId !== apiExecutionPollRequestId || capturedScopeKey !== apiProjectScopeKey()) return;
  if (apiExecutionPollController) apiExecutionPollController.abort();
  const controller = new AbortController();
  apiExecutionPollController = controller;
  try {
    const data = await apiRequest(`/api-testing/metersphere/executions/${encodeURIComponent(executionId)}`, {signal: controller.signal});
    if (controller !== apiExecutionPollController || requestId !== apiExecutionPollRequestId || capturedScopeKey !== apiProjectScopeKey() || activeWorkflow !== 'api_execution') return;
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
    if (controller !== apiExecutionPollController || requestId !== apiExecutionPollRequestId || capturedScopeKey !== apiProjectScopeKey() || activeWorkflow !== 'api_execution') return;
    apiExecutionPollTimer = setTimeout(() => pollApiMeterSphereExecution(executionId, requestId, capturedScopeKey), 5000);
  } finally {
    if (controller === apiExecutionPollController) apiExecutionPollController = null;
  }
}

async function startApiMeterSphereExecution(planId) {
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
    const data = await apiRequest('/api-testing/metersphere/executions', { method: 'POST', body: { plan_id: planId, test_plan_id: '' } });
    const execution = data.execution || {};
    apiExecutionStartingPlanId = '';
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
    showToast('✓ MeterSphere 执行已排队', 'success');
    apiExecutionPollRequestId += 1;
    scheduleApiExecutionPoll(execution, apiExecutionPollRequestId, apiProjectScopeKey());
  } catch (e) {
    apiExecutionStartingPlanId = '';
    showToast(e.message || 'MeterSphere 执行启动失败', 'error');
    await refreshApiExecutionContext(true);
  }
}

async function runApiPlanInMeterSphere(planId) {
  return startApiMeterSphereExecution(planId);
}

async function pushApiPlanToMeterSphere(planId) {
  try {
    await apiRequest('/api-testing/metersphere/push', { method: 'POST', body: { plan_id: planId } });
    showToast('✓ 已推送 MeterSphere', 'success');
    await refreshApiExecutionContext(true);
  } catch (e) {
    showToast(e.message || '推送失败', 'error');
  }
}

async function loadApiMeterSphereProjectEnvironments(projectId, intent = null) {
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

async function changeApiMeterSphereProject(projectId) {
  if (!projectId) {
    showToast('请选择 MeterSphere 业务', 'error');
    renderApiBusinessAuthInHeader();
    return;
  }
  const intent = beginApiExecutionBindingIntent(projectId);
  try {
    const environments = await loadApiMeterSphereProjectEnvironments(projectId, intent);
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

async function changeApiMeterSphereEnvironment(environmentId) {
  const projectId = document.querySelector('.api-execution-project-select')?.value || apiExecutionContext?.selection?.project_id || '';
  const intent = beginApiExecutionBindingIntent(projectId, environmentId);
  await saveApiSourceExecutionBinding(projectId, environmentId, intent);
}

async function updateApiMeterSphereSelection(selection) {
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
    showToast('请选择当前来源的 MeterSphere 业务和环境', 'error');
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

function openMeterSphereFromContext() {
  const url = apiExecutionContext?.connection?.base_url || '';
  if (!url) return openApiMeterSphereSettings();
  window.open(url, '_blank', 'noopener');
}

function openApiMeterSphereSettings() {
  apiExecutionSettingsOpen = true;
  const drawer = document.getElementById('api-ms-settings-drawer');
  const backdrop = document.getElementById('api-ms-settings-backdrop');
  if (drawer) {
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
  }
  if (backdrop) backdrop.hidden = false;
  renderApiMeterSphereSettings(apiExecutionContext || {});
}

function closeApiMeterSphereSettings() {
  apiExecutionSettingsOpen = false;
  const drawer = document.getElementById('api-ms-settings-drawer');
  const backdrop = document.getElementById('api-ms-settings-backdrop');
  if (drawer) {
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
  }
  if (backdrop) backdrop.hidden = true;
}

function renderApiMeterSphereSettings(context) {
  const target = document.getElementById('api-ms-settings-content');
  if (!target) return;
  const config = context.config || {};
  const selection = context.selection || {};
  const authMode = config.auth_mode === 'access_key' ? 'access_key' : 'token';
  target.innerHTML = `
    <div class="api-settings-head"><div><span>MeterSphere</span><h2>连接与执行设置</h2></div><button class="btn-sm icon-only" title="关闭设置" aria-label="关闭设置" onclick="closeApiMeterSphereSettings()">×</button></div>
    <div id="api-ms-status" class="generate-status"></div>
    <section class="api-settings-group"><h3>服务地址</h3><label><span>MeterSphere 地址</span><input id="api-ms-base-url" value="${escapeHtml(config.base_url || '')}" placeholder="https://metersphere.example.com"></label><label><span>连接检查路径</span><input id="api-ms-health-path" value="${escapeHtml(config.health_path || '/api/health')}"></label><button class="btn-sm" onclick="testApiMeterSphereHealth()">连接检查</button></section>
    <section class="api-settings-group"><h3>认证方式</h3><div class="api-ms-auth-mode"><label><input type="radio" name="api-ms-auth-mode" value="access_key" ${authMode === 'access_key' ? 'checked' : ''} onchange="syncApiMeterSphereAuthFields()"> Access Key</label><label><input type="radio" name="api-ms-auth-mode" value="token" ${authMode === 'token' ? 'checked' : ''} onchange="syncApiMeterSphereAuthFields()"> Token</label></div><div id="api-ms-auth-access" class="api-auth-fields"><label><span>Access Key</span><input id="api-ms-access-key" type="password" autocomplete="new-password" placeholder="${config.access_key_configured ? '已配置，留空保持' : '输入 Access Key'}"></label><label><span>Secret Key</span><input id="api-ms-secret-key" type="password" autocomplete="new-password" placeholder="${config.secret_key_configured ? '已配置，留空保持' : '输入 Secret Key'}"></label></div><div id="api-ms-auth-token" class="api-auth-fields"><label><span>Token</span><input id="api-ms-token" type="password" autocomplete="new-password" placeholder="${config.token_configured ? '已配置，留空保持' : '输入 Token'}"></label></div><button class="btn-sm danger ghost" onclick="clearApiMeterSphereAuth()">清除当前认证</button></section>
    <section class="api-settings-group"><h3>业务与环境</h3><label><span>Workspace ID</span><input id="api-ms-workspace" value="${escapeHtml(config.workspace_id || '')}"></label><label><span>业务</span><select id="api-ms-project">${apiSelectOptions(context.businesses, selection.project_id, '选择业务')}</select></label><label><span>环境</span><select id="api-ms-env">${apiSelectOptions(context.environments, selection.environment_id, '选择环境')}</select></label></section>
    <section class="api-settings-group"><h3>接口适配</h3><div class="api-settings-subhead">业务与环境读取</div><label><span>业务列表路径</span><input id="api-ms-project-list-path" value="${escapeHtml(config.project_list_path || '')}" placeholder="/project/list"></label><label><span>环境列表路径</span><input id="api-ms-environment-list-path" value="${escapeHtml(config.environment_list_path || '')}" placeholder="支持 {project_id}"></label><div class="api-settings-subhead">执行与报告</div><label><span>用例推送路径</span><input id="api-ms-case-path" value="${escapeHtml(config.case_push_path || '')}"></label><label><span>计划执行路径</span><input id="api-ms-run-path" value="${escapeHtml(config.plan_run_path || '')}"></label><label><span>运行状态路径</span><input id="api-ms-status-path" value="${escapeHtml(config.run_status_path || '')}" placeholder="支持 {run_id}"></label><label><span>报告查询路径</span><input id="api-ms-report-path" value="${escapeHtml(config.report_path || '')}" placeholder="支持 {run_id}"></label></section>
    <div class="api-settings-actions"><button class="btn-sm" onclick="closeApiMeterSphereSettings()">取消</button><button class="btn-sm primary" onclick="saveApiMeterSphereConfig()">保存并重新检查</button></div>
  `;
  syncApiMeterSphereAuthFields();
}

function syncApiMeterSphereAuthFields() {
  const mode = document.querySelector('input[name="api-ms-auth-mode"]:checked')?.value || 'token';
  document.getElementById('api-ms-auth-access')?.classList.toggle('hidden', mode !== 'access_key');
  document.getElementById('api-ms-auth-token')?.classList.toggle('hidden', mode !== 'token');
}

function collectApiMeterSphereConfig() {
  return {
    base_url: document.getElementById('api-ms-base-url')?.value.trim() || '',
    auth_mode: document.querySelector('input[name="api-ms-auth-mode"]:checked')?.value || 'token',
    token: document.getElementById('api-ms-token')?.value.trim() || '',
    access_key: document.getElementById('api-ms-access-key')?.value.trim() || '',
    secret_key: document.getElementById('api-ms-secret-key')?.value.trim() || '',
    workspace_id: document.getElementById('api-ms-workspace')?.value.trim() || '',
    project_id: document.getElementById('api-ms-project')?.value || '',
    environment_id: document.getElementById('api-ms-env')?.value || '',
    health_path: document.getElementById('api-ms-health-path')?.value.trim() || '/api/health',
    project_list_path: document.getElementById('api-ms-project-list-path')?.value.trim() || '',
    environment_list_path: document.getElementById('api-ms-environment-list-path')?.value.trim() || '',
    case_push_path: document.getElementById('api-ms-case-path')?.value.trim() || '',
    plan_run_path: document.getElementById('api-ms-run-path')?.value.trim() || '',
    run_status_path: document.getElementById('api-ms-status-path')?.value.trim() || '',
    report_path: document.getElementById('api-ms-report-path')?.value.trim() || ''
  };
}

async function saveApiMeterSphereConfig() {
  const status = document.getElementById('api-ms-status');
  try {
    await apiRequest('/api-testing/metersphere/config', { method: 'POST', body: collectApiMeterSphereConfig() });
    if (status) {
      status.className = 'generate-status show success';
      status.textContent = '配置已保存，正在重新检查连接和执行能力';
    }
    showToast('✓ MeterSphere 配置已保存', 'success');
    closeApiMeterSphereSettings();
    await refreshApiExecutionContext(true);
  } catch (e) {
    if (status) {
      status.className = 'generate-status show error';
      status.textContent = e.message || '保存失败';
    }
  }
}

async function clearApiMeterSphereAuth() {
  const mode = document.querySelector('input[name="api-ms-auth-mode"]:checked')?.value || 'token';
  if (!confirm(`确认清除当前 ${mode === 'access_key' ? 'Access Key / Secret Key' : 'Token'}？`)) return;
  const clearSecrets = mode === 'access_key' ? ['access_key', 'secret_key'] : ['token'];
  try {
    await apiRequest('/api-testing/metersphere/config', { method: 'POST', body: { clear_secrets: clearSecrets } });
    showToast('✓ 当前认证已清除', 'success');
    await refreshApiExecutionContext(true);
  } catch (e) {
    showToast(e.message || '清除认证失败', 'error');
  }
}

async function testApiMeterSphereHealth() {
  const status = document.getElementById('api-ms-status');
  try {
    const data = await apiRequest('/api-testing/metersphere/health', { method: 'POST' });
    if (status) {
      status.className = 'generate-status show success';
      status.textContent = `连接成功 · ${data.result?.elapsed_ms || 0}ms`;
    }
  } catch (e) {
    if (status) {
      status.className = 'generate-status show error';
      status.textContent = e.message || '连接失败';
    }
  }
}

async function showApiReportsPage() {
  const area = setApiTestingPage('api_reports', 'API 报告', '查看 MeterSphere 执行结果和接口失败归因。');
  if (!area) return;
  apiReportRequestController?.abort();
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
    const data = await apiRequest(`/api-testing/reports?${query.toString()}`, {signal: controller.signal});
    if (!apiReportResponseIsCurrent(controller, requestId, sourceId, scopeKey)) return;
    apiTestingReports = data.reports || [];
    area.innerHTML = `
      <div class="api-testing-page">
        <div class="generation-record-head">
          <div class="workflow-kicker">REPORT · API</div>
          <h2>API 报告</h2>
          <p>从 MeterSphere 回收执行结果后在这里统一归因。</p>
          <div class="generation-record-actions">
            <button class="btn-sm" onclick="showApiReportsPage()">刷新报告</button>
            <button class="btn-sm" onclick="showApiExecutionPage()">MeterSphere 执行</button>
          </div>
        </div>
        <section class="api-panel">
          ${apiTestingReports.length ? `<table class="report-table"><thead><tr><th>报告</th><th>状态</th><th>总数</th><th>通过</th><th>失败</th><th>时间</th></tr></thead><tbody>${apiTestingReports.map(row => `
            <tr><td>${escapeHtml(row.report_id || row.run_id || '-')}</td><td>${apiStatusPill(row.status, row.status === 'passed' ? 'success' : 'danger')}</td><td>${escapeHtml(row.total || 0)}</td><td>${escapeHtml(row.passed || 0)}</td><td>${escapeHtml(row.failed || 0)}</td><td>${escapeHtml(row.created_at || '-')}</td></tr>
          `).join('')}</tbody></table>` : apiTestingEmpty('暂无 API 报告。')}
        </section>
      </div>
    `;
  } catch(e) {
    if (!apiReportResponseIsCurrent(controller, requestId, sourceId, scopeKey)) return;
    area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty(e.message || 'API 报告读取失败')}</div>`;
  } finally {
    if (controller === apiReportRequestController) apiReportRequestController = null;
  }
}
