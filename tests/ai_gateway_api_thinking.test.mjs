import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../ai-gateway/server.js',import.meta.url),'utf8');
const part=(from,to)=>source.slice(source.indexOf(from),source.indexOf(to,source.indexOf(from)));
const official={action:'api_case_generation',model:'qwen3.8-max',baseUrl:'https://dashscope.aliyuncs.com/compatible-mode/v1',providerId:'official'};
const correlation='01234567-89ab-4cde-8fab-0123456789ab';

function completion(body={},route=official){
 const c=vm.createContext({body,route,URL,buildUserMessage:()=>'',imagePartsFromBody:()=>[],requestedCompletionTokens:()=>1000});
 vm.runInContext(part('function routeSupportsQwenHybridThinking(', 'async function appendAiLog('),c);
 return vm.runInContext('completionOptionsForRoute(route,"test",body)',c);
}
function runtime(routes=[official],failure=false,timeoutMs=5000){
 const logs=[];let calls=0;
 class Timeout extends Error{}
 const c=vm.createContext({URL,console,buildUserMessage:()=>'',imagePartsFromBody:()=>[],requestedCompletionTokens:()=>1000,OpenAI:{APIConnectionTimeoutError:Timeout},uuidv4:()=>correlation,
   validateVideoStage:()=>null, routeCandidatesForCall:async()=>{if(routes instanceof Error)throw routes;return routes;},requestedCallTimeoutMs:()=>timeoutMs,
   AI_CALL_FALLBACK_RESERVE_MS:15000,MOCK_ENABLED:false,
   completionOptionsForRoute:()=>({}), clientForRoute:()=>({chat:{completions:{create:async()=>{
    calls++;if(failure==='timeout')throw new Timeout('secret provider timeout');if(failure)throw new Error('secret provider message');
    return {choices:[{message:{content:'secret answer'},finish_reason:'stop'}],usage:{}};
   }}}}),completionUsageSummary:()=>({}),assistantOutputText:x=>x,
   sanitizeError:e=>e.message,preview:x=>JSON.stringify(x),isFallbackEligibleAiError:()=>false,appendAiLog:async x=>logs.push(x)});
 vm.runInContext(part('function routeSupportsQwenHybridThinking(', 'async function appendAiLog('),c);
 vm.runInContext(part('async function callAi(', 'function mockAiOutput('),c);
 return {call:(body)=>c.callAi('api_case_generation',body,{promptOverride:'',messages:body.messages}),logs,get calls(){return calls;}};
}
function handler(){
 let route;let passed;
 const c=vm.createContext({app:{post:(_path,fn)=>route=fn},asyncRoute:fn=>fn,
  callAi:async (_action,body)=>{passed=body;return {output:'answer',route:{providerId:'p',model:'m',fallbackUsed:true,fallbackReason:'secret failure'}};},
  completionAuditResponse:()=>({})});
 vm.runInContext(part("function isApiCorrelationId(", "// Stage capabilities"),c);
 vm.runInContext(part("app.post('/ai/api-case-generation'", "app.post('/ai/generate-bug'"),c);
 return {invoke:async body=>{let status=200,data;const res={status:n=>(status=n,res),json:x=>(data=x,res)};await route({body},res);return {status,data,passed};}};
}

