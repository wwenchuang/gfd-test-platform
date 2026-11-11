const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

test('workbench retries the original request after reload, reads a run and its protected evidence', async t => {
  const browser = await chromium.launch();
  t.after(() => browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(3500);
  const html = `<main id="app"><div id="editor-area"></div></main><script>
    window.currentAccessProfile={user_id:'colleague',scope:{ui_apps:'*'}};
    window.activeWorkflow='ai_practice'; window.sessionToken=()=>'fixture';
    window.authHeaders=(extra)=>({...extra,Authorization:'Bearer fixture'});
    window.hasPermission=p=>p==='ui.view'; window.canAccessWorkflow=()=>false;
    window.toggleLibrary=()=>{}; window.nativeFetch=fetch.bind(window);
  </script><script src="/js/ui-practice-results.js"></script><script src="/js/ui-simple-entry.js"></script>
  <script>showAiPractice()</script>`;
  const runId='bb40d378-8fc7-4aaf-ac6f-931e666e1042', runnerId='a'.repeat(32);
  const suite={suite_id:'published-catalog',environment:'platform',title:'已发布用例库检查'};
  const run={run_id:runId,...suite,runner_id:runnerId,created_at:1800000000,status:'passed',total:7,
    evidence:Object.fromEntries(['trace','screenshot'].map(key=>[key,'/api/ui-executions/web-check/artifact?id='+runnerId+'&item='+key]))};
  const ids=[], tools=[], artifacts=[];
  let history=[], access=true, previousScope=false;
  await page.route('http://127.0.0.1/**', async route => {
    const req=route.request(), url=new URL(req.url());
    if(url.pathname==='/') return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
    if(url.pathname.startsWith('/js/')) return route.fulfill({path:path.join(__dirname,'..',url.pathname)});
    assert.equal(req.headers().authorization,'Bearer fixture');
    if(!access) return route.fulfill({status:403,json:{ok:false}});
    if(url.pathname.endsWith('/runs')) return route.fulfill({json:{ok:true,runs:history}});
    if(url.pathname.endsWith('/acceptance')) return route.fulfill({json:{ok:true,report:{version:1,run_id:runId,checked_at:1800000000,
      checks:[{name:'非法环境',status:'passed',detail:'工具拒绝未知环境'}]}}});
    if(url.pathname.endsWith('/artifact')){
      assert.equal(url.searchParams.get('id'),runnerId);
      const item=url.searchParams.get('item');artifacts.push(item);
      return route.fulfill(item==='screenshot'?{contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j6iUAAAAASUVORK5CYII=','base64')}:{contentType:'application/zip',body:'fixture zip'});
    }
    assert.equal(url.pathname,'/api/platform/mcp/call');
    const call=req.postDataJSON();tools.push(call.name);
    if(call.name==='list_suites') return route.fulfill({json:{ok:true,result:{suites:[suite]}}});
    if(call.name==='run_suite'&&call.arguments.environment==='unlisted') {
      return route.fulfill({status:400,json:{ok:false,code:'invalid_arguments',error:'无效环境'}});
    }
    if(call.name==='run_suite'){
      assert.equal(call.arguments.environment,'platform');
      assert.equal(call.arguments.suite_id,suite.suite_id);
      if(previousScope) return route.fulfill({status:404,json:{ok:false,code:'run_not_found'}});
      ids.push(call.arguments.request_id);
      if(ids.length===1) return route.abort('failed');
      history=[{...run,request_id:ids[0]}];
      return route.fulfill({json:{ok:true,result:history[0]}});
    }
    assert.equal(call.name,'get_run');assert.equal(call.arguments.run_id,runId);
    return route.fulfill({json:{ok:true,result:history[0]}});
  });
  await page.goto('http://127.0.0.1/');
  const panel=page.locator('details').filter({has:page.locator('summary').getByText('工具执行',{exact:true})}).first();
  const open=()=>page.getByText('工具执行',{exact:true}).click();
  await open();
  await panel.getByRole('button',{name:'开始检查'}).click();
  await panel.getByRole('button',{name:'重试同一请求'}).waitFor();
  await page.reload(); await open();
  await panel.getByRole('button',{name:'重试同一请求'}).click();
  await panel.getByText('接口返回 7 条已发布用例。').waitFor();
  assert.match(ids[0],/^[0-9a-f-]{36}$/);
  assert.deepEqual(ids,[ids[0],ids[0]]);
  await panel.getByRole('button',{name:'查看截图'}).click();
  await panel.getByRole('img',{name:'本次用例库检查截图'}).waitFor();
  const [download]=await Promise.all([page.waitForEvent('download'),panel.getByRole('button',{name:'下载 trace'}).click()]);
  assert.ok(download.suggestedFilename().endsWith('.zip'));
  assert.deepEqual(artifacts,['screenshot','trace']);
  await panel.getByText('本次网页工具调用',{exact:true}).click();
  await panel.getByRole('button',{name:'验证环境限制'}).click();
  await panel.getByText('已拒绝未列出的环境（invalid_arguments）。',{exact:true}).waitFor();
  await panel.getByText('MCP 客户端验收',{exact:true}).click();
  await panel.getByText(/工具拒绝未知环境/).waitFor();
  assert.ok(tools.includes('get_run'));
  previousScope=true;
  await panel.getByRole('button',{name:'再运行一次'}).click();
  await panel.getByText('当前权限范围无法读取该请求。可重新发起检查。',{exact:true}).waitFor();
  assert.equal(await panel.getByRole('button',{name:'重试同一请求'}).count(),0);
  access=false;
  await panel.getByRole('button',{name:'刷新记录'}).click();
  await panel.getByText(/权限已变化/).waitFor();
  assert.equal(await panel.getByText(/接口返回 7 条/).count(),0);
  assert.equal(await panel.getByText(/工具拒绝未知环境/).count(),0);
  await page.evaluate(()=>{window.currentAccessProfile={user_id:'other'};});
  await page.getByText('工具执行',{exact:true}).waitFor({state:'detached'});
});
