const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const ARTIFACTS = path.join(ROOT, 'output/playwright/identity');
const EMPTY_SCOPE = { ui_apps: [], api_projects: [], api_environments: [] };
const ALL_SCOPE = { ui_apps: '*', api_projects: '*', api_environments: '*' };
const ADMIN = { username: 'admin', user_id: 'u1', display_name: '平台管理员', status: 'active', role_ids: ['super_admin'], permissions: ['auth.manage'], scope: ALL_SCOPE, is_superuser: true, must_change_password: false };
const READER = { ...ADMIN, username: 'reader', display_name: '只读成员', role_ids: ['viewer'], permissions: ['ui.view', 'api.view'], scope: EMPTY_SCOPE, is_superuser: false };
const ROLES = [{ id: 'super_admin', name: '超级管理员', permissions: ['auth.manage'] }, { id: 'tester', name: '测试成员', permissions: ['ui.view', 'api.view'] }, { id: 'viewer', name: '只读成员', permissions: ['ui.view', 'api.view'] }];

async function serve() {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const file = path.resolve(ROOT, '.' + (pathname === '/' ? '/task-manager.html' : pathname.endsWith('/') ? pathname + 'index.html' : pathname));
    if (!file.startsWith(ROOT + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404); res.end(); return;
    }
    const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' };
    res.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream');
    fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return server;
}

async function fixture(browser, base, options = {}) {
  const context = await browser.newContext({ viewport: options.mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.setDefaultTimeout(2500);
  const state = { profile: structuredClone(options.profile || ADMIN), users: [structuredClone(ADMIN), structuredClone(READER)], roles: structuredClone(ROLES), calls: [], errors: [], hooks: new Map() };
  page.on('pageerror', error => state.errors.push(error.message));
  if (options.publicHttp) await page.route('http://identity.fixture.test/**', async route => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: base + url.pathname + url.search });
    await route.fulfill({ response });
  });
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const key = request.method() + ' ' + url.pathname;
    const body = request.postDataJSON();
    state.calls.push({ key, body });
    if (state.hooks.has(key)) return state.hooks.get(key)(route, body);
    let payload = { ok: true };
    if (url.pathname === '/api/auth/me') payload = { ...payload, user: state.profile.username, ...(options.legacy ? {} : { profile: state.profile }) };
    else if (url.pathname === '/api/auth/login') payload = { ...payload, user: state.profile.username, token: 'fixture-login-token', profile: state.profile };
    else if (url.pathname === '/api/auth/change-password') {
      state.profile.must_change_password = false;
      payload = { ...payload, user: state.profile.username, token: 'fixture-fresh-token', profile: state.profile };
    } else if (url.pathname === '/api/auth/users' && request.method() === 'GET') payload.users = state.users;
    else if (url.pathname === '/api/auth/users' && request.method() === 'POST') {
      const user = { ...body, status: 'active', must_change_password: true };
      state.users.push(user);
      payload = { ...payload, user, temporary_password: 'fixture-only-temporary-password' };
    } else if (url.pathname.startsWith('/api/auth/users/') && request.method() === 'PUT') {
      const user = state.users.find(item => item.username === decodeURIComponent(url.pathname.split('/')[4]));
      Object.assign(user, body); payload.user = user;
    } else if (url.pathname.endsWith('/reset-password')) payload.temporary_password = 'fixture-only-reset-password';
    else if (url.pathname === '/api/auth/roles' && request.method() === 'GET') payload.roles = state.roles;
    else if (url.pathname === '/api/auth/roles' && request.method() === 'POST') { const role = { ...body, id: body.id || 'custom' }; state.roles.push(role); payload.role = role; }
    else if (url.pathname === '/api/auth/permissions') payload.permissions = [
      { id: 'ui.view', label: '查看 UI 测试', group: 'UI 测试' },
      { id: 'ui.edit', label: '编辑 UI 测试', group: 'UI 测试' },
      { id: 'api.view', label: '查看 API 测试', group: 'API 测试' },
      { id: 'api.execute', label: '执行 API 测试', group: 'API 测试' },
      { id: 'auth.manage', label: '管理成员与权限', group: '平台' },
    ];
    else if (url.pathname === '/api/auth/scope-options') payload = { ...payload, ui_apps: [{ id: 'com.fixture.app', name: '智小白 3D' }], api_projects: [{ id: 'p1', name: '家用 API' }, { id: 'p2', name: '共享 API' }], api_environments: [{ id: 'e1', name: '测试环境', project_id: 'p1' }, { id: 'e2', name: '生产环境', project_id: 'p2' }] };
    else if (url.pathname === '/api/auth/audit') payload.events = [
      { id: 2, actor: 'admin', action: 'operation.result', target: '/api/api-testing/v1/scheduled-jobs/job-123/run', created_at: Date.UTC(2026, 7, 31, 9) / 1000, details: { method: 'POST', status: 200, ok: true } },
      { id: 1, actor: 'admin', action: 'user.create', target: '<img src=x onerror=alert(1)>', created_at: Date.UTC(2026, 7, 31, 8) / 1000, details: { password: 'fixture-must-not-render' } },
      { id: 0, actor: '', action: 'login.failure', target: '', created_at: Date.UTC(2026, 7, 31, 7) / 1000, details: { outcome: 'failure' } },
    ];
    else if (url.pathname === '/api/auth/sessions') payload.sessions = [
      { id: 's2', created_at: Date.UTC(2026, 7, 31, 7) / 1000, expires_at: Date.UTC(2026, 8, 1, 7) / 1000, is_current: false },
      { id: 's1', created_at: Date.UTC(2026, 7, 31, 8) / 1000, expires_at: Date.UTC(2026, 8, 1, 8) / 1000, is_current: true },
    ];
    else if (url.pathname === '/api/modules') payload = {};
    else if (url.pathname === '/api/task-apps') payload.apps = [];
    else if (url.pathname === '/api/task-meta') payload.meta = {};
    else if (url.pathname === '/api/jobs') payload.jobs = [];
    else if (url.pathname === '/api/runners') payload.runners = [];
    else if (url.pathname === '/api/agent/runs') payload.runs = [];
    await route.fulfill({ json: payload });
  });
  await context.addInitScript(({ loggedIn, workflow }) => {
    if (sessionStorage.getItem('identity-fixture-ready')) return;
    sessionStorage.setItem('identity-fixture-ready', '1');
    if (loggedIn) { sessionStorage.setItem('sessionToken', 'fixture-session-token'); sessionStorage.setItem('user', 'admin'); }
    sessionStorage.setItem('midscene_active_workflow', workflow);
  }, { loggedIn: options.loggedIn !== false, workflow: options.workflow || 'identity' });
  options.setup?.(state);
  await page.goto((options.publicHttp ? 'http://identity.fixture.test' : base) + (options.path || '/task-manager.html') + (options.query || ''));
  return { page, context, state };
}

