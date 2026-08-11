# API Testing M2 Execution Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add batch debugging, strict baseline adoption, scoped baseline regression, and lineage-preserving failed reruns.

**Architecture:** Reuse the M1 execution snapshot and suite runner. Add execution lineage fields in migration `0005`, centralize baseline eligibility in `CaseService`, and expose batch/rerun actions through existing execution resources and UI drawers.

**Tech Stack:** Python 3.10, SQLAlchemy 2, Alembic, Celery, Vue 3, Pinia, TypeScript, Vitest, Playwright, SSE.

## Global Constraints

- Debug executions create durable evidence but never mutate baselines.
- Adoption requires a `PASSED` exact case/source/environment version and explicit user action.
- Reruns create a new execution and never overwrite the parent.
- Default rerun selects `FAILED` and `BROKEN`, never `PASSED`.
- A run with upgraded case or environment versions is a new task execution, not a rerun.
- Reports keep original and rerun statistics separately.

---

### Task 1: Add execution lineage migration 0005

**Files:**
- Modify: `task_server/api_testing/models/execution.py`
- Create: `task_server/api_testing/migrations/versions/0005_execution_lineage.py`
- Modify: `tests/api_testing/test_migrations.py`

**Interfaces:**
- Adds: `parent_execution_id`, `rerun_reason`, `root_execution_id`, and `execution_mode` to `ApiExecution`.

- [ ] **Step 1: Write failing migration tests**

```python
def test_0005_adds_execution_lineage(migrated_connection):
    columns = {item["name"] for item in inspect(migrated_connection).get_columns("api_executions")}
    assert {"parent_execution_id", "root_execution_id", "rerun_reason", "execution_mode"} <= columns
```

- [ ] **Step 2: Verify migration test fails**

Run: `.venv/bin/python -m pytest tests/api_testing/test_migrations.py -q`

Expected: FAIL because lineage columns are absent.

- [ ] **Step 3: Add nullable backward-compatible fields and indexes**

Existing records receive `execution_mode` from `execution_type` using deterministic mapping: `DEBUG` remains `DEBUG`; existing regression/task runs become `REGRESSION`. Existing records use their own ID as effective root in service views without rewriting every row.

- [ ] **Step 4: Apply and test migration**

Run: `.venv/bin/python -m alembic -c task_server/api_testing/migrations/alembic.ini upgrade head && .venv/bin/python -m pytest tests/api_testing/test_migrations.py -q`

Expected: Alembic reaches `0005`; PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/models/execution.py task_server/api_testing/migrations/versions/0005_execution_lineage.py tests/api_testing/test_migrations.py
git commit -m "Add API execution lineage"
```

### Task 2: Support batch debug without baseline mutation

**Files:**
- Modify: `task_server/api_testing/contracts/case.py`
- Modify: `task_server/api_testing/repositories/execution_repository.py`
- Modify: `task_server/api_testing/services/execution_service.py`
- Modify: `task_server/api_testing/http.py`
- Modify: `tests/api_testing/test_execution_service.py`
- Modify: `tests/api_testing/test_http_contract.py`

**Interfaces:**
- Extends: `POST /executions` with `mode=DEBUG`, multiple `case_version_ids`, optional `suite_version_id`, and non-secret `variable_overrides`.

- [ ] **Step 1: Write failing batch-debug tests**

```python
def test_batch_debug_creates_evidence_without_baselines(execution_service, case_versions):
    execution = execution_service.submit({
        "mode": "DEBUG",
        "case_version_ids": [item.id for item in case_versions],
        "environment_revision_id": case_versions[0].environment_revision_id,
        "variable_overrides": {"page_size": 10},
    }, "admin", "batch-debug-1")
    execution_service.run(execution.id)
    assert execution_service.get(execution.id).summary["total"] == len(case_versions)
    assert execution_service.list_created_baselines(execution.id) == ()


def test_debug_rejects_secret_override(execution_service, case_version):
    with pytest.raises(ExecutionConflictError, match="secret"):
        execution_service.submit(debug_request(case_version, {"ZXBToken": "plaintext"}), "admin", "secret-override")
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/api_testing/test_execution_service.py tests/api_testing/test_http_contract.py -q`

Expected: FAIL on batch mode/override behavior.

- [ ] **Step 3: Implement batch submission and validation**

Require 1-100 exact case versions, one environment revision, same owner/project/source scope, and non-secret overrides only. Store overrides in the execution snapshot after sanitization. Reuse suite scheduling for a suite draft; use a single parallel level for a flat case list.

- [ ] **Step 4: Run execution and HTTP tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_execution_service.py tests/api_testing/test_http_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/contracts/case.py task_server/api_testing/repositories/execution_repository.py task_server/api_testing/services/execution_service.py task_server/api_testing/http.py tests/api_testing/test_execution_service.py tests/api_testing/test_http_contract.py
git commit -m "Add API batch debugging"
```

### Task 3: Enforce exact baseline adoption

