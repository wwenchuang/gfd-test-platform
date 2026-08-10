# API Testing Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete manual Apifox refresh, durable API test tasks, and the deployed "My Favorites" regression without adding another primary workflow or navigation surface.

**Architecture:** Extend the existing API testing module rather than replacing it. Store one encrypted, owner-scoped Apifox credential; discover provider context through the installed CLI; export OpenAPI through the official HTTP API; and activate source plus environment snapshots only after explicit confirmation. Add a small durable task record that references existing AI jobs and executions, while the existing workbench remains the only design/debug surface.

**Tech Stack:** Python 3.11, SQLAlchemy 2, Alembic, PostgreSQL JSONB, urllib/subprocess adapters, Vue 3, Pinia, TypeScript, Vitest, Playwright.

## Global Constraints

- Apifox refresh is manual only; opening the workbench must never contact Apifox.
- The Apifox token is encrypted with `ApiTestingSettings.secret_key` and never returned, logged, reported, or sent to AI.
- JSON import remains available under an advanced fallback disclosure.
- Source revisions are immutable; local environment revisions are editable and never written back to Apifox.
- Activation must be explicit and a failed refresh must not replace the active source or environment.
- A task may reference only project-owned source, environment, endpoints, AI jobs, cases, and executions.
- Reuse existing AI job, execution, SSE, report, and `PASSED`/`FAILED`/`BROKEN` semantics.
- Do not add a new menu item, duplicate execution record, schedule, Mock, email, distributed lease, or suite DAG.
- All user-facing copy is Chinese and all secret values remain redacted.

---

### Task 1: Encrypted Apifox Credential

**Files:**
- Create: `task_server/api_testing/models/provider.py`
- Create: `task_server/api_testing/repositories/provider_repository.py`
- Create: `task_server/api_testing/services/provider_service.py`
- Create: `task_server/api_testing/migrations/versions/0003_api_testing_completion.py`
- Modify: `task_server/api_testing/models/__init__.py`
- Test: `tests/api_testing/test_provider_service.py`
- Test: `tests/api_testing/test_migrations.py`

**Interfaces:**
- Produces: `ProviderService.save_apifox_credential(owner_id: str, token: str) -> ProviderCredentialView`.
- Produces: `ProviderService.get_apifox_credential(owner_id: str) -> ProviderCredentialView`.
- Produces: `ProviderService.require_apifox_token(owner_id: str) -> str`, used only by provider adapters.

- [ ] **Step 1: Write failing service and migration tests**

```python
def test_apifox_token_is_encrypted_and_public_view_is_redacted(db, secret_key):
    view = ProviderService(db, secret_key).save_apifox_credential("admin", "afxp_secret")
    assert view.configured is True
    assert "secret" not in repr(view)
    assert db.scalar(select(ApiProviderCredential)).ciphertext != "afxp_secret"
    assert ProviderService(db, secret_key).require_apifox_token("admin") == "afxp_secret"

def test_completion_migration_creates_provider_credentials_and_test_tasks():
    tables = migrated_table_names("head")
    assert {"api_provider_credentials", "api_test_tasks"} <= tables
```

- [ ] **Step 2: Run focused tests and confirm the missing model/service failure**

Run: `python3 -m pytest tests/api_testing/test_provider_service.py tests/api_testing/test_migrations.py -q`

Expected: FAIL because `ApiProviderCredential` and `ProviderService` do not exist.

- [ ] **Step 3: Implement encrypted upsert and redacted view**

```python
@dataclass(frozen=True)
class ProviderCredentialView:
    provider: str
    configured: bool
    fingerprint: str
    updated_at: datetime | None

class ProviderService:
    def save_apifox_credential(self, owner_id, token): ...
    def get_apifox_credential(self, owner_id): ...
    def require_apifox_token(self, owner_id): ...
```

