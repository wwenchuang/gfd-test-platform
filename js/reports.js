// reports.js
// Round 5: 报告页概览 + 报告列表 + 失败操作入口。

function reportsOverview() {
  const jobs = Array.isArray(latestJobs) ? latestJobs : [];
  const total = jobs.length;
  let success = 0, failed = 0, running = 0;
  let lastRunAt = '';
  jobs.forEach(job => {
    const status = String(job.status || '').toLowerCase();
    if (status === 'success' || status === 'succeeded' || status === 'completed') success++;
    else if (status === 'failed' || status === 'timeout' || status === 'cancelled' || status === 'error') failed++;
    else if (status === 'running' || status === 'pending') running++;
    const t = job.finished_at || job.updated_at || job.started_at || job.created_at || '';
    if (t && t > lastRunAt) lastRunAt = t;
  });
  const failRate = total ? Math.round((failed / total) * 100) : 0;
  return { total, success, failed, running, failRate, lastRunAt: (lastRunAt || '').replace('T', ' ').slice(0, 19) };
}

function reportsFailureType(job) {
  const fr = job.failure_review || job.failureReview || {};
  const t = String(fr.failureType || fr.failure_type || job.failure_type || job.failureType || '').toUpperCase();
  if (['SCRIPT_ISSUE', 'PRODUCT_BUG', 'ENV_ISSUE', 'UNKNOWN'].includes(t)) return t;
  return '';
}

function reportsHasRepairDraft(job) {
  if (!Array.isArray(repairDrafts) || !repairDrafts.length) return false;
  const jobId = job.job_id || job.jobId;
  if (!jobId) return false;
  return repairDrafts.some(d => (d.jobId || d.job_id) === jobId);
}

function reportCenterStatusKey(job) {
  const status = String(job?.status || '').toLowerCase();
  if (['success', 'succeeded', 'completed'].includes(status)) return 'success';
  if (['failed', 'timeout', 'cancelled', 'error'].includes(status)) return 'failed';
  if (['running', 'pending', 'queued', 'assigned'].includes(status)) return 'running';
  return 'other';
}

function filterReportsForCenter(rows = latestJobs) {
  const query = String(reportFilters.query || '').trim().toLowerCase();
  return (Array.isArray(rows) ? rows : [])
    .filter(job => {
      if (reportFilters.status !== 'all' && reportCenterStatusKey(job) !== reportFilters.status) return false;
      const failureType = reportsFailureType(job) || 'NONE';
      if (reportFilters.failureType !== 'all' && failureType !== reportFilters.failureType) return false;
      if (!query) return true;
      return [job.job_id, job.jobId, job.target_task_name, job.current_task_name, job.task_name, job.file, job.module, job.app_name, job.appName]
        .filter(Boolean).join(' ').toLowerCase().includes(query);
    })
    .sort((a, b) => String(b.finished_at || b.updated_at || b.started_at || b.created_at || '').localeCompare(String(a.finished_at || a.updated_at || a.started_at || a.created_at || '')));
}

function setReportCenterFilter(key, value) {
  if (!['query', 'status', 'failureType'].includes(key)) return;
  reportFilters[key] = String(value || '');
  reportFilters.page = 1;
  if (key !== 'query') {
    showReportsCenter();
    return;
  }
  if (reportFilterTimer) clearTimeout(reportFilterTimer);
  reportFilterTimer = setTimeout(() => {
    if (activeWorkflow !== 'reports') return;
    showReportsCenter();
    const input = document.getElementById('report-center-search');
    input?.focus();
    input?.setSelectionRange(input.value.length, input.value.length);
  }, 180);
}

function setReportCenterPage(page) {
  reportFilters.page = Math.max(1, Number(page) || 1);
  showReportsCenter();
}

