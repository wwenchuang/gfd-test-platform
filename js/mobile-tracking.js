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
    add(panel,'h3','打印取消反馈 · 告诉我们');
    add(panel,'p','用例来源为打印取消原因收集专项需求。请选择指定手机本次操作的埋点请求 HAR，或本次事件 JSON。');
    const phone=add(panel,'a','打开 Sonic 选择指定手机');
    phone.href='http://101.34.197.12:3000/Index/Devices';phone.target='_blank';phone.rel='noopener';
    add(panel,'p','本轮手机 ecbfd645。进入该手机的抓包工具，限定 App 埋点请求，开始记录后再操作；结束后导入本次记录。');
    const capture=fileInput('本次手机抓包','.har,.json');
    const verified=add(panel,'label');
    const covered=add(verified,'input');covered.type='checkbox';covered.disabled=true;
    verified.append('已核对抓包仅含本轮埋点请求，已完成全部操作并覆盖上报等待时间');
    add(panel,'br');
    const evidence=fileInput('操作或前置状态截图','image/png,image/jpeg');
    const preview=add(panel,'img');preview.hidden=true;preview.style.maxWidth='min(100%, 380px)';
    const output=add(panel,'pre'),save=add(panel,'button','导出 Excel 测试表');save.type='button';
    let sheets=[],events=[],complete=false,photo=null,photoUrl='',planRev=0,captureRev=0,photoRev=0;
    function rows(){
      return steps.map(([step,event,wanted])=>{
        const matches=events.filter(x=>x.event===event);
        return [step,event,wanted?'出现':'不出现',events.length||complete?matches.length:null,
          matches.map(x=>x.time).filter(Boolean).join('\n'),
          !(complete||covered.checked)?'待实测':Boolean(matches.length)===wanted?'通过':'失败'];
      });
    }
    function render(){output.textContent=rows().map(r=>r[0]+'　'+r[5]).join('\n');save.disabled=!sheets.length;}
    plan.onchange=async()=>{
      const rev=++planRev;sheets=[];contents.replaceChildren();render();
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
    const message=add(panel,'p');
    capture.onchange=async()=>{
      const rev=++captureRev;events=[];complete=false;covered.checked=false;covered.disabled=true;message.textContent='';render();
      const file=capture.files[0];if(!file)return;
      try{
        const data=JSON.parse(await file.text());if(rev!==captureRev)return;
        if(Array.isArray(data.log?.entries)){
          for(const entry of data.log.entries){
            let body;try{body=JSON.parse(entry.request?.postData?.text);}catch{continue;}
            for(const item of Array.isArray(body)?body:(Array.isArray(body?.events)?body.events:[body])){
              if(typeof item?.event==='string')events.push({event:item.event,time:entry.startedDateTime});
            }
          }
          covered.disabled=!events.length;message.textContent='读取到 '+events.length+' 条可解析事件。';
        }else{
          if(!Array.isArray(data.events)||!data.events.every(x=>typeof x?.event==='string'))throw Error();
          events=data.events;complete=data.complete===true;
        }
      }catch{events=[];complete=false;message.textContent='未能读取事件，请检查抓包文件。';}render();
    };
    covered.onchange=render;
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
        result.addRow(['场景','打印取消反馈 · 告诉我们']);result.addRow(['规则来源','打印取消原因收集专项需求']);
        result.addRow(['手机操作','事件','预期','观测条数','请求时间','结论']);rows().forEach(row=>result.addRow(row));
        result.columns.forEach((c,i)=>c.width=[28,34,12,14,32,16][i]);
        if(photo){
          const sheet=wb.addWorksheet('截图');sheet.addRow(['操作或前置状态截图',photo.name]);
          const id=wb.addImage({buffer:photo.bytes,extension:photo.ext});
          sheet.addImage(id,{tl:{col:0,row:2},br:{col:5,row:2+Math.ceil(16*(preview.naturalHeight/preview.naturalWidth||2))}});
        }
        const catalog=wb.addWorksheet('方案事件清单');catalog.addRow(['来源工作表','原行号','业务动作','使用事件','页面或元素标识','结论']);
        for(const s of sheets){
          const header=s.rows[0]?.values||[];
          const code=header.indexOf('使用事件'),action=header.indexOf('需要统计的行为');
          const props=header.findIndex(x=>x?.includes('标识'));
          if(code<0)continue;
          for(const row of s.rows.slice(1))if(row.values[code])catalog.addRow([s.name+' / '+s.sheet,row.n,row.values[action],row.values[code],row.values[props],'待实测']);
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
