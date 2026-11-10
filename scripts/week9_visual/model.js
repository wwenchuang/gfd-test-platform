'use strict';

const MODEL_HOSTS = new Set(['dashscope.aliyuncs.com','dashscope-intl.aliyuncs.com','dashscope-us.aliyuncs.com']);
const LIMITS = Object.freeze({rounds:10, model_calls_per_step:1, model_calls_per_run:3, model_calls_total:110,
  output_tokens_per_call:512, request_bytes:3*1024*1024, model_timeout_ms:35000, run_timeout_ms:145000});
function modelEndpoint(base) {
  const url=new URL(base);
  if (url.protocol!=='https:' || !MODEL_HOSTS.has(url.host) || url.username || url.password || url.search || url.hash ||
      url.pathname.replace(/\/$/,'')!=='/compatible-mode/v1') throw Error('model_endpoint_not_allowed');
  return url.origin+'/compatible-mode/v1/chat/completions';
}
function createAgent(page, credentials, run, report, save) {
  const {PlaywrightAgent}=require('@midscene/web/playwright');
  const endpoint=modelEndpoint(credentials.base_url);
  let activeStep=null;
  const makeClient=async()=>({chat:{completions:{create:async params=>{
    if (!activeStep || activeStep.calls.length || run.model_calls>=LIMITS.model_calls_per_run ||
        report.runs.reduce((sum,item)=>sum+item.model_calls,0)>=LIMITS.model_calls_total) throw Error('model_budget');
    if (params.model!==report.model || params.stream || !Array.isArray(params.messages)) throw Error('model_request_not_allowed');
    const body=JSON.stringify({...params,stream:false,max_tokens:LIMITS.output_tokens_per_call,enable_thinking:false});
    if (Buffer.byteLength(body)>LIMITS.request_bytes) throw Error('model_request_size');
    const call={status:'in_flight',tokens:null,elapsed_ms:null,http_status:null};
    activeStep.calls.push(call);run.model_calls++;save();
    const started=Date.now();
    try {
      const res=await fetch(endpoint,{method:'POST',redirect:'error',headers:{Authorization:'Bearer '+credentials.model_key,
        'Content-Type':'application/json'},body,signal:AbortSignal.timeout(LIMITS.model_timeout_ms)});
      call.http_status=res.status;
      if (!res.ok) {await res.body?.cancel();throw Error('provider_http_'+res.status);}
      const data=await res.json();
      const tokens=data.usage?.total_tokens;
      if (Number.isSafeInteger(tokens) && tokens>=0) call.tokens=tokens;
      call.status='received';
      return data;
    } catch (error) {
      call.status=error.name==='TimeoutError' || error.name==='AbortError'?'model_timeout':
        /^provider_http_[0-9]{3}$/.test(error.message)?error.message:'provider_transport';
      throw Error(call.status);
    } finally {call.elapsed_ms=Date.now()-started;save();}
  }}}});
  const agent=new PlaywrightAgent(page,{cache:false,generateReport:false,persistExecutionDump:false,autoPrintReportMsg:false,
    replanningCycleLimit:1,createOpenAIClient:makeClient,modelConfig:{
      MIDSCENE_MODEL_BASE_URL:'http://127.0.0.1:1/v1',MIDSCENE_MODEL_API_KEY:'isolated-client',
      MIDSCENE_MODEL_NAME:report.model,MIDSCENE_MODEL_FAMILY:'qwen3',MIDSCENE_MODEL_RETRY_COUNT:0,
      MIDSCENE_MODEL_TIMEOUT:LIMITS.model_timeout_ms,
    }});
  return {
    async locate(step,instruction) {
      activeStep=step;
      try {
        const location=await agent.aiLocate(instruction);
        if (!Array.isArray(location?.center) || location.center.length!==2 || !location.center.every(Number.isFinite)) throw Error('invalid_location');
        return location.center;
      } finally {activeStep=null;}
    },
    async close(){await agent.destroy();}
  };
}
module.exports={LIMITS,modelEndpoint,createAgent};
