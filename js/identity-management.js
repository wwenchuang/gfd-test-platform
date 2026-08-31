// Local identity UI. Permission and scope decisions remain authoritative on the server.
(() => {
  const tabs = { members: '成员', roles: '角色', scope: '数据授权', audit: '操作记录' };
  const scopeLabels = { ui_apps: 'UI 应用', api_projects: 'API 项目', api_environments: 'API 环境' };
  let selectedTab = 'members';
  let pageVersion = 0;
  let activeDialog = null;
  const e = value => escapeHtml(String(value ?? ''));
  const emptyScope = () => ({ ui_apps: [], api_projects: [], api_environments: [] });
  const roleIds = user => Array.isArray(user.role_ids) ? user.role_ids : [];
  const isSuper = user => user.is_superuser || roleIds(user).includes('super_admin');
  const button = (label, action, index, cls = '', attributes = '') => `<button type="button" class="btn-sm ${cls}" data-action="${action}" data-index="${index}" ${attributes}>${label}</button>`;
  const message = (target, text) => { const node = target.querySelector('[role="alert"]'); if (node) { node.textContent = text; node.hidden = false; } };

  async function request(path, options = {}) {
    return apiRequest('/auth' + path, { timeoutMs: 15000, ...options });
  }

  function closeDialog() {
    if (!activeDialog) return;
    const dialog = activeDialog;
    activeDialog = null;
    dialog.querySelectorAll('input').forEach(input => { input.value = ''; });
    dialog.replaceChildren();
    dialog.close();
    dialog.remove();
  }

  function dialog(title) {
    closeDialog();
    document.getElementById('account-menu').hidden = true;
    document.getElementById('account-toggle').setAttribute('aria-expanded', 'false');
    const node = document.createElement('dialog');
    node.className = 'identity-dialog';
    node.setAttribute('role', 'dialog');
    node.setAttribute('aria-labelledby', 'identity-dialog-title');
    node.innerHTML = `<div class="identity-dialog-head"><h2 id="identity-dialog-title">${e(title)}</h2><button type="button" class="identity-icon" title="关闭" aria-label="关闭">×</button></div><div class="identity-dialog-body"></div>`;
    document.body.append(node);
    activeDialog = node;
    node.querySelector('.identity-icon').onclick = closeDialog;
    node.addEventListener('cancel', event => { event.preventDefault(); closeDialog(); });
    node.showModal();
    return node;
  }

  function current(node) { return activeDialog === node && node.isConnected; }
  function contents(node, html) { node.querySelector('.identity-dialog-body').innerHTML = html; }
  const alertHtml = '<p class="identity-error" role="alert" hidden></p>';
  function identityTime(value) {
    return formatDisplayTime(typeof value === 'number' ? new Date(value * 1000).toISOString() : value);
  }
  const footer = label => `<div class="identity-form-actions"><button type="button" class="btn-sm" data-cancel>取消</button><button type="submit" class="btn-sm primary">${label}</button></div>`;

  // Each handler captures its own dialog and target; detached requests cannot act on a newer form.
  function bindForm(node, submit) {
    node.querySelector('[data-cancel]')?.addEventListener('click', closeDialog);
    const form = node.querySelector('form');
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!current(node) || form.dataset.busy === '1' || !form.reportValidity()) return;
      form.dataset.busy = '1';
      const submitButton = form.querySelector('[type="submit"]');
      submitButton.disabled = true;
      try { await submit(form); }
      catch (error) { if (current(node)) message(node, error.message || '操作失败，请重试'); }
      finally { form.dataset.busy = ''; if (current(node)) submitButton.disabled = false; }
    });
  }

  function roleNames(user, roles) {
    return roleIds(user).map(id => roles.find(role => role.id === id)?.name || id).join('、') || '未分配角色';
  }

  function scopeSummary(scope = {}, user = {}) {
    if (isSuper(user)) scope = Object.fromEntries(Object.keys(scopeLabels).map(key => [key, '*']));
    return Object.entries(scopeLabels).map(([key, label]) => `${label}：${scope[key] === '*' ? '全部' : `${Array.isArray(scope[key]) ? scope[key].length : 0} 项`}`).join(' / ');
  }

  async function refreshSelf() {
    try {
      const data = await request('/me');
      acceptAuthSession(data);
      applyAccessNavigation();
      if (currentAccessProfile?.must_change_password) showChangePassword(true);
    } catch (_) { /* A failed profile refresh cannot undo a successful save. */ }
  }

  async function changed(node, text) {
    if (!current(node)) return;
    closeDialog();
    showToast(text);
    await refreshSelf();
    if (activeWorkflow === 'identity') await showIdentityManagement();
  }

  async function showIdentityManagement(tab = selectedTab) {
    if (!hasPermission('auth.manage')) {
      document.getElementById('editor-area').innerHTML = '<div class="identity-center"><p role="alert">缺少 auth.manage 权限，请联系管理员。</p></div>';
      return;
    }
    selectedTab = Object.hasOwn(tabs, tab) ? tab : 'members';
    const version = ++pageVersion;
    const area = document.getElementById('editor-area');
    area.className = 'editor-area';
    area.innerHTML = `<section class="identity-center" id="identity-center"><div class="identity-heading"><h2>成员与权限</h2><button type="button" class="identity-icon" data-refresh aria-label="刷新成员与权限" title="刷新成员与权限">↻</button></div><div class="identity-tabs" role="tablist" aria-label="成员与权限">${Object.entries(tabs).map(([id, name]) => `<button type="button" role="tab" id="identity-tab-${id}" aria-controls="identity-panel" aria-selected="${id === selectedTab}" data-tab="${id}">${name}</button>`).join('')}</div><div role="tabpanel" aria-labelledby="identity-tab-${selectedTab}" id="identity-panel"><p class="identity-empty" role="status">正在加载...</p></div></section>`;
    const center = area.querySelector('#identity-center');
    if (location.protocol === 'http:' && !/^(localhost|127(?:\.\d{1,3}){3}|\[::1\])$/i.test(location.hostname) && !location.hostname.endsWith('.localhost')) {
      const warning = document.createElement('p');
      warning.className = 'identity-security-state';
      warning.setAttribute('role', 'status');
      warning.textContent = '当前连接未加密，请启用 HTTPS 后再分发成员密码';
      center.querySelector('.identity-heading').after(warning);
    }
    center.querySelector('[data-refresh]').onclick = () => showIdentityManagement();
    center.querySelectorAll('[data-tab]').forEach(node => node.onclick = () => { closeDialog(); showIdentityManagement(node.dataset.tab); });
    const panel = center.querySelector('#identity-panel');
    try {
      if (selectedTab === 'audit') {
        const data = await request('/audit');
        if (version !== pageVersion || !center.isConnected) return;
        renderAudit(panel, data.events || []);
      } else if (selectedTab === 'roles') {
        const [rolesData, catalog] = await Promise.all([request('/roles'), request('/permissions')]);
        if (version !== pageVersion || !center.isConnected) return;
        renderRoles(panel, rolesData.roles || [], catalog.permissions || []);
      } else {
        const [usersData, rolesData] = await Promise.all([request('/users'), request('/roles')]);
        if (version !== pageVersion || !center.isConnected) return;
        renderMembers(panel, usersData.users || [], rolesData.roles || [], selectedTab === 'scope');
      }
    } catch (error) {
      if (version !== pageVersion || !center.isConnected) return;
      panel.innerHTML = `${alertHtml}<button type="button" class="btn-sm" data-retry>重试</button>`;
      message(panel, error.message || '加载失败');
      panel.querySelector('[data-retry]').onclick = () => showIdentityManagement();
    }
  }

  function table(headers, rows, empty) {
    return rows ? `<div class="identity-table-scroll"><table class="identity-table"><thead><tr>${headers.map(header => `<th scope="col">${header}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>` : `<p class="identity-empty">${empty}</p>`;
  }

  function renderMembers(panel, users, roles, scopes) {
    const activeSuperCount = users.filter(user => isSuper(user) && user.status === 'active').length;
    panel.innerHTML = `<div class="identity-list-tools"><input type="search" aria-label="搜索成员" placeholder="搜索用户名或姓名">${scopes ? '' : button('新增成员', 'create', '', 'primary')}</div><div data-rows></div>`;
    const render = () => {
      const query = panel.querySelector('input').value.trim().toLocaleLowerCase();
      const rows = users.map((user, index) => {
        if (!`${user.username} ${user.display_name}`.toLocaleLowerCase().includes(query)) return '';
        const actions = scopes ? (isSuper(user) ? '<span class="identity-muted">全部数据</span>' : button('编辑范围', 'scope', index)) : [
          button('编辑', 'edit', index),
          button(user.status === 'active' ? '停用' : '启用', 'status', index, '', isSuper(user) && user.status === 'active' && activeSuperCount === 1 ? 'disabled title="不能停用最后一个有效超级管理员"' : ''),
          button(user.username === sessionStorage.getItem('user') ? '修改密码' : '重置密码', 'reset', index), button('撤销会话', 'revoke', index)
        ].join('');
        return `<tr><td><strong>${e(user.display_name || user.username)}</strong><span class="identity-secondary">${e(user.username)}</span></td><td>${e(roleNames(user, roles))}</td><td>${scopes ? e(scopeSummary(user.scope, user)) : `<span class="identity-status ${user.status === 'active' ? 'active' : ''}">${user.status === 'active' ? '启用' : '停用'}</span>${user.must_change_password ? '<span class="identity-secondary">待修改密码</span>' : ''}`}</td><td><div class="identity-row-actions">${actions}</div></td></tr>`;
      }).join('');
      panel.querySelector('[data-rows]').innerHTML = table(['成员', '角色', scopes ? '数据范围' : '状态', '操作'], rows, query ? '没有匹配的成员' : '暂无成员');
    };
    panel.querySelector('input').oninput = render;
    panel.onclick = event => {
      const control = event.target.closest('[data-action]');
      if (!control) return;
      const user = users[Number(control.dataset.index)];
      const action = control.dataset.action;
      if (action === 'create') openMember(null, roles);
      else if (action === 'edit') openMember(user, roles);
      else if (action === 'scope') openScope(user);
      else if (action === 'reset') resetPassword(user);
      else if (action === 'status') confirmAction(`${user.status === 'active' ? '停用' : '启用'}成员`, `成员：${user.display_name || user.username}（${user.username}）`, '确认', () => request(`/users/${encodeURIComponent(user.username)}`, { method: 'PUT', body: { status: user.status === 'active' ? 'disabled' : 'active' } }));
      else if (action === 'revoke') confirmAction('撤销成员会话', `成员：${user.display_name || user.username}（${user.username}）。该成员需要重新登录。`, '确认撤销', () => request(`/users/${encodeURIComponent(user.username)}/revoke-sessions`, { method: 'POST', body: {} }));
    };
    render();
  }

  function roleSelectors(roles, selected, immutable) {
    const choices = [...roles];
    selected.filter(id => !choices.some(role => role.id === id)).forEach(id => choices.push({ id, name: id }));
    return `<fieldset class="identity-fieldset"><legend>角色</legend><div class="identity-checks">${choices.map(role => `<label><input type="checkbox" name="role_ids" value="${e(role.id)}" ${selected.includes(role.id) ? 'checked' : ''} ${immutable ? 'disabled' : ''}>${e(role.name)}</label>`).join('')}</div></fieldset>`;
  }

  async function openMember(user, roles) {
    const node = dialog(user ? '编辑成员' : '新增成员');
    contents(node, '<p role="status">正在加载数据范围...</p><button type="button" class="btn-sm" data-cancel>取消</button>');
    node.querySelector('[data-cancel]').onclick = closeDialog;
    try {
      const options = user ? null : await request('/scope-options');
      if (!current(node)) return;
      contents(node, `<form><label class="identity-field">用户名<input name="username" required autocomplete="off" ${user ? 'readonly' : ''} value="${e(user?.username || '')}"></label><label class="identity-field">姓名<input name="display_name" required value="${e(user?.display_name || '')}"></label>${roleSelectors(roles, user ? roleIds(user) : ['tester'], user && isSuper(user))}${user ? '' : '<label class="identity-field">初始密码（留空自动生成）<input type="password" name="password" minlength="15" maxlength="128" autocomplete="new-password"></label>'}${user ? `<p class="identity-muted">${e(scopeSummary(user.scope, user))}</p>` : scopeFields(emptyScope(), options)}${alertHtml}${footer(user ? '保存成员' : '创建成员')}</form>`);
      if (!user) bindScopes(node);
      bindForm(node, async form => {
        const values = new FormData(form);
        const body = { display_name: values.get('display_name').trim(), role_ids: user && isSuper(user) ? roleIds(user) : values.getAll('role_ids') };
        if (!user) {
          body.username = values.get('username').trim();
          body.scope = readScopes(form);
          if (values.get('password')) body.password = values.get('password');
        }
        const data = await request(user ? `/users/${encodeURIComponent(user.username)}` : '/users', { method: user ? 'PUT' : 'POST', body });
        form.querySelectorAll('[type="password"]').forEach(input => { input.value = ''; });
        if (!current(node)) return;
        if (data.temporary_password) showTemporaryPassword(node, data.temporary_password);
        else await changed(node, user ? '成员已更新' : '成员已创建');
      });
    } catch (error) { if (current(node)) { contents(node, `${alertHtml}<button type="button" class="btn-sm" data-retry>重试</button>`); message(node, error.message); node.querySelector('[data-retry]').onclick = () => openMember(user, roles); } }
  }

  function scopeFields(scope = {}, options = {}) {
    return Object.entries(scopeLabels).map(([key, label]) => {
      const selected = Array.isArray(scope[key]) ? scope[key] : [];
      const items = [...(options[key] || [])];
      selected.filter(id => !items.some(item => String(item.id) === String(id))).forEach(id => items.push({ id, name: `已失效或不可见：${id}` }));
      return `<fieldset class="identity-fieldset" data-scope="${key}"><legend>${label}</legend><div class="identity-scope-mode"><label><input type="radio" name="mode_${key}" value="selected" ${scope[key] !== '*' ? 'checked' : ''}>指定范围</label><label><input type="radio" name="mode_${key}" value="all" ${scope[key] === '*' ? 'checked' : ''}>全部${label}</label></div><div class="identity-checks">${items.length ? items.map(item => {
        const project = key === 'api_environments' ? options.api_projects?.find(project => String(project.id) === String(item.project_id)) : null;
        return `<label><input type="checkbox" name="scope_${key}" value="${e(item.id)}" ${selected.includes(item.id) ? 'checked' : ''}>${e(item.name)}${project ? `（${e(project.name)}）` : ''}</label>`;
      }).join('') : '<span class="identity-muted">暂无可选数据</span>'}</div></fieldset>`;
    }).join('');
  }

  function bindScopes(node) {
    node.querySelectorAll('[data-scope]').forEach(fieldset => {
      const update = () => {
        const all = fieldset.querySelector('[value="all"]').checked;
        fieldset.querySelectorAll('[type="checkbox"]').forEach(input => { input.disabled = all; });
      };
      fieldset.querySelectorAll('[type="radio"]').forEach(input => input.onchange = update);
      update();
    });
  }

  function readScopes(form) {
    const values = new FormData(form);
    return Object.fromEntries(Object.keys(scopeLabels).map(key => [key, values.get(`mode_${key}`) === 'all' ? '*' : values.getAll(`scope_${key}`)]));
  }

  async function openScope(user) {
    const username = user.username;
    const scope = structuredClone(user.scope || emptyScope());
    const node = dialog(`数据授权 · ${user.display_name || username}`);
    contents(node, '<p role="status">正在加载数据范围...</p><button type="button" class="btn-sm" data-cancel>取消</button>');
    node.querySelector('[data-cancel]').onclick = closeDialog;
    try {
      const options = await request('/scope-options');
      if (!current(node)) return;
      contents(node, `<form>${scopeFields(scope, options)}<p class="identity-muted">API 环境仅在已授权项目内生效；生产执行还需生产环境权限。</p>${alertHtml}${footer('保存范围')}</form>`);
      bindScopes(node);
      bindForm(node, async form => {
        await request(`/users/${encodeURIComponent(username)}`, { method: 'PUT', body: { scope: readScopes(form) } });
        await changed(node, '数据范围已更新');
      });
    } catch (error) {
      if (current(node)) { contents(node, `${alertHtml}<button type="button" class="btn-sm" data-retry>重试</button>`); message(node, error.message); node.querySelector('[data-retry]').onclick = () => openScope(user); }
    }
  }

  function renderRoles(panel, roles, permissions) {
    const labels = new Map(permissions.map(permission => [permission.id, permission.label]));
    panel.innerHTML = `<div class="identity-list-tools">${button('新增角色', 'create', '', 'primary')}</div>` + table(['角色', '权限', '操作'], roles.map((role, index) => `<tr><td>${e(role.name)}${role.id === 'super_admin' ? '<span class="identity-secondary">内置，不可修改</span>' : ''}</td><td>${(role.permissions || []).map(id => `<span title="${e(id)}">${e(labels.get(id) || id)}</span>`).join('、') || '未配置权限'}</td><td><div class="identity-row-actions">${button('复制', 'copy', index)}${role.id === 'super_admin' ? '' : button('编辑', 'edit', index) + button('删除', 'delete', index)}</div></td></tr>`).join(''), '暂无角色');
    panel.onclick = event => {
      const control = event.target.closest('[data-action]');
      if (!control) return;
      const role = roles[Number(control.dataset.index)];
      if (control.dataset.action === 'create') openRole();
      else if (control.dataset.action === 'edit') openRole(role);
      else if (control.dataset.action === 'copy') openRole({ ...role, id: '', name: role.name + ' 副本' });
      else if (control.dataset.action === 'delete') confirmAction('删除角色', `角色：${role.name}。正在使用的角色不能删除。`, '确认删除', () => request(`/roles/${encodeURIComponent(role.id)}`, { method: 'DELETE' }));
    };
  }

  async function openRole(role = {}) {
    if (role.id === 'super_admin') return;
    const node = dialog(role.id ? '编辑角色' : '新增角色');
    contents(node, '<p role="status">正在加载权限...</p>');
    try {
      const data = await request('/permissions');
      if (!current(node)) return;
      const groups = new Map();
      (data.permissions || []).forEach(permission => {
        const group = permission.group || '其他';
        if (!groups.has(group)) groups.set(group, []);
        groups.get(group).push(permission);
      });
      contents(node, `<form><label class="identity-field">角色名称<input name="name" required value="${e(role.name || '')}"></label>${Array.from(groups, ([group, permissions]) => `<fieldset class="identity-fieldset"><legend>${e(group)}</legend><div class="identity-checks">${permissions.map(permission => `<label><input type="checkbox" name="permissions" value="${e(permission.id)}" ${(role.permissions || []).includes(permission.id) ? 'checked' : ''}>${e(permission.label)}</label>`).join('')}</div></fieldset>`).join('')}${alertHtml}${footer('保存角色')}</form>`);
      bindForm(node, async form => {
        const values = new FormData(form);
        await request(role.id ? `/roles/${encodeURIComponent(role.id)}` : '/roles', { method: role.id ? 'PUT' : 'POST', body: { name: values.get('name').trim(), permissions: values.getAll('permissions') } });
        await changed(node, '角色已保存');
      });
    } catch (error) { if (current(node)) { contents(node, `${alertHtml}<button type="button" class="btn-sm" data-retry>重试</button>`); message(node, error.message); node.querySelector('[data-retry]').onclick = () => openRole(role); } }
  }

  function confirmAction(title, description, label, action, after) {
    const node = dialog(title);
    contents(node, `<form><p>${e(description)}</p>${alertHtml}${footer(label)}</form>`);
    bindForm(node, async () => {
      await action();
      if (!current(node)) return;
      if (after) { closeDialog(); after(); }
      else await changed(node, '操作已完成');
    });
  }

  function showTemporaryPassword(node, password) {
    if (!current(node)) return;
    contents(node, '<p>临时密码仅显示本次，关闭后不可再次查看。成员首次登录需修改密码。</p><output class="identity-secret"></output>');
    node.querySelector('output').textContent = password;
    if (activeWorkflow === 'identity') showIdentityManagement();
  }

  function resetPassword(user) {
    if (user.username === sessionStorage.getItem('user')) { showChangePassword(); return; }
    const node = dialog(`重置密码 · ${user.display_name || user.username}`);
    contents(node, `<form><p>现有会话将失效，下次登录需修改密码。</p><label class="identity-field">新临时密码（留空自动生成）<input type="password" name="password" minlength="15" maxlength="128" autocomplete="new-password"></label>${alertHtml}${footer('确认重置')}</form>`);
    bindForm(node, async form => {
      const password = new FormData(form).get('password');
      const data = await request(`/users/${encodeURIComponent(user.username)}/reset-password`, { method: 'POST', body: password ? { password } : {} });
      form.querySelector('input').value = '';
      if (!current(node)) return;
      if (data.temporary_password) showTemporaryPassword(node, data.temporary_password);
      else await changed(node, '密码已重置');
    });
  }

  function renderAudit(panel, events) {
    const actions = { 'user.create': '新增成员', 'user.update': '更新成员', 'user.reset_password': '重置密码', 'user.revoke_sessions': '撤销成员会话', 'role.create': '新增角色', 'role.update': '更新角色', 'role.delete': '删除角色', 'password.change': '修改密码', 'user.change_password': '修改密码', 'session.revoke': '撤销会话', 'session.revoke_all': '撤销全部会话', 'session.logout': '退出登录', 'login.success': '登录成功', 'login.failure': '登录失败', 'access.denied': '访问被拒绝', 'operation.result': '操作结果', login: '登录', logout: '退出登录' };
    // Only explicit safe columns are rendered. Never stringify arbitrary audit detail.
    panel.innerHTML = table(['时间', '操作者', '操作', '对象'], events.map(event => `<tr><td>${e(identityTime(event.created_at || event.timestamp))}</td><td>${e(event.actor || event.username || '-')}</td><td>${e(actions[event.action] || event.action || '-')}</td><td>${e(event.target || '-')}</td></tr>`).join(''), '暂无操作记录');
  }

  async function showPersonalAccount() {
    const node = dialog('个人资料与会话');
    contents(node, '<p role="status">正在加载...</p>');
    try {
      const [data, sessions] = await Promise.all([request('/me'), request('/sessions')]);
      if (!current(node)) return;
      acceptAuthSession(data);
      const profile = currentAccessProfile || { username: data.user };
      const roleLabels = { super_admin: '超级管理员', test_manager: '测试负责人', tester: '测试成员', viewer: '只读成员' };
      const names = Array.isArray(profile.role_names) ? profile.role_names : roleIds(profile).map(id => roleLabels[id] || id);
      contents(node, `<dl class="identity-profile"><dt>用户名</dt><dd>${e(profile.username)}</dd><dt>姓名</dt><dd>${e(profile.display_name || '-')}</dd><dt>角色</dt><dd>${e(names.join('、') || '未分配')}</dd><dt>数据范围</dt><dd>${e(scopeSummary(profile.scope, profile))}</dd></dl><h3>登录会话</h3>${table(['创建时间', '到期时间', '状态'], (sessions.sessions || []).map(session => `<tr><td>${e(identityTime(session.created_at))}</td><td>${e(identityTime(session.expires_at))}</td><td>${session.is_current || session.current ? '当前会话' : session.revoked_at ? '已撤销' : '有效'}</td></tr>`).join(''), '暂无会话')}<div class="identity-form-actions"><button class="btn-sm" type="button" data-password>修改密码</button><button class="btn-sm" type="button" data-revoke>撤销全部会话</button></div>`);
      node.querySelector('[data-password]').onclick = () => showChangePassword();
      node.querySelector('[data-revoke]').onclick = () => confirmAction('撤销全部会话', '包括当前会话，所有设备需要重新登录。', '确认撤销', () => request('/revoke-sessions', { method: 'POST', body: {} }), clearAuthSession);
    } catch (error) { if (current(node)) { contents(node, `${alertHtml}<button type="button" class="btn-sm" data-retry>重试</button>`); message(node, error.message); node.querySelector('[data-retry]').onclick = showPersonalAccount; } }
  }

  function showChangePassword(required = false) {
    required = required || currentAccessProfile?.must_change_password === true;
    closeDialog();
    let node;
    if (required) {
      node = document.getElementById('password-gate');
      if (!node.hidden && node.querySelector('form')) return;
      document.getElementById('app').style.display = 'none';
      document.getElementById('login-screen').style.display = 'none';
      node.hidden = false;
      node.innerHTML = '<section class="identity-password"><h2>修改密码</h2><p>当前账号需修改密码后继续。</p><div class="identity-password-body"></div></section>';
    } else node = dialog('修改密码');
    const host = node.querySelector(required ? '.identity-password-body' : '.identity-dialog-body');
    host.innerHTML = `<form><label class="identity-field">当前密码<input name="current_password" type="password" required autocomplete="current-password"></label><label class="identity-field">新密码<input name="new_password" type="password" required minlength="15" maxlength="128" autocomplete="new-password"></label><label class="identity-field">确认新密码<input name="confirm_password" type="password" required minlength="15" maxlength="128" autocomplete="new-password"></label><p class="identity-muted">新密码需 15–128 个字符，修改后其他会话失效。</p>${alertHtml}<div class="identity-form-actions"><button type="button" class="btn-sm" data-cancel>${required ? '退出登录' : '取消'}</button><button type="submit" class="btn-sm primary">保存密码</button></div></form>`;
    node.querySelector('[data-cancel]').onclick = required ? doLogout : closeDialog;
    const form = node.querySelector('form');
    form.onsubmit = async event => {
      event.preventDefault();
      if (form.dataset.busy === '1' || !form.reportValidity()) return;
      const values = new FormData(form);
      if (values.get('new_password') !== values.get('confirm_password')) { message(node, '两次输入的新密码不一致'); return; }
      form.dataset.busy = '1';
      form.querySelector('[type="submit"]').disabled = true;
      const token = sessionToken();
      try {
        const data = await request('/change-password', { method: 'POST', body: { current_password: values.get('current_password'), new_password: values.get('new_password') }, skipAuthRedirect: true });
        if (!data.token || !data.profile || data.profile.must_change_password) throw new Error('密码已提交，但未取得更新会话，请重新登录确认');
        if (!form.isConnected || sessionToken() !== token) return;
        acceptAuthSession(data);
        form.querySelectorAll('input').forEach(input => { input.value = ''; });
        if (required) { node.replaceChildren(); node.hidden = true; continueAfterAuthentication(); }
        else { closeDialog(); applyAccessNavigation(); showToast('密码已修改，其他会话已失效'); }
      } catch (error) { if (form.isConnected) message(node, error.message || '修改失败'); }
      finally { if (form.isConnected) { form.dataset.busy = ''; form.querySelector('[type="submit"]').disabled = false; } }
    };
  }

  window.showIdentityManagement = showIdentityManagement;
  window.closeIdentityDialog = closeDialog;
  window.showPersonalAccount = showPersonalAccount;
  window.showChangePassword = showChangePassword;
  window.toggleAccountMenu = () => {
    const menu = document.getElementById('account-menu');
    menu.hidden = !menu.hidden;
    document.getElementById('account-toggle').setAttribute('aria-expanded', String(!menu.hidden));
  };
  document.addEventListener('click', event => {
    if (event.target.closest('.account-control')) return;
    document.getElementById('account-menu').hidden = true;
    document.getElementById('account-toggle').setAttribute('aria-expanded', 'false');
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') { document.getElementById('account-menu').hidden = true; document.getElementById('account-toggle').setAttribute('aria-expanded', 'false'); }
  });
})();
