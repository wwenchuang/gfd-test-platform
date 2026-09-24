let deviceRecorderSession = null;
let deviceRecorderWindow = null;
let deviceRecorderPollTimer = null;
let deviceRecorderPollInFlight = false;
let deviceRecorderBridgeState = '等待 Sonic 接收录制会话';
let deviceRecorderBridgeErrorActive = false;
let deviceRecorderGenerated = null;
let deviceRecorderFileNameEdited = false;
let deviceRecorderRecognitionInFlight = false;
let deviceRecorderHistory = [];
let deviceRecorderHistoryState = 'ready';
let deviceRecorderHistoryError = '';
let deviceRecorderHistoryApp = '*';
let deviceRecorderHistoryModule = '*';
const deviceRecorderHistorySelected = new Set();
let deviceRecorderHistoryDeleting = false;
let deviceRecorderHistoryDeleteErrors = new Map();
let deviceRecorderModuleSaving = false;
let deviceRecorderView = 'record';
let deviceRecorderConfirmedSequence = 0;
const deviceRecorderFailedSequenceNotified = new Set();
let deviceRecorderSelectedStepId = '';
let deviceRecorderHookBoundSessionId = '';
let deviceRecorderPointDrag = null;
const deviceRecorderEvidenceUrls = new Map();
const deviceRecorderEvidenceLoads = new Map();
const deviceRecorderRecognitionRequests = new Set();

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

// Device status is independent of the recording heartbeat and its form rendering.
let recorderDevices = null;
let deviceRecorderDeviceTimer = null;
let recorderDeviceRequest = null;
let recorderDeviceGeneration = 0;
let recorderDeviceUpdatedAt = 0;
let recorderDeviceError = false;

function recorderVisibleDevices() {
  return (recorderDevices || runnerDevices || []).filter(device => !['9888E0094F2A', '18CEDF5BA7B2'].includes(String(device.device_id || '').toUpperCase()));
}

function recorderPhoneState(device) {
  if (recorderDeviceError) return 'unknown';
  if (device.runner_online === false || !['online', 'device'].includes(device.status)) return 'offline';
  if (device.usage_status === 'unknown') return 'unknown';
  return device.usage_status && device.usage_status !== 'idle' ? 'busy' : 'idle';
}

function recorderHasReadyPhone() {
  return !recorderDeviceError && recorderVisibleDevices().some(device => recorderPhoneState(device) === 'idle');
}

function recorderDeviceCards(session) {
  const devices = recorderVisibleDevices();
  if (!devices.length) return '<div class="device-recorder-device-empty">暂无在线 Android 手机，连接后会自动更新。</div>';
  return `<div class="device-recorder-phone-grid">${devices.map(device => {
    const state = recorderPhoneState(device);
    const current = session?.device_id === device.device_id;
    const label = state === 'offline' ? (device.runner_online === false ? '离线 · Runner 未连接' : '离线 · 手机未连接')
      : state === 'unknown' ? (recorderDeviceError ? '状态待更新' : device.usage_label || '状态待确认')
      : device.usage_label || (state === 'busy' ? '占用中' : '空闲可用');
    return `<div class="device-recorder-phone ${state} ${current ? 'selected' : ''}"><span class="device-recorder-phone-status ${state}" aria-label="${escapeHtml(label)}"></span><div class="device-recorder-phone-info"><div class="device-recorder-phone-title"><strong>${escapeHtml(device.model || device.name || 'Android 手机')}</strong>${current ? '<em>本次录制</em>' : ''}</div><small>${escapeHtml(device.device_id || '')} · ${escapeHtml(device.runner_id || '')}</small></div><span class="device-recorder-phone-label">${escapeHtml(label)}</span></div>`;
  }).join('')}</div>`;
}

function recorderDevicePanel() {
  const updated = recorderDeviceUpdatedAt ? new Date(recorderDeviceUpdatedAt).toLocaleTimeString('zh-CN', {hour12:false}) : '';
  const hint = recorderDeviceError ? `更新失败，上次状态可能已过期${updated ? ' · ' + updated : ''}`
    : updated ? `每 5 秒更新 · 最近 ${updated}` : '正在同步手机状态…';
  return `<div class="device-recorder-device-heading"><h4>手机状态</h4><button type="button" class="btn-sm" data-action="refresh-recorder-devices" onclick="refreshRecorderDevices()">刷新</button></div><div class="device-recorder-device-sync ${recorderDeviceError ? 'error' : ''}" role="status">${hint}</div>${recorderDeviceCards(deviceRecorderSession)}`;
}

function renderRecorderDevicePanel() {
  const panel = document.getElementById('device-recorder-devices');
  if (!panel) return;
  panel.innerHTML = recorderDevicePanel();
  const start = document.querySelector('[data-action="start-recording"]');
  if (start) start.disabled = !recorderHasReadyPhone();
}

function stopRecorderDevicePolling() {
  clearInterval(deviceRecorderDeviceTimer);
  deviceRecorderDeviceTimer = null;
  recorderDeviceGeneration++;
}

function startRecorderDevicePolling() {
  if (deviceRecorderDeviceTimer !== null) return;
  deviceRecorderDeviceTimer = setInterval(refreshRecorderDevices, 5000);
  setTimeout(refreshRecorderDevices, 0);
}

