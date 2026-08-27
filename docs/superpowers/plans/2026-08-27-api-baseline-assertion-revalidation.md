# API Baseline Assertion Revalidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用历史真实响应和受控重跑重新验证约 240 条活动 API 基线，并通过不可变版本升级补齐精确断言。

**Architecture:** 新增按需读取的基线断言审计服务，批量关联活动基线、断言和采纳调试响应，不增加数据库表。审计只给状态、证据摘要和建议；实际修改继续使用现有“新建用例版本 -> 调试 -> 采纳基线”门禁。

**Tech Stack:** Python 3、SQLAlchemy、PostgreSQL、Vue 3、Pinia、Vitest、pytest。

## Global Constraints

- 不在源码、文档或执行证据中保存 Authorization、Biz、临时凭证和设备秘密。
- 不直接修改历史用例版本或历史基线。
- 写操作必须满足准备、主体、始终清理；下发打印必须验证取消成功。
- JSON 断言使用严格类型比较；布尔值不能与数值业务码互相匹配。
- 安全复核选择和最终执行门禁都必须核对审计证据环境，切换环境后不能沿用旧候选。
- 线上未完成逐条实际响应复核前，不报告“240 条已调整完成”。

---

### Task 1: 真实响应断言审计

**Files:**
- Create: `task_server/api_testing/services/baseline_assertion_audit_service.py`
- Modify: `task_server/api_testing/repositories/case_repository.py`
- Modify: `task_server/api_testing/http.py`
- Test: `tests/api_testing/test_baseline_assertion_audit_service.py`
- Test: `tests/api_testing/test_http_contract.py`

**Interfaces:**
- Consumes: 活动 `ApiBaseline`、`ApiCaseAssertion`、采纳 `ApiExecutionCase` 和最新 `ApiExecutionAttempt.response`。
- Produces: `BaselineAssertionAuditService.list(project_id, actor_id)` 返回汇总和逐条审计结果。

- [x] **Step 1: 写失败测试，覆盖精确成功、可补成功码、业务失败、精确负向、无业务字段和无证据。**
- [x] **Step 2: 运行聚焦 pytest，确认新服务尚不存在而失败。**
- [x] **Step 3: 实现批量证据读取、脱敏响应解析、断言判定和安全执行分级。**
- [x] **Step 4: 增加 `GET /api/api-testing/v1/baselines/assertion-audit?project_id=...` 并验证权限边界。**
- [x] **Step 5: 运行聚焦后端测试并检查 `git diff --check`。**

### Task 2: 基线页复核入口

**Files:**
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/stores/baselines.ts`
- Modify: `api-testing-ui/src/views/BaselinesView.vue`
- Test: `api-testing-ui/src/stores/baselines.spec.ts`
- Test: `api-testing-ui/src/views/BaselinesView.spec.ts`

**Interfaces:**
- Consumes: Task 1 的 `summary/items` 审计合同。
- Produces: 检查入口、中文状态汇总、状态筛选、实际业务值和“选择可安全复核项”。

- [x] **Step 1: 写失败 Vitest，覆盖按需加载、中文状态和安全选择。**
- [x] **Step 2: 运行聚焦 Vitest，确认 UI 尚无复核入口而失败。**
- [x] **Step 3: 实现审计状态、筛选和逐条下一步，不自动修改或执行基线。**
- [x] **Step 4: 运行聚焦 Vitest、TypeScript 构建和前端静态检查。**

### Task 3: 线上逐批迁移

**Files:**
- Modify: `docs/api-testing-full-flow-audit-2026-08-27.md`
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Consumes: 已部署的断言审计、当前环境、Chrome 登录会话和执行报告。
- Produces: 每类数量、执行 ID、升级基线 ID、未升级原因和清理结果记录。

- [ ] **Step 1: Chrome 检查审计总数与平台当前活动基线数量一致。**
- [ ] **Step 2: 先执行只读且可安全复核项，逐条查看实际响应和新断言结果。**
- [ ] **Step 3: 按上下游完整度执行受控写操作；清理失败立即停止该链并记录。**
- [ ] **Step 4: 对通过项创建新版本、调试、采纳；确认旧基线只进入历史。**
- [ ] **Step 5: 汇总业务失败、证据不足和人工项，不把未处理项计入完成。**

### Task 4: 完整门禁与交付

**Files:**
- Modify: `CODEX_STATE.md`
- Modify: `docs/api-testing-full-flow-audit-2026-08-27.md`

**Interfaces:**
- Consumes: Task 1-3 的代码和真实执行记录。
- Produces: 可复现验证结果、提交和部署版本。

- [x] **Step 1: 运行 API 聚焦测试、完整 API testing gate 和静态检查。**
- [ ] **Step 2: Chrome 复验基线列表、用例编辑、调试、采纳、回归和报告上下游。**
- [x] **Step 3: 更新完成、延期和未验证边界，提交并推送 `main`。**
