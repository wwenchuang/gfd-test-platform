# MeterSphere Daily Execution Console Design

## Context

当前 `MeterSphere 执行` 页面把连接凭据、项目 ID、环境 ID 和三个 API 路径平铺为 11 个同级输入框。已保存的值会覆盖 placeholder，用户无法判断字段含义；Token 与 Access Key / Secret Key 同时出现，也无法判断应该填写哪一组。

页面右侧只读取平台本地已确认计划。没有计划时显示“暂无已确认计划”，但不说明计划从哪里来；技术日志还会生成“等待保存 MeterSphere 配置”的本地占位记录，即使实际连接已经正常。这个信息结构更像配置调试页，不适合日常执行人员。

本轮把页面调整为日常执行优先的执行台。连接和高级配置仍然保留，但从主操作区移入设置面板。业务、项目、环境、计划、运行状态和报告必须来自真实接口，不允许在前端写死 `3D业务` 或构造伪执行数据。

## Goals

- 用户进入页面后能立即判断当前是否可执行、正在执行什么、最近结果如何。
- 用户通过一个主操作完成“推送确认用例 -> 触发 MeterSphere 计划 -> 跟踪执行 -> 同步报告”。
- “业务”选择项由 MeterSphere 项目接口动态获取；当前显示名可以是 `3D业务`，但不能写入前端代码。
- 执行状态、阶段进度和技术日志只展示后端返回的真实数据。
- 连接配置、认证方式和高级 API 路径保持可编辑，但不占据日常执行页面的主体。
- 运行中的页面按固定频率更新，同时保留用户展开的技术日志和滚动位置。

## Non-Goals

- 不替代 MeterSphere 的用例库、计划管理或执行引擎。
- 不让浏览器直接访问 MeterSphere，也不把 Access Key、Secret Key 或 Token 下发前端。
- 不在本轮接入 Apifox Token 自动同步。接口资产仍沿用现有 OpenAPI 导入流程。
- 不修改现有 Agent、YAML、Runner、Sonic 或移动端执行链路。
- 不为单一 `3D业务` 项目写特殊逻辑。
- 不伪造尚未被 MeterSphere API 支持的“报告已同步”状态。

## Product Model

页面使用以下用户可见概念：

| 页面概念 | 数据归属 | 说明 |
| --- | --- | --- |
| 业务 | MeterSphere | 映射 MeterSphere Project；名称和 ID 均通过后端适配器读取 |
| 环境 | MeterSphere | 当前业务下可用的 API 测试环境 |
| 用例计划 | 本平台 | 由 OpenAPI 资产生成并已经人工确认的计划 |
| MeterSphere 计划 | MeterSphere | 平台计划推送后创建或绑定的执行计划 |
| 运行 | MeterSphere + 本平台映射 | MeterSphere 的真实运行 ID、状态和时间，平台保存映射与阶段事件 |
| 报告 | MeterSphere + 本平台归一化 | MeterSphere 原始结果经现有报告服务归一化后的结果 |

界面使用“业务”是为了符合日常执行人员的语言；接口和存储层继续使用 `project_id`，不新建含义重复的业务实体。

## Information Architecture

### 1. Execution Header

页面顶部是紧凑状态栏，不使用大面积说明卡：

- MeterSphere 连接状态：`连接正常`、`连接异常` 或 `未配置`。
- 当前业务和环境：来自实时元数据接口。
- 最近检查时间和请求耗时。
- 一个设置图标，用于打开连接与高级配置。
- 一个刷新图标，强制重新读取 MeterSphere 元数据和当前运行。

连接正常但能力不完整时，状态必须写成具体事实，例如：

```text
MeterSphere 已连接 · 业务：3D业务 · 执行能力待配置
缺少：用例推送接口、计划执行接口、报告查询接口
```

不能再显示“等待保存配置”这类与真实状态冲突的文字。

### 2. Daily Execution Area

主体显示平台本地已经确认的 API 测试计划。每行包含：

- 计划名称、接口数、用例数和确认时间。
- 绑定的 MeterSphere 计划，没有绑定时明确显示“首次执行时创建或选择”。
- 最近一次运行状态、开始时间、耗时和通过率。
- 主按钮 `推送并执行`。
- 次级操作菜单：仅推送、重新执行、查看历史、打开 MeterSphere。

