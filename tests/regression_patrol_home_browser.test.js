const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');

const origin = 'http://127.0.0.1';
const root = path.resolve(__dirname, '..');
const executionId = '1a775813-191d-44fa-8fb2-d8be08886731';
const priorExecutionId = '9b863caf-c7e7-4ab2-b284-a5ed672c78b5';
const item = {
  job_name: '家用业务每日回归', case_count: 297, state: 'failed', summary: '通过 296 条，失败 1 条。',
  expected_at: '2026-11-20T10:00:00+08:00', checked_at: '2026-11-20T10:15:00+08:00',
  inspected_at: '2026-11-20T02:13:55Z', inspected_execution_id: executionId,
  execution_id: executionId, investigation_status: 'limited',
};

async function openHome(t, {permissions = ['api.view', 'ui.view'], saved = '', mobile = false, simpleEnabled = true} = {}) {
  const browser = await chromium.launch();
  t.after(() => browser.close());
  const page = await browser.newPage({viewport: mobile ? {width: 390, height: 844} : {width: 1280, height: 900}});
  page.setDefaultTimeout(3500);
  const calls = [];
  const profile = {user_id: 'reader', username: 'reader', permissions, scope: {ui_apps: '*'}};
  let response = {json: {data: {items: [item]}}};
  await page.addInitScript(saved => {
    sessionStorage.setItem('sessionToken', 'patrol-browser');
    if (saved) sessionStorage.setItem('midscene_active_workflow', saved);
  }, saved);
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === '/task-manager.html' || /^\/(css|js|assets)\//.test(url.pathname)) {
      return route.fulfill({path: path.join(root, url.pathname)});
    }
    if (url.pathname === '/api/auth/me') return route.fulfill({json: {ok: true, user: 'reader', profile}});
    if (url.pathname === '/api/ui-executions/availability') return route.fulfill({json: {ok: true, enabled: simpleEnabled}});
    if (url.pathname === '/api/api-testing/v1/patrol/status') {
      calls.push([request.method(), request.headers().authorization]);
      return route.fulfill(response);
    }
    return route.fulfill({json: {ok: true, items: [], runs: [], reports: [], groups: [], modules: [], apps: []}});
  });
  await page.goto(origin + '/task-manager.html');
  await page.locator('#app').waitFor();
  return {page, calls, panel: page.getByRole('region', {name: '定时回归巡检'}), reply: value => { response = value; }};
}

test('fresh landing and workbench show the checked slot, investigation and exact execution', async t => {
  const {page, panel, calls} = await openHome(t, {mobile: true});
  await panel.getByText('家用业务每日回归', {exact: true}).waitFor();
  await panel.getByText('执行失败', {exact: true}).waitFor();
  assert.match(await panel.innerText(), /通过 296 条，失败 1 条。/);
  await panel.getByText('本次 297 条用例', {exact: true}).waitFor();
  assert.match(await panel.innerText(), /证据有限/);
  assert.equal(await panel.locator('time').first().getAttribute('datetime'), item.expected_at);
  await panel.getByText('1 项任务', {exact: true}).waitFor();
  const times = await panel.locator('time').evaluateAll(nodes => nodes.map(node => node.dateTime));
  assert.ok(times.includes(item.checked_at));
  assert.ok(times.includes(item.inspected_at));
  assert.equal(await panel.getByRole('link').getAttribute('href'), '/api-test/#/runs?executionId=' + executionId);
  const box = await panel.boundingBox();
  assert.ok(box.x >= 0 && box.x + box.width <= 391);
  assert.deepEqual(calls, [['GET', 'Bearer patrol-browser']]);
  await page.getByRole('button', {name: '测试工作台', exact: true}).click();
  await page.getByRole('heading', {name: '从这里开始测试'}).waitFor();
  await panel.getByText('家用业务每日回归', {exact: true}).waitFor();
  assert.equal(await panel.count(), 1);
});

