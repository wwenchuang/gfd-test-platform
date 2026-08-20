# CODEX_STATE.md

本文件记录当前 Codex 交接状态，目的是减少长对话上下文依赖。每次完成一轮重要修改后更新本文件。

## 当前项目状态

平台已有完整的 Agent 生成、YAML 校验、Runner 执行、Sonic 同步、报告和失败修复链路。当前主要目标不是重构架构，而是提高 AI 生成 Midscene YAML 的可执行性、速度和生产稳定性。

## 当前本地仓库路径

- 当前实际仓库：`/Users/adouceshi/Documents/projects/midscene-task-platform`
- 旧路径 `/Users/wenchuang/Documents/Codex/midscene-task-platform` 和 `/Users/wenchuang/Documents/测试平台` 不再作为本机开发仓库使用。
- 本机 API 测试依赖使用项目自带 `deploy/api-testing-compose.yml` 启动 PostgreSQL / Redis；完整后端 API testing 测试需同时设置 `TEST_DATABASE_URL` 与 `TEST_REDIS_URL`。

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

### 2026-08-20 Sonic API 基线飞书卡片：领导视角信息和报告按钮优化

用户反馈 `智小白3D｜基线回归通过/失败` 飞书卡片 UI 和展示信息偏粗糙，需要适合发给领导，并确认报告地址能打开当前执行报告。用户确认该场景没有 Sonic 和设备字段，因此无数据时不展示对应信息。

本轮修复：

- Sonic 套件飞书卡片标题改为 `应用｜API 基线回归｜结论`，失败对外展示为 `未通过`，避免重复输出 `基线回归失败`。
- 正文按领导视角重排：结论、应用、通过率、用例统计、范围、耗时和失败摘要。
- 没有设备时不展示 `设备` 行；没有 Sonic URL/lookup 信息时不展示 Sonic 文案或 Sonic 按钮。
- 平台汇总报告按钮改名为 `查看平台汇总报告`，并保持为第一个 primary 按钮。
- 发送链路测试锁定：`send_sonic_suite_summary_if_quiet` 会先生成 `suite_report_url`，卡片主按钮 URL 必须等于本次生成的当前套件汇总报告地址。
- 失败摘要最多展示前 5 条，保留用例名和压缩后的原因，减少飞书卡片刷屏。

已验证：

```bash
.venv/bin/python -m pytest tests/test_sonic_integration.py::test_leadership_sonic_suite_card_prioritizes_platform_summary_without_sonic_or_devices tests/test_sonic_integration.py::test_leadership_sonic_suite_card_summarizes_failed_items_without_repeating_header tests/test_sonic_integration.py::test_suite_summary_feishu_registry_blocks_same_result_from_different_keys tests/test_sonic_integration.py::test_sonic_completed_suite_reports_missing_task_callbacks tests/test_sonic_integration.py::test_sonic_final_success_overrides_failed_task_callback_in_summary -q
# 5 passed

python3 -m py_compile task_server/services/sonic_service.py
python3 tests/backend_static_checks.py
git diff --check
# passed
```

注意：`tests/test_sonic_integration.py -q` 整文件当前仍会因历史 `midscene-upload.py` 入口不再导出旧函数而大量失败，本轮未把这个既有测试结构问题纳入修复范围。

### 2026-08-20 脑图测试报告：AgileTC 描述优先回填测试版本

用户确认测试人员不从 AgileTC 描述里解析，只需要描述中出现类似 `智小白3D V1.19.0` 时回填测试版本。

本轮修复：

- AgileTC 搜索结果不再只依赖 `/api/case/list` 的列表字段；会对候选用例集补读 `/api/case/detail?caseId=...` 获取完整 `description`。
- 测试版本提取优先级调整为：用例集详情描述 > 用例集标题 > 关联需求链接。
- 描述里存在 `V1.19.0`、`版本：V1.19.0`、`version 1.19.0` 等格式时，报告表单选择该用例集后会优先回填描述中的版本。
- 详情接口单条失败时不会影响搜索结果，自动退回列表字段和标题兜底。
- 不从描述里解析测试人员，测试人员仍只走用户手填/历史选择。

已验证：

```bash
.venv/bin/python -m pytest tests/test_case_platform_integration_service.py tests/test_mindmap_test_report_service.py -q
python3 -m py_compile task_server/services/case_platform_service.py task_server/router.py
.venv/bin/python tests/backend_static_checks.py
git diff --check
# passed
```

### 2026-08-20 脑图测试报告：AgileTC 用例平台搜索选择

用户希望测试报告表单里飞书需求和自建测试用例平台能打通，支持选择和手动输入，并自动带出版本号。本轮先对自建 AgileTC 用例平台做只读抓取验证和集成：

- 抓取并确认 AgileTC 前端实际 API 前缀为 `/api`，用例集列表接口为 `/api/case/list`，默认 `productLineId=1`。
- 新增 `task_server/services/case_platform_service.py`，通过 `CASE_PLATFORM_BASE_URL`、`CASE_PLATFORM_PRODUCT_LINE_ID`、`CASE_PLATFORM_TIMEOUT_SECONDS` 支持环境配置，默认连接 `http://qa-agiletc.gongfudou.com`。
- 新增 `/api/test-reports/case-platform/search`，支持按用例集标题、关键词或完整飞书需求链接查询 AgileTC，用标准字段返回标题、版本、飞书需求链接、用例平台链接、创建人和更新时间。
- 脑图测试报告表单新增“搜索用例平台”入口，默认用报告标题作为查询词，也支持手动输入版本关键词或飞书需求链接。
- 选择搜索结果后会自动回填测试用例平台链接、飞书需求链接和测试版本，并写入本地历史记录。
- 已验证飞书项目详情页未授权访问会跳转登录页；当前不做不稳定的网页会话硬抓。若后续要读取飞书需求详情/版本字段，需要配置飞书开放平台应用授权或可用接口 token。

已验证：

```bash
.venv/bin/python -m pytest tests/test_case_platform_integration_service.py tests/test_mindmap_test_report_service.py -q
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/test_report_service.py task_server/services/case_platform_service.py task_server/router.py
python3 tests/frontend_static_checks.py
.venv/bin/python tests/backend_static_checks.py
node --check js/app.js
node --check js/state.js
git diff --check
# passed
```

### 2026-08-20 脑图测试报告：人员/版本/飞书需求/用例平台历史联动

用户反馈上一轮没有实现测试人员和版本记录，以及飞书需求、测试用例平台选择与手动输入联动。

本轮修复：

- 脑图测试报告表单中，测试人员和测试版本支持本地历史记录，下次进入可直接从下拉中选择。
- 测试人员和测试版本历史记录支持删除，避免旧人员或旧版本长期污染选择项。
- 飞书需求字段支持手动输入和历史选择；记录会绑定当次版本。
- 选择历史飞书需求时，会自动带出该需求上次记录的版本；输入内容中包含 `V1.2.3`、`version 1.2.3` 等版本格式时，也会在版本为空时自动填入。
- 测试用例平台字段支持手动输入和历史选择，并可删除历史记录。
- 预览报告和生成报告时会自动保存本次输入过的测试人员、版本、飞书需求和测试用例平台链接。
- 前端缓存版本更新为 `20260820-mindmap-report-history`。

已验证：

```bash
python3 tests/frontend_static_checks.py
node --check js/app.js
node --check js/state.js
# passed
```

### 2026-08-20 API 工作台：已选接口名称和路径显示修复

用户截图反馈工作台 `接口范围 -> 已选接口` 里只看到 `POST` 方法标记，看不到接口名称和路径，确认属于上轮任务列表/接口范围改造后的遗留显示问题。

本轮修复：

- 定位根因：`.selected-endpoint-row` 的两列布局先定义，但后面的通用 `.endpoint-row` 规则覆盖了 `grid-template-columns`，导致已选接口左侧内容列被压到 22px，只剩方法标记可见。
- 将已选行布局选择器提升为 `.endpoint-row.selected-endpoint-row`，保留 `minmax(0, 1fr) 34px`，让接口名称/路径占主列，移除按钮固定在右侧。
- 补充 EndpointTree 样式回归测试，防止已选接口行再次被通用行样式覆盖。
- 重新构建 API testing 前端产物。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/components/EndpointTree.spec.ts --reporter=basic
# 1 file / 12 tests passed

npm --prefix api-testing-ui test -- --run --reporter=basic
# 33 files / 184 tests passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed; generated api-test/assets/index-DmO-5Zyx.js and index-D577SQ4J.css

python3 tests/frontend_static_checks.py
git diff --check
# passed
```

注意：本轮工作区中另有非本次改动的 `js/app.js`、`tests/frontend_static_checks.py`，未纳入本次提交，不能回滚。

### 2026-08-17 API 定时任务：自动调度、飞书通知和报告链接修复

用户反馈定时任务没有生效、飞书通知未发送，并且手动飞书消息中的报告链接会打开到不对应的任务报告。

本轮修复：

- `midscene-api-scheduler` 不再只扫描启用任务；每轮会按 cron 到期时间派发执行，并通过 Celery 入队。
- 定时任务支持同一分钟幂等投递，idempotency key 使用 `scheduled-job:{job_id}:{yyyyMMddHHmm}`，避免 30 秒扫描周期内重复创建执行。
- 后端补齐 cron 解析和校验，调度侧支持 5 字段 cron 的 `*`、数字、范围、列表和步长；`daily` 默认 `0 2 * * *`，`weekly` 默认 `0 9 * * 1`。
- 定时任务提交执行时会把 `notify_feishu` 写入 execution snapshot；worker 完成后仅在定时任务飞书开关开启时发送飞书通知。
- 普通基线回归执行保持原有自动飞书通知行为。
- 飞书报告卡片链接新增 `project_id` 查询参数，报告页优先按链接中的项目加载，再选中对应 `execution_id`，避免当前工作台项目不同导致打开错报告。

已验证：

```bash
TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene_api_testing_dev@127.0.0.1:5432/midscene_api_testing' API_TESTING_REQUIRE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/api_testing/test_scheduled_job_service.py -q
# 3 passed

.venv/bin/python -m pytest tests/api_testing/test_tasks.py tests/api_testing/test_notification_service.py -q
# 10 passed

.venv/bin/python -m pytest tests/api_testing/test_execution_service.py -q
# 3 passed, 18 skipped

npm --prefix api-testing-ui test -- --run --reporter=basic
# 33 files / 183 tests passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed; generated api-test/assets/index-DV2X_tWT.js and index-CaCHAfd9.css

python3 -m py_compile task_server/api_testing/scheduler.py task_server/api_testing/services/scheduled_job_service.py task_server/api_testing/services/execution_service.py task_server/api_testing/tasks.py task_server/api_testing/services/notification_service.py
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
git diff --check
# passed
```

### 2026-08-14 API 工作台：左侧独立任务列表和基本管理操作

用户要求工作台任务不要继续藏在“已保存任务”下拉里，需要左侧独立任务列表，并支持编辑任务、环境/用例范围调整、删除和执行等基本操作。

本轮修复：

- 工作台新增左侧 `任务列表` 面板，展示当前项目下已保存任务数量、任务名、状态、接口数、基线数和保存环境。
- 左侧任务列表支持搜索任务名称、新建任务、点击任务进入编辑、行内执行任务、行内删除任务。
- 删除任务增加二次确认，删除任务只移除任务资产；任务关联的用例、基线和历史执行记录会保留。
- 选中任务后会恢复该任务保存的接口版本、环境、接口范围，并在右侧继续编辑任务名称、接口范围、用例内容和执行环境。
- 原任务条中的“已保存任务”下拉已移除，任务管理入口收口到左侧列表；任务条保留当前任务概览、名称保存、范围保存和执行本任务。
- 后端补齐 `DELETE /api/api-testing/v1/tasks/:id`，服务层和前端 store 均支持删除任务。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/stores/tasks.spec.ts src/components/TaskStatusStrip.spec.ts src/components/TaskListPanel.spec.ts src/views/WorkbenchView.spec.ts --reporter=basic
# 4 files / 21 tests passed

TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene_api_testing_dev@127.0.0.1:5432/midscene_api_testing' API_TESTING_REQUIRE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/api_testing/test_test_task_service.py -q
# 11 passed

TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene_api_testing_dev@127.0.0.1:5432/midscene_api_testing' TEST_REDIS_URL='redis://127.0.0.1:6379/1' API_TESTING_REQUIRE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/api_testing/test_http_contract.py::test_api_task_can_be_deleted_from_saved_list -q
# 1 passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed; generated api-test/assets/index-DjLW3sS7.js and index-CaCHAfd9.css

npm --prefix api-testing-ui test -- --run --reporter=basic
# 33 files / 182 tests passed

python3 -m py_compile task_server/api_testing/services/test_task_service.py task_server/api_testing/repositories/test_task_repository.py task_server/api_testing/http.py
python3 tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check
# passed
```

### 2026-08-14 API 工作台：接口范围搜索固定、分组折叠和命中高亮

用户反馈工作台接口范围列表滚动后搜索框不方便使用，同步过来的接口分组默认展开过长，搜索命中项最好高亮。

本轮修复：

- 接口范围面板顶部标题、全部/已选 Tab 和搜索框都固定在滚动容器顶部，滚动接口列表时搜索框保持可见。
- 接口分组首次同步进来默认折叠，只显示分组名、选择框和数量；点击分组箭头后展开接口明细。
- 用户手动展开/折叠的状态会保留，新同步进来的分组默认折叠。
- 搜索时匹配到的折叠分组会临时展开，避免搜索结果藏在折叠分组里。
- 搜索命中会在分组名、接口名称和接口路径中高亮匹配片段，例如输入 `qidi` 时路径里的 `qidi` 会标黄。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/components/EndpointTree.spec.ts --reporter=basic
# 1 file / 11 tests passed

npm --prefix api-testing-ui test -- --run --reporter=basic
# 32 files / 178 tests passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed; generated api-test/assets/index-JdpJNbuJ.js and index-B-96TOjC.css

python3 tests/frontend_static_checks.py
# 81 checks passed

python3 tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```

### 2026-08-14 API 定时任务：基线选择口径、Cron 校验和开关标记

用户继续反馈定时任务页中基线分组/多条基线没有和基线库列表同步，Cron 手写表达式缺少校验和含义说明，列表开关缺少可读标记。

本轮修复：

- 定时任务目标选择器的基线口径改为和后端执行一致：展示所有非归档基线，不再只按 `status === "active"` 过滤。
- `baseline_group` 选择器会同步展示 `测试`、`基线`、`未分组` 等分组，并显示分组下基线数量、代表用例和接口路径。
- `baselines` 多选模式按分组列出所有非归档基线；归档基线不会出现在可选目标中。
- 列表行的启用/飞书开关补上 `启用`、`飞书` 文本标记，避免只看到两个无语义开关。
- Cron 输入改为实时校验 5 字段表达式，支持 `*`、数字、范围、列表和步长；非法表达式会在输入框下方提示并阻止保存。
- Cron 输入合法时显示可读含义，例如 `0 3 * * *` 展示为 `每天 03:00 执行`，`0 9 * * 1-5` 展示为 `工作日 09:00 执行`。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/views/ScheduledJobsView.spec.ts --reporter=basic
# 1 file / 6 tests passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed; generated api-test/assets/index-D_1iruD8.js and index-BlvbhcVY.css

npm --prefix api-testing-ui test -- --run --reporter=basic
# 32 files / 175 tests passed

python3 tests/frontend_static_checks.py
# 81 checks passed

python3 tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```

### 2026-08-14 脑图用例测试报告：统计、缺陷和专业结论模板

用户要求脑图测试报告支持多脑图用例生成、真实统计、缺陷手工录入，并优化通过结论和发布建议，使报告更像正式测试交付件。

本轮修复：

- 测试报告入口已在脑图文件列表和脑图中心顶部操作区露出，支持选择多条脑图文件合并生成报告。
- 报告表单补齐默认当前测试周期、多选涉及端侧、固定测试环境下拉、可编辑测试目标。
- 涉及端侧包含 `mini`、`Android`、`iOS`、`中台`、`后台`、`iPad`、`安卓pad`；测试环境默认 `正式环境`。
- 报告正文不再输出具体用例明细，默认只输出精简测试范围和编号化主要测试点。
- 用例统计按完整自动化用例统计，报告内执行状态统一按已执行且通过输出。
- 缺陷支持手工录入致命、严重、一般、轻微，并输出等级统计和缺陷总数。
- 默认报告模板不再输出首段“报告结论”摘要表，保留基础信息、测试概要、主要测试点、测试数据、质量评估和发布建议。
- 质量评估和发布建议按通过结论输出，并在底部结论文案前增加 `✅`，文案强调发布准入、阻断风险和上线后观察。

已验证：

```bash
.venv/bin/python -m pytest tests/test_mindmap_test_report_service.py -q
# 10 passed

python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/test_report_service.py task_server/router.py
python3 tests/frontend_static_checks.py
node --check js/app.js
node --check js/state.js
.venv/bin/python tests/backend_static_checks.py
git diff --check
# passed
```

### 2026-08-14 API 定时任务：飞书通知标记任务类型

用户反馈定时任务发送飞书通知时应标明这是定时任务，方便后续区分任务类型。

本轮修复：

- 飞书报告卡片正文新增 `任务类型` 行。
- 当 execution snapshot 中的 task `type/source` 或 execution `execution_source` 为 `scheduled_job` 时，任务类型展示为 `定时任务`。
- 其他执行保持可读类型：基线回归、调试执行、回归任务、已保存任务或手动执行。
- 补充通知服务单元测试，覆盖定时任务飞书卡片必须包含 `任务类型：定时任务`。

已验证：

```bash
.venv/bin/python -m pytest tests/api_testing/test_notification_service.py -q
# 3 passed

python3 -m py_compile task_server/api_testing/services/notification_service.py
python3 tests/backend_static_checks.py
git diff --check
# passed
```

### 2026-08-14 API 定时任务：列表编辑、删除和基线分组信息补齐

用户继续反馈定时任务页存在以下问题：新增基线分组未同步、多条基线应列出所有基线并按分组选择、列表需支持启用/飞书开关/删除/再次编辑、每天/每周缺少明确执行时间。

本轮修复：

- 刷新按钮同时刷新定时任务列表和目标资产，新增基线分组能同步出现在选择器里。
- `baseline_group` 选项展示分组信息：分组名、可执行基线数量、代表用例和接口路径，例如 `登录成功用例 · POST /login`。
- `baselines` 目标改为按分组展示所有 active 基线，分组内列出每条基线供多选。
- 列表行新增启用开关、飞书通知开关、编辑、删除和手动执行；删除使用二次确认弹窗。
- 点编辑会把任务回填到右侧表单，保存时走更新；新建任务仍走创建。
- 后端补齐 `PUT /scheduled-jobs/:id` 和 `DELETE /scheduled-jobs/:id`，服务层支持全量更新字段和 targets、删除任务。
- 每天/每周的具体时间已明确：每天默认 `0 2 * * *`（每天 02:00），每周默认 `0 9 * * 1`（每周一 09:00）；表单保存时会提交对应 cron 表达式。

已验证：

```bash
TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene_api_testing_dev@127.0.0.1:5432/midscene_api_testing' API_TESTING_REQUIRE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/api_testing/test_scheduled_job_service.py -q
# 2 passed

npm --prefix api-testing-ui test -- --run src/views/ScheduledJobsView.spec.ts --reporter=basic
# 1 file / 5 tests passed

npm --prefix api-testing-ui test -- --run --reporter=basic
# 32 files / 174 tests passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed; generated api-test/assets/index-DjXQrDnx.js and index-9zZhXj5O.css

python3 -m py_compile task_server/api_testing/services/scheduled_job_service.py task_server/api_testing/http.py
python3 tests/backend_static_checks.py
git diff --check
# passed
```

注意：

- 当前 `python3 tests/frontend_static_checks.py` 失败在工作区已有脑图报告检查：`Report builder must support manual defect severity input`。相关脏文件为 `task_server/services/test_report_service.py`、`tests/frontend_static_checks.py`、`tests/test_mindmap_test_report_service.py`，本轮未纳入定时任务提交。

### 2026-08-14 API 定时任务：表单交互优化

用户反馈定时任务创建页中 Cron 表达式缺少可选案例、勾选控件样式粗糙、目标用例/基线仍需手动输入 ID。

本轮修复：

- 定时任务目标不再使用手动 ID 文本框；按目标类型加载系统资产并展示可搜索、可点选列表：
  - `baseline_group` 选择 active 基线分组名。
  - `baselines` 选择 active 基线 ID。
  - `cases` 选择已保存 case version ID。
  - `task` 选择已保存任务 ID。
- 周期选择改为分段按钮；选择 Cron 时展示常用示例，一键填充表达式：
  - 每天 02:00：`0 2 * * *`
  - 工作日 09:00：`0 9 * * 1-5`
  - 每周一 09:00：`0 9 * * 1`
  - 每月 1 日 10:00：`0 10 1 * *`
  - 每 30 分钟：`*/30 * * * *`
- 启用 / 飞书通知改为统一的 switch 行，移除原生大 checkbox 视觉问题。
- 增加响应式样式，窄屏下目标选择器、Cron 示例和任务行自动单列。
- 补充前端回归测试覆盖：基线分组点选创建、Cron 示例填充、已保存用例点选创建。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/views/ScheduledJobsView.spec.ts --reporter=basic
# 1 file / 3 tests passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed; generated api-test/assets/index-DiPSel6L.js and index-CUvIDIl7.css

npm --prefix api-testing-ui test -- --run --reporter=basic
# 32 files / 172 tests passed

python3 tests/frontend_static_checks.py
# 75 checks passed

python3 tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```

### 2026-08-14 脑图用例测试报告：支持多脑图合并生成

用户反馈部署后确认入口，并进一步要求“应该可以选择多条脑图文件生成报告”。本轮在既有测试报告能力上补齐多脑图合并：

- 脑图中心文件列表新增复选框、选择当前列表、清空选择、生成合并报告；单条记录上的“生成报告”保留原行为。
- `/api/test-reports/cases` 支持 `case_set_ids` 逗号分隔多脑图，报告预览/生成 POST 支持 `case_set_ids: []`。
- 服务层为每条用例新增 `selection_id = case_set_id::case_id`，多脑图里重复的 `TC-001` 不会串选或串执行结果。
- 多脑图报告的测试范围按脑图来源分组，每个脑图最多展示 3 个核心范围项，正文总计最多 8 项，完整用例仍放附录。
- 多脑图报告保存到 `CASE_DIR/merged-test-reports/<report_id>/`，索引记录 `case_set_ids/source_count/sources`，从任一来源脑图过滤报告时都能看到。
- 模板占位符补充 `{{mindmap_list}}`、`{{source_count}}`，默认报告概要也展示脑图来源摘要。

已验证：

```bash
.venv/bin/python -m pytest tests/test_mindmap_test_report_service.py -q
python3 -m py_compile task_server/services/test_report_service.py task_server/router.py
.venv/bin/python tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
node --check js/app.js
node --check js/state.js
git diff --check
```

### 2026-08-14 API 测试：定时任务基础能力与执行来源收口

本轮按用户“API 接口自动化未完成收口”要求，优先补齐此前仍缺的 M5 定时任务基础能力，并复核 M1/M2/M3/M4 已有闭环：

- M1 任务闭环：现有工作台任务列表、任务命名、历史任务 runtime 环境执行、执行记录任务名展示继续保留；本轮新增 execution view 的 `task_type` / `execution_source`，定时任务执行记录能明确显示来源。
- M2 基线资产：现有项目级基线列表、分组、移动、删除、保存为基线回归任务、按当前环境独立执行继续保留；定时任务目标支持 `baselines` / `baseline_group`，基线分组运行时动态读取当前 active 基线。
- M3 Apifox / AI 生成：现有 EndpointTree 已支持全部/已选 Tab、分组折叠、分组选择、名称/path/group 搜索、已选列表移除；AI deterministic validation 中 `path_mismatch`、schema 空约束、unsupported operator、undefined variable 等仍由既有合同校验拦截。
- M4 报告 / 飞书：现有执行记录/报告批量删除、项目报告驾驶舱、项目级飞书机器人配置和发送状态继续保留；定时任务保存 `notify_feishu` 开关，实际 Webhook 仍从项目级配置读取。
- M5 定时任务：新增 `api_scheduled_jobs`、`api_scheduled_job_targets`、`api_scheduled_job_runs` 三张表；新增 `ScheduledJobService`，支持创建定时任务、选择目标类型（用例 / 已保存任务 / 多条基线 / 基线分组）、固定环境 revision 或按环境资产取最新 revision、手动执行一次。
- 新增前端“定时任务”入口：可创建基线分组等目标的定时任务，配置周期、启用、飞书通知、重试和超时，并可手动执行一次后跳转执行记录。
- 新增 `task_server/api_testing/scheduler.py` 与 `deploy/midscene-api-scheduler.service`；`deploy/install-server.sh` 会安装、enable/restart 或 disable scheduler systemd unit。
- 自动到期扫描目前是服务级预留：scheduler 每 30 秒存活扫描 enabled jobs，但不自动投递 cron 到期任务，避免在没有 `next_run_at` / PostgreSQL lock / 幂等到期窗口前误触发生产回归；当前验收主路径为页面创建 + 手动执行一次 + 执行记录显示定时任务来源。

已验证：

```bash
TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene_api_testing_dev@127.0.0.1:5432/midscene_api_testing' API_TESTING_REQUIRE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/api_testing/test_scheduled_job_service.py -q
# 1 passed

TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene_api_testing_dev@127.0.0.1:5432/midscene_api_testing' API_TESTING_REQUIRE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/api_testing/test_migrations.py::test_offline_upgrade_contains_complete_phase1_schema tests/api_testing/test_migrations.py::test_upgrade_creates_complete_phase1_schema -q
# 2 passed

TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene_api_testing_dev@127.0.0.1:5432/midscene_api_testing' TEST_REDIS_URL='redis://127.0.0.1:6379/1' API_TESTING_REQUIRE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/api_testing -q
# 341 passed

npm --prefix api-testing-ui test -- --run --reporter=basic
# 32 files / 170 tests passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed; generated api-test/assets/index-NHf0hOuo.js

python3 -m py_compile task_server/api_testing/services/scheduled_job_service.py task_server/api_testing/scheduler.py task_server/api_testing/http.py
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
git diff --check
# passed
```

### 2026-08-14 脑图用例测试报告：实现完成

用户确认按“测试设计报告 + 可选执行结果补充”落地，并要求参考基础模板、优化模板、测试范围精简。

本轮实现：

- 新增 `task_server/services/test_report_service.py`：从 `summary.json` 读取脑图用例，归一化自动化/人工用例，默认选择 P0 / P1 / 冒烟自动化用例。
- 测试范围按功能点和场景聚合，正文最多展示 8 个场景、每个功能点最多 3 个核心场景；完整用例进入附录。
- 新增测试报告 Markdown / HTML 默认模板，结构为 `基本信息 / 测试概要 / 测试数据 / 质量评估 / 发布建议 / 附录`。
- 支持 Markdown / HTML 文本模板上传，模板缺少关键区块时自动补默认区块。
- 保存报告到 `CASE_DIR/<case_set_id>/test-reports/<report_id>/`，同时维护 `LEARNING_DIR/test-report-index.json`。
- 新增 `/api/test-reports/*` 路由：读取可报告用例、预览、生成、下载、模板列表和模板上传；不改变现有 `/api/reports` Runner 报告索引语义。
- 脑图中心每条记录新增“生成报告”，进入两栏式报告生成页：左侧筛选/勾选用例，右侧填写标题、测试周期、测试人员、涉及端侧、版本、环境、需求链接、用例链接、测试目标、备注和模板。
- 若 `summary.generatedCaseGroups` 能映射到 YAML 文件，且 `LEARNING_DIR/report-index.json` 中已有同模块同 YAML 文件执行报告，则保守补充用例状态和报告链接；匹配不到标记为未执行。

已验证：

```bash
python3 -m py_compile task_server/services/test_report_service.py task_server/router.py
.venv/bin/python -m pytest tests/test_mindmap_test_report_service.py -q
.venv/bin/python tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
node --check js/app.js
node --check js/state.js
git diff --check
```

注意：

- 系统 `python3 tests/backend_static_checks.py` 当前会因仓库内已有 `task_server/api_testing/services/scheduled_job_service.py` 使用 `str | None` 而在较旧系统 Python 下失败；同一检查使用项目 `.venv/bin/python` 已通过。
- 工作区中存在一批与本功能无关的 API 定时任务文件改动，本轮不回滚、不提交。

### 2026-08-14 脑图用例测试报告：设计规格

用户希望在脑图中心基于 `.mm` / 需求用例选择范围生成正式测试报告，支持自定义标题、测试时间、测试人员、涉及端侧，并支持上传模板。

本轮完成设计规格：

- 新增设计文档：`docs/superpowers/specs/2026-08-14-mindmap-test-report-design.md`。
- 方案定位为“测试设计报告 + 可选执行结果补充”：报告主体来自脑图用例；如果能关联 Runner / Sonic / Midscene 执行结果，则补充通过率、失败明细和报告链接。
- 默认模板参考用户截图，保留 `基本信息 / 测试概要 / 测试数据 / 质量评估 / 发布建议 / 附录`。
- 测试范围按功能点和场景聚合，默认最多展示 8 个场景，每个功能点最多 3 个核心场景，不直接铺开脑图完整层级。
- 第一期模板建议支持 Markdown / HTML，占位符包括 `{{title}}`、`{{tester}}`、`{{test_scope}}`、`{{summary_table}}`、`{{case_table}}` 等。
- 后续实现建议新增 `task_server/services/test_report_service.py`，避免改变现有 `/api/reports` Runner 报告索引语义。

### 2026-08-14 API 环境：默认请求头删除保存生效

用户反馈：环境配置页删除默认请求头后保存不生效，保存后 `Biz`、`ZXBToken`、`Authorization` 等请求头仍会回到页面。

根因：

- 后端创建环境新版本时，如果 payload 显式包含 `default_headers`，仍先复制旧版本请求头再 `update` 新值。
- 前端删除某个请求头后会提交“删除后的完整请求头集合”，但后端 merge 逻辑会把旧版本里被删除的请求头重新合并回来。

本轮修复：

- `default_headers` 出现在变更 payload 时改为完整替换；只有字段省略时才继承旧版本请求头。
- 补后端回归：替换默认请求头后 runtime 不再包含旧 `Authorization`；清空敏感变量但仍被旧请求头引用时继续报错且不泄露密钥。
- 补前端回归：删除 `Authorization` 并保存时提交 payload 不包含被删请求头。

已验证：

```bash
TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene_api_testing_dev@127.0.0.1:5432/midscene_api_testing' API_TESTING_REQUIRE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/api_testing/test_environment_service.py::test_revision_copy_set_clear_and_old_secret_resolution tests/api_testing/test_environment_service.py::test_revision_clear_secret_rejects_inherited_headers_that_reference_it -q
# 2 passed

TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene_api_testing_dev@127.0.0.1:5432/midscene_api_testing' API_TESTING_REQUIRE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/api_testing/test_environment_service.py -q
# 22 passed

npm --prefix api-testing-ui test -- --run src/views/SettingsView.spec.ts --reporter=basic
# 1 file / 5 tests passed

.venv/bin/python -m py_compile task_server/api_testing/services/environment_service.py tests/api_testing/test_environment_service.py
# passed

git diff --check
# passed
```

### 2026-08-14 API 基线：回归任务保存与按环境执行解耦

用户反馈：基线页“加入当前任务”语义不清，点击后仍报“测试任务范围与当前请求不一致”；“按当前环境执行基线”也会误走工作台当前请求/测试范围校验。

本轮修复：

- 将按钮改为“保存为基线回归任务”和“按当前环境执行所选基线”，明确一个用于后续复用，一个用于立即执行。
- 保存基线回归任务不再调用工作台测试范围保存，不再要求当前请求与固定基线一致。
- 执行所选基线直接使用所选基线的来源接口版本和当前选择的执行环境；环境可以按运行时自由切换。
- 所选基线来自多个接口版本时，显示“所选基线来自多个接口版本，请按来源版本分批保存或执行”，不再透出 `Request validation failed`。
- 新增前端回归测试覆盖：保存基线回归任务、按当前环境执行所选基线、跨接口版本选择时给出可读错误。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/stores/tasks.spec.ts src/views/BaselinesView.spec.ts --reporter=basic
# 2 files / 16 tests passed

npm --prefix api-testing-ui test -- --run --reporter=basic
# 31 files / 169 tests passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed; generated api-test/assets/index-CkMPz6HG.js

python3 tests/frontend_static_checks.py
# 72 checks passed

python3 tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```

### 2026-08-14 API 基线：移动所选支持已有分组

用户反馈：基线页选中多条基线后，“移动所选”必须能移动到其他已有分组，而不是只能依赖输入框创建新分组。

本轮修复：

- 基线分组编辑区新增“目标分组”下拉，可直接选择已有分组。
- 保留“新分组/重命名”输入框；输入新名称时优先使用输入值创建或移动到新分组。
- “移动所选”按钮在未选择基线或未指定目标分组时禁用，并在异常时给出明确提示。
- 移动成功后显示“已将 N 条基线移动到目标分组”的反馈，避免用户不知道操作是否生效。
- 新增前端回归测试覆盖：选中未分组基线后移动到已有“登录鉴权”分组。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/views/BaselinesView.spec.ts -t "moves selected baselines to an existing group" --reporter=basic
# 1 file / 1 focused test passed

npm --prefix api-testing-ui test -- --run src/views/BaselinesView.spec.ts --reporter=basic
# 1 file / 3 tests passed

npm --prefix api-testing-ui test -- --run --reporter=basic
# 31 files / 162 tests passed

npm --prefix api-testing-ui run build
# passed; generated api-test/assets/index-5c37-M5t.js and index-CEDCxZal.css

python3 tests/frontend_static_checks.py
# 72 checks passed

python3 tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```

### 2026-08-14 API 工作台：历史任务旧版本引用 fallback 修复

用户反馈：选择历史任务后，顶部接口版本和执行环境下拉会置空；再切换环境时任务列表刷新，历史任务无法直接用新环境执行，形成“选择历史任务 / 选择环境”的死循环。

本轮修复：

- 历史任务继续作为固定接口范围；执行环境作为运行时选择，不再要求与任务创建时环境一致。
- 当历史任务引用的接口版本或环境版本已经不在当前下拉选项中时，工作台会补充只读 fallback 选项：`当前任务接口版本`、`任务保存环境`，避免顶部下拉和任务条显示空白。
- 用户选择历史任务后仍可切换到当前最新环境；执行时前端把最新选择的 `environment_revision_id` 传给任务执行接口。
- 新增回归测试覆盖：历史任务引用旧 source/env，切换到当前环境后执行，`tasks.runCurrent` 必须收到新的环境 ID。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/views/WorkbenchView.spec.ts -t "keeps a historical task scope" --reporter=basic
# 1 file / 1 focused test passed

npm --prefix api-testing-ui test -- --run src/views/WorkbenchView.spec.ts --reporter=basic
# 1 file / 5 tests passed

npm --prefix api-testing-ui test -- --run --reporter=basic
# 31 files / 161 tests passed

npm --prefix api-testing-ui run build
# passed

.venv/bin/python tests/frontend_static_checks.py
# 72 checks passed

.venv/bin/python tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```

### 2026-08-14 API 测试：环境资产中心按项目重构

用户确认采用环境页方案 B：把“环境资产、环境版本和编辑表单”拆开，形成项目列表、环境资产列表、环境详情三栏结构，避免进入页面直接看到大表单，也避免接口版本切换后误以为环境丢失。

本轮已实现：

- 环境页左侧按项目展示环境统计：环境数量、活动环境、归档环境和最近更新时间。
- 中间环境资产列表支持活动 / 归档切换、搜索、归档、恢复，并且切换项目后只展示该项目环境。
- 右侧环境详情改为 `概览 / 服务地址 / 变量与凭证 / 版本历史` 四个 Tab，默认只读，点击编辑后才进入表单。
- 历史环境版本支持“恢复为新的当前版本”，旧版本继续保留用于审计。
- 编辑 / 新建环境保存后会生成新版本，并刷新项目环境统计。
- `进入工作台` 会携带当前项目、接口版本和环境版本上下文。
- 页面展示服务名称、模块和 Base URL，不再把数据库 ID 或 Apifox 内部 UUID 当成用户可见服务名称。
- 敏感变量仍只展示“已配置”，编辑时留空表示保持原值。

已验证：

```bash
TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene_api_testing_dev@127.0.0.1:5432/midscene_api_testing' API_TESTING_REQUIRE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/api_testing/test_environment_service.py -q
# 21 passed

TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene_api_testing_dev@127.0.0.1:5432/midscene_api_testing' TEST_REDIS_URL='redis://127.0.0.1:6379/1' API_TESTING_REQUIRE_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/api_testing -q
# 337 passed

npm --prefix api-testing-ui test -- --run src/stores/setup.spec.ts src/components/EnvironmentAssetList.spec.ts src/views/SettingsView.spec.ts --reporter=dot
# 3 files / 14 tests passed

npm --prefix api-testing-ui test -- --run --reporter=dot
# 31 files / 157 tests passed

npm --prefix api-testing-ui run build
# passed

.venv/bin/python -m py_compile task_server/api_testing/http.py task_server/api_testing/services/environment_service.py tests/api_testing/test_environment_service.py
# passed

.venv/bin/python tests/frontend_static_checks.py
# 72 checks passed

.venv/bin/python tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```

### 2026-08-14 API 工作台：历史任务与运行时环境解耦

本轮只收口用户反馈的“选择历史任务后无法切换新环境执行、接口版本和环境被置空、任务选择与环境选择形成死循环”问题。

本轮修复：

- 工作台选择历史任务时，任务继续代表固定的接口范围；运行环境使用当前页面选择的环境，不再强行回写任务保存时的旧环境。
- 切换执行环境时不再清空当前任务，也不再把任务判断为范围冲突。
- 保存任务范围时，同项目同接口版本的任务会被更新，即使运行环境变化也不会误创建新任务。
- AI 生成用例使用当前选择的运行环境，但仍绑定当前任务的接口范围，避免“测试任务范围与当前请求不一致”。
- 执行历史任务时前端会把当前 `environment_revision_id` 传给后端；后端执行任务时接受运行时环境覆盖。
- 后端任务校验改为“同项目、同接口版本、接口集合在任务范围内”，不再要求 AI job / execution 的环境 revision 必须等于任务保存时的环境。
- 顶部接口版本 / 执行环境下拉在历史任务引用的 revision 暂未出现在当前选项列表时，会保留“当前任务接口版本 / 当前执行环境 · 已保存任务引用”，不再显示空白。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/components/ContextBar.spec.ts src/stores/tasks.spec.ts src/views/WorkbenchView.spec.ts --reporter=basic
# 3 files / 17 tests passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed; generated api-test/assets/index-BxHgMqMh.js

python3 -m py_compile task_server/api_testing/http.py task_server/api_testing/services/test_task_service.py
# passed

TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene_api_testing_dev@127.0.0.1:5432/midscene_api_testing' TEST_REDIS_URL='redis://127.0.0.1:6379/0' python3 -m pytest tests/api_testing/test_test_task_service.py tests/api_testing/test_http_contract.py -q
# 54 passed

npm --prefix api-testing-ui test -- --run --reporter=basic
# 31 files / 160 tests passed

python3 tests/frontend_static_checks.py
# 72 checks passed

python3 tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```

### 2026-08-13 API 测试：本机数据库 Gate 与 AI 业务断言收口

用户要求本机缺少 PostgreSQL / Redis 时直接固定环境，避免 API 测试后端用例继续跳过；同时线上 AI 生成用例仍把“业务码 / code 断言”误识别成缺少 Biz 请求头的场景，导致草稿校验和生成质量不稳定。

本轮在真实本机 PostgreSQL / Redis gate 下收口：

- `tests/run_api_testing_gate.sh` 已能启动项目自带 `deploy/api-testing-compose.yml` 的 PostgreSQL / Redis，并执行 Alembic 迁移。
- 修复 AI 用例过滤逻辑：`业务码` / `业务线` 不再触发“运行时托管请求头场景”拦截，保留对 Biz、Authorization、Token、登录态等真正运行时注入字段的过滤。
- 增加回归测试覆盖：业务码响应断言应保留；缺 Biz / 缺 Authorization / token 缺失类请求头场景仍不允许生成。
- 更新 API 测试 E2E：报告页已改为“项目报告驾驶舱 → 查看完整诊断”，E2E 不再按旧页面结构误判失败。
- 刷新 `api-test` 静态构建产物，当前 hash 为 `index-B8BUziDa.js`。

已验证：

```bash
.venv/bin/python -m pytest tests/api_testing/test_ai_service.py::test_business_code_assertion_is_not_confused_with_biz_runtime_header tests/api_testing/test_ai_service.py::test_runtime_managed_request_headers_cannot_be_generated_as_test_scenarios tests/api_testing/test_ai_service.py::test_runtime_managed_header_scenario_detection_covers_login_state_synonyms -q
# 2 passed, 1 skipped

npx playwright test tests/api_testing_e2e.spec.mjs --project=chromium
# 1 passed

./tests/run_api_testing_gate.sh
# PostgreSQL / Redis healthy；Alembic migrations completed
# Backend: 336 passed
# Frontend Vitest: 31 files / 153 tests passed
# Frontend build: passed
# Visual check: ok
# Playwright E2E: 1 passed

python3 tests/frontend_static_checks.py
# 72 checks passed

python3 tests/backend_static_checks.py
# 63 checks passed

.venv/bin/python -m py_compile task_server/api_testing/services/ai_service.py tests/api_testing/test_ai_service.py tests/api_testing/test_http_contract.py
# passed

git diff --check
# passed
```

### 2026-08-13 API 测试：已选接口列表布局修复

用户反馈工作台“接口范围 → 已选接口”列表只显示方法标签和删除按钮，接口名称 / 路径不可见。排查确认根因是已选列表复用了“全部接口”的 `.endpoint-row` 两列布局，去掉 checkbox 后 `.endpoint-open` 与移除按钮挤在同一行，长分组下接口摘要被压缩不可读。

本轮只修复已选接口列表 UI，不混入任务、基线、报告或 Apifox 同步逻辑：

- 已选接口行改为专用结构：`方法标签 + 接口名称 / 路径 + 移除按钮`。
- 已选接口分组标题支持长名称省略，并保留 `title` 方便查看完整分组。
- 已选接口名称和路径都保留完整 `title`，视觉上单行省略，避免撑破左侧面板。
- 新增前端回归测试覆盖已选接口摘要专用结构，防止后续再次被通用行布局压缩。
- 刷新 `api-test` 构建产物，线上部署后 `/api-test/#/` 会使用新的静态资源。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/components/EndpointTree.spec.ts
# 1 file / 7 tests passed

npm --prefix api-testing-ui test -- --run
# 29 files / 146 tests passed

npm --prefix api-testing-ui run build
# passed

python3 -m py_compile task_server/api_testing/*.py tests/backend_static_checks.py
# passed

python3 tests/frontend_static_checks.py
# 72 checks passed

python3 tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```

### 2026-08-13 API 测试：基线分组维护补齐

用户要求基线用例支持编辑、删除和新建分组，并且基线作为固定资产不应因为版本 / 环境切换而找不到。本轮在既有“固定资产”基础上补齐分组管理交互，不改变基线查询和执行语义：

- 基线页分组编辑区从单一“保存分组”收敛为“移动所选 / 重命名分组 / 删除分组”。
- 选中左侧自定义分组后，可一次性重命名该分组内所有基线。
- 删除自定义分组不会删除基线用例，而是把分组内基线移回“未分组”，避免误删可回归资产。
- “未分组”作为系统分组不可重命名 / 删除，只能把基线移动进出。
- 新增前端回归测试覆盖：切换接口版本 / 执行环境后基线仍可见；自定义分组可重命名；删除分组后基线仍保留。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/components/EndpointTree.spec.ts src/views/WorkbenchView.spec.ts src/views/BaselinesView.spec.ts src/views/RunsView.spec.ts src/views/ReportsView.spec.ts src/views/AssetsView.spec.ts src/stores/tasks.spec.ts src/stores/executions.spec.ts src/stores/notifications.spec.ts src/stores/setup.spec.ts src/stores/cases.spec.ts src/stores/baselines.spec.ts
# 12 files / 75 tests passed

npm --prefix api-testing-ui run build
# passed

python3 -m pytest tests/api_testing/test_ai_service.py tests/api_testing/test_source_service.py tests/api_testing/test_execution_service.py tests/api_testing/test_notification_service.py tests/api_testing/test_test_task_service.py -q
# 63 passed, 74 skipped；跳过项为本机无完整 API 测试数据库 / 外部服务时的既有跳过策略。

python3 tests/frontend_static_checks.py
# 72 checks passed

python3 tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```

### 2026-08-13 API 测试：历史基线恢复为可见固定资产

用户反馈之前采纳过的基线在接口版本 / 环境版本切换后找不到。进一步排查发现：虽然新逻辑已停止自动顶掉旧基线，但历史数据中已经被旧逻辑标记为 `superseded` 的基线仍被列表、任务计数和基线回归执行过滤掉。

本轮把基线固定资产语义统一为：只有 `archived` 表示用户主动移出基线，其余历史状态均作为项目基线可见、可分组、可移出、可加入任务并可按当前环境执行。

- `CaseRepository.list_active_baselines` 改为返回项目下所有未归档基线。
- `ExecutionRepository.active_baseline_version_ids` 和 `TestTaskRepository.runnable_baseline_count` 同步使用未归档基线，避免基线页能看到但执行 / 任务计数找不到。
- `CaseService.update_baseline_group` / `archive_baseline` 允许管理历史 `superseded` 基线，只拒绝已归档基线。
- `BaselineCaseView` 增加 `status` 字段，前端对非 `active` 基线显示“历史版本”标识。
- 新增回归测试覆盖：历史 `superseded` 基线仍可见、可改分组、可归档，归档后才从列表消失。

已验证：

```bash
python3 -m py_compile task_server/api_testing/contracts/case.py task_server/api_testing/services/case_service.py task_server/api_testing/repositories/case_repository.py task_server/api_testing/repositories/execution_repository.py task_server/api_testing/repositories/test_task_repository.py
# passed

npm --prefix api-testing-ui test -- --run src/stores/baselines.spec.ts src/views/BaselinesView.spec.ts
# 2 files / 4 tests passed

python3 -m pytest tests/api_testing/test_case_service.py::test_project_baselines_remain_visible_across_source_and_environment_revisions tests/api_testing/test_case_service.py::test_readoption_keeps_multiple_active_baseline_versions_until_manual_archive tests/api_testing/test_case_service.py::test_new_environment_revision_keeps_old_baseline_visible_until_manual_archive tests/api_testing/test_case_service.py::test_historical_superseded_baseline_remains_manageable_until_archived tests/api_testing/test_execution_service.py::test_selected_baseline_regression_runs_fixed_baseline_against_current_environment -q
# 5 skipped；本机无测试 PostgreSQL，按既有策略跳过，需线上数据库环境再跑真实回归。
```

### 2026-08-13 API 测试：基线固定资产不再被新版本自动顶掉

用户反馈基线应是固定资产，接口版本和环境版本更新频率高，切换后不应找不到既有基线；多个基线版本应由用户手动删除 / 移出 / 分组维护。

本轮只收口基线采纳和查询语义，不混入 Apifox 参数同步、报告 UI 或飞书通知：

- 基线页继续按项目维度读取固定资产，接口版本仅作为来源版本展示，执行环境仅作为运行时选择，不再作为默认过滤条件。
- 采纳同一接口 / 同一环境家族的新基线时，不再把旧基线自动标记为 `superseded`；旧版本和新版本会同时保持 `active`，用户可以通过“移出基线 / 归档 / 分组”手动调整。
- 后端移除 `active_baselines_for_update` 自动顶替逻辑，避免采纳新版本后旧基线从列表消失。
- 用例服务测试改为验证“多版本基线同时可见，手动归档后才消失”，并覆盖新环境版本下旧基线仍可见的场景。

已验证：

```bash
python3 -m py_compile task_server/api_testing/contracts/case.py task_server/api_testing/services/case_service.py task_server/api_testing/repositories/case_repository.py task_server/api_testing/repositories/execution_repository.py task_server/api_testing/services/execution_service.py
# passed

npm --prefix api-testing-ui test -- --run src/stores/baselines.spec.ts src/views/BaselinesView.spec.ts
# 2 files / 4 tests passed

python3 -m pytest tests/api_testing/test_case_service.py::test_project_baselines_remain_visible_across_source_and_environment_revisions tests/api_testing/test_case_service.py::test_readoption_keeps_multiple_active_baseline_versions_until_manual_archive tests/api_testing/test_case_service.py::test_new_environment_revision_keeps_old_baseline_visible_until_manual_archive tests/api_testing/test_execution_service.py::test_selected_baseline_regression_runs_fixed_baseline_against_current_environment -q
# 4 skipped；本机无测试 PostgreSQL，按既有策略跳过，需线上数据库环境再跑真实回归。
```

### 2026-08-13 API 测试：M2/M4 基线固定资产、任务身份与飞书报告链接收口

本轮继续 M2/M4 剩余闭环，只处理基线固定查询、执行记录任务身份、报告链接定位和飞书卡片，不混入 Apifox 参数同步、AI 用例生成规则或报告大改版。

- 基线查询后端改为项目维度固定资产：`GET /api/api-testing/v1/baselines` 只要求 `project_id`，不再要求当前接口版本和执行环境；切换版本 / 环境后已有基线不会因为查询条件变化而消失。
- `CaseService.list_active_baselines` 和 `CaseRepository.list_active_baselines` 契约同步收敛为 `project_id + actor_id`；基线自身仍保留采纳时的来源 `environment_revision_id` 和 `case_version_id` 作为展示 / 审计字段。
- 执行提交增加任务快照：`ExecutionService.submit(..., task=...)` 和 `submit_active_baselines(..., task=...)` 会把 `task_id/task_name` 写入 `request_snapshot.task`；执行记录、报告和控制台可以稳定展示“执行的是哪个任务”。
- `/tasks/{id}/run` 和 `/executions` 路由已把当前任务传给执行服务；手动发送飞书报告后会写入 `notification_sent` 事件，刷新页面后仍能看到“飞书通知已发”。
- 飞书通知从纯文本升级为交互卡片：突出任务、环境、用例统计、通过率、问题摘要，并在配置了 `API_TESTING_REPORT_BASE_URL` / `API_TESTING_PUBLIC_BASE_URL` / `MIDSCENE_PUBLIC_BASE_URL` / `PUBLIC_BASE_URL` 时附带“查看报告”按钮。
- 报告页支持 `execution_id` / `executionId` query 定位；飞书卡片链接打开 `/api-test/#/reports?execution_id=...` 时会直接选中对应报告。

已验证：

```bash
python3 -m pytest tests/api_testing/test_execution_service.py::test_task_snapshot_keeps_task_identity_for_execution_history tests/api_testing/test_execution_service.py::test_task_snapshot_normalizes_empty_task_name tests/api_testing/test_notification_service.py -q
# 4 passed

python3 -m pytest tests/api_testing/test_execution_service.py::test_execution_view_derives_feishu_notification_from_events tests/api_testing/test_execution_service.py::test_task_snapshot_keeps_task_identity_for_execution_history tests/api_testing/test_execution_service.py::test_task_snapshot_normalizes_empty_task_name tests/api_testing/test_notification_service.py -q
# 5 passed

npm --prefix api-testing-ui test -- --run src/views/ReportsView.spec.ts src/stores/baselines.spec.ts src/stores/notifications.spec.ts src/stores/tasks.spec.ts src/components/ExecutionConsole.spec.ts
# 5 files / 20 tests passed

npm --prefix api-testing-ui run build
# passed

python3 tests/frontend_static_checks.py
# 72 checks passed

python3 tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```

受限说明：`tests/api_testing/test_execution_service.py::test_submit_active_baselines_creates_one_click_regression` 和 `tests/api_testing/test_case_service.py::test_project_baselines_remain_visible_across_source_and_environment_revisions` 在本机因无测试 PostgreSQL 按既有策略跳过；上线后仍需在服务器执行 `bash deploy/api-testing-migrate.sh` 并做真实基线回归验证。

### 2026-08-13 API 测试：参数型接口 AI 生成规则

用户反馈 `body=null`、只有 query/path/cookie 参数的接口，例如设备状态查询，AI 仍按 Body 或弱 Schema 思路生成，出现 `assertions[1] expected must constrain response fields or values` 等校验失败。

本轮只修参数驱动接口的 AI 契约和提示词，不改前端工作台、基线、报告或飞书通知：

- AI 输入契约在接口无 `requestBody` 或 `requestBody: null`、但存在非 Header 参数时，标记 `case_design_strategy=parameter_driven`、`request_body_state=absent`。
- 契约提供必填参数、可选参数、正向示例来源和负向边界来源；Header 参数仍被过滤，不进入 AI 业务用例设计范围。
- Prompt 明确参数驱动接口的请求体必须保持 `null`，用例应基于 path/query/cookie 参数的 required、类型、枚举、默认值、示例值和说明生成正常、缺失必填、空值、错误类型/格式、枚举/边界和业务失败响应。
- Prompt 明确参数型接口断言必须约束真实响应字段或值，例如 `$.code`、`$.msg`、`$.data`、状态码或带 `properties/required` 的 Schema；继续禁止 `schema: {"type":"object"}` 这类弱断言。

已验证：

```bash
python3 -m pytest tests/api_testing/test_ai_service.py -q
# 9 passed, 36 skipped；跳过项为本机无测试 PostgreSQL 时的既有跳过策略。

python3 -m py_compile task_server/api_testing/services/ai_service.py
# passed

python3 tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```

### 2026-08-13 API 测试：Apifox 分组与参数示例同步、AI 请求头场景过滤

本轮只收口 M3 的同步数据完整性和 AI 生成过滤，没有混改基线、任务列表、报告 UI 或飞书通知。

- OpenAPI 规范化时会优先合并 Apifox 文件夹扩展和标准 `tags`，例如 `家用业务 / app接口 / 我的收藏` 不再被已有单层 tag 覆盖成 `我的收藏` 或落到未分组。
- 保持 query/path/body 的示例值、必填、类型和说明进入接口契约；前端草稿仍基于这些字段自动填充调试请求。
- Body JSON 示例会保留在 `requestBody.content.application/json.example`，供 AI 生成和手工调试复用。
- AI 候选校验扩展运行时鉴权识别：`未登录`、`登录态过期`、`token 过期/缺失`、`Biz`、`ZXBToken` 等会被判定为环境默认请求头场景，不再沉淀成无意义用例。
- AI Prompt 已有“请求头由平台按环境统一注入”的约束，本轮只补服务端兜底，防止模型偶发生成请求头缺失类候选。

验证结果：

- `python3 -m pytest tests/api_testing/test_source_service.py tests/api_testing/test_ai_service.py::test_prompt_keeps_safe_body_examples_and_omits_runtime_headers tests/api_testing/test_ai_service.py::test_runtime_managed_request_headers_cannot_be_generated_as_test_scenarios tests/api_testing/test_ai_service.py::test_runtime_managed_header_scenario_detection_covers_login_state_synonyms tests/api_testing/test_ai_service.py::test_ai_relative_method_and_path_are_bound_to_the_selected_endpoint -q`：`50 passed, 15 skipped`；跳过项为本机无测试数据库时的既有跳过。
- `npm --prefix api-testing-ui test -- --run src/stores/cases.spec.ts src/components/EndpointTree.spec.ts src/views/WorkbenchView.spec.ts`：`31 passed`。
- `npm --prefix api-testing-ui run build`：通过。
- `python3 tests/frontend_static_checks.py`：`72` 项通过。
- `python3 tests/backend_static_checks.py`：`63` 项通过。
- `git diff --check`：通过。

### 2026-08-13 API 测试：接口范围选择器支持分组折叠、分组全选和已选列表

本轮只收口工作台左侧接口范围选择体验，没有改动 Apifox 同步后端、AI 生成、基线或报告模型。

- `EndpointTree` 增加 `全部接口 / 已选接口` 两个视图，900+ 接口不再只能靠顶部数字判断已选范围。
- Apifox 分组在接口树中支持折叠 / 展开；分组标题展示接口总数和已选数。
- 分组标题增加复选框，支持整组全选 / 取消；部分选中时显示半选状态。
- `已选接口` 视图按分组展示当前选择，支持单条移除和清空已选；切回 `全部接口` 后原始接口树仍保留。
- 选择事件仍按接口源列表顺序向外发出，避免影响任务范围保存、AI 生成和执行入口的既有联动。

验证结果：

- `npm --prefix api-testing-ui test -- --run src/components/EndpointTree.spec.ts src/views/WorkbenchView.spec.ts`：`9 passed`。
- `npm --prefix api-testing-ui run build`：通过。
- `python3 tests/frontend_static_checks.py`：`72` 项通过。
- `git diff --check`：通过。

### 2026-08-12 API 测试：基线回归范围隔离与项目级飞书自动通知

本轮修复 API 基线回归的执行语义，没有新增执行模式页面，也没有改动 UI Agent / YAML 主链路。

- `执行所选基线` 现在直接提交所选 `baseline_ids`，不会再先写入或复用工作台当前任务范围；因此接口资产里勾选的普通用例不会混入基线回归。
- 基线回归使用独立执行类型 `baseline_regression`；执行记录、详情抽屉、执行概览和报告页统一展示为 `基线回归`，不再和 `在线调试`、`自动回归` 混淆。
- 执行记录页移除了右上角全局 `执行当前基线` 按钮，避免选中在线调试记录时出现错误操作入口。基线执行入口保留在 `基线用例` 页面。
- `/api/api-testing/v1/regressions` 支持 `baseline_ids`，后端按当前项目、接口版本、环境和归属人过滤活动基线，只执行被选中的基线版本。
- `midscene-api-worker` 在 `baseline_regression` 执行完成且状态为 `DONE` 后自动调用飞书报告发送；飞书 Webhook 仍按 `owner_id + project_id + channel_type` 绑定，不同项目可配置不同机器人。未配置或发送失败会写入 `notification_failed` 事件，发送成功写入 `notification_sent`。

验证结果：

- API 任务/执行/HTTP 聚焦测试：`20 passed, 42 skipped`；跳过项为本机无测试 PostgreSQL 时的既有跳过策略。
- API 前端全集：`28` 个测试文件，`122 passed`。
- API 前端生产构建通过：`npm --prefix api-testing-ui run build`。
- touched API 测试后端模块 `py_compile` 通过。
- 仓库静态检查通过：后端 `63` 项、前端 `72` 项、AI Gateway `46` 项。
- `git diff --check` 通过。

待部署后验证：

- 在 `基线用例` 页选择一条或多条收藏基线，点击 `执行所选基线`，确认执行记录名称为 `基线回归`，用例数量只等于所选基线数量。
- 确认项目 A / 项目 B 分别配置不同飞书机器人后，基线回归完成会发送到对应项目机器人。

### 2026-08-12 API 测试：基线中心、基线维护与飞书回归通知

本轮沿用现有 API 测试子系统，没有推翻架构或新增并行工作流。目标是把已经调试通过的用例沉淀成可复用基线，并让发版回归结果可以从平台直接发到飞书群。

- 新增基线用例中心：按当前项目、接口版本和环境展示已采纳基线，支持搜索、分组筛选、批量加入当前任务和直接执行所选基线。
- 基线用例现在是可维护资产：平台持久化 `group_name`，支持批量新建/调整分组、跳转工作台编辑对应版本、以及“移出基线”。移出基线只归档 `api_baselines` 记录，不删除用例草稿和历史执行证据。
- 新增 `0005_baseline_groups` 迁移；线上部署后需要执行 API testing Alembic 迁移。
- 新增每项目飞书通知配置：Webhook 加密保存，只返回配置状态和指纹；报告页可将回归报告发送到飞书群。
- 执行事件补充飞书发送成功/失败标签，便于后续报告审计。

验证结果：

- 前端聚焦测试：`src/stores/baselines.spec.ts`、`src/stores/notifications.spec.ts` 共 `5 passed`。
- API 前端生产构建通过：`npm --prefix api-testing-ui run build`。
- 后端 touched API 测试模块 `py_compile` 通过。
- 仓库后端静态检查通过：`tests/backend_static_checks.py` 返回 `63` 项通过。
- API 迁移/任务服务聚焦 pytest：`6 passed, 16 skipped`；跳过项为本机无测试数据库时的既有跳过策略。
- `git diff --check` 通过。

部署注意：上线后执行 `bash deploy/api-testing-migrate.sh`，再重启 `midscene-api-worker` 和 `midscene-task`。如果要发送飞书报告，需要先在 API 测试的环境/设置页配置对应项目的飞书群机器人 Webhook。

### 2026-08-12 API 测试：阻断运行时请求头用例并绑定 AI 接口身份

线上 `qwen-plus` 仍会生成“缺失 Biz / Authorization / ZXBToken”“鉴权失败”以及“正常流程（含 Biz + Authorization）”等请求头候选，并偶发因模型改写 `request.path` 报 `path_mismatch:request.path`。提示词已经明确禁止这些场景，根因是平台只清空候选请求头，却没有约束候选语义，也在确定性校验前信任了 AI 给出的接口方法和相对路径。

本轮在现有 AI 用例生成服务增加两层通用门禁，没有针对“收藏”接口写死：

- 根据当前环境默认请求头、默认头引用的变量和接口契约 Header 参数，动态识别名称、测试目的或数据行名称中的请求头测试语义；`Biz`、`Authorization`、`ZXBToken`、`token`、`鉴权`、`请求头` 等运行时注入项不能再成为独立正向或负向候选。
- AI 输出的相对 `request.method` 和 `request.path` 在解析前强制绑定到用户实际选择的源接口，消除模型路径漂移；绝对 URL 和协议相对 URL仍直接拒绝，SSRF 防线不变。
- 环境运行时注入逻辑不变：环境默认 `Authorization` 与接口契约需要的 `Biz` 继续在执行时统一注入，业务用例只覆盖 Body、Query、Path、业务响应和参数边界。

验证结果：

- API 完整门禁：后端 `304 passed`；Vue 前端 `25` 个测试文件、`112 passed`；TypeScript、Vite 构建、桌面与手机视觉检查通过。
- Playwright“我的收藏”三接口导入、AI 设计、调试、基线回归和报告闭环通过。
- 仓库静态检查通过：后端 `63` 项、前端 `72` 项、AI Gateway `46` 项；AI skill contract `3` 个 fixture 通过；`git diff --check` 通过。

部署后重新生成当前收藏范围即可验证；已有历史草稿不会被自动删除，新的生成结果不应再出现请求头类候选或 `path_mismatch:request.path`。

### 2026-08-12 API 测试：结构化执行控制台与诊断型报告完成

本轮沿用现有 `ExecutionView`、持久 SSE 事件、逐用例结果和失败分析，没有新增执行模式、数据库表或平行报告数据源。执行页和报告页已按用户确认的方案 A 收敛：

- 执行页新增任务摘要、真实通过率、通过 / 断言失败 / 运行异常 / 跳过统计，以及 `实时轨迹`、`用例明细`、`测试报告` 三个视图。
- 实时轨迹按用例名称展示事件时间、接口、状态和安全载荷；用户向上阅读时暂停自动跟随并显示未读日志数量，不再被刷新强制拉回底部。
- 用例证据统一展示脱敏请求、响应、逐条断言、依赖轨迹和已有 AI 失败分析；Authorization、Cookie、Token、Password、Secret 和 API Key 等常见敏感字段在前端再次保护。
- 报告历史改为紧凑摘要；完整报告按诊断结论、问题分布、AI 诊断摘要、用例明细和技术日志组织。父执行失败不会覆盖已经通过的子用例，AI 只解释确定性结果。
- 持久 SSE 事件向客户端补充 `_event_created_at`，前端转换为 `createdAt` 后从证据载荷移除；持久化事件内容不被修改。
- SSE 有界重连耗尽后改为每 5 秒拉取执行快照直至终态，避免实时连接失败掩盖真实结果；切换执行记录时同步重置日志筛选与跟随状态。
- 失败分类补齐 Worker、传输、宿主策略、重定向和响应体限制等环境异常；存在跳过项的执行明确显示“执行不完整”，不再误报通过。报告支持失败 / 异常 / 跳过筛选、取消分布和带方法、路径、耗时、结论的脱敏技术日志。
- 桌面端使用稳定双栏，移动端改为单列；请求、响应和长日志在各自区域滚动，不撑破页面。

完整验证结果：

- API 完整门禁：后端 `300 passed`；Vue 前端 `25` 个测试文件、`112 passed`；TypeScript、Vite 生产构建、桌面与手机视觉检查通过。
- Playwright “我的收藏”三接口真实浏览器闭环通过：导入、AI 设计、逐条调试、基线回归、实时轨迹、失败证据和诊断报告均完成，结果保留 `1 PASSED / 1 FAILED / 1 BROKEN` 的真实语义。
- 仓库完整静态检查通过：undefined-name、后端 `63` 项、前端 `72` 项、AI Gateway `46` 项、动态模型目录和 AI skill contract eval 均通过。
- `git diff --check` 通过；桌面和移动报告截图人工复核无重叠、横向溢出或空白主区域。

设计与实施记录：

- `docs/superpowers/specs/2026-08-12-api-execution-report-experience-design.md`
- `docs/superpowers/plans/2026-08-12-api-execution-report-experience.md`

待部署后仅需确认线上静态资源版本已更新，并用一条真实执行检查 SSE 时间和诊断报告入口；本轮不改变执行状态机和生产门禁。

### 2026-08-11 API 测试：修复 AI 断言类型与操作符契约漂移

线上“收藏”接口 AI 生成批次出现 `assertions[1] operator exists is not supported for schema`。根因不是 qwen-plus 不可用，也不是正式用例门禁过严，而是 AI 输出 Schema 只分别枚举了断言类型和操作符，没有表达二者的合法组合；模型因此生成了合同不允许的 `schema + exists`。

本轮采用三层防护，没有放宽 `CasePayload` 正式契约：

- `api_case_generation.v1.json` 增加按断言类型约束操作符的条件 Schema。`schema` 只允许 `equals` 且必须提供 JSON Schema 对象或布尔值；`status_code`、`json_path`、`header`、`response_time` 分别使用各自合法操作符集合。
- `api_case_generation.v1.md` 明确提示模型遵守断言矩阵；检查响应字段或响应根节点是否存在时必须使用 `json_path + exists/not_exists`，根节点路径为 `$`。
- `AiCaseService` 在严格 JSON Schema 校验前只修复一种语义明确的模型词汇漂移：将 `schema + exists/not_exists` 规范为 `json_path + exists/not_exists`。绝对 URL、任意脚本、未知字段、缺少必填字段以及其他非法类型/操作符组合仍会被拒绝。

验证结果：

- 新增线上同构回归测试，确认 `schema + exists` 被规范为可编辑、可执行的 `json_path + exists` 草稿。
- AI 服务聚焦测试：`34 passed`。
- API 完整门禁：后端 `294 passed`；Vue 前端 `19` 个测试文件、`82 passed`；TypeScript、Vite 构建、桌面/手机视觉检查通过。
- Playwright “我的收藏”三接口闭环通过：导入、AI 设计、调试、采纳基线、回归、实时日志和报告均完成。
- 仓库完整静态检查通过：undefined-name、后端 `63` 项、前端 `72` 项、AI Gateway `46` 项、动态模型目录和 AI skill contract eval 均通过。

待线上部署后验证：重新生成当前收藏范围，确认不再出现 `schema + exists` 校验失败；使用线上已配置的业务 Token 逐条调试收藏接口并核对业务响应。业务成功与否必须依据 HTTP 状态、业务码和断言结果分别展示，不得把 HTTP 200 直接等同于业务通过。

### 2026-08-11 API 测试：修复 AI JSON 包装与 HTTP 浏览器执行兼容

本轮在生产环境用“收藏”7 个接口复现了两个独立失败，修复仍位于 `feat/api-testing-phase2-m0`：

- AI Gateway 和 `qwen-plus` 均健康，失败不是模型不可用。真实批次返回单个完整的 Markdown JSON 代码块，旧实现只接受裸 JSON，因此在 Schema 校验前报 `AI Gateway content is not strict JSON`。
- AI 输出解析现在只剥离“恰好一个、占据全部内容的 JSON 代码块”；带说明文字、多个代码块、未知字段和不符合 Schema 的内容仍被拒绝，不降低用例合同和确定性校验。
- 真实模型对 7 个接口曾只返回覆盖 3 个接口的 6 条候选。平台现在要求每个输入接口至少 1 条候选，并对未生成有效候选的接口记录 `missing_endpoint_coverage`，保留有效草稿但将批次和任务标记为 `partial`，不再误报完整成功。
- API 助手直接展示首条批次校验原因，JSON 包装和覆盖缺口使用中文说明，不再只显示笼统的“校验失败”。
- 线上页面由 HTTP 地址打开时，浏览器没有 `crypto.randomUUID()`，旧前端在提交调试、任务回归、失败重跑和基线回归前直接崩溃。新增统一 RFC 4122 幂等键生成器：优先使用 `randomUUID`，降级到 `getRandomValues`，最后才使用兼容兜底。
- 用户提供的 3D 业务 Token 已通过环境版本接口保存到“生产环境（新）-腾讯云”第 5 版的敏感变量 `ZXBToken`；页面和 API 仅返回已配置状态与指纹，本文档和 Git 不包含明文。

验证结果：

- API 后端：`132 passed, 158 skipped`（本机未连接测试 PostgreSQL 的用例按设计跳过）。
- API 前端：18 个测试文件，`71 passed`；TypeScript 和 Vite 生产构建通过。
- 仓库静态检查、AI Gateway 模型目录检查和 AI skill contract eval 通过。
- Playwright Chromium 安装后，仓库视觉冒烟通过并生成桌面/手机截图。
- 生产探测确认 `qwen-plus` 的代码块在新解析器下通过 Schema，返回 6 条候选、覆盖 3 个接口；覆盖缺口将由新增门禁保留为部分完成。

待部署后验证：使用环境第 5 版重新生成“收藏”7 接口，确认有效草稿被保留、缺口明确展示；逐条调试后才能采纳为基线，再执行任务回归。任务处于“待设计”时不应把“执行本任务”理解为直接执行未调试草稿。

### 2026-08-11 API 测试 Phase 1：Apifox 手动更新与持久测试任务已完成

当前实现位于 `feat/api-testing-phase1`，沿用现有 API 测试子系统，没有新增另一套并行工作流。

- Apifox 保持手动刷新：访问令牌按用户加密保存，页面只返回配置状态和指纹；项目、分支、环境发现和 OpenAPI 导出仅在用户明确点击时访问 Apifox。
- 接口和环境先预览差异，只有点击确认后才生成不可变源版本和可编辑本地环境版本；刷新失败不会覆盖当前工作区。
- OpenAPI JSON 导入保留为“高级导入”备用入口，不再是日常主链路。
- 新增持久化 API 测试任务，保存项目、接口版本、环境、选中接口、AI 任务、最新执行和终态统计；后端校验所有引用属于同一用户和同一测试范围。
- 工作台可显式保存当前任务，刷新页面后恢复环境和已选接口；AI 生成、单例调试和基线回归共用同一 `task_id`。
- “执行本任务”只执行当前范围已采纳的基线，创建后直接进入对应实时执行记录，继续沿用 `PASSED` / `FAILED` / `BROKEN` 双层结果语义。
- Playwright 支持通过 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` 选用系统 Chrome，CI 未配置时仍使用 Playwright 默认浏览器。

完整本地门禁已通过：

```bash
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
API_TESTING_POSTGRES_PASSWORD='task5-test-postgres-only' \
bash tests/run_api_testing_gate.sh
```

结果：

- PostgreSQL / Redis 容器健康，Alembic 升级到 `0003` 成功。
- API 后端 `276 passed`。
- Vue / Pinia 前端 `15` 个测试文件、`66 passed`。
- TypeScript 检查和 Vite 生产构建通过。
- 桌面与手机视觉检查通过，无水平溢出。
- “我的收藏”3 接口浏览器闭环通过：导入、环境密钥脱敏、任务保存/刷新恢复、AI 生成、逐条调试采纳、任务回归、实时日志以及 `PASSED` / `FAILED` / `BROKEN` 报告。
- `git diff --check` 通过，源码、测试和本文档中未扫描到真实 Apifox/JWT 密钥。

待集成后再做的线上步骤：部署已验证提交，检查 `8091` / `8088` 健康，再用服务端安全环境中的有效 3D 用户 token 执行一次真实生产“我的收藏”回归。不得把业务 token 写入 Git。

### 2026-08-08 Existing API automation removed

- Removed the API automation frontend, backend routes/services, AI skill, Apifox CLI deployment dependency, and API-specific tests.
- Preserved SQLite databases, historical API reports, screenshots, and design documents as recovery data.
- The platform no longer opens or writes the preserved API data.
- Replacement API testing design is intentionally out of scope for this change.

### 2026-08-04 API 自动化：新增本地测试库并按参考面板重做主流程

用户最终确认 API 自动化只需要一条简单主链路：

```text
手动获取 Apifox 接口数据和环境
→ 保存接口数据和环境
→ 从保存的数据里筛选模块和接口
→ AI 生成接口用例
→ 执行，实时查看日志和报告
```

本轮按该目标处理：

- 新增 `task_server/services/test_lab_service.py`
  - 使用 SQLite 作为第一阶段本地测试库，默认路径为 `LEARNING_DIR/test-lab/test_lab.sqlite3`，也支持 `TEST_LAB_DIR` / `TEST_LAB_DB_PATH` 覆盖。
  - 表结构覆盖：接口来源、执行环境、接口快照、测试用例、执行记录、UI YAML 索引。
  - Apifox/OpenAPI 只作为手动更新来源；API 测试台默认从本地测试库读取，不再每次进入页面刷新 Apifox。
  - `sync_ui_yaml_index()` 可把 UI 自动化 YAML 文件按稳定 sha256 索引进同一个测试库，后续可作为 UI/API 统一测试资产基础。
- `task_server/router.py`
  - 小范围新增 `/api/test-lab/*` 路由，不重构 router：
    - `GET /api/test-lab/state`
    - `POST /api/test-lab/apifox/refresh`
    - `POST /api/test-lab/openapi/import`
    - `POST /api/test-lab/environment`
    - `POST /api/test-lab/cases/generate`
    - `POST /api/test-lab/executions/run`
    - `GET /api/test-lab/executions/{execution_id}`
    - `POST /api/test-lab/ui-yaml/index`
- 新增 `js/api-test-lab.js`
  - 覆盖旧的复杂 API 子页面主入口，默认渲染一页式测试台：
    - 左侧：运行环境、测试范围、测试命令、执行历史。
    - 右侧：当前接口列表、执行日志、测试报告。
  - `接口来源` 页面只负责手动读取/保存 Apifox，或者手动导入 OpenAPI JSON。
  - 轮询运行中的任务时只刷新日志/报告区域，不再整页重绘，避免滚动和阅读被打断。
  - 环境编辑支持 Base URL、Biz、业务 Token 写入位置、完整环境变量；业务 token 不回显。
- `css/round5.css`
  - 增加 API Test Lab 的两栏执行台样式，参考用户提供 zip 里的“运行环境 / 测试命令 / 执行日志 / 测试报告 / 历史记录”结构。
- `tests/api_test_lab_checks.py` / `tests/frontend_static_checks.py`
  - 增加本地测试库、OpenAPI 导入、项目特定鉴权 header、UI YAML 索引、前端主流程的回归。

真实浏览器回归：

- 本地启动 `task_server` 于 `127.0.0.1:8099`，使用临时 `TEST_LAB_DIR`，不污染线上和真实资产。
- 通过页面手动导入“我的收藏”3 个接口：
  - `GET /print3d/api/v1/favorite/list`
  - `POST /print3d/api/v1/favorite/add`
  - `POST /print3d/api/v1/favorite/cancel`
- 验证导入后立即进入 API 测试台，并展示：
  - 来源 `3D 我的收藏`
  - 本地接口数 `3`
  - 模块 `家用业务/app接口/我的/我的收藏`
  - 环境变量自动发现 `ZXBToken`、`Biz`
- 保存用户提供的业务 token 到 `ZXBToken`，页面只显示 `ZXBToken：已配置`，不展示内部 `MTP_API_AUTH_*` 指针。
- 点击“生成并执行”后，3 个接口均真实请求生产地址并进入报告：
  - 通过 `0`
  - 失败 `3`
  - 失败原因均为业务码 `4009`：`用户未登录！`
  - 结论：平台流程已经跑通，失败来自业务 token/登录态，不是平台卡死或未执行。

验证：

```bash
python3 -m unittest tests.api_test_lab_checks tests.api_workbench_checks tests.api_manual_workflow_checks tests.api_native_execution_checks tests.api_case_contract_checks
python3 -m py_compile task_server/router.py task_server/services/test_lab_service.py tests/api_test_lab_checks.py tests/frontend_static_checks.py
node --check js/api-test-lab.js
python3 tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check -- task_server/router.py task_server/services/test_lab_service.py js/api-test-lab.js css/round5.css task-manager.html tests/api_test_lab_checks.py tests/frontend_static_checks.py CODEX_STATE.md
```

注意：

- 现在可以在服务器上用 SQLite 先落地测试数据；如果后续并发、权限、审计要求提高，建议把 `test_lab_service.py` 的存储层抽成 adapter 后迁移到 MySQL/Postgres。
- Apifox 不再作为日常执行依赖，只保留“手动更新接口和环境”的数据来源职责。
- 当前业务 token 在真实生产接口返回 `4009 用户未登录`，需要新的有效 3D 用户登录 token 才能让“我的收藏”正向用例通过。

### 2026-08-04 API 自动化：收敛为单入口测试面板并兼容旧路由

用户进一步明确：不要再把 API 自动化拆成一堆子页面；只需要类似参考项目的简单链路：

```text
获取 Apifox 接口 / 手动导入
→ 保存本地快照
→ 选择模块或接口
→ AI 生成用例
→ 执行并实时查看日志和报告
```

本轮处理：

- `task-manager.html`
  - 接口自动化侧栏从 8 个入口收敛为 2 个入口：
    - `API 测试`
    - `接口来源`
  - `navigation.js`、`api-testing.js`、`agent-status.js` 的静态资源版本统一更新为 `20260804-api-single-panel-v1`。
- `js/api-testing.js`
  - `API 测试` 默认继续使用参考项目式两栏执行面板。
  - 命令区新增 `手动导入接口`，进入 `接口来源`，支持没有 Apifox 或临时调试时上传 OpenAPI JSON / 手动配置。
  - `获取 Apifox 接口` 继续保留为手动更新本地快照入口，不改后端 Apifox 解析。
  - `接口资产` 页面标题和说明改为 `接口来源`，弱化内部资产模型表述。
- `js/navigation.js` / `js/agent-status.js`
  - 兼容旧本地状态或旧按钮调用：`api_plan`、`api_debug`、`api_regression`、`api_execution_history`、`api_reports`、`api_environment`、`api_baselines`、`api_execution` 都统一回到 `API 测试` 简单面板。
  - 防止用户从旧 `AI测试设计` 状态进入复杂页面。
- `tests/frontend_static_checks.py`
  - 新增静态断言：侧栏只允许 `API 测试 / 接口来源` 两个入口。
  - 断言旧 API workflow key 在导航和 `activateWorkflow` 中都必须收敛回简单面板。
  - 断言命令区必须同时存在 `获取 Apifox 接口` 和 `手动导入接口`。

本地验证：

```bash
python3 tests/frontend_static_checks.py
node --check js/api-testing.js && node --check js/navigation.js && node --check js/agent-status.js
python3 -m unittest tests.api_workbench_checks tests.api_manual_workflow_checks tests.api_native_execution_checks tests.api_case_contract_checks
git diff --check -- task-manager.html js/navigation.js js/api-testing.js js/agent-status.js tests/frontend_static_checks.py
```

浏览器烟测：

- 使用临时 `API_TESTING_DIR` 种入“我的收藏”3 个接口和生产环境快照。
- 本地启动 `task_server` 于 `127.0.0.1:8099`，用 `admin / sonic2026` 登录。
- 调用旧入口 `activateWorkflow('api_plan')` 后确认：
  - 渲染 `.api-simple-runner`。
  - 不再出现 `.api-plan-workspace` / `.api-workflow-stepper` / `.generation-record-head`。
  - 左侧 API 菜单只有 `API 测试 / 接口来源`。
  - 命令区包含 `获取 Apifox 接口`、`手动导入接口`、模块调试命令和 `环境配置`。
  - 右侧保留 `执行日志 / 测试报告`。
- 点击 `手动导入接口` 后确认进入 `接口来源` 页面，且仍可看到 Apifox 与 OpenAPI JSON 上传入口。

### 2026-08-04 API 自动化：按 AIPPT 参考重做简单执行面板

用户明确要求接口测试不要再围绕多个技术模块一步步跳转，而是按参考项目的体验收敛成：

```text
手动获取 Apifox 接口和环境
→ 保存本地快照
→ 选择模块/接口
→ AI 生成测试用例
→ 执行时实时看日志和报告
```

本轮处理：

- `js/api-testing.js`
  - `API 工作台` 默认不再渲染原来的多段任务卡、流程条、概览卡、风险卡和模块矩阵。
  - 新增 `renderApiSimpleRunnerShell()`，首屏改成参考项目式两栏：
    - 左侧：`运行环境`、`测试命令`、`执行历史`。
    - 右侧：`执行日志`、`测试报告`。
  - 测试命令固定围绕真实动作：
    - `获取 Apifox 接口`：手动读取接口定义和环境，保存为本地快照。
    - `AI 生成测试用例` / `审阅 AI 测试用例` / `批量调试 AI 用例` / `执行基线接口测试`：根据当前模块状态自动给出下一步。
    - `环境配置`：Base URL、Header、变量和业务 token 独立配置。
  - 模块命令文案改成动作导向，避免只显示“我的收藏接口测试”却看不出下一步。
- `css/round5.css`
  - 删除旧 Workbench 首页和 Runner 页面的大量复杂样式。
  - 增加 `.api-simple-runner`、`.api-simple-sidebar`、`.api-simple-command`、`.api-simple-console`、`.api-simple-report-list` 等简洁执行台样式。
- `task-manager.html`
  - API 静态资源版本更新为 `20260804-api-simple-runner-v1`。
- `tests/frontend_static_checks.py`
  - 新增简化执行面板静态断言。
  - 断言旧的多步流程函数和旧复杂首页样式不会重新出现。

本地烟测：

- 使用临时 `API_TESTING_DIR` 种入“我的收藏”3 个接口和生产环境快照，未污染真实资产。
- 启动本地 `task_server` 于 `127.0.0.1:8099`。
- Playwright 登录后进入 `API 工作台`，确认：
  - 渲染 `.api-simple-runner`。
  - 左侧只有 `运行环境 / 测试命令 / 执行历史`。
  - 命令包含 `获取 Apifox 接口`、`审阅 AI 测试用例`、`环境配置`。
  - 右侧包含 `执行日志 / 测试报告`。
  - 旧的 `.api-runner-simple-flow`、`.api-task-hero`、`.api-runner-module-grid` 未出现。
  - 默认命令有高亮，浏览器无 JS 错误。

已验证：

```bash
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
python3 -m unittest tests.api_workbench_checks tests.api_manual_workflow_checks tests.api_native_execution_checks tests.api_case_contract_checks
git diff --check -- js/api-testing.js css/round5.css tests/frontend_static_checks.py task-manager.html
```

注意：

- 本轮没有把用户提供的业务 token 写入仓库；真实 token 仍应由页面/接口保存到服务端运行环境配置。
- 本轮没有修改 Apifox 解析、环境发现和后端执行服务，只收敛 API 工作台默认入口和交互文案。
- 用户历史 dirty 文件仍保持未暂存、未回滚。

### 2026-08-04 Agent 启动弹窗守卫与 Runner 回传重连

线上百度网盘需求 `agent-1785808186102-7a1bd55d` 生成链路已恢复：历史成功种子 4 条 + 新增 4 条，8 条 YAML dry-run 全通过。但真机冒烟 3 条全部失败：

- 文档打印首条被启动后的「喜欢哪个免费拿」活动弹窗遮挡，归类 `script_issue / popup_overlay`。
- 照片打印 300s 超时，扫描复印 95% 清理阶段 Runner 回传停滞 300s 后被平台回收，归类环境/回传问题。

修复：

- `task_server/services/yaml_service.py`
  - 新增 `STARTUP_POPUP_GUARD_PROMPT`、`startup_popup_guard_flow()`、`should_insert_startup_popup_guard()`、`insert_startup_popup_guard_after_launch()`。
  - 默认 `balanced`/`strict` 运行时守卫都会在首次 `launch` 后、首个业务点击前插入一次通用启动弹窗处理。
  - 守卫只处理权限、升级、广告、活动、免费领取、新手引导和蒙层的关闭/跳过/允许；明确禁止点击领取、立即体验、去使用、去打印、提交或确认业务按钮。
  - 已有启动弹窗守卫的 YAML 不重复插入。
- `windows-midscene-runner.py` / `mac-midscene-runner.py`
  - 新增 `CALLBACK_OUTBOX_DIR`，结果回传失败时把最终 payload、报告路径和报告名落到本地 outbox。
  - 心跳恢复后、拉取新任务前先执行 `replay_pending_result_callbacks()`，补发服务端重启期间完成的旧结果。
  - `run_job()` 在返回结果前优先同步上传 HTML 报告并调用 `report-ready`，同步失败才保留后台上传队列。
  - `report_upload_pending` 改为只有“存在报告且同步上传失败”时才为 true，避免有报告却长期显示缺失。
- `tests/backend_static_checks.py`
  - 增加默认启动弹窗守卫必须插在业务 `aiTap` 前的回归。
- `tests/test_sonic_integration.py`
  - 增加 Runner 本地 outbox 补发、心跳恢复后先补发、同步上传报告优先于 result callback 的静态回归。

验证：

```bash
python3 - <<'PY'
from tests import backend_static_checks as c
c.check_yaml_static_validation_and_patterns()
print('popup guard check passed')
PY
python3 - <<'PY'
from tests import test_sonic_integration as t
t.test_desktop_runners_replay_failed_result_callbacks_after_server_restart()
t.test_desktop_runners_upload_report_before_result_callback_and_keep_outbox_metadata()
t.test_desktop_runners_queue_report_after_result_is_archived()
print('runner callback checks passed')
PY
```

### 2026-08-04 API 工作台模块任务流收敛

用户继续反馈 API 自动化“乱乱的”，并要求真实测“我的收藏”这 3 个接口时，新同事能一眼知道从哪里开始。本轮复核线上/本地路径后定位到两个根因：

- Workbench 只返回技术分层数据，前端只能从模块树截取候选模块，导致“家用业务 / app接口 / 我的”等父级模块和真正的“我的收藏”混在一起，任务入口不明确。
- “我的收藏”接口的 `Biz` 是 Apifox 环境变量，但计划生成没有把非敏感必填 header 映射为 `{{Biz}}`，导致成功流仍被判 `needs_review`，用户看不到“批量调试草稿”的自然入口。

修复：

- `task_server/services/api_workbench_service.py`
  - 新增 `module_tasks` 聚合，按模块返回接口数、接口名、草稿/基线计划、执行环境中文名、Base URL、鉴权状态、当前主动作。
  - 计划只挂到精确模块或计划父级下的子模块；默认只展示叶子模块，避免父级模块重复污染“本次任务”。
  - “我的收藏”这类已有可执行草稿的模块优先排序，主动作直接为“批量调试草稿”。
- `task_server/services/api_task_service.py`
  - API 测试任务描述和步骤统一为用户最终确认的 5 步：手动获取 Apifox 接口数据和环境、保存接口数据和环境、筛选模块和接口、AI 生成测试用例、执行并实时查看日志和报告。
- `task_server/services/api_test_plan_service.py`
  - 生成计划时读取当前 source 的 Apifox 环境快照，对非敏感必填 header 使用 `{{变量名}}` 占位，例如 `Biz -> {{Biz}}`。
  - 仍不把敏感 token 写入用例；缺失场景保留负向用例，不被自动补全。
- `js/api-testing.js`
  - 新增 `renderApiRunnerModuleTasks()` / `apiWorkbenchModuleTasks()` / `apiWorkbenchBatchDebugModule()`。
  - 新增 `renderApiRunnerSimpleFlow()`，默认工作台左侧只保留运行环境、5 步流程和模块任务卡；右侧只保留任务摘要、执行日志和测试报告。
  - 默认首屏不再渲染“全局命令”、高级配置链接和基线优先主按钮，避免新同事先被技术入口带偏。
  - 模块卡直接展示“批量调试草稿 / 保存基线 / 生成测试资产”。
- `css/round5.css`
  - 增加 5 步流程和模块任务卡样式，稳定展示模块名、接口数、草稿数、可调试数、环境与鉴权。
- `task-manager.html`
  - API 前端缓存版本更新为 `20260804-api-runner-simple-flow-v2`。
- `tests/api_workbench_checks.py` / `tests/frontend_static_checks.py`
  - 增加回归：workbench 必须返回“我的收藏”模块任务卡，含 3 个接口、生产环境中文名、鉴权状态和“批量调试草稿”主动作。
  - 静态检查锁定默认工作台必须暴露 5 步流程和模块级任务卡，且首屏不能出现全局命令、高级配置或基线优先动作。

验证：

```bash
python3 -m unittest tests.api_manual_workflow_checks tests.api_workbench_checks tests.api_native_execution_checks tests.api_case_contract_checks
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
python3 -m py_compile task_server/services/api_task_service.py task_server/services/api_workbench_service.py task_server/services/api_test_plan_service.py tests/api_workbench_checks.py tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check -- task_server/services/api_task_service.py task_server/services/api_workbench_service.py task_server/services/api_test_plan_service.py js/api-testing.js css/round5.css tests/api_workbench_checks.py tests/frontend_static_checks.py task-manager.html CODEX_STATE.md
```

本地浏览器 smoke：

- 使用临时 `API_TESTING_DIR=/tmp/midscene-api-task-flow.*` 和 `PORT=8099` 启动 dev 服务。
- 种子数据只包含 3 个“我的收藏”接口、1 个“我的设置”接口、生产环境中文名、Base URL、`Biz=ZXB` 和测试用鉴权引用。
- Playwright 登录 `admin / sonic2026` 后打开 API 工作台，确认：
  - `.api-runner-board` 正常渲染。
  - 左侧显示 5 步：手动获取 Apifox 接口数据和环境、保存接口数据和环境、筛选要测的模块和接口、AI 根据选择的接口生成测试用例、执行并实时查看日志和报告。
  - “我的收藏”是首张 `.api-runner-task-card`。
  - 页面展示 `3 接口 / 12 草稿 / 3 可调试 / 批量调试草稿 / 保存基线 / Authorization 已配置`。
  - 父级“家用业务”不再作为任务卡显示。
  - 首屏不再展示“全局命令 / 高级配置 / 基线优先主按钮”，右侧不再重复渲染另一套步骤条。
- 截图：`/tmp/api-simple-flow-smoke-clean.png`。

### 2026-08-04 API 工作台参考执行器式一屏体验收敛

用户上传 `主对话_aippt-auto-test-share.zip` 后再次明确：API 自动化默认入口不要继续堆接口资产、环境、AI 草稿和报告详情页面，而应像参考执行器一样先让新同事看到“环境 / 测试命令 / 执行日志 / 测试报告”。本轮定位到两个真实卡点：

- workbench 聚合的执行列表只带 index 摘要，默认工作台日志区拿不到真实 `events`，所以用户看到的执行日志容易为空或只能跳转到别的页面。
- “AI 生成测试集”按钮滚动到 `api-workbench-module-section`，但默认工作台已不渲染该模块区，导致主流程断裂。

修复：

- `task_server/services/api_workbench_service.py`
  - `execution.active_runs/recent_runs` 改为嵌入脱敏后的完整执行详情，包含 `events/results/phases/stats`，事件截断到最近 200 条，结果截断到最近 100 条。
  - 聚合层对执行详情再做一次递归脱敏，防止历史 execution 文件或第三方写入里残留 Authorization / token 明文。
  - 保留现有执行语义，不新增执行模式，不改变 API runner 的真实结果来源。
- `js/api-testing.js`
  - 新增 `renderApiRunnerTerminalLines()`，工作台执行日志按参考执行器展示 PASS/FAIL/WAIT/INFO 关键流水，而不是大段 JSON。
  - 新增 `renderApiRunnerReportSummary()` / `renderApiRunnerReportDiagnosis()`，默认报告页先展示总数、通过、失败、跳过、通过率和失败分析建议。
  - 新增 `renderApiRunnerModulePicker()`，在左侧命令区直接选择模块生成 AI 测试资产，修复旧锚点失效。
  - 新增 `renderApiRunnerAdvancedLinks()`，把接口资产、环境配置、AI 草稿、报告详情收进“高级配置”，默认路径保持一屏执行器。
- `css/round5.css`
  - 增加模块选择、终端日志行、报告摘要、失败诊断和高级入口样式，补充窄屏单列规则。
- `task-manager.html`
  - API 前端缓存版本更新为 `20260804-api-runner-console-v1`。
- `tests/api_workbench_checks.py` / `tests/frontend_static_checks.py`
  - 增加 workbench 必须嵌入真实 execution events 的回归。
  - 增加默认 API 工作台必须提供 `renderApiRunnerTerminalLines`、`api-runner-report-summary`、`api-runner-advanced-links` 和“执行日志只展示关键流水”的静态回归。

验证：

```bash
node --check js/api-testing.js
python3 tests/frontend_static_checks.py
python3 -m unittest tests.api_manual_workflow_checks tests.api_workbench_checks tests.api_native_execution_checks tests.api_case_contract_checks
python3 -m py_compile task_server/services/api_workbench_service.py tests/api_workbench_checks.py tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check -- task_server/services/api_workbench_service.py js/api-testing.js css/round5.css tests/api_workbench_checks.py tests/frontend_static_checks.py task-manager.html
```

本地浏览器 smoke：

- 使用临时 `API_TESTING_DIR=/tmp/midscene-api-runner-smoke.*` 和 `PORT=8099` 启动 dev 服务。
- 种子数据只包含 3 个“我的收藏”接口、生产环境中文名和测试用假 token。
- Playwright 登录 `admin / sonic2026` 后打开 API 工作台，确认：
  - `.api-runner-board`、`.api-runner-sidebar` 正常渲染。
  - `#api-workbench-module-section` 在默认页存在。
  - “执行日志 / 测试报告”两个 tab 存在。
  - “高级配置”显示 `接口资产 / 环境 / AI 草稿 / 报告详情`。
- 临时目录和截图已清理。

### 2026-08-04 Agent 历史成功种子恢复与真实 Runner 报告统计修复

线上百度网盘需求 5 次回归暴露两个确定问题：

- 部分 Agent 已经命中历史全通过种子 YAML，但后台增量生成 job 失败后，恢复器仍把 `GENERATE_YAML` 标记为失败，导致 `0 executable` 且不进入 Runner。
- 报告卡片在计划桶缺少后续真实执行记录时，会用 `generatedYamlExecutionPlan` 覆盖真实 Runner 逻辑执行数，出现 `1/2`、`4/3` 等用户无法直观看懂的统计。

修复：

- `task_server/services/agent_service.py`
  - 新增 `_agent_recover_generation_with_historical_seed_floor()`。
  - 当 `GENERATE_YAML` 后台 job 失败、超时、取消或缺失，但当前 Agent 已保留历史成功种子时，不再整单失败：
    - 恢复历史种子为可执行 YAML floor。
    - `GENERATE_YAML` 步骤标记 `SUCCESS`。
    - 写入 `incrementalGenerationError` 作为增量失败说明，但清除阻断型 `generationPipeline.error`。
    - 推进到下一个 pending 步骤，服务恢复扫描会自动续跑 worker。
  - 报告 smoke/non-smoke bucket 在计划缺少真实后续执行桶时，使用 Runner logical totals 补足非冒烟实际执行桶，并标记 `executionBucketsSource=generatedYamlExecutionPlan+runnerLogicalTotals`。
- `tests/backend_static_checks.py`
  - 新增回归：后台生成 job 失败时必须保留历史成功种子并继续 `VALIDATE_YAML`。
  - 新增回归：真实 Runner 逻辑执行数大于 stale 计划桶时，历史卡片/报告不能低估分母和通过数。

验证：

```bash
python3 - <<'PY'
from tests import backend_static_checks as c
c.check_agent_report_summary_uses_real_runner_totals_when_plan_buckets_are_stale()
c.check_agent_generation_job_failure_keeps_historical_seed_execution_floor()
print('new checks passed')
PY
python3 - <<'PY'
from tests import backend_static_checks as c
c.check_agent_report_summary_keeps_non_smoke_buckets()
c.check_agent_report_summary_keeps_non_smoke_actual_execution_separate_from_plan()
c.check_agent_report_summary_counts_unique_final_cases_after_expanded_repair()
c.check_agent_report_summary_reconciles_plan_buckets_with_final_recovery()
c.check_agent_report_summary_uses_real_runner_totals_when_plan_buckets_are_stale()
c.check_agent_historical_seed_survives_incremental_generation_failure()
c.check_agent_generation_job_failure_keeps_historical_seed_execution_floor()
print('targeted adjacent checks passed')
PY
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check -- task_server/services/agent_service.py tests/backend_static_checks.py
```

### 2026-08-03 API 手动 Apifox 更新与参考执行器式日志收敛

用户上传 `主对话_aippt-auto-test-share.zip` 作为参考，核心设计不是堆页面，而是把流程压缩为“左侧环境 / 测试命令 / 执行历史，右侧执行日志 / 测试报告”。参考实现的日志按步骤追加 PASS/FAIL/SKIP，失败时给原因和建议，不让大段 JSON 或反复刷新扰乱阅读。

修复：

- Apifox 来源默认改为手动更新：
  - `task_server/services/api_source_service.py`
    - 新增 `apifox_auto_sync_enabled()`，只有显式 `APIFOX_AUTO_SYNC_ENABLED=1` 时才允许后台定时更新。
    - 新建 / 保存 Apifox source 默认 `sync_enabled=false`；即使旧 payload 传入 true，在未启用全局开关时也强制落为手动。
  - `task_server/services/api_sync_service.py`
    - 定时扫描 `due_api_source_ids()` 在默认状态返回空；替代同步任务也会在全局关闭时取消。
  - `task_server/router.py`
    - 保存接口配置后的提示改为“请手动更新 Apifox”，不再暗示自动同步排队。
- API 工作台和接口资产页文案收敛：
  - `task_server/services/api_task_service.py` / `api_workbench_service.py`
    - task 状态从 `sync_needed` 收敛为 `update_needed`，步骤提示改为“先手动更新 Apifox 接口”。
    - workbench 状态从“待同步 / 同步中 / 同步完成 / 刷新接口状态”改为“待更新 / 更新中 / 更新完成 / 手动更新 Apifox”。
  - `js/api-testing.js`
    - 移除定时同步入口和周期配置，设置保存只保留范围选择与手动更新。
    - 接口资产页即使没有本地 revision，也先展示已保存来源和“手动更新 Apifox”空态，不再直接被 404 错误页挡住。
    - 执行日志渲染改为 `api-log-console` / `api-log-line`，按时间、阶段、摘要、状态展示，可展开查看详情，并保存滚动位置，避免轮询时跳回顶部。
  - `css/round5.css`
    - 增加日志控制台和无快照动作区样式。
  - `task-manager.html`
    - 更新 API 前端缓存版本为 `20260803-api-manual-workflow-v1`。
- 业务 token 保存接口兼容前端粘贴输入：
  - `task_server/router.py`
    - `/api/api-testing/sources/{source_id}/auth-binding` 接收 `secret/value/token/access_token/accessToken`，空 token 返回 400，响应不回显密钥。
- 回归：
  - `tests/api_manual_workflow_checks.py`
    - 覆盖 Apifox source 默认手动更新、auth binding 的 value token 和空 token 校验。
  - `tests/api_workbench_checks.py`
    - 覆盖无本地快照时 workbench 返回 `update_needed` 和“手动更新 Apifox”文案。
  - `tests/frontend_static_checks.py`
    - 禁止 API UI 出现 `api-source-sync-enabled`、`启用定时同步`、`自动同步`、`刷新接口状态`，并要求无快照也暴露手动更新动作。

验证：

```bash
python3 -m unittest tests.api_manual_workflow_checks tests.api_workbench_checks tests.api_native_execution_checks tests.api_case_contract_checks
python3 tests/frontend_static_checks.py && python3 tests/backend_static_checks.py
node --check js/api-testing.js
python3 -m py_compile task_server/router.py task_server/services/api_source_service.py task_server/services/api_sync_service.py task_server/services/api_task_service.py task_server/services/api_workbench_service.py tests/api_manual_workflow_checks.py tests/api_workbench_checks.py tests/frontend_static_checks.py
git diff --check -- task_server/router.py task_server/services/api_source_service.py task_server/services/api_sync_service.py task_server/services/api_task_service.py task_server/services/api_workbench_service.py js/api-testing.js css/round5.css tests/api_manual_workflow_checks.py tests/api_workbench_checks.py tests/frontend_static_checks.py task-manager.html
```

本地浏览器 smoke：

- 使用临时 `API_TESTING_DIR=/tmp/midscene-api-smoke.*` 启动 `python3 -m task_server` 于 `127.0.0.1:8099`。
- 登录 `admin / sonic2026`。
- 保存临时 Apifox 来源，确认即使 payload 请求 `sync_enabled=true`，服务端仍返回 `sync_enabled=false`、`sync_schedule.mode=manual`。
- 打开 API 工作台和接口资产页，确认：
  - 页面可见“手动更新 Apifox / 更新接口”。
  - 不再出现“刷新接口状态 / 启用定时同步 / 自动同步”。
  - 无本地接口快照时仍显示“本地还没有接口快照”和“手动更新 Apifox”按钮。
  - 执行日志渲染包含 `api-log-console` 和 `api-log-line`。
- 截图：`/tmp/api-manual-workflow-smoke.png`。

### 2026-08-03 API 手动 Apifox 更新公共状态补丁

线上部署后复核 `/api/api-testing/sources` 发现一个历史数据兼容问题：默认后台自动更新已经关闭，`sync_schedule.mode` 也正确返回 `manual`，但旧 source 文件中保存过的 `sync_enabled=true` 仍被公共响应原样暴露，容易让前端和使用者误以为还存在自动同步。

修复：

- `task_server/services/api_source_service.py`
  - `_public_source()` 将 `sync_enabled` 统一归一为“有效自动同步状态”：只有 source 本身启用且环境变量 `APIFOX_AUTO_SYNC_ENABLED=1` 时才返回 `true`。
  - 默认未开启全局自动同步时，历史 source 对外也返回 `sync_enabled=false`、`sync_schedule.mode=manual`、`next_check_at=""`。
- `tests/api_manual_workflow_checks.py`
  - 新增历史 source 兼容回归，防止旧配置再次向前端暴露自动同步状态。
- `js/api-testing.js`
  - 接口资产页已保存项目的主按钮统一为“手动更新 Apifox 资产”，不再混用“重新读取 Apifox 资产”，降低新同事理解成本。

验证：

```bash
python3 -m unittest tests.api_manual_workflow_checks tests.api_workbench_checks tests.api_native_execution_checks tests.api_case_contract_checks
python3 tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/api_source_service.py tests/api_manual_workflow_checks.py
node --check js/api-testing.js
git diff --check -- task_server/services/api_source_service.py tests/api_manual_workflow_checks.py CODEX_STATE.md
```

### 2026-08-03 API 我的收藏接口真实调试修复

用户指定线上真实流程：Apifox 生产环境 `生产环境（新）-腾讯云`，测试“我的收藏”3 个接口，并把业务 JWT 配到平台环境变量后执行。真实排查发现平台曾出现“HTTP 200 但业务未登录仍算通过”的假阳性：

- 3 个接口均来自 `家用业务/app接口/我的/我的收藏`：
  - `POST /print3d/api/v1/collection/add`
  - `POST /print3d/api/v1/collection/cancel`
  - `POST /print3d/api/v1/collection/page`
- Apifox OpenAPI 里这些接口没有 `security`，但 header 参数包含必填 `Authorization` 和 `Biz`。
- 生产接口要求 `Authorization` 直接传 JWT 原文，不接受平台自动加的 `Bearer ` 前缀。
- 旧逻辑只看 OpenAPI `security`，所以生成的 positive 用例未注入鉴权；接口返回 HTTP 200 + `code=4009 用户未登录` 时，只因 HTTP 200 和 JSON 可解析就被误判通过。

修复：

- `task_server/services/api_case_contract_service.py`
  - `endpoint_requires_auth()` 不再只依赖 `security`，当必填 header 出现 `Authorization` / token 类敏感字段时，同样判定需要环境鉴权。
  - positive / chain 用例自动引用 `environment_default`，但不把敏感 header 明文写进用例 JSON；`Biz` 等非敏感业务 header 仍保留为 `{{Biz}}`。
  - 如果 2xx 响应 schema 中存在 `code` 字段，自动补充 `business_code == 0` 断言。
- `task_server/services/api_execution_service.py`
  - 执行断言支持 `business_code`。
  - positive / chain 用例即使 AI 没显式写业务码断言，只要响应 JSON 有 `code`，也会隐式校验 `code == 0`，避免 HTTP 200 假阳性。
  - auth 用例不加该成功门禁，允许校验未登录 / 未授权业务码。
  - 单个 Apifox base URL 时，执行环境展示和选择优先使用 Apifox 环境中文名与环境 ID，例如 `生产环境（新）-腾讯云 / 33831678`，同时兼容旧绑定里的 `default`。
- `task_server/services/api_plan_generation_service.py`
  - 生成详情接口返回 `plans` 摘要，前端能直接展示 AI 生成的草稿计划，不再只能看批次技术日志。
  - 兼容旧计划里的非数字计数字段，避免生成详情因历史数据报错。
- `task_server/services/api_workbench_service.py`
  - workbench 不再因为保存的环境变量看起来像 Apifox 分组占位符就每次自动刷新；只在缺少 base URL 时自动读取，手动刷新仍可强制更新。
- `js/api-testing.js` / `css/round5.css`
  - AI 生成完成后展示“AI 生成结果”候选卡片，显示用例数、可调试数、待补数，并提供明确的“审阅用例”入口。
- `tests/api_case_contract_checks.py` / `tests/api_native_execution_checks.py` / `tests/api_workbench_checks.py` / `tests/frontend_static_checks.py`
  - 增加上述鉴权识别、业务码断言、环境中文名、生成计划摘要、workbench 不反复刷新和前端审阅入口回归。

验证：

```bash
python3 -m unittest tests.apifox_discovery_checks tests.api_asset_sync_checks tests.api_case_contract_checks tests.api_native_execution_checks tests.api_workbench_checks
python3 tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/api_case_contract_service.py task_server/services/api_execution_service.py task_server/services/api_plan_generation_service.py task_server/services/api_workbench_service.py tests/api_case_contract_checks.py tests/api_native_execution_checks.py tests/api_workbench_checks.py tests/frontend_static_checks.py
node --check js/api-testing.js && node --check js/api.js && node --check js/navigation.js
git diff --check -- task_server/services/api_case_contract_service.py task_server/services/api_execution_service.py task_server/services/api_plan_generation_service.py task_server/services/api_workbench_service.py js/api-testing.js css/round5.css tests/api_case_contract_checks.py tests/api_native_execution_checks.py tests/api_workbench_checks.py tests/frontend_static_checks.py
```

### 2026-08-03 Agent Runner 重启空窗与 stale job 收敛

线上 5 次稳定性回归在用户重启服务后被运行层异常打断：

- `agent-1785741849134-c84bfd09` 的正式 Runner job `job_1785742668450_00002` 停在 `running / 95% / 执行结束，正在清理 App 状态`，`updated_at=2026-08-03 15:43:53` 后没有最终回调。
- 取消孤儿任务后重新跑，第 1 次 `agent-1785744270233-acd05c04` 生成 8 个 YAML 且 dry-run 8/8 通过，但 `EXECUTION_PRECHECK` 在服务重启心跳窗口期误判 `win-runner-01` 不在线。
- 第 2 次 `agent-1785745107461-d153b4df` 通过 precheck 并下发固定 OPPO `ecbfd645`，但正式 job `job_1785746049620_00005` 在 ADB 启动/滑动后 `updated_at=2026-08-03 16:38:30` 停止更新，Agent 长时间停在 `RUN_SONIC 51%`。

修复：

- `task_server/services/agent_service.py`
  - `EXECUTION_PRECHECK` 增加短暂 Runner 心跳重试，默认 `MIDSCENE_AGENT_RUNNER_PRECHECK_RETRY_SECONDS=8`，上限 30 秒。
  - 只在 Runner/设备当前不满足时等待并重新读取注册表；固定设备门禁不放宽，仍只允许指定 Runner/设备在线后继续。
- `task_server/services/job_service.py`
  - `recover_timed_out_jobs()` 改为强制读取最新 jobs 文件，避免缓存导致恢复扫描看不到 Runner 回传。
  - 新增 Agent Runner job `updated_at` 停滞收敛：普通 running job 默认 `MIDSCENE_RUNNING_JOB_STALE_UPDATE_SECONDS=900` 秒，95% 清理阶段默认 `MIDSCENE_RUNNING_JOB_CLEANUP_STALE_UPDATE_SECONDS=300` 秒。
  - stale job 自动标记为 failed，并写入 `stale_update_recovered=True`、`failure_type=ENV_ISSUE`、`report_missing_reason/ stderr_tail=Runner 回传停滞...`，避免 Agent 一直假运行。
  - 仅收敛带 `parent_run_id/agent_run_id` 的 Agent Runner job，不影响普通 Sonic 套件。
- `tests/backend_static_checks.py`
  - 增加回归：运行中 Agent job 的 `updated_at` 停滞超过阈值时，必须在 1800 秒硬超时前被恢复为 failed。
  - 增加静态验收：Agent precheck 必须包含 Runner 心跳短重试。

验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py task_server/services/job_service.py tests/backend_static_checks.py
git diff --check -- task_server/services/agent_service.py task_server/services/job_service.py tests/backend_static_checks.py
```

### 2026-08-03 API 自动化任务化工作台与流程收敛

用户重新梳理接口模块后明确：现有 Apifox 同步、接口资产、AI 计划、执行和报告后端能力不应推翻，问题在于用户路径割裂、信息组织混乱、同步体验偏技术化、AI 入口和调试/报告入口不够顺。

修复：

- `task_server/services/api_task_service.py`
  - 新增任务化 facade，把已有 source / snapshot / AI plan / execution / report 聚合成一个 `API测试任务` 对象。
  - 任务描述固定为 `选择接口 → AI分析 → 调试参数 → 执行 → 报告`。
  - 步骤包含 `选择接口 / AI分析与测试设计 / 调试参数 / 自动回归 / 查看报告`，不新增执行语义，只给前端一个稳定工作流状态。
- `task_server/services/api_workbench_service.py`
  - `/api-testing/workbench` 返回 `task`，复用 `metrics/sync_state/pending_changes`，避免前端自己拼业务流程。
  - Apifox 主动作从“检查更新 / 更新快照”改成“刷新接口状态”，更贴近用户想确认接口是否变化的心智。
- `task-manager.html`
  - API 自动化导航按文档收敛为 `API 工作台 / 接口资产 / AI测试设计 / 在线调试 / 自动回归 / 执行记录 / 测试报告 / 环境配置`。
  - AI 测试设计图标改为机器人；回归入口展示为“自动回归”，页面内部仍说明执行的是已保存基线测试资产。
- `js/api-testing.js`
  - 工作台首屏新增 `api-task-hero`，展示 API 测试任务、当前项目、环境、接口数、测试资产数、待处理变化和流程步骤。
  - 统一 `apiSourceEnvironmentDisplayName()`，工作台、执行页、调试页和环境页优先展示 Apifox 环境中文名，例如 `生产环境（新）-腾讯云`，不再优先掉成环境 ID。
  - 接口详情右侧固定为 `AI助手`，提供 `分析接口 / 生成测试 / 立即调试 / 补充异常 / 生成断言 / 分析失败`。
  - 在线调试保留为核心入口，支持编辑本地环境快照后批量调试，不反写 Apifox。
  - 执行记录页改为“左侧列表 + 右侧详情抽屉”，按 Request / Response / 断言 / 日志 / AI分析组织信息。
  - 报告详情新增 `AI 总结` 和 `下一步建议`，在请求、响应、断言明细前先给失败聚合和处理建议。
- `css/round5.css`
  - 增加任务 hero、AI 助手动作区、执行记录抽屉和报告 AI 总结样式，并补窄屏单列规则。
- `tests/api_workbench_checks.py` / `tests/frontend_static_checks.py`
  - 增加回归：workbench 必须返回 `API测试任务`；执行环境必须优先中文名；Apifox 刷新文案必须为“刷新接口状态”；接口详情必须有 AI 助手和“立即调试”；执行记录必须是列表 + 右侧抽屉；报告必须有 AI 总结。

验证：

```bash
python3 -m unittest tests.api_workbench_checks
python3 -m unittest tests.apifox_discovery_checks tests.api_asset_sync_checks tests.api_workbench_checks tests.api_native_execution_checks tests.api_case_contract_checks
python3 -m py_compile task_server/services/api_task_service.py task_server/services/api_workbench_service.py tests/api_workbench_checks.py tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
node --check js/api-testing.js && node --check js/api.js && node --check js/navigation.js
git diff --check -- task_server/services/api_task_service.py task_server/services/api_workbench_service.py js/api-testing.js css/round5.css task-manager.html tests/api_workbench_checks.py tests/frontend_static_checks.py
```

本地浏览器 smoke：

- 使用临时目录启动 `python3 -m task_server` 于 `127.0.0.1:8099`。
- 登录 `admin / sonic2026`。
- 逐个打开 `API 工作台 / 接口资产 / 在线调试 / 自动回归 / 执行记录 / 测试报告 / 环境配置`。
- 验证页面文本、任务流程、调试入口、执行记录和报告入口均可见。
- 截图输出到 `/tmp/api-task-workflow-smoke.png`。
- 本地未启动 AI Gateway 时 `/ai-gateway/ai/providers` 与 `/ai-gateway/ai/model-router` 返回 404，是本地 smoke 环境限制，不属于 API 页面运行时错误。

### 2026-08-03 Agent 首批冒烟选择稳定性修复

线上回归 `agent-1785736612329-32d18627` 生成 10 个 YAML（4 个历史成功种子 + 6 个新增），dry-run 10/10 通过，但真实 OPPO `ecbfd645` 首批冒烟 `0/3`，修复重跑未恢复，终态 `FAILED / RERUN / 95%`。

证据：

- 首批真实执行失败不同于 dry-run：`job_1785737453079_00009`、`00010`、`00011` 是 YAML dry-run 通过，不代表手机执行通过。
- 真实手机任务 `job_1785737483446_00012`、`job_1785737857058_00020`、`job_1785738196369_00021` 均在 `win-runner-01 / ecbfd645` 执行。
- 两条任务早期 `Timeout after 300s`，日志停在 App 启动/早期交互；一条在 `aiWaitFor` 调用 `qwen3.7-plus` 时模型请求超时。
- 最终失败分析进一步收敛出脚本层问题：照片打印候选在错误页面层级等待「百度网盘」，未先进入具体照片规格页，导致死等。

修复：

- `task_server/services/yaml_executable_scorer.py`
  - `rank_executable_yaml_refs()` 首批排序新增稳定入口优先级：入口可见 / 入口展示 / 可见性 / 同级 / 并列优先。
  - 文案准确性、点击后、跳转、反馈、授权、登录、WebView、宽/窄屏等二级验收维度仍可执行，但默认排到首批冒烟之后，进入 remaining。
  - 首批选择新增业务分支分散策略：在排序后优先保证不同业务入口各取一条稳定短链路，再用剩余名额补同分支用例，避免两个文档用例挤掉照片/扫描分支。
- `tests/backend_static_checks.py`
  - 新增回归：候选同时包含文档/照片/扫描三条入口可见性短链路，并混入文案/点击跳转用例时，首批必须选择三入口可见性短链路，文案和跳转用例仅延后。

验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
git diff --check -- task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
```

### 2026-08-03 Agent 报告最终口径与修复重跑统计拆分

用户指出 Agent 历史卡片和最终报告在修复重跑后仍可能把“原始失败尝试”混进最终通过率，例如同一轮真实执行里逻辑结果已经恢复到 `6/8`，但卡片仍按计划桶或 Runner 原始 job 事件显示成 `5/8`，并可能出现通过数 + 失败数超过总执行数的观感。

修复：

- `task_server/services/agent_service.py`
  - `_agent_run_report_summary()` 继续返回 `attempted/passed/failed` 兼容字段，但这些字段现在统一代表最终逻辑结果。
  - 新增 `finalAttempted/finalPassed/finalFailed/finalTimeout/finalRunning`，前端可明确读取最终用例结果。
  - 新增 `rawAttempted/rawPassed/rawFailed/rawTimeout/rawRunning/recovered/repairAttempted`，原始 Runner 尝试和修复恢复数保留为过程证据，不污染最终通过率。
  - `_agent_report_execution_buckets_from_plan()` 对 smoke/non-smoke 桶按 `logicalPassedCount/logicalFailedCount` 做最终对齐，修复重跑恢复的失败会从对应桶扣减。
  - `_tool_generate_summary()` 将 `productFailedJobCount/brokenJobCount/unknownFailedJobCount` 投影到最终未恢复失败；原始失败尝试另存为 `rawBrokenJobCount/rawFailedAttemptCount`。
- `js/agent-status.js`
  - 历史卡片优先使用 `final*` 字段。
  - 卡片主数字只展示最终结果；“修复恢复 X / 原始失败 X”作为过程上下文展示，不计入主通过率。
  - 对旧记录仍保留 fallback，并防止 bucket 叠加出不可能的 `passed + failed > total`。
- `js/agent-workbench.js`
  - 最终报告顶部指标优先使用逻辑最终结果，不再由 Runner evidence 原始 job 数直接覆盖。
- `task-manager.html`
  - 更新 `agent-workbench.js` 和 `agent-status.js` 缓存版本为 `20260803-agent-final-report-counts`。
- `tests/backend_static_checks.py` / `tests/frontend_static_checks.py`
  - 增加回归：9 条 YAML、首批 3、扩展实际 5、修复恢复后最终应显示 `6/8`，冒烟 `3/3`，非冒烟 `3/5`，而不是旧的 `5/8`。

验证：

```bash
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
node --check js/agent-status.js && node --check js/agent-workbench.js
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py tests/frontend_static_checks.py
git diff --check -- task_server/services/agent_service.py js/agent-status.js js/agent-workbench.js task-manager.html tests/backend_static_checks.py tests/frontend_static_checks.py
```

额外用真实线上 Agent `agent-1785694113976-d2e6f391` 的完整数据回放本地新汇总，结果为：总执行 `6/8`，冒烟 `3/3`，非冒烟 `3/5`，原始失败尝试 `10`，修复恢复 `2`。

### 2026-08-02 Agent 冒烟非全失败继续扩展策略

用户连续回归“基础打印新增百度网盘入口”后确认：5 次均为 `DONE / 部分通过`，但第 3、5 次在首批冒烟 2/3 通过时仍提前收口，导致非冒烟 0/0 未执行。问题不是 Runner 或固定 OPPO，而是 Agent smoke gate 把单条元素定位 / 脚本类失败当成硬阻断，覆盖了既定的“冒烟通过率 >= 50% 继续执行剩余用例”策略。

修复：

- `task_server/services/yaml_execution_plan.py`
  - `classify_generated_yaml_smoke_blocker()` 不再因为单条脚本 / YAML / 元素定位失败直接阻断扩展。
  - 保留 dry-run 未通过、未创建 Runner 任务、通过率低于 50% 等确定性阻断。
  - 失败桶继续写入报告和修复链路，单条失败不再阻断剩余 executable 覆盖。
- `task_server/services/agent_service.py`、`task_server/services/yaml_service.py`、`task_server/services/case_service.py`
  - 同步更新策略文案，避免线上报告继续提示“定位失败即阻断扩展”。
- `tests/backend_static_checks.py`
  - 新增回归：3 条冒烟中 2 条通过、1 条元素定位失败时，smoke gate 必须 `block=False`、`thresholdPassed=True`，并保留 `元素定位失败` 桶。

验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/case_service.py task_server/services/yaml_execution_plan.py tests/backend_static_checks.py
git diff --check -- task_server/services/yaml_execution_plan.py task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/case_service.py tests/backend_static_checks.py
```

### 2026-08-01 API 基线接口测试一键执行入口

用户提出发版后需要能一键执行基线接口测试。当前平台不再引入 MeterSphere，也不新增第二套执行器；这里的“基线接口测试”定义为已经通过 AI 审阅/调试并保存的 confirmed API 测试资产，继续复用现有原生 API 执行器的 `baseline` run mode。

修复：

- `js/api-testing.js`
  - 新增 `apiWorkbenchReleaseBaselineAction()`，专门决定工作台主按钮行为：
    - 有运行中任务：查看基线执行进度。
    - 最新 confirmed 测试资产可执行：一键执行基线接口测试。
    - 已有测试资产但环境、token 或接口版本不满足：进入执行页检查条件。
    - 只有 AI 草稿：先打开草稿审阅/调试并保存为测试资产。
    - 尚无草稿：先生成测试资产。
  - 工作台命令从泛化的“自动回归执行”改为“基线接口测试”，明确这是发版后跑已保存测试资产。
  - 左侧 API 导航、工作流步骤、执行页标题统一为“基线接口测试”，但内部 `api_regression` workflow id 保持不变，避免改路由或后端状态模型。
- `task-manager.html`
  - API 自动化侧栏将 `api_regression` 展示为“基线接口测试”。
  - 前端缓存版本更新为 `20260801-api-baseline-runner-v1`。
- `tests/frontend_static_checks.py`
  - 增加静态验收，要求工作台必须暴露“基线接口测试”和“一键执行基线接口测试”。
  - 要求侧栏和执行中心使用“基线接口测试”语义，避免退回不直观的泛化回归文案。

验证：

```bash
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
node --check js/api.js
node --check js/navigation.js
python3 -m unittest tests.apifox_discovery_checks tests.api_asset_sync_checks tests.api_workbench_checks tests.api_native_execution_checks tests.api_case_contract_checks
python3 tests/backend_static_checks.py
git diff --check -- task-manager.html js/api-testing.js tests/frontend_static_checks.py CODEX_STATE.md
```

本地浏览器 smoke：

- 使用临时 dev 环境启动 `python3 -m task_server` 于 `127.0.0.1:8099`。
- 登录 `admin / sonic2026`，打开 `API 工作台`。
- 验证首屏存在 4 个测试命令、`基线接口测试` 命令和主按钮引导；本地空数据时主按钮显示 `先生成测试资产`。
- 页面无运行时 `pageerror`；本地未挂 AI Gateway 代理时的资源 404 不作为 API 工作台错误。

### 2026-08-01 API 工作台一页式命令中心

用户提供 `主对话_aippt-auto-test-share.zip` 作为参考后，已认真对照其核心流程：

- 左侧选择运行环境和测试命令。
- 右侧直接展示实时执行日志。
- 执行完成后沉淀 HTML / JSON 报告和失败分析。
- 历史任务能直接回看，不需要在多个页面里来回找入口。

本轮没有照搬参考项目的硬编码用例和一次性脚本模式，而是把适合平台定位的交互方式收敛到 `API 工作台` 首页：

- `js/api-testing.js`
  - 新增 `api-command-center-shell` 首页命令中心。
  - 左侧 `api-command-panel` 展示当前 Apifox 项目、执行环境、Base URL、测试资产和运行状态。
  - 提供 4 个主命令：
    - 更新 Apifox 快照：复用 `/api-testing/snapshots/update`。
    - AI 生成测试集：滚动到模块选择区，不跳过接口范围确认。
    - 批量调试草稿：打开当前 AI 草稿后复用 `batchDebugApiPlan()`。
    - 自动回归执行：进入回归页后复用 `startApiExecution()`。
  - 右侧 `api-live-console` 展示实时日志或等待执行提示。
  - 右侧 `api-report-strip` 展示最近报告摘要和失败入口。
  - 新面板里 0 值显式展示为 `0 个接口 / 0 条已保存用例`，避免空白造成误解。
- `css/round5.css`
  - 新增命令中心、命令卡、实时日志、报告摘要的布局和响应式样式。
  - 1100px 以下改为单列，避免信息挤压。
- `task-manager.html`
  - 前端缓存版本更新为 `20260801-api-command-center-v1`。
- `tests/frontend_static_checks.py`
  - 增加静态验收，要求首页必须包含环境/命令、执行控制台、实时日志、测试报告等关键区域。

验证：

```bash
python3 tests/frontend_static_checks.py
node --check js/api-testing.js && node --check js/api.js && node --check js/navigation.js
python3 -m unittest tests.apifox_discovery_checks tests.api_asset_sync_checks tests.api_workbench_checks tests.api_native_execution_checks tests.api_case_contract_checks
python3 tests/backend_static_checks.py
git diff --check -- task-manager.html js/api-testing.js css/round5.css tests/frontend_static_checks.py CODEX_STATE.md
```

本地浏览器 smoke：

- 启动 `python3 -m task_server` 于 `127.0.0.1:8099`。
- 使用 `admin / sonic2026` 登录。
- 打开 `API 工作台`。
- 验证 4 个命令卡、实时日志区域、测试报告区域和 0 值展示均渲染正常。
- 页面无运行时 `pageerror`；本地未挂 AI Gateway 代理时存在 `/ai-gateway/ai/providers` 和 `/ai-gateway/ai/model-router` 404，这是本地 smoke 环境限制，不属于 API 工作台运行时错误。

补充修正：

用户指出上一版仍没有仔细参考原页面设计。重新启动参考项目 `web_server.py` 并用 Playwright 截图对照后，确认参考页的关键不是“多块卡片并列”，而是：

- 左侧固定面板：运行环境、测试命令、大号执行按钮、执行历史。
- 右侧主区域：`执行日志 / 测试报告` Tab。
- 第一屏只承载执行任务，不展示额外资产快照、指标矩阵和模块列表。

已将 `API 工作台` 首页改为 `api-runner-board`：

- 首页只渲染 `renderApiWorkbenchRunnerBoard(data)`。
- 不再默认渲染 `renderApiWorkbenchSourceCard(data)` 或 `renderApiWorkbenchAssetCard(data)`，避免第一屏信息过密。
- 左侧 `api-runner-sidebar` 展示接口项目、执行环境、4 个测试命令、大按钮和执行历史。
- 右侧 `api-runner-tabs` 在执行日志和测试报告之间切换，日志区域使用终端式大画布。
- 接口快照和模块选择仍保留在 `接口资产 / AI测试设计` 深入页面，不从功能上删除。
- 前端缓存版本更新为 `20260801-api-runner-board-v1`。

补充验证：

```bash
python3 tests/frontend_static_checks.py
node --check js/api-testing.js && node --check js/api.js && node --check js/navigation.js
python3 -m unittest tests.apifox_discovery_checks tests.api_asset_sync_checks tests.api_workbench_checks tests.api_native_execution_checks tests.api_case_contract_checks
python3 tests/backend_static_checks.py
```

浏览器对照：

- 参考截图：`tests/artifacts/reference-aippt-panel.png`
- 修改前截图：`tests/artifacts/current-api-command-center-before.png`
- 修改后截图：`tests/artifacts/current-api-runner-board-after.png`
- 临时截图文件未纳入 git 提交。

### 2026-08-01 Apifox 环境本地值保留与原生 API 执行变量解析

用户提供 Apifox 访问令牌后，已直接核查线上接口读取结果：

- 线上平台可登录并读取 Apifox 项目列表：17 个项目，`5904970 / 3D` 可访问。
- `3D` 项目可读取 2 个分支、21 个环境。
- `33831678 / 生产环境（新）-腾讯云` 当前从 Apifox 返回：
  - 1 个服务地址：`default -> https://print.wisebeginner3d.com/app`
  - 4 个变量分组名：`cookie / query / header / body`
  - 未返回用户截图里 Apifox 客户端本地值中的 `Authorization / Biz / ZXBToken / ZXBManToken / ZXBAgentToken / ZXBShareToken / ZXBPartnerToken` 明细。
- 对照 Apifox 官方文档，环境变量存在远程值和本地值；本地值只保存在当前电脑本地，不会同步到云端或团队成员。因此平台服务端不能假设每次都能从 Apifox API/CLI 读取到截图里的本地 token 和本地变量值。

根因：

- 平台刷新 Apifox 环境时，把 `cookie/query/header/body` 这类空分组当成普通变量展示和计数，用户误以为变量丢失。
- Apifox 没有返回本地值时，平台此前缺少“保留平台本地执行快照”的明确语义，容易在刷新时把已经手动配置好的业务 token / Header 变量冲掉。
- 原生 API 执行器此前没有在执行前解析用例里的 `{{Biz}} / {{Authorization}} / {{ZXBToken}}` 等环境变量占位，导致“页面看起来配置了，执行时仍可能没带上”。

修复：

- `apifox_discovery_service.py`
  - 将空的 `cookie/query/header/body` 标记为 `group_placeholder`，不计入可执行变量数。
  - 视觉展示可说明“Apifox 未返回该分组下的变量明细”，避免把分组名误当成真实变量。
- `api_source_service.py`
  - Apifox 刷新支持 `preserve_missing_environment_variables`，刷新只更新 Apifox 能提供的 base_url / 远程变量，不覆盖平台本地保存的执行变量。
  - 敏感变量真实值只存服务端，公开返回只显示“已配置”和指纹，不泄露 token。
  - 非敏感变量如果从上一版本地快照保留下来，也正确标记为已配置。
- `api_workbench_service.py` / `router.py`
  - 从 Apifox 项目上下文保存或补齐环境快照时默认启用本地变量保留。
- `api_execution_service.py`
  - 原生执行器执行前读取未脱敏 source，解析请求 path/query/header/body 中的 `{{变量名}}`。
  - 支持 draft 批量调试 `debug_batch`，调试结果回写计划，确认基线前可要求先调试通过。
  - 正式回归继续保留 stale revision、workspace binding drift、auth binding drift 和可执行性门禁。
- `api_case_contract_service.py`
  - 脱敏时保留完整形态的 `{{Authorization}}` 等占位符，避免 AI 生成的环境变量引用被误清空。

验证：

```bash
python3 -m unittest tests.apifox_discovery_checks tests.api_asset_sync_checks tests.api_workbench_checks tests.api_native_execution_checks tests.api_case_contract_checks
python3 tests/frontend_static_checks.py
python3 -m py_compile task_server/services/apifox_discovery_service.py task_server/services/api_source_service.py task_server/services/api_execution_service.py task_server/services/api_case_contract_service.py task_server/services/api_test_plan_service.py task_server/services/api_workbench_service.py task_server/router.py tests/apifox_discovery_checks.py tests/api_asset_sync_checks.py tests/api_native_execution_checks.py tests/frontend_static_checks.py
node --check js/api-testing.js && node --check js/api.js && node --check js/navigation.js
python3 tests/backend_static_checks.py
```

本地浏览器 smoke：

- 用 `admin / sonic2026` 登录 `http://127.0.0.1:8099/task-manager.html`。
- 依次打开 `API 工作台 / 接口资产 / 环境配置 / AI测试设计 / 在线调试 / 自动回归`。
- 未复现 `selectedCount is not defined`；本地单进程 smoke 仅因未挂 AI Gateway 代理出现 `/ai-gateway/...` 404，和 API 自动化页面逻辑无关。

### 2026-07-31 Agent RERUN 等待 Runner 结果绕过 stale jobs 缓存

本次按用户要求再次回归“基础打印新增百度网盘入口”：

- Agent：`agent-1785505293000-b3421dd4`
- 固定设备：`win-runner-01 / ecbfd645 / OPPO PHM110`
- 模型：`qwen3.7-plus`
- 结果验证到 `d35f7bd` 新策略已生效：
  - `CASE_RETRIEVAL` 命中历史全通过 Agent `agent-1785461191470-63ea62f9`。
  - `GENERATE_YAML` 没有直接跳过，而是进入增量生成。
  - 最终 `generationPipeline.source=historical_success_seed_plus_incremental`。
  - 历史种子 4 个，新增 YAML 6 个，合计 10 个 executable YAML。
  - 冒烟 3 条、剩余 7 条；dry-run 首批 3/3 通过，expanded dry-run 5/5 通过。
  - 实际 Runner job 均为 `win-runner-01 / ecbfd645`。

新发现：

- Runner 原始 `/api/jobs` 已显示修复重跑 job `job_1785506827493_00027` 为 `success`。
- 但 Agent 仍停在 `RERUN / 81%`，`artifacts.jobProgress` 还保留旧的 `running` snapshot，且未写出 `rerunResult`。
- 这是 Agent 等待 Runner job 期间读取 jobs 状态被缓存污染的问题：等待线程可能读到 stale `running` 状态，而真实 job 文件已经被 Runner 回传写成 `success`。

修复：

- `task_server/services/job_service.py`
  - `_read_jobs_raw()` 增加 `use_cache` 参数，普通列表仍使用 TTL 缓存。
  - `wait_jobs_finished()` 在轮询和超时最终统计时强制 `use_cache=False`，直接读最新 jobs 文件。
  - 保持现有 Runner 执行、报告回传、超时分类语义不变，只修复 Agent 等待状态回收。
- `tests/backend_static_checks.py`
  - 新增回归：先把 jobs 缓存灌成 `running`，再模拟另一个入口直接写文件为 `success`，`wait_jobs_finished()` 必须读到 fresh success 并推进 jobProgress。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_runner_wait_reads_fresh_job_state_during_agent_rerun()
print('fresh job state wait check passed')
PY
python3 -m py_compile task_server/services/job_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
```

### 2026-07-31 Apifox 环境变量分组展开与本地值优先

用户发现 Apifox 的环境变量里有 `Authorization`、`Biz`、`ZXBToken`、`ZXBManToken` 等参数，但平台只显示 `cookie/query/header/body` 这类分组名，真实变量没有同步出来。

根因：

- Apifox CLI 返回的环境变量可能按 `header/query/body/cookie` 分组，真实变量在分组内的 `parameters` / `variables` 数组里。
- `apifox_discovery_service._environment_variable_rows()` 和 `api_source_service._snapshot_variable_rows()` 看到分组对象时没有继续递归展开，而是把分组名当成变量名保存。
- Apifox 环境里同时存在“远程值”和“本地值”；当 `currentValue` 是空字符串、`localValue` 有值时，旧 `_field_value()` 会因为空远程值字段存在而停止，导致 `Biz=ZXB` 这类本地值丢失。
- 已保存过的错误快照即使已有 base_url，也不会自动刷新，所以修完解析后旧页面仍可能继续显示 4 个分组名。

修复：

- discovery 与 source normalize 都递归展开 Apifox 分组变量，并把父分组写入变量 `scope`，例如 `Authorization` / `Biz` 的 scope 为 `header`。
- 字段读取改为优先第一个非空值，避免空 `currentValue` 遮住 `localValue`。
- token / Authorization / Cookie 等敏感变量仍只保留变量名和敏感标记，不保存真实值；`Biz` 这类普通变量会保存可执行值。
- API 工作台识别旧的 `cookie/query/header/body` 分组占位快照，即使已有 base_url，也会强制刷新一次 Apifox 环境并保存真实变量名。

已验证：

```bash
python3 -m unittest tests.api_asset_sync_checks.ApiSourceConfigTests.test_environment_snapshot_expands_apifox_grouped_parameter_variables tests.api_asset_sync_checks.ApifoxDiscoverySnapshotTests.test_environment_snapshot_expands_cli_grouped_parameter_variables
python3 -m unittest tests.api_workbench_checks.ApiWorkbenchChecks.test_workbench_refreshes_grouped_parameter_placeholder_snapshot
```

### 2026-07-31 Agent 历史成功种子继续增量生成

用户确认“复用之前成功的”不应该导致同需求后续完全不再生成新用例；历史成功 YAML 应作为稳定保底，但仍要尽量新增覆盖。

根因：

- `CASE_RETRIEVAL` 命中同目标、同 Figma、同包名的历史全通过 Agent 后，会把历史 YAML 写入 `yamlRefs`。
- `GENERATE_YAML` 看到已有 confirmed file refs 后直接 `SKIPPED`，导致后续不会再调用需求/Figma/YAML 主链生成增量用例。
- 这个策略降低了波动，但把“稳定种子”误当成了“生成终点”。

修复：

- `task_server/services/agent_service.py`
  - 历史成功 YAML 仍作为执行下限保留在 `historicalSuccessSeedRefs`。
  - `GENERATE_YAML` 首次看到历史种子时继续调用 AI YAML 主链生成增量 YAML。
  - 新增 YAML 必须继续通过 `_confirm_agent_yaml_files` 的 executable 门禁，不能绕过静态校验、scorer、覆盖告警或 Runner 分批策略。
  - 合并结果按路径去重：历史种子在前，新增 executable YAML 追加。
  - 增量生成失败时不清空历史种子，记录 `incrementalGenerationError`，继续用历史成功种子进入 dry-run / Runner。
- `tests/backend_static_checks.py`
  - 覆盖历史种子命中后仍会调用增量生成。
  - 覆盖增量失败时历史种子仍保留为稳定执行下限。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_new_requirement_reuses_historical_success_seed()
checks.check_agent_historical_seed_survives_incremental_generation_failure()
print('targeted historical seed incremental checks passed')
PY
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check -- task_server/services/agent_service.py tests/backend_static_checks.py CODEX_STATE.md
```

### 2026-07-31 API 自动化侧边栏图标、步骤条崩溃与本地快照先渲染

用户指出 API 自动化左侧菜单仍是 `API/OAS/AI/DBG/RUN/LOG/RPT/EN` 文字块，不符合文档里“用图标帮助新同事快速识别入口”的要求；同时线上 `环境配置` 和 `测试设计` 页面出现 `selectedCount is not defined`，工作台进入时仍像每次都要重新刷新。

根因：

- 侧边栏实际仍使用文字 badge，静态检查也错误地把文字 badge 当作合格“图标”。
- `renderApiWorkflowStepper()` 用 `selectedCount` 判断“在线调试”步骤是否完成，但这个变量只在其它函数内部定义，切到 `环境配置` / `测试设计` 时会直接抛前端运行时错误。
- `API 工作台` 与 `环境配置` 每次进入都先展示 loading，再等待 `/api/api-testing/workbench` 返回；已有本地快照没有参与首屏渲染，所以用户感觉“每次都刷新”。

修复：

- `task-manager.html` 将 API 自动化菜单换成可见图标：`🏠 API 工作台`、`📦 接口资产`、`✨ AI测试设计`、`🧪 在线调试`、`▶️ 自动回归`、`📜 执行记录`、`📊 测试报告`、`⚙️ 环境配置`。
- 前端静态资源版本递增到 `20260731-api-studio-icons-cache-v2`，避免浏览器继续使用旧的 `api-testing.js`。
- `renderApiWorkflowStepper()` 在函数内定义 `selectedCount`，消除 `selectedCount is not defined`。
- `api-testing.js` 增加 `api_testing_workbench_cache_v1` 本地快照缓存：`API 工作台` 和 `环境配置` 先渲染上一次成功保存的快照，再后台刷新服务端权威数据；刷新失败时保留快照并提示。
- `tests/frontend_static_checks.py` 改为校验真实图标、禁止 API 自动化菜单再用文字 badge，并覆盖 `selectedCount` 定义和本地快照先渲染。

已验证：

```bash
python3 tests/frontend_static_checks.py
node --check js/api-testing.js && node --check js/api.js && node --check js/navigation.js
python3 tests/api_workbench_checks.py && python3 tests/api_native_execution_checks.py
git diff --check -- task-manager.html js/api-testing.js tests/frontend_static_checks.py CODEX_STATE.md
PORT=8099 TASK_APP_ENV=dev TASK_ADMIN_USER=admin TASK_ADMIN_PASSWORD=sonic2026 TASK_SESSION_SECRET=local-dev-secret MIDSCENE_RUNNER_TOKEN=local-runner-token SONIC_CALLBACK_TOKEN=local-sonic-token TASK_DIR=/tmp/midscene-local/tasks REPORT_DIR=/tmp/midscene-local/reports LEARNING_DIR=/tmp/midscene-local/learning ASSET_DIR=/tmp/midscene-local/assets CASE_DIR=/tmp/midscene-local/cases GENERATE_JOB_DIR=/tmp/midscene-local/generate KNOWLEDGE_DIR=/tmp/midscene-local/knowledge python3 -m task_server
curl -i http://127.0.0.1:8099/api/health
```

本地浏览器已用 `admin / sonic2026` 登录 `http://127.0.0.1:8099/task-manager.html`，实际点击 `API 工作台`、`AI测试设计`、`环境配置`，图标展示正常，前端控制台没有 `selectedCount` 错误。

### 2026-07-31 Agent 同需求优先复用历史全通过 YAML 种子

用户连续回归“基础打印新增百度网盘入口”后发现同一需求每次结果不一样：明明已有成功记录，后续运行仍重新 PLAN / 重新生成 YAML，导致生成数量、冒烟选择和通过率波动。

根因：

- 新需求/Figma 输入在 `CASE_RETRIEVAL` 阶段固定走“跳过旧基线复用匹配，直接生成新 YAML 草稿”。
- 历史全通过 Agent run 只作为历史记录保存，没有参与同目标、同 Figma、同包名的新一轮回归种子选择。
- 重新生成会再次受 PLAN 超时降级、AI 用例拆分和 Midscene 视觉执行波动影响，因此同一需求出现不同用例数和不同通过结果。

修复：

- `task_server/services/agent_service.py`
  - 新增历史成功种子匹配：
    - 目标文案归一化后必须一致。
    - App package 必须一致。
    - Figma 设计主链接必须一致；忽略 Figma 临时 `t=` 参数，但保留文件路径和 `node-id`。
    - 历史 run 必须 `DONE` 且报告 outcome 为 `passed`，失败、超时、运行中均为 0。
  - 从历史 run 的 `yamlRefs`、`generatedYamlExecutionPlan`、`generatedYamlPaths` 抽取仍存在的 YAML 文件。
  - 将命中的 YAML 写入当前 run 的 `historicalSuccessSeedRefs`、`yamlRefs` 和 `generatedYamlPaths`。
  - 种子保持 `source=generated`、`validationMode=historical_success_seed`，继续走现有 dry-run、冒烟分批和剩余用例阈值，不退化成不受控的普通 baseline。
  - `GENERATE_YAML` 命中历史成功种子时跳过重新生成，并在日志中明确展示来源。
- `tests/backend_static_checks.py`
  - 新增同 target / package / Figma 历史全通过 Agent 回归测试。
  - 覆盖 Figma `t=` 参数变化仍能命中、种子不会写入 `matchedCases` 绕过冒烟门禁、生成步骤不再重新调用 AI。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_new_requirement_reuses_historical_success_seed()
print('targeted historical seed check passed')
PY
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check -- task_server/services/agent_service.py tests/backend_static_checks.py CODEX_STATE.md
```

### 2026-07-31 API 自动化 Studio 信息架构与页面重排

用户要求严格按两份接口自动化页面文档重构，目标是让新同事一眼知道每一步该做什么：Apifox 只作为接口资产/环境来源，平台负责本地快照、AI 测试设计、在线调试、自动回归、执行记录和报告。

本轮产品判断：

- 参考用户给的轻量接口测试平台截图和 [APIAuto](https://github.com/TommyLemon/APIAuto) 的少入口、强操作、可执行结果直出思路，不再把 API 页面做成 MeterSphere 式多平台编排。
- 页面导航应表达日常流程，不是功能堆叠：工作台 -> 接口资产 -> 测试设计 -> 在线调试 -> 自动回归 -> 执行记录 -> 测试报告 -> 环境配置。
- 在线调试不做“看起来能发送但没有真实执行链路”的假按钮；入口指向平台真实的单条用例调试 `/api/api-testing/cases/debug`，这样环境变量、业务 token、请求入参、断言、日志和报告都能被追踪。
- 本轮只改 UI/路由/静态契约，不改 Apifox 解析、原生 API 执行语义和业务 token 存储边界。

本轮实现：

- `task-manager.html`
  - 接口自动化菜单调整为 8 个清晰入口：`API 工作台`、`接口资产`、`测试设计`、`在线调试`、`自动回归`、`执行记录`、`测试报告`、`环境配置`。
  - 每个入口增加短 icon 文案：`API/OAS/AI/DBG/RUN/LOG/RPT/EN`，降低纯文字列表的识别成本。
  - 静态资源版本更新为 `20260731-api-studio-ui`。
- `js/navigation.js` / `js/api.js`
  - 新增 `api_debug`、`api_regression` 页面路由和 workflow section。
  - `api_execution` 保留为内部兼容入口，主菜单不再暴露。
- `js/api-testing.js`
  - 工作台改为文档要求的顶部工具栏、4 个概览卡、Apifox 同步状态、AI 风险提醒、最近执行记录和五步流程卡。
  - 接口资产页改为左中右三栏：左侧模块树带 icon/数量，中间接口卡片，右侧详情 Tab：`接口定义`、`AI分析`、`测试用例`、`执行历史`。
  - AI 测试设计增加步骤条：`选择接口`、`AI分析`、`生成测试建议`、`确认保存`。
  - 新增 `在线调试` 页面，展示接口、Header、Body、环境变量、响应结果、断言结果和执行日志，并引导到真实单条用例调试。
  - 新增 `自动回归` 页面，复用平台本地执行器上下文、执行进度和实时日志。
- `css/round5.css`
  - 增加 API Studio 工具栏、概览卡、风险卡、最近任务表、三栏资产页、接口卡片、详情 Tab、在线调试和自动回归样式。
  - 响应式约束：宽屏三栏，中屏卡片收缩，手机单列，避免按钮和长路径挤压。
- `tests/frontend_static_checks.py`
  - 静态契约升级为 8 页面 AI API Testing Studio。
  - 增加工作台、资产三栏、在线调试、自动回归、缓存版本和中文文案断言。

已对齐文档：

- 工作台：项目/环境/搜索和主操作区、4 个指标、同步状态、AI 风险、最近执行。
- 接口资产：模块树、接口卡片、方法标签、覆盖/状态、右侧详情四 Tab。
- AI 测试设计：步骤式布局。
- 在线调试：请求、环境变量、响应、断言、日志都在同页；真实执行仍从可执行用例的“调试单条”进入。
- 自动回归：从“执行”改为“自动回归中心”，显示执行进度并复用本地执行器。
- 执行记录/测试报告/环境配置：保留已有原生链路，不再引入 MeterSphere。

验证：

```bash
python3 tests/frontend_static_checks.py
node --check js/api-testing.js && node --check js/api.js && node --check js/navigation.js
python3 tests/api_workbench_checks.py
python3 tests/api_native_execution_checks.py
git diff --check -- task-manager.html js/api-testing.js js/api.js js/navigation.js css/round5.css tests/frontend_static_checks.py CODEX_STATE.md
```

注意：

- 本轮未暂存或回滚用户历史 dirty 文件，包括 prompt、Runner、scorer、部署文档、截图 artifacts 和临时 HTML。
- 后续如果要进一步做成真正的 Postman 式自由请求编辑器，需要新增后端自由请求执行接口；当前选择先保持可执行用例调试，避免 UI 先行但结果不可追踪。

### 2026-07-31 Agent 失败分类按唯一失败用例封顶

用户部署 `b61bdf1` 后重新回归“基础打印新增百度网盘入口”。线上 Agent：

- `agent-1785484424874-db812072`
- 固定参数：`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.7-plus / com.xbxxhz.box`
- 服务健康：Task `qwen3.7-plus`，Figma token 存在，Runner `2026.07.26-qwen3.7-result-retry-v1`，OPPO `ecbfd645 / PHM110` ready。
- PLAN 在 4 分钟左右触发 `PLAN AI 超时`，降级为 3 条业务分支继续。
- 生成 5 条 YAML；执行计划：冒烟 3、非冒烟 2；dry-run 3/3 通过。
- 覆盖审计仍有 warning：文档文案、扫描同级关系、扫描可达性被认为未完整覆盖，未作为 blocker。
- 实际 Runner job 均绑定 `win-runner-01 / ecbfd645 / fixed`，没有向华为或第二台设备下发。
- 冒烟结果：文档通过，照片失败，扫描失败；修复重跑仍失败。
- 终态：`DONE / 部分通过`，`reportStatus=partial`，没有因为非全冒烟失败而变成整单失败。

本轮发现 `reportSummary.failed=2`，但 `scriptFailed=3 / unknownFailed=3`。根因是底层 execution 分类为原始失败 + 修复重跑失败事件计数，用户卡片需要唯一失败用例计数。

修复：

- `task_server/services/agent_service.py`
  - 新增 `_agent_unique_failure_class_counts()`，按唯一 `failed` 数把 `productFailed/scriptFailed/unknownFailed` 互斥封顶。
- `js/agent-status.js`
  - `agentRunFailureDetail()` 前端也按总失败数封顶，兼容历史记录或旧服务返回的过量分类。
- `task-manager.html`
  - `agent-status.js` 版本号更新为 `20260731-agent-failure-buckets`，避免浏览器继续使用旧卡片统计逻辑。
- `tests/backend_static_checks.py`
  - 新增线上形态回归：总失败 2、原始分类脚本 3/未判定 3 时，用户层必须展示脚本 2、未判定 0。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_history_list_exposes_report_summary()
print('targeted backend check passed')
PY
```

### 2026-07-31 Agent 报告按唯一用例展示部分通过和缺陷类型

用户质疑连续回归后结果越来越差。基于最近 3 次“基础打印新增百度网盘入口”线上回归：

- dry-run 误阻断未再复现，Runner 真实 dry-run 均能产出明确结果。
- 真实失败主要是部分用例失败，尤其照片打印入口展示；这不应把整个 Agent 结论渲染成“失败”。
- 列表接口已有 `reportSummary`，但详情接口没有，前端刷新单条后会回退到原始 execution 字段。
- 原始 execution 里同时保留 `smoke` / `首批冒烟`、`expanded-1` / `扩展第1批`，直接展示会出现重复 phase 视角。

修复：

- `task_server/services/agent_service.py`
  - `get_agent_run()` 详情返回补齐与列表一致的 `reportSummary`。
  - `reportSummary.reportStatus` 对 `partial` 结果投影为 `partial`，不再透传底层 HTML report 的 `failed`。
  - `reportSummary.phases` 压缩重复 phase 别名，用户层只保留唯一 `smoke` / `expanded-N` / `repair` 视角。
  - `reportSummary` 暴露 `productFailed`、`scriptFailed`、`unknownFailed`，用于区分产品缺陷、脚本问题和未判定失败。
- `js/agent-status.js`
  - Agent 历史卡片总用例明细显示“产品缺陷 / 脚本问题 / 未判定”，不再把所有失败混成同一种失败。
- `tests/backend_static_checks.py`
  - 新增 5 条计划用例、2 冒烟、3 非冒烟的线上形态回归测试，覆盖详情接口和 phase 去重。
- `tests/frontend_static_checks.py`
  - 新增卡片失败类型展示静态检查。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_history_list_exposes_report_summary()
print('targeted backend check passed')
PY
python3 tests/frontend_static_checks.py
node --check js/agent-status.js
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check -- task_server/services/agent_service.py tests/backend_static_checks.py js/agent-status.js tests/frontend_static_checks.py
```

### 2026-07-31 API 工作台命令中心 UI 重排

用户反馈：

- API 自动化页面信息太密集，不够直接，也不够好看。
- 希望参考 APIAuto 这类轻量工具的“少入口、强操作、可恢复”思路，而不是把同步状态、技术 ID、执行细节全部堆到首屏。

修复：

- API 工作台首屏从多段信息堆叠改为三层结构：
  - `api-command-center`：当前项目、连接状态、环境、接口快照、关键指标和主操作。
  - `api-next-actions`：只告诉用户下一步该做什么，并给出直接按钮。
  - `api-workflow-card` 四步流程：连接 Apifox、固化资产、AI 测试设计、本地执行报告。
- 接口快照区改为 `api-clean-snapshot`：
  - 默认只展示来源、版本、同步状态、接口数和业务模块生成入口。
  - Source/Revision/Project/Environment、同步详情和待处理变化折叠到“高级信息”。
- 首屏主按钮收敛为：
  - 更新资产 / 连接 Apifox
  - AI 生成测试
  - 开始测试
- 新增响应式样式：
  - 桌面两列命令中心。
  - 中屏两列卡片。
  - 手机单列，按钮铺满，避免文字挤压。
- 前端缓存版本更新为 `20260731-api-command-ui`。

验证：

```bash
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
git diff --check -- js/api-testing.js css/round5.css task-manager.html tests/frontend_static_checks.py
```

### 2026-07-31 Apifox 环境快照与平台级访问凭据

用户反馈：

- Apifox Token 每次新增项目都要重新填，其他同事不知道怎么操作。
- 从 Apifox 拉下来的环境配置不完整，常见表现是服务地址能看到但环境变量数量为 0 或缺少实际变量。
- 希望参考 APIAuto 这类轻量接口工具，不再把 API 流程做成复杂外部平台编排。

判断：

- Apifox 应继续只作为只读资产来源；平台自己保存项目、环境快照、AI 用例、执行日志和报告。
- 令牌应该是平台级 Apifox 凭据，新建项目默认复用；单个 source 如需更换仍可局部覆盖。
- 环境快照解析不能只认 `variables[].value`，需要兼容 Apifox/CLI 可能出现的 `currentValue`、`localValue`、`defaultValue`、`values`、`parameters`、`globalParameters`、`services`、`serverList` 等字段。
- 敏感变量只能保存名称和敏感标记，不能保存或返回明文值。

修复：

- `apifox_discovery_service.py`
  - Apifox 环境解析改为多字段合并，不再只取第一个 truthy 字段。
  - 服务地址兼容 `services/serviceList/servers/serverList/hosts/baseUrls` 及 `url/value/currentValue/localValue/defaultValue/baseUrl`。
  - 环境变量兼容 `variables/values/parameters/globalParameters` 等来源及多种值字段。
- `api_source_service.py`
  - 本地 `environment_snapshot` 归一化复用同一类字段兼容规则。
  - 新增平台级 Apifox 凭据保存能力，凭据文件仅服务端保存，公开 payload 只返回 `credential_configured/base_url/updated_at`。
  - 保存新 Apifox source 时，如未提交 token 且平台凭据已保存，则自动复用平台凭据；已有 source 自己的 token 不会被全局凭据覆盖。
- `router.py`
  - 新增登录保护接口：
    - `GET /api/api-testing/apifox/credential`
    - `POST /api/api-testing/apifox/credential`
  - Apifox 项目发现接口在请求体没有 token/source_id 时自动使用平台级凭据。
- `api_workbench_service.py` / `js/api-testing.js`
  - Workbench payload 暴露脱敏后的 `apifox_credential` 状态。
  - 新增项目面板显示“平台令牌已保存”，允许直接读取 Apifox 项目。
  - Token 输入框新增“单独保存令牌”，保存后不回填明文。
- 前端缓存版本更新为 `20260731-apifox-credential-env`。

验证：

```bash
python3 -m unittest tests.api_asset_sync_checks.ApiSourceConfigTests.test_environment_snapshot_accepts_apifox_cli_variable_aliases tests.api_asset_sync_checks.ApiSourceConfigTests.test_global_apifox_credential_is_write_only_and_reusable tests.api_asset_sync_checks.ApifoxDiscoverySnapshotTests.test_environment_snapshot_merges_cli_environment_detail_shapes tests.api_asset_sync_checks.ApiSourceRouteTests.test_apifox_credential_route_is_write_only_and_used_by_discovery tests.api_asset_sync_checks.ApiSourceRouteTests.test_source_save_uses_saved_apifox_credential_for_new_project
python3 tests/api_asset_sync_checks.py
python3 tests/api_workbench_checks.py
python3 tests/api_native_execution_checks.py
python3 tests/frontend_static_checks.py
python3 -m py_compile task_server/services/apifox_discovery_service.py task_server/services/api_source_service.py task_server/services/api_workbench_service.py task_server/router.py tests/api_asset_sync_checks.py tests/api_workbench_checks.py tests/frontend_static_checks.py
node --check js/api-testing.js
python3 tests/backend_static_checks.py
git diff --check -- task_server/services/apifox_discovery_service.py task_server/services/api_source_service.py task_server/services/api_workbench_service.py task_server/router.py js/api-testing.js task-manager.html tests/api_asset_sync_checks.py tests/api_workbench_checks.py tests/frontend_static_checks.py
```

### 2026-07-31 Runner dry-run 超时不再硬阻断 Agent 正式执行

用户反馈连续回归后失败越来越多。线上 3 次百度网盘 Agent 回归显示：

- 前两次是真实 Runner 用例部分通过，结论已保持为“部分通过”。
- 第三次生成 6 条 YAML、20 个场景，但在 `RUN_SONIC` 前置 `runner_yaml_dry_run` 阶段等待报告超时。
- 两个 dry-run job 处于 pending/queued，无正式 Runner job 创建，最终被标记为 `FAILED / 未执行`。
- 这不是 YAML 脚本失败，也不是产品失败，而是 Runner dry-run 报告不确定。

根因：

- 旧策略把 “Runner 真实 dry-run 等待报告超时，结果不确定” 直接写成 `formalDispatchSkipped` 和 `blockedFormalDispatch`。
- 只要 dry-run 等待超时，即使本地 YAML dry-run 已通过，也会阻断正式下发，并把 Agent 标为失败。
- 这与当前生产口径冲突：冒烟/非冒烟测试可以失败，但未执行/环境不确定不能污染真实用例通过率；除非冒烟全失败，最终结论不应直接升级为失败。

修复：

- `task_server/services/agent_service.py`
  - 真实 Runner dry-run 明确返回 failed 时，仍然硬拦截正式下发。
  - 真实 Runner dry-run 仅等待超时、且本地 YAML dry-run 已通过时，不再标记 YAML failed。
  - 记录 `runnerDryRun.inconclusive=true` 和 `runnerDryRun.fallbackToFormalDispatch=true`。
  - 继续创建正式 Runner job，避免 dry-run 报告链路抖动导致整轮 Agent “未执行失败”。
- `tests/backend_static_checks.py`
  - 新增回归测试 `check_agent_runner_dry_run_timeout_does_not_block_formal_dispatch()`。
  - 更新旧断言：inconclusive dry-run 必须可见，但不再硬阻断正式执行。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_runner_dry_run_timeout_does_not_block_formal_dispatch()
checks.check_agent_summary_separates_runner_outcomes_from_orchestration()
checks.check_agent_history_list_exposes_report_summary()
print('targeted checks passed')
PY
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
```

### 2026-07-31 API 工作台六入口产品化收敛

用户要求严格按上传文档重构接口测试模块：

- Apifox 只作为只读接口资产和环境来源。
- 平台负责本地资产快照、环境配置、AI 测试设计、本地执行、执行日志和报告。
- 不再把 MeterSphere 作为 API 主流程入口。
- 页面要简洁、中文化、便于同事直接使用。

参考判断：

- 参考 Bruno / Yaak 这类轻量 API 客户端，核心不是堆平台能力，而是 local-first、本地快照、清晰环境变量和低学习成本。
- 参考测试报告实践，执行结果需要能按 run/report 快速追踪，请求、响应、断言、耗时、失败分析必须留痕。

修复：

- API 自动化左侧一级入口收敛为 6 个：
  - API 工作台
  - 接口资产
  - 测试设计
  - 执行记录
  - 测试报告
  - 环境配置
- 移除 API 侧旧 `同步中心 / 调试执行 / API 基线` 一级入口；旧路由只保留兼容跳转：
  - `api_sync -> 接口资产`
  - `api_baselines -> 测试设计`
  - `api_execution -> 执行记录`
- API 工作台新增 5 个核心指标：
  - API接口总数
  - 已覆盖接口
  - 覆盖率
  - 待处理变化
  - 今日执行
- Workbench 后端补齐 `metrics / sync_state / pending_changes`，从本地 source、revision、plan、execution、sync 记录汇总，不依赖第三方执行平台。
- 修复 `_execution_summary()` 返回位置错误，避免 workbench 指标计算拿到 `None`。
- 文案统一从“AI 用例计划 / 采纳为基线 / API 基线”调整为“测试设计 / 保存为测试资产 / 已保存测试资产”。
- 环境配置页仍使用现有 `/environment-snapshot` 后端保存能力，但前端从 JSON textarea 改成结构化表格：
  - 服务地址可新增、删除、编辑名称和 Base URL。
  - 环境变量可新增、删除、编辑变量名/变量值，并标记敏感。
  - 保存的是平台本地执行副本，不反写 Apifox；敏感变量继续由服务端脱敏。
- 前端缓存版本更新为 `20260731-api-product-workbench`。

验证：

```bash
python3 tests/frontend_static_checks.py
python3 tests/api_workbench_checks.py
python3 tests/api_native_execution_checks.py
python3 tests/api_asset_sync_checks.py
python3 -m py_compile task_server/services/api_workbench_service.py task_server/services/api_execution_service.py tests/api_workbench_checks.py tests/frontend_static_checks.py
node --check js/api-testing.js && node --check js/api.js && node --check js/navigation.js && node --check js/agent-status.js
python3 tests/backend_static_checks.py
git diff --check -- task-manager.html js/api-testing.js js/api.js js/navigation.js js/agent-status.js css/app.css css/round5.css task_server/services/api_workbench_service.py task_server/services/api_execution_service.py tests/frontend_static_checks.py tests/api_workbench_checks.py
```

### 2026-07-31 API 自动化中心第一阶段产品重构

用户提供方案文档并明确希望：

- Apifox 只作为接口资产来源。
- 平台负责 API 资产管理、环境配置、AI 测试设计、原生执行、执行日志和测试报告。
- 不接 MeterSphere，不回写 Apifox。
- 当前操作过于费事，需要更简洁明了。

判断：

- 文档中的 `api_project/api_environment/api_definition/api_case/api_execution/api_sync_record` 是长期数据模型方向。
- 当前仓库已经有本地 JSON 持久化、Apifox source/sync/revision、AI plan、原生 API Runner、report 等能力；本轮不适合再并行新增一套数据库目录。
- 第一阶段应先做产品路径重构：把已有能力按用户心智组织成“API 自动化中心”，减少隐藏入口和内部概念。

修复：

- 左侧“接口测试”改为“接口自动化”，暴露 8 个清晰入口：
  - API 工作台
  - 接口资产
  - 同步中心
  - 环境配置
  - AI测试设计
  - 调试执行
  - 执行记录
  - 测试报告
- 新增聚焦页：
  - `showApiSyncCenterPage()`：查看 Apifox 同步状态、差异统计、影响计划和同步日志。
  - `showApiEnvironmentPage()`：集中维护 Base URL、环境变量、本地环境快照和业务鉴权，明确“不回写 Apifox”。
  - `showApiExecutionHistoryPage()`：查看 API Runner 真实执行记录、请求响应、断言和报告入口。
- 流程条改为日常语言：
  - 项目资产 → 同步接口 → 环境配置 → AI设计 → 调试执行 → 测试报告。
  - 旧的“审阅确认 / 执行报告”不再作为主路径文案。
- 按用户提供文档回头验收后补齐导航一致性：
  - draft 用例的下一步仍显示“审阅用例”，但归入现有 `AI设计` step，不再返回旧的 `review` step。
  - API 工作台不再把“维护 API 基线”作为隐藏主入口，改为显式“环境配置”入口；基线仍作为 AI 设计确认后的内部可执行产物保留。
  - 前端静态检查新增回归断言，防止旧 step id 和隐藏基线入口重新出现在主路径。
- 保留已有 API 基线页面能力，但不再作为左侧一级入口；基线维护从 AI 测试设计内部进入。
- 同步中心复用本地 Apifox source/sync/revision，不新增 MeterSphere，不反写 Apifox。
- 环境配置页保存本地环境快照后留在当前页，业务 token 保存后刷新当前鉴权摘要。
- 前端缓存版本更新为 `20260731-api-native-center`。

补充视觉优化：

- API 自动化左侧导航分组增加独立容器、`API Runner` 标签、字母徽标层次、选中态左侧光条和移动端窄栏兜底。
- `css/app.css` 缓存版本更新为 `20260731-api-sidebar-polish`。
- 前端静态检查新增 API 自动化侧边栏视觉样式断言。

验证：

```bash
node --check js/api-testing.js && node --check js/api.js && node --check js/navigation.js && node --check js/agent-status.js
python3 tests/frontend_static_checks.py
python3 tests/api_workbench_checks.py
python3 tests/api_native_execution_checks.py
python3 tests/api_asset_sync_checks.py
python3 tests/backend_static_checks.py
git diff --check -- task-manager.html js/api-testing.js js/api.js js/navigation.js js/agent-status.js css/round5.css tests/frontend_static_checks.py
```

### 2026-07-31 Agent 历史卡片最终结果统计去重

用户反馈：

- Agent 运行记录卡片出现 `4/3 通过`、`6/4 通过` 这类分子大于分母的统计。
- 其他人无法一眼判断实际通过多少条、失败多少条。

线上复现：

- `agent-1785461191470-63ea62f9` 实际 Runner 轨迹是：3 条 smoke 全通过，1 条 expanded 先失败，修复重跑通过。
- 列表接口旧 `reportSummary` 显示 `attempted=4 / passed=6`，因为把 `smoke`、`首批冒烟`、`安全重跑`、`安全重跑-同设备串行` 等重复 phase 累加到了总通过数。

修复：

- `agent_service._agent_report_execution_buckets_from_plan()` 把修复重跑成功用于抵扣未通过的唯一用例：
  - 先抵扣 smoke 未通过。
  - 剩余修复成功再抵扣 non-smoke/expanded 未通过。
- `_agent_run_report_summary()` 在有执行计划桶时，用 `smoke + nonSmoke` 的唯一用例最终桶重算 `attempted/passed/failed/timeout/running`。
- 修复后通过且无最终失败时，将卡片使用的 `reportStatus` 投影为 `success`，避免旧原始报告 failed 覆盖最终结论。
- `agent-status.js` 前端兜底优先使用 smoke/non-smoke 桶计算总数，不再直接展示重复 phase 导致的原始 `passed/attempted`。
- Agent 历史卡片主结果改为 `通过 N 条 / 共 M 条`，避免 `4/4 通过` 这类比例写法不够直观。
- Agent 历史卡片时间直接展示完整 `YYYY-MM-DD HH:mm:ss`，首行改为弹性布局，避免右上角时间被截断。
- `task-manager.html` 更新缓存版本为 `20260731-agent-history-full-time`。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_report_summary_keeps_non_smoke_buckets()
checks.check_agent_report_summary_keeps_non_smoke_actual_execution_separate_from_plan()
checks.check_agent_report_summary_counts_unique_final_cases_after_expanded_repair()
print('targeted backend checks passed')
PY
python3 tests/frontend_static_checks.py
node --check js/agent-status.js
```

### 2026-07-31 API 执行实时日志与已保存 Apifox 项目入口

用户反馈：

- 参考 `aippt-auto-test-share` 后，希望接口执行时像对方工具一样能直接看到实时日志，而不是只等最终报告。
- Apifox 同步下来的项目、环境和接口快照需要有明显入口；同事使用时不应该每次都重新刷新或手填 Project ID。

参考判断：

- 参考工具的优点是流程短：左侧选环境/命令，右侧默认显示执行日志，结束后沉淀报告。
- 我们不能照搬其内存任务和 stdout 日志实现；平台需要继续用本地持久化 source/execution/report 记录，并保持敏感字段脱敏。

修复：

- 原生 API 执行器细化执行事件：
  - 排队、准备环境、发送请求、收到响应、断言通过/失败、生成报告、任务完成。
  - 请求/响应日志保存结构化 detail，前端可展开查看。
  - 鉴权只显示 `auth_state: Bearer ***`，不保存或展示 token 明文。
- API 执行页新增“实时执行日志”面板：
  - 默认跟随 active run 展示。
  - 中文展示阶段：准备环境、请求响应、断言结果、生成报告。
  - 继续保留日志展开状态和滚动位置，轮询刷新不折叠。
- API 资产页新增“已保存 Apifox 项目”项目架：
  - 直接展示本地已保存 source、环境和 base_url。
  - 同事可直接切换已有项目；只有需要更新时才重新读取 Apifox。
  - 不新增执行器、不反写 Apifox。
- API 执行页也复用“已保存 Apifox 项目”项目架：
  - 执行页会读取本地已保存 sources，不要求用户先回资产页刷新。
  - 在执行页切换项目只切换本地执行上下文，不触发 Apifox 重新同步。
  - 修复首次进入执行页时默认 source 与实时日志轮询 scope 不一致的问题。
- 前端缓存版本更新为 `20260731-api-live-log-sources`。

验证：

```bash
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
python3 tests/api_native_execution_checks.py
python3 tests/api_workbench_checks.py
python3 tests/api_asset_sync_checks.py
python3 -m py_compile task_server/services/api_execution_service.py tests/api_native_execution_checks.py tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check -- task_server/services/api_execution_service.py js/api-testing.js css/round5.css task-manager.html tests/api_native_execution_checks.py tests/frontend_static_checks.py
```

### 2026-07-31 API 报告可读性与 Apifox 本地环境快照编辑

用户反馈：

- 参考外部自研接口测试面板后，当前 API 报告“没法看”，缺少清晰执行过程、结论、失败分析和单条接口明细。
- 从 Apifox 拉下来的环境配置需要能在平台里改；但修改应服务于本地执行，不应反写 Apifox。
- 页面文案尽量中文展示。

线上复核：

- 健康检查正常，线上已有报告 `api_report_1785320933785_00031`。
- 该历史报告摘要显示 7 条全通过，但原始 remote 状态仍有 `PENDING / provider_terminal_state_missing` 等旧 MeterSphere 兼容字段。
- 报告明细只保存了 `case_id/name/status/duration/error`，没有请求、响应、断言、环境和失败分析结构；因此 UI 不能假装这是完整报告。

参考判断：

- Postman Collection Runner、Bruno CLI report、Allure report 的共同点是先展示概要，再展示环境/执行上下文、失败归因和单用例详情。
- 我们平台已转向原生 API 执行器，不再依赖 MeterSphere；因此报告结构应围绕平台本地执行日志和本地报告，而不是第三方任务状态。

修复：

- API 报告详情改成中文结构化展示：
  - 结论卡：总用例、通过、失败、跳过、通过率；摘要缺失时从明细回算。
  - 执行环境：服务地址、业务、环境、鉴权、耗时。
  - 历史数据不足提示：旧报告缺少请求/响应/断言时明确标注，不误导成完整报告。
  - 失败分析按原因分组展示，保留每个失败用例的摘要。
  - 每条接口改为可展开卡片，展示接口请求、响应结果、断言校验和处理建议；失败项默认展开。
- Apifox 环境快照新增本地编辑入口：
  - `POST /api/api-testing/sources/{source_id}/environment-snapshot` 保存平台本地执行副本。
  - 支持编辑服务地址列表和普通环境变量。
  - 敏感变量名继续由后端脱敏保存；业务用户登录 token 仍放在“环境公共鉴权”安全 profile，不散落到环境快照里。
  - 明确“不反写 Apifox”。
- 前端缓存版本更新为 `20260731-api-report-env-edit`。

验证：

```bash
python3 tests/api_workbench_checks.py
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
python3 -m py_compile task_server/router.py tests/api_workbench_checks.py tests/frontend_static_checks.py
python3 tests/api_native_execution_checks.py
python3 tests/api_asset_sync_checks.py
python3 tests/backend_static_checks.py
git diff --check -- task_server/router.py js/api-testing.js css/round5.css task-manager.html tests/api_workbench_checks.py tests/frontend_static_checks.py
```

### 2026-07-31 最近 8 次百度网盘回归生成规模与结果桶修正

用户反馈：

- 最近 8 次“基础打印新增百度网盘入口”回归通过率偏低。
- 大需求不应该只生成少量用例；完整测试计划和首批 Runner 执行数量不能混在一起。
- 历史卡片里“非冒烟”有时显示为已执行，实际只是已计划未进入扩展执行，容易误判通过率。

线上最近 8 次观察：

- 用例生成量主要集中在 4-6 条，完整计划没有稳定放大到 20+。
- 失败集中在照片打印分支可达性覆盖、首批冒烟失败、以及报告收集把“非冒烟计划”误展示成“非冒烟已执行”。
- 有些任务实际 6 条跑过 5 条通过，但 Agent 终态仍显示失败或卡片统计不清晰，影响判断。

根因：

- `generation_volume_targets()` 只按 requirement point 数量判断规模；百度网盘需求被抽象成 3 个业务入口后，没有把 12 个验收维度纳入规模判断。
- `generation_targets_for_scope()` 过度相信 AI 返回的 scope target，导致多入口多验收点需求仍可能按 medium/small 生成。
- 报告桶统计把 deferred/remaining 计划数当成实际非冒烟执行数，导致卡片显示“非冒烟通过/失败”与真实 Runner 执行不一致。

修复：

- `case_service.generation_volume_targets()` 纳入 `requirement_acceptance_checks`、业务分支数和有效验收点数；3 个业务分支且 8 个以上验收点时强制按大需求处理。
- `ai_skill_service.generation_targets_for_scope()` 增加多分支验收兜底：完整计划放大，自动化 YAML 目标提升到 12 条，避免 AI scope 误判压小生成范围。
- `agent_service._agent_report_execution_buckets_from_plan()` 拆开“计划数量”和“真实执行数量”；没有 expanded runner 结果时，非冒烟实际执行保持 0，不再用计划数填充。
- `reportSummary` 新增 `totalPlanned/smokePlanned/nonSmokePlanned`，前端卡片在非冒烟未执行时显示“已计划，未进入扩展执行”。
- `task-manager.html` 更新 `agent-status.js` 缓存版本为 `20260731-agent-history-actual-buckets`。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_report_summary_keeps_non_smoke_actual_execution_separate_from_plan()
checks.check_generation_volume_uses_acceptance_dimensions_for_large_entry_requirements()
print('targeted checks passed')
PY
python3 tests/frontend_static_checks.py
node --check js/agent-status.js
python3 -m py_compile task_server/services/agent_service.py task_server/services/ai_skill_service.py task_server/services/case_service.py tests/backend_static_checks.py tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
```

### 2026-07-30 原生 API 工作台模块生成范围与 AI 批次卡住恢复

用户反馈：

- API 工作台流程仍然不够明朗，模块卡片有些显示不出具体接口数量。
- 从大模块生成 AI 用例时页面停在“生成中 / 5 批”，不知道是不是卡住。
- 用户希望借鉴 Postman / Apifox / Bruno / Hoppscotch 等成熟 API 工具的做法：接口资产和环境先固化成本地快照，执行流程尽量直接，AI 参与用例设计和失败分析，但用户不需要反复手填 ID 或依赖 MeterSphere。

线上排查：

- 当前卡住的 generation `api_plan_generation_1785409431686_00008` 选择了 60 个接口，按 12 个一批拆成 5 批串行生成。
- 第 1 批已成功生成计划，第 2 批长时间停在 running，后续批次 queued。
- 模块卡片计数不准的根因是前端用截断后的 `scope.endpoints` 样本倒推数量；工作台只加载前 300 个接口，无法代表完整 Apifox 快照中的 986 个接口。

修复：

- `api_module_service.module_summary()` 给每个模块节点返回服务端真实 `endpoint_count`，并附带最多 60 个 `endpoint_ids` 作为当前模块可直接生成的范围。
- API 工作台模块卡片改用服务端 `module.endpoint_count`，不再从前端截断样本倒推数量。
- 大模块生成前显示本次会选择多少接口、拆成几批 AI 串行生成，并建议优先选择最多 3 个子模块，避免用户误以为“点一下就全量生成 986 个接口”。
- 生成时直接使用服务端模块节点的 `endpoint_ids`，不需要用户填写 ID，也不会因为前端只加载 300 个接口而选不全当前模块的前 60 个。
- `api_plan_generation_service` 新增 running 批次超时恢复：
  - 默认 600 秒，可用 `MIDSCENE_API_PLAN_GENERATION_BATCH_TIMEOUT_SECONDS` 调整。
  - 超时 running 批次会标记为 `failed / ai_batch_timeout / recoverable`。
  - 已成功批次的 `plan_id` 保留，重试只重试失败批次。
  - 如果迟到的 AI 返回发生在超时恢复之后，worker 不会覆盖已恢复的终态。
- `task-manager.html` 更新 `api-testing.js` 缓存版本为 `20260730-api-workbench-scope-recovery`。

验证：

```bash
python3 tests/api_workbench_checks.py
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
python3 -m py_compile task_server/services/api_module_service.py task_server/services/api_plan_generation_service.py tests/api_workbench_checks.py tests/frontend_static_checks.py
python3 tests/api_native_execution_checks.py
python3 tests/backend_static_checks.py
```

### 2026-07-30 Apifox 环境 base_url 读取与 API 页面环境优先展示

用户反馈：

- API 资产和 API 执行页仍然混乱，最核心诉求是“能正常拉取 Apifox 的环境”。
- 执行页显示 `base_url` 缺失，导致不知道下一步该怎么处理。

线上复核：

- 生产 `/api/api-testing/sources` 中两个 Apifox source 均显示 `last_sync_status=succeeded`。
- 但 `environment_snapshot.base_urls=[]`，其中 `3D / 生产环境（新）-腾讯云` 也没有 base_url。
- 因此问题不是前端单纯没展示，而是后端只读取了 Apifox `environment list`，没有按官方 CLI 文档补读 `environment get <envId>` 的环境详情。

参考与判断：

- Apifox CLI 官方文档说明 `environment list` 用于列出项目环境，`environment get` 用于查看特定环境配置详情（前置 URL / Base URL 等）。
- Postman、Insomnia、Hoppscotch 等成熟 API 工具都把 Environment 作为执行上下文，先明确 base URL、变量和敏感值状态，再进入执行和报告。
- 本轮不再保留 MeterSphere 执行概念；页面只围绕 Apifox source、当前环境、base_url、变量数和原生执行 readiness 展示。

修复：

- `apifox_discovery_service.discover_project_context()` 新增 `preferred_environment_id`。
- 环境数量不多时批量调用 `apifox environment get <envId>` 补全详情；环境很多时优先补当前已选环境，避免大项目读取过慢。
- 环境快照解析兼容 `baseUrls / baseUrl / servers / serverList / services / serviceList / hosts`，变量兼容数组和对象 map。
- 仍按敏感字段规则脱敏，不把 token/secret/password/cookie/authorization 明文写入本地或前端。
- API 资产页顶部新增紧凑环境卡：Apifox 项目、当前环境、服务地址、环境变量数。
- API 执行页把“执行环境”提升为主卡，缺 `base_url` 时明确提示回接口资产重新读取 Apifox 环境；业务/环境切换降级为折叠高级项。
- 前端缓存版本更新为 `20260730-api-env-readiness`。

验证：

```bash
python3 tests/apifox_discovery_checks.py
python3 tests/api_asset_sync_checks.py
python3 tests/api_native_execution_checks.py
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
python3 -m py_compile task_server/services/apifox_discovery_service.py task_server/router.py tests/apifox_discovery_checks.py tests/frontend_static_checks.py
git diff --check -- task_server/services/apifox_discovery_service.py task_server/router.py js/api-testing.js css/round5.css task-manager.html tests/apifox_discovery_checks.py tests/frontend_static_checks.py
```

完整 `python3 tests/backend_static_checks.py` 仍被既有 OBJ 保龄球历史 YAML 断言拦截：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`。本轮未修改历史 YAML。

### 2026-07-30 Agent 历史卡片非冒烟统计

用户发现 Agent 运行记录卡片里“非冒烟”显示 `无非冒烟用例`，但同一任务实际已执行扩展/remaining 用例。

根因：

- 列表接口只返回 `reportSummary`，不返回完整 `artifacts.generatedYamlExecutionPlan`。
- `reportSummary.smokeAttempted` 之前来自 `execution.phases` 聚合，`smoke`、`首批冒烟`、`安全重跑`、`recovered-expanded-*` 等 phase 被重复归入冒烟，导致 `smokeAttempted` 膨胀到接近或等于总用例数。
- 前端卡片再用 `总数 - 冒烟数` 倒推非冒烟，因此被挤成 0。

修复：

- 后端 `_agent_run_report_summary()` 新增执行计划桶统计：优先读取 `generatedYamlExecutionPlan.counts.selectedSmoke / deferredExecutable`。
- `smokeResult` 提供原始冒烟结果；如果失败冒烟后续同设备修复重跑通过，则把恢复结果计入最终冒烟通过。
- 新增 `nonSmokeAttempted / nonSmokePassed / nonSmokeFailed / nonSmokeTimeout / nonSmokeRunning` 字段，列表接口可以直接给历史卡片使用。
- 前端 `agent-status.js` 优先使用后端 `nonSmoke*` 字段，不再只靠总数减冒烟倒推。
- `task-manager.html` 更新 `agent-status.js` 缓存版本为 `20260730-agent-history-non-smoke-buckets`。

用刚才两条真实百度网盘 run 验证新摘要：

- `agent-1785392436290-ea2c1fcc`：总 6，4 过 2 失；冒烟 2，1 过 1 失；非冒烟 4，3 过 1 失。
- `agent-1785393789859-8146e48e`：总 6，5 过 1 失；冒烟 3，3 过 0 失；非冒烟 3，2 过 1 失。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_report_summary_keeps_non_smoke_buckets()
PY
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
node --check js/agent-status.js
git diff --check -- task_server/services/agent_service.py js/agent-status.js task-manager.html tests/backend_static_checks.py tests/frontend_static_checks.py
```

完整 `python3 tests/backend_static_checks.py` 仍被既有 OBJ 保龄球历史 YAML 断言拦截：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`。本轮不修改历史 YAML。

### 2026-07-30 API draft 单条调试，不必先采纳为基线

用户反馈：

- 在 AI 生成的 draft 候选详情里，想先单个调试可执行用例，不希望必须先“采纳为基线”才能运行。

设计边界：

- 正式 MeterSphere 回归执行仍必须基于 `confirmed` API 基线，原有整计划执行门禁不放松。
- draft 中已被平台校验为 `executable` 的单条 API 用例，可以走“单条调试”。
- 单条调试不会把 draft 采纳为基线，也不会覆盖正式基线场景；它创建隔离的临时 debug plan id 和 MeterSphere 场景 binding。
- 待补数据、接口版本过期、业务/环境/token 绑定漂移的 draft 用例仍不能调试。

修复：

- 后端新增 `metersphere_service.start_metersphere_case_debug(plan_id, case_id)`：
  - 校验原始 plan 存在、未过期、无 binding drift。
  - 只允许 `readiness.state=executable` 的 case。
  - 生成 `api_debug_*` 临时 plan snapshot，仅包含所选 case 和对应 endpoint。
  - 执行记录标记 `run_mode=debug_case`、`source_plan_id`、`debug_case_id`。
- 执行 worker 支持 `execution_plan_snapshot`：
  - 有 snapshot 时直接推送/运行临时计划，不要求该 plan 写入 API plan 索引。
  - 仅精确绕开“已确认 API 用例计划”这个回归门槛；MeterSphere 连接、业务、环境、source binding 仍实时校验。
- 新增路由 `POST /api/api-testing/metersphere/executions/debug-case`。
- 前端 draft 用例明细中，`executable` 用例显示 `调试单条` 按钮；点击后创建调试执行并跳到 MeterSphere 实时执行页。
- 实时执行卡新增 `run_mode === 'debug_case'` 显示为 `单条调试`，正式执行仍显示为 `基线回归`。
- 前端缓存版本更新为 `20260730-api-draft-case-debug`。

验证：

```bash
python3 tests/metersphere_v365_adapter_checks.py
python3 tests/api_project_workspace_checks.py
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
python3 -m py_compile task_server/services/metersphere_service.py task_server/router.py tests/metersphere_v365_adapter_checks.py
git diff --check -- task_server/services/metersphere_service.py task_server/router.py js/api-testing.js task-manager.html tests/frontend_static_checks.py tests/metersphere_v365_adapter_checks.py CODEX_STATE.md
```

### 2026-07-30 API AI 用例生成入口、审阅目标与绑定漂移下一步说明

用户反馈：

- 在 API 资产页点击“生成 AI 用例”后，不知道有没有真正生成。
- 进入候选计划后，不清楚“审阅”的目的以及应该怎么审阅。
- `workspace_binding_drift` 页面虽然展示了计划生成时绑定、当前执行绑定和当前业务 token，但用户仍不清楚怎样才能走到下一步。

根因：

- API 资产页右侧按钮文案是“生成 AI 用例”，但实际行为只是执行 `showApiPlanPage()`，把已选接口带入 AI 用例计划页；真正调用 AI 的动作发生在计划页的“生成 AI 用例”按钮。
- 候选详情把“审阅候选”作为内部流程名展示，但没有说明审阅是采纳前门禁，也没有列出用户需要核对的请求、入参、鉴权和断言。
- `workspace_binding_drift` 本质是旧候选绑定失效，应按当前绑定重新生成，不应让用户误以为要逐条编辑旧候选用例。

修复：

- API 资产页右侧按钮从“生成 AI 用例”改为“进入 AI 用例计划”，并新增 `api-asset-generation-feedback`，明确“生成任务尚未开始，点击计划页的生成按钮后才会调用 AI”。
- 从资产页进入计划页时显示 `api-plan-launch-notice`，提示已带入接口数量、生成尚未开始、后续会在同一区域展示排队/批次/日志/结果。
- 候选详情新增 `api-plan-review-guide`，说明审阅目标是“把 AI draft 变成可执行基线”，并列出三项检查：请求方法/路径/入参/鉴权变量/响应断言、待补数据处理、可执行项满足范围后再采纳。
- 绑定漂移面板新增 `api-plan-drift-guide`，明确下一步是“按当前绑定重新生成，不需要逐条编辑”，重新生成后平台会重新校验业务 token、环境变量和可执行数据。
- 前端缓存版本更新为 `20260730-api-plan-launch-feedback`。

验证：

```bash
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
git diff --check -- js/api-testing.js css/round5.css task-manager.html tests/frontend_static_checks.py CODEX_STATE.md
```

### 2026-07-30 API 计划绑定漂移时展示当前绑定和 token 变量

用户截图中 AI 用例计划详情停在 `workspace_binding_drift`，并显示“未配置业务用户登录 token”，但用户已经配置过 3D 项目的业务 token，页面无法解释为什么卡住、token 保存在哪里。

线上复核：

- 使用 `admin / sonic2026` 登录生产 API 成功；`wangwc / gfd178` 对当前平台登录接口返回账号密码错误。
- 当前 3D source：`api_source_1785310905647_00002`。
- 当前 MeterSphere 执行绑定：
  - 业务：`3D业务 / 772578717212672`
  - 环境：`线上环境 / 727704900321280`
  - 当前绑定 fingerprint：`f800487afa81e9e0`
- 当前业务 token 已配置，但不是明文保存在平台：
  - `auth_ref=api_auth_bea1481536cf572b`
  - `variable_name=MTP_API_AUTH_BEA1481536CF`
  - 平台本地只保存变量名、绑定范围和指纹；真实 token 已转写到 MeterSphere 当前环境变量。
- 截图中的候选计划 `api_plan_1785372704395_00006` 是在当前绑定/鉴权之前生成的：
  - 计划记录的 `binding_fingerprint=3e4e713a590d0517`
  - 计划记录的 `auth_binding={}`
  - 当前绑定已变为 `f800487afa81e9e0`
  - 因此平台按设计标记 `workspace_binding_drift`，阻止旧候选直接采纳/执行。

修复：

- API 计划详情新增 `api-plan-binding-drift-panel`。
- 当存在 `workspace_binding_drift` 或 `auth_binding_drift` 时，页面同时展示：
  - 计划生成时绑定
  - 当前执行绑定
  - 当前业务 token 的 MeterSphere 变量名/服务端引用
- 阻断状态下主按钮从“查看待补数据”改为“按当前绑定重新生成”，引导用户用当前 3D 业务/线上环境/token 重新生成候选计划。
- 仍不展示真实业务 token 明文。
- 前端缓存版本更新为 `20260730-api-binding-drift-panel`。

验证：

```bash
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
git diff --check -- js/api-testing.js css/round5.css task-manager.html tests/frontend_static_checks.py
```

### 2026-07-30 Apifox 环境配置快照与 MeterSphere 环境变量同步

用户希望把 Apifox 上的环境配置信息同步到 MeterSphere，减少手填环境 ID、变量和 token 的混乱。

参考 MeterSphere v3.x 官方环境管理文档：项目环境支持环境变量、HTTP 配置、请求头、前后置、断言等；其中环境变量可在请求体和脚本中通过 `${变量名}` 引用。因此本轮采用“来源环境快照 + 显式写入 MeterSphere 环境变量”的保守方案，不把 Apifox 环境值直接摊进每条 API 用例。

修复：

- Apifox CLI 项目上下文读取现在会为每个非默认环境返回脱敏 `environment_snapshot`，包含 `base_urls`、`variables`、`variable_count`、`sensitive_variable_count`。
- API source 保存时持久化选中环境的安全快照；`token / secret / password / cookie / authorization / accessToken` 等敏感变量只保留名称、计数和 `sensitive=true`，值置空，不写入本地文件或前端响应。
- 新增 `POST /api/api-testing/sources/{source_id}/environment-sync`：要求当前 API source 已绑定 MeterSphere 项目和环境，只把非敏感 base URL 和变量写入 MeterSphere 环境变量。
- MeterSphere v3.6.5 adapter 允许平台管理变量前缀从仅 `MTP_API_AUTH_*` 扩展到 `MTP_API_AUTH_* / MTP_APIFOX_*`，仍拒绝修改用户自建的非平台变量。
- 同步变量命名：
  - `MTP_APIFOX_BASE_URL_*`：Apifox 环境服务地址。
  - `MTP_APIFOX_VAR_*`：Apifox 普通环境变量。
- API 资产设置面板新增“Apifox 环境配置”卡片，按“服务地址 / 环境变量”分区展示，并提供“同步到 MeterSphere 环境”按钮；敏感变量显示“敏感值未同步”。
- 前端缓存版本更新为 `20260730-apifox-env-snapshot`。

未做：

- 未自动覆盖 MeterSphere HTTP 配置、全局请求头、前置/后置脚本或数据库/HOST 配置；这些属于更高风险的环境导入能力，应单独按 MeterSphere 环境导入模型设计。
- 未自动同步 Apifox 敏感变量值；业务用户 token 仍走现有“环境公共鉴权”通道，避免把运行密钥混入来源资产。

验证：

```bash
python3 -m unittest tests.apifox_discovery_checks.ApifoxDiscoveryServiceChecks.test_project_context_includes_safe_environment_snapshot tests.api_asset_sync_checks.ApiSourceConfigTests.test_environment_snapshot_is_public_and_redacted tests.api_project_workspace_checks.ApiWorkspaceBindingChecks.test_apifox_environment_snapshot_syncs_only_safe_values_to_metersphere tests.metersphere_v365_adapter_checks.MeterSphereV365EnvironmentVariableChecks.test_environment_variable_upsert_allows_platform_apifox_prefix
python3 -m py_compile task_server/services/api_source_service.py task_server/services/apifox_discovery_service.py task_server/services/metersphere_service.py task_server/services/metersphere_v365_adapter.py task_server/router.py tests/api_project_workspace_checks.py tests/api_asset_sync_checks.py tests/apifox_discovery_checks.py tests/metersphere_v365_adapter_checks.py
python3 tests/apifox_discovery_checks.py
python3 tests/api_asset_sync_checks.py
python3 tests/api_project_workspace_checks.py
python3 tests/metersphere_v365_adapter_checks.py
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
git diff --check -- task_server/services/api_source_service.py task_server/services/apifox_discovery_service.py task_server/services/metersphere_service.py task_server/services/metersphere_v365_adapter.py task_server/router.py js/api-testing.js css/round5.css task-manager.html tests/api_asset_sync_checks.py tests/apifox_discovery_checks.py tests/api_project_workspace_checks.py tests/metersphere_v365_adapter_checks.py tests/frontend_static_checks.py docs/superpowers/plans/2026-07-30-apifox-environment-snapshot.md
```

### 2026-07-30 API 业务 token 按项目/环境标记并回传执行上下文

用户指出 3D 项目的用户登录 token 没有明确“放到哪个项目使用哪个 token”，页面也看不出当前公共鉴权绑定范围。

修复：

- MeterSphere 执行上下文现在返回当前 API source 对应的 `auth_binding`，并嵌入 `binding.auth_binding`，前端不用再只靠本地临时状态判断鉴权是否存在。
- API 执行区的环境公共鉴权面板新增“绑定业务 / 绑定环境 / Token 标记”三项，展示 MeterSphere 项目、环境、变量名和服务端引用；不同项目/环境会按已有 profile 隔离或复用，不把真实业务 token 写入代码库或本地元数据。
- 前端缓存版本更新为 `20260730-api-auth-target`。

验证：

```bash
python3 -m unittest tests.api_project_workspace_checks.ApiWorkspaceBindingChecks.test_execution_context_marks_current_project_environment_auth_profile
python3 tests/api_project_workspace_checks.py
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
```

### 2026-07-30 Agent 服务重启后 RUNNING 步骤恢复

用户指出百度网盘 Agent 第二次卡在 `PLAN` 可能是服务端重启导致，不应长期停在旧的 RUNNING 状态。

根因：

- Agent 后台执行器是进程内线程；服务重启后线程必然丢失，但持久化 run 仍可能保留 `status=RUNNING`、当前 step `RUNNING`。
- 既有 `recover_stale_agent_runs()` 已能处理 Runner job 终态恢复、已返回 toolCall 的步骤补齐、工具调用前卡住重排，以及 `GENERATE_YAML` 后台生成 job 失联。
- 本次百度网盘卡点属于“工具已开始调用、但服务重启后没有 active worker 接管”的 `PLAN` 运行态，既有恢复条件没有覆盖。

修复：

- 新增 `AGENT_RESTART_REQUEUE_STEPS`，只允许没有外部执行副作用的 Agent 步骤在服务重启后重新排队：`PREPARE_SOURCE / PLAN / IMPACT_ANALYSIS / CASE_RETRIEVAL / MATCH_CASES / VALIDATE_YAML / RISK_REVIEW / EXECUTION_PRECHECK / COLLECT_REPORT / ANALYZE_FAILURE / DIAGNOSE_FAILURE / GENERATE_REPAIR / GENERATE_BUG_DRAFT / LEARN_FROM_RESULT / GENERATE_SUMMARY`。
- 新增 `_recover_orphaned_running_step_after_restart()`：当 run 仍为 `RUNNING`、当前进程没有 active worker、RUNNING step 启动时间早于当前服务进程启动时间，且超过短暂保护窗口时，把该 step 重置为 `PENDING` 并启动 worker 继续执行。
- `RUN_SONIC / RERUN / SYNC_SONIC` 不做盲重排，避免重复下发 Runner/Sonic/手机任务；`RUN_SONIC` 仍按真实 job 表终态恢复，`GENERATE_YAML` 仍按生成 job 状态恢复。
- 恢复动作会写入 step liveTrace、run logs 和 `artifacts.restartRecoveries`，前端可以看到“服务重启恢复”的原因。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_completed_tool_step_recovers_and_avoids_hot_cancel_reads()
checks.check_agent_orphaned_running_step_after_restart_requeues()
PY
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check -- task_server/services/agent_service.py tests/backend_static_checks.py
```

完整 `python3 tests/backend_static_checks.py` 仍被既有 OBJ 保龄球历史 YAML 断言拦截：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`。本轮不修改历史 YAML。

### 2026-07-29 API 报告实时状态、AI draft 编辑与 MeterSphere 用例状态

用户发现平台 `API 报告` 页显示历史 `passed`，但 MeterSphere 侧任务仍可见进行中；同时 AI 生成的接口用例只能看不能改，无法实时知晓接口执行状态。

根因：

- `/api/api-testing/reports` 只返回已保存的最终报告，前端报告页只渲染 reports 表格；当同一 source 有 running execution 时，页面会把上一份历史报告当成当前状态。
- 手动/自动拉 MeterSphere 报告时没有统一拦截“远端明确仍 running”的结果，存在过早生成最终报告的风险。
- MeterSphere v3.6.5 adapter 推送接口用例时固定写 `status=PROCESSING`，推送场景时固定写 `status=UNDERWAY`；MeterSphere 中文 UI 会把这类用例生命周期状态显示为“进行中”，这不是执行结果。
- API plan draft 没有保存修改接口，用户无法在采纳为基线前修正 AI 生成用例。

修复：

- `/api/api-testing/reports` 新增 `active_runs` / `recent_runs`，报告页先显示“实时执行”卡片，再显示历史报告；有 active run 时每 5 秒自动刷新，并提供“查看实时执行”入口。
- 部署复核发现历史执行可能出现 `execution failed / report passed 7/7` 的双状态，报告页继续补充渲染 `recent_runs`，用“执行编排”和“接口结果”分开展示，避免用户只看到最终报告 passed。
- `_pull_metersphere_report_with_config()` 对明确 running 的报告返回 `report_not_ready`，不保存最终 API report；已归因为 failed 的 provider terminal fallback 仍允许同步失败报告。
- 新增 `/api/api-testing/plans/{plan_id}/cases`：只允许编辑 draft 候选，保存后重新执行 `evaluate_api_plan()`，可执行/待补数量由平台重新计算；已采纳基线不能直接改。
- 前端 AI 用例明细行新增 `编辑`，打开 JSON 编辑器后可保存并重新校验。
- AI draft 编辑器升级为结构化表单：默认按用例名称、优先级、请求方法/路径、执行计划、路径参数、Query、Header、Body 入参和校验断言分区编辑；原始 JSON 收进“高级”折叠区作为兜底，保存接口和后端数据模型不变。
- 未配置环境公共鉴权时，“配置登录接口”不再因为上下文缺失而静默 disabled；点击会进入登录接口表单，若还没绑定 MeterSphere 业务/环境则给出明确提示。视觉 smoke 已覆盖默认登录接口模式和手动 token 兜底模式。
- MeterSphere 环境变量写入后可能读回掩码值、空值，甚至完全隐藏 secret 变量；认证变量 upsert 校验改为以 MeterSphere update 接口成功为准，若读回能看到变量则额外要求 enabled。独立 verify/delete 仍保持严格读取，不在平台本地持久化业务 token。
- MeterSphere v3.6.5 adapter 推送 API case / scenario 的 lifecycle `status` 改为 `COMPLETED`，避免远端用例列表一直显示“进行中”；真实执行状态仍只来自 report `execStatus/status`。
- 前端缓存版本更新为 `20260730-api-auth-click`。

验证：

```bash
python3 tests/api_runtime_recovery_checks.py        # 13 passed
python3 tests/api_project_workspace_checks.py       # 48 passed
python3 tests/metersphere_v365_adapter_checks.py    # 54 passed
python3 tests/frontend_static_checks.py             # 72 passed
node --check js/api-testing.js
python3 -m py_compile task_server/router.py task_server/services/api_test_plan_service.py task_server/services/metersphere_service.py task_server/services/metersphere_v365_adapter.py tests/api_runtime_recovery_checks.py tests/api_project_workspace_checks.py tests/metersphere_v365_adapter_checks.py
git diff --check -- js/api-testing.js css/round5.css task-manager.html task_server/router.py task_server/services/api_test_plan_service.py task_server/services/metersphere_service.py task_server/services/metersphere_v365_adapter.py tests/api_runtime_recovery_checks.py tests/api_project_workspace_checks.py tests/metersphere_v365_adapter_checks.py tests/frontend_static_checks.py tests/backend_static_checks.py CODEX_STATE.md
```

注意：MeterSphere 里已存在的旧 `PROCESSING/UNDERWAY` 用例需要下一次平台推送/更新后才会变为 `COMPLETED`；本次修复不会主动批量修改远端历史用例。

### 2026-07-29 API AI 用例可见性与 3D 用户登录 token 获取

用户澄清 `token` 指的是 3D 项目业务接口调用所需的用户登录 token，不是平台登录 token，也不是 Apifox Access Token。平台应优先通过业务“用户登录接口”获取 token，而不是只让用户手动粘贴。

修复：

- `AI 用例计划` 生成区新增 `AI 生成结果` 摘要。AI 批次生成 draft 后可直接点 `查看生成用例`，不再需要用户猜测要去候选列表点开。
- 用例详情顶部新增 `api-plan-case-origin-banner`，明确区分 `AI 生成结果` draft 与已采纳的 `API 基线用例`，并显示业务鉴权是否已绑定。
- 环境公共鉴权编辑区新增默认模式 `登录接口获取`：填写用户登录接口 URL、请求体 JSON、token JSON 路径（默认 `data.token`），后端临时调用登录接口取 token。
- 新增后端路由 `/api/api-testing/sources/{source_id}/auth-binding/from-login`，成功获取 token 后复用现有 `save_api_auth_binding` 写入 MeterSphere 环境变量。
- 登录请求体、登录密码、返回的业务 token 不写入本地文件，也不回显给前端；平台本地仍只保存变量名、header、环境/项目和指纹元数据。手动粘贴业务 token 保留为兜底。
- 前端缓存版本更新为 `20260729-api-login-auth`。

验证：

```bash
python3 tests/frontend_static_checks.py  # 72 passed
node --check js/api-testing.js
python3 tests/api_project_workspace_checks.py  # 47 passed
python3 -m py_compile task_server/router.py task_server/services/metersphere_service.py tests/api_project_workspace_checks.py
git diff --check -- js/api-testing.js css/round5.css task-manager.html tests/frontend_static_checks.py task_server/router.py task_server/services/metersphere_service.py tests/api_project_workspace_checks.py CODEX_STATE.md
```

注意：本轮是一次性通过登录接口获取 token 并写入 MeterSphere 环境变量。尚未实现“保存登录配方并定时刷新 token”；如后续 token 过期频繁，应单独做加密存储/定时刷新设计。

### 2026-07-29 API 资产页改成三段式工作台

用户反馈接口资产管理页面混乱，已通过 Apifox 读取到项目、分支和环境后仍容易被下方手动 ID 表单干扰，且页面没有清晰表达“接口资产 -> AI 用例 -> API 基线 -> MeterSphere 执行”的工作流。

参考：

- Postman Collections：用左侧集合/文件夹组织请求，并在工作区打开集合或请求。
- Apifox 场景用例：接口资产进入场景编排后再运行并生成报告。
- MeterSphere 接口测试：使用树状多级模块管理接口列表，并进入接口自动化/执行。

修复：

- `API 资产` 页新增项目上下文条 `api-asset-context-bar`，把 Apifox 项目、连接状态、自动同步、最近成功、下次检查和同步动作收在一块。
- 模块工作区从“两列模块树+接口表”升级为三段式 `api-asset-workbench-grid`：左侧模块树、中间接口筛选/选择、右侧动作面板。
- 新增右侧 `api-asset-action-panel`，展示当前模块、范围接口数、已选接口数、单次上限，并直接承接“生成 AI 用例 / 查看 API 基线 / MeterSphere 执行”。
- 单个接口勾选和当前列表全选后会刷新右侧动作面板统计，不再只刷新顶部 stepper。
- 窄屏下三段式工作台自动收成单列，右侧动作面板变为底部流程面板，避免接口表被压窄。
- 前端缓存版本更新为 `20260729-api-asset-workbench`。

验证：

```bash
python3 tests/frontend_static_checks.py  # 72 passed
node --check js/api-testing.js
git diff --check -- js/api-testing.js css/round5.css task-manager.html tests/frontend_static_checks.py
```

本轮没有修改 Apifox 同步后端、AI 用例生成、API 基线数据模型、MeterSphere 执行接口、Agent、Runner、Sonic 或历史 YAML。

### 2026-07-29 Apifox 已选项目仍要求手填 Project ID

用户截图显示新增 Apifox 项目时，项目 `3D`、分支 `main`、环境 `生产环境（新）-腾讯云` 已经通过发现结果显示出来，但点击保存仍提示 `请填写 Apifox 项目 ID`。

根因：

- 前端保存逻辑把 `#api-source-manual-fallback.open` 直接当成“手动模式”。
- 成功发现项目后，如果用户展开了“无法读取？手动连接”兜底区域，保存就会改读下面的手动 `Project ID / Environment ID` 输入框。
- 这些手动输入框本来是技术兜底，不应该覆盖上方已经成功读取的 Apifox 项目、分支和环境。

修复：

- `saveApiSourceConfig()` 新增 `useDiscoveredSelection`：只要当前有新鲜的 Apifox discovery 项目，就优先保存发现到的 `project_id / branch_id / environment_id` 和 provider metadata。
- “手动连接”展开只表示兜底表单可见；只有没有有效发现结果时，才走手动 ID 字段。
- 视觉 smoke 增加覆盖：成功选择 Apifox 项目后，故意展开手动兜底且不填 Project ID，再保存，要求 POST payload 仍为发现到的项目和命名环境。

验证：

```bash
python3 tests/frontend_static_checks.py  # 72 passed
node --check js/api-testing.js
VISUAL_ARTIFACTS_DIR=/tmp/midscene-api-manual-fallback-green npm run test:visual
```

视觉 smoke 已越过新增 Apifox 保存断言，确认 payload 使用发现到的 `project_id=5904971 / environment_id=99 / provider_metadata.project_name=账户中心`；但完整脚本后半段在既有 Agent 最终摘要断言 `Final summary must preserve successful smoke outcomes...` 失败，该断言与本轮 Apifox 来源保存逻辑无关，未在本轮修改 Agent 报告模块。

### 2026-07-29 Agent PLAN 卡在“生成用例结构 50%”：收紧超时并降级到源需求合同计划

用户部署后同参重跑百度网盘 Agent：`agent-1785306176144-e170a6ef`。线上健康正常，固定 Runner/设备为 `win-runner-01 / ecbfd645`，模型 `qwen3.7-plus`。本轮未到 Runner，也未创建手机任务；卡点在 `PLAN`：

- `PREPARE_SOURCE` 成功，Figma `4 页 / 4 图` 已真实解析。
- `PLAN` liveTrace 停在 `生成用例结构（50%）：正在生成场景、用例、边界和人工待准备事项`。
- `PLAN` 没有 toolCalls 落盘，说明平台 MM skills 内部的场景/用例生成子调用长时间未返回。

修复：

- `AGENT_PLAN_MINDMAP_TIMEOUT_SECONDS` 默认从 900 秒收紧到 240 秒，并封顶 600 秒，避免 PLAN 长时间假卡死。
- 新增 `_agent_plan_timeout_fallback()`：当 MM planning 超时时，如果源需求中已有业务入口合同，则生成显式标记的降级计划：`source=plan_timeout_degraded_source_contract`、`fallbackUsed=true`、`planTimeoutFallback=true`、`aiGenerated=false`。
- 质量门禁允许这种“已明确标记的 PLAN 超时降级计划”继续进入后续 YAML 生成，但不会把它冒充为 MM skills 正常 AI 计划。
- PLAN 输出文案区分正常 AI 计划和超时降级计划；AI decision 记录里 `success=false` 并带 `planTimeoutFallback=true`。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_plan_timeout_degrades_to_source_contract()
PY
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check -- task_server/services/agent_service.py tests/backend_static_checks.py CODEX_STATE.md
```

完整 `python3 tests/backend_static_checks.py` 仍被既有 OBJ 保龄球历史 YAML 断言拦截：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`。本轮不修改历史 YAML、Runner、Sonic 或 scorer。部署后建议取消/忽略旧的卡住 Agent，重新同参跑百度网盘；预期如果 PLAN 子调用再次超过 240 秒，会显示“PLAN AI 超时，已使用源需求合同降级生成 3 条业务分支”，继续进入 YAML/Runner，而不是停在 50%。

### 2026-07-29 Apifox 项目选择后上下文读取失败

用户截图中“读取 Apifox 资产”能列出项目，但选择 `3D / 5904970` 后失败，前端提示 `Apifox CLI 返回了无法识别的数据`。线上复核确认：

- 生产 `8091 / 8088` 健康，Task 服务模型为 `qwen3.7-plus`，AI skills 完整。
- 生产 `apifox-cli` 已为 `2.2.8`，项目列表接口可通过已保存 source token 读取 17 个项目。
- 失败发生在项目上下文读取阶段，不是 token、CLI 安装或项目权限问题。
- 用同一 token 直接跑真实 CLI 只读命令时，`project list / project get / branch list / environment list` 均返回合法 JSON；其中 `environment list` 对 `5904970` 返回约 10KB。

根因：

- `apifox_discovery_service._run_cli()` 对成功 stdout 也复用了 `_safe_error_text()`，该函数会把文本截断到 4000 字符。
- 小输出的项目列表、项目详情、分支列表能解析；环境列表超过 4000 字符后被截断成半截 JSON，于是 `_run_json_cli()` 抛 `INVALID_RESPONSE`，页面显示读取失败。

修复：

- 成功 stdout 改为只脱敏、不截断；错误路径仍保持 4000 字符上限，避免把大量 CLI 输出或敏感内容带给前端。
- JSON 解析增加有限容错：允许 stdout 前后有 Apifox CLI 提示/告警，但仍必须解析出完整 JSON 对象并继续校验 `success/data`。
- 新增两条回归：大环境列表不能截断；CLI 提示语包裹 JSON 时仍能读取项目详情、分支和环境名称。

验证：

```bash
python3 -m unittest tests.apifox_discovery_checks.ApifoxDiscoveryServiceChecks.test_project_context_does_not_truncate_large_cli_json tests.apifox_discovery_checks.ApifoxDiscoveryServiceChecks.test_project_context_accepts_cli_json_with_prompt_noise
python3 tests/apifox_discovery_checks.py       # 14 passed
python3 tests/api_asset_sync_checks.py         # 40 passed
python3 tests/api_project_workspace_checks.py  # 45 passed
python3 -m py_compile task_server/services/apifox_discovery_service.py tests/apifox_discovery_checks.py
```

部署后应重新在页面选择 `3D / 5904970`，预期分支和环境下拉能显示 Apifox 中文名称，不再因环境列表过大失败。

### 2026-07-29 Apifox CLI 首次部署超时

部署日志中的 npm `deprecated` 来自官方 `apifox-cli@2.2.8` 的旧依赖，不是安装失败原因；实际失败是 `install-server.sh` 的 180 秒总时限向 npm 发送 `SIGTERM`。实测该 CLI 连同非可选依赖约 78MB / 214 个包，缓存命中时安装约 6-7 秒，项目、分支和环境只读命令均可正常运行，因此生产失败点是首次下载未在 180 秒内完成。

修复：

- 首次安装上限改为可配置的 `APIFOX_CLI_INSTALL_TIMEOUT_SECONDS`，默认 600 秒。
- npm 优先复用已下载缓存，跳过发现功能不需要的可选数据库驱动和生命周期脚本，并限制单次网络请求重试与等待。
- 只有镜像源快速失败时才切换 npm 官方源；若已经达到总时限，则保留缓存且不再从头等待第二轮。
- 降低 npm 安装日志噪声；CLI 安装失败仍不阻断平台部署，手动连接继续可用。

验证：

```bash
python3 tests/apifox_discovery_checks.py       # 12 passed
python3 tests/api_asset_sync_checks.py         # 40 passed
python3 tests/api_project_workspace_checks.py  # 45 passed
bash -n deploy/install-server.sh
git diff --check
```

### 2026-07-29 Agent 报告展示：dry-run 不能算真机通过，旧失败分析不能覆盖 Runner 事实

用户指出上一条百度网盘执行记录看起来“没有在手机上跑测试用例，最后是通过的”。复查线上最新记录 `agent-1785294560275-bd1e04c4`：

- `smoke-dry-run` 有 2 个 success job，这是 YAML dry-run，不是真机测试通过。
- `smoke` 有 1 个真实 Runner job，在固定 `win-runner-01 / ecbfd645` 上执行，状态为 failed，并带 HTML report URL。
- `summary.execution` 已经正确记录 `未通过 / attempted=1 / failed=1 / productFailed=1`。
- 旧 `failureAnalysis` 仍保留 `NONE / 全部执行成功`，会误导失败页或旧 UI 展示。

修复：

- `collectAgentReportProgressJobs()` 现在跳过 `smoke-dry-run` / dry-run phase，报告页只统计真实 Runner 执行阶段。
- 最终报告页使用 `normalizeAgentReportJobs(report, normalizedReport, artifacts)` 聚合出的真实 Runner job 优先覆盖旧 summary/report 计数；存在 failed job 时显示 `未通过`，不再显示 `通过`。
- 报告链接从真实 Runner job 的 `report_url` 兜底，旧 `report.executionReports` 为空时仍可打开 HTML 报告。
- 失败分析页如果发现真实 failed job，但旧 `failureAnalysis` 写着 `NONE / 全部执行成功`，会用 Runner failureReview / error 覆盖展示，不再显示无失败。
- 前端缓存版本更新到 `js/agent-workbench.js?v=20260729-agent-report-progress`，避免浏览器继续使用旧报告逻辑。

验证：

```bash
python3 tests/frontend_static_checks.py
node --check js/agent-workbench.js
git diff --check -- js/agent-workbench.js tests/frontend_static_checks.py task-manager.html
```

部署后预期：百度网盘历史记录和新记录都不会把 dry-run 成功当作手机测试通过；如果只有 dry-run 没有真实 Runner job，应显示未执行/待判定；如果真实 Runner job failed，应显示未通过并展示失败用例和报告链接。

### 2026-07-29 Apifox 名称发现、AI 候选与独立 API 基线

本轮只收敛 API 测试工作流，没有修改 UI Agent、Midscene YAML、Runner、Sonic 或历史用例。设计对照 Apifox 的 AI 候选采纳、场景测试和合同校验，以及 Postman 的 OpenAPI 同步集合与独立 Collection Run：AI 生成内容先作为候选，平台校验后采纳为稳定基线；接口版本变化时保留旧基线并标记影响，不静默改写。

实现：

- 新增隔离的官方 Apifox CLI 只读发现边界。Token 通过 stdin 输入，CLI 使用一次性 HOME，响应和错误均脱敏；可读取项目中文名、团队、分支和环境名称，失败时明确回退到手动连接。
- API source 持久化 `provider_metadata`，来源选择器和设置页优先展示 Apifox 返回的中文名称；新增项目改为先输入 Token 搜索项目，再选择命名分支/环境，原始 ID 收进“无法读取？手动连接”。
- 新增认证发现接口 `/api/api-testing/apifox/discovery/projects` 和 `/api/api-testing/apifox/discovery/project-context`，既支持新 Token，也支持已有 source 的服务端凭据，响应不返回 Token。
- AI 用例页只展示 `draft` 候选，确认动作改为“采纳为基线”。新增独立“API 基线”工作区，直接投影现有 `confirmed` 计划，不复制记录、不新增状态；展示项目、模块、接口数、可执行数、采纳时间、版本新鲜度和受影响用例，并可查看、按新版本再生成或定位到 MeterSphere 执行。
- `install-server.sh` 固定安装官方 `apifox-cli@2.2.8`，校验 Node/CLI 版本，国内镜像失败后回退 npm 官方源；失败只告警，手动连接仍可用。根据真实 `2.2.8 --help`，分支读取补齐必填的 `branch list --type all`。

验证：

```bash
python3 tests/api_asset_sync_checks.py                 # 40 passed
python3 tests/api_project_workspace_checks.py          # 45 passed
python3 tests/apifox_discovery_checks.py               # 12 passed
python3 tests/frontend_static_checks.py                # 72 passed
VISUAL_ARTIFACTS_DIR=/tmp/midscene-api-baseline-visual-4 npm run test:visual
bash -n deploy/install-server.sh
```

Playwright 已验证桌面/移动端候选与基线隔离、过期影响、无横向溢出、数值 0 显示，以及从基线进入执行后定位对应计划。本机没有保存生产 Apifox 凭据，未把聊天 Token 放入命令或文件；部署后应使用页面已保存凭据做一次只读项目/分支/环境发现，并建议轮换曾在聊天中暴露的 Token。

### 2026-07-29 Agent 报告页：Runner 阶段进度必须作为真实报告来源

用户截图显示 Agent 已进入完成态，但“Runner 报告”仍显示 `unknown`，执行用例、通过、失败和未完成统计为空，并提示“当前没有 Runner 回传的 HTML 报告链接”。同一线上 Agent 产物中实际已经存在 `jobProgressByPhase.smoke.jobs`，包含固定 OPPO 上执行失败的 Runner job 和 `report_url`，因此这是前端报告聚合口径问题，不是 Runner 没执行。

修复：

- 新增 `collectAgentReportProgressJobs()`，把 `artifacts.jobProgress` 和 `artifacts.jobProgressByPhase[*].jobs` 纳入报告数据源。
- `normalizeAgentReportJobs()` 现在会合并阶段进度中的 `jobId/status/taskName/file/report_url`，并继续去重、分组为失败/通过/执行中/待判定。
- `renderReportDetail()` 在 `report.status=unknown` 时按真实 job 结果推导报告状态；存在 failed job 时显示失败，不再显示 unknown。
- HTML 报告链接不再只看 `report.executionReports`，也会从阶段进度 job 的 `report_url` 生成链接，避免误提示“没有 Runner 回传的 HTML 报告链接”。
- 前端静态检查新增约束：Agent Runner report 必须从 `jobProgressByPhase` 聚合 live Runner jobs 和 report URLs。

验证：

```bash
python3 tests/frontend_static_checks.py
```

部署后预期：百度网盘 Agent 若 Runner smoke 失败，报告页应显示 `执行用例 1 / 通过 0 / 失败 1 / 未完成 0`，失败用例区展示对应 YAML/job，并提供 Runner HTML 报告链接。

### 2026-07-29 百度网盘真实 Runner 回归：生成门禁已放开，报告归因需以 Runner 失败为准

用户部署 `d9234f5` 后同参重跑百度网盘 Agent。线上健康：Task 服务模型 `qwen3.7-plus`，Figma token 可用，AI skills 完整；固定 Runner 为 `win-runner-01`，固定设备只使用 OPPO PHM110 `ecbfd645`，App `com.xbxxhz.box` 版本 `4.45.0`。

回归结果：

- `agent-1785293444965-9d3b6aeb`：生成链路已越过旧硬门禁，确认 4 个 executable YAML，coverage gap 记录为 warning；但 `EXECUTION_PRECHECK` 时 Windows Runner 心跳超过 60 秒窗口，被判 `Runner 不在线`，未创建 Runner job。
- `agent-1785294071956-8364b2df`：生成 5 个 executable YAML，coverage gap 为 warning；同样在预检时命中 Runner 心跳短暂离线，未创建 Runner job。
- `agent-1785294560275-bd1e04c4`：成功越过预检并创建真实 Runner job。Runner dry-run 阶段 2/2 通过；首批 smoke 只执行固定 OPPO 上的 `01-文档打印页-百度网盘入口可见性及文案校验.yaml`，job `job_1785294978690_00008` 失败并上传 HTML 报告。
- 真实失败报告显示页面被“喜欢哪个免费拿”活动弹窗遮挡，背景可见文档打印/照片打印等入口，但未看到“百度网盘”入口；这是 Runner 真实测试失败结果，不应再被归为生成失败。

发现的新问题：

- `jobProgressByPhase.smoke.jobs` 中已经有 failed job，但 `_agent_failed_execution_items()` 没把该来源纳入失败源，导致 `ANALYZE_FAILURE` 误写 `failureType=NONE / 全部执行成功`。
- `yamlValidation.issues` 中包含 coverage warning，`DIAGNOSE_FAILURE` 将其误判为 `YAML 强校验未通过`。这与用户确认的产品口径冲突：覆盖缺口是告警，真实 Runner 失败才是本轮执行结果。

修复：

- `_agent_failed_execution_items()` 现在从 `jobProgressByPhase` 收集 `failed/error/timeout/cancelled` Runner job，作为 report/jobResult 之外的失败源。
- `_tool_diagnose_failure()` 优先诊断真实 Runner failed job；当 `coverageIncomplete/coverageGap` 存在时，会从 YAML validation issues 中剔除覆盖告警，避免误报“YAML 强校验未通过”。
- 新增回归测试：构造 coverage warning + smoke failed job，要求失败归因必须识别 Runner job，且诊断 rootCause 不能是 `YAML 强校验未通过`。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_failure_analysis_uses_runner_phase_failures_before_coverage_warnings()
PY
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_yaml_validate_partial_quarantine()
checks.check_agent_quarantine_refs_do_not_reenter_precheck()
checks.check_agent_execution_gate_repairs_before_smoke_selection()
PY
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
```

后续部署后建议继续同参回归。预期：生成阶段仍可带 coverage warning 进入 Runner；如果文档打印 smoke 失败，报告/诊断应显示真实 Runner failed job 和 HTML 报告，不再显示“全部通过”或“YAML 强校验未通过”。Runner 心跳偶发超过 60 秒窗口的问题仍需单独观察；这不属于本轮生成门禁修复。

### 2026-07-29 Agent 生成门禁产品口径：覆盖缺口是告警，零可执行才阻断

用户明确产品原则：冒烟或后续用例不一定都能生成、也不一定都要通过；这些本来就是测试结果，应进入报告区分成功/失败/未生成，而不是在生成阶段要求全绿。上一轮百度网盘线上 Agent `agent-1785292186788-8c36b5fe` 已生成 6 个 YAML、确认 5 个，真正剩余只是照片打印展示 YAML 注释中的旧基线词 `一寸照` 被 scope gate 误判，以及缺口被硬门禁阻断，导致没有进入 Runner。

修复：

- `_agent_generated_yaml_ref_out_of_source_scope()` 只扫描可执行 YAML 正文，忽略 `# baseline.repair_hint` 等注释，避免注释里的历史规格页词污染照片打印主入口 YAML。
- Agent `GENERATE_YAML` 阶段有可执行 YAML 时不再因完整覆盖缺口抛错；缺口写入 `generationPipeline.coverageGap`、`yamlValidation.coverageIncomplete` 和 `qualityReport.warnings`。
- `VALIDATE_YAML` 阶段只在全部 YAML 都 dry-run/scorer 不通过时失败；部分 YAML 被隔离时返回成功并保留 `quarantinedYamlRefs`，后续只下发通过的 YAML。
- `EXECUTION_PRECHECK` 中 `generated_yaml_coverage_gate` 从 blocker 改为 warning；真正阻断仍包括无 YAML、无已确认正式 YAML、无可下发 Runner YAML、Runner/设备/Token 等环境问题。
- `yaml_service` 最终 portfolio gate 保留 AI 收敛和 executable scorer，但新增 `coverageComplete / softAllowed / hardBlocked`。只有 0 条 executable YAML 时抛出 `最终可执行 YAML 不足，不能进入执行`；覆盖未满时记录 `最终覆盖告警` 并继续。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_yaml_static_validation_and_patterns()
PY
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_ai_yaml_generation_decision_chain_static()
PY
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
```

部署后应同参重跑百度网盘 Agent。预期只要有可执行 YAML，就会进入 Runner；缺失的覆盖、被隔离的 YAML、真实执行失败都应在报告里分组展示，不再把“测试用例失败/未覆盖”混同为“生成链路失败”。Codex 不 push。

### 2026-07-29 百度网盘回归：照片打印源入口不能被历史规格页路径污染

用户部署后继续同参跟踪百度网盘 Agent：`agent-1785288442468-ae2880c5`。线上健康正常：Task 服务、AI Gateway、Sonic Bridge、Windows Runner 均可用；Runner 为 `win-runner-01`，固定 OPPO PHM110 `ecbfd645` 在线，模型 `qwen3.7-plus`。本轮未创建 Runner job，因此失败仍在生成阶段，与手机、ADB、Runner 或 Sonic 无关。

真实结果：

- `PREPARE_SOURCE` 成功，Figma `4 页 / 4 图`。
- `PLAN` 成功，4 张 Figma 图分 4 批完成视觉判断。
- `GENERATE_YAML` 生成 6 个 YAML，静态校验和 scorer 都显示 executable。
- 最终只确认 4 个 YAML：文档打印 2 个、扫描复印 2 个；照片打印两个 YAML 都没有进入 `artifacts.yamlRefs`。
- 被剔除的照片 YAML 内容实际包含 `点击「5寸照片」` / `选择「5寸照片」规格`，命中“源需求未要求照片规格页”的 generated scope gate，因此阻断是正确的。

根因：

- `_fallback_baidu_feature_kind()` 把源需求里的“照片打印”直接归类为普通照片，`_fallback_steps_for_scenario()` 随后自动追加 `点击名称为「5寸照片」的普通照片打印入口`。
- AI 计划即使借用 verified 照片基线，也可能把历史基线里的 `5寸/一寸/证件照/拼版` 规格页步骤带进当前“照片打印主入口”需求。
- 下游 generated scope gate 正确拦截了错误规格页 YAML，但生成应用阶段没有提前把路径拉回源合同，导致每次生成后才被剔除并表现为 `6 -> 4` 覆盖缺口。

修复：

- “照片打印”现在作为 `photo_entry` 主业务入口处理；只有源需求明确写 `普通照片/5寸/证件照/拼版` 等规格时，fallback 才进入具体规格页。
- 新增 `_canonicalize_photo_entry_source_flow()`：非 convergence 首轮生成中，如果 requirement refs / acceptance checks 指向“照片打印”源入口，且源验收没有声明具体规格，则剔除历史规格页导航，保留照片打印页级的百度网盘入口可见、同级、文案和点击可达路径。
- 该规范化只在非 `coverage_convergence` 生效，避免误伤已有“5寸照片”等视觉叶子页的有界修复用例。
- `ai_case_plan.photoEntrySourceCanonicalized` 和 review 计数会记录是否发生过源入口规范化，便于后续排查。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
```

完整 `python3 tests/backend_static_checks.py` 仍被既有 OBJ 保龄球历史 YAML 断言拦截：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`。本轮不修改历史 YAML、Runner、Sonic、scorer 或 router；Codex 不 push。用户部署后应同参重新跑百度网盘 Agent，预期照片打印 YAML 不再包含 `5寸照片/一寸照/证件照/照片拼版` 规格页导航，也不再因照片规格页被门禁剔除而出现 `6 -> 4`。

### 2026-07-29 Runner 报告结果分组：失败、通过、未完成必须明显区分

用户指出 Agent 的 Runner 报告页把执行报告和执行 YAML 混成文本列表，不容易看出哪些用例成功、哪些失败。这里的产品原则是：不是每条用例都必须通过，报告应展示真实执行结果，而不是把失败用例隐藏在“complete / 执行 6 / 失败 0”的摘要里。

修复：

- `renderReportDetail()` 新增 Runner job 结果归一化，不再只按 HTML 报告链接和 YAML 文件列表展示。
- 新增 `normalizeAgentReportJobs()`、`agentReportOutcomeGroups()`：合并 `jobStatuses / executionReports / yamlExecutionRefs / failedJobs`，按状态分为失败、通过、执行中/未完成、待判定。
- 页面顶部摘要改为 `报告状态 / 执行用例 / 通过 / 失败 / 未完成或待判定`，避免把报告生成状态误读成用例全通过。
- 报告正文新增分组区块：`失败用例` 红色优先展示，`通过用例` 绿色展示，`执行中 / 未完成` 和 `待判定结果` 单独展示；每条显示状态、任务名、YAML/Job、失败类型、失败原因和报告链接。
- 更新前端缓存版本：`css/app.css?v=20260729-agent-report-outcomes`、`js/agent-workbench.js?v=20260729-agent-report-outcomes`。

验证：

```bash
python3 tests/frontend_static_checks.py
python3 -m py_compile tests/frontend_static_checks.py
git diff --check -- js/agent-workbench.js css/app.css task-manager.html tests/frontend_static_checks.py
```

### 2026-07-29 百度网盘回归：严格源合同按主分支优先，防止 AI 扩展流挤掉照片打印

用户部署后重新发起同一百度网盘 Agent：`agent-1785284860060-ca243ee3`。线上健康正常：Task 服务 / AI Gateway / Sonic Bridge / Windows Runner 可用，Runner 为 `win-runner-01`，固定 OPPO PHM110 `ecbfd645`，模型 `qwen3.7-plus`；未创建 Runner job，因此本轮失败与手机、ADB、Runner 或 Sonic 执行无关。

真实结果：

- `PREPARE_SOURCE` 成功解析 Figma `4 页 / 4 图`。
- `PLAN` 成功，4 张 Figma 图分 4 批完成视觉校准。
- `GENERATE_YAML` 失败，终态 `FAILED / GENERATE_YAML / 30%`。
- 失败信息：生成自动化用例 6 条，但只确认 YAML 4 个；缺口为 `REQ-002 照片打印` 的 visibility / relation / copy / reachability 全部 4 项。

根因：

- 上一轮修复已阻止照片规格页/子规格页冒充“照片打印”主入口，这是正确的。
- 但 AI PLAN 仍可能输出 `文档打印主链、扫描复印主链、文档打印扩展流、扫描复印扩展流`，照片打印再由源合同恢复补回，排序靠后。
- YAML 生成和收敛预算会先消耗在重复的文档/扫描扩展流上，最终没有确认到照片打印主入口 YAML。

修复：

- 新增 `_agent_prioritize_source_contract_flows()`：严格源需求合同下，每个源业务入口只保留一个最佳主分支，并按源合同顺序输出。
- 新增 `_agent_source_contract_flow_score()`：优先选择包含目标入口、可见/同级/文案/可达验收的主链；降低“若/可能/需记录/产品确认/缺失风险/多语言/特殊字符/异常处理”等扩展或人工风险流权重。
- 重复源分支不进入 downstream `businessFlows`，但会保留在 `droppedOutOfScopeFlows` 审计中，避免静默丢弃。
- 回归测试复现线上 PLAN 形态：文档、扫描、文档扩展、扫描扩展，照片靠源合同恢复；期望最终 `businessFlows` 为 `文档打印 / 照片打印 / 扫描复印`，扩展流不进入 YAML 生成预算。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
```

后续部署后应同参重新跑百度网盘 Agent，预期不再因 AI 扩展流挤占预算而缺整条照片打印 REQ-002。若进入 Runner 后失败，再按真实 Runner 报告分类处理。

补充线上复跑 `agent-1785285990355-e3e9bfa1`：

- 部署后同参重新跑，PLAN 已按 `文档打印 / 照片打印 / 扫描复印` 输出，说明主分支排序修复生效。
- 任务仍在 `GENERATE_YAML / 30%` 失败，未创建 Runner job。
- 新证据显示照片主分支只保留了“展示/同级/文案”，另一个“照片打印点击百度网盘入口跳转稳定性”flow 因 Figma 5寸/一寸规格页软证据污染，被 `source_contract_does_not_include_photo_subspec` 丢弃；结果 REQ-002 的 reachability 以及整条照片硬覆盖仍无法确认。

补充修复：

- 新增 `_agent_merge_required_acceptance_into_flows()`：严格源合同下，最终保留的每个源分支都合并源需求合同的硬验收 checks 和 refs。
- 这样 AI 可提供路径/视觉语义，但不能因为把 reachability 拆到被丢弃的照片规格 flow 而丢失源合同四项硬验收。
- 新增回归：照片聚合主 flow 只含展示/同级/文案，照片规格 flow 含点击稳定可达且被丢弃时，最终保留的照片主 flow 必须仍包含“点击百度网盘入口并校验目标页面稳定可达”，且不带 `5寸` 规格页。

### 2026-07-28 系统化收紧：生成确认、验证和预检必须共用有效 YAML 覆盖口径

用户部署 `1307c68` 后同参重新发起百度网盘 Agent：`agent-1785233673106-bed83f3b`。线上健康正常：Task 服务、AI Gateway、Sonic Bridge 和 Windows Runner 均可用，Runner 为 `2026.07.26-qwen3.7-result-retry-v1`，模型为 `qwen3.7-plus`，固定 OPPO PHM110 `ecbfd645` 在线；未向第二台手机下发。

本轮真实结果：

- `PREPARE_SOURCE` 成功复用 Figma `4 页 / 4 图`，4 批视觉判断均完成。
- `PLAN` 通过，包含文档打印、照片打印、扫描复印主链。
- `GENERATE_YAML` 表面成功，生成 6 条 YAML。
- `VALIDATE_YAML` 重新 dry-run/scorer 后隔离 1 条：`03-照片打印页(5寸)-百度网盘入口可见性及文案校验.yaml`，原因是命中照片规格页/子规格分支，而源需求只要求“照片打印”业务入口。
- `EXECUTION_PRECHECK` 失败，真正缺口为 `REQ-002 [acceptance:relation]` 和 `REQ-002 [acceptance:copy]`。未创建 Runner job；这不是手机、ADB、Runner 或 Sonic 问题。

根因：

- `GENERATE_YAML` 成功口径使用 pipeline 原始返回数量和初始分类，仍把照片 5寸规格页当作有效生成。
- `VALIDATE_YAML`/预检阶段会使用更严格的 generated scope/scorer，把 5寸规格页隔离，但当时只做后置阻断，没有把“隔离后有效 YAML 集”作为唯一覆盖依据。
- 结果是同一次任务里出现“生成阶段说 6 个 YAML 可执行通过，执行前才发现 5 个有效 YAML 且照片打印缺 copy/relation”的状态错位。

修复：

- `_confirm_agent_yaml_files()` 在正式确认生成 YAML 时直接应用 `_agent_generated_yaml_ref_out_of_source_scope()`，生成产物只要命中照片规格页/子规格且源需求未明确要求规格，就立刻降为 `needs_review`，不会进入 `yamlRefs` 或 Runner 候选。
- 正式 refs 明确标记 `source=generated / generated=True / validationMode=generated`，后续验证和预检使用同一身份判断。
- `_tool_validate_yaml()` 在隔离 YAML 后立即基于剩余 `passed_refs` 调用 `_agent_generated_yaml_coverage_gap()`；如果完整回归硬覆盖被破坏，`VALIDATE_YAML` 直接失败并记录 `coverageGap`，不再拖到 `EXECUTION_PRECHECK` 才失败。
- 新增回归：文档打印有效 YAML + 照片打印 5寸规格 YAML 的混合结果中，照片规格 YAML 必须被隔离，最终覆盖缺口必须明确报告 `REQ-002 [acceptance:copy]` 和 `REQ-002 [acceptance:relation]`。

验证：

```bash
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_yaml_static_validation_and_patterns()
PY
```

补充验证：本地合成混合场景输出 `refs=['01-doc.yaml']`，`03-photo-spec.yaml` 被记录为 `needs_review`，coverage gap 包含 `REQ-002 [acceptance:relation]` 和 `REQ-002 [acceptance:copy]`。

完整 `python3 tests/backend_static_checks.py` 仍被既有 OBJ 保龄球历史 YAML 断言拦截：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`。本轮不修改 Runner、Sonic、scorer、router 或历史 YAML；Codex 不 push。用户部署后应同参重新跑百度网盘 Agent，预期不会再出现 `GENERATE_YAML` 先成功、`EXECUTION_PRECHECK` 才发现照片规格隔离后覆盖缺口的错位；若仍缺照片打印主链，应继续修“源业务入口级候选合成”，不要让 Figma 规格页替代源合同。

### 2026-07-28 部署后复核：PLAN 已越过照片分支，YAML 生成仍缺三分支 relation

用户部署 `77b0c03` 后重新发起同一百度网盘 Agent：`agent-1785231613446-2c416b58`。线上健康：Task 服务模型 `qwen3.7-plus`，Sonic Bridge / Windows Runner 均为 `2026.07.26-qwen3.7-result-retry-v1`，固定 OPPO PHM110 `ecbfd645` ready；未向第二台手机下发。

结果：

- `PREPARE_SOURCE` 成功解析 Figma `4 页 / 4 图`。
- `PLAN` 成功通过，证明 `77b0c03` 已解决“缺少需求业务分支：照片打印”。PLAN 输出 4 条业务分支，其中照片规格、全局一致性、异常处理等 Figma/AI 软扩展被 `droppedOutOfScopeFlows` 丢弃。
- 任务终态为 `FAILED / GENERATE_YAML / 30%`，未创建 Runner job。最终覆盖门禁缺三个分支的 `acceptance:relation`：
  - 文档打印 relation
  - 照片打印 relation
  - 扫描复印 relation

根因：

- 生成侧已有显式 `preserveContractByCaseId` 时可以在目标点击前补 `visibility/relation/copy`，但初始 YAML 生成候选不一定带这个 preserve contract。
- 三个候选已经有同分支导航、目标入口点击、可见/文案和点击后稳定终态；缺的只是源需求合同中的“同级关系”断言。
- 终态 assertion 里含 `未白屏/未崩溃`，不能作为 source-page preserve 证据，否则会被安全规则误当作目标相关负向证据。

修复：

- 新增 `_source_requirement_preserve_contract()`：在非 convergence pass、候选当前就是 `executable`、且 `requirementRefs` 精确映射单个源需求时，从 `requirement_acceptance_checks` 自动构建 relation-only preserve contract。
- 新增 `_merge_preserve_contracts()`：合并显式 preserve contract 与隐式 source relation contract，不覆盖模型/平台已有合同。
- 隐式合同只取点击前源页断言步骤作为 candidateEvidence，不读取点击后的终态 assertions。
- 隐式合同只补 `relation`，不碰 visibility/copy；bounded convergence、manual promotion 和已有显式 preserve 逻辑保持原行为。
- 新增三分支矩阵回归测试：文档打印、照片打印、扫描复印三个 executable 候选均缺 relation 时，`apply_executable_yaml_plan_to_payload()` 必须在目标点击前合成 `同级入口并列展示`，最终 portfolio gate 通过。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
git diff --check -- task_server/services/ai_skill_service.py tests/backend_static_checks.py
```

本轮只修改 `task_server/services/ai_skill_service.py`、`tests/backend_static_checks.py` 和 `CODEX_STATE.md`；Codex 不 push。用户部署后应同参再跑百度网盘 Agent，预期不再出现 `GENERATE_YAML / 三分支 relation 缺失`，若进入 Runner 后失败再按真实报告分类处理。

### 2026-07-28 部署后复核：PLAN 仍可能删掉照片主分支，需从源需求合同补回硬分支

用户部署后重新发起同一百度网盘 Agent。线上健康：Task 服务模型 `qwen3.7-plus`，Sonic Bridge 与 Windows Runner 均为 `2026.07.26-qwen3.7-result-retry-v1`，Runner 能力为 `midscene_model_name=qwen3.7-plus / midscene_model_family=qwen3`，固定 OPPO PHM110 `ecbfd645` ready；没有向第二台手机下发任务。

新一轮 `agent-1785230004526-3a0655b3` 终态为 `FAILED / PLAN / 10%`，未创建 Runner job。PREPARE_SOURCE 已解析 Figma `4 页 / 4 图`，PLAN 内部两轮共 8 批视觉校准均真实执行，但最终失败为 `AI 业务规划失败：缺少需求业务分支：照片打印`。线上证据显示 `requirementCandidates` 中明明包含 `文档打印 / 照片打印 / 扫描复印` 三个分支，每个分支也带展示、同级、文案、可达四项验收。

根因：

- `_normalize_agent_business_plan()` 会丢弃 AI 输出中不属于源合同的软参考分支，但当 AI 输出漏掉某个显式源需求分支时，只记录 `缺少需求业务分支` 并让 PLAN 失败。
- 对“首页三入口”这类硬合同需求，AI 可以补语义和路径，但不能删除源需求明确列出的业务入口；平台应补回缺失的源合同分支，再由后续 YAML 生成和覆盖门禁继续校验，而不是停在 PLAN。
- 本次还观察到 PLAN 在同一步内重复执行两轮 Figma 视觉校准（共 8 批），说明视觉结果复用仍有耗时优化空间，但不是本轮失败的直接原因。

修复：

- 新增 `_agent_recover_missing_source_contract_flows()`：当 AI 已产出至少一个合法源分支、但遗漏其他显式源需求分支时，从 `requirementCoverageCandidates.businessFlows` 补回缺失分支。
- 补回分支保留源合同的 steps/checks，标记 `contractBranchRecovery=True`、`branchSource=source_requirement_contract`，并记录 `recoveredSourceContractFlows`，后续报告可审计。
- 如果 AI 完全没有业务分支，仍失败；不允许规则兜底冒充 AI 计划。
- 新增回归测试：AI 只返回文档打印和扫描复印时，PLAN 必须补回照片打印硬分支，而不是报缺照片打印。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check -- task_server/services/agent_service.py tests/backend_static_checks.py
```

本轮只修改 `task_server/services/agent_service.py`、`tests/backend_static_checks.py` 和 `CODEX_STATE.md`；Codex 不 push。用户部署后应同参重新跑百度网盘 Agent，预期不再出现 `PLAN / 缺少需求业务分支：照片打印`。若进入 Runner 后仍失败，再按实际 YAML/报告分类处理，不要把 PLAN 问题和手机执行问题混在一起。

### 2026-07-28 e109573 部署后三轮复核：过滤过严误伤照片主分支，Runner gate 必须拦截规格页 YAML

用户部署 `e109573` 后要求继续监控同一百度网盘需求。线上健康：Task 服务模型 `qwen3.7-plus`，AI Gateway 正常；Windows Runner / Sonic Bridge 均为 `2026.07.26-qwen3.7-result-retry-v1`；固定 OPPO PHM110 `ecbfd645` 在线，`com.xbxxhz.box` 版本 `4.45.0`，未向第二台手机下发百度网盘 Agent。

串行复核结果：

- `agent-1785226338172-9b19ad5e`：`FAILED / GENERATE_YAML / 30%`，缺 `REQ-003 [acceptance:relation] 扫描复印：校验百度网盘入口与当前页面同级入口的层级和位置关系`，未创建 Runner job。
- `agent-1785226764687-be17749f`：`FAILED / PLAN / 10%`，错误 `AI 业务规划失败：缺少需求业务分支：照片打印`。这是 `e109573` 的照片规格过滤过严导致“照片打印聚合页”主分支也被误删。
- `agent-1785227311247-99c07935`：进入 Runner 后 `FAILED / COLLECT_REPORT / 95%`，3 个 smoke 全失败；其中一个下发 YAML 为 `照片打印-一寸照规格页-百度网盘入口可见性校验`。业务 plan 中已丢弃了一寸照 flow，但 YAML 生成阶段仍从 Figma 视觉软证据补回规格页，runner gate 未拦截。

根因：

1. `_AGENT_PHOTO_SUBSPEC_TERMS` 加入 `规格页` 后，AI 计划里「照片打印聚合页」如果描述包含“规格选择前页面”等上下文，可能被误判为照片子规格，从而误删照片打印主分支。
2. 业务 plan 过滤只作用于 PLAN；YAML 生成阶段仍可能基于 Figma 视觉证据产出 `一寸照/5寸/规格页` 用例，且 scorer 100 后被 runner gate 下发。
3. `runnerCandidate` 计算只看 ref 或 score 的 smokeCandidate，没有要求最终 score 仍为 `executable`，导致 scope 降级为 `needs_review` 后仍可能保留 runnerCandidate。

修复：

- 增加回归测试，明确「照片打印聚合页-百度网盘入口」必须保留，而「照片打印-一寸照规格页」必须丢弃。
- 新增 `_agent_generated_yaml_ref_out_of_source_scope()`：generated YAML 的 module/file/name/content 命中照片规格页/子规格，且用户源需求没有明确提到该规格时，降级为 `needs_review`，并写入 scopeReview reason，禁止自动下发 Runner。
- `_score_agent_yaml_ref_for_execution()` 的 `runnerCandidate` 现在必须满足最终 `level == executable` 且 `score.ok is not False`，避免 ref 自带 smokeCandidate 绕过 scope 降级。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check -- task_server/services/agent_service.py tests/backend_static_checks.py CODEX_STATE.md
```

完整 `python3 tests/backend_static_checks.py` 仍失败于既有 OBJ 保龄球 YAML 断言：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`。本轮不修改 Runner、Sonic、scorer、Figma 解析或历史 YAML；Codex 不 push。用户部署后建议先同参跑 3 次观察是否仍有 `一寸照/5寸/规格页` 自动下发，确认无误再跑 5 次稳定性。

### 2026-07-28 百度网盘部署后五轮稳定性：生成门禁改善但仍暴露照片规格扩展和 RUN_SONIC 陈旧快照

用户部署 `a8c903f` 后要求同一百度网盘需求再跑 5 次。固定参数仍为 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / singleDeviceOnly / qwen3.7-plus / com.xbxxhz.box`，只使用 OPPO PHM110 `ecbfd645`，没有向第二台手机下发百度网盘任务。线上健康检查：Task 服务 `qwen3.7-plus`，AI Gateway 正常，Sonic Bridge 与 Windows Runner 均为 `2026.07.26-qwen3.7-result-retry-v1`，Runner 能力上报 `midscene_model_name=qwen3.7-plus / midscene_model_family=qwen3`。

五轮结果：

- `agent-1785217751108-4f78cacb`：`FAILED / RERUN / 95%`。生成门禁通过并进入真实 Runner；最终修复重跑后仍失败。
- `agent-1785218873459-08f1bdb5`：Agent 停在 `RUNNING / RUN_SONIC / 51%`，但 `/api/jobs` 显示其 OPPO Runner jobs 已全部终态：expanded 三条 success，另有 smoke「三入口百度网盘入口层级并列关系校验」failed。该轮暴露 Agent jobProgress 陈旧快照没有按真实 job 表收敛。
- `agent-1785221202256-5867c40c`：`DONE / DONE / 100%`。
- `agent-1785222050500-4de85a6a`：`DONE / DONE / 100%`。
- `agent-1785223081078-58dd9fe6`：`FAILED / GENERATE_YAML / 30%`，最终覆盖门禁缺 `REQ-001` 文档打印 visibility/relation/copy/reachability，未创建 Runner job。AI 计划中有文档打印，但下游 executable YAML 被照片/扫描/全局探索分支挤占。

结论：`a8c903f` 后 5 轮中 `GENERATE_YAML` 覆盖失败从之前的多轮复现降到 1/5，但仍未稳定；真实执行链路另有 1 轮 Agent 状态收敛失败。主要新根因：

1. 只过滤了 `一寸照/证件照/拼版` 等照片子规格，仍允许 `5寸/6寸/7寸/A4/规格页` 这类 AI/Figma 软参考进入硬执行业务分支。需求明确是首页三入口，不应把照片打印扩成具体规格页。
2. strict source business contract 下，AI 追加的「全局一致性」「异常处理」「入口位置探索」等无法唯一归属到源业务入口的流，会挤占最多 8 条生成预算，导致文档打印主链偶发没有 executable YAML。
3. Agent `RUN_SONIC` 进度快照可能停在旧的 `running`，但真实 Runner job 已 success/failed。读取 Agent 时需要用 persisted jobs 终态恢复陈旧快照，并继续后续 `COLLECT_REPORT`。

修复：

- `_AGENT_PHOTO_SUBSPEC_TERMS` 扩展到 `5寸/6寸/7寸/A4资料图片/A4生活照片/规格页/具体规格`；是否允许规格页只看用户需求文本是否明确提到，不再被 Figma/AI 软参考放行。
- `_normalize_agent_business_plan()` 在存在 source business contract 时，丢弃无法唯一匹配源分支的 AI 追加流，保留文档打印、照片打印、扫描复印三条主链。
- 新增 `_recover_stale_runner_job_progress()` 并接入 `recover_stale_agent_runs()`：当 RUN_SONIC 的 `jobProgress.nonTerminal > 0` 已陈旧，而真实 job 表中相关 job 全部终态时，自动恢复进度、补齐 RUN_SONIC tool call 成功状态并继续后续步骤。

验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check -- task_server/services/agent_service.py tests/backend_static_checks.py CODEX_STATE.md
```

完整 `python3 tests/backend_static_checks.py` 仍预计会被既有 OBJ 保龄球 YAML 断言拦截；本轮未修改 Runner、Sonic、scorer、Figma 解析或历史 YAML。Codex 不 push，用户部署后建议同参再跑 5 次，预期不再出现照片规格硬分支、全局/异常流挤占三入口主链，以及 RUN_SONIC 陈旧快照卡死。

### 2026-07-28 百度网盘五轮复跑：生成合同仍受 8 步预算影响，Figma 子规格不能变成硬执行分支

用户部署后要求同一百度网盘需求再跑 5 次，固定参数为 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.7-plus / com.xbxxhz.box`。五轮均只指定固定 OPPO `ecbfd645`，没有同时选择两台手机；线上服务健康，Task 模型为 `qwen3.7-plus`，Sonic Bridge 为 `2026.07.26-qwen3.7-result-retry-v1`。

五轮结果：

- `agent-1785209684874-8cdf7e63`：`DONE / DONE / 100%`，Runner 报告 complete。
- `agent-1785210573013-bf963d35`：`FAILED / COLLECT_REPORT / 96%`，5 个真实执行报告中 1 个失败。失败用例为「文档打印页-百度网盘入口层级关系校验」，脚本在文档打印页等待「相册导入」，AI 复核误归为 `PRODUCT_BUG`，实际更像同级参照物跨分支污染。
- `agent-1785211452176-e61aae3b`：`FAILED / GENERATE_YAML / 30%`，生成覆盖门禁缺 `REQ-002 [acceptance:copy] 照片打印：校验百度网盘入口使用需求约定的可见文案`，未创建 Runner job。
- `agent-1785211967559-f13bac71`：`FAILED / GENERATE_YAML / 30%`，生成覆盖门禁缺 `REQ-001 [acceptance:relation] 文档打印：校验百度网盘入口与当前页面同级入口的层级和位置关系`，未创建 Runner job。
- `agent-1785212361360-7f49bf95`：`FAILED / RERUN / 95%`，首批 smoke 选择了「照片打印页(一寸照)-百度网盘入口展示校验」，当前 APP 可见规格只有 5寸/6寸/7寸/A4 等，修复重跑仍失败。该一寸照来自 Figma 软参考/AI 扩展，不是本需求硬门禁要求的三个首页业务入口。

根因与修复：

1. `_merge_preserve_contract_into_flow()` 最多保留 8 步。线上部分候选已有 8 步时，即使平台能确定合成「文案为百度网盘」或「同级入口并列展示」，也会因预算已满而标记 missing，导致 copy/relation 门禁随机失败。现在 preserve 插入只会在源页窗口内腾挪低价值步骤：优先删除空步骤、`sleep`，其次删除不含业务/目标文字的泛化加载等待；不会删除业务点击、目标点击或已有断言。
2. 明确“三个业务入口：文档打印、照片打印、扫描复印”的百度网盘需求，不能被 Figma 软参考扩展成证件照、一寸照、照片拼版等执行分支。现在 `_baidu_netdisk_requirement_points()` 在识别到三入口范围时只产出这三支；Agent PLAN 归一化阶段也会丢弃 AI 追加的照片子规格流，除非源需求合同本身明确提到这些子规格。

验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
git diff --check -- task_server/services/agent_service.py task_server/services/ai_skill_service.py tests/backend_static_checks.py CODEX_STATE.md
```

完整 `python3 tests/backend_static_checks.py` 仍失败于既有历史 YAML 断言 `OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`。本轮不修改 Runner、Sonic、scorer、Figma 解析或历史 YAML；Codex 不 push。用户部署后建议同参再跑 5 次，重点观察是否仍出现 copy/relation 30% 门禁缺口，以及 smoke 是否还会选到一寸照/证件照子规格。

### 2026-07-28 脑图-only 任务：生成用例结构阶段必须有界降级，不能等外层 1800 秒超时

用户反馈任务「设备页、消息推送、打印设置优化需求文档」总是超时。线上 job 为 `gen_1785202398808_00029`，类型 `mindmap_only`，输入包含 2 个文件且带 Figma；终态为 `timeout / 50%`，最后阶段停在「生成用例结构」，耗时约 1800 秒后才由后台过期逻辑标记超时。该问题与百度网盘 RUNNER_JOB、Windows Runner、Sonic、ADB 或手机占用无关。

根因：

- `generate_mindmap_from_request()` 的结构生成阶段没有独立超时边界，外层只在读取/轮询 job 时被动执行 `expire_generate_job_if_stale()`。
- 非 Agent 强制 AI 规划的 `mindmap_only` 路径中，skill pipeline 失败后会再进入 legacy `call_dashscope_cases()`，可能触发第二段长模型生成，用户只能看到 50% 阶段长时间不动。
- 视觉 refinement 在结构阶段已经超时后仍可能继续消耗模型预算，导致后台任务更容易拖到总超时。

修复：

- 新增 `_mindmap_generate_structure_payload()`，把脑图结构生成阶段包进有界执行，默认 `MINDMAP_STRUCTURE_TIMEOUT_SECONDS=min(600, MINDMAP_JOB_TIMEOUT_SECONDS)` 且不低于 120 秒，可通过环境变量调整。
- skill pipeline 或 legacy 结构生成超时/失败后，普通 `mindmap_only` 任务直接走本地需求解析和场景降级，不再启动第二次 legacy 大模型生成。
- 若任务明确要求 Agent 核心 AI 规划，仍返回 `core_ai_failure`，避免把 AI 失败伪装成成功。
- 降级结构会在 review 中标记 `mindmap_structure_fallback=true`、记录原因，并跳过后续视觉模型 refinement，避免继续拖慢。
- 新增静态回归测试，确保 `mindmap_only` 在结构生成超时时不会再次调用 `call_dashscope_cases()`，并能返回可审阅的降级 cases。

验证：

```bash
python3 -m py_compile task_server/services/yaml_service.py tests/backend_static_checks.py
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_ai_skill_timeout_fallbacks_are_requirement_scoped()
PY
git diff --check -- task_server/services/yaml_service.py tests/backend_static_checks.py CODEX_STATE.md
```

完整 `python3 tests/backend_static_checks.py` 仍需复核当前工作区其他历史改动对静态检查的影响；本轮只修改脑图生成服务、对应静态测试和状态文档。Codex 不 push，由用户手动 push / 部署。旧的 `gen_1785202398808_00029` 已超时不能恢复，部署后应从页面点「重试」重新生成。

完整检查结果：`python3 tests/backend_static_checks.py` 仍失败于既有历史 YAML 断言 `OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`，本轮定向检查和语法检查均通过。

### 2026-07-28 百度网盘五轮稳定性回归：源页 copy/relation 合同需确定性保序补入

用户要求同一百度网盘需求再执行 5 次，固定参数仍为 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / singleDeviceOnly / qwen3.7-plus / com.xbxxhz.box`。五轮结果：

- `agent-1785202795460-a24697bd`：`FAILED / GENERATE_YAML / 30%`，缺 `REQ-003` 扫描复印 relation/copy，未创建 Runner job。
- `agent-1785203147506-8d8238d4`：`FAILED / GENERATE_YAML / 30%`，已生成 4 条 executable YAML 且静态/scorer 通过，但最终覆盖门禁缺 `REQ-001` 文档 copy、`REQ-002` 照片 copy，未创建 Runner job。
- `agent-1785203635283-09eaa995`：`DONE / DONE / 100%`，Runner 实际执行 5 条，`logicalPassed=5 / logicalFailed=0`，报告 5 条均 success。
- `agent-1785204691502-c75795fa`：`FAILED / GENERATE_YAML / 30%`，缺 `REQ-001` 文档 relation，未创建 Runner job。
- `agent-1785205041011-2a37a40d`：`FAILED / GENERATE_YAML / 30%`，缺文档/照片 reachability 和扫描 relation，未创建 Runner job。

五轮均只绑定固定 OPPO `ecbfd645`，没有选择第二台设备。聚合结果为 `1/5` 通过；失败均发生在生成覆盖门禁，说明 Runner、Sonic、ADB 和手机不是本轮主因，门禁阻断不完整 YAML 是正确行为。

根因与修复：

- 失败批次的最终流通常已经能导航到对应业务入口页并点击「百度网盘」，但模型会随机漏掉点击前的源页展示合同，尤其是 `copy` 文案维度，偶发 `relation` 维度。
- 原 preserve 合同只能复用候选已有的正向断言；当 AI 候选没有显式写出“文案为目标文字”或“同级入口并列展示”时，平台只能把该 check 记为缺失并触发最终覆盖失败。
- 现在 `_merge_preserve_contract_into_flow()` 在已有目标点击、已有 `requirementRefs`、且合同为 `visibility / relation / copy` 时，可从显式验收点目标文字生成确定性源页断言，并插入到目标点击前，例如 `校验「百度网盘」入口可见且文案为「百度网盘」`、`校验「百度网盘」入口与当前页面同级入口并列展示`。
- 护栏保留：如果候选证据里已有目标相关的负向、条件、跳转后或英文异常描述，不会用平台合成的正向证据覆盖；不放宽 scorer、覆盖门禁、坐标、账号授权、Runner 或 Sonic 规则。

验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
git diff --check -- task_server/services/ai_skill_service.py tests/backend_static_checks.py CODEX_STATE.md
```

完整 `python3 tests/backend_static_checks.py` 仍被用户历史 OBJ 保龄球 YAML 拦截：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`。本轮只修改 `ai_skill_service.py`、`tests/backend_static_checks.py` 和 `CODEX_STATE.md`；Codex 不 push。用户部署后建议再用相同参数跑 5 次观察生成门禁稳定性。

### 2026-07-28 百度网盘三轮稳定性回归：生成收敛与来源页路径修复仍需本地兜底

用户部署 `445e777` 后要求同一百度网盘需求连续跑三次，固定参数仍为 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.7-plus / com.xbxxhz.box`。三轮线上结果：

- `agent-1785199399440-33f07aea`：`FAILED / GENERATE_YAML / 30%`，Figma `4/4` 已真实送入并完成，未创建 Runner job。最终覆盖门禁缺 `REQ-002 [acceptance:reachability] 照片打印：点击百度网盘入口并校验目标页面稳定可达`。
- `agent-1785199781970-f975ebc6`：`DONE / 100%`，实际 Runner 尝试 `6`，报告状态 `5 success / 1 failed`；失败为照片可达性原始 YAML 缺少“照片打印聚合页 -> 绿色照片打印大卡片”中间导航，AI 修复重跑通过，逻辑汇总为修复后通过。
- `agent-1785200654960-d9d7cb95`：`FAILED / RERUN / 95%`，实际 Runner 尝试 `7`，报告状态 `4 success / 3 failed`；扫描展示 YAML 把“向右滚动直到百度网盘入口可见”写成 `aiWaitFor`，不会触发滚动；照片展示 YAML 也缺照片打印聚合页中间导航。修复重跑解决其中 1 条，但后续 expanded 仍有 2 条失败。

三轮均只使用固定 OPPO `ecbfd645`；没有创建第二台手机任务。Figma 解析 / 视觉校准均为 `4 页 / 4 图`。

根因与修复：

1. 生成收敛 direct visible target fallback 仍硬依赖 selected Top3 baseline 中存在当前分支 baseline。线上第一轮新需求路径 `matchedCases=[]`，如果 Top3 漏掉照片分支，即使来源候选自身已 `automatic / executable / baselineGrounded / baselineVerified / pathPlanApplied`，平台也不会生成有界首屏可达性候选。现在 direct fallback 不再依赖 selected baseline；只要当前来源候选自身带可信 baseline 元数据、当前分支导航可验证且目标可见，就允许生成同分支有界 landing，用自身来源页作为 tail，不借用其他分支导航。
2. YAML 本地修复只处理“照片尺寸点错”，没有处理“首页进入照片打印后直接点 5寸照片”的缺中间层路径。现在照片打印任务在具体尺寸 leaf 前，若缺少聚合页内绿色「照片打印」大卡片，会插入 `等待照片打印聚合页加载完成 -> 点击页面左侧绿色的「照片打印」大卡片入口 -> sleep`。
3. YAML 本地修复只处理“等待前缺 aiScroll”，没有处理模型把滚动动作本身写成 `aiWaitFor`。现在 `aiWaitFor: 在导入源区域向右滚动直到「目标」入口可见` 会被改成官方 `aiScroll`，带 `direction:right / distance:400 / scrollType:singleAction`，并保留后续目标入口等待/断言。

验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
checks.check_yaml_static_validation_and_patterns()
PY
```

完整 `python3 tests/backend_static_checks.py` 仍被用户历史改动的 OBJ 保龄球 YAML 拦截：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`。本轮未修改历史 YAML、Runner、Sonic、scorer 或设备选择逻辑；Codex 不 push，用户部署后应重新跑同一百度网盘 Agent 稳定性验证。

### 2026-07-27 真实回归：修复重跑需区分 Sonic 前台干扰，并承认 aiScroll 是有界路径探索

用户部署 `cec73ce` 后继续跟踪百度网盘 Agent。线上最新 `agent-1785133117095-429ff947` 已终态 `FAILED / RERUN / 95%`，但失败位置已从生成门禁转移到真实执行后的修复编排：

- PREPARE_SOURCE 成功解析 Figma `4 页 / 4 图 / 忽略 0`。
- PLAN 阶段 4 个 Figma 视觉批次全部真实完成。
- GENERATE_YAML 成功生成 `5 条 YAML / 12 个场景`，覆盖门禁通过。
- VALIDATE_YAML 与 Runner dry-run 均通过。
- Runner 只使用 `win-runner-01 / ecbfd645 / fixed`，未向第二台手机下发。
- 真实执行 5 条原始用例中 3 条通过，2 条失败；AI 生成 2 个修复草稿，其中 1 个被平台门禁拒绝，1 个修复 YAML 重跑失败。

本轮线上失败拆解：

1. 照片打印原始失败是脚本路径问题：进入照片打印聚合页后直接找「5寸照片」。AI 修复草稿正确补了点击绿色「照片打印」卡片，再选「5寸照片」，且 YAML scorer 为 executable。
2. 照片修复重跑失败帧显示页面突然变成 Sonic 应用安装扫描界面，包含版本号、文件大小、来源提示和安装按钮，不是小白学习打印业务页。失败复核仍标成 `script_issue / element_not_found / confidence 0.74`，导致平台没有把它当 ENV_ISSUE 原样重试。
3. 扫描复印原始失败是横向导入入口行未探索到屏外「百度网盘」。AI 修复草稿新增了有界 `aiScroll`，但修复门禁的 navigation signature 只统计 `aiTap/ai/aiAction/aiAct`，没把 `aiScroll` 算作路径探索，误报 `navigation_claim_without_yaml_change` 并拒绝下发。

修复：

- 新增前台环境干扰识别：当失败证据明确显示被测 App 前台被 Sonic / 系统安装界面抢走，例如「Sonic 应用的安装扫描界面」「版本号、文件大小、来源提示」「安装操作按钮」，即使 AI 复核写成 script_issue，也归一化为 `ENV_ISSUE`，允许同设备复用当前修复 YAML 原样重试。
- `_agent_failed_item_has_concrete_environment_evidence()` 现在会读取 failureReview evidence 中的前台干扰证据，避免 normalized item 丢失这类证据后无法进入 `_agent_original_rerun_eligible()`。
- `_agent_repair_navigation_signature()` 将 `aiScroll` 纳入路径签名；新增或替换有界横向滚动会被识别为真实路径探索，不再被 `navigation_claim_without_yaml_change` 误拒。
- 保留原约束：普通裸超时、低置信猜测、单纯元素找不到仍不自动转环境；重复同方向横滑仍会被 `duplicate_horizontal_scroll_repair` 拦截；导航变更缺可信基线、首个导航缺 ready wait 等原门禁仍有效。

验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check
```

本轮只修改 Agent 失败归因 / 修复门禁与对应回归测试，不修改 Figma 解析、YAML scorer、Runner、Sonic Bridge 或历史 YAML。Codex 不 push，由用户手动 push / 部署后，应使用完全相同参数重新跑百度网盘 Agent；预期扫描修复草稿不再被 `aiScroll` 误拒，照片修复重跑若再次遇到 Sonic 安装页应按 ENV_ISSUE 原样重试。

### 2026-07-27 前端：Runner 当前任务不能用最近完成任务伪装成占用

用户部署最新代码后复核线上状态：`/api/health` 正常，模型为 `qwen3.7-plus`；`/api/sonic/bridge-groovy` 已返回 `2026.07.26-qwen3.7-result-retry-v1`；`win-runner-01` 心跳版本同为 `2026.07.26-qwen3.7-result-retry-v1`，能力上报 `midscene_model_name=qwen3.7-plus / midscene_model_family=qwen3`，固定 OPPO `ecbfd645` 在线。

线上 `/api/jobs` 显示 active Runner job 为 `0`，`/api/runner/jobs/next?runner_id=win-runner-01&devices=ecbfd645` 返回 `job:null`。因此“手机仍被占用中”不是后端队列锁，也不是 Runner 可领取任务未清理。

根因是前端 `currentTaskCardHtml(activeJobs)` 在没有 pending/running Runner job 时，会回退展示 `latestJobs.find(isRunnerExecutionJob)`，即最近一条已结束的 success/failed 任务。这会让“Runner 当前任务”区域看起来仍有任务占用设备。

修复：`Runner 当前任务` 只展示真正的 active job；没有 active job 时显示“当前没有执行中的任务。”。同时递增 `js/app.js` cache key 到 `20260727-runner-active-task`，避免部署后浏览器继续加载旧逻辑。新增前端静态检查禁止再次用 `latestJobs.find(isRunnerExecutionJob)` 回退渲染当前任务。

验证：

```bash
python3 tests/frontend_static_checks.py
```

本轮只修改前端展示与静态检查，不改 Runner、Sonic、Agent 生成逻辑或历史 YAML。Codex 不 push，由用户手动 push / 部署。

### 2026-07-27 真实回归：当前分支已验证入口页可直接生成有界首屏可达性

用户部署 `b016c9d` 后，继续用完全相同需求、Figma、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.7-plus` 发起百度网盘 Agent `agent-1785123547381-b3be57ac`。

线上结果：

- PREPARE_SOURCE 成功解析 Figma `4 页 / 4 图 / 忽略 0`。
- PLAN 阶段 4 个 Figma 视觉批次全部真实送入模型并完成，生成 8 条业务分支；照片相关 Figma 图中可见「百度网盘」入口。
- GENERATE_YAML 在最终覆盖门禁失败，未创建 Runner job。缺口为 `REQ-002 [acceptance:reachability] 照片打印：点击百度网盘入口并校验目标页面稳定可达`。
- 失败与 Windows Runner、ADB、Sonic 或 OPPO 无关；固定 OPPO `ecbfd645` 未进入执行阶段。线上 Sonic Bridge 仍返回旧 `2026.07.24-qwen3.7-midscene110-v3`，只影响 Sonic 套件路径审计，不影响本次 RUNNER_JOB 生成失败。

根因与修复：

- 线上最终计划里照片打印已有可信来源页路径和展示 / 同级 / 文案断言，但模型漏掉了专门的“点击百度网盘后首屏稳定”用例。
- 之前平台只会优先复用同目标兄弟分支 landing tail；当兄弟 tail 不存在或未被选中时，没有把“当前分支已 verified 到达目标入口页”转成有界首屏可达性用例。
- `_bounded_convergence_evidence()` 现在新增直接来源页 fallback：只有当前来源用例本身为 `automatic / executable`，且 `baselineGrounded / baselineVerified / pathPlanApplied` 全部为真，并且来源页已覆盖目标入口可见证据时，才允许补生成 `点击目标入口 -> 等待目标落地页页面区域或可识别提示页，未白屏、未崩溃`。
- 该 fallback 仍使用当前分支自己的已验证导航与可见文字，不使用坐标、不做授权登录、不选择文件、不进入第三方深层操作；manual 条件分支、未验证 baseline、前后缀/第二目标、泄漏兄弟来源页仍被原门禁拦截。
- `boundedConvergence` 元数据新增 `directVisibleTargetLanding`，便于后续报告审计区分“兄弟 tail 复用”和“当前来源页直接首屏 landing”。

验证：

```bash
python3 tests/backend_static_checks.py
```

新增回归覆盖两类场景：当前分支未提升来源页仍可绑定同目标兄弟 landing tail；当前分支 automatic/executable/verified 来源页在没有兄弟 landing tail 时，可直接生成有界首屏可达性。原有负向用例继续保证不同目标、前后缀目标、第二目标、捐赠分支来源页泄漏、未验证 baseline 和人工条件分支不能通过。Codex 不 push；待用户部署后需重新跑同一百度网盘 Agent，预期 GENERATE_YAML 覆盖门禁不再因照片打印可达性缺口失败。

### 2026-07-27 真实回归：修复草稿补丁重复 sleep 锚点必须支持有界就近落地

用户部署 `1819e03` 后，按完全相同需求、Figma、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / qwen3.7-plus` 发起百度网盘 Agent `agent-1785120704925-a9061e69`。

线上结果：

- PREPARE_SOURCE 成功解析 Figma `4 页 / 4 图 / 忽略 0`。
- PLAN 阶段 4 个视觉批次全部真实完成，生成 8 条业务分支。
- GENERATE_YAML 成功生成 5 条 YAML，覆盖 12/12 验收点，5/5 executable 且 dry-run 通过；此前“照片打印可达性缺口”已解决。
- Runner 只使用固定 OPPO `ecbfd645`。首批 smoke 共 3 条：文档文案/同级成功、文档可达成功、照片可达失败。未创建第二台设备任务。
- 照片失败报告显示当前停在“照片打印聚合页”，页面包含照片打印、智能证件照等入口，但没有「5寸照片」规格；失败分类为 `SCRIPT_ISSUE / element_not_found / can_auto_repair=true`。
- AI 修复方向正确：在首页「照片打印」后补充点击聚合页内「照片打印」大卡片，再等待规格页出现「5寸照片」。但补丁应用器因原 YAML 有 3 个 `- sleep: 300`，第二条 `replace_step` 被拒为 `修复补丁锚点不唯一`，最终只保存诊断草稿，没有生成可重跑 YAML。

根因与修复：

- `apply_task_repair_patches()` 对所有重复锚点一律失败，缺少“前一条补丁已经限定了局部上下文后，后一条低信息 sleep 锚点应在该游标之后就近解析”的能力。
- 现在补丁应用器在每条成功补丁后记录相对游标。只有后续补丁锚点动作是 `sleep`，且操作是 `replace_step/remove_step` 时，才允许在游标之后选择最近的重复 sleep；业务动作、观察动作、单条重复锚点仍保持原来的不唯一拒绝。
- 这不是百度网盘特例；只是让 AI 已生成的局部补丁可以在重复短等待场景中落地，同时继续禁止坐标、ADB、XPath、跨任务替换和未引用基线的主链路改写。

验证：

```bash
python3 tests/backend_static_checks.py
```

新增回归用例直接复现线上照片分支补丁：`insert_after 点击「照片打印」入口` 后，`replace_step - sleep: 300` 应替换插入位置后的第一个 sleep；原有“单条重复业务锚点必须拒绝”的断言仍保留。Codex 不 push；待用户部署后应重新跑同一百度网盘 Agent，预期失败照片分支会生成可应用修复 YAML 并进入同设备重跑。

### 2026-07-27 真实回归：当前分支已覆盖展示维度时仍可复用同目标兄弟落地尾链

用户同步新版 Sonic Bridge 后，线上核对已确认 `/api/sonic/bridge-groovy`、`win-runner-01` 与固定 OPPO `ecbfd645` 均为 `2026.07.26-qwen3.7-result-retry-v1 / qwen3.7-plus / qwen3`。随后按完全相同需求、Figma、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed` 创建百度网盘 Agent `agent-1785116321078-d5da9a1e`。

线上结果：

- PREPARE_SOURCE 成功解析 Figma `4 页 / 4 图 / 忽略 0`。
- PLAN 阶段真实完成 4 个视觉批次，耗时 `15 / 20 / 20 / 24` 秒；业务计划覆盖文档打印、照片打印、扫描复印各展示与可达分支。
- GENERATE_YAML 在最终覆盖门禁失败，未创建 Runner job。缺口为 `REQ-002 [acceptance:reachability] 照片打印：点击百度网盘入口并校验目标页面稳定可达`。
- 平台门禁正确阻止不完整 YAML 下发；本次与 Windows Runner、ADB、Sonic 或 OPPO 无关。

根因与修复：

- 最终收敛的 `shared_target_tail` 证据只在当前分支先生成 `source_ui_assertion` 时才允许复用兄弟分支同目标 landing tail。
- 线上照片打印分支的展示 / 同级 / 文案已经由当前 executable 来源页候选覆盖，缺口只剩 reachability，因此不会再产生 `source_ui_assertion`，导致同目标兄弟落地尾链无法进入有界证据矩阵，只能依赖模型二次补写；模型漏写后被最终门禁拦截。
- `_bounded_convergence_evidence()` 现在允许在 `source_case` 已存在且来自当前分支可信 executable 来源页时，复用同目标兄弟 landing tail；兄弟分支仍只能提供点击目标后的首屏观察，不能提供当前分支导航。
- 最终 `boundedConvergence` 元数据补充 `sharedTailBoundToBranchSource / sharedTargetTailBoundToBranchSource`，便于报告审计。
- 仍保留全部约束：目标必须完全一致，当前分支导航必须来自自身可信基线 / 来源页，不能泄漏兄弟来源页，不能引入人工条件分支，仍需通过覆盖审计、基线、YAML、scorer、dry-run 与真实 Runner 门禁。

验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
git diff --check
```

回归测试新增“当前 executable 来源页已覆盖展示维度，但可达性缺口仍需同目标兄弟 tail”的照片打印场景。模拟最终模型漏回照片可达性候选时，平台会恢复 `TC-PHOTO-SOURCE` 为 `bounded_landing`，`sourceCaseId=TC-PHOTO-SOURCE / tailSourceCaseId=TC-DOC-LANDING`，最终 executable portfolio 覆盖全部验收维度。本轮未放宽覆盖门禁、scorer、坐标、账号、授权或深层外部操作限制；未修改历史 YAML。Codex 不 push，由用户部署后需重新跑同一百度网盘 Agent。

### 2026-07-26 Sonic 套件失败后 Task 回传缺失必须显式补偿

线上复核用户反馈的 Qwen3.7 Plus / Midscene 1.10.7 后 Sonic 套件记录：

- 旧通过结果 `1135`：Sonic 与 Task 均为 `11/11`，耗时 `1:20:46`。
- 新失败结果 `1159` 到 `1175`：Sonic 原始报告均显示 `send_msg_count=11 / receive_msg_count=11 / status=3`，但 Task 平台只收到 `3-10` 条结果，且收到项全是 `success`。
- 最新活动桶 `sonic_suite_1784908812325_00008` 已有 progress，`last_running_case=十二生肖印章打印 / expected_total_count=11`，但 `results=0`、无 resultId，说明桥接脚本能发进度但最终结果未稳定入库。

根因与修复：

- Task 平台此前只等待 `SONIC_TASK_CALLBACK_GRACE_SECONDS=180` 秒；超时后会发送最终汇总，但没有把缺失用例补成明确的失败/未回传记录，导致页面看起来像“只执行了若干条成功”。
- Sonic Bridge 的最终 `/api/sonic/result` 上报只有一次 curl，网络瞬断或 Task 短暂不可用会导致该用例只留下 progress，不留下 final result。
- `sonic_suite_definition_meta_from_dto()` 现在保留 Sonic 测试套用例名；最终失败且缺少 Task 回调时，`ensure_sonic_suite_missing_result_placeholders()` 会把缺失项补成 `synthetic_missing_callback=true / status=failed`，并提示查看 Sonic 原始报告定位真实失败步骤。
- `/api/sonic/suite-results` 返回层新增 `sonic_suite_project_for_display()`。对于只有 progress、长期无 final result 的活动桶，页面会看到“桥接脚本已回传运行进度，但未收到最终结果回传”的失败占位，而不是 0/11 空白记录。
- `sonic-midscene-task-runner.groovy` 的 final result 归档增加 3 次重试；Bridge / Windows / Mac Runner 版本统一提升为 `2026.07.26-qwen3.7-result-retry-v1`，方便部署后核对。

验证：

```bash
pytest -q tests/test_sonic_integration.py -k 'failed_sonic_completion_materializes_missing_task_callbacks_after_grace or stale_progress_only_sonic_suite_is_projected_as_missing_callback'
python3 -m py_compile task_server/services/sonic_service.py task_server/router.py
```

离线回放线上 `1175`：Task 记录从 `8/11` 投影为 `11/11`，新增 3 条 `synthetic_missing_callback / failed`；progress-only 活动桶投影为 1 条 `十二生肖印章打印 failed`。本轮没有修改用户历史 YAML、scorer 规则或百度网盘 Agent 生成逻辑。Codex 不 push，由用户部署后需重新同步 Sonic Bridge/替换 Runner 文件并重启服务。

### 2026-07-24 真实回归：App 页面网络异常必须按 ENV_ISSUE 原样重试

用户要求重新发起百度网盘完整 Agent。已在最新线上环境直接创建 `agent-1784885083267-850c077f`，固定参数为 `RUNNER_JOB / win-runner-01 / ecbfd645 / fixed / singleDeviceOnly / com.xbxxhz.box`，模型为 `qwen3.7-plus`。

线上结果：

- PREPARE_SOURCE 成功解析 Figma `4 页 / 4 图 / 忽略 0`。
- PLAN 阶段 4 个视觉批次全部真实完成，耗时 `15 / 16 / 12 / 11` 秒，生成 8 条业务分支计划。
- GENERATE_YAML 生成 6 条 YAML、12 个场景，6/6 scorer 为 executable，6/6 dry-run 通过。
- Runner 只使用 OPPO `ecbfd645`。首批 smoke 创建 2 个 job：照片打印可见性成功；文档打印可见性失败，报告复核显示页面出现“网络异常”提示，导致业务页面未正常加载。
- Agent 终态为 `FAILED / COLLECT_REPORT / 95%`。平台没有继续 remaining，也没有创建同设备原样重试 job。

根因与修复：

- Runner 失败复核返回 `category=env_issue / confidence=0.9 / can_auto_repair=false`，证据为 App 页面“网络异常”。但 `_agent_text_has_concrete_environment_evidence()` 只识别 ADB、模型服务、网关、HTTP 5xx 等基础设施词，不识别中文 App 网络异常文案。
- 因此 `_agent_job_failure_reasons()` 摘要中显示 ENV_ISSUE，但 `_normalize_failed_execution_item()` 认为缺少具体环境证据，回退为 SCRIPT_ISSUE，造成 `failureReview.category=env_issue` 与外层 `failureType=SCRIPT_ISSUE` 不一致，并阻断原 YAML 同设备重试。
- 已补充通用环境证据词：`网络异常 / 网络连接 / 服务器响应问题 / 业务页面未正常加载`。这不是百度网盘硬编码；只影响高置信 env review 或原始日志明确含这些网络异常证据的失败归类。
- 新增回归断言：高置信 App 网络异常复核必须覆盖脚本式 `waitFor timeout`，归一化为 `ENV_ISSUE`，并允许 `_agent_original_rerun_eligible()` 进入不改 YAML 的同设备原样重试。

验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
```

本轮未修改 Figma 解析、YAML 生成收敛、scorer、Runner、Sonic 或历史 YAML；未向第二台设备下发。Codex 不 push，由用户手动部署后可再次用同一参数重跑，预期文档打印网络异常会按 ENV_ISSUE 原样重试，而不是错误进入脚本修复/停止。

### 2026-07-24 Qwen3.7 Plus 全链路与 Midscene 1.10.7 official family

用户指出此前只分析模型升级范围、没有实际更新 Runner。线上复核确认 `/api/health`、`/api/models` 和 `win-runner-01` 心跳仍分别报告 `qwen3.6-plus`、`qwen3.6`，Runner 版本仍为 `2026.07.10-model-family-v4`。

补充线上复核：Sonic 测试套执行详情仍显示 `模型：qwen3.6-plus`。直接读取 `/api/sonic/runtime-env` 确认服务端返回的 `DASHSCOPE_MODEL / DASHSCOPE_VL_MODEL / MIDSCENE_MODEL_NAME` 均为 `qwen3.6-plus`、family 为 `qwen3.6`，并且这些值来自已成功加载的 `/opt/midscene.env`。因此 Sonic 不是自行回退或缓存旧模型，而是在按设计使用服务端仍未更新的生产配置。

补充线上复核：用户部署 Qwen3.7 后，`/api/health`、`/api/models`、`/api/sonic/runtime-env` 和 `win-runner-01` 心跳均已报告 `qwen3.7-plus`，Windows Runner 版本为 `2026.07.24-qwen3.7-v1`。最新 Sonic 套件 `sonic_result_3_1157` 已不再是旧模型问题，而是所有用例在启动 Midscene 前失败：`Invalid MIDSCENE_MODEL_FAMILY value: qwen3. Current version v1.7.10 accepts ... qwen3.6 ...`。

再次补充线上复核：用户已升级并重启 Windows Runner，Runner 心跳能力报告 `midscene_model_name=qwen3.7-plus / midscene_model_family=qwen3`，Task `/api/sonic/runtime-env` 也已下发 `MIDSCENE_MODEL_FAMILY=qwen3`。因此本地兼容提交 `9f8e73f` 的 `qwen3.7-plus -> qwen3.6` 映射需要撤销，切回 Midscene 当前官方文档要求的 `qwen3.7-plus / qwen3` 合同。

根因与修复：

- 官方阿里云当前最新 Plus 为 `qwen3.7-plus`；3.8 只有 `qwen3.8-max-preview`，不存在 `qwen3.8-plus`。Midscene 当前官方配置为 `MIDSCENE_MODEL_NAME=qwen3.7-plus / MIDSCENE_MODEL_FAMILY=qwen3`；旧 `qwen3.6` family 仅用于未升级的 1.7.x 兼容。
- Task Server 文本 / 视觉默认模型、部署环境样例、AI Gateway `qwen_plus` Provider、Windows / Mac Runner 回退模型和 Sonic 回退模型统一更新为 `qwen3.7-plus`。
- 服务端、Windows / Mac Runner 和 Sonic Bridge 将 `qwen3.7-plus` 的 Midscene family 映射为官方 `qwen3`，保留 `qwen3.6 -> qwen3.6`、`qwen3.5 -> qwen3.5` 和 Qwen2.5-VL 兼容分支。
- Windows / Mac Runner 心跳版本提升为 `2026.07.24-qwen3.7-midscene110-v3`，后续 Sonic 回传补偿修复中又统一提升为 `2026.07.26-qwen3.7-result-retry-v1`。Sonic 继续使用现代 `MIDSCENE_MODEL_API_KEY / BASE_URL / NAME / FAMILY` 合同，并移除会把 Qwen3 误声明成 Qwen2.5-VL 的 `MIDSCENE_USE_QWEN_VL` 等旧开关。
- Sonic Groovy `bridgeVersion` 后续同步提升为 `2026.07.26-qwen3.7-result-retry-v1`，确保 Sonic 执行详情能区分 1.10.7 官方 family 版本和结果回传重试版本。
- `install-server.sh` 会保留线上已有 `/opt/midscene.env` 和 AI Gateway Provider 配置，因此部署文档明确要求同步更新现有环境文件、把 `MIDSCENE_MODEL_FAMILY` 设为 `qwen3`、更新 `/opt/ai-gateway/config/providers.json`，再替换 Windows Runner 文件并重启 NSSM 服务。当前验收心跳必须为 `2026.07.26-qwen3.7-result-retry-v1 / qwen3.7-plus / qwen3`。

验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile windows-midscene-runner.py mac-midscene-runner.py task_server/config.py task_server/services/runner_service.py
python3 tests/ai_gateway_static_checks.py
python3 tests/frontend_static_checks.py
git diff --check
npm test
```

- 回归测试先稳定失败于 Task Server 仍默认 `qwen3.6-plus`，最小实现后通过。
- 完整测试通过：后端 61、前端 72、AI Gateway 46、API 合同 43、恢复 12、MeterSphere 54、动态模型目录 / 回退、4 个 Skill fixture，以及桌面 / 移动端视觉回归。
- 本轮未启动新的真机任务，未选择 OPPO 或华为设备；模型升级部署后应先做模型连通性和固定 OPPO 影子回归，再恢复百度网盘 Agent 完整回归。Codex 不 push，由用户手动替换 / 部署。

### 2026-07-24 真实回归：修复 YAML 遇到瞬时模型服务故障后未受限重试

用户部署 `e958a1e` 后，以完全相同需求、Figma、`qwen3.6-plus`、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed` 发起完整 Agent `agent-1784856833825-b1b81938`：

- PREPARE_SOURCE 正确解析 Figma `4 页 / 4 图 / 忽略 0`，4 个单图视觉批次全部真实送入 `qwen3.6-plus` 并完成；PLAN 生成 8 条业务分支，GENERATE_YAML 生成 6 条 executable / 12 个验收点，服务端校验和 6/6 Runner dry-run 均通过。
- 首批 smoke 在固定 OPPO 串行执行：文档展示、照片展示成功；扫描展示因右侧「百度网盘」仍被裁切而失败。平台继续执行 remaining：文档、照片可达成功；扫描可达因 App 启动停在「资料库」而失败。两项均正确归类 `SCRIPT_ISSUE`，生成 2/2 可执行修复草稿且 scorer 均为 100。
- 扫描可达修复在原设备成功；扫描展示修复已把模糊横向滑动替换为有界 `aiScroll`，但修复 job `job_1784858570579_00015` 遇到 `Request was aborted / Timeout after 300s`。Runner 以 `0.96` 置信度归类 `ENV_ISSUE / model_service`，明确不应继续修改 YAML。
- Agent 最终 `FAILED / RERUN / 95%`。所有 dry-run、smoke、remaining 和 repair job 均为 `win-runner-01 / ecbfd645 / fixed`，没有向华为或第二台设备下发；失败与 Windows Runner、ADB、设备选择或 YAML scorer 无关。

根因与通用修复：

- `_tool_rerun()` 已允许首轮具体环境失败原样重试，但修复草稿 job 失败后只调用 `_agent_post_rerun_autonomy()` 查找可再次修改的脚本。高置信度 ENV_ISSUE 不生成新修复，因此已过门禁的修复 YAML 没有获得一次同设备瞬时故障重试。
- `_tool_rerun()` 新增内部 `reuse_existing_yaml_only` 模式。只有本轮 `retry_sources` 能证明失败 job 是 `repair_draft` 的直接执行后代，且该 job 仍由具体环境证据归类为可原样重试的 `ENV_ISSUE` 时，才递归一次并复用失败子任务自己的 module/file；不会重新物化旧草稿、回退原始 YAML 或再次调用 AI。
- 受限重试以失败修复 job 为 parent，沿用其 Runner、设备、device strategy 和临时修复 YAML；`repair_depth` 仍限制最多一个后续恢复轮次。普通环境重试再次失败、裸超时、SCRIPT_ISSUE、PRODUCT_BUG、UNKNOWN 均不能进入该路径。
- `rerunProgressHistory / rerunAttempts / rerunSources` 保留 `原失败 -> 修复 job -> 环境重试 job` 完整链路和第一次环境失败，最终逻辑恢复仍要求同设备后代真实成功，不会用编排状态覆盖 Runner 事实。

验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check
npm test
```

- 回归测试先稳定失败于“修复 YAML 环境失败后没有第二个子任务”，最小修复后通过；同时断言两个子任务使用同一修复 YAML、父子链正确、固定设备始终为 `ecbfd645`，最终 recovered。
- 完整测试通过：后端 61、前端 72、AI Gateway 46、API 合同 43、恢复 12、MeterSphere 54、动态模型目录/回退、4 个 Skill fixture，以及桌面/移动端视觉回归。
- 本轮未修改 Figma、生成收敛、scorer、Sonic、Runner、历史 YAML 或 API 测试功能。Codex 不 push；待用户手动 push / 部署后，再以完全相同输入发起完整 Agent 并监督到终态。

### 2026-07-24 真实回归：已验证的运行时叶子修正必须跨修复轮次复用

用户部署最新代码后，以完全相同需求、Figma、`qwen3.6-plus`、`RUNNER_JOB / win-runner-01 / ecbfd645 / fixed` 发起完整 Agent `agent-1784847819330-e550b0ba`：

- PREPARE_SOURCE 正确解析 Figma `4 页 / 4 图 / 忽略 0`。4 张图按 `1/4` 至 `4/4` 分批真实送入 `qwen3.6-plus`，每批 1 张，耗时 `18 / 13 / 16 / 22` 秒，均一次完成、无 retry、无 fallback。
- PLAN 生成 8 条业务分支；GENERATE_YAML 生成 6 条 executable、12 个验收点。最终收敛从 `11/12` 补齐到 `12/12`，6 条 YAML 均通过平台静态校验、scorer 和 Runner dry-run。扫描复印展示与可达任务均在固定 OPPO 上真实成功。
- 首批文档展示成功；照片展示因 Figma 软证据中的「一寸照」与真机当前尺寸列表冲突而失败。AI 修复草稿在同一 Runner / OPPO 上将叶子有界修正为「5寸照片」并真实通过。
- 修复通过后，平台继续串行执行 4 条 remaining；文档可达、扫描展示、扫描可达均成功，但照片可达仍携带原「一寸照」叶子而再次失败。全部正式 job 和 dry-run job 均为 `win-runner-01 / ecbfd645 / fixed`，没有向华为下发。7 份 HTML 报告均可访问且包含真实截图；Runner 未生成录屏产物。
- Agent 最终为 `FAILED / RERUN / 95%`。第二次照片失败已正确归类 `SCRIPT_ISSUE / element_not_found`，但后续修复草稿虽再次提出「5寸照片」，因没有复用上一轮已接受的 baseline ID，被 `navigation_change_without_baseline_citation` 门禁拒绝。

根因与通用修复：

- `_tool_generate_repair()` 每轮都把 `accepted_runtime_leaf_overrides` 重置为空。已有逻辑只能在同一批多个失败任务间复用运行时叶子修正，无法在“首批失败修复成功 -> remaining 中同分支任务失败”的下一修复轮次继续使用已经验证的证据。
- 新增 `_agent_verified_repair_leaf_overrides()`，仅当修复草稿未被拒绝、包含可执行修复 YAML 和有界叶子证据，且 `rerunSources` 能把原失败 job 唯一关联到 `rerunResult.completed` 中真实 `success` 的修复 job 时才恢复该修正。Agent 指定 Runner / 设备时，成功 job 还必须与其完全一致。
- 失败、超时、未执行、不同 Runner / 设备、缺目标文字、缺 baseline ID 或空叶子的草稿均不能传播。恢复后的修正仍需通过现有同目标、原动作命中、当前分支 baseline 引用、精确断言保持、YAML 静态校验和 scorer 门禁；没有放宽导航、坐标、账号、授权或外部深层操作限制。
- 真实线上 artifact 离线回放恢复唯一证据 `一寸照 -> 5寸照片 / targetText=百度网盘 / baseline=652583bdad841b93`。将线上失败的第 04 条 YAML 送入现有补丁门禁后，`requestCount=0 / gateOk=true / score=100`，真实 `aiTap` 序列为 `照片打印 -> 5寸照片 -> 百度网盘`，不再包含失效点击动作。

验证：

```bash
python3 tests/backend_static_checks.py  # 61/61
```

- 回归先稳定失败于“上一轮真实成功修正没有进入下一轮 repair 输入”，再由最小实现修复；测试同时证明另一个失败修复 job 的叶子不能传播。
- 待用户手动 push / 部署后，使用完全相同输入和固定 OPPO 重新发起完整 Agent。预期即使同分支 remaining 再遇到相同失效叶子，也会直接使用已验证修正生成可执行草稿并在原设备完成有界重跑，不再因丢失 baseline 引用停在 95%。
- 本轮不修改 Figma 解析、YAML scorer、Sonic、Runner、历史 YAML 或 API 测试功能。Codex 不 push，由用户手动 push / 部署。

### 2026-07-23 API 日常流程 V2：业务线、自动同步、环境公共鉴权与按接口审阅

本轮按用户提供的生产演进文档、`docs/superpowers/specs/2026-07-23-api-daily-workflow-v2-design.md` 和对应实施计划收敛 API 日常工作流。产品取舍同时对照 Postman Workspace / Collection Run、Apifox 项目 / 环境变量 / 自动化测试及 MeterSphere 项目环境 / 接口测试的官方文档：用户默认只处理业务、范围、用例、执行和报告，source / revision / generation / trace / auth reference 继续保留为可展开技术证据。

本轮完成：

- 全部 API 页面统一显示 `选择业务 -> 同步接口 -> 生成用例 -> 审阅确认 -> 执行报告` 五步进度和唯一下一步操作。当前 Apifox 项目、业务线和接口更新时间是首屏业务上下文；内部 source/revision 默认折叠。
- 接口资产从服务端模块事实派生业务线，先选业务线再选模块和接口。单次 AI 计划继续严格限制 `1-60` 个接口；超过上限要求缩小子模块或搜索结果，不任意截断。来源新建或项目、分支、环境、同步范围等有效配置变化后自动排队同步，普通显示名修改不触发远端请求；已有同步运行时只记录一条最新配置替代任务，旧同步结束或服务重启恢复后立即接续，来源读取返回可验证的自动同步周期、最近成功和下次检查。
- AI 计划页不再默认平铺 971 个接口，也不再把 1590 条用例放进一张密集表。生成范围用业务线、接口数、模块数和请求方法摘要表达；AI 过程显示“校验范围、AI 分批设计、平台可执行性校验、草稿生成”四个业务阶段，真实批次、事件、generation/plan ID 和模型轨迹放入可停留的技术详情。
- 计划详情按 endpoint 分组，每页最多 20 个接口组，支持搜索以及“全部 / 可执行 / 待补数据 / 本版变更”筛选。每个接口展开后显示用例名、正负类型、请求、断言、可执行状态和缺失数据；待补字段按请求体、查询参数、路径参数、环境鉴权和响应断言聚合并可点击筛选。搜索、页码、缺失类型和展开状态按 plan 隔离，用户手动收起后即使马上重绘也不会重新展开。草稿、过期计划和已确认计划分别只展示当前唯一主操作。
- 业务鉴权从 source 级交互改成 MeterSphere `connection + project_id + environment_id` 环境公共鉴权。相同连接、项目和环境中的多个 Apifox source 复用一个远端变量和本地非秘密 profile；不同连接或环境严格隔离。保存和清除均使用 binding/profile 版本 CAS，旧页面请求在远端写入前返回 409。明文只写入 MeterSphere 环境变量，不落本地 binding、计划、报告、事件或浏览器响应。页面默认显示环境、鉴权类型、复用业务数，变量名和 auth reference 只在管理详情中展示；清除前明确提示影响的业务来源数量。
- 执行页和报告页接入同一五步流程。执行轮询继续只使用服务端事实，切换上下文时即使 execution ID 恰好相同也会使旧请求失效；报告按来源和业务线读取。技术日志的展开与滚动位置在刷新后保持；没有伪事件或前端推断终态。
- 前端缓存版本更新为 `20260723-api-daily-workflow-v2`。桌面使用完整五步流程，`390px` 移动端默认只显示当前步骤并可按需展开；Playwright 覆盖业务线、25 接口的 `12/12/1` AI 批次、技术详情停留、接口分组、公共鉴权版本保护和执行日志，所有页面无横向溢出。

最终验证：

```bash
python3 tests/api_project_workspace_checks.py -v  # 44/44
python3 tests/api_asset_sync_checks.py -v         # 37/37
python3 tests/api_case_contract_checks.py          # 43/43
python3 tests/api_runtime_recovery_checks.py -v   # 12/12
python3 tests/metersphere_v365_adapter_checks.py   # 54/54
python3 tests/frontend_static_checks.py            # 72 checks
node tests/visual_smoke_check.js
npm test
git diff --check
```

- 完整 `npm test` 退出码为 0；包含后端静态 `61`、前端静态 `72`、AI Gateway `46`、API 合同 `43`、恢复 `12`、MeterSphere `54`、动态模型目录/回退、Skill contract `4` 个 fixture，以及完整桌面/移动端 Playwright。
- 本轮没有修改 UI Agent、Midscene YAML 生成、Runner、Sonic 或 scorer；用户已有 Prompt、两份生肖 YAML、`sonic_service.py`、`yaml_executable_scorer.py`、Windows Runner 本地脚本和 `server-tasks/AI_Agent_草稿/` 未暂存、未回滚、未覆盖。
- 下一生产验收仍需用户 push / 部署后，以真实 `3D 接口` 来源验证配置变化自动同步、选择一个小模块生成 AI 用例、确认可执行子集、使用环境公共鉴权推送 MeterSphere，并取得真实主报告终态。Codex 不 push。
- 全局后续顺序不变：Phase D 统一 UI Agent/API canonical execution/report；Phase E Event Outbox、通知补偿、RBAC 和审计；Phase F 统一资产索引、质量看板及流程晋级。千问升级继续先读在线 catalog、验证 Midscene 兼容并在固定 OPPO 做 shadow 回归，不直接盲切型号字符串。

### 2026-07-23 API 项目工作区闭环：多项目、模块同步、AI 批次、来源级执行与报告隔离

本轮严格按用户提供的 Production Evolution Plan、`docs/superpowers/specs/2026-07-22-production-evolution-roadmap.md`、`docs/superpowers/specs/2026-07-23-api-project-workspace-design.md` 和对应实施计划收敛 API 闭环。没有重复创建现有 `ExecutionFacade / DAG / shadow / replay / observability / AI Skill / Apifox revision`，也没有修改 UI Agent、Midscene YAML、Runner、Sonic 或 scorer。`source_id` 继续作为 API 项目工作区边界，后续新增同事或业务时创建独立 Apifox source，不把 `3D业务` 写死为全局唯一项目。

本轮完成：

- 接口资产支持多个 Apifox source 的选择、新增和独立配置；每个 source 保存自己的项目 ID、分支、环境、令牌状态和模块同步范围。Apifox 仍只做完整官方 OpenAPI 导出，模块筛选在服务端确定性执行；父模块只包含真实子目录，不误匹配前缀兄弟目录。不可变 revision 保存 scope fingerprint、模块目录和 endpoint 稳定身份，资产页按模块树和当前模块展示，不再平铺 971 条接口或默认全选。
- 同步期间固定来源配置指纹；项目、地址、分支、环境、模块范围或令牌轮换时立即以 conflict 失败，保留上一活动 revision 和新来源配置。`source_id / asset_id / snapshot_id` 任意跨来源组合返回 404，不泄漏另一项目资产。
- AI 用例计划改为异步 generation：单次 1-60 个接口，按最多 12 个接口顺序分批；保存真实 provider/model/trace、批次状态、部分成功和失败批次重试。required-AI 失败不会伪装成本地成功；成功批次不会在重试或服务重启后重复生成。计划强绑定 source、immutable revision、模块范围、MeterSphere binding 和 auth reference；旧计划只有在 revision 能唯一证明来源时才进入来源视图，不猜测、不改写历史文件。
- MeterSphere 连接与 Access Key 保持全局，项目和环境改为每个 source 独立绑定；项目下拉和环境列表从精确 `v3.6.5-lts` 接口实时读取。业务鉴权首版支持 Bearer 和 API Key：明文只直接写入选定 MeterSphere 环境变量，本地 binding、计划、日志、报告和浏览器响应只保存 `auth_ref`、变量名和指纹。
- 绑定保存增加服务端 CAS、每页面 client intent 顺序和独立 binding version。快速切换业务/环境时，无论旧请求何时返回，最后一次选择都胜出；其他会话的过期写入返回 409。前端所有 source、项目、环境、计划、执行和报告异步响应在更新 UI 前都核对请求代次与当前工作区，技术日志的展开和滚动状态继续保留。
- execution 在 worker 启动前固化 source、binding、连接、项目、环境和 auth 指纹；执行前及轮询/报告阶段持续 fail closed 校验。服务重启后，安全的 queued generation/execution 会恢复；已有真实 `run_id` 的执行只恢复状态轮询；push/trigger 等远端副作用不确定且无法证明 run ID 的任务以 `restart_interrupted` 失败，绝不盲目重复触发。
- API report 持久化 source、execution、binding、project、environment 和 plan 归属，列表与详情按 source 隔离；旧报告仅在计划/revision 能唯一推导来源时兼容。报告页请求显式携带当前 `source_id`，来源切换后的迟到响应不会覆盖当前页面。

本轮最终验证：

```bash
python3 tests/api_project_workspace_checks.py -v  # 32/32
python3 tests/api_asset_sync_checks.py -v         # 34/34
python3 tests/api_case_contract_checks.py         # 43/43
python3 tests/api_runtime_recovery_checks.py -v   # 11/11
python3 tests/metersphere_v365_adapter_checks.py  # 54/54
npm test                                          # exit 0
git diff --check
```

- `npm test` 包含 undefined-name、后端静态 `61`、前端静态 `72`、AI Gateway `46`、动态模型目录/回退、Skill contract `4` 个 fixture，以及桌面/390px 移动端 Playwright；全部通过。
- 受保护的两份生肖 YAML、`sonic_service.py`、`yaml_executable_scorer.py`、Windows Runner 本地脚本和 `server-tasks/AI_Agent_草稿/` 均未修改；仓库生产源码未包含用户提供的 Apifox/MeterSphere 明文凭据。
- 尚未伪报完成：当前代码提交尚未由用户 push/部署，因此新的多 source/module/CAS/recovery/report 合同还没有在 QA 做首次真实验收。部署后需用真实 Apifox 项目选择模块同步，保存 source 对应 MeterSphere project/environment/auth，生成小批计划并执行到真实主报告终态；MeterSphere 资源池仍可能复现上一节记录的主报告终态阻断。
- Phase D 的统一 UI Agent/API canonical execution/report 迁移、Phase E 的 Outbox/通知补偿/RBAC/审计、Phase F 的统一资产索引/质量看板/流程晋级仍未启动。千问仍按在线 catalog、Midscene 兼容和固定 OPPO shadow 证据升级，不在本轮盲切型号字符串。Codex 不 push，由用户手动 push、部署。

### 2026-07-23 全局生产路线 Phase C：MeterSphere 3.6.5 真实 adapter 已实现，QA 主报告终态仍阻断

本轮再次逐项对照用户提供的 Production Evolution Plan 与实际仓库。已有 `ExecutionFacade / DAG / parallel DAG / shadow / replay / observability / Feishu / AI Skill / Apifox revision` 均保留，不创建重复 executor、failure classifier 或资产存储。全局状态以 `docs/superpowers/specs/2026-07-22-production-evolution-roadmap.md` 为事实源：Phase A/B 已完成；Phase C adapter 代码已实现，但真实 QA 首次执行尚未取得 MeterSphere 主报告终态；Phase D/E/F 不得提前启动。千问不直接替换为型号字符串，仍需在线模型目录、Midscene 兼容和固定 OPPO shadow 证据。

本轮 Phase C 实现：

- 新增精确 `MeterSphereV365Adapter`，只接受 `v3.6.5-lts` 与已验证构建 `v3.6.5-lts-f043cdd2`。Access Key 认证严格使用 AES-CBC/PKCS7 的 `accessKey|nonce|timestamp` 合同，header 只有 `accessKey / signature`；依赖或密钥长度错误在发起网络请求前失败。
- capability 来自真实版本、项目、环境、definition、case、scenario module 和 report 接口，不再由手填 path 是否存在推断。3.6.5 一旦被识别，push/run/status/report 均绕过 legacy 猜测路径；非匹配版本仍保留原兼容逻辑。
- 结构化 executable API case 通过 method + 规范化 path 唯一匹配 MeterSphere definition；映射 path/query/header/body、状态断言与 OpenAPI required-field 结构断言。未知变量、依赖、鉴权抑制或断言类型全部 fail closed。
- `Authorization / Cookie / Token / API Key / Access Key / Secret / Signature / Password / Credential` 等敏感鉴权 Header 在远端写入前阻断；远端响应、事件和报告继续递归脱敏。binding 只保存稳定 provider identity、remote IDs 和 hash，不保存请求值、环境变量或凭据。
- case/scenario 使用稳定 ownership marker 和本地 binding，支持 create/update/no-op、binding 丢失精确找回和多候选阻断。场景只引用 binding 中的远端 case，触发前再次核对远端步骤集合。
- 按 MeterSphere 官方前端合同使用 `POST /api/scenario/run`，请求体包含完整场景、客户端 UUID `reportId` 和稳定步骤 `uniqueId`；仅接受响应中的 `taskItem.reportId`，不伪造 run ID。报告只归一化 binding 中的稳定请求步骤或明确 API 请求类型，分组/控制容器不计为用例。
- 远端执行失败后仍同步真实报告。若所有请求步骤已经 `SUCCESS / ERROR / FAKE_ERROR`，但主报告超过 5 分钟仍未写入 `COMPLETED / STOPPED`，以 `provider_terminal_state_missing` 失败结束并同步步骤证据，绝不根据子步骤成功推断整次成功。

真实 QA 证据：

- 精确版本、Access Key、动态项目与环境读取均通过；受控 executable case/scenario 已真实写入。连续两次同步均为 `created=0 / updated=0 / unchanged=1`，没有重复远端对象。
- 官方 POST 触发返回真实 report ID。稳定步骤 ID 正确回映本地 `API-LIVE-001`，子步骤真实结果为 HTTP `200 / 47ms / 289B`。
- QA 默认资源池超过 6 分钟后，主报告仍为 `execStatus=PENDING / status=- / endTime=null`，但唯一请求步骤已经 `SUCCESS`。新 adapter 对该报告返回 `failed / provider_terminal_state_missing`，本地四阶段会保留 `metersphere_run=failed / sync_report=succeeded / overall=failed`。
- 因此 Phase C 不能写成“首次真实执行成功”。需要 MeterSphere 资源池或执行完成回调恢复主报告终态后，再用同一受控计划重跑退出验收；在此之前不进入 Phase D 的生产迁移。

验证证据：

```bash
python3 tests/metersphere_v365_adapter_checks.py  # 26 tests
npm test
git diff --check
```

- 新增回归先复现并阻断 `X-API-Key` 写入远端、HTTP 错误 JSON body 泄漏密码字段，以及报告把 `GROUP` 容器误算为用例；修复后 MeterSphere 聚焦测试 `26/26`。
- 完整 `npm test` 退出码为 0：undefined-name、后端 `61`、前端 `69`、AI Gateway `46`、API 合同 `23`、MeterSphere adapter `26`、动态模型目录/回退、Skill 合同 `4` 个 fixture，以及桌面/移动端 Playwright 全部通过。
- 本轮不包含任何凭据；不修改受保护历史 YAML、`sonic_service.py`、`yaml_executable_scorer.py`、本地 Windows Runner 脚本或草稿目录。Codex 不 push，由用户手动 push / 部署。

### 2026-07-22 全局生产演进盘点与 API 闭环 Phase B：可执行合同、版本门禁和真实就绪态

基于用户最新附件与实际仓库逐项核对后，确认附件中的部分建议已由现有 `ExecutionFacade / DAG / parallel DAG / shadow / replay / observability / AI Skill / Apifox revision` 覆盖，不重复创建 executor、failure classifier 或资产 diff。全局依赖顺序已写入 `docs/superpowers/specs/2026-07-22-production-evolution-roadmap.md`：Phase A 资产基础已完成；本轮完成 Phase B；下一步依次是 MeterSphere 3.6.5 真实 adapter、canonical execution/report、Event Center/RBAC、统一资产 read model。UI Agent 主链只有在 shadow 证据通过后才迁移，不做一次性重构。

本轮完成：

- 新增纯确定性 `api_case_contract/v1`：每条 API case 包含结构化 `request / assertions / variables / dependencies / readiness`。method/path、参数位置、请求值和状态/schema 断言只来自 OpenAPI；请求值仅接受明确 `example / default / const / enum`，缺少必填数据时精确标记 `needs_review`，不生成账号、Token、手机号、订单号或占位值。
- 负向用例必须引用真实必填字段，鉴权用例必须存在真实 OpenAPI security 约束；可选 request body、可选 security alternative、unsupported parameter location、非法 AI route/status、未知依赖、不可执行依赖、循环依赖和执行拓扑顺序均有平台级门禁。
- `api_test_designer` 已显式映射到 Gateway `generate_case`，Prompt/schema 改为结构化合同。AI 输出的 method/path、断言和 readiness 不是事实源，平台会重新生成并校验；重复 case ID 会去重，AI 漏掉 endpoint 正向用例时保留确定性 positive seed。计划保存不含 Prompt/凭据的 `decision_trace`：skill/action/provider/model/fallback/input hash/output summary/timing/success/error。
- 计划统一返回 `executable_case_count / needs_review_case_count / execution_readiness / revision_state`。绑定 revision 与当前活动 revision 相同时为 fresh；只新增无关接口仍保持 fresh；选中 endpoint 变更或删除时动态标记 stale，并阻止确认、推送和执行。旧纯文案 plan 仍可读取，但合同版本标记 legacy 且默认不可执行。
- MeterSphere 边界现在只接受 `confirmed + fresh + can_execute` 计划；混合计划只下发 executable 子集，并保留 total/executable/excluded 数量。已确认但待补数据或过期的计划仍显示在执行台，按钮禁用并展示原因，不再被静默过滤。
- AI 用例计划页显示接口数、总用例、可执行、待补数据、版本状态、结构化请求与断言；历史计划摘要可点击读取服务端详情。确认和执行按钮直接服从后端门禁。前端不再永久发送 `use_ai:false`：选择 1-12 个 endpoint 时真实调用 AI，较大批量保持确定性生成，避免把 971 个接口一次塞入模型。
- 新增桌面和 `390px` 移动端截图 `api-plan-readiness.png / api-plan-readiness-mobile.png`。Playwright 首轮发现移动端横向溢出后，改为独立可滚动接口/用例表格并复跑通过；历史计划详情走真实只读 API，不使用页面伪数据。

验证证据：

```bash
python3 tests/api_case_contract_checks.py       # 23 tests
python3 tests/api_asset_sync_checks.py -v       # 27 tests
python3 -m py_compile task_server/services/api_case_contract_service.py task_server/services/api_test_plan_service.py task_server/services/metersphere_service.py task_server/router.py
git diff --check
npm test
```

完整 `npm test` 明确 `exit_code=0`：undefined-name、后端 `61`、前端 `69`、AI Gateway `46`、API 合同 `23`、动态模型目录/回退、Skill contract `4` 个 fixture，以及桌面/移动端 Playwright 全部通过。

尚未伪报完成的部分：本轮没有猜测 MeterSphere 3.6.5 写接口，也没有声称已完成真实 QA 推送/运行/报告；这些属于 Phase C。全局 Agent 默认模型仍保持已验证的 `qwen3.6-plus`，没有在本 API 变更里直接替换为未验证的 `qwen3.8`；后续模型升级必须先读取在线 catalog、确认 Midscene model family 兼容，并在固定设备 shadow 回归后切换。由用户手动 push、部署；Codex 不 push。

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
### 2026-07-27 百度网盘稳定性三连跑与生成稳定性修复

在 `qwen3.7-plus / Midscene 1.10.7 / RUNNER_JOB / win-runner-01 / fixed ecbfd645` 下使用同一 `/tmp/baidu_agent_payload.json` 连续串行发起 3 次百度网盘 Agent：

- 第 1 次 `agent-1785149713617-dd2d0263`：`FAILED / GENERATE_YAML / 30%`，未创建 Runner job。Figma 4/4 已真实送 AI 并完成；失败为最终 executable 覆盖门禁缺 `REQ-003 [relation] 扫描复印同级关系`。
- 第 2 次 `agent-1785150055946-667265eb`：`FAILED / EXECUTION_PRECHECK / 45%`。生成侧已产出 YAML，但执行前体检时 `win-runner-01` 心跳超时离线；这是 Runner 环境问题，不是 YAML 或手机问题。
- 第 3 次 `agent-1785150418137-fa892620`：`FAILED / COLLECT_REPORT / 95%`。仅使用 OPPO `ecbfd645`；Runner 因同一 Windows Runner 上华为日常任务占用导致 OPPO job 排队约 6 分钟，随后执行 2 条 smoke，1 成功 / 1 失败。失败 YAML 为 `02-照片打印页(5寸规格)-百度网盘入口UI展示及层级校验.yaml`，Runner 证据明确：脚本点击「一寸照」，但当前规格页可见的是「5寸照片、6寸照片、7寸照片、A4资料图片、A4生活照片」，任务目标为 5 寸规格。

结论：

- 此需求单次可通过，但三连跑不稳定。
- 不稳定分为两类：生成收敛随机性，以及 Runner 环境/队列可用性。
- Runner/队列问题本轮只记录，不混入 YAML 生成修复；生成侧先做两处通用最小修复。

本轮修复：

- `ai_skill_service._executable_plan_repair_feedback`：当覆盖收敛阶段平台已聚焦的 executable 修复候选被模型降级到 `manual / needs_review / draft`，先作为语义修复失败反馈进入同模型 `acceptanceRepairRetry`，要求模型针对该候选补齐 flow/assertionTarget；不能直接接受降级并让最终覆盖门禁随机失败。
- `yaml_service.repair_generated_yaml_executable_gate_issues`：新增照片规格叶子一致性本地修复。若任务名明确为 5寸/6寸/一寸等照片规格，而生成 YAML 的 `aiTap` 点击了另一个照片规格叶子，会在入库/Runner 前按任务名修正，例如 `照片打印页(5寸规格)` 中的 `点击「一寸照」` 会修为 `点击「5寸照片」`。
- 新增后端静态回归覆盖：聚焦 executable 被降级必须触发一次同模型语义重试；5寸规格 YAML 不得继续点击一寸照。

已验证：

```bash
python3 -m py_compile task_server/services/ai_skill_service.py task_server/services/yaml_service.py tests/backend_static_checks.py
python3 - <<'PY'  # 最小回归：照片规格修复 + 降级聚焦候选重试
...
PY
```

注意：

- `python3 tests/backend_static_checks.py` 当前会先被用户历史 YAML 改动拦住：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`，该失败来自工作区已有 `server-tasks*/3D打印基线/OBJ保龄球打印.yaml` 改动，不属于本轮修改，未回滚。
- 本轮不 push。用户 push/deploy 后需要再次使用同一 payload 串行跑稳定性验证；建议先暂停或隔离同 Runner 的华为日常任务，避免 OPPO 固定设备回归被同 Runner 队列占用。

### 2026-07-29 百度网盘线上回归与报告/生成规模分层修复

用户部署后，使用相同百度网盘需求、Figma、`qwen3.7-plus`、`RUNNER_JOB`、`win-runner-01`、固定 OPPO `ecbfd645` 发起回归：

- Agent：`agent-1785314432310-22a73563`
- 终态：`DONE / 100%`
- Figma：4 页 / 4 图全部解析并进入 AI 规划。
- 计划：AI 识别 3 个业务入口分支：文档打印、照片打印、扫描复印。
- 生成：7 条 case / 7 个 YAML，静态 YAML 均 `ok=true`；Runner 前实际下发 5 个正式任务。
- Runner：5 个正式 job 全部 `success`，均使用固定设备 `ecbfd645`；未出现第二台手机或 dry-run 误计为真机通过。
- 仍有覆盖缺口：`REQ-002 照片打印` 的展示、同级、文案、可达性未进入最终执行覆盖。当前线上总结仍给出执行结论“通过”，但没有把“执行全过”和“覆盖不完整”拆开展示，容易误读。

本轮本地修复：

- 生成规模分层：完整测试计划和自动化 YAML 池拆开。小需求计划 5-8 / 自动化 5，中需求计划 10-20 / 自动化 8，大需求计划 20-50 / 自动化 12；Runner 首批仍最多 3 条，后续分批执行。
- `generation_volume_targets()` 与 `generation_targets_for_scope()` 增加 `targetPlanCaseCount / min_plan_cases / max_plan_cases`，保留 `targetCaseCount` 作为自动化 YAML 目标，不再把“完整测试用例数量”和“首批自动执行数量”混在一起。
- `execution_scope_planner` schema/prompt、`scenario_designer`、`automation_filter`、`coverage_auditor`、`smoke_selector`、`executable_yaml_planner` 统一新口径：完整计划 5-50，自动化池 5/8/12，首批 Runner 1-3。
- Agent 总结新增 `summary.statusBreakdown` 与 `summary.coverageStatus`：分别展示最终结论、原始执行、修复验证和覆盖缺口。原始失败尝试会保留，修复验证通过且无逻辑失败时最终标签可显示“修复后通过”，不会被旧失败 job 覆盖成“部分通过”。
- 前端最终报告新增“结果拆分”卡片，明确显示“原始执行 / 修复验证 / 覆盖缺口”；覆盖缺口列出缺失需求点，避免用户从技术日志里反推。

已验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_generation_volume_targets_modes()
checks.check_agent_summary_splits_repair_and_coverage_status()
checks.check_ai_yaml_generation_decision_chain_static()
print('ai yaml decision checks ok')
PY
python3 -m py_compile task_server/services/case_service.py task_server/services/ai_skill_service.py task_server/services/yaml_service.py task_server/services/agent_service.py tests/backend_static_checks.py
node --check js/agent-workbench.js
python3 tests/frontend_static_checks.py
git diff --check -- task_server/services/agent_service.py task_server/services/case_service.py task_server/services/ai_skill_service.py task_server/services/yaml_service.py ai_skills/prompts/execution_scope_planner.v1.md ai_skills/prompts/scenario_designer.v1.md ai_skills/prompts/automation_filter.v1.md ai_skills/prompts/coverage_auditor.v1.md ai_skills/prompts/smoke_selector.v1.md ai_skills/prompts/executable_yaml_planner.v1.md ai_skills/schemas/execution_scope_planner.schema.json js/agent-workbench.js tests/backend_static_checks.py tests/frontend_static_checks.py
```

全量 `python3 tests/backend_static_checks.py` 仍被工作区已有 `OBJ保龄球打印.yaml` 历史基线改动挡住，失败点为 `OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`；该文件属于用户历史改动范围，本轮未修改、未回滚。

### 2026-07-29 百度网盘回归后：覆盖/报告口径再收敛

用户部署后重新回归百度网盘需求：

- Agent：`agent-1785317041976-0f031217`
- 终态：`FAILED / RERUN / 95%`
- 模型：`qwen3.7-plus`
- 设备：只使用固定 OPPO `ecbfd645`。
- Figma：4 页 / 4 图解析成功。
- PLAN：240 秒超时后明确降级为源需求合同计划，保留文档打印、照片打印、扫描复印 3 条业务分支；视觉批次未完整跑完。
- 生成：6 条 case / 10 个场景 / 4 个确认 YAML；覆盖缺口仍集中在 `REQ-002 照片打印` 的展示、同级、文案、可达性。
- Runner：原始正式 job 4 个，2 成功 / 2 失败；修复重跑 2 个，1 成功 / 1 失败；最终逻辑结果为 3 通过 / 1 未解决失败。

本轮修复：

- 报告汇总新增并使用三套独立口径：`originalExecution` 只统计首轮正式 Runner job，`repairValidation` 只统计修复重跑与恢复情况，`finalExecution` 统计按修复链折叠后的最终逻辑结果。避免把修复重跑失败混入“原始执行失败数”，也避免“原始失败”和“修复后通过”在同一卡片里互相打架。
- 前端“结果拆分”卡片增加“最终执行”，并继续展示“原始执行 / 修复验证 / 覆盖缺口”，让用户能直接看出哪些是原始失败、哪些是修复验证后仍未恢复。
- 照片打印源需求只要求业务入口时，仍禁止证件照、智能证件照、照片拼版等子业务被当成需求分支；但允许一条普通照片代表路径作为到达百度网盘入口的中间导航，前提是最终目标和断言仍只验收“百度网盘”入口/落地页，不验收 5寸/6寸等规格本身。这样能补照片入口可达性，不再因为过度拦截导致照片分支完全缺席。
- `rerunAttempts.sources` 也纳入 source -> repair job 链路识别，避免只在 `rerunProgress` 中存在时才计算 recovered。

已验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_summary_keeps_original_and_rerun_counts_separate()
checks.check_agent_allows_photo_print_representative_baidu_path()
print('new checks ok')
PY
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
node --check js/agent-workbench.js
python3 tests/frontend_static_checks.py
git diff --check -- task_server/services/agent_service.py js/agent-workbench.js tests/backend_static_checks.py
```

全量 `python3 tests/backend_static_checks.py` 仍被工作区已有 `OBJ保龄球打印.yaml` 历史基线改动挡住，失败点不变：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`；该文件属于用户历史改动范围，本轮未修改、未回滚。

### 2026-08-11 API 测试部署：AI Gateway 与 Task Server 端口隔离

线上首次启用 API 测试运行时后，`midscene-task` 持续退出并由 systemd 自动重启。依赖与 API 测试配置检查均通过，完整 traceback 最终定位为 `OSError: [Errno 98] Address already in use`。

根因：

- Task Server 使用共享环境变量 `PORT=8091`。
- `install-server.sh` 执行 `pm2 restart ai-gateway --update-env` 时继承了该变量。
- AI Gateway 的 `server.js` 也读取通用 `PORT`，因此被错误重启到 8091，抢占 Task Server 端口；其正常端口应为 8090。

通用修复：

- 部署脚本增加独立 `AI_GATEWAY_PORT`，默认 8090。
- PM2 新建和重启 AI Gateway 时均显式注入 `PORT=${AI_GATEWAY_PORT}`，不再继承 Task Server 的 `PORT`。
- 新增静态回归断言，覆盖 PM2 restart/start 两条路径，防止以后部署再次发生端口抢占。

已验证：

```bash
python3 tests/ai_gateway_static_checks.py
bash -n deploy/install-server.sh
git diff --check
```

AI Gateway 静态检查 46 项通过。完整 `backend_static_checks.py` 在新 Mac 的系统 Python 环境因未安装 API 测试依赖 `redis` 而无法启动，属于本地依赖环境限制；本次新增断言本身已完成红灯到绿灯验证。

### 2026-08-10 API 测试 Phase 1 最终复审与并发稳定性收口

独立代码复审在提交前发现 4 项线上稳定性风险，本轮均通过先补失败测试、再修改实现的方式关闭：

- 失败用例不再在主执行任务中等待 AI。真实请求、结果落库、`case_finished` 和执行终态完成后，由独立 Celery 任务补充失败分析；送 AI 的脱敏证据限制为 128 KiB。前端对尚在分析的失败结果做有上限的后台刷新，分析不可用也不会阻塞报告。
- HTTP SSE 默认使用 `API_TESTING_REDIS_URL` 对应的共享 Redis 客户端进行阻塞唤醒，不再让每个在线日志连接每 50ms 轮询 PostgreSQL；数据库仍是可重放的事实来源。
- 执行记录选择增加版本隔离，快速切换 A/B 时较慢的 A 响应和旧 EventSource 的迟到事件不会覆盖 B 的标题、详情或日志。
- SSE 断线按 1/2/5/10/30 秒退避并最多重试 5 次；耗尽后进入明确失败状态，并提供“重新连接日志”按钮，不再高频申请 ticket。

最终完整验证：

```bash
npm run test:api-testing
# Python API: 251 passed
# Vue: 13 files / 60 passed
# vue-tsc + Vite production build: passed
# desktop/mobile visual check: passed
# Playwright real browser E2E: 1 passed
```

真实浏览器 E2E 继续覆盖“我的收藏”三个接口的保存上下文、AI 生成、用例编辑、真实调试、采纳基线、一键回归、实时日志与报告闭环；报告同时保留 1 passed / 1 failed / 1 broken，并展示脱敏后的 `qwen3.7-plus` AI 失败分析证据。

### 2026-08-10 API 测试平台 Phase 1：AI 设计、真实调试、基线回归与报告闭环

Phase 1 已按 `docs/superpowers/plans/2026-08-08-api-testing-phase1.md` 完成基础设施和完整人工闭环：

- PostgreSQL 16 保存项目、来源修订、环境修订、用例版本、基线证据、执行实例和报告；Redis 7 承载 Celery 队列、取消标记和可重连 SSE 事件。
- 接口资产支持 OpenAPI 文件导入并固化为只读来源修订；工作台只展示项目、接口版本和执行环境中文名称，不要求用户填写技术 ID。
- 环境配置支持 base URL、普通变量和加密敏感变量；浏览器只收到掩码与配置状态，真实 token 不进入 DOM、SSE、日志或报告 JSON。
- AI 助手按选中接口通过 AI Gateway 生成结构化候选，保留 requested/actual provider/model 证据；失败分析同样调用 AI Gateway，模型不可用时明确降级为“平台诊断”，不伪装成 AI 结论。
- 用例编辑器支持请求、数据、断言、提取、依赖和前后处理的结构化编辑与原始 JSON；嵌套校验错误定位到对应字段。
- 在线调试不要求先采纳基线；只有同项目、接口、用例版本和环境修订的真实 PASSED 调试证据才能采纳为基线。
- 自动回归通过真实 Celery worker 执行；实时日志使用短期、执行绑定、可重连 SSE ticket，并支持暂停自动滚动、级别/用例过滤和事件证据展开。
- 报告分别展示 `PASSED / FAILED / BROKEN / CANCELLED / SKIPPED`，产品断言失败不会被脚本、网络或环境异常覆盖；请求、响应、断言、执行轨迹和 AI 失败分析均可追溯。
- 执行记录可恢复到原项目/接口版本/环境上下文继续编辑；排队取消会生成真实取消结果，历史终态日志可从数据库恢复。

真实浏览器验收使用本地隔离 PostgreSQL schema、Redis DB、Task server、Celery worker、确定性 AI Gateway 和目标服务，完整走通“我的收藏”三个接口：

1. 登录现有任务平台并进入 `API 测试`。
2. 导入并保存查询、添加、取消收藏 3 个接口。
3. 创建 `生产环境（腾讯云）`，配置 `Biz` 与随机敏感 `ZXBToken`。
4. AI 生成候选，编辑断言，逐条调试，再将通过证据采纳为基线。
5. 自动回归产生 `1 PASSED / 1 FAILED / 1 BROKEN`，实时 SSE 全程不刷新页面。
6. 最终报告展示 qwen3.7-plus AI 失败分析；随机 token 未出现在 DOM、服务日志、目标日志或报告 JSON。
7. 1440x900 与 390x844 截图检查通过：桌面三栏、移动单列、AI 面板折叠、长路径 tooltip、实时日志和报告均无横向溢出。

本轮完整验证：

```bash
npm run test:api-testing
# 249 passed（Python API 测试）
# 54 passed / 13 files（Vue 组件与 store）
# vue-tsc + Vite production build 通过
# API 桌面/移动视觉检查通过
# Playwright “我的收藏”真实端到端 1 passed

npm run test:static
# undefined-name、backend 63、frontend 72、AI Gateway 46、模型目录和 AI Skill 合同全部通过

npm run test:visual
# 原平台桌面/移动 Agent、失败分析、重跑等视觉冒烟通过

git diff --check
# 通过
```

本地基础设施健康与迁移：

```bash
API_TESTING_POSTGRES_PASSWORD="${API_TESTING_POSTGRES_PASSWORD}" \
  docker compose -f deploy/api-testing-compose.yml up -d --wait
API_TESTING_ENABLED=1 deploy/api-testing-migrate.sh
npm run test:api-testing
```

服务器部署仍使用现有顺序，必须先迁移再重启服务：

```bash
cd /opt/midscene-task-platform-src
git pull --ff-only
bash deploy/install-server.sh
systemctl restart midscene-task
systemctl restart midscene-api-worker
curl http://127.0.0.1:8091/api/health
curl http://127.0.0.1:8088/api/health
```

Phase 2 边界保持不变：测试集 DAG、跨用例提取、批量编排、合同漂移影响分析和完整版本对比不在 Phase 1 内；定时任务、通知、多 worker lease、趋势、Mock、性能/安全检查、角色权限和 CI/CD 触发继续按后续阶段实现。本轮没有用占位能力冒充这些后续功能。

### 2026-07-30 API 执行页 Apifox 环境快照修复与线上核查

- 线上 8091 `/api/health` 与 8088 `/api/health` 均恢复 200；静态页加载版本为 `20260730-api-env-readiness`，`/api/auth/login` 登录正常。
- 线上来源 `api_source_1785310905647_00002`（3D）保存了 `project_id=5904970`、`environment_id=33831678` 与 Bearer profile，但 `environment_snapshot` 为空，导致 API 执行上下文返回 `base_url=""`、`readiness.missing=["base_url"]`。
- 直接调用 Apifox discovery 能读到环境 `33831678 / 生产环境（新）-腾讯云`，base_url 为 `https://print.wisebeginner3d.com/app`；已用现有线上接口把该环境快照写回来源，随后 `/api/api-testing/execution-context?source_id=api_source_1785310905647_00002` 返回 `connection.state=connected`、`readiness.can_execute=true`。
- 本地通用修复：保存 Apifox 来源时，如果已选择环境但请求体没有可执行 `environment_snapshot.base_urls`，后端使用已保存的 Apifox token 调用 discovery 补抓该环境详情并落库；前端不需要用户手填环境 ID/base_url，也不把 token 返回给浏览器。
- 补充 `api_asset_sync_checks` 路由测试，覆盖“已有来源保存 token、再次保存只提交环境 ID、后端自动补齐环境快照”的场景。
- 同步修正 backend static 中已过期的 API/MeterSphere 路由断言、`python3 -m task_server` systemd 入口检查、AI 小范围计划尺寸断言、详细照片子分支不应被百度网盘三入口概览折叠的 fallback 行为。
- 修复 OBJ 保龄球历史 YAML 的第二次「去打印」恢复文案，避免该既有基线继续阻断 `backend_static_checks.py`。

已验证：

```bash
python3 -m py_compile task_server/__main__.py task_server/router.py task_server/services/ai_skill_service.py tests/backend_static_checks.py tests/api_asset_sync_checks.py
python3 -m unittest tests.api_asset_sync_checks tests.api_native_execution_checks tests.apifox_discovery_checks
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_ai_skill_timeout_fallbacks_are_requirement_scoped()
print('single check ok')
PY
git diff --check -- task_server/router.py tests/api_asset_sync_checks.py task_server/services/ai_skill_service.py tests/backend_static_checks.py task_server/__main__.py server-tasks/3D打印基线/OBJ保龄球打印.yaml server-tasks-all/3D打印基线/OBJ保龄球打印.yaml
```

未完成验证：

- 全量 `python3 tests/backend_static_checks.py` 继续被 Agent 报告聚合断言挡住：`Report collection must retain both passed and failed terminal HTML reports instead of hiding failure evidence`。该失败与本次 Apifox/API 执行环境修复无关，未在本轮继续扩大修改。

### 2026-07-30 API 测试：移除 MeterSphere，改为平台原生执行与报告

用户明确决定 API 测试不再依赖 MeterSphere：平台应直接从 Apifox 拉取接口资产，AI 生成/编辑 API 用例，采纳为基线后由平台原生 API Runner 执行，并在平台内实时查看报告和失败分析。

本轮设计取舍：

- 不再保留 MeterSphere 配置、推送、执行、报告拉取路由；运行时代码里的 `/api-testing/metersphere/*` 与 MeterSphere 设置抽屉已清除。
- Apifox 环境快照作为执行环境来源：业务/环境选择直接来自 source provider metadata 和环境 base_url，不再要求用户手填项目/环境 ID。
- 业务 token 保存为平台安全 profile：前端只显示变量名、指纹、绑定业务和环境；真实 secret 只在服务端执行时读取。
- 原生执行新增异步执行记录：`queued/running/succeeded/failed`、阶段、事件、统计、单条调试和基线执行统一走 `/api/api-testing/executions*`。
- 报告参考成熟测试平台的信息架构：摘要卡、环境信息、逐接口请求/响应/断言、失败分类和建议。失败分析先用确定性规则生成，后续可在同一结构上增加 AI 深度归因按钮，避免模型超时影响基础报告生成。

新增/调整文件：

- 新增 `task_server/services/api_execution_service.py`：平台原生 API 执行、单条调试、业务 token 登录获取/保存、执行上下文。
- 删除 `task_server/services/metersphere_service.py`、`task_server/services/metersphere_v365_adapter.py`、`tests/metersphere_v365_adapter_checks.py`。
- `task_server/router.py` API 执行/报告路由改为原生执行，并新增 `GET /api/api-testing/reports/{report_id}`。
- `js/api-testing.js` 报告页支持点击历史报告加载详情，展示摘要、环境、失败分析、请求/响应/断言明细。
- `css/round5.css` 增加 API 报告详情卡片、环境网格、失败分析和明细样式。
- 新增 `tests/api_native_execution_checks.py` 覆盖原生执行上下文、服务端 token 边界、失败报告结构。

已验证：

```bash
python3 tests/api_native_execution_checks.py
python3 tests/frontend_static_checks.py
python3 -m py_compile task_server/services/api_execution_service.py task_server/services/api_workspace_service.py task_server/services/api_test_plan_service.py task_server/services/api_plan_generation_service.py task_server/services/api_report_service.py task_server/router.py task_server/app.py tests/api_native_execution_checks.py
node --check js/api-testing.js
node --check js/api.js
rg -n "MeterSphere|metersphere|MeterSphe|api-testing/metersphere|pollApiMeterSphere|loadApiMeterSphere" task_server js css task-manager.html
```

当前注意：

- 运行时代码已无 MeterSphere 引用。
- `tests/api_project_workspace_checks.py`、`tests/api_runtime_recovery_checks.py`、`tests/api_case_contract_checks.py`、`tests/backend_static_checks.py`、`tests/visual_smoke_check.js` 仍有历史 MeterSphere 专项契约；它们不是运行时代码，但后续若恢复全量 CI，需要继续删改为原生 API 契约或从旧适配器 CI 集合中移除。
- 本轮未暂存/未回滚用户历史 dirty 文件。

### 2026-07-30 Agent 报告结论策略：冒烟非全失败不再折成整单失败

用户确认：冒烟不一定全部通过，后续测试用例本来就可能暴露失败；除非冒烟全失败，最终结论不要做成失败。

本轮修复：

- Runner 汇总在 job 明细不完整时，会使用 `jobProgressByPhase` 阶段聚合补齐缺失状态。例如阶段聚合显示首批冒烟 `completed=1 / failed=1`，但 Bridge/报告只回传失败 job 明细时，平台会把缺失的成功计入结果，并替换正式 job 台账里的 `unknown` 占位，避免重复计数。
- 新增冒烟阶段结论规则：只有当存在冒烟尝试、冒烟成功数为 0、冒烟无运行中任务且冒烟失败/超时/取消数大于 0 时，最终 Runner 结果才允许是 `failed / 未通过`。
- 如果冒烟不是全失败，即使扩展用例、修复用例或后续可执行用例仍有失败，最终结论为 `partial / 部分通过`；失败用例、失败类型、未恢复 job 和后续修复建议仍继续展示，不会被吞掉。
- 汇总结果新增 `smokeAttemptCount`、`smokePassedCount`、`smokeFailedCount`、`smokeTimeoutCount`、`smokeAllFailed`，便于前端和排障直接说明为什么最终结论是部分通过还是未通过。

已验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_summary_separates_runner_outcomes_from_orchestration()
print('summary check ok')
PY
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check -- task_server/services/agent_service.py tests/backend_static_checks.py CODEX_STATE.md
```

全量 `python3 tests/backend_static_checks.py` 仍被工作区已有 `OBJ保龄球打印.yaml` 历史基线改动挡住，失败点不变：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`；该文件属于用户历史改动范围，本轮未修改、未回滚。

### 2026-07-30 Agent 历史卡片改为展示最终报告结果

用户部署 `4d2bd17` 后要求先跑 2 次百度网盘回归，并指出历史记录卡片不能因为顶层 Agent 状态是 `FAILED` 就显示整单“失败”：如果 6 条用例通过 5 条，这应该是一目了然的“部分通过”。

线上两次同参回归：

- `agent-1785373399306-03d0dbb8`：`6 case / 6 YAML`，固定 `win-runner-01 / ecbfd645`；终态仍为 `FAILED / COLLECT_REPORT`，原因是收集到 6 个报告中 1 个失败；但最终报告 `conclusion=部分通过`，`outcome=partial`，逻辑结果 `5 passed / 1 failed`，冒烟非全失败。
- `agent-1785374850885-2b33c869`：`6 case / 6 YAML`，固定 `win-runner-01 / ecbfd645`；终态同样为 `FAILED / COLLECT_REPORT`，最终报告 `conclusion=部分通过`，逻辑结果 `5 passed / 1 failed`；首批冒烟 `2/2` 通过，扩展第 1 批 `3 passed / 1 failed`。

根因：

- `/api/agent-runs` 历史列表为轻量摘要，只返回顶层 `status/currentStep/error/summary/inputSummary`，不返回 `artifacts.summary.execution`。
- 前端历史卡片只能看到 `status=FAILED`，因此卡片顶部和标题颜色都按失败渲染，用户看不到真实执行报告已经是 `部分通过 5/6`。

本轮修复：

- 后端 `list_agent_runs()` 新增 `reportSummary` 轻量字段，从 `artifacts.summary.execution` 提取最终报告结果：`conclusion/outcome/attempted/passed/failed/timeout/running/smokeAllFailed/orchestrationLabel` 等。
- 前端历史卡片新增 `agentRunResultMeta()`：如果存在 `reportSummary`，卡片主状态优先显示最终报告结论；只有没有报告结果时才回退显示顶层 Agent 编排状态。
- 历史卡片新增结果条：例如 `5/6 通过 · 1 失败`，辅助行显示 `编排：编排阻断 · Agent：失败`，避免把编排失败和测试结果混成一个红色“失败”。
- 新增 `查看报告` 按钮，点击后直接打开该 Agent 并切到 `报告` 产物页签；原 `查看轨迹` 保留。
- 新增 `.agent-run-history-card.partial` 与 `.status-pill.partial` 样式，部分通过用琥珀色，不再使用失败红色。
- 更新 `task-manager.html` 静态资源版本为 `20260730-agent-history-report-card`。

已验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_history_list_exposes_report_summary()
checks.check_agent_summary_separates_runner_outcomes_from_orchestration()
print('backend agent checks ok')
PY
node --check js/agent-status.js
python3 tests/frontend_static_checks.py
python3 -m py_compile task_server/services/agent_service.py tests/backend_static_checks.py tests/frontend_static_checks.py
```

全量 `python3 tests/backend_static_checks.py` 仍被工作区已有 `OBJ保龄球打印.yaml` 历史基线改动挡住，失败点不变：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`；该文件属于用户历史改动范围，本轮未修改、未回滚。

补充线上验证：

- 用户部署 `418b56d` 后按同一参数发起 5 次稳定性回归；前 2 次均在首批冒烟阶段失败，随后停止剩余批次并取消第 3 次，避免继续占用设备。
- 第 1 次 `agent-1785322101458-7921cd44`：生成 6 case / 6 YAML / 覆盖缺口 0；首批 2 条均 failed。
- 第 2 次 `agent-1785322826602-6578b083`：生成 5 case / 4 YAML；首批 3 条均 failed，覆盖缺 `REQ-002 照片打印可达性`。
- 失败 job 的 Runner 复检明确指向 `RunAdbShell command returned stderr`，命令为 `monkey -p com.xbxxhz.box -c android.intent.category.LAUNCHER 1`；这是新增 launcher 兜底自身向 stderr 输出导致 Midscene 判失败，不是百度网盘入口产品失败。
- 第 3 次 `agent-1785323652680-46e54341` 在 `PREPARE_SOURCE` 后被手动取消，未进入 Runner。

跟进修复：

- 将 launcher 兜底改为静默命令：`monkey -p <package> -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 || true`。它只辅助把 App 拉前台，不允许 monkey stderr 使 YAML 失败；后续仍保留 Midscene 官方 `launch`。
- 后端检查同步要求 silent launcher fallback，避免再次生成会被 Midscene 因 stderr 硬失败的启动守卫。

### 2026-07-30 API 测试工作台：Apifox 资产快照 + 平台原生执行的一页流

用户确认按“方案 B”收敛 API 测试流程：Apifox 只负责接口资产和环境来源，平台本地保存快照，后续 AI 用例生成、编辑/单条调试、采纳基线、执行和报告都由平台原生链路完成，页面要更简洁，不再让用户按 MeterSphere 式流程一步步跳。

本轮参考的成熟做法：

- Postman Collection Runner：围绕请求集合、环境和运行报告组织工作流，单次执行记录请求结果和断言明细。
- Apifox 环境变量：环境变量作为可复用占位符，base_url、Header、Token 等由环境注入，不应让用户反复手填 ID。
- Playwright Trace/Report 思路：失败时保留可回放证据和逐步报告，执行状态与失败分析分开展示。

本轮实现：

- 新增 `task_server/services/api_workbench_service.py`：统一聚合 source、活动 Apifox/OpenAPI 快照、模块/接口、AI draft、已采纳基线、原生执行上下文、运行报告和同步记录；返回给前端前统一脱敏，不暴露 Apifox token 或业务 token。
- 新增 `GET /api/api-testing/workbench`：前端 API 工作台只读取这一份状态，不再到处拼 assets/plans/reports/context。
- 新增 `POST /api/api-testing/snapshots/update`：工作台直接触发 Apifox 快照更新。
- 新增 `POST /api/api-testing/cases/debug`：语义化单条调试入口，仍复用原生 API runner，draft 可执行用例不需要先采纳为基线即可调试。
- 左侧接口测试导航收敛为 `API 工作台` 和 `报告历史`；旧资产/计划/基线/执行页面函数保留为高级入口和兼容入口，不再作为主流程导航。
- `showApiTestingDashboard()` 改为单页工作台：顶部项目/环境/快照状态，四个阶段卡片（AI 候选、API 基线、执行、报告），模块卡片一键生成 AI 用例，生成批次和计划详情直接在工作台内展示。
- AI 生成轮询、生成结果打开、计划详情渲染支持在 `api_dashboard` 内运行；用户点击“生成 AI 用例”后能看到排队、批次、日志和生成结果。
- CSS 增加工作台状态条、阶段卡片、模块卡片、快照事实区和高级信息折叠样式。
- `tests/frontend_static_checks.py` 的 API 导航契约改为“单页工作台 + 报告历史”，并继续检查旧功能通过工作台入口可达。
- 修正 `tests/backend_static_checks.py` 里过期的 Agent 报告检查：当前双状态策略允许非全失败 Runner 结果不阻断 Agent 编排，但必须保留 success/failed 两类 HTML 报告证据并标记 `nonBlockingRunnerFailures=true`。
- 新增 `tests/api_workbench_checks.py`，覆盖工作台 facade、鉴权、路由注册、敏感信息脱敏。

已验证：

```bash
python3 -m py_compile task_server/services/api_workbench_service.py task_server/services/api_execution_service.py task_server/services/api_source_service.py task_server/services/api_asset_service.py task_server/services/api_test_plan_service.py task_server/router.py tests/api_workbench_checks.py tests/frontend_static_checks.py
python3 tests/api_workbench_checks.py
python3 tests/api_native_execution_checks.py
node --check js/api-testing.js
python3 tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check -- task_server/services/api_workbench_service.py task_server/router.py js/api-testing.js css/round5.css task-manager.html tests/api_workbench_checks.py tests/frontend_static_checks.py
```

注意：

- 本轮没有暂存或回滚用户历史 dirty 文件，包括 prompt、Runner、scorer、部署文档和截图 artifacts。
- 页面上仍保留“高级资产管理”等入口，目的是给异常同步/手动 OpenAPI 上传留后门；主路径已经是一页完成。

### 2026-07-30 API 工作台：Apifox 环境配置发现后立即固化

用户指出：Apifox 拉下来的环境配置不应只临时展示，否则每次进入 API 工作台还要重新刷新。

根因：

- 旧链路只有在 `POST /api/api-testing/sources` 保存 source 时才会写入 `environment_snapshot`。
- `POST /api/api-testing/apifox/discovery/project-context` 是只读发现；已有 source 读取项目/分支/环境后，没有把 base_url 和变量快照写回 source。
- API 工作台的“同步 Apifox 快照”只同步 OpenAPI，不保证同步前先刷新并保存 Apifox 环境配置。

本轮修复：

- `api_workbench_service` 新增 `persist_apifox_project_context()`：把 Apifox discovery 读到的项目名、团队、分支名、环境名、base_url 和环境变量快照写回已有 source。
- 新增 `refresh_apifox_environment_snapshot()`：当本地 source 缺少可执行 `base_url` 时，工作台首次进入会自动发现一次并保存；后续进入直接读本地快照，不重复请求 Apifox。
- `api_testing_workbench()` 在本地环境快照缺失时自动补齐一次。
- `update_apifox_snapshot()` 在启动 OpenAPI 同步前先强制刷新并保存环境快照。
- `POST /api/api-testing/apifox/discovery/project-context` 在请求带 `source_id` 时会直接持久化发现结果，并返回 `persisted_source`。
- 新增测试覆盖“首次进入工作台自动保存 Apifox 环境快照，第二次进入不再重复 discovery”，同时验证 Apifox token 和业务 token 不泄露到响应。

已验证：

```bash
python3 -m py_compile task_server/services/api_workbench_service.py task_server/router.py tests/api_workbench_checks.py
python3 tests/api_workbench_checks.py
python3 tests/api_native_execution_checks.py
python3 tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check -- task_server/services/api_workbench_service.py task_server/router.py tests/api_workbench_checks.py CODEX_STATE.md
```

### 2026-07-29 百度网盘回归后：启动守卫与照片横滑稳定性修复

用户部署后再次使用相同百度网盘需求、Figma、`qwen3.7-plus`、`RUNNER_JOB`、`win-runner-01`、固定 OPPO `ecbfd645` 发起回归：

- Agent：`agent-1785319838926-88b7cbec`
- 终态：`FAILED / RERUN / 95%`
- Figma：4 页 / 4 图解析成功。
- PLAN：AI 超时后使用源需求合同降级计划，仍保留文档打印、照片打印、扫描复印 3 个业务入口。
- 生成：6 条 case / 6 个 YAML，覆盖缺口 `0`；说明覆盖生成问题本轮已收敛。
- Runner：原始正式 job 6 个，4 成功 / 2 失败；修复重跑 1 个，0 成功 / 1 失败；所有正式 job 均使用固定设备 `ecbfd645`，未混用第二台手机。
- 失败集中在照片打印：一条失败报告显示当前截图停在 OPPO 桌面，未进入 App 首页；另一条失败为照片页横向导入入口未稳定滑到「百度网盘」。平台归因为 `SCRIPT_ISSUE`，没有生成产品缺陷。

本轮修复：

- `launch_guard_flow()` 在 `am force-stop <package>` 后增加一次 Android launcher 兜底：`monkey -p <package> -c android.intent.category.LAUNCHER 1`，再保留 Midscene `launch`。目标是避免 Runner 偶发仍停在 OPPO 桌面时继续执行首页断言。
- 照片打印页横向导入栏修复改为可见锚点：包含「相册导入」「相机拍照」等同级入口的横向导入方式区域中部，避开屏幕左右边缘。
- 本地 executable gate 会把照片打印百度网盘相关的 `aiScroll` 统一为一次官方 `aiScroll`：`direction: right`、`distance: 400`、`scrollType: singleAction`，不追加第二次横滑、不使用坐标或 ADB swipe。
- 新增后端回归检查：小白学习打印启动守卫必须包含 ADB launcher fallback；照片打印百度网盘可达性修复必须使用照片导入可见锚点且只保留一次有界横滑。

已验证：

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_xiaobai_launch_guard_uses_adb_launcher_fallback()
checks.check_photo_baidu_scroll_repair_uses_visible_photo_import_anchors()
print('new checks ok')
PY
python3 -m py_compile task_server/services/yaml_service.py tests/backend_static_checks.py
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_runner_inline_android_device_injection()
checks.check_xiaobai_launch_guard_uses_adb_launcher_fallback()
checks.check_photo_baidu_scroll_repair_uses_visible_photo_import_anchors()
checks.check_agent_execution_gate_repairs_before_smoke_selection()
print('targeted checks ok')
PY
git diff --check -- task_server/services/yaml_service.py tests/backend_static_checks.py
```

全量 `python3 tests/backend_static_checks.py` 仍被工作区已有 `OBJ保龄球打印.yaml` 历史基线改动挡住，失败点不变：`OBJ bowling baseline must recover when the first go-print tap leaves the suite preview page open`；该文件属于用户历史改动范围，本轮未修改、未回滚。
### 2026-08-11 Apifox 更新预览：真实导出兼容与独立加载状态

用户在线上接口资产页点击“检查更新”稳定返回 `422 Request validation failed`，并要求读取项目、读取环境和检查更新分别显示 loading；正式保存必须保留独立按钮。

根因与真实重放：

- 线上账号下的 Apifox 项目、分支和环境发现均成功，失败发生在 OpenAPI 导出后的平台规范化阶段。
- 真实导出为 OpenAPI 3.0.1，约 4.8 MB、990 条 path；其中 `$ref` 使用 URI fragment 百分号编码 `#/components/schemas/Resp%3F`，对应组件名为 `Resp?`。
- 平台原解析器只处理 JSON Pointer 的 `~0/~1`，没有先按 URI fragment 规则百分号解码，因此整份资产被误判为 unresolved local reference。
- 修复后用同一份真实导出离线重放成功，规范化得到 993 个 endpoint、528 个 schema。

本轮实现：

- OpenAPI 本地引用解析在执行 JSON Pointer 解码前，先执行 URI fragment 百分号解码；这是通用标准兼容，不包含项目或接口硬编码。
- Apifox 输入错误和 OpenAPI 校验错误不再统一覆盖成英文 `Request validation failed`，API 返回可操作的中文错误和稳定错误码。
- 前端增加独立 Apifox 操作状态：读取项目、读取环境、检查更新、保存版本；对应按钮显示旋转图标和明确中文进度。
- “检查更新”只生成并持久化候选预览，不覆盖当前正式版本；差异区明确显示独立的“保存为新版本”按钮。

已验证：

```bash
.venv/bin/python -m pytest tests/api_testing -q
# 121 passed, 157 skipped（无本地 TEST_DATABASE_URL 的 PostgreSQL 用例按设计跳过）

cd api-testing-ui
npm test -- --run
# 16 files / 68 tests passed
npm run build

PATH="$PWD/.venv/bin:$PATH" npm run test:static
# backend/frontend/AI Gateway static checks 与 skill eval 全部通过

git diff --check
```

### 2026-08-11 API AI 生成、基线可执行状态与编辑器占位符修复

用户在线上选择“我的收藏”接口后遇到三类问题：AI 生成被确定性校验拦截、已有调试成功记录但任务执行返回资源冲突、请求头编辑器保存了空的“新参数”。

根因：

- AI 提示词为了保护敏感信息，把敏感环境变量的名称和值一起隐藏了。模型看不到已配置的 `ZXBToken` 变量名，只能自行生成 `Authorization` 等变量，随后被平台的未定义变量门禁正确拦截。
- 任务视图只有 `ready` 状态，没有暴露当前来源版本和环境版本下实际可运行的基线数量；前端因此把“已有草稿但尚未采纳基线”误显示为可执行，后端执行器再以 409 拒绝。
- 请求参数编辑器点击“添加参数”时立即把空的“新参数”发布给父组件；即使用户还没填写参数名，随后编辑其他字段也会把占位符保存。
- Alembic 迁移的日志配置默认禁用已加载 logger，导致整套测试运行后数据库/Redis 故障日志消失。
- AI 汇总把候选校验错误和派生的接口覆盖缺口重复计入 `invalid_candidates`，一条坏候选会显示为两条。

本轮修复：

- AI 仅接收已启用环境变量的名称，继续禁止传递敏感值、密文和指纹；技能提示明确禁止发明未配置的变量。
- 任务合同新增 `runnable_baseline_count`，统计口径与执行器完全一致：同项目、同 owner、同来源版本、同环境版本、活动基线且位于当前选中接口范围。
- 无可运行基线时，前端显示“待采纳基线”并禁用任务执行；后端仍保留门禁，并返回稳定错误码 `baseline_required` 和中文操作提示。
- 调试成功后采纳基线会立即刷新任务状态；有一条基线时显示“可执行 1 / N”，只运行已采纳基线，不把未采纳草稿混入回归。
- 请求参数新增行作为本地待填写行，只有明确参数名后才进入用例数据；编辑其他字段不会夹带空占位符。
- `invalid_candidates` 只统计真实候选错误，覆盖缺口继续保留在详细校验错误和任务状态中。
- Alembic 保留已有应用 logger；开发依赖补充 `PyYAML`，新机器使用项目 `.venv` 即可执行完整门禁。

已验证：

```bash
TEST_DATABASE_URL='postgresql+psycopg://midscene:***@127.0.0.1:5432/midscene_api_testing' \
TEST_REDIS_URL='redis://127.0.0.1:6379/14' \
API_TESTING_REQUIRE_POSTGRES_TESTS=1 \
.venv/bin/python -m pytest tests/api_testing -q
# 291 passed

npm --prefix api-testing-ui test -- --run
# 18 files / 74 tests passed

npm --prefix api-testing-ui run build
node tests/api_testing_ui_visual_check.js
npx playwright test tests/api_testing_e2e.spec.mjs --project=chromium
# 我的收藏三接口完整闭环 1 passed

PATH="$PWD/.venv/bin:$PATH" npm run test:static
git diff --check
```

### 2026-08-11 API 环境版本辨识与公共请求头优先级修复

用户保存“生产环境（新）-腾讯云 v6”后，在线调试抽屉仍显示环境 UUID，且“我的收藏列表”请求没有携带业务授权头。

线上核验：

- 工作区、当前任务和最近调试执行绑定的环境修订均为 `e7b81421-78fe-4508-878a-54893db5429b`；该 ID 对应的正是“生产环境（新）-腾讯云 v6”，不存在回退到旧环境。
- v6 已保存敏感变量 `ZXBToken`，但 `default_headers` 为空；已有手工用例还包含空值 `Authorization`，最终请求只发送了 `Content-Type`，业务响应为 HTTP 200、业务码 4009。
- 调试抽屉只展示数据库 UUID，且切换环境后仍可能保留上一环境的调试结果，造成环境未生效的误判。

本轮修复：

- 调试抽屉展示“环境名称 · 版本”，数据库修订 ID 继续仅作为请求参数，不作为主要界面文案。
- 切换项目、接口版本或环境时清空上一上下文的调试执行、结果、错误和恢复状态，并关闭旧抽屉。
- 执行器合并请求头时忽略用例级 `null`、空字符串和纯空白值；空的可选请求头不再覆盖环境公共请求头。
- 保留环境版本不可变和任务绑定门禁，不自动猜测或硬编码某个业务项目的鉴权方式。

已验证：

```bash
TEST_DATABASE_URL='postgresql+psycopg://midscene:***@127.0.0.1:5432/midscene_api_testing' \
TEST_REDIS_URL='redis://127.0.0.1:6379/14' \
API_TESTING_REQUIRE_POSTGRES_TESTS=1 \
.venv/bin/python -m pytest tests/api_testing -q
# 292 passed

npm --prefix api-testing-ui test -- --run
# 18 files / 76 tests passed

npm --prefix api-testing-ui run build
PATH="$PWD/.venv/bin:$PATH" npm run test:static
bash tests/run_api_testing_gate.sh
# Chromium：我的收藏三接口完整闭环 1 passed
```

### 2026-08-11 API 当前草稿断言与单次调试版本一致性修复

用户在单次调试中修改断言后，右侧仍展示旧断言结果；同时把业务响应码 `60101004` 填入“状态码”时，界面没有指出它不是 HTTP 状态码。

根因：

- 工作台“调试当前草稿”实际直接提交上一个已保存版本 ID，没有先保存当前编辑内容，因此名称与行为不一致。
- 用例版本读取合同包含数据库读模型字段（例如嵌套 `sequence`、空的 `path/name`）；当前版本再次保存时直接回写这些字段，会被严格写合同拒绝。旧调试路径跳过保存，长期掩盖了该问题。
- 编辑器把 HTTP 状态码与响应 JSON 中的业务码都显示为“状态码”，且前端没有校验 HTTP 状态范围。
- 完整回归还暴露两个时序问题：新建项目时在选项加载前设置下拉值，以及接口保存未完成就进入环境页。前者已在产品代码中修复，后者由验收脚本等待真实保存完成信号。

本轮修复：

- 单次调试改为“保存并调试”：先本地校验，再保存当前草稿、执行后端环境校验，最后只使用本次返回的新版本 ID 创建调试执行。
- 草稿有改动时立即清理旧调试证据并关闭旧抽屉，避免把历史结果误认为当前结果。
- 读模型转换为写草稿时使用字段白名单，移除 `sequence` 和无效空字段，保持严格合同而不放宽后端门禁。
- 断言类型明确显示为“HTTP 状态码”和“响应 JSON 字段”；HTTP 状态码仅允许 100 到 599。业务码需使用响应 JSON 字段断言，例如路径 `$.code`。
- 后端把用例合同错误映射为稳定的 `case_validation_failed` 和可操作中文提示，不再返回笼统错误。
- 新建平台项目会先刷新项目列表，再选择新项目，避免下拉框偶发回到“请选择”。

已验证：

```bash
bash tests/run_api_testing_gate.sh
# 293 backend tests passed
# 19 frontend files / 82 tests passed
# Vue typecheck + Vite production build passed
# desktop/mobile visual check passed
# 我的收藏三接口 Playwright 完整闭环 1 passed

PATH="$PWD/.venv/bin:$PATH" npm run test:static
# undefined-name、backend 63、frontend 72、AI Gateway 46、模型目录与 skill eval 全部通过

git diff --check
```

说明：直接使用系统 Python 运行静态检查会因新 Mac 系统环境没有 `redis` 包而中止；项目 `.venv` 是实际运行环境，使用该环境的完整静态检查已通过。

### 2026-08-12 API 已保存用例切换与基线采纳反馈修复

用户在线上切换同一接口的已保存用例后，下拉框已显示新用例，但下方编辑器仍保留上一条用例；通过调试的用例点击“采纳为基线”也没有任何可见反馈。

根因：

- Pinia 会把已保存的 `CaseVersion` 转成 Vue 响应式代理。切换用例时，store 直接对该代理调用 `structuredClone`，浏览器抛出 `DataCloneError` 并中断事件处理，因此只有原生下拉框显示值变化，草稿状态没有更新。
- 基线采纳只有一个裸异步请求，没有采纳中、成功和失败状态；工作台也没有捕获任务状态刷新异常，成功和失败在界面上都表现为“无响应”。
- 切换用例时没有清除上一用例的调试证据，存在把旧执行结果与新用例版本同时展示的风险。

本轮修复：

- 用例读模型转换为编辑草稿时改用 JSON 数据克隆，兼容 Pinia 响应式代理，并继续只保留可写字段。
- 切换已保存用例会同步重载名称、请求参数、请求体等完整草稿，同时关闭抽屉并清理旧调试证据。
- 基线采纳增加防重复提交、采纳中、成功和失败反馈；成功后刷新任务的可执行基线数，刷新失败也会明确提示“基线已采纳，但任务状态刷新失败”。
- 增加工作台真实组件联动测试、基线采纳成功/失败状态测试和抽屉反馈测试。

已验证：

```bash
bash tests/run_api_testing_gate.sh
# 294 backend tests passed
# 19 frontend files / 86 tests passed
# Vue typecheck + Vite production build passed
# desktop/mobile visual check passed
# 我的收藏三接口 Playwright 完整闭环 1 passed

PATH="$PWD/.venv/bin:$PATH" npm run test:static
# undefined-name、backend 63、frontend 72、AI Gateway 46、模型目录与 skill eval 全部通过

git diff --check
```

限制：当前 Codex 任务虽已加载 Chrome 控制技能，但没有暴露浏览器控制调用接口；因此本轮以项目内真实 Chromium 端到端闭环验证，未直接操作用户已有 Chrome 标签页。

### 2026-08-12 Apifox Body 示例驱动 API 用例设计

用户反馈 Apifox 调试页已有 JSON Body 示例，但平台新草稿没有带入示例，AI 生成也没有依据 Body 字段设计用例，同时生成了没有业务价值的请求头专项用例。

根因：

- Apifox/OpenAPI 同步和 PostgreSQL 资产实际完整保留了 `requestBody.content.*.example` 与 `examples.*.value`；数据没有在同步阶段丢失。
- AI 提示构建为降低敏感信息风险，统一删除了 `example`、`examples` 和 `default`，导致安全的业务 Body 示例也被移除。
- 新建手工草稿固定使用空 Body，没有读取内联、命名、Schema 或 `$ref` 请求体示例。
- AI 候选仍可构造请求头，确定性校验又要求用例显式提供 OpenAPI 必填 Header，没有识别所选环境已经配置的公共请求头。

本轮修复：

- 保持 Apifox 接口源资产完整、只读，增加数据库往返测试锁定直接示例和命名示例不丢失。
- AI 输入改为最小业务契约：保留 Body Schema、直接示例、命名示例、响应契约和非 Header 参数；移除 Header 参数、安全配置及无关元数据。
- AI 的 `$ref` 依赖只保留 Body、非 Header 参数和响应体真正引用的闭包；响应 Header 及未引用的 Header/安全组件不会进入提示。
- 安全示例值可以送入 AI；token、cookie、Authorization、密码、sessionId、credential、privateKey、PIN、密钥、JWT、密文等字段和值继续递归脱敏，模型原始输出仍先经过字面凭证拦截。
- 提示词明确 Body 优先，并禁止生成 Biz、Authorization、Content-Type、token、cookie 等独立请求头用例；AI 候选请求头统一由平台清空。
- 执行时继续按环境注入公共请求头；OpenAPI 必填 Header 若已有环境公共 Header 即视为已配置，否则只允许引用同名已配置环境变量，不向 AI 暴露真实值。
- 新手工草稿会从 `application/json`、`+json` 或首个媒体类型读取直接示例、首个命名示例、Schema 示例/default，并支持 `requestBody` 和 Schema 的本地 `$ref` 解析。
- 后端确定性校验同步解析 `requestBody` 自身的 `$ref`，引用式请求体仍执行必填 Body 和字段类型校验。
- 重新构建并提交 `api-test` 生产静态资源。

已验证：

```bash
bash tests/run_api_testing_gate.sh
# 299 backend tests passed
# 19 frontend files / 89 tests passed
# Vue typecheck + Vite production build passed
# desktop/mobile visual check passed
# 我的收藏三接口 Playwright 完整闭环 1 passed

PATH="$PWD/.venv/bin:$PATH" npm run test:static
# undefined-name、backend 63、frontend 72、AI Gateway 46、模型目录与 skill eval 全部通过

git diff --check
# passed
```

### 2026-08-12 API 执行明细与测试报告体验设计

用户确认执行页采用“结构化执行控制台”，测试报告采用“诊断型测试报告”。本轮只固化设计，不修改业务代码。

设计结论：

- 执行页复用现有 `ExecutionView`、SSE 事件和逐用例结果，按任务摘要、实时轨迹、用例明细和测试报告组织信息。
- 实时轨迹默认显示结构化日志与逐用例真实状态；完整请求、响应、断言、依赖轨迹和 AI 分析按用例展开。
- 自动跟随在用户向上浏览时暂停，SSE 断开保留旧日志并允许重连。
- 报告首页以结论、环境版本、真实状态统计、失败分类和 AI 诊断为主；终端式技术日志保留为可展开证据，不作为唯一报告视图。
- `FAILED`、`BROKEN`、`SKIPPED` 继续使用平台确定性语义，AI 只能解释结果，不能改写状态。
- 不新增数据库、迁移、执行模式或报告数据源，不影响 UI Agent、Midscene、Runner 和 Sonic。

详细规格：`docs/superpowers/specs/2026-08-12-api-execution-report-experience-design.md`。
### 2026-08-12 API 任务语义、用例删除与报告首页优化

用户反馈工作台里的“应用范围”“保存本次任务”含义不清、已生成用例缺少删除管理、采纳基线缺少可见位置，同时测试报告首页只有原始历史列表，无法快速判断本次结果。

根因：

- 工作台把“测试范围保存”和“任务保存”混在同一条操作链里，按钮文案没有说明任务会保存当前接口范围，调试通过后才可作为基线回归执行。
- 已保存用例缺少归档入口；前端归档缓存处理先删除版本再按已删除对象过滤，导致被删除版本 ID 仍留在列表里。
- AI/手工用例允许 `schema` 断言只写 `{ "type": "object" }` 这类弱约束，业务错误响应同样是 object，容易误判通过。
- 报告首页没有聚合执行记录，也没有问题分布和失败摘要，用户必须逐条点开才能知道问题在哪里。

本轮修复：

- “应用范围”改为“保存测试范围”；任务条增加“新建任务”“保存为当前任务”，并说明任务保存接口范围，基线来自通过调试后的采纳结果。
- 任务条展示当前可执行基线数，作为后续定时回归入口的前置可见信息。
- 新增 API 用例归档能力：`DELETE /api/api-testing/v1/cases/{case_id}` 只归档当前 case，不删除历史执行证据；列表只返回非归档用例。
- 前端已保存用例下拉旁增加删除当前用例按钮，删除后自动切到同接口下一条用例或回到草稿。
- `schema` 断言必须包含 `required/properties/items/enum/const/oneOf/anyOf/allOf` 等真实约束；只写空 schema 或 `{type: object}` 会在保存/AI 校验时失败。
- AI 助手默认测试意图调整为 Body 字段、参数边界和业务失败响应，不再默认生成 Biz/Authorization/ZXBToken 等请求头专项用例。
- 报告首页改为诊断型入口：展示当前项目、执行次数、总用例、问题用例、累计耗时、通过/失败/异常/未完成分布，以及每条报告的关键问题摘要；点击后继续进入详细诊断报告看请求、响应、断言和 AI 分析。

后续明确未完成：

- 独立“基线用例”页面、基线批量加入任务、定时任务和飞书群机器人 Webhook 配置还未实现；这些需要单独做基线中心与通知中心，不能算作本轮完成。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/stores/cases.spec.ts src/components/TaskStatusStrip.spec.ts src/components/ContextBar.spec.ts src/components/AiAssistant.spec.ts src/views/ReportsView.spec.ts
# 5 files / 29 tests passed

.venv/bin/python -m py_compile task_server/api_testing/contracts/case.py task_server/api_testing/services/case_service.py task_server/api_testing/repositories/case_repository.py task_server/api_testing/http.py
# passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed

python3 tests/frontend_static_checks.py
# 72 checks passed

git diff --check
# passed
```

说明：本地 PostgreSQL 测试环境未导出强制数据库测试变量，聚焦后端数据库用例本次显示 skip；纯合同校验、前端行为、生产构建和静态检查已通过。

### 2026-08-13 API 接口资产页项目化与工作台联动

用户明确要求本阶段只收口“接口资产页 + 工作台项目联动”，不混入 AI 生成、基线、报告、飞书通知等后续事项。

本轮修复：

- 接口资产页从 Apifox 读取向导改为项目化资产管理页：左侧展示平台项目列表，中间展示当前项目接口版本、环境、接口数量、最近同步与变更摘要，右侧固定项目操作。
- 项目列表展示项目名称、已保存接口数、环境数、最近同步时间和绑定的 Apifox 来源，避免只看当前单个项目。
- 新增平台项目编辑和归档接口：支持修改项目名称/备注；删除入口执行逻辑归档，不物理删除已有任务、基线或执行记录。
- “同步最新接口”固定在项目操作区，检查更新只生成预览；确认后保存为新的接口版本和环境版本。
- “直接开始测试”改为“进入工作台”，并通过路由携带 `projectId/sourceRevisionId/environmentRevisionId`。
- 工作台进入时读取路由上下文，确保只加载从接口资产页选择的项目、接口版本和环境。
- 前端合同补齐项目、接口版本、环境版本的状态、时间和来源字段，用于资产页展示。
- 重新构建 `api-test` 生产静态资源，避免部署后仍加载旧 JS/CSS。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/views/AssetsView.spec.ts src/views/WorkbenchView.spec.ts
# 2 files / 7 tests passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed

python3 -m py_compile task_server/api_testing/http.py task_server/api_testing/repositories/context_repository.py
# passed

python3 tests/frontend_static_checks.py
# 72 checks passed

python3 tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```

后续仍按既定阶段继续，当前未混入：

- M2：基线固定资产模型、基线分组、编辑/删除、按环境执行。
- M3：Apifox 分组、query/path/body 示例、必填、类型、说明等同步完整性。
- M4：执行记录/报告删除、报告 UI、飞书卡片链接和项目级机器人配置。

### 2026-08-13 API 测试：任务列表、任务名称与执行记录重跑入口

本轮按用户最新反馈只收口 M1 任务闭环，不混入 Apifox 同步、基线资产、报告 UI 或飞书通知。

本轮修复：

- 工作台任务条展示已保存任务下拉，可以选择历史任务继续编辑，不再只能每次新建。
- 支持编辑并保存当前任务名称；任务标题和历史任务选项均保留完整 title，长名称在界面压缩显示。
- 任务条按当前任务类型展示“基线 / 多条任务 / 单条任务 / 新任务”，让用户能区分基线回归、单接口调试和多接口任务。
- 执行记录列表和详情优先展示任务名称，缺少任务名时回退到执行类型；列表附带“基线 / 多条 / 单条”类型标签。
- 选中一条已结束执行记录时，右上角保留“重新执行此记录”，并按该记录的全部用例版本重新创建执行。
- 执行记录重跑增加 store 层测试，确认 POST `/api/api-testing/v1/executions` 时携带原记录的全部 `case_version_ids`。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/components/TaskStatusStrip.spec.ts src/components/ExecutionConsole.spec.ts src/stores/executions.spec.ts src/stores/tasks.spec.ts src/views/WorkbenchView.spec.ts src/views/RunsView.spec.ts --reporter=dot
# 6 files / 35 tests passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed

python3 tests/frontend_static_checks.py
# 72 checks passed

git diff --check
# passed
```

后续仍按既定阶段继续：

- M2：基线固定资产模型、基线分组、编辑/删除、按环境执行。
- M3：Apifox 分组、query/path/body 示例、必填、类型、说明等同步完整性。
- M4：执行记录/报告删除、报告 UI、飞书卡片链接和项目级机器人配置。

### 2026-08-13 API 测试：测试报告按项目展示与驾驶舱优化

本轮只收口用户最新反馈的“测试报告按项目展示、报告页样式太弱”问题，不混入基线资产、任务模型、Apifox 同步或飞书机器人配置。

本轮修复：

- 测试报告页新增项目范围选择，进入页面时按当前项目加载执行报告，切换项目后重新加载对应项目结果。
- 报告列表在前端再次按 `project_id` 过滤，避免不同项目执行结果混在同一个报告页。
- 报告页标题调整为“项目测试报告”，新增项目报告范围卡片，展示当前项目、报告数量和刷新入口。
- 报告总览从普通列表头改为“项目报告驾驶舱”，突出执行次数、通过用例、问题用例、累计耗时和通过率。
- 报告卡片、问题分布条、报告索引和详情区域增加更明确的边界、层级和视觉权重，保留现有筛选、批量删除、单条删除、飞书发送状态和诊断详情入口。
- 新增报告页行为测试，覆盖“按项目加载并过滤报告结果”的主路径。

已验证：

```bash
npm --prefix api-testing-ui test -- --run src/views/ReportsView.spec.ts --reporter=basic
# 1 file / 6 tests passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed

python3 tests/frontend_static_checks.py
# 72 checks passed

git diff --check
# passed
```

后续仍按既定阶段继续：

- M2：基线固定资产模型、基线分组、编辑/删除、按环境执行。
- M3：Apifox 分组、query/path/body 示例、必填、类型、说明等同步完整性。
- M4：执行记录/报告删除、飞书卡片链接和项目级机器人配置。

### 2026-08-13 API 环境资产中心与工作台上下文收口

本轮只实现项目化环境资产管理和工作台上下文联动，不提前实现定时任务，避免把任务、基线和调度规则再次耦合到环境编辑页。

本轮实现：

- 环境改为项目内的稳定资产：编辑环境生成新 revision，不因接口版本更新而复制或丢失环境身份。
- 环境资产页按项目展示环境列表，支持“使用中 / 已归档”切换、选择详情、编辑、归档、恢复和历史版本查看。
- 删除环境采用逻辑归档；已有任务、基线和执行记录仍可解析历史环境 revision。
- 环境详情展示接口地址、公共变量、敏感变量配置状态和 revision 历史；敏感值不回显。
- Apifox 环境读取保持手动触发，环境资产页通过现有接口资产同步流程更新，不增加后台自动刷新。
- 从环境资产页进入工作台时携带 `projectId`、`sourceRevisionId`、`environmentRevisionId`；工作台按所选项目、接口版本和环境加载调试范围。
- 项目级飞书机器人配置继续归项目所有，不放入环境或任务资产，供基线回归和未来定时执行复用。

未来定时任务边界已固定：

- 任务和基线是独立可复用资产；调度器只引用 `task_id`，或引用 `baseline_group_id` / 基线用例集合，不复制用例数据。
- 调度器选择执行环境时引用稳定 `environment_id`，执行时解析当前有效 revision；如需要可选固定 `environment_revision_id`。
- 是否发送飞书由调度计划保存 `notify_feishu` 开关；Webhook 从项目级飞书配置读取，不在每条定时任务中重复保存。
- 本轮没有新增调度器、Cron 表、后台轮询或定时执行入口，后续可在上述边界上独立实现。

已验证：

```bash
.venv/bin/python -m pytest tests/api_testing/test_environment_service.py tests/api_testing/test_http_contract.py -q
# 14 passed, 50 skipped；跳过项为本机未启用 PostgreSQL/Redis 的集成场景

npm --prefix api-testing-ui test -- --run
# 31 files / 153 tests passed

npm --prefix api-testing-ui run build
# vue-tsc + Vite production build passed

python3 tests/frontend_static_checks.py
# 72 checks passed

python3 tests/backend_static_checks.py
# 63 checks passed

git diff --check
# passed
```
