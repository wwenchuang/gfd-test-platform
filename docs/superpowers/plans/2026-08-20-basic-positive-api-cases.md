# Basic Positive API Cases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build deterministic generation of basic positive API case drafts from selected imported endpoints.

**Architecture:** Add a backend service that converts endpoint contracts plus environment revision metadata into validated case draft payloads and persists them through `CaseService`. Expose it through a small synchronous HTTP endpoint and connect the Workbench assistant to call it.

**Tech Stack:** Python services and pytest for backend; Vue 3, Pinia, Vitest for frontend.

## Global Constraints

- Do not call AI Gateway for this basic generation path.
- Do not persist literal token/secret values into case requests.
- Use selected environment default headers and variables for runtime-managed authentication.
- Keep generated cases editable drafts.

---

### Task 1: Backend Generator

**Files:**
- Create: `task_server/api_testing/services/basic_case_service.py`
- Test: `tests/api_testing/test_basic_case_service.py`

**Interfaces:**
- Produces: `BasicCaseService.generate(endpoint_ids, environment_revision_id, actor_id) -> tuple[CaseVersionView, ...]`

- [x] Write failing tests for generated GET and POST positive case drafts.
- [x] Run backend service tests and confirm they fail because the service is missing.
- [x] Implement deterministic request/response assertion generation and persistence.
- [x] Run backend service tests and confirm they pass.

### Task 2: HTTP API

**Files:**
- Modify: `task_server/api_testing/http.py`
- Test: `tests/api_testing/test_basic_case_service.py` or `tests/api_testing/test_http_contract.py`

**Interfaces:**
- Consumes: `BasicCaseService.generate(...)`
- Produces: `POST /api/api-testing/v1/cases/basic-positive`

- [x] Write failing route test for scoped generation.
- [x] Add route handling and domain error mapping if needed.
- [x] Run route/service tests and confirm pass.

### Task 3: Frontend Store And Workbench

**Files:**
- Modify: `api-testing-ui/src/stores/cases.ts`
- Modify: `api-testing-ui/src/components/AiAssistant.vue`
- Modify: `api-testing-ui/src/views/WorkbenchView.vue`
- Test: `api-testing-ui/src/stores/cases.spec.ts`
- Test: `api-testing-ui/src/views/WorkbenchView.spec.ts`

**Interfaces:**
- Consumes: `POST /cases/basic-positive`
- Produces: `cases.generateBasicPositive(...)`

- [x] Write failing store/view tests for the new action.
- [x] Add store action and assistant button event.
- [x] Wire Workbench to save task scope, call generation, refresh task state, and activate first generated draft.
- [x] Run targeted frontend tests and confirm pass.

### Task 4: Verification And State

**Files:**
- Modify: `CODEX_STATE.md`

- [x] Run backend py_compile and static checks.
- [x] Run targeted backend and frontend tests.
- [x] Run frontend build if Vue files changed.
- [x] Update `CODEX_STATE.md`.
- [ ] Commit all related files.
