const DEVICE_RECORDER_FIXED_DEVICE = '9888E0094F2A';
let deviceRecorderSession = null;
let deviceRecorderWindow = null;
let deviceRecorderPollTimer = null;
let deviceRecorderBridgeState = '等待 Sonic 接收录制会话';
let deviceRecorderGenerated = null;

function deviceRecorderAppOptions() {
  return (taskApps || []).filter(app => app.enabled !== false).map(app =>
    `<option value="${escapeHtml(app.package || '')}">${escapeHtml(app.name || app.package || '')}</option>`
  ).join('');
}

function fixedRecorderDevice() {
  return (runnerDevices || []).find(device => String(device.device_id || device.id || '') === DEVICE_RECORDER_FIXED_DEVICE) || null;
}

function recorderStatusText(session) {
  return ({recording: '录制中', paused: '已暂停', finished: '已结束', generating: '正在生成', cancelled: '已取消'})[session?.status] || '尚未开始';
}

function recorderModuleOptions(session) {
  const app = (taskApps || []).find(item => item.package === session?.app_package);
  return (app?.modules || []).filter(name => Object.prototype.hasOwnProperty.call(modules || {}, name)).map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
}

function recorderStepDescription(step) {
  return step.semantic_description || step.ui_node?.text || step.ui_node?.content_desc || step.ui_node?.resource_id || step.description || step.key || step.text || '';
}

function recorderStepNeedsMeaning(step) {
  return ['tap', 'text'].includes(step.type) && !recorderStepDescription(step);
}

function recorderTimelineItem(step) {
  const actionName = ({tap:'点击',swipe:'滑动',text:'输入',key:'按键',launch:'启动应用',checkpoint:'检查点'})[step.type] || step.type;
  const editor = recorderStepNeedsMeaning(step)
    ? `<div class="device-recorder-semantic"><input id="recorder-step-${escapeHtml(step.id)}" maxlength="200" placeholder="例如：提交按钮、搜索输入框"><button class="btn-sm" onclick="confirmRecorderStep('${escapeHtml(step.id)}')">确认控件</button></div>`
    : `<small>${escapeHtml(recorderStepDescription(step) || '等待语义证据')}</small>`;
  const evidence = step.evidence_status === 'pending' ? '证据待采集' : step.evidence_status === 'failed' ? '证据采集失败' : step.semantic_description ? '已人工确认' : step.evidence_status || '';
  return `<article class="${recorderStepNeedsMeaning(step) ? 'needs-confirmation' : ''}"><span>${step.sequence}</span><div><strong>${escapeHtml(actionName)}</strong>${editor}</div><em>${escapeHtml(evidence)}</em></article>`;
}

function renderDeviceRecorder() {
  const area = document.getElementById('editor-area');
  if (!area) return;
  const device = fixedRecorderDevice();
  const session = deviceRecorderSession;
  const steps = session?.steps || [];
  area.className = 'editor-area';
  area.innerHTML = `<div class="review-page device-recorder-page">
    <div class="review-head"><div><div class="workflow-kicker">SONIC 原生远控 · 操作旁路记录</div><h2>操作录制</h2><p>手机画面和触控继续由 Sonic 处理；平台只异步记录动作、关键证据并生成 YAML。</p></div><span class="status-pill ${session?.status === 'recording' ? 'success' : ''}">${escapeHtml(recorderStatusText(session))}</span></div>
    <div class="device-recorder-grid">
      <section class="review-panel"><h3>录制设置</h3>
        <label class="modal-label">应用</label><select id="device-recorder-app" ${session ? 'disabled' : ''}>${deviceRecorderAppOptions()}</select>
        <label class="modal-label">固定测试设备</label>
        <div class="device-recorder-device ${device ? 'online' : 'offline'}"><strong>${DEVICE_RECORDER_FIXED_DEVICE}</strong><span>${device ? `${escapeHtml(device.runner_id || '')} · 在线` : '当前 Runner 未上报在线，不能开始录制'}</span></div>
        <div class="review-actions">
          ${!session ? `<button class="btn-sm primary" ${device ? '' : 'disabled'} onclick="startDeviceRecording()">开始录制并打开 Sonic</button>` : ''}
          ${session?.status === 'recording' ? '<button class="btn-sm" onclick="openRecorderSonic()">打开 Sonic</button><button class="btn-sm danger" onclick="finishDeviceRecording()">结束录制</button>' : ''}
          ${session?.status === 'finished' ? '<button class="btn-sm ai" onclick="generateDeviceRecordingYaml()">生成并校验 YAML</button>' : ''}
        </div><div id="device-recorder-message" class="generate-hint">${session ? `会话：${escapeHtml(session.id)} · ${escapeHtml(deviceRecorderBridgeState)}` : '开始前请确认设备空闲。录制令牌不会写入网址。'}</div>
        ${session?.status === 'finished' ? `<div class="device-recorder-save"><label class="modal-label">用例名称</label><input id="device-recorder-task-name" value="录制生成用例"><label class="modal-label">保存到模块</label><select id="device-recorder-module">${recorderModuleOptions(session)}</select><label class="modal-label">YAML 文件名</label><input id="device-recorder-file" value="录制生成用例.yaml"><button class="btn-sm success" ${deviceRecorderGenerated ? '' : 'disabled'} onclick="saveDeviceRecordingYaml()">保存到用例资产</button></div>` : ''}
      </section>
      <section class="review-panel"><div class="review-head compact"><div><h3>步骤时间线</h3><p>${steps.length} 个动作</p></div>${session?.status === 'recording' ? '<button class="btn-sm" onclick="addRecorderCheckpoint()">添加检查点</button>' : ''}</div>
        <div class="device-recorder-timeline">${steps.length ? steps.map(recorderTimelineItem).join('') : '<div class="job-empty">还没有记录到操作。请在打开的 Sonic 页面中操作手机。</div>'}</div>
        <pre id="device-recorder-yaml" class="agent-artifact-box" hidden></pre>
      </section>
    </div></div>`;
}

