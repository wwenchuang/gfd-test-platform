// The authenticated shell keeps one panel while the editor re-renders its current workflow.
window.RegressionPatrol = (() => {
  const panel = document.getElementById('regression-patrol');
  const results = panel.querySelector('[data-patrol-results]');
  const refresh = panel.querySelector('button');
  const count = panel.querySelector('[data-patrol-count]');
  const states = {missed: '未按时启动', running: '执行中', completed: '执行完成', pending: '待执行',
    failed: '执行失败', overdue: '执行进度超时', disabled: '已停用'};
  const investigations = {queued: '排队中', running: '调查中', completed: '已完成', limited: '证据有限',
    failed: '失败', cancelled: '已取消', interrupted: '已中断'};
  let displayedContext = '';
  let requestNumber = 0;

  function context() {
    if (!['ai_practice', 'ui_start', 'dashboard'].includes(activeWorkflow)
        || document.getElementById('app').style.display !== 'flex'
        || !currentAccessProfile?.user_id || !sessionToken() || !hasPermission('api.view')) return '';
    return JSON.stringify([activeWorkflow, sessionToken(), currentAccessProfile]);
  }

  function reset() {
    requestNumber++;
    displayedContext = '';
    panel.hidden = true;
    count.textContent = '';
    results.replaceChildren();
  }

  function addTime(host, label, value) {
    if (!value) return;
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return;
    const time = document.createElement('time');
    time.dateTime = value;
    time.textContent = date.toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false});
    const field = document.createElement('span');
    field.append(label + ' ', time);
    host.append(field);
  }

  function executionLink(id, label) {
    const link = document.createElement('a');
    link.href = '/api-test/#/runs?executionId=' + encodeURIComponent(id);
    link.textContent = label;
    return link;
  }

  function render(item) {
    const row = document.createElement('article');
    const name = document.createElement('strong');
    name.textContent = item.job_name;
    const state = document.createElement('span');
    state.className = 'patrol-state';
    state.dataset.state = item.state;
    state.textContent = Object.hasOwn(states, item.state) ? states[item.state] : '状态待核对';
    const heading = document.createElement('div');
    heading.className = 'patrol-job';
    heading.append(name, state);
    if (Number.isSafeInteger(item.case_count) && item.case_count >= 0) {
      const cases = document.createElement('span');
      cases.textContent = '本次 ' + item.case_count + ' 条用例';
      heading.append(cases);
    }
    const summary = document.createElement('p');
    summary.textContent = item.summary;
    const meta = document.createElement('div');
    meta.className = 'patrol-meta';
    addTime(meta, '计划时间（北京）', item.expected_at);
    addTime(meta, '查询时间（北京）', item.checked_at);
    if (item.inspected_at) addTime(meta, '上次巡检（北京）', item.inspected_at);
    else meta.append('暂无巡检时间记录');
    const progress = document.createElement('span');
    const status = item.investigation_status;
    if (status === 'not_needed') progress.textContent = '本次巡检已完成，无需调查';
    else if (status) progress.textContent = 'AI 调查 · ' + (Object.hasOwn(investigations, status) ? investigations[status] : '状态待核对');
    else if (item.execution_id) progress.textContent = '本次巡检暂无回执';
    if (progress.textContent) meta.append(progress);
    if (item.execution_id) meta.append(executionLink(item.execution_id, '查看本次执行'));
    if (item.inspected_execution_id && item.inspected_execution_id !== item.execution_id) {
      meta.append(executionLink(item.inspected_execution_id, '查看上次巡检'));
    }
    row.append(heading, summary, meta);
    return row;
  }

  async function load() {
    const key = context();
    if (!key) { reset(); return; }
    const request = ++requestNumber;
    const current = () => request === requestNumber && key === context();
    refresh.disabled = true;
    count.textContent = '';
    results.textContent = '正在读取巡检状态…';
    try {
      const response = await nativeFetch('/api/api-testing/v1/patrol/status', {
        headers: authHeaders(), cache: 'no-store', signal: AbortSignal.timeout(10000),
      });
      if (!current()) return;
      if (!response.ok) {
        results.textContent = response.status === 401 ? '登录已过期，请重新登录。'
          : response.status === 403 ? '当前账号没有巡检查看权限。' : '巡检状态暂时无法读取，请稍后刷新。';
        return;
      }
      const {data} = await response.json();
      if (!current()) return;
      if (!Array.isArray(data?.items)) throw new Error('invalid patrol response');
      count.textContent = data.items.length + ' 项任务';
      if (data.items.length) results.replaceChildren(...data.items.map(render));
      else results.textContent = '当前账号范围内没有配置巡检的定时回归。';
    } catch {
      if (current()) results.textContent = '巡检状态暂时无法读取，请稍后刷新。';
    } finally {
      if (current()) refresh.disabled = false;
    }
  }

  function sync() {
    const key = context();
    if (key === displayedContext) return;
    reset();
    if (!key) return;
    displayedContext = key;
    panel.hidden = false;
    void load();
  }

  refresh.onclick = load;
  return {sync, reset};
})();
