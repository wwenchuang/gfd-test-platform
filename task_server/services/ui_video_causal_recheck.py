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
    # Declaration awareness only: preserve current claims triggered in the
    # neighborhood, without adding their off-image refs as visual evidence.
    for item in claims:
        t=item['track']
        if t['event_id'] not in selected and t['trigger_frame'] in refs:
            context[t['event_id']]=copy.deepcopy(item)
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
