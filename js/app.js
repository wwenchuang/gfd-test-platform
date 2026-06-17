// app.js
// Extracted from task-manager.html (no logic changes).

// ===== KNOWLEDGE BASE =====
function setKnowledgeStatus(text, type='') {
  const el = document.getElementById('knowledge-status');
  el.textContent = text || '';
  el.className = `generate-status${text ? ' show' : ''}${type ? ` ${type}` : ''}`;
}

function linesFromTextarea(id) {
  return document.getElementById(id).value
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean);
}

async function imageFileToPayload(file) {
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
  return { name: file.name, contentBase64: dataUrl.split(',')[1] || '' };
}

function setKnowledgePreview(dataUrl='') {
  const box = document.getElementById('knowledge-preview');
  const img = document.getElementById('knowledge-preview-img');
  if (!box || !img) return;
  if (!dataUrl) {
    box.classList.remove('show');
    img.removeAttribute('src');
    return;
  }
  img.src = dataUrl;
  box.classList.add('show');
}

async function fileToDataUrl(file) {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function pastedFileName(file, prefix='pasted') {
  const ext = file.type === 'image/jpeg' ? 'jpg' : file.type === 'image/png' ? 'png' : (file.name || '').split('.').pop() || 'txt';
  return `${prefix}-${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}.${ext}`;
}

async function fileFromClipboardItem(item, index=0) {
  const file = item.getAsFile();
  if (!file) return null;
  const name = file.name || pastedFileName(file, `pasted-${index + 1}`);
  return new File([file], name, { type: file.type || 'application/octet-stream' });
}

async function handleKnowledgePastedFile(file) {
  if (!file || !/^image\//.test(file.type || '')) {
    setKnowledgeStatus('页面知识库只支持粘贴图片截图。', 'error');
    return false;
  }
  const namedFile = new File([file], file.name || pastedFileName(file, 'page-screenshot'), { type: file.type || 'image/png' });
  knowledgeScreenshot = await imageFileToPayload(namedFile);
  setKnowledgePreview(await fileToDataUrl(namedFile));
  document.getElementById('knowledge-screenshot-name').textContent = `已粘贴：${namedFile.name} · ${formatBytes(namedFile.size)}`;
  setKnowledgeStatus('截图已粘贴，可以点击 AI 识别页面。', 'success');
  return true;
}

async function handleKnowledgeScreenshot(input) {
  const file = (input.files || [])[0];
  if (!file) return;
  if (!/\.(png|jpe?g)$/i.test(file.name)) {
    input.value = '';
    setKnowledgeStatus('页面截图只支持 PNG / JPG', 'error');
    return;
  }
  knowledgeScreenshot = await imageFileToPayload(file);
  setKnowledgePreview(await fileToDataUrl(file));
  document.getElementById('knowledge-screenshot-name').textContent = `已选择：${file.name} · ${formatBytes(file.size)}`;
  input.value = '';
}

async function analyzeKnowledgeScreenshot() {
  if (!knowledgeScreenshot) {
    setKnowledgeStatus('请先选择页面截图，再让 AI 识别。', 'error');
    return;
  }
  const button = document.getElementById('btn-analyze-knowledge');
  const appPackage = document.getElementById('knowledge-app-package').value.trim() || 'com.kfb.model';
  const pageName = document.getElementById('knowledge-page-name').value.trim();
  const hint = [
    document.getElementById('knowledge-route').value.trim(),
    document.getElementById('knowledge-description').value.trim()
  ].filter(Boolean).join('\n');

  button.disabled = true;
  button.textContent = '识别中...';
  setKnowledgeStatus('AI 正在识别页面结构和可点击元素...', 'busy');
  try {
    const data = await apiRequest('/knowledge/analyze', {
      method: 'POST',
      body: JSON.stringify({
        app_package: appPackage,
        page_name: pageName,
        hint,
        screenshot: knowledgeScreenshot
      })
    });
    const draft = data.draft || {};
    document.getElementById('knowledge-page-name').value = draft.page_name || pageName;
    document.getElementById('knowledge-route').value = draft.route || '';
    document.getElementById('knowledge-description').value = draft.description || '';
    document.getElementById('knowledge-elements').value = (draft.key_elements || []).join('\n');
    document.getElementById('knowledge-assertions').value = (draft.common_assertions || []).join('\n');
    setKnowledgeStatus('AI 已生成页面知识草稿，请检查后保存。', 'success');
  } catch(e) {
    setKnowledgeStatus(e.message || 'AI 识别失败', 'error');
  } finally {
    button.disabled = false;
    button.textContent = 'AI 识别页面';
  }
}

function renderFigmaDrafts(drafts=figmaDrafts) {
  const list = document.getElementById('figma-draft-list');
  const actions = document.getElementById('figma-actions');
  if (!list || !actions) return;
  if (!drafts.length) {
    list.classList.remove('show');
    list.innerHTML = '';
    actions.style.display = 'none';
    return;
  }
  list.classList.add('show');
  actions.style.display = 'flex';
  list.innerHTML = drafts.map(draft => {
    const figma = draft.figma || {};
    const texts = draft.key_elements || [];
    const size = figma.width && figma.height ? `${figma.width}×${figma.height}` : figma.type || '设计稿页面';
    const score = figma.score !== undefined ? ` · 分数 ${figma.score}` : '';
    return `
      <label class="figma-item" title="${escapeHtml(draft.description || figma.node_id || '')}">
        <input type="checkbox" class="figma-draft-check" value="${escapeHtml(figma.node_id || draft.page_id)}" checked>
        <div class="generate-knowledge-main">
          <div class="figma-name">${escapeHtml(draft.page_name || draft.page_id || 'Figma 页面')}</div>
          <div class="figma-meta">${escapeHtml(`${size}${score}`)}</div>
          <div class="figma-meta">${escapeHtml((texts.slice(0, 3).join(' / ')) || '未识别到页面文案')}</div>
        </div>
      </label>
    `;
  }).join('');
}

function toggleFigmaDrafts(checked) {
  document.querySelectorAll('.figma-draft-check').forEach(input => input.checked = checked);
}

function clearFigmaImport() {
  figmaDrafts = [];
  figmaParsedUrl = '';
  const input = document.getElementById('figma-url');
  if (input) input.value = '';
  renderFigmaDrafts([]);
}

function selectedFigmaNodeIds() {
  return Array.from(document.querySelectorAll('.figma-draft-check:checked')).map(input => input.value);
}

async function parseFigmaDesign() {
  const appPackage = document.getElementById('knowledge-app-package').value.trim() || 'com.kfb.model';
  const figmaUrl = document.getElementById('figma-url').value.trim();
  const mode = document.getElementById('figma-parse-mode').value || 'smart';
  const limit = Number(document.getElementById('figma-parse-limit').value || 20);
  const button = document.getElementById('btn-figma-parse');
  if (!figmaUrl) {
    setKnowledgeStatus('请先粘贴 Figma 链接。', 'error');
    return;
  }
  figmaDrafts = [];
  figmaParsedUrl = figmaUrl;
  renderFigmaDrafts([]);
  button.disabled = true;
  button.textContent = '解析中...';
  setKnowledgeStatus('Figma 解析任务创建中...', 'busy');
  try {
    const created = await apiRequest('/figma/parse-async', {
      method: 'POST',
      body: JSON.stringify({ figma_url: figmaUrl, app_package: appPackage, mode, limit })
    });
    const data = await pollGenericJob(created.job_id, job => {
      const progress = Number(job.progress || 0);
      const label = job.message || job.step || '正在解析 Figma';
      setKnowledgeStatus(`${label} · ${progress}%`, 'busy');
    });
    figmaDrafts = data.drafts || [];
    renderFigmaDrafts(figmaDrafts);
    setKnowledgeStatus(`已解析 ${figmaDrafts.length} 个 Figma 候选页面，勾选后导入到「${appDisplayLabel(appPackage)}」。如果漏页，切到“宽松”或“全部 Frame”。`, figmaDrafts.length ? 'success' : 'error');
  } catch(e) {
    setKnowledgeStatus(e.message || 'Figma 解析失败，请检查 Token、链接权限和 node-id。', 'error');
  } finally {
    button.disabled = false;
    button.textContent = '解析';
  }
}

async function importFigmaDrafts() {
  const appPackage = document.getElementById('knowledge-app-package').value.trim() || 'com.kfb.model';
  const figmaUrl = figmaParsedUrl || document.getElementById('figma-url').value.trim();
  const mode = document.getElementById('figma-parse-mode').value || 'smart';
  const limit = Number(document.getElementById('figma-parse-limit').value || 20);
  const selected = selectedFigmaNodeIds();
  const button = document.getElementById('btn-figma-import');
  if (!figmaUrl) {
    setKnowledgeStatus('请先解析 Figma 链接。', 'error');
    return;
  }
  if (!selected.length) {
    setKnowledgeStatus('请至少选择一个 Figma 页面。', 'error');
    return;
  }
  button.disabled = true;
  button.textContent = '导入中...';
  setKnowledgeStatus(`正在导入 ${selected.length} 个页面到当前 APP 页面知识库...`, 'busy');
  try {
    const selectedSet = new Set(selected);
    const selectedDrafts = figmaDrafts.filter(draft => selectedSet.has((draft.figma || {}).node_id || draft.page_id));
    const data = await apiRequest('/figma/import', {
      method: 'POST',
      body: JSON.stringify({ figma_url: figmaUrl, app_package: appPackage, selected_node_ids: selected, drafts: selectedDrafts, mode, limit })
    });
    const imported = data.imported || [];
    figmaDrafts = [];
    renderFigmaDrafts([]);
    await loadKnowledgeApps();
    await loadKnowledgePages();
    setKnowledgeStatus(`已导入 ${imported.length} 个 Figma 页面。生成和修复会自动参考当前 APP 的页面知识。`, 'success');
    showToast(`✓ 已导入 ${imported.length} 个 Figma 页面`, 'success');
  } catch(e) {
    setKnowledgeStatus(e.message || 'Figma 导入失败', 'error');
  } finally {
    button.disabled = false;
    button.textContent = '导入选中页面';
  }
}

async function showKnowledge() {
  setActiveWorkflow('assets');
  const currentApp = currentModuleAppPackage() || document.getElementById('generate-app-package')?.value.trim() || 'com.kfb.model';
  document.getElementById('knowledge-app-package').value = currentApp;
  document.getElementById('modal-knowledge').classList.add('show');
  clearKnowledgeForm(false);
  await loadKnowledgeApps();
  syncAppSelect('knowledge');
  await loadKnowledgePages();
}

async function loadKnowledgeApps() {
  try {
    const data = await apiRequest('/knowledge/apps');
    knowledgeApps = data.apps || [];
    knowledgeAppDetails = data.appDetails || [];
    mergeRecentApps(knowledgeApps);
    renderKnowledgeApps();
    renderAppPackageSelects();
  } catch(e) {
    knowledgeApps = [];
    knowledgeAppDetails = [];
    renderKnowledgeApps();
    renderAppPackageSelects();
  }
}

function allAppPackages() {
  const currentGenerate = document.getElementById('generate-app-package')?.value.trim();
  const currentKnowledge = document.getElementById('knowledge-app-package')?.value.trim();
  return Array.from(new Set([
    'com.kfb.model',
    ...taskApps.map(app => app.package),
    ...knowledgeApps,
    ...knowledgeAppDetails.map(app => app.package),
    ...recentAppPackages,
    currentGenerate,
    currentKnowledge
  ].filter(Boolean)));
}

function mergeRecentApps(apps) {
  recentAppPackages = Array.from(new Set([...recentAppPackages, ...apps].filter(Boolean))).slice(0, 30);
  localStorage.setItem('midscene_recent_apps', JSON.stringify(recentAppPackages));
}

function rememberAppPackage(appPackage) {
  appPackage = (appPackage || '').trim();
  if (!appPackage) return;
  recentAppPackages = [appPackage, ...recentAppPackages.filter(app => app !== appPackage)].slice(0, 30);
  localStorage.setItem('midscene_recent_apps', JSON.stringify(recentAppPackages));
  renderAppPackageSelects();
}

function renderAppPackageSelects() {
  const apps = allAppPackages();
  ['generate', 'knowledge', 'mindmap'].forEach(scope => {
    const select = document.getElementById(`${scope}-app-select`);
    const input = document.getElementById(`${scope}-app-package`);
    if (!select || !input) return;
    const current = input.value.trim();
    select.innerHTML = '<option value="">选择应用</option>' + apps.map(app => `<option value="${escapeHtml(app)}">${escapeHtml(appDisplayLabel(app))}</option>`).join('');
    select.value = apps.includes(current) ? current : '';
  });
}

function syncAppSelect(scope) {
  const select = document.getElementById(`${scope}-app-select`);
  const input = document.getElementById(`${scope}-app-package`);
  if (!select || !input) return;
  select.value = allAppPackages().includes(input.value.trim()) ? input.value.trim() : '';
}

function selectAppPackage(scope, appPackage) {
  if (!appPackage) return;
  document.getElementById(`${scope}-app-package`).value = appPackage;
  rememberAppPackage(appPackage);
  syncAppSelect(scope);
  if (scope === 'knowledge') {
    document.getElementById('generate-app-package').value = appPackage;
    syncAppSelect('generate');
    clearKnowledgeForm(false);
    renderKnowledgeApps();
    loadKnowledgePages();
  } else if (scope === 'generate') {
    updateGenerateAppHint();
    loadGenerateKnowledgePages();
  } else if (scope === 'mindmap') {
    rememberAppPackage(appPackage);
  }
}

function updateGenerateAppHint() {
  const hint = document.getElementById('generate-app-hint');
  if (!hint) return;
  const mod = document.getElementById('generate-module')?.value || '';
  const appPackage = document.getElementById('generate-app-package')?.value.trim() || '';
  const mappedApp = moduleApp(mod);
  if (mod && mappedApp) {
    hint.textContent = `当前模块「${mod}」绑定应用：${appDisplayLabel(mappedApp.package)}。生成时只会参考这个 APP 的页面知识。`;
  } else if (mod && appPackage) {
    hint.textContent = `当前模块「${mod}」未绑定固定 APP，生成时按手动包名「${appPackage}」读取页面知识。`;
  } else {
    hint.textContent = '页面知识库按 APP 包名隔离；测试生成可参考测试库和基线库，基线修复只参考基线库。';
  }
}

function handleGenerateAppInput(appPackage) {
  rememberAppPackage(appPackage);
  syncAppSelect('generate');
  updateGenerateAppHint();
  if (generateAppInputTimer) clearTimeout(generateAppInputTimer);
  generateAppInputTimer = setTimeout(() => {
    loadGenerateKnowledgePages();
  }, 300);
}

function handleMindmapAppInput(appPackage) {
  rememberAppPackage(appPackage);
  syncAppSelect('mindmap');
}

function syncGenerateAppFromModule() {
  const mod = document.getElementById('generate-module')?.value || '';
  const packageName = moduleAppPackage(mod);
  if (packageName) {
    document.getElementById('generate-app-package').value = packageName;
    rememberAppPackage(packageName);
    syncAppSelect('generate');
  } else {
    updateGenerateAppHint();
  }
  loadGenerateKnowledgePages();
}

function handleGenerateModuleChange() {
  const mod = document.getElementById('generate-module')?.value || '';
  if (mod && Object.prototype.hasOwnProperty.call(modules, mod)) {
    currentModule = mod;
    renderModules();
  }
  syncGenerateAppFromModule();
  updateGenerateAppHint();
}

async function loadGenerateKnowledgePages() {
  const appPackage = document.getElementById('generate-app-package').value.trim() || 'com.kfb.model';
  const tier = document.getElementById('generate-knowledge-tier')?.value || 'all';
  rememberAppPackage(appPackage);
  try {
    const data = await apiRequest(`/knowledge/pages?app_package=${encodeURIComponent(appPackage)}&tier=${encodeURIComponent(tier)}`);
    generateKnowledgePages = data.pages || [];
    renderGenerateKnowledgePages();
  } catch(e) {
    generateKnowledgePages = [];
    renderGenerateKnowledgePages(e.message || '读取页面知识失败');
  }
}

function renderGenerateKnowledgePages(error='') {
  const box = document.getElementById('generate-knowledge-box');
  const list = document.getElementById('generate-knowledge-list');
  const count = document.getElementById('generate-knowledge-count');
  if (!box || !list) return;
  box.classList.add('show');
  if (error) {
    if (count) count.textContent = '读取失败';
    list.innerHTML = `<div class="generate-knowledge-empty">${escapeHtml(error)}</div>`;
    return;
  }
  if (!generateKnowledgePages.length) {
    if (count) count.textContent = `${appDisplayName(document.getElementById('generate-app-package').value.trim())} · 0 个页面`;
    list.innerHTML = '<div class="generate-knowledge-empty">当前应用暂无页面知识，将使用自动需求分析。建议先在页面知识库维护首页、我的页和核心业务页。</div>';
    return;
  }
  if (count) count.textContent = `${appDisplayName(document.getElementById('generate-app-package').value.trim())} · ${generateKnowledgePages.length} 个页面，未勾选则自动匹配`;
  list.innerHTML = generateKnowledgePages.map(page => `
    <label class="generate-knowledge-item" title="${escapeHtml(page.route || page.description || '')}">
      <input type="checkbox" class="generate-knowledge-check" value="${escapeHtml(page.page_id)}" onchange="updateGenerateKnowledgeCount()">
      <div class="generate-knowledge-main">
        <div class="generate-knowledge-name">${escapeHtml(page.page_name || page.page_id)} · ${page.tier === 'baseline' ? '基线库' : '测试库'}</div>
        <div class="generate-knowledge-sub">${escapeHtml(page.route || page.description || '无路径说明')}</div>
      </div>
    </label>
  `).join('');
}

function toggleGenerateKnowledge(checked) {
  document.querySelectorAll('.generate-knowledge-check').forEach(input => input.checked = checked);
  updateGenerateKnowledgeCount();
}

function selectedGenerateKnowledgePageIds() {
  return Array.from(document.querySelectorAll('.generate-knowledge-check:checked')).map(input => input.value);
}

function updateGenerateKnowledgeCount() {
  const count = document.getElementById('generate-knowledge-count');
  if (!count) return;
  const selected = selectedGenerateKnowledgePageIds().length;
  const appName = appDisplayName(document.getElementById('generate-app-package').value.trim());
  count.textContent = selected ? `${appName} · 已选择 ${selected} / ${generateKnowledgePages.length}` : `${appName} · ${generateKnowledgePages.length} 个页面，未勾选则自动匹配`;
}

async function loadRunnerDevices(options = {}) {
  const force = options && options.force;
  // round 4: 设备列表只在执行/生成等需要弹设备选择时拉取，后续切页直接复用
  if (!force && AppState.loaded.runners) {
    renderRunnerDevices();
    if (activeWorkflow === 'dashboard' && !hasOpenEditor()) showWorkflowGuide('dashboard');
    return;
  }
  try {
    const data = await apiRequest('/runners');
    runnerDevices = (data.devices || []).filter(device => device.runner_online && device.status === 'online');
    AppState.loaded.runners = true;
  } catch(e) {
    runnerDevices = [];
  }
  renderRunnerDevices();
  if (activeWorkflow === 'dashboard' && !hasOpenEditor()) showWorkflowGuide('dashboard');
}

function ensureRunnersLoaded(options = {}) {
  if (AppState.loaded.runners) return Promise.resolve();
  return loadRunnerDevices(options);
}

function renderDeviceOptions(selectId) {
  const select = document.getElementById(selectId);
  if (!select) return;
  const previous = select.value;
  select.innerHTML = '<option value="">请选择执行设备（不自动分配）</option><option value="__AUTO_DEVICE__">自动选择在线设备（可选）</option>';
  runnerDevices.forEach(device => {
    const opt = document.createElement('option');
    opt.value = `${device.runner_id}::${device.device_id}`;
    opt.textContent = `${device.label || device.device_id} / ${device.runner_id}`;
    select.appendChild(opt);
  });
  if (runnerDevices.length === 0) {
    const opt = document.createElement('option');
    opt.value = "";
    opt.textContent = "暂无在线 Runner 设备";
    select.appendChild(opt);
  }
  if (previous && Array.from(select.options).some(opt => opt.value === previous)) {
    select.value = previous;
  }
}

function renderRunnerDevices() {
  renderDeviceOptions('generate-device');
  renderDeviceOptions('run-file-device');
  renderDeviceOptions('run-task-device');
}

function selectedRunnerDevice(selectId='generate-device') {
  const value = document.getElementById(selectId)?.value || '';
  if (!value) return { runner_id: '', device_id: '', device_strategy: 'manual_required' };
  if (value === '__AUTO_DEVICE__') return { runner_id: '', device_id: '', device_strategy: 'auto' };
  const [runner_id, device_id] = value.split('::');
  return { runner_id: runner_id || '', device_id: device_id || '', device_strategy: 'fixed' };
}

function requireRunnerDevice(selectId='generate-device', statusId='', actionLabel='创建执行任务') {
  const selected = selectedRunnerDevice(selectId);
  if (selected.device_strategy !== 'manual_required') return selected;
  const message = `${actionLabel}前请先选择执行设备；如确实需要平台分配，请选择“自动选择在线设备（可选）”。`;
  const status = statusId ? document.getElementById(statusId) : null;
  if (status) {
    status.textContent = message;
    status.className = 'generate-status show error';
  }
  showToast(message, 'error');
  return null;
}

function jobDeviceLabel(job = {}) {
  if (job.device_id || job.deviceId) return job.device_id || job.deviceId;
  const strategy = job.device_strategy || job.deviceStrategy || '';
  return strategy === 'auto' ? '自动选择在线设备' : '未指定设备';
}

function jobRunnerLabel(job = {}) {
  if (job.target_runner_id || job.targetRunnerId || job.runner_id || job.runnerId) {
    return job.target_runner_id || job.targetRunnerId || job.runner_id || job.runnerId;
  }
  const strategy = job.device_strategy || job.deviceStrategy || '';
  return strategy === 'auto' ? '任意在线 Runner' : '未指定 Runner';
}

function syncRunModeControls() {
  const auto = document.getElementById('generate-auto-optimize');
  if (!auto) return;
  auto.checked = false;
  auto.disabled = true;
}

function syncRunFileModeControls() {
  const auto = document.getElementById('run-file-auto-optimize');
  if (!auto) return;
  auto.checked = false;
  auto.disabled = true;
}

function syncRunTaskModeControls() {
  const auto = document.getElementById('run-task-auto-optimize');
  if (!auto) return;
  auto.checked = false;
  auto.disabled = true;
}

function jobStatusText(status) {
  const map = {
    pending: '排队中',
    running: '执行中',
    success: '成功',
    passed: '成功',
    failed: '失败',
    cancelled: '已取消'
  };
  return map[status] || status || '未知';
}

function jobKindText(job) {
  if (job.kind === 'background') {
    const typeMap = { repair: 'AI修复', figma_parse: 'Figma解析', generate: 'AI生成', mindmap_only: '脑图生成' };
    if (job.type === 'mindmap_only') return '脑图生成';
    if (isGenerateBackgroundJob(job)) return 'AI生成';
    return typeMap[job.type] || '后台任务';
  }
  return job.target_task_name ? '单条执行' : '整文件执行';
}

function explainCallbackHttp000(value) {
  const text = String(value || '');
  if (!/(HTTP\s*:?\s*0{3}|HTTP\D*000|Progress post failed)/i.test(text)) return text;
  const hint = '回传 HTTP 000 表示 Runner/Sonic 没拿到服务端响应，请检查公网 8088、Nginx 代理、MIDSCENE_PUBLIC_BASE_URL 和 Runner 网络；可访问 /api/sonic/callback-diagnose 查看诊断。';
  return text.includes(hint) ? text : `${text}｜${hint}`;
}

function jobTimeText(job) {
  return job.finished_at || job.updated_at || job.started_at || job.created_at || '';
}

function jobDurationSeconds(job) {
  if (job.elapsed_seconds !== undefined && job.elapsed_seconds !== null && Number(job.elapsed_seconds) >= 0) {
    return Number(job.elapsed_seconds);
  }
  const start = parseLocalTime(job.started_at || job.created_at);
  const end = parseLocalTime(job.finished_at || (!['pending', 'running'].includes(job.status || '') ? job.updated_at : ''));
  if (start && end && end >= start) return Math.round((end - start) / 1000);
  if (start && ['pending', 'running'].includes(job.status || '')) return Math.max(0, Math.round((Date.now() - start) / 1000));
  return 0;
}

function jobDurationText(job) {
  const text = formatDurationSeconds(jobDurationSeconds(job));
  if (!text) return '';
  return ['pending', 'running'].includes(job.status || '') ? `已用时 ${text}` : `耗时 ${text}`;
}

function jobTimingText(job) {
  const start = job.started_at || job.created_at || '';
  const finish = job.finished_at || '';
  const duration = jobDurationText(job);
  return [start ? `开始 ${start}` : '', finish ? `结束 ${finish}` : '', duration].filter(Boolean).join(' · ');
}

function jobErrorText(job) {
  if (job.kind === 'background') {
    const detail = job.error_detail || job.errorDetail || {};
    return explainCallbackHttp000(detail.message || detail.error || job.error || (job.status === 'failed' ? job.message : '') || '');
  }
  const review = job.failure_review || {};
  const optimize = job.optimize_result || job.manual_repair_result || job.manual_task_repair_result || {};
  const evidence = Array.isArray(review.evidence) ? review.evidence.filter(Boolean).slice(0, 2).join('；') : '';
  if (review.reason && evidence && !review.reason.includes(evidence)) {
    return `${review.reason}｜证据：${evidence}`;
  }
  return explainCallbackHttp000(review.reason || evidence || optimize.error || extractJobRawError(job) || '');
}

function extractJobRawError(job) {
  const text = [
    job.stderr_tail || '',
    job.stdout_tail || '',
    job.report_upload_error || job.reportUploadError || '',
    job.report_missing_reason || job.reportMissingReason || '',
    job.upload_warning || job.uploadWarning || ''
  ].filter(Boolean).join('\n');
  if (!text) return '';
  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const patterns = [
    /执行前环境自检失败[:：]?\s*(.+)/,
    /问题[:：]\s*(.+)/,
    /error[:：]\s*(.+)/i,
    /Task failed[:：]\s*(.+)/i,
    /Assertion failed[:：]\s*(.+)/i,
    /failed to locate element[:：]\s*(.+)/i,
    /unknown flowItem in yaml[:：]\s*(.+)/i,
    /YAML.*解析失败[:：]?\s*(.+)/i,
    /Model configuration is incomplete[:：]?\s*(.+)/i
  ];
  for (const line of lines.slice().reverse()) {
    for (const pattern of patterns) {
      const match = line.match(pattern);
      if (match) return match[1] || line;
    }
  }
  return lines.slice(-3).join('；');
}

function jobReportHint(job) {
  if (job.kind === 'background') return '';
  const uploadPending = job.report_upload_pending || job.reportUploadPending;
  const uploadError = job.report_upload_error || job.reportUploadError || '';
  const missingReason = job.report_missing_reason || job.reportMissingReason || '';
  const localPath = job.local_report_path || job.localReportPath || '';
  const uploadWarning = job.upload_warning || job.uploadWarning || '';
  if (uploadPending) {
    return job.report_url
      ? 'Midscene 报告链接已预留，文件正在后台上传；稍后重新点击即可查看。'
      : 'Midscene 报告正在后台上传，不影响后续用例执行；上传完成后将自动关联。';
  }
  if (uploadError) {
    return `报告上传失败：${uploadError}${localPath ? `；Runner 本地报告：${localPath}` : ''}`;
  }
  if (job.report_url) return '';
  if (missingReason) return missingReason;
  if (localPath) return `报告未上传；Runner 本地报告：${localPath}`;
  if (!['pending', 'running'].includes(job.status || '')) return 'Runner 未回传 Midscene 报告地址，可能是报告上传失败或执行前未生成报告。';
  return '';
}

function jobProgressInfo(job) {
  const status = job.status || '';
  const total = Number(job.total_task_count || 0);
  const completed = Number(job.completed_task_count || 0);
  let progress = Number(job.progress || 0);
  if (status === 'success' || status === 'passed') progress = 100;
  if (!progress && total > 0) progress = Math.round((completed / total) * 100);
  progress = Math.max(0, Math.min(100, progress || 0));
  const current = job.current_task_name || job.target_task_name || '';
  const index = Number(job.current_task_index || 0);
  const detail = total > 0
    ? `用例 ${Math.min(completed + (status === 'running' ? 1 : 0), total)} / ${total}`
    : jobKindText(job);
  const currentText = current ? `当前：${current}` : (job.progress_message || detail);
  return {progress, total, completed, current, index, detail, currentText};
}

function jobActions(job) {
  const id = job.job_id || '';
  const parts = [];
  if (job.kind === 'background') {
    const result = job.result || {};
    const nextJob = result.next_job || {};
    const nextJobId = nextJob.job_id || '';
    const caseSetId = generationJobCaseSetId(job);
    const mod = generationJobModule(job);
    const file = generationJobTitle(job);
    if (['pending', 'running'].includes(job.status || '')) {
      parts.push(`<button class="job-action danger" onclick="cancelGenerateJob(${jsArg(id)})">取消</button>`);
    }
    parts.push(`<button class="job-action" onclick="toggleJobDetail(${jsArg(id)})">详情</button>`);
    if (isGenerateBackgroundJob(job) && caseSetId) {
      parts.push(`<button class="job-action" onclick="showGenerationReviewByCaseSet(${jsArg(caseSetId)})">生成分析</button>`);
      if (job.type !== 'mindmap_only') parts.push(`<button class="job-action" onclick="regenerateGenerationCases(${jsArg(caseSetId)})">重新生成用例</button>`);
      parts.push(`<a class="job-action" href="${mindmapDownloadUrl(caseSetId)}" target="_blank">下载脑图</a>`);
      parts.push(`<button class="job-action" onclick="regenerateGenerationMindmap(${jsArg(caseSetId)})" title="只按现有生成分析刷新脑图文件（FreeMind .mm）；不调用千问，不改用例，不覆盖 YAML">刷新脑图文件</button>`);
    }
    if (isGenerateBackgroundJob(job) && mod && file && /\.ya?ml$/i.test(file)) {
      parts.push(`<button class="job-action" onclick="openFile(${jsArg(mod)}, ${jsArg(file)})">打开YAML</button>`);
    }
    if (result.diff_summary || (result.changes || []).length) {
      parts.push(`<button class="job-action" onclick="showRepairResultFromJob(${jsArg(id)})">修复结果</button>`);
    }
    if (nextJobId) {
      parts.push(`<button class="job-action" onclick="focusJob(${jsArg(nextJobId)})">查看重跑任务</button>`);
    }
    if (job.status === 'failed') {
      parts.push(`<button class="job-action ai" onclick="analyzeFailureFromJob(${jsArg(id)})">AI分析失败原因</button>`);
      parts.push(`<button class="job-action ai" onclick="openAiRepairForJob(${jsArg(id)})">生成修复 YAML</button>`);
      parts.push(`<button class="job-action" onclick="generateBugDraftForJob(${jsArg(id)})">生成飞书缺陷草稿</button>`);
      parts.push(`<span class="job-link muted" title="${escapeHtml(job.error || job.message || '')}">任务失败</span>`);
    }
    return parts.join('');
  }
  if (['pending', 'running'].includes(job.status)) {
    parts.push(`<button class="job-action danger" onclick="cancelJob('${escapeHtml(id)}')">取消</button>`);
  }
  if (!['pending', 'running'].includes(job.status)) {
    parts.push(`<button class="job-action" onclick="retryJob('${escapeHtml(id)}')">重跑</button>`);
  }
  parts.push(`<button class="job-action" onclick="toggleJobDetail('${escapeHtml(id)}')">详情</button>`);
  if (job.status === 'failed') {
    parts.push(`<button class="job-action ai" onclick="analyzeFailureFromJob(${jsArg(id)})">AI分析失败原因</button>`);
    parts.push(`<button class="job-action ai" onclick="openAiRepairForJob(${jsArg(id)})">生成修复 YAML</button>`);
    parts.push(`<button class="job-action" onclick="generateBugDraftForJob(${jsArg(id)})">生成飞书缺陷草稿</button>`);
    parts.push(`<button class="job-action" onclick="reviewJob('${escapeHtml(id)}','product_bug')">Bug</button>`);
    parts.push(`<button class="job-action" onclick="reviewJob('${escapeHtml(id)}','script_issue')">脚本</button>`);
  }
  if (job.report_url) {
    const uploadPending = job.report_upload_pending || job.reportUploadPending;
    const uploadError = job.report_upload_error || job.reportUploadError || '';
    const label = uploadPending ? '报告上传中' : (uploadError ? '报告上传失败' : '报告');
    parts.push(`<a class="job-link${uploadPending || uploadError ? ' muted' : ''}" href="${escapeHtml(job.report_url)}" target="_blank" title="${escapeHtml(jobReportHint(job))}">${label}</a>`);
  } else {
    const hint = jobReportHint(job);
    if (hint) {
      const label = (job.report_upload_pending || job.reportUploadPending) ? '报告上传中' : '无报告';
      parts.push(`<span class="job-link muted" title="${escapeHtml(hint)}">${label}</span>`);
    }
  }
  return parts.join('');
}

function toggleJobDetail(jobId) {
  if (expandedJobs.has(jobId)) expandedJobs.delete(jobId);
  else expandedJobs.add(jobId);
  renderJobs();
}

function jobDetailHtml(job, error, reportHint) {
  if (job.kind === 'background') {
    const result = job.result || {};
    const detail = job.error_detail || job.errorDetail || {};
    const changes = Array.isArray(result.changes) ? result.changes.slice(0, 8).join('\n') : '';
    const diff = result.diff_summary || '';
    return `
      <div class="job-detail">
        <div><strong>Job：</strong>${escapeHtml(job.job_id || '')}</div>
        <div><strong>类型：</strong>${escapeHtml(jobKindText(job))} · ${escapeHtml(job.step || '')}</div>
        <div><strong>消息：</strong>${escapeHtml(explainCallbackHttp000(job.message || ''))}</div>
        ${jobTimingText(job) ? `<div><strong>耗时：</strong>${escapeHtml(jobTimingText(job))}</div>` : ''}
        ${detail.stage ? `<div><strong>失败阶段：</strong>${escapeHtml(detail.stage)} · ${escapeHtml(detail.type || '')} · ${escapeHtml(String(detail.progress || job.progress || 0))}%</div>` : ''}
        ${detail.suggestion ? `<div><strong>处理建议：</strong>${escapeHtml(detail.suggestion)}</div>` : ''}
        ${result.updated_file ? `<div><strong>文件：</strong>${escapeHtml(result.updated_file)}</div>` : ''}
        ${typeof result.changed_line_count !== 'undefined' ? `<div><strong>改动行数：</strong>${escapeHtml(String(result.changed_line_count))}</div>` : ''}
        ${changes ? `<pre>${escapeHtml(changes)}</pre>` : ''}
        ${diff ? `<pre>${escapeHtml(diff.slice(0, 1600))}</pre>` : ''}
        ${detail.error ? `<pre>原始错误:\n${escapeHtml(detail.error)}</pre>` : (error ? `<div><strong>错误：</strong>${escapeHtml(error)}</div>` : '')}
        ${job.error_trace ? `<pre>trace:\n${escapeHtml(String(job.error_trace).slice(-1800))}</pre>` : ''}
      </div>
    `;
  }
  const review = job.failure_review || {};
  const evidence = Array.isArray(review.evidence) ? review.evidence.filter(Boolean).join('\n') : '';
  const attempts = Array.isArray(job.attempts) ? job.attempts.slice(-3).map(item => `${item.device_id || item.deviceId || ''} ${item.status || ''}`).join('\n') : '';
  const stdout = job.stdout_tail || '';
  const stderr = job.stderr_tail || '';
  const uploadWarning = job.upload_warning || job.uploadWarning || '';
  const rawError = extractJobRawError(job);
  const timeline = jobTimelineHtml(job);
  return `
    <div class="job-detail">
      <div><strong>Job：</strong>${escapeHtml(job.job_id || '')}</div>
      <div><strong>执行方式：</strong>${escapeHtml(job.target_task_name ? '单条调试：只执行选中的 task' : '整文件回归：执行当前 YAML 全部 tasks')}</div>
      <div><strong>设备：</strong>${escapeHtml(jobDeviceLabel(job))} · ${escapeHtml(jobRunnerLabel(job))}</div>
      ${jobTimingText(job) ? `<div><strong>时间：</strong>${escapeHtml(jobTimingText(job))}</div>` : ''}
      ${timeline}
      ${review.category ? `<div><strong>复检：</strong>${escapeHtml(review.category)} / ${escapeHtml(review.failure_type || '')} / ${escapeHtml(String(review.confidence || ''))}</div>` : ''}
      ${rawError ? `<div><strong>原始错误：</strong>${escapeHtml(rawError)}</div>` : ''}
      ${error ? `<div><strong>失败原因：</strong>${escapeHtml(error)}</div>` : ''}
      ${evidence ? `<pre>${escapeHtml(evidence)}</pre>` : ''}
      ${attempts ? `<pre>${escapeHtml(attempts)}</pre>` : ''}
      ${reportHint ? `<div><strong>报告：</strong>${escapeHtml(reportHint)}</div>` : ''}
      ${uploadWarning ? `<div><strong>回传告警：</strong>${escapeHtml(uploadWarning)}</div>` : ''}
      ${stderr ? `<pre>stderr:\n${escapeHtml(stderr.slice(-1600))}</pre>` : ''}
      ${stdout ? `<pre>${escapeHtml(stdout.slice(-1200))}</pre>` : ''}
    </div>
  `;
}

function jobTimelineHtml(job) {
  const rows = [];
  if (job.created_at) rows.push({ ts: job.created_at, title: '创建任务', message: job.target_task_name ? `单条：${job.target_task_name}` : '整文件执行' });
  if (job.started_at) rows.push({ ts: job.started_at, title: 'Runner 拉取', message: `${jobRunnerLabel(job)} / ${jobDeviceLabel(job)}` });
  const events = Array.isArray(job.events) ? job.events : [];
  events.forEach(event => {
    if (!event || typeof event !== 'object') return;
    const title = event.title || event.type || '进度';
    const current = event.current_task_name || event.currentTaskName || '';
    const progress = event.progress !== undefined && event.progress !== null ? `${event.progress}%` : '';
    const counts = event.total_task_count || event.totalTaskCount
      ? `用例 ${event.completed_task_count || event.completedTaskCount || 0}/${event.total_task_count || event.totalTaskCount || 0}`
      : '';
    const message = [explainCallbackHttp000(event.message || ''), current ? `当前：${current}` : '', counts, progress].filter(Boolean).join(' · ');
    rows.push({ ts: event.ts || event.time || '', title, message });
  });
  if (job.finished_at) rows.push({ ts: job.finished_at, title: '执行结束', message: jobStatusText(job.status) });
  const deduped = [];
  const seen = new Set();
  rows.forEach(row => {
    const key = `${row.ts}|${row.title}|${row.message}`;
    if (seen.has(key)) return;
    seen.add(key);
    deduped.push(row);
  });
  if (!deduped.length) return '<div class="job-timeline-empty">暂无进度流水，Runner 拉取任务后会自动回传。</div>';
  return `
    <div class="job-timeline">
      <strong>进度流水</strong>
      ${deduped.slice(-12).map(row => `
        <div class="job-timeline-item">
          <span class="job-timeline-dot"></span>
          <div>
            <div class="job-timeline-title">${escapeHtml(row.title)}${row.ts ? ` · ${escapeHtml(row.ts)}` : ''}</div>
            ${row.message ? `<div class="job-timeline-message">${escapeHtml(row.message)}</div>` : ''}
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

async function focusJob(jobId) {
  await loadJobs();
  const row = Array.from(document.querySelectorAll('.job-row')).find(el => el.textContent.includes(jobId));
  if (row) row.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function showRepairResultFromJob(jobId) {
  const job = latestJobs.find(item => item.job_id === jobId);
  const result = job?.result || {};
  if (!job || !Object.keys(result).length) {
    showToast('这个后台任务还没有可展示的修复结果', 'error');
    return;
  }
  showRepairResult(result, 'AI 修复结果');
}

function normalizeJob(job={}) {
  const status = String(job.status || job.state || 'unknown').toLowerCase();
  const jobId = job.job_id || job.jobId || job.id || '';
  const taskName = job.target_task_name || job.current_task_name || job.task_name || job.taskName || job.file || '';
  const reportUrl = job.report_url || job.reportUrl || job.sonic_report_url || '';
  return {
    ...job,
    job_id: jobId,
    jobId,
    runId: job.run_id || job.runId || jobId,
    traceId: job.trace_id || job.traceId || '',
    status,
    standardStatus: status,
    currentStep: job.step || job.current_step || job.currentStep || '',
    taskName,
    report_url: reportUrl,
    reportUrl,
    error: job.error || job.message || job.error_message || '',
    failureReview: job.failure_review || job.failureReview || {},
    repairDraft: job.repair_draft || job.repairDraft || {}
  };
}

function buildPendingActions(jobs=[], activeJobs=[]) {
  const actions = [];
  repairDrafts
    .filter(draft => ['DRAFTED', 'WAIT_CONFIRM'].includes(String(draft.status || '').toUpperCase()))
    .slice(0, 6)
    .forEach(draft => {
      const draftId = draft.draftId || draft.draft_id || '';
      actions.push({
        id: `repair:${draftId}`,
        type: (draft.riskHits || []).length ? 'RISK_CONFIRM' : 'REPAIR_DRAFT',
        title: draft.taskName || draft.file || 'YAML 修复草稿',
        meta: `${repairDraftStatusText(draft.status)} · ${draft.failureType || 'SCRIPT_ISSUE'}${(draft.riskHits || []).length ? ` · 风险 ${draft.riskHits.join('、')}` : ''}`,
        actions: `
          <button class="btn-sm" onclick="openRepairDraft(${jsArg(draftId)})">查看草稿</button>
          <button class="btn-sm success" onclick="confirmApplyRepairDraft(${jsArg(draftId)})">人工确认替换</button>
          <button class="btn-sm danger" onclick="rejectRepairDraft(${jsArg(draftId)})">拒绝</button>
        `
      });
    });
  const draftJobIds = new Set(repairDrafts.map(draft => draft.jobId || draft.job_id).filter(Boolean));
  jobs
    .filter(job => ['failed', 'timeout', 'cancelled'].includes(String(job.status || '').toLowerCase()))
    .filter(job => !draftJobIds.has(job.job_id))
    .slice(0, 6)
    .forEach(job => {
      const normalized = normalizeFailureAnalysis(job.failureReview || job.failure_review || job.error || '');
      let actionsHtml = `<button class="btn-sm" onclick="focusJob(${jsArg(job.job_id || '')})">查看</button>`;
      if (normalized.failureType === 'SCRIPT_ISSUE' && (job.failureReview || job.failure_review)) {
        actionsHtml += `<button class="btn-sm ai" onclick="openAiRepairForJob(${jsArg(job.job_id || '')})">生成修复草稿</button>`;
      } else if (normalized.failureType === 'PRODUCT_BUG') {
        actionsHtml += `<button class="btn-sm" onclick="generateBugDraftForJob(${jsArg(job.job_id || '')})">生成缺陷草稿</button>`;
      } else {
        actionsHtml += `<button class="btn-sm ai" onclick="analyzeFailureFromJob(${jsArg(job.job_id || '')}, {renderPage:true})">AI分析</button>`;
      }
      actions.push({
        id: `job:${job.job_id}`,
        type: normalized.failureType || 'FAILED_JOB',
        title: job.taskName || job.file || job.job_id || '失败任务',
        meta: `${jobStatusText(job.status)} · ${normalized.conclusion || jobErrorText(job) || '等待分析'}`,
        actions: actionsHtml
      });
    });
  const run = currentAgentRun();
  if (run && ['WAIT_CONFIRM_RUN', 'WAIT_CONFIRM_BUG'].includes(run.status)) {
    actions.unshift({
      id: `agent:${run.runId}`,
      type: run.status,
      title: run.options?.goal || run.taskName || 'Agent 等待确认',
      meta: run.status === 'WAIT_CONFIRM_RUN' ? 'YAML 已通过校验，等待确认是否执行 Sonic' : '缺陷草稿已生成，等待确认是否提交',
      actions: run.status === 'WAIT_CONFIRM_RUN'
        ? `<button class="btn-sm success" onclick="confirmAgentRun('CONFIRM_RUN')">确认执行</button><button class="btn-sm" onclick="setAgentTab('yaml')">查看 YAML</button><button class="btn-sm danger" onclick="cancelAgentRun()">取消</button>`
        : `<button class="btn-sm success" onclick="confirmAgentRun('CONFIRM_BUG')">提交缺陷</button><button class="btn-sm" onclick="setAgentTab('bug')">查看草稿</button><button class="btn-sm" onclick="confirmAgentRun('SKIP_BUG')">暂不提交</button>`
    });
  }
  return actions.slice(0, 8);
}

function pendingActionCardHtml(action) {
  return `
    <div class="job-meta pending-action-card" style="margin:8px 0;">
      <strong>${escapeHtml(action.title || '待处理')}</strong><br>
      <span>${escapeHtml(action.meta || action.type || '')}</span>
      <div class="job-actions" style="margin-top:6px;">${action.actions || ''}</div>
    </div>
  `;
}

function currentTaskCardHtml(activeJobs=[]) {
  const job = activeJobs[0] || normalizeJob(latestJobs[0] || {});
  if (!job?.job_id) return '<div class="job-meta">当前没有执行中的任务。</div>';
  const draft = job.repairDraft && Object.keys(job.repairDraft).length ? job.repairDraft : repairDrafts.find(item => (item.jobId || item.job_id) === job.job_id);
  const review = job.failureReview || {};
  return `
    <div class="job-meta">
      <strong>${escapeHtml(job.taskName || job.file || job.job_id)}</strong><br>
      Job: ${escapeHtml(job.job_id || '-')}<br>
      Run: ${escapeHtml(job.runId || '-')} · Trace: ${escapeHtml(job.traceId || '-')}<br>
      状态: ${escapeHtml(jobStatusText(job.status))}${job.currentStep ? ` · 步骤: ${escapeHtml(job.currentStep)}` : ''}<br>
      ${job.error ? `最近错误: ${escapeHtml(job.error).slice(0, 160)}<br>` : ''}
      ${review.category ? `AI 复检: ${escapeHtml(review.category)} · ${escapeHtml(review.reason || '')}<br>` : ''}
      ${draft ? `修复草稿: ${escapeHtml(repairDraftStatusText(draft.status))} · ${escapeHtml(draft.failureType || '')}<br>` : ''}
      ${job.reportUrl ? `<a class="job-link" href="${escapeHtml(job.reportUrl)}" target="_blank">查看报告</a>` : ''}
    </div>
  `;
}

function renderJobs() {
  const list = document.getElementById('jobs-list');
  const count = document.getElementById('jobs-count');
  if (!list || !count) return;
  // Agent 状态：dashboard / agent_history / agent_confirm 默认走 Agent 状态视图
  if (['dashboard', 'agent', 'agent_history', 'agent_confirm'].includes(activeWorkflow)) {
    renderAgentCenter();
    return;
  }
  const jobsTitle = document.getElementById('jobs-title');
  if (jobsTitle) jobsTitle.textContent = 'Agent 状态';
  updateToolbarState();
  renderEditorContextBar();
  const timeValue = job => Date.parse((job.updated_at || job.finished_at || job.started_at || job.created_at || '').replace(' ', 'T')) || 0;
  const normalizedJobs = latestJobs.map(job => normalizeJob({...job, kind: job.kind || 'runner'}));
  const activeJobs = normalizedJobs
    .filter(job => ['pending', 'running'].includes(job.status))
    .sort((a, b) => timeValue(b) - timeValue(a));
  const activeIds = new Set(activeJobs.map(job => job.job_id));
  const recentDone = normalizedJobs
    .filter(job => !activeIds.has(job.job_id))
    .sort((a, b) => timeValue(b) - timeValue(a))
    .slice(0, 18);
  const jobs = [...activeJobs, ...recentDone].slice(0, 40);
  const activeCount = activeJobs.length;
  count.textContent = activeCount ? `${activeCount} 个进行中 / 显示 ${jobs.length} 个` : `最近 ${jobs.length} 个`;
  if (!jobs.length) {
    list.innerHTML = '<div class="job-empty">暂无执行任务</div>';
    if (activeWorkflow === 'dashboard' && !hasOpenEditor()) showWorkflowGuide('dashboard');
    return;
  }
  const pendingActions = buildPendingActions(normalizedJobs, activeJobs);
  const todoHtml = `
    <div class="agent-side-card">
      <div class="agent-side-title">待我处理</div>
      ${pendingActions.length ? pendingActions.map(pendingActionCardHtml).join('') : '<div class="job-meta">暂无需要人工处理的失败或确认项。</div>'}
    </div>
    <div class="agent-side-card">
      <div class="agent-side-title">当前任务</div>
      ${currentTaskCardHtml(activeJobs)}
    </div>
  `;
  list.innerHTML = todoHtml + jobs.map(job => {
    const status = job.status || 'unknown';
    const targetTask = job.target_task_name || '';
    const device = jobDeviceLabel(job);
    const runner = jobRunnerLabel(job);
    const error = jobErrorText(job);
    const reportHint = jobReportHint(job);
    const queueMessage = job.queue_message || job.dispatch_message || '';
    const progress = jobProgressInfo(job);
    const title = job.kind === 'background'
      ? `${jobKindText(job)} / ${escapeHtml(job.module || job.title || job.step || '')}`
      : `${escapeHtml(job.module || '')}/${escapeHtml(job.file || '')}`;
    return `
      <div class="job-row ${escapeHtml(status)} ${expandedJobs.has(job.job_id || '') ? 'expanded' : ''}" title="${escapeHtml(error)}">
        <div class="job-row-head">
          <span class="job-badge ${escapeHtml(status)}">${jobStatusText(status)}</span>
          <div class="job-meta">${escapeHtml(jobTimeText(job))}</div>
        </div>
        <div class="job-main">
          <div class="job-file">${title}</div>
          <div class="job-task">${targetTask ? `单条：${escapeHtml(targetTask)}` : escapeHtml(job.message || job.step || jobKindText(job))}</div>
        </div>
        <div class="job-progress">
          <div class="job-progress-text">
            <span>${escapeHtml(progress.currentText)}</span>
            <span>${progress.progress}%</span>
          </div>
          <div class="job-progress-track"><div class="job-progress-bar" style="width:${progress.progress}%"></div></div>
          <div class="job-meta">${job.kind === 'background' ? escapeHtml(job.job_id || '') : `${escapeHtml(progress.detail)} · ${escapeHtml(device)} · ${escapeHtml(runner)}`}</div>
        </div>
        ${queueMessage ? `<div class="job-meta">${escapeHtml(queueMessage)}</div>` : ''}
        ${error ? `<div class="job-meta">${escapeHtml(error)}</div>` : ''}
        ${reportHint ? `<div class="job-meta job-report-hint">${escapeHtml(reportHint)}</div>` : ''}
        <div class="job-actions">${jobActions(job) || '-'}</div>
        ${jobDetailHtml(job, error, reportHint)}
      </div>
    `;
  }).join('');
  if (activeWorkflow === 'dashboard' && !hasOpenEditor()) showWorkflowGuide('dashboard');
}

async function postJobAction(jobId, action, payload={}) {
  const data = await apiRequest(`/jobs/${encodeURIComponent(jobId)}/${action}`, {
    method: 'POST',
    body: JSON.stringify(payload)
  });
  await loadJobs();
  return data;
}

async function cancelJob(jobId) {
  if (!confirm(`确认取消任务 ${jobId}？`)) return;
  try {
    await postJobAction(jobId, 'cancel', { reason: 'manual' });
    showToast('✓ 任务已取消', 'success');
  } catch(e) {
    showToast(e.message || '取消失败', 'error');
  }
}

async function cancelGenerateJob(jobId) {
  if (!jobId) return;
  if (!confirm(`确认取消后台生成任务 ${jobId}？已发出的模型请求可能还会在后台返回，但平台会保持取消状态。`)) return;
  try {
    await apiRequest(`/ui/generate-jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: 'POST',
      body: JSON.stringify({ reason: 'manual' })
    });
    showToast('✓ 后台生成任务已取消', 'success');
    await loadJobs(true);
    if (activeWorkflow === 'generate' && document.getElementById('editor-area')?.textContent.includes('生成任务与生成记录')) {
      renderGenerateJobsCenter();
    }
  } catch(e) {
    showToast(e.message || '取消生成任务失败', 'error');
  }
}

async function retryJob(jobId) {
  try {
    await postJobAction(jobId, 'retry', {});
    showToast('✓ 已创建重跑任务', 'success');
  } catch(e) {
    showToast(e.message || '重跑失败', 'error');
  }
}

async function reviewJob(jobId, category) {
  const reason = prompt(category === 'product_bug' ? '标记为产品 Bug，备注原因：' : '标记为脚本问题，备注原因：', '');
  if (reason === null) return;
  try {
    await postJobAction(jobId, 'review', { category, reason });
    showToast(category === 'product_bug' ? '✓ 已标记为产品 Bug' : '✓ 已标记为脚本问题', 'success');
  } catch(e) {
    showToast(e.message || '归因失败', 'error');
  }
}

async function loadJobs(manual=false, forceJobList=false) {
  if (!forceJobList && ['dashboard', 'agent', 'agent_history', 'agent_confirm'].includes(activeWorkflow)) {
    await refreshAgentRuns(manual);
    return;
  }
  try {
    const [data] = await Promise.all([
      apiRequest('/jobs'),
      loadRepairDrafts({silent: true})
    ]);
    const runnerJobs = (data.jobs || []).map(job => ({...job, kind: 'runner'}));
    const backgroundJobs = (data.background_jobs || []).map(job => ({...job, kind: 'background'}));
    latestJobs = [...runnerJobs, ...backgroundJobs];
    AppState.loaded.jobs = true;
    renderJobs();
    renderModules();
    if (manual) showToast('✓ 执行任务已刷新', 'success');
  } catch(e) {
    const count = document.getElementById('jobs-count');
    if (count) count.textContent = e.message || '任务读取失败';
    if (manual) showToast(e.message || '任务读取失败', 'error');
  }
}

// round 4: 仅在执行/报告/失败重跑等需要 jobs 数据的页面调用
function ensureJobsLoaded(options = {}) {
  const force = options && options.force;
  if (force) return loadJobs(false);
  if (AppState.loaded.jobs) return Promise.resolve();
  return loadJobs(false);
}

function startJobsAutoRefresh() {
  // round 4: 仅在执行/报告等页面开启 jobs 轮询
  if (jobsRefreshTimer) clearInterval(jobsRefreshTimer);
  loadJobs();
  jobsRefreshTimer = setInterval(loadJobs, 2500);
  AppState.polling.jobs = jobsRefreshTimer;
}

function stopJobsAutoRefresh() {
  if (jobsRefreshTimer) {
    clearInterval(jobsRefreshTimer);
    jobsRefreshTimer = null;
  }
  AppState.polling.jobs = null;
}

function renderKnowledgeApps() {
  const list = document.getElementById('knowledge-app-list');
  const current = document.getElementById('knowledge-app-package').value.trim() || 'com.kfb.model';
  const apps = allAppPackages();
  list.innerHTML = apps.map(app => {
    const info = appInfoByPackage(app) || {};
    const modules = info.modules || [];
    const pageCount = info.page_count ?? knowledgePages.filter(page => page.app_package === app).length;
    return `
    <div class="knowledge-app-item ${app === current ? 'active' : ''}" onclick="selectKnowledgeApp(${jsArg(app)})" title="${escapeHtml(appDisplayLabel(app))}">
      <div class="knowledge-app-text">
        <div class="knowledge-app-name">${escapeHtml(appDisplayName(app))}</div>
        <div class="knowledge-app-package">${escapeHtml(app)}</div>
        <div class="knowledge-app-meta">${pageCount || 0} 个页面 · 基线 ${info.baseline_count || 0} / 测试 ${info.test_count || 0}${modules.length ? ` · ${escapeHtml(modules.join('、'))}` : ''}</div>
      </div>
      <button class="knowledge-app-del" type="button" onclick="event.stopPropagation(); deleteKnowledgeApp(${jsArg(app)})" title="删除该应用页面知识">×</button>
    </div>
  `}).join('');
}

async function selectKnowledgeApp(appPackage) {
  document.getElementById('knowledge-app-package').value = appPackage;
  document.getElementById('generate-app-package').value = appPackage;
  rememberAppPackage(appPackage);
  syncAppSelect('knowledge');
  syncAppSelect('generate');
  clearFigmaImport();
  clearKnowledgeForm(false);
  renderKnowledgeApps();
  await loadKnowledgePages();
}

async function loadKnowledgePages() {
  const appPackage = document.getElementById('knowledge-app-package').value.trim() || 'com.kfb.model';
  const tier = document.getElementById('knowledge-tier-filter')?.value || 'all';
  rememberAppPackage(appPackage);
  try {
    const data = await apiRequest(`/knowledge/pages?app_package=${encodeURIComponent(appPackage)}&tier=${encodeURIComponent(tier)}`);
    knowledgePages = data.pages || [];
    const existing = knowledgeAppDetails.find(app => app.package === appPackage);
    if (existing) {
      existing.name = data.app_name || existing.name || appPackage;
      existing.modules = data.modules || existing.modules || [];
      existing.page_count = knowledgePages.length;
      existing.has_knowledge = knowledgePages.length > 0;
    } else {
      knowledgeAppDetails.push({
        package: appPackage,
        name: data.app_name || appDisplayName(appPackage),
        modules: data.modules || [],
        page_count: knowledgePages.length,
        has_knowledge: knowledgePages.length > 0
      });
    }
    renderKnowledgeApps();
    renderAppPackageSelects();
    renderKnowledgePages(knowledgePages);
    setKnowledgeStatus(`当前应用「${appDisplayLabel(appPackage)}」${tier === 'all' ? '全部' : (tier === 'baseline' ? '基线库' : '测试库')}共有 ${knowledgePages.length} 个页面知识。`);
  } catch(e) {
    knowledgePages = [];
    renderKnowledgePages([]);
    setKnowledgeStatus(e.message || '读取页面知识失败', 'error');
  }
}

function filteredKnowledgePages() {
  const keyword = (document.getElementById('knowledge-page-search')?.value || '').trim().toLowerCase();
  if (!keyword) return knowledgePages;
  return knowledgePages.filter(page => [
    page.page_name,
    page.page_id,
    page.route,
    page.description,
    (page.key_elements || []).join(' '),
    (page.common_assertions || []).join(' ')
  ].join(' / ').toLowerCase().includes(keyword));
}

function clearKnowledgeSearch() {
  const input = document.getElementById('knowledge-page-search');
  if (input) input.value = '';
  renderKnowledgePages();
}

function renderKnowledgePages(pages=filteredKnowledgePages()) {
  const list = document.getElementById('knowledge-list');
  if (!pages.length) {
    list.innerHTML = '<div class="knowledge-row"><div class="knowledge-main"><div class="knowledge-title">暂无页面知识</div><div class="knowledge-sub">换个筛选条件，或先保存首页、我的页、核心业务页。</div></div></div>';
    return;
  }
  list.innerHTML = pages.map(page => `
    <div class="knowledge-row ${document.getElementById('knowledge-page-id')?.value === page.page_id ? 'active' : ''}" onclick="editKnowledgePage(${jsArg(page.page_id)})">
      <div class="knowledge-main">
        <div class="knowledge-title">${escapeHtml(page.page_name)} <span class="knowledge-badge ${page.tier === 'baseline' ? 'baseline' : ''}">${page.tier === 'baseline' ? '基线库' : '测试库'}</span></div>
        <div class="knowledge-sub">${escapeHtml(page.route || page.description || page.page_id)}</div>
      </div>
      <div class="knowledge-row-actions">
        <button class="knowledge-mini-btn" type="button" onclick="event.stopPropagation(); editKnowledgePage(${jsArg(page.page_id)})" title="编辑">编</button>
        <button class="knowledge-mini-btn" type="button" onclick="event.stopPropagation(); toggleKnowledgeTier(${jsArg(page.page_id)})" title="${page.tier === 'baseline' ? '移回测试库' : '标记为基线库'}">${page.tier === 'baseline' ? '测' : '基'}</button>
        <button class="knowledge-mini-btn danger" type="button" onclick="event.stopPropagation(); deleteKnowledgePage(${jsArg(page.app_package)},${jsArg(page.page_id)})" title="删除">删</button>
      </div>
    </div>
  `).join('');
}

function clearKnowledgeForm(clearStatus=true) {
  knowledgeScreenshot = null;
  setKnowledgePreview('');
  document.getElementById('knowledge-page-id').value = '';
  document.getElementById('knowledge-page-name').value = '';
  document.getElementById('knowledge-tier').value = 'test';
  document.getElementById('knowledge-route').value = '';
  document.getElementById('knowledge-description').value = '';
  document.getElementById('knowledge-elements').value = '';
  document.getElementById('knowledge-assertions').value = '';
  document.getElementById('knowledge-screenshot-name').textContent = '点上方虚线区域后 Ctrl/Command + V 可直接粘贴截图。';
  if (clearStatus) setKnowledgeStatus('新建页面知识：粘贴截图后可让 AI 自动识别。');
}

function editKnowledgePage(pageId) {
  const page = knowledgePages.find(item => item.page_id === pageId);
  if (!page) return;
  knowledgeScreenshot = null;
  document.getElementById('knowledge-page-id').value = page.page_id || '';
  document.getElementById('knowledge-page-name').value = page.page_name || '';
  document.getElementById('knowledge-tier').value = page.tier || 'test';
  document.getElementById('knowledge-route').value = page.route || '';
  document.getElementById('knowledge-description').value = page.description || '';
  document.getElementById('knowledge-elements').value = (page.key_elements || []).join('\n');
  document.getElementById('knowledge-assertions').value = (page.common_assertions || []).join('\n');
  document.getElementById('knowledge-screenshot-name').textContent = page.screenshot ? `已保存截图：${page.screenshot}；粘贴新图可替换。` : '当前页面未保存截图，可粘贴补充。';
  if (page.screenshot) {
    setKnowledgePreview(`${API_BASE}/knowledge/screenshot?app_package=${encodeURIComponent(page.app_package)}&page_id=${encodeURIComponent(page.page_id)}&t=${Date.now()}`);
  } else {
    setKnowledgePreview('');
  }
  setKnowledgeStatus(`正在编辑：${page.page_name}`, 'success');
  renderKnowledgePages();
}

async function toggleKnowledgeTier(pageId) {
  const page = knowledgePages.find(item => item.page_id === pageId);
  if (!page) return;
  const nextTier = page.tier === 'baseline' ? 'test' : 'baseline';
  try {
    const payload = {
      ...page,
      tier: nextTier,
      screenshot: null
    };
    await apiRequest('/knowledge/page', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    await loadKnowledgeApps();
    await loadKnowledgePages();
    await refreshKnowledgeManagerIfVisible();
    setKnowledgeStatus(`已${nextTier === 'baseline' ? '标记为基线库' : '移回测试库'}：${page.page_name}`, 'success');
  } catch(e) {
    setKnowledgeStatus(e.message || '切换知识库类型失败', 'error');
  }
}

async function saveKnowledgePage() {
  const appPackage = document.getElementById('knowledge-app-package').value.trim() || 'com.kfb.model';
  const pageName = document.getElementById('knowledge-page-name').value.trim();
  if (!pageName) {
    setKnowledgeStatus('页面名称不能为空', 'error');
    return;
  }
  const payload = {
    app_package: appPackage,
    page_id: document.getElementById('knowledge-page-id').value.trim(),
    page_name: pageName,
    tier: document.getElementById('knowledge-tier').value || 'test',
    route: document.getElementById('knowledge-route').value.trim(),
    description: document.getElementById('knowledge-description').value.trim(),
    key_elements: linesFromTextarea('knowledge-elements'),
    common_assertions: linesFromTextarea('knowledge-assertions'),
    screenshot: knowledgeScreenshot
  };
  try {
    await apiRequest('/knowledge/page', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    knowledgeScreenshot = null;
    document.getElementById('knowledge-screenshot-name').textContent = '已保存；粘贴新截图可替换当前页面截图。';
    await loadKnowledgeApps();
    const tierFilter = document.getElementById('knowledge-tier-filter');
    const savedTier = payload.tier || 'test';
    if (tierFilter && tierFilter.value !== 'all' && tierFilter.value !== savedTier) {
      tierFilter.value = savedTier;
    }
    await loadKnowledgePages();
    await refreshKnowledgeManagerIfVisible();
    setKnowledgeStatus(`已保存：${pageName}。后续可在左侧列表点击再次编辑。`, 'success');
    showToast(`✓ 已保存页面知识：${pageName}`, 'success');
  } catch(e) {
    setKnowledgeStatus(e.message || '保存失败', 'error');
  }
}

async function deleteKnowledgePage(appPackage, pageId) {
  if (!confirm(`删除页面知识「${pageId}」？`)) return;
  try {
    await apiRequest(`/knowledge/page?app_package=${encodeURIComponent(appPackage)}&page_id=${encodeURIComponent(pageId)}`, { method: 'DELETE' });
    await loadKnowledgePages();
    await refreshKnowledgeManagerIfVisible();
    showToast('✓ 已删除页面知识', 'success');
  } catch(e) {
    setKnowledgeStatus(e.message || '删除失败', 'error');
  }
}

async function deleteKnowledgeApp(appPackage) {
  if (!confirm(`删除应用「${appPackage}」下的全部页面知识？这个操作不可恢复。`)) return;
  try {
    await apiRequest(`/knowledge/app?app_package=${encodeURIComponent(appPackage)}`, { method: 'DELETE' });
    knowledgeApps = knowledgeApps.filter(app => app !== appPackage);
    knowledgeAppDetails = knowledgeAppDetails.filter(app => app.package !== appPackage);
    recentAppPackages = recentAppPackages.filter(app => app !== appPackage);
    localStorage.setItem('midscene_recent_apps', JSON.stringify(recentAppPackages));
    const fallback = knowledgeApps.find(app => app !== appPackage) || 'com.kfb.model';
    document.getElementById('knowledge-app-package').value = fallback === appPackage ? 'com.kfb.model' : fallback;
    document.getElementById('generate-app-package').value = document.getElementById('knowledge-app-package').value;
    clearKnowledgeForm(false);
    await loadKnowledgeApps();
    await loadKnowledgePages();
    showToast('✓ 已删除应用知识库', 'success');
  } catch(e) {
    setKnowledgeStatus(e.message || '删除应用失败', 'error');
  }
}


// ===== GENERATE YAML =====
function formatBytes(size) {
  if (!size) return '0 B';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function parseLocalTime(value) {
  if (!value) return 0;
  const text = String(value).trim();
  const ms = Date.parse(text.replace(/-/g, '/'));
  return Number.isFinite(ms) ? ms : 0;
}

function formatDurationSeconds(seconds) {
  const total = Math.max(0, Math.round(Number(seconds || 0)));
  if (!total) return '';
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}小时${m}分${s}秒`;
  if (m) return `${m}分${s}秒`;
  return `${s}秒`;
}

function setGenerateStatus(text, type='') {
  const el = document.getElementById('generate-status');
  if (!el) return;
  el.textContent = text || '';
  el.className = `generate-status${text ? ' show' : ''}${type ? ` ${type}` : ''}`;
}

function setGenerateWizardStep(stepIndex=0, state='running') {
  const normalized = Math.max(0, Math.min(3, Number(stepIndex || 0)));
  document.querySelectorAll('#generate-wizard .generate-wizard-step').forEach((step, index) => {
    step.classList.toggle('done', index < normalized || state === 'success');
    step.classList.toggle('active', state !== 'error' && index === normalized);
    step.classList.toggle('error', state === 'error' && index === normalized);
  });
}

function setGenerateProgress(percent, label, activeStep=0, visible=true) {
  const progress = document.getElementById('generate-progress');
  const bar = document.getElementById('generate-progress-bar');
  const percentEl = document.getElementById('generate-progress-percent');
  const labelEl = document.getElementById('generate-progress-label');
  if (!progress || !bar || !percentEl || !labelEl) return;
  const safePercent = Math.max(0, Math.min(100, Math.round(percent)));
  progress.classList.toggle('show', visible);
  bar.style.background = 'var(--accent)';
  bar.style.width = `${safePercent}%`;
  percentEl.textContent = `${safePercent}%`;
  labelEl.textContent = label || '准备生成';
  const wizardStep = activeStep >= 4 ? 3 : activeStep >= 3 ? 2 : activeStep >= 2 ? 1 : 0;
  setGenerateWizardStep(wizardStep, safePercent >= 100 ? 'success' : 'running');
  document.querySelectorAll('#generate-progress-steps .progress-step').forEach((step, index) => {
    step.classList.toggle('done', index < activeStep);
    step.classList.toggle('active', index === activeStep);
  });
}

function setGenerateProgressError(label='生成失败') {
  const progress = document.getElementById('generate-progress');
  const bar = document.getElementById('generate-progress-bar');
  const percentEl = document.getElementById('generate-progress-percent');
  const labelEl = document.getElementById('generate-progress-label');
  if (!progress || !bar || !percentEl || !labelEl) return;
  progress.classList.add('show');
  bar.style.width = '90%';
  bar.style.background = 'var(--danger)';
  percentEl.textContent = '失败';
  labelEl.textContent = label;
  setGenerateWizardStep(1, 'error');
  document.querySelectorAll('#generate-progress-steps .progress-step').forEach((step, index) => {
    step.classList.toggle('done', index < 2);
    step.classList.toggle('active', index === 2);
  });
}

function stopGenerateProgress() {
  if (generateProgressTimer) {
    clearInterval(generateProgressTimer);
    generateProgressTimer = null;
  }
  if (generateProgressDelayTimer) {
    clearTimeout(generateProgressDelayTimer);
    generateProgressDelayTimer = null;
  }
}

function startGenerateProgress() {
  stopGenerateProgress();
  let current = 35;
  setGenerateProgress(current, '模型正在拆解用例并转换 YAML', 2);
  generateProgressTimer = setInterval(() => {
    current = Math.min(90, current + (current < 70 ? 3 : 1));
    setGenerateProgress(current, '模型仍在生成，请稍等', 2);
    if (current >= 90) stopGenerateProgress();
  }, 1200);
}

function compactResponseText(text) {
  return String(text || '')
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 180);
}

async function readJsonResponse(res) {
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : {};
  } catch(e) {
    const summary = compactResponseText(text);
    if (res.status === 502 || /bad gateway/i.test(summary)) {
      throw new Error('服务端网关错误 502：请检查 Python 服务是否运行，或查看 /opt/midscene-upload.log');
    }
    if (res.status === 504 || /gateway time.?out/i.test(summary)) {
      throw new Error('服务端生成超时 504：需求文件较大或模型响应较慢，需要调大 nginx proxy_read_timeout');
    }
    if (res.status === 413 || /request entity too large/i.test(summary)) {
      throw new Error('上传内容过大 413：需要调大 nginx client_max_body_size');
    }
    throw new Error(`接口返回的不是 JSON：HTTP ${res.status}${summary ? `，${summary}` : ''}`);
  }
  if (!res.ok || data.ok === false || data.success === false) {
    throw new Error((data && data.error) || `请求失败：HTTP ${res.status}`);
  }
  return data;
}

function sleepMs(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function pollGenerateJob(jobId) {
  for (;;) {
    const data = await apiRequest(`/ui/generate-status?job_id=${encodeURIComponent(jobId)}`);
    const job = data.job || {};
    const progress = Number(job.progress || 0);
    const status = job.status || 'running';
    const label = job.message || job.step || '正在生成';
    const stepIndex = progress >= 95 ? 4 : progress >= 80 ? 3 : progress >= 40 ? 2 : progress >= 15 ? 1 : 0;

    if (status === 'failed') {
      setGenerateProgressError(job.step || '生成失败');
      throw new Error(job.error || job.message || '生成失败');
    }

    setGenerateProgress(progress, label, stepIndex);
    setGenerateStatus(label, status === 'success' ? 'success' : 'busy');

    if (status === 'success') {
      return job.result || {};
    }

    await sleepMs(1200);
  }
}

async function pollGenericJob(jobId, onUpdate) {
  let tick = 0;
  for (;;) {
    const data = await apiRequest(`/ui/generate-status?job_id=${encodeURIComponent(jobId)}`);
    const job = data.job || {};
    if (typeof onUpdate === 'function') onUpdate(job);
    tick += 1;
    if (tick === 1 || tick % 3 === 0) loadJobs(false, true).catch(() => {});
    if (job.status === 'failed') {
      throw new Error(job.error || job.message || '任务失败');
    }
    if (job.status === 'success') {
      return job.result || {};
    }
    await sleepMs(1000);
  }
}

function buildGenerateResultNote(data) {
  const pages = data.knowledgePages || [];
  const yamlCheck = data.yamlCheck || {};
  const review = data.review || {};
  const summary = data.summary || {};
  const counts = summary.counts || {};
  const requirementAnalysis = summary.requirement_analysis || data.requirementAnalysis || data.analysis || {};
  const coverageAudit = data.coverageAudit || {};
  const parts = [];
  if (requirementAnalysis.readiness_score !== undefined || requirementAnalysis.readiness_level) {
    const label = readinessLabel(requirementAnalysis.readiness_level);
    parts.push(`资料完整度：${requirementAnalysis.readiness_score || 0}/100，${label.text}。`);
  }
  if ((requirementAnalysis.requirement_points || []).length) {
    const goals = (requirementAnalysis.business_goals || []).slice(0, 2).join('、');
    parts.push(`需求分析：提取 ${requirementAnalysis.requirement_points.length} 个需求点${goals ? `；业务目标：${goals}` : ''}。`);
  }
  if (data.scenarioCount) {
    parts.push(`已先设计 ${data.scenarioCount} 个测试场景，再筛选自动化用例。`);
  }
  if (summary.case_set_id) {
    const priorityText = counts.priority_counts
      ? Object.entries(counts.priority_counts).map(([k, v]) => `${k} ${v}`).join('，')
      : '';
    parts.push(`已生成批次汇总：${summary.case_set_id}；冒烟 ${counts.smoke_count || 0} 条${priorityText ? `；${priorityText}` : ''}。`);
  }
  if (pages.length) {
    parts.push(`参考页面知识：${pages.map(page => page.page_name || page.page_id).join('、')}`);
  } else {
    parts.push('未命中页面知识库：建议先维护相关页面截图和入口文案。');
  }
  const reviewText = review.coverage_check || review.automation_check || review.assertion_check;
  if (reviewText) {
    parts.push(`自评审：${reviewText}`);
  }
  if (review.coverage_repair_error) {
    parts.push(`覆盖补全提示：${review.coverage_repair_error}`);
  }
  if (coverageAudit.requirement_point_count !== undefined) {
    const missing = (coverageAudit.missing_case_points || []).length;
    const generic = (coverageAudit.generic_assertion_cases || []).length;
    parts.push(`覆盖审查：需求点 ${coverageAudit.requirement_point_count || 0} 个，用例 ${coverageAudit.case_count || 0} 条，漏覆盖 ${missing} 个，泛化断言 ${generic} 条。`);
  }
  if (yamlCheck.warnings && yamlCheck.warnings.length) {
    parts.push(`YAML 检查提示：${yamlCheck.warnings.slice(0, 3).join('；')}`);
  } else if (yamlCheck.ok) {
    parts.push('YAML 基础检查通过。');
  }
  return parts.join('\n');
}

function reviewArray(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value.map(item => String(item || '').trim()).filter(Boolean);
  if (typeof value === 'object') return Object.values(value).map(item => String(item || '').trim()).filter(Boolean);
  return String(value).split(/\n|；|;/).map(item => item.trim()).filter(Boolean);
}

function reviewListHtml(items, empty='暂无') {
  const rows = reviewArray(items);
  if (!rows.length) return `<p>${escapeHtml(empty)}</p>`;
  return `<div class="review-list">${rows.slice(0, 12).map(item => `<div>${escapeHtml(item)}</div>`).join('')}</div>`;
}

function reviewTag(text, cls='') {
  return `<span class="review-tag ${escapeHtml(cls)}">${escapeHtml(text)}</span>`;
}

function readinessLabel(level) {
  const key = String(level || '').toLowerCase();
  if (key === 'ready') return { text: '资料充分，可进入用例评审', short: '资料充分', cls: 'ok', meter: '' };
  if (key === 'blocked') return { text: '资料不足，建议先补充', short: '资料不足', cls: 'danger', meter: 'danger' };
  return { text: '资料部分缺失，需人工确认', short: '需确认', cls: 'warn', meter: 'warn' };
}

function sourceQualityText(value) {
  const key = String(value || '').toLowerCase();
  if (key === 'sufficient') return '充分';
  if (key === 'partial') return '部分';
  return '缺失';
}

function readinessTags(items, cls='', limit=8) {
  const rows = reviewArray(items).slice(0, limit);
  return rows.length ? rows.map(item => reviewTag(item, cls)).join('') : reviewTag('暂无', 'ok');
}

function supplementIssueRows(analysis={}, review={}) {
  const reviewReadiness = review.requirement_readiness || {};
  const rows = [];
  const pushRows = (type, label, items, cls) => {
    reviewArray(items).forEach(text => {
      if (!text) return;
      rows.push({
        type,
        label,
        cls,
        text,
        suggestion: supplementSuggestion(type, text)
      });
    });
  };
  pushRows('missing', '缺失资料', analysis.missing_inputs || reviewReadiness.missing_inputs, 'warn');
  pushRows('blocker', '阻断项', analysis.blockers || reviewReadiness.blockers, 'danger');
  pushRows('question', '待确认', analysis.questions || reviewReadiness.questions, 'warn');
  return rows.slice(0, 12);
}

function supplementSuggestion(type, text) {
  const raw = String(text || '').trim();
  const lower = raw.toLowerCase();
  if (!raw) return '';
  if (/截图|ui|页面|入口|按钮|文案|弹窗|图片|上传|格式|尺寸/.test(raw)) {
    return `已补充页面/UI 信息：${raw}。以本次上传截图或 UI 稿为准；若资料仍缺失，生成时先覆盖主流程和可见断言，相关边界作为人工待确认用例。`;
  }
  if (/语音|识别|停顿|sdk|参数|阈值/.test(raw)) {
    return `已确认语音/识别规则：${raw}。请按当前线上配置生成正常、弱网/噪声、超时、误触发和边界停顿用例；无法稳定自动化的场景标记为人工待准备。`;
  }
  if (/并发|排队|限流|队列|服务|稳定/.test(raw)) {
    return `已确认稳定性策略：${raw}。自动化只覆盖可稳定复现的单用户 UI 行为；并发、限流和服务端队列压测不进入 Midscene 自动化，转人工/专项验证。`;
  }
  if (/预研|版本|隐藏|配置|开关|灰度/.test(raw)) {
    return `已确认版本/开关状态：${raw}。本轮按当前版本可见入口生成用例；未上线或灰度不可见能力标记为人工待确认，不阻塞主链路自动化。`;
  }
  if (/敏感|过滤|审核|调用|接口/.test(raw)) {
    return `已确认内容/接口规则：${raw}。自动化覆盖用户侧可见输入、失败提示和结果页断言；具体后台审核或接口策略作为人工待准备项。`;
  }
  if (type === 'missing') {
    return `暂按当前资料生成：${raw}。已知缺失不阻塞主流程，相关断言采用页面可见文案和结果状态；缺失部分进入人工待准备或后续补图重生成。`;
  }
  if (type === 'blocker') {
    return `本轮不把该阻断项作为自动化前置：${raw}。请将相关场景降级为人工/待准备，只生成不依赖该条件的稳定 UI 用例。`;
  }
  return `已确认：${raw}。请按该结论补充正常、异常和边界用例；无法稳定执行的部分转人工待准备。`;
}

function reviewSupplementIssueHtml(row, index) {
  return `
    <div class="review-confirm-card" data-supplement-card="1" data-state="pending">
      <div class="review-confirm-head">
        ${reviewTag(row.label, row.cls)}
        <span class="review-tag" data-state-label>待处理</span>
        <strong>${escapeHtml(row.text)}</strong>
      </div>
      <textarea data-supplement-answer="1">${escapeHtml(row.suggestion)}</textarea>
      <div class="review-confirm-actions">
        <button class="btn-sm success" data-action="accepted" onclick="setSupplementIssueState(this, 'accepted')">采纳建议</button>
        <button class="btn-sm" data-action="ignored" onclick="setSupplementIssueState(this, 'ignored')">不考虑</button>
        <button class="btn-sm" onclick="clearSupplementIssue(this)">清空</button>
      </div>
      <input type="hidden" data-supplement-type value="${escapeHtml(row.label)}">
      <input type="hidden" data-supplement-issue value="${escapeHtml(row.text)}">
    </div>
  `;
}

function setSupplementIssueState(button, state) {
  const card = button?.closest('[data-supplement-card]');
  if (!card) return;
  card.dataset.state = state;
  card.classList.toggle('accepted', state === 'accepted');
  card.classList.toggle('ignored', state === 'ignored');
  const label = card.querySelector('[data-state-label]');
  if (label) label.textContent = state === 'accepted' ? '' : (state === 'ignored' ? '' : '待处理');
}

function clearSupplementIssue(button) {
  const card = button?.closest('[data-supplement-card]');
  if (!card) return;
  const textarea = card.querySelector('[data-supplement-answer]');
  if (textarea) textarea.value = '';
  setSupplementIssueState(button, 'pending');
}

function reviewYamlExecutabilityHtml(data={}) {
  if (!data || !Object.keys(data).length || data.level === 'unknown') return '';
  const score = Math.max(0, Math.min(100, Number(data.score) || 0));
  const level = String(data.level || '').toLowerCase();
  const label = level === 'good'
    ? { text: '执行风险低', cls: 'ok' }
    : (level === 'risky' || level === 'blocked')
      ? { text: '执行风险高', cls: 'danger' }
      : { text: '需要复核', cls: 'warn' };
  const suggestions = Array.isArray(data.suggestions) ? data.suggestions.filter(Boolean) : [];
  const stats = data.stats || {};
  return `
    <div class="review-panel">
      <div class="section-head" style="align-items:center;">
        <div>
          <h3>YAML 可执行性建议</h3>
          <p>这里检查生成脚本是否具备稳定起点、清晰定位、可见断言和少量等待；它不影响老基线同步，只提示生成质量。</p>
        </div>
        <div class="review-mini-score">
          ${reviewTag(`${score}/100`)}
          ${reviewTag(label.text, label.cls)}
        </div>
      </div>
      <div class="review-stats compact">
        <div class="review-stat"><strong>${stats.task_count || 0}</strong><span>脚本任务</span></div>
        <div class="review-stat"><strong>${stats.tasks_without_assertion || 0}</strong><span>缺断言</span></div>
        <div class="review-stat"><strong>${stats.ambiguous_prompt_count || 0}</strong><span>定位过泛</span></div>
        <div class="review-stat"><strong>${stats.long_sleep_count || 0}</strong><span>长等待</span></div>
      </div>
      ${suggestions.length ? `<div class="review-list">${suggestions.slice(0, 6).map(item => `<div>${escapeHtml(item)}</div>`).join('')}</div>` : '<p>暂无明显可执行性风险。</p>'}
    </div>
  `;
}

function acceptAllSupplementIssues() {
  document.querySelectorAll('[data-supplement-card]').forEach(card => {
    card.dataset.state = 'accepted';
    card.classList.add('accepted');
    card.classList.remove('ignored');
  });
}

function ignoreAllSupplementIssues() {
  document.querySelectorAll('[data-supplement-card]').forEach(card => {
    card.dataset.state = 'ignored';
    card.classList.add('ignored');
    card.classList.remove('accepted');
  });
}

function collectSupplementIssueText() {
  const accepted = [];
  const ignored = [];
  document.querySelectorAll('[data-supplement-card]').forEach(card => {
    const state = card.dataset.state || 'pending';
    const type = card.querySelector('[data-supplement-type]')?.value || '待确认';
    const issue = card.querySelector('[data-supplement-issue]')?.value || '';
    const answer = card.querySelector('[data-supplement-answer]')?.value.trim() || '';
    if (state === 'accepted' && answer) {
      accepted.push(`【${type}已确认】${answer}`);
    } else if (state === 'ignored') {
      ignored.push(`【${type}本轮不考虑】${issue}。本轮生成不围绕该项扩展自动化用例；如影响主流程，则转人工/待准备。`);
    }
  });
  return { accepted, ignored };
}

function reviewReadinessHtml(analysis={}, review={}) {
  const reviewReadiness = review.requirement_readiness || {};
  const scoreRaw = analysis.readiness_score ?? reviewReadiness.score ?? 0;
  const score = Math.max(0, Math.min(100, Number(scoreRaw) || 0));
  const level = analysis.readiness_level || reviewReadiness.level || (score >= 75 ? 'ready' : (score >= 50 ? 'review' : 'blocked'));
  const label = readinessLabel(level);
  const source = analysis.source_quality || {};
  const confidence = analysis.confidence || reviewReadiness.confidence || 'medium';
  const missingCount = reviewArray(analysis.missing_inputs || reviewReadiness.missing_inputs).length;
  const blockerCount = reviewArray(analysis.blockers || reviewReadiness.blockers).length;
  const questionCount = reviewArray(analysis.questions || reviewReadiness.questions).length;
  return `
    <div class="review-readiness">
      <div class="readiness-meter ${escapeHtml(label.meter)}">
        <strong>${escapeHtml(`${score}/100`)}</strong>
        <span>资料完整度 · ${escapeHtml(label.short)} · 模型信心 ${escapeHtml(confidence)}</span>
        <div class="readiness-bar" style="--score:${score}%"><i></i></div>
        <p>这个分数表示当前需求、UI、页面知识是否足够支撑生成稳定用例；不是用例质量分。低于 50 建议先采纳确认项或补充截图后重新生成。</p>
      </div>
      <div class="readiness-notes">
        <div class="readiness-note-row"><b>当前建议</b>${reviewTag(label.text, label.cls)}${reviewTag(`缺失 ${missingCount}`)}${reviewTag(`阻断 ${blockerCount}`, blockerCount ? 'danger' : 'ok')}${reviewTag(`待确认 ${questionCount}`, questionCount ? 'warn' : 'ok')}</div>
        <div class="readiness-note-row"><b>资料质量</b>
          ${reviewTag(`需求 ${sourceQualityText(source.requirement)}`)}
          ${reviewTag(`UI ${sourceQualityText(source.ui)}`)}
          ${reviewTag(`知识库 ${sourceQualityText(source.knowledge)}`)}
        </div>
        <div class="readiness-note-row"><b>缺失资料</b>${readinessTags(analysis.missing_inputs || reviewReadiness.missing_inputs, 'warn')}</div>
        <div class="readiness-note-row"><b>阻断项</b>${readinessTags(analysis.blockers || reviewReadiness.blockers, 'danger')}</div>
        <div class="readiness-note-row"><b>待确认</b>${readinessTags(analysis.questions || reviewReadiness.questions, 'warn')}</div>
      </div>
    </div>
  `;
}

function reviewSupplementHtml(caseSetId, analysis={}, review={}) {
  const issues = supplementIssueRows(analysis, review);
  return `
    <div class="review-supplement">
      <h3>人工确认 / 补充资料后重新生成</h3>
      <p>下面已把“缺失资料、阻断项、待确认”自动整理成确认项。可以采纳建议、手动改写、标记本轮不考虑，也可以补充截图或 UI 稿后重新生成。</p>
      ${issues.length ? `
        <div class="review-confirm-actions">
          <button class="btn-sm success" onclick="acceptAllSupplementIssues()">采纳全部建议</button>
          <button class="btn-sm" onclick="ignoreAllSupplementIssues()">全部本轮不考虑</button>
        </div>
        <div class="review-confirm-list">
          ${issues.map(reviewSupplementIssueHtml).join('')}
        </div>
      ` : '<p>当前没有明显缺失资料或待确认项。仍可在下方手动补充业务上下文或上传截图。</p>'}
      <textarea id="review-supplement-text" placeholder="例如：语音识别 1.5 秒停顿判定参数为 xxx；AI 推荐打印设置在当前版本不隐藏；模型匹配优先级为 xxx；新增截图说明入口和结果页文案。"></textarea>
      <div class="review-supplement-actions">
        <input type="file" id="review-supplement-files" accept=".txt,.md,.json,.pdf,.doc,.docx,.mm,.png,.jpg,.jpeg" multiple style="display:none" onchange="renderReviewSupplementFiles()">
        <button class="btn-sm" onclick="document.getElementById('review-supplement-files').click()">补充截图/文件</button>
        <span class="review-supplement-file" id="review-supplement-file-info">未选择文件</span>
        <button class="btn-sm primary" onclick="regenerateGenerationCasesWithSupplement(${jsArg(caseSetId)})">补充并重新生成用例</button>
      </div>
    </div>
  `;
}

function renderReviewSupplementFiles() {
  const input = document.getElementById('review-supplement-files');
  const info = document.getElementById('review-supplement-file-info');
  const files = Array.from(input?.files || []);
  if (info) {
    info.textContent = files.length
      ? `${files.length} 个文件：${files.slice(0, 3).map(file => file.name).join('、')}${files.length > 3 ? '...' : ''}`
      : '未选择文件';
  }
}

function reviewMatrixHtml(matrix=[]) {
  const rows = Array.isArray(matrix) ? matrix.filter(item => item && typeof item === 'object') : [];
  if (!rows.length) return '<p>暂无覆盖矩阵。建议在需求资料中补充功能点、异常流程和边界条件。</p>';
  return `
    <div class="review-matrix">
      <table>
        <thead><tr><th>功能</th><th>需求点</th><th>自动化用例</th><th>人工/待准备</th><th>未覆盖原因</th></tr></thead>
        <tbody>
          ${rows.slice(0, 20).map(row => `
            <tr>
              <td>${escapeHtml(row.feature || '-')}</td>
              <td>${escapeHtml(row.requirement_point || row.requirementPoint || '-')}</td>
              <td>${escapeHtml(reviewArray(row.auto_cases || row.autoCases).join('、') || '-')}</td>
              <td>${escapeHtml(reviewArray(row.manual_cases || row.manualCases).join('、') || '-')}</td>
              <td>${escapeHtml(row.uncovered_reason || row.uncoveredReason || '-')}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function reviewCaseCard(caseItem={}, type='auto') {
  const title = caseItem.title || caseItem.name || '未命名用例';
  const priority = caseItem.priority || 'P2';
  const smoke = caseItem.smoke ? '冒烟' : '';
  const reason = caseItem.automation_reason || caseItem.automationReason || caseItem.reason || '';
  const coverage = caseItem.coverage || caseItem.coverage_point || caseItem.test_point || '';
  const risk = caseItem.risk || caseItem.risks || '';
  const data = caseItem.data_requirements || caseItem.dataRequirements || caseItem.test_data || '';
  const tags = reviewArray(caseItem.tags).slice(0, 5);
  return `
    <div class="review-case-card">
      <div class="review-case-title">
        <strong>${escapeHtml(title)}</strong>
        <span>${reviewTag(type === 'manual' ? '人工' : '可自动化', type === 'manual' ? 'warn' : 'ok')}</span>
      </div>
      <div class="review-tags">
        ${reviewTag(caseItem.case_id || caseItem.caseId || '-')}
        ${reviewTag(priority)}
        ${smoke ? reviewTag(smoke, 'ok') : ''}
        ${tags.map(tag => reviewTag(tag)).join('')}
      </div>
      ${coverage ? `<p><strong>覆盖：</strong>${escapeHtml(coverage)}</p>` : ''}
      ${reason ? `<p><strong>建议：</strong>${escapeHtml(reason)}</p>` : ''}
      ${risk ? `<p><strong>风险：</strong>${escapeHtml(risk)}</p>` : ''}
      ${data ? `<p><strong>数据：</strong>${escapeHtml(data)}</p>` : ''}
      ${type === 'manual' ? `<div class="review-design-actions"><button class="btn-sm" onclick="appendManualCaseToSupplement(${jsArg(title)}, ${jsArg(reason || risk || data || coverage || '需要人工确认')})">加入补充说明</button><button class="btn-sm" onclick="copyText(${jsArg(`${title}\\n${reason || risk || data || coverage || ''}`)})">复制检查点</button></div>` : ''}
    </div>
  `;
}

function appendManualCaseToSupplement(title, reason) {
  const textarea = document.getElementById('review-supplement-text');
  if (!textarea) {
    showToast('请先回到生成结果评审页再补充人工项', 'error');
    return;
  }
  const line = `人工项确认：${title}。处理方式：${reason}。请据此补充稳定前置或保持为人工待准备，不要强行转自动化。`;
  textarea.value = [textarea.value.trim(), line].filter(Boolean).join('\n');
  textarea.focus();
  textarea.scrollIntoView({ behavior: 'smooth', block: 'center' });
  showToast('已加入补充说明，可补充截图后重新生成用例', 'success');
}

async function copyText(text) {
  const value = String(text || '');
  if (!value.trim()) {
    showToast('没有可复制的内容', 'error');
    return false;
  }
  try {
    if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error('clipboard api unavailable');
    await navigator.clipboard.writeText(value);
    showToast('✓ 已复制', 'success');
    return true;
  } catch(e) {
    try {
      const textarea = document.createElement('textarea');
      textarea.value = value;
      textarea.setAttribute('readonly', '');
      textarea.style.position = 'fixed';
      textarea.style.left = '-9999px';
      textarea.style.top = '0';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      textarea.setSelectionRange(0, textarea.value.length);
      const copied = document.execCommand && document.execCommand('copy');
      document.body.removeChild(textarea);
      if (!copied) throw new Error('execCommand copy failed');
      showToast('✓ 已复制', 'success');
      return true;
    } catch(fallbackError) {
      showToast('复制失败，请长按或手动选择检查点文本', 'error');
      return false;
    }
  }
}

function uiDesignImageUrl(caseSetId, item={}) {
  const assetId = item.asset_id || item.assetId || '';
  const filename = item.filename || '';
  return `${API_BASE}/cases/ui-design-image?case_set_id=${encodeURIComponent(caseSetId || '')}&asset_id=${encodeURIComponent(assetId)}&filename=${encodeURIComponent(filename)}`;
}

function reviewUiDesignCard(caseSetId, item={}) {
  const figma = item.figma || {};
  const title = item.page_name || item.name || item.filename || 'UI 设计稿';
  const source = item.source === 'figma' ? 'Figma 自动保存' : '人工补充';
  const reason = figma.relevance_reason || item.description || item.route || '';
  const relevance = figmaRelevanceLabel(figma.relevance_score, figma.pinned);
  return `
    <div class="review-design-card">
      <a class="review-design-thumb" href="${uiDesignImageUrl(caseSetId, item)}" target="_blank" title="打开设计稿">
        <img src="${uiDesignImageUrl(caseSetId, item)}" alt="${escapeHtml(title)}" loading="lazy">
      </a>
      <div class="review-design-body">
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(source)} · ${escapeHtml(relevance.text)} · ${escapeHtml(formatBytes(item.size || 0))}</span>
        ${reason ? `<p>${escapeHtml(reason)}</p>` : ''}
        <div class="review-design-actions">
          <a class="btn-sm" href="${uiDesignImageUrl(caseSetId, item)}" target="_blank">查看</a>
          <button class="btn-sm danger" onclick="deleteCaseUiDesign(${jsArg(caseSetId)}, ${jsArg(item.asset_id || '')})">删除</button>
        </div>
      </div>
    </div>
  `;
}

function figmaRelevanceLabel(scoreValue, pinned=false) {
  if (pinned) return { text: '手动指定页面', cls: 'ok' };
  if (scoreValue === undefined || scoreValue === null || scoreValue === '') return { text: '匹配度未记录', cls: '' };
  const score = Number(scoreValue) || 0;
  if (score >= 8) return { text: '匹配度高', cls: 'ok' };
  if (score >= 5) return { text: '匹配度中', cls: 'warn' };
  return { text: '匹配度低，建议删除', cls: 'danger' };
}

function reviewUiDesignsHtml(caseSetId, summary={}) {
  const designs = summary.ui_design_assets || [];
  const hiddenDesigns = summary.hidden_ui_design_assets || summary.hiddenUiDesignAssets || [];
  const excludedNodes = summary.excluded_figma_nodes || summary.excludedFigmaNodes || [];
  const lowCount = designs.filter(item => item?.source === 'figma' && Number((item.figma || {}).relevance_score || 0) < 5 && !(item.figma || {}).pinned).length;
  return `
    <div class="review-panel">
      <div class="section-head" style="align-items:flex-start;">
        <div>
          <h3>当前批次 UI 设计稿</h3>
          <p>这里保存本次生成实际参考的 Figma/UI 截图。系统默认只自动保存匹配度达标的 Figma 页面；低匹配页面会作为候选忽略，不进入生成参考。可以继续补充关键页面，也可以删除误选或无关的 UI 稿，再重新生成用例。</p>
        </div>
        <div class="review-actions">
          <input type="file" id="case-ui-design-files" accept=".png,.jpg,.jpeg" multiple style="display:none" onchange="renderCaseUiDesignFileInfo()">
          <button class="btn-sm" onclick="document.getElementById('case-ui-design-files').click()">新增UI稿</button>
          <button class="btn-sm primary" onclick="uploadCaseUiDesigns(${jsArg(caseSetId)})">保存新增</button>
        </div>
      </div>
      ${hiddenDesigns.length ? `<div class="generate-hint">已按当前需求自动隐藏 ${hiddenDesigns.length} 份低匹配 Figma UI 稿，避免误参考无关页面。</div>` : ''}
      ${excludedNodes.length ? excludedFigmaNodesHtml(caseSetId, excludedNodes) : ''}
      ${lowCount ? `<div class="generate-hint">发现 ${lowCount} 份历史低匹配 UI 稿，建议删除后重新生成；新版会在重新生成前自动清理旧的 Figma 自动保存结果。</div>` : ''}
      <span class="review-supplement-file" id="case-ui-design-file-info">未选择文件</span>
      ${designs.length
        ? `<div class="review-design-grid">${designs.map(item => reviewUiDesignCard(caseSetId, item)).join('')}</div>`
        : '<p>当前批次还没有保存 UI 设计稿。粘贴 Figma 链接生成时会自动保存命中的页面；也可以手动上传关键截图。</p>'}
    </div>
  `;
}

function excludedFigmaNodesHtml(caseSetId, nodes=[]) {
  const rows = (Array.isArray(nodes) ? nodes : []).filter(item => item && typeof item === 'object');
  if (!rows.length) return '';
  return `
    <div class="generate-hint">
      <strong>已排除 Figma 页面 ${rows.length} 个</strong>
      <div class="review-design-actions" style="margin-top:8px;">
        ${rows.slice(0, 8).map(item => `
          <span class="review-tag danger">${escapeHtml(item.page_name || item.node_id || '已排除页面')}</span>
          <button class="btn-sm" onclick="restoreExcludedFigmaNode(${jsArg(caseSetId)}, ${jsArg(item.node_id || item.nodeId || '')})">允许重新参考</button>
        `).join('')}
      </div>
    </div>
  `;
}

function renderCaseUiDesignFileInfo() {
  const input = document.getElementById('case-ui-design-files');
  const info = document.getElementById('case-ui-design-file-info');
  const files = Array.from(input?.files || []);
  if (info) {
    info.textContent = files.length
      ? `${files.length} 个 UI 稿：${files.slice(0, 3).map(file => file.name).join('、')}${files.length > 3 ? '...' : ''}`
      : '未选择文件';
  }
}

async function uploadCaseUiDesigns(caseSetId) {
  const input = document.getElementById('case-ui-design-files');
  const files = Array.from(input?.files || []);
  if (!caseSetId) return showToast('缺少生成批次 ID', 'error');
  if (!files.length) return showToast('请先选择 PNG/JPG UI 设计稿', 'error');
  try {
    const assets = [];
    for (const file of files) {
      if (!/\.(png|jpe?g)$/i.test(file.name)) throw new Error(`只支持图片 UI 稿：${file.name}`);
      assets.push(await fileToAsset(file));
    }
    const data = await apiRequest('/cases/ui-designs', {
      method: 'POST',
      body: JSON.stringify({ case_set_id: caseSetId, files: assets, source: 'manual' })
    });
    showToast(`✓ 已保存 ${data.saved?.length || files.length} 份 UI 稿`, 'success');
    await showGenerationReviewByCaseSet(caseSetId);
  } catch (e) {
    showToast(e.message || '保存 UI 设计稿失败', 'error');
  }
}

async function deleteCaseUiDesign(caseSetId, assetId) {
  if (!caseSetId || !assetId) return showToast('缺少 UI 稿标识', 'error');
  if (!confirm('确认删除这份 UI 设计稿？如果它来自 Figma 自动保存，会加入当前批次排除列表，重新生成时不再作为参考。不会删除 YAML 和已生成用例。')) return;
  try {
    const data = await apiRequest(`/cases/ui-design?case_set_id=${encodeURIComponent(caseSetId)}&asset_id=${encodeURIComponent(assetId)}`, { method: 'DELETE' });
    showToast(data.deleted ? '✓ 已删除 UI 设计稿；Figma 自动图会在重新生成时继续排除' : 'UI 设计稿不存在或已删除', data.deleted ? 'success' : 'error');
    await showGenerationReviewByCaseSet(caseSetId);
  } catch (e) {
    showToast(e.message || '删除 UI 设计稿失败', 'error');
  }
}

async function restoreExcludedFigmaNode(caseSetId, nodeId) {
  if (!caseSetId || !nodeId) return showToast('缺少 Figma 节点标识', 'error');
  try {
    const data = await apiRequest('/cases/ui-design-exclusion', {
      method: 'POST',
      body: JSON.stringify({ case_set_id: caseSetId, node_id: nodeId })
    });
    showToast(data.restored ? '✓ 已允许该 Figma 页面重新参与后续生成' : '该 Figma 页面未在排除列表中', data.restored ? 'success' : 'error');
    await showGenerationReviewByCaseSet(caseSetId);
  } catch (e) {
    showToast(e.message || '恢复 Figma 页面失败', 'error');
  }
}

function generationReviewHtml(data={}) {
  const summary = data.summary || data;
  const counts = summary.counts || {};
  const analysis = summary.requirement_analysis || {};
  const cases = summary.cases || data.cases?.cases || data.cases || [];
  const manualCases = summary.manual_cases || data.manual_cases || [];
  const review = summary.review || data.review || {};
  const yamlCheck = summary.yaml_check || data.yamlCheck || {};
  const yamlExecutability = summary.yaml_executability || data.yamlExecutability || {};
  const mod = summary.module || data.module || currentModule || '';
  const file = summary.yaml_file || data.file || currentFile || '';
  const caseSetId = summary.case_set_id || data.case_set_id || '';
  const reviewText = review.coverage_check || review.automation_check || review.assertion_check || '';
  const reportCheckpoints = Array.isArray(summary.report_checkpoints) ? summary.report_checkpoints : [];
  const reportCheckpointText = reportCheckpoints.slice(0, 5).map((item, index) => `${index + 1}. ${item}`).join('\n');
  const generationTargets = review.generation_targets || review.coverage_audit?.generation_targets || {};
  const targetText = generationTargets.target_automation_cases
    ? `${generationTargets.min_automation_cases || '-'}-${generationTargets.max_automation_cases || generationTargets.target_automation_cases}`
    : '-';
  return `
    <div class="review-page">
      <div class="review-head">
        <div>
          <div class="workflow-kicker">生成批次 ${escapeHtml(caseSetId || '-')} · Task 评审页</div>
          <h2>${escapeHtml(summary.title || '自动化测试生成结果')}</h2>
          <p>${escapeHtml(mod)} / ${escapeHtml(file)}。先看需求分析、缺口和用例分类；确认没问题后再查看自动化脚本或单条调试。</p>
        </div>
        <div class="review-actions">
          <button class="btn-sm" onclick="activateWorkflow('dashboard')">回工作台</button>
          ${caseSetId ? `<button class="btn-sm primary" onclick="regenerateGenerationCases(${jsArg(caseSetId)})">重新生成用例</button>` : ''}
          ${caseSetId ? `<a class="btn-sm" href="${mindmapDownloadUrl(caseSetId)}" target="_blank">下载脑图</a>` : ''}
          ${caseSetId ? `<button class="btn-sm" onclick="regenerateGenerationMindmap(${jsArg(caseSetId)})" title="只按现有生成分析刷新脑图文件（FreeMind .mm）；不调用千问，不改用例，不覆盖 YAML">刷新脑图文件</button>` : ''}
          ${caseSetId ? `<button class="btn-sm danger" onclick="deleteGenerationMindmap(${jsArg(caseSetId)})">删除脑图</button>` : ''}
          ${mod && file ? `<button class="btn-sm" onclick="openFile(${jsArg(mod)}, ${jsArg(file)})">查看自动化脚本</button>` : ''}
          ${mod && file ? `<button class="btn-sm success" onclick="openFile(${jsArg(mod)}, ${jsArg(file)}).then(() => showRunSelectedTask())">单条调试</button>` : ''}
          <button class="btn-sm primary" onclick="showGenerateYaml()">继续新建</button>
        </div>
      </div>
      <div class="review-stats">
        <div class="review-stat"><strong>${counts.scenario_count || (summary.scenarios || []).length || 0}</strong><span>测试场景</span></div>
        <div class="review-stat"><strong>${counts.automation_case_count || cases.length || 0}</strong><span>自动化用例</span></div>
        <div class="review-stat"><strong>${escapeHtml(targetText)}</strong><span>目标用例数</span></div>
        <div class="review-stat"><strong>${counts.manual_case_count || manualCases.length || 0}</strong><span>人工/待准备</span></div>
        <div class="review-stat"><strong>${counts.smoke_count || 0}</strong><span>冒烟用例</span></div>
      </div>
      <div class="review-panel">
        <div class="section-head" style="align-items:center;">
          <div>
            <h3>当前需求测试报告检查点</h3>
            <p>用于直接写入测试报告，聚焦本次需求的业务验收、交互、异常边界和人工确认项。</p>
          </div>
          ${reportCheckpoints.length ? `<button class="btn-sm" onclick="copyText(${jsArg(reportCheckpointText)})">复制检查点</button>` : ''}
        </div>
        ${reportCheckpoints.length ? `<ol>${reportCheckpoints.slice(0, 5).map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ol>` : '<p>暂无报告检查点。重新生成后会自动输出 5 条可写入测试报告的检查点。</p>'}
      </div>
      ${reviewYamlExecutabilityHtml(yamlExecutability)}
      ${reviewReadinessHtml(analysis, review)}
      ${caseSetId ? reviewSupplementHtml(caseSetId, analysis, review) : ''}
      ${caseSetId ? reviewUiDesignsHtml(caseSetId, summary) : ''}
      <div class="review-grid">
        <div class="review-panel">
          <h3>需求分析</h3>
          <p>业务目标</p>
          ${reviewListHtml(analysis.business_goals, '暂无业务目标，建议补充需求背景。')}
          <p>需求点</p>
          ${reviewListHtml(analysis.requirement_points, '暂无需求点，建议补充明确验收点。')}
          <p>可见结果</p>
          ${reviewListHtml(analysis.visible_outcomes, '暂无 UI 可见结果，建议补充页面标题、按钮、空态或结果页文案。')}
        </div>
        <div class="review-panel">
          <h3>风险和准入提示</h3>
          ${reviewListHtml(analysis.risks, '暂无显式风险。')}
          <p>当前假设</p>
          ${reviewListHtml(analysis.assumptions, '暂无额外假设。')}
          ${reviewText ? `<p><strong>模型自评：</strong>${escapeHtml(reviewText)}</p>` : ''}
          ${review.skill_pipeline_error ? `<p><strong>Skills 兜底：</strong>${escapeHtml(review.skill_pipeline_error)}</p>` : ''}
          ${yamlCheck.warnings?.length ? `<p><strong>脚本检查：</strong>${escapeHtml(yamlCheck.warnings.slice(0, 4).join('；'))}</p>` : '<p>脚本基础检查暂无阻断提示。</p>'}
        </div>
      </div>
      <div class="review-panel">
        <h3>覆盖矩阵</h3>
        ${reviewMatrixHtml(analysis.coverage_matrix)}
      </div>
      <div class="review-panel">
        <h3>可自动化用例</h3>
        ${cases.length ? `<div class="review-case-grid">${cases.slice(0, 24).map(item => reviewCaseCard(item, 'auto')).join('')}</div>` : '<p>暂无可自动化用例。</p>'}
      </div>
      <div class="review-panel">
        <h3>人工用例 / 待准备</h3>
        ${manualCases.length ? `<div class="review-case-grid">${manualCases.slice(0, 24).map(item => reviewCaseCard(item, 'manual')).join('')}</div>` : '<p>暂无人工用例。后续如遇到支付、删除、切账号、后台造数等场景，应放入这里。</p>'}
      </div>
    </div>
  `;
}

function showGenerationReview(data={}) {
  const area = document.getElementById('editor-area');
  if (!area) return;
  const summary = data.summary || data;
  currentModule = summary.module || data.module || currentModule;
  currentFile = summary.yaml_file || data.file || currentFile;
  setActiveWorkflow('generate');
  area.className = 'editor-area';
  area.innerHTML = generationReviewHtml(data);
  document.getElementById('toolbar-path').innerHTML = '<span>📁</span> 生成结果评审';
  document.getElementById('toolbar-help').textContent = '先确认需求分析、风险和用例分类，再进入 Midscene 单条调试；跑通后再入 Task 基线和同步 Sonic。';
  document.getElementById('file-info').textContent = '生成结果评审';
  updateToolbarState('生成结果评审');
}

async function showGenerationReviewByCaseSet(caseSetId) {
  if (!caseSetId) {
    showToast('这个生成任务没有关联批次 ID', 'error');
    return;
  }
  try {
    const data = await apiRequest(`/cases/summary?case_set_id=${encodeURIComponent(caseSetId)}`);
    showGenerationReview(data.summary || data || {});
  } catch(e) {
    showToast(e.message || '读取生成分析失败', 'error');
  }
}

async function showCurrentGenerationReview() {
  const meta = fileMeta(currentModule, currentFile);
  const caseSetId = meta.last_case_set_id;
  if (!caseSetId) {
    showToast('当前文件没有关联生成批次', 'error');
    return;
  }
  try {
    const data = await apiRequest(`/cases/summary?case_set_id=${encodeURIComponent(caseSetId)}`);
    showGenerationReview(data.summary || {});
  } catch(e) {
    showToast(e.message || '读取生成分析失败', 'error');
  }
}

function generationJobTitle(job) {
  const result = job.result || {};
  const request = job.request_summary || {};
  return result.file || job.file || request.title || job.title || result.summary?.title || result.module || job.module || job.step || '生成任务';
}

function generationJobModule(job) {
  const result = job.result || {};
  return result.module || job.module || '';
}

function generationJobCaseSetId(job) {
  const result = job.result || {};
  return result.case_set_id || result.summary?.case_set_id || job.case_set_id || '';
}

function generationJobCounts(job) {
  const result = job.result || {};
  const parts = [];
  if (typeof result.caseCount !== 'undefined') parts.push(`${result.caseCount} 条自动化`);
  if (typeof result.manualCaseCount !== 'undefined' && result.manualCaseCount) parts.push(`${result.manualCaseCount} 条人工`);
  if (typeof result.scenarioCount !== 'undefined' && result.scenarioCount) parts.push(`${result.scenarioCount} 个场景`);
  return parts.join(' · ');
}

function generationFailureHtml(job={}) {
  if (job.status !== 'failed') return '';
  const detail = job.error_detail || job.errorDetail || {};
  const raw = detail.error || job.error || '';
  const suggestion = detail.suggestion || '可以先查看失败阶段，减少上传资料或补充关键确认项后重新生成。';
  return `
    <div class="job-detail" style="margin-top:10px;">
      <div><strong>失败阶段：</strong>${escapeHtml(detail.stage || job.step || 'AI生成')} · ${escapeHtml(detail.type || 'generation_error')} · ${escapeHtml(String(detail.progress || job.progress || 0))}%</div>
      <div><strong>失败原因：</strong>${escapeHtml(explainCallbackHttp000(detail.message || job.message || raw || '生成失败'))}</div>
      <div><strong>处理建议：</strong>${escapeHtml(suggestion)}</div>
      ${raw ? `<pre>原始错误:\n${escapeHtml(raw)}</pre>` : ''}
    </div>
  `;
}

function generationJobActions(job) {
  const mod = generationJobModule(job);
  const file = generationJobTitle(job);
  const caseSetId = generationJobCaseSetId(job);
  const id = job.job_id || '';
  const parts = [];
  if (job.status === 'failed' && id && (job.can_retry !== false)) {
    parts.push(`<button class="btn-sm primary" onclick="retryGenerationJob(${jsArg(id)})">重试生成</button>`);
  }
  if (caseSetId) parts.push(`<button class="btn-sm" onclick="showGenerationReviewByCaseSet(${jsArg(caseSetId)})">生成分析</button>`);
  if (caseSetId && job.type !== 'mindmap_only') parts.push(`<button class="btn-sm primary" onclick="regenerateGenerationCases(${jsArg(caseSetId)})">重新生成用例</button>`);
  if (caseSetId) parts.push(`<a class="btn-sm" href="${mindmapDownloadUrl(caseSetId)}" target="_blank">下载脑图</a>`);
  if (caseSetId) parts.push(`<button class="btn-sm" onclick="regenerateGenerationMindmap(${jsArg(caseSetId)})" title="只按现有生成分析刷新脑图文件（FreeMind .mm）；不调用千问，不改用例，不覆盖 YAML">刷新脑图文件</button>`);
  if (caseSetId) parts.push(`<button class="btn-sm danger" onclick="deleteGenerationMindmap(${jsArg(caseSetId)})">删除脑图</button>`);
  if (mod && file && /\.ya?ml$/i.test(file)) parts.push(`<button class="btn-sm primary" onclick="openFile(${jsArg(mod)}, ${jsArg(file)})">打开 YAML</button>`);
  if (id) parts.push(`<button class="btn-sm" onclick="focusJob(${jsArg(id)})">定位执行中心</button>`);
  return parts.join('');
}

async function retryGenerationJob(jobId) {
  if (!jobId) return;
  if (!confirm(`确认重试生成任务 ${jobId}？会用原请求重新创建一个后台生成任务，旧失败记录会保留。`)) return;
  try {
    const data = await apiRequest(`/ui/generate-jobs/${encodeURIComponent(jobId)}/retry`, {
      method: 'POST',
      body: JSON.stringify({})
    });
    showToast(`✓ 已创建重试任务：${data.job_id || ''}`, 'success');
    await loadJobs(true);
    renderGenerateJobsCenter();
  } catch(e) {
    showToast(e.message || '重试生成失败', 'error');
  }
}

function mindmapDownloadUrl(caseSetId) {
  return `${API_BASE}/cases/mindmap?case_set_id=${encodeURIComponent(caseSetId || '')}`;
}

function mindmapApiPath(caseSetId) {
  return `/cases/mindmap?case_set_id=${encodeURIComponent(caseSetId || '')}`;
}

async function regenerateGenerationCases(caseSetId) {
  if (!caseSetId) {
    showToast('这个生成任务没有关联批次 ID', 'error');
    return;
  }
  if (!confirm(`确认按最新策略重新生成批次 ${caseSetId} 的用例？这会覆盖对应 YAML、生成分析和脑图文件。`)) return;
  await submitRegenerateGenerationCases(caseSetId, '', []);
}

async function regenerateGenerationCasesWithSupplement(caseSetId) {
  const manualText = document.getElementById('review-supplement-text')?.value.trim() || '';
  const issueText = collectSupplementIssueText();
  const text = [
    issueText.accepted.length ? `# 已采纳确认项\n${issueText.accepted.join('\n')}` : '',
    issueText.ignored.length ? `# 本轮不考虑项\n${issueText.ignored.join('\n')}` : '',
    manualText ? `# 手动补充说明\n${manualText}` : ''
  ].filter(Boolean).join('\n\n');
  const files = Array.from(document.getElementById('review-supplement-files')?.files || []);
  if (!text && !files.length) {
    showToast('请先采纳建议、标记不考虑、填写确认说明，或选择要补充的截图/文件', 'error');
    return;
  }
  if (!confirm(`确认把补充资料保存到批次 ${caseSetId}，并按最新策略重新生成用例？`)) return;
  const assets = [];
  for (const file of files) {
    assets.push(await fileToAsset(file));
  }
  await submitRegenerateGenerationCases(caseSetId, text, assets);
}

async function submitRegenerateGenerationCases(caseSetId, supplement='', files=[]) {
  try {
    const data = await apiRequest('/ui/regenerate-yaml-async', {
      method: 'POST',
      body: JSON.stringify({ case_set_id: caseSetId, supplement, files })
    });
    showToast(`✓ 已创建重新生成任务：${data.job_id || ''}`, 'success');
    await loadJobs(true);
    if (activeWorkflow === 'generate' && document.getElementById('editor-area')?.textContent.includes('生成任务与生成记录')) {
      renderGenerateJobsCenter();
    }
  } catch(e) {
    showToast(e.message || '重新生成用例失败', 'error');
  }
}

async function regenerateGenerationMindmap(caseSetId) {
  if (!caseSetId) {
    showToast('这个生成任务没有关联批次 ID', 'error');
    return;
  }
  try {
    const data = await apiRequest(mindmapApiPath(caseSetId), { method: 'POST' });
    const sizeText = data.mindmap_size ? `，${formatBytes(data.mindmap_size)}` : '';
    const timeText = data.mindmap_updated_at ? `，更新时间 ${data.mindmap_updated_at}` : '';
    showToast(data.ok ? `✓ 已按现有生成分析刷新脑图文件${sizeText}${timeText}` : '刷新脑图完成', 'success');
    if (activeWorkflow === 'generate' && document.getElementById('editor-area')?.textContent.includes('生成任务与生成记录')) {
      await loadJobs(true);
      renderGenerateJobsCenter();
    }
  } catch(e) {
    showToast(e.message || '刷新脑图失败', 'error');
  }
}

async function deleteGenerationMindmap(caseSetId) {
  if (!caseSetId) {
    showToast('这个生成任务没有关联批次 ID', 'error');
    return;
  }
  if (!confirm(`确认删除生成批次 ${caseSetId} 的脑图文件（FreeMind .mm）？不会删除 YAML 和生成分析。`)) return;
  try {
    const data = await apiRequest(mindmapApiPath(caseSetId), { method: 'DELETE' });
    showToast(data.deleted ? '✓ 已删除脑图文件，需要时可点“刷新脑图文件”恢复' : '脑图文件原本不存在，已记录删除状态', data.deleted ? 'success' : 'error');
    if (activeWorkflow === 'generate' && document.getElementById('editor-area')?.textContent.includes('生成任务与生成记录')) {
      renderGenerateJobsCenter();
    }
  } catch(e) {
    showToast(e.message || '删除脑图失败', 'error');
  }
}

async function deleteGenerationMindmapRecord(caseSetId) {
  if (!caseSetId) {
    showToast('这个脑图记录没有关联批次 ID', 'error');
    return;
  }
  if (!confirm(`确认从脑图中心删除这条记录？不会删除 YAML、生成分析和自动化用例。`)) return;
  try {
    const data = await apiRequest(`/cases/mindmap-record?case_set_id=${encodeURIComponent(caseSetId)}`, { method: 'DELETE' });
    showToast(data.ok ? '✓ 已从脑图中心删除记录' : '删除记录完成', 'success');
    await showMindmapCenter();
  } catch(e) {
    showToast(e.message || '删除脑图记录失败', 'error');
  }
}

function generationRecordCard(job) {
  const status = job.status || 'unknown';
  const title = generationJobTitle(job);
  const mod = generationJobModule(job);
  const caseSetId = generationJobCaseSetId(job);
  const counts = generationJobCounts(job);
  const error = jobErrorText(job);
  const timingText = jobTimingText(job);
  const cancelAction = ['pending', 'running'].includes(status) && job.job_id
    ? `<button class="btn-sm danger" onclick="cancelGenerateJob(${jsArg(job.job_id)})">取消</button>`
    : '';
  return `
    <div class="generation-record-card ${escapeHtml(status)}">
      <div class="generation-record-top">
        <span class="job-badge ${escapeHtml(status)}">${jobStatusText(status)}</span>
        <div>
          <div class="generation-record-title">${escapeHtml(title)}</div>
          <div class="generation-record-meta">
            ${escapeHtml([mod, job.step, timingText || jobTimeText(job)].filter(Boolean).join(' · '))}
            ${caseSetId ? `<br>批次：${escapeHtml(caseSetId)}` : ''}
          </div>
        </div>
        <div class="generation-record-meta">${escapeHtml(String(Math.max(0, Math.min(100, Number(job.progress || 0)))))}%</div>
      </div>
      <div class="job-progress">
        <div class="job-progress-text">
          <span>${escapeHtml(explainCallbackHttp000(job.message || job.step || jobKindText(job)))}</span>
          <span>${escapeHtml(counts || job.job_id || '')}</span>
        </div>
        <div class="job-progress-track"><div class="job-progress-bar" style="width:${Math.max(0, Math.min(100, Number(job.progress || (status === 'success' ? 100 : 0))))}%"></div></div>
      </div>
      ${error ? `<p>${escapeHtml(error)}</p>` : ''}
      ${generationFailureHtml(job)}
      <div class="generation-record-card-actions">${cancelAction}${generationJobActions(job) || '<span class="job-link muted">暂无可操作结果</span>'}</div>
    </div>
  `;
}

function generationRecordsHtml(jobs) {
  const active = jobs.filter(job => ['pending', 'running'].includes(job.status || '')).length;
  const success = jobs.filter(job => ['success', 'passed'].includes(job.status || '')).length;
  const failed = jobs.filter(job => job.status === 'failed').length;
  return `
    <div class="generation-records">
      <div class="generation-record-head">
        <div class="workflow-kicker">AI GENERATION JOBS · 需求解析 / 用例生成 / YAML</div>
        <h2>生成记录</h2>
        <p>这里看 AI 生成进度、耗时、失败原因和生成结果。任务提交后不用守着弹窗，回到这里看状态即可。</p>
        <div class="generation-flow">
          <div class="generation-flow-step active"><strong>1. 读资料</strong><span>需求、截图、Figma、页面知识</span></div>
          <div class="generation-flow-step"><strong>2. 拆场景</strong><span>需求点、风险、边界、人工项</span></div>
          <div class="generation-flow-step"><strong>3. 生成 YAML</strong><span>自动化用例、P级、冒烟标记</span></div>
          <div class="generation-flow-step"><strong>4. 调试入库</strong><span>单条验证、报告、基线同步</span></div>
        </div>
        <div class="dashboard-metrics">
          <div class="dashboard-metric"><span class="dashboard-metric-icon">Σ</span><strong>${jobs.length}</strong><span>全部记录</span></div>
          <div class="dashboard-metric"><span class="dashboard-metric-icon">▶</span><strong>${active}</strong><span>生成中</span></div>
          <div class="dashboard-metric"><span class="dashboard-metric-icon">✓</span><strong>${success}</strong><span>已完成</span></div>
          <div class="dashboard-metric"><span class="dashboard-metric-icon">!</span><strong>${failed}</strong><span>失败</span></div>
        </div>
        <div class="generation-record-actions">
          <button class="btn-sm primary" onclick="showGenerateYaml()">新建自动化测试</button>
          <button class="btn-sm" onclick="refreshGenerateJobsCenter()">刷新生成记录</button>
          <button class="btn-sm" onclick="showMindmapCenter()">脑图中心</button>
          <button class="btn-sm" onclick="activateWorkflow('dashboard')">回工作台</button>
        </div>
      </div>
      ${jobs.length
        ? `<div class="generation-record-list">${jobs.map(generationRecordCard).join('')}</div>`
        : `<div class="generation-record-empty">暂无生成记录。点击“新建自动化测试”，上传需求文档、UI 稿或截图后，这里会展示生成进度和结果入口。</div>`}
    </div>
  `;
}

function renderGenerateJobsCenter() {
  const area = document.getElementById('editor-area');
  if (!area) return;
  activeWorkspaceMode = '';
  const jobs = generationJobs()
    .sort((a, b) => Date.parse((b.updated_at || b.created_at || '').replace(' ', 'T')) - Date.parse((a.updated_at || a.created_at || '').replace(' ', 'T')))
    .slice(0, 80);
  setActiveWorkflow('generate');
  resetYamlToolbarForManager();
  area.className = 'editor-area';
  area.innerHTML = generationRecordsHtml(jobs);
  document.getElementById('toolbar-path').innerHTML = '<span>📁</span> 生成任务 / 生成记录';
  document.getElementById('toolbar-help').textContent = '查看 AI 需求解析和 YAML 生成任务；生成完成后可直接进入生成分析或打开 YAML。';
  document.getElementById('file-info').textContent = '生成记录';
  updateToolbarState('生成记录');
}

async function showGenerateJobsCenter() {
  await loadJobs(false, true);
  renderGenerateJobsCenter();
}

async function refreshGenerateJobsCenter() {
  await loadJobs(true, true);
  renderGenerateJobsCenter();
}

function mindmapStatusText(item={}) {
  if (item.mindmap_deleted) return '已删除';
  if (item.mindmap_exists) return '可下载';
  return '待生成';
}

function mindmapStatusClass(item={}) {
  if (item.mindmap_deleted) return 'failed';
  if (item.mindmap_exists) return 'success';
  return 'pending';
}

function mindmapRecordCard(item={}) {
  const caseSetId = item.case_set_id || '';
  const generatedAt = item.generated_at || '';
  const updatedAt = item.mindmap_updated_at || '';
  const priorityText = Object.entries(item.priority_counts || {})
    .map(([key, value]) => `${key} ${value}`)
    .join(' · ');
  return `
    <div class="generation-record-card">
      <div class="generation-record-top">
        <span class="job-badge ${mindmapStatusClass(item)}">${mindmapStatusText(item)}</span>
        <strong>${escapeHtml(item.title || caseSetId || '测试用例脑图')}</strong>
        <span>${escapeHtml(updatedAt ? `脑图更新 ${updatedAt}` : generatedAt)}</span>
      </div>
      <div class="job-progress">
        <div class="job-progress-text">
          <span>${escapeHtml([item.module, item.yaml_file].filter(Boolean).join(' / ') || caseSetId)}</span>
          <span>${escapeHtml(`${item.automation_case_count || 0} 自动化 · ${item.manual_case_count || 0} 人工 · ${item.scenario_count || 0} 场景`)}</span>
        </div>
        <div class="job-progress-track"><div class="job-progress-bar" style="width:${item.mindmap_exists ? 100 : 35}%"></div></div>
      </div>
      <p>${escapeHtml([priorityText, item.smoke_count ? `冒烟 ${item.smoke_count}` : '', item.mindmap_size ? formatBytes(item.mindmap_size) : '', generatedAt ? `生成 ${generatedAt}` : ''].filter(Boolean).join(' · ') || '脑图用于人工评审覆盖结构和测试点。')}</p>
      <div class="generation-record-card-actions">
        <button class="btn-sm" onclick="showGenerationReviewByCaseSet(${jsArg(caseSetId)})">生成分析</button>
        ${item.mindmap_downloadable ? `<a class="btn-sm" href="${mindmapDownloadUrl(caseSetId)}" target="_blank">下载脑图</a>` : ''}
        <button class="btn-sm primary" onclick="regenerateGenerationMindmap(${jsArg(caseSetId)}).then(() => showMindmapCenter())" title="只按现有生成分析刷新脑图文件（FreeMind .mm）；不调用千问，不改用例，不覆盖 YAML">刷新脑图文件</button>
        <button class="btn-sm danger" onclick="deleteGenerationMindmap(${jsArg(caseSetId)}).then(() => showMindmapCenter())">删除脑图</button>
        <button class="btn-sm danger" onclick="deleteGenerationMindmapRecord(${jsArg(caseSetId)})">删除记录</button>
      </div>
    </div>
  `;
}

async function showMindmapCenter() {
  const area = document.getElementById('editor-area');
  if (!area) return;
  activeWorkspaceMode = 'mindmap';
  setActiveWorkflow('generate');
  resetYamlToolbarForManager();
  area.className = 'editor-area';
  area.innerHTML = `
    <div class="generation-records">
      <div class="generation-record-head">
        <div class="workflow-kicker">MINDMAP CENTER · FreeMind .mm 文件</div>
        <h2>脑图中心</h2>
        <p>这里管理脑图文件（FreeMind .mm）。刷新脑图只重写文件；重新生成用例才会重新调用 AI、更新用例和 YAML。</p>
        <div class="generate-hint">
          刷新脑图文件：不调用千问、不改用例、不改 YAML，只把当前生成分析重新写成脑图文件。
          重新生成用例：重新读取需求/Figma/截图，重新生成用例、生成分析和脑图；完整 YAML 流程还会覆盖 YAML。
        </div>
        <div class="generation-record-actions">
          <button class="btn-sm primary" onclick="showCreateMindmapModal()">新建脑图</button>
          <button class="btn-sm primary" onclick="showMindmapCenter()">刷新脑图列表</button>
          <button class="btn-sm" onclick="showGenerateJobsCenter()">生成记录</button>
          <button class="btn-sm" onclick="activateWorkflow('generate')">去 AI 生成</button>
        </div>
      </div>
      <div id="mindmap-center-list" class="generation-record-empty">正在加载脑图...</div>
    </div>
  `;
  document.getElementById('toolbar-path').innerHTML = '<span>📁</span> 脑图中心';
  document.getElementById('toolbar-help').textContent = '集中下载、重建和删除脑图文件（FreeMind .mm）；需要改用例内容时回到生成分析补充资料后重新生成。';
  document.getElementById('file-info').textContent = '脑图中心';
  updateToolbarState('脑图中心');
  updateWorkflowActionGroups();
  try {
    const data = await apiRequest('/cases/mindmaps?limit=120');
    const rows = data.mindmaps || [];
    const list = document.getElementById('mindmap-center-list');
    if (!list) return;
    list.className = rows.length ? 'generation-record-list' : 'generation-record-empty';
    list.innerHTML = rows.length
      ? rows.map(mindmapRecordCard).join('')
      : '暂无脑图。先在「AI 生成」完成一次用例生成；脑图中心只管理已生成分析的脑图文件。';
  } catch(e) {
    const list = document.getElementById('mindmap-center-list');
    if (list) list.innerHTML = escapeHtml(e.message || '读取脑图失败');
  }
}

function reportCleanupResultHtml(data={}) {
  const policy = data.policy || {};
  const stats = data.stats || {};
  const items = data.items || [];
  return `
    <div class="review-panel" style="margin-top:14px;">
      <h3>${data.dry_run ? '清理预览' : '清理结果'}</h3>
      <div class="dashboard-metrics">
        <div class="dashboard-metric"><strong>${data.dry_run ? data.candidate_count || 0 : data.deleted_count || 0}</strong><span>${data.dry_run ? '候选文件' : '已删除'}</span></div>
        <div class="dashboard-metric"><strong>${formatBytes(data.reclaimed_bytes || 0)}</strong><span>预计/已释放</span></div>
        <div class="dashboard-metric"><strong>${stats.total_html || 0}</strong><span>报告总数</span></div>
        <div class="dashboard-metric"><strong>${policy.retention_days || '-'}</strong><span>保留天数</span></div>
      </div>
      <p>策略：至少保留最近 ${escapeHtml(policy.min_keep || 0)} 份 HTML 报告；超过 ${escapeHtml(policy.retention_days || '-')} 天且不在保护范围内的报告才会清理。</p>
      ${items.length ? `<div class="review-matrix"><table><thead><tr><th>文件</th><th>时间</th><th>大小</th></tr></thead><tbody>${items.slice(0, 50).map(item => `<tr><td>${escapeHtml(item.name || item.path)}</td><td>${escapeHtml(item.mtime || '')}</td><td>${formatBytes(item.size || 0)}</td></tr>`).join('')}</tbody></table></div>` : '<p>当前没有需要清理的报告。</p>'}
      ${data.errors?.length ? `<p><strong>清理异常：</strong>${escapeHtml(data.errors.map(item => item.error || item.path).join('；'))}</p>` : ''}
    </div>
  `;
}

async function previewReportCleanup() {
  const days = Number(document.getElementById('report-clean-days')?.value || 14);
  const minKeep = Number(document.getElementById('report-clean-keep')?.value || 200);
  const target = document.getElementById('report-clean-result');
  if (target) target.innerHTML = '<div class="generation-record-empty">正在计算清理候选...</div>';
  try {
    const data = await apiRequest(`/reports/cleanup?dry_run=1&days=${encodeURIComponent(days)}&min_keep=${encodeURIComponent(minKeep)}`);
    if (target) target.innerHTML = reportCleanupResultHtml(data);
  } catch(e) {
    if (target) target.innerHTML = `<div class="generate-status show error">${escapeHtml(e.message || '预览失败')}</div>`;
  }
}

async function runReportCleanup() {
  const days = Number(document.getElementById('report-clean-days')?.value || 14);
  const minKeep = Number(document.getElementById('report-clean-keep')?.value || 200);
  if (!confirm(`确认清理超过 ${days} 天的 Midscene HTML 报告？系统仍会至少保留最近 ${minKeep} 份。`)) return;
  const target = document.getElementById('report-clean-result');
  if (target) target.innerHTML = '<div class="generation-record-empty">正在清理报告...</div>';
  try {
    const data = await apiRequest('/reports/cleanup', {
      method: 'POST',
      body: JSON.stringify({ days, min_keep: minKeep, dry_run: false })
    });
    if (target) target.innerHTML = reportCleanupResultHtml(data);
    showToast(`✓ 报告清理完成，删除 ${data.deleted_count || 0} 个文件`, 'success');
  } catch(e) {
    if (target) target.innerHTML = `<div class="generate-status show error">${escapeHtml(e.message || '清理失败')}</div>`;
    showToast(e.message || '清理失败', 'error');
  }
}

async function showReportCleanupCenter() {
  const area = document.getElementById('editor-area');
  if (!area) return;
  setActiveWorkflow('config');
  resetYamlToolbarForManager();
  area.className = 'editor-area';
  area.innerHTML = `
    <div class="generation-records">
      <div class="generation-record-head">
        <div class="workflow-kicker">REPORT RETENTION · Midscene</div>
        <h2>Midscene 报告清理</h2>
        <p>用于控制本地 HTML 报告和上传分片占用。先预览，再执行清理；后台服务也会按同一策略定期清理。</p>
        <div class="figma-row" style="margin-top:12px;max-width:520px;">
          <input id="report-clean-days" type="number" min="1" value="14" title="保留天数">
          <input id="report-clean-keep" type="number" min="0" value="200" title="至少保留最近多少份">
        </div>
        <div class="generation-record-actions">
          <button class="btn-sm" onclick="previewReportCleanup()">预览清理</button>
          <button class="btn-sm danger" onclick="runReportCleanup()">立即清理</button>
          <button class="btn-sm" onclick="showPreflightDashboard()">环境体检</button>
        </div>
      </div>
      <div id="report-clean-result" class="generation-record-empty">点击“预览清理”查看将要删除的报告。</div>
    </div>
  `;
  document.getElementById('toolbar-path').innerHTML = '<span>📁</span> Midscene 报告清理';
  document.getElementById('toolbar-help').textContent = '清理过期 Midscene HTML 报告，避免长期回归后磁盘压力过大。';
  document.getElementById('file-info').textContent = '报告清理';
  updateToolbarState('报告清理');
  previewReportCleanup();
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function jsArg(value) {
  return escapeHtml(JSON.stringify(String(value || '')));
}

function renderGenerateAssetList() {
  const list = document.getElementById('generate-asset-list');
  if (!list) return;
  if (generateAssetFiles.length === 0) {
    list.innerHTML = '';
    return;
  }
  list.innerHTML = generateAssetFiles.map((file, index) => `
    <div class="asset-row">
      <span class="asset-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
      <span class="asset-meta">${escapeHtml(file.type || 'file')} · ${formatBytes(file.size || 0)}</span>
      <button class="asset-remove" type="button" onclick="removeGenerateAsset(${index})" title="移除">×</button>
    </div>
  `).join('');
}

function removeGenerateAsset(index) {
  generateAssetFiles.splice(index, 1);
  renderGenerateAssetList();
  const total = generateAssetFiles.reduce((sum, file) => sum + (file.size || 0), 0);
  if (generateAssetFiles.length === 0) setGenerateProgress(0, '准备生成', 0, false);
  setGenerateStatus(generateAssetFiles.length ? `已选择 ${generateAssetFiles.length} 个文件，共 ${formatBytes(total)}` : '文件已清空，可重新添加。');
}

function resetGenerateModal() {
  stopGenerateProgress();
  const currentKnowledgeApp = document.getElementById('knowledge-app-package')?.value.trim();
  document.getElementById('generate-app-package').value = moduleAppPackage(currentModule) || currentKnowledgeApp || 'com.kfb.model';
  syncAppSelect('generate');
  document.getElementById('generate-title').value = '';
  document.getElementById('generate-file').value = '';
  document.getElementById('generate-content').value = '';
  document.getElementById('generate-figma-url').value = '';
  document.getElementById('generate-figma-mode').value = 'smart';
  document.getElementById('generate-figma-limit').value = '80';
  document.getElementById('generate-knowledge-tier').value = 'all';
  document.getElementById('generate-create-job').checked = false;
  document.getElementById('generate-run-mode').value = 'test';
  document.getElementById('generate-device').value = '';
  document.getElementById('generate-auto-optimize').checked = false;
  syncRunModeControls();
  document.getElementById('generate-asset-files').value = '';
  generateAssetFiles = [];
  generateKnowledgePages = [];
  document.getElementById('generate-knowledge-list').innerHTML = '';
  document.getElementById('generate-knowledge-box').classList.remove('show');
  updateGenerateAppHint();
  renderGenerateAssetList();
  setGenerateStatus('');
  setGenerateProgress(0, '准备生成', 0, false);
  setGenerateWizardStep(0);
  const bar = document.getElementById('generate-progress-bar');
  if (bar) bar.style.background = 'var(--accent)';
}

function setGenerateBusy(busy) {
  generateBusy = busy;
  const button = document.getElementById('btn-generate-yaml');
  const closeButton = document.getElementById('btn-close-generate');
  button.disabled = busy;
  button.textContent = busy ? '生成中...' : '生成并进入调试';
  if (closeButton) closeButton.disabled = busy;
  document.querySelectorAll('#modal-generate input, #modal-generate select, #modal-generate textarea, #modal-generate .btn-cancel, #modal-generate .asset-remove')
    .forEach(el => el.disabled = busy);
}

function closeGenerateModal() {
  if (generateBusy) {
    setGenerateStatus('正在生成中，完成后会自动关闭并打开 YAML。', 'busy');
    return;
  }
  closeModal('modal-generate');
  resetGenerateModal();
}

function showGenerateYaml() {
  setActiveWorkflow('generate');
  renderModuleSelects();
  resetGenerateModal();
  document.getElementById('generate-module').value = currentModule || '';
  syncGenerateAppFromModule();
  updateGenerateAppHint();
  setGenerateStatus('先上传资料或粘贴说明，平台会先做需求分析、用例分类，再生成可调试的 Midscene 自动化脚本。');
  document.getElementById('modal-generate').classList.add('show');
  loadKnowledgeApps().then(() => {
    syncAppSelect('generate');
    loadGenerateKnowledgePages();
  });
  loadRunnerDevices();
}

async function fileToAsset(file) {
  const isText = /\.(txt|md|json)$/i.test(file.name);
  if (isText) {
    return {
      name: file.name,
      content: await file.text(),
      size: file.size,
      type: file.type || 'text'
    };
  }
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
  return {
    name: file.name,
    contentBase64: dataUrl.split(',')[1] || '',
    size: file.size,
    type: file.type || file.name.split('.').pop().toLowerCase()
  };
}

function setMindmapStatus(text, type='') {
  const el = document.getElementById('mindmap-status');
  if (!el) return;
  el.textContent = text || '';
  el.className = `generate-status ${type || ''}`;
}

function renderMindmapAssetList() {
  const list = document.getElementById('mindmap-asset-list');
  if (!list) return;
  list.innerHTML = mindmapAssetFiles.map((file, index) => `
    <div class="asset-item">
      <span>${escapeHtml(file.name)} · ${formatBytes(file.size || 0)}</span>
      <button class="asset-remove" onclick="mindmapAssetFiles.splice(${index},1);renderMindmapAssetList()">移除</button>
    </div>
  `).join('');
}

async function handleMindmapAssetFiles(input) {
  const files = Array.from(input.files || []);
  await addMindmapAssetFiles(files);
  input.value = '';
}

async function addMindmapAssetFiles(files, source='选择') {
  if (!files.length) return;
  const allowed = /\.(txt|md|json|pdf|docx?|mm|png|jpe?g)$/i;
  const invalid = files.find(file => !allowed.test(file.name));
  if (invalid) {
    setMindmapStatus(`不支持的文件类型：${invalid.name}`, 'error');
    return;
  }
  setMindmapStatus(`正在读取 ${files.length} 个文件...`, 'busy');
  try {
    for (const file of files) {
      const asset = await fileToAsset(file);
      const exists = mindmapAssetFiles.findIndex(item => item.name === asset.name);
      if (exists >= 0) mindmapAssetFiles[exists] = asset;
      else mindmapAssetFiles.push(asset);
    }
  } catch(e) {
    setMindmapStatus(`文件读取失败：${e.message || e}`, 'error');
    return;
  }
  if (!document.getElementById('mindmap-title').value.trim()) {
    document.getElementById('mindmap-title').value = files[0].name.replace(/\.(txt|md|json|pdf|docx?|mm|png|jpe?g)$/i, '');
  }
  renderMindmapAssetList();
  setMindmapStatus(`已${source} ${files.length} 个文件；当前共 ${mindmapAssetFiles.length} 个文件。`, 'success');
}

function setMindmapBusy(busy) {
  mindmapBusy = busy;
  const button = document.getElementById('btn-create-mindmap');
  if (button) {
    button.disabled = busy;
    button.textContent = busy ? '生成中...' : '只生成脑图';
  }
  document.querySelectorAll('#modal-mindmap-create input, #modal-mindmap-create select, #modal-mindmap-create textarea, #modal-mindmap-create .btn-cancel, #modal-mindmap-create .asset-remove')
    .forEach(el => el.disabled = busy);
  document.querySelectorAll('#modal-mindmap-create .modal-close')
    .forEach(el => el.disabled = false);
}

function closeMindmapCreateModal() {
  if (mindmapBusy) {
    setMindmapStatus('脑图正在生成中，完成后会自动刷新列表。', 'busy');
    return;
  }
  mindmapBusy = false;
  setMindmapBusy(false);
  const modal = document.getElementById('modal-mindmap-create');
  if (modal) modal.classList.remove('show');
}

function showCreateMindmapModal() {
  renderModuleSelects();
  document.getElementById('mindmap-title').value = '';
  document.getElementById('mindmap-module').innerHTML = '<option value="">选择归属模块</option>' + Object.keys(modules).map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join('');
  document.getElementById('mindmap-module').value = currentModule || '';
  const appPackage = currentModuleAppPackage() || document.getElementById('generate-app-package')?.value.trim() || 'com.kfb.model';
  document.getElementById('mindmap-app-package').value = appPackage;
  document.getElementById('mindmap-figma-url').value = '';
  document.getElementById('mindmap-figma-mode').value = 'smart';
  document.getElementById('mindmap-figma-limit').value = '80';
  document.getElementById('mindmap-content').value = '';
  document.getElementById('mindmap-asset-files').value = '';
  mindmapAssetFiles = [];
  renderMindmapAssetList();
  setMindmapStatus('上传需求、已有用例或截图后，只生成脑图文件（FreeMind .mm），不生成 YAML。');
  document.getElementById('modal-mindmap-create').classList.add('show');
  loadKnowledgeApps().then(() => syncAppSelect('mindmap'));
}

async function createMindmapOnly() {
  const title = document.getElementById('mindmap-title').value.trim() || '测试用例脑图';
  const module = document.getElementById('mindmap-module').value || 'AI测试';
  const supplement = document.getElementById('mindmap-content').value.trim();
  const figmaUrl = document.getElementById('mindmap-figma-url').value.trim();
  if (!mindmapAssetFiles.length && !supplement && !figmaUrl) {
    setMindmapStatus('请先上传需求/截图/已有用例，填写补充说明，或粘贴 Figma 链接。', 'error');
    return;
  }
  const files = [...mindmapAssetFiles];
  if (supplement) {
    files.push({ name: `mindmap-supplement-${Date.now()}.txt`, content: supplement, size: supplement.length, type: 'text/plain' });
  }
  setMindmapBusy(true);
  setMindmapStatus('正在创建只生成脑图的后台任务...', 'busy');
  try {
    const created = await apiRequest('/cases/mindmap-only-async', {
      method: 'POST',
      body: JSON.stringify({
        title,
        module,
        app_package: document.getElementById('mindmap-app-package').value.trim() || 'com.kfb.model',
        figma_url: figmaUrl,
        figma_mode: document.getElementById('mindmap-figma-mode').value || 'smart',
        figma_limit: Number(document.getElementById('mindmap-figma-limit').value || 80),
        files
      })
    });
    const data = await pollGenericJob(created.job_id, job => {
      setMindmapStatus(`${job.message || job.step || '正在生成脑图'} · ${Number(job.progress || 0)}%`, 'busy');
    });
    setMindmapStatus(`脑图已生成：${data.case_set_id || ''}`, 'success');
    showToast('✓ 脑图生成完成，未生成 YAML', 'success');
    closeModal('modal-mindmap-create');
    mindmapBusy = false;
    await showMindmapCenter();
  } catch(e) {
    setMindmapStatus(e.message || '生成脑图失败', 'error');
    showToast(e.message || '生成脑图失败', 'error');
  } finally {
    setMindmapBusy(false);
  }
}

async function handleGenerateAssetFiles(input) {
  const files = Array.from(input.files || []);
  await addGenerateAssetFiles(files);
  input.value = '';
}

async function addGenerateAssetFiles(files, source='选择') {
  if (files.length === 0) return;

  const allowed = /\.(txt|md|json|pdf|docx?|mm|png|jpe?g)$/i;
  const invalid = files.find(file => !allowed.test(file.name));
  if (invalid) {
    setGenerateStatus(`不支持的文件类型：${invalid.name}`, 'error');
    return;
  }

  setGenerateProgress(8, '读取本地文件', 0);
  setGenerateStatus(`正在读取 ${files.length} 个文件...`, 'busy');
  try {
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const asset = await fileToAsset(file);
      const exists = generateAssetFiles.findIndex(item => item.name === asset.name);
      if (exists >= 0) generateAssetFiles[exists] = asset;
      else generateAssetFiles.push(asset);
      setGenerateProgress(8 + Math.round(((i + 1) / files.length) * 22), `读取文件 ${i + 1}/${files.length}`, 0);
    }
  } catch(e) {
    setGenerateProgress(0, '读取失败', 0, false);
    setGenerateStatus(`文件读取失败：${e.message || e}`, 'error');
    return;
  }
  renderGenerateAssetList();
  const total = generateAssetFiles.reduce((sum, file) => sum + (file.size || 0), 0);
  setGenerateProgress(30, '文件读取完成', 1);
  setGenerateStatus(`已${source} ${files.length} 个文件；当前共 ${generateAssetFiles.length} 个文件，${formatBytes(total)}。`, 'success');

  if (!document.getElementById('generate-title').value.trim()) {
    document.getElementById('generate-title').value = files[0].name.replace(/\.(txt|md|json|pdf|docx?|mm|png|jpe?g)$/i, '');
  }
  if (!document.getElementById('generate-file').value.trim()) {
    document.getElementById('generate-file').value = `task-${files[0].name.replace(/\.(txt|md|json|pdf|docx?|mm|png|jpe?g)$/i, '')}.yaml`;
  }
}

async function handleGeneratePaste(event) {
  if (isEditablePasteTarget(event.target)) return false;
  const items = Array.from(event.clipboardData?.items || []);
  const pastedFiles = [];
  for (let i = 0; i < items.length; i++) {
    if (items[i].kind === 'file') {
      const file = await fileFromClipboardItem(items[i], i);
      if (file) pastedFiles.push(file);
    }
  }

  if (pastedFiles.length) {
    event.preventDefault();
    await addGenerateAssetFiles(pastedFiles, '粘贴');
    return true;
  }

  const text = event.clipboardData?.getData('text/plain') || '';
  if (text.trim()) {
    event.preventDefault();
    const file = new File([text], `pasted-requirement-${Date.now()}.txt`, { type: 'text/plain' });
    await addGenerateAssetFiles([file], '粘贴');
    return true;
  }
  return false;
}

async function handleKnowledgePaste(event) {
  if (isEditablePasteTarget(event.target)) return false;
  const items = Array.from(event.clipboardData?.items || []);
  for (let i = 0; i < items.length; i++) {
    if (items[i].kind === 'file') {
      const file = await fileFromClipboardItem(items[i], i);
      if (file && /^image\//.test(file.type || '')) {
        event.preventDefault();
        await handleKnowledgePastedFile(file);
        return true;
      }
    }
  }
  return false;
}

function isEditablePasteTarget(target) {
  if (!target) return false;
  const tag = (target.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable;
}

async function generateYaml() {
  const mod = document.getElementById('generate-module').value;
  const title = document.getElementById('generate-title').value.trim() || 'UI自动化用例';
  const appPackage = document.getElementById('generate-app-package').value.trim() || 'com.kfb.model';
  const fileInput = document.getElementById('generate-file').value.trim();
  const content = document.getElementById('generate-content').value.trim();
  const figmaUrl = document.getElementById('generate-figma-url').value.trim();
  const figmaMode = document.getElementById('generate-figma-mode').value || 'smart';
  const figmaLimit = Number(document.getElementById('generate-figma-limit').value || 80);
  const createJob = document.getElementById('generate-create-job').checked;
  const autoOptimize = document.getElementById('generate-auto-optimize').checked;
  const runMode = document.getElementById('generate-run-mode').value || 'test';
  const selectedDevice = createJob ? requireRunnerDevice('generate-device', '', '生成后创建调试任务') : selectedRunnerDevice();
  const knowledgePageIds = selectedGenerateKnowledgePageIds();
  const knowledgeTier = document.getElementById('generate-knowledge-tier').value || 'all';

  if (!mod || (!content && generateAssetFiles.length === 0 && !figmaUrl)) {
    setGenerateStatus('请选择目标模块，并上传文件、填写需求补充说明或粘贴 Figma 链接。', 'error');
    showToast('请选择模块，并上传文件、填写需求说明或粘贴 Figma 链接', 'error');
    return;
  }
  if (!selectedDevice) {
    return;
  }

  const file = fileInput || `task-${title}.yaml`;
  rememberAppPackage(appPackage);
  setGenerateBusy(true);
  setGenerateProgress(32, '准备上传资料', 1);
  setGenerateStatus('正在上传资料并调用模型生成用例，稍等一下...', 'busy');

  try {
    const files = [...generateAssetFiles];
    if (content) {
      files.unshift({
        name: 'requirement.txt',
        content
      });
    }

    setGenerateProgress(35, '创建后台生成任务', 1);
    const created = await apiRequest('/ui/generate-yaml-async', {
      method: 'POST',
      body: JSON.stringify({
        title,
        module: mod,
        app_package: appPackage,
        knowledge_page_ids: knowledgePageIds,
        knowledge_tier: knowledgeTier,
        figma_url: figmaUrl,
        figma_mode: figmaMode,
        figma_limit: figmaLimit,
        file,
        files,
        createJob,
        autoOptimize,
        run_mode: runMode,
        runner_id: selectedDevice.runner_id,
        device_id: selectedDevice.device_id,
        device_strategy: selectedDevice.device_strategy
      })
    });
    setGenerateProgress(40, '后台任务已创建，等待模型生成', 2);
    showToast('✓ 生成任务已提交，已切到生成记录查看进度', 'success');
    closeModal('modal-generate');
    await showGenerateJobsCenter();
    const data = await pollGenerateJob(created.job_id);
    loadJobs();

    const resultModule = data.module || mod;
    const resultFile = data.file || file;
    await loadModules();
    if (!modules[resultModule]) modules[resultModule] = [];
    if (!modules[resultModule].includes(resultFile)) modules[resultModule].push(resultFile);
    currentModule = resultModule;
    currentFile = resultFile;
    const appFilter = document.getElementById('app-filter');
    if (appFilter && appFilter.value) {
      const selectedApp = taskApps.find(app => app.package === appFilter.value);
      if (selectedApp && !(selectedApp.modules || []).includes(resultModule)) {
        appFilter.value = '';
      }
    }
    renderModules();
    const manual = data.manualCaseCount ? `，${data.manualCaseCount} 条转人工` : '';
    const jobText = data.job ? '，已创建执行任务' : '';
    const resultNote = buildGenerateResultNote(data);
    setGenerateProgress(100, '生成完成', 4);
    setGenerateStatus(`生成成功：${resultModule}/${resultFile}，共 ${data.caseCount || 0} 条用例${manual}${jobText}\n已打开生成结果评审页，确认后可查看自动化脚本或单条调试。\n${resultNote}`, 'success');
    showToast(`✓ 已生成 ${data.caseCount || 0} 条用例${manual}${jobText}`, 'success');
    showGenerationReview(data);
  } catch(e) {
    stopGenerateProgress();
    setGenerateProgressError('生成失败');
    setGenerateStatus(e.message || '生成失败，请检查服务端日志或模型配置。', 'error');
    loadJobs().catch(() => {});
    showToast(e.message || '生成失败', 'error');
  } finally {
    setGenerateBusy(false);
  }
}


// 快捷键
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 's') { e.preventDefault(); saveFile(); }
  if (e.key === 'Escape' && document.getElementById('modal-generate').classList.contains('show')) {
    closeGenerateModal();
  }
});

document.addEventListener('click', e => {
  if (!e.target.closest('.toolbar-menu')) {
    document.getElementById('more-actions')?.classList.remove('show');
  }
});

document.addEventListener('paste', async e => {
  if (document.getElementById('modal-knowledge').classList.contains('show')) {
    await handleKnowledgePaste(e);
    return;
  }
  if (document.getElementById('modal-generate').classList.contains('show')) {
    await handleGeneratePaste(e);
    return;
  }
  if (activeWorkflow === 'agent' && document.getElementById('agent-source-panel')) {
    await handleAgentSourcePaste(e);
  }
});

['generate-upload-zone', 'knowledge-upload-zone'].forEach(id => {
  const zone = document.getElementById(id);
  if (!zone) return;
  zone.addEventListener('focus', () => zone.classList.add('paste-active'));
  zone.addEventListener('blur', () => zone.classList.remove('paste-active'));
});

// 初始化检查登录
initResizableLayout();
async function initAuthSession() {
  const token = sessionToken();
  if (!token) return;
  try {
    const data = await apiRequest('/auth/me');
    if (data.ok && data.user) {
      sessionStorage.setItem('user', data.user);
      showAuthedApp();
      return;
    }
  } catch(e) {}
  sessionStorage.removeItem('user');
  sessionStorage.removeItem('sessionToken');
}
initAuthSession();
