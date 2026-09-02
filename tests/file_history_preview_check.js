// Run: node tests/file_history_preview_check.js
// Real history/editor functions and page markup; all requests stay in memory.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { JSDOM } = require('../api-testing-ui/node_modules/jsdom');

const ROOT = path.resolve(__dirname, '..');
const SAVED = 'android: {}\ntasks:\n  - name: 当前已保存版本\n    flow: []\n';
const DRAFT = SAVED.replace('当前已保存版本', '未保存编辑');
const OLD = SAVED.replace('当前已保存版本', '历史版本');

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

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function fixture(t, { dirty = true, response, confirmResult = false, restoreWait, canEdit = true, fileRead, readStarted } = {}) {
  // Default jsdom does not load external resources or execute page scripts.
  const dom = new JSDOM(fs.readFileSync(path.join(ROOT, 'task-manager.html'), 'utf8'));
  t.after(() => dom.window.close());
  const calls = [], confirmations = [], toasts = [], opened = [], blockedNetwork = [];
  const blockNetwork = () => {
    blockedNetwork.push('network');
    throw new Error('Real network is forbidden');
  };
  dom.window.fetch = dom.window.XMLHttpRequest = dom.window.WebSocket = blockNetwork;
  dom.window.navigator.sendBeacon = blockNetwork;
  const context = vm.createContext({
    window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
    fetch: blockNetwork, XMLHttpRequest: blockNetwork, WebSocket: blockNetwork,
    currentModule: '历史测试', currentFile: '当前.yaml', editorDirty: false, editorInitialContent: '', pendingBatchBusy: false,
    currentAccessProfile: { permissions: canEdit ? ['ui.view', 'ui.edit'] : ['ui.view'], scope: {ui_apps: '*'} },
    activeWorkflow: 'yaml_edit', activeWorkspaceMode: '', sonicStatusData: null,
    isPanelCollapsed: () => false, fileMeta: () => ({}), yamlDisplayName: file => file,
    setActiveWorkflow(value) { context.activeWorkflow = value; }, setFileContextVisible() {}, renderModules() {},
    // Side panels are unrelated to the history/editor state boundary under test.
    handleTab() {}, updateToolbarState() {}, refreshBaselinePreview() {}, refreshSonicPreview() {},
    scheduleBaselinePreviewRefresh() {}, renderYamlTaskNav() {}, renderEditorContextBar() {}, applyEditorPanelState() {},
    showToast: (message, type) => toasts.push({ message, type }),
    confirm: message => { confirmations.push(message); return confirmResult; },
    apiTextRequest: async url => {
      calls.push({ url, method: 'GET' });
      if (readStarted) readStarted.resolve();
      return fileRead ? fileRead() : OLD;
    },
    apiRequest: async (url, options = {}) => {
      const call = { url, method: options.method || 'GET' };
      if (options.body) call.body = JSON.parse(options.body);
      calls.push(call);
      if (url.startsWith('/file/history?')) return { ok: true, versions: [{ id: 'version-1', reason: 'save', size: OLD.length }] };
      if (url.startsWith('/file/version?')) return response ? response(url) : { ok: true, content: OLD };
      if (url === '/file/restore' && call.method === 'POST') {
        if (restoreWait) await restoreWait;
        return { ok: true, version: { id: call.body.version } };
      }
      throw new Error(`Unexpected request: ${call.method} ${url}`);
    },
  });
  loadFunctions(context, 'js/app.js', ['escapeHtml', 'jsArg', 'formatBytes']);
  loadFunctions(context, 'js/utils.js', ['closeModal']);
  loadFunctions(context, 'js/navigation.js', ['hasOpenEditor']);
  loadFunctions(context, 'js/auth.js', ['hasPermission', 'requireUiEditPermission', 'applyRestrictedActionControls', 'canOperateAgent', 'agentAccessReason', 'canAccessGlobalSonic', 'sonicAccessReason', 'canUseSharedUiAi', 'uiAiAccessReason']);
  loadFunctions(context, 'js/execution.js', ['openFile', 'showEditor', 'escHtml', 'updateLines', 'markEditorDirty', 'canLeaveEditor', 'saveFile']);
  const openFile = context.openFile;
  context.openFile = async (module, file) => { opened.push({module, file}); await openFile(module, file); };
  const execution = fs.readFileSync(path.join(ROOT, 'js/execution.js'), 'utf8');
  vm.runInContext(execution.slice(execution.indexOf('function fileHistoryReasonText('), execution.indexOf('async function showBaselineRefs(')), context);
  const run = code => vm.runInContext(code, context);
  const field = id => dom.window.document.getElementById(id);
  context.initialYaml = SAVED;
  run('showEditor(initialYaml)');
  const editor = field('editor');
  editor.value = dirty ? DRAFT : SAVED;
  run('markEditorDirty()');
  editor.setSelectionRange(7, 11);
  const unchanged = () => {
    assert.equal(field('editor'), editor, 'History must not replace the current editor DOM');
    assert.equal(editor.value, dirty ? DRAFT : SAVED);
    assert.equal(run('editorInitialContent'), SAVED);
    assert.equal(run('editorDirty'), dirty);
    assert.equal(editor.selectionStart, 7);
    assert.equal(editor.selectionEnd, 11);
  };
  t.after(() => assert.deepEqual(blockedNetwork, []));
  return { run, field, editor, calls, confirmations, toasts, opened, unchanged };
}

