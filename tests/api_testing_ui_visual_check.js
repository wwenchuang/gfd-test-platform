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
  request: { method: 'GET', path: endpoint.path, service: 'default', path_params: {}, query: {}, headers: { Biz: '{{Biz}}' }, cookies: {}, body: null },
  data_rows: [], assertions: [{ type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true }],
  extractions: [], dependencies: [], processing: { pre: [], post: [] },
};

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
    if (url.pathname === '/api/api-testing/v1/workspace' && req.method === 'GET') {
      return sendJson(res, { workspace: { project_id: 'project-1', source_revision_id: 'source-revision-1', environment_revision_id: 'environment-revision-1' } });
    }
    if (url.pathname === '/api/api-testing/v1/workspace' && req.method === 'PUT') {
      return sendJson(res, { workspace: { project_id: 'project-1', source_revision_id: 'source-revision-1', environment_revision_id: 'environment-revision-1' } });
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
    if (url.pathname === '/api/api-testing/v1/endpoints') {
      return sendJson(res, { endpoints: [endpoint,
        { ...endpoint, id: 'endpoint-favorite-add', method: 'POST', path: '/print3d/api/v1/favorite/add', summary: '添加收藏' },
        { ...endpoint, id: 'endpoint-favorite-cancel', method: 'POST', path: '/print3d/api/v1/favorite/cancel', summary: '取消收藏' },
      ] });
    }
    if (url.pathname === '/api/api-testing/v1/cases' && req.method === 'GET') return sendJson(res, { case_versions: [caseVersion] });
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
    if (url.pathname === '/api/api-testing/v1/ai-jobs/latest') return sendJson(res, { job: null });
    if (url.pathname === '/api/api-testing/v1/executions' && req.method === 'POST') {
      return sendJson(res, { execution: { id: 'execution-1', state: 'QUEUED', case_statuses: [], case_results: [], summary: {} } }, 202);
    }
    if (url.pathname === '/api/api-testing/v1/executions/execution-1') {
      return sendJson(res, { execution: {
        id: 'execution-1', state: 'DONE', case_statuses: ['PASSED'], summary: { passed: 1 },
        case_results: [{ execution_case_id: 'execution-case-1', case_version_id: caseVersion.id, endpoint_id: endpoint.id, status: 'PASSED', failure_category: '', duration_ms: 48, sanitized_result: { sanitized_request: { method: 'GET', url: 'https://example.test/favorite/list' }, sanitized_response: { status_code: 200, body: '{"code":0}' }, assertions: [{ passed: true }], trace: [{ phase: 'request', message: '请求已发送' }] } }],
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
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.addInitScript(() => sessionStorage.setItem('sessionToken', 'visual-api-test-token'));
  try {
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: '接口测试工作台' }).waitFor();
    await page.getByRole('button', { name: '我的收藏列表' }).click();
    const desktopBoxes = await Promise.all(['.endpoint-tree', '.design-center', '.ai-assistant'].map(selector => page.locator(selector).boundingBox()));
    if (desktopBoxes.some(box => !box) || !(desktopBoxes[0].x < desktopBoxes[1].x && desktopBoxes[1].x < desktopBoxes[2].x)) {
      throw new Error(`desktop columns are not ordered: ${JSON.stringify(desktopBoxes)}`);
    }
    await assertNoHorizontalOverflow(page, 'desktop');
    await page.screenshot({ path: path.join(ARTIFACTS, 'workbench-desktop.png'), fullPage: true });

    await page.getByRole('button', { name: '保存并调试' }).click();
    await page.getByRole('dialog', { name: '在线调试' }).waitFor();
    await page.getByText('PASSED', { exact: true }).waitFor();
    await page.keyboard.press('Escape');
    if (await page.getByRole('dialog', { name: '在线调试' }).count()) throw new Error('Escape did not close the debug dialog');

    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: '我的收藏列表' }).click();
    const mobileBoxes = await Promise.all(['.endpoint-tree', '.design-center', '.ai-assistant'].map(selector => page.locator(selector).boundingBox()));
    if (mobileBoxes.some(box => !box) || !(mobileBoxes[0].y < mobileBoxes[1].y && mobileBoxes[1].y < mobileBoxes[2].y)) {
      throw new Error(`mobile panels are not stacked: ${JSON.stringify(mobileBoxes)}`);
    }
    await assertNoHorizontalOverflow(page, 'mobile');
    await page.screenshot({ path: path.join(ARTIFACTS, 'workbench-mobile.png'), fullPage: true });
    if (errors.length) throw new Error(`browser errors: ${errors.join(' | ')}`);
    console.log(JSON.stringify({ ok: true, url, screenshots: ['workbench-desktop.png', 'workbench-mobile.png'] }));
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {
  console.error(error && error.stack || error);
  process.exit(1);
});
