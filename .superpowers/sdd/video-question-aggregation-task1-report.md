# Video question aggregation Task1 report

Starting HEAD13907ad3. Requirements: current amended `docs/superpowers/specs/2026-10-31-video-question-aggregation.md` and matching plan. Root explicitly expanded Task1 after downstream inspection to include exact-text business draft question deduplication under unchanged100 limit and bounded immutable raw-root provenance. No prompts/docs/STATE touched by implementer, no provider/source/API/browser/device/deploy/push or extra agents. Parent's concurrent dirty files remain excluded.

## Changes and invariants

`validate_analysis` gains keyword-only max_questions=30, accepting exact ints30 or128 only. AI/model payload cannot select the internal policy. Only the final business trajectory aggregation explicitly passes128; legacy and all other validators retain30. No root text is sliced, summarized or dropped. Exact128 passes/129 fails. Root overflow keeps the existing safe public video_model_invalid code with closed analysis_reason=root_question_limit and numeric count/limit. Trajectory emits existing capacity_refusal diagnostics with reason=trajectory_questions/count/limit, enabling existing runtime failure accounting to retain the precise private cause without model text or error-string logging. Malformed options fail video_analysis_options_invalid. Event10, default50/explicit120 events, references/actions/text bounds and independent gates remain.

Business draft presentation is selected only by server-frozen video_runtime_selection.mode=video-business-first-v1, which survives consolidation, normal-worker ready validation and normal private-source import. Exact-identical root/event question text is displayed once with ALL affected1-based event indices and time-range anchors. Repeated question text within one event does not repeat an anchor. Distinct or unmatched questions keep their original presentation; no semantic matching. Event-only questions keep the original prefix. Step review_state stays needs_review; sensitive-input/manual placeholders remain unresolved questions.

Every original event question remains in snapshot.events, including repetitions; business snapshots additionally retain raw_root_questions as an unchanged deep copy with list<=128 and each string<=2000. Shared overflow diagnostics apply to this bounded copy. No new raw model content or credential access. Existing normalizer still enforces final100 questions,50 steps and2000 characters per presented question. Long combined anchors or >100 distinct blockers still fail; no limit increase or truncation. Legacy/nonbusiness draft path remains unchanged and does not gain raw_root_questions. No source execution/compiler/safety/goal/expected-review gates altered.

## TDD

1. `python3 -m pytest -q tests/test_ui_video_question_aggregation.py`: RED27 (missing explicit policy, default overflow diagnostics, business39/128 aggregation); minimal validator/trajectory implementation ->GREEN27/0.40s.
2. After root's downstream scope amendment: `python3 -m pytest -q tests/test_ui_video_question_presentation.py`: RED8FAIL/2PASS, including107-question draft rejection, absent all-anchor grouping/raw root copy, normal worker failed status and import failure. Exact-only business presentation implementation ->GREEN10; combined aggregation/presentation37PASS/0.39s.
3. Snapshot129 overflow precise-diagnostic regression: `python3 -m pytest -q tests/test_ui_video_question_presentation.py -k bounded`: RED1FAIL/3PASS (missing closed overflow attributes); shared bounded count helper ->GREEN with final covering suite below.
4. Final controls additionally cover exact event10, explicit120/121, and model payload cannot select policy. No substring-mirroring tests. Meaningful original synthetic shape39events/39rootquestions/max4eventquestions, full actual trajectory and independent-audit aggregation39/128, legacy39 rejection,129 safe diagnostics, malformed/overlarge policy/type/text/reference/action/event gates are exercised. Draft controls prove107plain+prefixed entries ->72 lossless questions, repeated questions across events/all anchors, exact-only matching, placeholder retention, final100 rejection for101 distinct questions, legacy unchanged, normal worker ready validation and normal source import with ready=false.

## Read-only actual26 evidence controls

Input reconstructed-final-before-validation.json SHA256 daa435bb3332af0438c4058d5084a4b9ef71e648e295312e21c198aa6f0a76ad under `/Users/adouceshi/Documents/projects/video-reliability-20261026/new-touch/current-platform/normal-run26`. Read actual source.json and5 canonical shards plus reconstructed payload; all7 file hashes unchanged after each in-memory control. No source/evidence files written or replay helper executed by this task.

- Actual39events/39rootquestions/max4eventquestions: default30 rejects with root_question_limit/count39/limit30; explicit128 retains all39 root questions and39 events using authoritative stored media timestamps.
- Simulated next consolidation uses the existing exact root+event blocker union excluding the existing input manual placeholder from that union, not from events/presentation:72 immutable root blockers and35 original event-question occurrences (107 before safety/goal/reuse). Normal draft proposal returns79 questions including all existing safety/goal/reuse additions. Every blocker text and every associated event-index/time anchor verified; raw_root_questions and all snapshot event-question lists retained; all steps remain needs_review. No AI consolidated output, expected predicates, provider audit or source visual correctness is fabricated.

These controls diagnose deterministic local aggregation/presentation gates only. Root owns full24-saved-response integrated replay, real whole-packet positive/negative Qwen probe, independent review, integration and later unchanged normal original27/draft/SDK/phone acceptance. Original26 remains failed and immutable.

## Verification commands

- `python3 -m pytest -q tests/test_ui_video_question_aggregation.py tests/test_ui_video_question_presentation.py tests/test_ui_video_to_case.py tests/test_ui_video_trajectory.py tests/test_ui_video_runtime_evidence_reuse.py tests/test_ui_video_business_runtime.py tests/test_ui_video_whole_context.py tests/test_ui_video_outcomes.py tests/test_ui_video_business_inputs.py tests/test_ui_video_runtime.py tests/test_ui_video_executor_contract.py tests/test_ui_video_reconcile.py` —602PASS/22.56s.
- `python3 -m pytest -q tests/test_ui_video_question_aggregation.py tests/test_ui_video_question_presentation.py` —39PASS/0.44s after final shared diagnostic helper and two additional boundary controls; these overlap affected suite, not39 additional unique tests.
- `python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ui_video_analysis.py task_server/services/ui_video_trajectory.py` —PASS.
- `python3 tests/backend_static_checks.py` —63checks PASS.
- `git diff --check` —PASS.

Commit scope: analysis/trajectory services, two new focused test modules, this report only. No known implementation blocker; bounds intentionally still reject excessive distinct blockers or overlong anchored presentation. Please perform fresh independent SPEC/QUALITY review before integration.
