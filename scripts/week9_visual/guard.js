'use strict';

const crypto = require('node:crypto');
const {allowRequest, recordRouteError} = require('../../task_server/fixtures/week5_web/policy');
const {readLoopback} = require('../../task_server/fixtures/week5_web/proxy');
const {browserData} = require('../../task_server/fixtures/week5_web/workflows');

const origin = 'http://127.0.0.1:8088';
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const mime = {'.html':['text/html'],'.css':['text/css'],'.js':['text/javascript','application/javascript']};
async function openPage(context, credentials, scenario) {
  const catalogQueries = new Set(), deviceQueries = new Set();
  const state = {auth:null, availability:null, catalog:null, catalogs:new Map(), devices:new Map(), writes:0, hashes:new Map()};
  await context.routeWebSocket('**/*', socket => socket.close());
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    const kind = allowRequest(origin, request.method(), url.href, catalogQueries, deviceQueries);
    if (!kind) {
      if (url.origin===origin && url.pathname.startsWith('/api/') && request.method()!=='GET') state.writes++;
      await route.abort().catch(()=>{});
      return;
    }
    try {
      const res = await readLoopback(url.pathname+url.search, credentials.token, 8088, catalogQueries, deviceQueries);
      if (kind==='static') {
        const expected = mime[url.pathname.match(/\.[a-z]+$/)?.[0]] || ['image/png','image/svg+xml','application/octet-stream'];
        if (res.status!==200 || !expected.includes(res.contentType)) throw Error('static_response');
        state.hashes.set(url.pathname,digest(res.body));
      } else {
        if (res.status!==200) {
          if ([401,403].includes(res.status)) recordRouteError(state,'authentication');
          throw Error('api_response');
        }
        const data = JSON.parse(res.body.toString('utf8'));
        if (data?.ok!==true) throw Error('api_payload');
        const projected = browserData(url.pathname,data,url.searchParams.get('app_id'),scenario);
        if (url.pathname==='/api/auth/me') {
          const profile=data.profile;
          if (profile?.user_id!==credentials.userId || profile.status!=='active' || profile.must_change_password ||
              !(profile.is_superuser===true || profile.permissions?.includes('ui.view'))) {
            recordRouteError(state,'authentication');
            throw Error('owner');
          }
          state.auth=data;
        } else if (url.pathname.endsWith('/availability')) state.availability=data;
        else if (url.pathname.endsWith('/devices')) state.devices.set(url.search,projected.devices);
        else {state.catalog=data;state.catalogs.set(url.search,data);}
        res.body=Buffer.from(JSON.stringify(projected));res.contentType='application/json';
      }
      await route.fulfill({status:200,contentType:res.contentType,body:res.body,headers:{'Cache-Control':'no-store'}});
    } catch (_) {
      recordRouteError(state,'environment');
      await route.abort().catch(()=>{});
    }
  });
  await context.addInitScript(() => {
    if (location.origin==='http://127.0.0.1:8088') {
      sessionStorage.setItem('sessionToken','invalid-browser-placeholder');
      sessionStorage.setItem('midscene_active_workflow','ui_library');
    }
  });
  const page=await context.newPage();
  await page.goto(origin+'/task-manager.html',{waitUntil:'domcontentloaded',timeout:15000});
  try {
    await page.waitForFunction(()=>document.querySelector('[data-ui-case-count]')?.textContent.includes('条匹配'),null,{timeout:12000});
  } catch (error) {
    if (state.routeError==='authentication') throw Error('authentication');
    throw Error('page_unavailable');
  }
  if (!state.auth || !state.catalog || !state.availability?.enabled || state.writes || state.routeError) throw Error('unsafe_or_unavailable');
  const expected=state.catalog.total+' 条匹配基线 · 每个应用先显示 3 条';
  if (!Number.isSafeInteger(state.catalog.total) || await page.locator('[data-ui-case-count]').innerText()!==expected ||
      await page.locator('[data-ui-mode="library"] h1').innerText()!=='用例库') throw Error('baseline_assertion');
  return {state,page,catalogQueries,deviceQueries};
}
module.exports={openPage,digest,origin};
