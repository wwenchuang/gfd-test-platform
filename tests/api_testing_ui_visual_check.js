const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('playwright');
const { expect } = require('playwright/test');

const ROOT = path.resolve(__dirname, '..');
const APP_ROOT = path.join(ROOT, 'api-test');
const ARTIFACTS = process.env.VISUAL_ARTIFACTS_DIR
  ? path.resolve(process.env.VISUAL_ARTIFACTS_DIR)
  : path.join(__dirname, 'artifacts', 'api-testing');

const endpoint = {
  id: 'endpoint-favorite-list',
  revision_id: 'source-revision-1',
  operation_id: 'favoriteList',
  method: 'GET',
  path: '/print3d/api/v1/favorite/list',
  summary: '我的收藏列表',
  tags: ['我的收藏'],
  operation: { responses: { 200: { description: '成功' } } },
};
const caseVersion = {
  id: 'case-version-1', case_id: 'case-1', project_id: 'project-1', endpoint_id: endpoint.id,
  status: 'draft', origin: 'ai', version: 1, validation_summary: {},
  name: '查询我的收藏', purpose: '验证登录用户可以查询收藏列表', priority: 'P0',
  app_package: 'com.example.school', app_name: '校园应用', business: 'campus',
  request: { method: 'GET', path: endpoint.path, service: 'default', path_params: {}, query: {}, headers: { Biz: '{{Biz}}' }, cookies: {}, body: null },
  data_rows: [], assertions: [{ type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true }],
  extractions: [], dependencies: [], processing: { pre: [], post: [] },
};
const baselines = Array.from({ length: 51 }, (_, index) => {
  const baseline = {
    id: `baseline-${index + 1}`, project_id: 'project-1', case_id: `case-${index + 1}`,
    case_version_id: `case-version-${index + 1}`, environment_revision_id: 'environment-revision-1',
    source_revision_id: 'source-revision-1', endpoint_id: endpoint.id, status: 'active',
    case_name: `收藏基线 ${index + 1}`, case_version: 1, priority: 'P0', origin: 'manual',
    app_package: 'com.example.school', app_name: '校园应用', business: 'campus',
    method: endpoint.method, path: endpoint.path, endpoint_summary: endpoint.summary,
    tags: ['我的收藏'], group_name: '我的收藏', adoption_reason: '真实调试通过', adopted_at: '2026-08-25T08:00:00Z',
  };
  if (index === 0) return {
    ...baseline,
    case_name: '重新打印判断 - 创建后取消并清理完整业务链路验证',
    app_package: 'com.kfb.model', app_name: '智小白3D', business: 'home',
    method: 'POST',
    path: '/print3d/api/v1/printJob/createReprintWithCancellationAndResourceCleanup',
    endpoint_summary: '重新打印判断接口创建后取消并清理所有临时资源',
    group_name: 'API Test / 一次性',
  };
  if (index === 1) return {
    ...baseline,
    app_package: 'com.kfb.model', app_name: '智小白3D', business: 'shared',
    group_name: '设备共享',
  };
  return baseline;
});

function sendJson(res, data, status = 200) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify({ data }));
}

function serveStatic(urlPath, res) {
  const relative = urlPath === '/api-test/' ? 'index.html' : urlPath.slice('/api-test/'.length);
  const file = path.resolve(APP_ROOT, relative);
  if (!file.startsWith(APP_ROOT) || !fs.existsSync(file) || !fs.statSync(file).isFile()) return false;
  const contentType = file.endsWith('.html') ? 'text/html; charset=utf-8'
    : file.endsWith('.css') ? 'text/css; charset=utf-8'
      : 'application/javascript; charset=utf-8';
  res.writeHead(200, { 'content-type': contentType });
  res.end(fs.readFileSync(file));
  return true;
}

