const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const {Workbook}=require(path.join(root,'js/vendor/tracking/exceljs.min.js'));

test('a refreshed personal history exports only owned run evidence with exact row references; retry keeps the report',async()=>{
  const browser=await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
  try{
    const page=await browser.newPage();
    page.setDefaultTimeout(9000);
    const title='埋点测试.xlsx',device='test-phone',created=1794561194;
    const rows=[
      {action:'广场',origin:'表2 第2行',status:'通过',reason:'已核对',event:'AEC',receiptIds:['request-a','request-a']},
      {action:'分享',origin:'表2 第3行',status:'待实测',reason:'事件未回读',event:'AEC',receiptIds:['missing']},
      {action:'旧项',origin:'表2 第4行',status:'通过',reason:'旧结论'}
    ];
    const raw={reqId:'request-a',index:0,event:'AEC',name:'元素点击',method:'POST',sent:42,time:41,httpStatus:200,businessCode:0,ack:'已接收',
      contents:[{page:'home',ele:'square_tab',tel:'private-phone',keyword:'private-word'}]};
    const reports=[{id:'report',title,device,deviceName:'荣耀',created,runIds:['run1','other-device','unavailable'],rows},
      {id:'old',title:'旧版.xlsx',device,created,runIds:['run1'],rows:[rows[2]]}];
    const published=JSON.parse(await fs.readFile(path.join(root,'task_server/assets/tracking-acceptance.json'),'utf8'));
    const queried=[];let partial=true,historyCalls=0,failDownload=true,failRefresh=false,failReceipt=true;
    const receiptMap=JSON.parse(await fs.readFile(path.join(root,'task_server/assets/tracking/honor-receipts.json'),'utf8'));
    const {execFileSync}=require('node:child_process');
    await page.route('http://localhost:23457/**',async route=>{
      const url=new URL(route.request().url());
      if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset=utf-8><main></main>'});
      if(url.pathname.startsWith('/js/'))return route.fulfill({path:path.join(root,url.pathname),contentType:'application/javascript; charset=UTF-8'});
      const body=route.request().postDataJSON();
      if(body.history){historyCalls++;return failRefresh?route.fulfill({status:503,json:{ok:false}}):route.fulfill({json:{ok:true,reports,published}});}
      if(body.publishedFile){
        if(failDownload){failDownload=false;return route.fulfill({status:503,json:{ok:false}});}
        assert.equal(body.publishedFile,'iphone');
        return route.fulfill({path:path.join(root,'task_server/assets/tracking/iphone-tracking.xlsx')});
      }
      if(body.publishedReceipt){
        assert.equal(body.publishedReceipt.report,'honor');
        if(failReceipt){failReceipt=false;return route.fulfill({status:503,json:{ok:false}});}
        const member=receiptMap[body.publishedReceipt.id].member;
        return route.fulfill({contentType:'image/png',body:execFileSync('unzip',['-p',path.join(root,'task_server/assets/tracking/honor-tracking.xlsx'),member])});
      }
      assert.deepEqual(Object.keys(body),['id']);queried.push(body.id);
      if(body.id==='unavailable'&&partial)return route.fulfill({status:404,json:{ok:false}});
      return route.fulfill({json:{ok:true,state:'done',device:body.id==='other-device'?'foreign-phone':device,
        endpoint:'https://event.example.test/upload?token=private-token',performed:[{label:'广场'}],
        events:body.id==='run1'?[raw,{...raw,reqId:'generic',event:'AOP'}]:[{...raw,reqId:'missing'}]}});
    });
    async function openPanel(){
      await page.goto('http://localhost:23457/');
      for(const name of ['vendor/tracking/exceljs.min.js','mobile-tracking-report.js','mobile-tracking-history.js'])await page.addScriptTag({url:'/js/'+name});
      await page.evaluate(()=>{
        window.authHeaders=h=>h;window.nativeFetch=window.fetch.bind(window);window.MobileTracking={libraries:()=>Promise.resolve()};
        window.disposeHistory=TrackingHistory.mount(document.querySelector('main'),()=>true);
      });
      await page.getByRole('heading',{name:'iPhone 13 · 2026-11-13 设备核验'}).waitFor();
    }
    await openPanel();await openPanel();
    assert.equal(historyCalls,2);
    const article=name=>page.getByRole('article').filter({has:page.getByRole('heading',{name,exact:true})});
    const block=article(title),old=article('旧版.xlsx'),iphone=article(published.reports[1].title),honor=article(published.reports[0].title);
    assert.equal(await page.getByRole('article').count(),4);
    assert.match(await honor.locator('.tracking-report-counts').innerText(),/通过 27/);
    assert.match(await honor.locator('.tracking-report-counts').innerText(),/差异 30/);
    assert.match(await honor.locator('.tracking-report-meta').innerText(),/2026-11-13/);
    assert.match(await iphone.locator('.tracking-report-counts').innerText(),/待实测 94/);
    await honor.getByRole('button',{name:'预览逐项结论'}).click();
    assert.equal(await honor.getByRole('button',{name:'查看接口证据'}).count(),57);
    const firstRow=honor.locator('tbody tr').first(),receipt=firstRow.getByRole('button',{name:'查看接口证据'});
    await receipt.click();
    const dialog=page.getByRole('dialog',{name:'查看接口证据'});
    await dialog.getByRole('button',{name:'重试读取截图'}).click();
    await dialog.getByRole('img').waitFor();
    const checksum=await dialog.getByRole('img').evaluate(async img=>{
      const buffer=await (await fetch(img.src)).arrayBuffer();
      return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)),b=>b.toString(16).padStart(2,'0')).join('');
    });
    assert.equal(checksum,receiptMap[published.reports[0].rows[0].receiptIds[0]].sha256);
    await dialog.getByRole('button',{name:'返回逐项结论'}).click();
    assert.ok(await receipt.evaluate(el=>el===document.activeElement));
    await page.getByRole('searchbox').fill('设备核验');
    assert.equal(await page.getByRole('article').count(),1);
    await page.getByRole('searchbox').fill('');
    await page.getByRole('combobox',{name:'筛选报告设备'}).selectOption('iPhone 13');
    assert.equal(await page.getByRole('article').count(),1);
    await iphone.getByRole('button',{name:'预览逐项结论'}).click();
    assert.equal(await iphone.locator('tbody tr').count(),94);
    assert.equal(await iphone.getByRole('button',{name:'查看接口证据'}).count(),0);
    await iphone.getByRole('combobox',{name:'筛选测试结论'}).selectOption('通过');
    await iphone.getByText('没有符合此结论的需求行。').waitFor();
    const publishedButton=iphone.getByRole('button',{name:'下载 iPhone 对照表'});
    await publishedButton.click();await iphone.getByText('下载失败，点击按钮可重试。').waitFor();
    const publishedDownload=page.waitForEvent('download');await publishedButton.click();
    assert.deepEqual(await fs.readFile(await (await publishedDownload).path()),await fs.readFile(path.join(root,'task_server/assets/tracking/iphone-tracking.xlsx')));
    await page.getByRole('combobox',{name:'筛选报告设备'}).selectOption('');
    assert.equal(historyCalls,2);
    failRefresh=true;await page.getByRole('button',{name:'刷新报告'}).click();await page.getByRole('button',{name:'重试读取报告'}).waitFor();
    assert.equal(await page.getByRole('article').count(),4);
    failRefresh=false;await page.getByRole('button',{name:'重试读取报告'}).click();await page.getByRole('button',{name:'刷新报告'}).waitFor();
    async function download(block){
      const capture=page.waitForEvent('download');
      await block.getByRole('button',{name:'导出本次实测 Excel'}).click();
      const file=await capture,w=new Workbook();
      await w.xlsx.load(await fs.readFile(await file.path()));
      return w;
    }
    let w=await download(block);
    assert.deepEqual(queried,['run1','other-device','unavailable']);
    assert.equal(w.getWorksheet('逐项结论').getCell('F2').value,'request-a');
    assert.match(w.getWorksheet('逐项结论').getCell('G3').text,/missing/);
    assert.equal(w.getWorksheet('本轮范围').getCell('F2').value,1);
    assert.equal(w.getWorksheet('接口上报截图').getImages().length,1);
    assert.equal(w.getWorksheet('抓包事件').getCell('C3').value,'仅归属整次运行');
    let all=w.worksheets.map(s=>JSON.stringify(s.getSheetValues())).join('\n');
    assert.doesNotMatch(all,/private-phone|private-word|private-token/);
    assert.match(all,/已脱敏/);
    assert.match(await block.getByRole('status').innerText(),/2 条采集记录未能回读/);
    partial=false;
    w=await download(block);assert.equal(w.getWorksheet('接口上报截图').getImages().length,2);
    w=await download(old);
    assert.equal(w.getWorksheet('接口上报截图'),undefined);
    assert.equal(w.getWorksheet('抓包事件').getCell('C2').value,'仅归属整次运行');
    assert.equal(w.getWorksheet('逐项结论').getCell('D2').value,'通过');
    assert.match(w.getWorksheet('逐项结论').getCell('G2').text,/旧报告未保存/);
    await page.evaluate(()=>window.disposeHistory());
    assert.equal(await page.getByRole('region',{name:'实测报告'}).count(),0);
  }finally{await browser.close();}
});
