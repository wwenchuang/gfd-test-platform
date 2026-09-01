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

function failureAnalysisSourceText(data) {
  const source = data?.source || data?.analysis_source || {};
  if (source.used_full_logs) {
    const stdoutChars = Number(source.stdout_chars || 0);
    const stderrChars = Number(source.stderr_chars || 0);
    const parts = [`stdout ${stdoutChars} 字`, `stderr ${stderrChars} 字`];
    if (source.summary_available) parts.push('summary 已读取');
    if (source.report_url) parts.push('报告已关联');
    return `已读取 Runner 完整日志（${parts.join('，')}）`;
  }
  if (source.fallback_reason) return `使用页面摘要日志兜底：${source.fallback_reason}`;
  return '';
}

async function analyzeFailureFromJob(jobId, options={}) {
  const inputJob = jobId && typeof jobId === 'object' ? jobId : null;
  const resolvedJobId = inputJob?.job_id || jobId || selectedRepairJobId || '';
  const job = inputJob || latestJobs.find(item => item.job_id === resolvedJobId) || {};
  await LoadingManager.withLoading(async () => {
    try {
      selectedRepairJobId = resolvedJobId || selectedRepairJobId;
      let originalYaml = jobYamlForAi(job);
      let data = null;
      let backendError = null;
      if (resolvedJobId) {
        try {
          data = await apiRequest(`/jobs/${encodeURIComponent(resolvedJobId)}/analyze-failure`, {
            method: 'POST',
            body: {}
          });
        } catch(e) {
          backendError = e;
        }
      }
      if (!data) {
        data = await aiGatewayPost('/ai/analyze-failure', {
          taskName: job.target_task_name || job.current_task_name || job.file || job.module || resolvedJobId || '',
          yaml: originalYaml,
          log: jobLogForAi(job),
          screenshotDesc: job.screenshot_desc || job.report_url || ''
        });
        data.source = {
          ...(data.source || {}),
          used_full_logs: false,
          fallback_reason: backendError?.message || '服务端完整日志分析接口暂不可用'
        };
      }
      originalYaml = data.yaml || originalYaml;
      const sourceText = failureAnalysisSourceText(data);
      aiFailureDraft = {
        title: 'AI分析失败原因',
        summary: `任务：${job.target_task_name || job.file || resolvedJobId || ''}${sourceText ? `；${sourceText}` : ''}`,
        analysis: stringifyArtifact(data.analysis || data.failure_review || data),
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

function bugDraftEnvironmentFacts(job = {}) {
  const pick = (...keys) => {
    for (const key of keys) {
      const value = job?.[key];
      if (value !== undefined && value !== null && String(value).trim()) return String(value).trim();
    }
    return '未提供';
  };
  const appName = pick('app_name', 'appName');
  const appPackage = pick('app_package', 'appPackage', 'package');
  const app = appName === '未提供' && appPackage === '未提供'
    ? '未提供'
    : (appName !== '未提供' && appPackage !== '未提供' ? `${appName}（${appPackage}）` : (appName !== '未提供' ? appName : appPackage));
  return [
    '测试平台：功夫豆测试平台',
    `应用：${app}`,
    `设备：${pick('device_name', 'deviceName', 'device_id', 'deviceId')}`,
    `Runner：${pick('runner_id', 'runnerId')}`,
    `Midscene 版本：${pick('midscene_version', 'midsceneVersion', 'midscene_cli_version')}`,
    `Sonic 版本：${pick('sonic_version', 'sonicVersion')}`,
    `报告地址：${pick('report_url', 'reportUrl', 'sonic_report_url')}`,
  ].join('\n');
}

async function generateBugDraftFromAnalysis() {
  if (!aiFailureDraft?.analysis) {
    showToast('请先进行 AI 失败分析', 'error');
    return;
  }
  await LoadingManager.withLoading(async () => {
    try {
      const job = normalizeJob(latestJobs.find(item => (item.job_id || item.jobId) === selectedRepairJobId) || {});
      const data = await aiGatewayPost('/ai/generate-bug', {
        taskName: aiFailureDraft.requirement || 'AI Agent 任务',
        envInfo: bugDraftEnvironmentFacts(job),
        failureAnalysis: aiFailureDraft.analysis
      });
      const bug = data.bug || data;
      const description = typeof bug === 'string'
        ? bug
        : (bug.description || bug.summary || stringifyArtifact(bug));
      const saved = await apiRequest('/feishu-drafts', {
        method: 'POST',
        body: JSON.stringify({
          draftId: `job-${selectedRepairJobId || 'manual'}-${Date.now()}`,
          title: (typeof bug === 'object' && bug.title) || aiFailureDraft.requirement || '测试执行缺陷',
          description,
          severity: typeof bug === 'object' ? bug.severity : '',
          appName: job.app_name || job.appName || '',
          appPackage: job.app_package || job.appPackage || job.package || '',
          sourceJobId: selectedRepairJobId || '',
          reportUrl: job.report_url || job.reportUrl || job.sonic_report_url || '',
          failureType: aiFailureDraftNormalized().failureType
        })
      });
      aiFailureDraft.title = '飞书缺陷草稿';
      aiFailureDraft.analysis = stringifyArtifact(saved.draft || bug);
      aiFailureDraft.activeTab = 'analysis';
      AppState.loaded.feishuDrafts = false;
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
    showToast(`${failureTypeText(normalized.failureType)}不允许自动修 YAML，请人工复核或生成缺陷草稿`, 'error');
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
  const normalized = aiFailureDraftNormalized();
  const generateButton = document.getElementById('ai-gateway-generate-repair');
  const downloadButton = document.getElementById('ai-gateway-download-repair');
  const feedback = document.getElementById('ai-gateway-action-feedback');
  const canGenerate = Boolean(normalized.canAutoRepair);
  const canDownload = Boolean(aiFailureDraft.fixedYaml);
  if (generateButton) {
    generateButton.disabled = !canGenerate;
    generateButton.title = canGenerate ? '' : `${failureTypeText(normalized.failureType)}不允许自动修 YAML`;
  }
  if (downloadButton) {
    downloadButton.disabled = !canDownload;
    downloadButton.title = canDownload ? '' : '请先生成修复 YAML';
  }
  if (feedback) {
    feedback.textContent = !canGenerate
      ? `${failureTypeText(normalized.failureType)}只做人工处理；请人工复核或生成缺陷草稿。`
      : !canDownload
        ? '当前还没有修复 YAML；先生成草稿，再下载或人工确认。'
        : '修复 YAML 已生成；下载只保存草稿，不会覆盖正式文件。';
  }
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
  const broadTypes = ['SCRIPT_ISSUE', 'ENV_ISSUE', 'PRODUCT_BUG', 'UNKNOWN'];
  const typeCandidates = [
    pick('category', 'failureCategory', 'failure_category'),
    pick('failureType', 'failure_type', 'type')
  ].map(value => String(value || '').toUpperCase());
  let failureType = typeCandidates.find(value => broadTypes.includes(value)) || '';
  if (!failureType) {
    if (/PRODUCT[_\s-]?BUG|产品\s*Bug|产品缺陷|真实缺陷/i.test(text)) failureType = 'PRODUCT_BUG';
    else if (/ENV[_\s-]?ISSUE|环境问题|设备问题|网络|模型超时|服务不可用|Request was aborted/i.test(text)) failureType = 'ENV_ISSUE';
    else if (/SCRIPT[_\s-]?ISSUE|scroll_not_effective|wait_strategy|input_failed|popup_overlay|yaml_syntax|脚本问题|定位失败|断言|YAML|selector|locate element/i.test(text)) failureType = 'SCRIPT_ISSUE';
    else failureType = 'UNKNOWN';
  }
  const conclusion = pick('conclusion', 'summary', 'title') || extractByLabel(text, ['失败结论', '结论', 'Conclusion']) || `${failureTypeText(failureType)}待复核`;
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

function repairFailureTypeText(type) {
  const map = {
    SCRIPT_ISSUE: '脚本问题',
    PRODUCT_BUG: '产品缺陷',
    ENV_ISSUE: '环境问题',
    UNKNOWN: '待人工复核'
  };
  return map[String(type || '').toUpperCase()] || '待人工复核';
}

function repairRiskText(level) {
  const map = { low: '低', medium: '中', high: '高' };
  return map[String(level || '').toLowerCase()] || '中';
}

function promptRepairUnavailable(reason) {
  const message = reason || '当前条件还不能执行这个操作，请先完成前一步。';
  const feedback = document.getElementById('repair-action-feedback');
  if (feedback) {
    feedback.textContent = message;
    feedback.hidden = false;
  }
  showToast(message, 'warn');
}

async function copyRepairDraftYaml() {
  const yaml = aiFailureDraft?.fixedYaml || '';
  if (!yaml) {
    promptRepairUnavailable('暂无修复草稿可复制，请先生成修复草稿');
    return;
  }
  try {
    await copyText(yaml);
    showToast('已复制修复草稿', 'success');
  } catch(e) {
    showToast('复制失败，请手动选择文本', 'error');
  }
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
  if (pendingBatchBusy || !hasPermission('ui.edit')) return;
  const draft = repairDraftById(draftId) || currentRepairDraft();
  if (!draft) {
    showToast('没有可应用的修复草稿', 'error');
    return;
  }
  if (hasOpenEditor() && currentModule === draft.module && currentFile === draft.file
      && (editorDirty || document.getElementById('editor').value !== editorInitialContent)) {
    showToast('当前编辑器有未保存修改，请先保存并重新生成草稿，或撤销修改后再处理', 'error');
    return;
  }
  if (!['DRAFTED', 'WAIT_CONFIRM'].includes(draft.status)) {
    showToast('草稿已处理，请刷新查看最新状态', 'error');
    return;
  }
  showPendingBatchDialog('apply', [`repair:${draft.draftId || draft.draft_id}`]);
}

async function rejectRepairDraft(draftId) {
  if (pendingBatchBusy || !hasPermission('ui.edit')) return;
  const draft = repairDraftById(draftId) || currentRepairDraft();
  if (!draft) return;
  const reason = prompt('拒绝这个修复草稿的原因（可选）：', '');
  if (reason === null) return;
  try {
    const data = await apiRequest('/repair-drafts/reject', {
      method: 'POST',
      body: JSON.stringify({draftId: draft.draftId || draft.draft_id, reason})
    });
    if (data.draft?.status !== 'REJECTED') throw new Error('未收到草稿最终状态，请刷新核实，勿重复提交');
    await upsertRepairDraft(data.draft, {persist: false});
    showToast('✓ 已拒绝修复草稿', 'success');
    if (activeWorkflow === 'repair') showAiRepairCenter();
    else renderJobs();
  } catch(e) {
    showToast(e.message || '拒绝失败', 'error');
  }
}

function aiRepairFailedJobs({ignoreQuery = false} = {}) {
  const query = ignoreQuery ? '' : String(repairJobFilters.query || '').trim().toLowerCase();
  return latestJobs
    .filter(job => {
      const status = String(job.status || '').toLowerCase();
      if (['success', 'passed', 'pass', 'ok'].includes(status)) return false;
      const errorText = [job.error, job.message, job.stderr_tail, job.stdout_tail, job.error_trace].filter(Boolean).join('\n').trim();
      const failed = ['failed', 'timeout', 'cancelled'].includes(status) || Boolean(errorText) || hasMeaningfulFailureReview(job.failure_review);
      if (!failed || !query) return failed;
      return [job.job_id, job.jobId, job.file, job.target_task_name, job.task_name, job.module, job.app_name, job.appName, errorText]
        .filter(Boolean).join(' ').toLowerCase().includes(query);
    })
    .slice(0, 200);
}

function repairJobCountText(totalCount, matchedCount, visibleCount, hasQuery) {
  if (hasQuery) return `匹配 ${matchedCount}/${totalCount} 条`;
  return totalCount === visibleCount ? `${totalCount} 条` : `${totalCount} 条 · 当前页 ${visibleCount}`;
}

function repairJobEmptyStateHtml(hasQuery, totalCount) {
  if (hasQuery && totalCount > 0) {
    return `<div class="job-empty">没有匹配当前搜索的失败任务。共加载 ${totalCount} 条失败记录。<button class="btn-sm" onclick="clearRepairJobSearch()">清除搜索</button></div>`;
  }
  return '<div class="job-empty">暂无失败任务。</div>';
}

function clearRepairJobSearch() {
  repairJobFilters.query = '';
  repairJobFilters.page = 1;
  if (repairJobFilterTimer) clearTimeout(repairJobFilterTimer);
  repairJobFilterTimer = null;
  if (['repair', 'failure_analysis'].includes(activeWorkflow)) showAiRepairCenter();
}

function setRepairJobSearch(value) {
  repairJobFilters.query = String(value || '');
  repairJobFilters.page = 1;
  if (repairJobFilterTimer) clearTimeout(repairJobFilterTimer);
  repairJobFilterTimer = setTimeout(() => {
    if (!['repair', 'failure_analysis'].includes(activeWorkflow)) return;
    showAiRepairCenter();
    const input = document.getElementById('repair-job-search');
    input?.focus();
    input?.setSelectionRange(input.value.length, input.value.length);
  }, 180);
}

function setRepairJobPage(page) {
  repairJobFilters.page = Math.max(1, Number(page) || 1);
  showAiRepairCenter();
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
  ENV_ISSUE: { tag: '环境问题', tone: 'warn', primary: { label: '查看环境建议', onClick: 'showPreflightDashboard()' } },
  UNKNOWN: { tag: '待人工复核', tone: '', primary: { label: '人工复核', onClick: 'showToast(\'已标记为人工复核，请在右侧查看完整分析\',\'success\')' } }
};

function failureTypeChip(type) {
  const meta = FAILURE_TYPE_META[type] || FAILURE_TYPE_META.UNKNOWN;
  return `<span class="failure-type-chip failure-${type.toLowerCase()}">${escapeHtml(meta.tag)}</span>`;
}

function aiRepairSummaryHtml(normalized) {
  const meta = FAILURE_TYPE_META[normalized.failureType] || FAILURE_TYPE_META.UNKNOWN;
  const reasons = String(normalized.reason || '').split(/[；;\n]/).map(s => s.trim()).filter(Boolean);
  return `
    <div class="review-panel ai-repair-analysis">
      <div class="section-head">
        <div>
          <h3>失败原因判断</h3>
          <p>先判断失败属于脚本、产品、环境还是不确定，再决定下一步。</p>
        </div>
        ${failureTypeChip(normalized.failureType)}
      </div>
      <div class="review-stats ai-repair-stat-grid">
        <div class="review-stat"><strong>${escapeHtml(repairFailureTypeText(normalized.failureType))}</strong><span>失败类型</span></div>
        <div class="review-stat"><strong>${escapeHtml(repairRiskText(normalized.riskLevel))}</strong><span>风险等级</span></div>
        <div class="review-stat"><strong>${normalized.canAutoRepair ? '可生成草稿' : '只做人工处理'}</strong><span>下一步</span></div>
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
  const noDraftReason = !canRepair
    ? `当前归因为${repairFailureTypeText(normalized.failureType)}，不能自动修 YAML`
    : '请先点击“生成修复草稿”';
  const fixedYamlReady = Boolean(aiFailureDraft?.fixedYaml);
  const actionButton = ({label, cls = '', enabled, onClick, unavailable}) => `<button class="btn-sm ${cls}" ${enabled
    ? `onclick="${onClick}"`
    : `disabled aria-disabled="true" title="${escapeHtml(unavailable)}"`}>${escapeHtml(label)}</button>`;
  const nextStep = !canRepair
    ? `${noDraftReason}。请人工复核，或生成飞书缺陷草稿。`
    : !fixedYamlReady
      ? '请先生成修复草稿；生成后才能复制、下载或人工确认替换。'
      : !canApplyDraft
      ? '修复 YAML 已生成，但草稿尚未进入可确认状态，请刷新草稿状态后再处理。'
      : '';
  const tabUnavailable = aiFailureDraft
    ? ''
    : 'disabled aria-disabled="true" title="请先选择失败任务并完成 AI 分析"';
  return `
    <div class="review-panel ai-repair-draft-panel">
      <div class="section-head">
        <div>
          <h3>YAML 修复草稿</h3>
          <p>这里先生成可检查的草稿；只有你点“人工确认替换”后，才会覆盖正式 YAML，并自动备份原文件。</p>
          <div class="job-meta">草稿状态：${escapeHtml(draftStatus)}${draftId ? ` · ${escapeHtml(draftId)}` : ''}</div>
        </div>
        <div class="review-actions">
          ${actionButton({label: '生成修复草稿', cls: 'ai', enabled: canRepair, onClick: 'generateRepairYamlFromAnalysis()', unavailable: noDraftReason})}
          ${actionButton({label: '复制草稿', enabled: fixedYamlReady, onClick: 'copyRepairDraftYaml()', unavailable: '暂无修复草稿可复制，请先生成修复草稿'})}
          ${actionButton({label: '下载草稿', enabled: fixedYamlReady, onClick: 'downloadAiGatewayYamlDraft()', unavailable: '暂无修复草稿可下载，请先生成修复草稿'})}
          ${actionButton({label: '人工确认替换', cls: 'success', enabled: canApplyDraft, onClick: `confirmApplyRepairDraft(${jsArg(draftId)})`, unavailable: '需要先生成并保存修复草稿，才能人工确认替换'})}
          ${actionButton({label: '拒绝草稿', cls: 'danger', enabled: Boolean(draftId), onClick: `rejectRepairDraft(${jsArg(draftId)})`, unavailable: '暂无可拒绝的修复草稿'})}
        </div>
      </div>
      <p id="repair-action-feedback" class="generate-hint" role="status" ${nextStep ? '' : 'hidden'}>${escapeHtml(nextStep)}</p>
      ${!canRepair ? `<div class="agent-risk show">当前归因为${escapeHtml(repairFailureTypeText(normalized.failureType))}，不建议自动改 YAML。请人工复核，或生成飞书缺陷草稿。</div>` : ''}
      ${riskHits.length ? `<div class="agent-risk show">该任务包含高风险动作：${escapeHtml(riskHits.join('、'))}。禁止自动执行，请人工确认后继续。</div>` : ''}
      <div class="agent-tabs">
        <button class="agent-tab ${aiFailureDraft?.activeTab === 'original' ? 'active' : ''}" onclick="showAiRepairTab('original')" ${tabUnavailable}>原始 YAML</button>
        <button class="agent-tab ${aiFailureDraft?.activeTab === 'fixed' ? 'active' : ''}" onclick="showAiRepairTab('fixed')" ${tabUnavailable}>修复 YAML</button>
        <button class="agent-tab ${aiFailureDraft?.activeTab === 'diff' || aiFailureDraft?.activeTab === 'validation' ? 'active' : ''}" onclick="showAiRepairTab('diff')" ${tabUnavailable}>Diff / 校验</button>
      </div>
      ${aiFailureDraft?.activeTab === 'diff'
        ? buildYamlDiffHtml(aiFailureDraft?.originalYaml || '', aiFailureDraft?.fixedYaml || '')
        : aiFailureDraft?.activeTab === 'validation'
          ? `<pre class="agent-artifact-box">${escapeHtml(validationText && validationText !== '{}' ? validationText : '暂无校验结果，请先生成修复 YAML。')}</pre>`
          : `<pre class="agent-artifact-box ${aiFailureDraft?.activeTab === 'fixed' ? 'yaml-fixed' : (aiFailureDraft?.activeTab === 'original' ? 'yaml-original' : '')}">${escapeHtml(aiRepairTabText(diffText, validationText))}</pre>`}
      <div class="generate-hint">修复草稿不会自动覆盖当前文件或基线；所有替换动作都会先进入人工确认。</div>
    </div>
  `;
}

function aiRepairTabText(diffText='', validationText='') {
  const tab = aiFailureDraft?.activeTab || 'analysis';
  if (tab === 'original') return aiFailureDraft?.originalYaml || '暂无原始 YAML。\n\n建议：从执行页选择带 YAML 的失败任务进入，或先在用例资产里打开对应 YAML，再点击 AI 分析。';
  if (tab === 'fixed') return aiFailureDraft?.fixedYaml || '暂无修复 YAML 草稿。\n\n如果失败类型是“脚本问题”，点击上方“生成修复草稿”。如果是产品缺陷、环境问题或待人工复核，不会自动改 YAML。';
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

function resolveAiRepairSelectedJob(failedJobs = []) {
  const matched = failedJobs.find(job => job.job_id === selectedRepairJobId) || null;
  if (matched || selectedRepairJobId) return matched;
  if (aiFailureDraft?.draftId) return null;
  return failedJobs[0] || null;
}

function aiRepairDraftContextHtml(selectedJob) {
  if (!aiFailureDraft?.draftId) return '';
  const title = aiFailureDraft.title || aiFailureDraft.file || 'YAML 修复草稿';
  const source = selectedRepairJobId
    ? (selectedJob ? `关联失败任务：${selectedJob.taskName || selectedJob.file || selectedRepairJobId}` : `关联失败任务 ${selectedRepairJobId} 不在当前加载范围`)
    : '未关联失败任务';
  return `<div class="ai-repair-source-context" role="status"><div><strong>当前从 Runner 打开的草稿：${escapeHtml(title)}</strong><span>${escapeHtml(source)}。右侧分析、原始 YAML 和修复 YAML 均属于这份草稿。</span></div><button class="btn-sm" onclick="returnToRunnerPendingActions()">返回 Runner 待我处理</button></div>`;
}

async function returnToRunnerPendingActions() {
  if (typeof activateWorkflow === 'function') await activateWorkflow('execute');
  else if (typeof setActiveWorkflow === 'function') setActiveWorkflow('execute');
  if (typeof setExecutionTab === 'function') setExecutionTab('debug');
}

function showAiRepairCenter() {
  activeWorkspaceMode = 'ai-repair';
  const area = document.getElementById('editor-area');
  if (!area) return;
  const hasSearch = Boolean(String(repairJobFilters.query || '').trim());
  const allFailedJobs = aiRepairFailedJobs({ignoreQuery: true});
  const failedJobs = hasSearch ? aiRepairFailedJobs() : allFailedJobs;
  const totalPages = Math.max(1, Math.ceil(failedJobs.length / REPAIR_JOB_PAGE_SIZE));
  repairJobFilters.page = Math.min(Math.max(1, repairJobFilters.page), totalPages);
  const visibleFailedJobs = failedJobs.slice((repairJobFilters.page - 1) * REPAIR_JOB_PAGE_SIZE, repairJobFilters.page * REPAIR_JOB_PAGE_SIZE);
  const selectedJob = resolveAiRepairSelectedJob(failedJobs);
  if (!selectedRepairJobId && selectedJob?.job_id) selectedRepairJobId = selectedJob.job_id;
  const normalized = aiFailureDraft ? aiFailureDraftNormalized() : normalizeFailureAnalysis('');
  area.className = 'editor-area';
  area.innerHTML = `
    <div class="review-page ai-repair-page">
      <div class="review-head">
        <div>
          <div class="workflow-kicker">AI 修复 · 选失败任务 / 判断原因 / 生成草稿 / 人工确认</div>
          <h2>AI修复工作台</h2>
          <p>从下方失败任务列表选择记录，先判断失败原因。只有脚本问题会生成 YAML 草稿；产品缺陷、环境问题和不确定问题只给处理建议，不自动改脚本。</p>
        </div>
        <div class="review-actions">
          <button class="btn-sm" onclick="loadJobs(true).then(() => showAiRepairCenter())">刷新失败任务</button>
          <button class="btn-sm primary" onclick="selectedRepairJobId && analyzeFailureFromJob(selectedRepairJobId, {renderPage:true})" ${selectedJob ? '' : 'disabled'}>AI分析失败原因</button>
          <button class="btn-sm" onclick="generateBugDraftFromAnalysis()" ${aiFailureDraft ? '' : 'disabled'}>生成飞书缺陷草稿</button>
        </div>
      </div>
      ${aiRepairDraftContextHtml(selectedJob)}
      <div class="review-grid ai-repair-grid">
        <div class="review-panel ai-repair-job-panel">
          <h3>失败任务列表</h3><div class="management-filter-scope">${escapeHtml(jobHistoryScopeText())}</div>
          <div class="management-filter-bar compact"><input id="repair-job-search" type="search" value="${escapeHtml(repairJobFilters.query)}" placeholder="搜索失败任务、模块或错误" oninput="setRepairJobSearch(this.value)"><span class="management-filter-count">${escapeHtml(repairJobCountText(allFailedJobs.length, failedJobs.length, visibleFailedJobs.length, hasSearch))}</span></div>
          ${visibleFailedJobs.length ? `<div class="yaml-task-nav-list ai-repair-job-list">${visibleFailedJobs.map(job => `
            <div class="yaml-task-nav-item ${job.job_id === selectedRepairJobId ? 'active' : ''}" onclick="selectRepairJob(${jsArg(job.job_id || '')})">
              <div class="yaml-task-nav-name">${escapeHtml(job.file || job.target_task_name || job.task_name || job.job_id || '失败任务')}</div>
              <div class="yaml-task-nav-meta">${escapeHtml([job.module, job.target_task_name, jobStatusText(job.status || 'failed')].filter(Boolean).join(' · '))}</div>
              <div class="yaml-task-nav-actions">
                <button onclick="event.stopPropagation(); analyzeFailureFromJob(${jsArg(job.job_id || '')}, {renderPage:true})">AI分析</button>
                <button onclick="event.stopPropagation(); focusJob(${jsArg(job.job_id || '')})">去执行页</button>
              </div>
            </div>
          `).join('')}</div>${failedJobs.length > REPAIR_JOB_PAGE_SIZE ? `<div class="management-pager"><span>第 ${repairJobFilters.page}/${totalPages} 页</span><div><button class="btn-sm" onclick="setRepairJobPage(${repairJobFilters.page - 1})" ${repairJobFilters.page <= 1 ? 'disabled' : ''}>上一页</button><button class="btn-sm" onclick="setRepairJobPage(${repairJobFilters.page + 1})" ${repairJobFilters.page >= totalPages ? 'disabled' : ''}>下一页</button></div></div>` : ''}` : repairJobEmptyStateHtml(hasSearch, allFailedJobs.length)}
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
