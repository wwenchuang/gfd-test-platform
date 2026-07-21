// API testing workspace: OpenAPI assets -> AI plan drafts -> MeterSphere execution -> reports.

function setApiTestingPage(workflow, title, help) {
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
          <p>第一阶段使用 Apifox 导出的 OpenAPI JSON 建资产，确认用例计划后再进入 MeterSphere。</p>
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

function renderApiAssetTable(endpoints) {
  if (!endpoints.length) return apiTestingEmpty('暂无接口资产。');
  return `
    <table class="assets-table api-endpoint-table">
      <thead><tr><th><input type="checkbox" onchange="toggleApiEndpointSelection(this.checked)" checked></th><th>接口</th><th>模块</th><th>名称</th><th>必填</th><th>Schema</th></tr></thead>
      <tbody>${endpoints.map(endpoint => `
        <tr>
          <td><input class="api-endpoint-check" type="checkbox" value="${escapeHtml(endpoint.endpoint_id || '')}" checked></td>
          <td><strong>${escapeHtml(apiEndpointLabel(endpoint))}</strong></td>
          <td>${escapeHtml(endpoint.module || '-')}</td>
          <td>${escapeHtml(endpoint.name || '-')}</td>
          <td>${escapeHtml((endpoint.required_fields || []).join('、') || '-')}</td>
          <td><code>${escapeHtml(endpoint.schema_hash || '-')}</code></td>
        </tr>
      `).join('')}</tbody>
    </table>
  `;
}

function toggleApiEndpointSelection(checked) {
  document.querySelectorAll('.api-endpoint-check').forEach(input => input.checked = !!checked);
}

async function showApiAssetsPage() {
  const area = setApiTestingPage('api_assets', '接口资产', '上传 Apifox OpenAPI JSON 并查看接口快照。');
  if (!area) return;
  area.innerHTML = `
    <div class="api-testing-page">
      <div class="generation-record-head">
        <div class="workflow-kicker">API ASSET · OpenAPI</div>
        <h2>接口资产</h2>
        <p>从 Apifox 导出 OpenAPI JSON 后上传，平台会生成接口快照。</p>
        <div class="api-upload-row">
          <input id="api-openapi-name" placeholder="快照名称">
          <input id="api-openapi-file" type="file" accept=".json,application/json" onchange="handleApiOpenApiFile(this)">
          <button class="btn-sm" onclick="showApiAssetsPage()">刷新</button>
        </div>
        <div id="api-assets-status" class="generate-status"></div>
      </div>
      <div id="api-assets-body">${apiTestingEmpty('正在读取接口资产...')}</div>
    </div>
  `;
  await refreshApiAssetsBody();
}

async function refreshApiAssetsBody() {
  const body = document.getElementById('api-assets-body');
  if (!body) return;
  try {
    const data = await apiRequest(`/api-testing/assets${apiTestingCurrentSnapshotId ? `?snapshot_id=${encodeURIComponent(apiTestingCurrentSnapshotId)}` : ''}`);
    apiTestingSnapshots = data.snapshots || [];
    apiTestingEndpoints = data.endpoints || [];
    apiTestingCurrentSnapshotId = (data.snapshot || {}).snapshot_id || apiTestingCurrentSnapshotId || (apiTestingSnapshots[0] || {}).snapshot_id || '';
    body.innerHTML = `
      <div class="api-panel">
        <div class="assets-table-head"><strong>接口列表</strong><span>${escapeHtml(apiTestingEndpoints.length)} 个接口</span></div>
        ${renderApiAssetTable(apiTestingEndpoints)}
      </div>
    `;
  } catch(e) {
    body.innerHTML = apiTestingEmpty(e.message || '接口资产读取失败');
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
    if (status) {
      status.className = 'generate-status show success';
      status.textContent = `已导入 ${(data.endpoints || []).length} 个接口`;
    }
    showToast('✓ OpenAPI 已导入', 'success');
    await refreshApiAssetsBody();
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

async function showApiPlanPage() {
  const area = setApiTestingPage('api_plan', 'AI 用例计划', '生成 API 用例草稿，确认后才能推送 MeterSphere。');
  if (!area) return;
  area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty('正在读取接口资产和计划...')}</div>`;
  try {
    const [assets, plans] = await Promise.all([
      apiRequest(`/api-testing/assets${apiTestingCurrentSnapshotId ? `?snapshot_id=${encodeURIComponent(apiTestingCurrentSnapshotId)}` : ''}`),
      apiRequest('/api-testing/plans')
    ]);
    apiTestingEndpoints = assets.endpoints || [];
    apiTestingCurrentSnapshotId = (assets.snapshot || {}).snapshot_id || apiTestingCurrentSnapshotId || ((assets.snapshots || [])[0] || {}).snapshot_id || '';
    apiTestingPlans = plans.plans || [];
    area.innerHTML = `
      <div class="api-testing-page">
        <div class="generation-record-head">
          <div class="workflow-kicker">AI PLAN · API Cases</div>
          <h2>AI 用例计划</h2>
          <p>默认生成草稿，确认后才能进入 MeterSphere。</p>
          <div class="generation-record-actions">
            <button class="btn-sm ai" onclick="generateApiTestPlan()">生成计划草稿</button>
            <button class="btn-sm" onclick="showApiAssetsPage()">接口资产</button>
          </div>
        </div>
        <div class="api-two-column">
          <section class="api-panel">
            <h3>选择接口</h3>
            ${renderApiAssetTable(apiTestingEndpoints)}
          </section>
          <section class="api-panel" id="api-plan-result">
            <h3>最近计划</h3>
            ${renderApiPlanList(apiTestingPlans)}
          </section>
        </div>
      </div>
    `;
  } catch(e) {
    area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty(e.message || 'API 用例计划读取失败')}</div>`;
  }
}

function renderApiPlanList(plans) {
  if (!plans.length) return apiTestingEmpty('暂无计划草稿。');
  return `<div class="api-list">${plans.map(plan => `
    <div class="api-list-row">
      <div><strong>${escapeHtml(plan.name || plan.plan_id)}</strong><small>${escapeHtml(plan.created_at || '')}</small></div>
      <span>${apiStatusPill(apiPlanStatusText(plan.status), plan.status === 'confirmed' ? 'success' : 'warn')} ${escapeHtml(plan.case_count || 0)} 条</span>
    </div>
  `).join('')}</div>`;
}

function renderApiPlanDetail(plan) {
  const cases = plan.cases || [];
  return `
    <h3>${escapeHtml(plan.name || 'API 用例计划')}</h3>
    <div class="review-stats compact">
      <div class="review-stat"><strong>${escapeHtml(plan.endpoint_count || 0)}</strong><span>接口</span></div>
      <div class="review-stat"><strong>${escapeHtml(plan.case_count || cases.length)}</strong><span>用例</span></div>
      <div class="review-stat"><strong>${escapeHtml(apiPlanStatusText(plan.status))}</strong><span>状态</span></div>
    </div>
    <div class="generation-record-actions">
      <button class="btn-sm success" onclick="confirmApiTestPlan(${jsArg(plan.plan_id)})">确认计划</button>
      <button class="btn-sm" onclick="showApiExecutionPage()">去执行</button>
    </div>
    <table class="assets-table api-case-table">
      <thead><tr><th>用例</th><th>类型</th><th>优先级</th><th>接口</th><th>断言</th></tr></thead>
      <tbody>${cases.map(item => `
        <tr><td>${escapeHtml(item.name || '-')}</td><td>${escapeHtml(item.type || '-')}</td><td>${escapeHtml(item.priority || '-')}</td><td>${escapeHtml(item.endpoint || '-')}</td><td>${escapeHtml((item.assertions || []).join('；'))}</td></tr>
      `).join('')}</tbody>
    </table>
  `;
}

async function generateApiTestPlan() {
  const target = document.getElementById('api-plan-result');
  if (!apiTestingCurrentSnapshotId) {
    showToast('请先导入 OpenAPI 接口资产', 'error');
    return;
  }
  const endpointIds = apiSelectedEndpointIds();
  if (target) target.innerHTML = `<h3>生成中</h3>${apiTestingEmpty('正在生成 API 用例计划草稿...')}`;
  try {
    const data = await apiRequest('/api-testing/plans/generate', {
      method: 'POST',
      timeoutMs: 180000,
      body: { snapshot_id: apiTestingCurrentSnapshotId, endpoint_ids: endpointIds, use_ai: false }
    });
    apiTestingCurrentPlan = data.plan || null;
    if (target) target.innerHTML = renderApiPlanDetail(apiTestingCurrentPlan || {});
    showToast('✓ API 用例计划草稿已生成', 'success');
  } catch(e) {
    if (target) target.innerHTML = `<h3>生成失败</h3>${apiTestingEmpty(e.message || '生成失败')}`;
    showToast(e.message || '生成失败', 'error');
  }
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

async function showApiExecutionPage() {
  const area = setApiTestingPage('api_execution', 'MeterSphere 执行', '配置 MeterSphere，推送确认后的 API 用例计划并触发执行。');
  if (!area) return;
  area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty('正在读取 MeterSphere 配置...')}</div>`;
  try {
    const [configData, plansData] = await Promise.all([
      apiRequest('/api-testing/metersphere/config'),
      apiRequest('/api-testing/plans')
    ]);
    const config = configData.config || {};
    apiTestingPlans = plansData.plans || [];
    area.innerHTML = `
      <div class="api-testing-page">
        <div class="generation-record-head">
          <div class="workflow-kicker">EXECUTE · MeterSphere</div>
          <h2>MeterSphere 执行</h2>
          <p>账号密码不进入代码；这里保存服务端连接配置和可选 API 路径。</p>
        </div>
        <div class="api-two-column">
          <section class="api-panel">
            <h3>连接配置</h3>
            <div class="api-form-grid">
              <input id="api-ms-base-url" placeholder="MeterSphere 地址" value="${escapeHtml(config.base_url || '')}">
              <input id="api-ms-token" placeholder="Token / Access Token" value="${escapeHtml(config.token || '')}">
              <input id="api-ms-workspace" placeholder="Workspace ID" value="${escapeHtml(config.workspace_id || '')}">
              <input id="api-ms-project" placeholder="Project ID" value="${escapeHtml(config.project_id || '')}">
              <input id="api-ms-env" placeholder="Environment ID" value="${escapeHtml(config.environment_id || '')}">
              <input id="api-ms-health-path" placeholder="Health Path" value="${escapeHtml(config.health_path || '/api/health')}">
              <input id="api-ms-case-path" placeholder="Case Push Path" value="${escapeHtml(config.case_push_path || '')}">
              <input id="api-ms-run-path" placeholder="Plan Run Path" value="${escapeHtml(config.plan_run_path || '')}">
              <input id="api-ms-report-path" placeholder="Report Path，支持 {run_id}" value="${escapeHtml(config.report_path || '')}">
            </div>
            <div class="generation-record-actions">
              <button class="btn-sm primary" onclick="saveApiMeterSphereConfig()">保存配置</button>
              <button class="btn-sm" onclick="testApiMeterSphereHealth()">连接检查</button>
            </div>
            <div id="api-ms-status" class="generate-status"></div>
          </section>
          <section class="api-panel">
            <h3>计划推送与执行</h3>
            ${renderApiExecutionPlans(apiTestingPlans)}
            <div id="api-execution-log">${renderApiExecutionLogRows([])}</div>
          </section>
        </div>
      </div>
    `;
  } catch(e) {
    area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty(e.message || 'MeterSphere 执行页读取失败')}</div>`;
  }
}