function createServer() {
  return http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname === '/favicon.ico') {
      res.writeHead(204);
      return res.end();
    }
    if (url.pathname.startsWith('/api-test/') && serveStatic(url.pathname, res)) return;
    if (url.pathname === '/api/auth/me') return sendJson(res, { ok: true, user: 'visual-user' });
    if (url.pathname === '/api/task-apps') {
      return sendJson(res, { apps: [
        {
          package: 'com.kfb.model',
          name: '智小白3D',
          enabled: true,
          business_lines: [
            { id: 'home', name: '家用', enabled: true },
            { id: 'shared', name: '共享', enabled: true },
          ],
        },
        {
          package: 'com.example.school',
          name: '校园应用',
          enabled: true,
          business_lines: [{ id: 'campus', name: '校园版', enabled: true }],
        },
      ] });
    }
    if (url.pathname === '/api/api-testing/v1/workspace' && req.method === 'GET') {
      return sendJson(res, { workspace: { project_id: 'project-1', source_revision_id: 'source-revision-1', environment_revision_id: 'environment-revision-1' } });
    }
    if (url.pathname === '/api/api-testing/v1/workspace' && req.method === 'PUT') {
      return sendJson(res, { workspace: { project_id: 'project-1', source_revision_id: 'source-revision-1', environment_revision_id: 'environment-revision-1' } });
    }
    if (url.pathname === '/api/api-testing/v1/tasks' && req.method === 'GET') {
      return sendJson(res, { tasks: [{
        id: 'task-1', project_id: 'project-1', source_revision_id: 'source-revision-1',
        environment_revision_id: 'environment-revision-1', name: '每日收藏链路回归', state: 'completed',
        selected_endpoint_ids: [endpoint.id], runnable_baseline_count: 1, latest_ai_job_id: null,
        latest_execution_id: 'execution-1',
        latest_execution_state: 'DONE',
        latest_execution_summary: { total: 3, passed: 2, failed: 1, broken: 0, skipped: 0, cancelled: 0 },
        latest_execution_at: '2026-08-26T10:00:00+08:00',
        summary: { total: 3, passed: 2, failed: 1, broken: 0, skipped: 0, cancelled: 0 },
        created_at: '2026-08-25T08:00:00Z', updated_at: '2026-08-26T08:30:00Z',
      }] });
    }
    if (url.pathname === '/api/api-testing/v1/tasks/active') return sendJson(res, { task: null });
    if (url.pathname === '/api/api-testing/v1/tasks' && req.method === 'POST') {
      return sendJson(res, { task: {
        id: 'task-1', project_id: 'project-1', source_revision_id: 'source-revision-1',
        environment_revision_id: 'environment-revision-1', name: '3D 项目接口测试', state: 'draft',
        selected_endpoint_ids: [endpoint.id], latest_ai_job_id: null, latest_execution_id: null,
        summary: {}, created_at: '', updated_at: '',
      } });
    }
    if (url.pathname === '/api/api-testing/v1/context-options') {
      return sendJson(res, {
        projects: [{ id: 'project-1', name: '3D 项目' }],
        source_revisions: [{ id: 'source-revision-1', source_id: 'source-1', project_id: 'project-1', name: 'Apifox 接口', revision_number: 3, endpoint_count: 3 }],
        environment_revisions: [{ id: 'environment-revision-1', environment_id: 'environment-1', project_id: 'project-1', name: '生产环境（新）- 腾讯云', revision: 2 }],
      });
    }
    if (url.pathname.startsWith('/api/api-testing/v1/endpoints/')) {
      const endpointId = url.pathname.split('/').pop();
      const rows = [endpoint,
        { ...endpoint, id: 'endpoint-favorite-add', method: 'POST', path: '/print3d/api/v1/favorite/add', summary: '添加收藏' },
        { ...endpoint, id: 'endpoint-favorite-cancel', method: 'POST', path: '/print3d/api/v1/favorite/cancel', summary: '取消收藏' },
      ];
      return sendJson(res, { endpoint: rows.find(item => item.id === endpointId) || endpoint });
    }
    if (url.pathname === '/api/api-testing/v1/endpoints') {
      return sendJson(res, { endpoints: [
        { ...endpoint, operation: {} },
        { ...endpoint, id: 'endpoint-favorite-add', method: 'POST', path: '/print3d/api/v1/favorite/add', summary: '添加收藏', operation: {} },
        { ...endpoint, id: 'endpoint-favorite-cancel', method: 'POST', path: '/print3d/api/v1/favorite/cancel', summary: '取消收藏', operation: {} },
      ] });
    }
    if (url.pathname === '/api/api-testing/v1/cases' && req.method === 'GET') return sendJson(res, { case_versions: [caseVersion] });
    if (url.pathname === '/api/api-testing/v1/baselines' && req.method === 'GET') return sendJson(res, { baselines });
    if (url.pathname === '/api/api-testing/v1/scheduled-jobs' && req.method === 'GET') return sendJson(res, { scheduled_jobs: [{
      id: 'scheduled-job-1', project_id: 'project-1', source_revision_id: 'source-revision-1',
      environment_revision_id: 'environment-revision-1', environment_id: 'environment-1', name: '每日收藏基线回归',
      target_type: 'baseline_group', target_ids: ['我的收藏'], schedule_type: 'cron', cron_expression: '0 10 * * *',
      effective_cron_expression: '0 10 * * *', scheduler_timezone: 'Asia/Shanghai', scheduler_utc_offset: '+08:00', environment_strategy: 'fixed_revision', enabled: true, notify_feishu: true,
      retry_count: 1, timeout_seconds: 1800, next_run_at: '2026-08-27T10:00:00+08:00',
      latest_run_at: '2026-08-26T10:00:00+08:00', latest_run_trigger: 'schedule', latest_execution_id: 'execution-1',
      latest_execution_state: 'DONE', latest_execution_summary: { total: 3, passed: 2, failed: 1, broken: 0, skipped: 0, cancelled: 0 },
      created_at: '2026-08-25T08:00:00Z', updated_at: '2026-08-26T08:30:00Z',
    }] });
    if (url.pathname === '/api/api-testing/v1/executions' && req.method === 'GET') return sendJson(res, { executions: [] });
    if (url.pathname === '/api/api-testing/v1/providers/apifox/credential' && req.method === 'GET') {
      return sendJson(res, { credential: { provider: 'apifox', configured: true, fingerprint: 'visual-check', updated_at: null } });
    }
    if (url.pathname === '/api/api-testing/v1/environments' && req.method === 'GET') return sendJson(res, { environments: [] });
    if (url.pathname === '/api/api-testing/v1/notifications/feishu' && req.method === 'GET') {
      return sendJson(res, { notification: { project_id: 'project-1', channel_type: 'feishu', name: 'API 基线报告', enabled: true, configured: true, fingerprint: 'visual-check', updated_at: '2026-08-26T08:30:00Z' } });
    }
    if (url.pathname === '/api/api-testing/v1/notifications/feishu/test' && req.method === 'POST') {
      return sendJson(res, { notification: { project_id: 'project-1', channel_type: 'feishu', sent: true, message: '飞书测试通知已发' } });
    }
    if (url.pathname === '/api/api-testing/v1/cases/case-1/versions' && req.method === 'POST') {
      return sendJson(res, { case_version: { ...caseVersion, id: 'case-version-2', version: 2 } });
    }
    if (url.pathname === '/api/api-testing/v1/environment-revisions/environment-revision-1') {
      return sendJson(res, { environment_revision: {
        id: 'environment-revision-1', revision_id: 'environment-revision-1', name: '生产环境（新）- 腾讯云', revision: 2,
        variables: { Biz: 'ZXB' }, services: { default: { name: 'default', base_url: 'https://example.test', unresolved: false } },
      } });
    }
    if (url.pathname === '/api/api-testing/v1/case-versions/case-version-2/validate' && req.method === 'POST') {
      return sendJson(res, { validation: { valid: true, errors: [], warnings: [] } });
    }
    if (url.pathname === '/api/api-testing/v1/workflow-steps/preview' && req.method === 'POST') {
      return sendJson(res, { preview: {
        status: 'PASSED', failure_category: '', error_message: '', trace: [{ stage: 'setup', index: 0, status: 'PASSED' }],
        target_index: 0, executed_index: 0, target_reached: true,
        response: { status_code: 200, body: { code: 0, data: { access_token: 'visual-real-token', modelSn: 'model-001' } } },
        fields: [
          { id: 'status_code:status_code', source: 'status_code', name: 'status_code', value: 200, value_type: 'number', sensitive: false, suggested_target: 'status_code' },
          { id: 'json_path:$.code', source: 'json_path', path: '$.code', name: 'code', value: 0, value_type: 'number', sensitive: false, suggested_target: 'code' },
          { id: 'json_path:$.data.access_token', source: 'json_path', path: '$.data.access_token', name: 'access_token', value: 'visual-real-token', value_type: 'string', sensitive: true, suggested_target: 'access_token' },
          { id: 'json_path:$.data.modelSn', source: 'json_path', path: '$.data.modelSn', name: 'modelSn', value: 'model-001', value_type: 'string', sensitive: false, suggested_target: 'modelSn' },
        ],
        truncated: false, available_variables: [], missing_variables: [],
      } });
    }
    if (url.pathname === '/api/api-testing/v1/ai-jobs/latest') return sendJson(res, { job: null });
    if (url.pathname === '/api/api-testing/v1/executions' && req.method === 'POST') {
      return sendJson(res, { execution: { id: 'execution-1', state: 'QUEUED', case_statuses: [], case_results: [], summary: {} } }, 202);
    }
    if (/^\/api\/api-testing\/v1\/executions\/execution-(1|drawer)\/sse-ticket$/.test(url.pathname) && req.method === 'POST') {
      return sendJson(res, { ticket: 'visual-sse-ticket' });
    }
    if (/^\/api\/api-testing\/v1\/executions\/execution-(1|drawer)\/events$/.test(url.pathname) && req.method === 'GET') {
      res.writeHead(200, {
        'content-type': 'text/event-stream; charset=utf-8',
        'cache-control': 'no-cache',
        connection: 'close',
      });
      const executionId = url.pathname.split('/').at(-2);
      return res.end(`id: 1\nevent: execution_finished\ndata: ${JSON.stringify({ execution_id: executionId, state: 'DONE' })}\n\n`);
    }
    if (url.pathname === '/api/api-testing/v1/executions/execution-1') {
      return sendJson(res, { execution: {
        id: 'execution-1', state: 'DONE', case_statuses: ['PASSED'], summary: { passed: 1 },
        case_results: [{ execution_case_id: 'execution-case-1', case_version_id: caseVersion.id, endpoint_id: endpoint.id, status: 'PASSED', failure_category: '', duration_ms: 48, sanitized_result: { sanitized_request: { method: 'GET', url: 'https://example.test/favorite/list' }, sanitized_response: { status_code: 200, body: '{"code":0}' }, assertions: [{ passed: true }], trace: [
          { phase: 'workflow_step', stage: 'setup', index: 0, name: '添加收藏', status: 'PASSED', request: { method: 'POST', path: '/favorite/add' }, response: { status_code: 200 }, assertions: [{ passed: true }], extracted_variables: { favoriteSn: '***' }, error_message: '' },
          { phase: 'workflow_step', stage: 'main', index: 0, name: '主体请求', status: 'PASSED', request: { method: 'GET', path: '/favorite/list' }, response: { status_code: 200 }, assertions: [{ passed: true }], extracted_variables: {}, error_message: '' },
        ] } }],
      } });
    }
    res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
    res.end('Not Found');
  });
}

