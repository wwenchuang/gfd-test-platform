// ai-repair.js
// Extracted from task-manager.html (no logic changes).


function jobYamlForAi(job) {
  if (job?.yaml) return job.yaml;
  if (currentFile && document.getElementById('editor')) return editorInitialContent || document.getElementById('editor')?.value || '';
  return '';
}

function jobLogForAi(job) {
  return [
    job?.error,
    job?.message,
    job?.stderr_tail,
    job?.stdout_tail,
    job?.error_trace,
    JSON.stringify(job?.failure_review || {})
  ].filter(Boolean).join('\n').slice(-6000);
}

async function analyzeFailureFromJob(jobId, options={}) {
  const job = latestJobs.find(item => item.job_id === jobId) || {};
  await LoadingManager.withLoading(async () => {
    try {
      selectedRepairJobId = jobId || selectedRepairJobId;
      const originalYaml = jobYamlForAi(job);
      const data = await aiGatewayPost('/ai/analyze-failure', {
        taskName: job.target_task_name || job.current_task_name || job.file || job.module || jobId || '',
        yaml: originalYaml,
        log: jobLogForAi(job),
        screenshotDesc: job.screenshot_desc || job.report_url || ''
      });
      aiFailureDraft = {
        title: 'AI分析失败原因',
        summary: `任务：${job.target_task_name || job.file || jobId || ''}`,
        analysis: stringifyArtifact(data.analysis || data),
        originalYaml,
        fixedYaml: '',
        requirement: job.target_task_name || job.current_task_name || job.file || '',
        activeTab: 'analysis'
      };
      if (options.renderPage || activeWorkflow === 'repair') {
        showAiRepairCenter();
      } else if (!options.silentModal) {
        renderAiGatewayResult();
      }
      showToast('✓ AI 分析完成', 'success');
    } catch(e) {
      showToast(e.message || 'AI分析失败', 'error');
      if (activeWorkflow === 'repair') showAiRepairCenter();
    }
  }, { overlay: 'AI 正在分析失败原因...' });
}

async function analyzeCurrentAgentFailure() {
  const run = currentAgentRun();
  const yaml = run?.artifacts?.yamlDraft || aiFailureDraft?.originalYaml || '';
  const log = stringifyArtifact(run?.steps || []);
  await LoadingManager.withLoading(async () => {
    try {
      const data = await aiGatewayPost('/ai/analyze-failure', {
        taskName: run?.options?.goal || 'Agent 当前任务',
        yaml,
        log,
        screenshotDesc: ''
      });
      aiFailureDraft = {
        title: 'AI Agent 失败分析',
        summary: run?.options?.goal || '当前 Agent 任务',
        analysis: stringifyArtifact(data.analysis || data),
        originalYaml: yaml,
        fixedYaml: '',
        requirement: run?.options?.goal || '',
        activeTab: 'analysis'
      };
      renderAiGatewayResult();
      showToast('✓ Agent 失败分析完成', 'success');
    } catch(e) {
      showToast(e.message || 'Agent 失败分析失败', 'error');
    }
  }, { overlay: 'AI 正在分析 Agent 失败原因...' });
}

async function generateBugDraftFromAnalysis() {
  if (!aiFailureDraft?.analysis) {
    showToast('请先进行 AI 失败分析', 'error');
    return;
  }
  await LoadingManager.withLoading(async () => {
    try {
      const data = await aiGatewayPost('/ai/generate-bug', {
        taskName: aiFailureDraft.requirement || 'AI Agent 任务',
        envInfo: '功夫豆测试平台 / Midscene / Sonic',
        failureAnalysis: aiFailureDraft.analysis
      });
      aiFailureDraft.title = '飞书缺陷草稿';
      aiFailureDraft.analysis = stringifyArtifact(data.bug || data);
      aiFailureDraft.activeTab = 'analysis';
      if (activeWorkflow === 'repair') showAiRepairCenter();
      else renderAiGatewayResult();
      showToast('✓ 飞书缺陷草稿已生成，提交前仍需人工确认', 'success');
    } catch(e) {
      showToast(e.message || '生成飞书缺陷草稿失败', 'error');
    }
  }, { overlay: 'AI 正在生成飞书缺陷草稿...' });
}

