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
  for(const name of ['agentApplicationPackage','agentApplicationByPackage','agentBusinessLines','agentBusinessOptionsHtml','agentAppsWithDefault','appendAgentAppOptions','renderAgentBusinessOptions','loadAppList']) load(win,name);
  const pending = win.loadAppList('甲','home');
  win.document.getElementById('agent-app-name').value='乙';
  win.document.getElementById('agent-business').value='shared';
  respond({apps:[{name:'甲',package:'app.a',business_lines:[{id:'home',name:'家用'}]}, {name:'乙',package:'app.b',business_lines:[{id:'shared',name:'共享'}]}]});
  await pending;
  assert.equal(win.document.getElementById('agent-app-name').value,'乙');
  assert.equal(win.document.getElementById('agent-business').value,'shared');
});
