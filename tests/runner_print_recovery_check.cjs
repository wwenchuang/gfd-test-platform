const assert=require('node:assert/strict');
const {installPrintRecovery}=require(process.argv[2]);
const prefix='平台打印取消恢复v1：';
const cmd=(operation)=>prefix+JSON.stringify({operation,target:operation==='submit'?'检查无误，确认打印':'取消本次打印',timeout_ms:180000,max_attempts:8,retry_delay_ms:10000});
const frame=(state,extra={})=>({state,printer_id:'printer-test',task_id:state==='ready'?'':'task-new',unique:true,...extra});
function fixture(states, opts={}){
 let time=0, task=0, taps=[],events=[],reads=0;
 const agent={aiQuery:async()=>{reads++;return states.shift()||frame('processing');},aiAssert:async()=>{},aiTap:async p=>taps.push(p)};
 installPrintRecovery(agent,()=>task,{now:()=>time,sleep:async ms=>{time+=ms;opts.onSleep?.();},emit:e=>events.push(e)});
 return {agent,taps,events,get reads(){return reads;},setTask:n=>task=n,setTime:n=>time=n};
}
async function run(){
 let f=fixture([frame('ready'),frame('active'),frame('active'),frame('confirm'),frame('processing'),frame('done')]);
 await f.agent.aiQuery(cmd('submit'));await f.agent.aiQuery(cmd('cancel'));
 assert.equal(f.taps.length,3);assert.equal(f.events.at(-1).attempts,1);assert.equal(f.events.at(-1).status,'passed');
 await assert.rejects(()=>f.agent.aiQuery(cmd('cancel')),/重复/);
 f=fixture([frame('ready'),frame('active'),frame('done')]);await f.agent.aiQuery(cmd('submit'));await assert.rejects(()=>f.agent.aiQuery(cmd('cancel')),/尚未发起取消/);assert.equal(f.events.at(-1).status,'failed');assert.equal(f.taps.length,1);
 f=fixture([frame('ready'),frame('active'),frame('active'),frame('retryable'),frame('done')]);
 await f.agent.aiQuery(cmd('submit'));await f.agent.aiQuery(cmd('cancel'));assert.equal(f.taps.length,2);
 f=fixture([frame('ready'),frame('active'),frame('active'),frame('retryable'),frame('active'),frame('confirm'),frame('done')]);
 await f.agent.aiQuery(cmd('submit'));await f.agent.aiQuery(cmd('cancel'));assert.equal(f.taps.length,4);assert.equal(f.events.at(-1).attempts,2);assert.equal(f.events.at(-1).recovered,true);
 f=fixture([frame('ready'),frame('active'),frame('active'),frame('active')]);
 await f.agent.aiQuery(cmd('submit'));await assert.rejects(()=>f.agent.aiQuery(cmd('cancel')),/超时/);assert.equal(f.taps.length,2);
 f=fixture([frame('ready'),frame('active'),frame('active',{task_id:'another'})]);
 await f.agent.aiQuery(cmd('submit'));await assert.rejects(()=>f.agent.aiQuery(cmd('cancel')),/归属/);assert.equal(f.taps.length,1);
 f=fixture([frame('unknown')]);await assert.rejects(()=>f.agent.aiQuery(cmd('submit')),/空闲/);assert.equal(f.taps.length,0);
 f=fixture([frame('ready'),frame('active')]);await f.agent.aiQuery(cmd('submit'));f.setTask(1);await assert.rejects(()=>f.agent.aiQuery(cmd('cancel')),/绑定/);
 const abort=new AbortController();f=fixture([frame('ready')]);abort.abort();await assert.rejects(()=>f.agent.aiQuery(cmd('submit'),{abortSignal:abort.signal}));assert.equal(f.reads,0);
 const stop=new AbortController(); // separate late-response check
 let resolve;const a={aiQuery:()=>new Promise(r=>resolve=r),aiAssert:async()=>{},aiTap:async()=>{throw Error('must not tap');}};
 installPrintRecovery(a,()=>0);const pending=a.aiQuery(cmd('submit'),{abortSignal:stop.signal});await new Promise(r=>setImmediate(r));stop.abort();resolve(frame('ready'));await assert.rejects(()=>pending);
 let states=[frame('ready'),frame('active'),frame('active')];for(let i=0;i<8;i++)states.push(frame('retryable'),frame('active'));
 f=fixture(states);await f.agent.aiQuery(cmd('submit'));await assert.rejects(()=>f.agent.aiQuery(cmd('cancel')),/次数/);assert.equal(f.taps.length,9);
 f=fixture([frame('ready'),frame('active'),frame('active'),frame('retryable'),frame('retryable'),frame('done')]);
 await f.agent.aiQuery(cmd('submit'));await f.agent.aiQuery(cmd('cancel'));assert.equal(f.taps.length,3);assert.equal(f.events.at(-1).attempts,2);assert.ok(f.events.at(-1).elapsed_ms>=10000);
 console.log('print recovery state checks passed');
}
run().catch(e=>{console.error(e);process.exit(1)});
