const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const {JSDOM} = require('../api-testing-ui/node_modules/jsdom');

const ROOT = path.resolve(__dirname, '..');

test('task manager uses a new cache key for the server-bridged recorder script', () => {
  const html = fs.readFileSync(path.join(ROOT, 'task-manager.html'), 'utf8');
  assert.match(html, /device-recorder\.js\?v=20260924-recorder-live-v32/);
});

test('history groups by app and module and filter changes clear hidden selections', () => {
  const f = fixture();
  f.run(`deviceRecorderHistory=[
    {id:'a',status:'finished',app_package:'com.kfb.model',module_name:'3D打印基线',step_count:3},
    {id:'b',status:'cancelled',app_package:'com.kfb.model',step_count:1},
    {id:'c',status:'recording',app_package:'com.tencent.mm',module_name:'微信登录',step_count:2}
  ]; showDeviceRecordingHistory()`);
  assert.equal(f.dom.window.document.querySelectorAll('[data-history-app-group]').length, 2);
  assert.match(f.dom.window.document.body.textContent, /未分组/);
  f.run("setRecorderHistoryFilter('app','com.kfb.model'); toggleRecorderHistoryAll(true)");
  assert.deepEqual(Array.from(f.run('deviceRecorderHistorySelected')), ['a','b']);
  f.run("setRecorderHistoryFilter('module','3D打印基线')");
  assert.equal(f.run('deviceRecorderHistorySelected.size'), 0);
  assert.equal(f.dom.window.document.querySelectorAll('.device-recorder-history-entry').length, 1);
  assert.match(f.dom.window.document.body.textContent, /4 步/);
});

test('batch deletion retains failures and excludes active and hidden records', async () => {
  const f = fixture();
  const deleted = [];
  const notices = [];
  f.context.confirm = () => true;
  f.context.showToast = (message,kind) => notices.push({message,kind});
  f.run(`deviceRecorderHistory=[{id:'a',status:'finished',app_package:'com.kfb.model'},
    {id:'b',status:'cancelled',app_package:'com.kfb.model'},
    {id:'live',status:'recording',app_package:'com.kfb.model'},
    {id:'other',status:'finished',app_package:'com.tencent.mm'}]; showDeviceRecordingHistory();
    setRecorderHistoryFilter('app','com.kfb.model'); toggleRecorderHistoryAll(true)`);
  f.context.apiRequest = async (url, options={}) => {
    if (options.method === 'DELETE') {
      const id = new URL(url, 'http://test').searchParams.get('id'); deleted.push(id);
      if (id === 'b') throw new Error('删除失败，请重试');
      return {ok:true};
    }
    return {sessions:Array.from(f.run('deviceRecorderHistory')).filter(x => x.id !== 'a')};
  };
  await f.run('deleteSelectedDeviceRecordings()');
  assert.deepEqual(deleted,['a','b']);
  assert.deepEqual(Array.from(f.run('deviceRecorderHistorySelected')),['b']);
  assert.match(notices.at(-1).message,/已删除 1.*失败 1/);
  assert.ok(f.dom.window.document.querySelector('[data-recording-select="live"]').disabled);
});

test('canceling bulk confirmation does not send delete requests', async () => {
  const f = fixture(); f.context.confirm=()=>false;
  f.run("deviceRecorderHistory=[{id:'a',status:'finished'}];showDeviceRecordingHistory();toggleRecorderHistoryAll(true)");
  await f.run('deleteSelectedDeviceRecordings()');
  assert.equal(f.calls.length,0);
});

test('history loads later summary pages instead of silently limiting to thirty', async () => {
  const f = fixture(); const urls=[];
  f.context.apiRequest = async url => {
    urls.push(url);
    return url.includes('offset=100') ? {sessions:[{id:'last'}],next_offset:null}
      : {sessions:[{id:'first'}],next_offset:100};
  };
  await f.run('loadDeviceRecordingHistory(false)');
  assert.equal(urls.length,2);
  assert.deepEqual(Array.from(f.run('deviceRecorderHistory'),x=>x.id),['first','last']);
});

