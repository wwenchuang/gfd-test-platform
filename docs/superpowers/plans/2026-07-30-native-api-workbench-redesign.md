# Native API Workbench Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the first phase of the native API workbench clearer and more reliable for Apifox-sourced API testing.

**Architecture:** Keep existing services and routes. Extend the module summary contract with server-owned counts and endpoint id samples; extend plan generation records with timeout recovery; update frontend cards to use the server contract instead of guessing from truncated endpoint samples.

**Tech Stack:** Python stdlib services/tests, vanilla JavaScript frontend, existing JSON-file persistence.

## Global Constraints

- Do not reintroduce MeterSphere as the main API execution path.
- Do not modify Agent, Runner, Sonic, historical YAML, or Apifox parsing beyond fields needed by the workbench.
- Preserve user dirty files and only stage files touched for this task.
- Keep AI generation batched and retryable; do not lower execution gates.

---

### Task 1: Server Module Counts

**Files:**
- Modify: `task_server/services/api_module_service.py`
- Modify: `tests/api_workbench_checks.py`

**Interfaces:**
- Produces: `module_summary(endpoints)` nodes with `endpoint_count` and `endpoint_ids`.
- Consumes: existing endpoint records with `endpoint_id`, `module_path`, and `module`.

- [x] Add a failing test that a module summary node for a parent module has total endpoint count and up to 60 endpoint ids.
- [x] Run `python3 tests/api_workbench_checks.py` and verify the new test fails.
- [x] Update `module_summary()` to accumulate endpoint ids on each ancestor node.
- [x] Run `python3 tests/api_workbench_checks.py` and verify it passes.

### Task 2: Workbench Scope UX

**Files:**
- Modify: `js/api-testing.js`
- Modify: `tests/frontend_static_checks.py`

**Interfaces:**
- Consumes: `workbench.scope.modules.roots[*].endpoint_count` and `endpoint_ids`.
- Produces: `apiWorkbenchEndpointCountForModule(module)` and `apiWorkbenchEndpointIdsForModule(modulePath)`.

- [x] Add a failing frontend static check that module cards do not call `apiWorkbenchEndpointCountForPath(module.path)`.
- [x] Run `python3 tests/frontend_static_checks.py` and verify it fails.
- [x] Update workbench module rendering and generation to use server counts and ids.
- [x] Add large-module confirmation copy with batch count and suggested child modules.
- [x] Run `python3 tests/frontend_static_checks.py` and `node --check js/api-testing.js`.

### Task 3: AI Batch Timeout Recovery

**Files:**
- Modify: `task_server/services/api_plan_generation_service.py`
- Modify: `tests/api_workbench_checks.py`

**Interfaces:**
- Produces: `get_api_plan_generation(generation_id)` may mark stale running batches as `failed` with `recoverable=true`.
- Consumes: existing `retry_api_plan_generation(generation_id)`.

- [x] Add a failing test for stale running batch recovery that preserves succeeded plan ids.
- [x] Run the focused test through `python3 tests/api_workbench_checks.py`.
- [x] Add `RUNNING_BATCH_TIMEOUT_SECONDS`, stale timestamp parsing, timeout marking, and stale-write guards.
- [x] Run `python3 tests/api_workbench_checks.py`.

### Task 4: Verification And State

**Files:**
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Consumes: all prior task outputs.
- Produces: updated handoff state and one local commit.

- [x] Run Python compile checks for modified services and tests.
- [x] Run API workbench, native execution, frontend static, and backend static checks.
- [x] Run `git diff --check` on touched files.
- [x] Update `CODEX_STATE.md`.
- [ ] Stage only task files and commit.
