import {AGENT_STATES} from './agent-state-machine.js';
import fs from 'fs/promises';
import path from 'path';
import {fileURLToPath} from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ASSET_DIR = path.join(__dirname, '..', 'agent-assets');

// ── Task Server 基地址 ─────────────────────────────────────────────
const TASK_SERVER_BASE = process.env.TASK_SERVER_URL || 'http://127.0.0.1:8091';

// ── 通用的 task server 请求封装 ─────────────────────────────────────
export async function taskServerRequest(pathSuffix, options = {}) {
  const { method = 'GET', body, timeout = 30000 } = options;
  const url = `${TASK_SERVER_BASE}${pathSuffix}`;
  const fetchOptions = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) fetchOptions.body = JSON.stringify(body);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const res = await fetch(url, { ...fetchOptions, signal: controller.signal });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`Task server ${method} ${pathSuffix} failed: ${res.status} ${text.slice(0, 200)}`);
    }
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

// ── AI Gateway 本地路由请求封装 ─────────────────────────────────────
const AI_GATEWAY_BASE = process.env.AI_GATEWAY_URL || 'http://127.0.0.1:8090';

export async function aiGatewayRequest(pathSuffix, options = {}) {
  const { method = 'POST', body, timeout = 120000 } = options;
  const url = `${AI_GATEWAY_BASE}${pathSuffix}`;
  const fetchOptions = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) fetchOptions.body = JSON.stringify(body);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const res = await fetch(url, { ...fetchOptions, signal: controller.signal });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`AI Gateway ${method} ${pathSuffix} failed: ${res.status} ${text.slice(0, 200)}`);
    }
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

