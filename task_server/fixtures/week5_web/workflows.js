'use strict';
const crypto = require('node:crypto');
const {catalogTarget} = require('./policy');

const scenarios = new Set(['ui_case_selection', 'ui_case_revision', 'ui_start_handoff', 'ui_start_switch', 'ui_start_unready',
  'ui_catalog_paging', 'ui_catalog_module', 'ui_case_hidden_selection', 'ui_start_empty_guard']);
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
function browserData(path, data, app, scenario) {
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
    // Paging uses the catalog's own cursor, bound to the final visible case.
    // Check its narrow public payload before allowing it into browser evidence.
    const paging={};
    if (scenario==='ui_catalog_paging' && data.next_cursor!=null) {
      const cursor=data.next_cursor;
      if (typeof cursor!=='string' || cursor.length>1024 || !/^[A-Za-z0-9_-]+={0,2}$/.test(cursor)) throw Error('invalid_cursor');
      const decoded=Buffer.from(cursor,'base64url').toString('utf8');
      const fields=JSON.parse(decoded);
      if (!/^\["[a-f0-9]{16}",-?[0-9]{1,20},"/.test(decoded) || !Array.isArray(fields) || fields.length!==3 ||
          fields[2]!==data.items.at(-1)?.case_id || !decoded.endsWith('"]')) throw Error('invalid_cursor');
      paging.next_cursor=cursor;
    }
    return {...pick(data,['ok','total','modules']),...paging,items:data.items.map(row),apps:data.apps.map(app=>pick(app,['app_id','name'])),
      groups:data.groups.map(group=>({...pick(group,['app_id','name','total','has_more']),items:group.items.map(row)}))};
  }
  throw Error('unexpected_api');
}

