# API Testing Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a simplified API testing workbench where Apifox is only an asset snapshot source and the platform owns case generation, debugging, baseline storage, execution, and reports.

**Architecture:** Add a backend facade over existing API source, asset, plan, execution, and report services. Replace the current multi-page API UI with a single workbench-oriented view that hides internal IDs by default while preserving existing lower-level routes for compatibility.

**Tech Stack:** Python standard-library HTTP router/services, existing JSON file storage under `LEARNING_DIR/api-testing`, existing `js/api-testing.js`, existing `css/round5.css`, existing unittest/static check suite.

## Global Constraints

- Do not reintroduce MeterSphere.
- Do not destructively migrate or delete historical API records.
- Keep existing lower-level API routes compatible.
- Hide `source_id`, `snapshot_id`, `revision_id`, `plan_id`, and binding fingerprints from normal UI.
- Allow draft API cases to be debugged before baseline adoption.
- AI can propose cases and data, but platform readiness validation decides executable state.
- Preserve user dirty files outside the API workbench scope.

---

### Task 1: Backend Workbench Facade

**Files:**
- Create: `task_server/services/api_workbench_service.py`
- Modify: `task_server/router.py`
- Test: `tests/api_workbench_checks.py`

**Interfaces:**
- Consumes: `api_source_service.list_api_sources()`, `api_asset_service.get_api_asset()`, `api_asset_service.list_api_endpoints()`, `api_test_plan_service.list_api_test_plans()`, `api_execution_service.api_execution_context()`, `api_report_service.list_api_reports()`
- Produces:
  - `api_workbench_service.api_testing_workbench(source_id: str = "") -> dict`
  - `GET /api/api-testing/workbench`

- [ ] **Step 1: Write the failing facade test**

Create `tests/api_workbench_checks.py` with a temporary API testing directory. Save one Apifox source, import one OpenAPI document, create one plan, and assert the facade returns `snapshot`, `scope`, `cases`, `execution`, and no secrets.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.api_workbench_checks`

Expected: fail with missing `api_workbench_service` or missing route.

- [ ] **Step 3: Implement `api_workbench_service.api_testing_workbench()`**

Create a small aggregate payload:

```python
{
  "ok": True,
  "source": {...},
  "snapshot": {"id": "...", "title": "...", "endpoint_count": 0, "updated_at": "..."},
  "scope": {"endpoints": [...], "modules": [...], "selected_endpoint_ids": []},
  "cases": {"drafts": [...], "baselines": [...]},
  "execution": {"readiness": {...}, "active_runs": [...], "recent_runs": [...]},
  "reports": [...]
}
```

- [ ] **Step 4: Add router endpoint**

Add `@route_get("/api/api-testing/workbench")` guarded by user auth.

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m unittest tests.api_workbench_checks
python3 -m py_compile task_server/services/api_workbench_service.py task_server/router.py tests/api_workbench_checks.py
```

Expected: pass.

### Task 2: Snapshot Update Facade

**Files:**
- Modify: `task_server/services/api_workbench_service.py`
- Modify: `task_server/router.py`
- Test: `tests/api_workbench_checks.py`

**Interfaces:**
- Consumes: existing `api_sync_service.start_api_source_sync()`
- Produces:
  - `api_workbench_service.update_apifox_snapshot(source_id: str, spawn: bool = True) -> dict`
  - `POST /api/api-testing/snapshots/update`

- [ ] **Step 1: Add failing test**

Assert `POST /api/api-testing/snapshots/update` starts an Apifox sync for the selected source and returns user-facing wording `snapshot_update`.

- [ ] **Step 2: Implement facade**

Use existing sync service but return simplified payload:

```python
{"ok": True, "snapshot_update": {"status": "...", "source_id": "...", "message": "..."}}
```

- [ ] **Step 3: Run tests**

Run: `python3 -m unittest tests.api_workbench_checks`

Expected: pass.

### Task 3: Draft Debug Facade

**Files:**
- Modify: `task_server/services/api_workbench_service.py`
- Modify: `task_server/router.py`
- Test: `tests/api_workbench_checks.py`

**Interfaces:**
- Consumes: `api_execution_service.debug_api_case(source_id, case)`
- Produces:
  - `api_workbench_service.debug_api_case_from_workbench(source_id: str, case: dict) -> dict`
  - `POST /api/api-testing/cases/debug`

- [ ] **Step 1: Add failing test**

Create a draft case dict and assert it can be passed to the workbench debug route without requiring plan confirmation.

- [ ] **Step 2: Implement route and service wrapper**

Delegate to native execution debug behavior and return `debug_result`.

- [ ] **Step 3: Run tests**

Run:

```bash
python3 -m unittest tests.api_workbench_checks tests.api_native_execution_checks
```

Expected: pass.

### Task 4: Frontend Single Workbench View

**Files:**
- Modify: `js/api-testing.js`
- Modify: `css/round5.css`
- Modify: `task-manager.html`
- Test: `tests/frontend_static_checks.py`

**Interfaces:**
- Consumes: `GET /api/api-testing/workbench`
- Produces:
  - `loadApiWorkbench()`
  - `renderApiWorkbench(data)`
  - A single workbench layout with snapshot strip, endpoint scope, AI cases, execution/report panel.

- [ ] **Step 1: Add frontend static expectations**

Update `tests/frontend_static_checks.py` to require:

- `/api-testing/workbench`
- `renderApiWorkbench`
- user-facing strings `更新 Apifox 快照`, `AI 用例`, `单条调试`, `保存为基线`
- absence of visible sync-first copy in the main workbench.

- [ ] **Step 2: Implement loader and renderer**

Add workbench loading functions and render a simplified layout. Reuse existing endpoint table and plan/case rendering helpers where possible.

- [ ] **Step 3: Wire navigation**

Make API entry pages call the workbench renderer by default. Keep old functions available for compatibility, but make the main visible flow the workbench.

- [ ] **Step 4: Add CSS**

Add compact workbench classes: status strip, two-column scope, case cards, execution panel, report summary.

- [ ] **Step 5: Run checks**

Run:

```bash
node --check js/api-testing.js
python3 tests/frontend_static_checks.py
```

Expected: pass.

### Task 5: Final Verification and State

**Files:**
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Consumes: all tasks.
- Produces: documented verification and known limitations.

- [ ] **Step 1: Run focused backend and frontend checks**

Run:

```bash
python3 -m unittest tests.api_workbench_checks tests.api_asset_sync_checks tests.api_native_execution_checks tests.apifox_discovery_checks
python3 tests/frontend_static_checks.py
python3 -m py_compile task_server/services/api_workbench_service.py task_server/router.py tests/api_workbench_checks.py
node --check js/api-testing.js
git diff --check
```

- [ ] **Step 2: Update `CODEX_STATE.md`**

Record the simplified workbench flow, routes added, tests run, and any unrelated pre-existing full backend check blockers.

- [ ] **Step 3: Commit only scoped files**

Stage only:

```bash
CODEX_STATE.md
docs/superpowers/plans/2026-07-30-api-testing-workbench.md
task_server/services/api_workbench_service.py
task_server/router.py
js/api-testing.js
css/round5.css
task-manager.html
tests/api_workbench_checks.py
tests/frontend_static_checks.py
```

Commit message:

```bash
git commit -m "Simplify API testing workbench"
```
