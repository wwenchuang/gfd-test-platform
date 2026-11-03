import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import fs from 'node:fs';
const source=fs.readFileSync(new URL('../ai-gateway/server.js',import.meta.url),'utf8');
const ctx=vm.createContext({Date:{now:()=>1000}});
vm.runInContext(source.slice(source.indexOf('function requestedCallTimeoutMs('),source.indexOf('function requestedCompletionTokens(')),ctx);
test('effective timeout has closed stage cap and respects shorter clocks',()=>{
 for(const [stage,cap] of [['grounding',180000],['audit',180000],['perception',120000],['semantic',120000],['timeline',120000]]) {
  ctx.body={videoStage:stage,timeoutMs:999999,videoDeadlineMs:999999};
  assert.equal(vm.runInContext('requestedCallTimeoutMs(body)',ctx),cap);
  ctx.body.timeoutMs=5000;assert.equal(vm.runInContext('requestedCallTimeoutMs(body)',ctx),5000);
  ctx.body.videoDeadlineMs=2000;assert.equal(vm.runInContext('requestedCallTimeoutMs(body)',ctx),1000);
 }
});
