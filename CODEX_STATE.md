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
- `deploy/install-windows-runner-service.local.ps1`

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
