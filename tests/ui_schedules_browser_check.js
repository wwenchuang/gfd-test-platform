const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

const target = (platform, device_id) => ({platform, device_id, runner_id:'runner-'+device_id});
const baseline = id => ({case_id:id, revision:'rev-'+id, task_name:'基线 '+id,filename:id==='one'?'关节龙打印.yaml':id+'.yaml'});
const android = target('android','original'), other = target('android','tablet'), ios = target('ios','iphone'), ipad = target('ios','ipad');
const sources = [baseline('one'), baseline('two'), baseline('two')];
const initial = [
  {platform:'android',time:'05:00',target:android,cases:[sources[0]],version:'v1',next_run_at:'2026-11-26T05:00:00+08:00'},
  {platform:'ios',time:'07:00',target:ios,cases:[baseline('one'),{...baseline('defer'),deferred:true}],version:'v1',next_run_at:'2026-11-26T07:00:00+08:00'}
];

async function fixture(t, permissions, firstScheduleDevices) {
  const browser = await chromium.launch({channel:process.env.CHROME_CHANNEL || 'chrome',headless:true});
  t.after(()=>browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(4000);
  let schedules=structuredClone(initial), failSave=true;
  const posts=[];
  let schedulesRequested=false;
  await page.addInitScript(()=>sessionStorage.setItem('sessionToken','fixture'));
  await page.route('http://workbench.test/**',async route=>{
    const req=route.request(), url=new URL(req.url()), p=url.pathname;
    if (!p.startsWith('/api/')) {
      const file=path.join(__dirname,'..',p==='/'?'task-manager.html':p);
      return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404});
    }
    if (p==='/api/auth/me') return route.fulfill({json:{ok:true,user:'test',profile:{user_id:'u',permissions,scope:{ui_apps:['com.kfb.model']}}}});
    if (p==='/api/ui-executions/availability') return route.fulfill({json:{ok:true,enabled:true}});
    if (p==='/api/ui-schedules') {
      schedulesRequested=true;
      return route.fulfill({json:{ok:true,schedules}});
    }
    if (p==='/api/ui-executions/devices' && schedulesRequested && firstScheduleDevices) {
      const respond=firstScheduleDevices;
      firstScheduleDevices=null;
      return respond(route);
    }
    if (p==='/api/ui-executions/devices') return route.fulfill({json:{ok:true,devices:[android,other,ios,ipad].map(d=>({target:d,name:d.device_id,connection_status:'ready'}))}});
    if (p.endsWith('/options')) {
      const chosen=[android,other,ios,ipad].find(x=>x.device_id===url.searchParams.get('device_id')&&x.runner_id===url.searchParams.get('runner_id'));
      assert.ok(chosen);
      return route.fulfill({json:{ok:true,target:chosen,cases:sources}});
    }
    if (['/api/ui-schedules/android','/api/ui-schedules/ios'].includes(p)&&req.method()==='POST') {
      const body=req.postDataJSON();posts.push(body);
      if(failSave&&p.endsWith('/android')){failSave=false;return route.fulfill({status:503,json:{ok:false,error:'测试服务暂时离线'}});}
      const index=schedules.findIndex(s=>s.platform===body.target.platform);
      const cases=body.cases.map(c=>sources.find(item=>item.case_id===c.case_id));
      cases.push(...schedules[index].cases.filter(c=>c.deferred));
      schedules[index]={...schedules[index],...body,cases,version:'v2',next_run_at:'2026-11-26T'+body.time+':00+08:00'};
      return route.fulfill({json:{ok:true,schedule:schedules[index]}});
    }
    return route.fulfill({json:{ok:true,items:[],apps:[],groups:[],devices:[]}});
  });
  await page.goto('http://workbench.test/');
  const nav=page.getByRole('button',{name:'定时任务',exact:true});
  await nav.click();
  return {page,posts};
}

