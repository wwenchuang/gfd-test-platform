# Configurable Test Applications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make platform-configured applications and their business lines the authoritative identity for UI/API test creation, history, execution, and notifications.

**Architecture:** Keep application package names as stable IDs in the existing task application configuration and store application snapshots alongside case versions. Resolve business lines with an explicit application package at every boundary; legacy constants are fallback readers only. Both frontends load one application catalog and derive business choices from the currently selected application.

**Tech Stack:** Python 3 services and pytest, vanilla JavaScript task manager, Vue 3/TypeScript/Pinia API workbench, Vitest, Chromium smoke checks.

## Global Constraints

- Application and business line display names must be Chinese-facing platform configuration, not hidden English IDs.
- `com.kfb.model`, `home`, and `shared` remain read-compatible but cannot override saved configuration.
- A disabled application or business line remains readable in history and cannot be selected for a new save.
- No real Runner, Sonic, or Feishu side effect is permitted during automated verification.
- Preserve existing API case JSON persistence; do not add a database migration when `request_template` can carry the fields.

---

### Task 1: Application Catalog And Per-App Business Resolution

**Files:**
- Modify: `task_server/services/business_line_service.py`
- Modify: `task_server/services/job_service.py`
- Modify: `task_server/services/sonic_service.py`
- Modify: `task_server/router.py`
- Test: `tests/test_business_line_service.py`

**Interfaces:**
- Produces: `configured_test_applications(include_disabled=False) -> list`, `configured_test_application(package, include_disabled=True) -> dict`, `test_application_name(package, snapshot_name='') -> str`.
- Produces: `configured_business_lines(app_package, include_disabled=False) -> list` without cross-application fallback.

- [ ] Write tests proving configured names override legacy names, disabled apps remain resolvable but are excluded from creation, and non-primary apps without business configuration receive no `家用/共享` fallback.
- [ ] Run `.venv/bin/python -m pytest tests/test_business_line_service.py -q` and confirm the new assertions fail for the fixed primary-app behavior.
- [ ] Implement catalog normalization, Chinese application-name validation, enabled-state persistence, first-run legacy fallback, and explicit per-app business lookup.
- [ ] Run `.venv/bin/python -m pytest tests/test_business_line_service.py -q` and confirm all tests pass.

### Task 2: UI Automation Creation And Case Editing

**Files:**
- Modify: `task-manager.html`
- Modify: `js/app.js`
- Modify: `js/agent-status.js`
- Modify: `js/utils.js`
- Modify: `task_server/services/case_service.py`
- Modify: `css/app.css`
- Test: `tests/test_case_business.py`
- Test: `tests/frontend_static_checks.py`
- Test: `tests/visual_smoke_check.js`

**Interfaces:**
- Consumes: application catalog and per-app business lookup from Task 1.
- Produces: required `app_package` and `business` payloads for UI generation and per-case business updates.

- [ ] Add failing service and browser assertions for selecting a configured application, filtering its business lines, clearing incompatible business on app switch, and preserving application identity in generated/history views.
- [ ] Run focused Python and Chromium checks and confirm they fail on the fixed `智小白3D` field and primary-app business validation.
- [ ] Replace the fixed application field with a configured selector plus readonly package detail; add disabled/empty states and application status controls in the configuration editor.
- [ ] Pass the case application package into every UI business validation and display helper.
- [ ] Re-run focused checks until the complete UI create/edit loop passes.

### Task 3: API Case Application Persistence And Editor

**Files:**
- Modify: `task_server/api_testing/contracts/case.py`
- Modify: `task_server/api_testing/repositories/case_repository.py`
- Modify: `task_server/api_testing/services/case_service.py`
- Modify: `task_server/api_testing/services/basic_case_service.py`
- Modify: `api-testing-ui/src/api/contracts.ts`
- Create: `api-testing-ui/src/utils/testApplications.ts`
- Modify: `api-testing-ui/src/utils/businessLines.ts`
- Modify: `api-testing-ui/src/stores/cases.ts`
- Modify: `api-testing-ui/src/components/CaseEditor.vue`
- Modify: `api-testing-ui/src/main.ts`
- Test: `tests/api_testing/test_case_contract.py`
- Test: `tests/api_testing/test_case_service.py`
- Test: `api-testing-ui/src/components/CaseEditor.spec.ts`
- Test: `api-testing-ui/src/utils/testApplications.spec.ts`

