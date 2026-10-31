# Bounded Causal Question Recheck Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give an internally unsettled causal attribution one independent original-image recheck before packet commitment, retaining truthful review when evidence or budget cannot support correction.

**Architecture:** A pure selector prepares a limited original-frame neighborhood only after normal complete audit stabilizes. The existing audit stage performs one partial check; exact refutations enter full normal correction with immutable prior locks, while every actual dispatch remains on the source's existing budget and clock.

**Tech Stack:** Python, existing trajectory/runtime/BusinessJobs/RecordStore, existing Qwen audit JSON schema, pytest. No new dependency or provider.

## Global Constraints

Spec: `docs/superpowers/specs/2026-10-31-causal-question-recheck-design.md`.

- 32 NORMAL trajectory calls; MAX2 partial causal rechecks per source and1 per packet; explicit total<=34. This is root's explicit quota decision, not an implicit extension of normal32.
- Global RuntimeBudget remains38 for this9-packet source; original4500s source deadline and480.5s engine tail unchanged. Unknown transport remains charged.
- 24 unique original JPEGs per partial; no acquisition, retry, fallback, model/budget increase or manual frame/answer injection in partial.
- Partial cannot satisfy full coverage. Prior locks/packets/questions immutable; exact contradiction cannot be overwritten by newer support.
- Preserve97592a71, dirty CODEX_STATE, existing a773 exclusion, source and physical-device safeguards.
- PLAN ONLY now: no staging/commits/provider work. Execution commits only after root reviews exact diffs. Do not auto-deploy after tests.

## File map and execution boundary

Create `task_server/services/ui_video_causal_recheck.py` (pure selection/merge/helpers), `tests/test_ui_video_causal_recheck.py` (pure and real-controller fixtures). Modify `ui_video_trajectory.py` (one optional branch/counters), `ui_video_runtime.py` (strict partial adapter/admission), `ui_video_business_jobs.py` (explicit one-attempt option), and append one conditional paragraph to deployed-baseline `ai_skills/prompts/ui_video_trajectory.audit.md`. Do not transplant this worktree's unqualified prompt wholesale onto primary.

No output-schema scaffold, Gateway stage table, receipt policy or thinking option changes: use existing audit stage and canonical audit schema with narrowed leaf enums. Partial scope is trusted payload/record metadata. Its real output_schema_sha256 and image_count must be independently attested through the existing pipeline.

The snippets below are complete proposed additions/replacements, not executed implementation. If current surrounding code no longer matches, stop and reconcile the diff with root; do not improvise a broader refactor.

### Task1: integrated bounded causal-question recheck

Implement all four step groups as one coherent feature. Do not commit/review an unused helper or a partially wired branch separately. Complete one task review and then final whole-change review.

#### Step group A: pure selector and contradiction dominance

**Files:** create helper and test module above.

**Interfaces:** `prepare(packet, claims, prior, audit) -> dict|None`; `combine(full, partial) -> dict`; `identity(track) -> str`. Inputs are already validated ordinary audit objects. RuntimeBudget owns availability/time admission; the controller owns normal/partial quotas. There is no second pure admission predicate.

- [ ] Write this initial failing test in `tests/test_ui_video_causal_recheck.py`:

```python
import copy
import pytest
from task_server.services.ui_video_causal_recheck import prepare, combine


def sample():
    fs=[{'frame_id':f'f{i}', 'timestamp_ms':i*100} for i in range(10)]
    def claim(eid,action,kind,refs):
        return {'track':{'event_id':eid,'action':action,'target':'visible target','value':'',
            'trigger_frame':refs[0],'trigger_kind':kind,'continuation_of':'',
            'frame_refs':refs,'required_frame_refs':[refs[0],refs[-1]],
            'allowed_frame_refs':refs,'outcome':{'frame_refs':[refs[-1]],'observation':'state'}}}
    claims=[claim('a','aiTap','pressed_control',['f1','f2']),
            claim('b','aiWaitFor','automatic',['f2','f3','f4'])]
    packet={'packet_id':'p','frames':fs,'owned_frame_ids':[f['frame_id'] for f in fs],
            'context_frame_ids':[],'owned_start_ms':0,'owned_end_ms':1000}
    audit={'checks':[{'event_id':c['track']['event_id'],'trigger_verdict':'supported',
        'outcome_verdict':'supported','question':''} for c in claims],
        'issues':[{'kind':'unresolved_evidence','owner_frame':'f3',
                   'frame_refs':['f2','f3'],'question':'causal ownership unknown'}],
        'multiple_scenarios':False,'revisit':[]}
    return packet,claims,audit


def test_selector_preserves_causal_predecessor_and_originals():
    p,c,a=sample(); original=copy.deepcopy((p,c,a)); r=prepare(p,c,[],a)
    assert [x['track']['event_id'] for x in r['trajectory']]==['b']
    assert [x['track']['event_id'] for x in r['context_trajectory']]==['a']
    assert [f['frame_id'] for f in r['frames']]==['f0','f1','f2','f3','f4','f5']
    assert (p,c,a)==original
    a['issues']=[]
    assert prepare(p,c,[],a) is None


def test_contradiction_is_not_overwritten_by_new_support():
    _,_,a=sample(); b=copy.deepcopy(a); b['checks']=b['checks'][1:]
    b['checks'][0].update(trigger_verdict='contradicted',question='different causal source')
    once=combine(a,b); again=combine(once,a)
    assert again['checks'][1]['trigger_verdict']=='contradicted'
    assert 'different causal source' in again['checks'][1]['question']
    assert again['issues']==a['issues']


```

