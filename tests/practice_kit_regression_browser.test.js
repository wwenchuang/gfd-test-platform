const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

test('suite regression shows source, linked checks, outstanding items and verified download', async t => {
  const browser = await chromium.launch();
  t.after(() => browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(3500);
  const html = `<main id="app"><div id="editor-area"></div></main><script>
    window.currentAccessProfile={user_id:'viewer',scope:{ui_apps:['com.kfb.model']}};
    window.activeWorkflow='ai_practice';window.sessionToken=()=>'fixture';
    window.authHeaders=()=>({Authorization:'Bearer fixture'});window.hasPermission=p=>p==='ui.view';
    window.canAccessWorkflow=()=>true;window.toggleLibrary=()=>{};window.nativeFetch=fetch.bind(window);
    </script><script src="/js/ui-practice-results.js"></script><script src="/js/ui-simple-entry.js"></script>
    <script>showAiPractice()</script>`;
  const digest = 'a'.repeat(64);
  const report = {run_id:'kit-e2e-20261111-r1', created_at:'2026-11-11T09:00:00+08:00',
    requirement:'原方案 R1', summary:'仅限本轮', generation:{status:'已生成',observed:'一条草稿',evidence:'生成回执'},
    cases:[{id:'C1',title:'关联入口',expected:'出现关联模型',source:'R1'}],
    checks:[{id:'K1',case_id:'C1',title:'关联详情',channel:'ui',status:'blocked',observed:'无可用样本',evidence:'现场记录'}],
    defects:[], knowledge:[{id:'N1',title:'入口规则',status:'待回读',observed:'记录规则',evidence:'文档段落'}],
    limitations:['人工审核待办'], evidence:{sha256:digest,bytes:4}};
  const ticket={id:'KIT-'+'c'.repeat(24),title:'构建差异',type:'构建或配置待查',status:'已登记 · 待核对',
    run_id:report.run_id,observation_id:'field-01',created_at:report.created_at,observed:'独立原件中的观测',evidence:'原件证据'};
  report.defects=[{...ticket,observed:'报告回读摘要'}];
  let status=200, ticketReads=0;
  await page.route('http://127.0.0.1/**', async route => {
    const req=route.request(), url=new URL(req.url());
    if(url.pathname==='/') return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
    if(url.pathname.startsWith('/js/')) return route.fulfill({path:path.join(__dirname,'..',url.pathname)});
    assert.equal(req.method(),'GET');
    assert.equal(req.headers().authorization,'Bearer fixture');
    if(url.pathname.endsWith('/kit-regression-evidence')) {
      assert.equal(url.searchParams.get('id'),digest);
      return route.fulfill({contentType:'application/zip',body:Buffer.from('test')});
    }
    if(url.pathname.endsWith('/kit-regression-ticket')) {
      assert.equal(url.searchParams.get('id'),ticket.id);
      ticketReads++;
      return route.fulfill({json:{ok:true,ticket}});
    }
    assert.equal(url.pathname,'/api/platform/requirement-design/practice-results/kit-regression');
    return route.fulfill({status,json:status===200?{ok:true,report}:{ok:false}});
  });
  await page.goto('http://127.0.0.1/');
  const title=page.getByText('套件专区需求回归',{exact:true});
  await title.click();
  const panel=page.locator('details').filter({has:title}).first();
  await panel.getByText('暂不能确认可以发版',{exact:true}).waitFor();
  assert.equal(await panel.getByText(report.requirement,{exact:true}).isVisible(),false);
  await panel.getByText('查看全部 1 项检查',{exact:true}).click();
  await panel.getByText('关联详情 · 受阻',{exact:true}).click();
  await panel.getByText('无可用样本',{exact:false}).waitFor();
  await panel.getByText('关联用例 · C1',{exact:true}).waitFor();
  await panel.getByText('查看待排查问题（1）',{exact:true}).click();
  await panel.getByRole('button',{name:'查看排查单 · 构建差异'}).click();
  await panel.getByText('独立原件中的观测',{exact:false}).waitFor();
  assert.equal(ticketReads,1);
  await panel.getByText('技术详情与证据',{exact:true}).click();
  await panel.getByText('人工审核待办',{exact:true}).waitFor();
  const download=page.waitForEvent('download');
  await panel.getByRole('button',{name:'下载回归证据 ZIP'}).click();
  assert.equal((await download).suggestedFilename(),'kit-regression-evidence-'+digest.slice(0,12)+'.zip');
  status=403;
  await panel.getByRole('button',{name:'刷新结果'}).click();
  await panel.getByText('登录状态或权限已变化',{exact:false}).waitFor();
  assert.equal(await panel.getByText('人工审核待办',{exact:true}).count(),0);
});
