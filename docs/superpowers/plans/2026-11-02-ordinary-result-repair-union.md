# Collect ordinary result repair needs before the single repair

An invalid optional result reference can enter the bounded visual repair before
other, already discoverable result quality concerns are collected. A successful
repair then consumes the only repair opportunity, and later proposal validation
rejects another action's conditional or otherwise unsuitable expected result.

Use one pure collector for both the normal proposal gate and the invalid-result
fallback. Preserve the normal rules for missing stable expected text,
`needs_result_review`, and configuration review. Keep the full set of concerning step IDs separately from candidate indexes,
so legacy plans without indexes still fail the normal quality gate and use the
existing planning correction path. Union mapped candidate indexes with all
invalid result indexes before calling the existing repair. Do not infer
or rewrite expected text: only the visual repair may propose replacement results.

The collector receives the already validated action proposal and its snapshot.
Action, target, value, time, source evidence, raw replies and uncertainties remain
unchanged. Result replacements go through the existing repair validator and then
all normal consolidation gates. Runtime object and print recovery paths keep
their existing exclusions and dedicated guards.

Keep the single repair, eight check maximum, 2..60 image admission, existing
planning passes, timeouts, owners, stage schemas and all other budgets unchanged.
Refuse a union above the limit instead of dropping checks. No new model stage,
new provider dispatch, prompt, Gateway or Runner changes are needed.

Verification plan:

1. Reproduce a too-early wait result plus a valid-reference action whose expected
   text is conditional, sequential or contains unrelated recommendation content.
   Old code must finish the wait repair and fail at the real later result gate.
2. Require one repair containing both concerns, successful revalidation, and exact
   preservation of original actions, evidence, raw questions and audit blockers.
3. Exercise exactly eight combined checks and refusal of nine before dispatch.
4. Run outcome, quality, temporal, planning, whole and runtime focused suites;
   required Python compilation, backend static checks and diff whitespace checks.
5. Compare all protected worktree files, including the full state document, before
   committing only this implementation, its tests and this plan.

This repairs collection order only. Local deterministic tests do not establish
actual Qwen recovery, complete source semantics, draft acceptance or phone results.
Those require separate qualification owned by the coordinating task.

Legacy regression coverage includes conditional, sequential, incidental-content
and missing expected results without candidate indexes. An unchanged bad reply
refuses after two planning calls; a corrected second reply passes. Neither path
invents candidate indexes or calls the indexed visual repair.
