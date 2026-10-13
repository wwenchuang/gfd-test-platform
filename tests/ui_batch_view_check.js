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
