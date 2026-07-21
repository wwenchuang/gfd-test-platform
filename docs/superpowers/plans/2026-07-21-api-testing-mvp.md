# API Testing MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first working API testing loop: import Apifox OpenAPI JSON, generate confirmable API test plans, push/run through MeterSphere adapter configuration, and show unified API reports.

**Architecture:** Keep UI automation unchanged. Add focused API testing services under `task_server/services/`, register small routes in `task_server/router.py`, and render a new frontend workspace in `js/api-testing.js`. Store API assets, plans, MeterSphere mappings, and reports under a dedicated `LEARNING_DIR/api-testing` directory.

**Tech Stack:** Python standard library HTTP server and JSON storage, existing `apiRequest` frontend client, vanilla JavaScript, existing CSS system, existing AI skill gateway, MeterSphere HTTP adapter with token-based auth first.

## Global Constraints

- First phase uses OpenAPI JSON upload; it does not require Apifox token.
- Do not implement a custom API runner in this platform.
- Do not replace MeterSphere case, plan, execution, or report management.
- Do not change existing Sonic/Midscene/Runner execution behavior.
- Do not let AI silently overwrite MeterSphere cases; user confirmation is required before push.
- Keep MeterSphere tokens server-side and masked in frontend/logs.
- Do not stage, revert, or overwrite existing user dirty files outside this feature.

---

### Task 1: API Asset Parser And Storage

**Files:**
- Create: `task_server/services/api_asset_service.py`
- Modify: `tests/backend_static_checks.py`

**Interfaces:**
- Produces: `import_openapi_document(name: str, content: object, filename: str = "") -> dict`
- Produces: `list_api_snapshots(limit: int = 20) -> list[dict]`
- Produces: `list_api_endpoints(snapshot_id: str = "") -> list[dict]`
- Produces: `get_api_snapshot(snapshot_id: str) -> dict`

- [ ] **Step 1: Write failing backend checks**

Add `check_api_asset_service_openapi_import()` to `tests/backend_static_checks.py`:

```python
def check_api_asset_service_openapi_import():
    from task_server.services import api_asset_service

    doc = {
        "openapi": "3.0.1",
        "info": {"title": "打印接口", "version": "1.0.0"},
        "paths": {
            "/print/task": {
                "post": {
                    "tags": ["打印"],
                    "summary": "创建打印任务",
                    "operationId": "createPrintTask",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["modelId"],
                                    "properties": {"modelId": {"type": "string"}}
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "成功",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "code": {"type": "integer"},
                                            "data": {
                                                "type": "object",
                                                "properties": {"taskId": {"type": "string"}}
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    imported = api_asset_service.import_openapi_document("打印接口", doc, "print-openapi.json")
    endpoints = imported.get("endpoints") or []
    require(imported.get("snapshot_id"), "OpenAPI import must return a snapshot id")
    require(len(endpoints) == 1, "OpenAPI import must extract one operation")
    endpoint = endpoints[0]
    require(endpoint.get("method") == "POST", "OpenAPI import must normalize method")
    require(endpoint.get("path") == "/print/task", "OpenAPI import must keep path")
    require(endpoint.get("module") == "打印", "OpenAPI import must use first tag as module")
    require("modelId" in endpoint.get("required_fields", []), "OpenAPI import must extract required request fields")
    require(endpoint.get("schema_hash"), "OpenAPI import must compute schema hash")
```

Call it from `main()`.

- [ ] **Step 2: Run failing check**

Run: `python3 tests/backend_static_checks.py`

Expected: FAIL with import error for `api_asset_service`.

- [ ] **Step 3: Implement service**

Create `task_server/services/api_asset_service.py` with JSON file storage under `safe_join(LEARNING_DIR, "api-testing")`. Parse `paths` operations for HTTP methods `GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS`, normalize request/response schemas, compute `schema_hash` from method/path/request/response/security, and save `snapshots/<snapshot_id>.json`.

- [ ] **Step 4: Run check**

Run: `python3 tests/backend_static_checks.py`

Expected: PASS for the new OpenAPI import check.

---

### Task 2: API Test Plan Drafts

**Files:**
- Create: `task_server/services/api_test_plan_service.py`
- Create: `ai_skills/prompts/api_test_designer.v1.md`
- Create: `ai_skills/schemas/api_test_designer.schema.json`
- Modify: `tests/backend_static_checks.py`

**Interfaces:**
- Consumes: `api_asset_service.get_api_snapshot(snapshot_id)`
- Produces: `generate_api_test_plan(snapshot_id: str, endpoint_ids: list[str], model_config: dict | None = None) -> dict`
- Produces: `confirm_api_test_plan(plan_id: str) -> dict`
- Produces: `list_api_test_plans(limit: int = 20) -> list[dict]`

