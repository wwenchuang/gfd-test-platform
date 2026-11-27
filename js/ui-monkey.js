// The execution count, outcome and incident evidence come from the selected Runner.
(function () {
  const uncertain = new Map();
  const labels = {
    queued:'等待手机接单',running:'执行中',stopping:'正在停止',stopped:'已停止',
    passed:'已通过',failed:'执行失败',crashed:'应用崩溃',inconclusive:'证据不足'
  };
  const live = run => ['queued','running','stopping'].includes(run?.status);
  const platform = target => target?.platform === 'ios' ? 'iOS' : 'Android';
  const sameTarget = (a,b) => a && b && ['platform','runner_id','device_id'].every(k=>a[k]===b[k]);
  const durationText = seconds => seconds>=60 && seconds%60===0 ? seconds/60+' 分钟' :
    (seconds>=60?Math.floor(seconds/60)+' 分 ':'')+seconds%60+' 秒';
  const budget = item => item.duration_seconds ? {duration_seconds:item.duration_seconds} : {event_count:item.event_count};
  function mount(page, {request, identity, canExecute, canEdit}) {
    const owner = identity();
    let disposed = false, generation = 0, detailSequence = 0, refreshSequence = 0, timer;
    let app = '', directory = [], devices = [], runs = [], selected = null, pending = null, busy = false;
    let schedules=[], schedulesReady=false, editing=null;
    const alive = () => !disposed && page.isConnected && identity() === owner;
    const q = selector => page.querySelector(selector);
    const pendingKey = () => 'ui.monkey.submit.' + JSON.stringify([owner,app]);
    const url = run => '/ui-monkey/runs/' + encodeURIComponent(run.run_id);
    page.classList.add('ui-simple','ui-monkey');
    page.innerHTML = `<header class="ui-simple-heading"><div><p>工作台 <span>/</span> UI 自动化</p><h1>Monkey 稳定性测试</h1></div>
      <button type="button" class="btn-sm" data-refresh>刷新</button></header>
      <p role="status" data-notice></p>
      <div class="monkey-grid"><section class="monkey-panel"><h2>执行配置</h2>
      <form><label>测试应用<select aria-label="测试应用" data-app><option value="">选择应用</option></select></label>
      <label>执行手机<select aria-label="执行手机" data-device><option value="">先选择应用</option></select></label>
      <label>执行方式<select aria-label="执行方式" data-kind><option value="manual">手动执行</option><option value="daily">每日定时</option></select></label>
      <label>操作模式<select aria-label="操作模式" data-mode><option value="mixed">随机点击与滑动</option><option value="browse">仅随机滑动</option></select></label>
      <p data-mode-note hidden>随机点击可能变更应用数据。请核对所选应用与手机。</p>
      <label>执行上限<select aria-label="执行上限" data-limit><option value="events">按事件数</option><option value="duration">按持续时长</option></select></label>
      <div class="monkey-numbers"><label data-count-label>事件数<input data-count type="number" min="1" max="10000" step="1" value="100" required></label>
      <label data-duration-label hidden>持续时长（分钟）<input data-duration type="number" min="1" max="1440" step="1" value="30" required></label>
      <label>随机种子<input data-seed type="number" min="0" max="2147483647" step="1" value="1" required></label></div>
      <div class="monkey-presets" data-presets hidden aria-label="常用时长">常用时长</div>
      <div class="monkey-numbers" data-daily-fields hidden><label>每日时间（北京时间）<input type="time" step="60" data-time required value="09:00"></label>
      <label>方案状态<select aria-label="方案状态" data-enabled><option value="true">启用</option><option value="false">停用</option></select></label></div>
      <button class="btn-primary" type="submit" data-start>开始 Monkey 测试</button></form></section>
      <section class="monkey-panel"><h2>执行记录</h2><div class="monkey-history" data-history></div></section></div>
      <section class="monkey-panel"><h2>每日定时方案</h2><div class="monkey-history" data-schedules></div><p data-schedule-notice role="status"></p></section>
      <section class="monkey-panel" data-monkey-detail hidden><header><h2>执行详情</h2><strong data-monkey-state></strong></header>
      <p data-summary></p><div class="monkey-meter"><progress aria-label="Runner 已执行事件"></progress><i data-fill aria-hidden="true"></i></div>
      <div class="monkey-meter-labels"><p role="status" data-progress></p><span data-percent></span><span data-elapsed></span></div>
      <div class="monkey-event-strip" data-events aria-label="事件编号与执行状态"></div>
      <p data-run-message></p><p data-connection-lost role="status" hidden>Runner 超过 120 秒未回报，手机仍保留。恢复原 Runner 后将核对进程是否已停止。</p><button class="btn-sm" type="button" data-stop>停止执行</button>
      <h3>运行日志</h3><pre data-log></pre><section data-crash hidden><h3>事故证据</h3><pre></pre></section>
      <button type="button" class="btn-sm" data-analyze>AI 分析事故证据</button><section data-analysis hidden><h3>AI 证据分析</h3><p data-model></p><div data-findings></div></section></section>`;
    const form = q('form'), appSelect = q('[data-app]'), deviceSelect = q('[data-device]');
    const mode = q('[data-mode]'), count = q('[data-count]'), seed = q('[data-seed]');
    const limit=q('[data-limit]'), minutes=q('[data-duration]'), kind=q('[data-kind]');
    const dailyTime=q('[data-time]'), enabled=q('[data-enabled]');
    const formBudget=()=>limit.value==='duration'?{duration_seconds:minutes.valueAsNumber*60}:{event_count:count.valueAsNumber};
    const formTarget=()=>devices[Number(deviceSelect.value)-1]?.target || (deviceSelect.value==='saved'?editing?.target:null);
    const say = text => { q('[data-notice]').textContent=text; };
    const option = (text,value) => new Option(text,value);
    for(const value of [5,15,30,60,120,480,1440]){
      const button=document.createElement('button');
      button.type='button';button.className='btn-sm';button.dataset.minutes=String(value);
      button.textContent=value>=60?value/60+' 小时':value+' 分钟';
      button.onclick=()=>{minutes.value=value;controls();};
      q('[data-presets]').append(button);
    }
    function controls() {
      appSelect.disabled = busy;
      const daily=kind.value==='daily', timed=limit.value==='duration';
      const locked=busy || !!pending || !(daily?canEdit():canExecute());
      kind.disabled=busy || !!pending;
      deviceSelect.disabled = !devices.length || busy || !!pending;
      for (const field of [mode,limit,seed]) field.disabled=locked;
      count.disabled=locked||timed;minutes.disabled=locked||!timed;
      dailyTime.disabled=enabled.disabled=locked||!daily;
      q('[data-count-label]').hidden=timed;
      q('[data-duration-label]').hidden=q('[data-presets]').hidden=!timed;
      for(const button of q('[data-presets]').children){
        button.disabled=locked;button.setAttribute('aria-pressed',String(button.dataset.minutes===minutes.value));
      }
      q('[data-daily-fields]').hidden=!daily;
      q('[data-mode-note]').hidden = mode.value !== 'mixed';
      const start = q('[data-start]');
      start.hidden = !(daily?canEdit():canExecute());
      start.disabled = busy || !app || (daily && !schedulesReady) || (!pending && (!formTarget() || !form.checkValidity()));
      start.textContent = pending ? '核对上次提交' : daily?'保存每日方案':'开始 Monkey 测试';
      for(const button of q('[data-schedules]').querySelectorAll('button'))button.disabled=busy||!!pending||!schedulesReady;
      q('[data-stop]').hidden = !canExecute() || !['queued','running'].includes(selected?.status);
      q('[data-analyze]').hidden = !canExecute() || !!selected?.analysis || !selected?.crash_log || !['crashed','failed'].includes(selected.status);
      q('[data-stop]').disabled = q('[data-analyze]').disabled = busy;
      q('[data-refresh]').disabled = busy;
    }
    function loadBudget(item){
      limit.value=item.duration_seconds?'duration':'events';
      if(item.duration_seconds)minutes.value=item.duration_seconds/60;
      else count.value=item.event_count;
      mode.value=item.mode;seed.value=item.seed;
    }
    function phoneName(target) {
      const name = directory.find(device => sameTarget(device.target,target))?.name;
      return name && name !== target.device_id ? name : platform(target)+' 设备（机型未记录）';
    }
    function phoneOption(target) {
      return phoneName(target)+' · '+platform(target)+' · 设备编号 '+target.device_id;
    }
    function editPlan(plan){
      if(!plan||!canEdit()||busy||pending)return;
      editing=plan;
      const index=devices.findIndex(d=>sameTarget(d.target,plan.target));
      deviceSelect.querySelector('option[value="saved"]')?.remove();
      if(index<0)deviceSelect.append(new Option(phoneOption(plan.target)+' · 未就绪','saved',true,true));
      else deviceSelect.value=String(index+1);
      kind.value='daily';loadBudget(plan);dailyTime.value=plan.daily_time;enabled.value=String(plan.enabled);
      controls();kind.scrollIntoView({block:'nearest'});
    }
    function renderPlans(){
      const host=q('[data-schedules]');
      host.replaceChildren();
      if(!schedules.length){
        host.textContent=!app?'选择应用后查看方案。':schedulesReady?'暂无每日定时方案。':'正在读取每日定时方案。';
        return;
      }
      for(const plan of schedules){
        const row=document.createElement('article'),title=document.createElement('strong'),info=document.createElement('p');
        const name=phoneName(plan.target);
        title.textContent=plan.daily_time+' 北京时间 · '+(plan.enabled?'已启用':'已停用')+' · '+name;
        info.textContent=[platform(plan.target)+' · 设备编号 '+plan.target.device_id,
          plan.mode==='mixed'?'混合':'浏览',plan.duration_seconds?'计划 '+durationText(plan.duration_seconds):plan.event_count+' 个事件','种子 '+plan.seed].join(' · ');
        row.append(title,info);
        if(plan.enabled && plan.next_run_at){
          const next=new Date(plan.next_run_at);
          if(!Number.isNaN(next.getTime())){
            const line=document.createElement('p');
            line.textContent='下次执行 · '+next.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+' 北京时间';
            row.append(line);
          }
        }
        if(plan.last_status||plan.last_message){
          const last=document.createElement('p');
          last.textContent='最近调度 · '+[labels[plan.last_status]||({skipped:'已跳过',submitted:'已提交',error:'异常'})[plan.last_status]||plan.last_status,plan.last_message].filter(Boolean).join(' · ');
          row.append(last);
        }
        if(canEdit())for(const [label,action] of [['编辑方案',()=>editPlan(plan)],[plan.enabled?'停用方案':'启用方案',()=>{
          savePlan({app_id:app,target:plan.target,mode:plan.mode,...budget(plan),seed:plan.seed,daily_time:plan.daily_time,enabled:!plan.enabled});
        }]]){
          const button=document.createElement('button');
          button.type='button';button.className='btn-sm';button.textContent=label;button.onclick=action;
          row.append(button);
        }
        host.append(row);
      }
    }
    async function savePlan(body){
      if(!alive()||busy||pending||!canEdit()||!schedulesReady)return;
      busy=true;clearTimeout(timer);detailSequence++;refreshSequence++;controls();
      const notice=q('[data-schedule-notice]');notice.textContent='正在保存每日方案…';
      try{
        const {schedule}=await request('/ui-monkey/schedules',{method:'POST',body,timeoutMs:30000});
        if(!alive())return;
        if(schedule?.app_id!==app||!sameTarget(schedule?.target,body.target))throw new Error('定时方案回执与所选设备不符。');
        schedules=[schedule,...schedules.filter(p=>!sameTarget(p.target,schedule.target))];
        if(kind.value==='daily'&&sameTarget(formTarget(),schedule.target)){editing=schedule;enabled.value=String(schedule.enabled);}
        renderPlans();notice.textContent='每日方案已保存。';
      }catch(error){if(alive())notice.textContent='保存结果待核对：'+error.message+'。可刷新读取已保存方案。';}
      finally{if(alive()){busy=false;controls();schedulePoll();}}
    }
    function renderHistory() {
      const host = q('[data-history]');
      host.replaceChildren();
      if (!runs.length) {
        host.textContent = app ? '暂无执行记录。' : '选择应用后查看执行记录。';
        return;
      }
      for (const run of runs) {
        const button = document.createElement('button');
        button.type='button'; button.className='monkey-history-row';
        button.setAttribute('aria-pressed',String(selected?.run_id===run.run_id));
        const title=document.createElement('strong'), meta=document.createElement('small');
        const started = Number(run.created_ns) / 1e6;
        const date = started ? new Date(started).toLocaleString('zh-CN',{hour12:false})+' · ' : '';
        title.textContent = (labels[run.status] || run.status) + ' · ' + phoneName(run.target);
        const result=run.duration_seconds?'计划 '+durationText(run.duration_seconds)+' · '+run.executed+' 个事件':run.executed+' / '+run.event_count;
        meta.textContent = date + result + ' · 设备尾号 ' + run.target.device_id.slice(-4);
        button.append(title,meta);
        button.onclick = () => { if (!busy) open(run); };
        host.append(button);
      }
    }
    function renderRun() {
      const run = selected, detail = q('[data-monkey-detail]');
      detail.hidden = !run;
      if (!run) { controls(); return; }
      q('[data-monkey-state]').textContent = labels[run.status] || '状态待核对';
      q('[data-monkey-state]').classList.toggle('ui-activity-line',run.status==='running' && !run.connection_lost);
      q('[data-summary]').textContent = [appSelect.selectedOptions[0]?.textContent || run.app_id,
        phoneOption(run.target),
        (run.mode==='mixed'?'混合':'浏览')+' · 种子 '+run.seed,'运行编号 '+run.run_id].join('　');
      const meter = detail.querySelector('progress');
      const timed=Number(run.duration_seconds)>0, actual=Number.isInteger(run.elapsed_seconds)?run.elapsed_seconds:null;
      meter.max=timed?run.duration_seconds:run.event_count;
      meter.value=timed?(actual||0):run.executed;
      meter.setAttribute('aria-label',timed?'Runner 实际运行秒数':'Runner 已执行事件');
      q('[data-progress]').textContent = '已执行 '+run.executed+(timed?'':' / '+run.event_count)+' 个事件';
      q('[data-connection-lost]').hidden = run.connection_lost!==true;
      const fraction=Math.min(1,meter.value/meter.max);
      q('[data-fill]').style.transform = 'scaleX('+fraction+')';
      q('[data-percent]').textContent = timed && actual===null ? '' : (fraction*100).toLocaleString('zh-CN',{maximumFractionDigits:1})+'%';
      const start=run.started_ns || run.created_ns, end=run.finished_ns || run.updated_ns;
      const seconds=start && end>=start ? Math.floor((end-start)/1e9) : null;
      const elapsed=q('[data-elapsed]');
      if(timed)elapsed.textContent=(actual===null?'等待时长回报':'实际运行 '+durationText(actual))+' · 计划 '+durationText(run.duration_seconds);
      else if(actual!==null)elapsed.textContent='实际运行 '+durationText(actual);
      else elapsed.textContent=seconds===null?'':(run.started_ns?'已运行 ':'自创建至最近更新 ')+durationText(seconds);
      const strip=q('[data-events]'), signature=[run.run_id,run.executed,run.event_count,run.status].join('-');
      if(strip.dataset.signature!==signature){
        strip.dataset.signature=signature;
        strip.replaceChildren();
        const total=timed?run.executed:run.event_count;
        const first=Math.max(1,Math.min(total-7,run.executed-3));
        for(let number=first;number<=Math.min(total,first+7);number++){
          const marker=document.createElement('span');
          marker.textContent=number;marker.dataset.phase=number===run.executed?'latest':number<run.executed?'done':'pending';
          const state=number<=run.executed?'已完成':live(run)?'待执行':'未执行';
          marker.title='第 '+number+' 个事件 · '+state;
          marker.setAttribute('aria-label',marker.title);
          strip.append(marker);
        }
      }
      q('[data-run-message]').textContent = run.message || '';
      q('[data-log]').textContent = run.log || '暂无 Runner 日志。';
      q('[data-crash]').hidden = !run.crash_log;
      q('[data-crash] pre').textContent = run.crash_log || '';
      q('[data-analysis]').hidden = !run.analysis;
      q('[data-model]').textContent = run.analysis?.model ? '模型 · '+run.analysis.model : '';
      const findings = q('[data-findings]');
      findings.replaceChildren();
      if (run.analysis) for (const [key,title] of [['facts','已观察事实'],['hypotheses','待验证推测'],['next_steps','建议核查']]) {
        const heading = document.createElement('h4'),list=document.createElement('ul');
        heading.textContent=title;
        for(const line of (Array.isArray(run.analysis[key])?run.analysis[key]:[])) {
          const item=document.createElement('li');item.textContent=line;list.append(item);
        }
        if(list.children.length)findings.append(heading,list);
      }
      controls();
    }
    function present(run) {
      if(selected?.run_id!==run?.run_id)q('[data-fill]').style.transform='scaleX(0)';
      if (!run?.run_id || run.app_id !== app || !run.target) throw new Error('执行回执与所选应用不符。');
      selected = run;
      const index = runs.findIndex(row=>row.run_id===run.run_id);
      if(index<0)runs.unshift(run); else runs[index]=run;
      renderHistory(); renderRun();
    }
    function schedulePoll() {
      clearTimeout(timer);
      if (alive() && live(selected) && !busy) timer=setTimeout(()=>open(selected),2500);
    }
    async function open(run) {
      clearTimeout(timer);
      const seq=++detailSequence, version=generation;
      if (selected?.run_id!==run.run_id) present(run);
      try {
        const data=await request(url(run));
        if(!alive() || seq!==detailSequence || version!==generation)return;
        if(data.run?.run_id!==run.run_id) throw new Error('执行编号不符。');
        present(data.run);say('');
      } catch(error) {
        if(alive() && seq===detailSequence && version===generation)say('读取进度失败：'+error.message);
      } finally {
        if(alive() && seq===detailSequence && version===generation)schedulePoll();
      }
    }
    async function refresh() {
      if(!alive() || !app || busy)return;
      const currentApp=app, version=++refreshSequence, previousDevice=devices[Number(deviceSelect.value)-1]?.target;
      const query='?app_id='+encodeURIComponent(currentApp);
      const results=await Promise.allSettled([request('/ui-executions/devices'+query),request('/ui-monkey/runs'+query),request('/ui-monkey/schedules'+query)]);
      if(!alive() || currentApp!==app || version!==refreshSequence || busy)return;
      const [phones,history,plans]=results;
      if(phones.status==='fulfilled')directory=phones.value.devices || [];
      devices = phones.status==='fulfilled' ? directory.filter(d=>d.connection_status==='ready'&&d.monkey_supported===true) : [];
      deviceSelect.replaceChildren(option(devices.length?'选择手机':'暂无支持 Monkey 的就绪手机',''));
      devices.forEach((d,i)=>{
        const o=option(phoneOption(d.target),String(i+1));
        const target=pending?.target || previousDevice || editing?.target;
        o.selected=!!target && ['platform','runner_id','device_id'].every(k=>target[k]===d.target[k]);
        deviceSelect.append(o);
      });
      if(pending && !deviceSelect.value)deviceSelect.append(new Option(phoneOption(pending.target),'pending',true,true));
      if(!pending && editing && !deviceSelect.value && kind.value==='daily')deviceSelect.append(new Option(phoneOption(editing.target)+' · 未就绪','saved',true,true));
      schedulesReady=plans.status==='fulfilled'&&Array.isArray(plans.value.schedules);
      if(schedulesReady)schedules=plans.value.schedules.filter(p=>p.app_id===app&&p.target);
      renderPlans();q('[data-schedule-notice]').textContent=schedulesReady?'':'每日方案暂时无法读取，请刷新重试。';
      if(history.status==='fulfilled')runs=(history.value.runs||[]).filter(r=>r.app_id===app);
      renderHistory(); controls();
      const errors=results.filter(r=>r.status==='rejected');
      if(errors.length)say(errors.map(r=>r.reason.message).join(' · '));
      if(selected)open(selected);
    }
    async function changeApp() {
      if(busy)return;
      app=appSelect.value;generation++;detailSequence++;clearTimeout(timer);
      selected=null;directory=[];devices=[];runs=[];pending=null;schedules=[];schedulesReady=false;editing=null;
      q('[data-schedule-notice]').textContent='';
      if(app){
        pending=uncertain.get(pendingKey()) || null;
        try {pending=pending || JSON.parse(sessionStorage.getItem(pendingKey())||'null');}catch(_){}
      }
      if(pending){kind.value='manual';loadBudget(pending);}
      say(pending?'上次提交的结果待核对，请使用原请求读取回执。':'');
      deviceSelect.replaceChildren(option(app?'正在读取手机…':'先选择应用',''));
      renderHistory();renderPlans();renderRun();
      if(app)await refresh();
    }
    function storePending(value) {
      pending=value;
      const key=pendingKey();
      if(value)uncertain.set(key,value);else uncertain.delete(key);
      try{
        if(value)sessionStorage.setItem(key,JSON.stringify(value));else sessionStorage.removeItem(key);
      }catch(_){/* Keep the pending request across workspace navigation in this tab. */}
    }
    async function submit(event) {
      event.preventDefault();
      if(!alive() || busy || !canExecute() || !app)return;
      const target=formTarget();
      if(!pending && (!target || !form.reportValidity()))return;
      if(kind.value==='daily'){
        await savePlan({app_id:app,target,mode:mode.value,...formBudget(),seed:seed.valueAsNumber,daily_time:dailyTime.value,enabled:enabled.value==='true'});
        return;
      }
      if(!pending && !devices.some(d=>sameTarget(target,d.target)))return;
      let received=false;
      busy=true;clearTimeout(timer);detailSequence++;refreshSequence++;
      try {
        if(!pending){
          const bytes=crypto.getRandomValues(new Uint8Array(16));
          const requestId=Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');
          storePending({app_id:app,target,mode:mode.value,...formBudget(),seed:seed.valueAsNumber,request_id:requestId});
        }
        controls();say('正在提交…');
        const data=await request('/ui-monkey/runs',{method:'POST',body:pending,timeoutMs:30000});
        if(!alive())return;
        present(data.run);received=true;storePending(null);say('');
      } catch(error) {
        if(!alive())return;
        if(error.status>=400 && error.status<500 && error.status!==408 && error.status!==429) {
          storePending(null);say(error.message);
        } else say('提交结果待核对：'+error.message);
      } finally {
        if(alive()){busy=false;controls();schedulePoll();if(received)q('[data-monkey-detail]').scrollIntoView({block:'nearest'});}
      }
    }
    async function act(action) {
      if(!alive()||!selected||busy||!canExecute())return;
      const run=selected;
      busy=true;clearTimeout(timer);detailSequence++;refreshSequence++;controls();
      say(action==='stop'?'正在请求停止…':'正在分析事故证据…');
      try{
        const data=await request(url(run)+'/'+action,{method:'POST',body:{},timeoutMs:action==='analysis'?120000:30000});
        if(!alive())return;
        if(data.run?.run_id!==run.run_id)throw new Error('执行编号不符。');
        present(data.run);say('');
      }catch(error){if(alive())say(error.message);}
      finally{if(alive()){busy=false;controls();schedulePoll();}}
    }
    form.onsubmit=submit;form.oninput=controls;
    appSelect.onchange=changeApp;mode.onchange=limit.onchange=controls;
    function selectPlan(){
      if(kind.value==='manual'){
        deviceSelect.querySelector('option[value="saved"]')?.remove();
        editing=null;
      }else{
        const plan=schedules.find(p=>sameTarget(formTarget(),p.target));
        if(plan)editPlan(plan);
        else {editing=null;enabled.value='true';}
      }
      controls();
    }
    deviceSelect.onchange=kind.onchange=selectPlan;
    q('[data-stop]').onclick=()=>act('stop');q('[data-analyze]').onclick=()=>act('analysis');
    async function catalog() {
      const version=generation;
      try{
        const data=await request('/ui-executions/catalog');
        if(!alive()||generation!==version||app)return;
        appSelect.replaceChildren(option('选择应用',''),...(data.apps||[]).map(a=>option(a.name||a.app_id,a.app_id)));
        say(appSelect.options.length===1?'当前账号没有可用的测试应用。':'');
      }catch(error){if(alive())say(error.message);}
    }
    q('[data-refresh]').onclick=()=>app?refresh():catalog();
    controls();renderHistory();renderPlans();catalog();
    return {refresh:()=>app?refresh():catalog(),dispose(){disposed=true;clearTimeout(timer);page.replaceChildren();}};
  }
  window.UiMonkey={mount};
})();
