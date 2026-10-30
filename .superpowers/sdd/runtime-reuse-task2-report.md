# Task2 — evidence-first closed-stage transport and optional measured cache tokens

Base: reviewed Task1 at `bf9b6e86`. Task1 scheduling, deadlines, acquisition, audits and repairs are untouched. Existing dirty `CODEX_STATE.md` is preserved and excluded from this scoped commit as directed.

## Scope

- Closed visual video stages compose the unchanged fixed system instruction followed by exact chronological labels and JPEG/video parts, then the complete dynamic Skill prompt. No changed bytes, roles, pixels, model, thinking, schemas, provider retry/options, audit claims, admission or budgets. No `cache_control` or application/model-answer cache. Legacy text-first composition and zero-image semantic content remain unchanged.
- Python independently reconstructs that new message order for provider-options attestation/hash verification. Actual Gateway `/ai/skill` dispatch options and Python expected options/proof are compared across all nine closed stages, including native timeline video and text-only semantic.
- Optional `cachedTokens` is projected from SDK `prompt_tokens_details.cached_tokens`, retained when normalized again, and only accepted as a nonnegative safe integer bounded by a strict prompt count. Absence stays absent, zero stays zero; negatives, bools, strings, fractions and counts over prompt are omitted. Base usage's legacy conversion/filter behavior remains.
- One small Python `cached_token_usage` sanitizer is shared by actual skill usage/raw receipts, record sanitization (therefore `_safe_trace` and child IPC) and trajectory call telemetry. Existing archived captures have not been rewritten.
- Extracted the existing real Gateway function-slice harness into `tests/ai_gateway_video_test_helpers.mjs`; importing it registers no tests or server/listener. Existing stage tests use the same harness. Existing exact-order expectations changed only where opt-in closed-stage composition changed.

## RED before production

1. `node --test tests/test_ai_gateway_video_evidence_prefix.js > /tmp/runtime-task2-node-red.log 2>&1`
   Result: 19 tests, 8 PASS / 11 FAIL. Seven visual stages fail because the final content is an image/video rather than complete dynamic text; the overview test has a fixture route error; same-evidence prefix comparison fails with text-first composition; actual SDK cached zero/seven disappear. Semantic/absent/invalid count controls already pass. A later GREEN run exposed the overview route error in both the stage and mismatch controls (the fixture inherited Max instead of Flash); setting the exact existing overview Flash route corrected those tests, without any production route change.
2. `python3 -m pytest -q tests/test_ui_video_evidence_prefix.py > /tmp/runtime-task2-python-red.log 2>&1`
   Initial result: 12 FAIL / 17 PASS. Four expected service/ModelJobs missing-counter failures; eight trajectory fixture errors (missing mandatory audit revisit key, then wrong result container key). Corrected the fixture before any production edit.
3. `python3 -m pytest -q tests/test_ui_video_evidence_prefix.py > /tmp/runtime-task2-python-red-corrected.log 2>&1`
   Corrected RED: 6 FAIL / 23 PASS in 14.58s. Actual skill/raw/record/trace, actual spawned ModelJobs receipts/trace, and trajectory each lose valid zero/seven. All invalid/absent controls pass.

Only after this corrected RED did production code change.

## GREEN and verification

- `python3 -m pytest -q tests/test_ui_video_evidence_prefix.py > /tmp/runtime-task2-python-green.log 2>&1`: 29 PASS in 14.56s.
- `node --test tests/test_ai_gateway_video_evidence_prefix.js tests/ai_gateway_video_stage_wire.test.mjs tests/ai_gateway_business_video.test.mjs > /tmp/runtime-task2-node-final.log 2>&1`: 60 PASS, 0 FAIL. Dynamic prompt controls use full Unicode Skill text/payload/claims/feedback; exact audit contains no observer narrative. Same-model/same-images grounding/audit prefix equality and model/count/bytes/labels/detail/chronology mismatch controls are explicit. Original legacy exact-options assertions remain.
- After adding additional image-count and final-label mismatch controls, `node --test tests/test_ai_gateway_video_evidence_prefix.js > /tmp/runtime-task2-node-prefix-final.log 2>&1`: 19 PASS, 0 FAIL.
- `python3 -m pytest -q tests/test_ui_video_stage_runtime.py tests/test_ui_video_stage_wire.py tests/test_ui_video_evidence_prefix.py tests/test_ui_video_records.py tests/test_ui_video_model_jobs.py tests/test_ui_video_business_transport.py > /tmp/runtime-task2-python-affected.log 2>&1`: 407 PASS in 126.06s.
- Final controls: `node --test tests/test_ai_gateway_video_evidence_prefix.js tests/ai_gateway_video_stage_wire.test.mjs tests/ai_gateway_business_video.test.mjs > /tmp/runtime-task2-node-final-controls.log 2>&1`: 71 PASS, 0 FAIL. Adds exact actual-handler legacy visual text-first/no-image options, raw nested cached zero precedence over normalized nonzero (invalid raw does not fall back), and nine malformed raw prompt-count controls.
- `python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ai_skill_service.py task_server/services/ui_video_stage_receipts.py task_server/services/ui_video_records.py task_server/services/ui_video_trajectory.py`: PASS.
- `python3 tests/backend_static_checks.py > /tmp/runtime-task2-backend.log 2>&1`: PASS, 63 checks.
- `python3 tests/ai_gateway_static_checks.py > /tmp/runtime-task2-gateway.log 2>&1`: PASS, 46 checks.
- `node --check ai-gateway/server.js`, `node --check tests/test_ai_gateway_video_evidence_prefix.js`, `node --check tests/ai_gateway_video_test_helpers.mjs`, `git diff --check`: PASS.

## Limits and handoff

This is offline transport/telemetry verification with explicitly synthetic provider replies and loopback HTTP for actual spawned Python children. No external provider/network calls, browser, credentials, device, deployment, original-source edits, archived-capture rewrites or push. No cache-hit rate, provider cache availability, latency/cost improvement, whole-source success, generated draft, formal case or phone acceptance is claimed. Cache telemetry never participates in correctness, admission or deadline decisions. Root owns fresh broader affected checks, independent task/release review, exact integration and authorized deployed-original/normal-draft/device acceptance.
