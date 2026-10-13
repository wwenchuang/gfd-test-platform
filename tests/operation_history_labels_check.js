const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../js/operation-history.js'), 'utf8');
const declarations = source.slice(source.indexOf('  const domains'), source.indexOf('  const results'));
const label = source.slice(source.indexOf('  function actionLabel'), source.indexOf('  function resultLabel'));
const context = vm.createContext({});
vm.runInContext(declarations + label + ';globalThis.label = actionLabel;', context);
test('UI batch operations show explicit Chinese business labels', () => {
  const verbs = { create:'创建', preflight:'运行前检查', view:'查看', list:'列表', cancel:'取消', rerun:'重跑', confirm_stop:'确认停止', report_generate:'生成报告', report_view:'查看报告', report_download:'下载报告', summary:'查看报告摘要', artifact_download:'下载附件', notification_status:'查看通知状态', notification_retry:'补发通知' };
  for (const [verb, expected] of Object.entries(verbs)) assert.equal(context.label('ui_batch.'+verb), 'UI 测试批次 · '+expected);
  assert.equal(context.label('job.dispatch_prepared'), '执行任务 · 下发内容已准备');
});
test('served page changes the operation history script cache version', () => {
  const html = fs.readFileSync(require('node:path').join(__dirname, '../task-manager.html'), 'utf8');
  assert.match(html, /operation-history\.js\?v=20261013-ui-batch-audit-v1/);
});
