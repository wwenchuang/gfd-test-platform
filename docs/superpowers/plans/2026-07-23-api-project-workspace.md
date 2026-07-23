# API Project Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn each existing Apifox source into a complete API project workspace with module-scoped assets, auditable AI batches, project-specific MeterSphere execution, and non-secret business-authentication references.

**Architecture:** Keep `source_id` as the project boundary and extend the existing source, immutable revision, plan, MeterSphere adapter and UI contracts. Add one non-secret workspace binding store and one asynchronous plan-generation job store; business secrets are forwarded to a MeterSphere environment variable and never written to local storage.

**Tech Stack:** Python 3 standard library services and JSON storage, MeterSphere `v3.6.5-lts` exact adapter, vanilla JavaScript/HTML/CSS, `unittest` contract checks, Playwright visual smoke tests.

## Global Constraints

- `source_id` is the API project workspace boundary; do not add a duplicate tenant/workspace identifier.
- Apifox project discovery must not use undocumented APIs.
- A generation contains 1-60 endpoints and each sequential AI batch contains at most 12.
- AI failure is explicit; a required-AI batch cannot be labeled successful with deterministic local fallback.
- Module paths are selectors only; stable endpoint identity remains `endpoint_key`.
- MeterSphere connection credentials remain global, while project/environment selection is stored per `source_id`.
- Business secrets are stored only in the selected MeterSphere environment; plans, revisions, local bindings, browser responses, logs and reports contain only `auth_ref` and variable metadata.
- Literal sensitive headers remain blocked; only an exact, binding-authorized MeterSphere variable reference may be materialized remotely.
- Preserve existing source, asset, revision, plan, execution and remote ownership IDs.
- Do not modify UI Agent, Midscene YAML, Runner, Sonic, `yaml_executable_scorer.py`, historical task YAML or local Windows Runner files.
- Do not start Phase D/E/F or change the verified Qwen model policy.
- Codex commits locally but never pushes; the user performs push and deployment.

---

### Task 1: Module Catalog and Scoped Synchronization

**Files:**
- Create: `task_server/services/api_module_service.py`
- Create: `tests/api_project_workspace_checks.py`
- Modify: `task_server/services/api_source_service.py`
- Modify: `task_server/services/api_asset_service.py`
- Modify: `task_server/services/api_sync_service.py`
- Modify: `task_server/router.py`
- Modify: `package.json`

**Interfaces:**
- Produces: `normalize_module_path(value) -> str`
- Produces: `module_catalog(document) -> list[dict]`
- Produces: `filter_document(document, module_paths) -> dict`
- Produces: `module_summary(endpoints) -> dict`
- Produces: source fields `sync_scope`, `module_catalog`, `scope_fingerprint`
- Consumes: existing Apifox full-document export and immutable revision staging.

- [ ] **Step 1: Write failing module and source tests**

Add `tests/api_project_workspace_checks.py` with `unittest` cases that prove:

```python
class ApiModuleScopeChecks(unittest.TestCase):
    def test_parent_module_matches_descendants_but_not_prefix_siblings(self):
        filtered = api_module_service.filter_document(
            sample_document(),
            ["家用业务/app接口/我的"],
        )
        operations = api_module_service.module_catalog(filtered)
        self.assertEqual(
            [item["path"] for item in operations if item["endpoint_count"]],
            [
                "家用业务/app接口/我的/我的下载",
                "家用业务/app接口/我的/我的收藏",
            ],
        )

    def test_selected_scope_requires_a_module(self):
        with self.assertRaisesRegex(ValueError, "至少选择一个模块"):
            api_source_service.save_api_source({
                "name": "项目 B",
                "project_id": "2",
                "access_token": "secret",
                "sync_scope": {"mode": "selected", "module_paths": []},
            })

    def test_public_source_never_returns_token(self):
        source = api_source_service.save_api_source({
            "name": "项目 A",
            "project_id": "1",
            "access_token": "secret",
        })
        self.assertNotIn("access_token", source)
        self.assertTrue(source["credential_configured"])
```

The fixture must include `A/B`, `A/B/C` and `A/BB` folders so boundary behavior is
unambiguous.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 tests/api_project_workspace_checks.py -v
```

Expected: import failure for `api_module_service` or missing `sync_scope`.

- [ ] **Step 3: Implement deterministic module helpers**

Create `api_module_service.py` with these public functions:

```python
MODULE_MATCHER_VERSION = "apifox_folder_v1"

