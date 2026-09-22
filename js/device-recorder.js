let deviceRecorderSession = null;
let deviceRecorderWindow = null;
let deviceRecorderPollTimer = null;
let deviceRecorderBridgeState = '等待 Sonic 接收录制会话';
let deviceRecorderGenerated = null;
let deviceRecorderFileNameEdited = false;
let deviceRecorderRecognitionInFlight = false;

function deviceRecorderAppOptions() {
  return (taskApps || []).filter(app => app.enabled !== false).map(app =>
    `<option value="${escapeHtml(app.package || '')}">${escapeHtml(app.name || app.package || '')}</option>`
  ).join('');
}

function recorderDeviceCards(session) {
  const devices = (runnerDevices || []).filter(device => !['9888E0094F2A', '18CEDF5BA7B2'].includes(String(device.device_id || '').toUpperCase()));
  if (!devices.length) return '<div class="job-empty">暂无在线 Android 手机。请启动 Windows Runner 并在 Sonic 确认手机在线后刷新。</div>';
  return `<div class="device-recorder-phone-grid">${devices.map(device => {
    const busy = device.usage_status && device.usage_status !== 'idle';
    const stateClass = busy ? 'busy' : 'idle';
    const current = session?.device_id === device.device_id;
    return `<div class="device-recorder-phone ${busy ? 'busy' : ''} ${current ? 'selected' : ''}"><span class="device-recorder-phone-status ${stateClass}" aria-label="${busy ? '不可用' : '空闲可用'}"></span><span><strong>${escapeHtml(device.model || device.name || 'Android 手机')}</strong><small>${escapeHtml(device.device_id || '')}</small><small>${escapeHtml(device.usage_label || (busy ? '当前不可用' : '空闲'))} · ${escapeHtml(device.runner_id || '')}</small></span></div>`;
  }).join('')}</div>`;
}

function recorderStatusText(session) {
  return ({recording: '录制中', paused: '已暂停', finished: '已结束', generating: '正在生成', cancelled: '已取消'})[session?.status] || '尚未开始';
}

function recorderModuleOptions(session) {
  const app = (taskApps || []).find(item => item.package === session?.app_package);
  return (app?.modules || []).map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
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
  const evidence = step.evidence_status === 'pending' ? '证据待采集'
    : step.evidence_status === 'failed' ? '证据采集失败'
    : step.semantic_recognition_status === 'running' ? '正在自动识别'
    : step.semantic_recognition_status === 'failed' ? '自动识别失败，可手工补充'
    : step.semantic_source === 'ai_visual' ? `视觉识别 ${Math.round((step.semantic_confidence || 0) * 100)}%`
    : step.semantic_description ? '已人工确认' : step.evidence_status || '';
  return `<article class="${recorderStepNeedsMeaning(step) ? 'needs-confirmation' : ''}"><span>${step.sequence}</span><div><strong>${escapeHtml(actionName)}</strong>${editor}<details class="device-recorder-step-tools"><summary>编辑或删除</summary><div><input id="recorder-edit-${escapeHtml(step.id)}" maxlength="200" value="${escapeHtml(recorderStepDescription(step))}" placeholder="补充操作说明"><button class="btn-sm" onclick="editRecorderStep('${escapeHtml(step.id)}')">保存说明</button><button class="btn-sm danger" data-action="delete-recording-step" onclick="deleteRecorderStep('${escapeHtml(step.id)}')">删除</button></div></details></div><em>${escapeHtml(evidence)}</em></article>`;
}

function recorderCanGenerate(session) {
  return session?.status === 'finished'
    && (session.steps || []).length > 0
    && !(session.steps || []).some(step => step.evidence_status === 'pending' || recorderStepNeedsMeaning(step));
}

function recorderEvidencePending(session) {
  return (session?.steps || []).some(step => step.evidence_status === 'pending');
}

function recorderRecognitionPending(session) {
  return (session?.steps || []).some(step => step.evidence_status === 'captured'
    && recorderStepNeedsMeaning(step)
    && step.semantic_recognition_status !== 'failed');
}

function recorderGenerateLabel(session) {
  if (recorderEvidencePending(session) || recorderRecognitionPending(session)) return '等待自动识别';
  if ((session?.steps || []).some(recorderStepNeedsMeaning)) return '请补充未识别步骤';
  return '生成并校验 YAML';
}

