# 简化跨平台 UI 测试流程 Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: 使用 executing-plans 按批执行；每批完成必要检查、真实验收和状态记录后再推进。此文是用户要求的修改计划，不代表实现、部署或验收已完成。

**Goal:** 使用者选择基线与手机即可执行，自动取得本人本次报告和飞书通知；文字、录制与视频统一生成可编辑、可验证的执行用例。

**Architecture:** 在现有 Python/原生 JS 平台内收敛流程，通过轻量执行批次连接现有任务、Runner/Sonic 和报告；公共业务步骤由平台编译器生成各端执行副本。身份权限与 AI Gateway 复用现有实现，不另建账号或调度平台。

**Tech Stack:** 现有 Python 服务、SQLite、YAML、原生 JS、Windows/Mac Runner、Midscene、Sonic、现有 AI Gateway、现有 Word 渲染与飞书卡片；视频阶段增加受限后台解码工具。

**设计依据：** [完整设计](../specs/2026-10-13-simple-cross-platform-ui-design.md)。

## Global Constraints

- 2026-10-13 用户已明确批准按整套计划实施；从隔离分支分批开发、验证、发布。飞书只向明确配置且授权的目标发送相应测试报告。
- 实施从当时最新 main 建隔离分支，重读 AGENTS/CODEX_STATE，保留所有既有用户改动。本次调查基准为 bb7bc93。
- 不大规模重构 router.py；dag_safe/parallel_dag 保留；不新增虚构模式或非官方 Midscene action/参数。
- 业务手机选择与打印机数据分开；已选择手机不可用不得回退其他手机。9888E0094F2A 不作为手机标识。
- Android package/iOS bundle 的映射、能力和权限都由服务端校验，不能依据前端名字或工作目录推测。
- 自动化生成小/中/大需求分别 3/5/最多 8 条，首批最多 3 条，冒烟至少 50% 才继续；正式基线既有策略保持明确，不将生成上限误用于完整基线套件。
- 优先 Top3 相似成功基线；普通入口短等待，上传/导入/生成等长任务单独上限；AI 不能改门禁、篡改断言、把环境错误当脚本错误。
- 旧资产按需只读适配；新执行建立可信身份和快照，不补猜历史数据、不批量改历史基线。
- 新功能每批开关上线；失败、取消、未执行真实显示。新报告只关联本批次的执行证据。
- 性能对比作为发布条件；不增加每卡片独立轮询，不在热路径扫描完整审计/报告库，不在请求线程解码长视频。

## 交付顺序与依赖

| 批次 | 工作包 | 使用者可见结果 | 依赖 |
| --- | --- | --- | --- |
| P0 | T1–T3 | 手机状态可靠、用例兼容范围明确、执行来源可追溯 | 现有身份和 Runner |
| P1 | T4–T7 | 选机跑基线 → 本人报告 → 飞书下载 | P0 |
| P2 | T8–T9 | 大白话/已有录制 → 中文步骤 → 试跑保存 | P1 的执行和报告契约 |
| P3 | T10 | 上传录屏 → 步骤证据 → 可执行用例 | P2 |
| P4 | T11 | 最近配置、公共步骤、参数化、多设备对比等 | 核心链路验收 |

P0/P1 首先形成可用闭环，再推进视频；不把五批合并成一次大上线。每批的“实现通过”“线上部署”“实机验收”独立记录。

## T1：冻结现状验收与收尾已知稳定性缺口

**Files**
- Read/targeted modify: `mac-midscene-runner.py`、`js/agent-workbench.js`、`js/agent-status.js`、`js/execution.js`。
- Tests: 复用 `tests/test_ios_case_reuse.py`、`tests/test_mac_ios_device_snapshot.py`、`tests/agent_device_cards_check.js`；按实际复现补充失败用例。
- Create: `docs/evidence/ui-simple-flow-p0.md`。

