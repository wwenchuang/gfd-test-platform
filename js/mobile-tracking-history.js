// Current user's previous runs and the shared, dated acceptance report.
window.TrackingHistory = (() => {
  function mount(panel, allowed) {
    const add=(parent,tag,value='')=>{const e=document.createElement(tag);e.textContent=value;parent.append(e);return e;};
    const section=add(panel,'section');section.className='tracking-history';section.setAttribute('aria-label','实测报告');
    const heading=add(section,'header');heading.className='tracking-history-head';add(heading,'h2','实测报告');
    const refresh=add(heading,'button','刷新报告');refresh.type='button';
    const filters=add(section,'div');filters.className='tracking-history-filters';
    const query=add(filters,'input');query.type='search';query.placeholder='搜索报告标题';query.setAttribute('aria-label','搜索报告标题');
    const device=add(filters,'select');device.setAttribute('aria-label','筛选报告设备');
    const allDevices=add(device,'option','全部设备');allDevices.value='';
    const message=add(section,'div');message.setAttribute('role','status');
    const body=add(section,'div');body.className='tracking-report-list';
    let cards=[];
    let revision=0;
    let closeReceipt=()=>{};
    async function load(payload) {
      const res=await nativeFetch('/api/mobile-tracking',{method:'POST',headers:authHeaders({'Content-Type':'application/json'}),body:JSON.stringify(payload)});
      const data=await res.json();
      if(!res.ok||!data.ok)throw Error(data.error||'读取失败，请重试');
      return data;
    }
    function showReceipt(report,row,opener){
      if(!allowed())return;
      closeReceipt();
      const dialog=add(section,'dialog');dialog.className='tracking-receipt-dialog';dialog.setAttribute('aria-label','查看接口证据');
      add(dialog,'h3',row.action+' · 接口证据');add(dialog,'p',row.origin);
      const selector=add(dialog,'select');selector.setAttribute('aria-label','选择请求编号');
      for(const id of row.receiptIds)add(selector,'option',id);
      const status=add(dialog,'p');status.setAttribute('role','status');
      const img=add(dialog,'img');img.alt='本次接口的脱敏请求与回执截图';img.hidden=true;
      img.style.maxWidth='100%';img.style.height='auto';
      const actions=add(dialog,'div');actions.className='tracking-actions';
      const retry=add(actions,'button','重试读取截图');retry.type='button';retry.hidden=true;
      const back=add(actions,'button','返回逐项结论');back.type='button';back.autofocus=true;
      let epoch=0,url='';
      function clearImage(){
        img.hidden=true;img.removeAttribute('src');
        if(url){URL.revokeObjectURL(url);url='';}
      }
      const close=()=>{
        if(!dialog.isConnected)return;
        ++epoch;clearImage();if(dialog.open)dialog.close();dialog.remove();
        if(opener.isConnected)opener.focus();
        closeReceipt=()=>{};
      };
      closeReceipt=close;back.onclick=close;dialog.oncancel=e=>{e.preventDefault();close();};
      async function open(){
        if(!allowed())return;
        const stamp=++epoch;clearImage();retry.hidden=true;status.textContent='正在读取接口截图…';
        try{
          const response=await nativeFetch('/api/mobile-tracking',{method:'POST',headers:authHeaders({'Content-Type':'application/json'}),body:JSON.stringify({publishedReceipt:{report,id:selector.value}})});
          if(!response.ok||!/^image\/png\b/i.test(response.headers.get('Content-Type')||''))throw Error();
          const blob=await response.blob();
          if(!allowed()||stamp!==epoch||!dialog.isConnected)return;
          url=URL.createObjectURL(blob);img.src=url;
          await img.decode();
          if(!allowed()||stamp!==epoch||!dialog.isConnected)return;
          img.hidden=false;status.textContent='请求编号 '+selector.value;
        }catch{
          if(allowed()&&stamp===epoch&&dialog.isConnected){clearImage();status.textContent='接口截图读取失败，可重试。';retry.hidden=false;}
        }
      }
      selector.onchange=open;retry.onclick=open;dialog.showModal();open();
    }
    function table(rows,preview,published,receiptReport){
      const filter=add(preview,'select');filter.setAttribute('aria-label','筛选测试结论');
      for(const s of ['全部结论','通过','差异待确认','待实测','失败']) add(filter,'option',s);
      const wrap=add(preview,'div');wrap.className='tracking-report-table-wrap';
      const t=add(wrap,'table'),thead=t.createTHead().insertRow(),tbody=t.createTBody();
      const headers=['原表位置','动作','结论','说明'];
      if(published)headers.push('接口证据');
      for(const s of headers)add(thead,'th',s);
      function render(){tbody.replaceChildren();for(const r of rows){
        if(filter.selectedIndex && filter.value!==r.status)continue;
        const tr=tbody.insertRow();for(const v of [r.origin,r.action,r.status,r.reason])tr.insertCell().textContent=v||'—';
        if(published){
          const cell=tr.insertCell();
          if(receiptReport && r.receiptIds?.length){
            const btn=add(cell,'button','查看接口证据');btn.type='button';btn.setAttribute('aria-haspopup','dialog');
            btn.onclick=()=>showReceipt(receiptReport,r,btn);
          }else cell.textContent='暂无关联截图';
        }
      }if(!tbody.rows.length){const cell=tbody.insertRow().insertCell();cell.colSpan=headers.length;cell.textContent='没有符合此结论的需求行。';}}
      filter.onchange=render;render();
    }
    function downloadButton(parent,id,text,filename) {
      const btn=add(parent,'button',text);btn.type='button';
      const notice=add(parent,'span');notice.setAttribute('role','status');
      btn.onclick=async()=>{
        if(!allowed())return;
        btn.disabled=true;notice.textContent='正在下载…';
        try{
          const res=await nativeFetch('/api/mobile-tracking',{method:'POST',headers:authHeaders({'Content-Type':'application/json'}),body:JSON.stringify({publishedFile:id})});
          if(!res.ok)throw Error('下载失败');
          const blob=await res.blob();
          if(!allowed()||!parent.isConnected)return;
          const url=URL.createObjectURL(blob),link=add(parent,'a');
          link.href=url;link.download=filename;link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);
          notice.textContent='已开始下载。';
        }catch{notice.textContent='下载失败，点击按钮可重试。';}finally{btn.disabled=false;}
      };
    }
    function personalExport(controls,report){
      const button=add(controls,'button','导出本次实测 Excel');button.type='button';
      const notice=add(controls,'span');notice.setAttribute('role','status');
      const active=()=>allowed()&&controls.isConnected;
      button.onclick=async()=>{
        if(!active())return;
        button.disabled=true;notice.textContent='正在回读本次手机采集记录…';
        try{
          await window.MobileTracking.libraries();
          const R=window.MobileTrackingReport,book=new ExcelJS.Workbook();
          const runs=[],byId=new Map(),observations=[];
          const runIds=[...new Set(report.runIds||[])];
          for(const id of runIds){
            if(!active())return;
            let data;
            try{data=await load({id});}
            catch{runs.push([id,'暂时无法读取，可重试导出','',0]);continue;}
            if(data.state!=='done'||!data.device||data.device!==report.device){
              runs.push([id,data.state!=='done'?'未完成采集':'无法核对报告设备','',0]);continue;
            }
            const events=(data.events||[]).map(raw=>R.sanitize(raw,id,[],data.endpoint));
            runs.push([id,'已回读',(data.performed||[]).map(a=>a.label).join(' → '),events.length]);
            for(const event of events){
              observations.push(event);
              if(!event.reqId)continue;
              if(!byId.has(event.reqId))byId.set(event.reqId,[]);
              byId.get(event.reqId).push(event);
            }
          }
          if(!active())return;
          const linked=new Set();
          const results=report.rows.map(r=>{
            const expected=Array.isArray(r.receiptIds)?[...new Set(r.receiptIds)]:null;
            const read=[],missing=[];
            for(const id of expected||[]){
              const group=byId.get(id);
              if(group && group.every(e=>e.group===group[0].group)) {linked.add(id);read.push(id);}
              else missing.push(id);
            }
            const notes=[];
            if(!expected)notes.push('旧报告未保存逐项请求编号，回读事件仅归属整次运行');
            else if(!expected.length)notes.push('此项未保存已关联请求');
            if(missing.length)notes.push('未能唯一回读的请求：'+missing.join('、'));
            return [r.origin,r.action,r.event||'未记录',r.status,r.reason,read.join('、'),notes.join('；')||'已关联'];
          });
          function sheet(name,headers,widths,rows){
            const s=book.addWorksheet(name,{views:[{state:'frozen',ySplit:1}]});
            s.addRow(headers);s.addRows(rows);
            s.columns.forEach((column,i)=>{column.width=widths[i];column.alignment={vertical:'top',wrapText:true};});
            const header=s.getRow(1);header.font={bold:true,color:{argb:'FFFFFFFF'}};
            header.fill={type:'pattern',pattern:'solid',fgColor:{argb:'FF245449'}};
            s.autoFilter={from:{row:1,column:1},to:{row:1,column:headers.length}};
            return s;
          }
          sheet('逐项结论',['原表位置','需求动作','方案事件','保存的结论','原结论说明','回读的请求编号','证据说明'],[30,36,22,18,65,37,62],results);
          sheet('本轮范围',['方案','测试手机','保存时间','采集记录数','回读事件数','有逐项归属的请求数'],[42,28,30,20,20,28],
            [[report.title,report.deviceName||report.device||'未记录',R.when(report.created),runIds.length,observations.length,linked.size]]);
          sheet('采集记录',['采集编号','读取结果','实际操作','事件数'],[36,36,64,14],runs);
          sheet('抓包事件',['请求编号','采集编号','归属','请求时间','事件时间','接口','请求分组','实收事件','事件名称','脱敏字段','HTTP 状态','业务码','回执'],
            [28,35,26,31,31,58,17,18,23,78,16,14,18],observations.map(e=>[e.reqId,e.group,linked.has(e.reqId)?'已关联需求行':'仅归属整次运行',R.when(e.sent),R.when(e.time),e.endpoint,e.requestType,e.event,e.name,JSON.stringify(e.contents),e.httpStatus,e.businessCode,e.ack]));
          if(linked.size){
            const pictures=book.addWorksheet('接口上报截图');pictures.getColumn(1).width=126;
            let row=1;
            for(const id of linked){
              const img=R.evidenceImage(byId.get(id));pictures.getCell(row,1).value=id;
              const imageId=book.addImage({base64:img.base64,extension:'png'});
              pictures.addImage(imageId,{tl:{col:0,row},ext:{width:img.width,height:img.height}});
              row+=Math.ceil(img.height/20)+3;
            }
          }
          const bytes=await book.xlsx.writeBuffer();
          if(!active())return;
          const url=URL.createObjectURL(new Blob([bytes],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));
          const link=add(controls,'a');link.href=url;
          const name=(report.deviceName||'手机').replace(/[\\/:*?"<>|]/g,'-');
          const title=report.title.replace(/\.(docx|xlsx)$/i,'').replace(/[\\/:*?"<>|]/g,'-');
          link.download=name+'-'+title+'-埋点实测.xlsx';link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);
          const unread=runs.filter(run=>run[1]!=='已回读').length;
          notice.textContent=unread?'已导出，'+unread+' 条采集记录未能回读。可再次导出重试。':'已导出，含 '+linked.size+' 笔逐项关联的接口证据。';
        }catch(e){if(active())notice.textContent='导出失败，可点击重试：'+e.message;}
        finally{button.disabled=false;}
      };
    }
    const deviceNames={
      A9TU024622000978:'荣耀 Magic Vs3',ecbfd645:'OPPO Reno9','00008110-000150CC21C2801E':'iPhone 13'
    };
    function describe(report,published){
      const archiveId=report.files?.[0]?.id;
      const archiveDevice={honor:'荣耀 Magic Vs3',iphone:'iPhone 13'}[archiveId];
      const name=report.deviceName||deviceNames[report.device]||report.device||archiveDevice||'设备未记录';
      const publishedDate=report.title.match(/\b\d{4}-\d{2}-\d{2}\b/)?.[0];
      const date=report.created?new Date(report.created*1000).toLocaleString('zh-CN',{hour12:false}):publishedDate;
      return {title:report.title,name,date:date||'日期未记录',published};
    }
    function card(report,published){
      const info=describe(report,published),block=add(body,'article');block.className='tracking-report-card';
      add(block,'h3',info.title);
      const meta=add(block,'p');meta.className='tracking-report-meta';
      meta.textContent=[info.name,info.date,published?'已发布':'我的实测'].join(' · ');
      const rows=report.rows||[],counts=add(block,'p');counts.className='tracking-report-counts';
      for(const [label,key] of [['通过','通过'],['差异','差异待确认'],['待实测','待实测'],['失败','失败']]){
        const count=rows.filter(row=>row.status===key).length;
        if(label==='失败'&&!count)continue;
        const badge=add(counts,'span',label+' '+count);badge.dataset.result=key;
      }
      const exposure=rows.filter(row=>String(row.origin).includes('正文')).length;
      const caption=add(counts,'span',exposure?('业务 '+(rows.length-exposure)+' 项 · 曝光 '+exposure+' 项'):('共 '+rows.length+' 项'));
      caption.className='tracking-report-scope';
      if(report.note)add(block,'p',report.note).className='tracking-report-note';
      const controls=add(block,'div');controls.className='tracking-actions tracking-report-actions';
      if(published){for(const file of report.files||[])downloadButton(controls,file.id,file.label,file.name);}
      else personalExport(controls,report);
      const previewButton=add(controls,'button','预览逐项结论');previewButton.type='button';previewButton.setAttribute('aria-expanded','false');
      const preview=add(block,'div');preview.className='tracking-report-preview';preview.hidden=true;
      previewButton.onclick=()=>{
        if(!allowed())return;
        if(!preview.children.length)table(rows,preview,published,report.receiptReport);
        preview.hidden=!preview.hidden;previewButton.setAttribute('aria-expanded',String(!preview.hidden));
        previewButton.textContent=preview.hidden?'预览逐项结论':'收起预览';
      };
      if(!published&&report.runIds?.length){
        const button=add(controls,'button','查看采集记录');button.type='button';
        const evidence=add(block,'div');evidence.className='tracking-report-evidence';
        button.onclick=async()=>{
          if(!allowed())return;
          button.disabled=true;evidence.replaceChildren();
          for(const id of report.runIds){
            if(!allowed()||!block.isConnected)return;
            try{
              const run=await load({id});
              if(!allowed()||!block.isConnected)return;
              if(run.state!=='done'||!run.device||run.device!==report.device){add(evidence,'p','本条采集尚未取得可核验的设备结果。');continue;}
              const entry=add(evidence,'details');
              add(entry,'summary',(run.performed||[]).map(x=>x.label).join(' → ')||run.note||'采集结果');
              for(const raw of run.events||[]){
                const e=window.MobileTrackingReport.sanitize(raw,id,[],run.endpoint);
                add(entry,'p',[e.reqId,e.event,e.name,'HTTP '+(e.httpStatus??'—'),'业务码 '+(e.businessCode??'—'),e.ack].join(' · '));
              }
            }catch{if(allowed()&&block.isConnected)add(evidence,'p','有一条采集记录暂时无法读取，可点击重试。');}
          }
          button.disabled=false;
        };
      }
      return {block,...info};
    }
    function filterCards(){
      const keyword=query.value.trim().toLocaleLowerCase();
      for(const item of cards)item.block.hidden=(device.value&&item.name!==device.value)||!item.title.toLocaleLowerCase().includes(keyword);
      if(!cards.length)message.textContent='还没有可查看的实测报告。';
      else if(cards.every(item=>item.block.hidden))message.textContent='没有匹配的报告，请调整标题或设备筛选。';
      else message.textContent='';
    }
    query.oninput=filterCards;device.onchange=filterCards;
    async function reload(){
      const stamp=++revision;
      if(!allowed())return;
      refresh.disabled=true;message.textContent='正在读取报告…';
      try{
        const data=await load({history:true});
        if(!allowed()||!section.isConnected||revision!==stamp)return;
        const previous=device.value;
        closeReceipt();body.replaceChildren();
        cards=[...(data.reports||[]).map(r=>card(r,false)),...(data.published?.reports||[]).map(r=>card(r,true))];
        device.replaceChildren(allDevices);
        for(const name of [...new Set(cards.map(c=>c.name))].sort())add(device,'option',name);
        if([...device.options].some(opt=>opt.value===previous))device.value=previous;
        filterCards();refresh.textContent='刷新报告';
      }catch{
        if(allowed()&&section.isConnected&&revision===stamp){
          message.textContent='报告读取失败，可重试。';refresh.textContent='重试读取报告';
        }
      }finally{if(revision===stamp)refresh.disabled=false;}
    }
    refresh.onclick=reload;reload();
    const dispose=()=>{revision++;closeReceipt();section.remove();};
    dispose.refresh=reload;
    dispose.showDevice=name=>{
      if(!allowed())return;
      query.value='';
      if(!Array.from(device.options).some(option=>option.value===name))add(device,'option',name);
      device.value=name;filterCards();
    };
    return dispose;
  }
  return {mount,attach:mount};
})();
