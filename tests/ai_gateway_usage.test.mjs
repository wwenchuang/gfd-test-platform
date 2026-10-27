import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../ai-gateway/server.js',import.meta.url),'utf8');
const code=source.slice(source.indexOf('function completionUsageSummary('),source.indexOf('function emptyAssistantOutputError('));
const context=vm.createContext({});vm.runInContext(code,context);
const evaluate=expression=>JSON.parse(JSON.stringify(vm.runInContext(expression,context)));

test('provider reasoning usage survives normalization and response projection',()=>{
 context.raw={prompt_tokens:100,completion_tokens:1100,total_tokens:1200,
              completion_tokens_details:{reasoning_tokens:1024},reasoning_content:'PRIVATE'};
 const one=evaluate('completionUsageSummary(raw)');
 assert.equal(one.reasoningTokens,1024);
 context.summary=one;
 assert.deepEqual(evaluate('completionUsageSummary(summary)'),one);
 assert.deepEqual(evaluate('completionAuditResponse({finishReason:"stop",usage:summary})'),{finishReason:'stop',usage:one});
 assert.ok(!JSON.stringify(one).includes('PRIVATE'));
});
test('explicit nested zero has precedence and existing missing defaults remain',()=>{
 context.usage={completion_tokens_details:{reasoning_tokens:0},reasoningTokens:1024};
 assert.equal(evaluate('completionUsageSummary(usage)').reasoningTokens,0);
 assert.equal(evaluate('completionUsageSummary({})').reasoningTokens,0);
});