- [ ] 记录平台/Runner/CLI 版本、已连接手机、可用应用映射和步骤能力。核对 CODEX_STATE 已存在的首次触控不跳转、下拉轮询失焦、false 归因展示。
- [ ] 先复现并保留时间线、动作前后截图和 WDA/Runner 日志；有证据才改相应层，不默认加长 sleep 或对点击无限重试。
- [ ] 验证恢复后的首次导航、取消、锁屏/断连。业务中断保留失败；环境恢复不自动重放有副作用的步骤。
- [ ] Android 与当前明确连接的 iOS 设备分别完成三轮非破坏性导航；第二台未连接/未验证则标缺口，不写全部设备通过。
- [ ] 发布记录必须包含失败样本及根因未确认项，不能只收集最后三次成功。

**产出：** 可用设备/版本证据表、已复现问题清单、真实通过范围；后续不能超出该范围宣称跨端支持。

## T2：应用映射、设备能力与纯编译器

**Files**
- Modify: `task_server/services/business_line_service.py`、`task_server/services/job_service.py`、`task_server/services/runner_service.py`、`mac-midscene-runner.py`、`windows-midscene-runner.py`。
- Create: `task_server/services/ui_execution_contract.py`、`task_server/services/ui_case_compiler.py`。
- Tests: Create `tests/test_ui_execution_contract.py`、`tests/test_ui_case_compiler.py`；extend `tests/test_ios_case_reuse.py`。

**Interfaces**
- `resolve_ui_target(app_id, device_target, actor) -> TargetResolution`：返回明确平台、Runner、设备、包映射、能力和阻塞码；无匹配时不返回任意 Runner。
- `compile_ui_case(case_version, target_resolution) -> CompiledCase`：纯函数返回 YAML、source_hash、compiled_hash、step_map、unsupported_steps；不执行手机动作。
- `DeviceTarget = {runner_id, device_id, platform}`；`step_map` 把源 step_id 映射编译 flow 序号，包含 AI 补充的 wait/assert 子步骤。

- [ ] 先写映射正确、映射缺失、能力不足、旧 Runner、不匹配手机、伪造平台字段、跨权限应用和源 YAML 不变的失败测试。
- [ ] 应用目录添加平台映射；保留原 package 别名与应用权限关联。已有 Mac 本地映射作为迁移输入，由管理员核对后写平台，不向浏览器暴露 Runner 私密配置。
- [ ] 抽取当前 Mac 导航编译规则；首批仅启用已验证的四类动作。输入/滚动等逐项验证 CLI、参数和设备后开放；Android 返回键无 iOS 等效时明确阻塞。
- [ ] Runner 声明能力版本与动作支持；复用已有能力字段，旧 Runner 降级提示。所有新入口精确指定 runner_id/device_id。
- [ ] 新编译器覆盖纯函数测试后，用同用例版本在两端核对目标页与步骤映射，不把最新版文档中的动作直接全部加入白名单。

## T3：用户执行批次和不可变快照

**Files**
- Create: `task_server/services/ui_execution_store.py`、`task_server/services/ui_execution_service.py`、`tests/test_ui_execution_store.py`、`tests/test_ui_execution_service.py`。
- Modify: `task_server/services/job_service.py`、`task_server/services/agent_service.py`、`task_server/access_control.py`、`task_server/router.py`（仅薄路由接入）。

**Interfaces**
- `create_ui_batch(actor, request, idempotency_key) -> UiBatch`；身份从认证上下文读取。请求仅含 app_id、suite/case revisions、DeviceTarget、测试数据引用和通知选项。
- `append_case_attempt(batch_id, case_revision, target, compiled_case) -> BatchCaseAttempt`。
- `get_ui_batch(actor, batch_id)`、`list_ui_batches(actor, filters, cursor, limit)`：同一权限策略用于列表、明细和附件。
- 显式区分 `ui_batch_id`、原 `agent_run_id`、`job_id` 和 `sonic_result_id`；禁止替换旧 run_id 的多种含义。

