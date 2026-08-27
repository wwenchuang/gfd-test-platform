# API Case Lifecycle Continuity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make API source sync, case creation, generation, debugging, baseline admission, and regression a continuous and truthful workflow.

**Architecture:** The active workspace revision wins unless a task deep link is explicit. Cases are projected across source revisions by endpoint `stable_key` and lazily adapted to the current endpoint on first mutation, while immutable historical versions and baselines remain fixed. Case-list lifecycle metadata and resumable AI jobs expose downstream state without duplicating domain records.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, Pydantic, Vue 3, Pinia, TypeScript, Vitest.

## Global Constraints

- Preserve historical case versions, baselines, execution evidence, and explicit historical task deep links.
- A non-success business response must never be reported as passed without an exact assertion that accepts the actual business code.
- Do not render all endpoint leaves for a 1000+ endpoint source while groups are collapsed.
- User-facing validation and execution summaries are Chinese; protocol identifiers remain unchanged.
- Preserve existing user changes and keep `output/` out of commits.

---

### Task 1: Truthful business assertion gate

**Files:**
- Modify: `task_server/api_testing/assertions.py`
- Modify: `task_server/api_testing/validation.py`
- Modify: `task_server/api_testing/executor.py`
- Modify: `api-testing-ui/src/components/DebugDrawer.vue`
- Modify: `api-testing-ui/src/utils/executionPresentation.ts`
- Test: `tests/api_testing/test_validation.py`
- Test: `tests/api_testing/test_executor.py`
- Test: `api-testing-ui/src/components/DebugDrawer.spec.ts`

**Interfaces:**
- Produces: `business_response_guard(response, assertions, assertion_results)` returning a deterministic pass/fail explanation.
- Consumes: existing assertion dictionaries and executor response payloads.

- [x] Add failing backend tests for broad negative business-code assertions and exact negative assertions.
- [x] Run focused tests and verify the broad assertion test fails for the existing false-positive behavior.
- [x] Reject broad business-code assertions in validation and add the runtime business-response guard.
- [x] Add failing component tests for Chinese HTTP/business/assertion summaries and disabled baseline action.
- [x] Implement the debug summary and run all focused tests to green.

### Task 2: Active source revision wins over implicit task restore

**Files:**
- Modify: `api-testing-ui/src/views/WorkbenchView.vue`
- Modify: `api-testing-ui/src/views/CasesView.vue`
- Modify: `api-testing-ui/src/stores/tasks.ts`
- Test: `api-testing-ui/src/views/WorkbenchView.spec.ts`
- Test: `api-testing-ui/src/views/CasesView.spec.ts`

**Interfaces:**
- Produces: implicit restore that never overwrites the saved active source revision; explicit `taskId` restore keeps historical behavior.

- [x] Add failing view tests for current workspace plus an older latest task.
- [x] Verify the current implementation restores the old source revision.
- [x] Separate implicit task metadata restoration from explicit historical task context restoration.
- [x] Show a version mismatch notice with an action to rebuild the task scope on the current source.
- [x] Run focused view tests to green.

### Task 3: Cross-revision logical case projection and lazy adaptation

