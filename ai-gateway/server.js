import cors from 'cors';
import {createHash} from 'node:crypto';
import dotenv from 'dotenv';
import express from 'express';
import fs from 'fs/promises';
import path from 'path';
import {fileURLToPath} from 'url';
import OpenAI from 'openai';
import {v4 as uuidv4} from 'uuid';
import {cancelAgentRun, confirmAgentRun, getAgentRun, listAgentRuns, startAgentRun} from './agent/agent-orchestrator.js';
import {validateMidsceneYaml} from './validators/midscene-yaml-validator.js';
import {createGatewayAuth} from './gateway-auth.js';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = Number(process.env.PORT || 8090);
const HOST = process.env.AI_GATEWAY_HOST || '127.0.0.1';
const LOG_ENABLED = String(process.env.LOG_ENABLED || 'true').toLowerCase() !== 'false';
const MOCK_ENABLED = String(process.env.AI_GATEWAY_MOCK || '0').toLowerCase() === '1';
const LOG_FILE = path.join(__dirname, 'logs', 'ai-calls.jsonl');

const ROUTER_FILE = process.env.AI_GATEWAY_ROUTER_FILE
  ? path.resolve(process.env.AI_GATEWAY_ROUTER_FILE)
  : path.join(__dirname, 'config', 'model-router.json');
const PROVIDERS_FILE = process.env.AI_GATEWAY_PROVIDERS_FILE
  ? path.resolve(process.env.AI_GATEWAY_PROVIDERS_FILE)
  : path.join(__dirname, 'config', 'providers.json');
const AGENT_WHITELIST_FILE = path.join(__dirname, 'config', 'agent-whitelist.json');
const DYNAMIC_PROVIDER_PREFIX = 'catalog_';
const MODEL_CATALOG_TIMEOUT_MS = boundedEnvNumber('AI_PROVIDER_CATALOG_TIMEOUT_MS', 5000, 1000, 15000);
const MODEL_CATALOG_CACHE_MS = boundedEnvNumber('AI_PROVIDER_CATALOG_CACHE_MS', 60000, 0, 300000);
const MODEL_CATALOG_ALLOW_REFRESH = String(process.env.AI_PROVIDER_CATALOG_ALLOW_REFRESH || '0').toLowerCase() === '1';
const JSON_BODY_LIMIT = /^\d+(?:kb|mb)$/i.test(String(process.env.AI_GATEWAY_JSON_LIMIT || '').trim())
  ? String(process.env.AI_GATEWAY_JSON_LIMIT).trim()
  : '20mb';
const AI_CALL_TIMEOUT_MS = boundedEnvNumber('AI_CALL_TIMEOUT_MS', 90000, 5000, 600000);
const AI_CALL_FALLBACK_RESERVE_MS = boundedEnvNumber('AI_CALL_FALLBACK_RESERVE_MS', 15000, 3000, 60000);
const providerCatalogCache = new Map();
const PROMPTS = {
  generate_case: 'generate-case-v1.txt',
  generate_yaml: 'generate-yaml-v1.txt',
  analyze_failure: 'analyze-failure-v1.txt',
  optimize_yaml: 'optimize-yaml-v1.txt',
  generate_bug: 'generate-bug-v1.txt',
};
const ROUTER_ACTIONS = [
  'generate_case',
  'generate_yaml',
  'analyze_failure',
  'optimize_yaml',
  'agent_plan',
  'generate_bug',
];
const SKILL_ACTION_MAP = {
  requirement_analyzer: 'generate_case',
  scenario_designer: 'generate_case',
  automation_filter: 'generate_case',
  visual_grounder: 'generate_case',
  smoke_selector: 'agent_plan',
  baseline_reranker: 'agent_plan',
  execution_scope_planner: 'agent_plan',
  executable_yaml_planner: 'generate_yaml',
  coverage_auditor: 'analyze_failure',
  repair_patch_planner: 'optimize_yaml',
  yaml_patch_repair: 'optimize_yaml',
  yaml_static_repair: 'optimize_yaml',
  case_coverage_repair: 'generate_case',
  legacy_case_generation: 'generate_case',
};

function boundedEnvNumber(name, fallback, minimum, maximum) {
  const value = Number(process.env[name]);
  const normalized = Number.isFinite(value) ? value : fallback;
  return Math.max(minimum, Math.min(maximum, normalized));
}

function preview(value, limit = 500) {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? '', (key, item) => {
    if ((key === 'base64' || key === 'dataUrl') && typeof item === 'string') {
      return `[inline image omitted, ${item.length} chars]`;
    }
    return item;
  });
  return text.replace(/\s+/g, ' ').slice(0, limit);
}

function sanitizeError(error) {
  return String(error?.message || error || '未知错误')
    .replace(/sk-[A-Za-z0-9_-]+/g, 'sk-***')
    .replace(/hk-[A-Za-z0-9_-]+/g, 'hk-***')
    .replace(/figd_[A-Za-z0-9_-]+/g, 'figd_***')
    .slice(0, 1000);
}

async function readJson(filePath, fallback = {}) {
  try {
    return JSON.parse(await fs.readFile(filePath, 'utf8'));
  } catch {
    return fallback;
  }
}

async function writeJson(filePath, data) {
  await fs.mkdir(path.dirname(filePath), {recursive: true});
  await fs.writeFile(filePath, `${JSON.stringify(data, null, 2)}\n`, 'utf8');
}

async function readPrompt(action) {
  const fileName = PROMPTS[action];
  if (!fileName) throw new Error(`未配置 Prompt：${action}`);
  return fs.readFile(path.join(__dirname, 'prompts', fileName), 'utf8');
}

function defaultProviders() {
  return {
    providers: {
      qwen_plus: {
        type: 'openai_compatible',
        name: '千问 Qwen Plus',
        baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        apiKeyEnv: 'QWEN_API_KEY',
        model: 'qwen-plus',
        catalogMode: 'static',
        defaultMaxTokens: 4096,
        temperatureLocked: false,
      },
      highway_gpt5_mini: {
        type: 'openai_compatible',
        name: 'Highway GPT-5 Mini',
        baseUrl: 'https://api.highwayapi.ai/openai',
        apiKeyEnv: 'HIGHWAY_API_KEY',
        model: 'gpt-5-mini',
        catalogMode: 'live',
        catalogName: 'Highway',
        defaultMaxTokens: 4096,
        temperatureLocked: true,
        fixedTemperature: 1,
      },
    },
  };
}

async function readProviders() {
  const data = await readJson(PROVIDERS_FILE, defaultProviders());
  return data.providers ? data : defaultProviders();
}

function normalizeLegacyProviderId(provider) {
  if (provider === 'qwen') return 'qwen_plus';
  if (provider === 'highway_openai') return 'highway_gpt5_mini';
  return provider;
}

function providerConfigured(providerConfig) {
  const envName = providerConfig?.apiKeyEnv || '';
  const value = envName ? process.env[envName] : '';
  return Boolean(value && !/^your_.*_api_key$/i.test(value) && value !== 'test-key');
}

function providerCatalogMode(providerId, providerConfig) {
  const configuredMode = String(providerConfig?.catalogMode || '').trim().toLowerCase();
  if (configuredMode === 'live' || configuredMode === 'static') return configuredMode;
  const baseUrl = String(providerConfig?.baseUrl || '').toLowerCase();
  const apiKeyEnv = String(providerConfig?.apiKeyEnv || '').toUpperCase();
  const normalizedId = String(providerId || '').toLowerCase();
  return apiKeyEnv === 'QWEN_API_KEY' || baseUrl.includes('dashscope.aliyuncs.com') || normalizedId.startsWith('qwen_')
    ? 'static'
    : 'live';
}

function providerChannelKey(providerConfig) {
  return [
    String(providerConfig?.type || 'openai_compatible').trim().toLowerCase(),
    String(providerConfig?.baseUrl || '').trim().replace(/\/+$/, '').toLowerCase(),
    String(providerConfig?.apiKeyEnv || '').trim(),
  ].join('|');
}

function catalogProviderId(baseProviderId, model) {
  const encoded = Buffer.from(JSON.stringify([String(baseProviderId || ''), String(model || '')]), 'utf8').toString('base64url');
  return `${DYNAMIC_PROVIDER_PREFIX}${encoded}`;
}

function parseCatalogProviderId(providerId) {
  const value = String(providerId || '').trim();
  if (!value.startsWith(DYNAMIC_PROVIDER_PREFIX)) return null;
  try {
    const decoded = JSON.parse(Buffer.from(value.slice(DYNAMIC_PROVIDER_PREFIX.length), 'base64url').toString('utf8'));
    if (!Array.isArray(decoded) || decoded.length !== 2) return null;
    const baseProviderId = String(decoded[0] || '').trim();
    const model = String(decoded[1] || '').trim();
    if (!baseProviderId || !model || model.length > 300) return null;
    return {baseProviderId, model};
  } catch {
    return null;
  }
}

function catalogChannelName(providerConfig) {
  const explicit = String(providerConfig?.catalogName || '').trim();
  if (explicit) return explicit;
  const firstNameToken = String(providerConfig?.name || providerConfig?.providerName || '').trim().split(/\s+/)[0];
  return firstNameToken || String(providerConfig?.apiKeyEnv || 'OpenAI Compatible').replace(/_API_KEY$/i, '');
}

