const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const { JSDOM } = require('../api-testing-ui/node_modules/jsdom');

function load(win, file, name) {
  const source = fs.readFileSync(file, 'utf8');
  const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
  assert.notEqual(start, -1, name);
  const rest = source.slice(start);
  const next = rest.slice(1).search(/^(?:async )?function [\w$]+\(/m);
  win.eval(next < 0 ? rest : rest.slice(0, next + 1));
}

test('planning failure offers diagnosis and an explicit new retry, never a phantom approval or cancellation', async t => {
  const dom = new JSDOM('<body></body>', { runScripts: 'dangerously' });
  t.after(() => dom.window.close());
  const win = dom.window;
  const actions = [];
  Object.assign(win, { setAgentTab: tab => actions.push(['view', tab]),
    retryAgentRunById: id => actions.push(['retry', id]),
    canOperateAgent: () => true,
  });
  for (const name of ['escapeHtml', 'jsArg']) load(win, 'js/app.js', name);
  load(win, 'js/agent-status.js', 'agentFailureActionsHtml');
  win.document.body.innerHTML = win.agentFailureActionsHtml({runId: 'audit-1', status: 'FAILED', artifacts: {}});
  const buttons = [...win.document.querySelectorAll('button')];
  assert.ok(win.document.body.textContent.includes('旧记录'));
  assert.ok(!win.document.body.textContent.includes('应用修复并重跑'));
  assert.ok(!win.document.body.textContent.includes('生成缺陷草稿'));
  assert.ok(!win.document.body.textContent.includes('人工处理'));
  for (const button of buttons) button.click();
  assert.deepEqual(actions, [['view', 'failure'], ['retry', 'audit-1']]);
});

test('stale failed-run confirmation buttons cannot send a confirmation request', async t => {
  const dom = new JSDOM('<body></body>', { runScripts: 'dangerously' });
  t.after(() => dom.window.close());
  const win = dom.window, requests = [], messages = [];
  Object.assign(win, { canOperateAgent: () => true,
    currentAgentRun: () => ({runId:'audit-1', status:'FAILED', pendingConfirmations:[]}),
    apiRequest: async (...args) => { requests.push(args); return {}; },
    normalizeAgentRun: value => value, mergeAgentRun() {}, renderAgentPageAfterRunUpdate() {},
    showToast: message => messages.push(message),
  });
  load(win, 'js/agent-status.js', 'confirmAgentRun');
  await win.confirmAgentRun('APPLY_REPAIR_AND_RERUN');
  assert.deepEqual(requests, []);
  assert.match(messages.join(' '), /待确认项/);
});