- [ ] Run `python3 -m pytest -q tests/test_ui_video_causal_recheck.py`; expect import failure before helper exists.
- [ ] Create helper with this complete content:

```python
"""Partial causal audit selection; never authors source actions or changes ownership."""
import copy
import json

INTERACTIONS={'aiTap','aiInput','aiScroll'}


def identity(track):
    # A within-packet physical-claim key, stable across target/prose/modality edits.
    # Automatic-to-interaction correction is allowed; same-anchor interaction
    # relabeling cannot erase a refutation. Original IDs remain in audit records.
    family='interaction' if track['action'] in INTERACTIONS else track['action']
    return json.dumps([family,track['trigger_frame'],track.get('continuation_of','')],ensure_ascii=False)


def prepare(packet,claims,prior,audit):
    checks={c['event_id']:c for c in audit['checks']}
    owned=set(packet['owned_frame_ids']); selected={}; issues=[]
    for issue in audit['issues']:
        if issue['kind']!='unresolved_evidence' or issue['owner_frame'] not in owned:
            continue
        hits=[]
        for claim in claims:
            t=claim['track']; check=checks.get(t['event_id'],{})
            eligible=((t['action'],t['trigger_kind']) in
                      {('aiWaitFor','automatic'),('aiTap','navigation')})
            if (eligible and check.get('trigger_verdict')==check.get('outcome_verdict')=='supported'
                    and set(issue['frame_refs']) & set(t['frame_refs'])):
                hits.append(claim)
        if hits:
            issues.append(copy.deepcopy(issue))
            selected.update((c['track']['event_id'],copy.deepcopy(c)) for c in hits)
    if not selected:
        return None
    # Prior history is read-only projected context; only the last fragment is
    # needed to find the immediate predecessor of the current packet.
    chain=[{'track':{**copy.deepcopy(p['fragments'][-1]),'event_id':p['event_id']}}
           for p in prior if p.get('fragments')]+copy.deepcopy(claims)
    context={}
    for eid in selected:
        index=next(i for i,c in enumerate(chain) if c['track']['event_id']==eid)
        for item in reversed(chain[:index]):
            t=item['track']; context[t['event_id']]=copy.deepcopy(item)
            if t['action'] in INTERACTIONS:
                break
    refs={ref for issue in issues for ref in issue['frame_refs']}
    for item in list(selected.values())+list(context.values()):
        t=item['track']
        refs.update(t['frame_refs']); refs.add(t['trigger_frame'])
        refs.update(t['outcome']['frame_refs'])
        refs.update(t.get('required_frame_refs',[]));refs.update(t.get('allowed_frame_refs',[]))
    ordered=[f['frame_id'] for f in packet['frames']]
    if not refs<=set(ordered):
        return {'status':'review','reason':'causal_context_unavailable','issues':issues}
    positions={r:i for i,r in enumerate(ordered)}
    for ref in tuple(refs):
        i=positions[ref];refs.update(ordered[max(0,i-1):i+2])
    if len(refs)>24:
        return {'status':'review','reason':'causal_image_capacity','issues':issues}
    images=[copy.deepcopy(f) for f in packet['frames'] if f['frame_id'] in refs]
    return {'status':'ready','frames':images,'trajectory':list(selected.values()),
        'context_trajectory':[c for c in chain if c['track']['event_id'] in context and
                              c['track']['event_id'] not in selected],
        'owned_frame_ids':[r for r in ordered if r in refs and r in owned],
        'context_frame_ids':[r for r in ordered if r in refs and r not in owned],
        'issues':issues}


def combine(full,partial):
    """Derived verdicts retain every question; contradiction outranks later support."""
    rank={'supported':0,'unclear':1,'contradicted':2}
    out=copy.deepcopy(full); by_id={c['event_id']:c for c in out['checks']}
    for newer in partial['checks']:
        if newer['event_id'] not in by_id:
            raise ValueError('video_causal_check_scope')
        old=by_id[newer['event_id']]
        for field in ('trigger_verdict','outcome_verdict'):
            old[field]=max((old[field],newer[field]),key=rank.__getitem__)
        old['question']='；'.join(dict.fromkeys(q for q in
            (old['question'],newer['question']) if q))
    for issue in partial['issues']:
        if issue not in out['issues']:
            out['issues'].append(copy.deepcopy(issue))
    out['multiple_scenarios']|=partial['multiple_scenarios']
    # Partial acquisition is deferred evidence, never executed by this helper.
    for request in partial['revisit']:
        if request not in out['revisit']:
            out['revisit'].append(copy.deepcopy(request))
    return out
```

- [ ] Run the two pure tests; expect PASS. Run `git diff --check`, then continue wiring the same feature before task review.

#### Step group B: budget and one-physical-attempt adapter

**Files:** modify `ui_video_runtime.py`, `ui_video_business_jobs.py`; extend test module.

**Interfaces:** new `RuntimeBudget.preflight_causal_recheck(normal_reserve, remaining_packets, partial=1)` returns bool without incrementing `reserved`; actual ModelJobs.reserve remains sole charging. `BusinessJobs.run(..., allow_transport_retry=True, **kwargs)` consumes this private argument before transport. No provider receives it.