- [ ] **Step 1: Write failing plan check**

Add `check_api_test_plan_generation_is_confirmable()`:

```python
def check_api_test_plan_generation_is_confirmable():
    from task_server.services import api_asset_service, api_test_plan_service

    doc = {
        "openapi": "3.0.0",
        "info": {"title": "账号接口"},
        "paths": {
            "/user/login": {
                "post": {
                    "tags": ["账号"],
                    "summary": "登录",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["username", "password"],
                                    "properties": {
                                        "username": {"type": "string"},
                                        "password": {"type": "string"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "ok"}}
                }
            }
        }
    }
    snapshot = api_asset_service.import_openapi_document("账号接口", doc, "account.json")
    plan = api_test_plan_service.generate_api_test_plan(snapshot.get("snapshot_id"), [])
    require(plan.get("status") == "draft", "Generated API plan must start as draft")
    require(plan.get("cases"), "Generated API plan must contain cases")
    require(any(case.get("type") == "positive" for case in plan.get("cases", [])), "Plan must include positive case")
    require(any(case.get("type") == "negative" for case in plan.get("cases", [])), "Plan must include negative case for required fields")
    confirmed = api_test_plan_service.confirm_api_test_plan(plan.get("plan_id"))
    require(confirmed.get("status") == "confirmed", "Plan confirmation must be explicit")
```

- [ ] **Step 2: Run failing check**

Run: `python3 tests/backend_static_checks.py`

Expected: FAIL with import error for `api_test_plan_service`.

- [ ] **Step 3: Implement service and AI skill**

Implement local deterministic cases first, then attempt `run_ai_skill("api_test_designer", ...)`. On AI error, save deterministic fallback with `ai.enabled=false` and `fallback_reason`. The schema requires `cases[].name`, `cases[].endpoint_id`, `cases[].type`, `cases[].priority`, `cases[].steps`, and `cases[].assertions`.

- [ ] **Step 4: Run check**

Run: `python3 tests/backend_static_checks.py`

Expected: PASS for plan generation and confirmation.

---

### Task 3: MeterSphere Adapter And API Reports

**Files:**
- Create: `task_server/services/metersphere_service.py`
- Create: `task_server/services/api_report_service.py`
- Modify: `tests/backend_static_checks.py`

**Interfaces:**
- Produces: `save_metersphere_config(payload: dict) -> dict`
- Produces: `metersphere_config(masked: bool = True) -> dict`
- Produces: `metersphere_health() -> dict`
- Produces: `push_plan_to_metersphere(plan_id: str) -> dict`
- Produces: `create_metersphere_run(plan_id: str, test_plan_id: str = "") -> dict`
- Produces: `pull_metersphere_report(run_id: str, raw_report: dict | None = None) -> dict`

- [ ] **Step 1: Write failing adapter check**

Add `check_metersphere_config_masks_secrets()`:

```python
def check_metersphere_config_masks_secrets():
    from task_server.services import metersphere_service

    saved = metersphere_service.save_metersphere_config({
        "base_url": "http://metersphere.local",
        "token": "secret-token",
        "workspace_id": "ws1",
        "project_id": "project1",
        "environment_id": "env1"
    })
    require(saved.get("base_url") == "http://metersphere.local", "MeterSphere config must save base_url")
    masked = metersphere_service.metersphere_config(masked=True)
    require(masked.get("token") != "secret-token", "MeterSphere token must be masked")
    require(masked.get("token_configured") is True, "Masked config must expose token presence")
    raw = metersphere_service.metersphere_config(masked=False)
    require(raw.get("token") == "secret-token", "Raw config must remain available server-side")
```

- [ ] **Step 2: Run failing check**

Run: `python3 tests/backend_static_checks.py`

Expected: FAIL with import error for `metersphere_service`.

- [ ] **Step 3: Implement services**

Store config in `LEARNING_DIR/api-testing/metersphere-config.json`. Use `Authorization: Bearer <token>` for token mode. If no push/run endpoint path is configured, return `ok=False`, `requires_config=True`, and a clear message instead of pretending to execute.

- [ ] **Step 4: Run check**

Run: `python3 tests/backend_static_checks.py`

Expected: PASS for secret masking.

---

### Task 4: Backend Routes

**Files:**
- Modify: `task_server/router.py`
- Modify: `tests/backend_static_checks.py`

