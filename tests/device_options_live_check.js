const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const {JSDOM} = require('../api-testing-ui/node_modules/jsdom');

function fixture(t) {
  const dom = new JSDOM('<select id="run-task-device"></select>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const w = dom.window;
  w.runnerDevices = [{runner_id: 'win', device_id: 'android', model: 'Android', usage_status: 'idle'},
    {runner_id: 'mac', device_id: 'iphone', model: 'iPhone', usage_status: 'idle'}];
  w.runnerDeviceOptionLabel = device => device.model;
  const source = fs.readFileSync('js/app.js', 'utf8');
  for (const name of ['renderDeviceOptions', 'selectedRunnerDevice']) {
    const start = source.search(new RegExp(`^function ${name}\\(`, 'm'));
    assert.notEqual(start, -1);
    const rest = source.slice(start);
    const end = rest.slice(1).search(/^(?:async )?function [\w$]+\(/m);
    w.eval(end < 0 ? rest : rest.slice(0, end + 1));
  }
  w.renderDeviceOptions('run-task-device');
  return {w, select: w.document.getElementById('run-task-device')};
}

test('unchanged background refresh preserves option nodes, selected phone and focus', t => {
  const {w, select} = fixture(t);
  select.value = 'mac::iphone';
  select.focus();
  const options = [...select.options];
  w.renderDeviceOptions('run-task-device');
  assert.equal(select.value, 'mac::iphone');
  assert.equal(w.document.activeElement, select);
  options.forEach((option, index) => assert.equal(select.options[index], option));
});

test('busy updates in place and prevents running a previously selected occupied device', t => {
  const {w, select} = fixture(t);
  select.value = 'mac::iphone';
  const selected = select.selectedOptions[0];
  w.runnerDevices[1].usage_status = 'busy';
  w.renderDeviceOptions('run-task-device');
  assert.equal(select.selectedOptions[0], selected);
  assert.equal(selected.disabled, true);
  assert.equal(w.selectedRunnerDevice('run-task-device').device_strategy, 'manual_required');
});

test('selected offline phone stays visible and disabled without selecting another phone', t => {
  const {w, select} = fixture(t);
  select.value = 'mac::iphone';
  const selected = select.selectedOptions[0];
  w.runnerDevices = [w.runnerDevices[0]];
  w.renderDeviceOptions('run-task-device');
  assert.equal(select.value, 'mac::iphone');
  assert.equal(select.selectedOptions[0], selected);
  assert.equal(selected.disabled, true);
  assert.match(selected.textContent, /离线|不可用/);
  assert.equal(w.selectedRunnerDevice('run-task-device').device_strategy, 'manual_required');
  w.runnerDevices.push({runner_id:'mac', device_id:'iphone', model:'iPhone', usage_status:'idle'});
  w.renderDeviceOptions('run-task-device');
  assert.equal(select.selectedOptions[0], selected);
  assert.equal(selected.disabled, false);
  assert.equal(w.selectedRunnerDevice('run-task-device').device_id, 'iphone');
});

test('no idle device disables automatic selection and escapes phone metadata', t => {
  const {w, select} = fixture(t);
  w.runnerDevices = [{runner_id:'mac', device_id:'iphone', model:'<img src=x onerror=alert(1)>', usage_status:'unknown'}];
  w.renderDeviceOptions('run-task-device');
  select.value = '__AUTO_DEVICE__';
  assert.equal(w.selectedRunnerDevice('run-task-device').device_strategy, 'manual_required');
  assert.equal(select.querySelector('img'), null);
});