test('old records can persist a module and new recordings submit their selected module', async () => {
  const f = fixture();
  f.run("deviceRecorderSession={id:'old',status:'cancelled',app_package:'com.kfb.model',steps:[]};renderDeviceRecorder()");
  assert.ok(f.dom.window.document.getElementById('device-recorder-group-module'));
  f.context.apiRequest = async (url, options) => {
    assert.equal(url, '/device-recordings/module');
    assert.equal(JSON.parse(options.body).module_name,'3D打印基线');
    return {session:{id:'old',status:'cancelled',app_package:'com.kfb.model',module_name:'3D打印基线',steps:[]}};
  };
  await f.run("updateRecorderModule('3D打印基线')");
  assert.equal(f.run('deviceRecorderSession.module_name'),'3D打印基线');
  assert.equal(f.dom.window.document.getElementById('device-recorder-group-module').value,'3D打印基线');
  const fresh = fixture();
  fresh.run('renderDeviceRecorder()');
  fresh.dom.window.document.getElementById('device-recorder-group-module').value = '3D打印基线';
  await fresh.run('startDeviceRecording()');
  assert.equal(fresh.calls[0].body.module_name,'3D打印基线');
});

test('manual re-recognition reports the actual result and prevents duplicate requests', async () => {
  const f = fixture();
  const notices = [];
  let requests = 0;
  let release;
  f.context.showToast = (message, kind) => notices.push({message, kind});
  f.context.apiRequest = async () => {
    requests += 1;
    await new Promise(resolve => { release = resolve; });
    return {session:{id:'s1',status:'finished',app_package:'com.kfb.model',steps:[{id:'tap-1',sequence:1,type:'tap',point:{x:10,y:20},screenshot_path:'/tmp/one.png',semantic_description:'我的',semantic_source:'ui_xml',semantic_recognition_status:'recognized',evidence_status:'captured'}]}};
  };
  f.run("deviceRecorderSession={id:'s1',status:'finished',app_package:'com.kfb.model',steps:[{id:'tap-1',sequence:1,type:'tap',point:{x:10,y:20},screenshot_path:'/tmp/one.png',semantic_description:'AI建模',evidence_status:'captured'}]}; renderDeviceRecorder()");
  const pending = f.run("retryRecorderRecognition('tap-1')");
  const duplicate = f.run("retryRecorderRecognition('tap-1')");
  assert.equal(requests, 1);
  release();
  await Promise.all([pending, duplicate]);
  assert.match(notices.at(-1).message, /我的/);
  assert.equal(notices.at(-1).kind, 'success');
});

test('a recovered Sonic bridge clears only its own stale error for the active session', async () => {
  const f = fixture();
  f.run("deviceRecorderSession={id:'s1',status:'recording',device_id:'ecbfd645',app_package:'com.kfb.model',steps:[]}; sessionStorage.setItem('deviceRecorderSonicUrl','http://sonic.example/Index/Devices'); renderDeviceRecorder()");
  await f.run("handleDeviceRecorderMessage({origin:'http://sonic.example',data:{type:'MIDSCENE_RECORDING_ERROR',sessionId:'s1',source:'bridge',message:'Failed to fetch'}})");
  assert.match(f.dom.window.document.getElementById('device-recorder-message').textContent, /Failed to fetch/);
  await f.run("handleDeviceRecorderMessage({origin:'http://sonic.example',data:{type:'MIDSCENE_RECORDING_BRIDGE_RECOVERED',sessionId:'old'}})");
  assert.match(f.dom.window.document.getElementById('device-recorder-message').textContent, /Failed to fetch/);
  await f.run("handleDeviceRecorderMessage({origin:'http://sonic.example',data:{type:'MIDSCENE_RECORDING_BRIDGE_RECOVERED',sessionId:'s1'}})");
  assert.doesNotMatch(f.dom.window.document.getElementById('device-recorder-message').textContent, /Failed to fetch/);
  assert.match(f.dom.window.document.getElementById('device-recorder-message').textContent, /已恢复/);
});