test('preview and closing history preserve unsaved edits and the leave-page warning', async t => {
  const f = fixture(t);
  await f.run('showFileHistory()');
  await f.run('previewFileVersion("version-1")');
  f.unchanged();
  assert.equal(f.field('modal-history').classList.contains('show'), true);
  f.run('closeModal("modal-history")');
  assert.equal(f.run('canLeaveEditor()'), false);
  assert.match(f.confirmations.at(-1), /未保存修改/);
  assert.ok(f.calls.every(call => call.method === 'GET'));
});

test('clean-editor preview is visibly readonly and renders historical content as text', async t => {
  const historical = OLD + '\n# </textarea><img src=x onerror=alert(1)>';
  const f = fixture(t, { dirty: false, response: () => ({ content: historical }) });
  await f.run('showFileHistory()');
  await f.run('previewFileVersion("version-1")');
  f.unchanged();
  const preview = f.field('history-preview-content');
  assert.ok(preview, 'History needs its own preview content area');
  assert.equal(preview.readOnly, true);
  assert.equal(preview.value, historical);
  assert.equal(preview.selectionStart, 0, 'A newly selected version should open at its beginning');
  assert.equal(f.field('history-preview').hidden, false);
  assert.match(f.field('history-preview').textContent, /只读/);
  assert.equal(f.field('modal-history').querySelectorAll('img').length, 0);
  assert.ok(f.calls.every(call => call.method === 'GET'));
});

test('saving an unchanged editor is a visible no-op without creating history', async t => {
  const f = fixture(t, { dirty: false });

  assert.equal(await f.run('saveFile()'), true);
  assert.equal(f.calls.length, 0);
  assert.deepEqual(f.toasts.at(-1), { message: '当前内容没有修改，无需保存', type: 'info' });
});

test('toolbar disables save until the editor content changes', t => {
  const dom = new JSDOM('<button id="btn-save">保存</button><div id="toolbar-state"></div>');
  t.after(() => dom.window.close());
  const context = vm.createContext({
    document: dom.window.document,
    currentModule: '历史测试', currentFile: '当前.yaml', editorDirty: false,
    moduleAppPackage: () => '', appDisplayLabel: value => value,
    escapeHtml: value => String(value), detectSelectedTaskName: () => '',
    latestJobForFile: () => null, jobStatusText: value => value,
  });
  loadFunctions(context, 'js/navigation.js', ['toolbarStateChip', 'updateToolbarState']);

  vm.runInContext('updateToolbarState()', context);
  assert.equal(dom.window.document.getElementById('btn-save').disabled, true);
  assert.match(dom.window.document.getElementById('btn-save').title, /没有修改/);

  vm.runInContext('editorDirty = true; updateToolbarState()', context);
  assert.equal(dom.window.document.getElementById('btn-save').disabled, false);
});