test('scheduled Android regression preserves selections on failure and saves to the exact phone', async t=>{
  const {page,posts}=await fixture(t,['ui.view','ui.edit','ui.execute']);
  await page.getByRole('button',{name:'编辑 Android 定时任务'}).click();
  await page.getByLabel('执行时间（北京时间）').fill('06:15');
  const device=page.getByLabel('执行手机');
  assert.ok(!(await device.textContent()).includes('iphone'));
  await device.selectOption({label:'tablet · runner-tablet · tablet'});
  const two=page.getByLabel('基线 two',{exact:true});
  await two.check();
  const box=await two.boundingBox();
  assert.ok(box.width>=16&&box.height>=16,'baseline checkbox is visible at its real size');
  assert.equal(await two.count(),1);
  await page.getByRole('button',{name:'保存修改'}).click();
  await page.getByText(/测试服务暂时离线/).waitFor();
  assert.equal(await two.isChecked(),true);
  assert.equal(await page.getByLabel('执行时间（北京时间）').inputValue(),'06:15');
  await page.getByRole('button',{name:'保存修改'}).click();
  await page.getByText(/已保存/).waitFor();
  assert.equal(posts.length,2);
  assert.deepEqual(posts[1],{version:'v1',time:'06:15',target:other,cases:sources.slice(0,2).map(({case_id,revision})=>({case_id,revision}))});
  await page.reload();
  await page.getByRole('button',{name:'编辑 Android 定时任务'}).click();
  assert.equal(await page.getByLabel('执行时间（北京时间）').inputValue(),'06:15');
  await page.getByRole('button',{name:'编辑 iOS 定时任务'}).click();
  await page.getByText('基线 defer',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('基线 defer',{exact:true}).count(),0);
});

test('view-only account can inspect the schedule without editing',async t=>{
  const {page,posts}=await fixture(t,['ui.view']);
  await page.getByRole('button',{name:'查看 iOS 定时任务'}).click();
  await page.getByText('基线 defer',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'保存修改'}).count(),0);
  assert.equal(posts.length,0);
});

test('iOS edits keep deferred rows outside the payload across search and a phone change',async t=>{
  const {page,posts}=await fixture(t,['ui.view','ui.edit','ui.execute']);
  await page.getByRole('button',{name:'编辑 iOS 定时任务'}).click();
  const device=page.getByLabel('执行手机');
  await device.selectOption({label:'ipad · runner-ipad · ipad'});
  await page.getByLabel('基线 one',{exact:true}).uncheck();
  await page.getByLabel('搜索基线').fill('two');
  await page.getByLabel('基线 two',{exact:true}).check();
  await page.getByLabel('执行时间（北京时间）').fill('20:30');
  await page.getByRole('button',{name:'保存修改'}).click();
  await page.getByText(/已保存/).waitFor();
  assert.deepEqual(posts,[{version:'v1',time:'20:30',target:ipad,cases:[{case_id:'two',revision:'rev-two'}]}]);
  await page.getByRole('button',{name:'编辑 iOS 定时任务'}).click();
  await page.getByText('基线 defer',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('基线 two',{exact:true}).isChecked(),true);
});

test('overview resolves exact device names before either editor is opened',async t=>{
  const {page}=await fixture(t,['ui.view'],route=>route.fulfill({json:{ok:true,devices:[
    {target:{...android,runner_id:'old-runner'},name:'旧连接'},
    {target:android,name:'OPPO Reno9'}, {target:ios,name:'iPhone 13'}
  ]}}));
  const summary=page.locator('.schedule-cards [data-summary]');
  await summary.filter({hasText:'OPPO Reno9'}).waitFor();
  await summary.filter({hasText:'iPhone 13'}).waitFor();
  assert.equal(await page.locator('.schedule-editor').isVisible(),false);
});

test('overview keeps both schedules usable when device names cannot be loaded',async t=>{
  let failed=false;
  const {page}=await fixture(t,['ui.view'],async route=>{
    failed=true;
    await route.fulfill({status:503,json:{ok:false,error:'设备暂不可用'}});
  });
  await page.getByRole('button',{name:'查看 iOS 定时任务'}).click();
  await page.getByText('基线 defer',{exact:true}).waitFor();
  assert.equal(failed,true);
  assert.match(await page.locator('.schedule-cards').textContent(),/original/);
  assert.match(await page.locator('.schedule-cards').textContent(),/iphone/);
});

test('late overview response leaves current editor phone, time and checkboxes intact',async t=>{
  let release;
  const {page}=await fixture(t,['ui.view','ui.edit','ui.execute'],route=>new Promise(resolve=>{
    release=async()=>{await route.fulfill({json:{ok:true,devices:[{target:android,name:'旧记录'}]}});resolve();};
  }));
  await page.getByRole('button',{name:'编辑 Android 定时任务'}).click();
  const device=page.getByLabel('执行手机');
  await device.selectOption({label:'tablet · runner-tablet · tablet'});
  await page.getByLabel('基线 two',{exact:true}).check();
  await page.getByLabel('执行时间（北京时间）').fill('15:35');
  const response=page.waitForResponse(r=>r.url().includes('/ui-executions/devices'));
  await release();
  await (await response).finished();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(resolve)));
  assert.match(await device.inputValue(),/runner-tablet/);
  assert.equal(await page.getByLabel('执行时间（北京时间）').inputValue(),'15:35');
  assert.equal(await page.getByLabel('基线 two',{exact:true}).isChecked(),true);
  assert.equal(await page.getByRole('button',{name:'保存修改'}).isEnabled(),true);
});

