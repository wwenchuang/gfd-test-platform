const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('js/agent-workbench.js', 'utf8');
const definition = source.slice(source.indexOf('function isGenerateBackgroundJob('), source.indexOf('function dashboardNextItems('));
const context = { latestJobs: [
  {kind:'background',type:'mindmap_only',job_id:'interrupted',status:'failed',step:'服务重启中断',message:'服务重启中断了正在执行的后台任务，请重新发起或使用重试功能'},
  {kind:'background',type:'mindmap_only',job_id:'pending',status:'pending'},
  {kind:'background',type:'generate',job_id:'yaml',status:'failed'},
  {kind:'background',type:'apk_install',job_id:'install',status:'failed'},
  {kind:'runner',type:'mindmap_only',job_id:'runner',status:'failed'},
] };
vm.createContext(context);vm.runInContext(definition,context);
assert.equal(vm.runInContext('generationJobs().map(j=>j.job_id).join(",")',context),'interrupted,pending,yaml');
console.log('PASS: interrupted and queued mindmap jobs remain visible; unrelated jobs excluded');
