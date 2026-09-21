const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const { JSDOM } = require('../api-testing-ui/node_modules/jsdom');
function fixture(t) {
  const dom = new JSDOM('<select id="agent-runner-device"><option value="__AUTO_DEVICE__">自动</option></select><div id="agent-runner-device-cards"></div><div id="agent-runner-device-hint"></div>', {runScripts:'dangerously'});
  t.after(()=>dom.window.close());
  const w=dom.window;
  Object.assign(w,{runnerDevices:[{runner_id:'r1',device_id:'p1',model:'Phone A',android_version:'12',resolution:'1200x2640'},{runner_id:'r1',device_id:'p2',model:'Phone B'}],selectedAgentAppPackage:()=> 'app.test',runnerDeviceDisplayName:d=>d.model,runnerDeviceOptionLabel:d=>d.model,runnerDeviceVersionLabel:()=> 'app.test 1.2 (3)',appDisplayLabel:()=> '测试应用',escapeHtml:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;').replaceAll("'",'&#39;')});
  const src=fs.readFileSync('js/app.js','utf8');
  for(const name of ['renderAgentRunnerDeviceOptions','renderAgentRunnerDeviceCards','updateAgentRunnerDeviceHint','selectedRunnerDevice']) {
    const start=src.search(new RegExp(`^function ${name}\\(`,'m')); assert.notEqual(start,-1,name);
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