**Interfaces:**
- `GET /api/api-testing/overview`
- `GET /api/api-testing/assets`
- `POST /api/api-testing/openapi/import`
- `GET /api/api-testing/plans`
- `POST /api/api-testing/plans/generate`
- `POST /api/api-testing/plans/confirm`
- `GET /api/api-testing/metersphere/config`
- `POST /api/api-testing/metersphere/config`
- `POST /api/api-testing/metersphere/health`
- `POST /api/api-testing/metersphere/push`
- `POST /api/api-testing/metersphere/run`
- `GET /api/api-testing/reports`
- `POST /api/api-testing/reports/pull`

- [ ] **Step 1: Write failing route registration check**

Add `check_api_testing_routes_registered()`:

```python
def check_api_testing_routes_registered():
    from task_server import router

    for path in (
        "/api/api-testing/overview",
        "/api/api-testing/assets",
        "/api/api-testing/plans",
        "/api/api-testing/metersphere/config",
        "/api/api-testing/reports",
    ):
        require(path in router.GET_ROUTES, f"Missing API testing GET route: {path}")
    for path in (
        "/api/api-testing/openapi/import",
        "/api/api-testing/plans/generate",
        "/api/api-testing/plans/confirm",
        "/api/api-testing/metersphere/config",
        "/api/api-testing/metersphere/health",
        "/api/api-testing/metersphere/push",
        "/api/api-testing/metersphere/run",
        "/api/api-testing/reports/pull",
    ):
        require(path in router.POST_ROUTES, f"Missing API testing POST route: {path}")
```

- [ ] **Step 2: Run failing check**

Run: `python3 tests/backend_static_checks.py`

Expected: FAIL with missing routes.

- [ ] **Step 3: Register routes**

Import the new services in `task_server/router.py`. Routes read JSON bodies with `handler._json_body()`, return `handler._json({...})`, and never expose raw tokens.

- [ ] **Step 4: Run check**

Run: `python3 tests/backend_static_checks.py`

Expected: PASS for route registration.

---

### Task 5: Frontend API Testing Workspace

**Files:**
- Modify: `task-manager.html`
- Modify: `js/api.js`
- Modify: `js/navigation.js`
- Modify: `js/state.js`
- Create: `js/api-testing.js`
- Modify: `css/round5.css`
- Modify: `tests/frontend_static_checks.py`

**Interfaces:**
- Produces frontend functions: `showApiTestingDashboard()`, `showApiAssetsPage()`, `showApiPlanPage()`, `showApiExecutionPage()`, `showApiReportsPage()`
- Consumes backend routes from Task 4 through `apiRequest`

- [ ] **Step 1: Write failing frontend checks**

Update `tests/frontend_static_checks.py`:

```python
require(html.count('class="nav-group"') == 6, "Sidebar must include six nav groups after adding API testing")
require('data-nav-group="api-testing"' in html, "Sidebar nav groups must include api-testing")
for workflow in ("api_dashboard", "api_assets", "api_plan", "api_execution", "api_reports"):
    require(f'data-workflow="{workflow}"' in html, f"Sidebar missing API testing workflow: {workflow}")
require("js/api-testing.js" in html, "API testing frontend module must be loaded")
require("showApiTestingDashboard" in html and "showApiAssetsPage" in html, "API testing pages must render through dedicated functions")
require("apiLogExpandedKeys" in html and "runId + stepId" in html, "API execution logs must preserve expanded state by stable keys")
```

- [ ] **Step 2: Run failing check**

Run: `python3 tests/frontend_static_checks.py`

Expected: FAIL with missing API testing group/module.

- [ ] **Step 3: Implement frontend**

Add the sidebar group after `用例`. Add `WORKFLOW_SECTIONS` entries for five API workflows. Add `showWorkflowGuide()` dispatches. Implement `js/api-testing.js` pages with upload, endpoint table, plan generation, confirmation, MeterSphere config, push/run actions, reports, and stable log details.

- [ ] **Step 4: Run check**

Run: `python3 tests/frontend_static_checks.py`

Expected: PASS for frontend static checks.

---

### Task 6: End-To-End Verification And State

**Files:**
- Modify: `CODEX_STATE.md`

- [ ] **Step 1: Run backend checks**

Run:

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/api_asset_service.py task_server/services/api_test_plan_service.py task_server/services/metersphere_service.py task_server/services/api_report_service.py task_server/router.py
```

Expected: both commands pass.

- [ ] **Step 2: Run frontend checks**

Run:

```bash
python3 tests/frontend_static_checks.py
```

Expected: PASS.

- [ ] **Step 3: Run existing full frontend test**

Run:

```bash
npm test
```

Expected: PASS.

- [ ] **Step 4: Check diff hygiene**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; status only includes feature files plus pre-existing user dirty files.

- [ ] **Step 5: Update state**

Update `CODEX_STATE.md` with the API testing MVP files, verification commands, and note that no push was performed.
