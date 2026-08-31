import assert from 'node:assert/strict';
import {execFile, spawn} from 'node:child_process';
import {once} from 'node:events';
import fs from 'node:fs/promises';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {after, before, test} from 'node:test';
import {fileURLToPath} from 'node:url';
import {promisify} from 'node:util';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ORIGIN = 'https://platform.example.test';
const SESSION = 'gateway-auth-fixture-session';
const PROFILE = {is_superuser: false, permissions: ['ui.view'], must_change_password: false};
let backendStatus = 200;
let backendBody = {ok: true, ...PROFILE};
let backendCalls = [];
let backendHangs = false;
let gateway;
let gatewayUrl;
let gatewayEnv;
let tempDir;
let output = '';

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server.address().port));
  });
}

async function close(server) {
  const closed = new Promise((resolve) => server.close(resolve));
  server.closeAllConnections?.();
  await closed;
}

const backend = http.createServer((req, res) => {
  backendCalls.push({url: req.url, headers: req.headers});
  if (backendHangs) return;
  res.writeHead(backendStatus, {'content-type': 'application/json'});
  res.end(JSON.stringify(backendBody));
});

before(async () => {
  tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'ai-gateway-auth-'));
  const backendPort = await listen(backend);
  const probe = net.createServer();
  const gatewayPort = await listen(probe);
  await close(probe);
  const providersFile = path.join(tempDir, 'providers.json');
  const routerFile = path.join(tempDir, 'router.json');
  await fs.writeFile(providersFile, JSON.stringify({providers: {
    qwen_plus: {type: 'openai_compatible', model: 'fixture-model', catalogMode: 'static'},
  }}));
  await fs.writeFile(routerFile, '{}');
  const env = {
    ...process.env,
    PORT: String(gatewayPort),
    AI_GATEWAY_AUTH_BASE_URL: `http://127.0.0.1:${backendPort}`,
    AI_GATEWAY_AUTH_TIMEOUT_MS: '100',
    AI_GATEWAY_PROVIDERS_FILE: providersFile,
    AI_GATEWAY_ROUTER_FILE: routerFile,
    AI_GATEWAY_MOCK: '1',
    LOG_ENABLED: 'false',
  };
  delete env.AI_GATEWAY_HOST;
  gatewayEnv = env;
  gateway = spawn(process.execPath, [path.join(ROOT, 'ai-gateway/server.js')], {
    cwd: tempDir, env, stdio: ['ignore', 'pipe', 'pipe'],
  });
  gateway.stdout.on('data', (chunk) => { output += chunk; });
  gateway.stderr.on('data', (chunk) => { output += chunk; });
  gatewayUrl = `http://127.0.0.1:${gatewayPort}`;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    assert.equal(gateway.exitCode, null, 'gateway must remain running');
    try {
      const response = await fetch(`${gatewayUrl}/health`);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 30));
  }
  assert.fail('gateway startup timed out');
});

after(async () => {
  if (gateway && gateway.exitCode === null && gateway.signalCode === null) {
    const exited = once(gateway, 'exit');
    gateway.kill('SIGTERM');
    await exited;
  }
  await close(backend);
  if (tempDir) await fs.rm(tempDir, {recursive: true, force: true});
});

async function request(route, {method = 'GET', headers = {}, body} = {}) {
  return fetch(`${gatewayUrl}${route}`, {
    method, headers, ...(body === undefined ? {} : {body}),
  });
}

test('public health is minimal and never calls the auth backend', async () => {
  backendCalls = [];
  const response = await request('/health', {headers: {Origin: ORIGIN}});
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {ok: true, service: 'ai-gateway'});
  assert.equal(backendCalls.length, 0);
});

test('gateway defaults to listening on 127.0.0.1, not the public network interface', async (t) => {
  const address = Object.values(os.networkInterfaces()).flat().find((entry) => (
    entry.family === 'IPv4' && !entry.internal
  ))?.address;
  if (!address) return t.skip('no non-loopback IPv4 interface is available');
  await new Promise((resolve, reject) => {
    const socket = net.connect({host: address, port: Number(new URL(gatewayUrl).port)});
    socket.setTimeout(1000);
    socket.once('connect', () => { socket.destroy(); reject(new Error('gateway accepted a non-loopback connection')); });
    socket.once('timeout', () => { socket.destroy(); reject(new Error('bind check timed out')); });
    socket.once('error', (error) => {
      if (error.code === 'ECONNREFUSED') resolve();
      else reject(error);
    });
  });
});

