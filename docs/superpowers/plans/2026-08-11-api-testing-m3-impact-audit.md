# API Testing M3 Contract Impact and Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show deterministic source-change impact before activation, provide useful version comparisons, retain sanitized audit history, and complete M0-M3 acceptance.

**Architecture:** Extend source diff processing with deterministic impact records, store generic API testing audit events in migration `0006`, and add comparison services that read immutable versions. AI may summarize deterministic results but cannot change impact severity or activate revisions.

**Tech Stack:** Python 3.10, SQLAlchemy 2, Alembic, PostgreSQL JSONB, Vue 3, Pinia, TypeScript, Vitest, Playwright.

## Global Constraints

- Source refresh stays preview-first and manual.
- Impact severity is deterministic: `BREAKING`, `RISKY`, or `SAFE`.
- AI summary is optional and cannot mutate severity or activate a revision.
- Source activation never updates cases, suites, or baselines in place.
- Version comparison never returns secret plaintext.
- Audit history is append-only through service APIs and stores sanitized summaries.

---

### Task 1: Add impact and audit migration 0006

**Files:**
- Create: `task_server/api_testing/models/audit.py`
- Modify: `task_server/api_testing/models/source.py`
- Modify: `task_server/api_testing/models/__init__.py`
- Create: `task_server/api_testing/migrations/versions/0006_impact_audit.py`
- Modify: `tests/api_testing/test_migrations.py`

**Interfaces:**
- Produces: `ApiSourceImpact`, `ApiAuditEvent`, and source preview impact-confirmation fields.

- [ ] **Step 1: Write failing schema tests**

```python
def test_0006_creates_impact_and_audit_tables(migrated_connection):
    tables = set(inspect(migrated_connection).get_table_names())
    assert {"api_source_impacts", "api_audit_events"} <= tables


def test_audit_payload_is_json_and_actor_scoped(migrated_connection):
    columns = {item["name"]: item for item in inspect(migrated_connection).get_columns("api_audit_events")}
    assert "summary" in columns
    assert "owner_id" in columns
```

- [ ] **Step 2: Verify migration tests fail**

Run: `.venv/bin/python -m pytest tests/api_testing/test_migrations.py -q`

Expected: FAIL because `0006` tables are absent.

- [ ] **Step 3: Add models and append-only schema**

`ApiSourceImpact` stores preview/diff/endpoint/object type/object ID/severity/reason codes. `ApiAuditEvent` stores project/workspace/actor/action/object type/object ID/version ID/sanitized summary/time. Do not add update or delete service methods for audit events.

- [ ] **Step 4: Apply migration and run tests**

Run: `.venv/bin/python -m alembic -c task_server/api_testing/migrations/alembic.ini upgrade head && .venv/bin/python -m pytest tests/api_testing/test_migrations.py -q`

Expected: Alembic reaches `0006`; PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/models/audit.py task_server/api_testing/models/source.py task_server/api_testing/models/__init__.py task_server/api_testing/migrations/versions/0006_impact_audit.py tests/api_testing/test_migrations.py
git commit -m "Add API impact and audit schema"
```

### Task 2: Classify deterministic contract changes

**Files:**
- Create: `task_server/api_testing/contract_impact.py`
- Create: `tests/api_testing/test_contract_impact.py`

**Interfaces:**
- Produces: `classify_endpoint_change(before, after, referenced_paths) -> ImpactClassification`.

- [ ] **Step 1: Write failing severity tests**

```python
@pytest.mark.parametrize((before, after, code), [
    (endpoint("GET", "/favorites"), None, "endpoint_removed"),
    (endpoint("GET", "/favorites"), endpoint("POST", "/favorites"), "method_changed"),
    (endpoint_with_optional("page"), endpoint_with_required("page"), "required_parameter_added"),
])
def test_breaking_changes(before, after, code):
    result = classify_endpoint_change(before, after, referenced_paths=set())
    assert result.severity == "BREAKING"
    assert code in result.reason_codes


def test_description_only_change_is_safe():
    result = classify_endpoint_change(endpoint(description="old"), endpoint(description="new"), set())
    assert result.severity == "SAFE"