**Files:**
- Modify: `task_server/api_testing/repositories/case_repository.py`
- Modify: `task_server/api_testing/services/case_service.py`
- Modify: `tests/api_testing/test_case_service.py`

**Interfaces:**
- Produces: `CaseService.baseline_eligibility(case_version_id, environment_revision_id, actor) -> BaselineEligibility`.
- Tightens: `CaseService.adopt_baseline()`.

- [ ] **Step 1: Write failing gate tests**

```python
def test_baseline_requires_exact_passed_versions(case_service, passed_debug, newer_case_version):
    with pytest.raises(BaselineGateError, match="用例版本"):
        case_service.adopt_baseline(
            newer_case_version.id,
            passed_debug.environment_revision_id,
            passed_debug.execution_case_id,
            "admin",
        )


def test_broken_or_skipped_debug_cannot_be_adopted(case_service, broken_debug):
    eligibility = case_service.baseline_eligibility(
        broken_debug.case_version_id,
        broken_debug.environment_revision_id,
        "admin",
    )
    assert eligibility.eligible is False
    assert eligibility.code == "debug_not_passed"
```

- [ ] **Step 2: Verify tests expose current loose behavior**

Run: `.venv/bin/python -m pytest tests/api_testing/test_case_service.py -q`

Expected: at least one new test FAILS before the stricter gate.

- [ ] **Step 3: Centralize eligibility and adopt through it**

Check exact case version, endpoint/source revision, environment revision, owner, execution mode `DEBUG`, child status `PASSED`, assertion completion, no unresolved variables, and no missing secret evidence. Return structured Chinese reason codes for UI; `adopt_baseline()` calls the same function inside its transaction.

- [ ] **Step 4: Run case tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_case_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/repositories/case_repository.py task_server/api_testing/services/case_service.py tests/api_testing/test_case_service.py
git commit -m "Enforce exact API baseline evidence"
```

### Task 4: Add explicit regression scopes

**Files:**
- Modify: `task_server/api_testing/repositories/execution_repository.py`
- Modify: `task_server/api_testing/services/execution_service.py`
- Modify: `task_server/api_testing/http.py`
- Modify: `tests/api_testing/test_execution_service.py`
- Modify: `tests/api_testing/test_http_contract.py`

**Interfaces:**
- Extends: `POST /regressions` with exactly one scope: `task_id`, `module_path`, or `suite_version_id`.
- Produces: preflight response listing runnable and blocked items before enqueue.

- [ ] **Step 1: Write failing scope tests**

```python
def test_module_regression_reports_blocked_baselines(execution_service, module_baselines):
    preview = execution_service.preview_regression({
        "project_id": module_baselines.project_id,
        "module_path": "家用业务/app接口/我的/我的收藏",
        "environment_revision_id": module_baselines.environment_revision_id,
    }, "admin")
    assert preview.total == 3
    assert preview.runnable == 2
    assert preview.blocked[0].reason_code == "baseline_needs_revalidation"
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/api_testing/test_execution_service.py tests/api_testing/test_http_contract.py -q`

Expected: FAIL because previews and explicit scopes are absent.

- [ ] **Step 3: Implement preview then submit**

The preview and submit methods must share one resolver so counts cannot diverge. Submission requires the preview fingerprint and rejects stale previews. Never silently omit blocked baselines.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_execution_service.py tests/api_testing/test_http_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/repositories/execution_repository.py task_server/api_testing/services/execution_service.py task_server/api_testing/http.py tests/api_testing/test_execution_service.py tests/api_testing/test_http_contract.py
git commit -m "Add scoped API baseline regressions"
```

### Task 5: Add lineage-preserving reruns

**Files:**
- Modify: `task_server/api_testing/repositories/execution_repository.py`
- Modify: `task_server/api_testing/services/execution_service.py`
- Modify: `task_server/api_testing/http.py`
- Modify: `tests/api_testing/test_execution_service.py`
- Modify: `tests/api_testing/test_http_contract.py`

**Interfaces:**
- Produces: `ExecutionService.rerun(execution_id, reason, selected_child_ids, actor, idempotency_key)`.
- Produces: `POST /executions/{execution_id}/rerun`.

- [ ] **Step 1: Write failing rerun tests**

```python
def test_failed_only_rerun_keeps_parent_snapshot(execution_service, mixed_execution):
    rerun = execution_service.rerun(mixed_execution.id, "FAILED_ONLY", (), "admin", "rerun-1")
    assert rerun.parent_execution_id == mixed_execution.id
    assert rerun.root_execution_id == mixed_execution.id
    assert rerun.requested_case_ids == mixed_execution.failed_and_broken_case_version_ids
    assert rerun.request_snapshot["environment_revision_id"] == mixed_execution.request_snapshot["environment_revision_id"]


def test_rerun_does_not_overwrite_parent_summary(execution_service, mixed_execution):
    original = dict(mixed_execution.summary)
    rerun = execution_service.rerun(mixed_execution.id, "FAILED_ONLY", (), "admin", "rerun-2")
    execution_service.run(rerun.id)
    assert execution_service.get(mixed_execution.id).summary == original
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/api_testing/test_execution_service.py tests/api_testing/test_http_contract.py -q`

