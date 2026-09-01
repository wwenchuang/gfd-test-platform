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
