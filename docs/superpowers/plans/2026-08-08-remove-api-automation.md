# Remove Existing API Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the existing API automation runtime and product surface while preserving historical databases, reports, screenshots, specifications, and all non-API platform workflows.

**Architecture:** Delete the API-only vertical slice from the outside inward: first the browser entry points, then the server routes and services, then AI/deployment dependencies, and finally obsolete tests. Shared files are edited surgically so stale browser workflow state falls back to the default Agent view and old HTTP routes use the normal 404 handler.

**Tech Stack:** Static HTML/CSS/JavaScript, Python HTTP router and services, Node.js AI Gateway, Bash deployment, Python static checks.

## Global Constraints

- Preserve existing SQLite files and server-side API history as read-only recovery data.
- Do not open, migrate, truncate, or delete `LEARNING_DIR/test-lab/test_lab.sqlite3`, `TEST_LAB_DIR`, `TEST_LAB_DB_PATH`, prior API reports, or uploaded artifacts.
- Preserve UI Agent, Midscene YAML, Runner, Sonic, reports, and all non-API workflows.
- Do not add a compatibility redirect, feature flag, placeholder page, or replacement API module.
- Do not refactor unrelated `router.py` code.
- Do not stage or revert unrelated dirty worktree files.

---

### Task 1: Remove the API Browser Surface

**Files:**
- Modify: `tests/frontend_static_checks.py`
- Modify: `task-manager.html:86-97`
- Modify: `task-manager.html:912-924`
- Modify: `js/navigation.js:239-263`
- Modify: `js/agent-status.js:2117-2126`
- Modify: `js/agent-status.js:2232-2261`
- Modify: `css/round5.css`
- Delete: `js/api.js`
- Delete: `js/api-testing.js`
- Delete: `js/api-test-lab.js`

**Interfaces:**
- Consumes: the existing `activateWorkflow(sectionKey)` and `showWorkflowGuide(sectionKey)` navigation contract.
- Produces: a frontend with no API navigation or script dependency; stale `api_*` workflow keys resolve through the existing default workflow fallback.

- [ ] **Step 1: Replace obsolete API frontend assertions with removal assertions**

Add checks equivalent to:

```python
assert 'data-nav-group="api-testing"' not in html
assert 'js/api.js' not in html
assert 'js/api-testing.js' not in html
assert 'js/api-test-lab.js' not in html
assert 'api_dashboard:' not in agent_status_js
assert "sectionKey === 'api_dashboard'" not in navigation_js
assert not (ROOT / 'js' / 'api.js').exists()
assert not (ROOT / 'js' / 'api-testing.js').exists()
assert not (ROOT / 'js' / 'api-test-lab.js').exists()
```

Remove prior assertions that require API navigation, API lab selectors, or API workflow functions.

- [ ] **Step 2: Run the frontend check and verify the new contract fails**

Run:

```bash
python3 tests/frontend_static_checks.py
```

Expected: FAIL because the API navigation, scripts, and files still exist.

- [ ] **Step 3: Remove the frontend implementation**

Apply these exact changes:

```text
task-manager.html:
  delete the complete <details data-nav-group="api-testing"> block
  delete script tags for js/api.js, js/api-testing.js, js/api-test-lab.js

js/navigation.js:
  delete api_dashboard/api_assets/api_sync branches
  delete collapsedApiWorkflows handling

js/agent-status.js:
  delete API entries from CONTEXT_TOOLBAR_MAP
  delete API branches and collapsedApiWorkflows from activateWorkflow

css/round5.css:
  delete API-only selector blocks (.api-*, .api-testing-*, .api-lab-*)
  retain any mixed selector rule by removing only its API selector arm

js/:
  delete api.js, api-testing.js, api-test-lab.js
```

- [ ] **Step 4: Run frontend syntax and static checks**

Run:

```bash
node --check js/navigation.js
node --check js/agent-status.js
python3 tests/frontend_static_checks.py
```

Expected: all commands exit 0 and the frontend check reports `ok: True`.

- [ ] **Step 5: Commit the frontend removal**

```bash
git add task-manager.html js/navigation.js js/agent-status.js css/round5.css tests/frontend_static_checks.py
git add -u js/api.js js/api-testing.js js/api-test-lab.js
git commit -m "Remove API automation frontend"
```

---

### Task 2: Remove API Routes and Runtime Services

