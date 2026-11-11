const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

test('workbench opens per-case evidence, downloads authenticated PNG and discards inaccessible results',async t=>{
  const browser=await chromium.launch();t.after(()=>browser.close());
  const page=await browser.newPage(),digest='a'.repeat(64);
  const html='<main id="app"><div id="editor-area"></div></main><script>'+
    "window.currentAccessProfile={user_id:'u',is_superuser:true};window.activeWorkflow='ai_practice';"+
    "window.sessionToken=()=>'fixture';window.authHeaders=()=>({Authorization:'Bearer fixture'});"+
    "window.hasPermission=()=>true;window.canAccessWorkflow=()=>true;window.toggleLibrary=()=>{};window.nativeFetch=fetch.bind(window);"+
    '</script><script src="/js/ui-practice-results.js"></script><script src="/js/ui-simple-entry.js"></script><script>showAiPractice()</script>';
  let status=200;
  await page.route('http://127.0.0.1/**',async route=>{
    const req=route.request(),u=new URL(req.url());
    if(u.pathname==='/') return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
    if(u.pathname.startsWith('/js/')) return route.fulfill({path:path.join(__dirname,'..',u.pathname)});
    if(!u.pathname.includes('/practice-results/')) return route.fulfill({json:{ok:true}});
    assert.equal(req.headers().authorization,'Bearer fixture');
    if(u.pathname.endsWith('/screenshot')){
      assert.equal(u.searchParams.get('id'),digest);
      return route.fulfill({contentType:'image/png',body:Buffer.from('PNG test fixture')});
    }
    if(status!==200) return route.fulfill({status,json:{ok:false}});
    const replay={checked_at:'2026-11-01T00:00:00Z',total:1,fully_replayed:1,manual_pending:1,generated:0,
      cases:[{id:'E01',title:'回放样本',status:'complete',review:'pending',execution_id:'exe1',
              checks:[{stage:'main',title:'金额正确',status:'verified',evidence:'确定性反控失败'}],gaps:[]}]};
    const visual={generated_at:'2026-11-01T00:00:00Z',model:'model',scenarios:[{id:'f1',title:'用例交接'}],
      runs:[{scenario_id:'f1',round:1,arm:'ai',status:'failed',elapsed_ms:2300,assertion:'执行按钮禁用',
         reason:'指定控件未命中',actions:1,model_calls:1,tokens:null,screenshots:[{label:'失败截图',sha256:digest}]}]};
    const mobile={build:'2.0.1（55）',device:'OPPO',notes:[],runs:[{
      flow:'收藏分类切换',round:1,status:'passed',duration_s:12,checks:['主题专区选中'],screenshots:[]}]};
    return route.fulfill({json:{ok:true,report:u.pathname.endsWith('/mobile')?mobile:u.pathname.endsWith('/replay')?replay:visual}});
  });
  await page.goto('http://127.0.0.1/');
  await page.getByText('手机回归实测',{exact:true}).click();
  await page.getByText('收藏分类切换 · 1/1 通过',{exact:true}).click();
  await page.getByText('第 1 轮 · 通过 · 12 秒',{exact:true}).click();
  await page.getByText('主题专区选中',{exact:true}).waitFor();
  await page.getByText('接口回放证据',{exact:true}).click();
  await page.getByText('E01 · 回放样本 · 完整回放',{exact:true}).click();
  await page.getByText(/金额正确。确定性反控失败/).waitFor();
  await page.getByText('视觉巡检对照',{exact:true}).click();
  await page.getByText('用例交接 · 0 / 1 轮通过',{exact:true}).click();
  await page.getByText('AI 视觉 · 第 1 轮 · 失败',{exact:true}).click();
  await page.getByText(/指定控件未命中/).waitFor();
  const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'下载失败截图'}).click()]);
  assert.equal(download.suggestedFilename(),'视觉对照-'+digest.slice(0,12)+'.png');
  status=404;
  await page.getByRole('button',{name:'刷新结果'}).last().click();
  await page.getByText('暂时没有可查看的已发布证据。',{exact:true}).waitFor();
  assert.equal(await page.getByText(/指定控件未命中/).count(),0);
  await page.evaluate(()=>{window.currentAccessProfile={user_id:'new-user'};});
  await page.getByText('接口回放证据',{exact:true}).waitFor({state:'detached'});
  assert.equal(await page.getByText(/金额正确/).count(),0);
});
