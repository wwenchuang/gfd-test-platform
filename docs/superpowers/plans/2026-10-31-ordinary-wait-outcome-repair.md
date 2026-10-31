# Ordinary Wait Outcome Repair Implementation Plan

**Goal:** Allow an ordinary `aiWaitFor` with invalid optional outcome evidence to use the existing single visual result repair.

**Architecture:** Extend only `ui_video_outcomes.repair_outcomes` eligibility alongside `aiTap` and `aiScroll`. Keep the candidate's original end, next independent interaction, exclusive system boundary, attached original frames, and final validation unchanged. A returned result must be one valid `aiAssert` per requested candidate; only outcome and reusable expected text may change.

**Tech stack:** Python services and pytest.

## Constraints

- No new model, stage, request class, retry, budget, schema, or prompt.
- Retain at most eight checks, two to sixty images, and one outcome repair.
- Runtime objects, selections, unsupported actions, malformed assertions, unresolved questions, and invalid references remain refused.
- Preserve original candidate timestamps, action, target, value, origin, and uncertainties.
- Local synthetic tests demonstrate platform dispatch and validation only; live semantic and device acceptance remain separate.

## Task: Repair ordinary wait eligibility

- [x] Add a consolidation regression: an early optional wait result must reach visual repair once and accept a new original-frame assertion at the inclusive end/next-interaction boundary. Verify original evidence and questions remain unchanged.
- [x] Extend focused outcome validation coverage to waits, including early, unknown, cross-interaction and system results, malformed responses, missing results, and runtime-object refusal.
- [x] Replace the obsolete temporal test's blanket wait refusal with empty-domain refusal for both ordinary taps and waits, including an exclusive system boundary.
- [x] Run new regressions before editing production and record the expected type-refusal failure.
- [x] Add `aiWaitFor` to the existing helper eligibility set only.
- [x] Run focused outcome, temporal, planning, and consolidation tests, required mainchain compilation, backend static checks, and diff checks.
- [x] Verify protected workspace bytes, self-review, and commit only task files. Report verification and limitations for independent review.