function renderDeviceRecorder() {
  const area = document.getElementById('editor-area');
  if (!area) return;
  const session = deviceRecorderSession;
  const steps = session?.steps || [];
  area.className = 'editor-area';
  area.innerHTML = `<div class="review-page device-recorder-page">
    <div class="review-head"><div><div class="workflow-kicker">SONIC 原生远控 · 操作旁路记录</div><h2>操作录制</h2><p>手机画面和触控继续由 Sonic 处理；平台只记录你在所选手机上的真实操作。应用用于生成启动步骤，与手机分配互不绑定。</p></div><div class="review-actions"><button class="btn-sm" onclick="leaveDeviceRecorder()">返回用例资产</button><span class="status-pill ${session?.status === 'recording' ? 'success' : ''}">${escapeHtml(recorderStatusText(session))}</span></div></div>
    <div class="device-recorder-grid">
      <section class="review-panel"><h3>录制设置</h3>
        <label class="modal-label">应用</label><select id="device-recorder-app" ${session ? 'disabled' : ''}>${deviceRecorderAppOptions()}</select>
        <label class="modal-label">手机在线状态</label>
        ${recorderDeviceCards(session)}
        <div class="generate-hint">这里只展示状态，不再重复选择。点击开始后，在 Sonic 中选择一次你要操作的手机；平台以 Sonic 实际打开的手机作为录制对象。</div>
        <div class="review-actions">
          ${!session ? `<button class="btn-sm primary" data-action="start-recording" ${(runnerDevices || []).some(device => !device.usage_status || device.usage_status === 'idle') ? '' : 'disabled'} onclick="startDeviceRecording()">前往 Sonic 选择手机并开始</button>` : ''}
          ${session?.status === 'recording' ? '<button class="btn-sm" onclick="openRecorderSonic()">打开所选手机</button><button class="btn-sm" onclick="finishDeviceRecording()">结束录制</button><button class="btn-sm danger" onclick="cancelDeviceRecording()">取消本次录制</button>' : ''}
          ${session?.status === 'finished' ? `<button class="btn-sm ai" data-action="generate-recording-yaml" ${recorderCanGenerate(session) ? '' : 'disabled'} onclick="generateDeviceRecordingYaml()">${recorderGenerateLabel(session)}</button>` : ''}
        </div><div id="device-recorder-message" class="generate-hint">${session ? `手机：${escapeHtml(session.device_id || '等待在 Sonic 选择')} · 会话：${escapeHtml(session.id)} · ${escapeHtml(deviceRecorderBridgeState)}` : '录制手机只在 Sonic 选择一次；录制令牌不会写入网址。'}</div>
        ${session?.status === 'finished' ? `<div class="device-recorder-save"><label class="modal-label">用例名称</label><input id="device-recorder-task-name" value="录制生成用例" oninput="syncRecorderFileName(this.value)"><label class="modal-label">保存到模块</label><select id="device-recorder-module">${recorderModuleOptions(session)}</select>${recorderModuleOptions(session) ? '' : '<div class="agent-risk show">当前应用没有已关联模块，请先到应用配置关联模块。</div>'}<label class="modal-label">YAML 文件名</label><input id="device-recorder-file" value="录制生成用例.yaml" oninput="deviceRecorderFileNameEdited=true"><button class="btn-sm success" ${deviceRecorderGenerated && recorderModuleOptions(session) ? '' : 'disabled'} onclick="saveDeviceRecordingYaml()">保存到用例资产</button></div>` : ''}
      </section>
      <section class="review-panel"><div class="review-head compact"><div><h3>步骤时间线</h3><p>${steps.length} 个动作</p></div>${session?.status === 'recording' ? '<button class="btn-sm" onclick="addRecorderCheckpoint()">添加检查点</button>' : ''}</div>
        <div class="device-recorder-timeline">${steps.length ? steps.map(recorderTimelineItem).join('') : '<div class="job-empty">还没有记录到操作。请在打开的 Sonic 页面中操作手机。</div>'}</div>
        <pre id="device-recorder-yaml" class="agent-artifact-box" hidden></pre>
      </section>
    </div></div>`;
}

function leaveDeviceRecorder() {
  clearInterval(deviceRecorderPollTimer);
  if (typeof activateWorkflow === 'function') activateWorkflow('assets');
}