function renderApiExecutionPlans(plans) {
  const confirmed = (plans || []).filter(plan => plan.status === 'confirmed');
  if (!confirmed.length) return apiTestingEmpty('暂无已确认计划。');
  return `<div class="api-list">${confirmed.map(plan => `
    <div class="api-list-row">
      <div><strong>${escapeHtml(plan.name || plan.plan_id)}</strong><small>${escapeHtml(plan.case_count || 0)} 条用例</small></div>
      <span>
        <button class="btn-sm" onclick="pushApiPlanToMeterSphere(${jsArg(plan.plan_id)})">推送</button>
        <button class="btn-sm success" onclick="runApiPlanInMeterSphere(${jsArg(plan.plan_id)})">执行</button>
      </span>
    </div>
  `).join('')}</div>`;
}

function collectApiMeterSphereConfig() {
  return {
    base_url: document.getElementById('api-ms-base-url')?.value.trim() || '',
    token: document.getElementById('api-ms-token')?.value.trim() || '',
    workspace_id: document.getElementById('api-ms-workspace')?.value.trim() || '',
    project_id: document.getElementById('api-ms-project')?.value.trim() || '',
    environment_id: document.getElementById('api-ms-env')?.value.trim() || '',
    health_path: document.getElementById('api-ms-health-path')?.value.trim() || '/api/health',
    case_push_path: document.getElementById('api-ms-case-path')?.value.trim() || '',
    plan_run_path: document.getElementById('api-ms-run-path')?.value.trim() || '',
    report_path: document.getElementById('api-ms-report-path')?.value.trim() || ''
  };
}

