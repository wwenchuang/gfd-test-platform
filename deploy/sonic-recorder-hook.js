/* Sonic 2.7.2 action mirror for Midscene Task Platform.
 * Load before Sonic's application bundle. It never proxies or delays WebSocket traffic.
 */
(function () {
  'use strict';
  if (window.__MIDSCENE_RECORDER_HOOK__) return;
  window.__MIDSCENE_RECORDER_HOOK__ = true;

  let recording = null;
  let touch = null;
  let activeDeviceSocket = null;
  let pendingActions = [];
  let mirroredCount = 0;
  const NativeWebSocket = window.WebSocket;
  const STORAGE_KEY = 'midsceneSonicRecording';
  const HANDOFF_MAX_AGE_MS = 5 * 60 * 1000;

  function showRecorderStatus(message, state = 'waiting') {
    if (typeof document === 'undefined') return;
    const mount = () => {
      if (!document.body) return;
      let badge = document.getElementById('midscene-recorder-status');
      if (!badge) {
        badge = document.createElement('div');
        badge.id = 'midscene-recorder-status';
        badge.style.cssText = 'position:fixed;right:20px;bottom:20px;z-index:2147483647;padding:10px 14px;border-radius:10px;color:#fff;font:14px/1.4 sans-serif;box-shadow:0 4px 16px #0005;pointer-events:none';
        document.body.appendChild(badge);
      }
      badge.style.background = state === 'error' ? '#b42318' : state === 'active' ? '#067647' : '#344054';
      badge.textContent = `平台录制：${message}`;
    };
    if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount, {once: true});
  }

  function platformWindows() {
    if (!window.opener) return [];
    const targets = [window.opener];
    try {
      if (window.opener.opener && window.opener.opener !== window.opener) targets.push(window.opener.opener);
    } catch (_) {}
    return targets.filter(target => typeof target?.postMessage === 'function');
  }

  function notifyPlatform(message) {
    if (!recording?.platformOrigin) return;
    platformWindows().forEach(target => target.postMessage(message, recording.platformOrigin));
  }

  function saveRecording() {
    try {
      if (recording) {
        const value = JSON.stringify(recording);
        window.sessionStorage?.setItem(STORAGE_KEY, value);
        window.localStorage?.setItem(STORAGE_KEY, value);
      } else {
        window.sessionStorage?.removeItem(STORAGE_KEY);
        window.localStorage?.removeItem(STORAGE_KEY);
      }
    } catch (_) {}
  }

  function clearSharedHandoff() {
    try { window.localStorage?.removeItem(STORAGE_KEY); } catch (_) {}
  }

  try {
    const inherited = window.sessionStorage?.getItem(STORAGE_KEY) || window.localStorage?.getItem(STORAGE_KEY);
    if (inherited) {
      recording = JSON.parse(inherited);
      const createdAt = Number(recording?.createdAt || 0);
      if (!createdAt || Date.now() - createdAt > HANDOFF_MAX_AGE_MS) {
        recording = null;
        saveRecording();
      }
    }
  } catch (_) { recording = null; }

  function id() {
    return `sonic-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function point(value) {
    const parts = String(value || '').split(',').map(Number);
    return parts.length === 2 && parts.every(Number.isFinite) ? {x: parts[0], y: parts[1]} : null;
  }

  function socketDeviceId(url) {
    const match = String(url || '').match(/\/websockets\/android\/[^/]+\/([^/?#]+)/i);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function mirror(action) {
    if (!recording || !action) return;
    if (!recording.bound) {
      pendingActions.push(action);
      pendingActions = pendingActions.slice(-20);
      showRecorderStatus(`已连接手机，等待平台确认（暂存 ${pendingActions.length} 步）`);
      return;
    }
    mirroredCount += 1;
    showRecorderStatus(`录制中，已同步 ${mirroredCount} 步`, 'active');
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
      showRecorderStatus(`第 ${mirroredCount} 步同步失败`, 'error');
      notifyPlatform({type: 'MIDSCENE_RECORDING_ERROR', message: String(error.message || error)});
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
    const target = String(url || '');
    const connectedDeviceId = socketDeviceId(target);
    const isSelectedDevice = Boolean(recording && connectedDeviceId && (!recording.deviceId || connectedDeviceId === recording.deviceId));
    if (isSelectedDevice && !/\/screen\/|\/terminal\//.test(target)) {
      recording.deviceId = connectedDeviceId;
      activeDeviceSocket = socket;
      saveRecording();
      showRecorderStatus(`已进入手机 ${recording.deviceId}，等待平台确认`);
      notifyPlatform({type: 'MIDSCENE_RECORDING_READY', sessionId: recording.sessionId, deviceId: recording.deviceId});
    }
    const nativeSend = socket.send;
    socket.send = function (data) {
      nativeSend.call(socket, data);
      try {
        if (recording && socket === activeDeviceSocket && typeof data === 'string') {
          mirror(mirroredAction(JSON.parse(data)));
        }
      } catch (_) {}
    };
    return socket;
  };
  window.WebSocket.prototype = NativeWebSocket.prototype;
  Object.assign(window.WebSocket, {CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3});

  platformWindows().forEach(target => target.postMessage({type: 'MIDSCENE_RECORDER_HOOK_READY'}, '*'));

  window.addEventListener('message', event => {
    const data = event.data || {};
    if (!platformWindows().includes(event.source)) return;
    if (data.type === 'MIDSCENE_RECORDING_BOUND' && recording && data.sessionId === recording.sessionId && data.deviceId === recording.deviceId) {
      recording.bound = true;
      saveRecording();
      clearSharedHandoff();
      showRecorderStatus(`录制中，已同步 ${mirroredCount} 步`, 'active');
      const queued = pendingActions;
      pendingActions = [];
      queued.forEach(mirror);
      return;
    }
    if (data.type !== 'MIDSCENE_RECORDING_START') return;
    try {
      const endpoint = new URL(data.endpoint);
      recording = {
        sessionId: String(data.sessionId || ''), recordingToken: String(data.recordingToken || ''),
        deviceId: String(data.deviceId || ''), endpoint: endpoint.href, platformOrigin: endpoint.origin, bound: Boolean(data.deviceId),
        createdAt: Date.now(),
      };
      if (!recording.sessionId || !recording.recordingToken) recording = null;
      pendingActions = [];
      mirroredCount = 0;
      saveRecording();
      if (recording) showRecorderStatus('已接收任务，请在 Sonic 选择手机');
    } catch (_) { recording = null; saveRecording(); }
  });
})();