**Files:**
- Modify: `tests/backend_static_checks.py`
- Modify: `task_server/router.py:2439-3575`
- Delete: `task_server/services/api_asset_service.py`
- Delete: `task_server/services/api_case_contract_service.py`
- Delete: `task_server/services/api_execution_service.py`
- Delete: `task_server/services/api_module_service.py`
- Delete: `task_server/services/api_plan_generation_service.py`
- Delete: `task_server/services/api_report_service.py`
- Delete: `task_server/services/api_schema_diff_service.py`
- Delete: `task_server/services/api_source_service.py`
- Delete: `task_server/services/api_sync_service.py`
- Delete: `task_server/services/api_task_service.py`
- Delete: `task_server/services/api_test_plan_service.py`
- Delete: `task_server/services/api_workbench_service.py`
- Delete: `task_server/services/api_workspace_service.py`
- Delete: `task_server/services/apifox_discovery_service.py`
- Delete: `task_server/services/apifox_service.py`
- Delete: `task_server/services/test_lab_service.py`

**Interfaces:**
- Consumes: the router's existing unmatched-route behavior.
- Produces: no registered `/api/api-testing/*` or `/api/test-lab/*` endpoints and no importable API automation service modules.

- [ ] **Step 1: Add backend removal assertions**

Add checks equivalent to:

```python
router_text = (ROOT / 'task_server' / 'router.py').read_text(encoding='utf-8')
assert '/api/api-testing/' not in router_text
assert '/api/test-lab/' not in router_text
for name in (
    'api_asset_service.py', 'api_case_contract_service.py',
    'api_execution_service.py', 'api_module_service.py',
    'api_plan_generation_service.py', 'api_report_service.py',
    'api_schema_diff_service.py', 'api_source_service.py',
    'api_sync_service.py', 'api_task_service.py',
    'api_test_plan_service.py', 'api_workbench_service.py',
    'api_workspace_service.py', 'apifox_discovery_service.py',
    'apifox_service.py', 'test_lab_service.py',
):
    assert not (ROOT / 'task_server' / 'services' / name).exists()
```

Remove shared backend assertions that call the deleted API modules.

- [ ] **Step 2: Run the backend check and verify the new contract fails**

Run:

```bash
python3 tests/backend_static_checks.py
```

Expected: FAIL because routes and service files still exist.

- [ ] **Step 3: Delete the API route block and service files**

Delete only the contiguous router section beginning with:

```python
# ── API Testing ─────────────────────────────────────────────────────
```

and ending immediately before:

```python
# ── 修复草稿保存 ────────────────────────────────────────────────────
```

Delete the 16 API/Apifox/test-lab service files listed above. Do not touch data directories resolved through `LEARNING_DIR`, `API_TESTING_DIR`, `TEST_LAB_DIR`, or `TEST_LAB_DB_PATH`.

- [ ] **Step 4: Verify router syntax, imports, and backend checks**

Run:

```bash
python3 -m py_compile task_server/router.py
python3 tests/backend_static_checks.py
rg -n "task_server\.services\.(api_|apifox|test_lab)|/api/api-testing/|/api/test-lab/" task_server --glob '*.py'
```

Expected: compile and static checks pass; `rg` returns no matches.

- [ ] **Step 5: Commit the backend removal**

```bash
git add task_server/router.py tests/backend_static_checks.py
git add -u task_server/services
git commit -m "Remove API automation backend"
```

---

### Task 3: Remove API AI Skill and Deployment Dependency

**Files:**
- Modify: `tests/ai_gateway_static_checks.py`
- Modify: `ai-gateway/server.js`
- Modify: `ai_skills/evals/run_skill_evals.py`
- Modify: `deploy/install-server.sh`
- Delete: `ai_skills/prompts/api_test_designer.v1.md`
- Delete: `ai_skills/schemas/api_test_designer.schema.json`

**Interfaces:**
- Consumes: existing AI Gateway skill-action routing and server installer.
- Produces: a Gateway without `api_test_designer` and an installer that does not inspect or install Apifox CLI.

- [ ] **Step 1: Change Gateway checks to require absence**

Replace API designer requirements with:

```python
assert "api_test_designer" not in server
assert not (ROOT / 'ai_skills' / 'prompts' / 'api_test_designer.v1.md').exists()
assert not (ROOT / 'ai_skills' / 'schemas' / 'api_test_designer.schema.json').exists()
```

Remove the API designer schema contract check.

- [ ] **Step 2: Run Gateway checks and verify the new contract fails**

Run:

```bash
python3 tests/ai_gateway_static_checks.py
```

Expected: FAIL because the API skill route and files still exist.

- [ ] **Step 3: Remove AI and deployment dependencies**