async function generateRepairYamlFromAnalysis() {
  if (!aiFailureDraft) {
    showToast('请先进行 AI 失败分析', 'error');
    return;
  }
  const normalized = aiFailureDraftNormalized();
  if (!normalized.canAutoRepair) {
    showToast(`${normalized.failureType} 不允许自动修 YAML，请人工复核或生成缺陷草稿`, 'error');
    if (activeWorkflow === 'repair') showAiRepairCenter();
    return;
  }
  await LoadingManager.withLoading(async () => {
    try {
      const data = await aiGatewayPost('/ai/optimize-yaml', {
        yaml: aiFailureDraft.originalYaml || '',
        failureAnalysis: aiFailureDraft.analysis || '',
        requirement: aiFailureDraft.requirement || ''
      });
      aiFailureDraft.fixedYaml = data.fixedYaml || data.yaml || '';
      aiFailureDraft.diff = data.diff || data.diff_summary || data.diffSummary || '';
      aiFailureDraft.validation = data.validation || {};
      aiFailureDraft.riskHits = data.riskHits || data.risk_hits || repairDraftRiskHits();
      aiFailureDraft.requireConfirm = Boolean(data.requireConfirm ?? data.require_confirm ?? (aiFailureDraft.riskHits || []).length);
      aiFailureDraft.activeTab = 'fixed';
      const job = normalizeJob(latestJobs.find(item => (item.job_id || item.jobId) === selectedRepairJobId) || {});
      const draft = await upsertRepairDraft(createRepairDraftFromAiResult(job, aiFailureDraft, data));
      aiFailureDraft.draftId = draft.draftId || draft.draft_id;
      if (activeWorkflow === 'repair') showAiRepairCenter();
      else renderAiGatewayResult();
      showToast('✓ 修复 YAML 草稿已生成，已进入待我确认', 'success');
    } catch(e) {
      showToast(e.message || 'YAML修复失败', 'error');
    }
  }, { overlay: 'AI 正在生成修复 YAML...' });
}

function aiGatewayResultText() {
  if (!aiFailureDraft) return '';
  const tab = aiFailureDraft.activeTab || 'analysis';
  if (tab === 'original') return aiFailureDraft.originalYaml || '暂无原始 YAML。打开 YAML 文件后再从执行中心分析，可自动带入当前文件内容。';
  if (tab === 'fixed') return aiFailureDraft.fixedYaml || '暂无修复 YAML。请先点击“生成修复 YAML”。';
  return aiFailureDraft.analysis || '';
}

function showAiGatewayResultTab(tab) {
  if (!aiFailureDraft) return;
  aiFailureDraft.activeTab = tab;
  renderAiGatewayResult();
}

function renderAiGatewayResult() {
  if (!aiFailureDraft) return;
  document.getElementById('ai-gateway-result-title').textContent = aiFailureDraft.title || 'AI 分析结果';
  document.getElementById('ai-gateway-result-summary').textContent = aiFailureDraft.summary || '';
  document.querySelectorAll('#ai-gateway-result-tabs .agent-tab').forEach(btn => {
    const text = btn.textContent || '';
    const active = (aiFailureDraft.activeTab === 'analysis' && text.includes('分析'))
      || (aiFailureDraft.activeTab === 'original' && text.includes('原始'))
      || (aiFailureDraft.activeTab === 'fixed' && text.includes('修复'));
    btn.classList.toggle('active', active);
  });
  const risk = document.getElementById('ai-gateway-risk-hint');
  const riskHits = agentRiskHits([aiFailureDraft.originalYaml, aiFailureDraft.fixedYaml].filter(Boolean).join('\n'));
  risk.classList.toggle('show', riskHits.length > 0);
  risk.textContent = riskHits.length ? `风险提示：YAML 命中 ${riskHits.join('、')}，修复草稿必须人工确认后才能使用。` : '';
  document.getElementById('ai-gateway-result-box').textContent = aiGatewayResultText();
  document.getElementById('modal-ai-gateway-result').classList.add('show');
}

async function copyAiGatewayResult() {
  try {
    await copyText(aiGatewayResultText());
    showToast('✓ 已复制当前内容', 'success');
  } catch(e) {
    showToast('复制失败，请手动选择文本', 'error');
  }
}

