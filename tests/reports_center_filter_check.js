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

test('failed and timed-out reports without an analysis remain discoverable as unclassified', t => {
  const source = fs.readFileSync('js/reports.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {
    reportFilters: {query: '', status: 'failed', failureType: 'UNKNOWN', page: 1},
    latestJobs: [
      {job_id: 'failed-1', status: 'failed'},
      {job_id: 'timeout-1', status: 'timeout'},
      {job_id: 'success-1', status: 'success'},
      {job_id: 'script-1', status: 'failed', failure_type: 'SCRIPT_ISSUE'},
    ],
  });
  loadFunction(win, source, 'reportsFailureType');
  loadFunction(win, source, 'reportCenterStatusKey');
  loadFunction(win, source, 'filterReportsForCenter');

  assert.equal(win.reportsFailureType(win.latestJobs[0]), 'UNKNOWN');
  assert.equal(win.reportsFailureType(win.latestJobs[1]), 'UNKNOWN');
  assert.equal(win.reportsFailureType(win.latestJobs[2]), '');
  assert.deepEqual(Array.from(win.filterReportsForCenter(), job => job.job_id), ['failed-1', 'timeout-1']);
  assert.match(source, /<span class="report-overview-label">未通过<\/span>/);
  assert.match(source, />未通过（失败\/超时\/取消）<\/option>/);
  assert.match(source, />待确认\/未归因<\/option>/);
  assert.match(source, /!AppState\.loaded\.jobs.*AppState\.loading\.jobs/);
  assert.match(source, /正在读取历史执行报告/);
  assert.match(source, /完成后可搜索和筛选/);
});

test('report cleanup rejects unsafe policy values and keeps a direct return path', t => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  const dom = new JSDOM(`
    <body>
      <input id="report-clean-days" value="-1">
      <input id="report-clean-keep" value="200">
    </body>
  `, {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  const messages = [];
  win.showToast = (message, type) => messages.push([message, type]);
  loadFunction(win, source, 'reportCleanupPolicyInput');

  assert.equal(win.reportCleanupPolicyInput(), null);
  assert.match(messages.at(-1)[0], /保留天数.*大于等于 1.*整数/);
  win.document.getElementById('report-clean-days').value = '14';
  win.document.getElementById('report-clean-keep').value = '-2';
  assert.equal(win.reportCleanupPolicyInput(), null);
  assert.match(messages.at(-1)[0], /至少保留.*大于等于 0.*整数/);
  win.document.getElementById('report-clean-keep').value = '';
  assert.equal(win.reportCleanupPolicyInput(), null);
  assert.match(messages.at(-1)[0], /至少保留.*大于等于 0.*整数/);
  win.document.getElementById('report-clean-keep').value = '200';
  assert.equal(JSON.stringify(win.reportCleanupPolicyInput()), JSON.stringify({days: 14, minKeep: 200}));
  assert.match(source, /setActiveWorkflow\('reports'\)/);
  assert.match(source, /onclick="showReportsCenter\(\)">返回执行报告<\/button>/);
  assert.match(source, /清理预览完成，候选/);
});
