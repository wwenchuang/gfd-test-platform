const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const {JSDOM} = require('../api-testing-ui/node_modules/jsdom');

const ROOT = path.resolve(__dirname, '..');

test('task manager uses a new cache key for the ready-handshake recorder script', () => {
  const html = fs.readFileSync(path.join(ROOT, 'task-manager.html'), 'utf8');
  assert.match(html, /device-recorder\.js\?v=20260922-sonic-native-recorder-v13/);
});

function fixture() {
  const dom = new JSDOM('<div id="editor-area"></div>', {url: 'http://platform.example/task-manager.html'});
  const calls = [];
  const opened = [];
  const context = vm.createContext({
    window: dom.window, document: dom.window.document, location: dom.window.location,
    sessionStorage: dom.window.sessionStorage, URL, taskApps: [{name: '微信', package: 'com.tencent.mm', enabled: true, modules: ['微信登录']}],
    modules: {'微信登录': []},
    runnerDevices: [{runner_id: 'win-runner-01', device_id: 'ecbfd645', model: 'OPPO Reno9', status: 'online', usage_status: 'idle'}],
    escapeHtml: value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;'),
    resetYamlToolbarForManager() {}, loadModules: async () => {}, loadRunnerDevices: async () => {},
    showToast() {}, prompt: () => '', clearInterval() {}, setInterval: () => 1, setTimeout() {},
    apiRequest: async (url, options = {}) => {
      calls.push({url, body: options.body ? JSON.parse(options.body) : null});
      return {session: {id: 'session-1', status: 'recording', recording_token: 'secret', runner_id: '', device_id: '', steps: []}, sonic_url: 'http://sonic.example/Index/Devices'};
    },
  });
  context.window.open = url => { const ref = {url, closed: false, postMessage() {}}; opened.push(ref); return ref; };
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'js/device-recorder.js'), 'utf8'), context);
  return {context, dom, calls, opened, run: code => vm.runInContext(code, context)};
}

test('opens Sonic for the only phone selection and does not put token in its URL', async () => {
  const f = fixture();
  f.run('renderDeviceRecorder()');
  assert.match(f.dom.window.document.getElementById('editor-area').textContent, /OPPO Reno9/);
  await f.run('startDeviceRecording()');
  assert.equal(f.calls[0].body.device_id, undefined);
  assert.equal(f.calls[0].body.app_package, 'com.tencent.mm');
  assert.equal(f.opened[0].url, 'http://sonic.example/Index/Devices');
  assert.doesNotMatch(f.opened[0].url, /secret|session-1/);
});

test('shows an empty state when no Android phone is online', async () => {
  const f = fixture();
  f.run('runnerDevices = []');
  f.run('renderDeviceRecorder()');
  assert.match(f.dom.window.document.getElementById('editor-area').textContent, /暂无在线 Android 手机/);
  assert.equal(f.dom.window.document.querySelector('[data-action="start-recording"]').disabled, true);
});

test('never offers business printer identifiers as recording phones', () => {
  const f = fixture();
  f.run("runnerDevices = [{runner_id:'win-runner-01',device_id:'9888E0094F2A',status:'online'}]");
  f.run('renderDeviceRecorder()');
  assert.doesNotMatch(f.dom.window.document.getElementById('editor-area').textContent, /9888E0094F2A/);
});

test('shows device health colors and explains that app and phone are independent', () => {
  const f = fixture();
  f.run('renderDeviceRecorder()');
  assert.ok(f.dom.window.document.querySelector('.device-recorder-phone-status.idle'));
  assert.match(f.dom.window.document.getElementById('editor-area').textContent, /应用用于生成启动步骤，与手机分配互不绑定/);
  assert.equal(f.dom.window.document.querySelectorAll('input[name="device-recorder-device"]').length, 0);
});

test('keeps the recorded application selected after the session starts', () => {
  const f = fixture();
  f.run("taskApps.push({name:'智小白3D',package:'com.kfb.model',enabled:true,modules:['3D打印基线']}); deviceRecorderSession={id:'s1',status:'recording',app_package:'com.kfb.model',device_id:'ecbfd645',steps:[]}; renderDeviceRecorder()");
  assert.equal(f.dom.window.document.getElementById('device-recorder-app').value, 'com.kfb.model');
});

