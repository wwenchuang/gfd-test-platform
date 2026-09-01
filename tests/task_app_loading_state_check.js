// Run with: node --test tests/task_app_loading_state_check.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const {JSDOM} = require('../api-testing-ui/node_modules/jsdom');

function loadFunction(context, source, name) {
  const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
  assert.notEqual(start, -1, `${name} must exist`);
  const rest = source.slice(start);
  const next = rest.slice(1).search(/^(?:async )?function [A-Za-z_$][\w$]*\(/m);
  vm.runInContext(next < 0 ? rest : rest.slice(0, next + 1), context);
}

function fixture(t) {
  const dom = new JSDOM(`<!doctype html><body>
    <select id="generate-application"></select>
    <button id="btn-generate-yaml"></button>
    <button id="generate-app-config-action"></button>
    <input id="generate-app-package-detail">
    <input id="generate-app-package">
    <span class="generate-application-name"></span>
    <div id="modal-generate"></div>
  </body>`, {runScripts: 'outside-only'});
  t.after(() => dom.window.close());
  const source = fs.readFileSync('js/app.js', 'utf8');
  const context = vm.createContext({
    window: dom.window,
    document: dom.window.document,
    AppState: {loaded: {modules: false, taskApps: false}, loading: {}, errors: {}},
    taskApps: [],
    generateBusy: false,
    escapeHtml: value => String(value ?? ''),
  });
  for (const name of ['taskAppCatalogLoading', 'taskAppCatalogError', 'enabledGenerateApplications', 'selectedGenerateApplication', 'updateGenerateSubmitState', 'renderGenerateApplicationOptions']) {
    loadFunction(context, source, name);
  }
  return {dom, context, run: code => vm.runInContext(code, context)};
}

test('generation distinguishes an application catalog still loading from a real empty catalog', t => {
  const f = fixture(t);
  f.run("renderGenerateApplicationOptions('')");
  assert.equal(f.dom.window.document.querySelector('.generate-application-name').textContent, '正在加载应用配置...');
  assert.equal(f.dom.window.document.getElementById('generate-application').disabled, true);
  assert.equal(f.dom.window.document.getElementById('generate-app-config-action').hidden, true);

  f.run('AppState.loaded.taskApps = true; renderGenerateApplicationOptions(\'\')');
  assert.equal(f.dom.window.document.querySelector('.generate-application-name').textContent, '暂无已启用应用');
  assert.equal(f.dom.window.document.getElementById('generate-app-config-action').hidden, false);
});

test('generation rerenders configured applications after the shared catalog finishes loading', async t => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  assert.match(source, /Promise\.all\(\[ensureModulesLoaded\(\),\s*loadKnowledgeApps\(\)\]\)/);
  assert.match(source, /renderGenerateApplicationOptions\(moduleAppPackage\(currentModule\)\)/);
});
