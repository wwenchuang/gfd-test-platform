// model-config.js
// Extracted from task-manager.html (no logic changes).


function providerStatusText(provider) {
  return provider?.configured ? '已配置' : '未配置';
}

function providerDisplayText(provider) {
  if (!provider) return '未选择模型';
  const tempLabel = provider.temperatureLocked ? ' · 参数策略：平台固定' : ' · 参数策略：可自定义';
  const source = provider.catalogSource === 'live'
    ? ' · 实时目录'
    : provider.catalogSource === 'configured_fallback'
      ? ' · 目录降级'
      : ' · 独立配置';
  return `${provider.name || provider.id} · ${provider.model || ''}${source} · ${providerStatusText(provider)}${tempLabel}`;
}

function modelProviderOptions(selectedId='') {
  return aiProviders.map(provider => `
    <option value="${escapeHtml(provider.id)}" ${provider.id === selectedId ? 'selected' : ''}>
      ${escapeHtml(providerDisplayText(provider))}
    </option>
  `).join('');
}

async function loadAiModelConfig(options = {}) {
  const force = options && options.force;
  // round 4: 模型配置只在进入“配置→模型配置”页时按需加载
  if (!force && AppState.loaded.modelConfig) {
    return { providers: aiProviders, router: aiModelRouter };
  }
  if (AppState.loading.modelConfig) return AppState.loading.modelConfig;
  const request = (async () => {
    try {
      const [providersData, routerData] = await Promise.all([
        aiGatewayGet('/ai/providers'),
        aiGatewayGet('/ai/model-router')
      ]);
      aiProviders = providersData.providers || [];
      aiModelRouter = routerData.router || {};
      AppState.loaded.modelConfig = true;
      return {providers: aiProviders, router: aiModelRouter};
    } finally {
      if (AppState.loading.modelConfig === request) AppState.loading.modelConfig = null;
    }
  })();
  AppState.loading.modelConfig = request;
  return request;
}

function currentStrategyName() {
  // Heuristic: if router matches recommended → "稳定省钱版"; else "自定义"
  const recommended = {
    generate_case: 'qwen_plus',
    generate_yaml: 'qwen_plus',
    analyze_failure: 'qwen_plus',
    optimize_yaml: 'qwen_plus',
    agent_plan: 'qwen_plus',
    generate_bug: 'qwen_plus'
  };
  let allMatch = true;
  for (const [key, val] of Object.entries(recommended)) {
    if ((aiModelRouter[key] || '').toLowerCase() !== val) { allMatch = false; break; }
  }
  if (allMatch) return '稳定省钱版';
  // empty router
  if (Object.keys(aiModelRouter || {}).length === 0) return '尚未配置';
  return '自定义策略';
}

function providerLabelById(id) {
  const p = aiProviders.find(x => x.id === id);
  if (!p) return id || '—';
  return `${p.name || p.id}${p.model ? ' · ' + p.model : ''}`;
}