function providerConfigForModel(providerConfig, model, name = '') {
  const targetModel = String(model || '').trim();
  const configuredModel = String(providerConfig?.model || '').trim();
  const sameModel = targetModel === configuredModel;
  const gpt5TemperaturePolicy = /^gpt-5(?:[.\-_]|$)/i.test(targetModel);
  const temperatureLocked = sameModel
    ? Boolean(providerConfig?.temperatureLocked)
    : gpt5TemperaturePolicy;
  return {
    ...providerConfig,
    name: name || catalogChannelName(providerConfig),
    model: targetModel,
    temperatureLocked,
    fixedTemperature: temperatureLocked
      ? (sameModel && typeof providerConfig?.fixedTemperature === 'number' ? providerConfig.fixedTemperature : 1)
      : undefined,
  };
}

function publicProvider(providerId, providerConfig, catalog = {}) {
  return {
    id: providerId,
    name: providerConfig.name || providerId,
    type: providerConfig.type || 'openai_compatible',
    model: providerConfig.model || '',
    configured: providerConfigured(providerConfig),
    temperatureLocked: Boolean(providerConfig.temperatureLocked),
    fixedTemperature: providerConfig.fixedTemperature,
    defaultMaxTokens: providerConfig.defaultMaxTokens,
    catalogSource: catalog.source || providerCatalogMode(providerId, providerConfig),
    catalogLive: catalog.source === 'live',
    available: Object.prototype.hasOwnProperty.call(catalog, 'available') ? catalog.available : true,
    catalogHealthy: catalog.healthy !== false,
    catalogError: catalog.error || '',
    ownedBy: catalog.ownedBy || '',
    created: Number(catalog.created || 0) || undefined,
    baseProviderId: catalog.baseProviderId || providerId,
  };
}

function resolveProviderConfig(providersData, providerId) {
  const normalized = normalizeLegacyProviderId(providerId || 'qwen_plus');
  const configured = providersData.providers?.[normalized];
  if (configured) return {providerId: normalized, providerConfig: configured};
  const dynamic = parseCatalogProviderId(normalized);
  if (!dynamic) throw new Error(`未配置 providerId：${normalized}`);
  const baseProviderConfig = providersData.providers?.[dynamic.baseProviderId];
  if (!baseProviderConfig || providerCatalogMode(dynamic.baseProviderId, baseProviderConfig) !== 'live') {
    throw new Error(`动态 providerId 的基础通道不存在：${dynamic.baseProviderId}`);
  }
  return {
    providerId: normalized,
    providerConfig: providerConfigForModel(baseProviderConfig, dynamic.model),
  };
}

function liveProviderChannels(providersData) {
  const channels = new Map();
  for (const [providerId, providerConfig] of Object.entries(providersData.providers || {})) {
    if (providerCatalogMode(providerId, providerConfig) !== 'live') continue;
    const key = providerChannelKey(providerConfig);
    if (!channels.has(key)) channels.set(key, {key, entries: []});
    channels.get(key).entries.push({providerId, providerConfig});
  }
  return [...channels.values()];
}

async function discoverProviderChannel(channel, forceRefresh = false) {
  const now = Date.now();
  const cached = providerCatalogCache.get(channel.key);
  if (!forceRefresh && cached && cached.expiresAt > now) {
    return {...(await cached.promise), cached: true};
  }
  const base = channel.entries[0];
  const promise = (async () => {
    if (!providerConfigured(base.providerConfig)) {
      throw new Error(`未配置 ${base.providerConfig.apiKeyEnv || '模型通道 Key'}`);
    }
    if (base.providerConfig.type !== 'openai_compatible') {
      throw new Error(`实时模型发现暂不支持 provider type：${base.providerConfig.type}`);
    }
    const client = new OpenAI({
      apiKey: process.env[base.providerConfig.apiKeyEnv],
      baseURL: base.providerConfig.baseUrl,
      timeout: MODEL_CATALOG_TIMEOUT_MS,
      maxRetries: 0,
    });
    const page = await client.models.list();
    const models = (Array.isArray(page?.data) ? page.data : [])
      .map((item) => ({
        id: String(item?.id || '').trim(),
        ownedBy: String(item?.owned_by || '').trim(),
        created: Number(item?.created || 0) || 0,
      }))
      .filter((item) => item.id && item.id.length <= 300)
      .filter((item, index, items) => items.findIndex((candidate) => candidate.id === item.id) === index)
      .sort((left, right) => left.id.localeCompare(right.id));
    if (!models.length) throw new Error('上游 /models 未返回任何模型');
    return {success: true, models, error: ''};
  })().catch((error) => ({success: false, models: [], error: sanitizeError(error)}));
  providerCatalogCache.set(channel.key, {promise, expiresAt: now + MODEL_CATALOG_TIMEOUT_MS});
  const result = await promise;
  const cacheForMs = result.success ? MODEL_CATALOG_CACHE_MS : Math.min(MODEL_CATALOG_CACHE_MS, 10000);
  providerCatalogCache.set(channel.key, {promise: Promise.resolve(result), expiresAt: Date.now() + cacheForMs});
  return {...result, cached: false};
}

async function buildProviderCatalog(providersData, forceRefresh = false) {
  const providers = [];
  for (const [providerId, providerConfig] of Object.entries(providersData.providers || {})) {
    if (providerCatalogMode(providerId, providerConfig) === 'static') {
      providers.push(publicProvider(providerId, providerConfig, {source: 'static', available: true}));
    }
  }
  const channels = liveProviderChannels(providersData);
  const discoveries = await Promise.all(channels.map((channel) => discoverProviderChannel(channel, forceRefresh)));
  const channelResults = [];
  channels.forEach((channel, index) => {
    const result = discoveries[index];
    const base = channel.entries[0];
    channelResults.push({
      baseProviderId: base.providerId,
      name: catalogChannelName(base.providerConfig),
      source: result.success ? 'live' : 'configured_fallback',
      modelCount: result.success ? result.models.length : channel.entries.length,
      cached: Boolean(result.cached),
      error: result.error || '',
    });
    if (!result.success) {
      for (const entry of channel.entries) {
        providers.push(publicProvider(entry.providerId, entry.providerConfig, {
          source: 'configured_fallback',
          available: null,
          healthy: false,
          error: result.error,
          baseProviderId: base.providerId,
        }));
      }
      return;
    }
    for (const modelInfo of result.models) {
      const matched = channel.entries.find((entry) => String(entry.providerConfig.model || '').trim() === modelInfo.id);
      const routeBase = matched || base;
      const providerId = matched?.providerId || catalogProviderId(base.providerId, modelInfo.id);
      const providerConfig = providerConfigForModel(
        routeBase.providerConfig,
        modelInfo.id,
        matched?.providerConfig?.name || catalogChannelName(base.providerConfig),
      );
      providers.push(publicProvider(providerId, providerConfig, {
        source: 'live',
        available: true,
        ownedBy: modelInfo.ownedBy,
        created: modelInfo.created,
        baseProviderId: base.providerId,
      }));
    }
  });
  return {
    providers,
    catalog: {
      generatedAt: new Date().toISOString(),
      cacheMs: MODEL_CATALOG_CACHE_MS,
      timeoutMs: MODEL_CATALOG_TIMEOUT_MS,
      refreshAllowed: MODEL_CATALOG_ALLOW_REFRESH,
      channels: channelResults,
      errors: channelResults.filter((item) => item.error).map((item) => ({
        baseProviderId: item.baseProviderId,
        error: item.error,
      })),
    },
  };
}

function routeFromProviderConfig(action, providerId, providerConfig, configured = {}) {
  return {
    action,
    providerId,
    provider: providerId,
    providerName: providerConfig.name || providerId,
    type: providerConfig.type || 'openai_compatible',
    baseUrl: providerConfig.baseUrl,
    apiKeyEnv: providerConfig.apiKeyEnv,
    model: providerConfig.model,
    temperatureLocked: Boolean(providerConfig.temperatureLocked),
    fixedTemperature: providerConfig.fixedTemperature,
    defaultMaxTokens: providerConfig.defaultMaxTokens,
    temperature: Number.isFinite(Number(configured.temperature)) ? Number(configured.temperature) : 0.2,
  };
}

async function routeFor(action) {
  const router = await readJson(ROUTER_FILE, {});
  const configured = router[action] || {};
  const providersData = await readProviders();
  const providerId = normalizeLegacyProviderId(configured.providerId || configured.provider || 'qwen_plus');
  const resolved = resolveProviderConfig(providersData, providerId);
  return routeFromProviderConfig(action, resolved.providerId, resolved.providerConfig, configured);
}

async function routeCandidatesFor(action) {
  const router = await readJson(ROUTER_FILE, {});
  const configured = router[action] || {};
  const providersData = await readProviders();
  const ids = [
    configured.providerId || configured.provider || 'qwen_plus',
    ...(
      Array.isArray(configured.fallbackProviderIds)
        ? configured.fallbackProviderIds
        : Array.isArray(configured.fallbackProviders)
          ? configured.fallbackProviders
          : []
    ),
  ].map(normalizeLegacyProviderId);
  const uniqueIds = [...new Set(ids.filter(Boolean))];
  const routes = [];
  for (const providerId of uniqueIds) {
    try {
      const resolved = resolveProviderConfig(providersData, providerId);
      routes.push(routeFromProviderConfig(action, resolved.providerId, resolved.providerConfig, configured));
    } catch {
      // Keep valid fallbacks usable when one persisted route was removed.
    }
  }
  if (!routes.length) throw new Error(`能力 ${action} 没有可用 provider`);
  return routes;
}