- [ ] 先测试 A 创建共享基线/B 执行、A/B 同时执行同名 YAML、客户端伪造 tester、重复开始、异步丢失 actor、服务重启、无历史归属等场景。
- [ ] 建立版本化 SQLite 表：batches、case_attempts、snapshots、device_leases、delivery_outbox。索引覆盖 actor/time、app/time、batch/case/device/attempt、job_id、outbox status/due_time；不迁移整个旧任务库。
- [ ] 冻结选择范围、用例版本、编译内容、映射版本、设备和身份；新增字段采用明确命名，与现有审计事件关联，但授权不从审计日志推导。
- [ ] 批次创建与派发意图同事务保存。后台根据唯一的 dispatch_key 创建/查找现有 job；崩溃在两存储写入中间时重入可核对并补链，不重复执行。不得先把批次标 running 再等待实际领取。
- [ ] 先发布元数据路径开关，检查真实接单耗时，避免重复发生审计扫描导致数十秒任务未领取。

## T4：统一基线执行、设备租约与进度

**Files**
- Modify: `task_server/services/ui_execution_service.py`、`task_server/services/runner_service.py`、`task_server/services/sonic_service.py`、`task_server/services/agent_service.py`、`task_server/services/job_service.py`。
- Create: `tests/test_ui_batch_dispatch.py`、`tests/test_ui_batch_recovery.py`。

**Interfaces**
- `preflight_ui_batch(actor, request) -> {ready, blockers, compatibility, required_inputs}`。
- `dispatch_ui_batch(batch_id)`：调用既有 Job/Agent/Sonic 路径，禁止建立另一套 Runner 轮询队列。
- `record_ui_attempt_event(job_id, sequence, event)`：去重进度；终态与取消不可被迟到回包复活。
- `cancel_ui_batch(actor, batch_id)`、`rerun_ui_batch(actor, batch_id, scope) -> new_batch`。

- [ ] 先测两用户争用同手机、不同手机可并行、Sonic 远控占用、断线、失联租约、取消与结果竞态、服务重启、旧 Runner 拒绝能力等。
- [ ] 把 Runner 自有派发和 Sonic 套件包装为同一批次结果契约；基线保留现有 executor 类型和执行策略，UI 隐藏部署细节，不强制所有基线先同步 Sonic。
- [ ] 原子领取设备并二次核对就绪/应用版本；设备失联后阻塞，确认旧执行已停止才释放/重新排队。不能只凭过期时间认为可以重复下发。
- [ ] 对进入新批次的生成用例沿用现有静态/评分/冒烟门禁；原生 iOS 不支持 dry-run 时明示缺失，执行受控实机冒烟，不能填充一个假的 dry-run passed。
- [ ] 步骤使用稳定 step_id，wait/assert 的内部子动作折叠。没有步骤上报能力的 Runner 显示“仅用例级进度”，不按日志关键词猜出绿色对勾。
- [ ] 三轮现场验收覆盖导航、已验证输入/滑动、受控失败；失败和中断的证据同样保留。

## T5：本次报告、权限与真实导出

**Files**
- Create: `task_server/services/ui_batch_report_service.py`、`tests/test_ui_batch_report_service.py`、`tests/test_ui_batch_report_access.py`。
- Modify: `task_server/services/test_report_service.py`、`task_server/services/report_word_service.py`、`task_server/services/report_service.py`、`task_server/access_control.py`、`task_server/router.py`、`js/reports.js`。
- Inspect deployment: 搜索实际 Nginx 配置中的 `/reports` 静态服务及下载路径，列出本批资产需要受控访问的位置后再定点修改。

**Interfaces**
- `build_ui_batch_report(actor, batch_id) -> ReportSnapshot`：仅使用该批的已冻结 case/attempt/证据；持久化 report_revision 和 manifest。
- `download_ui_batch_report(actor, report_id, format)`：复用真实 DOCX/HTML/Markdown 渲染，附件与页面一并校验应用和批次权限。

- [ ] 先用同名文件、同一用例不同版本、两用户交错结果、部分未执行和首次失败后重跑数据，证明不会串入最近历史成功结果。
- [ ] 从批次填充执行者、时间、设备、系统/应用版本、范围和结论；累积 Bug 数量与本批结果分开，保留原失败/重跑明细。
- [ ] 批次终态后异步生成，报告失败支持补生成，不触发执行重跑；未完成报告在 UI 明示。
- [ ] A/B 越权测试覆盖直接 ID、URL、HTML 静态路径、截图、下载；修改服务器反代配置时附回滚步骤。旧历史不补造归属，沿用明确历史访问策略。
- [ ] 真实下载 Word 并用已安装办公软件打开；核对正文、表格、末页、中文文件名、MIME、扩展名与无重复“测试报告”。复制摘要来自同一报告快照。

