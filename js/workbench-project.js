// Start a mapped smoke run from a requirement, or inspect the evidence without starting a run.
window.WorkbenchProject = (() => {
  const intakePath = '/platform/requirement-design/intake';
  const statuses = {awaiting_qa_target:'QA 包已到 · 等待配置该端的 QA 执行设备',no_admitted_scripts:'QA 包已到 · 本端尚无通过校验的需求脚本',build_unavailable:'构建回执待核对 · 暂缓执行',source_unavailable:'需求或设计材料读取异常 · 暂缓测试',smoke_running:'冒烟执行中 · 后续回归等待结果',smoke_blocked:'冒烟未达继续条件 · 后续回归已暂停',design_completed:'测试设计已生成', design_failed:'测试设计生成失败',
    design_running:'正在设计测试用例',design_busy:'测试设计等待模型资源',
    qa_package_scope_required:'已转测 · 等待配置可核实的专用 QA 包', awaiting_qa_build:'已转测 · 等待 QA 构建', design_changed_since_build:'已转测 · 构建对应的设计已变更',
    awaiting_design:'已转测 · 等待当前版本测试设计',awaiting_reviewed_baselines:'已转测 · 待绑定已发布基线',awaiting_mapping:'需求已登记 · 可以从验收条件生成脚本', blocked:'执行受阻', partial:'部分平台已提交', submitted:'已提交回归'};
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
    if(build?.script_origin==='requirement'){
      line(output,'本版需求生成 '+build.generated_count+' 条可执行脚本，另有 '+build.deferred.length+' 条未纳入本端执行。');
      for(const item of build.deferred) line(output,item.title+' · '+item.reason);
    }
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
        for(const [platform,script] of Object.entries(c.scripts || {})){
          line(details,(platform==='ios'?'iOS':'Android')+' · '+(script.ready?'脚本已就绪 · 可执行性 '+script.score+' 分':script.reason));
          if(script.ready) {
            const preview=line(details,'','details');
            line(preview,'查看生成的脚本','summary');
            line(preview,script.yaml_content,'pre');
          }
        }
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
      const uploadLabel=line(output,'需求附图（可选 PNG）','label');
      const image=document.createElement('input');
      image.type='file';image.accept='image/png';
      uploadLabel.appendChild(image);
      const button = line(output,'用验收条件准备测试设计','button');
      button.type = 'button';
      button.onclick = async () => {
        if (!designer() || button.disabled) return;
        button.disabled = true;
        try {
          const {source,event_id,app_id} = receipt;
          const body={source,event_id,app_id};
          if(image.files?.length){
            const file=image.files[0];
            if(file.size>2*1024*1024){line(output,'原型图需要小于等于 2 MB。');return;}
            body.image_base64=await new Promise((resolve,reject)=>{
              const reader=new FileReader();
              reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);
            });
          }
          const result = await apiRequest(intakePath + '/design-source',{method:'POST',body});
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
  function showApiEvidence(parent, source, id) {
    const label='接口请求与响应证据';
    const figure=line(parent,'','figure');
    figure.style.margin='12px 0';
    line(figure,label,'figcaption');
    const thumbnail=document.createElement('img');
    thumbnail.src=source;thumbnail.alt=label;
    thumbnail.style.cssText='display:block;max-width:100%;width:480px;height:auto;margin:8px 0';
    figure.append(thumbnail);
    const zoom=line(figure,'放大证据图','button');
    zoom.type='button';
    zoom.onclick=()=>{
      const dialog=line(figure,'','dialog');
      dialog.className='ui-evidence-dialog';
      dialog.setAttribute('aria-label',label);
      const header=line(dialog,'','header');
      line(header,label,'strong');
      const close=line(header,'关闭预览','button');
      const viewport=line(dialog,'','div');
      viewport.dataset.evidenceViewport='';
      viewport.classList.add('is-zoomed');
      const img=document.createElement('img');img.src=source;img.alt=label;viewport.append(img);
      close.type='button';close.onclick=()=>dialog.close();
      dialog.onclick=event=>{if(event.target===dialog)dialog.close();};
      dialog.onclose=()=>{dialog.remove();zoom.focus();};
      dialog.showModal();
    };
    const download=line(figure,'下载证据 PNG','a');
    download.style.marginInlineStart='12px';download.href=source;download.download='接口核对-'+id+'.png';
  }
  function attachApiInvestigation(parent, report, base, live) {
    if (!report.api_investigation_supported && !report.api_investigation) return;
    line(parent,'接口核对','h4');
    const result=line(parent,'','div');
    const feedback=line(parent,'','p');
    feedback.setAttribute('role','status');
    function render(data) {
      result.replaceChildren();
      line(result,data.status==='confirmed'?'已核实':'待核实','strong');
      if(data.conclusion)line(result,data.conclusion);
      const names=[['设计稿',data.design_name],['真机',data.device_name],['接口',data.api_name]]
        .filter(([,name])=>name).map(([label,name])=>label+'「'+name+'」');
      if(names.length)line(result,names.join(' · '));
      const req=data.request||{};
      if(req.queried_at)line(result,'核对时间　'+req.queried_at);
      if(req.url)line(result,[req.method,req.url].filter(Boolean).join(' '));
      const receipt=[['HTTP',req.http],['业务码',req.business_code],['返回条数',req.count],['请求 ID',req.request_id],['响应请求 ID',req.response_request_id]]
        .filter(([,value])=>value!=null && value!=='').map(([label,value])=>label+' '+value);
      if(receipt.length)line(result,receipt.join(' · '));
      if(data.image_match){
        const labels={matched:'已匹配',no_match:'未匹配',ambiguous:'匹配到多处',invalid_image:'图片不可读',insufficient_detail:'图片细节不足'};
        const parts=[['设计稿',data.image_match.board],['真机',data.image_match.shot]]
          .map(([label,image])=>label+(labels[image?.status]||'待核实'));
        line(result,'接口预设图　'+parts.join(' · '));
      }
      if(typeof data.evidence_image==='string' && data.evidence_image.startsWith('data:image/png;base64,'))
        showApiEvidence(result,data.evidence_image,report.id);
      if(/^https:\/\/project\.feishu\.cn\//i.test(data.issue_url||'')){
        const issue=line(result,'查看已有飞书缺陷','a');
        issue.href=data.issue_url;issue.rel='noopener noreferrer';issue.target='_blank';
      }
    }
    if(report.api_investigation)render(report.api_investigation);
    if(!report.api_investigation_supported)return;
    const button=line(parent,report.api_investigation?'重新核对接口':'一键核对接口','button');
    button.type='button';
    result.before(button);
    button.onclick=async()=>{
      if(!live() || !button.isConnected || button.disabled)return;
      button.disabled=true;feedback.textContent='正在请求接口并核对证据…';
      try{
        const {investigation}=await apiRequest(base+'/investigate',{method:'POST',body:{id:report.id}});
        if(!live() || !button.isConnected)return;
        if(!investigation)throw Error('未收到核对结果，请重试。');
        render(investigation);button.textContent='重新核对接口';feedback.textContent='';
      }catch(error){
        if(live() && button.isConnected)feedback.textContent=error.message||'接口核对失败，请重试。';
      }finally{button.disabled=false;}
    };
  }
  function attachVisual(panel,current) {
    if (!hasPermission('platform.configure') || !hasPermission('ui.view') ||
        !(currentAccessProfile?.is_superuser || currentAccessProfile?.scope?.ui_apps==='*')) return;
    const box=document.createElement('article');
    box.className='workbench-receipt';
    panel.append(box);
    const open=line(box,'设计稿与真机对照','button');
    const body=document.createElement('div');
    body.hidden=true;box.append(body);
    const base='/platform/requirement-design/visual';
    let sourceSequence=0,boardSequence=0,stepSequence=0,viewSequence=0, version='', loaded=false;
    let busy=false, designEdition='';
    const deviceNames=new Map();
    const live=()=>current() && box.isConnected;
    function field(name,tag){
      const label=line(body,name,'label');
      label.style.display='block';
      const input=document.createElement(tag);label.append(input);
      input.style.cssText='display:block;max-width:100%;width:100%;margin:4px 0 12px';
      return input;
    }
    const source=field('需求材料','select'), link=field('飞书需求关联的 Figma 文件或画板','input');
    link.type='url';link.placeholder='粘贴「查看设计稿」的链接';
    const fetchBoards=line(body,'读取 Figma 画板','button'),board=field('设计画板','select');
    const job=field('我的真机执行记录','select'),step=field('实测截图所在步骤','select');
    const env=field('本次真机的实际环境','select');
    env.add(new Option('请选择环境',''));for(const s of ['测试','正式'])env.add(new Option(s,s));
    const run=line(body,'对照两张图并保存分析','button');
    const status=line(body,'','p');status.setAttribute('role','status');
    const history=document.createElement('div'),result=document.createElement('div');body.append(history,result);
    function options(select,label,entries=[]){
      select.replaceChildren(new Option(label,''));
      for(const [value,name] of entries)select.add(new Option(name,value));
    }
    const editionHint=line(body,'','p');editionHint.setAttribute('role','status');run.before(editionHint);
    function ready(){
      const appEdition=step.selectedOptions[0]?.dataset.edition;
      const mismatch=designEdition && appEdition && designEdition!==appEdition;
      editionHint.textContent=version ? '设计稿：'+(designEdition||'端别待确认')+
        (step.value?' · 执行应用：'+(appEdition||'端别待确认'):'')+
        (mismatch?'。请选择同一端的画板和执行记录。':'') : '';
      run.disabled=busy || !!mismatch || !(source.value && version && board.value && job.value && step.value && env.value);
    }
    function fail(err){if(live())status.textContent=err.message||'读取失败，请重试';}
    async function show(id){
      const seq=++viewSequence;
      const r=await apiRequest(base+'/result?id='+encodeURIComponent(id));
      if(!live()||seq!==viewSequence)return;
      result.replaceChildren();line(result,r.title+' · '+r.environment+'环境','h3');
      const appEdition=r.step.app_edition, edition=r.design_edition;
      if(edition && appEdition && edition!==appEdition){
        line(result,'跨端历史对照 · '+edition+'设计稿 / '+appEdition+'执行 · 此报告仅供参考，不计入同端 UI 验收','strong');
      }else{
        line(result,'设计稿：'+(edition||'端别待确认')+' · 执行应用：'+(appEdition||'端别待确认'));
      }
      line(result,r.file_name+' / '+r.board.name+' · Figma 版本 '+r.version);
      const href='https://www.figma.com/design/'+encodeURIComponent(r.file)+'/?node-id='+encodeURIComponent(r.board.id.replace(':','-'));
      const designLink=line(result,'打开 Figma 原画板','a');designLink.href=href;designLink.target='_blank';designLink.rel='noopener';
      const pair=document.createElement('div');pair.style.cssText='display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:16px';result.append(pair);
      for(const [label,src] of [['Figma 原画板 · '+r.board.width+' × '+r.board.height,r.board_image],['真机步骤 · '+r.size.join(' × '),r.device_image]]){
        const figure=document.createElement('figure');figure.style.margin='8px 0';pair.append(figure);
        line(figure,label,'figcaption');const img=document.createElement('img');img.alt=label;img.src=src;img.style.cssText='width:100%;height:360px;object-fit:contain';figure.append(img);
      }
      line(result,'执行设备 '+(deviceNames.get(r.device)||r.device)+' · '+r.executed+' · '+r.step.label);
      const direct=line(result,'打开本次对照记录','a');direct.href='?visual_compare='+r.id;
      const report=line(result,'查看原始执行报告','a');report.href=r.report;report.target='_blank';report.rel='noopener';
      line(result,'AI 对照意见 · '+r.model,'h4');
      for(const [key,title] of [['matches','两边一致'],['differences','可见差异'],['unverified','待核验']]){
        if(!r.findings[key]?.length)continue;
        line(result,title,'strong');const list=document.createElement('ul');result.append(list);
        for(const item of r.findings[key])line(list,item,'li');
      }
      attachApiInvestigation(result,r,base,live);
    }
    function addHistory(id,label){
      const btn=line(history,label,'button');btn.onclick=()=>show(id).catch(fail);
    }
    async function changeSource(){
      const seq=++sourceSequence, initialBoardSequence=++boardSequence;++stepSequence;++viewSequence;
      result.replaceChildren();history.replaceChildren();link.value='';version='';env.value='';
      options(board,'先读取画板');options(job,'选择执行记录');options(step,'选择执行记录后可查看截图');status.textContent='';ready();
      if(!source.value)return;
      const r=await apiRequest(base+'/context?source='+encodeURIComponent(source.value));
      if(!live()||seq!==sourceSequence)return;
      const prior=r.history?.[0];
      const ref=r.refs?.[0] || (prior && {file_key:prior.file,node_id:prior.board.id});
      const prefill=ref && initialBoardSequence===boardSequence;
      if(prefill)link.value='https://www.figma.com/design/'+ref.file_key+'/?node-id='+ref.node_id.replace(':','-');
      options(job,'选择执行记录',r.jobs.map(j=>[j.id,[j.name,j.time,deviceNames.get(j.device)||j.device].filter(Boolean).join(' · ')]));
      for(const item of r.history)addHistory(item.id,'查看已存对照 · '+item.board.name+' · '+item.environment+' · '+item.executed);
      if(prefill)await readBoards();
    }
    async function readBoards(){
      const seq=++boardSequence;version='';options(board,'正在读取…');ready();
      try{
        const r=await apiRequest(base+'/boards',{method:'POST',body:{link:link.value.trim()}});
        if(!live()||seq!==boardSequence)return;
        version=r.version;designEdition=r.design_edition||'';options(board,'选择画板',r.boards.map(b=>[b.id,b.name+' · '+b.width+' × '+b.height+' · '+b.id]));
        if(r.boards.length===1)board.selectedIndex=1;
        status.textContent=r.file_name+' · '+r.boards.length+' 个画板';ready();
      }catch(e){if(seq===boardSequence){options(board,'读取失败');fail(e);}}
    }
    source.onchange=()=>changeSource().catch(fail);fetchBoards.onclick=readBoards;
    link.oninput=()=>{++boardSequence;version='';options(board,'重新读取画板');ready();};
    job.onchange=async()=>{
      const seq=++stepSequence;options(step,'选择截图步骤');ready();
      if(!job.value)return;
      try{const r=await apiRequest(base+'/steps?job='+encodeURIComponent(job.value));
        if(live()&&seq===stepSequence){
          options(step,'选择截图步骤',r.steps.map((s,i)=>[s.id,(i+1)+'. '+s.label]));
          r.steps.forEach((s,i)=>{step.options[i+1].dataset.edition=s.app_edition||'';});
        }
      }catch(e){if(seq===stepSequence)fail(e);}
    };
    board.onchange=step.onchange=env.onchange=ready;
    run.onclick=async()=>{
      if(run.disabled)return;
      const seq=sourceSequence;const payload={source:source.value,link:link.value.trim(),version,board:board.value,job:job.value,step:step.value,environment:env.value};
      busy=true;ready();status.textContent='正在导出指定版本的画板并分析两张图…';
      try{
        const r=await apiRequest(base+'/compare',{method:'POST',body:payload});
        if(!live()||seq!==sourceSequence)return;
        await show(r.id);addHistory(r.id,'查看本次已存对照');status.textContent='已保存，可重新打开查看。';
      }catch(e){if(seq===sourceSequence)fail(e);}finally{busy=false;ready();}
    };
    open.onclick=async()=>{
      body.hidden=!body.hidden;
      if(body.hidden||loaded)return;
      try{const r=await apiRequest('/platform/requirement-design');if(!live())return;
        options(source,'选择需求材料',r.sources.map(s=>[s.id,s.title+' · '+s.version]));loaded=true;
        const devices=await apiRequest('/ui-executions/devices').catch(()=>({}));
        for(const d of devices.devices||[])if(d.target?.device_id&&d.name)deviceNames.set(d.target.device_id,d.name);
      }catch(e){fail(e);}
    };
    const wanted=new URLSearchParams(location.search).get('visual_compare');
    if(/^[a-f0-9]{64}$/.test(wanted||'')){
      open.click();
      show(wanted).then(()=>{if(live())result.scrollIntoView({block:'start'});}).catch(fail);
    }
    ready();
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
    attachVisual(panel,current);
    const refresh = attachRecent(panel,current);
    attachIntake(panel,current,refresh);
    attachLogs(panel,current);
    if (!panel.querySelector('details,button')) panel.remove();
  }};
})();