def normalize_module_path(value: Any) -> str:
    parts = [part.strip() for part in str(value or "").replace("\\", "/").split("/")]
    return "/".join(part for part in parts if part)

def operation_module_path(path: str, operation: Dict[str, Any]) -> str:
    folder = normalize_module_path(operation.get("x-apifox-folder"))
    if folder:
        return folder
    tags = operation.get("tags") if isinstance(operation.get("tags"), list) else []
    if tags and normalize_module_path(tags[0]):
        return normalize_module_path(tags[0])
    segments = [part for part in str(path or "").split("/") if part]
    return normalize_module_path(segments[0] if segments else "未分组")

def module_selected(module_path: str, selected: Iterable[str]) -> bool:
    module = normalize_module_path(module_path)
    return any(
        module == candidate or module.startswith(f"{candidate}/")
        for candidate in (normalize_module_path(item) for item in selected)
        if candidate
    )
```

`filter_document()` must deep-copy the document, remove only HTTP operations outside the
selected module boundaries, remove empty path items and raise `ValueError` when no
operation remains. `module_catalog()` must return every leaf path with parent/depth/count
and deterministic sort order. `module_summary()` must build the nested read model without
using module paths as endpoint identity.

- [ ] **Step 4: Persist source scope and discovery metadata**

Extend source save/public contracts with:

```python
sync_scope = {
    "mode": mode,
    "module_paths": normalized_paths,
    "matcher_version": api_module_service.MODULE_MATCHER_VERSION,
}
```

Preserve an existing `module_catalog` and `scope_fingerprint` on ordinary source edits.
Add `update_api_source_discovery_state(source_id, module_catalog, scope_fingerprint)` for
structured metadata; do not coerce the catalog to a string.

- [ ] **Step 5: Apply scope before immutable revision staging**

In `run_api_source_sync()`:

```python
full_document = fetched.get("document") or {}
catalog = api_module_service.module_catalog(full_document)
scope = api_source_service.normalized_sync_scope(source.get("sync_scope"))
scoped_document = (
    full_document
    if scope["mode"] == "all"
    else api_module_service.filter_document(full_document, scope["module_paths"])
)
scope_fingerprint = api_module_service.scope_fingerprint(scope)
```

Pass `scope_fingerprint`, `sync_scope` and `module_catalog` to
`stage_api_revision()`. The stage hash must include both the scoped document and scope
fingerprint. Add `module_path` and `module_segments` to endpoints while retaining
`module`. Update source discovery state on no-change and success, and include scope/module
counts in the sync record.

- [ ] **Step 6: Return the module read model**

Extend `/api/api-testing/assets` with:

```json
{
  "source_id": "api_source_xxx",
  "module_summary": {
    "total_modules": 0,
    "total_endpoints": 0,
    "roots": []
  }
}
```

Do not change existing `snapshot`, `endpoints`, `asset` or `revisions` fields.

- [ ] **Step 7: Run focused and compatibility tests**

Run:

```bash
python3 tests/api_project_workspace_checks.py -v
python3 tests/api_asset_sync_checks.py -v
python3 -m py_compile task_server/services/api_module_service.py task_server/services/api_source_service.py task_server/services/api_asset_service.py task_server/services/api_sync_service.py task_server/router.py
git diff --check
```

Expected: all tests pass and no whitespace errors.

- [ ] **Step 8: Commit**

```bash
git add package.json task_server/services/api_module_service.py task_server/services/api_source_service.py task_server/services/api_asset_service.py task_server/services/api_sync_service.py task_server/router.py tests/api_project_workspace_checks.py
git commit -m "Add module scoped API synchronization"
```

---

### Task 2: Source-Specific MeterSphere Binding

**Files:**
- Create: `task_server/services/api_workspace_service.py`
- Modify: `task_server/services/metersphere_service.py`
- Modify: `task_server/services/metersphere_v365_adapter.py`
- Modify: `task_server/router.py`
- Modify: `tests/api_project_workspace_checks.py`
- Modify: `tests/metersphere_v365_adapter_checks.py`

**Interfaces:**
- Produces: `get_api_workspace_binding(source_id, allow_legacy=True) -> dict`
- Produces: `save_api_workspace_binding(source_id, project_id, environment_id, metadata) -> dict`
- Produces: `MeterSphereV365Adapter.list_projects()`
- Produces: `MeterSphereV365Adapter.list_environments(project_id)`
- Produces: `metersphere_execution_context(force=False, source_id="")`
- Consumes: global MeterSphere connection/authentication config.

- [ ] **Step 1: Write failing binding tests**

Add tests for:

```python
def test_two_sources_keep_independent_metersphere_bindings(self):
    first = api_workspace_service.save_api_workspace_binding(
        "api_source_a", "ms_project_a", "ms_env_a",
        project_name="A", environment_name="A测试",
    )
    second = api_workspace_service.save_api_workspace_binding(
        "api_source_b", "ms_project_b", "ms_env_b",
        project_name="B", environment_name="B测试",
    )
    self.assertNotEqual(first["binding_id"], second["binding_id"])
    self.assertEqual(
        api_workspace_service.get_api_workspace_binding("api_source_a")["project_id"],
        "ms_project_a",
    )