**Interfaces:**
- Produces: API case fields `app_package`, `app_name`, and `business` in versions and drafts.
- Consumes: `business_line_id(value, app_package=..., require_active=True)`.

- [ ] Add failing contract/repository/component tests for saving app identity, loading legacy versions, selecting configured apps, and app-specific business choices.
- [ ] Run focused pytest and Vitest targets and confirm failures are caused by missing application fields and the fixed editor label.
- [ ] Persist application fields inside `request_template`, expose them through version/baseline views, and validate business against the selected application.
- [ ] Implement the Vue application catalog, required selector, unavailable-history warning, and app-switch business reset.
- [ ] Re-run focused tests until API create, new-version, reload, and local validation paths pass.

### Task 4: Execution Snapshots, History, Baselines, And Notifications

**Files:**
- Modify: `task_server/api_testing/services/execution_service.py`
- Modify: `task_server/api_testing/services/notification_service.py`
- Modify: `task_server/services/notification_presentation.py`
- Modify: `task_server/services/sonic_service.py`
- Modify: `api-testing-ui/src/views/BaselinesView.vue`
- Modify: `api-testing-ui/src/views/TasksView.vue`
- Modify: `api-testing-ui/src/views/ScheduledJobsView.vue`
- Modify: `api-testing-ui/src/components/CaseListPanel.vue`
- Test: `tests/api_testing/test_execution_service.py`
- Test: `tests/api_testing/test_notification_service.py`
- Test: `tests/test_notification_presentation.py`
- Test: `tests/test_sonic_integration.py`

**Interfaces:**
- Consumes: case version application snapshots and configured catalog.
- Produces: application-aware execution snapshots and Chinese UI/API notification titles.

- [ ] Add failing tests for one-app, mixed-app, renamed-app, disabled historical app, and same business ID under different apps.
- [ ] Run focused notification/execution tests and confirm current global `com.kfb.model` lookup fails them.
- [ ] Carry application fields into execution case snapshots and resolve each business with its own package.
- [ ] Render application and business in list, baseline, task, schedule, report, and Feishu card contexts without conflating UI and API test types.
- [ ] Re-run all focused tests and verify no card contains an internal package/ID when a Chinese configured name exists.

### Task 5: Full Gate, Deployment, And Flow Audit

**Files:**
- Modify: `CODEX_STATE.md`
- Modify: `docs/task-platform-ux-audit-2026-08-26.md`
- Generated: `api-test/index.html`, `api-test/assets/index-*.js`, `api-test/assets/index-*.css`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: deployable static assets, verification evidence, and a follow-up defect list separated into fixed/deferred/unverified.

- [ ] Run focused pytest/Vitest suites, `tests/run_api_testing_gate.sh`, backend/frontend static checks, required `py_compile`, Vue build, both Chromium smoke checks, and `git diff --check`.
- [ ] Update state and audit records with exact checks, residual risks, and side effects not exercised.
- [ ] Commit all relevant source/docs/generated frontend assets while excluding `output/`, push `main`, and confirm local/remote commit IDs match.
- [ ] Through the Huawei bastion select `qa.test.sonic-00.txsh`, retry Git fetch/pull on transient failures, run `bash deploy/update-main-server.sh`, and confirm the deployed commit and health URLs.
- [ ] In Chrome, exercise application configuration, UI/API case creation/edit/history/baseline/task/report flows, inspect visible loading delays and upstream/downstream continuity, then fix and redeploy any reproducible defect within scope.
