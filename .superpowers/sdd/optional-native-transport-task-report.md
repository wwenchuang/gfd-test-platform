# Optional native transport task report

Scope: spec/plan `docs/superpowers/{specs,plans}/2026-10-30-video-optional-native-transport.md`, including root's committed encoded-size amendment a6b3f651. Worktree base fe8a8812, docs amendment a6b3f651. No production source/API/browser/provider/device access, deployment, push, source edits, or acceptance claims. Loopback-only test listeners execute actual ModelJobs owned subprocesses.

## Behavior and evidence

- Native timeline now uses a separately closed ModelJobs owner with the same RecordStore, source deadline and reservation callback. Its failure latch is never reset or reused. Main authoritative owner retains normal gates, model options, budgets and deadlines.
- Only durable timeline proof plus closed/clean terminal native job can qualify. Exact provider-failed HTTP429/5xx receipt matching its proof, or parent-observed local deadline without receipt, creates a fixed unavailable marker. Failed model content never enters that marker. Source/access boundary is rechecked after native closure and before disposition/next dispatch.
- All native/main proof/receipt/status rows remain in one verified snapshot and aggregate, with unique aggregate status indices. Private scoped owner provenance links status refs and cleanup failures. No failed call refund or new source budget.
- ModelJobs modification is a TimeoutError subclass for actual parent deadline checks only. This distinguishes child-reported generic timeout from an actual local deadline; child timeout is deliberately ineligible. No recovery/reset API, latch/close behavior change, retry or IPC/public error-code change.
- Local whole MP4 must validate integrity first. Encoded string >=10,000,000 characters yields fixed trusted source SHA/size provenance and no native job, reservation, proof or receipt. Direct Python and Gateway native validators enforce strict encoded<10,000,000. Existing12MiB bounded local loader remains. No truncation, transcoding or external upload.
- This corrects a documented transport-size mismatch; it does not establish the cause of original22/23 failures. Their original exports are read-only and remain failed. Safe closed records from their first shards are copied verbatim into `tests/fixtures/video_optional_native_transport.json`; tests need no local export paths.
- Gateway video logs add only closed failureCategory and validated integer400..599 httpStatus (otherwise null). SDK known timeout/connection/abort names map to fixed categories, otherwise unknown. Arbitrary error text/body/code/headers/URL are never added. Existing video error log projection is closed, while public error selection, routing, no-retry behavior and legacy logs are preserved.

## TDD evidence

1. `python3 -m pytest -q tests/test_ui_video_optional_native_transport.py`: first RED1, missing verified optional-native disposition; implemented durable classifier, GREEN1.
2. Same command after strict encoded boundary controls: RED2/2PASS because7,499,998 and12,249,942 byte inputs were still admitted. Strict Python boundary implementation -> GREEN4.
3. `node --test --test-name-pattern='closed video diagnostics' tests/ai_gateway_business_video.test.mjs`: initial test-harness correction added assert.rejects because handler intentionally propagates errors; meaningful RED5 with failureCategory undefined. Closed diagnostics implementation -> GREEN5.
4. Actual owned subprocess controls cover native429/500 failures and actual local deadline then actual trajectory overview/grounding/independent audit. Native owner is closed and latched, guard rejects reuse; authoritative owner shares store/deadline/budget, preserves status/receipt accounting. A later independent-audit503 remains overall failure. Oversize synthetic12,249,942 byte original follows actual full299frame/75,376ms trajectory with all10 independent packet audits and no native dispatch/reservation/evidence. These are synthetic transport/gate controls, not source visual-quality or normal worker/phone acceptance.
5. Negative controls cover missing/wrong proof/stage/hash/receipt/model/policy/status, malformed local oversize asset, cleanup failures/live child, cancellation/revocation/source deadline/schema and generic child timeout. Existing business runtime invalid timelines/authorization tests and ModelJobs real IPC/cleanup controls remain in focused suite.

## Verification commands and results

- `python3 -m pytest -q tests/test_ui_video_optional_native_transport.py tests/test_ui_video_business_runtime.py tests/test_ui_video_business_transport.py tests/test_ui_video_runtime.py tests/test_ui_video_model_jobs.py` —232PASS in182.72s.
- `python3 -m pytest -q tests/test_ui_video_optional_native_transport.py::test_oversize_malformed_original_cannot_use_preflight_skip` —1PASS (added after focused suite launch).
- `node --test tests/ai_gateway_business_video.test.mjs` —16PASS.
- `python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ui_video_runtime.py task_server/services/ui_video_native.py task_server/services/ui_video_model_jobs.py` —PASS.
- `python3 tests/backend_static_checks.py` —63checks PASS.
- `python3 tests/ai_gateway_static_checks.py` —46checks PASS.
- `node --check ai-gateway/server.js` —PASS.
- `git diff --check` —PASS.

Root owns fresh independent task SPEC/QUALITY review, whole-release review, integration/push/exact deployment, original24 normal source run, source visual/full-baseline qualification, normal draft, SDK and specified-phone acceptance. No deployment command executed by this task. No additional agents spawned.