async function refreshRecorderDevices() {
  if (deviceRecorderView !== 'record' || !document.getElementById('device-recorder-devices')) {
    if (deviceRecorderDeviceTimer !== null) stopRecorderDevicePolling();
    return;
  }
  if (document.hidden || recorderDeviceRequest) return;
  const generation = recorderDeviceGeneration;
  const request = {};
  recorderDeviceRequest = request;
  try {
    const data = await apiRequest('/runners', {timeoutMs:8000});
    if (!Array.isArray(data.devices)) throw new Error('设备状态响应不完整');
    if (generation !== recorderDeviceGeneration || !document.getElementById('device-recorder-devices')) return;
    const known = recorderVisibleDevices();
    const keys = new Set(data.devices.map(device => `${device.runner_id || ''}/${device.device_id}`));
    recorderDevices = [...data.devices, ...known.filter(device => !keys.has(`${device.runner_id || ''}/${device.device_id}`)).map(device => ({...device,status:'offline',usage_status:'unknown'}))];
    recorderDeviceUpdatedAt = Date.now();
    recorderDeviceError = false;
    renderRecorderDevicePanel();
  } catch (_) {
    if (generation !== recorderDeviceGeneration) return;
    recorderDeviceError = true;
    renderRecorderDevicePanel();
  } finally {
    if (recorderDeviceRequest === request) recorderDeviceRequest = null;
  }
}

document.addEventListener('visibilitychange', () => { if (!document.hidden) void refreshRecorderDevices(); });
window.addEventListener('focus', () => { void refreshRecorderDevices(); });

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

function recorderHistoryVisible() {
  return deviceRecorderHistory.filter(item =>
    (deviceRecorderHistoryApp === '*' || (item.app_package || '') === deviceRecorderHistoryApp)
    && (deviceRecorderHistoryModule === '*' || (item.module_name || '') === deviceRecorderHistoryModule));
}

function recorderHistoryDeletable(item) { return !['recording', 'generating'].includes(item.status); }

function setRecorderHistoryFilter(kind, value) {
  if (deviceRecorderHistoryDeleting) return;
  if (kind === 'app') { deviceRecorderHistoryApp = value; deviceRecorderHistoryModule = '*'; }
  else deviceRecorderHistoryModule = value;
  deviceRecorderHistorySelected.clear();
  renderDeviceRecorderHistory();
}

function toggleRecorderHistorySelection(id, checked) {
  if (deviceRecorderHistoryDeleting) return;
  const item = recorderHistoryVisible().find(item => item.id === id);
  if (!item || !recorderHistoryDeletable(item)) return;
  checked ? deviceRecorderHistorySelected.add(id) : deviceRecorderHistorySelected.delete(id);
  renderDeviceRecorderHistory();
}

function toggleRecorderHistoryAll(checked) {
  if (deviceRecorderHistoryDeleting) return;
  deviceRecorderHistorySelected.clear();
  if (checked) recorderHistoryVisible().filter(recorderHistoryDeletable).forEach(item => deviceRecorderHistorySelected.add(item.id));
  renderDeviceRecorderHistory();
}

function recorderHistoryGroups(items) {
  const apps = new Map();
  items.forEach(item => {
    const app = item.app_package || '';
    if (!apps.has(app)) apps.set(app, new Map());
    const groups = apps.get(app), moduleName = item.module_name || '';
    if (!groups.has(moduleName)) groups.set(moduleName, []);
    groups.get(moduleName).push(item);
  });
  return Array.from(apps, ([app, groups]) => `<section class="device-recorder-history-app" data-history-app-group="${escapeHtml(app)}"><h3>${escapeHtml(recorderHistoryAppName({app_package:app}))}</h3>${Array.from(groups, ([moduleName, rows]) => `<details class="device-recorder-history-group" open><summary>${escapeHtml(moduleName || '未分组')} <span>${rows.length} 条</span></summary><div class="device-recorder-history-list">${rows.map(item => `<div class="device-recorder-history-entry">
    <label class="device-recorder-history-check"><input type="checkbox" data-recording-select="${escapeHtml(item.id)}" aria-label="选择录制 ${escapeHtml(item.task_name || item.created_at || item.id)}" ${deviceRecorderHistorySelected.has(item.id) ? 'checked' : ''} ${deviceRecorderHistoryDeleting || !recorderHistoryDeletable(item) ? 'disabled' : ''} onchange="toggleRecorderHistorySelection(this.dataset.recordingSelect,this.checked)"></label>
    <button class="device-recorder-history-card" data-recording-id="${escapeHtml(item.id)}" onclick="openDeviceRecordingHistory(this.dataset.recordingId)"><span class="status-pill ${item.status === 'recording' ? 'success' : ''}">${escapeHtml(recorderStatusText(item))}</span><strong>${escapeHtml(item.task_name || item.generated_result?.task_name || '未命名录制')}</strong><small>${escapeHtml(item.finished_at || item.updated_at || item.created_at || '')}</small><small>${escapeHtml(item.device_id || '未绑定手机')} · ${Number(item.step_count ?? (item.steps || []).length) + (item.app_package ? 1 : 0)} 步${item.has_generated_yaml || item.generated_result?.yaml ? ' · 已生成 YAML' : ''}</small><em>查看步骤、截图和 YAML</em></button>
    <button class="btn-sm danger" data-action="delete-recording-history" data-recording-id="${escapeHtml(item.id)}" ${deviceRecorderHistoryDeleting || !recorderHistoryDeletable(item) ? 'disabled' : ''} onclick="deleteDeviceRecording(this.dataset.recordingId)">删除记录</button>${deviceRecorderHistoryDeleteErrors.has(item.id) ? `<small class="device-recorder-history-error">${escapeHtml(deviceRecorderHistoryDeleteErrors.get(item.id))}</small>` : ''}</div>`).join('')}</div></details>`).join('')}</section>`).join('');
}