def test_second_source_never_inherits_legacy_global_selection(self):
    self._create_sources(2)
    self.assertEqual(
        api_workspace_service.get_api_workspace_binding(
            "api_source_b", allow_legacy=True
        ),
        {},
    )
```

Adapter tests must verify project options and environments are normalized from exact
v3.6.5 endpoints and that request headers use the bound project, not the global project.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 tests/api_project_workspace_checks.py -v
python3 tests/metersphere_v365_adapter_checks.py
```

Expected: missing workspace service/list methods or wrong project header.

- [ ] **Step 3: Implement the non-secret binding store**

Store one file under `api-testing/workspace-bindings/<source_id>.json`:

Create the exact public signature
`save_api_workspace_binding(source_id: str, project_id: str, environment_id: str, *,
project_name: str = "", environment_name: str = "", verified_at: str = "") ->
Dict[str, Any]`.

The stable `binding_id` is a SHA-256 derivative of `source_id`, not a timestamp. The file
contains no MeterSphere token/access key/secret key. `config_fingerprint` hashes provider,
project and environment IDs.

When no binding exists, `allow_legacy=True` may persist the global project/environment
only if exactly one API source exists. It must return `{}` for a second source.

- [ ] **Step 4: Make MeterSphere requests configuration-aware**

Change the internal request boundary to:

```python
def _request_json(
    method: str,
    path: str,
    payload: Dict[str, Any] | None = None,
    timeout: float = 30,
    *,
    config: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    cfg = dict(config or _load_raw_config())
```

Bind the exact adapter callback to its selected config so `PROJECT` and `ORGANIZATION`
headers match the source binding.

- [ ] **Step 5: Add exact project/environment reads**

Add adapter methods:

```python
def list_projects(self) -> List[Dict[str, Any]]:
    result = self._request(
        "GET",
        f"/project/list/options/{self.config['workspace_id']}",
        timeout=20,
    )
    return self._normalize_project_options(result)

def list_environments(self, project_id: str) -> List[Dict[str, Any]]:
    result = self._request("GET", f"/api/test/env-list/{project_id}", timeout=20)
    return self._normalize_environment_options(result, project_id)
```

Reject missing organization/project IDs and normalize only enabled records with stable ID,
name and project ID.

- [ ] **Step 6: Extend binding routes and execution context**

Add authenticated routes:

```text
GET  /api/api-testing/sources/{source_id}/execution-binding
POST /api/api-testing/sources/{source_id}/execution-binding
```

POST must validate the selected IDs through live exact adapter reads before persisting.
Extend execution context with `source_id`, `binding`, all live businesses for the
organization, environments for the selected project, source-filtered plans and
source-filtered executions.

- [ ] **Step 7: Snapshot binding into executions**

New execution records persist:

```json
{
  "source_id": "api_source_xxx",
  "binding_id": "api_execution_binding_xxx",
  "project_id": "ms-project",
  "environment_id": "ms-env",
  "binding_fingerprint": "71b07863c4bb6447"
}
```

`start_metersphere_execution`, worker readiness, push, trigger, polling and report reads
must derive the adapter config from this snapshot. A plan belonging to another source or a
changed binding is rejected before remote writes.

