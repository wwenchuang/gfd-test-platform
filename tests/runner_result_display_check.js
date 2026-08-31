const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync('js/app.js', 'utf8');
const ctx = vm.createContext({
  escapeHtml: value => String(value), explainCallbackHttp000: value => value,
  jobTimelineHtml: () => '', jobTimingText: () => '', jobExecutionLabel: () => '单条调试',
  jobDeviceLabel: () => '测试设备', jobRunnerLabel: () => '测试Runner',
});
for (const name of ['extractJobRawError', 'jobErrorText', 'jobDetailHtml']) {
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf('\nfunction ', start + 1);
  vm.runInContext(source.slice(start, end < 0 ? undefined : end), ctx);
}
function detail(job) {
  ctx.job = job;
  return vm.runInContext('jobDetailHtml(job, jobErrorText(job), "")', ctx);
}
test('successful Runner output stays visible without being presented as a failure', () => {
  const job = {status: 'success', stdout_tail: 'Report: fixture.html\nAll files executed successfully!'};
  const html = detail(job);
  assert.doesNotMatch(html, /原始错误|失败原因/);
  assert.match(html, /All files executed successfully/);
});
test('failed assertions keep their actual failure explanation', () => {
  const html = detail({status: 'failed', stderr_tail: 'Assertion failed: 首页标题不存在'});
  assert.match(html, /失败原因/);
  assert.match(html, /首页标题不存在/);
});
test('success with upload warnings preserves the warning without changing execution outcome', () => {
  const html = detail({status: 'success', upload_warning: '报告上传稍后重试', stdout_tail: 'All files executed successfully!'});
  assert.doesNotMatch(html, /失败原因/);
  assert.match(html, /回传告警.*报告上传稍后重试/);
});