function renderDeviceRecorderHistory() {
  const area = document.getElementById('editor-area');
  if (!area) return;
  area.className = 'editor-area';
  const visible = recorderHistoryVisible();
  const selectable = visible.filter(recorderHistoryDeletable);
  const apps = [...new Set(deviceRecorderHistory.map(item => item.app_package || ''))];
  const moduleNames = [...new Set(deviceRecorderHistory.filter(item => deviceRecorderHistoryApp === '*' || (item.app_package || '') === deviceRecorderHistoryApp).map(item => item.module_name || ''))];
  const content = deviceRecorderHistoryState === 'loading'
    ? '<div class="job-empty">正在读取录制记录…</div>'
    : deviceRecorderHistoryState === 'error'
      ? `<div class="job-empty"><strong>录制记录读取失败</strong><span>${escapeHtml(deviceRecorderHistoryError || '请稍后重试')}</span><button class="btn-sm" data-action="retry-recording-history" onclick="loadDeviceRecordingHistory()">重新读取</button></div>`
      : deviceRecorderHistory.length
        ? visible.length ? recorderHistoryGroups(visible) : '<div class="job-empty">当前筛选没有录制记录，请切换应用或模块。</div>'
        : '<div class="job-empty"><strong>暂无录制记录</strong><span>完成、暂停或取消的录制都会保留在这里。</span></div>';
  area.innerHTML = `<div class="review-page device-recorder-page device-recorder-history-page">
    <div class="review-head"><div><div class="workflow-kicker">SONIC 原生远控 · 历史记录</div><h2>录制记录</h2><p>查看本人历次录制的步骤、点击截图、识别结果和已生成 YAML。</p></div><div class="review-actions"><button class="btn-sm primary" onclick="newDeviceRecording()">新建录制</button><button class="btn-sm" onclick="showDeviceRecorder()">返回录制页</button></div></div>
    <section class="review-panel"><div class="device-recorder-history-filters">
      <label>应用<select id="recorder-history-app" ${deviceRecorderHistoryDeleting ? 'disabled' : ''} onchange="setRecorderHistoryFilter('app',this.value)"><option value="*">全部应用</option>${apps.map(app => `<option value="${escapeHtml(app)}" ${app === deviceRecorderHistoryApp ? 'selected' : ''}>${escapeHtml(recorderHistoryAppName({app_package:app}))}</option>`).join('')}</select></label>
      <label>模块<select id="recorder-history-module" ${deviceRecorderHistoryDeleting ? 'disabled' : ''} onchange="setRecorderHistoryFilter('module',this.value)"><option value="*">全部模块</option>${moduleNames.map(name => `<option value="${escapeHtml(name)}" ${name === deviceRecorderHistoryModule ? 'selected' : ''}>${escapeHtml(name || '未分组')}</option>`).join('')}</select></label>
      <button class="btn-sm" ${deviceRecorderHistoryDeleting ? 'disabled' : ''} onclick="loadDeviceRecordingHistory()">刷新记录</button></div>
      <div class="device-recorder-history-bulk"><label><input type="checkbox" id="recorder-history-all" ${selectable.length && selectable.every(item => deviceRecorderHistorySelected.has(item.id)) ? 'checked' : ''} ${!selectable.length || deviceRecorderHistoryDeleting || deviceRecorderHistoryState !== 'ready' ? 'disabled' : ''} onchange="toggleRecorderHistoryAll(this.checked)">全选当前筛选结果</label><span>显示 ${visible.length} / ${deviceRecorderHistory.length} 条 · 已选 ${deviceRecorderHistorySelected.size} 条</span><button class="btn-sm danger" data-action="delete-selected-recordings" ${!deviceRecorderHistorySelected.size || deviceRecorderHistoryDeleting || deviceRecorderHistoryState !== 'ready' ? 'disabled' : ''} onclick="deleteSelectedDeviceRecordings()">${deviceRecorderHistoryDeleting ? '正在删除…' : '批量删除'}</button></div>
      <p class="generate-hint">录制中、正在生成的记录需先结束或取消。删除录制不会删除已保存的 YAML 用例。</p>${content}</section>
  </div>`;
  const all = document.getElementById('recorder-history-all');
  if (all) all.indeterminate = selectable.some(item => deviceRecorderHistorySelected.has(item.id)) && !selectable.every(item => deviceRecorderHistorySelected.has(item.id));
}

function recorderModuleOptions(session) {
  const app = (taskApps || []).find(item => item.package === session?.app_package);
  return (app?.modules || []).map(name => `<option value="${escapeHtml(name)}" ${name === session?.module_name ? 'selected' : ''}>${escapeHtml(name)}</option>`).join('');
}

function refreshRecorderModuleOptions() {
  const input = document.getElementById('device-recorder-group-module');
  if (input) input.innerHTML = '<option value="">未分组</option>' + recorderModuleOptions({app_package:document.getElementById('device-recorder-app')?.value});
}

