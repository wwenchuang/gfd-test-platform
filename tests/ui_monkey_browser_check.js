const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

const target = {device_id:'phone',platform:'ios',runner_id:'mac'};
async function fixture(t, execute=true, edit=true) {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  t.after(()=>browser.close());
  const page = await browser.newPage({reducedMotion:'reduce'});
  page.setDefaultTimeout(7000);
  const writes=[];
  const run={run_id:'monkey_test',app_id:'demo',target,mode:'mixed',event_count:12,seed:42,executed:0,status:'queued',log:'',message:''};
  const prior={...run,run_id:'old',status:'crashed',mode:'browse',executed:7,crash_log:'application crash evidence'};
  await page.addInitScript(()=>sessionStorage.setItem('sessionToken','fixture'));
  await page.route('http://workbench.test/**',async route=>{
    const req=route.request(), p=new URL(req.url()).pathname;
    if (!p.startsWith('/api/')) {
      const file=path.join(__dirname,'..',p==='/'?'task-manager.html':p);
      return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404});
    }
    const json=value=>route.fulfill({json:{ok:true,...value}});
    if (p==='/api/auth/me')return json({user:'test',profile:{user_id:'tester',permissions:['ui.view',...(execute?['ui.execute']:[]),...(edit?['ui.edit']:[])],scope:{ui_apps:['demo']}}});
    if (p==='/api/ui-executions/availability')return json({enabled:true});
    if (p==='/api/ui-executions/catalog')return json({apps:[{app_id:'demo',name:'演示应用'}]});
    if (p==='/api/ui-executions/devices')return json({devices:[
      {target,name:'iPhone 13',connection_status:'ready',monkey_supported:true},
      {target:{...target,device_id:'old'},name:'旧 Runner',connection_status:'ready',monkey_supported:false}]});
    if (p==='/api/ui-monkey/schedules')return json({schedules:[]});
    if (p==='/api/ui-monkey/runs'&&req.method()==='POST'){
      writes.push(req.postDataJSON());return json({run});
    }
    if (p==='/api/ui-monkey/runs')return json({runs:[prior]});
    if (p.endsWith('/old/analysis'))return json({run:{...prior,analysis:{model:'model-test',facts:['已采集崩溃'],hypotheses:['待验证原因'],next_steps:['复现并核对']}}});
    if (p.endsWith('/old'))return json({run:prior});
    if (p.endsWith('/monkey_test/stop'))return json({run:Object.assign(run,{status:'stopped'})});
    if (p.endsWith('/monkey_test'))return json({run:Object.assign(run,{status:'running',executed:3,log:'执行事件 3 <img src=x>'})});
    return json({items:[],apps:[],groups:[],devices:[]});
  });
  await page.goto('http://workbench.test/');
  await page.getByRole('button',{name:'Monkey 稳定性测试',exact:true}).click();
  await page.getByLabel('测试应用',{exact:true}).selectOption('demo');
  return {page,writes};
}
test('workbench filters devices, starts an exact seeded run, polls real progress and stops',async t=>{
  const {page,writes}=await fixture(t);
  const device=page.getByLabel('执行手机',{exact:true});
  await device.selectOption({label:'iPhone 13 · iOS · 设备编号 phone'});
  assert.doesNotMatch(await device.textContent(),/旧 Runner/);
  assert.equal(await page.getByLabel('操作模式').inputValue(),'mixed');
  await page.getByText(/随机点击可能变更应用数据/).waitFor();
  await page.getByLabel('事件数',{exact:true}).fill('12');
  await page.getByLabel('随机种子').fill('42');
  await page.getByRole('button',{name:'开始 Monkey 测试'}).click();
  await page.getByText('执行事件 3 <img src=x>',{exact:true}).waitFor();
  assert.equal(await page.locator('[data-monkey-detail] progress').evaluate(p=>p.value),3);
  assert.equal(await page.locator('[data-monkey-detail] img').count(),0);
  assert.equal(writes.length,1);
  assert.deepEqual(writes[0],{app_id:'demo',target,event_count:12,seed:42,request_id:writes[0].request_id,mode:'mixed'});
  assert.ok(writes[0].request_id);
  await page.getByRole('button',{name:'停止执行',exact:true}).click();
  await page.locator('[data-monkey-state]').getByText('已停止',{exact:true}).waitFor();
  await page.getByRole('button',{name:/应用崩溃 · iPhone 13/}).click();
  await page.getByRole('button',{name:'AI 分析事故证据'}).click();
  await page.getByText('已采集崩溃',{exact:true}).waitFor();
  await page.getByText('待验证原因',{exact:true}).waitFor();
  await page.getByText('复现并核对',{exact:true}).waitFor();
});
test('view-only users can inspect incident evidence',async t=>{
  const {page,writes}=await fixture(t,false);
  await page.getByRole('button',{name:/应用崩溃 · iPhone 13/}).click();
  await page.getByText('application crash evidence',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'开始 Monkey 测试'}).count(),0);
  assert.equal(await page.getByRole('button',{name:'AI 分析事故证据'}).count(),0);
  assert.deepEqual(writes,[]);
});