主按钮只有在以下条件全部满足时可用：

- MeterSphere 连接检查成功。
- 已选择有效业务和环境。
- 用例推送、计划执行和报告查询能力均已配置。
- 平台计划为 `confirmed` 且至少包含一条用例。
- 当前计划没有另一条未结束的运行。

按钮禁用时必须在同一区域列出缺失条件，并提供唯一、可执行的下一步，例如 `配置执行接口`、`选择环境` 或 `去确认计划`。

### 3. Active Run

存在运行中的任务时，计划列表上方显示固定高度的当前运行区：

```text
推送用例 -> 触发计划 -> MeterSphere 执行 -> 同步报告
```

每个阶段只使用后端返回的 `waiting / running / succeeded / failed / skipped` 状态。当前阶段展示开始时间、已运行时间和最后更新时间。轮询不会重建整页，只更新状态区域。

若现有 MeterSphere API 只能确认“执行已触发”，页面停留在 `MeterSphere 执行中`，直到状态接口返回终态；不能根据前端计时推测成功。报告查询能力未配置时，执行终态后显示 `执行已结束，报告尚未同步`，不标记完整流程成功。

### 4. Empty States

空状态必须解释原因并给出一个主要动作：

- 没有 OpenAPI 资产：`尚未导入接口`，按钮 `去导入接口`。
- 有资产但没有计划：`尚未生成 API 用例计划`，按钮 `去生成计划`。
- 有草稿但没有已确认计划：`有待确认计划`，按钮 `去确认计划`。
- 已确认计划但 MeterSphere 未就绪：显示缺失配置，按钮 `完成 MeterSphere 配置`。
- 全部就绪但没有运行历史：`可以开始首次执行`，主按钮 `推送并执行`。

空状态由接口返回的事实组合得出，不在前端注入示例业务、示例计划或示例日志。

### 5. Technical Logs

技术日志位于当前运行区下方，默认折叠。每条日志包含真实时间、阶段、结果摘要、请求 ID 或运行 ID。展开后显示脱敏后的请求摘要、MeterSphere 响应和错误详情。

- 展开键使用 `run_id + event_id`。
- 轮询更新后保持展开状态和每个日志容器的滚动位置。
- 用户手动收起前，自动刷新不能收起日志。
- 后端没有事件时显示 `暂无执行日志`，不生成 `local` 占位事件。
- 密钥、Token、Authorization、Cookie 和签名原文不得进入日志响应。

### 6. Advanced Settings

设置面板按职责分组，不再平铺输入框：

1. `服务地址`：MeterSphere 地址和连接检查。
2. `认证方式`：单选 `Access Key` 或 `Token`，只展示当前方式需要的字段；已保存密钥只显示“已配置”，不回填脱敏字符串到密码输入框。
3. `业务与环境`：从 MeterSphere API 获取的业务和环境下拉框；保存 ID，展示名称。
4. `执行接口`：用例推送、计划执行、运行状态和报告查询路径。作为高级配置显示，并标注缺失项对执行能力的影响。

保存后立即重新执行连接和能力检查，关闭面板并刷新主页面状态。保存失败时保留用户输入和面板位置。

## Data Architecture

### Browser Boundary

浏览器只访问本平台 `/api/api-testing/...` 接口。MeterSphere 地址、认证签名、版本差异和响应字段归一化全部由 `metersphere_service.py` 处理。

前端禁止：

- 直接请求 MeterSphere 域名。
- 读取或保存真实 Token、Access Key、Secret Key。
- 根据已知 ID 写死业务名或环境名。
- 用倒计时、按钮点击结果或本地数组推断执行终态。

### Execution Context API

新增聚合读取接口：

```http
GET /api/api-testing/metersphere/execution-context
```

返回稳定的本平台合同：

