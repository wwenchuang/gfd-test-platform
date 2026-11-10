'use strict';
const {catalogTarget} = require('./policy');

const scenarios = new Set(['ui_case_selection', 'ui_case_revision', 'ui_start_handoff', 'ui_start_switch', 'ui_start_unready']);
const statuses = new Set(['ready','offline','not_ready','mapping_missing','upgrade_required']);
const booleanFields=new Set(['ok','enabled','evidence_available','must_change_password','is_superuser','execution_eligible','has_more']);
function pick(obj,keys){
  const projected={};
  for(const key of keys){
    if(!obj||!Object.hasOwn(obj,key))continue;
    const value=obj[key];
    const valid=booleanFields.has(key)?typeof value==='boolean':key==='total'?Number.isSafeInteger(value)&&value>=0:
      key==='modules'?Array.isArray(value)&&value.every(item=>typeof item==='string'):typeof value==='string';
    if(!valid)throw Error('invalid_browser_field');
    projected[key]=value;
  }
  return projected;
}

// Keep private identity, notification settings, serials and phone images out of browser traces.
// The host makes each GET with the actual session and verifies its owner separately.
function browserData(path, data, app) {
  if (data?.ok!==true) return {ok:false};
  if (path.endsWith('/auth/me')) {
    const profile = data.profile || {};
    return {ok:true,user:'检查账号',profile:{...pick(profile,['must_change_password','is_superuser']),status:profile.status==='active'?'active':'disabled',
      user_id:'web-check',username:'检查账号',permissions:(profile.permissions||[]).filter(key=>['ui.view','ui.execute'].includes(key))}};
  }
  if (path.endsWith('/availability')) return pick(data,['ok','enabled','evidence_available']);
  if (path.endsWith('/devices')) {
    if (!Array.isArray(data.devices)) throw Error('invalid_devices');
    return {ok:true,devices:data.devices.map((device,index)=>{
      if (device.app_id!==app || !statuses.has(device.connection_status) || !device.target?.device_id || !device.target.runner_id ||
          !['android','ios'].includes(device.target.platform)) throw Error('invalid_device');
      return {connection_status:device.connection_status,name:'设备 '+(index+1),
        target:{platform:device.target.platform,device_id:String(index+1),runner_id:'已脱敏'}};
    })};
  }
  if (path.endsWith('/catalog')) {
    const row = item => pick(item,['case_id','app_id','revision','task_name','module','execution_eligible','block_code']);
    if (!Array.isArray(data.groups)||!Array.isArray(data.items)||!Array.isArray(data.apps)) throw Error('invalid_catalog');
    return {...pick(data,['ok','total','modules']),items:data.items.map(row),apps:data.apps.map(app=>pick(app,['app_id','name'])),
      groups:data.groups.map(group=>({...pick(group,['app_id','name','total','has_more']),items:group.items.map(row)}))};
  }
  throw Error('unexpected_api');
}

