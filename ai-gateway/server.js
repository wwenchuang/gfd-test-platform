import cors from 'cors';
import dotenv from 'dotenv';
import express from 'express';
import fs from 'fs/promises';
import path from 'path';
import {fileURLToPath} from 'url';
import OpenAI from 'openai';
import {v4 as uuidv4} from 'uuid';
import {cancelAgentRun, confirmAgentRun, getAgentRun, listAgentRuns, startAgentRun} from './agent/agent-orchestrator.js';
import {validateMidsceneYaml} from './validators/midscene-yaml-validator.js';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = Number(process.env.PORT || 8090);
const LOG_ENABLED = String(process.env.LOG_ENABLED || 'true').toLowerCase() !== 'false';
const MOCK_ENABLED = String(process.env.AI_GATEWAY_MOCK || '0').toLowerCase() === '1';
const LOG_FILE = path.join(__dirname, 'logs', 'ai-calls.jsonl');

const ROUTER_FILE = path.join(__dirname, 'config', 'model-router.json');
const PROVIDERS_FILE = path.join(__dirname, 'config', 'providers.json');
const AGENT_WHITELIST_FILE = path.join(__dirname, 'config', 'agent-whitelist.json');
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
};

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
        defaultMaxTokens: 4096,
        temperatureLocked: false,
      },
      highway_gpt5_mini: {
        type: 'openai_compatible',
        name: 'Highway GPT-5 Mini',
        baseUrl: 'https://api.highwayapi.ai/openai',
        apiKeyEnv: 'HIGHWAY_API_KEY',
        model: 'gpt-5-mini',
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

function publicProvider(providerId, providerConfig) {
  return {
    id: providerId,
    name: providerConfig.name || providerId,
    type: providerConfig.type || 'openai_compatible',
    model: providerConfig.model || '',
    configured: providerConfigured(providerConfig),
    temperatureLocked: Boolean(providerConfig.temperatureLocked),
    fixedTemperature: providerConfig.fixedTemperature,
    defaultMaxTokens: providerConfig.defaultMaxTokens,
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
  const providerConfig = providersData.providers?.[providerId];
  if (!providerConfig) throw new Error(`未配置 providerId：${providerId}`);
  return routeFromProviderConfig(action, providerId, providerConfig, configured);
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
    const providerConfig = providersData.providers?.[providerId];
    if (providerConfig) routes.push(routeFromProviderConfig(action, providerId, providerConfig, configured));
  }
  if (!routes.length) throw new Error(`能力 ${action} 没有可用 provider`);
  return routes;
}

async function routeForProviderId(providerId) {
  const providersData = await readProviders();
  const normalized = normalizeLegacyProviderId(providerId || 'qwen_plus');
  const providerConfig = providersData.providers?.[normalized];
  if (!providerConfig) throw new Error(`未配置 providerId：${normalized}`);
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

async function routeCandidatesForCall(action, body = {}, options = {}) {
  const disableFallback = Boolean(options.disableFallback || body?.disableFallback);
  const requestedProviderId = requestedProviderIdFromBody(body, options);
  let routes = [];
  if (requestedProviderId) {
    routes = [{...(await routeForProviderId(requestedProviderId)), action}];
    if (!disableFallback) {
      const fallbackRoutes = await routeCandidatesFor(action);
      const existing = new Set(routes.map((route) => route.providerId));
      routes.push(...fallbackRoutes.filter((route) => !existing.has(route.providerId)));
    }
  } else {
    routes = disableFallback ? [await routeFor(action)] : await routeCandidatesFor(action);
  }
  const requestedModel = requestedModelFromBody(body, options);
  if (requestedModel) {
    routes = routes.map((route) => ({...route, model: requestedModel}));
  }
  if (!routes.length) throw new Error(`能力 ${action} 没有可用 provider`);
  return routes.map((route) => ({...route, action}));
}

function clientForRoute(route) {
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
  });
}

function routeSupportsQwenHybridThinking(route) {
  const model = String(route?.model || '').toLowerCase();
  const baseUrl = String(route?.baseUrl || '').toLowerCase();
  return baseUrl.includes('dashscope.aliyuncs.com') && /^qwen3\.(?:5|6|7)(?:-|$)/.test(model);
}

function completionOptionsForRoute(route, prompt, body, callOptions = {}) {
  const userText = callOptions.userMessage || buildUserMessage(route.action || 'chat', body);
  const imageParts = imagePartsFromBody(body);
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
  if (route.defaultMaxTokens) completionOptions.max_tokens = route.defaultMaxTokens;
  if (body?.jsonResponse === true) {
    completionOptions.response_format = {type: 'json_object'};
    if (routeSupportsQwenHybridThinking(route)) {
      // DashScope JSON Mode is incompatible with thinking=true. These skill
      // calls already have explicit schemas and deterministic output gates.
      completionOptions.enable_thinking = false;
    }
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
  return assets.slice(0, 6).flatMap((item) => {
    const mime = String(item?.mime || 'image/png').toLowerCase();
    const base64 = String(item?.base64 || '').replace(/\s+/g, '');
    const dataUrl = String(item?.dataUrl || '').trim();
    const url = dataUrl.startsWith('data:image/') ? dataUrl : (base64 ? `data:${mime};base64,${base64}` : '');
    if (!url || !/^data:image\/(?:png|jpe?g|webp);base64,/i.test(url)) return [];
    return [{type: 'image_url', image_url: {url}}];
  });
}

function isRetryableAiError(errorText) {
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
    text.includes('500')
  );
}

