# API Baseline And Feishu Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make API baseline cases visible and reusable, and allow API regression reports to be sent to a configured Feishu group.

**Architecture:** Keep the existing API testing subsystem. Add a baseline read model on top of existing `api_baselines`, add one encrypted per-project Feishu notification channel, and expose both through the current authenticated API adapter. The frontend gets one baseline page, one Feishu settings section, and report send actions.

**Tech Stack:** Python stdlib HTTP adapter, SQLAlchemy/Alembic, existing Feishu service, Vue 3, Pinia, Vitest.

## Global Constraints

- Do not replace the API testing architecture.
- Do not touch Agent/Runner/YAML generation code.
- Keep baseline source of truth in existing API testing tables.
- Feishu webhook must be stored encrypted and only expose configured state plus fingerprint.
- Prefer small, verifiable changes.

---

### Task 1: Baseline List API

**Files:**
- Modify: `task_server/api_testing/contracts/case.py`
- Modify: `task_server/api_testing/repositories/case_repository.py`
- Modify: `task_server/api_testing/services/case_service.py`
- Modify: `task_server/api_testing/http.py`

**Interfaces:**
- Produces: `GET /api/api-testing/v1/baselines?project_id=&source_revision_id=&environment_revision_id=`
- Returns: `{ "baselines": ApiBaselineCaseView[] }`

- [x] Write failing frontend store test expecting baseline list loading.
- [x] Add repository query joining baseline, case, active version, endpoint and environment revision.
- [x] Add service method returning stable baseline case views.
- [x] Add authenticated GET route with project/source/environment ownership checks.
- [x] Run focused tests.

### Task 2: Baseline Center Frontend

**Files:**
- Create: `api-testing-ui/src/stores/baselines.ts`
- Create: `api-testing-ui/src/views/BaselinesView.vue`
- Modify: `api-testing-ui/src/router.ts`
- Modify: `api-testing-ui/src/App.vue`
- Modify: `api-testing-ui/src/api/contracts.ts`

**Interfaces:**
- Produces: baseline page where users can search, select, and add baseline endpoint ranges into the current task.
- Consumes: `useTasksStore.saveSelection`.

- [x] Write failing store test for selecting baselines and computing endpoint IDs.
- [x] Implement baseline store.
- [x] Add page and navigation entry.
- [x] Add batch add-to-task action.
- [x] Run frontend tests.

### Task 3: Feishu Notification Channel

**Files:**
- Create: `task_server/api_testing/models/notification.py`
- Create: `task_server/api_testing/repositories/notification_repository.py`
- Create: `task_server/api_testing/services/notification_service.py`
- Create: `task_server/api_testing/migrations/versions/0004_notifications.py`
- Modify: `task_server/api_testing/models/__init__.py`
- Modify: `task_server/api_testing/http.py`

**Interfaces:**
- Produces: `GET /api/api-testing/v1/notifications/feishu?project_id=`
- Produces: `PUT /api/api-testing/v1/notifications/feishu`
- Produces: `POST /api/api-testing/v1/executions/{execution_id}/notify`

- [x] Write failing store test for notification settings.
- [x] Add encrypted notification model and migration.
- [x] Add service using existing `task_server.services.feishu_service`.
- [x] Add route handlers and error mapping.
- [x] Run backend import checks.

### Task 4: Feishu Settings And Report Send UI

**Files:**
- Create: `api-testing-ui/src/stores/notifications.ts`
- Modify: `api-testing-ui/src/views/SettingsView.vue`
- Modify: `api-testing-ui/src/views/ReportsView.vue`
- Modify: `api-testing-ui/src/stores/executions.ts`
- Modify: `api-testing-ui/src/api/contracts.ts`

**Interfaces:**
- Produces: Feishu Hook configuration in settings.
- Produces: report list/detail send-to-Feishu action.

- [x] Implement notification store.
- [x] Add settings section with encrypted webhook semantics.
- [x] Add report send action and user-facing status.
- [x] Add execution event labels for notification sent/failed.
- [x] Run frontend tests and build.

### Task 6: Baseline Maintenance UX

**Files:**
- Modify: `task_server/api_testing/models/case.py`
- Create: `task_server/api_testing/migrations/versions/0005_baseline_groups.py`
- Modify: `task_server/api_testing/contracts/case.py`
- Modify: `task_server/api_testing/repositories/case_repository.py`
- Modify: `task_server/api_testing/services/case_service.py`
- Modify: `task_server/api_testing/http.py`
- Modify: `api-testing-ui/src/stores/baselines.ts`
- Modify: `api-testing-ui/src/views/BaselinesView.vue`
- Modify: `api-testing-ui/src/styles/app.css`

**Interfaces:**
- Produces: `PUT /api/api-testing/v1/baselines/{baseline_id}`
- Produces: `POST /api/api-testing/v1/baselines/bulk-group`
- Produces: `DELETE /api/api-testing/v1/baselines/{baseline_id}`

- [x] Write failing store tests for group rename and baseline archive.
- [x] Persist platform-managed baseline groups.
- [x] Add owner-scoped group update and archive APIs.
- [x] Add baseline page group editor, edit entry, and archive action.
- [x] Run frontend tests, backend compile, build, and static checks.

### Task 5: Verification And State

**Files:**
- Modify: `CODEX_STATE.md`

- [x] Run `python3 -m py_compile` on touched backend files.
- [x] Run API frontend tests and build.
- [x] Run static checks for changed areas.
- [ ] Update `CODEX_STATE.md`.
- [ ] Commit focused changes on `main`.