async function showDeviceRecorder() {
  resetYamlToolbarForManager();
  if (typeof loadModules === 'function') await loadModules();
  if (typeof loadRunnerDevices === 'function') await loadRunnerDevices({force: true, quiet: true});
  renderDeviceRecorder();
}

async function startDeviceRecording() {
  const device = fixedRecorderDevice();
  const appPackage = document.getElementById('device-recorder-app')?.value || '';
  if (!device || !appPackage) return showToast(!device ? '固定测试设备当前不在线' : '请选择应用', 'error');
  try {
    const data = await apiRequest('/device-recordings', {method: 'POST', body: JSON.stringify({runner_id: device.runner_id, device_id: DEVICE_RECORDER_FIXED_DEVICE, app_package: appPackage})});
    deviceRecorderSession = data.session;
    deviceRecorderGenerated = null;
    sessionStorage.setItem('deviceRecorderToken', data.session.recording_token || '');
    sessionStorage.setItem('deviceRecorderSonicUrl', data.sonic_url || '');
    renderDeviceRecorder();
    openRecorderSonic();
    startDeviceRecorderPolling();
  } catch (error) { showToast(error.message || '开始录制失败', 'error'); }
}

function recorderHandshake() {
  if (!deviceRecorderWindow || deviceRecorderWindow.closed || !deviceRecorderSession) return;
  const sonicUrl = sessionStorage.getItem('deviceRecorderSonicUrl') || '';
  const origin = sonicUrl ? new URL(sonicUrl).origin : '*';
  deviceRecorderWindow.postMessage({type: 'MIDSCENE_RECORDING_START', sessionId: deviceRecorderSession.id, recordingToken: sessionStorage.getItem('deviceRecorderToken'), deviceId: DEVICE_RECORDER_FIXED_DEVICE, endpoint: `${location.origin}/api/device-recordings/action`}, origin);
}

function openRecorderSonic() {
  const url = sessionStorage.getItem('deviceRecorderSonicUrl');
  if (!url) return showToast('Sonic 地址未配置', 'error');
  deviceRecorderWindow = window.open(url, 'midscene-sonic-recorder');
  [800, 1800, 3500].forEach(delay => setTimeout(recorderHandshake, delay));
}

function startDeviceRecorderPolling() {
  clearInterval(deviceRecorderPollTimer);
  deviceRecorderPollTimer = setInterval(refreshDeviceRecording, 2000);
}

async function refreshDeviceRecording() {
  if (!deviceRecorderSession?.id) return;
  try {
    const data = deviceRecorderSession.status === 'recording'
      ? await apiRequest('/device-recordings/heartbeat', {method: 'POST', body: JSON.stringify({session_id: deviceRecorderSession.id})})
      : await apiRequest(`/device-recordings?id=${encodeURIComponent(deviceRecorderSession.id)}`);
    deviceRecorderSession = data.session;
    renderDeviceRecorder();
    if (deviceRecorderSession.status !== 'recording') clearInterval(deviceRecorderPollTimer);
  } catch (_) {}
}

