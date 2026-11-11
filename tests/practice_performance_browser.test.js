const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

test('a scoped viewer opens real report sections, downloads the approved evidence and clears revoked results', async t => {
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
  const report = {
    environment:'isolated', created_at:'2026-11-11T09:00:00+08:00', scenario:'只读目录', conditions:'固定间隔',
    thresholds:{error_pct:1,p95_ms:20}, limitations:['仅限单机'], retest:'未复测', stop_reason:'完成计划',
    resources:{generator:'监控已记录',server:'未采集',database:'未采集'}, evidence:{sha256:digest,bytes:4},
    stages:[{phase:'baseline', threads:5, ramp_s:0, warmup_s:3, steady_s:10, elapsed_s:13, samples:100, errors:3,
      http_successes:99,business_successes:97,tps:10,p50_ms:10,p95_ms:25,p99_ms:75,jtl_sha256:digest,
      monitor:[{source:'generator',metric:'CPU',unit:'%',samples:5,min:2,mean:4,max:6}]}]
  };
  let status = 200, calls = 0;
  await page.route('http://127.0.0.1/**', async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.pathname === '/') return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
    if (url.pathname.startsWith('/js/')) return route.fulfill({path:path.join(__dirname,'..',url.pathname)});
    if (url.pathname.endsWith('/practice-results/recent')) return route.fulfill({json:{ok:true,reports:[]}});
    assert.equal(req.method(),'GET');
    assert.equal(req.headers().authorization,'Bearer fixture');
    calls++;
    if (url.pathname.endsWith('/performance-evidence')) {
      assert.equal(url.searchParams.get('id'), digest);
      return route.fulfill({contentType:'application/zip',body:Buffer.from('test')});
    }
    assert.equal(url.pathname,'/api/platform/requirement-design/practice-results/performance');
    return route.fulfill({status,json:status === 200 ? {ok:true,report} : {ok:false}});
  });
  await page.goto('http://127.0.0.1/');
  assert.equal(calls, 0);
  const title = page.getByText('接口性能实测',{exact:true});
  await title.click();
  const panel = page.locator('details').filter({has:title}).first();
  await panel.getByText('只读目录',{exact:true}).waitFor();
  await panel.getByText('预热 3 秒',{exact:false}).waitFor();
  await panel.getByText('99%',{exact:false}).first().waitFor();
  await panel.getByText('97%',{exact:false}).first().waitFor();
  await panel.getByText('资源采样',{exact:false}).first().click();
  await panel.getByText('CPU',{exact:false}).waitFor();
  await panel.getByText('仅限单机',{exact:true}).waitFor();
  const download = page.waitForEvent('download');
  await panel.getByRole('button',{name:'下载本次原始证据 ZIP'}).click();
  assert.equal((await download).suggestedFilename(), '接口性能实测-2026-11-11.zip');
  status = 403;
  await panel.getByRole('button',{name:'刷新结果'}).click();
  await panel.getByText('登录状态或权限已变化',{exact:false}).waitFor();
  assert.equal(await panel.getByText('只读目录').count(), 0);
  await page.evaluate(()=>{currentAccessProfile.scope.ui_apps=['other']; showAiPractice();});
  await title.waitFor({state:'detached'});
});