async function assertNoHorizontalOverflow(page, label) {
  const metrics = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth }));
  if (metrics.scrollWidth > metrics.width + 1) throw new Error(`${label} horizontal overflow: ${JSON.stringify(metrics)}`);
}

async function assertLargeExecutionDrawer(page, url) {
  const results = Array.from({ length: 120 }, (_, index) => ({
    execution_case_id: `drawer-case-${index}`, case_version_id: `drawer-version-${index}`,
    endpoint_id: endpoint.id, case_name: `大量基线用例 ${index + 1}`, method: 'GET', path: `/fixture/${index}`,
    status: index === 0 ? 'FAILED' : 'PASSED', failure_category: index === 0 ? 'product_assertion' : '', duration_ms: 5,
    sanitized_result: { request: { method: 'GET', url: `https://example.test/fixture/${index}` },
      response: { status_code: 200, body: JSON.stringify({ code: 0, data: index === 119 ? `${'详细响应'.repeat(5000)}LARGE_RESPONSE_TAIL` : '详细响应'.repeat(200) }) },
      assertions: [{ type: 'status_code', passed: index !== 0, expected: index === 0 ? 201 : 200, actual: 200 }] },
  }));
  const execution = {
    id: 'execution-drawer', project_id: 'project-1', source_revision_id: 'source-revision-1',
    environment_revision_id: 'environment-revision-1', environment_name: '测试环境',
    state: 'DONE', execution_type: 'baseline', summary: { total: 120, passed: 119, failed: 1 },
    case_results: results, case_statuses: results.map(result => result.status), cancellation_requested: false,
  };
  await page.route('**/executions/execution-drawer', route => route.fulfill({ json: { data: { execution } } }));
  for (const [label, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 390, height: 844 }], ['short-mobile', { width: 390, height: 320 }], ['tiny-mobile', { width: 390, height: 260 }]]) {
    await page.setViewportSize(viewport);
    await page.goto(`${url}?drawer=${label}#/runs?executionId=execution-drawer`, { waitUntil: 'networkidle' });
    if (viewport.width <= 920) await page.waitForFunction(() => document.querySelector('.side-rail')?.getBoundingClientRect().right <= 0);
    await page.getByRole('button', { name: '测试报告', exact: true }).click();
    await expect(page.getByText('已优先定位 1 个问题')).toBeVisible();
    await expect(page.getByTestId('report-preview-case-row')).toHaveCount(1);
    await page.screenshot({ path: path.join(ARTIFACTS, `execution-report-focus-${label}.png`) });
    await page.getByTestId('execution-report-filter-skipped').click();
    await expect(page.getByText('当前筛选没有用例。')).toBeVisible();
    await page.getByTestId('execution-report-filter-cancelled').click();
    await expect(page.getByText('当前筛选没有用例。')).toBeVisible();
    await page.getByTestId('execution-report-filter-all').click();
    const reportPreview = page.locator('.execution-report-preview');
    await reportPreview.getByTestId('case-result-next').click();
    await reportPreview.getByTestId('case-result-next').click();
    await page.getByTestId('report-preview-case-row').last().click();
    const drawer = page.getByRole('dialog', { name: '执行详情', exact: true });
    await drawer.waitFor();
    await expect(drawer.locator('.case-evidence > header')).toContainText('大量基线用例 120');
    const resultSearch = drawer.getByTestId('case-result-search');
    await resultSearch.fill('大量基线用例 120');
    await expect(drawer.getByTestId('case-result-row')).toHaveCount(1);
    await resultSearch.fill('');
    const assertEvidenceVisible = async () => {
      const header = await drawer.locator('.case-evidence > header').boundingBox();
      const close = await drawer.getByRole('button', { name: '关闭详情' }).boundingBox();
      if (!header || header.y < 0 || header.y + header.height > viewport.height || !close || close.y < 0 || close.y + close.height > viewport.height) {
        throw new Error(`large drawer ${label} selected evidence and close must remain in view: ${JSON.stringify({ header, close })}`);
      }
      for (const [container, title] of [['.execution-detail-list', '.active strong'], ['.execution-detail-evidence', '.case-evidence > header']]) {
        const pane = await drawer.locator(container).boundingBox();
        const content = await drawer.locator(container).locator(title).boundingBox();
        if (!pane || !content || content.y < pane.y - 1 || content.y + content.height > pane.y + pane.height + 1) {
          throw new Error(`large drawer ${label} title clipped inside ${container}: ${JSON.stringify({ pane, content })}`);
        }
      }
    };
    await assertEvidenceVisible();
    await drawer.locator('.case-evidence').evaluate(element => { element.parentElement.scrollTop = element.parentElement.scrollHeight; });
    await drawer.getByRole('button', { name: '上一页', exact: true }).click();
    await drawer.getByRole('button', { name: '上一页', exact: true }).click();
    await drawer.getByTestId('case-result-row').first().click();
    await expect(drawer.locator('.case-evidence > header')).toContainText('大量基线用例 1');
    await assertEvidenceVisible();
    await drawer.getByTestId('case-result-next').click();
    await drawer.getByTestId('case-result-next').click();
    await drawer.getByTestId('case-result-row').last().click();
    await expect(drawer.locator('.case-evidence > header')).toContainText('大量基线用例 120');
    await assertEvidenceVisible();
    const responseEvidence = drawer.getByTestId('response-evidence');
    await expect(responseEvidence).not.toHaveAttribute('open', '');
    await responseEvidence.locator('summary').click();
    const expandResponse = drawer.getByTestId('expand-response-evidence');
    await expect(expandResponse).toBeVisible();
    await expect(drawer).not.toContainText('LARGE_RESPONSE_TAIL');
    await expandResponse.click();
    await expect(drawer).toContainText('LARGE_RESPONSE_TAIL');
    await expect(expandResponse).toContainText('恢复精简预览');
    await expandResponse.click();
    await expect(drawer).not.toContainText('LARGE_RESPONSE_TAIL');
    await assertNoHorizontalOverflow(page, `large drawer ${label}`);
    await page.screenshot({ path: path.join(ARTIFACTS, `execution-drawer-${label}.png`) });
    await page.keyboard.press('Escape');
    await drawer.waitFor({ state: 'hidden' });
  }
  await page.unroute('**/executions/execution-drawer');
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(url, { waitUntil: 'networkidle' });
}

