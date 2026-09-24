const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const {JSDOM} = require('../api-testing-ui/node_modules/jsdom');

function loadFunction(win, file, name) {
  const source = fs.readFileSync(file, 'utf8');
  const start = source.search(new RegExp(`^async function ${name}\\(`, 'm'));
  assert.notEqual(start, -1, name);
  const rest = source.slice(start);
  const next = rest.slice(1).search(/^async function [\w$]+\(/m);
  win.eval(next < 0 ? rest : rest.slice(0, next + 1));
}

test('job cancellation displays the server execution stop notice', async t => {
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const messages = [];
  Object.assign(dom.window, {
    confirm: () => true,
    postJobAction: async () => ({execution_stop_notice: '平台已标记取消；正在执行的 Runner 动作可能继续至结束。'}),
    showToast: message => messages.push(message),
  });
  loadFunction(dom.window, 'js/app.js', 'cancelJob');
  await dom.window.cancelJob('job-running');
  assert.match(messages.join(' '), /可能继续至结束/);
});

test('Agent cancellation displays the server execution stop notice', async t => {
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const messages = [];
  Object.assign(dom.window, {
    confirm: () => true,
    agentCurrentRun: null,
    AppState: {},
    apiRequest: async () => ({run: {runId: 'agent-running', status: 'CANCELLED'}, execution_stop_notice: '平台已停止 Agent 后续步骤；正在执行的 Runner 动作可能继续至结束。'}),
    normalizeAgentRun: run => run,
    mergeAgentRun() {},
    renderAgentPageAfterRunUpdate() {},
    showToast: message => messages.push(message),
  });
  loadFunction(dom.window, 'js/agent-status.js', 'cancelAgentRunById');
  await dom.window.cancelAgentRunById('agent-running');
  assert.match(messages.join(' '), /可能继续至结束/);
});
