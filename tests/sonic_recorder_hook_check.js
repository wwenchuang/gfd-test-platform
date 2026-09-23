const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('Sonic hook only becomes ready and mirrors the platform-selected phone', async () => {
  const listeners = {};
  const order = [];
  const ready = [];
  const opener = {postMessage(message) { ready.push(message); }};
  class FakeWebSocket {
    constructor(url) { this.url = url; }
    send(data) { order.push(['sonic', data]); }
  }
  const window = {
    opener, WebSocket: FakeWebSocket,
    addEventListener(type, fn) { listeners[type] = fn; },
    document: {body: {appendChild() {}}, getElementById() { return {style: {}}; }, createElement() { return {style: {}}; }},
  };
  const context = vm.createContext({window, document: window.document, URL, fetch: async (url, options) => { order.push([url.endsWith('/bridge') ? 'bridge' : 'mirror', JSON.parse(options.body)]); return {ok: true}; }, Date, Math, JSON, String, Number, Object, RegExp, Error, setTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);
  assert.equal(ready[0].type, 'MIDSCENE_RECORDER_HOOK_READY');
  ready.length = 0;
  listeners.message({source: opener, origin: 'http://platform.example', data: {type: 'MIDSCENE_RECORDING_START', sessionId: 's1', recordingToken: 't1', deviceId: '', endpoint: 'http://platform.example/api/device-recordings/action'}});
  assert.equal(ready.length, 0);
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone-a/token');
  assert.equal(ready.at(-1).type, 'MIDSCENE_RECORDING_READY');
  assert.equal(ready.at(-1).deviceId, 'phone-a');
  listeners.message({source: opener, data: {type: 'MIDSCENE_RECORDING_BOUND', sessionId: 's1', deviceId: 'phone-a'}});
  socket.send(JSON.stringify({type: 'debug', detail: 'tap', point: '10,20'}));
  await Promise.resolve();
  assert.deepEqual(order.filter(item => item[0] !== 'bridge').map(item => item[0]), ['sonic', 'mirror']);
  assert.deepEqual(order.find(item => item[0] === 'mirror')[1].action.point, {x: 10, y: 20});
});

test('Sonic rechecks ready evidence while the user pauses between actions', async () => {
  const listeners = {};
  const scheduled = [];
  const opener = {postMessage() {}};
  class FakeWebSocket { constructor(url) { this.url = url; } send() {} }
  const document = {body: {appendChild() {}}, getElementById() { return {style: {}}; }, createElement() { return {style: {}}; }};
  const window = {opener, WebSocket: FakeWebSocket, document, addEventListener(type, fn) { listeners[type] = fn; }};
  const context = vm.createContext({window, document, URL, fetch: async () => ({ok: true, json: async () => ({session: {pre_action_frame_status: 'ready', steps: []}})}), Date, Math, JSON, String, Number, Object, RegExp, Error, Set,
    setTimeout(fn, delay) { scheduled.push({fn, delay}); return scheduled.length; }, clearTimeout() {}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);
  listeners.message({source: opener, origin: 'http://platform.example', data: {type: 'MIDSCENE_RECORDING_START', sessionId: 'pause', recordingToken: 't', endpoint: 'http://platform.example/api/device-recordings/action'}});
  new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  await new Promise(setImmediate);
  assert.ok(scheduled.some(item => item.delay === 15000));
});

test('Sonic footer keyEvent back and home are mirrored with the native command', async () => {
  const listeners = {};
  const sent = [];
  const opener = {postMessage() {}};
  class FakeWebSocket {
    constructor(url) { this.url = url; }
    send(data) { sent.push(['sonic', JSON.parse(data)]); }
  }
  const document = {body: {appendChild() {}}, getElementById() { return {style: {}}; }, createElement() { return {style: {}}; }};
  const window = {opener, WebSocket: FakeWebSocket, document, addEventListener(type, fn) { listeners[type] = fn; }};
  const context = vm.createContext({window, document, URL, fetch: async (url, options) => {
    if (!url.endsWith('/bridge')) sent.push(['platform', JSON.parse(options.body).action]);
    return {ok: true, json: async () => ({session: {pre_action_frame_status:'ready',steps: sent.filter(item=>item[0]==='platform').map((_,index)=>({sequence:index+1,type:'key',evidence_status:'captured',semantic_description:'返回'}))}})};
  }, Date, Math, JSON, String, Number, Object, RegExp, Error, Set, setTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);
  listeners.message({source: opener, origin: 'http://platform.example', data: {type: 'MIDSCENE_RECORDING_START', sessionId: 'keys', recordingToken: 't', endpoint: 'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  listeners.message({source: opener, data: {type: 'MIDSCENE_RECORDING_BOUND', sessionId: 'keys', deviceId: 'phone'}});
  socket.send(JSON.stringify({type: 'keyEvent', detail: 4}));
  listeners.message({source: opener, data: {type: 'MIDSCENE_RECORDING_STEP_CONFIRMED', sessionId: 'keys', sequence: 1, success: true}});
  await new Promise(setImmediate);
  socket.send(JSON.stringify({type: 'keyEvent', detail: 3}));
  await Promise.resolve();
  assert.deepEqual(sent.filter(item => item[0] === 'platform').map(item => item[1].key), ['BACK', 'HOME']);
  assert.deepEqual(sent.filter(item => item[0] === 'sonic').map(item => item[1].detail), [4, 3]);
});

test('ending a recording stops mirroring while Sonic keeps sending phone commands', async () => {
  const listeners = {};
  const sent = [];
  const opener = {postMessage() {}};
  class FakeWebSocket {
    constructor(url) { this.url = url; }
    send(data) { sent.push(['sonic', JSON.parse(data)]); }
  }
  const document = {body: {appendChild() {}}, getElementById() { return {style: {}}; }, createElement() { return {style: {}}; }};
  const window = {opener, WebSocket: FakeWebSocket, document, addEventListener(type, fn) { listeners[type] = fn; }};
  const context = vm.createContext({window, document, URL, fetch: async (url, options) => {
    if (!url.endsWith('/bridge')) sent.push(['platform', JSON.parse(options.body).action]);
    return {ok: true};
  }, Date, Math, JSON, String, Number, Object, RegExp, Error, Set, setTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);
  listeners.message({source: opener, origin: 'http://platform.example', data: {type: 'MIDSCENE_RECORDING_START', sessionId: 'done', recordingToken: 't', endpoint: 'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  listeners.message({source: opener, data: {type: 'MIDSCENE_RECORDING_BOUND', sessionId: 'done', deviceId: 'phone'}});
  listeners.message({source: opener, data: {type: 'MIDSCENE_RECORDING_STOP', sessionId: 'done'}});
  socket.send(JSON.stringify({type: 'debug', detail: 'tap', point: '10,20'}));
  await Promise.resolve();
  assert.deepEqual(sent.map(item => item[0]), ['sonic']);
});

test('a refreshed Sonic page drops a finished recording instead of retrying its stale bridge', async () => {
  const listeners = {};
  const sent = [];
  const scheduled = [];
  let stored = null;
  const opener = {postMessage() {}};
  class FakeWebSocket {
    constructor(url) { this.url = url; }
    send(data) { sent.push(['sonic', JSON.parse(data)]); }
  }
  const document = {body: {appendChild() {}}, getElementById() { return {style: {}}; }, createElement() { return {style: {}}; }};
  const storage = {getItem() { return stored; }, setItem(_key, value) { stored = value; }, removeItem() { stored = null; }};
  const window = {opener, WebSocket: FakeWebSocket, document, sessionStorage: storage, localStorage: storage, addEventListener(type, fn) { listeners[type] = fn; }};
  const context = vm.createContext({window, document, URL, fetch: async url => {
    if (url.endsWith('/bridge')) return {ok: false, status: 409, json: async () => ({error: '录制会话当前不能绑定手机'})};
    sent.push(['platform']);
    return {ok: false, status: 409};
  }, Date, Math, JSON, String, Number, Object, RegExp, Error, Set, setTimeout(fn) { scheduled.push(fn); return scheduled.length; }, clearTimeout() {}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);
  listeners.message({source: opener, origin: 'http://platform.example', data: {type: 'MIDSCENE_RECORDING_START', sessionId: 'old', recordingToken: 't', endpoint: 'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  await new Promise(setImmediate);
  assert.equal(stored, null);
  assert.equal(scheduled.length, 0);
  socket.send(JSON.stringify({type: 'debug', detail: 'tap', point: '10,20'}));
  await Promise.resolve();
  assert.deepEqual(sent.map(item => item[0]), ['sonic']);
});

test('Sonic mirrors touch coordinates without reading its video canvas', async () => {
  const listeners = {};
  const order = [];
  const opener = {postMessage() {}};
  class FakeWebSocket { constructor(url) { this.url=url; } send(data) { order.push(['sonic', JSON.parse(data).detail]); } }
  let inspectedCanvases = 0;
  const canvas = {
    width:1080,height:2400,
    toDataURL() { throw new Error('recording must not read the Sonic video canvas'); },
    toBlob() { throw new Error('recording must not read the Sonic video canvas'); },
  };
  const document = {querySelectorAll() { inspectedCanvases += 1; return [canvas]; }, body:{appendChild(){}}, getElementById(){return {style:{}}}, createElement(){return {style:{}}}};
  const window = {opener,WebSocket:FakeWebSocket,document,addEventListener(type,fn){listeners[type]=fn;}};
  const context = vm.createContext({window,document,URL,fetch:async(url)=>{order.push([url.endsWith('/bridge') ? 'bridge' : 'platform']);return {ok:true}},Date,Math,JSON,String,Number,Object,RegExp,Error,Set,setTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','deploy/sonic-recorder-hook.js'),'utf8'),context);
  listeners.message({source:opener,origin:'http://platform.example',data:{type:'MIDSCENE_RECORDING_START',sessionId:'s',recordingToken:'t',deviceId:'',endpoint:'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  listeners.message({source:opener,data:{type:'MIDSCENE_RECORDING_BOUND',sessionId:'s',deviceId:'phone'}});
  socket.send(JSON.stringify({type:'touch',detail:'down 10 20'}));
  socket.send(JSON.stringify({type:'touch',detail:'up 10 20'}));
  await Promise.resolve();
  assert.deepEqual(order.filter(item=>item[0]!=='bridge').slice(0,2).map(item=>item[0]), ['sonic','sonic']);
  assert.equal(inspectedCanvases, 0);
  assert.ok(order.find(item=>item[0]==='platform'));
});

test('Sonic forwards touch before mirroring and leaves screenshot capture to the Runner', async () => {
  const listeners = {};
  const sent = [];
  const opener = {postMessage() {}};
  class FakeWebSocket { constructor(url) { this.url = url; } send(data) { sent.push(['sonic', JSON.parse(data).detail]); } }
  const video = {readyState: 3, videoWidth: 200, videoHeight: 400};
  const canvas = {width: 0, height: 0, getContext() { return {drawImage() { sent.push(['draw']); }}; },
    toDataURL() { sent.push(['encode']); return 'data:image/png;base64,iVBORw0KGgo='; }};
  const document = {body: {appendChild() {}}, getElementById(id) {
    return id === 'scrcpy-video' ? video : {style: {}};
  }, createElement(tag) { return tag === 'canvas' ? canvas : {style: {}}; }};
  const window = {opener, WebSocket: FakeWebSocket, document, addEventListener(type, fn) { listeners[type] = fn; }};
  const context = vm.createContext({window, document, URL, fetch: async (url, options) => {
    if (url.endsWith('/bridge')) return {ok: true};
    sent.push(['platform', JSON.parse(options.body).action]); return {ok: true};
  }, Date, Math, JSON, String, Number, Object, RegExp, Error, Set, setTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);
  listeners.message({source: opener, origin: 'http://platform.example', data: {type: 'MIDSCENE_RECORDING_START', sessionId: 'live', recordingToken: 't', endpoint: 'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  listeners.message({source: opener, data: {type: 'MIDSCENE_RECORDING_BOUND', sessionId: 'live', deviceId: 'phone'}});
  socket.send(JSON.stringify({type: 'touch', detail: 'down 50 100'}));
  socket.send(JSON.stringify({type: 'touch', detail: 'up 50 100'}));
  await Promise.resolve();
  const order = sent.map(item => item[0]);
  assert.deepEqual(order.slice(0, 2), ['sonic', 'sonic']);
  assert.ok(!order.includes('draw'));
  assert.ok(!order.includes('encode'));
  const action = sent.find(item => item[0] === 'platform')[1];
  assert.equal(action.evidence_content_base64, undefined);
  assert.equal(action.evidence_width, undefined);
  assert.equal(action.evidence_height, undefined);
});

test('Sonic can mirror twenty confirmed taps without touching the video canvas or dropping phone commands', async () => {
  const listeners = {};
  const sent = [];
  let canvasReads = 0;
  const opener = {postMessage() {}};
  class FakeWebSocket { constructor(url){this.url=url} send(data){sent.push(['sonic',JSON.parse(data).detail])} }
  const document = {querySelectorAll(){canvasReads += 1;return []},body:{appendChild(){}},getElementById(){return {style:{}}},createElement(){return {style:{}}}};
  const window = {opener,WebSocket:FakeWebSocket,document,addEventListener(type,fn){listeners[type]=fn;}};
  const context = vm.createContext({window,document,URL,fetch:async(url,options)=>{
    if(url.endsWith('/bridge')) return {ok:true,json:async()=>({session:{pre_action_frame_status:'ready',steps:sent.filter(item=>item[0]==='platform').map((_,index)=>({sequence:index+1,type:'tap',evidence_status:'captured',semantic_description:'按钮'}))}})};
    sent.push(['platform',JSON.parse(options.body).action]);
    return {ok:true,json:async()=>({step:{sequence:sent.filter(item=>item[0]==='platform').length}})};
  },Date,Math,JSON,String,Number,Object,RegExp,Error,Set,setTimeout,clearTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','deploy/sonic-recorder-hook.js'),'utf8'),context);
  listeners.message({source:opener,origin:'http://platform.example',data:{type:'MIDSCENE_RECORDING_START',sessionId:'stress',recordingToken:'t',endpoint:'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  listeners.message({source:opener,data:{type:'MIDSCENE_RECORDING_BOUND',sessionId:'stress',deviceId:'phone'}});
  for(let index=0;index<20;index+=1){
    socket.send(JSON.stringify({type:'touch',detail:`down ${index} ${index}`}));
    socket.send(JSON.stringify({type:'touch',detail:`up ${index} ${index}`}));
    listeners.message({source:opener,data:{type:'MIDSCENE_RECORDING_STEP_CONFIRMED',sessionId:'stress',sequence:index+1,success:true}});
    await new Promise(setImmediate);
  }
  await Promise.resolve();
  assert.equal(canvasReads,0);
  assert.equal(sent.filter(item=>item[0]==='sonic').length,40);
  assert.equal(sent.filter(item=>item[0]==='platform').length,20);
});

test('Sonic never records a second action while the prior step is still being verified', async () => {
  const listeners = {};
  const sent = [];
  const notices = [];
  const opener = {postMessage(message){notices.push(message)}};
  class FakeWebSocket { constructor(url){this.url=url} send(data){sent.push(['sonic',JSON.parse(data).detail])} }
  const document = {querySelectorAll(){throw new Error('must not read canvas')},body:{appendChild(){}},getElementById(){return {style:{}}},createElement(){return {style:{}}}};
  const window = {opener,WebSocket:FakeWebSocket,document,addEventListener(type,fn){listeners[type]=fn;}};
  const context = vm.createContext({window,document,URL,fetch:async(url,options)=>{sent.push([url.endsWith('/bridge')?'bridge':'platform',JSON.parse(options.body).action]);return {ok:true}},Date,Math,JSON,String,Number,Object,RegExp,Error,Set,setTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','deploy/sonic-recorder-hook.js'),'utf8'),context);
  listeners.message({source:opener,origin:'http://platform.example',data:{type:'MIDSCENE_RECORDING_START',sessionId:'guard',recordingToken:'t',endpoint:'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  listeners.message({source:opener,data:{type:'MIDSCENE_RECORDING_BOUND',sessionId:'guard',deviceId:'phone'}});
  for(const coordinate of [10,20]){
    socket.send(JSON.stringify({type:'touch',detail:`down ${coordinate} ${coordinate}`}));
    socket.send(JSON.stringify({type:'touch',detail:`up ${coordinate} ${coordinate}`}));
  }
  await Promise.resolve();
  assert.equal(sent.filter(item=>item[0]==='sonic').length,4);
  assert.equal(sent.filter(item=>item[0]==='platform').length,1);
  assert.ok(notices.some(item=>item.type==='MIDSCENE_RECORDING_ERROR' && /未记录/.test(item.message)));
});

test('a failed recognition is retained but releases the recorder for the next action', async () => {
  const listeners = {};
  const sent = [];
  const opener = {postMessage(){}};
  class FakeWebSocket { constructor(url){this.url=url} send(data){sent.push(['sonic',JSON.parse(data).detail])} }
  const document = {body:{appendChild(){}},getElementById(){return {style:{}}},createElement(){return {style:{}}}};
  const window = {opener,WebSocket:FakeWebSocket,document,addEventListener(type,fn){listeners[type]=fn;}};
  const context = vm.createContext({window,document,URL,fetch:async(url,options)=>{
    if (url.endsWith('/bridge')) return {ok:true,json:async()=>({session:{pre_action_frame_status:'ready',steps:sent.filter(item=>item[0]==='platform').length > 1
      ? [{sequence:2,type:'tap',evidence_status:'captured',semantic_recognition_status:'recognized',semantic_description:'下一按钮'}]
      : [{sequence:1,type:'tap',evidence_status:'captured',semantic_recognition_status:'failed'}]}})};
    sent.push(['platform',JSON.parse(options.body).action]);
    return {ok:true};
  },Date,Math,JSON,String,Number,Object,RegExp,Error,Set,setTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','deploy/sonic-recorder-hook.js'),'utf8'),context);
  listeners.message({source:opener,origin:'http://platform.example',data:{type:'MIDSCENE_RECORDING_START',sessionId:'failed',recordingToken:'t',endpoint:'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  listeners.message({source:opener,data:{type:'MIDSCENE_RECORDING_BOUND',sessionId:'failed',deviceId:'phone'}});
  socket.send(JSON.stringify({type:'debug',detail:'tap',point:'10,10'}));
  listeners.message({source:opener,data:{type:'MIDSCENE_RECORDING_STEP_CONFIRMED',sessionId:'failed',sequence:1,success:false}});
  await new Promise(setImmediate);
  socket.send(JSON.stringify({type:'debug',detail:'tap',point:'20,20'}));
  await Promise.resolve();
  assert.equal(sent.filter(item=>item[0]==='platform').length,2);
});

test('bridge recognition failure with a ready next frame still permits the next recorded tap', async () => {
  const listeners = {};
  const sent = [];
  const opener = {postMessage() {}};
  class FakeWebSocket { constructor(url) { this.url = url; } send(data) { sent.push(['sonic', JSON.parse(data).detail]); } }
  const document = {body:{appendChild(){}},getElementById(){return {style:{}}},createElement(){return {style:{}}}};
  let bridgeCount = 0;
  const fetch = async (url, options) => {
    if (url.endsWith('/bridge')) {
      bridgeCount += 1;
      return {ok: true, json: async () => ({session: bridgeCount === 1
        ? {pre_action_frame_status: 'ready', steps: []}
        : bridgeCount === 2
          ? {pre_action_frame_status: 'pending', steps: [{sequence: 1, type: 'tap', evidence_status: 'captured', semantic_recognition_status: 'running'}]}
          : {pre_action_frame_status: 'ready', steps: [{sequence: 1, type: 'tap', evidence_status: 'captured', semantic_recognition_status: 'failed'}]}})};
    }
    sent.push(['platform', JSON.parse(options.body).action]);
    return {ok: true, json: async () => ({step: {sequence: sent.filter(item => item[0] === 'platform').length}})};
  };
  let poll;
  const window = {opener,WebSocket:FakeWebSocket,document,addEventListener(type,fn){listeners[type]=fn;}};
  const context = vm.createContext({window,document,URL,fetch,Date,Math,JSON,String,Number,Object,RegExp,Error,Set,setTimeout(fn){poll=fn;return 1;},clearTimeout(){}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','deploy/sonic-recorder-hook.js'),'utf8'),context);
  listeners.message({source:opener,origin:'http://platform.example',data:{type:'MIDSCENE_RECORDING_START',sessionId:'failed-bridge',recordingToken:'t',endpoint:'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  await new Promise(setImmediate);
  socket.send(JSON.stringify({type:'debug',detail:'tap',point:'10,10'}));
  await new Promise(setImmediate);
  poll();
  await new Promise(setImmediate);
  poll();
  await new Promise(setImmediate);
  socket.send(JSON.stringify({type:'debug',detail:'tap',point:'20,20'}));
  await new Promise(setImmediate);
  assert.equal(sent.filter(item => item[0] === 'platform').length, 2);
});

test('a ready next frame permits another tap while prior visual naming is still running', async () => {
  const listeners = {};
  const sent = [];
  const notices = [];
  let bridgeCount = 0;
  let poll;
  const opener = {postMessage(message) { notices.push(message); }};
  class FakeWebSocket { constructor(url) { this.url = url; } send(data) { sent.push(['sonic', JSON.parse(data).detail]); } }
  const document = {body:{appendChild(){}},getElementById(){return {style:{},textContent:''}},createElement(){return {style:{},textContent:''}}};
  const window = {opener,WebSocket:FakeWebSocket,document,addEventListener(type,fn){listeners[type]=fn;}};
  const context = vm.createContext({window,document,URL,fetch:async(url,options)=>{
    if (url.endsWith('/bridge')) {
      bridgeCount += 1;
      return {ok:true,json:async()=>({session: bridgeCount === 1
        ? {pre_action_frame_status:'ready',steps:[]}
        : {pre_action_frame_status:'ready',steps:[{sequence:1,type:'tap',evidence_status:'captured',semantic_recognition_status:'running'}]}})};
    }
    sent.push(['platform',JSON.parse(options.body).action]);
    return {ok:true,json:async()=>({step:{sequence:sent.filter(item=>item[0]==='platform').length}})};
  },Date,Math,JSON,String,Number,Object,RegExp,Error,Set,setTimeout(fn){poll=fn;return 1;},clearTimeout(){}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','deploy/sonic-recorder-hook.js'),'utf8'),context);
  listeners.message({source:opener,origin:'http://platform.example',data:{type:'MIDSCENE_RECORDING_START',sessionId:'slow-ai',recordingToken:'t',endpoint:'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  await new Promise(setImmediate);
  socket.send(JSON.stringify({type:'debug',detail:'tap',point:'10,10'}));
  await new Promise(setImmediate);
  poll();
  await new Promise(setImmediate);
  socket.send(JSON.stringify({type:'debug',detail:'tap',point:'20,20'}));
  await new Promise(setImmediate);
  assert.equal(sent.filter(item=>item[0]==='platform').length, 2);
  assert.equal(notices.filter(item=>item.type==='MIDSCENE_RECORDING_ERROR').length, 0);
});

test('a delayed platform success does not unlock a pending next screenshot', async () => {
  const listeners = {};
  const sent = [];
  const opener = {postMessage() {}};
  class FakeWebSocket { constructor(url) { this.url=url; } send(data) { sent.push(['sonic',JSON.parse(data).detail]); } }
  const document = {body:{appendChild(){}},getElementById(){return {style:{}}},createElement(){return {style:{}}}};
  const window = {opener,WebSocket:FakeWebSocket,document,addEventListener(type,fn){listeners[type]=fn;}};
  const context = vm.createContext({window,document,URL,fetch:async(url,options)=>{
    if (url.endsWith('/bridge')) return {ok:true,json:async()=>({session:{pre_action_frame_status:'pending',steps:[{sequence:1,type:'tap',evidence_status:'captured',semantic_recognition_status:'recognized'}]}})};
    sent.push(['platform',JSON.parse(options.body).action]);
    return {ok:true,json:async()=>({step:{sequence:1}})};
  },Date,Math,JSON,String,Number,Object,RegExp,Error,Set,setTimeout(){return 1;},clearTimeout(){}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','deploy/sonic-recorder-hook.js'),'utf8'),context);
  listeners.message({source:opener,origin:'http://platform.example',data:{type:'MIDSCENE_RECORDING_START',sessionId:'stale-ack',recordingToken:'t',endpoint:'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  listeners.message({source:opener,data:{type:'MIDSCENE_RECORDING_BOUND',sessionId:'stale-ack',deviceId:'phone'}});
  socket.send(JSON.stringify({type:'debug',detail:'tap',point:'10,10'}));
  listeners.message({source:opener,data:{type:'MIDSCENE_RECORDING_STEP_CONFIRMED',sessionId:'stale-ack',sequence:1,success:true}});
  socket.send(JSON.stringify({type:'debug',detail:'tap',point:'20,20'}));
  await new Promise(setImmediate);
  assert.equal(sent.filter(item=>item[0]==='platform').length, 1);
});

test('early touches wait for an in-flight frame before requesting one replacement frame', async () => {
  const listeners = {};
  const sent = [];
  const pending = [];
  const bridgeCalls = [];
  const opener = {postMessage() {}};
  class FakeWebSocket { constructor(url) { this.url=url; } send(data) { sent.push(JSON.parse(data).detail); } }
  const document = {body:{appendChild(){}},getElementById(){return {style:{}}},createElement(){return {style:{}}}};
  const window = {opener,WebSocket:FakeWebSocket,document,addEventListener(type,fn){listeners[type]=fn;}};
  const responses = ['pending','pending','ready','pending'];
  const context = vm.createContext({window,document,URL,fetch:async(url,options)=>{
    if (url.endsWith('/bridge')) {
      const body = JSON.parse(options.body);
      bridgeCalls.push(body);
      return {ok:true,json:async()=>({session:{pre_action_frame_status:responses.shift() || 'pending',steps:[]}})};
    }
    throw new Error('early action must not be claimed as recorded');
  },Date,Math,JSON,String,Number,Object,RegExp,Error,Set,
  setTimeout(fn){pending.push(fn);return pending.length;},clearTimeout(){}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','deploy/sonic-recorder-hook.js'),'utf8'),context);
  listeners.message({source:opener,origin:'http://platform.example',data:{type:'MIDSCENE_RECORDING_START',sessionId:'early',recordingToken:'t',endpoint:'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  await new Promise(setImmediate);
  socket.send(JSON.stringify({type:'debug',detail:'tap',point:'10,10'}));
  socket.send(JSON.stringify({type:'debug',detail:'tap',point:'20,20'}));
  pending.shift()();
  await new Promise(setImmediate);
  pending.shift()();
  await new Promise(setImmediate);
  assert.deepEqual(bridgeCalls.map(call=>call.refresh_evidence), [true,false,false,true]);
  assert.equal(sent.length,2);
});

test('a rejected action resynchronizes evidence and an expired token stops the session', async () => {
  const listeners = {};
  const bridgeCalls = [];
  const notices = [];
  const pending = [];
  let actionStatus = 409;
  const opener = {postMessage(message){notices.push(message)}};
  class FakeWebSocket { constructor(url){this.url=url} send(){} }
  const document = {body:{appendChild(){}},getElementById(){return {style:{}}},createElement(){return {style:{}}}};
  const window = {opener,WebSocket:FakeWebSocket,document,addEventListener(type,fn){listeners[type]=fn;}};
  const context = vm.createContext({window,document,URL,fetch:async(url,options)=>{
    if (url.endsWith('/bridge')) {
      bridgeCalls.push(JSON.parse(options.body));
      return {ok:true,json:async()=>({session:{pre_action_frame_status:'ready',steps:[]}})};
    }
    return {ok:false,status:actionStatus};
  },Date,Math,JSON,String,Number,Object,RegExp,Error,Set,
  setTimeout(fn){pending.push(fn);return pending.length;},clearTimeout(){}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','deploy/sonic-recorder-hook.js'),'utf8'),context);
  listeners.message({source:opener,origin:'http://platform.example',data:{type:'MIDSCENE_RECORDING_START',sessionId:'reject',recordingToken:'t',endpoint:'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  await new Promise(setImmediate);
  socket.send(JSON.stringify({type:'debug',detail:'tap',point:'10,10'}));
  await new Promise(setImmediate);
  pending.shift()();
  await new Promise(setImmediate);
  assert.deepEqual(bridgeCalls.map(call=>call.refresh_evidence),[true,false,true]);
  actionStatus = 401;
  listeners.message({source:opener,origin:'http://platform.example',data:{type:'MIDSCENE_RECORDING_START',sessionId:'reject2',recordingToken:'t2',endpoint:'http://platform.example/api/device-recordings/action'}});
  await new Promise(setImmediate);
  socket.send(JSON.stringify({type:'debug',detail:'tap',point:'20,20'}));
  await new Promise(setImmediate);
  assert.ok(notices.some(item=>item.type==='MIDSCENE_RECORDING_ERROR' && /认证/.test(item.message)));
});

test('bridge accepts UI-node naming and warns when a recorded tap did not change the phone screen', async () => {
  const listeners = {};
  const badge = {style: {}, textContent: ''};
  const opener = {postMessage() {}};
  class FakeWebSocket { constructor(url) { this.url = url; } send() {} }
  const document = {body: {appendChild() {}}, getElementById() { return badge; }, createElement() { return badge; }};
  const window = {opener, WebSocket: FakeWebSocket, document, addEventListener(type, fn) { listeners[type] = fn; }};
  let poll;
  const context = vm.createContext({window, document, URL, fetch: async url => url.endsWith('/bridge')
    ? {ok: true, json: async () => ({session: {pre_action_frame_status: 'ready', steps: poll
      ? [{sequence: 1, type: 'tap', evidence_status: 'captured', ui_node: {text: '打印记录'}, screen_change_status: 'unchanged'}]
      : []}})}
    : {ok: true, json: async () => ({step: {sequence: 1}})}, Date, Math, JSON, String, Number, Object, RegExp, Error, Set,
  setTimeout(fn) {poll = fn; return 1;}, clearTimeout() {}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);
  listeners.message({source: opener, origin: 'http://platform.example', data: {type: 'MIDSCENE_RECORDING_START', sessionId: 's', recordingToken: 't', endpoint: 'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  await new Promise(setImmediate);
  socket.send(JSON.stringify({type: 'debug', detail: 'tap', point: '10,20'}));
  await new Promise(setImmediate);
  poll();
  await new Promise(setImmediate);
  assert.match(badge.textContent, /页面结构未变化/);
  assert.doesNotMatch(badge.textContent, /识别失败|记录成功/);
});

test('Sonic hook announces that it can receive a recording session after page load', () => {
  const messages = [];
  const opener = {postMessage(message, origin) { messages.push({message, origin}); }};
  class FakeWebSocket {}
  const window = {opener, WebSocket: FakeWebSocket, addEventListener() {}};
  const context = vm.createContext({window, URL, fetch: async () => ({ok: true}), Date, Math, JSON, String, Number, Object, RegExp, Error, setTimeout});

  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);

  assert.equal(messages[0].message.type, 'MIDSCENE_RECORDER_HOOK_READY');
  assert.equal(messages[0].origin, '*');
});

test('Sonic consumes and clears a fragment handoff even when cross-site isolation removes the opener', async () => {
  const listeners = {};
  const requests = [];
  const handoff = encodeURIComponent(JSON.stringify({
    sessionId: 's-fragment', recordingToken: 't-fragment', deviceId: '',
    endpoint: 'http://platform.example/api/device-recordings/action',
  }));
  class FakeWebSocket { constructor(url) { this.url = url; } send() {} }
  const window = {
    name: '', opener: null, WebSocket: FakeWebSocket,
    location: {hash: `#__MIDSCENE_RECORDING_HANDOFF__${handoff}`, pathname: '/Index/Devices', search: ''},
    history: {replaceState(_state, _title, url) { this.url = url; window.location.hash = ''; }},
    sessionStorage: {getItem() { return null; }, setItem() {}, removeItem() {}},
    localStorage: {getItem() { return null; }, setItem() {}, removeItem() {}},
    addEventListener(type, fn) { listeners[type] = fn; },
  };
  const context = vm.createContext({window, URL, fetch: async (url, options) => { requests.push({url, body: JSON.parse(options.body)}); return {ok: true, json: async () => ({session:{id:'s-fragment',status:'recording',device_id:'phone-fragment',pre_action_frame_status:'ready',steps:[]}})}; }, Date, Math, JSON, String, Number, Object, RegExp, Error, Set, setTimeout, clearTimeout, decodeURIComponent});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);

  assert.equal(window.location.hash, '');
  assert.equal(window.history.url, '/Index/Devices');
  new window.WebSocket('ws://agent/websockets/android/secret/phone-fragment/token');
  await Promise.resolve(); await Promise.resolve();
  assert.equal(requests[0].url, 'http://platform.example/api/device-recordings/bridge');
  assert.deepEqual(requests[0].body, {session_id:'s-fragment',recording_token:'t-fragment',device_id:'phone-fragment',refresh_evidence:true});
});

test('an already-open Sonic page consumes a later fragment handoff without reloading', () => {
  const listeners = {};
  const stored = [];
  class FakeWebSocket {}
  const window = {
    name: '', opener: null, WebSocket: FakeWebSocket,
    location: {hash: '', pathname: '/Index/Devices', search: ''},
    history: {replaceState() { window.location.hash = ''; }},
    sessionStorage: {getItem() { return null; }, setItem(key, value) { stored.push([key, value]); }, removeItem() {}},
    localStorage: {getItem() { return null; }, setItem() {}, removeItem() {}},
    addEventListener(type, fn) { listeners[type] = fn; },
  };
  const context = vm.createContext({window,URL,fetch:async()=>({ok:true}),Date,Math,JSON,String,Number,Object,RegExp,Error,Set,setTimeout,decodeURIComponent});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','deploy/sonic-recorder-hook.js'),'utf8'),context);
  const handoff = encodeURIComponent(JSON.stringify({sessionId:'later',recordingToken:'later-token',endpoint:'http://platform.example/api/device-recordings/action'}));
  window.location.hash = `#__MIDSCENE_RECORDING_HANDOFF__${handoff}`;
  listeners.hashchange();
  assert.equal(window.location.hash, '');
  assert.ok(stored.some(([, value]) => value.includes('later-token')));
});

test('Sonic carries the recorder handoff into the newly opened remote phone tab', () => {
  const opened = [];
  const handoff = encodeURIComponent(JSON.stringify({
    sessionId: 'session-1', recordingToken: 'secret', endpoint: 'http://platform.example/api/device-recordings/action',
  }));
  class FakeWebSocket {}
  const window = {
    opener: null, WebSocket: FakeWebSocket,
    location: {href:'http://sonic.example/Index/Devices',origin:'http://sonic.example',hash:`#__MIDSCENE_RECORDING_HANDOFF__${handoff}`,pathname:'/Index/Devices',search:''},
    history: {replaceState(){window.location.hash='';}},
    sessionStorage: {getItem(){return null},setItem(){},removeItem(){}},
    localStorage: {getItem(){return null},setItem(){},removeItem(){}},
    addEventListener(){},
    open(url, target, features) { opened.push({url, target, features}); return {}; },
  };
  const context = vm.createContext({window,URL,fetch:async()=>({ok:true}),Date,Math,JSON,String,Number,Object,RegExp,Error,Set,setTimeout,decodeURIComponent});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);

  window.open('/AndroidRemote/35', '_blank');

  assert.equal(opened.length, 1);
  const target = new URL(opened[0].url);
  assert.equal(`${target.origin}${target.pathname}`, 'http://sonic.example/AndroidRemote/35');
  assert.match(target.searchParams.get('midsceneRecorder') || '', /^\d+$/);
  assert.match(target.hash, /^#__MIDSCENE_RECORDING_HANDOFF__/);
  assert.doesNotMatch(target.search, /secret|session-1/);
});

test('Sonic keeps a cache-busting URL when its router enters the remote phone in the same tab', () => {
  const navigations = [];
  const handoff = encodeURIComponent(JSON.stringify({
    sessionId: 'session-router', recordingToken: 'secret-router', endpoint: 'http://platform.example/api/device-recordings/action',
  }));
  class FakeWebSocket {}
  const window = {
    opener: null, WebSocket: FakeWebSocket,
    location: {href:'http://sonic.example/Index/Devices',origin:'http://sonic.example',hash:`#__MIDSCENE_RECORDING_HANDOFF__${handoff}`,pathname:'/Index/Devices',search:''},
    history: {
      replaceState(_state, _title, url) { navigations.push(['replace', url]); window.location.hash=''; },
      pushState(_state, _title, url) { navigations.push(['push', url]); },
    },
    sessionStorage: {getItem(){return null},setItem(){},removeItem(){}},
    localStorage: {getItem(){return null},setItem(){},removeItem(){}},
    addEventListener(){},
  };
  const context = vm.createContext({window,URL,fetch:async()=>({ok:true}),Date,Math,JSON,String,Number,Object,RegExp,Error,Set,setTimeout,decodeURIComponent});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);

  window.history.pushState({}, '', '/AndroidRemote/35');

  const target = new URL(navigations.at(-1)[1]);
  assert.equal(`${target.origin}${target.pathname}`, 'http://sonic.example/AndroidRemote/35');
  assert.match(target.searchParams.get('midsceneRecorder') || '', /^\d+$/);
  assert.doesNotMatch(target.href, /secret-router|session-router/);
});

test('Sonic hook notifies the direct opener when that opener also has an opener', () => {
  const directMessages = [];
  const parentMessages = [];
  const parent = {postMessage(message) { parentMessages.push(message); }};
  const opener = {opener: parent, postMessage(message) { directMessages.push(message); }};
  class FakeWebSocket {}
  const window = {opener, WebSocket: FakeWebSocket, addEventListener() {}};
  const context = vm.createContext({window, URL, fetch: async () => ({ok: true}), Date, Math, JSON, String, Number, Object, RegExp, Error, setTimeout});

  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);

  assert.equal(directMessages[0].type, 'MIDSCENE_RECORDER_HOOK_READY');
  assert.equal(parentMessages[0].type, 'MIDSCENE_RECORDER_HOOK_READY');
});

test('an already-open Sonic device-center tab accepts a same-origin platform handoff without an opener', () => {
  const listeners = {};
  const sent = [];
  const platform = {postMessage(message) { sent.push(message); }};
  class FakeWebSocket { constructor(url) { this.url = url; } send() {} }
  const window = {opener: null, WebSocket: FakeWebSocket, addEventListener(type, fn) { listeners[type] = fn; }};
  const context = vm.createContext({window, URL, fetch: async () => ({ok: true}), Date, Math, JSON, String, Number, Object, RegExp, Error, Set, setTimeout});

  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);
  listeners.message({
    source: platform,
    origin: 'http://platform.example',
    data: {type: 'MIDSCENE_RECORDING_START', sessionId: 's-existing', recordingToken: 't-existing', deviceId: '', endpoint: 'http://platform.example/api/device-recordings/action'},
  });

  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone-existing/token');
  assert.equal(socket.url.includes('phone-existing'), true);
  assert.equal(sent.at(-1).type, 'MIDSCENE_RECORDING_READY');
});

test('remote phone tab never replays an action performed before the evidence handshake', async () => {
  const listeners = {};
  const sent = [];
  const timers = [];
  const platform = {postMessage(message) { sent.push(['platform', message]); }};
  const sonicCenter = {opener: platform};
  const stored = JSON.stringify({
    sessionId: 's-child', recordingToken: 'token-child', deviceId: '',
    endpoint: 'http://platform.example/api/device-recordings/action',
    platformOrigin: 'http://platform.example', bound: true, createdAt: Date.now(),
  });
  const window = {
    opener: sonicCenter,
    sessionStorage: {getItem(key) { return key === 'midsceneSonicRecording' ? stored : null; }, setItem() {}, removeItem() {}},
    addEventListener(type, fn) { listeners[type] = fn; },
  };
  class FakeWebSocket {
    constructor(url) { this.url = url; }
    send(data) { sent.push(['sonic', data]); }
  }
  window.WebSocket = FakeWebSocket;
  const context = vm.createContext({window, URL, fetch: async (url, options) => { sent.push([url.endsWith('/bridge') ? 'bridge' : 'mirror', JSON.parse(options.body)]); return {ok: true}; }, Date, Math, JSON, String, Number, Object, RegExp, Error, setTimeout(fn) { timers.push(fn); return timers.length; }, clearTimeout() {}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);

  const socket = new window.WebSocket('ws://agent/websockets/android/secret/ecbfd645/token');
  const ready = sent.findLast(item => item[0] === 'platform' && item[1].type === 'MIDSCENE_RECORDING_READY');
  assert.equal(ready[1].deviceId, 'ecbfd645');

  socket.send(JSON.stringify({type: 'debug', detail: 'tap', point: '12,34'}));
  assert.equal(sent.filter(item => item[0] === 'mirror').length, 0);
  assert.ok(sent.some(item => item[0] === 'platform' && item[1].type === 'MIDSCENE_RECORDING_ERROR' && /未记录/.test(item[1].message)));
  timers.shift()();
  assert.ok(sent.some(item => item[0] === 'bridge' && item[1].refresh_evidence === true));
  listeners.message({source: platform, data: {type: 'MIDSCENE_RECORDING_BOUND', sessionId: 's-child', deviceId: 'ecbfd645'}});
  await Promise.resolve();
  assert.equal(sent.filter(item => item[0] === 'mirror').length, 0);
});

test('remote phone tab restores a short-lived recording handoff from Sonic local storage', () => {
  const listeners = {};
  const sent = [];
  const platform = {postMessage(message) { sent.push(message); }};
  const sonicCenter = {opener: platform};
  const stored = JSON.stringify({
    sessionId: 's-shared', recordingToken: 'token-shared', deviceId: '',
    endpoint: 'http://platform.example/api/device-recordings/action',
    platformOrigin: 'http://platform.example', bound: false, createdAt: Date.now(),
  });
  const removed = [];
  const window = {
    opener: sonicCenter,
    sessionStorage: {getItem() { return null; }, setItem() {}, removeItem() {}},
    localStorage: {getItem(key) { return key === 'midsceneSonicRecording' ? stored : null; }, setItem() {}, removeItem(key) { removed.push(key); }},
    addEventListener(type, fn) { listeners[type] = fn; },
  };
  class FakeWebSocket { constructor(url) { this.url = url; } send() {} }
  window.WebSocket = FakeWebSocket;
  const context = vm.createContext({window, URL, fetch: async () => ({ok: true}), Date, Math, JSON, String, Number, Object, RegExp, Error, setTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);

  new window.WebSocket('ws://agent/websockets/android/secret/phone-shared/token');
  assert.equal(sent.at(-1).type, 'MIDSCENE_RECORDING_READY');
  listeners.message({source: platform, data: {type: 'MIDSCENE_RECORDING_BOUND', sessionId: 's-shared', deviceId: 'phone-shared'}});
  assert.ok(removed.includes('midsceneSonicRecording'));
});

test('an active remote tab older than the handoff window refreshes evidence on reconnect', async () => {
  const listeners = {};
  const requests = [];
  let stored = JSON.stringify({
    sessionId: 's-active', recordingToken: 't-active', deviceId: 'ecbfd645',
    endpoint: 'http://platform.example/api/device-recordings/action',
    platformOrigin: 'http://platform.example', bound: true, createdAt: Date.now() - 6 * 60 * 1000,
  });
  class FakeWebSocket { constructor(url) { this.url = url; } send() {} }
  const window = {
    WebSocket: FakeWebSocket, opener: null,
    sessionStorage: {getItem() { return stored; }, setItem(_key, value) { stored = value; }, removeItem() { stored = null; }},
    addEventListener(type, fn) { listeners[type] = fn; },
  };
  const context = vm.createContext({window, URL, fetch: async (url, options) => {
    requests.push({url, body: JSON.parse(options.body)});
    return {ok: true, json: async () => ({session: {pre_action_frame_status: 'pending', steps: []}})};
  }, Date, Math, JSON, String, Number, Object, RegExp, Error, Set, setTimeout(){return 1;}, clearTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);
  new window.WebSocket('ws://agent/websockets/android/key/ecbfd645/token');
  await new Promise(setImmediate);
  assert.equal(requests[0].body.refresh_evidence, true);
  assert.equal(requests[0].body.device_id, 'ecbfd645');
});