```

- [ ] **Step 2: Verify tests fail due to missing classifier**

Run: `.venv/bin/python -m pytest tests/api_testing/test_contract_impact.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement canonical schema comparison**

Compare stable key, method, path, required parameters, parameter types, request schema, response statuses, and response schema. Removing an assertion-referenced response path is `BREAKING`; unknown structural compatibility is `RISKY`; descriptions/examples and additive optional fields are `SAFE`. Sort reason codes for deterministic storage and tests.

- [ ] **Step 4: Run classifier tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_contract_impact.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/contract_impact.py tests/api_testing/test_contract_impact.py
git commit -m "Classify API contract impact"
```

### Task 3: Resolve affected cases, suites, and baselines

**Files:**
- Create: `task_server/api_testing/repositories/impact_repository.py`
- Create: `task_server/api_testing/services/impact_service.py`
- Modify: `task_server/api_testing/services/source_service.py`
- Modify: `task_server/api_testing/services/apifox_service.py`
- Create: `tests/api_testing/test_impact_service.py`
- Modify: `tests/api_testing/test_source_service.py`
- Modify: `tests/api_testing/test_apifox_service.py`

**Interfaces:**
- Produces: `ImpactService.analyze_preview(preview_id, actor) -> ImpactSummary`.
- Produces: `ImpactService.confirm(preview_id, impact_hash, actor)`.

- [ ] **Step 1: Write failing transitive-impact tests**

```python
def test_endpoint_change_maps_to_cases_suites_and_baselines(impact_service, changed_endpoint):
    summary = impact_service.analyze_preview(changed_endpoint.preview_id, "admin")
    assert summary.endpoints == 1
    assert summary.cases == 2
    assert summary.suite_versions == 1
    assert summary.baselines == 1
    assert summary.highest_severity == "BREAKING"


def test_source_activation_requires_current_impact_confirmation(source_service, preview):
    with pytest.raises(SourcePreviewStateError, match="影响"):
        source_service.activate_preview(preview.id, "admin")
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/api_testing/test_impact_service.py tests/api_testing/test_source_service.py tests/api_testing/test_apifox_service.py -q`

Expected: FAIL because impacts and confirmation are absent.

- [ ] **Step 3: Implement deterministic mapping and activation gate**

Map `ApiCaseVersion.endpoint_id`, `ApiSuiteNode.case_version_id`, and `ApiBaseline.case_version_id`. Calculate an `impact_hash` from preview hash plus sorted impact records. Activation requires confirmation of that exact hash; a refreshed preview invalidates prior confirmation. Do not mutate any affected asset.

- [ ] **Step 4: Run impact/source tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_impact_service.py tests/api_testing/test_source_service.py tests/api_testing/test_apifox_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/repositories/impact_repository.py task_server/api_testing/services/impact_service.py task_server/api_testing/services/source_service.py task_server/api_testing/services/apifox_service.py tests/api_testing/test_impact_service.py tests/api_testing/test_source_service.py tests/api_testing/test_apifox_service.py
git commit -m "Report API source revision impact"
```

### Task 4: Add sanitized audit recording

**Files:**
- Create: `task_server/api_testing/repositories/audit_repository.py`
- Create: `task_server/api_testing/services/audit_service.py`
- Modify: `task_server/api_testing/services/source_service.py`
- Modify: `task_server/api_testing/services/environment_service.py`
- Modify: `task_server/api_testing/services/case_service.py`
- Modify: `task_server/api_testing/services/suite_service.py`
- Modify: `task_server/api_testing/services/execution_service.py`
- Create: `tests/api_testing/test_audit_service.py`

**Interfaces:**
- Produces: `AuditService.record(action, object_type, object_id, actor, summary, project_id, workspace_id=None, version_id=None)`.
- Produces: `AuditService.list(project_id, actor, filters, limit, cursor)`.

- [ ] **Step 1: Write failing redaction and event tests**

