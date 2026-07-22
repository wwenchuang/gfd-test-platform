# MeterSphere Daily Execution Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the MeterSphere configuration debug page with an API-backed daily execution console that reads projects, environments, plans, runs, reports, and technical events from the backend and executes confirmed plans through one asynchronous orchestration endpoint.

**Architecture:** Extend the existing `metersphere_service.py` adapter instead of creating a second integration layer. The backend owns MeterSphere authentication, metadata caching, readiness, run persistence, orchestration threads, polling, report normalization, and recursive redaction; the browser consumes the documented `/api/api-testing/metersphere` contracts and only patches dynamic execution regions during polling.

**Tech Stack:** Python standard library HTTP/threading/file persistence, existing route decorators and storage helpers, vanilla JavaScript, existing CSS variables and static-check/visual-smoke test harnesses.

## Global Constraints

- Browser code must never call the MeterSphere host directly or receive real Token, Access Key, Secret Key, Authorization, Cookie, or signature values.
- `3D业务` is remote project data, never a production HTML/JavaScript constant.
- Metadata cache TTL is 30 seconds; stale cache is view-only and cannot enable execution.
- `POST /api/api-testing/metersphere/executions` returns HTTP 202 with a local `execution_id`; terminal state comes only from backend polling.
- Preserve existing push, run, and report-pull endpoints for compatibility.
- Do not modify Agent, YAML, Runner, Sonic, or mobile execution behavior.
- Preserve all unrelated user worktree changes and do not push.

---

### Task 1: MeterSphere metadata, configuration, and readiness

**Files:**
- Modify: `task_server/services/metersphere_service.py`
- Modify: `deploy/midscene.env.example`
- Test: `tests/backend_static_checks.py`

**Interfaces:**
- Produces: `list_metersphere_projects(force=False) -> dict`, `list_metersphere_environments(project_id, force=False) -> dict`, `metersphere_execution_context(force=False) -> dict`.
- Produces configuration fields: `auth_mode`, `project_list_path`, `environment_list_path`, `run_status_path`, plus configured booleans and capabilities.

- [x] **Step 1: Write failing backend checks**

Add fixture-driven checks that monkeypatch `_request_json` and assert project/environment normalization, 30-second cache metadata, stale cache behavior, recursive redaction, dynamic project names, and `connected_needs_setup` when execution paths are missing.

- [x] **Step 2: Run the checks and confirm RED**

Run: `python3 tests/backend_static_checks.py`

Expected: failure because metadata/context functions and readiness fields do not exist.

- [x] **Step 3: Implement metadata and context**

Add the exact typed interfaces `list_metersphere_projects(force: bool = False) -> Dict[str, Any]`, `list_metersphere_environments(project_id: str, force: bool = False) -> Dict[str, Any]`, and `metersphere_execution_context(force: bool = False) -> Dict[str, Any]`.

Normalize common MeterSphere response containers (`data`, `list`, `records`, `items`) to `{id, name, enabled}` and `{id, name, project_id, enabled}`. Persist only successful metadata caches with `fetched_at` and epoch timestamps; stale fallback must set `source=cache`, `stale=true`. Build `connection`, `selection`, `businesses`, `environments`, `capabilities`, `plans`, `active_runs`, `recent_runs`, `readiness`, and `empty_reason` on the server.

- [x] **Step 4: Run backend checks GREEN**

Run: `python3 tests/backend_static_checks.py`

Expected: all checks pass.

---

### Task 2: Asynchronous execution orchestration and status

**Files:**
- Modify: `task_server/services/metersphere_service.py`
- Test: `tests/backend_static_checks.py`

**Interfaces:**
- Produces: `start_metersphere_execution(plan_id, test_plan_id='') -> dict`.
- Produces: `get_metersphere_execution(execution_id, refresh=True) -> dict`.
- Persists: `metersphere-executions/<execution_id>.json` with overall state, four phase states, remote IDs, report state, and redacted events.

- [x] **Step 1: Write failing orchestration checks**

Cover empty/unconfirmed plans, duplicate active runs, queued 202 contract, push success/run failure, real remote status mapping, report failure after remote success, event redaction, and terminal `poll_after_ms=0`.

- [x] **Step 2: Run the checks and confirm RED**

Run: `python3 tests/backend_static_checks.py`

Expected: failure because execution orchestration APIs do not exist.

