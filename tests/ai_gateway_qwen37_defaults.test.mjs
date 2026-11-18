import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const root = new URL('../', import.meta.url);
const server = fs.readFileSync(new URL('ai-gateway/server.js', root), 'utf8');
const context = vm.createContext({ URL, requestedCompletionTokens: () => 1000, imagePartsFromBody: () => [], buildUserMessage: () => '' });
vm.runInContext(server.slice(server.indexOf('function routeSupportsQwenHybridThinking('), server.indexOf('async function appendAiLog(')), context);
const official = { model: 'qwen3.7-plus', action: 'api_case_generation', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' };

test('Gateway default Qwen provider rolls back Max to 3.7 Plus without changing action routing', () => {
  const providers = JSON.parse(fs.readFileSync(new URL('ai-gateway/config/providers.json', root), 'utf8'));
  const routes = JSON.parse(fs.readFileSync(new URL('ai-gateway/config/model-router.json', root), 'utf8'));
  assert.equal(providers.providers.qwen_plus.model, 'qwen3.7-plus');
  assert.equal(providers.providers.qwen_plus.name, '千问 Qwen 3.7 Plus');
  for (const entry of Object.values(routes)) assert.equal(entry.providerId, 'qwen_plus');
});
test('Max video stages use 3.7 Plus while existing Flash stages retain their model and budgets', () => {
  for (const [stage, expected] of Object.entries({ timeline: 'qwen3.7-plus', grounding: 'qwen3.7-plus', perception: 'qwen3.7-plus', audit: 'qwen3.7-plus', consolidate: 'qwen3.7-plus', outcome: 'qwen3.7-plus', inputs: 'qwen3.7-plus', overview: 'qwen3.8-flash', semantic: 'qwen3.8-flash' })) {
    context.stage = stage;
    assert.equal(vm.runInContext('videoStageConfig(stage)[3]', context), expected, stage);
  }
});
test('3.7 Plus supports explicit API thinking and existing 3.8 compatibility remains available', () => {
  for (const model of ['qwen3.7-plus', 'qwen3.8-max']) {
    for (const region of ['dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com', 'dashscope-us.aliyuncs.com']) {
      context.route = { ...official, model, baseUrl: `https://${region}/compatible-mode/v1` };
      assert.equal(vm.runInContext('completionOptionsForRoute(route,"test",{thinkingMode:"on"}).enable_thinking', context), true);
      assert.equal(vm.runInContext('completionOptionsForRoute(route,"test",{thinkingMode:"off"}).enable_thinking', context), false);
    }
  }
});
test('3.7 Plus JSON and bounded trajectory retain intended thinking scope', () => {
  context.route = { ...official, action: 'generate_case' };
  assert.equal(vm.runInContext('completionOptionsForRoute(route,"test",{jsonResponse:true}).enable_thinking', context), false);
  context.body = { jsonResponse: true, skillName: 'ui_video_trajectory', payload: { phase: 'whole-trajectory', reasoning_mode: 'bounded' } };
  const options = vm.runInContext('completionOptionsForRoute(route,"test",body)', context);
  assert.equal(options.enable_thinking, true); assert.equal(options.thinking_budget, 1024);
});