function downloadAiGatewayYamlDraft() {
  const yaml = aiFailureDraft?.fixedYaml || '';
  if (!yaml) {
    showToast('暂无修复 YAML 可下载', 'error');
    return;
  }
  const blob = new Blob([yaml], {type: 'text/yaml;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${safeFilename(aiFailureDraft.requirement || 'ai-repair-draft')}.repair.yaml`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function normalizeFailureAnalysis(raw) {
  const rawText = stringifyArtifact(raw || '').trim();
  let parsed = null;
  if (raw && typeof raw === 'object') {
    parsed = raw;
  } else if (rawText) {
    try { parsed = JSON.parse(rawText); } catch(e) { parsed = null; }
  }
  const text = rawText || stringifyArtifact(parsed || '');
  const pick = (...keys) => {
    for (const key of keys) {
      const value = parsed && parsed[key];
      if (Array.isArray(value)) return value.filter(Boolean).join('；');
      if (value !== undefined && value !== null && String(value).trim()) return String(value).trim();
    }
    return '';
  };
  let failureType = pick('failureType', 'failure_type', 'type', 'category').toUpperCase();
  if (!failureType) {
    if (/PRODUCT[_\s-]?BUG|产品\s*Bug|产品缺陷|真实缺陷/i.test(text)) failureType = 'PRODUCT_BUG';
    else if (/ENV[_\s-]?ISSUE|环境问题|设备问题|网络|模型超时|服务不可用|Request was aborted/i.test(text)) failureType = 'ENV_ISSUE';
    else if (/SCRIPT[_\s-]?ISSUE|脚本问题|定位失败|断言|YAML|selector|locate element/i.test(text)) failureType = 'SCRIPT_ISSUE';
    else failureType = 'UNKNOWN';
  }
  if (!['SCRIPT_ISSUE', 'ENV_ISSUE', 'PRODUCT_BUG', 'UNKNOWN'].includes(failureType)) failureType = 'UNKNOWN';
  const conclusion = pick('conclusion', 'summary', 'title') || extractByLabel(text, ['失败结论', '结论', 'Conclusion']) || `${failureType} 待复核`;
  const reason = pick('reason', 'possibleReason', 'possible_reasons', 'rootCause', 'root_cause') || extractByLabel(text, ['可能原因', '失败原因', '原因', 'Root Cause']) || '暂未识别到明确原因，请查看完整分析。';
  const suggestion = pick('suggestion', 'suggestions', 'nextAction', 'next_action', 'recommendedAction') || extractByLabel(text, ['修复建议', '建议动作', '建议', 'Next Action']) || (failureType === 'SCRIPT_ISSUE' ? '可以生成 YAML 修复草稿，但需要人工确认后再覆盖。' : '不建议自动修改 YAML，请人工复核。');
  const yamlPatch = pick('yamlPatch', 'yaml_patch', 'patch', 'diff', 'diff_summary');
  let canAutoRepair = Boolean(parsed && (parsed.canAutoRepair === true || parsed.can_auto_repair === true));
  if (failureType === 'SCRIPT_ISSUE' && !(parsed && (parsed.canAutoRepair === false || parsed.can_auto_repair === false))) canAutoRepair = true;
  if (['UNKNOWN', 'PRODUCT_BUG', 'ENV_ISSUE'].includes(failureType)) canAutoRepair = false;
  const riskLevel = (pick('riskLevel', 'risk_level') || (failureType === 'SCRIPT_ISSUE' ? 'medium' : 'high')).toLowerCase();
  return { failureType, conclusion, reason, canAutoRepair, riskLevel, suggestion, yamlPatch, rawText: text };
}

function extractByLabel(text, labels=[]) {
  for (const label of labels) {
    const re = new RegExp(`${label}[：:\\\\s]+([^\\n]+)`, 'i');
    const match = String(text || '').match(re);
    if (match) return match[1].trim();
  }
  return '';
}

function aiFailureDraftNormalized() {
  return normalizeFailureAnalysis(aiFailureDraft?.analysis || aiFailureDraft?.rawAnalysis || '');
}

function repairDraftRiskHits() {
  return agentRiskHits([
    aiFailureDraft?.originalYaml,
    aiFailureDraft?.fixedYaml,
    aiFailureDraft?.analysis,
    aiFailureDraft?.bugDraft
  ].filter(Boolean).join('\n'));
}

function repairDraftStatusText(status) {
  const map = {
    DRAFTED: '已生成草稿',
    WAIT_CONFIRM: '待我确认',
    APPLIED: '已人工应用',
    REJECTED: '已拒绝',
    EXPIRED: '已过期'
  };
  return map[String(status || '').toUpperCase()] || '待处理';
}

function currentRepairDraft() {
  if (!aiFailureDraft) return null;
  const draftId = aiFailureDraft.draftId || aiFailureDraft.draft_id || '';
  if (draftId) return repairDrafts.find(draft => (draft.draftId || draft.draft_id) === draftId) || null;
  if (selectedRepairJobId) {
    return repairDrafts.find(draft => (draft.jobId || draft.job_id) === selectedRepairJobId && ['DRAFTED', 'WAIT_CONFIRM'].includes(String(draft.status || '').toUpperCase())) || null;
  }
  return null;
}

function createRepairDraftFromAiResult(job={}, draft={}, optimizeResult={}) {
  const normalized = normalizeFailureAnalysis(draft.analysis || draft.rawAnalysis || '');
  const riskText = [
    draft.originalYaml,
    draft.fixedYaml,
    draft.analysis,
    draft.bugDraft,
    optimizeResult.diff || optimizeResult.diff_summary
  ].filter(Boolean).join('\n');
  const draftId = draft.draftId || optimizeResult.draftId || `repair_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  return {
    draftId,
    draft_id: draftId,
    jobId: job.job_id || job.jobId || selectedRepairJobId || draft.jobId || '',
    job_id: job.job_id || job.jobId || selectedRepairJobId || draft.jobId || '',
    module: job.module || draft.module || currentModule || '',
    file: job.file || draft.file || currentFile || '',
    taskName: job.target_task_name || job.current_task_name || job.taskName || draft.requirement || draft.title || '',
    status: 'WAIT_CONFIRM',
    failureType: normalized.failureType,
    riskLevel: normalized.riskLevel,
    conclusion: normalized.conclusion,
    reason: normalized.reason,
    suggestion: normalized.suggestion,
    analysis: draft.analysis || '',
    originalYaml: draft.originalYaml || '',
    fixedYaml: draft.fixedYaml || '',
    diff: draft.diff || optimizeResult.diff || optimizeResult.diff_summary || '',
    validation: draft.validation || optimizeResult.validation || {},
    riskHits: agentRiskHits(riskText),
    requireConfirm: true,
    createdAt: draft.createdAt || new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    source: 'ai_gateway'
  };
}

async function upsertRepairDraft(draft, options={}) {
  const normalized = {
    ...draft,
    draftId: draft.draftId || draft.draft_id,
    draft_id: draft.draft_id || draft.draftId,
    updatedAt: new Date().toISOString()
  };
  repairDrafts = [normalized, ...repairDrafts.filter(item => (item.draftId || item.draft_id) !== normalized.draftId)];
  if (options.persist !== false) {
    const data = await apiRequest('/repair-drafts', {
      method: 'POST',
      body: JSON.stringify(normalized)
    });
    const saved = data.draft || normalized;
    repairDrafts = [saved, ...repairDrafts.filter(item => (item.draftId || item.draft_id) !== (saved.draftId || saved.draft_id))];
    return saved;
  }
  return normalized;
}

async function loadRepairDrafts(options={}) {
  try {
    const data = await apiRequest('/repair-drafts');
    repairDrafts = data.drafts || [];
    return repairDrafts;
  } catch(e) {
    if (!options.silent) showToast(e.message || '读取修复草稿失败', 'error');
    return repairDrafts;
  }
}

function repairDraftById(draftId) {
  return repairDrafts.find(draft => (draft.draftId || draft.draft_id) === draftId) || null;
}

function hasMeaningfulFailureReview(review) {
  if (!review) return false;
  if (typeof review === 'string') return review.trim().length > 0;
  if (typeof review !== 'object') return Boolean(review);
  return Object.values(review).some(value => {
    if (Array.isArray(value)) return value.length > 0;
    if (value && typeof value === 'object') return Object.keys(value).length > 0;
    return String(value || '').trim().length > 0;
  });
}

function openRepairDraft(draftId) {
  const draft = repairDraftById(draftId);
  if (!draft) {
    showToast('修复草稿不存在或已过期', 'error');
    return;
  }
  selectedRepairJobId = draft.jobId || draft.job_id || '';
  aiFailureDraft = {
    draftId: draft.draftId || draft.draft_id,
    title: draft.taskName || draft.file || 'YAML 修复草稿',
    summary: repairDraftStatusText(draft.status),
    analysis: draft.analysis || '',
    originalYaml: draft.originalYaml || '',
    fixedYaml: draft.fixedYaml || '',
    diff: draft.diff || '',
    validation: draft.validation || {},
    riskHits: draft.riskHits || [],
    requireConfirm: true,
    requirement: draft.taskName || '',
    module: draft.module || '',
    file: draft.file || '',
    activeTab: 'fixed'
  };
  setActiveWorkflow('repair');
  showAiRepairCenter();
}

async function confirmApplyRepairDraft(draftId) {
  const draft = repairDraftById(draftId) || currentRepairDraft();
  if (!draft) {
    showToast('没有可应用的修复草稿', 'error');
    return;
  }
  const riskHits = draft.riskHits || [];
  const confirmText = riskHits.length
    ? `这个草稿命中高风险动作：${riskHits.join('、')}。\n\n确认人工应用到 ${draft.module}/${draft.file}？系统会先自动备份当前 YAML。`
    : `确认人工应用修复草稿到 ${draft.module}/${draft.file}？系统会先自动备份当前 YAML。`;
  if (!confirm(confirmText)) return;
  try {
    const data = await apiRequest('/repair-drafts/apply', {
      method: 'POST',
      body: JSON.stringify({
        draftId: draft.draftId || draft.draft_id,
        confirmRisk: true,
        confirmApply: true
      })
    });
    const saved = data.draft || draft;
    await upsertRepairDraft(saved, {persist: false});
    showToast('✓ 修复草稿已人工应用，当前 YAML 已自动备份', 'success');
    if (saved.module && saved.file) await openFile(saved.module, saved.file);
    else if (activeWorkflow === 'repair') showAiRepairCenter();
  } catch(e) {
    showToast(e.message || '应用修复草稿失败', 'error');
  }
}

async function rejectRepairDraft(draftId) {
  const draft = repairDraftById(draftId) || currentRepairDraft();
  if (!draft) return;
  const reason = prompt('拒绝这个修复草稿的原因（可选）：', '');
  if (reason === null) return;
  try {
    const data = await apiRequest('/repair-drafts/reject', {
      method: 'POST',
      body: JSON.stringify({draftId: draft.draftId || draft.draft_id, reason})
    });
    await upsertRepairDraft(data.draft || {...draft, status: 'REJECTED'}, {persist: false});
    showToast('✓ 已拒绝修复草稿', 'success');
    if (activeWorkflow === 'repair') showAiRepairCenter();
    else renderJobs();
  } catch(e) {
    showToast(e.message || '拒绝失败', 'error');
  }
}

function aiRepairFailedJobs() {
  return latestJobs
    .filter(job => {
      const status = String(job.status || '').toLowerCase();
      if (['success', 'passed', 'pass', 'ok'].includes(status)) return false;
      const errorText = [job.error, job.message, job.stderr_tail, job.stdout_tail, job.error_trace].filter(Boolean).join('\n').trim();
      return ['failed', 'timeout', 'cancelled'].includes(status) || Boolean(errorText) || hasMeaningfulFailureReview(job.failure_review);
    })
    .slice(0, 80);
}

function selectRepairJob(jobId) {
  selectedRepairJobId = jobId || '';
  if (activeWorkflow === 'repair') showAiRepairCenter();
}

async function openAiRepairForJob(jobId) {
  selectedRepairJobId = jobId || '';
  activeWorkspaceMode = 'ai-repair';
  setActiveWorkflow('repair');
  await analyzeFailureFromJob(jobId, {renderPage: true});
}

async function generateBugDraftForJob(jobId) {
  if (jobId) await analyzeFailureFromJob(jobId, {renderPage: true, silentModal: true});
  await generateBugDraftFromAnalysis();
  if (activeWorkflow === 'repair') showAiRepairCenter();
}

const FAILURE_TYPE_META = {
  SCRIPT_ISSUE: { tag: '脚本问题', tone: 'warn', primary: { label: '生成修复草稿', onClick: 'generateRepairYamlFromAnalysis()' } },
  PRODUCT_BUG: { tag: '产品缺陷', tone: 'danger', primary: { label: '生成缺陷草稿', onClick: 'generateBugDraftFromAnalysis()' } },
  ENV_ISSUE: { tag: '环境问题', tone: 'warn', primary: { label: '查看环境建议', onClick: 'showAiRepairTab(\'analysis\')' } },
  UNKNOWN: { tag: '待人工复核', tone: '', primary: { label: '人工复核', onClick: 'showToast(\'已标记为人工复核，请在右侧查看完整分析\',\'success\')' } }
};

function failureTypeChip(type) {
  const meta = FAILURE_TYPE_META[type] || FAILURE_TYPE_META.UNKNOWN;
  return `<span class="failure-type-chip failure-${type.toLowerCase()}">${escapeHtml(meta.tag)} · ${escapeHtml(type)}</span>`;
}

function aiRepairSummaryHtml(normalized) {
  const meta = FAILURE_TYPE_META[normalized.failureType] || FAILURE_TYPE_META.UNKNOWN;
  const reasons = String(normalized.reason || '').split(/[；;\n]/).map(s => s.trim()).filter(Boolean);
  return `
    <div class="review-panel ai-repair-analysis">
      <div class="section-head">
        <div>
          <h3>结构化分析</h3>
          <p>结构化展示失败归因；按类型路由到不同操作。</p>
        </div>
        ${failureTypeChip(normalized.failureType)}
      </div>
      <div class="review-stats ai-repair-stat-grid">
        <div class="review-stat"><strong>${escapeHtml(normalized.failureType)}</strong><span>失败类型</span></div>
        <div class="review-stat"><strong>${escapeHtml(normalized.riskLevel || '-')}</strong><span>风险等级</span></div>
        <div class="review-stat"><strong>${normalized.canAutoRepair ? '允许草稿' : '禁止自动修复'}</strong><span>修复策略</span></div>
      </div>
      <div class="ai-repair-block">
        <div class="ai-repair-block-label">失败结论</div>
        <p>${escapeHtml(normalized.conclusion)}</p>
      </div>
      <div class="ai-repair-block">
        <div class="ai-repair-block-label">可能原因</div>
        ${reasons.length > 1
          ? `<ul class="ai-repair-reasons">${reasons.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>`
          : `<p>${escapeHtml(normalized.reason || '暂未识别明确原因')}</p>`}
      </div>
      <div class="ai-repair-block">
        <div class="ai-repair-block-label">建议动作</div>
        <p>${escapeHtml(normalized.suggestion)}</p>
      </div>
      <div class="review-actions" style="margin-top:8px;">
        <button class="btn-sm primary" onclick="${meta.primary.onClick}">${escapeHtml(meta.primary.label)}</button>
      </div>
      <details class="dashboard-accordion" style="margin-top:12px;">
        <summary><h3>完整分析</h3></summary>
        <pre class="agent-artifact-box">${escapeHtml(normalized.rawText || '暂无完整分析')}</pre>
      </details>
    </div>
  `;
}

function buildYamlDiffHtml(originalYaml, fixedYaml) {
  const oldLines = String(originalYaml || '').split('\n');
  const newLines = String(fixedYaml || '').split('\n');
  const oldSet = new Set(oldLines);
  const newSet = new Set(newLines);
  const rows = [];
  const max = Math.max(oldLines.length, newLines.length);
  for (let i = 0; i < max; i++) {
    const o = oldLines[i] !== undefined ? oldLines[i] : '';
    const n = newLines[i] !== undefined ? newLines[i] : '';
    const oCls = o && !newSet.has(o) ? 'diff-del' : 'diff-eq';
    const nCls = n && !oldSet.has(n) ? 'diff-add' : 'diff-eq';
    rows.push(`<tr><td class="diff-line ${oCls}">${escapeHtml(o)}</td><td class="diff-line ${nCls}">${escapeHtml(n)}</td></tr>`);
  }
  return `
    <table class="diff-view">
      <thead><tr><th>原始 YAML</th><th>修复 YAML</th></tr></thead>
      <tbody>${rows.join('') || '<tr><td colspan="2" class="job-empty">暂无差异，请先生成修复 YAML。</td></tr>'}</tbody>
    </table>
  `;
}

function repairYamlDraftHtml(normalized) {
  const riskHits = repairDraftRiskHits();
  const canRepair = normalized.canAutoRepair;
  const validationText = stringifyArtifact(aiFailureDraft?.validation || {});
  const diffText = aiFailureDraft?.diff || aiFailureDraft?.diffSummary || aiFailureDraft?.yamlPatch || normalized.yamlPatch || '';
  const draft = currentRepairDraft();
  const draftId = draft?.draftId || draft?.draft_id || aiFailureDraft?.draftId || '';
  const draftStatus = draft ? repairDraftStatusText(draft.status) : '未保存草稿';
  const canApplyDraft = Boolean(draftId && aiFailureDraft?.fixedYaml && canRepair && ['DRAFTED', 'WAIT_CONFIRM'].includes(String(draft?.status || '').toUpperCase()));
  return `
    <div class="review-panel ai-repair-draft-panel">
      <div class="section-head">
        <div>
          <h3>YAML 修复草稿</h3>
          <p>这里只生成草稿，不自动覆盖原 YAML；保存到正式脚本前必须人工确认。</p>
          <div class="job-meta">草稿状态：${escapeHtml(draftStatus)}${draftId ? ` · ${escapeHtml(draftId)}` : ''}</div>
        </div>
        <div class="review-actions">
          <button class="btn-sm ai" onclick="generateRepairYamlFromAnalysis()" ${canRepair ? '' : 'disabled title="只有 SCRIPT_ISSUE 才允许生成修复草稿"'}>生成修复草稿</button>
          <button class="btn-sm" onclick="copyText(aiFailureDraft?.fixedYaml || '')" ${aiFailureDraft?.fixedYaml ? '' : 'disabled'}>复制草稿</button>
          <button class="btn-sm" onclick="downloadAiGatewayYamlDraft()" ${aiFailureDraft?.fixedYaml ? '' : 'disabled'}>下载草稿</button>
          <button class="btn-sm success" onclick="confirmApplyRepairDraft(${jsArg(draftId)})" ${canApplyDraft ? '' : 'disabled title="需要先生成并保存修复草稿"'}>人工确认替换</button>
          <button class="btn-sm danger" onclick="rejectRepairDraft(${jsArg(draftId)})" ${draftId ? '' : 'disabled'}>拒绝草稿</button>
        </div>
      </div>
      ${!canRepair ? `<div class="agent-risk show">当前归因为 ${escapeHtml(normalized.failureType)}，禁止自动修 YAML。请人工复核，或生成飞书缺陷草稿。</div>` : ''}
      ${riskHits.length ? `<div class="agent-risk show">该任务包含高风险动作：${escapeHtml(riskHits.join('、'))}。禁止自动执行，请人工确认后继续。</div>` : ''}
      <div class="agent-tabs">
        <button class="agent-tab ${aiFailureDraft?.activeTab === 'original' ? 'active' : ''}" onclick="showAiRepairTab('original')">原始 YAML</button>
        <button class="agent-tab ${aiFailureDraft?.activeTab === 'fixed' ? 'active' : ''}" onclick="showAiRepairTab('fixed')">修复 YAML</button>
        <button class="agent-tab ${aiFailureDraft?.activeTab === 'diff' || aiFailureDraft?.activeTab === 'validation' ? 'active' : ''}" onclick="showAiRepairTab('diff')">Diff / 校验</button>
      </div>
      ${aiFailureDraft?.activeTab === 'diff'
        ? buildYamlDiffHtml(aiFailureDraft?.originalYaml || '', aiFailureDraft?.fixedYaml || '')
        : aiFailureDraft?.activeTab === 'validation'
          ? `<pre class="agent-artifact-box">${escapeHtml(validationText && validationText !== '{}' ? validationText : '暂无校验结果，请先生成修复 YAML。')}</pre>`
          : `<pre class="agent-artifact-box ${aiFailureDraft?.activeTab === 'fixed' ? 'yaml-fixed' : (aiFailureDraft?.activeTab === 'original' ? 'yaml-original' : '')}">${escapeHtml(aiRepairTabText(diffText, validationText))}</pre>`}
      <div class="generate-hint">修复草稿不会自动覆盖当前文件或基线。后续接 Agent Orchestrator 时，也会先进入“待我处理”。</div>
    </div>
  `;
}

function aiRepairTabText(diffText='', validationText='') {
  const tab = aiFailureDraft?.activeTab || 'analysis';
  if (tab === 'original') return aiFailureDraft?.originalYaml || '暂无原始 YAML。请从失败任务进入，或先打开 YAML 后分析。';
  if (tab === 'fixed') return aiFailureDraft?.fixedYaml || '暂无修复 YAML 草稿。';
  if (tab === 'diff') return [
    diffText ? `Diff 摘要:\n${diffText}` : '暂无 diff 摘要。',
    validationText && validationText !== '{}' ? `\n\n校验结果:\n${validationText}` : '\n\n校验结果：待生成草稿后校验。'
  ].join('');
  return aiFailureDraft?.analysis || '请选择失败任务并点击 AI分析失败原因。';
}

function showAiRepairTab(tab) {
  if (!aiFailureDraft) return;
  aiFailureDraft.activeTab = tab;
  showAiRepairCenter();
}

function showAiRepairCenter() {
  activeWorkspaceMode = 'ai-repair';
  const area = document.getElementById('editor-area');
  if (!area) return;
  const failedJobs = aiRepairFailedJobs();
  const selectedJob = failedJobs.find(job => job.job_id === selectedRepairJobId) || failedJobs[0] || null;
  if (!selectedRepairJobId && selectedJob?.job_id) selectedRepairJobId = selectedJob.job_id;
  const normalized = aiFailureDraft ? aiFailureDraftNormalized() : normalizeFailureAnalysis('');
  area.className = 'editor-area';
  area.innerHTML = `
    <div class="review-page ai-repair-page">
      <div class="review-head">
        <div>
          <div class="workflow-kicker">AI REPAIR · 失败归因 / YAML 草稿 / 人工确认</div>
          <h2>AI修复工作台</h2>
          <p>从失败任务进入，先结构化判断失败类型，再生成可复核的 YAML 草稿。产品缺陷、环境问题和 UNKNOWN 不自动修脚本。</p>
        </div>
        <div class="review-actions">
          <button class="btn-sm" onclick="loadJobs(true).then(() => showAiRepairCenter())">刷新失败任务</button>
          <button class="btn-sm primary" onclick="selectedRepairJobId && analyzeFailureFromJob(selectedRepairJobId, {renderPage:true})" ${selectedJob ? '' : 'disabled'}>AI分析失败原因</button>
          <button class="btn-sm" onclick="generateBugDraftFromAnalysis()" ${aiFailureDraft ? '' : 'disabled'}>生成飞书缺陷草稿</button>
        </div>
      </div>
      <div class="review-grid ai-repair-grid">
        <div class="review-panel ai-repair-job-panel">
          <h3>失败任务列表</h3>
          ${failedJobs.length ? `<div class="yaml-task-nav-list ai-repair-job-list">${failedJobs.map(job => `
            <div class="yaml-task-nav-item ${job.job_id === selectedRepairJobId ? 'active' : ''}" onclick="selectRepairJob(${jsArg(job.job_id || '')})">
              <div class="yaml-task-nav-name">${escapeHtml(job.file || job.target_task_name || job.task_name || job.job_id || '失败任务')}</div>
              <div class="yaml-task-nav-meta">${escapeHtml([job.module, job.target_task_name, jobStatusText(job.status || 'failed')].filter(Boolean).join(' · '))}</div>
              <div class="yaml-task-nav-actions">
                <button onclick="event.stopPropagation(); analyzeFailureFromJob(${jsArg(job.job_id || '')}, {renderPage:true})">分析</button>
                <button onclick="event.stopPropagation(); focusJob(${jsArg(job.job_id || '')})">定位</button>
              </div>
            </div>
          `).join('')}</div>` : '<div class="job-empty">暂无失败任务。可以先执行 YAML，失败后这里会出现待分析项。</div>'}
        </div>
        ${aiRepairSummaryHtml(normalized)}
        ${repairYamlDraftHtml(normalized)}
      </div>
    </div>
  `;
  document.getElementById('toolbar-path').innerHTML = '<span>📁</span> AI修复';
  document.getElementById('toolbar-help').textContent = '失败先归因；只有脚本问题才生成 YAML 修复草稿，产品缺陷和环境问题进入人工处理。';
  document.getElementById('file-info').textContent = 'AI修复工作台';
  updateToolbarState('AI修复');
}
