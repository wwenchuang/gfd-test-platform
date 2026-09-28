# 2026-09-28 recorder remaining defects

## Chrome diagnostic, not new recording acceptance

Authorized phone PHM110 / ecbfd645; printer 9888E0094F2A was not used. Chrome native UI opened Sonic AndroidRemote/35 from the idle device card. No platform recording session was started, so this test does not depend on platform recognition or evidence readiness.

- On 智小白3D homepage, one default-mode click on bottom 我的 left the phone on homepage after more than a minute; the banner continued updating.
- Selected Sonic 手动修复 → 修复触控, observed 修复成功. Same visible 我的 target then navigated to personal center.
- Clicked 打印记录 once in repair mode. First screenshot was still the old page; subsequent settled screenshot showed print records. No second click was sent to force navigation.
- Android Back returned to personal center; clicking 首页 restored the actual homepage. Closed only this diagnostic remote tab; Sonic device center then showed PHM110 空闲中.
- No print record was deleted and no print job was sent. Browser zoom was unchanged; diagnostic screenshot files were resized only for inspection.

This proves a difference between touch paths, not the exact Android-side root cause. Do not claim default touch fixed or count this as one of the requested three recorder+Runner rounds.

## Source findings

Official client `AndroidRemote.vue`: default sends touch down/move/up; 修复触控 toggles a page-local boolean and uses debug tap/swipe, which Agent handles with ADB input. The toggle resets on reload.

Official Agent v2.7.2 `AndroidTouchHandler.writeToOutputStream` logs null stream / IOException without reporting a UI operation failure. It does not prove phone input was accepted.

Official APK v2.0.8 `SonicPluginTouchService.processLoop` mutates shared `events[0]` then posts a lambda that reads it later. Rapid down/up can be observed by the queued callbacks as up/up. Source-level race is independently reviewed; actual PHM110 causal attribution still requires patched APK comparison or device-side trace.

Sources:
- https://github.com/SonicCloudOrg/sonic-client-web/blob/main/src/views/RemoteEmulator/AndroidRemote.vue
- https://github.com/SonicCloudOrg/sonic-agent/blob/v2.7.2/src/main/java/org/cloud/sonic/agent/tests/handlers/AndroidTouchHandler.java
- https://github.com/SonicCloudOrg/sonic-android-apk/blob/v2.0.8/app/src/main/java/org/cloud/sonic/android/plugin/SonicPluginTouchService.kt

## Platform repeated-target regression

The old model prompt required only a short control name; parsing dropped returned record identity. Two new tests failed against baseline: qualified Delete became plain Delete; ambiguous target at confidence 1 was accepted.

Independent review caught ordinal-only context still being accepted; an additional failing regression preceded the guard for obvious position/coordinate-only strings. Bare numeric context is conservatively rejected; a labeled visible record number is accepted. Review of the corrected service/tests found no further blocking findings.

Candidate retains visible record context for repeated targets in one existing vision request; requires explicit unique/repeated scope, rejects ambiguous/missing scope, missing record context, non-text and overlong descriptions. Existing foreground XML rejection, manual edits, force recognition and late request-ID guards remain.

Final local rerun: service + YAML + protocol 66 passed (0.77s); backend static 63 passed; required Python compilation and git diff --check passed. Mocked model regressions are not real model acceptance. Existing history is not rewritten. No extra model requests, images, retries, heartbeat or polling work added; model latency/token cost of the changed prompt still needs live measurement.

## Outstanding gates

- Real model evaluation of repeated controls and popup/home targets.
- Native-touch patch build and device acceptance; local host has no JDK/Android SDK.
- Reviewed recorder-only deployment and revision verification.
- Three fresh Chrome recordings and Runner replays with exact same record identity, then temporary data cleanup.
- Account-attribution performance branch remains isolated and not deployable until its separate performance gate passes.

## Deployment boundary

The Chrome/phone diagnostic is complete and the phone is released. The user previously reserved the shared deployment terminal; a question about terminal availability is pending. No terminal input, production code replacement, or server restart was made in this turn. Local source has no configured model credential, so real-model and deployment checks remain pending that access.

APK patch was dry-run and applied to a fresh copy of official v2.0.8, then byte-compared with the reviewed candidate. A first repeat apply against the already-patched temporary tree failed as expected; the clean-source verification passed. The Python scheduling model passed tap, drag and two gestures, but is not an Android runtime test. Matching InputManagerWrapper source was inspected and does not retain the injected event; it ignores the reflected Boolean result. The browser WebSocket path calls writeToOutputStream directly, without the 300 ms delay in the separate tap helper.
