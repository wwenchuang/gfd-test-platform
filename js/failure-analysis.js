// Shared by the authenticated UI report and the API report component.
(() => {
  const activeStatuses = new Set(['queued', 'running']);
  const statusLabels = { queued: '等待调查', running: '正在调查', completed: '调查完成', limited: '调查受限', failed: '调查失败', cancelled: '已停止', interrupted: '调查已中断' };
  const toolLabels = { get_failed_steps: '读取失败步骤', get_request_trace: '核对请求与回执', get_device_logs: '核对设备日志', get_workbench_log_window: '核对工作台日志窗口', compare_last_pass: '对照同条件成功记录' };
  const missingLabels = {
    evidence_output_truncated: '证据量超过本次读取上限，仅保留了部分记录。',
    secret_context_incomplete: '缺少完整的脱敏上下文，相关证据暂未展示。',
    step_progress_not_recorded: '该失败用例未记录逐步执行进度，需要补充步骤日志或执行回放。',
    failed_cases_truncated_at_25: '本次仅列出前 25 条失败用例，其余失败尚未调查。',
    failed_case_not_recorded: '当前记录中没有可读取的失败用例，请核对执行状态和结果。',
    request_not_recorded: '本次执行未保存请求内容，需要补充该次请求记录。',
    response_not_recorded: '本次执行未保存响应内容，需要补充该次服务回执。',
    trace_not_recorded: '本次执行未保存调用轨迹，暂不能核对完整请求链路。',
    network_trace_not_collected: '本次 UI 执行未采集网络请求与回执，需要同次运行的网络证据。',
    runner_logs_not_recorded: '本次 UI 执行未保存 Runner 日志，需要补充对应执行日志。',
    environment_identity_unavailable: '本次记录缺少完整的环境信息，暂不能与历史结果进行有效对照。',
    run_timestamp_not_recorded: '本次记录缺少运行时间，无法确定哪些成功记录早于本次。',
    prior_comparable_pass_not_found: '没有找到同用例版本、同环境且早于本次的成功记录，无法完成有效对照。',
  };
  const stopLabels = {
    token_budget: '已达到本次用量上限', round_budget: '已达到调查轮次上限', tool_budget: '已达到取证次数上限',
    time_budget: '已达到本次调查时间上限', message_budget: '本次调查上下文已达到上限', evidence_budget: '本次证据量已达到上限',
    repeated_tool_limit: '重复取证已达到上限，已保留取得的证据', cancelled: '用户已停止调查',
    missing_key: '未配置千问访问凭证，请联系管理员配置后重试', model_timeout: '模型响应超时，可保留现有证据后重试',
    model_request_failed: '模型服务请求失败，请稍后重试', model_http_error: '模型服务暂不可用，请稍后重试',
    model_http_401: '千问鉴权失败，请管理员核对服务端凭证与地域后重试',
    model_http_403: '千问访问被拒绝，请管理员核对模型权限与服务开通状态后重试',
    model_http_429: '千问请求受到限流，请稍后重试',
    unsupported_endpoint: '模型服务地址不可用，请联系管理员核对配置', invalid_model: '模型配置无效，请联系管理员核对配置',
    access_denied: '当前无权继续读取执行证据', tool_read_failed: '执行证据读取失败，尚不能完成调查',
    no_evidence: '没有取得可引用的执行证据，原因未知', incomplete_model_output: '模型输出不完整，已保留取得的证据',
    invalid_final_output: '模型结论未通过结构校验，已保留取得的证据', invalid_evidence_references: '模型引用未通过证据校验，未采纳该结论',
    invalid_tool: '模型提出了不支持的取证操作，调查已停止', invalid_tool_arguments: '模型取证参数未通过校验，调查已停止',
    invalid_tool_call_id: '模型取证调用未通过校验，调查已停止', invalid_tool_result: '取证结果未通过校验，调查已停止',
    conflicting_evidence: '证据记录存在冲突，需人工核对', invalid_model_response: '模型响应未通过校验，请重试',
    model_response_too_large: '模型响应超出本次读取限制', model_completed: '模型已完成本次调查',
  };
  const list = value => Array.isArray(value) ? value : [];
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  };
  function requestId() {
    if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    // HTTP deployments have getRandomValues but may not expose randomUUID.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
    return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
  }
  function safeHref(value) {
    if (typeof value !== 'string' || !value.trim()) return '';
    try {
      const url = new URL(value, window.location.href);
      return ['http:', 'https:'].includes(url.protocol) && url.origin === window.location.origin && !url.username && !url.password
        ? url.pathname + url.search + url.hash : '';
    } catch (_) { return ''; }
  }
  function missingList(items) {
    const ul = make('ul');
    for (const value of items) {
      const raw = String(value), code = /^[A-Za-z][A-Za-z0-9_.-]*(?::[^\r\n]*)?$/.test(raw) ? raw.split(':')[0] : '';
      const li = make('li'), message = make('span', code ? missingLabels[code] || '该项执行证据尚未记录。' : raw);
      message.dataset.analysisMissingMessage = ''; li.append(message);
      if (code) {
        const detail = make('details', undefined, 'failure-analysis-missing-code'); detail.dataset.analysisMissingCode = '';
        detail.append(make('summary', '技术详情'), make('code', raw)); li.append(detail);
      }
      ul.append(li);
    }
    return ul;
  }
  function mount(host, options) {
    let live = true, busy = false, denied = false, task = null, timer = null, controller = null;
    let retryAction = 'read', pendingRequestId = '', error = '', signature = '';
    const identity = options.identity(), token = sessionStorage.getItem('sessionToken');
    host.classList.add('failure-analysis');
    host.innerHTML = '<header class="failure-analysis-head"><div><p class="failure-analysis-eyebrow">AI 失败调查</p><h3>从执行证据找原因</h3><p>按本次失败自主选择取证路径，保留事实、候选原因和待补证据。</p></div><span data-analysis-status role="status">正在读取已有调查…</span></header><div class="failure-analysis-actions"><button type="button" data-analysis-start>AI 分析失败</button><button type="button" data-analysis-cancel hidden>停止调查</button><button type="button" data-analysis-refresh>刷新调查</button><button type="button" data-analysis-retry hidden>重试</button></div><p data-analysis-error role="alert" hidden></p><p data-analysis-meta></p><div data-analysis-content></div>';
    const q = selector => host.querySelector(selector);
    const visible = () => !document.hidden && !host.closest('[hidden]');
    function clearPrivate(message) {
      denied = true; task = null; pendingRequestId = ''; signature = ''; error = ''; clearTimeout(timer); controller?.abort();
      q('[data-analysis-content]').replaceChildren(); q('[data-analysis-meta]').textContent = '';
      q('[data-analysis-error]').textContent = ''; q('[data-analysis-error]').hidden = true;
      q('[data-analysis-status]').textContent = message;
      for (const button of host.querySelectorAll('button')) button.disabled = true;
      q('[data-analysis-cancel]').hidden = true; q('[data-analysis-retry]').hidden = true;
    }
    function valid() {
      if (!live || !host.isConnected) return false;
      if (!identity) { clearPrivate('请登录后打开本次报告'); return false; }
      if (options.identity() !== identity || sessionStorage.getItem('sessionToken') !== token) {
        clearPrivate('登录状态已变化，请重新打开本次报告'); return false;
      }
      return true;
    }
    function controls() {
      const running = activeStatuses.has(task?.status);
      q('[data-analysis-start]').hidden = running;
      q('[data-analysis-start]').disabled = busy || denied || Boolean(error);
      q('[data-analysis-start]').textContent = pendingRequestId ? '核对上次提交' : task ? '重新分析' : 'AI 分析失败';
      q('[data-analysis-cancel]').hidden = !running || denied;
      q('[data-analysis-cancel]').disabled = busy || denied;
      q('[data-analysis-refresh]').disabled = busy || denied;
      q('[data-analysis-retry]').hidden = !error || denied;
      q('[data-analysis-retry]').disabled = busy || denied;
      q('[data-analysis-error]').hidden = !error;
      q('[data-analysis-error]').textContent = error;
      host.setAttribute('aria-busy', String(busy));
    }
    function section(title, className) {
      const box = make('section', undefined, className || 'failure-analysis-section');
      box.append(make('h4', title)); return box;
    }
    function render() {
      if (denied) return;
      q('[data-analysis-status]').textContent = task ? statusLabels[task.status] || '状态待确认' : '尚未发起调查';
      q('[data-analysis-status]').dataset.state = task?.status || 'empty';
      const meta = [];
      if (Number.isFinite(task?.elapsed_ms)) meta.push(`调查用时 ${(task.elapsed_ms / 1000).toFixed(1)} 秒`);
      if (task?.model) meta.push(`模型 ${task.model}`);
      const usage = task?.usage;
      if (usage?.complete === false) meta.push(`供应商用量不完整 · 已报告 ${usage.total_tokens || 0} Token${Number.isFinite(usage.estimated_tokens) ? ` · 未报告部分预算估算 ${usage.estimated_tokens} Token` : ''}`);
      else if (Number.isFinite(usage?.total_tokens)) meta.push(`Token ${usage.total_tokens}${Number.isFinite(usage.prompt_tokens) && Number.isFinite(usage.completion_tokens) ? `（输入 ${usage.prompt_tokens} / 输出 ${usage.completion_tokens}）` : ''}`);
      else if (task) meta.push('模型用量未提供');
      q('[data-analysis-meta]').textContent = meta.join(' · ');
      const nextSignature = JSON.stringify([task?.status, task?.result, task?.evidence, task?.trace, task?.stop_reason, task?.error]);
      if (nextSignature === signature) return;
      signature = nextSignature;
      const content = q('[data-analysis-content]');
      const open = new Set([...content.querySelectorAll('details[open]')].map(node => node.dataset.analysisEvidence || node.dataset.analysisTrace));
      const focused = content.contains(document.activeElement) ? document.activeElement.dataset.analysisRef : null;
      const focusedDetails = content.contains(document.activeElement) && document.activeElement.tagName === 'SUMMARY' ? document.activeElement.parentElement : null;
      const focusEvidence = focusedDetails?.dataset.analysisEvidence, focusTrace = focusedDetails?.dataset.analysisTrace;
      content.replaceChildren();
      if (!task) { content.append(make('p', '点击后，AI 将读取本次失败步骤，按需要核对请求和历史成功记录。', 'failure-analysis-empty')); return; }
      const evidence = new Map(list(task.evidence).filter(item => item && typeof item.id === 'string').map(item => [item.id, item]));
      const result = task.result || {};
      const overview = section('失败概况');
      overview.append(make('p', result.summary || (activeStatuses.has(task.status) ? '正在收集与核对证据，结论尚未生成。' : '尚未形成可验证的结论。')));
      if (task.stop_reason || task.error) overview.append(make('p', task.error || stopLabels[task.stop_reason] || (/^model_http_\d{3}$/.test(task.stop_reason) ? '模型服务返回错误，请稍后重试' : '调查已停止，已取得的证据保留'), 'failure-analysis-note'));
      content.append(overview);
      const grid = make('div', undefined, 'failure-analysis-grid');
      for (const [key, title, empty] of [['facts', '已确认事实', '尚无有证据支持的事实。'], ['hypotheses', '可能原因', '证据不足，原因未知。'], ['recommendations', '建议动作', '等待调查结果或补充证据。']]) {
        const box = section(title), ul = make('ul');
        if (key === 'hypotheses') box.append(make('p', '候选解释，需要进一步验证。', 'failure-analysis-note'));
        for (const item of list(result[key])) {
          if (!item || typeof item.text !== 'string') continue;
          const refs = [...new Set(list(item.evidence_ids))].filter(id => evidence.has(id));
          if (key === 'facts' && !refs.length) continue;
          const li = make('li'); li.append(make('p', item.text));
          for (const id of refs) {
            const button = make('button', evidence.get(id).title || id, 'failure-analysis-ref');
            button.type = 'button'; button.dataset.analysisRef = id;
            button.onclick = () => {
              if (!valid()) return;
              const target = [...content.querySelectorAll('[data-analysis-evidence]')].find(node => node.dataset.analysisEvidence === id);
              if (target) { target.open = true; target.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' }); target.querySelector('summary')?.focus(); }
            };
            li.append(button);
          }
          ul.append(li);
        }
        box.append(ul.childElementCount ? ul : make('p', empty, 'failure-analysis-empty')); grid.append(box);
      }
      content.append(grid);
      const missing = section('还缺哪些证据', 'failure-analysis-section failure-analysis-missing');
      if (list(result.missing_evidence).length) missing.append(missingList(result.missing_evidence));
      else missing.append(make('p', activeStatuses.has(task.status) ? '调查进行中，证据覆盖范围尚未确认。' : '未报告额外缺口；请结合下方实际取证范围核对。'));
      content.append(missing);
      const trace = section('调查记录');
      const steps = list(task.trace);
      if (!steps.length) trace.append(make('p', task.status === 'queued' ? '等待后台开始调查，尚未读取证据。' : '尚无已记录的取证动作。', 'failure-analysis-empty'));
      for (const [index, step] of steps.entries()) {
        const row = make('details', undefined, 'failure-analysis-trace'); row.dataset.analysisTrace = String(index); row.open = open.has(String(index));
        const tool = step.tool || step.name;
        const label = toolLabels[tool] || (step.kind === 'model' ? '核对证据与选择下一步' : tool || '调查动作');
        const state = ({ completed: '已完成', ok: '已完成', running: '进行中', failed: '失败', error: '失败', rejected: '已拒绝' })[step.status] || step.status || '已记录';
        row.append(make('summary', `第 ${step.round ?? index + 1} 轮 · ${label} · ${state}${Number.isFinite(step.elapsed_ms) ? ` · ${step.elapsed_ms} ms` : ''}`));
        if (step.summary) row.append(make('p', step.summary));
        if (step.kind === 'tool' || tool) {
          if (Array.isArray(step.evidence_ids)) row.append(make('p', `取得 ${step.evidence_ids.filter(id => evidence.has(id)).length} 份证据`));
          if (list(step.missing).length) row.append(missingList(step.missing));
          row.append(make('pre', JSON.stringify(step.arguments || {}, null, 2)));
        }
        trace.append(row);
      }
      content.append(trace);
      const evidenceBox = section(`引用证据（${evidence.size} 份）`);
      for (const item of evidence.values()) {
        const details = make('details', undefined, 'failure-analysis-evidence'); details.dataset.analysisEvidence = item.id; details.open = open.has(item.id);
        details.append(make('summary', item.title || '执行证据'));
        details.append(make('pre', typeof item.data === 'string' ? item.data : JSON.stringify(item.data ?? {}, null, 2)));
        const href = safeHref(item.href);
        if (href) { const link = make('a', '打开原始执行记录'); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; details.append(link); }
        evidenceBox.append(details);
      }
      if (!evidence.size) evidenceBox.append(make('p', '尚未取得可引用证据。', 'failure-analysis-empty'));
      content.append(evidenceBox);
      if (focused) [...content.querySelectorAll('[data-analysis-ref]')].find(node => node.dataset.analysisRef === focused)?.focus();
      else if (focusEvidence !== undefined || focusTrace !== undefined) [...content.querySelectorAll('details')].find(node => focusEvidence !== undefined ? node.dataset.analysisEvidence === focusEvidence : node.dataset.analysisTrace === focusTrace)?.querySelector('summary')?.focus();
    }
    function schedule() {
      clearTimeout(timer);
      if (!live || denied || error || !task || !activeStatuses.has(task.status)) return;
      timer = setTimeout(() => refresh(), 2000);
    }
    async function run(action) {
      if (!valid() || denied || busy || (action === 'read' && !visible())) return;
      clearTimeout(timer); busy = true; error = ''; controls();
      controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 30000);
      try {
        let path = '/failure-analysis/tasks', body;
        if (action === 'start') { pendingRequestId ||= requestId(); body = { source: options.source, run_id: options.runId, request_id: pendingRequestId }; }
        else if (action === 'cancel') { path = '/failure-analysis/cancel'; body = { id: task.id }; }
        else path += '?' + new URLSearchParams(task?.id ? { id: task.id } : { source: options.source, run_id: options.runId });
        const response = await options.request(path, { method: action === 'read' ? 'GET' : 'POST', ...(body ? { body } : {}), signal: controller.signal });
        if (!valid()) return;
        if (!response?.ok || !Object.prototype.hasOwnProperty.call(response, 'task')) throw Error(response?.error || '调查读取失败，请重试');
        const next = response.task;
        if (next && (next.source !== options.source || next.run_id !== options.runId)) throw Error('调查记录与当前执行不匹配，请重新打开报告');
        task = next;
        if (action === 'start') pendingRequestId = '';
        render();
      } catch (failure) {
        if (!valid()) return;
        if ([401, 403, 404].includes(failure.status)) clearPrivate('当前无法访问这份调查，请核对账号和报告权限');
        else { error = failure.name === 'AbortError' ? '调查请求超时，请重试核对结果。' : failure.message || '调查请求失败，请重试。'; retryAction = action; }
      } finally {
        clearTimeout(timeout); busy = false;
        if (valid()) { controls(); schedule(); }
      }
    }
    async function refresh() { return run('read'); }
    function onVisibility() { clearTimeout(timer); if (visible()) refresh(); }
    function dispose() { live = false; clearTimeout(timer); controller?.abort(); document.removeEventListener('visibilitychange', onVisibility); window.removeEventListener('focus', onVisibility); host.replaceChildren(); }
    q('[data-analysis-start]').onclick = () => run('start');
    q('[data-analysis-cancel]').onclick = () => run('cancel');
    q('[data-analysis-refresh]').onclick = refresh;
    q('[data-analysis-retry]').onclick = () => run(retryAction);
    document.addEventListener('visibilitychange', onVisibility); window.addEventListener('focus', onVisibility);
    refresh(); return { refresh, dispose };
  }
  window.FailureAnalysis = { mount };
})();