```python
def test_audit_redacts_nested_secrets(audit_service):
    event = audit_service.record(
        "environment.updated", "environment", "env-1", "admin",
        {"headers": {"Authorization": "Bearer secret"}, "name": "生产环境"},
        "project-1",
    )
    assert event.summary["headers"]["Authorization"] == "***"
    assert event.summary["name"] == "生产环境"


def test_execution_rerun_records_parent_and_child_ids(execution_service, audit_repository, failed_execution):
    child = execution_service.rerun(failed_execution.id, "FAILED_ONLY", (), "admin", "audit-rerun")
    event = audit_repository.latest("execution.rerun")
    assert event.summary == {"parent_execution_id": failed_execution.id, "execution_id": child.id, "reason": "FAILED_ONLY"}
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/api_testing/test_audit_service.py -q`

Expected: FAIL because audit service is absent.

- [ ] **Step 3: Implement one sanitizer and transactional audit writes**

Use an allowlist for summary fields plus recursive redaction for keys matching authorization, cookie, token, secret, password, key, and credential. Write the audit row in the same transaction as the domain change. A domain rollback must also roll back its audit event.

- [ ] **Step 4: Run audit and affected service tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_audit_service.py tests/api_testing/test_source_service.py tests/api_testing/test_environment_service.py tests/api_testing/test_case_service.py tests/api_testing/test_suite_service.py tests/api_testing/test_execution_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/repositories/audit_repository.py task_server/api_testing/services/audit_service.py task_server/api_testing/services/source_service.py task_server/api_testing/services/environment_service.py task_server/api_testing/services/case_service.py task_server/api_testing/services/suite_service.py task_server/api_testing/services/execution_service.py tests/api_testing/test_audit_service.py
git commit -m "Audit API testing asset changes"
```

### Task 5: Add version comparison services and HTTP resources

**Files:**
- Create: `task_server/api_testing/version_compare.py`
- Modify: `task_server/api_testing/http.py`
- Create: `tests/api_testing/test_version_compare.py`
- Modify: `tests/api_testing/test_http_contract.py`

**Interfaces:**
- Produces: `compare_versions(kind, left_id, right_id, actor) -> VersionComparison`.
- Produces: `GET /comparisons?kind=&left_id=&right_id=`, impact, confirmation, and audit routes.

- [ ] **Step 1: Write failing comparison and route tests**

```python
def test_environment_comparison_exposes_fingerprint_change_not_secret(compare_service, environment_versions):
    result = compare_service.compare("environment", environment_versions.left.id, environment_versions.right.id, "admin")
    assert result.changes["ZXBToken"] == {"secret_changed": True}
    assert "secret-value" not in str(result)


def test_audit_route_is_owner_scoped(api_client, foreign_project):
    response = api_client.get(f"/api/api-testing/v1/audit-events?project_id={foreign_project.id}")
    assert response.status_code == 404
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/api_testing/test_version_compare.py tests/api_testing/test_http_contract.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement structured comparisons and HTTP routes**

Support source, environment, case, suite, and execution kinds. Return display names, timestamps, semantic fields, and sanitized structural changes. Reject cross-owner/project comparisons. Add `GET /source-previews/{id}/impacts`, `POST /source-previews/{id}/impact-confirmation`, and cursor-based `GET /audit-events`.

- [ ] **Step 4: Run comparison/HTTP tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_version_compare.py tests/api_testing/test_http_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/version_compare.py task_server/api_testing/http.py tests/api_testing/test_version_compare.py tests/api_testing/test_http_contract.py
git commit -m "Expose API impact and version history"
```

### Task 6: Build impact, comparison, and audit UI

**Files:**
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/api/client.ts`
- Modify: `api-testing-ui/src/stores/assets.ts`
- Create: `api-testing-ui/src/components/ImpactReview.vue`
- Create: `api-testing-ui/src/components/VersionComparison.vue`
- Create: `api-testing-ui/src/components/AuditTimeline.vue`
- Modify: `api-testing-ui/src/views/AssetsView.vue`
- Modify: `api-testing-ui/src/views/SettingsView.vue`
- Create: `api-testing-ui/src/components/ImpactReview.spec.ts`
- Create: `api-testing-ui/src/components/VersionComparison.spec.ts`
- Create: `api-testing-ui/src/components/AuditTimeline.spec.ts`

