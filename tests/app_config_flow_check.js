// Run with: node tests/app_config_flow_check.js
// Uses the real form functions and HTML; no browser, server, or real network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { JSDOM } = require('../api-testing-ui/node_modules/jsdom');

const ROOT = path.resolve(__dirname, '..');
const APP_A = 'com.fixture.app.a';
const APP_B = 'com.fixture.app.b';

function loadFunctions(context, filename, names) {
  const source = fs.readFileSync(path.join(ROOT, filename), 'utf8');
  for (const name of names) {
    const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
    assert.notEqual(start, -1, `${filename} must define ${name}`);
    const remainder = source.slice(start);
    const next = remainder.slice(1).search(/^(?:async )?function [A-Za-z_$][\w$]*\(/m);
    vm.runInContext(next === -1 ? remainder : remainder.slice(0, next + 1), context, { filename });
  }
}

function fixture(t, { workflow = 'app_config', deleteWait, catalogLoaded = true, catalogError = null } = {}) {
  // jsdom does not load subresources or run page scripts by default.
  const dom = new JSDOM(fs.readFileSync(path.join(ROOT, 'task-manager.html'), 'utf8'));
  t.after(() => dom.window.close());
  const calls = [];
  const toasts = [];
  let catalogReloads = 0;
  let feedbackClears = 0;
  const forbiddenNetwork = [];
  const blockNetwork = () => {
    forbiddenNetwork.push('unexpected real network request');
    throw new Error('Real network is forbidden in this test');
  };
  dom.window.fetch = blockNetwork;
  dom.window.XMLHttpRequest = blockNetwork;
  dom.window.WebSocket = blockNetwork;
  dom.window.navigator.sendBeacon = blockNetwork;
  const context = vm.createContext({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    fetch: blockNetwork,
    XMLHttpRequest: blockNetwork,
    WebSocket: blockNetwork,
    taskApps: [
      {
        package: APP_A, name: '应用甲', enabled: true, modules: ['模块甲'],
        business_lines: [{ id: 'school', name: '校园版', enabled: true }],
        sonic_project_name: '测试项目', sonic_project_id: 'fixture-project',
        sonic_suite_name: '测试套', sonic_suite_id: 'fixture-suite',
        feishu_webhook: 'https://example.invalid/fixture-only-webhook',
        feishu_ready: true, feishu_source: 'app',
      },
      { package: APP_B, name: '应用乙', enabled: false, modules: ['模块乙'], business_lines: [{ id: 'home', name: '家用', enabled: true }], feishu_ready: false, feishu_source: 'missing' },
    ],
    modules: { '模块甲': {}, '模块乙': {}, '模块丙': {} },
    AppState: {loaded: {taskApps: catalogLoaded}, errors: {taskApps: catalogError}},
    activeWorkflow: workflow,
    feishuDrafts: [],
    runnerDevices: [{id: 'fixture-device', online: true}],
    confirm: () => true,
    showToast: (message, type) => toasts.push({ message, type }),
    hideToast: () => { feedbackClears += 1; },
    loadModules: async () => {
      catalogReloads += 1;
      context.AppState.loaded.taskApps = true;
      context.AppState.errors.taskApps = null;
    },
    // Unrelated sidebar and YAML controls are outside this form regression.
    renderModules() {},
    refreshBusinessLineControls() {},
    setFileContextVisible() {},
    updateToolbarState() {},
    setActiveWorkflow(value) { context.activeWorkflow = value; },
    apiRequest: async (url, options = {}) => {
      const call = { url, method: options.method || 'GET' };
      if (options.body) call.body = JSON.parse(options.body);
      calls.push(call);
      if (call.method === 'DELETE' && url.startsWith('/task-app?package=')) {
        if (deleteWait) await deleteWait;
        return { ok: true };
      }
      if (call.method === 'POST' && url === '/task-app') return { ok: true, app: call.body };
      throw new Error(`Unexpected API request: ${call.method} ${url}`);
    },
  });
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'js/form-steps.js'), 'utf8'), context);
  loadFunctions(context, 'js/app.js', ['escapeHtml', 'jsArg']);
  loadFunctions(context, 'js/utils.js', ['defaultBusinessLines', 'taskAppBusinessLines']);
  loadFunctions(context, 'js/app.js', [
    'selectedGenerateApplication', 'enabledGenerateApplications', 'renderGenerateBusinessOptions',
  ]);
  loadFunctions(context, 'js/agent-status.js', [
    'resetYamlToolbarForManager', 'setManagementToolbar', 'taskAppCatalogPageState', 'taskAppCatalogNoticeHtml',
    'showAppConfigCenter', 'reloadTaskAppCatalog', 'showFeishuConfigCenter', 'showSonicConfigCenter',
    'showTaskApps', 'setTaskAppEditorContext', 'openTaskAppEditor', 'nextTaskAppStep', 'validateTaskAppBasicInfo', 'goToTaskAppStep',
    'openUnassignedTaskAppModules', 'selectTaskAppModuleOwner', 'renderTaskAppModuleOwnerGuide',
    'isTemporaryAgentModule', 'taskAppBusinessModuleNames',
    'renderTaskAppModal', 'renderTaskAppBusinessLineEditor', 'readTaskAppBusinessLines',
    'addTaskAppBusinessLine', 'removeTaskAppBusinessLine', 'filterTaskAppModules', 'clearTaskAppForm',
    'editTaskApp', 'taskAppFeishuReady', 'taskAppFeishuLabel', 'setSonicMigrationAvailability',
    'renderTaskAppList', 'saveTaskApp', 'deleteTaskApp',
  ]);
  const run = code => vm.runInContext(code, context);
  const field = id => dom.window.document.getElementById(id);
  const selectedModules = () => Array.from(dom.window.document.querySelectorAll('.task-app-module-check:checked'), input => input.value).sort();
  const selectModule = (name, checked) => {
    const input = Array.from(dom.window.document.querySelectorAll('.task-app-module-check')).find(item => item.value === name);
    assert.ok(input, `Module checkbox must exist: ${name}`);
    input.checked = checked;
  };
  const expectCalls = expected => {
    assert.deepEqual(calls.map(({ method, url }) => `${method} ${url}`), expected);
    assert.deepEqual(forbiddenNetwork, []);
  };
  t.after(() => assert.deepEqual(forbiddenNetwork, []));
  return {
    run, field, calls, toasts, selectedModules, selectModule, expectCalls,
    catalogReloads: () => catalogReloads,
    feedbackClears: () => feedbackClears,
  };
}