async function assertBaselineSelectionReadable(page, label) {
  const diagnostic = await page.getByTestId('baseline-selection-summary').evaluate(element => {
    const style = getComputedStyle(element);
    const metrics = Array.from(element.querySelectorAll('.baseline-selection-metric')).map(metric => {
      const strong = metric.querySelector('strong');
      const copy = metric.querySelector('span');
      return {
        metricWidth: Math.round(metric.getBoundingClientRect().width),
        numberHeight: Math.round(strong?.getBoundingClientRect().height || 0),
        labelHeight: Math.round(copy?.getBoundingClientRect().height || 0),
        labelLineHeight: Number.parseFloat(getComputedStyle(copy).lineHeight || '0'),
      };
    });
    return { whiteSpace: style.whiteSpace, width: Math.round(element.getBoundingClientRect().width), metrics };
  });
  if (diagnostic.whiteSpace !== 'nowrap') throw new Error(`${label} baseline summary may split: ${JSON.stringify(diagnostic)}`);
  for (const metric of diagnostic.metrics) {
    if (metric.metricWidth < 48 || metric.numberHeight > 24 || metric.labelHeight > metric.labelLineHeight + 2) {
      throw new Error(`${label} baseline metric wrapped or collapsed: ${JSON.stringify(diagnostic)}`);
    }
  }
}

