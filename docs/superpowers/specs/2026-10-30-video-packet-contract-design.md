# Bounded video packet contracts

The same full original v16 failed on deployed 434a41ee, observing 5/9, with no draft. Export integrity: 3 manifest files and 46 canonical model records. Independent exact replay reproduces two `audit_issue_owner` rejections in packet001 followed by `outcome_reference` in packet006. Request receipts attest strict audit schema forwarding, not visual correctness or provider enforcement of relational constraints.

The current action interval derives from `frame_refs` endpoints throughout binding, ordinary analysis, continuation and consolidation. Outcome must end at or after that interval. The original frames185/186/187 show cancel tap / dialog / independent confirm tap; captured event1 cites185/186/187 but selects outcome186. It is representable under the existing contract by a model correctly attributing each independent action; platform must not rewrite the capture to manufacture that result.

## Decision

Retain the current interval and evidence gates. Make their request-side constraints and correction feedback consistent. Business audit schema must narrow frame IDs to actual packet images, issue owner to current owned IDs, and checks to actual shard claim IDs. Revisit endpoints remain actual images; owned interval/revisit rules remain authoritative. No provider-facing schema scaffold change. No enum for empty check-claim domain: existing exact empty-check validation remains authoritative.

Provide a fixed platform-generated explanation for invalid outcome references with zero-based event index and exact already-validated frame IDs/timestamps. Never echo target, observation, question, arbitrary exception text or media. Explain both valid repair directions: re-evaluate the action's own evidence range and its own actual outcome; never extend outcome into a later independent action just to satisfy ordering. Preserve raw replies, shared two corrections, calls, revisits and all ownership/audit checks.

Clarify the observer prompt: per-event evidence describes this action and its own response. Following independent actions are available in packet images/context, but their trigger/result must not be inserted into this event. Preserve complete input/edit evidence and independent keyboard actions.

## Alternatives considered

1. Recommended: current contract, dynamic audit leaf constraints, precise correction feedback and clarified evidence scope. Bounded change; still requires actual normal full-source and device acceptance.
2. Remove the temporal check or automatically clip/extend evidence. Rejected: downstream interval validation would conflict, and program cannot infer visual causality from timestamps.
3. Versioned separate action interval and supporting-context evidence. Potential future design; would require coordinated schema, binding, continuation, consolidation and provenance migration. Not necessary for the captured representable event and not this repair's scope.

## Verification and acceptance

Commit authentic receipts8/10/30, original frame metadata and packet ownership with canonical hash provenance. Tests must continue to refuse the original captures. Prove narrowed schema appears on actual skill/ModelJobs wire with matching independently reconstructed receipt hash; typed out-of-domain output rejection preserves raw receipts/no automatic retry. Exercise real trajectory audit shards and correction, including empty claims. Verify outcome feedback reaches the existing correction request, remains bounded/safe, and the uncorrected output still fails when two corrections are already spent. Any corrected replies in tests are synthetic regression evidence only.

Run affected trajectory/runtime/schema/receipts/ModelJobs tests, main-chain compile, backend checks and diff check. Fresh task and whole-release review precede integration. Preserve all primary-worktree protected files and append-only state; integrate only exact reviewed commits and verify tree equality. Deploy once with exact package and deployed revision evidence, then normal UI regeneration of unchanged full original once. A generated draft is not a phone verdict: normal draft gates and only designated phone/printer remain required.

## Fixed constraints

All Qwen models unchanged. Grounding/audit cap128, existing timeouts/calls/shared2corrections/revisits unchanged. No source-specific frame repair, source/goal/answers mutation, manual draft construction, gate relaxation, Gateway/router refactor, new Runner actions, alternate device or physical print. Only 9888E0094F2A, Android phone ecbfd645/runner mac-android-adouceshi after current binding checks. OLD attributable test task: cancel if present, otherwise continue and report unexercised branch. No final print authorization.

User already authorized good solutions and verification without further approval. This document records the decision; no additional design confirmation is required.
