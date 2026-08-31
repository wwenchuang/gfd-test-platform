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

function fixture(t, { workflow = 'app_config', deleteWait } = {}) {
  // jsdom does not load subresources or run page scripts by default.
  const dom = new JSDOM(fs.readFileSync(path.join(ROOT, 'task-manager.html'), 'utf8'));
  t.after(() => dom.window.close());
  const calls = [];
  const toasts = [];
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
      },
      { package: APP_B, name: '应用乙', enabled: false, modules: ['模块乙'], business_lines: [{ id: 'home', name: '家用', enabled: true }] },
    ],
    modules: { '模块甲': {}, '模块乙': {}, '模块丙': {} },
    activeWorkflow: workflow,
    feishuDrafts: [],
    confirm: () => true,
    showToast: (message, type) => toasts.push({ message, type }),
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
  loadFunctions(context, 'js/utils.js', ['defaultBusinessLines']);
  loadFunctions(context, 'js/agent-status.js', [
    'resetYamlToolbarForManager', 'setManagementToolbar', 'showAppConfigCenter', 'showFeishuConfigCenter',
    'showTaskApps', 'openTaskAppEditor', 'isTemporaryAgentModule', 'taskAppBusinessModuleNames',
    'renderTaskAppModal', 'renderTaskAppBusinessLineEditor', 'readTaskAppBusinessLines',
    'filterTaskAppModules', 'clearTaskAppForm', 'editTaskApp', 'taskAppFeishuLabel',
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
  return { run, field, calls, toasts, selectedModules, selectModule, expectCalls };
}

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
