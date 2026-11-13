const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');

test('browser reports reflect exact action windows, evidence, aliases and source coverage',async()=>{
  const browser=await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
  try{
    const page=await browser.newPage();
    await page.addScriptTag({path:path.join(root,'js/mobile-tracking-report.js')});
    const results=await page.evaluate(()=>{
      function grade(code,wire,props='page=home',actual={page:'home'},want=true){
        const rule=['点击卡片',code,want,props,'模块','表1第2行'];
        const plan={id:0,source:0,path:['卡片'],batch:'r',done:true};
        const input={reqId:'request1',event:wire,method:'POST',contents:[actual],sent:42,time:41,httpStatus:200,businessCode:0,ack:'已接收'};
        return MobileTrackingReport.report([rule],[plan],[MobileTrackingReport.sanitize(input,'r',[0],'https://example.test/a?token=secret')]).sources[0];
      }
      return [grade('CC','CCZ').status,grade('CS','CSZ').status,grade('AEC','AEC','page=home\nele=tab',{page:'elsewhere',ele:'tab'}).status,grade('AEC','AV').status,grade('CC','CCZ','page=home',{page:'home'},false).status];
    });
    assert.deepEqual(results,['通过','差异待确认','差异待确认','待实测','失败']);
  }finally{await browser.close();}
});

test('start, poll, store, inspect and download in the actual panel',async()=>{
  const browser=await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
  const fixture={device:'A9TU024622000978',endpoint:'https://event.gongfudou.com/event/zxbEvent',performed:[{label:'首页',time:30},{label:'广场',time:40,until:50}],events:[{reqId:'example-request',index:0,name:'元素点击',event:'AEC',method:'POST',sent:42,time:41,httpStatus:200,businessCode:0,ack:'已接收',actionIndex:1,contents:[{ele:'square_tab',page:'home'}]}]};
  const run=process.env.TRACKING_REPORT_RUN?JSON.parse(await fs.readFile(process.env.TRACKING_REPORT_RUN,'utf8')):fixture;
  try{
    const page=await browser.newPage();page.setDefaultTimeout(10000);let persisted;
    if(process.env.TRACKING_DEBUG){page.on('console',m=>console.log(m.text()));page.on('pageerror',e=>console.error(e));page.on('response',r=>console.log(r.status(),r.url()));}
    await page.route('http://localhost:23456/**',async route=>{
      const url=new URL(route.request().url());
      if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset=utf-8><main></main>'});
      if(url.pathname.startsWith('/js/'))return route.fulfill({path:path.join(root,url.pathname),contentType:'application/javascript; charset=UTF-8'});
      const body=route.request().postDataJSON();
      if(body.devices)return route.fulfill({json:{ok:true,devices:[{id:fixture.device,name:'荣耀 Magic Vs3',ready:true,sonicStatus:'online',runnerStatus:'online',reason:'可执行本轮采集'}]}});
      if(body.requirements)return route.fulfill({json:{ok:true,items:[{id:0,event:'AEC',action:'点击广场',path:['首页','广场'],steps:['打开首页','点击广场']}]}});
      if(body.saveReport){persisted=body.saveReport;return route.fulfill({json:{ok:true}});}
      if(body.id)return route.fulfill({json:{ok:true,state:'done',...run}});
      assert.deepEqual(body.path,['首页','广场']);return route.fulfill({json:{ok:true,id:'real-flow'}});
    });
    await page.goto('http://localhost:23456/');
    for(const asset of ['vendor/tracking/exceljs.min.js','mobile-tracking-source.js','mobile-tracking-report.js','mobile-tracking.js'])await page.addScriptTag({url:'/js/'+asset});
    const {Workbook}=require(path.join(root,'js/vendor/tracking/exceljs.min.js'));
    await page.evaluate(()=>{
      window.authHeaders=x=>x;window.nativeFetch=window.fetch.bind(window);
      document.querySelector('main').addEventListener('tracking-report-saved',()=>window.receivedReport=true);
      MobileTracking.attach(document.querySelector('main'));
    });
    await page.getByRole('combobox',{name:'测试手机'}).getByRole('option',{name:'荣耀 Magic Vs3'}).waitFor({state:'attached'});
    await page.getByText('执行机 · 在线').waitFor();await page.getByText('Sonic · 在线').waitFor();
    assert.equal(await page.getByRole('button',{name:'开始测试'}).isEnabled(),false);
    assert.equal(await page.getByRole('button',{name:'导出 Excel 测试表'}).isVisible(),false);
    assert.equal(await page.getByText('只测打印取消反馈').count(),0);
    const wb=new Workbook(),s=wb.addWorksheet('首页');
      s.addRow(['图','模块','需要统计的行为','使用事件','元素标识']);s.addRow(['','导航','点击广场','AEC','ele=square_tab\npage=home']);
    const bytes=await wb.xlsx.writeBuffer();
    await page.locator('input[type=file]').first().setInputFiles({name:'广场实测.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from(bytes)});
    const downloaded=page.waitForEvent('download',{timeout:15000});await page.getByText('开始测试',{exact:true}).click();
    const file=await downloaded;await page.getByText('测试结束，已生成本次报告。').waitFor();
    assert.equal(await page.evaluate(()=>window.receivedReport),true);
    assert.equal(persisted.rows[0].status,'通过');assert.deepEqual(persisted.runIds,['real-flow']);
    const extracted=await page.evaluate(async(data)=>{
      const wb=new ExcelJS.Workbook();await wb.xlsx.load(Uint8Array.from(data));return {sheets:wb.worksheets.map(s=>s.name),images:wb.getWorksheet('接口上报截图').getImages().length,rows:wb.getWorksheet('抓包事件').rowCount};
    },[...await fs.readFile(await file.path())]);
    assert.equal(extracted.images,1);assert.equal(extracted.rows,run.events.length+1);assert.ok(!extracted.sheets.includes('手机现场截图'));
    const report=page.getByRole('region',{name:'本次测试报告'});await report.locator('details summary').first().click();await report.getByText(/^查看接口上报/).click();
    await report.getByRole('img').waitFor();assert.ok((await report.getByRole('img').getAttribute('src')).startsWith('data:image/png;base64,'));
  }finally{await browser.close();}
});
