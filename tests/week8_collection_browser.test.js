// UI contracts only. Python collection tests exercise real SQLite, XLSX parsing and concurrency.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require('playwright');

test('import and download a private standard collection, surface conflict, clear stale content',async t=>{
  const browser=await chromium.launch({headless:true});t.after(()=>browser.close());
  const page=await browser.newPage({acceptDownloads:true});
  const doc={schema_version:1,cases:[{id:'caseA',version:1}]};
  const html=`<meta charset="utf-8"><main id="app"><div id="editor-area"></div></main><script>
    window.currentAccessProfile={user_id:'u',scope:{ui_apps:'*'}};window.activeWorkflow='ai_practice';
    window.sessionToken=()=>'fixture';window.authHeaders=(extra={})=>({...extra,Authorization:'Bearer fixture'});
    window.hasPermission=p=>['ui.view','platform.configure'].includes(p);window.canAccessWorkflow=()=>true;
    window.toggleLibrary=()=>{};window.nativeFetch=window.fetch.bind(window);
    </script><script src="/js/ui-simple-entry.js"></script><script>showAiPractice()</script>`;
  let imports=0,permission=true;
  await page.route('http://127.0.0.1/**',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
    if(url.pathname==='/js/ui-simple-entry.js')return route.fulfill({path:path.join(__dirname,'..',url.pathname)});
    assert.equal(req.headers().authorization,'Bearer fixture');
    if(url.pathname==='/api/platform/requirement-design')return route.fulfill({json:{ok:true,source:null,sources:[],runs:[]}});
    assert.equal(url.pathname,'/api/platform/requirement-design/collection');
    if(!permission)return route.fulfill({status:403,json:{ok:false}});
    if(req.method()==='POST'){
      const body=req.postDataJSON();assert.deepEqual(Object.keys(body).sort(),['file_base64','format']);
      assert.equal(body.format,'json');assert.deepEqual(JSON.parse(Buffer.from(body.file_base64,'base64')),doc);
      if(++imports>1)return route.fulfill({status:409,json:{ok:false,error:'用例版本冲突：caseA'}});
      return route.fulfill({json:{ok:true,total:1,added:1,updated:0,unchanged:0}});
    }
    if(url.searchParams.get('format')==='json')return route.fulfill({contentType:'application/json',body:JSON.stringify(doc)});
    return route.fulfill({json:{ok:true,total:imports?1:0,acceptance:{real_case_count:40,stress_row_count:100,offline_controls:10,eligible_count:0,generated:0}}});
  });
  await page.goto('http://127.0.0.1/');
  await page.getByText('需求生成测试设计',{exact:true}).click();
  await page.getByText('我的标准用例工作集',{exact:true}).click();
  await page.getByText('当前工作集共 0 条用例。',{exact:true}).waitFor();
  await page.getByText(/40 条历史用例完成 Excel 往返，100 行边界数据通过/).waitFor();
  const upload=page.getByLabel('标准用例文件'), submit=page.getByRole('button',{name:'导入到我的工作集'});
  const sample={name:'cases.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(doc))};
  await upload.setInputFiles(sample);await submit.click();
  await page.getByText('已导入。新增 1 条、更新 0 条、未变动 0 条。',{exact:true}).waitFor();
  assert.equal(await upload.inputValue(),'');
  await page.getByLabel('工作集格式').selectOption('json');
  const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'下载我的工作集'}).click()]);
  assert.equal(download.suggestedFilename(),'我的标准用例工作集.json');
  assert.deepEqual(JSON.parse(await fs.readFile(await download.path(),'utf8')),doc);
  await upload.setInputFiles(sample);await submit.click();
  await page.getByText(/用例版本冲突：caseA。整批未写入/).waitFor();
  permission=false;await page.getByRole('button',{name:'刷新工作集'}).click();
  await page.getByText('登录状态或权限已变化，请刷新后查看。',{exact:true}).waitFor();
  assert.equal(await page.getByText('当前工作集共 1 条用例。',{exact:true}).count(),0);
  assert.equal(await upload.inputValue(),'');
  assert.equal(await page.getByText(/40 条历史用例完成 Excel 往返/).count(),0);
});
