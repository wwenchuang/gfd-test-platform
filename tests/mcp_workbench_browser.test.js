const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

const html = `<main id="app"><div id="editor-area"></div></main><script>
    window.currentAccessProfile={user_id:'colleague',scope:{ui_apps:'*'}};
    window.activeWorkflow='ai_practice'; window.sessionToken=()=>'fixture';
    window.authHeaders=(extra)=>({...extra,Authorization:'Bearer fixture'});
    window.hasPermission=p=>p==='ui.view'; window.canAccessWorkflow=()=>false;
    window.toggleLibrary=()=>{}; window.nativeFetch=fetch.bind(window);
  </script><script src="/js/ui-practice-results.js"></script><script src="/js/ui-simple-entry.js"></script>
  <script>showAiPractice()</script>`;

test('workbench retries the original request after reload, reads a run and its protected evidence', async t => {
  const browser = await chromium.launch();
  t.after(() => browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(3500);
  const runId='bb40d378-8fc7-4aaf-ac6f-931e666e1042', runnerId='a'.repeat(32);
  const suite={suite_id:'published-catalog',environment:'platform',title:'已发布用例库检查'};
  const run={run_id:runId,...suite,runner_id:runnerId,created_at:1800000000,status:'passed',total:7,
    evidence:Object.fromEntries(['trace','screenshot'].map(key=>[key,'/api/ui-executions/web-check/artifact?id='+runnerId+'&item='+key]))};
  const ids=[], tools=[], artifacts=[];
  let history=[], access=true, previousScope=false,drillReads=0;
  await page.route('http://127.0.0.1/**', async route => {
    const req=route.request(), url=new URL(req.url());
    if(url.pathname==='/') return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
    if(url.pathname.startsWith('/js/')) return route.fulfill({path:path.join(__dirname,'..',url.pathname)});
    assert.equal(req.headers().authorization,'Bearer fixture');
    if(!access) return route.fulfill({status:403,json:{ok:false}});
    if(url.pathname.endsWith('/runs')) return route.fulfill({json:{ok:true,runs:history}});
    if(url.pathname.endsWith('/recovery-drill')) {drillReads++;return route.fulfill({json:{ok:true,report:null}});}
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
  assert.equal(drillReads,0);
  await panel.getByText('隔离故障恢复演练',{exact:true}).click();
  await panel.getByText('暂无当前账号可查看的恢复演练记录。',{exact:true}).waitFor();
  assert.equal(drillReads,1);
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

test('Python suite retries after reload, cancels the selected run, polls the terminal case and downloads JUnit', async t => {
  const browser = await chromium.launch();
  t.after(()=>browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(6500);
  const id='bb40d378-8fc7-4aaf-ac6f-931e666e1043';
  const catalog={suite_id:'published-catalog',environment:'platform',title:'已发布目录核对',runner:'node'};
  const feedback={suite_id:'feedback-categories',environment:'platform',title:'反馈分类核对',runner:'python'};
  const junit='/api/platform/mcp/junit?run_id='+id;
  const requests=[];
  let run=null,cancelled=false,reads=0,downloads=0;
  await page.route('http://127.0.0.1/**',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname==='/') return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
    if(url.pathname.startsWith('/js/')) return route.fulfill({path:path.join(__dirname,'..',url.pathname)});
    assert.equal(req.headers().authorization,'Bearer fixture');
    if(url.pathname.endsWith('/runs')) return route.fulfill({json:{ok:true,runs:run?[run]:[]}});
    if(url.pathname.endsWith('/acceptance')) return route.fulfill({json:{ok:true,report:null}});
    if(url.pathname+url.search===junit){
      downloads++;
      return route.fulfill({contentType:'application/xml',body:'<testsuite/>'});
    }
    const {name,arguments:args}=req.postDataJSON();
    if(name==='list_suites') return route.fulfill({json:{ok:true,result:{suites:[catalog,feedback]}}});
    if(name==='run_suite'){
      requests.push(args);
      if(requests.length===1) return route.abort('failed');
      run={run_id:id,...feedback,status:'running',request_id:args.request_id,created_at:1800000000,cases:[],evidence:{}};
    } else {
      assert.equal(args.run_id,id);
      if(name==='cancel_run'){cancelled=true;run={...run,status:'cancelling'};}
      else {
        assert.equal(name,'get_run');
        if(cancelled && ++reads>=2) run={...run,status:'cancelled',junit_url:junit,error_kind:'cancelled',cases:[
          {run_id:id,case_id:'feedback_categories',attempt:1,runner:'python',version:'v1',status:'cancelled',duration_ms:375,
            exit_code:null,error_kind:'cancelled',evidence:{}}
        ]};
      }
    }
    return route.fulfill({json:{ok:true,result:run}});
  });
  const open=()=>page.getByText('工具执行',{exact:true}).click();
  await page.goto('http://127.0.0.1/');await open();
  const select=page.getByRole('combobox',{name:'执行套件'});
  await select.selectOption('feedback-categories');
  await page.getByRole('button',{name:'开始检查'}).click();
  await page.getByRole('button',{name:'重试同一请求'}).waitFor();
  await page.reload();await open();
  await page.getByRole('button',{name:'重试同一请求'}).click();
  assert.deepEqual(requests.map(r=>r.suite_id),['feedback-categories','feedback-categories']);
  assert.deepEqual(requests[0],requests[1]);
  await page.getByRole('button',{name:'取消本次运行'}).click();
  await page.getByRole('heading',{name:'当前运行 · 正在取消'}).waitFor();
  await page.getByRole('heading',{name:'当前运行 · 已取消'}).waitFor();
  await page.getByText(/进程退出码 未记录/).waitFor();
  await page.getByText(/耗时 375 ms/).waitFor();
  await page.getByText(/版本 v1/).waitFor();
  assert.equal(await page.getByRole('button',{name:'查看截图'}).count(),0);
  const [file]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'下载 JUnit XML'}).click()]);
  assert.ok(file.suggestedFilename().endsWith('.xml'));
  assert.equal(downloads,1);
  await page.reload();await open();
  await page.getByText('我的运行记录',{exact:true}).click();
  await page.getByRole('button',{name:new RegExp(feedback.title+'.*已取消.*'+id)}).waitFor();
});

