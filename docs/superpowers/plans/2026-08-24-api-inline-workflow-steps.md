# API Inline Workflow Steps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add editable, executable setup and always-run cleanup steps to API cases, and require successful print cancellation before a print-dispatch case can pass as a baseline.

**Architecture:** Extend the existing strict case contract inside `processing_spec` so no schema migration is needed. Extract one-request execution into a reusable executor primitive, orchestrate setup/main/cleanup around it, and expose step evidence through the existing trace payload. Keep cross-case dependencies unchanged for reusable flows.

**Tech Stack:** Python 3, dataclasses, SQLAlchemy JSONB, pytest, Vue 3, TypeScript, Vitest.

## Global Constraints

- Never persist or log the user-provided JWT; authentication continues through environment runtime injection.
- Cleanup steps run after setup or main failures and after normal cancellation, subject to bounded HTTP timeouts.
- Print dispatch cannot be baseline-ready unless its cancel step is configured and succeeds.
- Preserve existing cases that only contain `processing.pre` and `processing.post`.
- Do not add a database migration.

---

### Task 1: Strict inline-step contract

**Files:**
- Modify: `task_server/api_testing/contracts/case.py`
- Modify: `task_server/api_testing/validation.py`
- Test: `tests/api_testing/test_case_contract.py`

**Interfaces:**
- Consumes: existing `_parse_request`, `_parse_assertions`, `_parse_extractions` helpers.
- Produces: `processing.setup_steps` and `processing.cleanup_steps`, each containing `name`, `enabled`, `request`, `assertions`, `extractions`, and `required_variables`.

- [ ] **Step 1: Write failing parser tests** for valid steps, unknown fields, duplicate names, more than 20 steps, and invalid required variable names.
- [ ] **Step 2: Run** `pytest -q tests/api_testing/test_case_contract.py` and confirm failures come from unsupported `setup_steps` / `cleanup_steps`.
- [ ] **Step 3: Implement `_parse_inline_steps`** by reusing strict request/assertion/extraction parsing and accepting at most 20 uniquely named steps per phase.
- [ ] **Step 4: Extend semantic validation** so setup exports are defined for later setup/main/cleanup and cleanup missing required variables is reported before execution.
- [ ] **Step 5: Run** `pytest -q tests/api_testing/test_case_contract.py tests/api_testing/test_validation.py` and confirm green.

### Task 2: Setup/main/cleanup executor

**Files:**
- Modify: `task_server/api_testing/executor.py`
- Test: `tests/api_testing/test_executor.py`

**Interfaces:**
- Consumes: parsed inline steps and existing runtime rendering, extraction, assertion, redaction, timeout, and host-policy functions.
- Produces: trace entries with `phase=workflow_step`, `stage`, `name`, `index`, `status`, request/response/assertion evidence, and extracted variable names.

- [ ] **Step 1: Write failing HTTP integration tests** proving setup extraction feeds the main request and main extraction feeds cleanup.
- [ ] **Step 2: Write failing failure-path tests** proving setup failure skips main, main failure still runs cleanup, missing cleanup variables skip only that step, and cleanup failure changes an otherwise passing result to `FAILED / cleanup`.
- [ ] **Step 3: Run the focused tests** and confirm they fail because the executor ignores inline steps.
- [ ] **Step 4: Extract a private single-request executor** from the current main request logic without changing old-case behavior.
- [ ] **Step 5: Add workflow orchestration** with shared variables and `finally` cleanup semantics. Keep main request/response in top-level result fields and put setup/cleanup evidence in trace.
- [ ] **Step 6: Run** `pytest -q tests/api_testing/test_executor.py tests/api_testing/test_execution_service.py` and confirm old dependency orchestration remains green.

### Task 3: Print cancellation generation and validation

**Files:**
- Modify: `task_server/api_testing/services/basic_case_service.py`
- Modify: `task_server/api_testing/services/response_assertion_policy.py`
- Modify: `task_server/api_testing/validation.py`
- Test: `tests/api_testing/test_basic_case_service.py`
- Test: `tests/api_testing/test_validation.py`