- [ ] Add failing tests:

```python
def test_runtime_admission_preserves_limit_and_deadline():
    from task_server.services.ui_video_runtime import RuntimeBudget
    b=RuntimeBudget(0,clock=lambda:0,business=True);b.freeze_packets(9,business=True)
    b.start_engine(business=True);b.reserved=29
    before=(b.limit,b.deadline,b.engine_deadline(),b.reserved)
    assert b.preflight_causal_recheck(2,0)
    assert (b.limit,b.deadline,b.engine_deadline(),b.reserved)==before
    b.reserved=32
    assert not b.preflight_causal_recheck(2,0)
    assert b.limit==38 and b.deadline==4500
```

- [ ] Run that test; expect missing-method failure. Add inside RuntimeBudget:

```python
    def preflight_causal_recheck(self, normal_reserve, remaining_packets, partial=1):
        if (any(type(n) is not int or n<0 for n in
                (normal_reserve,remaining_packets,partial)) or partial not in (0,1)):
            raise ValueError('video_causal_capacity_invalid')
        self.check()
        needed=normal_reserve+2*remaining_packets+partial
        end=min(self.deadline,self._engine_deadline or self.deadline)
        return (self.frozen and self.limit-self.reserved>=needed+4 and
                end-self.clock()>=120*needed+.5)
```

- [ ] In BusinessJobs replace the method signature with `def run(self, skill, payload=None, *, allow_transport_retry=True, **kwargs):`. Immediately after it insert:

```python
        if type(allow_transport_retry) is not bool:
            raise ValueError('video_job_options_invalid')
```

Replace its retry refusal condition `if attempt or len(self.retries) >= 2 or hooks or reason is None:` with:

```python
                if not allow_transport_retry or attempt or len(self.retries) >= 2 or hooks or reason is None:
```

This is a consumed internal kwarg, not a user-selectable model option. Existing calls default unchanged. Add this complete test to `tests/test_ui_video_business_jobs.py` using its existing scripted fixture:

```python
@pytest.mark.parametrize('failure',['503','deadline'])
def test_partial_recheck_is_one_actual_attempt(tmp_path,failure):
    records,budget,jobs,owners,calls=scripted(tmp_path,[failure,'ok'])
    try:
        with jobs:
            with pytest.raises((jm.ModelJobError,jm.LocalModelJobDeadline)):
                invoke(jobs,stage='audit',allow_transport_retry=False)
        assert budget.reserved==1 and len(calls)==len(jobs.accounting)==len(owners)==1
        assert jobs.retries==[] and owners[0]._closed
        assert jobs.accounting[0]['terminal'] and jobs.accounting[0]['cleanup']
        assert 'allow_transport_retry' not in calls[0][2]
    finally:records.close()
```

Do not substitute retry_count=0 for this test.

- [ ] In runtime `runner`, define after `payload = copy.deepcopy(payload)`:

```python
                    partial=payload.get('causal_recheck',{}).get('version')=='causal-question-recheck-v1'
                    if partial and (not business or kwargs.get('version')!='audit'):
                        raise ValueError('video_causal_stage_invalid')
```

In the existing business audit branch replace `_audit_schema(payload,forward_context_issues=True)` with `_audit_schema(payload,forward_context_issues=not partial)`, and replace the existing reference_contract assignment with:

```python
                        payload['reference_contract']={
                            'version':'packet-audit-reference-v1' if partial else 'packet-audit-reference-v2',
                            'check_event_ids':[c['track']['event_id'] for c in payload['trajectory']],
                            'issue_owner_frame_ids':list(payload['owned_frame_ids']),
                            'image_frame_ids':[f['frame_id'] for f in payload['frames']]}
                        if not partial:
                            payload['reference_contract']['future_issue_owner_frame_ids']=audit_domains(payload)[1]
```

Immediately before `return jobs.run(...)`, insert `if partial: kwargs['allow_transport_retry']=False`. The existing call remains unchanged. Immediately before the observe_trajectory invocation define this private callback:

```python
                def causal_recheck_safe_review():
                    boundary()
                    owner=jobs.current
                    rows=jobs.accounting
                    return (not jobs._closed and not owner._closed and not owner._latched and
                            not owner.active_pids and not owner.cleanup_failures and
                            not owner.pending_receipts and bool(rows) and
                            rows[-1].get('terminal') is True and rows[-1].get('cleanup') is True and
                            bool(rows[-1].get('proof_ref')) and bool(rows[-1].get('receipt_ref')))
```

Pass `causal_recheck_preflight=budget.preflight_causal_recheck` and `causal_recheck_safe_review=causal_recheck_safe_review` ONLY in the business-specific keyword dictionary supplied to observe_trajectory. The callback checks source authority/time through boundary plus the actual owned job health; it does not create or replace an owner.

- [ ] Run runtime/BusinessJobs tests and strict stage/receipt tests. Existing exact hashes remain valid for non-partial; partial hashes must be the actual narrowed schema, not aliases of full audit. No Gateway edits expected.

#### Step group C: explicit partial scope, counters and full correction integration

**Files:** modify trajectory and append conditional audit paragraph. Implementation uses the interfaces introduced in step groups A/B.

- [ ] Add `causal_recheck_preflight=None,causal_recheck_safe_review=None` to observe_trajectory's keyword-only arguments. Add this exact argument validation at function entry:

```python
    for hook in (causal_recheck_preflight,causal_recheck_safe_review):
        if hook is not None and (not callable(hook) or business_timeline is None):
            raise ValueError('video_causal_callback_invalid')
```
 Import helper with `from . import ui_video_causal_recheck as causal`.

- [ ] After counter initialization add:

```python
    normal_count=0;causal_recheck_count=0;causal_rechecks=[]
```

Replace ask_once's `nonlocal calls` and initial call-limit/clock guard through `calls+=1;trace={};tick=time.monotonic()` with:

```python
        nonlocal calls,normal_count,causal_recheck_count
        partial=context.get('causal_recheck',{}).get('version')=='causal-question-recheck-v1'
        if cancelled():raise ValueError('video_cancelled')
        if ((causal_recheck_count>=2 if partial else normal_count>=call_limit) or
                time.monotonic()>=engine_deadline):
            diagnose('capacity_refusal',reason='causal_recheck_quota' if partial else 'trajectory_calls')
            raise ValueError('video_vision_limit')
        if partial:causal_recheck_count+=1
        else:normal_count+=1
        calls+=1;trace={};tick=time.monotonic()
```

Partial uses `ask_once`, never `ask`, so no automatic format retry. In ask_once set `delivered=[]` for partial (ordinary expression remains otherwise): replace the delivered assignment with `delivered=[] if partial else ([q for q in forward_questions if q['target_packet_id']==context.get('packet_id')] if forward_context_issues else [])`. Replace `if exhausted:` with `if exhausted and not partial:` and `if deferred:` with `if deferred and not partial:`. Replace the acquisition-budget conditional `if version in {'observe','audit'}:` immediately under `if business_timeline is not None:` with `if version in {'observe','audit'} and not partial:`. Replace the existing revisit_context_anchors assignment with `if version in {'observe','audit'}:payload['revisit_context_anchors']=False if partial else bounded_revisit`. Add `audit_scope='causal_partial' if partial else 'packet_complete'` to audit call entry. Never append partial calls to forward-question delivered audit evidence. Include `normal_count`, `causal_recheck_count`, `total_calls=calls` and `causal_rechecks=redact(causal_rechecks)` in final trajectory_audit metadata. Existing `calls` remains the full ordered record list, not a count.

- [ ] At each packet initialization add `causal_attempted=False;causal_veto=set();causal_repair=False`. Lock checkpoint: the final first ordinary audit has not yet called `_merge_positive_trigger_locks` (that existing call only occurs on a normal repair). Defer only its NEW positive candidates until partial/full verdict combination. Locks already established by an earlier current-packet normal repair remain immutable for this branch; earlier packet events/tracks are immutable independently. A partial refutation conflicting with one of those inherited locks must stop, never revoke it. Keep original event_id/fragment identity and raw verdict alongside the stable veto key; the veto key excludes target prose and modality, so paraphrasing cannot evade it. Immediately after `bind_packet_events(...)` inside its existing try block insert:

```python
                current_fragments=[f for t in history for f in t['fragments']
                                   if f['trigger_frame'] in packet['owned_frame_ids']]
                if any(causal.identity(f) in causal_veto for f in current_fragments):
                    # Same exact refuted claim cannot become supported by repetition.
                    _fail('causal_refuted_claim_reappeared')
```

The existing normal correction/error gate handles this. Do not add the refuted claim to preservation signatures. Prior locks remain checked by existing `_validate_positive_trigger_locks`.

- [ ] After `audit_batches` is built, before any normal audit shard, insert:

```python
            if causal_repair and (normal_count+len(audit_batches)>call_limit or
                    not causal_recheck_preflight(len(audit_batches),len(packets)-index-1,partial=0)):
                _fail('causal_repair_capacity')
```

This checks actual new shard count after corrected observation. Each actual dispatch continues using normal runtime reserve. It never counts targeted audit as a normal shard.

- [ ] Insert the following block immediately after ordinary audit combination (after the optional `packet_id+'-audit-combined'` record), before the existing `break` that commits this packet. This position is reached only after ordinary expanded/repair paths are exhausted:

```python
            if causal_recheck_preflight is not None and not causal_attempted:
                proposal=causal.prepare(packet,audit_batch,audit_prior,audited)
                if proposal is not None:
                    causal_attempted=True
                    note={'packet_id':packet['packet_id'],'classification':'unverified_causal_recheck',
                          'instruction_authority':False,'status':proposal['status'],
                          'issues':copy.deepcopy(proposal['issues'])}
                    causal_rechecks.append(note)
                    required_normal=1+len(audit_batches)
                    admitted=(proposal['status']=='ready' and correction_available() and
                        causal_recheck_count<2 and
                        normal_count+required_normal+2*(len(packets)-index-1)<=call_limit and
                        causal_recheck_preflight(required_normal,len(packets)-index-1))
                    if not admitted:
                        note.update(status='review',reason=proposal.get('reason','causal_capacity'))
                        questions.extend(i['question'] for i in proposal['issues'])
                        diagnose('capacity_refusal',reason=note['reason'])
                    else:
                        scoped={**packet,'frames':proposal['frames'],
                                'owned_frame_ids':proposal['owned_frame_ids'],
                                'context_frame_ids':proposal['context_frame_ids']}
                        note['origin_audit_calls']=[n for shard in audit_shards for n in shard['calls']]
                        note['image_frame_ids']=[f['frame_id'] for f in proposal['frames']]
                        note['check_event_ids']=[c['track']['event_id'] for c in proposal['trajectory']]
                        try:
                            partial=ask_once('audit',proposal['frames'],
                                packet_id=packet['packet_id'],
                                owned_frame_ids=proposal['owned_frame_ids'],
                                context_frame_ids=proposal['context_frame_ids'],
                                owned_time_range_ms=[packet['owned_start_ms'],packet['owned_end_ms']],
                                trajectory=proposal['trajectory'],context_trajectory=proposal['context_trajectory'],
                                prior_events=[],causal_recheck={'version':'causal-question-recheck-v1',
                                    'scope':'partial','instruction_authority':False,
                                    'issues':copy.deepcopy(proposal['issues'])})
                            bind_audit_checks(partial['checks'],[c['track'] for c in proposal['trajectory']],proposal['frames'])
                            validate_audit_issues(partial['issues'],scoped)
                            validate_revisit_requests(partial['revisit'],scoped,allow_context_anchors=False)
                        except ValueError as error:
                            from .ai_skill_service import SkillSchemaError
                            format_reasons={'audit_shape','audit_missing_event','audit_verdict',
                                'audit_question','audit_contradiction','audit_issue_shape',
                                'audit_issue_kind','audit_issues'}
                            expected=(isinstance(error,SkillSchemaError) or
                                      getattr(error,'trajectory_reason',None) in format_reasons)
                            if not (expected and causal_recheck_safe_review is not None and
                                    causal_recheck_safe_review()):
                                raise
                            note.update(status='review',reason='causal_format_rejection',call=calls,
                                rejection_kind='schema' if isinstance(error,SkillSchemaError) else error.trajectory_reason,
                                origin_refs=copy.deepcopy({k:last_diagnostic[k] for k in
                                    ('proof_ref','receipt_ref') if k in last_diagnostic}))
                            questions.extend(i['question'] for i in proposal['issues'])
                            diagnose('rejection',reason='causal_format_rejection')
                            if record:record(packet['packet_id']+'-causal-recheck',redact(note))
                            break  # Keep original complete audit plus unresolved questions.
                        note.update(status='checked',call=calls,raw=redact(copy.deepcopy(partial)),
                            origin_refs=copy.deepcopy({k:last_diagnostic[k] for k in
                                ('proof_ref','receipt_ref') if k in last_diagnostic}))
                        # Preserve all questions. The partial never invokes expand.
                        questions.extend(i['question'] for i in partial['issues'])
                        questions.extend(c['question'] for c in partial['checks'] if c['question'])
                        questions.extend(r['question'] for r in partial['revisit'])
                        combined=causal.combine(audited,partial)
                        refuted={c['event_id'] for c in partial['checks'] if c['trigger_verdict']=='contradicted'}
                        refuted_claims=[c for c in audit_batch if c['track']['event_id'] in refuted]
                        veto={causal.identity(c['track']) for c in refuted_claims}
                        if veto & {causal.identity(lock) for lock in positive_trigger_locks}:
                            _fail('causal_prior_lock_conflict')
                        must_repair=(any(c[f]=='contradicted' for c in partial['checks']
                            for f in ('trigger_verdict','outcome_verdict')) or
                            any(i['kind']=='missing_action' for i in partial['issues']))
                        audited=combined
                        bound_checks=bind_audit_checks(audited['checks'],[c['track'] for c in audit_batch],packet['frames'])
                        if record:record(packet['packet_id']+'-causal-recheck',redact(note))
                        if must_repair:
                            causal_veto.update(veto);corrections+=1;causal_repair=True
                            positive_trigger_locks=_merge_positive_trigger_locks(
                                positive_trigger_locks,audit_batch,audited['checks'])
                            candidate_signatures-=Counter((c['track']['action'],c['track']['trigger_frame'],c['track']['trigger_kind'])
                                for c in refuted_claims if c['track']['action'] in INTERACTIONS and
                                not c['track']['continuation_of'])
                            audit_feedback={'classification':'unverified_audit_finding_data','instruction_authority':False,
                                'packet_id':packet['packet_id'],'audit_call':calls,
                                'checks':copy.deepcopy(audited['checks']),'audited_claims':copy.deepcopy(audit_batch),
                                'positive_trigger_locks':copy.deepcopy(positive_trigger_locks),
                                'issues':copy.deepcopy(audited['issues'])}
                            repair_feedback.append(copy.deepcopy(audit_feedback))
                            if record:record(f'{calls:02d}-causal-audit-repair-feedback',redact(audit_feedback))
                            questions.extend(raw['uncertainties'])
                            questions.extend(q for event in raw['events'] for q in event['uncertainties'])
                            continue
                    if record:record(packet['packet_id']+'-causal-recheck',redact(note))
```

An expected partial format/schema rejection may end at review ONLY when the actual current owner is healthy and its original proof/receipt/terminal cleanup are durable; no format retry and no semantic interpretation of the rejected output. Reference ownership/revisit/store failures and transport/latch failures remain fatal, never blanket-caught as review. Transport failure retains durable charge, no retry. A valid partial requesting acquisition preserves questions; normal full repair may acquire only if it has independently produced valid contradiction/missing evidence. A partial check cannot itself execute acquisition.