async function run() {
  fs.mkdirSync(ARTIFACTS, { recursive: true });
  const server = await serve();
  const browser = await chromium.launch({ headless: true });
  const base = `http://127.0.0.1:${server.address().port}`;
  let failed = 0;
  const check = async (name, callback) => {
    try { await callback(); console.log('PASS ' + name); }
    catch (error) { failed++; console.error('FAIL ' + name + ': ' + error.message); }
  };
  try {
    await check('member creation defaults, one-time secret and escaped values', async () => {
      const { page, context, state } = await fixture(browser, base);
      await page.getByRole('tab', { name: '成员', exact: true }).waitFor();
      await page.getByRole('button', { name: '新增成员', exact: true }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByLabel('用户名', { exact: true }).fill('fixture-user');
      await dialog.getByLabel('姓名', { exact: true }).fill('<img src=x onerror=alert(1)>');
      await dialog.getByRole('button', { name: '创建成员' }).click();
      await page.getByText('fixture-only-temporary-password', { exact: true }).waitFor();
      const created = state.calls.find(call => call.key === 'POST /api/auth/users').body;
      assert.deepEqual(created.scope, EMPTY_SCOPE);
      assert.deepEqual(created.role_ids, ['tester']);
      assert.equal(created.password, undefined);
      assert.equal(await page.locator('#identity-center img').count(), 0);
      await page.getByRole('button', { name: '关闭', exact: true }).click();
      assert.equal(await page.getByText('fixture-only-temporary-password', { exact: true }).count(), 0);
      assert.equal(await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }).includes('fixture-only-temporary-password')), false);
      assert.deepEqual(state.errors, []);
      await page.screenshot({ path: path.join(ARTIFACTS, 'members-desktop.png'), fullPage: true });
      await context.close();
    });
    await check('custom role creation and immutable super admin', async () => {
      const { page, context, state } = await fixture(browser, base);
      await page.getByRole('tab', { name: '角色', exact: true }).click();
      const adminRow = page.getByRole('row').filter({ hasText: '超级管理员' });
      assert.equal(await adminRow.getByRole('button', { name: '编辑', exact: true }).count(), 0);
      await page.getByRole('button', { name: '新增角色', exact: true }).click();
      await page.getByLabel('角色名称', { exact: true }).fill('接口观察员');
      await page.getByLabel('查看 API 测试', { exact: true }).check();
      await page.getByRole('button', { name: '保存角色', exact: true }).click();
      await page.getByRole('cell', { name: '接口观察员', exact: true }).waitFor();
      assert.deepEqual(state.calls.find(call => call.key === 'POST /api/auth/roles').body.permissions, ['api.view']);
      await context.close();
    });
    await check('member roles avoid contradictory administrator and readonly combinations', async () => {
      const { page, context } = await fixture(browser, base);
      await page.getByRole('button', { name: '新增成员', exact: true }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByLabel('超级管理员', { exact: true }).check();
      assert.equal(await dialog.getByLabel('测试成员', { exact: true }).isChecked(), false);
      await dialog.getByLabel('只读成员', { exact: true }).check();
      assert.equal(await dialog.getByLabel('超级管理员', { exact: true }).isChecked(), false);
      assert.equal(await dialog.getByLabel('只读成员', { exact: true }).isChecked(), true);
      await dialog.getByLabel('测试成员', { exact: true }).check();
      assert.equal(await dialog.getByLabel('只读成员', { exact: true }).isChecked(), false);
      await context.close();
    });
    await check('role editor adds required view permission and supports group selection', async () => {
      const { page, context, state } = await fixture(browser, base);
      await page.getByRole('tab', { name: '角色', exact: true }).click();
      await page.getByRole('button', { name: '新增角色', exact: true }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByLabel('角色名称', { exact: true }).fill('接口执行员');
      await dialog.getByLabel('执行 API 测试', { exact: true }).check();
      assert.equal(await dialog.getByLabel('查看 API 测试', { exact: true }).isChecked(), true);
      const uiGroup = dialog.getByRole('group', { name: 'UI 测试' });
      await uiGroup.getByRole('button', { name: '全选本组', exact: true }).click();
      assert.equal(await dialog.getByLabel('查看 UI 测试', { exact: true }).isChecked(), true);
      assert.equal(await dialog.getByLabel('编辑 UI 测试', { exact: true }).isChecked(), true);
      await uiGroup.getByRole('button', { name: '清空本组', exact: true }).click();
      assert.equal(await dialog.getByLabel('查看 UI 测试', { exact: true }).isChecked(), false);
      await dialog.getByRole('button', { name: '保存角色', exact: true }).click();
      const permissions = state.calls.find(call => call.key === 'POST /api/auth/roles').body.permissions;
      assert.deepEqual(permissions.sort(), ['api.execute', 'api.view']);
      await context.close();
    });
    await check('scope selection uses names and persists selected IDs', async () => {
      const { page, context, state } = await fixture(browser, base);
      await page.getByRole('tab', { name: '数据授权', exact: true }).click();
      await page.getByRole('row').filter({ hasText: 'reader' }).getByRole('button', { name: '编辑范围' }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByLabel('智小白 3D', { exact: true }).check();
      await dialog.getByLabel('家用 API', { exact: true }).check();
      await dialog.getByLabel('测试环境（家用 API）', { exact: true }).check();
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
      await page.screenshot({ path: path.join(ARTIFACTS, 'scope-mobile.png'), fullPage: true });
      await dialog.getByRole('button', { name: '保存范围' }).click();
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      assert.deepEqual(state.calls.find(call => call.key === 'PUT /api/auth/users/reader').body.scope, { ui_apps: ['com.fixture.app'], api_projects: ['p1'], api_environments: ['e1'] });
      await context.close();
    });
    await check('late scope response cannot replace a newer dialog', async () => {
      const { page, context, state } = await fixture(browser, base);
      await page.getByRole('tab', { name: '数据授权', exact: true }).click();
      let release;
      state.hooks.set('GET /api/auth/scope-options', async route => { await new Promise(resolve => { release = resolve; }); await route.fulfill({ json: { ok: true, ...EMPTY_SCOPE } }); });
      await page.getByRole('row').filter({ hasText: 'reader' }).getByRole('button', { name: '编辑范围' }).click();
      await page.waitForFunction(() => document.querySelector('[role="dialog"]')?.textContent.includes('加载'));
      await page.getByRole('button', { name: '取消', exact: true }).click();
      await page.getByRole('tab', { name: '角色', exact: true }).click();
      await page.getByRole('button', { name: '新增角色', exact: true }).click();
      release();
      await page.getByLabel('角色名称', { exact: true }).fill('保持当前弹窗');
      assert.equal(await page.getByLabel('角色名称', { exact: true }).inputValue(), '保持当前弹窗');
      assert.equal(await page.getByRole('button', { name: '保存范围' }).count(), 0);
      await context.close();
    });
    await check('mandatory password change precedes mount and preserves return_to', async () => {
      const { page, context, state } = await fixture(browser, base, { profile: { ...READER, must_change_password: true }, query: '?return_to=%2Fapi-test%2F%23%2Freports%3FprojectId%3Dp1' });
      await page.getByRole('heading', { name: '修改密码', exact: true }).waitFor();
      assert.equal(await page.locator('#app').isVisible(), false);
      assert.equal(state.calls.filter(call => !call.key.includes('/api/auth/')).length, 0);
      await page.getByLabel('当前密码', { exact: true }).fill('fixture-current-password');
      await page.getByLabel('新密码', { exact: true }).fill('fixture-new-password-123');
      await page.getByLabel('确认新密码', { exact: true }).fill('fixture-new-password-123');
      await page.route('**/api-test/**', route => route.fulfill({ contentType: 'text/html', body: '<h1>Fixture API</h1>' }));
      await page.getByRole('button', { name: '保存密码', exact: true }).click();
      await page.waitForURL('**/api-test/#/reports?projectId=p1');
      assert.equal(await page.evaluate(() => sessionStorage.getItem('sessionToken')), 'fixture-fresh-token');
      await context.close();
    });
    await check('login also gates must-change users and clears the password field', async () => {
      const { page, context } = await fixture(browser, base, { loggedIn: false, profile: { ...READER, must_change_password: true } });
      await page.locator('#username').fill('reader');
      await page.locator('#password').fill('fixture-current-password');
      await page.locator('#login-form button[type="submit"]').click();
      await page.getByRole('heading', { name: '修改密码', exact: true }).waitFor();
      assert.equal(await page.locator('#password').inputValue(), '');
      await context.close();
    });
    await check('403 displays a remedy without clearing a valid login', async () => {
      const { page, context, state } = await fixture(browser, base);
      await page.getByRole('tab', { name: '成员', exact: true }).waitFor();
      state.hooks.set('GET /api/auth/users', route => route.fulfill({ status: 403, json: { ok: false, error: '缺少 auth.manage 权限' } }));
      await page.getByRole('button', { name: '刷新成员与权限', exact: true }).click();
      await page.getByText(/联系管理员/).first().waitFor();
      assert.equal(await page.evaluate(() => sessionStorage.getItem('sessionToken')), 'fixture-session-token');
      assert.equal(await page.locator('#login-screen').isVisible(), false);
      await context.close();
    });
    await check('readonly navigation excludes configuration and execution', async () => {
      const { page, context } = await fixture(browser, base, { profile: READER });
      await page.locator('#app').waitFor({ state: 'visible' });
      assert.equal(await page.locator('[data-workflow="identity"]').getAttribute('hidden'), '');
      assert.equal(await page.locator('[data-workflow="execute"]').getAttribute('hidden'), '');
      assert.equal(await page.locator('[data-workflow="config"]').getAttribute('hidden'), '');
      assert.equal(await page.locator('.api-test-link').isVisible(), true);
      await context.close();
    });
    await check('readonly assets disable mutations and keep file content read-only', async () => {
      const { page, context, state } = await fixture(browser, base, {
        profile: READER, workflow: 'assets',
        setup(state) {
          state.hooks.set('GET /api/modules', route => route.fulfill({ json: { '只读模块': ['only.yaml'] } }));
          state.hooks.set('GET /api/file', route => route.fulfill({ contentType: 'text/plain', body: 'android: {}\ntasks:\n  - name: Audit readonly\n    flow:\n      - aiAssert: Home visible\n' }));
        },
      });
      await page.getByRole('button', { name: '新建 YAML', exact: true }).waitFor();
      assert.equal(await page.getByRole('button', { name: '新建 YAML', exact: true }).isDisabled(), true);
      assert.equal(await page.getByRole('button', { name: '上传 YAML', exact: true }).isDisabled(), true);
      await page.getByRole('button', { name: '选择当前列表', exact: true }).click();
      for (const name of ['批量移动', '批量删除', '同步当前已选至 Sonic 平台']) {
        assert.equal(await page.getByRole('button', { name, exact: true }).isDisabled(), true, name);
      }
      for (const name of ['执行', '重命名', '移动', '删除']) {
        assert.equal(await page.locator('.asset-row-actions').getByRole('button', { name, exact: true }).isDisabled(), true, name);
      }
      await page.evaluate(() => { showAddTask(); showUpload(); });
      assert.equal(await page.locator('#modal-task').isVisible(), false);
      assert.equal(await page.locator('#modal-upload').isVisible(), false);
      await page.locator('.asset-row-actions').getByRole('button', { name: '打开', exact: true }).click();
      await page.locator('#editor').waitFor();
      assert.equal(await page.locator('#editor').evaluate(el => el.readOnly), true);
      const content = await page.locator('#editor').inputValue();
      assert.equal(await page.evaluate(() => parseYamlTasks(document.getElementById('editor').value).length), 1);
      await page.locator('#editor').press('Tab');
      assert.equal(await page.locator('#editor').inputValue(), content);
      for (const select of await page.locator('.priority-select').all()) assert.equal(await select.isDisabled(), true);
      assert.equal(await page.getByRole('button', { name: '执行当前', exact: true }).isDisabled(), true);
      assert.equal(await page.getByRole('button', { name: '修复当前', exact: true }).isDisabled(), true);
      await page.evaluate(() => changeTaskPriority(0, 'P0'));
      assert.equal(await page.locator('#editor').inputValue(), content);
      await page.evaluate(() => saveFile());
      assert.equal(state.calls.some(call => call.key === 'POST /api/file'), false);
      await context.close();
    });
    await check('asset search keeps focus while typing and after background refresh', async () => {
      const { page, context } = await fixture(browser, base, {
        workflow: 'assets', setup(state) {
          state.hooks.set('GET /api/modules', route => route.fulfill({ json: { audit: ['Codex-Audit.yaml', 'Other.yaml'] } }));
        },
      });
      await page.locator('#asset-search').click();
      await page.keyboard.type('Codex', { delay: 20 });
      assert.equal(await page.locator('#asset-search').inputValue(), 'Codex');
      await page.evaluate(() => { document.getElementById('asset-search').setSelectionRange(1, 3); showAssetsCenter(); });
      assert.deepEqual(await page.locator('#asset-search').evaluate(el => [document.activeElement === el, el.selectionStart, el.selectionEnd]), [true, 1, 3]);
      assert.equal(await page.locator('.asset-row-actions').count(), 1);
      await context.close();
    });
    await check('asset actions follow edit, delete and shared configuration permissions separately', async () => {
      for (const [permissions, editable, deletable, movable, syncable] of [
        [['ui.view', 'ui.edit', 'ui.execute'], true, false, false, false],
        [['ui.view', 'ui.delete'], false, true, false, false],
        [['ui.view', 'ui.edit', 'ui.delete'], true, true, true, false],
        [['ui.view', 'ui.baseline'], false, false, false, false],
        [['ui.view', 'platform.configure'], false, false, false, true],
      ]) {
        const { page, context } = await fixture(browser, base, {
          profile: { ...READER, permissions, scope: ALL_SCOPE }, workflow: 'assets',
          setup(state) { state.hooks.set('GET /api/modules', route => route.fulfill({ json: { audit: ['only.yaml'] } })); },
        });
        await page.getByRole('button', { name: '选择当前列表', exact: true }).click();
        assert.equal(await page.getByRole('button', { name: '新建 YAML', exact: true }).isDisabled(), !editable);
        assert.equal(await page.locator('.asset-row-actions').getByRole('button', { name: '删除', exact: true }).isDisabled(), !deletable);
        assert.equal(await page.getByRole('button', { name: '批量删除', exact: true }).isDisabled(), !deletable);
        assert.equal(await page.locator('.asset-row-actions').getByRole('button', { name: '移动', exact: true }).isDisabled(), !movable);
        assert.equal(await page.getByRole('button', { name: '同步当前已选至 Sonic 平台', exact: true }).isDisabled(), !syncable);
        assert.equal(await page.evaluate(() => requireUiDeletePermission()), deletable);
        await context.close();
      }
    });
    await check('audit renders safe columns and mobile layout fits', async () => {
      const { page, context, state } = await fixture(browser, base, { mobile: true });
      await page.getByRole('tab', { name: '操作记录', exact: true }).click();
      await page.getByRole('cell', { name: '<img src=x onerror=alert(1)>', exact: true }).waitFor();
      assert.equal(await page.locator('#identity-center img').count(), 0);
      assert.equal(await page.getByText('fixture-must-not-render').count(), 0);
      await page.getByRole('cell', { name: '手动执行定时任务', exact: true }).waitFor();
      await page.getByRole('cell', { name: '成功 · POST 200', exact: true }).waitFor();
      await page.getByRole('searchbox', { name: '搜索操作记录', exact: true }).fill('不存在');
      await page.getByText('没有匹配的操作记录', { exact: true }).waitFor();
      await page.getByRole('searchbox', { name: '搜索操作记录', exact: true }).fill('');
      await page.getByRole('combobox', { name: '筛选操作结果', exact: true }).selectOption('failure');
      assert.equal(await page.getByRole('row').filter({ hasText: '登录失败' }).count(), 1);
      assert.equal(await page.getByRole('row').filter({ hasText: '手动执行定时任务' }).count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
      await page.screenshot({ path: path.join(ARTIFACTS, 'audit-mobile.png'), fullPage: true });
      assert.deepEqual(state.errors, []);
      await context.close();
    });
    await check('personal sessions and self revocation', async () => {
      const { page, context, state } = await fixture(browser, base);
      await page.getByRole('button', { name: '个人账号', exact: true }).click();
      await page.getByRole('button', { name: '个人资料与会话', exact: true }).click();
      await page.getByText('当前会话', { exact: true }).waitFor();
      await page.getByRole('button', { name: '撤销全部会话', exact: true }).click();
      await page.getByRole('button', { name: '确认撤销', exact: true }).click();
      await page.locator('#login-screen').waitFor({ state: 'visible' });
      assert.equal(await page.evaluate(() => sessionStorage.getItem('sessionToken')), null);
      assert(state.calls.some(call => call.key === 'POST /api/auth/revoke-sessions'));
      await context.close();
    });
    await check('one old session can be revoked without ending the current session', async () => {
      const { page, context, state } = await fixture(browser, base);
      await page.getByRole('button', { name: '个人账号', exact: true }).click();
      await page.getByRole('button', { name: '个人资料与会话', exact: true }).click();
      await page.getByRole('button', { name: '撤销此会话', exact: true }).click();
      await page.getByRole('button', { name: '确认撤销', exact: true }).click();
      await page.getByRole('heading', { name: '个人资料与会话', exact: true }).waitFor();
      assert.deepEqual(state.calls.find(call => call.key === 'POST /api/auth/sessions/revoke').body, { session_id: 's2' });
      assert.equal(await page.evaluate(() => sessionStorage.getItem('sessionToken')), 'fixture-session-token');
      await context.close();
    });
    await check('legacy fixtures without a profile still mount', async () => {
      const { page, context } = await fixture(browser, base, { legacy: true, workflow: 'dashboard' });
      await page.locator('#app').waitFor({ state: 'visible' });
      assert.equal(await page.locator('[data-workflow="execute"]').getAttribute('hidden'), null);
      await context.close();
    });
    await check('public HTTP state is visible and not shown for loopback', async () => {
      const { page, context } = await fixture(browser, base, { publicHttp: true });
      await page.getByText('当前连接未加密，请启用 HTTPS 后再分发成员密码', { exact: true }).waitFor();
      await context.close();
      const local = await fixture(browser, base);
      await local.page.getByRole('tab', { name: '成员', exact: true }).waitFor();
      assert.equal(await local.page.getByText('当前连接未加密，请启用 HTTPS 后再分发成员密码', { exact: true }).count(), 0);
      await local.context.close();
    });
    await check('Agent gate requires configure, execute and all UI data while manual tools remain', async () => {
      const profile = { ...READER, permissions: ['platform.configure', 'ui.view', 'ui.edit', 'ui.execute', 'api.view'], scope: { ...EMPTY_SCOPE, ui_apps: ['com.fixture.app'] } };
      const { page, context, state } = await fixture(browser, base, { profile, workflow: 'agent_history' });
      await page.locator('#app').waitFor({ state: 'visible' });
      assert.equal(await page.locator('[data-workflow="dashboard"]').getAttribute('hidden'), '');
      assert.equal(await page.locator('[data-workflow="agent_history"]').getAttribute('hidden'), null);
      assert.equal(await page.locator('[data-workflow="generate"]').getAttribute('hidden'), null);
      assert.equal(await page.locator('[data-workflow="yaml_edit"]').getAttribute('hidden'), null);
      const denial = await page.evaluate(async () => { try { await apiRequest('/agent-runs/start', { method: 'POST', body: {} }); return ''; } catch (error) { return error.message; } });
      assert.match(denial, /platform.configure.*ui.execute.*全部 UI 应用/);
      assert.equal(state.calls.some(call => call.key === 'POST /api/agent-runs/start'), false);
      await context.close();
    });
    await check('restored identity workspace hides unrelated Agent panels', async () => {
      const { page, context } = await fixture(browser, base);
      await page.getByRole('tab', { name: '成员', exact: true }).waitFor();
      assert.equal(await page.locator('.jobs-panel').isVisible(), false);
      assert.equal(await page.locator('#toolbar-help').textContent(), '');
      await context.close();
    });
    await check('last-admin failure stays in dialog with precise error and no logout', async () => {
      const { page, context, state } = await fixture(browser, base, { setup: state => state.users.push({ ...ADMIN, username: 'second-super', display_name: '另一位管理员' }) });
      await page.getByRole('tab', { name: '成员', exact: true }).waitFor();
      state.hooks.set('PUT /api/auth/users/admin', route => route.fulfill({ status: 409, json: { ok: false, error: { message: '不能停用最后一个有效超级管理员' } } }));
      await page.getByRole('row').filter({ hasText: 'admin' }).getByRole('button', { name: '停用', exact: true }).click();
      await page.getByRole('button', { name: '确认', exact: true }).click();
      await page.getByRole('dialog').getByText('不能停用最后一个有效超级管理员', { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => sessionStorage.getItem('sessionToken')), 'fixture-session-token');
      await context.close();
    });
    await check('numeric audit timestamps render as dates', async () => {
      const { page, context, state } = await fixture(browser, base);
      state.hooks.set('GET /api/auth/audit', route => route.fulfill({ json: { ok: true, events: [{ created_at: Date.UTC(2026, 7, 31, 8) / 1000, actor: 'admin', action: 'login.success', target: 'admin' }] } }));
      await page.getByRole('tab', { name: '操作记录', exact: true }).click();
      await page.getByRole('cell', { name: '2026-08-31 16:00:00', exact: true }).waitFor();
      await context.close();
    });
    await check('pending scope save cannot close a newer dialog', async () => {
      const { page, context, state } = await fixture(browser, base);
      await page.getByRole('tab', { name: '数据授权', exact: true }).click();
      let release;
      state.hooks.set('PUT /api/auth/users/reader', async route => { await new Promise(resolve => { release = resolve; }); await route.fulfill({ json: { ok: true } }); });
      await page.getByRole('row').filter({ hasText: 'reader' }).getByRole('button', { name: '编辑范围' }).click();
      await page.getByRole('dialog').getByRole('button', { name: '保存范围' }).click();
      await page.waitForRequest(request => request.method() === 'PUT', { timeout: 1000 }).catch(() => {});
      await page.getByRole('button', { name: '关闭', exact: true }).click();
      await page.getByRole('tab', { name: '角色', exact: true }).click();
      await page.getByRole('button', { name: '新增角色', exact: true }).click();
      release();
      await page.getByLabel('角色名称', { exact: true }).fill('保留新表单');
      assert.equal(await page.getByLabel('角色名称', { exact: true }).inputValue(), '保留新表单');
      await context.close();
    });
    await check('password validation and wrong-current-password never log out', async () => {
      const { page, context, state } = await fixture(browser, base, { profile: { ...READER, must_change_password: true }, mobile: true });
      await page.getByLabel('当前密码', { exact: true }).fill('fixture-current-password');
      await page.getByLabel('新密码', { exact: true }).fill('short');
      await page.getByLabel('确认新密码', { exact: true }).fill('short');
      await page.getByRole('button', { name: '保存密码', exact: true }).click();
      assert.equal(state.calls.some(call => call.key === 'POST /api/auth/change-password'), false);
      await page.getByLabel('新密码', { exact: true }).fill('fixture-new-password');
      await page.getByLabel('确认新密码', { exact: true }).fill('fixture-different-password');
      await page.getByRole('button', { name: '保存密码', exact: true }).click();
      await page.getByText('两次输入的新密码不一致').waitFor();
      await page.getByLabel('确认新密码', { exact: true }).fill('fixture-new-password');
      state.hooks.set('POST /api/auth/change-password', route => route.fulfill({ status: 401, json: { ok: false, error: '当前密码不正确' } }));
      await page.getByRole('button', { name: '保存密码', exact: true }).click();
      await page.getByText('当前密码不正确', { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => sessionStorage.getItem('sessionToken')), 'fixture-session-token');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
      await page.screenshot({ path: path.join(ARTIFACTS, 'password-mobile.png'), fullPage: true });
      await context.close();
    });
    await check('API deep links verify me and move mandatory change to main before business reads', async () => {
      const { page, context, state } = await fixture(browser, base, { profile: { ...READER, must_change_password: true }, path: '/api-test/', query: '#/reports?projectId=p1' });
      await page.getByRole('heading', { name: '修改密码', exact: true }).waitFor();
      assert.match(page.url(), /task-manager.html\?return_to=/);
      assert.equal(state.calls.some(call => call.key.includes('/api/api-testing/')), false);
      await context.close();
    });
    await check('permission table uses catalog labels and last-super action is disabled', async () => {
      const { page, context } = await fixture(browser, base);
      await page.getByRole('tab', { name: '成员', exact: true }).waitFor();
      const disable = page.getByRole('row').filter({ hasText: 'admin' }).getByRole('button', { name: '停用', exact: true });
      assert.equal(await disable.isDisabled(), true);
      assert.match(await disable.getAttribute('title'), /最后一个/);
      await page.getByRole('row').filter({ hasText: 'admin' }).getByText('需保留至少 1 名有效超级管理员', { exact: true }).waitFor();
      await page.getByRole('tab', { name: '角色', exact: true }).click();
      await page.getByText('管理成员与权限', { exact: true }).first().waitFor();
      assert.equal(await page.getByRole('cell', { name: 'auth.manage', exact: true }).count(), 0);
      await page.screenshot({ path: path.join(ARTIFACTS, 'roles-desktop.png'), fullPage: true });
      await context.close();
    });
    await check('readonly account uses custom role_names without fetching management roles', async () => {
      const { page, context, state } = await fixture(browser, base, { profile: { ...READER, role_ids: ['custom'], role_names: ['接口观察员'] } });
      await page.getByRole('button', { name: '个人账号', exact: true }).click();
      await page.getByRole('button', { name: '个人资料与会话', exact: true }).click();
      await page.getByRole('dialog').getByText('接口观察员', { exact: true }).waitFor();
      assert.equal(state.calls.some(call => call.key === 'GET /api/auth/roles'), false);
      await context.close();
    });
    await check('scoped UI AI is disabled without disabling manual YAML or API', async () => {
      const { page, context, state } = await fixture(browser, base, { profile: { ...READER, permissions: ['ui.view', 'ui.edit', 'ui.execute', 'api.view'], scope: { ...EMPTY_SCOPE, ui_apps: ['com.fixture.app'] } } });
      await page.locator('#app').waitFor({ state: 'visible' });
      assert.equal(await page.locator('[data-workflow="generate"]').isDisabled(), true);
      await page.getByText('AI 生成需完整 UI 应用范围（共用基线库）', { exact: true }).waitFor();
      assert.equal(await page.locator('[data-workflow="yaml_edit"]').getAttribute('hidden'), null);
      assert.equal(await page.locator('[data-workflow="execute"]').getAttribute('hidden'), null);
      assert.equal(await page.locator('.api-test-link').isVisible(), true);
      const error = await page.evaluate(async () => { try { await apiRequest('/ui/generate-yaml-async', { method: 'POST', body: {} }); return ''; } catch (error) { return error.message; } });
      assert.match(error, /共用基线库/);
      assert.equal(state.calls.some(call => call.key === 'POST /api/ui/generate-yaml-async'), false);
      await context.close();
    });
    for (const [name, profile, allowed] of [
      ['scoped config member', { ...READER, permissions: ['ui.view', 'ui.edit', 'platform.configure'], scope: { ...EMPTY_SCOPE, ui_apps: ['com.fixture.app'] } }, false],
      ['full-scope tester without configure', { ...READER, permissions: ['ui.view', 'ui.edit'], scope: ALL_SCOPE }, false],
      ['full-scope config member', { ...READER, permissions: ['ui.view', 'ui.edit', 'platform.configure'], scope: ALL_SCOPE }, true],
      ['super admin with empty stored scope', { ...ADMIN, scope: EMPTY_SCOPE }, true],
    ]) {
      await check(`${name} loads manual YAML with the correct global Sonic request gate`, async () => {
        const yaml = 'android: {}\ntasks:\n  - name: manual fixture\n    flow:\n      - aiAssert: "fixture page is visible"\n';
        const { page, context, state } = await fixture(browser, base, { profile, workflow: 'assets', setup(state) {
          state.hooks.set('GET /api/modules', route => route.fulfill({ json: { fixture: ['manual.yaml'] } }));
          state.hooks.set('GET /api/task-apps', route => route.fulfill({ json: { ok: true, apps: [{ package: 'com.fixture.app', name: 'Fixture App', modules: ['fixture'], enabled: true }] } }));
          state.hooks.set('GET /api/file', route => route.fulfill({ contentType: 'text/plain', body: yaml }));
          for (const endpoint of ['cases', 'status']) state.hooks.set(`GET /api/sonic/${endpoint}`, route => route.fulfill({ status: allowed ? 200 : 403, json: { ok: allowed, cases: [], summary: { total: 0 }, error: allowed ? undefined : '全局 Sonic 访问未授权' } }));
        } });
        try {
          await page.locator('#app').waitFor({ state: 'visible' });
          await page.evaluate(() => loadModules());
          await page.locator('.asset-file-link').filter({ hasText: 'manual' }).click();
          await page.locator('#editor').waitFor();
          await page.evaluate(() => refreshSonicPreview(true));
          assert.equal(await page.locator('#editor').inputValue(), yaml);
          assert.equal(state.calls.some(call => call.key === 'GET /api/file'), true);
          assert.equal(state.calls.some(call => call.key === 'GET /api/sonic/cases'), allowed);
          assert.equal(state.calls.some(call => call.key === 'GET /api/sonic/status'), allowed);
          assert.equal(await page.evaluate(() => sessionStorage.getItem('sessionToken')), 'fixture-session-token');
          assert.deepEqual(state.errors, []);
        } finally {
          await context.close();
        }
      });
    }
    await check('logout destroys in-memory workspace data before another member logs in', async () => {
      const { page, context } = await fixture(browser, base);
      await page.locator('#app').waitFor({ state: 'visible' });
      await page.evaluate(() => { window.fixturePrivateState = 'previous-member-data'; });
      await page.getByRole('button', { name: '个人账号', exact: true }).click();
      await page.getByRole('button', { name: '退出登录', exact: true }).click();
      await page.waitForFunction(() => window.fixturePrivateState === undefined);
      assert.equal(await page.evaluate(() => sessionStorage.getItem('sessionToken')), null);
      await context.close();
    });
    await check('new super admin with empty stored scope gains full access after changing password', async () => {
      const { page, context } = await fixture(browser, base, { profile: { ...ADMIN, scope: EMPTY_SCOPE, must_change_password: true } });
      await page.getByLabel('当前密码', { exact: true }).fill('fixture-current-password');
      await page.getByLabel('新密码', { exact: true }).fill('fixture-new-password');
      await page.getByLabel('确认新密码', { exact: true }).fill('fixture-new-password');
      await page.getByRole('button', { name: '保存密码', exact: true }).click();
      await page.locator('#app').waitFor({ state: 'visible' });
      assert.equal(await page.locator('[data-workflow="dashboard"]').getAttribute('hidden'), null);
      assert.equal(await page.locator('[data-workflow="generate"]').isDisabled(), false);
      await page.getByRole('button', { name: '个人账号', exact: true }).click();
      await page.getByRole('button', { name: '个人资料与会话', exact: true }).click();
      await page.getByRole('dialog').getByText('UI 应用：全部 / API 项目：全部 / API 环境：全部', { exact: true }).waitFor();
      await context.close();
    });
    await check('API-only account mounts without forbidden UI data reads', async () => {
      const { page, context, state } = await fixture(browser, base, { profile: { ...READER, permissions: ['api.view'], scope: { ...EMPTY_SCOPE, api_projects: ['p1'] } } });
      await page.getByRole('heading', { name: '个人账号', exact: true }).waitFor();
      assert.equal(await page.locator('.api-test-link').isVisible(), true);
      assert.equal(state.calls.some(call => !call.key.includes('/api/auth/')), false);
      await context.close();
    });
    await check('reset password validates length and shows generated secret only once', async () => {
      const { page, context, state } = await fixture(browser, base);
      await page.getByRole('row').filter({ hasText: 'reader' }).getByRole('button', { name: '重置密码', exact: true }).click();
      await page.getByRole('dialog').getByLabel('新临时密码（留空自动生成）').fill('short');
      await page.getByRole('button', { name: '确认重置', exact: true }).click();
      assert.equal(state.calls.some(call => call.key.endsWith('/reset-password')), false);
      await page.getByRole('dialog').getByLabel('新临时密码（留空自动生成）').fill('');
      await page.getByRole('button', { name: '确认重置', exact: true }).click();
      await page.getByText('fixture-only-reset-password', { exact: true }).waitFor();
      await page.getByRole('button', { name: '关闭', exact: true }).click();
      assert.equal(await page.getByText('fixture-only-reset-password', { exact: true }).count(), 0);
      await context.close();
    });
    await check('own member password action uses verified change instead of revoking its only session', async () => {
      const { page, context, state } = await fixture(browser, base);
      await page.getByRole('row').filter({ hasText: 'admin' }).getByRole('button', { name: '修改密码', exact: true }).click();
      await page.getByRole('dialog').getByLabel('当前密码', { exact: true }).waitFor();
      assert.equal(state.calls.some(call => call.key === 'POST /api/auth/users/admin/reset-password'), false);
      await context.close();
    });
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
  assert.equal(failed, 0, `${failed} frontend checks failed`);
}
run().catch(error => { console.error(error.message); process.exitCode = 1; });
