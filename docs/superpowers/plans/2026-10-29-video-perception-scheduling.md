# Bounded video perception scheduling implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the serial prefix of normal video analysis without deleting evidence or extending its deadline.

**Architecture:** Permit a closed four-job option in the existing parent-owned supervisor, explicitly select it only for independent runtime perception windows, and keep serial model reasoning. Preserve joined cleanup under the same absolute reserve.

**Tech Stack:** Python supervisor/socketpair/Popen, existing pytest loopback HTTP, normal platform/Gateway.

## Global Constraints

- Same source, frames/roles, Qwen stage policies and token caps; no semantic/audit/tail parallelization.
- Default ModelJobs.map concurrency2 and run concurrency1 remain; accepted concurrency values exactly integer1,2,4 (bool is invalid).
- Runtime produce explicitly selects concurrency4; lazy builders, indexed results and durable proof/raw ACK remain.
- Outer1800s, engine1200s/24, shared80/lowercallcap, per-call120s, cleanup0.5s, record64files/16MiB/1MiB, correction2/revisit4/new64 and gates remain.
- Four-child cleanup shares one existing reserve and attempts every sibling, preserving original errors and failure latching.
- No auth/source edits, model/browser/device/deploy actions by implementer; root owns actual acceptance.

### Task 1: Bounded four-way perception and joined cleanup

**Files:** Modify task_server/services/ui_video_model_jobs.py and task_server/services/ui_video_runtime.py; tests/test_ui_video_model_jobs.py and tests/test_ui_video_runtime.py. Add a focused helper/test only if these responsibilities require it; no other production changes without reporting the concrete reason first.

**Interfaces:** ModelJobs.map(builders, *, concurrency=2) continues yielding (input_index, validated_result); run stays serial. Runtime perception calls map(builders(), concurrency=4). Existing source auth/version checks and parent evidence callbacks remain synchronous.

- [ ] Add failing tests: strict supported concurrency values, four actual loopback requests and lazy38-job refill, explicit runtime4 with serial downstream unchanged.
- [ ] Run the tests on old code and retain expected RED evidence.
- [ ] Implement the smallest bound and wiring change; ensure four-child cleanup does not multiply the existing absolute0.5s reserve.
- [ ] Test four-child cancellation, permission/deadline, proof/raw writer/schema errors, abandonment, SIGTERM-resistant child cleanup and no late records. Default2 and run1 regression remain.
- [ ] Run scoped affected tests, mainchain compile, backend_static_checks.py, git diff --check; Gateway checks if production Gateway changes become necessary (not expected).
- [ ] Self-review the change and write report with exact commands/results, red/green and limits, update CODEX_STATE.md by append only.
- [ ] Commit only scoped production/tests/docs; exclude existing dirty CODEX_STATE.md. Root independent reviewer must issue both spec and quality verdicts before integration.

### Root acceptance after Task 1

- [ ] Integrate reviewed committed scope into primary branch, preserve all existing dirt, push, build/checksum release.
- [ ] Native QA upload/deploy and verify exact release. No unchanged full-source retry before deployment.
- [ ] Retry same original normal source once, record progress and export terminal evidence; verify every hash and classify outcome from actual receipts.
- [ ] Continue whole-source review/import/normal gates and specified phone execution. Any content defect requires evidence-based next fix; generation/deployment alone never means task complete.