## T6：批次飞书通知与补发

**Files**
- Create: `task_server/services/ui_batch_notification_service.py`、`tests/test_ui_batch_notification.py`。
- Modify: `task_server/services/feishu_service.py`、`task_server/services/notification_presentation.py`、`task_server/services/sonic_service.py`、`task_server/services/ui_execution_store.py`。
- Read only/reuse patterns: `task_server/api_testing/services/notification_service.py`，不直接扩展 API 测试的数据表来承载 UI 批次。

**Interfaces**
- `enqueue_batch_delivery(batch_id, report_revision, channel_id)`；唯一 delivery key。
- `process_due_batch_deliveries(limit)`；队列领取、发送、结果存储分离，不持锁做网络请求。
- `retry_batch_delivery(actor, delivery_id)`；校验批次访问和通知权限。

- [ ] 先测无渠道、错误渠道、403/超时、远端成功回包丢失、重复终态、Sonic 回调并发和人工补发。
- [ ] 复用应用渠道和 webhook 发送器；不在新批次同时启用子任务/Sonic 的重复汇总发送。UI 批次拥有最终报告通知，旧独立 Sonic 套件保留原行为。
- [ ] 卡片展示执行者/批次/版本/设备/分项结果，链接到带权限的报告落地页；登录后正确返回原批次，下载文件名正常。
- [ ] 初始有界重试 30 秒/2 分钟/10 分钟，耗尽显示失败；不确定远端是否送达时记录 unknown 和核对提示，避免宣称严格一次送达。
- [ ] 在明确授权的测试渠道验收消息、飞书内置浏览器打开、下载、复制。测试通知也必须在消息中标识测试批次。
- [ ] 原生 DOCX 附件列为可选独立适配：企业机器人权限/群目标准备完成后实施；核心卡片链路不被该凭据依赖阻塞。

## T7：普通用户页面与少操作闭环

**Files**
- Create: `js/ui-test-home.js`、`js/ui-batch-view.js`、`tests/ui_simple_workflow_check.js`。
- Modify: `task-manager.html`、`js/app.js`、`js/execution.js`、`js/reports.js`、`js/cases.js`、现有对应样式文件（实施前从 HTML 引用确认）。

- [ ] 先建页面交互检查：已有基线详情只需选手机/开始；首次使用应用/套件/设备/开始；登录用户切换不会复用他人的隐私状态。
- [ ] 导航收敛为开始测试、用例库、我的报告；设备/配置/高级 YAML 收纳。最近选择按用户存储，权限或设备状态变化时重新校验。
- [ ] 按应用/模块搜索筛选，全部每组最近 3 条，展开分页；结果“我执行的”默认，不把作者当执行者。
- [ ] 页面更新复用现有可见页轮询和事件，保持焦点/展开/滚动；执行中置顶，灰蓝绿红节点加文字图标，准备阶段有实际进度和超时提示。
- [ ] 真实 Chrome 在 1440、1024、390 CSS px 下验证，保持用户正常浏览器缩放；按钮不截断，图片完整适配，列表不大面积留白。
- [ ] P1 整体验收：A/B 独立登录、明确 Android/iOS 各三轮、自动生成报告、飞书结果核对；包含取消与失败，逐条登记证据。

## T8：统一中文步骤与自然语言生成

**Files**
- Create: `task_server/services/ui_case_draft_service.py`、`ai_skills/prompts/ui_step_normalizer.v1.md`、`ai_skills/schemas/ui_step_normalizer.schema.json`、`js/ui-case-draft.js`、`tests/test_ui_case_draft_service.py`、`tests/ui_case_draft_check.js`。
- Modify: `task_server/services/ai_skill_service.py`、`task_server/services/agent_service.py`、`task_server/services/case_service.py`、`task_server/services/yaml_service.py`、`task_server/services/yaml_execution_plan.py`。

