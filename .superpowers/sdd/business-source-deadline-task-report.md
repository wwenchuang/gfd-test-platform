# Business source deadline scoped task report

Task1 requirements: `docs/superpowers/specs/2026-10-30-video-business-source-deadline.md` and matching plan, starting HEAD8bd14c4b, cumulative root-review base12bbbee6. TDD skill retained from prior task; no additional agents spawned. Parent authorized the necessary normal-worker constructor call site and requested that its file be added to the plan mapping.

## Implemented

- Business RuntimeBudget defaults to4500seconds, selected explicitly by frozen business mode at the normal worker and direct runtime entrypoints. Legacy constructor defaults remain1800seconds. The source clock is still created once before native and shared with every owner/engine/tail; no reset after native/acquisition/failure and no extension of supplied existing budgets.
- Business engine derives its exact deadline from the same original source deadline minus480.5seconds, retaining four bounded120second tail calls plus cleanup. Legacy engine still uses engine-start+1200, with the existing source bound. Source checks still stop at deadline−0.5 for cleanup and after end_engine.
- Explicit business trajectory deadline may be at most observer-start+4020seconds; legacy explicit maximum remains1560seconds. Both require finite int/float excluding bool. Direct observers without explicit deadline still default to1200seconds, including business observers.
- No model, prompt, schema, per-call120, engine32/legacy24, provider formula/cap, acquisition/image/correction/question gate, cancellation or optional-native transport semantic change. No retry/refund or owner/reset changes.

## RED / GREEN

`python3 -m pytest -q tests/test_ui_video_business_deadline.py` initially produced6FAIL/12PASS before any production edits. Failures demonstrated absent business budget selection, normal entrypoint/worker still selecting1800seconds, custom business source tail still240.5seconds and business4020second explicit deadline being rejected as invalid before observation.

After minimal implementation, one test incorrectly expected duplicate-engine ValueError after the clock had already expired; existing deadline precedence correctly raised TimeoutError. The duplicate-start assertion was moved before expiration; production precedence was retained. New controls additionally parametrize the actual normal worker for legacy/business mode. `python3 -m pytest -q tests/test_ui_video_business_deadline.py tests/test_ui_video_runtime_evidence_reuse.py` ->43PASS,0.67seconds.

Fake-clock controls admit reservations at the old1659.5 absolute cutoff and immediately before the new4119.5 cutoff for a source created at100; reject exactly at4119.5; preserve the original4600 source deadline/reservations; end_engine only removes engine restriction; reject at4599.5 cleanup boundary and4600. Smaller injected1800second source remains1900 absolute with engine1419.5, never extended. Actual trajectory engine accepts late valid observe/audit calls with business4020/legacy1560 explicit maxima, rejects overlarge/nonfinite/string/bool bounds and rejects at exact4020 cutoff. Existing default direct-business1200 cutoff control remains unchanged.

## Verification

- `python3 -m pytest -q tests/test_ui_video_business_deadline.py tests/test_ui_video_runtime_evidence_reuse.py tests/test_ui_video_business_runtime.py tests/test_ui_video_runtime.py tests/test_ui_video_trajectory.py tests/test_ui_video_model_jobs.py tests/test_ui_video_optional_native_transport.py` —321PASS in177.32seconds.
- `python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ui_video_runtime.py task_server/services/ui_video_trajectory.py task_server/workers/ui_video_worker.py` —PASS.
- `python3 tests/backend_static_checks.py` —63checks PASS.
- `git diff --check` —PASS.

## Files and limits

Production: runtime/trajectory services and the single exact-mode budget construction in `task_server/workers/ui_video_worker.py`. Tests: new fake-clock deadline controls plus three exact boundary expectations in existing runtime-evidence tests. Documentation: plan file mapping amended to include the necessary worker call site; this report. STATE appended only and deliberately excluded from commit, preserving prior dirty content.

No known implementation concerns. This changes a finite operational time budget, so a bad provider/evidence run can remain active longer; all source/engine/call/cancellation limits remain enforced. Passing local controls does not establish original-source visual correctness, normal generated-case readiness or phone acceptance. No original25, provider request, deployment, push, API, browser or device action was performed. Root owns independent task/whole-release review, integration/exact deployment, unchanged original25 and downstream baseline/normal-draft/SDK/phone gates. Original24 failure is preserved by root.