test('history can reopen a persisted generated YAML and start a new recording', () => {
  const f = fixture();
  f.run("deviceRecorderHistory=[{id:'old',status:'finished',device_id:'phone',finished_at:'2026-09-22 12:00:00',steps:[{id:'x'}],generated_result:{yaml:'tasks: []'}}]; deviceRecorderSession=deviceRecorderHistory[0]; deviceRecorderGenerated=deviceRecorderHistory[0].generated_result; renderDeviceRecorder()");
  assert.match(f.dom.window.document.body.textContent, /录制记录（1）/);
  assert.match(f.dom.window.document.getElementById('device-recorder-yaml').textContent, /tasks: \[\]/);
  f.run('newDeviceRecording()');
  assert.match(f.dom.window.document.body.textContent, /尚未开始/);
});

test('blocks empty generation and keeps case name linked to YAML filename', () => {
  const f = fixture();
  f.run("deviceRecorderSession = {id:'session-1',status:'finished',runner_id:'win-runner-01',device_id:'ecbfd645',app_package:'com.tencent.mm',steps:[]}; renderDeviceRecorder()");
  assert.equal(f.dom.window.document.querySelector('[data-action="generate-recording-yaml"]').disabled, true);
  const name = f.dom.window.document.getElementById('device-recorder-task-name');
  name.value = '微信登录';
  f.run("syncRecorderFileName('微信登录')");
  assert.equal(f.dom.window.document.getElementById('device-recorder-file').value, '微信登录.yaml');
  assert.equal(f.dom.window.document.getElementById('device-recorder-module').value, '微信登录');
});

test('waits for automatic evidence recognition before enabling YAML generation', () => {
  const f = fixture();
  f.run("deviceRecorderSession = {id:'session-1',status:'finished',runner_id:'win-runner-01',device_id:'ecbfd645',app_package:'com.tencent.mm',steps:[{id:'s1',sequence:1,type:'tap',point:{x:1,y:2},evidence_status:'pending'}]}; renderDeviceRecorder()");
  const button = f.dom.window.document.querySelector('[data-action="generate-recording-yaml"]');
  assert.equal(button.disabled, true);
  assert.match(button.textContent, /等待自动识别/);
});

test('timeline exposes unobtrusive edit and delete controls', () => {
  const f = fixture();
  f.run("deviceRecorderSession = {id:'session-1',status:'finished',runner_id:'win-runner-01',device_id:'ecbfd645',app_package:'com.tencent.mm',steps:[{id:'s1',sequence:1,type:'key',key:'BACK',semantic_description:'返回上一页'}]}; renderDeviceRecorder()");
  const details = f.dom.window.document.querySelector('.device-recorder-step-tools');
  assert.ok(details);
  assert.match(details.textContent, /编辑或删除/);
  assert.ok(details.querySelector('[data-action="delete-recording-step"]'));
});

test('accepts the ready message from the Sonic remote child tab and binds back to that tab', async () => {
  const f = fixture();
  f.run('renderDeviceRecorder()');
  await f.run('startDeviceRecording()');
  const replies = [];
  const remoteTab = {postMessage(message, origin) { replies.push({message, origin}); }};
  f.context.remoteTab = remoteTab;
  await f.run("handleDeviceRecorderMessage({origin:'http://sonic.example',source:remoteTab,data:{type:'MIDSCENE_RECORDING_READY',sessionId:'session-1',deviceId:'ecbfd645'}})");
  const bind = f.calls.find(call => call.url === '/device-recordings/bind');
  assert.deepEqual(bind.body, {session_id: 'session-1', device_id: 'ecbfd645'});
  assert.equal(replies[0].message.type, 'MIDSCENE_RECORDING_BOUND');
  assert.equal(replies[0].origin, 'http://sonic.example');
});

test('resends the recording session when a freshly loaded Sonic page announces its hook', async () => {
  const f = fixture();
  f.run('renderDeviceRecorder()');
  await f.run('startDeviceRecording()');
  const messages = [];
  const sonicTab = {closed: false, postMessage(message, origin) { messages.push({message, origin}); }};
  f.context.sonicTab = sonicTab;

  await f.run("handleDeviceRecorderMessage({origin:'http://sonic.example',source:sonicTab,data:{type:'MIDSCENE_RECORDER_HOOK_READY'}})");

  assert.equal(messages[0].message.type, 'MIDSCENE_RECORDING_START');
  assert.equal(messages[0].message.sessionId, 'session-1');
  assert.equal(messages[0].origin, 'http://sonic.example');
});