- [ ] **Step 8: Run focused tests**

Run:

```bash
python3 tests/api_project_workspace_checks.py -v
python3 tests/metersphere_v365_adapter_checks.py
python3 tests/api_case_contract_checks.py
python3 -m py_compile task_server/services/api_workspace_service.py task_server/services/metersphere_service.py task_server/services/metersphere_v365_adapter.py task_server/router.py
git diff --check
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add task_server/services/api_workspace_service.py task_server/services/metersphere_service.py task_server/services/metersphere_v365_adapter.py task_server/router.py tests/api_project_workspace_checks.py tests/metersphere_v365_adapter_checks.py
git commit -m "Bind MeterSphere execution per API source"
```

---

### Task 3: Business Authentication References

**Files:**
- Modify: `task_server/services/api_workspace_service.py`
- Modify: `task_server/services/api_case_contract_service.py`
- Modify: `task_server/services/api_test_plan_service.py`
- Modify: `task_server/services/metersphere_service.py`
- Modify: `task_server/services/metersphere_v365_adapter.py`
- Modify: `task_server/router.py`
- Modify: `tests/api_project_workspace_checks.py`
- Modify: `tests/api_case_contract_checks.py`
- Modify: `tests/metersphere_v365_adapter_checks.py`

**Interfaces:**
- Produces: public `auth_binding` metadata under one source binding.
- Produces: `save_api_auth_binding(source_id, auth_type, header_name, secret) -> dict`
- Produces: `clear_api_auth_binding(source_id) -> dict`
- Produces: exact adapter environment-variable `upsert`, `verify` and `delete`.
- Consumes: one verified source-specific MeterSphere project/environment binding.

- [ ] **Step 1: Write failing security tests**

Cover these exact invariants:

```python
def test_sensitive_openapi_header_example_never_enters_case(self):
    contract = api_case_contract_service.build_api_case_contract(
        secured_endpoint_with_authorization_example("Bearer leaked"),
        "positive",
    )
    serialized = json.dumps(contract, ensure_ascii=False)
    self.assertNotIn("leaked", serialized)
    self.assertNotIn("Authorization", contract["request"]["headers"])

def test_auth_secret_is_forwarded_but_never_persisted(self):
    result = metersphere_service.save_api_auth_binding(
        "api_source_a", "bearer", "Authorization", "runtime-secret"
    )
    self.assertTrue(result["configured"])
    self.assertNotIn("runtime-secret", json.dumps(self._all_local_files()))

def test_exact_variable_reference_is_the_only_sensitive_header_allowed(self):
    payload, _ = adapter._materialize_case(plan, case, endpoint, definition)
    self.assertEqual(
        payload["request"]["headers"][0]["value"],
        "Bearer ${MTP_API_AUTH_ABC123}",
    )
```

