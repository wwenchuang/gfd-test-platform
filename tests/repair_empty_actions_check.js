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

test('every unavailable repair action leaves a readable next step inside its panel', t => {
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
    button.click();
    const feedback = win.document.getElementById('repair-action-feedback');
    assert.ok(feedback && !feedback.hidden, `${label} must leave visible feedback even after the toast disappears`);
    assert.ok(feedback.textContent.includes(message), label);
  }
});