test('refresh replaces rows, provides an empty state, and clears data after permission errors', async t => {
  const {panel, reply} = await openHome(t, {saved: 'ai_practice'});
  await panel.getByText('家用业务每日回归', {exact: true}).waitFor();
  const refresh = panel.getByRole('button', {name: '刷新巡检状态'});
  reply({json: {data: {items: [{...item, state: 'missed', execution_id: null, inspected_execution_id: null, inspected_at: null, investigation_status: null, summary: '本次没有定时执行记录。'}]}}});
  await refresh.click();
  await panel.getByText('未按时启动', {exact: true}).waitFor();
  assert.equal(await panel.getByRole('link').count(), 0);
  reply({json: {data: {items: []}}});
  await refresh.click();
  await panel.getByText('当前账号范围内没有配置巡检的定时回归。').waitFor();
  reply({status: 403, json: {error: 'forbidden'}});
  await refresh.click();
  await panel.getByText('当前账号没有巡检查看权限。').waitFor();
  reply({status: 503, json: {}});
  await refresh.click();
  await panel.getByText('巡检状态暂时无法读取，请稍后刷新。').waitFor();
});

test('UI-only workbench makes no patrol request', async t => {
  const {page, panel, calls} = await openHome(t, {permissions: ['ui.view'], saved: 'ai_practice'});
  await page.getByRole('heading', {name: '从这里开始测试'}).waitFor();
  assert.equal(await panel.isVisible(), false);
  assert.equal(calls.length, 0);
});

test('a finished execution with an older patrol receipt links each run separately', async t => {
  const {panel, reply} = await openHome(t, {permissions: ['api.view']});
  await panel.getByText('家用业务每日回归', {exact: true}).waitFor();
  reply({json: {data: {items: [{...item, state: 'completed', summary: '通过 297 条。',
    inspected_at: '2026-11-19T02:05:00Z', inspected_execution_id: priorExecutionId, investigation_status: null}]}}});
  await panel.getByRole('button', {name: '刷新巡检状态'}).click();
  await panel.getByText('本次巡检暂无回执', {exact: true}).waitFor();
  assert.equal(await panel.getByRole('link', {name: '查看本次执行'}).getAttribute('href'), '/api-test/#/runs?executionId=' + executionId);
  assert.equal(await panel.getByRole('link', {name: '查看上次巡检'}).getAttribute('href'), '/api-test/#/runs?executionId=' + priorExecutionId);
});

test('legacy Agent landing exposes the same patrol and clears the panel when leaving home', async t => {
  const {page, panel, calls} = await openHome(t, {simpleEnabled: false,
    permissions: ['ui.view', 'ui.execute', 'platform.configure', 'api.view']});
  await panel.getByText('家用业务每日回归', {exact: true}).waitFor();
  assert.equal(await page.locator('[data-workflow="dashboard"]').evaluate(node => node.classList.contains('active')), true);
  assert.equal(calls.length, 1);
  await page.evaluate(() => setActiveWorkflow('assets'));
  assert.equal(await panel.isVisible(), false);
  assert.equal(await page.locator('#regression-patrol article').count(), 0);
});

test('successful completed inspection, running and stale execution remain distinct', async t => {
  const {panel, reply, page} = await openHome(t);
  await panel.getByText('家用业务每日回归', {exact: true}).waitFor();
  const states = [['completed', '执行完成'], ['pending', '待执行'], ['running', '执行中'], ['overdue', '执行进度超时'], ['disabled', '已停用']];
  reply({json: {data: {items: states.map(([state]) => ({...item, job_name: '回归-' + state, state,
    investigation_status: state === 'completed' ? 'not_needed' : null}))}}});
  await panel.getByRole('button', {name: '刷新巡检状态'}).click();
  for (const [, label] of states) await panel.getByText(label, {exact: true}).waitFor();
  await panel.getByText('本次巡检已完成，无需调查', {exact: true}).waitFor();
  await panel.getByText('5 项任务', {exact: true}).waitFor();
  reply({status: 401, json: {}});
  await panel.getByRole('button', {name: '刷新巡检状态'}).click();
  await panel.getByText('登录已过期，请重新登录。').waitFor();
  assert.equal(await page.evaluate(() => Boolean(sessionStorage.getItem('sessionToken'))), true);
});
