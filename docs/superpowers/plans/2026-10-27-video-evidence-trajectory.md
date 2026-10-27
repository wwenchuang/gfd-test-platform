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

## Evidence-led architecture checkpoint after run10 (not production enabled)

The sequential observer/audit protocol completed run6 structurally but failed source content. Run10 exhausted packet7 image capacity after20 calls/1192.315 model seconds. Native whole-video v4 improves business state continuity; atomic variants v5/v7 still merge repeat triggers or omit boundaries. Input-only native v6 directly returns two parent sessions but has approximate clocks, wrong pre/post states and nested questions. Do not continue atomic native prompt variants or enlarge the old serial budget as a substitute for fixing the failure layer.

Blind58-frame inventory (one118.539-second Qwen3.8Max/1024 call,6522 completion tokens) correctly records generation mark→absence→reappearance, next mark→absence→reappearance after canonical added frames, late blank-area mark and distinct print-button mark. It misses actual055 card and110 keyboard-arrow marks. Mechanical full ID coverage is not visual qualification or full-video coverage. All original calls remain immutable outside source storage; current normal worker stays unchanged.

### Diagnostic checkpoint A: spatial re-observation

- [x] Freeze8 original images054..056/108..112; select using native-v7 card candidate and native-v6 keyboard-boundary disagreement with blind inventory. Record selection provenance; this is a diagnostic sample, not automatic whole-film coverage.
- [x] Compare exact same blind prompt with8 originals against8 originals+8 fixed quadrant tiles from055/110 (2x nearest-neighbor, no annotations/ROI answers). Both requests are separate single calls, same Qwen/thinking/stream/timeout; tiles retain parent, bounding rectangle, transform and pixel SHA. Do not count tiles as new temporal evidence or owners.
- [x] Independently inspect original055/110 pixels and returned mark location; retain original failure even if re-observation improves. If zoom does not recover either small mark, stop this implementation branch and diagnose perception before adding a global assembler.

### Diagnostic checkpoint B: one fixed cross-packet editing sample

Only proceed if checkpointA supplies actual visible evidence. Use canonical fixed32-original-frame grid; select adjacent packets065..096 and097..128 without moving their boundary to fit known actions. Context may show before/after but never own a new trigger.

- [x] Freeze exact source SHA, original images/PTS/SHA and owned/context manifests; obtain independent blind per-frame facts, no candidate actions, actual input values or expected answers. Use the existing raw v6 session as a candidate only; no new native-video call for this sample.
- [ ] Construct a separate evidence mapping with source candidate IDs and actual frame references. Verify a single input parent across the packet boundary, independent keyboard-dismiss trigger at the actual visible original frame, and distinct generation trigger identities where source marks disappear/reappear. No post-hoc edits to raw calls; no nearest-time conversion into fake verified frame IDs.
- [ ] Save every nested question by original JSON path and impact dimension. Specific keystroke uncertainty is distinct from field identity/continuity/boundary uncertainty; never clear original questions because a later model says supported. Record any resolution and its evidence separately.
- [ ] Verify both directions: every candidate has evidence and every unmatched mark/important state change has an explanation or explicit question. Original sampled-frame coverage and unverified original-video time gaps are separate ledgers.
- [ ] Feed only mapped ordinary events to existing validate_analysis/consolidate_observed_frames and draft gates. Compilation must not invent missing evidence or merge independent raw triggers. Diagnostic structural success alone is not original-film or Runner qualification.

All actual calls in a proposed full run must share the existing24-call/1200-second budget and2 contract corrections, including native input, blind facts, mapping, audit, spatial re-observation and revisit. Existing per-call image<=60/10MiB,4 revisit attempts/64 new source-frame limits, ownership/cancellation/provider/decoder failures remain hard. Diagnostic300-second native requests do not silently change production per-call limits. Qualification remains three fresh complete source runs plus normal platform and fixed-device acceptance. No new production integration before these diagnostic checkpoints demonstrate the claimed improvement.

CheckpointB actual outcome: both blind calls completed; platform-normalized32-owned contracts pass. Mapping's single120-second call timed out with truncated JSON; no event/continuity qualification. The old shared run is failed/incomplete and its deadline is never reset. Partial output exposes duplicated SHA/path bookkeeping and editing-key actions repeated outside the parent input. A separate compact-protocol diagnostic may reuse frozen observations for comparison, explicitly not fresh full-source qualification: model selects short immutable evidence aliases; platform binds owner/PTS/hash/path and retains original questions without asking the model to copy them; independent actions inside a continuous input interval must identify a real boundary or be rejected. No production default integration follows this failed checkpoint.