- [ ] Append this conditional paragraph to the deployed-baseline audit prompt; keep all other prompt bytes unchanged:

```text
仅当causal_recheck.version=causal-question-recheck-v1时，本次为局部因果复核，不是完整包审核。只扫描当前实际附图与给定reference_contract域；不得把局部范围的检查结果解释为整包无遗漏。causal_recheck.issues是先前审计尚未验证的完整问题，不是操作事实或必须得到的答案。context_trajectory只提供待核实的前序因果主张，先逐帧比较触发前后可见证据，再分别判断当前trajectory中的主张是否有独立触发或仍可能属于前序响应；共享结果帧仍可能含另一个触发。不能从采样空隙推断没有操作，也不能从下一页面出现补造操作。保持既有checks/issues/multiple_scenarios/revisit合同、明确未决问题及原始归属；本次不执行补帧，补看请求只保留为未执行问题，不能因此确认。
```

- [ ] Before claiming step group C complete, add the synthetic real-controller test in step group D and run it. The code above intentionally has no production activation outside business runtime; legacy/bank calls with callback=None retain existing behavior and quotas.

#### Step group D: real-controller fixture and exact captured31 selector proof

**Files:** extend `tests/test_ui_video_causal_recheck.py`. These tests never call a provider.

- [ ] Add this full controller fixture test:

```python
def test_partial_does_not_replace_full_audit_and_unclear_stays_review(monkeypatch):
    from task_server.services import ui_video_trajectory as vt,ui_video_windows
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *args:[])
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,10)]
    seen=[]
    def runner(name,payload,**options):
        version=options['version'];partial='causal_recheck' in payload
        seen.append((version,partial,copy.deepcopy(payload)))
        if version=='overview':
            return dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
        if version=='observe':
            e=dict(action='aiWaitFor',target='visible state',value='',frame_refs=['f2','f3'],
                observation='state changes',uncertainties=[],trigger_frame='f2',trigger_kind='automatic',
                page_before='p',page_after='p',continuation_of='',edit_boundary='none',
                outcome={'frame_refs':['f3'],'observation':'state visible'})
            return dict(states=[],events=[e],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
        checks=[dict(event_id=c['track']['event_id'],trigger_verdict='unclear' if partial else 'supported',
            outcome_verdict='supported',question='causality remains uncertain' if partial else '')
            for c in payload['trajectory']]
        return dict(checks=checks,issues=[dict(kind='unresolved_evidence',owner_frame='f2',
            frame_refs=['f2','f3'],question='mechanism cannot be determined')],multiple_scenarios=False,revisit=[])
    result=vt.observe_trajectory({'media':{'frames':fs,'duration_ms':1000}},None,lambda:False,
        skill_runner=runner,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1,
        business_timeline={},causal_recheck_preflight=lambda *args,**kwargs:True)
    a=result['trajectory_audit']
    assert a['normal_count']==3 and a['causal_recheck_count']==1 and a['total_calls']==4
    assert [(v,p) for v,p,_ in seen]==[('overview',False),('observe',False),('audit',False),('audit',True)]
    assert a['checks'][0]['status']=='needs_review'
    assert 'mechanism cannot be determined' in result['uncertainties']
    partial=seen[-1][2]
    assert not {'state_memory','overview_hypotheses'} & partial.keys()
    assert len(a['packets'])==1 and len(a['causal_rechecks'])==1
```

Run this fixture before and after step group C; RED must be missing callback/counters, not an unrelated malformed fixture. The existing binder validates nonempty page identity without requiring a state declaration; this fixture uses that existing contract and does not relax a validator.

- [ ] Execute this exact zero-provider selector proof from the repository root after step group A. It reads private captured evidence without copying it into git; it does not modify the reconstruction or source:

```bash
python3 - <<'PY'
import copy,hashlib,json
from pathlib import Path
from task_server.services.ui_video_causal_recheck import prepare
from task_server.services.ui_video_runtime import _audit_schema
from task_server.services.ui_video_stage_policy import validate_output_schema
root=Path('/Users/adouceshi/Documents/projects/video-reliability-20261026/new-touch/current-platform/normal-run31/downloaded-evidence')
r=json.loads((root/'root-trajectory-reconstruction.json').read_text())
before=copy.deepcopy(r); selected={}
for p in r['result']['trajectory_audit']['packets']:
    pid=p['packet']['packet_id']
    # Last ordinary audit payload gives the full projected context; checks/issues
    # come from FINAL packet result, never an earlier raw receipt.
    call=[c for c in r['calls'] if c['stage']=='audit' and c['payload'].get('packet_id')==pid][-1]
    payload=call['payload']
    out=prepare(p['packet'],payload['context_trajectory'],payload['prior_events'],p['audit'])
    if out is None:continue
    assert out['status']=='ready'
    selected[pid]=out
    proof={x['frame_id']:x['sha256'] for x in call['images']}
    for f in out['frames']:
        actual=hashlib.sha256((root/'run31/frames'/f"{f['frame_id']}.jpg").read_bytes()).hexdigest()
        assert actual==proof[f['frame_id']]
    scoped={**payload,**out}
    validate_output_schema(_audit_schema(scoped,forward_context_issues=False),stage='audit')
assert set(selected)=={'packet-007','packet-009'}
assert len(selected['packet-007']['frames'])==16
assert len(selected['packet-009']['frames'])==23
assert 'frame-205' in {f['frame_id'] for f in selected['packet-007']['frames']}
assert 'frame-282' in {f['frame_id'] for f in selected['packet-009']['frames']}
assert r==before
print('ZERO_PROVIDER selector: 007=16 images including205;009=23 including282; original SHA/schema PASS')
PY
```

