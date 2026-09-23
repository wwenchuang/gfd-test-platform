# Recorder readiness and page responsiveness implementation plan

> **For agentic workers:** Execute this plan inline in the current task. The user has already authorized the fix, deployment, and Chrome verification.

**Goal:** A Sonic touch must never be reported as successfully recorded before evidence and semantic checks finish, while a slow semantic call must not keep the next already-captured frame unusable. The recording page should not rebuild itself on an unchanged heartbeat.

**Architecture:** Keep Sonic's native touch delivery unchanged. Let the bridge distinguish the next frame's readiness from the previous step's semantic recognition. Use the bridge as the only source of permission to mirror a new action; a delayed platform acknowledgement must not override a pending next frame. Compare the recorder's displayed state before rebuilding its DOM.

**Tech Stack:** Browser JavaScript, Node test runner, Python backend, Sonic 2.7.2 hook, Chrome acceptance.

## Global Constraints

- The recording phone is the Sonic-selected Android device. `9888E0094F2A` is a printer identifier, not a phone.
- A successful tap means a captured pre-action frame, a recognized control, and an observed destination; a mere Sonic WebSocket send is insufficient.
- Preserve the user's existing workspace changes and avoid a Windows Runner change unless live diagnostics show it is necessary.

---

### Task 1: Separate readiness from recognition

**Files:** `deploy/sonic-recorder-hook.js`, `tests/sonic_recorder_hook_check.js`

- [ ] Add a regression where the next frame is ready but the prior step is still being recognized; assert the next Sonic tap is mirrored without a false “控件已记录” success notice.
- [ ] Add a regression where an old platform success message arrives while the next frame is pending; assert it cannot unlock recording.
- [ ] Run `node --test tests/sonic_recorder_hook_check.js` and confirm both regressions fail before the fix.
- [ ] Make the bridge own recording readiness and use precise messages for “已收动作”, “识别中”, and “识别完成”.
- [ ] Rerun the hook tests.

### Task 2: Avoid redundant recorder DOM rebuilding

**Files:** `js/device-recorder.js`, `tests/device_recorder_ui_check.js`, `task-manager.html`

- [ ] Add a regression: an unchanged heartbeat must preserve an in-progress editable field and the existing DOM node.
- [ ] Run `node --test tests/device_recorder_ui_check.js` and confirm failure before the fix.
- [ ] Compare display-relevant session fields and render only when they change; refresh message text without replacing the page.
- [ ] Rerun the UI tests and update the script cache key.

### Task 3: Verify and deploy

**Files:** `CODEX_STATE.md`

- [ ] Run scoped tests, repository static checks, `git diff --check`, and relevant syntax checks.
- [ ] Deploy the server and Sonic hook to QA using the existing Huawei bastion path; verify public health commit and hook hash.
- [ ] Use Chrome and PHM110 to record a real short navigation, inspect step evidence and destination, finish, and generate YAML; separately report any Windows Runner replay blocker.
- [ ] Record measured results and any unverified behavior in `CODEX_STATE.md`.
