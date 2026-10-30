import fs from 'node:fs';
import vm from 'node:vm';
import {createHash} from 'node:crypto';
import {EventEmitter} from 'node:events';
const source=fs.readFileSync(new URL('../ai-gateway/server.js',import.meta.url),'utf8');
const route={model:'qwen3.8-max',baseUrl:'https://dashscope.aliyuncs.com/compatible-mode/v1',temperature:.1};
const ctx=vm.createContext({Buffer,createHash});
vm.runInContext(source.slice(source.indexOf('function requestedCompletionTokens('),source.indexOf('async function appendAiLog('))+
 source.slice(source.indexOf('function buildUserMessage('),source.indexOf('\nfunction assistantOutputText')),ctx);
function complete(body,selected={...route,model:body.videoStage==='semantic'?'qwen3.8-flash':route.model}) {ctx.body=body;ctx.route=selected;return JSON.parse(JSON.stringify(vm.runInContext('completionOptionsForRoute(route,"pixels",body)',ctx)));}
const observerSchema=JSON.parse(fs.readFileSync(new URL('../ai_skills/schemas/ui_video_trajectory.schema.json',import.meta.url),'utf8')).oneOf[0];
function project(node, normalized=true) {
 if(node.oneOf)return {[normalized?'anyOf':'oneOf']:node.oneOf.map(child=>project(child,normalized))};
 const out=Object.fromEntries(Object.entries(node).filter(([k])=>['type','enum','additionalProperties'].includes(k)));
 if(out.enum&&normalized)out.type??='string';
 if(node.maxLength===0&&normalized)out.enum=[''];
 if(node.properties) {out.properties=Object.fromEntries(Object.entries(node.properties).map(([k,v])=>[k,project(v,normalized)]));out.required=Object.keys(out.properties);}
 if(node.items)out.items=project(node.items,normalized);
 return out;
}
function schema() {return project(observerSchema);}
function input(stage='perception',n=8) {
 const frames=Array.from({length:n},(_,i)=>({frame_id:`frame-${String(i).padStart(3,'0')}`,timestamp_ms:i*100}));
 const b={skillName:stage==='perception'?'ui_video_perception':'ui_video_trajectory',videoStage:stage,jsonResponse:true,
 skillVersion:stage==='perception'?'v1':stage==='semantic'?'observe':stage,videoPolicyVersion:'video-stage-policy-v1',videoCorrelation:'a'.repeat(32),timeoutMs:120000,videoDeadlineMs:Date.now()+120000,
 payload:{phase:stage==='perception'?'whole-perception':'whole-trajectory',packet_id:'w1',frames,image_frames:frames,
 owned_frame_ids:frames.slice(0,-1).map(f=>f.frame_id),context_frame_ids:[frames.at(-1).frame_id]},
 imageAssets:frames.map(f=>({name:f.frame_id,timestamp_ms:f.timestamp_ms,mime:'image/jpeg',base64:'YQ=='}))};
 if(stage==='semantic') {b.model='qwen3.8-flash';b.imageAssets=[];delete b.payload.image_frames;b.payload.reasoning_mode='bounded';b.outputSchema=schema();}
 if(stage==='audit')Object.assign(b.payload,{reasoning_mode:'bounded',perception_mode:'fine-detail',perception_frame_ids:frames.slice(0,Math.min(n,12)).map(f=>f.frame_id)});
 return b;
}
function orchestration(fail=false, usage={prompt_tokens:1,completion_tokens:2,total_tokens:3},dispatch=null,primaryRoute=route) {
 const calls=[],logs=[];let handler;
 const official=JSON.parse(fs.readFileSync(new URL('../ai-gateway/config/providers.json',import.meta.url),'utf8')).providers.qwen_plus;
 const providers={providers:{primary:{...primaryRoute,name:'primary'},backup:{...route,name:'backup'},qwen_plus:official}};
 const sandbox=vm.createContext({Buffer,createHash,AbortController,setTimeout,clearTimeout,DYNAMIC_PROVIDER_PREFIX:'dynamic:',ROUTER_FILE:'offline',AI_CALL_TIMEOUT_MS:120000,
 AI_CALL_FALLBACK_RESERVE_MS:10000,MOCK_ENABLED:false,SKILL_ACTION_MAP:{},readJson:async()=>({generate_case:{providerId:'primary',fallbackProviderIds:['backup']}}),
 readProviders:async()=>providers,uuidv4:()=> 'offline',appendAiLog:async e=>logs.push(e),sanitizeError:e=>String(e.message),preview:()=>'',
 asyncRoute:fn=>fn,app:{post:(_p,fn)=>{handler=fn;}},clientForRoute:r=>({chat:{completions:{create:async (options,requestOptions)=>{
  calls.push({providerId:r.providerId,options});if(dispatch)return dispatch(options,requestOptions);if(fail)throw new Error('primary timeout');
  return {model:r.model,choices:[{message:{content:'{}'},finish_reason:'stop'}],usage};
 }}}})});
 for(const [start,end] of [['function normalizeLegacyProviderId(','async function discoverProviderChannel('],
 ['function routeFromProviderConfig(','function clientForRoute('],['function requestedCallTimeoutMs(','async function appendAiLog('],
 ['function stripMarkdownFence(','function mockAiOutput('],["app.post('/ai/skill'","app.post('/ai/analyze-failure'"]])
 vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end)),sandbox);
 return {calls,logs,get handler(){return handler;},async request(body, prompt='pixels') {let response;const req=Object.assign(new EventEmitter(),{body:{...body,prompt}});const res=Object.assign(new EventEmitter(),{json:r=>response=r});await handler(req,res);return response;}};
}

export {source, route, ctx, complete, input, schema, project, observerSchema, orchestration};
