// auth.js
// Profiles stay in memory; the server rechecks authorization on every request.
let currentAccessProfile = null;

function acceptAuthSession(data) {
  if (data.token) sessionStorage.setItem('sessionToken', data.token);
  sessionStorage.setItem('user', typeof data.user === 'string' ? data.user : data.profile?.username || '');
  // Older visual fixtures omit profile. An explicit empty profile fails closed.
  currentAccessProfile = Object.hasOwn(data, 'profile') ? (data.profile || {}) : null;
  const label = document.getElementById('account-name');
  if (label) label.textContent = currentAccessProfile?.display_name || sessionStorage.getItem('user') || '个人账号';
}

function hasPermission(permission) {
  if (currentAccessProfile === null) return true;
  if (currentAccessProfile.must_change_password || currentAccessProfile.status === 'disabled') return false;
  return currentAccessProfile.is_superuser === true || (currentAccessProfile.permissions || []).includes(permission);
}

function requireUiEditPermission() {
  if (hasPermission('ui.edit')) return true;
  showToast('当前账号没有用例编辑权限，请联系管理员确认角色和数据授权', 'error');
  return false;
}

function requireUiDeletePermission() {
  if (hasPermission('ui.delete')) return true;
  showToast('当前账号没有删除用例权限；移动和重命名也需要此权限，请联系管理员', 'error');
  return false;
}

function canOperateAgent() {
  return currentAccessProfile === null || (hasPermission('platform.configure') && hasPermission('ui.execute')
    && (currentAccessProfile.is_superuser === true || currentAccessProfile.scope?.ui_apps === '*'));
}

function agentAccessReason() {
  return 'Agent 自动编排需要 platform.configure、ui.execute 权限及全部 UI 应用数据范围，请联系管理员。';
}

function canUseSharedUiAi() {
  return currentAccessProfile === null || (hasPermission('ui.edit')
    && (currentAccessProfile.is_superuser === true || currentAccessProfile.scope?.ui_apps === '*'));
}

function uiAiAccessReason() {
  return 'AI 生成需完整 UI 应用范围（共用基线库）';
}

function canAccessGlobalSonic() {
  return currentAccessProfile === null || (hasPermission('platform.configure')
    && (currentAccessProfile.is_superuser === true || currentAccessProfile.scope?.ui_apps === '*'));
}

function sonicAccessReason() {
  return '查看 Sonic 全局状态需平台配置权限及完整 UI 应用范围';
}

function applyRestrictedActionControls() {
  const restrict = (selector, allowed, reason) => {
    document.querySelectorAll(selector).forEach(node => {
      if (!allowed) {
        if (!node.dataset.accessDisabled) node.dataset.accessWasDisabled = String(node.disabled);
        node.dataset.accessDisabled = '1';
        node.disabled = true;
        node.title = reason;
      } else if (node.dataset.accessDisabled) {
        node.disabled = node.dataset.accessWasDisabled === 'true';
        delete node.dataset.accessDisabled;
        delete node.dataset.accessWasDisabled;
        node.removeAttribute('title');
      }
    });
  };
  restrict('#agent-start-btn', canOperateAgent(), agentAccessReason());
  restrict('[onclick*="showAddTask("], [onclick*="showUpload("], [onclick*="changeTaskBusiness("], [onclick*="openKnowledgePageForEdit("], #btn-copy-file, #btn-save, .task-nav-bulk-select, .priority-select, [data-action-permission="ui.edit"]', hasPermission('ui.edit'), '当前账号没有用例编辑权限，请联系管理员');
  restrict('[data-action-permission="ui.delete"]', hasPermission('ui.delete'), '当前账号没有删除用例权限，请联系管理员');
  restrict('#btn-move-file, #btn-rename-file, [data-action-permission="ui.edit ui.delete"]', hasPermission('ui.edit') && hasPermission('ui.delete'), '移动和重命名需要编辑及删除用例权限，请联系管理员');
  restrict('[data-action-permission="platform.configure"]', canAccessGlobalSonic(), '此操作需要平台配置权限及完整 UI 应用范围，请联系管理员');
  restrict('[onclick*="runTaskFromNav("], #btn-run-file, #btn-run-task, [data-action-permission="ui.execute"]', hasPermission('ui.execute'), '当前账号没有执行用例权限，请联系管理员');
  restrict('[onclick*="showBaselineRefsForCurrentTask("], [onclick*="removeBaselinePreviewRef("], [data-action-permission="ui.baseline"]', hasPermission('ui.baseline'), '当前账号没有基线管理权限，请联系管理员');
  const editor = document.getElementById('editor');
  if (editor) editor.readOnly = !hasPermission('ui.edit');
  restrict('#btn-sonic-status, .sonic-preview-actions button', canAccessGlobalSonic(), sonicAccessReason());
  restrict('[data-workflow="generate"], #btn-generate-yaml, #btn-repair-file, #btn-repair-task, [onclick*="repairTaskFromNav("], [onclick*="showGenerateYaml("], [onclick*="showGenerateMindmap("], [onclick*="retryGenerateJob("]', canUseSharedUiAi(), uiAiAccessReason());
}

