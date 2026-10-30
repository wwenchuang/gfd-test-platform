# Input continuation guidance implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development with a fresh implementer, task review and complete-release review.

**Goal:** Give Qwen one consistent input identity rule and retain unresolved correction constraints across nested schema retries.

**Architecture:** Existing binder gates remain authoritative; add diagnostic metadata and correct prompt semantics. The inner ask loop keeps a bounded immutable parent slot and replaces only its latest-inner slot.

**Tech Stack:** Existing Python trajectory/skill transport, Markdown prompt, pytest.

## Global constraints

Spec: docs/superpowers/specs/2026-10-30-video-continuation-guidance-design.md. No auto-renaming/coercion/capture mutation/source-specific production conditions; no changed action/continuation/field/page/order/outcome/ownership/input/audit/positive-trigger/canonical-schema/draft/Runner gates; Qwen-only, zero provider retry, all packet/global correction/call/image/revisit/time budgets unchanged. Existing prior_events is the identity source; no duplicate runtime continuation contract. Preserve dirty STATE prefix/primary249 and never store credentials. Root owns primary integration/push/deployment/same-original normal generation and designated phone/printer binding.

### Task 1: Consistent prompt, safe continuation feedback and nested constraint retention

**Files:** Modify task_server/services/ui_video_trajectory.py and ai_skills/prompts/ui_video_trajectory.observe.md. Create tests/test_ui_video_continuation_guidance.py and tests/fixtures/ui_video_v18_continuation_conflicts.json. Existing tests may be adjusted only for intended additive feedback/prompt wording, never weakened gates.

**Interfaces:** bind_packet_events and observe_trajectory signatures unchanged. Add model_feedback at existing input_continuation failure containing event_index, bounded fixed failed_checks, prior eligibility and expected identity/history references. Preserve original short-circuit predicate behavior; helper must not raise on missing history or echo arbitrary model text. Expected both page_before/page_after come from history[-1].page_after, target from prior_events[-1].event.target. Return literals only for short safe opaque IDs; use fixed references otherwise. No input values or raw prose in metadata.

- [ ] Capture exact run18 entries4/6/9/10/11/12/13/14 (proofs+receipts) from R/current-platform/normal-run18/run18; reuse media299 from verified v16 fixture with explicit provenance. Derive and label packet ownership from captured overview. Verify all canonical hashes and original dictionary equality.
- [ ] Write and run RED before production: authentic10/14 rejected input_continuation with only page-after equality failing;12 fails typed semantic schema $.events[4].trigger_frame for foreign065; exact captures unchanged. Test metadata names offending index and stable expected end-page reference; malicious/long page/target/question/value prose never echoed; no-history/intervening action/field/page/boundary/order/gap/outcome negative controls still refuse.
- [ ] Write real observer-loop RED: raw6→10→12→synthetic corrected10 through real skill output validation shows outer continuation feedback retained after nested schema correction. Model corrections are explicitly synthetic; repeated authentic10/12/14 still exhaust the existing current scope. Cover empty parent, two inner errors replace latest only, parent appears once, maximum12000-character slots/truncation markers and unchanged stage/call/packet budget accounting. Existing overview/audit behavior must remain compatible.
- [ ] Verify actual ai.run_ai_skill and actual Gateway buildUserMessage serialize both parent/latest constraints plus current frames, without replacing the typed error identity/raw receipt proof semantics. Retain RED logs; final archived-baseline comparison is supplemental only.
- [ ] Implement minimal indexed safe feedback at existing failure and preserve existing conditional ordering. In ask capture `outer_feedback=context.get('validation_feedback','')` ONCE before its while; on correction add bounded parent only if nonempty, latest inner bounded separately, no recursive concatenation. Explicit truncation flags if relevant. Do not add provider calls/retries or alter exhaustion locations.
- [ ] Correct prompt input rule: continuation_of copies prior event_id, target copies prior event target, BOTH page fields copy prior page_after. Clarify states' editing intervals do not rename stable continuation page identity. Real boundaries forbid joining; preserve independent actions, full input evidence/uncertainty and revisit. Parent and current validation constraints apply together; repairing schema alone does not resolve parent semantic failure.
- [ ] Run focused GREEN, affected trajectory/runtime/state/contract/wire/jobs/executor checks and required mainchain+changed compile/backend/diff. Record exact commands/results, fixture SHA and limitations in .superpowers/sdd/continuation-guidance-report.md and logs. Append STATE only; self-review and commit only named files, excluding STATE. No external/primary mutations.

### Root release and real acceptance

- [ ] Fresh task and whole-release reviews; tested fixes for material findings. Protect249 and originalSTATEprefix; integrate exact reviewed commits into codex/ui-platform-simple-workflows; exact-tree comparison and mandatory checks, push and remote-ref verification.
- [ ] Build/check offline ZIP; server preflight18 terminal, native upload/server hash/deploy/full local+public exact revision health. Same full original normalUI generation19 once; never restart/redeploy/retry an active analysis.
- [ ] Export terminal19 and validate all manifests/canonical shards; inspect whole fixed manual business baseline, inputs and questions. Ready source proceeds through normal draft review/static/score/target compile/dry-run and designated phoneecbfd645/runner mac-android-adouceshi/printer9888E0094F2A, current task binding and no final physical print. OLD-absent branch follows user condition with honest coverage. If failure, use exact new evidence before another fix.
