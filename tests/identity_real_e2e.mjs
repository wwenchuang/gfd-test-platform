import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {once} from 'node:events';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ARTIFACTS = path.join(ROOT, 'output/playwright/identity-real');
const MEMBER = 'iam-browser-member';
const ROLE = 'IAM scoped reader';
const APP_A = 'app.iam.a';
const FILE_A = '/api/file?module=IAM-A&file=scope-a.yaml';
const FILE_B = '/api/file?module=IAM-B&file=scope-b.yaml';
const secrets = new Set();
const secret = () => { const value = randomBytes(24).toString('base64url'); secrets.add(value); return value; };
const bootstrapPassword = secret();
const memberPassword = secret();
const state = await fs.mkdtemp(path.join(os.tmpdir(), 'identity-real-e2e-'));
const python = process.env.PYTHON || await fs.access(path.join(ROOT, '.venv/bin/python'))
  .then(() => path.join(ROOT, '.venv/bin/python'), () => 'python3');
const env = {
  PATH: process.env.PATH, LANG: 'en_US.UTF-8', PYTHONUNBUFFERED: '1',
  PYTHONDONTWRITEBYTECODE: '1', IDENTITY_E2E_STATE: state,
  TASK_ADMIN_PASSWORD: bootstrapPassword, TASK_SESSION_SECRET: secret(),
  API_TESTING_ENABLED: '0', TASK_ALLOWED_ORIGINS: '',
};
const result = {passed: 0, failed: 0, checks: [], failures: [], screenshots: [], layout: [], outboundAttempts: 0};
const serverRuns = [];
const browserErrors = [];
const requestFailures = [];
const deniedBackgroundRequests = [];
let server;
let browser;
let base;
let admin;
let member;
let adminToken;
let memberToken;
let temporaryToken;

