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

function fixture(t, {loaded = false, loadRenderer = true} = {}) {
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
  if (loadRenderer) {
    for (const name of ['providerStatusText', 'providerDisplayText', 'modelProviderOptions', 'currentStrategyName', 'providerLabelById', 'renderModelConfigCenter']) {
      loadFunction(context, source, name);
    }
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

function interactiveFixture(t) {
  const f=fixture(t,{loaded:true,loadRenderer:false});
  f.context.document.body.insertAdjacentHTML('beforeend','<div id="toolbar-path"></div><div id="toolbar-help"></div><div id="file-info"></div>');
  Object.assign(f.context,{activeWorkflow:'config',activeWorkspaceMode:'',resetYamlToolbarForManager(){},
    setActiveWorkflow(value){f.context.activeWorkflow=value;},showToast(){},confirm:()=>true,alert(){}});
  vm.runInContext(fs.readFileSync('js/model-config.js','utf8'),f.context);
  return f;
}

test('late model configuration response cannot replace another workflow', async t=>{
  const f=interactiveFixture(t); let release;
  f.context.loadAiModelConfig=()=>new Promise(resolve=>release=resolve);
  const pending=vm.runInContext('showModelConfigCenter()',f.context);
  f.context.activeWorkflow='assets';
  f.context.document.getElementById('editor-area').textContent='asset page';
  release(); await pending;
  assert.equal(f.text(),'asset page');
});

test('failed recommended strategy keeps server state and can be saved again',async t=>{
  const f=interactiveFixture(t); let calls=0;
  f.context.aiModelRouter={generate_case:'old',analyze_failure:'old'};
  f.context.aiProviders.push({id:'old',name:'old',configured:true});
  f.context.aiGatewayPost=async()=>{calls++;throw new Error('network failure');};
  vm.runInContext('renderModelConfigCenter()',f.context);
  await vm.runInContext('applyRecommendedStrategy()',f.context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.context.aiModelRouter.generate_case,'old');
  assert.equal(f.context.document.querySelector('[data-model-router="generate_case"]').value,'qwen_plus');
  await vm.runInContext('saveModelRouterConfig()',f.context);
  assert.equal(calls,2);
});

test('saving keeps edits made after request started and blocks a second write',async t=>{
  const f=interactiveFixture(t); let release; let calls=0; const toasts=[];
  f.context.aiProviders.push({id:'old',name:'old',configured:true},{id:'later',name:'later',configured:true});
  f.context.aiModelRouter={generate_case:'old',analyze_failure:'old'};
  f.context.showToast=(message,type)=>toasts.push({message,type});
  f.context.aiGatewayPost=()=>{calls++;return new Promise(resolve=>release=resolve);};
  vm.runInContext('renderModelConfigCenter()',f.context);
  const select=f.context.document.querySelector('[data-model-router="generate_case"]');
  select.value='qwen_plus';
  const saving=vm.runInContext('saveModelRouterConfig()',f.context);
  select.value='later';
  const second=vm.runInContext('saveModelRouterConfig()',f.context);
  assert.equal(calls,1);
  release({router:{generate_case:'qwen_plus',analyze_failure:'old'}});
  await Promise.all([saving,second]);
  assert.equal(f.context.document.querySelector('[data-model-router="generate_case"]').value,'later');
  assert.equal(f.context.aiModelRouter.generate_case,'qwen_plus');
  assert.ok(toasts.some(item=>/未保存/.test(item.message)));
});

test('recommended strategy save keeps a later manual choice as an unsaved draft',async t=>{
  const f=interactiveFixture(t); let release; let calls=0;
  f.context.aiProviders.push({id:'later',name:'later',configured:true});
  f.context.aiGatewayPost=()=>{calls++;return new Promise(resolve=>release=resolve);};
  vm.runInContext('renderModelConfigCenter()',f.context);
  const saving=vm.runInContext('applyRecommendedStrategy()',f.context);
  f.context.document.querySelector('[data-model-router="generate_case"]').value='later';
  await vm.runInContext('saveModelRouterConfig()',f.context);
  assert.equal(calls,1);
  release({router:{generate_case:'qwen_plus',analyze_failure:'qwen_plus'}});
  await saving;
  assert.equal(f.context.document.querySelector('[data-model-router="generate_case"]').value,'later');
});