function safeName(value, fallback = 'agent-yaml') {
  return String(value || fallback)
    .replace(/[\\/:*?"<>|\s]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 80) || fallback;
}

export function createAgentTools(deps = {}) {
  const callAi = deps.callAi;
  const validateMidsceneYaml = deps.validateMidsceneYaml;

  return {
    // ── 用例列表：GET /api/modules ───────────────────────────────
    async listCases(input = {}) {
      try {
        const data = await taskServerRequest('/api/modules');
        return { modules: data.modules || data, ok: true };
      } catch (err) {
        return { modules: [], ok: false, error: String(err?.message || err) };
      }
    },

    // ── 读取 YAML 内容：GET /api/yaml?module=X&file=Y ───────────
    async readYaml(input = {}) {
      const { module, file } = input;
      const qs = new URLSearchParams({ module: module || '', file: file || '' }).toString();
      try {
        const data = await taskServerRequest(`/api/yaml?${qs}`);
        return { yaml: data.yaml || data.content || '', ok: true };
      } catch (err) {
        return { yaml: '', ok: false, error: String(err?.message || err) };
      }
    },

    // ── 生成用例 ──────────────────────────────────────────────────
    async generateCase(input) {
      const {output} = await callAi('generate_case', {
        moduleName: input.moduleName || '',
        requirement: input.requirement || input.testCase || input.goal || '',
      });
      return {cases: output};
    },

    // ── 生成 YAML ─────────────────────────────────────────────────
    async generateYaml(input) {
      const {output} = await callAi('generate_yaml', {
        appName: input.appName || '',
        platform: input.platform || 'android',
        testCase: input.testCase || input.requirement || input.goal || '',
      }, {stripFence: true});
      return {yaml: output};
    },

    // ── 校验 YAML ─────────────────────────────────────────────────
    async validateYaml(input) {
      return validateMidsceneYaml(input.yaml || '');
    },

    // ── 保存 YAML 草稿 ───────────────────────────────────────────
    async saveYamlAsset(input) {
      if (input.lockedBaseline || input.autoOverwriteBaseline) {
        return {
          state: AGENT_STATES.WAIT_CONFIRM,
          requiresConfirmation: true,
          saved: false,
          message: '检测到锁定基线或覆盖基线请求，Agent 不允许自动覆盖，必须人工处理。',
        };
      }
      await fs.mkdir(ASSET_DIR, {recursive: true});
      const fileName = `${Date.now()}-${safeName(input.taskName || input.goal || input.appName)}.yaml`;
      const filePath = path.join(ASSET_DIR, fileName);
      await fs.writeFile(filePath, String(input.yaml || ''), 'utf8');
      return {
        state: AGENT_STATES.SAVE_ASSET,
        saved: true,
        assetId: fileName,
        filePath,
        message: '已保存为 Agent 草稿 YAML；后续接入 Task 资产接口后再同步到正式 YAML 库。',
        yamlPreview: String(input.yaml || '').slice(0, 500),
      };
    },

    // ── 失败分析：POST /ai/analyze-failure (通过本地 AI Gateway) ──
    async analyzeFailure(input) {
      const {output} = await callAi('analyze_failure', {
        taskName: input.taskName || input.goal || '',
        yaml: input.yaml || '',
        log: input.log || '',
        screenshotDesc: input.screenshotDesc || '',
      });
      return {analysis: output};
    },

    // ── Sonic 项目列表：GET /api/sonic/projects ──────────────────
    async sonicListProjects(input = {}) {
      try {
        const data = await taskServerRequest('/api/sonic/projects');
        return { projects: data.projects || data, ok: true };
      } catch (err) {
        return {
          state: AGENT_STATES.WAIT_CONFIRM,
          requiresConfirmation: true,
          ok: false,
          message: `Sonic 项目列表获取失败：${err?.message || err}。后续必须通过 Task 后端接口触发，不允许 Agent 直接操作 Sonic 页面。`,
        };
      }
    },

    // ── Sonic 测试套列表：GET /api/sonic/suites?projectId=X ─────
    async sonicListSuites(input = {}) {
      const { projectId } = input;
      const qs = projectId ? `?projectId=${encodeURIComponent(projectId)}` : '';
      try {
        const data = await taskServerRequest(`/api/sonic/suites${qs}`);
        return { suites: data.suites || data, ok: true };
      } catch (err) {
        return { suites: [], ok: false, error: String(err?.message || err) };
      }
    },

    // ── 运行 Sonic 任务 ──────────────────────────────────────────
    async runSonicTask(input = {}) {
      // 高风险操作：必须通过 Task 后端接口触发
      try {
        const data = await taskServerRequest('/api/sonic/publish', {
          method: 'POST',
          body: input,
        });
        return { ok: true, jobId: data.jobId || data.id, state: AGENT_STATES.RUN_TASK };
      } catch (err) {
        return {
          state: AGENT_STATES.WAIT_CONFIRM,
          requiresConfirmation: true,
          message: `Sonic 执行请求失败：${err?.message || err}。后续必须通过 Task 后端接口触发，不允许 Agent 直接操作 Sonic 页面。`,
        };
      }
    },

    // ── 创建 Runner Job：POST /api/jobs ──────────────────────────
    async createRunnerJob(input = {}) {
      try {
        const data = await taskServerRequest('/api/jobs', {
          method: 'POST',
          body: input,
        });
        return { ok: true, jobId: data.jobId || data.id, ...data };
      } catch (err) {
        return { ok: false, error: String(err?.message || err) };
      }
    },

    // ── 任务状态查询：GET /api/jobs/:jobId ───────────────────────
    async getTaskStatus(input = {}) {
      const { jobId } = input;
      if (!jobId) return { ok: false, error: '缺少 jobId' };
      try {
        const data = await taskServerRequest(`/api/jobs/${encodeURIComponent(jobId)}`);
        return { ok: true, ...data };
      } catch (err) {
        return {
          state: AGENT_STATES.WAIT_CONFIRM,
          message: `Task 状态查询失败：${err?.message || err}`,
          ok: false,
        };
      }
    },

    // ── 任务日志查询：GET /api/jobs/:jobId/log ───────────────────
    async getTaskLog(input = {}) {
      const { jobId } = input;
      if (!jobId) return { ok: false, error: '缺少 jobId' };
      try {
        const data = await taskServerRequest(`/api/jobs/${encodeURIComponent(jobId)}/log`);
        return { ok: true, log: data.log || data, ...data };
      } catch (err) {
        return {
          state: AGENT_STATES.WAIT_CONFIRM,
          message: `Task 日志查询失败：${err?.message || err}`,
          ok: false,
        };
      }
    },

    // ── 优化 YAML（自动修复） ────────────────────────────────────
    async optimizeYaml(input = {}) {
      // 自动修复 YAML：限制最多自动修复 2 次，禁止覆盖锁定基线
      const {output} = await callAi('optimize_yaml', {
        yaml: input.yaml || '',
        failureAnalysis: input.failureAnalysis || '',
        requirement: input.requirement || '',
      }, {stripFence: true});
      return {
        yaml: output,
        requiresConfirmation: true,
        message: '自动修复 YAML 尚未完全接入。后续会限制最多自动修复 2 次，并禁止覆盖锁定基线。',
      };
    },

    // ── 生成缺陷草稿 ─────────────────────────────────────────────
    async generateBug(input) {
      const {output} = await callAi('generate_bug', {
        taskName: input.taskName || input.goal || '',
        envInfo: input.envInfo || '',
        failureAnalysis: input.failureAnalysis || '',
      });
      return {
        bug: output,
        requiresConfirmation: true,
        message: '已生成缺陷草稿；autoCreateBug 默认关闭，提交飞书前必须人工确认。',
      };
    },

    // ── 创建飞书工单 ─────────────────────────────────────────────
    async createFeishuTicket(input = {}) {
      // 飞书缺陷创建：必须先展示草稿并等待人工确认
      return {
        state: AGENT_STATES.WAIT_CONFIRM,
        requiresConfirmation: true,
        message: '飞书缺陷创建尚未接入。即使接入后，也必须先展示草稿并等待人工确认。',
      };
    },

    // ── 飞书通知 ─────────────────────────────────────────────────
    async notifyFeishu(input = {}) {
      // 飞书通知：需要复用现有机器人配置并记录发送结果
      return {
        state: AGENT_STATES.WAIT_CONFIRM,
        requiresConfirmation: true,
        message: '飞书通知尚未接入 Agent；后续需要复用现有机器人配置并记录发送结果。',
      };
    },

    // ── AI 规划：POST /api/agent/plan ────────────────────────────
    async agentPlan(input = {}) {
      try {
        const data = await aiGatewayRequest('/api/agent/plan', { body: input });
        return { plan: data.plan || data, ok: true };
      } catch (err) {
        return { plan: null, ok: false, error: String(err?.message || err) };
      }
    },

    // ── 生成总结：POST /api/agent/generate-summary ───────────────
    async generateSummary(input = {}) {
      try {
        const data = await aiGatewayRequest('/api/agent/generate-summary', { body: input });
        return { summary: data.summary || data, ok: true };
      } catch (err) {
        return { summary: null, ok: false, error: String(err?.message || err) };
      }
    },

    // ── 生成缺陷草稿（AI Gateway 路由） ────────────────────────
    async generateBugDraft(input = {}) {
      try {
        const data = await aiGatewayRequest('/api/agent/generate-bug-draft', { body: input });
        return { bugDraft: data, ok: true };
      } catch (err) {
        return { bugDraft: null, ok: false, error: String(err?.message || err) };
      }
    },

    // ── KNOWLEDGE TOOLS ───────────────────────────────────────────

    // ── 查询页面知识：GET /api/knowledge/pages?app=X&page=Y ──────
    async queryPageKnowledge(input = {}) {
      const { appId, pageName } = input;
      if (!appId || !pageName) {
        return { ok: false, error: '缺少 appId 或 pageName', elements: [], waitConditions: [], commonPopups: [] };
      }
      try {
        const qs = new URLSearchParams({ app: appId, page: pageName }).toString();
        const data = await taskServerRequest(`/api/knowledge/pages?${qs}`);
        return { ...data, ok: true };
      } catch (err) {
        return { ok: false, error: String(err?.message || err), elements: [], waitConditions: [], commonPopups: [] };
      }
    },

    // ── 查询失败知识：GET /api/knowledge/failures?log=xxx ────────
    async queryFailureKnowledge(input = {}) {
      const { logText } = input;
      if (!logText) {
        return { ok: false, error: '缺少 logText', matches: [] };
      }
      try {
        const data = await taskServerRequest('/api/knowledge/failures?log=' + encodeURIComponent(logText));
        return { ...data, ok: true };
      } catch (err) {
        return { ok: false, error: String(err?.message || err), matches: [] };
      }
    },

    // ── 查询用例历史：GET /api/knowledge/cases?file=xxx ──────────
    async queryCaseHistory(input = {}) {
      const { yamlFile } = input;
      if (!yamlFile) {
        return { ok: false, error: '缺少 yamlFile', totalExecutions: 0, recentResults: [], repairHistory: [] };
      }
      try {
        const data = await taskServerRequest('/api/knowledge/cases?file=' + encodeURIComponent(yamlFile));
        return { ...data, ok: true };
      } catch (err) {
        return { ok: false, error: String(err?.message || err), totalExecutions: 0, recentResults: [], repairHistory: [] };
      }
    },
  };
}