function redact(value) {
  let text = String(value || '');
  for (const credential of secrets) text = text.split(credential).join('[REDACTED]');
  return text.replace(/Bearer\s+[^\s"']+/gi, 'Bearer [REDACTED]').slice(0, 5000);
}

async function startServer(port = 0) {
  const child = spawn(python, [path.join(ROOT, 'tests/fixtures/identity-server.py')], {
    cwd: state, env: {...env, IDENTITY_E2E_PORT: String(port)}, stdio: ['ignore', 'pipe', 'pipe'],
  });
  const run = {child, stdout: '', stderr: ''};
  serverRuns.push(run);
  child.stdout.on('data', (chunk) => { run.stdout += chunk; });
  child.stderr.on('data', (chunk) => { run.stderr += chunk; });
  for (let attempt = 0; attempt < 150; attempt += 1) {
    if (child.exitCode !== null) throw new Error(`Real server startup failed: ${redact(run.stderr)}`);
    const ready = run.stdout.split('\n').find((line) => /^\{"port":\s*\d+\}$/.test(line));
    if (ready) return {run, port: JSON.parse(ready).port};
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error('Real server startup timed out');
}

async function stopServer(run) {
  if (!run || run.collected) return;
  if (run.child.exitCode === null && run.child.signalCode === null) {
    const exited = once(run.child, 'exit');
    const killTimer = setTimeout(() => run.child.kill('SIGKILL'), 5000);
    run.child.kill('SIGTERM');
    try { await exited; } finally { clearTimeout(killTimer); }
  }
  run.collected = true;
  for (const line of run.stdout.split('\n')) {
    if (line.startsWith('{"outbound_attempts":')) result.outboundAttempts += JSON.parse(line).outbound_attempts;
  }
  assert.equal(run.child.exitCode, 0, 'fixture must shut down cleanly');
}

async function check(name, callback, critical = false) {
  try {
    const value = await callback();
    result.passed += 1;
    result.checks.push(name);
    return value;
  } catch (error) {
    result.failed += 1;
    result.failures.push({check: name, error: redact(error.message)});
    if (critical) throw new Error('Prerequisite failed; see sanitized result artifact');
  }
}

async function newPage(workflow) {
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block'});
  // Only a navigation preference is seeded. Authentication always happens through the UI.
  await context.addInitScript((value) => sessionStorage.setItem('midscene_active_workflow', value), workflow);
  await context.route('**/*', (route) => {
    const url = new URL(route.request().url());
    if (url.origin === base) return route.continue();
    requestFailures.push({method: route.request().method(), path: 'blocked-external-request'});
    return route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', (error) => browserErrors.push(redact(error.message)));
  page.on('response', (response) => {
    if (response.status() >= 500) requestFailures.push({path: new URL(response.url()).pathname, status: response.status()});
    if (response.status() === 403) deniedBackgroundRequests.push({
      method: response.request().method(), path: new URL(response.url()).pathname,
    });
  });
  await page.goto(`${base}/task-manager.html`);
  return page;
}

async function uiSubmit(page, pathname, method, action) {
  const pending = page.waitForResponse((response) => (
    new URL(response.url()).pathname === pathname && response.request().method() === method
  ));
  const [response] = await Promise.all([pending, action()]);
  const data = await response.json();
  if (data.token) secrets.add(data.token);
  if (data.temporary_password) secrets.add(data.temporary_password);
  assert.equal(response.status(), 200, `${method} ${pathname}: ${data.code || data.error || 'unexpected status'}`);
  assert.equal(data.ok, true, `${method} ${pathname} must succeed`);
  return data;
}

async function login(page, username, password) {
  await page.locator('#username').fill(username);
  await page.locator('#password').fill(password);
  return uiSubmit(page, '/api/auth/login', 'POST', () => page.locator('#login-form button[type="submit"]').click());
}

async function api(token, pathname, method = 'GET', body) {
  const response = await fetch(`${base}${pathname}`, {
    method, headers: {Authorization: `Bearer ${token}`, ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}), signal: AbortSignal.timeout(5000),
  });
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch {}
  return {status: response.status, text, data};
}

async function capture(page, name) {
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height: width === 390 ? 844 : 1000});
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const layout = await page.evaluate(() => {
      const visible = (element) => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden';
      const dialogs = [...document.querySelectorAll('dialog[open]')].map((element) => {
        const rect = element.getBoundingClientRect();
        return {x: rect.x, right: rect.right, width: rect.width, scroll: element.scrollWidth, client: element.clientWidth};
      });
      const clipped = [...document.querySelectorAll('dialog[open] input:not([type="checkbox"]):not([type="radio"]), dialog[open] button, .identity-password input')]
        .filter(visible).filter((element) => {
          const rect = element.getBoundingClientRect();
          return rect.x < -1 || rect.right > innerWidth + 1;
        }).map((element) => ({tag: element.tagName, name: element.name || element.getAttribute('aria-label') || ''}));
      return {viewport: innerWidth, page: document.documentElement.scrollWidth, dialogs, clipped};
    });
    result.layout.push({name, width, ...layout});
    const filename = `${name}-${width}.png`;
    await page.screenshot({path: path.join(ARTIFACTS, filename), fullPage: true,
      mask: [page.locator('input[type="password"]'), page.locator('.identity-secret')]});
    result.screenshots.push(filename);
    await check(`layout ${name} ${width}`, () => {
      assert.ok(layout.page <= width + 1, `page width ${layout.page} exceeds ${width}`);
      assert.equal(layout.clipped.length, 0, `visible controls overflow: ${JSON.stringify(layout.clipped)}`);
      assert.ok(layout.dialogs.every((dialog) => dialog.x >= -1 && dialog.right <= width + 1), 'dialog exceeds viewport');
    });
  }
  await page.setViewportSize({width: 1440, height: 1000});
}

async function closeDialog(page) {
  await page.getByRole('dialog').getByRole('button', {name: '关闭', exact: true}).click();
  await page.getByRole('dialog').waitFor({state: 'hidden'});
}

async function editRole(editable, permission = 'ui.edit') {
  await admin.getByRole('tab', {name: '角色', exact: true}).click();
  await admin.getByRole('row').filter({hasText: ROLE}).getByRole('button', {name: '编辑', exact: true}).click();
  const dialog = admin.getByRole('dialog');
  await dialog.locator(`input[name="permissions"][value="${permission}"]`).setChecked(editable);
  const response = admin.waitForResponse((response) => (
    new URL(response.url()).pathname.startsWith('/api/auth/roles/') && response.request().method() === 'PUT'
  ));
  const [saved] = await Promise.all([response, dialog.getByRole('button', {name: '保存角色', exact: true}).click()]);
  assert.equal(saved.status(), 200);
  await dialog.waitFor({state: 'hidden'});
}

try {
  await fs.mkdir(ARTIFACTS, {recursive: true});
  const started = await startServer();
  server = started.run;
  base = `http://127.0.0.1:${started.port}`;
  browser = await chromium.launch({headless: true});
  admin = await newPage('identity');
  await check('real administrator browser login', async () => {
    const data = await login(admin, 'admin', bootstrapPassword);
    adminToken = data.token;
    assert.equal(data.profile.is_superuser, true);
    await admin.getByRole('tab', {name: '成员', exact: true}).waitFor();
  }, true);

  await check('custom role created through the UI', async () => {
    await admin.getByRole('tab', {name: '角色', exact: true}).click();
    await admin.getByRole('button', {name: '新增角色', exact: true}).click();
    const dialog = admin.getByRole('dialog');
    await dialog.getByLabel('角色名称', {exact: true}).fill(ROLE);
    await dialog.locator('input[name="permissions"][value="ui.view"]').check();
    await capture(admin, 'role-dialog');
    await uiSubmit(admin, '/api/auth/roles', 'POST', () => dialog.getByRole('button', {name: '保存角色', exact: true}).click());
    await admin.getByRole('cell', {name: ROLE, exact: true}).waitFor();
  }, true);

  let temporaryPassword;
  await check('member created with real one-time password through the UI', async () => {
    await admin.getByRole('tab', {name: '成员', exact: true}).click();
    await admin.getByRole('button', {name: '新增成员', exact: true}).click();
    const dialog = admin.getByRole('dialog');
    await dialog.getByLabel('用户名', {exact: true}).fill(MEMBER);
    await dialog.getByLabel('姓名', {exact: true}).fill('IAM范围成员');
    for (const checkbox of await dialog.locator('input[name="role_ids"]:checked').all()) await checkbox.uncheck();
    await dialog.getByLabel(ROLE, {exact: true}).check();
    await capture(admin, 'member-dialog');
    const data = await uiSubmit(admin, '/api/auth/users', 'POST', () => dialog.getByRole('button', {name: '创建成员', exact: true}).click());
    temporaryPassword = data.temporary_password;
    assert.ok(temporaryPassword && temporaryPassword.length >= 15, 'backend must provide a temporary password');
    await dialog.locator('.identity-secret').waitFor();
    assert.equal((await dialog.locator('.identity-secret').textContent()) === temporaryPassword, true);
    await capture(admin, 'temporary-password-masked');
    await closeDialog(admin);
  }, true);

  await check('app A scope selected and persisted through the UI', async () => {
    await admin.getByRole('tab', {name: '数据授权', exact: true}).click();
    await admin.getByRole('row').filter({hasText: MEMBER}).getByRole('button', {name: '编辑范围'}).click();
    const dialog = admin.getByRole('dialog');
    await dialog.getByLabel('IAM应用A', {exact: true}).check();
    assert.equal(await dialog.getByLabel('IAM应用B', {exact: true}).isChecked(), false);
    await capture(admin, 'scope-dialog');
    await uiSubmit(admin, `/api/auth/users/${MEMBER}`, 'PUT', () => dialog.getByRole('button', {name: '保存范围'}).click());
    await dialog.waitFor({state: 'hidden'});
    const users = await api(adminToken, '/api/auth/users');
    assert.deepEqual(users.data.users.find((user) => user.username === MEMBER).scope.ui_apps, [APP_A]);
    await capture(admin, 'scope-list');
  }, true);

  member = await newPage('assets');
  await check('temporary member login is gated until a real password change', async () => {
    const data = await login(member, MEMBER, temporaryPassword);
    temporaryToken = data.token;
    assert.equal(data.profile.must_change_password, true);
    await member.getByRole('heading', {name: '修改密码', exact: true}).waitFor();
    assert.equal(await member.locator('#app').isVisible(), false);
    assert.equal((await api(temporaryToken, '/api/modules')).status, 403);
    await capture(member, 'password-gate');
    await member.getByLabel('当前密码', {exact: true}).fill(temporaryPassword);
    await member.getByLabel('新密码', {exact: true}).fill(memberPassword);
    await member.getByLabel('确认新密码', {exact: true}).fill(memberPassword);
    const changed = await uiSubmit(member, '/api/auth/change-password', 'POST', () => member.getByRole('button', {name: '保存密码', exact: true}).click());
    memberToken = changed.token;
    assert.equal(changed.profile.must_change_password, false);
    assert.equal((await api(temporaryToken, '/api/auth/me')).status, 401);
  }, true);

  await check('member sees only app A list and opens its actual YAML through UI', async () => {
    await member.locator('.asset-file-link').filter({hasText: 'scope-a'}).waitFor();
    assert.equal(await member.locator('.asset-file-link').filter({hasText: 'scope-b'}).count(), 0);
    const modules = await api(memberToken, '/api/modules');
    assert.equal(modules.status, 200);
    assert.deepEqual(modules.data, {'IAM-A': ['scope-a.yaml']});
    await capture(member, 'member-assets');
    await member.locator('.asset-file-link').filter({hasText: 'scope-a'}).click();
    await member.locator('#editor').waitFor();
    assert.ok((await member.locator('#editor').inputValue()).includes('IAM_ONLY_A'));
    const detail = await api(memberToken, FILE_A);
    assert.equal(detail.status, 200);
    assert.ok(detail.text.includes('IAM_ONLY_A'));
    await capture(member, 'member-detail');
  });

  await check('direct app B browser URL is denied and does not disclose YAML', async () => {
    const direct = await member.context().newPage();
    try {
      await direct.setExtraHTTPHeaders({Authorization: `Bearer ${memberToken}`});
      const response = await direct.goto(`${base}${FILE_B}`);
      assert.equal(response.status(), 403);
      assert.equal((await direct.locator('body').innerText()).includes('IAM_ONLY_B'), false);
    } finally { await direct.close(); }
  });

  await check('app A file traversal to an existing app B YAML is denied without disclosure', async () => {
    const privateYaml = await fs.readFile(path.join(state, 'tasks/IAM-B/scope-b.yaml'), 'utf8');
    assert.ok(privateYaml.includes('IAM_ONLY_B'), 'traversal target must be an existing private fixture');
    const query = new URLSearchParams({module: 'IAM-A', file: '../IAM-B/scope-b.yaml'});
    const response = await api(memberToken, `/api/file?${query}`);
    assert.equal(response.status, 403);
    assert.equal(response.text.includes('IAM_ONLY_B'), false);
    assert.equal(response.text.includes(privateYaml), false);
  });

  const write = {module: 'IAM-A', file: 'role-change.yaml', content: 'android: {}\ntasks:\n  - name: IAM write\n    flow:\n      - aiAssert: IAM_WRITE\n'};
  await check('readonly member write is denied without side effects', async () => {
    assert.equal((await api(memberToken, '/api/file', 'POST', write)).status, 403);
    await assert.rejects(fs.access(path.join(state, 'tasks/IAM-A/role-change.yaml')));
  });

  await check('baseline runMode alias is denied for readonly and execute-only roles without creating jobs', async () => {
    const jobsFile = path.join(state, 'learning/jobs.json');
    const beforeJobs = await fs.readFile(jobsFile, 'utf8').catch((error) => {
      if (error.code === 'ENOENT') return null;
      throw error;
    });
    const metadataFile = path.join(state, 'learning/task-meta.json');
    const beforeMetadata = await fs.readFile(metadataFile, 'utf8');
    try {
      for (const executable of [false, true]) {
        if (executable) await editRole(true, 'ui.execute');
        const profile = await api(memberToken, '/api/auth/me');
        assert.equal(profile.status, 200);
        assert.equal(profile.data.profile.permissions.includes('ui.execute'), executable);
        assert.equal(profile.data.profile.permissions.includes('ui.baseline'), false);
        assert.equal((await api(memberToken, FILE_A)).status, 200, 'run target must exist and be readable');
        const response = await api(memberToken, '/api/run-request', 'POST', {
          module: 'IAM-A', file: 'scope-a.yaml', run_mode: '', runMode: 'baseline',
          device_strategy: 'auto',
        });
        assert.equal(response.status, 403, `${executable ? 'execute-only' : 'readonly'} baseline request must be denied`);
        if (executable) assert.match(response.text, /ui\.baseline/);
        assert.equal(response.data?.job, undefined);
        const afterJobs = await fs.readFile(jobsFile, 'utf8').catch((error) => {
          if (error.code === 'ENOENT') return null;
          throw error;
        });
        assert.equal(afterJobs, beforeJobs, 'denied requests must not persist jobs');
        assert.equal(await fs.readFile(metadataFile, 'utf8'), beforeMetadata, 'denied requests must not update task metadata');
      }
    } finally {
      await editRole(false, 'ui.execute');
    }
  });

  await check('UI role grant takes effect immediately on the existing member token', async () => {
    await editRole(true);
    const profile = await api(memberToken, '/api/auth/me');
    assert.equal(profile.status, 200);
    assert.ok(profile.data.profile.permissions.includes('ui.edit'));
    assert.equal((await api(memberToken, '/api/file', 'POST', write)).status, 200);
    assert.equal(await fs.readFile(path.join(state, 'tasks/IAM-A/role-change.yaml'), 'utf8'), write.content);
  }, true);

  await check('mixed A/B batch is denied atomically even after edit permission is granted', async () => {
    const beforeA = await fs.readdir(path.join(state, 'tasks/IAM-A'));
    const beforeB = await fs.readdir(path.join(state, 'tasks/IAM-B'));
    const batch = await api(memberToken, '/api/files/op', 'POST', {
      op: 'copy', items: [{module: 'IAM-A', file: 'scope-a.yaml'}, {module: 'IAM-B', file: 'scope-b.yaml'}],
      targetModule: 'IAM-A', app_package: APP_A,
    });
    assert.equal(batch.status, 403);
    assert.deepEqual(await fs.readdir(path.join(state, 'tasks/IAM-A')), beforeA);
    assert.deepEqual(await fs.readdir(path.join(state, 'tasks/IAM-B')), beforeB);
    assert.equal((await api(memberToken, FILE_B)).status, 403);
  });

  await check('UI role removal immediately revokes write without logging the member out', async () => {
    await editRole(false);
    assert.equal((await api(memberToken, '/api/file', 'POST', {...write, content: 'denied'})).status, 403);
    assert.equal((await api(memberToken, FILE_A)).status, 200);
    assert.equal(await fs.readFile(path.join(state, 'tasks/IAM-A/role-change.yaml'), 'utf8'), write.content);
  });

  await check('UI administrator disable invalidates the existing member token', async () => {
    await admin.getByRole('tab', {name: '成员', exact: true}).click();
    await admin.getByRole('row').filter({hasText: MEMBER}).getByRole('button', {name: '停用', exact: true}).click();
    await capture(admin, 'disable-dialog');
    await uiSubmit(admin, `/api/auth/users/${MEMBER}`, 'PUT', () => admin.getByRole('dialog').getByRole('button', {name: '确认', exact: true}).click());
    assert.equal((await api(memberToken, '/api/auth/me')).status, 401);
    assert.equal((await api(memberToken, FILE_A)).status, 401);
  }, true);

  await check('disabled member remains revoked after actual server restart', async () => {
    const port = Number(new URL(base).port);
    await stopServer(server);
    server = (await startServer(port)).run;
    assert.equal((await api(memberToken, '/api/auth/me')).status, 401);
    assert.equal((await api(memberToken, FILE_A)).status, 401);
    const retained = await api(adminToken, '/api/auth/me');
    assert.equal(retained.status, 200, 'restart must preserve valid administrator sessions');
    const loginResponse = await fetch(`${base}/api/auth/login`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: MEMBER, password: memberPassword}),
    });
    assert.equal(loginResponse.status, 401);
    const rejectedSession = member.waitForResponse((response) => (
      new URL(response.url()).pathname === '/api/auth/me' && response.status() === 401
    ));
    await member.reload({waitUntil: 'domcontentloaded'});
    await rejectedSession;
    await member.waitForLoadState('networkidle');
    await member.locator('#login-screen').waitFor({state: 'visible'});
    await capture(member, 'revoked-login');
  }, true);

  await check('no browser runtime or server contract errors', () => {
    assert.deepEqual(browserErrors, []);
    assert.deepEqual(requestFailures, []);
  });
  await check('authorized member pages do not issue forbidden background requests', () => {
    assert.deepEqual(deniedBackgroundRequests, []);
  });
} catch (error) {
  if (!result.failed) {
    result.failed += 1;
    result.failures.push({check: 'fixture setup or uncaught failure', error: redact(error.message)});
  }
} finally {
  await browser?.close().catch((error) => {
    result.failed += 1;
    result.failures.push({check: 'browser cleanup', error: redact(error.message)});
  });
  for (const run of serverRuns) await stopServer(run).catch((error) => {
    result.failed += 1;
    result.failures.push({check: 'fixture cleanup', error: redact(error.message)});
  });
  await check('no outbound server connections or running child processes', () => {
    assert.equal(result.outboundAttempts, 0);
    assert.ok(serverRuns.every((run) => run.child.exitCode !== null || run.child.signalCode !== null));
  });
  await fs.rm(state, {recursive: true, force: true});
  await fs.mkdir(ARTIFACTS, {recursive: true});
  await fs.writeFile(path.join(ARTIFACTS, 'result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({passed: result.passed, failed: result.failed, screenshots: result.screenshots.length}));
  process.exitCode = result.failed ? 1 : 0;
}