Also test wrong `auth_ref`, wrong environment, literal Bearer/API key, clear and recursive
redaction of `apiKey`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 tests/api_case_contract_checks.py
python3 tests/metersphere_v365_adapter_checks.py
python3 tests/api_project_workspace_checks.py -v
```

Expected: sensitive header is materialized, auth binding functions are missing, or exact
adapter still rejects every Authorization header.

- [ ] **Step 3: Prevent sensitive OpenAPI values from entering contracts**

Add one shared normalized sensitive-header predicate. For required sensitive headers,
remove literal values from `request.headers`; a secured positive case relies on
`request.auth_ref`, while an undocumented required sensitive header remains
`needs_review`.

- [ ] **Step 4: Implement exact MeterSphere environment variable updates**

Extend the exact adapter with:

Add the exact methods `get_environment_detail(environment_id)`,
`upsert_environment_variable(environment_id, key, value, description)`,
`delete_environment_variable(environment_id, key)` and
`verify_environment_variable(environment_id, key)`, each returning a dictionary.

Use exact `GET /project/environment/get/{id}` and multipart
`POST /project/environment/update` with one JSON `request` part. Read-modify-write the
full environment config and mutate only the platform-owned variable key. Never return the
value.

- [ ] **Step 5: Persist only auth metadata**

Use deterministic names:

```python
variable_name = f"MTP_API_AUTH_{stable_hash(f'{source_id}:{environment_id}', 12).upper()}"
auth_ref = f"api_auth_{stable_hash(f'{source_id}:{environment_id}', 16)}"
```

Supported types are `bearer` and `api_key`. Bearer fixes the header name to
`Authorization`; API key requires a printable custom header without CR/LF. Store only
type, header name, variable name, configured state and timestamps after remote verify
succeeds.

- [ ] **Step 6: Bind secured plans and remote cases**

New secured positive cases use the current source auth binding `auth_ref`. Evaluation adds
`request.auth_ref` when missing or mismatched. The plan stores the non-secret auth binding
snapshot and binding fingerprint.

In `_materialize_case()`, add a sensitive header only when:

```python
request["auth_ref"] == auth_binding["auth_ref"]
and auth_binding["configured"] is True
and auth_binding["environment_id"] == self.config["environment_id"]
```

The value must be exactly `Bearer ${VAR}` or `${VAR}` from the binding. Existing literal
sensitive-header rejection remains unchanged for every other input.

- [ ] **Step 7: Add authenticated auth routes**

```text
POST   /api/api-testing/sources/{source_id}/auth-binding
DELETE /api/api-testing/sources/{source_id}/auth-binding
```

Responses return only public metadata. Empty secret does not clear; DELETE is required.

- [ ] **Step 8: Run focused tests**

Run:

```bash
python3 tests/api_case_contract_checks.py
python3 tests/metersphere_v365_adapter_checks.py
python3 tests/api_project_workspace_checks.py -v
python3 -m py_compile task_server/services/api_workspace_service.py task_server/services/api_case_contract_service.py task_server/services/api_test_plan_service.py task_server/services/metersphere_service.py task_server/services/metersphere_v365_adapter.py task_server/router.py
git diff --check
```

Expected: all tests pass and credential fixture strings appear only in test inputs.

- [ ] **Step 9: Commit**

```bash
git add task_server/services/api_workspace_service.py task_server/services/api_case_contract_service.py task_server/services/api_test_plan_service.py task_server/services/metersphere_service.py task_server/services/metersphere_v365_adapter.py task_server/router.py tests/api_project_workspace_checks.py tests/api_case_contract_checks.py tests/metersphere_v365_adapter_checks.py
git commit -m "Add MeterSphere environment auth bindings"
```

---

### Task 4: Source-Scoped Asynchronous AI Plan Batches

**Files:**
- Create: `task_server/services/api_plan_generation_service.py`
- Modify: `task_server/services/api_test_plan_service.py`
- Modify: `task_server/router.py`
- Modify: `tests/api_project_workspace_checks.py`
- Modify: `tests/api_case_contract_checks.py`

**Interfaces:**
- Produces: `start_api_plan_generation(source_id, revision_id, endpoint_ids, module_paths, model_config=None, spawn=True) -> dict`
- Produces: `get_api_plan_generation(generation_id) -> dict`
- Produces: `retry_api_plan_generation(generation_id) -> dict`
- Produces: plan fields `source_id`, `module_paths`, `selected_endpoint_keys`,
  `generation_id`, `batch_index`, `batch_count`, binding/auth snapshots.
- Consumes: current immutable revision and source workspace binding.

- [ ] **Step 1: Write failing generation tests**

Add tests using a stubbed AI result:

```python
def test_twenty_five_endpoints_generate_sequential_12_12_1_batches(self):
    generation = api_plan_generation_service.start_api_plan_generation(
        "api_source_a",
        "api_revision_a",
        self.endpoint_ids(25),
        ["家用业务/app接口"],
        spawn=False,
    )
    completed = api_plan_generation_service.run_api_plan_generation(
        generation["generation_id"],
        generate_plan=self.recording_generator,
    )
    self.assertEqual([row["endpoint_count"] for row in completed["batches"]], [12, 12, 1])
    self.assertEqual(self.max_concurrent_generators, 1)

def test_required_ai_failure_is_not_saved_as_successful_local_fallback(self):
    completed = self.run_with_second_batch_failure()
    self.assertEqual(completed["status"], "partial")
    self.assertEqual(completed["batches"][1]["status"], "failed")
    self.assertFalse(completed["batches"][1].get("plan_id"))
