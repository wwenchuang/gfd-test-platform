// api.js
// Extracted from task-manager.html (no logic changes).

// ===== CONFIG =====
const API_BASE = '/api';
const AI_GATEWAY_BASE = '/ai-gateway';

// Moved here from STATE block to keep dependency order (state.js loads before api.js)
const AGENT_API_BASE = AI_GATEWAY_BASE;

// ===== API CLIENT =====
const nativeFetch = window.fetch.bind(window);

function sessionToken() {
  return sessionStorage.getItem('sessionToken') || '';
}

function authHeaders(headers = {}) {
  const next = new Headers(headers || {});
  const token = sessionToken();
  if (token && !next.has('Authorization')) next.set('Authorization', `Bearer ${token}`);
  return next;
}

const WORKFLOW_SECTIONS = {
  dashboard: {
    index: '0',
    title: 'Agent 工作台',
    subtitle: '全自动 Agent 测试工作台',
    help: '输入测试目标后，Agent 自动完成用例选择、YAML 生成、Sonic 执行、失败分析、修复重跑和报告沉淀。',
    cards: [],
    checklist: ['输入测试目标，选择范围和模式', '启动后右侧查看进度和确认节点', '高风险动作会停在待确认']
  },
  assets: {
    index: '1',
    title: '资料中心',
    subtitle: '沉淀页面知识、截图和设计稿，让 AI 生成有可靠上下文',
    help: '这里不直接生成 YAML，只维护可复用资料。新建自动化测试请到“AI 生成”，生成和修复会自动引用这里的页面知识。',
    cards: [
      { title: '页面知识库', text: '按 APP 保存首页、我的页、核心业务页截图、入口路径、关键元素和断言。AI 生成与 AI 修复都会优先参考这些真实页面。', actions: [
        { label: '打开页面知识库', cls: 'primary', fn: 'showKnowledgeManager()' }
      ]},
      { title: 'Figma 设计稿', text: '粘贴具体页面 Frame 链接后导入相关 UI。系统会按需求筛选相关页面，避免把无关设计稿混进生成上下文。', actions: [
        { label: '导入 Figma 页面', fn: 'openKnowledgeFigmaImport()' }
      ]},
      { title: '补充截图与说明', text: '当需求分析提示资料缺失、待确认或风险点时，可以在生成详情里补截图和说明；稳定资料再沉淀到页面知识库。', actions: [
        { label: '查看生成记录', fn: 'showGenerateJobsCenter()' }
      ]},
      { title: '开始生成', text: '需求文档、补充说明和临时 UI 稿属于一次性生成输入，统一从 AI 生成发起，避免资料管理和生成入口混在一起。', actions: [
        { label: '去 AI 生成', cls: 'success', fn: 'activateWorkflow("generate")' }
      ]}
    ],
    checklist: ['长期复用的页面截图放页面知识库', '一次性需求文档和补充说明从 AI 生成上传', 'Figma 优先选择具体页面 Frame，不要整文件一股脑导入']
  },
  generate: {
    index: '1',
    title: 'AI 生成',
    subtitle: '资料准备、需求解析、脑图和 YAML 统一从这里发起',
    help: '新建自动化测试会自动组合需求文档、Figma、页面知识和补充说明；生成记录、测试脑图和页面知识都归在这里。',
    cards: [
      { title: '生成可调试自动化', text: 'AI 会先拆测试场景，再筛选适合自动化的用例，最后转换成 Midscene 能执行的自动化脚本。', actions: [
        { label: '新建自动化测试', cls: 'primary', fn: 'showGenerateYaml()' }
      ]},
      { title: '页面知识 / Figma', text: '把长期复用的页面截图、入口、断言和 Figma Frame 放到页面知识库；生成和修复会自动引用。', actions: [
        { label: '页面知识库', fn: 'showKnowledgeManager()' },
        { label: '导入 Figma', fn: 'openKnowledgeFigmaImport()' }
      ]},
      { title: '生成任务 / 生成记录', text: '查看需求解析、用例生成、Figma 解析和 AI 修复等后台任务，不再去执行中心里翻。', actions: [
        { label: '查看生成记录', fn: 'showGenerateJobsCenter()' }
      ]},
      { title: '脑图中心', text: '把生成结果整理成脑图文件（FreeMind .mm），方便人工评审覆盖范围、风险点、优先级和人工待准备事项。', actions: [
        { label: '打开脑图中心', fn: 'showMindmapCenter()' }
      ]}
    ],
    checklist: ['需求文档和临时截图从“新建自动化测试”上传', '长期复用页面沉淀到页面知识库', '生成后先看分析和脑图，再进入调试']
  },
  agent: {
    index: '2',
    title: 'AI Agent',
    subtitle: '全自动 Agent 编排生成、校验、执行、失败分析和安全重跑',
    help: 'Agent 会按测试目标自动规划、生成用例和 YAML、校验、同步执行、分析失败并生成修复草稿；高风险动作会停在确认节点。',
    cards: [
      { title: '启动全自动 Agent', text: '输入测试目标后，Agent 自动规划用例、生成 YAML、执行、分析失败、生成修复草稿并按风险策略等待确认。', actions: [
        { label: '打开Agent 工作台', cls: 'primary', fn: 'showAgentWorkbench()' }
      ]},
      { title: '查看运行轨迹', text: '每一步都会记录状态、耗时、输入输出摘要、产物和等待确认节点，便于排查全自动链路。', actions: [
        { label: '刷新 Agent 状态', fn: 'refreshAgentRuns(true)' }
      ]},
      { title: '人工确认节点', text: '高风险动作、基线覆盖、飞书缺陷提交等动作都会停下来等你确认。', actions: [
        { label: '查看右侧执行中心', fn: 'renderAgentCenter()' }
      ]}
    ],
    checklist: ['SEMI_AUTO 执行 Sonic 前必须确认', '命中确认打印/支付/删除等风险词会提示', 'YAML 校验失败不能确认执行']
  },
  execute: {
    index: '3',
    title: '调试修复',
    subtitle: '执行、报告、失败归因和 AI 修复放在同一个入口',
    help: '打开 YAML 后先单条执行；失败后在同一页看报告、截图和日志，再决定修复选中用例或当前文件。',
    cards: [
      { title: '单条/多条调试', text: '适合新生成或刚修复的用例。每条只下发当前 task 给 Runner，不会跑 Sonic 整套。', actions: [
        { label: '单条/多条', cls: 'success', fn: 'safeRunSelectedTask()' }
      ]},
      { title: '整文件回归', text: '适合已经调试过的 YAML。按当前文件全部 tasks 执行，便于回归和生成完整报告。', actions: [
        { label: '执行整文件', cls: 'success', fn: 'safeRunCurrentFile()' }
      ]},
      { title: '查看执行中心', text: '执行任务、当前用例、失败原因、报告链接都会集中展示在右侧执行中心。', actions: [
        { label: '刷新执行中心', fn: 'loadJobs(true)' }
      ]},
      { title: 'AI 修复', text: '确认是脚本问题后先生成修复草稿，人工确认后再应用。支持修复当前选中用例，也支持整文件失败后的批量修复。', actions: [
        { label: '修复选中', cls: 'ai', fn: 'safeRepairSelectedTask()' },
        { label: '生成修复草稿', cls: 'ai', fn: 'safeRepairCurrentFile()' }
      ]}
    ],
    checklist: ['新用例先单条调试', '失败先看报告和当前用例进度', '确认脚本问题后再 AI 修复']
  },
  repair: {
    index: '3',
    title: 'AI 修复',
    subtitle: '用失败日志、报告和页面知识修复脚本问题',
    help: '基线回归只记录失败与证据，不自动修改已验证脚本；确认属于脚本问题后，再手动发起修复。',
    cards: [
      { title: '修复选中用例', text: '只修当前 tasks[].name，适合多用例 YAML 中某一条失败的场景。', actions: [
        { label: '修复选中', cls: 'ai', fn: 'safeRepairSelectedTask()' }
      ]},
      { title: '生成修复草稿', text: '用于整文件失败后的批量分析。会优先保留业务链路，生成草稿后需要人工确认再应用。', actions: [
        { label: '生成修复草稿', cls: 'ai', fn: 'safeRepairCurrentFile()' }
      ]},
      { title: '绑定辅助截图', text: '给基线文件或某条用例绑定页面知识，AI 修复时会按当前 APP 包名优先参考。', actions: [
        { label: '基线辅助截图', fn: 'safeShowBaselineRefs()' }
      ]}
    ],
    checklist: ['没有执行记录时只做静态体检', '优先补启动和关闭 APP 前后置', '弹窗、入口变更、断言过泛化要分别处理']
  },
  baseline: {
    index: '4',
    title: '同步至 Sonic 平台',
    subtitle: '把验证过的 YAML 入库、版本化，并同步至 Sonic 平台用于回归',
    help: '通过状态标记区分草稿、待评审、已入库、基线、需维护，后续接缺陷平台也依赖这些状态。',
    cards: [
      { title: '标记生命周期', text: '选中文件后在“更多”里标记草稿、待评审、已入库、基线、需维护等状态。', actions: [
        { label: '打开更多', fn: 'toggleMoreMenu(event)' }
      ]},
      { title: '历史版本', text: '保存、AI 修复、覆盖、移动前会保留版本，方便回滚稳定基线。', actions: [
        { label: '历史版本', fn: 'safeShowFileHistory()' }
      ]},
      { title: '同步至 Sonic 平台', text: '已入库或基线 YAML 可以同步为 Sonic 平台可选用例；Sonic 执行时按 case_id 回 Task 平台拉取最新版 YAML。', actions: [
        { label: '同步当前 YAML 至 Sonic 平台', fn: 'publishCurrentFileToSonic()' }
      ]},
      { title: '批量维护', text: '支持按模块全选、批量移动、批量删除，后续可以按应用组织基线目录。', actions: [
        { label: '全选当前模块', fn: 'selectCurrentModuleFiles()' }
      ]}
    ],
    checklist: ['跑通并评审后再标基线', '基线用例尽量绑定辅助页面', '废弃和阻塞不要混在回归集合里']
  },
  config: {
    index: '5',
    title: '模型配置',
    subtitle: '配置模型通道、模型路由和参数策略',
    help: '默认全部使用千问；页面只配置能力到模型的路由，需要时可手动切换单项能力。',
    cards: [
      { title: '推荐策略：默认千问版', text: '用例、YAML、失败分析、修复、Agent 判断和缺陷草稿默认使用千问；需要时可在模型配置里按能力切换。', actions: [
        { label: '应用推荐策略', cls: 'primary', fn: 'showModelConfigCenter()' }
      ]},
      { title: '测试当前策略', text: '发送一条测试请求，确认 AI 模型服务和模型策略可用。', actions: [
        { label: '测试 AI 模型服务', cls: 'success', fn: 'testAiGateway()' }
      ]}
    ],
    checklist: ['服务端统一配置 API Key', '全部 AI 能力默认用千问', '需要更强推理时在模型配置里手动切换']
  },
  app_config: {
    index: '5',
    title: '应用配置',
    subtitle: '配置应用包名、模块归属和 Sonic 项目绑定',
    help: '应用分组决定模块归属、包名匹配和 Sonic 项目绑定。',
    cards: [
      { title: '应用分组、包名和 Sonic', text: '按 APP 中文名、包名、模块和 Sonic 测试套绑定。', actions: [
        { label: '应用分组', cls: 'primary', fn: 'showTaskApps()' }
      ]}
    ],
    checklist: ['模块要绑定正确 APP 包名', 'Sonic 项目要和 APP 对应']
  },
  sonic_config: {
    index: '5',
    title: 'Sonic 配置',
    subtitle: 'Sonic 连接、步骤清理和桥接管理',
    help: '扫描 Sonic 中旧模板或新旧并存的 Midscene 步骤，也可以批量刷新已同步用例的桥接脚本和当前 runner token。',
    cards: [
      { title: 'Sonic 步骤清理', text: '扫描 Sonic 中旧模板或新旧并存的 Midscene 步骤。', actions: [
        { label: '扫描旧/重复步骤', cls: 'primary', fn: 'scanLegacySonicCases("all")' }
      ]},
      { title: '刷新桥接脚本', text: 'Token 或桥接逻辑更新后，一键刷新 Sonic 中已托管用例的 Groovy 引导脚本，不修改 YAML。', actions: [
        { label: '刷新全部桥接脚本', cls: 'primary', fn: 'refreshSonicBridgeScripts("all")' }
      ]}
    ],
    checklist: ['定期清理旧步骤', 'Token 变更后刷新桥接脚本', '桥接步骤保持唯一']
  },
  feishu_config: {
    index: '5',
    title: '飞书配置',
    subtitle: '配置飞书通知群 Webhook 与缺陷协同',
    help: '飞书 Webhook 由 Task 平台按测试套汇总通知；缺陷草稿确认后会按此通道提交。',
    cards: [
      { title: '飞书通知群 Webhook', text: '在“应用分组”里按 APP 维护飞书通知群 Webhook；缺陷草稿确认后会按此通道汇总发送。', actions: [
        { label: '维护应用分组与 Webhook', cls: 'primary', fn: 'showTaskApps()' }
      ]},
      { title: '缺陷草稿', text: 'Agent 失败分析后会生成飞书缺陷草稿，人工确认后才会发送。', actions: [
        { label: '查看缺陷草稿', fn: 'activateWorkflow("bug_drafts")' }
      ]}
    ],
    checklist: ['Webhook 必须为有效自定义机器人地址', '缺陷草稿默认需要人工确认才提交']
  },
  system_config: {
    index: '5',
    title: '系统设置',
    subtitle: '环境体检、Runner 和系统维护',
    help: '一次检查 Task 服务、Sonic、Runner、模型、桥接脚本和旧/重复步骤清理状态。',
    cards: [
      { title: '环境体检工作台', text: '一次检查 Task 服务、Sonic、Runner、模型、桥接脚本状态。', actions: [
        { label: '打开体检工作台', cls: 'primary', fn: 'showPreflightDashboard()' }
      ]},
      { title: 'Midscene 报告清理', text: '定期清理本地 HTML 报告和过期上传分片。', actions: [
        { label: '报告清理', fn: 'showReportCleanupCenter()' }
      ]}
    ],
    checklist: ['Runner 环境变量和 Midscene 模型要一致', '定期体检确认环境可用']
  },
  knowledge: {
    index: '6',
    title: '页面知识库',
    subtitle: '按 APP 维护页面截图、入口路径和断言',
    help: '沉淀可复用的页面资产，AI 生成和 AI 修复会自动引用。',
    cards: [
      { title: '页面知识库', text: '按 APP 保存首页、核心业务页截图、入口路径和关键元素。', actions: [
        { label: '打开页面知识库', cls: 'primary', fn: 'showKnowledgeManager()' }
      ]},
      { title: 'Figma 设计稿', text: '粘贴具体页面 Frame 链接后导入相关 UI。', actions: [
        { label: '导入 Figma 页面', fn: 'openKnowledgeFigmaImport()' }
      ]}
    ],
    checklist: ['长期复用页面沉淀到知识库', 'Figma 优先选择具体页面 Frame']
  },
  reports: {
    index: '6',
    title: '执行报告',
    subtitle: '查看历史执行报告和清理',
    help: '集中管理所有执行报告、历史记录和清理操作。',
    cards: [
      { title: '查看执行中心', text: '执行任务、当前用例、失败原因、报告链接集中展示。', actions: [
        { label: '刷新执行中心', cls: 'primary', fn: 'loadJobs(true)' }
      ]},
      { title: '报告清理', text: '清理本地 HTML 报告和过期上传分片。', actions: [
        { label: '报告清理', fn: 'showReportCleanupCenter()' }
      ]}
    ],
    checklist: ['定期清理过期报告', '保留基线相关报告']
  },
  agent_history: {
    index: '0',
    title: '运行记录',
    subtitle: '查看所有Agent 运行历史',
    help: '查看Agent 运行轨迹、状态和产物。',
    cards: [],
    checklist: []
  },
  agent_confirm: {
    index: '0',
    title: '待我确认',
    subtitle: 'Agent 等待人工确认的事项',
    help: '高风险动作、修复草稿、基线覆盖等需要人工确认后才能继续。',
    cards: [],
    checklist: []
  },
  yaml_edit: {
    index: '1',
    title: 'YAML 编辑',
    subtitle: '从左侧选择 YAML 文件进行编辑和调试',
    help: '从左侧用例树选择 YAML 文件，在编辑器中修改、保存和执行。',
    cards: [],
    checklist: []
  },
  failure_analysis: {
    index: '6',
    title: '失败分析',
    subtitle: 'AI 分析失败原因并生成修复建议',
    help: '选择失败任务，AI 自动分析失败原因并生成修复草稿。',
    cards: [
      { title: '分析失败原因', text: '选择失败任务后，AI 会分析失败原因并给出归因。', actions: [
        { label: 'AI 分析失败原因', cls: 'ai', fn: 'showAiRepairCenter()' }
      ]}
    ],
    checklist: []
  },
  bug_drafts: {
    index: '6',
    title: '缺陷草稿',
    subtitle: 'Agent 或 AI 生成的缺陷草稿',
    help: '查看 Agent 生成的缺陷草稿，确认后提交到飞书。',
    cards: [],
    checklist: []
  }
};