**Interfaces:**
- Consumes: impact, confirmation, comparison, and audit resources.
- Produces: pre-activation impact review, structured diff drawer, and sanitized audit timeline.

- [ ] **Step 1: Write failing UI tests**

```ts
it('requires impact confirmation before source activation', async () => {
  const wrapper = mount(ImpactReview, { props: { preview: breakingPreviewFixture } })
  expect(wrapper.get('[data-action="activate-source"]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-action="confirm-impact"]').trigger('click')
  expect(wrapper.emitted('confirmed')).toBeTruthy()
})


it('never renders secret values in environment comparison', () => {
  const wrapper = mount(VersionComparison, { props: { comparison: secretComparisonFixture } })
  expect(wrapper.text()).toContain('密钥已变化')
  expect(wrapper.text()).not.toContain('secret-value')
})
```

- [ ] **Step 2: Verify tests fail**

Run: `npm --prefix api-testing-ui test -- --run src/components/ImpactReview.spec.ts src/components/VersionComparison.spec.ts src/components/AuditTimeline.spec.ts`

Expected: FAIL.

- [ ] **Step 3: Implement progressive disclosure**

Show breaking/risky/safe counts first, then affected business names grouped by endpoint. Keep full JSON schema diff in a technical drawer. Put audit history under existing configuration/history surfaces, not a new first-level menu. Use Lucide icons and Chinese copy; do not use raw IDs as titles.

- [ ] **Step 4: Run frontend tests, build, and visuals**

Run:

```bash
npm --prefix api-testing-ui test -- --run
npm --prefix api-testing-ui run build
node tests/api_testing_ui_visual_check.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api-testing-ui/src/api/contracts.ts api-testing-ui/src/api/client.ts api-testing-ui/src/stores/assets.ts api-testing-ui/src/components/ImpactReview.vue api-testing-ui/src/components/VersionComparison.vue api-testing-ui/src/components/AuditTimeline.vue api-testing-ui/src/views/AssetsView.vue api-testing-ui/src/views/SettingsView.vue api-testing-ui/src/components/ImpactReview.spec.ts api-testing-ui/src/components/VersionComparison.spec.ts api-testing-ui/src/components/AuditTimeline.spec.ts
git commit -m "Add API change impact review"
```

### Task 7: Complete M0-M3 acceptance

**Files:**
- Modify: `tests/api_testing_e2e.spec.mjs`
- Modify: `tests/api_testing_ui_visual_check.js`
- Modify: `CODEX_STATE.md`

- [ ] **Step 1: Add end-to-end source-impact coverage**

Import revision A, create case/baseline/suite references, preview revision B with a breaking response-field removal, verify affected counts and disabled activation, confirm the current impact hash, activate B, and verify existing assets remain unchanged but marked for revalidation.

- [ ] **Step 2: Run the complete roadmap gate twice**

Run the roadmap milestone gate, restart local PostgreSQL/Redis containers and the test application, then run it a second time.

Expected: both runs exit `0`, proving migrations and setup are repeatable.

- [ ] **Step 3: Deploy and run production acceptance**

Verify Task health, API readiness, and AI Gateway health. Execute the real “我的收藏” task, one sequential suite with explicit variable mapping, one controlled batch debug, one failed-only rerun, and one manual source update preview without activation. Follow every execution to terminal state and inspect reports.

- [ ] **Step 4: Check secrets and audit evidence**

Search API responses, logs, report HTML/JSON, and audit rows for known token prefixes. Expected: no plaintext secrets; only masks/fingerprints. Confirm audit events exist for source confirmation, environment update, baseline adoption, suite publication, execution, cancellation if used, and rerun.

- [ ] **Step 5: Update state and commit final evidence**

Record test counts, migration head, production execution IDs, terminal statuses, impact counts, screenshots, and residual risks in `CODEX_STATE.md`.

```bash
git add tests/api_testing_e2e.spec.mjs tests/api_testing_ui_visual_check.js CODEX_STATE.md
git commit -m "Verify API testing Phase 2 completion"
```
