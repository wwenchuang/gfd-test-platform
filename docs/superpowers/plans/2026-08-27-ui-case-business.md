# UI Case Business Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require UI/API automation creation to select a configured Chinese business line and preserve its stable identity through case metadata, execution, history, and notification display.

**Architecture:** Application configuration stores `business_lines: [{id, name, enabled}]`. Creation forms submit the hidden stable ID, server services validate active configuration and persist it, and display surfaces resolve the configured Chinese name. UI metadata remains keyed by stable case ID without changing Midscene YAML syntax.

**Tech Stack:** Plain JavaScript/HTML/CSS, Python task server, pytest/static checks, existing task metadata and generation artifact stores.

## Global Constraints

- The only application display name is `智小白3D`.
- Default business lines are `家用` and `共享`; administrators may add, rename, or disable Chinese names.
- `home` and `shared` are legacy-compatible internal IDs, not user-entered values.
- New generation requires an explicit business selection and has no silent default.
- Do not add unsupported Midscene YAML fields.
- Existing unmarked cases remain readable and can be marked later.

---

### Task 1: Creation Form Contract

**Files:**
- Modify: `task-manager.html`
- Modify: `js/app.js`
- Modify: `css/app.css`
- Test: `tests/frontend_static_checks.py`

**Interfaces:**
- Consumes: DOM field `generate-business` with values `home` and `shared`.
- Produces: request property `business` for `/api/ui/generate-yaml-async`.

- [x] Add a failing static check for the required segmented control, validation message, and request property.
- [x] Run `python3 tests/frontend_static_checks.py` and confirm the new check fails.
- [x] Add the fixed application display and accessible business segmented control to the first wizard step.
- [x] Validate the selection before creating a generation job and include `business` in the request body.
- [x] Run `python3 tests/frontend_static_checks.py` and confirm it passes.

### Task 2: Generation Persistence

**Files:**
- Modify: `task_server/services/yaml_service.py`
- Test: `tests/test_case_business.py`

**Interfaces:**
- Consumes: `business` from a generation request or persisted batch metadata during regeneration.
- Produces: normalized batch `business` and per-file `case_businesses: {case_id: business}` task metadata.

- [x] Add failing unit tests for business validation, inheritance, and per-case task metadata patches.
- [x] Run the focused pytest tests and confirm the new assertions fail.
- [x] Normalize the request value, inherit it only for regeneration, and reject missing/invalid new generation values.
- [x] Save the business to asset metadata, generated case payload, summary, and each generated case's task metadata.
- [x] Run the focused pytest tests and confirm they pass.

### Task 3: Regeneration and Display Continuity

**Files:**
- Modify: `js/app.js`
- Modify: `task_server/services/yaml_service.py`
- Modify: `docs/task-platform-ux-audit-2026-08-26.md`
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Consumes: batch business from generation job/result/asset metadata.
- Produces: inherited regeneration requests and documented user path.

- [x] Ensure regeneration requests retain the existing batch business.
- [x] Include the business in generation results so the review page can retain context.
- [x] Document the creation, editing, mixed execution, and legacy-case behavior.
- [x] Run focused frontend/backend checks.

### Task 4: Configurable Business Lines

**Files:**
- Add: `task_server/services/business_line_service.py`
- Modify: application persistence, notifications, main UI, and API testing UI.
- Test: business-line, case, notification, frontend component, and visual checks.

- [x] Add failing tests for Chinese names, hidden stable IDs, rename, disable, and legacy compatibility.
- [x] Persist business-line configuration under the application and provide default legacy lines.
- [x] Render active lines dynamically in UI generation, Agent, UI case editing, and API case editing.
- [x] Resolve configured Chinese names in histories, baselines, schedules, executions, and notifications.
- [x] Keep disabled lines readable for historical records while rejecting them for new writes.

### Task 5: Full Verification and Commit

**Files:**
- Verify all modified source and generated frontend assets.

**Interfaces:**
- Consumes: completed implementation and tests.
- Produces: one verified git commit excluding `output/`.

- [x] Run Python syntax compilation for modified server files.
- [x] Run `python3 tests/backend_static_checks.py`.
- [x] Run `python3 tests/frontend_static_checks.py`.
- [x] Run `bash tests/run_api_testing_gate.sh`.
- [x] Run `node tests/visual_smoke_check.js` and inspect desktop/mobile screenshots.
- [x] Run `git diff --check` and review staged files for credentials and generated-file consistency.
- [x] Commit the complete batch with message `Improve task platform business labeling and usability`.
