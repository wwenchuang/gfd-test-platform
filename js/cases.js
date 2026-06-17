// cases.js
// Extracted from task-manager.html (no logic changes).

// ===== MODULES =====
async function loadModules(options = {}) {
  const force = options && options.force;
  // round 4: 同一会话只首次拉取，后续切换页面直接渲染，避免反复请求 /modules
  if (!force && AppState.loaded.modules && modulesLoaded) {
    if (activeWorkflow === 'dashboard' && !hasOpenEditor()) showWorkflowGuide('dashboard');
    renderModules();
    return;
  }
  if (AppState.loading.modules) return AppState.loading.modules;
  modulesLoaded = false;
  if (activeWorkflow === 'dashboard' && !hasOpenEditor()) showWorkflowGuide('dashboard');
  const task = (async () => {
    try {
      const [moduleData, appData, metaData, sonicData] = await Promise.all([
        apiRequest('/modules'),
        apiRequest('/task-apps').catch(() => null),
        apiRequest('/task-meta').catch(() => null),
        apiRequest('/sonic/cases').catch(() => null)
      ]);
      modules = moduleData || {};
      if (appData) {
        taskApps = appData.apps || [];
      }
      if (metaData) {
        taskMeta = metaData.meta || {};
      }
      if (sonicData) {
        sonicCaseRows = sonicData.cases || [];
      }
      modulesLoaded = true;
      AppState.loaded.modules = true;
      renderModules();
      warmupYamlStats().catch(() => {});
      if (activeWorkflow === 'dashboard' && !hasOpenEditor()) showWorkflowGuide('dashboard');
    } catch(e) {
      // 离线演示模式
      modules = {
        '文档打印': ['文字速印.yaml', '图片打印.yaml'],
        '首页': ['首页导航.yaml', '首页弹窗.yaml'],
        '用户中心': ['登录.yaml']
      };
      modulesLoaded = true;
      AppState.loaded.modules = true;
      renderModules();
      warmupYamlStats().catch(() => {});
      if (activeWorkflow === 'dashboard' && !hasOpenEditor()) showWorkflowGuide('dashboard');
      showToast(`⚠ 模块接口不可用，已切换演示数据：${e.message || e}`, 'error');
    }
  })();
  AppState.loading.modules = task;
  try {
    await task;
  } finally {
    AppState.loading.modules = null;
  }
}

// round 4: 在进入用例/执行/Agent 等需要模块树的页面时调用，避免重复请求
function ensureModulesLoaded(options = {}) {
  if (AppState.loaded.modules || AppState.loading.modules) return AppState.loading.modules || Promise.resolve();
  return loadModules(options);
}

function moduleApp(mod) {
  return taskApps.find(app => (app.modules || []).includes(mod));
}

function appInfoByPackage(packageName) {
  packageName = (packageName || '').trim();
  if (!packageName) return null;
  const taskApp = taskApps.find(app => app.package === packageName) || {};
  const knowledgeApp = knowledgeAppDetails.find(app => app.package === packageName) || {};
  if (!taskApp.package && !knowledgeApp.package) return null;
  return {
    ...knowledgeApp,
    ...taskApp,
    package: packageName,
    name: taskApp.name || knowledgeApp.name || packageName,
    modules: taskApp.modules || knowledgeApp.modules || [],
    page_count: knowledgeApp.page_count ?? taskApp.page_count ?? 0,
    has_knowledge: knowledgeApp.has_knowledge ?? false,
    source: [taskApp.package ? 'task-apps' : '', knowledgeApp.package ? 'knowledge' : ''].filter(Boolean).join('+')
  };
}

function appDisplayName(packageName) {
  const app = appInfoByPackage(packageName);
  return app?.name || packageName || '未命名应用';
}

function appDisplayLabel(packageName) {
  const app = appInfoByPackage(packageName);
  if (!packageName) return '未选择应用';
  const name = app?.name || packageName;
  return name === packageName ? packageName : `${name} / ${packageName}`;
}

function moduleAppPackage(mod) {
  return moduleApp(mod)?.package || '';
}

function currentModuleAppPackage() {
  return moduleAppPackage(currentModule) || document.getElementById('generate-app-package')?.value.trim() || 'com.kfb.model';
}

function metaKey(mod, file) {
  return `${mod}::${file}`;
}

function fileMeta(mod, file) {
  return taskMeta[metaKey(mod, file)] || {};
}

