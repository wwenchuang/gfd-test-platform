# Apifox Asset Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace manual Apifox project, branch, and environment ID entry with official CLI-backed name discovery while retaining an explicit manual fallback.

**Architecture:** Add a focused Python discovery adapter that runs the official Apifox CLI in a temporary isolated home, sends the token over stdin, and normalizes JSON output. Keep source persistence and OpenAPI synchronization in their existing services, expose two thin authenticated routes, and drive a frontend discovery state machine that saves stable IDs plus non-secret display metadata.

**Tech Stack:** Python 3 standard library, Apifox CLI 2.2.8 (minimum 2.2.6), existing JSON-file services and router, vanilla JavaScript, existing CSS, Python `unittest`, Playwright visual smoke checks.

## Global Constraints

- Apifox discovery uses only documented CLI commands; do not call CLI-internal HTTP endpoints.
- The supported CLI floor is `2.2.6`; deployment installs and verifies `2.2.8`.
- Tokens must not enter argv, shell strings, logs, HTTP responses, screenshots, git diffs, or global CLI login state.
- Project, branch, and environment IDs remain the stable synchronization values; names are display metadata only.
- Existing OpenAPI export, immutable revisions, module scoping, AI generation, MeterSphere execution, Agent, Runner, Sonic, Figma, and historical YAML behavior must remain unchanged.
- CLI failures must expose a safe manual fallback and must not block existing manually configured sources.
- `task_server/router.py` receives only thin route handlers; do not refactor it.
- Preserve all pre-existing dirty worktree changes and stage only changes made for this feature.

---

### Task 1: Isolated Apifox CLI Discovery Adapter

**Files:**
- Create: `task_server/services/apifox_discovery_service.py`
- Create: `tests/apifox_discovery_checks.py`
- Modify: `package.json`

**Interfaces:**
- Produces: `ApifoxDiscoveryError(code: str, message: str, http_status: int)`
- Produces: `get_cli_capability(cli_bin: str | None = None) -> dict`
- Produces: `discover_projects(access_token: str, *, base_url: str = ..., timeout_seconds: float = 20.0, cli_bin: str | None = None) -> dict`
- Produces: `discover_project_context(access_token: str, project_id: str, *, base_url: str = ..., timeout_seconds: float = 25.0, cli_bin: str | None = None) -> dict`
- Depends on: Python standard-library `subprocess`, `tempfile`, `json`, `shutil`, `time`, and `os`.

- [ ] **Step 1: Write failing adapter security and normalization tests**

Create a fake executable CLI in the test temporary directory. It must record argv separately, read login tokens from stdin, store login state only under the injected temporary `HOME`, and return Apifox-shaped JSON envelopes:

```python
FAKE_CLI = r'''#!/usr/bin/env python3
import json
import os
import pathlib
import sys

home = pathlib.Path(os.environ["HOME"])
args_log = pathlib.Path(os.environ["FAKE_APIFOX_ARGS_LOG"])
args_log.write_text(
    args_log.read_text(encoding="utf-8") + json.dumps(sys.argv[1:]) + "\n"
    if args_log.exists()
    else json.dumps(sys.argv[1:]) + "\n",
    encoding="utf-8",
)
args = sys.argv[1:]
if args == ["--version"]:
    print("2.2.8")
elif args[:2] == ["auth", "login"]:
    token = sys.stdin.readline().strip()
    if token != os.environ["FAKE_APIFOX_EXPECTED_TOKEN"]:
        print(json.dumps({"success": False, "error": {"code": "AUTHENTICATION_FAILED"}}))
        raise SystemExit(1)
    (home / "logged-in").write_text("yes", encoding="utf-8")
    print(json.dumps({"success": True, "data": {"message": "ok"}}))
elif not (home / "logged-in").exists():
    print(json.dumps({"success": False, "error": {"code": "AUTHENTICATION_FAILED"}}))
    raise SystemExit(1)
elif args[:2] == ["project", "list"]:
    print(json.dumps({"success": True, "data": [{
        "id": 5904970,
        "name": "3D 接口",
        "description": "打印业务",
        "team": {"id": 12, "name": "功夫豆"},
    }]}))
elif args[:2] == ["project", "get"]:
    print(json.dumps({"success": True, "data": {
        "id": 5904970,
        "name": "3D 接口",
        "description": "打印业务",
        "team": {"id": 12, "name": "功夫豆"},
    }}))
elif args[:2] == ["branch", "list"]:
    print(json.dumps({"success": True, "data": [{"id": 88, "name": "测试分支"}]}))
elif args[:2] == ["environment", "list"]:
    print(json.dumps({"success": True, "data": [{"id": 99, "name": "APP 测试环境"}]}))
else:
    print(json.dumps({"success": False, "error": {"code": "UNKNOWN"}}))
    raise SystemExit(2)
'''
```

