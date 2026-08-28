const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('playwright');

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
const baselines = Array.from({ length: 51 }, (_, index) => ({
  id: `baseline-${index + 1}`, project_id: 'project-1', case_id: `case-${index + 1}`,
  case_version_id: `case-version-${index + 1}`, environment_revision_id: 'environment-revision-1',
  source_revision_id: 'source-revision-1', endpoint_id: endpoint.id, status: 'active',
  case_name: `收藏基线 ${index + 1}`, case_version: 1, priority: 'P0', origin: 'manual',
  app_package: 'com.example.school', app_name: '校园应用', business: 'campus',
  method: endpoint.method, path: endpoint.path, endpoint_summary: endpoint.summary,
  tags: ['我的收藏'], group_name: '我的收藏', adoption_reason: '真实调试通过', adopted_at: '2026-08-25T08:00:00Z',
}));

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
    if (url.pathname === '/api/api-testing/v1/executions/execution-1/sse-ticket' && req.method === 'POST') {
      return sendJson(res, { ticket: 'visual-sse-ticket' });
    }
    if (url.pathname === '/api/api-testing/v1/executions/execution-1/events' && req.method === 'GET') {
      res.writeHead(200, {
        'content-type': 'text/event-stream; charset=utf-8',
        'cache-control': 'no-cache',
        connection: 'close',
      });
      return res.end('id: 1\nevent: execution_finished\ndata: {"execution_id":"execution-1","state":"DONE"}\n\n');
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
  await loggedOutPage.goto(`${url}#/baselines`, { waitUntil: 'domcontentloaded' });
  await loggedOutPage.waitForURL(current => current.pathname === '/task-manager.html');
  const returnTo = new URL(loggedOutPage.url()).searchParams.get('return_to');
  if (returnTo !== '/api-test/#/baselines') {
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
    await page.getByRole('heading', { name: '接口测试工作台' }).waitFor();
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
    await assertNoHorizontalOverflow(page, 'baselines desktop');
    await page.screenshot({ path: path.join(ARTIFACTS, 'baselines-desktop.png'), fullPage: true });
    if (errors.length) throw new Error(`browser errors: ${errors.join(' | ')}`);
    console.log(JSON.stringify({ ok: true, url, screenshots: ['workflow-preview-desktop.png', 'workflow-preview-mobile.png', 'workbench-desktop.png', 'workbench-compact-desktop.png', 'workbench-tablet.png', 'workbench-mobile.png', 'baselines-mobile.png', 'settings-mobile.png', 'baselines-desktop.png'] }));
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {
  console.error(error && error.stack || error);
  process.exit(1);
});