**Interfaces**
- `normalize_ui_sources(actor, app_id, sources) -> CaseDraft`。
- `CaseDraft = {draft_id, revision, app_id, title, preconditions, steps, data_refs, questions, source_refs}`；每步有稳定 step_id、操作/预期/来源和 review_state。
- `validate_ui_draft(actor, draft_id, revision, target) -> GateResult`；`try_ui_draft(...) -> ui_batch_id` 走 T3/T4，不另外直调 Runner。
- `save_ui_case_version(actor, draft_id, revision) -> CaseVersion`；草稿保存与发布共享基线权限分开。

- [ ] 先测自然语言步骤、缺预期、歧义目标、跨应用内容、模型结构错误/超时、来源中含越权指令、超量生成和平台不支持动作。
- [ ] 复用现有 AI Gateway/Top3 基线规划，不新增一套模型配置。规则解析确定字段，AI 生成建议；平台 Schema/白名单/评分校验。
- [ ] 步骤编辑支持删改重排、增加检查点、恢复上一版；集中显示关键问题；无法确定的预期标为建议，确认前不发布为已验证基线。
- [ ] 中文步骤为主要编辑源，高级 YAML 改动解析为新候选 revision；解析不了保留原文且阻塞，不双向静默覆盖。
- [ ] 分别用导航、输入搜索、滚动断言任务做真实生成与回放；动作未在目标平台验证前只保存草稿。模型成功返回 JSON 不算验收。

## T9：平台录制汇入统一步骤，补齐平台边界

**Files**
- Modify: `task_server/services/device_recording_service.py`、`task_server/services/device_recording_yaml_service.py`、`js/device-recorder.js`、`task_server/services/ui_case_draft_service.py`。
- Extend: `tests/test_device_recording_yaml_service.py`；Create `tests/test_recording_to_ui_draft.py`。

- [ ] 先测旧录制导入、动作截图过期、连续同名控件、弹窗确认、未跳转、红点重识别、删除多余步骤、取消/结束后不再入库。
- [ ] 真实点击前后截图与动作 ID 映射到 CaseStep；旧坐标作为识别证据，不默认生成坐标脚本。检测页面结果，不把“动作已发送”当导航成功。
- [ ] 由 T2 按目标平台编译，去掉新路径中固定 android 输出；安卓系统键无法跨端时集中提示适配。
- [ ] 独立核验 iOS 远控动作采集、截图时序、结束/取消和重新连接；具备可靠能力才启用 iOS 实时录制，否则页面提供上传视频替代。
- [ ] Chrome 完整录制 → 编辑 → 生成 → 保存 → Runner 三轮回放；保留每一步手机页面证据与最终断言。

## T10：操作视频异步解析

**Files**
- Create: `task_server/services/ui_video_source_service.py`、`task_server/workers/ui_video_worker.py`、`tests/test_ui_video_source_service.py`、`tests/test_ui_video_to_case.py`。
- Modify: T8 草稿服务与编辑页、`task_server/router.py` 薄路由、部署依赖说明；AI 视频步骤提示词在 T8 normalizer 契约内扩展视觉输入。

**Interfaces**
- `create_video_source(actor, upload) -> {source_id, status}`；来源资产按用户/应用授权，文件名不决定访问路径。
- `analyze_video_source(source_id, cancel_token) -> {segments, candidate_steps, uncertainties}`。
- segment 含 source_id、start_ms、end_ms、frame_refs；候选步骤必须引用存在的 segment，不接受模型虚构帧编号。

- [ ] 先覆盖 MP4/MOV、格式伪装、截断文件、超尺寸/时长、旋转、黑屏/静止、快速转场、无触点、多次重复操作、缺结尾结果及取消。
- [ ] 校验后在后台受限解码，默认 200 MB/10 分钟、并发 1、候选帧 60；CPU/内存/执行时长有界，不在主 HTTP 线程运行解码。
- [ ] 去重等待帧、保留页面转场和弹窗证据，建立时间轴；超过容量提示分段或选片段，不能截取前面就假装整个视频完成。
- [ ] 不确定步骤进入集中确认，明确区分观察/推断/用户补充；用 T8 校验和 T3/T4 试跑，最终用例可追到原视频片段。
- [ ] 授权、取消、失败重试、过期临时帧清理、草稿保留和服务器重启恢复验收；清理不能删除已被正式用例引用的证据。
- [ ] 至少三类真实素材各完成识别和实机回放；含无触点录屏和弹窗案例，不只用顺滑演示视频。

