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

test('cancelled Agent runs stay separate from partial business results', () => {
  const source = fs.readFileSync('js/agent-status.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  const win = dom.window;
  for (const name of [
    'agentStatusText',
    'agentRunStatus',
    'agentRunIsTerminal',
    'agentRunResultSource',
    'agentRunResultMeta',
    'agentRunHasBlockingFailureWithoutResult',
    'agentRunPanelTitle',
    'agentRunProgressColor',
    'agentRunPillClass',
    'agentRunCardStatusClass',
    'agentRunDisplayStatusText',
    'agentHistoryStatusKey',
  ]) loadFunction(win, source, name);

  const cancelled = {
    status: 'CANCELLED',
    reportSummary: {attempted: 8, passed: 5, failed: 3, outcome: 'partial', label: '部分通过'},
  };
  const completedPartial = {
    status: 'DONE',
    reportSummary: {attempted: 8, passed: 5, failed: 3, outcome: 'partial', label: '部分通过'},
  };
  const staleDoneAfterFailedPlan = {
    status: 'DONE',
    currentStep: 'DONE',
    error: '没有可转换为自动化 YAML 的用例',
    steps: [
      {step: 'PREPARE_SOURCE', status: 'SUCCESS'},
      {step: 'PLAN', status: 'FAILED', error: '没有可转换为自动化 YAML 的用例'},
      {step: 'GENERATE_YAML', status: 'SKIPPED'},
    ],
    artifacts: {},
  };

  assert.equal(win.agentHistoryStatusKey(cancelled), 'cancelled');
  assert.equal(win.agentRunDisplayStatusText(cancelled), '已取消');
  assert.equal(win.agentRunPillClass(cancelled), 'warn');
  assert.equal(win.agentRunCardStatusClass(cancelled), 'cancelled');
  assert.equal(win.agentRunProgressColor(cancelled), 'var(--danger)');

  assert.equal(win.agentHistoryStatusKey(completedPartial), 'partial');
  assert.equal(win.agentRunDisplayStatusText(completedPartial), '部分通过');
  assert.equal(win.agentRunPillClass(completedPartial), 'partial');
  assert.equal(win.agentRunCardStatusClass(completedPartial), 'partial');
  assert.equal(win.agentRunProgressColor(completedPartial), 'var(--warn)');

  assert.equal(win.agentRunHasBlockingFailureWithoutResult(staleDoneAfterFailedPlan), true);
  assert.equal(win.agentHistoryStatusKey(staleDoneAfterFailedPlan), 'failed');
  assert.equal(win.agentRunDisplayStatusText(staleDoneAfterFailedPlan), '失败');
  assert.equal(win.agentRunPanelTitle(staleDoneAfterFailedPlan), '最近失败');
  assert.equal(win.agentRunPillClass(staleDoneAfterFailedPlan), 'failed');
  assert.equal(win.agentRunCardStatusClass(staleDoneAfterFailedPlan), 'failed');
  assert.equal(win.agentRunProgressColor(staleDoneAfterFailedPlan), 'var(--danger)');
  assert.match(source, /<option value="cancelled"[^>]*>已取消<\/option>/);
  assert.match(source, /count\.textContent = run \? `\$\{agentModeText\(run\.mode\)\} · \$\{agentRunDisplayStatusText\(run\)/);
  assert.match(source, /<strong>状态<\/strong><span[^>]*>\$\{escapeHtml\(agentRunDisplayStatusText\(run\)\)\}<\/span>/);
  assert.match(source, /agentYamlDisplayRows\(artifacts\)/);
  dom.window.close();
});