async function callAi(action, body, options = {}) {
  const id = uuidv4();
  const prompt = options.promptOverride ?? await readPrompt(action);
  const routes = await routeCandidatesForCall(action, body, options);
  let output = '';
  let lastErrorText = null;
  for (let index = 0; index < routes.length; index += 1) {
    const route = routes[index];
    const startedAt = Date.now();
    let success = false;
    let errorText = null;
    try {
      if (MOCK_ENABLED) {
        output = mockAiOutput(action, body);
        if (options.stripFence) output = stripMarkdownFence(output);
        success = true;
        return {id, route: {...route, mock: true}, output};
      }
      const client = clientForRoute(route);
      const completion = await client.chat.completions.create(completionOptionsForRoute(
        {...route, action},
        prompt,
        body,
        {userMessage: options.userMessage, temperature: options.temperature},
      ));
      output = completion.choices?.[0]?.message?.content || '';
      if (options.stripFence) output = stripMarkdownFence(output);
      success = true;
      return {id, route, output};
    } catch (error) {
      errorText = sanitizeError(error);
      lastErrorText = errorText;
      if (!isRetryableAiError(errorText) || index === routes.length - 1) {
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
        inputPreview: preview(body),
        outputPreview: preview(output),
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
      res.status(500).json({
        success: false,
        error: sanitizeError(error),
      });
    }
  };
}

const app = express();
app.use(cors());
app.use(express.json({limit: '2mb'}));

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

app.get('/ai/providers', asyncRoute(async (_req, res) => {
  const providersData = await readProviders();
  const providers = Object.entries(providersData.providers || {})
    .map(([providerId, providerConfig]) => publicProvider(providerId, providerConfig));
  res.json({
    success: true,
    providers,
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
    if (!providersData.providers?.[providerId]) {
      res.status(400).json({success: false, error: `能力 ${action} 选择了不存在的 providerId：${providerId}`});
      return;
    }
    for (const fallbackId of fallbackProviderIds) {
      if (!providersData.providers?.[fallbackId]) {
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
  };
  const {output} = await callAi('generate_yaml', body, {stripFence: true});
  const validation = validateMidsceneYaml(output);
  res.json({
    success: true,
    yaml: output,
    validation,
  });
}));

app.post('/ai/generate-case', asyncRoute(async (req, res) => {
  const body = {
    moduleName: req.body?.moduleName || '',
    requirement: req.body?.requirement || '',
  };
  const {output} = await callAi('generate_case', body);
  res.json({
    success: true,
    data: output,
  });
}));

app.post('/ai/skill', asyncRoute(async (req, res) => {
  const skillName = String(req.body?.skillName || req.body?.skill || 'skill').trim();
  const prompt = String(req.body?.prompt || '').trim();
  if (!prompt) {
    res.status(400).json({success: false, error: 'prompt required'});
    return;
  }
  const body = {
    skillName,
    payload: req.body?.payload || {},
    jsonResponse: req.body?.jsonResponse !== false,
    modelConfig: req.body?.modelConfig || req.body?.model_config || {},
    providerId: req.body?.providerId || req.body?.provider || '',
    model: req.body?.model || req.body?.modelName || '',
  };
  const action = SKILL_ACTION_MAP[skillName] || 'generate_case';
  const {output, route} = await callAi(action, body, {
    promptOverride: '你是移动端测试平台 AI Skill 执行器。必须严格遵守用户输入的 Skill Prompt，只输出合法 JSON。',
    userMessage: prompt,
    temperature: typeof req.body?.temperature === 'number' ? req.body.temperature : 0.1,
  });
  res.json({
    success: true,
    content: output,
    action,
    skillName,
    providerId: route.providerId,
    model: route.model,
  });
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
  };
  const {output} = await callAi('analyze_failure', body);
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
  };
  const {output} = await callAi('optimize_yaml', body, {stripFence: true});
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
  });
}));

app.post('/ai/chat', asyncRoute(async (req, res) => {
  const {messages, temperature, model, providerId, provider} = req.body || {};
  if (!messages || !Array.isArray(messages) || messages.length === 0) {
    return res.status(400).json({error: 'messages required'});
  }
  const routes = providerId || provider ? [await routeForProviderId(providerId || provider)] : await routeCandidatesFor('agent_plan');
  let lastError = null;
  for (let index = 0; index < routes.length; index += 1) {
    const route = {...routes[index], action: 'agent_plan'};
    try {
      const client = clientForRoute(route);
      const completionOptions = completionOptionsForRoute(route, '', {messages}, {temperature});
      completionOptions.messages = messages;
      if (model && !route.temperatureLocked) completionOptions.model = model;
      const completion = await client.chat.completions.create(completionOptions);
      const content = completion.choices?.[0]?.message?.content || '';
      res.json({success: true, content, providerId: route.providerId, model: completionOptions.model});
      return;
    } catch (error) {
      lastError = sanitizeError(error);
      if (!isRetryableAiError(lastError) || index === routes.length - 1) throw new Error(lastError);
    }
  }
  throw new Error(lastError || 'chat failed');
}));

app.post('/ai/generate-bug', asyncRoute(async (req, res) => {
  const body = {
    taskName: req.body?.taskName || '',
    envInfo: req.body?.envInfo || '',
    failureAnalysis: req.body?.failureAnalysis || '',
  };
  const {output} = await callAi('generate_bug', body);
  res.json({
    success: true,
    bug: output,
  });
}));

app.listen(PORT, () => {
  console.log(`ai-gateway running on port ${PORT}`);
});
