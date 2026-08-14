# 脑图用例测试报告设计

## 背景

平台已经支持从需求、Figma、截图和已有用例生成完整 FreeMind `.mm` 脑图，并保存结构化 `summary.json`。现有“执行报告”页面主要展示 Runner / Sonic / Midscene 的执行结果，不支持测试人员从脑图中心选择需求用例后生成正式测试报告。

本功能新增“脑图用例测试报告”：报告主体来自脑图用例，支持自定义报告标题、测试时间、测试人员、测试端侧、需求链接、用例链接和模板。若选中用例已经有关联执行结果，则在报告中补充通过率、失败明细和报告链接；没有执行结果时仍可生成测试设计报告，并明确标记“未执行 / 未关联”。

## 目标

- 在脑图中心选择一个 `.mm` / `case_set_id`，进入报告生成流程。
- 支持按功能点、场景、优先级、冒烟、自动化/人工用例筛选并勾选用例。
- 支持填写报告元信息：标题、测试周期、测试人员、涉及端侧、版本、需求链接、用例链接、测试环境和备注。
- 支持上传模板生成报告，第一期支持 Markdown / HTML 模板。
- 默认报告模板参考用户提供的基础模板，并优化测试范围展示，避免把脑图节点逐条铺满报告。
- 支持生成 HTML 与 Markdown 产物，并保存历史记录。

## 非目标

- 第一期不生成 Word / PDF。报告数据结构和模板占位符预留扩展点，后续可加 `.docx` / PDF。
- 不改变现有 Runner 报告索引语义，`/api/reports` 继续代表 Midscene 执行报告。
- 不让 AI 根据报告绕过冒烟门禁或继续执行阈值。
- 不强制上传外部 `.mm`。平台自身生成的脑图优先读取 `summary.json`，只有外部模板或外部 `.mm` 才走解析。

## 默认模板

默认报告结构：

1. 基本信息
   - 测试周期
   - 测试人员
   - 涉及端侧
   - 测试版本
   - 测试环境

2. 测试概要
   - 需求链接
   - 测试用例链接
   - 测试目标
   - 测试范围

3. 测试数据
   - 用例统计：总计、通过、失败、阻塞、未执行、通过率
   - 缺陷统计：总计、按严重程度统计（若有）

4. 质量评估
   - 测试结果：通过 / 不通过 / 有风险通过 / 未执行
   - 主要风险
   - 遗留问题

5. 发布建议
   - 建议发布 / 有条件发布 / 不建议发布 / 仅完成测试设计
   - 建议说明

6. 附录
   - 选中用例明细
   - 失败用例明细
   - 关联 Midscene / Sonic 报告链接

## 测试范围精简规则

测试范围必须精简，不直接输出脑图的完整层级和每条步骤。

默认展示规则：

- 按“功能点 / 场景组”聚合。
- 每个功能点最多展示 3 个核心场景。
- 每个场景只展示关联用例编号范围或前 3 个代表用例，例如 `TC-002, TC-003, TC-004`。
- 优先展示 P0 / P1 / 冒烟用例覆盖到的范围。
- 人工用例只汇总数量和关键风险，不默认展开步骤。
- 若范围超过 8 个场景，报告正文只展示 Top 8，完整明细放入附录。

示例：

```markdown
测试范围：

1. 管理员分成与权限
   - 模块与权限：验证经销商、管理员角色菜单和模块访问差异（TC-001）
   - 管理员分成设置：验证入口展示、基础设置和保存反馈（TC-002 ~ TC-004）

2. 商户分润
   - 分润配置：验证商户分润规则创建、编辑和生效展示（TC-005 ~ TC-007）
```

## 模板能力

第一期模板支持 Markdown / HTML 文本模板。模板上传后保存到 `LEARNING_DIR/test-report-templates/`，并生成模板索引。

模板占位符：

- `{{title}}`
- `{{report_title}}`
- `{{test_start}}`
- `{{test_end}}`
- `{{tester}}`
- `{{client_side}}`
- `{{version}}`
- `{{environment}}`
- `{{requirement_link}}`
- `{{case_link}}`
- `{{test_goal}}`
- `{{test_scope}}`
- `{{summary_table}}`
- `{{case_table}}`
- `{{failure_table}}`
- `{{manual_case_table}}`
- `{{quality_assessment}}`
- `{{release_suggestion}}`
- `{{generated_at}}`

模板缺少关键占位符时，系统不报错，但会在模板正文后补充默认缺失区块，避免生成空报告。

## 数据来源

平台内部脑图：

- 使用 `case_set_id` 定位 `CASE_DIR/<case_set_id>/summary.json`。
- 从 `cases`、`manual_cases`、`scenarios`、`report_checkpoints` 生成可选择用例树和报告内容。
- 如果 `cases.mm` 缺失，可继续使用 `summary.json` 生成报告。

