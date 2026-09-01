// Run with: node tests/model_config_loading_state_check.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const { JSDOM } = require('../api-testing-ui/node_modules/jsdom');

function loadFunction(context, source, name) {
  const start = source.search(new RegExp(`^function ${name}\\(`, 'm'));
  assert.notEqual(start, -1, `missing ${name}`);
  const rest = source.slice(start);
  const next = rest.slice(1).search(/^function [A-Za-z_$][\w$]*\(/m);
  vm.runInContext(next === -1 ? rest : rest.slice(0, next + 1), context);
}

function fixture(t, {loaded = false} = {}) {
  const dom = new JSDOM('<body><div id="editor-area"></div></body>');
  t.after(() => dom.window.close());
  const context = vm.createContext({
    document: dom.window.document,
    AppState: {loaded: {modelConfig: loaded}},
    MODEL_ROUTER_FIELDS: [
      ['generate_case', '生成测试用例模型'],
      ['analyze_failure', '失败分析模型'],
    ],
    aiProviders: loaded ? [{id: 'qwen_plus', name: '千问 Qwen Plus', model: 'qwen-plus', configured: true}] : [],
    aiModelRouter: loaded ? {generate_case: 'qwen_plus', analyze_failure: 'qwen_plus'} : {},
    escapeHtml: value => String(value ?? ''),
  });
  const source = fs.readFileSync('js/model-config.js', 'utf8');
  for (const name of ['providerStatusText', 'providerDisplayText', 'modelProviderOptions', 'currentStrategyName', 'providerLabelById', 'renderModelConfigCenter']) {
    loadFunction(context, source, name);
  }
  return {context, text: () => dom.window.document.getElementById('editor-area').textContent};
}

test('model configuration loading state does not publish a false empty strategy', t => {
  const f = fixture(t);
  vm.runInContext('renderModelConfigCenter(true)', f.context);
  assert.match(f.text(), /正在加载模型策略/);
  assert.doesNotMatch(f.text(), /尚未配置/);
  assert.doesNotMatch(f.text(), /qwen_plus/);
  const buttons = f.context.document.querySelectorAll('.model-strategy-actions button');
  assert.ok(Array.from(buttons).every(button => button.disabled));
});

test('model configuration load failure offers retry without showing defaults as saved data', t => {
  const f = fixture(t);
  vm.runInContext("renderModelConfigCenter(false, '模型服务暂不可用')", f.context);
  assert.match(f.text(), /模型配置加载失败/);
  assert.match(f.text(), /重新加载/);
  assert.doesNotMatch(f.text(), /尚未配置/);
  assert.doesNotMatch(f.text(), /qwen_plus/);
});