async function routeForProviderId(providerId) {
  const providersData = await readProviders();
  const resolved = resolveProviderConfig(providersData, providerId || 'qwen_plus');
  const normalized = resolved.providerId;
  const providerConfig = resolved.providerConfig;
  return {
    action: 'provider_test',
    providerId: normalized,
    provider: normalized,
    providerName: providerConfig.name || normalized,
    type: providerConfig.type || 'openai_compatible',
    baseUrl: providerConfig.baseUrl,
    apiKeyEnv: providerConfig.apiKeyEnv,
    model: providerConfig.model,
    temperatureLocked: Boolean(providerConfig.temperatureLocked),
    fixedTemperature: providerConfig.fixedTemperature,
    defaultMaxTokens: providerConfig.defaultMaxTokens,
    temperature: 0.2,
  };
}

function requestedProviderIdFromBody(body = {}, options = {}) {
  const modelConfig = body?.modelConfig || body?.model_config || body?.payload?.modelConfig || body?.payload?.model_config || {};
  return normalizeLegacyProviderId(
    options.providerId ||
    body?.providerId ||
    body?.provider ||
    body?.modelProviderId ||
    body?.aiProviderId ||
    modelConfig.providerId ||
    modelConfig.provider ||
    ''
  );
}

function requestedModelFromBody(body = {}, options = {}) {
  const modelConfig = body?.modelConfig || body?.model_config || body?.payload?.modelConfig || body?.payload?.model_config || {};
  return (
    options.model ||
    body?.model ||
    body?.modelName ||
    body?.aiModel ||
    modelConfig.model ||
    modelConfig.modelName ||
    ''
  );
}

function fallbackModelConfigFromBody(body = {}) {
  const raw = body?.fallbackModelConfig || body?.fallback_model_config || body?.payload?.fallbackModelConfig || {};
  return {
    providerId: normalizeLegacyProviderId(raw?.providerId || raw?.provider || ''),
    model: String(raw?.model || raw?.modelName || '').trim(),
  };
}

function routeWithModel(action, route, model) {
  const providerConfig = providerConfigForModel(
    {...route, name: route.providerName || route.name || route.providerId},
    model,
    route.providerName || route.name || '',
  );
  return routeFromProviderConfig(action, route.providerId, providerConfig, route);
}

async function routeCandidatesForCall(action, body = {}, options = {}) {
  const disableFallback = Boolean(Object.hasOwn(body, 'videoStage') || options.disableFallback || body?.disableFallback);
  const requestedProviderId = requestedProviderIdFromBody(body, options);
  let routes = [];
  if (requestedProviderId) {
    routes = [{...(await routeForProviderId(requestedProviderId)), action}];
    if (!disableFallback) {
      const fallbackRoutes = await routeCandidatesFor(action);
      const fallbackRoute = fallbackRoutes.find((route) => route.providerId !== requestedProviderId);
      if (fallbackRoute) routes.push(fallbackRoute);
    }
  } else {
    routes = disableFallback ? [await routeFor(action)] : await routeCandidatesFor(action);
  }
  const requestedModel = requestedModelFromBody(body, options);
  if (requestedModel && routes.length) {
    routes = routes.map((route, index) => (
      index === 0
        ? routeWithModel(action, route, requestedModel)
        : route
    ));
  }
  const fallbackModelConfig = fallbackModelConfigFromBody(body);
  const hasImageInput = imagePartsFromBody(body).length > 0;
  if (hasImageInput && fallbackModelConfig.providerId && fallbackModelConfig.model) {
    const explicitSelection = Boolean(requestedProviderId || requestedModel);
    if (explicitSelection) {
      if (!disableFallback) {
        const fallbackBase = await routeForProviderId(fallbackModelConfig.providerId);
        const fallbackRoute = routeWithModel(action, fallbackBase, fallbackModelConfig.model);
        routes = (
          routes[0].providerId === fallbackRoute.providerId
          && routes[0].model === fallbackRoute.model
        ) ? [routes[0]] : [routes[0], fallbackRoute];
      }
    } else {
      let fallbackIndex = routes.findIndex((route) => route.providerId === fallbackModelConfig.providerId);
      if (fallbackIndex < 0 && !disableFallback) {
        routes.push({...await routeForProviderId(fallbackModelConfig.providerId), action});
        fallbackIndex = routes.length - 1;
      }
      if (fallbackIndex >= 0) {
        const fallbackRoute = routeWithModel(action, routes[fallbackIndex], fallbackModelConfig.model);
        routes[fallbackIndex] = fallbackRoute;
      }
    }
  }
  if (!routes.length) throw new Error(`能力 ${action} 没有可用 provider`);
  return routes.map((route) => ({...route, action}));
}

function clientForRoute(route, timeoutMs = AI_CALL_TIMEOUT_MS) {
  if (route.type !== 'openai_compatible') {
    throw new Error(`暂不支持 provider type：${route.type}`);
  }
  const apiKey = process.env[route.apiKeyEnv];
  if (!apiKey || /^your_.*_api_key$/i.test(apiKey)) {
    throw new Error(`未配置 ${route.apiKeyEnv}`);
  }
  return new OpenAI({
    apiKey,
    baseURL: route.baseUrl,
    timeout: timeoutMs,
    maxRetries: 0,
  });
}

function requestedCallTimeoutMs(body = {}) {
  if (Object.hasOwn(body, 'videoStage')) {
    const remaining = Math.min(body.timeoutMs, body.videoDeadlineMs - Date.now(), 120000);
    if (!Number.isFinite(remaining) || remaining <= 0) throw new Error('video_stage_deadline');
    return remaining;
  }
  const requested = Number(body?.timeoutMs || body?.timeout_ms);
  if (!Number.isFinite(requested)) return AI_CALL_TIMEOUT_MS;
  return Math.max(5000, Math.min(600000, requested));
}

function requestedCompletionTokens(body = {}, route = {}) {
  const raw = body?.maxTokens ?? body?.max_tokens;
  const requested = Number(raw);
  if (Number.isFinite(requested) && requested > 0) {
    return Math.max(256, Math.min(32768, Math.floor(requested)));
  }
  return Number(route?.defaultMaxTokens || 0);
}

function routeSupportsQwenHybridThinking(route) {
  const model = String(route?.model || '').toLowerCase();
  const baseUrl = String(route?.baseUrl || '').toLowerCase();
  return baseUrl.includes('dashscope.aliyuncs.com') && /^qwen3\.(?:5|6|7|8)(?:-|$)/.test(model);
}

// Stage capabilities are server invocation parameters, never a public response_format.
function videoSemanticContract() {
  const text = () => ({type: 'string'});
  const list = items => ({type: 'array', items});
  const object = properties => ({type: 'object', properties, required: Object.keys(properties), additionalProperties: false});
  const choice = values => ({type: 'string', enum: values});
  const refs = () => list(text());
  return object({
    states: list(object({state_id: text(), first_frame: text(), last_frame: text(),
      kind: choice(['page', 'input', 'wait', 'transition']), description: text()})),
    events: list(object({action: choice(['aiTap', 'aiInput', 'aiScroll', 'aiAssert', 'aiWaitFor']),
      target: text(), value: {anyOf: [choice(['']), object({direction: choice(['up', 'down', 'left', 'right']),
        scrollType: choice(['singleAction'])})]}, frame_refs: refs(), observation: text(), uncertainties: refs(),
      trigger_frame: text(), trigger_kind: choice(['touch', 'pressed_control', 'navigation', 'edit', 'automatic', 'uncertain']),
      page_before: text(), page_after: text(), continuation_of: text(),
      edit_boundary: choice(['none', 'submission', 'field_change', 'navigation']),
      outcome: object({frame_refs: refs(), observation: text()})})),
    checks: list(object({event_id: text(), status: choice(['confirmed', 'needs_review']), frame_refs: refs(), question: text()})),
    uncertainties: refs(), multiple_scenarios: {type: 'boolean'},
    revisit: list(object({first_frame: text(), last_frame: text(), question: text()})),
  });
}