async function saveApiMeterSphereConfig() {
  const status = document.getElementById('api-ms-status');
  try {
    const data = await apiRequest('/api-testing/metersphere/config', { method: 'POST', body: collectApiMeterSphereConfig() });
    if (status) {
      status.className = 'generate-status show success';
      status.textContent = `已保存配置，token ${data.config?.token_configured ? '已配置' : '未配置'}`;
    }
    showToast('✓ MeterSphere 配置已保存', 'success');
  } catch(e) {
    if (status) {
      status.className = 'generate-status show error';
      status.textContent = e.message || '保存失败';
    }
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
  } catch(e) {
    if (status) {
      status.className = 'generate-status show error';
      status.textContent = e.message || '连接失败';
    }
  }
}

function apiExecutionLogKey(runId, stepId) {
  // Stable key uses runId + stepId so polling refresh keeps expanded logs open.
  return `${runId || 'run'}::${stepId || 'step'}`;
}

function toggleApiExecutionLog(runId, stepId, open) {
  const key = apiExecutionLogKey(runId, stepId);
  if (open) apiLogExpandedKeys.add(key);
  else apiLogExpandedKeys.delete(key);
  localStorage.setItem('api_log_expanded_keys', JSON.stringify(Array.from(apiLogExpandedKeys)));
}