If this exact predicate fails, do not hand-insert205/282 or adjust the selector to filename/frame constants. Reject the implementation against the spec.

- [ ] Run focused existing suites: `python3 -m pytest -q tests/test_ui_video_causal_recheck.py tests/test_ui_video_trajectory.py tests/test_ui_video_runtime.py`. Discover actual adjacent BusinessJobs/stage/receipt test paths with `rg --files tests | rg 'business_jobs|stage_policy|stage_receipts|audit|temporal'`, and run all returned Python test files with pytest. No live provider tests.
- [ ] Run mandatory checks:

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ui_video_causal_recheck.py task_server/services/ui_video_trajectory.py task_server/services/ui_video_runtime.py task_server/services/ui_video_business_jobs.py
python3 tests/backend_static_checks.py
git diff --check
```

- [ ] Root reviews exact files and evidence; append actual results/limits to CODEX_STATE without overwriting existing dirt. Root owns precise staging/commit; this plan neither stages nor commits. No deployment command is appropriate before separate real-Qwen qualification. Prepare that external artifact only after code/whole review approval, with its own sealed inputs, actual main preflight, quota/canonical accounting and no implicit live calls inside implementation.

### Additional gate tests before integrated task approval

Add these exact tests to the helper test module:

```python
def test_refutation_identity_survives_target_and_modality_paraphrase():
    from task_server.services.ui_video_causal_recheck import identity
    _,claims,_=sample(); original=claims[0]['track']; changed=copy.deepcopy(original)
    changed.update(target='renamed equivalent control',trigger_kind='navigation')
    assert identity(original)==identity(changed)
    changed['trigger_frame']='f3'
    assert identity(original)!=identity(changed)


def test_capacity_refusal_and_noneligible_claim_do_not_seed_actions():
    packet,claims,audit=sample()
    claims[1]['track']['action']='aiAssert'
    assert prepare(packet,claims,[],audit) is None
    claims[1]['track']['action']='aiWaitFor'
    claims[1]['track']['frame_refs'].append('absent-original')
    result=prepare(packet,claims,[],audit)
    assert result['status']=='review' and result['reason']=='causal_context_unavailable'
    assert 'trajectory' not in result
