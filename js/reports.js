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

function showReportsCenter() {
  const area = document.getElementById('editor-area');
  if (!area) return;
  activeWorkspaceMode = 'reports';
  const ov = reportsOverview();
  const jobs = (Array.isArray(latestJobs) ? latestJobs : []).slice(0, 200);
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
