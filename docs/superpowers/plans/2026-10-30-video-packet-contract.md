# Bounded video packet contract implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Express existing packet ownership constraints at the Qwen audit boundary and make temporal contract correction actionable without admitting invalid captures.

**Architecture:** Keep the current event interval and downstream gates. Derive a narrowed audit schema from canonical oneOf[1] per actual dispatch and attach bounded trusted temporal metadata to the existing correction path.

**Tech Stack:** Python, existing JSON Schema projection/skill/ModelJobs/receipt transport, Markdown prompt, pytest.

## Global constraints

Spec: docs/superpowers/specs/2026-10-30-video-packet-contract-design.md. No source-specific production conditions, automatic clipping/extension, altered model/cap/budget/timeouts/ownership/continuation/audit gates or provider auto retry. Original failing captures must remain rejected. Synthetic corrected replies are never original acceptance. Preserve existing dirty CODEX_STATE prefix and primary249 protected files. All device work root-owned and only designated target.

### Task 1: Request constraints, temporal feedback and complete regression evidence

**Files:**
- Modify task_server/services/ui_video_runtime.py (audit schema helper + business audit dispatch).
- Modify task_server/services/ui_video_trajectory.py (safe indexed outcome feedback at validator failure + existing correction propagation).
- Modify ai_skills/prompts/ui_video_trajectory.observe.md (evidence scope clarity; preserve input and independent action instructions).
- Create tests/fixtures/ui_video_v16_contract_conflicts.json and tests/test_ui_video_packet_contract.py.
- Update tests/test_ui_video_audit_structured_output.py only where canonical equality is superseded by actual per-dispatch narrowing; retain strict stage/scaffold assertions.

**Interfaces:** `_audit_schema(payload)` returns `validate_output_schema(projected_and_narrowed,stage='audit')`; payload carries frames, owned_frame_ids and trajectory shard. Existing run_runtime, bind_packet_events and observe_trajectory signatures unchanged. A trajectory error carries fixed safe model feedback only for already-validated outcome-reference conflicts; existing ValueError/video_model_invalid and trajectory_reason remain unchanged.

- [ ] Capture authentic fixture from R/current-platform/normal-run16/run16: source identity/version/SHA, original media frame metadata, exact entries8/10/30 with entry hashes and their packet ownership derived from overview receipt4. Verify canonical_sha of entries and byte-identical capture; no generated media or claimed corrected source content.
- [ ] Write RED tests for current-owned owner domain, actual-image references/revisit endpoints, exact shard event IDs excluding context claims, empty claims, immutability and strict canonical-scaffold validation. Captured owner048 must be refused at the schema output boundary. Add real skill/child wire hash+raw receipt/cleanup/no-retry checks using existing loopback helpers. Test real business trajectory sharding/correction schema propagation.
- [ ] Write RED test authentic receipt30 fails outcome_reference but exposes event_index1, trigger185, evidence-end187/time51433, outcome-end186/time50867 in fixed bounded feedback; no target/observation/question/arbitrary raw exception text. Existing trigger/input/ownership/evidence gates remain unchanged. Test correction request receives this metadata and fixed causal instruction, with synthetic valid reply clearly marked; exhaustion still fails, never rewrites capture.
- [ ] Run focused new tests and retain RED log before editing production.
- [ ] Implement `_audit_schema` using this canonical narrowing pattern (the canonical projection and validation helpers remain authoritative):

```python
schema = _provider_projection(load_ai_skill_schema('ui_video_trajectory')['oneOf'][1])
refs = [f['frame_id'] for f in payload['frames']]
owned = payload['owned_frame_ids']
ids = [claim['track']['event_id'] for claim in payload['trajectory']]
p = schema['properties']
p['issues']['items']['properties']['owner_frame']['enum'] = list(owned)
p['issues']['items']['properties']['frame_refs']['items']['enum'] = refs
for name in ('first_frame', 'last_frame'):
    p['revisit']['items']['properties'][name]['enum'] = refs
if ids:
    p['checks']['items']['properties']['event_id']['enum'] = ids
return validate_output_schema(schema, stage='audit')
```

Use only for business audit dispatch, including each correction/shard; legacy no-schema audit unchanged. Do not silently fabricate claim IDs for empty arrays.
- [ ] At existing outcome_reference branch construct fixed feedback containing only validated event index/frame IDs/timestamps, e.g. a bounded JSON object with `event_index`, `trigger`, `evidence_last`, `outcome_first`, `outcome_last`, each frame carrying trusted timestamp. Retain `_fail` reason/error identity. Propagate fixed feedback only in outcome_reference correction branch. Explain to re-observe own action/response and preserve following independent action separately; never instruct merely extending outcome or deleting evidence. If set membership/time prerequisites are invalid, generic safe failure remains valid without echoing unknown IDs.
- [ ] Clarify prompt frame_refs scope consistently with unchanged endpoint gate and outcome rule; do not add a competing context field or alter schema.
- [ ] Run focused GREEN, affected trajectory/runtime/stage/receipts/jobs tests (include strict audit and original v14/v15 fixtures), main-chain compile/backend/diff. If any affected expectation needs update, show it is solely the intentional per-packet narrowing; do not weaken semantic assertions. Retain logs and report limitations.
- [ ] Append CODEX_STATE task facts, exclude STATE from implementation commit, self-review diff and commit only named files. Report commit plus tests/RED and evidence hashes in .superpowers/sdd/packet-contract-report.md. No provider/browser/phone/deploy work.

### Root release and external acceptance

- [ ] Fresh task review spec+quality and final full-release review. Resolve material findings before integration.
- [ ] Verify primary protected manifest/prefix, cherry-pick exact reviewed commits, exact committed tree match and mandatory checks; push authorized codex/ui-platform-simple-workflows. No remote main update.
- [ ] Build verified offline ZIP and exact deployment command. Native QA preflight must show source16 terminal; if login expired request credentials only after concrete reviewed package is ready. Do not claim upload/deploy from attempted commands.
- [ ] Verify server ZIP hash, execute deploy.sh, capture full deployed revision and independent public health. Same unchanged full original normal UI regeneration once; no overlapping/repeated generation or deploy while active.
- [ ] Export terminal source17 evidence and verify integrity. If ready, follow normal draft confirmation/static scoring/target compile/dry-run gates, then designated phone only after current account/task/printer binding. Record OLD-absent branch accurately. Report generation, draft and phone separately; no success without actual execution evidence.
