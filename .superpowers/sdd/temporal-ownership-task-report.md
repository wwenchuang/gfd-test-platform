# Temporal ordinary-result ownership implementation report

Status: DONE. Implementation base `40dd0690`; implementation commit `8b26eb24` (`fix: qualify business trajectory ordinary result ownership`). This report is committed separately so the implementation commit is named exactly. Parent owns independent task/whole review and CODEX_STATE.md integration.

## Implemented scope

- Added pure `ordinary_outcome_domain`, `ordinary_outcome_frames`, and `ordinary_export_conflicts` in `task_server/services/ui_video_temporal_ownership.py`. Planner frame domains preserve the existing inclusive `[own.end_ms,next independent canonical.start_ms]` interval and exclusive next system boundary. The next evidence start remains distinct from the physical trigger. Diagnostics contain only validated canonical/fragment/packet IDs, action kind, refs, integer PTS, interval bounds and classification/required status.
- Added business-only preflight after full event binding/continuation folding and positive audit-lock validation, before acquisition/audit/admission. Impossible required current-packet projections use the original two-correction path. Cross-packet required conflicts fail explicitly, with both owners, before spending a current correction. A final global preflight remains before return.
- During temporal reobservation, preserve Counter multiplicity of independently validated `(action,trigger_frame,trigger_kind)` signatures as explicitly unverified candidate constraints. These are separate from stronger supported audit locks; targets/prose/evidence can be re-observed. Deletion, merging or kind changes cannot silently remove a constraint. Existing binding, continuation and audit lock validators remain unchanged.
- Preserve complete rejected root/event questions, including envelope-rejected replies, as final root review blockers. No truncation or automatic resolution is introduced; existing question capacity validation still applies. Original response records are retained.
- Optional waits/asserts keep their raw observation role. Their conflicts are recorded under `trajectory_audit.ordinary_export_conflicts`; no mandatory rejection is added for them. Planner candidates receive explicit domain metadata and unchanged exact allowed frame IDs; normal planner evidence gates and repair refusal remain intact.

## TDD evidence

Initial RED command:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_ui_video_temporal_ownership.py
```

After correcting the injected acquisition callback to return a valid empty result, **8 failed** for the expected missing behavior: acquisition occurred before temporal correction; repeated conflicts/deleted or merged or kind-changed candidates were admitted rather than exhausted; optional ledger and pure helper were absent; cross-packet conflict was accepted. This was not a provider failure.

Initial GREEN: **8 passed in 0.43s** after implementation. Additional regression fixtures then passed, including folded cross-packet input continuation, shared/system boundaries, signature multiplicity, candidate planner domain and zero-call repair refusal. A system-boundary fixture was corrected to use an actual recording-tail system boundary (900ms in a 1000ms recording), preserving the existing boundary policy.

Second RED: new envelope-rejection question preservation test produced **1 failed, 15 passed** because `original schema-rejected root question` disappeared. Added question preservation at the envelope boundary; GREEN **16 passed in 0.36s**.

Third RED: explicit fragment diagnostic identity test produced **1 failed, 15 passed**, missing `fragments`. Added safe fragment IDs/owners/first/last/trigger anchors; GREEN **16 passed in 0.35s**.

The synthetic checks cover correction before audit/acquisition; original correction exhaustion; independent signature multiplicity; optional async observations; canonical/shared versus trigger boundary; full long rejected questions; immutable original response records; cross-packet failure without prior mutation/correction reset; valid continuation folding; unchanged legacy behavior; planner candidate domains; and zero-call empty-domain/unsupported-wait repair.

## Final verification

Final expanded regression command:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_ui_video_trajectory*.py tests/test_ui_video_packet_contract.py tests/test_ui_video_audit*.py tests/test_ui_video_business*.py tests/test_ui_video_outcomes.py tests/test_ui_video_to_case.py tests/test_ui_video_temporal_ownership.py tests/test_ui_video_consolidation_entry.py tests/test_ui_video_runtime*.py
```

