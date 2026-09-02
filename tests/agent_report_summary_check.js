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

test('Agent report summary counts logical cases instead of repair attempts', () => {
  const source = fs.readFileSync('js/agent-workbench.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  const win = dom.window;
  win.agentRunResultMeta = () => ({
    hasReportResult: true,
    total: 8,
    passed: 5,
    failed: 3,
    timeout: 0,
    running: 0,
    rawAttempted: 11,
    repairAttempted: 3,
  });
  loadFunction(win, source, 'agentReportSummaryMetrics');

  const jobs = Array.from({length: 11}, (_, index) => ({id: index}));
  const metrics = win.agentReportSummaryMetrics(
    {summary: {execution: {logicalAttemptCount: 8}}},
    jobs,
    {success: jobs.slice(0, 5), failed: jobs.slice(5), active: [], unknown: []},
    jobs.slice(5),
  );

  assert.deepEqual(
    {total: metrics.total, passed: metrics.passed, failed: metrics.failed, pending: metrics.pending},
    {total: 8, passed: 5, failed: 3, pending: 0},
  );
  assert.match(metrics.attemptNote, /原始执行 11 次/);
  assert.match(metrics.attemptNote, /修复重跑 3 次/);
  assert.match(metrics.attemptNote, /8 条逻辑用例/);
  dom.window.close();
});

