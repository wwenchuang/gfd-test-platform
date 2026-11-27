(() => {
  'use strict';
  const platformName = {android:'Android', ios:'iOS'};
  const deviceKey = t => JSON.stringify([t.platform, t.runner_id, t.device_id]);
  const dueFormat = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false});

  const unconfirmed = new Map();
  function pendingRequest(key, value) {
    if (value !== undefined) {
      if (value) unconfirmed.set(key,value);
      else unconfirmed.delete(key);
      try {
        if (value) sessionStorage.setItem(key,value);
        else sessionStorage.removeItem(key);
      } catch {}
    }
    try { return sessionStorage.getItem(key) || unconfirmed.get(key); }
    catch { return unconfirmed.get(key); }
  }
  function requestId() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes,b => b.toString(16).padStart(2,'0')).join('');
    return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
  }
  function mount(page, {request, identity, canEdit}) {
    const user = identity();
    let disposed = false, sequence = 0, schedules = [], devices = [], draft = null;
    const alive = () => !disposed && page.isConnected && user === identity();
    const submitting = new Set(), runMessages = new Map();
    const intentKey = task => 'ui.schedule.run.'+JSON.stringify([user,task.platform,task.version]);
    const phoneName = task => devices.find(d => deviceKey(d.target) === deviceKey(task.target))?.name || task.target.device_id;
    const current = (d, seq) => alive() && draft === d && seq === sequence;
    const q = selector => page.querySelector(selector);
    page.classList.add('ui-simple', 'ui-schedules');
    page.innerHTML = `<header class="ui-simple-heading"><div><p>工作台 <span>/</span> UI 自动化</p><h1>定时任务</h1></div>
      <button type="button" class="btn-sm" data-refresh>刷新</button></header>
      <p class="ui-simple-intro">智小白 3D 每日 UI 回归 · 北京时间（UTC+8） · 执行时使用已选基线的最新发布版</p>
      <p role="status" aria-live="polite" data-message></p>
      <section class="schedule-cards" aria-label="每日回归任务"></section>
      <section class="schedule-editor" aria-label="定时任务配置" hidden></section>`;
    const host = q('.schedule-editor');
    const message = (text, error = false) => {
      q('[data-message]').textContent = text;
      q('[data-message]').classList.toggle('schedule-error', error);
    };
    function confirmLeave() {
      if (draft?.saving) return false;
      return !draft?.dirty || window.confirm('当前定时任务有未保存的修改，确认放弃修改吗？');
    }
    async function manualRun(task) {
      if (!alive() || !canEdit() || !schedules.includes(task) || submitting.has(task.platform) || draft?.saving) return;
      const key = intentKey(task), pending = pendingRequest(key);
      const platform = platformName[task.platform];
      const active = task.cases.filter(c => !c.deferred);
      const physical = active.some(c => c.filename === '关节龙打印.yaml');
      const prompt = (pending ? '将使用上次请求编号重试。' : '')
        +'按已保存配置在 '+phoneName(task)+' 上执行 '+platform+' 每日回归的 '+active.length+' 条基线？'
        +(physical?' 此次包含关节龙真实打印。':'')+(draft?.dirty?' 当前未保存的修改不会用于本次执行。':'');
      if (!active.length || !window.confirm(prompt)) return;
      const id = pending || requestId();
      submitting.add(task.platform);
      pendingRequest(key,id);
      runMessages.set(task.platform,'正在提交手动执行…');
      updateRun(task);
      try {
        const result = await request('/ui-schedules/'+task.platform+'/run',{
          method:'POST',body:{version:task.version,request_id:id},timeoutMs:30000
        });
        if (!result?.ok || !result.run_id) throw new Error('缺少执行回执');
        pendingRequest(key,null);
        const states = {queued:'已排队',running:'执行中',finished:'已结束',failed:'执行失败'};
        runMessages.set(task.platform,'手动执行'+(states[result.status] || '已提交')+' · '+result.run_id);
      } catch (error) {
        if (error.status >= 400 && error.status < 500 && error.status !== 408) {
          pendingRequest(key,null);
          runMessages.set(task.platform,'手动执行未提交：'+error.message);
        } else {
          runMessages.set(task.platform,'提交结果待核对：'+error.message+'。请点击“核对上次执行”。');
        }
      } finally {
        submitting.delete(task.platform);
        if (alive()) {
          const saved = schedules.find(s => s.platform === task.platform);
          if (saved) updateRun(saved);
        }
      }
    }
    function updateRun(task) {
      const card = q('article[data-platform="'+task.platform+'"]');
      const run = card?.querySelector('[data-run]');
      if (!run) return;
      const pending = pendingRequest(intentKey(task));
      run.textContent = pending ? '核对上次执行' : '手动执行';
      const platform = platformName[task.platform];
      run.setAttribute('aria-label',pending ? '核对上次 '+platform+' 手动执行' : '手动执行 '+platform+' 定时任务');
      run.disabled = !task.cases.some(c => !c.deferred) || submitting.has(task.platform) || !!draft?.saving;
      card.querySelector('[role=status]').textContent = runMessages.get(task.platform) || '';
    }
    function renderCards() {
      const cards = q('.schedule-cards');
      cards.replaceChildren();
      for (const task of schedules) {
        const card = document.createElement('article');
        card.dataset.platform = task.platform;
        card.innerHTML = '<h2></h2><strong></strong><p data-summary></p><p data-next></p><button type="button" class="btn-sm"></button>';
        const title = platformName[task.platform]+' 每日回归';
        card.querySelector('h2').textContent = title;
        card.querySelector('strong').textContent = task.time;

        const active = task.cases.filter(c => !c.deferred).length;
        const deferred = task.cases.length-active;
        card.querySelector('[data-summary]').textContent = phoneName(task)+' · '+active+' 条基线'+(deferred?' · '+deferred+' 条暂缓':'');
        const due = new Date(task.next_run_at);
        card.querySelector('[data-next]').textContent = '下次执行 '+(task.next_run_at && Number.isFinite(due.getTime()) ? dueFormat.format(due) : '待确认');
        const action = card.querySelector('button');
        action.textContent = canEdit() ? '编辑任务' : '查看任务';
        action.setAttribute('aria-label', (canEdit()?'编辑 ':'查看 ')+platformName[task.platform]+' 定时任务');
        action.disabled = draft?.platform === task.platform || !!draft?.saving;
        action.onclick = () => { if (confirmLeave()) open(task); };
        if (canEdit()) {
          const run = document.createElement('button'), status = document.createElement('p');
          run.dataset.run = '';
          run.type = 'button';
          run.className = 'btn-sm';
          run.onclick = () => manualRun(task);
          status.setAttribute('role','status');
          card.append(document.createTextNode(' '),run,status);
        }
        cards.append(card);
        updateRun(task);
      }
    }
    async function refresh() {
      if (!alive() || !confirmLeave()) return;
      const seq = ++sequence;
      if (draft) host.querySelector('fieldset').disabled = true;
      q('[data-refresh]').disabled = true;
      message('正在读取定时任务…');
      try {
        const data = await request('/ui-schedules');
        if (!alive() || sequence !== seq) return;
        schedules = data.schedules;
        draft = null;
        host.replaceChildren();
        host.hidden = true;
        renderCards();
        message(schedules.length ? '' : '暂无可查看的定时任务。');
        if (schedules.length) loadOverviewDevices(seq);
      } catch (error) {
        if (alive() && sequence === seq) {
          message('读取失败：'+error.message, true);
          if (draft && !draft.ready && canEdit()) q('[data-retry]').hidden = false;
        }
      } finally {
        if (alive() && sequence === seq) {
          q('[data-refresh]').disabled = false;
          if (draft) host.querySelector('fieldset').disabled = false;
        }
      }
    }
    async function loadOverviewDevices(seq) {
      try {
        const data = await request('/ui-executions/devices?app_id=com.kfb.model');
        if (!alive() || seq !== sequence || !Array.isArray(data.devices)) return;
        devices = data.devices;
        renderCards();
      } catch { /* A saved device ID remains visible when the catalog is unavailable. */ }
    }
    function updateSave(d) {
      const save = q('[data-save]');
      if (!save) return;
      const incompatible = [...d.selected].some(([id,c]) => d.available.get(id)?.revision !== c.revision);
      save.disabled = !canEdit() || !d.dirty || d.saving || !d.ready || !d.selected.size || incompatible || !q('[name=time]').validity.valid;
      q('[data-count]').textContent = '已选 '+d.selected.size+' 条基线';
    }
    function renderCases(d) {
      const list = q('[data-cases]');
      list.replaceChildren();
      const search = q('[name=filter]').value.trim().toLowerCase();
      const add = (c, incompatible) => {
        const name = c.task_name || c.filename || c.case_id;
        if (!(name+' '+(c.filename||'')+' '+(c.module||'')).toLowerCase().includes(search)) return;
        const label = document.createElement('label');
        const input = document.createElement('input');
        input.type = 'checkbox';
        if (incompatible) label.dataset.incompatible = '';
        input.checked = d.selected.get(c.case_id)?.revision === c.revision;
        input.onchange = () => {
          if (input.checked) d.selected.set(c.case_id,c);
          else d.selected.delete(c.case_id);
          d.dirty = true;
          if (incompatible || list.querySelector('[data-incompatible]')) renderCases(d);
          updateSave(d);
        };
        label.append(input, document.createTextNode(name+(incompatible?'（旧版本或当前设备不可用，请取消勾选）':'')));
        list.append(label);
      };
      for (const [id,c] of d.selected) if (d.available.get(id)?.revision !== c.revision) add(c,true);
      for (const c of d.available.values()) add(c,false);
      if (!list.children.length) list.textContent = search?'没有匹配的基线。':'这台手机暂无可运行的已发布基线。';
      updateSave(d);
    }
    function populateDevices(d) {
      const select = q('[name=device]');
      select.replaceChildren();
      const ready = devices.filter(device => device.target.platform === d.platform && device.connection_status === 'ready');
      for (const phone of ready) {
        select.add(new Option(phone.name+' · '+phone.target.runner_id+' · '+phone.target.device_id, deviceKey(phone.target)));
      }
      const key = deviceKey(d.target);
      if (!ready.some(phone => deviceKey(phone.target) === key)) {
        const unavailable = new Option(d.target.device_id+' · '+d.target.runner_id+'（当前不可用）',key);
        unavailable.disabled = true;
        select.add(unavailable);
      }
      select.value = key;
      return ready.some(phone => deviceKey(phone.target) === key);
    }
    async function loadOptions(d, reloadDevices) {
      const seq = ++sequence;
      d.ready = false;
      q('[data-cases]').textContent = '正在读取可选基线…';
      q('[data-retry]').hidden = true;
      updateSave(d);
      try {
        if (reloadDevices) {
          const data = await request('/ui-executions/devices?app_id=com.kfb.model');
          if (!current(d,seq)) return;
          devices = data.devices;
          renderCards();
        }
        if (!populateDevices(d)) throw new Error('当前配置的手机未就绪。请选择同平台的就绪手机，或恢复连接后重试。');
        const query = new URLSearchParams({runner_id:d.target.runner_id,device_id:d.target.device_id});
        const result = await request('/ui-schedules/'+d.platform+'/options?'+query);
        if (!current(d,seq)) return;
        if (!result.target || deviceKey(result.target) !== deviceKey(d.target)) throw new Error('手机连接已变化，请重新读取。');
        d.available = new Map(result.cases.map(c => [c.case_id,c]));
        // Editing keeps the selected identities when a compatible revision is published.
        for (const id of d.selected.keys()) {
          if (d.available.has(id)) d.selected.set(id,d.available.get(id));
        }
        d.ready = true;
        message('');
        renderCases(d);
      } catch (error) {
        if (!current(d,seq)) return;
        q('[data-cases]').textContent = '待核对当前手机可运行的基线。';
        q('[data-retry]').hidden = false;
        message(error.message,true);
      }
    }
    async function save(d) {
      if (!alive() || draft !== d || !canEdit() || q('[data-save]').disabled) return;
      const payload = {version:d.version,time:q('[name=time]').value,target:d.target,
        cases:[...d.selected.values()].map(({case_id,revision}) => ({case_id,revision}))};
      d.saving = true;
      host.querySelector('fieldset').disabled = true;
      q('[data-refresh]').disabled = true;
      renderCards();
      updateSave(d);
      message('正在保存…');
      try {
        const data = await request('/ui-schedules/'+d.platform, {method:'POST',body:payload});
        if (!alive() || draft !== d) return;
        schedules = schedules.map(s => s.platform === d.platform ? data.schedule : s);
        draft = null;
        host.replaceChildren();
        host.hidden = true;
        message('已保存，下一次定时执行将使用新配置。');
      } catch (error) {
        if (!alive() || draft !== d) return;
        message('保存失败：'+error.message+(error.status === 409?'。请刷新后核对最新配置。':''), true);
      } finally {
        if (alive()) {
          d.saving = false;
          q('[data-refresh]').disabled = false;
          if (draft === d) {
            host.querySelector('fieldset').disabled = false;
            updateSave(d);
          }
          renderCards();
        }
      }
    }
    function caseList(container, cases) {
      const list = document.createElement('ul');
      for (const c of cases) {
        const item = document.createElement('li');
        item.textContent = c.task_name || c.filename || c.case_id;
        list.append(item);
      }
      container.append(list);
    }
    function open(task) {
      ++sequence;
      const active = task.cases.filter(c => !c.deferred);
      const deferred = task.cases.filter(c => c.deferred);
      const d = draft = {platform:task.platform,version:task.version,target:task.target,
        selected:new Map(active.map(c => [c.case_id,c])),available:new Map(),dirty:false,ready:false,saving:false};
      renderCards();
      message('');
      host.hidden = false;
      host.innerHTML = '<h2 tabindex="-1"></h2><fieldset></fieldset><div data-deferred></div>';
      host.querySelector('h2').textContent = platformName[d.platform]+' 定时任务配置';
      host.querySelector('h2').focus({preventScroll:true});
      const form = host.querySelector('fieldset');
      if (deferred.length) {
        const section = q('[data-deferred]');
        const title = document.createElement('h3');
        title.textContent = '暂缓执行（'+deferred.length+' 条）';
        section.append(title);
        caseList(section,deferred);
      }
      if (!canEdit()) {
        const info = document.createElement('p');
        info.textContent = '每天 '+task.time+' · '+task.target.device_id+' · '+task.target.runner_id+' · 当前账号仅可查看';
        form.append(info);
        caseList(form,active);
        return;
      }
      form.innerHTML = `<div class="ui-simple-filters"><label>执行时间（北京时间）<input type="time" name="time" required></label>
        <label>执行手机<select name="device"></select></label></div>
        <div class="schedule-baselines"><h3>执行基线</h3><span data-count></span>
          <label class="schedule-search">搜索基线<input type="search" name="filter" placeholder="用例名、文件名或模块"></label></div>
        <div data-cases class="schedule-cases"></div><button type="button" class="btn-sm" data-retry hidden>重新读取可选项</button>
        <div class="schedule-actions"><button type="button" class="btn-primary" data-save disabled>保存修改</button></div>`;
      q('[name=time]').value = task.time;
      q('[name=time]').oninput = () => { d.dirty = true; updateSave(d); };
      q('[name=device]').onchange = event => {
        const phone = devices.find(p => deviceKey(p.target) === event.target.value);
        if (!phone) return;
        d.target = phone.target;
        d.dirty = true;
        loadOptions(d,false);
      };
      q('[name=filter]').oninput = () => { if (d.ready) renderCases(d); };
      q('[data-save]').onclick = () => save(d);
      q('[data-retry]').onclick = () => loadOptions(d,true);
      loadOptions(d,true);
    }
    q('[data-refresh]').onclick = refresh;
    refresh();
    return {refresh,confirmLeave,dispose(){disposed = true;}};
  }
  window.UiSchedules = {mount};
})();
