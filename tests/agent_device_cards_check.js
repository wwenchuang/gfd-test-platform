const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const { JSDOM } = require('../api-testing-ui/node_modules/jsdom');
function fixture(t) {
  const dom = new JSDOM('<select id="agent-runner-device"><option value="__AUTO_DEVICE__">自动</option></select><div id="agent-runner-device-cards"></div><div id="agent-runner-device-hint"></div>', {runScripts:'dangerously',url:'http://platform.local/task-manager.html'});
  t.after(()=>dom.window.close());
  const w=dom.window;
  Object.assign(w,{runnerDevices:[{runner_id:'r1',device_id:'p1',model:'Phone A',android_version:'12',resolution:'1200x2640'},{runner_id:'r1',device_id:'p2',model:'Phone B'}],selectedAgentAppPackage:()=> 'app.test',runnerDeviceDisplayName:d=>d.model,runnerDeviceOptionLabel:d=>d.model,runnerDeviceVersionLabel:()=> 'app.test 1.2 (3)',appDisplayLabel:()=> '测试应用',escapeHtml:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;').replaceAll("'",'&#39;')});
  const src=fs.readFileSync('js/app.js','utf8');
  for(const name of ['renderAgentRunnerDeviceOptions','loadAgentDeviceSnapshotImages','renderAgentRunnerDeviceCards','updateAgentRunnerDeviceHint','selectedRunnerDevice']) {
    const start=src.search(new RegExp(`^(?:async )?function ${name}\\(`,'m')); assert.notEqual(start,-1,name);
    const rest=src.slice(start), next=rest.slice(1).search(/^(?:async )?function [\w$]+\(/m);
    w.eval(next<0?rest:rest.slice(0,next+1));
  }
  return w;
}
test('phone card selection updates the existing execution contract and is exclusive',t=>{
  const w=fixture(t); w.renderAgentRunnerDeviceOptions();
  const cards=w.document.querySelectorAll('input[name="agent-device-choice"]');
  assert.equal(cards.length,3); cards[1].click();
  assert.equal(w.selectedRunnerDevice('agent-runner-device').device_id,'p1');
  cards[2].click(); assert.equal(w.selectedRunnerDevice('agent-runner-device').device_id,'p2');
  assert.equal(w.document.querySelectorAll('input:checked').length,1);
  assert.match(w.document.getElementById('agent-runner-device-cards').textContent,/1.2/);
});
test('refresh preserves a fixed choice and blocks an offline device without automatic fallback',t=>{
  const w=fixture(t); w.renderAgentRunnerDeviceOptions('r1::p1'); w.runnerDevices=[]; w.renderAgentRunnerDeviceOptions();
  assert.equal(w.document.getElementById('agent-runner-device').value,'r1::p1');
  assert.equal(w.selectedRunnerDevice('agent-runner-device').device_strategy,'manual_required');
  assert.match(w.document.getElementById('agent-runner-device-hint').textContent,/离线|不可用/);
  w.runnerDevices=[{runner_id:'r1',device_id:'p1',model:'Phone A'}]; w.renderAgentRunnerDeviceOptions();
  assert.equal(w.selectedRunnerDevice('agent-runner-device').device_id,'p1');
});
test('device metadata is text and missing versions remain explicit',t=>{
  const w=fixture(t); w.runnerDevices[0].model='<img src=x onerror=alert(1)>'; w.runnerDeviceVersionLabel=()=>'';
  w.renderAgentRunnerDeviceOptions();
  assert.equal(w.document.querySelector('img'),null);
  assert.match(w.document.getElementById('agent-runner-device-cards').textContent,/未上报/);
});
test('live screenshot and current usage are shown without guessing occupation',t=>{
  const w=fixture(t);
  Object.assign(w.runnerDevices[0], {snapshot_url:'/api/runner/device-snapshot?runner_id=r1&device_id=p1',snapshot_captured_at:'2026-09-21 14:00:00',battery_level:80,battery_temperature_c:31.2,foreground_package:'com.kfb.model',screen_on:true,usage_status:'busy',usage_label:'平台任务执行中'});
  w.renderAgentRunnerDeviceOptions('r1::p1');
  const card=w.document.querySelector('.agent-phone-card');
  assert.equal(card.querySelector('img').dataset.snapshotUrl.startsWith('/api/runner/device-snapshot'),true);
  assert.match(card.textContent,/平台任务执行中/);
  assert.match(card.textContent,/80%/);
  assert.equal(card.querySelector('input').disabled,true);
});
test('protected screenshots are fetched with session auth instead of a bare image request',async t=>{
  const w=fixture(t); const calls=[];
  w.nativeFetch=async(url,options)=>{calls.push({url,options});return {ok:true,blob:async()=>({type:'image/png'})};};
  w.authHeaders=()=>({get:name=>name==='Authorization'?'Bearer session-test':null});
  w.URL.createObjectURL=()=> 'blob:device-picture'; w.URL.revokeObjectURL=()=>{};
  const host=w.document.getElementById('agent-runner-device-cards');
  host.innerHTML='<img data-snapshot-url="/api/runner/device-snapshot?runner_id=r1&amp;device_id=p1" hidden><span>读取中</span>';
  await w.loadAgentDeviceSnapshotImages(host);
  await w.loadAgentDeviceSnapshotImages(host);
  assert.equal(calls.length,1);
  assert.equal(calls[0].options.headers.get('Authorization'),'Bearer session-test');
  assert.equal(host.querySelector('img').src,'blob:device-picture');
});
test('unknown phone state cannot be selected or automatically assigned',t=>{
  const w=fixture(t); w.runnerDevices.forEach(device=>Object.assign(device,{usage_status:'unknown',usage_label:'状态待确认'}));
  w.renderAgentRunnerDeviceOptions();
  assert.equal(w.document.querySelector('input[value="__AUTO_DEVICE__"]').disabled,true);
  assert.equal(w.document.querySelectorAll('.agent-phone-card input:disabled').length,2);
});