test('unknown submission survives reload and uses the exact same request id and selection', async t=>{
  const {page,writes}=await fixture(t);
  const originals=[];
  await page.route('**/api/ui-monkey/runs',async(route)=>{
    if(route.request().method()!=='POST')return route.fallback();
    originals.push(route.request().postDataJSON());
    if(originals.length===1)return route.abort();
    return route.fallback();
  });
  await page.getByLabel('执行手机',{exact:true}).selectOption({label:'iPhone 13 · iOS · 设备编号 phone'});
  await page.getByRole('button',{name:'开始 Monkey 测试'}).click();
  await page.getByRole('button',{name:'核对上次提交'}).waitFor();
  await page.reload();
  await page.getByLabel('测试应用',{exact:true}).selectOption('demo');
  const retry=page.getByRole('button',{name:'核对上次提交'});
  await retry.click();
  await page.getByText('执行事件 3 <img src=x>',{exact:true}).waitFor();
  assert.equal(originals.length,2);
  assert.deepEqual(originals[1],originals[0]);
  assert.equal(writes.length,1);
});

test('leaving an active detail cancels polling and stale devices cannot enter a different app', async t=>{
  const {page}=await fixture(t);
  await page.route('**/api/ui-executions/catalog',route=>route.fulfill({json:{ok:true,apps:[{app_id:'demo',name:'演示应用'},{app_id:'other',name:'应用乙'}]}}));
  await page.reload();
  let releasePhones,resolveStart;
  const started=new Promise(r=>resolveStart=r);
  await page.route('**/api/ui-executions/devices?app_id=demo',async route=>{
    resolveStart();
    await new Promise(resolve=>releasePhones=resolve);
    return route.fallback();
  });
  await page.getByLabel('测试应用',{exact:true}).selectOption('demo');
  await started;
  await page.route('**/api/ui-executions/devices?app_id=other',route=>route.fulfill({json:{ok:true,devices:[]}}));
  await page.getByLabel('测试应用',{exact:true}).selectOption('other');
  releasePhones();
  await page.getByText('暂无支持 Monkey 的就绪手机',{exact:true}).waitFor({state:'attached'});
  assert.doesNotMatch(await page.getByLabel('执行手机',{exact:true}).textContent(),/iPhone/);
  await page.unroute('**/api/ui-executions/devices?app_id=demo');
  await page.getByLabel('测试应用',{exact:true}).selectOption('demo');
  await page.getByLabel('执行手机',{exact:true}).selectOption({label:'iPhone 13 · iOS · 设备编号 phone'});
  let polls=0;
  await page.route('**/api/ui-monkey/runs/monkey_test',async route=>{polls++;return route.fallback();});
  await page.getByRole('button',{name:'开始 Monkey 测试'}).click();
  await page.getByText('执行事件 3 <img src=x>',{exact:true}).waitFor();
  await page.getByRole('button',{name:'开始测试',exact:true}).click();
  const before=polls;
  await new Promise(r=>setTimeout(r,2700));
  assert.equal(polls,before);
});

