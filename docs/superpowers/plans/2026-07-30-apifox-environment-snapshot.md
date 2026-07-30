# Apifox Environment Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sync and display Apifox environment configuration snapshots for the selected API source, then explicitly write safe values into the bound MeterSphere environment without persisting sensitive values.

**Architecture:** Extend the existing Apifox CLI discovery boundary to normalize environment metadata into a safe `environment_snapshot`. Store the selected snapshot on the API source, render it in the API asset workspace, and provide a manual sync action that writes only non-sensitive base URLs and variables to MeterSphere environment variables with the `MTP_APIFOX_*` prefix.

**Tech Stack:** Python service modules and unittest checks; vanilla JavaScript and CSS for the existing static frontend.

## Global Constraints

- Preserve existing user dirty files and avoid unrelated Agent, Runner, YAML, and Sonic changes.
- Do not store Apifox access tokens, business tokens, passwords, or raw secret variable values in public source metadata.
- Apifox local variable values are not assumed available; only remote/config values returned by the server-side CLI/API boundary can be synchronized.
- MeterSphere writes must be explicit, source-bound, and limited to platform-owned `MTP_APIFOX_*` variables.

---

### Task 1: Normalize Apifox Environment Snapshot

**Files:**
- Modify: `task_server/services/apifox_discovery_service.py`
- Test: `tests/apifox_discovery_checks.py`

**Interfaces:**
- Produces: `environment_snapshot` on each non-default environment option with `base_urls`, `variables`, `variable_count`, and `sensitive_variable_count`.
- Consumes: existing `discover_project_context(access_token, project_id, ...)`.

- [x] **Step 1: Write the failing tests**

Add coverage where fake `environment list` includes `baseUrls` and variable rows with sensitive and non-sensitive names. Assert normalized snapshot includes base URLs, masks values, and never leaks token-like values.

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.apifox_discovery_checks.ApifoxDiscoveryServiceChecks.test_project_context_includes_safe_environment_snapshot`
Expected: FAIL because `environment_snapshot` is missing.

- [x] **Step 3: Implement minimal normalization**

Add small helpers in `apifox_discovery_service.py` for base URL maps and variable lists. Keep output bounded and token-safe.

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.apifox_discovery_checks.ApifoxDiscoveryServiceChecks.test_project_context_includes_safe_environment_snapshot`
Expected: PASS.

### Task 2: Persist Snapshot on API Source

**Files:**
- Modify: `task_server/services/api_source_service.py`
- Test: `tests/api_asset_sync_checks.py`

**Interfaces:**
- Consumes: `environment_snapshot` from frontend payload or discovery metadata.
- Produces: public `source.environment_snapshot` sanitized for UI.

- [x] **Step 1: Write the failing tests**

Add a source-save test that passes an environment snapshot with secret-looking values and asserts the public source contains counts/variable names but not secret values.

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.api_asset_sync_checks.ApiSourceServiceTests.test_environment_snapshot_is_public_and_redacted`
Expected: FAIL because source snapshots are not persisted.

- [x] **Step 3: Implement minimal persistence**

Add `normalize_environment_snapshot()` and wire it into `_public_source()` and `_save_api_source_locked()`.

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.api_asset_sync_checks.ApiSourceServiceTests.test_environment_snapshot_is_public_and_redacted`
Expected: PASS.

### Task 3: Sync Snapshot to MeterSphere Environment

**Files:**
- Modify: `task_server/services/metersphere_service.py`
- Modify: `task_server/services/metersphere_v365_adapter.py`
- Modify: `task_server/router.py`
- Test: `tests/api_project_workspace_checks.py`
- Test: `tests/metersphere_v365_adapter_checks.py`

**Interfaces:**
- Consumes: stored `source.environment_snapshot` and the source-bound MeterSphere project/environment.
- Produces: `POST /api/api-testing/sources/{source_id}/environment-sync`, writing `MTP_APIFOX_BASE_URL_*` and `MTP_APIFOX_VAR_*` variables.