**Files:**
- Modify: `task_server/api_testing/repositories/case_repository.py`
- Modify: `task_server/api_testing/services/case_service.py`
- Modify: `task_server/api_testing/contracts/case.py`
- Modify: `task_server/api_testing/http.py`
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/stores/cases.ts`
- Test: `tests/api_testing/test_case_service.py`
- Test: `api-testing-ui/src/stores/cases.spec.ts`

**Interfaces:**
- Produces: case list items with `source_state`, `current_endpoint_id`, and lifecycle metadata; save/debug adaptation returns a current-revision version.
- Consumes: endpoint `stable_key`, logical `ApiCase`, immutable `ApiCaseVersion`.

- [x] Add failing service tests showing a historical same-`stable_key` case appears as `needs_adaptation` on the new source.
- [x] Add failing tests that first save/debug creates a current endpoint version without changing the historical version or baseline.
- [x] Implement repository projection and transactional lazy adaptation.
- [x] Update contracts and Pinia state to consume the projection.
- [x] Run backend and store tests to green.

### Task 4: Endpoint-first case management and lifecycle filters

**Files:**
- Modify: `api-testing-ui/src/views/CasesView.vue`
- Modify: `api-testing-ui/src/components/CaseListPanel.vue`
- Create: `api-testing-ui/src/components/CaseEndpointPicker.vue`
- Modify: `api-testing-ui/src/utils/caseListPresentation.ts`
- Test: `api-testing-ui/src/views/CasesView.spec.ts`
- Test: `api-testing-ui/src/components/CaseListPanel.spec.ts`
- Test: `api-testing-ui/src/components/CaseEndpointPicker.spec.ts`

**Interfaces:**
- Produces: endpoint picker events `create-manual`, `generate-basic`, `generate-ai`; work views `regular`, `debugged`, `baseline`.
- Consumes: endpoint rows, projected case versions, lifecycle fields, existing case-store generation methods.

- [x] Add failing tests for selecting an endpoint with no cases and launching each creation path.
- [x] Implement a searchable, bounded endpoint picker for large sources.
- [x] Add lifecycle badges, filters, and direct debug/baseline history actions without duplicating cases.
- [x] Replace the dead empty-state instruction with direct creation actions.
- [x] Run component and view tests to green.

### Task 5: Resumable generation and result destination

**Files:**
- Modify: `api-testing-ui/src/stores/cases.ts`
- Modify: `api-testing-ui/src/components/AiAssistant.vue`
- Modify: `api-testing-ui/src/views/WorkbenchView.vue`
- Modify: `api-testing-ui/src/views/CasesView.vue`
- Test: `api-testing-ui/src/stores/cases.spec.ts`
- Test: `api-testing-ui/src/components/AiAssistant.spec.ts`

**Interfaces:**
- Produces: `restoreLatestAiJob(projectId, sourceRevisionId)` and result-location state containing generated version IDs.

- [x] Add failing tests that terminal AI jobs survive refresh and expose generated result IDs.
- [x] Restore the latest terminal or running job for the current source and load its generated results.
- [x] Add Chinese progress, elapsed, completion, failure, retry, and “查看生成结果” actions.
- [x] Open the first generated result directly from the restored status.
- [x] Run focused tests to green.

### Task 6: Chinese validation copy and large-list behavior

**Files:**
- Modify: `task_server/api_testing/validation.py`
- Modify: `api-testing-ui/src/components/AssertionListEditor.vue`
- Modify: `api-testing-ui/src/utils/caseDraftValidation.ts`
- Modify: `api-testing-ui/src/components/EndpointTree.vue`
- Test: `tests/api_testing/test_validation.py`
- Test: `api-testing-ui/src/components/EndpointTree.spec.ts`

**Interfaces:**
- Produces: field-aware Chinese validation messages and collapsed endpoint groups that do not render child endpoints.

- [x] Add failing tests for Chinese optional-parameter and assertion-quality messages.
- [x] Replace user-facing validator and executor messages with field-aware Chinese text.
- [x] Add a rendering-count test for a collapsed 1000-endpoint tree.
- [x] Verify search and expansion render only visible branches while preserving selection.
- [x] Run focused tests to green.

### Task 7: Scheduled baseline consistency and integrated verification

**Files:**
- Modify: `task_server/api_testing/services/scheduled_job_service.py`
- Modify: `api-testing-ui/src/views/ScheduledJobsView.vue`
- Test: `tests/api_testing/test_scheduled_job_service.py`
- Test: `api-testing-ui/src/views/ScheduledJobsView.spec.ts`
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Produces: schedule creation and editing limited to active baselines, matching runtime execution behavior.

- [x] Preserve and re-run the existing focused schedule tests.
- [x] Run backend compile/static checks and frontend static checks.
- [x] Run the complete `bash tests/run_api_testing_gate.sh` gate and `git diff --check`.
- [x] Perform a post-gate semantic review of source/case/baseline boundaries.
- [ ] Update `CODEX_STATE.md`, commit only source/docs/tests, and push `main`.
- [ ] Deploy through `deploy/update-main-server.sh`, verify server SHA and assets, then run the Chrome full-flow acceptance audit.
