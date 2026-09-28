const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const ROOT = path.resolve(__dirname, '..');
const READER = { user_id: 'u-reader', username: 'reader', display_name: '只读成员', status: 'active', is_superuser: false, permissions: ['ui.view'], scope: { ui_apps: [] }, must_change_password: false };
const ADMIN = { ...READER, user_id: 'u-admin', username: 'admin', is_superuser: true, permissions: ['auth.manage'] };
const event = (id = 'first', fields = {}) => ({ event_id: id, timestamp: 1790568000, actor: { kind: 'user', user_id: 'u-reader', username: 'reader', display_name: '只读成员' }, action: 'asset.view', resource_type: 'asset', resource_id: id, result: 'success', item_total: 0, item_captured: 0, items_complete: true, ...fields });
let server, browser, base;
before(async () => {
  server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const file = path.resolve(ROOT, '.' + (pathname === '/' ? '/task-manager.html' : pathname));
    if (!file.startsWith(ROOT + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' })[path.extname(file)] || 'application/octet-stream');
    fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true });
});
after(async () => { await browser?.close(); await new Promise(resolve => server?.close(resolve)); });
async function fixture(options = {}) {
  const context = await browser.newContext({ viewport: options.viewport || { width: 1366, height: 768 } });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const state = { profile: options.profile || READER, calls: [], errors: [], handler: null };
  page.on('pageerror', error => state.errors.push(error.message));
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    let data = { ok: true };
    if (url.pathname === '/api/auth/me') data = { ok: true, user: state.profile.username, profile: state.profile };
    else if (url.pathname.startsWith('/api/operations')) {
      state.calls.push(url);
      if (state.handler) return state.handler(route, url);
      data = { ok: true, events: [event()], total: 1, next_cursor: null, audit: {} };
    } else if (url.pathname === '/api/auth/audit') data.events = [];
    else if (url.pathname === '/api/modules') data = {};
    else if (url.pathname === '/api/task-apps') data.apps = [];
    else if (url.pathname === '/api/task-meta') data.meta = {};
    else if (url.pathname === '/api/jobs') data.jobs = [];
    else if (url.pathname === '/api/runners') data.runners = [];
    else if (url.pathname === '/api/agent/runs') data.runs = [];
    await route.fulfill({ json: data });
  });
  await context.addInitScript(({ ignoreAbort }) => {
    if (ignoreAbort) {
      const fetchRequest = window.fetch.bind(window);
      window.fetch = (url, options = {}) => {
        if (String(url).startsWith('/api/operations')) { const request = { ...options }; delete request.signal; return fetchRequest(url, request); }
        return fetchRequest(url, options);
      };
    }
    if (sessionStorage.getItem('operation-fixture')) return;
    sessionStorage.setItem('operation-fixture', '1');
    sessionStorage.setItem('sessionToken', 'fixture-secret-token');
    sessionStorage.setItem('user', 'reader');
    sessionStorage.setItem('midscene_active_workflow', 'assets');
  }, { ignoreAbort: options.ignoreAbort === true });
  await page.goto(base + '/task-manager.html');
  await page.locator('#app').waitFor({ state: 'visible' });
  return { page, state, context };
}
async function open(f) {
  await f.page.locator('#account-toggle').click();
  await f.page.getByRole('button', { name: '我的操作记录', exact: true }).click();
  await f.page.locator('.operation-history tbody tr').first().waitFor();
}
async function change(page, name, value) {
  const input = page.locator(`.operation-history [name="${name}"]`);
  await input.fill(value);
  await input.dispatchEvent('change');
}
async function waitText(page, text) { await page.locator('.operation-history').getByText(text, { exact: true }).waitFor(); }
async function until(predicate) {
  const deadline = Date.now() + 5000;
  while (!predicate()) { assert.ok(Date.now() < deadline, 'expected request was not received'); await new Promise(resolve => setTimeout(resolve, 10)); }
}
async function release(route, payload) { try { await route.fulfill({ json: payload }); } catch (_) { /* The browser may already have aborted this obsolete request. */ } }