function renderModelConfigCenter(loading=false, errorText='') {
  const area = document.getElementById('editor-area');
  if (!area) return;
  const providerCards = aiProviders.length ? aiProviders.map(provider => {
    const tempInfo = provider.temperatureLocked
      ? `<span class="status-pill warn" title="该模型由服务商固定参数，平台自动适配">参数策略：平台固定</span>`
      : `<span class="status-pill success" title="可按任务类型自定义参数策略">参数策略：可自定义</span>`;
    const sourceInfo = provider.catalogSource === 'live'
      ? '<span class="status-pill success">上游实时目录</span>'
      : provider.catalogSource === 'configured_fallback'
        ? '<span class="status-pill warn">目录降级</span>'
        : '<span class="status-pill">独立配置</span>';
    return `
    <div class="workflow-card">
      <h3>${escapeHtml(provider.name || provider.id)}</h3>
      <p style="font-family:var(--mono);font-size:12px;color:var(--text2);margin:4px 0;">${escapeHtml(provider.model || '')}</p>
      <div class="card-tags" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;">
        <span class="status-pill ${provider.configured ? 'success' : 'warn'}">${escapeHtml(providerStatusText(provider))}</span>
        ${sourceInfo}
        ${tempInfo}
      </div>
    </div>
  `}).join('') : `
    <div class="workflow-card"><h3>暂无模型通道</h3><p>请联系管理员检查模型通道配置。</p></div>
  `;
  const routerRows = MODEL_ROUTER_FIELDS.map(([key, label]) => {
    const selected = aiModelRouter[key] || 'qwen_plus';
    return `
      <label class="agent-field">
        <span>${escapeHtml(label)}</span>
        <select data-model-router="${escapeHtml(key)}" ${loading ? 'disabled' : ''}>
          ${modelProviderOptions(selected)}
        </select>
      </label>
    `;
  }).join('');
  // 策略概览表格
  const strategyRows = MODEL_ROUTER_FIELDS.map(([key, label]) => {
    const selected = aiModelRouter[key] || 'qwen_plus';
    return `
      <tr>
        <td>${escapeHtml(label)}</td>
        <td class="model-strategy-model">${escapeHtml(providerLabelById(selected))}</td>
      </tr>
    `;
  }).join('');
  const strategyName = currentStrategyName();
  area.className = 'editor-area';
  area.innerHTML = `
    <div class="workflow-guide model-config-guide">
      <div class="workflow-hero">
        <div class="workflow-kicker">模型策略 · 服务端 Key · 多模型路由</div>
        <h2>模型配置</h2>
        <p>这里只配置各能力对应的模型策略；日常执行请回到全自动 Agent 工作台。</p>
      </div>
      ${errorText ? `<div class="agent-risk show">${escapeHtml(errorText)}</div>` : ''}

      <!-- 当前策略概览 -->
      <div class="model-strategy-card">
        <div class="model-strategy-head">
          <div>
            <div class="model-strategy-kicker">当前模型策略</div>
            <h3 class="model-strategy-name">${escapeHtml(strategyName)}</h3>
          </div>
          <div class="model-strategy-actions">
            <button class="btn-sm primary" onclick="applyRecommendedStrategy()" ${loading ? 'disabled' : ''}>一键应用推荐策略</button>
            <button class="btn-sm success" onclick="testAiGateway()" ${loading ? 'disabled' : ''}>测试当前策略</button>
            <button class="btn-sm" onclick="document.getElementById('model-config-advanced')?.toggleAttribute('open')">高级设置</button>
          </div>
        </div>
        <table class="model-strategy-table">
          <thead><tr><th>能力</th><th>模型</th></tr></thead>
          <tbody>${strategyRows}</tbody>
        </table>
        <div class="generate-hint" style="margin-top:8px;">推荐策略：默认全部使用 Qwen Plus。需要更换某一类能力时，可以在高级设置里单独切换并保存。</div>
      </div>

      <!-- 高级设置（折叠） -->
      <details class="dashboard-panel dashboard-accordion" id="model-config-advanced" style="margin-top:8px;">
        <summary style="cursor:pointer;padding:10px 14px;font-weight:600;font-size:14px;">高级设置：模型通道 / 自定义路由</summary>
        <div class="dashboard-accordion-body" style="padding:12px 14px;">
          <h3 style="margin:0 0 8px 0;font-size:13px;">自定义路由</h3>
          <p class="generate-hint" style="margin-bottom:10px;">先按能力选择模型，再点击“保存模型策略”才会生效。列表较长时可键入模型名快速跳转；未配置 Key 的模型调用会失败。</p>
          <div class="agent-form-grid">${routerRows}</div>
          <div class="agent-actions" style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
            <button class="btn-sm primary" onclick="saveModelRouterConfig()" ${loading ? 'disabled' : ''}>保存模型策略</button>
            <span class="generate-hint" style="margin-left:auto;">测试目标：</span>
            <select id="model-test-provider" class="inline-select" ${loading ? 'disabled' : ''}>
              ${modelProviderOptions(aiModelRouter.analyze_failure || 'qwen_plus')}
            </select>
          </div>
          <details class="dashboard-panel dashboard-accordion model-provider-catalog" style="margin-top:12px;">
            <summary style="cursor:pointer;padding:10px 14px;font-weight:600;font-size:13px;">模型通道目录 · ${aiProviders.length} 个（按需查看）</summary>
            <div class="dashboard-accordion-body" style="padding:12px 14px;">
              <p class="generate-hint" style="margin-bottom:10px;">这里展示服务端发现的全部通道，只用于核对名称、配置状态和参数策略；日常使用无需逐项查看。</p>
              <div class="workflow-grid">${providerCards}</div>
            </div>
          </details>
        </div>
      </details>
    </div>
  `;
}