```json
{
  "ok": true,
  "connection": {
    "state": "connected",
    "base_url": "(服务端已配置的 MeterSphere 地址)",
    "auth_mode": "access_key",
    "latency_ms": 126,
    "checked_at": "2026-07-22 10:00:00"
  },
  "selection": {
    "project_id": "772578717212672",
    "environment_id": "env-1"
  },
  "businesses": [
    {"id": "772578717212672", "name": "3D业务", "enabled": true}
  ],
  "environments": [
    {"id": "env-1", "name": "测试环境", "project_id": "772578717212672", "enabled": true}
  ],
  "capabilities": {
    "can_push": true,
    "can_run": true,
    "can_query_run": true,
    "can_pull_report": true,
    "missing": []
  },
  "plans": [],
  "active_runs": [],
  "recent_runs": [],
  "empty_reason": "no_confirmed_plan"
}
```

`businesses` 和 `environments` 由后端的 `list_metersphere_projects()` 与 `list_metersphere_environments(project_id)` 调用当前部署实例的项目、环境接口并归一化。远端路径和版本差异封装在适配器中，不进入浏览器。元数据成功响应最多缓存 30 秒；远端短时故障时可以返回最近一次成功缓存，但必须同时返回 `source: cache`、`stale: true` 和 `fetched_at`。过期缓存只供查看，执行按钮保持禁用，直到一次实时校验成功。

`plans` 来自平台本地计划接口，再由后端关联 MeterSphere push/run 映射与最新报告。前端不自行拼接多个来源的状态。

### Configuration Metadata API

设置面板沿用现有配置读写接口，但读取响应增加：

- `auth_mode`
- `*_configured` 布尔值
- 当前选中业务和环境的显示名
- 执行能力及缺失字段

密码输入为空代表保留原值；只有用户明确执行“清除”动作时才删除密钥。前端不再把 `ac***1234` 一类脱敏值提交回密码字段。

### Run Orchestration API

新增日常执行入口：

```http
POST /api/api-testing/metersphere/executions
```

请求只包含平台计划 ID 和可选的 MeterSphere 计划绑定 ID：

```json
{"plan_id": "api_plan_123", "test_plan_id": ""}
```

接口先校验请求、创建 `execution_id` 和 `queued` 记录，然后以 HTTP 202 返回；后端执行工作线程按顺序完成：

1. 校验连接、业务、环境、执行能力和平台计划确认状态。
2. 推送确认用例并保存 push 映射。
3. 创建或绑定 MeterSphere 测试计划并触发执行。
4. 保存统一 `execution_id`、MeterSphere `run_id` 和阶段事件。
5. 由状态查询持续更新真实运行状态；终态后拉取并归一化报告。

推送成功但触发失败时保留已完成阶段和远端 ID，整体状态为 `failed`，允许用户从失败阶段重试，不能重复静默创建相同用例。

### Run Status API

```http
GET /api/api-testing/metersphere/executions/{execution_id}
```

返回：

- 当前阶段和整体状态。
- MeterSphere 真实运行状态、开始/结束时间、通过/失败统计。
- 结构化阶段事件和脱敏技术日志。
- 报告同步状态及本平台报告 ID。
- `poll_after_ms`，由后端控制前端下一次轮询间隔。

运行中建议 `poll_after_ms=3000`；连续无变化时可以放宽到 5000，终态后停止轮询。用户点击刷新时强制请求一次，不复用浏览器缓存。

## Readiness State Model

后端生成统一 `readiness`，前端只负责翻译展示：

| 状态 | 条件 | 主要动作 |
| --- | --- | --- |
| `not_configured` | 缺少地址或认证 | 配置连接 |
| `disconnected` | 连接检查失败 | 查看错误并重试 |
| `connected_needs_setup` | 连接正常，但缺业务、环境或执行接口 | 完成配置 |
| `ready_no_plan` | 执行能力完整，但没有已确认计划 | 去生成或确认计划 |
| `ready` | 执行能力完整且有可执行计划 | 推送并执行 |
| `running` | 存在当前计划的未结束运行 | 查看实时进度 |
| `failed` | 最近编排或远端运行失败 | 查看失败阶段并重试 |

连接成功不等于执行就绪。页面必须同时展示连接状态和执行能力，避免当前“连接正常但实际无法推送/执行”的误解。

## Error Handling

