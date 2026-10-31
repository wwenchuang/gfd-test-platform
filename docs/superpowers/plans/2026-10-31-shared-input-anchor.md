# Shared Input Anchor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this task and obtain fresh task and whole-change reviews.

**Goal:** Ensure a valid final input frame is offered to Qwen even when the next independent action begins at the same timestamp.

**Architecture:** Keep the current bounded final input-recovery pass. Permit its existing last input anchor to establish an eligible recovery check when there is no frame in the subsequent half-open interval. Retain the original safety and evidence gates and immutable caller data.

**Tech Stack:** Python, pytest, current normal video worker and Gateway contracts.

## Global Constraints

- Change only business input recovery and its focused regression tests; no model, prompt, provider, protocol, budgets, global gates, source data, or device policy change.
- No value is hardcoded, inferred from a later independent action, copied from model prose, or supplied manually to claim AI recovery.
- Reuse only the actual final input frame when it exactly matches the input end and is no later than the next independent interaction boundary.
- Sensitive/untyped fields, unsafe values, evidence-reference validation, unresolved-model rejection, cancellation, and input immutability retain their current behavior.
- Preserve the original frame's provenance and use the existing two-labelled-image transport rule, with an actual earlier anchor if required; do not synthesize an image.
- Existing source30 and its imported draft/revisions remain unchanged; model proof for this fix must be separately labelled until a fresh normal source completes.
- Phone trial uses only ecbfd645 and printer9888E0094F2A, and must settle before any deployment/restart or additional live diagnostic provider work.

### Task 1: Permit an eligible existing final input anchor at an empty following interval

**Files:**
- Modify: `task_server/services/ui_video_business_inputs.py`
- Test: `tests/test_ui_video_business_inputs.py`

**Interfaces:**
- Consume unchanged `recover_business_inputs(result, document, root, cancelled, *, skill_runner=None)` and current event/media contracts.
- Produce the same detached result and input_recovery audit; no signature change.

- [ ] Write RED regression with fixture input end200, own final ref f2 at200, following keyboard aiTap start200; assert the normal mocked provider receives candidate:0 with f2, sees no expected answer in payload, and a returned safe model value using f2 is recorded as recovered. Existing code skips it, which must produce the initial failure.
- [ ] Add a later-boundary/no-sample case; reject use of another input field's pixels, end-after-boundary and unknown/mismatched anchor cases. Cover protected field/value, invalid returned ref, uncertain result, cancellation and original input immutability at this boundary.
- [ ] Make the smallest evidence-selection correction. A suitable empty-interval check is an existing last ref present in media with its timestamp equal to event end and end no later than boundary. Continue to use existing `refs` construction and final model contract. Do not change the nonempty-following selection path or safe value policy.
- [ ] Run focused tests first, then `python3 -m pytest -q tests/test_ui_video_business_inputs.py tests/test_ui_video_consolidation_entry.py tests/test_ui_video_business_runtime.py tests/test_ui_video_runtime.py tests/test_ui_video_input_overlap.py` once at final code; report existing unrelated failures with reproduction rather than changing them.
- [ ] Run `python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ui_video_business_inputs.py`, `python3 tests/backend_static_checks.py`, and `git diff --check`.
- [ ] Commit only the two implementation/test files, leaving the dirty CODEX_STATE.md untouched. Self-review and write the full RED/GREEN/output/commit report to `.superpowers/sdd/shared-input-anchor-task-report.md`.

Controller then obtains task and whole-change review, checks unchanged run30 inputs against the actual pure function, and prepares one bounded real-Qwen recovery diagnostic. No new provider work/deploy while phone trial is active. Do not count that diagnostic as whole-source or phone acceptance.