Tests must assert:

```python
result = discovery.discover_projects(
    token,
    cli_bin=str(fake_cli),
    timeout_seconds=5,
)
self.assertEqual("3D 接口", result["projects"][0]["name"])
self.assertEqual("5904970", result["projects"][0]["id"])
self.assertNotIn(token, args_log.read_text(encoding="utf-8"))
self.assertNotIn(token, json.dumps(result, ensure_ascii=False))
self.assertEqual([], list(temp_root.glob("midscene-apifox-*")))
```

Add context assertions for the synthetic `主分支（默认）` and `不绑定环境` entries, plus tests for unsupported version, auth failure, malformed JSON, and timeout.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3 tests/apifox_discovery_checks.py -v
```

Expected: `ImportError` for missing `task_server.services.apifox_discovery_service`.

- [ ] **Step 3: Implement the minimal isolated CLI runner**

Implement the public error and capability contract:

```python
MINIMUM_CLI_VERSION = (2, 2, 6)
DEFAULT_CLI_VERSION = "2.2.8"


class ApifoxDiscoveryError(RuntimeError):
    def __init__(self, code: str, message: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.manual_fallback = True

    def as_dict(self) -> dict:
        return {
            "ok": False,
            "code": self.code,
            "error": str(self),
            "manual_fallback": True,
        }
```

Resolve `APIFOX_CLI_BIN` with `shutil.which`, parse semantic version components, and reject versions below `2.2.6`.

For each public discovery call:

```python
with tempfile.TemporaryDirectory(prefix="midscene-apifox-") as home:
    env = _isolated_environment(home)
    _run_cli(
        [cli_path, "auth", "login", "--api-base-url", base_url],
        input_text=f"{access_token}\n",
        env=env,
        deadline=deadline,
        token=access_token,
    )
    payload = _run_json_cli(
        [cli_path, "project", "list", "--api-base-url", base_url],
        env=env,
        deadline=deadline,
        token=access_token,
    )
```

Use `shell=False`, argument arrays, captured output, a monotonic overall deadline, and a `finally`-safe temporary directory. The child environment must set `HOME`, `XDG_CONFIG_HOME`, `NO_COLOR=1`, and `APIFOX_CLI_TELEMETRY=0`, while preserving only network/certificate/proxy variables needed by HTTPS.

Normalize CLI envelopes through `payload["data"]`; stringify IDs; include only project `id/name/description/team`, branch `id/name/is_default`, and environment `id/name/is_default`. Never return raw CLI output.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
python3 tests/apifox_discovery_checks.py -v
python3 -m py_compile task_server/services/apifox_discovery_service.py tests/apifox_discovery_checks.py
```

Expected: all discovery adapter tests pass.

- [ ] **Step 5: Register the focused test command**

Add to `package.json`:

```json
"test:apifox-discovery": "python3 tests/apifox_discovery_checks.py -v"
```

Include the same test in `test:static` after `tests/api_asset_sync_checks.py`.

- [ ] **Step 6: Commit the adapter**

```bash
git add task_server/services/apifox_discovery_service.py tests/apifox_discovery_checks.py package.json
git commit -m "Add isolated Apifox asset discovery"
```

---

### Task 2: Persist Provider Names and Backfill OpenAPI Titles

**Files:**
- Modify: `task_server/services/api_source_service.py`
- Modify: `task_server/services/api_sync_service.py`
- Modify: `tests/api_asset_sync_checks.py`
- Modify: `tests/api_project_workspace_checks.py`

**Interfaces:**
- Consumes: normalized project/context dictionaries from Task 1.
- Produces: `normalize_provider_metadata(value: object) -> dict`
- Produces: additive public `source["provider_metadata"]`.
- Extends: `update_api_source_discovery_state(..., provider_metadata: dict | None = None)`.

- [ ] **Step 1: Write failing source metadata tests**

Add tests that save discovered metadata and verify the raw/public source behavior:

```python
saved = self.service.save_api_source({
    "source_type": "apifox",
    "project_id": "5904970",
    "access_token": "secret-apifox-token",
    "provider_metadata": {
        "project_name": "3D 接口",
        "team_name": "功夫豆",
        "branch_name": "主分支（默认）",
        "environment_name": "APP 测试环境",
        "discovery_source": "apifox_cli",
    },
})
self.assertEqual("3D 接口", saved["provider_metadata"]["project_name"])
self.assertEqual("3D 接口", saved["name"])
self.assertNotIn("secret-apifox-token", json.dumps(saved, ensure_ascii=False))
```

Add synchronization tests:

- Missing project metadata receives `info.title` with `discovery_source=openapi_info`.
- Existing `discovery_source=apifox_cli` project name is not overwritten by a different OpenAPI title.
- Provider metadata changes do not change `source_config_fingerprint`.

- [ ] **Step 2: Run source/sync tests and verify RED**

Run:

```bash
python3 tests/api_asset_sync_checks.py -v
python3 tests/api_project_workspace_checks.py -v
```

Expected: provider metadata assertions fail because the field is not persisted/backfilled.

- [ ] **Step 3: Implement additive provider metadata normalization**

Add a bounded normalizer:

```python
def normalize_provider_metadata(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "project_name": _bounded_text(raw.get("project_name", raw.get("projectName")), 200),
        "project_description": _bounded_text(raw.get("project_description", raw.get("projectDescription")), 500),
        "team_id": _bounded_text(raw.get("team_id", raw.get("teamId")), 100),
        "team_name": _bounded_text(raw.get("team_name", raw.get("teamName")), 200),
        "branch_name": _bounded_text(raw.get("branch_name", raw.get("branchName")), 200),
        "environment_name": _bounded_text(raw.get("environment_name", raw.get("environmentName")), 200),
        "discovered_at": _bounded_text(raw.get("discovered_at", raw.get("discoveredAt")), 40),
        "discovery_source": (
            str(raw.get("discovery_source", raw.get("discoverySource")) or "")
            if str(raw.get("discovery_source", raw.get("discoverySource")) or "") in {"apifox_cli", "openapi_info"}
            else ""
        ),
    }
```

When a new Apifox source has no explicit non-placeholder name, default `name` to
`provider_metadata.project_name`. Preserve existing names on edits.

Extend `update_api_source_discovery_state` to merge provider metadata under the source lock. During successful/no-change sync, derive:

```python
info_title = str(((full_document.get("info") or {}).get("title") or "")).strip()
provider_metadata = dict(source.get("provider_metadata") or {})
if info_title and not provider_metadata.get("project_name"):
    provider_metadata.update({
        "project_name": info_title,
        "discovered_at": _now(),
        "discovery_source": "openapi_info",
    })
```

Do not overwrite an `apifox_cli` project name.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python3 tests/api_asset_sync_checks.py -v
python3 tests/api_project_workspace_checks.py -v
python3 -m py_compile task_server/services/api_source_service.py task_server/services/api_sync_service.py
```

Expected: all source, sync, and workspace tests pass.

- [ ] **Step 5: Commit provider metadata**

```bash
git add task_server/services/api_source_service.py task_server/services/api_sync_service.py tests/api_asset_sync_checks.py tests/api_project_workspace_checks.py
git commit -m "Persist Apifox provider display names"
```

---

### Task 3: Add Authenticated Discovery Routes

**Files:**
- Modify: `task_server/router.py`
- Modify: `tests/apifox_discovery_checks.py`
- Modify: `tests/backend_static_checks.py`

**Interfaces:**
- Consumes: Task 1 `discover_projects`, `discover_project_context`, and `ApifoxDiscoveryError`.
- Consumes: `api_source_service.get_api_source(source_id, masked=False)`.
- Produces:
  - `POST /api/api-testing/apifox/discovery/projects`
  - `POST /api/api-testing/apifox/discovery/project-context`

- [ ] **Step 1: Write failing route tests**

Add a fake handler with `_authorized`, `_body`, and `_json`. Monkeypatch Task 1 functions and assert:

```python
router.POST_ROUTES["/api/api-testing/apifox/discovery/projects"](handler, {})
self.assertEqual(200, handler.responses[-1][0])
self.assertEqual("3D 接口", handler.responses[-1][1]["projects"][0]["name"])
```

Cover:

- unauthenticated request returns `401`;
- direct `access_token` reaches the service but never appears in response;
- `source_id` resolves the stored raw token and source `base_url`;
- missing source returns `404`;
- missing credentials/project ID returns `400`;
- `ApifoxDiscoveryError("AUTH_FAILED", ..., 401)` preserves safe code/status and `manual_fallback=true`.

- [ ] **Step 2: Run route tests and verify RED**

Run:

```bash
python3 tests/apifox_discovery_checks.py -v
```

Expected: both route keys are missing from `router.POST_ROUTES`.

- [ ] **Step 3: Implement two thin route handlers**

Add a small private credential resolver near the API testing routes:

```python
def _api_testing_apifox_discovery_credentials(data):
    from task_server.services import api_source_service
    source_id = str(data.get("source_id") or data.get("sourceId") or "").strip()
    if source_id:
        source = api_source_service.get_api_source(source_id, masked=False)
        if not source:
            raise LookupError("API source 不存在")
        return (
            str(source.get("access_token") or "").strip(),
            str(source.get("base_url") or "https://api.apifox.com").strip(),
        )
    return (
        str(data.get("access_token") or data.get("accessToken") or "").strip(),
        str(data.get("base_url") or data.get("baseUrl") or "https://api.apifox.com").strip(),
    )
```

Each route must:

1. call `_require_user_auth`;
2. validate the JSON object and credentials;
3. call the discovery service;
4. return only normalized output;
5. map `LookupError`, `ValueError`, and `ApifoxDiscoveryError` to the specified status.

- [ ] **Step 4: Extend route registration checks**

Require both exact POST paths in `check_api_testing_routes_registered`.

- [ ] **Step 5: Run route/static tests and verify GREEN**

Run:

```bash
python3 tests/apifox_discovery_checks.py -v
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/router.py tests/backend_static_checks.py
```

Expected: route/security tests and backend static checks pass.

- [ ] **Step 6: Commit only the new router hunks**

Because `task_server/router.py` contains pre-existing unstaged changes, stage only the discovery route hunks plus the clean test files. Verify with:

```bash
git diff --cached -- task_server/router.py tests/apifox_discovery_checks.py tests/backend_static_checks.py
git commit -m "Expose Apifox discovery endpoints"
```

The cached diff must not contain unrelated router changes.

---

### Task 4: Replace Default ID Inputs with a Name-First Discovery Flow

**Files:**
- Modify: `js/state.js`
- Modify: `js/api-testing.js`
- Modify: `css/round5.css`
- Modify: `task-manager.html`
- Modify: `tests/frontend_static_checks.py`

**Interfaces:**
- Consumes: Task 3 discovery routes.
- Consumes/produces: Task 2 `provider_metadata`.
- Produces: `apiSourceDisplayName(source)`, discovery state reset/render/action functions, and manual fallback controls.

- [ ] **Step 1: Write failing frontend contract checks**

Replace the old requirement that `api-source-environment-id` is always visible with checks for:

```python
require(
    "/api-testing/apifox/discovery/projects" in api_testing_js
    and "/api-testing/apifox/discovery/project-context" in api_testing_js
    and "读取 Apifox 资产" in api_testing_js,
    "New Apifox sources must discover projects and context by name",
)
require(
    "apiSourceDisplayName" in api_testing_js
    and "provider_metadata" in api_testing_js
    and "source.project_id" not in project_selector_function,
    "Project selector must prefer provider names and stop appending raw IDs",
)
require(
    "无法读取？手动连接" in api_testing_js
    and "api-source-manual-fields" in api_testing_js,
    "Manual IDs must remain an explicit fallback",
)
```

Add checks for project search, named branch/environment selectors, loading/error states, token invalidation, and cache version updates.

- [ ] **Step 2: Run frontend static checks and verify RED**

Run:

```bash
python3 tests/frontend_static_checks.py
```

Expected: discovery flow checks fail against the manual form.

- [ ] **Step 3: Add frontend discovery state**

In `js/state.js` add:

```javascript
let apiSourceDiscoveryState = {
  status: 'idle',
  projects: [],
  project: null,
  branches: [],
  environments: [],
  error: '',
  manual: false,
  search: ''
};
```

Implement a reset helper in `js/api-testing.js` and call it when starting/canceling a draft, changing the Token, changing the project, or closing settings.

- [ ] **Step 4: Render provider names and the two-step selection flow**

Change the project switcher label to:

```javascript
function apiSourceDisplayName(source = {}) {
  const metadata = source.provider_metadata || {};
  return metadata.project_name || source.name || source.source_id || 'API 项目';
}
```

Do not append `project_id`.

For a new draft render:

- password Token input;
- “读取 Apifox 资产” primary button;
- fixed-height loading/error/empty area;
- search input and repeated project result buttons;
- selected project summary;
- branch and environment `<select>` controls using names;
- sync interval, automatic sync, and scope controls after context loads;
- a collapsed “无法读取？手动连接” details section containing the legacy name/ID inputs.

For an existing source render provider project/branch/environment names, saved credential state, and “重新读取 Apifox 资产”. Existing manual data remains accessible only through the fallback details.

- [ ] **Step 5: Implement discovery actions and save payload**

Project discovery payload:

```javascript
const payload = source.source_id
  ? {source_id: source.source_id}
  : {access_token: token};
```

Context discovery payload:

```javascript
const payload = source.source_id && !token
  ? {source_id: source.source_id, project_id: projectId}
  : {access_token: token, project_id: projectId};
```

On selection, save:

```javascript
provider_metadata: {
  project_name: selectedProject.name || '',
  project_description: selectedProject.description || '',
  team_id: selectedProject.team?.id || '',
  team_name: selectedProject.team?.name || '',
  branch_name: selectedBranch?.name || '主分支（默认）',
  environment_name: selectedEnvironment?.name || '不绑定环境',
  discovered_at: new Date().toISOString(),
  discovery_source: 'apifox_cli'
}
```

Use the selected IDs for `project_id`, `branch_id`, and `environment_id`. Manual mode continues collecting the legacy inputs. A discovery error must preserve entered Token and expose retry plus fallback.

- [ ] **Step 6: Add responsive, readable styles**

Add focused styles for:

- `.api-source-discovery`
- `.api-source-discovery-action`
- `.api-source-project-results`
- `.api-source-project-option`
- `.api-source-context-grid`
- `.api-source-discovery-state`
- `.api-source-manual-fallback`

Use existing surfaces and border variables, radius at most `4px`, stable min/max heights, no nested cards, and one-column layout below `700px`.

- [ ] **Step 7: Update cache versions**

Update `round5.css`, `state.js`, and `api-testing.js` query versions in `task-manager.html` to `20260729-apifox-discovery`. Update the exact cache assertion in `tests/frontend_static_checks.py`.

- [ ] **Step 8: Run frontend checks and inspect desktop/mobile**

Run:

```bash
python3 tests/frontend_static_checks.py
python3 -m py_compile tests/frontend_static_checks.py
```

Use Playwright against a mock route implementation to inspect:

- desktop `1440x900`;
- mobile `390x844`;
- no horizontal overflow;
- names, search, loading, error, and manual fallback are readable;
- no raw ID is present in the primary project switcher.

- [ ] **Step 9: Commit the frontend**

```bash
git add js/state.js js/api-testing.js css/round5.css task-manager.html tests/frontend_static_checks.py
git commit -m "Add name-first Apifox source setup"
```

---

### Task 5: Install and Report the Supported CLI Capability

**Files:**
- Modify: `deploy/install-server.sh`
- Modify: `deploy/midscene.env.example`
- Modify: `tests/backend_static_checks.py`

**Interfaces:**
- Consumes: Task 1 `APIFOX_CLI_BIN` and version floor.
- Produces: deploy-time `apifox-cli@2.2.8` installation when Node/npm are available.

- [ ] **Step 1: Write failing deployment contract checks**

Add static assertions that installation:

- checks Node major version `>=14`;
- installs exactly `apifox-cli@2.2.8`;
- verifies `apifox --version`;
- does not fail the main server install when npm/CLI installation is unavailable;
- documents `APIFOX_CLI_BIN` in `midscene.env.example`.

- [ ] **Step 2: Run backend checks and verify RED**

Run:

```bash
python3 tests/backend_static_checks.py
```

Expected: Apifox CLI deployment assertions fail.

- [ ] **Step 3: Add bounded deployment setup**

Add an installer block before the service file is enabled:

```bash
APIFOX_CLI_PACKAGE_VERSION="${APIFOX_CLI_PACKAGE_VERSION:-2.2.8}"
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  node_major="$(node -p 'Number(process.versions.node.split(\".\")[0])' 2>/dev/null || printf '0')"
  if [ "${node_major}" -ge 14 ]; then
    if npm install -g "apifox-cli@${APIFOX_CLI_PACKAGE_VERSION}" \
      && command -v apifox >/dev/null 2>&1 \
      && apifox --version >/dev/null 2>&1; then
      echo "Apifox CLI 已就绪：$(apifox --version)"
    else
      echo "警告：Apifox CLI 安装或校验失败；平台仍可使用手动 ID 连接。"
    fi
  else
    echo "警告：Node.js 版本低于 14；Apifox 自动发现不可用，可使用手动 ID 连接。"
  fi
else
  echo "警告：未检测到 Node.js/npm；Apifox 自动发现不可用，可使用手动 ID 连接。"
fi
```

Add an empty documented `APIFOX_CLI_BIN` override to the environment example without forcing a path.

- [ ] **Step 4: Run deployment/static checks and verify GREEN**

Run:

```bash
bash -n deploy/install-server.sh
python3 tests/backend_static_checks.py
git diff --check
```

Expected: shell syntax and backend checks pass.

- [ ] **Step 5: Commit deployment support**

```bash
git add deploy/install-server.sh deploy/midscene.env.example tests/backend_static_checks.py
git commit -m "Install supported Apifox discovery CLI"
```

---

### Task 6: Full Verification, Real Read-Only Discovery, and State Handoff

**Files:**
- Modify: `CODEX_STATE.md`
- Verify only: all files changed by Tasks 1-5.

**Interfaces:**
- Consumes: complete discovery flow.
- Produces: verified implementation state and deployment instructions.

- [ ] **Step 1: Run focused and repository checks**

Run:

```bash
python3 tests/apifox_discovery_checks.py -v
python3 tests/api_asset_sync_checks.py -v
python3 tests/api_project_workspace_checks.py -v
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
python3 -m py_compile \
  task_server/services/apifox_discovery_service.py \
  task_server/services/api_source_service.py \
  task_server/services/api_sync_service.py \
  task_server/router.py
bash -n deploy/install-server.sh
git diff --check
```

Run `npm test` from a temporary repository copy so the user's pre-existing dirty visual artifacts are not overwritten.

- [ ] **Step 2: Run real Apifox read-only discovery**

Install `apifox-cli@2.2.8` outside the repository if needed. Invoke a local Python harness that prompts for the Token on stdin/getpass, calls `discover_projects`, selects the known project only after matching its returned name, and calls `discover_project_context`.

Verify:

- CLI capability reports `2.2.8` or a compatible version;
- project list includes the expected Chinese name;
- branch/environment names are returned;
- output and errors contain no Token;
- no persistent Apifox account remains under the real user home;
- no remote write command runs.

- [ ] **Step 3: Inspect frontend visually**

Start the local server on an unused port or use the visual mock server. Capture temporary desktop/mobile screenshots and inspect them. Confirm:

- first viewport shows Token plus one clear action;
- projects are searchable and name-first;
- branch/environment are name selections;
- ID fields are hidden until manual fallback expands;
- the project switcher contains no appended ID;
- no overlap, overflow, or unexpected scroll reset occurs.

- [ ] **Step 4: Update state**

Append a dated `CODEX_STATE.md` entry containing:

- root cause;
- official CLI design;
- exact files changed;
- security boundaries;
- focused/full test results;
- real read-only discovery result without secret values;
- deployment and post-deploy verification steps.

- [ ] **Step 5: Review final diff and commit**

Check:

```bash
git status --short --branch
git diff --stat
git diff --check
git diff --cached --check
```

Stage only feature-owned changes and commit:

```bash
git add CODEX_STATE.md
git commit -m "Document Apifox discovery verification"
```

- [ ] **Step 6: Provide deployment and verification commands**

Use the repository's standard deployment commands:

```bash
cd /opt/midscene-task-platform-src
git pull --ff-only
bash deploy/install-server.sh
systemctl restart midscene-task
curl http://127.0.0.1:8091/api/health
curl http://127.0.0.1:8088/api/health
```

After deployment, verify the page through the real platform login and confirm the Token is rotated because it was previously pasted into this conversation.
