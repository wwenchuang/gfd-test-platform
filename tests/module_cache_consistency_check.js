const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

const casesSource = fs.readFileSync('js/cases.js', 'utf8');
const executionSource = fs.readFileSync('js/execution.js', 'utf8');
const navigationSource = fs.readFileSync('js/agent-status.js', 'utf8');

function loadFunction(context, source, name) {
  const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
  assert.notEqual(start, -1, `${name} must exist`);
  const rest = source.slice(start);
  const next = rest.slice(1).search(/^(?:async )?function [\w$]+\(/m);
  vm.runInContext(next < 0 ? rest : rest.slice(0, next + 1), context);
}

function modulesFixture() {
  let requestCount = 0;
  let fail = false;
  let serverModules = { old: ['original.yaml'] };
  const toasts = [];
  const context = vm.createContext({
    AppState: { loaded: { modules: false, taskApps: false }, loading: {}, errors: {} },
    modules: {}, modulesLoaded: false, taskApps: [], taskMeta: {}, sonicCaseRows: [],
    activeWorkflow: 'assets',
    apiRequest: async path => {
      if (path === '/modules') {
        requestCount++;
        if (fail) throw new Error('module unavailable');
        return serverModules;
      }
      if (path === '/task-apps?include_disabled=1') return { apps: [] };
      if (path === '/task-meta') return { meta: {} };
      throw new Error(`Unexpected ${path}`);
    },
    canAccessGlobalSonic: () => false, refreshBusinessLineControls() {}, renderModules() {},
    warmupYamlStats: async () => {}, showToast: (message, type) => toasts.push({ message, type }),
  });
  loadFunction(context, casesSource, 'loadModules');
  return {context, toasts, get requestCount() { return requestCount; }, set fail(value) { fail = value; }, set serverModules(value) { serverModules = value; }};
}

test('module failure stays retryable and never inserts fictitious assets', async () => {
  const f = modulesFixture();
  f.fail = true;
  await vm.runInContext('loadModules()', f.context);
  assert.equal(f.context.AppState.loaded.modules, false);
  assert.equal(Object.keys(f.context.modules).length, 0);
  assert.match(f.toasts.at(-1).message, /module unavailable/);
  f.fail = false;
  await vm.runInContext('loadModules()', f.context);
  assert.equal(f.requestCount, 2);
  assert.deepEqual(Object.keys(f.context.modules), ['old']);
});

test('refresh failure preserves the last confirmed module tree', async () => {
  const f = modulesFixture();
  await vm.runInContext('loadModules()', f.context);
  f.fail = true;
  await vm.runInContext('loadModules({force:true})', f.context);
  assert.deepEqual(Object.keys(f.context.modules), ['old']);
  assert.equal(f.context.AppState.loaded.modules, false);
  assert.match(f.context.AppState.errors.modules.message, /module unavailable/);
});

test('empty module list shows load failure with retry instead of a successful empty state', () => {
  assert.match(navigationSource, /AppState\.errors\.modules[\s\S]*?loadModules\(\{force:true\}\)/);
});

test('file copy and batch move force the cached module tree to refresh', async () => {
  const calls = [];
  const elements = new Map([
    ['file-op-type', { value: 'copy' }], ['file-op-module', { value: 'target' }],
    ['file-op-name', { value: 'copy.yaml' }], ['file-op-overwrite', { checked: false }],
    ['batch-move-module', { value: 'target' }], ['batch-move-overwrite', { checked: false }],
  ]);
  const context = vm.createContext({
    document: { getElementById: id => elements.get(id) },
    currentModule: 'old', currentFile: 'original.yaml', selectedFiles: new Set(),
    apiRequest: async path => path === '/file/op' ? { module: 'target', file: 'copy.yaml' } : { results: [{}] },
    loadModules: async options => calls.push(options || {}),
    openFile: async () => true, selectedFileItems: () => [{ module: 'old', file: 'original.yaml' }],
    closeModal() {}, showToast() {},
  });
  loadFunction(context, executionSource, 'submitFileOp');
  loadFunction(context, executionSource, 'submitBatchMove');
  await vm.runInContext('submitFileOp()', context);
  await vm.runInContext('submitBatchMove()', context);
  assert.deepEqual(calls.map(options => options.force), [true, true]);
});

test('partial batch move refreshes modules and keeps only unmoved source files selected', async () => {
  const selectedFiles = new Set(['old::moved.yaml', 'old::retry.yaml']);
  const messages = [];
  const closed = [];
  const count = { textContent: '2' };
  const context = vm.createContext({
    document: { getElementById: id => ({
      'batch-move-module': { value: 'target' },
      'batch-move-overwrite': { checked: false },
      'batch-move-count': count,
    })[id] },
    activeWorkflow: 'yaml_edit', selectedFiles,
    modules: { old: ['moved.yaml', 'retry.yaml'], target: [] },
    selectedFileItems: () => [...selectedFiles].map(key => ({ module: key.split('::')[0], file: key.split('::')[1] })),
    apiRequest: async () => { throw Object.assign(new Error('1 个文件操作失败'), { status: 207 }); },
    loadModules: async options => {
      assert.equal(options.force, true);
      context.modules = { old: ['retry.yaml'], target: ['moved.yaml'] };
    },
    renderModules() {}, closeModal: id => closed.push(id),
    showToast: (message, type) => messages.push({ message, type }),
  });
  loadFunction(context, executionSource, 'submitBatchMove');

  await vm.runInContext('submitBatchMove()', context);

  assert.deepEqual([...selectedFiles], ['old::retry.yaml']);
  assert.match(count.textContent, /1 个 YAML 文件/);
  assert.deepEqual(closed, []);
  assert.match(messages.at(-1).message, /部分.*移动/);
});