test('history presents feedback assertions, drill differences and observed worker exits independently of case exits',async t=>{
  const browser=await chromium.launch();
  t.after(()=>browser.close());
  const page=await browser.newPage();
  page.setDefaultTimeout(3000);
  const feedback={suite_id:'feedback-categories',environment:'platform',runner:'python',title:'反馈分类'};
  const drill={suite_id:'catalog-failure-drill',environment:'platform',runner:'node',title:'目录故障演练',drill:true};
  const drillId='641ad378-8fc7-4aaf-ac6f-931e666e1040';
  let recoveryStatus=200;
  const records=[
    {...feedback,case_id:'feedback_categories',status:'passed',exit_code:0,worker_exit_code:0,
      evidence:{http_status:200,actual_code:0,expected_code:0,data_is_list:true,checks:{http_200:true,code_zero:true,data_list:true}}},
    {...drill,case_id:'ui_catalog_failure_drill',status:'failed',exit_code:1,worker_exit_code:0,
      evidence:{expected_total:0,total:36},error_kind:'business'},
    {...feedback,case_id:'feedback_categories',status:'interrupted',exit_code:null,worker_exit_code:-9,error_kind:'worker_crash',evidence:{}}
  ].map((record,i)=>{
    const id='bb40d378-8fc7-4aaf-ac6f-931e666e104'+i;
    return {...record,run_id:id,request_id:id,created_at:1800000000,
      cases:[{...record,version:i?'v22':'3.11',attempt:1,run_id:id,duration_ms:27}],evidence:{}};
  });
  await page.route('http://127.0.0.1/**',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname==='/') return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
    if(url.pathname.startsWith('/js/')) return route.fulfill({path:path.join(__dirname,'..',url.pathname)});
    assert.equal(req.headers().authorization,'Bearer fixture');
    if(url.pathname.endsWith('/runs')) return route.fulfill({json:{ok:true,runs:records}});
    if(url.pathname.endsWith('/recovery-drill')) return route.fulfill({status:recoveryStatus,json:recoveryStatus===200?
      {ok:true,report:{version:1,environment:'isolated',checked_at:1800000000,checks:[{kind:'worker_crash',before:'running',
        status:'interrupted',error_kind:'worker_crash',duration_ms:213,run_id:drillId,worker_exit_code:8,verified:true}]}}:
      {ok:false,code:'receipt_unavailable'}});
    if(url.pathname.endsWith('/acceptance')) return route.fulfill({json:{ok:true,report:null}});
    const {name,arguments:args}=req.postDataJSON();
    const result=name==='list_suites'?{suites:[feedback,drill]}:records.find(row=>row.run_id===args.run_id);
    return route.fulfill({json:{ok:true,result}});
  });
  await page.goto('http://127.0.0.1/');await page.getByText('工具执行',{exact:true}).click();
  await page.getByText('业务码 0 · 预期 0',{exact:true}).waitFor();
  await page.getByText('断言 3 / 3 通过',{exact:true}).waitFor();
  await page.getByText('我的运行记录',{exact:true}).click();
  await page.getByRole('button',{name:new RegExp(records[1].run_id)}).click();
  await page.getByText('接口返回 36 条 · 演练预期 0 条',{exact:true}).waitFor();
  await page.getByText('用例退出码 1 · 进程退出码 0',{exact:true}).waitFor();
  await page.getByRole('button',{name:new RegExp(records[2].run_id)}).click();
  await page.getByText('执行原因 · 执行进程异常退出',{exact:true}).waitFor();
  await page.getByText('用例退出码 未记录 · 进程退出码 -9',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'取消本次运行'}).count(),0);
  await page.getByText('隔离故障恢复演练',{exact:true}).click();
  await page.getByText('执行器崩溃 · 恢复已核实',{exact:true}).click();
  await page.getByText('执行中 → 中断',{exact:true}).waitFor();
  await page.getByText('隔离运行编号 '+drillId,{exact:true}).waitFor();
  recoveryStatus=503;
  await page.getByRole('button',{name:'刷新记录'}).click();
  await page.getByText('恢复演练记录暂时无法读取，请刷新记录。',{exact:true}).waitFor();
  assert.equal(await page.getByText('隔离运行编号 '+drillId,{exact:true}).count(),0);
  await page.getByText('用例退出码 未记录 · 进程退出码 -9',{exact:true}).waitFor();
});

