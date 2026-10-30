# Lossless video review groups implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for this single bounded task and independent task/whole review.

**Goal:** Keep all business video review facts usable under the existing draft presentation limits.

**Architecture:** Collect event-associated review texts before normal draft normalization, then pack complete texts into deterministic anchored groups. Retain unmatched root questions and full immutable source snapshot. Normal draft and execution validation remain unchanged.

**Tech Stack:** Python, existing draft_proposal/normalize_steps, pytest.

## Global Constraints

- Business-only mode video-business-first-v1; legacy unchanged.
- Preserve every distinct full issue text and every actual event/time association; no semantic resolution or text truncation.
- Keep100 question entries/2000characters each/50steps and every normal review/execution gate.
- Preserve source/root/event/audit snapshots and private model records; no model/device/source actions.
- Only later designated phoneecbfd645/Runner mac-android-adouceshi/printer9888E0094F2A, no final print.

### Task 1: Lossless event-associated review presentation

Files: task_server/services/ui_video_analysis.py, optionally a small focused helper module if justified, tests/test_ui_video_question_presentation.py and a focused new group test module.

- [ ] Write failing tests for actual27-like45root+48event issue shape and reused-result confirmations; prove original100-entry failure first. Include all event anchors and exact text retention, same-text multiple-event association and unrelated roots.
- [ ] Implement business-only collection/packing; join complete atomic review texts within2000 characters, split at issue boundaries, preserve oversize/over100 failure. Keep snapshot and all steps needs_review.
- [ ] Keep existing exact duplicate behavior wherever grouping is unnecessary; revise only assertions whose presentation intentionally changes, retaining stronger losslessness checks. Test legacy/defaultAI root30/internal128 and final unmatched101 failure.
- [ ] Run focused question aggregation/presentation/group and normal draft/import/worker tests, then mandatory compile/backend/diff. Report RED/GREEN, actual-shape counts and remaining limits.
- [ ] Append STATE without staging existing dirty history; scoped commit, task review and whole change review. Root owns integration, protected-file checks, exact package/deploy and full source/SDK/phone acceptance after other diagnosed defects are resolved.
