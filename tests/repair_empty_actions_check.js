// Real repair panel HTML and inline click handlers, with no network or file writes.
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

test('unavailable repair actions are visibly disabled and the panel keeps a readable next step', t => {
  const dom = new JSDOM('<body></body>', { runScripts: 'dangerously' });
  t.after(() => dom.window.close());
  const win = dom.window;
  const app = fs.readFileSync('js/app.js', 'utf8');
  const repair = fs.readFileSync('js/ai-repair.js', 'utf8');
  for (const name of ['escapeHtml', 'jsArg']) loadFunction(win, app, name);
  Object.assign(win, {
    aiFailureDraft: null, repairDraftRiskHits: () => [], stringifyArtifact: JSON.stringify,
    currentRepairDraft: () => null, repairFailureTypeText: () => '待人工复核',
    aiRepairTabText: () => '', showToast: () => {},
  });
  for (const name of ['promptRepairUnavailable', 'repairYamlDraftHtml']) loadFunction(win, repair, name);
  win.document.body.innerHTML = win.repairYamlDraftHtml({ canAutoRepair: false, failureType: 'UNKNOWN' });
  const messages = {
    '生成修复草稿': '不能自动修 YAML',
    '复制草稿': '暂无修复草稿可复制',
    '下载草稿': '暂无修复草稿可下载',
    '人工确认替换': '需要先生成并保存修复草稿',
    '拒绝草稿': '暂无可拒绝的修复草稿',
  };
  for (const [label, message] of Object.entries(messages)) {
    const button = [...win.document.querySelectorAll('button')].find(item => item.textContent === label);
    assert.ok(button, label);
    assert.equal(button.disabled, true, `${label} must look unavailable before the user tries it`);
    assert.ok(button.title.includes(message), `${label} must explain its prerequisite`);
  }
  for (const label of ['原始 YAML', '修复 YAML', 'Diff / 校验']) {
    const tab = [...win.document.querySelectorAll('button')].find(item => item.textContent === label);
    assert.ok(tab, label);
    assert.equal(tab.disabled, true, `${label} must not look interactive before a failure analysis exists`);
    assert.match(tab.title, /选择失败任务.*AI 分析/);
  }
  const feedback = win.document.getElementById('repair-action-feedback');
  assert.ok(feedback && !feedback.hidden, 'The next step must stay visible without requiring a click');
  assert.match(feedback.textContent, /待人工复核.*不能自动修 YAML/);
});

test('a missing selected failure job does not silently highlight an unrelated first row', t => {
  const dom = new JSDOM('<body></body>', { runScripts: 'dangerously' });
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {selectedRepairJobId: 'old-job', aiFailureDraft: null});
  loadFunction(win, fs.readFileSync('js/ai-repair.js', 'utf8'), 'resolveAiRepairSelectedJob');
  const jobs = [{job_id:'new-job'}];
  assert.equal(win.resolveAiRepairSelectedJob(jobs), null);
  win.selectedRepairJobId = '';
  assert.equal(win.resolveAiRepairSelectedJob(jobs), jobs[0]);
});

test('a repairable script subtype keeps its broad script classification', t => {
  const dom = new JSDOM('<body></body>', { runScripts: 'dangerously' });
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {
    stringifyArtifact: value => typeof value === 'string' ? value : JSON.stringify(value),
    failureTypeText: type => type,
  });
  const source = fs.readFileSync('js/ai-repair.js', 'utf8');
  for (const name of ['extractByLabel', 'normalizeFailureAnalysis']) loadFunction(win, source, name);
  const normalized = win.normalizeFailureAnalysis({
    category: 'script_issue',
    failure_type: 'scroll_not_effective',
    can_auto_repair: true,
    reason: '横向滑动没有生效',
  });
  assert.equal(normalized.failureType, 'SCRIPT_ISSUE');
  assert.equal(normalized.canAutoRepair, true);
});

test('failure search distinguishes no match from no history and offers a clear action', t => {
  const dom = new JSDOM('<body></body>', { runScripts: 'dangerously' });
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {
    latestJobs: [
      {job_id: 'failed-1', status: 'failed', file: '模型生成记录.yaml'},
      {job_id: 'timeout-1', status: 'timeout', file: '姓名牌打印.yaml'},
      {job_id: 'passed-1', status: 'success', file: '通过.yaml'},
    ],
    repairJobFilters: {query: '不存在', page: 2},
    repairJobFilterTimer: null,
    activeWorkflow: 'repair',
    showAiRepairCenter: () => { win.renderCount = (win.renderCount || 0) + 1; },
  });
  const source = fs.readFileSync('js/ai-repair.js', 'utf8');
  for (const name of ['aiRepairFailedJobs', 'repairJobCountText', 'repairJobEmptyStateHtml', 'clearRepairJobSearch']) {
    loadFunction(win, source, name);
  }

  const allFailures = win.aiRepairFailedJobs({ignoreQuery: true});
  const matchedFailures = win.aiRepairFailedJobs();
  assert.equal(allFailures.length, 2);
  assert.equal(matchedFailures.length, 0);
  assert.equal(win.repairJobCountText(allFailures.length, matchedFailures.length, 0, true), '匹配 0/2 条');

  win.document.body.innerHTML = win.repairJobEmptyStateHtml(true, allFailures.length);
  assert.match(win.document.body.textContent, /共加载 2 条失败记录/);
  const clear = win.document.querySelector('button');
  assert.equal(clear.textContent, '清除搜索');
  clear.click();
  assert.equal(win.repairJobFilters.query, '');
  assert.equal(win.repairJobFilters.page, 1);
  assert.equal(win.renderCount, 1);
});

test('environment failure action opens the environment health workflow', () => {
  const source = fs.readFileSync('js/ai-repair.js', 'utf8');
  assert.match(
    source,
    /ENV_ISSUE:\s*\{[^\n]+label:\s*'查看环境建议'[^\n]+showPreflightDashboard\(\)/,
  );
});