async function updateRecorderModule(moduleName) {
  if (!deviceRecorderSession || deviceRecorderModuleSaving) return;
  deviceRecorderModuleSaving = true;
  const input = document.getElementById('device-recorder-group-module');
  if (input) input.disabled = true;
  try {
    const data = await apiRequest('/device-recordings/module', {method:'POST',body:JSON.stringify({session_id:deviceRecorderSession.id,module_name:moduleName})});
    deviceRecorderSession = data.session;
    deviceRecorderHistory = deviceRecorderHistory.map(item => item.id === data.session.id ? {...item,module_name:moduleName} : item);
    const saveModule = document.getElementById('device-recorder-module');
    if (saveModule && moduleName) saveModule.value = moduleName;
    showToast('录制所属模块已保存','success');
  } catch (error) { showToast(error.message || '保存所属模块失败','error'); }
  finally {
    deviceRecorderModuleSaving = false;
    if (input) { input.disabled = false; input.value = deviceRecorderSession.module_name || ''; }
  }
}

function recorderNodeLabel(value) {
  const label = String(value || '').trim();
  return label.length > 200 || (label.length >= 24 && /^[A-Za-z0-9+/=]+$/.test(label)) ? '' : label;
}

function recorderStepDescription(step) {
  return step.semantic_description || recorderNodeLabel(step.ui_node?.text) || recorderNodeLabel(step.ui_node?.content_desc) || recorderNodeLabel(step.ui_node?.resource_id) || step.description || step.key || step.text || '';
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
  const screenWarning = step.screen_change_status === 'unchanged' ? '点击后页面结构未变化，请核对手机是否响应；必要时重试或删除该步' : '';
  const evidenceWarning = step.evidence_warning || screenWarning;
  const evidencePreview = `${evidenceWarning ? `<div class="agent-risk show">${escapeHtml(evidenceWarning)}</div>` : ''}${step.screenshot_path ? `<div class="device-recorder-evidence"><div class="device-recorder-shot"><div class="device-recorder-shot-frame"><img data-recorder-evidence="${escapeHtml(step.id)}" data-point-x="${escapeHtml(pointX)}" data-point-y="${escapeHtml(pointY)}" alt="第 ${displaySequence} 步点击截图"><i hidden role="button" tabindex="0" aria-label="拖动校正点击位置" onpointerdown="beginRecorderPointDrag(event, '${escapeHtml(step.id)}')"></i></div><span data-recorder-coordinate="${escapeHtml(step.id)}">截图坐标：${escapeHtml(pointX || '-')}，${escapeHtml(pointY || '-')}${rawPoint}${adjusted}；拖动红点可校正</span></div>${pointControls}</div>` : '<div class="agent-risk show">该步骤没有截图证据，请手工标记后再生成。</div>'}`;
  const evidence = step.evidence_status === 'pending' ? '证据待采集'
    : step.evidence_status === 'failed' ? '证据采集失败'
    : step.semantic_recognition_status === 'running' ? '正在自动识别'
    : step.semantic_recognition_status === 'failed' ? '自动识别失败，可手工补充'
    : step.screen_change_status === 'unchanged' ? '页面结构未变化，待核对'
    : step.semantic_source === 'ai_visual' ? `视觉识别 ${Math.round((step.semantic_confidence || 0) * 100)}%`
    : step.semantic_source === 'ui_xml' ? 'UI 结构识别'
    : step.semantic_description ? '已人工确认' : step.evidence_status || '';
  return `<article class="${recorderStepNeedsMeaning(step) ? 'needs-confirmation' : ''}"><span>${displaySequence}</span><div><strong>${escapeHtml(actionName)}</strong>${editor}${evidencePreview}<details class="device-recorder-step-tools"><summary>手动标记、编辑或删除</summary><div><input id="recorder-edit-${escapeHtml(step.id)}" maxlength="200" value="${escapeHtml(recorderStepDescription(step))}" placeholder="输入正确控件名称，如：左上角返回"><button class="btn-sm" onclick="editRecorderStep('${escapeHtml(step.id)}')">保存人工标记</button><button class="btn-sm danger" data-action="delete-recording-step" onclick="deleteRecorderStep('${escapeHtml(step.id)}')">删除</button></div></details></div><em>${escapeHtml(evidence)}</em></article>`;
}

