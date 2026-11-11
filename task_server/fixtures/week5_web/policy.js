'use strict';

// A closed, versioned list: no user-provided target, route, query or method.
const staticPaths = new Set([
  '/js/ui-practice-results.161ad13198453172.js',
  '/js/ui-practice-results.0c08c8345fdbbc22.js',
  '/js/ui-simple-entry.a5a4cffebf79000d.js',
  '/js/ui-practice-results.cd1a9970aa904f16.js',
  '/js/ui-simple-entry.646bd2bdf3e59095.js',
  '/task-manager.html', '/assets/brand/kongfudou-icon.png', '/assets/icons/flask-conical.svg',
  '/js/ui-simple-entry.1514fe455649371c.js', '/js/ui-simple-entry.d6214cd9458c31ef.js',
  '/js/ui-simple-entry.cc3d421d522c3502.js', '/js/ui-test-home.550ea3ea79d3dd5a.js',
  '/js/ui-simple-entry.5544fabaad139990.js', '/js/ui-test-home.c7eaa641cdc4556b.js',
  '/js/ui-simple-entry.c102fcaa6d0c5610.js', '/js/ui-simple-entry.6f02212d46c421f4.js',
  '/js/ui-simple-entry.bf1f935375e4b048.js',
  '/js/ui-simple-entry.4066b05157b7ed35.js', '/js/ui-simple-entry.4e7bbb6db84a5b84.js',
  '/css/ui-case-draft.98c46b5734c85379.css',
  ...['operation-history','app','round5','ui-simple','platform-workbench','ui-workbench','ui-case-draft'].map(name=>`/css/${name}.css`),
  ...['utils','error-handler','empty-states','loading-manager','form-steps','state','auth','identity-management','operation-history','navigation','agent-workbench','agent-status','device-recorder','cases','execution','reports','ai-repair','model-config','yaml-editor','pending-actions','ui-execution-replay','ui-batch-view','ui-batch-report','ui-test-home','ui-case-draft','ui-practice-results','ui-simple-entry','app'].map(name=>`/js/${name}.js`),
]);
const apiPaths = new Set(['/api/auth/me', '/api/ui-executions/availability']);

function allowRequest(origin, method, input, catalogQueries = new Set(), deviceQueries = new Set()) {
  if (origin !== 'http://127.0.0.1:8088' || method !== 'GET') return false;
  let url; try { url = new URL(input); } catch (_) { return false; }
  if (url.origin !== origin || url.username || url.password || url.hash || url.pathname.includes('%')) return false;
  if (url.pathname === '/api/ui-executions/catalog') return url.search === '?app_id=&module=&query=&scope=published&limit=20' || catalogQueries.has(url.search) ? 'api' : false;
  if (url.pathname === '/api/ui-executions/devices') return deviceQueries.has(url.search) ? 'api' : false;
  if (apiPaths.has(url.pathname)) return url.search ? false : 'api';
  if (!staticPaths.has(url.pathname)) return false;
  if (url.pathname === '/task-manager.html' && url.search) return false;
  if (url.search && !/^\?v=[a-zA-Z0-9_-]{1,512}$/.test(url.search)) return false;
  return 'static';
}

function responseContainsSecret(content, secret) {
  if (!secret || !Buffer.isBuffer(content)) return true;
  const bytes = Buffer.from(secret);
  const variants = [bytes, Buffer.from(encodeURIComponent(secret)), Buffer.from(bytes.toString('base64')),
    Buffer.from(bytes.toString('base64url')), Buffer.from(bytes.toString('hex')), Buffer.from(secret, 'utf16le')];
  return variants.some(value => content.includes(value));
}

function classifyIncomplete(state, userId) {
  if (state.routeError === 'authentication') return {status:'blocked',error_kind:'authentication'};
  if (!state.routeError && state.auth?.ok && state.auth?.profile?.user_id === userId
      && state.availability?.ok && state.availability.enabled === true && state.catalog?.ok
      && Number.isSafeInteger(state.catalog.total) && state.catalog.total >= 0) {
    return {status:'failed',error_kind:'business'};
  }
  return {status:'blocked',error_kind:'environment'};
}

function recordRouteError(state, kind) {
  if (kind === 'authentication') state.routeError = 'authentication';
  else if (!state.routeError) state.routeError = true;
}

function catalogTarget(data) {
  if (!Array.isArray(data?.apps) || !Array.isArray(data?.groups)) return null;
  const apps=new Set(data.apps.map(app=>app?.app_id).filter(id=>typeof id==='string'&&id.length>0&&id.length<=128&&!/[\x00-\x1f\x7f]/.test(id)));
  for (const group of data.groups) {
    if(!apps.has(group?.app_id)||!Array.isArray(group.items))continue;
    for(const item of group.items){
      if(item?.app_id===group.app_id&&item.execution_eligible===true&&typeof item.case_id==='string'&&item.case_id.length>0&&item.case_id.length<=512&&/^[a-f0-9]{64}$/.test(item.revision||'')
         &&typeof item.task_name==='string'&&item.task_name.trim().length>0&&item.task_name.trim().length<=200&&!/[\x00-\x1f\x7f]/.test(item.task_name))
        return {app:group.app_id,caseId:item.case_id,name:item.task_name.trim(),revision:item.revision};
    }
  }
  return null;
}

module.exports = {allowRequest, responseContainsSecret, classifyIncomplete, recordRouteError, catalogTarget};