async function assertBaselineRowsReadable(page, label) {
  const firstRow = page.locator('.baseline-row').first();
  await firstRow.waitFor();
  await expect(page.getByTestId('baseline-business-baseline-1')).toHaveText('家用');
  await expect(page.getByTestId('baseline-application-baseline-1')).toHaveText('智小白3D');
  await expect(page.getByTestId('baseline-maintenance-group-baseline-1')).toHaveText('API Test');
  await expect(page.getByTestId('baseline-one-time-baseline-1')).toHaveText('一次性');
  const diagnostic = await firstRow.evaluate(element => {
    const selectors = [
      '.baseline-case-title',
      '.baseline-case-copy > small',
      '.baseline-endpoint-copy b > span:last-child',
      '.baseline-endpoint-copy code',
      '.baseline-classification-copy',
    ];
    return selectors.map(selector => {
      const target = element.querySelector(selector);
      if (!target) return { selector, missing: true };
      const style = getComputedStyle(target);
      return {
        selector,
        display: style.display,
        whiteSpace: style.whiteSpace,
        textOverflow: style.textOverflow,
        clientWidth: Math.round(target.clientWidth),
        scrollWidth: Math.round(target.scrollWidth),
        clientHeight: Math.round(target.clientHeight),
        scrollHeight: Math.round(target.scrollHeight),
      };
    });
  });
  for (const item of diagnostic) {
    if (item.missing || item.display === 'none' || item.whiteSpace === 'nowrap' || item.textOverflow === 'ellipsis' || item.scrollWidth > item.clientWidth + 1) {
      throw new Error(`${label} baseline content is clipped: ${JSON.stringify(diagnostic)}`);
    }
  }
}

async function acceptExecutionConfirmation(page, click, label) {
  const dialogPromise = page.waitForEvent('dialog');
  const clickPromise = click();
  const dialog = await dialogPromise;
  const message = dialog.message();
  if (!message.includes('生产环境') || !message.includes('真实发送')) {
    await dialog.dismiss();
    throw new Error(`${label} confirmation does not explain the production effect: ${message}`);
  }
  await dialog.accept();
  await clickPromise;
}

async function assertCompactWorkbench(page, label, viewport, screenshotName) {
  await page.setViewportSize(viewport);
  await page.reload({ waitUntil: 'networkidle' });
  if (await page.getByTestId('context-project').isVisible()) throw new Error(`${label} context controls must be collapsed initially`);
  await page.getByTestId('context-toggle').click();
  if (!await page.getByTestId('context-project').isVisible()) throw new Error(`${label} context controls did not expand`);
  await page.getByTestId('context-toggle').click();
  await page.getByTestId('mobile-nav-toggle').click();
  const drawer = await page.locator('.side-rail.mobile-open').boundingBox();
  if (!drawer || drawer.width < 220) throw new Error(`${label} navigation drawer is too narrow: ${JSON.stringify(drawer)}`);
  if (!await page.getByTestId('nav-cases').getByText('用例管理', { exact: true }).isVisible()) throw new Error(`${label} navigation drawer must show labeled entries`);
  await page.getByRole('button', { name: '关闭导航' }).first().click();
  await page.getByTestId('endpoint-search').fill('我的收藏列表');
  await page.getByRole('button', { name: '我的收藏列表' }).click();
  await page.getByTestId('mobile-workbench-editor').waitFor();
  await page.waitForFunction(() => document.querySelector('[data-testid="mobile-workbench-editor"]')?.getAttribute('aria-selected') === 'true');
  const boxes = await Promise.all(['.endpoint-tree', '.design-center', '.ai-assistant'].map(selector => page.locator(selector).boundingBox()));
  if (boxes[0] || !boxes[1] || boxes[2]) {
    throw new Error(`${label} workbench must show only the selected editor pane: ${JSON.stringify(boxes)}`);
  }
  if (await page.getByTestId('mobile-workbench-editor').getAttribute('aria-selected') !== 'true') throw new Error(`${label} selecting an endpoint must activate the editor pane`);
  await assertNoHorizontalOverflow(page, label);
  await page.screenshot({ path: path.join(ARTIFACTS, screenshotName), fullPage: true });
}

async function openCompactPage(page, navigationLabel, heading, label) {
  await page.getByTestId('mobile-nav-toggle').click();
  await page.getByRole('link', { name: navigationLabel, exact: true }).click();
  await page.getByRole('heading', { name: heading, exact: true, level: 1 }).waitFor();
  await page.locator('.side-rail').waitFor({ state: 'hidden' });
  await assertNoHorizontalOverflow(page, label);
}

