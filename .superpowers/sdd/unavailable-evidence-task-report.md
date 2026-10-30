# Bounded unavailable-evidence implementation

Scope: approved `docs/superpowers/specs/2026-10-30-video-bounded-unavailable-evidence.md`, baseline `a400bf43`, plus root's general visible-interaction clarification in `968ad490`. Original21 remains failed. No source/phone/physical-print acceptance is asserted.

## Implemented

- `validate_states` keeps identical acceptance predicates and precedence (AST comparison against a400bf43 passed). Its state_reference error now carries zero-based index, fixed failed checks, validated current-frame anchors/timestamps and previous-start timestamp. No model state ID/description or foreign anchor is echoed. Both outer feedback and nested parent/latest transport retain the safe metadata.
- Business acquisition validates all requests first, excludes genuinely exhausted ranges, and preflights the entire pending batch against actual4attempts, global64newframes and128image slots with minimum2capacity/request. Atomic denial records every pending request as `not_acquired`, exact request index, request/owned time spans, absent decoded span, current budgets, stage/call and available proof/receipt refs. It never decodes a prefix or increments decoder attempts. Capacity allocation divides remaining global frames fairly between pending requests.
- Capacity denial preserves complete original questions as separate strings from fixed status text. Current-packet deferred records and fixed non-confirmation guidance reach subsequent requests. Same valid candidate proceeds through independent audit; unavailable requests alone do not reobserve/re-audit. Real additions and actual successful zero additions retain prior routing. Missing/contradicted findings and positive-trigger locks retain bounded repair/failure behavior.
- Missing loader remains hard `revisit_unavailable` in both modes. Decoder failures, cancellation and foreign frames remain failures. Legacy zero/exhausted question strings, hard refusals and absent deferred audit field are preserved.
- Explicit business32logical dispatches, RuntimeBudget `min(80, original_count + 2*count +20)`, diagnostic128; legacy24/+12/64 retained. All deadlines, actual acquisition/image/frame/repair/question/ownership/input/action/audit gates unchanged. No retry/reset/refund.
- Observe/audit prompts clarify timestamp ordering after acquisition, not_acquired semantics, and visible control press-feedback/independent response evidence without requiring a physical finger in view. No source-specific action count, time or business answer is encoded; ambiguous decoration stays unclear and genuine boundary requests are retained.

## Evidence and TDD

`tests/fixtures/ui_video_v21_unavailable_evidence.json` freezes the exact original source (359frames), all43canonical entries and all3manifest path/byte/SHA pins. Current local original export was compared byte hashes/dictionary equality, including failed/no-draft status. Original raw24/26 stay rejected; raw28 states validate unchanged. The fixture contains no synthetic successful source remainder.

Initial pre-production command:

`python3 -m pytest -q tests/test_ui_video_unavailable_evidence.py`

Initial RED:10failed/1passed (`unavailable-evidence-red.log`). Initial authentic replay setup required correcting source-frame selection and the synthetic SkillSchemaError constructor; no claim that that initial replay failed at the production symptom. After correction, final unchanged test file was copied into an isolated `git archive a400bf43` tree and run with the identical command: **19failed/9passed** (`unavailable-evidence-final-baseline-red.log`). Its authentic21 path reproduces `revisit_budget` after the original fourth prefix decode. Supplemental general prompt requirement was separately RED1failed/20passed before adding its prose (`unavailable-evidence-supplemental-red.log`).

Final focused GREEN: **28passed**, latest `unavailable-evidence-green.log`. The real engine replays authentic replies through raw28 using actual Skill output validation and captured exact per-attempt source frame sets. New behavior stops at independent packet004 audit with only3actual decoder calls/84packet frames, both raw28 requests deferred with exact proof27/receipt28 provenance and no invented audit answer/remainder. Synthetic controls cover later shards, actual zero/mixed additions,64global-frame fair allocation, absent loader, decode/foreign/cancel failure, missing/contradicted repairs/positive locks, exact diagnostic limits, exact business38/legacy30provider allowance and no re-freeze reset. Numeric later frame IDs at earlier timestamps pass. More than30required questions fails the existing gate.

Actual nested Skill + real Gateway `buildUserMessage` rendering preserves parent state-reference and latest typed schema slots. An actual spawned normal child against an attested **loopback-only** Gateway preserves both slots/deferred data with one proof/receipt, admitted terminal cleanup and no active child PID. Full2000character question remains exact through actual consolidation, draft proposal, private VideoStore import, build_draft_yaml and validate_draft blocking. These are isolated synthetic regressions, never original source success or live device evidence.

## Verification

Affected command is recorded in full at the top of `unavailable-evidence-affected-final.log`: `python3 -m pytest -q` over28 explicit modules covering runtime, business, trajectory, audits, revisits, stage transport, schema, executor, records, model jobs, consolidation and the new tests. **1097passed in154.58s**. Final focused28 were rerun after refining immutable original frame/proof replay assertions; production files unchanged after the broad suite.

Mandatory commands (all exit0; `unavailable-evidence-mandatory.log`):

```
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ui_video_trajectory.py task_server/services/ui_video_runtime.py
python3 tests/backend_static_checks.py
python3 tests/ai_gateway_static_checks.py
git diff --check
```

Backend63 and Gateway46 checks PASS. Root corrected a trailing space in its own prior STATE note; no user prefix was modified. This task appended a checkpoint to existing dirty CODEX_STATE.md and excludes the entire STATE file from its commit. Only six production/affected-test files plus new tests/fixture and this report/logs are staged. No unrelated prototype/artifact is included.

Logs preserve stdout with terminal trailing whitespace removed for repository whitespace checks; original frozen receipts/fixture bytes are unchanged.

## Remaining root work / limitations

No external provider/network call, browser, source regeneration, deployment, push or device action was performed. Spawned-child controls use local loopback only. Independent task/release review, exact-tree integration, packaging/server SHA/health, unchanged original22 once, full source/business baseline review and normal draft/SDK/designated-phone gates remain root-owned. Current offline evidence establishes bounded controller behavior, not model convergence or source acceptance. No deployment command is executed by this task.

## Review follow-up: rendered observe/audit guidance

Task review identified a narrow verification gap: template assertions did not prove the general guidance reached actual Skill and Gateway text. Test-only follow-up extends the existing real nested Skill/Gateway control to label captured stages and check **both observe and audit** actual request `prompt` strings and actual `buildUserMessage` output. Assertions cover visible control press-feedback/independent response candidates, no physical-finger requirement, marker-count ambiguity, unresolved decoration/unclear, physical-proof limitations, retention of genuine revisit requests, not_acquired non-support/full-question semantics and timestamp-based state ordering. It also excludes the original source ID/filename from rendered requests and source-specific time/count/frame answers from the new general paragraph; existing numeric budget/schema rules remain allowed.

Command: `python3 -m pytest -q tests/test_ui_video_unavailable_evidence.py` — **28passed in2.78s**, recorded in `unavailable-evidence-review-wire.log`. `git diff --check` and `git diff --cached --check` PASS. Only this test, report append and new test log are included. No production, STATE, fixture, source or external action changed. Existing1097affected/mandatory verification remains applicable because this follow-up changes assertions only.
