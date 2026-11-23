const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

test('release core selection crosses pages and stays editable through start and preflight', async t => {
  const browser = await chromium.launch();
  t.after(() => browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(4000);
  const origin = 'http://127.0.0.1';
  const app = 'com.kfb.model';
  const root = path.resolve(__dirname, '..');
  const profile = {user_id:'fixture', username:'fixture', scope:{ui_apps:[app]}, permissions:['ui.view','ui.execute']};
  const apps = [{app_id:app, name:'智小白3D'}];
  const row = (id, core) => ({case_id:id, task_name:id, revision:'a'.repeat(64), app_id:app,
    module:'3D打印基线', platform:'android', execution_eligible:true, release_core:core});
  const first = row('核心甲', true), second = row('核心乙', true), extra = row('普通用例', false);
  const submitted = [];
  await page.addInitScript(() => {
    sessionStorage.setItem('sessionToken', 'fixture');
    sessionStorage.setItem('midscene_active_workflow', 'ui_library');
  });
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === '/task-manager.html' || /^\/(css|js|assets)\//.test(url.pathname))
      return route.fulfill({path:path.join(root, url.pathname)});
    let body = {ok:true, items:[], groups:[], modules:[], apps:[]};
    if (url.pathname === '/api/auth/me') body = {ok:true, user:'fixture', profile};
    if (url.pathname === '/api/ui-executions/availability') body = {ok:true, enabled:true};
    if (url.pathname === '/api/ui-executions/catalog') {
      const scoped = url.searchParams.get('app_id') === app;
      body = {ok:true, apps, modules:['3D打印基线'], items:scoped?[first,extra]:[], groups:[], total:3,
        next_cursor:scoped?'next':null, release_core:scoped?{total:2,cases:[second,first]}:null};
    }
    if (url.pathname === '/api/ui-executions/devices') body = {ok:true,devices:[
      {name:'测试 Android',target:{runner_id:'fixture',device_id:'fixture-phone',platform:'android'},connection_status:'ready'}]};
    if (route.request().method() === 'POST') {
      submitted.push({path:url.pathname, body:route.request().postDataJSON()});
      if (url.pathname === '/api/ui-executions/preflight') body = {ok:true,ready:false,blockers:[{code:'revision_stale',message:'用例版本已更新'}]};
    }
    return route.fulfill({json:body});
  });
  await page.goto(origin + '/task-manager.html');
  const library = page.locator('.ui-simple[data-ui-mode="library"]');
  await library.locator('[name="ui-app"]').selectOption(app);
  await library.getByText('核心流程', {exact:true}).waitFor();
  await library.locator('[data-case-id="普通用例"]').check();
  await library.getByRole('button', {name:/选择核心流程/}).click();
  assert.equal(await library.locator('[data-case-id="普通用例"]').isChecked(), false);
  await library.getByRole('button', {name:'取消选择 核心乙'}).waitFor();
  assert.equal(await library.locator('[data-case-id="核心甲"]').isChecked(), true);
  await library.locator('[data-case-id="核心甲"]').uncheck();
  await library.locator('[data-case-id="普通用例"]').check();
  await library.getByRole('button', {name:/去开始测试/}).click();
  const startPage = page.locator('.ui-simple[data-ui-mode="start"]');
  await startPage.getByRole('button', {name:'取消选择 核心乙'}).waitFor();
  assert.equal(await startPage.locator('[data-case-id="普通用例"]').isChecked(), true);
  const start = startPage.getByRole('button', {name:'开始测试',exact:true});
  assert.equal(await start.isDisabled(), true);
  assert.equal(await startPage.locator('[data-ui-release]').isChecked(), false);
  await startPage.getByRole('button', {name:/选择核心流程/}).click();
  assert.equal(await startPage.locator('[data-case-id="普通用例"]').isChecked(), false);
  await startPage.locator('[data-case-id="核心甲"]').uncheck();
  await startPage.locator('[data-case-id="普通用例"]').check();
  await startPage.locator('[data-ui-release]').check();
  await startPage.locator('[data-device-key]').check();
  await start.click();
  await startPage.getByText(/用例版本已更新/).waitFor();
  assert.deepEqual(submitted.map(r=>r.path), ['/api/ui-executions/preflight']);
  assert.deepEqual(submitted[0].body.release_report, {enabled:true});
  assert.deepEqual(submitted[0].body.cases.map(c=>c.case_id), ['核心乙','普通用例']);
});
