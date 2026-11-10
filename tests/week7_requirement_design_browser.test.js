// Isolated browser fixture for the practice UI. All API and model results below are test data.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright');

test('workbench previews authorized PNGs, polls to completion and downloads matching JSON', {timeout: 30000}, async t => {
  const browser = await chromium.launch({headless: true});
  t.after(() => browser.close());
  const page = await browser.newPage({acceptDownloads: true});
  const id = 'a'.repeat(64);
  const material = {id, title:'搜索需求', app:'应用', version:'v2', prd_file_sha256:id,
    clauses:[{id:'P1',text:'搜索以关键词筛选'}], prototypes:[{id:'V1',caption:'搜索原型',sha256:id}]};
  const run = {id:'run-fixture',created:1730000000,status:'running'};
  const result = {points:[{id:'T1',title:'搜索结果',expected:'结果匹配',refs:['P1','V1']}],
    cases:[{id:'C1',title:'关键词筛选',precondition:'已登录',steps:['输入关键词'],expected:'命中结果',point_ids:['T1']}],
    questions:[],checks:{partial:false,review_required:true},evidence:{model:'fixture-model',request_id:'fixture-request'}};
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/nS8AAAAASUVORK5CYII=', 'base64');
  const candidate={id:'g1-C1',group:'g1',title:'测试点关联检查',precondition:'已有材料',steps:['查看列表'],expected:'显示模型',refs:['P1']};
  const group={id:'g1',title:'关联模型',sample_count:1,prototype_count:1,
    variants:[{variant:'zero',status:'completed',model:'fixture',request_id:'request-1',point_count:1,case_count:1,question_count:0,
      elapsed_ms:23,usage:{total_tokens:66},unreferenced_source_ids:[]},{variant:'few',status:'failed'}],
    observations:[{kind:'visual',text:'原型中显示列表',refs:['V1']}],questions:['何时同步？']};
  const assessment={version:1,status:'partial',candidate_count:1,candidate_target:20,sample_count:1,prototype_count:1,groups:[group],candidates:[candidate]};
  let evaluationAllowed=true, badStandardMime=true;
  let started = false, runningReads = 0;
  const html = `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/css/ui-workbench.css"><link rel="stylesheet" href="/css/ui-case-draft.css">
    <div id="app"><main id="editor-area"></main></div><script>
      window.currentAccessProfile={user_id:'fixture',scope:{ui_apps:'*'}};
      window.activeWorkflow='ai_practice';window.sessionToken=()=>'fixture';
      window.authHeaders=(extra={})=>({...extra,Authorization:'Bearer fixture'});
      window.hasPermission=p=>['ui.view','platform.configure'].includes(p);
      window.canAccessWorkflow=()=>true;window.toggleLibrary=()=>{};window.nativeFetch=window.fetch.bind(window);
    </script><script src="/js/ui-simple-entry.js"></script><script>showAiPractice()</script>`;
  await page.route('http://127.0.0.1/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname === '/') return route.fulfill({contentType:'text/html', body:html});
    if (['/js/ui-simple-entry.js','/css/ui-workbench.css','/css/ui-case-draft.css'].includes(url.pathname)) {
      return route.fulfill({path:path.join(__dirname,'..',url.pathname)});
    }
    assert.equal(request.headers().authorization, 'Bearer fixture');
    if(url.pathname.endsWith('/standard-cases')){
      assert.equal(request.method(),'GET');
      const format=url.searchParams.get('format');
      assert.equal(url.searchParams.get('source_id'),id);assert.equal(url.searchParams.get('run_id'),run.id);
      if(badStandardMime)return route.fulfill({contentType:'text/html',body:'login'});
      if(format==='json')return route.fulfill({contentType:'application/json',body:JSON.stringify({cases:[candidate]})});
      assert.equal(format,'xlsx');
      return route.fulfill({contentType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',body:Buffer.from('fixture-excel-binary')});
    }
    if(url.pathname.includes('/evaluation')){
      if(!evaluationAllowed)return route.fulfill({status:403,json:{ok:false}});
      if(url.pathname.endsWith('/blind'))return route.fulfill({json:{ok:true,blind:{status:'pending',items:[{label:'A1',title:'匿名用例',review:null}]}}});
      return route.fulfill({json:{ok:true,report:assessment}});
    }
    if (url.pathname.endsWith('/prototype')) {
      assert.equal(url.searchParams.get('source_id'), id); assert.equal(url.searchParams.get('id'), 'V1');
      return route.fulfill({contentType:'image/png',body:png});
    }
    assert.equal(url.pathname,'/api/platform/requirement-design');
    if (request.method() === 'POST') {
      assert.equal(request.postDataJSON().source_id,id);assert.match(request.postDataJSON().request_id,/^[a-f0-9]{32}$/);
      started=true;return route.fulfill({status:202,json:{ok:true,run}});
    }
    const runs = !started ? [] : ++runningReads === 1 ? [run] : [{...run,status:'completed',finished:1730000002,result}];
    return route.fulfill({json:{ok:true,source:material,sources:[material],runs}});
  });
  await page.goto('http://127.0.0.1/');
  await page.getByText('需求生成测试设计',{exact:true}).click();
  await page.getByText('查看 PRD 条款和原型',{exact:true}).click();
  await page.getByRole('button',{name:'查看原型'}).click();
  const img = page.getByAltText('搜索原型');await img.waitFor();
  await img.evaluate(el=>el.decode());assert.equal(await img.evaluate(el=>el.naturalWidth),1);
  const generate = page.getByRole('button',{name:'生成测试设计'});
  assert.equal(await generate.evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(131, 235, 203)');
  await generate.click();await page.getByText('正在生成，页面会自动更新。',{exact:true}).waitFor();
  assert.equal(await generate.isDisabled(),true);
  await page.getByText('已生成 · 待人工复核',{exact:false}).first().waitFor({timeout:8000});
  await page.getByText('测试用例（1）',{exact:true}).click();await page.getByText('关联测试点　T1',{exact:true}).waitFor();
  const [download] = await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'下载证据 JSON'}).click()]);
  const evidence = JSON.parse(await fs.readFile(await download.path(),'utf8'));
  assert.equal(evidence.source.id,id);assert.equal(evidence.run.id,run.id);assert.equal(evidence.run.result.evidence.request_id,'fixture-request');
  assert.equal(await generate.isDisabled(),false);
  await page.getByText('导出标准用例',{exact:true}).click();
  const exportButton=page.getByRole('button',{name:'下载标准用例'});
  await exportButton.click();
  await page.getByText('导出失败，请刷新记录后重试。').waitFor();
  badStandardMime=false;
  for(const format of ['json','xlsx']){
    await page.getByLabel('文件格式').selectOption(format);
    const [file]=await Promise.all([page.waitForEvent('download'),exportButton.click()]);
    assert.equal(file.suggestedFilename(),'标准用例-'+run.id+'.'+format);
    const contents=await fs.readFile(await file.path(),'utf8');
    if(format==='json')assert.deepEqual(JSON.parse(contents),{cases:[candidate]});
    else assert.equal(contents,'fixture-excel-binary');
  }
  await page.getByText('示范需求的提示词对照评估',{exact:true}).click();
  await page.getByText('1 个需求片段 · 1 张原型 · 候选 1 / 20 条 · 待人工盲评',{exact:true}).waitFor();
  await page.getByText('关联模型',{exact:true}).click();
  await page.getByText('无例子 · 已生成',{exact:true}).click();
  await page.getByText(/request-1/).waitFor();
  await page.getByText('带例子 · 生成失败',{exact:true}).waitFor();
  await page.getByText('原型观察 · 原型中显示列表　V1',{exact:true}).waitFor();
  await page.getByText('查看候选用例（1）',{exact:true}).click();
  await page.getByText('g1-C1 · 测试点关联检查',{exact:true}).click();
  await page.getByText('预期　显示模型',{exact:true}).waitFor();
  const [handout]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'下载匿名盲评表'}).click()]);
  const sheet=JSON.parse(await fs.readFile(await handout.path(),'utf8'));
  assert.equal(sheet.items[0].label,'A1');assert.equal(sheet.items[0].variant,undefined);
  evaluationAllowed=false;
  await page.getByRole('button',{name:'刷新评估'}).click();
  await page.getByText('登录状态或权限已变化，请刷新后查看。',{exact:true}).waitFor();
  assert.equal(await page.getByText('g1-C1 · 测试点关联检查',{exact:true}).count(),0);
  assert.equal(await page.getByText('下载匿名盲评表',{exact:true}).count(),0);
  assert.equal(await page.getByText('生成编号 run-fixture',{exact:true}).count(),0);
});
