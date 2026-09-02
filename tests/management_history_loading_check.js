const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const { JSDOM } = require('../api-testing-ui/node_modules/jsdom');

function loadFunction(window, source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1);
  const end = source.indexOf('\nfunction ', start + 1);
  window.eval(source.slice(start, end < 0 ? undefined : end));
}

test('report and failure-analysis entry refresh the complete management history', async t => {
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  const calls = [];
  Object.assign(win, {
    activeWorkflow: 'reports',
    AppState: {polling: {jobs: null}},
    ensureJobsLoaded: options => {
      calls.push(options || {});
      return Promise.resolve();
    },
    stopJobsAutoRefresh: () => {},
    showReportsCenter: () => {},
  });
  loadFunction(win, fs.readFileSync('js/agent-status.js', 'utf8'), 'applyLazyLoadForSection');

  win.applyLazyLoadForSection('reports');
  await Promise.resolve();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].force, true);

  calls.length = 0;
  win.activeWorkflow = 'failure_analysis';
  win.showAiRepairCenter = () => {};
  win.applyLazyLoadForSection('failure_analysis');
  await Promise.resolve();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].force, true);
});

test('ordinary execution pages keep their existing cached-load behavior', async t => {
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  const calls = [];
  Object.assign(win, {
    activeWorkflow: 'execute',
    AppState: {polling: {jobs: 1}},
    ensureJobsLoaded: options => {
      calls.push(options || {});
      return Promise.resolve();
    },
    ensureModulesLoaded: () => Promise.resolve(),
    ensureRunnersLoaded: () => Promise.resolve(),
    maybeAdjustAgentPolling: () => {},
    hasOpenEditor: () => true,
  });
  loadFunction(win, fs.readFileSync('js/agent-status.js', 'utf8'), 'applyLazyLoadForSection');

  win.applyLazyLoadForSection('execute');
  await Promise.resolve();
  assert.equal(calls.length, 2);
  assert.equal(calls[0].force, undefined);
  assert.equal(calls[1].force, undefined);
});
