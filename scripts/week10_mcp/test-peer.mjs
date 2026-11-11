// Process-local HTTP fixture; the official client/server still negotiate over child stdio.
import assert from 'node:assert/strict';
const runs = new Map();
const id = '72fbaa24-3e2d-4c16-9b04-5e77e283538c';
globalThis.fetch = async (url,options) => {
  assert.equal(String(url),'http://127.0.0.1:8088/api/platform/mcp/call');
  assert.equal(options.method,'POST');
  assert.equal(options.redirect,'error');
  assert.equal(new Headers(options.headers).get('authorization'),'Bearer test-only-authorized-session');
  const {name,arguments:args} = JSON.parse(options.body);
  let result;
  if (name === 'list_suites') {
    assert.deepEqual(args,{});
    result = {suites:[{suite_id:'published-catalog',environment:'platform',title:'已发布目录'}]};
  } else if (name === 'run_suite') {
    assert.equal(args.environment,'platform');
    assert.ok(['published-catalog','feedback-categories'].includes(args.suite_id));
    assert.deepEqual(Object.keys(args).sort(),['environment','request_id','suite_id']);
    if (!runs.has(args.request_id)) runs.set(args.request_id,{...args,run_id:id,status:'running'});
    result = runs.get(args.request_id);
  } else if (name === 'get_run') {
    if (args.run_id!==id) return Response.json({ok:false,code:'run_not_found',error:'private-server-message'},{status:404});
    result = {...[...runs.values()][0],status:'passed',error_kind:null,runner_id:'abe21e09f979465f982f08e89f48373d',total:1,
      evidence:Object.fromEntries(['screenshot','trace'].map(item=>[item,'/api/ui-executions/web-check/artifact?id=abe21e09f979465f982f08e89f48373d&item='+item]))};
    if (process.env.FIXTURE_VARIANT==='wrong-run') result.evidence.screenshot=result.evidence.screenshot.replace(result.runner_id,'0'.repeat(32));
    if (process.env.FIXTURE_VARIANT==='missing-trace') delete result.evidence.trace;
    if (process.env.FIXTURE_VARIANT==='negative-count') result.total=-1;
    if (process.env.FIXTURE_VARIANT==='error-on-pass') result.error_kind='environment';
  } else assert.fail('unexpected HTTP call');
  return Response.json({ok:true,result});
};