```

Also test 0/61 endpoint rejection, source/revision mismatch, module-outside selection,
failed-batch-only retry, source-filtered plan listing and stale binding fingerprint.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 tests/api_project_workspace_checks.py -v
python3 tests/api_case_contract_checks.py
```

Expected: missing generation service and source fields.

- [ ] **Step 3: Extend ordinary plan generation**

Change `generate_api_test_plan()` additively:

Use the exact additive signature
`generate_api_test_plan(snapshot_id, endpoint_ids, model_config=None, use_ai=None, *,
source_id="", module_paths=None, generation_id="", batch_index=1, batch_count=1,
require_ai_success=False) -> Dict[str, Any]`.

Verify source ownership and selected module boundaries by endpoint key. If
`require_ai_success` and AI fails, raise before writing a plan. Persist the source,
revision, selected endpoint keys, normalized module paths, scope/binding fingerprints and
non-secret auth snapshot.

Extend `list_api_test_plans(limit, source_id="")` and
`list_full_api_test_plans(limit, source_id="")`.

- [ ] **Step 4: Implement generation job storage and worker**

Store records under `api-testing/plan-generations`. Enforce:

```python
MAX_ENDPOINTS = 60
AI_BATCH_SIZE = 12
TERMINAL_STATES = {"succeeded", "partial", "failed", "cancelled"}
```

The worker processes chunks serially, persists each transition/event, and calls ordinary
plan generation with `use_ai=True` and `require_ai_success=True`. Retry processes only
failed batches and reuses succeeded plan IDs.

- [ ] **Step 5: Add generation routes**

```text
POST /api/api-testing/plan-generations
GET  /api/api-testing/plan-generations/{generation_id}
POST /api/api-testing/plan-generations/{generation_id}/retry
```

Start returns HTTP 202 and `poll_after_ms`. Extend plan GET with `source_id`; retain direct
legacy `/plans/generate`.

- [ ] **Step 6: Revalidate plan scope at confirmation and execution**

Confirmation and `_execution_plan()` must reject:

- source mismatch;
- selected endpoint outside the stored revision/module scope;
- active revision stale;
- changed MeterSphere binding fingerprint;
- missing/mismatched required authentication binding.

Legacy plans keep existing behavior.

- [ ] **Step 7: Run focused tests**

Run:

```bash
python3 tests/api_project_workspace_checks.py -v
python3 tests/api_case_contract_checks.py
python3 tests/metersphere_v365_adapter_checks.py
python3 -m py_compile task_server/services/api_plan_generation_service.py task_server/services/api_test_plan_service.py task_server/services/metersphere_service.py task_server/router.py
git diff --check
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add task_server/services/api_plan_generation_service.py task_server/services/api_test_plan_service.py task_server/services/metersphere_service.py task_server/router.py tests/api_project_workspace_checks.py tests/api_case_contract_checks.py
git commit -m "Generate API plans in auditable AI batches"
```

---

### Task 5: API Project and Module Asset UI

**Files:**
- Modify: `js/state.js`
- Modify: `js/api-testing.js`
- Modify: `css/round5.css`
- Modify: `task-manager.html`
- Modify: `tests/frontend_static_checks.py`
- Modify: `tests/visual_smoke_check.js`

**Interfaces:**
- Consumes: source `sync_scope`/`module_catalog`, asset `module_summary`.
- Produces: one current `sourceId + revisionId` scope key and controlled module/endpoint
  selections.

- [ ] **Step 1: Add failing frontend assertions and fixtures**

Static checks must require:

```text
apiTestingProjectScope
apiTestingSourceDraftMode
apiTestingSelectionByScope
renderApiProjectSelector
renderApiModuleTree
apiModuleSelectionState
```

Playwright fixtures must provide two sources, at least three module paths and 25 endpoints.
Assertions must prove no endpoint is selected initially, source switching clears revision
state, parent selection excludes a same-prefix sibling, and the right table contains only
the active module.

- [ ] **Step 2: Run checks and verify RED**

Run:

```bash
python3 tests/frontend_static_checks.py
node tests/visual_smoke_check.js
```

Expected: missing project/module UI assertions.

- [ ] **Step 3: Centralize API project scope state**

Add state:

```javascript
let apiTestingProjectScope = { sourceId: '', revisionId: '' };
let apiTestingSourceDraftMode = false;
const apiTestingSelectionByScope = new Map();
```

