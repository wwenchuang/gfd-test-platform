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