function syncRecorderFileName(taskName) {
  if (deviceRecorderFileNameEdited) return;
  const file = document.getElementById('device-recorder-file');
  if (!file) return;
  const safe = String(taskName || '').trim().replace(/[\\/:*?"<>|]+/g, '-');
  file.value = `${safe || '录制生成用例'}.yaml`;
}

async function showDeviceRecorder() {
  resetYamlToolbarForManager();
  if (typeof loadModules === 'function') await loadModules();
  if (typeof loadRunnerDevices === 'function') await loadRunnerDevices({force: true, quiet: true});
  renderDeviceRecorder();
}

async function startDeviceRecording() {
  const appPackage = document.getElementById('device-recorder-app')?.value || '';
  const available = (runnerDevices || []).some(device => !device.usage_status || device.usage_status === 'idle');
  if (!available || !appPackage) return showToast(!available ? '当前没有空闲 Android 手机' : '请选择应用', 'error');
  try {
    const data = await apiRequest('/device-recordings', {method: 'POST', body: JSON.stringify({app_package: appPackage})});
    deviceRecorderSession = data.session;
    deviceRecorderGenerated = null;
    deviceRecorderFileNameEdited = false;
    sessionStorage.setItem('deviceRecorderToken', data.session.recording_token || '');
    sessionStorage.setItem('deviceRecorderSonicUrl', data.sonic_url || '');
    renderDeviceRecorder();
    openRecorderSonic();
    startDeviceRecorderPolling();
  } catch (error) { showToast(error.message || '开始录制失败', 'error'); }
}

function recorderHandshake(target = deviceRecorderWindow) {
  if (!target || target.closed || !deviceRecorderSession) return;
  const sonicUrl = sessionStorage.getItem('deviceRecorderSonicUrl') || '';
  const origin = sonicUrl ? new URL(sonicUrl).origin : '*';
  target.postMessage({type: 'MIDSCENE_RECORDING_START', sessionId: deviceRecorderSession.id, recordingToken: sessionStorage.getItem('deviceRecorderToken'), deviceId: deviceRecorderSession.device_id || '', endpoint: `${location.origin}/api/device-recordings/action`}, origin);
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
    await maybeRecognizeDeviceRecording();
    if (deviceRecorderSession.status !== 'recording' && !recorderEvidencePending(deviceRecorderSession) && !recorderRecognitionPending(deviceRecorderSession)) {
      clearInterval(deviceRecorderPollTimer);
    }
  } catch (_) {}
}

async function maybeRecognizeDeviceRecording() {
  if (deviceRecorderRecognitionInFlight || !deviceRecorderSession?.id || !recorderRecognitionPending(deviceRecorderSession)) return;
  deviceRecorderRecognitionInFlight = true;
  try {
    const data = await apiRequest('/device-recordings/recognize', {method: 'POST', body: JSON.stringify({session_id: deviceRecorderSession.id})});
    deviceRecorderSession = data.session;
    renderDeviceRecorder();
  } catch (error) {
    showToast(error.message || '自动识别手机控件失败，可手工补充', 'warn');
  } finally {
    deviceRecorderRecognitionInFlight = false;
  }
}

async function handleDeviceRecorderMessage(event) {
  const sonicUrl = sessionStorage.getItem('deviceRecorderSonicUrl') || '';
  if (!sonicUrl || event.origin !== new URL(sonicUrl).origin) return;
  if (event.data?.type === 'MIDSCENE_RECORDER_HOOK_READY') {
    deviceRecorderWindow = event.source || deviceRecorderWindow;
    recorderHandshake(deviceRecorderWindow);
  } else if (event.data?.type === 'MIDSCENE_RECORDING_READY' && event.data.sessionId === deviceRecorderSession?.id) {
    try {
      const data = await apiRequest('/device-recordings/bind', {method: 'POST', body: JSON.stringify({session_id: deviceRecorderSession.id, device_id: event.data.deviceId})});
      deviceRecorderSession = data.session;
      event.source?.postMessage({type: 'MIDSCENE_RECORDING_BOUND', sessionId: deviceRecorderSession.id, deviceId: deviceRecorderSession.device_id}, event.origin);
      deviceRecorderBridgeState = `Sonic 已进入手机 ${deviceRecorderSession.device_id}，正在记录操作`;
      renderDeviceRecorder();
    } catch (error) {
      deviceRecorderBridgeState = `手机绑定失败：${String(error.message || error)}`;
      renderDeviceRecorder();
    }
  } else if (event.data?.type === 'MIDSCENE_RECORDING_ERROR') {
    deviceRecorderBridgeState = `动作记录失败：${String(event.data.message || '未知错误')}`;
    renderDeviceRecorder();
  }
}

window.addEventListener('message', handleDeviceRecorderMessage);

async function addRecorderCheckpoint() {
  const description = prompt('输入需要验证的页面结果');
  if (!description) return;
  const token = sessionStorage.getItem('deviceRecorderToken');
  await apiRequest('/device-recordings/action', {method: 'POST', body: JSON.stringify({session_id: deviceRecorderSession.id, recording_token: token, action: {event_id: `checkpoint-${Date.now()}`, type: 'checkpoint', checkpoint_kind: 'assert', description, device_id: deviceRecorderSession.device_id}})});
  await refreshDeviceRecording();
}

async function finishDeviceRecording() {
  try {
    const data = await apiRequest('/device-recordings/finish', {method: 'POST', body: JSON.stringify({session_id: deviceRecorderSession.id})});
    deviceRecorderSession = data.session;
    deviceRecorderGenerated = null;
    if (recorderEvidencePending(deviceRecorderSession) || recorderRecognitionPending(deviceRecorderSession)) startDeviceRecorderPolling();
    else clearInterval(deviceRecorderPollTimer);
    sessionStorage.removeItem('deviceRecorderToken');
    renderDeviceRecorder();
  } catch (error) { showToast(error.message || '结束录制失败', 'error'); }
}

async function cancelDeviceRecording() {
  if (!confirm('确认取消本次录制？已记录但未保存的操作会保留在审计记录中，不会生成用例。')) return;
  try {
    const data = await apiRequest('/device-recordings/cancel', {method: 'POST', body: JSON.stringify({session_id: deviceRecorderSession.id})});
    deviceRecorderSession = null;
    deviceRecorderGenerated = null;
    deviceRecorderBridgeState = '等待 Sonic 进入所选手机';
    clearInterval(deviceRecorderPollTimer);
    sessionStorage.removeItem('deviceRecorderToken');
    renderDeviceRecorder();
    showToast(`录制 ${data.session.id} 已取消`, 'success');
  } catch (error) { showToast(error.message || '取消录制失败', 'error'); }
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

async function editRecorderStep(stepId) {
  const input = document.getElementById(`recorder-edit-${stepId}`);
  const description = input?.value.trim() || '';
  if (!description) return showToast('请填写操作说明', 'error');
  try {
    const data = await apiRequest('/device-recordings/step', {method: 'POST', body: JSON.stringify({
      session_id: deviceRecorderSession.id, step_id: stepId, semantic_description: description,
    })});
    deviceRecorderSession = data.session;
    deviceRecorderGenerated = null;
    renderDeviceRecorder();
    showToast('操作说明已更新', 'success');
  } catch (error) { showToast(error.message || '更新操作失败', 'error'); }
}

async function deleteRecorderStep(stepId) {
  if (!confirm('确认删除这条录制操作？')) return;
  try {
    const data = await apiRequest('/device-recordings/step/delete', {method: 'POST', body: JSON.stringify({
      session_id: deviceRecorderSession.id, step_id: stepId,
    })});
    deviceRecorderSession = data.session;
    deviceRecorderGenerated = null;
    renderDeviceRecorder();
    showToast('操作记录已删除', 'success');
  } catch (error) { showToast(error.message || '删除操作失败', 'error'); }
}

async function generateDeviceRecordingYaml() {
  if (!recorderCanGenerate(deviceRecorderSession)) return showToast('没有记录到手机操作，不能生成 YAML', 'error');
  const taskName = document.getElementById('device-recorder-task-name')?.value.trim() || '录制生成用例';
  let data;
  try {
    data = await apiRequest('/device-recordings/generate', {method: 'POST', body: JSON.stringify({session_id: deviceRecorderSession.id, task_name: taskName})});
  } catch (error) {
    return showToast(error.message || 'YAML 生成失败', 'error');
  }
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
