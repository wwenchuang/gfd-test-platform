'use strict';

// A closed, versioned list: no user-provided target, route, query or method.
const staticPaths = new Set([
  '/task-manager.html', '/assets/brand/kongfudou-icon.png', '/assets/icons/flask-conical.svg',
  '/js/ui-simple-entry.1514fe455649371c.js', '/js/ui-simple-entry.d6214cd9458c31ef.js',
  '/css/ui-case-draft.98c46b5734c85379.css',
  ...['operation-history','app','round5','ui-simple','platform-workbench','ui-workbench','ui-case-draft'].map(name=>`/css/${name}.css`),
  ...['utils','error-handler','empty-states','loading-manager','form-steps','state','auth','identity-management','operation-history','navigation','agent-workbench','agent-status','device-recorder','cases','execution','reports','ai-repair','model-config','yaml-editor','pending-actions','ui-execution-replay','ui-batch-view','ui-batch-report','ui-test-home','ui-case-draft','ui-simple-entry','app'].map(name=>`/js/${name}.js`),
]);
const apiPaths = new Set(['/api/auth/me', '/api/ui-executions/availability']);

function allowRequest(origin, method, input) {
  if (origin !== 'http://127.0.0.1:8088' || method !== 'GET') return false;
  let url; try { url = new URL(input); } catch (_) { return false; }
  if (url.origin !== origin || url.username || url.password || url.hash || url.pathname.includes('%')) return false;
  if (url.pathname === '/api/ui-executions/catalog') return url.search === '?app_id=&module=&query=&scope=published&limit=20' ? 'api' : false;
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

module.exports = {allowRequest, responseContainsSecret, classifyIncomplete, recordRouteError};
