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
  let mirroredCount = 0;
  let platformTarget = null;
  let awaitingRecognition = 0;
  const NativeWebSocket = window.WebSocket;
  const STORAGE_KEY = 'midsceneSonicRecording';
  const WINDOW_HANDOFF_PREFIX = '__MIDSCENE_RECORDING_HANDOFF__';
  const HASH_HANDOFF_PREFIX = '#__MIDSCENE_RECORDING_HANDOFF__';
  const HANDOFF_MAX_AGE_MS = 5 * 60 * 1000;
  let bridgePollTimer = null;

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
    const targets = platformTarget ? [platformTarget] : [];
    if (window.opener) {
      targets.push(window.opener);
      try {
        if (window.opener.opener && window.opener.opener !== window.opener) targets.push(window.opener.opener);
      } catch (_) {}
    }
    return [...new Set(targets)].filter(target => typeof target?.postMessage === 'function');
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

  function acceptHandoff(data, target) {
    const endpoint = new URL(data.endpoint);
    if (!String(data.sessionId || '') || !String(data.recordingToken || '') || !/^https?:$/.test(endpoint.protocol)) return false;
    recording = {
      sessionId: String(data.sessionId), recordingToken: String(data.recordingToken),
      deviceId: String(data.deviceId || ''), endpoint: endpoint.href, platformOrigin: endpoint.origin,
      bound: false, createdAt: Date.now(),
    };
    platformTarget = target || null;
    saveRecording();
    showRecorderStatus('已接收任务，请在 Sonic 选择手机');
    return true;
  }

  function consumeHashHandoff() {
    if (!String(window.location?.hash || '').startsWith(HASH_HANDOFF_PREFIX)) return false;
    try {
      const raw = String(window.location.hash).slice(HASH_HANDOFF_PREFIX.length);
      const accepted = acceptHandoff(JSON.parse(decodeURIComponent(raw)), window.opener || null);
      window.history?.replaceState?.(null, '', `${window.location.pathname || '/'}${window.location.search || ''}`);
      return accepted;
    } catch (_) {
      recording = null;
      saveRecording();
      return false;
    }
  }

  try {
    const inherited = window.sessionStorage?.getItem(STORAGE_KEY) || window.localStorage?.getItem(STORAGE_KEY);
    if (inherited) {
      recording = JSON.parse(inherited);
      // A refreshed Sonic page must complete a fresh platform/Runner handshake
      // before it may forward actions from this new browser context.
      if (recording) recording.bound = false;
      const createdAt = Number(recording?.createdAt || 0);
      if (!createdAt || Date.now() - createdAt > HANDOFF_MAX_AGE_MS) {
        recording = null;
        saveRecording();
      }
    }
  } catch (_) { recording = null; }

  // A URL fragment is not sent to nginx and is consumed before Sonic's module
  // bundle runs. Unlike window.name/opener it survives cross-site isolation.
  consumeHashHandoff();
  window.addEventListener('hashchange', consumeHashHandoff);

  // Retain window.name support for already-issued pages during a rolling
  // deployment, but new pages use the fragment handoff above.
  try {
    if (String(window.name || '').startsWith(WINDOW_HANDOFF_PREFIX)) {
      const raw = String(window.name).slice(WINDOW_HANDOFF_PREFIX.length);
      acceptHandoff(JSON.parse(decodeURIComponent(raw)), window.opener || null);
      window.name = 'midscene-sonic-recorder';
    }
  } catch (_) {
    recording = null;
    window.name = 'midscene-sonic-recorder';
    saveRecording();
  }

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

  function bridgeUrl() {
    return String(recording?.endpoint || '').replace(/\/action(?:\?.*)?$/, '/bridge');
  }

  function bridgeStepState(session) {
    const steps = Array.isArray(session?.steps) ? session.steps : [];
    const step = steps.find(item => Number(item.sequence || 0) === Number(awaitingRecognition || 0));
    if (!step) return awaitingRecognition ? 'pending' : 'ready';
    if (step.evidence_status === 'pending' || step.semantic_recognition_status === 'running' || session.pre_action_frame_status === 'pending') return 'pending';
    if (step.evidence_status === 'failed' || step.semantic_recognition_status === 'failed') return 'failed';
    if (step.type === 'tap' && !String(step.semantic_description || '').trim()) return 'failed';
    return session.pre_action_frame_status === 'ready' ? 'ready' : 'pending';
  }

  function scheduleBridgePoll() {
    if (bridgePollTimer || !recording?.deviceId) return;
    bridgePollTimer = setTimeout(() => { bridgePollTimer = null; syncBridge(); }, 1000);
  }

  function syncBridge(deviceId = recording?.deviceId) {
    if (!recording || !deviceId || !bridgeUrl()) return Promise.resolve();
    recording.deviceId = String(deviceId);
    saveRecording();
    return fetch(bridgeUrl(), {
      method: 'POST', mode: 'cors',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: recording.sessionId, recording_token: recording.recordingToken, device_id: recording.deviceId}),
    }).then(async response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (typeof response.json !== 'function') return null;
      const payload = await response.json();
      const session = payload?.session;
      if (!session) return null;
      mirroredCount = Math.max(mirroredCount, ...(session.steps || []).map(item => Number(item.sequence || 0)), 0);
      const state = bridgeStepState(session);
      if (state === 'ready') {
        recording.bound = session.pre_action_frame_status === 'ready';
        if (awaitingRecognition) showRecorderStatus(`第 ${awaitingRecognition} 步记录成功，可以继续操作`, 'active');
        else if (recording.bound) showRecorderStatus(`录制中，已同步 ${mirroredCount} 步，可以开始操作`, 'active');
        awaitingRecognition = 0;
        saveRecording();
      } else if (state === 'failed') {
        showRecorderStatus(`第 ${awaitingRecognition} 步识别失败，已保留；可继续操作并稍后在平台修正`, 'error');
        awaitingRecognition = 0;
      } else {
        recording.bound = false;
        showRecorderStatus(awaitingRecognition ? `第 ${awaitingRecognition} 步正在采集截图并识别，请暂缓下一步` : 'Runner 正在准备真实点击前画面', 'waiting');
        scheduleBridgePoll();
      }
      return session;
    }).catch(error => {
      recording.bound = false;
      showRecorderStatus(`录制桥接失败：${String(error.message || error)}`, 'error');
      notifyPlatform({type: 'MIDSCENE_RECORDING_ERROR', message: String(error.message || error)});
      scheduleBridgePoll();
      return null;
    });
  }

  function mirror(action) {
    if (!recording || !action) return;
    if (!recording.bound) {
      const message = '录制证据尚未准备，本次操作已在手机执行但未记录；请等待“可以开始操作”后重试';
      showRecorderStatus(message, 'error');
      notifyPlatform({type: 'MIDSCENE_RECORDING_ERROR', message});
      return;
    }
    if (awaitingRecognition) {
      const message = `上一操作仍在确认，本次操作已在手机执行但未记录；请等待“记录成功”后重试`;
      showRecorderStatus(message, 'error');
      notifyPlatform({type: 'MIDSCENE_RECORDING_ERROR', message});
      return;
    }
    mirroredCount += 1;
    awaitingRecognition = mirroredCount;
    showRecorderStatus(`第 ${mirroredCount} 步已收到，正在采集截图并识别，请暂缓下一步`, 'waiting');
    const body = JSON.stringify({
      session_id: recording.sessionId,
      recording_token: recording.recordingToken,
      action: {...action, event_id: id(), device_id: recording.deviceId},
    });
    fetch(recording.endpoint, {
      method: 'POST', mode: 'cors', keepalive: !action.evidence_content_base64,
      headers: {'Content-Type': 'application/json'}, body,
    }).then(async response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (typeof response.json === 'function') {
        const payload = await response.json();
        awaitingRecognition = Number(payload?.step?.sequence || awaitingRecognition);
      }
      scheduleBridgePoll();
    }).catch(error => {
      awaitingRecognition = 0;
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
      syncBridge(recording.deviceId);
    }
    const nativeSend = socket.send;
    socket.send = function (data) {
      let action = null;
      try {
        if (recording && socket === activeDeviceSocket && typeof data === 'string') {
          action = mirroredAction(JSON.parse(data));
        }
      } catch (_) {}
      // Sonic owns phone rendering and touch delivery. The platform mirrors only
      // action metadata; the Windows Runner supplies stable pre-action evidence.
      nativeSend.call(socket, data);
      mirror(action);
    };
    return socket;
  };
  window.WebSocket.prototype = NativeWebSocket.prototype;
  Object.assign(window.WebSocket, {CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3});

  platformWindows().forEach(target => target.postMessage({type: 'MIDSCENE_RECORDER_HOOK_READY'}, '*'));

  window.addEventListener('message', event => {
    const data = event.data || {};
    if (data.type === 'MIDSCENE_RECORDING_START') {
      try {
        if (new URL(data.endpoint).origin !== event.origin) return;
        platformTarget = event.source;
      } catch (_) { return; }
    } else if (!platformWindows().includes(event.source)) return;
    if (data.type === 'MIDSCENE_RECORDING_BOUND' && recording && data.sessionId === recording.sessionId && data.deviceId === recording.deviceId) {
      recording.bound = true;
      saveRecording();
      clearSharedHandoff();
      showRecorderStatus(`录制中，已同步 ${mirroredCount} 步`, 'active');
      return;
    }
    if (data.type === 'MIDSCENE_RECORDING_STEP_CONFIRMED' && recording && data.sessionId === recording.sessionId) {
      const sequence = Number(data.sequence || 0);
      awaitingRecognition = 0;
      if (data.success) {
        showRecorderStatus(`第 ${sequence} 步记录成功，可以继续操作`, 'active');
      } else {
        showRecorderStatus(`第 ${sequence} 步识别失败，已保留；可以继续操作并稍后在平台修正`, 'error');
      }
      return;
    }
    if (data.type !== 'MIDSCENE_RECORDING_START') return;
    try {
      const endpoint = new URL(data.endpoint);
      const sameSession = recording?.sessionId === String(data.sessionId || '');
      recording = {
        sessionId: String(data.sessionId || ''), recordingToken: String(data.recordingToken || ''),
        deviceId: String(data.deviceId || recording?.deviceId || ''), endpoint: endpoint.href, platformOrigin: endpoint.origin,
        bound: sameSession ? Boolean(recording?.bound) : false, createdAt: sameSession ? Number(recording?.createdAt || Date.now()) : Date.now(),
      };
      if (!recording.sessionId || !recording.recordingToken) recording = null;
      mirroredCount = 0;
      saveRecording();
      if (recording) {
        showRecorderStatus(recording.deviceId ? '已恢复录制任务，正在确认手机状态' : '已接收任务，请在 Sonic 选择手机');
        if (recording.deviceId) syncBridge();
      }
    } catch (_) { recording = null; saveRecording(); }
  });
})();
