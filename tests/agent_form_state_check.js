const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const { JSDOM } = require('../api-testing-ui/node_modules/jsdom');

function load(win, name) {
  const source = fs.readFileSync('js/agent-workbench.js', 'utf8');
  const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
  assert.notEqual(start, -1, name);
  const rest = source.slice(start);
  const next = rest.slice(1).search(/^(?:async )?function [\w$]+\(/m);
  win.eval(next < 0 ? rest : rest.slice(0, next + 1));
}

test('Agent input survives navigation without conflating a new draft with old run data', t => {
  const dom = new JSDOM('<textarea id="agent-goal"></textarea><select id="agent-business"><option value="home">家用</option></select>', {runScripts:'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {agentFormDraft:{}, _AGENT_FORM_FIELD_IDS:['agent-goal','agent-business']});
  load(win, 'captureAgentFormDraft');
  win.document.getElementById('agent-goal').value = '当前未提交的目标';
  win.captureAgentFormDraft();
  win.document.body.innerHTML = '<h2>待我确认</h2>';
  win.captureAgentFormDraft();
  assert.equal(win.agentFormDraft['agent-goal'].value, '当前未提交的目标');
  assert.equal(win.agentFormDraft['agent-business'].value, 'home');
});

test('an application response cannot overwrite the user selection made while loading', async t => {
  const dom = new JSDOM('<select id="agent-app-name"><option value="甲" data-package="app.a">甲</option><option value="乙" data-package="app.b">乙</option></select><select id="agent-business"><option value="home">家用</option><option value="shared">共享</option></select>', {runScripts:'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  let respond;
  Object.assign(win, {agentApplicationCatalog:[], agentAppListRequestSeq:0, agentBusinessDraft:'home',
    apiRequest: () => new Promise(resolve => { respond=resolve; }),
    escapeHtml: value => value,
    selectedAgentAppPackage: () => win.document.getElementById('agent-app-name')?.selectedOptions[0]?.dataset.package || '',
  });
  for(const name of ['agentApplicationPackage','agentApplicationByPackage','agentBusinessLines','agentBusinessOptionsHtml','agentAppsWithDefault','preferredAgentApplication','appendAgentAppOptions','renderAgentBusinessOptions','loadAppList']) load(win,name);
  const pending = win.loadAppList('甲','home');
  win.document.getElementById('agent-app-name').value='乙';
  win.document.getElementById('agent-business').value='shared';
  respond({apps:[{name:'甲',package:'app.a',business_lines:[{id:'home',name:'家用'}]}, {name:'乙',package:'app.b',business_lines:[{id:'shared',name:'共享'}]}]});
  await pending;
  assert.equal(win.document.getElementById('agent-app-name').value,'乙');
  assert.equal(win.document.getElementById('agent-business').value,'shared');
});

test('Agent defaults to the first enabled application that has an active business line', t => {
  const dom = new JSDOM('<select id="agent-app-name"></select><select id="agent-business"></select><div id="agent-business-hint"></div>', {runScripts:'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {
    agentApplicationCatalog: [
      {name:'小白学习', package:'app.study', business_lines:[]},
      {name:'智小白3D', package:'com.kfb.model', business_lines:[{id:'home', name:'家用', enabled:true}]},
    ],
    agentBusinessDraft:'',
    escapeHtml: value => value,
    selectedAgentAppPackage: () => win.document.getElementById('agent-app-name')?.selectedOptions[0]?.dataset.package || '',
  });
  for (const name of ['agentApplicationPackage','agentApplicationByPackage','agentBusinessLines','agentBusinessOptionsHtml','agentAppsWithDefault','preferredAgentApplication','appendAgentAppOptions','renderAgentBusinessOptions']) load(win,name);
  win.appendAgentAppOptions(win.document.getElementById('agent-app-name'), win.agentApplicationCatalog, '');
  win.renderAgentBusinessOptions('');
  assert.equal(win.document.getElementById('agent-app-name').value, '智小白3D');
  assert.equal(win.document.getElementById('agent-business').disabled, false);
  assert.match(win.document.getElementById('agent-business-hint').textContent, /请选择本次测试所属业务/);
});

test('Agent explains how to fix an application with no active business line', t => {
  const dom = new JSDOM('<select id="agent-app-name"><option value="小白学习" data-package="app.study">小白学习</option></select><select id="agent-business"></select><div id="agent-business-hint"></div>', {runScripts:'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {
    agentApplicationCatalog:[{name:'小白学习', package:'app.study', business_lines:[]}],
    agentBusinessDraft:'', escapeHtml: value => value,
    selectedAgentAppPackage: () => 'app.study',
  });
  for (const name of ['agentApplicationPackage','agentApplicationByPackage','agentBusinessLines','agentBusinessOptionsHtml','renderAgentBusinessOptions']) load(win,name);
  win.renderAgentBusinessOptions('');
  assert.equal(win.document.getElementById('agent-business').disabled, true);
  assert.match(win.document.getElementById('agent-business-hint').textContent, /应用配置.*业务/);
});
