# Runtime evidence reuse implementation plan

Read the matching design first. Authorized execution includes implementation through original/phone validation; no routine approval pause. Current isolatedbase8fc40df8, deployedprimary1e1b6229. Two separately testable commits, one reviewed release.

## Task1: Acquisition routing and a single source-bound engine clock

Files: task_server/services/ui_video_trajectory.py, task_server/services/ui_video_runtime.py, tests/test_ui_video_runtime_evidence_reuse.py and exact-capture fixture tests/fixtures/ui_video_v20_runtime_limits.json. Additive adjustments to affected existing tests only for deliberately changed business scheduling; preserve legacy semantics and gates.

- Capture exact original20 records relevant to acquisitions, no-new audit, corrected frame error and final timeout, plus source/frame provenance; verify canonical hashes and dictionary equality. Preserve20asfailed.
- RED before implementation: actual business observer-loop no-new audit repeats same candidate/current images instead of full observer; mixed requests with any new frames must reobserve; original no-new questions/records remain; repeat exhausted audit request bounded; empty/multiple shards and candidate changes; missing/contradicted still repair and audit; positive locks/correction accounting remain. Observer no-new remains reconsideration. Do not create source-success claims from synthetic audits.
- RED clock tests: legacy1200, business absolute source deadline minus240.5 headroom, actual trajectory receives same absolute boundary, standalone default1200 and explicit bounded finite1560horizon; cleanup/cancellation/source limit/no reset across packet/shard/revisit/correction; postprocess still source-bound and headroom is not480guarantee. Original proof39 timeout82205/no raw/cleanup remains authentic failure.
- Implement minimal routing and deadline signatures; document headroom limitation. Run new plus trajectory/revisit/runtime/audit/executor checks and mandatory commands. Record logs and commit only scoped paths, append STATE/exclude it.

## Task2: Deterministic visual prefix and optional cache-hit measurement

Files: ai-gateway/server.js, task_server/services/ui_video_stage_receipts.py, task_server/services/ui_video_trajectory.py (optional cache count projection), tests/test_ui_video_evidence_prefix.py, tests/test_ai_gateway_video_evidence_prefix.js. Adjust existing exact-order stage tests only as required; legacy invariance assertions remain.

- RED actual provider options for all relevant closed stages: fixed system instruction, exact chronological labeled pixels/video before complete dynamic text, independent Python expected-options reconstruction/hash agreement; legacy text-first/no-image semantics unchanged. Same-model same-image observer/audit prefix equality and mismatched model/images/bytes/labels/detail/chronology controls; no observer narrative in audit. No explicit cache_control or new provider option/retry.
- RED optional cachedTokens: raw SDK prompt_tokens_details.cached_tokens and normalized second projection; absent stays absent, reported0 remains0; negative/bool/string/fraction/greater-than-prompt rejected. Actual skill/Gateway/child receipt trace preserves reported count; no cache-hit/perf assertion from layout alone.
- Implement opt-in closed-stage ordering and telemetry, retain models/options/schema/claims/pixels and independent attestation. Run focused Node/Python wire/attestation/child tests, affected suite and mandatory compile/backend/Gateway/JSsyntax/diff. Retain RED/GREEN/report and scoped commit.

## Root release and acceptance

- Fresh task review per task and final complete-release review; fix material findings with covering checks. Primary protected249/STATEprefix snapshots, exact-tree integration and required fresh checks; push and verify remote codex branch, no claim origin/main advanced.
- Offline package CRC/internal hashes/bundle/script checks; server20terminal/noactivepreflight, upload/serverSHA/deploy/full local+public exact revision. Run unchanged full original normal21once; no restart/deploy/retry active analysis. Measure actual cache reports/latency honestly, export all terminal records and inspect fixed manual business baseline.
- Ready source only proceeds normal draft review, strict static/score/exact target compile/dry-run, designated phoneecbfd645/Runner mac-android-adouceshi/printer9888E0094F2A, current account/task binding, conditional OLD-absent coverage statement and no final physical print. Any failure requires its exact evidence; no blind repeats or fabricated acceptance.