test('an older preview response cannot replace the most recently selected version', async t => {
  const first = deferred(), second = deferred();
  const f = fixture(t, { response: url => url.includes('version=version-1') ? first.promise : second.promise });
  await f.run('showFileHistory()');
  const pendingFirst = f.run('previewFileVersion("version-1")');
  const pendingSecond = f.run('previewFileVersion("version-2")');
  second.resolve({ content: 'version two' });
  await pendingSecond;
  first.resolve({ content: 'version one' });
  await pendingFirst;
  f.unchanged();
  assert.equal(f.field('history-preview-content').value, 'version two');
  assert.match(f.field('history-preview-title').textContent, /version-2/);
});

test('a preview response arriving after the dialog closes does not change the editor', async t => {
  const pending = deferred();
  const f = fixture(t, { response: () => pending.promise });
  await f.run('showFileHistory()');
  const request = f.run('previewFileVersion("version-1")');
  f.run('closeModal("modal-history")');
  pending.resolve({ content: OLD });
  await request;
  f.unchanged();
  assert.equal(f.field('modal-history').classList.contains('show'), false);
});

test('reopening history for another file discards the previous file response', async t => {
  const oldRequest = deferred();
  const f = fixture(t, { response: url => url.includes(encodeURIComponent('当前.yaml')) ? oldRequest.promise : { content: 'another file version' } });
  await f.run('showFileHistory()');
  const pending = f.run('previewFileVersion("version-1")');
  f.run('closeModal("modal-history"); currentFile = "另一个.yaml";');
  await f.run('showFileHistory()');
  await f.run('previewFileVersion("version-1")');
  oldRequest.resolve({ content: OLD });
  await pending;
  f.unchanged();
  assert.equal(f.field('history-preview-content').value, 'another file version');
  assert.match(f.field('history-source').textContent, /另一个.yaml/);
});

test('a failed or missing preview never leaves the previous content under a new version title', async t => {
  const f = fixture(t, { response: url => {
    if (url.includes('version=version-1')) return { content: OLD };
    if (url.includes('version=version-2')) throw new Error('fixture version unavailable');
    return { ok: true };
  } });
  await f.run('showFileHistory()');
  await f.run('previewFileVersion("version-1")');
  await f.run('previewFileVersion("version-2")');
  f.unchanged();
  assert.equal(f.field('history-preview-content').value, '');
  assert.match(f.field('history-preview-status').textContent, /fixture version unavailable/);
  await f.run('previewFileVersion("version-3")');
  assert.equal(f.field('history-preview-content').value, '');
  assert.match(f.field('history-preview-status').textContent, /内容|失败/);
  f.unchanged();
});

test('rollback refuses unsaved content even if its dirty flag has not updated', async t => {
  const f = fixture(t, { confirmResult: true });
  await f.run('showFileHistory()');
  f.run('editorDirty = false');
  await f.run('restoreFileVersion("version-1")');
  assert.equal(f.editor.value, DRAFT);
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 0);
  assert.equal(f.confirmations.length, 0);
  assert.match(f.toasts.at(-1).message, /未保存/);
});

test('cancelled rollback makes no write request', async t => {
  const f = fixture(t, { dirty: false });
  await f.run('showFileHistory()');
  await f.run('restoreFileVersion("version-1")');
  assert.equal(f.confirmations.length, 1);
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 0);
  f.unchanged();
});

test('confirmed clean rollback retains the server restore endpoint and archive explanation', async t => {
  const f = fixture(t, { dirty: false, confirmResult: true });
  await f.run('showFileHistory()');
  await f.run('restoreFileVersion("version-1")');
  assert.equal(f.confirmations.length, 1);
  assert.match(f.confirmations[0], /留档|保存为历史/);
  const writes = f.calls.filter(call => call.method === 'POST');
  assert.deepEqual(writes, [{ url: '/file/restore', method: 'POST', body: { module: '历史测试', file: '当前.yaml', version: 'version-1' } }]);
  assert.ok(f.calls.some(call => call.method === 'GET' && call.url.startsWith('/file?module=')));
  assert.equal(f.field('editor').value, OLD);
  assert.equal(f.field('modal-history').classList.contains('show'), false);
});

