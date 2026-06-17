# Agent 工具白名单详细说明

## 目录

- [1. 概述](#1-概述)
- [2. 工具分类体系](#2-工具分类体系)
- [3. READ_TOOLS（只读工具）](#3-read_tools只读工具)
- [4. AI_TOOLS（AI 能力工具）](#4-ai_toolsai-能力工具)
- [5. SONIC_TOOLS（Sonic 集成工具）](#5-sonic_toolssonic-集成工具)
- [6. TASK_TOOLS（任务管理工具）](#6-task_tools任务管理工具)
- [7. CONFIRM_TOOLS（确认工具）](#7-confirm_tools确认工具)
- [8. 权限模型](#8-权限模型)
- [9. 工具调用流程](#9-工具调用流程)

---

## 1. 概述

Agent 工具白名单定义了 Agent 可调用的全部工具，包含 26 个工具，分为 5 个类别。每个工具标注了风险等级、是否写操作、是否需要人工确认，确保 Agent 在不同运行模式下的操作安全。

工具注册表定义于：
- Task Server 端：`task_server/services/agent_service.py` → `AGENT_TOOLS`
- AI Gateway 端：`ai-gateway/agent/agent-tools.js` → `createAgentTools()`

---

## 2. 工具分类体系

| 类别 | 标识 | 风险等级范围 | 说明 |
|------|------|-------------|------|
| READ | `READ` | low | 只读查询，不修改任何数据 |
| AI | `AI` | low | AI 生成/分析，产出草稿但不直接修改 |
| SONIC | `SONIC` | low ~ high | Sonic 平台操作，同步/执行含写操作 |
| TASK | `TASK` | low ~ high | 任务管理，创建/执行/修复 |
| CONFIRM | `CONFIRM` | medium ~ high | 人工确认操作，高风险动作必须经此确认 |

---

## 3. READ_TOOLS（只读工具）

### 3.1 list_cases

| 属性 | 值 |
|------|-----|
| **名称** | `list_cases` |
| **标题** | 读取用例列表 |
| **类别** | READ |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：** 无

**输出说明：**
```json
{
  "modules": { "模块名": ["文件1.yaml", "文件2.yaml"] },
  "ok": true
}
```

**实现：** 调用 Task Server `GET /api/modules`

---

### 3.2 read_yaml

| 属性 | 值 |
|------|-----|
| **名称** | `read_yaml` |
| **标题** | 读取 YAML 文件 |
| **类别** | READ |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `module` | string | 是 | 模块名 |
| `file` | string | 是 | 文件名 |

**输出说明：**
```json
{
  "yaml": "yaml 内容字符串",
  "ok": true
}
```

**实现：** 调用 Task Server `GET /api/yaml?module=X&file=Y`

---

### 3.3 list_jobs

| 属性 | 值 |
|------|-----|
| **名称** | `list_jobs` |
| **标题** | 读取执行记录 |
| **类别** | READ |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：** 无

**输出说明：** Job 列表

**实现：** 调用 Task Server `GET /api/jobs`

---

### 3.4 read_report

| 属性 | 值 |
|------|-----|
| **名称** | `read_report` |
| **标题** | 读取执行报告 |
| **类别** | READ |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `job_id` | string | 是 | Job ID |

**输出说明：** 报告内容

---

### 3.5 read_sonic_result

| 属性 | 值 |
|------|-----|
| **名称** | `read_sonic_result` |
| **标题** | 读取 Sonic 执行结果 |
| **类别** | SONIC |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `result_id` | string | 否 | 结果 ID |

---

### 3.6 list_runners

| 属性 | 值 |
|------|-----|
| **名称** | `list_runners` |
| **标题** | 读取 Runner 列表 |
| **类别** | READ |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：** 无

**输出说明：** Runner 列表与在线设备信息

---

### 3.7 read_model_strategy

| 属性 | 值 |
|------|-----|
| **名称** | `read_model_strategy` |
| **标题** | 读取模型策略 |
| **类别** | READ |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：** 无

**输出说明：** 当前 Action → Provider 路由配置

---

## 4. AI_TOOLS（AI 能力工具）

### 4.1 analyze_goal

| 属性 | 值 |
|------|-----|
| **名称** | `analyze_goal` |
| **标题** | 分析测试目标 |
| **类别** | AI |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `goal` | string | 是 | 测试目标描述 |
| `requirement` | string | 否 | 需求描述 |
| `testCase` | string | 否 | 测试用例 |

**输出说明：** 分析结果

---

### 4.2 generate_cases

| 属性 | 值 |
|------|-----|
| **名称** | `generate_cases` |
| **标题** | 生成测试用例 |
| **类别** | AI |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `moduleName` | string | 否 | 模块名 |
| `requirement` | string | 否 | 需求描述 |

**输出说明：**
```json
{
  "cases": "AI 生成的用例 JSON 字符串"
}
```

**实现：** 调用 AI Gateway `POST /ai/generate-case`

---

### 4.3 generate_yaml

| 属性 | 值 |
|------|-----|
| **名称** | `generate_yaml` |
| **标题** | 生成 YAML |
| **类别** | AI |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `appName` | string | 否 | 应用名 |
| `platform` | string | 否 | 平台（默认 android） |
| `testCase` | string | 否 | 测试用例描述 |

**输出说明：**
```json
{
  "yaml": "AI 生成的 Midscene YAML 内容"
}
```

**实现：** 调用 AI Gateway `POST /ai/generate-yaml`，自动去除 Markdown 围栏

---

### 4.4 analyze_failure

| 属性 | 值 |
|------|-----|
| **名称** | `analyze_failure` |
| **标题** | 分析失败原因 |
| **类别** | AI |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `taskName` | string | 否 | 任务名 |
| `yaml` | string | 否 | YAML 内容 |
| `log` | string | 否 | 日志 |
| `screenshotDesc` | string | 否 | 截图描述 |

**输出说明：**
```json
{
  "analysis": "AI 分析结果，含 failureType、possibleReasons、suggestions"
}
```

**实现：** 调用 AI Gateway `POST /ai/analyze-failure`

---

### 4.5 generate_repair_draft

| 属性 | 值 |
|------|-----|
| **名称** | `generate_repair_draft` |
| **标题** | 生成修复草稿 |
| **类别** | AI |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `yaml` | string | 是 | 原始 YAML |
| `failureAnalysis` | string | 否 | 失败分析 |
| `requirement` | string | 否 | 需求描述 |

**输出说明：**
```json
{
  "yaml": "AI 修复后的 YAML",
  "requiresConfirmation": true,
  "message": "自动修复 YAML 尚未完全接入"
}
```

**实现：** 调用 AI Gateway `POST /ai/optimize-yaml`

---

### 4.6 generate_bug_draft

| 属性 | 值 |
|------|-----|
| **名称** | `generate_bug_draft` |
| **标题** | 生成缺陷草稿 |
| **类别** | AI |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `taskName` | string | 否 | 任务名 |
| `envInfo` | string | 否 | 环境信息 |
| `failureAnalysis` | string | 否 | 失败分析 |

**输出说明：**
```json
{
  "bug": "AI 生成的缺陷草稿文本",
  "requiresConfirmation": true,
  "message": "已生成缺陷草稿；autoCreateBug 默认关闭，提交飞书前必须人工确认"
}
```

**实现：** 调用 AI Gateway `POST /ai/generate-bug`

---

### 4.7 generate_summary

| 属性 | 值 |
|------|-----|
| **名称** | `generate_summary` |
| **标题** | 生成总结报告 |
| **类别** | AI |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `runId` | string | 否 | Agent Run ID |
| `results` | object | 否 | 执行结果 |

**输出说明：**
```json
{
  "summary": "AI 生成的总结报告",
  "ok": true
}
```

---

## 5. SONIC_TOOLS（Sonic 集成工具）

### 5.1 sonic_list_projects

| 属性 | 值 |
|------|-----|
| **名称** | `sonic_list_projects` |
| **标题** | 查询 Sonic 项目 |
| **类别** | SONIC |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：** 无

**输出说明：** Sonic 项目列表

**实现：** 调用 Task Server `GET /api/sonic/projects`

---

### 5.2 sonic_list_suites

| 属性 | 值 |
|------|-----|
| **名称** | `sonic_list_suites` |
| **标题** | 查询 Sonic 测试套 |
| **类别** | SONIC |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `projectId` | string | 否 | Sonic 项目 ID |

**输出说明：** Sonic 测试套列表

**实现：** 调用 Task Server `GET /api/sonic/suites?projectId=X`

---

### 5.3 sonic_sync_case

| 属性 | 值 |
|------|-----|
| **名称** | `sonic_sync_case` |
| **标题** | 同步单条用例到 Sonic |
| **类别** | SONIC |
| **风险等级** | medium |
| **写操作** | 是 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `module` | string | 是 | 模块名 |
| `file` | string | 是 | 文件名 |
| `projectId` | string | 否 | 目标项目 ID |

**输出说明：** 同步结果（含 Sonic case ID）

**实现：** 调用 Task Server `POST /api/sonic/publish`

**风险说明：** 单条同步属于中风险操作，AUTO_SAFE 下可自动执行但会审计记录。

---

### 5.4 sonic_sync_batch

| 属性 | 值 |
|------|-----|
| **名称** | `sonic_sync_batch` |
| **标题** | 批量同步 Sonic 用例 |
| **类别** | SONIC |
| **风险等级** | high |
| **写操作** | 是 |
| **需要确认** | 是 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `module` | string | 否 | 模块名 |
| `files` | array | 否 | 文件列表 |
| `projectId` | string | 否 | 目标项目 ID |

**输出说明：** 批量同步结果

**风险说明：** 批量操作属于高风险，任何模式下都必须人工确认。

---

### 5.5 sonic_run_suite

| 属性 | 值 |
|------|-----|
| **名称** | `sonic_run_suite` |
| **标题** | 执行 Sonic 测试套 |
| **类别** | SONIC |
| **风险等级** | medium |
| **写操作** | 是 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `suiteId` | string | 否 | 测试套 ID |
| `projectId` | string | 否 | 项目 ID |

**输出说明：** 执行结果（含 jobId）

**实现：** 调用 Task Server `POST /api/sonic/publish`

---

### 5.6 sonic_read_result

| 属性 | 值 |
|------|-----|
| **名称** | `sonic_read_result` |
| **标题** | 读取 Sonic 执行结果 |
| **类别** | SONIC |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `resultId` | string | 否 | 结果 ID |

**输出说明：** Sonic 执行结果详情

---

### 5.7 sonic_read_report

| 属性 | 值 |
|------|-----|
| **名称** | `sonic_read_report` |
| **标题** | 读取 Sonic 报告 |
| **类别** | SONIC |
| **风险等级** | low |
| **写操作** | 否 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `resultId` | string | 否 | 结果 ID |

**输出说明：** Sonic 报告 URL 与内容

---

## 6. TASK_TOOLS（任务管理工具）

### 6.1 create_runner_job

| 属性 | 值 |
|------|-----|
| **名称** | `create_runner_job` |
| **标题** | 创建 Runner 任务 |
| **类别** | TASK |
| **风险等级** | medium |
| **写操作** | 是 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `module` | string | 是 | 模块名 |
| `file` | string | 是 | YAML 文件名 |
| `deviceId` | string | 否 | 目标设备 |
| `runnerId` | string | 否 | 目标 Runner |

**输出说明：**
```json
{
  "ok": true,
  "jobId": "job_xxx"
}
```

**实现：** 调用 Task Server `POST /api/jobs`

---

### 6.2 run_midscene_task

| 属性 | 值 |
|------|-----|
| **名称** | `run_midscene_task` |
| **标题** | 执行 Midscene 任务 |
| **类别** | TASK |
| **风险等级** | medium |
| **写操作** | 是 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `module` | string | 是 | 模块名 |
| `file` | string | 是 | 文件名 |
| `targetTaskName` | string | 否 | 目标 Task 名 |
| `runMode` | string | 否 | 运行模式（test/baseline） |

**输出说明：** 执行结果

**实现：** 调用 Task Server `POST /api/run-request`

---

### 6.3 retry_failed_job

| 属性 | 值 |
|------|-----|
| **名称** | `retry_failed_job` |
| **标题** | 重跑失败任务 |
| **类别** | TASK |
| **风险等级** | medium |
| **写操作** | 是 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `jobId` | string | 是 | 失败的 Job ID |

**输出说明：** 新 Job 信息

**实现：** 调用 Task Server `POST /api/jobs/{id}/retry`

---

### 6.4 save_repair_draft

| 属性 | 值 |
|------|-----|
| **名称** | `save_repair_draft` |
| **标题** | 保存修复草稿 |
| **类别** | TASK |
| **风险等级** | low |
| **写操作** | 是 |
| **需要确认** | 否 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `jobId` | string | 否 | Job ID |
| `module` | string | 否 | 模块名 |
| `file` | string | 否 | 文件名 |
| `fixedYaml` | string | 否 | 修复后的 YAML |

**输出说明：** 保存的草稿信息

**风险说明：** 仅保存草稿，不直接修改基线文件，属于低风险。

---

### 6.5 apply_repair_after_confirm

| 属性 | 值 |
|------|-----|
| **名称** | `apply_repair_after_confirm` |
| **标题** | 应用修复（需确认） |
| **类别** | TASK |
| **风险等级** | high |
| **写操作** | 是 |
| **需要确认** | 是 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `draftId` | string | 是 | 修复草稿 ID |
| `confirmApply` | boolean | 是 | 必须为 true |
| `confirmRisk` | boolean | 条件 | 含高风险关键词时必须为 true |

**输出说明：** 应用结果（含备份信息、YAML 校验结果）

**风险说明：** 直接覆盖基线 YAML 文件，属于高风险操作。应用前会自动备份原始文件到 `versions/` 目录。

---

## 7. CONFIRM_TOOLS（确认工具）

确认工具本身不直接执行业务操作，而是作为高风险操作的人工确认关口。

### 7.1 confirm_high_risk_action

| 属性 | 值 |
|------|-----|
| **名称** | `confirm_high_risk_action` |
| **标题** | 确认高风险动作 |
| **类别** | CONFIRM |
| **风险等级** | high |
| **写操作** | 是 |
| **需要确认** | 是 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `action` | string | 是 | 动作描述 |
| `riskHits` | array | 否 | 命中的风险关键词 |

**输出说明：** 确认结果

---

### 7.2 confirm_apply_yaml

| 属性 | 值 |
|------|-----|
| **名称** | `confirm_apply_yaml` |
| **标题** | 确认应用 YAML |
| **类别** | CONFIRM |
| **风险等级** | high |
| **写操作** | 是 |
| **需要确认** | 是 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `draftId` | string | 是 | 修复草稿 ID |
| `yamlPreview` | string | 否 | YAML 预览 |

**输出说明：** 确认结果

---

### 7.3 confirm_rerun

| 属性 | 值 |
|------|-----|
| **名称** | `confirm_rerun` |
| **标题** | 确认重新执行 |
| **类别** | CONFIRM |
| **风险等级** | medium |
| **写操作** | 是 |
| **需要确认** | 是 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `jobId` | string | 是 | Job ID |
| `reason` | string | 否 | 重跑原因 |

**输出说明：** 确认结果

---

### 7.4 confirm_baseline_update

| 属性 | 值 |
|------|-----|
| **名称** | `confirm_baseline_update` |
| **标题** | 确认覆盖基线 |
| **类别** | CONFIRM |
| **风险等级** | high |
| **写操作** | 是 |
| **需要确认** | 是 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `module` | string | 是 | 模块名 |
| `file` | string | 是 | 文件名 |
| `confirmRisk` | boolean | 是 | 必须为 true |

**输出说明：** 确认结果

**风险说明：** 覆盖基线是不可逆操作（虽有版本备份），任何模式下都必须人工确认。

---

### 7.5 confirm_bug_submit

| 属性 | 值 |
|------|-----|
| **名称** | `confirm_bug_submit` |
| **标题** | 确认提交缺陷 |
| **类别** | CONFIRM |
| **风险等级** | medium |
| **写操作** | 是 |
| **需要确认** | 是 |

**输入参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `bugDraftId` | string | 是 | 缺陷草稿 ID |
| `feishuWebhook` | string | 否 | 飞书 Webhook 地址 |

**输出说明：** 确认结果

---

## 8. 权限模型

### 8.1 权限等级

| 等级 | 允许的类别 | 最大自动风险 |
|------|-----------|-------------|
| `READ_ONLY` | READ | low |
| `AUTO_SAFE` | READ, AI, SONIC, TASK, CONFIRM | medium |
| `FULL_AUTO` | READ, AI, SONIC, TASK, CONFIRM | medium |

### 8.2 风险等级排序

```python
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
```

### 8.3 自动执行规则

| 运行模式 | low 工具 | medium 工具 | high 工具 |
|----------|---------|------------|----------|
| `SEMI_AUTO` | 需确认 | 需确认 | 需确认 |
| `AUTO_SAFE` | 自动执行 | 自动执行（审计） | 需确认 |
| `FULL_AUTO` | 自动执行 | 自动执行 | **仍需确认** |

> **关键约束：** 即使在 `FULL_AUTO` 模式下，high 风险工具仍然必须人工确认。

---

## 9. 工具调用流程

### 9.1 Agent 编排器调用链

```
startAgentRun(input)
  │
  ├── applyAgentPolicy()     → 策略评估，可能降级模式
  ├── generateCase()         → AI 生成用例
  ├── generateYaml()         → AI 生成 YAML
  ├── validateYaml()         → 本地校验
  │   └── 失败 → WAIT_CONFIRM
  ├── saveYamlAsset()        → 保存草稿
  │   └── 锁定基线 → WAIT_CONFIRM
  ├── [SEMI_AUTO] → WAIT_CONFIRM_RUN
 