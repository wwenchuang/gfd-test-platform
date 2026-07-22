# API Source and Asset Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace one-off OpenAPI upload as the normal workflow with a secure, read-only Apifox source, immutable revisions, deterministic diffs, asynchronous synchronization, and a usable asset console while preserving existing snapshots and plans.

**Architecture:** Keep orchestration inside the API testing domain. A source service stores masked configuration, an Apifox adapter exports OpenAPI, a synchronization service stages and activates immutable revisions, and a pure diff service computes endpoint and plan impact. Existing snapshot APIs remain compatibility views over the active revision.

**Tech Stack:** Python standard library HTTP/JSON/threading, the repository's atomic JSON storage helpers, vanilla JavaScript/CSS, standard-library `unittest`, and existing static/Playwright checks.

## Global Constraints

- Work in `/Users/wenchuang/Documents/Codex/midscene-task-platform`.
- Never commit an Apifox token, MeterSphere credential, account password, or raw authorization header.
- Token inputs are write-only. Empty updates preserve the current token; only an explicit clear removes it.
- Apifox integration is read-only and uses the official OpenAPI export endpoint. It never writes back to Apifox.
- Keep `/api/api-testing/openapi/import` and legacy snapshot/plan reads working.
- Do not modify, stage, revert, or overwrite the protected user files listed in `AGENTS.md` and `CODEX_STATE.md`.
- Do not push. The user owns push and deployment.
- Every behavior change follows RED, GREEN, REFACTOR and records the focused verification result.

Implementation note: the user owns push/deployment, so Tasks 1-8 are delivered as one cohesive Phase A commit instead of intermediate task commits.

## Task 1: Server-Side API Source Configuration

**Files:**
- Create: `task_server/services/api_source_service.py`
- Test: `tests/api_asset_sync_checks.py`
- Modify: `task_server/config.py`
- Modify: `deploy/midscene.env.example`
- Modify: `package.json`

- [x] Add failing tests for creating/updating an `apifox` source, masked reads, empty-token preservation, explicit token clearing, allowed source types, and interval clamping to 15-1440 minutes.
- [x] Run `python3 tests/api_asset_sync_checks.py -v` and confirm failures are caused by the missing service.
- [x] Implement JSON-backed sources under `API_TESTING_DIR/sources`, merging `APIFOX_*` environment defaults without returning credentials.
- [x] Add `APIFOX_` to startup environment prefixes and document only empty placeholders in `deploy/midscene.env.example`.
- [x] Register the focused suite in `package.json` without weakening existing tests.
- [x] Re-run the focused suite and include the scoped files in the final Phase A commit.

## Task 2: Official Apifox Read-Only Export Adapter

**Files:**
- Create: `task_server/services/apifox_service.py`
- Test: `tests/api_asset_sync_checks.py`

- [x] Add failing contract tests around an injected HTTP opener for URL, method, `Authorization`, `X-Apifox-Api-Version`, export body, timeout, malformed JSON, payload size, and sanitized errors.
- [x] Confirm RED with the focused test command.
- [x] Implement `ApifoxSourceAdapter.fetch_openapi()` against `POST /v1/projects/{projectId}/export-openapi?locale=zh-CN`, requesting all APIs as OpenAPI 3.0 JSON with vendor extensions and an explicit platform user agent; use the current CLI route only as an empty/404/405 compatibility fallback.
- [x] Reject missing project/token, non-2xx responses, non-object JSON, empty `paths`, and responses larger than 20 MiB.
- [x] Ensure tokens and authorization values cannot enter returned or persisted errors.
- [x] Re-run focused tests and include the adapter/tests in the final Phase A commit.

## Task 3: Immutable Assets, Revisions, and Stable Endpoint Identity

**Files:**
- Modify: `task_server/services/api_asset_service.py`
- Test: `tests/api_asset_sync_checks.py`

- [x] Add failing tests for first revision, same-document no-change, stable endpoint keys across schema edits, method/path fallback identity, activation after persistence, and legacy snapshot compatibility.
- [x] Confirm RED.
- [x] Add immutable asset/revision storage and deterministic document hashes.
- [x] Derive endpoint identity in this order: provider endpoint ID, unique `operationId`, normalized `METHOD + path`.
- [x] Keep `endpoint_id` as a compatibility alias and add `endpoint_key`, `endpoint_revision_id`, `source_ref`, and `asset_revision_id`.
- [x] Make current `list_api_snapshots`, `get_api_snapshot`, and `list_api_endpoints` read the active revision when present while retaining legacy files unchanged.
- [x] Re-run focused and existing API asset checks and include them in the final Phase A commit.

## Task 4: Deterministic Diff and Plan Impact

**Files:**
- Create: `task_server/services/api_schema_diff_service.py`
- Modify: `task_server/services/api_test_plan_service.py`
- Test: `tests/api_asset_sync_checks.py`