async function retryRecorderRecognition(stepId) {
  if (!deviceRecorderSession?.id || deviceRecorderRecognitionRequests.has(stepId)) return;
  const sessionId = deviceRecorderSession.id;
  deviceRecorderRecognitionRequests.add(stepId);
  showToast('正在根据红点和点击前截图重新识别，请稍候', 'info');
  try {
    const data = await apiRequest('/device-recordings/recognize', {method:'POST', body:JSON.stringify({session_id:sessionId, step_id:stepId, force:true})});
    if (deviceRecorderSession?.id !== sessionId) return;
    deviceRecorderSession = data.session; deviceRecorderGenerated = null; renderDeviceRecorder();
    const step = (data.session?.steps || []).find(item => item.id === stepId);
    const label = step?.semantic_description;
    if (label) showToast(`重新识别为「${label}」，请对照红点和手机实际页面核对`, 'success');
    else showToast(step?.semantic_recognition_error || '未能确定红点对应的控件，请拖动红点或手工标记', 'warn');
  } catch (error) { showToast(error.message || '重新识别失败', 'error'); }
  finally { deviceRecorderRecognitionRequests.delete(stepId); }
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
    <div class="review-head"><div><div class="workflow-kicker">SONIC 原生远控 · 操作旁路记录</div><h2>操作录制</h2><p>手机画面和触控继续由 Sonic 处理；平台只记录你在所选手机上的真实操作。应用用于生成启动步骤，与手机分配互不绑定。</p><div class="generate-hint">操作提示：每次点击后，请等待 Sonic 显示“控件已记录”，并核对手机页面；页面结构未变化时先确认触控是否生效，再继续操作。</div></div><div class="review-actions">${recorderHistoryButton()}<button class="btn-sm" onclick="leaveDeviceRecorder()">返回用例资产</button>${session && session.status !== 'recording' && session.status !== 'generating' ? '<button class="btn-sm primary" data-action="new-device-recording" onclick="newDeviceRecording()">新建录制</button>' : ''}${session && !['recording','generating'].includes(session.status) ? `<button class="btn-sm danger" data-action="delete-current-recording" onclick="deleteDeviceRecording('${escapeHtml(session.id)}')">删除记录</button>` : ''}<span class="status-pill ${session?.status === 'recording' ? 'success' : ''}">${escapeHtml(recorderStatusText(session))}</span></div></div>
    <div class="device-recorder-grid">
      <section class="review-panel"><h3>录制设置</h3>
        <label class="modal-label">应用</label><select id="device-recorder-app" ${session ? 'disabled' : ''} onchange="refreshRecorderModuleOptions()">${deviceRecorderAppOptions(session?.app_package || '')}</select>
        <label class="modal-label" for="device-recorder-group-module">所属模块</label><select id="device-recorder-group-module" onchange="updateRecorderModule(this.value)"><option value="">未分组</option>${recorderModuleOptions(session || {app_package:recorderDefaultAppPackage()})}</select>
        <section id="device-recorder-devices" class="device-recorder-devices" aria-label="手机实时状态">${recorderDevicePanel()}</section>
        <div class="generate-hint">在 Sonic 中选择录制手机；此处自动同步手机状态。</div>
        <div class="review-actions">
          ${!session ? `<button class="btn-sm primary" data-action="start-recording" ${recorderHasReadyPhone() ? '' : 'disabled'} onclick="startDeviceRecording()">前往 Sonic 选择手机并开始</button>` : ''}
          ${session?.status === 'recording' ? '<button class="btn-sm" onclick="openRecorderSonic()">打开所选手机</button><button class="btn-sm" onclick="finishDeviceRecording()">结束录制</button><button class="btn-sm danger" onclick="cancelDeviceRecording()">取消本次录制</button>' : ''}
          ${['paused','generating'].includes(session?.status) ? '<button class="btn-sm danger" data-action="cancel-recording" onclick="cancelDeviceRecording()">取消录制</button>' : ''}
          ${session?.status === 'finished' ? `<button class="btn-sm ai" data-action="generate-recording-yaml" ${recorderCanGenerate(session) ? '' : 'disabled'} onclick="generateDeviceRecordingYaml()">${recorderGenerateLabel(session)}</button>` : ''}
        </div><div id="device-recorder-message" class="generate-hint">${session ? `手机：${escapeHtml(session.device_id || '等待在 Sonic 选择')} · 会话：${escapeHtml(session.id)} · ${escapeHtml(deviceRecorderBridgeState)}` : '录制手机只在 Sonic 选择一次；短期交接信息只放在浏览器片段中，不会发送给 Sonic 服务器，并会在页面加载时立即清除。'}</div>
        ${session?.status === 'finished' ? `<div class="device-recorder-save"><label class="modal-label">用例名称</label><input id="device-recorder-task-name" value="${escapeHtml(deviceRecorderGenerated?.task_name || '录制生成用例')}" oninput="syncRecorderFileName(this.value)"><div id="device-recorder-name-note" class="generate-hint" hidden>名称已修改；保存时会按新名称重新生成 YAML。</div><label class="modal-label">保存到模块</label><select id="device-recorder-module">${recorderModuleOptions(session)}</select>${recorderModuleOptions(session) ? '' : '<div class="agent-risk show">当前应用没有已关联模块，请先到应用配置关联模块。</div>'}<label class="modal-label">YAML 文件名</label><input id="device-recorder-file" value="${escapeHtml(deviceRecorderGenerated?.task_name || '录制生成用例')}.yaml" oninput="deviceRecorderFileNameEdited=true"><button class="btn-sm success" ${deviceRecorderGenerated && recorderModuleOptions(session) ? '' : 'disabled'} onclick="saveDeviceRecordingYaml()">保存到用例资产</button></div>` : ''}
      </section>
      <section class="review-panel"><div class="review-head compact"><div><h3>步骤时间线</h3><p>${steps.length + (hasAutomaticLaunch ? 1 : 0)} 个步骤${hasAutomaticLaunch ? '（含自动启动）' : ''}</p></div>${session?.status === 'recording' ? '<button class="btn-sm" onclick="addRecorderCheckpoint()">添加检查点</button>' : ''}</div>
        <div class="device-recorder-track">${steps.map(step => `<button class="${step.id === selectedStep?.id ? 'active' : ''}" onclick="selectRecorderStep('${escapeHtml(step.id)}')"><span>${Number(step.sequence || 0) + (hasAutomaticLaunch ? 1 : 0)}</span><small>${step.screen_change_status === 'unchanged' ? '⚠ ' : ''}${escapeHtml(recorderStepDescription(step) || '待识别')}</small></button>`).join('')}</div>
        <div class="device-recorder-timeline">${automaticLaunch}${selectedStep ? recorderTimelineItem(selectedStep, Number(selectedStep.sequence || 0) + (hasAutomaticLaunch ? 1 : 0)) : '<div class="job-empty">启动应用后，还没有记录到手机操作。请在打开的 Sonic 页面中操作手机。</div>'}</div>
        <pre id="device-recorder-yaml" class="agent-artifact-box" ${deviceRecorderGenerated?.yaml ? '' : 'hidden'}>${escapeHtml(deviceRecorderGenerated?.yaml || '')}</pre>
      </section>
    </div></div>`;
  startRecorderDevicePolling();
  if (selectedStep?.screenshot_path) setTimeout(() => loadRecorderEvidence(selectedStep.id), 0);
}

function selectRecorderStep(stepId) { deviceRecorderSelectedStepId = stepId; renderDeviceRecorder(); }

async function loadRecorderEvidence(stepId) {
  const sessionId = deviceRecorderSession?.id;
  if (!sessionId) return;
  const key = `${sessionId}:${stepId}`;
  const showImage = url => {
    // Polling replaces the timeline DOM while a screenshot fetch is in flight.
    // Always attach the result to the current element, not the removed one.
    if (deviceRecorderSession?.id !== sessionId) return;
    const image = document.querySelector(`[data-recorder-evidence="${CSS.escape(stepId)}"]`);
    if (!image) return;
    const showMarker = () => {
      const marker = image.parentElement?.querySelector('i');
      const x = Number(image.dataset.pointX), y = Number(image.dataset.pointY);
      if (!marker || !image.naturalWidth || !image.naturalHeight || !Number.isFinite(x) || !Number.isFinite(y)) return;
      marker.style.left = `${Math.max(0, Math.min(100, x / image.naturalWidth * 100))}%`;
      marker.style.top = `${Math.max(0, Math.min(100, y / image.naturalHeight * 100))}%`;
      marker.hidden = false;
    };
    image.onload = showMarker;
    image.src = url;
    if (image.complete) showMarker();
  };
  if (deviceRecorderEvidenceUrls.has(key)) { showImage(deviceRecorderEvidenceUrls.get(key)); return; }
  try {
    if (!deviceRecorderEvidenceLoads.has(key)) {
      deviceRecorderEvidenceLoads.set(key, (async () => {
        const response = await fetch(`/api/device-recordings/evidence?id=${encodeURIComponent(sessionId)}&step_id=${encodeURIComponent(stepId)}`, {headers: authHeaders()});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const url = URL.createObjectURL(await response.blob());
        deviceRecorderEvidenceUrls.set(key, url);
        if (deviceRecorderEvidenceUrls.size > 40) {
          const oldest = deviceRecorderEvidenceUrls.keys().next().value;
          URL.revokeObjectURL?.(deviceRecorderEvidenceUrls.get(oldest));
          deviceRecorderEvidenceUrls.delete(oldest);
        }
        return url;
      })());
    }
    showImage(await deviceRecorderEvidenceLoads.get(key));
  } catch (_) {
    const image = document.querySelector(`[data-recorder-evidence="${CSS.escape(stepId)}"]`);
    if (image) image.alt = '截图加载失败，请刷新后重试';
  } finally { deviceRecorderEvidenceLoads.delete(key); }
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
  stopRecorderDevicePolling();
  clearInterval(deviceRecorderPollTimer);
  if (typeof activateWorkflow === 'function') activateWorkflow('assets');
}

function syncRecorderFileName(taskName) {
  const note = document.getElementById('device-recorder-name-note');
  if (note) note.hidden = !deviceRecorderGenerated?.yaml || String(taskName || '').trim() === deviceRecorderGenerated.task_name;
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
    const rows = new Map();
    let offset = 0;
    do {
      const data = await apiRequest(`/device-recordings?limit=100&offset=${offset}`);
      (data.sessions || []).forEach(item => rows.set(item.id,item));
      const next = data.next_offset;
      if (next == null || !Number.isInteger(next) || next <= offset) break;
      offset = next;
    } while (true);
    deviceRecorderHistory = Array.from(rows.values());
    for (const id of deviceRecorderHistorySelected) if (!deviceRecorderHistory.some(item => item.id === id && recorderHistoryDeletable(item))) deviceRecorderHistorySelected.delete(id);
    deviceRecorderHistoryState = 'ready';
  } catch (error) {
    deviceRecorderHistory = [];
    deviceRecorderHistoryState = 'error';
    deviceRecorderHistoryError = String(error.message || error || '读取失败');
  }
  if (render) deviceRecorderView === 'history' ? renderDeviceRecorderHistory() : renderDeviceRecorder();
}

function showDeviceRecordingHistory() {
  stopRecorderDevicePolling();
  if (deviceRecorderSession) {
    const current = deviceRecorderSession;
    deviceRecorderHistory = [current, ...deviceRecorderHistory.filter(item => item.id !== current.id)];
  }
  deviceRecorderView = 'history';
  renderDeviceRecorderHistory();
}

async function deleteSelectedDeviceRecordings() {
  if (deviceRecorderHistoryDeleting) return;
  const selected = recorderHistoryVisible().filter(item => deviceRecorderHistorySelected.has(item.id) && recorderHistoryDeletable(item));
  if (!selected.length || !confirm(`确认删除选中的 ${selected.length} 条录制记录及截图？无法撤销，已保存的 YAML 用例不受影响。`)) return;
  deviceRecorderHistoryDeleting = true;
  deviceRecorderHistoryDeleteErrors.clear();
  renderDeviceRecorderHistory();
  let deleted = 0;
  try {
    for (const item of selected) {
      try {
        await apiRequest(`/device-recordings?id=${encodeURIComponent(item.id)}`, {method:'DELETE'});
        deleted++;
        deviceRecorderHistorySelected.delete(item.id);
        deviceRecorderHistory = deviceRecorderHistory.filter(row => row.id !== item.id);
        if (deviceRecorderSession?.id === item.id) { deviceRecorderSession = null; deviceRecorderGenerated = null; }
      } catch (error) { deviceRecorderHistoryDeleteErrors.set(item.id, error.message || '删除失败，请重试'); }
    }
  } finally {
    deviceRecorderHistoryDeleting = false;
    if (deviceRecorderView === 'history') renderDeviceRecorderHistory();
  }
  showToast(`已删除 ${deleted} 条${deviceRecorderHistoryDeleteErrors.size ? `，失败 ${deviceRecorderHistoryDeleteErrors.size} 条，失败项已保留` : ''}`, deviceRecorderHistoryDeleteErrors.size ? 'warn' : 'success');
}

async function deleteDeviceRecording(sessionId) {
  if (deviceRecorderHistoryDeleting) return;
  if (!confirm('确认删除整条录制记录及其截图证据？此操作无法撤销。')) return;
  try {
    await apiRequest(`/device-recordings?id=${encodeURIComponent(sessionId)}`, {method:'DELETE'});
    deviceRecorderHistorySelected.delete(sessionId);
    deviceRecorderHistoryDeleteErrors.delete(sessionId);
    if (deviceRecorderSession?.id === sessionId) {
      deviceRecorderSession = null;
      deviceRecorderGenerated = null;
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
  deviceRecorderBridgeErrorActive = false;
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
    const moduleName = document.getElementById('device-recorder-group-module')?.value || '';
    const data = await apiRequest('/device-recordings', {method: 'POST', body: JSON.stringify({app_package: appPackage, module_name:moduleName})});
    deviceRecorderSession = data.session;
    deviceRecorderGenerated = null;
    deviceRecorderBridgeState = '等待 Sonic 选择手机并接收录制会话';
    deviceRecorderBridgeErrorActive = false;
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
  const handoff = encodeURIComponent(JSON.stringify({
    sessionId: deviceRecorderSession?.id || '',
    recordingToken: sessionStorage.getItem('deviceRecorderToken') || '',
    deviceId: deviceRecorderSession?.device_id || '',
    endpoint: `${location.origin}/api/device-recordings/action`,
  }));
  // The fragment is never sent in the HTTP request. Sonic's first script
  // consumes and clears it before the application bundle runs. This survives
  // browsers that isolate cross-site openers and clear window.name.
  const target = new URL(url);
  // Sonic's service worker/browser cache may otherwise reuse an older
  // index.html which does not contain the current recorder hook.  Make each
  // recording start a real document navigation while keeping credentials in
  // the fragment only.
  target.searchParams.set('midsceneRecorder', String(Date.now()));
  target.hash = `__MIDSCENE_RECORDING_HANDOFF__${handoff}`;
  deviceRecorderWindow = window.open(target.href, `midscene-sonic-recorder-${deviceRecorderSession?.id || Date.now()}`);
  [800, 1800, 3500].forEach(delay => setTimeout(recorderHandshake, delay));
}

function startDeviceRecorderPolling() {
  clearInterval(deviceRecorderPollTimer);
  deviceRecorderPollTimer = setInterval(refreshDeviceRecording, 2000);
}

function recorderDisplaySnapshot(session) {
  if (!session) return '';
  const {heartbeat_ts, updated_ts, updated_at, ...display} = session;
  return JSON.stringify(display);
}

async function refreshDeviceRecording() {
  if (!deviceRecorderSession?.id || deviceRecorderPollInFlight) return;
  deviceRecorderPollInFlight = true;
  try {
    const previousDisplay = recorderDisplaySnapshot(deviceRecorderSession);
    const data = deviceRecorderSession.status === 'recording'
      ? await apiRequest('/device-recordings/heartbeat', {method: 'POST', body: JSON.stringify({session_id: deviceRecorderSession.id})})
      : await apiRequest(`/device-recordings?id=${encodeURIComponent(deviceRecorderSession.id)}`);
    deviceRecorderSession = data.session;
    confirmRecorderSonicBinding();
    if (recorderDisplaySnapshot(deviceRecorderSession) !== previousDisplay) renderDeviceRecorder();
    else {
      const message = document.getElementById('device-recorder-message');
      if (message) message.textContent = `手机：${deviceRecorderSession.device_id || '等待在 Sonic 选择'} · 会话：${deviceRecorderSession.id} · ${deviceRecorderBridgeState}`;
    }
    notifyRecorderStepResult();
    void maybeRecognizeDeviceRecording();
    if (deviceRecorderSession.status !== 'recording' && !recorderEvidencePending(deviceRecorderSession) && !recorderRecognitionPending(deviceRecorderSession)) {
      clearInterval(deviceRecorderPollTimer);
    }
  } catch (_) {}
  finally { deviceRecorderPollInFlight = false; }
}

function notifyRecorderStepResult() {
  if (!deviceRecorderWindow || deviceRecorderWindow.closed || !deviceRecorderSession) return;
  const step = (deviceRecorderSession.steps || []).filter(item => Number(item.sequence || 0) > deviceRecorderConfirmedSequence).sort((a,b) => Number(a.sequence || 0) - Number(b.sequence || 0))[0];
  if (!step || step.evidence_status === 'pending' || step.semantic_recognition_status === 'running') return;
  if (recorderStepNeedsMeaning(step) && step.evidence_status !== 'failed' && step.semantic_recognition_status !== 'failed') return;
  if (deviceRecorderSession.pre_action_frame_status === 'pending') return;
  const success = !recorderStepNeedsMeaning(step)
    && step.evidence_status !== 'failed'
    && deviceRecorderSession.pre_action_frame_status === 'ready';
  const sequence = Number(step.sequence || 0);
  if (!success && deviceRecorderFailedSequenceNotified.has(sequence)) return;
  const sonicUrl = sessionStorage.getItem('deviceRecorderSonicUrl') || '';
  deviceRecorderWindow.postMessage({type:'MIDSCENE_RECORDING_STEP_CONFIRMED', sessionId:deviceRecorderSession.id, sequence:step.sequence, success, screenUnchanged:step.screen_change_status === 'unchanged'}, sonicUrl ? new URL(sonicUrl).origin : '*');
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

function stopRecorderSonicMirroring(sessionId) {
  if (!deviceRecorderWindow || deviceRecorderWindow.closed || !sessionId) return;
  const sonicUrl = sessionStorage.getItem('deviceRecorderSonicUrl') || '';
  deviceRecorderWindow.postMessage({type: 'MIDSCENE_RECORDING_STOP', sessionId}, sonicUrl ? new URL(sonicUrl).origin : '*');
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
  } else if (event.data?.type === 'MIDSCENE_RECORDING_ERROR' && (!event.data.sessionId || event.data.sessionId === deviceRecorderSession?.id)) {
    deviceRecorderBridgeErrorActive = event.data.source === 'bridge';
    deviceRecorderBridgeState = `动作记录失败：${String(event.data.message || '未知错误')}`;
    renderDeviceRecorder();
  } else if (event.data?.type === 'MIDSCENE_RECORDING_BRIDGE_RECOVERED' && event.data.sessionId === deviceRecorderSession?.id && deviceRecorderBridgeErrorActive) {
    deviceRecorderBridgeErrorActive = false;
    deviceRecorderBridgeState = 'Sonic 与平台已重新连接，录制已恢复';
    const message = document.getElementById('device-recorder-message');
    if (message) message.textContent = `手机：${deviceRecorderSession.device_id || '等待在 Sonic 选择'} · 会话：${deviceRecorderSession.id} · ${deviceRecorderBridgeState}`;
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
    stopRecorderSonicMirroring(deviceRecorderSession.id);
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
    stopRecorderSonicMirroring(deviceRecorderSession.id);
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
  showToast(data.result.can_debug ? 'YAML 静态校验通过；仍需 Runner 真机回放验证' : `YAML 已生成，但还有 ${data.result.issues?.length || 1} 个问题需要处理`, data.result.can_debug ? 'success' : 'warn');
  const save = document.querySelector('.device-recorder-save button');
  if (save) save.disabled = false;
}

async function saveDeviceRecordingYaml() {
  if (!deviceRecorderGenerated?.yaml) return showToast('请先生成 YAML', 'error');
  const taskName = document.getElementById('device-recorder-task-name')?.value.trim() || '录制生成用例';
  const moduleName = document.getElementById('device-recorder-module')?.value || '';
  let fileName = document.getElementById('device-recorder-file')?.value.trim() || '';
  if (!moduleName || !fileName) return showToast('请选择模块并填写文件名', 'error');
  if (!/\.ya?ml$/i.test(fileName)) fileName += '.yaml';
  try {
    if (deviceRecorderGenerated.task_name !== taskName) {
      const data = await apiRequest('/device-recordings/generate', {method: 'POST', body: JSON.stringify({session_id: deviceRecorderSession.id, task_name: taskName})});
      deviceRecorderGenerated = data.result;
      deviceRecorderSession = data.session || deviceRecorderSession;
      const box = document.getElementById('device-recorder-yaml');
      if (box) box.textContent = data.result.yaml;
    }
    const grouped = await apiRequest('/device-recordings/module', {method:'POST',body:JSON.stringify({session_id:deviceRecorderSession.id,module_name:moduleName})});
    deviceRecorderSession = grouped.session || deviceRecorderSession;
    await apiRequest('/file', {method: 'POST', body: JSON.stringify({app_package:deviceRecorderSession.app_package,module: moduleName, file: fileName, content: deviceRecorderGenerated.yaml})});
  } catch (error) { return showToast(error.message || '保存录制 YAML 失败', 'error'); }
  if (!modules[moduleName]) modules[moduleName] = [];
  if (!modules[moduleName].includes(fileName)) modules[moduleName].push(fileName);
  showToast(deviceRecorderGenerated.requires_confirmation ? '草稿已保存；确认有歧义步骤后再调试' : 'YAML 已保存到用例资产', deviceRecorderGenerated.requires_confirmation ? 'warn' : 'success');
  if (typeof openFile === 'function') openFile(moduleName, fileName);
}
