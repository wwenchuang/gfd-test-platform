# Video audit structured output implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this task with independent task and final release review.

**Goal:** Prevent business-first audit requests from drifting into unrelated JSON formats by using the existing canonical audit contract at provider and skill-validation boundaries.

**Architecture:** Reuse the strict schema transport already used by semantic/grounding. The audit contract is exactly the provider projection of ui_video_trajectory oneOf[1]. Only business-first runtime opts into supplying this audit schema; historical audit calls without a schema retain their existing behavior. Stage-specific validation prevents crossing observer and audit contracts. Original-pixel audit, per-event binding, ownership and normal execution gates remain authoritative.

**Tech Stack:** Python, existing ModelJobs IPC, Qwen Gateway Node, pytest/node tests.

## Evidence and decision

Verified v15 failed observing7/9 on d01cf791. Full export integrity verified by root. Receipt40 packet008/call19 is valid JSON with keys reasoning/action/outcome, while the existing audit envelope requires checks/issues/multiple_scenarios/revisit. Its provider attestation says response_format=json_object and output_schema_sha256=null. All20 calls have receipts; corrections were used for input_continuation (packet002) and interaction_as_state (packet008), so this last rejection had no correction remaining. Increase of image/correction budgets would not enforce the missing format. Preserve rejected text verbatim and reject it under the new audit contract; do not turn action=accept into confirmed checks. User authorizes fixes and verification without additional confirmation.

## Global constraints

- All models remain Qwen; audit qwen3.8-max, temperature0.1/thinking1024/max_tokens10000 unchanged.
- Keep grounding/audit128, other stage caps,120s model deadline,1800/1200s runtime deadlines,24 trajectory calls,2 shared corrections,4 revisits,64 new frames,10MiB assets/16MiB IPC. No provider retry/fallback/JSON repair.
- Preserve source/version/owner/CAS, original raw receipts/proofs, attestation hashes, child cleanup and cancellation. No accepted data inferred from reasoning/action/outcome.
- Preserve immutable claim binding, issue ownership, independent raw-pixel audit, required actions/inputs and normal review/static/score78/phone gates. No changed video/goal/answers, manual input fill or deleted business steps.
- Audit schemas may match only canonical audit scaffold; semantic/grounding schemas may match only canonical observer scaffold. Cross-stage substitution and extra properties remain rejected before dispatch.
- Legacy audit with no output schema remains unchanged; the business-first runtime must always supply the strict audit schema on every audit shard and correction. Keep all unrelated stages unchanged.
- Work in this isolated checkout. Protect dirty CODEX_STATE.md and primary checkout249 dirty files. Root handles integration/push/deployment/same-source normal run and designated-phone acceptance.

## Task1: Closed audit schema capability and business runtime wiring

**Files:** task_server/services/ui_video_stage_policy.py, ai_skill_service.py, ui_video_runtime.py; ai-gateway/server.js. Modify ui_video_model_jobs.py only if necessary for stage-schema path verification. Add focused tests/test_ui_video_audit_structured_output.py, tests/fixtures/ui_video_v15_audit_shape.json and affected Gateway tests. Use existing tests as integration helpers. Avoid large refactors.

**Interfaces:** Extend validate_output_schema with explicit stage selector defaulting to existing observer behavior. For audit the expected contract is `_provider_projection(load_ai_skill_schema('ui_video_trajectory')['oneOf'][1])`; normalize and match using the existing bounded schema subset. Validate supplied audit schema at validate_video_stage, skill request/output validation, Gateway dispatch/provider format and attestation. Audit stage with UNSET schema stays historical; explicit null is invalid. Skill only translates VideoStageOutputError into existing SkillSchemaError; no arbitrary exceptions/messages. Business runtime passes canonical projected audit schema to all audit calls via jobs.run; no new model or schema authored facts.

- [ ] Step1: Copy authentic receipt fixture from .superpowers/sdd/v15-audit-shape-replay.json, preserving its bytes/provenance. Add failing tests that business audit reaches provider as strict json_schema with canonical audit root and schema hash; the actual captured JSON fails typed output validation with preserved receipt. Test observer-as-audit and audit-as-observer rejected, unexpected fields rejected, explicit null rejected, no-schema historical audit unchanged. Use actual validators and wire, not mocked validation assertions.
- [ ] Step2: Run focused tests to record RED in .superpowers/sdd/audit-structured-output-logs/. Verify failure is missing strict audit capability. Exercise actual run_ai_skill and real ModelJobs child/loopback proof/receipt/error/cleanup/no-auto-retry, plus business runtime shard/correction schema propagation. Synthetic images/corrected replies are offline regression evidence only.
- [ ] Step3: Implement minimal stage-aware reuse. Python gateway request includes outputSchema only for supplied audit schema; Gateway strict provider response_format and receipt schema hash use the audit scaffold selector. Existing legacy json_object audit remains possible only when schema absent. Business runtime builds the audit schema from canonical source and supplies it automatically. Do not edit prompt/contract to accept captured wrong output. Keep per-event validators after structured output validation.
- [ ] Step4: GREEN focused Python/Gateway suites plus affected stage wire, schema classification, ModelJobs, trajectory, transport, runtime, business runtime and cap tests. Run one heavy suite at a time. Required main-chain+changed py_compile, backend_static_checks.py, ai_gateway_static_checks.py, JS syntax and git diff --check. Report exact commands/counts; avoid full unrelated suite repeats.
- [ ] Step5: Self-review and write .superpowers/sdd/audit-structured-output-report.md including RED/GREEN, fixture/receipt authenticity and synthetic-test limits. Append CODEX_STATE.md preserving prefix; exclude it from scoped commit. Commit code/tests/fixture only. Root independently reviews task and whole release, integrates exact tree, deploys verified offline bundle, and runs one unchanged full original through normal UI before gates/phone.

## Acceptance after deployment

- [ ] Fresh deployed revision and health verified; no active run restarted.
- [ ] Same original source normal regeneration once; audit provider receipt proves strict json_schema plus correct schema hash and model, no semantic acceptance from format alone.
- [ ] Full generated content checked against fixed baseline, then normal draft review/static/score gate and phone ec bfd645 (actual ID ecbfd645) / printer9888E0094F2A. Verify fresh account/task binding before any cancellation, no final physical print.
