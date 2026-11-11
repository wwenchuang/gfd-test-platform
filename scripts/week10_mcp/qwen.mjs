const defaultBase = 'https://dashscope.aliyuncs.com/compatible-mode/v1';
const origins = ['https://dashscope.aliyuncs.com','https://dashscope-intl.aliyuncs.com','https://dashscope-us.aliyuncs.com'];
const modelName = /^[A-Za-z0-9._-]{1,100}$/;
function officialEndpoint(baseUrl) {
  let url;
  try {url=new URL(baseUrl);} catch {throw Error('provider_config_invalid');}
  if (!origins.includes(url.origin) || url.username || url.password || url.search || url.hash ||
      url.pathname.replace(/\/$/,'')!=='/compatible-mode/v1') throw Error('provider_config_invalid');
  return url.origin+'/compatible-mode/v1/chat/completions';
}
export function qwenPlanner({key,model='qwen3.8-flash',baseUrl=defaultBase},tools,request,requestId,receipts) {
  if (typeof model!=='string' || !modelName.test(model)) throw Error('provider_config_invalid');
  const endpoint=officialEndpoint(baseUrl);
  const messages = [
    {role:'system',content:'Use the available tools to carry out this allowed QA request. Discover available suites first, start the requested suite once using the supplied request_id, then inspect the returned run_id. Make one native function call per response. Never invent identifiers. request_id='+requestId},
    {role:'user',content:request},
  ];
  const functions = tools.map(({name,description,inputSchema}) => ({
    type:'function',function:{name,description,parameters:inputSchema},
  }));
  let previous;
  return async function choose(toolResult) {
    if (previous) messages.push({role:'tool',tool_call_id:previous.id,content:JSON.stringify(toolResult)});
    const start = Date.now();
    const response = await fetch(endpoint,{
      method:'POST',redirect:'error',signal:AbortSignal.timeout(60000),
      headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'},
      body:JSON.stringify({model,messages,tools:functions,tool_choice:'auto',parallel_tool_calls:false,
        stream:false,enable_thinking:false,max_tokens:512}),
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw Error('provider_http_'+response.status);
    }
    const data = await response.json();
    const message = data.choices?.[0]?.message;
    previous = message?.tool_calls?.[0];
    if (message?.tool_calls?.length !== 1 || previous?.type !== 'function' ||
        !tools.some(tool=>tool.name===previous.function?.name)) throw Error('provider_tool_choice_invalid');
    const tokens = data.usage?.total_tokens;
    receipts.push({model,response_model:typeof data.model==='string' && modelName.test(data.model) ? data.model : null,tool:previous.function.name,
      response_id:typeof data.id==='string' && /^[a-zA-Z0-9_-]{1,150}$/.test(data.id) ? data.id : null,
      tokens:Number.isSafeInteger(tokens) && tokens>=0 ? tokens : null,elapsed_ms:Date.now()-start});
    messages.push({role:'assistant',content:null,tool_calls:[previous]});
    let args;
    try {args=JSON.parse(previous.function.arguments);} catch {throw Error('provider_arguments_invalid');}
    return {name:previous.function.name,arguments:args};
  };
}
