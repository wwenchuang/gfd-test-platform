import {test} from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js';
import {fileURLToPath} from 'node:url';
import {runAcceptance, DEFAULT_REQUEST} from './acceptance.mjs';

const peer = fileURLToPath(new URL('./test-peer.mjs', import.meta.url));
const server = fileURLToPath(new URL('./server.mjs', import.meta.url));
const runId = '72fbaa24-3e2d-4c16-9b04-5e77e283538c';

async function session(t, variant="normal") {
  const client = new Client({name:'contract-test', version:'1'});
  const transport = new StdioClientTransport({
    command: process.execPath, args: ['--import', peer, server],
    env: {PLATFORM_SESSION_TOKEN: 'test-only-authorized-session', FIXTURE_VARIANT:variant}, stderr:'pipe',
  });
  t.after(() => client.close());
  await client.connect(transport);
  return client;
}

test('SDK session discovers only closed tool schemas and rejects invalid fields before HTTP', async t => {
  const client = await session(t);
  const {tools} = await client.listTools();
  assert.deepEqual(tools.map(tool => tool.name).sort(), ['get_run','list_suites','run_suite']);
  for (const tool of tools) assert.equal(tool.inputSchema.additionalProperties, false);
  const begin = {suite_id:'published-catalog',environment:'platform',request_id:randomUUID()};
  for (const [name,args] of [
    ['run_suite',{...begin, environment:'production'}],
    ['run_suite',{...begin, suite_id:'catalog-failure-drill'}], ['run_suite',{...begin, command:'example'}], ['list_suites',{unexpected:true}],
    ['run_suite',{...begin,request_id:'bad'}], ['get_run',{run_id:'bad'}],
  ]) {
    assert.equal((await client.callTool({name,arguments:args})).isError,true);
  }
});

test('SDK submits a feedback contract run with its original request ID', async t => {
  const client=await session(t);
  const args={suite_id:'feedback-categories',environment:'platform',request_id:randomUUID()};
  const response=await client.callTool({name:'run_suite',arguments:args});
  assert.ok(!response.isError);
  assert.equal(response.structuredContent.suite_id,args.suite_id);
  assert.equal(response.structuredContent.request_id,args.request_id);
});

test('SDK acceptance discovers, refuses, starts, replays, and reads terminal evidence', async t => {
  const report = await runAcceptance(await session(t), {mode:'deterministic',request:DEFAULT_REQUEST});
  assert.equal(report.run_id, runId);
  assert.equal(report.status, 'passed');
  assert.equal(report.invalid_environment_refused, true);
  assert.equal(report.idempotent_replay, true);
  assert.equal(report.mode,'deterministic');
  assert.deepEqual(report.model_calls,[]);
  assert.equal(report.runner_id.length,32);
  assert.deepEqual(Object.keys(report.evidence),['screenshot','trace']);
  assert.ok(report.publication.checks.some(check=>check.detail.includes('1 条')));
  assert.equal(JSON.stringify(report).includes('test-only-authorized-session'),false);
});

test('Qwen receives discovered tools and native calls, only the authorized HTTP origin and model are used',async t=>{
  const client=await session(t);
  const requestId=randomUUID();
  const calls=[];
  t.mock.method(globalThis,'fetch',async (url,opts)=>{
    assert.equal(url,'https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions');
    assert.equal(opts.redirect,'error');
    assert.equal(new Headers(opts.headers).get('authorization'),'Bearer provider-test-key');
    const data=JSON.parse(opts.body);
    assert.equal(data.model,'qwen-plus');
    assert.deepEqual(data.tools.map(v=>v.function.name).sort(),['get_run','list_suites','run_suite']);
    assert.ok(!opts.body.includes('test-only-authorized-session'));
    const index=calls.length;
    const name=['list_suites','run_suite','get_run'][index];
    const args=[{},{suite_id:'published-catalog',environment:'platform',request_id:requestId},{run_id:runId}][index];
    calls.push(name);
    return Response.json({id:'fixture-'+index,model:'qwen-plus-snapshot',usage:{total_tokens:123},choices:[
      {message:{role:'assistant',tool_calls:[{type:'function',id:'tool-'+index,function:{name,arguments:JSON.stringify(args)}}]}}]});
  });
  const report=await runAcceptance(client,{mode:'qwen',requestId,modelKey:'provider-test-key',
    model:'qwen-plus',baseUrl:'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'});
  assert.deepEqual(calls,['list_suites','run_suite','get_run']);
  assert.equal(report.model_calls[0].model,'qwen-plus');
  assert.equal(report.model_calls[0].response_model,'qwen-plus-snapshot');
  assert.equal(report.model_calls[0].tokens,123);
});

test('untrusted provider endpoint and model never receive credentials',async t=>{
  const client=await session(t);
  const fetch=t.mock.method(globalThis,'fetch',()=>{throw Error('network_not_expected');});
  for (const [baseUrl,model] of [
    ['https://example.org/compatible-mode/v1','qwen-plus'],
    ['https://dashscope.aliyuncs.com/other','qwen-plus'],
    ['https://dashscope.aliyuncs.com/compatible-mode/v1','unknown/name'],
  ]) await assert.rejects(runAcceptance(client,{mode:'qwen',baseUrl,model,modelKey:'secret'}),/provider_config_invalid/);
  assert.equal(fetch.mock.callCount(),0);
});

test('allowlisted HTTP failure code survives without server error text',async t=>{
  const client=await session(t);
  const out=await client.callTool({name:'get_run',arguments:{run_id:randomUUID()}});
  assert.equal(out.isError,true);
  assert.equal(out.content[0].text,'platform_http_404_run_not_found');
});

test('a passed state requires both owner-bound evidence URIs and a nonnegative count',async t=>{
  for (const variant of ['wrong-run','missing-trace','negative-count','error-on-pass']) {
    const client=await session(t,variant);
    await assert.rejects(runAcceptance(client,{mode:'deterministic'}),/passed_evidence_invalid/);
  }
});