test('every account has a real menu entry and own history uses apiRequest bearer transport', async () => {
  const f = await fixture();
  try {
    let auth;
    f.state.handler = async route => { auth = route.request().headers().authorization; await release(route, { ok: true, events: [event()], total: 1 }); };
    await open(f);
    assert.equal(auth, 'Bearer fixture-secret-token');
    assert.equal(f.state.calls[0].searchParams.get('limit'), '50');
    assert.equal(f.state.calls[0].searchParams.get('scope'), 'own');
    assert.equal(await f.page.locator('.operation-history [name="scope"] option').count(), 1);
    assert.equal(await f.page.locator('.operation-history').textContent().then(s => s.includes('fixture-secret-token')), false);
    assert.deepEqual(f.state.errors, []);
  } finally { await f.context.close(); }
});

test('cursor next/back and action filters reset the real server cursor', async () => {
  const f = await fixture();
  try {
    f.state.handler = async (route, url) => release(route, { ok: true, events: [event(url.searchParams.get('cursor') ? 'second-page' : 'first-page')], total: 75, next_cursor: url.searchParams.get('cursor') ? null : 90 });
    await open(f);
    await f.page.getByRole('button', { name: '下一页', exact: true }).click();
    await waitText(f.page, 'second-page');
    assert.equal(f.state.calls.at(-1).searchParams.get('cursor'), '90');
    await f.page.getByRole('button', { name: '上一页', exact: true }).click();
    await waitText(f.page, 'first-page');
    assert.equal(f.state.calls.at(-1).searchParams.has('cursor'), false);
    await f.page.getByRole('button', { name: '下一页', exact: true }).click(); await waitText(f.page, 'second-page');
    await change(f.page, 'action', 'runner.heartbeat'); await waitText(f.page, 'first-page');
    assert.equal(f.state.calls.at(-1).searchParams.get('action'), 'runner.heartbeat');
    assert.equal(f.state.calls.at(-1).searchParams.has('cursor'), false);
    assert.equal(await f.page.getByRole('button', { name: '上一页', exact: true }).isDisabled(), true);
  } finally { await f.context.close(); }
});

test('filters encode values, dates and backend failed result without local filtering', async () => {
  const f = await fixture();
  try {
    await open(f);
    await change(f.page, 'resource_id', 'asset & + /?');
    await f.page.waitForFunction(() => !document.querySelector('[data-history-refresh]').disabled);
    await change(f.page, 'resource_type', 'asset');
    await f.page.locator('[name="result"]').selectOption('failed');
    await change(f.page, 'from_ts', '2026-09-28T10:00');
    await change(f.page, 'to_ts', '2026-09-28T12:00');
    await f.page.waitForFunction(() => !document.querySelector('[data-history-refresh]').disabled);
    const q = f.state.calls.at(-1).searchParams;
    assert.equal(q.get('resource_id'), 'asset & + /?'); assert.equal(q.get('resource_type'), 'asset'); assert.equal(q.get('result'), 'failed');
    assert.ok(Number(q.get('from_ts')) > 0); assert.ok(Number(q.get('to_ts')) > Number(q.get('from_ts')));
    assert.equal(await f.page.locator('.operation-history tbody tr').count(), 1, 'server rows stay visible even when fixture ignores filters');
    assert.ok(f.state.calls.every(url => !url.href.includes('fixture-secret-token')));
  } finally { await f.context.close(); }
});

test('only is_superuser enables all-account scope and actor filtering; auth.manage does not', async () => {
  for (const profile of [ADMIN, { ...READER, permissions: ['auth.manage'] }]) {
    const f = await fixture({ profile });
    try {
      await open(f);
      assert.equal(await f.page.locator('[name="scope"] option[value="all"]').count(), profile.is_superuser ? 1 : 0);
      assert.equal(await f.page.locator('[name="actor_id"]').isVisible(), false);
      if (profile.is_superuser) {
        await f.page.locator('[name="scope"]').selectOption('all');
        await change(f.page, 'actor_id', 'u-other');
        await f.page.waitForFunction(() => !document.querySelector('[data-history-refresh]').disabled);
        assert.equal(f.state.calls.at(-1).searchParams.get('scope'), 'all'); assert.equal(f.state.calls.at(-1).searchParams.get('actor_id'), 'u-other');
        await f.page.locator('[name="scope"]').selectOption('own');
        await f.page.waitForFunction(() => !document.querySelector('[data-history-refresh]').disabled);
        assert.equal(f.state.calls.at(-1).searchParams.has('actor_id'), false);
      }
    } finally { await f.context.close(); }
  }
});

