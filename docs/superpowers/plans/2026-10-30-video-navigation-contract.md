# Video navigation contract implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development task-by-task with fresh task and final reviews.

**Goal:** Repair actionable navigation classification feedback and isolate correction scheduling between independent business packets without admitting invalid evidence.

**Architecture:** Keep canonical schemas and all validation gates. Add a single trusted pair-policy projection, safe indexed conflict feedback, and packet-local business correction scopes with cumulative accounting.

**Tech Stack:** Existing Python observer/runtime, Markdown prompt, pytest and loopback transport.

## Global constraints

Spec: docs/superpowers/specs/2026-10-30-video-navigation-contract-design.md. No source-specific production conditions, automatic coercion, changed canonical schema scaffold, changed accepted action/kind pairs, input/ownership/evidence/audit/positive-trigger gates, model/provider fallback, provider retries, image limits or global24-call/1200s engine/RuntimeBudget/request timeout changes. Business-only correction scope changes as specified; legacy global2 unchanged. Keep immutable original captures. Preserve dirty CODEX_STATE prefix and protected primary249; root owns integration/deployment/same original and phone. Only9888E0094F2A is designated printer, phoneecbfd645/runner mac-android-adouceshi; current binding required.

### Task 1: Coherent action contract, conflict feedback and scoped correction budget

**Files:** Modify task_server/services/ui_video_trajectory.py, task_server/services/ui_video_runtime.py, ai_skills/prompts/ui_video_trajectory.observe.md. Create tests/test_ui_video_navigation_contract.py and tests/fixtures/ui_video_v17_navigation_conflicts.json. Adjust existing affected test expectations only for intentional business-local scope. No unrelated production files.

**Interfaces:** bind_packet_events and observe_trajectory signatures unchanged. A helper in trajectory exposes a fresh JSON-safe action contract and another attaches sanitized metadata to existing ValueError/trajectory_reason errors. Runtime imports the contract helper only on existing business grounding dispatch and overwrites payload action_contract. Use exactly these current pairs: aiTap/aiScroll allow touch,pressed_control,navigation,edit,uncertain; aiInput only edit (value remains empty); aiAssert/aiWaitFor allow automatic,uncertain. Preserve existing error precedence for malformed shape/ownership/refs/questions/text and current pair error reasons. Fixed feedback may enumerate all safely known mismatches (max50 events) but must never echo arbitrary text/unknown IDs or mutate raw input.

- [ ] Capture exact exported entries4,28,30,38,40 and associated proof37/39 from R/current-platform/normal-run17/run17. Preserve canonical hashes/raw content. Reference existing v16 fixture media299 only after exact equality with original source-document; record provenance/fixture SHA. Include derived packet008 ownership explicitly labeled.
- [ ] Write RED tests: original38/40 still raise interaction_as_state, both offending indices2/3 safely reported; no raw target/observation/question leaks or foreignframe echoes; pair matrix behavior unchanged for all action/kind combinations including input contract. Actual business dispatch overwrites spoofed action_contract; prompt/actual skill+Gateway text contains trusted policy, current images and fixed correction metadata. Retain strict schema/raw receipt semantics.
- [ ] Write real observer-loop RED tests for multi-packet independent repairs exceeding cumulative2 but eachscope<=2; single packet third invalidresponse stillfails; schema/bind/audit/shards/revisit retries share same packet scope; overview separate scope; legacy global2 unchanged; total corrections_used cumulative; global24/1200 limits unchanged. Replay actual run17 envelopes in real engine; only explicitly synthetic model correction can pass, no capture edit/promotion.
- [ ] Run new test module before production edits; retain RED log.
- [ ] Implement action contract helper from exact policy above; in existing binder attach sanitized indexed conflicts to existing pair-error branches (interaction_as_state, automatic_interaction, input_contract). Keep unknown/invalid structural failures generic. In existing outer correction branch append safe metadata and fixed causal instruction for these errors. No platform rewriting of model actions.
- [ ] Set correction_scope_start=0 near cumulative corrections initialization. A helper checks `corrections-correction_scope_start < 2`. In business mode only, set scope start to current cumulative count ONCE at beginning of each packet, outside all re-observation/revisit/shard loops; overview remains initialscope. Replace all three corrections>=2 checks with this policy and retain existing increment locations/reasons. Never reset cumulative corrections. Add bounded scope/count diagnostics and final policy metadata; retain calls>=24/time>1200 as existing refusal.
- [ ] In runtime business grounding copy and overwrite payload action_contract from trusted helper. Prompt defines active navigation vs automatic response, operation-before equivalent visible control, and forbids changing kind/deleting nav/guessing control to satisfy contract; all real uncertainties/revisit policy retained. Use generic examples only, no original filenames/frame constants/business strings.
- [ ] Run focused GREEN, affected trajectory/runtime/audit/stage/receipts/jobs/transport tests; mandatory mainchain+changed compile/backend/diff and Gateway checks when release touches Gateway. Save commands, full outcomes and limits in .superpowers/sdd/navigation-contract-report.md/logs. Append STATE only, self-review and commit only scoped implementation files.

### Root release and normal acceptance

- [ ] Fresh task review and full release review; resolve material findings with tested fixes before integration.
- [ ] Verify primary249/originalSTATEprefix and no changedpath overlap; cherry-pick exact reviewed commits into codex/ui-platform-simple-workflows, compare committed tree and run required checks. Push and verify exact remote ref; do not tell user main is updated when it is not.
- [ ] Build verified offline ZIP. Native QA preflight source17 terminal; upload/hash/extract/deploy.sh/full local and independent public revision health. No active-run redeploy/restart. Same full original normal UI regenerate once; keep read-only monitoring.
- [ ] Export terminal18, verify ZIP/manifests/all canonical shards and inspect full business flow against fixed baseline. Ready source follows normal draft review/static/score/target compile/dry-run, then designated phone with current account/printer/task binding. Report OLD-absent branch accurately. If failure, diagnose exact layer without blind retries or claiming completion.
