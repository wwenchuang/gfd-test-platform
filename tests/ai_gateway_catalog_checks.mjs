import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import fs from 'node:fs/promises';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ACTIONS = [
  'generate_case',
  'generate_yaml',
  'analyze_failure',
  'optimize_yaml',
  'agent_plan',
  'generate_bug',
];

function listen(server, port = 0) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => {
      server.off('error', reject);
      resolve(server.address().port);
    });
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

async function freePort() {
  const probe = net.createServer();
  const port = await listen(probe);
  await close(probe);
  return port;
}

async function readJsonBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
}

function sendJson(res, status, data) {
  const body = JSON.stringify(data);
  res.writeHead(status, {'content-type': 'application/json', 'content-length': Buffer.byteLength(body)});
  res.end(body);
}

async function requestJson(baseUrl, pathname, options = {}) {
  const response = await fetch(`${baseUrl}${pathname}`, {
    ...options,
    headers: {'content-type': 'application/json', ...(options.headers || {})},
  });
  const data = await response.json();
  assert.equal(response.ok, true, `${pathname} failed: ${JSON.stringify(data)}`);
  return data;
}

async function waitForGateway(baseUrl, child, output) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (child.exitCode !== null) throw new Error(`gateway exited early: ${output()}`);
    try {
      const response = await fetch(`${baseUrl}/health`);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`gateway did not start: ${output()}`);
}

const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'ai-gateway-catalog-'));
let modelListCalls = 0;
let failModelList = false;
const completionModels = [];
const upstream = http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/v1/models') {
    modelListCalls += 1;
    if (failModelList) {
      sendJson(res, 503, {error: {message: 'catalog temporarily unavailable', type: 'server_error'}});
      return;
    }
    sendJson(res, 200, {
      object: 'list',
      data: [
        {id: 'gpt-static', object: 'model', owned_by: 'fixture'},
        {id: 'gpt-new', object: 'model', owned_by: 'fixture'},
        {id: 'gpt-down', object: 'model', owned_by: 'fixture'},
        {id: 'gpt-no-vision', object: 'model', owned_by: 'fixture'},
        {id: 'gpt-hang', object: 'model', owned_by: 'fixture'},
      ],
    });
    return;
  }
  if (req.method === 'POST' && req.url === '/v1/chat/completions') {
    const body = await readJsonBody(req);
    completionModels.push(body.model);
    const hasImageInput = (body.messages || []).some((message) => (
      Array.isArray(message?.content)
      && message.content.some((part) => part?.type === 'image_url')
    ));
    if (body.model === 'gpt-no-vision' && hasImageInput) {
      sendJson(res, 400, {error: {message: 'model does not support image input', type: 'invalid_request_error'}});
      return;
    }
    if (body.model === 'gpt-hang') {
      await new Promise((resolve) => setTimeout(resolve, 4000));
    }
    if (body.model === 'gpt-down') {
      sendJson(res, 503, {error: {message: 'model temporarily unavailable', type: 'server_error'}});
      return;
    }
    const systemText = String(body.messages?.[0]?.content || '');
    const content = systemText.includes('gateway ok') ? 'gateway ok' : JSON.stringify({accepted: true, model: body.model});
    sendJson(res, 200, {
      id: 'chatcmpl-fixture',
      object: 'chat.completion',
      created: 1,
      model: body.model,
      choices: [{index: 0, message: {role: 'assistant', content}, finish_reason: 'stop'}],
    });
    return;
  }
  sendJson(res, 404, {error: {message: 'not found'}});
});

