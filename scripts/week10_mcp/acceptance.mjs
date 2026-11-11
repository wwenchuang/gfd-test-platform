import {randomUUID} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';
import {setTimeout as delay} from 'node:timers/promises';
import {qwenPlanner} from './qwen.mjs';

export const DEFAULT_REQUEST = '请在平台测试环境运行已发布目录冒烟套件，查询本次结果和执行证据。';
const suite = {suite_id:'published-catalog',environment:'platform'};
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const pending = ['queued','running'];
const terminal = ['passed','failed','blocked','timed_out','interrupted'];

async function call(client,tool) {
  const result = await client.callTool(tool);
  if (result.isError || !result.structuredContent) {
    const detail = result.content?.[0]?.text;
    throw Error(/^platform_(http_\d{3}(?:_[a-z_]+)?|result_invalid|transport_failed)$/.test(detail) ? detail : 'tool_call_failed');
  }
  return result.structuredContent;
}

function checkRun(value,requestId,runId) {
  if (typeof value.run_id!=='string' || !uuid.test(value.run_id) || (runId && value.run_id !== runId) ||
      value.request_id !== requestId || value.suite_id !== suite.suite_id || value.environment !== suite.environment ||
      ![...pending,...terminal].includes(value.status)) throw Error('run_identity_or_status_invalid');
}

function publicChecks(report) {
  const checks = [
    {name:'请求概述',status:'passed',detail:report.request_summary},
    {name:'工具发现',status:'passed',detail:report.tools.join(', ')},
    {name:'非法环境拒绝',status:'passed',detail:'stdio 参数校验拒绝 production'},
    {name:'重复请求',status:'passed',detail:'相同 request_id 两次返回同一 run_id'},
    {name:'宿主工具选择',status:'passed',detail:report.mode==='qwen' ?
      '千问原生函数调用 '+report.model_calls.map(call=>call.tool).join(' → ') :
      '确定性测试宿主，无模型调用'},
    {name:'运行终态',status:report.status==='passed'?'passed':'failed',detail:report.status+(report.error_kind ? ' / '+report.error_kind : '')},
  ];
  for (const call of report.model_calls) {
    checks.push({name:'模型调用 · '+call.tool,status:'passed',detail:(call.response_model || call.model)+
      ' / '+(call.tokens===null?'用量未回传':call.tokens+' tokens')+' / '+call.elapsed_ms+' ms'});
  }
  if (report.status==='passed') checks.push({name:'目录计数与证据引用',status:'passed',
    detail:report.total+' 条，截图和 trace 引用均匹配本次网页执行记录'});
  if (report.runner_id) checks.push({name:'网页执行记录',status:'passed',detail:report.runner_id});
  return {version:1,run_id:report.run_id,checked_at:Math.floor(Date.now()/1000),checks};
}

export async function runAcceptance(client,{mode,request=DEFAULT_REQUEST,requestId=randomUUID(),modelKey,model,baseUrl,onStarted}) {
  if (!['qwen','deterministic'].includes(mode) || typeof requestId!=='string' || !uuid.test(requestId) ||
      typeof request!=='string' || !request.trim() || request.length>1000) throw Error('input_invalid');
  if (mode==='deterministic' && request!==DEFAULT_REQUEST) throw Error('deterministic_request_unsupported');
  if (mode==='qwen' && (typeof modelKey!=='string' || !modelKey || /[\r\n]/.test(modelKey))) throw Error('model_key_required');
  const {tools} = await client.listTools();
  const names = tools.map(tool=>tool.name).sort();
  if (!isDeepStrictEqual(names,['get_run','list_suites','run_suite'])) throw Error('tool_catalog_invalid');
  const report = {mode,request_id:requestId,request_summary:request===DEFAULT_REQUEST ? DEFAULT_REQUEST :
    '运行 platform 的 published-catalog 并读取本次结果，已按请求约束核对工具参数。',
    tools:names,model_calls:[],status_history:[]};
  const args = {...suite, request_id:requestId};
  const invalid = await client.callTool({name:'run_suite',arguments:{...args,environment:'production'}});
  if (!invalid.isError || !invalid.content?.some(item => item.type==='text' && /environment/.test(item.text)))
    throw Error('invalid_environment_not_refused');
  report.invalid_environment_refused = true;
  const choose = mode==='qwen' ? qwenPlanner({key:modelKey,model,baseUrl},tools,request,requestId,report.model_calls) : null;
  let feedback;
  async function execute(name,expected) {
    const proposal = choose ? await choose(feedback) : {name,arguments:expected};
    if (proposal.name!==name || !isDeepStrictEqual(proposal.arguments,expected)) throw Error('tool_choice_outside_request');
    return call(client,proposal);
  }
  const catalog = await execute('list_suites',{});
  if (!Array.isArray(catalog.suites) || !catalog.suites.some(s=>s.suite_id===suite.suite_id && s.environment===suite.environment))
    throw Error('suite_missing');
  feedback = {suites:[suite]};
  const started = await execute('run_suite',args);
  checkRun(started,requestId);
  report.run_id = started.run_id;
  report.status_history.push(started.status);
  onStarted?.(report);
  const replay = await call(client,{name:'run_suite',arguments:args});
  checkRun(replay,requestId,report.run_id);
  report.idempotent_replay = true;
  feedback = {run_id:report.run_id,status:started.status};
  const read = {run_id:report.run_id};
  let state = await execute('get_run',read);
  const deadline = Date.now()+240000;
  for (;;) {
    checkRun(state,requestId,report.run_id);
    if (report.status_history.at(-1)!==state.status) report.status_history.push(state.status);
    if (terminal.includes(state.status)) break;
    if (Date.now()>=deadline) throw Error('poll_deadline_exceeded');
    await delay(1500);
    state = await call(client,{name:'get_run',arguments:read});
  }
  report.status = state.status;
  report.runner_id = /^[a-f0-9]{32}$/.test(state.runner_id) ? state.runner_id : null;
  report.total = Number.isSafeInteger(state.total) && state.total>=0 ? state.total : null;
  const reasons = ['business','environment','authentication','evidence','heavy_workload_busy','check_busy',
    'execution_disabled','worker_unavailable','worker_timeout','browser_timeout','service_restarted','invalid_runner_result'];
  report.error_kind = reasons.includes(state.error_kind) ? state.error_kind : null;
  report.evidence = {};
  for (const name of ['screenshot','trace']) {
    const expected='/api/ui-executions/web-check/artifact?id='+report.runner_id+'&item='+name;
    if (report.runner_id && state.evidence?.[name]===expected) report.evidence[name]=expected;
  }
  if (report.status==='passed' && (state.error_kind!==null || !report.runner_id || report.total===null ||
      !report.evidence.screenshot || !report.evidence.trace)) throw Error('passed_evidence_invalid');
  report.publication = publicChecks(report);
  return report;
}
