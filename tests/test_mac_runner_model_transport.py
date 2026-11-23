import subprocess

from test_mac_runner_runtime_config import load_runner


BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_transport_scope_and_explicit_model_settings():
    runner = load_runner()
    for enabled, extra in [
        (False, {}), (True, {"MIDSCENE_MODEL_BASE_URL": "https://other.example/v1"}),
        (True, {"MIDSCENE_MODEL_RETRY_COUNT": "0"}), (True, {"MIDSCENE_MODEL_RETRY_COUNT": "2"}),
        (True, {"MIDSCENE_MODEL_RETRY_INTERVAL": "500"}),
        (True, {"MIDSCENE_PLANNING_MODEL_NAME": "separate-model"}),
    ]:
        env = {"MIDSCENE_MODEL_BASE_URL": BASE, **extra}
        original = dict(env)
        env["MIDSCENE_ZXB_MODEL_CONNECT_RETRY"] = "1"
        runner.configure_zxb_model_transport(env, enabled)
        assert env == original
    env = {"MIDSCENE_MODEL_BASE_URL": BASE + "/", "MIDSCENE_MODEL_TIMEOUT": "45000"}
    runner.configure_zxb_model_transport(env, True)
    assert env["MIDSCENE_MODEL_TIMEOUT"] == "45000"
    assert env["MIDSCENE_MODEL_RETRY_COUNT"] == "0"
    assert env["MIDSCENE_ZXB_MODEL_CONNECT_RETRY"] == "1"


def test_inference_fetch_only_retries_confirmed_preconnect_failures():
    runner = load_runner()
    script = runner.RUNNER_ZXB_MODEL_TRANSPORT + r'''
const assert = require('node:assert/strict');
const endpoint = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions';
const opts = () => ({method:'POST', body:'{"model":"fixture"}', headers:{'x-midscene-execution-id':'fixture'}});
const fail = (code, syscall) => new TypeError('fetch failed', {cause:Object.assign(new Error('details'), {code,syscall})});
async function exercise(replies, options = opts(), url = endpoint, duringWait) {
  const attempts=[], delays=[];
  const base = async(...args) => {
    attempts.push(args);
    const reply = replies[attempts.length-1];
    assert.ok(reply !== undefined, 'unexpected extra request');
    if(reply instanceof Error) throw reply;
    return reply;
  };
  const delay = async(ms, _, cfg) => {delays.push(ms); if(duringWait) duringWait(cfg.signal);};
  const fetch = zxbModelConnectFetch(base, delay);
  let value;
  try { value = await fetch(url, options); } catch(err) {value=err;}
  assert.ok(attempts.every(([u, i])=> u===url && i===attempts[0][1] &&
    i.body===options.body && i.signal===options.signal && i.headers===options.headers));
  return {attempts, delays, value};
}
(async()=>{
  const timeout=fail('ETIMEDOUT','connect'), response=new Response('ok'), permanent=new Response(null,{status:401});
  let r=await exercise([timeout, timeout, response]); assert.equal(r.value,response); assert.deepEqual(r.delays,[3000,3000]);
  assert.equal(r.attempts[0][1].redirect,'manual');
  r=await exercise([timeout, timeout, timeout, timeout]); assert.equal(r.value,timeout); assert.equal(r.attempts.length,4);
  for(const reason of [permanent, new Response(null,{status:307,headers:{location:'https://other.example'}}), new Response(null,{status:503}), fail('ECONNRESET','read'),
                       fail('ETIMEDOUT','read'), fail('ERR_TLS_CERT_ALTNAME_INVALID','connect')]){
    r=await exercise([reason]); assert.equal(r.value,reason); assert.equal(r.attempts.length,1);
  }
  for (const [url,options] of [[endpoint,{...opts(),method:'GET'}],[endpoint,{...opts(),headers:{}}],
      [endpoint.replace('/chat/completions','/files'),opts()], [endpoint.replace('dashscope.aliyuncs.com','other.example'),opts()]]) {
    r=await exercise([timeout],options,url); assert.equal(r.value,timeout); assert.equal(r.attempts.length,1);
    assert.equal(r.attempts[0][1],options);
  }
  const controller=new AbortController(); const signal=controller.signal;
  r=await exercise([timeout],{...opts(),signal},endpoint,s=>{assert.equal(s,signal);controller.abort();});
  assert.equal(r.attempts.length,1); assert.equal(r.value.name,'AbortError');
  const stream=new ReadableStream({pull(c){c.error(new Error('midstream failure'));}});
  r=await exercise([new Response(stream)]); await assert.rejects(r.value.text()); assert.equal(r.attempts.length,1);
  console.log('passed');
})().catch(err=>{console.error(err);process.exitCode=1;});
'''
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=8)
    assert result.returncode == 0 and "passed" in result.stdout, result.stderr