test('lost runner retains evidence count and a reduced-motion user sees no pulse',async t=>{
  const {page}=await fixture(t);
  const run={app_id:'demo',target,run_id:'monkey_test',mode:'browse',event_count:20,executed:4,seed:2,status:'running',started_ns:10e9,updated_ns:75e9};
  let lost=false;
  await page.route('**/api/ui-monkey/runs/monkey_test',route=>route.fulfill({json:{ok:true,run:{...run,connection_lost:lost}}}));
  await page.getByLabel('执行手机',{exact:true}).selectOption({label:'iPhone 13 · iOS · 设备编号 phone'});
  await page.getByRole('button',{name:'开始 Monkey 测试'}).click();
  await page.getByText('已执行 4 / 20 个事件').waitFor();
  assert.equal(await page.locator('[data-elapsed]').textContent(),'已运行 1 分 5 秒');
  assert.equal(await page.locator('[data-events] [data-phase=latest]').textContent(),'4');
  assert.equal(await page.locator('[data-fill]').evaluate(n=>getComputedStyle(n).transitionDuration),'0s');
  const pulse=()=>page.locator('[data-monkey-state]').evaluate(el=>getComputedStyle(el,'::before').animationName);
  assert.equal(await pulse(),'none');
  await page.emulateMedia({reducedMotion:'no-preference'});
  assert.equal(await pulse(),'ui-run-pulse');
  assert.notEqual(await page.locator('[data-fill]').evaluate(n=>getComputedStyle(n).transitionDuration),'0s');
  lost=true;
  await page.getByText(/Runner 超过 120 秒未回报/).waitFor();
  assert.equal(await pulse(),'none');
  assert.equal(await page.locator('[data-monkey-detail] progress').evaluate(p=>p.value),4);
});

test('manual duration accepts custom minutes and displays only measured elapsed time and events',async t=>{
  const {page}=await fixture(t);
  let body;
  const run={app_id:'demo',target,run_id:'timed',mode:'browse',duration_seconds:2220,event_count:null,executed:42,
    elapsed_seconds:17,status:'running',started_ns:10e9,updated_ns:600e9,seed:1};
  await page.route('**/api/ui-monkey/runs',async route=>{
    if(route.request().method()!=='POST')return route.fallback();
    body=route.request().postDataJSON();return route.fulfill({json:{ok:true,run}});
  });
  await page.getByLabel('执行手机',{exact:true}).selectOption({label:'iPhone 13 · iOS · 设备编号 phone'});
  await page.getByLabel('执行上限').selectOption('duration');
  await page.getByRole('button',{name:'24 小时',exact:true}).click();
  assert.equal(await page.getByLabel('持续时长（分钟）').inputValue(),'1440');
  await page.getByLabel('持续时长（分钟）').fill('1441');
  assert.equal(await page.getByRole('button',{name:'开始 Monkey 测试'}).isDisabled(),true);
  await page.getByLabel('持续时长（分钟）').fill('37');
  await page.getByRole('button',{name:'开始 Monkey 测试'}).click();
  await page.getByText('已执行 42 个事件',{exact:true}).waitFor();
  assert.equal(body.duration_seconds,2220);assert.equal('event_count' in body,false);
  assert.equal(await page.locator('[data-elapsed]').textContent(),'实际运行 17 秒 · 计划 37 分钟');
  assert.equal(await page.locator('[data-monkey-detail] progress').evaluate(n=>[n.max,n.value].join('/')),'2220/17');
  assert.equal(await page.locator('[data-events] [data-phase=pending]').count(),0);
});

test('daily plans can be saved, reopened and toggled using the exact saved settings',async t=>{
  const {page,writes}=await fixture(t);
  const requests=[];let saved;
  await page.route('**/api/ui-monkey/schedules*',async route=>{
    if(route.request().method()==='POST'){
      const body=route.request().postDataJSON();requests.push(body);
      saved={...body,last_status:'skipped',last_message:'手机忙碌'};
      return route.fulfill({json:{ok:true,schedule:saved}});
    }
    return route.fulfill({json:{ok:true,schedules:saved?[saved]:[]}});
  });
  await page.getByLabel('执行方式').selectOption('daily');
  await page.getByLabel('执行手机',{exact:true}).selectOption({label:'iPhone 13 · iOS · 设备编号 phone'});
  await page.getByLabel('执行上限').selectOption('duration');
  await page.getByLabel('持续时长（分钟）').fill('90');
  await page.getByLabel('每日时间（北京时间）').fill('06:15');
  await page.getByLabel('随机种子').fill('13');
  await page.getByRole('button',{name:'保存每日方案'}).click();
  await page.getByRole('button',{name:'停用方案'}).waitFor();
  await page.reload();
  await page.getByLabel('测试应用',{exact:true}).selectOption('demo');
  await page.getByRole('button',{name:'编辑方案'}).click();
  assert.equal(await page.getByLabel('持续时长（分钟）').inputValue(),'90');
  assert.equal(await page.getByLabel('每日时间（北京时间）').inputValue(),'06:15');
  assert.equal(await page.getByLabel('随机种子').inputValue(),'13');
  await page.getByLabel('持续时长（分钟）').fill('120');
  await page.getByRole('button',{name:'停用方案'}).click();
  await page.getByRole('button',{name:'启用方案'}).click();
  await page.getByRole('button',{name:'停用方案'}).waitFor();
  assert.deepEqual(requests[0],{app_id:'demo',target,mode:'mixed',duration_seconds:5400,seed:13,daily_time:'06:15',enabled:true});
  assert.deepEqual(requests,[requests[0],{...requests[0],enabled:false},requests[0]]);
  assert.deepEqual(writes,[]);
  assert.match(await page.locator('[data-schedules]').textContent(),/手机忙碌/);
});

