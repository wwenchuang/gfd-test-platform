# Recover business input at a shared action boundary

The normal run30 input-recovery call included only the search field. The model-text input was classified as an ordinary business field, but its end and the following independent keyboard-dismiss trigger were both 27167ms. `recover_business_inputs` discarded the input before building checks because the half-open following-frame interval was empty. Its existing last original input frame was clear and already belonged to that input. The model never received that field for recovery.

Use the existing final input anchor when that interval is empty and the anchor is an actual provided frame at `event.end_ms`, with `end_ms <= next independent interaction boundary`. Equality allows one original frame to show both the input's preserved final value and the next action's trigger. A later image, another field, or an overlapping input end after the independent boundary must not become recovery authority. Ordinary nonempty-interval behavior remains unchanged.

The chosen scope is the small evidence-selection correction. Increasing provider budgets does not repair a field omitted before dispatch. Supplying a manual value would not prove model recovery. The user has already explicitly authorized choosing and implementing the repair without further approval.

## Binding constraints

- Change only business input recovery and its focused regression tests; no model, prompt, provider, protocol, budgets, global gates, source data, or device policy change.
- No value is hardcoded, inferred from a later independent action, copied from model prose, or supplied manually to claim AI recovery.
- Reuse only the actual final input frame when it exactly matches the input end and is no later than the next independent interaction boundary.
- Sensitive/untyped fields, unsafe values, evidence-reference validation, unresolved-model rejection, cancellation, and input immutability retain their current behavior.
- Preserve the original frame's provenance and use the existing two-labelled-image transport rule, with an actual earlier anchor if required; do not synthesize an image.
- Existing source30 and its imported draft/revisions remain unchanged; model proof for this fix must be separately labelled until a fresh normal source completes.
- Phone trial uses only ecbfd645 and printer9888E0094F2A, and must settle before any deployment/restart or additional live diagnostic provider work.

## Verification

First reproduce that an ordinary input with a final original anchor and a next action at the same timestamp is not sent to the model. Then show the normal recovery function requests that field and accepts only its own returned safe value and allowed original ref. Cover a strictly later boundary with no additional original sample, next-field isolation, an input end after its boundary, unknown/mismatched final anchor, protected field/value, invalid returned ref, unclear response, cancellation, and unchanged caller-owned data. Existing focused input/consolidation/runtime tests and mandatory main-chain compilation/backend/diff checks must pass. Fresh task and whole-change review are required before integration. A bounded real-Qwen replay must use immutable run30 inputs and real version30 pixels, with no source/draft/device writes and no answers in its request.
