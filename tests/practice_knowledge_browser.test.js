const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

test('workbench searches with the current app, shows citations, abstains and clears revoked evidence', async t => {
  const browser = await chromium.launch();
  t.after(() => browser.close());
  const page = await browser.newPage();
  const html = `<main id="app"><div id="editor-area"></div></main><script>
    window.currentAccessProfile={user_id:'u',scope:{ui_apps:['com.kfb.model']}};
    window.activeWorkflow='ai_practice';window.sessionToken=()=>'fixture';
    window.authHeaders=(init)=>{const headers=new Headers(init);headers.set('Authorization','Bearer fixture');return headers;};window.hasPermission=(p)=>p==='ui.view';
    window.canAccessWorkflow=()=>true;window.toggleLibrary=()=>{};window.nativeFetch=fetch.bind(window);
    </script><script src="/js/ui-practice-knowledge.js"></script><script src="/js/ui-practice-results.js"></script>
    <script src="/js/ui-simple-entry.js"></script><script>showAiPractice()</script>`;
  const calls = [];
  let revoked = false;
  let delayed;
  let requestStarted;
  const delayedRequest = new Promise(resolve => {requestStarted = resolve;});
  await page.route('http://127.0.0.1/**', async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.pathname === '/') return route.fulfill({contentType:'text/html; charset=utf-8', body:html});
    if (url.pathname.startsWith('/js/')) return route.fulfill({path:path.join(__dirname,'..',url.pathname)});
    assert.equal(req.headers().authorization, 'Bearer fixture');
    const name = url.pathname.split('/').pop();
    calls.push(name);
    if (revoked) return route.fulfill({status:403, json:{ok:false}});
    if (name === 'knowledge') return route.fulfill({json:{ok:true, apps:[{id:'com.kfb.model', name:'智小白3D'}]}});
    if (name === 'evaluation') return route.fulfill({json:{ok:true, report:{
      methods:{vector:{metrics:{citation_support:{count:5,total:6}}}},
      human_review:{reviewed:0,total:30,status:'pending'},
      questions:[{id:'H1',question:'预期问题',gold:[{document_id:'doc1',paragraph_id:'p1'}],
        results:{vector:{status:'cited',top5:[{document_id:'doc1',paragraph_id:'p1'}]}}}]
    }}});
    assert.equal(req.method(),'POST');
    const payload = req.postDataJSON();
    assert.equal(payload.app_id, 'com.kfb.model');
    const known = payload.question === '证据在哪';
    const reply = () => route.fulfill({json:{ok:true,result:{
      method:payload.method, document_count:10, paragraph_count:37, status:known?'cited':'unknown',answer:known?'已核对摘要':'材料不足',
      results:known?[{title:'资料',document_id:'doc1',paragraph_id:'p1',locator:'需求章节一',version:'abc',excerpt:'引用原文'}]:[]}}});
    if (payload.question === '慢速请求') {
      delayed = reply;
      requestStarted();
      return;
    }
    return reply();
  });
  await page.goto('http://127.0.0.1/');
  assert.equal(calls.length, 0);
  await page.getByText('测试知识检索',{exact:true}).click();
  const question = page.getByRole('textbox',{name:'测试问题'});
  const submit = page.getByRole('button',{name:'检索',exact:true});
  await question.fill('证据在哪');
  await page.getByRole('combobox',{name:'检索方式'}).selectOption('keyword');
  await submit.click();
  await page.getByText('已核对摘要',{exact:true}).waitFor();
  await page.getByText('资料 · doc1 / p1',{exact:true}).click();
  await page.getByText('引用原文',{exact:true}).waitFor();
  await page.getByText('原文位置 · 需求章节一',{exact:true}).waitFor();
  await page.getByText('材料版本 · abc',{exact:true}).waitFor();
  await question.fill('未知问题');
  await submit.click();
  await page.getByRole('heading',{name:'未知'}).waitFor();
  assert.equal(await page.getByText('引用原文').count(), 0);
  await page.getByText('查看检索评估与人工审查').click();
  await page.getByText('引用支持答案 · 5 / 6').waitFor();
  await page.getByText('待审查 30 题', {exact:false}).waitFor();
  await page.getByText('H1 · 预期问题').click();
  await page.getByText('预期来源 · doc1 / p1').waitFor();
  await question.fill('慢速请求');
  await submit.click();
  await delayedRequest;
  await question.fill('证据在哪');
  await submit.click();
  await page.getByText('已核对摘要').waitFor();
  await delayed();
  await page.waitForLoadState('networkidle');
  assert.equal(await page.getByRole('heading',{name:'未知'}).count(),0);
  revoked = true;
  await submit.click();
  await page.getByText('当前登录或应用权限无法读取测试知识',{exact:false}).waitFor();
  assert.equal(await page.getByText('已核对摘要').count(),0);
  assert.equal(await page.getByText('预期问题',{exact:false}).count(),0);
  await page.evaluate(()=>{currentAccessProfile={user_id:'new-user'};});
  await page.getByText('测试知识检索',{exact:true}).waitFor({state:'detached'});
});
