# Recorder touch and repeated targets implementation plan

> Execute task by task with regression evidence; the user has authorized implementation and Chrome/PHM110 verification.

**Goal:** Make recorded actions actually execute and preserve the identity of repeated controls during replay.

**Architecture:** Keep Sonic native input separate from asynchronous recognition. Name a clicked control using the existing marked crop plus full screenshot in one model request. Return explicit target scope, and qualify repeated controls with visible record identity. Ambiguity fails closed through existing recognition/YAML gates.

**Tech Stack:** Python service and tests, Sonic Vue/Java reference sources, Chrome, Windows Runner.

## Constraints
- PHM110/ecbfd645 is the authorized phone; 9888E0094F2A is a printer.
- Do not merge account-attribution performance work, rewrite old evidence, send duplicate physical taps, or add per-step model requests.
- No business-record deletion during acceptance: open deletion dialog, verify target, cancel.
- Existing historic recognition results remain unchanged until explicitly re-recognized.

## 1. Repeated target identity
Files: task_server/services/device_recording_service.py; tests/test_device_recording_service.py; tests/test_device_recording_yaml_service.py.
- [x] Add failing cases: repeated label with visible record identity must preserve identity; missing/ambiguous scope or repeated label without context must fail even at confidence 1; foreground dialog must not use background context.
- [x] Update model contract: semantic_description, confidence, target_scope (unique/repeated/ambiguous), target_context. Compose repeated target as `context中的「label」`; reject overlong or malformed output rather than truncate away identity. Require context from visible record number/name/time; never ordinal-only or guessed identity.
- [x] Confirm manual correction and forced recognition retain existing request-ID protection and invalidate generated YAML.
- [x] Confirm qualified target survives aiWaitFor and aiTap; failed target blocks can_debug.
- [x] Run service/YAML/protocol regressions and required backend checks.

## 2. Native touch diagnosis and repair
Reference: official Sonic client AndroidRemote.vue and Agent v2.7.2 AndroidTouchHandler.java/AndroidWSServer.java.
- [x] In Chrome, compare one default tap to one explicitly selected repair-mode tap; inspect actual phone destination, not recognition status.
- [ ] Trace observed failure to touch connection/readiness/coordinate handling before choosing patch. Do not convert a delivered tap into a second ADB tap.
- [ ] Add regression reproducing the diagnosed failure, make minimal patch, verify refresh and reconnect behavior. If Windows component is necessary, preserve deployment boundary explicitly.

## 3. Acceptance and handoff
- [x] Required compile, backend static, diff checks; frontend checks if hook/UI changes.
- [ ] Review diff and deploy only the recorder candidate with revision/health verified.
- [ ] Run three new Chrome recordings and Windows Runner replays: 我的 → 打印记录 → a specific visible record's 删除 → 取消 → 返回 → 首页. Compare record identity and final page in reports.
- [ ] Check one-call recognition, response timing, idle polling; remove only temporary test assets after acceptance, retain sanitized evidence.
- [ ] Update CODEX_STATE.md with pass/fail and remaining limits; commit verified changes.