Use `encrypt_secret`, `decrypt_secret`, and `secret_fingerprint`. Persist provider `apifox`, ciphertext, fingerprint, and key version under a unique `(owner_id, provider)` constraint.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m pytest tests/api_testing/test_provider_service.py tests/api_testing/test_migrations.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/models task_server/api_testing/repositories/provider_repository.py task_server/api_testing/services/provider_service.py task_server/api_testing/migrations/versions/0003_api_testing_completion.py tests/api_testing/test_provider_service.py tests/api_testing/test_migrations.py
git commit -m "Add encrypted Apifox credentials"
```

### Task 2: Apifox Discovery and Official OpenAPI Export

**Files:**
- Create: `task_server/api_testing/adapters/apifox_discovery.py`
- Create: `task_server/api_testing/adapters/apifox_openapi.py`
- Create: `task_server/api_testing/contracts/provider.py`
- Create: `task_server/api_testing/services/apifox_service.py`
- Modify: `task_server/api_testing/adapters/__init__.py`
- Modify: `task_server/api_testing/services/__init__.py`
- Test: `tests/api_testing/test_apifox_service.py`

**Interfaces:**
- Produces: `ApifoxDiscoveryAdapter.list_projects(token: str) -> tuple[ApifoxProject, ...]`.
- Produces: `ApifoxDiscoveryAdapter.get_context(token: str, project_id: str) -> ApifoxProjectContext`.
- Produces: `ApifoxOpenApiAdapter.export(token: str, project_id: str, branch_id: str | None) -> dict`.
- Produces: `ApifoxService.preview_refresh(owner_id, request, actor_id) -> ApifoxRefreshPreviewView`.

- [ ] **Step 1: Write failing adapter/service tests**

```python
def test_discovery_passes_token_on_stdin_and_parses_projects(fake_runner):
    projects = ApifoxDiscoveryAdapter(runner=fake_runner).list_projects("afxp_secret")
    assert projects[0].name == "3D"
    assert "afxp_secret" not in fake_runner.argv_text
    assert fake_runner.stdin_text == "afxp_secret\n"

def test_export_uses_official_endpoint_and_bearer_header(fake_http):
    document = ApifoxOpenApiAdapter(http=fake_http).export("afxp_secret", "5904970", None)
    assert fake_http.url.endswith("/v1/projects/5904970/export-openapi")
    assert fake_http.headers["Authorization"] == "Bearer afxp_secret"
    assert document["openapi"] == "3.0.1"

def test_preview_contains_source_diff_and_environment_candidate(services):
    preview = services.apifox.preview_refresh("admin", request, "admin")
    assert preview.source.added_count == 3
    assert preview.environment.name == "生产环境（新）-腾讯云"
    assert preview.environment.services[0].base_url.startswith("https://")
```

- [ ] **Step 2: Run the focused tests and confirm missing adapters fail**

Run: `python3 -m pytest tests/api_testing/test_apifox_service.py -q`

Expected: FAIL on missing `ApifoxDiscoveryAdapter` and `ApifoxOpenApiAdapter`.

- [ ] **Step 3: Implement safe discovery and export**

Use isolated CLI config, `apifox auth login` with the token on stdin, and fixed argument arrays for `project list`, `project get`, `branch list`, and `environment list`. Use `POST https://api.apifox.com/v1/projects/{project_id}/export-openapi` with bounded timeout, JSON content validation, and stable Chinese errors for unavailable CLI, authentication, permission, timeout, and malformed responses.

Normalize environment services, public variables, headers, and secret placeholders into the existing `EnvironmentService.import_from_source` input shape. Never place provider or environment secrets in an exception message.

- [ ] **Step 4: Run adapter/service and subprocess safety tests**

Run: `python3 -m pytest tests/api_testing/test_apifox_service.py tests/api_testing/test_source_service.py tests/api_testing/test_environment_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/adapters task_server/api_testing/contracts/provider.py task_server/api_testing/services/apifox_service.py tests/api_testing/test_apifox_service.py
git commit -m "Connect Apifox discovery and OpenAPI export"
```

### Task 3: Provider HTTP Flow and Explicit Activation