function validateVideoOutputSchema(schema) {
  const fail = () => { throw new Error('video_output_schema_invalid'); };
  let encoded;
  try { encoded = JSON.stringify(schema); } catch { fail(); }
  if (!encoded || Buffer.byteLength(encoded, 'utf8') > 32768) fail();
  let count = 0;
  function normalize(node, depth = 0) {
    if (++count > 512 || depth > 16 || !node || typeof node !== 'object' || Array.isArray(node)) fail();
    const keys = Object.keys(node);
    if (keys.length === 1 && ['anyOf', 'oneOf'].includes(keys[0])) {
      const variants = node[keys[0]];
      if (!Array.isArray(variants) || variants.length < 2 || variants.length > 8) fail();
      return {anyOf: variants.map(child => normalize(child, depth + 1))};
    }
    const out = {...node};
    if (keys.length === 1 && keys[0] === 'enum') out.type = 'string';
    const kind = out.type;
    const allowed = new Set(['type', 'enum']);
    if (kind === 'object') {
      ['properties', 'required', 'additionalProperties'].forEach(k => allowed.add(k));
      const props = out.properties, required = out.required;
      if (!props || typeof props !== 'object' || Array.isArray(props) || !Array.isArray(required) ||
          out.additionalProperties !== false || Object.keys(props).some(k => !k || k.length > 128) ||
          required.some(k => typeof k !== 'string') || new Set(required).size !== required.length ||
          required.length !== Object.keys(props).length || required.some(k => !Object.hasOwn(props, k))) fail();
      out.properties = Object.fromEntries(Object.entries(props).map(([k, child]) => [k, normalize(child, depth + 1)]));
    } else if (kind === 'array') {
      allowed.add('items'); out.items = normalize(out.items, depth + 1);
    } else if (!['string', 'boolean', 'integer', 'number'].includes(kind)) fail();
    if (Object.keys(out).some(k => !allowed.has(k))) fail();
    if (Object.hasOwn(out, 'enum')) {
      const values = out.enum;
      if (['object', 'array'].includes(kind) || !Array.isArray(values) || !values.length || values.length > 128 ||
          new Set(values).size !== values.length ||
          values.some(v => kind === 'integer' ? !Number.isSafeInteger(v) : kind === 'number' ?
            typeof v !== 'number' || !Number.isFinite(v) : typeof v !== kind)) fail();
    }
    return out;
  }
  const normalized = normalize(schema), expected = videoSemanticContract();
  const props = normalized.properties || {}, fields = ['states', 'events', 'checks', 'uncertainties', 'multiple_scenarios'];
  if (Object.keys(props).length === fields.length && fields.every(k => Object.hasOwn(props, k))) {
    delete expected.properties.revisit; expected.required = fields;
  }
  function match(actual, contract) {
    if (Object.hasOwn(contract, 'anyOf')) {
      if (Object.keys(actual).length !== 1 || !Array.isArray(actual.anyOf) || actual.anyOf.length !== contract.anyOf.length) fail();
      actual.anyOf.forEach((child, i) => match(child, contract.anyOf[i]));
      return;
    }
    if (actual.type !== contract.type) fail();
    if (contract.type === 'object') {
      if (Object.keys(actual.properties).length !== Object.keys(contract.properties).length ||
          Object.keys(contract.properties).some(k => !Object.hasOwn(actual.properties, k))) fail();
      Object.entries(contract.properties).forEach(([k, child]) => match(actual.properties[k], child));
    } else if (contract.type === 'array') match(actual.items, contract.items);
    if (contract.enum) {
      if (contract.enum.length === 1 && contract.enum[0] === '' && !actual.enum) actual.enum = [''];
      if (!actual.enum || actual.enum.some(v => !contract.enum.includes(v))) fail();
    }
  }
  match(normalized, expected);
  return normalized;
}

function videoStageConfig(stage) {
  return {
    perception: ['ui_video_perception', 'v1', 'whole-perception', 'qwen3.8-max', 8192],
    overview: ['ui_video_trajectory', 'overview', 'whole-trajectory', 'qwen3.8-flash', 10000],
    semantic: ['ui_video_trajectory', 'observe', 'whole-trajectory', 'qwen3.8-flash', 10000],
    audit: ['ui_video_trajectory', 'audit', 'whole-trajectory', 'qwen3.8-max', 10000],
    consolidate: ['ui_video_steps', 'consolidate', 'whole-consolidate', 'qwen3.8-max', 10000],
    outcome: ['ui_video_observe', 'outcome', 'whole-observe', 'qwen3.8-max', 4000],
    inputs: ['ui_video_observe', 'inputs', 'whole-observe', 'qwen3.8-max', 3000],
  }[stage];
}

function videoCanonicalSha(value) {
  const canonical = node => Array.isArray(node) ? node.map(canonical) : node && typeof node === 'object' ?
    Object.fromEntries(Object.keys(node).sort().map(k => [k, canonical(node[k])])) : node;
  return createHash('sha256').update(JSON.stringify(canonical(value)), 'utf8').digest('hex');
}

function videoPolicyAttestation(body, options) {
  const {messages, ...effective} = options;
  effective.response_format = options.response_format.type;
  const images = Array.isArray(messages[1].content) ? messages[1].content.filter(p => p.type === 'image_url') : [];
  effective.image_count = images.length;
  effective.fine_frame_ids = images.flatMap((part, i) => part.min_pixels === 1658880 ? [body.imageAssets[i].name] : []);
  return {version:'video-stage-policy-v1',stage:body.videoStage,skill:body.skillName,skill_version:body.skillVersion,
    correlation:body.videoCorrelation,model:options.model,effective_options:effective,
    provider_options_sha256:videoCanonicalSha(options),
    output_schema_sha256:body.outputSchema ? videoCanonicalSha(validateVideoOutputSchema(body.outputSchema)) : null};
}

function validateVideoStage(body, route) {
  if (!Object.hasOwn(body || {}, 'videoStage')) {
    if (body?.skillName === 'ui_video_perception') throw new Error('video_stage_required');
    if (Object.hasOwn(body || {}, 'outputSchema')) throw new Error('video_stage_schema_without_stage');
    return null;
  }
  const stage = body.videoStage, payload = body.payload;
  const config = typeof stage === 'string' && videoStageConfig(stage);
  if (!config) throw new Error('video_stage_invalid');
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) || body.jsonResponse !== true ||
      body.skillName !== config[0] || body.skillVersion !== config[1] || payload.phase !== config[2])
    throw new Error('video_stage_scope_invalid');
  if (body.videoPolicyVersion !== 'video-stage-policy-v1' || !/^[a-f0-9]{32}$/.test(body.videoCorrelation || ''))
    throw new Error('video_stage_metadata_invalid');
  if ((body.maxTokens != null && body.maxTokens !== config[4]) ||
      (body.temperature != null && body.temperature !== 0.1) ||
      (body.model && body.model !== config[3])) throw new Error('video_stage_options_invalid');
  if (route && (route.model !== config[3] ||
      !/^https:\/\/dashscope(?:-intl|-us)?\.aliyuncs\.com(?:\/|$)/i.test(String(route.baseUrl || ''))))
    throw new Error('video_stage_route_unsupported');
  const frames = stage === 'semantic' ? payload.frames : payload.image_frames;
  if (!Array.isArray(frames) || frames.length < 2 || frames.length > 1000) throw new Error('video_frames_invalid');
  let last = -1; const names = new Set();
  for (const frame of frames) {
    if (!/^frame-(?:[0-9]{3}|end)$/.test(frame?.frame_id || '') || names.has(frame.frame_id) ||
        !Number.isInteger(frame.timestamp_ms) || frame.timestamp_ms <= last || frame.timestamp_ms > 600000)
      throw new Error('video_frames_invalid');
    names.add(frame.frame_id); last = frame.timestamp_ms;
  }
  const assets = body.imageAssets;
  if (stage === 'semantic') {
    if (!Array.isArray(assets) || assets.length || payload.reasoning_mode !== 'bounded' ||
        ['image_frames', 'detail_frames', 'perception_mode', 'perception_frame_ids'].some(k => Object.hasOwn(payload, k)))
      throw new Error('video_stage_semantic_invalid');
    validateVideoOutputSchema(body.outputSchema);
    return stage;
  }
  if (Object.hasOwn(body, 'outputSchema')) throw new Error('video_stage_schema_invalid');
  if (!Array.isArray(assets) || assets.length !== frames.length || assets.length < 2 || assets.length > (stage === 'perception' ? 12 : 60))
    throw new Error('video_vision_limit');
  if (stage === 'perception') {
    if (typeof payload.packet_id !== 'string' || !payload.packet_id || payload.packet_id.length > 128 ||
        JSON.stringify(payload.frames) !== JSON.stringify(frames) ||
        ['detail_frames', 'perception_mode', 'perception_frame_ids'].some(k => Object.hasOwn(payload, k)))
      throw new Error('video_stage_perception_invalid');
    const partition = new Set();
    for (const role of ['owned', 'context']) {
      const refs = payload[`${role}_frame_ids`]; let previous = -1;
      if (!Array.isArray(refs)) throw new Error('video_frames_invalid');
      for (const ref of refs) {
        const index = frames.findIndex(f => f.frame_id === ref);
        if (typeof ref !== 'string' || !/^frame-(?:[0-9]{3}|end)$/.test(ref) || index <= previous || partition.has(ref))
          throw new Error('video_frames_invalid');
        partition.add(ref); previous = index;
      }
    }
    if (partition.size !== frames.length) throw new Error('video_frames_invalid');
  } else {
    if ((['overview','audit'].includes(stage) && payload.reasoning_mode !== 'bounded') || Object.hasOwn(payload, 'detail_frames')) throw new Error('video_stage_audit_invalid');
    if (stage !== 'audit' && ['perception_mode', 'perception_frame_ids'].some(k => Object.hasOwn(payload,k)))
      throw new Error('video_stage_options_invalid');
    if (['perception_mode', 'perception_frame_ids'].some(k => Object.hasOwn(payload, k)) &&
        (payload.perception_mode !== 'fine-detail' || !Array.isArray(payload.perception_frame_ids) ||
         !payload.perception_frame_ids.length || payload.perception_frame_ids.length > 12 ||
         new Set(payload.perception_frame_ids).size !== payload.perception_frame_ids.length ||
         payload.perception_frame_ids.some(ref => typeof ref !== 'string' || !names.has(ref))))
      throw new Error('video_stage_audit_invalid');
  }
  let total = 0;
  for (const [i, asset] of assets.entries()) {
    const frame = frames[i], data = asset?.base64;
    if (asset?.name !== frame.frame_id || asset.timestamp_ms !== frame.timestamp_ms || asset.mime !== 'image/jpeg' ||
        typeof data !== 'string' || !data || asset.dataUrl) throw new Error('video_frames_invalid');
    total += data.length;
    if (total > 10 * 1024 * 1024) throw new Error('video_vision_limit');
    if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(data)) throw new Error('video_frames_invalid');
  }
  return stage;
}

