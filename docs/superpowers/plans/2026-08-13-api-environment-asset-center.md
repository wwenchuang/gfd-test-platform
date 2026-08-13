# API Environment Asset Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a project-scoped environment asset center with stable environment identities, revision history, archive/restore operations, and reliable workbench navigation.

**Architecture:** Extend the existing `ApiEnvironment` and `ApiEnvironmentRevision` model instead of introducing another environment store. Add read-model repository/service contracts for asset summaries and revision history, expose them through the existing API testing dispatcher, then reshape `SettingsView` around project and environment lists while reusing the current revision editor and Apifox sync workflow.

**Tech Stack:** Python 3.10, SQLAlchemy 2, PostgreSQL, Vue 3, Pinia, TypeScript, Vitest, Lucide.

## Global Constraints

- Environment identity is project-scoped and stable; edits create revisions.
- Interface revisions are provenance only and must not filter the environment asset list.
- Delete means archive; historical references remain resolvable.
- Apifox access remains manual.
- Feishu robot configuration remains project-scoped.
- Scheduled execution is not implemented in this plan; future schedules reference task or baseline assets and own only a notification toggle.
- Do not refactor `task_server/router.py` or modify historical YAML files.

---

### Task 1: Environment asset read and lifecycle contracts

**Files:**
- Modify: `task_server/api_testing/contracts/environment.py`
- Modify: `task_server/api_testing/repositories/environment_repository.py`
- Modify: `task_server/api_testing/services/environment_service.py`
- Modify: `tests/api_testing/test_environment_service.py`

**Interfaces:**
- Produces: `EnvironmentAssetSummary`, `EnvironmentRevisionSummary`.
- Produces: `EnvironmentService.list_environments(project_id, actor_id, status)`.
- Produces: `EnvironmentService.list_revisions(environment_id)`.
- Produces: `EnvironmentService.archive(environment_id, actor_id)` and `restore(environment_id, actor_id)`.

- [ ] Write tests that create two projects and prove a project query returns only its stable environment assets, including counts and active revision metadata.
- [ ] Run the focused tests and confirm they fail because the list/lifecycle methods do not exist.
- [ ] Add repository queries and immutable summary contracts.
- [ ] Add service ownership validation, status validation, idempotent archive and restore, and revision history ordering.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Environment asset HTTP API

**Files:**
- Modify: `task_server/api_testing/http.py`
- Modify: `tests/api_testing/test_http_routes.py`

**Interfaces:**
- Consumes: environment lifecycle interfaces from Task 1.
- Produces: project-filtered environment list, revision history, archive and restore routes.

- [ ] Write route tests for owner-scoped list, revisions, archive and restore responses.
- [ ] Run the route tests and confirm the new routes return not found.
- [ ] Add the minimal dispatch branches and payload serialization.
- [ ] Re-run route tests and confirm they pass.

### Task 3: Pinia environment asset store

**Files:**
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/stores/setup.ts`
- Modify: `api-testing-ui/src/stores/setup.spec.ts`

**Interfaces:**
- Produces: `environmentAssets`, `environmentHistory`, list filters and lifecycle actions.
- Preserves: existing `loadEnvironmentRevision()` and `saveEnvironment()` editor APIs.

- [ ] Write store tests for project list loading, history loading, archive and restore.
- [ ] Run the store tests and confirm requests/actions are missing.
- [ ] Add typed API contracts and minimal Pinia actions.
- [ ] Re-run the store tests and confirm they pass.

### Task 4: Project-scoped environment asset center

**Files:**
- Create: `api-testing-ui/src/components/EnvironmentAssetList.vue`
- Create: `api-testing-ui/src/components/EnvironmentAssetList.spec.ts`
- Modify: `api-testing-ui/src/views/SettingsView.vue`
- Create: `api-testing-ui/src/views/SettingsView.spec.ts`
- Modify: `api-testing-ui/src/styles/api-testing.css`

**Interfaces:**
- Consumes: setup store environment assets and history.
- Emits: select, edit, archive, restore, sync and enter-workbench commands.

- [ ] Write component tests for project switching, active/archived filters, environment selection, archive/restore and workbench navigation query.
- [ ] Run the tests and confirm they fail because the asset center is absent.
- [ ] Implement the project list, environment asset list, read-only detail, explicit edit mode and revision history.
- [ ] Keep the existing structured editor, but show friendly service labels while preserving stable service keys.
- [ ] Route “前往同步” to the existing asset sync workflow and “进入工作台” with project/source/environment context.
- [ ] Re-run component and workbench tests and confirm they pass.

### Task 5: Verification, state and delivery

**Files:**
- Modify: `CODEX_STATE.md`

- [ ] Run focused backend and frontend tests.
- [ ] Run the full frontend Vitest suite and production build.
- [ ] Run Python compilation, backend and frontend static checks, and `git diff --check`.
- [ ] Review the implementation against every acceptance item in the design spec.
- [ ] Update `CODEX_STATE.md` with implemented behavior, tests and the deferred task/schedule boundary.
- [ ] Commit all scoped changes directly to `main` with a concise message.

