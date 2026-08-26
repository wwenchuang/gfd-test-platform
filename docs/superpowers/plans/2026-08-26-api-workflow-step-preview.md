# API 前置步骤试运行与响应取值 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在用例编辑器中执行到指定前置步骤，展示真实响应并把勾选字段转换成可复用输出变量。

**Architecture:** 新增无持久化的前置步骤预览服务，复用 `HttpExecutor` 的正式请求、断言和提取路径；前端通过独立响应选择面板把字段选择回填现有 `extractions`，预览覆盖值只保存在当前组件会话。正式执行轨迹继续使用原有脱敏结果。

**Tech Stack:** Python 3、现有 WSGI HTTP adapter、SQLAlchemy、Vue 3、Pinia、TypeScript、Vitest、Playwright。

## Global Constraints

- 预览不执行主体请求，不创建执行记录。
- 动态 Token 可主动查看和编辑，但不得持久化到用例、日志或报告。
- 所有请求语义复用现有 `HttpExecutor`，不复制 HTTP 客户端逻辑。
- 响应字段最多 500 个、最大深度 16。
- 生产 setup/main/always-run-cleanup 语义保持不变。

---

### Task 1: 后端预览执行契约

**Files:**
- Modify: `task_server/api_testing/contracts/case.py`
- Modify: `task_server/api_testing/executor.py`
- Create: `task_server/api_testing/services/workflow_step_preview_service.py`
- Test: `tests/api_testing/test_workflow_step_preview_service.py`

**Interfaces:**
- Produces: `WorkflowStepPreviewService.preview(payload, actor_id) -> dict`
- Produces: `HttpExecutor.preview_setup_steps(environment_revision_id, setup_steps, target_index, initial_variables, processing_pre, extraction_overrides) -> dict`

- [ ] **Step 1: Write failing service tests** for prefix execution, extracted-variable propagation, preview override application, failed-prefix blocking, response field flattening and sensitive-field metadata.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/api_testing/test_workflow_step_preview_service.py -q` and confirm the missing service/API fails.
- [ ] **Step 3: Implement strict preview parsing and executor prefix execution** while keeping sanitized formal traces unchanged and raw target response request-scoped.
- [ ] **Step 4: Run the focused tests** and confirm all preview service cases pass.

### Task 2: HTTP endpoint and ownership gate

**Files:**
- Modify: `task_server/api_testing/http.py`
- Test: `tests/api_testing/test_http_contract.py`

**Interfaces:**
- Consumes: `WorkflowStepPreviewService.preview()`
- Produces: `POST /api/api-testing/v1/workflow-steps/preview`

- [ ] **Step 1: Write failing HTTP contract tests** for authenticated success, foreign environment rejection, invalid target index and response envelope.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/api_testing/test_http_contract.py -q -k workflow_step_preview` and confirm 404/contract failure.
- [ ] **Step 3: Add the scoped route** and map payload errors to the existing error envelope.
- [ ] **Step 4: Run focused HTTP tests** and confirm they pass.

### Task 3: 前端响应字段选择面板

**Files:**
- Modify: `api-testing-ui/src/api/contracts.ts`
- Create: `api-testing-ui/src/components/WorkflowStepPreviewPanel.vue`
- Create: `api-testing-ui/src/components/WorkflowStepPreviewPanel.spec.ts`
- Modify: `api-testing-ui/src/components/InlineWorkflowStepEditor.vue`
- Modify: `api-testing-ui/src/components/InlineWorkflowStepEditor.spec.ts`

**Interfaces:**
- Produces: `WorkflowStepPreviewPanel` events `apply(extractions, overrides)` and `close()`.
- Consumes: preview response fields and current extraction rules.

- [ ] **Step 1: Write failing component tests** for field search, grouping, sensitive reveal, edited preview override, variable-name editing and extraction de-duplication.
- [ ] **Step 2: Run** `npm --prefix api-testing-ui test -- --run src/components/WorkflowStepPreviewPanel.spec.ts src/components/InlineWorkflowStepEditor.spec.ts` and confirm missing UI behavior fails.
- [ ] **Step 3: Implement the panel and preview request flow** with busy/error states and local-only override storage.
- [ ] **Step 4: Run focused component tests** and confirm they pass.

### Task 4: 编辑器上下文与变量校验

**Files:**
- Modify: `api-testing-ui/src/components/CaseEditor.vue`
- Modify: `api-testing-ui/src/views/WorkbenchView.vue`
- Modify: `api-testing-ui/src/utils/caseDraftValidation.ts`
- Modify: `api-testing-ui/src/utils/caseDraftValidation.spec.ts`
- Modify: `api-testing-ui/src/styles/app.css`

**Interfaces:**
- Consumes: current environment revision and complete draft prefix.
- Produces: preview payload and user-facing missing-variable feedback.

- [ ] **Step 1: Write failing validation and editor integration tests** for environment absence, invalid target step and unknown variable references.
- [ ] **Step 2: Run focused tests** and confirm expected failures.
- [ ] **Step 3: Wire environment context, draft prefix, validation navigation and responsive layout.**
- [ ] **Step 4: Run focused tests** and confirm pass.

### Task 5: 文档、完整验证和交付

**Files:**
- Modify: `docs/api-testing-ux-audit-2026-08-26.md`
- Modify: `CODEX_STATE.md`
- Modify: `tests/api_testing_e2e.spec.mjs`

**Interfaces:**
- Produces: repeatable acceptance evidence and deployment-ready main branch.

- [ ] **Step 1: Extend Chromium E2E** to preview a setup step, select a response field and verify extraction insertion without persisting a raw token.
- [ ] **Step 2: Run** `bash tests/run_api_testing_gate.sh`, static checks, `git diff --check`, and relevant `py_compile` commands.
- [ ] **Step 3: Review the diff** for secret persistence, regressions and unrelated changes; fix all findings and rerun affected checks.
- [ ] **Step 4: Update audit/state documentation**, commit all intended files, and push `main` to `origin`.