test('an old catalog request remains retryable while an unavailable pending suite can be dismissed locally',async t=>{
  const browser=await chromium.launch();t.after(()=>browser.close());
  const page=await browser.newPage();page.setDefaultTimeout(3000);
  const id='80000000-0000-4000-a000-000000000000';
  const suite={suite_id:'published-catalog',environment:'platform',runner:'node',title:'已发布目录核对'};
  const calls=[];
  await page.route('http://127.0.0.1/**',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname==='/') return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
    if(url.pathname.startsWith('/js/')) return route.fulfill({path:path.join(__dirname,'..',url.pathname)});
    if(url.pathname.endsWith('/runs')) return route.fulfill({json:{ok:true,runs:[]}});
    if(url.pathname.endsWith('/acceptance')) return route.fulfill({json:{ok:true,report:null}});
    const {name,arguments:args}=req.postDataJSON();
    if(name==='run_suite'){calls.push(args);return route.abort('failed');}
    return route.fulfill({json:{ok:true,result:{suites:[suite]}}});
  });
  await page.goto('http://127.0.0.1/');
  const key='practice-mcp-request:colleague';
  const set=async value=>{await page.evaluate(([k,v])=>sessionStorage.setItem(k,v),[key,value]);await page.reload();
    await page.getByText('工具执行',{exact:true}).click();};
  await set(id);
  await page.getByRole('button',{name:'重试同一请求'}).click();
  assert.deepEqual(calls,[{request_id:id,suite_id:'published-catalog',environment:'platform'}]);
  await set(JSON.stringify({request_id:id,suite_id:'feedback-categories',environment:'platform'}));
  await page.getByRole('button',{name:'清除本地重试记录'}).click();
  await page.locator('button:enabled').filter({hasText:'开始检查'}).waitFor();
  assert.equal(await page.getByRole('combobox',{name:'执行套件'}).inputValue(),suite.suite_id);
  assert.equal(calls.length,1);
});