Use `${sourceId}:${revisionId}` as the only selection key. Switching source aborts pending
asset/generation/context requests, clears active module and execution state, and discards
late responses by request ID.

- [ ] **Step 4: Build project create/switch controls**

Render a compact selector plus Lucide-compatible add icon button. Add mode opens an empty
source form and saves without `source_id`; edit mode preserves write-only token behavior.
Options display source name and Apifox project ID. Do not add a new sidebar entry.

- [ ] **Step 5: Render a controlled module tree and endpoint table**

Use an unframed two-column work surface:

- left module tree with counts and parent/child checked/indeterminate states;
- right current-module table with search/method filters and its own scroll;
- explicit select-current-module action;
- no default selection after a source/revision change.

At `700px` and below, stack the tree and table with independent bounded scrolling. Do not
allow the endpoint table to push page width.

- [ ] **Step 6: Save sync scope**

Source settings expose an `all/selected` segmented control and selected module summary.
Save normalized paths through the source route; sync remains a separate explicit action.

- [ ] **Step 7: Update cache versions and visual evidence**

Add desktop/mobile screenshots:

```text
api-project-switch.png
api-project-switch-mobile.png
api-module-tree.png
api-module-tree-mobile.png
```

Update `state.js`, `api-testing.js` and `round5.css` cache versions in
`task-manager.html`.

- [ ] **Step 8: Run frontend checks**

Run:

```bash
python3 tests/frontend_static_checks.py
node tests/visual_smoke_check.js
git diff --check
```

Expected: checks pass and screenshots show no horizontal overflow or overlapping text.

- [ ] **Step 9: Commit**

```bash
git add js/state.js js/api-testing.js css/round5.css task-manager.html tests/frontend_static_checks.py tests/visual_smoke_check.js tests/artifacts/api-project-switch.png tests/artifacts/api-project-switch-mobile.png tests/artifacts/api-module-tree.png tests/artifacts/api-module-tree-mobile.png
git commit -m "Add API project and module workspace UI"
```

---

### Task 6: Plan Review, Binding and Authentication UI

**Files:**
- Modify: `js/api-testing.js`
- Modify: `css/round5.css`
- Modify: `task-manager.html`
- Modify: `tests/frontend_static_checks.py`
- Modify: `tests/visual_smoke_check.js`

**Interfaces:**
- Consumes: asynchronous plan generation, source-filtered plans, execution binding,
  auth-binding and source-filtered MeterSphere execution context.
- Produces: persistent generation progress and guarded execution actions.

- [ ] **Step 1: Add failing UI flow assertions**

Fixtures and assertions must prove:

- a 25-endpoint generation reports `12 / 12 / 1`;
- batches run sequentially and each successful batch exposes its real server `plan_id`;
- a failed batch offers retry without resubmitting successful batches;
- plan cards show source/revision/module/AI trace/readiness/binding/auth status;
- stale plans offer regenerate and cannot confirm;
- MeterSphere project/environment changes save to the selected source binding;
- Bearer/API-key inputs are empty after save and never rehydrated;
- an old generation/execution poll response cannot redraw a new source.

- [ ] **Step 2: Run checks and verify RED**

Run:

```bash
python3 tests/frontend_static_checks.py
node tests/visual_smoke_check.js
```

Expected: missing generation/binding/auth controls.

- [ ] **Step 3: Use asynchronous server plan generation**

POST selected endpoint IDs, module paths, source and revision to
`/plan-generations`. Poll by backend `poll_after_ms`, render stable batch rows, and stop at
terminal state. Use one `AbortController` and request ID per source scope.

Plan detail renders only backend facts; frontend must not infer AI success, freshness or
executability.

- [ ] **Step 4: Add project-specific MeterSphere binding controls**

Execution context always includes `source_id`. Business/environment selectors save the
source execution binding rather than global MeterSphere selection. Keep connection
credentials and advanced paths in the existing settings drawer.

Every plan card shows its actual binding and disables execution when the current binding
fingerprint differs.

- [ ] **Step 5: Add the business authentication panel**

Render a separate panel with:

- type segmented control: Bearer/API Key;
- API-key header field only for API Key;
- empty password input for a new secret;
- configured state, environment, variable name and update time;
- replace and explicit clear actions.

