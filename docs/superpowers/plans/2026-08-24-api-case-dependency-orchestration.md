# API Case Dependency Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute saved API case dependencies in deterministic order, pass allowlisted extracted variables, skip blocked dependents truthfully, and make dependency selection usable in the editor.

**Architecture:** `ExecutionService` expands immutable case-version dependencies at submit time and records the expanded roles in the execution snapshot. The worker resolves direct dependency outcomes before each child and supplies only declared exports as runtime overrides. `CaseEditor` receives grouped dependency options from the existing case store and emits normal case drafts without a parallel state model.

**Tech Stack:** Python 3, SQLAlchemy, PostgreSQL, pytest, Vue 3, TypeScript, Pinia, Vitest.

## Global Constraints

- Keep the existing 500-case execution limit after dependency expansion.
- Do not alter cases without dependencies.
- Never expose extracted secrets or undeclared variables.
- Required dependency failures produce `SKIPPED / dependency`; optional dependency failures do not block.
- No automatic dependency inference or historical case rewrite.

---

### Task 1: Dependency graph expansion

**Files:**
- Modify: `tests/api_testing/test_execution_service.py`
- Modify: `tests/api_testing/test_executor.py`
- Modify: `task_server/api_testing/executor.py`
- Modify: `task_server/api_testing/services/execution_service.py`
- Modify: `task_server/api_testing/repositories/execution_repository.py`

**Interfaces:**
- Consumes: immutable `ApiCaseVersion.dependency_spec`.
- Produces: stable expanded version order and snapshot role metadata.

- [x] Add failing tests for transitive ordering, deduplication, missing dependencies, cycles, and the 500-item expanded limit.
- [x] Run the focused tests and confirm the expected failures.
- [x] Implement dependency expansion in `_validate_snapshot()` and pass expanded case count to execution persistence.
- [x] Run the focused tests until green.

### Task 2: Runtime exports and dependency blocking

**Files:**
- Modify: `tests/api_testing/test_execution_service.py`
- Modify: `task_server/api_testing/services/execution_service.py`
- Modify: `task_server/api_testing/repositories/execution_repository.py`

**Interfaces:**
- Consumes: direct dependency specs and prior `CaseExecutionResult.extracted_variables`.
- Produces: per-child overrides and persisted `SKIPPED / dependency` results.

- [x] Add failing tests for export allowlists, required dependency blocking, optional dependency continuation, and `skipped` summaries.
- [x] Run focused tests and confirm they fail for missing runtime behavior.
- [x] Implement in-memory dependency outcomes, allowlisted override assembly, and skipped result creation.
- [x] Run the focused tests until green.

### Task 3: Dependency roles in reports

**Files:**
- Modify: `tests/api_testing/test_execution_service.py`
- Modify: `task_server/api_testing/services/execution_service.py`
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/components/CaseResultList.vue`

**Interfaces:**
- Consumes: `request_snapshot.case_versions[].role`.
- Produces: `execution_role` on every case result and a visible dependency marker.

- [x] Add failing backend and component tests for the dependency role.
- [x] Implement role projection and compact UI labeling.
- [x] Run focused backend and frontend tests until green.

### Task 4: Grouped dependency selector

**Files:**
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/components/CaseEditor.vue`
- Modify: `api-testing-ui/src/components/CaseEditor.spec.ts`
- Modify: `api-testing-ui/src/views/CasesView.vue`
- Modify: `api-testing-ui/src/views/WorkbenchView.vue`
- Modify: `api-testing-ui/src/styles/app.css`

**Interfaces:**
- Consumes: current source `CaseVersion[]` plus endpoint metadata.
- Produces: grouped dependency options and checkbox-based export selection in the existing draft.

- [x] Add failing editor tests for selecting a dependency and configuring exports.
- [x] Add a shared dependency-option contract and view-level computed options.
- [x] Replace manual version-ID/export text inputs with grouped select and checkboxes.
- [x] Run focused component/view tests until green.

### Task 5: Verification and handoff

**Files:**
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Consumes: completed backend and frontend behavior.
- Produces: verified repository state and deployment-ready `main`.

- [x] Run focused API testing suites with local PostgreSQL and Redis.
- [x] Run frontend tests, production build, backend/frontend static checks, syntax checks, and `git diff --check`.
- [x] Update `CODEX_STATE.md` with behavior, limits, and verification evidence.
- [x] Commit all scoped files and push `main` to `origin`.