test('opening or switching the application editor clears stale operation feedback', t => {
  const f = fixture(t);
  f.run('openTaskAppEditor()');
  f.run(`editTaskApp('${APP_A}')`);
  assert.equal(f.feedbackClears(), 2);
});

test('new application entry clears the previously edited application draft', t => {
  const f = fixture(t);
  f.run(`openTaskAppEditor('${APP_A}', 3)`);
  assert.equal(f.field('task-app-package').value, APP_A);
  assert.deepEqual(f.selectedModules(), ['模块甲']);

  f.run('openTaskAppEditor()');

  assert.equal(f.run('FormSteps.currentStep'), 0);
  assert.equal(f.field('task-app-name').value, '');
  assert.equal(f.field('task-app-package').value, '');
  assert.equal(f.field('task-app-sonic-project-name').value, '');
  assert.equal(f.field('task-app-feishu-webhook').value, '');
  assert.deepEqual(f.selectedModules(), []);
  assert.deepEqual(
    Array.from(f.field('task-app-business-lines').querySelectorAll('.task-app-business-name'), input => input.value),
    ['家用', '共享'],
  );
  assert.equal(f.field('task-app-modal-title').textContent, '新增应用');
  assert.match(f.field('task-app-edit-context').textContent, /正在新建应用/);
});