test('pending rollback neither duplicates writes nor overwrites newly typed content', async t => {
  const finish = deferred();
  const f = fixture(t, { dirty: false, confirmResult: true, restoreWait: finish.promise });
  await f.run('showFileHistory()');
  const restoring = f.run('restoreFileVersion("version-1")');
  await f.run('restoreFileVersion("version-1")');
  assert.equal(f.field('history-list').querySelector('button').disabled, true);
  f.editor.value = DRAFT;
  f.run('markEditorDirty()');
  finish.resolve();
  await restoring;
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 1);
  assert.deepEqual(f.opened, []);
  assert.equal(f.editor.value, DRAFT);
  assert.equal(f.run('editorDirty'), true);
  assert.match(f.toasts.at(-1).message, /未被替换/);
});

test('a completed rollback does not navigate back or close a newly opened history dialog', async t => {
  const finish = deferred();
  const f = fixture(t, { dirty: false, confirmResult: true, restoreWait: finish.promise });
  await f.run('showFileHistory()');
  const restoring = f.run('restoreFileVersion("version-1")');
  f.run('closeModal("modal-history"); currentFile = "另一个.yaml"');
  await f.run('showFileHistory()');
  finish.resolve();
  await restoring;
  assert.deepEqual(f.opened, []);
  assert.equal(f.field('modal-history').classList.contains('show'), true);
  assert.match(f.field('history-source').textContent, /另一个.yaml/);
  f.unchanged();
});

test('readonly members see a disabled rollback with a reason while preview stays available', async t => {
  const f = fixture(t, {dirty: false, canEdit: false});
  await f.run('showFileHistory()');
  const buttons = f.field('history-list').querySelectorAll('button');
  assert.equal(buttons[0].disabled, false);
  assert.equal(buttons[1].dataset.actionPermission, 'ui.edit');
  assert.equal(buttons[1].disabled, true);
  assert.match(buttons[1].title, /编辑权限/);
  await f.run('previewFileVersion("version-1")');
  assert.equal(f.field('history-preview-content').value, OLD);
  f.unchanged();
});

test('readonly members cannot bypass the rollback button guard by calling its function', async t => {
  const f = fixture(t, {dirty: false, canEdit: false, confirmResult: true});
  await f.run('showFileHistory()');
  await f.run('restoreFileVersion("version-1")');
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 0);
  assert.equal(f.confirmations.length, 0);
  assert.match(f.toasts.at(-1).message, /编辑权限/);
  f.unchanged();
});

test('navigation during the post-rollback file read cannot be overwritten by its late response', async t => {
  const read = deferred(), started = deferred();
  const f = fixture(t, {dirty: false, confirmResult: true, fileRead: () => read.promise, readStarted: started});
  await f.run('showFileHistory()');
  const restoring = f.run('restoreFileVersion("version-1")');
  await started.promise;
  f.run('closeModal("modal-history"); currentFile = "另一个.yaml"; document.getElementById("editor-area").textContent = "新页面";');
  read.resolve(OLD);
  await restoring;
  assert.equal(f.field('editor-area').textContent, '新页面');
  assert.equal(f.run('currentFile'), '另一个.yaml');
});

test('typing during the post-rollback file read remains an unsaved edit after its response', async t => {
  const read = deferred(), started = deferred();
  const f = fixture(t, {dirty: false, confirmResult: true, fileRead: () => read.promise, readStarted: started});
  await f.run('showFileHistory()');
  const restoring = f.run('restoreFileVersion("version-1")');
  await started.promise;
  f.editor.value = DRAFT;
  f.run('markEditorDirty()');
  read.resolve(OLD);
  await restoring;
  assert.equal(f.field('editor'), f.editor);
  assert.equal(f.field('editor').value, DRAFT);
  assert.equal(f.run('editorDirty'), true);
});

test('a failed post-rollback read preserves the editor and reports that the server restore succeeded', async t => {
  const f = fixture(t, {dirty: false, confirmResult: true, fileRead: () => { throw new Error('fixture reload unavailable'); }});
  await f.run('showFileHistory()');
  await f.run('restoreFileVersion("version-1")');
  f.unchanged();
  assert.match(f.toasts.at(-1).message, /已回滚.*读取/);
  assert.match(f.toasts.at(-1).message, /fixture reload unavailable/);
});