async function assertScheduledServerBlocks(page) {
  let blockedReason = 'blocked: permission or scope revoked';
  await page.route(url => url.pathname === '/api/api-testing/v1/scheduled-jobs', async route => {
    const response = await route.fetch();
    const payload = await response.json();
    payload.data.scheduled_jobs.forEach(job => { job.blocked_reason = blockedReason; });
    await route.fulfill({ response, json: payload });
  });
  await page.getByRole('link', { name: '定时任务', exact: true }).click();
  await page.getByTestId('scheduled-edit-scheduled-job-1').click();
  for (const [size, reason, message, viewport] of [
    ['desktop', 'blocked: permission or scope revoked', '保存任务配置的成员的执行权限或数据范围已撤销', { width: 1440, height: 900 }],
    ['mobile', 'blocked: scheduled target unavailable or outside current scope', '定时任务目标不可用，或已超出当前数据范围', { width: 390, height: 844 }],
  ]) {
    blockedReason = reason;
    await page.setViewportSize(viewport);
    if (size === 'mobile') {
      if (await page.locator('.side-rail.mobile-open').isVisible()) await page.getByRole('button', { name: '关闭导航' }).first().click();
      await page.locator('.side-rail').waitFor({ state: 'hidden' });
    }
    await page.getByTestId('scheduled-refresh').click();
    const refreshBox = await page.getByTestId('scheduled-refresh').boundingBox();
    if (!refreshBox || Math.abs(refreshBox.width - 34) > 1 || Math.abs(refreshBox.height - 34) > 1) throw new Error(`scheduled ${size} refresh must remain a 34px square: ${JSON.stringify(refreshBox)}`);
    const row = page.getByTestId('scheduled-row-scheduled-job-1');
    await row.getByRole('status').filter({ hasText: message }).waitFor();
    await page.getByTestId('scheduled-editor-blocked').filter({ hasText: message }).waitFor();
    if (!(await row.textContent()).includes('配置：已启用') || (await row.textContent()).includes('下次执行')) throw new Error(`blocked ${size} must distinguish configuration from dispatch`);
    await expect(page.getByTestId('scheduled-run-scheduled-job-1'), `blocked ${size} must allow an authorized retry`).toBeEnabled();
    const contentBox = await row.locator('.scheduled-row-main').boundingBox();
    const rowBox = await row.boundingBox();
    if (!contentBox || !rowBox || contentBox.width < Math.min(260, rowBox.width - 30)) throw new Error(`blocked ${size} status is squeezed by row actions: ${JSON.stringify(contentBox)}`);
    await assertNoHorizontalOverflow(page, `scheduled blocked ${size}`);
    await page.screenshot({ path: path.join(ARTIFACTS, `scheduled-blocked-${size}.png`), fullPage: true });
  }
  blockedReason = '';
  await page.getByTestId('scheduled-refresh').click();
  await page.getByTestId('scheduled-editor-blocked').waitFor({ state: 'hidden' });
  const rowText = await page.getByTestId('scheduled-row-scheduled-job-1').textContent();
  if (rowText.includes('执行已阻断') || !rowText.includes('下次执行')) throw new Error('cleared server reason must restore the schedule display');
}

async function assertAssetSyncClarity(page, url) {
  const project = { id: 'fox-1', name: '3D接口库', description: '', team_name: '' };
  await page.route('**/providers/apifox/projects', route => route.fulfill({ json: { data: { projects: [project] } } }));
  await page.route('**/providers/apifox/context', route => route.fulfill({ json: { data: { context: {
    project, branches: [{ id: 'main', name: '主分支', is_default: true }], cli_version: 'fixture',
    environments: [{ id: 'dev', name: '开发环境', services: [], variables: [] }, { id: 'prod', name: '生产环境（新）- 腾讯云', services: [], variables: [] }],
  } } } }));
  await page.route('**/sources/apifox/preview', route => route.fulfill({ json: { data: { preview: {
    source_preview: { id: 'preview-clarity', project_id: 'project-1', source_id: 'source-1', candidate_revision_id: 'next-source',
      previous_revision_id: 'source-revision-1', added_count: 1, changed_count: 1, removed_count: 0,
      changes: [
        { change_type: 'added', method: 'GET', path: '/print3d/api/v1/new/endpoint/with/a/long/path', changed_fields: [] },
        { change_type: 'changed', method: 'POST', path: '/print3d/api/v1/favorite/add', changed_fields: ['responses'] },
      ] }, environment_candidate: { name: '生产环境（新）- 腾讯云', secret_placeholders: [] },
  } } } }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${url}#/assets`, { waitUntil: 'networkidle' });
  const panel = page.getByTestId('apifox-sync-panel');
  if (await panel.getAttribute('open') !== null) throw new Error('saved assets should not start with synchronization expanded');
  await page.getByRole('link', { name: '进入工作台', exact: true }).waitFor();
  await page.screenshot({ path: path.join(ARTIFACTS, 'assets-saved-desktop.png'), fullPage: true });
  await page.getByTestId('open-apifox-sync').click();
  await page.getByRole('button', { name: '读取项目', exact: true }).click();
  await page.getByRole('button', { name: '读取环境', exact: true }).click();
  await page.getByTestId('apifox-environment').locator('option[value="prod"]').waitFor({ state: 'attached' });
  if (await page.getByTestId('apifox-environment').inputValue() !== 'prod') throw new Error('sync changed saved production environment to the first development environment');
  await page.getByRole('button', { name: '检查更新', exact: true }).click();
  await page.getByTestId('source-changes').getByText('响应定义', { exact: true }).waitFor();
  for (const [size, viewport] of [['desktop', { width: 1440, height: 900 }], ['compact', { width: 1024, height: 768 }], ['mobile', { width: 390, height: 844 }]]) {
    await page.setViewportSize(viewport);
    if (size === 'mobile') {
      if (await page.locator('.side-rail.mobile-open').isVisible()) await page.getByRole('button', { name: '关闭导航' }).first().click();
      // A desktop-to-mobile resize also animates the closed drawer for 180ms.
      await page.locator('.side-rail').waitFor({ state: 'hidden' });
    }
    await assertNoHorizontalOverflow(page, `asset sync ${size}`);
    await page.getByTestId('source-preview').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(ARTIFACTS, `assets-preview-${size}.png`), fullPage: size !== 'mobile' });
  }
  await page.getByTestId('source-change-search').fill('long/path');
  if (await page.getByTestId('source-changes').locator('tbody tr').count() !== 1) throw new Error('change search should find only the new endpoint');
  await page.getByTestId('apifox-environment').selectOption('dev');
  if (await page.getByTestId('source-preview').count()) throw new Error('changing the import environment must invalidate the old preview');
}