Result: **816 passed in 48.27s**. This includes existing invalid/intervened continuation rejection, immutable trigger locks, packet ownership/roles, audit/transport/call capacity, business runtime/planner, and exact-boundary versus late planner outcome regressions. Earlier narrower batches passed 346 tests and then 706 tests; the final count supersedes these.

An exploratory command named nonexistent `tests/test_ui_video_bounded_planner.py` and exited 4 with no tests run. It was corrected to actual existing consolidation/runtime suites above; it is not counted as verification.

```sh
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ui_video_analysis.py task_server/services/ui_video_trajectory.py task_server/services/ui_video_temporal_ownership.py
python3 tests/backend_static_checks.py
git diff --check
```

Results: compile exit 0; backend `ok: True`, **63 checks**; diff exit 0. No frontend, Gateway or Runner implementation was changed.

## Files and self-review

Implementation commit contains only:

- `task_server/services/ui_video_temporal_ownership.py`
- `task_server/services/ui_video_trajectory.py`
- `task_server/services/ui_video_analysis.py`
- `tests/test_ui_video_temporal_ownership.py`

Self-review found the envelope question-loss gap and lack of fragment diagnostic identity; both were repaired with explicit RED/GREEN checks. Temporal guidance precedes diagnostic JSON so existing bounded feedback cannot truncate away the guidance. All new helper functions have executable coverage. Existing dirty CODEX_STATE.md and independently authored diagnostic documents were not staged or modified by this implementation. Parent received a suggested STATE append after checks.

## Limits and handoff

No actual provider calls, all-source rerun, source regeneration, browser/API/device/phone operation, draft creation, deployment, push or model change occurred. AI remains Qwen. Offline tests do not prove visual attribution, that every preserved trigger is physically real, that a supported ordinary result exists, or fresh whole-source/normal-case/device acceptance. Candidate constraints intentionally remain unverified. No source-specific IDs/targets/coordinates/answers are present in the implementation.

No deployment command is issued: task scope explicitly excludes deployment. Parent should independently review base `40dd0690` through implementation `8b26eb24` (and this report commit), then coordinate any separately authorized diagnostics. No rollback, automatic evidence trimming, trigger relocation, causal inference or softening of normal export/review/input/side-effect/printer gates was added.

## Independent review fixes (2026-10-31)

The independent review in `.superpowers/sdd/temporal-ownership-review.md` found two P2 combinations absent from the first test set. Parent authorized both repairs and clarified that exact validated trigger refutation may retire only its corresponding unverified temporal constraint.

1. Rejected schema-invalid question text had been promoted to final review blockers. A 2001-character root/event question could poison final validation despite a valid correction and supported audit. `preserve_questions` now admits only strings that are nonblank and at most 2000 characters, matching the existing valid question domain. It does not strip, slice, resolve, edit or overwrite raw text. Six injected root/event tests preserve the original response exactly, exclude oversized/empty/whitespace malformed entries from blockers, and retain a separate valid question of exactly 2000 characters. The whitespace-event fixture uses an independent envelope shape rejection to enter correction because the existing binder itself does not reject whitespace; binder and legacy behavior remain unchanged.
2. Temporal candidate constraints had survived a later independent explicit trigger contradiction. At the existing validated audit-repair boundary, the matched audited fragment IDs whose `trigger_verdict` is exactly `contradicted` now subtract only their own structural signature multiplicity from the unverified Counter. No target matching or raw/unmatched check is used. Strong positive audit locks are unchanged. `unclear` triggers, outcome-only contradiction and unrelated supported fragments remain protected. Two same-signature fragments plus a contradiction of exactly one leave multiplicity one. A temporal correction followed by a trigger contradiction can now reach a fresh audit after replacement, using exactly the original two corrections.

Review-fix RED/GREEN:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_ui_video_temporal_ownership.py
```

Question-fix RED: **5 failed, 17 passed**, including both oversized question cases failing final validation. GREEN after the blocker predicate and whitespace correction-path fixture adjustment: **22 passed in 0.36s**. Existing valid long-question retention continues to pass.

Audit-retirement RED: **2 failed, 24 passed**, both intended legitimate replacements rejected as `ordinary_candidate_preservation`. After exact validated multiplicity retirement:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_ui_video_temporal_ownership.py tests/test_ui_video_executor_contradiction.py
```