## T11：已验证闭环上的便利功能

- [ ] 先做收藏/最近套件、最近授权设备预填、复制摘要和仅重跑失败项；不新增配置步骤。
- [ ] 公共登录/导航步骤作为版本化资产引用，执行时冻结依赖版本；凭据引用私密配置，禁止从他人录屏抄密码。
- [ ] 参数化查询词/账号角色/测试记录，平台维护测试数据契约，避免每次视频生成硬编码数据。
- [ ] Android/iOS 多设备对比作为展开项，各端独立结论；共用数据有冲突时串行执行或明确阻塞。
- [ ] 一句话“跑智小白3D的首页冒烟”推荐精确存在的套件，显示范围/设备再开始；不默认让 AI 扩大为全库回归。
- [ ] 原有定时 UI 入口逐个接入同一批次与报告契约，记录任务所有者和系统执行主体；不在此改造 API 定时任务。

## 每批检查、部署与回滚

新增 Python 测试沿用所在模块实际测试风格，用 pytest 收集函数式测试，避免 unittest discover 显示 0 tests 被误当通过：

```bash
python3 -m pytest tests/test_ui_*.py -q
python3 -m pytest tests/test_ios_case_reuse.py -q
python3 -m pytest tests/test_device_recording_yaml_service.py -q
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py
python3 tests/backend_static_checks.py
git diff --check
```

涉及前端增加 `python3 tests/frontend_static_checks.py` 与对应 Node 专项、Chrome 真实页面；涉及 Gateway 增加 `python3 tests/ai_gateway_static_checks.py`；涉及 Runner 增加 `python3 -m py_compile mac-midscene-runner.py windows-midscene-runner.py` 及双方版本/启动说明。只有本批实际触及的范围扩展测试，避免重复跑无关长测试。

新批次存储做增量建表/索引，先备份并验证迁移；派发不可逆副作用发生后只能停止/取消，不能因回滚重复提交。服务端部署沿用：

```bash
cd /opt/midscene-task-platform-src
bash deploy/update-main-server.sh
```

只有对应实现批次通过检查后才执行该命令；部署完成仍需线上验收。涉及 Runner 变更时另更新对应 Windows/Mac 脚本，在设备无执行任务时重启并核对心跳能力；平台发布不能替代 Runner 更新。

## 性能与最终验收证据模板

每批记录基线版本/改动版本、数据量、硬件、测试时段和并发。冷热分开、至少三组交替测试任务列表/保存/心跳/接单/报告查询；检查新增视频任务与两个设备执行同时存在时的尾延迟、错误率与 CPU/内存。

初始发布告警线为同场景 p95 增幅同时超过 10% 与 50 ms，或新增超时/错误/持续内存增长；触发必须解释并处理，不能靠扩大容忍值放行。小于阈值也需检查设备操作卡顿和接单延迟。阈值是拟定验收标准，不是已测性能承诺。

证据登记字段：批次/用户/设备/应用版本、用例 revision 与 hash、job/attempt、开始结束、每步截图与断言、报告真实打开结果、飞书投递结果、重跑/取消轨迹、遗留问题。发布至少覆盖两账号、Android/iOS、三轮真实主流程，以及并发/断连/失败/取消，不以静态通过或“已发送请求”代替完成。

## 计划自检

- [x] 覆盖选机执行、跨端复用、他人独立报告、飞书下载、文字/录制/视频生成及少操作页面。
- [x] 已区分现有能力、新增能力、尚未验证能力，明确 iOS 白名单与实时录制边界。
- [x] 同名报告混用、权限、步骤与证据、重复派发、设备占用、通知不确定性、性能和回滚纳入任务。
- [x] 不要求普通执行者配置模型/Runner，不强制所有基线重写，不增加两套业务用例。
- [x] 分批可交付，P1 完成即可使用；P2/P3 不阻塞选机执行和本人报告。
- [ ] 已批准并开始分批实施；各任务实现、部署与真实验收见进度证据文档，整体验收尚未完成。