test('offline saved event plan can be disabled without selecting a replacement device',async t=>{
  const {page}=await fixture(t);
  const saved={app_id:'demo',target:{platform:'android',device_id:'offline',runner_id:'original'},
    event_count:345,mode:'mixed',seed:0,daily_time:'23:59',enabled:true};
  const posts=[];
  await page.route('**/api/ui-monkey/schedules*',async route=>{
    if(route.request().method()==='POST'){
      posts.push(route.request().postDataJSON());
      return route.fulfill({json:{ok:true,schedule:{...saved,enabled:false}}});
    }
    return route.fulfill({json:{ok:true,schedules:[saved]}});
  });
  await page.locator('.ui-monkey [data-refresh]').click();
  await page.getByRole('button',{name:'编辑方案'}).click();
  assert.equal(await page.getByLabel('执行上限').inputValue(),'events');
  assert.equal(await page.getByLabel('事件数',{exact:true}).inputValue(),'345');
  await page.getByText(/随机点击可能变更应用数据/).waitFor();
  await page.getByRole('button',{name:'停用方案'}).click();
  await page.getByRole('button',{name:'启用方案'}).waitFor();
  assert.deepEqual(posts,[{...saved,enabled:false}]);
  await page.getByLabel('执行方式').selectOption('manual');
  assert.equal(await page.getByLabel('执行手机',{exact:true}).inputValue(),'');
  assert.equal(await page.getByRole('button',{name:'开始 Monkey 测试'}).isDisabled(),true);
});
test('execute without edit can launch manual runs but cannot write daily plans',async t=>{
  const {page}=await fixture(t,true,false);
  await page.getByRole('button',{name:'开始 Monkey 测试'}).waitFor();
  await page.getByLabel('执行方式').selectOption('daily');
  assert.equal(await page.getByRole('button',{name:'保存每日方案'}).count(),0);
});

test('history identifies an offline phone and links its full serial to its model in details',async t=>{
  const {page}=await fixture(t,false);
  await page.route('**/api/ui-executions/devices?app_id=demo',route=>route.fulfill({json:{ok:true,devices:[
    {target:{...target,runner_id:'another'},name:'同号的其他设备',connection_status:'ready',monkey_supported:true},
    {target,name:'iPhone 13',connection_status:'offline',monkey_supported:false}
  ]}}));
  await page.locator('.ui-monkey [data-refresh]').click();
  await page.waitForFunction(()=>document.querySelector('.ui-monkey [data-device]').textContent.includes('同号的其他设备'));
  const row=page.locator('[data-history] button').first();
  assert.match(await row.innerText(),/iPhone 13/);
  assert.doesNotMatch(await row.innerText(),/同号的其他设备|old/);
  await row.click();
  assert.match(await page.locator('[data-summary]').innerText(),/iPhone 13 · iOS · 设备编号 phone/);
  await page.route('**/api/ui-executions/devices?app_id=demo',route=>route.fulfill({json:{ok:true,devices:[]}}));
  await page.locator('.ui-monkey [data-refresh]').click();
  await page.getByRole('button',{name:/应用崩溃 · iOS 设备（机型未记录）/}).waitFor();
});