GREEN: **57 passed in 0.51s** (26 temporal tests plus 31 existing executor contradiction tests). The unclear and outcome-only controls continue to reject the changed trigger, including rejection by the stronger positive lock for the supported-trigger/outcome-only case. Original deletion/merge exhaustion tests remain passing.

Review fixes implementation commit: `71f53314` (`fix: respect question validity and exact audit trigger refutation`). Only `ui_video_trajectory.py` and its temporal ownership test file are included.

Final review-fix verification:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_ui_video_temporal_ownership.py tests/test_ui_video_executor_contradiction.py tests/test_ui_video_trajectory*.py tests/test_ui_video_packet_contract.py tests/test_ui_video_audit*.py tests/test_ui_video_business*.py tests/test_ui_video_outcomes.py tests/test_ui_video_to_case.py tests/test_ui_video_consolidation_entry.py tests/test_ui_video_runtime*.py
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ui_video_trajectory.py
python3 tests/backend_static_checks.py
git diff --check
```

Results on the final repaired code: **857 passed in 48.72s**, compile exit 0, backend **63 checks / ok True**, diff exit 0. An earlier question-only intermediate suite passed 822 tests in 49.74s; the 857-test final run includes the subsequent audit-retirement repair and supersedes it. No provider/browser/device/source/deployment/push operations, budget changes or STATE edits were performed. All original limits and legacy behavior remain unchanged. Parent retains review and integration ownership.

## Narrow cross-packet current-context follow-up (spec base 7d9fdd1b)

Read the complete revised temporal ownership spec and `.superpowers/sdd/temporal-cross-packet-context27-followup.md`. This follow-up authorizes only bounded whole-current-packet reobservation when immutable accepted history can remain unchanged. Historical final receipt16 changed trigger/kind and is **not** evidence of a successful unchanged-signature correction of receipt10. No private source replay or new actual provider evidence was performed by this implementation.

Added pure `unsafe_current_correction_conflicts` in the existing temporal helper. It checks every required conflict before correction against accepted pre-packet `events`/`tracks` (the binder makes proposed copies). A system-exclusive violation, missing next interaction, next canonical boundary with historical identity/prefix, unknown historical left owner, or accepted snapshot `max(end_ms, terminal_pts)` later than the current next interaction's preserved trigger fails before spending. Equality is eligible. For a folded prior-plus-current left input, eligibility still uses the accepted prefix snapshot; current fragment presence does not make the prefix mutable. Only a wholly current next independent interaction can supply a potentially revisable evidence-start boundary.

The existing observer branch now invokes that pure classifier before using its unchanged two-correction path. Feedback explicitly preserves accepted history and distinguishes trigger eligibility from the final evidence-start bound; it gives no desired frame answer. `ordinary_outcome_domain`, inclusive `next.start`, signature multiplicity, exact validated refutation, positive audit locks, acquisition ordering, raw receipts, complete valid questions, final global guard, caps and legacy behavior are unchanged. No clipping, rollback or automatic evidence edits occur.

TDD: after repairing generic fixture timestamps to use a shared frame for equality and a distinct sorted source frame for the one-unit case, the focused command produced **6 failed, 27 passed** on the old guard: both safe/equality cases immediately failed, and repeat/signature-loss cases received no bounded correction. The existing one-unit and older hard negative remained passing. After the helper/branch change: **33 passed in 0.43s**. Additional continuation/system/multiple-conflict/lock/final checks then passed. A system fixture was moved into the actual recording-tail boundary so it exercised the unchanged system classifier.

Eight verification groups in the real injected engine (plus pure checks where a valid accepted canonical order cannot naturally construct the malformed ownership case):

1. `test_safe_cross_packet_context_reobserves_only_current_with_immutable_history`: safe prior end/terminal between current context and trigger, exact shared equality; prior raw/fragments/audit and prior event snapshots remain equal, all prior/current rejected valid questions retained, no acquisition before correction, expected overview/prior observe-audit/current observe-observe-audit sequence.
2. Same test's equality parameter and `test_one_unit_unsafe_immutable_floor_fails_before_spending`: equality eligible and final shared bound inclusive; floor one millisecond after trigger fails with one current observation and no current audit/acquisition. Existing `test_cross_packet_fails_without_current_correction_or_prior_mutation` remains a hard negative.
3. `test_eligible_cross_packet_context_still_exhausts_and_preserves_signatures` and `test_cross_context_keeps_independent_signature_multiplicity`: unchanged bad envelopes exhaust exactly two corrections; changed trigger/kind, deleted action or duplicate-signature loss cannot pass. Eligibility never admits an unchanged out-of-domain response.
4. `test_current_left_input_continuation_respects_immutable_prefix_floor`, existing valid continuation controls, and `test_eligibility_refuses_historical_next_prefix_and_absent_next_without_mutation`: folding happens first; safe current suffix may be reobserved, unsafe accepted prefix still fails immediately; historical next-prefix and no-next are refused without mutation. Existing malformed/intervened continuation rejection remains in the trajectory regressions.
5. `test_multiple_safe_required_conflicts_use_one_whole_current_correction` and `test_all_required_conflicts_classified_before_any_partial_correction`: all-safe conflicts use one complete packet correction, a single unsafe immutable conflict blocks the whole transaction before spending. Existing optional-wait ledger tests still pass.
6. `test_system_conflict_is_immediate_even_for_current_only_left` plus the no-next pure control: system/no-boundary violations do not enter a correction exception even if a current observation could otherwise change.
7. `test_positive_current_lock_remains_authoritative_during_context_correction`: an existing supported current trigger lock survives a later temporal context correction; changing its target is rejected by the stronger original lock after the shared two-correction budget. Existing exact-refutation, unclear, outcome-only and exact-multiplicity tests remain passing.
8. `test_final_global_preflight_is_still_a_fail_closed_safety_net`: an injected residual conflict at final preflight fails closed without further correction. Existing legacy, safe diagnostics, immutable receipt and valid/malformed question controls remain passing.

Final focused command:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_ui_video_temporal_ownership.py tests/test_ui_video_executor_contradiction.py
```