(async () => {
  fs.mkdirSync(ARTIFACTS, { recursive: true });
  const server = createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}/api-test/`;
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : {}),
  });
  const loggedOutPage = await browser.newPage();
  await loggedOutPage.goto(`${url}?verify=fixture#/baselines`, { waitUntil: 'domcontentloaded' });
  await loggedOutPage.waitForURL(current => current.pathname === '/task-manager.html');
  const returnTo = new URL(loggedOutPage.url()).searchParams.get('return_to');
  if (returnTo !== '/api-test/?verify=fixture#/baselines') {
    throw new Error(`logged-out baseline deep link was not preserved: ${loggedOutPage.url()}`);
  }
  await loggedOutPage.close();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('response', response => {
    if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
  });
  await page.addInitScript(() => sessionStorage.setItem('sessionToken', 'visual-api-test-token'));
  try {
    const taskAppsResponsePromise = page.waitForResponse(response => new URL(response.url()).pathname === '/api/task-apps');
    await page.goto(url, { waitUntil: 'networkidle' });
    const taskAppsPayload = await (await taskAppsResponsePromise).json();
    if (!JSON.stringify(taskAppsPayload).includes('校园版')) throw new Error(`business-line configuration response is incomplete: ${JSON.stringify(taskAppsPayload)}`);
    await assertLargeExecutionDrawer(page, url);
    await page.getByRole('heading', { name: '接口测试工作台' }).waitFor();
    await page.goto(`${url}#/?newTask=1`, { waitUntil: 'networkidle' });
    await page.getByText('从一个接口开始', { exact: true }).waitFor();
    for (const [label, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 390, height: 844 }]]) {
      await page.setViewportSize(viewport);
      if (label === 'mobile') await page.waitForFunction(() => document.querySelector('.side-rail')?.getBoundingClientRect().right <= 0);
      await assertNoHorizontalOverflow(page, `new workbench ${label}`);
      await page.screenshot({ path: path.join(ARTIFACTS, `workbench-start-${label}.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByTestId('endpoint-search').fill('我的收藏列表');
    await page.getByRole('button', { name: '我的收藏列表' }).click();
    await page.getByTestId('case-business-campus').waitFor();
    const desktopBoxes = await Promise.all(['.endpoint-tree', '.design-center', '.ai-assistant'].map(selector => page.locator(selector).boundingBox()));
    if (desktopBoxes.some(box => !box) || !(desktopBoxes[0].x < desktopBoxes[1].x && desktopBoxes[1].x < desktopBoxes[2].x)) {
      throw new Error(`desktop columns are not ordered: ${JSON.stringify(desktopBoxes)}`);
    }
    await page.getByTestId('add-setup-step').click();
    await page.getByTestId('endpoint-picker-search').fill('添加收藏');
    await page.getByTestId('endpoint-picker-option-endpoint-favorite-add').click();
    await page.getByTestId('setup-step-summary-0').waitFor();
    if (!await page.getByTestId('production-environment-warning').isVisible()) throw new Error('production environment warning is not visible');
    await acceptExecutionConfirmation(page, () => page.getByTestId('setup-preview-0').click(), 'workflow preview');
    const tokenInput = page.getByTestId('workflow-preview-sensitive-json_path:$.data.access_token');
    if (await tokenInput.getAttribute('type') !== 'password') throw new Error('sensitive preview value must be masked initially');
    await assertNoHorizontalOverflow(page, 'workflow preview desktop');
    await page.screenshot({ path: path.join(ARTIFACTS, 'workflow-preview-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(100);
    if (await page.locator('.side-rail.mobile-open').isVisible()) {
      await page.getByRole('button', { name: '关闭导航' }).first().click();
      await page.locator('.side-rail').waitFor({ state: 'hidden' });
    }
    await assertNoHorizontalOverflow(page, 'workflow preview mobile');
    await page.screenshot({ path: path.join(ARTIFACTS, 'workflow-preview-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByTestId('workflow-preview-reveal-json_path:$.data.access_token').click();
    if (await tokenInput.getAttribute('type') !== 'text') throw new Error('sensitive preview value cannot be revealed explicitly');
    await tokenInput.fill('visual-session-replacement');
    await page.getByTestId('workflow-preview-select-json_path:$.data.access_token').check();
    await page.getByTestId('workflow-preview-target-json_path:$.data.access_token').fill('accessToken');
    await page.getByTestId('workflow-preview-apply').click();
    await page.getByTestId('setup-reselect-endpoint-0').click();
    await page.getByTestId('endpoint-picker-search').fill('取消收藏');
    await page.getByTestId('endpoint-picker-option-endpoint-favorite-cancel').click();
    await page.locator('.workflow-endpoint-selection').getByText('/print3d/api/v1/favorite/cancel', { exact: true }).waitFor();
    await assertNoHorizontalOverflow(page, 'desktop');
    await page.screenshot({ path: path.join(ARTIFACTS, 'workbench-desktop.png'), fullPage: true });

    await page.setViewportSize({ width: 1024, height: 768 });
    await assertNoHorizontalOverflow(page, 'compact desktop');
    await page.screenshot({ path: path.join(ARTIFACTS, 'workbench-compact-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });

    await acceptExecutionConfirmation(page, () => page.getByRole('button', { name: '保存并调试' }).click(), 'case debug');
    await page.getByRole('dialog', { name: '在线调试' }).waitFor();
    await page.locator('.result-status').getByText('通过', { exact: true }).waitFor();
    await page.getByTestId('debug-trace').getByText('添加收藏', { exact: true }).waitFor();
    await page.keyboard.press('Escape');
    if (await page.getByRole('dialog', { name: '在线调试' }).count()) throw new Error('Escape did not close the debug dialog');

    await assertCompactWorkbench(page, 'tablet', { width: 768, height: 1024 }, 'workbench-tablet.png');
    await assertCompactWorkbench(page, 'mobile', { width: 390, height: 844 }, 'workbench-mobile.png');
    const compactPages = [
      ['接口资产', '接口资产', 'assets mobile'],
      ['用例管理', '用例管理', 'cases mobile'],
      ['任务管理', '任务管理', 'tasks mobile'],
      ['基线用例', '基线用例', 'baselines mobile'],
      ['定时任务', '定时任务', 'scheduled jobs mobile'],
      ['执行记录', '执行记录', 'runs mobile'],
      ['测试报告', '项目测试报告', 'reports mobile'],
      ['环境配置', '项目环境', 'settings mobile'],
    ];
    for (const [navigationLabel, heading, label] of compactPages) {
      await openCompactPage(page, navigationLabel, heading, label);
      if (navigationLabel === '基线用例') {
        await assertBaselineSelectionReadable(page, 'baselines mobile');
        await assertBaselineRowsReadable(page, 'baselines mobile');
        const action = page.locator('.baseline-row-actions').first();
        await action.waitFor();
        if (!await action.isVisible()) {
          const diagnostic = await action.count() ? await action.evaluate(element => {
            const style = getComputedStyle(element);
            return { display: style.display, visibility: style.visibility, width: element.getBoundingClientRect().width, html: element.outerHTML };
          }) : { count: 0 };
          throw new Error(`baseline row actions are hidden on mobile: ${JSON.stringify(diagnostic)}`);
        }
        if (!await page.getByTestId('baseline-page-next').isVisible()) throw new Error('baseline pagination is hidden on mobile');
        await page.screenshot({ path: path.join(ARTIFACTS, 'baselines-mobile.png'), fullPage: true });
      }
      if (navigationLabel === '任务管理') {
        await page.getByTestId('task-list-item-task-1').click();
        await page.getByTestId('task-latest-execution').click();
        await page.getByRole('heading', { name: '执行记录', exact: true, level: 1 }).waitFor();
      }
      if (navigationLabel === '定时任务') {
        await page.getByTestId('scheduled-latest-execution-scheduled-job-1').click();
        await page.getByRole('heading', { name: '执行记录', exact: true, level: 1 }).waitFor();
      }
      if (navigationLabel === '环境配置') {
        await page.getByTestId('feishu-test').click();
        await page.getByText('飞书测试通知已发', { exact: true }).waitFor();
      }
    }
    await page.screenshot({ path: path.join(ARTIFACTS, 'settings-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole('link', { name: '基线用例', exact: true }).click();
    await page.getByRole('heading', { name: '基线用例', exact: true, level: 1 }).waitFor();
    await assertBaselineSelectionReadable(page, 'baselines desktop');
    await assertBaselineRowsReadable(page, 'baselines desktop');
    await assertNoHorizontalOverflow(page, 'baselines desktop');
    await page.screenshot({ path: path.join(ARTIFACTS, 'baselines-desktop.png'), fullPage: true });
    await assertScheduledServerBlocks(page);
    await assertAssetSyncClarity(page, url);
    if (errors.length) throw new Error(`browser errors: ${errors.join(' | ')}`);
    console.log(JSON.stringify({ ok: true, url, screenshots: ['execution-report-focus-desktop.png', 'execution-report-focus-mobile.png', 'execution-report-focus-short-mobile.png', 'execution-report-focus-tiny-mobile.png', 'execution-drawer-desktop.png', 'execution-drawer-mobile.png', 'execution-drawer-short-mobile.png', 'execution-drawer-tiny-mobile.png', 'workbench-start-desktop.png', 'workbench-start-mobile.png', 'workflow-preview-desktop.png', 'workflow-preview-mobile.png', 'workbench-desktop.png', 'workbench-compact-desktop.png', 'workbench-tablet.png', 'workbench-mobile.png', 'baselines-mobile.png', 'settings-mobile.png', 'baselines-desktop.png', 'scheduled-blocked-desktop.png', 'scheduled-blocked-mobile.png', 'assets-saved-desktop.png', 'assets-preview-desktop.png', 'assets-preview-compact.png', 'assets-preview-mobile.png'] }));
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {
  console.error(error && error.stack || error);
  process.exit(1);
});
