'use strict';
// The browser receives a placeholder. The user's real session is confined to
// this short-lived process and the exact loopback GET routes in policy.js.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {chromium} = require('playwright');
const {allowRequest, classifyIncomplete, recordRouteError, catalogTarget} = require('./policy');
const {readLoopback} = require('./proxy');

async function main() {
  const input = fs.readFileSync(0, 'utf8');
  if (Buffer.byteLength(input) > 4096) throw Error('input_invalid');
  const payload = JSON.parse(input);
  if (!payload || typeof payload.token !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(payload.token)
      || typeof payload.userId !== 'string' || !payload.userId || payload.userId.length > 128) throw Error('input_invalid');
  const scenario=payload.scenario||'ui_catalog_count';
  if (!['ui_catalog_count','ui_catalog_filter','ui_catalog_empty'].includes(scenario)) throw Error('input_invalid');
  let secret = payload.token; payload.token = '';
  const origin = 'http://127.0.0.1:8088';
  const output = path.resolve(process.env.WEEK5_ARTIFACTS || '');
  if (!process.env.WEEK5_ARTIFACTS || !path.isAbsolute(process.env.WEEK5_ARTIFACTS)) throw Error('output_invalid');
  const result = {scenario,version:1,phase:'initial',status:'blocked',error_kind:'environment'};
  const state = {auth:null, availability:null, catalog:null, catalogs:new Map(), routeError:false, staticCount:0,writeAttempts:0};
  const catalogQueries=new Set();
  let browser, context, page, tracing = false;
  let awaitingAssertion = false;
  try {
    result.stage='browser';
    browser = await chromium.launch({headless:true, args:['--disable-background-networking','--disable-dev-shm-usage']});
    result.stage='context';
    context = await browser.newContext({serviceWorkers:'block'});
    if (typeof context.routeWebSocket !== 'function') throw Error('websocket_guard_unavailable');
    await context.routeWebSocket('**/*', socket => socket.close());
    await context.route('**/*', async route => {
      const request = route.request();
      const kind = allowRequest(origin, request.method(), request.url(),catalogQueries);
      if (!kind) {const url=new URL(request.url());if(request.method()!=='GET'&&url.origin===origin&&url.pathname.startsWith('/api/'))state.writeAttempts++;await route.abort(); return; }
      try {
        const url = new URL(request.url());
        const response = await readLoopback(url.pathname + url.search, secret,8088,catalogQueries);
        if (kind === 'static') state.staticCount++;
        if (kind === 'api') {
          if ([401,403].includes(response.status)) recordRouteError(state, 'authentication');
          else if (response.status !== 200) recordRouteError(state, 'environment');
          else {
            const data = JSON.parse(response.body.toString('utf8'));
            if (url.pathname === '/api/auth/me') state.auth = data;
            if (url.pathname === '/api/ui-executions/availability') state.availability = data;
            if (url.pathname === '/api/ui-executions/catalog') {state.catalog = data;state.catalogs.set(url.search,data);}
          }
        }
        await route.fulfill({status:response.status, contentType:response.contentType, body:response.body, headers:{'Cache-Control':'no-store'}});
      } catch (_) { recordRouteError(state, 'environment'); await route.abort().catch(()=>{}); }
    });
    await context.addInitScript(() => {
      if (window.location.origin !== 'http://127.0.0.1:8088') return;
      sessionStorage.setItem('sessionToken', 'invalid-browser-placeholder');
      sessionStorage.setItem('midscene_active_workflow', 'ui_library');
    });
    result.stage='trace';
    await context.tracing.start({screenshots:true,snapshots:true}); tracing = true;
    page = await context.newPage();
    result.stage='page';
    await page.goto(`${origin}/task-manager.html`, {waitUntil:'domcontentloaded', timeout:15000});
    awaitingAssertion = true;
    await page.locator('[data-ui-case-count]').waitFor({state:'visible', timeout:15000});
    await page.waitForFunction(() => (document.querySelector('[data-ui-case-count]')?.textContent || '').includes('条匹配'), null, {timeout:10000});
    if (!state.auth?.ok || state.auth?.profile?.user_id !== payload.userId) result.error_kind = 'authentication';
    else if (!state.availability?.ok || state.availability.enabled !== true || !state.catalog?.ok || !Number.isSafeInteger(state.catalog.total) || state.catalog.total < 0) result.error_kind = 'environment';
    else {
      const count = (await page.locator('[data-ui-case-count]').innerText()).trim();
      const title = (await page.locator('[data-ui-mode="library"] h1').innerText()).trim();
      if (title !== '用例库' || count !== `${state.catalog.total} 条匹配基线 · 每个应用先显示 3 条`) {
        result.status='failed';result.error_kind='business';
      } else if(scenario==='ui_catalog_count'){
        result.status='passed';result.error_kind=null;result.total=state.catalog.total;
      } else {
        const params=(app,query)=>'?'+new URLSearchParams({app_id:app,module:'',query,scope:'published',limit:'20'}).toString();
        const waitCatalog=query=>page.waitForResponse(response=>{const url=new URL(response.url());return url.origin===origin&&url.pathname==='/api/ui-executions/catalog'&&url.search===query;},{timeout:15000});
        const target=scenario==='ui_catalog_filter'?catalogTarget(state.catalog):null;
        if(scenario==='ui_catalog_filter'&&!target){result.error_kind='environment';}
        else {
          if(target){result.phase='filter';result.asset={app_id:target.app,case_id:target.caseId,revision:target.revision};}else result.phase='empty';
          const app=target?.app||'', query=target?target.name:'__web_check_missing_'+crypto.randomBytes(16).toString('hex');
          const finalQuery=params(app,query);
          if(app){const initial=params(app,'');catalogQueries.add(initial);await Promise.all([waitCatalog(initial),page.locator('[name="ui-app"]').selectOption(app)]);}
          catalogQueries.add(finalQuery);
          await page.locator('[name="ui-query"]').fill(query);
          await Promise.all([waitCatalog(finalQuery),page.locator('[name="ui-query"]').press('Enter')]);
          const catalog=state.catalogs.get(finalQuery);
          if(!catalog?.ok||!Number.isSafeInteger(catalog.total)||catalog.total<0||!Array.isArray(catalog.items)||!Array.isArray(catalog.groups))throw Error('catalog_invalid');
          if(target&&!catalog.items.some(item=>item.case_id===target.caseId&&item.app_id===app&&item.revision===target.revision)){result.error_kind='environment';}
          else if(!target&&catalog.total!==0){result.error_kind='environment';}
          else {
            const expectedText=`${catalog.total} 条匹配基线${app?'':' · 每个应用先显示 3 条'}`;
            await page.waitForFunction(({text,query})=>document.querySelector('[data-ui-case-count]')?.textContent===text
              &&document.querySelector('[data-ui-cases]')?.dataset.uiCatalogQuery===query,{text:expectedText,query:finalQuery.slice(1)},{timeout:10000});
            const shown=await page.locator('[data-ui-cases] [data-case-id]').evaluateAll(nodes=>nodes.map(node=>node.dataset.caseId));
            const expected=target?catalog.items.map(item=>item.case_id):[];
            const emptyText=target||await page.locator('[data-ui-cases]').innerText();
            const ok=JSON.stringify(shown)===JSON.stringify(expected)&&(!target?emptyText.includes('没有匹配的已发布基线'):shown.includes(target.caseId))&&!state.writeAttempts&&!state.routeError;
            result.status=ok?'passed':'failed';result.error_kind=ok?null:'business';result.total=catalog.total;
          }
        }
      }
    }
  } catch (error) {
    if (state.routeError === 'authentication' || (awaitingAssertion && error?.name === 'TimeoutError')) {
      Object.assign(result, classifyIncomplete(state, payload.userId));
    }
  } finally {
    result.observed={auth:!!state.auth,availability:!!state.availability,catalog:!!state.catalog,static_count:state.staticCount,route_error:!!state.routeError};
    try { if (page) await page.screenshot({path:path.join(output,'screenshot.png')}); else throw Error(); }
    catch (_) {result.status='blocked';result.error_kind='evidence';}
    try {if (context && tracing) await context.tracing.stop({path:path.join(output,'trace.zip')});else throw Error();}
    catch (_) {result.status='blocked';result.error_kind='evidence';}
    if (context) await context.close().catch(()=>{});
    if (browser) await browser.close().catch(()=>{});
    secret='';
  }
  process.stdout.write(JSON.stringify(result));
}
main().catch(()=>{process.stdout.write(JSON.stringify({scenario:'ui_catalog_count',version:1,status:'blocked',error_kind:'environment'}));process.exitCode=1;});