Apply these exact changes:

```text
ai-gateway/server.js:
  remove api_test_designer from SKILL_ACTION_MAP

ai_skills/evals/run_skill_evals.py:
  remove api_test_designer from the registered skill list

deploy/install-server.sh:
  remove APIFOX_CLI_* variables
  remove apifox_cli_usable and install_apifox_cli functions
  remove the Apifox install/validation branch
  remove APIFOX_CLI_BIN from generated environment defaults

ai_skills:
  delete the API designer prompt and schema
```

- [ ] **Step 4: Verify Gateway and installer syntax**

Run:

```bash
node --check ai-gateway/server.js
bash -n deploy/install-server.sh
python3 tests/ai_gateway_static_checks.py
rg -n "api_test_designer|APIFOX_CLI|apifox-cli|command -v apifox" ai-gateway ai_skills deploy/install-server.sh tests/ai_gateway_static_checks.py
```

Expected: syntax and static checks pass; `rg` returns no runtime/check matches.

- [ ] **Step 5: Commit the AI and deployment removal**

```bash
git add ai-gateway/server.js ai_skills/evals/run_skill_evals.py deploy/install-server.sh tests/ai_gateway_static_checks.py
git add -u ai_skills/prompts/api_test_designer.v1.md ai_skills/schemas/api_test_designer.schema.json
git commit -m "Remove API automation AI dependencies"
```

---

### Task 4: Delete Obsolete API Tests and Verify the Platform

**Files:**
- Delete: `tests/api_asset_sync_checks.py`
- Delete: `tests/api_case_contract_checks.py`
- Delete: `tests/api_manual_workflow_checks.py`
- Delete: `tests/api_native_execution_checks.py`
- Delete: `tests/api_project_workspace_checks.py`
- Delete: `tests/api_runtime_recovery_checks.py`
- Delete: `tests/api_test_lab_checks.py`
- Delete: `tests/api_workbench_checks.py`
- Delete: `tests/apifox_discovery_checks.py`
- Modify: `tests/visual_smoke_check.js` only if it still imports API-specific flows
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Consumes: Tasks 1-3 completed removal contracts.
- Produces: a clean repository state with no executable API automation module and documented preserved-data boundary.

- [ ] **Step 1: Delete obsolete API-specific test modules**

Delete the nine test files listed above. Remove API-only imports or calls from shared visual checks, preserving all UI Agent coverage.

- [ ] **Step 2: Scan runtime and tests for remaining API automation dependencies**

Run:

```bash
rg -n "api-testing|test-lab|api_test_designer|api_(asset|case_contract|execution|module|plan_generation|report|schema_diff|source|sync|task|test_plan|workbench|workspace)_service|apifox_(discovery_)?service" task_server js task-manager.html ai-gateway ai_skills deploy tests --glob '!artifacts/**'
```

Expected: no production or active-test dependency matches. Historical docs and preserved screenshots are outside this scan's decision boundary.

- [ ] **Step 3: Update handoff state**

Add a top entry to `CODEX_STATE.md` stating:

```markdown
### 2026-08-08 Existing API automation removed

- Removed the API automation frontend, backend routes/services, AI skill, Apifox CLI deployment dependency, and API-specific tests.
- Preserved SQLite databases, historical API reports, screenshots, and design documents as recovery data.
- The platform no longer opens or writes the preserved API data.
- Replacement API testing design is intentionally out of scope for this change.
```

- [ ] **Step 4: Run complete scoped verification**

Run:

```bash
python3 -m py_compile task_server/router.py
node --check js/navigation.js
node --check js/agent-status.js
node --check ai-gateway/server.js
bash -n deploy/install-server.sh
python3 tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
python3 tests/ai_gateway_static_checks.py
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 5: Verify preserved data was not staged or deleted**

Run:

```bash
git status --short
git diff --name-status HEAD~3..HEAD
```

Expected: no SQLite/database file, API report artifact, UI YAML, historical screenshot, or unrelated dirty file appears as a deletion caused by this work.

- [ ] **Step 6: Commit final cleanup and state**

```bash
git add CODEX_STATE.md tests/visual_smoke_check.js
git add -u tests/api_asset_sync_checks.py tests/api_case_contract_checks.py tests/api_manual_workflow_checks.py tests/api_native_execution_checks.py tests/api_project_workspace_checks.py tests/api_runtime_recovery_checks.py tests/api_test_lab_checks.py tests/api_workbench_checks.py tests/apifox_discovery_checks.py
git commit -m "Finish API automation removal"
```