外部 `.mm`：

- 使用 XML 解析 FreeMind 节点。
- 优先识别平台约定层级：功能点 -> 场景 -> 用例 -> 测试步骤 / 预期结果。
- 解析失败时返回可读错误，不生成半结构化空报告。

执行结果：

- 可选读取现有 `latestJobs` / `report-index.json` / Sonic 结果。
- 通过 `case_id`、YAML 文件名、任务名进行保守匹配。
- 匹配不到的用例标记为“未执行 / 未关联”。
- 失败归因只引用已有 Runner / Agent / Sonic 证据。

## 后端设计

新增服务：`task_server/services/test_report_service.py`

核心函数：

- `list_reportable_case_sets(limit=100)`：复用脑图中心记录。
- `load_reportable_cases(case_set_id)`：读取用例树、统计和默认选择状态。
- `preview_test_report(payload)`：生成预览模型，不落盘。
- `create_test_report(payload)`：生成并保存报告。
- `list_test_reports(case_set_id=None, limit=100)`：读取历史测试报告。
- `read_test_report(report_id)`：读取报告元数据和内容。
- `render_test_report(data, template_id=None, format="html")`：渲染默认或自定义模板。
- `save_test_report_template(files)`：保存模板。

新增路由：

- `GET /api/test-reports/cases?case_set_id=...`
- `POST /api/test-reports/preview`
- `POST /api/test-reports`
- `GET /api/test-reports?case_set_id=...`
- `GET /api/test-reports/{report_id}`
- `GET /api/test-reports/{report_id}/download?format=html|md`
- `GET /api/test-reports/templates`
- `POST /api/test-reports/templates`

存储：

```text
CASE_DIR/<case_set_id>/test-reports/<report_id>/
  report.json
  report.md
  report.html

LEARNING_DIR/test-report-index.json
LEARNING_DIR/test-report-templates/
  <template_id>.json
  <template_id>.md
```

## 前端设计

脑图中心新增操作：

- 每条脑图记录增加“生成报告”按钮。
- 报告生成页使用两栏布局。

左侧：用例选择

- 用例树按功能点和场景分组。
- 支持搜索、优先级筛选、冒烟筛选、自动化/人工筛选。
- 默认选中 P0 / P1 / 冒烟自动化用例。
- 提供“全选当前筛选”“仅选冒烟”“包含人工用例”“清空选择”。

右侧：报告信息与预览

- 报告标题默认：`<脑图标题>-测试报告`。
- 测试周期支持开始/结束日期。
- 测试人员、涉及端侧、版本、环境、需求链接、用例链接、备注可编辑。
- 模板下拉与上传入口。
- 预览区展示默认模板渲染后的正文摘要。
- 生成成功后提供“打开 HTML”“下载 Markdown”“回到脑图中心”。

## 质量和发布规则

质量评估默认规则：

- 有执行结果且失败数为 0、阻塞数为 0：`通过`。
- 有失败但均为低风险且用户勾选“允许有风险通过”：`有风险通过`。
- 有 P0 / P1 失败或阻塞：`不通过`。
- 没有任何执行结果：`未执行，仅完成测试设计`。

发布建议默认规则：

- `通过`：建议发布。
- `有风险通过`：有条件发布，并列出风险。
- `不通过`：不建议发布。
- `未执行`：不输出“建议发布”，只输出“仅完成测试设计，需补充执行验证”。

## 错误处理

- `case_set_id` 不存在：提示“脑图记录不存在或已删除”。
- 选中用例为空：禁止生成，提示“请至少选择一条用例”。
- 模板解析失败：提示具体模板文件和错误位置，允许切回默认模板。
- 外部 `.mm` 不符合 FreeMind XML：提示“无法识别脑图结构”。
- 执行结果匹配不到：不阻塞生成，报告中标记为“未执行 / 未关联”。

## 测试计划

后端：

- 测试从 `summary.json` 加载自动化和人工用例。
- 测试默认选择 P0 / P1 / 冒烟用例。
- 测试测试范围聚合不超过配置上限。
- 测试无执行结果时报告状态为“未执行”。
- 测试有成功 / 失败 / 阻塞结果时统计和发布建议正确。
- 测试模板缺失占位符时自动补默认区块。

前端：

- 测试脑图中心展示“生成报告”入口。
- 测试用例筛选和批量选择。
- 测试报告表单字段提交。
- 测试模板上传和切换。
- 测试生成成功后的打开 / 下载入口。

静态检查：

```bash
python3 -m py_compile task_server/services/test_report_service.py task_server/router.py
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
git diff --check
```
