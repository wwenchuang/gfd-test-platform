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
  const context = vm.createContext({window, document: window.document, URL, fetch: async (url, options) => { order.push(['mirror', JSON.parse(options.body)]); return {ok: true}; }, Date, Math, JSON, String, Number, Object, RegExp, Error, setTimeout});
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
  assert.equal(order[0][0], 'sonic');
  assert.equal(order[1][0], 'mirror');
  assert.deepEqual(order[1][1].action.point, {x: 10, y: 20});
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
  const context = vm.createContext({window,document,URL,fetch:async()=>{order.push(['platform']);return {ok:true}},Date,Math,JSON,String,Number,Object,RegExp,Error,Set,setTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','deploy/sonic-recorder-hook.js'),'utf8'),context);
  listeners.message({source:opener,origin:'http://platform.example',data:{type:'MIDSCENE_RECORDING_START',sessionId:'s',recordingToken:'t',deviceId:'',endpoint:'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  listeners.message({source:opener,data:{type:'MIDSCENE_RECORDING_BOUND',sessionId:'s',deviceId:'phone'}});
  socket.send(JSON.stringify({type:'touch',detail:'down 10 20'}));
  socket.send(JSON.stringify({type:'touch',detail:'up 10 20'}));
  await Promise.resolve();
  assert.deepEqual(order.slice(0,2).map(item=>item[0]), ['sonic','sonic']);
  assert.equal(inspectedCanvases, 0);
  assert.ok(order.find(item=>item[0]==='platform'));
});

test('Sonic can mirror twenty confirmed taps without touching the video canvas or dropping phone commands', async () => {
  const listeners = {};
  const sent = [];
  let canvasReads = 0;
  const opener = {postMessage() {}};
  class FakeWebSocket { constructor(url){this.url=url} send(data){sent.push(['sonic',JSON.parse(data).detail])} }
  const document = {querySelectorAll(){canvasReads += 1;return []},body:{appendChild(){}},getElementById(){return {style:{}}},createElement(){return {style:{}}}};
  const window = {opener,WebSocket:FakeWebSocket,document,addEventListener(type,fn){listeners[type]=fn;}};
  const context = vm.createContext({window,document,URL,fetch:async(url,options)=>{sent.push(['platform',JSON.parse(options.body).action]);return {ok:true}},Date,Math,JSON,String,Number,Object,RegExp,Error,Set,setTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','deploy/sonic-recorder-hook.js'),'utf8'),context);
  listeners.message({source:opener,origin:'http://platform.example',data:{type:'MIDSCENE_RECORDING_START',sessionId:'stress',recordingToken:'t',endpoint:'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/phone/token');
  listeners.message({source:opener,data:{type:'MIDSCENE_RECORDING_BOUND',sessionId:'stress',deviceId:'phone'}});
  for(let index=0;index<20;index+=1){
    socket.send(JSON.stringify({type:'touch',detail:`down ${index} ${index}`}));
    socket.send(JSON.stringify({type:'touch',detail:`up ${index} ${index}`}));
    listeners.message({source:opener,data:{type:'MIDSCENE_RECORDING_STEP_CONFIRMED',sessionId:'stress',sequence:index+1,success:true}});
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
  const context = vm.createContext({window,document,URL,fetch:async(url,options)=>{sent.push(['platform',JSON.parse(options.body).action]);return {ok:true}},Date,Math,JSON,String,Number,Object,RegExp,Error,Set,setTimeout});
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
  const context = vm.createContext({window, URL, fetch: async (url, options) => { sent.push(['mirror', JSON.parse(options.body)]); return {ok: true}; }, Date, Math, JSON, String, Number, Object, RegExp, Error, setTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);

  const socket = new window.WebSocket('ws://agent/websockets/android/secret/ecbfd645/token');
  assert.equal(sent.at(-1)[0], 'platform');
  assert.equal(sent.at(-1)[1].type, 'MIDSCENE_RECORDING_READY');
  assert.equal(sent.at(-1)[1].deviceId, 'ecbfd645');

  socket.send(JSON.stringify({type: 'debug', detail: 'tap', point: '12,34'}));
  assert.equal(sent.filter(item => item[0] === 'mirror').length, 0);
  assert.ok(sent.some(item => item[0] === 'platform' && item[1].type === 'MIDSCENE_RECORDING_ERROR' && /未记录/.test(item[1].message)));
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
