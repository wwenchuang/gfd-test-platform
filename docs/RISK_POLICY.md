# 风险策略文档

## 目录

- [1. 概述](#1-概述)
- [2. 风险关键词](#2-风险关键词)
- [3. 风险等级定义](#3-风险等级定义)
- [4. 运行模式与自动执行规则](#4-运行模式与自动执行规则)
- [5. 失败类型处理规则](#5-失败类型处理规则)
- [6. 禁止自动执行的动作列表](#6-禁止自动执行的动作列表)
- [7. 策略评估流程](#7-策略评估流程)
- [8. 修复草稿安全约束](#8-修复草稿安全约束)

---

## 1. 概述

风险策略是 Agent 自动化执行的安全护栏，确保 Agent 在不同运行模式下不会执行超出安全边界的操作。策略引擎定义在 `ai-gateway/agent/agent-policy.js`，同时在 Task Server 端 `task_server/schemas.py` 和 `task_server/services/agent_service.py` 也有对应的常量定义。

核心原则：

1. **安全优先**：宁可多确认，不可误执行
2. **分级管控**：低风险自动、中风险审计、高风险确认
3. **不可绕过**：即使 `FULL_AUTO` 模式，高风险操作仍需人工确认
4. **可追溯**：所有操作记录到 `agent-tool-calls.json` 和 `agent-runs.jsonl`

---

## 2. 风险关键词

### 2.1 高风险关键词（阻塞列表）

以下关键词出现在 Agent 目标、需求或 YAML 内容中时，**强制降级为 `SEMI_AUTO`**：

| 关键词 | 场景 |
|--------|------|
| `确认打印` | 3D 打印确认步骤，可能导致真实耗材消耗 |
| `开始打印` | 触发真实打印动作 |
| `支付` | 涉及资金操作 |
| `删除` | 数据删除，不可逆 |
| `覆盖基线` | 修改已验证的基线 YAML |
| `格式化` | 可能导致数据清空 |
| `清空` | 批量数据删除 |
| `解绑` | 设备/账号解绑，影响连接 |
| `重置` | 恢复出厂或清空配置 |
| `批量同步` | 批量操作影响范围大 |
| `批量执行` | 批量触发执行，资源消耗大 |

### 2.2 中风险关键词

以下关键词出现时，风险等级评估为 `MEDIUM`：

| 关键词 | 场景 |
|--------|------|
| `同步` | 数据同步操作 |
| `执行` | 触发任务执行 |
| `上传` | 文件上传 |
| `连接` | 设备连接操作 |
| `设备` | 设备相关操作 |

### 2.3 白名单配置

高风险关键词和允许的任务白名单配置在 `ai-gateway/config/agent-whitelist.json`：

```json
{
  "fullAutoEnabled": false,
  "allowedTasks": [
    "首页打开检查",
    "搜索模型流程",
    "文档打印入口检查"
  ],
  "blockedKeywords": [
    "删除", "确认打印", "清空", "提交订单",
    "支付", "解绑", "开始打印", "覆盖基线",
    "格式化", "重置"
  ]
}
```

> **注意：** `fullAutoEnabled` 默认为 `false`，即 `FULL_AUTO` 模式全局未开启。

---

## 3. 风险等级定义

### 3.1 LOW — 低风险

| 属性 | 值 |
|------|-----|
| **描述** | 读取、查询、截图、文本校验、AI 分析、生成草稿 |
| **AUTO_SAFE** | 可自动执行 |
| **FULL_AUTO** | 可自动执行 |
| **需要审计** | 否 |

**包含的操作类型：**
- 所有 READ 类工具（`list_cases`、`read_yaml`、`list_jobs` 等）
- 所有 AI 类工具（`generate_cases`、`generate_yaml`、`analyze_failure` 等）
- SONIC 只读工具（`sonic_list_projects`、`sonic_read_result` 等）
- 保存草稿（`save_repair_draft`）

### 3.2 MEDIUM — 中风险

| 属性 | 值 |
|------|-----|
| **描述** | 同步单条 Sonic、触发低风险执行、文件上传、设备连接 |
| **AUTO_SAFE** | 可自动执行但审计 |
| **FULL_AUTO** | 可自动执行 |
| **需要审计** | 是 |

**包含的操作类型：**
- 单条 Sonic 同步（`sonic_sync_case`）
- Sonic 测试套执行（`sonic_run_suite`）
- 创建 Runner 任务（`create_runner_job`）
- 执行 Midscene 任务（`run_midscene_task`）
- 重跑失败任务（`retry_failed_job`）
- 确认重新执行（`confirm_rerun`）
- 确认提交缺陷（`confirm_bug_submit`）

### 3.3 HIGH — 高风险

| 属性 | 值 |
|------|-----|
| **描述** | 支付、删除、覆盖基线、真实打印、硬件危险、批量操作 |
| **AUTO_SAFE** | 必须人工确认（WAIT_CONFIRM） |
| **FULL_AUTO** | **仍必须人工确认** |
| **需要审计** | 是 |

**包含的操作类型：**
- 批量 Sonic 同步（`sonic_sync_batch`）
- 应用修复草稿（`apply_repair_after_confirm`）
- 确认高风险动作（`confirm_high_risk_action`）
- 确认应用 YAML（`confirm_apply_yaml`）
- 确认覆盖基线（`confirm_baseline_update`）
- 任何命中高风险关键词的操作

---

## 4. 运行模式与自动执行规则

### 4.1 运行模式

| 模式 | 标识 | 说明 |
|------|------|------|
| 半自动 | `SEMI_AUTO` | 每个关键步骤都需人工确认 |
| 安全自动 | `AUTO_SAFE` | 低/中风险自动执行，高风险需确认 |
| 全自动 | `FULL_AUTO` | 低/中风险自动执行，高风险仍需确认 |

### 4.2 AUTO_SAFE 规则

```
shouldWaitConfirmAutoSafe(riskLevel, failureType):
  - riskLevel === 'HIGH'         → 必须确认
  - failureType === 'PRODUCT_BUG' → 必须确认（不自动修 YAML）
  - failureType === 'ENV_ISSUE'   → 必须确认（不自动修 YAML）
  - failureType === 'UNKNOWN'     → 必须确认（人工复核）
  - 其他                          → 可自动执行
```

### 4.3 FULL_AUTO 规则

```
shouldWaitConfirmAutoFull(riskLevel, failureType):
  - riskLevel === 'HIGH'         → 必须确认（与 AUTO_SAFE 一致）
  - failureType === 'PRODUCT_BUG' → 必须确认
  - failureType === 'ENV_ISSUE'   → 必须确认
  - failureType === 'UNKNOWN'     → 必须确认
  - 其他                          → 可自动执行
```

> **关键区别：** `AUTO_SAFE` 和 `FULL_AUTO` 在确认规则上几乎一致。`FULL_AUTO` 的额外权限主要体现在：
> - 允许 `autoCreateBug: true`（AUTO_SAFE 下 `autoCreateBug` 始终为 false）
> - 白名单任务范围内可自动执行

### 4.4 模式降级

```
请求 FULL_AUTO：
  ├── fullAutoEnabled === false  → 降级为 AUTO_SAFE
  ├── 任务不在 allowedTasks 白名单 → 降级为 AUTO_SAFE
  └── 命中 blockedKeywords       → 降级为 SEMI_AUTO（无论请求什么模式）

请求 AUTO_SAFE：
  └── 命中 blockedKeywords       → 降级为 SEMI_AUTO
```

### 4.5 maxRetries 安全上限

```python
maxRetries: Math.max(0, Math.min(3, Math.floor(input.maxRetries || 2)))
```

- 默认 2 次，硬上限 3 次
- 超过 3 次自动截断

---

## 5. 失败类型处理规则

### 5.1 故障类型定义

| 类型 | 标识 | 说明 |
|------|------|------|
| 脚本问题 | `SCRIPT_ISSUE` | YAML 定位或流程逻辑问题 |
| 产品缺陷 | `PRODUCT_BUG` | 产品本身存在 Bug |
| 环境问题 | `ENV_ISSUE` | 运行环境异常（网络、设备等） |
| 未知原因 | `UNKNOWN` | 无法归类 |

### 5.2 处理策略

| 故障类型 | YAML 修复 | 缺陷草稿 | 环境 | 人工 |
|----------|----------|----------|------|------|
| `SCRIPT_ISSUE` | 允许生成修复 YAML | 不需要 | 不需要 | 可选 |
| `PRODUCT_BUG` | **禁止** | 生成缺陷草稿 | 不需要 | **必须** |
| `ENV_ISSUE` | **禁止** | 不需要 | 给出环境建议 | **必须** |
| `UNKNOWN` | **禁止** | 不需要 | 不需要 | **必须** |

### 5.3 代码实现

```javascript
// 允许生成 YAML 修复的条件
function canGenerateYamlRepair(failureType) {
  return failureType === 'SCRIPT_ISSUE';
}
```

```python
# Task Server 端同样约束
REPAIRABLE_FAILURE_TYPES = {"SCRIPT_ISSUE"}
NON_REPAIRABLE_FAILURE_TYPES = {"PRODUCT_BUG", "ENV_ISSUE", "UNKNOWN"}
```

### 5.4 自动修复限制

- 最多自动修复 **2 次**（`maxAutoRepair`）
- 禁止覆盖锁定基线
- 每次修复后必须通过 YAML 校验

---

## 6. 禁止自动执行的动作列表

以下动作**在任何模式下都不允许自动执行**，必须经过人工确认：

| 动作 | 原因 | 对应工具/操作 |
|------|------|--------------|
| 真实打印 | 消耗耗材、硬件操作 | 含「确认打印」「开始打印」关键词 |
| 支付操作 | 涉及资金 | 含「支付」关键词 |
| 删除数据 | 不可逆 | 含「删除」「清空」关键词 |
| 覆盖基线 | 破坏已验证的测试基准 | `confirm_baseline_update`、`apply_repair_after_confirm` |
| 格式化/重置 | 数据丢失风险 | 含「格式化」「重置」关键词 |
| 设备解绑 | 影响设备连接 | 含「解绑」关键词 |
| 批量同步 | 影响范围大 | `sonic_sync_batch` |
| 批量执行 | 资源消耗大 | 含「批量执行」关键词 |
| 提交订单 | 涉及交易 | 含「提交订单」关键词 |
| 飞书缺陷创建 | 外部系统不可撤回 | `createFeishuTicket` |
| 飞书通知发送 | 外部系统不可撤回 | `notifyFeishu` |

---

## 7. 策略评估流程

### 7.1 评估入口

策略评估在 `startAgentRun` 时触发：

```javascript
const policy = applyAgentPolicy(input, whitelist);
```

### 7.2 评估流程

```
输入: { mode, goal, requirement, testCase, yaml, maxRetries, ... }
  │
  ├── 1. 标准化模式 (normalizeMode)
  │     AUTO_FULL → FULL_AUTO, 其他校验
  │
  ├── 2. 检查 maxRetries 上限
  │     超过 3 → 自动截断并记录原因
  │
  ├── 3. FULL_AUTO 降级检查
  │     ├── fullAutoEnabled === false → 降级 AUTO_SAFE
  │     └── 任务不在 allowedTasks → 降级 AUTO_SAFE
  │
  ├── 4. 高风险关键词检查
  │     搜索 goal + requirement + testCase + yaml
  │     命中 blockedKeywords → 强制降级 SEMI_AUTO
  │
  └── 5. 输出策略结果
        {
          requestedMode,     // 用户请求的模式
          effectiveMode,     // 实际生效的模式
          reasons,           // 降级原因列表
          blockedHits,       // 命中的风险关键词
          autoRun,           // 是否自动执行
          autoRepair,        // 是否自动修复
          autoCreateBug,     // 是否自动创建缺陷
          autoOverwriteBaseline, // 始终 false
          maxRetries         // 截断后的重试次数
        }
```

### 7.3 策略结果示例

**正常请求（无降级）：**
```json
{
  "requestedMode": "AUTO_SAFE",
  "effectiveMode": "AUTO_SAFE",
  "reasons": [],
  "blockedHits": [],
  "autoRun": true,
  "autoRepair": true,
  "autoCreateBug": false,
  "autoOverwriteBaseline": false,
  "maxRetries": 2
}
```

**降级请求（命中风险关键词）：**
```json
{
  "requestedMode": "FULL_AUTO",
  "effectiveMode": "SEMI_AUTO",
  "reasons": [
    "FULL_AUTO 未全局开启，已降级为 AUTO_SAFE",
    "命中高风险关键词：确认打印、开始打印，已强制降级为 SEMI_AUTO"
  ],
  "blockedHits": ["确认打印", "开始打印"],
  "autoRun": false,
  "autoRepair": false,
  "autoCreateBug": false,
  "autoOverwriteBaseline": false,
  "maxRetries": 2
}
```

---

## 8. 修复草稿安全约束

### 8.1 应用前校验

修复草稿应用前必须满足以下条件：

1. **草稿状态**：必须为 `DRAFTED` 或 `WAIT_CONFIRM`
2. **显式确认**：`confirmApply=true`
3. **高风险确认**：如果草稿包含风险关键词命中（`riskHits`），还须 `confirmRisk=true`
4. **YAML 校验**：修复后的 YAML 必须通过结构校验
5. **可执行性校验**：修复后的 YAML 必须通过可执行性校验

### 8.2 版本备份

应用修复前自动备份原始 YAML：

```python
backup = save_file_version(module, file, reason="before_repair_draft_apply")
```

备份保存在 `/opt/midscene-learning/versions/` 目录，可通过 `GET /api/file/history` 查询历史版本。

### 8.3 禁止自动覆盖基线

```javascript
// agent-tools.js
async saveYamlAsset(input) {
  if (input.lockedBaseline || input.autoOverwriteBaseline) {
    return {
      state: AGENT_STATES.WAIT_CONFIRM,
      requiresConfirmation: true,
      saved: false,
      message: '检测到锁定基线或覆盖基线请求，Agent 不允许自动覆盖，必须人工处理。'
    };
  }
  // ... 正常保存草稿
}
```

```python
# config.py
ENABLE_AUTOMATIC_BASELINE_REPAIR = env_int("MIDSCENE_ENABLE_AUTO_BASELINE_REPAIR", 0) != 0
# 默认关闭，生产环境不建议开启
```

### 8.4 不可修复的故障类型

以下故障类型禁止生成 YAML 修复，只能走缺陷草稿或人工流程：

```python
NON_REPAIRABLE_FAILURE_TYPES = {"PRODUCT_BUG", "ENV_ISSUE", "UNKNOWN"}
```

修复草稿的 `riskHits` 字段会记录修复内容中命中的高风险关键词，应用时必须额外确认。
