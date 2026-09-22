let deviceRecorderSession = null;
let deviceRecorderWindow = null;
let deviceRecorderPollTimer = null;
let deviceRecorderBridgeState = '等待 Sonic 接收录制会话';
let deviceRecorderGenerated = null;
let deviceRecorderFileNameEdited = false;
let deviceRecorderRecognitionInFlight = false;
let deviceRecorderHistory = [];
let deviceRecorderHistoryState = 'ready';
let deviceRecorderHistoryError = '';
let deviceRecorderView = 'record';
let deviceRecorderConfirmedSequence = 0;
const deviceRecorderFailedSequenceNotified = new Set();
let deviceRecorderSelectedStepId = '';
let deviceRecorderHookBoundSessionId = '';
let deviceRecorderPointDrag = null;

function recorderDefaultAppPackage() {
  const apps = (taskApps || []).filter(app => app.enabled !== false);
  return (apps.find(app => app.package === 'com.kfb.model')
    || apps.find(app => String(app.name || '').trim() === '智小白3D')
    || apps[0]
    || {}).package || '';
}

function deviceRecorderAppOptions(selectedPackage = '') {
  const effectivePackage = selectedPackage || recorderDefaultAppPackage();
  return (taskApps || []).filter(app => app.enabled !== false).map(app =>
    `<option value="${escapeHtml(app.package || '')}" ${effectivePackage === app.package ? 'selected' : ''}>${escapeHtml(app.name || app.package || '')}</option>`
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

function recorderHistoryButton() {
  const count = deviceRecorderHistory.length ? ` ${deviceRecorderHistory.length}` : '';
  return `<button class="btn-sm" data-action="open-recording-history" onclick="showDeviceRecordingHistory()">录制记录${count}</button>`;
}

function recorderHistoryAppName(session) {
  return (taskApps || []).find(app => app.package === session?.app_package)?.name || session?.app_package || '未指定应用';
}

function renderDeviceRecorderHistory() {
  const area = document.getElementById('editor-area');
  if (!area) return;
  area.className = 'editor-area';
  const content = deviceRecorderHistoryState === 'loading'
    ? '<div class="job-empty">正在读取录制记录…</div>'
    : deviceRecorderHistoryState === 'error'
      ? `<div class="job-empty"><strong>录制记录读取失败</strong><span>${escapeHtml(deviceRecorderHistoryError || '请稍后重试')}</span><button class="btn-sm" data-action="retry-recording-history" onclick="loadDeviceRecordingHistory()">重新读取</button></div>`
      : deviceRecorderHistory.length
        ? `<div class="device-recorder-history-list">${deviceRecorderHistory.map(item => `<div class="device-recorder-history-entry"><button class="device-recorder-history-card" onclick="openDeviceRecordingHistory('${escapeHtml(item.id)}')"><span class="status-pill ${item.status === 'recording' ? 'success' : ''}">${escapeHtml(recorderStatusText(item))}</span><strong>${escapeHtml(recorderHistoryAppName(item))}</strong><small>${escapeHtml(item.finished_at || item.updated_at || item.created_at || '')}</small><small>${escapeHtml(item.device_id || '未绑定手机')} · ${(item.steps || []).length + (item.app_package ? 1 : 0)} 步${item.generated_result?.yaml ? ' · 已生成 YAML' : ''}</small><em>查看步骤、截图和 YAML</em></button><button class="btn-sm danger" data-action="delete-recording-history" onclick="deleteDeviceRecording('${escapeHtml(item.id)}')">删除记录</button></div>`).join('')}</div>`
        : '<div class="job-empty"><strong>暂无录制记录</strong><span>完成、暂停或取消的录制都会保留在这里。</span></div>';
  area.innerHTML = `<div class="review-page device-recorder-page device-recorder-history-page">
    <div class="review-head"><div><div class="workflow-kicker">SONIC 原生远控 · 历史记录</div><h2>录制记录</h2><p>查看本人历次录制的步骤、点击截图、识别结果和已生成 YAML。</p></div><div class="review-actions"><button class="btn-sm primary" onclick="newDeviceRecording()">新建录制</button><button class="btn-sm" onclick="showDeviceRecorder()">返回录制页</button></div></div>
    <section class="review-panel">${content}</section>
  </div>`;
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

function recorderTimelineItem(step, displaySequence = step.sequence) {
  const actionName = ({tap:'点击',swipe:'滑动',text:'输入',key:'按键',launch:'启动应用',checkpoint:'检查点'})[step.type] || step.type;
  const editor = recorderStepNeedsMeaning(step)
    ? `<div class="device-recorder-semantic"><input id="recorder-step-${escapeHtml(step.id)}" maxlength="200" placeholder="例如：提交按钮、搜索输入框"><button class="btn-sm" onclick="confirmRecorderStep('${escapeHtml(step.id)}')">确认控件</button></div>`
    : `<small>${escapeHtml(recorderStepDescription(step) || '等待语义证据')}</small>`;
  const pointX = step.point?.x ?? step.end?.x ?? '';
  const pointY = step.point?.y ?? step.end?.y ?? '';
  const rawPoint = step.raw_point ? `；Sonic 原始坐标：${escapeHtml(step.raw_point.x)}，${escapeHtml(step.raw_point.y)}` : '';
  const adjusted = step.point_manually_adjusted ? '；已人工校正' : '';
  const pointControls = step.type === 'tap'
    ? `<div class="device-recorder-point-actions"><button class="btn-sm" onclick="retryRecorderRecognition('${escapeHtml(step.id)}')">用当前红点重新识别</button><button class="btn-sm" onclick="resetRecorderPoint('${escapeHtml(step.id)}')" ${step.point_manually_adjusted ? '' : 'disabled'}>重置点击位置</button></div>`
    : `<button class="btn-sm" onclick="retryRecorderRecognition('${escapeHtml(step.id)}')">用这张截图重新识别</button>`;
  const evidencePreview = step.screenshot_path ? `${step.evidence_warning ? `<div class="agent-risk show">${escapeHtml(step.evidence_warning)}</div>` : ''}<div class="device-recorder-evidence"><div class="device-recorder-shot"><div class="device-recorder-shot-frame"><img data-recorder-evidence="${escapeHtml(step.id)}" data-point-x="${escapeHtml(pointX)}" data-point-y="${escapeHtml(pointY)}" alt="第 ${displaySequence} 步点击截图"><i hidden role="button" tabindex="0" aria-label="拖动校正点击位置" onpointerdown="beginRecorderPointDrag(event, '${escapeHtml(step.id)}')"></i></div><span data-recorder-coordinate="${escapeHtml(step.id)}">截图坐标：${escapeHtml(pointX || '-')}，${escapeHtml(pointY || '-')}${rawPoint}${adjusted}；拖动红点可校正</span></div>${pointControls}</div>` : '<div class="agent-risk show">该步骤没有截图证据，请手工标记后再生成。</div>';
  const evidence = step.evidence_status === 'pending' ? '证据待采集'
    : step.evidence_status === 'failed' ? '证据采集失败'
    : step.semantic_recognition_status === 'running' ? '正在自动识别'
    : step.semantic_recognition_status === 'failed' ? '自动识别失败，可手工补充'
    : step.semantic_source === 'ai_visual' ? `视觉识别 ${Math.round((step.semantic_confidence || 0) * 100)}%`
    : step.semantic_description ? '已人工确认' : step.evidence_status || '';
  return `<article class="${recorderStepNeedsMeaning(step) ? 'needs-confirmation' : ''}"><span>${displaySequence}</span><div><strong>${escapeHtml(actionName)}</strong>${editor}${evidencePreview}<details class="device-recorder-step-tools"><summary>手动标记、编辑或删除</summary><div><input id="recorder-edit-${escapeHtml(step.id)}" maxlength="200" value="${escapeHtml(recorderStepDescription(step))}" placeholder="输入正确控件名称，如：左上角返回"><button class="btn-sm" onclick="editRecorderStep('${escapeHtml(step.id)}')">保存人工标记</button><button class="btn-sm danger" data-action="delete-recording-step" onclick="deleteRecorderStep('${escapeHtml(step.id)}')">删除</button></div></details></div><em>${escapeHtml(evidence)}</em></article>`;
}

async function retryRecorderRecognition(stepId) {
  try {
    const data = await apiRequest('/device-recordings/recognize', {method:'POST', body:JSON.stringify({session_id:deviceRecorderSession.id, step_id:stepId, force:true})});
    deviceRecorderSession = data.session; deviceRecorderGenerated = null; renderDeviceRecorder();
  } catch (error) { showToast(error.message || '重新识别失败', 'error'); }
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
  const launchApp = (taskApps || []).find(app => app.package === session?.app_package);
  const hasAutomaticLaunch = Boolean(session?.app_package);
  const automaticLaunch = hasAutomaticLaunch
    ? `<article data-recorder-auto-launch><span>1</span><div><strong>启动应用</strong><small>${escapeHtml(launchApp?.name || session.app_package)} · ${escapeHtml(session.app_package)}</small></div><em>自动加入 YAML</em></article>`
    : '';
  if (steps.length && !steps.some(step => step.id === deviceRecorderSelectedStepId)) deviceRecorderSelectedStepId = steps[0].id;
  const selectedStep = steps.find(step => step.id === deviceRecorderSelectedStepId) || steps[0];
  area.className = 'editor-area';
  area.innerHTML = `<div class="review-page device-recorder-page">
    <div class="review-head"><div><div class="workflow-kicker">SONIC 原生远控 · 操作旁路记录</div><h2>操作录制</h2><p>手机画面和触控继续由 Sonic 处理；平台只记录你在所选手机上的真实操作。应用用于生成启动步骤，与手机分配互不绑定。</p><div class="generate-hint">操作提示：每次点击后，请等待 Sonic 显示“本步记录成功，可以继续操作”，再执行下一步；页面跳转后需等待画面稳定。</div></div><div class="review-actions">${recorderHistoryButton()}<button class="btn-sm" onclick="leaveDeviceRecorder()">返回用例资产</button>${session && session.status !== 'recording' && session.status !== 'generating' ? '<button class="btn-sm primary" data-action="new-device-recording" onclick="newDeviceRecording()">新建录制</button>' : ''}${session && !['recording','generating'].includes(session.status) ? `<button class="btn-sm danger" data-action="delete-current-recording" onclick="deleteDeviceRecording('${escapeHtml(session.id)}')">删除记录</button>` : ''}<span class="status-pill ${session?.status === 'recording' ? 'success' : ''}">${escapeHtml(recorderStatusText(session))}</span></div></div>
    <div class="device-recorder-grid">
      <section class="review-panel"><h3>录制设置</h3>
        <label class="modal-label">应用</label><select id="device-recorder-app" ${session ? 'disabled' : ''}>${deviceRecorderAppOptions(session?.app_package || '')}</select>
        <label class="modal-label">手机在线状态</label>
        ${recorderDeviceCards(session)}
        <div class="generate-hint">这里只展示状态，不再重复选择。点击开始后，在 Sonic 中选择一次你要操作的手机；平台以 Sonic 实际打开的手机作为录制对象。</div>
        <div class="review-actions">
          ${!session ? `<button class="btn-sm primary" data-action="start-recording" ${(runnerDevices || []).some(device => !device.usage_status || device.usage_status === 'idle') ? '' : 'disabled'} onclick="startDeviceRecording()">前往 Sonic 选择手机并开始</button>` : ''}
          ${session?.status === 'recording' ? '<button class="btn-sm" onclick="openRecorderSonic()">打开所选手机</button><button class="btn-sm" onclick="finishDeviceRecording()">结束录制</button><button class="btn-sm danger" onclick="cancelDeviceRecording()">取消本次录制</button>' : ''}
          ${['paused','generating'].includes(session?.status) ? '<button class="btn-sm danger" data-action="cancel-recording" onclick="cancelDeviceRecording()">取消录制</button>' : ''}
          ${session?.status === 'finished' ? `<button class="btn-sm ai" data-action="generate-recording-yaml" ${recorderCanGenerate(session) ? '' : 'disabled'} onclick="generateDeviceRecordingYaml()">${recorderGenerateLabel(session)}</button>` : ''}
        </div><div id="device-recorder-message" class="generate-hint">${session ? `手机：${escapeHtml(session.device_id || '等待在 Sonic 选择')} · 会话：${escapeHtml(session.id)} · ${escapeHtml(deviceRecorderBridgeState)}` : '录制手机只在 Sonic 选择一次；录制令牌不会写入网址。'}</div>
        ${session?.status === 'finished' ? `<div class="device-recorder-save"><label class="modal-label">用例名称</label><input id="device-recorder-task-name" value="录制生成用例" oninput="syncRecorderFileName(this.value)"><label class="modal-label">保存到模块</label><select id="device-recorder-module">${recorderModuleOptions(session)}</select>${recorderModuleOptions(session) ? '' : '<div class="agent-risk show">当前应用没有已关联模块，请先到应用配置关联模块。</div>'}<label class="modal-label">YAML 文件名</label><input id="device-recorder-file" value="录制生成用例.yaml" oninput="deviceRecorderFileNameEdited=true"><button class="btn-sm success" ${deviceRecorderGenerated && recorderModuleOptions(session) ? '' : 'disabled'} onclick="saveDeviceRecordingYaml()">保存到用例资产</button></div>` : ''}
      </section>
      <section class="review-panel"><div class="review-head compact"><div><h3>步骤时间线</h3><p>${steps.length + (hasAutomaticLaunch ? 1 : 0)} 个步骤${hasAutomaticLaunch ? '（含自动启动）' : ''}</p></div>${session?.status === 'recording' ? '<button class="btn-sm" onclick="addRecorderCheckpoint()">添加检查点</button>' : ''}</div>
        <div class="device-recorder-track">${steps.map(step => `<button class="${step.id === selectedStep?.id ? 'active' : ''}" onclick="selectRecorderStep('${escapeHtml(step.id)}')"><span>${Number(step.sequence || 0) + (hasAutomaticLaunch ? 1 : 0)}</span><small>${escapeHtml(recorderStepDescription(step) || '待识别')}</small></button>`).join('')}</div>
        <div class="device-recorder-timeline">${automaticLaunch}${selectedStep ? recorderTimelineItem(selectedStep, Number(selectedStep.sequence || 0) + (hasAutomaticLaunch ? 1 : 0)) : '<div class="job-empty">启动应用后，还没有记录到手机操作。请在打开的 Sonic 页面中操作手机。</div>'}</div>
        <pre id="device-recorder-yaml" class="agent-artifact-box" ${deviceRecorderGenerated?.yaml ? '' : 'hidden'}>${escapeHtml(deviceRecorderGenerated?.yaml || '')}</pre>
      </section>
    </div></div>`;
  if (selectedStep?.screenshot_path) setTimeout(() => loadRecorderEvidence(selectedStep.id), 0);
}

function selectRecorderStep(stepId) { deviceRecorderSelectedStepId = stepId; renderDeviceRecorder(); }

async function loadRecorderEvidence(stepId) {
  const image = document.querySelector(`[data-recorder-evidence="${CSS.escape(stepId)}"]`);
  if (!image) return;
  try {
    const response = await fetch(`/api/device-recordings/evidence?id=${encodeURIComponent(deviceRecorderSession.id)}&step_id=${encodeURIComponent(stepId)}`, {headers: authHeaders()});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    image.src = URL.createObjectURL(await response.blob());
    image.onload = () => {
      const marker = image.parentElement?.querySelector('i');
      const x = Number(image.dataset.pointX), y = Number(image.dataset.pointY);
      if (!marker || !image.naturalWidth || !image.naturalHeight || !Number.isFinite(x) || !Number.isFinite(y)) return;
      marker.style.left = `${Math.max(0, Math.min(100, x / image.naturalWidth * 100))}%`;
      marker.style.top = `${Math.max(0, Math.min(100, y / image.naturalHeight * 100))}%`;
      marker.hidden = false;
    };
  } catch (_) { image.alt = '截图加载失败，请刷新后重试'; }
}

function recorderPointFromPointer(event, image) {
  const rect = image.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(image.naturalWidth - 1, Math.round((event.clientX - rect.left) / rect.width * image.naturalWidth))),
    y: Math.max(0, Math.min(image.naturalHeight - 1, Math.round((event.clientY - rect.top) / rect.height * image.naturalHeight))),
  };
}

function moveRecorderPointDrag(event) {
  if (!deviceRecorderPointDrag || event.pointerId !== deviceRecorderPointDrag.pointerId) return;
  const {image, marker, stepId} = deviceRecorderPointDrag;
  const point = recorderPointFromPointer(event, image);
  deviceRecorderPointDrag.point = point;
  marker.style.left = `${point.x / image.naturalWidth * 100}%`;
  marker.style.top = `${point.y / image.naturalHeight * 100}%`;
  const label = document.querySelector(`[data-recorder-coordinate="${CSS.escape(stepId)}"]`);
  if (label) label.textContent = `截图坐标：${point.x}，${point.y}；待保存校正位置`;
}

async function endRecorderPointDrag(event) {
  if (!deviceRecorderPointDrag || event.pointerId !== deviceRecorderPointDrag.pointerId) return;
  const current = deviceRecorderPointDrag;
  deviceRecorderPointDrag = null;
  document.removeEventListener('pointermove', moveRecorderPointDrag);
  document.removeEventListener('pointerup', endRecorderPointDrag);
  if (!current.point) return;
  try {
    const data = await apiRequest('/device-recordings/step/point', {method:'POST', body:JSON.stringify({session_id:deviceRecorderSession.id, step_id:current.stepId, point:current.point})});
    deviceRecorderSession = data.session;
    deviceRecorderGenerated = null;
    renderDeviceRecorder();
    showToast('点击位置已校正，可用当前红点重新识别', 'success');
  } catch (error) {
    showToast(error.message || '保存点击位置失败', 'error');
    renderDeviceRecorder();
  }
}

function beginRecorderPointDrag(event, stepId) {
  const marker = event.currentTarget;
  const image = marker.parentElement?.querySelector('img');
  if (!image?.naturalWidth || !image?.naturalHeight) return;
  event.preventDefault();
  deviceRecorderPointDrag = {pointerId:event.pointerId, stepId, marker, image, point:null};
  document.addEventListener('pointermove', moveRecorderPointDrag);
  document.addEventListener('pointerup', endRecorderPointDrag);
}

async function resetRecorderPoint(stepId) {
  try {
    const data = await apiRequest('/device-recordings/step/point', {method:'POST', body:JSON.stringify({session_id:deviceRecorderSession.id, step_id:stepId, reset:true})});
    deviceRecorderSession = data.session;
    deviceRecorderGenerated = null;
    renderDeviceRecorder();
    showToast('已恢复录制时的点击位置', 'success');
  } catch (error) { showToast(error.message || '重置点击位置失败', 'error'); }
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
  deviceRecorderView = 'record';
  resetYamlToolbarForManager();
  if (typeof loadModules === 'function') await loadModules();
  if (typeof loadRunnerDevices === 'function') await loadRunnerDevices({force: true, quiet: true});
  await loadDeviceRecordingHistory(false);
  if (deviceRecorderSession?.status === 'finished') deviceRecorderSession = null;
  deviceRecorderGenerated = null;
  renderDeviceRecorder();
}

async function loadDeviceRecordingHistory(render = true) {
  deviceRecorderHistoryState = 'loading';
  deviceRecorderHistoryError = '';
  if (render && deviceRecorderView === 'history') renderDeviceRecorderHistory();
  try {
    deviceRecorderHistory = (await apiRequest('/device-recordings')).sessions || [];
    deviceRecorderHistoryState = 'ready';
  } catch (error) {
    deviceRecorderHistory = [];
    deviceRecorderHistoryState = 'error';
    deviceRecorderHistoryError = String(error.message || error || '读取失败');
  }
  if (render) deviceRecorderView === 'history' ? renderDeviceRecorderHistory() : renderDeviceRecorder();
}

function showDeviceRecordingHistory() {
  deviceRecorderView = 'history';
  renderDeviceRecorderHistory();
}

async function deleteDeviceRecording(sessionId) {
  if (!confirm('确认删除整条录制记录及其截图证据？此操作无法撤销。')) return;
  try {
    await apiRequest(`/device-recordings?id=${encodeURIComponent(sessionId)}`, {method:'DELETE'});
    if (deviceRecorderSession?.id === sessionId) {
      deviceRecorderSession = null;
      deviceRecorderGenerated = null;
      deviceRecorderView = 'record';
    }
    await loadDeviceRecordingHistory(false);
    deviceRecorderView === 'history' ? renderDeviceRecorderHistory() : renderDeviceRecorder();
    showToast('录制记录已删除', 'success');
  } catch (error) { showToast(error.message || '删除录制记录失败', 'error'); }
}

function newDeviceRecording() {
  deviceRecorderView = 'record';
  deviceRecorderSession = null;
  deviceRecorderGenerated = null;
  deviceRecorderBridgeState = '等待 Sonic 接收录制会话';
  deviceRecorderHookBoundSessionId = '';
  deviceRecorderFailedSequenceNotified.clear();
  renderDeviceRecorder();
}

async function openDeviceRecordingHistory(sessionId) {
  try {
    const data = await apiRequest(`/device-recordings?id=${encodeURIComponent(sessionId)}`);
    deviceRecorderSession = data.session;
    deviceRecorderGenerated = data.session.generated_result || null;
    deviceRecorderView = 'record';
    renderDeviceRecorder();
  } catch (error) { showToast(error.message || '读取录制记录失败', 'error'); }
}

async function startDeviceRecording() {
  const appPackage = document.getElementById('device-recorder-app')?.value || '';
  const available = (runnerDevices || []).some(device => !device.usage_status || device.usage_status === 'idle');
  if (!available || !appPackage) return showToast(!available ? '当前没有空闲 Android 手机' : '请选择应用', 'error');
  try {
    const data = await apiRequest('/device-recordings', {method: 'POST', body: JSON.stringify({app_package: appPackage})});
    deviceRecorderSession = data.session;
    deviceRecorderGenerated = null;
    deviceRecorderBridgeState = '等待 Sonic 选择手机并接收录制会话';
    deviceRecorderFileNameEdited = false;
    deviceRecorderConfirmedSequence = 0;
    deviceRecorderHookBoundSessionId = '';
    deviceRecorderFailedSequenceNotified.clear();
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
    confirmRecorderSonicBinding();
    renderDeviceRecorder();
    await maybeRecognizeDeviceRecording();
    notifyRecorderStepResult();
    if (deviceRecorderSession.status !== 'recording' && !recorderEvidencePending(deviceRecorderSession) && !recorderRecognitionPending(deviceRecorderSession)) {
      clearInterval(deviceRecorderPollTimer);
    }
  } catch (_) {}
}

function notifyRecorderStepResult() {
  if (!deviceRecorderWindow || deviceRecorderWindow.closed || !deviceRecorderSession) return;
  const step = (deviceRecorderSession.steps || []).filter(item => Number(item.sequence || 0) > deviceRecorderConfirmedSequence).sort((a,b) => Number(a.sequence || 0) - Number(b.sequence || 0))[0];
  if (!step || step.evidence_status === 'pending' || step.semantic_recognition_status === 'running') return;
  if (deviceRecorderSession.pre_action_frame_status === 'pending') return;
  const success = !recorderStepNeedsMeaning(step)
    && step.evidence_status !== 'failed'
    && deviceRecorderSession.pre_action_frame_status === 'ready';
  const sequence = Number(step.sequence || 0);
  if (!success && deviceRecorderFailedSequenceNotified.has(sequence)) return;
  const sonicUrl = sessionStorage.getItem('deviceRecorderSonicUrl') || '';
  deviceRecorderWindow.postMessage({type:'MIDSCENE_RECORDING_STEP_CONFIRMED', sessionId:deviceRecorderSession.id, sequence:step.sequence, success}, sonicUrl ? new URL(sonicUrl).origin : '*');
  deviceRecorderConfirmedSequence = sequence;
  if (success) {
    deviceRecorderFailedSequenceNotified.delete(sequence);
  } else {
    deviceRecorderFailedSequenceNotified.add(sequence);
  }
}

function confirmRecorderSonicBinding(target = deviceRecorderWindow) {
  if (!target || target.closed || !deviceRecorderSession?.id || !deviceRecorderSession.device_id) return false;
  if (deviceRecorderHookBoundSessionId === deviceRecorderSession.id) return true;
  if (deviceRecorderSession.pre_action_frame_status !== 'ready') {
    deviceRecorderBridgeState = deviceRecorderSession.pre_action_frame_status === 'failed'
      ? `Runner 点击前画面采集失败：${deviceRecorderSession.pre_action_frame_error || '请检查 ADB'}`
      : 'Runner 正在准备真实点击前画面，准备好后才开始记录';
    return false;
  }
  const sonicUrl = sessionStorage.getItem('deviceRecorderSonicUrl') || '';
  target.postMessage({type: 'MIDSCENE_RECORDING_BOUND', sessionId: deviceRecorderSession.id, deviceId: deviceRecorderSession.device_id}, sonicUrl ? new URL(sonicUrl).origin : '*');
  deviceRecorderHookBoundSessionId = deviceRecorderSession.id;
  deviceRecorderBridgeState = `Sonic 已进入手机 ${deviceRecorderSession.device_id}，真实点击前画面已准备，可以开始操作`;
  return true;
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
      deviceRecorderWindow = event.source || deviceRecorderWindow;
      confirmRecorderSonicBinding(deviceRecorderWindow);
      renderDeviceRecorder();
      startDeviceRecorderPolling();
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
    deviceRecorderSession = data.session;
    deviceRecorderGenerated = null;
    deviceRecorderBridgeState = '等待 Sonic 进入所选手机';
    clearInterval(deviceRecorderPollTimer);
    sessionStorage.removeItem('deviceRecorderToken');
    await loadDeviceRecordingHistory(false);
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
    notifyRecorderStepResult();
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
    notifyRecorderStepResult();
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
  deviceRecorderSession = data.session || deviceRecorderSession;
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