function completionOptionsForRoute(route, prompt, body, callOptions = {}) {
  const videoStage = validateVideoStage(body, route);
  const userText = callOptions.userMessage || buildUserMessage(route.action || 'chat', body);
  const imageParts = imagePartsFromBody(body);
  const trajectoryDetailScope = body?.skillName === 'ui_video_trajectory' && body?.payload?.phase === 'whole-trajectory';
  const hasDetailSelection = trajectoryDetailScope && Object.prototype.hasOwnProperty.call(body.payload, 'perception_frame_ids');
  const detailFrameIds = hasDetailSelection ? body.payload.perception_frame_ids : null;
  if (hasDetailSelection) {
    const names = new Set(body.imageAssets.map(asset => asset.name));
    if (body.payload.perception_mode !== 'fine-detail' || !Array.isArray(detailFrameIds) ||
        detailFrameIds.length < 1 || detailFrameIds.length > 12 ||
        detailFrameIds.some(name => typeof name !== 'string' || !names.has(name)) ||
        new Set(detailFrameIds).size !== detailFrameIds.length) {
      throw new Error('video_perception_selection_invalid');
    }
  }
  if (trajectoryDetailScope && Object.prototype.hasOwnProperty.call(body.payload, 'perception_mode')) {
    if (body.payload.perception_mode !== 'fine-detail') throw new Error('video_perception_mode_invalid');
    if (route.model !== 'qwen3.8-max' ||
        !/^https:\/\/dashscope(?:-intl|-us)?\.aliyuncs\.com(?:\/|$)/i.test(String(route.baseUrl || ''))) {
      throw new Error('video_perception_route_unsupported');
    }
    let imageIndex = 0;
    for (const part of imageParts) {
      if (part.type !== 'image_url') continue;
      const asset = body.imageAssets[imageIndex++];
      if (!hasDetailSelection || detailFrameIds.includes(asset.name)) part.min_pixels = 1658880;
    }
  }
  const completionOptions = {
    model: route.model,
    messages: [
      {role: 'system', content: prompt},
      {role: 'user', content: imageParts.length ? [{type: 'text', text: userText}, ...imageParts] : userText},
    ],
  };
  if (route.temperatureLocked) {
    completionOptions.temperature = typeof route.fixedTemperature === 'number' ? route.fixedTemperature : 1;
  } else {
    completionOptions.temperature = typeof callOptions.temperature === 'number' ? callOptions.temperature : route.temperature;
  }
  const maxTokens = requestedCompletionTokens(body, route);
  if (maxTokens) completionOptions.max_tokens = maxTokens;
  if (body?.jsonResponse === true) {
    completionOptions.response_format = {type: 'json_object'};
    if (routeSupportsQwenHybridThinking(route)) {
      // Preserve non-thinking JSON defaults; bounded trajectory reasoning is
      // explicitly requested only on official Qwen 3.8 Max/Flash routes.
      completionOptions.enable_thinking = false;
    }
  }
  if (body?.jsonResponse === true && body?.skillName === 'ui_video_trajectory' &&
      body?.payload?.phase === 'whole-trajectory' && body?.payload?.reasoning_mode === 'bounded' &&
      ['qwen3.8-max', 'qwen3.8-flash'].includes(String(route.model || '').trim().toLowerCase()) &&
      /^https:\/\/dashscope(?:-intl|-us)?\.aliyuncs\.com(?:\/|$)/i.test(String(route.baseUrl || ''))) {
    completionOptions.enable_thinking = true;
    completionOptions.thinking_budget = 1024;
  }
  if (videoStage) {
    completionOptions.temperature = 0.1;
    completionOptions.max_tokens = videoStageConfig(videoStage)[4];
    completionOptions.enable_thinking = videoStage !== 'perception';
    if (videoStage === 'perception') {
      delete completionOptions.thinking_budget;
      for (const part of imageParts) if (part.type === 'image_url') part.min_pixels = 1658880;
    } else completionOptions.thinking_budget = 1024;
    completionOptions.response_format = videoStage === 'semantic' ?
      {type: 'json_schema', json_schema: {name: 'video_semantic', strict: true, schema: validateVideoOutputSchema(body.outputSchema)}} :
      {type: 'json_object'};
  }
  return completionOptions;
}

async function appendAiLog(entry) {
  if (!LOG_ENABLED) return;
  await fs.mkdir(path.dirname(LOG_FILE), {recursive: true});
  await fs.appendFile(LOG_FILE, JSON.stringify(entry) + '\n', 'utf8');
}

function stripMarkdownFence(text) {
  return String(text || '')
    .replace(/^\s*```(?:yaml|yml|json|text)?\s*/i, '')
    .replace(/\s*```\s*$/i, '')
    .trim();
}

function buildUserMessage(action, body) {
  const promptBody = {...(body || {})};
  if (Array.isArray(promptBody.imageAssets)) {
    promptBody.imageAssets = promptBody.imageAssets.map((item) => ({
      name: item?.name || '',
      mime: item?.mime || '',
      attached: Boolean(item?.base64 || item?.dataUrl),
    }));
  }
  const context = {
    action,
    input: promptBody,
  };
  return `请根据以下 JSON 输入完成任务：\n${JSON.stringify(context, null, 2)}`;
}

function imagePartsFromBody(body = {}) {
  const assets = Array.isArray(body?.imageAssets) ? body.imageAssets : [];
  if (body.videoStage === 'semantic') return [];
  const wholeVideo = (body.videoStage === 'perception' && body.skillName === 'ui_video_perception' && body.payload?.phase === 'whole-perception') ||
    (body.skillName === 'ui_video_observe' && body.payload?.phase === 'whole-observe') ||
    (body.skillName === 'ui_video_steps' && body.payload?.phase === 'whole-consolidate') ||
    (body.skillName === 'ui_video_trajectory' && body.payload?.phase === 'whole-trajectory');
  if (wholeVideo) {
    const frames = body.payload?.image_frames;
    const details = body.payload?.detail_frames || [];
    if (!Array.isArray(details) || details.length > 6 || (details.length && body.skillName !== 'ui_video_observe')) throw new Error('video_frames_invalid');
    if (assets.length < 2 || assets.length > 60 || !Array.isArray(frames) || frames.length < 2 || frames.length + details.length !== assets.length)
      throw new Error('video_vision_limit');
    let total = 0; let last = -1; const names = new Set();
    for (const [index, item] of assets.slice(0, frames.length).entries()) {
      const frame = frames[index];
      if (!/^frame-(?:[0-9]{3}|end)$/.test(frame?.frame_id || '') || names.has(frame.frame_id) ||
          !Number.isInteger(frame.timestamp_ms) || frame.timestamp_ms <= last || frame.timestamp_ms > 600000 ||
          item?.name !== frame.frame_id || item.timestamp_ms !== frame.timestamp_ms ||
          item.mime !== 'image/jpeg' || typeof item.base64 !== 'string' || !item.base64 || item.dataUrl)
        throw new Error('video_frames_invalid');
      total += item.base64.length;
      if (total > 10 * 1024 * 1024) throw new Error('video_vision_limit');
      names.add(frame.frame_id); last = frame.timestamp_ms;
    }
    for (const [index, detail] of details.entries()) {
      const item = assets[frames.length + index];
      const parent = frames.find(f => f.frame_id === detail.source_frame_id);
      if (!parent || detail.frame_id !== `${parent.frame_id}-detail` || names.has(detail.frame_id) ||
          detail.timestamp_ms !== parent.timestamp_ms || item?.name !== detail.frame_id ||
          item.timestamp_ms !== detail.timestamp_ms || item.mime !== 'image/jpeg' ||
          typeof item.base64 !== 'string' || !item.base64 || item.dataUrl) throw new Error('video_frames_invalid');
      total += item.base64.length;
      if (total > 10 * 1024 * 1024) throw new Error('video_vision_limit');
      names.add(detail.frame_id);
    }
  }
  return assets.slice(0, wholeVideo ? 60 : 6).flatMap((item) => {
    const mime = String(item?.mime || 'image/png').toLowerCase();
    const base64 = String(item?.base64 || '').replace(/\s+/g, '');
    const dataUrl = String(item?.dataUrl || '').trim();
    const url = dataUrl.startsWith('data:image/') ? dataUrl : (base64 ? `data:${mime};base64,${base64}` : '');
    if (!url || !/^data:image\/(?:png|jpe?g|webp);base64,/i.test(url)) return [];
    const detail = wholeVideo && body.payload?.detail_frames?.find(f => f.frame_id === item.name);
    const role = body.videoStage === 'perception' ? ` [${body.payload.context_frame_ids.includes(item.name) ? 'context' : 'owned'}]` : detail ? ` [${detail.source_frame_id}的同帧局部放大，不是新事件；仅辅助读文字，${body.payload?.context_frame_ids?.includes(detail.source_frame_id) ? '父帧仅供上下文参考，禁止引用或输出该帧操作' : '引用原帧'}]` : wholeVideo && ['ui_video_observe', 'ui_video_trajectory'].includes(body.skillName) && Array.isArray(body.payload?.context_frame_ids)
      ? (body.payload.context_frame_ids.includes(item.name) ? (body.skillName === 'ui_video_trajectory' ? ' [可引用为前后态或结果证据；不得作为本包新动作触发]' : ' [仅供上下文参考，禁止引用或输出该帧操作]') : ' [当前窗口证据，可引用]') : '';
    return [...(wholeVideo ? [{type: 'text', text: `${item.name} ${item.timestamp_ms}ms${role}`}] : []),
      {type: 'image_url', image_url: {url}}];
  });
}