test('API explicit thinking is request-scoped and default unchanged',()=>{
 assert.equal(completion({}).enable_thinking,undefined);
 assert.equal(completion({thinkingMode:'on'}).enable_thinking,true);
 assert.equal(completion({thinkingMode:'off'}).enable_thinking,false);
 assert.equal(completion({thinkingMode:'on'},{...official,action:'generate_case'}).enable_thinking,undefined);
});
test('unsupported and spoofed routes are refused before any provider call',async()=>{
 for(const route of [ {...official,baseUrl:'https://dashscope.aliyuncs.com.evil.test/v1'}, {...official,baseUrl:'https://dashscope.aliyuncs.com@evil.test/v1'}, {...official,baseUrl:'https://evil@dashscope.aliyuncs.com/v1'}, {...official,baseUrl:'http://dashscope.aliyuncs.com/v1'}, {...official,model:'qwen-plus'} ]){
  const r=runtime([official,route]);await assert.rejects(r.call({messages:[{role:'user',content:'secret prompt'}],thinkingMode:'off',correlationId:correlation}));assert.equal(r.calls,0);assert.equal(r.logs[0].correlationId,correlation);
 }
});
test('regional official routes support the explicit mode',()=>{
 for (const host of ['dashscope.aliyuncs.com','dashscope-intl.aliyuncs.com','dashscope-us.aliyuncs.com']) {
  assert.equal(completion({thinkingMode:'off'},{...official,baseUrl:`https://${host}/compatible-mode/v1`}).enable_thinking,false);
 }
});
test('API logs correlate success and failure without body, output or provider errors',async()=>{
 for(const fail of [false,true]){
  const r=runtime([official],fail), body={messages:[{role:'user',content:'secret prompt'}],thinkingMode:'off',correlationId:correlation};
  if(fail) await assert.rejects(r.call(body));else await r.call(body);
  assert.equal(r.logs.length,1);assert.equal(r.logs[0].correlationId,correlation);
  const line=JSON.stringify(r.logs);for(const secret of ['secret prompt','secret answer','secret provider message'])assert.equal(line.includes(secret),false);
 }
});
test('route resolution failure retains correlation without error details',async()=>{
 const r=runtime(new Error('secret route resolution'));
 await assert.rejects(r.call({messages:[{role:'user',content:'secret prompt'}],correlationId:correlation,thinkingMode:'off'}));
 assert.equal(r.calls,0);assert.equal(r.logs[0].correlationId,correlation);assert.equal(r.logs[0].error,'api_route_unavailable');
 assert.equal(JSON.stringify(r.logs).includes('secret'),false);
});
test('provider and total timeouts retain correlation with fixed timeout category',async()=>{
 for(const r of [runtime([official],'timeout'),runtime([official],false,0)]) {
  await assert.rejects(r.call({messages:[{role:'user',content:'secret prompt'}],correlationId:correlation,thinkingMode:'off'}));
  assert.equal(r.logs[0].correlationId,correlation);assert.equal(r.logs[0].error,'provider_timeout');
  assert.equal(JSON.stringify(r.logs).includes('secret'),false);
 }
});
test('API handler validates UUID and mode, passes valid values and suppresses fallback error',async()=>{
 const h=handler();
 for(const extra of [{correlationId:'bad'},{thinkingMode:'arbitrary'},{thinkingMode:true}]){
  const bad=await h.invoke({messages:[{role:'user',content:'x'}],...extra});assert.equal(bad.status,400);
 }
 const ok=await h.invoke({messages:[{role:'user',content:'x'}],thinkingMode:'off',correlationId:correlation});
 assert.equal(ok.passed.thinkingMode,'off');assert.equal(ok.passed.correlationId,correlation);assert.equal(ok.data.thinkingModeSent,'off');
 assert.equal(JSON.stringify(ok.data).includes('secret failure'),false);
});

test('API log omits untrusted model and provider metadata',async()=>{
 const r=runtime([{...official,model:'secret-business-model',providerId:'secret-token',providerName:'secret-name'}]);
 await r.call({messages:[{role:'user',content:'hello'}],correlationId:correlation});
 const line=JSON.stringify(r.logs);
 for(const value of ['secret-business-model','secret-token','secret-name']) assert.equal(line.includes(value),false);
});

test('API handler returns safe unsupported-route code without provider details',async()=>{
 let route;
 const c=vm.createContext({app:{post:(_path,fn)=>route=fn},asyncRoute:fn=>fn,
  callAi:async()=>{const e=new Error('secret provider details');e.code='thinking_route_unsupported';throw e;},completionAuditResponse:()=>({})});
 vm.runInContext(part('function isApiCorrelationId(', '// Stage capabilities'),c);
 vm.runInContext(part("app.post('/ai/api-case-generation'", "app.post('/ai/generate-bug'"),c);
 let status=200,data;const res={status:n=>(status=n,res),json:x=>(data=x,res)};
 await route({body:{messages:[{role:'user',content:'x'}],thinkingMode:'off'}},res);
 assert.equal(status,400);assert.equal(data.error_code,'thinking_route_unsupported');assert.equal(JSON.stringify(data).includes('secret'),false);
});
