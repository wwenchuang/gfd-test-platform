// API testing workspace: OpenAPI assets -> AI plan drafts -> MeterSphere execution -> reports.

function setApiTestingPage(workflow, title, help) {
  if (workflow !== 'api_execution') stopApiExecutionPolling();
  if (workflow !== 'api_assets') stopApiAssetSyncPolling();
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

function renderApiSourceOptions(sources, selectedId) {
  if ((sources || []).length <= 1) return '';
  return `<select class="api-source-select" aria-label="API 来源" onchange="selectApiAssetSource(this.value)">${sources.map(source => `
    <option value="${escapeHtml(source.source_id || '')}" ${String(source.source_id || '') === String(selectedId || '') ? 'selected' : ''}>${escapeHtml(source.name || source.source_id || 'API 来源')}</option>
  `).join('')}</select>`;
}

function renderApiSourceSummary(source, latestSync, snapshot = {}) {
  const configured = source?.configured === true;
  const status = latestSync?.status || source?.last_sync_status || '';
  const syncDisabled = !configured || ['queued', 'running'].includes(status);
  return `
    <div class="api-source-status-row">
      <div class="api-source-identity">
        ${apiStatusPill(configured ? '连接已配置' : '待配置', configured ? 'success' : 'warn')}
        <strong>${escapeHtml(source?.name || 'Apifox 来源')}</strong>
        ${renderApiSourceOptions(apiTestingSources, source?.source_id)}
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
  return `
    <div class="api-source-settings-head"><div><span>APIFOX SOURCE</span><h3>只读同步设置</h3></div><button class="btn-sm icon-only" title="关闭设置" aria-label="关闭设置" onclick="toggleApiSourceSettings(false)">×</button></div>
    <div class="api-source-settings-grid">
      <label><span>来源名称</span><input id="api-source-name" value="${escapeHtml(source.name || 'Apifox 接口')}" placeholder="例如：3D 接口"></label>
      <label><span>项目 ID</span><input id="api-source-project-id" value="${escapeHtml(source.project_id || '')}" inputmode="numeric" placeholder="Apifox 项目设置中的 Project ID"></label>
      <label><span>分支 ID（可选）</span><input id="api-source-branch-id" value="${escapeHtml(source.branch_id || '')}" placeholder="默认主分支"></label>
      <label><span>环境 ID（可选）</span><input id="api-source-environment-id" value="${escapeHtml(source.environment_id || '')}" inputmode="numeric" placeholder="导出指定环境的服务地址"></label>
      <label><span>同步周期（分钟）</span><input id="api-source-interval" type="number" min="15" max="1440" step="15" value="${escapeHtml(source.sync_interval_minutes || 60)}"></label>
      <label class="api-source-token-field"><span>访问令牌（只写）</span><input id="api-source-token" type="password" value="" autocomplete="new-password" placeholder="${source.credential_configured ? '已配置；留空保持不变' : '输入 Apifox Access Token'}"></label>
      <label class="api-source-toggle"><input id="api-source-sync-enabled" type="checkbox" ${source.sync_enabled !== false ? 'checked' : ''}><span>启用定时同步</span></label>
    </div>
    <div class="api-source-settings-actions">
      ${source.credential_configured ? '<button class="btn-sm danger" onclick="clearApiSourceCredential()">清除当前令牌</button>' : ''}
      <button class="btn-sm primary" onclick="saveApiSourceConfig()">保存设置</button>
    </div>
  `;
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
    <section class="api-panel api-asset-endpoints">
      <div class="assets-table-head"><strong>接口列表</strong><span>${escapeHtml(endpoints.length)} 个接口</span></div>
      <div class="api-endpoint-scroll">${renderApiAssetTable(endpoints)}</div>
    </section>
  `;
}

async function refreshApiAssetWorkspace(force = false, requestedRevisionId = null) {
  const body = document.getElementById('api-assets-body');
  if (!body) return;
  const requestId = ++apiAssetContextRequestId;
  captureApiAssetSyncViewState(document.getElementById('editor-area'));
  try {
    const sourceData = await apiRequest(`/api-testing/sources${force ? '?limit=20' : ''}`);
    if (requestId !== apiAssetContextRequestId || activeWorkflow !== 'api_assets') return;
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
      ? `?snapshot_id=${encodeURIComponent(revisionId)}`
      : (source?.source_id ? `?source_id=${encodeURIComponent(source.source_id)}` : '');
    const assetData = await apiRequest(`/api-testing/assets${assetQuery}`);
    if (requestId !== apiAssetContextRequestId || activeWorkflow !== 'api_assets') return;
    if (!source && !apiAssetSettingsOpen) apiAssetSettingsOpen = true;
    apiTestingSnapshots = assetData.snapshots || [];
    apiTestingEndpoints = assetData.endpoints || [];
    apiAssetSelectedRevisionId = (assetData.snapshot || {}).revision_id || (assetData.snapshot || {}).snapshot_id || '';
    apiTestingCurrentSnapshotId = apiAssetSelectedRevisionId || apiTestingCurrentSnapshotId || (apiTestingSnapshots[0] || {}).snapshot_id || '';
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
    restoreApiAssetSyncViewState(document.getElementById('editor-area'));
    scheduleApiAssetSyncPoll(latestSync);
  } catch(e) {
    body.innerHTML = apiTestingEmpty(e.message || '接口资产读取失败');
  }
}

async function refreshApiAssetsBody() {
  return refreshApiAssetWorkspace(true);
}

function toggleApiSourceSettings(open = null) {
  apiAssetSettingsOpen = open === null ? !apiAssetSettingsOpen : !!open;
  const panel = document.getElementById('api-source-settings-panel');
  if (panel) panel.hidden = !apiAssetSettingsOpen;
}

async function selectApiAssetSource(sourceId) {
  apiAssetSelectedSourceId = sourceId || '';
  apiAssetSelectedRevisionId = '';
  apiAssetRevisionPinned = false;
  apiAssetActiveSyncId = '';
  await refreshApiAssetWorkspace(true);
}

async function selectApiAssetRevision(revisionId) {
  apiAssetSelectedRevisionId = revisionId || '';
  apiAssetRevisionPinned = !!apiAssetSelectedRevisionId;
  await refreshApiAssetWorkspace(true, apiAssetSelectedRevisionId);
}

async function saveApiSourceConfig(clearCredentials = false) {
  const source = selectedApiAssetSource() || {};
  const token = document.getElementById('api-source-token')?.value.trim() || '';
  const payload = {
    source_id: source.source_id || undefined,
    source_type: 'apifox',
    name: document.getElementById('api-source-name')?.value.trim() || 'Apifox 接口',
    project_id: document.getElementById('api-source-project-id')?.value.trim() || '',
    branch_id: document.getElementById('api-source-branch-id')?.value.trim() || '',
    environment_id: document.getElementById('api-source-environment-id')?.value.trim() || '',
    sync_interval_minutes: Number(document.getElementById('api-source-interval')?.value || 60),
    sync_enabled: !!document.getElementById('api-source-sync-enabled')?.checked,
    clear_credentials: !!clearCredentials
  };
  if (token) payload.access_token = token;
  try {
    const data = await apiRequest('/api-testing/sources', { method: 'POST', body: payload });
    apiAssetSelectedSourceId = data.source?.source_id || apiAssetSelectedSourceId;
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
            <div class="api-endpoint-scroll">${renderApiAssetTable(apiTestingEndpoints)}</div>
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

function stopApiExecutionPolling() {
  if (apiExecutionPollTimer) clearTimeout(apiExecutionPollTimer);
  apiExecutionPollTimer = null;
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
  stopApiExecutionPolling();
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
  const requestId = ++apiExecutionContextRequestId;
  try {
    const data = await apiRequest(`/api-testing/metersphere/execution-context${force ? '?force=1' : ''}`);
    if (requestId !== apiExecutionContextRequestId || activeWorkflow !== 'api_execution') return;
    apiExecutionContext = data;
    apiTestingPlans = data.plans || [];
    const active = (data.active_runs || [])[0] || null;
    apiExecutionActiveId = active?.execution_id || '';
    renderApiExecutionDynamic(data, active);
    if (active && !apiExecutionTerminal(active)) scheduleApiExecutionPoll(active);
    else stopApiExecutionPolling();
  } catch (e) {
    const header = document.getElementById('api-execution-header');
    if (header) header.innerHTML = `<div class="api-inline-error">${escapeHtml(e.message || 'MeterSphere 执行上下文读取失败')}</div>`;
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
      <label><span>业务</span><select onchange="changeApiMeterSphereProject(this.value)">${apiSelectOptions(context.businesses, selection.project_id, '选择业务')}</select></label>
      <label><span>环境</span><select onchange="changeApiMeterSphereEnvironment(this.value)" ${selection.project_id ? '' : 'disabled'}>${apiSelectOptions(context.environments, selection.environment_id, '选择环境')}</select></label>
      <div class="api-readiness-fact">
        <span>${metadata.stale ? '过期缓存，仅供查看' : '实时数据'}</span>
        <strong>${escapeHtml(readiness.primary_action || '-')}</strong>
      </div>
    </div>
    ${missing.length ? `<div class="api-readiness-missing"><strong>还缺：</strong>${missing.map(item => `<span>${escapeHtml(item)}</span>`).join('')}</div>` : ''}
    ${metadata.stale ? `<div class="api-stale-warning">业务或环境来自过期缓存。完成一次实时校验前，执行按钮保持禁用。</div>` : ''}
  `;
}

function apiExecutionEmptyAction(context) {
  const reason = context.empty_reason || '';
  if (reason === 'no_assets') return { text: '尚未导入接口', action: '去导入接口', handler: 'showApiAssetsPage()' };
  if (reason === 'no_plans') return { text: '尚未生成 API 用例计划', action: '去生成计划', handler: 'showApiPlanPage()' };
  if (reason === 'unconfirmed_plans') return { text: '有待确认计划', action: '去确认计划', handler: 'showApiPlanPage()' };
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
    const starting = String(apiExecutionStartingPlanId || '') === String(plan.plan_id || '');
    const disabled = starting || metadata.stale || readiness.can_execute !== true || plan.can_execute !== true;
    const passRate = latest.stats?.total ? `${Math.round((latest.stats.passed || 0) * 100 / latest.stats.total)}%` : '-';
    const disabledReason = starting ? '正在创建执行' : (metadata.stale ? '元数据已过期' : ((readiness.missing || [])[0] || (plan.active_run ? '当前计划正在执行' : '暂不可执行')));
    return `
      <article class="api-execution-plan-row">
        <div class="api-plan-identity">
          <strong>${escapeHtml(plan.name || plan.plan_id)}</strong>
          <span>${escapeHtml(plan.endpoint_count || 0)} 个接口 · ${escapeHtml(plan.case_count || 0)} 条用例 · 确认于 ${escapeHtml(plan.confirmed_at || '-')}</span>
        </div>
        <div class="api-plan-binding"><span>MeterSphere 计划</span><strong>${escapeHtml(plan.test_plan_name || plan.test_plan_id || '首次执行时创建或选择')}</strong></div>
        <div class="api-plan-latest"><span>最近运行</span><strong>${escapeHtml(apiExecutionStateText(latest.status))} · 通过率 ${escapeHtml(passRate)}</strong><small>${escapeHtml(latest.started_at || latest.created_at || '暂无历史')} · 耗时 ${escapeHtml(apiDurationText(latest.duration_seconds))}</small></div>
        <div class="api-plan-actions">
          <button class="btn-sm primary" onclick="startApiMeterSphereExecution(${jsArg(plan.plan_id)})" ${disabled ? 'disabled' : ''} title="${escapeHtml(disabled ? disabledReason : '推送确认用例并执行')}">推送并执行</button>
          <details class="api-plan-menu"><summary title="更多操作" aria-label="更多操作">⋯</summary><div>
            <button onclick="pushApiPlanToMeterSphere(${jsArg(plan.plan_id)})">仅推送</button>
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
  // Stable key uses runId + eventId so polling refresh keeps expanded logs open.
  return `${runId || 'run'}::${eventId || 'event'}`;
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

function scheduleApiExecutionPoll(execution) {
  stopApiExecutionPolling();
  if (!execution?.execution_id || apiExecutionTerminal(execution) || activeWorkflow !== 'api_execution') return;
  const delay = Math.max(1000, Number(execution.poll_after_ms || 3000));
  apiExecutionPollTimer = setTimeout(() => pollApiMeterSphereExecution(execution.execution_id), delay);
}

async function pollApiMeterSphereExecution(executionId) {
  if (activeWorkflow !== 'api_execution' || executionId !== apiExecutionActiveId) return;
  try {
    const data = await apiRequest(`/api-testing/metersphere/executions/${encodeURIComponent(executionId)}`);
    const execution = data.execution || {};
    const active = document.getElementById('api-active-run');
    captureApiExecutionLogViewState(active);
    if (active) {
      active.hidden = false;
      active.innerHTML = renderApiActiveRun(execution);
    }
    restoreApiExecutionLogViewState(active);
    if (apiExecutionTerminal(execution)) await refreshApiExecutionContext(true);
    else scheduleApiExecutionPoll(execution);
  } catch (e) {
    apiExecutionPollTimer = setTimeout(() => pollApiMeterSphereExecution(executionId), 5000);
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
    scheduleApiExecutionPoll(execution);
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

async function changeApiMeterSphereProject(projectId) {
  await updateApiMeterSphereSelection({ project_id: projectId, environment_id: '' });
}

async function changeApiMeterSphereEnvironment(environmentId) {
  await updateApiMeterSphereSelection({ environment_id: environmentId });
}

async function updateApiMeterSphereSelection(selection) {
  try {
    await apiRequest('/api-testing/metersphere/config', { method: 'POST', body: selection });
    await refreshApiExecutionContext(true);
  } catch (e) {
    showToast(e.message || '业务或环境保存失败', 'error');
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
