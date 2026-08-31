import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createGatewayAuth} from '../ai-gateway/gateway-auth.js';

const PROFILE = {is_superuser: false, permissions: ['ui.view'], must_change_password: false};
const BASE_URL = 'http://127.0.0.1:8091';
const SESSION = 'gateway-auth-unit-fixture';

function response(profile = PROFILE, status = 200) {
  return {status, ok: status === 200, json: async () => ({ok: true, ...profile})};
}

async function invoke(middleware, overrides = {}) {
  const req = {
    method: 'GET', path: '/ai/model-router',
    headers: {authorization: `Bearer ${SESSION}`},
    socket: {remoteAddress: '127.0.0.1'},
    ...overrides,
  };
  const result = {status: 200, next: false, headers: {}};
  const res = {
    status(code) { result.status = code; return this; },
    set(name, value) { result.headers[name.toLowerCase()] = value; return this; },
    json(body) { result.body = body; return this; },
  };
  await middleware(req, res, () => { result.next = true; });
  return result;
}

function authFor(profile, options = {}) {
  return createGatewayAuth({baseUrl: BASE_URL, fetchImpl: async () => response(profile), ...options});
}

test('only socket loopback without external marker headers receives internal access', async () => {
  let calls = 0;
  const middleware = createGatewayAuth({baseUrl: BASE_URL, fetchImpl: async () => {
    calls += 1;
    throw new Error('unavailable');
  }});
  for (const remoteAddress of ['127.0.0.1', '127.12.34.56', '::1', '::ffff:127.0.0.1']) {
    assert.equal((await invoke(middleware, {
      path: '/unclassified', headers: {'content-type': 'application/json'}, socket: {remoteAddress},
    })).next, true);
  }
  for (const remoteAddress of ['10.0.0.1', '192.168.1.2', '::ffff:10.0.0.1', undefined]) {
    const result = await invoke(middleware, {headers: {}, socket: {remoteAddress}, ip: '127.0.0.1'});
    assert.equal(result.status, 401);
    assert.equal(result.next, false);
  }
  for (const name of ['origin', 'forwarded', 'x-forwarded-for', 'authorization',
    'x-real-ip', 'x-forwarded-host', 'x-forwarded-proto', 'sec-fetch-site', 'referer']) {
    for (const value of ['', '127.0.0.1']) {
      const result = await invoke(middleware, {headers: {[name]: value}});
      assert.equal(result.status, 401, name);
      assert.equal(result.next, false);
    }
  }
  assert.equal(calls, 0, 'anonymous requests must not call /me');
});

test('Bearer is the sole credential source and reaches only the fixed /me endpoint', async () => {
  const calls = [];
  const middleware = createGatewayAuth({baseUrl: BASE_URL, fetchImpl: async (...args) => {
    calls.push(args);
    return response();
  }});
  for (const authorization of ['', 'Basic fixture', 'Bearer ', 'Bearer a b', 'Bearer a,Bearer b',
    ['Bearer a', 'Bearer b']]) {
    assert.equal((await invoke(middleware, {headers: {authorization}})).status, 401);
  }
  assert.equal((await invoke(middleware, {
    headers: {origin: '', cookie: `token=${SESSION}`, 'x-token': SESSION},
    query: {token: SESSION, access_token: SESSION},
  })).status, 401);
  const allowed = await invoke(middleware, {headers: {
    authorization: `bEaReR ${SESSION}`, origin: 'https://platform.example.test', cookie: 'private',
  }});
  assert.equal(allowed.next, true);
  assert.equal(calls.length, 1);
  const [url, options] = calls[0];
  assert.equal(String(url), `${BASE_URL}/api/auth/me`);
  assert.equal(options.method, 'GET');
  assert.deepEqual(options.headers, {Authorization: `Bearer ${SESSION}`, Accept: 'application/json'});
  assert.equal(options.redirect, 'error', 'never follow redirects with credentials');
  assert.equal(options.cache, 'no-store');
  assert.ok(options.signal instanceof AbortSignal);
  assert.equal(allowed.headers['cache-control'], 'no-store');
});