test('step screenshot survives timeline rerenders without repeated downloads', async () => {
  const f = fixture();
  let downloads = 0;
  f.context.authHeaders = () => ({Authorization: 'Bearer test'});
  f.context.CSS = {escape: value => value};
  f.context.fetch = async () => { downloads += 1; return {ok: true, blob: async () => new Blob(['png'])}; };
  f.context.URL = class extends URL { static createObjectURL() { return 'blob:recording-frame'; } };
  f.run("deviceRecorderSession={id:'s1',status:'recording',app_package:'com.kfb.model',steps:[{id:'tap-1',sequence:1,type:'tap',point:{x:10,y:20},screenshot_path:'/tmp/one.png',evidence_status:'captured'}]}; renderDeviceRecorder()");
  await f.run("loadRecorderEvidence('tap-1')");
  f.run('renderDeviceRecorder()');
  await f.run("loadRecorderEvidence('tap-1')");
  assert.equal(downloads, 1);
  assert.equal(f.dom.window.document.querySelector('[data-recorder-evidence="tap-1"]').src, 'blob:recording-frame');
});

test('an unchanged recorder heartbeat does not replace an in-progress form or timeline', async () => {
  const f = fixture();
  const session = {id:'same',status:'finished',app_package:'com.kfb.model',heartbeat_ts:1,updated_ts:1,updated_at:'before',steps:[{id:'one',sequence:1,type:'key',key:'BACK'}]};
  f.context.apiRequest = async () => ({session:{...session,heartbeat_ts:2,updated_ts:2,updated_at:'after'}});
  f.context.sessionFixture = session;
  f.run('deviceRecorderSession=sessionFixture;renderDeviceRecorder()');
  const input = f.dom.window.document.getElementById('device-recorder-task-name');
  const timeline = f.dom.window.document.querySelector('.device-recorder-timeline');
  input.value = '正在编辑的用例';
  await f.run('refreshDeviceRecording()');
  assert.equal(f.dom.window.document.getElementById('device-recorder-task-name'), input);
  assert.equal(input.value, '正在编辑的用例');
  assert.equal(f.dom.window.document.querySelector('.device-recorder-timeline'), timeline);
});

test('saving after renaming a generated case regenerates YAML with the new task name', async () => {
  const f = fixture();
  const requests = [];
  f.context.apiRequest = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({url, body});
    if (url === '/device-recordings/generate') return {
      result: {yaml: `android: {}\ntasks:\n- name: ${body.task_name}\n  flow:\n  - aiTap: 我的\n`, task_name: body.task_name, can_debug: true},
      session: {id:'s1',status:'finished',app_package:'com.kfb.model',steps:[{id:'tap',sequence:1,type:'tap',semantic_description:'我的',evidence_status:'captured'}]},
    };
    return {};
  };
  f.run("deviceRecorderSession={id:'s1',status:'finished',app_package:'com.kfb.model',steps:[{id:'tap',sequence:1,type:'tap',semantic_description:'我的',evidence_status:'captured'}]}; renderDeviceRecorder()");
  await f.run('generateDeviceRecordingYaml()');
  f.dom.window.document.getElementById('device-recorder-task-name').value = '新的用例名称';
  f.run("syncRecorderFileName('新的用例名称')");
  await f.run('saveDeviceRecordingYaml()');
  assert.equal(requests.filter(item => item.url === '/device-recordings/generate').length, 2);
  assert.equal(requests.at(-1).body.file, '新的用例名称.yaml');
  assert.match(requests.at(-1).body.content, /- name: 新的用例名称/);
});

test('legacy image bytes are shown as unrecognized instead of a control name', () => {
  const f = fixture();
  f.run("deviceRecorderSession={id:'s1',status:'finished',app_package:'com.kfb.model',steps:[{id:'tap-1',sequence:1,type:'tap',ui_node:{text:'Fn+i0op0v4AAAAAElFTkSuQmCC'},evidence_status:'captured'}]}; renderDeviceRecorder()");
  assert.match(f.dom.window.document.querySelector('.device-recorder-timeline').textContent, /确认控件/);
  assert.doesNotMatch(f.dom.window.document.querySelector('.device-recorder-timeline').textContent, /Fn\+i0op0v4/);
});