function assistantOutputText(content) {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content.map((part) => {
    if (typeof part === 'string') return part;
    if (typeof part?.text === 'string') return part.text;
    if (typeof part?.content === 'string') return part.content;
    return '';
  }).join('');
}

function completionUsageSummary(usage = {}) {
  const details = usage?.completion_tokens_details || usage?.completionTokensDetails || {};
  return {
    promptTokens: Number(usage?.prompt_tokens || usage?.promptTokens || 0),
    completionTokens: Number(usage?.completion_tokens || usage?.completionTokens || 0),
    totalTokens: Number(usage?.total_tokens || usage?.totalTokens || 0),
    // The route already carries a normalized summary when projected to HTTP.
    reasoningTokens: Number(details?.reasoning_tokens ?? details?.reasoningTokens ?? usage?.reasoningTokens ?? 0),
  };
}

function completionAuditResponse(route = {}) {
  return {
    finishReason: String(route?.finishReason || ''),
    usage: completionUsageSummary(route?.usage || {}),
    ...(route.videoPolicy ? {videoPolicy:route.videoPolicy} : {}),
  };
}

function emptyAssistantOutputError(choice = {}, usage = {}) {
  const finishReason = String(choice?.finish_reason || choice?.finishReason || 'unknown');
  const tokenUsage = completionUsageSummary(usage);
  const refused = Boolean(choice?.message?.refusal);
  return (
    `AI provider returned empty content (finish_reason=${finishReason}, `
    + `completion_tokens=${tokenUsage.completionTokens}, reasoning_tokens=${tokenUsage.reasoningTokens}, `
    + `refusal=${refused})`
  );
}

function isFallbackEligibleAiError(errorText) {
  const text = String(errorText || '').toLowerCase();
  return (
    text.includes('timeout') ||
    text.includes('timed out') ||
    text.includes('econnreset') ||
    text.includes('etimedout') ||
    text.includes('socket') ||
    text.includes('429') ||
    text.includes('rate limit') ||
    text.includes('throttl') ||
    text.includes('overload') ||
    text.includes('503') ||
    text.includes('502') ||
    text.includes('504') ||
    text.includes('500') ||
    text.includes('404') ||
    text.includes('model_not_found') ||
    text.includes('model not found') ||
    text.includes('model does not exist') ||
    text.includes('model is not available') ||
    text.includes('no access to model') ||
    text.includes('empty content') ||
    text.includes('empty response') ||
    text.includes('structured output truncated') ||
    text.includes('finish_reason=length') ||
    text.includes('unsupported') ||
    text.includes('does not support image') ||
    text.includes('image input is not supported')
  );
}

async function callAi(action, body, options = {}) {
  const videoStage = validateVideoStage(body);
  if (videoStage && options.messages) throw new Error('video_stage_messages_invalid');
  const id = uuidv4();
  const prompt = options.promptOverride ?? await readPrompt(action);
  const routes = await routeCandidatesForCall(action, body, options);
  if (videoStage) {
    if (routes.length !== 1) throw new Error('video_stage_fallback_invalid');
    validateVideoStage(body, routes[0]);
  }
  const totalTimeoutMs = requestedCallTimeoutMs(body);
  const deadline = Date.now() + totalTimeoutMs;
  let output = '';
  let lastErrorText = null;
  const fallbackErrors = [];
  for (let index = 0; index < routes.length; index += 1) {
    const route = routes[index];
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) throw new Error(`AI call timeout after ${totalTimeoutMs}ms`);
    const remainingFallbacks = routes.length - index - 1;
    const perFallbackReserveMs = Math.min(
      AI_CALL_FALLBACK_RESERVE_MS,
      Math.max(3000, Math.floor(totalTimeoutMs * 0.25)),
    );
    const reserveMs = Math.min(
      Math.max(0, remainingMs - 3000),
      perFallbackReserveMs * remainingFallbacks,
    );
    const routeTimeoutMs = videoStage ? remainingMs : Math.max(3000, remainingMs - reserveMs);
    const startedAt = Date.now();
    let success = false;
    let errorText = null;
    let finishReason = '';
    let usage = {};
    try {
      if (videoStage && (MOCK_ENABLED || options.signal?.aborted)) throw new Error('video_stage_cancelled');
      if (MOCK_ENABLED) {
        output = mockAiOutput(action, body);
        if (options.stripFence) output = stripMarkdownFence(output);
        success = true;
        return {
          id,
          route: {
            ...route,
            mock: true,
            fallbackIndex: index,
            fallbackUsed: index > 0,
            fallbackReason: fallbackErrors[fallbackErrors.length - 1]?.error || '',
          },
          output,
        };
      }
      const client = clientForRoute(route, routeTimeoutMs);
      const completionOptions = completionOptionsForRoute(
        {...route, action},
        prompt,
        body,
        {userMessage: options.userMessage, temperature: options.temperature},
      );
      if (Array.isArray(options.messages) && options.messages.length) {
        completionOptions.messages = options.messages;
      }
      const policy = videoStage ? videoPolicyAttestation(body, completionOptions) : null;
      if (videoStage && (options.signal?.aborted || Date.now() >= deadline)) throw new Error('video_stage_deadline');
      const completion = await client.chat.completions.create(completionOptions,
        ...(videoStage ? [{signal: options.signal, timeout: Math.min(routeTimeoutMs, deadline-Date.now()), maxRetries:0}] : []));
      if (videoStage && (options.signal?.aborted || Date.now() >= deadline)) throw new Error('video_stage_cancelled');
      if (videoStage && completion.model !== videoStageConfig(videoStage)[3]) throw new Error('video_stage_actual_model_invalid');
      const choice = completion.choices?.[0] || {};
      finishReason = String(choice?.finish_reason || choice?.finishReason || '');
      if (videoStage === 'perception') {
        const rawUsage = completion?.usage || {};
        const rawDetails = [rawUsage.completion_tokens_details, rawUsage.completionTokensDetails];
        const reported = rawDetails.flatMap(details => details && typeof details === 'object' ?
          ['reasoning_tokens', 'reasoningTokens'].filter(k => Object.hasOwn(details, k)).map(k => details[k]) : []);
        for (const key of ['reasoning_tokens', 'reasoningTokens'])
          if (Object.hasOwn(rawUsage, key)) reported.push(rawUsage[key]);
        if (reported.some(value => !Number.isInteger(value) || value !== 0))
          throw new Error('video_perception_reasoning_usage_invalid');
      }
      usage = completionUsageSummary(completion?.usage || {});
      if (videoStage && finishReason.toLowerCase() === 'length') {
        throw new Error('video_stage_output_truncated');
      }
      output = assistantOutputText(choice?.message?.content);
      if (options.stripFence) output = stripMarkdownFence(output);
      if (!String(output || '').trim()) {
        throw new Error(emptyAssistantOutputError(choice, completion?.usage || {}));
      }
      if (body?.jsonResponse === true && finishReason.toLowerCase() === 'length') {
        throw new Error(
          `Structured output truncated: finish_reason=length, `
          + `completion_tokens=${usage.completionTokens || 0}, `
          + `reasoning_tokens=${usage.reasoningTokens || 0}`
        );
      }
      success = true;
      return {
        id,
        route: {
          ...route,
          fallbackIndex: index,
          fallbackUsed: index > 0,
          fallbackReason: fallbackErrors[fallbackErrors.length - 1]?.error || '',
          finishReason,
          usage,
          ...(videoStage ? {model:completion.model, videoPolicy:policy} : {}),
        },
        output,
      };
    } catch (error) {
      errorText = videoStage ? (/^video_[a-z_]+$/.test(error?.message || '') ? error.message : 'video_stage_provider_failed') : sanitizeError(error);
      lastErrorText = errorText;
      fallbackErrors.push({
        providerId: route.providerId,
        model: route.model,
        error: errorText,
      });
      if (!isFallbackEligibleAiError(errorText) || index === routes.length - 1) {
        throw new Error(errorText);
      }
    } finally {
      await appendAiLog({
        id,
        time: new Date().toISOString(),
        action,
        providerId: route.providerId,
        provider: route.providerName || route.provider,
        model: route.model,
        success,
        durationMs: Date.now() - startedAt,
        fallbackIndex: index,
        fallbackTotal: routes.length,
        routeTimeoutMs,
        totalTimeoutMs,
        finishReason,
        usage,
        inputPreview: videoStage ? '[video stage content omitted]' : preview(body),
        outputPreview: videoStage ? '[video stage content omitted]' : preview(output),
        error: errorText,
      }).catch(() => {});
    }
  }
  throw new Error(lastErrorText || 'AI Gateway 调用失败');
}

function mockAiOutput(action, body) {
  if (action === 'generate_yaml') {
    const name = body?.testCase || '示例自动化用例';
    return `android:
  tasks:
    - name: "${String(name).replace(/"/g, '')}"
      flow:
        - sleep: 1000
        - aiTap: "首页关键入口"
        - aiAction: "按需求完成主要业务操作"
        - aiAssert: "业务结果符合预期"`;
  }
  if (action === 'generate_case') {
    return JSON.stringify({
      moduleName: body?.moduleName || '未命名模块',
      cases: [
        {
          caseName: body?.requirement || '核心链路验证',
          priority: 'P1',
          checks: ['正常流程', '异常提示', '边界状态'],
        },
      ],
    });
  }
  if (action === 'analyze_failure') {
    return JSON.stringify({
      conclusion: '脚本定位或模型服务波动导致失败',
      failureType: 'SCRIPT_ISSUE',
      possibleReasons: ['页面入口文案变化', '等待条件不够明确', '模型请求超时'],
      suggestions: ['补充 aiWaitFor', '使用更稳定的可见文案定位', '保留失败截图后再修复'],
      yamlSnippet: '      - aiWaitFor: "目标页面加载完成，关键按钮可见"'
    }, null, 2);
  }
  if (action === 'optimize_yaml') {
    return `android:
  tasks:
    - name: "AI 修复草稿"
      flow:
        - sleep: 1000
        - aiWaitFor: "首页加载完成，关键入口可见"
        - aiTap: "更稳定的业务入口"
        - aiAssert: "目标业务结果符合预期"`;
  }
  if (action === 'generate_bug') {
    return '缺陷草稿：请补充环境、复现步骤、期望结果、实际结果和附件后再提交飞书。';
  }
  return '';
}