**Files:**
- Modify: `task_server/api_testing/http.py`
- Modify: `task_server/api_testing/services/apifox_service.py`
- Modify: `task_server/api_testing/services/source_service.py`
- Modify: `task_server/api_testing/services/environment_service.py`
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/api/client.ts`
- Modify: `api-testing-ui/src/stores/setup.ts`
- Modify: `api-testing-ui/src/views/AssetsView.vue`
- Modify: `api-testing-ui/src/styles/app.css`
- Test: `tests/api_testing/test_http_contract.py`
- Test: `api-testing-ui/src/stores/setup.spec.ts`

**Interfaces:**
- HTTP `GET /api/api-testing/v1/providers/apifox/credential` returns only configured/fingerprint/update time.
- HTTP `PUT /api/api-testing/v1/providers/apifox/credential` accepts `{token}`.
- HTTP `POST /api/api-testing/v1/providers/apifox/projects` discovers selectable projects.
- HTTP `POST /api/api-testing/v1/providers/apifox/context` discovers branches and environments.
- HTTP `POST /api/api-testing/v1/sources/apifox/preview` returns source diff plus environment candidate.
- HTTP `POST /api/api-testing/v1/sources/apifox/{preview_id}/activate` activates source and persists the environment candidate.

- [ ] **Step 1: Write failing HTTP and Pinia tests**

```python
def test_provider_credential_response_never_contains_token(api_client):
    response = api_client.put("/providers/apifox/credential", {"token": "afxp_secret"})
    assert response.status == 200
    assert "afxp_secret" not in response.text

def test_failed_apifox_preview_keeps_current_revisions(api_client, active_context):
    response = api_client.post("/sources/apifox/preview", invalid_provider_request)
    assert response.status == 422
    assert current_workspace() == active_context
```

```ts
it('saves token, discovers context, previews and explicitly activates', async () => {
  await store.saveApifoxToken('afxp_secret')
  await store.discoverApifoxProjects()
  await store.previewApifox(selection)
  expect(store.activeRevision).toBeNull()
  await store.activateApifoxPreview()
  expect(store.message).toContain('已保存')
})
```

- [ ] **Step 2: Run tests and confirm missing routes/actions fail**

Run: `python3 -m pytest tests/api_testing/test_http_contract.py -q && npm --prefix api-testing-ui test -- --run src/stores/setup.spec.ts`

Expected: FAIL on provider routes and setup store actions.

- [ ] **Step 3: Implement routes and source/environment activation orchestration**

Scope every request to `actor`. Activation validates the candidate source diff before creating a local environment revision. Return both active revision views and update workspace only after both writes succeed in the same database transaction. Preserve the existing JSON preview/activate endpoints unchanged.

- [ ] **Step 4: Replace the primary JSON-only asset flow**

Render three compact steps: `保存访问令牌`, `选择项目与环境`, `检查变化并保存`. Show configured fingerprint, project/branch/environment selects, diff counts, and an explicit `确认保存` command. Move the existing file picker into `<details><summary>高级导入：OpenAPI JSON</summary>...</details>`.

- [ ] **Step 5: Run backend and frontend focused tests**

Run: `python3 -m pytest tests/api_testing/test_http_contract.py tests/api_testing/test_apifox_service.py -q && npm --prefix api-testing-ui test -- --run src/stores/setup.spec.ts`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add task_server/api_testing api-testing-ui/src tests/api_testing
git commit -m "Add manual Apifox refresh workflow"
```

### Task 4: Durable Lightweight API Test Task

**Files:**
- Create: `task_server/api_testing/models/test_task.py`
- Create: `task_server/api_testing/repositories/test_task_repository.py`
- Create: `task_server/api_testing/services/test_task_service.py`
- Create: `task_server/api_testing/contracts/test_task.py`
- Modify: `task_server/api_testing/models/__init__.py`
- Modify: `task_server/api_testing/http.py`
- Modify: `task_server/api_testing/services/ai_service.py`
- Modify: `task_server/api_testing/services/execution_service.py`
- Test: `tests/api_testing/test_test_task_service.py`
- Test: `tests/api_testing/test_http_contract.py`

