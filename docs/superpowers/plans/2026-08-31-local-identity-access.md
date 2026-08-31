# Local Identity And Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Checkboxes retain progress across context transitions.

**Goal:** Replace the shared administrator with local members, configurable roles, scoped collaboration and persistent revocable sessions.

**Architecture:** A standalone SQLite identity store owns authentication and role/scope policy. Main platform and API testing enforce that policy at their HTTP/data boundaries while keeping actor identity separate from project ownership.

**Tech Stack:** Python sqlite3, argon2-cffi, existing HTTP router, SQLAlchemy/PostgreSQL API testing, vanilla main-platform JS and Vue.

## Global Constraints

- Do not rewrite router.py broadly, change Runner/Sonic credentials, rewrite baselines or operate a bastion.
- `get_access_profile(username)` returns username, user_id, display_name, status, role_ids, permissions, scope, is_superuser, must_change_password.
- `has_permission(username, permission)` and `scope_allows(username, kind, resource_id)` use current persistent identity. Scope kind: ui_apps/api_projects/api_environments; value: `"*"` or ID array.
- Personal workspace remains personal; shared resources keep immutable audit actor distinct from owner.
- Permission denied is 403 with a Chinese remedy, not an unauthenticated redirect. Unknown scope fails closed.
- No passwords/tokens in logs, git, normal list responses or audit detail. Argon2id work is bounded to two concurrent hashes.

## Task 1: Identity And Session Foundation

Files: `task_server/identity.py`, `task_server/auth.py`, `task_server/identity_http.py`, `tests/test_identity.py`, `requirements-api-testing.txt`.

- [x] Write failing tests for local user lifecycle, role changes, scope intersection, persistent revocation, initial password changes and last-admin protection.
- [x] Run `.venv/bin/python -m pytest tests/test_identity.py -q`, observe missing behavior.
- [x] Implement IdentityStore, profile/permission helpers, Argon2id bootstrap migration and opaque hashed sessions; keep login/verify_session_token/create_session_token wrappers compatible.
- [x] Implement `handle_auth_request(handler, method, path, qs) -> bool` for `/api/auth/me`, `/users`, `/roles`, `/permissions`, `/audit`, `/sessions`, `/change-password`, `/revoke-sessions`, user reset/update and role delete. `scope-options` is supplied by integration layer.
- [x] Run focused tests including concurrency, bootstrap and session persistence; review before integration.

## Task 2: API Project And Environment Authorization

Files: `task_server/api_testing/access.py`, API HTTP/services/repositories, focused API authorization tests.

- [x] Write failing shared-member/read-only/cross-project/environment/SSE/production tests.
- [x] Replace owner-only visibility with verified project scope, without impersonating owner; preserve standalone service tests' owner contract where no platform identity exists.
- [x] Enforce action permission at HTTP dispatch and data scope in all resource resolvers; bound list queries and reauthorize schedules.
- [x] Keep provider credentials private and per-user workspace; add scope-option query returning IDs and display labels only.
- [x] Run API focused tests and full backend gate; review actor/owner and background worker behavior. Final backend suite: 621 passed, including actual PostgreSQL/Redis scope, schedule and SSE contracts.

## Task 3: Main Platform Authorization And Deployment

Files: `task_server/access_control.py`, small dispatcher hooks in `router.py`, `response.py`, deployment scripts/env/docs, tests.

- [x] Write failing tests for read-only writes, unknown routes, scoped file/list access, Runner rejection at auth management and mandatory password change.
- [x] Hook identity endpoints before business routes; cache parsed request body once after size validation for resource authorization.
- [x] Resolve main-platform application ownership from stored metadata, filter collection responses, deny ambiguous/global actions to limited members.
- [x] Install the persistent identity directory outside APP_DIR; preserve through update, add HTTPS and backup instructions.
- [x] Run focused auth boundary tests, static checks, Bash/Python syntax and diff checks. Identity/HTTP/main/resource/Feishu suite: 170 passed; main boundary suite includes 52 tests.

## Task 4: Member/Role/Scope UI And Password Flow

Files: `js/identity-management.js`, `js/auth.js`, `js/app.js`, `js/agent-status.js`, `js/utils.js`, `task-manager.html`, CSS, Vue auth compatibility, browser tests.