test('late filter responses and old batch details cannot repopulate a new query', async () => {
  const f = await fixture(); let held, detail;
  try {
    f.state.handler = async (route, url) => {
      if (url.pathname.endsWith('/items')) { detail = route; return; }
      if (url.searchParams.get('action') === 'old.view') { held = route; return; }
      await release(route, { ok: true, events: [event('current', { item_total: 2, item_captured: 2 })], total: 1 });
    };
    await open(f);
    await f.page.getByRole('button', { name: '批量明细', exact: true }).click();
    await f.page.waitForFunction(() => document.querySelector('[data-history-detail]').textContent.includes('加载'));
    await change(f.page, 'action', 'old.view');
    await until(() => held);
    await change(f.page, 'action', 'new.view'); await waitText(f.page, 'current');
    await release(held, { ok: true, events: [event('obsolete')], total: 1 });
    await release(detail, { ok: true, items: [{ resource_id: 'obsolete-detail', result: 'success' }], total: 2 });
    await f.page.waitForTimeout(80);
    assert.equal(await f.page.getByText('obsolete', { exact: true }).count(), 0);
    assert.equal(await f.page.getByText('obsolete-detail', { exact: true }).count(), 0);
    assert.equal(await f.page.locator('[data-history-detail]').textContent(), '');
  } finally { await f.context.close(); }
});

test('auth switch and logout clear rows, details, filters and pending replies', async () => {
  const f = await fixture(); let held;
  try {
    await open(f);
    f.state.handler = async route => { held = route; };
    await change(f.page, 'resource_id', 'old-filter');
    await until(() => held);
    const old = held;
    f.state.handler = async route => release(route, { ok: true, events: [event('new-account')], total: 1 });
    await f.page.evaluate(profile => acceptAuthSession({ token: 'other-token', user: profile.username, profile }), { ...READER, user_id: 'u-new', username: 'new' });
    await waitText(f.page, 'new-account');
    assert.equal(await f.page.locator('[name="resource_id"]').inputValue(), '');
    assert.equal(f.state.calls.at(-1).searchParams.has('cursor'), false);
    await release(old, { ok: true, events: [event('old-account')], total: 1 });
    await f.page.waitForTimeout(60); assert.equal(await f.page.getByText('old-account', { exact: true }).count(), 0);
    f.state.handler = async route => { held = route; };
    await f.page.locator('[data-history-refresh]').click();
    await until(() => held !== old);
    const logoutPending = held;
    await f.page.evaluate(() => clearAuthSession());
    await f.page.locator('#login-screen').waitFor({ state: 'visible' });
    await release(logoutPending, { ok: true, events: [event('logout-obsolete')], total: 1 });
    assert.equal(await f.page.locator('.operation-history').count(), 0);
  } finally { await f.context.close(); }
});

test('error retry and duplicate refresh remain bounded and truthful', async () => {
  const f = await fixture(); let held;
  try {
    await open(f);
    f.state.handler = route => route.fulfill({ status: 503, json: { ok: false, code: 'audit_unavailable' } });
    await f.page.locator('[data-history-refresh]').click();
    await f.page.locator('.operation-history [role="alert"]').waitFor();
    assert.equal(await f.page.locator('.operation-history tbody tr').count(), 0);
    f.state.handler = async route => { held = route; };
    const count = f.state.calls.length;
    await f.page.getByRole('button', { name: '重试', exact: true }).click();
    await f.page.evaluate(() => { showOperationHistory(); document.querySelector('[data-history-refresh]').click(); });
    await until(() => held);
    assert.equal(f.state.calls.length, count + 1);
    await release(held, { ok: true, events: [], total: 0, next_cursor: null });
    await f.page.getByText('暂无匹配的操作记录', { exact: true }).waitFor();
    await f.page.waitForTimeout(200); assert.equal(f.state.calls.length, count + 1);
  } finally { await f.context.close(); }
});

