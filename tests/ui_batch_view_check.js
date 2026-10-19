// Run: node --test tests/ui_batch_view_check.js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM} = require('../api-testing-ui/node_modules/jsdom');
function fixture(t) {
  const dom = new JSDOM('<main id="view"></main>');
  t.after(()=>dom.window.close());
  vm.runInContext(fs.readFileSync('js/ui-batch-view.js','utf8'), vm.createContext({window:dom.window, document:dom.window.document}));
  return {node:dom.window.document.querySelector('main'), view:dom.window.UiBatchView};
}
function batch() {
  return {ui_batch_id:'uib_fixture',app_id:'com.fixture.app',owner_display_name:'执行者乙',status:'running',report_status:'pending',delivery_status:'not_requested',
    snapshots:[{case_id:'c1',metadata:{task_name:'我的 → 首页'}}],case_attempts:[{attempt_id:'a1',case_id:'c1',device_id:'iphone',platform:'ios',status:'running',attempt_no:1,
      target_resolution:{device_snapshot:{label:'iPhone 13'}},step_progress_known:true,
      steps:[{step_id:'s1',label:'打开应用',status:'passed'},{step_id:'s2',label:'点击我的',status:'running'},{step_id:'s3',label:'检查我的页面',status:'pending'}]}]};
}
test('print recovery exposes attempts and prior failures without HTML injection',t=>{
 const f=fixture(t),b=batch();b.case_attempts[0].execution_progress={tasks:[{print_recovery:{status:'passed',attempts:3,recovered:true,elapsed_ms:48000,history:[{elapsed_ms:10000,attempts:1,state:'暂时失败 <img src=x>'}]}}]};
 f.view.update(f.node,b);assert.match(f.node.textContent,/重试后取消成功.*尝试 3 次.*耗时 48 秒/);
 const details=f.node.querySelector('[data-print-recovery-history]');details.open=true;assert.match(details.textContent,/暂时失败/);assert.equal(details.querySelector('img'),null);
 f.view.update(f.node,b);assert.equal(details.open,true);
 delete b.case_attempts[0].execution_progress;f.view.update(f.node,b);assert.equal(f.node.querySelector('[data-print-recovery-history]'),null);
});
test('normal running batch does not claim it is waiting to stop', t=>{
  const f=fixture(t), b=batch();b.stop_pending=true;b.execution_settled=false;
  f.view.update(f.node,b);
  assert.match(f.node.querySelector('[data-batch-hint]').textContent,/真实上报/);
  assert.doesNotMatch(f.node.querySelector('[data-batch-hint]').textContent,/等待.*停止/);
  b.status='cancelling';f.view.update(f.node,b);
  assert.match(f.node.querySelector('[data-batch-hint]').textContent,/已请求取消/);
  b.status='passed';f.view.update(f.node,b);
  assert.match(f.node.querySelector('[data-batch-hint]').textContent,/等待手机确认停止/);
});
test('ordinary settled test keeps real results and exposes evidence without a report action',t=>{
 const f=fixture(t),b=batch();b.status='failed';b.execution_settled=true;b.report_status='not_requested';b.case_attempts[0].status='failed';
 f.view.update(f.node,b,{report:()=>{}});
 assert.equal(f.node.querySelector('[data-batch-action="generateReport"]'),null);
 assert.match(f.node.querySelector('[data-batch-action="report"]').textContent,/截图|证据/);
 assert.match(f.node.querySelector('[data-batch-report]').textContent,/普通测试，未申请发版报告/);
 assert.equal(f.node.querySelectorAll('[data-step-state]').length,3);
 b.execution_settled=false;b.stop_pending=true;f.view.update(f.node,b,{report:()=>{}});
 assert.equal(f.node.querySelector('[data-batch-action="report"]').hidden,true);
});
test('shows real per-step states and keeps unfinished steps grey after failure', t=>{
  const f=fixture(t), b=batch();
  f.view.update(f.node,b);
  assert.deepEqual([...f.node.querySelectorAll('[data-step-state]')].map(n=>n.dataset.stepState),['passed','running','pending']);
  b.status='failed';b.case_attempts[0].status='failed';b.case_attempts[0].steps[1].status='failed';
  f.view.update(f.node,b);
  assert.deepEqual([...f.node.querySelectorAll('[data-step-state]')].map(n=>n.dataset.stepState),['passed','failed','pending']);
  assert.match(f.node.textContent,/执行者乙/);
});
test('overall success with missing step evidence does not fabricate green steps', t=>{
  const f=fixture(t), b=batch();b.status='success';b.case_attempts[0].status='success';b.case_attempts[0].step_progress_known=false;
  f.view.update(f.node,b);
  assert.equal(f.node.querySelectorAll('[data-step-state="passed"]').length,0);
  assert.match(f.node.textContent,/仅用例级进度/);
});
test('suite title does not hide the application identity', t=>{
  const f=fixture(t), b=batch();b.suite_name='导航基线';b.app_name='智小白3D';f.view.update(f.node,b);
  assert.equal(f.node.querySelector('[data-batch-title]').textContent,'导航基线');
  assert.equal(f.node.querySelector('[data-batch-app]').textContent,'智小白3D');
});
test('batch heading uses frozen business and application names instead of repeated package ids', t=>{
  const f=fixture(t), b=batch();b.app_id='com.kfb.model';b.app_name='后续修改的名称';
  b.snapshots[0].metadata={task_name:'智小白3D跨端导航验收',app_name:'智小白3D'};
  f.view.update(f.node,b);
  assert.equal(f.node.querySelector('[data-batch-title]').textContent,'智小白3D跨端导航验收');
  assert.equal(f.node.querySelector('[data-batch-app]').textContent,'智小白3D');
  assert.doesNotMatch(f.node.querySelector('.ui-batch-head').textContent,/com\.kfb\.model|后续修改的名称/);
});
test('multiple cases get a batch heading rather than the first case name', t=>{
  const f=fixture(t), b=batch();b.snapshots[0].metadata.app_name='智小白3D';
  b.snapshots.push({case_id:'c2',metadata:{task_name:'第二个用例',app_name:'智小白3D'}});
  f.view.update(f.node,b);
  assert.equal(f.node.querySelector('[data-batch-title]').textContent,'UI 测试');
  assert.equal(f.node.querySelector('[data-batch-app]').textContent,'智小白3D');
});
test('missing frozen names keep the package identity only in the subtitle', t=>{
  const f=fixture(t), b=batch();b.snapshots=[];f.view.update(f.node,b);
  assert.equal(f.node.querySelector('[data-batch-title]').textContent,'UI 测试');
  assert.equal(f.node.querySelector('[data-batch-app]').textContent,'com.fixture.app');
});
test('unconfirmed interruption is not finished and retains the stop action',t=>{
  const f=fixture(t), b=batch();b.status='interrupted';b.execution_settled=false;b.stop_pending=true;
  b.case_attempts[0].status='interrupted';b.case_attempts[0].execution_settled=false;
  f.view.update(f.node,b,{cancel:()=>{},rerun:()=>{}});
  assert.match(f.node.querySelector('[data-batch-count]').textContent,/0 \/ 1/);
  assert.equal(f.node.querySelector('[data-batch-action="cancel"]').hidden,false);
  assert.equal(f.node.querySelector('[data-batch-action="rerun"]').hidden,true);
  assert.match(f.node.querySelector('[data-batch-hint]').textContent,/停止/);
});
test('refresh preserves details node, expansion and keyboard focus', t=>{
  const f=fixture(t), b=batch();f.view.update(f.node,b);
  const details=f.node.querySelector('details'), summary=details.querySelector('summary');details.open=true;summary.focus();
  b.case_attempts[0].steps[1].status='passed';
  f.view.update(f.node,b);
  assert.equal(f.node.querySelector('details'),details);assert.equal(details.open,true);
  assert.equal(f.node.ownerDocument.activeElement,summary);
});
test('running attempts sort above queued and finished attempts with no lost identity', t=>{
  const f=fixture(t), b=batch(), running=b.case_attempts[0];
  b.case_attempts=[{...running,attempt_id:'done',status:'passed'},{...running,attempt_id:'queued',status:'queued'},running];
  f.view.update(f.node,b);
  assert.deepEqual([...f.node.querySelectorAll('[data-attempt-id]')].map(n=>n.dataset.attemptId),['a1','queued','done']);
});
test('sorting a newly running attempt preserves focus and expanded details', t=>{
  const f=fixture(t), b=batch();
  b.case_attempts.push({...b.case_attempts[0],attempt_id:'a2',status:'queued'});
  f.view.update(f.node,b);
  const details=f.node.querySelector('[data-attempt-id="a2"]'), summary=details.querySelector('summary');
  details.open=true;summary.focus();b.case_attempts[0].status='passed';b.case_attempts[1].status='running';
  f.view.update(f.node,b);
  assert.equal(f.node.querySelector('details'),details);assert.equal(details.open,true);
  assert.equal(f.node.ownerDocument.activeElement,summary);
});
test('an unknown device platform is not labelled Android', t=>{
  const f=fixture(t), b=batch();delete b.case_attempts[0].platform;f.view.update(f.node,b);
  assert.match(f.node.querySelector('[data-attempt-device]').textContent,/平台待确认/);
  assert.doesNotMatch(f.node.querySelector('[data-attempt-device]').textContent,/Android/);
});
test('untrusted labels and block messages render as text and never HTML', t=>{
  const f=fixture(t), b=batch();b.owner_display_name='<img src=x onerror=alert(1)>';b.snapshots[0].metadata.task_name='<script>bad()</script>';
  b.case_attempts[0].block_message='<svg onload=bad()>blocked</svg>';
  f.view.update(f.node,b);
  assert.equal(f.node.querySelector('img,script,svg'),null);
  assert.match(f.node.textContent,/<script>bad/);
});
test('cancel action receives batch id and is disabled until the request settles', async t=>{
  const f=fixture(t), b=batch();let resolve, calls=[];const waiting=new Promise(r=>resolve=r);
  f.view.update(f.node,b,{cancel:id=>{calls.push(id);return waiting;}});
  const button=f.node.querySelector('[data-batch-action="cancel"]');button.click();button.click();
  assert.deepEqual(calls,['uib_fixture']);assert.equal(button.disabled,true);
  resolve();await new Promise(setImmediate);assert.equal(button.disabled,false);
});
test('switching batches clears previous identities and action state', t=>{
  const f=fixture(t), b=batch();f.view.update(f.node,b,{cancel:()=>{}});
  b.ui_batch_id='uib_other';b.owner_display_name='执行者丙';b.case_attempts=[];
  f.view.update(f.node,b);
  assert.doesNotMatch(f.node.textContent,/执行者乙/);assert.equal(f.node.querySelectorAll('[data-attempt-id]').length,0);
  assert.equal(f.node.querySelector('[data-batch-action="cancel"]').hidden,true);
});
function recoverable(){const b=batch();b.status='interrupted';b.stop_pending=true;b.execution_settled=false;Object.assign(b.case_attempts[0],{status:'interrupted',job_id:'job1',event_sequence:3,manual_stop_recovery_version:'f'.repeat(64),manual_stop_recovery_eligible:true});return b;}
test('manual recovery requires unchecked confirmation and cancel or Escape sends no action',async t=>{
 const f=fixture(t),b=recoverable(),calls=[];f.view.update(f.node,b,{recover:(...args)=>calls.push(args)});
 const button=f.node.querySelector('[data-attempt-recover]');assert.ok(button);button.click();
 let form=f.node.querySelector('[data-stop-confirmation]');assert.ok(form);assert.match(form.textContent,/iPhone 13/);assert.match(form.textContent,/com.fixture.app/);assert.match(form.textContent,/关闭浏览器/);
 assert.equal(form.querySelector('input').checked,false);form.querySelector('[data-stop-submit]').click();assert.equal(calls.length,0);
 form.querySelector('[data-stop-dismiss]').click();assert.equal(calls.length,0);assert.equal(f.node.ownerDocument.activeElement,button);
 button.click();form=f.node.querySelector('[data-stop-confirmation]');form.dispatchEvent(new f.node.ownerDocument.defaultView.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(calls.length,0);assert.equal(form.hidden,true);
});
test('confirmed recovery sends frozen exact attempt and discards changed state',async t=>{
 const f=fixture(t),b=recoverable(),calls=[];f.view.update(f.node,b,{recover:(...args)=>calls.push(args)});
 f.node.querySelector('[data-attempt-recover]').click();let form=f.node.querySelector('[data-stop-confirmation]');form.querySelector('input').click();form.querySelector('[data-stop-submit]').click();await new Promise(setImmediate);
 assert.equal(calls.length,1);assert.equal(calls[0][0],'uib_fixture');assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1])),{attempt_id:'a1',expected_status:'interrupted',expected_event_sequence:3,expected_recovery_version:'f'.repeat(64),confirmed_stopped:true,reason:'已在执行机确认该任务及其子进程已结束'});
 f.node.querySelector('[data-attempt-recover]').click();b.case_attempts[0].event_sequence++;f.view.update(f.node,b,{recover:(...args)=>calls.push(args)});assert.equal(f.node.querySelector('[data-stop-confirmation]').hidden,true);assert.equal(calls.length,1);
});
test('viewers have no recovery action and manual provenance is labelled',t=>{
 const f=fixture(t),b=recoverable();f.view.update(f.node,b);assert.equal(f.node.querySelector('[data-attempt-recover]').hidden,true);
 b.case_attempts[0].manual_stop_recovery={actor_display_name:'核对者',confirmed_ns:123,reason:'已核对'};f.view.update(f.node,b);assert.match(f.node.textContent,/人工确认停止/);assert.match(f.node.textContent,/核对者/);
});
test('recovery confirmation retains focus under unchanged polling and blocks account changes',async t=>{
 const f=fixture(t),b=recoverable(),calls=[];let current=true;const actions={isCurrent:()=>current,recover:(...args)=>calls.push(args)};
 f.view.update(f.node,b,actions);f.node.querySelector('[data-attempt-recover]').click();const panel=f.node.querySelector('[data-stop-confirmation]'),input=panel.querySelector('input');input.click();
 f.view.update(f.node,b,actions);assert.equal(panel.hidden,false);assert.equal(f.node.ownerDocument.activeElement,input);assert.equal(input.checked,true);
 current=false;panel.querySelector('[data-stop-submit]').click();await new Promise(setImmediate);assert.equal(calls.length,0);
 f.view.update(f.node,b,actions);assert.equal(panel.hidden,true);assert.equal(f.node.querySelector('[data-attempt-recover]').hidden,true);
});
test('recovery busy guard sends one request and discards an old view error',async t=>{
 const f=fixture(t),b=recoverable(),calls=[];let reject;
 f.view.update(f.node,b,{recover:(...args)=>{calls.push(args);return new Promise((_,r)=>reject=r);}});
 f.node.querySelector('[data-attempt-recover]').click();const panel=f.node.querySelector('[data-stop-confirmation]');panel.querySelector('input').click();panel.querySelector('[data-stop-submit]').click();panel.querySelector('[data-stop-submit]').click();
 assert.equal(calls.length,1);assert.equal(panel.querySelector('[data-stop-submit]').disabled,true);
 f.view.update(f.node,{...batch(),ui_batch_id:'uib_other'});reject(new Error('旧账号私密错误'));await new Promise(setImmediate);assert.doesNotMatch(f.node.textContent,/旧账号私密错误/);
});
test('polling invalidates confirmation without taking focus from another control',t=>{
 const f=fixture(t),b=recoverable();f.view.update(f.node,b,{recover:()=>{}});f.node.querySelector('[data-attempt-recover]').click();
 const other=f.node.querySelector('[data-batch-action="cancel"]');other.hidden=false;other.focus();b.case_attempts[0].event_sequence++;
 f.view.update(f.node,b,{recover:()=>{},cancel:()=>{}});assert.equal(f.node.ownerDocument.activeElement,other);
});

test('settled execution exposes replay in either result host without formal report',t=>{const f=fixture(t),b=batch();b.status='passed';b.execution_settled=true;b.report_status='not_requested';f.view.update(f.node,b,{replay:()=>{}});assert.equal(f.node.querySelector('[data-batch-action="replay"]').hidden,false);b.execution_settled=false;f.view.update(f.node,b,{replay:()=>{}});assert.equal(f.node.querySelector('[data-batch-action="replay"]').hidden,true);});
