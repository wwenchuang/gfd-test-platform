# CODEX_STATE.md

本文件记录当前 Codex 交接状态，目的是减少长对话上下文依赖。每次完成一轮重要修改后更新本文件。

## 当前项目状态

平台已有完整的 Agent 生成、YAML 校验、Runner 执行、Sonic 同步、报告和失败修复链路。当前主要目标不是重构架构，而是提高 AI 生成 Midscene YAML 的可执行性、速度和生产稳定性。

## 当前重点问题

1. Agent 生成 YAML 时偶尔会把“入口展示 / 布局 / 同级校验”误生成成“点击入口进入第三方流程”。
2. 生成 YAML 有时缺少目标模块路径，例如没有先进入文档打印页就校验百度网盘入口。
3. 过泛的 `aiTap` / `aiWaitFor` / `aiAssert` 会导致 Runner 反复重规划或定位失败。
4. 设备 / ADB / AI 模型服务异常需要和 YAML 脚本问题分开归因。
5. 旧任务和新任务状态展示、重跑、修复范围需要持续保持透明。
6. Windows Runner 需要作为服务稳定运行，并上报能力、设备、App 版本和 last_seen。

## 已有能力

- `yaml_executable_scorer.py`：YAML 可执行性评分。
- `yaml_static_validator.py`：YAML 静态校验。
- `yaml_baseline_cache.py`：基线缓存。
- `yaml_pattern_service.py` / `yaml_template_matcher.py`：基线写法和模板匹配。
- Agent smoke gate：首批冒烟控制。
- `/api/cases/rerun-smoke`：人工修改冒烟后重跑入口。
- Runner `yaml_dry_run` 能力：Windows Runner 已支持上报。
- Windows Runner 服务脚本：使用 NSSM 安装为服务。

## 最近完成的关键修复

### 2026-07-22 Apifox 凭据体验、接口导航图标与线上真实同步

用户反馈接口资产设置仍展示 Token 输入框，且接口测试侧栏使用 `API / OAS / AI / MS / RPT` 字母占位，和平台已有导航风格不一致。本轮只调整 API 测试前端和对应测试，没有修改 Agent、YAML、Runner、Sonic 或历史任务：

- 根因不是服务端空值覆盖：`api_source_service` 已支持令牌只写、读取脱敏、空值更新保留和显式清除。真实线上检查发现当时 source 列表为空，用户此前提供的 Token 尚未写入服务端，所以页面正确显示“令牌未配置”。
- 已通过平台认证 source API 将用户提供的 Token 写入线上服务端配置，读取接口只返回 `credential_configured=true`，不返回明文。随后绑定已验证的 Apifox `3D` 项目 `5904970`，来源变为 `3D 接口 / configured=true`。
- 首次线上同步 `succeeded`：`added=971 / changed=0 / removed=0`；紧接着第二次真实同步 `no_change`：`unchanged=971`。两次均完成到 `analyze_impact`，没有错误，证明 Token、项目、导出、不可变版本和 no-change 复用链路真实可用。
- 已配置 Token 在设置面板默认只显示“已安全保存 / 密钥仅保存在服务端”，不再呈现为待输入表单。只有点击“更换”才展开空白密码框；取消会清空输入并恢复保存状态，普通保存不会覆盖现有密钥，清除仍需独立确认。
- 接口测试五个导航入口改为与现有侧栏一致的语义图形图标，并保留可见名称及按钮 title；移除字母占位。桌面和 `390px` 移动端均验证无横向溢出、文本遮挡或导航跳动。
- 前端缓存版本已更新，避免部署后浏览器继续读取旧设置面板和旧侧栏。

已验证：

```bash
python3 tests/api_asset_sync_checks.py -v  # 27 tests
python3 tests/frontend_static_checks.py     # 69 checks
node tests/visual_smoke_check.js
git diff --check
npm test
```

- 完整结果：后端静态 `61` 项、前端 `69` 项、AI Gateway `46` 项、动态模型目录 / 回退、Skill 契约 `3` 个 fixture 及全套桌面 / 移动端 Playwright 回归全部通过。
- 新增视觉证据：`tests/artifacts/api-source-settings.png`、`api-source-settings-mobile.png`；测试同时覆盖 Token 默认隐藏、更换后空输入、取消恢复和五个导航图标。
- 待用户 push、部署前端提交；Codex 不 push。线上 Apifox source 与 971 接口资产已经配置并同步完成。

### 2026-07-22 API 闭环 Phase A：Apifox 只读同步、不可变版本与真实资产控制台

按 `docs/superpowers/specs/2026-07-22-api-automation-production-closure-design.md` 的首个子项目完成 API source / asset 基础闭环；本轮没有修改 UI Agent、YAML 生成、Runner、Sonic 或历史任务：

- 新增服务端 Apifox source 配置、只读导出 adapter、不可变 API revision、确定性 schema diff / plan impact 和异步同步调度。令牌只写，读取只返回 `credential_configured`；空值更新保留原令牌，显式清除才删除。已保存令牌绑定原 `base_url`，修改来源地址必须重新提交令牌，防止只写凭据被配置改址间接发送到新主机。
- 官方导出优先调用 `POST /v1/projects/{projectId}/export-openapi?locale=zh-CN`，使用公开版本头 `2024-03-28`、OpenAPI 3.0 JSON、Apifox 扩展字段和诚实的 `User-Agent: midscene-task-platform/api-sync`。真实排查确认 Python 默认 User-Agent 会收到未文档化的 `201` 空体，而显式平台 User-Agent 返回 `200 JSON`；只有官方路由空体或 `404/405` 时才有界降级到当前 CLI 兼容路由。
- endpoint 身份优先从真实 `x-run-in-apifox` 链接提取 Apifox API ID，其次使用唯一 `operationId`，最后才回退 `METHOD + path`。Apifox `x-apifox-folder` 作为业务模块第一事实来源，避免数百个接口退化成 URL 首段 `print3d`。
- revision 先持久化再切换 `active_revision_id`。同步失败或线程异常继续保留上一活动版本；默认 snapshot 兼容视图只读取活动 revision，未激活 revision 仅保留在版本历史。diff 同时识别 schema、method/path、鉴权、响应、名称、标签和弃用状态等元数据变化，计划影响只按稳定 endpoint key 确定性关联，不猜测旧计划映射。
- 同步记录使用真实 `sync_id / status / phase / poll_after_ms / events`，支持排队、运行、成功、无变化、失败、重复同步复用、重启恢复和 60 秒调度器。新增 `last_attempt_at`，远端失败后按配置周期退避，不会每分钟持续重试；手工同步不受退避限制。
- 新增认证 source/sync/revision/diff/impact 路由，并保持旧 OpenAPI 上传、snapshot 和 plan 读取兼容。接口资产页改为 `同步 Apifox` 主操作，支持来源、环境 ID、活动/历史版本选择、真实增改删未变和受影响计划计数。技术日志使用稳定 key，轮询重绘保留展开和独立滚动位置；一次状态读取失败会保留已有日志并在 3 秒后重试。阶段显示为中文，JSON 上传仍作为折叠备用入口。

真实 Apifox 验证（没有落盘或输出令牌）：

- 使用用户提供的只读令牌查询到 `3D` 项目 `5904970`。生产 adapter 真实导出 `968` 条 paths、`971` 个 operations；`971 / 971` 均获得稳定 Apifox provider key，fallback key `0`，重复 endpoint key `0`。
- 在隔离临时存储中连续执行两次完整同步：首轮 `succeeded / added=971` 并激活 revision；第二轮 `no_change / unchanged=971`，复用同一 revision，revision 总数仍为 `1`。
- 本地集成夹具覆盖首轮、无变化、schema 变化、接口删除、远端失败保留活动 revision、线程异常脱敏、调度退避和未激活版本隔离。

已验证：

```bash
python3 tests/api_asset_sync_checks.py -v  # 27 tests
python3 -m py_compile task_server/services/api_source_service.py task_server/services/apifox_service.py task_server/services/api_asset_service.py task_server/services/api_schema_diff_service.py task_server/services/api_sync_service.py task_server/services/api_test_plan_service.py task_server/router.py task_server/app.py
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
git diff --check
npm test
```

- 全量结果：undefined-name、后端 `61` 项、前端 `69` 项、AI Gateway `46` 项、动态模型目录 / 回退检查、Skill 契约 `3` 个 fixture，以及桌面 / `390px` 移动端视觉 smoke 全部通过。新增截图 `tests/artifacts/api-assets-sync.png` 与 `api-assets-sync-mobile.png`。
- 凭据扫描未发现 Apifox 令牌内容；本轮提交只包含 API 闭环源码、测试、设计/计划和状态文档。用户历史 YAML、`sonic_service.py`、`yaml_executable_scorer.py`、本地 Windows Runner 脚本和 `server-tasks/AI_Agent_草稿/` 不暂存、不回滚、不覆盖。

待部署后完成：

- 由用户手动 push、部署本轮提交；Codex 不 push。部署后通过认证 source API 或 `/opt/midscene.env` 保存真实 token / project `5904970`，在线执行首轮和 no-change 两次同步并核对页面 revision、971 接口、业务目录和技术日志。
- Phase B 继续完成 executable API 请求/断言/依赖合同、确定性 readiness/stale 门禁和 AI trace；Phase C 完成 MeterSphere `3.6.5-lts` capability probe、定义映射、真实运行与报告闭环；Phase D 再把 MeterSphere 注册到全局 `ExecutionFacade`，不改现有 UI Agent 主链。

### 2026-07-22 真实回归：跨 App 语义门禁不能误伤扫描导入栏，模型动作子字段必须还原为官方标量

用户部署 `e531598` 后，以完全相同需求、Figma、`qwen3.6-plus`、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed` 发起完整 Agent `agent-1784701056435-88e908e2`：

- PREPARE_SOURCE 正确解析 Figma `4 页 / 4 图 / 忽略 0`。4 张图分 4 批、每批 1 张真实送入 `qwen3.6-plus`，耗时约 `26 / 24 / 21 / 9` 秒，全部一次完成，无重试、无 fallback；PLAN 生成 8 条 AI 业务分支。
- 生成阶段形成 5 条 executable 候选：文档展示、照片展示、文档可达、照片可达、扫描可达。前 4 条 YAML 静态校验通过；扫描可达 YAML 已使用自己的可信扫描导航，并生成有界横向导入栏滚动及点击后首个可见页校验。
- Agent 最终为 `FAILED / GENERATE_YAML / 30%`。扫描可达文件中 3 个 `aiWaitFor` 和 1 个 `aiScroll` 被改写为空标量加 `text` 子字段，强校验分别报“内容不能为空 / 描述必须是非空字符串”。后续步骤全部跳过，Agent 没有创建 Runner job，也没有向 OPPO 或华为下发执行。

深层根因与通用修复：

- `_yaml_current_app_semantic_issues()` 同时识别小白学习打印和智小白 3D，但“开始创作旧版多入口”规则没有像用例规划门禁一样限定到 `com.kfb.model`。扫描可达 YAML 的横向滚动文案“使右侧更多入口进入视野”被子串命中“多入口”，再与后续“页面跳转”组合，错误触发 AI 建模旧入口门禁，导致本来合法的 YAML 进入不必要的 AI 静态修复。
- 现在 AI 建模旧入口、旧文字输入、动态推荐、旧欢迎态和语音录制规则只在 AI 建模 App 上生效；小白学习打印的扫描/文档/照片导入栏不再被跨 App 规则误伤。原 AI 建模旧入口回归样本仍继续被阻断。
- AI 静态修复可能返回 `aiWaitFor: {text: ...}` 或 `aiScroll: {description: ...}` 风格的 YAML。运行时规范化现将 `text / description` 与已有 `prompt / locate / value` 一样视为模型提示别名，扁平化为 Midscene 1.7.10 官方字符串动作；`aiScroll` 的 `direction / distance / scrollType` 保留为同级官方参数。静态校验仍拒绝未规范化的空动作，没有放宽 action 合同。
- 修复没有硬编码百度网盘、扫描复印、具体 case ID 或目标入口；没有修改 scorer、覆盖门禁、Runner、Sonic、Figma 解析、设备策略、坐标、账号/授权或历史 YAML。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py  # 61 checks
npm test
```

真实线上失败文件离线回放：原始空 prompt 动作 `4` 个，规范化后 `0` 个；横向滚动仍保留 `right / 400 / singleAction`；dry-run `ok=true / 0 error`。提交后不 push；由用户手动 push、部署，再用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，继续监督 5 条 YAML 的最终覆盖、smoke、remaining、真实报告和修复重跑到终态。

### 2026-07-22 MeterSphere 日常执行台：接口数据、异步执行与可停留技术日志

按已确认的 `docs/superpowers/specs/2026-07-22-metersphere-daily-execution-console-design.md` 完成 MeterSphere 执行页重构，不修改 Agent、YAML、Runner、Sonic 或移动端执行链路：

- 主页面不再平铺连接凭据和 11 个调试字段，改为紧凑连接状态、动态业务/环境、当前运行、已确认计划和右侧高级设置抽屉。业务名称来自 MeterSphere Project API，生产 HTML/JavaScript 没有写死 `3D业务`。
- 新增聚合读取合同 `GET /api/api-testing/metersphere/execution-context`。项目、环境、平台已确认计划、运行、报告映射、能力缺项和 readiness 均由后端归一化；项目/环境成功结果最多缓存 30 秒，实时刷新失败时仅返回 `stale=true` 的只读缓存，执行按钮保持禁用。
- 新增异步编排合同 `POST /api/api-testing/metersphere/executions` 与状态合同 `GET /api/api-testing/metersphere/executions/{execution_id}`。入口先持久化 `queued` 记录并以 HTTP 202 返回 `execution_id`，工作线程再强制实时校验连接、业务、环境和执行能力，校验通过后依次执行 `push_cases / trigger_plan / metersphere_run / sync_report`。
- 远端终态只由配置的运行状态接口返回，前端计时和等待时间不会推断成功。计划触发响应必须包含真实 MeterSphere `run_id`；缺失时明确失败，不再生成本地假运行 ID。远端执行成功但报告同步失败时，保留 `remote_status=succeeded`，整体流程单独标记报告阶段失败。
- 后端根据已持久化开始/结束时间返回运行和阶段 `duration_seconds`；前端只做格式化展示。状态轮询遵循后端 `poll_after_ms`，终态停止。
- 技术日志只渲染后端事件；没有事件时显示“暂无执行日志”。日志展开键优先使用事件自己的 `run_id / execution_id + event_id`，轮询局部更新前后保存每条日志的展开状态和独立滚动位置。真实 `run_id` 首次出现时，早期事件也不会因此收起。
- 所有 MeterSphere 远端响应、事件和归一化报告在返回或落盘前递归清理 Authorization、Token、Access Key、Secret Key、Cookie、签名和密码字段。配置读取只返回 `*_configured` 布尔值，密码输入始终为空；空输入保留原密钥，只有明确“清除当前认证”才删除。
- 请求头严格遵循当前 `auth_mode`：选择 Token 时即使服务端仍保留未启用的 Access Key，也只发送 Bearer Token；选择 Access Key 时必须同时存在 Access Key 和 Secret Key 才签名。
- 点击“推送并执行”后立即锁定当前计划并写入本地 active run，避免下一次上下文刷新前重复提交；后端仍保留同计划未结束运行的 409 冲突门禁。
- 工作线程、报告归一化或报告落盘出现非预期异常时，会把当前阶段持久化为终态失败并停止轮询，不会留下永久 `running` 记录；远端已经成功时仍保留 `remote_status=succeeded`。
- 保留原 `/metersphere/push`、`/metersphere/run` 和 `/reports/pull` 接口兼容旧调用，但同样收紧响应脱敏和真实 `run_id` 要求。

定向验证：

```bash
python3 -m py_compile task_server/services/metersphere_service.py task_server/router.py tests/backend_static_checks.py tests/frontend_static_checks.py
python3 tests/backend_static_checks.py   # 61 checks
python3 tests/frontend_static_checks.py  # 69 checks
node tests/visual_smoke_check.js
git diff --check
npm test
```

视觉回归新增 `metersphere-execution.png / metersphere-execution-mobile.png / metersphere-settings.png / metersphere-settings-mobile.png`，覆盖动态业务/环境、四阶段、计划主操作、设置职责分组、认证字段切换、密钥不回填、桌面/手机无横向溢出，以及一次真实轮询式重绘后技术日志仍保持展开和滚动位置。

待部署后真实验收：

- 由用户手动 push、部署；不要由 Codex push。
- 在服务端设置当前 MeterSphere 版本的项目列表、环境列表、用例推送、计划执行、运行状态和报告查询路径，再强制刷新确认 `source=live / stale=false / readiness=ready`。
- 使用现有 QA MeterSphere 的动态业务和环境发起一条已确认计划，核对真实 push ID、run ID、四阶段终态和归一化报告。代码和本地夹具已通过，但本轮提交前没有把“真实 QA 执行已跑通”写成完成事实。

### 2026-07-22 真实回归：人工 Figma 验收候选不能借可信导航提升为 Runner 用例

用户确认部署后，以完全相同需求、Figma、`qwen3.6-plus`、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed` 发起完整 Agent `agent-1784690470923-f7198fc0`：

- 线上 `8091 / 8088`、AI Gateway、Sonic、Runner 健康；固定 OPPO `ecbfd645 / PHM110 / com.xbxxhz.box 4.45.0 (357)` 可用。华为设备同时在线，但本轮 Agent 只绑定固定 OPPO；生成阶段失败前没有创建 Runner job，因此没有向任一手机下发执行。
- PREPARE_SOURCE 正确解析 Figma `4 页 / 4 图`。4 张图分 4 批、每批 1 图真实送入 `qwen3.6-plus`，耗时约 `18 / 26 / 22 / 23` 秒，全部一次完成，无重试、无 fallback。
- AI PLAN 生成 8 条业务流和 12 个场景。收敛结果中扫描复印 `TC-005` 已成功组合本分支可信导航基线 `d623c1e73180bfac` 与同目标兄弟分支 `TC-004` 的有界落地尾链，`boundedConvergence.kind=bounded_landing`，证明上一轮父路径归一化和兄弟尾链修复已在线生效。
- Agent 最终为 `FAILED / GENERATE_YAML / 30%`。YAML 转换契约拒绝 `MC-001`：该用例标题和场景仍是“扫描复印页百度网盘入口 UI 结构 / Figma 设计稿人工确认”，却被 `source_ui_assertion` 收敛错误提升为 executable；后续确定性 Runner 门禁正确判定“属于设计稿对比或视觉验收，Runner 无设计稿上下文时容易误判”。转换契约没有静默丢弃该用例，而是阻止整批进入 Runner，行为正确。

深层根因与通用修复：

- `_bounded_convergence_evidence()` 原来只根据 `originLevel == manual` 设置 `manualPromotionEligible`。只要存在同分支可信导航，人工 Figma/设计稿验收候选也能生成 `source_ui_assertion` 证据并被强制提升；规划层接受后，YAML 转换层再按 `_case_manual_block_reason()` 拒绝，形成同一用例在两个阶段结论不一致。
- 现在构造来源页断言证据时，会按实际候选级别和最终收敛字段建立 Runner probe，并复用同一个确定性 Runner 资格门禁。计划应用层还会在路径、断言、标题和上下文全部写回后检查每一条最终 executable case；命中阻断时恢复原候选到 manual 并记录原因。这样无论候选来自 manual、needs_review 或 automatic，模型即使直接返回 executable、可信 baseline 和完整 flow，也不能绕过门禁；普通可见文字、展示、同级、文案断言及合法 bounded landing 仍可执行。
- 没有放宽 YAML 转换契约、scorer、覆盖门禁、坐标、账号/授权、深层外部操作、Runner、Sonic、Figma 解析或设备策略；没有针对百度网盘、扫描复印或具体 case ID 写死规则。

已验证：

```bash
python3 tests/backend_static_checks.py  # 61 checks
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
npm test
```

线上产物离线复核：真实 `MC-001 / source_ui_assertion` 命中设计稿人工验收阻断；真实 `TC-005 / bounded_landing` 无 Runner 资格阻断。提交本轮修复但不 push；用户手动 push/部署后，再使用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，继续监督生成、smoke、remaining、真实报告和修复重跑到终态。

### 2026-07-22 真实回归：权限请求“对话框”证据也应支持有界弹窗修复；照片叶子缺运行时否定证据不能硬改

用户部署 `1104516` 后，先完成接口测试第一阶段线上配置，再继续以完全相同需求、Figma、`qwen3.6-plus`、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed` 发起百度网盘完整 Agent `agent-1784681089790-2ea0f8e1`：

- MeterSphere QA 地址已通过平台配置保存为 `http://qa-ms-apiauto.gongfudou.com:8081`，平台 `/api/api-testing/overview` 显示 `metersphere.configured=true`、`token_configured=false`。使用用户提供的账号完成 MeterSphere 登录握手后，`/user/api/key/list` 返回 403，当前账号缺少 `SYSTEM_PERSONAL_API_KEY:READ` 权限；平台 MeterSphere adapter 目前只支持 Bearer token/API Key 形态，不能把临时登录态当正式 token 提交到仓库或配置。
- 线上 `8091 / 8088`、AI Gateway、Sonic、Runner 健康；固定 OPPO `ecbfd645 / PHM110` 是唯一 dry-run 和 smoke 执行设备。华为设备在线但本轮没有被 Agent 选择或执行。
- PREPARE_SOURCE 成功，Figma 解析 `4 页 / 4 图 / 忽略 0`；PLAN 阶段 4 张图分 4 批真实送入 `qwen3.6-plus` 并全部完成。GENERATE_YAML 成功生成 6 条 executable YAML，覆盖 12 个场景：文档/照片/扫描三个入口的展示、同级、文案和可达性均通过生成阶段覆盖门禁。
- 首批 smoke 3 条真实执行：文档展示通过；扫描展示失败于「小白扫描王」页的“温馨提示”相机权限请求对话框遮挡；照片展示在“请选择需要制作的照片尺寸”弹窗后点击「一寸照规格页」发生 Runner/Midscene 300 秒超时。冒烟通过率 `1/3 = 33.3%`，remaining 3 条被门禁暂停。
- 扫描修复草稿方向正确，只是在 `点击「文件扫描」` 后插入“若出现标题为温馨提示且内容包含请求使用相机权限的弹窗，点击黄色确定按钮”。旧门禁只接受运行证据同时 OCR 出“取消/确定”的情况；本轮真实报告的关键文本主要是“权限请求对话框/请求使用相机权限”，导致 `navigation_change_without_baseline_citation` 误挡。
- 照片失败公开报告只能证明当前在照片打印页底部尺寸选择弹窗，随后 `aiTap: 点击「一寸照规格页」` 超时；没有稳定 OCR 出可选尺寸，也没有明确“没有一寸照”的运行时否定证据。因此本轮没有把照片叶子硬改成 `5寸照片`，避免绕过既有“运行时否定 + 当前 Figma 替代叶子 + 当前分支基线”证据链。

本轮通用修复：

- `positive_overlay_evidence()` 将中文“对话框”纳入弹窗/浮层遮挡证据：覆盖“权限请求对话框”“温馨提示权限对话框”“业务入口被对话框遮挡”等真实报告表述，并增加“无对话框/未出现对话框”等否定词，避免误判。
- repair candidate gate 在已有报告关键帧和强权限上下文时，允许使用补丁动作里明确写出的确认类控件补齐 OCR 缺失，只限 `确定 / 确认 / 允许 / 同意 / 继续 / 我知道了 / ok / confirm / allow`；不会因为动作里出现“如果没有弹窗则跳过”而把 `跳过` 当目标控件，也不会允许 `取消`、业务导航、坐标或无弹窗证据。
- 该修复不硬编码百度网盘，不放宽导航基线门禁、scorer、覆盖门禁、账号/授权、Runner、Sonic、Figma 解析、设备策略或历史 YAML；只修正真实报告证据与 transient overlay 门禁之间的同义词/按钮 OCR 缺失问题。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py task_server/services/ai_skill_service.py tests/backend_static_checks.py
git diff --check
npm test
```

待完成：

- 提交本轮修复但不 push；用户手动 push/部署后，再用相同参数和固定 OPPO `ecbfd645` 发起下一轮完整 Agent，重点确认扫描权限请求对话框修复稿能通过门禁并进入同设备重跑。照片“一寸照”问题仍需等 Runner 失败证据明确否定该叶子，或生成阶段有更强通用规则证明应优先选择 5 寸当前页，禁止为百度网盘写死。

### 2026-07-21 接口测试 MVP：OpenAPI 导入到 MeterSphere 执行闭环第一版

本轮按用户确认的“先跑通”范围，新增 API 测试工作区，不改现有 Sonic/Midscene/Runner 主链路：

- 新增左侧「接口测试」分组：`API 工作台 / 接口资产 / AI 用例计划 / MeterSphere 执行 / API 报告`。
- 第一阶段从 Apifox 导出的 OpenAPI JSON 导入接口资产；不接 Apifox token 自动同步，不自研 API Runner。
- 新增 API 资产服务：解析 OpenAPI paths、method/path、module、request/response schema、required fields 和 schema hash，并落盘到 `LEARNING_DIR/api-testing`。
- 新增 API 用例计划服务：生成 confirmable draft，用例覆盖成功响应、必填字段缺失、鉴权等基础场景；默认本地确定性生成，显式开启时可走 `api_test_designer` AI skill，AI 失败会回退本地草稿。
- 新增 MeterSphere adapter：保存服务端配置、token 脱敏、健康检查、用例推送、执行触发和报告拉取入口。未配置具体 MeterSphere API 路径时返回 `requires_config`，不会假装执行成功。
- 新增 API 报告服务：归并 MeterSphere 结果，并按鉴权、环境、测试数据、断言、接口/产品问题做轻量归因。
- 技术日志展开状态使用 `runId + stepId` 稳定 key 存到 localStorage，刷新后不会立即收回。
- 启动环境加载器已放行 `METERSPHERE_` 前缀，`deploy/midscene.env.example` 增加 MeterSphere 配置项；用户提供的 QA 地址应通过环境变量或页面配置写入，不把账号密码提交到代码。

本轮主要涉及：

- `task_server/services/api_asset_service.py`
- `task_server/services/api_test_plan_service.py`
- `task_server/services/metersphere_service.py`
- `task_server/services/api_report_service.py`
- `task_server/router.py`
- `ai_skills/prompts/api_test_designer.v1.md`
- `ai_skills/schemas/api_test_designer.schema.json`
- `js/api-testing.js`
- `task-manager.html`
- `js/api.js`
- `js/navigation.js`
- `js/agent-status.js`
- `js/state.js`
- `css/round5.css`
- `deploy/midscene.env.example`
- `tests/backend_static_checks.py`
- `tests/frontend_static_checks.py`
- `docs/superpowers/plans/2026-07-21-api-testing-mvp.md`
- `CODEX_STATE.md`

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/api_asset_service.py task_server/services/api_test_plan_service.py task_server/services/metersphere_service.py task_server/services/api_report_service.py task_server/router.py
python3 tests/frontend_static_checks.py
npm test
```

下一步部署后，在页面或 `/opt/midscene.env` 配置 `METERSPHERE_BASE_URL`、token/access key、workspace/project/environment ID，以及当前 MeterSphere 版本的 case push / plan run / report API path；如只能账号登录换 token，再用用户提供的测试账号做临时联调，但不要把明文账号密码写入仓库。

### 2026-07-21 真实回归：同分支运行时叶子修正要复用，重跑必须处理启动停留非首页 Tab

用户部署 `81199a6` 后，以完全相同需求、Figma、`qwen3.6-plus`、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed` 发起完整 Agent `agent-1784626372632-9784175e`：

- 线上 `8091 / 8088`、AI Gateway、Sonic、Runner 健康；固定 OPPO `ecbfd645 / PHM110` 是唯一 dry-run、smoke、repair rerun 设备。华为设备在线但未被本轮 Agent 选择或执行。
- PREPARE_SOURCE 正确保留完整需求正文，Figma 正确解析 `4 页 / 4 图 / 忽略 0`；PLAN 阶段 4 张 Figma 图全部真实送入 `qwen3.6-plus` 并完成，随后成功进入 GENERATE_YAML、RISK_REVIEW、EXECUTION_PRECHECK。
- 本轮生成 5 条 executable YAML：文档展示、照片展示、文档可达、照片可达、扫描可达。人工确认 / 人工走查稿没有再进入 smoke，说明上一轮 `manualHint` Runner gate 生效。
- 首批 smoke 3 条均绑定 `win-runner-01 / ecbfd645`。文档展示通过；照片展示和照片可达失败，根因相同：生成 YAML 采用了 Figma 软参考中的「一寸照」路径，但真机照片打印聚合页可见的是「5寸照片 / 6寸照片 / 7寸照片 / A4资料图片 / A4生活照片」或「普通证件照 / 智能证件照」，直接定位「一寸照」失败。
- 修复阶段第一条照片展示用例正确基于真实失败帧、当前 Figma 证据和分支基线，将「一寸照」改为「5寸照片」并创建重跑；第二条照片可达兄弟用例没有复用这个已接受的 `sourceLeafRuntimeOverrides`，AI 另提“普通证件照 -> 一寸照”且无可信分支基线，被平台门禁正确拒绝。
- `5寸照片` 修复稿重跑后又暴露启动状态问题：App launch 后停在底部「资料库」Tab，YAML 直接等待“首页已加载完成”超时。后续 AI 修复只增加照片页等待，没有先点击底部「首页」，再次失败。Agent 终态为 `FAILED / RERUN / 95%`，错误为“重跑后仍有失败或超时任务”。

深层根因与通用修复：

- 修复批处理现在维护已通过门禁的 `sourceLeafRuntimeOverrides`。同批后续失败用例若包含同一个被运行时否定的导航叶子，并且目标文案一致、当前分支基线 ID 仍在候选集中，平台会先用局部 patch 复用该 `fromLeaf -> toLeaf` 修正，再走现有 candidate gate、断言契约、scorer 和 YAML 校验；过不了才回退 AI。这样同一照片分支的展示和可达兄弟用例不会一个改成 `5寸照片`、另一个又被 AI 带去无基线的子流程。
- 修复候选现在能识别“启动后停在非首页底部 Tab”的真实失败证据：错误文本同时证明底部导航可见、首页未选中、当前在「资料库」或非首页时，平台会在 `launch` 后插入可见底部导航等待、点击底部「首页」、再等待首页核心入口稳定显示。该本地 patch 仍通过常规 repair gate；不会使用坐标、ADB swipe 或跨设备重跑。
- 两个修复都不硬编码百度网盘、不放宽导航基线门禁、不改 scorer、Sonic、Runner、Figma 解析、设备策略或历史 YAML；只是把已经由真实运行证据和可信基线证明的局部修复，在同批/同设备闭环中复用，并补齐真实启动状态守卫。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check
npm test
```

本轮涉及 `task_server/services/agent_service.py`、`tests/backend_static_checks.py` 和 `CODEX_STATE.md`。提交后不 push；用户手动 push/部署后，需要再次使用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，重点确认照片展示与照片可达在修复阶段共享 `一寸照 -> 5寸照片` 运行时叶子修正，重跑遇到「资料库」起点时先回到底部「首页」，随后继续监督 remaining、repair rerun 和所有 Runner 报告到真实终态。

### 2026-07-21 真实回归：人工确认稿、横向入口、修复补丁与权限弹窗门禁必须闭环

用户部署 `bd300df` 后，以完全相同需求、Figma、`qwen3.6-plus`、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed` 发起完整 Agent `agent-1784622871663-d8f0ec7b`：

- 线上 `8091 / 8088`、AI Gateway、Sonic、Runner 健康；固定 OPPO `ecbfd645 / PHM110` 是唯一 dry-run、smoke 和 repair 设备。另有华为在线，但本轮没有被 Agent 选择或执行。
- Figma 正确解析为 `4 页 / 4 图 / 忽略 0`，4 张图分 4 批真实送入 `qwen3.6-plus` 并全部完成。Agent 越过 `GENERATE_YAML / RISK_REVIEW / EXECUTION_PRECHECK`，生成 7 条 YAML。
- 首批 smoke 3 条真实执行：文档展示通过；扫描展示失败为 `SCRIPT_ISSUE / scroll_not_effective`，真实截图显示横向导入栏里「本地导入 / 相册导入 / 微信导入」可见，但右侧目标入口被裁切，生成 YAML 未先横向滑动；扫描人工走查失败为权限请求弹窗遮挡「立即使用」。冒烟通过率低于 50%，remaining 未执行，Agent 终态为 `FAILED / COLLECT_REPORT / 95%`。
- 生成 YAML 中 `05/06` 标题含“需人工确认UI”，`07` 为“人工走查”，但仍被标成 executable 并进入 smoke；这违反“人工确认 / 人工走查稿不能下发 Runner”原则。
- 自动修复里扫描展示 patch 方向正确，但模型返回了 `aiScroll:` 空标量 + `value:` 子字段，旧补丁 normalizer 认为 `aiScroll` 值为空，阻断应用；权限弹窗 patch 已有真实失败帧支持，但 transient overlay 证据识别没有覆盖“权限请求弹窗，包含取消和确定按钮”的线上文案，被导航基线门禁误挡。

深层根因与通用修复：

- Agent Runner gate 现在会消费 scorer 的 `manualHint`：生成 YAML 只要标题或任务被识别为人工确认 / 人工走查提示，即使 AI 或 ref 标为 smoke，也会降级为 `needs_review`，禁止进入 Runner 和首批 smoke。没有修改用户当前未提交的 `yaml_executable_scorer.py`。
- 生成 YAML 本地修复器现在把“等待扫描复印页面加载”收敛为可见导入区锚点，并在下一步校验横向入口且目标可能被裁切时，插入有界 `aiScroll` 向右单次滑动和短 sleep，再等待目标可见。该逻辑按“本地导入 / 相册导入 / 微信导入”横向导入栏通用锚点工作，不硬编码百度网盘。
- repair patch normalizer 现在接受 AI 产出的 `aiScroll:` 空标量加 `value:` 子字段，规范化为 Midscene 官方字符串动作，并保留 `direction / distance / scrollType` 子字段；仍限制坐标、ADB、隐藏定位器、过长距离和非官方动作。
- transient overlay 门禁现在识别“权限请求弹窗，包含/显示可见按钮”的真实运行证据。只有在报告关键帧和当前 job 错误文本共同证明弹窗存在、且新增动作只处理弹窗控件时，才豁免导航基线引用；业务导航新增、跨 job 聚合分析和无关键帧泛化失败仍会被挡。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/ai_skill_service.py task_server/services/repair_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
git diff --check
npm test
```

本轮涉及 `task_server/services/agent_service.py`、`task_server/services/ai_skill_service.py`、`task_server/services/yaml_service.py`、`task_server/services/repair_service.py`、`tests/backend_static_checks.py` 和 `CODEX_STATE.md`。提交后不 push；用户手动 push/部署后，需要再次使用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，重点确认人工确认稿不进入 smoke、扫描展示 YAML 先做有界横向滑动、`aiScroll.value` 修复补丁可应用、权限弹窗修复稿能进入同设备重跑，再监督 smoke、remaining、repair rerun 和所有 Runner 报告到真实终态。

### 2026-07-21 真实回归：点击后可达性 YAML 不能等待“跳转过程”，扫描页等待必须锚定可见导入区

用户部署 `bb7189c` 后，以完全相同需求、Figma、`qwen3.6-plus`、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed` 发起完整 Agent `agent-1784618340121-02ece831`：

- 线上 `8091 / 8088`、AI Gateway、Sonic、Runner 健康；固定 OPPO `ecbfd645 / PHM110` 是唯一被本轮 Agent 选择和执行的设备。另有华为设备在线，但本轮 Agent 的 dry-run、smoke、remaining、repair rerun 均绑定 `win-runner-01 / ecbfd645`。
- Figma 正确解析为 `4 页 / 4 图`，4 张图分 4 批真实送入 `qwen3.6-plus` 并全部完成。生成阶段已越过上一轮照片打印文案门禁，产出 6 条 executable YAML：文档展示、照片展示、扫描展示、文档可达、照片可达、扫描可达。
- Smoke 3 条中 2 条通过、文档可达失败；remaining 3 条均真实执行。失败集中为脚本问题：点击百度网盘后真实页面已稳定显示文件列表/授权页，但 YAML 仍等待“页面跳转或弹窗/弹出新窗口”；扫描修复重跑中“等待扫描复印页面加载完成”在首页/扫描壳页被误判为真，随后在错误页面查找“本地导入/相册导入”横向区域失败。
- 自动修复 AI 对文档/扫描可达的 patch 方向正确，想把泛化跳转等待替换为文件选择页稳定信号；但修复候选门禁把“替换点击入口后的等待条件”误识别成“声称修改导航但 aiTap 路径未变”，以 `navigation_claim_without_yaml_change` 拒绝，没有下发 Runner。

深层根因与通用修复：

- 生成前本地修复器只删除了窄形态“等待页面跳转或弹窗出现”，没有覆盖线上出现的“等待页面跳转或授权/文件列表弹窗出现”“等待页面跳转或弹出新窗口”“点击后的目标页面或提示已稳定显示”等过程型等待变体。新逻辑会在后续已有具体稳定落地页 wait/assert 时删除这些过程型等待，保留授权页、登录页、文件选择页、返回/搜索/确定/文件列表等可见终态信号。
- 扫描复印页的泛化加载等待不能只写“页面加载完成”。当下一步要在导入横向区滑动或校验入口时，平台会把等待收敛为“扫描复印页面或复印扫描导入页面加载完成，可见「本地导入」「相册导入」「微信导入」等导入入口区域”，避免在首页或错误壳页误放行。百度网盘本地 fallback 的扫描分支同步使用该可见锚点。
- 修复候选门禁继续约束真实导航路径变更和基线引用，但不再把“wait/assert 条件替换”误当成导航路径修改声明；同一 aiTap 路径下替换点击后的落地页等待可以进入 scorer、静态校验和同设备重跑。
- 该修复不硬编码百度网盘业务结果，不放宽覆盖门禁、scorer、坐标、账号/授权、Runner、Sonic、Figma 解析、设备策略或历史 YAML；只收紧生成 YAML 的可观测等待条件，并修正 repair gate 对等待条件修复的误判。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/ai_skill_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
git diff --check
npm test
```

本轮涉及 `task_server/services/agent_service.py`、`task_server/services/ai_skill_service.py`、`task_server/services/yaml_service.py`、`tests/backend_static_checks.py` 和 `CODEX_STATE.md`。提交后不 push；用户手动 push/部署后，需要再次使用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，重点确认生成 YAML 不再包含过程型跳转等待，扫描页等待包含真实导入区可见锚点，repair draft 不再因等待条件替换被 `navigation_claim_without_yaml_change` 拒绝，随后监督 smoke、remaining、repair rerun 到真实终态。

### 2026-07-21 真实回归：视觉软参考不能用“展示入口”覆盖显式文案验收

用户部署 `9f9d594` 后，以完全相同需求、Figma、`qwen3.6-plus`、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed` 发起完整 Agent `agent-1784611806002-d2141bd5`：

- 线上 `8091 / 8088`、AI Gateway、Sonic、Runner 健康；固定 OPPO `ecbfd645 / PHM110` 是唯一选中设备。Agent 在 `GENERATE_YAML / 30%` 失败，没有创建 Runner job，也没有向第二台手机下发。
- PREPARE_SOURCE 已确认 `normalizedInput.requirementText` 是完整长需求，不再退化为短标题；PLAN 阶段 4 张 Figma 图分 4 批真实送入 `qwen3.6-plus` 并全部完成。
- 生成阶段产出 6 条 executable YAML：文档展示、照片展示、文档可达、照片可达、扫描展示、扫描可达。case portfolio 初始 `8/12`，收敛后 `12/12`；但最终 YAML 覆盖门禁仍阻断，缺口为 `REQ-002 [acceptance:copy] 照片打印：校验百度网盘入口使用需求约定的可见文案`。
- 人工复核生成 YAML 发现照片展示 YAML 只断言“照片打印规格页底部展示「百度网盘」入口，与「相册导入」、「相机拍照」等同级并列”，覆盖展示和同级关系，但没有“文案准确 / 文案为百度网盘”等显式文案断言。平台最终门禁阻断正确，问题在视觉合并把需求侧 copy 断言吞掉。

深层根因与通用修复：

- `merge_visual_grounder_payload()` 用同一个 `case_covers_requirement_acceptance()` 判断视觉增量是否已覆盖所有验收维度；该函数为了 portfolio 粗审允许“断言里出现目标文字并且展示/显示/可见”作为 copy 的弱证据。视觉合并阶段复用这个弱判断后，把“展示「目标」入口”误当作文案验收已覆盖，导致原始“文案准确”断言不再保留。
- 新逻辑只收紧视觉合并的 copy 覆盖判断：视觉软参考要覆盖 copy 验收，必须包含“文案准确 / 文案正确 / 文案为 / 文字准确 / 显示为 / 文案完整 / 文案清晰”等显式文案谓词；单纯“展示目标入口”只能证明展示，不能证明文案。
- 若视觉增量只覆盖展示/同级而没有覆盖 copy，平台会保留需求侧文案断言，并把该断言同步恢复到 `expected_result` / `ai_case_plan.assertionTarget`，避免 YAML 生成在默认单断言限制下只选择视觉同级断言。
- 该修复不硬编码百度网盘，不放宽最终覆盖门禁、scorer、dry-run、Runner、Sonic、设备选择、账号/授权、坐标或深层外部动作限制；只让视觉软参考不能覆盖掉未实际证明的验收维度。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
git diff --check
npm test
```

本轮只修改 `task_server/services/ai_skill_service.py`、`tests/backend_static_checks.py` 和 `CODEX_STATE.md`。尚未 push；用户手动 push/部署后，需要再次用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，重点确认 6 条 YAML 的照片展示项包含显式文案验收，随后监督 smoke、remaining、修复重跑和所有 Runner 报告到真实终态。

### 2026-07-21 真实回归：Agent start 必须保留 `requirement` 正文，避免 PLAN 退化成短标题

用户部署 `683c181` 后，以同一需求、Figma、`qwen3.6-plus`、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed` 发起完整 Agent `agent-1784605378358-1e58d5ad`：

- 公网 `8091 / 8088`、AI Gateway、Sonic、Runner 健康；固定 OPPO `ecbfd645 / PHM110` 是唯一执行设备，dry-run 和 smoke 均绑定 `win-runner-01 / ecbfd645 / fixed`，没有向第二台手机下发。
- PREPARE_SOURCE 成功，Figma 解析 `4 页 / 4 图`；PLAN 阶段 4 张图分 4 批全部真实送入 `qwen3.6-plus` 并完成。
- Agent 终态为 `FAILED / COLLECT_REPORT / 95%`。生成阶段只产出 3 条 YAML：文档展示、照片展示、以及错误的“基础打印首页”展示；缺扫描复印 YAML，缺三业务入口的可达性 YAML，覆盖审计却认为缺口为 0。首批 smoke 3 条真实执行，文档通过，照片和“基础打印首页”失败；修复草稿 2 条均被导航变更门禁阻断，没有创建修复重跑。

深层根因与通用修复：

- 线上 run 的 `normalizedInput.requirementText` 为空，PLAN tool input 里的 `requirement` 只有短标题“基础打印新增百度网盘入口”。因此原本明确的正文“基础打印的入口在首页：文档打印、照片打印、扫描复印……覆盖展示、同级关系、文案及可达页面”没有进入 `PREPARE_SOURCE / PLAN / MM skills / 覆盖审计`。
- `AgentContext` 只把 `requirementText` 和 `sourceInputs.requirementText` 归一化为需求正文，没有兼容 start payload 中的 `requirement` 字段。带 Figma 的新需求因此被当作“短标题 + Figma 软参考”，业务分支退化为“目标业务页”，覆盖矩阵也随之错误收缩。
- 新逻辑只扩展 Agent 输入归一化：`requirement` / `sourceInputs.requirement` 与 `requirementText` 等价进入 `normalizedInput.requirementText`，并继续由 `_agent_plan_requirement_text()` 和 `_agent_source_material_context()` 传给 PLAN 与 PREPARE_SOURCE。
- 该修复不硬编码百度网盘，不改 scorer、覆盖门禁、AI prompt、Figma 解析、Runner、Sonic、设备选择、账号/授权、坐标或历史 YAML。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check
npm test
```

本轮涉及 `task_server/services/agent_service.py`、`tests/backend_static_checks.py` 和 `CODEX_STATE.md`。提交后不 push；用户手动 push/部署后，需要再次使用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，重点先确认 `normalizedInput.requirementText` 和 PLAN tool input 为完整长需求，再监督 4 张 Figma AI 批次、YAML 覆盖矩阵、smoke、remaining、修复重跑和所有 Runner 报告到真实终态。

### 2026-07-21 真实回归：修复补丁多行锚点不能因可选 timeout 缺失被整条阻断

用户部署 `90f0822` 后，以完全相同需求、Figma、`qwen3.6-plus`、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed` 发起完整 Agent `agent-1784602711778-e6032d45`：

- 线上 `8091 / 8088`、AI Gateway、Sonic、Runner 健康；固定 OPPO `ecbfd645` 是唯一执行设备，本轮 dry-run、smoke 和修复重跑均绑定 `win-runner-01 / ecbfd645 / fixed`，没有向华为或第二台设备下发。
- PREPARE_SOURCE 成功，Figma 解析 `4 页 / 4 图`；PLAN 阶段 4 张图分 4 批全部真实送入 `qwen3.6-plus` 并完成，`sentToAiForJudgement=true / aiJudgementCompleted=true`。
- GENERATE_YAML 成功生成 6 条 YAML：文档展示、照片展示、扫描展示、文档可达、照片可达、扫描可达。此前“待确认 / 若存在 / 记录缺陷”一类人工条件 YAML 没有再进入 smoke，说明 `90f0822` 的人工条件门禁生效。
- 首批 smoke 3 条真实执行均失败，平台归因为 `SCRIPT_ISSUE` 并进入自动修复。修复重跑创建 2 条：照片修复稿通过，扫描修复稿因 Midscene AI 调用 `Timeout after 300s` 失败；文档修复稿没有创建 Runner job，因为 AI patch 被 `repair_patch_application_failed` 门禁阻断。Agent 终态为 `FAILED / RERUN / 95%`，总结为“部分通过”。

深层根因与通用修复：

- 文档失败的诊断和 patch plan 是正确方向：启动后设备停留在「资料库」Tab，应在 `launch` 后点击底部「首页」并用真实首页入口等待。失败点不是 AI 不会修，而是补丁应用器把第二个锚点写成多行：`aiWaitFor: "被测 App 首页已加载完成，首页核心功能入口可见"\n  timeout: 8000`。
- 原始 YAML 的该 `aiWaitFor` 没有 `timeout: 8000` 子字段。旧 `_repair_patch_anchor_parts()` 对多行锚点按整串解析，导致锚点找不到；虽然第一条 `insert_after launch` 可用，整个 patch 应用仍被拒绝，文档修复无法落到可执行 YAML。
- 新逻辑只改变锚点主动作行解析：若模型返回多行锚点，平台提取其中第一条受支持的 flow 动作行作为匹配依据；仍要求动作文本完整相等且唯一，不允许子串锚点、坐标、ADB、XPath、替换 `launch` 或删除业务断言。
- 该修复不硬编码百度网盘，不改 Runner、Sonic、Figma、scorer、历史 YAML、设备选择或模型配置；只让 AI 产出的通用局部 patch 在可选 child 字段不一致时仍能匹配唯一原始 flow item。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/repair_service.py tests/backend_static_checks.py
git diff --check
npm test
```

待完成：

- 提交本轮修复但不 push；用户手动 push/部署后，再用完全相同参数和固定 OPPO `ecbfd645` 发起下一轮完整 Agent，重点确认文档 repair patch 能应用并进入同设备重跑，扫描超时继续按环境 / 模型服务问题和报告证据分开归因。

### 2026-07-21 真实回归：人工条件式 YAML 不能进入 Runner smoke

用户部署 `a1dd727` 后，以完全相同需求和 Figma 发起完整 Agent `agent-1784600036692-2accacd3`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic、Runner 健康；本轮所有 dry-run 和正式 smoke job 均绑定 `win-runner-01 / ecbfd645 / fixed`，没有向华为或第二台设备下发。
- PREPARE_SOURCE 成功，Figma 解析 `4 页 / 4 图 / 忽略 0`；4 张图分 4 批真实送入 `qwen3.6-plus` 并完成。GENERATE_YAML 成功生成 8 个 YAML，VALIDATE_YAML dry-run `8/8` 通过。
- 首批 smoke 选择 3 条：文档入口成功、照片入口成功、扫描复印 `06-扫描复印页-百度网盘入口UI展示与文案校验（待确认UI稿）.yaml` 失败。Agent 终态为 `FAILED / COLLECT_REPORT / 96%`，报告收集到 3 个执行报告，2 成功、1 失败，remaining 5 条按冒烟门禁延后。
- 失败分析把扫描项归为 `PRODUCT_BUG`，理由是扫描复印页缺少「百度网盘」。但人工复核生成 YAML 发现被下发的 `06` 本身含“待确认UI稿”“若存在，检查文案”“或确认该页面无此入口”“记录入口的具体位置”等人工条件分支，不应被 scorer 标成 executable/smoke；同轮 `03-扫描复印页-百度网盘入口可见性及文案校验.yaml` 已包含扫描复印点击百度网盘后的可达性短链路。

深层根因与通用修复：

- 旧 scorer 只会惩罚条件式 `aiTap`，没有识别条件式 `aiWaitFor / aiAssert` 和标题中的待确认语义，因此“若存在/或确认无此入口”这类人工验收脚本仍可能拿到 `executable` 并进入首批 smoke。
- 新增 `GENERATED_MANUAL_CONDITION_WORDS` 和 `_has_generated_manual_condition()`，生成 YAML 只要含“待确认、若存在、如果存在、或确认无、确认该页面无此入口、记录缺陷、记录入口的具体位置”等人工条件分支，就降级为人工评审，不允许自动下发 Runner。
- 该修复不把百度网盘结果硬编码为产品缺陷或脚本缺陷；只是恢复既有原则：人工条件文案不能进入 Runner。Figma 解析、AI 规划、覆盖门禁、Runner、Sonic、设备选择、账号/授权/坐标限制均未放宽。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
git diff --check
npm test
```

本轮涉及 `task_server/services/yaml_executable_scorer.py`、`tests/backend_static_checks.py`、`CODEX_STATE.md`。注意：`task_server/services/yaml_executable_scorer.py` 进入本轮前已有用户未提交改动，提交时必须只暂存本轮新增的人工条件门禁，不能把用户原有 scorer 改动一并提交。用户明确要求后续不要尝试 push；提交后等待用户手动 push/部署，再用完全相同输入和固定 OPPO `ecbfd645` 发起下一轮完整 Agent。

### 2026-07-21 真实回归：低置信复检不能阻断明确脚本失败的自动修复

用户部署 `7a7d091` 后，以完全相同需求和 Figma 发起完整 Agent `agent-1784596911529-3e875d9d`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康；固定 OPPO `ecbfd645` 被唯一选中。所有本轮 Agent dry-run、首批冒烟和修复重跑均绑定 `win-runner-01 / ecbfd645 / fixed`，没有向华为或第二台设备下发。
- PREPARE_SOURCE 成功，Figma 解析 `4 页 / 4 图 / 忽略 0`。PLAN 不再假卡死，4 张 Figma 图分 4 批真实送入 `qwen3.6-plus`，批次均完成；随后 GENERATE_YAML、VALIDATE_YAML、RISK_REVIEW、EXECUTION_PRECHECK 均通过。
- 生成阶段本轮已越过前几轮的覆盖门禁：5 条 executable YAML、12 个验收维度通过生成门禁和 dry-run。生成文件为文档展示、照片展示、文档可达、照片可达、扫描可达首屏 5 条。
- 首批冒烟选择文档展示和照片展示两条。两条 dry-run 均成功；正式 Runner 串行执行后两条均失败。照片失败是 `一寸照` 不存在，AI 修复为 `5寸照片` 后创建 `job_1784597940251_00007` 并在同一 OPPO 成功。文档失败是点击「文档打印」后仍停留在首页，Runner 原始错误明确指出“等待文档打印页面加载完成”不准确；但失败复检被错误降级为 `review_source_mismatch / can_auto_repair=false`，导致只保存 1/2 条修复草稿，最终 Agent 为 `FAILED / RERUN / 95%`，错误为“使用修复草稿 1/2 条，未覆盖失败任务 1 个”。

深层根因与通用修复：

- 失败复检清洗会检查 AI review 是否引用了当前 YAML、日志、summary 或报告文本中不存在的 UI 术语。线上文档失败中，review 证据把相邻日志片段和换行拼成了不稳定片段，被误判为“未出现”。这个低置信 `review_source_mismatch` 本应只表示“复检结论不可采信”，却通过 `can_auto_repair=false` 覆盖了 Runner 原始的明确脚本证据。
- `_normalize_failed_execution_item()` 已能保证低置信复检不覆盖 `failureType=SCRIPT_ISSUE`，但 `_agent_repair_eligibility()` 仍读取 review 内的 explicit false，从而把本可修复的文档脚本失败挡在 AI patch 之前。
- 新逻辑只忽略低置信 `unknown / review_source_mismatch` 复检里的 `canAutoRepair=false`。产品缺陷、环境问题、高置信不可修复、以及 job 顶层明确的 `canAutoRepair=false` 仍保持硬门禁。
- 新增回归复现本轮文档形态：Runner summary 明确 `waitFor timeout` 且当前页仍是首页，但低置信 source-mismatch review 声称不可修复。修复后 normalized item 仍是 `SCRIPT_ISSUE`，不会写入硬 `canAutoRepair=false`，修复资格保持 eligible，后续可进入同一套 AI patch、基线引用、断言契约、scorer、dry-run 和 Runner 门禁。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check
npm test
```

本轮只修改 `task_server/services/agent_service.py`、`tests/backend_static_checks.py` 和 `CODEX_STATE.md`。尚未部署；用户手动部署后，需要再次使用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，确认文档失败能生成修复草稿并同设备重跑，随后监督 remaining 到真实终态。另：前端“实时展开日志会被刷新收回/技术日志不便停留查看”的体验问题已确认存在，但本轮先处理阻断回归闭环的后端根因，后续应单独做前端展开状态持久化和实时数据刷新优化。

### 2026-07-20 真实回归：Agent PLAN 同步 MM 规划必须有硬超时，避免线上假卡死

用户部署 `ff71991` 后，以完全相同需求和 Figma 发起完整 Agent `agent-1784543629519-7212477f`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic、Windows Runner 均健康；固定 OPPO `ecbfd645 / PHM110` ready，Runner 上报 `qwen3.6-plus / qwen3.6`。本轮未创建 Runner job，没有向 OPPO、华为或第二台设备下发。
- PREPARE_SOURCE 成功，Figma 正确解析 `4 页 / 4 图 / 忽略 0`；但 Agent 随后停留在 `RUNNING / PLAN / progress=6` 超过 15 分钟，`updatedAt` 未推进。
- `visualReferenceReport` 显示 `sentToAiForJudgement=false / aiJudgementCompleted=false / visualBatchesDone=0 / visualBatchesTotal=0`，说明本轮尚未进入 Figma 视觉分批 AI 判断，也不是上一轮的 YAML 覆盖门禁失败。
- `mindmapPlan=null / plan=null`，无 pending confirmation，无 Runner job。问题边界在 PLAN 内部的 MM 业务规划调用返回前，而不是 scorer、Sonic、ADB、Windows Runner 或固定设备策略。

深层根因与通用修复：

- `_tool_agent_plan()` 为共享生成 job 写入了 `timeout_seconds=900`，但随后同步直接调用 `generate_mindmap_from_request()`。共享 job 的过期逻辑只能在读取/镜像进度时把 job 标记 timeout，不能中断正在等待的 Agent worker；如果 MM 规划内部某个 AI/网络调用迟迟不返回，外层 Agent 就无法返回 `FAILED` 终态。
- 新增 Agent 级 `AGENT_PLAN_MINDMAP_TIMEOUT_SECONDS`（环境变量 `MIDSCENE_AGENT_PLAN_MINDMAP_TIMEOUT_SECONDS`，默认 900s），并用 `_run_agent_call_with_hard_timeout()` 包住 PLAN 的 MM 规划调用。超时后不等待卡住的 executor 线程退出，立即把 progress job 标成 `timeout`，让 `_tool_agent_plan()` 返回 `FAILED`，外层状态机可正常落到终态。
- 该修复不改变 AI 规划 prompt、Figma 解析、视觉批次、YAML 生成策略、覆盖门禁、scorer、Runner、Sonic、设备选择、坐标/账号/授权限制或历史 YAML；只补 Agent runtime 的超时收敛边界。
- 新增后端静态回归要求 PLAN 不能只依赖共享 job expiry，必须有 Agent 自己的硬超时包装，并确认 executor shutdown 使用 `wait=False`，避免超时路径再次阻塞。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
git diff --check
npm test
```

本轮修复尚未提交/部署。提交、推送并部署后，不要复用已卡住的 Agent；必须再次使用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，监督 PLAN、4 张 Figma 视觉批次、GENERATE_YAML、Smoke、remaining、可能的 AI 修复和所有 Runner 报告到真实终态。

### 2026-07-20 真实回归：收敛改写被守卫降级时恢复既有 executable，避免回归验收维度

用户部署 `8870013` 后，以完全相同需求和 Figma 发起完整 Agent `agent-1784542291067-84192d7a`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康；通过 `x-token` 核对 `win-runner-01` 在线，固定 OPPO `ecbfd645 / PHM110` ready，`com.xbxxhz.box 4.45.0 (357)`，Runner 上报 `qwen3.6-plus / qwen3.6`。本轮仍在 `GENERATE_YAML / 30%` 终止，没有创建 Runner job，没有向 OPPO、华为或第二台设备下发。
- Figma 正确解析 `4 页 / 4 图 / 忽略 0`。4 张图分 4 批真实送入 `qwen3.6-plus`，批次均 completed、`fallback=false / finishReason=stop / hardGate=false`；设计资料继续作为完整送 AI 判断的软参考。
- AI 规划 8 条业务分支。初始 executable portfolio 覆盖 `8/12`，缺 `REQ-001-CHECK-04` 文档可达、`REQ-002-CHECK-04` 照片可达、`REQ-003-CHECK-02` 扫描同级、`REQ-003-CHECK-04` 扫描可达。
- 最终收敛聚焦 `TC-002 / TC-003 / TC-007 / MC-002 / MC-004`。提案新增了文档可达和扫描同级/可达，但改写 `TC-002` 后丢失照片展示/同级。单调门禁正确拒绝整份提案并保留收敛前组合，最终仍缺 4 个验收维度。

深层根因与通用修复：

- `acceptance_repair_retry` 对 `TC-002` 的局部语义反馈显示 `remaining_feedback=[]`，但后续可信基线、动态数据、视觉/路径守卫仍可能把该 repairable executable 的改写降级。旧逻辑对 repairable 候选会完全接受模型分类；一旦改写被守卫降级，原本已通过审计的 executable 也从组合中消失，导致提案产生回归验收维度并被整体回滚。
- 新逻辑只在 `coverage_convergence` 中保护“已有 executable 且属于 repairableExecutableCandidateIds”的候选：如果 AI 改写在后续守卫中变为 manual/needs_review，则恢复该候选收敛前已通过门禁的 executable 短链路，并记录 `convergence_repair_restore_count`。AI 的坏改写不会覆盖原路径，新增验收仍必须由其它候选或后续收敛真正证明。
- 这不放宽覆盖门禁、scorer、dry-run、Runner、坐标、账号/授权/选文件或深层外部动作限制；如果恢复原 executable 后仍缺新增验收，最终门禁继续失败，但不会因为一个候选的坏改写丢掉其它候选可用增量。
- 新增回归覆盖两种线上形态：AI 把显式可达性拆到通用风险流时，收敛仍聚焦各主分支 executable；repairable executable 的改写若含未被当前需求支持的动态文件名并被守卫降级，平台恢复原 executable 且不泄漏该动态文字。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
```

本轮修复尚未提交/部署。提交、推送、部署后必须再次用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，监督生成、首批、remaining、可能的 AI 修复和所有 Runner 报告到真实终态。

### 2026-07-20 真实回归：有界落地尾链必须从 verified baseline 恢复前置，并规范化当前分支条件尾链

用户确认 `ebbf857` 部署后，以同一需求和 Figma 发起完整 Agent `agent-1784540218073-9ff88889`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康；Windows Runner 在线，固定 OPPO ready，上报 qwen3.6 模型族。本轮仍在 `GENERATE_YAML / 30%` 终止，没有创建 Runner job，没有向 OPPO、华为或第二台设备下发。
- Figma 正确解析 `4 页 / 4 图 / 忽略 0`。4 张图分 4 批真实送入 qwen3.6-plus，批次均 completed、`fallback=false / finishReason=stop / hardGate=false`；设计资料仍是完整送 AI 判断的软参考。
- AI 规划 8 条业务分支；本轮结构与上一轮不同：扫描自动候选 `TC-007` 只覆盖展示/文案，扫描可达性被放入人工项“扫描复印页-点击百度网盘入口可达性校验”，且该人工项缺少 `case_id / coverage / requirementRefs`，步骤和断言带有“若存在 / 若入口存在”的人工条件文案。
- 生成阶段初始覆盖缺 6 个，最终仍缺 `REQ-003-CHECK-04` 扫描可达性，错误为“点击百度网盘入口并校验目标页面稳定可达”。平台没有采用兜底 YAML，覆盖门禁行为正确。

深层根因与通用修复：

- `ebbf857` 已允许“当前扫描来源页证据 + 同目标兄弟落地尾链”，但线上新形态中的扫描来源页自动候选只引用了 verified baseline，没有写 `precondition`。reachability 组合路径在已找到 selected baseline 时没有再从 baseline 恢复 `# baseline.start_page`，导致前置为空并丢弃有界证据。
- 当前文档 donor 尾链又包含“已离开文档打印页”，按“不泄漏捐赠分支来源页”的安全规则被正确拒绝。因此不能为了过门禁复用带 donor 来源页的兄弟尾链。
- 扫描当前人工项本身有正向点击后观察，但因没有 `requirementRefs` 被 donor 过滤提前丢弃；同时“查找并点击「百度网盘」入口（若存在）”和“若入口存在，点击后…”未被规范化，不能直接进入 Runner。
- 新逻辑只做窄修复：source candidate 已有 verified selected baseline 但缺 precondition 时，从该 baseline 恢复前置；仅对匹配当前缺失验收且包含当前分支路径的 manual donor，从显式需求矩阵推断 `requirementRefs`；将“若存在”条件点击规范化为真实可见文字点击，并剥离条件前缀，补入“已离开来源页、落地页元素可见、无崩溃、无白屏”的稳定首屏断言。
- 仍拒绝 donor 来源页泄漏、不同/前后缀/第二目标、未验证 baseline、账号/授权确认/选文件等深层外部动作；没有放宽 scorer、覆盖门禁、坐标、Figma 解析、Runner、Sonic、设备策略或历史 YAML。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
git diff --check
npm test
```

- 新增两个回归：verified source baseline 缺 precondition 时仍能生成 bounded landing；兄弟尾链泄漏 donor 来源页时，当前分支 manual 条件尾链会被正向规范化并绑定 `REQ-003-CHECK-04`。
- 后端 61、undefined-name、前端 69、AI Gateway 46、动态模型目录/回退、Skill 契约 3 个 fixture、桌面/移动视觉回归均通过。第一次 `npm test` 遇到临时端口 `57477 EADDRINUSE`，端口释放后原样重跑整套通过。
- 本轮修复尚未提交/部署。提交、推送、部署后必须再次使用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，监督生成、首批、remaining、可能的 AI 修复和所有 Runner 报告到真实终态。

### 2026-07-20 真实回归：最终收敛按验收增量合并，不再用整体回滚丢掉 AI 已补缺口

用户部署 `31afa8b` 后，以相同需求和 Figma 发起完整 Agent `agent-1784514545628-705062d7`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康；Windows Runner 在线，固定 OPPO ready，上报 qwen3.6 模型族。本轮在 `GENERATE_YAML` 终止，没有创建 Runner job，没有向 OPPO、华为或第二台设备下发。
- Figma parser 未修改，仍解析 `4 页 / 4 图 / 忽略 0`。4 张图按 4 个单图批次全部送入 `qwen_plus / qwen3.6-plus`，均 `finishReason=stop / fallback=false / hardGate=false`；设计资料继续是完整送 AI 判断的软参考。
- 路由正确为 `new_requirement_source / generate_draft`。初始 portfolio 有 5 条 executable，覆盖 `8/12`，缺文档可达、照片可达、扫描同级关系和扫描可达；`unresolvedAutomaticCount=0`，证明 `31afa8b` 的“模型漏回自动候选”修复已生效，但本轮没有触发该形态。
- qwen 最终收敛实际补齐了上述 4 个缺口，提案达到 `11/12`；但改写 `TC-002` 可达路径时丢掉了上一轮已证明的 `REQ-002-CHECK-02` 照片打印同级关系。同模型的现有语义纠偏仍漏掉该保留断言，单调收敛门禁因 1 个回归验收维度原子拒绝整份提案，因而最终又保留收敛前的 `8/12`。
- Agent 真实终态为 `FAILED / GENERATE_YAML / 30%`；最终错误因为原子回滚再次列出 4 个原始缺口，不是视觉、Runner、Windows 脚本、设备或 scorer 导致。

深层根因与通用修复：

- 最终收敛本质是“为已有候选增加缺失验收路径”，旧协议却要求 AI 重写整个 `flow / assertionTarget`。AI 即使正确补出新的点击和首个稳定落地页，也可能在长上下文中遗失上一轮已通过审计的展示/同级/文案断言。再调一次模型既增加延迟，也不能确定性恢复已知事实。
- 新逻辑把收敛结果当作 AI 负责的“新验收增量”。平台从聚焦候选构建独立于模型响应的 `preserveContractByCaseId`；即使模型只返回标题、遗漏 caseId、伪造内部字段，或后续有界证据重建 item，最终仍按平台规范化后的 canonical caseId 读取原候选契约。
- 对同一候选中 `contractRoles=preserve` 的 visibility/relation/copy 验收，平台只能从该候选原有 assertions 或明确断言步骤中携带证据，并放在“最后一次非目标导航之后、同一目标点击之前”的来源页窗口。目标点击后、前一页面、条件/负向/复合导航文案均不能证明来源页状态；同窗口内完全相同的非导航断言只保留一条，避免无谓耗尽 8 步上限。
- 可携带证据采用保守的正向结构：按验收类型识别引号外的可见性、同级关系或准确文案谓词；拒绝中英文导航、条件/负向/错误语义、引号外未识别英文，以及引号内英文负向语义。平台不会生成 `repair` 或 `evidence` 角色的新业务事实，无法安全携带时仍进入原有同模型语义纠偏；纠偏后仍缺失则继续由覆盖门禁阻断。
- preserve 契约在可信基线导航、当前 Figma 叶子和动态终态适配全部完成后再执行一次；任何最终适配若破坏来源页断言，候选会降级并由覆盖门禁阻断，不会用中间态审计冒充最终 YAML 覆盖。
- 未改动 Figma parser、`router.py`、生成数量策略、执行模式、Runner、Sonic、scorer、设备策略或历史 YAML。

生产产物离线重放与验证：

- 原样读取线上保存的 5 条自动候选、9 条人工候选、12 个验收维度和 3 条成功基线，模拟线上那种“补可达但漏旧关系断言”的 qwen 返回。聚焦候选仍精确为 `TC-001 / TC-002 / TC-003 / MC-003 / MC-002 / MC-001`。
- 新逻辑携带 `REQ-001-CHECK-02 / REQ-002-CHECK-02 / REQ-003-CHECK-03`；文档与照片的同级关系断言均位于“点击百度网盘”之前，扫描原文案证据也保留在来源页。不触发第二次模型调用，最终 portfolio 为 `12/12 / 5 executable / missing=0 / unresolvedAutomatic=0`，三个执行流均不超过 8 步且只用真实可见文字定位。
- TDD 先后在旧逻辑上复现：保留断言丢失、点击后证据冒充来源页、负向/英文导航证据绕过、模型标题映射和伪造契约、最终视觉/基线改写后二次丢失，以及同窗口重复断言占满 8 步。独立 reviewer 的 P1/P2 反例也按 relation/visibility 真实类型完成 RED/GREEN；最终复核为 `No findings`。残余风险是少见别名或未枚举措辞可能被保守降级，但不会放宽覆盖门禁或伪造正向事实。
- 完整检查命令：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态 7 模型目录及多类回退、Skill 契约 3 个 fixture，以及桌面/移动视觉回归全部通过。
- 本轮修复将随本次提交落盘，当前尚未推送和部署，不能宣称完整 Agent 已闭环成功。部署后必须继续用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，监督生成、Smoke、remaining、AI 修复及所有 Runner 报告到真实终态。

### 2026-07-20 真实回归：最终收敛漏回自动候选时使用同模型定向语义纠偏

用户部署 `3689aa1` 后，以相同需求和 Figma 发起完整 Agent `agent-1784512888040-e6ea0da4`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康；Windows Runner 在线，固定 OPPO ready，并上报 `qwen3.6-plus / qwen3.6`。本轮在生成阶段终止，没有创建 Runner job，也没有向 OPPO、华为或第二台设备下发。
- Figma parser 未修改，仍解析 `4 页 / 4 图 / 忽略 0`。4 张图按 4 个单图批次全部送入 `qwen_plus / qwen3.6-plus`，分别约 `28 / 22 / 23 / 21s` 完成，均 `finishReason=stop / fallback=false`；`sentToAiForJudgement=true / aiJudgementCompleted=true / hardGate=false`，设计资料继续作为完整送 AI 判断的软参考。
- 路由正确为 `new_requirement_source / generate_draft`。平台 MM skills 生成 `8 flows / 12 scenarios / 7 cases`，执行规划选出 5 条 executable；文档、照片、扫描三个需求分支及 12 个显式验收维度均进入最终覆盖审计，5 寸照片明确位于照片打印分支。
- 初始 executable portfolio 覆盖 `8/12`，缺文档可达、照片可达、扫描同级关系和扫描可达。最终 qwen 收敛补齐前三项，但漏回聚焦自动候选 `TC-003`，只留下扫描可达 `REQ-003-CHECK-04` 未覆盖；最终为 `11/12 / 5 executable / 1 needs_review`。
- Agent 真实终态为 `FAILED / GENERATE_YAML / 30%`，错误明确为“扫描复印：点击百度网盘入口并校验目标页面稳定可达”。平台没有采用部分/兜底 YAML，也没有进入 Runner，覆盖门禁行为正确。

深层根因：

- `TC-003` 首轮 AI 已引用扫描成功基线 `d623c1e73180bfac`，能够从首页进入扫描复印并等待百度网盘入口；但候选只写了“查找入口”，没有点击入口后的稳定落地页观察，所以被标记为本轮 `repairableExecutableCandidate`。
- 最终收敛请求已包含 `TC-003` 及其 4 个候选本地验收契约：保留可见性/文案，补齐同级关系/可达性。qwen 的结构化响应只修了文档和照片可达，并用 `TC-008` 补扫描同级关系，却完全遗漏 `TC-003`；`review` 还错误声称“其他候选由平台保留”。
- 平台原有语义纠偏只检查“模型已经返回 executable、但 flow/assertionTarget 未满足契约”的候选。被模型完全漏回的自动候选不进入纠偏；平台随后按安全策略将它降为 `needs_review`，导致显式覆盖和分类终态同时失败。
- 旧本地回放覆盖了有界证据候选的模型遗漏，可由已审计证据恢复且不增加模型调用；没有覆盖“候选承担显式缺口、没有可自动恢复的有界证据、模型又完全漏回”的线上形态。这是本地检查通过而线上仍失败的直接差异。

通用修复：

- 复用现有且唯一的 `acceptance_repair_retry`，在四个结构化分类组中检测模型漏回项。只有候选来自自动池、携带 `requiredAcceptanceChecks`、仍承担显式验收缺口，且没有 `convergenceEvidence.eligible=true` 可由现有有界证据恢复时，才加入同一模型的小范围语义纠偏。
- 纠偏请求只包含漏回 caseId，并携带完整 `missingChecks / missingPreservedCheckIds / omittedFromClassification`。AI 必须明确返回 executable 或 manual；平台不替 AI 编写业务路径、不自动升级，不新增生成轮次上限之外的重型调用。
- 若 AI 返回 executable，flow/assertionTarget 仍必须真实证明新增和保留契约，再继续经过需求覆盖、分类终态、可信基线、可见文字路径、YAML、scorer、dry-run 和 Runner 门禁；若 AI 判断为 manual、再次遗漏或仍缺验收证据，最终门禁继续失败。
- 已有有界证据遗漏恢复保持原行为和单次模型调用，不为可恢复项增加延迟。未修改 Figma parser、`router.py`、执行模式、Runner、Sonic、scorer、设备策略或历史 YAML。

生产候选离线重放与验证：

- 原样读取线上保存的 6 个自动候选、8 个人工候选、3 条 AI 选中成功基线和初始 portfolio；新检测精确命中 `TC-003`，反馈同时携带 `REQ-003-CHECK-01/02/03/04`，不会为了补点击而丢掉原可见性、同级关系或文案断言。
- TDD 回归先在旧代码上稳定失败：模型只被调用 1 次，漏回项没有纠偏；最小实现后变为 2 次，第二次请求只含唯一漏回候选，使用原选择模型，并在模拟 AI 返回完整可达短链路后通过最终 portfolio audit。

已验证：

```bash
python3 tests/backend_static_checks.py
npm test
```

- undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录/回退检查、Skill 契约 3 个 fixture，以及桌面 / 移动端视觉回归全部通过。
- 本轮修复尚未部署，不能宣称完整 Agent 已闭环成功。提交、推送并部署后，必须再次使用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，持续监督生成、首批、remaining、可能的 AI 修复和所有 Runner 报告到真实终态。

### 2026-07-20 部署后真实回归：关键帧佐证的临时弹窗修复不能冒充业务改路

用户部署 `c2ee824` 后，以相同需求和 Figma 发起完整 Agent `agent-1784508011655-ac1b0f0d`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康；Runner 在线并上报 `qwen3.6-plus / qwen3.6`。所有 dry-run、首批和修复重跑均只绑定固定 OPPO，没有选择或下发华为设备。
- Figma parser 未修改，仍解析 `4 页 / 4 图 / 忽略 0`。4 张图按 4 个批次全部送入 qwen3.6-plus，约 `32 / 20 / 20 / 17s` 完成，均无回退、`hardGate=false`；设计资料继续作为完整送 AI 判断的软参考。
- AI 规划 `8 flows / 12 scenarios / 6 automated cases`，最终生成 6 个一一对应 YAML：文档、照片、扫描各有 UI 展示和可达检查。6 条均为 `executable / scorer 100 / dry-run 通过`，使用真实可见文字且无坐标；5 寸照片明确位于照片打印分支。
- 首批在固定 OPPO 串行执行：文档 UI `job_1784508482425_00004` 成功，扫描 UI `job_1784508607121_00005` 失败，照片 UI `job_1784508738427_00006` 失败；平台真实保留 `1 passed / 2 failed`，remaining 3 条按冒烟门禁延后。Agent 终态为 `FAILED / RERUN / 95%`，不是“全部失败”。
- 文档报告真实确认“百度网盘”与“本地文档 / QQ文档 / WPS文档”同排。照片失败帧显示底部照片 Tab 的聚合页仍需点击大卡片“照片打印”；结构化修复引用当前照片分支成功基线、补一次真实文字点击后，`job_1784508956985_00009` 在同一 OPPO 成功，最终 5 寸照片页可见“相册导入 / 微信导入 / 相机拍照 / 百度网盘”。
- 扫描失败帧显示点击“证件扫描”后出现“温馨提示”权限说明弹窗，底部只有“取消 / 确定”，因此原脚本下一步查找“立即使用”失败。AI 正确提出在原失败点处理“确定 / 允许”后继续原路径，但两次均返回 `usedBaselineIds=[]`；旧门禁把自然语言 `ai` 一律计为业务导航修改，以 `navigation_change_without_baseline_citation / navigation_change_without_branch_baseline` 拒绝，最终只创建 1/2 条修复重跑。

深层根因与通用修复：

- 当前分支基线引用门禁对真实业务改路是必要的，但“失败关键帧已经显示临时系统/权限弹窗，补丁只关闭遮罩并继续原路径”不应要求一条成功业务路径基线。该证据来自本次 Runner 画面，不是 AI 猜测，也不能由历史基线证明。
- 新门禁只在以下条件全部成立时豁免基线引用：存在报告关键帧；当前失败任务的 Runner 文本有非否定的弹窗/遮挡证据；结构化 patch 只使用 `insert_before / insert_after`；原业务导航完整保序；最多新增 1-2 个弹窗动作；每个动作同时含弹窗语义和同一条弹窗证据中真实出现的按钮文字。放行原因以 `transientOverlayChange / baselineCitationExempt` 写入修复摘要和草稿。
- 普通定位失败、无关键帧、删除/替换原步骤、超过两个新增动作、按钮文字未被证据观察到，或借弹窗证据插入其它业务入口，仍继续触发原分支基线门禁。每个“点击/选择”子句只能指向弹窗控件，并拒绝“进入/前往/导航/打开页面”等转场语义，不能把业务改路藏在同一个 `ai` 动作里。精确文案断言、source/Figma 叶子、scorer 非回退、YAML 强校验和固定设备约束均未放宽。
- `repair_patch_planner.v1` 同步要求 AI 只使用关键帧中真实可见的弹窗上下文和按钮文案；系统权限弹窗可由自然语言动作有界处理，但不得顺带执行业务导航。未修改 Figma parser、`router.py`、执行模式、Runner、Sonic、scorer、设备策略或历史 YAML。

生产失败产物离线重放：

- 原样读取生产扫描 YAML，在“点击证件扫描”后应用线上 AI 提议的条件弹窗处理和短 sleep；原始“立即使用”路径及“百度网盘”精确断言全部保留。
- 新门禁得到 `ok=true / navigationChanged=true / baselineCitationExempt=true / matchedControls=[确定] / assertionContractPreserved=true`，YAML 强校验通过，原始和修复后的 scorer 均为 `100 executable`。
- 负向回归确认：只有普通 `failed to locate element` 时仍拒绝；其它失败任务或 AI 汇总中的弹窗描述不能串用；即使有弹窗关键帧，单独插入或藏进复合 `ai` 的无关业务入口仍拒绝。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/agent_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录/回退检查、Skill 契约 3 个 fixture，以及桌面 / 移动端视觉回归全部通过。
- 本轮修复尚未部署，不能宣称完整 Agent 已闭环成功。提交、推送并部署后，必须再次使用完全相同输入和固定 OPPO `ecbfd645` 发起完整 Agent，持续监督首批、remaining、可能的 AI 修复和所有 Runner 报告到真实终态。

### 2026-07-20 真实回归：失败关键帧与成功基线驱动结构化 AI 修复，不再重写整份 YAML

用户部署 `5dff82c` 后，以相同需求和 Figma 发起完整 Agent `agent-1784475423573-fd7be255`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康；Runner 在线并上报 `midscene_model_family=qwen3.6`。Runner 虽登记华为和 OPPO 两台设备，本 Agent 的 dry-run、首批及后续计划始终绑定 OPPO，未向华为下发。
- Figma parser 未修改，仍解析 `4 页 / 4 图`。4 张图按 4 个批次全部送入 qwen3.6-plus，约 `17-20s` 完成，无回退、`hardGate=false`；设计资料继续是完整送 AI 的软参考。
- AI 规划 `8 flows / 12 scenarios / 7 cases`，最终接受 6 条 executable 并生成 6 个一一对应 YAML，覆盖完整且没有机械凑 5 条。6 条均通过 static/scorer/dry-run，使用真实可见文字且无坐标。
- 首批在固定 OPPO 串行执行：文档、照片真实成功，扫描失败；报告真实保留 `2 passed / 1 script failed`。文档报告帧可见“本地文档/百度网盘/QQ文档/WPS文档”，扫描失败帧可见“小白扫描王”及“本地导入/相册导入/微信导入”，右侧同级入口被屏幕边缘裁切。remaining 3 条按门禁延后，没有并发抢占同一设备。
- failure review 确定性归因为 `SCRIPT_ISSUE / scroll_not_effective / confidence=0.93 / can_auto_repair=true`。失败分析和修复规划均使用 qwen；修复请求携带 3 张真实报告关键帧及 Top3 已验证扫描基线 `02b01e0cab690788 / d623c1e73180bfac / 3623ac0b65b5d2ca`。

深层根因：

- Agent 旧修复链路绕过已有 `repair_patch_planner.v1`，直接调用 `/ai/optimize-yaml` 要求模型重写整份 YAML。首个 qwen 候选的业务判断正确，增加了官方横向 `aiScroll`；但 Gateway 旧整 YAML validator 把合法子字段 `direction/distance/scrollType` 当成三个独立动作，错误拒绝候选。唯一一次整 YAML 纠错随后超时，最终草稿为 `REJECTED / ai_no_yaml`。
- 模块化 `repair_service.apply_task_repair_patches()` 仍是迁移时的空 stub，导致 Agent 无法采用“AI 只规划局部补丁、平台负责应用”的短链路。本地旧回放只验证了最终候选门禁，没有覆盖线上真实的模型输出协议与 Gateway validator 差异，这是此前“本地 OK、线上失败”的直接原因。
- 该问题不是 Figma、千问业务理解、Runner、Windows 脚本、华为设备、用例数量或 scorer 导致；本轮没有降低任何覆盖和可执行门禁。

通用修复：

- Agent 现在调用创建任务时所选模型的 `repair_patch_planner.v1`，输入原需求、失败任务 block、失败原因、最多 3 张报告/录屏关键帧、Top3 当前分支成功基线、Figma/source evidence、不可变精确文案断言，以及固定 Runner/设备约束。选定模型保持不变，只有 Gateway 因超时、不可用或能力不足时才按既有策略回退。
- AI 最多返回 2 个局部结构化 patch；Task Server 用唯一完整 `动作: 值` 锚点应用到唯一失败 task，并安全序列化 YAML 标量、正确嵌套 `aiScroll` 子字段。补丁只允许可见文字 AI 动作，确定性拒绝 `runAdbShell/runWdaRequest/javascript/launch/terminate`、XPath/locator、非法方向/距离、歧义或部分锚点；`remove_step` 只能删除冗余 sleep，不能删除等待、点击、断言或生命周期动作。
- 应用后继续走原有 assertion contract、source/Figma 叶子、导航 diff、当前分支基线引用、起始稳定等待、Task Server 强校验和语义 no-op 门禁；新增 scorer 非回退门禁，原 executable YAML 修复后仍必须是 executable。AI 不能用当前产品值覆盖需求期望，也不能把成功基线的深层叶子复制到当前需求。
- 首次补丁结构或平台门禁失败时，只给同一模型一次有界纠错：最多 2 张最新关键帧、当前失败任务、上一补丁及精确校验错误；不再重传和重写整份 YAML。首轮默认上限 90s、纠错 60s，减少 token、超时和无关业务漂移。
- 该流程对应成熟框架共同边界：Playwright healer 重放失败、检查当前 UI、提出补丁并由 guardrail 限制重跑；BrowserStack Appium Self-Heal 使用最近成功上下文生成替代定位、记录修改，并明确不把真实产品或系统故障伪装成可修复脚本问题。

线上真实产物回放：

- 原样读取生产保存的扫描失败 YAML、3 张关键帧名称、3 条基线、失败分类和固定设备配置，完整回放 `_tool_generate_repair()` 与重跑准备。一次结构化补丁后得到 `SUCCESS / WAIT_CONFIRM / repairSource=ai_skill_patch`，请求模型为 qwen3.6-plus、`fallback=false`、`allowOtherDevices=false`。
- 修复只在“等待扫描复印页面加载完成”后插入一次针对“本地导入、相册导入、微信导入所在横向入口区域”的 `aiScroll singleAction / right / 350`，原百度网盘文案、同级关系、点击和终态断言全部保留。
- 原 YAML 与修复 YAML 的 scorer 均为 `100 executable`；重跑准备只生成 1 个修复目标，继续绑定 `win-runner-01 / ecbfd645 / fixed`，不会重跑旧失败脚本或选择第二台设备。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/repair_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态 7 模型目录及空答/截断/图像/超时回退、Skill fixtures `3/3`、Playwright 桌面/移动视觉回归全部通过。第一次 `npm test` 仅遇到测试夹具临时端口竞争 `EADDRINUSE`，端口释放后原样重跑整套通过。
- 未修改 Figma parser、`router.py`、执行模式、Runner、Sonic、scorer、历史 YAML 或设备策略；用户已有 dirty 文件继续保持未暂存。
- 本轮提交待用户 push / 部署。部署后必须再运行完全相同的完整 Agent，监督固定 OPPO 上首批与 remaining 到真实终态；不能以离线回放代替线上成功。

### 2026-07-19 真实回归：最终 executable 到 YAML 必须原子保真，独立断言属于点击后终态

用户部署 `ed14bdf` 后，以完全相同需求和 Figma 发起完整 Agent `agent-1784473300752-48472e24`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康；Windows Runner 1 台在线并上报 `midscene_model_family=qwen3.6`。本轮在生成阶段终止，没有创建 Runner job，没有向 OPPO 或同 Runner 上的第二台设备下发。
- Figma parser 未修改，仍解析 `4 页 / 4 图 / 忽略 0`。4 张图按 4 个单图批次全部送入 qwen3.6-plus，分别约 `26 / 17 / 15 / 11s` 完成，均 `fallback=false / finishReason=stop / hardGate=false`。AI 在 5 寸照片设计页识别到百度网盘入口及同级关系；设计资料继续是完整送 AI 的软参考。
- 初始规划得到 5 条 executable、9 条 manual，覆盖 `10/12`。最终 qwen 收敛真实调用成功且无回退，把 `TC-007` 照片可达和 `MC-001` 扫描同级关系升级为 remaining executable；最终 portfolio 为 `12/12 / 7 executable / missing=0`，证明 `ed14bdf` 已保留 AI 收敛契约，也没有按 5 条规划目标机械截断。
- `TC-007` 使用真实可见文字路径 `首页 -> 照片打印 -> 照片打印 -> 5寸照片 -> 百度网盘`，点击百度网盘后只校验首个合法页面、授权弹窗或系统提示及无白屏/崩溃；不含坐标、账号、授权确认或文件操作。
- 实际转换却只生成 6 个 YAML，缺少 `TC-007`。其余 6 个 YAML 均通过 static/scorer 100。Agent 因唯一缺口 `REQ-002-CHECK-04 照片打印点击百度网盘后目标页面稳定可达` 在 `FAILED / GENERATE_YAML / 30%` 阻断。

深层根因：

- `case_to_task_yaml()` 会在所有自然语言步骤之后渲染独立 `assertions`，所以 `TC-007` 的终态断言实际位于最后一次百度网盘点击之后，是合法的点击后观察。
- `_case_is_bounded_external_landing_check()` 却只接受 `steps` 内显式存在点击后等待；当最后点击之后没有重复写一遍等待、终态只存在于 `assertions` 时，它错误返回 false。随后 `_case_manual_block_reason()` 因文案提到“授权弹窗”把已经由 AI 和 portfolio 接受的 `TC-007` 再降为 manual。
- 最终覆盖审计发生在该确定性 Runner eligibility 转换之前，旧链路没有核对“已接受 executable ID”和“实际 YAML case ID”是否一一对应，因此以部分 6 个 YAML 继续返回，直到 Agent 下游覆盖复核才发现缺口。

通用修复：

- 有可信基线、已应用路径、显式需求映射、真实目标点击、多合法首屏终态及稳定性断言的有界外部跳转用例，现在允许把独立 `assertions` 作为点击后的终态观察；深层授权确认、账号/验证码、文件选择等动作仍由原门禁阻断。
- 新增 executable-to-YAML 原子转换审计：最终明确接受的每个 executable case ID 必须恰好对应一个 Runner-ready case 和一个 YAML。确定性门禁仍可拒绝风险用例，但必须在写任何 YAML 前整批失败，并记录 case ID、标题、拦截阶段和原因，不能静默返回部分结果。
- 3/5/8 继续只是 AI 规划目标，不是转换上限。本轮回归显式覆盖“目标为 5、AI 最终收敛出 7 条”的顺序无关转换；没有降低 portfolio、static、scorer、dry-run、Smoke 或 Runner 门禁。

真实产物离线重放：

- 原样读取线上保存的 7 条 case，不改任何业务字段。修复前稳定复现 `ready/yaml=6` 且 `TC-007` 被以权限弹窗风险降级；修复后 `accepted=7 / runnerReady=7 / yaml=7 / missing=0 / duplicate=0`。
- 7 个 YAML 全部使用真实可见文字、无坐标；dry-run `7/7 ok`，static 均为 executable，scorer 均为 `100 executable`。
- 负向回归移除同一候选的可信有界证据后，确定性门禁仍正确拒绝该候选；新增转换契约同时报告 `missingYamlCaseIds=[TC-007] / stage=runner_eligibility`，证明没有绕过安全边界。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录及空答/截断/图像/超时回退、Skill fixtures `3/3`、Playwright 桌面/移动视觉回归全部通过。
- 未修改 Figma parser、`router.py`、执行模式、Runner、Sonic、scorer、设备策略或历史 YAML；用户已有 dirty 文件继续保持未暂存。
- 本轮提交待用户 push / 部署。部署后再次运行完全相同 Agent，必须确认 7 条 YAML 全部生成，再只在固定 OPPO 上串行监督最多 3 条 Smoke 和 remaining 到真实终态。

### 2026-07-19 真实全链路：保留精确需求断言，失败帧只能修路径不能改期望

用户部署 `a063ec5` 后，以同一需求、Figma 和固定设备发起完整 Agent `agent-1784456588304-b48b8363`：

- `101.34.197.12:8091 / :8088` 健康，AI Gateway、Sonic 健康；Windows Runner 在线并上报 qwen3.6 模型族。所有 Agent dry-run、首批、remaining 和修复重跑均只使用 `win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`，没有向华为或第二台设备下发。验证期间出现的其它 Sonic 任务与本 Agent 无关，后续不得等待它们。
- Figma parser 未修改，仍解析 `4 页 / 4 图`；4 张图分别送入 qwen3.6-plus，`4/4` 批次完成、无回退、`hardGate=false`。AI 规划 8 个业务 flow，最终生成 8 条自动化用例和 8 个 YAML，portfolio `12/12 / missing=0`，`manual_reclassification_canonicalized_count=1` 证明 `a063ec5` 已在线生效。
- 8 条 YAML 全部通过 static、scorer 和 dry-run，使用真实可见文字且无坐标。人工复核确认文档打印、照片打印、扫描复印三条分支均有展示/关系/文案或可达覆盖；照片可达路径明确为 `照片打印 -> 5寸照片 -> 百度网盘`。
- 首批和 remaining 都在固定 OPPO 串行执行到终态。首次正式执行共 `2 成功 / 6 失败`：文档文案与文档展示通过；一寸照路径缺父页、首页抽象等待、跳转过程等待、扫描页实际缺少目标入口和条件式人工描述等分别失败。
- 文档可达修复正确利用真实报告帧和成功基线：删除 `等待页面跳转或弹窗出现`，改用已可见的百度文件列表/`去打印` 稳定态，关联重跑 `job_1784459094137_00023` 真实成功。
- 照片文案修复也正确利用失败帧和 6 寸照片成功基线，补出 `照片打印 -> 智能证件照 -> 一寸照`；关联重跑 `job_1784459219369_00024` 已到达目标页，并明确看到实际按钮为“百度网盘上传”。原需求/YAML 的精确期望是“百度网盘”，因此这应是产品文案差异。
- 旧逻辑把该差异继续归为 `SCRIPT_ISSUE`，第二轮 AI 把断言从“百度网盘”改成“百度网盘上传”，随后 `job_1784459561608_00026` 通过。这个通过是 assertion drift，不是业务恢复；虽然 Agent 最终仍为 `FAILED / RERUN / 95%`，报告却错误增加了一条 recovered。

深层根因：

- 修复门禁只检查 YAML 契约、导航 diff、分支基线和 source-backed 叶子，没有把原始精确可见文案当成不可变业务契约。AI 因而可以依据失败截图修导航，也可以错误地用当前产品值覆盖需求期望值。
- `classify_failure_by_context()` 将所有普通断言失败先归为可修复脚本问题；即使 Runner 证据同时给出明确 expected、actual 和“不严格等于”，也没有产品差异的确定性分类。汇总 AI 还能把已确认的产品失败再次降为脚本问题。
- 生成规范化只处理 `ai/aiAction/aiAct: 回到首页`，没有处理已经写成 `aiWaitFor: 等待 App 首页稳定显示` 的抽象状态；Midscene 可能把底部其它高亮 Tab 当成非首页。`等待页面跳转或弹窗出现` 描述的是短暂过程而非稳定终态，页面已经到达后反而会等待失败。
- `若存在则...若不存在反馈产品` 是人工评审说明，不是 Runner 可判真的等待条件；它应被还原成需求定义的明确可见状态，真实不存在时保留产品失败。

通用修复：

- 从 `aiWaitFor / aiAssert` 中结构化提取“严格等于/文案为”等带引号的精确 UI 值，作为 repairPolicy 中的不可变 assertion contract 送给 AI。候选返回后再次比较；删除、弱化或改值统一以 `assertion_contract_drift` 拒绝，不能下发 Runner。
- Runner 证据同时包含精确 expected、不同的 observed value 和明确 mismatch 语句时，确定性归类为 `PRODUCT_BUG / visible_value_mismatch / confidence=0.98 / can_auto_repair=false`。高置信产品结论不可再被汇总 AI 降级，后续只保留失败帧并生成缺陷草稿。
- 生成落盘前把抽象首页等待锚定到下一条真实文字点击目标，例如 `App 首页加载完成，可见「照片打印」入口`；当后续已有稳定页面状态时删除过程型跳转等待；把“存在/不存在并反馈”的人工分支改成明确目标入口可见状态。只匹配这些窄语义，不改普通等待、业务路径、用例数量或覆盖门禁。
- 修复摘要改为分别显示“可应用数 / 分析数 / 门禁拒绝数”，不再把 2 条可用草稿描述成覆盖 6/6。

真实产物离线重放：

- 直接重放线上第二轮错误修复，“百度网盘 -> 百度网盘上传”现在得到 `ok=false / assertionContractPreserved=false / assertion_contract_drift`。
- 同一 Runner 失败证据现在直接归为 `product_bug / visible_value_mismatch / can_auto_repair=false`，不会创建第三个修复 job。
- 直接重放线上 YAML：03 删除过程等待；04 将首页等待锚定到“照片打印”并删除重复跳转等待；08 将人工条件分支改成明确“百度网盘入口可见”。已有目标稳定态、点击和断言均保留。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ai_skill_service.py
python3 tests/backend_static_checks.py
git diff --check
```

- 后端 61 项全部通过；针对性回归覆盖断言漂移拒绝、保留断言的等待修复放行、expected/actual 产品分类、产品结论不可降级、抽象首页等待、过程等待和人工条件分支。
- 未修改 Figma parser、`router.py`、执行模式、Runner、Sonic、scorer、设备策略或历史 YAML；用户已有 dirty 文件继续保持未暂存。
- 本轮提交待用户 push / 部署。部署后再次运行完全相同 Agent，只监督固定 OPPO 到终态；不等待其它设备或无关 Sonic 任务。

### 2026-07-19 真实回归：AI 恢复 executable 时原子替换旧人工执行契约

用户部署 `2b91966` 后，以完全相同需求和 Figma 发起完整 Agent `agent-1784454424819-fba97f18`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 生产实际可用地址为 `101.34.197.12:8091 / :8088`，两端健康；AI Gateway、Sonic 健康，Windows Runner 1 台在线并上报 `midscene_model_family=qwen3.6`，固定 OPPO 上 `com.xbxxhz.box 4.45.0 (357)` ready。域名 `sonic.xiaobaiai.net` 当前解析到另一个可建立 TCP 但 HTTP 无响应的地址，真实验证未据此误判服务离线。
- Figma parser 未修改，复用并解析 `4 页 / 4 张 UI 图`；4 张图按 4 个单图批次全部送入 qwen3.6-plus，均一次完成、无回退。AI 生成 8 个业务 flow，明确拆分文档打印、照片打印、扫描复印的展示与可达性。
- 最终 AI 收敛把 `TC-001/002/003/004/005/006` 共 6 条候选判为 executable，portfolio audit 已达到 `12/12 / missing=0`；3/5/8 数量只是规划目标，没有为凑数生成额外用例。
- 随后的实际 YAML 转换只产出 5 条，并在最终覆盖复核中以唯一缺口 `REQ-001-CHECK-04 文档打印点击百度网盘后目标页稳定可达` 阻断。Agent 终态为 `FAILED / GENERATE_YAML / 30%`，没有创建 Runner job，也没有向第二台设备下发。

深层根因：

- 上游第一轮 AI 曾把文档可达 `TC-003` 判为 manual，其旧 `goal` 留有“若未授权需 Mock 或预置授权态”，并写入 manual reason。最终收敛 AI 已基于文档打印成功基线 `b6a163ea9dc815d9` 把它改为状态无关的短链路：只点击百度网盘并校验首个合法页面，无深层授权、账号或文件操作。
- 旧状态应用只在 `originExecutionLevel=manual` 时清理人工元数据。`TC-003` 原本由 AI 生成、后来暂时降级，因此 origin 仍是 `automatic`；第二轮恢复 executable 后，步骤、断言和新理由已更新，旧人工 `goal / reason` 却继续残留。
- `split_automation_ready_cases()` 的确定性闸门随后从旧 goal 读到 `Mock`，把已经通过 AI 收敛和 `12/12` 审计的 `TC-003` 再次转回 manual。直接把生产保存的 6 条 cases 喂给转换器，可稳定复现 `ready=[TC-001,002,004,005,006] / TC-003 manualized`。这不是模型漏选、Figma、数量截断、scorer 或 Runner 问题。

通用修复：

- 只要候选当前状态确实从 manual 恢复为 executable，并且权威 AI 已给出可信基线路径或有界证据、明确前置、完整 flow 和可见终态，就把这些字段作为新的原子执行契约：替换 goal、business path、preconditions、steps、assertions 和 expected result，清除旧 reason、数据准备及 suggested setup。
- 旧人工上下文不丢弃，转存到 `previous_manual_reason / previous_manual_context` 供审计，但不再参与 Runner eligibility。新增 `manual_reclassification_canonicalized_count`，线上可直接确认该路径是否生效。
- 该规则不直接放行候选：需求映射、基线可信度、路径完整性、static、scorer、dry-run、首批最多 3 条、固定设备和真实 Runner 门禁全部保留；manual -> needs_review 不会触发执行契约替换。

真实产物离线重放：

- 用生产 `TC-003` 的原 goal、manual reason、最终 AI flow、断言和基线 ID 重建两轮状态转换；正序和倒序候选均得到 6 个 ready cases 和 6 个独立 YAML，`TC-003` 不再回到 manual，旧 goal 只保留在审计上下文。
- 6 个 YAML 全部使用真实可见文字、无坐标；structure、dry-run 和 static 均 `6/6` 通过。scorer 均为 executable，`TC-001/002/003/005/006=100`，照片长链路 `TC-004=82` 且只保留非阻断的链路偏长建议。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录及空答/截断/图像/超时回退、Skill fixtures `3/3`、Playwright 桌面/移动视觉回归全部通过。
- 新增回归覆盖“automatic 候选先降 manual、再由 AI 恢复 executable”的真实两轮状态，并交换候选顺序后再次经过最终 YAML eligibility 闸门。
- 本轮改动完成本地提交后待用户 push / 部署；部署后必须重跑同一 Agent，并监督固定 OPPO 上首批 Smoke 与 remaining 到真实终态。

### 2026-07-19 真实回归：无显式 REQ 映射的 AI 来源页候选按精确验收意图收敛

用户部署 `bdc5640` 后，以完全相同需求和 Figma 发起完整 Agent `agent-1784450235670-fd8ad477`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康；Windows Runner 1 台在线，OPPO Android 15 上 `com.xbxxhz.box 4.45.0` ready。Runner 虽登记第二台设备，但本轮在生成阶段终止，没有创建 Runner job，也没有向第二台设备下发。
- Figma parser 未修改，解析 `4 页 / 4 张 UI 图 / 忽略 0`；4 张图分别送入 qwen3.6-plus，4 批均在约 `21-30s` 完成，未回退、`hardGate=false`。AI 业务计划生成 8 个 flow 并通过计划质量门禁。
- 初始 5 条 executable 已达到数量目标并覆盖 `11/12`。最终 qwen 收敛成功、无回退，9 个候选全部结构化分类；`unclassified_focused_candidate_count=0 / bounded_omission_recovered_count=0`，证明 `bdc5640` 的候选遗漏闭环已在线生效。
- Agent 终态仍为 `FAILED / GENERATE_YAML / 30%`，唯一缺口是 `REQ-003-CHECK-02`：扫描复印页百度网盘入口与当前页面同级入口的层级和位置关系。没有生成 YAML、precheck 或 Runner job。

深层根因：

- 上游 AI 已生成 `MC-001 扫描复印页-百度网盘入口同级关系人工确认`，其标题、步骤和期望结果明确包含“扫描复印 / 百度网盘 / 同级关系”，但没有 `coverage / requirementRefs`。旧有界证据在交给最终 AI 前要求候选先携带 REQ ID，因此这个最准确候选被排除，`acceptanceCheckCandidateIds` 为空；这不是模型遗漏、Figma 超时、数量门槛、scorer 或 Runner 问题。
- 已验证扫描基线 `d623c1e73180bfac` 的历史深链路为 `扫描复印 -> 证件扫描 -> 立即使用 -> 相册导入`。当前需求只需要扫描复印来源页上的同级关系，旧叶子不能复制到新用例；原路径适配至少要求两个候选 action，无法用候选唯一的“扫描复印”共同动作截断历史深层叶子。
- `MC-001` 的期望结果写有“与产品设计稿一致”，但本次 Figma 没有扫描复印同页 Frame。设计资料仍应完整送 AI 作为软参考，但平台不能把不存在的同页视觉证据包装成事实。

通用修复：

- 对完全没有原始 REQ 映射的上游 AI 候选，只有同时命中验收项的业务分支、目标文字和验收 kind，步骤确实进入该分支，且不含深层外部动作时，才允许绑定该唯一 canonical requirement point；已有 REQ 映射的候选仍严格保持原边界。绑定来源通过 `requirementRefsInferredFromAcceptanceIntent` 写入收敛证据和最终计划，便于审计。
- 成功基线与候选只有一个共同业务 action 时，若候选尾部明确描述当前来源页或保留目标跳转，允许在共同 action 后截断历史叶子；候选仍描述历史叶子时不接管，继续由现有高置信 Figma 叶子适配处理。已有视觉叶子回归保持通过。
- 没有当前同页视觉证据时，不继承候选中依赖 `Figma / 设计稿 / 原型 / 截图` 的断言，只使用原始需求定义的运行时可观察关系；这不改变 Figma parser，也不把视觉软参考变成硬门禁。
- 当唯一缺口证据属于 manual 候选时，最终模型请求不再机械附带全部已冻结 executable。线上产物回放从 6 个聚焦候选缩为仅 `MC-001`，精确矩阵为 `REQ-003-CHECK-02 -> MC-001`，减少无效 token 和模型改写绿色结果的机会。

线上产物离线重放：

- 直接读取 `/private/tmp/bdc5640-cases-final.json`，补入线上所选扫描成功基线的本地可信 snippet。即使模拟 qwen 仍把 `MC-001` 判为 manual，现有有界证据闭环也只提升该候选为 remaining executable。
- 生成路径为 `启动 App 并等待首页 -> 点击扫描复印 icon -> 校验百度网盘与当前页面同级入口的层级和位置关系`，不含历史的“证件扫描 / 立即使用”，也不宣称与不存在的扫描页设计 Frame 一致。AI 人工候选中的“观察页面导入区域”只是被动记录说明，路径抽取后不会进入 Runner。
- 最终 portfolio audit 从 `11/12` 变为 `12/12 / missing=0 / ok=true`；错误分支的无 REQ 候选不会被推断映射。
- 同一重放实际渲染 `6` 个独立 YAML；新增扫描关系 YAML 为真实文字 `aiTap / aiWaitFor / aiAssert`，无坐标，`static ok / dry-run ok（无 warning）/ scorer 100 executable`。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录与空答/截断/图像/超时回退、Skill fixtures `3/3`、Playwright 桌面/移动视觉回归全部通过。
- 未修改 Figma parser、`router.py`、执行模式、Runner、Sonic、scorer、设备策略或历史 YAML。

待完成：提交、推送并部署本轮修复；部署后用完全相同输入发起唯一 Agent，持续监督 4 个视觉批次、最终 YAML、固定 OPPO 上串行 Smoke、失败帧驱动修复和 remaining 到真实终态。任何阶段不得向第二台设备下发。

### 2026-07-19 真实回归：最终收敛必须按精确验收项选择候选，模型漏回已审计候选时有界闭环

用户部署 `a67cb48` 后，以完全相同需求和 Figma 发起完整 Agent `agent-1784443923344-6cd2fc19`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康，Windows Runner 1 台在线并上报 `midscene_model_family=qwen3.6`。本轮在 `GENERATE_YAML / 30%` 终止，没有创建 Runner job，也没有向同 Runner 登记的第二台设备下发任务。
- Figma parser 保持原实现，解析 `4 页 / 4 张 UI 图`；4 张图按 4 个批次全部送入 qwen3.6-plus，`attempted=4 / done=4 / aiJudgementStatus=completed / hardGate=false`。AI 业务计划生成 8 个分支并通过计划质量门禁，设计稿继续是完整送 AI 的软参考。
- 初始组合已有 `5` 条 executable，数量目标已满足；最终 qwen 收敛调用成功且未回退，聚焦 `TC-003/004/005/006 + MC-001/002`，消耗 `22602 prompt / 2161 completion` tokens。覆盖从 `6/12` 增加到 `11/12`，唯一缺口为 `REQ-003-CHECK-04` 扫描复印百度网盘可达性，因此 coverage gate 正确阻断 YAML 和 Runner。

深层根因：

- `TC-005` 只有扫描来源页导航和百度网盘 visibility / relation / copy 断言；`TC-006` 才包含真实的“点击百度网盘 -> 等待百度/登录/授权首屏 -> 非白屏断言”，并由平台有界证据精确映射 `REQ-003-CHECK-04`。
- qwen 的说明文字声称 `TC-005` 同时覆盖扫描 UI 与可达性，但结构化结果只返回 5/6 个聚焦候选，遗漏 `TC-006`。旧请求只给宽泛 requirement refs，没有把验收项到候选的精确归属作为顶层矩阵；应用层又只有在模型返回某个分类时才会使用该候选的 `convergenceEvidence`，所以遗漏项保持 manual。
- 这是模型结构化输出违反“每个聚焦候选恰好分类一次”的契约，不是数量门槛、Figma、scorer 或 Runner 问题。不能通过把 `TC-005` 文案解释成点击动作、降低覆盖门禁或硬凑用例解决。

通用修复：

- 最终收敛请求新增 `planningContext.focus.acceptanceCheckCandidateIds`，只列出本次实际发送给模型、且已由真实步骤/断言和 `convergenceEvidence.acceptanceCheckIds` 审计的 `验收项 ID -> 候选 ID`。提示要求每个缺口必须从对应矩阵中选择，visibility / relation / copy 不能代替 reachability，planning reason 的文字声明不计覆盖。
- AI 仍负责在合法候选中选择。只有最终模型漏回聚焦候选，且该候选同时满足 `eligible=true`、拥有精确 acceptance IDs、属于本次矩阵时，平台才把它恢复到现有 `needs_review` 分类入口；非矩阵项、证据不足项和模型明确返回的其它分类不被扩大。
- 恢复分类不是直接放行：同分支成功基线、前置、短路径、验收覆盖、YAML static、scorer、dry-run、固定设备和真实 Runner 门禁全部保留。没有增加模型轮次、硬凑数量或修改 Figma parser、`router.py`、Runner、Sonic、scorer、执行模式及历史 YAML。

真实产物离线重放：

- 直接读取线上保存的 `a67cb48` cases payload；旧审计为 `11/12`，唯一缺 `REQ-003-CHECK-04`。新 focus 只发送 `TC-006`，矩阵精确为 `REQ-003-CHECK-04 -> TC-006`。
- 模拟 qwen 再次遗漏 `TC-006` 后，trace 只记录并恢复 `TC-006`；其 `boundedConvergence.acceptanceCheckIds` 仍只有 `REQ-003-CHECK-04`，最终 portfolio audit 为 `12/12 / missing=0 / ok=true`。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录及空答/截断/图像/超时回退、Skill fixtures `3/3` 和 Playwright 桌面/移动视觉回归全部通过。

待完成：提交、推送并部署本轮修复；部署后再次用完全相同输入发起唯一 Agent，持续监督 4 个视觉批次、最终 YAML、固定 OPPO 上串行 Smoke、失败帧驱动修复和 remaining 到真实终态。任何阶段不得向第二台设备下发。

### 2026-07-19 真实回归：修复 AI 候选必须携带精确校验反馈，“导航保持不变”不能误判为路径修改

用户部署 `0741347` 后，以完全相同需求和 Figma 发起完整 Agent `agent-1784441215220-ba6b5958`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康；Windows Runner 在线并上报 `midscene_model_family=qwen3.6`。首批、环境原样重跑及 dry-run 均只使用固定 OPPO，没有选择或执行同 Runner 上的第二台设备。
- Figma parser 保持原实现，解析 `4 页 / 4 张 UI 图`。4 张图按 4 个批次全部送入 qwen3.6-plus，`attempted=4 / done=4 / status=completed / fallback=false / hardGate=false`；设计资料继续是 AI 软参考，不是绕过需求或执行门禁的硬判定。
- AI 规划 `8` 个业务流，生成 `12` 个场景、`7` 条自动化用例和 `7` 个 YAML；需求覆盖审计通过，static、scorer 和 Runner dry-run 为 `7/7`，全部使用真实可见文字，没有坐标动作。人工复核确认文档打印、照片打印、扫描复印三条业务分支均有展示/关系/文案或可达覆盖；两条照片 YAML 都保留 `照片打印 -> 5寸照片 -> 百度网盘`，可达 YAML 保留真实目标点击。
- 首批只选择 3 条并在固定 OPPO 串行执行。文档展示 `job_1784441684210_00004` 首次恰好 300 秒失败，Runner 报告为 qwen/Midscene 请求 abort，归类 `ENV_ISSUE`；同一原 YAML 的安全重跑 `job_1784442397939_00009` 在 131.60 秒真实成功，证明不是 Windows Runner、ADB 或脚本缺陷。文档/WPS 关系 `job_1784442139343_00006` 在 89.09 秒成功。
- 扫描展示 `job_1784441998961_00005` 在 120.27 秒失败。Midscene 真实末帧已进入“小白扫描王”，横向导入行可见“本地导入 / 相册导入 / 微信导入”，下一项在屏幕右侧被裁切；失败属于 `SCRIPT_ISSUE / scroll_not_effective`，不是产品入口缺失。按逻辑任务计，首批已有 2 条真实通过，扫描 1 条未解决；remaining 4 条因 Smoke 修复门禁未启动，Agent 最终 `FAILED / RERUN / 95%`，没有把 Agent 失败解释为全部产品失败。
- 修复 AI 确实收到最新 3 张 Midscene 真机帧和 Top3 已验证扫描基线（证件扫描、文件扫描、PDF 合并），并使用创建 Agent 时选择的 qwen3.6-plus、无回退。模型正确提出在目标等待前对可见导入行进行一次有界横向 `aiScroll`，没有改业务导航；但最终完整 YAML 把 `target` 对象错误嵌套在 `aiScroll` 下，并在双引号标量中嵌入未转义的 `"百度网盘"`，Gateway 与 Task Server 均解析失败，修复草稿被正确拒绝而没有下发 Runner。

深层根因：

- 现有两次上限本身合理：首个候选失败后只允许同一模型再纠错一次。但第二次请求只拼入“Gateway 校验失败”等高层门禁文案，没有携带 Gateway/Task Server 的精确 parser error，也没有携带上一份被拒 YAML；模型看不到错误位置，无法可靠修正自身输出。
- `navigationClaimed` 旧逻辑只要 analysis/changes 出现“导航、路径、route”等词就判定声称修改导航。模型写“保持原有导航路径和断言逻辑不变”仍被误判为 `navigation_claim_without_yaml_change`，尽管真实 diff 只有 `aiScroll`。
- 有候选 YAML 时第二次请求不会压缩上下文；失败批次、关键帧和历史证据会继续占用纠错预算。该问题影响速度和精确度，但不应通过增加模型轮次、延长 Runner 超时或降低语义门禁解决。

通用修复：

- 导航声明改为识别“修改动词 + 导航对象”的正向语义，并先排除“保持原有导航不变、未修改导航、navigation unchanged”等否定/保留表达。真实 YAML 导航 signature 仍独立比较；只要实际修改 `aiTap/ai/aiAction/aiAct`，原有分支基线引用、启动稳定等待和 Figma 当前叶子保护继续生效。
- 唯一一次有界纠错现在同时携带：高层门禁 code、Gateway 原始 errors、Task Server 原始 issues、上一候选 analysis/changes 和被拒 YAML。提示明确逐条修正并返回完整 YAML，禁止无转义嵌套 ASCII 双引号；没有第三次模型请求，也没有绕过候选复验。
- 第二次请求无论首轮是否返回 YAML，都统一缩为当前失败任务、最近 2 张真实关键帧和 Top3 基线。`aiScroll` 提示补充当前 Midscene 合法形态：滚动区域是 `aiScroll` 的非空字符串，`direction / distance / scrollType` 为同一 flow item 的同级字段，禁止嵌套 `target` 对象。
- 回归覆盖真实线上形态：合法横向滚动且声明“导航保持不变”可通过；首个候选同时含错误导航声明、Gateway parse error 和坏引号时，第二次请求必须包含精确错误与原候选、压缩为 2 帧/1 个当前失败任务，并在合法候选返回后进入 `WAIT_CONFIRM`；连续两个非法候选仍在恰好 2 次调用后保持 REJECTED。
- 直接重放本 Agent 保存的被拒 YAML：新门禁不再产生导航误报，只保留真实 YAML 契约错误；将嵌套 `target` 改为标量 `aiScroll`、将内层 ASCII 引号改为中文引号后，同一完整候选得到 `Task Server ok=true / navigationClaimed=false / navigationChanged=false / issues=[]`。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py tests/ai_gateway_static_checks.py
python3 tests/backend_static_checks.py
python3 tests/ai_gateway_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录及空答/截断/图像/超时回退、Skill fixtures `3/3` 和 Playwright 桌面/移动视觉回归全部通过。
- 未修改 Figma parser、`router.py`、执行模式、Runner、Sonic、scorer、设备策略或任何历史 YAML。

待完成：提交、推送并部署本轮修复；部署后再次用完全相同输入发起唯一 Agent。必须确认扫描失败的真实帧驱动 AI 产出合法横向滚动 YAML，在固定 OPPO 上通过修复重跑，再串行执行 remaining 4 条到终态；成功后仍需人工复核三条业务分支、5 寸照片实体、入口文案/同级关系和真实可达页面。

### 2026-07-19 真实回归：目标跳转不得在基线路径适配后消失，失败 AI 空答必须有界纠正

用户部署 `7827802` 后，以完全相同需求和 Figma 发起完整 Agent `agent-1784434405265-959a92a5`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康；Task Server 文本/视觉模型为 `qwen3.6-plus`，Windows Runner 在线并上报 `midscene_model_family=qwen3.6`。本 Agent 的 3 个 dry-run 和 3 个正式 job 均只下发固定 OPPO，没有选择或执行 Runner 上登记的第二台设备。
- Figma parser 保持原实现，解析 `4 页 / 4 张 UI 图`。4 张图分别送入 qwen3.6-plus，4 批均在约 `14-22s` 完成，未回退、未触发硬门禁；视觉 AI 明确识别照片分支 `5寸照片 -> 百度网盘` 和同级关系。
- AI 生成 `12` 个场景、`6` 条自动化用例和 `6` 个 YAML。6 条均为 executable、scorer 100、static/dry-run `6/6`，且没有坐标动作。
- 首批 3 条 Smoke 在固定 OPPO 串行执行：文档 `job_1784434888320_00004` 通过，照片 `job_1784435148730_00006` 通过，扫描 `job_1784435029811_00005` 失败。双状态汇总正确保留 `2 passed / 1 script failed / productFailed=0`，没有把两个真实通过覆盖成全失败。
- 扫描 Midscene 报告的真实末帧显示相机权限“温馨提示”，可见按钮为“取消 / 确定”；原 YAML 复制历史基线继续点击“立即使用”，因此发生 element-not-found。平台正确提取 4 张真实 Midscene 关键帧并召回扫描分支成功基线，但 optimize-yaml 在约 88 秒后未返回 YAML。旧代码吞掉 Gateway 错误并在首个 `ai_no_yaml` 后直接退出，只保存 REJECTED 诊断草稿，没有 APPLY_SAFE_REPAIR、RERUN 或 remaining；Agent 终态为 `FAILED / COLLECT_REPORT`。

深层根因：

- 照片可达用例的上游 AI 原始路径包含 `5寸照片 -> 点击百度网盘 -> 等待落地页`。可信基线父路径适配先调用 `_candidate_source_navigation_flow()`，该函数按设计在目标入口前截断；适配结果覆盖完整计划后没有重新拼回目标点击和终态，最终 `TC-005` 只到 5 寸照片页。
- reachability 覆盖审计在所有证据文本中搜索“点击/选择”等子串。`显示百度网盘授权或文件选择相关界面` 中的名词“选择”被误当成目标动作，使缺少百度网盘点击的 `TC-005` 仍被判定覆盖。
- 自然语言动作分类没有把“校验/断言”视为被动检查，并把“可点击”中的“点击”当成执行命令；扫描可达 YAML 因而把 `校验百度网盘入口可见、可点击` 错渲染为 `aiTap`。
- Runner 首批排除项的 gateReason 明写“待首批完成后扩展”，但 Agent 只把“超过上限/非首批候选”两个前缀归入 deferred；两条正常可达性 executable 因此被错误计入 blocking，remaining 从应有 3 条缩成 1 条。
- repair 请求同时携带 4 张关键帧和 6 条基线，Task Server 与 Gateway 总预算同为 90 秒；首轮空答/超时既没有错误证据，也不会进入现有第二次有界纠正。

通用修复：

- 可信父路径适配现在从 AI 候选中独立提取真实目标动作及其后有界终态；适配后若目标动作不存在，则在 8 步上限内拼回。无法同时保留父路径和目标尾链时拒绝适配，保留原 AI 路径，不能静默截断业务目标。
- reachability 只接受有序执行流中指向验收目标的真实 navigation action；终态必须位于该动作之后或来自明确 expected/assertion。`aiTap/aiAction/aiAct` 文本也按同一目标解析；断言中的“可点击/文件选择”不能代替动作。执行流不再按文本去重，重复出现在两个独立业务分支的相同步骤保持各自顺序。
- `校验/断言` 加入被动检查前缀，并在动作判定前移除“可点击/不可点击/是否可点击”等能力形容词。生成 YAML 将这些步骤稳定渲染为 `aiWaitFor`，真实“点击目标”仍为 `aiTap`。
- 所有达到 executable 且 gateReason 明确“待首批完成执行准入后再扩展”的非首批用例统一进入 deferred；首批 Smoke 上限和 50% 阈值、脚本失败先修复门禁均保持不变。
- repair 首轮使用最新 3 张真实关键帧和 Top3 分支基线，默认总预算由 90 秒调整为 120 秒。HTTP/超时错误写入 `aiAttemptErrors`；首轮空答也进入现有唯一一次纠正，第二次只携带最后 2 帧、Top3 基线和当前失败任务，最长 75 秒，并继续使用创建 Agent 时选择的模型路由。没有无限重试，也没有让 AI 绕过 YAML、证据、dry-run 或设备门禁。

线上产物离线重放：

- 直接读取线上 cases payload，修复前严格审计为 `11/12`，唯一缺口为 `REQ-002-CHECK-04`；恢复上游 AI 已生成的 `点击百度网盘 -> 等待页面跳转` 后变为 `12/12 / missing=0 / ok=true`。
- 同一 payload 重新生成 6 条 YAML：全部 executable，static/dry-run `6/6`，无坐标；照片可达路径完整保留 5 寸和百度网盘目标尾链，扫描两条“校验…可点击”均为 `aiWaitFor`，真实百度网盘点击仍为 `aiTap`。
- 用线上 runner gate 的 3 个 blocked 项重放，新 deferred 判定为 `3/3`，不再把文档/照片可达性误计为预执行阻断。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/ai_skill_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录及空答/截断/图像/超时回退、Skill fixtures `3/3` 和 Playwright 桌面/移动视觉回归全部通过。
- 未修改 Figma parser、`router.py`、执行模式、Runner、Sonic、scorer、设备策略或历史 YAML。

待完成：提交、推送并部署本轮修复；部署后再次使用完全相同输入发起唯一 Agent，持续监督 4 个视觉批次、6 条最终 YAML、固定 OPPO 上最多 3 条串行 Smoke、真实失败帧驱动的一次有界 AI 修复、3 条 remaining 和最终报告到终态。成功也必须人工复核照片为 5 寸分支、三个入口文案/同级关系及真实跳转结果。

### 2026-07-18 真实回归：视觉 Frame 替换不得截断目标动作，名义 executable 的验收缺口仍由 AI 收敛

用户部署 `6b244b1` 后，以完全相同需求和 Figma 发起唯一 Agent `agent-1784336356080-c0199926`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / singleDeviceOnly / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康；服务 uptime 证明已完成本轮重启。Windows Runner 在线并上报 `yaml_dry_run=true / midscene_model_family=qwen3.6`，固定 OPPO 上 `com.xbxxhz.box 4.45.0 (357)` ready。Agent 在生成阶段终止，没有创建 Windows Runner job，也没有向华为设备下发任务。
- Figma parser 保持原实现，解析 `4 页 / 4 张 UI 图 / 忽略 0`。4 个单图批次全部真实送入 `qwen3.6-plus`，分别约 `31s / 22s / 25s / 18s` 完成；`sentToAiForJudgement=true / attempted=4 / done=4 / status=completed / hardGate=false`。
- AI PLAN 生成 8 个业务分支，前三条分别为文档打印、照片打印、扫描复印，另含三个可达性分支及人工异常/兼容性候选。路由仍为 `new_requirement_source / generate_draft`。
- Agent 终态 `FAILED / GENERATE_YAML / 30%`，没有进入 Runner。初始 executable 为 `TC-001..TC-005`，覆盖 `9/12`；最终 qwen 收敛增加扫描可达 `TC-006` 和扫描关系 `MC-001` 后达到 `11/12`，唯一缺口为 `REQ-002-CHECK-04` 照片打印百度网盘可达性。数量目标已达到，不是 5 条门槛或 scorer 问题。

深层根因：

- 上游 AI 原始 `TC-005` 明确是照片打印百度网盘可达性用例，包含 `照片打印 -> 6寸照片 -> 点击百度网盘 -> 等待百度相关页面`；视觉 AI 又正确把当前需求 Frame 映射到 `照片打印 / 5寸照片 / 百度网盘`。
- `_adapt_trusted_navigation_to_visual_evidence()` 在把历史 6 寸叶子替换为当前 5 寸叶子时，只保留成功基线父路径并追加 `点击5寸照片`，错误丢弃了旧叶子后的 `点击百度网盘` 和落地页稳定等待。用例标题、场景和断言仍写“可达”，因此被标成名义 `executable`，但 portfolio audit 正确判定它没有实际 target action。
- `6b244b1` 为保护绿色结果，将所有当前 level 为 executable 的候选都排除出最终收敛并在应用阶段冻结。该策略没有区分“已真实覆盖验收维度”和“只被标成 executable 但正是缺口责任用例”，导致 AI 只能补扫描，不能修回 `TC-005`。
- 门禁行为正确：最终仍缺 `1/12` 时阻断 YAML 转换和 Runner 下发，没有把假可执行结果发送到手机。

通用修复：

- 当前视觉叶子替换现在只替换历史页面状态。若原路径在叶子后包含需求目标点击，则完整保留该目标动作及其后的有界稳定终态；若历史尾部没有当前目标动作，只保留与当前目标直接相关的观察，不重新带入旧基线动态文件名或样例数据。
- 视觉适配最多保留 8 个紧凑步骤，与 executable planner / YAML 转换上限一致；替换前存在的目标动作若替换后消失，适配直接拒绝，不能静默截断。
- 最终 convergence 根据原用例 `title / scenario / goal / business_path / tags / originalFlow` 识别“声明负责缺失验收维度、但实际执行证据未覆盖”的名义 executable。每个缺失验收维度最多选择一个最短责任用例重新交给现有 AI；普通展示兄弟用例和其他已覆盖 executable 继续冻结。
- 应用阶段只允许平台标记的 `repairableExecutableCandidateIds` 被 AI 重写或降级；模型不能自行扩大该集合。原有 rewrite / demotion 保护继续覆盖真正通过审计的 executable，并新增 `repairable_executable_count` 供报告审计。
- 没有新增模型轮次或执行模式，没有降低覆盖、static、scorer、dry-run 或 Runner 门禁，也没有修改 Figma parser、`router.py`、Runner、Sonic、scorer、设备策略或历史 YAML。

线上失败产物离线重放：

- 直接读取线上保存的 `agent-agent-1784336356080-c0199926` cases payload。新 focus 只返回 `TC-005`，其余 6 条 executable 保持冻结；视觉证据选择 `5寸照片`，没有漂移到一寸照或历史 6 寸。
- 修复后的真实路径为 `首页稳定 -> 照片打印 icon -> 照片打印 -> 5寸照片 -> 等待导入区 -> 点击百度网盘 -> 等待落地页首个稳定页面且无白屏/崩溃`。
- 同一 payload 的 portfolio audit 从唯一缺 `REQ-002-CHECK-04` 恢复为 `12/12 / missing=0 / ok=true`；`preserved_executable_count=6 / repairable_executable_count=1`。
- 回归测试同时验证：历史动态文件名不会随视觉尾部复活；已通过展示用例不会因缺失 reachability 被误选；模型试图重写或降级非责任 executable 时仍被阻断。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态目录及文本/空答/截断/图像/超时降级、Skill fixtures `3/3` 和 Playwright 桌面/移动视觉回归全部通过。

待完成：提交、推送并部署本轮修复；部署后再次使用完全相同输入发起唯一 Agent，持续监督最终 YAML、固定 OPPO 上最多 3 条串行 Smoke、remaining、真实失败帧驱动的有界 AI 修复及最终报告到终态。任何阶段不得向第二台设备下发。

### 2026-07-18 真实回归：最终 AI 收敛只处理缺口，已通过用例与已接纳视觉状态保持不可变

用户部署 `924762d` 后，以完全相同需求和 Figma 发起完整 Agent `agent-1784333460207-2717c372`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / qwen3.6-plus`：

- 公网 `8091 / 8088`、AI Gateway、Sonic 健康；Windows Runner 在线并上报 `yaml_dry_run=true / midscene_model_family=qwen3.6`，固定 OPPO 上 `com.xbxxhz.box 4.45.0 (357)` ready。Runner 同时登记华为设备，但本 Agent 只保存 OPPO 的固定设备参数；本轮在生成阶段终止，没有创建 Runner job，也没有操作第二台设备。
- Figma parser 保持原实现，解析 `4 页 / 4 张 UI 图 / 忽略 0`；4 个视觉批次全部送入 `qwen3.6-plus`，约 `14s / 19s / 18s / 16s` 完成，视觉资料为软参考且没有触发硬门禁。
- Top3 基线重排从每个业务分支各自 `4` 个合格候选中选择一条真实执行成功基线：文档 `4478142771b41fcd`、照片 `300d829473029a32`、扫描 `d623c1e73180bfac`，三个必需分支均有专属导航证据。
- Agent 终态 `FAILED / GENERATE_YAML / 30%`。初始组合只有 `TC-001/002/003` 三条 executable，覆盖 `7/12`；缺少照片 reachability 和扫描四个验收维度，`TC-004/005` 仍为非终态，因此覆盖门禁正确阻断，未进入 Runner。

深层根因：

- 最终一次 qwen 收敛调用本身成功，模型把扫描 visibility / relation / copy / reachability 全部补齐，并选择照片 reachability 候选；但它同时重写了已通过的照片展示 `TC-002`，丢失 `REQ-002-CHECK-02` 同级关系。平台的单调门禁只能整批拒绝该组合，于是正确新增的扫描结果也被一并回滚。这是收敛状态合并错误，不是模型没有产出。
- 收敛聚焦先排除了 executable，随后有界证据构造又把它们重新加入请求；安全人工落地尾链也被嫁接回已通过来源用例，使模型仍有机会改写绿色结果。
- 同一 `REQ-002 / TC-002` 有两个 Figma 当前页状态：`5寸照片 confidence=0.90` 与 `一寸照 confidence=0.95`。旧排序只看置信度，忽略源用例 `originalFlow` 已明确写出 5 寸；应用阶段还会在 AI 已选择有界路径后重新选一次视觉状态并重新拼接历史基线，导致 5 寸证据再次漂移到一寸照。
- 原始步骤中的 `若存在尺寸选择，点击「5寸照片」或类似选项` 不是单一可执行目标；即使后续选中了正确 Frame，旧适配也不会把该条件句收敛成确定的可见文字动作。

通用修复：

- 最终 coverage convergence 现在只接收未解决候选。当前 executable 从模型请求中排除，并在应用阶段冻结其既有路径、断言和需求映射；模型即使返回改写或降级也只记录 `convergence_rewrite_blocked_count / convergence_demotion_blocked_count`，不会覆盖绿色结果。
- 有界证据不能把 executable 重新加入 focus。自动缺口候选拥有自己的分支证据；安全人工落地候选通过原有基线、首屏和验收门禁后提升候选自身，而不是改写已通过的来源页展示用例。跨需求候选只保留实际执行分支对应的 requirement refs。
- 视觉状态排序先匹配源用例 title / scenario / business path / `ai_case_plan.originalFlow` 中明确写出的具体实体，再比较 Frame 置信度。条件式或“或类似”导航在接纳当前 Frame 后收敛为一个精确可见文字动作，并移除该叶子与目标入口之间冲突的兄弟状态动作。
- AI 已选择且通过有界门禁的路径成为应用阶段事实源，不再被第二次历史基线重建或兄弟 Frame 重选覆盖。只有旧视觉叶子与本轮接纳叶子明确不同时，才刷新对应的旧视觉冲突 repair hint，避免错误实体进入未来基线和失败修复上下文。
- 没有修改 Figma parser、模型轮次、scorer、Runner、Sonic、`router.py`、执行模式、设备策略、历史 YAML 或覆盖门禁。

真实数据重放与验证：

- 使用线上完整 cases payload、4 条结构化 Figma 证据和三条线上成功基线重放，收敛请求从旧的 `TC-001/002/004/005/006 + MC-001` 缩为 `TC-004/005/006 + MC-001`；最终为 `6 executable / 12 of 12 / missing=0 / unresolved=0 / gate ok=true`。
- 照片展示与可达两条路径均为 `首页稳定 -> 照片打印 -> 5寸照片 -> 百度网盘展示/点击/首屏`；生成的全部 6 个 YAML 不含“一寸照”、6 寸执行动作、模糊“或类似”或坐标。6 条均通过 static、scorer 100，warning 为 0。
- 扫描路径仍只使用可见文字和已验证同分支基线；是否遇到当前真机权限弹窗必须由部署后的固定 OPPO Smoke 及真实末帧决定，不能用离线重放伪装真机成功。

已验证：

```bash
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录及文本/空答/截断/图像/超时回退、Skill fixtures `3/3` 和 Playwright 桌面/移动视觉回归全部通过。

待完成：提交、推送并部署本轮修复；部署后发起唯一一条完全相同的 Agent，持续监督 4 个视觉批次、最终 YAML、3 条首批 Smoke、基于真实失败帧的有界 AI 修复、remaining 和最终报告到终态。所有 dry-run、正式任务和修复任务必须继续固定 `win-runner-01 / ecbfd645` 串行执行，不得向华为设备下发。

### 2026-07-18 真实回归：失败 AI 必须读取 Midscene 真机帧，当前视觉叶子不能截断成功基线父路径

用户部署 `bf879a2` 后，以完全相同需求和 Figma 发起完整 Agent `agent-1784328618231-f2042acf`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / singleDeviceOnly`，创建时选择 `qwen3.6-plus`：

- 8091 / 8088、AI Gateway、Sonic 健康；Windows Runner 在线并上报 `yaml_dry_run=true / midscene_model_family=qwen3.6`，固定 OPPO 上 `com.xbxxhz.box 4.45.0 (357)` ready。所有 Agent dry-run 和正式任务只下发固定 OPPO，没有选择或执行第二台设备。
- Figma parser 保持原实现并解析 `4 页 / 4 张 UI 图 / 忽略 0`；4 个视觉批次全部送入 `qwen3.6-plus` 并完成。结构化视觉证据明确给出 `REQ-002 / 照片打印 / 5寸照片 / 百度网盘 / sameBranch=true / confidence=1.0`。
- AI 生成 `12` 个场景、`6` 条 executable YAML 和 `8` 条人工项，显式需求覆盖完整；6 条 YAML 均通过 static、scorer 100 和 Runner dry-run，无坐标动作。首批仅选文档、照片、扫描 3 条展示 Smoke，并固定 OPPO 串行执行。
- 文档 `job_1784329079617_00004` 首次成功；照片 `job_1784329188641_00005` 在 300 秒定位 `5寸照片` 超时；扫描 `job_1784329507846_00006` 在 128 秒定位 `立即使用` 失败。Smoke 为 `1 passed / 2 script failed`，低于 50% 后正确暂停 remaining；最终 `FAILED / COLLECT_REPORT / 95%`，报告继续保留真实通过数，没有覆盖成全部失败。
- 照片真实末帧停在第一层“照片打印”页，页面中仍有第二个同名“照片打印”卡片；正确父路径是 `照片打印 icon -> 照片打印 -> 当前规格`。生成计划虽然引用了执行成功基线 `c29b5ecd70bbfe27`，却只保留模型的单次“照片打印”点击，再接 5 寸，因此跨层定位失败。
- 扫描真实帧已到达“小白扫描王”首页，导入区域依次显示本地导入、相册导入、微信导入，百度网盘图标在右侧边缘可见；脚本继续复制深层历史动作 `证件扫描 -> 立即使用`，真机实际出现“取消 / 确定”相机权限说明弹窗。

深层根因：

- `report_image_context()` 旧实现扫描整份自包含 HTML 中的所有 `data:image`，并取最后若干张。Midscene 报告前端 bundle 内置了 Swag Labs 演示图片，这些图片被当作失败录屏关键帧送给 AI；失败分析因此虚构“执行到了 Swag Labs 登录页/环境混乱”，扫描任务还被错误描述成照片规格问题，最终未生成可执行修复。
- Midscene 的真实执行帧有明确结构：`midscene_web_dump` 中按执行顺序保存 `midscene_screenshot_ref`，再通过 `script[type=midscene-image][data-id]` 解析图片。平台此前没有使用这条结构化引用链。
- executable planner 能看到成功基线完整 snippet，但响应落地后只保留 baseline ID；应用阶段直接信任模型缩短后的 flow，再对该 flow 替换视觉叶子，无法恢复被模型省略的同名父页面动作。

通用修复：

- 报告关键帧改为解析 Midscene typed image store 和 execution dump，按真实 screenshot ref 顺序去重并只取最新执行帧；存在 Midscene image store 时禁止回退到 bundle 任意 data URL，旧报告没有 typed store 时才使用兼容提取。图片用完整内容 SHA-256 去重，避免相同 JPEG 头导致不同真机帧被误合并。
- executable planner 返回本轮已提供给 AI 的 compact selected baselines。只有 baseline 确认为 `verified_execution / execution_success`，且当前视觉证据与 case、REQ、分支、目标文案一致时，应用阶段才复用成功基线的可见文字父路径；AI 当前 flow 负责新叶子，已有当前 Frame 规则继续负责历史叶子替换。
- 同名入口在不同页面出现时不按文案机械去重：保留成功基线中两次动作之间的稳定等待，再接当前视觉叶子。没有加入“照片打印”“5寸”“百度网盘”等业务硬编码，也没有新增模型轮次或放宽 static/scorer/dry-run/Smoke 门禁。

真实数据重放：

- 照片报告从 `11` 个被 execution dump 引用的真机帧中返回最后 `4` 帧，末帧 screenshot id 为 `2e59aa7a-adc0-4a69-bb8f-eefbc87297a7`，真实显示第一层照片打印页；扫描报告从 `17` 个真机帧中返回最后 `4` 帧，末帧真实显示“取消 / 确定”权限提示。两者均不再包含 Swag Labs。
- 使用线上 `TC-002`、Figma 结构化证据和成功基线 `c29b5ecd70bbfe27` 重放，最终 YAML 动作为 `首页稳定等待 -> 照片打印 icon -> 等待照片打印主页 -> 照片打印 -> 等待尺寸入口 -> 5寸照片 -> 百度网盘等待/断言`，完整保留父路径且不残留 6 寸。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/ai_skill_service.py task_server/services/report_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py tests/ai_gateway_static_checks.py
python3 tests/backend_static_checks.py
python3 tests/ai_gateway_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录及文本/空答/截断/图像/超时回退、Skill fixtures `3/3` 和 Playwright 桌面/移动视觉回归全部通过。
- 未修改 Figma parser、`router.py`、执行模式、Runner、Sonic、scorer、设备策略或任何历史 YAML。

待完成：提交、推送并部署本轮修复；部署后用完全相同输入发起唯一一条完整 Agent，继续固定 OPPO `ecbfd645`。必须监督 4 个视觉批次、6 条左右最终 YAML、3 条首批 Smoke、AI 使用真实失败帧修复、remaining 以及最终报告到终态；离线重放不能替代真机成功。

### 2026-07-18 真实回归：保留视觉目标实体并前置新生成 YAML 启动稳定态

用户部署 `4dee24e` 后，以完全相同需求和 Figma 发起完整 Agent `agent-1784321921903-4ebdca4b`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed`，创建时选择 `highway_gpt4_1_mini / gpt-4.1-mini`：

- 8091 / 8088、AI Gateway、Sonic 健康；Windows Runner 在线并上报 `yaml_dry_run=true / midscene_model_family=qwen3.6`，固定 OPPO 上 `com.xbxxhz.box 4.45.0 (357)` ready。所有正式和 dry-run job 都只使用 `win-runner-01 / ecbfd645`，没有选择、并发或下发第二台设备。
- Figma parser 保持原实现，解析 `4 页 / 4 张 UI 图 / 忽略 0`；4 批视觉资料约 `7s / 6s / 7s / 3s` 全部送入 GPT-4.1 Mini 并完成，`fallbackUsed=false / hardGate=false`。视觉 AI 明确输出照片分支 `5寸照片 -> 百度网盘` 和一寸照页面证据。
- AI 生成 8 个场景、6 条 executable YAML 和 2 条人工项，最终需求覆盖 `12/12`，6 条 YAML 均通过静态校验、scorer 100 和 Runner dry-run；没有坐标动作。
- 原始文档/照片 smoke 均因 launch 后立即 `aiTap`、App 仍在启动页而失败。AI 使用 Runner 报告关键帧和同分支成功基线生成两条修复，文档修复在 OPPO 真机通过；报告末帧真实显示文档打印页中的本地文档、百度网盘、QQ 文档、WPS 文档同级入口。
- 照片第一条修复把 5 寸点击留在百度网盘断言之后，停在尺寸弹窗失败；第二次有界 AI 修复错误地把参考 `6寸照片打印.yaml` 复制成实际点击 6 寸。Runner 在 6 寸页面真实通过百度网盘断言，但报告标题和截图明确为“6寸照片”，因此人工业务复核判定该绿色结果不能代表 Figma 的 5 寸目标。
- 修复后恢复扩展只执行 remaining 扫描用例。它在扫描主页进入“证件扫描”后遇到相机权限说明弹窗，真机显示“取消 / 确定”，原 YAML 却继续点击历史基线中的“立即使用”，最终失败。失败前关键帧已经显示扫描主页导入入口横向区域，百度网盘图标在右侧边缘部分可见。
- Agent 终态 `FAILED / RERUN / 95%`。双状态汇总正确保留真实结果：6 次正式尝试中 2 次通过、4 次脚本失败；按 3 个逻辑业务任务统计为 2 个 recovered、1 个 unresolved，产品失败为 0。没有把 Agent 编排失败覆盖成“全部任务失败”。

根因：

- 新生成 YAML 只有当 AI 步骤显式写“启动并等待首页”时才生成启动稳定等待；普通“点击首页 / 点击业务入口”会在 `launch` 后立即定位。静态校验能判断动作合法，却不能证明真机资源加载已结束。
- `_adapt_trusted_navigation_to_visual_evidence()` 发现规划中已经存在视觉 navigationLeaf 时直接返回，未检查该叶子是否排在 targetText 等待/断言之后；本次 5 寸点击因此没有被移到百度网盘断言之前。
- 失败修复请求虽带 Figma 文本，但没有携带视觉 AI 已结构化确认的 `caseId / navigationLeaf / targetText`。模型把 6 寸成功基线的样例实体误当成当前目标，现有修复门禁只能校验引用和路径变化，无法识别“路径结构可复用、具体变体不可替换”。
- 有界重跑上限本身合理；但照片错误顺序消耗了唯一一次重跑后 AI 恢复，扫描最新关键帧便没有下一次自动修复机会。应消除更早的生成/修复偏差，而不是继续增加无限重试。

通用修复：

- 所有新生成 Agent YAML 在 `launch` 后、首次 AI 导航前增加一个可见首页稳定态 `aiWaitFor`；若 AI 已显式提供启动/首页等待则只保留该具体等待，不重复调用模型。历史 YAML 不迁移。
- 视觉 AI 已确认且规划中已采用的 navigationLeaf 若位于 targetText 校验之后，平台将同一可见文字动作移动到首次目标等待/断言之前；没有新增或写死 5 寸规则。
- 修复请求新增有界 `visualCurrentPageEvidence`。候选门禁只保护“原 YAML 已采用、同分支、置信度不低于 0.75”的视觉叶子：先叶子后目标断言；不得用相邻基线的尺寸、颜色、模式、产品或套餐样例替换。未采用的 Figma 状态仍是软参考，不会强制扩展用例。
- AI 修复提示明确“基线只提供父页面路径结构”，并要求失败前关键帧已出现目标同级区域或边缘入口时回到最早真实状态，不继续复制更深的尺寸、权限、授权或确认动作。现有一次重跑后 AI 恢复上限保持不变。
- 使用本次线上 cases 和 3 条真实照片 YAML 离线重放：照片顺序变为 `启动稳定等待 -> 照片打印 -> 5寸照片 -> 百度网盘等待/断言`；第一条修复被识别为 `source_backed_leaf_after_target_check`，6 寸替换被识别为 `source_backed_navigation_target_removed`，正确 5 寸候选通过。没有修改 Figma parser、scorer、Runner、Sonic、`router.py`、执行模式、历史 YAML 或设备策略。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/ai_skill_service.py tests/backend_static_checks.py tests/ai_gateway_static_checks.py
python3 tests/backend_static_checks.py
python3 tests/ai_gateway_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录及文本/空答/截断/图像/超时回退、Skill fixtures `3/3` 和视觉回归全部通过。

待完成：推送并部署本轮修复；部署后仍需用完全相同输入发起唯一一条完整 Agent，固定 OPPO `ecbfd645`。重点验证文档/5 寸照片 smoke 首轮通过，以及扫描失败关键帧能在现有有界恢复轮次内驱动 AI 从最早真实导入区域修复并完成 remaining。

### 2026-07-18 真实回归：单一具体落地状态也应进入 AI 有界收敛

用户部署 `1d4362c` 后，以相同需求和 Figma 发起完整 Agent `agent-1784301845490-a6bf385b`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed`，创建时选择 `highway_gpt4_1_mini / gpt-4.1-mini`：

- 8091 / 8088、AI Gateway、Sonic 健康，Task Server 重启约 2 分钟；Windows Runner 在线并上报 `yaml_dry_run=true / midscene_model_family=qwen3.6`，固定 OPPO 预检 ready。任务开始前没有活动 Runner job，未选择或下发同 Runner 上的华为设备。
- GPT Provider 实时目录可用且探针成功。需求分析、场景设计、基线重排、规划、收敛和 4 个视觉批次全程使用 `gpt-4.1-mini`，`fallbackUsed=false`。
- Figma parser 保持原实现，解析 `4 页 / 4 张 UI 图 / 忽略 0`；4 张图按 4 批全部送入 AI，约 `5s / 8s / 8s / 3s` 完成，`sent=true / attempted=4 / done=4 / status=completed / hardGate=false`。视觉 AI 识别了照片打印 5 寸照片页中的百度网盘入口、文案和位置关系。
- PLAN 由 MM skills 生成 8 个 AI 业务分支。初始 planner 形成 6 条文档/照片 executable；现有一次收敛使用执行成功的扫描基线 `d623c1e73180bfac`，将扫描展示 `TC-007` 提升为 remaining executable，补齐扫描 visibility / relation / copy。
- Agent 终态仍为 `FAILED / GENERATE_YAML / 30%`，最终为 `7 executable / 11 of 12`，只缺 `REQ-003 reachability`。覆盖门禁正确阻断，未创建 Runner job，因此本轮没有冒烟、remaining、报告或截图，也没有操作第二台设备。

根因：

- 本轮 GPT 将扫描跳转自动候选 `TC-008` 降为人工并清空步骤，但同时生成了可用人工证据 `MC-002`：点击百度网盘入口，观察跳转到文件列表页，确认页面包含文件名和操作按钮，再确认无崩溃、无白屏；没有账号、授权确认、文件选择或坐标动作。
- `1d4362c` 已能识别声明式“确认页面/列表”，但现有 bounded landing scorer 还要求至少两个可观察首屏类别。上一次 AI 给出“跳转或授权窗口 + 文件列表”，可以满足；本次 AI 只给出一个更明确的文件列表状态，所以同样安全的证据仍被丢弃。
- 这是模型表达变化与平台证据规范化之间的契约缺口，不应删除 reachability 门禁、放宽 scorer，或要求 AI 猜测授权/登录等未提供状态。

通用修复：

- 当 AI 候选已经包含真实文字目标点击、一个具体首屏状态以及明确的无崩溃/白屏稳定性时，保留 AI 的具体状态，并补充同一点击目标绑定的可见落地页区域作为另一合法观察结果，再交给原 bounded landing scorer 复核。
- 只有“页面跳转情况 / 页面有响应”等模糊描述时，仍因缺少具体首屏类别被拒绝；确认授权/登录、输入凭据、文件选择、坐标、多目标导航和深层外部动作门禁均保持不变。没有修改 scorer、Figma parser、Runner、设备策略、执行模式、`router.py`、Sonic 或历史 YAML。
- 使用本次线上完整 cases payload、真实 `MC-002` 文案和扫描成功基线重放：收敛证据为 `kind=bounded_landing / sourceCaseId=TC-007 / tailSourceCaseId=MC-002`，覆盖从 `8/12` 变为 `12/12`，最终门禁 `ok=true`；合并 case 保持 `remaining`，不挤占 smoke。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态目录及文本/空答/截断/图像/超时降级、Skill fixtures `3/3`、桌面和移动端视觉回归全部通过。

待完成：提交、推送并部署本轮修复；部署后仍需用完全相同输入发起唯一一条完整 Agent，固定 OPPO `ecbfd645`，持续监督 YAML、smoke、AI 修复、remaining、真实报告和关键帧到最终终态。离线 `12/12` 只证明生成收敛，不等于真机成功。

### 2026-07-17 真实回归：AI 有界落地页中的“确认状态”不能被误判为动作

用户部署 `6afea34` 后，以相同需求和 Figma 发起完整 Agent `agent-1784299036082-dd00ea9d`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed`，创建时选择 `highway_gpt4_1_mini / gpt-4.1-mini`：

- 线上 `8091 / 8088`、AI Gateway、Sonic 健康；线上 `js/agent-workbench.js` 与本地目标提交文件 SHA-256 完全一致。GPT-4.1 Mini 与千问实时探针均成功；Windows Runner 在线，上报 `yaml_dry_run=true / midscene_model_family=qwen3.6`，固定 OPPO 上 `com.xbxxhz.box 4.45.0 (357)` ready。
- Figma parser 保持原实现，解析 `4 页 / 4 张 UI 图 / 忽略 0`。4 张图按 4 批全部送入创建时选择的 GPT，约 `10s / 9s / 11s / 10s` 完成，均为 `fallback=false / finishReason=stop / hardGate=false`。第一批识别扫描复印父页面且未把未见目标入口升级成硬门禁；其余批次结构化识别照片打印 `5寸照片 / 一寸照 / 百度网盘`。
- PLAN 由平台 MM skills 生成 8 个 AI 业务分支，不是启动前预置主链。需求契约仍独立保留文档打印、照片打印、扫描复印各自的 visibility / relation / copy / reachability 共 12 个验收维度。
- Agent 终态 `FAILED / GENERATE_YAML / 30%`，未创建任何 Runner job、未操作 OPPO，也未向同 Runner 上的华为设备下发。失败不是视觉超时、模型额度、scorer、Runner 或设备问题。
- 首轮 executable planner 形成文档和照片 4 条 executable。现有一次最终 AI 收敛又使用执行成功的扫描基线 `d623c1e73180bfac`，把扫描展示人工项 `MC-001` 提升为 remaining executable，补齐 visibility / relation / copy；最终只缺 `REQ-003 reachability`，覆盖门禁正确阻断。

根因：

- 上游 AI 已提供扫描可达短链路 `MC-002`：点击百度网盘后观察跳转/授权窗口，确认无崩溃和长时间白屏，再确认文件列表页加载完成。该链路只观察首个稳定状态，不输入账号、不确认授权、不选择文件。
- `_bounded_landing_tail()` 只接受 `确认是否 / 确认无 / 确认未 / 确认已 / 确认页面`，把同样是只读观察的“确认文件列表页加载完成”当成未知动作，直接丢弃整条 AI 候选。收敛请求因此只有扫描展示证据，没有 reachability 证据；GPT 没有安全候选可选。
- 这是平台对 AI 产物的语义解析缺口，不应通过降低 12 维覆盖门禁、硬凑用例数量或针对百度网盘写特例处理。

通用修复：

- 有界外部落地页现在识别“确认 + 可见 / 显示 / 出现 / 加载 / 完成 / 页面 / 列表 / 弹窗 / 跳转 / 状态”等声明式观察，并统一规范为只读检查。
- `确认打印 / 支付 / 上传 / 提交 / 删除 / 下载 / 保存 / 发送 / 下单 / 选择 / 授权 / 登录` 继续判定为真实动作，不能伪装成观察；既有深层账号、授权确认、文件操作、坐标和多目标门禁保持不变。
- 使用本次线上完整 cases payload、真实扫描成功基线和原 AI 人工候选重放：同一 `MC-001` 合并来源页展示断言与 `MC-002` 有界点击尾链，`acceptanceCheckIds` 从 3 个变为完整 4 个；最终组合 `5 executable / 12 of 12 / missing=0 / gate ok`。它仍属于 remaining，不挤占三条 smoke。
- 回归测试同时覆盖声明式“确认内容列表页加载完成”可进入 AI 有界证据，以及“确认打印”必须继续被拒绝。没有修改 Figma parser、`router.py`、执行模式、Runner、Sonic、scorer、历史 YAML或设备策略。

已验证：

```bash
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- 后端 61 项、前端 69 项、Gateway 46 项、实时模型目录与文本/空答/截断/图像/超时降级、Skill fixtures `3/3` 和 Playwright 桌面/移动端视觉回归通过。

待完成：提交、推送并部署本轮修复后，再执行一次完全相同的完整 Agent。继续固定 `win-runner-01 / ecbfd645`，监督 Figma、最终 YAML、首批 smoke、AI 修复、remaining、报告和关键帧到 Agent 终态；不得选择或并发执行第二台设备，离线重放不等于真机成功。

### 2026-07-17 真实回归：当前设计叶子、动态样例隔离与逐任务恢复

部署 `de69242` 后，以相同需求和 Figma 发起完整 Agent `agent-1784279799286-3163a6e1`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed`，创建时选择 `highway_gpt4_1_mini / gpt-4.1-mini`：

- `8091 / 8088`、AI Gateway、Sonic 健康；Windows Runner 在线，上报 `yaml_dry_run=true / midscene_model_family=qwen3.6`。Agent 规划、视觉判断和 YAML 生成持续使用创建时选择的 GPT；Runner 内 Midscene 视觉执行仍使用其已配置的 qwen3.6 模型族。
- Figma parser 保持原实现，解析 `4 页 / 4 张 UI 图 / 忽略 0`。4 张图按 4 批全部送入 GPT，约 `9s / 13s / 12s / 4s` 完成，均未降级。视觉证据明确包含 `REQ-002 / 照片打印 / 5寸照片 / 百度网盘 / confidence=0.9`。
- AI 形成 `7` 条 executable、`2` 条 manual；7 个 YAML 均通过 static、scorer 和 Runner dry-run，评分 100、无 warning、无坐标。最终失败发生在真实 Smoke / RERUN，不是生成覆盖门禁、视觉超时或第二台设备。
- 原始 Smoke 固定 OPPO 串行：文档 `job_1784280153426_00004` 成功，报告确认文档打印页显示本地文档、百度网盘、QQ、WPS；扫描 `job_1784280425103_00005` 失败，最后关键帧显示本地导入、相册导入、微信导入，右侧第四个同级图标被屏幕边缘裁切；照片 `job_1784280569718_00006` 因模型 `Request aborted` 失败。
- 旧安全重跑仍机械执行原脚本：扫描 `job_1784280911400_00007` 再次在相同裁切位置失败；照片 `job_1784281030191_00008` 在同一 OPPO 恢复成功。Agent 终态 `FAILED / RERUN`；共 5 次真实尝试，原始/恢复后逻辑结果为文档通过、照片通过、扫描失败，原始通过 2、失败 3、恢复 1。首批逻辑门禁未恢复，因此 remaining 未下发。整个 Agent 未选择或执行同 Runner 上的第二台设备。

根因：

- 最终规划仍从照片成功基线复制 `6寸照片`。当前视觉证据的 `requirementId` 带描述文本，而旧匹配用完整字符串比较规范化 `REQ-002`；同时当前 Frame 叶子适配只用于有界收敛候选，没有覆盖所有已接受的 baseline-grounded planner flow。
- 文档、照片、扫描三条可达性 YAML 都从历史成功样例复制了 `百度文档测试.doc` 和“去打印”终态。该文件名不在当前需求或当前 Figma 中，历史基线只能证明路径，不能成为新需求硬断言。
- 扫描报告已给出同级横向入口被右边缘裁切的恢复证据，但旧分类器只在原 YAML 已含 `aiScroll` 时识别滑动脚本问题；因此 AI 没有得到补充屏外探索的机会。
- 两个 Smoke 失败分别是扫描脚本问题和照片环境问题。旧聚合优先得到 `ENV_ISSUE`，跳过 `GENERATE_REPAIR`，随后 `_tool_rerun` 又把所有失败原 YAML 一起重跑。失败 HTML 也没有进入 `executionReports`，部分恢复后报告不刷新，导致最终产物漏掉真实失败报告和已恢复尝试。

通用修复：

- 当前视觉证据按规范化 `REQ-*`、同业务分支、目标文字和置信度匹配；同需求兄弟 case 可以提供当前 Frame 叶子。所有 baseline-grounded executable planner flow 都执行当前叶子适配，不再只处理覆盖收敛。明确叶子优先于仅由页面标题推导的叶子；同等级视觉变体保持 Figma 页面/视觉批次稳定顺序，不修改 Figma parser。
- automation filter、executable planner 和平台写回共同禁止把历史文件名、账号、手机号、订单号、记录标题、时间戳复制为当前硬条件。平台检测只在历史出现的动态值：若它只位于最后的等待/观察步骤，改用 AI planner 为当前 case 给出的稳定终态；若出现在动作或 planner 断言中且无法安全落地，则降级复核，不能下发 Runner。
- 失败关键帧/报告若明确显示同级入口行在屏幕边缘被裁切，即使原 YAML 没有滑动，也先归为一次可修 `SCRIPT_ISSUE`。修复 AI 可在失败等待前补最多两次官方 `aiScroll`，区域使用当前页真实可见文字、`direction=right / distance<=400`，滑动后重新等待目标；禁止坐标、ADB swipe 和整页盲滑。
- 失败分析保留 `failureTypeCounts / mixedFailureTypes`，后续动作按每个 job 分流：可修脚本只下发通过语义/证据/YAML 门禁的 AI 临时修复稿；有明确模型中止、设备断开、网关等临时环境证据的任务只原样重试一次；产品失败只生成缺陷证据；未知或证据不足不盲重跑。固定设备仍严格串行，没有新增重试轮次或执行模式。
- 重跑产物显式区分 `mixed / repair_draft / original_yaml / diagnosis_only`；界面按每个任务显示“AI 修复、原脚本重试、诊断处理”，不会再把混合恢复整批显示成 AI 修复。
- 成功和失败的终态 HTML 都进入 `executionReports / yamlExecutionRefs` 并保留各自状态；每轮真实重跑后立即刷新报告，只有全部失败源由关联后代通过且 remaining 完成时才标记逻辑恢复。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/ai_skill_service.py task_server/services/repair_service.py
python3 tests/backend_static_checks.py
python3 tests/ai_gateway_static_checks.py
npm test
git diff --check
```

- 后端 61 项、前端 69 项、Gateway 46 项、实时模型目录/降级集成、Skill fixtures `3/3` 和 Playwright 桌面/移动端视觉回归通过。
- 直接重放线上 `agent-1784279799286-3163a6e1` 产物：TC-004 从 `6寸照片` 适配为 Figma 第一个明确叶子 `5寸照片`，证据来源和置信度保留；三条历史 `百度文档测试.doc` 终态改为授权页、登录页、内容列表或空态页任一稳定状态。
- 回归模拟同一批包含 `SCRIPT_ISSUE + ENV_ISSUE + PRODUCT_BUG`：只创建 2 个任务，分别为 AI 修复稿和环境原脚本，均绑定 `win-runner-01 / ecbfd645`；产品任务不下发。报告聚合同时保留 passed / failed HTML。
- 未修改 `router.py`、Figma parser、执行模式、Runner、历史 YAML、`sonic_service.py` 或 `yaml_executable_scorer.py`。

待完成：提交、推送并部署本轮修复后，再发起一次完全相同的完整 Agent。必须持续监督 Figma 4 批、最终 YAML、首批 Smoke、可能的 AI 修复、remaining、真实报告和关键帧到终态；只允许固定 OPPO `ecbfd645`，不得并发或选择第二台设备。

### 2026-07-17 部署后真实回归：自动候选降级时保持身份与需求证据

用户确认部署 `1464e77` 后，以完全相同需求和 Figma 发起 Agent `agent-1784275188111-cc3f2a2d`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / singleDeviceOnly`，创建时选择 `highway_gpt4_1_mini / gpt-4.1-mini`：

- `8091 / 8088`、AI Gateway、Sonic 健康；Gateway 实时目录包含 `gpt-4.1-mini`，模型探针成功。Windows Runner 在线并上报 `yaml_dry_run=true / midscene_model_family=qwen3.6`，固定 OPPO 上的小白学习打印为 `4.45.0 (357)`。
- Figma parser 保持原实现并解析 `4 页 / 4 张 UI 图 / 忽略 0`。4 张图按 4 批全部送入创建时选定的 GPT，4 / 4 批完成，耗时约 `5s / 15s / 10s / 9s`，均为 `fallbackIndex=0 / finishReason=stop / hardGate=false`。第 2 批结构化证据正确识别 `REQ-002 / 照片打印 / 5寸照片 / 百度网盘 / sameBranch=true / confidence=0.9`。
- requirement、scenario、automation filter、smoke、Top3 基线重排、范围规划和最终 YAML 规划均使用选定 GPT，没有切换千问。基线重排分别选中文档、照片和扫描三个执行成功分支；每个必需分支候选数均为 4，扫描成功基线并未缺失。
- Agent 终态 `FAILED / GENERATE_YAML / 30%`。没有创建 Runner job，没有操作 OPPO，也没有向同 Runner 上的华为设备下发；同期华为上的 Sonic 基线任务属于外部任务，不是本 Agent 创建。
- 最终 4 条 executable 覆盖文档和照片的 `8 / 12` 个验收维度；扫描复印 `REQ-003` 的 visibility / relation / copy / reachability 均缺失。数量 `4 < 5` 仍只是 advisory，失败由显式覆盖门禁触发，不是机械凑数、视觉门禁、模型额度、Runner 或设备问题。

根因：

- PLAN 的 `coverage_matrix` 和 smoke 记录仍引用扫描候选 `TC-005 / TC-006`，但保存后的人工区只剩两个无 ID、无需求映射、无业务路径、无断言的 `MC-004 / MC-005`。
- `split_automation_ready_cases()` 对缺步骤的自动候选重新创建了只含标题、原因和准备建议的对象，丢失原 `case_id / requirementRefs / coverage / business_path / expected / assertions / repair_hints`，也把来源误记成原生 manual。
- 初始 GPT planner 明确规划了 6 条 executable，却因响应中的 `TC-005 / TC-006` 已无法映射当前 `MC-*` 候选而拒绝 2 条分类；最终收敛请求因此只包含 `TC-001..TC-004`。模型没有扫描候选可选，即使全局已经有可信扫描基线也无法恢复。
- 现有最终覆盖门禁正确阻断了不完整组合，不能降低或绕过。

通用修复：

- 自动候选进入 Runner 资格拆分前统一规范 `case_id / caseId / id`；缺 ID 时按原自动池顺序生成不冲突的稳定 `TC-*`。因此第 5、6 个候选不会在人工池被重新编号或与已有 ID 冲突。
- 被确定性风险或缺步骤阻断时，深拷贝完整候选并标记 `executionLevel=manual / originExecutionLevel=automatic`，保留需求、路径、断言、视觉补充和修复提示。它仍不能直接生成 YAML，但现有初次 planner 和同一次覆盖收敛可以把它作为原自动候选交给 AI 判断。
- AI 只有在返回可映射的原候选 ID、显式需求引用、可信基线路径、明确前置和可见终态后才可恢复 executable；static、scorer、dry-run、Smoke、Runner 和最终覆盖门禁均保持不变。没有新增模型轮次、业务词特判或数量硬门槛。
- 没有修改 Figma parser、视觉分批、`router.py`、执行模式、Runner、Sonic、scorer、历史 YAML 或设备策略。

行为验证：

- 回归构造 4 个既有自动候选和第 5 个无 ID、无步骤但保留 `REQ-003` 证据的候选；拆分后稳定得到 `TC-005`，完整需求映射、业务路径、断言和 repair hint 均保留，原输入对象未被修改。
- 模拟线上 GPT 再次返回 `TC-005` 和扫描成功基线路径，planner 请求真实包含该 ID，`rejected_case_count=0`；候选仅在 `baselineVerified=true` 后恢复 executable。覆盖收敛也能按缺失需求重新聚焦该候选。
- `python3 -m py_compile ...`、`python3 tests/backend_static_checks.py`、完整 `npm test` 和 `git diff --check` 通过。完整检查包含 undefined-name、后端 `61`、前端 `69`、Gateway `46`、实时模型目录与降级集成、Skill fixtures `3/3` 及 Playwright 桌面/移动端视觉回归。

待完成：提交、推送并部署本轮修复后，只发起一次相同完整 Agent，继续使用创建时选定且探针可用的 `gpt-4.1-mini`，仅在超时、限流或不可用时走已有模型降级；固定 `win-runner-01 / ecbfd645`，监督生成、Smoke、remaining、真实报告、截图/录屏和最终终态。不得选择第二台设备，也不得用本地重放替代真机成功。

### 2026-07-17 显式需求溯源、当前 Frame 导航叶子与逐任务修复资格

部署 `1057e04` 后，以相同需求和 Figma 发起完整 Agent `agent-1784267243585-95268e66`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed`，创建时选择 `highway_gpt4_1_mini / gpt-4.1-mini`：

- `8091 / 8088` 健康；Gateway 实时模型测试确认 `gpt-4.1-mini` 可用。Windows Runner 在线并上报 `yaml_dry_run=true / midscene_model_family=qwen3.6`。所有 dry-run、原始 Smoke 和修复重跑均串行下发到固定 OPPO，没有选择或执行同 Runner 上的第二台设备。
- Figma parser 保持原实现，解析 `4 页 / 4 张 UI 图 / 忽略 0`。4 批图片全部送入创建时选定的 `gpt-4.1-mini`，均由首选模型在约 `3-12s` 完成，`fallbackIndex=0 / finishReason=stop / hardGate=false`。第 2 批结构化证据为 `REQ-002 / 照片打印 / pageTitle=5寸照片 / navigationLeaf=照片打印页 / targetText=百度网盘 / confidence=0.9`；设计稿真实参与 AI 判断且仍是软参考。
- AI 生成 `8` 条业务流、`7` 条 executable YAML，static、dry-run 和 scorer 均通过且无坐标。首批固定设备串行执行 3 条：照片展示、AI 推测的“基础打印首页百度网盘入口”、扫描展示；文档展示被延后。Agent 最终为 `FAILED / RERUN`，不是生成、视觉、模型额度、设备选择或并发问题。
- 照片展示 YAML 从 `App 首页` 直接等待照片页，没有点击照片打印，也没有进入当前设计的 `5寸照片`。原始 job `job_1784267561033_00004` 的 Midscene 报告关键帧确认手机仍停在首页，正确归类为 `SCRIPT_ISSUE / can_auto_repair=true`。
- `TC-007` 没有任何 `REQ-*` 映射，只引用 `business_goals / ai_suggested_requirement_points`，却因旧 scope review 按全局目标关键词计数而获得 Runner 资格。真机首页只有文档打印、照片打印、扫描复印，job `job_1784267637893_00005` 因不存在首页百度网盘入口被归为 `PRODUCT_BUG / can_auto_repair=false`；这是无需求依据的测试假阳性，不是产品缺陷。
- 批量修复错误地把 overall `SCRIPT_ISSUE` 覆盖到每个失败任务，导致上述产品失败又生成“点击基础打印”的虚构修复并重跑。照片修复虽补了点击照片打印，但在 `launch` 后立即 `aiTap`，没有首页稳定等待；修复 job `job_1784268120943_00010` 的报告显示 App 仍在“资源加载中 0%”时开始定位，因而再次失败。扫描失败复检证据不一致且 `can_auto_repair=false`，旧逻辑虽未下发修复 YAML，但仍做了无效 AI 修复尝试。

通用修复：

- 当需求分析已建立显式 `REQ-*` 契约时，自动用例必须映射至少一个真实存在的需求 ID；全局业务目标、AI 建议文本或伪造 ID 不能替代。scope gate 提前到最终组合审计和 Smoke 选择之前，并在收敛结果后再次执行；无映射候选保留到人工区供审阅，不占 Runner 名额，也不能满足覆盖门禁。没有显式 REQ 契约的旧需求继续使用原语义匹配，不强制改造历史输入。
- AI 规划从 App 首页开始时，必须包含进入需求业务分支的真实可见文字导航；只有等待/断言的子页面假定路径降为复核。明确以首页为验收页的合法需求不要求虚构导航。多分支候选仍使用原有独立分支证据门禁。
- 视觉证据若把上一级模块误填为 `navigationLeaf`、但 `pageTitle` 给出了同分支更具体的尺寸/版本/类型/状态标题，平台在 `sameBranch=true / confidence>=0.75 / REQ 与目标文案一致 / 无坐标和备选目标` 时把上一级移入 `parentPath`，将具体标题作为当前叶子。该规则不识别或硬编码 5 寸、6 寸等业务词，Figma parser 未修改。
- 同一次已有覆盖收敛现在同时处理“验收缺口”和“未决自动用例”。即使兄弟用例已覆盖同一 REQ，未决路径仍可获得成功基线父路径和当前 Frame 叶子证据；不新增模型轮次。首页起点统一保留一个可见稳定等待，历史叶子只在共同父路径被证明后替换。
- 修复资格绑定到每个失败 job 的不可变 `failureType / failureReview / canAutoRepair`。只有 `SCRIPT_ISSUE` 且未明确禁止自动修复才调用报告关键帧、可信分支基线和 AI YAML 优化；`PRODUCT_BUG / ENV_ISSUE / UNKNOWN / canAutoRepair=false` 只保存诊断草稿。Runner 下发前再次核验来源分类，旧持久化的错误修复也无法绕过。
- AI 新增或改写导航时，修复 YAML 的首个 AI 导航动作前必须有 `aiWaitFor` 起始页稳定态；否则使用现有唯一一次有界纠错让 AI 修正，不用固定 sleep、坐标或新增重试循环。

使用本次线上完整快照离线重放：

- `TC-007` 变为 `scopeReview.ok=false / matchedRequirementIds=[]` 并移出自动池；其余 6 条真实 REQ 用例仍完整覆盖 `REQ-001/002/003`，组合审计 `ok=true / missing=[]`。
- 原照片等待链返回 `path accepted=false`。同一次收敛生成的可信路径为 `首页稳定 -> 照片打印 icon -> 照片打印 -> 5寸照片 -> 校验百度网盘入口`，`currentLeafAdapted=true`，不含历史 `6寸照片`；转换后的 YAML 为 `static ok / scorer 100 / executable / coordinates=0`。
- 原始照片失败仍允许 AI 修复；首页假阳性的 `PRODUCT_BUG` 和扫描 `canAutoRepair=false` 均不再调用 YAML 修复。旧照片修复候选被 `navigation_missing_ready_wait` 准确拦截，交给已有一次有界 AI 纠错。
- `python3 -m py_compile ...`、`python3 tests/backend_static_checks.py`、完整 `npm test` 和 `git diff --check` 通过。完整检查包括 undefined-name、后端 `61`、前端 `69`、Gateway `46`、动态目录/文本/空答/截断/图像/超时降级、Skill fixtures `3/3` 以及 Playwright 桌面/移动端视觉回归。

本地修复完成后待用户 push 和部署。部署后先探测创建时选定的 GPT；有额度且返回有效内容则继续使用 GPT，超时、限流、不可用或结构化截断才按 Gateway 能力路由降级。随后只发起一次相同完整 Agent，固定 `win-runner-01 / ecbfd645`，持续监督生成、首批 Smoke、remaining、修复、真实 Runner 报告、截图/录屏和最终终态；不得选择第二台设备，也不得用离线重放冒充真机成功。

### 2026-07-17 AI 修复成功必须恢复执行链，当前设计页证据必须覆盖历史叶子

部署 `02158b9` 后，以完全相同需求和 Figma 发起 Agent `agent-1784262324968-f9f123a9`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed`，创建时选择 `highway_gpt4_1_mini / gpt-4.1-mini`：

- 线上 `8091 / 8088` 可达；Gateway 实时目录返回 `182` 个 provider 项，`gpt-4.1-mini` 为 `available / live / healthy`。Windows Runner 在线并上报 `yaml_dry_run=true / midscene_model_family=qwen3.6`。同 Runner 虽上报 OPPO 和华为两台设备，但本次原始 job 与修复 job 都只使用 `ecbfd645`，没有向第二台设备下发。
- Figma parser 保持原实现，得到 `4 页 / 4 张 UI 图 / 忽略 0`。4 批全部送入 `gpt-4.1-mini` 并在约 `3 / 6 / 5 / 3s` 完成，`sentToAiForJudgement=true / aiJudgementCompleted=true / hardGate=false`。第 2 批明确识别“照片打印 5 寸照片变体、百度网盘入口及同级排列”。
- 生成结果为 `5 cases / 9 scenarios / 5 YAML`，5 份 static / dry-run 均通过，scorer 均为 `100 / executable`。执行门禁选 1 条文档展示 Smoke，延后照片和扫描 2 条；文档深层跳转与单分支化文案检查 2 条没有进入自动首批/remaining。
- 首次 Smoke `job_1784262680670_00002` 在固定 OPPO 上失败。最终 AI 规划断言已经是“百度网盘紧邻本地文档、同一行第 2 个”，但 case 仍保留上游旧 `expected_result=本地文档入口之后第2位`；YAML 断言选择优先读取旧字段，导致 Runner 把实际正确的 `本地文档、百度网盘、QQ文档、WPS文档` 判为失败。失败被正确归类为 `SCRIPT_ISSUE / assertion_too_strict`。
- 失败分析真实使用报告关键帧和 6 条已验证基线，AI 生成修复草稿；同机修复 job `job_1784262908572_00004` 执行成功并有独立 Midscene 报告。旧状态机仍以首次报告为准，把 `RUN_SONIC / COLLECT_REPORT` 留为失败，不恢复 2 条 deferred，最终 Agent 为 `FAILED / COLLECT_REPORT`。这不是模型、额度、Figma、设备或修复 YAML 失败，而是修复结果没有回写逻辑执行链。
- 人工复核还确认照片 remaining 仍复制历史成功基线的 `6寸照片` 叶子，虽然视觉 AI 已明确看到当前 `5寸照片`；旧视觉结果只有自然语言 judgement，无法作为结构化同分支当前页证据参与确定性收敛。

通用修复：

- 一旦可信 AI path plan 被接受，`flow / assertionTarget / assertions / expected_result` 作为同一执行契约同步写回；旧生成文案不再覆盖 AI 最终断言。原始 case 的 AI plan 和来源证据继续保留，未通过 baseline / requirement mapping 的路径仍不能落地。
- `visual_grounder` 在当前 Frame 能明确映射到候选时，额外返回 `caseId / requirementId / branch / parentPath / navigationLeaf / targetText / sameBranch / confidence / source`。多批证据累积，不被最后一张图覆盖。平台只有在同分支、置信度不低于 `0.75`、真实目标入口文案存在、共同父路径能与成功基线逐级对齐且无坐标/多目标时，才替换历史叶子；与旧叶子绑定的等待也同时移除。Figma 仍是软参考，parser、static、scorer、dry-run 和 Smoke 门禁均未降低。
- 报告改用不可变 attempt ledger，包含原始、扩展和每次修复 job。Runner 汇总同时给出原始 `passed / failed / broken` 尝试数和逻辑 `recovered / unresolved` 用例数；原失败报告、关键帧、错误分类和修复报告均保留，不能把红色简单改成绿色。
- 每个失败源必须有显式 `sourceJobId -> newJobId` 且后继 job 真正通过，才可记为 recovered。成功修复后仅恢复原 gate 中的 deferred executable，继续使用 Agent 创建时选定的 `runnerId / deviceId / deviceStrategy`；固定设备仍逐条终态后再创建下一条。后续失败继续使用现有一次有界 AI 诊断/修复；dry-run 拦截、未覆盖、超时、取消、remaining 未清空或逻辑失败都会保持 Agent 失败。
- 全部 deferred 到终态且没有 unresolved 后，`RUN_SONIC / COLLECT_REPORT` 保存原始失败到 `attemptHistory`，再标记逻辑恢复；原始报告 `status=failed` 不改写，新增 `logicalStatus=recovered`。最终总结可显示“修复后通过”，同时继续显示真实失败尝试数。

验证结果：

- 使用线上完整快照离线重放，新模型对当前数据给出 `2 attempts / 1 passed / 1 failed / recovered=1`，但因 `remainingDeferredCount=2` 仍保持“部分通过”；模拟两条 deferred 真实成功后才变为 `4 attempts / 3 passed / 1 failed / logical 3 of 3 / 修复后通过`。这证明修复成功不会掩盖首次失败，也不会在 remaining 未执行时提前 DONE。
- 使用线上 TC-001 / TC-005 cases 重放，旧 `expected_result` 均与最终 AI `assertionTarget` 对齐。使用仓库真实 `6寸照片打印.yaml` 与本次结构化 Figma 证据重放，共同路径 `照片打印 icon -> 照片打印` 保持不变，历史 `点击「6寸照片」` 被替换为 `点击「5寸照片」`，无坐标、无历史叶子残留。
- `python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py`、`python3 tests/backend_static_checks.py`、完整 `npm test` 和 `git diff --check` 均通过。完整检查包括 undefined-name、后端 `61`、前端 `69`、Gateway `46`、实时目录/降级集成、Skill fixtures `3/3` 和 Playwright 桌面/移动端视觉回归。

待完成：本节修改待提交、用户推送和部署。部署后只发起一次同输入完整 Agent，继续固定 `win-runner-01 / ecbfd645`，持续监督到 Agent、Smoke、修复、remaining、报告、截图/录屏和最终终态；人工复核三个业务分支、5 寸当前页、真实可见文字和无坐标。离线重放不等于真机成功，不得选择或并发执行第二台设备。

### 2026-07-17 结构化输出截断、伪分支路径与历史叶子覆盖当前设计证据

部署 `63ae3f1` 后，以同一需求和 Figma 发起完整 Agent `agent-1784257038297-b3c5e283`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / singleDeviceOnly`，创建时选择 `highway_gpt4_1_mini / gpt-4.1-mini`：

- `8091 / 8088` 健康，AI Gateway 从 Highway 实时返回 `182` 个模型且无目录错误；Windows Runner 在线并上报 `yaml_dry_run=true / midscene_model_family=qwen3.6`，固定 OPPO 上 `com.xbxxhz.box 4.45.0` ready。Agent 终态为 `FAILED / GENERATE_YAML`，没有创建 Runner job、没有操作手机，也没有向同 Runner 上在线的第二台设备下发。
- Figma parser 保持原实现，解析 `4 页 / 4 张 UI 图 / 忽略 0`。4 张图分 4 批全部送入创建时选择的 `gpt-4.1-mini`，约 `4 / 5 / 7 / 5s` 完成，`4/4 completed / fallback=false / hardGate=false`；第 2 批视觉 AI 明确识别“照片打印页 5 寸照片导入页面及百度网盘入口”。设计稿确实参与 AI 判断且仍为软参考。
- 首个根因是 `automation_filter` 请求约 `9909` tokens，模型在默认 `4096` completion tokens 处以 `finishReason=length` 截断，返回非完整 JSON；同模型 45 秒纯语法修复随后超时。旧 Gateway 把非空但被截断的结构化内容当成功，导致 Task 只能在 JSON parser 层补救。
- 第二个根因是候选中存在“扫描复印或扫描仪扫描”“依次进入三个页面”“进入任一业务入口”等伪执行路径；旧覆盖审计按需求映射文本把它们算作多个分支已覆盖，静态 AI 修复还可能把一个备选点击拆成多个顺序点击。
- 第三个根因是最终收敛即使收到 4 批视觉判断，仍可能机械复制照片成功基线的历史“6 寸照片”叶子；旧证据选择又偏好步骤更短的已自动候选，忽略了上游 AI 已生成且路径更具体的“点击 5 寸照片”候选。这解释了“离线 static/scorer 通过，线上业务路径仍不对”的差异。

通用修复：

- `automation_filter` 使用可配置且有界的 `8192` 输出预算，并把预算透传到同模型语法修复。Gateway 对 JSON 请求收到 `finish_reason=length` 时明确判定为结构化输出截断，在原有同一总超时预算内最多使用既有一个能力备用模型；不会把半截 JSON 当成功，也没有新增循环重试。
- 覆盖审计对映射多个需求分支的候选要求每个分支都有独立的具体导航片段和当前页证据；“任一 / 或 / 依次 / 分别”等点击路径不能计入分支覆盖。应用规划和 YAML 静态校验继续阻断多目标 `aiTap`，静态修复不得替 AI 选分支或拆成连续点击。
- 成功基线只复用真实执行过的共同父页面层级和等待策略。若同分支 AI 候选具有更多明确、无歧义的可见文字点击，平台按共同动作锚点对齐并替换历史叶子，再把 `currentLeafAdapted` 证据交给现有唯一一次最终 AI 收敛；没有共同锚点、存在多目标、深层外部动作或超过短链上限时不适配。该逻辑不识别或硬编码 5 寸、6 寸等产品词。
- 最终收敛保留全部已完成视觉批次判断以及累积的 `visual_notes / ui_notes`，不再只看到最后一个 Frame。AI 返回了完整、同需求、同基线且覆盖全部有界验收项的当前路径时保留 AI 路径；否则使用已验证证据并继续经过 YAML static、scorer、dry-run、Smoke 和真实 Runner 门禁。
- 可信首页起点统一补一个可见首页稳定等待。重复的来源页加载等待和落地页观察由紧邻的入口断言及独立 `assertionTarget` 承担，维持最多 8 步的短链并减少重复模型观察。

验证结果：

- 使用本次线上完整 artifacts 精确重放，最终为 `4 executable / 12 of 12 checks / missing=0 / unresolved=0`；5 条数量目标仅保留 advisory，没有为凑数升级低价值用例。文档打印、照片打印、扫描复印均为独立路径。
- 只读真实模型探针实际调用 `gpt-4.1-mini / fallback=false / finishReason=stop`，约 `26s`，`promptTokens=17698 / completionTokens=2755`。最终 4 份 YAML 全部 `validate ok / scorer 100 / executable / warnings=[] / coordinates=0`；照片可达路径明确为 `照片打印 -> 5寸照片 -> 百度网盘`，不含 `6寸照片`，相邻“一寸照”设计状态作为独立展示检查保留。
- 完整 `npm test` 通过：undefined-name、后端 `61`、前端 `69`、Gateway `46`、实时模型目录、普通错误/空答/结构化截断/图像/超时降级、Skill fixtures `3/3` 以及 Playwright 桌面/移动端视觉回归；Python 主链编译和 `git diff --check` 通过。

待完成：本节代码待用户推送和部署。部署后使用完全相同输入只发起一次完整 Agent，模型仍按创建选择贯穿；首选 GPT 有效时使用 GPT，超时、不可用、限流或结构化截断时才按能力路由降级。固定 `win-runner-01 / ecbfd645`，持续监督到 Agent、首批 Smoke、remaining、Runner 报告、截图/录屏和最终终态；不得选择第二台设备，也不得用本地重放代替真机成功。

### 2026-07-17 已选成功基线必须绑定到对应 AI 候选，视觉软证据不得删除需求断言

部署 `4df77a9` 后，以同一需求和 Figma 发起完整 Agent `agent-1784253374492-b0803487`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / singleDeviceOnly`，模型为 `highway_gpt4_1_mini / gpt-4.1-mini`：

- `8091 / 8088` 健康，Windows Runner 在线并上报 `yaml_dry_run=true / midscene_model_family=qwen3.6`；OPPO 上 `com.xbxxhz.box 4.45.0` ready。任务终态为 `FAILED / GENERATE_YAML`，没有创建 Runner job，也没有向华为或第二台设备下发。
- Figma parser 保持原实现，解析 `4 页 / 4 张 UI 图 / 忽略 0`。4 张图分 4 批全部送入 `gpt-4.1-mini`，分别约 `4 / 7 / 7 / 4s` 完成，`4/4 completed / fallback=false / hardGate=false`；设计稿确实参与 AI 判断且仍是软参考。
- Top3 基线已按三个分支各选中一条 `verified_execution / execution_success`：文档打印成功修复基线、`6寸照片打印.yaml`、`证件扫描.yaml`。但初始规划仍把扫描复印两条候选转为 manual，理由是缺少扫描 Frame 和可信路径；最终组合只有文档、照片 4 条 executable，覆盖 `8/12`，缺少扫描复印 visibility / relation / copy / reachability，覆盖门禁正确阻断。
- 失败不是模型额度、视觉超时、Figma 门禁、Runner 或设备故障。收敛请求虽然携带全局扫描成功基线，但旧证据构造只给 automatic 候选绑定来源页证据，且压缩后的基线未携带 `baseline.start_page`；已被上游 AI 转 manual 的扫描候选看不到同分支基线，模型只能再次声称“没有可信基线”。
- 同一轮还发现视觉增量把照片入口原有的“百度网盘可见 / 文案 / 同级”断言整体替换成相邻 Frame 的“一寸照标题 / 拍照建议 / 温馨提示”。覆盖门禁因此依赖后续补救，而不是从源头保持需求契约。

通用修复：

- 基线压缩时从缓存 snippet 的 `# baseline.start_page` 恢复明确前置。显式需求候选即使被上游 AI 保守转为 manual，只要同分支已选择执行成功基线、真实可见文字路径唯一、需求映射完整、且不包含账号、验证码、确认授权、选文件或破坏性动作，也会收到可审计的 `convergenceEvidence`。
- 对同一目标入口，上游 AI 产生的多个“点击后首个稳定可见状态”可以合并为有界 alternatives，例如授权窗口或内容列表；只复用 AI 已生成的可见终态，不由平台编造产品页面。模型仍执行现有最终收敛；若模型继续判 manual，平台仅在上述证据全部成立时将该候选放入 `remaining`，随后仍必须通过需求范围、YAML static、scorer、dry-run 和真实 Runner。
- 视觉校准改为单调合并：视觉 AI 可以补充或修正当前 Frame 实际覆盖的断言，但不能删除它没有处理的 requirement-mapped visibility / copy / relation。审计记录保留的 acceptance check IDs；Figma parser、图片分批和软参考策略均未修改。
- 如果步骤已经包含完整最终断言的显式等待，YAML 转换不再生成重复 `aiWaitFor`，减少一次模型观察开销。没有新增模型轮次、执行模式、数量硬门槛或业务词硬编码，也没有修改 scorer、Sonic、Runner、`router.py` 或历史 YAML。

使用本次线上失败 cases JSON 和本地真实 `证件扫描.yaml` 精确重放，即使模拟最终模型仍坚持把扫描候选判为 manual：

- 组合从 `4 executable / 8 of 12 checks` 收敛为 `5 executable / 12 of 12 checks / missing=0`。新增项是补齐显式扫描分支的 remaining 用例，不是为了达到 5 条而凑数；首批仍为 3 条 Smoke。
- 扫描路径为 `App 首页 -> 扫描复印 icon -> 证件扫描 -> 立即使用 -> 校验入口 -> 点击入口 -> 校验任一首个稳定状态`，全部使用真实可见文字，不使用坐标。
- 5 份 YAML 逐条通过 `validate_midscene_yaml`，均为 `ok=true / warnings=[] / issues=[]`；scorer 全部为 `100 / executable / 0 warnings`，坐标动作数为 0。

已验证：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/midscene-pycache python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/ai_skill_service.py task_server/services/yaml_executable_scorer.py
PYTHONPYCACHEPREFIX=/private/tmp/midscene-pycache python3 tests/backend_static_checks.py
PYTHONPYCACHEPREFIX=/private/tmp/midscene-pycache npm test
git diff --check
```

结果：undefined-name、后端 `61`、前端 `69`、Gateway `46`、实时模型目录和文本/空答/图像/超时降级集成、Skill fixtures `3/3`、Playwright 桌面/移动端视觉回归全部通过。

待完成：本节修改尚待提交、用户推送和部署。部署后先真实探测 `gpt-4.1-mini`；有额度且返回有效内容则继续使用创建 Agent 时选定的 GPT，不可用、限流或超时才按既有能力路由降级到 `qwen3.6-plus`。随后用完全相同输入只发起一次完整 Agent，固定 `win-runner-01 / ecbfd645`，持续监督生成、首批 Smoke、remaining、Runner 报告、截图/录屏和最终终态；不得选择第二台设备，也不得用离线重放代替真机成功。

### 2026-07-17 automation_filter 畸形 JSON 使用选定模型做一次有界语法修复

部署 `f8b8eeb` 后，先后用 GPT 与千问执行同一完整回归，均固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / OPPO PHM110 / fixed / singleDeviceOnly`：

- GPT Agent `agent-1784249816070-e454ec58` 与 Qwen Agent `agent-1784250515450-26a3a030` 均在 `GENERATE_YAML` 终态失败，没有创建 Runner job、没有操作固定 OPPO，也没有向第二台设备下发。
- 两轮都保持原 Figma parser，解析为 `4 页 / 4 张 UI 图 / 忽略 0`。GPT 视觉 4/4 批完成；Qwen 视觉 4/4 批完成，实际模型均为 `qwen3.6-plus / qwen_plus / fallback=false`，约 `14-17s/批`。设计稿确实送 AI 判断，且继续是软参考而不是硬门禁。
- Qwen 生成 6 份 YAML，覆盖收敛从缺 1 个 reachability 验收点推进到 `missing=[] / unresolved automatic candidates=0`；三类 Top3 参考分别来自文档打印成功修复基线、6 寸照片打印成功基线和证件扫描成功基线。6 份 YAML 的 static / task scorer 均为 `100 / executable`，但最终全被来源门禁降为 `needs_review`，因此没有把未确认来源的脚本冒险下发 Runner。
- 实际失败不是视觉、覆盖、YAML 动作或 GPT 额度。Qwen `automation_filter` 返回了约 9KB 业务 JSON，但缺少 JSON 分隔符，原始错误为 `Expecting ',' delimiter: line 290 column 6 (char 8952)`。旧代码捕获所有异常后统一写成 `local_fallback_after_ai_timeout`，既误报“超时”，又让后续已收敛的 6 份 YAML 一直携带本地兜底来源。
- GPT 证据也不支持“额度耗尽”：`gpt-5-mini` 的生产规模重放返回 `finish_reason=length / completion_tokens=4096 / reasoning_tokens=4096 / visible output=0`，Gateway 已正确降级；同一请求使用 `gpt-4.1-mini` 在约 11 秒返回有效 JSON。按用户最新决定，下一轮完整验收仍固定使用 `qwen3.6-plus`。

通用修复：

- 只有 `automation_filter` 已返回内容但 `json.loads` 发生语法错误时，才把“原始畸形 JSON + parse error + Skill schema”交给创建 Agent 时选择的同一模型做一次纯语法修复。禁止新增、删除、改写或重排业务内容；不重跑完整需求/Figma/基线分析，也不增加常规成功链路耗时。
- 修复调用硬限制为一次、默认最多 `45s`、输入最多 `30000` 字符，不带图片。超限、修复再次失败、schema 不合法、网络错误或真实超时均进入原有保守兜底，不会循环修复。
- trace 新增 `jsonRepairAttempted / jsonRepairSucceeded / jsonRepair`，并保留首轮与修复轮的实际 provider/model/fallback/finish/usage。失败来源准确区分 `local_fallback_after_ai_timeout / local_fallback_after_ai_invalid_json / local_fallback_after_ai_failure`。
- 所有 `local_fallback_after_ai_*` 仍统一限制为 `needs_review`，static/scorer 不得提升为 executable。旧 `local_fallback_after_ai_timeout` 数据保持兼容；没有降低 coverage、scorer、static、dry-run、Smoke 或 Runner 门禁。
- 没有修改 Figma parser、业务提示词、`router.py`、scorer、Sonic、Runner、执行模式、设备策略或历史 YAML。

已验证：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/midscene-pycache python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
PYTHONPYCACHEPREFIX=/private/tmp/midscene-pycache python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：定向测试覆盖“同模型修复成功 / 修复再次失败仍阻断 / 真实超时单独分类”；完整 undefined-name、后端 `61`、前端 `69`、Gateway `46`、实时目录与模型降级集成、Skill fixtures `3/3`、Playwright 桌面/移动端视觉回归全部通过。

待完成：提交后由用户推送并部署；部署后使用同一需求、Figma 和 `qwen3.6-plus` 再发起一次完整 Agent，固定 `win-runner-01 / ecbfd645`，持续监督到 Agent、首批 Smoke、remaining 和报告全部终态。人工复核三个业务分支、真实可见文字、无坐标、Runner 报告、截图/录屏和失败分类；不得选择第二台设备，也不得把本地测试当作真机成功。

### 2026-07-16 GPT 长 Skill 空内容不能冒充成功

部署 `56001ae` 后发起同一完整回归：

- 线上 `8091 / 8088` 健康；AI Gateway 为非 mock。`GET /ai/providers` 从 Highway 上游实时返回 `180` 个模型，`catalog.channels[0].source=live / errors=[]`，`highway_gpt5_mini / gpt-5-mini` 的真实 provider test 在约 `2.6s` 返回 `gateway ok`。
- Windows Runner `win-runner-01` 在线并上报 `yaml_dry_run=true / midscene_model_family=qwen3.6`。固定设备 `ecbfd645 / OPPO PHM110 / Android 15 / com.xbxxhz.box 4.45.0` 为 ready；Agent 请求明确为 `RUNNER_JOB / fixed / singleDeviceOnly`，未选择第二台设备。
- Agent `agent-1784197491234-39e4098d` 在 `PLAN` 终态失败。`PREPARE_SOURCE` 保持原 Figma parser 并得到 `4 页 / 4 张 UI 图 / 忽略 0`；核心 `requirement_analyzer` 失败后，下游 scenario、视觉校准、YAML 和 Runner 全部未执行，因此没有创建 Runner job，也没有操作任何手机。
- 同一 GPT 的短 `/ai/skill` 探针以 `HTTP 200 / 216 bytes / 3.29s` 返回合法 JSON。使用本轮真实需求、Figma 软证据和 Top3 成功基线上下文重建生产规模 `requirement_analyzer` 请求后，稳定复现 `HTTP 200 / 202 bytes / 47.09s`，正文为 `success=true` 但 `content="" / fallbackUsed=false`。

根因与通用修复：

- Gateway 旧实现直接将 `completion.choices[0].message.content || ""` 标为成功；空 assistant 内容既不会触发既有备用路由，也没有保存 `finish_reason / usage`。Task 随后对空字符串执行 JSON 解析，最终只显示失真的 `Expecting value: line 1 column 1`。
- Gateway 现在兼容字符串和 text-part 数组输出，并把空白、缺失内容或去 fence 后的空内容视为可降级 provider failure。在同一总预算内最多尝试既有唯一备用模型；两个候选都空答时返回明确失败，不能冒充成功。
- AI 调用日志及所有 Gateway AI 响应增加 `finishReason` 和汇总 token usage（prompt / completion / total / reasoning），空答原因包含首选模型的 finish/token 证据；不记录或使用模型 reasoning 正文。
- Task AI Skill 客户端区分空 HTTP body、非 JSON/HTML body、非对象 JSON、HTTP 错误和 `success=true` 包裹中的空模型内容，并在拒绝前保存实际 provider/model/fallback/finish/usage trace。后续不再用 JSON parser 异常掩盖传输或模型空答。
- OpenAI Chat Completions 官方文档说明 `max_completion_tokens` 同时包括可见输出和 reasoning tokens，且 GPT-5.1 之前默认使用 reasoning。当前旧响应没有 finish/token 证据，因此本轮没有凭推断修改 GPT reasoning 或 token 参数；部署后 trace 会直接证明是否为 `length / reasoning_tokens`：`https://platform.openai.com/docs/api-reference/chat/create`。
- 没有修改 Figma parser、提示词业务规则、scorer、static gate、Runner、Sonic、执行模式、设备策略或历史 YAML。

行为验证：

- 假上游 `gpt-empty` 返回 `HTTP 200 + content="" + finish_reason=length + completion/reasoning_tokens=256` 时，真实路由为 `gpt-empty -> qwen-plus`，响应 `fallbackUsed=true / fallbackIndex=1` 并保留空答原因；两个候选都空答时 Gateway 返回 `HTTP 500`。
- Task 客户端行为测试验证空 body、HTML body、数组 JSON 和成功包裹中的空模型内容分别得到准确诊断；空内容错误保留 `finish_reason=length / reasoning_tokens=4096`。
- 完整 `npm test` 通过：undefined-name、后端 `61`、前端 `69`、Gateway `46`、实时目录/普通错误/空答/图像/超时降级集成、Skill fixtures `3/3` 和 Playwright 桌面/移动端视觉回归。

待完成：提交后由用户推送并部署；部署后先用同一生产规模 Skill 核对首选 GPT 的 `finishReason / usage` 和实际 fallback，再以完全相同输入重跑固定 `win-runner-01 / ecbfd645` Agent，持续监督生成、Smoke、remaining、报告和终态。不得选择第二台设备，也不得把本次本地测试当作真机成功。

### 2026-07-16 Agent 选定模型贯穿、可审计能力降级与有界超时

本地代码已完成，尚未推送/部署，也未据此宣称 OPPO 真机回归成功。

- Agent 创建时选择的 `providerId + model` 现在贯穿目标理解、用例召回、MM requirement/scenario/filter/smoke、Top3 基线重排、范围规划、视觉校准、覆盖补全、YAML 规划/收敛、静态 AI 修复、Runner 失败分析、修复草稿和缺陷草稿。显式选模时，Python 服务不再在 Gateway 失败后静默直连另一千问模型；无显式模型的旧入口仍保留兼容兜底。
- 图片先送用户选定模型。仅当 Gateway 收到超时、限流、5xx、模型不可用或明确“不支持图像”时，才使用 `fallbackModelConfig` 指定的视觉模型；默认备用 provider 为 `qwen_plus`，模型沿用 `DASHSCOPE_VL_MODEL`。Figma parser、页面筛选、4 图分批和软参考门禁均未修改。
- 每个 AI 产物记录 `selectedProviderId / selectedModel` 与实际 `providerId / model / fallbackUsed / fallbackIndex / fallbackReason`。PLAN 不再固定写 `fallbackUsed=false`，而是聚合 MM Skill 的真实 trace；视觉批次、失败分析和每条修复草稿也保留实际模型证据。
- Gateway 的 `/ai/chat`、`/ai/skill`、生成、失败分析和修复接口共用同一降级实现。显式选模最多尝试“首选 + 当前能力路由的 1 个备用”；调用方传入 `timeoutMs` 总预算，Gateway 为备用保留有界窗口、关闭 SDK 隐式重试，避免 Python 已超时而 Gateway 仍后台占用连接。
- 非千问模型继续通过上游 `/models` 实时发现；千问保持独立静态配置。上游目录只证明账号可见，不臆测图像能力，能力由真实请求验证。
- 最终 YAML coverage convergence 只发送未收敛候选、验收缺口、可信证据和压缩后的来源上下文；已批准 executable 由平台保留。超时从 45 秒调整为 60 秒，没有增加模型轮次或放宽覆盖/scorer/static/Runner 门禁。

行为验证：

- 假上游目录返回 5 个实时模型，动态 provider ID 可保存并在目录 503 时继续解析。
- 文本不可用：`gpt-down -> qwen-plus`；`/ai/chat` 按已保存 `agent_plan` 路由为 `gpt-down -> gpt-new`。
- 图像能力不支持：`gpt-no-vision -> qwen-vision`，响应保留能力错误原因。
- 首选模型故意挂起 4 秒、总预算 5 秒时：`gpt-hang -> qwen-plus`，约 `3.0s` 完成降级调用。
- 显式 GPT 的用例直选/语义重排在 Gateway 候选耗尽后直连 DashScope 次数为 `0`。

已验证：完整 `npm test` 通过，包括 undefined-name、后端 `61`、前端 `69`、Gateway `46`、实时目录/文本/图像/超时集成测试、Skill fixtures `3/3` 和 Playwright 桌面/移动端视觉回归；`git diff --check` 通过。

待完成：提交后由用户推送并部署；线上核对 `/ai/providers` 实时目录与实际模型 trace，再使用同一需求固定 `win-runner-01 / ecbfd645 / fixed` 发起一次完整 Agent，持续监督 Smoke、remaining、报告、截图/录屏和终态。不得选择第二台设备。

### 2026-07-16 AI Gateway 非千问模型实时目录

线上核对结果：`GET /ai/providers` 只返回 `gpt-5-mini`、`gpt-4.1-mini` 和 `qwen-plus`，直接来自 `config/providers.json`。前端 Agent 下拉已调用该接口，根因在 Gateway 目录源写死，不在前端。

通用修复：

- `providers.json` 只保留通道、Key 环境变量名、参数策略和兼容种子。千问保持 `catalogMode=static` 独立配置；非千问 OpenAI 兼容通道按 `baseUrl + apiKeyEnv + type` 去重，调用上游 `client.models.list()` 获取当前账号可见模型。旧线上配置即使没有 `catalogMode` 也能自动识别，部署不覆盖现有 Key 配置。
- 目录请求最多 `5s`，成功结果缓存 `60s`，默认禁止匿名 `refresh=1` 绕过缓存。上游失败时返回 `catalog.errors`，并保留种子模型为 `configured_fallback / available=null`；目录故障不会让模型页整体不可用。
- 新发现模型使用可逆 `catalog_*` provider ID，可用于 Agent、`/ai/providers/test` 和全局 router，保存后跨服务重启仍能解析。
- 修正 fallback 串模型：用户模型只覆盖首选路由；超时、429 / 5xx、model 不可用或能力不支持时，备用 provider 使用自己的模型，不再把 `gpt-5-mini` 带到千问通道。
- Agent 下拉标记“实时目录 / 目录降级”，并不再混入 Task `/api/models` 中旧静态 Gateway 重复项；DashScope 独立项仍保留。

依据与验证：

- OpenAI 官方 Models API 说明 `/v1/models` 只提供当前可用 ID 和 owner / created 等基本信息：`https://platform.openai.com/docs/api-reference/models/object?lang=curl`。因此平台分开“实时列举”和“真实能力测试”，不凭名称宣称支持图像。
- 本地假上游行为测试验证实时 3 模型、动态 ID 调用 / 保存、目录 503 降级和已保存 ID 继续可用。首选 `gpt-down` 返回 503 后，实际请求序列为 `gpt-down -> qwen-plus`。
- `npm test` 全部通过：u540e端 `61`、前端 `69`、Gateway `46`、目录集成、Skill fixtures `3/3` 和 Playwright 桌面 / 移动端回归；`git diff --check` 通过。

待完成：部署后核对线上 `catalog.channels[].source=live`、实时数量和 `catalog.errors=[]`。“Agent 选中模型贯穿文本 / 视觉 / 失败分析 / 修复”仍是后续代码项，不把目录完成冒充为贯穿已完成。本轮未修改 Figma parser、YAML scorer / static、Runner / Sonic、设备策略、执行模式或历史 YAML。

### 2026-07-16 部署后 GPT Agent 验收：区分等待截止时间并贯通成功基线证据

部署 `cb7f7ed` 后发起同一完整回归：

- Agent `agent-1784182814050-d7b01959`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed`，文本规划和 YAML 收敛均为 `highway_gpt5_mini / gpt-5-mini`。8091 / 8088、AI Gateway、Sonic 健康；Windows Runner 在线并上报 `yaml_dry_run=true / qwen3.6`，固定 OPPO PHM110 在线。任务终态为 `FAILED / GENERATE_YAML`，没有创建 Runner job，也没有向第二台设备下发。
- Figma parser 保持原实现并解析 `4 页 / 4 图 / 忽略 0`。4 张图分 4 批真实送入视觉 AI，分别约 `22 / 18 / 18 / 13s` 完成，最终 `4/4 completed / hardGate=false`；设计稿继续作为 AI 软参考。
- `cb7f7ed` 已在线生效：初始组合覆盖 `10/12`，缺扫描复印 relation / reachability；GPT 在一次既有收敛调用中选择 `TC-006` 补齐扫描可达，来源页补强补齐 relation，最终组合为 `12/12 / 6 executable / 0 unresolved`，且没有覆盖退化。
- 扫描路径使用平台记录的成功执行基线 `d623c1e73180bfac / 证件扫描`，来源为 `verified_execution / execution_success`，实际可见文字路径为 `扫描复印 icon -> 证件扫描 -> 立即使用 -> ...`。这不是本地 `文件扫描.yaml`，也不是仅按标题相似推断。
- 后置需求范围门禁把 `TC-006` 的普通步骤“等待授权页、文件列表页或 H5 登录页加载，超时15秒”误判为需求未声明的“超时异常场景”，将其转回 manual，最终只生成 5 份 YAML 并在覆盖门禁阻断。已生成的 5 份均通过 static / scorer；本轮失败不是 Figma、Runner、设备或这 5 份 YAML 执行失败。

根因与通用修复：

- 范围门禁现在区分用例意图与执行机制：标题、目标、断言或标签要求网络超时处理时仍会阻断；步骤中的数值等待截止时间（例如 `，超时15秒`）只作为执行参数，不再被提升为新测试场景。没有放宽弱网、断网、超时提示或重试场景门禁。
- 成功基线身份此前只存在于 AI 收敛计划，确定性 YAML 转换 / 首页动作修复后没有传给 scorer；长链路因此被当作“无成功基线”降为 manual。现在仅当 baseline 同时满足服务端 `verified_execution / execution_success`、AI 已按该 ID 落地同分支路径、需求映射未被门禁拒绝时，才记录 `baselineVerified=true` 并在 YAML 评分前恢复显式来源证据。
- 所有既有 `matched baseline` 注释会先被清除，再按服务端验证结果重写；未验证候选、只匹配标题的基线或 AI 自行写入的注释不能伪造 scorer 证据。scorer、静态白名单、需求范围、dry-run、Smoke 阈值和真实 Runner 门禁均未修改。
- 没有产品词或单一 case ID 硬编码，没有新增模型调用、重试或执行模式，也没有修改 Figma parser、`router.py`、历史 YAML、scorer、Sonic / Runner 脚本或设备策略。

使用本次线上失败 cases JSON 精确重放：

- 从范围门禁前状态恢复 `TC-006`，按线上三条 `verified_execution / execution_success` 基线重放 `scope gate -> automation split -> YAML 转换 -> 本地静态修复 -> 可信证据恢复 -> syntax / executable / static / stability / requirement scope / scorer`。
- 最终保留 6 条、转出 6 份 YAML，`TC-001..TC-006` 全部为 `100 / executable`，所有检查通过；`TC-006` 确实发生首页动作修复，并在修复后恢复 `d623c1e73180bfac` 来源证据。重放结果保存于 `/tmp/agent-cb7f7ed-complete-replay.json`。
- 该结果只证明生成门禁可以放行，不代表 App 真实断言成功。必须部署本轮提交后再执行一次完整 Agent，并以 OPPO Runner 报告、截图 / 录屏和终态为准。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/ai_skill_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name、后端 `61` 项、前端 `69` 项、AI Gateway `46` 项、AI Skill contract fixtures `3/3`，以及 Playwright 桌面 / 移动端视觉回归全部通过。

待完成：

- 推送并部署本轮修复。
- 部署后只发起一次同输入真实验收，固定 `win-runner-01 / ecbfd645 / gpt-5-mini`。持续监督 Agent、首批 Smoke、remaining 和可能的一次有界 AI 修复到终态；人工复核三业务分支、真实可见文字、无坐标，以及 OPPO Runner 报告和截图 / 录屏，不能以离线重放替代 Runner 成功。

### 2026-07-16 部署后 GPT Agent 验收：来源页补强必须保留已有覆盖维度

部署 `83c5f8f` 后发起同一完整回归：

- Agent `agent-1784180626840-ca8eeade`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed`，文本模型为 `highway_gpt5_mini / gpt-5-mini`。8091 / 8088、AI Gateway、Sonic 健康；Windows Runner 在线并上报 `yaml_dry_run=true / qwen3.6`，固定 OPPO PHM110 在线。终态为 `FAILED / GENERATE_YAML`，没有创建 Runner job，也没有向第二台设备下发。
- PREPARE_SOURCE 保持原 Figma parser 并解析 `4 页 / 4 图 / 忽略 0`。视觉资料全部送入 AI，4 个批次分别约 `17 / 20 / 19 / 17s` 完成，最终 `4/4 completed / hardGate=false`；图片仍是软参考，不是失败门禁。
- PLAN、baseline reranker、scope planner、initial planner 和 convergence 均记录 `highway_gpt5_mini / gpt-5-mini`。初始组合有 7 条 executable、1 条未收敛自动候选，已覆盖 `11/12`；唯一缺口为 `REQ-003-CHECK-02` 扫描复印页的同级层级和位置关系。
- `83c5f8f` 已在线生效：最终收敛确实为 `TC-007` 构造并应用 `source_ui_assertion`，同时处理 `TC-008`，拟议组合的未收敛候选降为 0。但补强只使用当前缺失的 relation 文本，替换了 `TC-007` 原有“百度网盘入口展示、无缺失”断言，覆盖从缺 relation 交换成缺 copy。单调门禁正确记录 `added=REQ-003-CHECK-02 / regressed=REQ-003-CHECK-03` 并拒绝拟议组合，所以没有把退化 YAML 下发 Runner。

根因与单点修复：

- `source_ui_assertion` 现在先从原候选的 `assertions / expected / ai_case_plan.assertionTarget` 中挑选真正覆盖来源页 visibility / copy / relation 的最小断言集合，再追加当前缺失维度；每条旧断言必须新增一个已覆盖维度，避免重复文案和长提示。补强由“替换”改为“单调合并”，AI 仍负责规划和收敛，平台只保证证据组合不丢失已覆盖验收维度。
- 新回归测试构造“已有可见 + 文案，只缺同级关系”的通用入口需求，验证补强后三个来源页维度同时存在且 portfolio audit 通过。没有产品词硬编码，没有新增模型调用或重试，没有修改 Figma parser、数量门槛、scorer、static、Runner、执行模式或历史 YAML。
- 方案边界与成熟实践一致：[BrowserStack mobile automation](https://www.browserstack.com/docs/test-companion/mobile-testing/automate-tests) 使用真实设备、UI hierarchy、截图、显式等待和有意义断言；[BrowserStack self-heal](https://www.browserstack.com/docs/low-code-automation/test-recording/browserstack-ai/ai-self-heal) 只在规则定位失败后使用 AI 并记录原因，不能掩盖 App 崩溃或连接问题；[Mobile-Agent-v2](https://arxiv.org/abs/2406.01014) 采用规划、决策、反思和记忆分工。平台对应采用“成功基线路径 + 当前证据 + 一次有界 AI 反思 + 确定性门禁”，不会让 AI 把产品失败自动改成成功。

使用本次线上失败 cases JSON 精确重放：

- 修复前为 `11/12`、缺 `REQ-003-CHECK-02`、7 条 executable、1 条未收敛；修复后为 `12/12`、`missing=[]`、7 条 executable、0 条未收敛。`TC-007` 最终断言只保留一次“扫描复印页面展示百度网盘入口，无缺失”，并合并“同级入口的层级和位置关系”。
- 转换层按既有安全规则把人工来源的“快速连续点击 3 次”候选 `TC-005` 归回 manual，实际生成 6 份 YAML，不为数量硬凑。6 份均通过语法、可执行、static 和稳定性检查，scorer 为 `100 / 89 / 89 / 89 / 100 / 88`，全部为 executable；步骤使用真实可见文字，没有坐标。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name、后端 `61` 项、前端 `69` 项、AI Gateway `46` 项、AI Skill contract fixtures `3/3`，以及 Playwright 桌面 / 移动端视觉回归全部通过。

待完成：

- 提交、推送并部署本轮单点修复。
- 部署后只发起一次同输入验收运行，固定 `win-runner-01 / ecbfd645 / gpt-5-mini`。必须确认生成覆盖门禁通过，再人工复核实际 YAML；持续监督 OPPO 首批 Smoke、remaining 和可能的一次有界修复到终态，核对 Runner 报告、截图 / 录屏和失败分类。真实 App 断言是否通过只能由这次 Runner 执行确认，不以离线重放冒充成功。

### 2026-07-16 部署后 GPT Agent 验收：闭合已有 executable 与人工观察尾链

部署 `81cacbe` 后发起同一完整回归：

- Agent `agent-1784176347317-b8789e86`，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed`，文本模型明确为 `highway_gpt5_mini / gpt-5-mini`。8091 / 8088、AI Gateway、Sonic 健康；Runner 持续上报 `yaml_dry_run=true / qwen3.6`，OPPO PHM110 在线。终态为 `FAILED / GENERATE_YAML`，没有创建 Runner job，也没有向华为或第二台设备下发。
- PREPARE_SOURCE 保持原 Figma parser 并解析 `4 页 / 4 图 / 忽略 0`。PLAN 使用既有两次上限完成一次受控校正；每次都把 4 张图逐批送入视觉 AI，最终报告为 `4/4 completed / attempted=4 / hardGate=false`，单批约 `13-26s`。视觉资料确实参与判断且仍是软参考。
- PLAN 生成 8 条 AI 业务分支；YAML 阶段的 baseline reranker、scope planner、initial planner 和 convergence trace 均为 `highway_gpt5_mini / gpt-5-mini`。三个必需分支各选中一条 `verified_execution / execution_success` 基线：文档、6 寸照片、文件扫描。
- 初始组合已有 5 条 executable，覆盖 `9/12`；缺文档 reachability、扫描 relation / reachability。最终 GPT 收敛保持这 5 条，但没有补写 3 个缺口，单调门禁正确拒绝无改进结果。失败不是 5 条数量目标、Figma、scorer、Runner 或设备问题。

根因与有界修复：

- 平台已为扫描 relation 构造 `source_ui_assertion`，但旧应用条件只在模型把候选降级时采用可信证据；模型保持候选为 executable 时，缺失断言反而不会补强。现只要证据来自同分支成功基线、显式缺失 acceptance check 和上游自动候选，即使模型保持 executable，也会写回经过验证的补强路径。
- 文档 / 扫描 reachability 的人工候选已经由 AI 写成“点击入口后只检查授权页、登录页或文件列表，并且没有白屏/崩溃”。旧稳定性词表漏掉“没有白屏 / 没有崩溃”等自然表达；同时已有 executable 来源 case 没有映射回 automatic record，导致安全尾链通过局部检查后仍被丢弃。现补齐等价稳定性表达和来源记录映射；账号、验证码、确认授权、选文件等深层动作仍由原门禁拒绝。
- 组合时保留上游 AI 候选的启动稳定等待。来源页可见 / 文案 / 同级断言在点击目标入口前执行，点击后只断言首个合法落地状态，避免同一最终断言同时要求“已离开来源页”和“来源页入口仍可见”。不新增模型调用、重试或执行模式，不降低 scorer / static / Runner 门禁，也没有业务关键词特判。

使用本次线上失败 cases JSON 精确重放：

- 保留线上 GPT 的 5 条 executable 决策，可信证据只补强 `TC-001 / TC-005`；最终 `12/12`、`missing=[]`、5 条 executable。文档和扫描各复用同分支真实文字基线路径，再拼接 AI 已生成的有界首屏观察尾链。
- 5 份 YAML 均通过语法、可执行、static 和稳定性检查；scorer 分别为 `88 / 100 / 100 / 100 / 82`，全部为 executable。来源页断言位于点击前，落地断言位于点击后；无坐标、无账号 / 授权确认 / 文件选择动作。
- `bounded_landing` 统一进入 remaining；首批仍受最多 3 条 Smoke 控制。测试覆盖模型保持 executable、已有 executable 来源 + 人工观察尾链、`没有白屏/崩溃`、启动等待保留和前后断言时序。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name、后端 `61` 项、前端 `69` 项、AI Gateway `46` 项、AI Skill contract fixtures `3/3`，以及 Playwright 桌面 / 移动端视觉回归全部通过。

待完成：

- 提交、推送并部署本轮有界修复。
- 部署后只发起一次同输入验收运行，固定 `win-runner-01 / ecbfd645 / gpt-5-mini`。必须轮询 Agent、Smoke、remaining 和可能的一次 AI 修复到终态，并人工复核 YAML、OPPO Runner 报告、截图 / 录屏和真实失败分类；不把每次产品断言失败自动扩展成新规则。

### 2026-07-16 GPT Agent 回归：区分完整可执行池与首批 Smoke，并用成功基线闭合来源页断言

部署 `ae72da4` 后发起同一完整回归：

- Agent `agent-1784173704570-40dc6cc9`，目标 / 需求 / Figma / App 与前轮一致，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed`，文本模型为 `highway_gpt5_mini / gpt-5-mini`。8091 / 8088、AI Gateway、Sonic 健康；Windows Runner 心跳约 3 秒并上报 `yaml_dry_run=true / qwen3.6`，OPPO PHM110 在线、App `4.45.0` ready。任务终态为 `FAILED / GENERATE_YAML`，没有创建 Runner job，也没有向华为或第二台设备下发。
- `ae72da4` 已在线生效：PLAN 的 8 条 flow 分别恢复为文档打印、照片打印、扫描复印三个来源分支，全局场景没有被强行归类。YAML Top3 的 `required_branch_count=3`，三个分支 eligible 成功基线数为 `4 / 3 / 4`，GPT 选中 `1b4e6a94768902d3`（文档）、`d8931c2dd082926e`（6 寸照片）、`02b01e0cab690788`（文件扫描），没有无基线或跨分支引用问题。
- Figma parser 保持原实现并解析 `4 页 / 4 图 / 忽略 0`。4 张图逐批送入 `qwen3.6-plus`，约 `40 / 19 / 19 / 17s` 全部完成，`retry=false / hardGate=false`；第 2、3 批正确识别照片打印页与百度网盘入口，第 4 批只说明当前一寸照配置 Frame 没有导入入口。视觉资料确实送 AI 且仍为软参考。
- 初始 GPT 可执行组合为文档展示、照片可达、扫描可达，覆盖 `5/12`；最终收敛新增文档可达，却以“保持 Smoke 精简”为由把扫描可达转人工，仍为 `5/12` 且丢失整个扫描需求点。最终 3 条 executable、11 条 manual，覆盖门禁正确阻断。

根因与通用修复：

- GPT 把“首批 Smoke 最多 3 条”误解为“完整 executable 最多 3 条”，并继续把兄弟页面缺少 Figma Frame 当作展示断言的硬门禁。规划契约现明确：`cases` 是完整可执行池，前 3 条为 `batch=smoke`，其他合格项为 `batch=remaining`；`manual_cases` 不是 Smoke 溢出池。请求同时传入机器可读 `batchContract / evidenceContract`，不增加模型轮次。
- 单个视觉批次只能修改路径、文案、断言和 `repair_hints`。视觉模型若试图把输入自动候选跨数组移动到 manual，平台保留候选身份并记录 `visual_classification_guard`；真正的负向需求用例仍可正常校准。未修改 Figma 解析、图片选择、批次或软参考策略。
- 最终收敛增加单调门禁：已有 executable 不允许在覆盖收敛中降级；只有不丢失任何已覆盖 acceptance check 且确实减少缺口的组合才应用。拟议组合、应用决定和退化 check ID 全部进入审计，不能用另一个分支的通过覆盖原通过。
- 对需求明确的来源页可见 / 文案 / 同级检查，平台只在“同分支 verified execution 基线含真实 action 导航 + 上游 AI 自动候选保留原 REQ 边界 + 无深层外部动作”同时成立时构造 `source_ui_assertion` 证据。基线动作在相册导入、选文件、授权、打印等数据动作前截断，需求原文定义 Runner 需要验证的断言；GPT 仍先决策，过度保守或 45 秒超时时才使用该可审计证据，后续 scorer、static、dry-run、Smoke 和真实 Runner 门禁全部保留。
- manual 分类现在会清除旧 `smoke=true / 冒烟` 标记，避免人工项仍显示为冒烟。

线上产物重放与检查：

- 使用本轮初始组合重放，保留原 3 条 Smoke，并得到三条证据：文档可达 `TC-002 / bounded_landing`、照片展示 `TC-007 / source_ui_assertion`、扫描展示 `TC-008 / source_ui_assertion`。最终为 6 条 executable、`12/12` acceptance checks、`missing=[]`；照片路径复用 `照片打印 icon -> 照片打印 -> 6寸照片`，扫描路径复用 `扫描复印 icon -> 文件扫描`，均不使用坐标。
- 使用已损耗的最终失败态调用真实 GPT：新契约已让模型把额外可达用例放入 remaining；带来源页证据的一轮在 45 秒超时，平台没有重试，并按证据从 `5/12` 提升到 `11/12`。该重放缺扫描可达是因为最终失败态已将其降级；按真实初始态重放时保留该已批准用例并闭合 `12/12`。
- 照片 / 扫描来源页证据实际转换为 Midscene YAML 后，结构、动作白名单、mock dry-run、稳定性和 scorer 全部通过，分别为 `87 / 88 executable`；断言规范后只生成一个最终 `aiWaitFor`。未修改 scorer。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name、后端 `61` 项、前端 `69` 项、AI Gateway `46` 项、AI Skill contract fixtures `3/3`，以及 Playwright 桌面 / 移动端视觉回归全部通过。

待完成：

- 提交并部署本轮修复。
- 部署后再次使用同一需求 / Figma、固定 `win-runner-01 / ecbfd645` 和 `highway_gpt5_mini / gpt-5-mini` 跑完整 Agent。必须人工复核最终 YAML 的三个分支、真实可见文字、Smoke / remaining 分批，并持续监督所有 OPPO Runner job、报告、截图 / 录屏和可能的一次 AI 修复到终态。

### 2026-07-16 GPT Agent 回归：恢复 PLAN 到 Top3 基线的业务分支身份

部署 `f7c5a6c` 后发起同一完整回归：

- Agent `agent-1784172504590-4322d988`，目标 / 需求 / Figma / App 与前轮一致，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed`，文本模型为 `highway_gpt5_mini / gpt-5-mini`。8091 / 8088、AI Gateway、Sonic 健康；Windows Runner 心跳约 3 秒并上报 `yaml_dry_run=true / qwen3.6`，固定 OPPO PHM110 在线、App `4.45.0` ready。本轮在 `GENERATE_YAML` 阶段失败，没有创建 Runner job，也没有向华为或第二台设备下发。
- PREPARE_SOURCE 正确解析 Figma `4 页 / 4 图 / 忽略 0`。PLAN 一次通过，明确记录 `providerId=highway_gpt5_mini / model=gpt-5-mini / fallback=false`。图片视觉仍由 Qwen VL 完成，4 批分别约 `19 / 23 / 19 / 24s`，结果 `4/4 completed / retry=false / hardGate=false`；第 2、3 批正确识别 5 寸 / 一寸照属于照片打印及“相机拍照 / 百度网盘”的垂直同级关系。
- `f7c5a6c` 的模型贯穿已在线生效：YAML 阶段 `baseline_reranker` 和 `execution_scope_planner` trace 均明确为 `highway_gpt5_mini / gpt-5-mini`，不再为空。GPT 不是因为模型选择丢失而失败。
- 最终 8 条自动候选全部转为 manual，产物为 `0 executable / 14 manual / 0/12 acceptance checks`。GPT 的统一理由是 `selectedBaselines` 为空，无法提供硬规则要求的可信 `baselineId`；覆盖门禁正确阻断，没有为了 5 条目标硬凑。

根因与通用修复：

- `scenario_designer` schema 中 `feature` 表示功能域。本轮 AI 合理地给 8 条具体场景统一输出 `feature="打印-百度网盘入口"`，同时在 `requirement_point / scenario / business_path` 中分别明确文档打印、照片打印、扫描复印。旧 `_agent_business_plan_from_mindmap` 却优先把 `feature` 当具体 `branch`，导致 PLAN 的 8 条 flow 全部丢失真实分支身份。
- YAML 阶段因此只构造一个错误 required branch：`FLOW-001 / 打印-百度网盘入口`，锚点退化为“百度网盘”；20 个成功候选对该分支的 eligible 数为 0。GPT 选出的 3 条候选被平台以 `invalid_branch_count=3` 全部拒绝，generation reranker trace 为 `selected_count=0 / unavailable_required_branch_ids=[FLOW-001]`。页面中仍能看到 PLAN 阶段旧参考基线，但 executable planner 的真实 `selectedBaselines` 为空，造成展示与执行上下文不一致。
- 计划归一现使用原始需求契约恢复分支：按 flow 名称、需求引用、步骤、检查点依次匹配，只在唯一命中文档 / 照片 / 扫描之一时写入 `branch` 并记录 `branchSource=source_requirement_contract`；同时命中多个业务分支的全局 / 一致性场景保持 AI 原值，不强行归类。
- 没有写入百度网盘、基础打印或三个固定产品分支。算法复用任意需求契约的 `businessFlows`，只修正“功能域标签覆盖具体来源分支”的通用数据问题；现有 Top3 仍必须满足成功基线、真实可见导航锚点、分支 eligibility 和精确 baselineId。

线上产物离线重放：

- 修复前同一 PLAN 只产生 `FLOW-001 / 打印-百度网盘入口 / anchors=[百度网盘, 百度]` 一条 required branch。
- 修复后同一 8 条 GPT flow 归一为文档 3 条、照片 3 条、扫描 2 条，Top3 required branches 为 `FLOW-001 文档打印 / FLOW-004 照片打印 / FLOW-007 扫描复印`，anchors 分别为 `[文档打印, 文档] / [照片打印, 照片] / [扫描复印, 扫描]`。
- 新测试使用与线上一致的“所有 scenario.feature 都是泛化功能域、scenario 名称和 requirement_point 保留具体分支”夹具，并验证跨文档 / 照片场景同时命中两个分支时返回空匹配，不能被平台擅自归到某一分支。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name、后端 `61` 项、前端 `69` 项、AI Gateway `46` 项、AI Skill contract fixtures `3/3`，以及 Playwright 桌面 / 移动端视觉回归全部通过。

待完成：

- 提交并部署本轮分支身份修复。
- 部署后再次使用同一需求 / Figma、固定 `win-runner-01 / ecbfd645` 和 `highway_gpt5_mini / gpt-5-mini` 跑完整 Agent。必须确认 YAML reranker 的 `required_branch_count=3`、三个分支各有 eligible 成功基线、`selectedBaselines` 非空，再人工审计 YAML 并监督 OPPO smoke / remaining / 一次有界修复到终态。

### 2026-07-16 GPT Agent 回归：修复 YAML 阶段模型选择丢失

部署 `af2653c` 后发起同一完整回归：

- Agent `agent-1784170705253-b23f6186`，目标 / 需求 / Figma / App 与前轮一致，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed`；文本模型显式选择 `highway_gpt5_mini / gpt-5-mini`。8091 / 8088、AI Gateway、Sonic 健康，Windows Runner 在线并上报 `qwen3.6`，固定 OPPO PHM110 在线且 App `4.45.0` ready。本轮在 `GENERATE_YAML` 阶段失败，没有创建 Runner job，也没有向华为或第二台设备下发任务。
- PREPARE_SOURCE 正确解析 Figma `4 页 / 4 图 / 忽略 0`。最终 PLAN 产物明确记录 `providerId=highway_gpt5_mini / model=gpt-5-mini / fallbackUsed=false`，生成 8 条业务分支并通过计划门禁；PLAN 的可信基线重排同样记录 `used_local_fallback=false`。这证明 GPT 实际承担了文本计划，不只是前端选择字段。
- 图片视觉仍使用现有 `qwen3.6-plus` VL，不伪称 GPT 处理图片。最终 4 个单图批次分别约 `27 / 35 / 29 / 18s` 完成，均有非空 judgement，结果为 `4/4 completed / retry=false / hardGate=false`。第 2、3 批正确识别 5 寸 / 一寸照属于照片打印及底部“相册导入 / 相机拍照 / 百度网盘”垂直关系；局部上半页没有入口时只记录冲突，没有覆盖前序正向证据。
- 最终生成组合只有 `TC-001 / TC-002 / TC-007` 三条 executable，11 条归入 manual，覆盖显式 12 个验收维度中的 8 个。缺文档 / 照片 reachability、扫描复印 relation / reachability；覆盖门禁正确阻断。数量目标 5 只产生 advisory，没有为了数量硬凑；失败不是 Figma、视觉门禁、scorer、Runner 或设备问题。

根因与通用修复：

- Agent PLAN 请求会传 `modelProviderId / aiModel`，但 `_agent_generate_yaml_from_ui_pipeline` 构造共享 YAML 请求时漏掉了这些字段。线上失败产物中 PLAN 明确为 GPT，而 YAML 阶段 `baseline_reranker / execution_scope_planner / executable_yaml_planner / executable_yaml_convergence` 的 trace 全部 `providerId="" / model=""`，因此网关使用默认模型完成生成与收敛；用户选择的 GPT 只在 PLAN 生效，没有贯穿完整 Agent。
- 共享 YAML 请求现同时传递 `modelProviderId / aiProviderId / aiModel / model`，其中 `model` 使用真实模型名而不是 `provider:...` 选择令牌。该修复适用于所有 Agent provider，不包含百度网盘、照片打印或单需求硬编码。
- 新运行级检查截获 `_agent_generate_yaml_from_ui_pipeline` 发给 `generate_ui_yaml_from_request` 的真实请求，验证 `highway_gpt5_mini / gpt-5-mini` 与 `win-runner-01 / ecbfd645 / fixed / singleDeviceOnly=true` 同时保留。没有修改覆盖门禁、scorer、Figma parser、视觉软参考、Runner 脚本、执行模式或历史 YAML。
- 异常处理依据仍采用成熟方案的边界：BrowserStack self-heal 只在存在成功历史时修复定位漂移并保留修复原因，[Test Failure Analysis](https://www.browserstack.com/docs/test-reporting-and-analytics/agents/test-failure-analysis?fw-lang=nodejs) 用日志 / 截图 / 元数据给出证据化根因；[Maestro waits](https://docs.maestro.dev/maestro-flows/flow-control-and-logic/wait-commands) 用条件等待代替固定 sleep；[Mobile-Agent-v2](https://arxiv.org/abs/2406.01014) 用一次结果反思纠正无效动作。平台继续执行“稳定等待、成功基线路径、证据反思、仅失败项一次有界修复”，不把产品断言失败用重跑隐藏。

已验证：

```bash
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name、后端 `61` 项、前端 `69` 项、AI Gateway `46` 项、AI Skill contract fixtures `3/3`，以及 Playwright 桌面 / 移动端视觉回归全部通过。

待完成：

- 提交并部署本轮模型贯穿修复。
- 部署后使用同一需求 / Figma、固定 `win-runner-01 / ecbfd645` 和 `highway_gpt5_mini / gpt-5-mini` 重跑完整 Agent。必须核对 YAML 阶段各 AI trace 不再为空且均指向 GPT，再监督 smoke、remaining 和可能的一次 AI 修复到终态；图片视觉仍应为 4/4 Qwen VL 软参考。

### 2026-07-16 部署后回归：有界终态语言归一与 GPT 下一轮准备

部署 `545f132` 后发起同一完整回归：

- Agent `agent-1784168045164-98ea26f8`，输入仍为“基础打印新增百度网盘入口”、同一需求 / Figma，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`。8091 / 8088、AI Gateway、Sonic 健康；Windows Runner 在线并上报 `qwen3.6`，固定 OPPO PHM110 在线且 App `4.45.0` ready。本轮在 `GENERATE_YAML` 阶段失败，没有创建任何 Runner job，也没有向华为或第二台设备下发任务。
- PREPARE_SOURCE 正确解析 Figma `4 页 / 4 图 / 忽略 0`。4 张图按 4 个独立批次真实送入视觉 AI，分别约 `13 / 18 / 16 / 18s` 完成，`4/4 completed / retry=false / hardGate=false`。视觉判断正确区分首页、5 寸照片导入页、一寸照引导页与一寸照编辑页，并明确局部页面缺失不能否定其他页面；Figma 继续是软参考，解析逻辑未改。
- AI PLAN 生成 8 条业务流 / 12 个场景 / 8 条自动候选。最终基线选择已正确覆盖三个分支：文档 `1b4e6a94768902d3`、照片 `d8931c2dd082926e`（6 寸照片成功路径）、扫描 `d623c1e73180bfac`，均为可信成功导航证据；`545f132` 的分支动作校验已在线生效。
- 初始 3 条 executable 分别覆盖文档、照片、扫描的展示 / 同级 / 文案，共覆盖 `9/12`；`TC-004 / TC-005 / TC-006` 已由上游 AI 生成为点击入口后只观察首个可见状态的自动候选，但最终收敛模型把三条全部降为 manual，任务正确在覆盖门禁阻断。最终错误只缺三条 reachability，不是数量 5 门槛、视觉门禁、scorer、Runner 或设备失败。

失败根因与通用修复：

- 线上候选在“等待首个可见页”后追加“断言当前页已离开来源页且未出现崩溃或白屏”。旧有界提取器只接受等待 / 观察 / 检查 / 验证，不接受语义等价的校验 / 断言；稳定性门禁也不识别“未出现崩溃或白屏”，导致三条 `convergenceEvidence` 全为空。现统一归一这些终态观察动词和否定失败表达，仍拒绝观察之后的账号、验证码、确认授权、文件选择及其他交互。
- 新端点允许以“点击目标的真实可见品牌文字 + 明确已离开来源页 + 一个授权 / 登录 / 内容页等可观察备选 + 无白屏 / 崩溃”作为 Runner 可验证终态；抽象“跳转成功 / 页面正常”仍不能覆盖 reachability。
- 有界组合优先从本轮 AI 已选中且可信的同分支基线提取真实 `aiTap / aiWaitFor / aiScroll` 导航前缀，并在导入、上传、文件选择、打印、授权等数据动作前截断，再拼接上游 AI 的目标点击尾链。没有写入百度网盘、5 寸或单需求特判。
- 增加跨叶子一致性保护：来源导航含“点击 A 或 B / 等待 A 或 B”时不允许 Runner 猜路径；成功基线走到 6 寸照片而 AI 尾链声称离开一寸照时，不得为了覆盖率强行组合，必须由 AI 选择一个具体叶子并重写。规划提示同步要求保留基线父层级、按需求 / Figma 适配当前真实叶子。

线上失败产物离线重放：

- 原样重放时只得到 `TC-004 / TC-006` 两条一致证据，最终 `11/12`，照片 reachability 继续被门禁阻断，证明平台没有把红色改成绿色。
- 将照片候选模拟为 AI 收敛后的单一路径“照片打印 icon -> 照片打印 -> 6 寸照片 -> 百度网盘 -> 首个可见页”后，三条 remaining 均获得证据，最终 `12/12 / ok=true`；三条 `_case_manual_block_reason` 均为空。加入深层授权 / 凭据 / 文件操作的既有测试仍被硬阻断。
- 线上 AI Gateway 的 `highway_gpt5_mini / gpt-5-mini` provider 已配置并通过 `/ai/providers/test`，返回 `gateway ok`。下一次 Agent 将显式指定该 provider 运行文本 MM skills、规划和收敛；当前图片视觉 Skill 仍走已验证的 `qwen3.6-plus` VL，不伪称 GPT 已承担未验证的图片接口。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name、后端 `61` 项、前端 `69` 项、AI Gateway `46` 项、AI Skill contract fixtures `3/3`，以及 Playwright 桌面 / 移动端视觉回归全部通过。

待完成：

- 提交并部署本轮修复。
- 部署后用同一需求 / Figma、固定 `win-runner-01 / ecbfd645`，显式选择 `highway_gpt5_mini / gpt-5-mini` 再跑完整 Agent。必须持续监督 Agent、smoke、remaining 与可能的 AI 修复到终态，并人工复核最终 YAML、真实 Runner 报告、截图 / 录屏、失败分类和单设备约束。

### 2026-07-16 完整 Agent Runner 回归与 AI 路径修复证据闭环

部署 `38f1e71` 后发起同一完整回归：

- Agent `agent-1784162808009-0ef740e0`，目标仍为“基础打印新增百度网盘入口”，使用同一需求 / Figma，`scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`。线上 `8091 / 8088`、AI Gateway 与 Sonic 健康；Windows Runner 在线并上报 `qwen3.6`，OPPO PHM110 `ecbfd645` 在线且 App `4.45.0` ready。所有正式和修复 job 都只绑定该 OPPO，没有选择或下发第二台设备。
- 路由为 `new_requirement_source / generate_draft`。Figma parser 保持原实现，正确解析 `4 页 / 4 图 / 忽略 0`；4 张原图分 4 批真实送入 `qwen3.6-plus`，分别约 `19 / 26 / 19 / 21s` 完成，每批都有 judgement，结果为 `4/4 completed / retry=false / hardGate=false`。第 2 批明确识别“5寸照片”属于照片打印，并识别相册导入、相机拍照、微信导入、百度网盘的同级关系；视觉资料继续是软参考。
- PLAN 产出 8 条 AI 业务流、12 个场景、8 条用例。最终确认 6 份 executable YAML：文档 / 照片 / 扫描三条展示校验评分均为 `100`，三条点击可达校验约为 `89 / 89 / 88`；全部通过 static / dry-run，使用真实可见文字，无坐标动作。
- 首批真实 Runner 为 `job_1784163194371_00004` 文档展示成功、`job_1784163327947_00005` 照片展示失败、`job_1784163481978_00006` 扫描展示失败，即 `1 通过 / 2 失败 = 33.3%`。文档成功报告截图真实显示 `本地文档 / 百度网盘 / QQ文档 / WPS文档`；照片失败截图停在包含“照片打印 / 智能证件照”等卡片的父页面；扫描失败截图到达“小白扫描王”，横向入口只完整显示 `本地导入 / 相册导入 / 微信导入`，右侧目标入口被裁切，需要可见文字驱动的横向滚动。
- 旧扩展策略只把 dry-run / 定位 / 超时视作 blocker，没有执行 `AGENTS.md` 的首批通过率 `>=50%` 规则，因此错误下发 remaining。后续 `job_1784163633641_00010` 文档可达成功，报告到达百度文件列表并显示 `百度文档测试.doc / 去打印`；`job_1784163834081_00011` 照片可达失败，仍停在父页面；`job_1784163983367_00012` 扫描可达失败，仍受横向入口裁切影响。初始六条真实结果为 `2 通过 / 4 失败`，不能被 Agent 总失败覆盖。
- AI 首轮修复生成 4 条草稿：两条照片草稿可校验但没有真实补齐父子导航，`job_1784164240261_00017 / job_1784164344374_00018` 均失败；两条扫描草稿输出对象形式 `aiScroll`，被 Gateway / Task Server 正确拒绝，未下发 Runner。第二次有界修复只补了“5寸照片”点击，仍漏掉中间的第二次“照片打印”点击；`job_1784164513966_00021` 最终失败。修复真实结果为 `0 通过 / 3 失败`，Agent 终态为 `FAILED / RERUN`，不是视觉或 Figma 门禁失败。

根因与本轮通用修复：

- 冒烟继续条件恢复为确定性的 `>=50%`：每条 Runner 原始 passed / failed 继续保留；另行记录 `smokeExecutable` 和 `smokePassThresholdMet`。`0/1` 产品断言失败表示脚本真实执行但未达到扩展门槛，`1/2` 恰好 50% 可以继续；脚本 / YAML / 定位 / 超时硬阻断仍优先停止。Agent 总状态与任务真实统计保持双状态。
- 生成时已召回三个分支，但 AI 把一条元数据写着“文档/照片”、实际动作只点击“文档打印”的候选分给照片，又拒绝 `6寸照片打印.yaml`，理由是长链路。候选分支资格现在优先检查 `snippet` 中真实 `aiTap / ai / aiAction / aiAct` 可见文字；宽泛标题和 `businessPath` 不能覆盖实际走向。可信长链路可以作为 `navigation_path`，但只复用到目标页之前的父子导航前缀，不复制选图、授权、支付或打印尾链。
- 修复检索把 AI 规划的当前业务分支变成带兄弟分支排他锚点的查询。照片失败优先获得相邻 `6寸照片打印.yaml` 的 `照片打印 icon -> 照片打印 -> 6寸照片` 层级；扫描失败优先获得文件 / 证件扫描路径，不再被共同的“百度网盘 / 入口校验”关键词挤成文档 TopN。
- AI 修复候选新增统一语义证据门禁：`analysis / changes` 声称新增点击或修正导航时，实际 YAML 的 `aiTap / ai / aiAction / aiAct` signature 必须变化；导航变化必须引用当前 `retrievalRoles=business_branch` 的可信路径基线，全局或兄弟分支引用不能授权。候选已返回但被该门禁或 YAML 契约拒绝时，最多把具体问题反馈给现有修复模型一次；合格候选不增加调用，网络失败不盲重试，第二次仍不合格则只保存 `REJECTED` 诊断草稿并禁止 Runner。
- AI Gateway 和 repair skill 的 `aiScroll` 提示统一为当前 validator 接受的非空自然语言字符串，禁止模型继续生成 `direction / distance / scrollType` 对象。平台 validator / scorer 未放宽。
- 未修改 Figma parser、图片选择 / 计数 / 软参考策略、`router.py`、执行模式、历史 YAML、scorer、Runner 脚本或设备策略；用户 dirty 文件未暂存、未回滚。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_execution_plan.py task_server/services/ai_skill_service.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name、后端 `61` 项、前端 `69` 项、AI Gateway `46` 项、AI skill contract fixtures `3/3`，以及 Playwright 桌面 / 移动端 Agent、失败报告和重跑视觉烟测全部通过。回归覆盖 `0% / 50%` 冒烟边界、元数据与真实动作冲突、照片 / 扫描分支基线检索、跨分支引用拒绝、修复说明与 YAML diff 不一致的一次有界 AI 自纠，以及对象形式 `aiScroll` 拒绝。

待完成：

- 本轮修复尚未部署。部署后必须再次使用同一需求 / Figma 和固定 `win-runner-01 / ecbfd645 / qwen3.6-plus` 发起完整 Agent，持续轮询到 `DONE / FAILED / CANCELLED`。
- 新回归仍需人工复核视觉 `4/4` judgement、6 份以内 YAML 的三个业务分支 / 文案 / 同级 / 可达页 / 无坐标，以及首批通过率门禁。若进入修复，核对 AI 是否真实采用同分支基线前缀、`changes` 与 YAML diff 一致，并逐个检查只在 OPPO 上执行的 Runner 报告、截图 / 录屏和终态。

### 2026-07-15 Figma 多 Frame 负向软证据作用域保护

部署 `f344dd4` 后发起同一完整回归：

- Agent `agent-1784114477002-86b168da`，参数仍为“基础打印新增百度网盘入口”、同一 Figma、`scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`。`8088` 健康，Windows Runner 在线并上报 `qwen3.6` 模型族；固定 OPPO PHM110 在线且 App 预检 ready。华为上同时存在用户允许继续的“十二生肖印章打印”，本 Agent 没有选择或下发华为。
- PREPARE_SOURCE 正确解析 Figma `4 页 / 4 图 / 忽略 0`。PLAN 复用这 4 张原图并逐张送入 `qwen3.6-plus`，4 个批次分别约 `15 / 15 / 20 / 17s` 完成，均有独立 judgement，结果为 `4/4 completed / 4 attempted / retry=false / hardGate=false`。
- AI 生成 8 条业务分支，明确覆盖文档打印、照片打印、扫描复印的展示、同级关系、文案和点击可达；第 2 批正确识别“5寸照片”属于照片打印，并识别相册导入、相机拍照、微信导入、百度网盘入口。路由仍为 `new_requirement_source / generate_draft`。
- 终态为 `FAILED / GENERATE_YAML`，没有创建 Runner job。最终组合已有 4 条 executable，覆盖 12 个显式验收维度中的 11 个；`f344dd4` 的有界证据正确补齐扫描复印 4 个维度，唯一缺口是 `REQ-002 [relation]` 照片打印同级关系。

根因与本轮通用修复：

- 第 2 张“5寸照片导入页”提供了明确正向证据；第 4 张是“一寸照参数配置页”，当前可视区域没有导入入口。旧视觉增量按 case ID 逐批覆盖字段，导致后一个局部页把 `TC-002` 从“照片打印页百度网盘位于相机拍照下方”改成“当前参数页无文件导入入口”。这违反 Figma 软参考和页面状态作用域，不是需求解析、基线召回、scorer 或 Runner 失败。
- `visual_grounder` 提示现明确：每批只证明当前 Frame / 页面 / 状态；局部缺失不能否定另一页的正向证据；无法证明同页时返回空增量，只在 judgement / repair hints 记录冲突。
- 增量合并增加通用语义反转保护：正向需求用例不能被软参考改写成“入口不存在”。AI 的 `repair_hints`、批次 judgement 和冲突审计继续保留；原本就验证“入口应隐藏”的真正负向用例仍允许正常校准。
- 作用域只在视觉增量合并处，不改 Figma parser、需求契约、场景生成、最终覆盖门禁、YAML scorer、Runner 调度或设备策略，也没有增加 AI 调用和执行步骤。

使用本次线上 `TC-002` 产物离线重放：

- 后一张参数页的负向 patch 被记录并阻止反转，原断言“照片打印页面底部展示百度网盘、位于相机拍照下方、文案和布局正确”得到保留，AI 冲突提示仍存在。
- 把该受保护候选放回线上最终组合后得到 5 条 executable，覆盖 `12/12` 个验收维度，`missing=[]`；没有通过数量下限硬凑用例。
- 通用“发票入口”夹具同时验证：无关参数页不能覆盖正向入口用例，而真实的入口隐藏负向用例仍可被视觉 AI 修改。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name、后端 `61` 项、前端 `69` 项、AI Gateway `46` 项、AI skill contract fixtures `3/3`，以及 Playwright 桌面 / 移动端 Agent、失败报告和重跑视觉烟测全部通过。

待完成：

- 提交并部署本轮修复；部署后再次使用同一需求 / Figma / `win-runner-01 / ecbfd645 / fixed / qwen3.6-plus` 发起完整 Agent。
- 必须持续轮询到 Agent、smoke、remaining 与可能的 AI 修复全部终态，人工复核最终 YAML、真实 Runner 报告、截图 / 录屏和失败归因；本轮线上 Agent 尚未进入 Runner，不能称为完整成功。

### 2026-07-15 Agent 同分支 AI 首屏证据组合与收敛超时降级

部署 `cf85317` 后发起同一完整回归：

- Agent `agent-1784110642603-d250d9c2`，参数仍为“基础打印新增百度网盘入口”、同一 Figma、`scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`。线上 `8091 / 8088`、AI Gateway 与 Sonic 健康，Windows Runner 在线并上报 `qwen3.6` 模型族；本 Agent 固定 OPPO PHM110，没有创建 Runner job，也没有向华为或第二台设备下发。
- Figma parser 保持原实现，正确解析 4 页 / 4 张原图 / 忽略 0 页。4 个单图批次全部真实送入 `qwen3.6-plus`，分别约 `20 / 16 / 15 / 16s`完成，每批均有独立非空 judgement，结果为 `4/4 completed / 4 attempted / retry=false / hardGate=false`。第 2 批明确识别“5寸照片”属于照片打印，并识别“相册导入 / 微信导入 / 相机拍照 / 百度网盘”同级入口；第 4 批正确指出一寸照拍摄页不是文件导入页。
- Top3 基线重排已正确选中三条分支多样的历史成功路径：文档 `dec99a59a1c46ae8 / FLOW-001`、照片 `c582cd168dd13dcc / FLOW-002`、扫描 `d623c1e73180bfac / FLOW-003`，均为 `verified_execution / execution_success`。因此本轮不是 Figma、视觉模型或基线召回失败。
- 任务终态为 `FAILED / GENERATE_YAML`。首轮只确认 `TC-001 / TC-002 / TC-007` 三条 executable，覆盖 12 个显式验收维度中的 7 个；缺照片可达性和扫描复印的展示 / 同级 / 文案 / 可达性，`TC-004 / 005 / 006` 仍为 needs_review，门禁正确阻断。

根因与本轮通用修复：

- 照片可达候选使用“授权页 / 文件页 / WebView”表达合法首个终态，旧安全检查只识别“文件列表 / H5 / 网页”等字面词，又不把 `/` 视为多终态枚举，因此误拒语义等价的 AI 候选。现按“授权 / 登录 / WebView-H5-网页 / 文件页-列表-选择页 / 空态-提示 / 弹窗”语义组归一，仍要求至少两类合法终态、明确枚举以及无白屏 / 崩溃断言。
- 旧有界证据只能处理“自动候选自身已有点击尾链”。扫描分支实际已有上游 AI 生成的自动展示候选 `TC-003`、同分支人工候选 `MC-003` 中的“点击 -> 观察首个终态”以及成功扫描基线，但平台没有给它们组合的机会。现在只对 `portfolioAudit` 真实缺失的同一 REQ 进行组合：成功基线负责来源页导航，自动候选负责当前分支的真实文字检查，人工候选只捐赠有界首屏尾链。账号、验证码、确认授权、选文件、坐标和非观察动作仍直接拒绝。
- 证据记录 `sourceCaseId / tailSourceCaseId / baselineId / acceptanceCheckIds`，并与现有一次最终 AI 收敛调用一起发送。模型仍先决策；模型过度保守时，只有来源、安全性和显式覆盖全部通过的原自动候选可被平台接管；原人工候选本身仍保留 manual。
- 本机用线上原始 payload 真实调用同一 `qwen3.6-plus` 收敛模型，本次在旧 `75s` 预算内仍超时；线上上一次成功收敛约用 `33s`。现对已有 `eligible` 证据的最终收敛给 AI `45s` 决策窗口，初始规划和无证据收敛仍保留原 `75s`；不新增模型重试。仅当 `coverage_convergence` 调用不可用且已存在 `eligible=true` 证据时，使用上游 AI 候选的证据降级，并在 trace / report 明确记录 `evidenceFallback=true`，不伪称本轮模型成功。无可信证据时仍按原逻辑失败。
- 有界组合用例标题改为“点击后首个可见页校验”，准确区分“当前页展示检查”和“点击后可达性检查”；这使现有 scorer 能按真实业务意图评分，没有修改或降低 scorer。

使用线上原始 JSON 离线重放：

- 正常 AI 过度保守模拟和 AI 超时模拟都得到 5 条 executable：`TC-001 / TC-002 / TC-007 / TC-003 / TC-008`，覆盖文档打印、照片打印、扫描复印的 `12/12` 个展示 / 同级 / 文案 / 可达验收维度。`TC-004 / 005 / 006` 在完整覆盖后作为重复候选转 manual，不为 5 条数量目标硬凑。
- 5 份 YAML 经现有静态修复、动作白名单、Midscene 结构、可执行语法、启动守卫、需求 scope 和 scorer 检查，全部为 `100 / executable`，无坐标动作。扫描 YAML 使用真实可见文字进入“扫描复印”，先等待“百度网盘”与同级入口，再点击并检查授权 WebView / 登录页 / 文件选择页之一，不输入凭据、不确认授权、不选文件。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/ai_skill_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name、后端 `61` 项、前端 `69` 项、AI Gateway `46` 项、AI skill contract fixtures `3/3`，以及 Playwright 桌面 / 移动端 Agent、失败报告和重跑视觉烟测全部通过。用线上 payload 的真实本机收敛调用确认 `qwen3.6-plus` 超时事实，模拟超时重放确认证据降级无第二次模型调用。

待完成：

- 本轮新修复尚未部署。部署后必须继续使用同一需求 / Figma / `win-runner-01 / ecbfd645 / fixed / qwen3.6-plus` 发起完整 Agent，持续轮询到 `DONE / FAILED / CANCELLED`。
- 新任务要再确认视觉 `4/4 completed` 且每批有 judgement，核对最终 YAML 的三个分支、5 寸照片归属照片打印、真实文字定位和无坐标。如进入 Runner，首批与 remaining 每个 job 必须只在 OPPO 串行到终态，逐个检查真实报告、截图 / 失败录屏、失败分类和 AI 修复证据。

### 2026-07-15 Agent 收敛来源证据与 Figma 视觉增量校准修复

部署 `4a911c9` 后发起同一完整回归：

- Agent `agent-1784104032479-b3584431`，参数仍为“基础打印新增百度网盘入口”、同一 Figma、`scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`。线上 `8091 / 8088`、AI Gateway、Sonic 健康，Windows Runner 在线且只选择 OPPO PHM110 `ecbfd645`。
- 路由为 `new_requirement_source / generate_draft`。Figma parser 正确解析 4 页 / 4 张原图、忽略 0 页；4 个单图批次均真实送入 `qwen3.6-plus`，但旧 `visual_grounder` 每批要求模型重写完整场景/用例 JSON，4 批均在约 90 秒超时，结果为 `0/4 completed / 4 attempted / failed / hardGate=false`。
- 任务终态为 `FAILED / GENERATE_YAML`，没有创建 Runner job，也没有向第二台手机下发。门禁报告缺文档/照片可达性和扫描同级关系/可达性，并有 1 条自动候选未终结。
- 首轮 planner 已把自动生成的 `TC-003/004/005/006` 降入 `manual_cases`。旧二次收敛按当前容器推断来源，把这些候选误当成人工原生项，因此 `4a911c9` 的有界端点证据无法接管。
- `TC-007` 声称覆盖文档、照片、扫描三个 REQ，但步骤只进入文档和照片；旧验收匹配只看 REQ 引用和“三个页面”文案，误报扫描展示/文案覆盖。扫描分支还没有首轮 executable 来源 case，但 Top3 中已有 AI 选择且历史执行成功的 `证件扫描` 同分支基线。
- 收敛模型漏回冗余 `TC-008`，旧安全策略仍把它留为 `needs_review`，即使其他路径随后完整覆盖，也会继续阻断。

本轮通用修复：

- 每条候选持久保存 `originExecutionLevel`，二次收敛不再用 `cases/manual_cases` 容器覆盖 AI 原始自动化来源。多 REQ case 必须在真实步骤/断言中逐个出现对应业务分支，否则初轮降为 `needs_review`、终轮降为 manual，不能用跨页总结文案冒充执行证据。
- 有界收敛优先组合“同 REQ 已验证来源页路径 + 上游 AI 生成的点击后首个稳定终态”。当某分支还没有 executable 来源 case 时，可使用 AI Top3 中明确绑定该分支、`verified_execution / execution_success` 的基线补来源导航，再补齐同页展示/同级/文案检查；只允许到授权页、登录页、文件列表或弹窗等首个可见终态，账号、验证码、确认授权、选文件等深层动作仍由原门禁阻断。
- 最终 AI 漏回的自动候选只有在其他 executable 已覆盖全部显式验收维度时才转 manual；绝不自动升级，也不因冗余项继续阻断。`3/5/8` 仍只是规划目标，不为数量凑弱网、系统设置或重复链路。
- `visual_grounder` 改为视觉增量协议：每张图只返回按 case/scenario ID 关联的 UI 文案、入口、同级关系和可见终态修正，平台合并回完整规划，不再让模型复述整包。线上同一失败 payload 的视觉结构文本由 `57,195` 字符压至 `8,564` 字符，保留全部需求点、12 个场景索引、4 条自动用例步骤/断言、10 条人工项索引和原图；响应上限为 2048 tokens。
- 每个 90 秒视觉批次最多进行一次同预算有界重试，不增加总预算；首轮最多 45 秒，失败后把剩余批次预算交给第二次调用。模型原始响应必须为当前图片返回非空 `review.visual_grounding_check`，否则直接失败，不能从上一批 review 继承文案后误计 completed。逐批产物新增 `attemptCount / retryUsed / judgement`；解析成功、已发送、部分成功和模型失败继续分开统计。Figma parser、选页、原图、4 图来源计数和软参考策略均未修改。
- 基线产物继续保存稳定 `id`、分支 ID/名称，并兼容 snake_case / camelCase，确保失败恢复和离线回放可以重新绑定同一成功基线。

使用线上保存的原始 12 场景、8 条自动候选、6 条人工候选和 Top3 分支基线重放：

- 最终 executable 为 `TC-001 / TC-002 / TC-004 / TC-005 / TC-006`，覆盖文档打印、照片打印、扫描复印的展示、同级关系、文案和点击可达共 `12/12` 个验收维度；`TC-007` 因缺扫描步骤、`TC-008` 因冗余均保留 manual。
- 五份 YAML 经现有静态修复和 scorer 后分别为 `100 / 100 / 89 / 89 / 87`，全部为 executable、无坐标动作。扫描链路使用真实文字进入“扫描复印”，先检查“百度网盘”入口/同级关系/文案，再点击并等待首个合法落地页；没有输入账号、确认授权或选择文件。
- 该结果是同一线上产物经过新通用逻辑的离线重放，不代表线上模型和 Runner 已成功；必须以部署后的新 Agent、4/4 视觉 judgement 和真实 Runner 报告为最终结论。

设计依据不是照搬框架：Google [AndroidWorld](https://google-research.github.io/android_world/) 强调可复现初始化、系统状态成功判定和清理；[Mobile-Agent](https://arxiv.org/abs/2401.16158) 先把截图转为视觉/文本感知，再基于感知结果规划动作；[Mobile-Agent-v2](https://proceedings.neurips.cc/paper_files/paper/2024/file/0520537ba799d375b8ff5523295c337a-Paper-Conference.pdf) 将规划、决策、反思分开以减少长文本和图像历史的干扰。本平台据此采用“视觉 AI 返回小型可审计增量、规划持有完整需求和基线状态、平台执行确定性安全/覆盖门禁”，而不是让一次多模态调用重写全部事实或直接决定通过。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
MIDSCENE_AI_SKILLS_USE_GATEWAY=0 DASHSCOPE_API_KEY= OPENAI_API_KEY= MIDSCENE_API_KEY= FALLBACK_DASHSCOPE_API_KEY= python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name、后端 `61` 项、前端 `69` 项、AI Gateway `46` 项、AI skill contract fixtures `3/3`，以及 Playwright 桌面/移动端 Agent、失败报告和重跑视觉烟测全部通过。本机通过同一 DashScope 通道强制 `qwen3.6-plus`、按新增量协议发送一张真实平台截图，`18.5s` 返回非空 judgement 和定向 case 增量；该探针只证明协议可完成，不替代部署后的 4 张 Figma 线上验收。`tests/test_sonic_integration.py -k 'visual_grounder or refine_cases_falls'` 的 2 条旧测试仍从已拆空的 `midscene-upload.py` 兼容壳取迁移后函数，均为既有 `AttributeError`，与本轮行为无关。

待完成：

- 本轮提交尚未部署。部署后必须再用同一需求/Figma、固定 `win-runner-01 / ecbfd645` 发起完整 Agent，持续轮询到 `DONE / FAILED / CANCELLED`。
- 最终人工验收要求视觉批次真实 `4/4 completed` 且每批有 judgement；截图/Figma 对生成仍是软参考，但不能把“Figma 已提取”误写成“视觉 AI 已完成”。若仍失败，按视觉服务/模型调用失败单独归因。
- 人工复核最终 YAML 的三个业务分支、5 寸照片归属照片打印、同级关系/文案/首个可达页和真实文字定位。若进入 Runner，首批与 remaining 每个 job 必须只在 OPPO 串行到终态，逐条核对报告、截图/录屏、失败分类和 AI 修复证据。

### 2026-07-15 Agent 验收维度、固定设备调度与 AI 修复闭环修复

部署 `f0ce998` 后发起同一完整回归：

- Agent `agent-1784094382180-7b373076`，参数仍为“基础打印新增百度网盘入口”、同一 Figma、`scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`。线上 `8091 / 8088`、AI Gateway 与 Sonic 健康，Runner 上报模型族 `qwen3.6`，OPPO PHM110 在线；所有正式与修复 job 均绑定 `ecbfd645`，没有向华为或第二台设备下发。
- 任务路由仍为 `new_requirement_source / generate_draft`，Figma parser 正确保留 4 页 / 4 图。四个单图视觉批次都真实送入 `qwen3.6-plus`，每批约 90 秒超时，结果为 `0/4 completed / 4 attempted / failed / hardGate=false`；图像继续是软参考，顶层报告仍保留 4 图计数。
- 原始需求契约正确保留文档打印、照片打印、扫描复印三个分支，每个分支都有展示 / 同级关系 / 文案 / 点击可达四个验收维度。AI 最终只生成三条展示类 executable case，平台又补了一条重复的文档冒烟；实际 YAML 都没有点击目标入口并断言首个稳定落地页。旧 portfolio gate 仅因 `requirementRefs` 挂了整条 REQ 就误判四个维度已全覆盖。
- Runner 真实结果不是“全部失败”：两条文档打印正式冒烟在 OPPO 上通过，报告截图真实显示 `本地文档 / 百度网盘 / QQ文档 / WPS文档`。照片用例的 Runner dry-run 成功，但在旧调度中被前面长时间正式任务挡在同一队列，Agent 120 秒等待超时后没有创建它的正式 job。扫描复印正式 job 失败，报告到达“小白扫描王”并显示 `本地导入 / 相册导入 / 微信导入`，目标文案仍未可见。
- 旧 `_agent_create_runner_jobs_for_refs` 按单条交错执行 `dry-run -> 正式 -> 下一条 dry-run`。固定单设备的长正式任务会让后续 dry-run 在队列中超过等待上限；超时又只标记为 inconclusive，不计入 blocker，导致冒烟实际执行数和计划数不一致。
- 失败录屏 / 关键帧、Runner 日志和可信扫描分支基线都已送给 AI。AI 正确提出“以相册导入为锨点，横向滑动后断言目标入口”，但返回了非法 Midscene 结构 `aiScroll: {direction: right, distance: 1, scrollType: singleAction}`。AI Gateway 已返回 `success=true / valid=false`，旧 Task Server 却忽略该拒绝并宣称校验通过；Runner 因 `failed to locate element 'undefined'` 失败。后续修复周期又把新旧同名草稿同时取出，合计执行三次同类非法修复。
- Agent 终态为 `FAILED / RERUN`。旧最终报告保留了部分通过概念，但只汇总逻辑用例，没有计入全部修复 job，并在 `GENERATE_SUMMARY` 时把当时的 `RUNNING` 存入 `orchestration.runStatus`。本轮真实正式尝试为 6 次：2 通过，扫描原始脚本失败 1 次，非法 AI 修复失败 3 次；照片正式任务未创建。

本轮通用修复：

- 将原始需求分支的 checks 结构化为独立 acceptance dimensions。portfolio audit 只从真实 `steps / flow / assertions / YAML actions` 判定展示、同级、文案和可达性；标题、`requirementRefs` 和 REQ 文案只表示归属，不再充当执行证据。缺失维度会送入现有一次收敛 AI，优先从同分支 manual / needs-review 短链路候选中补“点击 -> 首个有界可见终态 -> 断言”；仍然无法落地时保持人工并由门禁阻断。`3/5/8` 数量仍只是目标 / 上限，不为凑数补弱网或深层授权。
- 最终确认 YAML 再次从实际 Midscene flow 审计验收维度，避免 case 计划完整但转换后 YAML 丢步骤。展示类需求不再触发一条重复的平台合成冒烟；已有低跳转、有断言、无高重规划风险的 12 动作以内短 case 可直接作为冒烟。
- Runner 调度改为两阶段：先创建并等待整批真实 dry-run 终态，再进入正式执行。显式固定设备时，每条正式 job 必须到终态后才创建下一条；任何 dry-run 等待超时都是显式 blocker，不再被当作不影响统计的 inconclusive。这不增加设备实际执行时间，但避免同一手机的长任务挤占后续预检。
- `aiScroll` 目标必须为非空字符串，`direction / distance / scrollType` 保持官方同级字段。AI Gateway 的 `valid=false` 和 Task Server 本地强校验任一失败，草稿都只作为 `REJECTED` 诊断证据，不得产生 `fixedYaml`、不得下发 Runner。重跑只读取当前 `repairSummary.draftIds`，不再混入旧周期草稿。
- 最终执行汇总以原始正式 `jobIds` 和每轮 `rerunAttempts.createdJobIds` 建立尝试台账，再从 Runner job store 刷新真实终态。报告同时展示通过、产品失败、Broken（脚本 / 环境）、超时和原始 / 重跑尝试数；Agent 编排状态独立汇总。`GENERATE_SUMMARY` 期间会根据已失败步骤投影最终 `FAILED / DONE`，不再存储过期 `RUNNING`。
- 未修改 Figma parser、图片选择 / 计数 / 软参考策略、`router.py`、执行模式、历史 YAML、Runner 脚本或设备选择；未暂存或回滚用户 dirty 文件。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/ai_skill_service.py task_server/services/yaml_static_validator.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name 通过，后端 `61` 项、前端 `67` 项、AI Gateway `46` 项、AI skill contract fixtures `3/3` 和 Playwright 桌面 / 移动端 Agent 及重跑视觉回归全部通过。定向行为测试覆盖通用发票入口样例，证明新验收门禁不是百度网盘或小白学习专用硬编码。

待完成：

- 本轮新修复尚未部署。部署后必须再发起同一完整 Agent，持续轮询到 `DONE / FAILED / CANCELLED`，不以进度条代替终态。
- 新一轮必须确认 12 个原始验收维度的 AI 收敛结果，人工复核最终 YAML 的文档 / 照片 / 扫描分支、文案 / 同级 / 可达页、真实可见文字定位和无坐标。如进入 Runner，核对整批 dry-run 先完成、正式 job 只在 OPPO 上严格串行，并逐个检查报告、截图 / 失败录屏、AI 修复校验和最终原始 / 重跑尝试数。

### 2026-07-15 Agent 内部执行轨迹轮询后自动收缩修复

- 运行中的 Agent 每 3 秒调用 `updateAgentWorkbenchDynamic()` 重绘时间线。内部执行轨迹使用原生 `<details>`，但旧代码用 `onchange` 保存展开状态；`details` 的交互事件实际为 `toggle`，所以用户点开后 `agentCheckpointTraceOpen` 仍为 false，下一次轮询重绘就恢复成关闭。
- 改为 `ontoggle="agentCheckpointTraceOpen=this.open"`，只保存用户对当前工作台轨迹总区的展开意图。启动新 Agent 时仍按原逻辑重置为默认关闭；手动展开和手动收起都会跨轮询重绘保持，不改变 Agent 状态、轮询周期或后端接口。
- 更新 `agent-workbench.js` 资源版本，避免部署后浏览器继续命中旧缓存。Playwright 新增真实交互回归：默认关闭 -> 用户打开 -> 模拟轮询重绘后仍打开 -> 用户收起 -> 再次重绘后仍关闭。
- 已通过前端静态检查 `67` 项和桌面 / 移动端视觉烟测；未修改 Agent 执行、Figma、YAML、Runner、设备或报告逻辑。

### 2026-07-15 Agent 分支证据误绑与运行历史并发清空修复

部署 `f5c7dec` 后发起同一完整回归：

- Agent `agent-1784084185210-75f905b6`，参数仍为“基础打印新增百度网盘入口”、同一 Figma、`scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`。线上 `8091 / 8088`、AI Gateway、Sonic 均健康，Runner 上报模型族 `qwen3.6`，固定 OPPO PHM110 在线；本轮没有向华为或第二台设备下发任务。
- Figma parser 保持原实现，正确解析 4 页 / 4 图。PLAN 由 MM AI 生成 8 条业务分支，路由仍为 `new_requirement_source / generate_draft`。4 张图分成 4 个真实视觉批次送入 `qwen3.6-plus`，每批约 90 秒超时，结果为 `0/4 completed / 4 attempted / failed / hardGate=false`，顶层报告仍保留 sourceContext 的 4 图计数。
- 任务在 `GENERATE_YAML` 阶段被覆盖门禁阻断，没有创建 Runner job。最终只有 `TC-001 / TC-002 / TC-003` 三条文档打印 executable，缺少照片打印 `REQ-002`、扫描复印 `REQ-003` 和点击可达 `REQ-004`；`5` 条数量目标只报告 shortfall，没有为了数量硬凑。
- 本地候选池实际已有照片分支 `小白学习基线用例-基础打印/6寸照片打印.yaml`，扫描分支已有 `证件扫描.yaml / 文件扫描.yaml`。但候选去重合并后，一条文档百度网盘基线同时带上多个分支的 `retrievalQueries`；AI 随后把通用百度网盘候选分给照片，又把文档短链路分给扫描，并声称可从文档模式泛化。旧门禁只校验 retrieval query，没有校验候选自身页面路径，导致错误分支证据占据 Top3。

同一轮还暴露了独立的数据完整性事故：

- Agent 生成后台 job 已终态失败、cases 产物仍可读取时，`/api/agent-runs` 从约 48 条历史加当前运行瞬间变成 0，当前 Agent GET 返回 404；服务 uptime 持续增长，没有发生重启，也不是用户删除。
- `_watch_agent_generation_progress` 与主 Agent worker 都会通过 `_append_step_trace` 保存同一个 run。旧 `_persist_agent_run_snapshot` 没有使用 `AGENT_RUN_LOCK` 包住读取、修改、写回；`write_json_file` 又让所有线程共用同一个 `agent-runs.json.tmp`。并发 rename / 写入可使目标 JSON 暂时损坏，`read_json_file` 随后回退为 `{"runs": []}`，未找到当前 run 的 snapshot 保存又把空列表原样写回，因而清空全部历史。

本轮通用修复：

- 从 AI PLAN 的层级分支名通用提取叶子锚点，不包含本需求产品词硬编码。候选只有同时具备“该分支 retrieval query 命中”和“候选自身 title / path / snippet / actions 路径锚点”时，才获得对应 `eligibleBranchIds`。
- AI 仍负责 Top3 选择；平台只校验 AI 返回的 `branchId` 必须属于候选 `eligibleBranchIds`。非法跨分支分配不再占用 Top3 名额，只允许现有一次有界 AI 自纠，并且只有覆盖分支数实际增加时才采用纠正结果。有显式分支合同时，AI 失败或无有效选择不再回退无关全局 TopN，后续覆盖门禁继续阻断。
- JSON 原子写入改用同目录、进程 / 线程 / 纳秒唯一的临时文件。Agent snapshot 的完整读改写由已有 `AGENT_RUN_LOCK` 串行化，先冻结 run 快照；记录意外缺失时 upsert 当前 run，而不是保存空列表。Runner job 进度回写同样补上缺失 run 的 upsert。
- 未修改 Figma parser、图片选择 / 计数 / 软参考策略、`router.py`、历史 YAML、scorer、执行模式或设备选择；没有降低显式需求覆盖、静态校验、冒烟和 remaining 门禁。

已验证：

```bash
python3 -m py_compile task_server/storage.py task_server/services/yaml_service.py task_server/services/ai_skill_service.py task_server/services/agent_service.py task_server/services/job_service.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：线上同形测试确认“同时命中三个 retrievalQueries 的文档基线”只能绑定文档分支，6 寸照片和文件扫描分别只能绑定自己的分支；错误 AI 选择经过一次纠偏后仍错误时不会触发无关本地 fallback。16 个 Agent snapshot 并发 upsert 保留两条既有历史，24 个 200KB JSON 并发写入均完整可解析。undefined-name、后端 `61` 项、前端 `67` 项、AI Gateway `46` 项、AI skill contract fixtures `3/3` 及 Playwright 桌面 / 移动端视觉烟测全部通过。

历史恢复与待完成：

- 本轮提交尚未部署，修复部署前禁止再发起真实 Agent。线上历史能否恢复取决于 `/opt/midscene-learning/` 中是否存在 `agent-runs.json.bad*`、旧 `.tmp`、磁盘快照或主机备份；先只列出并验证每个候选 JSON 的可解析记录数，不能直接覆盖当前文件。恢复时必须按 `runId` 合并 backup 和当前 runs，再原子替换。
- 修复部署后先确认 `8091 / 8088` 健康、Runner 在线、模型族 `qwen3.6`、固定 OPPO 可用且没有活动任务；再发起同一完整 Agent，持续轮询到真实终态。人工复核 Top3 的分支证据、最终 YAML 的真实可见文字、文档 / 照片 / 扫描 / 文案 / 可达覆盖；若进入 Runner，smoke 和 remaining 的每个 job 都必须固定 `ecbfd645`，核对报告、截图、失败录屏和 AI 修复证据。

### 2026-07-15 完整 Agent 分支基线、横切覆盖与非凑数门禁修复

部署 `8abf30e` 后发起同一完整回归：

- Agent `agent-1784080784835-7ceb6d1f`，输入仍为“基础打印新增百度网盘入口”、同一 Figma、`scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`。线上 `8091 / 8088` 健康，Runner 在线并上报 `qwen3.6` 模型族，OPPO PHM110 `ecbfd645` 在线；本 Agent 没有向华为或第二台设备下发任务。
- `PREPARE_SOURCE` 正常保留 Figma 4 页 / 4 图。PLAN 由平台 MM skills 生成 8 条 AI 业务分支，没有预设伪主链；4 个视觉批次均真实送入 `qwen3.6-plus`，每批约 90 秒超时，视觉资料继续作为软参考。
- 任务终态为 `FAILED / GENERATE_YAML`，没有创建 Runner job。覆盖门禁正确阻断：最终只有 `TC-001 / TC-002 / TC-004` 三条 executable，缺少 `REQ-003` 扫描复印和 `REQ-005` 入口 UI 一致性。
- 根因一是 Top3 重排虽然收到 20 个分支多样候选，却把 `navigation_path / capability_pattern / assertion_pattern` 三个角色全部分配给文档打印旧成功稿，并明确拒绝照片、扫描分支；因此 planner 看不到本地可信的 `6寸照片打印.yaml` 与 `文件扫描.yaml` 路径证据。
- 根因二是 automation_filter 把横跨三个页面的文案 / 图标 / 同级要求替换成深色模式用例，随后归为 manual；文档、照片、扫描三个分支 case 自身已有同页可见断言，却没有保留 `REQ-005` 映射。
- 报告还有一个独立问题：PLAN 内已准确记录视觉 `0/4 completed、4 attempted、failed`，但顶层 `visualReferenceReport` 只在 YAML 成功返回后刷新，因此生成失败时仍错误显示 `pending / sent=false`。

本轮通用修复：

- Top3 数量保持不变，不扩成 Top6。平台从 AI PLAN 的 `smokeFlowIds` 提取最多三个必需业务分支，要求 AI 重排先做到“一分支一条可信路径基线”，再考虑角色互补；结果会校验 `branchId + retrievalQuery`。只有首轮遗漏分支时才进行一次有界 AI 自纠，正常路径不增加模型轮次，第二次仍不合格则由后续覆盖门禁阻断。
- AI 选中的分支 ID / 名称会继续传给 executable planner、生成上下文和报告，便于确认照片分支确实使用 6 寸照片成功路径、扫描分支确实使用文件扫描 / 证件扫描路径，而不是只看模糊的 Top3 标题。
- automation_filter 现在要求每条 case 输出 `requirementRefs`。横跨多个兄弟页面的可见 UI 要求由各分支独立短 case 共同证明：case 保留自己的主分支 `coverage`，并在自身步骤 / 断言确有证据时附加横切 REQ；禁止把横切要求擅自替换成深色模式、多语言、横竖屏或跨页面长链路。
- planner 的需求边界改为候选原始 `coverage + requirementRefs` 并集。这样 AI 原先建立的横切映射不会被 coverage 单值覆盖，同时 planner 后加的跨分支 REQ 仍会触发 path mapping guard，不能把照片路径偷换成扫描需求。
- `3/5/8` 明确改为 AI 规划目标和规模上限，不是最终 executable 数量硬下限。最终门禁仍阻断零 executable、显式 REQ 缺失和自动候选分类未终结；如果更少的独立短 case 已完整覆盖需求，则返回 `ok=true`，通过 `targetMet / targetShortfall / advisories` 如实报告数量差额，不允许用弱网、深色模式、系统设置、重复路径或深层授权项凑数。单条 YAML scorer、静态校验、dry-run、冒烟和 remaining 门禁均未降低。
- PLAN 结束后立即用真实 mindmap 视觉批次刷新顶层报告；即使 GENERATE_YAML 随后失败，也会保留 sent / completed / attempted / failed 计数和逐批错误。未修改 Figma parser、选页、图片计数或软参考策略。

使用线上保存产物离线重放：

- AI 首批分支目标精确为文档打印、照片打印、扫描复印；本地候选池分别包含 `百度网盘打印.yaml`、`6寸照片打印.yaml`、`文件扫描.yaml / 证件扫描.yaml`。
- 在原线上三条 executable 基础上，把扫描展示按可信扫描路径升级，并由文档 / 照片 / 扫描三个同页 case 保留 `REQ-005` 后，结果为 4 条 executable 覆盖全部 5 个 REQ：`ok=true / targetExecutableCount=5 / targetMet=false / targetShortfall=1`。不需要再升级照片深层授权或其他低价值项。
- 原线上未修形态仍因缺少 `REQ-003 / REQ-005` 被阻断；多目标点击、跨分支需求偷换和原人工候选无可信基线升级仍被原门禁拒绝。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name 通过，后端 `61` 项、前端 `67` 项、AI Gateway `46` 项、AI skill contract fixtures `3/3` 通过；Playwright 桌面 / 移动端 Agent 与重跑视觉烟测通过。

待完成：

- 本轮提交尚未部署。部署后再次使用同一需求 / Figma 和固定 `win-runner-01 / ecbfd645` 发起完整 Agent，并持续轮询到 `DONE / FAILED / CANCELLED`。
- 人工复核 Top3 分支归属、最终 YAML 的真实可见文字定位、文档 / 照片 / 扫描及横切文案要求；如果进入 Runner，首批与 remaining 的每个 job 必须都固定 OPPO，核对真实报告、截图、失败分类和 AI 修复，不以进度条或 Agent 总状态替代执行事实。

### 2026-07-15 智小白 Sonic 基线失败突增定位与失败状态隔离

线上核查最近 10 次 `智小白3D / 3D测试自动` Sonic 套件：

- `1072` 为 11 / 11 通过，`1074`、`1075` 为 10 / 11 通过，`1076` 为 9 / 11 通过；从 `1077`（2026-07-14 18:21）开始突增到 7-8 条失败，最新 `1081` 为 3 成功 / 8 失败。
- 最新套件中 `打印记录查看`、`我的收藏查看`、`小白客商城-耗材` 仍成功，11 条均有 Task 回调和 Midscene 报告；Windows 主机、ADB、模型和 Sonic Driver 并未整体失效。
- Sonic Bridge 实际直接在 `D:\\sonic` 执行 `midscene <yaml>`，不经过 `windows-midscene-runner.py`。失败突增前后 Bridge 版本相同，仓库在对应时间也没有 Runner / Bridge 提交，因此不是 Windows Runner 服务脚本突然回归。
- 失败 `标牌打印`、`OBJ保龄球打印` 报告中反复出现 App 明确弹窗“没有可用的耗材，请先进料”；15:00 的成功 `标牌打印` 报告没有该弹窗，而是正常的耗材颜色确认。首因是华为设备当前绑定打印机的耗材 / 进料环境状态发生变化。
- 连锁机制已经由时序报告确认：打印用例的“取消打印”点击被缺耗材弹窗拦截，后续等待停在“模型打印预览”；下一条用例启动时，复合 AI 清理需要依次进入遗留任务、关闭弹窗、取消、确认、返回，超过线上 5 次重规划上限。最新结果因此呈现 `waitFor 失败 -> 下一条 replan 超限` 的交替模式。

本轮通用修复没有修改任何历史 YAML：

- Sonic Bridge 版本更新为 `2026.07.15-bounded-ai-recovery-v1`，重规划有效下限与平台统一为 8；正常动作完成即停止，只有复杂多弹窗动作才会使用额外预算，线上遗留的 5 次配置不会继续覆盖该安全下限。
- 只有重规划、等待超时、定位、断言等 UI 状态型失败才触发一次失败后 AI 恢复；AI 根据当前真实可见文字关闭阻塞弹窗、取消未完成流程并回到首页 / 主导航，不包含智小白、耗材或某条用例的业务硬编码。
- 恢复最长 180 秒，独立使用至少 8 次重规划；不会增加正常成功用例耗时。模型中止、限流、服务不可用、YAML 加载 / 语法问题跳过二次 AI 调用并直接强停 App。
- 恢复无论成功与否都会强停目标 App；临时恢复 YAML 和恢复过程生成的 Midscene 报告会删除，原始失败报告与失败结论保持不变。该修复只隔离用例状态污染，不会把“缺耗材”改成通过。

已验证：

```bash
python3 -m pytest -q tests/test_sonic_integration.py -k 'groovy'
python3 tests/backend_static_checks.py
```

结果为 Bridge 专项 `14 passed`、后端静态检查 `61` 项通过；测试还实际解析了临时恢复 YAML。`tests/test_sonic_integration.py` 全文件仍有 72 条既有失败，原因是旧测试继续从已拆空的 `midscene-upload.py` 兼容壳读取迁移后的函数，统一为 `AttributeError`，与本轮 Bridge 修改无关；其余 21 条通过。本机没有 Groovy CLI，临时下载编译器又受 DNS 限制，因此未完成独立 Groovy 编译，必须以部署后的 Bridge 诊断和真实单设备套件作为最终运行验证。

待完成：

- 华为设备当前绑定打印机先完成进料，并在 App 中确认不再出现“没有可用的耗材，请先进料”；这是打印链路恢复的物理前提。
- 本轮提交部署后确认线上 Bridge 版本，再只在当前华为设备上复跑 `3D测试自动` 到终态；重点检查首个真实打印用例、失败后 AI 恢复日志、后续用例是否不再继承打印预览状态，以及 11 条真实报告 / 截图。
- 用户已有 dirty 的两份十二生肖 YAML、`sonic_service.py`、`yaml_executable_scorer.py`、本地 Windows Runner 服务脚本和 `server-tasks/AI_Agent_草稿/` 不纳入本轮提交。

### 2026-07-15 完整 Agent 流程证据回放与最终执行门禁补强

重新核对最近一次真正走完生成、冒烟、remaining、失败分析和 AI 修复的线上 Agent：

- Agent `agent-1784024849032-89428fd5`，终态 `FAILED / RERUN`。Figma 仍正确解析为 4 页 / 4 图，4 个单图批次均真实送入 `qwen3.6-plus`，但每批约 90 秒超时；视觉资料保持软参考。PLAN 总耗时约 615 秒，其中视觉串行超时是主要性能成本之一。
- 最终只有 4 个可执行引用：文档短冒烟、文档、照片、当前固定设备文案。映射只覆盖 `REQ-001 / REQ-002 / REQ-005`，缺少 `REQ-003` 扫描复印和 `REQ-004` 点击后可达；旧最终门禁却只读取设计期 `missing_case_points`，而设计期 audit 会把 manual 项也视为覆盖，因此错误放行 Runner。
- 原始 Runner 事实是 3 成功 / 1 失败，不是全部失败。文档、文档短冒烟和手机文案成功；照片失败。4 个正式任务、1 次原 YAML 证据重试和 1 次 AI 修复重试均固定在 `win-runner-01 / ecbfd645` 串行执行，没有下发第二台设备。
- 人工复核真实 Midscene 报告：照片失败停在展示“照片打印 / 智能证件照 / 普通证件照 / 照片拼版打印”等入口的父页面。首轮 Runner review 错判为 `PRODUCT_BUG`；后续关键帧 AI 正确改判 `SCRIPT_ISSUE`，原脚本缺少进入内层照片打印和照片规格页的导航。
- 旧 AI 修复虽然提出进入 `5寸照片/一寸照`，但 Top3 全为文档分支基线，没有召回 `6寸照片打印.yaml`。修复 YAML 又生成 `aiTap: 点击「5寸照片」或「一寸照」等任一照片规格`：前一条宽泛 `aiWaitFor` 在父页面误判成功，随后多目标 Locate 一直无法完成，最终被 Windows Runner 的 300 秒单任务上限终止；可访问的是被强停前保存的部分报告，未形成完整 Midscene 终态报告。

本轮通用修复：

- 最终 Runner 覆盖门禁现在从完整 `generatedCases.analysis.requirement_points` 提取全部 `REQ-*`，只与已确认、可执行、Runner 可下发的 YAML 引用比较。manual / needs_review / draft 仍计入测试设计覆盖，但不能再掩盖最终 YAML 缺口。
- 修复基线分支识别优先使用失败任务名、文件名和真实失败原因；原 YAML 仍参与全局相关性检索，但注释中的“复用文档策略”等旁支文字不能改变首个分支候选。用线上 Run 原对象离线重放后，首个候选为 `server-tasks-all/小白学习基线用例-基础打印/6寸照片打印.yaml`。
- YAML 强校验新增多目标点击门禁：`aiTap` 只能指向当前页一个真实可见目标；包含“任一 / 任意 / 任选”或两个引号目标加“或”的点击会在生成或 AI 修复后直接阻断。多个合法结果仍可写入 `aiWaitFor / aiAssert`，没有降低 AI 对状态分支的判断能力。
- 使用线上 Run 原始 artifacts 重放：新门禁精确返回缺少 `REQ-003 / REQ-004`，映射只包含 `REQ-001 / REQ-002 / REQ-005`；旧修复 YAML 被强校验识别为多目标点击，不会再消耗 300 秒真实设备执行。
- 未修改 Figma parser、视觉资料软参考策略、`router.py`、历史 YAML、执行模式或设备选择；本轮没有为了缩短 PLAN 盲目减少设计图。视觉串行超时仍需在部署后的下一次完整回归中测量，再基于真实模型耗时决定是否做有界并发或自适应批次。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

结果：undefined-name 通过，后端 `61` 项、前端 `67` 项、AI Gateway `46` 项、AI skill contract fixtures `3/3` 通过；Playwright 桌面 / 移动端 Agent 与重跑视觉烟测通过。

待完成：

- 本轮提交尚未部署。部署后先确认当前 Sonic 单设备套件已终态，避免两台手机并行；再用同一需求、同一 Figma、固定 OPPO `ecbfd645` 发起完整 Agent。
- 新 Agent 必须轮询到 `DONE / FAILED / CANCELLED`，重点核对 5 个需求映射、照片分支是否召回并引用 6 寸成功基线、最终 YAML 是否只有单一可见目标点击、smoke / remaining / AI 修复报告和截图，以及全程单设备串行。

### 2026-07-14 Agent 人工初判可重评、状态无关可达性与视觉小批次修复

部署 `cb36a17` 后，继续使用同一完整需求和固定设备做线上回归：

- Agent：`agent-1784002894995-d3823074`，参数为 `scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`，App 为 `小白学习打印 / com.xbxxhz.box`。
- 已确认本地/远端提交为 `cb36a17`，公网 `8091 / 8088` 健康，AI Gateway、Sonic 健康，文本/视觉模型均为 `qwen3.6-plus`；平台只有 1 个 Windows Runner 在线。
- 终态为 `FAILED / GENERATE_YAML`，不是 Runner 失败。`executionPrecheck / sonicJob / report / jobProgressByPhase` 均为空，没有向 OPPO 或第二台设备下发任务。
- Figma parser 保持原实现，解析为 4 页/4 图、忽略 0 页；4 张图确实进入 AI 视觉批次。qwen3.6-plus 在 120 秒内未返回，视觉状态为 `failed / 0/1`，继续按软参考处理，没有升级为硬门禁。
- PLAN 来自平台 MM AI，共形成 8 个核心业务分支；可信基线重排选中 3 条 `verified_execution / execution_success` 记录。覆盖审查后设计为 5 条自动化、11 条人工、12 个场景，生成 5 个 YAML；其中生成结果只有 3 个达到 executable，加上平台补充的短冒烟共确认 4 个。
- 覆盖门禁正确阻断：5 个需求点只映射 `REQ-001` 到 `REQ-004`，缺少 `REQ-005 百度网盘入口可达性`。因此首批冒烟和 remaining 均未执行，本轮没有新的 Runner 截图、报告或失败录屏可复核。

根因与设计判断：

1. 上游 automation filter 已生成“已授权进入文件列表”“未授权进入授权流程”等可达性场景，但先判为 `manual_cases` 后，最终 executable planner 只收到自动候选，不能基于成功基线、当前设备和需求重新判断。AI 的第一次分类被平台做成不可逆结论，导致明确需求点永久缺失。
2. 本需求只要求点击后到达授权页或文件列表且无白屏/崩溃，不要求输入账号、验证码或选择网盘文件。授权页、登录页、文件列表、空态页可作为账号状态不同导致的多个合法首个终态；一条到首个稳定页面即停止的短链路可以自动化，深层第三方操作仍应保留人工。
3. 多端文案要求在本次固定单设备执行约束下，只能生成一条当前设备可复用的文案/布局检查；不能把小屏和宽屏各生成一条 Runner 用例，更不能根据 `deviceId` 猜测屏幕形态或选择第二台手机。
4. 连续两轮生产证据表明 4 张图单批调用在 120 秒超时。Figma 解析数量和文本仍正确，问题在视觉模型单批负载；减小图片批次比抬高超时更有利于完成率和总速度。

本轮通用修复：

- executable planner 现在一次接收自动候选和前序人工候选，并用 `originLevel` 保留来源。前序人工结论只是 AI 初判，可以重新分类；未被最终 AI 提及的人工候选仍保留 manual，未提及的自动候选仍降为 needs_review。
- 原人工候选只有同时具备可信且在允许列表中的成功基线、明确前置、至少两步短 flow、可见终态和显式 `requirementRefs`，才能升级为 executable；任一条件缺失都会由代码门禁降回 needs_review。现有 scorer、静态校验、需求范围门禁和完整覆盖门禁均未降低。
- 对需求明确要求的点击可达性，AI 可规划“多个合法首个终态任一出现”的状态无关短链路；到首个落地页即停止，不输入账号/验证码、不确认授权、不选文件。第三方深层状态、特定账号数据、断网和权限切换继续进入 manual。
- Agent 的 `executionMode / runnerId / deviceId / deviceStrategy / singleDeviceOnly` 作为执行上下文传给最终 AI。固定单机时最多保留一条当前设备通用适配检查，其他设备形态进入 manual；YAML 仍只允许真实可见文字，不允许坐标。
- AI 返回的 `requirementRefs` 进入 case、覆盖审计和需求范围审查，避免标题相似但需求映射丢失。人工候选只在统一 `cases` 数组出现一次，额外只传数量，避免重复上下文拖慢规划。
- 脑图视觉默认批次从 8 张收敛为 2 张，4 张 Figma 图会拆成两批并受原 300 秒总预算约束；没有修改 Figma parser、选页、图片计数或软参考策略。

线上原始 `generatedCases` 离线重放结果：

- AI 协议可把文档打印、照片打印、扫描复印、当前固定设备文案检查和状态无关可达性规划为 5 条 YAML，逐条映射 `REQ-001` 到 `REQ-005`。
- 5 条 YAML 经现有 `score_midscene_yaml_executable(..., generated=True)` 均为 `executable`，完整覆盖 gap 为 `{}`，没有坐标；宽屏、特定账号、数据选择、断网和权限分支仍保留 11 条人工用例。
- 该结果是对同一线上产物执行新 AI 输出协议和原有门禁的重放，不是手改历史 YAML，也没有把单一“百度网盘”需求写入代码分支。

方案依据：[AndroidWorld](https://google-research.github.io/android_world/) 使用独立初始化、成功判定和清理保证移动 Agent 任务可复现；[BrowserGym](https://arxiv.org/abs/2412.05467) 使用统一观测/动作空间和可审计评测；[Midscene Android API](https://midscenejs.com/android-api-reference) 同时提供 AI 全流程规划与原子交互能力。当前实现据此采用“AI 负责状态分支和路径计划，平台负责候选绑定、终态证据与安全门禁”的分层，而不是无约束执行长第三方流程。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/ai_skill_service.py task_server/config.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
bash -n deploy/install-server.sh
npm test
git diff --check
```

结果：undefined-name 通过，后端 `61` 项、前端 `67` 项、AI Gateway `46` 项、AI skill contract fixtures `3/3` 通过；Playwright 桌面/移动端 Agent 和重跑视觉烟测通过。

未修改 `router.py`，未新增执行模式，未修改 Figma parser 或历史 YAML；用户已有 dirty 的历史 YAML、`sonic_service.py`、`yaml_executable_scorer.py`、本地 Windows Runner 服务脚本和 `server-tasks/AI_Agent_草稿/` 不纳入本轮提交。

本轮新提交尚未部署，不能宣称线上 Agent 已闭环成功。部署后必须再次使用同一需求/Figma和固定 `win-runner-01 / ecbfd645` 跑到 `DONE / FAILED / CANCELLED`，重点核对两批视觉 AI、5 个需求映射、首批 smoke、remaining、真实 Runner 报告/截图以及全程单 OPPO 串行约束。

### 2026-07-14 真实 Runner 结果闭环、AI 分层决策与累计可观测性修复

部署 `e08ff7a` 后，使用同一完整需求继续线上验证：

- Agent：`agent-1783996803174-72c6fae8`。
- 参数固定为 `scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`，App 为 `小白学习打印 / com.xbxxhz.box`。
- 线上 `8091 / 8088` 健康，AI Gateway、Sonic 健康，文本/视觉模型为 `qwen3.6-plus`；只有 1 个 Windows Runner 在线。所有正式执行和修复重跑都在 `win-runner-01 / ecbfd645（OPPO PHM110）` 串行完成，没有选择或并发第二台手机。
- Figma parser 保持原实现，解析为 4 页/4 图。全部 4 张图进入视觉 AI 批次，qwen3.6-plus 在 120 秒超时，状态为 failed；平台按软参考处理，没有作为硬门禁，也没有把部分返回的 asset 误计成 Figma 只有 1 张。
- Agent 最终为 `FAILED / RERUN`，不是“全部没有成功”。真实结果如下：
  - 首批冒烟 `3/3` 成功：`job_1783997814495_00002`、`job_1783997923650_00004`、`job_1783998016962_00006`。
  - remaining 扩展 3 条均在同一 OPPO 执行后失败：`job_1783998179648_00008` 已到百度网盘文件列表但被冗余模糊等待拖死；`job_1783998349285_00011` 停在照片打印父页面，没有进入 `5寸照片/一寸照` 叶子页；`job_1783998492174_00014` 是跨三页长链路超时。
  - AI 使用原 YAML、Runner 日志、Midscene 报告关键帧和可信 Top3 基线生成 3 条修复草稿，并继续固定 OPPO 串行验证：`job_1783998907297_00018` 文档链路修复成功；`job_1783999008879_00019` 仍停在错误的照片打印父页面；`job_1783999116965_00020` 因 AI 生成非法 `aiScroll(direction=horizontal, distance=medium)` 被 Midscene schema 拒绝。
- 当前 Runner/Midscene 产物没有独立上传 mp4 录屏，因此平台没有伪报“使用了完整录屏”；失败报告的时序关键帧、截图、日志和终态页面已经实际送入 AI。后续若 Runner 提供视频产物，应从视频抽取关键帧并纳入同一有界证据包，而不是把整段视频无差别塞给模型。

根因与设计判断：

1. executable planner 已经把 7 条候选分成 3 条 executable、1 条 needs_review、3 条 manual，但旧应用逻辑只消费 executable 的路径计划，忽略另外三组；后续静态 scorer 又把未应用的候选提升，导致 AI 明明判断不应自动执行的长链路仍进入 Runner。
2. Figma 叶子页面能证明目标控件同屏存在，但不能单独证明从父页面如何导航。照片链路需要由 AI 组合“需求范围 + Figma 同屏事实 + 已成功 6 寸照片基线 + 当前失败关键帧”，不能把 Figma frame 名直接当真实路径，也不能为 `5寸照片` 写单点硬编码。
3. AI repair 输出只经过网关自报校验，服务端没有独立复核 Midscene 子参数，因而非法 `aiScroll` 浪费了一次约 118 秒的真实设备执行。
4. 失败分类用简单关键词看到断言里的“无遮挡”就误判 popup/overlay；最终失败分析又优先读取过期 execution precheck，覆盖了最新 Runner 的明确脚本证据，错误得到 `ENV_ISSUE`，阻止了受限第二轮 AI 纠偏。
5. 前端实时轨迹按每个串行 job/phase 重置 `0 成功 / 1 运行`，最终只剩最后一个失败 phase，淹没了首批 `3/3` 和修复 `1/3` 的成功事实；`timeout=1800` 还同时承担等待上限和超时数量语义。

本轮通用修复：

- AI executable planner 成功返回后，其四组分类成为候选执行分层的权威语义结果；同一候选冲突时采用 `manual > draft > needs_review > executable` 的保守优先级，未提及候选进入 needs_review。manual 从 Runner 候选池移出，最终 smoke selector 只接收 executable，平台 scorer 和覆盖门禁仍独立保留。
- 需求分析、场景设计、executable planning、失败分析和修复统一接收有界 `sourceEvidence`。Figma 继续复用原 parser 的 4 页/4 图，只作为“同一 frame 内可见控件/文案/布局”的 AI 软证据；导航路径、入口归属和跨页关系必须结合需求或可信成功基线推断。
- 修复策略优先从成功基线恢复父页面路径，再用当前失败关键帧定位分叉点；失败草稿不会进入成功基线。`5寸照片` 可参考同分支 `6寸照片` 的成功父级导航，但不复制无关叶子断言。
- 新增 Midscene 子参数契约校验，生成 YAML、AI 修复草稿和服务端重跑前都独立检查 `aiScroll`：方向仅允许 `down/up/right/left`，distance 必须为正数，scrollType 仅支持 `singleAction`。动作 schema 失败统一归为 `SCRIPT_ISSUE / YAML 动作参数不兼容`，不会下发 Runner。
- 失败归因只把明确的正向弹窗/遮挡证据视为 overlay；`无遮挡/未出现弹窗/no popup` 等否定文本不再误触发。最新 Runner 失败优先于过期 precheck，只有证据一致时才沿用旧诊断。
- 安全重跑新增任务级 `rerunProgress.items`，逐条持久化“原失败 -> AI 修复 -> 固定设备新 job -> Runner 报告/结果”；受限第二轮修复保存 history，顶部按所有轮次累计成功/失败/超时，仍保持固定设备串行。
- Runner 详情按 `jobProgressByPhase` 展示真实执行累计和各阶段结果，当前 phase 单独显示。`1800s` 只显示为等待上限，不再误报成 1800 个超时。主要结果先展示，内部 `_tool_rerun` 轨迹和工具调用默认收进“技术日志”。
- 没有增加额外模型轮次：复用已解析的 Figma、已有 Top3 基线和当前报告关键帧；通过 AI 分层减少不可信 long-chain Runner 下发，并在服务端提前挡住非法动作，兼顾 Agent 自主性和执行速度。

方案依据：

- Midscene 推荐自然语言自动规划与 workflow/atomic steps 组合，复杂流程应拆分并通过报告回放定位失败：[Midscene introduction](https://midscenejs.com/introduction)。
- BrowserStack Appium Self-Heal 从成功执行的元素上下文学习替代定位并记录 healing，说明成功基线应先于失败修复成为可信记忆：[Appium Self-Heal](https://www.browserstack.com/docs/app-automate/appium/self-healing?fw-lang=nodejs)。
- mabl 只在已有足够成功历史时启用高级 GenAI auto-heal，低置信匹配继续失败而不是冒险点击；与“AI 自主决策 + 可信历史 + 平台门禁”边界一致：[How auto-heal works](https://help.mabl.com/hc/en-us/articles/19078583792404-How-auto-heal-works)。
- UI-Mem 使用 workflow、subtask skill 和 failure pattern 的分层记忆，支持把成功路径与失败分叉分开沉淀，而不是保存单一需求补丁：[UI-Mem](https://arxiv.org/abs/2602.05832)。

已验证：

```bash
npm test
git diff --check
```

结果：undefined-name 通过，后端 `61` 项、前端 `67` 项、AI Gateway `46` 项、AI skill contract fixtures `3/3` 通过；Playwright 桌面 1440px 和移动端 390px 视觉烟测通过，重跑成功项、累计统计、报告入口和折叠技术日志无水平溢出。完整测试还发现并修复了脑图分支中 Figma 软证据变量作用域错误，没有修改 Figma parser。

未修改 `router.py`，未新增执行模式，未修改历史 YAML；用户已有 dirty 的历史 YAML、`sonic_service.py`、`yaml_executable_scorer.py`、本地 Windows Runner 服务脚本和 `server-tasks/AI_Agent_草稿/` 不纳入本轮提交。

本轮新提交尚未部署，不能宣称线上闭环已通过。部署后必须再次使用同一需求/Figma和固定 `win-runner-01 / ecbfd645` 发起完整 Agent，持续轮询到 `DONE / FAILED / CANCELLED`；人工复核四组 AI 分层、三个业务入口/文案/多端要求、最终 YAML 可见文字定位、首批与 remaining 的真实报告和截图。


### 2026-07-14 Agent PLAN 结构化调用与动态阶段优化

部署 `de2bf40` 后发起同一完整线上回归：

- Agent `agent-1783990871168-817d049a`，继续固定 `scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`，App 为 `小白学习打印 / com.xbxxhz.box`。
- 线上 `8091 / 8088` 健康，AI Gateway、Sonic 健康，文本/视觉模型为 `qwen3.6-plus`；只有 1 个 Runner 在线。任务前无其他 RUNNING Agent。
- `PREPARE_SOURCE` 正常：Figma 保留 4 页/4 图，忽略 0 页，prepared context 被 PLAN 复用；没有修改 Figma parser、选页或计数逻辑。
- PLAN 正确先显示“文档打印、照片打印、扫描复印”为原始需求候选，`candidateOnly=true / strict=false / businessFlow=[]`，没有再把三个同级入口串成伪业务主链。
- PLAN 两次均复用平台 MM skills，但最终 `FAILED / PLAN`。`requirement_analyzer` 真实成功；`scenario_designer` 两次都在 90 秒超时并返回本地兜底，`baseline_reranker / smoke_selector` 也超时，视觉批次送入全部 4 张 Figma 图后在 120 秒超时。
- 本轮真实 AI 参与度不能按架构预期计算：必需语义节点中只有需求理解成功，基线重排、场景设计、冒烟选择和视觉校准都没有形成可用 AI 结论。平台拒绝把兜底伪报为 AI 计划是正确行为。
- 没有产生可执行 YAML，`executionPrecheck / sonicJob / report` 均不存在；未向 OPPO 或第二台设备下发任务。

根因与设计判断：

1. `/ai/skill` 的调用方明确要求 JSON，但 AI Gateway 丢掉了 `jsonResponse`，因此没有向模型传 `response_format=json_object`。
2. 阿里云官方文档说明 Qwen3.6 默认开启 thinking，而 JSON Mode 与 `enable_thinking=true` 不兼容。结构化 skill 在深度思考模式下消耗预算，是 45/90/120 秒级联超时的共性原因，不应继续简单抬高超时。
3. 核心 scenario AI 已经失败后，旧流程仍会继续调用 automation/smoke/visual，既不可能挽救当次 PLAN，也使一次有界重试变成长串行等待。
4. 用户看到的 20 个检查点是状态机审计细节，不应冒充顶层业务计划。第一阶段必须是资料准备，第二阶段才能由 AI 基于完整证据规划。

本轮通用修复：

- AI Gateway 为结构化 skills 传递 JSON Mode；DashScope `qwen3.5/3.6/3.7` 同时设置 `enable_thinking=false`。直连 DashScope 的文本/视觉 JSON 调用使用同一规则。AI 仍负责语义决策，输出继续通过 schema、覆盖和平台门禁校验。
- Agent MM 启用 `require_ai_core`：`requirement_analyzer` 或 `scenario_designer` 未产出真实 AI 结果时，返回显式 `core_ai_failure`，跳过不可能修复当次计划的下游 skills 和视觉批次，立即交给 Agent 现有的最多 2 次有界重试。普通非 Agent 生成仍可使用原本兜底。
- 前端顶层改为 5 个正常阶段：资料准备、AI 计划、生成与门禁、固定设备执行、总结沉淀。只有真实进入失败处理时才出现第 6 个“诊断与恢复”条件阶段。
- 原有 20 个内部检查点没有删除，改到默认收起、可展开的“内部执行轨迹”；保留每步时间、产物、错误、AI 调用和安全门禁，不降低可观测性。阶段顺序修正为 `PREPARE_SOURCE -> PLAN`。
- 失败分析/修复已经使用 Midscene 报告的有界时序截图关键帧，并同时传入原 YAML、Runner 日志、`failureReview`、最新失败证据和可信 Top3 基线。当前 Runner/Midscene 没有上传独立 mp4 录屏产物，因此平台不伪报“已使用完整录屏”；现有关键帧已作为视觉失败轨迹交给 AI。

设计依据：

- Google ADK 2.0 建议用确定性 graph/workflow 管理外层编排，把概率性模型放在认知节点，并使用动态路由、有界循环和 eval：[Why we built ADK 2.0](https://developers.googleblog.com/why-we-built-adk-20/)。
- OpenAI Agents 把 trace 与 guardrail 分层：内部检查点应完整可审计，但不应全部变成用户主流程；不可绕过的安全约束继续由 guardrail 负责：[tracing](https://openai.github.io/openai-agents-python/tracing/)、[guardrails](https://openai.github.io/openai-agents-python/guardrails/)。
- Anthropic 的 evaluator-optimizer 模式要求修复循环有明确评估证据和终止条件，与当前“最新 Runner 证据 + 可信基线 + 最多 1 轮修复”一致：[Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)。
- Qwen thinking/JSON 兼容性依据阿里云官方文档：[Deep thinking](https://help.aliyun.com/en/model-studio/deep-thinking)、[Structured output](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。

已验证：

```bash
npm run test:static
npm run test:visual
git diff --check
```

结果：u540e端 `61` 项、前端 `65` 项、AI Gateway `46` 项通过，undefined-name 通过，AI skill contract fixtures `3/3` 通过；Playwright 视觉烟测通过。额外检查了 1440px 桌面与 390px 移动端：正常链显示 5 个阶段，失败链动态显示第 6 阶段，无水平溢出。未修改 `router.py`，未新增执行模式，未修改 Figma parser 或历史 YAML，未纳入用户已有 dirty 文件。

新提交部署后必须使用同一需求/Figma 和固定 `win-runner-01 / ecbfd645` 再跑到终态。重点核对各结构化 skill 的真实成功/耗时、Figma 4/4 视觉批次、三个业务入口 YAML 覆盖、首批 smoke 和 remaining 在同一 OPPO 上的报告终态。

### 2026-07-13 Agent PLAN 改为复用平台 MM skills，规则候选不再冒充业务主链

部署 `630489f` 后发起同一线上回归：

- Agent `agent-1783943773146-d1db26ce`，参数仍固定为 `scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`，App 为 `小白学习打印 / com.xbxxhz.box`。
- 公网 `8091 / 8088` 健康；AI Gateway、Sonic 健康；文本/视觉模型均为 `qwen3.6-plus`；平台状态为 1 个 Runner 在线。任务发起前没有其他 RUNNING Agent。
- 线上新版本确实进入 `agent-business-plan-v2`，但真实 AI PLAN 连续耗时约 92 秒后没有得到可解析 JSON，最终产物为 `source=rule_fallback / aiGenerated=false / fallbackReason=Expecting value: line 1 column 1`。平台却把该步骤记成 `SUCCESS`。
- 截图中“业务主链：进入首页 → 进入文档打印 → 进入照片打印 → 进入扫描复印”不是 AI、Figma 或基线结论，而是 `create_agent_run()` 在 PLAN 前调用 `_ensure_business_flow_constraint()`，把原始需求里的三个同级入口先做正则抽取，再通过兼容扁平字段串成了一条伪顺序链。
- Figma 准备阶段本身正常：仍为 4 页/4 图、忽略 0 页，缓存中保留 4 份图片内容。本轮问题不在 Figma parser。
- 发现规划语义不成立后，于 `20:01:01` 主动取消该任务。终态为 `CANCELLED`，停在 `GENERATE_YAML`；`executionPrecheck / sonicJob / report` 均不存在，没有向 OPPO 或第二台设备下发 Runner 任务。生成线程在取消前留下 3 个局部 refs，但没有进入执行门禁。

根因与边界判断：

1. Agent 的第一步重复实现了一条独立纯文本 `/ai/chat` 规划链，没有复用平台已有的 MM/脑图需求分析能力，也拿不到 PREPARE_SOURCE 整理后的 Figma 图片和可信基线。
2. 状态机顺序是 `PLAN -> PREPARE_SOURCE`，因此 AI 规划先于资料整理；确定性正则候选又被标成 `strict=true`，反过来要求 AI “遵守主链”。规则从覆盖兜底越权成了业务路径决策者。
3. `/ai/chat` 异常被 `_ai_gateway_post()` 吞成空对象；PLAN 两次失败后生成规则计划并返回 `SUCCESS`。这使“AI 不可用”和“AI 已规划”在状态、UI 和下游约束中无法区分。

本轮通用修复：

- 状态顺序改为 `PREPARE_SOURCE -> PLAN -> IMPACT_ANALYSIS`。先继续使用现有 Figma 解析流程生成 prepared context，再开始 AI 规划；没有修改 `load_figma_generation_context`、选页规则、图片格式或 4 页/4 图计数逻辑。
- PLAN 直接复用平台已有 `generate_mindmap_from_request()`：`requirement_analyzer.v1 -> scenario_designer.v1 -> automation_filter.v1 -> visual_grounder.v1`。Agent 强制关闭入口类确定性 fast path，避免当前需求再次绕过 AI。
- MM 规划复用 prepared Figma context，不重新解析 Figma；Figma/上传截图仍是 AI 软参考。PLAN 分开记录 `sentToAiForJudgement / aiJudgementCompleted / aiJudgementStatus`，视觉批次失败不会升级成硬门禁。
- 视觉批次状态按 `done/total` 区分 `completed / partial / failed`；只有全部批次完成且无错误才标记 `aiJudgementCompleted=true`，部分成功仍可作为 AI 软参考，但不能伪报“已完成”。
- MM 规划前使用现有可信基线缓存和 `baseline_reranker` 选择 Top3，并把完整 `provenancePath / sourceKind / verificationStatus / sourceTrust / role` 送入 scenario skills。未验证 `server-tasks/AI_Agent_草稿` 仍不能教给 AI。本地真实检索继续命中维护库 `百度网盘打印 / 6寸照片打印 / 证件扫描` 等，不含当前需求专用硬编码。
- 正则抽取结果改为 `candidateOnly=true / strict=false / required=false / relationship=unknown`，`businessFlow=[]`。它只保存显式需求入口供 AI 输出后的覆盖审计，不再扁平为顺序路径，也不再显示“业务主链约束”。
- 只有 MM 的 requirement/scenario AI 真正成功、业务场景包含完整路径与可见检查点、且覆盖原始候选后，PLAN 才升级为 `source=platform_mindmap_ai / agent-business-plan-v3 / strict=true`。同级入口以独立 `businessFlows` 保存。
- MM 核心 skills 返回本地兜底时自动重试一次；两次仍失败则 PLAN 终态失败，后续 YAML/Runner 不执行。规则候选不会再生成一个看似成功的计划，也不会要求人工来判断是否继续。
- PLAN 生成的结构化 cases/scenarios/视觉结果保存为 `mindmapPlan`，YAML 阶段通过 `preparedCasesPayload` 复用，不重复发送同一批 Figma/截图；后续 coverage auditor、executable planner、YAML 校验、风险和固定设备门禁继续执行。
- UI 将启动前接口明确显示为“启动前预览”，没有真实 AI 结果时不再展示旧平台步骤为业务计划；运行详情显示 MM skills、Figma 页/图、视觉送 AI 状态和失败原因。
- 启动前预览进一步把规则抽取结果放入 `requirementCandidates`，并固定返回空 `businessFlows / steps`；界面只显示“需求显式候选（非业务路径）”，真实业务分支必须等 MM AI 完成后才出现。

设计上采用“模型负责可变业务推理，代码 guardrail 只验证输出并在失败时 tripwire”的边界，和 OpenAI Agents 的 output/tool guardrail、trace 分层一致；不再把 guardrail 的候选输入反向当成模型结论。参考：[OpenAI Agents guardrails](https://openai.github.io/openai-agents-python/guardrails/)、[OpenAI Agents tracing](https://openai.github.io/openai-agents-python/tracing/)。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/config.py task_server/schemas.py tests/backend_static_checks.py
node --check js/state.js
node --check js/agent-workbench.js
python3 tests/undefined_name_checks.py
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
python3 tests/ai_gateway_static_checks.py
python3 ai_skills/evals/run_skill_evals.py
git diff --check
```

结果：后端 `61` 项、前端 `65` 项、AI Gateway `46` 项通过，undefined-name 通过，AI skill contract fixtures `3/3` 通过。定向行为检查覆盖：同级分支不扁平；真实 MM 计划升级 strict constraint；prepared Figma provenance 保留；可信 6 寸基线可进入 MM 上下文；核心 AI 两次兜底后显式失败。本轮未修改 `router.py`、未新增执行模式、未修改历史 YAML，也未触碰用户已有 dirty 文件。

本提交部署后必须重新运行同一需求/Figma和固定 `win-runner-01 / ecbfd645`。首先人工核对 PLAN 是否为 `platform_mindmap_ai`、Figma 4 页/4 图是否送入并完成视觉批次、Top3 是否包含可信相邻路径基线；随后才继续检查 YAML、首批 smoke、remaining 和所有 Runner 报告终态。

### 2026-07-13 Agent 业务计划、可信基线与失败证据闭环优化

部署 `3f14956` 后继续真实验证任务：

- Agent `agent-1783936219379-9a464b80`，固定 `scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`，App 为 `小白学习打印 / com.xbxxhz.box`。
- 2026-07-13 19:34 再次确认公网 `8088 -> 8091` 和直连 `8091` 的 `/api/health` 均为 `ok=true`，文本/视觉模型均为 `qwen3.6-plus`。本次 Agent 的执行前体检记录指定 `win-runner-01` 和 `ecbfd645（OPPO PHM110）` 在线；所有 dry-run、正式任务和安全重跑均只使用该 OPPO，没有选择第二台设备。
- Figma 解析为 4 页/4 图，`sentToAiForJudgement=true / aiJudgementCompleted=true / aiJudgementStatus=completed`，视觉资料确实送入 AI；Figma/截图仍是软参考。
- 生成 5 条自动化用例、8 个场景、5 份业务 YAML，并补充 1 份入口短链路；6 个 refs 均通过静态/可执行准入。旧 PLAN 仍只展示 8 个平台生命周期步骤，业务约束也只展开了文档打印，证明第一步并非真正的 AI 业务计划。
- 首批固定 OPPO 执行事实：
  - `job_1783936878166_00002`，文档打印页百度网盘入口校验成功；报告保留了 `本地文档 / 百度网盘 / QQ文档 / WPS文档` 的真机页面证据。
  - `job_1783937019636_00005`，5 寸照片用例首轮在模型请求被中止后 300 秒超时；Runner `failureReview=env_issue / model_service / confidence=0.96`，这是环境故障，不应修改 YAML。
  - 扫描复印 smoke dry-run `job_1783936878192_00003` 等待报告超时且无失败证据，被正确标为 `inconclusive / formalDispatchSkipped`，没有误记为 YAML 失败，证明 `3f14956` 的不确定态语义已在线生效。
  - 安全重跑 `job_1783937374337_00006` 仍在同一 `ecbfd645` 串行执行，得到新的脚本证据：首个照片打印页面只看到“照片打印/热门素材”等内容，无法直接定位“5寸照片”。报告关键帧显示真实路径应继续点击页面内“照片打印”，再进入规格页选择“5寸照片”。
- Agent 最终 `FAILED / RERUN`。remaining 没有执行，因为 smoke 未形成稳定通过结论。旧失败分析正确识别首轮模型环境故障，但没有消费安全重跑产生的新页面证据，也曾建议切换其他设备；平台实际未切换设备，但 AI 建议本身也必须受固定设备约束。

根因不是“5寸照片”单点，而是 Agent 自主决策链存在通用断点：

1. PLAN 调用了不匹配的旧接口并长期退化为平台生命周期，AI 没有真正输出分支、页面路径、验收点和 smoke/remaining 策略。
2. 基线缓存把运行目录中的未验证 Agent 草稿和维护库样本同等使用；相同 YAML 按首次扫描去重时还可能保留弱来源、丢掉维护库来源。关键词相同的旧草稿会挤掉同业务分支的相邻规格路径基线。
3. executable planner 的 AI 路径计划只记录元数据，不会覆盖后续用例步骤；模型即使识别出中间父页面，YAML 仍可能沿用错误短路径。
4. 失败修复只发送文本日志，未把 Midscene 报告关键帧、Runner failureReview 和可信 Top3 基线一起交给 AI；安全重跑产生的新证据也不会再进入分析/修复。
5. Runner failureReview 未完整保留到 Agent 等待结果；smoke 选择排除项与真实 dry-run 失败混在一起，影响失败归因和 remaining 门禁解释。

本轮通用修改：

- PLAN 改为真实调用所选模型的 `/ai/chat`，输出结构化 `businessFlows / checks / coverage / unknowns / executionStrategy`；平台生命周期单独展示。计划必须覆盖需求中的全部业务分支，失败后允许模型自我纠正一次，仍不合格才显式回退。快速预览通过通用“入口在某页：A、B、C”句式抽取任意同级分支，不硬编码当前产品分支。
- 上游 AI 业务计划继续传入用例/YAML 生成链路，但原始需求仍是硬范围；计划中的 unknown/假设不会被确定性解析器升级为新需求。
- 基线缓存增加来源信任：`verified_execution` 为真实执行成功，`maintained_library` 为维护库，显式审批运行副本为 `approved_runtime`，未验证工作副本不可教给生成/修复 AI。相同内容去重时保留信任更高的来源，并记录完整 `server-tasks-all/...` provenance。
- 基线 AI 重排要求 Top3 角色互补：父页面导航路径、能力模式、稳定等待/断言；尺寸/模板/规格等叶子项可组合相邻规格路径基线。planner 只能引用本次候选真实 ID/路径；有来源的 AI flow 会真正替换原 case 路径，编造 ID 的计划不能改步骤或进入 smoke。
- 失败分析与修复现在最多附带 6 张 Midscene 报告关键帧，并同时发送原 YAML、Runner 日志、failureReview、可信 Top3 基线和固定 Runner/设备约束。AI 只能生成最小语义修复；编造基线、只堆 sleep、等价/no-op 或 YAML 校验失败都不能自动重跑。
- 安全重跑产生新失败后，Agent 只消费最新尝试证据；若最新归因明确为 `SCRIPT_ISSUE` 且 AI 返回 `canAutoRepair=true`，允许再进行一轮关键帧分析、可信基线修复和原设备串行验证。该闭环最多 1 轮、总尝试最多 3 次；环境、产品和未知问题不改 YAML，不会形成无限重试。
- `executionConstraint` 明确传给失败分析/修复 AI。固定设备时 `allowOtherDevices=false`，禁止 AI 建议或 YAML 选择、切换、并发第二台手机。
- Runner 高置信 failureReview 才能覆盖文本推断；模型请求中止/模型服务/设备离线单独归为环境。低置信 review 不能覆盖明确的定位失败。`selectionExcluded` 与 `dryRunBlocked` 分开记录，`failure_review` 保留到 wait 结果。
- coverage 缺口会扣除已经由真实 refs 映射的过期 `REQ` 报告，但真实未覆盖需求仍阻断；达到 3/5/8 数量上限不代表覆盖完成，应合并重复 case。显式多端展示需求可生成设备无关、真实可见文字定位的复用 case，其他未执行形态进入 manual，不在 YAML 内选择设备或使用坐标。
- Agent 页面展示 AI 计划来源/模型、业务分支、验收点、unknowns、平台门禁和“重跑后 AI 闭环”，不再把通用状态机冒充业务计划。

设计取舍参考了成熟 Agent 的共同做法：AI 负责可变的推理与规划，确定性 guardrail 负责不可绕过的安全边界；全链路记录输入、决策、工具和结果；失败修复使用有终止条件的 evaluator loop。参考：[OpenAI Agents tracing](https://openai.github.io/openai-agents-python/tracing/)、[OpenAI Agents guardrails](https://openai.github.io/openai-agents-python/ref/guardrail/)、[AutoGen termination](https://microsoft.github.io/autogen/dev/user-guide/agentchat-user-guide/tutorial/termination.html)、[Anthropic agent evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)。

离线基线重放：

- 缓存 66 条，可信 62 条；`AI_Agent_草稿` 4 条均为未验证来源，可信数为 0。
- 查询“照片打印 5寸照片 百度网盘入口 同级展示 文案 可达页面”的 Top3 包含：
  - `server-tasks-all/小白学习基线用例-基础打印/百度网盘打印.yaml`
  - `server-tasks-all/小白学习基线用例-基础打印/6寸照片打印.yaml`
  - `server-tasks-all/小白学习基线用例-基础打印/照片拼版.yaml`
- 6 寸基线保留 `照片打印 icon -> 照片打印 -> 6寸照片` 的父级页面路径，能作为同分支相邻规格导航证据；平台没有写“5寸必须怎样走”的需求特判。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/ai_skill_service.py task_server/services/job_service.py task_server/services/yaml_baseline_cache.py task_server/services/yaml_execution_plan.py task_server/services/yaml_service.py tests/backend_static_checks.py
node --check ai-gateway/server.js
node --check js/agent-workbench.js
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
python3 tests/ai_gateway_static_checks.py
git diff --check
```

结果：后端 `61` 项、前端 `65` 项、AI Gateway `46` 项全部通过；离线真实 AI PLAN 模拟验证了 `/ai/chat`、三分支、模型 provenance 和计划门禁。未修改 `router.py`，未新增执行模式，未修改历史 YAML；用户已有 dirty 的历史 YAML、`sonic_service.py`、`yaml_executable_scorer.py`、本地 Windows Runner 服务脚本和 `server-tasks/AI_Agent_草稿/` 均未纳入本轮改动。

本轮新提交部署前不能宣称线上闭环已通过。部署后必须继续使用同一需求/Figma和固定 `win-runner-01 / ecbfd645` 发起完整 Agent 回归，人工复核业务 PLAN、最终 YAML、关键帧、首批与 remaining 的真实报告，持续轮询到 `DONE / FAILED / CANCELLED`。

### 2026-07-13 完整回归 Runner dry-run 超时归因修复

部署 `d0516f3` 后继续真实验证任务：

- Agent `agent-1783934517395-33c20197`，固定 `scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`，App 为 `小白学习打印 / com.xbxxhz.box`。
- 公网 `8088 -> 8091` 健康，AI Gateway 正常；Task Server 模型为 `qwen3.6-plus`。
- 生成阶段已通过：Figma 4 页/4 图解析成功，视觉 AI `sentToAiForJudgement=true / aiJudgementCompleted=true / aiJudgementStatus=completed`；6 个 YAML refs 均为 `executable`。
- 执行预检通过：指定 Runner `win-runner-01` 和固定 OPPO `ecbfd645（OPPO Reno9 / PHM110）` 在线；首批 3/3 本地 dry-run 通过，未选择第二台设备。
- Runner 阶段创建了 2 个正式本地任务，均在 `ecbfd645` 上成功：
  - `job_1783934990328_00002`：`00-文档打印百度网盘入口可见性短链路冒烟.yaml`，报告 `http://101.34.197.12:8088/reports/00-%E6%96%87%E6%A1%A3%E6%89%93%E5%8D%B0%E7%99%BE%E5%BA%A6%E7%BD%91%E7%9B%98%E5%85%A5%E5%8F%A3%E5%8F%AF%E8%A7%81%E6%80%A7%E7%9F%AD%E9%93%BE%E8%B7%AF%E5%86%92%E7%83%9F-job_1783934990328_00002.html`
  - `job_1783935088325_00004`：`01-文档打印页百度网盘入口可见性及相对位置校验（本地文档之后第2位）.yaml`，报告 `http://101.34.197.12:8088/reports/01-%E6%96%87%E6%A1%A3%E6%89%93%E5%8D%B0%E9%A1%B5%E7%99%BE%E5%BA%A6%E7%BD%91%E7%9B%98%E5%85%A5%E5%8F%A3%E5%8F%AF%E8%A7%81%E6%80%A7%E5%8F%8A%E7%9B%B8%E5%AF%B9%E4%BD%8D%E7%BD%AE%E6%A0%A1%E9%AA%8C%EF%BC%88%E6%9C%AC%E5%9C%B0%E6%96%87%E6%A1%A3%E4%B9%8B%E5%90%8E%E7%AC%AC2%E4%BD%8D%EF%BC%89-job_1783935088325_00004.html`
- Agent 最终 `FAILED / RUN_SONIC`，原因是第 3 个首批 Runner dry-run（`5寸照片页百度网盘入口并列展示校验`）等待报告 120 秒超时，无失败结果但未及时回传；平台把它计为 `YAML dry-run 未通过`，并把 remaining 停止。

根因：

- Runner dry-run 等待报告超时且没有失败证据时，平台不应归因为 YAML 不可执行。更合理的分层与成熟 CI/Runner 系统一致：脚本断言失败、YAML 解析失败、定位失败、Runner/报告回传超时要分开归因。GitHub Actions/Jenkins 一类系统也会区分 test failure、runner lost、artifact upload timeout/report collection timeout，避免把基础设施不确定性污染为测试脚本失败。
- 本轮两个正式 Runner 任务已成功，说明固定 OPPO、Runner 下发、报告回传主链整体可用；第 3 个 dry-run 是“无失败但报告等待超时”的不确定态，不应计入 `dryRunBlocked`，也不应显示为“3 个 YAML 未通过”。

已修改：

- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- Runner 真实 dry-run 等待报告超时但没有 failed 结果时，标记为 `inconclusive / formalDispatchSkipped`，不创建该正式 Runner 任务，但也不加入 `dryRunBlocked`，不再归因为 `YAML dry-run 未通过`。
- RUN_SONIC 汇总新增 `inconclusiveCount` 和 `inconclusive` 列表，分别展示真正拦截和 Runner dry-run 不确定结果。
- 首批/remaining 门禁仍保留：真正 dry-run failed、YAML 静态失败、定位/脚本硬失败仍阻断扩展；不确定 dry-run 不会被误记为脚本质量失败。
- 未修改 `router.py`，未新增执行模式，未修改历史 YAML，未触碰用户已有 dirty 的 `yaml_executable_scorer.py`、`sonic_service.py` 和历史任务文件。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_execution_plan.py tests/backend_static_checks.py
python3 - <<'PY'
from tests.backend_static_checks import check_generated_yaml_short_guards_and_execution_level_floor
check_generated_yaml_short_guards_and_execution_level_floor()
PY
python3 tests/backend_static_checks.py
git diff --check
```

结果：定向检查通过，后端静态检查 `61` 项通过。部署后需要再次用同一需求/Figma和固定 OPPO `ecbfd645` 跑完整回归；重点确认 dry-run 不确定态不会被显示为 YAML 不通过，并继续观察 remaining 是否在首批成功后执行。

### 2026-07-13 完整回归确认门禁与多端展示映射修复

部署 `d4a7b3e` 后继续真实验证任务：

- Agent `agent-1783933420084-86171325`，固定 `scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`，App 为 `小白学习打印 / com.xbxxhz.box`。
- 公网 `8088 -> 8091` 健康，Task Server、AI Gateway 正常；Task Server 模型为 `qwen3.6-plus`。本轮没有进入 Runner，没有向任何设备下发任务。
- 路由继续正确进入 `new_requirement_source / generate_draft`；Figma 源解析为 4 页/4 图。
- 视觉资料已送 AI 判断并完成：`sentToAiForJudgement=true / aiJudgementCompleted=true / aiJudgementStatus=completed`，Figma/截图仍作为软参考。
- 生成阶段产出 5 个自动化候选、8 个场景，但 scope gate 把 `REQ-005 多设备形态适配` 对应的“宽屏设备下百度网盘入口横向列表滑动可见性验证”误移到 manual，最终只生成 4 个 YAML。
- 4 个 YAML 在 `yaml_service` 生成分级中均为 `executable`，但 Agent 最终确认 `_confirm_agent_yaml_files` 重新本地评分后，对 `replanRisk=high / baselineEvidence=false` 的 generated YAML 再次降级为 `needs_review`，导致 `GENERATE_YAML` 阶段 `FAILED`。
- 根因是两处通用规则不一致：生成分级已允许明确映射需求点的低风险展示/位置/同级校验进入 Runner，但确认门禁重复应用更保守降级；scope gate 对“多设备形态适配”与“宽屏/手机展示一致性”的显式 REQ 映射追溯过窄。

已修改：

- `task_server/services/agent_service.py`
- `task_server/services/yaml_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- Agent 确认阶段保留生成阶段已声明为 `executable`、本地评分仍为 `executable`、分数大于等于 80、静态校验通过、范围审查通过的 generated YAML，不再仅因“高重规划风险且缺少成功基线”重复降级；显式 `needs_review/draft/manual`、范围审查失败、静态校验失败仍不会被提升。
- scope gate 增加通用展示适配词追溯：显式映射到当前 `REQ-xxx` 的文案/展示/可见性/位置/多端/多设备/宽屏/手机/横向滚动类需求，不因“多设备形态适配”和“宽屏设备”分词不完全一致而被移到 manual。
- 未修改 `router.py`，未新增执行模式，未修改历史 YAML，未触碰用户已有 dirty 的 `yaml_executable_scorer.py`、`sonic_service.py` 和历史任务文件。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 - <<'PY'
from tests.backend_static_checks import check_generated_yaml_short_guards_and_execution_level_floor, check_generated_yaml_semantic_scope_and_visual_trace
check_generated_yaml_short_guards_and_execution_level_floor()
check_generated_yaml_semantic_scope_and_visual_trace()
PY
python3 tests/backend_static_checks.py
git diff --check
```

结果：定向检查通过，后端静态检查 `61` 项通过。部署后需要再次用同一需求/Figma和固定 OPPO `ecbfd645` 跑完整回归；预期 5 条自动化用例应能完整生成/确认 YAML，再进入首批 3 条 Runner 冒烟，冒烟通过率达标后继续 remaining。

### 2026-07-13 完整回归首页恢复动作与视觉计数修复

部署 `8de0541` 后继续真实验证任务：

- Agent `agent-1783929144616-0db0f2ad`，固定 `scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`，App 为 `小白学习打印 / com.xbxxhz.box`。
- 线上 `8088 -> 8091` 健康，Task Server、AI Gateway、Sonic 均健康；模型为 `qwen3.6-plus`；平台状态显示 1 个 Runner 在线、2 台设备记录。本轮没有进入 Runner，也没有向第二台设备下发任务。
- 路由继续正确进入 `new_requirement_source / generate_draft`，未复用历史 YAML；Figma 源解析为 4 页/4 图。
- 视觉校准已送 AI 判断，但第 1 批在 `qwen3.6-plus` 900 秒超时，状态为 `failed`。Figma/截图仍是软参考，失败原因被保留，没有作为硬门禁直接阻断 Runner。
- 覆盖门禁继续生效：生成 5 条自动化用例、8 个场景、5 个 YAML，但最终只确认 3 个 YAML，Agent 在 `GENERATE_YAML` 阶段 `FAILED`，没有进入 Runner。
- 根因定位后没有放宽评分门槛：5 份 YAML 静态校验均 executable，但 01/03/05 在可执行性评分中被降级。离线重放线上 YAML 发现 01/03 的真实问题是 `launch` 后使用 `ai: 回到首页` 这种 Midscene 自动规划动作，随后直接 `aiTap`，触发“aiTap 前缺少就近等待”和“复合 ai 动作”风险；这符合 Midscene 官方语义中 `ai()` 会自动规划、明确目标时应优先使用即时动作/等待的原则。05 是跨三页长链路，仍应保持 draft，不自动下发。

已修改：

- `task_server/services/yaml_service.py`
- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 扩展已有 `repair_generated_yaml_executable_gate_issues`，把生成 YAML 中 `ai/aiAction/aiAct: 回到首页/返回首页/确保首页...` 规范为 `aiWaitFor: App 首页加载完成，主要入口或底部导航可见...`，并结合下一步 `aiTap` 提取可见入口名。该修复只改变动作语义，不新增用例、不补断言、不降低 scorer。
- 线上失败 YAML 离线重放结果：01 从 `70 / needs_review` 修复为 `100 / executable`；03 从 `40 / draft` 修复为 `70 / needs_review`，随后由上一轮“明确需求映射的低风险可见 UI 文案/展示校验”通用纠偏进入 executable；05 修复后仍因 19 个动作、跨三页长链路保持 draft。
- 视觉报告计数改为取 `sourceContext` 和生成 summary 的最大 Figma 图数。视觉批次失败只返回部分 asset 时，报告仍显示真实解析的 Figma 4 图，避免误导为“只送了 1 张图”。
- 未修改 `router.py`，未新增执行模式，未修改历史 YAML，未触碰用户已有 dirty 文件，也未修改已有 dirty 的 `yaml_executable_scorer.py`。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 - <<'PY'
from tests.backend_static_checks import check_agent_executable_gate_invokes_ai_rewrite, check_agent_quality_report_uses_figma_visual_reference
check_agent_executable_gate_invokes_ai_rewrite()
check_agent_quality_report_uses_figma_visual_reference()
PY
python3 tests/backend_static_checks.py
git diff --check
```

结果：定向检查通过，后端静态检查 `61` 项通过。部署后需要再次用同一需求/Figma和固定 OPPO `ecbfd645` 跑完整回归；预期 01/02/03/04 加合成入口冒烟可以完整进入可执行 refs，05 跨页长链路继续保留 draft/人工复核，首批 3 条在固定 OPPO 执行后再检查 remaining 终态。

### 2026-07-13 完整回归生成分级误判修复

部署 `3ab39f3` 后继续真实验证任务：

- Agent `agent-1783924700909-8f2ab6ba`，固定 `scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`，App 为 `小白学习打印 / com.xbxxhz.box`。
- 线上 `8088 -> 8091` 健康，Task Server、AI Gateway、Sonic 均健康；模型族为 `qwen3.6-plus`；平台状态显示 1 个 Runner 在线、2 台设备记录。本轮没有向第二台设备下发任务。
- 路由继续正确进入 `new_requirement_source / generate_draft`，Figma 4 页/4 图解析成功；视觉校准已送 AI 判断并完成，截图/Figma 仍作为软参考。
- 覆盖门禁生效：生成阶段产出 5 条自动化用例、8 个场景和 5 个 YAML 文件，但只确认 4 个可执行 YAML；需求点 5 个、可执行 YAML 覆盖不足，因此 Agent 在 `GENERATE_YAML` 阶段 `FAILED`，没有进入 Runner。
- 根因定位：两条明确映射 `REQ-004` 的低风险可见文案/展示校验，被可执行性评分中的泛化诊断“异常/边界/鲁棒性扩展缺少成功基线依据”降为 `needs_review`。这不是百度网盘专用问题，而是“需求明确要求的 UI 文案/展示一致性”被误归为边界/鲁棒性扩展。

已修改：

- `task_server/services/yaml_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 在生成分级阶段新增通用纠偏：仅当用例明确映射当前 `REQ-xxx`/需求点、范围审查通过、非本地超时兜底、生成用例未声明 `needs_review/draft/manual`、且内容是低风险可见 UI 文案/展示/位置/同级/布局校验时，才允许纠正“缺少成功基线依据”的泛化 `needs_review` 诊断，交给 Runner 和视觉 AI 实际判断。
- 本地兜底、范围不匹配、静态校验失败、固定坐标、抽象 UI 目标、高风险、缓存/断网/加载中点击/超时/重试等非展示类鲁棒性扩展仍保持 `needs_review`，不会自动下发 Runner。
- 生成分组的 score/reasons 同步写入纠偏原因，避免 UI 上仍显示为降级；确认阶段“不能把生成阶段 `needs_review/draft` 提升为 executable”的保护不变。
- 未修改 `router.py`，未新增执行模式，未修改历史 YAML，未触碰用户已有 dirty 文件。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 - <<'PY'
from tests.backend_static_checks import check_generated_yaml_semantic_scope_and_visual_trace
check_generated_yaml_semantic_scope_and_visual_trace()
PY
python3 tests/backend_static_checks.py
git diff --check
```

结果：定向检查通过，后端静态检查 `61` 项通过。部署后需要再次用同一需求/Figma和固定 OPPO `ecbfd645` 跑完整回归；预期 5 条自动化用例应能完整确认为可执行 YAML，首批 3 条冒烟通过后继续执行 remaining，并人工复核最终 YAML 是否覆盖文档打印、照片打印、扫描复印以及多端/文案要求。

### 2026-07-13 完整回归真实验证后的覆盖门禁修复

部署后真实验证任务：

- Agent `agent-1783922695359-39c38f0c`，固定 `scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`，App 为 `小白学习打印 / com.xbxxhz.box`。
- 线上 `8088 -> 8091` 健康，Task Server、AI Gateway、Sonic 均健康；模型为 `qwen3.6-plus`；平台状态显示 1 个 Runner 在线、2 台设备记录。预检真实识别指定 Runner 和固定 OPPO `ecbfd645 / OPPO Reno9 / PHM110` 在线，没有选择第二台设备。
- 路由正确进入 `new_requirement_source / generate_draft`，未复用历史 YAML；Figma 4 页/4 图解析成功，视觉校准批次 `1/1` 已送 AI 并继续进入 coverage auditor。
- 生成阶段显示“用例 5 条，场景 8 个”，但最终只确认 2 个 YAML：入口短链路冒烟和 `REQ-001` 文档打印用例；照片打印、扫描复印、多端/文案要求没有对应可执行 YAML，也没有 remaining 扩展任务。
- 首批真实 Runner 只在固定 OPPO 上执行。第一条入口短链路成功；第二条文档打印用例失败，失败原因是断言把其他页面/相邻业务分支的同级控件混入文档打印页同级关系，真实截图显示目标入口可见但同级控件集合与断言不一致。安全重跑也在同一 OPPO 上执行，最终 Agent `FAILED / COLLECT_REPORT`。

已修改：

- `task_server/services/agent_service.py`
- `task_server/services/yaml_service.py`
- `ai_skills/prompts/automation_filter.v1.md`
- `ai_skills/prompts/executable_yaml_planner.v1.md`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 新增通用 Agent 覆盖缺口门禁：完整回归中，生成自动化用例数、需求点数、最终确认 YAML refs 数和生成分组不一致时，`qualityReport` 标为 blocked，生成阶段不再自动确认进入 Runner；执行前体检也会用 `generated_yaml_coverage_gate` 阻断。
- YAML 生成分组现在会把“已进入自动化 cases 但未生成对应 YAML 文件”的用例记录为 `needs_review_cases`，保留缺口证据，不再只展示已生成的 YAML 文件。
- `automation_filter` 和 `executable_yaml_planner` prompt 增加通用 AI 证据约束：位置、顺序、同级、文案一致性断言必须来自同一页面路径、同一业务检查点以及当前需求/页面知识/Figma/截图同页证据；证据不足进入 `needs_review_cases` 或 `manual_cases`。未写针对单一需求的业务词硬编码。
- 没有修改 `router.py`，没有新增执行模式，没有修改历史 YAML。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 - <<'PY'
from tests.backend_static_checks import check_agent_blocks_incomplete_generated_yaml_coverage, check_generated_yaml_semantic_scope_and_visual_trace
check_agent_blocks_incomplete_generated_yaml_coverage()
check_generated_yaml_semantic_scope_and_visual_trace()
PY
python3 tests/backend_static_checks.py
git diff --check
```

结果：新增定向检查通过，后端静态检查 `61` 项通过。部署后需要再次用同一需求/Figma和固定 OPPO `ecbfd645` 跑完整回归；预期如果仍只生成 2 个 YAML，会在生成/预检阶段阻断而不会下发 Runner；如果生成 5 条完整可执行 YAML，再继续验证首批和 remaining 终态。

### 2026-07-13 完整回归视觉输出、需求映射与取消生命周期修复

部署 `8809f73` 后真实验证任务：

- Agent `agent-1783919922418-a1cbde3c`，继续使用原需求和 Figma，固定 `scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`。
- 新需求路由正确，`automation_filter.v1` 在 150 秒预算内成功完成；统一数量计划为 `min/target/max=5/5/5`、首批 3 条，未再回退到 8 条，也未把 Figma 内部页名追加为需求点。
- 视觉资料确实送入 AI，但视觉模型只返回了判断上下文、遗漏 schema 必填的 `cases`，导致第 1 批报 `$.cases 为必填字段`，整批视觉校准被记为失败。
- AI 原始结果含文档、照片、扫描和多端文案需求；范围校验却用全量需求点开头的文档打印关键词审查 `REQ-003` 扫描用例，把扫描用例错误降级。移动端文案校验又因标题含“一致性”被当成需求外鲁棒性场景，最终只生成 3 份 YAML，未完整覆盖 5 个需求点。
- Agent 进入固定 OPPO `ecbfd645` 的首批 Runner：第一条文档打印用例真实执行成功；发现生成范围不完整后主动取消 Agent，第二条 Runner 任务随后手工取消。没有向另一台设备下发本轮任务。
- 取消过程暴露生命周期缺口：Agent 原有取消逻辑只处理生成进度任务，不会级联取消已经创建的 Runner 子任务。

已修改：

- `task_server/services/ai_skill_service.py`
- `task_server/services/yaml_service.py`
- `task_server/services/agent_service.py`
- `task_server/services/job_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- `visual_grounder` 继续真实调用视觉 AI；如果模型只返回视觉判断而遗漏 `cases`，schema 校验前保留原始自动化用例。截图/Figma 仍为软参考，不会因视觉输出字段缺失而删除业务用例或把整批判失败。
- 视觉 review 记录保留策略、输入用例数和输出用例数，后续可以区分“AI 没有重写用例”和“视觉调用没有执行”。
- 生成范围校验先读取用例的 `REQ-xxx` 映射；有明确映射时只使用对应需求点做强关键词追溯，未带编号时再按业务主题映射，不再默认受首个需求点支配。
- 取消对所有“一致性”用例的泛化拦截，只继续拦截返回状态一致性、缓存、超时、干扰等需求未声明的鲁棒性扩展；明确映射到需求的多端/文案一致性可以进入后续 YAML 准入。
- Agent 取消会按 `parent_run_id` 级联标记所有非终态 Runner job 为 `cancelled`，并把取消数量和 job ID 写入 Agent artifacts；首批、扩展、通用工具和安全重跑创建的 Runner job 都写入父 Agent ID。
- Runner 下发和安全重跑循环在创建每个 dry-run/正式任务前检查 Agent 取消标记，取消后不再继续创建后续任务。
- 没有修改 `router.py`、没有新增执行模式、没有修改历史 YAML，也不需要替换 Windows Runner。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/ai_skill_service.py task_server/services/job_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 -c 'from tests import backend_static_checks as checks; checks.check_generated_yaml_semantic_scope_and_visual_trace(); checks.check_agent_cancel_cascades_runner_jobs()'
python3 tests/backend_static_checks.py
git diff --check
```

定向检查通过；后端静态检查 `61` 项通过。部署后仍需使用同一需求/Figma和固定 OPPO `ecbfd645` 再跑一次完整回归，重点核对视觉批次完成、扫描/照片/文档需求均有对应 YAML、首批冒烟成功后 remaining 扩展全部执行到终态，并人工复核最终 YAML 是否符合业务需求。

### 2026-07-13 完整回归筛选超时与数量决策冲突修复

部署后真实验证任务：

- Agent `agent-1783917542885-a2a4a781`，继续使用原需求、Figma 4 页/4 张 UI 图，固定 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`。
- 新需求路由、Figma 解析和视觉判断均正常；视觉批次 `1/1` 完成，未向另一台设备下发任务。
- 任务在 `GENERATE_YAML` 安全终止，没有创建真实 Runner 任务。直接原因是 `automation_filter` 在 150 秒内未返回，8 条本地兜底 YAML 全部按门禁保持 `needs_review`。
- 根因不是单一超时值：前置 `execution_scope_planner` 已规划 5 条，但 skills、coverage 和 smoke 又各自重新计算为 8 条；同时 `_ensure_rich_generation_scope` 因资料长度较大，把 Figma 内部页名“首页 / 文档打印首页备份 2 / 引导1”追加成硬验收点，触发额外 coverage repair，后者又超时 180 秒。

已修改：

- `task_server/services/ai_skill_service.py`
- `task_server/services/yaml_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- `execution_scope_planner` 经平台收敛后的 3/5/8 结果成为场景设计、automation filter、coverage auditor 和最终 smoke selector 的统一数量约束；需求点数量仍提供最低档位保护，AI 不能把多需求点错误压缩成 3 条。
- scope planner 的 `size` 由平台按最终数量统一计算，避免模型返回 `size=large / targetCaseCount=5` 这种自相矛盾状态。
- automation filter 仍由 AI 执行，但只接收自动化适用性所需的需求点、业务入口、可见结果、风险、场景和最多 6000 字符 Top3 YAML 基线；最终 payload 继续保留完整需求分析。
- automation filter 的输入规模、场景数、目标数和超时值写入 review，后续可直接判断是输入膨胀还是模型服务问题。
- rich requirement/Figma 逻辑不再根据资料长度、Figma 页面名或占位文本伪造 requirement points，也不再因此默认增加第二轮 coverage repair；Figma 保持 AI 视觉软参考。
- automation filter 真超时时，原有 `local_fallback_after_ai_timeout -> needs_review` 安全门禁保持不变，不允许静态评分把兜底用例重新提升为 executable。
- 本轮没有修改 `router.py`、没有新增执行模式、没有修改历史 YAML，也不需要替换 Windows Runner。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 - <<'PY'
from tests.backend_static_checks import check_ai_skills_receive_yaml_reference_context, check_generated_yaml_semantic_scope_and_visual_trace
check_ai_skills_receive_yaml_reference_context()
check_generated_yaml_semantic_scope_and_visual_trace()
PY
python3 tests/backend_static_checks.py
```

结果：定向行为检查通过，后端静态检查 `61` 项通过。使用失败任务摘要离线重放后，保留 5 个真实需求点，统一目标为 5 条、首批 3 条；automation filter 输入收敛为分析约 1856 字符、场景约 4543 字符、基线最多 6000 字符。部署后必须再次用同一需求/Figma和固定 OPPO `ecbfd645` 跑完整回归，并监督生成 YAML、首批冒烟和 remaining 全部到终态。

### 2026-07-13 完整回归生成误判与视觉追踪修复

真实验证任务：

- Agent `agent-1783914434480-a93177cf`，固定参数为 `scope=regression / RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`，使用原需求文档和 Figma 4 页、4 张 UI 图。
- 路由已正确进入 `new_requirement_source / generate_draft`，没有复用历史 YAML；预检只识别并绑定 OPPO `ecbfd645 / Reno9 / PHM110`。
- `automation_filter` 在 90 秒后超时，本地兜底生成 8 条用例；其中出现“进入基础打印-入口一致性相关页面或入口区域”等不存在的抽象 UI 目标，并无需求依据地增加横向滑动。
- 生成范围审查直接扫描 YAML 原文，把每个合法 `aiWaitFor.timeout` 结构字段误判为需求未说明的“超时场景”，导致正常文档/照片/扫描用例被降级。
- Figma/UI 图实际已送入视觉模型，但模型漏返回必填 `analysis`；失败标记又被覆盖率补全和规划阶段覆盖，Agent 页面错误显示为 `skipped_or_pending`。
- 任务仅进入 Windows Runner 的 `yaml_dry_run`，在真实 UI 操作前主动取消；OPPO 没有执行这些错误脚本，另一台设备也未下发。

已修改：

- `task_server/services/ai_skill_service.py`
- `task_server/services/yaml_service.py`
- `task_server/services/agent_service.py`
- `deploy/install-server.sh`
- `deploy/midscene.env.example`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 视觉 skill 在 schema 校验前只补回模型遗漏的原始 `title/module/analysis/scenarios/manual_cases/review`，不伪造 `cases`；Figma 判断缺少 `analysis` 时不再整批失败。
- 视觉调用完成、失败和分批错误信息在覆盖率补全/执行规划后继续保留；Agent 明确区分 `completed / failed / pending / skipped`，并分别显示“已送 AI”和“AI 已完成”。
- 需求范围审查改为解析 YAML 结构，只读取 task 名和 Midscene 动作语义，排除 `timeout` 等结构键；真实动作中的超时场景仍可被识别。
- 新增抽象 UI 目标门禁：把“相关页面或入口区域、入口一致性、跨设备适配、权限与状态”等测试分组当成 `aiTap` 目标时降级，不允许下发 Runner。
- `automation_filter` 超时兜底改为按需求点识别文档打印、照片打印、证件照、照片拼版和扫描复印，通过真实可见入口文字导航，不再默认横向滑动。
- 超时兜底来源在后续 AI 重写后仍强制保持 `needs_review`；静态评分只能保持或降级，不能重新提升为 `executable`。
- `automation_filter` 默认超时由 90 秒调整为 150 秒，部署脚本会把线上仍为 90 秒的旧默认值迁移到 150 秒。
- 依据 Midscene 官方语义动作约束继续使用自然语言 `aiTap / aiWaitFor / aiAssert`，没有引入坐标定位、selector 或新执行模式。
- 没有修改 `router.py`，没有修改历史 YAML，也不需要替换 Windows Runner。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
bash -n deploy/install-server.sh
git diff --check
```

后端静态检查 `61` 项通过。部署后必须用同一需求/Figma、`scope=regression`、固定 OPPO `ecbfd645` 重新执行，并继续监督正式 YAML、首批冒烟和 remaining 全部到终态。

### 2026-07-13 完整回归误复用历史 YAML 修复

真实验证任务：

- Agent `agent-1783911422395-136ac783`，参数为 `scope=regression / qwen3.6-plus / win-runner-01 / ecbfd645 / fixed`，Figma 4 页、4 张 UI 图解析成功。
- 平台错误地把 UI 选择的 `regression` 范围当成“明确复用历史用例”，先匹配了 `小白学习基线用例-基础打印/百度网盘打印.yaml`，没有进入完整需求/Figma生成主链。
- 拒绝复用后又落到通用 AI Gateway 草稿；草稿虽有 5 个 tasks，但包含 Figma 画板名、固定时间 `9:41`、过多 60 秒等待，且没有结构化用例和正式拆分 YAML，不符合执行准入。
- 本轮在 `WAIT_CONFIRM` 主动取消，未创建 Runner job，没有影响 OPPO 或另一台设备。

已修改：

- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- `regression` 只表示完整执行范围，不再单独触发历史 YAML 复用；需求/Figma输入仍进入新需求主链。
- 只有目标文字明确要求回归/基线/复用/已有用例，或来源为失败任务、范围为失败重跑时，才判定为复用意图。
- Figma 页面/图片和上传截图统一作为需要 AI 视觉判断的软参考；保持 `hardGate=false`，但视觉校准未完成时明确显示待复核。
- YAML 草稿质量报告也回退使用 `visualReferenceReport` 的 Figma 数量，不再把已解析的 4 张图显示为 0。
- 增加行为测试，覆盖完整回归新需求、明确复用、失败任务、Figma AI 判断和草稿 Figma 计数。

部署后必须重新发起同一 `regression` 任务，确认 Case Retrieval 直接显示“新需求输入，跳过旧基线复用”，再检查完整用例、拆分 YAML、首批冒烟和 remaining 全部终态。

再次真实验证：

- Agent `agent-1783912200589-4309b260` 已确认路由修复生效：Case Retrieval 为 `new_requirement_source / generate_draft`，未再复用历史 YAML。
- 完整主链产出 8 条自动化用例、1 条人工用例、12 个场景、8 份拆分 YAML；Figma 4 页/4 图已完成 AI 视觉校准，`aiJudgementStatus=completed`。
- 生成结果暴露新的通用问题：横向滑动规范化非幂等，1 次自然语言滑动被多轮修复扩成 8 次 `aiScroll` 和 4 次固定坐标 ADB swipe；生成阶段的 `needs_review/draft` 又在确认阶段被重新评分升级为 executable。
- 质量报告已显示 `blocked / executableTaskCount=0`，Agent 却继续进入 Runner dry-run。本轮在真实 Midscene 执行前取消，没有向 OPPO 下发真实 UI 操作。

继续修复：

- 按 Midscene 官方 `aiScroll(locate, {scrollType: singleAction, direction, distance})` 约束，横向滑动只生成一次语义 `aiScroll`，移除固定坐标 ADB 横滑；规范化改为幂等。
- 启动守卫吸收“启动 App”和“如不在首页则返回”重复步骤；下一个自然语言步骤已有明确等待时，不再额外插入泛化跳转等待。
- 去掉平衡模式下无必要的固定 sleep 和任务末尾 force-stop；下一任务仍在开始时 force-stop，最终报告可以保留业务断言页面。
- 断言目标已有明确 `aiWaitFor` 时不再重复等待；真实 8 条用例重放后，主要自动化从 19-53 个动作降到 9-12 个动作，横向滑动从 8 次降为 1 次。
- Agent 确认阶段以生成阶段级别为上限，只能保持或降级，不能把 `needs_review/draft` 提升为 executable；高重规划且无成功基线的 YAML 自动降为 `needs_review`。
- 完整回归至少需要 1 条正式需求 YAML 达到 executable，不能仅补一条合成冒烟后继续。
- 本轮真实用例本地重放结果：5 条正式需求 YAML executable，加 1 条入口冒烟；宽屏变体和高重规划扫描跳转为 needs_review，跨三页面长链路为 draft，不下发 Runner。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check
```

结果：后端静态检查 `61` 项通过；未修改 `router.py`、未新增执行模式、未修改历史 YAML。部署后需重新跑同一完整回归并持续跟踪全部 executable YAML 终态。

### 2026-07-13 完整需求回归范围分流

问题定位：

- 已成功的 Agent `agent-1783907519406-1cb3572a` 使用 `scope=smoke`，生成和执行计划均只有 1 条：`total=1 / selectedSmoke=1 / deferredExecutable=0 / remaining=0`。
- 入口可见性需求在 Agent 层直接生成单条冒烟；即使跳过 Agent 直接生成，YAML 服务和 AI Skills 仍会根据“百度网盘入口可见”文本自动进入确定性快路径，因此只把 scope 改成 `regression` 仍不能生成完整需求用例。
- 用户要求在已通过 1 条冒烟后继续执行完整需求，正确行为应是：`smoke` 保留稳定单条快路径；`regression/full` 进入完整需求分析、Figma 视觉校准、3/5/8 用例生成和 remaining 分批执行。

已修改：

- `task_server/services/agent_service.py`
- `task_server/services/yaml_service.py`
- `task_server/services/ai_skill_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 新增 Agent 范围门禁：仅 `smoke / 冒烟 / single / 单条` 使用直接入口短链路；`regression` 不再提前返回单条 YAML。
- Agent 向 YAML 生成链同时传递 `forceEntryVisibilityFastPath` 和 `disableEntryVisibilityFastPath`，避免完整范围被下游文本规则重新切回快路径。
- YAML 服务新增统一快路径决策函数；显式 disable 时不执行本地入口快路径、不跳过 AI 基线重排和范围规划。
- AI Skills 用例生成增加 `allow_entry_visibility_fast_path` 参数；完整范围会进入 requirement analyzer、scenario designer、automation filter、smoke selector 和 Figma 视觉校准。
- 完整生成结果仍由现有执行计划控制：首批最多 3 条，达到门禁后分批执行 remaining，不新增执行模式。
- 没有修改 `router.py`，没有修改历史 YAML；本轮只需部署服务端，不需要替换 Windows Runner。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 -c "from tests.backend_static_checks import check_ai_skill_timeout_fallbacks_are_requirement_scoped; check_ai_skill_timeout_fallbacks_are_requirement_scoped(); print('ok')"
python3 tests/backend_static_checks.py
git diff --check
```

结果：范围分流定向检查通过，后端检查 `61` 项通过。部署后下一轮必须使用同一需求/Figma、`scope=regression`、固定 OPPO `ecbfd645`，并跟踪首批与 remaining 全部终态。

### 2026-07-13 Agent 目标页单跳短链路与失败类型归一

部署后真实验证：

- 服务端 `8091 / 8088` 健康检查通过；Windows Runner 心跳确认 `2026.07.10-model-family-v4`，模型配置为 `qwen3.6-plus / qwen3.6`。
- OPPO `ecbfd645 / Reno9 / PHM110` 在线，`com.xbxxhz.box` 为 `4.45.0`。发起 Agent 前先等待用户侧华为 Sonic 任务 `sonic_1783905114419` 成功结束，避免两台设备并行干扰。
- Agent：`agent-1783905536792-3729b303`，固定 `win-runner-01 / ecbfd645 / fixed`；需求文本和 Figma 链接均复用上一轮，Figma 4 个页面、4 张 UI 图全部读取成功。
- 生成 YAML 为单条 `executable / 100` 冒烟，Runner job 为 `job_1783905613659_00002`，只绑定 OPPO。
- Qwen3 坐标协议修复已被真机验证：同一视觉框最终执行 `adb -s ecbfd645 input ... 138 318`，不再是旧错误坐标 `(240,267)`；点击后真实进入了标题为“文档打印”的目标页面。
- 本轮失败不是坐标或设备问题。旧模板先用宽泛“打印 / 学习打印 / 小白打印”点击，实际上已直接进入文档打印页，随后却继续等待“同时展示文档打印、照片打印、扫描复印的打印首页”，因此在正确目标页等待错误条件并超时。
- 失败报告把具体类型写成展示标签“等待目标超时”；Agent 只识别 `ENV_ISSUE / SCRIPT_ISSUE / PRODUCT_BUG / UNKNOWN`，因此误判为 `UNKNOWN`。人工确认后后置逻辑又忽略 `unknownFailureConfirmed`，产生第二个相同确认项。本轮已主动取消，避免无效重跑，终态为 `CANCELLED`。

再次部署后最终验证：

- Agent：`agent-1783907519406-1cb3572a`，Runner job：`job_1783907577726_00002`；仍固定 `win-runner-01 / ecbfd645 / fixed`，发起前等待华为 Sonic 任务 `sonic_1783907264077` 成功结束，全程没有两台设备并行。
- 线上实际 YAML 已变为 6 个动作、1 次 `aiTap` 的单跳版本，Runner 执行 93 秒后 `1 成功 / 0 失败`，Agent 最终 `DONE / 100%`，总结结论为“通过”。
- HTML 报告记录 `qwen3.6-plus / qwen3.6 mode`，没有 `qwen2.5-vl mode`；全部 ADB 命令均带 `-s ecbfd645`，目标点击坐标为 `(135,279)`。
- 最终截图标题为“文档打印”，页面入口依次包含“本地文档、百度网盘、QQ文档、WPS文档”；最终断言 `文档打印页面展示百度网盘入口` 返回 `StatementIsTruthy=true`。
- 执行结果正确，但质量报告出现展示不一致：`visualReferenceReport` 已记录 Figma 4 页、4 图，顶层 `figmaImageCount` 和“Figma 解析图片”层却仍为 0，并产生错误缺图警告。

已修改：

- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 入口可见性冒烟改为通用目标页单跳：冷启动 -> 等待首页 -> 按推断出的目标页面文字直接点击 -> 等待目标页展示需求入口 -> 断言。文档打印示例从 8 个动作、2 次跳转缩短为 6 个动作、1 次跳转，不使用固定坐标。
- 移除“先进入打印聚合首页、再进入目标页”的模糊中间跳转，避免目标入口本身被第一次 `aiTap` 命中后又等待错误页面。
- “等待目标超时 / 元素定位失败 / 断言页面不匹配 / 重规划超限 / Runner 单任务超时”等具体展示标签统一归为 `SCRIPT_ISSUE`，并通过 `failureKind` 保留原始细分原因。
- Runner `failure_review` 的 `ENV_ISSUE / PRODUCT_BUG` 仍保持优先；AI 不允许把已确定类型降级回 `UNKNOWN`。
- `UNKNOWN` 人工确认增加一次性门禁；`unknownFailureConfirmed=True` 后不再重复创建确认项。
- 质量报告在直接短链路没有复制 `summary.ui_design_assets` 时，回退使用 `visualReferenceReport.figmaImageCount / ignoredFigmaCount`，避免已解析 Figma 图片被显示为 0 或产生假警告。
- 没有修改 `router.py`、没有新增执行模式、没有修改历史 YAML，也不需要再次替换 Windows Runner。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
python3 -c "from tests.backend_static_checks import check_agent_failure_review_and_repair_guard; check_agent_failure_review_and_repair_guard(); print('ok')"
python3 -c "from tests.backend_static_checks import check_agent_quality_report_uses_figma_visual_reference; check_agent_quality_report_uses_figma_visual_reference(); print('ok')"
python3 tests/backend_static_checks.py
git diff --check
```

结果：新单跳 YAML 为 `executable / 100`；真实 Runner/Agent 全链路通过；百度网盘入口最终断言为真。质量报告 Figma 计数修复通过定向检查，部署该展示修复后不需要替换 Windows Runner。

### 2026-07-10 Qwen3 坐标协议与 Agent 无效修复拦截

部署前真实验证：

- Agent：`agent-1783651627927-666f33ac`，固定使用 `win-runner-01 / ecbfd645`（OPPO），没有向另一台设备下发。
- 需求与 Figma 均被平台读取；Figma 共使用 4 个页面、4 张 UI 图。生成的首批 YAML 为单条 `executable / 100` 短链路，业务步骤为进入打印首页 -> 文档打印 -> 校验百度网盘入口。
- 首跑 `job_1783651720495_00002` 在 300 秒 Runner 上限后失败；重跑 `job_1783652091030_00004` 在 191 秒后明确失败，页面仍停留在首页，未进入文档打印页。
- 两份 Midscene 报告中，模型都正确理解了文字目标并返回约 `[48,122,194,145]` 的框；这不是 YAML 固定坐标，`aiTap` 最终仍需由 Android ADB 执行物理点击。
- 准确根因是服务端配置的模型名为 `qwen3.6-plus`，Runner 却继续下发旧变量 `MIDSCENE_USE_QWEN_VL=1`。Midscene 1.7.10 因而把模型声明成 `qwen2.5-vl mode`，将 Qwen3 的 0-1000 归一化框误当成像素框，最终点击约 `(240,267)`；按 Qwen3.6 协议映射到 `1080x2412` 物理屏后应约为 `(130,321)`。
- 首跑的 Runner `failure_review` 已判定为 `env_issue / model_service`，但 Agent 丢失该字段后误归类为 `SCRIPT_ISSUE`；AI 修复只增加 `sleep` 就重跑，既没有改变定位语义，也浪费了一轮执行。

已修改：

- `task_server/services/runner_service.py`
- `windows-midscene-runner.py`
- `mac-midscene-runner.py`
- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 服务端按模型名显式下发 Midscene 现代配置：`MIDSCENE_MODEL_NAME=qwen3.6-plus`、`MIDSCENE_MODEL_FAMILY=qwen3.6` 及对应 API key/base URL，不再把 Qwen3 声明成旧 `qwen2.5-vl`。
- Windows/Mac Runner 以服务端现代模型配置为准；即使服务进程环境残留旧模型族或 `MIDSCENE_USE_QWEN_VL`，执行前也会清除全部旧模型选择开关。
- Runner 版本更新为 `2026.07.10-model-family-v4`，心跳增加 `midscene_model_name`、`midscene_model_family`，部署后可直接确认真实进程使用的坐标协议。
- Agent 报告收集、失败项归一和 AI 分析全程保留 Runner `failure_review`；环境、模型服务、产品和脚本问题不再统一误判为脚本问题。
- AI 修复候选会做解析后的执行语义比较；只增加 `sleep`、只改用例名/说明或返回等价 YAML 时保存为 `REJECTED` 诊断证据，不允许自动重跑旧脚本。
- 没有修改 `router.py`、没有新增执行模式、没有修改历史 YAML。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/runner_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py windows-midscene-runner.py mac-midscene-runner.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check
```

结果：全部通过，后端检查 `61` 项通过。部署后仍需确认 Windows Runner 心跳为 `2026.07.10-model-family-v4 / qwen3.6-plus / qwen3.6`，再用同一需求和 OPPO 单设备完整跑到终态。

### 2026-07-10 Runner CLI Android 设备配置修复

部署后真实验证：

- `agent-1783645927510-396f4a20` 在 `IMPACT_ANALYSIS` 中断；任务启动后 Task Server 被重启，后台线程丢失，因此已取消该轮并重新发起，不作为 Runner 验收结论。
- `agent-1783646228653-affd528c` 完整通过 `GENERATE_YAML`、`VALIDATE_YAML` 和 `EXECUTION_PRECHECK`，固定使用 `win-runner-01 / ecbfd645`；预检确认物理设备为 `OPPO Reno9 / PHM110`，没有向华为设备下发。
- 生成 YAML 达到 `executable / 100`，共 8 个动作、2 次业务跳转，链路为冷启动 App -> 进入打印首页 -> 进入文档打印 -> 断言百度网盘入口可见，业务方向符合当前需求。
- Runner dry-run 通过，但真实执行和同设备安全重跑均在 Midscene 加载前失败；临时 YAML 被旧 Runner 拼成 `android: {}` 后跟缩进的 `deviceId: ecbfd645`，PyYAML 报 `bad indentation of a mapping entry`。
- 线上心跳仍报告 `runner_version=2026.07.07-stability`，说明 Windows 服务实际运行的脚本没有包含本轮设备注入修复。

`v2` 部署后继续验证：

- `agent-1783647477107-2c4ebefc` 完整通过 YAML 生成、校验和执行前体检，Runner 心跳已确认 `2026.07.10-device-id-yaml-v2`，设备仍固定为 `ecbfd645 / OPPO Reno9 / PHM110`。
- 生成结果为 1 条 P0 可执行冒烟、0 条人工项，评分 `100`；业务步骤是启动 App -> 恢复应用首页 -> 进入小白学习打印首页 -> 进入文档打印 -> 校验百度网盘入口，符合当前需求。
- 真实执行和同设备串行重跑仍在 Midscene 解析前失败，错误 YAML 与上一轮一致，说明已排除“Runner 没替换”，`v2` 修复没有覆盖真实调用路径。
- 准确根因是 `run_job` 调用 `midscene_cli_yaml_text`，其中 `ensure_cli_interface_config` 用正则把合法的块配置 `android:\n  deviceId: ecbfd645` 强制改成 `android: {}`，导致 `deviceId` 变成非法悬空缩进；`ensure_android_device_id` 在真实执行路径中没有被调用。

`v3` 部署后继续验证：

- `agent-1783648885693-5c30d28e` 心跳确认 `2026.07.10-cli-interface-v3`，固定设备仍为 `ecbfd645 / OPPO Reno9 / PHM110`；Runner dry-run 和真实 YAML 解析均通过，日志中的全部 ADB 命令都带 `-s ecbfd645`，没有使用华为设备。
- 首跑 `job_1783648969345_00002` 真正进入 Midscene 页面执行，215 秒后失败；报告截图显示 App 已在正确首页，蓝色“文档打印”卡片清晰可见，但 Midscene 定位返回中心约 `(121,135)`，ADB 实际也点击该位置，未命中文档打印卡片的真实纵向区域，随后等待百度网盘入口超时。
- 自动修复重跑 `job_1783649230612_00004` 仍停在同一首页；修复 YAML 只改了等待/断言文案，没有修正点击根因，180 秒后再次失败。
- Midscene 1.7.10 报告记录物理截图 `1080x2412`、设备 DPR `3`、`shrunkShotToLogicalRatio=3`；DashScope 返回的定位坐标明显对应约 1/2 尺寸。按 Midscene 官方移动端建议，在临时 Runner YAML 设置 `agent.screenshotShrinkFactor: 2`，使模型接收 `540x1206` 截图，再由 Midscene 按比例映射回物理坐标。
- 同轮发现 Agent 调 `/ai/analyze-failure` 时发送的是聚合 `context/failedJobs`，而 AI Gateway 实际读取 `taskName/yaml/log/screenshotDesc`，导致分析结果误报四个字段均为空，降低自动修复质量。

已修改：

- `windows-midscene-runner.py`
- `mac-midscene-runner.py`
- `task_server/services/yaml_service.py`
- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- `ensure_android_device_id` 遇到 Midscene CLI 规范化产生的 `android: {}` 时，先展开为块结构 `android:`，再写入 `deviceId`，作为兼容保护。
- `ensure_cli_interface_config` 改为只把真正没有子配置的空接口头转换为 `{}`；已有 `deviceId` 等缩进字段时原样保留，不再用正则破坏合法 YAML。
- 按 Midscene 官方 YAML/CLI 约定，在临时 YAML 保留 `android.deviceId` 的同时，执行命令增加 `--android.deviceId <selected device>`，形成第二层固定设备绑定，不修改保存的历史脚本。
- Windows Runner 默认版本更新为 `2026.07.10-cli-interface-v3`，后续可直接通过平台心跳确认 Windows 服务是否加载了真实调用路径修复。
- 后端检查实际串联服务端 CLI 下发、Windows/Mac Runner 规范化和执行前守卫，再用 PyYAML 解析最终临时 YAML；同时校验设备配置、root tasks 和唯一 Android 顶层配置。
- Android 固定设备任务的临时 CLI YAML 增加 `agent.screenshotShrinkFactor: 2`；已有显式 Agent 配置优先，保存的 YAML 和历史 YAML 不修改。
- Agent 失败分析增加真实网关字段映射：主失败任务名、原始 YAML、Runner 日志/summary 和截图派生失败描述，同时保留聚合失败列表。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py windows-midscene-runner.py mac-midscene-runner.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check
```

结果：全部通过，后端检查 `61` 项通过。

### 2026-07-09 Agent 入口短链路首页恢复与失败摘要归因修复

继续跟踪部署后的新任务：

- `agent-1783591410278-01390b35`
- 目标：基础打印新增百度网盘入口

结果：

- `GENERATE_YAML` / `VALIDATE_YAML` / `EXECUTION_PRECHECK` 均通过，短链路 YAML 已下发 Runner。
- Runner 正式执行 115 秒后失败，说明不再是 CLI 结构或 Android SDK 环境问题。
- Midscene summary 真实错误：App 启动后停留在“三维创作 / 3D 打印 / 模型推荐”界面，脚本直接定位“文档打印入口”，因此找不到目标入口。
- 自动失败分析仍没有使用 summary 中的真实错误，导致修复草稿只增加 `sleep`，没有解决“先回到基础打印首页”的问题。

问题定位：

- Agent 入口可见性短链路默认假设 `launch` 后已经在小白学习打印首页；但真实设备可能保留在 3D 打印 / 三维创作首页。
- `_agent_runner_job_material` 已读取 `summary.json`，但 `_normalize_failed_execution_item` 没有保留 `summary/summaryText`，失败分析和修复证据会丢失 Midscene 的关键错误。

已修改：

- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 入口可见性短链路在 `launch` 后增加非打印首页恢复步骤：如果当前在三维创作、3D 打印、模型推荐或其他非打印首页，先通过底部导航或首页入口切回学习打印 / 基础打印首页，再等待首页业务入口。
- 失败项规范化保留 `summary` 和 `summaryText`，并优先从 Midscene summary 的 `results[].error` 提取失败摘要。
- 后续 `ANALYZE_FAILURE` / `GENERATE_REPAIR` 能拿到真实错误，如“在三维创作界面找不到文档打印入口”，避免再生成只加 sleep 的无效修复。
- 静态检查覆盖入口短链路必须具备非打印首页恢复，以及失败归因必须保留 Midscene summary。

追加线上验证：

- `agent-1783592463983-d2d4353b`
- 参数仍为固定设备：`runnerId=win-runner-01`，`deviceId=ecbfd645`，`deviceStrategy=fixed`；Runner 目录为 `attempt-1-ecbfd645`，不是两个手机并发。
- 原始执行失败在“资料库”页：底部导航选中资料库，未进入文档打印页。
- AI 自动修复重跑失败在手机桌面：`launch` 后未稳定进入 App，继续等待“打印”标签导致失败。

追加修复：

- 入口可见性短链路启动守卫从自由 `ai` 恢复改为确定性起点：
  `runAdbShell monkey -p <package> ...` -> `launch` -> `aiTap: 底部导航栏首页` -> 等待首页同时展示“文档打印 / 照片打印 / 扫描复印”，并明确排除资料库、教辅、模型、3D 打印页。
- 进入目标业务页时使用“首页的文档打印入口，不要点击资料库、教辅、模型或3D打印入口”，减少 Midscene 误点资料库/模型页的概率。
- 后续验证必须使用用户指定的 OPPO 单设备；如果平台只给设备 ID，需要先确认 OPPO 对应的 `deviceId`，不要使用自动设备策略。

再次部署后验证：

- `agent-1783593383366-df6c5b2b`
- 任务固定单设备 `win-runner-01 / ecbfd645`，没有并发两台设备。
- `GENERATE_YAML` 成功，但 `VALIDATE_YAML` 被平台规则拦截：负向提示里出现 `3D打印` 文案，校验器认为小白学习打印包名对应脚本不能写成“3D 打印”。

追加修复：

- 入口短链路负向提示避开平台禁词，将“3D 打印页”改为“三维创作页”，仍保留对资料库、教辅、模型页的排除。
- 静态检查同步覆盖新提示，避免后续入口模板再次写回 `3D打印` 导致校验阶段失败。

继续验证：

- `agent-1783593731773-e3fb8da2`
- 服务端只创建了一个 job：`job_1783593824505_00002`。
- job 只有一个 attempt：`attempt-1-ecbfd645`，服务端没有主动向第二台设备下发。
- 用户观察到另一台手机也打开了小白学习打印；结合模板里存在 `runAdbShell: monkey -p ...`，判断裸 adb shell 可能没有被 Midscene 严格限制在选中设备上。
- 同时真机失败页面为“匹配本地考情 / 易错题”学习页，说明底部“首页”不是打印首页，必须进入“打印 / 学习打印 / 小白打印”入口。

追加修复：

- 入口可见性短链路移除裸 `runAdbShell monkey -p ...`，避免 adb 命令影响非目标手机。
- 启动后改为 `launch` + `aiTap: 底部导航栏或首页中的打印、学习打印、小白打印入口`，再等待“文档打印 / 照片打印 / 扫描复印”同时出现。
- 静态检查要求入口模板不得再包含裸 `monkey -p` 启动。

再次核对设备问题：

- 用户确认 `ecbfd645` 是 OPPO，但指出 Codex 没有确认“当前执行的物理机器是哪一台”。
- 当前服务端只能证明 Agent run 固定了 `deviceId=ecbfd645`，Runner report 也只显示 `attempt-1-ecbfd645`；旧 UI/预检没有显示设备品牌/型号，不能让用户直观看出是哪台物理设备。
- 另一台设备截图也显示同一 job 信息，说明只靠服务端 job device_id 和 Runner 环境变量仍不足以让执行链路可审计。

追加修复：

- `midscene_cli_dispatch_yaml_text` 支持 `device_id`，只在 Runner 拉取任务时生成临时 CLI YAML：`android.deviceId=<selected device>` + root `tasks`；不修改保存的 YAML，不改历史 YAML。
- `/api/runner/jobs/next` 将 selected job 的 `device_id` 传入临时 CLI YAML，作为 `ANDROID_SERIAL` 环境变量之外的第二层设备绑定。
- Agent 执行前体检的 Runner 设备 detail 增加设备 label / display_name / brand / model，后续页面上可以直接看到 `ecbfd645` 对应的物理设备信息。
- 静态检查覆盖临时 CLI YAML 必须注入 selected `android.deviceId`，且保存格式仍保持 `android.tasks`。

再次核对生成 YAML 是否符合业务需求：

- 最新失败的短链路没有进入“文档打印”页面；实际失败在“等待小白学习打印首页同时展示文档打印 / 照片打印 / 扫描复印”阶段，后续点击文档打印和断言百度网盘入口都没有执行到。
- 该 YAML 符合“不点击百度网盘、只做入口可见性”的目标，但不满足完整业务前置链路：真实 App 启动后可能停在“计算练习 / 题库 / 资料库 / 教辅 / 模型 / 三维创作”等非打印页，脚本必须先恢复到应用首页，再进入小白学习打印首页，再进入文档打印页。

追加修复：

- Agent 入口可见性短链路在 `launch` 后先从计算练习、题库、错题、资料库、教辅、模型页、三维创作页等非打印功能页返回或关闭到应用首页。
- 再点击应用首页或底部导航里的打印 / 学习打印 / 小白打印入口，并等待打印首页同时出现“文档打印 / 照片打印 / 扫描复印”。
- 只有在打印首页成立后，才点击首页的目标业务入口（如文档打印），等待目标业务页或导入入口区域加载并展示目标入口（如百度网盘），最后断言入口可见。
- 修正 `_agent_entry_visibility_intent` 漏读 run 根字段 `requirementText` 的问题，避免线上 payload 只把需求文本放在根字段时目标页退化成“目标页面”；该样例现在会明确生成“首页的文档打印入口 -> 文档打印页面/导入入口区域 -> 百度网盘入口”。
- 静态检查覆盖这条业务前置链路，防止后续只生成“启动 App -> 找百度网盘/文档打印”的松散脚本。

部署后继续验证：

- `agent-1783595698297-a75849a4`
- 新任务固定 `win-runner-01 / ecbfd645`，在 `GENERATE_YAML` 阶段被平台门禁拦截，没有下发 Runner。
- 拦截原因：短链路中的 `ai: 如果当前在计算练习...先点击返回或关闭...` 被评分器判为复合 AI 动作；首个 `aiTap` 前也缺少就近 `aiWaitFor/sleep`，执行等级降为 `needs_review`。

追加修复：

- 入口短链路改为官方动作的确定性冷启动：`terminate` -> `launch` -> `aiWaitFor` 应用首页/启动页 -> `aiTap` 打印入口 -> 等待打印首页 -> 进入目标业务页 -> 断言目标入口。
- 移除复合 `ai` 恢复指令，避免让 AI 在一个步骤里同时判断页面、返回/关闭和导航。
- 静态检查直接对该样例调用 `score_midscene_yaml_executable(..., generated=True)`，要求必须达到 `executable`，防止后续再次在生成阶段被 needs_review 门禁拦住。

### 2026-07-09 Runner Android SDK 环境注入与环境失败归因

继续跟踪部署后的新任务：

- `agent-1783589092511-a0a8be01`
- 目标：基础打印新增百度网盘入口

结果：

- `GENERATE_YAML` / `VALIDATE_YAML` / `EXECUTION_PRECHECK` 继续通过。
- Runner 真实 dry-run 通过，说明上一轮 Midscene CLI YAML 结构问题已修复。
- 正式执行失败从 0 秒结构错误变成 Android SDK 环境错误：
  `Neither ANDROID_HOME nor ANDROID_SDK_ROOT environment variable was exported`。

问题定位：

- 用户确认未替换 Windows runner，线上 Runner 仍使用旧脚本/旧服务环境。
- Midscene CLI 的 Android 集成要求 `ANDROID_HOME` 或 `ANDROID_SDK_ROOT`；Runner 虽能找到 `ADB_BIN` 并上报设备，但没有把 adb 所在 SDK 根目录注入给 Midscene 子进程。
- Agent 失败分析把环境错误继续交给 AI，AI 又误判为 `SCRIPT_ISSUE` 并生成 `runAdbShell: adb devices` 这种无效 YAML 修复。

已修改：

- `windows-midscene-runner.py`
- `mac-midscene-runner.py`
- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- Runner `midscene_env` 会从 `ADB_BIN` / 已解析 adb 路径反推 SDK 根目录，自动注入 `ANDROID_HOME`、`ANDROID_SDK_ROOT`，并把 `platform-tools` 加入 PATH。
- `_agent_job_failure_type` 将 `ANDROID_HOME/ANDROID_SDK_ROOT` 缺失、无法获取 Android 设备列表、ADB 设备异常归为 `ENV_ISSUE`。
- `ANALYZE_FAILURE` 遇到环境类 Runner 失败时保持 `ENV_ISSUE`，不允许 AI Gateway 覆盖成 `SCRIPT_ISSUE`。
- `GENERATE_REPAIR` 对 `ENV_ISSUE` 跳过 YAML 修复，避免无意义地改脚本和重跑。
- 静态检查覆盖 Android SDK 环境补齐和环境失败归因。

### 2026-07-09 Runner CLI YAML 接口配置保留修复

真实跟踪部署后的新任务：

- `agent-1783587131630-42b7fd26`
- 目标：基础打印新增百度网盘入口

结果：

- `GENERATE_YAML` 2 秒内成功，生成 1 条入口可见性短链路。
- `VALIDATE_YAML` / `EXECUTION_PRECHECK` 均通过，首批可执行 1/1。
- Runner 正式执行 0 秒失败，重跑仍失败。
- Midscene CLI 报错：`No valid interface configuration found in the yaml script, should be either "web", "android", "ios", "computer", or "interface"`。

问题定位：

- Runner 的 `midscene_cli_yaml_text` 把服务端 `android.tasks` 转成 CLI 根 `tasks` 时，丢掉了 `android` 接口配置。
- Runner dry-run 只检查了根 `tasks` 和 action，没有检查 CLI 必需的 `android/web/ios/computer/interface` 接口配置，导致 dry-run 通过、正式 CLI 0 秒失败。
- Agent 自动修复把结构性错误误修成加 `sleep`，因为失败分析材料虽包含 stdout evidence，但 AI 分析输入仍未稳定提取结构性错误字段。

已修改：

- `task_server/services/yaml_service.py`
- `task_server/router.py`
- `windows-midscene-runner.py`
- `mac-midscene-runner.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 参考 Midscene 官方 YAML CLI 结构：环境配置段（如 `android`）与根级 `tasks` 同级。
- 新增 `midscene_cli_dispatch_yaml_text`，只在 Runner 拉取任务时把服务端保存格式临时转换为官方 CLI 结构；不修改已保存 YAML，也不改历史脚本。
- `yaml_with_single_task` 保持原有 `android.tasks` 提取语义，避免影响 UI、评分、修复等非 Runner 下发调用。
- Windows/Mac Runner 的 `midscene_cli_yaml_text` 转换 `android.tasks` / `ios.tasks` 时保留 `android: {}` / `ios: {}` 接口配置。
- Runner dry-run 新增接口配置检查：缺少 `android/web/ios/computer/interface` 时直接失败，不再放行到正式执行。
- 静态检查覆盖：保存格式不变、Runner 临时下发格式符合官方 CLI、Runner CLI 接口配置检查必须存在。

### 2026-07-09 Agent 入口可见性快路径通用化

用户明确指出“基础打印新增百度网盘入口”只是测试样例，平台不能只针对单个需求优化。

已修改：

- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- Agent 层新增通用 `_agent_entry_visibility_intent`，识别“新增/展示/显示/可见/校验/位置/同级/并列某入口”类需求。
- 入口可见性快路径不再以“百度网盘”为触发条件；百度网盘只是 `entryLabel=百度网盘` 的一个样例。
- 对明确要求“点击后/跳转/授权/登录/文件选择/导入文件/WebView/SDK”等外部流程的需求，不走短链路，避免误伤真实点击流程。
- 直接生成 YAML、首批冒烟兜底、传给通用 YAML 生成器的 `forceEntryVisibilityFastPath` 均使用同一份通用入口意图。
- 短链路 YAML 只做：启动 App -> 等首页稳定 -> 进入目标业务页（如有） -> 等待/断言目标入口可见；不点击第三方或外部入口。
- 业务主链兜底也从百度网盘专用改为通用入口可见性链路。
- 静态检查改为防止 Agent 入口快路径退回百度网盘专用判断。

### 2026-07-09 入口可见性 Agent 生成快路径

继续监督线上任务：

- `agent-1783580174161-ba7d6782`
- 目标：基础打印新增百度网盘入口

问题定位：

- 上一轮部署后，任务仍先卡在 `GENERATE_YAML` 的 `requirement_analyzer skill`，约 4 分钟后才进入视觉校准。
- 视觉校准对 4 张 Figma/UI 图使用 900 秒单批上限，之后又进入 coverage auditor / coverage repair，导致一个明确的“入口展示/可见性”需求在 YAML 生成前被多个重型 AI 阶段串行阻塞。
- “基础打印新增百度网盘入口”没有显式写“展示/可见”，旧规则在没有展示词时会默认生成“点击百度网盘后进入授权/文件选择”链路，导致短链路兜底只能在后置阶段修复，而不是一开始就生成。

已修改：

- `task_server/services/ai_skill_service.py`
- `task_server/services/yaml_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 对包含“百度网盘 + 入口”，且没有“点击后/跳转/授权/登录/文件选择/导入文件/WebView/SDK”等外部流程词的需求，新增确定性 `deterministic_baidu_entry_visibility` 快路径。
- 快路径在 AI skill pipeline 前直接生成 3 条入口可见性短链路用例，并用本地 smoke gate 选为首批冒烟，不再等待 `requirement_analyzer` / `smoke_selector`。
- 默认策略调整为：新增入口需求只要未明确要求外部点击流程，就优先按“入口展示/同级并列/位置可见”处理，不点击第三方百度网盘入口。
- `yaml_service.py` 对该快路径跳过重型视觉校准、coverage auditor 补全和 executable YAML planner；Figma/截图仍记录为视觉参考，但不阻塞首批 YAML 生成。
- 静态检查覆盖快路径不能调用 AI skill、必须产出首批 smoke、首条链路必须从小白学习打印首页进入文档打印，并且不能点击百度网盘或等待授权页。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
```

追加复盘：

- 部署后新开 `agent-1783581355812-efc69e19` 仍停在 `GENERATE_YAML` 的 `requirement_analyzer skill`，未出现预期的“视觉校准跳过 / 本地覆盖审查”。
- 直接原因不是用户未部署，而是快路径判断读取了后续追加到 `stage1_text_assets` 的平台 YAML 生成策略文本；策略文本里包含“点击百度网盘 / 第三方授权页 / 授权页”等禁用说明，反而让快路径误判为用户要求外部点击流程。
- 进一步检查发现，即使快路径命中，`yaml_service.py` 仍会先调用 baseline reranker 和 execution scope planner，两者也是重型 AI 决策点，不符合“入口展示需求一开始就生成短链路”的目标。

追加修复：

- `_joined_requirement_source` 过滤平台生成策略、YAML 基线提示、模板提示等派生上下文，只用用户标题 / 模块 / 原始需求判断是否要求百度网盘外部点击流程。
- `_fallback_requirement_points_from_text` 同样使用过滤后的原始需求来源，避免策略文本污染本地需求点拆分。
- 暴露 `should_fast_path_baidu_entry_visibility` 给 `yaml_service.py`，在 baseline reranker / execution scope planner 之前识别入口可见性快路径。
- 命中快路径时跳过 AI 基线重排和 AI 执行范围规划，固定生成 3 条首批短链路冒烟，并在 trace 中显示“入口可见性快路径：跳过重型 AI 需求解析”。
- 静态检查扩展为带真实 `build_executable_smoke_yaml_policy_text()` 的输入，确保策略文本不会再次把入口展示需求污染成点击授权流程。

再次线上验证发现：

- 新开 `agent-1783582585669-6fcc8d79` 后仍出现旧 trace `正在按 requirement_analyzer skill 做需求体检和测试点拆解`，说明只靠 YAML 生成器重新从文本推断快路径仍不够稳。
- Agent 层已经有 `_agent_needs_baidu_entry_smoke(run)`，能够基于业务主链明确识别“百度网盘入口可见性”需求；该意图需要显式传给 YAML 生成器，不能让下游再次猜测。

再次追加修复：

- Agent 调用 `generate_ui_yaml_from_request` 时传入 `target=title` 和 `forceEntryVisibilityFastPath=_agent_needs_baidu_entry_smoke(run)`。
- YAML 生成器支持 `target/goal` 作为 `title` 兜底，并优先尊重 `forceEntryVisibilityFastPath` / `force_entry_visibility_fast_path` / `entryVisibilityFastPath`。
- 静态检查覆盖 Agent 必须传强制快路径标记，YAML 生成器必须支持目标兜底和强制标记。

再次线上验证：

- `agent-1783584291715-7c800514` 已命中 trace：`入口可见性快路径：跳过重型 AI 需求解析，直接生成短链路冒烟用例`。
- 但 generate job 仍停在 45%，说明即使 YAML 生成器命中快路径，通用生成链路内部仍可能在当前线上环境卡住。

再次追加修复：

- Agent 对 `_agent_needs_baidu_entry_smoke(run)` 命中的任务直接写入 `00-文档打印首页百度网盘入口可见性短链路冒烟.yaml`。
- 该路径不再调用通用 `generate_ui_yaml_from_request`，直接返回 `agent_direct_entry_visibility_smoke.v1` 生成结果并进入现有 YAML 校验 / Runner 流程。
- 静态检查覆盖 Agent 必须直接生成百度网盘入口可见性短链路 YAML，不能再阻塞在通用生成器。

追加验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/ai_skill_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check
```

### 2026-07-09 Agent 生成 YAML 长时间卡住与需求主链偏移修复

本轮真实跟踪线上新任务：

- `agent-1783567710600-f29a4658`
- 目标：基础打印新增百度网盘入口
- 输入：复用上一条任务的需求文字、Figma 链接、App、Runner 和设备

问题定位：

- 任务在 `GENERATE_YAML` 阶段长时间停留；trace 显示先进入 `requirement_analyzer skill`，随后进入 `视觉校准`，旧配置给 Agent YAML 生成预留了 7200 秒，视觉批次动态预算可到 3600 秒，导致界面长期显示执行中。
- 业务主链抽取被 Figma 首页文案影响，出现“拍照扫描文件 / 智能图片矫正”，没有优先使用用户需求里的“首页、文档打印、照片打印、扫描复印、百度网盘入口”。
- 生成 job 只有被外部读取时才会做 stale timeout 收敛，Agent watcher 本身没有主动触发 stale 检查，因此容易出现 Agent 状态长时间不刷新。

已修改：

- `task_server/services/agent_service.py`
- `task_server/services/yaml_service.py`
- `deploy/install-server.sh`
- `deploy/midscene.env.example`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- Agent YAML 生成默认超时从 7200 秒收敛为 900 秒，并在 Agent 生成 job 创建时显式写入 `timeout_seconds=900`。
- Agent 生成进度 watcher 周期性调用 `expire_generate_job_if_stale`，让 stale job 自动进入 timeout 状态，不再让 Agent UI 长时间假运行。
- 部署脚本新增 `MIDSCENE_AGENT_GENERATE_YAML_TIMEOUT_SECONDS=900` 默认值，并把旧的 `7200/3600/1800` 自动迁移到 `900`。
- 业务主链兜底优先识别“基础打印新增百度网盘入口”类需求，抽取为：首页 -> 文档打印 -> 照片打印 -> 扫描复印 -> 校验百度网盘入口可见。
- 生成 YAML 自动确认门禁改为必须达到 `executionLevel=executable`；`draft/needs_review` 不再因为结构校验通过就进入 `VALIDATE_YAML` / Runner。
- 质量报告中的可执行任务数改为只统计 executable 文件，避免 51 action / 33 wait 的长链路 draft 被误报为可执行。
- 静态检查覆盖 Agent 生成超时、stale watcher、部署默认值迁移和需求主链优先级。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check
```

### 2026-07-09 AI Gateway skill 硬超时修复

部署上一轮修复后重新验证：

- `agent-1783570675172-67dc71dc`
- 业务主链已正确变为：首页 -> 文档打印 -> 照片打印 -> 扫描复印 -> 校验百度网盘入口可见
- 但任务仍在 `GENERATE_YAML` 的 `requirement_analyzer skill` 长时间无新 trace，说明单个文本 skill 的 AI Gateway 调用没有按 90 秒及时返回。

已修改：

- `task_server/services/ai_skill_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 文本 AI skill 的 AI Gateway 调用增加硬超时包装，超过传入 timeout 后直接抛 `TimeoutError`。
- AI Gateway skill 超时后交给上层 requirement/scenario/automation fallback，不再继续进入另一条可能同样长等待的 provider 调用。
- 静态检查覆盖硬超时包装、`future.result(timeout=...)` 和 timeout 向上层 fallback 暴露。

### 2026-07-09 入口可见性首批冒烟兜底

继续跟踪线上验证任务：

- `agent-1783570675172-67dc71dc`
- 状态：`FAILED`
- 失败点：`EXECUTION_PRECHECK`

问题定位：

- 生成结果只保留了第 8 条“百度网盘入口点击后跳转终态”作为 executable。
- 该用例不是稳定首批冒烟候选，precheck 正确拦截：`首批可执行 0/1`。
- 前 7 条入口展示类用例没有形成短链路 executable，导致流程没有真正跑起来。

已修改：

- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- Agent 生成确认阶段增加确定性入口可见性冒烟兜底。
- 当需求明确包含“百度网盘入口”，且生成结果没有稳定 `smokeCandidate` 时，自动写入 `00-文档打印首页百度网盘入口可见性短链路冒烟.yaml`。
- 兜底 YAML 只做：启动 App -> 等首页稳定 -> 进入文档打印 -> 等待/断言百度网盘入口可见，不点击第三方入口。
- 静态检查覆盖该兜底必须带 `smokeCandidate` / `runnerCandidate`，用于首批 Runner 冒烟。

后续验证发现：

- 线上生成器会把 19 action / 13 wait 的照片打印入口长链路也标成 `smokeCandidate`。
- 因此兜底触发条件不能只看 `smokeCandidate=true`，还必须要求首批候选是稳定短链路。

追加修复：

- 只有 `actionCount <= 8`、`waitCount <= 6` 且 `replanRisk != high` 的 smokeCandidate 才算稳定首批候选。
- 长链路 smokeCandidate 不再阻止短链路兜底生成。

继续验证任务：

- `agent-1783578506591-7a41d9fb`
- 兜底短链路 `00-文档打印首页百度网盘入口可见性短链路冒烟.yaml` 已插入并作为首批 Runner 执行。
- Runner dry-run 通过，但正式 Midscene 执行 0 秒失败，stdout 只显示 `Failed files` 和 summary JSON 路径。
- Agent failureAnalysis 输入缺少 YAML/log/screenshot/summary 细节，导致 AI 只能返回“输入为空，无法分析”。

追加修复：

- Agent 收集 Runner 失败 job 时，从 `LEARNING_DIR/runs/<jobId>/summary.json`、`stdout.log`、`stderr.log`、`attempts.json` 读取失败材料。
- `failureAnalysis` 和 `repairDraft` evidence 增加 `summaryText`，后续诊断能看到 Midscene summary 里的真实错误，而不是只有 `Failed files`。

### 2026-07-09 Runner dry-run 与 Midscene CLI YAML 结构一致性修复

本轮定位线上任务：

- `agent-1783565230180-da12543f`
- 目标：基础打印新增百度网盘入口
- 状态：`FAILED`
- 失败停在：`RERUN`

问题定位：

- 服务端已生成 `android.tasks`，Runner 真实 dry-run 也通过，说明上一轮平台根修复生效。
- 正式执行时 Midscene CLI 1.7.10 报错：`property "tasks" is required in yaml script`。
- 直接原因是 Windows Runner 的 dry-run 规则检查 `android/ios` 平台根，但正式执行直接把同一份 `android.tasks` YAML 交给 Midscene CLI；当前 CLI 实际加载的是顶层 `tasks` 格式，导致 dry-run 与真实执行结构不一致。
- 后续修复重跑仍使用同类平台根 YAML，因此重跑也同样失败。

已修改：

- `windows-midscene-runner.py`
- `mac-midscene-runner.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- Runner 新增 `midscene_cli_yaml_text`，在交给 Midscene CLI 前把服务端平台根 `android.tasks` / `ios.tasks` 展开成 CLI 可加载的顶层 `tasks`。
- Runner YAML dry-run 改为使用同一份 CLI 展开结果做结构检查，避免 dry-run 假通过、正式执行失败。
- 固定设备不再写入 CLI YAML 的 `android.deviceId`，改为通过 `ANDROID_SERIAL` / `DEVICE_ID` 环境变量传给 Midscene 进程。
- Windows / Mac Runner 保持一致行为。
- 后端静态检查覆盖 Runner 必须做 CLI YAML 展开、dry-run 与真实执行一致、固定设备通过环境变量传递。

已验证：

```bash
python3 -m py_compile windows-midscene-runner.py mac-midscene-runner.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
```

### 2026-07-09 Runner dry-run 平台根节点修复

本轮定位线上最新任务：

- `agent-1783508474367-2fa0485c`
- 目标：基础打印新增百度网盘入口
- 状态：`FAILED`
- 失败停在：`RUN_SONIC`

问题定位：

- Agent 生成、平台本地 dry-run 和静态校验均通过了部分 YAML。
- Windows Runner 真实 dry-run 全部拒绝选中的 YAML，错误为：`缺少 android 或 ios 平台根节点`。
- 直接原因是服务端生成/拆分链路会产出或保留根级 `tasks:`；平台本地校验允许 `root.tasks`，但 Runner 真实 dry-run 要求顶层必须是 `android.tasks` 或 `ios.tasks`。

已修改：

- `task_server/services/yaml_service.py`
- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 新增 `ensure_midscene_platform_root`，在 Runner 相关链路把根级 `tasks:` 包装为 `android.tasks`。
- `cases_to_midscene_yaml` / `cases_to_separate_midscene_yamls` 直接生成 `android.tasks`，不再生成 `android: null + root tasks`。
- Agent 确认生成 YAML、拆分多任务文件、确认已有生成文件时都会写入平台根结构。
- Runner 下发前的执行修复会兜底改写旧生成 YAML，并记录到 `yamlExecutionRepairs`。
- AI 修复草稿和修复重跑写入前也会规范化平台根，避免修复链路再次触发 Runner dry-run 结构失败。
- 后端静态检查覆盖根级 `tasks` 包装、生成器输出和 Agent 拆分文件输出。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
```

### 2026-07-08 Skills 链路 app_package 参数漏提交修复

问题定位：

- 线上 Agent 在 `GENERATE_YAML` 阶段进入“兼容生成”。
- 日志显示：`build_cases_payload_from_skills() got an unexpected keyword argument 'app_package'`。
- 直接原因是 `yaml_service.py` 已经向 Skills 用例生成链路传入 `app_package`，但 `ai_skill_service.py` 的签名修复之前未随上次提交一起提交/部署，导致线上调用方和被调用方版本不一致。

已修改：

- `task_server/services/ai_skill_service.py`
- `task_server/services/case_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- `build_cases_payload_from_skills(..., app_package="", app_name="")` 接收 App 上下文。
- `call_skill_automation_filter` 和本地 fallback 自动化筛选继续透传 App 上下文，避免 fallback 步骤写错 App 品牌/首页。
- `case_service.py` 的委托入口同步接收并转发 `app_package` / `app_name`，避免间接调用再触发同类错误。
- 后端静态检查增加签名和透传覆盖，防止后续调用方/被调方参数再次不一致。

### 2026-07-08 上传截图作为 AI 视觉软参考可追踪

本轮修改目标：

- 用户上传的截图/需求补充截图都要作为 AI 判断参考进入视觉校准。
- 上传图不是硬门禁：不能因为没有完全引用截图就阻断生成或 Runner 执行。
- 需要在 Agent 产物里透明展示：识别到哪些上传图、是否要求进入 AI 判断、视觉判断是否完成、和 Figma/需求冲突时如何处理。

已修改：

- `task_server/services/agent_service.py`
- `js/agent-workbench.js`
- `tests/backend_static_checks.py`
- `tests/frontend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 新增 `visualReferenceReport` artifact，记录上传截图、Figma 页面/UI 图、参考来源、软参考规则、冲突处理策略。
- `visualReferenceReport` 明确 `hardGate=false`，同时标记 `aiJudgementRequired` / `sentToAiForJudgement` / `aiJudgementStatus`，用于说明上传图是否参与 AI 视觉判断。
- 质量报告 `qualityReport` 增加“上传截图参考”层和 `uploadedImageCount`，不改变 Runner 准入。
- Agent 工作台质量检查和输入来源详情新增“图片参考”卡片，展示上传截图数量、Figma 图数量、AI 判断状态和硬门禁状态。
- 后端静态检查覆盖：上传截图必须作为 AI 视觉软参考暴露，且 YAML 视觉校准输入仍包含 `figma_images + uploaded_image_assets`。
- 前端静态检查覆盖：UI 必须展示图片参考、上传截图、AI 判断和硬门禁状态。
- 顺手修正两条旧静态检查的 YAML 标量格式依赖：不再要求 `aiAssert` / `aiWaitFor` 必须带双引号，只校验语义和动作类型。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
git diff --check
```

### 2026-07-08 线上最新 Agent 失败归因与重跑稳定性修复

本轮定位线上最新任务：

- `agent-1783500104352-7eb42766`
- 目标：基础打印新增百度网盘入口
- 状态：`FAILED`
- 失败停在：`RERUN`

问题定位：

- 首次 Runner 执行失败不是设备离线，而是生成 YAML 顶层出现 `android: null`，Runner/修复流程注入 `android.deviceId` 后形成重复顶层 `android`，Midscene 解析报错。
- 自动修复后重跑又把 2 条修复 YAML 同时下发到同一台固定设备 `ecbfd645`；两条任务都包含 `am force-stop com.xbxxhz.box`，并发执行互相清理 App 状态，导致其中一条等待扫描复印页时实际停在手机桌面。

已修改：

- `task_server/services/yaml_service.py`
- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 新增空平台头清理：生成/修复进入运行时守卫前会移除顶层 `android: null` / `ios: null`。
- YAML 强校验遇到 `android: null` / `ios: null` 与 root `tasks` 共存时直接判不可执行，避免再次进入 Runner 后被设备注入放大成重复平台声明。
- Agent 修复重跑写临时 YAML 前也清理空平台头。
- Agent 安全重跑在固定 runner/device 场景下串行创建并等待 job，避免同一设备上的多个重跑任务互相 `force-stop`。
- 后端静态检查增加空平台头拦截/规范化、同设备重跑串行源码覆盖。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
python3 - <<'PY'
from task_server.services.yaml_service import validate_midscene_yaml_executability, remove_empty_midscene_platform_roots
from task_server.services import agent_service
bad = "android: null\ntasks:\n  - name: demo\n    flow:\n      - aiTap: 首页搜索框\n"
print(validate_midscene_yaml_executability(bad).get("ok"))
fixed = remove_empty_midscene_platform_roots(bad)
print(validate_midscene_yaml_executability(fixed).get("ok"))
print(agent_service._agent_rerun_requires_serial_device({"runnerId":"win-runner-01","deviceId":"ecbfd645","deviceStrategy":"fixed"}))
PY
git diff --check
```

未通过项：

```bash
python3 tests/backend_static_checks.py
```

当前失败在既有的 `check_generated_yaml_uses_single_final_assertion`：实际生成 `aiAssert: 图片建模上传入口、提示文案或空态区域可见`，静态检查精确匹配带双引号的字符串，属于无关格式断言，不是本轮 `android: null` 或同设备并发重跑改动引入。

### 2026-07-08 Agent YAML 可执行性收敛

已修改：

- `task_server/services/yaml_executable_scorer.py`
- `task_server/services/yaml_service.py`
- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `server-tasks/AI_Agent_草稿/基础打印新增百度网盘入口-可执行冒烟.yaml`

修复点：

- 入口展示 / 位置 / 同级类百度网盘用例不能点击百度网盘或等待第三方页面。
- 文档打印 / 扫描复印 / 照片打印 / 证件照类百度网盘用例必须先进入正确业务页。
- 埋点 / 统计 / eleTitle 类不应自动下发 Runner。
- 生成 YAML 默认不使用最近任务多次滑动清理。
- 普通入口 / 文案 / 布局等待压缩到 12-15 秒。
- 上传 / 导入 / 模型生成 / 切片等长任务才允许 120-180 秒。
- Agent 校验阶段会把“aiTap 写成检查/断言”的错误修成 `aiWaitFor` / `aiAssert`。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/yaml_executable_scorer.py task_server/services/yaml_service.py task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check
```

参考 YAML 校验结果：

```text
executionLevel=executable
score=100
dry_ok=True
```

### 2026-07-08 生成 YAML 可执行性增强

本轮已修改：

- `task_server/services/yaml_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 自然语言步骤转换时，`检查 / 验证 / 确认 / 可见 / 存在 / 展示 / 显示 / 布局 / 同级` 等页面状态检查优先生成 `aiWaitFor`，不再因为包含“按钮 / 选择”等词误生成 `aiTap`。
- 明确点击步骤会补轻量后置稳定等待；百度网盘入口点击后等待授权页、登录页、文件选择页、空状态页或提示页等跳转后信号。
- Agent 本地可执行修复在 AI 重写超时时，对“文档打印页百度网盘入口可见性”类误点用例补文档打印路径，并把修正后的可见性等待补成同语义 `aiAssert`，避免缺路径/缺终态断言导致继续进 draft。
- 后端静态检查增加展示/存在类步骤不误点、百度网盘第三方入口点击后等待跳转后信号的覆盖。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check
```

### 2026-07-08 Agent 生成 App 品牌上下文修复

本轮已修改：

- `task_server/services/ai_skill_service.py`
- `task_server/services/yaml_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

问题定位：

- 需求文档明确是“小白学习打印app”的基础打印百度网盘入口需求。
- 本地 Agent 草稿 `server-tasks/AI_Agent_草稿/基础打印新增百度网盘入口-可执行冒烟.yaml` 中出现了“小白扫描王首页已加载完成”。
- 直接原因是 `ai_skill_service.py` 的本地 fallback 步骤缺少 App 上下文，只能硬编码首页等待文案；当 AI 超时/失败走本地兜底时，会把跨 App 品牌词带进当前需求。
- 语义检查里也曾把“小白扫描王”当成 `com.xbxxhz.box` 的识别词，这会让错误品牌被误认为当前 App 语境。

修复点：

- `build_cases_payload_from_skills` / `call_skill_automation_filter` / fallback 自动化筛选透传 `app_package` / `app_name`。
- fallback 首页等待统一由当前 App 上下文生成；`com.xbxxhz.box` 使用小白学习打印入口信号，`com.kfb.model` 使用 3D/AI 建模入口信号，未知 App 使用“当前 App”中性描述。
- 非百度网盘 fallback 也使用同一 App 上下文，避免只修百度网盘路径。
- dry-run 语义检查改为按包名识别当前 App，并使用品牌冲突规则拦截跨 App 文案，例如 `com.xbxxhz.box` 不能出现“小白扫描王 / 智小白3D”，`com.kfb.model` 不能出现“小白学习打印 / 小白扫描王”。
- 移除把“小白扫描王”作为小白学习打印 App 识别词的逻辑。
- 后端静态检查覆盖 fallback steps、fallback YAML、Learning Print 被扫描王污染、3D 包被小白学习污染，以及非百度需求 fallback 的 App 上下文。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check
```

## 当前未提交/需注意改动

工作区可能存在用户或历史改动，不要默认回滚：

- `server-tasks-all/3D打印基线/十二生肖印章打印.yaml`
- `server-tasks/3D打印基线/十二生肖印章打印.yaml`
- `task_server/services/sonic_service.py`
- `task_server/services/yaml_executable_scorer.py`
- `deploy/install-windows-runner-service.local.ps1`
- `server-tasks/AI_Agent_草稿/`

提交时不要直接 `git add .`，按任务文件精确添加。

## 下一步优先级

1. 用真实需求 + Figma + 现有基线验证 Agent 新生成 YAML 是否贴合需求。
2. 对失败 Runner 报告继续做归因分类：YAML 问题、页面状态问题、设备问题、AI 服务问题。
3. 优化 Agent 生成结果展示：完整用例、可执行 YAML、需确认项、人工项、失败原因要分层清楚。
4. 持续沉淀成功执行的 YAML 片段到基线缓存，不把失败样例当成功模板。

## 常用部署流程

本地提交：

```bash
git status --short
git add <本次任务相关文件>
git commit -m "<提交说明>"
git pull --rebase
git push
```

服务端部署：

```bash
cd /opt/midscene-task-platform-src
git pull --ff-only
bash deploy/install-server.sh
systemctl restart midscene-task
curl http://127.0.0.1:8091/api/health
curl http://127.0.0.1:8088/api/health
```

本次部署快速命令：

现在服务端部署执行：

```bash
cd /opt/midscene-task-platform-src
git pull --ff-only
bash deploy/install-server.sh
systemctl restart midscene-task
curl http://127.0.0.1:8091/api/health
curl http://127.0.0.1:8088/api/health
```

本地剩余未提交内容可以之后再看：

```bash
git status
```

## 新对话推荐开头

```text
请先阅读 AGENTS.md 和 CODEX_STATE.md，然后只处理本次任务。

本次任务：
<写一个明确的小任务>

要求：
1. 先阅读相关文件并列修改计划。
2. 不要重构 router.py。
3. 不要新增执行模式。
4. 不要修改历史 YAML。
5. 不要改本任务无关文件。
6. 修改后跑相关检查，并更新 CODEX_STATE.md。
```

### 2026-07-14 Agent 覆盖收敛、视觉批次与真实执行结果修复

真实线上回归（修复前）：

- 服务端当时部署提交 `0a99ccc`，8091 / 8088、AI Gateway、Sonic 健康，模型为 `qwen3.6-plus`，`win-runner-01` 在线；固定设备为 OPPO `ecbfd645`，未选择或下发第二台设备。
- Agent：`agent-1784008419035-c7712069`。
- 终态：`FAILED / GENERATE_YAML`，未创建 Runner 任务，因此没有手机实际执行结果。
- Figma 正确解析 4 页 / 4 图；视觉资料确实进入 AI 批次。首批 2 图在 120 秒超时后，旧逻辑停止后续批次，最终为 0 / 2 完成。视觉判断仍是软参考，不是硬门禁。
- AI 生成 8 条自动化用例，但最终只确认 4 个 YAML；5 个明确需求点只覆盖 4 个，`REQ-005` 缺失，覆盖门禁正确阻断。
- 可执行规划器还把两个兄弟业务分支串线：照片打印候选被替换成扫描复印路径，扫描复印候选被替换成照片打印路径。

本轮通用修复：

- 增加 AI 最终覆盖收敛轮次。平台继续负责数量、静态校验和覆盖门禁；AI 在门禁前对全部候选做一次有界重分类，补齐遗漏的明确需求点，不靠单需求硬编码放宽门禁。
- requirement refs 以原始候选来源为事实锚点。AI 可以优化步骤，但不能用兄弟分支的 requirement refs 和路径替换当前候选；跨分支替换会保留原路径并记录 guard 计数。
- 视觉批次改为每批 1 图、逐批继续执行；单批失败不会取消剩余设计图。每批记录 attempted / completed / not_attempted、耗时、图片名和错误。重复大字段在调用前压缩，AI 返回后再合并完整上下文，Figma 解析代码未改。
- 默认单批视觉超时调整为 90 秒，总预算 360 秒；部署脚本仅迁移旧默认值，保留显式自定义配置。
- Agent 结果采用双状态：`orchestration` 表示完成 / 门禁阻断 / 取消，`execution` 表示未执行 / 通过 / 部分通过 / 失败。Runner 真实通过数不会再被后续门禁失败覆盖。
- Runner 失败进一步拆分为产品断言失败（`PRODUCT_BUG`）、脚本或环境待修复（`SCRIPT_ISSUE` / `ENV_ISSUE`）和未归因失败；前端分别展示通过、产品失败、脚本环境失败和运行中数量。
- 聚合时去重 progress / jobResult / report 多来源记录，并避免把 `timeout: 1800` 的等待上限误计成 1800 条超时任务。

离线重放真实线上生成产物：

- 文档打印、照片打印、扫描复印三条业务分支保持原始正确路径和 `REQ-001..003`。
- 固定设备上的百度网盘可见性 / 文案 / 同级关系检查收敛为第 4 条可执行 YAML。
- 点击入口后到首个真实可见落地页的有界检查收敛为第 5 条可执行 YAML；允许百度 App、Web 授权 / 登录、系统文件选择器等首屏状态，不深入第三方账号流程。
- 最终覆盖审计为 5 / 5 requirement、5 条 executable、0 条未解决自动化候选；5 个 YAML 均通过静态校验和可执行性评分，得分均为 100，无坐标点击。
- 未写入或修改任何历史 YAML。

参考的成熟状态模型与移动 AI 自动化模式：

- Playwright 保留 passed / flaky / failed；Allure 区分 Failed 与 Broken；GitHub Actions 区分原始 outcome 与编排 conclusion。平台据此保留执行事实与 Agent 编排结论两套状态。
- AndroidWorld、Mobile-Agent-E、AppAgent、Mobile-Agent-V 的共同点是观察、计划、执行、反思和可复用经验分层；成功基线可用于规划和加速，失败录屏 / 截图用于归因和修复上下文，但不能直接沉淀为成功模板。
- Midscene planning cache 只适合在成功执行后沉淀；本轮未在缺少真实 Runner 版本验证时直接开启缓存。

已验证：

```bash
npm test
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/ai_skill_service.py
bash -n deploy/install-server.sh
git diff --check
```

待完成：

- 提交并部署本轮修复后，用同一需求、同一 Figma、`qwen3.6-plus`、固定 OPPO `ecbfd645` 发起完整 Agent 回归。
- 必须轮询至 Agent 和所有 smoke / remaining Runner 任务终态，再人工复核 YAML、Runner 报告、截图和失败归因。

### 2026-07-14 部署后真实回归：聚焦覆盖收敛与有界第三方落地页

部署 `b2f070f` 后真实验证：

- 8091 / 8088 健康；AI Gateway、Sonic 健康；text / VL 模型均为 `qwen3.6-plus`。
- `win-runner-01` 在线，能力族为 `qwen3.6`；固定 OPPO `ecbfd645` 在线。任务创建前队列为 0，未选择华为设备。
- Agent：`agent-1784022300773-a3733e6d`。
- Figma 仍正确解析 4 页 / 4 图、忽略 0 页。视觉校准按 1 图一批真实尝试 4 / 4 批，每批约 90 秒；4 批均因 qwen3.6-plus 超时失败，0 / 4 完成。批次结果、图片名、耗时和错误均被保留；视觉仍为软参考，没有阻断后续生成。
- PLAN 由平台 MM skills 生成 8 个 AI 业务分支；路由仍为 `new_requirement_source / generate_draft`。
- 终态：`FAILED / GENERATE_YAML`。没有创建 Runner 任务，OPPO 和华为均未下发。

失败根因：

- 初始可执行规划为 3 条 executable，缺 `REQ-002` 照片打印和 `REQ-003` 扫描复印，另有 3 条未决自动候选。
- 最终覆盖收敛在线触发，但把 6 条自动候选和 13 条 manual 全部再次送给模型。模型只返回 3 条 executable + 2 条 manual，却在 review 中声称“所有输入候选均已终结”；平台正确识别遗漏并继续阻断。
- 生成的文档、位置、照片、扫描 4 个 YAML 均通过静态校验，任务级 scorer 原始评分均为 100；照片 / 扫描需求映射分别正确命中 `REQ-002 / REQ-003`，不是 YAML 结构或需求串线问题。它们因 AI 未返回分类而保留为 `needs_review`。
- `TC-005` 已被 AI 正确规划为“点击百度网盘后只等待授权页 / H5 / 文件列表任一首个可见终态”，但旧 `_case_manual_block_reason` 只要看见“授权弹窗”就再次降为 manual，和平台允许的有界第三方入口策略冲突。

第二轮通用修复：

- 最终收敛只发送当前 executable、未决自动候选，以及每个缺失需求点最多 1 个 manual 备选；本次真实结构从 19 条缩为 8 条（6 自动 + 2 缺口备选），其余 11 条人工项由平台原样保留。
- AI 第二轮漏回既有 executable 时保留上一轮已通过的可信路径；漏回未决候选时仍保持 `needs_review`，不得自动升级。
- requirement ID 使用规范化 `REQ-*` 精确匹配，候选只写 `coverage: REQ-002` 也能追溯到完整需求点，不要求重复中文全文。
- requirement analyzer 不再把缺少 Figma 帧擅自追加到需求点正文，证据缺口放入 questions / missing_inputs。
- executable planner 明确：需求是验收依据，Figma / 截图 / 页面知识是软参考。候选已有真实文字路径、可信兄弟基线且只做固定设备可见性检查时，应交给 Runner 验证；入口不存在属于产品断言失败，不能仅因缺对应设计帧提前转 manual。
- 确定性人工闸门允许“可信基线路径 + 点击入口 + 只等待多个合法首个可见终态 + 不输入账号 / 验证码、不确认授权、不选择文件”的有界检查；深层授权、凭据和文件操作仍明确阻断。

线上真实产物离线重放：

- focus 为 8 / 19，最终 executable 为 `TC-001..TC-005`，需求覆盖 5 / 5，未决自动候选 0。
- `TC-005` 通过自动化拆分闸门；深层授权 / 文件操作测试仍被单测阻断。
- 照片和扫描 YAML 的“回到首页”动作由现有通用静态 repair 规范化；最终 5 个 YAML 均为 static executable、scorer 100、0 warning、0 坐标。
- 未写入或修改历史 YAML。

已验证：

```bash
npm test
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check
```

待完成：

- 提交并部署第二轮通用修复。
- 用同一需求、同一 Figma、固定 OPPO `ecbfd645` 再跑完整 Agent；只有 Agent、smoke 和 remaining Runner 全部到终态，并人工检查真实报告 / 截图后才能给出最终结论。

### 2026-07-14 部署后真实回归：最终覆盖门禁与证据驱动失败恢复

部署 `51ea947` 后真实验证：

- 8091 / 8088 健康，text / VL 模型均为 `qwen3.6-plus`；`win-runner-01` 在线，固定 OPPO `ecbfd645` 在线。虽然 Runner 还登记了华为设备，但本次所有原始、证据重试和 AI 修复任务均只下发 OPPO，保持同设备串行。
- Agent：`agent-1784024849032-89428fd5`；终态为 `FAILED / RERUN`，错误为“重跑后仍有失败或超时任务”。
- Figma 仍正确解析 4 页 / 4 图并全部送入 AI 判断。4 个单图批次分别在约 90 秒超时，`aiJudgementStatus=failed`；视觉资料保持软参考，没有阻断生成。
- AI 规划了 8 个业务分支，明确包含文档打印、照片打印、扫描复印、全局交互、UI 适配、异常处理和数据展示。
- 最终只形成 4 个可执行引用：1 个文档短冒烟、文档、照片、手机文案；扫描复印 `REQ-003` 和入口可达 `REQ-004` 没有进入最终 YAML。旧代码没有在最终组合后再次硬门禁，仍错误下发 Runner。
- 原始 Runner 事实为 3 成功 / 1 失败：文档、文档短冒烟、手机文案成功；照片打印失败。前端最终红色状态不能覆盖这 3 个真实成功结果。
- 照片失败关键帧停在显示“照片打印 / 智能证件照 / 普通证件照 / 照片拼版打印”等卡片的父页面，说明脚本缺少进入内层“照片打印”及照片规格页的导航，不是已到目标页后的产品断言失败。
- 原脚本证据重试 `job_1784026358619_00009` 仍失败；AI 修复重试 `job_1784026515295_00011` 在同一 OPPO 上 300 秒超时。旧 AI 修复候选却是 3 条文档打印基线，未召回真实相邻分支基线 `6寸照片打印.yaml`，因此虽然看到了关键帧，仍没有可靠补齐父子页面路径。

本轮通用修复：

- 保留 AI 的初次规划和一次有界覆盖收敛；收敛后由平台重新审计最终可执行组合。显式 requirement 映射、3 / 5 / 8 数量或分类终态不完整时，在 YAML 转换和 Runner 下发前硬阻断，并保留审计结果，不再让“不完整但看似 executable”的组合进入手机。
- 常规生成仍遵守 Top3 和执行速度约束，但候选池按 AI 已规划的业务分支轮询召回，使文档、照片、扫描等核心分支都有机会进入 Top3，再由 AI 重排。该逻辑只使用 AI 业务计划和通用基线索引，没有写入百度网盘、5 寸照片或单需求特判。
- 失败分析同时接收 Midscene 报告关键帧和可信同分支基线；照片分支离线检索已优先命中 `server-tasks-all/小白学习基线用例-基础打印/6寸照片打印.yaml`，扫描分支可命中 `文件扫描.yaml`。
- AI 修复最多接收 6 条当前失败分支证据，并必须把关键帧当前页与基线 `businessPath` 对齐。若修复前后 YAML 的点击 / 导航动作序列发生变化，必须通过 `usedBaselineIds` 引用本次真实候选；平台直接比较 YAML 动作，不依赖 AI 自述，编造或漏引均阻断下发。
- 300 秒墙钟超时本身不再锁定为 `ENV_ISSUE`，AI 可以结合关键帧改判脚本路径问题；Android SDK、设备离线、Runner 断开、模型请求中止、网关和网络错误等具体证据仍锁定为环境问题。
- Runner 进度按逐任务状态拆分“执行中 / 排队中”；同一固定设备的一运行一等待不再显示成“2 个运行中”。重跑页在工具仍执行时直接展示“原脚本证据重试 / AI 修复脚本验证”、原任务到新任务链路、固定设备和累计结果，不再暴露 `_tool_rerun` 内部名。

已验证：

```bash
npm test
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
node --check js/agent-workbench.js
node --check ai-gateway/server.js
git diff --check
```

- 全量结果：后端 61 项、前端 67 项、AI Gateway 46 项、Skill 契约 3 个 fixture 以及桌面 / 移动端视觉回归全部通过。
- 将线上 Agent 保存的原始 `generatedCases` 输入新门禁重放，结果为 3 条 executable / 平台目标 5 条，精确缺失 `REQ-003` 扫描复印和 `REQ-004` 入口交互可达；新代码会在 Runner 下发前阻断该旧产物。
- 本地真实基线缓存重放：混合业务分支前三个候选分别覆盖文档、`6寸照片打印`、`文件扫描`；失败照片分支的首个修复候选为 6 寸照片成功基线。
- 未修改 Figma 解析逻辑、历史 YAML、`sonic_service.py`、`yaml_executable_scorer.py` 或设备选择策略。

待完成：

- 提交并部署本轮修复后，再用同一需求、同一 Figma、`qwen3.6-plus`、`win-runner-01`、固定 OPPO `ecbfd645` 发起完整 Agent 回归。
- 必须等 Agent、首批冒烟、remaining 和有界 AI 修复全部到终态，再人工复核最终 YAML 的三个业务入口、文案 / 同级关系 / 可达页、真实报告、关键帧和失败归因。

### 2026-07-15 部署后真实回归：原始需求契约与分支基线召回

部署 `80a9b84` 后真实验证：

- 本地、`origin/main` 和线上前端均为 `80a9b84`；8091 / 8088、AI Gateway、Sonic 健康，text / VL 模型为 `qwen3.6-plus`。
- `win-runner-01` 在线，模型族为 `qwen3.6`；固定 OPPO `ecbfd645` 在线。任务开始前没有运行中的 Agent 或 Runner job，未选择或下发华为设备。
- Agent：`agent-1784086634757-e7c92043`；终态 `FAILED / GENERATE_YAML`，进度 30。生成后台任务 `agent-generate-agent-1784086634757-e7c92043` 在最终覆盖门禁失败；没有创建 Runner job，因此不存在本次 smoke / remaining 真机结果。
- PREPARE_SOURCE 正确解析 Figma 4 页 / 4 图、忽略 0 页。PLAN 将 4 张图按单图批次全部送入 `qwen3.6-plus`，4 / 4 批均在约 90 秒超时，`sent=true`、`attempted=4`、`done=0`、`status=failed`、`hardGate=false`；Figma 仍是软参考，页面/图片计数没有丢失。
- 路由保持 `new_requirement_source / generate_draft`。AI PLAN 生成 8 个业务分支，冒烟建议仍是文档打印、照片打印、扫描复印。
- 最终只确认 3 条 executable：文档展示、照片展示、文档排序；门禁缺失 `REQ-003..006` 后正确阻断，没有把不完整组合发给 Runner。

失败根因：

- 原始需求明确要求文档打印、照片打印、扫描复印三个兄弟入口都覆盖展示、同级关系、文案和可达页面。requirement analyzer 却把扫描复印弱化成“需确认是否需要新增”，又把模型推断出的未绑定授权、已绑定文件列表和手机/宽屏适配扩写成 3 个新的硬 requirement；最终门禁实际检查的是 AI 扩写后的 6 点，而不是原始验收契约。
- 生成阶段已经识别三个必需首批分支，但旧多样化检索同时轮询全部 8 个 AI 场景。运行库中大量通用/文档类百度网盘成功样本挤占每分支 TopN 后，严格证据闸门得到文档 4 个、照片 0 个、扫描 0 个合格候选；AI 最终只收到文档基线，无法可靠升级扫描短链路。
- 3 / 5 / 8 数量在本次只作为 advisory，没有因为目标 8 条而硬凑；失败不是数量下限，也不是 scorer、Runner、ADB 或设备问题。

本轮通用修复：

- Agent 在开始 PLAN 前已从原始需求抽取“业务分支 + 验收维度”候选。本轮把该候选作为 `requirementCoverageContract` 传给现有 MM skills：它不预设页面层级或路径，AI 仍负责需求理解、场景设计、最短导航、风险和人工项。
- 对可审计的原始入口契约，硬 `requirement_points` 由原文分支及 checks 建立；AI 原始建议完整保留在 `ai_suggested_requirement_points`。授权态、账号数据、空态、弱网和额外设备形态若不是原文明确要求，只能进入 risks / questions / assumptions / manual，不能扩大硬门禁；缺 Figma 帧也不能把明确分支改成“待确认是否需要”。
- 必需首批分支检索改为锚点约束：先要求候选自身 title / file / businessPath / snippet / actions 命中 AI 分支叶子，再记录 `retrievalBranchIds`，最后仍交给现有 AI reranker 从可信候选中选择 Top3。平台不替 AI 选具体脚本，也不允许兄弟分支互相冒充。
- 本次线上 PLAN 原样离线重放后，文档、照片、扫描各得到 4 个可审计候选；代表候选分别为文档打印、`6寸照片打印`、`文件扫描`。没有修改 Figma 解析、历史 YAML、Runner、执行模式、`router.py`、`sonic_service.py` 或 `yaml_executable_scorer.py`。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/ai_skill_service.py task_server/services/yaml_baseline_cache.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name 通过，后端 61 项、前端 67 项、AI Gateway 46 项、Skill 契约 3 个 fixture，以及桌面 / 移动端视觉回归全部通过。
- 回归测试覆盖两种真实失败形态：AI 返回 6 个含授权/账号/设备推断的硬点时，门禁恢复为原文 3 个兄弟分支且保留 AI 建议审计；24 个通用成功样本挤占全局相似度时，照片和扫描的同分支基线仍有来源地进入 AI 候选池。

待完成：

- 提交并部署本轮修复。
- 部署后用完全相同输入再次发起 Agent，持续轮询 Agent、smoke、remaining 和可能的有界修复到终态；人工复核最终 YAML、真实 Runner 报告、截图和失败分类，且所有任务只能下发固定 OPPO `ecbfd645`。

### 2026-07-15 Agent 失败产物可读性与轮询阅读位置修复

问题与根因：

- Agent 产物区的失败分析仍走通用 JSON `<pre>`，把根因、Runner 任务、证据、基线和内部字段压成一整段，无法快速区分“哪里失败、为何失败、影响什么、下一步做什么”。
- Agent 轮询时 `updateAgentWorkbenchDynamic()` 每次都会替换整个 `agent-artifacts-card.innerHTML`；当前页签虽然由全局状态保留，但产物内容区的 `scrollTop`、移动端导航横向位置和 `<details>` 展开状态没有保存，因此阅读长产物时会周期性跳回顶部。
- 参考 BrowserStack Test Failure Analysis 的 RCA summary / failure type / impact / fix / evidence 分层、Playwright Trace Viewer 的错误与动作证据下钻，以及 Allure 的步骤 / 附件 / 重试渐进披露方式；本轮只采用适合当前 Agent 的信息层级，不新增模型调用或执行步骤。

本轮修复：

- 失败产物首屏改为结构化信息：失败分类和关键计数、根因判断、影响范围、建议动作；Runner 失败按任务独立列出，展示短文件名、job、状态和报告入口。
- 明确展示 AI 实际使用的 Runner 关键帧、成功基线和 AI 证据数量及摘要；完整路径、原始分析和 Runner 字段保留在默认折叠的“技术详情”中，“复制当前产物”仍复制完整原始数据。
- 修复草稿产物复用已有结构化 renderer，不再默认展示原始 JSON。
- 轮询重绘前捕获当前 run / tab、内容区纵横滚动位置、移动端导航横向位置和详情展开状态；仅在同一 run、同一 tab 重绘后恢复。用户主动切换任务或页签仍从顶部开始，避免错误继承旧位置。
- 兼容尚未形成 `failureAnalysis`、只有顶层 `run.error` 的早期失败，不会因结构化视图而隐藏真实阻断原因。
- 未修改 Agent 后端数据格式、AI 调用、Figma 解析、Runner、执行模式、历史 YAML、`sonic_service.py` 或 `yaml_executable_scorer.py`。

已验证：

```bash
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、Skill 契约 3 个 fixture 及 Playwright 视觉回归全部通过。
- Playwright 使用 12 条长失败任务、3 张 Runner 关键帧、2 条成功基线和长原始回包验证：失败首屏为三张摘要卡；原始 JSON 默认折叠；普通滚动和打开技术详情后的轮询均保持原位置与展开状态。
- 桌面和 390px 移动端截图已人工复核；移动端状态标签保持横排，产物卡片无横向溢出。

待完成：

- 推送并部署本轮前端修复后，在真实 Agent 失败记录上复核轮询期间的触控滚动、失败卡片字段和 Runner 报告链接。

### 2026-07-15 部署后真实回归：新端点有界首屏收敛

部署 `b1e3e96` 后真实验证：

- 8091 / 8088、AI Gateway、Sonic 健康，text / VL 模型均为 `qwen3.6-plus`；线上静态资源为 `b1e3e96`。
- Agent：`agent-1784099684235-43f82f1f`；终态 `FAILED / GENERATE_YAML`，没有创建 Runner job，因此本轮没有手机执行结果，也没有向第二台设备下发任务。
- PREPARE_SOURCE 正确解析 Figma 4 页 / 4 图。PLAN 将 4 张图逐批真实送入视觉模型，4 批均约 90 秒超时，`attempted=4`、`done=0`、`status=failed`、`hardGate=false`；Figma 文本与页面计数仍作为软参考保留，没有改动 Figma 解析。
- AI 生成 8 个业务分支，并把 5 寸照片放在照片打印分支。生成阶段保存 8 个场景、3 条 executable 展示检查和多条自动候选，但最终覆盖门禁仍缺文档 / 照片 / 扫描三条 reachability，并把扫描分支“展示百度网盘入口”误判为缺文案覆盖。

失败根因：

- 上游 `automation_filter` 已分别生成 `TC-004 / TC-005 / TC-008`：点击目标入口后只等待授权页或文件列表页等首个合法可见状态，不输入账号 / 验证码、不确认授权、不选择文件。
- 初始 executable 已分别引用同需求分支的成功来源页基线并形成可信导航。最终收敛 AI 却把“新目标落地页没有历史成功基线”当成 manual 理由，忽略了新功能本来就不可能预先拥有目标页成功基线，导致同一次 Agent 内两次 AI 判断冲突。
- `case_covers_requirement_acceptance(kind=copy)` 只识别“文案 / 文字 / 显示为”等术语，没有把断言中的“展示目标文字入口”计作文案证据；点击步骤本身仍不应计作文案覆盖。

本轮通用修复：

- 最终收敛仍只调用现有一次 AI。平台为缺失的 reachability 提供结构化 `convergenceEvidence`：同需求 executable 的成功来源页路径 + 上游 AI 候选的目标点击和有界首屏尾链。规划提示明确：来源页基线不需要证明新端点目标页已经成功执行，应让后续 YAML、评分、dry-run 和 Runner 验证真实首个终态。
- 只有自动候选、显式缺失的 reachability、同需求成功来源页、至少两个可观察终态、真实文字点击、无坐标且无深层账号 / 授权确认 / 文件操作时才允许合并；统一进入 `remaining`，不挤占 smoke。AI 仍可决定其余候选，平台保留分类、静态 scorer、dry-run 和真实 Runner 门禁。
- 若收敛 AI 明确降级上述已验证候选，平台记录原模型级别 / 原因并保留安全短链路，解决同一次流程内 AI 决策互相覆盖；AI 漏回的普通未决候选仍不会自动升级。
- 文案审计接受断言中“展示 / 显示 / 可见 / 出现 + 目标文字”的具体证据；仅点击目标、仅图标或明确无文字仍不能满足 copy。
- 没有新增执行模式，没有修改 `router.py`、Figma 解析、历史 YAML、设备策略、`sonic_service.py` 或 `yaml_executable_scorer.py`。

线上失败产物离线重放：

- 修正文案审计后，初始覆盖从旧逻辑的 8 / 12 变为真实的 9 / 12，只剩三条 reachability。
- 同一线上候选经新收敛得到 `TC-004 / TC-005 / TC-008` 三条 `remaining`，来源分别为 `TC-001 / TC-002 / TC-003`；最终覆盖 12 / 12、`afterOk=true`。
- 合并时只移除来源展示 case 末尾与目标文字相同的重复校验，父页面层级和加载等待均保留；三条真实 YAML 均为 executable、scorer 100、0 warning、0 坐标。
- 三条任务均通过 `_case_manual_block_reason`；加入“点击同意授权 + 输入账号验证码 + 选择文件”后仍被硬阻断。弱网、字体 / 系统设置、布局重复项没有因数量目标被升级。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、Skill 契约 3 个 fixture，以及桌面 / 移动端视觉回归全部通过。

待完成：

- 提交、推送并部署本轮修复。
- 部署后使用完全相同需求、Figma、`qwen3.6-plus`、`win-runner-01` 和固定 OPPO `ecbfd645` 发起完整 Agent；持续轮询 Agent、smoke、remaining 与可能的 AI 修复到终态，并人工复核最终 YAML、真实 Runner 报告、截图和失败归因。

### 2026-07-19 部署后真实回归：可执行规划截断与验收语义纠偏

部署 `d240266` 后真实验证：

- 8091 / 8088、AI Gateway、Sonic 健康；`win-runner-01` 在线并上报 `qwen3.6-plus / qwen3.6`，固定 OPPO `ecbfd645` 在线。本轮没有选择或下发华为设备。
- Agent：`agent-1784464343852-5e478235`；终态 `FAILED / GENERATE_YAML`，进度 30。没有创建关联 Runner job，因此失败与 Windows Runner、ADB、设备或历史 Sonic 任务无关。
- PREPARE_SOURCE 正确解析 Figma 4 页 / 4 图。PLAN 将 4 张图按 4 个单图批次真实送入 `qwen3.6-plus`，4 / 4 均在 13-19 秒完成，`sent=true`、`attempted=4`、`done=4`、`status=completed`、`hardGate=false`，所有批次均 `fallbackUsed=false`。
- AI 生成 8 条业务分支；硬需求仍是文档打印、照片打印、扫描复印三个兄弟分支，各自覆盖 visibility / relation / copy / reachability。基线重排为三个必需分支各提供 4 个可信候选，最终选中文档、照片和文件扫描成功基线。

失败根因：

- 初始 `executable_yaml_planner` 一次接收 20 个候选：8 个待决自动候选和 12 个上游已判定人工项，同时携带约 4 万字符重复上下文。线上 `qwen3.6-plus` 在 4096 completion token 截断，Gateway 返回 `Structured output truncated: finish_reason=length`。
- 规划异常兜底保留了原始 8 个自动候选，但没有将其升级或终结为 manual，最终覆盖门禁因此看到 0 executable、8 条非终态候选并正确阻断。
- 真实产物继续重放后发现第二层问题：最终收敛按“入口位置”等泛词选择人工备选，可能把照片分支候选用于扫描缺口，也没有优先给 AI 当前同 REQ executable 来补缺失验收维度。
- 千问在收敛中还会出现“review 声称已补齐，但 flow / assertionTarget 实际未包含 relation 检查”的语义漂移；既有覆盖门禁能识别并拒绝，不能把 review 文案当作通过依据。

本轮通用修复：

- 初始可执行规划始终保留全部待决自动候选；只有总决策面未达到平台 8 条上限时，才最多补 3 个上游人工备选供 AI 主动升级。超出预算的人工项不丢失，原样保持 manual，并在 trace 中记录 included / deferred 数量。
- 规划输入只保留原始 requirement contract、12 个验收检查、压缩场景索引、候选步骤/断言、可信基线、视觉批次判断和固定设备约束。真实失败请求从 20 个待输出候选、约 4 万字符降为 8 个待决候选、17115 字符。
- planner 默认输出预算由 4096 提升为 6144；只有明确的 `finish_reason=length` 才在同一所选模型上做一次更紧凑的 8192-token 有界重试。不会因业务输出截断静默切换用户选择的模型。
- 收敛候选先按原始 `REQ-*` 与业务分支匹配，再参考语义；兄弟分支不能互相冒充。缺失验收维度没有专门自动候选或有界证据时，才把同 REQ 的现有 executable 作为可修复候选，并继续提供一个同分支人工备选给 AI。
- 每个可修复 executable 携带局部 `repairAcceptanceChecks` 和 `preserveAcceptanceCheckIds`。平台不代写业务 flow；如果 AI 把候选标为 executable 却未在 flow / assertionTarget 证明局部缺口，只把不合格候选交回同一模型做最多一次语义纠偏。第二次仍不满足时，原覆盖门禁继续阻断。
- Prompt 要求只返回本次输入 / focus caseId，已保留 executable 不得重复输出；理由和 review 简写，避免解释文本挤占结构化候选。没有修改 Figma 解析、覆盖/scorer/Runner 门禁、执行模式、`router.py`、历史 YAML、`sonic_service.py` 或 `yaml_executable_scorer.py`。

线上同模型真实重放：

- 初始压缩请求直接调用线上 Gateway：32 秒，`qwen3.6-plus`，`finishReason=stop`，1803 completion token，0 fallback；8 个 caseId 全部且仅分类一次，AI 返回 5 executable / 3 manual。
- 现有基线、路径和覆盖审计接受 5 条 executable，但如实发现照片、扫描 relation 两个缺口，覆盖 10 / 12。
- 收敛第一次真实调用仍只在 review 声称补齐；语义检查识别 `TC-002 / TC-003` 的结构化结果未证明 relation。局部纠偏真实调用耗时 14 秒，仍为同一 `qwen3.6-plus`、0 fallback，并把同级关系写入两条 flow / assertionTarget。
- 最终离线重放覆盖 12 / 12、5 executable、0 未决自动候选；`executable_yaml_convergence_decision` 为 accepted，新增 `REQ-002-CHECK-02 / REQ-003-CHECK-02`，无回退验收维度。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/agent_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录/回退检查、Skill 契约 3 个 fixture，以及桌面 / 移动端视觉回归全部通过。
- 新回归覆盖 8 自动 + 12 人工的真实截断形态、同模型截断重试、REQ/兄弟分支候选隔离、已有 executable 不可回退，以及 AI review 声明与实际 flow 不一致时的单次语义纠偏。

待完成：

- 提交、推送并部署本轮修复。
- 部署后使用完全相同输入再次发起 Agent，持续轮询生成、首批冒烟、remaining 和可能的有界修复到 Agent 终态；所有 Runner job 只允许固定 OPPO `ecbfd645`，并人工复核最终 YAML、真实报告、截图和失败归因。

### 2026-07-19 部署后真实回归：收敛契约保留与必验入口点击

部署 `e4762be` 后真实验证：

- 8091 / 8088、AI Gateway、Sonic 健康；`win-runner-01` 在线并上报 `qwen3.6-plus / qwen3.6`，固定 OPPO `ecbfd645` ready。Runner 虽同时登记了华为设备，本次 Agent 明确保持 `deviceStrategy=fixed / deviceId=ecbfd645`，没有选择或下发第二台设备。
- Agent：`agent-1784469157132-cd007495`；终态 `FAILED / GENERATE_YAML`，进度 30。没有创建任何关联 Runner job，因此本轮失败与 Windows Runner、ADB、OPPO、华为或并行 Sonic 任务无关。
- PREPARE_SOURCE 正确解析 Figma 4 页 / 4 图、忽略 0 页。PLAN 将 4 张图按 4 个单图批次真实送入 `qwen3.6-plus`，4 / 4 均完成，`hardGate=false`、所有批次 `fallbackUsed=false`；Figma 继续作为软参考，没有修改现有解析链路。
- 初始 planner 请求约 2.7 万字符，`qwen3.6-plus` 正常 `finishReason=stop`、0 fallback，返回 5 executable / 3 manual。`e4762be` 的输入压缩和 6144 token 预算已生效，本次不再发生结构化输出截断。
- 初始组合覆盖 9 / 12：缺照片 reachability、扫描 relation、扫描 reachability。最终收敛聚焦 `TC-002 / TC-008 / MC-004 / MC-001`，数量目标已经满足，没有按 5 条门槛硬凑候选。

失败根因：

- `TC-002` 在补照片 reachability 时丢失了原本已有的 relation。旧语义纠偏只验证 `repairAcceptanceChecks`，没有把 `preserveAcceptanceCheckIds` 作为同一候选的硬返回契约；组合级门禁最终发现回退并原子拒绝整次收敛，但已没有机会在同一次模型调用内纠正。
- `TC-008` 的上游 AI 步骤写成“若百度网盘入口可见，则点击”。有界首屏证据曾接受这条尾链，但验收审计正确地不把条件点击计为真实 reachability：入口缺失时条件步骤会静默跳过，不能代表产品断言。
- 收敛 AI 已用 `TC-008` 同时覆盖扫描 relation / reachability，却把重复的 `MC-001` 明确保留为 manual。旧证据兜底仍强制把 `MC-001` 升为 executable，虽然不破坏覆盖，但会无意义增加一条 Runner 任务。

本轮通用修复：

- 每个聚焦候选现在携带统一 `requiredAcceptanceChecks`，由 repair、preserve、evidence 三类局部契约组成并记录 `contractRoles`。同模型有界语义纠偏检查实际 `flow / assertionTarget`，既要补新增缺口，也不能丢失已有验收；review 自述仍不计入门禁。
- 当原始显式需求要求点击某个可见文字入口，而上游 AI 写成“若/如果入口可见则点击”时，只把该目标点击规范为真实文字动作 `点击「目标」入口`。入口不存在应由 Runner 报产品断言失败，不能条件跳过；坐标、深层授权、账号、验证码和文件操作限制均未放宽。
- 有界证据兜底仍先完整经过基线、需求映射、导航、动态数据和分支守卫。最终 executable 组合形成后，只有另一条最终可执行路径真实覆盖该兜底用例的全部验收项时，才尊重 AI 的 manual 决策并去掉重复任务；若替代路径被任一守卫降级，兜底继续保留。
- 没有新增模型轮次、执行模式或数量门槛，没有修改 Figma 解析、`router.py`、历史 YAML、Runner、`sonic_service.py` 或 `yaml_executable_scorer.py`。

线上真实模型产物重放：

- 同一生产 payload、同一三条生产成功基线和同一 `qwen3.6-plus` 收敛请求真实返回 `finishReason=stop / fallback=false`。首轮返回 `TC-008 / TC-002` executable；候选局部契约发现 `TC-002` 丢失 relation 后，只对 `TC-002` 做一次同模型语义纠偏，纠偏后无剩余 feedback。
- `TC-008` 的条件目标点击被规范为真实可见文字点击，AI 返回流同时证明扫描 relation 和 reachability；`TC-002` 同时保留照片 visibility / relation / copy 并补齐 reachability。
- 使用最终代码重新应用上述真实模型返回：覆盖由 9 / 12 变为 12 / 12，缺失 0，最终 6 条 executable：`TC-001 / TC-002 / TC-003 / TC-006 / TC-007 / TC-008`。重复的 `MC-001` 保留 manual，`bounded_convergence_redundant_count=1`、fallback override 0。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录/回退检查、Skill 契约 3 个 fixture，以及桌面 / 移动端视觉回归全部通过。
- 新回归覆盖候选补新验收却丢旧验收、显式必验入口的条件点击、最终替代路径真实覆盖时去重，以及替代路径被导航守卫降级时必须保留兜底。

待完成：

- 提交、推送并部署本轮修复。
- 部署后使用完全相同需求、Figma、`qwen3.6-plus`、`win-runner-01` 和固定 OPPO `ecbfd645` 发起完整 Agent；持续轮询到 Agent、首批 smoke、remaining 和可能的 AI 修复全部终态，再人工复核最终 YAML、Runner 报告、截图和失败分类。

### 2026-07-20 部署后真实回归：同目标兄弟分支有界落地尾链

部署 `1457369` 后真实验证：

- 8091 / 8088 健康，Figma Token 和 10 个 AI Skill 就绪，text / VL 均为 `qwen3.6-plus`。`win-runner-01` 在线并上报 `qwen3.6` 模型族，固定 OPPO `ecbfd645 / PHM110` 在线；任务开始前无活动 Runner job，没有选择或下发华为或第二台设备。
- Agent：`agent-1784529244171-3db70532`；终态 `FAILED / GENERATE_YAML`，进度 30。没有创建 Runner job，因此本轮失败与 Windows Runner、ADB、手机或并发执行无关。
- PREPARE_SOURCE 正确复用 Figma 解析结果：4 页 / 4 图 / 忽略 0。PLAN 将 4 张原图按 4 个单图批次真实送入 `qwen3.6-plus`，分别约 23 / 24 / 20 / 17 秒完成，`attempted=4 / done=4 / status=completed / hardGate=false`，所有批次 `fallbackUsed=false`。
- AI 生成 8 条业务分支和 12 个场景，覆盖文档打印、照片打印、扫描复印各自的展示 / 同级 / 文案 / 可达性。初始 executable planner 正常返回 5 条 executable，数量规划目标已满足，不存在“为了 5 条硬凑”的问题。
- 初始组合覆盖 8 / 12 个验收维度，缺扫描复印 4 项。现有一次 AI 最终收敛将合并的人工候选 `MC-001` 提升为 executable；模型 review 声称已覆盖 visibility / relation / copy / reachability，但实际 flow 只补了前三项。平台最终覆盖门禁正确按结构化步骤和断言识别为 11 / 12，唯一缺 `REQ-003-CHECK-04 reachability`，没有采信 AI 自述或把不完整 YAML 下发 Runner。

失败根因：

- 上游 AI 这次没有把扫描展示与扫描跳转拆成两个候选，而是合并为一个人工项：先查入口、检查文案 / 同级，再以“若存在则点击、若不存在则记录缺陷”描述跳转。现有有界证据能用扫描成功基线 `d623c1e73180bfac` 组合来源页展示检查，因此为 `MC-001` 建立了 `source_ui_assertion`，但该证据只允许 visibility / relation / copy。
- 同一个人工项中的条件跳转尾链因包含人工缺陷记录分支而不能作为 Runner 尾链，这是正确的安全限制。与此同时，文档 `TC-003` 和照片 `TC-004` 已有相同可见目标“百度网盘”的 executable 点击后稳定首屏尾链，但旧组合只接受同一 REQ 内的尾链，AI 没有机会把“当前扫描分支成功父路径”和“相同目标的已验证短尾链”组合起来。
- 这解释了为什么本地旧夹具和前一轮线上输出能通过，而本轮千问换成“展示 + 跳转合并人工项”后又失败：不是环境差异，而是候选结构的随机变化暴露了未覆盖的数据形态。

本轮通用修复：

- 保留现有一次最终 AI 收敛，不新增模型轮次。若当前分支已有自己的 `verified_execution` 导航基线和 AI 来源页展示证据，可向 AI 提供兄弟分支中**相同可见目标**的有界落地尾链；兄弟分支只捐赠目标点击后的稳定观察，不捐赠导航路径。
- 捐赠候选必须来自上游 automatic、当前为 executable，并同时具备 `baselineGrounded / baselineVerified / pathPlanApplied`；目标动作文字规范化后必须与当前缺口目标完全相等，不能用前后缀子串冒充。落地观察不得引用捐赠分支点击目标之前的来源页面，当前分支继续使用自己的基线、requirement refs 和来源页断言。
- 同 REQ 尾链仍优先于兄弟分支，避免新能力抢占原有更强证据。跨分支尾链本身必须独立通过现有有界首屏可执行检查，不允许再借其它模糊尾链拼接。
- 坐标、账号、密码、验证码、确认授权、文件选择和其它深层外部动作限制均未放宽；最终仍经过验收覆盖、YAML 转换、scorer、dry-run 和真实 Runner 门禁。
- “若未实现则记录缺陷”等人工备选叙述继续保留在原始候选和审计中，但不再拼进 Runner 的 `aiWaitFor`；只按分句移除条件人工分支，同一原始断言中的独立合法产品条款仍保留。Runner 只执行明确的可见 / 同级 / 文案产品断言，入口缺失应真实失败而不是条件跳过。
- 没有修改 Figma 解析、`router.py`、历史 YAML、Runner、`sonic_service.py` 或 `yaml_executable_scorer.py`。

本轮生产产物离线重放：

- 使用线上用例集 `agent-agent-1784529244171-3db70532`、线上原始扫描人工项、线上文档 `TC-003` 落地尾链和扫描成功基线 `d623c1e73180bfac` 重放。证据审计为 `kind=bounded_landing / sourceCaseId=MC-001 / tailSourceCaseId=TC-003 / sharedTargetTail=true`，一次覆盖扫描 4 个验收维度。
- 组合从 8 / 12 变为 12 / 12，`missing=[]`，`MC-001` 成为 remaining executable；没有新增数量目标或模型调用。
- 转出的扫描 YAML 为 `05-扫描复印百度网盘点击后首个可见页校验.yaml`，使用真实可见文字进入扫描复印并点击百度网盘，结构校验、可执行校验均通过，scorer `100 / executable / 0 warning`，无坐标。Runner flow 中不再包含“若 UI 已实现 / 记录缺陷”的人工叙述。
- 负向夹具确认：目标文字不同或仅为前后缀变体、落地断言泄漏捐赠分支来源页，或捐赠候选没有已验证 executable 基线时，只能保留来源页展示证据，不能满足 reachability；同 REQ 候选仍优先于跨 REQ 候选。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录 / 回退检查、Skill 契约 3 个 fixture，以及桌面 / 移动端视觉回归全部通过。

### 2026-07-22 部署后完整回归：横滑边缘手势、重复修复与视觉父路径归一化

部署 `9ebb40b` 后发起完全相同的百度网盘 Agent：

- Agent：`agent-1784717510972-926edb56`；终态 `FAILED / RERUN / 95%`，错误为“重跑后仍有失败或超时任务”。
- 输入保持原固定参数：`scope=regression`、`RUNNER_JOB`、`win-runner-01 / ecbfd645 / fixed`、`qwen3.6-plus`、`com.xbxxhz.box`。全部 dry-run、smoke 和修复重跑只使用 OPPO PHM110，没有向第二台设备下发。
- 8091 / 8088、AI Gateway、Sonic 和 Runner 健康；Figma 解析 4 页 / 4 图，分 4 个单图批次全部真实送入 `qwen3.6-plus`，4 / 4 完成、无 fallback。
- PLAN 生成 8 条 AI 业务流；GENERATE_YAML 生成 6 条 YAML / 12 个场景，6 / 6 通过 Task Server 静态和可执行门禁，3 / 3 Runner dry-run 成功。这证明上一轮空标量 YAML 修复已生效。
- 固定 OPPO 首批 smoke 仅文档展示用例成功；扫描展示与照片展示失败，smoke 为 1 / 3，未达到扩展阈值，因此 remaining 3 条没有执行。
- 最终 6 条 YAML 在结构上覆盖文档 / 照片 / 扫描三个分支的展示、同级、文案和可达性，且没有坐标、XPath、selector 或 ADB swipe；但照片与扫描 smoke 未通过，不能判定需求回归完成。

真实执行根因：

- 照片 YAML 点击“一寸照规格页”，固定 OPPO 真机弹窗明确只有 `5寸 / 6寸 / 7寸 / A4`。AI 修复正确使用 3 张报告关键帧、同 case Figma Node `1:70` 的“5寸照片”和照片分支成功基线 `652583bdad841b93`，提出 `一寸照规格页 -> 5寸照片`；候选仍为 `100 / executable`，但被 `source_backed_navigation_target_removed` 错误拒绝。
- 错误拒绝来自视觉 AI 的父路径表示不一致：旧叶子证据把自身 `一寸照规格页` 再写入 `parentPath`，替代叶子 `5寸照片` 的 `parentPath` 只写到照片打印。两条证据实际属于同一父分支，旧门禁却要求数组完全相等。
- 扫描 YAML 已成功点击“扫描复印”并等待到“小白扫描王”导入页；报告中横滑定位框中心位于逻辑屏幕 `x=522 / width=540` 的最右边缘，Runner 实际执行 `input swipe 1044 1374 0 1374 1000`。Android 15 把右边缘向内滑识别为系统返回，页面因此回到 App 首页，并非扫描导航失败或滑动次数不足。
- 旧失败分类与 repair prompt 允许每轮继续追加 1-2 次同方向横滑。首次修复把 1 次横滑变为 2 次，post-rerun autonomy 又变为 3 次；两次重跑均在错误首页继续查找导入栏并失败。这与已有“横向列表幂等规范为一次官方 aiScroll”的生成规则冲突。
- 扫描可达 YAML 的执行 flow 没有人工条件动作，但文件名 / tags / `baseline.repair_hint` 仍残留“需人工确认路径 / 若未找到 / 建议人工验证”等已被可信 `ai_case_plan` 替代的旧元数据，应从 Runner 文件中移除。

本轮通用修复：

- 横向 `aiScroll` 标准化会保留一次官方动作，并把自然语言定位区域约束为“从横向内容区中部起手，避开屏幕左右边缘”；扫描导入栏的确定性生成路径也使用同一安全描述。没有新增坐标或修改 Runner。
- 修复候选门禁按 task 审计横向 `aiScroll` 数量：原 YAML 没有时最多新增一次；已有横滑时只能替换原动作，继续追加会以 `duplicate_horizontal_scroll_repair` 拒绝。安全的单动作区域替换仍可通过。
- 横滑失败分类识别 `screen bounds / input swipe / returned home` 等边缘手势证据，明确要求替换原 `aiScroll`，不再建议增加距离或第二次横滑。`repair_patch_planner.v1`、AI repair guide 和 legacy repair prompt 的策略已同步。
- 视觉父路径比较会先移除与自身 `navigationLeaf` 完全重复的尾段，再判断同 case / REQ / target 的替代叶子是否位于同一父分支。真机否定、关键帧、当前 Figma 替代叶子、当前分支成功基线和断言保留等原门禁均未放宽。
- 已验证 `ai_case_plan` 渲染前会清理过期的人工 tags、repair hints、data / automation 元数据；拆分 YAML 的文件名也使用清理后的标题。真正包含人工条件的 plan 仍被 `_verified_case_plan_for_yaml` 拒绝。

线上生产数据离线重放：

- 原照片失败 YAML + 原 AI 5 寸修复候选 + 4 批视觉证据 + 原 Runner 错误 + 3 张关键帧 + 原照片分支基线：候选现在 `ok=true / 0 issue`，审计记录 `一寸照规格页 -> 5寸照片`、`TC-002 / REQ-002`、Figma Node `1:70`、基线 `652583bdad841b93`。
- 原扫描失败 YAML 经运行时规范化后仍只有 1 个 `aiScroll`，并包含内容区中部 / 避开左右边缘约束；线上 AI 生成的第二次横滑修复被 `duplicate_horizontal_scroll_repair` 正确拒绝。
- 已验证计划样例生成的 task、拆分文件名和 baseline comments 不再包含 `待确认 / 需人工 / 若存在 / 若不存在 / 记录缺陷`，原可见文字动作和精确断言保持不变。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/yaml_service.py task_server/services/agent_service.py task_server/services/ai_skill_service.py task_server/services/repair_service.py tests/backend_static_checks.py
git diff --check
npm test
```

- RED 测试先分别复现：安全横滑描述缺失、边缘手势仍建议追加、重复横滑候选通过、视觉父路径尾段不一致导致正确 5 寸修复被拒、已验证 Runner YAML 残留人工元数据。
- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录 / 回退检查、Skill 契约 3 个 fixture，以及桌面 / 移动端视觉回归全部通过。

模型升级记录：

- 本次真实回归继续使用固定参数 `qwen3.6-plus`，避免把模型变化混入修复验证。
- 2026-07-22 阿里云 Model Studio 官方目录显示通用多模态 Plus 最新为 `qwen3.7-plus`；没有 `qwen3.8-plus`，`qwen3-8b` 是参数规模名称。当前线上 `/api/models` 只暴露 `qwen3.6-plus`，后续应先验证 AI Gateway / Midscene 模型族与视觉路由，再单独升级默认 text / VL 配置，不在本轮回归修复中猜测切换。

待完成：

- 本轮修复已创建本地提交；由用户 push、部署，Codex 不尝试 push。
- 部署后立即用完全相同参数重新发起百度网盘 Agent，持续监督到终态。重点确认照片 5 寸候选通过并在固定 OPPO 重跑、扫描只保留一次内容区中部横滑、smoke 达标后 remaining 全部执行，以及 6 条最终 YAML 不再带过期人工元数据。

### 2026-07-22 部署后真实回归：Figma 父路径展示后缀阻断真机叶子纠正

部署 `0103401` 后发起完全相同百度网盘 Agent：

- Agent：`agent-1784686459528-f818e642`；输入保持 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`，只向 OPPO PHM110 下发本 Agent 的 dry-run 和真机任务。
- 8091 / 8088、AI Gateway、Figma、Runner 和固定 OPPO 健康。Figma 4 页 / 4 图分 4 个单图批次全部真实送入 `qwen3.6-plus`，4 / 4 completed，无 retry、无 fallback，耗时约 10 / 17 / 19 / 11 秒。
- `0103401` 的生成修复已在线上生效：PLAN 生成 8 条 AI 业务分支和 12 个场景；生成 6 个 YAML，6 / 6 executable，服务端静态校验无 error / warning，Runner dry-run 6 / 6 通过。
- 三个业务入口均分别生成展示和可达性 YAML。扫描复印两条使用成功基线 `d623c1e73180bfac`，标题不再含“待确认”，Runner flow 不含“若存在 / 记录缺陷”，包含明确 `aiAssert`；scorer 均为 executable。三个入口的展示、同级关系、文案和可达性均进入自动化覆盖。
- 首批 smoke 在固定 OPPO 串行执行：文档打印展示用例成功；照片打印展示用例在尺寸弹窗点击“一寸照规格页”失败。真机明确报告弹窗只有 5寸、6寸、7寸、A4 等选项，失败正确分类为 `SCRIPT_ISSUE / element_not_found`，报告和关键帧均已上传。
- 首批通过 1 / 2，未达到扩展门槛，remaining 4 条按策略暂停。Agent 最终为 `FAILED / COLLECT_REPORT / 95%`，不是生成覆盖、设备、ADB、Sonic 或报告上传问题。

失败根因：

- 修复 AI 使用 3 张报告关键帧、当前 Figma 的 5寸页面和照片分支成功基线 `46123c7c7595934e`，正确提出把“一寸照规格页”替换为“5寸照片”，并完整保留百度网盘断言；候选 YAML 仍为 100 / executable。
- 平台仍以 `source_backed_navigation_target_removed` 拒绝候选。两条同 case / REQ / 目标的 Figma 证据父路径分别为 `App首页 / 照片打印 / 规格选择` 和 `App首页 / 照片打印 / 规格选择页`；旧门禁按父路径字符串数组完全相等比较，把仅多一个展示后缀“页”误判为不同业务父路径，因此 `sourceLeafRuntimeOverrides=[]`。

本轮通用修复：

- 真机叶子纠正门禁在比较 Figma 父路径段时，只在前缀至少有两个有效字符、且不是首页 / 主页语义时规范化末尾展示后缀“页 / 页面”；单独“页面”、`网页 / 分页`、`首页 / App首页 / 主页` 均保持原值，不能产生空路径键或词义别名。caseId、requirementId、目标文案、完整父路径层级、失败关键帧、真机明确否定、当前 Figma 替代叶子、已引用分支基线、替代动作必须位于目标断言前，以及精确断言合同等门禁均未放宽。
- 回归夹具使用生产差异 `规格选择` 与 `规格选择页`；修改前稳定失败，修改后通过，并补充空值、词义型“页”和首页 / 主页负例。目标不同、case / REQ 不同、父路径层级不同、无真机否定、无关键帧、无当前 Figma 替代叶子或无已引用分支基线时仍不能纠正。
- 使用线上 Agent 完整产物离线重放：候选 `ok=true / issues=[] / assertionContractPreserved=true / executable`，审计为 `一寸照规格页 -> 5寸照片 / TC-002 / REQ-002 / 3 张关键帧 / baseline 46123c7c7595934e`。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录 / 回退检查、Skill 契约 3 个 fixture，以及桌面 / 移动端视觉 smoke 全部通过。

待完成：

- 由用户 push、部署本轮提交；部署后继续使用完全相同参数发起百度网盘 Agent，重点确认照片 5寸修复候选通过门禁并在固定 OPPO 自动重跑，随后恢复 remaining 扩展执行到 Agent 终态。

### 2026-07-22 最新真实回归：已验证执行计划写入 YAML 时丢失

部署 `5303203` 后已先完成 MeterSphere 线上配置校验，平台保存了 MeterSphere Base URL、`3D业务` 项目、Access Key / Secret Key（配置读取只返回脱敏值），`/api/api-testing/metersphere/health` 返回 `health_ok=true`、MeterSphere `code=100200`。随后继续监督百度网盘回归。

最新 Agent：

- Agent：`agent-1784684943625-4d915138`。
- 输入保持不变：`基础打印新增百度网盘入口`，Figma 原链接，`scope=regression`，`executionMode=RUNNER_JOB`，`runnerId=win-runner-01`，固定 OPPO `ecbfd645`，模型 `qwen3.6-plus`，包名 `com.xbxxhz.box`。
- Runner 和设备预检正常：`win-runner-01` 在线，OPPO PHM110 / `ecbfd645` 在线且 app `4.45.0` 可用；未选择第二台设备。
- 终态：`FAILED / GENERATE_YAML / 30%`，未创建 Runner job，失败与 Windows Runner、ADB、Sonic 或手机无关。
- Figma 4 页 / 4 图全部真实送入 `qwen3.6-plus` 并完成；PLAN 成功生成 8 条业务流、12 个场景。
- GENERATE_YAML 产物生成 5 个 YAML，但最终只有 4 个 executable；覆盖门禁正确阻断，缺口集中在 `REQ-003 扫描复印` 的展示、同级、文案、可达性 4 个验收点。

根因：

- 生成 payload 中扫描 case `TC-006` 已经是 executable，且 `ai_case_plan` 具备 `baselineGrounded=true / baselineVerified=true / pathPlanApplied=true`，引用扫描成功基线 `d623c1e73180bfac`，计划 flow 也包含扫描复印导航、等待百度网盘入口、点击百度网盘和首屏落地断言。
- 但 `case_to_task_yaml` 最终写 YAML 时仍优先使用旧的 `case.steps / case.assertions / title`，没有把服务端已验证的 `ai_case_plan.flow / assertionTarget` 作为 Runner YAML 渲染合同。
- 旧标题和步骤残留“待确认布局 / 若存在 / 记录缺陷”，生成的扫描 YAML 因人工条件文案和缺少明确 `aiAssert` 被 scorer 降级，导致 12 个验收点只确认 8 个可执行覆盖。

本轮通用修复：

- `case_to_task_yaml` 增加已验证计划渲染路径：只有 `baselineGrounded + baselineVerified + pathPlanApplied` 同时为真、且计划自身不含人工条件分支时，才使用 `ai_case_plan.flow` 生成 Runner 步骤，并把 `assertionTarget` 写入最终 `aiAssert`。
- 已验证计划场景下清理任务标题中的审稿型尾巴，例如“待确认 / 需人工 / 人工复核 / 记录缺陷”等，避免标题本身触发人工条件降级。
- 未验证计划、计划 flow 自身带“若存在 / 若不存在 / 记录缺陷 / 人工确认”等条件分支时，仍走原有保守路径并由 scorer / 覆盖门禁阻断。
- 未修改 `yaml_executable_scorer.py`、Runner、Sonic、Figma 解析、坐标策略、账号授权或深层外部文件操作限制。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/yaml_service.py task_server/services/ai_skill_service.py task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check
npm test
```

- RED 测试先复现了线上失败：已验证 `ai_case_plan` 未被用于 YAML 渲染时，扫描 YAML 仍含“待确认 / 若存在 / 记录缺陷”并被降级。
- 修复后后端 61 项、undefined-name、前端 69 项、AI Gateway 46 项、动态模型目录 / 回退检查、Skill 契约 3 个 fixture，以及桌面 / 移动端 visual smoke 全部通过。

待完成：

- 提交本轮修复；由用户 push、部署。
- 部署后继续使用完全相同的百度网盘 Agent 参数发起真实线上回归；重点确认扫描复印 YAML 由已验证 `ai_case_plan` 渲染，三个业务入口的展示、同级关系、文案和可达性都被 executable YAML 覆盖，然后再进入固定 OPPO Runner 执行。

### 2026-07-21 最新真实回归：扫描 relation 收敛候选识别

部署 `058d4f6` 后重新发起有效 Agent `agent-1784595694809-776c1a1a`：

- 输入、需求正文、Figma、模型和固定设备均正确：`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.6-plus`，小白学习打印 `com.xbxxhz.box 4.45.0 (357)`。
- 线上 `8091 / 8088`、AI Gateway、Sonic 健康；Runner 清单里 OPPO 和华为都在线，但本 Agent 没有创建 Runner job，也没有向第二台设备下发。
- Figma 4 页 / 4 图全部真实送入 `qwen3.6-plus`：4 个视觉批次均完成，耗时约 17 / 21 / 19 / 24 秒；PLAN 生成 8 条 AI 业务分支。
- 终态 `FAILED / GENERATE_YAML / 30%`，失败已从上一轮扫描 reachability 缩小为单一缺口：`REQ-003 [acceptance:relation] 扫描复印：校验百度网盘入口与当前页面同级入口的层级和位置关系`。

根因：

- 生成结果已有 7 个 executable YAML，scorer 均为 100；`06-扫描复印百度网盘点击后首个可见页校验.yaml` 已覆盖扫描复印的入口可见、文案和点击后可达。
- 该扫描用例的 `requirementRefs` 明确包含完整 REQ-003 四个验收维度，其中包括同级关系；但 `_case_intends_requirement_acceptance` 只读取 title / scenario / business_path / expected / tags / originalFlow，没有把完整 requirementRefs 纳入“可修复验收意图”。
- 因此最终 coverage convergence 没有把这个已具备同分支可信导航的 executable 用例聚焦为 relation 修复候选，AI 没机会在点击「百度网盘」之前补入“同级 / 并列 / 位置关系”断言；最终门禁正确阻断 Runner。

本轮通用修复：

- `_case_intends_requirement_acceptance` 现在把 `coverage / requirementRefs / requirement_point` 作为验收维度意图文本来源，但分支身份仍必须来自 title / scenario / goal / business_path / expected / tags / originalFlow 等候选自身上下文，避免泛化授权流仅凭 requirementRefs 误绑定具体分支。
- 新增回归证明：扫描复印 landing executable 若 requirementRefs 含完整 relation 义务，会进入 convergence repair 候选并要求修复 `REQ-003-CHECK-02`；但在补入具体“同级 / 并列”断言前，仍不能算覆盖 relation。
- 保留既有负例：泛化“任意打印子页面”授权风险流不能被 requirementRefs 单独绑定到文档 / 照片主分支；最终门禁、scorer、Runner、Figma、坐标和账号/授权限制均未放宽。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
git diff --check
npm test
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录 / 回退检查、Skill 契约 3 个 fixture，以及桌面 / 移动端视觉回归全部通过。

待完成：

- 提交本轮修复；由用户推送、部署。
- 部署后继续用完全相同参数重新发起 Agent，重点确认扫描 relation 被收敛修复为 executable，随后生成阶段通过并只向固定 OPPO 创建 smoke / remaining Runner job。

待完成：

- 提交、推送并部署本轮修复。
- 部署后继续使用完全相同需求、Figma、`qwen3.6-plus`、`win-runner-01` 和固定 OPPO `ecbfd645` 发起完整 Agent；必须持续监督到首批 smoke、AI 修复重跑、remaining 和 Agent 全部终态，再人工复核所有 YAML、Runner 报告、截图 / 录屏和失败分类。

### 2026-07-20 部署后真实回归：真机证据纠正软视觉叶子

部署 `fe44f14` 后真实验证：

- 8091 / 8088、AI Gateway、Sonic 健康；text / VL 均为 `qwen3.6-plus`。`win-runner-01` 在线并上报 `qwen3.6` 模型族，固定 OPPO `ecbfd645` ready；全部 dry-run、smoke job 都是 `deviceStrategy=fixed`，没有向华为或第二台设备下发。
- Agent：`agent-1784526099999-fca69d80`；终态 `FAILED`。本轮已越过此前 `GENERATE_YAML` 阻断：生成 6 条用例 / 12 个场景 / 6 个 executable YAML，6 / 6 服务端校验通过，3 / 3 Runner dry-run 通过。
- Figma 正确解析 4 页 / 4 图，并按 4 个单图批次全部送入 `qwen3.6-plus`；4 / 4 完成、`fallbackUsed=false`、`hardGate=false`。视觉资料继续是软参考，没有修改现有 Figma 解析。
- 首批 smoke 在固定 OPPO 串行执行到终态：文档打印 1 条真实通过；扫描复印和照片打印 2 条失败。报告汇总正确保留 `passed=1 / broken=2 / productFailed=0`，没有把已通过冒烟覆盖成全失败。因首批通过率未达门槛，3 条 remaining 被如实延后，没有创建第二台设备任务。

失败根因：

- 照片用例在尺寸弹窗点击“一寸照”，Runner 明确报告当前弹窗没有该选项。失败修复 AI 使用 3 张报告关键帧、Figma Node `1:70` 的“5寸照片”页和当前照片分支成功基线，正确提出将失败步骤改为“5寸照片”，保留“百度网盘”断言；候选 YAML 仍为 executable / scorer 100。
- 旧 `source_backed_navigation_target_removed` 门禁把 Figma 软参考叶子视为永不可替换，即使本次真机已明确否定它，也错误拦截上述 AI 修复。这里不是 scorer 或 Runner 脚本失败，而是生成阶段软证据与执行阶段新证据的优先级缺少受限纠正路径。
- 扫描报告的视觉模型英文结论明确写出右侧同级导入 icon `partially visible`、文案 `cut off and not visible`。横向裁切规则只覆盖中文表达，未进入有界 `aiScroll` 修复，随后 AI 复检中的引用被来源校验降级为 `review_source_mismatch`，导致没有提取关键帧和修复 YAML。

本轮通用修复：

- Figma 仍是生成阶段软参考，不能仅凭历史基线替换其尺寸 / 模式 / 产品叶子。只有本次 Runner 错误明确否定旧叶子、存在报告关键帧、同 case / REQ / 父路径的当前 Figma 证据提供替代叶子、AI 说明同时引用新旧叶子、已引用当前业务分支基线证明父路径，且原始精确文案断言完整保留时，才允许一次真机证据纠正。
- 成功基线只证明父路径结构，不要求样例值与替代值相同；例如成功 6 寸照片基线可证明照片打印规格路径，具体 5 寸值仍必须来自当前 Figma 和本次失败帧。这样既能复用基线，也不会把单一需求值硬编码进门禁。
- 修复产物新增 `sourceLeafRuntimeOverrides` 审计，记录 from / to leaf、case / requirement、Figma 来源、引用基线和关键帧数量。缺任一证据时，原 `source_backed_navigation_target_removed`、断言契约、分支基线、YAML 可执行性和 scorer 门禁继续拒绝。
- 横向裁切识别补齐视觉模型常见英文表达（`partially visible / cut off / not visible / to the right` 等），与既有中文证据走同一条最多一次、可见文字区域描述、禁止坐标和 ADB swipe 的 AI `aiScroll` 修复路径。
- 没有新增模型轮次、执行模式或设备；没有修改 Figma 解析、`router.py`、历史 YAML、`sonic_service.py`、`yaml_executable_scorer.py` 或 Windows Runner 脚本。

线上失败数据离线重放：

- 使用生产照片原始 YAML、生产 AI 已生成但被拒的 5 寸候选、生产 4 批视觉证据、生产 Runner 错误和 3 张关键帧，配合本地真实召回的 6 寸照片分支基线重放：候选 `ok=true`、0 issue、断言契约保留、execution level 为 executable；审计记录 `一寸照 -> 5寸照片`、Figma 来源和 3 张关键帧。
- 使用生产扫描原始 YAML 和完整英文 Runner 结果重放：稳定识别为 `script_issue / can_auto_repair=true`，建议在具体同级导入区域执行官方 `aiScroll`；来源清洗后仍保持该分类，不再误降级为 `review_source_mismatch`。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
npm test
git diff --check
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录 / 回退检查、Skill 契约 3 个 fixture，以及桌面 / 移动端视觉回归全部通过。
- 新回归覆盖 Runner 英文横向裁切证据，以及“有关键帧但没有真机否定”仍禁止替换、“真机否定 + 同 case Figma 替代叶子 + 当前分支父路径基线 + 断言不变”才允许纠正。

待完成：

- 提交、推送并部署本轮修复。
- 部署后继续使用完全相同需求、Figma、`qwen3.6-plus`、`win-runner-01` 和固定 OPPO `ecbfd645` 发起完整 Agent；持续轮询首批 smoke、AI 修复重跑、修复恢复后的 remaining 扩展及 Agent 到终态，并人工复核 6 条 YAML、所有真实报告、截图和失败分类。

### 2026-07-20 最新真实回归：执行阶段暴露人工提示与日志可观测性问题

部署 `d368f0d` 后真实验证：

- Agent：`agent-1784544927906-70b76349`；终态 `FAILED / COLLECT_REPORT / 95%`。这次不再卡在 PLAN，也没有在 GENERATE_YAML 阶段被覆盖门禁阻断。
- Figma 正确解析 4 页 / 4 图，4 个单图批次全部送入 `qwen3.6-plus` 并完成，耗时约 22 / 28 / 20 / 21 秒；`fallbackUsed=false`。
- PLAN 成功生成 8 条 AI 业务流；GENERATE_YAML 成功生成 6 条 YAML / 12 个场景，VALIDATE_YAML 和执行前 dry-run 均通过。
- 所有真实 Runner job 均固定 `win-runner-01 / ecbfd645 / fixed`，没有向第二台设备下发。首批 smoke 2 成功 / 1 失败，随后 expanded remaining 3 条执行完成但失败；总计 2 成功 / 4 失败。

失败根因：

- 扫描复印展示 YAML 的标题 / tags / reason 已明确包含“需人工确认 / needs_review”，但后续展示类提升逻辑仍把它提升为 executable，导致人工未消解项进入 Runner。
- 同一 YAML 中 `aiTap: 检查页面导入或文件选择区域` 是页面检查语义，不是可点击目标。Runner 真实点击后进入系统照片选择器，页面变为“此应用只能访问您选择的照片”，自然无法再看到“百度网盘”。
- 前端时间线在轮询时整体重绘 `agent-progress`，技术日志 `<details>` 的展开状态和滚动位置没有保存；同时实时轨迹只保留最后 12 条，用户无法展开后停留追查长执行过程。RUN_SONIC 摘要也优先展示旧 step summary，没有使用已有 `artifacts.jobProgress` 的最新 Runner 进度。

本轮通用修复：

- 生成用例只要标题、场景、reason、tags 或 automation 字段显式包含 `needs_review / manual / 人工确认 / 人工复核 / 需人工 / 待确认` 等提示，就不能被展示类修正规则提升为 executable。
- YAML 入库前修复新增页面检查型 `aiTap` 识别：`检查 / 校验 / 验证 / 查看 / 观察 / 判断 / 识别 / 确认页面...` 且没有真实点击动作时，自动改为 `aiWaitFor`，保留原可见文字和 timeout，不放宽坐标、账号、授权、文件选择或深层外部动作限制。
- Agent 前端时间线新增技术日志状态缓存：轮询刷新后保留技术日志展开状态和滚动位置；技术轨迹从最后 12 条扩展为最后 80 条，并显示当前展示数量。
- RUN_SONIC 时间线摘要优先使用已有 `artifacts.jobProgress` / `jobProgressByPhase`，展示最新成功 / 失败 / 执行中 / 排队中、等待耗时、当前任务和更新时间。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
python3 -m py_compile task_server/services/yaml_service.py tests/backend_static_checks.py tests/frontend_static_checks.py
git diff --check
npm test
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录 / 回退检查、Skill 契约 3 个 fixture，以及桌面 / 移动端视觉回归全部通过。

待完成：

- 提交本轮修复；由用户推送、部署。
- 部署后继续使用完全相同需求、Figma、`qwen3.6-plus`、`win-runner-01` 和固定 OPPO `ecbfd645` 发起完整 Agent；重点确认人工提示项不会进入 Runner、页面检查型 tap 不再误点系统选择器，以及技术日志可展开停留查看。

补充前端修复：

- 用户在最新线上页面验证发现“技术日志 / 实时轨迹”展开后会立刻收回。根因是 Agent 轮询后可能走 `showAgentWorkbench()` 整页重绘路径，而前一版只在 `updateAgentWorkbenchDynamic()` 局部刷新里恢复时间线 details 状态；同时技术日志点击事件仍可能冒泡到父级时间线 step。
- 已把时间线状态保存 / 恢复接入整页重绘路径，并增加恢复期间的 `ontoggle` 抑制，避免程序化恢复 open 状态时反向覆盖用户操作；技术日志自身增加 pointer/click 事件隔离，防止点击 summary 时触发父级 step 折叠。
- 已验证：`python3 tests/frontend_static_checks.py`、`python3 -m py_compile tests/frontend_static_checks.py`、`git diff --check`、`npm test` 全部通过。

补充生成门禁修复：

- 最新部署后 Agent `agent-1784547916186-4ba828d0` 终态 `FAILED / GENERATE_YAML / 30%`，未创建 Runner job。Figma 4 页 / 4 图全部真实送入 `qwen3.6-plus` 并完成，PLAN 成功；失败点是最终生成 5 条 YAML 但只有 4 条 executable，REQ-003 扫描复印 4 个验收点缺失。
- 生产产物显示 TC-003 同时存在两类信息：标题 / tags 残留“需人工确认 / 待确认”，但 `ai_case_plan` 已经具备 `baselineGrounded=true / baselineVerified=true / pathPlanApplied=true`，并引用扫描成功基线 `d623c1e73180bfac` 形成稳定扫描父路径和百度网盘点击步骤。上一版门禁把 stale 人工提示当作最终事实，错误地把已被平台证据修复的 TC-003 降为 `needs_review`。
- 已收窄规则：人工提示默认仍降级；只有同一 case 同时具备可信基线 grounding、已验证 baseline、path plan applied、scope review 通过、scorer 高分且 flow 不包含“若不存在 / 记录缺陷 / 人工确认”等条件人工分支时，才把残留人工文案视为 stale metadata，不阻断 executable。
- 生产 TC-003 离线判定已变为 `manualHint=true / verifiedPlan=true / effective=executable`；条件人工分支负例仍保持 `needs_review`。
- 已验证：`python3 tests/backend_static_checks.py`、`python3 -m py_compile task_server/services/yaml_service.py tests/backend_static_checks.py`、`git diff --check`、`npm test` 全部通过。

### 2026-07-20 最新回归补充：扫描 reachability 覆盖识别

部署 `40958cd` 后发起 Agent `agent-1784549118642-bd8e3b01`：

- 输入、Figma、模型和固定 OPPO 均正确：`win-runner-01 / ecbfd645 / fixed`，`com.xbxxhz.box 4.45.0`。
- Figma 4 页 / 4 图全部送入 `qwen3.6-plus` 并完成，PLAN 成功，未创建 Runner job。
- 终态仍为 `FAILED / GENERATE_YAML / 30%`，但失败已缩小为单一缺口：`REQ-003 [acceptance:reachability] 扫描复印：点击百度网盘入口并校验目标页面稳定可达`。

根因：

- 生成产物中的扫描复印步骤已经有目标点击和后续等待：`点击「百度网盘」入口` -> `等待跳转至百度网盘相关页面`。
- `case_covers_requirement_acceptance` 的 reachability 终态词只认授权页、文件列表、落地页、稳定可达等固定词，没有把“目标名 + 相关页面”识别为有界首屏落地证据，导致最终 portfolio 覆盖门禁误判扫描 reachability 缺失。

本轮修复：

- reachability 覆盖判断在目标点击动作之后，允许“目标入口名 + 相关页面”作为有界首屏落地终态，例如“百度网盘相关页面”。
- 加负向约束：`未 / 没有 / 无法 / 不能 / 失败 ... 相关页面` 不能满足 reachability，避免把失败观察当覆盖。
- 不修改 Runner 动作、scorer、Figma、坐标、账号、授权或深层外部文件选择限制。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
git diff --check
npm test
```

- 全量结果：undefined-name、后端 61 项、前端 69 项、AI Gateway 46 项、动态模型目录 / 回退检查、Skill 契约 3 个 fixture，以及桌面 / 移动端视觉回归全部通过。
