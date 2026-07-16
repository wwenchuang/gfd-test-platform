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
- `GET /ai/providers`：查看服务端可用模型 Provider（非千问通道实时读取上游 `/models`，不返回 Key）
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
AI_PROVIDER_CATALOG_TIMEOUT_MS=5000
AI_PROVIDER_CATALOG_CACHE_MS=60000
AI_PROVIDER_CATALOG_ALLOW_REFRESH=0
AI_GATEWAY_JSON_LIMIT=20mb
AI_CALL_TIMEOUT_MS=90000
AI_CALL_FALLBACK_RESERVE_MS=15000
AI_GATEWAY_MOCK=0
```

`config/providers.json` 保存通道、Key 环境变量名和兼容种子模型，不再作为非千问模型全集。`catalogMode=live` 的 OpenAI 兼容通道通过上游 `/models` 获取当前账号可见模型；千问保持 `catalogMode=static` 独立配置。能力路由放在 `config/model-router.json`。`.env` 只放真实 Key，不要把 API Key 写入代码、前端、日志或提交文件。

实时目录默认缓存 60 秒，单次上游请求最多等待 5 秒。上游目录超时或失败时，接口返回 `catalog.errors`，同时保留已配置种子模型作为 `configured_fallback`；目录失败不会把整个模型页变成不可用。

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

`/ai/providers` 中的 `catalogSource` 为 `live`、`static` 或 `configured_fallback`。新发现模型使用可逆的 `catalog_*` provider ID，可以像旧 provider ID 一样保存到模型路由并在服务重启后解析。上游 `/models` 只提供模型 ID 和基本可见性，具体模型是否支持当前 Chat Completions 请求仍需用 `/ai/providers/test` 真实验证。

Agent 显式选择模型时，各文本阶段先使用该 `providerId + model`。只有超时、限流、服务端不可用、模型不可用或不支持当前图像输入时才尝试配置的备用路由；接口返回 `providerId`、`model`、`fallbackUsed`、`fallbackIndex` 和 `fallbackReason`。带图调用可传 `fallbackModelConfig` 指定已验证的视觉模型，Task Server 默认使用 `MIDSCENE_AI_GATEWAY_VISION_FALLBACK_PROVIDER_ID=qwen_plus` 与当前 `DASHSCOPE_VL_MODEL`。Python 服务不会在显式选模失败后再静默直连另一模型。

调用方通过 `timeoutMs` 传入阶段总预算。显式选模最多尝试首选模型和一个备用模型；Gateway 会为备用模型保留有界时间，且关闭 SDK 内部隐式重试，避免 Python/HTTP 先超时后 Gateway 仍在后台占用模型连接。未传 `timeoutMs` 时使用 `AI_CALL_TIMEOUT_MS`，备用保留上限由 `AI_CALL_FALLBACK_RESERVE_MS` 控制。

HTTP 200 但 assistant `content` 为空或只有空白不算成功。Gateway 会把它记录为 provider failure，并在总预算内尝试唯一备用模型；响应和 JSONL 日志保留 `finishReason`、汇总 token usage 与空答原因。Task Server 同时区分空 HTTP body、非 JSON body 和成功包裹中的空模型内容。OpenAI Chat Completions 文档说明 `max_completion_tokens` 同时包含可见输出与 reasoning tokens，因此必须先保存 finish/token 证据，再决定是否调整具体模型的推理参数：<https://platform.openai.com/docs/api-reference/chat/create>。

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