**Interfaces:**
- Consumes: active-revision endpoint catalog, endpoint operation schema/examples, and shared response assertion policy.
- Produces: a print-dispatch draft with a task-ID extraction and cancel-print cleanup step, or a blocking validation issue when the pair cannot be inferred safely.

- [ ] **Step 1: Write failing tests** for unique cancel-endpoint matching, task-ID JSONPath inference, ambiguous cancel endpoints, and missing task-ID response fields.
- [ ] **Step 2: Run focused tests** and confirm current generator does not create cleanup steps.
- [ ] **Step 3: Add semantic endpoint matching** limited to the same active source revision and exact print lifecycle vocabulary.
- [ ] **Step 4: Add response-schema/example extraction inference** for cancel endpoint required fields; do not invent fixed IDs.
- [ ] **Step 5: Add baseline readiness validation** that blocks print-dispatch cases without a valid cancel step.
- [ ] **Step 6: Run** `pytest -q tests/api_testing/test_basic_case_service.py tests/api_testing/test_validation.py`.

### Task 4: Structured case editor

**Files:**
- Create: `api-testing-ui/src/components/InlineWorkflowStepEditor.vue`
- Modify: `api-testing-ui/src/components/CaseEditor.vue`
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/stores/cases.ts`
- Modify: parent views that render `CaseEditor.vue`
- Test: `api-testing-ui/src/components/__tests__/CaseEditor.spec.ts`

**Interfaces:**
- Consumes: available project endpoint options and `CaseDraft.processing`.
- Produces: ordered setup/cleanup arrays with add, delete, move, enable, endpoint selection, request, assertion, extraction, and required-variable controls.

- [ ] **Step 1: Write failing component tests** for adding a setup step, choosing an endpoint, moving steps, and adding a cleanup required variable.
- [ ] **Step 2: Run** `npm --prefix api-testing-ui test -- --run src/components/__tests__/CaseEditor.spec.ts` and confirm missing controls.
- [ ] **Step 3: Extend TypeScript contracts and draft normalization** so old cases receive empty step arrays.
- [ ] **Step 4: Build the step editor** with stable compact rows and expandable details; method/path auto-fill from the selected endpoint.
- [ ] **Step 5: Integrate three-stage layout** into `CaseEditor.vue` and pass endpoint options from both workbench and standalone case management.
- [ ] **Step 6: Run the focused component tests and TypeScript build**.

### Task 5: Step-aware execution report

**Files:**
- Modify: `api-testing-ui/src/components/CaseEvidence.vue`
- Test: `api-testing-ui/src/components/__tests__/CaseEvidence.spec.ts`

**Interfaces:**
- Consumes: `workflow_step` trace entries.
- Produces: grouped setup/main/cleanup evidence with status and failure details.

- [ ] **Step 1: Write a failing component test** with one passed setup step, one failed main step, and one passed cleanup step.
- [ ] **Step 2: Run the test** and confirm workflow evidence is not rendered.
- [ ] **Step 3: Render collapsible stage rows** while preserving the existing raw trace fallback.
- [ ] **Step 4: Run the focused test and frontend static checks**.

### Task 6: Verification, state, and delivery

**Files:**
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Consumes: all completed implementation tasks.
- Produces: one pushed `main` revision with deployment instructions.

- [x] **Step 1: Run backend tests** for contracts, validation, executor, generation, and execution service.
- [x] **Step 2: Run repository checks:** `python3 tests/backend_static_checks.py`, `python3 tests/frontend_static_checks.py`, and `git diff --check`.
- [x] **Step 3: Run frontend tests/build** with the repository package manager.
- [x] **Step 4: Update `CODEX_STATE.md`** with behavior, verification, and deployment notes without credentials.
- [x] **Step 5: Review the final diff** for credential leakage and unrelated changes.
- [x] **Step 6: Commit and push `main`**, then report the exact commit and QA deployment command.
