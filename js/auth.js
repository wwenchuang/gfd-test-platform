// auth.js
// Extracted from task-manager.html (no logic changes).

// ===== LOGIN =====
function clearTransientAuthFeedback() {
  const error = document.getElementById('login-error');
  if (error) error.style.display = 'none';
  const toast = document.getElementById('toast');
  if (toast) {
    if (toast._toastTimer) clearTimeout(toast._toastTimer);
    toast.className = 'toast';
    toast.textContent = '';
  }
}

function showAuthedApp() {
  clearTransientAuthFeedback();
  if (typeof restoreWorkflowPreference === 'function') restoreWorkflowPreference();
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app').style.display = 'flex';
  renderWorkflowNav();
  if (typeof initNavigationGroupPreferences === 'function') initNavigationGroupPreferences();
  toggleLibrary(false); // Hide library by default, Agent workbench first
  updateContextToolbar();
  if (typeof renderActiveWorkflowPage === 'function') renderActiveWorkflowPage();
  else showWorkflowGuide(activeWorkflow);
  if (typeof applyLazyLoadForSection === 'function') applyLazyLoadForSection(activeWorkflow);
}

function loginReturnToPath() {
  const params = new URLSearchParams(window.location.search || '');
  const value = String(params.get('return_to') || '').trim();
  if (!value || !value.startsWith('/') || value.startsWith('//')) return '';
  if (/[\r\n]/.test(value)) return '';
  return value;
}

async function doLogin() {
  const u = document.getElementById('username').value.trim();
  const p = document.getElementById('password').value;
  document.getElementById('login-error').style.display = 'none';
  try {
    // 登录接口属于无会话状态调用，401 表示账号或密码错误，跳过统一登出跳转
    const data = await apiRequest('/auth/login', {
      method: 'POST',
      body: {username: u, password: p},
      skipAuthRedirect: true
    });
    if (!data || !data.ok || !data.token) throw new Error(data?.error || '账号或密码错误');
    sessionStorage.setItem('user', data.user || u);
    sessionStorage.setItem('sessionToken', data.token);
    const returnTo = loginReturnToPath();
    if (returnTo) {
      window.location.assign(returnTo);
      return;
    }
    showAuthedApp();
  } catch(e) {
    const rawMessage = String(e?.message || '登录失败');
    const message = /HTTP\s+404|Not Found/i.test(rawMessage)
      ? '登录服务版本不匹配，请联系管理员重新部署并重启后端服务'
      : rawMessage;
    const error = document.getElementById('login-error');
    error.textContent = message;
    error.style.display = 'block';
    showToast(message, 'error');
  }
}

async function doLogout() {
  try {
    await apiRequest('/auth/logout', {method: 'POST', body: {}});
  } catch(e) {}
  sessionStorage.removeItem('user');
  sessionStorage.removeItem('sessionToken');
  location.reload();
}