function renderApiExecutionLogRows(rows) {
  const logRows = rows.length ? rows : [
    { runId: 'local', stepId: 'config', title: '连接配置', detail: '等待保存 MeterSphere 配置' }
  ];
  return `<div class="api-tech-log"><h3>技术日志</h3>${logRows.map(row => {
    const key = apiExecutionLogKey(row.runId, row.stepId);
    const open = apiLogExpandedKeys.has(key);
    return `
      <details class="api-log-detail" ${open ? 'open' : ''} ontoggle="toggleApiExecutionLog(${jsArg(row.runId)}, ${jsArg(row.stepId)}, this.open)">
        <summary><span>${escapeHtml(row.title || row.stepId)}</span><small>${escapeHtml(row.runId || '')}</small></summary>
        <pre>${escapeHtml(row.detail || row.error || JSON.stringify(row, null, 2))}</pre>
      </details>
    `;
  }).join('')}</div>`;
}

async function pushApiPlanToMeterSphere(planId) {
  const target = document.getElementById('api-execution-log');
  try {
    const data = await apiRequest('/api-testing/metersphere/push', { method: 'POST', body: { plan_id: planId } });
    if (target) target.innerHTML = renderApiExecutionLogRows([{ runId: data.result?.push_id || planId, stepId: 'push', title: '推送 MeterSphere', detail: JSON.stringify(data.result || {}, null, 2) }]);
    showToast('✓ 已推送 MeterSphere', 'success');
  } catch(e) {
    if (target) target.innerHTML = renderApiExecutionLogRows([{ runId: planId, stepId: 'push_error', title: '推送失败', error: e.message }]);
    showToast(e.message || '推送失败', 'error');
  }
}

async function runApiPlanInMeterSphere(planId) {
  const target = document.getElementById('api-execution-log');
  try {
    const data = await apiRequest('/api-testing/metersphere/run', { method: 'POST', body: { plan_id: planId } });
    if (target) target.innerHTML = renderApiExecutionLogRows([{ runId: data.result?.run_id || planId, stepId: 'run', title: '触发执行', detail: JSON.stringify(data.result || {}, null, 2) }]);
    showToast('✓ MeterSphere 执行已触发', 'success');
  } catch(e) {
    if (target) target.innerHTML = renderApiExecutionLogRows([{ runId: planId, stepId: 'run_error', title: '执行失败', error: e.message }]);
    showToast(e.message || '执行失败', 'error');
  }
}

async function showApiReportsPage() {
  const area = setApiTestingPage('api_reports', 'API 报告', '查看 MeterSphere 执行结果和接口失败归因。');
  if (!area) return;
  area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty('正在读取 API 报告...')}</div>`;
  try {
    const data = await apiRequest('/api-testing/reports');
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
    area.innerHTML = `<div class="api-testing-page">${apiTestingEmpty(e.message || 'API 报告读取失败')}</div>`;
  }
}
