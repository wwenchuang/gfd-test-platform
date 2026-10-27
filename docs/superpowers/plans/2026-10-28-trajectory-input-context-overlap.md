# Trajectory Input Context Overlap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development task-by-task.

**Goal:** Resolve the demonstrated conflict between allowed future-context evidence and same-input continuation timing without trimming source evidence or weakening identity boundaries.

**Architecture:** Keep existing packet ownership and same-field identity. Compare new edit trigger to the last immutable fragment trigger, retain nonregressing canonical action/result endpoints, and bound genuine forward trigger gap. Context reference overlap alone is not a new edit boundary. All raw fragments/questions and independent audits remain.

**Tech Stack:** Existing Python trajectory observer, pytest, offline actual-response replay.

## Global Constraints

- Active worktree /Users/adouceshi/.codex/worktrees/recorder-touch-target-fix/midscene-task-platform; branch codex/ui-platform-simple-workflows. Base1a372559. Preserve all unrelated dirty files and historical CODEX_STATE. Root owns docs/state.
- Production edits only task_server/services/ui_video_trajectory.py continuation temporal predicate; focused tests may use existing trajectory modules. No prompt, schema, model route, Gateway, worker default, API, Runner or deployment changes.
- Exact input identity, field target, page_before/page_after, previous last-event adjacency, action/kind, edit_boundary, trigger ownership, evidence validation and8-reference compilation limit stay enforced. Do not compare Chinese observation strings, erase questions, drop original references or auto-confirm audits.
- Original whole fine-detail run ended failed/input_continuation at1/9 after8actualQwenMax calls592.990modelseconds. No restart of its budget or modification of its records. Only offline captured-response replay in this task; no model or device calls.

## Task 1: Continue same-field edits through overlapping context

**Files:** Modify task_server/services/ui_video_trajectory.py; add focused tests/test_ui_video_trajectory_input_context.py or extend existing trajectory tests.

**Evidence:** External /Users/adouceshi/Downloads/qwen-fine-detail-trajectory-shadow-prepared-20261028-xwcxpkvv/records. Accepted04observe input trigger3073933/end0365067. Current packet002 initial06 raw refs0334767..04711600/trigger0344800, exact accepted target/id/pages. Old predicate rejects -300ms because firstref precedes prior future-context end.08 has trigger0375567 but same firstref, so samefalse rejection.07 separately changes page_before and must remain invalid. Preserve all actual files and use generic shifted synthetic fixtures in committed tests.

**Temporal requirements, in addition to all existing structural guards:**
- Current owned edit trigger is strictly later than the last accepted raw fragment trigger, including three-fragment cases (not just original track trigger).
- Current raw first reference cannot precede canonical input start.
- Current action evidence end cannot precede canonical input action end.
- Current raw outcome terminal cannot precede prior raw outcome terminal; prior outcome may extend beyond action refs.
- max(0, current trigger timestamp - prior canonical action end)<=5000. Old context references cannot bridge a long gap to a late new trigger.
- Overlap otherwise is allowed for verified same input identity; all relevant original refs/fragments/anchors/questions remain, compiled canonical start stays unchanged and endpoints do not regress.

- [x] Write failing overlap test(s) including current trigger before/after previous future-context endpoint; confirm meaningful RED on current code.
- [x] Write guard tests for repeated/backward trigger incl last of3fragments, regressing action endpoint, regressing outcome-only endpoint, reference before canonical start, long forward trigger gap despite overlapping first context ref. Existing different field/page/id/independent-action/boundary/question tests must remain passing.
- [x] Implement minimal temporal predicate only. No response repair/rewrite or special source conditions.
- [x] GREEN focused trajectory/input-overlap/reference/audit/consolidation tests; mandatory mainchain py_compile, backend static, git diff --check. Gateway/frontend unchanged, no need to rerun those checks.
- [x] Replay accepted04 plus actual06/08 offline and report binding result;07 must still fail page identity. This replay proves contract handling only, never source qualification or actual new model run.
- [x] Self-review; commit only owned code/tests after checks, write .superpowers/sdd/input-context/task-1-report.md with RED/GREEN evidence/commit/exact commands and concerns. Root will dispatch fresh independent spec/quality review and broader video tests.

After review, root will decide one fresh diagnostic based on actual fixed failure. Existing full-source qualification, normal platform integration, shared whole-chain budgets and fixed-device acceptance remain open; this repair alone cannot claim completion.