- [x] Write failing UI checks for member creation, custom roles, scope selectors, mandatory change and 403 feedback.
- [x] Add four tabs and personal account actions, safe escaping and explicit loading/error/empty states. Use real names from `/api/auth/scope-options`.
- [x] Apply profile-based navigation without treating hidden buttons as security; retain deep-link return after password change.
- [x] Verify desktop/mobile flow with isolated users and no production side effects. The 31 browser checks use HTTP fixtures; the additional real identity-store/production-handler browser run passed 38 checks with 20 desktop/mobile screenshots, no outbound connections and no remaining child processes. The parent independently repeated this run.

## Task 5: Integration, Security Review And Delivery

- [x] Independent review of privilege escalation, forgotten endpoints, cross-project IDs, retired sessions, SQLite locking and secret exposure. Review findings were reproduced and covered by regression tests; API origin/header/scheduler and session/machine-boundary corrections received independent confirmation. The final main-platform path/alias corrections were verified by focused and real HTTP tests, not a completed second independent review.
- [x] Run `bash tests/run_api_testing_gate.sh`, main-platform visual smoke, static checks and `git diff --check`. Final complete gate exited 0 on 2026-08-31: API 621, Vue 382, production build, 11 visual screenshots, Chromium import/generation/debug/baseline/report/schedule/task-cleanup flow passed.
- [x] Update CODEX_STATE and design/plan with exact implemented coverage and remaining deployment constraints.
- [ ] Commit and push tested code; user deploys. Do not report online migration/HTTPS as completed by local tests.

## Review Findings Tracked In This Plan

- [x] Prevent delegated account managers from granting permissions/data outside their own authority.
- [x] Check execution and baseline permissions for combined generate-and-run and repair-and-run actions.
- [x] Package and deploy AI Gateway auth middleware; preserve Docker proxy connectivity with an explicit network-boundary warning.
- [x] Deny scoped UI AI/Agent routes whose internal shared baseline/history retrieval has not yet been isolated; document the limitation rather than silently exposing cross-app data.
- [x] Filter authorized UI cases before pagination and show a retriable Chinese error, not an empty permission picker, when the API scope catalog is unavailable.
- [x] Restrict rendered API request destinations before attaching shared environment credentials.
- [x] Mask legacy literal sensitive environment headers and prevent roundtrip overwrite with redacted placeholders.
- [x] Keep invalid scheduled targets from aborting unrelated scheduled jobs.
- [x] Add the Chinese blocked state and recovery advice to the schedule UI, distinguishing enabled configuration from blocked dispatch and naming the member who saved the task configuration.
- [x] Bind reconnectable SSE tickets to the originating revocable session.
- [x] Separate machine callback credentials from interactive member sessions and retain narrow Runner/Sonic reads used by the deployed clients.
- [x] Reauthorize stored execution/baseline flags when retrying jobs or generation; protect the default rerun in file repair routes.
- [x] Reject module-relative traversal, absolute paths and symlink escapes; validate every item of a batch before mutation.
- [x] Match handler precedence for `run_mode`/`runMode` and validate the smoke-rerun `mod` alias.
- [x] Keep audit write failures from rewriting a completed business response; identity mutations retain transactional audit requirements.
- [x] Prevent baseline workspace restoration from clearing a just-completed assertion audit; disable audit until initial context and list loading complete.
- [x] Keep limited members' manual YAML pages from requesting forbidden global Sonic status/case lists.
- [x] Include identity dependencies and gateway middleware in the offline deployment archive; inspect the archive for missing files or accidentally bundled identity databases.
- [x] Fix the scheduled-page mobile refresh label compression with a fixed-size accessible icon button; a browser dimension check failed before the fix and passed afterward.

## Integration Evidence And Remaining Delivery

- Real IAM browser acceptance uses random temporary credentials and an isolated SQLite store, with the production HTTP handler and no authentication mocks. It covers UI-created roles/members, scope selection, mandatory password change, A/B file access, path and batch denial, live role changes, account disable and actual server restart.
- The API end-to-end fixture now has its own identity database. Reusing a unit-test identity database had produced a legitimate login rejection, not a production password-migration defect.
- Earlier complete-gate attempts exposed a baseline audit initialization race and outdated scheduled-state wording. Both were corrected; failed attempts are not recorded as successful acceptance.
- Standalone main-platform visual smoke: 15 screenshots. API visual smoke: 11 screenshots. Main/frontend/gateway static checks: 63/84/46. Gateway authorization: 13 unit + 11 integration tests.
- Final full API gate has passed. Commit/push remains the delivery checkpoint until its commands finish. Online migration, HTTPS, public static report protection, external Sonic authentication and historical baseline replay are not completed by local acceptance.
- The build emits a non-blocking chunk-size warning for the approximately 506 kB main JavaScript bundle; route-level code splitting is not included in this authorization change.
