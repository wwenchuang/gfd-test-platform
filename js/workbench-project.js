// Start a mapped smoke run from a requirement, or inspect the evidence without starting a run.
window.WorkbenchProject = (() => {
  const intakePath = '/platform/requirement-design/intake';
  const statuses = {build_unavailable:'构建回执待核对 · 暂缓执行',source_unavailable:'需求或设计材料读取异常 · 暂缓测试',smoke_running:'冒烟执行中 · 后续回归等待结果',smoke_blocked:'冒烟未达继续条件 · 后续回归已暂停',design_completed:'测试设计已生成', design_failed:'测试设计生成失败',
    design_running:'正在设计测试用例',design_busy:'测试设计等待模型资源',
    awaiting_qa_build:'已转测 · 等待 QA 构建', design_changed_since_build:'已转测 · 构建对应的设计已变更',
    awaiting_design:'已转测 · 等待当前版本测试设计',awaiting_reviewed_baselines:'已转测 · 待绑定已发布基线',awaiting_mapping:'已接收，等待配置对应基线', blocked:'执行受阻', partial:'部分平台已提交', submitted:'已提交回归'};
  function line(parent, text, tag='p') {
    const node = document.createElement(tag);
    node.textContent = text;
    parent.append(node);
    return node;
  }
  function showReceipt(output, receipt, current) {
    output.replaceChildren();
    const batches = receipt.batches || [];
    let summary = statuses[receipt.status] || '状态待核对';
    if (receipt.status === 'submitted' && batches.length) {
      if (batches.some(batch => batch.status === 'failed')) summary = '回归出现失败';
      else if (batches.every(batch => batch.status === 'passed')) summary = '本次回归通过';
      else if (batches.some(batch => batch.status === 'running')) summary = '回归执行中';
    }
    line(output, receipt.title + ' · ' + summary);
    const build=receipt.build_context;
    if(build) line(output,[build.phase==='smoke'?'冒烟':'功能回归','QA',build.platform==='ios'?'iOS':'Android',
      build.version_name+' ('+build.version_code+')','构建 '+build.build_id].join(' · '));
    line(output, (receipt.origin === 'integration' ? '飞书项目' : '工作台登记') + ' · ' + receipt.requirement_id + (/^[a-f0-9]{64}$/.test(build?.requirement_revision||receipt.revision)?'':' · '+(build?.requirement_revision||receipt.revision)));
    const designer = () => current() && hasPermission('platform.configure') && hasPermission('ui.view')
      && (currentAccessProfile?.is_superuser === true || currentAccessProfile?.scope?.ui_apps === '*');
    if (receipt.design_cases?.length) {
      const details=line(output,'','details');
      line(details,'查看测试用例（'+receipt.design_cases.length+' 条）','summary');
      for(const c of receipt.design_cases){
        line(details,c.title,'h4');
        line(details,'前置条件　'+c.precondition);
        const steps=line(details,'','ol');
        for(const step of c.steps || []) line(steps,step,'li');
        line(details,'预期结果　'+c.expected);
        if(c.plan?.evidence) line(details,'验收证据　'+c.plan.evidence);
      }
    }
    if (receipt.status === 'design_completed' && designer() && /^[a-f0-9]{64}$/.test(receipt.design_source)) {
      const button = line(output,'打开需求设计详情','button');
      button.type='button';
      button.onclick=() => {
        const panel=output.closest('[data-practice-workbench]')?.querySelector('[data-requirement-design]');
        if (!designer() || !panel) return;
        panel.dispatchEvent(new CustomEvent('use-intake-source',{detail:receipt.design_source}));
        panel.scrollIntoView({block:'start'});
      };
    }
    if (receipt.status === 'awaiting_mapping' && designer()) {
      const button = line(output,'用验收条件准备测试设计','button');
      button.type = 'button';
      button.onclick = async () => {
        if (!designer() || button.disabled) return;
        button.disabled = true;
        try {
          const {source,event_id,app_id} = receipt;
          const result = await apiRequest(intakePath + '/design-source',{method:'POST',body:{source,event_id,app_id}});
          const panel = output.closest('[data-practice-workbench]')?.querySelector('[data-requirement-design]');
          if (!designer() || !output.isConnected) return;
          if (!panel || !/^[a-f0-9]{64}$/.test(result.source_id)) throw Error();
          panel.dispatchEvent(new CustomEvent('use-intake-source',{detail:result.source_id}));
          panel.scrollIntoView({block:'start'});
        } catch (_) { if (designer()) line(output,'准备材料失败，请重试。'); }
        finally { button.disabled = false; }
      };
    }
    for (const batch of receipt.batches || []) {
      if (!/^uib_[a-f0-9]{32}$/.test(batch.ui_batch_id)) { line(output, batch.platform + ' · 已提交'); continue; }
      const platform = {android:'Android',ios:'iOS'}[batch.platform] || batch.platform;
      const state = {queued:'排队中',running:'执行中',passed:'通过',failed:'失败',cancelled:'已取消',interrupted:'已中断'}[batch.status];
      const link = line(output, [platform,state,'查看回归证据'].filter(Boolean).join(' · '), 'a');
      link.href = '?ui_batch=' + encodeURIComponent(batch.ui_batch_id);
      const review = batch.defect_review;
      if (!review) continue;
      const labels = {no_failed_attempts:'已执行用例无失败',configuration_required:'待配置飞书项目',permission_required:'需缺陷登记权限',owner_unresolved:'需求开发负责人待确认',needs_evidence:'证据不足，继续调查',receipt_unknown:'飞书回执待核对',rejected:'飞书拒绝创建',submitted:'缺陷已登记',mixed:'各失败项处理结果不同'};
      if (review.status !== 'no_failed_attempts' || batch.status !== 'passed') line(output, labels[review.status] || '调查结果待核对');
      for (const item of review.attempts || []) {
        if (item.status === 'submitted' && Number.isSafeInteger(item.issue_id)) line(output, '飞书项目缺陷 #' + item.issue_id);
      }
      if (review.investigation) {
        const investigation = {queued:'排队中',running:'调查中',completed:'调查完成',succeeded:'调查完成',failed:'调查失败',busy:'等待调查资源'};
        line(output, 'Agent · ' + (investigation[review.investigation.status] || '查看报告中的调查进度'));
      }
    }
    for (const blocker of receipt.blockers || []) {
      line(output, blocker.platform + ' · 未启动' + (blocker.code ? '（' + blocker.code + '）' : ''));
    }
  }
  function attachIntake(panel,current,reload) {
    const allowed = () => current() && hasPermission('ui.execute') && hasPermission('ui.view');
    if (!allowed()) return;
    const box = document.createElement('details');
    box.innerHTML = '<summary>接收需求并匹配冒烟用例</summary><form><label>测试应用<select name="app_id" required><option value="">选择应用</option></select></label><label>需求编号<input name="requirement_id" required maxlength="64" pattern="[A-Za-z0-9_.:-]+"></label><label>需求版本<input name="revision" required maxlength="48" pattern="[A-Za-z0-9_.:-]+"></label><label>需求标题<input name="title" required maxlength="160"></label><label class="wide-field">验收条件（每行一条）<textarea name="criteria" rows="3" required></textarea></label><button type="submit">接收并执行已映射用例</button></form><output aria-live="polite"></output>';
    panel.append(box);
    const form = box.querySelector('form'), output = box.querySelector('output');
    const apps = form.elements.namedItem('app_id');
    let loaded = false, pending = false;
    box.addEventListener('toggle', async () => {
      if (!box.open || loaded || !allowed()) return;
      loaded = true;
      try {
        const data = await apiRequest('/ui-executions/catalog');
        if (!allowed()) return;
        for (const app of data.apps || []) apps.add(new Option(app.name, app.app_id));
        if (!apps.options[1]) output.textContent = '当前账号没有可选择的应用。';
      } catch (_) { loaded = false; if (allowed()) output.textContent = '应用列表读取失败，重新展开可重试。'; }
    });
    form.onsubmit = async event => {
      event.preventDefault();
      if (!allowed() || pending || !form.reportValidity()) return;
      const values = new FormData(form);
      const id = values.get('requirement_id').trim(), revision = values.get('revision').trim();
      const app = values.get('app_id');
      const body = {source:'workbench', event_id:'ui.' + crypto.getRandomValues(new Uint32Array(4)).join('.'), app_id:app, requirement_id:id, revision,
        title:values.get('title').trim(), acceptance_criteria:values.get('criteria').split(/\r?\n/).map(s=>s.trim()).filter(Boolean)};
      pending = true;
      form.querySelector('button').disabled = true;
      output.textContent = '正在接收需求并检查映射…';
      try {
        const data = await apiRequest(intakePath, {method:'POST', body:JSON.stringify(body)});
        if (allowed()) { showReceipt(output, data.intake, current); reload?.(); }
      } catch (err) {
        if (allowed()) output.textContent = err.message || '接收失败，可按原需求编号和版本重试。';
      } finally { pending = false; form.querySelector('button').disabled = false; }
    };
  }
  function attachRecent(panel,current) {
    const allowed = () => current() && hasPermission('ui.view');
    if (!allowed()) return;
    const list = document.createElement('div');
    const refresh = line(panel,'刷新需求进度','button');
    refresh.type = 'button';
    list.setAttribute('aria-live','polite');
    panel.append(list);
    let sequence = 0;
    const load = async () => {
      if (!allowed()) return;
      const request = ++sequence;
      list.textContent = '正在读取最近需求…';
      try {
        const data = await apiRequest(intakePath + '/recent?limit=10');
        if (!allowed() || request !== sequence) return;
        list.replaceChildren();
        for (const receipt of data.items || []) {
          const row = document.createElement('div');
          row.className = 'workbench-receipt';
          showReceipt(row, receipt, current);
          list.append(row);
        }
        if (!list.childElementCount) line(list,'暂无可查看的需求回归记录。接入飞书项目前，也可以在这里登记需求。');
      } catch (_) { if (allowed() && request === sequence) list.textContent = '需求进度暂时无法读取，可点击刷新重试。'; }
    };
    refresh.onclick = load;
    load();
    return load;
  }
  function attachLogs(panel,current) {
    const allowed = () => current() && hasPermission('platform.configure');
    if (!allowed()) return;
    const box = document.createElement('details');
    box.innerHTML = '<summary>服务日志巡检</summary><p>查看测试平台服务最近一次采集。智小白 3D 业务日志等待接入。</p><button type="button">刷新采集状态</button><output aria-live="polite"></output>';
    panel.append(box);
    const output = box.querySelector('output');
    let sequence = 0;
    const refresh = async () => {
      if (!allowed()) return;
      const request = ++sequence;
      output.textContent = '正在读取巡检状态…';
      try {
        const data = await apiRequest('/platform/log-monitor/status');
        if (!allowed() || request !== sequence) return;
        output.replaceChildren();
        const label = {current:'最近有采集',stale:'采集已过期',unavailable:'尚无可用采集'};
        line(output,label[data.status] || '采集状态待核对');
        for (const source of data.sources || []) {
          const coverage = {complete:'采集完整',partial:'采集不完整',source_unavailable:'来源不可用',unobserved:'未采集'};
          const time = source.window_end ? new Date(source.window_end*1000).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'}) : '';
          const names = {'midscene-task.service':'工作台后端','midscene-api-worker.service':'接口任务执行器','midscene-api-thinking-worker.service':'接口 AI 执行器','midscene-api-scheduler.service':'接口定时调度'};
          const errors = Object.entries(source.matches || {}).map(([key,n]) => ({http_5xx:'HTTP 5xx',traceback:'异常堆栈',error:'错误日志'}[key] || key) + ' ' + n + ' 行');
          line(output,[names[source.unit] || source.unit,coverage[source.coverage] || '待核对',source.error_active === true ? '有错误告警' : '',...errors,time].filter(Boolean).join(' · '));
        }
      } catch (_) { if (allowed() && request === sequence) output.textContent = '采集状态暂时无法读取，可点击刷新重试。'; }
    };
    box.addEventListener('toggle',() => {if (box.open) refresh();});
    box.querySelector('button').onclick = refresh;
  }
  function attachLatest(section,allowed) {
    const list = document.createElement('div');
    list.className = 'workbench-recent-runs';
    section.append(list);
    const load = async () => {
      if (!allowed()) return;
      list.textContent = '正在读取我的回归报告…';
      try {
        const data = await apiRequest('/ui-executions?scope=own&limit=20');
        if (!allowed() || !section.isConnected) return;
        list.replaceChildren();
        const terminal = (data.items || []).filter(row=>row.execution_settled && /^uib_[a-f0-9]{32}$/.test(row.ui_batch_id));
        for (const row of terminal.slice(0,3)) {
          const state = {passed:'通过',failed:'失败',cancelled:'已取消',interrupted:'已中断'}[row.status] || '已结束';
          const names = (row.case_names || []).slice(0,1).join('') || row.app_name || 'UI 回归';
          const device = (row.devices || []).map(d=>d.name).filter(Boolean).join('、');
          const when = new Date(row.created_ns/1000000).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'});
          const link = line(list,[names+(row.case_count>1?' 等 '+row.case_count+' 条':''),device,state,when].filter(Boolean).join(' · '),'a');
          link.href = '?ui_batch=' + row.ui_batch_id;
        }
        if (!terminal.length) line(list,'最近 20 次执行中暂无已结束的回归。');
      } catch (_) {
        if (!allowed() || !section.isConnected) return;
        list.textContent = '最近回归暂时无法读取。';
        line(list,'重试','button').onclick = load;
      }
    };
    load();
  }
  return {attachLatest,attach(page,current) {
    const panel = document.createElement('section');
    panel.className = 'workbench-project';
    page.append(panel);
    line(panel,'需求回归与巡检','h2');
    if (hasPermission('ui.view')) {
      const card = document.createElement('article');
      line(card,'需求回归','h2');
      line(card,'查看飞书项目需求、匹配的基线和两端回归结果。');
      const action = line(card,'打开需求回归','button');
      action.type = 'button';
      action.onclick = () => {
        if (!current() || !hasPermission('ui.view')) return;
        const details = panel.querySelector('details');
        if (hasPermission('ui.execute') && details) details.open = true;
        panel.tabIndex = -1;
        panel.focus({preventScroll:true});
        panel.scrollIntoView({block:'start'});
      };
      page.querySelector('.ui-practice-tasks')?.prepend(card);
    }
    const refresh = attachRecent(panel,current);
    attachIntake(panel,current,refresh);
    attachLogs(panel,current);
    if (!panel.querySelector('details,button')) panel.remove();
  }};
})();