Expected: FAIL because rerun is absent.

- [ ] **Step 3: Implement rerun selection and lineage**

Allow `FAILED_ONLY`, `BROKEN_ONLY`, or explicit `SELECTED` children that belong to the parent. Copy exact original snapshot inputs for selected nodes, create a new idempotent execution, and enqueue normally. Reject non-terminal parents and empty selections.

- [ ] **Step 4: Run execution and HTTP tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_execution_service.py tests/api_testing/test_http_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/repositories/execution_repository.py task_server/api_testing/services/execution_service.py task_server/api_testing/http.py tests/api_testing/test_execution_service.py tests/api_testing/test_http_contract.py
git commit -m "Add failed API execution reruns"
```

### Task 6: Add batch debug and rerun UI

**Files:**
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/api/client.ts`
- Modify: `api-testing-ui/src/stores/executions.ts`
- Modify: `api-testing-ui/src/components/DebugDrawer.vue`
- Modify: `api-testing-ui/src/components/ExecutionConsole.vue`
- Modify: `api-testing-ui/src/components/ReportSummary.vue`
- Modify: `api-testing-ui/src/views/WorkbenchView.vue`
- Modify: `api-testing-ui/src/views/ReportsView.vue`
- Modify: `api-testing-ui/src/stores/executions.spec.ts`
- Modify: `api-testing-ui/src/components/DebugDrawer.spec.ts`
- Modify: `api-testing-ui/src/components/ReportSummary.spec.ts`

**Interfaces:**
- Consumes: batch debug, regression preview/submit, baseline eligibility, and rerun routes.
- Produces: selected-case batch drawer, explicit adoption eligibility, and rerun count/action.

- [ ] **Step 1: Write failing UI tests**

```ts
it('does not show baseline adoption for broken batch results', async () => {
  const wrapper = mount(DebugDrawer, { props: { execution: brokenBatchFixture } })
  expect(wrapper.find('[data-action="adopt-baseline"]').exists()).toBe(false)
  expect(wrapper.text()).toContain('平台或环境异常')
})


it('shows exact rerun count and preserves original totals', async () => {
  const wrapper = mount(ReportSummary, { props: { report: mixedReportFixture } })
  expect(wrapper.get('[data-action="rerun-failed"]').text()).toContain('重跑 3 条')
  expect(wrapper.text()).toContain('原执行')
})
```

- [ ] **Step 2: Verify tests fail**

Run: `npm --prefix api-testing-ui test -- --run src/components/DebugDrawer.spec.ts src/components/ReportSummary.spec.ts src/stores/executions.spec.ts`

Expected: FAIL.

- [ ] **Step 3: Implement progressive controls**

Use the existing drawer and console rather than new pages. Show batch children in a stable left list, selected child detail on the right, and one footer with “修订”, eligible “采纳为基线”, and “重跑 N 条”. Stop SSE/polling at terminal state and retain scroll/selection.

- [ ] **Step 4: Run frontend tests/build/visual checks**

Run:

```bash
npm --prefix api-testing-ui test -- --run
npm --prefix api-testing-ui run build
node tests/api_testing_ui_visual_check.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api-testing-ui/src/api/contracts.ts api-testing-ui/src/api/client.ts api-testing-ui/src/stores/executions.ts api-testing-ui/src/components/DebugDrawer.vue api-testing-ui/src/components/ExecutionConsole.vue api-testing-ui/src/components/ReportSummary.vue api-testing-ui/src/views/WorkbenchView.vue api-testing-ui/src/views/ReportsView.vue api-testing-ui/src/stores/executions.spec.ts api-testing-ui/src/components/DebugDrawer.spec.ts api-testing-ui/src/components/ReportSummary.spec.ts
git commit -m "Add API batch and rerun workflows"
```

### Task 7: Verify M2 end to end

**Files:**
- Modify: `tests/api_testing_e2e.spec.mjs`
- Modify: `CODEX_STATE.md`

- [ ] **Step 1: Extend Playwright coverage**

Add one batch debug with passed/failed/broken fixtures, assert only passed exact versions are eligible, submit a scoped regression, rerun failed/broken children, and verify parent counts remain unchanged.

- [ ] **Step 2: Run the roadmap milestone gate**

Expected: all commands exit `0`.

- [ ] **Step 3: Deploy and verify a real failed rerun**

Use a controlled assertion failure rather than corrupting credentials. Confirm original execution remains failed, rerun is a separate execution, passed children are not selected, and both reports remain reachable.

- [ ] **Step 4: Record and commit evidence**

```bash
git add tests/api_testing_e2e.spec.mjs CODEX_STATE.md
git commit -m "Verify API execution workflows"
```