- [x] **Step 1: Write failing tests**

Add service coverage that a snapshot containing one base URL, one normal variable, and one token-like variable writes only the safe values to MeterSphere and never stores the token-like value. Add adapter coverage that `MTP_APIFOX_*` is allowed while still rejecting non-platform variables.

- [x] **Step 2: Implement sync service and route**

Add `sync_apifox_environment_to_metersphere()` and route it behind the existing API source authentication boundary. Require a bound MeterSphere project and environment before writing.

- [x] **Step 3: Verify**

Run focused service and adapter tests.

### Task 4: Render Snapshot in API Asset UI

**Files:**
- Modify: `js/api-testing.js`
- Modify: `css/round5.css`
- Modify: `task-manager.html`
- Test: `tests/frontend_static_checks.py`

**Interfaces:**
- Consumes: `source.environment_snapshot` and selected discovery environment `environment_snapshot`.
- Produces: visible Apifox environment snapshot cards, explicit MeterSphere sync action, and cache version `20260730-apifox-env-snapshot`.

- [x] **Step 1: Write static checks**

Require `renderApiSourceEnvironmentSnapshot`, `syncApiSourceEnvironmentToMeterSphere`, `environment_snapshot`, `Apifox 环境配置`, `同步到 MeterSphere 环境`, and `敏感值未同步` in frontend checks.

- [x] **Step 2: Run check to verify it fails**

Run: `python3 tests/frontend_static_checks.py`
Expected: FAIL because UI functions are missing.

- [x] **Step 3: Implement UI**

Render snapshot cards in the asset summary/settings area. Send selected environment snapshot in `saveApiSourceConfig()`.

- [x] **Step 4: Run checks**

Run: `python3 tests/frontend_static_checks.py && node --check js/api-testing.js`
Expected: PASS.

### Task 5: Final Verification and Commit

**Files:**
- Modify: `CODEX_STATE.md`

- [x] **Step 1: Update state**

Record the Apifox environment snapshot behavior and verification commands.

- [x] **Step 2: Run focused verification**

Run:

```bash
python3 tests/apifox_discovery_checks.py
python3 tests/api_asset_sync_checks.py
python3 tests/api_project_workspace_checks.py
python3 tests/metersphere_v365_adapter_checks.py
python3 tests/frontend_static_checks.py
node --check js/api-testing.js
python3 -m py_compile task_server/services/apifox_discovery_service.py task_server/services/api_source_service.py task_server/services/metersphere_service.py task_server/services/metersphere_v365_adapter.py task_server/router.py tests/apifox_discovery_checks.py tests/api_asset_sync_checks.py tests/api_project_workspace_checks.py tests/metersphere_v365_adapter_checks.py tests/frontend_static_checks.py
git diff --check -- task_server/services/apifox_discovery_service.py task_server/services/api_source_service.py task_server/services/metersphere_service.py task_server/services/metersphere_v365_adapter.py task_server/router.py js/api-testing.js css/round5.css task-manager.html tests/apifox_discovery_checks.py tests/api_asset_sync_checks.py tests/api_project_workspace_checks.py tests/metersphere_v365_adapter_checks.py tests/frontend_static_checks.py CODEX_STATE.md docs/superpowers/plans/2026-07-30-apifox-environment-snapshot.md
```

- [x] **Step 3: Commit only related files**

Run:

```bash
git add task_server/services/apifox_discovery_service.py task_server/services/api_source_service.py task_server/services/metersphere_service.py task_server/services/metersphere_v365_adapter.py task_server/router.py js/api-testing.js css/round5.css task-manager.html tests/apifox_discovery_checks.py tests/api_asset_sync_checks.py tests/api_project_workspace_checks.py tests/metersphere_v365_adapter_checks.py tests/frontend_static_checks.py CODEX_STATE.md docs/superpowers/plans/2026-07-30-apifox-environment-snapshot.md
git commit -m "Sync Apifox environment snapshots"
```