test('application editor names the target app and assignment purpose', t => {
  const f = fixture(t);

  f.run(`openTaskAppEditor('${APP_A}', 2)`);
  assert.equal(f.field('task-app-modal-title').textContent, '编辑应用 · 应用甲');
  assert.match(f.field('task-app-edit-context').textContent, /当前编辑：应用甲/);
  assert.match(f.field('task-app-edit-context').textContent, /群通知/);

  f.run('openUnassignedTaskAppModules()');
  assert.equal(f.field('task-app-modal-title').textContent, '分配未归属模块');
  assert.match(f.field('task-app-edit-context').textContent, /先选择目标应用/);

  f.run(`selectTaskAppModuleOwner('${APP_B}')`);
  assert.equal(f.field('task-app-modal-title').textContent, '分配模块 · 应用乙');
  assert.match(f.field('task-app-edit-context').textContent, /当前归入：应用乙/);
});

test('generation asks for an application before diagnosing its business configuration', t => {
  const f = fixture(t);
  f.field('generate-application').innerHTML = '<option value="">选择已启用应用</option>';
  f.field('generate-application').value = '';

  f.run(`renderGenerateBusinessOptions('')`);

  const text = f.field('generate-business-options').textContent;
  assert.match(text, /请先选择应用/);
  assert.doesNotMatch(text, /当前应用没有启用的业务线/);
});

test('application ownership hides cache and Agent history modules', t => {
  const f = fixture(t);
  f.run(`modules.cache = []; modules['AI_Agent_草稿_agent-1'] = []; modules['AI_Agent_修复重跑_agent-2'] = ['repair.yaml']`);

  const names = f.run('taskAppBusinessModuleNames()');

  assert.deepEqual(Array.from(names), ['模块丙', '模块乙', '模块甲']);
});

test('application center shows a loading state instead of false zero counts', t => {
  const f = fixture(t, {catalogLoaded: false});
  f.run('showAppConfigCenter()');
  const text = f.field('editor-area').textContent;
  assert.match(text, /正在加载应用配置/);
  assert.doesNotMatch(text, /0 个应用/);
});

test('notification center does not publish false readiness while applications are loading', t => {
  const f = fixture(t, {workflow: 'feishu_config', catalogLoaded: false});
  f.run('showFeishuConfigCenter()');
  const text = f.field('editor-area').textContent;
  assert.match(text, /正在加载应用配置/);
  assert.doesNotMatch(text, /1\/2/);
});

test('execution environment does not publish false Sonic binding counts while applications are loading', t => {
  const f = fixture(t, {workflow: 'sonic_config', catalogLoaded: false});
  f.run('showSonicConfigCenter()');
  const text = f.field('editor-area').textContent;
  assert.match(text, /正在加载应用配置/);
  assert.match(text, /1在线设备/);
  assert.doesNotMatch(text, /1\/2已绑定 Sonic 应用/);
});

test('execution environment edit binding opens the Sonic step directly', t => {
  const f = fixture(t, {workflow: 'sonic_config'});
  f.run('showSonicConfigCenter()');
  const configure = f.field('editor-area').querySelector('.management-row button');

  f.run(configure.getAttribute('onclick'));

  assert.equal(f.run('FormSteps.currentStep'), 1);
  assert.equal(f.field('task-app-sonic-project-name').closest('.form-step-content').style.display, 'block');
  assert.equal(f.field('task-app-package').value, APP_A);
});

test('catalog retry rerenders the notification center with the loaded readiness count', async t => {
  const f = fixture(t, {workflow: 'feishu_config', catalogLoaded: false});
  f.run('showFeishuConfigCenter()');
  await f.run('reloadTaskAppCatalog()');
  assert.equal(f.catalogReloads(), 1);
  assert.match(f.field('editor-area').textContent, /1\/2通知可用应用/);
});

test('deleting another app preserves saved module assignments in the next save', async t => {
  const f = fixture(t);
  f.run(`openTaskAppEditor('${APP_A}')`);
  assert.deepEqual(f.selectedModules(), ['模块甲']);
  await f.run(`deleteTaskApp('${APP_B}')`);
  assert.equal(f.field('task-app-package').value, APP_A);
  assert.deepEqual(f.selectedModules(), ['模块甲']);
  await f.run('saveTaskApp()');
  assert.deepEqual(f.calls.at(-1).body.modules, ['模块甲']);
  assert.equal(f.calls.at(-1).body.package, APP_A);
  f.expectCalls([`DELETE /task-app?package=${APP_B}`, 'POST /task-app']);
});

