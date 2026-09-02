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

function makeWindow() {
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  const win = dom.window;
  win.escapeHtml = value => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
  win.jsArg = value => JSON.stringify(String(value ?? ''));
  return {dom, win};
}

test('terminal Agent runs use ending labels and readable structured failures', () => {
  const source = fs.readFileSync('js/agent-status.js', 'utf8');
  const {dom, win} = makeWindow();
  for (const name of [
    'agentRunStatus',
    'agentRunIsTerminal',
    'agentRunStepFieldLabel',
    'agentFailureDisplayText',
  ]) loadFunction(win, source, name);

  assert.equal(win.agentRunStepFieldLabel({status: 'FAILED'}), '结束步骤');
  assert.equal(win.agentRunStepFieldLabel({status: 'RUNNING'}), '当前步骤');
  assert.equal(win.agentFailureDisplayText({
    failureType: 'SCRIPT_ISSUE',
    conclusion: '照片打印导航步骤顺序错误',
  }), '照片打印导航步骤顺序错误');
  assert.doesNotMatch(source, /String\(failure \|\| '暂无'\)/);
  dom.window.close();
});

test('phase failures describe the failed outcome instead of a running stage', () => {
  const source = fs.readFileSync('js/agent-workbench.js', 'utf8');
  const {dom, win} = makeWindow();
  win.agentStepLabel = value => ({VALIDATE_YAML: '校验 YAML'}[String(value)] || String(value));
  loadFunction(win, source, 'agentPhaseFailureSummary');
  assert.equal(win.agentPhaseFailureSummary('RUN_SONIC'), 'Runner 执行未通过');
  assert.equal(win.agentPhaseFailureSummary('RERUN'), '安全重跑未恢复');
  assert.equal(win.agentPhaseFailureSummary('VALIDATE_YAML'), '校验 YAML 未通过');
  dom.window.close();
});

test('generated execution actions count only executable YAML', () => {
  const source = fs.readFileSync('js/agent-workbench.js', 'utf8');
  const {dom, win} = makeWindow();
  win.agentGeneratedCaseGroups = () => ({
    executable_cases: [
      {name: '冒烟', smoke: true},
      {name: '第二条'},
      {name: '第三条'},
    ],
    needs_review_cases: [],
    draft_cases: [],
    manual_cases: Array.from({length: 9}, (_, index) => ({name: `人工 ${index + 1}`})),
  });
  win.agentMindmapInfo = () => ({caseSetId: 'agent-demo'});
  win.agentGeneratedSmokeRerunLimit = (_artifacts, total) => Math.min(3, total || 3);
  win.agentGeneratedCaseIsSmoke = item => item.smoke === true;
  loadFunction(win, source, 'renderGeneratedExecutionLevelSummary');

  const html = win.renderGeneratedExecutionLevelSummary({});
  assert.match(html, /继续下一批可执行 2\/2/);
  assert.match(html, /执行全部可执行 3/);
  assert.doesNotMatch(html, /执行全部当前可执行/);
  assert.doesNotMatch(html, /all_executable'\)\">执行全部可执行 12/);
  dom.window.close();
});

test('Runner report labels attempts separately and suppresses duplicate legacy failures', () => {
  const source = fs.readFileSync('js/agent-workbench.js', 'utf8');
  const {dom, win} = makeWindow();
  loadFunction(win, source, 'agentReportFailureSectionTitle');
  assert.equal(
    win.agentReportFailureSectionTitle({failed: 1, attemptNote: '重复尝试不重复计数。'}),
    '失败执行记录（1 条逻辑用例）',
  );
  assert.match(source, /if \(!reportJobs\.length && failedJobs\.length > 0\)/);
  dom.window.close();
});