**Interfaces:**
- Produces: `TestTaskService.save_context(owner_id, payload, actor_id) -> ApiTestTaskView`.
- Produces: `TestTaskService.get_active(project_id, owner_id) -> ApiTestTaskView | None`.
- Produces: `TestTaskService.attach_ai_job(task_id, ai_job_id, actor_id)`.
- Produces: `TestTaskService.attach_execution(task_id, execution_id, actor_id)`.
- Produces: `TestTaskService.refresh_terminal_summary(task_id, actor_id)`.
- HTTP `GET /tasks/active?project_id=...`, `POST /tasks`, `PUT /tasks/{id}`, and `POST /tasks/{id}/run`.

- [ ] **Step 1: Write failing task lifecycle and authorization tests**

```python
def test_task_restores_selection_and_advances_through_ai_debug_and_execution(services):
    task = services.tasks.save_context("admin", selection, "admin")
    services.tasks.attach_ai_job(task.id, ai_job.id, "admin")
    services.tasks.attach_execution(task.id, execution.id, "admin")
    completed = services.tasks.refresh_terminal_summary(task.id, "admin")
    assert completed.state == "completed"
    assert completed.summary["passed"] == 3

def test_task_rejects_endpoint_from_another_source_revision(services):
    with pytest.raises(TestTaskScopeError):
        services.tasks.save_context("admin", cross_revision_selection, "admin")
```

- [ ] **Step 2: Run focused tests and confirm missing task model fails**

Run: `python3 -m pytest tests/api_testing/test_test_task_service.py tests/api_testing/test_http_contract.py -q`

Expected: FAIL on missing `ApiTestTask`.

- [ ] **Step 3: Implement the task record and strict scope validation**

Persist one row per user-created task with project/source/environment references, JSON selected endpoint IDs, state, display name, latest AI job ID, latest execution ID, and JSON summary. Active restoration returns the newest task whose state is not `completed` or `cancelled`. Reusing the same selection updates the active task instead of creating duplicates.

- [ ] **Step 4: Associate existing AI and execution services**

Accept optional `task_id` in AI generation and execution submission. Validate ownership and context before attaching. Async terminal execution updates task state and summary from the canonical execution counts; failures preserve the task for retry.

- [ ] **Step 5: Run task, AI, execution, and HTTP tests**

Run: `python3 -m pytest tests/api_testing/test_test_task_service.py tests/api_testing/test_ai_service.py tests/api_testing/test_execution_service.py tests/api_testing/test_http_contract.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add task_server/api_testing tests/api_testing
git commit -m "Add durable API test tasks"
```

### Task 5: Restore and Run the Current Task in the Workbench

**Files:**
- Create: `api-testing-ui/src/stores/tasks.ts`
- Create: `api-testing-ui/src/stores/tasks.spec.ts`
- Create: `api-testing-ui/src/components/TaskStatusStrip.vue`
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/api/client.ts`
- Modify: `api-testing-ui/src/views/WorkbenchView.vue`
- Modify: `api-testing-ui/src/stores/cases.ts`
- Modify: `api-testing-ui/src/components/DebugDrawer.vue`
- Modify: `api-testing-ui/src/styles/app.css`
- Test: `api-testing-ui/src/stores/cases.spec.ts`
- Test: `tests/api_testing_e2e.spec.mjs`

**Interfaces:**
- `useTasksStore.restore(projectId)` restores the latest active task.
- `useTasksStore.saveSelection(context, endpointIds)` persists the current scope.
- `useTasksStore.runCurrent(caseVersionIds)` submits existing baselines and routes to live execution.

- [ ] **Step 1: Write failing store and browser workflow tests**

```ts
it('restores a saved task without clearing endpoint selection', async () => {
  await store.restore(projectId)
  expect(store.task?.selected_endpoint_ids).toEqual(['endpoint-1', 'endpoint-2'])
})

