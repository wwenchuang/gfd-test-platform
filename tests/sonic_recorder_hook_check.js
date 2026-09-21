const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('Sonic hook sends original control first and mirrors it asynchronously', async () => {
  const listeners = {};
  const order = [];
  const opener = {postMessage() {}};
  class FakeWebSocket {
    constructor(url) { this.url = url; }
    send(data) { order.push(['sonic', data]); }
  }
  const window = {
    opener, WebSocket: FakeWebSocket,
    addEventListener(type, fn) { listeners[type] = fn; },
  };
  const context = vm.createContext({window, URL, fetch: async (url, options) => { order.push(['mirror', JSON.parse(options.body)]); return {ok: true}; }, Date, Math, JSON, String, Number, Object, RegExp, Error, setTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'deploy/sonic-recorder-hook.js'), 'utf8'), context);
  listeners.message({source: opener, data: {type: 'MIDSCENE_RECORDING_START', sessionId: 's1', recordingToken: 't1', deviceId: '9888E0094F2A', endpoint: 'http://platform.example/api/device-recordings/action'}});
  const socket = new window.WebSocket('ws://agent/websockets/android/key/9888E0094F2A/token');
  socket.send(JSON.stringify({type: 'debug', detail: 'tap', point: '10,20'}));
  await Promise.resolve();
  assert.equal(order[0][0], 'sonic');
  assert.equal(order[1][0], 'mirror');
  assert.deepEqual(order[1][1].action.point, {x: 10, y: 20});
});