function fixture() {
  const dom = new JSDOM('<div id="editor-area"></div>', {url: 'http://platform.example/task-manager.html'});
  const calls = [];
  const opened = [];
  const context = vm.createContext({
    window: dom.window, document: dom.window.document, location: dom.window.location,
    sessionStorage: dom.window.sessionStorage, URL, taskApps: [{name: '微信', package: 'com.tencent.mm', enabled: true, modules: ['微信登录']},{name:'智小白3D',package:'com.kfb.model',enabled:true,modules:['3D打印基线']}],
    modules: {'微信登录': [], '3D打印基线': []},
    runnerDevices: [{runner_id: 'win-runner-01', device_id: 'ecbfd645', model: 'OPPO Reno9', status: 'online', usage_status: 'idle'}],
    escapeHtml: value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;'),
    resetYamlToolbarForManager() {}, loadModules: async () => {}, loadRunnerDevices: async () => {},
    showToast() {}, prompt: () => '', clearInterval() {}, setInterval: () => 1, setTimeout() {},
    apiRequest: async (url, options = {}) => {
      calls.push({url, body: options.body ? JSON.parse(options.body) : null});
      return {session: {id: 'session-1', status: 'recording', recording_token: 'secret', runner_id: '', device_id: '', steps: []}, sonic_url: 'http://sonic.example/Index/Devices'};
    },
  });
  context.window.open = (url, name) => { const ref = {url, name, closed: false, postMessage() {}}; opened.push(ref); return ref; };
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'js/device-recorder.js'), 'utf8'), context);
  return {context, dom, calls, opened, run: code => vm.runInContext(code, context)};
}