test('display escapes every event label and distinguishes Runner, unknown, initiator, accepted and partial', async () => {
  const f = await fixture();
  try {
    f.state.handler = route => release(route, { ok: true, events: [
      event('submitted', { actor: { kind: 'runner' }, initiator_user_id: 'u-reader', action: 'job.submit', result: 'accepted' }),
      event('<img src=x onerror="window.historyXss=1">', { actor: { kind: 'unknown' }, action: '<script>window.historyXss=1</script>', resource_type: '<svg onload=alert(1)>', result: 'partial' }),
      event('human', { actor: { kind: 'user', user_id: 'u-id', display_name: '<b>Human</b>' }, result: 'interrupted' }),
    ], total: 3, audit: { capture_degraded: true, unresolved: 4 } });
    await open(f);
    const text = await f.page.locator('.operation-history').textContent();
    assert.match(text, /Runner/); assert.match(text, /发起人 ID：u-reader/); assert.match(text, /归属未知/);
    assert.match(text, /已受理（尚未完成执行）/); assert.match(text, /部分成功/); assert.match(text, /已中断/); assert.match(text, /至少 4/);
    assert.ok(text.includes('<b>Human</b>')); assert.ok(text.includes('<img src=x'));
    assert.equal(await f.page.locator('.operation-history img, .operation-history script, .operation-history svg').count(), 0);
    assert.equal(await f.page.evaluate(() => window.historyXss), undefined);
  } finally { await f.context.close(); }
});

test('batch details page by next_cursor and show total versus captured without claiming completeness', async () => {
  const f = await fixture();
  try {
    f.state.handler = async (route, url) => {
      if (!url.pathname.endsWith('/items')) return release(route, { ok: true, events: [event('batch-id', { result: 'partial', item_total: 500, item_captured: 51, items_complete: false, capture_status: 'limit_exceeded' })], total: 1 });
      return release(route, { ok: true, items: [{ resource_id: url.searchParams.has('cursor') ? 'last-item' : 'first-item', result: 'accepted', reason_code: 'pending' }], total: 500, item_captured: 51, items_complete: false, capture_status: 'limit_exceeded', next_cursor: url.searchParams.has('cursor') ? null : 50 });
    };
    await open(f);
    assert.equal(f.state.calls.length, 1, 'details only fetched on demand');
    await f.page.getByRole('button', { name: '批量明细', exact: true }).click(); await waitText(f.page, 'first-item');
    assert.match(await f.page.locator('[data-history-detail]').textContent(), /共 500 项.*已采集 51 项/);
    assert.match(await f.page.locator('[data-history-detail]').textContent(), /采集不完整/);
    await f.page.getByRole('button', { name: '下一页明细', exact: true }).click(); await waitText(f.page, 'last-item');
    assert.equal(f.state.calls.at(-1).searchParams.get('cursor'), '50');
    assert.equal(f.state.calls.at(-1).searchParams.get('limit'), '50');
    assert.equal(await f.page.getByRole('button', { name: '下一页明细', exact: true }).isDisabled(), true);
    await f.page.getByRole('button', { name: '上一页明细', exact: true }).click(); await waitText(f.page, 'first-item');
    assert.equal(f.state.calls.at(-1).searchParams.has('cursor'), false);
  } finally { await f.context.close(); }
});

test('identity security audit remains available and links to business history', async () => {
  const f = await fixture({ profile: ADMIN });
  try {
    await f.page.evaluate(() => activateWorkflow('identity'));
    await f.page.getByRole('tab', { name: '操作记录', exact: true }).click();
    await f.page.getByRole('button', { name: '查看业务操作记录', exact: true }).click();
    await f.page.locator('.operation-history').waitFor();
    assert.equal(await f.page.evaluate(() => activeWorkflow), 'operation_history');
    assert.deepEqual(f.state.errors, []);
  } finally { await f.context.close(); }
});

