# API Body Example AI Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve Apifox request Body examples through the saved API asset, use their fields and values as the primary AI case-design contract, and stop generating request-header-specific test cases while retaining runtime header injection.

**Architecture:** Keep the complete normalized OpenAPI operation as the read-only source asset. Build a separate sanitized business-test contract for AI that excludes header parameters and credential values but retains safe Body schemas/examples and response contracts. Seed manual drafts from the same OpenAPI Body example; execution continues to merge environment default headers and case-level non-empty overrides exactly as before.

**Tech Stack:** Python 3.12, SQLAlchemy, pytest, Vue 3, Pinia, TypeScript, Vitest.

## Global Constraints

- Do not change the Apifox source asset format or remove full OpenAPI operation metadata.
- Never send token, cookie, authorization values, secret values, fingerprints, or ciphertext to AI.
- Do not generate independent positive or negative request-header test cases.
- Keep runtime `Biz`, `Authorization`, `Content-Type`, and other common headers supplied by the selected environment and executor.
- Body examples may be sent to AI only after recursive credential redaction.
- Do not refactor `task_server/router.py`, change execution modes, or modify historical YAML.

---

### Task 1: Lock the persisted Body example contract

**Files:**
- Modify: `tests/api_testing/test_source_service.py`

**Interfaces:**
- Consumes: `normalize_openapi_document(document, source_id)` and `SourceService.activate_preview(...)`.
- Produces: Regression evidence that `requestBody.content.*.example` and `examples.*.value` survive normalization and persistence.

- [x] **Step 1: Add assertions for the single example and named example forms.**
- [x] **Step 2: Run the focused source test and confirm current persistence behavior.**
- [x] **Step 3: Change source normalization only if the focused test exposes loss.** No source change was needed; persistence already retained both standard example forms.
- [x] **Step 4: Run the focused source test and the complete backend gate.**

### Task 2: Build a safe Body-first AI contract

**Files:**
- Modify: `tests/api_testing/test_ai_service.py`
- Modify: `task_server/api_testing/services/ai_service.py`
- Modify: `ai_skills/api_case_generation.v1.md`

**Interfaces:**
- Consumes: full `ApiSourceEndpoint.operation` and environment variable/service metadata.
- Produces: `_business_operation_contract(operation)` containing non-header parameters, Body schema/examples, response contracts, and `runtime_headers_managed_by_environment: true`.

- [x] **Step 1: Add a failing prompt test proving Body examples are present, header parameters are absent, and credentials are redacted.**
- [x] **Step 2: Run the focused AI prompt test and confirm it fails because examples are currently omitted.**
- [x] **Step 3: Implement a minimum business-contract projection and recursive sanitizer that keeps safe examples.**
- [x] **Step 4: Update the generation skill to prioritize Body examples/constraints and forbid header-focused cases or case-level header construction.**
- [x] **Step 5: Run the AI service tests and confirm prompt security tests remain green.**

### Task 3: Seed manual drafts from OpenAPI Body examples

**Files:**
- Modify: `api-testing-ui/src/stores/cases.spec.ts`
- Modify: `api-testing-ui/src/stores/cases.ts`

**Interfaces:**
- Consumes: `ApiEndpoint.operation.requestBody.content` and `operation.resolved_dependencies`.
- Produces: `blankDraft(endpoint)` with a cloned JSON Body example, supporting direct `example`, named `examples.*.value`, schema examples/defaults, and resolved schema properties.

- [x] **Step 1: Add failing store tests for direct, named, and referenced Body examples.**
- [x] **Step 2: Run `vitest` for `cases.spec.ts` and confirm the draft Body is currently `null`.**
- [x] **Step 3: Implement deterministic media selection, request-body/schema reference resolution, and safe JSON cloning for Body initialization.**
- [x] **Step 4: Run the focused store tests and production TypeScript build.**

### Task 4: Verify the complete API workflow contract

**Files:**
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Consumes: backend and frontend changes from Tasks 1-3.
- Produces: checked-in verification evidence and a deployable `main` commit.

- [x] **Step 1: Run `bash tests/run_api_testing_gate.sh`.**
- [x] **Step 2: Run `PATH="$PWD/.venv/bin:$PATH" npm run test:static`.**
- [x] **Step 3: Run `git diff --check` and inspect the final diff for credential leakage.**
- [x] **Step 4: Update `CODEX_STATE.md` with root cause, behavior, and exact verification results.**
- [x] **Step 5: Commit only the scoped files and push `main` to `origin`.**