```

One full-controller contradiction/all-shards test is additionally required; add this complete test (no real image/provider IO):

```python
def test_partial_refutation_runs_full_observer_and_every_audit_shard(monkeypatch):
    from task_server.services import ui_video_trajectory as vt,ui_video_windows
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *args:[])
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,10)]
    seen=[];refuted=False
    def runner(name,payload,**options):
        nonlocal refuted
        version=options['version'];partial='causal_recheck' in payload
        seen.append((version,partial,len(payload.get('trajectory',[]))))
        if version=='overview':
            return dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
        if version=='observe':
            def event(action,kind,refs,target):
                return dict(action=action,target=target,value='',frame_refs=refs,observation='state changes',
                    uncertainties=[],trigger_frame=refs[0],trigger_kind=kind,page_before='p',page_after='p',
                    continuation_of='',edit_boundary='none',outcome={'frame_refs':[refs[-1]],'observation':'state visible'})
            first=event('aiTap' if refuted else 'aiWaitFor','touch' if refuted else 'automatic',['f2','f3'],'first')
            second=event('aiTap','touch',['f6','f7'],'preserved independent target')
            return dict(states=[],events=[first,second],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
        checks=[dict(event_id=c['track']['event_id'],trigger_verdict='contradicted' if partial else 'supported',
            outcome_verdict='supported',question='independent trigger visible' if partial else '') for c in payload['trajectory']]
        issues=[] if refuted else [dict(kind='unresolved_evidence',owner_frame='f2',frame_refs=['f2','f3'],question='causal ambiguity')]
        if partial:refuted=True
        return dict(checks=checks,issues=issues,multiple_scenarios=False,revisit=[])
    result=vt.observe_trajectory({'media':{'frames':fs,'duration_ms':1000}},None,lambda:False,
        skill_runner=runner,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1,business_timeline={},
        audit_batcher=lambda claims:[[c] for c in claims],
        causal_recheck_preflight=lambda *args,**kwargs:True)
    assert seen==[('overview',False,0),('observe',False,0),('audit',False,1),('audit',False,1),
                  ('audit',True,1),('observe',False,0),('audit',False,1),('audit',False,1)]
    a=result['trajectory_audit']
    assert (a['normal_count'],a['causal_recheck_count'],a['total_calls'])==(7,1,8)
    assert [e['action'] for e in result['events']]==['aiTap','aiTap']
    assert result['events'][1]['target']=='preserved independent target'
    assert len(a['packets'][0]['audit_shards'])==2 and len(a['causal_rechecks'])==1
    assert 'causal ambiguity' in result['uncertainties']
```

### Healthy expected-format rejection test

Add this exact test; it exercises actual proposed controller handling without a provider. The callback False case represents a latched/unhealthy owner and must propagate, while True permits a recorded review exit only for SkillSchemaError:

```python
@pytest.mark.parametrize('healthy',[True,False])
def test_partial_schema_rejection_requires_healthy_owner(monkeypatch,healthy):
    from task_server.services import ui_video_trajectory as vt,ui_video_windows
    from task_server.services.ai_skill_service import SkillSchemaError
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *args:[])
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,10)]
    partial_calls=[]
    def runner(name,payload,**options):
        if 'causal_recheck' in payload:
            partial_calls.append(payload)
            raise SkillSchemaError('$','schema rejection fixture')
        version=options['version']
        if version=='audit':
            return dict(checks=[dict(event_id=c['track']['event_id'],trigger_verdict='supported',
                outcome_verdict='supported',question='') for c in payload['trajectory']],
                issues=[dict(kind='unresolved_evidence',owner_frame='f2',frame_refs=['f2','f3'],
                             question='original uncertainty')],multiple_scenarios=False,revisit=[])
        events=[]
        if version=='observe':
            events=[dict(action='aiWaitFor',target='visible state',value='',frame_refs=['f2','f3'],
                observation='state changes',uncertainties=[],trigger_frame='f2',trigger_kind='automatic',
                page_before='p',page_after='p',continuation_of='',edit_boundary='none',
                outcome={'frame_refs':['f3'],'observation':'state visible'})]
        return dict(states=[],events=events,checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
    def run():
        return vt.observe_trajectory({'media':{'frames':fs,'duration_ms':1000}},None,lambda:False,
            skill_runner=runner,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1,business_timeline={},
            causal_recheck_preflight=lambda *args,**kwargs:True,
            causal_recheck_safe_review=lambda:healthy)
    if healthy:
        result=run()
        assert 'original uncertainty' in result['uncertainties']
        assert result['trajectory_audit']['causal_rechecks'][0]['reason']=='causal_format_rejection'
    else:
        with pytest.raises(SkillSchemaError):run()
    assert len(partial_calls)==1
```

## Self-review and release concerns

Fixtures monkeypatch the actual ui_video_windows.load_assets entry point before passing workspace=None; RED/GREEN must confirm no real image/PIL/filesystem path is reached. Do not treat plan transcription as test execution.

1. This is a safe investigation branch, not a promise that sparse213→214 proves no action. Honest unclear/refusal is review, not semantic qualification.
2. Actual31's best-case38 reservation arithmetic has zero slack; downstream planning's known outcome_reference repair may require extra calls. Do not infer whole-source completion from the6-call local semantic minimum.
3. A partial cannot clear historical questions; this may leave review questions even after the particular candidate is corrected, as existing policy requires.
4. Every loop/call must be reviewed against both normal32/partial2 and global/time admission. A transport timeout closes the current owner; partial retries remain forbidden.
5. Private reconstructed evidence is not committed or regenerated. Full normal output and targeted raw output remain independently inspectable; combined verdicts are derived data.

## External qualification handoff correction

Use exact NEW31 preservation-control observer contexts call05/packet002 (44 originals) and call19/packet006 (64 originals after its already-completed normal acquisition), from root reconstruction. Do not use oldR6 cases05/15 or transplant their frame IDs. Current packet006 initialcall17 requested a required original revisit; freezing post-acquisition call19 preserves that actual new31 evidence while keeping the isolated diagnostic no-acquisition. Root must compute actual normal audit shard minimum, control calls, partial calls, normal corrections and all reservations before sealing the external artifact. This is a preparation requirement, not a live call step in implementation.

## Plan-only verification actually performed

The proposed pure helper and two retained initial pure tests were executed in an ephemeral Python namespace, not written as production modules:2PASS. The unused admission helper/test was removed; actual controller quotas and RuntimeBudget own admission. The exact final31 selector proof also executed against private root reconstruction:007=16 originals including205,009=23 including282; original JPEG hashes and existing narrowed audit schema validation PASS. No provider/server/phone call; no production or test file changed. Controller/runtime snippets remain proposed and require the RED/GREEN execution and independent review above. A lone conditional replacement is intentionally an insertion fragment rather than a standalone Python program.

## Root-approved declaration-awareness amendment (2026-10-31)

After image selection, retain the unchanged positive projection of any unselected current claim whose trigger is in the selected originals as read-only context. Do not acquire or add that claim's off-image refs, or change check/owner domains. This metadata makes existing declarations visible; it is not visual support or an extra causal predecessor. The conditional audit paragraph must state that unprovided referenced frames are not visible evidence, metadata cannot confirm a claim, and exclusion from partial checks does not make an already-declared action missing. Exact captured009 event3 trigger288 remains context with its original off-image refs; the selector stays007=16 and009=23 originals.

All validated original complete-audit issue, check and revisit questions enter the historical question list once on proposal formation, before admission. This retains selected and unselected original questions even when a valid partial refutation and the subsequent full repair clear their issue/check lists; new partial questions are retained independently. These remain unverified review questions, not action authority; ordinary forward ownership and origin records are unchanged.

Schema-boundary fix: healthy partial format review uses only explicit known nonreference schema paths. Owner/frame/check-ID/revisit-reference paths, ambiguous item-object paths, and root/unknown paths (including IPC paths normalized to `$`) remain fatal. Known array-shape or verdict/question/kind format paths still require the same durable healthy-owner checks; no reference validator or IPC policy is relaxed.
