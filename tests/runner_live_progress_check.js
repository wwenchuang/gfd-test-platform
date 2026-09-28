const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = fs.readFileSync('js/app.js', 'utf8');
const ctx = vm.createContext({escapeHtml: s => String(s).replaceAll('<','&lt;'), jsArg: JSON.stringify, runnerProgressExpanded: new Set()});
for (const name of ['runnerJobOrder', 'runnerProgressView', 'runnerProgressHtml']) {
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf('\nfunction ', start + 1);
  if (start >= 0) vm.runInContext(source.slice(start, end < 0 ? undefined : end), ctx);
}
const view = job => ctx.runnerProgressView(job);
test('running precedes newer queued jobs', () => {
 const jobs=[{status:'pending',created_at:'2026-09-28 12:00:00'},{status:'running',created_at:'2026-09-27 12:00:00'}];
 jobs.sort(ctx.runnerJobOrder); assert.equal(jobs[0].status,'running');
});
test('confirmed snapshot exposes actual step and failure leaves later steps gray',()=>{
 const v=view({status:'failed',execution_progress:{version:1,tasks:[{name:'用例',status:'failed',current_step:1,steps:[{label:'启动'},{label:'等待我的'},{label:'点击我的'}]}]}});
 assert.deepEqual(Array.from(v.steps,x=>x.status),['passed','failed','pending']); assert.equal(v.currentLabel,'等待我的');
});
test('legacy runner cannot invent steps or task successes from percentage',()=>{
 const v=view({status:'running',progress:95,task_names:['A','B'],current_task_index:1});
 assert.equal(v.steps.length,0); assert.equal(v.tasks.filter(t=>t.status==='passed').length,0);
 assert.match(ctx.runnerProgressHtml({status:'running',task_names:['A']}),/暂未上报步骤/);
});
test('cancelled and timed-out jobs never retain a spinning step',()=>{
 for(const status of ['cancelled','timeout']){
  const v=view({status,execution_progress:{version:1,tasks:[{name:'A',status:'running',current_step:1,steps:[{label:'A'},{label:'B'},{label:'C'}]}]}});
  assert.equal(v.steps[2].status,'pending'); assert.notEqual(v.steps[1].status,'running');
 }
});
test('progress nodes are escaped and use symbols as well as color',()=>{
 const html=ctx.runnerProgressHtml({job_id:'j',status:'running',execution_progress:{version:1,tasks:[{name:'A',status:'running',current_step:1,steps:[{label:'<img>'},{label:'等待我的'}]}]}});
 assert.doesNotMatch(html,/<img>/); assert.match(html,/✓/); assert.match(html,/aria-label/); assert.match(html,/等待我的/);
});
test('actual sidebar renders active cards before history and pending actions',()=>{
 const start=source.indexOf('function renderJobs()');const end=source.indexOf('\nasync function postJobAction',start);
 const list={innerHTML:''};
 Object.assign(ctx,{document:{getElementById:id=>id==='jobs-list'?list:{}},activeWorkflow:'execute',latestJobs:[{job_id:'done',status:'failed'},{job_id:'queue',status:'pending'},{job_id:'active',status:'running'}],focusedJobId:'',expandedJobs:new Set(),pendingActionsVisibleLimit:3,
 normalizeJob:j=>j,isRunnerExecutionJob:()=>true,recentJobsWithFocus:j=>j,buildPendingActions:()=>[{title:'旧失败'}],pendingActionCardHtml:()=>'<div>待处理卡片</div>',pendingBatchToolbarHtml:()=>'',updateToolbarState:()=>{},renderEditorContextBar:()=>{},jobDeviceLabel:()=>'',jobRunnerLabel:()=>'',jobErrorText:()=>'',summarizeJobError:()=>'',jobReportHint:()=>'',jobKindText:()=>'',jobStatusText:s=>s,jobTimeText:j=>j.job_id,jobModeBadgeHtml:()=>'',jobActions:()=>'',jobDetailHtml:()=>''});
 vm.runInContext(source.slice(start,end),ctx);ctx.renderJobs();
 assert.ok(list.innerHTML.indexOf('active')<list.innerHTML.indexOf('queue'));
 assert.ok(list.innerHTML.indexOf('queue')<list.innerHTML.indexOf('done'));
 assert.ok(list.innerHTML.indexOf('done')<list.innerHTML.indexOf('待处理卡片'));
});
test('continue-on-error follows the currently running case instead of an earlier failure',()=>{
 const v=view({status:'running',execution_progress:{version:1,tasks:[{name:'A',status:'failed',current_step:0,steps:[{label:'失败步骤'}]},{name:'B',status:'running',current_step:1,steps:[{label:'启动'},{label:'当前等待'}]}]}});
 assert.equal(v.current,1);assert.equal(v.currentLabel,'当前等待');
});
test('truncated steps never show a misleading full progress bar',()=>{
 const job={status:'running',execution_progress:{version:1,truncated:true,tasks:[{name:'long',status:'running',current_step:220,total_steps:300,steps:Array.from({length:200},()=>({label:'步骤'}))}]}};
 assert.equal(view(job).percent,73);assert.match(view(job).currentLabel,/221/);assert.doesNotMatch(ctx.runnerProgressHtml(job),/role="progressbar"/);
});
