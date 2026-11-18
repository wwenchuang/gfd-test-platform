const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM } = require('../api-testing-ui/node_modules/jsdom');
const tick = () => new Promise(setImmediate);
const task = (extra = {}) => ({ id: 'task-1', source: 'ui', run_id: 'uib_example', status: 'completed',
  result: { summary: '请求超时，仍需核对服务日志', facts: [{ text: '响应耗时 30000ms', evidence_ids: ['trace-1'] }], hypotheses: [{ text: '可能是网关超时', evidence_ids: ['trace-1'] }], recommendations: [{ text: '核对该请求的网关日志', evidence_ids: ['trace-1'] }], missing_evidence: ['没有同环境成功记录'] },
  evidence: [{ id: 'trace-1', kind: 'request_trace', title: '本次请求', href: '/task-manager.html?ui_batch=uib_example', data: { elapsed_ms: 30000 } }],
  trace: [{ round: 1, tool: 'get_request_trace', arguments: { case_id: 'case-1' }, status: 'completed', elapsed_ms: 30, summary: '读取本次请求和回执' }], elapsed_ms: 2000, usage: { prompt_tokens: 100, completion_tokens: 50, total_tokens: 150 }, ...extra });
function setup(t, request) {
  const dom = new JSDOM('<main></main>', { url: 'http://localhost', runScripts: 'dangerously', pretendToBeVisual: true });
  const w = dom.window;
  if (fs.existsSync('js/failure-analysis.js')) w.eval(fs.readFileSync('js/failure-analysis.js', 'utf8'));
  assert.equal(typeof w.FailureAnalysis?.mount, 'function', 'FailureAnalysis mount must be implemented');
  let identity = 'owner'; const calls = [];
  const node = w.document.querySelector('main');
  const view = w.FailureAnalysis.mount(node, { source: 'ui', runId: 'uib_example', identity: () => identity,
    request: async (path, options = {}) => { calls.push({ path, ...options }); return request(path, options); } });
  t.after(() => { view.dispose(); dom.window.close(); });
  return { w, node, view, calls, setIdentity: value => { identity = value; } };
}
test('opens read-only, restores structured results and actual investigation evidence', async t => {
  const f = setup(t, async () => ({ ok: true, task: task() })); await tick();
  assert.equal(f.calls.length, 1); assert.match(f.calls[0].path, /source=ui&run_id=uib_example/); assert.notEqual(f.calls[0].method, 'POST');
  for (const label of ['失败概况', '已确认事实', '可能原因', '建议动作', '还缺哪些证据', '调查记录', '响应耗时 30000ms', '读取本次请求和回执', '没有同环境成功记录', '150']) assert.ok(f.node.textContent.includes(label), label);
  assert.equal(f.node.querySelector('textarea'), null);
  f.node.querySelector('[data-analysis-ref]').click(); assert.equal(f.node.querySelector('[data-analysis-evidence]').open, true);
});
test('explicit start is idempotent across uncertain submission and can cancel the returned task', async t => {
  let attempts = 0;
  const f = setup(t, async (path, o) => {
    if (path.endsWith('/cancel')) return { ok: true, task: task({ status: 'cancelled' }) };
    if (o.method === 'POST') { if (++attempts === 1) throw Error('网络中断'); return { ok: true, task: task({ status: 'running' }) }; }
    return { ok: true, task: null };
  }); await tick();
  f.node.querySelector('[data-analysis-start]').click(); await tick();
  assert.match(f.node.textContent, /网络中断/); f.node.querySelector('[data-analysis-retry]').click(); await tick();
  const posts = f.calls.filter(c => c.method === 'POST'); assert.equal(posts.length, 2);
  assert.match(posts[0].body.request_id, /^[0-9a-f-]{36}$/); assert.equal(posts[0].body.request_id, posts[1].body.request_id);
  assert.equal(posts[0].body.source, 'ui'); assert.equal(posts[0].body.run_id, 'uib_example');
  f.node.querySelector('[data-analysis-cancel]').click(); await tick();
  assert.equal(f.calls.at(-1).path, '/failure-analysis/cancel'); assert.deepEqual(JSON.parse(JSON.stringify(f.calls.at(-1).body)), { id: 'task-1' });
  assert.match(f.node.textContent, /已停止/);
});
test('running tasks refresh by task id and hidden or disposed views stop reads', async t => {
  let current = task({ status: 'running' }); const f = setup(t, async () => ({ ok: true, task: current })); await tick();
  current = task({ status: 'limited', stop_reason: 'token_budget' }); await f.view.refresh();
  assert.match(f.calls.at(-1).path, /\?id=task-1/); assert.match(f.node.textContent, /调查受限/);
  const count = f.calls.length; Object.defineProperty(f.w.document, 'hidden', { configurable: true, value: true }); await f.view.refresh(); assert.equal(f.calls.length, count);
  Object.defineProperty(f.w.document, 'hidden', { configurable: true, value: false }); f.view.dispose(); await f.view.refresh(); assert.equal(f.calls.length, count);
});
test('transport failure preserves findings while revoked access clears private findings', async t => {
  let error; const f = setup(t, async () => { if (error) throw error; return { ok: true, task: task() }; }); await tick();
  error = Error('暂时离线'); await f.view.refresh(); assert.match(f.node.textContent, /响应耗时 30000ms/); assert.equal(f.node.querySelector('[data-analysis-retry]').hidden, false);
  error = Object.assign(Error('无权访问'), { status: 403 }); await f.view.refresh(); assert.doesNotMatch(f.node.textContent, /响应耗时 30000ms|case-1/); assert.equal(f.node.querySelector('[data-analysis-start]').disabled, true);
});
test('account change rejects a late response and removes earlier private findings', async t => {
  let resolve; let first = true; const f = setup(t, async () => first ? (first = false, { ok: true, task: task() }) : new Promise(r => { resolve = r; })); await tick();
  const pending = f.view.refresh(); f.setIdentity('other'); resolve({ ok: true, task: task({ result: { summary: 'late private value' } }) }); await pending;
  assert.doesNotMatch(f.node.textContent, /late private value|响应耗时 30000ms|case-1/);
});
test('only tool evidence can be cited, text is escaped and external or executable links are rejected', async t => {
  const f = setup(t, async () => ({ ok: true, task: task({ result: { summary: '<img src=x onerror=alert(1)>', facts: [{ text: 'forged fact', evidence_ids: ['missing'] }], hypotheses: [{ text: 'unconfirmed', evidence_ids: ['missing', 'trace-1'] }], recommendations: [], missing_evidence: [] }, evidence: [
    { id: 'trace-1', title: 'safe', href: '/report', data: { status: 504 } }, { id: 'evil', title: 'external', href: 'https://evil.invalid' }, { id: 'script', title: 'bad', href: 'javascript:alert(1)' },
  ] }) })); await tick();
  assert.equal(f.node.querySelector('img'), null); assert.doesNotMatch(f.node.textContent, /forged fact/);
  assert.deepEqual([...f.node.querySelectorAll('a')].map(a => a.getAttribute('href')), ['/report']);
  assert.deepEqual([...f.node.querySelectorAll('[data-analysis-ref]')].map(n => n.dataset.analysisRef), ['trace-1']);
});
test('HTTP pages use cryptographic UUID v4 fallback when randomUUID is unavailable', async t => {
  const f = setup(t, async (_, o) => ({ ok: true, task: o.method === 'POST' ? task({ status: 'queued' }) : null })); await tick();
  Object.defineProperty(f.w.crypto, 'randomUUID', { value: undefined });
  f.node.querySelector('[data-analysis-start]').click(); await tick();
  assert.match(f.calls.at(-1).body.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test('native model/tool trace names and partial provider usage stay truthful', async t => {
  const f = setup(t, async () => ({ ok: true, task: task({ status: 'limited', stop_reason: 'round_budget', usage: { complete: false, total_tokens: 0, estimated_tokens: 1700, missing_usage_calls: 1 }, trace: [
    { kind: 'model', round: 1, status: 'completed', elapsed_ms: 80 },
    { kind: 'tool', name: 'get_request_trace', round: 1, status: 'completed', elapsed_ms: 2, arguments: { case_id: 'case-a' }, evidence_ids: ['trace-1'], missing: ['缺少业务日志'] },
  ] }) })); await tick();
  for(const text of ['核对证据与选择下一步','核对请求与回执','取得 1 份证据','缺少业务日志','供应商用量不完整','1700','调查轮次上限']) assert.ok(f.node.textContent.includes(text),text);
  assert.doesNotMatch(f.node.textContent,/停止原因：round_budget/);
});

test('reading an older completed task does not lose an uncertain start request id', async t => {
  let attempts=0;const f=setup(t,async(_path,o)=>{
    if(o.method==='POST'){if(++attempts===1)throw Error('提交超时');return {ok:true,task:task()};}
    return {ok:true,task:task()};
  });await tick();f.node.querySelector('[data-analysis-start]').click();await tick();
  await f.view.refresh();f.node.querySelector('[data-analysis-start]').click();await tick();
  const writes=f.calls.filter(c=>c.method==='POST');assert.equal(writes.length,2);assert.equal(writes[0].body.request_id,writes[1].body.request_id);
});
test('evidence disclosure retains focus when a new investigation action arrives',async t=>{
  let current=task();const f=setup(t,async()=>({ok:true,task:current}));await tick();
  const details=f.node.querySelector('[data-analysis-evidence]');details.open=true;details.querySelector('summary').focus();
  current=task({trace:[...current.trace,{round:2,tool:'compare_last_pass',status:'completed',summary:'无匹配成功记录'}]});await f.view.refresh();
  assert.equal(f.node.querySelector('[data-analysis-evidence]').open,true);
  assert.equal(f.w.document.activeElement,f.node.querySelector('[data-analysis-evidence] summary'));
});

test('source missing codes are readable in both conclusions and actual tool records without opaque IDs in primary copy',async t=>{
 const codes=['evidence_output_truncated','secret_context_incomplete','step_progress_not_recorded:opaque-child-42','failed_cases_truncated_at_25','failed_case_not_recorded','request_not_recorded','response_not_recorded','trace_not_recorded','network_trace_not_collected','runner_logs_not_recorded','environment_identity_unavailable','run_timestamp_not_recorded','prior_comparable_pass_not_found','future_missing:opaque-private-1','需要业务方补充本次请求的网关日志。'];
 const f=setup(t,async()=>({ok:true,task:task({result:{summary:'待补证据',facts:[],hypotheses:[],recommendations:[],missing_evidence:codes},trace:[{kind:'tool',name:'get_request_trace',round:1,status:'completed',missing:codes,arguments:{},evidence_ids:[]}]})}));await tick();
 const messages=[...f.node.querySelectorAll('[data-analysis-missing-message]')].map(n=>n.textContent);
 assert.equal(messages.length,codes.length*2);assert.ok(messages.every(text=>!text.includes('opaque-')&&!text.includes('_not_')));
 for(const message of ['本次 UI 执行未采集网络请求与回执，需要同次运行的网络证据。','没有找到同用例版本、同环境且早于本次的成功记录，无法完成有效对照。','该项执行证据尚未记录。','需要业务方补充本次请求的网关日志。']) assert.ok(messages.includes(message),message);
 const details=[...f.node.querySelectorAll('[data-analysis-missing-code]')];assert.equal(details.length,(codes.length-1)*2);assert.ok(details.every(node=>!node.open));
 assert.ok(details.some(node=>node.textContent.includes('step_progress_not_recorded:opaque-child-42')));
});

test('model authentication, entitlement and rate-limit failures give distinct actions while preserving collected evidence',async t=>{
 const expected={
  model_http_401:'千问鉴权失败，请管理员核对服务端凭证与地域后重试',
  model_http_403:'千问访问被拒绝，请管理员核对模型权限与服务开通状态后重试',
  model_http_429:'千问请求受到限流，请稍后重试',
 };
 let current=task({status:'failed',stop_reason:'model_http_401'});
 const f=setup(t,async()=>({ok:true,task:current}));await tick();
 for(const [code,message] of Object.entries(expected)){
  current=task({status:'failed',stop_reason:code});await f.view.refresh();
  assert.ok(f.node.textContent.includes(message),code);
  assert.match(f.node.textContent,/响应耗时 30000ms/);
  assert.equal(f.node.querySelectorAll('[data-analysis-evidence]').length,1);
  assert.equal(f.node.querySelector('[data-analysis-start]').disabled,false);
 }
});
