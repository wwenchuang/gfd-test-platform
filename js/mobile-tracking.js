// Parse the plan locally. Raw capture bodies never leave the browser.
window.MobileTracking = (() => {
  const steps = [
    ['显示打印已取消弹窗','pv_print_canceled_toast',true],
    ['点击告诉我们','click_tell_us',true],
    ['进入取消原因收集页','pv_cancel_reason_collect',true],
    ['停留且未提交反馈','click_cancel',false]
  ];
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
    const bytes=await file.arrayBuffer();
    let books=[[file.name,bytes]];
    if(file.name.toLowerCase().endsWith('.docx')){
      const doc=await JSZip.loadAsync(bytes);
      const entries=doc.file(/^word\/embeddings\/[^/]+\.xlsx$/i);
      books=[];
      for(const entry of entries) books.push([entry.name.split('/').pop(),await entry.async('uint8array')]);
    }
    const sheets=[];
    for(const [name,content] of books){
      const zip=await JSZip.loadAsync(content);
      async function xml(path){
        const member=zip.file(path);
        if(!member)throw Error('工作表缺少 '+path);
        const root=new DOMParser().parseFromString(await member.async('string'),'application/xml');
        if(root.querySelector('parsererror'))throw Error('表格 XML 不完整');
        return root;
      }
      const texts=node=>Array.from(node.getElementsByTagNameNS('*','t'),t=>t.textContent).join('');
      const strings=zip.file('xl/sharedStrings.xml')?Array.from((await xml('xl/sharedStrings.xml')).getElementsByTagNameNS('*','si'),texts):[];
      const rels=Array.from((await xml('xl/_rels/workbook.xml.rels')).getElementsByTagNameNS('*','Relationship'));
      for(const tab of (await xml('xl/workbook.xml')).getElementsByTagNameNS('*','sheet')){
        const rel=rels.find(r=>r.getAttribute('Id')===tab.getAttribute('r:id'));
        if(!rel)throw Error('缺少工作表引用');
        const path=new URL(rel.getAttribute('Target'),'https://local.invalid/xl/').pathname.slice(1);
        const rows=[];
        for(const row of (await xml(path)).getElementsByTagNameNS('*','row')){
          const values=[];
          for(const cell of row.getElementsByTagNameNS('*','c')){
            const ref=cell.getAttribute('r')||'';
            let col=0;for(const char of ref.match(/^[A-Z]+/)?.[0]||'')col=col*26+char.charCodeAt(0)-64;
            if(!col||col>16384)continue;
            const value=cell.getElementsByTagNameNS('*','v')[0]?.textContent||'';
            const kind=cell.getAttribute('t');
            values[col-1]=kind==='s'?strings[Number(value)]:kind==='inlineStr'?texts(cell):value;
          }
          if(values.some(v=>v?.trim()))rows.push({n:Number(row.getAttribute('r')),values});
        }
        sheets.push({name,sheet:tab.getAttribute('name'),rows});
      }
    }
    if(!sheets.length)throw Error('未发现内嵌 Excel 或工作表');
    return sheets;
  }
  function attach(page){
    function add(parent,tag,text){
      const el=document.createElement(tag);el.textContent=text||'';parent.append(el);return el;
    }
    const panel=add(page,'details');panel.className='ui-practice-evidence';
    add(panel,'summary','手机埋点测试');
    function fileInput(label,accept){
      const field=add(panel,'label',label+' '), el=add(field,'input');el.type='file';el.accept=accept;add(panel,'br');return el;
    }
    const plan=fileInput('上传埋点方案','.docx,.xlsx');
    const planState=add(panel,'p','请选择 Word 方案或 Excel 表。');
    const list=add(panel,'details');add(list,'summary','查看解析明细');
    const contents=add(list,'div');
    const scene=add(panel,'select');scene.setAttribute('aria-label','测试场景');
    add(scene,'option','使用上传方案');
    add(scene,'option','打印取消反馈 · 告诉我们');
    const device=add(panel,'select');device.setAttribute('aria-label','选择手机');
    add(device,'option','荣耀 Magic Vs3').value='A9TU024622000978';
    add(device,'option','OPPO Reno9 · ecbfd645').value='ecbfd645';
    const start=add(panel,'button','开始测试');start.type='button';
    const message=add(panel,'p');message.setAttribute('role','status');
    const evidence=fileInput('操作或前置状态截图','image/png,image/jpeg');
    const preview=add(panel,'img');preview.hidden=true;preview.style.maxWidth='min(100%, 380px)';
    const output=add(panel,'pre'),save=add(panel,'button','导出 Excel 测试表');save.type='button';
    let sheets=[],events=[],photo=null,photoUrl='',planRev=0,photoRev=0;
    let frames=[],busy=false,plans=[];
    function sourceChecks(){
      if(scene.selectedIndex===1)return steps;
      const planned=[];
      for(const sheet of sheets){
        const header=sheet.rows[0]?.values||[];
        const code=header.indexOf('使用事件'),action=header.indexOf('需要统计的行为'),props=header.findIndex(x=>x?.includes('标识'));
        for(const row of sheet.rows.slice(1))if(code>=0&&row.values[code])planned.push([row.values[action]||sheet.sheet,row.values[code],true,row.values[props]||'',row.values[1]||sheet.sheet,sheet.name+' / '+sheet.sheet+' 第 '+row.n+' 行']);
      }
      return planned;
    }
    function checks(){
      const source=sourceChecks();
      return plans.length?plans.map(p=>[p.action||source[p.source][0],...source[p.source].slice(1)]):source;
    }
    function rows(){
      const rules=checks();
      return rules.map(([step,event,wanted,props],id)=>{
        const conditions=(props||'').trim().split(/\n/).filter(Boolean).map(v=>v.trim().replace(/^[`]+|[；;`]+$/g,'').match(/^([a-zA-Z][\w]*)=([^=]+)$/));
        const unique=plans[id]?.observed && !plans.some((p,j)=>j!==id && p.observed===plans[id].observed && rules[j][1]===event && rules[j][3]===props);
        const defined=unique && (scene.selectedIndex===1 || (conditions.length>0 && conditions.every(Boolean)));
        const matches=events.filter(x=>x.ids.includes(id) && String(x.event).toUpperCase()===plans[id]?.event && (!conditions.length || (x.contents||[x.fields]).some(item=>conditions.every(c=>c && item?.[c[1]]===c[2]))));
        const aligned=matches.filter(x=>x.frame!=null);
        return [step,event,wanted?'出现':'不出现',events.length?matches.length:null,
          aligned.map(x=>new Date(x.time*1000).toISOString()+' / 截图 '+(x.frame+1)).join('\n'),
          !defined||!aligned.length?'待实测':!wanted||aligned.some(x=>x.ack==='上报失败')?'失败':aligned.some(x=>x.ack==='已接收')?'通过':'待实测'];

      });
    }
    function render(){output.textContent=rows().map(r=>r[0]+'　'+r[5]).join('\n');save.disabled=!sheets.length||busy;start.disabled=!sheets.length||busy;scene.disabled=busy;plan.disabled=busy;device.disabled=busy;}
    plan.onchange=async()=>{
      plans=[];events=[];frames=[];photo=null;preview.hidden=true;message.textContent='';const rev=++planRev;sheets=[];contents.replaceChildren();render();
      const file=plan.files[0];if(!file)return;
      planState.textContent='正在解析…';
      try{
        const parsed=await extract(file);if(rev!==planRev)return;sheets=parsed;
        for(const s of sheets){
          const part=add(contents,'details');add(part,'summary',s.name+' / '+s.sheet);
          for(const row of s.rows)add(part,'div','第 '+row.n+' 行　'+row.values.filter(Boolean).join(' · '));
        }
        planState.textContent='已解析 '+sheets.length+' 张工作表';
      }catch(e){if(rev===planRev)planState.textContent='解析失败：'+e.message;}render();
    };
    scene.onchange=()=>{plans=[];events=[];frames=[];photo=null;preview.hidden=true;message.textContent='';render();};
    async function call(body){
      const reply=await nativeFetch('/api/mobile-tracking',{method:'POST',headers:authHeaders({'Content-Type':'application/json'}),body:JSON.stringify(body)});
      const data=await reply.json();if(!reply.ok||!data.ok)throw Error(data.error||'采集服务不可用');return data;
    }
    async function runGroup(group){
      const path=group[0].path;
      const run=await call({device:device.value,path});
      const deadline=Date.now()+160000;
      while(Date.now()<deadline && panel.isConnected){
        const data=await call({id:run.id});
        if(data.state==='done'){
          const offset=frames.length;
          const pictures=data.frames||[], performed=data.performed||[];
          const done=performed.length===path.length && performed.every((a,i)=>a.label===path[i]);
          const ids=done?group.map(g=>g.id):[];
          frames.push(...pictures);
          for(const e of data.events||[]){
            let frame=null,distance=3;
            pictures.forEach((f,i)=>{const d=Math.abs(f.time-e.time);if(d<distance){frame=offset+i;distance=d;}});
            events.push({...e,frame,ids:done&&e.time>=performed.at(-1).time && e.sent>=performed.at(-1).time?ids:[]});
          }
          for(const g of group){
            g.endpoint=data.endpoint||'';
            g.observed=performed.map(a=>a.label).join(' → ');
            g.actualTypes=[...new Set((data.events||[]).filter(e=>String(e.event).toUpperCase()===g.event).map(e=>e.requestType).filter(Boolean))].join('、');
            g.reason=done?(data.note||'采集结束'):(data.note||'待实测')+'；未完成预期操作';
          }
          const verdicts=rows();
          return group.every(g=>verdicts[g.id][5]==='通过');
        }
        await new Promise(resolve=>setTimeout(resolve,1500));
      }
      throw Error('等待设备结果超时');
    }
    start.onclick=async()=>{
      busy=true;plans=[];events=[];frames=[];photo=null;preview.hidden=true;render();
      try{
        const source=sourceChecks().map(([action,event,,properties,feature,origin],id)=>({id,action,event,properties,feature,origin}));
        const hot=source.find(r=>r.event==='AEC' && r.action.includes('热门') && /^ele=model_category$/m.test(r.properties));
        if(hot){
          const first={...hot,id:0,source:hot.id,action:'点击热门',steps:['在首页点击可见的热门分类'],path:['热门']};
          plans.push(first);
          message.textContent='正在验证首页「热门」埋点';
          await runGroup([first]);
        }
        for(let i=0;i<source.length;i+=12){
          message.textContent='AI 正在分析需求行 '+(i+1)+'–'+Math.min(i+12,source.length);
          const batch=source.slice(i,i+12);
          const reply=await call({requirements:batch});
          for(const item of reply.items){if(hot && item.id===hot.id && item.path?.at(-1)==='热门')continue;plans.push({...item,source:item.id,id:plans.length,origin:source[item.id]?.origin});}
        }
        const paths=new Map();
        for(const item of plans){
          if(!item?.path.length || item.observed!==undefined)continue;
          const key=JSON.stringify(item.path);
          if(!paths.has(key))paths.set(key,[]);
          paths.get(key).push(item);
        }
        const groups=[...paths.values()];
        let passed=0;
        for(let i=0;i<groups.length;i++){
          if(i===3 && passed<2){
            groups.slice(i).flat().forEach(g=>g.reason='首批 3 条流程通过不足 50%，待实测');break;
          }
          message.textContent='手机正在自动执行 '+(i+1)+' / '+groups.length+'：'+groups[i][0].path.join(' → ');
          if(await runGroup(groups[i]))passed++;
        }
        message.textContent='本轮结束，已生成实测表。';
      }catch(e){
        sourceChecks().forEach((r,i)=>{if(!plans.some(p=>p.source===i))plans.push({id:plans.length,source:i,feature:r[4],action:r[0],path:[],steps:[],reason:e.message});});
        message.textContent=e.message+' · 未取得证据的项目待实测';
      }
      finally{busy=false;render();await save.onclick();}
    };
    evidence.onchange=async()=>{
      const rev=++photoRev;photo=null;preview.hidden=true;if(photoUrl)URL.revokeObjectURL(photoUrl);
      const file=evidence.files[0];if(!file)return;
      const bytes=await file.arrayBuffer();if(rev!==photoRev)return;
      photo={bytes,ext:file.type==='image/jpeg'?'jpeg':'png',name:file.name};
      photoUrl=URL.createObjectURL(file);preview.src=photoUrl;preview.hidden=false;
    };
    save.onclick=async()=>{
      try{
        const wb=new ExcelJS.Workbook();const result=wb.addWorksheet('结论');
        result.addRow(['测试手机',device.selectedOptions[0].textContent]);result.addRow(['场景',scene.value]);result.addRow(['规则来源',scene.selectedIndex===1?'打印取消原因收集专项需求':plan.files[0]?.name]);
        result.addRow(['手机操作','事件','预期','观测条数','发生时间与截图','结论']);rows().forEach(row=>result.addRow(row));
        result.columns.forEach((c,i)=>c.width=[28,34,12,14,32,16][i]);
        const design=wb.addWorksheet('AI 测试步骤');
        design.addRow(['需求来源','功能','需求事件','AI 预估请求分组','实收请求分组','测试步骤','实际手机操作','采集地址','执行说明','实测结论']);
        const verdicts=rows();
        checks().forEach((rule,i)=>{
          const p=plans[i]||{};
          design.addRow([rule[5],p.feature||rule[4],p.event||rule[1],p.transport||'未知',p.actualTypes||'未观测',(p.steps||[]).join('\n'),p.observed||'',p.endpoint||'',p.reason||'本轮未执行',verdicts[i][5]]);
        });
        design.columns.forEach(c=>c.width=34);
        if(frames.length){
          const pictures=wb.addWorksheet('手机现场截图');
          frames.forEach((frame,i)=>{
            const row=34*i;pictures.getCell(row+1,1).value='截图 '+(i+1)+' · '+new Date(frame.time*1000).toISOString();
            const id=wb.addImage({base64:'data:image/png;base64,'+frame.png,extension:'png'});
            pictures.addImage(id,{tl:{col:0,row:row+1},br:{col:5,row:row+33}});
          });
        }
        const observations=wb.addWorksheet('抓包事件');observations.addRow(['请求类型','事件类型','事件名称','发生时间','请求时间','最近现场截图','内容属性','上报回执','HTTP 状态','业务码']);
        events.forEach(e=>observations.addRow([e.requestType,e.event,e.name,e.time?new Date(e.time*1000).toISOString():'未提供',new Date((e.sent||e.time)*1000).toISOString(),e.frame==null?'无相邻截图':e.frame+1,JSON.stringify(e.contents||e.fields),e.ack,e.httpStatus,e.businessCode]));
        if(photo){
          const sheet=wb.addWorksheet('截图');sheet.addRow(['操作或前置状态截图',photo.name]);
          const id=wb.addImage({buffer:photo.bytes,extension:photo.ext});
          sheet.addImage(id,{tl:{col:0,row:2},br:{col:5,row:2+Math.ceil(16*(preview.naturalHeight/preview.naturalWidth||2))}});
        }
        const catalog=wb.addWorksheet('方案事件清单');catalog.addRow(['来源工作表','原行号','业务动作','使用事件','页面或元素标识','结论']);
        let catalogIndex=0;const conclusions=rows();
        function sourceConclusion(index){
          const items=plans.filter(p=>p.source===index).map(p=>conclusions[p.id][5]);
          return items.includes('失败')?'失败':items.length && items.every(v=>v==='通过')?'通过':'待实测';
        }
        for(const s of sheets){
          const header=s.rows[0]?.values||[];
          const code=header.indexOf('使用事件'),action=header.indexOf('需要统计的行为');
          const props=header.findIndex(x=>x?.includes('标识'));
          if(code<0)continue;
          for(const row of s.rows.slice(1))if(row.values[code])catalog.addRow([s.name+' / '+s.sheet,row.n,row.values[action],row.values[code],row.values[props],scene.selectedIndex===0?sourceConclusion(catalogIndex++):'待实测']);
        }
        catalog.columns.forEach(c=>c.width=32);
        sheets.forEach((s,i)=>{
          const sheet=wb.addWorksheet('方案表'+(i+1));sheet.addRow([s.name,s.sheet]);
          s.rows.forEach(r=>sheet.addRow([r.n,...r.values]));sheet.columns.forEach(c=>c.width=28);
        });
        const url=URL.createObjectURL(new Blob([await wb.xlsx.writeBuffer()]));
        const a=document.createElement('a');a.href=url;a.download='手机埋点测试结果.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }catch(e){message.textContent='导出失败：'+e.message;}
    };render();
  }
  return {attach};
})();
