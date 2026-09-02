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

test('a stale YAML link stays on the current page instead of opening a fake saved template', async () => {
  const source = fs.readFileSync('js/execution.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  const win = dom.window;
  win.canLeaveEditor = () => true;
  win.activeWorkspaceMode = 'agent';
  win.currentModule = '当前模块';
  win.currentFile = '当前文件.yaml';
  win.sonicStatusData = {loaded: true};
  win.activeWorkflow = 'agent';
  win.apiTextRequest = async () => {
    throw new Error('请求的资源不存在');
  };
  win.showToast = (message, type) => {
    win.lastToast = {message, type};
  };
  win.showEditor = content => {
    win.openedEditorContent = content;
  };
  win.setActiveWorkflow = () => {};
  win.setFileContextVisible = () => {};
  win.fileMeta = () => ({});
  win.renderModules = () => {};
  win.updateToolbarState = () => {};
  win.yamlDisplayName = value => value;
  for (const id of [
    'toolbar-path', 'toolbar-help', 'btn-save', 'btn-copy-file', 'btn-move-file',
    'btn-rename-file', 'btn-history-file', 'btn-baseline-refs', 'btn-generation-review',
    'btn-sonic-status', 'btn-publish-sonic', 'file-status-select', 'btn-run-file',
    'btn-run-task', 'btn-repair-task', 'btn-repair-file', 'toggle-refs-panel',
    'toggle-case-panel', 'file-info',
  ]) {
    const element = win.document.createElement(id === 'file-status-select' ? 'select' : 'div');
    element.id = id;
    win.document.body.appendChild(element);
  }
  loadFunction(win, source, 'openFile');

  const opened = await win.openFile('AI_Agent_草稿', '已清理.yaml');

  assert.equal(opened, false);
  assert.equal(win.currentModule, '当前模块');
  assert.equal(win.currentFile, '当前文件.yaml');
  assert.equal(win.openedEditorContent, undefined);
  assert.deepEqual(win.lastToast, {
    message: '无法打开 AI_Agent_草稿/已清理.yaml：请求的资源不存在。文件可能已被清理，请从原运行记录查看历史结果。',
    type: 'error',
  });
  dom.window.close();
});
