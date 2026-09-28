// Business history uses server filtering and bearer authorization through apiRequest.
(() => {
  const domains = { account: '账号', asset: '资产', file: '文件', case: '用例', job: '执行任务', runner: 'Runner', recording: '录制', report: '报告', agent_run: 'Agent', app: '应用', module: '模块', sonic: '设备', yaml: 'YAML', api: 'API', api_project: 'API 项目', api_environment: 'API 环境', model_config: '模型配置', knowledge: '知识库', task: '任务', repair_draft: '修复草稿', config: '配置', figma: 'Figma', ui: 'UI', preflight: '运行前检查', test_run: '测试运行', api_collection: 'API 集合', api_execution: 'API 执行', api_request: 'API 请求', api_load_run: 'API 压测运行', api_load_scenario: 'API 压测场景', api_load_agent: 'API 压测节点', api_dataset: 'API 数据集', unknown: '未知领域' };
  const verbs = { view: '查看', read: '读取', list: '列表', create: '创建', update: '修改', save: '保存', delete: '删除', run: '执行', execute: '执行', submit: '提交', accepted: '受理', cancel: '取消', retry: '重试', start: '启动', stop: '停止', upload: '上传', download: '下载', import: '导入', export: '导出', generate: '生成', replay: '回放', heartbeat: '心跳', login: '登录', logout: '退出', manage: '管理', preview: '预览', finish: '结束', refresh: '刷新', rename: '重命名', copy: '复制', move: '移动', inspect: '检查', request: '请求', reset: '重置', approve: '批准', reject: '拒绝', apply: '应用', parse: '解析', parse_async: '异步解析' };
  const results = { success: '成功', failed: '失败', denied: '已拒绝', partial: '部分成功', accepted: '已受理（尚未完成执行）', interrupted: '已中断' };
  const e = value => escapeHtml(String(value ?? ''));
  let root = null, revision = 0, pendingList = null, pendingDetail = null;
  let filters = {}, cursor = null, previous = [], next = null, rows = [];
  let detailEvent = null, detailCursor = null, detailPrevious = [], detailNext = null;
  const superuser = () => currentAccessProfile?.is_superuser === true;
  const identity = () => `${currentAccessProfile?.user_id || ''}:${sessionStorage.getItem('sessionToken') || ''}`;
  const current = (node, version, actor) => root === node && node.isConnected && activeWorkflow === 'operation_history' && revision === version && identity() === actor;

  function abort(kind) {
    const request = kind === 'list' ? pendingList : pendingDetail;
    if (request) request.abort();
    if (kind === 'list') pendingList = null;
    else pendingDetail = null;
  }
  function clearDetail() {
    abort('detail');
    detailEvent = null; detailCursor = null; detailPrevious = []; detailNext = null;
    root?.querySelector('[data-history-detail]')?.replaceChildren();
  }
  function invalidate() {
    revision++;
    abort('list'); clearDetail();
    rows = []; next = null;
    root?.querySelector('[data-history-rows]')?.replaceChildren();
    root?.querySelector('[data-history-count]')?.replaceChildren();
    root?.querySelector('[data-history-audit]')?.replaceChildren();
  }
  // Explicit auth hooks invalidate rows and pending requests before an account changes.
  window.resetOperationHistory = () => {
    const reopen = root?.isConnected && activeWorkflow === 'operation_history' && sessionStorage.getItem('sessionToken');
    invalidate(); root?.replaceChildren(); root = null;
    filters = {}; cursor = null; previous = [];
    if (reopen) showOperationHistory();
  };
  window.leaveOperationHistory = () => { invalidate(); root = null; };

  async function request(path, controller) {
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      // Redirect only after checking this view and identity; obsolete 401s must not clear a newer login.
      const data = await apiRequest(path, { signal: controller.signal, skipAuthRedirect: true });
      if (data.code === 'unauthorized') throw Object.assign(new Error('unauthorized'), { status: 401 });
      if (data.ok !== true) throw new Error('operation_history_unavailable');
      return data;
    } finally { clearTimeout(timer); }
  }
  function query(pageCursor) {
    const q = new URLSearchParams({ limit: '50', scope: filters.scope === 'all' && superuser() ? 'all' : 'own' });
    for (const key of ['action', 'result', 'resource_type', 'resource_id', 'from_ts', 'to_ts']) {
      if (filters[key] !== '' && filters[key] != null) q.set(key, filters[key]);
    }
    if (q.get('scope') === 'all' && filters.actor_id) q.set('actor_id', filters.actor_id);
    if (pageCursor != null) q.set('cursor', pageCursor);
    return q;
  }
  function time(value) {
    const date = new Date(Number(value) * 1000);
    return Number.isFinite(date.getTime()) ? formatDisplayTime(date.toISOString()) : '时间未知';
  }
  function actorLabel(actor = {}) {
    if (actor.kind === 'user') return actor.display_name || actor.username || actor.user_id || '归属未知';
    return ({ runner: 'Runner', system: '系统', anonymous: '未登录', unknown: '归属未知' })[actor.kind] || '归属未知';
  }
  function actionLabel(action) {
    const [domain, ...tail] = String(action || '').split('.');
    const verb = tail.join('.');
    return `${domains[domain] || domain || '未知操作'}${verb ? ' · ' + (verbs[verb] || verb) : ''}`;
  }
  function resultLabel(result) { return results[result] || `未知结果：${result || '未记录'}`; }
  function table(headers, content) {
    return `<div class="operation-table-scroll"><table><thead><tr>${headers.map(label => `<th scope="col">${label}</th>`).join('')}</tr></thead><tbody>${content}</tbody></table></div>`;
  }
  function auditWarning(audit = {}) {
    const unresolved = Math.max(0, Number(audit.unresolved) || 0);
    return audit.capture_degraded || unresolved > 0 ? `操作记录采集降级；至少 ${unresolved} 条待处理（下限），记录可能不完整。` : '';
  }
  function listButtons() {
    if (!root) return;
    root.querySelector('[data-history-prev]').disabled = Boolean(pendingList) || !previous.length;
    root.querySelector('[data-history-next]').disabled = Boolean(pendingList) || next == null;
    root.querySelector('[data-history-refresh]').disabled = Boolean(pendingList);
  }
  async function loadList() {
    if (!root || pendingList) return;
    invalidate();
    const node = root, version = revision, actor = identity();
    const timeError = ['from_ts', 'to_ts'].some(key => filters[key] !== '' && filters[key] != null && !Number.isFinite(filters[key]))
      ? '请输入有效的起止时间'
      : filters.from_ts !== '' && filters.to_ts !== '' && filters.from_ts != null && filters.to_ts != null && filters.from_ts > filters.to_ts
        ? '结束时间不能早于开始时间' : '';
    if (timeError) {
      node.querySelector('[data-history-rows]').innerHTML = `<p class="operation-state" role="alert">${e(timeError)}</p>`;
      listButtons(); return;
    }
    const controller = new AbortController(); pendingList = controller;
    const body = node.querySelector('[data-history-rows]');
    body.innerHTML = '<p class="operation-state" role="status">正在加载操作记录…</p>';
    listButtons();
    try {
      const data = await request('/operations?' + query(cursor), controller);
      if (!current(node, version, actor)) return;
      rows = Array.isArray(data.events) ? data.events : [];
      next = data.next_cursor ?? null;
      node.querySelector('[data-history-audit]').textContent = auditWarning(data.audit);
      node.querySelector('[data-history-count]').textContent = `共 ${Number(data.total) || 0} 条 · 第 ${previous.length + 1} 页 · 本页 ${rows.length} 条`;
      body.innerHTML = rows.length ? table(['时间', '操作者 / 发起人', '操作', '资源', '结果 / 明细'], rows.map((item, index) => `<tr><td>${e(time(item.timestamp))}</td><td>${e(actorLabel(item.actor))}${item.actor?.user_id ? `<small>账号 ID：${e(item.actor.user_id)}</small>` : ''}${item.initiator_user_id ? `<small>发起人 ID：${e(item.initiator_user_id)}</small>` : ''}</td><td>${e(actionLabel(item.action))}<small>${e(item.action)}</small></td><td>${e(domains[item.resource_type] || item.resource_type || '未知资源')}<small class="operation-resource-id">${e(item.resource_id || '未记录资源 ID')}</small></td><td>${e(resultLabel(item.result))}${Number(item.item_total) > 0 || item.items_complete === false ? `<button type="button" class="btn-sm" data-history-batch="${index}">批量明细</button>` : ''}</td></tr>`).join('')) : '<p class="operation-state" role="status">暂无匹配的操作记录</p>';
      body.querySelectorAll('[data-history-batch]').forEach(button => button.onclick = () => openDetail(rows[Number(button.dataset.historyBatch)]));
    } catch (error) {
      if (!current(node, version, actor)) return;
      if (error.status === 401) { clearAuthSession(); return; }
      next = null;
      body.innerHTML = '<p class="operation-state" role="alert">操作记录加载失败，请重试。</p><button type="button" class="btn-sm" data-history-retry>重试</button>';
      body.querySelector('[data-history-retry]').onclick = loadList;
    } finally {
      if (pendingList === controller) { pendingList = null; listButtons(); }
    }
  }
  function openDetail(item) {
    if (pendingDetail && detailEvent === item) return;
    clearDetail(); detailEvent = item;
    loadDetail();
  }
  function safeMetadata(item) {
    const values = { '事件 ID': item.event_id, '请求 ID': item.request_id, '接口': item.route_key, 'HTTP 状态': item.status_code, '耗时 ms': item.duration_ms, '来源任务 ID': item.source_job_id, '来源运行 ID': item.source_run_id, '来源录制 ID': item.source_recording_id, '版本 ID': item.version_id, '内容 SHA256': item.content_sha256, '原因': item.summary?.reason_code, '触发方式': item.summary?.trigger, '修改字段': Array.isArray(item.summary?.changed_fields) ? item.summary.changed_fields.join('、') : '' };
    return Object.entries(values).filter(([, value]) => value !== '' && value != null).map(([label, value]) => `<span>${label}：${e(value)}</span>`).join('');
  }
  async function loadDetail() {
    if (!root || !detailEvent || pendingDetail) return;
    const node = root, version = revision, actor = identity(), item = detailEvent;
    const controller = new AbortController(); pendingDetail = controller;
    const panel = node.querySelector('[data-history-detail]');
    panel.innerHTML = '<p class="operation-state" role="status">正在加载批量明细…</p>';
    try {
      // Only scope, cursor and page size belong to item queries; event filters do not.
      const q = new URLSearchParams({ limit: '50', scope: filters.scope === 'all' && superuser() ? 'all' : 'own' });
      if (detailCursor != null) q.set('cursor', detailCursor);
      const data = await request(`/operations/${encodeURIComponent(item.event_id)}/items?${q}`, controller);
      if (!current(node, version, actor) || detailEvent !== item || pendingDetail !== controller) return;
      detailNext = data.next_cursor ?? null;
      const items = Array.isArray(data.items) ? data.items : [];
      const complete = data.items_complete === true;
      panel.innerHTML = `<div class="operation-detail-head"><h3>批量明细</h3><button type="button" class="btn-sm" data-detail-close>关闭明细</button></div><p>共 ${e(data.total ?? 0)} 项 · 已采集 ${e(data.item_captured ?? 0)} 项 · 本页 ${items.length} 项${complete ? '' : ' · 采集不完整'}</p>${complete ? '' : `<p class="operation-warning">明细只包含已采集项，无法据此确认全部结果。采集状态：${e(data.capture_status || '未知')}</p>`}<p class="operation-warning">${e(auditWarning(data.audit))}</p><div class="operation-metadata">${safeMetadata(item)}</div>${items.length ? table(['资源 ID', '结果', '原因'], items.map(value => `<tr><td>${e(value.resource_id || '未记录')}</td><td>${e(resultLabel(value.result))}</td><td>${e(value.reason_code || '—')}</td></tr>`).join('')) : '<p class="operation-state">暂无已采集明细</p>'}<div class="operation-paging"><button type="button" class="btn-sm" data-detail-prev ${detailPrevious.length ? '' : 'disabled'}>上一页明细</button><span>第 ${detailPrevious.length + 1} 页</span><button type="button" class="btn-sm" data-detail-next ${detailNext == null ? 'disabled' : ''}>下一页明细</button></div>`;
      panel.querySelector('[data-detail-close]').onclick = clearDetail;
      panel.querySelector('[data-detail-prev]').onclick = () => { if (pendingDetail || !detailPrevious.length) return; detailCursor = detailPrevious.pop(); loadDetail(); };
      panel.querySelector('[data-detail-next]').onclick = () => { if (pendingDetail || detailNext == null) return; detailPrevious.push(detailCursor); detailCursor = detailNext; loadDetail(); };
    } catch (error) {
      if (!current(node, version, actor) || detailEvent !== item || pendingDetail !== controller) return;
      if (error.status === 401) { clearAuthSession(); return; }
      panel.innerHTML = '<p class="operation-state" role="alert">批量明细加载失败，请重试。</p><button type="button" class="btn-sm" data-detail-retry>重试明细</button><button type="button" class="btn-sm" data-detail-close>关闭明细</button>';
      panel.querySelector('[data-detail-retry]').onclick = loadDetail;
      panel.querySelector('[data-detail-close]').onclick = clearDetail;
    } finally { if (pendingDetail === controller) pendingDetail = null; }
  }
  function readFilters() {
    const values = Object.fromEntries(new FormData(root.querySelector('form')));
    for (const key of ['from_ts', 'to_ts']) values[key] = values[key] ? new Date(values[key]).getTime() / 1000 : '';
    if (values.scope !== 'all' || !superuser()) { values.scope = 'own'; delete values.actor_id; }
    if (['scope', 'action', 'result', 'resource_type', 'resource_id', 'actor_id', 'from_ts', 'to_ts'].every(key => (filters[key] || '') === (values[key] || ''))) return;
    filters = values; cursor = null; previous = [];
    root.querySelector('[data-actor-filter]').hidden = values.scope !== 'all';
    root.querySelector('[name="actor_id"]').disabled = values.scope !== 'all';
    invalidate(); loadList();
  }
  window.showOperationHistory = (options = {}) => {
    // Background updates for other workflows must leave the mounted page and its details intact.
    if (options.refresh === false && root?.isConnected && activeWorkflow === 'operation_history') return;
    document.getElementById('account-menu').hidden = true;
    document.getElementById('account-toggle').setAttribute('aria-expanded', 'false');
    if (root?.isConnected && activeWorkflow === 'operation_history') { loadList(); return; }
    invalidate(); filters = { scope: 'own' }; cursor = null; previous = [];
    const area = document.getElementById('editor-area'); area.className = 'editor-area';
    area.innerHTML = `<section class="operation-history" aria-label="业务操作记录"><div class="operation-heading"><h2>我的操作记录</h2><button type="button" class="btn-sm" data-history-refresh>刷新记录</button></div><p class="operation-intro">包含读取、下载、心跳及业务变更。已受理表示请求已提交，执行完成需看后续结果。</p><form class="operation-filters"><label>范围<select name="scope"><option value="own">我的操作</option>${superuser() ? '<option value="all">全部账号</option>' : ''}</select></label><label>操作<input name="action" type="search" placeholder="如 asset.view" maxlength="128"></label><label>结果<select name="result"><option value="">全部结果</option>${Object.entries(results).map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select></label><label>开始时间<input name="from_ts" type="datetime-local"></label><label>结束时间<input name="to_ts" type="datetime-local"></label><label>资源类型<input name="resource_type" type="search" placeholder="如 asset" maxlength="128"></label><label>资源 ID<input name="resource_id" type="search" maxlength="128"></label><label data-actor-filter hidden>操作者 ID<input name="actor_id" type="search" maxlength="128" disabled></label></form><p class="operation-warning" data-history-audit role="status"></p><p class="operation-count" data-history-count role="status"></p><div data-history-rows></div><div class="operation-paging"><button type="button" class="btn-sm" data-history-prev disabled>上一页</button><span>每页最多 50 条</span><button type="button" class="btn-sm" data-history-next disabled>下一页</button></div><section class="operation-detail" data-history-detail aria-label="批量操作明细"></section></section>`;
    root = area.querySelector('.operation-history');
    const form = root.querySelector('form');
    form.onchange = readFilters;
    form.onsubmit = event => { event.preventDefault(); readFilters(); };
    root.querySelector('[data-history-refresh]').onclick = loadList;
    root.querySelector('[data-history-prev]').onclick = () => { if (pendingList || !previous.length) return; cursor = previous.pop(); loadList(); };
    root.querySelector('[data-history-next]').onclick = () => { if (pendingList || next == null) return; previous.push(cursor); cursor = next; loadList(); };
    loadList();
  };
})();
