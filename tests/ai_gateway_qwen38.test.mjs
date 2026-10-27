import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../ai-gateway/server.js',import.meta.url),'utf8');
const begin=source.indexOf('function routeSupportsQwenHybridThinking(');
const end=source.indexOf('async function appendAiLog(',begin);
const context=vm.createContext({buildUserMessage:()=>'',imagePartsFromBody:()=>[],requestedCompletionTokens:()=>1000});
vm.runInContext(source.slice(begin,end),context);
for(const model of ['qwen3.7-plus','qwen3.8-max']){
 test(`${model} structured output disables thinking`,()=>{
  context.route={model,baseUrl:'https://dashscope.aliyuncs.com/compatible-mode/v1'};
  assert.equal(vm.runInContext('completionOptionsForRoute(route,"test",{jsonResponse:true}).enable_thinking',context),false);
 });
}
test('does not alter other providers or non-JSON requests',()=>{
 context.route={model:'qwen3.8-max',baseUrl:'https://example.com/v1'};
 assert.equal(vm.runInContext('completionOptionsForRoute(route,"test",{jsonResponse:true}).enable_thinking',context),undefined);
 context.route.baseUrl='https://dashscope.aliyuncs.com/compatible-mode/v1';
 assert.equal(vm.runInContext('completionOptionsForRoute(route,"test",{}).enable_thinking',context),undefined);
});
for (const model of ['qwen3.8-max','qwen3.8-flash']) {
 test(`${model} exact bounded trajectory thinking`,()=>{
  context.route={model,baseUrl:'https://dashscope.aliyuncs.com/compatible-mode/v1'};
  context.body={jsonResponse:true,skillName:'ui_video_trajectory',payload:{phase:'whole-trajectory',reasoning_mode:'bounded',thinking_budget:99999}};
  const result=vm.runInContext('completionOptionsForRoute(route,"test",body)',context);
  assert.equal(result.enable_thinking,true);assert.equal(result.thinking_budget,1024);
 });
}
test('bounded reasoning scope cannot broaden other requests or routes',()=>{
 for (const change of [{skillName:'ui_video_observe'}, {payload:{phase:'whole-observe',reasoning_mode:'bounded'}},
  {payload:{phase:'whole-trajectory',reasoning_mode:'unbounded'}},{payload:{phase:'whole-trajectory'}},{jsonResponse:false}]) {
  context.route={model:'qwen3.8-max',baseUrl:'https://dashscope.aliyuncs.com/compatible-mode/v1'};
  context.body={jsonResponse:true,skillName:'ui_video_trajectory',payload:{phase:'whole-trajectory',reasoning_mode:'bounded'},...change};
  const result=vm.runInContext('completionOptionsForRoute(route,"test",body)',context);
  assert.notEqual(result.enable_thinking,true);assert.equal(result.thinking_budget,undefined);
 }
 for (const route of [{model:'qwen3.7-plus',baseUrl:'https://dashscope.aliyuncs.com/compatible-mode/v1'},
  {model:'qwen3.8-max',baseUrl:'https://example.com/v1'},
  {model:'qwen3.8-max',baseUrl:'https://dashscope.aliyuncs.com.evil.example/v1'},
  {model:'qwen3.8-unknown',baseUrl:'https://dashscope.aliyuncs.com/compatible-mode/v1'}]) {
  context.route=route;context.body={jsonResponse:true,skillName:'ui_video_trajectory',payload:{phase:'whole-trajectory',reasoning_mode:'bounded'}};
  const result=vm.runInContext('completionOptionsForRoute(route,"test",body)',context);
  assert.notEqual(result.enable_thinking,true);assert.equal(result.thinking_budget,undefined);
 }
});