test('an explicit Docker-compatible bind still authenticates every non-loopback peer', async (t) => {
  const address = Object.values(os.networkInterfaces()).flat().find((entry) => (
    entry.family === 'IPv4' && !entry.internal
  ))?.address;
  if (!address) return t.skip('no non-loopback IPv4 interface is available');
  backendStatus = 200;
  backendBody = {ok: true, ...PROFILE};
  const probe = net.createServer();
  const port = await listen(probe);
  await close(probe);
  const child = spawn(process.execPath, [path.join(ROOT, 'ai-gateway/server.js')], {
    cwd: tempDir,
    env: {...gatewayEnv, PORT: String(port), AI_GATEWAY_HOST: '0.0.0.0'},
    stdio: 'ignore',
  });
  try {
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      assert.equal(child.exitCode, null);
      try {
        ready = (await fetch(`http://127.0.0.1:${port}/health`)).ok;
        if (ready) break;
      } catch {}
      await new Promise((resolve) => setTimeout(resolve, 30));
    }
    assert.equal(ready, true, 'explicit-bind gateway must start');
    const externalUrl = `http://${address}:${port}`;
    const health = await fetch(`${externalUrl}/health`);
    assert.deepEqual(await health.json(), {ok: true, service: 'ai-gateway'});
    for (const headers of [{}, {'X-Forwarded-For': ''}, {'X-Forwarded-For': '127.0.0.1'}]) {
      assert.equal((await fetch(`${externalUrl}/ai/model-router`, {headers})).status, 401);
    }
    const authenticated = await fetch(`${externalUrl}/ai/model-router`, {
      headers: {Authorization: `Bearer ${SESSION}`},
    });
    assert.equal(authenticated.status, 200);
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      const exited = once(child, 'exit');
      child.kill('SIGTERM');
      await exited;
    }
  }
});

test('browser and forged or empty forwarding headers cannot use loopback trust', async () => {
  for (const headers of [
    {Origin: ORIGIN}, {Origin: ''},
    {'X-Forwarded-For': '127.0.0.1'}, {'X-Forwarded-For': ''},
    {Forwarded: 'for=127.0.0.1'}, {Forwarded: ''},
    {Authorization: ''}, {Authorization: 'Basic fixture'},
    {'Sec-Fetch-Site': 'same-origin'},
  ]) {
    const response = await request('/ai/model-router', {headers});
    assert.equal(response.status, 401, `external marker ${Object.keys(headers)[0]}`);
  }
});

test('authorization happens before JSON parsing and protects every gateway route', async () => {
  for (const [method, route] of [
    ['GET', '/ai/providers'], ['GET', '/agent/runs'], ['GET', '/agent/runs/fixture'],
    ['POST', '/ai/providers/test'], ['POST', '/ai/model-router'],
    ['POST', '/agent/run'], ['POST', '/agent/runs/fixture/confirm'],
    ['POST', '/agent/runs/fixture/cancel'], ['POST', '/ai/validate-yaml'],
    ...['generate-yaml', 'generate-case', 'skill', 'analyze-failure', 'optimize-yaml',
      'chat', 'api-case-generation', 'generate-bug'].map((name) => ['POST', `/ai/${name}`]),
    ['GET', '/unclassified'], ['OPTIONS', '/ai/model-router'],
  ]) {
    const response = await request(route, {
      method,
      headers: {Origin: ORIGIN, 'Content-Type': 'application/json'},
      ...(method === 'POST' ? {body: '{invalid-json'} : {}),
    });
    assert.equal(response.status, 401, `${method} ${route}`);
  }
});

test('live profile changes, revocation and mandatory password change fail closed', async () => {
  const headers = {Authorization: `Bearer ${SESSION}`, Origin: ORIGIN};
  backendCalls = [];
  backendStatus = 200;
  backendBody = {ok: true, ...PROFILE};
  assert.equal((await request('/ai/model-router', {headers})).status, 200);
  backendBody = {ok: true, ...PROFILE, permissions: []};
  assert.equal((await request('/ai/model-router', {headers})).status, 403);
  backendBody = {ok: true, ...PROFILE, is_superuser: true, must_change_password: true};
  assert.equal((await request('/ai/model-router', {headers})).status, 403);
  backendStatus = 401;
  assert.equal((await request('/ai/model-router', {headers})).status, 401);
  backendStatus = 403;
  assert.equal((await request('/ai/model-router', {headers})).status, 403);
  backendStatus = 500;
  const unavailable = await request('/ai/model-router', {headers});
  assert.equal(unavailable.status, 503);
  assert.equal(backendCalls.length, 6, 'permissions must not be cached');
  for (const call of backendCalls) {
    assert.equal(call.url, '/api/auth/me', 'credentials must not be placed in URLs');
    assert.equal(call.headers.authorization, `Bearer ${SESSION}`);
    assert.equal(call.headers.origin, undefined);
    assert.equal(call.headers['x-forwarded-for'], undefined);
  }
  assert.equal(output.includes(SESSION), false, 'credentials must not appear in gateway logs');
});

