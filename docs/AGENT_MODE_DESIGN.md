# Controlled QA Agent Design

目标不是一上来做完全自主 Agent，而是做受控 Agent：Agent 可以规划和调用工具，但高风险动作需要人工确认。

## 分层

```text
Task 管理平台
  -> Agent Orchestrator
    -> AI Gateway
    -> YAML Validator
    -> 用例资产库
    -> Sonic/Task 执行接口
    -> 日志/报告接口
    -> 飞书通知/缺陷接口
```

## 第一阶段目标

先在 `ai-gateway` 内增加 Agent Orchestrator 骨架，接口先跑通状态机、日志和人工确认，不直接改现有 Task/Sonic 基线链路。

第一阶段已经落地到 `ai-gateway/agent/`：

- `agent-state-machine.js`：状态、限次和入参归一化。
- `agent-memory.js`：第一阶段内存态运行记录。
- `agent-logger.js`：`logs/agent-runs.jsonl` 追踪日志。
- `agent-tools.js`：工具封装；高风险工具先返回人工确认。
- `agent-policy.js`：模式、白名单、风险关键词和限次策略。
- `agent-orchestrator.js`：受控编排。

## 上线节奏

1. 先完成 AI Gateway：模型调用、YAML 生成、YAML 校验、失败分析、缺陷草稿。
2. 加 Agent Orchestrator：状态机、工具调用、日志、人工确认和取消。
3. 先跑 `AUTO_SAFE`：自动生成、自动校验、自动保存草稿；执行和修复按策略受控。
4. 把稳定用例加入白名单：只允许稳定、低风险、可回滚的用例进入白名单。
5. 开启 `FULL_AUTO` 夜间回归：仅限白名单任务，且风险关键词不命中。

## Agent 模式

- `SEMI_AUTO`：半自动。生成、校验和保存草稿后，在执行前停到 `WAIT_CONFIRM_RUN`。
- `AUTO_SAFE`：安全自动。推荐默认模式；可自动保存草稿，可在接入 Task/Sonic 工具后自动执行，但失败建 Bug 只生成草稿。
- `FULL_AUTO`：全自动。默认关闭，只允许白名单任务；命中高风险关键词会降级。

## Agent 状态

- `START`
- `ANALYZE_REQUIREMENT`
- `GENERATE_CASE`
- `GENERATE_YAML`
- `VALIDATE_YAML`
- `SAVE_ASSET`
- `WAIT_CONFIRM_RUN`
- `RUN_TASK`
- `WAIT_RESULT`
- `ANALYZE_RESULT`
- `ANALYZE_FAILURE`
- `OPTIMIZE_YAML`
- `VALIDATE_REPAIRED_YAML`
- `RERUN_TASK`
- `GENERATE_BUG_DRAFT`
- `WAIT_CONFIRM_BUG`
- `WAIT_CONFIRM`
- `CREATE_FEISHU_TICKET`
- `GENERATE_REPORT`
- `NOTIFY_FEISHU`
- `FINISH`
- `FAILED`
- `CANCELLED`

## 工具

- `generateCase()`
- `generateYaml()`
- `validateYaml()`
- `saveYamlAsset()`
- `runSonicTask()`
- `getTaskStatus()`
- `getTaskLog()`
- `analyzeFailure()`
- `optimizeYaml()`
- `generateBug()`
- `createFeishuTicket()`
- `notifyFeishu()`

## 安全限制

1. `maxRetries` 默认 2，最多 3。
2. `autoCreateBug` 默认 false。
3. 创建飞书缺陷前必须支持人工确认。
4. 不允许 Agent 删除已有用例资产。
5. 不允许 Agent 覆盖人工锁定的基线 YAML。
6. 每一步都写 trace 日志。
7. 每次工具调用记录 `runId`、`stepName`、`inputPreview`、`outputPreview`、`durationMs`、`success`、`error`。
8. 失败必须进入 `FAILED` 或 `WAIT_CONFIRM`，不允许无限循环。
9. `FULL_AUTO` 必须通过 `config/agent-whitelist.json` 白名单。
10. 命中确认打印、支付、删除、覆盖基线、提交订单等高风险关键词时，强制降级为 `SEMI_AUTO`。

## 接口

- `POST /agent/run`
- `GET /agent/runs/:runId`
- `POST /agent/runs/:runId/confirm`
- `POST /agent/runs/:runId/cancel`

## 半自动策略

第一版默认执行到 `VALIDATE_YAML` 或 `WAIT_CONFIRM`。

- `autoRun=false`：生成并校验后等待人工确认。
- `autoRun=true` 但未配置 Task/Sonic 工具：进入 `WAIT_CONFIRM`，提示需要接入工具。
- `autoRepair=true`：失败分析后最多自动修复 2 次。
- `autoCreateBug=false`：只生成缺陷草稿，不自动提单。

## 第一阶段接口行为

`POST /agent/run` 默认执行：

```text
START
  -> GENERATE_CASE
  -> GENERATE_YAML
  -> VALIDATE_YAML
  -> SAVE_ASSET
  -> WAIT_CONFIRM_RUN 或 RUN_TASK
```

如果 `autoRun=true`，也不会直接触发 Sonic；当前会进入 `WAIT_CONFIRM` 并提示需要接入 Task 后端执行工具。

`POST /agent/runs/:runId/confirm` 当前只记录人工确认，不继续写资产或跑 Sonic。后续接入 Task 后端工具后，再根据确认类型推进 `SAVE_ASSET`、`RUN_TASK` 或 `CREATE_FEISHU_TICKET`。

`POST /agent/runs/:runId/cancel` 会进入 `CANCELLED`。

## 后续接入顺序

1. 接入 Task 平台保存 YAML 资产接口，但禁止覆盖人工锁定基线。
2. 接入 Task 平台执行接口，由 Task 再触发 Sonic/Midscene，不让 Agent 直接操作 Sonic UI。
3. 接入执行结果轮询和日志摘要。
4. 接入失败分析与最多 2 次自动修复。
5. 接入飞书缺陷草稿和人工确认提交。

## 参考资料

涉及 Sonic 或 Midscene 的实现，先看 `docs/OFFICIAL_REFERENCE_SOURCES.md`。