- [x] **Step 3: Implement persisted phases and worker**

Use these stable phase IDs and states:

```python
EXECUTION_PHASES = (
    ("push_cases", "推送用例"),
    ("trigger_plan", "触发计划"),
    ("metersphere_run", "MeterSphere 执行"),
    ("sync_report", "同步报告"),
)
PHASE_STATES = {"waiting", "running", "succeeded", "failed", "skipped"}
```

Create the queued record before starting a daemon worker. The worker calls existing push/run/report functions, stores push/run/report IDs, appends sanitized events, and never infers a remote success from elapsed time. `get_metersphere_execution()` calls the configured status path for nonterminal remote runs and returns backend-controlled `poll_after_ms`.

- [x] **Step 4: Run backend checks GREEN**

Run: `python3 tests/backend_static_checks.py`

Expected: all checks pass.

---

### Task 3: HTTP contracts

**Files:**
- Modify: `task_server/router.py`
- Test: `tests/backend_static_checks.py`

**Interfaces:**
- `GET /api/api-testing/metersphere/execution-context?force=1`
- `POST /api/api-testing/metersphere/executions`
- `GET /api/api-testing/metersphere/executions/{execution_id}`

- [x] **Step 1: Add failing route registration checks**

Assert exact context/start routes and the execution-status regex route are registered, with start returning status 202.

- [x] **Step 2: Run the checks and confirm RED**

Run: `python3 tests/backend_static_checks.py`

- [x] **Step 3: Add thin route handlers**

Handlers only parse input, call service functions, and map invalid input to 400, duplicate active execution to 409, missing execution to 404, and accepted execution to 202.

- [x] **Step 4: Run backend checks GREEN**

Run: `python3 tests/backend_static_checks.py`

Expected: all checks pass.

---

### Task 4: Daily execution frontend

**Files:**
- Modify: `js/state.js`
- Modify: `js/api-testing.js`
- Modify: `task-manager.html`
- Test: `tests/frontend_static_checks.py`

**Interfaces:**
- Consumes only the three new backend contracts from Task 3.
- Produces page functions `showApiExecutionPage()`, `refreshApiExecutionContext(force)`, `startApiMeterSphereExecution(planId)`, `pollApiMeterSphereExecution(executionId)`, and settings drawer handlers.

- [x] **Step 1: Add failing frontend static checks**

Assert that production JS contains no `3D业务`, no fabricated `local/config` log, no 11-field main-page form, and does contain execution-context fetch, async execution start, backend `poll_after_ms`, stable `run_id + event_id` expansion keys, scroll restoration, stale-cache execution guard, and settings drawer controls.

- [x] **Step 2: Run the checks and confirm RED**

Run: `python3 tests/frontend_static_checks.py`

- [x] **Step 3: Implement the execution console**

Render a compact connection header, business/environment selectors sourced from context, a single-column confirmed-plan list, optional active-run region, four fixed phases, real technical events, and a controlled settings drawer. Poll only the active-run/context dynamic roots. Capture expanded keys and each `.api-log-content` scroll position before patching and restore them after patching.

- [x] **Step 4: Run frontend checks GREEN**

Run: `python3 tests/frontend_static_checks.py`

Expected: all checks pass.

---

### Task 5: Responsive styling and full verification

**Files:**
- Modify: `css/round5.css`
- Modify: `CODEX_STATE.md`
- Test: `tests/visual_smoke_check.js`

**Interfaces:**
- Uses existing color variables and button classes; cards remain at 8px radius or less.

- [x] **Step 1: Add execution-console CSS**

Create stable dimensions for the header, stage grid, plan rows, settings drawer, and independently scrolling log body. At `max-width: 700px`, stack metadata controls and keep the primary execution button full-width without overlapping menus.

- [x] **Step 2: Run targeted and full verification**

Run:

```bash
python3 -m py_compile task_server/services/metersphere_service.py task_server/router.py tests/backend_static_checks.py tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
git diff --check
npm test
```

Expected: all commands exit 0; visual smoke produces desktop/mobile screenshots without overflow or overlap.

- [x] **Step 3: Update state and commit only scoped files**

Document the API contracts, readiness behavior, stale-cache restriction, real log behavior, verification evidence, and remaining live MeterSphere path configuration in `CODEX_STATE.md`. Stage only this plan, MeterSphere/backend/router/frontend/CSS/env/tests/state files, commit, and do not push.