async function checkWorkflow({scenario,page,state,catalogQueries,deviceQueries, experiment}) {
  // A comparison can replace only these user actions. Both arms use the original
  // setup, live backend responses and business assertions below.
  const act = (locator, instruction, method='click') => experiment
    ? experiment.act(locator,instruction,method) : locator[method]();
  const blocked = detail => ({status:'blocked',error_kind:'precondition',detail});
  const profile=state.auth.profile;
  const catalogOnly=['ui_catalog_paging','ui_catalog_module'].includes(scenario);
  if (!catalogOnly && !profile.is_superuser && !profile.permissions?.includes('ui.execute')) return blocked('execute_permission_required');
  const initial=state.catalog;
  // Prefer an app with a second page over an earlier, smaller app.
  const groups=scenario==='ui_catalog_paging'
    ? [...initial.groups].sort((left,right)=>Number(right.total>20)-Number(left.total>20))
    : initial.groups;
  const target=catalogTarget({...initial,groups});
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
  function queryFor(app,{module='',query='',cursor=''}={}) {
    const params=new URLSearchParams({app_id:app,module,query,scope:'published',limit:'20'});
    if (cursor) params.set('cursor',cursor);
    return '?'+params;
  }
  const deviceQuery=app=>'?'+new URLSearchParams({app_id:app});
  async function catalogAfter(app,action,filters) {
    const query=queryFor(app,filters);catalogQueries.add(query);
    // The same URL and count can recur on a refresh or handoff. The catalog
    // replaces its rows on every render, including an unchanged revision.
    const host=page.locator('[data-ui-cases]');
    const oldRow=await host.getAttribute('data-ui-catalog-query')===query.slice(1)
      ? await host.locator('[data-case-id]').first().elementHandle({timeout:3000}):null;
    const [response]=await Promise.all([
      page.waitForResponse(r=>r.url()==='http://127.0.0.1:8088/api/ui-executions/catalog'+query,{timeout:experiment ? experiment.actionTimeoutMs + 6000 : 6000}),
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
  if (experiment) await experiment.freeze({asset,items:catalog.items.map(i=>[i.case_id,i.revision]),total:catalog.total});
  const passed=()=>({status:'passed',error_kind:null,phase:'workflow',asset,total:catalog.total,...evidence});
  const ids=data=>data.items.map(item=>item.case_id);
  const sameIds=(left,right)=>JSON.stringify(left)===JSON.stringify(right);
  async function rowsMatch(data) {
    const shown=await page.locator('[data-ui-cases] [data-case-id]').evaluateAll(nodes=>nodes.map(node=>node.dataset.caseId));
    return await appSelect.inputValue()===target.app && data.items.every(item=>item.app_id===target.app) &&
      sameIds(shown,ids(data)) && await page.locator('[data-ui-case-count]').innerText()===data.total+' 条匹配基线';
  }
  if (scenario==='ui_catalog_paging') {
    const next=page.locator('[data-ui-next]'), prev=page.locator('[data-ui-prev]');
    const first=ids(catalog), total=catalog.total;
    if (experiment && total<=20) return blocked('no_second_page');
    if (!await rowsMatch(catalog) || !await prev.isDisabled() || first.length!==Math.min(total,20) ||
        new Set(first).size!==first.length || !!catalog.next_cursor!==(total>20) || await next.isEnabled()!==(total>20)) return failed;
    evidence.pages_checked=1;
    if (total>20) {
      catalog=await catalogAfter(target.app,()=>act(next,'用例列表底部的“下一页”按钮'),{cursor:catalog.next_cursor});
      if (catalog.total!==total) return blocked('catalog_changed');
      const second=ids(catalog);
      if (!await rowsMatch(catalog) || !await prev.isEnabled() || second.length!==Math.min(total-20,20) ||
          new Set([...first,...second]).size!==first.length+second.length || !!catalog.next_cursor!==(total>40) ||
          await next.isEnabled()!==(total>40)) return failed;
      catalog=await catalogAfter(target.app,()=>act(prev,'用例列表底部的“上一页”按钮'));
      if (catalog.total!==total || !sameIds(first,ids(catalog)) || !matchesAsset(catalog)) return blocked('catalog_changed');
      if (!await rowsMatch(catalog) || !await next.isEnabled() || !await prev.isDisabled()) return failed;
      evidence.pages_checked=2;
    }
    return passed();
  }
  if (scenario==='ui_catalog_module') {
    const module=catalog.items.find(item=>item.case_id===target.caseId)?.module;
    if (typeof module!=='string'||!module.trim()||module.length>128||/[\x00-\x1f\x7f]/.test(module)||!catalog.modules?.includes(module)) return blocked('no_case_module');
    const select=page.locator('[name="ui-module"]');
    catalog=await catalogAfter(target.app,()=>select.selectOption(module),{module});
    if (!matchesAsset(catalog)) return blocked('case_version_changed');
    if (await select.inputValue()!==module || !await rowsMatch(catalog) || !catalog.items.every(item=>item.module===module)) return failed;
    catalog=await catalogAfter(target.app,()=>select.selectOption(''));
    if (!matchesAsset(catalog)) return blocked('case_version_changed');
    if (await select.inputValue()!=='' || !await rowsMatch(catalog)) return failed;
    return passed();
  }
  if (await checkbox.count()!==1 || !await checkbox.isEnabled()) return failed;
  await act(checkbox,'标题为「'+target.name+'」的基线行左侧复选框，只定位这个方框','check');
  if (!await selectedVersion('library') || !await go.isEnabled()) return failed;
  if (scenario==='ui_case_selection') {
    await act(chips.first(),'已选清单中名称为「'+target.name+'」的已选标签，点击该标签上的移除符号 ×');
    if (await checkbox.isChecked() || await chips.count() || !await go.isDisabled() ||
        await page.locator('[data-ui-selection-count]').innerText()!=='已选 0 条基线') return failed;
  } else if (scenario==='ui_case_hidden_selection') {
    const search=page.locator('[name="ui-query"]');
    const missing='__web_check_missing_'+crypto.randomBytes(16).toString('hex');
    catalog=await catalogAfter(target.app,async()=>{
      await search.fill(missing);
      await search.press('Enter');
    },{query:missing});
    if (catalog.total!==0) return blocked('catalog_changed');
    if (!await rowsMatch(catalog) || await checkbox.count() || await chips.count()!==1 ||
        await chips.first().innerText()!==target.name+' ×' || !await go.isEnabled() ||
        await page.locator('[data-ui-selection-count]').innerText()!=='已选 1 条基线') return failed;
    catalog=await catalogAfter(target.app,()=>search.fill(''));
    if (!matchesAsset(catalog)) return blocked('case_version_changed');
    if (!await rowsMatch(catalog) || !await selectedVersion('library')) return failed;
  } else if (scenario==='ui_case_revision') {
    catalog=await catalogAfter(target.app,()=>act(page.locator('[data-ui-refresh]'),'用例库标题行右侧的“刷新”按钮'));
    if (!matchesAsset(catalog)) return blocked('case_version_changed');
    if (!await selectedVersion('library') || !await go.isEnabled()) return failed;
  } else {
    deviceQueries.add(deviceQuery(target.app));
    catalog=await catalogAfter(target.app,()=>act(go,'已选清单区域的“去开始测试 →”按钮，只打开交接页面'));
    if (!matchesAsset(catalog)) return blocked('case_version_changed');
    if (await page.locator('[data-ui-mode="start"] h1').innerText()!=='开始测试' || await appSelect.inputValue()!==target.app ||
        !await selectedVersion('start') || !await start.isDisabled()) return failed;
    if (scenario==='ui_start_empty_guard') {
      await act(chips.first(),'已选清单中名称为「'+target.name+'」的已选标签，点击该标签上的移除符号 ×');
      if (await checkbox.isChecked() || await chips.count() || !await start.isDisabled() ||
          await page.locator('[data-ui-selection-count]').innerText()!=='已选 0 条用例' ||
          await page.locator('[data-ui-start-hint]').innerText()!=='勾选至少一条已发布基线。') return failed;
    } else if (scenario==='ui_start_switch') {
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
  return passed();
}
module.exports={scenarios,browserData,checkWorkflow};