- MeterSphere 认证失败：标记连接异常，设置面板定位到认证方式，不暴露响应中的敏感头。
- 业务或环境接口失败：保留上次成功值并标记过期；没有缓存时禁用执行。
- 已选择业务被删除或无权限：清空无效选择，要求重新选择。
- 用例推送部分成功：记录成功和失败数量，禁止自动进入执行阶段，提供按失败项重试。
- 触发执行超时：先通过幂等键或远端查询确认是否已经创建运行，再决定重试。
- 状态轮询失败：当前运行保持“状态暂时不可用”，不直接判失败；连续失败达到后端阈值后提示人工检查。
- 报告拉取失败：保留真实 MeterSphere 终态，单独标记报告同步失败并允许重拉。

## Security

- 认证信息只保存于服务端配置或环境变量。
- 所有 MeterSphere 请求由服务端适配器签名。
- 返回前递归清理 `authorization`、`token`、`accessKey`、`secretKey`、`cookie`、`signature` 等字段。
- 技术日志只保留必要请求参数、状态码、远端 ID 和脱敏响应摘要。
- 配置修改和执行触发继续使用平台现有登录鉴权。

## Responsive Behavior

- 桌面端使用“状态栏 + 计划主列表 + 当前运行”的单主列结构，设置采用右侧抽屉或受控弹层。
- 小屏幕上状态信息换行，业务和环境选择占满一行；计划操作收进菜单，主按钮保持完整可见。
- 当前运行阶段使用固定网格或横向可滚动轨道，动态文案不能改变整体布局宽度。
- 技术日志内容区独立滚动，不推动主操作区无限增长。

## Testing

### Backend

- MeterSphere 项目响应能归一化为 `businesses`，且前端显示名不依赖固定项目 ID。
- 环境列表按当前项目过滤。
- 连接成功但缺执行路径时返回 `connected_needs_setup` 和准确 `missing` 列表。
- 元数据实时请求失败时，缓存响应带 `stale=true`，无缓存时不能返回可执行状态。
- 未确认计划、空计划和重复运行均被编排接口拒绝。
- 推送成功/执行失败、执行成功/报告失败等部分失败状态可恢复。
- 所有响应和事件递归脱敏。

### Frontend

- 主页面不出现 Token、Access Key、Secret Key 和 11 个平铺输入框。
- `3D业务` 只在接口 fixture 中出现，不出现在生产 HTML/JS 常量中。
- 不同 readiness 状态显示正确说明、缺项和唯一主要动作。
- 没有后端日志时显示真实空状态，不生成 `local` 占位日志。
- 轮询刷新不收起已展开日志，也不重置日志滚动位置。
- 执行中只更新动态区域，不重绘整个页面。
- 移动端和桌面端均无文字溢出、按钮重叠或布局跳动。

### End-to-End

- 连接真实 MeterSphere 后能读取业务列表，选择 `3D业务` 并读取对应环境。
- 对已确认平台计划执行一次 `推送并执行`，能看到真实 push ID、run ID、阶段状态和终态。
- 终态后能拉取报告并跳转 API 报告页。
- MeterSphere 不可用、执行路径缺失和报告拉取失败三种情况均显示准确状态，不误报成功。

## Delivery Scope

本轮实现聚焦 `MeterSphere 执行` 日常执行闭环：

1. 增加 MeterSphere 项目/环境元数据适配和执行上下文接口。
2. 增加后端 readiness、执行编排、状态查询和脱敏事件合同。
3. 重构执行页为状态栏、计划列表、当前运行和高级设置。
4. 保留原 `push`、`run`、`reports/pull` 接口兼容已有调用；新页面使用编排接口。
5. 增加后端、前端和真实 MeterSphere 回归验证。

Apifox 自动同步、跨项目批量执行、定时任务和自定义报表不进入本轮。

## Acceptance Criteria

- 用户无需理解 Token、Workspace ID 或 API 路径即可完成日常执行。
- 业务、环境、计划、运行和报告均由真实接口提供，前端无业务名称和执行结果硬编码。
- 当前线上“已连接但执行路径未配置”的情况能被准确显示并引导完成配置。
- 已确认计划可以通过单一主操作进入 MeterSphere 执行并跟踪到真实终态。
- 页面刷新和轮询不会让技术日志自动收起或丢失阅读位置。
- 任一部分失败都有明确失败阶段、真实错误和可执行的恢复动作。
- 不影响现有接口资产、AI 用例计划、Agent、Runner、Sonic 和报告页面。