async function readAgentResponse(res) {
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch(e) {
    throw new Error(`Agent 接口返回的不是 JSON：HTTP ${res.status}`);
  }
  if (!res.ok || !(data.ok || data.success)) {
    throw new Error(data.error || `Agent 请求失败：HTTP ${res.status}`);
  }
  return data;
}

async function readAiGatewayResponse(res) {
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch(e) {
    throw new Error(`AI 模型服务返回的不是 JSON：HTTP ${res.status}`);
  }
  if (!res.ok || !(data.ok || data.success)) {
    throw new Error(data.error || data.message || `AI 模型服务请求失败：HTTP ${res.status}`);
  }
  return data;
}

async function apiRequest(path, options = {}) {
  const { skipAuthRedirect, ...rest } = options || {};
  const headers = authHeaders(rest.headers || {});
  // FormData 上传不要主动设置 Content-Type，浏览器会自动处理 boundary
  if (rest.body && !(rest.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  // body 支持对象，自动 JSON 序列化（FormData/string/Blob 等保持原样）
  if (rest.body && typeof rest.body === 'object'
      && !(rest.body instanceof FormData)
      && !(rest.body instanceof Blob)
      && !(rest.body instanceof ArrayBuffer)
      && !(rest.body instanceof URLSearchParams)) {
    rest.body = JSON.stringify(rest.body);
  }
  const res = await nativeFetch(`${API_BASE}${path}`, {
    ...rest,
    headers
  });
  if (res.status === 401) {
    if (skipAuthRedirect) {
      // 登录页等场景下，由调用方处理 401（避免循环跳转登录页）
    } else {
      forceLogoutWithMessage();
    }
  } else if (res.status === 403) {
    showFriendlyError(res.status, '无权限执行此操作');
  } else if (res.status === 413) {
    showFriendlyError(res.status, '请求体过大');
  } else if (res.status === 502 || res.status === 504) {
    showFriendlyError(res.status, '服务暂时不可用');
  } else if (!res.ok) {
    showFriendlyError(res.status, `HTTP ${res.status}`);
  }
  return readJsonResponse(res);
}

async function apiTextRequest(path, options = {}) {
  const headers = authHeaders(options.headers || {});
  const res = await nativeFetch(`${API_BASE}${path}`, {
    ...options,
    headers
  });
  if (res.status === 401) {
    forceLogoutWithMessage();
  }
  if (!res.ok) {
    const rawMsg = await res.text();
    showFriendlyError(res.status, rawMsg || `请求失败：HTTP ${res.status}`);
    throw new Error(rawMsg || `请求失败：HTTP ${res.status}`);
  }
  return res.text();
}

function forceLogoutWithMessage(message) {
  const msg = message || friendlyError(401, '').msg;
  sessionStorage.removeItem('user');
  sessionStorage.removeItem('sessionToken');
  showToast(`🔐 ${msg}`, 'error');
  const login = document.getElementById('login-screen');
  const app = document.getElementById('app-root');
  if (login) login.style.display = 'flex';
  if (app) app.style.display = 'none';
  throw new Error(msg);
}

async function aiRequest(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const res = await nativeFetch(`${AI_GATEWAY_BASE}${path}`, {
    ...options,
    headers
  });
  if (res.status === 401) {
    forceLogoutWithMessage();
  } else if (res.status === 403) {
    showFriendlyError(res.status, '无权限调用 AI 模型服务');
  } else if (res.status === 413) {
    showFriendlyError(res.status, 'AI 请求体过大');
  } else if (res.status === 502 || res.status === 504) {
    showFriendlyError(res.status, 'AI 模型服务暂时不可用或模型超时');
  }
  return readAiGatewayResponse(res);
}

async function aiGatewayPost(path, payload={}) {
  return aiRequest(path, {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

async function aiGatewayGet(path) {
  return aiRequest(path);
}
