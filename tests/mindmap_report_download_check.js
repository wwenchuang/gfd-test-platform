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

test('generated report actions never navigate directly to a protected download URL', () => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  const createReport = source.slice(
    source.indexOf('async function createMindmapTestReport('),
    source.indexOf('async function uploadMindmapReportTemplate('),
  );

  assert.doesNotMatch(createReport, /<a[^>]+href=.*test-reports|<a[^>]+href="\$\{escapeHtml\(data\.download/);
  assert.match(createReport, /downloadMindmapTestReport\(this\)/);
  assert.match(createReport, /data-report-download=/);
});

test('report download converts the server URL to an authenticated API path and restores the button', async t => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  const dom = new JSDOM('<body><button id="download" data-report-download="/api/test-reports/download?report_id=tpr_1&amp;format=doc" data-report-filename="回归测试报告.doc">下载 Word</button></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  const calls = [];
  win.downloadAuthenticatedFile = async (path, filename) => {
    calls.push({path, filename});
    return '服务端报告.doc';
  };
  win.showToast = (message, type) => { win.lastToast = {message, type}; };
  loadFunction(win, source, 'mindmapReportDownloadPath');
  loadFunction(win, source, 'downloadMindmapTestReport');
  const button = win.document.getElementById('download');

  assert.equal(await win.downloadMindmapTestReport(button), true);
  assert.deepEqual(calls, [{
    path: '/test-reports/download?report_id=tpr_1&format=doc',
    filename: '回归测试报告.doc',
  }]);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, '下载 Word');
  assert.deepEqual(win.lastToast, {message: '✓ 已下载：服务端报告.doc', type: 'success'});
});

test('report download leaves a visible Chinese error instead of opening a JSON error tab', async t => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  const dom = new JSDOM('<body><button id="download" data-report-download="/api/test-reports/download?report_id=missing&amp;format=md">下载 Markdown</button></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  win.downloadAuthenticatedFile = async () => { throw new Error('测试报告文件不存在，请重新生成'); };
  win.showToast = (message, type) => { win.lastToast = {message, type}; };
  loadFunction(win, source, 'mindmapReportDownloadPath');
  loadFunction(win, source, 'downloadMindmapTestReport');
  const button = win.document.getElementById('download');

  assert.equal(await win.downloadMindmapTestReport(button), false);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, '下载 Markdown');
  assert.deepEqual(win.lastToast, {message: '测试报告文件不存在，请重新生成', type: 'error'});
});