function applyRecommendedStrategy() {
  if (!confirm('确认应用推荐策略？应用后会立即保存，并影响后续 AI 生成、分析和修复任务。')) return;
  const recommended = {
    generate_case: 'qwen_plus',
    generate_yaml: 'qwen_plus',
    analyze_failure: 'qwen_plus',
    optimize_yaml: 'qwen_plus',
    agent_plan: 'qwen_plus',
    generate_bug: 'qwen_plus'
  };
  // Update selects in DOM if they exist
  document.querySelectorAll('[data-model-router]').forEach(select => {
    const key = select.dataset.modelRouter;
    if (recommended[key]) select.value = recommended[key];
  });
  // Save directly
  aiGatewayPost('/ai/model-router', recommended).then(data => {
    aiModelRouter = data.router || recommended;
    showToast('✓ 推荐策略已应用并保存', 'success');
    showModelConfigCenter();
  }).catch(e => {
    // Even if save fails, update local state so user sees the change
    Object.assign(aiModelRouter, recommended);
    showToast('推荐策略已填入，保存失败：' + (e.message || ''), 'warn');
    showModelConfigCenter();
  });
}

async function showModelConfigCenter() {
  if (activeWorkflow !== 'config') setActiveWorkflow('config');
  activeWorkspaceMode = 'model-config';
  resetYamlToolbarForManager();
  document.getElementById('toolbar-path').innerHTML = '<span>📁</span> 模型配置';
  document.getElementById('toolbar-help').textContent = '服务端统一保存 API Key；页面只配置能力到模型的路由。';
  document.getElementById('file-info').textContent = '模型配置';
  renderModelConfigCenter(true);
  try {
    await loadAiModelConfig();
    renderModelConfigCenter(false);
  } catch(e) {
    renderModelConfigCenter(false, e.message || '模型配置加载失败');
    showToast(e.message || '模型配置加载失败', 'error');
  }
}

async function saveModelRouterConfig() {
  const router = {};
  document.querySelectorAll('[data-model-router]').forEach(select => {
    router[select.dataset.modelRouter] = select.value;
  });
  const changed = Object.keys(router).filter(key => String(router[key] || '') !== String(aiModelRouter[key] || ''));
  if (!changed.length) {
    showToast('当前模型策略没有变化，无需保存', 'success');
    return;
  }
  if (!confirm(`确认保存模型策略？本次将修改 ${changed.length} 项能力路由，并影响后续 AI 生成、分析和修复任务。`)) return;
  try {
    const data = await aiGatewayPost('/ai/model-router', router);
    aiModelRouter = data.router || router;
    showToast('✓ 模型配置已保存', 'success');
    await showModelConfigCenter();
  } catch(e) {
    showToast(e.message || '模型配置保存失败', 'error');
    alert(`模型配置保存失败：${e.message || e}`);
  }
}

async function testAiGateway() {
  try {
    const providerId = document.getElementById('model-test-provider')?.value || aiModelRouter.analyze_failure || 'qwen_plus';
    const data = await aiGatewayPost('/ai/providers/test', {providerId});
    showModelServiceTestResult(data, true);
    showToast('✓ AI 模型服务调用成功', 'success');
  } catch(e) {
    showModelServiceTestResult({ message: e.message || '模型服务调用失败' }, false);
    showToast(e.message || 'AI 模型服务调用失败', 'error');
  }
}

function showModelServiceTestResult(data = {}, success = true) {
  const title = document.getElementById('model-test-result-title');
  const status = document.getElementById('model-test-result-status');
  const details = document.getElementById('model-test-result-details');
  if (!title || !status || !details) return;
  title.textContent = success ? '模型服务连接正常' : '模型服务连接失败';
  status.className = `generate-status show ${success ? 'success' : 'error'}`;
  status.textContent = success ? '测试请求已成功返回，可继续使用当前模型策略。' : (data.message || '请检查模型通道、服务端密钥和网络连接。');
  details.innerHTML = success ? `
    <div class="review-stat"><span>服务商</span><strong>${escapeHtml(data.provider || data.providerId || '当前策略')}</strong></div>
    <div class="review-stat"><span>模型</span><strong>${escapeHtml(data.model || '服务端默认')}</strong></div>
    <div class="review-stat"><span>结果</span><strong>调用成功</strong></div>
  ` : '<div class="review-stat"><span>建议</span><strong>检查模型配置后重试</strong></div>';
  document.getElementById('modal-model-test-result')?.classList.add('show');
}
