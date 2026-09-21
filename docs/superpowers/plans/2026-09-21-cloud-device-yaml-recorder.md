# Cloud Device YAML Recorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start recording from the task platform, operate the fixed Android device in Sonic native remote control, mirror actions and evidence, then convert confirmed steps into validated Midscene YAML.

**Architecture:** Add a focused recording service and a small Sonic Web recording hook. The hook asynchronously mirrors control actions without proxying Sonic video or touch traffic. Windows Runner collects debounced UI XML and keyframe evidence only. Semantic conversion is deterministic from UI metadata first, with AI used only for ambiguous descriptions; existing YAML validation and scoring remain mandatory.

**Tech Stack:** Python standard library service/router, existing JSON storage helpers, Windows Python Runner with ADB, plain JavaScript/CSS frontend, unittest/node:test/static checks.

## Global Constraints

- The user explicitly selects one Android phone currently reported by its Runner; refresh never switches phones automatically. Business printer IDs `9888E0094F2A` and `18CEDF5BA7B2` are invalid recording targets.
- Coordinates are evidence only and never the default final YAML locator.
- Support only existing Midscene actions: `launch`, `aiTap`, `aiInput`, `aiScroll`, `aiWaitFor`, `aiAssert`, `sleep`, `runAdbShell`, and other actions already accepted by the current whitelist.
- A recording draft cannot execute until every ambiguous step is confirmed and existing static validation passes.
- Preserve all user Word documents and `docs/career/`.

---

### Task 1: Recording session domain and device lock

**Files:**
- Create: `task_server/services/device_recording_service.py`
- Modify: `task_server/config.py`
- Test: `tests/test_device_recording_service.py`

**Interfaces:**
- Produces: `create_recording_session(user, runner_id, device_id, app_package) -> dict`, `get_recording_session(session_id) -> dict`, `finish_recording_session(session_id, user) -> dict`, `active_recording_for_device(runner_id, device_id) -> dict | None`.

- [x] Write focused tests for fixed-device rejection, one active session per device, owner checks, state transitions, atomic storage, and stale-session pause.
- [x] Run `python3 -m unittest tests.test_device_recording_service`.
- [x] Implement the minimal locked file-backed session state machine: `recording`, `paused`, `generating`, `finished`, `cancelled`.
- [x] Re-run the focused tests and include the domain layer in the recorder change.

### Task 2: Sonic action mirror and authenticated recording protocol

**Files:**
- Modify: `task_server/services/device_recording_service.py`
- Modify: `task_server/services/runner_service.py`
- Modify: `task_server/router.py`
- Test: `tests/test_device_recording_protocol.py`

**Interfaces:**
- Produces: authenticated action-ingest API, short-lived recording token, ordered action storage, and evidence collection requests.

- [x] Test monotonic sequence numbers, command-id replay protection, wrong Runner/device rejection, payload limits, and result ownership.
- [x] Expose authenticated session create/read/action/finish APIs and Runner-authenticated result upload.
- [x] Include pending evidence requests in the existing Runner heartbeat response without changing ordinary job or snapshot behavior.
- [x] Run protocol, access-control, backend-static, syntax, and diff checks.

### Task 3: Windows Runner ADB evidence collector

**Files:**
- Modify: `windows-midscene-runner.py`
- Test: `tests/test_windows_recording_executor.py`
- Modify: `deploy/windows-stack/README.md`

**Interfaces:**
- Consumes: heartbeat `recording_evidence_requests[]` created after Sonic has already sent an action.
- Produces: bounded keyframe PNG, bounded UI XML, timestamps, and matched device ID; it never repeats the phone action.

- [x] Test screenshot/XML collection and rejection when the selected phone is absent from the Runner ADB list.
- [x] Implement read-only screenshot and UI XML collection; reject other devices and never accept action commands.
- [x] Suppress duplicate uploads in process; server uploads are idempotent across Runner restarts.
- [ ] Run Runner syntax and focused tests, rebuild `dist/windows-stack-launcher.zip`, and deploy the updated Runner.

### Task 4: Evidence normalization and YAML draft generation

**Files:**
- Create: `task_server/services/device_recording_yaml_service.py`
- Modify: `task_server/services/device_recording_service.py`
- Test: `tests/test_device_recording_yaml_service.py`

**Interfaces:**
- Produces: `normalize_recorded_step(step) -> dict`, `generate_recording_yaml(session) -> dict` with `yaml`, `issues`, `score`, `requires_confirmation`.

- [x] Test UI-node text/content-desc/resource-id mapping, input, swipe, supported keys and checkpoints.
- [x] Add explicit ambiguous-step output and owner-reviewed semantic confirmation; never emit coordinate actions.
- [x] Pass generated YAML through existing static validation and executable scoring; block debug while issues or confirmations remain.
- [x] Run YAML generation, validator, scorer, backend-static, syntax, and diff checks.

### Task 5: Cloud-device recording page

**Files:**
- Create: `js/device-recorder.js`
- Modify: `task-manager.html`
- Modify: `js/navigation.js`
- Modify: `js/state.js`
- Modify: `css/app.css`
- Test: `tests/device_recorder_ui_check.js`
- Modify: `tests/frontend_static_checks.py`

**Interfaces:**
- Consumes: `/api/device-recordings` session APIs and authenticated evidence images.
- Produces: fixed-device recorder, action toolbar, step timeline, ambiguity editor, checkpoint editor, YAML preview, and links to existing editor/debug flow.

- [x] Add DOM/protocol tests for fixed-device gating, token-safe Sonic opening and original-control-first mirroring.
- [x] Implement the recorder without continuous capture; collect one keyframe and UI tree for each mirrored action.
- [x] Render Chinese labels and errors, explicit selectable phone cards, timeline, ambiguity confirmation, YAML preview and asset save.
- [ ] Run DOM tests, frontend static checks, responsive visual smoke, and diff checks; commit.

### Task 6: End-to-end verification and deployment

**Files:**
- Modify: `CODEX_STATE.md`
- Modify: `docs/superpowers/specs/2026-09-21-cloud-device-yaml-recorder-design.md` only if implementation evidence changes a stated contract.

- [ ] Run all focused Python and Node suites plus required backend/frontend/Runner checks.
- [ ] Push `main`, deploy with `deploy/update-main-server.sh`, and confirm `/api/health` reports the exact commit.
- [ ] Update the Windows package and restart Runner only because Task 3 changes the Runner protocol.
- [ ] In Safari, explicitly select one idle Sonic Android phone, record one safe read-only flow, verify evidence and semantic YAML, run static validation and a single debug, then end the session and confirm device release.
- [ ] Record exact session ID, generated asset, debug job, result, and remaining limitations in `CODEX_STATE.md`; commit and push the handoff.