- [x] Add failing tests for added/changed/removed/unchanged endpoint classification and exact affected plan/case IDs.
- [x] Confirm RED.
- [x] Implement a pure revision comparator keyed by `endpoint_key` with compact field changes and old/new hashes.
- [x] Add a full plan reader and deterministic impact join. Do not guess mappings for unresolved legacy plans.
- [x] Persist diff summaries separately from immutable revisions.
- [x] Re-run focused tests and include them in the final Phase A commit.

## Task 5: Asynchronous Manual and Scheduled Synchronization

**Files:**
- Create: `task_server/services/api_sync_service.py`
- Modify: `task_server/app.py`
- Test: `tests/api_asset_sync_checks.py`

- [x] Add failing tests for queued/running/succeeded/no-change/failed records, single active sync per source, prior-revision preservation on failure, restart recovery, and due-source scheduling.
- [x] Confirm RED.
- [x] Implement phases `fetch_source -> parse_document -> diff_revision -> persist_revision -> analyze_impact` with stable `sync_id` and `poll_after_ms`.
- [x] Stage the revision before atomically moving the active asset pointer.
- [x] Return the current active sync instead of launching duplicate work.
- [x] Recover abandoned queued/running records as failed on startup and run one daemon scheduler with a 60-second wake interval.
- [x] Re-run focused tests and include them in the final Phase A commit.

## Task 6: Additive Authenticated Routes

**Files:**
- Modify: `task_server/router.py`
- Modify: `tests/backend_static_checks.py`
- Test: `tests/api_asset_sync_checks.py`

- [x] Add failing route tests for source list/save, sync start/status, revision list, diff, and impact.
- [x] Confirm RED.
- [x] Add `GET/POST /api/api-testing/sources`.
- [x] Add `POST /api/api-testing/sources/{source_id}/sync` returning HTTP 202 and `GET /api/api-testing/syncs/{sync_id}`.
- [x] Add revision/diff/impact GET routes and extend existing assets/overview responses without removing fields.
- [x] Map validation to 400, missing records to 404, and active-sync conflicts to a stable non-destructive response.
- [x] Re-run route/static checks and include them in the final Phase A commit.

## Task 7: API Asset Synchronization Console

**Files:**
- Modify: `js/state.js`
- Modify: `js/api-testing.js`
- Modify: `js/api.js`
- Modify: `css/round5.css`
- Modify: `task-manager.html`
- Modify: `tests/frontend_static_checks.py`
- Modify: `tests/visual_smoke_check.js`

- [x] Add failing frontend checks for source state, write-only token behavior, sync action, stable polling, revision/diff summary, and retained upload fallback.
- [x] Confirm RED.
- [x] Replace upload-first copy with source status and `同步 Apifox` as the primary action.
- [x] Add a compact source settings surface for name, project ID, optional environment ID, interval, enablement, and write-only token.
- [x] Poll the real `sync_id`; do not fabricate progress or duplicate timers.
- [x] Preserve the selected revision, expanded regions, and scroll position across refreshes.
- [x] Show added/changed/removed/unchanged and affected-plan counts; keep manual JSON upload as a secondary fallback.
- [x] Verify desktop and mobile screenshots and include the console in the final Phase A commit.

## Task 8: Compatibility, Security, and Real QA Acceptance

**Files:**
- Modify: `CODEX_STATE.md`
- Test: `tests/api_asset_sync_checks.py`
- Test: existing full suites

- [x] Add an integration fixture covering first sync, no-change sync, schema change, endpoint removal, and failed sync preserving the active revision.
- [x] Run `python3 tests/api_asset_sync_checks.py -v`.
- [x] Run `python3 -m py_compile task_server/services/api_source_service.py task_server/services/apifox_service.py task_server/services/api_asset_service.py task_server/services/api_schema_diff_service.py task_server/services/api_sync_service.py task_server/router.py task_server/app.py`.
- [x] Run `python3 tests/backend_static_checks.py`, `python3 tests/frontend_static_checks.py`, `python3 tests/ai_gateway_static_checks.py`, and `python3 ai_skills/evals/run_skill_evals.py`.
- [x] Run `node tests/visual_smoke_check.js`, `git diff --check`, and `npm test`.
- [x] Inspect the intended commit scope and confirm no credential or protected user file will be staged.
- [x] Update `CODEX_STATE.md` with exact verification evidence and remaining Phase B/C/D work.
- [x] Commit the implementation without pushing.
- [ ] After user deployment, configure the real token through authenticated server configuration or `/opt/midscene.env`, synchronize the selected real Apifox project twice, and verify first-sync plus no-change evidence without exposing the token.

## Follow-On Plans

- **Phase B:** executable API request/assertion/dependency contract, deterministic readiness/stale gates, and normalized AI traces.
- **Phase C:** MeterSphere `v3.6.5-lts` capability probe, definition mapping, actual supported run strategy, terminal polling, and real report closure.
- **Phase D:** register MeterSphere behind the global `ExecutionFacade` and converge shared execution/report/failure contracts without changing the existing UI Agent path.