let gateway = null;
try {
  const upstreamPort = await listen(upstream);
  const gatewayPort = await freePort();
  const providersFile = path.join(tempDir, 'providers.json');
  const routerFile = path.join(tempDir, 'model-router.json');
  const baseUrl = `http://127.0.0.1:${upstreamPort}/v1`;
  await fs.writeFile(providersFile, JSON.stringify({
    providers: {
      highway_seed: {
        type: 'openai_compatible',
        name: 'Fixture GPT Static',
        baseUrl,
        apiKeyEnv: 'HIGHWAY_API_KEY',
        model: 'gpt-static',
        catalogMode: 'live',
        catalogName: 'Fixture',
        defaultMaxTokens: 256,
      },
      qwen_plus: {
        type: 'openai_compatible',
        name: 'Fixture Qwen',
        baseUrl,
        apiKeyEnv: 'QWEN_API_KEY',
        model: 'qwen-plus',
        catalogMode: 'static',
        defaultMaxTokens: 256,
      },
    },
  }, null, 2));
  await fs.writeFile(routerFile, JSON.stringify(Object.fromEntries(ACTIONS.map((action) => [
    action,
    {providerId: 'qwen_plus', fallbackProviderIds: []},
  ])), null, 2));

  let gatewayOutput = '';
  gateway = spawn(process.execPath, [path.join(ROOT, 'ai-gateway', 'server.js')], {
    cwd: path.join(ROOT, 'ai-gateway'),
    env: {
      ...process.env,
      PORT: String(gatewayPort),
      LOG_ENABLED: 'false',
      AI_GATEWAY_MOCK: '0',
      AI_GATEWAY_PROVIDERS_FILE: providersFile,
      AI_GATEWAY_ROUTER_FILE: routerFile,
      AI_PROVIDER_CATALOG_CACHE_MS: '60000',
      AI_PROVIDER_CATALOG_ALLOW_REFRESH: '1',
      HIGHWAY_API_KEY: 'fixture-highway-key',
      QWEN_API_KEY: 'fixture-qwen-key',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  gateway.stdout.on('data', (chunk) => { gatewayOutput += chunk.toString(); });
  gateway.stderr.on('data', (chunk) => { gatewayOutput += chunk.toString(); });
  const gatewayUrl = `http://127.0.0.1:${gatewayPort}`;
  await waitForGateway(gatewayUrl, gateway, () => gatewayOutput);

  const first = await requestJson(gatewayUrl, '/ai/providers?refresh=1');
  assert.equal(modelListCalls, 1);
  assert.equal(first.catalog.channels.length, 1);
  assert.equal(first.catalog.channels[0].source, 'live');
  assert.equal(first.providers.find((item) => item.id === 'qwen_plus')?.catalogSource, 'static');
  assert.equal(first.providers.find((item) => item.model === 'gpt-static')?.id, 'highway_seed');
  const dynamicNew = first.providers.find((item) => item.model === 'gpt-new');
  const dynamicDown = first.providers.find((item) => item.model === 'gpt-down');
  const dynamicNoVision = first.providers.find((item) => item.model === 'gpt-no-vision');
  const dynamicHang = first.providers.find((item) => item.model === 'gpt-hang');
  assert.ok(dynamicNew?.id.startsWith('catalog_'));
  assert.ok(dynamicDown?.id.startsWith('catalog_'));
  assert.ok(dynamicNoVision?.id.startsWith('catalog_'));
  assert.ok(dynamicHang?.id.startsWith('catalog_'));

  await requestJson(gatewayUrl, '/ai/providers');
  assert.equal(modelListCalls, 1, 'catalog should use the short cache');
  await requestJson(gatewayUrl, '/ai/providers?refresh=1');
  assert.equal(modelListCalls, 2, 'refresh=1 should bypass the short cache');

  const testResult = await requestJson(gatewayUrl, '/ai/providers/test', {
    method: 'POST',
    body: JSON.stringify({providerId: dynamicNew.id}),
  });
  assert.equal(testResult.model, 'gpt-new');
  assert.equal(completionModels.at(-1), 'gpt-new');

  const saved = await requestJson(gatewayUrl, '/ai/model-router', {
    method: 'POST',
    body: JSON.stringify({router: {agent_plan: dynamicNew.id}}),
  });
  assert.equal(saved.router.agent_plan, dynamicNew.id);
  const persisted = await requestJson(gatewayUrl, '/ai/model-router');
  assert.equal(persisted.router.agent_plan, dynamicNew.id);

  const skillResult = await requestJson(gatewayUrl, '/ai/skill', {
    method: 'POST',
    body: JSON.stringify({
      skillName: 'scenario_designer',
      prompt: 'Return JSON.',
      jsonResponse: true,
      providerId: dynamicDown.id,
      model: 'gpt-down',
    }),
  });
  assert.equal(skillResult.model, 'qwen-plus');
  const fallbackModels = completionModels.slice(-2);
  assert.deepEqual(fallbackModels, ['gpt-down', 'qwen-plus']);
  assert.equal(skillResult.fallbackUsed, true);
  assert.equal(skillResult.fallbackIndex, 1);
  assert.match(skillResult.fallbackReason, /temporarily unavailable/i);

  const chatResult = await requestJson(gatewayUrl, '/ai/chat', {
    method: 'POST',
    body: JSON.stringify({
      messages: [{role: 'user', content: 'Return a small JSON object.'}],
      providerId: dynamicDown.id,
      model: 'gpt-down',
      timeoutMs: 5000,
    }),
  });
  assert.equal(chatResult.model, 'gpt-new');
  assert.equal(chatResult.fallbackUsed, true);
  assert.match(chatResult.fallbackReason, /temporarily unavailable/i);
  const chatFallbackModels = completionModels.slice(-2);
  assert.deepEqual(chatFallbackModels, ['gpt-down', 'gpt-new']);

  const visualSkillResult = await requestJson(gatewayUrl, '/ai/skill', {
    method: 'POST',
    body: JSON.stringify({
      skillName: 'visual_grounder',
      prompt: 'Return JSON after inspecting the image.',
      jsonResponse: true,
      providerId: dynamicNoVision.id,
      model: 'gpt-no-vision',
      imageAssets: [{name: 'frame.png', mime: 'image/png', base64: 'AA=='}],
      fallbackModelConfig: {providerId: 'qwen_plus', model: 'qwen-vision'},
    }),
  });
  assert.equal(visualSkillResult.providerId, 'qwen_plus');
  assert.equal(visualSkillResult.model, 'qwen-vision');
  assert.equal(visualSkillResult.fallbackUsed, true);
  assert.equal(visualSkillResult.fallbackIndex, 1);
  assert.match(visualSkillResult.fallbackReason, /does not support image/i);
  const visualFallbackModels = completionModels.slice(-2);
  assert.deepEqual(visualFallbackModels, ['gpt-no-vision', 'qwen-vision']);

  const timeoutStartedAt = Date.now();
  const timeoutSkillResult = await requestJson(gatewayUrl, '/ai/skill', {
    method: 'POST',
    body: JSON.stringify({
      skillName: 'scenario_designer',
      prompt: 'Return JSON.',
      jsonResponse: true,
      providerId: dynamicHang.id,
      model: 'gpt-hang',
      timeoutMs: 5000,
    }),
  });
  const timeoutDurationMs = Date.now() - timeoutStartedAt;
  assert.equal(timeoutSkillResult.model, 'qwen-plus');
  assert.equal(timeoutSkillResult.fallbackUsed, true);
  assert.match(timeoutSkillResult.fallbackReason, /timed out|timeout/i);
  const timeoutFallbackModels = completionModels.slice(-2);
  assert.deepEqual(timeoutFallbackModels, ['gpt-hang', 'qwen-plus']);
  assert.ok(timeoutDurationMs < 6000, `timeout fallback exceeded total budget: ${timeoutDurationMs}ms`);

  failModelList = true;
  const degraded = await requestJson(gatewayUrl, '/ai/providers?refresh=1');
  assert.equal(degraded.catalog.errors.length, 1);
  const configuredFallback = degraded.providers.find((item) => item.id === 'highway_seed');
  assert.equal(configuredFallback.catalogSource, 'configured_fallback');
  assert.equal(configuredFallback.available, null);
  assert.equal(configuredFallback.configured, true);
  const persistedTest = await requestJson(gatewayUrl, '/ai/providers/test', {
    method: 'POST',
    body: JSON.stringify({providerId: dynamicNew.id}),
  });
  assert.equal(persistedTest.model, 'gpt-new');

  console.log(JSON.stringify({
    ok: true,
    liveModels: first.providers.filter((item) => item.catalogSource === 'live').length,
    dynamicRoutePersisted: true,
    fallbackModels,
    chatFallbackModels,
    visualFallbackModels,
    timeoutFallbackModels,
    timeoutDurationMs,
    degradedCatalogPreservedConfiguredRoutes: true,
  }));
} finally {
  if (gateway && gateway.exitCode === null) {
    gateway.kill('SIGTERM');
    await Promise.race([once(gateway, 'exit'), new Promise((resolve) => setTimeout(resolve, 1000))]);
  }
  if (upstream.listening) await close(upstream);
  await fs.rm(tempDir, {recursive: true, force: true});
}
