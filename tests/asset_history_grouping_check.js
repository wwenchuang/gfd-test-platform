const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const {JSDOM} = require('../api-testing-ui/node_modules/jsdom');

function loadFunction(window, source, name) {
  const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
  assert.notEqual(start, -1, `${name} must exist`);
  const rest = source.slice(start);
  const next = rest.slice(1).search(/^(?:async )?function [\w$]+\(/m);
  window.eval(next < 0 ? rest : rest.slice(0, next + 1));
}

function createWindow() {
  const dom = new JSDOM(`
    <body>
      <input id="asset-search">
      <input id="task-search">
      <select id="asset-app-filter"><option value=""></option></select>
      <select id="app-filter"><option value=""></option></select>
      <select id="new-task-module"></select>
      <div id="module-list"></div>
    </body>
  `, {runScripts: 'dangerously'});
  const win = dom.window;
  Object.assign(win, {
    modules: {
      '3D打印基线': ['print.yaml'],
      '共享业务基线': ['shared.yaml'],
      'AI_Agent_修复重跑_agent-123': ['repair.yaml'],
      'AI_Agent_草稿_agent-456': [],
      cache: [],
    },
    taskApps: [],
    currentModule: null,
    currentFile: null,
    libraryView: 'module',
    assetModuleScope: 'business',
    assetListPage: 1,
    selectedFiles: new Set(),
    escapeHtml: value => String(value),
    jsArg: value => JSON.stringify(value),
    taskAppBusinessLines: () => [],
    moduleApp: () => null,
    moduleFileRows: () => [
      {mod: '3D打印基线', file: 'print.yaml', meta: {status: 'baseline'}, job: {status: 'success'}, time: 2},
      {mod: '共享业务基线', file: 'shared.yaml', meta: {status: 'baseline'}, job: {status: 'success'}, time: 1},
      {mod: 'AI_Agent_修复重跑_agent-123', file: 'repair.yaml', meta: {status: 'draft'}, job: {}, time: 0},
    ],
    latestJobForFile: () => ({}),
    yamlStatsForFile: () => ({}),
    mergeYamlStats: () => ({}),
    prioritySummaryText: () => '1 用例',
    lifecycleText: value => value || '',
    jobStatusText: value => value || '',
    fileKey: (mod, file) => `${mod}::${file}`,
    resetYamlToolbarForManager: () => {},
    renderModules: () => {},
    showAssetsCenter: () => {},
    resetAssetListPage: () => { win.assetListPage = 1; },
    renderAppFilter: () => {},
    renderModuleSelects: () => {},
    sonicBadgeHtml: () => '',
    fileMeta: () => ({status: ''}),
    yamlDisplayName: file => file.replace(/\.ya?ml$/i, ''),
    hasOpenEditor: () => false,
    activeWorkflow: '',
  });
  return {dom, win};
}

test('asset modules keep generated repair history out of business and baseline groups', t => {
  const source = fs.readFileSync('js/agent-status.js', 'utf8');
  const {dom, win} = createWindow();
  t.after(() => dom.window.close());
  for (const name of ['isAssetHistoryModule', 'assetRowsForCurrentFilters', 'assetModuleListHtml', 'selectAssetModuleScope']) {
    loadFunction(win, source, name);
  }

  const html = win.assetModuleListHtml();
  assert.match(html, /3D打印基线/);
  assert.match(html, /共享业务基线/);
  assert.match(html, /AI 生成\/修复历史/);
  assert.match(html, /3 个历史模块 · 1 个 YAML · 2 个空模块/);
  assert.doesNotMatch(html, /修复重跑_agent-123|草稿_agent-456|>cache</);
  assert.deepEqual(win.assetRowsForCurrentFilters().map(row => row.mod), ['3D打印基线', '共享业务基线']);

  win.selectAssetModuleScope('history');
  assert.equal(win.assetModuleScope, 'history');
  assert.deepEqual(win.assetRowsForCurrentFilters().map(row => row.mod), ['AI_Agent_修复重跑_agent-123']);
});

test('new and uploaded YAML cannot be assigned to generated repair history', t => {
  const source = fs.readFileSync('js/agent-status.js', 'utf8');
  const {dom, win} = createWindow();
  t.after(() => dom.window.close());
  loadFunction(win, source, 'isAssetHistoryModule');
  loadFunction(win, source, 'fillModuleSelect');

  win.fillModuleSelect(win.document.getElementById('new-task-module'), '选择所属模块');
  const labels = Array.from(win.document.querySelectorAll('#new-task-module option'), option => option.textContent);
  assert.deepEqual(labels, ['选择所属模块', '3D打印基线', '共享业务基线']);
});

test('left YAML directory keeps generated files in one history group', t => {
  const source = fs.readFileSync('js/agent-status.js', 'utf8');
  const {dom, win} = createWindow();
  t.after(() => dom.window.close());
  loadFunction(win, source, 'isAssetHistoryModule');
  loadFunction(win, source, 'renderModules');

  win.renderModules();
  const businessLabels = Array.from(
    win.document.querySelectorAll('.module-item:not(.module-history-group) .module-name'),
    item => item.textContent,
  );
  assert.deepEqual(businessLabels, ['3D打印基线', '共享业务基线']);
  const historyGroup = win.document.querySelector('.module-history-group');
  assert.equal(historyGroup.querySelector('.module-name').textContent, 'AI 生成/修复历史');
  assert.equal(historyGroup.querySelectorAll('.task-item').length, 1);
  assert.match(historyGroup.textContent, /repair/);
  assert.doesNotMatch(historyGroup.textContent, /草稿_agent-456|cache/);
});

test('empty generated history modules can be cleaned in one confirmed operation', async t => {
  const source = fs.readFileSync('js/agent-status.js', 'utf8');
  const {dom, win} = createWindow();
  t.after(() => dom.window.close());
  const requests = [];
  Object.assign(win, {
    confirm: message => {
      assert.match(message, /2 个空的 AI 生成\/修复历史模块/);
      assert.match(message, /不会删除任何 YAML/);
      return true;
    },
    apiRequest: async (path, options) => requests.push([path, options.method]),
    requireUiDeletePermission: () => true,
    showToast: () => {},
  });
  loadFunction(win, source, 'isAssetHistoryModule');
  loadFunction(win, source, 'cleanupEmptyAssetHistoryModules');

  await win.cleanupEmptyAssetHistoryModules();
  assert.deepEqual(requests, [
    ['/module?module=AI_Agent_%E8%8D%89%E7%A8%BF_agent-456', 'DELETE'],
    ['/module?module=cache', 'DELETE'],
  ]);
  assert.deepEqual(Object.keys(win.modules).sort(), [
    '3D打印基线',
    '共享业务基线',
    'AI_Agent_修复重跑_agent-123',
  ].sort());
});