test('deleting another app preserves unsaved names, business lines, and module additions', async t => {
  const f = fixture(t);
  f.run(`openTaskAppEditor('${APP_A}')`);
  f.field('task-app-name').value = '应用甲未保存修改';
  f.field('task-app-business-lines').querySelector('input').value = '企业版';
  f.selectModule('模块丙', true);
  await f.run(`deleteTaskApp('${APP_B}')`);
  assert.equal(f.field('task-app-name').value, '应用甲未保存修改');
  assert.deepEqual(f.selectedModules(), ['模块丙', '模块甲'].sort());
  await f.run('saveTaskApp()');
  assert.deepEqual(f.calls.at(-1).body.modules.sort(), ['模块丙', '模块甲'].sort());
  assert.equal(f.calls.at(-1).body.business_lines[0].name, '企业版');
  f.expectCalls([`DELETE /task-app?package=${APP_B}`, 'POST /task-app']);
});

test('deleting another app retains the latest module changes made while DELETE is pending', async t => {
  let finishDelete;
  const deleteWait = new Promise(resolve => { finishDelete = resolve; });
  const f = fixture(t, { deleteWait });
  f.run(`openTaskAppEditor('${APP_A}')`);
  const pending = f.run(`deleteTaskApp('${APP_B}')`);
  f.selectModule('模块甲', false);
  f.selectModule('模块丙', true);
  finishDelete();
  await pending;
  assert.deepEqual(f.selectedModules(), ['模块丙']);
  await f.run('saveTaskApp()');
  assert.deepEqual(f.calls.at(-1).body.modules, ['模块丙']);
  f.expectCalls([`DELETE /task-app?package=${APP_B}`, 'POST /task-app']);
});

test('deletion does not clear an app opened while the request is pending', async t => {
  let finishDelete;
  const deleteWait = new Promise(resolve => { finishDelete = resolve; });
  const f = fixture(t, { deleteWait });
  f.run(`openTaskAppEditor('${APP_B}')`);
  const pending = f.run(`deleteTaskApp('${APP_B}')`);
  f.run(`editTaskApp('${APP_A}')`);
  f.selectModule('模块丙', true);
  finishDelete();
  await pending;
  assert.equal(f.field('task-app-package').value, APP_A);
  assert.deepEqual(f.selectedModules(), ['模块丙', '模块甲'].sort());
  f.expectCalls([`DELETE /task-app?package=${APP_B}`]);
});

test('deleting the current app clears the form and cannot recreate a ghost draft on save', async t => {
  const f = fixture(t);
  f.run(`openTaskAppEditor('${APP_A}', 3)`);
  f.selectModule('模块丙', true);
  await f.run(`deleteTaskApp('${APP_A}')`);
  for (const id of ['task-app-package', 'task-app-name', 'task-app-feishu-webhook', 'task-app-sonic-project-id', 'task-app-sonic-project-name', 'task-app-sonic-suite-id', 'task-app-sonic-suite-name']) {
    assert.equal(f.field(id).value, '', `${id} must be cleared`);
  }
  assert.deepEqual(f.selectedModules(), []);
  assert.equal(f.field('task-app-enabled').checked, true);
  assert.doesNotMatch(f.field('task-app-business-lines').innerHTML, /校园版/);
  assert.equal(f.run('FormSteps.currentStep'), 0);
  assert.equal(f.field('task-app-name').closest('.form-step-content').style.display, 'block');
  assert.doesNotMatch(f.field('task-app-list').textContent, /应用甲/);
  assert.doesNotMatch(f.field('editor-area').textContent, /应用甲/);
  assert.match(f.field('editor-area').textContent, /应用乙/);
  await f.run('saveTaskApp()');
  assert.match(f.toasts.at(-1).message, /填写应用中文名和包名/);
  f.expectCalls([`DELETE /task-app?package=${APP_A}`]);
});