async function checkWorkflow({scenario,page,state,catalogQueries,deviceQueries}) {
  const blocked = detail => ({status:'blocked',error_kind:'precondition',detail});
  const profile=state.auth.profile;
  if (!profile.is_superuser && !profile.permissions?.includes('ui.execute')) return blocked('execute_permission_required');
  const initial=state.catalog, target=catalogTarget(initial);
  if (!target) return blocked('no_published_case');
  const other=initial.apps.find(a=>typeof a.app_id==='string' && a.app_id!==target.app && a.app_id.length>0 && a.app_id.length<=128 && !/[\x00-\x1f\x7f]/.test(a.app_id));
  if (scenario==='ui_start_switch' && !other) return blocked('no_second_app');
  const asset={case_id:target.caseId,revision:target.revision,app_id:target.app};
  const evidence={};
  const failed={status:'failed',error_kind:'business',asset,phase:'workflow'};
  const appSelect=page.locator('[name="ui-app"]');
  const caseKey=await page.evaluate(value=>CSS.escape(value),target.caseId);
  const checkbox=page.locator('[data-ui-cases] input[data-case-id='+caseKey+']');
  const go=page.locator('[data-ui-go-start]'), start=page.locator('[data-ui-start]');
  const chips=page.locator('[data-ui-selected] button');
  const queryFor=app=>'?'+new URLSearchParams({app_id:app,module:'',query:'',scope:'published',limit:'20'});
  const deviceQuery=app=>'?'+new URLSearchParams({app_id:app});
  async function catalogAfter(app,action) {
    const query=queryFor(app);catalogQueries.add(query);
    // The same URL and count can recur on a refresh or handoff. The catalog
    // replaces its rows on every render, including an unchanged revision.
    const host=page.locator('[data-ui-cases]');
    const oldRow=await host.getAttribute('data-ui-catalog-query')===query.slice(1)
      ? await host.locator('[data-case-id]').first().elementHandle({timeout:3000}):null;
    const [response]=await Promise.all([
      page.waitForResponse(r=>r.url()==='http://127.0.0.1:8088/api/ui-executions/catalog'+query,{timeout:6000}),
      action(),
    ]);
    const catalog=state.catalogs.get(query);
    if (response.status()!==200 || !catalog?.ok || !Array.isArray(catalog.items) || !Number.isSafeInteger(catalog.total) || catalog.total<0) throw Error('catalog_unavailable');
    if(oldRow){
      try { await page.waitForFunction(row=>!row.isConnected,oldRow,{timeout:3000}); }
      finally { await oldRow.dispose(); }
    }
    await page.waitForFunction(q=>document.querySelector('[data-ui-cases]')?.dataset.uiCatalogQuery===q && !document.querySelector('[data-ui-cases]')?.hasAttribute('aria-busy'),query.slice(1),{timeout:5000});
    return catalog;
  }
  const matchesAsset=catalog=>catalog.items.some(i=>i.app_id===asset.app_id && i.case_id===asset.case_id && i.revision===asset.revision && i.execution_eligible===true);
  async function selectedVersion(mode) {
    return await checkbox.isChecked() && await chips.count()===1 && (await chips.first().innerText())===target.name+' ×'
      && await page.locator('[data-ui-selection-count]').innerText()===(mode==='start'?'已选 1 条用例':'已选 1 条基线')
      && await page.locator('[data-ui-selected]').evaluate((node,asset)=>{
        const selected=JSON.parse(node.dataset.signature||'[]');
        return selected.length===1 && selected[0][0]===asset.case_id && selected[0][1]?.revision===asset.revision;
      },asset);
  }
  let catalog=await catalogAfter(target.app,()=>appSelect.selectOption(target.app));
  if (!matchesAsset(catalog)) return blocked('case_version_changed');
  if (await checkbox.count()!==1 || !await checkbox.isEnabled()) return failed;
  await checkbox.check();
  if (!await selectedVersion('library') || !await go.isEnabled()) return failed;
  if (scenario==='ui_case_selection') {
    await chips.first().click();
    if (await checkbox.isChecked() || await chips.count() || !await go.isDisabled() ||
        await page.locator('[data-ui-selection-count]').innerText()!=='已选 0 条基线') return failed;
  } else if (scenario==='ui_case_revision') {
    catalog=await catalogAfter(target.app,()=>page.locator('[data-ui-refresh]').click());
    if (!matchesAsset(catalog)) return blocked('case_version_changed');
    if (!await selectedVersion('library') || !await go.isEnabled()) return failed;
  } else {
    deviceQueries.add(deviceQuery(target.app));
    catalog=await catalogAfter(target.app,()=>go.click());
    if (!matchesAsset(catalog)) return blocked('case_version_changed');
    if (await page.locator('[data-ui-mode="start"] h1').innerText()!=='开始测试' || await appSelect.inputValue()!==target.app ||
        !await selectedVersion('start') || !await start.isDisabled()) return failed;
    if (scenario==='ui_start_switch') {
      deviceQueries.add(deviceQuery(other.app_id));
      catalog=await catalogAfter(other.app_id,()=>appSelect.selectOption(other.app_id));
      evidence.switched_app_id=other.app_id;
      const text=await page.locator('[data-ui-notice]').innerText();
      if (await appSelect.inputValue()!==other.app_id || await chips.count() || await page.locator('[data-case-id]:checked').count() ||
          !await start.isDisabled() || !text.includes('之前选择的用例和手机已清除') ||
          await page.locator('[data-ui-selection-count]').innerText()!=='已选 0 条用例') return failed;
    } else if (scenario==='ui_start_unready') {
      await page.waitForFunction(()=>{
        const hint=document.querySelector('[data-ui-device-hint]')?.textContent||'';
        return /同步连接状态|没有发现手机|读取失败/.test(hint);
      },null,{timeout:6000});
      const devices=state.devices.get(deviceQuery(target.app));
      if (!devices?.length) return blocked('no_devices');
      if (devices.some(d=>d.connection_status==='ready')) return blocked('ready_device_present');
      evidence.device_count=devices.length;
      const radios=page.locator('[data-ui-devices] input[name="ui-device"]');
      if (await radios.count()!==devices.length || await page.locator('[name="ui-device"]:enabled').count() ||
          await page.locator('[name="ui-device"]:checked').count() || !await start.isDisabled() ||
          await page.locator('[data-ui-start-hint]').innerText()!=='请选择一台连接就绪的手机。') return failed;
    }
  }
  return {status:'passed',error_kind:null,phase:'workflow',asset,total:catalog.total,...evidence};
}
module.exports={scenarios,browserData,checkWorkflow};
