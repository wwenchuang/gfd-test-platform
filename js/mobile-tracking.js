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
    add(device,'option','OPPO Reno9 · ecbfd645').value='ecbfd645';
    const start=add(panel,'button','开始测试');start.type='button';
    const message=add(panel,'p');message.setAttribute('role','status');
    const evidence=fileInput('操作或前置状态截图','image/png,image/jpeg');
    const preview=add(panel,'img');preview.hidden=true;preview.style.maxWidth='min(100%, 380px)';
    const output=add(panel,'pre'),save=add(panel,'button','导出 Excel 测试表');save.type='button';
    let sheets=[],events=[],photo=null,photoUrl='',planRev=0,photoRev=0;
    let frames=[],busy=false;
    function checks(){
      if(scene.selectedIndex===1)return steps;
      const planned=[];
      for(const sheet of sheets){
        const header=sheet.rows[0]?.values||[];
        const code=header.indexOf('使用事件'),action=header.indexOf('需要统计的行为'),props=header.findIndex(x=>x?.includes('标识'));
        for(const row of sheet.rows.slice(1))if(code>=0&&row.values[code])planned.push([row.values[action]||sheet.sheet,row.values[code],true,row.values[props]||'']);
      }
      return planned;
    }
    function rows(){
      const rules=checks();
      return rules.map(([step,event,wanted,props])=>{
        const conditions=(props||'').trim().split(/\n/).filter(Boolean).map(v=>v.trim().match(/^([a-zA-Z]+)=([^=]+)$/));
        const unique=rules.filter(rule=>rule[1]===event && rule[3]===props).length===1;
        const defined=unique && (scene.selectedIndex===1 || (conditions.length>0 && conditions.every(Boolean)));
        const matches=events.filter(x=>x.event===event && conditions.every(c=>c && x.fields?.[c[1]]===c[2]));
        const aligned=matches.filter(x=>x.frame!=null);
        return [step,event,wanted?'出现':'不出现',events.length?matches.length:null,
          aligned.map(x=>new Date(x.time*1000).toISOString()+' / 截图 '+(x.frame+1)).join('\n'),
          !defined||!aligned.length?'待实测':wanted?'通过':'失败'];

      });
    }
    function render(){output.textContent=rows().map(r=>r[0]+'　'+r[5]).join('\n');save.disabled=!sheets.length||busy;start.disabled=!sheets.length||busy;scene.disabled=busy;plan.disabled=busy;}
    plan.onchange=async()=>{
      events=[];frames=[];photo=null;preview.hidden=true;message.textContent='';const rev=++planRev;sheets=[];contents.replaceChildren();render();
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
    scene.onchange=()=>{events=[];frames=[];photo=null;preview.hidden=true;message.textContent='';render();};
    async function call(body){
      const reply=await nativeFetch('/api/mobile-tracking',{method:'POST',headers:authHeaders({'Content-Type':'application/json'}),body:JSON.stringify(body)});
      const data=await reply.json();if(!reply.ok||!data.ok)throw Error(data.error||'采集服务不可用');return data;
    }
    start.onclick=async()=>{
      busy=true;events=[];frames=[];photo=null;preview.hidden=true;render();message.textContent='正在连接指定手机…';
      try{
        const run=await call({device:device.value});const deadline=Date.now()+160000;
        while(Date.now()<deadline&&panel.isConnected){
          const data=await call({id:run.id});
          if(data.state==='done'){
            frames=data.frames||[];
            events=(data.events||[]).map(e=>{
              let frame=null,distance=3;
              frames.forEach((f,i)=>{const d=Math.abs(f.time-e.time);if(d<distance){frame=i;distance=d;}});
              return {...e,frame};
            });
            if(frames.length){preview.src='data:image/png;base64,'+frames[0].png;preview.hidden=false;}
            message.textContent=(data.note||'采集结束')+' · '+events.length+' 条事件，'+frames.length+' 张截图';break;
          }
          message.textContent=data.state==='ready'?'已开始 30 秒抓包，请在手机上操作 App。':'正在准备手机采集…';
          await new Promise(resolve=>setTimeout(resolve,1500));
        }
        if(Date.now()>=deadline)message.textContent='采集超时，待实测';
      }catch(e){message.textContent=e.message+' · 待实测';}finally{busy=false;render();}
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
        result.addRow(['场景',scene.value]);result.addRow(['规则来源',scene.selectedIndex===1?'打印取消原因收集专项需求':plan.files[0]?.name]);
        result.addRow(['手机操作','事件','预期','观测条数','请求时间','结论']);rows().forEach(row=>result.addRow(row));
        result.columns.forEach((c,i)=>c.width=[28,34,12,14,32,16][i]);
        if(frames.length){
          const pictures=wb.addWorksheet('手机现场截图');
          frames.forEach((frame,i)=>{
            const row=34*i;pictures.getCell(row+1,1).value='截图 '+(i+1)+' · '+new Date(frame.time*1000).toISOString();
            const id=wb.addImage({base64:'data:image/png;base64,'+frame.png,extension:'png'});
            pictures.addImage(id,{tl:{col:0,row:row+1},br:{col:5,row:row+33}});
          });
        }
        const observations=wb.addWorksheet('抓包事件');observations.addRow(['事件','请求时间','最近现场截图','页面或元素']);
        events.forEach(e=>observations.addRow([e.event,new Date(e.time*1000).toISOString(),e.frame==null?'无相邻截图':e.frame+1,JSON.stringify(e.fields)]));
        if(photo){
          const sheet=wb.addWorksheet('截图');sheet.addRow(['操作或前置状态截图',photo.name]);
          const id=wb.addImage({buffer:photo.bytes,extension:photo.ext});
          sheet.addImage(id,{tl:{col:0,row:2},br:{col:5,row:2+Math.ceil(16*(preview.naturalHeight/preview.naturalWidth||2))}});
        }
        const catalog=wb.addWorksheet('方案事件清单');catalog.addRow(['来源工作表','原行号','业务动作','使用事件','页面或元素标识','结论']);
        let catalogIndex=0;const conclusions=rows();
        for(const s of sheets){
          const header=s.rows[0]?.values||[];
          const code=header.indexOf('使用事件'),action=header.indexOf('需要统计的行为');
          const props=header.findIndex(x=>x?.includes('标识'));
          if(code<0)continue;
          for(const row of s.rows.slice(1))if(row.values[code])catalog.addRow([s.name+' / '+s.sheet,row.n,row.values[action],row.values[code],row.values[props],scene.selectedIndex===0?conclusions[catalogIndex++][5]:'待实测']);
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