test('opens Sonic with a fragment handoff that is never sent in the HTTP request', async () => {
  const f = fixture();
  f.run('renderDeviceRecorder()');
  assert.match(f.dom.window.document.getElementById('editor-area').textContent, /OPPO Reno9/);
  await f.run('startDeviceRecording()');
  assert.equal(f.calls[0].body.device_id, undefined);
  assert.equal(f.calls[0].body.app_package, 'com.kfb.model');
  const openedUrl = new URL(f.opened[0].url);
  assert.equal(`${openedUrl.origin}${openedUrl.pathname}`, 'http://sonic.example/Index/Devices');
  assert.match(openedUrl.searchParams.get('midsceneRecorder') || '', /^\d+$/);
  assert.doesNotMatch(`${openedUrl.origin}${openedUrl.pathname}${openedUrl.search}`, /secret|session-1/);
  assert.match(openedUrl.hash, /^#__MIDSCENE_RECORDING_HANDOFF__/);
  assert.equal(f.opened[0].name, 'midscene-sonic-recorder-session-1');
  const handoff = JSON.parse(decodeURIComponent(openedUrl.hash.replace(/^#__MIDSCENE_RECORDING_HANDOFF__/, '')));
  assert.equal(handoff.sessionId, 'session-1');
  assert.equal(handoff.recordingToken, 'secret');
  assert.equal(handoff.endpoint, 'http://platform.example/api/device-recordings/action');
});

test('defaults recording to 智小白3D and shows its automatic launch as the first step', () => {
  const f = fixture();
  f.run('renderDeviceRecorder()');
  assert.equal(f.dom.window.document.getElementById('device-recorder-app').value, 'com.kfb.model');
  f.run("deviceRecorderSession={id:'s1',status:'recording',app_package:'com.kfb.model',steps:[{id:'tap-1',sequence:1,type:'tap',semantic_description:'我的',evidence_status:'captured'}]}; renderDeviceRecorder()");
  const automatic = f.dom.window.document.querySelector('[data-recorder-auto-launch]');
  assert.ok(automatic);
  assert.match(automatic.textContent, /1.*启动应用.*智小白3D.*com\.kfb\.model/s);
  assert.match(f.dom.window.document.querySelector('.device-recorder-timeline').textContent, /2.*点击.*我的/s);
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
  const historyButton = f.dom.window.document.querySelector('[data-action="open-recording-history"]');
  assert.ok(historyButton);
  assert.match(historyButton.textContent, /录制记录 1/);
  assert.match(f.dom.window.document.getElementById('device-recorder-yaml').textContent, /tasks: \[\]/);
  f.run('showDeviceRecordingHistory()');
  assert.ok(f.dom.window.document.querySelector('.device-recorder-history-page'));
  assert.match(f.dom.window.document.body.textContent, /2026-09-22 12:00:00/);
  assert.match(f.dom.window.document.body.textContent, /已生成 YAML/);
  f.run('newDeviceRecording()');
  assert.match(f.dom.window.document.body.textContent, /尚未开始/);
});

test('cancelled history has a direct new recording action', () => {
  const f = fixture();
  f.run("deviceRecorderSession={id:'cancelled',status:'cancelled',app_package:'com.kfb.model',steps:[]}; renderDeviceRecorder()");
  const button = f.dom.window.document.querySelector('[data-action="new-device-recording"]');
  assert.ok(button);
  assert.match(button.textContent, /新建录制/);
  f.run('newDeviceRecording()');
  assert.equal(f.run('deviceRecorderSession'), null);
  assert.equal(f.dom.window.document.getElementById('device-recorder-app').value, 'com.kfb.model');
});

test('timeline explains scaled coordinates while retaining the raw Sonic point', () => {
  const f = fixture();
  f.run("deviceRecorderSession={id:'s1',status:'finished',app_package:'com.kfb.model',steps:[{id:'tap',sequence:1,type:'tap',point:{x:976,y:2331},raw_point:{x:1084,y:2564},coordinate_transform:'1200x2640->1080x2400',semantic_description:'我的',evidence_status:'captured',screenshot_path:'/tmp/x.png'}]}; renderDeviceRecorder()");
  assert.match(f.dom.window.document.querySelector('.device-recorder-timeline').textContent, /截图坐标：976，2331/);
  assert.match(f.dom.window.document.querySelector('.device-recorder-timeline').textContent, /Sonic 原始坐标：1084，2564/);
  assert.match(f.dom.window.document.querySelector('.device-recorder-timeline').textContent, /拖动红点可校正/);
  assert.match(f.dom.window.document.querySelector('.device-recorder-timeline').textContent, /重置点击位置/);
  assert.match(f.dom.window.document.querySelector('.device-recorder-timeline').textContent, /用当前红点重新识别/);
});

test('history offers deletion for a whole completed or cancelled recording', () => {
  const f = fixture();
  f.run("deviceRecorderHistory=[{id:'done',status:'cancelled',app_package:'com.kfb.model',steps:[]}]; showDeviceRecordingHistory()");
  assert.ok(f.dom.window.document.querySelector('[data-action="delete-recording-history"]'));
  f.run("deviceRecorderSession={id:'done',status:'cancelled',app_package:'com.kfb.model',steps:[]}; renderDeviceRecorder()");
  assert.ok(f.dom.window.document.querySelector('[data-action="delete-current-recording"]'));
});

test('recording history entry remains visible for an empty or failed history request', () => {
  const f = fixture();
  f.run("deviceRecorderHistory=[]; deviceRecorderHistoryState='error'; deviceRecorderHistoryError='读取失败'; renderDeviceRecorder()");
  assert.ok(f.dom.window.document.querySelector('[data-action="open-recording-history"]'));
  f.run('showDeviceRecordingHistory()');
  assert.match(f.dom.window.document.body.textContent, /录制记录/);
  assert.match(f.dom.window.document.body.textContent, /读取失败/);
  assert.ok(f.dom.window.document.querySelector('[data-action="retry-recording-history"]'));
});

test('selecting a recording history card restores its steps and generated YAML', async () => {
  const f = fixture();
  f.context.apiRequest = async url => {
    assert.match(url, /\/device-recordings\?id=old/);
    return {session:{id:'old',status:'finished',app_package:'com.tencent.mm',device_id:'phone',steps:[{id:'step-1',sequence:1,type:'key',key:'BACK',semantic_description:'返回上一页'}],generated_result:{yaml:'tasks:\n  - name: 历史用例'}}};
  };
  await f.run("openDeviceRecordingHistory('old')");
  assert.match(f.dom.window.document.body.textContent, /返回上一页/);
  assert.match(f.dom.window.document.getElementById('device-recorder-yaml').textContent, /历史用例/);
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

test('binds Sonic only after the Runner has cached a real pre-action frame', async () => {
  const f = fixture();
  f.run('renderDeviceRecorder()');
  await f.run('startDeviceRecording()');
  const replies = [];
  const remoteTab = {postMessage(message, origin) { replies.push({message, origin}); }};
  f.context.remoteTab = remoteTab;
  f.context.apiRequest = async (url, options = {}) => {
    f.calls.push({url, body: options.body ? JSON.parse(options.body) : null});
    return {session:{id:'session-1',status:'recording',runner_id:'win-runner-01',device_id:'ecbfd645',app_package:'com.tencent.mm',pre_action_frame_status:'pending',steps:[]}};
  };
  await f.run("handleDeviceRecorderMessage({origin:'http://sonic.example',source:remoteTab,data:{type:'MIDSCENE_RECORDING_READY',sessionId:'session-1',deviceId:'ecbfd645'}})");
  const bind = f.calls.find(call => call.url === '/device-recordings/bind');
  assert.deepEqual(bind.body, {session_id: 'session-1', device_id: 'ecbfd645'});
  assert.equal(replies.length, 0);
  f.run("deviceRecorderSession.pre_action_frame_status='ready'; confirmRecorderSonicBinding()");
  assert.equal(replies[0].message.type, 'MIDSCENE_RECORDING_BOUND');
  assert.equal(replies[0].origin, 'http://sonic.example');
});

test('does not confirm a recorded step until the next pre-action frame is ready', () => {
  const f = fixture();
  const replies = [];
  f.context.remoteTab = {closed:false,postMessage(message){replies.push(message)}};
  f.run("deviceRecorderWindow=remoteTab; deviceRecorderSession={id:'session-1',status:'recording',pre_action_frame_status:'pending',steps:[{id:'s1',sequence:1,type:'tap',semantic_description:'返回',evidence_status:'captured'}]}; notifyRecorderStepResult()");
  assert.equal(replies.length, 0);
  f.run("deviceRecorderSession.pre_action_frame_status='ready'; notifyRecorderStepResult()");
  assert.equal(replies[0].type, 'MIDSCENE_RECORDING_STEP_CONFIRMED');
  assert.equal(replies[0].success, true);
});

test('does not announce recognition failure while visual identification is still pending', () => {
  const f = fixture();
  const replies = [];
  f.context.remoteTab = {closed:false,postMessage(message){replies.push(message)}};
  f.run("deviceRecorderWindow=remoteTab; deviceRecorderSession={id:'session-1',status:'recording',pre_action_frame_status:'ready',steps:[{id:'s1',sequence:1,type:'tap',evidence_status:'captured'}]}; notifyRecorderStepResult()");
  assert.equal(replies.length, 0);
  f.run("deviceRecorderSession.steps[0].semantic_recognition_status='running'; notifyRecorderStepResult()");
  assert.equal(replies.length, 0);
  f.run("deviceRecorderSession.steps[0].semantic_recognition_status='recognized'; deviceRecorderSession.steps[0].semantic_description='打印记录'; notifyRecorderStepResult()");
  assert.equal(replies.length, 1);
  assert.equal(replies[0].success, true);
});

test('finishing a recording tells the Sonic tab to stop mirroring later phone actions', async () => {
  const f = fixture();
  const replies = [];
  f.context.remoteTab = {closed: false, postMessage(message, origin) { replies.push({message, origin}); }};
  f.context.apiRequest = async () => ({session: {id: 'session-1', status: 'finished', steps: []}});
  f.run("deviceRecorderWindow=remoteTab; deviceRecorderSession={id:'session-1',status:'recording',steps:[{id:'tap',sequence:1,type:'tap',semantic_description:'我的'}]}; sessionStorage.setItem('deviceRecorderSonicUrl','http://sonic.example/Index/Devices')");
  await f.run('finishDeviceRecording()');
  assert.equal(replies.at(-1)?.message.type, 'MIDSCENE_RECORDING_STOP');
  assert.equal(replies.at(-1)?.message.sessionId, 'session-1');
  assert.equal(replies.at(-1)?.origin, 'http://sonic.example');
});

test('a recognized tap with an unchanged phone screen is not reported as a successful navigation', () => {
  const f = fixture();
  const replies = [];
  f.context.remoteTab = {closed:false,postMessage(message){replies.push(message)}};
  f.run("deviceRecorderWindow=remoteTab; deviceRecorderSession={id:'session-1',status:'recording',pre_action_frame_status:'ready',steps:[{id:'s1',sequence:1,type:'tap',ui_node:{text:'打印记录'},screen_change_status:'unchanged',evidence_status:'captured'}]}; notifyRecorderStepResult()");
  assert.equal(replies.length, 1);
  assert.equal(replies[0].success, true);
  assert.equal(replies[0].screenUnchanged, true);
  f.run('renderDeviceRecorder()');
  const html = f.run("document.getElementById('editor-area').innerHTML");
  assert.match(html, /页面结构未变化/);
  assert.match(html, /请核对手机是否响应/);
});

test('a failed recognition is reported once and lets later steps be confirmed', () => {
  const f = fixture();
  const replies = [];
  f.context.remoteTab = {closed:false,postMessage(message){replies.push(message)}};
  f.run("deviceRecorderWindow=remoteTab; deviceRecorderSession={id:'session-1',status:'recording',pre_action_frame_status:'ready',steps:[{id:'s1',sequence:1,type:'tap',evidence_status:'captured',semantic_recognition_status:'failed'}]}; notifyRecorderStepResult(); notifyRecorderStepResult()");
  assert.equal(replies.length, 1);
  assert.equal(replies[0].success, false);
  assert.equal(f.run('deviceRecorderConfirmedSequence'), 1);
  f.run("deviceRecorderSession.steps.push({id:'s2',sequence:2,type:'tap',semantic_description:'打印记录',evidence_status:'captured',semantic_recognition_status:'recognized'}); notifyRecorderStepResult()");
  assert.equal(replies.length, 2);
  assert.equal(replies[1].success, true);
  assert.equal(f.run('deviceRecorderConfirmedSequence'), 2);
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

test('live phone refresh changes availability without replacing form or evidence DOM', async () => {
  const f=fixture(); Object.defineProperty(f.dom.window.document,'hidden',{value:false,configurable:true});
  f.run('renderDeviceRecorder()');
  const app=f.dom.window.document.getElementById('device-recorder-app');app.value='com.tencent.mm';
  const timeline=f.dom.window.document.querySelector('.device-recorder-timeline');
  let devices=[{device_id:'ecbfd645',model:'PHM110',runner_online:true,status:'online',usage_status:'busy',usage_label:'wangwc 占用中'}];
  f.context.apiRequest=async()=>({devices});
  await f.run('refreshRecorderDevices()');
  assert.equal(f.dom.window.document.getElementById('device-recorder-app'),app);
  assert.equal(app.value,'com.tencent.mm');
  assert.equal(f.dom.window.document.querySelector('.device-recorder-timeline'),timeline);
  assert.match(f.dom.window.document.getElementById('device-recorder-devices').textContent,/wangwc 占用中/);
  assert.equal(f.dom.window.document.querySelector('[data-action="start-recording"]').disabled,true);
  devices=[{device_id:'ecbfd645',model:'PHM110',runner_online:true,status:'online',usage_status:'idle'}];
  await f.run('refreshRecorderDevices()');
  assert.equal(f.dom.window.document.querySelector('[data-action="start-recording"]').disabled,false);
  devices=[]; await f.run('refreshRecorderDevices()');
  assert.match(f.dom.window.document.getElementById('device-recorder-devices').textContent,/PHM110.*离线/s);
});

test('refresh failure marks cached devices stale and recovery restores availability', async () => {
  const f=fixture();Object.defineProperty(f.dom.window.document,'hidden',{value:false,configurable:true});f.run('renderDeviceRecorder()');
  f.context.apiRequest=async()=>{throw new Error('network down')};await f.run('refreshRecorderDevices()');
  assert.match(f.dom.window.document.getElementById('device-recorder-devices').textContent,/更新失败.*可能已过期/s);
  assert.equal(f.dom.window.document.querySelector('[data-action="start-recording"]').disabled,true);
  assert.equal(f.dom.window.document.querySelector('.device-recorder-phone-status.idle'),null);
  f.context.apiRequest=async()=>({devices:[{device_id:'ecbfd645',status:'online',runner_online:true,usage_status:'idle'}]});
  await f.run('refreshRecorderDevices()');
  assert.equal(f.dom.window.document.querySelector('[data-action="start-recording"]').disabled,false);
});

test('device polling avoids overlapping calls and ignores late results after leaving', async () => {
  const f=fixture();Object.defineProperty(f.dom.window.document,'hidden',{value:false,configurable:true});f.run('renderDeviceRecorder()');
  let count=0,release; f.context.apiRequest=async()=>{count++;return new Promise(r=>release=r)};
  const a=f.run('refreshRecorderDevices()');await f.run('refreshRecorderDevices()');assert.equal(count,1);
  f.run('showDeviceRecordingHistory()'); const content=f.dom.window.document.getElementById('editor-area').innerHTML;
  release({devices:[{device_id:'different',status:'online'}]});await a;
  assert.equal(f.dom.window.document.getElementById('editor-area').innerHTML,content);
  await f.run('refreshRecorderDevices()');assert.equal(count,1);
  assert.equal(f.run('deviceRecorderDeviceTimer'),null);
});

test('offline and unknown phones are never green or offered as ready', () => {
  const f=fixture();f.run("recorderDevices=[{device_id:'a',runner_online:false,status:'online',usage_status:'idle'},{device_id:'b',runner_online:true,status:'online',usage_status:'unknown'}];renderDeviceRecorder()");
  assert.ok(f.dom.window.document.querySelector('.device-recorder-phone-status.offline'));
  assert.ok(f.dom.window.document.querySelector('.device-recorder-phone-status.unknown'));
  assert.equal(f.dom.window.document.querySelector('.device-recorder-phone-status.idle'),null);
  assert.equal(f.dom.window.document.querySelector('[data-action="start-recording"]').disabled,true);
});


test('hidden pages pause status traffic and focus resumes with one five second timer', async () => {
  const f=fixture(); let polls=[]; f.context.setInterval=(fn,ms)=>{polls.push({fn,ms});return 7};
  Object.defineProperty(f.dom.window.document,'hidden',{value:true,configurable:true});
  let count=0;f.context.apiRequest=async()=>{count++;return {devices:[]}};
  f.run('renderDeviceRecorder();renderDeviceRecorder()');assert.equal(polls.length,1);assert.equal(polls[0].ms,5000);
  await f.run('refreshRecorderDevices()');assert.equal(count,0);
  Object.defineProperty(f.dom.window.document,'hidden',{value:false,configurable:true});
  f.dom.window.document.dispatchEvent(new f.dom.window.Event('visibilitychange'));await new Promise(resolve=>setImmediate(resolve));assert.equal(count,1);
  f.dom.window.document.getElementById('editor-area').innerHTML='<p>其他页面</p>';
  await polls[0].fn();assert.equal(count,1);assert.equal(f.run('deviceRecorderDeviceTimer'),null);
});