function asyncRoute(handler) {
  return async (req, res) => {
    try {
      await handler(req, res);
    } catch (error) {
      if (res.destroyed || res.writableEnded) return;
      res.status(500).json({
        success: false,
        error: sanitizeError(error),
      });
    }
  };
}

const app = express();
app.use(cors({preflightContinue: true}));
app.use(createGatewayAuth());
app.use(express.json({limit: JSON_BODY_LIMIT}));

app.get('/health', asyncRoute(async (_req, res) => {
  const route = await routeFor('generate_yaml');
  res.json({
    ok: true,
    service: 'ai-gateway',
    providerId: route.providerId,
    provider: route.providerName,
    model: route.model,
    mock: MOCK_ENABLED,
  });
}));

app.get('/agent/runs', (req, res) => {
  res.json({
    success: true,
    runs: listAgentRuns(Number(req.query?.limit || 50)),
  });
});

app.post('/agent/run', asyncRoute(async (req, res) => {
  const agentWhitelist = await readJson(AGENT_WHITELIST_FILE, {});
  const run = await startAgentRun(req.body || {}, {callAi, validateMidsceneYaml, agentWhitelist});
  res.json({
    success: run.status !== 'FAILED',
    run,
  });
}));

app.get('/agent/runs/:runId', (req, res) => {
  const run = getAgentRun(req.params.runId);
  if (!run) {
    res.status(404).json({success: false, error: 'Agent run 不存在'});
    return;
  }
  res.json({success: true, run});
});

app.post('/agent/runs/:runId/confirm', (req, res) => {
  const run = confirmAgentRun(req.params.runId, req.body || {});
  if (!run) {
    res.status(404).json({success: false, error: 'Agent run 不存在'});
    return;
  }
  res.json({success: true, run});
});

app.post('/agent/runs/:runId/cancel', (req, res) => {
  const run = cancelAgentRun(req.params.runId, req.body || {});
  if (!run) {
    res.status(404).json({success: false, error: 'Agent run 不存在'});
    return;
  }
  res.json({success: true, run});
});

app.post('/ai/validate-yaml', (req, res) => {
  res.json(validateMidsceneYaml(req.body?.yaml || ''));
});

app.get('/ai/providers', asyncRoute(async (req, res) => {
  const providersData = await readProviders();
  const refreshRequested = ['1', 'true'].includes(String(req.query?.refresh || '').toLowerCase());
  const forceRefresh = MODEL_CATALOG_ALLOW_REFRESH && refreshRequested;
  const {providers, catalog} = await buildProviderCatalog(providersData, forceRefresh);
  res.json({
    success: true,
    providers,
    catalog,
  });
}));

app.post('/ai/providers/test', asyncRoute(async (req, res) => {
  const route = await routeForProviderId(req.body?.providerId || req.body?.provider || 'qwen_plus');
  if (MOCK_ENABLED) {
    res.json({
      success: true,
      providerId: route.providerId,
      provider: route.providerName,
      model: route.model,
      configured: providerConfigured({apiKeyEnv: route.apiKeyEnv}),
      output: 'gateway ok',
      mock: true,
    });
    return;
  }
  const client = clientForRoute(route);
  const prompt = '你是网关健康检查助手，只返回 gateway ok。';
  const completion = await client.chat.completions.create(completionOptionsForRoute(route, prompt, {providerId: route.providerId}));
  const output = completion.choices?.[0]?.message?.content || '';
  res.json({
    success: true,
    providerId: route.providerId,
    provider: route.providerName,
    model: route.model,
    output: output.trim(),
  });
}));

app.get('/ai/model-router', asyncRoute(async (_req, res) => {
  const router = await readJson(ROUTER_FILE, {});
  const normalized = {};
  const fallbackRouter = {};
  for (const action of ROUTER_ACTIONS) {
    const configured = router[action] || {};
    normalized[action] = normalizeLegacyProviderId(configured.providerId || configured.provider || 'qwen_plus');
    fallbackRouter[action] = (
      Array.isArray(configured.fallbackProviderIds)
        ? configured.fallbackProviderIds
        : Array.isArray(configured.fallbackProviders)
          ? configured.fallbackProviders
          : []
    ).map(normalizeLegacyProviderId);
  }
  res.json({
    success: true,
    router: normalized,
    fallbackRouter,
  });
}));

app.post('/ai/model-router', asyncRoute(async (req, res) => {
  const providersData = await readProviders();
  const currentRouter = await readJson(ROUTER_FILE, {});
  const source = req.body?.router && typeof req.body.router === 'object' ? req.body.router : (req.body || {});
  const nextRouter = {};
  for (const action of ROUTER_ACTIONS) {
    const raw = source[action];
    const current = currentRouter[action] || {};
    const providerId = normalizeLegacyProviderId(
      typeof raw === 'object' && raw
        ? (raw.providerId || raw.provider)
        : (raw || current.providerId || current.provider || 'qwen_plus')
    );
    const fallbackProviderIds = (
      typeof raw === 'object' && raw
        ? (raw.fallbackProviderIds || raw.fallbackProviders || [])
        : (current.fallbackProviderIds || current.fallbackProviders || [])
    ).map(normalizeLegacyProviderId).filter((id) => id && id !== providerId);
    try {
      resolveProviderConfig(providersData, providerId);
    } catch {
      res.status(400).json({success: false, error: `能力 ${action} 选择了不存在的 providerId：${providerId}`});
      return;
    }
    for (const fallbackId of fallbackProviderIds) {
      try {
        resolveProviderConfig(providersData, fallbackId);
      } catch {
        res.status(400).json({success: false, error: `能力 ${action} 选择了不存在的 fallback providerId：${fallbackId}`});
        return;
      }
    }
    nextRouter[action] = {providerId, fallbackProviderIds};
  }
  await writeJson(ROUTER_FILE, nextRouter);
  res.json({
    success: true,
    router: Object.fromEntries(Object.entries(nextRouter).map(([action, item]) => [action, item.providerId])),
    fallbackRouter: Object.fromEntries(Object.entries(nextRouter).map(([action, item]) => [action, item.fallbackProviderIds || []])),
  });
}));

app.post('/ai/generate-yaml', asyncRoute(async (req, res) => {
  const body = {
    appName: req.body?.appName || '',
    platform: req.body?.platform || 'android',
    target: req.body?.target || req.body?.goal || '',
    testCase: req.body?.testCase || req.body?.target || '',
    requirement: req.body?.requirement || '',
    sourceType: req.body?.sourceType || '',
    sourceContext: req.body?.sourceContext || {},
    businessContext: req.body?.businessContext || {},
    promptCenter: req.body?.promptCenter || {},
    modelConfig: req.body?.modelConfig || req.body?.model_config || {},
    providerId: req.body?.providerId || req.body?.provider || '',
    model: req.body?.model || req.body?.modelName || '',
    timeoutMs: req.body?.timeoutMs || req.body?.timeout_ms,
  };
  const {output, route} = await callAi('generate_yaml', body, {stripFence: true});
  const validation = validateMidsceneYaml(output);
  res.json({
    success: true,
    yaml: output,
    validation,
    providerId: route.providerId,
    model: route.model,
    fallbackUsed: Boolean(route.fallbackUsed),
    fallbackIndex: Number(route.fallbackIndex || 0),
    fallbackReason: route.fallbackReason || '',
    ...completionAuditResponse(route),
  });
}));

app.post('/ai/generate-case', asyncRoute(async (req, res) => {
  const body = {
    moduleName: req.body?.moduleName || '',
    requirement: req.body?.requirement || '',
    modelConfig: req.body?.modelConfig || req.body?.model_config || {},
    providerId: req.body?.providerId || req.body?.provider || '',
    model: req.body?.model || req.body?.modelName || '',
    timeoutMs: req.body?.timeoutMs || req.body?.timeout_ms,
  };
  const {output, route} = await callAi('generate_case', body);
  res.json({
    success: true,
    data: output,
    providerId: route.providerId,
    model: route.model,
    fallbackUsed: Boolean(route.fallbackUsed),
    fallbackIndex: Number(route.fallbackIndex || 0),
    fallbackReason: route.fallbackReason || '',
    ...completionAuditResponse(route),
  });
}));