test('read-only members cannot configure, generate, execute or read global Agent state', async () => {
  backendStatus = 200;
  backendBody = {ok: true, ...PROFILE};
  const headers = {Authorization: `Bearer ${SESSION}`, 'Content-Type': 'application/json'};
  for (const [method, route] of [
    ['POST', '/ai/model-router'], ['POST', '/ai/providers/test'], ['POST', '/ai/chat'],
    ['POST', '/agent/run'], ['GET', '/agent/runs?appId=forged'],
    ['GET', '/agent/runs/fixture?appId=forged'], ['GET', '/new-route'],
  ]) {
    assert.equal((await request(route, {method, headers})).status, 403, `${method} ${route}`);
  }
});

test('an actual stalled auth backend is aborted and returns a bounded 503', async () => {
  backendHangs = true;
  const started = Date.now();
  try {
    const response = await request('/ai/model-router', {headers: {Authorization: `Bearer ${SESSION}`}});
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), {
      success: false, code: 'authentication_unavailable', error: 'authentication_unavailable',
    });
    assert.ok(Date.now() - started < 1500);
  } finally {
    backendHangs = false;
  }
});

test('scoped executors cannot enter any Agent route, while full-data administrators can', async () => {
  backendStatus = 200;
  const headers = {Authorization: `Bearer ${SESSION}`, 'Content-Type': 'application/json'};
  const routes = [
    ['GET', '/agent/runs?appId=app-a', 200], ['GET', '/agent/runs/fixture', 404],
    ['POST', '/agent/run', 400], ['POST', '/agent/runs/fixture/confirm', 400],
    ['POST', '/agent/runs/fixture/cancel', 400],
  ];
  for (const [permissions, scope, allowed] of [
    [['ui.execute'], {ui_apps: '*'}, false],
    [['platform.configure'], {ui_apps: '*'}, false],
    [['platform.configure', 'ui.execute'], {ui_apps: ['app-a']}, false],
    [['platform.configure', 'ui.execute'], {ui_apps: '*'}, true],
  ]) {
    backendBody = {ok: true, ...PROFILE, permissions, scope};
    for (const [method, route, status] of routes) {
      const result = await request(route, {
        method, headers, ...(method === 'POST' ? {body: '{invalid-json'} : {}),
      });
      assert.equal(result.status, allowed ? status : 403, `${method} ${route}`);
    }
  }
});

test('plain loopback Python client headers retain existing health and generation contracts', async () => {
  backendCalls = [];
  backendStatus = 503;
  const health = await request('/health');
  assert.equal((await health.json()).model, 'fixture-model');
  for (const [route, body] of [
    ['/ai/generate-case', {requirement: 'fixture'}],
    ['/ai/skill', {prompt: 'fixture'}],
    ['/ai/chat', {messages: [{role: 'user', content: 'fixture'}]}],
    ['/ai/api-case-generation', {messages: [{role: 'user', content: 'fixture'}]}],
  ]) {
    const response = await request(route, {
      method: 'POST', headers: {'Content-Type': 'application/json; charset=utf-8'},
      body: JSON.stringify(body),
    });
    assert.equal(response.status, 200, route);
    assert.equal((await response.json()).success, true);
  }
  assert.equal((await request('/agent/runs')).status, 200);
  assert.equal(backendCalls.length, 0);
});

test('the actual Python gateway client can still read health and generate without credentials', async () => {
  backendCalls = [];
  backendStatus = 503;
  const python = process.env.PYTHON || await fs.access(path.join(ROOT, '.venv/bin/python'))
    .then(() => path.join(ROOT, '.venv/bin/python'), () => 'python3');
  await promisify(execFile)(python, ['-c', [
    'from task_server.services.ai_gateway_client import ai_gateway_health, _post_json',
    'health = ai_gateway_health()',
    'assert health["ok"] is True and health["model"] == "fixture-model"',
    'result = _post_json("/ai/generate-case", {"requirement": "fixture"})',
    'assert result["success"] is True and result["ok"] is True',
  ].join('\n')], {
    cwd: ROOT, env: {...process.env, AI_GATEWAY_URL: gatewayUrl}, timeout: 10000,
  });
  assert.equal(backendCalls.length, 0);
});