const READS = ['/ai/providers', '/ai/model-router'];
const GENERATION = ['validate-yaml', 'generate-yaml', 'generate-case', 'skill', 'analyze-failure',
  'optimize-yaml', 'chat', 'api-case-generation', 'generate-bug'].map((name) => `/ai/${name}`);
const EXECUTION = ['/agent/run', '/agent/runs/fixture/confirm', '/agent/runs/fixture/cancel'];

test('route matrix requires exact permissions and defaults unknown routes to superuser only', async () => {
  const routes = [
    ...READS.flatMap((route) => ['GET', 'HEAD'].map((method) => [method, route, ['ui.view', 'api.view']])),
    ...GENERATION.map((route) => ['POST', route, ['ui.edit', 'api.edit']]),
    ...EXECUTION.map((route) => ['POST', route, []]),
    ['POST', '/ai/model-router', ['platform.configure']],
    ['POST', '/ai/providers/test', ['platform.configure']],
    ['GET', '/agent/runs', []],
    ['HEAD', '/agent/runs/fixture', []],
    ['GET', '/unknown', []], ['POST', '/ai/providers', []], ['GET', '/ai/chat', []],
    ['POST', '/ai/new-generation-route', []], ['POST', '/ai/chat/extra', []],
  ];
  for (const permission of ['ui.view', 'api.view', 'ui.edit', 'api.edit', 'ui.execute',
    'api.execute', 'platform.configure', '*']) {
    const middleware = authFor({...PROFILE, permissions: [permission]});
    for (const [method, route, allowed] of routes) {
      const result = await invoke(middleware, {method, path: route});
      assert.equal(result.next, allowed.includes(permission), `${permission}: ${method} ${route}`);
      if (!result.next) assert.equal(result.status, 403);
    }
  }
  for (const [method, route] of routes) {
    assert.equal((await invoke(authFor({...PROFILE, is_superuser: true, permissions: []}), {
      method, path: route,
    })).next, true, `superuser: ${method} ${route}`);
  }
});

test('Agent listing and details never accept caller supplied app scope as proof', async () => {
  for (const path of ['/agent/runs', '/agent/runs/private-run']) {
    const result = await invoke(authFor({...PROFILE, permissions: ['ui.view', 'ui.execute']}), {
      path, query: {appId: 'forged'}, body: {appId: 'forged'},
    });
    assert.equal(result.status, 403);
  }
});

test('all Agent routes require configure AND execute AND all UI apps for non-superusers', async () => {
  const routes = [
    ['GET', '/agent/runs'], ['GET', '/agent/runs/fixture'], ['HEAD', '/agent/runs/fixture'],
    ...EXECUTION.map((route) => ['POST', route]),
  ];
  for (const [permissions, scope, allowed] of [
    [['ui.execute'], {ui_apps: '*'}, false],
    [['platform.configure'], {ui_apps: '*'}, false],
    [['platform.configure', 'ui.execute'], undefined, false],
    [['platform.configure', 'ui.execute'], {ui_apps: []}, false],
    [['platform.configure', 'ui.execute'], {ui_apps: ['app-a']}, false],
    [['platform.configure', 'ui.execute'], {ui_apps: ['*']}, false],
    [['platform.configure', 'ui.execute'], {ui_apps: '*'}, true],
  ]) {
    const middleware = authFor({...PROFILE, permissions, scope});
    for (const [method, path] of routes) {
      const result = await invoke(middleware, {method, path, query: {appId: 'app-a'}});
      assert.equal(result.next, allowed, `${method} ${path}: ${JSON.stringify({permissions, scope})}`);
      if (!allowed) assert.equal(result.status, 403);
    }
    assert.equal((await invoke(middleware, {path: '/agent/new-global-route'})).status, 403);
  }
});

test('Express case-insensitive paths, trailing slashes and HEAD do not bypass checks', async () => {
  for (const route of ['/AI/MODEL-ROUTER', '/ai/model-router/', '/ai/model-router//', '/ai/model-router/extra']) {
    assert.equal((await invoke(authFor({...PROFILE, permissions: []}), {path: route, method: 'HEAD'})).status, 403);
  }
  for (const route of ['/AI/MODEL-ROUTER', '/ai/model-router/']) {
    assert.equal((await invoke(authFor(PROFILE), {path: route, method: 'HEAD'})).next, true);
  }
});