function reportCenterPager(total, page, pages) {
  if (total <= REPORT_PAGE_SIZE) return '';
  return `<div class="management-pager report-center-pager">
    <span>共 ${total} 条 · 第 ${page}/${pages} 页</span>
    <div>
      <button class="btn-sm" onclick="setReportCenterPage(${page - 1})" ${page <= 1 ? 'disabled' : ''}>上一页</button>
      <button class="btn-sm" onclick="setReportCenterPage(${page + 1})" ${page >= pages ? 'disabled' : ''}>下一页</button>
    </div>
  </div>`;
}

function showReportsCenter() {
  const area = document.getElementById('editor-area');
  if (!area) return;
  activeWorkspaceMode = 'reports';
  const ov = reportsOverview();
  const filteredJobs = filterReportsForCenter();
  const totalPages = Math.max(1, Math.ceil(filteredJobs.length / REPORT_PAGE_SIZE));
  reportFilters.page = Math.min(Math.max(1, reportFilters.page), totalPages);
  const start = (reportFilters.page - 1) * REPORT_PAGE_SIZE;
  const jobs = filteredJobs.slice(start, start + REPORT_PAGE_SIZE);
  area.className = 'editor-area';
  area.innerHTML = `
    <div class="review-page reports-page">
      <div class="review-head">
        <div>
          <div class="workflow-kicker">REPORTS · 历史执行报告</div>
          <h2>执行报告</h2>
          <p>查看任务执行结果、失败归因和报告链接，失败任务可一键进入 AI 修复或缺陷草稿。</p>
        </div>
        <div class="review-actions">
          <button class="btn-sm primary" onclick="loadJobs(true).then(()=>showReportsCenter())">刷新报告</button>
          <button class="btn-sm" onclick="showReportCleanupCenter && showReportCleanupCenter()">报告清理</button>
        </div>
      </div>

      <div class="report-overview">
        <div class="report-overview-card">
          <span class="report-overview-label">总任务数</span>
          <strong class="report-overview-value">${ov.total}</strong>
        </div>
        <div class="report-overview-card success">
          <span class="report-overview-label">成功</span>
          <strong class="report-overview-value">${ov.success}</strong>
        </div>
        <div class="report-overview-card danger">
          <span class="report-overview-label">失败</span>
          <strong class="report-overview-value">${ov.failed}</strong>
        </div>
        <div class="report-overview-card warn">
          <span class="report-overview-label">失败率</span>
          <strong class="report-overview-value">${ov.failRate}%</strong>
        </div>
        <div class="report-overview-card">
          <span class="report-overview-label">最近运行</span>
          <strong class="report-overview-value report-overview-time">${escapeHtml(ov.lastRunAt || '—')}</strong>
        </div>
      </div>

      <div class="management-filter-bar">
        <input id="report-center-search" type="search" value="${escapeHtml(reportFilters.query)}" placeholder="搜索任务、应用或报告" oninput="setReportCenterFilter('query', this.value)">
        <select onchange="setReportCenterFilter('status', this.value)">
          <option value="all" ${reportFilters.status === 'all' ? 'selected' : ''}>全部状态</option>
          <option value="success" ${reportFilters.status === 'success' ? 'selected' : ''}>成功</option>
          <option value="failed" ${reportFilters.status === 'failed' ? 'selected' : ''}>失败</option>
          <option value="running" ${reportFilters.status === 'running' ? 'selected' : ''}>进行中</option>
        </select>
        <select onchange="setReportCenterFilter('failureType', this.value)">
          <option value="all" ${reportFilters.failureType === 'all' ? 'selected' : ''}>全部归因</option>
          <option value="PRODUCT_BUG" ${reportFilters.failureType === 'PRODUCT_BUG' ? 'selected' : ''}>产品缺陷</option>
          <option value="SCRIPT_ISSUE" ${reportFilters.failureType === 'SCRIPT_ISSUE' ? 'selected' : ''}>脚本问题</option>
          <option value="ENV_ISSUE" ${reportFilters.failureType === 'ENV_ISSUE' ? 'selected' : ''}>环境问题</option>
          <option value="UNKNOWN" ${reportFilters.failureType === 'UNKNOWN' ? 'selected' : ''}>待确认</option>
        </select>
        <span class="management-filter-count">显示 ${jobs.length}/${filteredJobs.length} 条</span>
        <span class="management-filter-scope">${escapeHtml(jobHistoryScopeText())}</span>
      </div>

      <div class="report-list-wrap">
        <table class="report-table">
          <thead>
            <tr>
              <th>任务</th>
              <th>状态</th>
              <th>模块</th>
              <th>执行时间</th>
              <th>失败类型</th>
              <th>报告 / 草稿</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            ${jobs.length ? jobs.map(job => renderReportRow(job)).join('') : `<tr><td colspan="7">${renderEmptyState('reports')}</td></tr>`}
          </tbody>
        </table>
      </div>
      ${reportCenterPager(filteredJobs.length, reportFilters.page, totalPages)}
    </div>
  `;
  const path = document.getElementById('toolbar-path');
  if (path) path.innerHTML = '<span>📊</span> 执行报告';
  const help = document.getElementById('toolbar-help');
  if (help) help.textContent = '查看任务结果与失败归因，失败任务可进入 AI 修复或生成缺陷草稿。';
  const info = document.getElementById('file-info');
  if (info) info.textContent = '执行报告';
  if (typeof updateToolbarState === 'function') updateToolbarState('执行报告');
}