app.post('/ai/skill', asyncRoute(async (req, res) => {
  const skillName = String(req.body?.skillName || req.body?.skill || 'skill').trim();
  const prompt = String(req.body?.prompt || '').trim();
  if (!prompt) {
    res.status(400).json({success: false, error: 'prompt required'});
    return;
  }
  const stageRequested = Object.hasOwn(req.body || {}, 'videoStage');
  const body = {
    skillName,
    payload: req.body?.payload || {},
    jsonResponse: stageRequested && Object.hasOwn(req.body || {}, 'jsonResponse') ?
      req.body.jsonResponse : req.body?.jsonResponse !== false,
    modelConfig: req.body?.modelConfig || req.body?.model_config || {},
    providerId: req.body?.providerId || req.body?.provider || '',
    model: req.body?.model || req.body?.modelName || '',
    imageAssets: stageRequested && Object.hasOwn(req.body || {}, 'imageAssets') ?
      req.body.imageAssets : req.body?.imageAssets || [],
    fallbackModelConfig: req.body?.fallbackModelConfig || req.body?.fallback_model_config || {},
    maxTokens: req.body?.maxTokens ?? req.body?.max_tokens,
    timeoutMs: req.body?.timeoutMs || req.body?.timeout_ms,
    ...(stageRequested ? {videoStage: req.body.videoStage, skillVersion:req.body.skillVersion,
      videoPolicyVersion:req.body.videoPolicyVersion, videoCorrelation:req.body.videoCorrelation,
      videoDeadlineMs:req.body.videoDeadlineMs, temperature:req.body.temperature} : {}),
    ...(Object.hasOwn(req.body || {}, 'outputSchema') ? {outputSchema: req.body.outputSchema} : {}),
  };
  const action = SKILL_ACTION_MAP[skillName] || 'generate_case';
  const controller = stageRequested ? new AbortController() : null;
  const abort = () => controller?.abort();
  const close = () => {if (!res.writableEnded) abort();};
  let timer;
  try {
  if (stageRequested) {
    req.on('aborted', abort);res.on('close', close);
    timer = setTimeout(abort, requestedCallTimeoutMs(body));
    if (req.aborted || res.destroyed) abort();
  }
  const {output, route} = await callAi(action, body, {
    ...(controller ? {signal:controller.signal} : {}),
    disableFallback: skillName === 'ui_video_trajectory' &&
      body.payload.phase === 'whole-trajectory' && body.payload.perception_mode === 'fine-detail',
    promptOverride: '你是移动端测试平台 AI Skill 执行器。必须严格遵守用户输入的 Skill Prompt，只输出合法 JSON。',
    userMessage: prompt,
    temperature: typeof req.body?.temperature === 'number' ? req.body.temperature : 0.1,
  });
  if (controller?.signal.aborted || res.destroyed || res.writableEnded) return;
  res.json({
    success: true,
    content: output,
    action,
    skillName,
    providerId: route.providerId,
    model: route.model,
    fallbackUsed: Boolean(route.fallbackUsed),
    fallbackIndex: Number(route.fallbackIndex || 0),
    fallbackReason: route.fallbackReason || '',
    ...completionAuditResponse(route),
  });
  } finally {
    if (controller) {clearTimeout(timer);req.removeListener('aborted',abort);res.removeListener('close',close);}
  }
}));

app.post('/ai/analyze-failure', asyncRoute(async (req, res) => {
  const body = {
    target: req.body?.target || '',
    requirement: req.body?.requirement || '',
    taskName: req.body?.taskName || '',
    yaml: req.body?.yaml || '',
    log: req.body?.log || '',
    screenshotDesc: req.body?.screenshotDesc || '',
    failureType: req.body?.failureType || '',
    context: req.body?.context || '',
    failedJobs: req.body?.failedJobs || [],
    imageAssets: req.body?.imageAssets || [],
    reportKeyframes: req.body?.reportKeyframes || [],
    baselineExamples: req.body?.baselineExamples || [],
    evidenceSources: req.body?.evidenceSources || [],
    sourceEvidence: req.body?.sourceEvidence || {},
    executionConstraint: req.body?.executionConstraint || {},
    modelConfig: req.body?.modelConfig || req.body?.model_config || {},
    providerId: req.body?.providerId || req.body?.provider || '',
    model: req.body?.model || req.body?.modelName || '',
    fallbackModelConfig: req.body?.fallbackModelConfig || req.body?.fallback_model_config || {},
    timeoutMs: req.body?.timeoutMs || req.body?.timeout_ms,
  };
  const {output, route} = await callAi('analyze_failure', body);
  let structured = null;
  try {
    structured = JSON.parse(stripMarkdownFence(output));
  } catch {
    structured = null;
  }
  res.json({
    success: true,
    analysis: structured?.conclusion || structured?.analysis || output,
    conclusion: structured?.conclusion || structured?.analysis || '',
    recommendation: structured?.recommendation || structured?.suggestion || '',
    failureType: structured?.failureType || '',
    evidence: Array.isArray(structured?.evidence) ? structured.evidence : [],
    canAutoRepair: structured?.canAutoRepair === true,
    providerId: route.providerId,
    model: route.model,
    fallbackUsed: Boolean(route.fallbackUsed),
    fallbackIndex: Number(route.fallbackIndex || 0),
    fallbackReason: route.fallbackReason || '',
    ...completionAuditResponse(route),
  });
}));

app.post('/ai/optimize-yaml', asyncRoute(async (req, res) => {
  const body = {
    yaml: req.body?.yaml || '',
    failureAnalysis: req.body?.failureAnalysis || '',
    requirement: req.body?.requirement || '',
    taskName: req.body?.taskName || '',
    target: req.body?.target || '',
    allFailedJobs: req.body?.allFailedJobs || [],
    imageAssets: req.body?.imageAssets || [],
    reportKeyframes: req.body?.reportKeyframes || [],
    baselineExamples: req.body?.baselineExamples || [],
    evidenceSources: req.body?.evidenceSources || [],
    sourceEvidence: req.body?.sourceEvidence || {},
    executionConstraint: req.body?.executionConstraint || {},
    repairPolicy: req.body?.repairPolicy || {},
    modelConfig: req.body?.modelConfig || req.body?.model_config || {},
    providerId: req.body?.providerId || req.body?.provider || '',
    model: req.body?.model || req.body?.modelName || '',
    fallbackModelConfig: req.body?.fallbackModelConfig || req.body?.fallback_model_config || {},
    timeoutMs: req.body?.timeoutMs || req.body?.timeout_ms,
  };
  const {output, route} = await callAi('optimize_yaml', body, {stripFence: true});
  let structured = null;
  try {
    structured = JSON.parse(stripMarkdownFence(output));
  } catch {
    structured = null;
  }
  const yaml = String(structured?.yaml || structured?.fixedYaml || output || '').trim();
  const validation = validateMidsceneYaml(yaml);
  res.json({
    success: true,
    yaml,
    analysis: structured?.analysis || '',
    changes: Array.isArray(structured?.changes) ? structured.changes : [],
    usedBaselineIds: Array.isArray(structured?.usedBaselineIds) ? structured.usedBaselineIds : [],
    validation,
    providerId: route.providerId,
    model: route.model,
    fallbackUsed: Boolean(route.fallbackUsed),
    fallbackIndex: Number(route.fallbackIndex || 0),
    fallbackReason: route.fallbackReason || '',
    ...completionAuditResponse(route),
  });
}));

app.post('/ai/chat', asyncRoute(async (req, res) => {
  const {messages, temperature, model, providerId, provider} = req.body || {};
  if (!messages || !Array.isArray(messages) || messages.length === 0) {
    return res.status(400).json({error: 'messages required'});
  }
  const body = {
    messages,
    providerId: providerId || provider || '',
    model: model || '',
    timeoutMs: req.body?.timeoutMs || req.body?.timeout_ms,
  };
  const {output, route} = await callAi('agent_plan', body, {
    promptOverride: '',
    messages,
    temperature,
  });
  res.json({
    success: true,
    content: output,
    providerId: route.providerId,
    model: route.model,
    fallbackUsed: Boolean(route.fallbackUsed),
    fallbackIndex: Number(route.fallbackIndex || 0),
    fallbackReason: route.fallbackReason || '',
    ...completionAuditResponse(route),
  });
}));

app.post('/ai/api-case-generation', asyncRoute(async (req, res) => {
  const {messages, temperature, model, providerId, provider} = req.body || {};
  if (!messages || !Array.isArray(messages) || messages.length === 0) {
    return res.status(400).json({error: 'messages required'});
  }
  const body = {
    messages,
    providerId: providerId || provider || '',
    model: model || '',
    timeoutMs: req.body?.timeoutMs || req.body?.timeout_ms,
  };
  const {output, route} = await callAi('api_case_generation', body, {
    promptOverride: '',
    messages,
    temperature,
  });
  res.json({
    success: true,
    content: output,
    providerId: route.providerId,
    model: route.model,
    fallbackUsed: Boolean(route.fallbackUsed),
    fallbackIndex: Number(route.fallbackIndex || 0),
    fallbackReason: route.fallbackReason || '',
    ...completionAuditResponse(route),
  });
}));

app.post('/ai/generate-bug', asyncRoute(async (req, res) => {
  const body = {
    taskName: req.body?.taskName || '',
    envInfo: req.body?.envInfo || '',
    failureAnalysis: req.body?.failureAnalysis || '',
    modelConfig: req.body?.modelConfig || req.body?.model_config || {},
    providerId: req.body?.providerId || req.body?.provider || '',
    model: req.body?.model || req.body?.modelName || '',
    timeoutMs: req.body?.timeoutMs || req.body?.timeout_ms,
  };
  const {output, route} = await callAi('generate_bug', body);
  res.json({
    success: true,
    bug: output,
    providerId: route.providerId,
    model: route.model,
    fallbackUsed: Boolean(route.fallbackUsed),
    fallbackIndex: Number(route.fallbackIndex || 0),
    fallbackReason: route.fallbackReason || '',
    ...completionAuditResponse(route),
  });
}));

app.listen(PORT, HOST, () => {
  console.log(`ai-gateway running on port ${PORT}`);
});