it('attaches AI generation and debug execution to the current task', async () => {
  await store.saveSelection(context, ['endpoint-1'])
  await cases.generate(['endpoint-1'], environmentId, '覆盖收藏流程', store.task!.id)
  expect(lastRequest.body.task_id).toBe(store.task!.id)
})
```

- [ ] **Step 2: Run UI tests and confirm missing store/component fails**

Run: `npm --prefix api-testing-ui test -- --run src/stores/tasks.spec.ts src/stores/cases.spec.ts`

Expected: FAIL because the task store does not exist.

- [ ] **Step 3: Implement task restoration and a compact status strip**

Restore project/source/environment and selected endpoints after context options load. Persist selection on explicit `保存本次任务`, not on every checkbox click. Display task name, selected count, environment, state, and `执行本任务`; keep AI controls beside endpoint details.

- [ ] **Step 4: Carry task ID through AI, debug, baseline, and execution**

AI generation updates task state to `designing`. Debug attaches the execution but does not mark the task complete. `执行本任务` runs adopted baselines for the selected endpoint set, opens the existing live console, and updates the final task summary from the report.

- [ ] **Step 5: Run unit and browser tests**

Run: `npm --prefix api-testing-ui test -- --run && node --test tests/api_testing_e2e.spec.mjs`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api-testing-ui/src tests/api_testing_e2e.spec.mjs
git commit -m "Connect API tasks to the workbench"
```

### Task 6: Full Verification, State, and Deployment Regression

**Files:**
- Modify: `CODEX_STATE.md`
- Modify only if a verified defect requires it: files already listed in Tasks 1-5

**Interfaces:**
- Consumes all completed behavior from Tasks 1-5.
- Produces a deployable commit and a verified online "My Favorites" run.

- [ ] **Step 1: Run the complete local gate**

Run: `bash tests/run_api_testing_gate.sh`

Expected: Python, Vue, static, browser, and visual checks all PASS.

- [ ] **Step 2: Inspect secret leakage and changed-file scope**

Run: `git diff --check && rg -n "afxp_|eyJ0eXAi" task_server api-testing-ui tests CODEX_STATE.md`

Expected: `git diff --check` exits 0 and the token scan returns no committed secret values.

- [ ] **Step 3: Verify desktop and mobile rendering**

Run: `node tests/api_testing_ui_visual_check.js`

Expected: nonblank workbench and assets screenshots with no overlap, clipped labels, uncontrolled refresh, or horizontal overflow.

- [ ] **Step 4: Update repository state**

Record implemented contracts, migration, focused/full test commands, deployment prerequisites, and the remaining online regression in `CODEX_STATE.md` without modifying protected historical YAML or unrelated user changes.

- [ ] **Step 5: Commit the verified implementation**

```bash
git add CODEX_STATE.md
git commit -m "Complete API testing provider and task workflow"
```

- [ ] **Step 6: Deploy the verified commit**

Push the feature commit through the repository's approved integration path, deploy with `deploy/install-server.sh`, restart `midscene-task`, and verify both `8091` and `8088` health endpoints before browser testing.

- [ ] **Step 7: Run the real deployed regression**

Sign in to `http://101.34.197.12:8088/task-manager.html`, select the saved production environment, place the supplied business token in the local secret variable, select the three "我的收藏" endpoints, generate and inspect AI drafts, debug them, adopt passing versions, execute the current task, watch SSE logs to a terminal state, and inspect the final report and failure analysis. Confirm no request, log, report, or browser response exposes the Apifox or business token.

- [ ] **Step 8: Fix only reproduced general defects and rerun the gate**

For each reproduced defect, add a failing focused regression test, implement the smallest general fix, rerun the focused test and `bash tests/run_api_testing_gate.sh`, update `CODEX_STATE.md`, and commit. Do not add requirement-specific endpoint names or hardcoded payloads.
