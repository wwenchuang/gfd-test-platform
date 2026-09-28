const fs=require('node:fs'); const vm=require('node:vm'); const test=require('node:test'); const assert=require('node:assert/strict');
const source=fs.readFileSync('windows-midscene-runner.py','utf8');
function observer(Player){
 const script=source.match(/RUNNER_PROGRESS_OBSERVER = r'''([\s\S]*?)'''/); assert.ok(script,'embedded observer exists');
 const frames=[];const timers=new Set();
 const req=name=> name.includes('package.json')?{version:'1.13.0'}:{ScriptPlayer:Player};req.resolve=x=>x;
 vm.runInNewContext(script[1],{require:req,process:{argv:['node','/cli/bin/midscene'],stdout:{write:line=>frames.push(JSON.parse(line.slice('MIDSCENE_PLATFORM_PROGRESS '.length)))}},setInterval:fn=>{timers.add(fn);return fn;},clearInterval:fn=>timers.delete(fn),console});
 return {frames,tick:()=>timers.forEach(fn=>fn()),timers};
}
test('observer preserves return value, captures steps and removes timer',async()=>{
 let resume;class Player{constructor(){this.taskStatusList=[{name:'测试',status:'init',flow:[{launch:'app'},{aiTap:'我的'},{aiInput:'private-password'}]}];} async run(){this.taskStatusList[0].status='running'; await new Promise(r=>resume=r); this.taskStatusList[0].status='done';return 42;}}
 const o=observer(Player);const p=new Player();const promise=p.run();p.taskStatusList[0].currentStep=1;o.tick();
 assert.equal(o.frames.at(-1).tasks[0].current_step,1);assert.equal(o.frames.at(-1).tasks[0].steps[1].label,'点击 · 我的');
 assert.doesNotMatch(JSON.stringify(o.frames),/private-password/);resume();assert.equal(await promise,42);
 assert.equal(o.frames.at(-1).tasks[0].status,'passed');assert.equal(o.timers.size,0);
});
test('observer preserves original error and failed step',async()=>{
 const failure=new Error('assertion');class Player{constructor(){this.taskStatusList=[{name:'重复名',status:'running',currentStep:1,flow:[{launch:'a'},{aiAssert:'标题正确'},{aiTap:'后续'}]}];}async run(){this.taskStatusList[0].status='error';throw failure;}}
 const o=observer(Player);await assert.rejects(new Player().run(),e=>e===failure);assert.equal(o.frames.at(-1).tasks[0].status,'failed');assert.equal(o.timers.size,0);
});
test('observing an unsupported player does not prevent its run',async()=>{
 class Player{async run(){return 'unchanged';}}
 const o=observer(Player);assert.equal(await new Player().run(),'unchanged');assert.equal(o.timers.size,0);
});