test('manual run confirms saved Android settings and reuses its request after a lost receipt and reload',async t=>{
  const {page,posts}=await fixture(t,['ui.view','ui.edit','ui.execute']);
  const runs=[];
  await page.route('**/ui-schedules/android/run',async route=>{
    runs.push(route.request().postDataJSON());
    if (runs.length===1) return route.abort('failed');
    return route.fulfill({json:{ok:true,status:'queued',run_id:'run-test'}});
  });
  await page.getByRole('button',{name:'编辑 Android 定时任务'}).click();
  await page.getByLabel('基线 two',{exact:true}).check();
  const run=page.getByRole('button',{name:'手动执行 Android 定时任务'});
  page.once('dialog',async dialog=>{
    assert.match(dialog.message(),/已保存/);
    assert.match(dialog.message(),/1 条/);
    assert.match(dialog.message(),/真实打印/);
    await dialog.dismiss();
  });
  await run.click();
  assert.equal(runs.length,0);
  page.once('dialog',dialog=>dialog.accept());
  await run.click();
  await page.getByText(/提交结果待核对/).waitFor();
  assert.equal(await page.getByLabel('基线 two',{exact:true}).isChecked(),true);
  await page.reload();
  page.once('dialog',dialog=>dialog.accept());
  await page.getByRole('button',{name:'核对上次 Android 手动执行'}).click();
  await page.getByText(/已排队.*run-test/).waitFor();
  assert.deepEqual(runs[0],runs[1]);
  assert.deepEqual(Object.keys(runs[0]).sort(),['request_id','version']);
  assert.equal(runs[0].version,'v1');
  assert.ok(runs[0].request_id);
  assert.deepEqual(posts,[]);
});
test('manual iOS run reports a version conflict and excludes deferred rows',async t=>{
  const {page}=await fixture(t,['ui.view','ui.edit','ui.execute']);
  await page.route('**/ui-schedules/ios/run',route=>route.fulfill({status:409,json:{ok:false,error:'配置已变化，请刷新。'}}));
  page.once('dialog',async dialog=>{
    assert.match(dialog.message(),/1 条/);
    await dialog.accept();
  });
  await page.getByRole('button',{name:'手动执行 iOS 定时任务'}).click();
  await page.getByText(/手动执行未提交.*配置已变化/).waitFor();
});

test('one manual submission stays in flight per platform',async t=>{
  const {page}=await fixture(t,['ui.view','ui.edit','ui.execute']);
  let finish, count=0;
  await page.route('**/ui-schedules/android/run',route=>{
    count++;
    return new Promise(resolve=>{
      finish=async()=>{await route.fulfill({json:{ok:true,status:'running',run_id:'run-active'}});resolve();};
    });
  });
  page.once('dialog',dialog=>dialog.accept());
  await page.getByRole('button',{name:'手动执行 Android 定时任务'}).click();
  const busy=page.getByRole('button',{name:'核对上次 Android 手动执行'});
  await busy.waitFor();
  assert.equal(await busy.isDisabled(),true);
  await busy.evaluate(button=>button.click());
  assert.equal(count,1);
  await finish();
  await page.getByText(/执行中.*run-active/).waitFor();
});

for (const permission of ['ui.edit','ui.execute']) {
  test(permission+' alone cannot launch a schedule',async t=>{
    const {page}=await fixture(t,['ui.view',permission]);
    await page.getByRole('button',{name:'查看 Android 定时任务'}).waitFor();
    assert.equal(await page.getByRole('button',{name:/手动执行/}).count(),0);
  });
}

test('refreshing published versions preserves the same selected cases and requires saving', async t => {
  const {page,posts} = await fixture(t,['ui.view','ui.edit','ui.execute']);
  await page.route('**/api/ui-schedules/android/options?**', route => route.fulfill({
    json:{target:android,cases:[{...sources[0],revision:'latest'},sources[1]]}
  }));
  await page.getByRole('button',{name:'编辑 Android 定时任务'}).click();
  await page.getByLabel('基线 one',{exact:true}).waitFor();
  await page.getByLabel('执行时间（北京时间）').fill('05:01');
  assert.equal(await page.getByLabel('基线 one',{exact:true}).isChecked(),true);
  assert.equal(await page.getByLabel('基线 two',{exact:true}).isChecked(),false);
  assert.equal(posts.length,0);
  await page.getByRole('button',{name:'保存修改'}).click();
  await page.getByText(/测试服务暂时离线/).waitFor();
  assert.deepEqual(posts[0].cases,[{case_id:'one',revision:'latest'}]);
});