for (const viewport of [{ width: 1366, height: 768 }, { width: 768, height: 480 }, { width: 390, height: 600 }]) {
  test(`real Chromium layout ${viewport.width}x${viewport.height} keeps filters and rows scrollable`, async () => {
    const f = await fixture({ viewport });
    try {
      await open(f);
      const result = await f.page.locator('.operation-history').evaluate(root => {
        const controls = Array.from(root.querySelectorAll('input, select, button')).filter(node => node.offsetParent);
        return { width: root.clientWidth, overflow: root.scrollWidth > root.clientWidth + 1, clipped: controls.some(node => node.getBoundingClientRect().width < 25), scroll: getComputedStyle(root).overflowY };
      });
      assert.ok(result.width > 250); assert.equal(result.overflow, false); assert.equal(result.clipped, false); assert.match(result.scroll, /auto|scroll/);
      await f.page.locator('[name="to_ts"]').scrollIntoViewIfNeeded(); assert.equal(await f.page.locator('[name="to_ts"]').isVisible(), true);
      const folder = path.join(ROOT, 'output/playwright/operation-history'); fs.mkdirSync(folder, { recursive: true });
      await f.page.screenshot({ path: path.join(folder, `${viewport.width}x${viewport.height}.png`) });
      assert.deepEqual(f.state.errors, []);
    } finally { await f.context.close(); }
  });
}

test('submitting the same filters repeatedly does not restart an in-flight duplicate query', async () => {
  const f = await fixture(); let held;
  try {
    await open(f);
    f.state.handler = async route => { held = route; };
    await change(f.page, 'action', 'asset.view');
    await until(() => held);
    const count = f.state.calls.length;
    await f.page.locator('.operation-history form').dispatchEvent('submit');
    await f.page.locator('[name="action"]').dispatchEvent('change');
    await f.page.waitForTimeout(100);
    assert.equal(f.state.calls.length, count, 'same pending filters must not issue duplicates');
    await release(held, { ok: true, events: [event('fresh')], total: 1 });
    await waitText(f.page, 'fresh');
  } finally { await f.context.close(); }
});

test('invalid time range is explained before requesting and clears prior query rows', async () => {
  const f = await fixture();
  try {
    await open(f);
    await change(f.page, 'from_ts', '2026-09-28T12:00');
    await f.page.waitForFunction(() => !document.querySelector('[data-history-refresh]').disabled);
    const count = f.state.calls.length;
    await change(f.page, 'to_ts', '2026-09-28T10:00');
    await f.page.getByText('结束时间不能早于开始时间', { exact: true }).waitFor();
    assert.equal(f.state.calls.length, count);
    assert.equal(await f.page.locator('.operation-history tbody tr').count(), 0);
    await f.page.locator('[data-history-refresh]').click();
    assert.equal(f.state.calls.length, count);
  } finally { await f.context.close(); }
});

test('late all-account response cannot replace own scope; view switch clears pending history', async () => {
  const f = await fixture({ profile: ADMIN }); let held;
  try {
    await open(f);
    f.state.handler = async (route, url) => {
      if (url.searchParams.get('scope') === 'all') { held = route; return; }
      await release(route, { ok: true, events: [event('own-view')], total: 1 });
    };
    await f.page.locator('[name="scope"]').selectOption('all');
    await until(() => held);
    await f.page.locator('[name="scope"]').selectOption('own'); await waitText(f.page, 'own-view');
    await release(held, { ok: true, events: [event('other-account')], total: 1 });
    await f.page.waitForTimeout(60); assert.equal(await f.page.getByText('other-account', { exact: true }).count(), 0);
    f.state.handler = async route => { held = route; };
    const count = f.state.calls.length;
    await f.page.locator('[data-history-refresh]').click();
    await until(() => f.state.calls.length !== count);
    await f.page.evaluate(() => activateWorkflow('identity'));
    await f.page.locator('#identity-center').waitFor();
    await release(held, { ok: true, events: [event('old-view')], total: 1 });
    assert.equal(await f.page.getByText('old-view', { exact: true }).count(), 0);
    f.state.handler = async route => release(route, { ok: true, events: [event('restored')], total: 1 });
    await f.page.evaluate(() => activateWorkflow('operation_history')); await waitText(f.page, 'restored');
    assert.equal(f.state.calls.at(-1).searchParams.has('cursor'), false);
  } finally { await f.context.close(); }
});

