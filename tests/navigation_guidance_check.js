const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM } = require('../api-testing-ui/node_modules/jsdom');

function fixture(t) {
  const dom = new JSDOM(`<div class="toolbar-context"><div id="toolbar-help"></div><details id="workflow-navigation-help" hidden><summary>此页怎么用</summary><p id="workflow-navigation-help-text"></p></details></div><main id="editor-area"></main>`, { runScripts: 'dangerously' });
  t.after(() => dom.window.close());
  const w = dom.window;
  w.activeWorkflow = 'config';
  w.updateWorkflowActionGroups = () => {};
  w.eval(fs.readFileSync('js/navigation.js', 'utf8'));
  return w;
}

test('advanced guidance remains available when page rendering replaces toolbar help and content', t => {
  const w = fixture(t);
  w.renderWorkflowNav();
  const help = w.document.getElementById('workflow-navigation-help');
  const text = w.document.getElementById('workflow-navigation-help-text').textContent;
  assert.equal(help.hidden, false);
  assert.match(text, /模型/);
  assert.match(text, /管理员/);
  help.open = true;
  w.document.getElementById('toolbar-help').textContent = '正在加载';
  w.document.getElementById('editor-area').innerHTML = '<h2>加载后的模型配置</h2>';
  assert.equal(w.document.getElementById('workflow-navigation-help-text').textContent, text);
  w.activeWorkflow = 'bug_drafts';
  w.renderWorkflowNav();
  assert.equal(help.open, false);
  assert.match(w.document.getElementById('workflow-navigation-help-text').textContent, /确认.*提交/);
  assert.doesNotMatch(w.document.getElementById('workflow-navigation-help-text').textContent, /模型/);
});

test('simple workflow and unknown routes remove advanced guidance, including previously expanded text', t => {
  const w = fixture(t);
  const help = w.document.getElementById('workflow-navigation-help');
  for (const key of ['ui_start', 'ui_library', 'ui_reports', 'unknown']) {
    w.activeWorkflow = 'config';
    w.renderWorkflowNav();
    help.open = true;
    w.activeWorkflow = key;
    w.renderWorkflowNav();
    assert.equal(help.hidden, true);
    assert.equal(help.open, false);
    assert.equal(w.document.getElementById('workflow-navigation-help-text').textContent, '');
  }
});

test('refreshing the same route preserves expanded guidance without requests or timers', t => {
  const w = fixture(t);
  w.fetch = () => assert.fail('guidance must not fetch');
  w.setTimeout = () => assert.fail('guidance must not schedule timers');
  w.renderWorkflowNav();
  const help = w.document.getElementById('workflow-navigation-help');
  help.open = true;
  w.renderWorkflowNav();
  assert.equal(help.open, true);
});