window.addEventListener('message', event => {
  const sonicUrl = sessionStorage.getItem('deviceRecorderSonicUrl') || '';
  if (!sonicUrl || event.origin !== new URL(sonicUrl).origin || event.source !== deviceRecorderWindow) return;
  if (event.data?.type === 'MIDSCENE_RECORDING_READY' && event.data.sessionId === deviceRecorderSession?.id) {
    deviceRecorderBridgeState = 'Sonic 已接入录制';
    renderDeviceRecorder();
  } else if (event.data?.type === 'MIDSCENE_RECORDING_ERROR') {
    deviceRecorderBridgeState = `动作记录失败：${String(event.data.message || '未知错误')}`;
    renderDeviceRecorder();
  }
});

async function addRecorderCheckpoint() {
  const description = prompt('输入需要验证的页面结果');
  if (!description) return;
  const token = sessionStorage.getItem('deviceRecorderToken');
  await apiRequest('/device-recordings/action', {method: 'POST', body: JSON.stringify({session_id: deviceRecorderSession.id, recording_token: token, action: {event_id: `checkpoint-${Date.now()}`, type: 'checkpoint', checkpoint_kind: 'assert', description, device_id: DEVICE_RECORDER_FIXED_DEVICE}})});
  await refreshDeviceRecording();
}

async function finishDeviceRecording() {
  const data = await apiRequest('/device-recordings/finish', {method: 'POST', body: JSON.stringify({session_id: deviceRecorderSession.id})});
  deviceRecorderSession = data.session;
  deviceRecorderGenerated = null;
  clearInterval(deviceRecorderPollTimer);
  sessionStorage.removeItem('deviceRecorderToken');
  renderDeviceRecorder();
}

async function confirmRecorderStep(stepId) {
  const input = document.getElementById(`recorder-step-${stepId}`);
  const description = input?.value.trim() || '';
  if (!description) return showToast('请填写这个控件的名称或用途', 'error');
  try {
    const data = await apiRequest('/device-recordings/step', {method: 'POST', body: JSON.stringify({
      session_id: deviceRecorderSession.id, step_id: stepId, semantic_description: description,
    })});
    deviceRecorderSession = data.session;
    deviceRecorderGenerated = null;
    renderDeviceRecorder();
    showToast('控件说明已确认', 'success');
  } catch (error) { showToast(error.message || '确认控件失败', 'error'); }
}

async function generateDeviceRecordingYaml() {
  const taskName = document.getElementById('device-recorder-task-name')?.value.trim() || '录制生成用例';
  const data = await apiRequest('/device-recordings/generate', {method: 'POST', body: JSON.stringify({session_id: deviceRecorderSession.id, task_name: taskName})});
  deviceRecorderGenerated = data.result;
  const box = document.getElementById('device-recorder-yaml');
  box.hidden = false;
  box.textContent = data.result.yaml;
  showToast(data.result.can_debug ? 'YAML 已生成并通过基础校验' : `YAML 已生成，但还有 ${data.result.issues?.length || 1} 个问题需要处理`, data.result.can_debug ? 'success' : 'warn');
  const save = document.querySelector('.device-recorder-save button');
  if (save) save.disabled = false;
}

async function saveDeviceRecordingYaml() {
  if (!deviceRecorderGenerated?.yaml) return showToast('请先生成 YAML', 'error');
  const moduleName = document.getElementById('device-recorder-module')?.value || '';
  let fileName = document.getElementById('device-recorder-file')?.value.trim() || '';
  if (!moduleName || !fileName) return showToast('请选择模块并填写文件名', 'error');
  if (!/\.ya?ml$/i.test(fileName)) fileName += '.yaml';
  await apiRequest('/file', {method: 'POST', body: JSON.stringify({module: moduleName, file: fileName, content: deviceRecorderGenerated.yaml})});
  if (!modules[moduleName]) modules[moduleName] = [];
  if (!modules[moduleName].includes(fileName)) modules[moduleName].push(fileName);
  showToast(deviceRecorderGenerated.requires_confirmation ? '草稿已保存；确认有歧义步骤后再调试' : 'YAML 已保存到用例资产', deviceRecorderGenerated.requires_confirmation ? 'warn' : 'success');
  if (typeof openFile === 'function') openFile(moduleName, fileName);
}