test('each request revalidates revocation and role edits without a session cache', async () => {
  let current = response();
  let calls = 0;
  const middleware = createGatewayAuth({baseUrl: BASE_URL, fetchImpl: async () => { calls += 1; return current; }});
  assert.equal((await invoke(middleware)).next, true);
  current = response({...PROFILE, permissions: []});
  assert.equal((await invoke(middleware)).status, 403);
  current = response(PROFILE, 401);
  assert.equal((await invoke(middleware)).status, 401);
  current = response(PROFILE, 403);
  assert.equal((await invoke(middleware)).status, 403);
  assert.equal(calls, 4);
});

test('must-change users including superusers are blocked before permission checks', async () => {
  for (const is_superuser of [false, true]) {
    const result = await invoke(authFor({...PROFILE, is_superuser, must_change_password: true}));
    assert.equal(result.status, 403);
    assert.equal(result.body.code, 'password_change_required');
    assert.equal(result.next, false);
  }
});

test('malformed or legacy /me profiles never gain access from truthy fields', async () => {
  for (const payload of [
    null, [], {}, {ok: true, user: 'legacy-admin'}, {ok: false, ...PROFILE},
    {ok: true, ...PROFILE, is_superuser: 'true'},
    {ok: true, ...PROFILE, must_change_password: 'false'},
    {ok: true, ...PROFILE, permissions: 'ui.view'},
    {ok: true, ...PROFILE, permissions: [null]},
  ]) {
    const middleware = createGatewayAuth({baseUrl: BASE_URL, fetchImpl: async () => ({
      status: 200, ok: true, json: async () => payload,
    })});
    const result = await invoke(middleware);
    assert.equal(result.status, 503);
    assert.equal(result.next, false);
  }
});

test('a profile envelope from /me preserves the same strict permission contract', async () => {
  const middleware = createGatewayAuth({baseUrl: BASE_URL, fetchImpl: async () => ({
    status: 200, json: async () => ({ok: true, profile: PROFILE}),
  })});
  assert.equal((await invoke(middleware)).next, true);
});

test('backend exceptions, redirects, bad JSON and server errors are sanitized and fail closed', async () => {
  for (const fetchImpl of [
    async () => { throw new Error(`unavailable ${SESSION}`); },
    async () => response(PROFILE, 302),
    async () => response(PROFILE, 500),
    async () => ({status: 200, ok: true, json: async () => { throw new Error(`bad json ${SESSION}`); }}),
  ]) {
    const result = await invoke(createGatewayAuth({baseUrl: BASE_URL, fetchImpl}));
    assert.equal(result.status, 503);
    assert.equal(result.next, false);
    assert.equal(JSON.stringify(result).includes(SESSION), false);
  }
});

test('timeouts bound both /me fetch and body parsing even when a mock ignores abort', async () => {
  for (const hangBody of [false, true]) {
    let signal;
    const never = new Promise(() => {});
    const middleware = createGatewayAuth({baseUrl: BASE_URL, timeoutMs: 100, fetchImpl: async (_url, options) => {
      signal = options.signal;
      return hangBody ? {status: 200, ok: true, json: () => never} : never;
    }});
    const started = Date.now();
    const result = await invoke(middleware);
    assert.equal(result.status, 503);
    assert.equal(result.next, false);
    assert.equal(signal.aborted, true);
    assert.ok(Date.now() - started < 1000);
  }
});

test('invalid auth base URLs fail at initialization without echoing credentials', () => {
  for (const baseUrl of ['not-a-url', 'http://example.test', 'http://10.0.0.1:8091',
    `http://name:${SESSION}@127.0.0.1:8091`, `http://127.0.0.1:8091?token=${SESSION}`,
    'http://127.0.0.1:8091#fragment', 'http://127.0.0.1:8091/prefix', 'file:///api/auth/me']) {
    assert.throws(() => createGatewayAuth({baseUrl}), (error) => !error.message.includes(SESSION));
  }
});