test('batch error retries its page, then pending details are discarded on identity switch', async () => {
  const f = await fixture(); let held;
  try {
    f.state.handler = async (route, url) => {
      if (url.pathname.endsWith('/items')) return route.fulfill({ status: 503, json: { ok: false } });
      await release(route, { ok: true, events: [event('batch', { item_total: 2 })], total: 1 });
    };
    await open(f); await f.page.getByRole('button', { name: '批量明细', exact: true }).click();
    await f.page.getByRole('button', { name: '重试明细', exact: true }).waitFor();
    f.state.handler = async route => release(route, { ok: true, items: [{ resource_id: 'retried-item', result: 'failed', reason_code: 'missing' }], total: 2, item_captured: 2, items_complete: true, next_cursor: 1 });
    await f.page.getByRole('button', { name: '重试明细', exact: true }).click(); await waitText(f.page, 'retried-item');
    f.state.handler = async route => { held = route; };
    await f.page.getByRole('button', { name: '下一页明细', exact: true }).click();
    await until(() => held);
    f.state.handler = async route => release(route, { ok: true, events: [event('new-person')], total: 1 });
    await f.page.evaluate(profile => acceptAuthSession({ token: 'switched-token', user: profile.username, profile }), { ...READER, user_id: 'u-switched', username: 'switched' });
    await waitText(f.page, 'new-person');
    await release(held, { ok: true, items: [{ resource_id: 'stale-private-item', result: 'success' }], total: 2 });
    await f.page.waitForTimeout(60);
    assert.equal(await f.page.getByText('stale-private-item', { exact: true }).count(), 0);
    assert.equal(await f.page.locator('[data-history-detail]').textContent(), '');
  } finally { await f.context.close(); }
});

test('registered backend verbs and API resource domains have readable business labels', async () => {
  const f = await fixture();
  try {
    const cases = [
      ['file.move', '文件 · 移动'], ['file.copy', '文件 · 复制'], ['file.rename', '文件 · 重命名'],
      ['yaml.inspect', 'YAML · 检查'], ['job.approve', '执行任务 · 批准'], ['job.reject', '执行任务 · 拒绝'],
      ['repair_draft.apply', '修复草稿 · 应用'], ['account.reset', '账号 · 重置'], ['unknown.request', '未知领域 · 请求'],
      ['figma.parse_async', 'Figma · 异步解析'], ['api_request.view', 'API 请求 · 查看'],
      ['api_collection.create', 'API 集合 · 创建'], ['api_load_run.stop', 'API 压测运行 · 停止'],
    ];
    f.state.handler = route => release(route, { ok: true, events: cases.map(([action], index) => event(String(index), { action })), total: cases.length });
    await open(f);
    const labels = await f.page.locator('.operation-history tbody tr td:nth-child(3)').allTextContents();
    cases.forEach(([action, label], index) => {
      assert.ok(labels[index].startsWith(label), `${action} should render ${label}, got ${labels[index]}`);
      assert.ok(labels[index].endsWith(action), 'original action stays available for exact filtering');
    });
    assert.equal(await f.page.locator('.operation-history tbody tr').count(), cases.length);
  } finally { await f.context.close(); }
});


test('an already completed obsolete 401 cannot log out the newly accepted identity', async () => {
  const f = await fixture({ ignoreAbort: true }); let held;
  try {
    await open(f);
    f.state.handler = async route => { held = route; };
    await f.page.locator('[data-history-refresh]').click();
    await until(() => held);
    f.state.handler = async route => release(route, { ok: true, events: [event('new-identity')], total: 1 });
    await f.page.evaluate(profile => acceptAuthSession({ token: 'new-valid-token', user: profile.username, profile }), { ...READER, user_id: 'u-accepted', username: 'accepted' });
    await waitText(f.page, 'new-identity');
    await held.fulfill({ status: 401, json: { ok: false, code: 'unauthorized' } });
    await f.page.waitForTimeout(100);
    assert.equal(await f.page.locator('#app').isVisible(), true);
    assert.equal(await f.page.evaluate(() => sessionStorage.getItem('user')), 'accepted');
    await waitText(f.page, 'new-identity');
  } finally { await f.context.close(); }
});

test('a current history 401 still clears the expired session', async () => {
  const f = await fixture();
  try {
    await open(f);
    f.state.handler = route => route.fulfill({ status: 401, json: { ok: false, code: 'unauthorized' } });
    await f.page.locator('[data-history-refresh]').click();
    await f.page.locator('#login-screen').waitFor({ state: 'visible' });
    assert.equal(await f.page.evaluate(() => sessionStorage.getItem('sessionToken')), null);
    assert.equal(await f.page.locator('.operation-history').count(), 0);
  } finally { await f.context.close(); }
});
