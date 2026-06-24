// auth.js
// Extracted from task-manager.html (no logic changes).

// ===== LOGIN =====
function showAuthedApp() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app').style.display = 'flex';
  renderWorkflowNav();
  toggleLibrary(false); // Hide library by default, Agent workbench first
  updateContextToolbar();
  if (typeof renderActiveWorkflowPage === 'function') renderActiveWorkflowPage();
  else showWorkflowGuide(activeWorkflow);
  // round 4: 首屏只加载 Agent 工作台必需的数据，其它模块进入对应页面再懒加载
  ensureAgentRunsLoaded({ limit: 10 }).catch(() => {});
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
    showAuthedApp();
  } catch(e) {
    document.getElementById('login-error').style.display = 'block';
    showToast(e.message || '登录失败', 'error');
  }
}
document.getElementById('password').addEventListener('keydown', e => { if(e.key==='Enter') doLogin(); });

async function doLogout() {
  try {
    await apiRequest('/auth/logout', {method: 'POST', body: {}});
  } catch(e) {}
  sessionStorage.removeItem('user');
  sessionStorage.removeItem('sessionToken');
  location.reload();
}
