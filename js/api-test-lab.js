// One-page API test lab.  Apifox is only a manual source update; daily testing
// runs from the local SQLite-backed test library.

(function () {
  let apiLabData = null;
  let apiLabLoading = false;
  let apiLabSelectedSourceId = localStorage.getItem('api_lab_source_id') || '';
  let apiLabSelectedModulePath = localStorage.getItem('api_lab_module_path') || '';
  let apiLabSelectedEndpointIds = new Set();
  let apiLabActiveTab = localStorage.getItem('api_lab_active_tab') || 'log';
  let apiLabEditingEnvironment = false;
  let apiLabPollTimer = null;
  let apiLabSourceForm = {};
  let apiLabDiscoveredProjects = [];
  let apiLabDiscoveredContext = {};

  function html(value) {
    return typeof escapeHtml === 'function'
      ? escapeHtml(value)
      : String(value ?? '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
  }

  function js(value) {
    return typeof apiJsString === 'function'
      ? apiJsString(value)
      : String(value ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n').replace(/\r/g, '');
  }

  function toast(message, type = 'success') {
    if (typeof showToast === 'function') showToast(message, type);
  }

  function methodTone(method = '') {
    const value = String(method || '').toUpperCase();
    if (value === 'GET') return 'get';
    if (value === 'POST') return 'post';
    if (value === 'PUT' || value === 'PATCH') return 'put';
    if (value === 'DELETE') return 'delete';
    return 'other';
  }

  async function apiLabRequest(path, options = {}) {
    const payload = await apiRequest(`/test-lab${path}`, options);
    if (payload.ok === false) throw new Error(payload.error || '请求失败');
    return payload;
  }

  function apiLabSetPage(title, help = '') {
    const area = typeof setApiTestingPage === 'function'
      ? setApiTestingPage('api_dashboard', title, help)
      : document.getElementById('editor-area');
    if (area) area.className = 'editor-area api-testing-area api-lab-area';
    return area;
  }

  function apiLabCacheKey(sourceId = apiLabSelectedSourceId) {
    return `api_lab_state_${sourceId || 'default'}`;
  }

  function apiLabCacheState(data) {
    try {
      localStorage.setItem(apiLabCacheKey(data?.source?.source_id || apiLabSelectedSourceId), JSON.stringify({
        saved_at: Date.now(),
        data,
      }));
    } catch (_) {}
  }

  function apiLabReadCache() {
    try {
      const raw = localStorage.getItem(apiLabCacheKey());
      const parsed = raw ? JSON.parse(raw) : {};
      return parsed && parsed.data ? parsed.data : null;
    } catch (_) {
      return null;
    }
  }

  async function apiLabLoad({modulePath = apiLabSelectedModulePath, sourceId = apiLabSelectedSourceId, silent = false, executionId = ''} = {}) {
    const area = apiLabSetPage('API 测试', '从本地测试库选择接口，AI 生成用例，然后执行并实时查看日志和报告。');
    if (!area) return;
    if (!silent) {
      const cached = apiLabReadCache();
      if (cached) renderApiLabDashboard(cached, {cached: true});
      else area.innerHTML = `<div class="api-lab-loading">正在读取本地测试库...</div>`;
    }
    apiLabLoading = true;
    try {
      const params = new URLSearchParams();
      if (sourceId) params.set('source_id', sourceId);
      if (modulePath) params.set('module_path', modulePath);
      if (executionId) params.set('execution_id', executionId);
      const data = await apiLabRequest(`/state${params.toString() ? `?${params}` : ''}`);
      apiLabData = data;
      apiLabSelectedSourceId = data.source?.source_id || sourceId || '';
      apiLabSelectedModulePath = data.selected_module_path || modulePath || '';
      if (apiLabSelectedSourceId) localStorage.setItem('api_lab_source_id', apiLabSelectedSourceId);
      if (apiLabSelectedModulePath) localStorage.setItem('api_lab_module_path', apiLabSelectedModulePath);
      apiLabCacheState(data);
      renderApiLabDashboard(data);
      apiLabScheduleRunPolling(data);
      return data;
    } catch (error) {
      if (!silent) area.innerHTML = apiLabErrorState(error.message || error);
      else toast(error.message || String(error), 'error');
      return null;
    } finally {
      apiLabLoading = false;
    }
  }

  function apiLabErrorState(message) {
    return `
      <div class="api-lab-shell">
        <section class="api-lab-empty">
          <strong>${html(message || '测试库读取失败')}</strong>
          <span>可以先进入「接口来源」重新保存 Apifox 项目，或者刷新页面后重试。</span>
          <div class="api-lab-actions">
            <button class="btn-sm primary" onclick="showApiAssetsPage()">配置接口来源</button>
            <button class="btn-sm" onclick="showApiTestingDashboard()">重试</button>
          </div>
        </section>
      </div>
    `;
  }

  function renderApiLabDashboard(data = {}, options = {}) {
    apiLabData = data;
    const area = apiLabSetPage('API 测试', '手动更新 Apifox 后保存到本地测试库；之后只按环境、模块和接口执行。');
    if (!area) return;
    const source = data.source || {};
    const modules = data.modules || [];
    const selectedModule = modules.find(item => item.module_path === data.selected_module_path) || modules[0] || {};
    const endpointCount = Number(data.endpoint_count || source.endpoint_count || 0);
    const selectedCount = apiLabSelectedEndpointIds.size || Number(data.selected_endpoint_count || (data.endpoints || []).length || 0);
    area.innerHTML = `
      <div class="api-lab-shell">
        ${renderApiLabHeader(data, options)}
        <div class="api-lab-layout">
          <aside class="api-lab-sidebar">
            ${renderApiLabEnvironmentCard(data)}
            ${renderApiLabModulePicker(data)}
            ${renderApiLabCommandPanel(data, selectedModule, selectedCount)}
            ${renderApiLabHistory(data)}
          </aside>
          <main class="api-lab-main">
            ${renderApiLabScopePanel(data, selectedModule, endpointCount)}
            ${renderApiLabConsole(data)}
          </main>
        </div>
      </div>
    `;
  }

  function renderApiLabHeader(data = {}, options = {}) {
    const source = data.source || {};
    const env = data.environment || {};
    const baseUrl = env.base_urls?.[0]?.base_url || '';
    const status = source.source_id ? '本地测试库已就绪' : '请先保存接口来源';
    return `
      <section class="api-lab-hero">
        <div>
          <span class="api-lab-kicker">API 自动化</span>
          <h2>接口测试台</h2>
          <p>获取 Apifox 数据只做手动更新；测试、日志和报告都从平台本地测试库执行。</p>
        </div>
        <div class="api-lab-hero-metrics">
          <div><span>来源</span><strong>${html(source.name || '未配置')}</strong></div>
          <div><span>接口</span><strong>${Number(data.endpoint_count || source.endpoint_count || 0)}</strong></div>
          <div><span>环境</span><strong>${html(env.base_urls?.[0]?.name || source.environment_name || '未配置')}</strong></div>
          <div><span>状态</span><strong>${html(options.cached ? '本地缓存' : status)}</strong></div>
        </div>
        <div class="api-lab-hero-actions">
          <button class="btn-sm" onclick="showApiAssetsPage()">接口来源</button>
          <button class="btn-sm primary" onclick="apiLabRefreshApifox()" ${source.source_id ? '' : 'disabled'}>更新 Apifox</button>
        </div>
        ${baseUrl ? `<div class="api-lab-base-url">${html(baseUrl)}</div>` : ''}
      </section>
    `;
  }

  function renderApiLabEnvironmentCard(data = {}) {
    const source = data.source || {};
    const env = data.environment || {};
    const baseUrl = env.base_urls?.[0]?.base_url || '';
    const variables = (env.variables || []).filter(item => !item.group_placeholder);
    const authHeader = env.auth?.header_name || (variables.some(item => item.name === 'ZXBToken') ? 'ZXBToken' : 'Authorization');
    const authReady = !!env.auth?.configured || variables.some(item => item.name === authHeader && item.configured);
    if (apiLabEditingEnvironment) return renderApiLabEnvironmentEditor(data);
    return `
      <section class="api-lab-card">
        <div class="api-lab-card-head">
          <span class="api-lab-icon">🧪</span>
          <div><strong>运行环境</strong><small>${html(env.base_urls?.[0]?.name || source.environment_name || '未选择')}</small></div>
          <button class="btn-sm" onclick="apiLabToggleEnvironmentEditor(true)">编辑环境</button>
        </div>
        <div class="api-lab-env-line"><span>Base URL</span><b>${html(baseUrl || '未配置')}</b></div>
        <div class="api-lab-env-line"><span>业务鉴权</span><b class="${authReady ? 'ok' : 'warn'}">${authReady ? `已配置（${html(authHeader)}）` : `未配置（${html(authHeader)}）`}</b></div>
        <div class="api-lab-env-vars">
          ${variables.slice(0, 12).map(item => `
            <span title="${html(item.name)}">${html(item.name)}：${html(item.value_preview || (item.configured ? '已配置' : '空'))}</span>
          `).join('') || '<span>暂无环境变量</span>'}
          ${variables.length > 12 ? `<span>还有 ${variables.length - 12} 个，点编辑查看</span>` : ''}
        </div>
      </section>
    `;
  }

  function renderApiLabEnvironmentEditor(data = {}) {
    const env = data.environment || {};
    const variables = (env.variables || []).filter(item => !item.group_placeholder);
    const baseUrl = env.base_urls?.[0]?.base_url || '';
    const biz = variables.find(item => item.name === 'Biz')?.value_preview || '';
    const authHeader = env.auth?.header_name || (variables.some(item => item.name === 'ZXBToken') ? 'ZXBToken' : 'Authorization');
    const authOptions = Array.from(new Set([
      authHeader,
      'Authorization',
      'ZXBToken',
      ...variables
        .filter(item => /token|auth|authorization|api[-_]?key/i.test(item.name || ''))
        .map(item => item.name),
    ].filter(Boolean)));
    return `
      <section class="api-lab-card api-lab-env-editor">
        <div class="api-lab-card-head">
          <span class="api-lab-icon">⚙️</span>
          <div><strong>配置环境</strong><small>只保存到平台本地，不回写 Apifox</small></div>
        </div>
        <label>Base URL<input id="api-lab-env-base-url" value="${html(baseUrl)}" placeholder="https://print.wisebeginner3d.com/app"></label>
        <label>Biz<input id="api-lab-env-biz" value="${html(biz && biz !== '已配置' ? biz : 'ZXB')}" placeholder="ZXB"></label>
        <label>业务 Token 写入位置
          <select id="api-lab-env-auth-header">
            ${authOptions.map(item => `<option value="${html(item)}" ${item === authHeader ? 'selected' : ''}>${html(item)}</option>`).join('')}
          </select>
        </label>
        <label>业务 Token<input id="api-lab-env-token" type="password" placeholder="已配置时不用重复填写"></label>
        <div class="api-lab-variable-editor">
          <div class="api-lab-variable-editor-head">
            <strong>环境变量</strong>
            <small>${variables.length} 个变量；敏感值不回显，留空表示沿用服务端已保存值。</small>
          </div>
          ${variables.map(item => `
            <div class="api-lab-variable-row" data-api-lab-variable-row data-name="${html(item.name)}" data-scope="${html(item.scope || 'environment')}" data-sensitive="${item.sensitive ? '1' : '0'}" data-configured="${item.configured ? '1' : '0'}">
              <span>${html(item.name)}</span>
              <em>${html(item.scope || 'environment')}</em>
              <input data-api-lab-variable-value value="${item.sensitive ? '' : html(item.value_preview || '')}" placeholder="${html(item.sensitive && item.configured ? '已配置，粘贴后更新' : '空')}">
            </div>
          `).join('') || '<div class="api-lab-small-empty">暂无 Apifox 环境变量，可先填写 Base URL、Biz 和业务 Token。</div>'}
        </div>
        <div class="api-lab-inline-actions">
          <button class="btn-sm primary" onclick="apiLabSaveEnvironment()">保存环境</button>
          <button class="btn-sm" onclick="apiLabToggleEnvironmentEditor(false)">取消</button>
        </div>
      </section>
    `;
  }

  function renderApiLabModulePicker(data = {}) {
    const modules = data.modules || [];
    const current = data.selected_module_path || apiLabSelectedModulePath;
    return `
      <section class="api-lab-card">
        <div class="api-lab-card-head">
          <span class="api-lab-icon">📦</span>
          <div><strong>测试范围</strong><small>选择模块后自动列出接口</small></div>
        </div>
        <div class="api-lab-module-list">
          ${modules.slice(0, 18).map(item => `
            <button class="api-lab-module ${item.module_path === current ? 'active' : ''}" onclick="apiLabSelectModule('${js(item.module_path)}')">
              <span>${html(item.name || item.module_path)}</span>
              <b>${Number(item.endpoint_count || 0)}</b>
            </button>
          `).join('') || `<div class="api-lab-small-empty">还没有本地接口数据</div>`}
        </div>
      </section>
    `;
  }

  function renderApiLabCommandPanel(data = {}, module = {}, selectedCount = 0) {
    const hasSource = !!data.source?.source_id;
    const hasEndpoints = Number(data.selected_endpoint_count || 0) > 0;
    const hasPlan = !!module.plan_id;
    return `
      <section class="api-lab-card">
        <div class="api-lab-card-head">
          <span class="api-lab-icon">▶️</span>
          <div><strong>测试命令</strong><small>${html(module.name || '先选模块')}</small></div>
        </div>
        <button class="api-lab-command" onclick="showApiAssetsPage()">
          <span>1</span><div><strong>获取 Apifox 接口</strong><small>手动更新并保存到本地测试库</small></div>
        </button>
        <button class="api-lab-command" onclick="apiLabGenerateCases()" ${hasSource && hasEndpoints ? '' : 'disabled'}>
          <span>2</span><div><strong>AI 生成用例</strong><small>${selectedCount || Number(data.selected_endpoint_count || 0)} 个接口，最多 ${Number(data.limits?.max_ai_endpoint_count || 60)} 个</small></div>
        </button>
        <button class="api-lab-command primary" onclick="apiLabRunCases()" ${hasPlan ? '' : 'disabled'}>
          <span>3</span><div><strong>执行测试</strong><small>实时查看日志和报告</small></div>
        </button>
        <button class="api-lab-command" onclick="apiLabGenerateAndRun()" ${hasSource && hasEndpoints ? '' : 'disabled'}>
          <span>⚡</span><div><strong>生成并执行</strong><small>适合首次调试一个模块</small></div>
        </button>
      </section>
    `;
  }

  function renderApiLabHistory(data = {}) {
    const rows = data.runs || [];
    return `
      <section class="api-lab-card">
        <div class="api-lab-card-head">
          <span class="api-lab-icon">📜</span>
          <div><strong>执行历史</strong><small>最近 ${rows.length} 次</small></div>
        </div>
        <div class="api-lab-history">
          ${rows.slice(0, 8).map(row => `
            <button onclick="apiLabOpenRun('${js(row.execution_id)}')" class="api-lab-history-row ${row.execution_id === data.latest_run?.execution_id ? 'active' : ''}">
              <span>${html(row.updated_at || row.created_at || '-')}</span>
              <b class="${html(row.status || '')}">${html(apiLabStatusText(row.status))}</b>
            </button>
          `).join('') || '<div class="api-lab-small-empty">暂无执行历史</div>'}
        </div>
      </section>
    `;
  }

  function renderApiLabScopePanel(data = {}, module = {}, endpointCount = 0) {
    const endpoints = data.endpoints || [];
    const executableCount = Number(module.executable_case_count || 0);
    const reviewCount = Number(module.needs_review_case_count || 0);
    const caseSummary = executableCount
      ? `${executableCount} 条可执行${reviewCount ? ` · ${reviewCount} 条待补数据` : ''}`
      : '还没有可执行用例';
    return `
      <section class="api-lab-panel">
        <div class="api-lab-panel-head">
          <div>
            <span class="api-lab-kicker">当前测试</span>
            <h3>${html(module.module_path || data.selected_module_path || '未选择模块')}</h3>
            <p>${Number(endpointCount || 0)} 个本地接口，当前模块 ${Number(data.selected_endpoint_count || endpoints.length || 0)} 个；AI 生成和执行都基于保存后的本地快照。</p>
          </div>
          <div class="api-lab-plan-state">
            <span>${module.plan_id ? '已有 AI 用例' : '待生成用例'}</span>
            <strong>${html(caseSummary)}</strong>
          </div>
        </div>
        <div class="api-lab-endpoint-list">
          ${endpoints.map(item => `
            <label class="api-lab-endpoint">
              <input type="checkbox" onchange="apiLabToggleEndpoint('${js(item.endpoint_id)}', this.checked)">
              <span class="method ${methodTone(item.method)}">${html(item.method)}</span>
              <strong>${html(item.path)}</strong>
              <small>${html(item.name || item.summary || '')}</small>
              ${item.requires_auth ? '<em>鉴权</em>' : ''}
            </label>
          `).join('') || `<div class="api-lab-empty-inline">这个模块没有可测试接口。</div>`}
        </div>
      </section>
    `;
  }

  function renderApiLabConsole(data = {}) {
    return `
      <section class="api-lab-console" id="api-lab-console-root">
        <div class="api-lab-tabs">
          <button class="${apiLabActiveTab === 'log' ? 'active' : ''}" onclick="apiLabSwitchTab('log')">执行日志</button>
          <button class="${apiLabActiveTab === 'report' ? 'active' : ''}" onclick="apiLabSwitchTab('report')">测试报告</button>
        </div>
        <div data-api-lab-tab="log" ${apiLabActiveTab === 'log' ? '' : 'hidden'}>${renderApiLabLogs(data.latest_run || {})}</div>
        <div data-api-lab-tab="report" ${apiLabActiveTab === 'report' ? '' : 'hidden'}>${renderApiLabReport(data.latest_run || {}, data.reports || [])}</div>
      </section>
    `;
  }

  function renderApiLabLogs(run = {}) {
    const events = Array.isArray(run.events) ? run.events : [];
    if (!run.execution_id) {
      return `<div class="api-lab-console-empty">选择模块后点击「生成并执行」，这里会实时追加请求、响应和断言日志。</div>`;
    }
    return `
      <div class="api-lab-task-line">
        <strong>任务：${html(run.execution_id)}</strong>
        <span class="${html(run.status || '')}">${html(apiLabStatusText(run.status))}</span>
      </div>
      <div class="api-lab-log-list">
        ${events.map(event => `
          <div class="api-lab-log-row ${html(event.status || '')}">
            <time>${html(event.timestamp || event.at || '')}</time>
            <b>${html(apiLabEventLevel(event))}</b>
            <span>${html(event.summary || event.message || '')}</span>
          </div>
        `).join('') || '<div class="api-lab-console-empty">暂无执行日志</div>'}
      </div>
    `;
  }

  function renderApiLabReport(run = {}, reports = []) {
    const stats = run.stats || {};
    const results = Array.isArray(run.results) ? run.results : [];
    const total = Number(stats.total || results.length || 0);
    const passed = Number(stats.passed || results.filter(item => item.status === 'passed').length || 0);
    const failed = Number(stats.failed || results.filter(item => item.status === 'failed').length || 0);
    const skipped = Math.max(0, total - passed - failed);
    const passRate = total ? Math.round((passed / total) * 1000) / 10 : 0;
    const failures = results.filter(item => item.status === 'failed');
    return `
      <div class="api-lab-report-summary">
        <div><strong>${total}</strong><span>总用例</span></div>
        <div class="ok"><strong>${passed}</strong><span>通过</span></div>
        <div class="bad"><strong>${failed}</strong><span>失败</span></div>
        <div><strong>${skipped}</strong><span>跳过</span></div>
        <div><strong>${passRate}%</strong><span>通过率</span></div>
      </div>
      ${failures.length ? `
        <div class="api-lab-failure-box">
          <strong>失败分析</strong>
          ${failures.slice(0, 8).map(item => `
            <p><b>${html(item.name || item.case_id || '失败用例')}</b>：${html(apiLabFailureReason(item))}</p>
          `).join('')}
        </div>
      ` : `<div class="api-lab-success-box">${total ? '本次没有失败用例。' : '执行完成后这里会展示摘要和失败分析。'}</div>`}
      <div class="api-lab-result-table">
        ${results.map((item, index) => `
          <div class="api-lab-result-row ${html(item.status || '')}">
            <span>${index + 1}</span>
            <strong>${html(item.name || item.case_id || '-')}</strong>
            <b>${html(apiLabStatusText(item.status))}</b>
            <em>${html(item.duration_ms || 0)}ms</em>
          </div>
        `).join('')}
      </div>
      ${!results.length && reports.length ? `<div class="api-lab-console-empty">最近报告：${html(reports[0].report_id || reports[0].name || '-')}</div>` : ''}
    `;
  }

  function apiLabStatusText(status = '') {
    const value = String(status || '').toLowerCase();
    if (value === 'succeeded' || value === 'passed') return '通过';
    if (value === 'failed') return '失败';
    if (value === 'running') return '运行中';
    if (value === 'queued') return '排队中';
    if (value === 'cancelled') return '已取消';
    return status || '-';
  }

  function apiLabEventLevel(event = {}) {
    const status = String(event.status || '').toLowerCase();
    if (status === 'passed' || status === 'succeeded') return 'PASS';
    if (status === 'failed') return 'FAIL';
    if (status === 'queued') return 'WAIT';
    return 'INFO';
  }

  function apiLabFailureReason(result = {}) {
    const assertion = result.assertion || result.assertions || {};
    const response = result.response || {};
    return result.error
      || assertion.error
      || assertion.summary
      || response.error
      || `HTTP ${response.status_code || '-'}，请查看请求和响应详情`;
  }

  function apiLabScheduleRunPolling(data = {}) {
    if (apiLabPollTimer) clearTimeout(apiLabPollTimer);
    apiLabPollTimer = null;
    const run = data.latest_run || {};
    if (!['queued', 'running'].includes(String(run.status || '').toLowerCase())) return;
    apiLabPollTimer = setTimeout(
      () => apiLabOpenRun(run.execution_id, {silent: true}),
      Number(run.poll_after_ms || 1500),
    );
  }

  function apiLabCurrentPayload(extra = {}) {
    return {
      source_id: apiLabSelectedSourceId || apiLabData?.source?.source_id || '',
      module_path: apiLabSelectedModulePath || apiLabData?.selected_module_path || '',
      endpoint_ids: Array.from(apiLabSelectedEndpointIds),
      ...extra,
    };
  }

  async function apiLabRefreshApifox() {
    const source = apiLabData?.source || {};
    if (!source.source_id) return showApiAssetsPage();
    toast('正在从 Apifox 手动更新接口和环境...', 'info');
    try {
      const result = await apiLabRequest('/apifox/refresh', {
        method: 'POST',
        body: JSON.stringify({
          source_id: source.source_id,
          project_id: source.project_id,
          branch_id: source.branch_id,
          environment_id: source.environment_id,
          name: source.name,
        }),
      });
      if (result.environment_warning) toast(`环境读取有提示：${result.environment_warning}`, 'warning');
      toast('Apifox 数据已保存到本地测试库');
      apiLabSelectedEndpointIds.clear();
      renderApiLabDashboard(result.state || await apiLabLoad({silent: true}));
    } catch (error) {
      toast(error.message || String(error), 'error');
    }
  }

  async function apiLabGenerateCases() {
    toast('正在让 AI 生成接口测试用例...', 'info');
    try {
      const result = await apiLabRequest('/cases/generate', {
        method: 'POST',
        body: JSON.stringify(apiLabCurrentPayload()),
      });
      toast(`AI 用例已生成：${Number(result.plan?.case_count || 0)} 条`);
      renderApiLabDashboard(result.state || await apiLabLoad({silent: true}));
      return result;
    } catch (error) {
      toast(error.message || String(error), 'error');
      return null;
    }
  }

  async function apiLabRunCases(planId = '') {
    toast('接口测试已提交，开始读取执行日志...', 'info');
    try {
      const result = await apiLabRequest('/executions/run', {
        method: 'POST',
        body: JSON.stringify(apiLabCurrentPayload({plan_id: planId})),
      });
      apiLabActiveTab = 'log';
      localStorage.setItem('api_lab_active_tab', apiLabActiveTab);
      renderApiLabDashboard(result.state || await apiLabLoad({silent: true, executionId: result.execution?.execution_id || ''}));
      apiLabScheduleRunPolling(result.state || {});
      return result;
    } catch (error) {
      toast(error.message || String(error), 'error');
      return null;
    }
  }

  async function apiLabGenerateAndRun() {
    const result = await apiLabGenerateCases();
    const planId = result?.plan?.plan_id || '';
    if (planId) await apiLabRunCases(planId);
  }

  async function apiLabOpenRun(executionId, options = {}) {
    if (!executionId) return;
    try {
      const result = await apiLabRequest(`/executions/${encodeURIComponent(executionId)}`);
      if (options.silent) apiLabRefreshConsoleOnly(result.state || apiLabData || {});
      else renderApiLabDashboard(result.state || apiLabData || {});
      apiLabScheduleRunPolling(result.state || {});
    } catch (error) {
      if (!options.silent) toast(error.message || String(error), 'error');
    }
  }

  function apiLabSelectModule(modulePath) {
    apiLabSelectedModulePath = String(modulePath || '');
    apiLabSelectedEndpointIds.clear();
    localStorage.setItem('api_lab_module_path', apiLabSelectedModulePath);
    apiLabLoad({modulePath: apiLabSelectedModulePath});
  }

  function apiLabToggleEndpoint(endpointId, checked) {
    const id = String(endpointId || '');
    if (!id) return;
    if (checked) apiLabSelectedEndpointIds.add(id);
    else apiLabSelectedEndpointIds.delete(id);
  }

  function apiLabSwitchTab(tab) {
    apiLabActiveTab = tab === 'report' ? 'report' : 'log';
    localStorage.setItem('api_lab_active_tab', apiLabActiveTab);
    document.querySelectorAll('[data-api-lab-tab]').forEach(panel => {
      panel.hidden = panel.dataset.apiLabTab !== apiLabActiveTab;
    });
    document.querySelectorAll('.api-lab-tabs button').forEach(button => {
      button.classList.toggle('active', button.textContent.includes(apiLabActiveTab === 'log' ? '日志' : '报告'));
    });
  }

  function apiLabToggleEnvironmentEditor(value) {
    apiLabEditingEnvironment = !!value;
    renderApiLabDashboard(apiLabData || {});
  }

  function apiLabReadEnvironmentVariables() {
    return Array.from(document.querySelectorAll('[data-api-lab-variable-row]')).map(row => {
      const input = row.querySelector('[data-api-lab-variable-value]');
      return {
        name: row.dataset.name || '',
        scope: row.dataset.scope || 'environment',
        sensitive: row.dataset.sensitive === '1',
        configured: row.dataset.configured === '1',
        value: input ? input.value : '',
      };
    }).filter(item => item.name);
  }

  async function apiLabSaveEnvironment() {
    const baseUrl = document.getElementById('api-lab-env-base-url')?.value || '';
    const biz = document.getElementById('api-lab-env-biz')?.value || '';
    const token = document.getElementById('api-lab-env-token')?.value || '';
    const authHeaderName = document.getElementById('api-lab-env-auth-header')?.value || 'Authorization';
    try {
      const result = await apiLabRequest('/environment', {
        method: 'POST',
        body: JSON.stringify({
          source_id: apiLabSelectedSourceId || apiLabData?.source?.source_id || '',
          base_url: baseUrl,
          biz,
          auth_header_name: authHeaderName,
          business_token: token,
          variables: apiLabReadEnvironmentVariables(),
        }),
      });
      apiLabEditingEnvironment = false;
      toast('环境已保存到本地测试库');
      renderApiLabDashboard(result.state || await apiLabLoad({silent: true}));
    } catch (error) {
      toast(error.message || String(error), 'error');
    }
  }

  function apiLabRefreshConsoleOnly(state = {}) {
    apiLabData = state || apiLabData || {};
    const previousLog = document.querySelector('.api-lab-log-list');
    const stickToBottom = previousLog
      ? previousLog.scrollHeight - previousLog.scrollTop - previousLog.clientHeight < 24
      : true;
    const root = document.getElementById('api-lab-console-root');
    if (!root) {
      renderApiLabDashboard(apiLabData || {});
      return;
    }
    root.outerHTML = renderApiLabConsole(apiLabData || {});
    if (stickToBottom) {
      const nextLog = document.querySelector('.api-lab-log-list');
      if (nextLog) nextLog.scrollTop = nextLog.scrollHeight;
    }
  }

  function renderApiLabSourcePage(data = apiLabData || {}) {
    const area = typeof setApiTestingPage === 'function'
      ? setApiTestingPage('api_assets', '接口来源', '只在这里手动更新 Apifox；成功后接口和环境会保存到本地测试库。')
      : document.getElementById('editor-area');
    if (!area) return;
    const sources = data.sources || [];
    const source = data.source || {};
    const form = apiLabSourceForm;
    area.className = 'editor-area api-testing-area api-lab-area';
    area.innerHTML = `
      <div class="api-lab-shell">
        <section class="api-lab-hero compact">
          <div>
            <span class="api-lab-kicker">接口来源</span>
            <h2>Apifox 手动更新</h2>
            <p>第一次配置项目和环境；以后只点“保存并更新”，平台会把接口、环境和变量保存到本地测试库。</p>
          </div>
          <button class="btn-sm primary" onclick="showApiTestingDashboard()">回到测试台</button>
        </section>
        <div class="api-lab-source-layout">
          <section class="api-lab-panel">
            <div class="api-lab-panel-head">
              <div><h3>已保存来源</h3><p>同事进入后默认使用这里的本地测试库，不需要每次刷 Apifox。</p></div>
            </div>
            <div class="api-lab-source-list">
              ${sources.map(item => `
                <button class="api-lab-source-row ${item.source_id === source.source_id ? 'active' : ''}" onclick="apiLabSelectSource('${js(item.source_id)}')">
                  <strong>${html(item.name || item.project_name || item.source_id)}</strong>
                  <span>${Number(item.endpoint_count || 0)} 接口 · ${html(item.environment_name || '默认环境')}</span>
                  <em>${html(item.last_sync_at || item.updated_at || '-')}</em>
                </button>
              `).join('') || '<div class="api-lab-empty-inline">还没有保存过接口来源。</div>'}
            </div>
          </section>
          <section class="api-lab-panel">
            <div class="api-lab-panel-head">
              <div><h3>保存或更新 Apifox</h3><p>只读拉取项目接口和环境，本地保存后用于测试。</p></div>
            </div>
            <div class="api-lab-form-grid">
              <label>Apifox Token<input id="api-lab-apifox-token" type="password" value="${html(form.access_token || '')}" placeholder="afxp_..."></label>
              <label>来源名称<input id="api-lab-apifox-name" value="${html(form.name || source.name || '3D')}" placeholder="3D"></label>
              <label>Apifox 服务<input id="api-lab-apifox-base" value="${html(form.base_url || 'https://api.apifox.com')}" placeholder="https://api.apifox.com"></label>
            </div>
            <details class="api-lab-source-advanced">
              <summary>高级字段：手动填写项目、分支和环境 ID</summary>
              <div class="api-lab-form-grid">
                <label>项目 ID<input id="api-lab-apifox-project" value="${html(form.project_id || source.project_id || '')}" placeholder="从项目列表选择后自动填入"></label>
                <label>分支 ID<input id="api-lab-apifox-branch" value="${html(form.branch_id || source.branch_id || '')}" placeholder="主分支可留空"></label>
                <label>环境 ID<input id="api-lab-apifox-env" value="${html(form.environment_id || source.environment_id || '')}" placeholder="从环境列表选择后自动填入"></label>
              </div>
            </details>
            ${apiLabSelectedSourceHint()}
            <div class="api-lab-inline-actions">
              <button class="btn-sm" onclick="apiLabReadApifoxProjects()">读取项目列表</button>
              <button class="btn-sm" onclick="apiLabReadApifoxContext()">读取分支环境</button>
              <button class="btn-sm primary" onclick="apiLabSaveAndRefreshApifox()">保存并更新本地测试库</button>
            </div>
            ${renderApiLabDiscovery()}
            <details class="api-lab-manual-import">
              <summary>没有 Apifox 时手动导入 OpenAPI JSON</summary>
              <div class="api-lab-form-grid">
                <label>名称<input id="api-lab-openapi-name" placeholder="OpenAPI 项目"></label>
                <label>Base URL<input id="api-lab-openapi-base" placeholder="https://example.com"></label>
              </div>
              <textarea id="api-lab-openapi-json" placeholder='{"openapi":"3.0.1","paths":{...}}'></textarea>
              <button class="btn-sm" onclick="apiLabImportOpenApi()">保存 OpenAPI 到测试库</button>
            </details>
          </section>
        </div>
      </div>
    `;
  }

  function renderApiLabDiscovery() {
    const projects = apiLabDiscoveredProjects || [];
    const branches = apiLabDiscoveredContext.branches || [];
    const environments = apiLabDiscoveredContext.environments || [];
    if (!projects.length && !branches.length && !environments.length) return '';
    return `
      <div class="api-lab-discovery">
        ${projects.length ? `<h4>选择项目</h4><div>${projects.map(item => `<button onclick="apiLabPickProject('${js(item.id)}','${js(item.name)}')"><strong>${html(item.name)}</strong><small>${html(item.team?.name || '未标注团队')}</small></button>`).join('')}</div>` : ''}
        ${branches.length ? `<h4>选择分支</h4><div>${branches.map(item => `<button onclick="apiLabPickBranch('${js(item.id)}','${js(item.name)}')"><strong>${html(item.name)}</strong><small>${item.is_default ? '默认' : '可选'}</small></button>`).join('')}</div>` : ''}
        ${environments.length ? `<h4>选择环境</h4><div>${environments.map(item => {
          const snapshot = item.environment_snapshot || {};
          const variableCount = Number(snapshot.variable_count || (snapshot.variables || []).filter(v => !v.group_placeholder).length || 0);
          const baseUrlCount = Number((snapshot.base_urls || []).length || 0);
          return `<button onclick="apiLabPickEnvironment('${js(item.id)}','${js(item.name)}')"><strong>${html(item.name)}</strong><small>${baseUrlCount} 个服务地址 · ${variableCount} 个变量</small></button>`;
        }).join('')}</div>` : ''}
      </div>
    `;
  }

  function apiLabSelectedSourceHint() {
    const projectName = apiLabSourceForm.name || apiLabDiscoveredContext.project?.name || '';
    const projectId = apiLabSourceForm.project_id || '';
    const branchId = apiLabSourceForm.branch_id || '';
    const environmentId = apiLabSourceForm.environment_id || '';
    if (!projectId && !environmentId && !projectName) return '';
    return `
      <div class="api-lab-selected-source">
        <strong>${html(projectName || '已选择 Apifox 项目')}</strong>
        <span>${html(branchId ? '已选择分支' : '主分支')} · ${html(environmentId ? '已选择环境' : '未绑定环境')}</span>
      </div>
    `;
  }

  function apiLabReadSourceForm() {
    apiLabSourceForm = {
      access_token: document.getElementById('api-lab-apifox-token')?.value || apiLabSourceForm.access_token || '',
      project_id: document.getElementById('api-lab-apifox-project')?.value || '',
      branch_id: document.getElementById('api-lab-apifox-branch')?.value || '',
      environment_id: document.getElementById('api-lab-apifox-env')?.value || '',
      name: document.getElementById('api-lab-apifox-name')?.value || '',
      base_url: document.getElementById('api-lab-apifox-base')?.value || 'https://api.apifox.com',
    };
    return apiLabSourceForm;
  }

  async function apiLabReadApifoxProjects() {
    const form = apiLabReadSourceForm();
    if (!form.access_token) return toast('请输入 Apifox Token', 'warning');
    try {
      const data = await apiRequest('/api-testing/apifox/discovery/projects', {
        method: 'POST',
        body: {access_token: form.access_token, base_url: form.base_url},
      });
      if (data.ok === false) throw new Error(data.error || '读取项目失败');
      apiLabDiscoveredProjects = data.projects || [];
      renderApiLabSourcePage(apiLabData || {});
    } catch (error) {
      toast(error.message || String(error), 'error');
    }
  }

  async function apiLabReadApifoxContext() {
    const form = apiLabReadSourceForm();
    if (!form.access_token || !form.project_id) return toast('请输入 Token 和项目 ID', 'warning');
    try {
      const data = await apiRequest('/api-testing/apifox/discovery/project-context', {
        method: 'POST',
        body: {access_token: form.access_token, project_id: form.project_id, base_url: form.base_url},
      });
      if (data.ok === false) throw new Error(data.error || '读取分支环境失败');
      apiLabDiscoveredContext = data.context || data || {};
      renderApiLabSourcePage(apiLabData || {});
    } catch (error) {
      toast(error.message || String(error), 'error');
    }
  }

  async function apiLabSaveAndRefreshApifox() {
    const form = apiLabReadSourceForm();
    if (!form.project_id) return toast('请输入或选择 Apifox 项目 ID', 'warning');
    try {
      toast('正在保存并更新本地测试库...', 'info');
      const result = await apiLabRequest('/apifox/refresh', {
        method: 'POST',
        body: JSON.stringify({
          ...form,
          source_id: apiLabData?.source?.source_id || '',
        }),
      });
      apiLabData = result.state || null;
      apiLabSelectedSourceId = apiLabData?.source?.source_id || '';
      apiLabSelectedModulePath = apiLabData?.selected_module_path || '';
      apiLabDiscoveredProjects = [];
      apiLabDiscoveredContext = {};
      toast('接口和环境已保存到本地测试库');
      showApiTestingDashboard();
    } catch (error) {
      toast(error.message || String(error), 'error');
    }
  }

  async function apiLabImportOpenApi() {
    const name = document.getElementById('api-lab-openapi-name')?.value || 'OpenAPI 导入';
    const baseUrl = document.getElementById('api-lab-openapi-base')?.value || '';
    const raw = document.getElementById('api-lab-openapi-json')?.value || '';
    if (!raw.trim()) return toast('请粘贴 OpenAPI JSON', 'warning');
    try {
      const parsed = JSON.parse(raw);
      const result = await apiLabRequest('/openapi/import', {
        method: 'POST',
        body: {name, base_url: baseUrl, document: parsed},
      });
      apiLabData = result.state || null;
      apiLabSelectedSourceId = apiLabData?.source?.source_id || '';
      apiLabSelectedModulePath = apiLabData?.selected_module_path || '';
      if (apiLabSelectedSourceId) localStorage.setItem('api_lab_source_id', apiLabSelectedSourceId);
      if (apiLabSelectedModulePath) localStorage.setItem('api_lab_module_path', apiLabSelectedModulePath);
      apiLabSelectedEndpointIds.clear();
      toast('OpenAPI 已保存到本地测试库');
      showApiTestingDashboard();
    } catch (error) {
      toast(`OpenAPI 导入失败：${error.message || error}`, 'error');
    }
  }

  function apiLabPickProject(id, name) {
    apiLabSourceForm = {...apiLabReadSourceForm(), project_id: id, name: name || apiLabSourceForm.name || 'Apifox 项目'};
    renderApiLabSourcePage(apiLabData || {});
  }

  function apiLabPickBranch(id, name) {
    apiLabSourceForm = {...apiLabReadSourceForm(), branch_id: id, branch_name: name || ''};
    renderApiLabSourcePage(apiLabData || {});
  }

  function apiLabPickEnvironment(id, name) {
    apiLabSourceForm = {...apiLabReadSourceForm(), environment_id: id, environment_name: name || ''};
    renderApiLabSourcePage(apiLabData || {});
  }

  async function apiLabSelectSource(sourceId) {
    apiLabSelectedSourceId = String(sourceId || '');
    apiLabSelectedModulePath = '';
    localStorage.setItem('api_lab_source_id', apiLabSelectedSourceId);
    localStorage.removeItem('api_lab_module_path');
    await apiLabLoad({sourceId: apiLabSelectedSourceId});
  }

  async function showApiTestingDashboardOverride() {
    return apiLabLoad();
  }

  async function showApiAssetsPageOverride() {
    const cached = apiLabData || apiLabReadCache();
    if (cached) renderApiLabSourcePage(cached);
    const data = await apiLabLoad({silent: true});
    renderApiLabSourcePage(data || cached || {});
  }

  window.showApiTestingDashboard = showApiTestingDashboardOverride;
  window.showApiAssetsPage = showApiAssetsPageOverride;
  window.apiLabRefreshApifox = apiLabRefreshApifox;
  window.apiLabGenerateCases = apiLabGenerateCases;
  window.apiLabRunCases = apiLabRunCases;
  window.apiLabGenerateAndRun = apiLabGenerateAndRun;
  window.apiLabOpenRun = apiLabOpenRun;
  window.apiLabSelectModule = apiLabSelectModule;
  window.apiLabToggleEndpoint = apiLabToggleEndpoint;
  window.apiLabSwitchTab = apiLabSwitchTab;
  window.apiLabToggleEnvironmentEditor = apiLabToggleEnvironmentEditor;
  window.apiLabSaveEnvironment = apiLabSaveEnvironment;
  window.apiLabReadApifoxProjects = apiLabReadApifoxProjects;
  window.apiLabReadApifoxContext = apiLabReadApifoxContext;
  window.apiLabSaveAndRefreshApifox = apiLabSaveAndRefreshApifox;
  window.apiLabImportOpenApi = apiLabImportOpenApi;
  window.apiLabPickProject = apiLabPickProject;
  window.apiLabPickBranch = apiLabPickBranch;
  window.apiLabPickEnvironment = apiLabPickEnvironment;
  window.apiLabSelectSource = apiLabSelectSource;
})();
