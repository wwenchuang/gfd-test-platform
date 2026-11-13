// Parse the plan locally. Raw capture bodies never leave the browser.
window.MobileTracking = (() => {
  let loading;
  function script(src) {
    return new Promise((resolve,reject)=>{
      const el=document.createElement('script');
      el.onload=resolve; el.onerror=()=>reject(Error('解析组件加载失败，请重试'));
      el.src='/js/vendor/tracking/'+src;document.head.append(el);
    });
  }
  function libraries() {
    if(!loading) loading=Promise.all([script('exceljs.min.js'),script('jszip.min.js')]).catch(e=>{loading=null;throw e;});
    return loading;
  }
  async function extract(file) {
    await libraries();
    return window.MobileTrackingSource.extract(file);
  }
  function attach(page, canExecute=true){
    function add(parent,tag,text){
      const el=document.createElement(tag);el.textContent=text||'';parent.append(el);return el;
    }
    const panel=add(page,'section');panel.className='tracking-panel tracking-setup';panel.dataset.mobileTracking='';
    if(!canExecute){panel.hidden=true;return panel;}
    panel.setAttribute('aria-label','新建埋点测试');
    const field=(parent,text,type)=>{const label=add(parent,'label',text), el=add(label,type);el.setAttribute('aria-label',text);return el;};
    const button=(parent,text)=>{const el=add(parent,'button',text);el.type='button';return el;};
    const controls=add(panel,'div');controls.className='tracking-controls';
    const fileGroup=add(controls,'div');fileGroup.className='tracking-file-field';
    const plan=field(fileGroup,'埋点方案','input');plan.type='file';plan.accept='.docx,.xlsx';
    const planState=add(fileGroup,'p','支持 Word（.docx）和 Excel（.xlsx）');planState.setAttribute('role','status');
    const list=add(fileGroup,'details');add(list,'summary','查看解析明细');const contents=add(list,'div');list.hidden=true;
    const scene=field(fileGroup,'测试范围','select');add(scene,'option','全部需求');
    scene.parentElement.hidden=true;
    const deviceGroup=add(controls,'div');deviceGroup.className='tracking-device-field';
    const device=field(deviceGroup,'测试手机','select'),refresh=button(deviceGroup,'刷新设备');
    const badges=add(deviceGroup,'div');badges.className='tracking-device-status';
    const deviceState=add(deviceGroup,'p');deviceState.setAttribute('role','status');
    const actions=add(panel,'div');actions.className='tracking-actions';
    const start=button(actions,'开始测试'),startHint=add(actions,'span');
    startHint.className='tracking-start-hint';
    const message=add(panel,'p');message.setAttribute('role','status');
    const downloads=add(panel,'div');downloads.className='tracking-actions tracking-downloads';
    const save=button(downloads,'导出 Excel 测试表'),word=button(downloads,'导出回填 Word');
    const output=add(panel,'section');output.setAttribute('aria-label','本次测试报告');
    const R=window.MobileTrackingReport;
    let sheets=[],rules=[],specificRules=[],planRev=0,busy=false,available=[],deviceRev=0,reading=false,current=null,previous=null;
    let fetchingDevices=false,deviceError='';
    function sourceChecks(){return scene.selectedIndex===1?specificRules:rules;}
    async function call(body){
      const reply=await nativeFetch('/api/mobile-tracking',{method:'POST',headers:authHeaders({'Content-Type':'application/json'}),body:JSON.stringify(body)});
      const data=await reply.json();if(!reply.ok||!data.ok)throw Error(data.error||'采集服务不可用');return data;
    }
    function state(){
      const d=available.find(item=>item.id===device.value);
      const requirements=sourceChecks();
      start.disabled=busy||reading||fetchingDevices||!requirements.length||!d?.ready;
      plan.disabled=scene.disabled=busy;
      device.disabled=busy||fetchingDevices||!available.length;refresh.disabled=busy||fetchingDevices;
      downloads.hidden=!current||busy;save.disabled=!current;word.disabled=!current;
      word.hidden=!current?.file?.name.toLowerCase().endsWith('.docx')||current?.specific;
      startHint.textContent=busy?'测试正在运行':reading?'正在解析方案':!plan.files.length?'先选择埋点方案':!requirements.length?'当前方案中没有可测试的需求':!d?.ready?'选择可采集的手机后开始':'';
      badges.replaceChildren();
      const status=(name,value,label,reason)=>{
        const badge=add(badges,'span',name+' · '+label);badge.dataset.status=value;
        if(reason)badge.title=reason;
      };
      if(d){
        const labels={online:'在线',busy:'占用中',offline:'离线',not_listed:'未登记',unknown:'待确认'};
        status('Sonic',d.sonicStatus||'unknown',labels[d.sonicStatus]||'待确认',d.sonicReason);
        status('执行机',d.runnerStatus||'unknown',labels[d.runnerStatus]||'待确认',d.runnerReason);
        status('埋点采集',d.ready?'ready':'blocked',d.ready?'可开始':'未就绪',d.reason);
      }
      deviceState.textContent=fetchingDevices?'正在检查设备连接…':deviceError||d?.reason||(!d?'当前没有可选手机':'');
    }
    async function devices(){
      if(busy)return;
      const revision=++deviceRev,selected=device.value;
      fetchingDevices=true;deviceError='';state();
      try{
        const data=await call({devices:true});if(revision!==deviceRev||!panel.isConnected)return;
        available=Array.isArray(data.devices)?data.devices:[];device.replaceChildren();
        for(const d of available)add(device,'option',d.name).value=d.id;
        device.value=available.find(d=>d.id===selected)?.id||available.find(d=>d.ready)?.id||available[0]?.id||'';
      }catch(e){
        if(revision===deviceRev){
          available=[];device.replaceChildren();deviceError=e.message;
        }
      }finally{if(revision===deviceRev){fetchingDevices=false;state();}}
    }
    refresh.onclick=devices;device.onchange=state;
    function resultFor(session){return R.report(session.rules,session.plans,session.events);}
    function groupEvents(session,id){return session.events.filter(e=>e.reqId===id);}
    function receiptIds(rows){return [...new Set(rows.map(r=>r.e?.reqId).filter(Boolean))];}
    function render(){
      state();output.replaceChildren();if(!current)return;
      const session=current,{sources}=resultFor(session);
      const old=previous && session!==previous;
      if(old){const show=button(output,'下载上一次测试表');show.onclick=()=>exportExcel(previous).catch(e=>{message.textContent='导出失败：'+e.message;});}
      add(output,'h3',session.file.name+' · '+session.deviceName);
      const totals=add(output,'p');totals.className='tracking-totals';
      for(const status of ['通过','差异待确认','失败','待实测']){
        const n=sources.filter(s=>s.status===status).length;
        add(totals,'span',status+' '+n).dataset.conclusion=status;
      }
      const filter=field(output,'筛选本次结论','select');
      for(const label of ['全部结论','通过','差异待确认','失败','待实测'])add(filter,'option',label);
      const wrap=add(output,'div');wrap.className='tracking-table-wrap';
      const table=add(wrap,'table'),head=table.createTHead().insertRow(),list=table.createTBody();
      for(const title of ['需求动作','预期事件','实测结论','原因与接口证据'])add(head,'th',title);
      filter.onchange=()=>{for(const item of list.rows)item.hidden=filter.selectedIndex>0&&item.dataset.status!==filter.value;};
      for(const source of sources){
        const row=list.insertRow();row.dataset.status=source.status;
        row.insertCell().textContent=source.rule[0]||source.rule[4]||'待确认动作';
        row.insertCell().textContent=source.rule[1]||'未指定';
        add(row.insertCell(),'strong',source.status).dataset.conclusion=source.status;
        const evidence=row.insertCell(),detail=add(evidence,'details');
        add(detail,'summary',source.reason||'查看明细');
        add(detail,'p',source.rule[5]||'专项需求');
        for(const part of source.parts){
          if(!part.e)continue;
          add(detail,'p',part.plan.action+' · 实收 '+part.e.event+' · '+part.reason);
          if(!part.e.reqId)continue;
          const inspect=add(detail,'details');add(inspect,'summary','查看接口上报 '+part.e.reqId);
          inspect.addEventListener('toggle',()=>{
            if(!inspect.open||inspect.querySelector('img'))return;
            const img=add(inspect,'img');img.alt='脱敏接口请求与服务端回执 '+part.e.reqId;
            img.src=R.evidenceImage(groupEvents(session,part.e.reqId)).base64;
          });
        }
      }
    }
    plan.onchange=async()=>{
      const rev=++planRev;sheets=[];rules=[];specificRules=[];reading=false;list.hidden=true;contents.replaceChildren();
      scene.replaceChildren();add(scene,'option','全部需求');scene.parentElement.hidden=true;state();
      const file=plan.files[0];if(!file){planState.textContent='支持 Word（.docx）和 Excel（.xlsx）';return;}
      reading=true;state();planState.textContent='正在解析…';
      try{
        const parsed=await extract(file);if(rev!==planRev)return;sheets=parsed;
        rules=window.MobileTrackingSource.checks(parsed);
        specificRules=rules.filter(r=>/告诉我们|取消原因|click_tell_us|pv_cancel_reason_collect/.test([r[0],r[1],r[4]].join(' ')));
        if(specificRules.length){
          add(scene,'option','只测打印取消反馈');scene.parentElement.hidden=false;
        }
        const exposure=rules.filter(r=>r[7]==='exposure').length;
        planState.textContent=(rules.length-exposure)+' 条业务需求 · '+exposure+' 条曝光规则 · '+parsed.filter(s=>s.kind==='reference').length+' 张参考表';
        for(const s of parsed){
          const part=add(contents,'details');add(part,'summary',s.name+' / '+s.sheet);
          part.addEventListener('toggle',()=>{if(part.open&&part.childElementCount===1)for(const row of s.rows)add(part,'div','第 '+row.n+' 行　'+row.values.filter(Boolean).join(' · '));});
        }
        list.hidden=false;
      }catch(e){if(rev===planRev)planState.textContent='解析失败：'+e.message;}
      finally{if(rev===planRev){reading=false;state();}}
    };
    scene.onchange=state;
    function sessionActive(session){
      return panel.isConnected && (typeof sessionToken!=='function' || session.identity===sessionToken());
    }
    function requireActive(session){
      if(!sessionActive(session))throw Error('页面或登录账号已切换，后续采集已停止');
    }
    async function runGroup(session,group){
      requireActive(session);
      const path=group[0].path;
      const run=await call({device:session.device,path});
      session.runIds.push(run.id);
      const deadline=Date.now()+160000;
      while(Date.now()<deadline && panel.isConnected){
        requireActive(session);
        const data=await call({id:run.id});
        requireActive(session);
        if(data.state==='done'){
          const performed=data.performed||[],last=performed.at(-1);
          const done=data.device===session.device&&performed.length===path.length&&performed.every((a,i)=>a.label===path[i]);
          const ids=group.map(g=>g.id);
          for(const e of data.events||[]){
            const inside=done && e.actionIndex===path.length-1 && Number.isFinite(last.time)&&Number.isFinite(last.until)&&
              e.sent>=last.time&&e.sent<=last.until&&e.time>=last.time&&e.time<=last.until;
            session.events.push(R.sanitize(e,run.id,inside?ids:[],data.endpoint));
          }
          for(const item of group)Object.assign(item,{done,batch:run.id,observed:performed.map(a=>a.label).join(' → '),
            reason:done?(data.note||'已完成路径'):(data.note||'未能核对所选手机和预期操作')});
          const {details}=resultFor(session);
          return group.every(item=>details.find(d=>d.plan===item)?.status==='通过');
        }
        await new Promise(resolve=>setTimeout(resolve,1500));
      }
      throw Error('等待设备结果超时，已取得的结果可导出');
    }
    function addPlan(session,source,item){
      const rule=session.rules[source];const p={...item,id:session.plans.length,source,action:item.action||rule[0],event:rule[1],path:item.path||[],steps:item.steps||[]};
      session.plans.push(p);return p;
    }
    start.onclick=async()=>{
      if(!canExecute||start.disabled)return;
      busy=true;
      const session={identity:typeof sessionToken==='function'?sessionToken():null,file:plan.files[0],specific:scene.selectedIndex===1,rules:sourceChecks(),sheets,device:device.value,deviceName:available.find(d=>d.id===device.value).name,plans:[],events:[],runIds:[]};
      previous=current;current=session;render();
      try{
        const source=session.rules.map(([action,event,,properties,feature,origin,additionalFields,kind],id)=>({id,action,event,properties,feature,origin,additionalFields,kind}));
        const hot=source.find(r=>r.event==='AEC'&&r.action.includes('热门')&&/^ele=model_category$/m.test(r.properties));
        let smokeRuns=0,smokePasses=0;
        if(hot){
          const p=addPlan(session,hot.id,{action:'点击热门',steps:['打开首页','切换到推荐分类','点击热门'],path:['首页','推荐','热门']});
          message.textContent='正在验证「热门」';
          if(await runGroup(session,[p]))smokePasses++;
          smokeRuns++;render();
        }
        for(let i=0;i<source.length;i+=12){
          requireActive(session);
          message.textContent='AI 正在拆解第 '+(i+1)+'–'+Math.min(i+12,source.length)+' 条需求';
          const batch=source.slice(i,i+12),reply=await call({requirements:batch});
          for(const item of reply.items||[]){
            if(!batch.some(r=>r.id===item.id))continue;
            if(hot && item.id===hot.id && !item.path?.length && item.action===hot.action && !item.steps?.length)continue;
            if(hot&&item.id===hot.id&&item.path?.at(-1)==='热门')continue;
            addPlan(session,item.id,item);
          }
        }
        const paths=new Map();
        for(const item of session.plans){
          if(!item.path.length||item.observed!==undefined)continue;
          const key=JSON.stringify(item.path);if(!paths.has(key))paths.set(key,[]);paths.get(key).push(item);
        }
        const groups=[...paths.values()];
        for(let i=0;i<groups.length;i++){
          if(smokeRuns===3&&smokePasses<2){
            groups.slice(i).flat().forEach(p=>p.reason='首批三条流程通过不足 50%，后续流程待实测');break;
          }
          message.textContent='正在执行 '+(i+1)+' / '+groups.length+' · '+groups[i][0].path.join(' → ');
          const passed=await runGroup(session,groups[i]);
          if(smokeRuns<3){smokeRuns++;if(passed)smokePasses++;}
        }
        message.textContent='测试结束，已生成本次报告。';
      }catch(e){
        for(const p of session.plans)if(!p.batch)p.reason=e.message;
        session.rules.forEach((r,i)=>{if(!session.plans.some(p=>p.source===i))addPlan(session,i,{reason:e.message});});
        message.textContent=e.message+'。已取得的结果可导出。';
      }finally{
        if(!sessionActive(session)){busy=false;return;}
        render();
        try{
          const rows=resultFor(session).sources.map(s=>({origin:s.rule[5]||'专项需求',action:s.rule[0],event:s.rule[1],receiptIds:receiptIds(s.parts),status:s.status,reason:s.reason}));
          await call({saveReport:{title:session.file.name,device:session.device,rows,runIds:session.runIds}});
          if(sessionActive(session))panel.dispatchEvent(new CustomEvent('tracking-report-saved',{bubbles:true}));
        }catch(e){message.textContent+=' 历史记录保存失败：'+e.message+'。本页结果仍可导出。';}
        if(!sessionActive(session)){busy=false;return;}
        try{await exportExcel(session);}catch(e){message.textContent='自动导出失败，可再次点击「导出 Excel 测试表」：'+e.message;}
        busy=false;state();
      }
    };
    async function exportExcel(session){
      const {sources,details}=resultFor(session);const wb=new ExcelJS.Workbook();
      const sheet=(name,header,widths,rows)=>{
        const s=wb.addWorksheet(name,{views:[{state:'frozen',ySplit:1}]});
        s.addRow(header);for(const row of rows)s.addRow(row);
        s.columns.forEach((c,i)=>c.width=widths[i]||28);s.getRow(1).font={bold:true,color:{argb:'FFFFFFFF'}};
        s.getRow(1).fill={type:'pattern',pattern:'solid',fgColor:{argb:'FF245449'}};return s;
      };
      sheet('结论',['来源','需求动作','方案事件','结论','原因'],[34,36,24,18,82],sources.map(s=>[s.rule[5]||'专项需求',s.rule[0],s.rule[1],s.status,s.reason]));
      sheet('本轮范围',['测试手机','方案文件','场景','事件口径'],[28,40,30,72],[[session.deviceName,session.file.name,session.specific?'告诉我们':'使用上传方案','CC 与 CCZ 表示内容点击，CS 与 CSZ 分开核对。']]);
      sheet('AI 测试步骤',['来源','动作','步骤','实际路径','结论','需求字段','重点记录','实收字段','请求编号','说明'],[34,32,55,34,18,60,55,70,26,80],details.map(d=>{
        const r=session.rules[d.plan.source];return [r[5],d.plan.action,d.plan.steps.join('\n'),d.plan.observed,d.status,r[3],r[6],d.actual&&JSON.stringify(d.actual),d.e?.reqId,d.reason];
      }));
      sheet('抓包事件',['请求编号','请求时间','事件时间','接口','请求分组','实收类型','事件名称','保留属性','HTTP 状态','业务码','回执','是否匹配操作窗口'],[26,34,34,62,18,18,26,95,16,14,20,26],session.events.map(e=>[e.reqId,R.when(e.sent),R.when(e.time),e.endpoint,e.requestType,e.event,e.name,JSON.stringify(e.contents),e.httpStatus,e.businessCode,e.ack,e.ids.length?'是':'待确认']));
      const ids=receiptIds(details);
      if(ids.length){
        const pictures=wb.addWorksheet('接口上报截图');pictures.getColumn(1).width=126;let row=1;
        for(const id of ids){
          const image=R.evidenceImage(groupEvents(session,id));pictures.getCell(row,1).value=id;
          const img=wb.addImage({base64:image.base64,extension:'png'});pictures.addImage(img,{tl:{col:0,row},ext:{width:image.width,height:image.height}});
          row+=Math.ceil(image.height/20)+3;
        }
      }
      session.sheets.forEach((s,i)=>{const raw=wb.addWorksheet('方案表'+(i+1));raw.addRow([s.name,s.sheet]);for(const r of s.rows)raw.addRow([r.n,...r.values]);raw.columns.forEach(c=>c.width=30);});
      const url=URL.createObjectURL(new Blob([await wb.xlsx.writeBuffer()]));
      const a=add(document.body,'a');a.href=url;a.download=session.deviceName+'-埋点实测.xlsx';a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
    save.onclick=()=>exportExcel(current).catch(e=>{message.textContent='导出失败，可重试：'+e.message;});
    word.onclick=async()=>{
      const session=current;try{
        const entries=resultFor(session).sources.map(s=>({origin:s.rule[5],action:s.rule[0],status:s.status,reason:s.reason,
          receipts:receiptIds(s.parts).map(id=>({id,png:R.evidenceImage(groupEvents(session,id)).base64}))}));
        await window.TrackingWord.download(session.file,entries);
      }catch(e){message.textContent='Word 导出失败，可重试：'+e.message;}
    };
    state();
    devices();
    return panel;
  }
  return {attach,libraries};
})();
