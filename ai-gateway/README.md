# AI Gateway

`ai-gateway` 是功夫豆测试平台旁边的独立 AI 网关服务，用来把 Task 平台里的模型调用统一收口。

支持千问 OpenAI 兼容接口，也支持 HighwayAPI / Jiekou 的 OpenAI 兼容接口。

## 能力

- `GET /health`：健康检查
- `POST /ai/generate-case`：生成测试用例
- `POST /ai/generate-yaml`：生成 Midscene YAML
- `POST /ai/validate-yaml`：校验 Midscene YAML
- `POST /ai/analyze-failure`：分析自动化失败原因
- `POST /ai/generate-bug`：生成飞书缺陷单内容
- `GET /ai/providers`：查看服务端可用模型 Provider（不返回 Key）
- `POST /ai/providers/test`：测试 provider 是否可用
- `GET /ai/model-router`：查看各能力当前使用哪个 Provider
- `POST /ai/model-router`：保存各能力到 Provider 的路由
- `POST /ai/optimize-yaml`：根据失败分析生成修复 YAML 草稿
- `POST /ai/chat`：通用轻量对话入口
- `POST /agent/run`：启动受控 Agent 运行
- `GET /agent/runs`：查看 Agent 运行列表
- `GET /agent/runs/:runId`：查看单个 Agent 运行详情
- `POST /agent/runs/:runId/confirm`：记录人工确认
- `POST /agent/runs/:runId/cancel`：取消 Agent 运行

Agent 支持三种模式：

- `SEMI_AUTO`：生成、校验、保存草稿后等待人工确认执行。
- `AUTO_SAFE`：推荐默认模式；自动生成、校验、保存草稿，接入 Task/Sonic 工具后才自动执行。
- `FULL_AUTO`：默认关闭，只允许白名单任务；命中高风险关键词会自动降级。

## 本地启动

```bash
cd ai-gateway
npm install
cp .env.example .env
vi .env
npm start
```

`.env` 至少需要配置：

```bash
PORT=8090
QWEN_API_KEY=你的 DashScope API Key
HIGHWAY_API_KEY=你的 HighwayAPI Key
LOG_ENABLED=true
AI_GATEWAY_MOCK=0
```

模型清单放在 `config/providers.json`，能力路由放在 `config/model-router.json`。`.env` 只放真实 Key，不要把 API Key 写入代码、前端、日志或提交文件。

默认路由：

- `generate_case`、`generate_yaml`、`analyze_failure`、`optimize_yaml`、`agent_plan`、`generate_bug`：`qwen_plus`

Highway / DeepSeek 等 Provider 会继续保留在服务端配置中，需要时可以在平台「模型配置」里按能力手动切换。

## 快速验证

```bash
curl http://127.0.0.1:8090/health
```

校验 YAML：

```bash
curl -X POST http://127.0.0.1:8090/ai/validate-yaml \
  -H "Content-Type: application/json" \
  -d '{
    "yaml": "android:\n  tasks:\n    - name: 关节龙打印\n      flow:\n        - sleep: 1000\n        - aiTap: 首页搜索入口\n        - aiAssert: 搜索结果出现关节龙"
  }'
```

查看 Provider 和模型路由：

```bash
curl http://127.0.0.1:8090/ai/providers
curl http://127.0.0.1:8090/ai/model-router
```

测试指定 Provider：

```bash
curl -X POST http://127.0.0.1:8090/ai/providers/test \
  -H "Content-Type: application/json" \
  -d '{"providerId":"qwen_plus"}'
```

预期 `output` 包含 `gateway ok`。

保存模型路由：

```bash
curl -X POST http://127.0.0.1:8090/ai/model-router \
  -H "Content-Type: application/json" \
  -d '{
    "generate_case": "qwen_plus",
    "generate_yaml": "qwen_plus",
    "analyze_failure": "qwen_plus",
    "optimize_yaml": "qwen_plus",
    "agent_plan": "qwen_plus",
    "generate_bug": "qwen_plus"
  }'
```

## HighwayAPI GPT-5 Mini 注意事项

`providers.json` 里如果配置了 `temperatureLocked: true` 和 `fixedTemperature: 1`，网关会强制固定 temperature，并跳过不兼容的采样参数，避免 HighwayAPI 的 `gpt-5-mini` 返回 400。

生成 YAML：

```bash
curl -X POST http://127.0.0.1:8090/ai/generate-yaml \
  -H "Content-Type: application/json" \
  -d '{
    "appName": "智小白3D APP",
    "platform": "android",
    "testCase": "搜索关节龙，点击去打印，等待切片进度到 100%，确认打印"
  }'
```

本地不调用真实模型的 Agent 接口自检：

```bash
AI_GATEWAY_MOCK=1 PORT=18090 QWEN_API_KEY=test-key npm start
curl -X POST http://127.0.0.1:18090/agent/run \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "回归关节龙打印流程",
    "mode": "AUTO_SAFE",
    "appName": "智小白3D APP",
    "platform": "android",
    "testCase": "搜索关节龙，点击去打印，等待进度条到100%，确认打印",
    "autoRun": false,
    "autoRepair": false,
    "autoCreateBug": false
  }'
```

`AI_GATEWAY_MOCK=1` 只能用于本地接口检查，线上必须保持 `0`。

## YAML 校验规则

当前第一版强约束：

- 顶层必须包含 `android` 或 `ios`
- `${platform}.tasks` 必须是数组
- 每个 task 必须包含 `name` 和 `flow`
- `flow` 必须是数组
- 允许的 flowItem：
  - `sleep`
  - `aiTap`
  - `aiAction`
  - `aiAssert`
- 禁止的 flowItem：
  - `repeat`
  - `click`
  - `tap`
  - `wait`
  - `loop`
- `sleep` 必须是数字

## 调用日志

AI 调用日志写入：

```text
logs/ai-calls.jsonl
```

日志字段：

- `id`
- `time`
- `action`
- `provider`
- `model`
- `success`
- `durationMs`
- `inputPreview`
- `outputPreview`
- `error`

日志只记录输入/输出预览，最多 500 字，不记录 API Key。

Agent 运行日志写入：

```text
logs/agent-runs.jsonl
```

每一步记录 `runId`、`traceId`、`state`、`tool`、`durationMs`、输入/输出预览和错误摘要。

## 受控 Agent 约束

- 第一阶段只执行到生成、校验和人工确认。
- Agent 调用后端工具，不直接点击 Sonic 页面。
- `autoCreateBug` 默认关闭，飞书提单前必须人工确认。
- `maxRetries` 默认 2，最多 3。
- 不允许 Agent 删除资产或覆盖人工锁定基线。
- Sonic/Midscene 相关改动必须先参考 `docs/OFFICIAL_REFERENCE_SOURCES.md`。
- `FULL_AUTO` 只允许 `config/agent-whitelist.json` 里的稳定白名单任务。
- 包含确认打印、支付、删除、覆盖基线、提交订单等风险词时，强制降级为 `SEMI_AUTO`。

## 白名单配置

```text
config/agent-whitelist.json
```

默认 `fullAutoEnabled=false`。夜间回归前，需要先把稳定用例加入 `allowedTasks`，再开启全自动。

## Task 平台接入建议

先不要一次性替换 Task 后端全部千问调用。

推荐迁移顺序：

1. Task 后端增加 `AI_GATEWAY_BASE_URL=http://127.0.0.1:8090`
2. 先把 YAML 校验迁到 `/ai/validate-yaml`
3. 再把简单的失败分析迁到 `/ai/analyze-failure`
4. 最后迁移需求解析和 YAML 生成

这样不会影响当前 Sonic 基线回归链路。