**75 passed in 0.72s**: 44 temporal ownership tests and 31 existing executor contradiction tests.

Follow-up implementation commit: `6b032a5c` (`fix: permit bounded current-context ownership correction`), exactly the helper, trajectory service and temporal test file. This report update is committed separately. No analysis/runtime/planning code was touched.

Final expanded command:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_ui_video_temporal_ownership.py tests/test_ui_video_executor_contradiction.py tests/test_ui_video_trajectory*.py tests/test_ui_video_packet_contract.py tests/test_ui_video_audit*.py tests/test_ui_video_business*.py tests/test_ui_video_outcomes.py tests/test_ui_video_to_case.py tests/test_ui_video_consolidation_entry.py tests/test_ui_video_runtime*.py
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ui_video_trajectory.py task_server/services/ui_video_temporal_ownership.py
python3 tests/backend_static_checks.py
git diff --check
```

Final result: **877 passed in 49.31s**, compile exit 0, backend **63 checks / ok True**, diff exit 0. An earlier expanded collection passed 875 tests before the last two duplicate-signature parameters entered collection; the final run above supersedes that count. Self-review confirmed accepted snapshots remain unchanged because binding updates only proposed deep copies, and the classifier is read-only. Final global guard and all prior correction/refutation/question controls remain in place.

No provider/API/browser/source/device/deployment/push action or fresh actual Qwen correction occurred. Previous actual27 remains failed; same-trigger envelope correction has only synthetic evidence. No model or limits changed. Dirty parent CODEX_STATE.md was neither edited nor staged. Independent review and any actual diagnostics remain parent-owned.
