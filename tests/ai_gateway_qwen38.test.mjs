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
