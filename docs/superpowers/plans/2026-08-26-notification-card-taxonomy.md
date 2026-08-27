# UI 与 API 通知卡片统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 UI 自动化和 API 测试通知准确区分“智小白3D”应用、家用/共享业务、测试类型、执行场景与触发来源。

**Architecture:** 新增一个无状态展示辅助模块，分别负责应用名称归一化和家用/共享业务识别。Sonic/UI 与 API 通知继续由现有两个渲染器独立构建卡片，分别传入固定测试类型和各自场景、任务、环境、报告数据。

**Tech Stack:** Python、Feishu interactive card JSON、pytest、仓库静态检查。

## Global Constraints

- UI 自动化通知不得出现 API 测试类型。
- API 通知不得出现 UI 自动化测试类型。
- 应用、业务、测试类型、执行场景和触发方式必须独立表达。
- 应用固定归一为“智小白3D”，家用和共享不得再作为应用名展示。
- 保留现有 Webhook 路由、执行结果判断、失败归因和精确报告链接。
- 不改数据库结构，不修改 Runner、Sonic 或 API 执行协议。

---

### Task 1: 应用与业务展示归一化

**Files:**
- Create: `task_server/services/notification_presentation.py`
- Create: `tests/test_notification_presentation.py`

**Interfaces:**
- Produces: `canonical_test_application_name(value: object, package: str = "") -> str`
- Produces: `canonical_test_business_name(*values: object) -> str`

- [x] **Step 1: Write the failing test**

增加参数化测试，覆盖智小白3D应用别名、家用/共享业务识别、共享线索优先级、包名回退和未知项目保留。

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_notification_presentation.py -q`

Expected: FAIL，因为展示辅助模块尚不存在。

- [x] **Step 3: Write minimal implementation**

实现精确应用别名映射、业务线索识别、空白清理和 `com.kfb.model` 包名回退；共享线索优先于智小白3D默认家用口径。

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_notification_presentation.py -q`

Expected: PASS。

### Task 2: UI/Sonic 卡片修复

**Files:**
- Modify: `task_server/services/sonic_service.py`
- Modify: `tests/test_sonic_integration.py`

**Interfaces:**
- Consumes: `canonical_test_application_name`、`canonical_test_business_name`
- Produces: `build_sonic_suite_summary_card(suite: dict) -> dict`

- [x] **Step 1: Write the failing test**

更新成功、失败、家用和共享断言，要求标题使用“智小白3D｜业务｜UI 自动化”，正文包含业务、执行场景与可选测试套，并拒绝“API 基线”文案。

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sonic_integration.py -k "sonic_suite_summary_card or leadership_sonic_suite_card" -q`

Expected: FAIL，现有卡片仍硬编码 API。

- [x] **Step 3: Write minimal implementation**

标题改为“应用｜业务｜UI 自动化｜场景+结论”；正文分组展示结论、应用、业务、测试类型、执行场景、测试套、指标、范围和报告按钮，统计分隔符统一使用中文竖线。

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sonic_integration.py -k "sonic_suite_summary_card or leadership_sonic_suite_card" -q`

Expected: PASS。

### Task 3: API 卡片修复

**Files:**
- Modify: `task_server/api_testing/services/notification_service.py`
- Modify: `tests/api_testing/test_notification_service.py`

**Interfaces:**
- Consumes: `canonical_test_application_name`、`canonical_test_business_name`
- Produces: `NotificationService._card(execution, children, metadata) -> dict`

- [x] **Step 1: Write the failing test**

更新卡片断言，要求标题使用“智小白3D｜业务｜API 测试”，正文独立展示业务、执行场景与触发方式；增加家用/共享项目名测试。

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/api_testing/test_notification_service.py -q`

Expected: FAIL，现有卡片仍使用“API 基线测试”和“任务类型”。

- [x] **Step 3: Write minimal implementation**

新增场景与触发方式标签函数，重组标题和正文；保留失败摘要与 `_report_url(execution.id, execution.project_id)`。

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/api_testing/test_notification_service.py -q`

Expected: PASS。

### Task 4: 全面验证与交接

**Files:**
- Modify: `CODEX_STATE.md`

- [x] **Step 1: Run notification regressions**

Run: `.venv/bin/python -m pytest tests/test_notification_presentation.py tests/api_testing/test_notification_service.py -q`

Run: `.venv/bin/python -m pytest tests/test_sonic_integration.py -k "sonic_completed_suite_reports_missing_task_callbacks or sonic_final_success_overrides_failed_task_callback_in_summary or leadership_sonic_suite_card or sonic_suite_card_uses_shared_business_context" -q`

Expected: PASS。

- [x] **Step 2: Run repository checks**

Run: `python3 tests/backend_static_checks.py`

Run: `python3 -m py_compile task_server/services/notification_presentation.py task_server/services/sonic_service.py task_server/api_testing/services/notification_service.py`

Run: `git diff --check`

Expected: 全部通过。

- [x] **Step 3: Review semantic output**

从测试夹具生成一张 UI 成功卡、一张 UI 失败卡、一张 API 成功卡和一张 API 失败卡，检查标题、字段顺序、中文统计和报告动作，不真实发送飞书消息。

- [x] **Step 4: Update handoff**

在 `CODEX_STATE.md` 记录修改范围、验证结果、未真实发送飞书的边界和部署后复核项。