function renderReportRow(job) {
  const jobId = job.job_id || job.jobId || '';
  const status = String(job.status || '').toLowerCase();
  const isFailed = ['failed', 'timeout', 'cancelled', 'error'].includes(status);
  const isSuccess = ['success', 'succeeded', 'completed'].includes(status);
  const time = (job.finished_at || job.updated_at || job.started_at || '').replace('T', ' ').slice(0, 19);
  const ft = reportsFailureType(job);
  const hasDraft = reportsHasRepairDraft(job);
  const reportUrl = job.report_url || job.reportUrl || job.sonic_report_url || '';
  const taskName = job.target_task_name || job.current_task_name || job.file || jobId || '任务';
  return `
    <tr class="report-row ${isFailed ? 'failed' : (isSuccess ? 'success' : '')}">
      <td><div class="report-cell-task">${escapeHtml(String(taskName).slice(0, 60))}</div><div class="report-cell-id">${escapeHtml(jobId.slice(0, 20))}</div></td>
      <td><span class="status-pill ${isSuccess ? 'success' : (isFailed ? 'warn' : '')}">${escapeHtml(jobStatusText(job.status || ''))}</span></td>
      <td>${escapeHtml(job.module || '-')}</td>
      <td class="report-cell-time">${escapeHtml(time || '-')}</td>
      <td>${ft ? `<span class="failure-type-chip failure-${ft.toLowerCase()}">${escapeHtml(ft)}</span>` : '<span class="report-muted">—</span>'}</td>
      <td class="report-cell-links">
        ${reportUrl ? `<a class="job-link" href="${escapeHtml(reportUrl)}" target="_blank">报告</a>` : '<span class="report-muted">无报告</span>'}
        ${hasDraft ? '<span class="status-pill success" style="margin-left:6px;">已有修复草稿</span>' : ''}
      </td>
      <td class="report-cell-actions">
        ${isFailed ? `<button class="btn-sm" onclick="analyzeFailureFromJob(${jsArg(jobId)}, {renderPage:true})">查看失败分析</button>` : ''}
        ${isFailed ? `<button class="btn-sm" onclick="generateBugDraftForJob(${jsArg(jobId)})">生成缺陷草稿</button>` : ''}
        ${isFailed ? `<button class="btn-sm primary" onclick="openAiRepairForJob(${jsArg(jobId)})">去 AI 修复</button>` : ''}
        <button class="btn-sm" onclick="focusJob(${jsArg(jobId)})">定位</button>
      </td>
    </tr>
  `;
}
