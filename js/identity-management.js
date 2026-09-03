// Local identity UI. Permission and scope decisions remain authoritative on the server.
(() => {
  const tabs = { members: '成员', roles: '角色', scope: '数据授权', audit: '操作记录' };
  const scopeLabels = { ui_apps: 'UI 应用', api_projects: 'API 项目', api_environments: 'API 环境' };
  const permissionPrerequisites = {
    'ui.edit': ['ui.view'], 'ui.execute': ['ui.view'], 'ui.delete': ['ui.view'], 'ui.baseline': ['ui.view'],
    'api.edit': ['api.view'], 'api.execute': ['api.view'], 'api.delete': ['api.view'],
    'api.baseline': ['api.view'], 'api.environment': ['api.view'], 'api.production': ['api.view', 'api.execute'],
    'api.loadtest.view': ['api.view'], 'api.loadtest.edit': ['api.view', 'api.loadtest.view'],
    'api.loadtest.execute': ['api.view', 'api.execute', 'api.loadtest.view'],
    'api.loadtest.manage_agents': ['api.view', 'api.loadtest.view'],
  };
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
        const protectsLastSuper = isSuper(user) && user.status === 'active' && activeSuperCount === 1;
        const actions = scopes ? (isSuper(user) ? '<span class="identity-muted">全部数据</span>' : button('编辑范围', 'scope', index)) : [
          button('编辑', 'edit', index),
          button(user.status === 'active' ? '停用' : '启用', 'status', index, '', protectsLastSuper ? 'disabled title="不能停用最后一个有效超级管理员"' : ''),
          button(user.username === sessionStorage.getItem('user') ? '修改密码' : '重置密码', 'reset', index), button('撤销会话', 'revoke', index)
        ].join('') + (protectsLastSuper ? '<span class="identity-action-note">需保留至少 1 名有效超级管理员</span>' : '');
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
    return `<fieldset class="identity-fieldset" data-role-selector><legend>角色</legend><div class="identity-checks">${choices.map(role => {
      const permissions = Array.isArray(role.permissions) ? role.permissions : [];
      const readOnly = role.id === 'viewer' || (permissions.length > 0 && permissions.every(id => id === 'ui.view' || id === 'api.view'));
      return `<label><input type="checkbox" name="role_ids" value="${e(role.id)}" data-role-kind="${role.id === 'super_admin' ? 'super' : readOnly ? 'readonly' : 'write'}" ${selected.includes(role.id) ? 'checked' : ''} ${immutable ? 'disabled' : ''}>${e(role.name)}</label>`;
    }).join('')}</div>${immutable ? '' : '<p class="identity-muted">超级管理员和只读角色为独立身份；选择它们会自动清除冲突角色。负责人和测试成员可以组合。</p>'}</fieldset>`;
  }

  function bindRoleSelectors(node) {
    const fieldset = node.querySelector('[data-role-selector]');
    if (!fieldset) return;
    const boxes = Array.from(fieldset.querySelectorAll('[name="role_ids"]'));
    boxes.forEach(input => input.addEventListener('change', () => {
      if (!input.checked) return;
      const kind = input.dataset.roleKind;
      boxes.forEach(other => {
        if (other === input) return;
        if (kind === 'super' || kind === 'readonly' || other.dataset.roleKind === 'super' || other.dataset.roleKind === 'readonly') other.checked = false;
      });
    }));
  }

  async function openMember(user, roles) {
    const node = dialog(user ? '编辑成员' : '新增成员');
    contents(node, '<p role="status">正在加载数据范围...</p><button type="button" class="btn-sm" data-cancel>取消</button>');
    node.querySelector('[data-cancel]').onclick = closeDialog;
    try {
      const options = user ? null : await request('/scope-options');
      if (!current(node)) return;
      contents(node, `<form><label class="identity-field">用户名<input name="username" required autocomplete="off" ${user ? 'readonly' : ''} value="${e(user?.username || '')}"></label><label class="identity-field">姓名<input name="display_name" required value="${e(user?.display_name || '')}"></label>${roleSelectors(roles, user ? roleIds(user) : ['tester'], user && isSuper(user))}${user ? '' : '<label class="identity-field">初始密码（留空自动生成）<input type="password" name="password" minlength="15" maxlength="128" autocomplete="new-password"></label>'}${user ? `<p class="identity-muted">${e(scopeSummary(user.scope, user))}</p>` : scopeFields(emptyScope(), options)}${alertHtml}${footer(user ? '保存成员' : '创建成员')}</form>`);
      bindRoleSelectors(node);
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
      return `<fieldset class="identity-fieldset" data-scope="${key}"><legend>${label}</legend><div class="identity-scope-mode"><label><input type="radio" name="mode_${key}" value="selected" ${scope[key] !== '*' ? 'checked' : ''}>指定范围</label><label><input type="radio" name="mode_${key}" value="all" ${scope[key] === '*' ? 'checked' : ''}>全部${label}（含今后新增）</label></div><div class="identity-checks">${items.length ? items.map(item => {
        const project = key === 'api_environments' ? options.api_projects?.find(project => String(project.id) === String(item.project_id)) : null;
        return `<label><input type="checkbox" name="scope_${key}" value="${e(item.id)}" ${selected.includes(item.id) ? 'checked' : ''}>${e(item.name)}${project ? `（${e(project.name)}）` : ''}</label>`;
      }).join('') : '<span class="identity-muted">暂无可选数据</span>'}</div><p class="identity-muted identity-scope-hint">指定范围未勾选任何项目时，该类数据不可见。</p></fieldset>`;
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
    const catalog = new Map(permissions.map(permission => [permission.id, permission]));
    const summary = role => {
      const groups = new Map();
      (role.permissions || []).forEach(id => {
        const permission = catalog.get(id) || { id, label: id, group: '其他' };
        if (!groups.has(permission.group)) groups.set(permission.group, []);
        groups.get(permission.group).push(permission);
      });
      if (!groups.size) return '<span class="identity-muted">未配置权限</span>';
      return `<div class="identity-permission-summary">${Array.from(groups, ([group, items]) => `<div><strong>${e(group)}</strong><span>${items.map(item => `<em title="${e(item.id)}">${e(item.label)}</em>`).join('')}</span></div>`).join('')}</div>`;
    };
    panel.innerHTML = `<div class="identity-list-tools">${button('新增角色', 'create', '', 'primary')}</div>` + table(['角色', '权限', '操作'], roles.map((role, index) => `<tr><td>${e(role.name)}${role.id === 'super_admin' ? '<span class="identity-secondary">内置，不可修改</span>' : ''}</td><td>${summary(role)}</td><td><div class="identity-row-actions">${button('复制', 'copy', index)}${role.id === 'super_admin' ? '' : button('编辑', 'edit', index) + button('删除', 'delete', index)}</div></td></tr>`).join(''), '暂无角色');
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
      contents(node, `<form><label class="identity-field">角色名称<input name="name" required value="${e(role.name || '')}"></label><p class="identity-muted">编辑、执行、删除、基线和环境管理会自动补齐对应的查看权限，避免成员有操作权却看不到入口。</p>${Array.from(groups, ([group, permissions]) => `<fieldset class="identity-fieldset" data-permission-group><legend>${e(group)}</legend><div class="identity-permission-group-actions"><button type="button" class="btn-sm" data-permission-all>全选本组</button><button type="button" class="btn-sm" data-permission-clear>清空本组</button></div><div class="identity-checks">${permissions.map(permission => `<label><input type="checkbox" name="permissions" value="${e(permission.id)}" ${(role.permissions || []).includes(permission.id) ? 'checked' : ''}>${e(permission.label)}</label>`).join('')}</div></fieldset>`).join('')}${alertHtml}${footer('保存角色')}</form>`);
      bindPermissionSelectors(node);
      bindForm(node, async form => {
        const values = new FormData(form);
        await request(role.id ? `/roles/${encodeURIComponent(role.id)}` : '/roles', { method: role.id ? 'PUT' : 'POST', body: { name: values.get('name').trim(), permissions: values.getAll('permissions') } });
        await changed(node, '角色已保存');
      });
    } catch (error) { if (current(node)) { contents(node, `${alertHtml}<button type="button" class="btn-sm" data-retry>重试</button>`); message(node, error.message); node.querySelector('[data-retry]').onclick = () => openRole(role); } }
  }

  function bindPermissionSelectors(node) {
    const form = node.querySelector('form');
    const input = id => form.querySelector(`[name="permissions"][value="${CSS.escape(id)}"]`);
    const applyPrerequisites = changed => {
      if (!changed.checked) {
        const stillRequired = Array.from(form.querySelectorAll('[name="permissions"]:checked')).some(item => (permissionPrerequisites[item.value] || []).includes(changed.value));
        if (stillRequired) changed.checked = true;
        return;
      }
      (permissionPrerequisites[changed.value] || []).forEach(id => { const dependency = input(id); if (dependency) dependency.checked = true; });
    };
    form.querySelectorAll('[name="permissions"]').forEach(control => control.addEventListener('change', () => applyPrerequisites(control)));
    form.querySelectorAll('[data-permission-group]').forEach(fieldset => {
      const controls = Array.from(fieldset.querySelectorAll('[name="permissions"]'));
      fieldset.querySelector('[data-permission-all]').onclick = () => {
        controls.forEach(control => { control.checked = true; applyPrerequisites(control); });
      };
      fieldset.querySelector('[data-permission-clear]').onclick = () => {
        controls.forEach(control => { control.checked = false; });
        Array.from(form.querySelectorAll('[name="permissions"]:checked')).forEach(applyPrerequisites);
      };
    });
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

  function auditOperationPresentation(event) {
    const actions = { 'user.create': '新增成员', 'user.update': '更新成员', 'user.reset_password': '重置密码', 'user.revoke_sessions': '撤销成员会话', 'role.create': '新增角色', 'role.update': '更新角色', 'role.delete': '删除角色', 'password.change': '修改密码', 'user.change_password': '修改密码', 'session.revoke': '撤销会话', 'session.revoke_all': '撤销全部会话', 'session.logout': '退出登录', 'login.success': '登录成功', 'login.failure': '登录失败', 'access.denied': '访问被拒绝', login: '登录', logout: '退出登录' };
    const path = String(event.target || '');
    const details = event.details && typeof event.details === 'object' ? event.details : {};
    const method = String(details.method || '').toUpperCase();
    let action = actions[event.action] || event.action || '-';
    let target = path || '-';
    if (event.action === 'operation.result') {
      const routes = [
        [/^\/api\/api-testing\/v1\/scheduled-jobs\/[^/]+\/run$/, '手动执行定时任务', 'API 定时任务'],
        [/^\/api\/api-testing\/v1\/scheduled-jobs(?:\/|$)/, method === 'DELETE' ? '删除 API 定时任务' : '保存 API 定时任务', 'API 定时任务'],
        [/^\/api\/api-testing\/v1\/workspace$/, '保存 API 工作台', 'API 工作台'],
        [/^\/api\/api-testing\/v1\/providers\/apifox\/context$/, '读取 Apifox 上下文', '接口同步'],
        [/^\/api\/api-testing\/v1\/providers\/apifox\/projects$/, '读取 Apifox 项目', '接口同步'],
        [/^\/api\/api-testing\/v1\/sources\/apifox\/preview$/, '预览 Apifox 同步', '接口同步'],
        [/^\/api\/api-testing\/v1\/sources\/apifox\/[^/]+\/activate$/, '启用接口来源版本', '接口版本'],
        [/^\/api\/api-testing\/v1\/projects(?:\/[^/]+)?$/, '保存 API 项目', 'API 项目'],
        [/^\/api\/api-testing\/v1\/executions\/[^/]+\/rerun$/, '重跑 API 执行', 'API 执行'],
        [/^\/api\/api-testing\/v1\/executions\/restore$/, '恢复 API 执行记录', 'API 执行记录'],
        [/^\/api\/api-testing\/v1\/executions$/, '创建 API 执行', 'API 执行'],
        [/^\/api\/api-testing\/v1\/baselines\/bulk-group$/, '批量更新 API 基线分组', 'API 基线'],
        [/^\/api\/api-testing\/v1\/case-versions\/[^/]+\/baseline$/, '采纳 API 基线', 'API 基线'],
        [/^\/api\/api-testing\/v1\/case-versions\/[^/]+\/group$/, '更新 API 用例分组', 'API 用例'],
        [/^\/api\/api-testing\/v1\/case-versions\/[^/]+\/validate$/, '校验 API 用例版本', 'API 用例'],
        [/^\/api\/api-testing\/v1\/cases\/[^/]+\/versions$/, '保存 API 用例版本', 'API 用例'],
        [/^\/api\/api-testing\/v1\/cases\/basic-positive\/preview$/, '预览 API 基础正向用例', 'API 用例生成'],
        [/^\/api\/api-testing\/v1\/workflow-steps\/preview$/, '预览 API 用例工作流', 'API 用例编排'],
        [/^\/api\/api-testing\/v1\/cases$/, method === 'POST' ? '新建 API 用例' : '保存 API 用例', 'API 用例'],
        [/^\/api\/api-testing\/v1\/tasks\/[^/]+\/run$/, '执行 API 任务', 'API 任务'],
        [/^\/api\/api-testing\/v1\/tasks\/[^/]+\/name$/, '更新 API 任务名称', 'API 任务'],
        [/^\/api\/api-testing\/v1\/tasks\/[^/]+$/, '保存 API 任务', 'API 任务'],
        [/^\/api\/api-testing\/v1\/tasks$/, method === 'POST' ? '新建 API 任务' : '保存 API 任务', 'API 任务'],
        [/^\/api\/test-reports\/preview$/, '预览 UI 测试报告', 'UI 测试报告'],
        [/^\/api\/sonic\/refresh-bridges$/, '刷新 Sonic 托管桥接', 'Sonic 托管脚本'],
        [/^\/api\/sonic\/scan-legacy$/, '扫描 Sonic 旧步骤', 'Sonic 维护'],
        [/^\/api\/sonic\/diagnose$/, '诊断 Sonic 连接', 'Sonic 配置'],
        [/^\/api\/jobs\/[^/]+\/analyze-failure$/, '分析 UI 失败任务', 'UI 失败任务'],
        [/^\/api\/feishu-drafts(?:\/|$)/, '生成缺陷草稿', '缺陷草稿'],
        [/^\/api\/task-app(?:\/|$)/, '保存应用配置', '应用配置'],
        [/^\/api\/module$/, method === 'DELETE' ? '删除 YAML 模块' : '保存 YAML 模块', 'YAML 模块'],
      ];
      const matched = routes.find(([pattern]) => pattern.test(path));
      action = matched?.[1] || `${method || '提交'}平台操作`;
      target = matched?.[2] || '平台接口';
    }
    let kind = 'success';
    let result = '已完成';
    if (event.action === 'access.denied' || details.outcome === 'denied') {
      kind = 'denied'; result = '已拒绝';
    } else if (event.action === 'login.failure' || details.outcome === 'failure' || details.ok === false || Number(details.status || 0) >= 400) {
      kind = 'failure'; result = '失败';
    } else if (details.ok === true) result = '成功';
    const facts = [method, details.status].filter(value => value !== '' && value !== undefined && value !== null);
    if (facts.length) result += ` · ${facts.join(' ')}`;
    return { event, action, target, rawTarget: path, kind, result };
  }

  function renderAudit(panel, events) {
    const items = events.map(auditOperationPresentation);
    let page = 1;
    const pageSize = 20;
    panel.innerHTML = `<div class="identity-list-tools identity-audit-tools"><input type="search" aria-label="搜索操作记录" placeholder="搜索操作者、操作或对象"><select aria-label="筛选操作结果"><option value="all">全部结果</option><option value="success">成功</option><option value="failure">失败</option><option value="denied">已拒绝</option></select><span class="identity-muted" data-audit-count></span></div><div data-audit-rows></div><div class="identity-audit-pagination"><button type="button" class="btn-sm" data-audit-prev>上一页</button><span class="identity-muted" data-audit-page></span><button type="button" class="btn-sm" data-audit-next>下一页</button></div>`;
    const search = panel.querySelector('input');
    const status = panel.querySelector('select');
    const render = () => {
      const query = search.value.trim().toLocaleLowerCase();
      const filtered = items.filter(item => (status.value === 'all' || item.kind === status.value) && `${item.event.actor || item.event.username || ''} ${item.action} ${item.target} ${item.rawTarget}`.toLocaleLowerCase().includes(query));
      const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
      page = Math.min(page, pages);
      const visible = filtered.slice((page - 1) * pageSize, page * pageSize);
      const rows = visible.map(item => `<tr><td>${e(identityTime(item.event.created_at || item.event.timestamp))}</td><td>${e(item.event.actor || item.event.username || '-')}</td><td>${e(item.action)}</td><td>${e(item.target)}${item.rawTarget && item.rawTarget !== item.target ? `<span class="identity-secondary" title="原始接口">${e(item.rawTarget)}</span>` : ''}</td><td><span class="identity-audit-result ${e(item.kind)}">${e(item.result)}</span></td></tr>`).join('');
      panel.querySelector('[data-audit-rows]').innerHTML = table(['时间', '操作者', '操作', '对象', '结果'], rows, query || status.value !== 'all' ? '没有匹配的操作记录' : '暂无操作记录');
      panel.querySelector('[data-audit-count]').textContent = `${filtered.length}/${items.length} 条`;
      panel.querySelector('[data-audit-page]').textContent = `第 ${page}/${pages} 页`;
      panel.querySelector('[data-audit-prev]').disabled = page <= 1;
      panel.querySelector('[data-audit-next]').disabled = page >= pages;
    };
    search.oninput = () => { page = 1; render(); };
    status.onchange = () => { page = 1; render(); };
    panel.querySelector('[data-audit-prev]').onclick = () => { page--; render(); };
    panel.querySelector('[data-audit-next]').onclick = () => { page++; render(); };
    render();
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
      const sessionItems = sessions.sessions || [];
      const sessionRows = sessionItems.map((session, index) => {
        const currentSession = session.is_current || session.current;
        return `<tr><td>${e(identityTime(session.created_at))}</td><td>${e(identityTime(session.expires_at))}</td><td>${currentSession ? '当前会话' : session.revoked_at ? '已撤销' : '有效'}</td><td>${currentSession || session.revoked_at ? '<span class="identity-muted">—</span>' : `<button class="btn-sm" type="button" data-revoke-session="${index}">撤销此会话</button>`}</td></tr>`;
      }).join('');
      contents(node, `<dl class="identity-profile"><dt>用户名</dt><dd>${e(profile.username)}</dd><dt>姓名</dt><dd>${e(profile.display_name || '-')}</dd><dt>角色</dt><dd>${e(names.join('、') || '未分配')}</dd><dt>数据范围</dt><dd>${e(scopeSummary(profile.scope, profile))}</dd></dl><h3>登录会话 · ${sessionItems.length} 个</h3><p class="identity-muted">可单独撤销不认识的旧会话；“撤销全部会话”会同时退出当前设备。</p>${table(['创建时间', '到期时间', '状态', '操作'], sessionRows, '暂无会话')}<div class="identity-form-actions"><button class="btn-sm" type="button" data-password>修改密码</button><button class="btn-sm" type="button" data-revoke>撤销全部会话</button></div>`);
      node.querySelector('[data-password]').onclick = () => showChangePassword();
      node.querySelector('[data-revoke]').onclick = () => confirmAction('撤销全部会话', '包括当前会话，所有设备需要重新登录。', '确认撤销', () => request('/revoke-sessions', { method: 'POST', body: {} }), clearAuthSession);
      node.querySelectorAll('[data-revoke-session]').forEach(control => {
        const session = sessionItems[Number(control.dataset.revokeSession)];
        control.onclick = () => confirmAction('撤销登录会话', `创建于 ${identityTime(session.created_at)}，撤销后该设备需要重新登录。`, '确认撤销', () => request('/sessions/revoke', { method: 'POST', body: { session_id: session.id } }), showPersonalAccount);
      });
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
