'use strict';
// The browser receives a placeholder. The user's real session is confined to
// this short-lived process and the exact loopback GET routes in policy.js.
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {allowRequest, classifyIncomplete, recordRouteError} = require('./policy');
const {readLoopback} = require('./proxy');

async function main() {
  const input = fs.readFileSync(0, 'utf8');
  if (Buffer.byteLength(input) > 4096) throw Error('input_invalid');
  const payload = JSON.parse(input);
  if (!payload || typeof payload.token !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(payload.token)
      || typeof payload.userId !== 'string' || !payload.userId || payload.userId.length > 128) throw Error('input_invalid');
  let secret = payload.token; payload.token = '';
  const origin = 'http://127.0.0.1:8088';
  const output = path.resolve(process.env.WEEK5_ARTIFACTS || '');
  if (!process.env.WEEK5_ARTIFACTS || !path.isAbsolute(process.env.WEEK5_ARTIFACTS)) throw Error('output_invalid');
  const result = {scenario:'ui_catalog_count',version:1,status:'blocked',error_kind:'environment'};
  const state = {auth:null, availability:null, catalog:null, routeError:false, staticCount:0};
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
      const kind = allowRequest(origin, request.method(), request.url());
      if (!kind) { await route.abort(); return; }
      try {
        const url = new URL(request.url());
        const response = await readLoopback(url.pathname + url.search, secret);
        if (kind === 'static') state.staticCount++;
        if (kind === 'api') {
          if ([401,403].includes(response.status)) recordRouteError(state, 'authentication');
          else if (response.status !== 200) recordRouteError(state, 'environment');
          else {
            const data = JSON.parse(response.body.toString('utf8'));
            if (url.pathname === '/api/auth/me') state.auth = data;
            if (url.pathname === '/api/ui-executions/availability') state.availability = data;
            if (url.pathname === '/api/ui-executions/catalog') state.catalog = data;
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
      if (title === '用例库' && count === `${state.catalog.total} 条匹配基线 · 每个应用先显示 3 条`) {
        result.status='passed';result.error_kind=null;result.total=state.catalog.total;
      } else {result.status='failed';result.error_kind='business';}
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
