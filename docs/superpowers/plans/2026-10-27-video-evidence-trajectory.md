# Video Evidence Trajectory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement and evaluate a state-aware, owned-action video observer, reuse existing case compilation and perform normal platform and real-device acceptance.

**Architecture:** A bounded whole-film overview supplies page/input/wait state memory. Complete local packets own actions by an actual trigger frame; structured edit continuation preserves event identity without natural-language merge predicates. An independent evidence review confirms every resulting event or requests bounded re-observation. Existing consolidation, input recovery and all execution gates remain.

**Tech Stack:** Python, Pillow, existing AI Skill/Gateway, JSON Schema, pytest, native Chrome bastion, existing Midscene Runner.

## Global Constraints

- Preserve current unrelated dirty files; work in existing isolated codex/ui-platform-simple-workflows worktree.
- Source and test baseline are fixed; never place baseline answers or source-specific business values in production prompts.
- Exactly ui_video_trajectory / whole-trajectory receives the existing 2..60-image chronological transport; total 10MiB and existing frame identity validation remain. No trajectory detail images.
- Inputs stay empty until existing safe business-input recovery. Original images, observations, state facts and event ownership remain auditable under owner/app/source/version permissions.
- Fixed device ecbfd645 / 9888E0094F2A. Existing attributable test task: cancel if present, continue if absent. No final physical print confirmation.
- Mandatory main-chain compile, backend static checks, Gateway checks when changed, scoped tests, diff check and actual original-film/device acceptance.

## Task 1: Bounded transport

Files: ai_skill_service.py, ai-gateway/server.js, corresponding existing tests. Interface: run_ai_skill('ui_video_trajectory', payload with phase='whole-trajectory', image_assets=labelled_originals). Keep other skills and phases unchanged.

- [x] Write and run failing tests for exact name/phase, 60 images, chronological/reference roles and rejected details/invalid identities.
- [x] Extend only the two existing whole-video predicates and reference-role labeling.
- [x] Run scoped Python/Node tests and Gateway checks; independent review and commit only task files.

## Task 2: State and owned-action contracts

Files: new task_server/services/ui_video_trajectory.py, ai_skills/schemas/ui_video_trajectory.schema.json, ai_skills/prompts/ui_video_trajectory.{overview,observe,audit}.md, tests/test_ui_video_trajectory.py.

Interface: observe_trajectory(document,root,cancelled,progress=None,record=None) returns validated ordinary events plus trajectory_audit. record(stage,data) receives bounded structured input/output/model metadata, never image bytes. Public logs never contain observation text.

- [x] TDD: prove full frame ownership once, packet/context bounds, state references, trigger ownership, duplicate identifiers, input continuation with intervening business action, non-input continuation rejection, complete audit coverage and unknown image/event references.
- [x] Implement make_packets(frames,states): prefer complete overview input intervals within a 48-frame core; default 32-frame core, up to 6 original context frames per side, maximum 60 images. Every source frame belongs to one core; context cannot independently authorize an action.
- [x] Implement strict top-level states/events/checks/uncertainties/multiple_scenarios contract; assign stable platform event IDs. Structured input continuation requires previous input identity, same target/page, no independent boundary, ordered endpoint evidence and no intervening action. Preserve all original fragments in audit; compiled references remain at most 8.
- [x] Implement one overview, sequential packet observations with state/prior-event memory, one independent event audit. Shared document budget permits at most 2 corrective calls plus a final audit; no nested retry amplification.
- [x] Run tests red/green; reviewer examines evidence loss, false merge, sensitive values and budgets.

## Task 3: Isolated original-film comparison

Files: diagnostic CLI outside source storage; external acceptance evidence. Reuse original source media and existing AI runtime in a separate process; never mutate source/draft/device during comparison.

- [x] Upload exact changed modules/prompts/schema in an isolated diagnostic package through native bastion; confirm actual hash and command output.
- [ ] Execute observe_trajectory with captured stage results. Compare output with fixed manual baseline including true print-entry, two next actions, distinct cancellations, continuous edits, independent keyboard dismiss and home endpoint.
- [x] Reproduce failures from captured responses before changing the failure layer. No fabricated output or broad gate reduction.
- [ ] At least 3 complete runs satisfy all fixed-film hard requirements; evaluate separate recordings and report limits.

## Task 4: Existing platform integration and acceptance

Files: ui_video_analysis.py, workers/ui_video_worker.py, source snapshot persistence only as necessary, focused integration tests.

- [x] Extract the existing consolidation tail into consolidate_observed_frames, preserving all legacy tests and gate behavior; new observer feeds that same function.
- [ ] Persist private trajectory audit and stage diagnostics with existing source permissions and revision cancellation guards.
- [ ] After isolated qualification, enable new observer; run full scoped regression and mandatory checks, independent review, commit/push and deploy with exact revision/health evidence.
- [ ] Normal platform original-film generation, review/import, stage checkpoint gates and full Runner execution on fixed phone. Separate generation/content/branch/device outcomes; do not report completion until evidence supports it.

Implementation note: independent audit runs immediately after every owned packet with all original images and immutable fragment IDs. Optional revisit requests acquire bounded actual source frames, preserve canonical decoder time, extend the private media ledger and own half-open time intervals. A failed shape may consume the shared two-correction budget; missing evidence is never accepted by stripping fields. Actual full-source validation remains pending.

2026-10-27 protocol revision: audit now judges supplied immutable trigger/outcome claims via separate supported/unclear/contradicted verdicts. Platform binds provided anchors, labels reference provenance and preserves raw verdicts. Owned structured issues carry final package blockers; sparse overview hypotheses remain review context. Original raw v1 failures remain unchanged; v2 must be assessed with new real model requests.
