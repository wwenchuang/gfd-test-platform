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
    sessionStorage: dom.window.sessionStorage, URL, taskApps: [{name: '微信', package: 'com.tencent.mm', enabled: true}],
    runnerDevices: [{runner_id: 'win-runner-01', device_id: '9888E0094F2A', status: 'online'}],
    escapeHtml: value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;'),
    resetYamlToolbarForManager() {}, loadModules: async () => {}, loadRunnerDevices: async () => {},
    showToast() {}, prompt: () => '', clearInterval() {}, setInterval: () => 1, setTimeout() {},
    apiRequest: async (url, options = {}) => {
      calls.push({url, body: options.body ? JSON.parse(options.body) : null});
      return {session: {id: 'session-1', status: 'recording', recording_token: 'secret', steps: []}, sonic_url: 'http://sonic.example/Index/Devices'};
    },
  });
  context.window.open = url => { const ref = {url, closed: false, postMessage() {}}; opened.push(ref); return ref; };
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'js/device-recorder.js'), 'utf8'), context);
  return {context, dom, calls, opened, run: code => vm.runInContext(code, context)};
}

test('renders fixed device and starts recording without putting token in Sonic URL', async () => {
  const f = fixture();
  f.run('renderDeviceRecorder()');
  assert.match(f.dom.window.document.getElementById('editor-area').textContent, /9888E0094F2A/);
  await f.run('startDeviceRecording()');
  assert.equal(f.calls[0].body.device_id, '9888E0094F2A');
  assert.equal(f.calls[0].body.app_package, 'com.tencent.mm');
  assert.equal(f.opened[0].url, 'http://sonic.example/Index/Devices');
  assert.doesNotMatch(f.opened[0].url, /secret|session-1/);
});

test('blocks start when the fixed device is absent', () => {
  const f = fixture();
  f.run('runnerDevices = []');
  f.run('renderDeviceRecorder()');
  const button = f.dom.window.document.querySelector('button[onclick="startDeviceRecording()"]');
  assert.equal(button.disabled, true);
  assert.match(f.dom.window.document.getElementById('editor-area').textContent, /不能开始录制/);
});