test('notification configuration still opens its visible target step without sending', async t => {
  const f = fixture(t, { workflow: 'feishu_config' });
  f.run('showFeishuConfigCenter()');
  const configure = f.field('editor-area').querySelector('.management-row button');
  f.run(configure.getAttribute('onclick'));
  assert.equal(f.run('FormSteps.currentStep'), 2);
  assert.equal(f.field('task-app-feishu-webhook').closest('.form-step-content').style.display, 'block');
  assert.equal(f.field('task-app-package').value, APP_A);
  await f.run(`deleteTaskApp('${APP_B}')`);
  assert.equal(f.run('FormSteps.currentStep'), 2);
  assert.doesNotMatch(f.field('editor-area').textContent, /应用乙/);
  assert.match(f.field('editor-area').textContent, /应用甲/);
  f.expectCalls([`DELETE /task-app?package=${APP_B}`]);
});

test('notification readiness uses server evidence instead of package-name guesses', t => {
  const f = fixture(t, { workflow: 'feishu_config' });
  f.run(`taskApps = [{package: 'com.kfb.model', name: '智小白3D', feishu_ready: false, feishu_source: 'missing'}]`);
  assert.equal(f.run('taskAppFeishuReady(taskApps[0])'), false);
  assert.equal(f.run('taskAppFeishuLabel(taskApps[0])'), '飞书：未配置');
  f.run('showFeishuConfigCenter()');
  assert.match(f.field('editor-area').textContent, /0\/1/);
  assert.match(f.field('editor-area').textContent, /请先配置机器人 Webhook/);
});

test('unassigned-module entry requires an explicit target app before saving', t => {
  const f = fixture(t);
  f.run('openUnassignedTaskAppModules()');
  assert.equal(f.run('FormSteps.currentStep'), 3);
  assert.equal(f.field('task-app-name').value, '');
  assert.equal(f.field('task-app-module-unassigned').checked, true);
  assert.equal(f.field('task-app-module-owner-guide').textContent.includes('先选择归属应用'), true);
  assert.equal(f.field('modal-task-apps').querySelector('.step-submit-btn').disabled, true);

  f.run(`selectTaskAppModuleOwner('${APP_A}')`);
  assert.equal(f.run('FormSteps.currentStep'), 3);
  assert.equal(f.field('task-app-name').value, '应用甲');
  assert.equal(f.field('task-app-module-unassigned').checked, true);
  assert.equal(f.field('modal-task-apps').querySelector('.step-submit-btn').disabled, false);
  assert.match(f.field('task-app-module-owner-guide').textContent, /当前归入：应用甲/);
});

test('extra business lines can be removed before save', t => {
  const f = fixture(t);
  f.run(`openTaskAppEditor('${APP_A}')`);
  f.run('addTaskAppBusinessLine()');
  assert.equal(f.field('task-app-business-lines').children.length, 2);
  f.run('removeTaskAppBusinessLine(1)');
  assert.equal(f.field('task-app-business-lines').children.length, 1);
  assert.equal(f.field('task-app-business-lines').querySelector('input').value, '校园版');
});

test('Sonic cleanup is disabled when the scan has no automatic matches', t => {
  const f = fixture(t);
  f.run('setSonicMigrationAvailability(0, 37)');
  assert.equal(f.field('sonic-action-migrate').disabled, true);
  assert.match(f.field('sonic-action-migrate').title, /0 条可自动清理/);
  f.run('setSonicMigrationAvailability(2, 0)');
  assert.equal(f.field('sonic-action-migrate').disabled, false);
});

test('editing from the final wizard step reveals and focuses the selected app basics', t => {
  const f = fixture(t);
  f.run(`openTaskAppEditor('${APP_A}', 3)`);
  const edit = f.field('task-app-list').querySelector('.app-row button');
  f.run(edit.getAttribute('onclick'));
  assert.equal(f.run('FormSteps.currentStep'), 0);
  assert.equal(f.field('task-app-name').closest('.form-step-content').style.display, 'block');
  assert.equal(f.field('task-app-name').ownerDocument.activeElement, f.field('task-app-name'));
  f.expectCalls([]);
});
