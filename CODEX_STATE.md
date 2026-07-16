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