Never display or refill the token. Saving posts to the source auth-binding route and
renders only returned public metadata.

- [ ] **Step 6: Preserve polling/log interaction state**

After every awaited generation or execution response, compare the captured source scope
key with the current scope. Keep existing stable technical-log expansion and scroll
preservation, keyed by source plus execution/generation ID.

- [ ] **Step 7: Add visual evidence**

Add desktop/mobile screenshots:

```text
api-batch-review.png
api-batch-review-mobile.png
api-business-auth.png
api-business-auth-mobile.png
metersphere-project-binding.png
metersphere-project-binding-mobile.png
```

Update cache versions.

- [ ] **Step 8: Run frontend checks**

Run:

```bash
python3 tests/frontend_static_checks.py
node tests/visual_smoke_check.js
git diff --check
```

Expected: all checks pass and no secret fixture appears in rendered HTML or screenshots.

- [ ] **Step 9: Commit**

```bash
git add js/api-testing.js css/round5.css task-manager.html tests/frontend_static_checks.py tests/visual_smoke_check.js tests/artifacts/api-batch-review.png tests/artifacts/api-batch-review-mobile.png tests/artifacts/api-business-auth.png tests/artifacts/api-business-auth-mobile.png tests/artifacts/metersphere-project-binding.png tests/artifacts/metersphere-project-binding-mobile.png
git commit -m "Complete API plan and execution workspace UI"
```

---

### Task 7: Full Verification and State Handoff

**Files:**
- Modify: `CODEX_STATE.md`
- Modify if required by checks: `deploy/midscene.env.example`

**Interfaces:**
- Consumes: all completed tasks.
- Produces: deployable local commit and precise remaining QA acceptance.

- [ ] **Step 1: Run credential and protected-file audits**

Run:

```bash
git diff 916fe4d --name-only
git diff 916fe4d -- task_server/services/sonic_service.py task_server/services/yaml_executable_scorer.py server-tasks server-tasks-all deploy/install-windows-runner-service.local.ps1 ai_skills/prompts/api_test_designer.v1.md
rg -n "afxp_|d1Z9CKVE|IXSO|IR4q|runtime-secret|Bearer leaked" --glob '!tests/**' .
```

Expected: protected-file diff is empty and no real/fixture secret exists in production
files.

- [ ] **Step 2: Run all focused backend checks**

Run:

```bash
python3 tests/api_project_workspace_checks.py -v
python3 tests/api_asset_sync_checks.py -v
python3 tests/api_case_contract_checks.py
python3 tests/metersphere_v365_adapter_checks.py
python3 -m py_compile task_server/services/api_module_service.py task_server/services/api_workspace_service.py task_server/services/api_plan_generation_service.py task_server/services/api_source_service.py task_server/services/api_asset_service.py task_server/services/api_sync_service.py task_server/services/api_case_contract_service.py task_server/services/api_test_plan_service.py task_server/services/metersphere_service.py task_server/services/metersphere_v365_adapter.py task_server/router.py
```

Expected: all checks pass.

- [ ] **Step 3: Run required static and visual checks**

Run:

```bash
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
node tests/visual_smoke_check.js
git diff --check
```

Expected: all checks pass.

- [ ] **Step 4: Run the complete suite**

Run:

```bash
npm test
```

Expected: exit code 0, including undefined-name, backend, frontend, AI Gateway, API
contracts, MeterSphere adapter, skill fixtures and desktop/mobile Playwright.

- [ ] **Step 5: Update the handoff state**

Add one dated `CODEX_STATE.md` entry with:

- source-as-workspace decision;
- module catalog and selected-module sync semantics;
- AI `12` batch and `60` generation limits;
- source/revision/module/binding/auth plan gates;
- MeterSphere environment-variable secret flow;
- tests and screenshots;
- exact QA acceptance still required after user deployment;
- explicit statement that Codex did not push and protected files were untouched.

- [ ] **Step 6: Commit**

```bash
git add CODEX_STATE.md deploy/midscene.env.example
git commit -m "Document API project workspace delivery"
```

- [ ] **Step 7: Final branch review**

Generate a review package from `916fe4d` to `HEAD`, run a broad code review, fix every
Critical/Important finding with focused tests, then rerun `npm test`.
