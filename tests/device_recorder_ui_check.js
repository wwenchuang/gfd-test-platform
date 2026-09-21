const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const {JSDOM} = require('../api-testing-ui/node_modules/jsdom');

const ROOT = path.resolve(__dirname, '..');

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
