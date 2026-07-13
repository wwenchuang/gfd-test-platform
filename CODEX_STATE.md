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
