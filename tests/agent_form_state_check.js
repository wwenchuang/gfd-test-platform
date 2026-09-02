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
  for(const name of ['agentApplicationPackage','agentApplicationByPackage','agentBusinessLines','agentBusinessOptionsHtml','agentAppsWithDefault','preferredAgentApplication','appendAgentAppOptions','renderAgentBusinessOptions','agentModuleNames','renderAgentModuleOptions','toggleFailedJobField','loadAppList']) load(win,name);
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

test('specified-module scope exposes a real application-scoped module picker', t => {
  const dom = new JSDOM(`
    <select id="agent-app-name"><option selected data-modules='["家用基线","共享基线"]'>智小白3D</option></select>
    <select id="agent-scope"><option value="auto">自动</option><option value="module" selected>指定模块</option></select>
    <div id="agent-module-field" hidden><select id="agent-module"></select></div>
    <div id="agent-failed-job-field"></div>
  `, {runScripts:'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {modules: {'家用基线': [], '共享基线': [], '内部历史': []}, escapeHtml: value => value});
  for (const name of ['agentModuleNames','renderAgentModuleOptions','toggleFailedJobField']) load(win, name);

  win.renderAgentModuleOptions('共享基线');
  win.toggleFailedJobField();

  const field = win.document.getElementById('agent-module-field');
  const select = win.document.getElementById('agent-module');
  assert.equal(field.hidden, false);
  assert.deepEqual(Array.from(select.options, option => option.value), ['', '家用基线', '共享基线']);
  assert.equal(select.value, '共享基线');

  win.document.getElementById('agent-scope').value = 'auto';
  win.toggleFailedJobField();
  assert.equal(field.hidden, true);
});

test('analysis-only mode visibly removes execution controls and changes the primary action', t => {
  const dom = new JSDOM(`
    <select id="agent-mode-select"><option value="AUTO_SAFE">安全自动</option><option value="ANALYZE_ONLY" selected>只分析</option></select>
    <input type="radio" name="agent-mode" value="AUTO_SAFE" checked>
    <input type="radio" name="agent-mode" value="ANALYZE_ONLY">
    <div id="agent-runner-field"></div>
    <div id="agent-install-strip"></div>
    <strong id="agent-config-title">执行配置</strong>
    <em id="agent-config-description"></em>
    <button id="agent-start-btn">启动 Agent</button>
  `, {runScripts:'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  load(win, 'syncAgentModeRadios');

  win.syncAgentModeRadios();

  assert.equal(win.document.querySelector('input[value="ANALYZE_ONLY"]').checked, true);
  assert.equal(win.document.getElementById('agent-runner-field').hidden, true);
  assert.equal(win.document.getElementById('agent-install-strip').hidden, true);
  assert.equal(win.document.getElementById('agent-config-title').textContent, '分析配置');
  assert.match(win.document.getElementById('agent-config-description').textContent, /不会生成 YAML.*Runner/);
  assert.equal(win.document.getElementById('agent-start-btn').textContent, '开始分析');
});

test('analysis-only payload disables every YAML and Runner mutation policy', t => {
  const dom = new JSDOM(`
    <textarea id="agent-goal">分析家用打印入口</textarea>
    <select id="agent-app-name"><option selected>智小白3D</option></select>
    <select id="agent-business"><option value="home" selected>家用</option></select>
    <select id="agent-platform"><option value="android" selected>Android</option></select>
    <select id="agent-scope"><option value="auto" selected>自动</option></select>
    <input type="radio" name="agent-mode" value="ANALYZE_ONLY" checked>
    <input type="checkbox" id="agent-policy-runSonic" checked>
    <input type="checkbox" id="agent-policy-autoRepair" checked>
    <input type="checkbox" id="agent-policy-bugDraft" checked>
    <input type="checkbox" id="agent-policy-generateCase" checked>
    <input type="checkbox" id="agent-policy-validateYaml" checked>
    <input type="checkbox" id="agent-policy-safeRerun" checked>
  `, {runScripts:'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {
    DEFAULT_AGENT_APP_NAME: '智小白3D', rememberAgentBusiness: () => {},
    selectedAgentModelInfo: () => ({kind:'default', model:'', providerId:''}),
    collectAgentSourceMaterials: () => ({requirementText:'', figmaUrl:'', files:[], images:[]}),
    agentRiskHits: () => [], collectAgentSourceRefs: () => ({sourceType:'manual', sourceRefs:{}}),
    selectedRunnerDevice: () => ({runner_id:'runner-1', device_id:'device-1', device_strategy:'fixed'}),
    selectedAgentAppPackage: () => 'com.kfb.model',
  });
  load(win, 'agentPayloadFromForm');

  const payload = win.agentPayloadFromForm();

  assert.equal(payload.mode, 'ANALYZE_ONLY');
  assert.equal(payload.autoRun, false);
  assert.equal(payload.autoRepair, false);
  assert.equal(payload.autoCreateBug, false);
  assert.equal(payload.strategy.generateYaml, false);
  assert.equal(payload.strategy.validateYaml, false);
  assert.equal(payload.strategy.runSonic, false);
  assert.equal(payload.strategy.safeRerun, false);
  assert.equal(payload.strategy.bugDraftOnly, false);
});
