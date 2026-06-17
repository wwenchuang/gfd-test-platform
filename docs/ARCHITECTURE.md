# Midscene Task Platform 整体架构文档

## 目录

- [1. 系统总览](#1-系统总览)
- [2. 系统组成](#2-系统组成)
- [3. 请求流向](#3-请求流向)
- [4. Agent 工作流](#4-agent-工作流)
- [5. 模块边界](#5-模块边界)
- [6. 数据存储](#6-数据存储)
- [7. 部署架构](#7-部署架构)
- [8. 技术栈概览](#8-技术栈概览)

---

## 1. 系统总览

Midscene Task Platform 是一个**全自动 Agent 测试工作台**，核心目标是将人工驱动的功能集合后台升级为以 Agent 为中心的极简工作流。平台将 AI 模型服务、自动化执行引擎（Sonic）、本地 Runner 客户端和飞书协同平台整合为统一的测试自动化解决方案。

```
┌─────────────────────────────────────────────────────────────┐
│                    Midscene Task Platform                    │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  Nginx   │  │  Task    │  │    AI    │  │  Runner  │   │
│  │  (8088)  │  │  Server  │  │  Gateway │  │  Client  │   │
│  │          │  │  (8091)  │  │  (8090)  │  │          │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │
│       │             │             │              │          │
│       └──────┬──────┘──────┬──────┘              │          │
│              │             │                     │          │
│         ┌────▼────┐   ┌───▼────┐          ┌─────▼─────┐    │
│         │ Sonic   │   │ AI模型 │          │ Midscene  │    │
│         │ Server  │   │ 路由   │          │ Playwright│    │
│         └─────────┘   └────────┘          └───────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 系统组成

| 组件 | 语言 | 默认端口 | 职责 |
|------|------|----------|------|
| **Task Server** | Python | 8091 | 用例管理、Job 调度、Runner 管理、报告、修复草稿、飞书集成 |
| **AI Gateway** | Node.js | 8090 | 多模型路由、Agent 编排、状态机、风险策略、工具注册 |
| **Nginx** | - | 8088 | 静态文件服务、反向代理 `/api/` → Task Server、`/ai-gateway/` → AI Gateway |
| **Runner** | Python | - | Midscene YAML 执行、报告上传、心跳上报 |
| **Sonic** | Java | - | 用例同步、任务触发、报告回传（第三方系统） |

### 2.1 Task Server

主入口文件为 `midscene-upload.py`（Python HTTP Server），正在逐步向 `task_server/` 模块化架构迁移：

```
task_server/
├── app.py              # 启动入口，注册 legacy handlers
├── router.py           # 路由分发，新路由装饰器注册 + legacy fallback
├── auth.py             # Session Token 鉴权
├── config.py           # 运行时配置（路径、环境变量、常量）
├── storage.py          # 原子写入、TTL 缓存、安全路径拼接
├── schemas.py          # 业务常量与枚举定义
├── response.py         # 统一响应工具
├── repair_service.py   # 修复草稿独立服务
├── sonic_service.py    # Sonic 集成服务
└── services/
    ├── agent_service.py      # Agent 运行框架
    ├── job_service.py        # Job 管理服务
    ├── runner_service.py     # Runner 注册与心跳
    ├── yaml_service.py       # YAML 解析与规范化
    ├── report_service.py     # 报告索引与清理
    ├── repair_service.py     # 修复草稿
    ├── feishu_service.py     # 飞书集成
    ├── knowledge_service.py  # 知识库管理
    └── platform_service.py   # 平台健康监控
```

### 2.2 AI Gateway

基于 Express.js 的 AI 模型网关：

```
ai-gateway/
├── server.js               # Express 主服务，路由定义
├── agent/
│   ├── agent-orchestrator.js  # Agent 编排器
│   ├── agent-state-machine.js # Agent 状态机
│   ├── agent-policy.js        # 风险策略引擎
│   ├── agent-tools.js         # 工具注册表
│   ├── agent-memory.js        # 运行时内存存储
│   └── agent-logger.js        # Agent 日志
├── config/
│   ├── providers.json         # 模型 Provider 配置
│   ├── model-router.json      # Action → Provider 路由策略
│   └── agent-whitelist.json   # Agent 白名单与风险关键词
├── prompts/                   # AI Prompt 模板
└── validators/
    └── midscene-yaml-validator.js  # Midscene YAML 校验器
```

---

## 3. 请求流向

### 3.1 用户请求流

```
用户浏览器
    │
    ▼
Nginx (:8088)
    ├── /              → 静态文件 (task-manager.html)
    ├── /api/*         → Task Server (:8091)
    └── /ai-gateway/*  → AI Gateway (:8090)
```

### 3.2 任务执行流

```
用户 → POST /api/run-request
         │
         ▼
    Task Server 创建 Job（pending）
         │
         ▼
    Runner 轮询 GET /api/runner/jobs/next（Runner Token 鉴权）
         │
         ▼
    Task Server 分发 Job（status: running）
         │
         ▼
    Runner 执行 Midscene YAML（Playwright）
         │
         ├── 进度 → POST /api/runner/jobs/{id}/progress
         ├── 报告 → POST /api/report/chunk + chunk-finish
         └── 结果 → POST /api/runner/jobs/{id}/result
              │
              ▼
    Task Server 更新 Job 状态 → 飞书通知（可选）
```

### 3.3 Agent 请求流

```
用户 → POST /ai-gateway/agent/run
         │
         ▼
    AI Gateway: 策略评估 → 状态机执行
         │
         ├── generateCase     → AI 模型（generate_case）
         ├── generateYaml     → AI 模型（generate_yaml）
         ├── validateYaml     → 本地校验器
         ├── saveYamlAsset    → 本地文件系统
         ├── runSonicTask     → Task Server /api/sonic/publish
         ├── analyzeFailure   → AI 模型（analyze_failure）
         └── optimizeYaml    → AI 模型（optimize_yaml）
              │
              ▼
    Agent Run 完成 → 用户确认 → 继续/取消
```

### 3.4 Sonic 集成流

```
Task Server ←→ Sonic Server
    │                     │
    ├── 同步用例 ──────────→ POST Sonic /controller/testCases/batchSave
    ├── 查询项目 ──────────→ GET  Sonic /controller/projects
    ├── 查询测试套 ────────→ GET  Sonic /controller/suites
    └── 回调通知 ←────────── POST /api/sonic/suite-complete
                       │
                       ▼
                  飞书卡片通知
```

---

## 4. Agent 工作流

### 4.1 标准化步骤

Agent 执行遵循以下 14 步标准化流程：

```
IDLE → PLAN → MATCH_CASES → GENERATE_YAML → VALIDATE_YAML
  → RISK_REVIEW → SYNC_SONIC → RUN_TASK → COLLECT_REPORT
  → ANALYZE_FAILURE → GENERATE_REPAIR → WAIT_CONFIRM → RERUN
  → GENERATE_SUMMARY → GENERATE_BUG_DRAFT → DONE
```

异常分支：`DONE` → `FAILED` → `CANCELLED`

### 4.2 状态机

Agent 状态机定义在 `agent-state-machine.js` 中，核心状态：

| 状态 | 说明 |
|------|------|
| `START` | 初始状态，策略评估 |
| `ANALYZE_REQUIREMENT` | 分析测试需求 |
| `GENERATE_CASE` | AI 生成测试用例 |
| `GENERATE_YAML` | AI 生成 Midscene YAML |
| `VALIDATE_YAML` | 校验 YAML 结构与语义 |
| `SAVE_ASSET` | 保存草稿 YAML |
| `WAIT_CONFIRM_RUN` | 等待人工确认执行 |
| `RUN_TASK` | 触发 Sonic/Runner 执行 |
| `WAIT_RESULT` | 等待执行结果 |
| `ANALYZE_FAILURE` | AI 分析失败原因 |
| `OPTIMIZE_YAML` | AI 修复 YAML |
| `GENERATE_BUG_DRAFT` | 生成缺陷草稿 |
| `WAIT_CONFIRM` | 等待人工确认（高风险操作） |
| `FINISH` / `FAILED` / `CANCELLED` | 终态 |

### 4.3 跳过逻辑

状态机支持条件跳过：

- `ANALYZE_FAILURE`：无失败 → 直接跳到 `GENERATE_SUMMARY`
- `GENERATE_REPAIR`：无需修复 → 跳到 `GENERATE_SUMMARY`
- `WAIT_CONFIRM`：无需确认 → 跳到 `RERUN`
- `RERUN`：无需重跑 → 跳到 `GENERATE_SUMMARY`
- `GENERATE_BUG_DRAFT`：无缺陷草稿 → 跳到 `DONE`

### 4.4 运行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `maxRetries` | 2 | 最大重试次数（硬上限 3） |
| `maxAutoRepair` | 2 | 最大自动修复次数 |
| `mode` | `AUTO_SAFE` | 运行模式：`SEMI_AUTO` / `AUTO_SAFE` / `FULL_AUTO` |

---

## 5. 模块边界

### 5.1 Task Server

| 模块 | 职责 | 关键文件 |
|------|------|----------|
| 用例管理 | YAML 文件 CRUD、模块列表、优先级统计 | `yaml_service.py` |
| Job 调度 | 创建/分发/重试/取消 Job、超时恢复 | `job_service.py` |
| Runner 管理 | 注册、心跳、设备列表、任务分发 | `runner_service.py` |
| 报告 | 索引构建、分片上传、清理策略 | `report_service.py` |
| 修复草稿 | 创建/应用/拒绝修复草稿、风险评估 | `repair_service.py` |
| 飞书集成 | Webhook 验证、卡片通知、缺陷草稿 | `feishu_service.py` |
| Agent 运行 | Agent Run CRUD、工具调用记录 | `agent_service.py` |
| 知识库 | 页面管理、截图分析、Figma 集成 | `knowledge_service.py` |
| 平台健康 | 子系统状态聚合、环境检查 | `platform_service.py` |
| Sonic 集成 | 项目/测试套查询、用例同步、回调处理 | `sonic_service.py` |

### 5.2 AI Gateway

| 模块 | 职责 |
|------|------|
| 多模型路由 | `model-router.json` 配置 Action → Provider 映射 |
| Agent 编排 | `agent-orchestrator.js` 协调工具调用链 |
| 状态机 | `agent-state-machine.js` 定义步骤与转移规则 |
| 风险策略 | `agent-policy.js` 评估风险等级、自动降级 |
| 工具注册 | `agent-tools.js` 封装 Task Server/AI/Sonic 工具 |

### 5.3 Runner

| 职责 | 说明 |
|------|------|
| Midscene YAML 执行 | 通过 Playwright 执行自动化测试 |
| 报告上传 | 分片上传 HTML 报告到 Task Server |
| 心跳上报 | 定期发送 Runner 状态与设备列表 |
| 跨平台支持 | `mac-midscene-runner.py` / `windows-midscene-runner.py` |

### 5.4 Sonic

| 职责 | 说明 |
|------|------|
| 用例同步 | Task Server 将 YAML 同步为 Sonic 测试用例 |
| 任务触发 | 通过 Sonic API 触发自动化测试套执行 |
| 报告回传 | Sonic 完成后回调 Task Server，触发飞书通知 |

---

## 6. 数据存储

### 6.1 存储方式

当前采用 **JSON 文件** 作为持久化存储，目录结构：

```
/opt/midscene-learning/
├── jobs.json                    # Job 执行记录
├── agent-runs.json              # Agent 运行历史
├── agent-tool-calls.json        # Agent 工具调用记录
├── repair-drafts.json           # 修复草稿
├── runners.json                 # Runner 注册表
├── task-apps.json               # 应用配置（含飞书 Webhook）
├── task-meta.json               # 用例元信息
├── baseline-page-refs.json      # 基线页面引用
├── sonic-sync.json              # Sonic 同步状态
├── sonic-suite-results.json     # Sonic 测试套结果
├── sonic-token-cache.json       # Sonic Token 缓存
├── feishu-drafts.json           # 飞书缺陷草稿
├── versions/                    # YAML 版本历史
└── runs/                        # 执行日志目录

/opt/midscene-tasks/             # YAML 用例文件（按模块目录组织）
/opt/midscene-reports/           # HTML 测试报告
/opt/midscene-assets/            # 上传的资产文件
/opt/midscene-knowledge/         # 知识库（按 app_package 组织）
/opt/midscene-generate-jobs/     # AI 生成任务记录
```

### 6.2 缓存策略

采用进程内 TTL 内存缓存（`read_json_cached`），详见 [STORAGE.md](./STORAGE.md)。

### 6.3 原子写入

所有 JSON 写入采用"先写 tmp 再 rename"策略，详见 [STORAGE.md](./STORAGE.md)。

---

## 7. 部署架构

### 7.1 systemd 服务

Task Server 以 systemd 服务方式运行：

```ini
# /etc/systemd/system/midscene-task.service
[Unit]
Description=Midscene Task Platform
After=network-online.target

[Service]
Type=simple
User=midscene
Group=midscene
WorkingDirectory=/opt/midscene-task-platform
Environment=MIDSCENE_ENV_FILE=/opt/midscene.env
ExecStart=/usr/bin/env python3 /opt/midscene-task-platform/midscene-upload.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
```

### 7.2 Nginx 反向代理

```nginx
server {
    listen 80;
    server_name _;
    root /www/html;
    index task-manager.html;

    location / {
        try_files $uri $uri/ /task-manager.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8091;
        proxy_read_timeout 3600s;
    }

    location /ai-gateway/ {
        proxy_pass http://127.0.0.1:8090/;
        proxy_connect_timeout 60s;
        proxy_read_timeout 300s;
    }
}
```

### 7.3 环境配置

运行时配置集中在 `/opt/midscene.env`，支持的前缀：

- `DASHSCOPE_*` — AI 模型 API
- `OPENAI_*` — OpenAI 兼容 API
- `FEISHU_*` — 飞书集成
- `FIGMA_*` — Figma 设计解析
- `SONIC_*` — Sonic 自动化平台
- `MIDSCENE_*` — 平台核心配置
- `TASK_*` — Task Server 配置

### 7.4 Docker 部署

支持 Docker 容器化部署，通过 `deploy/sync-docker-web.sh` 同步前端资源。

---

## 8. 技术栈概览

| 层次 | 技术 |
|------|------|
| 后端 | Python 3 (stdlib HTTPServer) + Node.js 18+ (Express) |
| 前端 | 原生 HTML/CSS/JS |
| 自动化 | Playwright 1.60+ (Midscene) |
| AI | OpenAI 兼容 API（通义千问 Qwen、Highway GPT-5 Mini 等） |
| 执行引擎 | Sonic Server (Java) |
| 协同 | 飞书机器人 Webhook |
| 部署 | systemd + Nginx + Docker（可选） |
| 存储 | JSON 文件 + TTL 内存缓存 |
