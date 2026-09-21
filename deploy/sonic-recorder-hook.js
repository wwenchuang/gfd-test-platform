/* Sonic 2.7.2 action mirror for Midscene Task Platform.
 * Load before Sonic's application bundle. It never proxies or delays WebSocket traffic.
 */
(function () {
  'use strict';
  if (window.__MIDSCENE_RECORDER_HOOK__) return;
  window.__MIDSCENE_RECORDER_HOOK__ = true;

  let recording = null;
  let touch = null;
  const NativeWebSocket = window.WebSocket;

  function id() {
    return `sonic-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function point(value) {
    const parts = String(value || '').split(',').map(Number);
    return parts.length === 2 && parts.every(Number.isFinite) ? {x: parts[0], y: parts[1]} : null;
  }

  function mirror(action) {
    if (!recording || !action) return;
    const body = JSON.stringify({
      session_id: recording.sessionId,
      recording_token: recording.recordingToken,
      action: {...action, event_id: id(), device_id: recording.deviceId},
    });
    fetch(recording.endpoint, {
      method: 'POST', mode: 'cors', keepalive: true,
      headers: {'Content-Type': 'application/json'}, body,
    }).then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    }).catch(error => {
      if (window.opener) window.opener.postMessage({type: 'MIDSCENE_RECORDING_ERROR', message: String(error.message || error)}, recording.platformOrigin);
    });
  }

  function mirroredAction(message) {
    if (!message || typeof message !== 'object') return null;
    if (message.type === 'debug' && message.detail === 'tap') {
      const value = point(message.point); return value ? {type: 'tap', point: value} : null;
    }
    if (message.type === 'debug' && message.detail === 'swipe') {
      const start = point(message.pointA), end = point(message.pointB);
      return start && end ? {type: 'swipe', start, end} : null;
    }
    if (message.type === 'text') {
      if (message.detail === 'CODE_AC_BACK') return {type: 'key', key: 'BACK'};
      if (message.detail === 'CODE_AC_ENTER') return {type: 'key', key: 'ENTER'};
      return {type: 'text', text: String(message.detail || '').slice(0, 500)};
    }
    if (message.type !== 'touch') return null;
    const parts = String(message.detail || '').trim().split(/\s+/);
    if (parts[0] === 'down') {
      touch = {start: {x: Number(parts[1]), y: Number(parts[2])}, end: {x: Number(parts[1]), y: Number(parts[2])}, started: Date.now()};
    } else if (parts[0] === 'move' && touch) {
      touch.end = {x: Number(parts[1]), y: Number(parts[2])};
    } else if (parts[0] === 'up' && touch) {
      const current = touch; touch = null;
      const distance = Math.hypot(current.end.x - current.start.x, current.end.y - current.start.y);
      return distance < 12 ? {type: 'tap', point: current.end} : {type: 'swipe', start: current.start, end: current.end, duration_ms: Date.now() - current.started};
    }
    return null;
  }

  window.WebSocket = function (url, protocols) {
    const socket = protocols === undefined ? new NativeWebSocket(url) : new NativeWebSocket(url, protocols);
    const nativeSend = socket.send;
    socket.send = function (data) {
      nativeSend.call(socket, data);
      try {
        const target = String(url || '');
        if (recording && /\/websockets\/android\//.test(target) && !/\/screen\/|\/terminal\//.test(target) && typeof data === 'string') {
          mirror(mirroredAction(JSON.parse(data)));
        }
      } catch (_) {}
    };
    return socket;
  };
  window.WebSocket.prototype = NativeWebSocket.prototype;
  Object.assign(window.WebSocket, {CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3});

  window.addEventListener('message', event => {
    const data = event.data || {};
    if (event.source !== window.opener || data.type !== 'MIDSCENE_RECORDING_START') return;
    try {
      const endpoint = new URL(data.endpoint);
      recording = {
        sessionId: String(data.sessionId || ''), recordingToken: String(data.recordingToken || ''),
        deviceId: String(data.deviceId || ''), endpoint: endpoint.href, platformOrigin: endpoint.origin,
      };
      if (!recording.sessionId || !recording.recordingToken || !recording.deviceId) recording = null;
      if (recording && window.opener) window.opener.postMessage({type: 'MIDSCENE_RECORDING_READY', sessionId: recording.sessionId}, recording.platformOrigin);
    } catch (_) { recording = null; }
  });
})();
