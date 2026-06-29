// utils.js
// Extracted from task-manager.html (no logic changes).

// ===== ADD MODULE =====
function showAddModule() { document.getElementById('modal-module').classList.add('show'); }
async function addModule() {
  const name = document.getElementById('new-module-name').value.trim();
  if (!name) return;
  try {
    await apiRequest('/module', { method:'POST', body: JSON.stringify({name}) });
  } catch(e) {}
  modules[name] = modules[name] || [];
  renderModules();
  closeModal('modal-module');
  document.getElementById('new-module-name').value = '';
  showToast(`✓ 模块「${name}」已创建`, 'success');
}

// ===== ADD TASK =====
function showAddTask() { document.getElementById('modal-task').classList.add('show'); }
async function addTask() {
  const mod = document.getElementById('new-task-module').value;
  const name = document.getElementById('new-task-name').value.trim();
  if (!mod || !name) { showToast('请填写完整信息', 'error'); return; }
  const filename = name.endsWith('.yaml') ? name : name + '.yaml';
  const defaultContent = `android:\n  deviceId: UQG0220513008845\n\ntasks:\n  - name: ${name}\n    flow:\n      - sleep: 1000\n      - ai: \n`;
  try {
    await apiRequest('/file', {
      method: 'POST',
      body: JSON.stringify({ module: mod, file: filename, content: defaultContent })
    });
  } catch(e) {}
  if (!modules[mod]) modules[mod] = [];
  if (!modules[mod].includes(filename)) modules[mod].push(filename);
  renderModules();
  closeModal('modal-task');
  document.getElementById('new-task-name').value = '';
  openFile(mod, filename);
  showToast(`✓ Task「${filename}」已创建`, 'success');
}

// ===== UPLOAD =====
function showUpload() { document.getElementById('modal-upload').classList.add('show'); }
function handleFileSelect(input) {
  const file = input.files[0];
  if (!file) return;
  uploadFileName = file.name;
  document.getElementById('upload-filename').textContent = `已选择：${file.name}`;
  const reader = new FileReader();
  reader.onload = e => { uploadFileContent = e.target.result; };
  reader.readAsText(file);
}
async function uploadFile() {
  const mod = document.getElementById('upload-module').value;
  if (!mod || !uploadFileName) { showToast('请选择模块和文件', 'error'); return; }
  try {
    await apiRequest('/file', {
      method: 'POST',
      body: JSON.stringify({ module: mod, file: uploadFileName, content: uploadFileContent })
    });
  } catch(e) {}
  if (!modules[mod]) modules[mod] = [];
  if (!modules[mod].includes(uploadFileName)) modules[mod].push(uploadFileName);
  renderModules();
  closeModal('modal-upload');
  openFile(mod, uploadFileName);
  showToast(`✓ 上传成功：${uploadFileName}`, 'success');
}


// ===== UTILS =====
function openModal(id) {
  const modal = document.getElementById(id);
  if (!modal) {
    showToast(`弹窗不存在：${id}`, 'error');
    return false;
  }
  modal.classList.add('show');
  return true;
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.classList.remove('show');
}

function toggleMoreMenu(event) {
  event?.stopPropagation();
  document.getElementById('more-actions')?.classList.toggle('show');
}

function formatDisplayTime(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  const zoneAware = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(text);
  if (zoneAware) {
    const date = new Date(text);
    if (!Number.isNaN(date.getTime())) {
      return new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
      }).format(date).replace(/\//g, '-');
    }
  }
  return text.replace('T', ' ').slice(0, 19);
}

function showToast(msg, type = 'success', duration) {
  const t = document.getElementById('toast');
  if (!t) return;
  const icons = { success: '✓', error: '✕', warn: '⚠', info: 'ℹ' };
  // 如果消息本身已经包含 emoji 图标则不重复添加
  const hasEmoji = /[\u{1F300}-\u{1FAFF}]|[\u2600-\u27FF]/u.test(msg.slice(0, 4));
  t.textContent = hasEmoji ? msg : `${icons[type] || '•'} ${msg}`;
  t.className = `toast ${type} show`;
  // 错误消息显示更久
  const ms = duration || (type === 'error' ? 5000 : type === 'warn' ? 4000 : 3000);
  if (t._toastTimer) clearTimeout(t._toastTimer);
  t._toastTimer = setTimeout(() => { t.className = 'toast'; }, ms);
}