const WORKFLOW_PERMISSIONS = {
  identity: 'auth.manage', config: 'platform.configure', app_config: 'platform.configure',
  sonic_config: 'platform.configure', system_config: 'platform.configure', feishu_config: 'platform.notify',
  dashboard: 'ui.execute', agent: 'ui.execute', agent_confirm: 'ui.execute', agent_history: 'ui.view',
  assets: 'ui.view', generate: 'ui.edit', yaml_edit: 'ui.edit', execute: 'ui.execute',
  baseline: 'ui.baseline', repair: 'ui.edit', failure_analysis: 'ui.view',
  reports: 'ui.view', bug_drafts: 'ui.view', knowledge: 'ui.view'
};

function canAccessWorkflow(key) {
  if (['dashboard', 'agent', 'agent_confirm'].includes(key)) return canOperateAgent();
  if (key === 'generate') return canUseSharedUiAi();
  return !WORKFLOW_PERMISSIONS[key] || hasPermission(WORKFLOW_PERMISSIONS[key]);
}

function applyAccessNavigation() {
  document.querySelectorAll('[data-workflow]').forEach(node => {
    node.hidden = node.dataset.workflow === 'generate' ? !hasPermission('ui.edit') : !canAccessWorkflow(node.dataset.workflow);
  });
  document.querySelectorAll('.api-test-link').forEach(node => { node.hidden = !hasPermission('api.view'); });
  document.querySelectorAll('.nav-group').forEach(group => {
    group.hidden = !Array.from(group.querySelectorAll('[data-workflow]')).some(node => !node.hidden);
  });
  document.querySelectorAll('[data-permission]').forEach(node => { node.hidden = !hasPermission(node.dataset.permission); });
  const notice = document.getElementById('ui-access-notice');
  if (notice) { notice.textContent = uiAiAccessReason(); notice.hidden = !hasPermission('ui.edit') || canUseSharedUiAi(); }
  applyRestrictedActionControls();
}

function clearAuthSession() {
  sessionStorage.removeItem('user');
  sessionStorage.removeItem('sessionToken');
  currentAccessProfile = null;
  if (typeof closeIdentityDialog === 'function') closeIdentityDialog();
  if (typeof stopJobsAutoRefresh === 'function') stopJobsAutoRefresh();
  const gate = document.getElementById('password-gate');
  if (gate) { gate.replaceChildren(); gate.hidden = true; }
  document.getElementById('app').style.display = 'none';
  document.getElementById('login-screen').style.display = 'flex';
  // Drop cached records and polling state before another identity uses this tab.
  window.location.reload();
}

new MutationObserver(applyRestrictedActionControls).observe(document.getElementById('app'), { childList: true, subtree: true });

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
  if (!canAccessWorkflow(activeWorkflow)) {
    activeWorkflow = ['dashboard', 'assets', 'identity', 'config', 'feishu_config'].find(canAccessWorkflow) || 'account';
  }
  if (typeof updateWorkbenchPanelMode === 'function') updateWorkbenchPanelMode();
  document.getElementById('toolbar-help').textContent = WORKFLOW_SECTIONS[activeWorkflow]?.help || '';
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app').style.display = 'flex';
  renderWorkflowNav();
  applyAccessNavigation();
  if (typeof initNavigationGroupPreferences === 'function') initNavigationGroupPreferences();
  toggleLibrary(false); // Hide library by default, Agent workbench first
  updateContextToolbar();
  if (activeWorkflow === 'account') {
    document.getElementById('editor-area').innerHTML = '<div class="identity-center"><h2>个人账号</h2><button class="btn-sm" onclick="showPersonalAccount()">个人资料与会话</button></div>';
  } else if (typeof renderActiveWorkflowPage === 'function') renderActiveWorkflowPage();
  else showWorkflowGuide(activeWorkflow);
  if (typeof applyLazyLoadForSection === 'function') applyLazyLoadForSection(activeWorkflow);
  applyAccessNavigation();
}

function loginReturnToPath() {
  const params = new URLSearchParams(window.location.search || '');
  const value = String(params.get('return_to') || '').trim();
  if (!value || !value.startsWith('/') || value.startsWith('//')) return '';
  if (/[\x00-\x20\\]/.test(value)) return '';
  const target = new URL(value, window.location.origin);
  if (target.origin !== window.location.origin || target.pathname === '/task-manager.html') return '';
  return target.pathname + target.search + target.hash;
}

function continueAfterAuthentication() {
  if (currentAccessProfile?.must_change_password) {
    showChangePassword(true);
    return;
  }
  const returnTo = loginReturnToPath();
  if (returnTo) {
    window.location.assign(returnTo);
    return;
  }
  showAuthedApp();
}

async function doLogin() {
  const button = document.querySelector('#login-form button');
  if (button.disabled) return;
  button.disabled = true;
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
    acceptAuthSession({ ...data, user: data.user || u });
    document.getElementById('password').value = '';
    continueAfterAuthentication();
  } catch(e) {
    const rawMessage = String(e?.message || '登录失败');
    const message = /HTTP\s+404|Not Found/i.test(rawMessage)
      ? '登录服务版本不匹配，请联系管理员重新部署并重启后端服务'
      : rawMessage;
    const error = document.getElementById('login-error');
    error.textContent = message;
    error.style.display = 'block';
    showToast(message, 'error');
  } finally {
    button.disabled = false;
  }
}

async function doLogout() {
  try {
    await apiRequest('/auth/logout', {method: 'POST', body: {}});
  } catch(e) {}
  clearAuthSession();
}
