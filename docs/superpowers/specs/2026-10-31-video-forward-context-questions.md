# Forward context audit questions

## Evidence and decision

Original run28 failed in packet002 after three actual Qwen audit replies put issue.owner_frame in attached future context (081/082) rather than current owned048..079. All four checks were present. Root reconstructed the exact dynamic schema hash carried in each actual Gateway receipt and reproduced the owner leaf failures; all25 canonical records and two workspace manifests passed integrity checks. The one proactive input-tail decode in packet003 was never reached. Neither a source timeout nor image acquisition caused this failure.

Alibaba's current structured-output documentation explicitly states that multimodal inputs automatically fall back from JSON Schema to JSON Object, so the provider cannot enforce our narrowed enum: https://help.aliyun.com/en/model-studio/qwen-structured-output . Local validation remains mandatory.

Considered: (1) another ownership prompt / repeated correction, already present and failed three times; (2) deterministic forward-context question routing while preserving all local evidence gates, selected; (3) deleting invalid issues or publishing a partial case, rejected because it loses evidence or bypasses gates. User has explicitly authorized implementing and verifying good solutions without further approval.

## Scope and invariant

BUSINESS runtime alone enables forward_context_issues=True. Direct/legacy observe_trajectory and _audit_schema calls default False and retain strict current-owned issues. Explicit malformed capability or enabling without a business timeline fails before any provider call. No environment switch, public endpoint, new model, extra provider pass or action is introduced.

A future issue is never a validated missing action, a positive check, an execution step, or permission to act. It is unverified model finding data with immutable origin and exact question text. Existing current-owned issues and all checks retain their exact current validation, correction, positive-lock, temporal, planning, input and review gates. Past-context issues remain invalid; accepted history is never rewritten. No source-specific rule or expected action is encoded.

## Ownership and validation

Freeze original frame-to-packet ownership from the existing make_packets result before processing. Build the mapping from actual packet-owned IDs, validate uniqueness and full original coverage, and retain the frozen packet order/cuts. Later new frames continue to belong to the current packet under the existing loader; never repartition originals.

A forward finding is eligible only when its owner_frame is an actual attached context frame, is contained in the same issue.frame_refs, belongs to a strictly later frozen packet, and has timestamp at or after the current half-open owned end. All ordinary issue shape, kind, maximum30, question nonempty/max2000, actual ordered unique1..8 attached refs apply before partitioning. Unknown IDs, unattached IDs, past context, missing owner ref, forged owner mapping, malformed role/range, invalid current-owned metadata fail closed. No owner reassignment, value repair, filtering rejected data into success, or business-fact inference.

The runtime dynamic schema may narrow issue.owner_frame to the union of current-owned IDs and independently derived eligible future-context IDs ONLY on this business capability. Revisit refs and check IDs remain unchanged. The actual business runner constructs trusted packet-audit-reference-v2 metadata with separate current issue-owner IDs, future issue-owner IDs and actual image/check IDs; overwrite any incoming model-like metadata. Existing v1 prompt behavior stays strict. Conditional v2 prompt text explains that future findings are unresolved questions for the actual later owner, cannot create a current issue/action, and cannot be cleared by later supported checks.

## Derived queue and retention

Keep every admitted audit response/shard raw object unchanged. Partition validated issues into current-owned versus forward findings. Only current-owned missing_action participates in CURRENT packet repair (including both normal and no_new_frames paths), and repair feedback issues are current-owned. All current checks/contradictions still behave identically. Future findings never spend a current correction merely because their owner is later.

After a WHOLE audit reply passes envelope, revisit, check binding and issue validation, immediately append each future finding to a separate derived ledger. Include source packet, actual target packet, original issue index/kind/owner/refs/question, audit call and available proof/receipt references; mark classification unverified_forward_context_question and instruction_authority=False. Preserve this even if a later decode/re-observation/shard replaces the candidate. Derive future queues without mutating raw receipts, states, events, original questions, frame roles or cuts. Multiple actual occurrences retain provenance even if the final ordinary question list deduplicates identical full text.

Deliver exact queued findings to the target packet's observer and independent audit as explicitly unverified data. Do not feed another packet's checks as confirmed facts. A target's complete owned images still undergo ordinary independent audit, including empty current claims, and its own missing-action/contradiction findings still use the existing correction path and positive locks. A silent or supported later audit MUST NOT clear the handed question. Every admitted forward question remains in final uncertainties and existing needs_review/manual-confirmation gates. In particular a future kind=missing_action remains an unresolved blocker until normal evidence review; routing is NOT proof that the action was found. No automatic question resolution, omission approval, or source/case acceptance is added.

Keep the derived ledger in successful trajectory_audit. For failure provenance emit bounded diagnostic references linking the original receipt/issue index and actual target, without model prose in error codes. Original raw receipt remains canonical. Do not manufacture provider refs for platform operations.

## Bounds and unchanged gates

Do not truncate questions, silently drop handoffs, raise caps, or add retries. Keep original trajectory32 calls, shared provider38 for this9-packet source, per-call120s, original source4500s, existing per-packet two corrections, shared4 acquisition attempts/64new frames, packet128images/10MiB, and current diagnostic bounds. Per-response issues30/question2000/refs8 and whole business question128 remain unchanged; final draft100questions/2000chars/50steps and all question grouping/provenance remain unchanged. Existing request size/time admission remains authoritative. True overflow fails closed with capacity evidence, no fallback.

No Gateway/model policy change, source file/goal/baseline mutation, manually authored generated answer, device input, old history rewrite, or unrelated refactor. Only designated printer9888E0094F2A and phoneecbfd645 may later execute; no final print is in the original video.

## Verification and acceptance

TDD generic controls: default strict legacy, future/past/offimage/unknown/duplicate/owner-not-in-refs/malformed domains, exact raw immutability, trustworthy ownership, current missing-action still repairs, future missing-action never repairs current, delivery to later observer/audit, complete audit with empty claims, later silence/supported cannot erase the exact blocker, acquisition/reobserve/sharding retention, all question/attempt/call caps, runtime reference metadata override and schema domains, failure provenance.

Root additionally replays actual run28 receipts12/14/16 without changing their bytes through the new derivation. This is historical protocol evidence only, not fresh Qwen/source acceptance. Independent task and whole-release review, mandatory compile/backend/diff checks, protected primary worktree integrity, correct branch/revision/package/server SHA/livehealth precede one unchanged-source regeneration. Only a complete valid case, fixed manual baseline comparison, normal draft review, installed SDK parse and designated phone execution can establish end-to-end acceptance. Report deployment, generation, review and phone separately.
