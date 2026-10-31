"""State memory and owned action packets; diagnostic observer, not execution authority."""
import copy
import json
import math
from collections import Counter
import time

from .ui_video_analysis import _bind_whole_evidence, validate_analysis
from .ui_video_stage_policy import video_stage_image_limit
from .ui_video_stage_receipts import cached_token_usage
from .ui_video_execution_contract import validate_execution_contract, project_audit_claims

ROOT_FIELDS={'states','events','checks','uncertainties','multiple_scenarios'}
EVENT_FIELDS={'action','target','value','frame_refs','observation','uncertainties','trigger_frame',
              'trigger_kind','page_before','page_after','continuation_of','edit_boundary','outcome'}
INTERACTIONS={'aiTap','aiInput','aiScroll'}


def _fail(reason,model_feedback=None):
    error=ValueError('video_model_invalid');error.trajectory_reason=reason
    if model_feedback is not None:error.model_feedback=model_feedback
    raise error


def action_contract():
    """Fresh trusted description of existing local admissibility, not evidence."""
    return {'version':'action-trigger-v1','allowed_trigger_kinds':{
        'aiTap':['touch','pressed_control','navigation','edit','uncertain'],
        'aiScroll':['touch','pressed_control','navigation','edit','uncertain'],
        'aiInput':['edit'],'aiAssert':['automatic','uncertain'],
        'aiWaitFor':['automatic','uncertain']},'input_value':''}


def _continuation_feedback(index,raw,events,history,times,refs,after,checked):
    """Describe the failed existing predicates without echoing model prose."""
    feedback={'event_index':index,'prior_eligible':False,'failed_checks':[]}
    if not history or not events:
        feedback['failed_checks']=['prior_history_available']
        return feedback
    prior=history[-1];event=events[-1]
    checks={
        'last_event_identity':raw['continuation_of']==prior['event_id'],
        'current_action_input':raw['action']=='aiInput',
        'prior_action_input':event['action']=='aiInput',
        'same_field':raw['target']==event['target'],
        'page_before_matches_prior_end':raw['page_before']==prior['page_after'],
        'page_after_matches_page_before':raw['page_after']==raw['page_before'],
        'current_boundary_none':raw['edit_boundary']=='none',
        'prior_boundary_none':prior['edit_boundary']=='none',
        'trigger_moves_forward':times[raw['trigger_frame']]>times[prior['fragments'][-1]['trigger_frame']],
        'evidence_not_before_prior_start':times[refs[0]]>=event['start_ms'],
        'end_not_before_prior_end':checked['end_ms']>=event['end_ms'],
        'outcome_not_before_prior_outcome':times[after[-1]]>=times[prior['outcome']['frame_refs'][-1]],
        'gap_at_most_5000ms':max(0,times[raw['trigger_frame']]-event['end_ms'])<=5000,
    }
    feedback.update(prior_eligible=event['action']=='aiInput' and prior['edit_boundary']=='none',
                    failed_checks=[name for name,passed in checks.items() if not passed],
                    expected_identity={'continuation_of':'prior_events[-1].event_id',
                        'target':'prior_events[-1].event.target',
                        'page_before':'prior_events[-1].page_after','page_after':'prior_events[-1].page_after'})
    return feedback


def _correction_feedback(context,parent,latest):
    """Keep one unresolved outer cause and the latest inner error, bounded separately."""
    for key,value in (('parent_validation_feedback',parent),('validation_feedback',latest)):
        context.pop(key,None);context.pop(key+'_truncated',None)
        if value or key=='validation_feedback':
            context[key]=value[:12000]
            if len(value)>12000:context[key+'_truncated']=True


def _action_conflicts(raw_events,packet,times):
    conflicts=[];allowed={f['frame_id'] for f in packet['frames']}
    policy=action_contract()['allowed_trigger_kinds']
    for index,raw in enumerate(raw_events[:50]):
        try:
            if not isinstance(raw,dict) or set(raw)!=EVENT_FIELDS:continue
            action=raw['action'];kind=raw['trigger_kind'];trigger=raw['trigger_frame']
            if action not in policy or kind not in {'touch','pressed_control','navigation','edit','automatic','uncertain'}:continue
            if trigger not in packet['owned_frame_ids'] or trigger not in times:continue
            refs=_refs(raw['frame_refs'],times,limit=60)
            if not set(refs)<=allowed or trigger not in refs:continue
            if (not isinstance(raw['uncertainties'],list) or len(raw['uncertainties'])>10 or
                any(not isinstance(q,str) or not q or len(q)>2000 for q in raw['uncertainties'])):continue
            for key in ('target','observation','page_before','page_after'):_text(raw[key])
            _text(raw['continuation_of'],100,empty=True)
            if kind=='uncertain' and not raw['uncertainties']:continue
            if kind not in policy[action] or (action=='aiInput' and raw['value']!=''):
                conflicts.append({'event_index':index,'action':action,'trigger_kind':kind,
                                  'trigger':{'frame_id':trigger,'timestamp_ms':times[trigger]}})
        except (ValueError,TypeError,KeyError):continue
    return {'action_conflicts':conflicts}


def _text(value,limit=2000,empty=False):
    if not isinstance(value,str) or len(value)>limit or (not empty and not value.strip()):_fail('text')
    return value


def _refs(refs,times,limit=8):
    if (not isinstance(refs,list) or not 1<=len(refs)<=limit or
        any(type(r) is not str or r not in times for r in refs) or len(set(refs))!=len(refs) or
        [times[r] for r in refs]!=sorted(times[r] for r in refs)):_fail('frame_reference')
    return refs


def _compile_references(refs,times,required):
    """Select only validated raw evidence, preserving endpoints and edit anchors."""
    mandatory=set(required)
    if len(mandatory)>8:_fail('input_evidence_budget')
    if not mandatory<=set(refs):_fail('event_evidence')
    if len(refs)<=8:selected=list(refs)
    else:
        selected=set(mandatory)
        # Farthest-point sampling spreads remaining slots over actual time,
        # including unevenly sampled recordings, with stable earliest ties.
        while len(selected)<8:
            remaining=set(refs)-selected
            selected.add(max(remaining,key=lambda r:(min(abs(times[r]-times[a]) for a in selected),-times[r],r)))
        selected=sorted(selected,key=times.get)
    return selected,{'raw_frame_refs':list(refs),'selected_frame_refs':list(selected),
                     'required_anchors':sorted(mandatory,key=times.get)}


def _state_feedback(index,a,b,last,times):
    # Only authoritative current-frame identifiers and times may leave this helper.
    first=type(a) is str and a in times
    end=type(b) is str and b in times
    checks={'first_anchor_available':first,'last_anchor_available':end}
    if first and end:
        checks.update(first_not_after_last=times[a]<=times[b],
                      start_not_before_previous=last<=times[a])
    feedback={'state_index':index,'previous_start_timestamp_ms':last,'failed_checks':[k for k,v in checks.items() if not v]}
    for key,ref,valid in (('first_anchor',a,first),('last_anchor',b,end)):
        if valid:feedback[key]={'frame_id':ref,'timestamp_ms':times[ref]}
    return feedback


def validate_states(states,frames):
    times={f['frame_id']:f['timestamp_ms'] for f in frames}
    if not isinstance(states,list) or len(states)>60:_fail('states')
    last=-1
    for index,s in enumerate(states):
        if not isinstance(s,dict) or set(s)!={'state_id','first_frame','last_frame','kind','description'}:_fail('state_shape')
        _text(s['state_id'],80);_text(s['description'])
        if s['kind'] not in {'page','input','wait','transition'}:_fail('state_kind')
        a,b=s['first_frame'],s['last_frame']
        if a not in times or b not in times or not last<=times[a]<=times[b]:_fail('state_reference',_state_feedback(index,a,b,last,times))
        last=times[a]
    return copy.deepcopy(states)


def make_packets(frames,states):
    if not isinstance(frames,list) or not 2<=len(frames)<=300:_fail('frame_count')
    if (any(not isinstance(f,dict) or not isinstance(f.get('frame_id'),str) or not f['frame_id'] or
            type(f.get('timestamp_ms'))is not int or f['timestamp_ms']<0 for f in frames) or
        len({f['frame_id'] for f in frames})!=len(frames) or
        any(a['timestamp_ms']>=b['timestamp_ms'] for a,b in zip(frames,frames[1:]))):_fail('frame_reference')
    validate_states(states,frames)
    positions={f['frame_id']:i for i,f in enumerate(frames)}
    packets=[];start=0
    while start<len(frames):
        stop=min(len(frames),start+32)
        # A coarse input interval is a boundary hint, never an accepted action.
        for s in states:
            a,b=positions[s['first_frame']],positions[s['last_frame']]
            if s['kind']=='input' and start<=a<stop<=b and b-start+1<=48:stop=b+1
        local=frames[max(0,start-6):min(len(frames),stop+6)]
        owned=[f['frame_id'] for f in frames[start:stop]];ids=set(owned)
        packets.append({'packet_id':f'packet-{len(packets)+1:03d}','frames':local,
                        # Freeze nearest-original-sample ownership; later revisits never move these cuts.
                        'owned_start_ms':((frames[start-1]['timestamp_ms']+frames[start]['timestamp_ms']+1)//2
                                          if start else frames[0]['timestamp_ms']),
                        'owned_end_ms':((frames[stop-1]['timestamp_ms']+frames[stop]['timestamp_ms']+1)//2
                                        if stop<len(frames) else frames[-1]['timestamp_ms']+1),
                        'owned_frame_ids':owned,'context_frame_ids':[f['frame_id'] for f in local if f['frame_id'] not in ids]})
        start=stop
    return packets


def bind_packet_events(raw_events,packet,frames,previous,tracks):
    """Use explicit edit identity; business actions always break continuation."""
    if not isinstance(raw_events,list) or len(raw_events)>50:_fail('events')
    times={f['frame_id']:f['timestamp_ms'] for f in frames}
    allowed={f['frame_id'] for f in packet['frames']};owned=set(packet['owned_frame_ids'])
    events=copy.deepcopy(previous);history=copy.deepcopy(tracks);last_trigger=-1
    for index,raw in enumerate(raw_events):
        if not isinstance(raw,dict) or set(raw)!=EVENT_FIELDS:_fail('event_shape')
        action=raw['action'];trigger=raw['trigger_frame'];kind=raw['trigger_kind']
        if action not in INTERACTIONS|{'aiAssert','aiWaitFor'}:_fail('action')
        if trigger not in owned or times[trigger]<last_trigger:_fail('trigger_ownership')
        last_trigger=times[trigger]
        refs=_refs(raw['frame_refs'],times,limit=60)
        if not set(refs)<=allowed or trigger not in refs:_fail('event_evidence')
        if (not isinstance(raw['uncertainties'],list) or len(raw['uncertainties'])>10 or
            any(not isinstance(q,str) or not q or len(q)>2000 for q in raw['uncertainties'])):_fail('questions')
        for key in ('target','observation','page_before','page_after'):_text(raw[key])
        _text(raw['continuation_of'],100,empty=True)
        if kind not in {'touch','pressed_control','navigation','edit','automatic','uncertain'}:_fail('trigger_kind')
        if kind=='uncertain' and not raw['uncertainties']:_fail('missing_uncertainty')
        if action in INTERACTIONS and kind=='automatic':_fail('automatic_interaction',_action_conflicts(raw_events,packet,times))
        if action not in INTERACTIONS and kind in {'touch','pressed_control','navigation','edit'}:_fail('interaction_as_state',_action_conflicts(raw_events,packet,times))
        if action=='aiInput' and (kind!='edit' or raw['value']!=''):_fail('input_contract',_action_conflicts(raw_events,packet,times))
        if raw['edit_boundary'] not in {'none','submission','field_change','navigation'}:_fail('edit_boundary')
        outcome=raw['outcome']
        if not isinstance(outcome,dict) or set(outcome)!={'frame_refs','observation'}:_fail('outcome')
        after=_refs(outcome['frame_refs'],times,limit=60)
        if not set(after)<=allowed:_fail('outcome_reference')
        if times[after[0]]<times[trigger] or times[after[-1]]<times[refs[-1]]:
            # Only IDs and timestamps already validated against actual packet images.
            anchors={'trigger':trigger,'evidence_last':refs[-1],
                     'outcome_first':after[0],'outcome_last':after[-1]}
            feedback={'event_index':index,**{name:{'frame_id':ref,'timestamp_ms':times[ref]}
                                           for name,ref in anchors.items()}}
            _fail('outcome_reference',feedback)
        _text(outcome['observation'])
        selected,action_compilation=_compile_references(refs,times,{refs[0],refs[-1],trigger})
        selected_outcome=[after[-1]]
        outcome_compilation={'method':'terminal_outcome_anchor','version':1,'raw_frame_refs':list(after),
                             'selected_frame_refs':list(selected_outcome),'required_anchors':list(selected_outcome)}
        compiled_outcome={**copy.deepcopy(outcome),'frame_refs':selected_outcome}
        fragment_compilation={'action':action_compilation,'outcome':outcome_compilation}
        ordinary={k:copy.deepcopy(raw[k]) for k in ('action','target','value','frame_refs','observation','uncertainties')}
        ordinary['frame_refs']=selected
        bound=_bind_whole_evidence({'events':[ordinary],'uncertainties':[],'multiple_scenarios':False},packet['frames'])
        checked=validate_analysis(bound,packet['frames'],frames[-1]['timestamp_ms']+1)['events'][0]
        checked['outcome']=compiled_outcome
        fragment=copy.deepcopy(raw)
        if raw['continuation_of']:
            if (not history or raw['continuation_of']!=history[-1]['event_id'] or action!='aiInput' or
                events[-1]['action']!='aiInput' or raw['target']!=events[-1]['target'] or
                raw['page_before']!=history[-1]['page_after'] or raw['page_after']!=raw['page_before'] or
                raw['edit_boundary']!='none' or history[-1]['edit_boundary']!='none' or
                times[trigger]<=times[history[-1]['fragments'][-1]['trigger_frame']] or
                times[refs[0]]<events[-1]['start_ms'] or checked['end_ms']<events[-1]['end_ms'] or
                times[after[-1]]<times[history[-1]['outcome']['frame_refs'][-1]] or
                max(0,times[trigger]-events[-1]['end_ms'])>5000):
                _fail('input_continuation',_continuation_feedback(index,raw,events,history,times,refs,after,checked))
            old=events[-1];track=history[-1]
            fragments=track['fragments']+[fragment]
            mandatory={r for part in fragments for r in (part['frame_refs'][0],part['frame_refs'][-1],part['trigger_frame'])}
            if len(mandatory)>8:_fail('input_evidence_budget')
            union=sorted(set(track['frame_refs'])|set(refs),key=times.get)
            selected,action_compilation=_compile_references(union,times,mandatory)
            # Identity is structural; unresolved evidence remains a review blocker.
            # Never resolve an earlier question just because a later fragment exists.
            old.update(frame_refs=selected,end_ms=checked['end_ms'],
                       observation=checked['observation'],outcome=compiled_outcome,
                       uncertainties=list(dict.fromkeys(old['uncertainties']+checked['uncertainties'])))
            fragment_compilations=track['reference_compilation']['fragments']+[fragment_compilation]
            track.update(fragments=fragments,frame_refs=sorted(set(track['frame_refs'])|set(refs),key=times.get),
                         page_after=raw['page_after'],outcome=copy.deepcopy(outcome),
                         reference_compilation={'method':'anchor_time_spread','version':1,'action':action_compilation,
                                                'outcome':outcome_compilation,'fragments':fragment_compilations})
        else:
            events.append(checked)
            history.append({'event_id':f"{packet['packet_id']}:event-{index}",'packet_id':packet['packet_id'],
                            'trigger_frame':trigger,'trigger_kind':kind,'page_before':raw['page_before'],
                            'page_after':raw['page_after'],'edit_boundary':raw['edit_boundary'],
                            'frame_refs':list(refs),'outcome':copy.deepcopy(outcome),'fragments':[fragment],
                            'reference_compilation':{'method':'anchor_time_spread','version':1,'action':action_compilation,
                                                     'outcome':outcome_compilation,'fragments':[fragment_compilation]}})
    return events,history


def bind_audit_checks(checks,tracks,frames):
    """The model judges claims; only immutable fragments supply references."""
    times={f['frame_id']:f['timestamp_ms'] for f in frames};by_id={t['event_id']:t for t in tracks};seen=set();bound=[]
    if not isinstance(checks,list) or len(checks)!=len(tracks):_fail('audit_missing_event')
    for c in checks:
        if not isinstance(c,dict) or set(c)!={'event_id','trigger_verdict','outcome_verdict','question'}:_fail('audit_shape')
        key=c['event_id']
        if not isinstance(key,str) or key not in by_id or key in seen:_fail('audit_identity')
        seen.add(key);track=by_id[key]
        for field in ('trigger_verdict','outcome_verdict'):
            if not isinstance(c[field],str) or c[field] not in {'supported','unclear','contradicted'}:_fail('audit_verdict')
        _text(c['question'],empty=True)
        supported=c['trigger_verdict']==c['outcome_verdict']=='supported'
        if supported and c['question']:_fail('audit_contradiction')
        if not supported and not c['question'].strip():_fail('audit_question')
        refs={track['trigger_frame'],track['outcome']['frame_refs'][-1]}
        if not refs<=set(times):_fail('audit_evidence')
        bound.append({**copy.deepcopy(c),'status':'confirmed' if supported else 'needs_review',
                      'frame_refs':sorted(refs,key=times.get),'reference_binding':'platform_immutable_fragment'})
    return bound


def validate_audit_issues(issues,packet):
    if not isinstance(issues,list) or len(issues)>30:_fail('audit_issues')
    times={f['frame_id']:f['timestamp_ms'] for f in packet['frames']};owned=set(packet['owned_frame_ids'])
    for issue in issues:
        if not isinstance(issue,dict) or set(issue)!={'kind','owner_frame','frame_refs','question'}:_fail('audit_issue_shape')
        if not isinstance(issue['kind'],str) or issue['kind'] not in {'missing_action','unresolved_evidence'}:_fail('audit_issue_kind')
        if not isinstance(issue['owner_frame'],str) or issue['owner_frame'] not in owned:_fail('audit_issue_owner')
        refs=_refs(issue['frame_refs'],times)
        if issue['owner_frame'] not in refs:_fail('audit_issue_owner')
        _text(issue['question'])
    return copy.deepcopy(issues)


_TRIGGER_LOCK_FIELDS=('action','target','value','trigger_frame','trigger_kind','continuation_of')


def _supported_trigger_locks(claims,checks):
    """Copy actual supported business candidates, preserving multiplicity."""
    supported={check['event_id'] for check in checks if check['trigger_verdict']=='supported'}
    return [{key:copy.deepcopy(claim['track'][key]) for key in _TRIGGER_LOCK_FIELDS}
            for claim in claims if claim['track']['event_id'] in supported and claim['track']['action'] in INTERACTIONS]


def _trigger_lock_identity(fragment):
    return json.dumps({key:fragment[key] for key in _TRIGGER_LOCK_FIELDS},sort_keys=True,ensure_ascii=False)


def _merge_positive_trigger_locks(previous,claims,checks):
    """Skipped/unclear audits cannot revoke earlier positive trigger evidence."""
    contradicted={check['event_id'] for check in checks if check['trigger_verdict']=='contradicted'}
    refuted=Counter(_trigger_lock_identity(claim['track']) for claim in claims
                    if claim['track']['event_id'] in contradicted and claim['track']['action'] in INTERACTIONS)
    supported=_supported_trigger_locks(claims,checks)
    # Maximum observed multiplicity, not an append across repeated audits.
    required=(Counter(map(_trigger_lock_identity,previous))-refuted)|Counter(map(_trigger_lock_identity,supported))
    merged=[];seen=Counter()
    for lock in previous+supported:
        identity=_trigger_lock_identity(lock)
        if seen[identity]<required[identity]:
            merged.append(copy.deepcopy(lock));seen[identity]+=1
    return merged


def _validate_positive_trigger_locks(locks,history,packet):
    """Compare newly bound current-owned fragments, never regenerated IDs."""
    if not locks:return
    owned=set(packet['owned_frame_ids'])
    available=Counter(_trigger_lock_identity(fragment) for track in history for fragment in track['fragments']
                      if fragment['trigger_frame'] in owned)
    required=Counter(map(_trigger_lock_identity,locks))
    missing=required-available
    if missing:
        feedback=[]
        for index,lock in enumerate(locks):
            identity=_trigger_lock_identity(lock)
            if identity in missing:
                feedback.append({'lock_index':index,'missing_count':missing.pop(identity)})
        _fail('audit_positive_trigger_changed',feedback)


def _envelope(raw,version=None):
    if version=='audit':
        if not isinstance(raw,dict) or set(raw)!={'checks','issues','multiple_scenarios','revisit'}:_fail('audit_shape')
        if type(raw['multiple_scenarios']) is not bool or not isinstance(raw['checks'],list) or not isinstance(raw['issues'],list):_fail('audit_shape')
        if not isinstance(raw['revisit'],list) or len(raw['revisit'])>2:_fail('revisit_shape')
        return raw
    if (not isinstance(raw,dict) or not ROOT_FIELDS<=set(raw)<=ROOT_FIELDS|{'revisit'} or type(raw['multiple_scenarios']) is not bool or
        not isinstance(raw['uncertainties'],list) or len(raw['uncertainties'])>30):_fail('root')
    for key in ('states','events','checks'):
        if not isinstance(raw[key],list):_fail('root_array')
    for q in raw['uncertainties']:_text(q)
    if not isinstance(raw.get('revisit',[]),list) or len(raw.get('revisit',[]))>2:_fail('revisit_shape')
    return raw


def overview_frames(frames,limit=60):
    if len(frames)<=limit:return frames
    # Actual timestamps, rather than density of selected micro-motion frames.
    start,end=frames[0]['timestamp_ms'],frames[-1]['timestamp_ms']
    ids={min(range(len(frames)),key=lambda i:abs(frames[i]['timestamp_ms']-(start+(end-start)*j/(limit-1)))) for j in range(limit)}
    return [f for i,f in enumerate(frames) if i in ids]


def audit_claim_context(trajectory,prior_events,*,execution_contract=None):
    """Fork audit claims without observer narrative; preserve original evidence."""
    validate_execution_contract(execution_contract)
    if execution_contract is not None:
        return project_audit_claims(trajectory),project_audit_claims(prior_events)
    claims=copy.deepcopy(trajectory);prior=copy.deepcopy(prior_events)
    for item in claims:item['track'].pop('observation',None)
    for item in prior:
        item['event'].pop('observation',None)
        for fragment in item['fragments']:fragment.pop('observation',None)
    return claims,prior


def _validated_audit_batches(claims,batcher,packet):
    """A callback may partition immutable claims, never rewrite or discard them."""
    batches=batcher(copy.deepcopy(claims))
    if not isinstance(batches,list) or not batches:raise ValueError('video_audit_batches_invalid')
    original={claim['track']['event_id']:claim for claim in claims};seen=set()
    available={frame['frame_id'] for frame in packet['frames']}
    if len(original)!=len(claims):raise ValueError('video_audit_batches_invalid')
    for batch in batches:
        if not isinstance(batch,list) or (not batch and (claims or len(batches)!=1)):
            raise ValueError('video_audit_batches_invalid')
        anchors=set()
        for claim in batch:
            track=claim.get('track') if isinstance(claim,dict) else None
            identity=track.get('event_id') if isinstance(track,dict) else None
            if (not isinstance(identity,str) or identity not in original or identity in seen or
                claim!=original[identity]):raise ValueError('video_audit_batches_invalid')
            seen.add(identity);anchors.update(track['required_frame_refs'])
        if len(anchors)>12 or not anchors<=available:raise ValueError('video_audit_batches_invalid')
    if seen!=set(original):raise ValueError('video_audit_batches_invalid')
    return copy.deepcopy(batches)


def _revisit_range(request,packet,allow_context_anchors):
    times={f['frame_id']:f['timestamp_ms'] for f in packet['frames']}
    start,end=packet['owned_start_ms'],packet['owned_end_ms']
    if (not isinstance(request,dict) or set(request)!={'first_frame','last_frame','question'} or
        type(request['first_frame']) is not str or type(request['last_frame']) is not str or
        request['first_frame'] not in times or request['last_frame'] not in times):_fail('revisit_reference')
    if not isinstance(request['question'],str) or not request['question'].strip() or len(request['question'])>2000:_fail('revisit_question')
    left,right=times[request['first_frame']],times[request['last_frame']]
    if not 0<right-left<=3000:_fail('revisit_span')
    if allow_context_anchors:
        if not {request['first_frame'],request['last_frame']} & set(packet['owned_frame_ids']):_fail('revisit_outside_owned_interval')
        clipped_left,clipped_right=max(left,start),min(right,end)
        if clipped_left>=clipped_right:_fail('revisit_outside_owned_interval')
    else:
        if left<start or right>end:_fail('revisit_outside_owned_interval')
        clipped_left,clipped_right=left,right
    return left,right,clipped_left,clipped_right


def validate_revisit_requests(requests,packet,*,allow_context_anchors=False):
    """Validate every locator before decode; bounded capability is explicit."""
    if type(allow_context_anchors)is not bool:raise ValueError('video_revisit_invalid')
    seen=set()
    for request in requests:
        bounds=_revisit_range(request,packet,allow_context_anchors)
        if allow_context_anchors and bounds[:2]in seen:_fail('revisit_reference')
        seen.add(bounds[:2])


def select_input_tail(frames,states,packets):
    """Freeze one unverified initial input-tail sampling hint from original metadata."""
    ordered=sorted(frames,key=lambda f:f['timestamp_ms'])
    positions={f['frame_id']:i for i,f in enumerate(ordered)}
    candidates={}
    for state in states:
        if not isinstance(state,dict) or state.get('kind')!='input':continue
        anchor=state.get('last_frame')
        if not isinstance(anchor,str) or anchor not in positions:continue
        i=positions[anchor]
        if i==0 or i==len(ordered)-1:continue
        left,right=ordered[i-1],ordered[i+1]
        span=right['timestamp_ms']-left['timestamp_ms']
        if not 500<=span<=3000:continue
        owner=next((p for p in packets if anchor in p['owned_frame_ids']),None)
        if owner is None:continue
        request={'first_frame':left['frame_id'],'last_frame':right['frame_id'],
                 'question':'核对输入区间末端相邻原始画面的可见变化。'}
        try:a,b,c,d=_revisit_range(request,owner,True)
        except ValueError:continue
        key=(-span,a,left['frame_id'],right['frame_id'])
        candidates[key]={'policy_version':'input-tail-v1','origin':'platform_input_tail',
            'packet_id':owner['packet_id'],'request':request,'requested_time_range_ms':[a,b],
            'decoded_time_range_ms':[c,d],
            'owned_time_range_ms':[owner['owned_start_ms'],owner['owned_end_ms']]}
    return copy.deepcopy(candidates[min(candidates)]) if candidates else None


def observe_trajectory(document,root,cancelled,progress=None,record=None,revisit=None,*,revisit_bounded=None,skill_runner=None,audit_batcher=None,execution_contract=None,diagnostics=None,business_timeline=None,engine_deadline=None,dense_input_tail_sampling=False,forward_context_issues=False):
    if type(forward_context_issues) is not bool or (forward_context_issues and not isinstance(business_timeline,dict)):
        raise ValueError('video_forward_context_invalid')
    validate_execution_contract(execution_contract)
    from .ui_video_context_questions import freeze_ownership, partition_issues
    forward_audit_reference_feedback=(
        'issues的owner_frame只能使用当前owned_frame_ids，或平台根据实际附图context角色、当前owned半开终点及冻结原始归属独立确定的未来owner（future_issue_owner_frame_ids）；'
        'owner必须包含在自身1至8个唯一且时间有序的frame_refs中；past context、unknown或未附图owner仍非法。'
        '未来finding仅为unverified_forward_context_question且instruction_authority=False，不是当前遗漏或已验证动作，不得据此推断当前动作。'
        '完整保留合法未来finding的问题及原始owner/refs；不得改派owner到当前包或删除问题来隐藏其他校验错误，应按原图修正其他无效字段，不能伪造归属或证据。'
        'checks必须逐项对应当前trajectory中的track.event_id且恰好一次；issues.frame_refs及revisit端点只能引用当前实际附图，既有check绑定及revisit时间/归属规则不变。')
    from . import ai_skill_service as ai
    from .ui_video_windows import load_assets
    if revisit_bounded is not None and not callable(revisit_bounded):raise ValueError('video_revisit_invalid')
    if skill_runner is not None and not callable(skill_runner):raise ValueError('video_skill_runner_invalid')
    if audit_batcher is not None and not callable(audit_batcher):raise ValueError('video_audit_batches_invalid')
    if (type(dense_input_tail_sampling) is not bool or
        (dense_input_tail_sampling and (not isinstance(business_timeline,dict) or revisit_bounded is None))):
        raise ValueError('video_dense_input_tail_invalid')
    run_skill=ai.run_ai_skill if skill_runner is None else skill_runner
    packet_image_limit=video_stage_image_limit('grounding' if business_timeline is not None else None)
    bounded_revisit=revisit_bounded is not None
    call_limit=32 if business_timeline is not None else 24
    diagnostic_limit=128 if business_timeline is not None else 64
    deferred_revisit_requests=[]
    forward_questions=[]
    frames=copy.deepcopy(document['media']['frames']);duration=document['media']['duration_ms']
    calls=0;corrections=0;correction_scope_start=0;revisits=0;started=time.monotonic();secrets=[];call_audit=[];revisit_attempts=[];repair_feedback=[]
    if engine_deadline is None:
        engine_deadline=started+1200
    elif (type(engine_deadline) not in (int,float) or not math.isfinite(engine_deadline) or
          engine_deadline>started+(4020 if business_timeline is not None else 1560)):
        raise ValueError('video_runtime_engine_deadline_invalid')
    def correction_available():
        return corrections-correction_scope_start<2
    # Business32 / legacy24 replies, bounded corrections,4 actual acquisitions.
    # Business128 / legacy64 diagnostic records retain deferred acquisitions.
    # Questions stay in immutable raw receipts; paths retain provenance without
    # turning rejected model narrative into execution authority.
    last_diagnostic={}
    rejected_questions=[]
    def preserve_questions(raw):
        if business_timeline is None or not isinstance(raw,dict):return
        root_questions=raw.get('uncertainties',[])
        if isinstance(root_questions,list):rejected_questions.extend(q for q in root_questions if isinstance(q,str) and q.strip() and len(q)<=2000)
        raw_events=raw.get('events',[])
        for event in raw_events if isinstance(raw_events,list) else []:
            if isinstance(event,dict) and isinstance(event.get('uncertainties'),list):
                rejected_questions.extend(q for q in event['uncertainties'] if isinstance(q,str) and q.strip() and len(q)<=2000)
    def diagnose(kind, **fields):
        if diagnostics is not None and len(diagnostics)<diagnostic_limit:
            diagnostics.append(copy.deepcopy({'kind':kind,**({} if fields.get('origin')=='platform_input_tail' else last_diagnostic),
                'corrections':corrections,'correction_scope_used':corrections-correction_scope_start,
                'correction_policy':'business_packet_two' if business_timeline is not None else 'legacy_global_two','revisits':revisits,
                'acquired_frames':sum(len(a['new_frames']) for a in revisit_attempts),**fields}))
    def question_refs(raw):
        if not isinstance(raw,dict):return {'status':'unverified_model_claims'}
        return {'status':'unverified_model_claims','root_path':'uncertainties',
                'root_uncertainty_count':len(raw.get('uncertainties',[])) if isinstance(raw.get('uncertainties'),list) else 0,
                'event_path':'events[*].uncertainties','audit_paths':['checks[*].question','issues[*].question'],
                'revisit_path':'revisit[*].question',
                'revisit_count':len(raw.get('revisit',[])) if isinstance(raw.get('revisit'),list) else 0}
    def redact(value):
        if isinstance(value,str):
            for secret in secrets:
                if secret:value=value.replace(secret,'[输入内容未保存]')
        elif isinstance(value,list):value=[redact(v) for v in value]
        elif isinstance(value,dict):value={k:redact(v) for k,v in value.items()}
        return value
    def ask_once(version,images,**context):
        nonlocal calls
        if cancelled():raise ValueError('video_cancelled')
        if calls>=call_limit or time.monotonic()>=engine_deadline:
            diagnose('capacity_refusal',reason='trajectory_calls' if calls>=call_limit else 'trajectory_deadline')
            raise ValueError('video_vision_limit')
        calls+=1;trace={};tick=time.monotonic()
        exhausted=[copy.deepcopy(a) for a in revisit_attempts if a['status']=='no_new_frames' and a.get('origin')!='platform_input_tail' and a['packet_id']==context.get('packet_id')]
        if exhausted:
            context.update(exhausted_revisit_ranges=exhausted,revisit_feedback='这些区间已成功解码全部真实原帧但没有新增帧，不会重复解码；继续核对当前同组原图。证据不够时保留unclear和具体问题，或据实际原图更正观察，不得因无新增帧而确认。')
        deferred=[copy.deepcopy(a) for a in deferred_revisit_requests if a['packet_id']==context.get('packet_id')]
        if deferred:
            context.update(deferred_revisit_requests=deferred,
                deferred_revisit_feedback='补帧尚未执行，not_acquired不等于无新增帧或证据支持。保留完整问题，独立核对现有证据；不能因补帧请求被拒绝而判定证据充分；仍按原图核对，无法判定时使用unclear并保留问题。')
        delivered=[q for q in forward_questions if q['target_packet_id']==context.get('packet_id')] if forward_context_issues else []
        if delivered and version in {'observe','audit'}:
            context['forward_context_questions']=copy.deepcopy(delivered)
        payload={'phase':'whole-trajectory','duration_ms':duration,'frames':images,'image_frames':images,**context,'reasoning_mode':'bounded'}
        if business_timeline is not None:
            if version in {'overview','observe'}:
                payload['business_timeline'] = copy.deepcopy(business_timeline)
            if version in {'observe','audit'}:
                payload['acquisition_budget'] = {
                    'remaining_revisit_attempts': max(0, 4-revisits),
                    'remaining_new_frames': max(0, 64-sum(len(a['new_frames']) for a in revisit_attempts)),
                    'packet_image_slots': max(0, packet_image_limit-len(images)),
                    'max_requests_per_response': 2, 'max_frames_per_request': 20,
                    'max_request_span_ms': 3000}
        if execution_contract is not None:payload['execution_contract']=execution_contract
        if version in {'observe','audit'}:payload['revisit_context_anchors']=bounded_revisit
        if record:record(f'{calls:02d}-{version}-request',redact(payload))
        last_diagnostic.clear()
        last_diagnostic.update(stage=version,packet_id=context.get('packet_id'),call=calls)
        for finding in delivered:
            if version in {'observe','audit'}:finding['observer_calls' if version=='observe' else 'audit_calls'].append(calls)
        raw=None
        try:
            raw=run_skill('ui_video_trajectory',payload,version=version,image_assets=load_assets(images,root,cancelled),
                                runtime_trace=trace,timeout=120,respect_global_timeout=False,retry_count=0,max_tokens=10000,repair_invalid_json=False)
        finally:
            last_diagnostic.update(copy.deepcopy(trace.get('video_record_refs',{})))
            last_diagnostic['questions_ref']=question_refs(raw)
            diagnose('response')
        if cancelled():raise ValueError('video_cancelled')
        if isinstance(raw,dict):
            secrets.extend(e.get('value') for e in raw.get('events',[]) if isinstance(e,dict) and e.get('action')=='aiInput' and isinstance(e.get('value'),str) and e['value'])
        entry={'call':calls,'stage':version,'elapsed_seconds':round(time.monotonic()-tick,3),
               'model':{k:trace[k] for k in ('providerId','model','selectedModel','fallbackUsed','source') if k in trace},
               'frame_refs':[f['frame_id'] for f in images],'requested_reasoning_mode':'bounded'}
        usage=trace.get('usage')
        if isinstance(usage,dict):
            counts={k:v for k,v in usage.items() if k in {'promptTokens','completionTokens','reasoningTokens','totalTokens'}
                    and type(v) in {int,float} and v>=0 and (type(v) is int or math.isfinite(v))}
            counts.update(cached_token_usage(usage))
            if counts:entry['usage']=counts
        if execution_contract is not None:entry['raw_response']=redact(copy.deepcopy(raw))
        if version=='audit' and audit_batcher is not None:
            entry.update(packet_id=context['packet_id'],
                         audit_target_event_ids=[p['track']['event_id'] for p in context['trajectory']],
                         audit_context_event_ids=[p['track']['event_id'] for p in context['context_trajectory']],
                         raw_response=redact(copy.deepcopy(raw)))
        call_audit.append(entry)
        if record:record(f'{calls:02d}-{version}-result',redact({'result':raw,'runtime':entry}))
        try:return _envelope(raw,version)
        except ValueError:
            preserve_questions(raw)
            raise
    def ask(version,images,validator=None,revisit_packet=None,**context):
        nonlocal corrections
        outer_feedback=context.get('validation_feedback','')
        if outer_feedback:_correction_feedback(context,'',outer_feedback)
        while True:
            raw=None
            try:
                raw=ask_once(version,images,**context)
                if version=='overview' and (raw['events'] or raw['checks']):_fail('overview_shape')
                if version=='observe' and raw['checks']:_fail('observe_checks')
                if revisit_packet is not None:validate_revisit_requests(raw.get('revisit',[]),revisit_packet,allow_context_anchors=bounded_revisit)
                if validator is not None:validator(raw)
                return raw
            except ValueError as error:
                from .ai_skill_service import SkillSchemaError
                if not isinstance(error,SkillSchemaError) and not hasattr(error,'trajectory_reason'):raise
                preserve_questions(raw)
                diagnose('rejection',reason=getattr(error,'trajectory_reason','schema'))
                if not correction_available():raise
                corrections+=1
                reason=getattr(error,'trajectory_reason','schema')
                rejection={'stage':version,'reason':reason}
                if reason.startswith('revisit_') and revisit_packet is not None and isinstance(raw,dict):
                    rejection.update(requests=copy.deepcopy(raw.get('revisit',[])),
                                     owned_time_range_ms=[revisit_packet['owned_start_ms'],revisit_packet['owned_end_ms']])
                if record:record(f'{calls:02d}-contract-rejection',redact(rejection))
                explanation={
                    'revisit_reference':'补帧端点必须是当前实际附图中的真实frame_id，且请求仅含first_frame/last_frame/question。',
                    'revisit_question':'补帧question必须是非空且不超过2000字符的问题。',
                    'revisit_outside_owned_interval':('context端点仅作定位，至少一端必须为owned且与当前owned半开区间有非空交集；平台只解码交集，不改变原帧归属。' if bounded_revisit else '补帧只能位于当前owned时间区间；future context属于后续owner，不能提前归入当前包。'),
                    'revisit_span':('原始定位端点first必须早于last且间隔不超过3000ms，裁剪后也必须有非空当前owned交集。' if bounded_revisit else '补帧first必须早于last且间隔不超过3000ms，只能请求当前owned区间。'),
                    'audit_shape':'audit根只能含checks/issues/multiple_scenarios/revisit；check只能含event_id/trigger_verdict/outcome_verdict/question，不得输出旧status/frame_refs或states/events。',
                    'audit_missing_event':'checks数量必须与trajectory一致，每个track.event_id恰好出现一次，不得漏项或重复。',
                    'audit_identity':'checks只能使用给定track.event_id，每个ID恰好一次。',
                    'frame_reference':'每项frame_refs必须为1至8个真实且唯一的原帧ID，按时间顺序排列。',
                    'audit_evidence':'每项frame_refs必须包含其required_frame_refs，额外帧只能来自同项allowed_frame_refs。',
                    'audit_verdict':'trigger_verdict和outcome_verdict只能是supported、unclear或contradicted字符串。',
                    'audit_issue_owner':(forward_audit_reference_feedback if forward_context_issues else
                        'issues的owner_frame必须属于本包owned_frame_ids且包含在该issue的frame_refs中，不可把context帧当本包新操作。'),
                    'audit_issue_shape':'issues每项只能含kind/owner_frame/frame_refs/question。',
                    'audit_issue_kind':'issue.kind只能为missing_action或unresolved_evidence。',
                    'audit_issues':'issues必须为最多30项的数组。',
                    'audit_question':'任一verdict不是supported时必须填写具体证据疑问。',
                    'audit_contradiction':'双supported的question必须为空；仍有疑问请明确使用unclear或contradicted。',
                }.get(reason,'当前阶段输出结构不符合合同，请只返回规定字段及合法值。')
                if isinstance(error,SkillSchemaError):
                    explanation=error.model_feedback+(
                        (forward_audit_reference_feedback if forward_context_issues else
                         'checks必须逐项对应当前trajectory中的track.event_id且恰好一次；owner_frame必须属于当前owned_frame_ids并包含在该issue的frame_refs中；issues.frame_refs及revisit端点只能引用当前实际附图。context触发属于其实际owner，不能另造当前issue。')
                        if version=='audit' else
                        'trigger_frame必须属于owned_frame_ids，context仅可提供证据，不能作为本包新动作触发。')
                if reason=='state_reference' and hasattr(error,'model_feedback'):
                    explanation+=json.dumps(error.model_feedback,ensure_ascii=False,separators=(',',':'))
                if reason.startswith('revisit_'):
                    explanation+=('本次支持context端点仅作定位：至少一个端点须为owned，原始跨度最多3000ms；平台只解码与当前owned半开区间相交的非空部分，不改派context原帧。'
                                  if bounded_revisit else '仅请求当前owned时间区间；future context由后续owner处理，不得夹取或改派原帧。')
                latest_feedback=('平台合同校验未通过：'+explanation+
                    ('重新核对全部附图，分别判断给定片段的触发和结果；check不返回帧号，由平台绑定原片段锚点。'
                     if version=='audit' else '重新核对本阶段全部原图，完整输出该阶段合同。')+'不补造操作。')
                _correction_feedback(context,outer_feedback,latest_feedback)
    def expand(packet,requests,*,origin=None):
        nonlocal revisits,frames
        if not requests:return 'not_attempted'
        if revisit is None and not bounded_revisit:_fail('revisit_unavailable')
        validate_revisit_requests(requests,packet,allow_context_anchors=bounded_revisit)
        times={f['frame_id']:f['timestamp_ms'] for f in frames}
        allowed={f['frame_id'] for f in packet['frames']}
        start,end=packet['owned_start_ms'],packet['owned_end_ms']
        outcome='not_attempted'
        platform=origin=='platform_input_tail'
        provenance={'origin':origin,'policy_version':'input-tail-v1'} if platform else {}
        if platform:
            # Optional headroom is checked only now; earlier model requests keep priority.
            reason=('revisit_attempts' if 4-revisits<2 else
                    'new_frame_capacity' if 64-sum(len(a['new_frames']) for a in revisit_attempts)<40 else
                    'request_image_capacity' if packet_image_limit-len(packet['frames'])<20 else
                    'engine_deadline' if engine_deadline-time.monotonic()<270.5 else None)
            if reason:
                dense_plan.update(status='not_acquired',reason=reason)
                diagnose('acquisition_deferred',**dense_plan)
                if record:record('dense-input-tail-deferred',redact(dense_plan))
                return 'not_acquired'
        def exhausted(request):
            span=[times[request['first_frame']],times[request['last_frame']]]
            return not platform and any(a['status']=='no_new_frames' and a['packet_id']==packet['packet_id'] and a['time_range_ms']==span for a in revisit_attempts)
        pending=[(i,r) for i,r in enumerate(requests) if not exhausted(r)]
        if business_timeline is not None and pending:
            available=min(64-sum(len(a['new_frames']) for a in revisit_attempts),
                          packet_image_limit-len(packet['frames']))
            reason=('revisit_attempts' if len(pending)>4-revisits else
                    'new_frame_capacity' if 64-sum(len(a['new_frames']) for a in revisit_attempts)<2*len(pending) else
                    'request_image_capacity' if available<2*len(pending) else None)
            if reason:
                for index,request in pending:
                    left,right,clipped_left,clipped_right=_revisit_range(request,packet,bounded_revisit)
                    deferred={'status':'not_acquired','reason':reason,'request':copy.deepcopy(request),
                        'request_index':index,'packet_id':packet['packet_id'],
                        'requested_time_range_ms':[left,right],'decoded_time_range_ms':None,
                        'owned_time_range_ms':[start,end],
                        'remaining_revisit_attempts':max(0,4-revisits),
                        'remaining_new_frames':max(0,64-sum(len(a['new_frames']) for a in revisit_attempts)),
                        'packet_image_slots':max(0,packet_image_limit-len(packet['frames'])),
                        **{k:copy.deepcopy(last_diagnostic[k]) for k in ('call','stage','proof_ref','receipt_ref') if k in last_diagnostic}}
                    deferred_revisit_requests.append(deferred)
                    # Full original question stays separate from fixed explanation (2000-char gate).
                    questions.extend([request['question'],'补帧尚未执行，证据问题未解决，需人工复核。'])
                    diagnose('acquisition_deferred',**deferred)
                    if record:record(f"deferred-revisit-{len(deferred_revisit_requests)}",redact(deferred))
                pending=[]
                outcome='not_acquired'
        pending_indexes={i for i,_ in pending}
        for index,request in enumerate(requests):
            left,right,clipped_left,clipped_right=_revisit_range(request,packet,bounded_revisit)
            if exhausted(request):
                if business_timeline is not None:questions.extend([request['question'],'补帧区间已无新增原帧，仍需核实。'])
                else:questions.append('补帧区间已无新增原帧，仍需核实：'+request['first_frame']+'至'+request['last_frame']+'：'+request['question'])
                continue
            if index not in pending_indexes:continue
            if revisits>=4:
                diagnose('capacity_refusal',reason='revisit_attempts',packet_frames=len(packet['frames']),
                         pending_request_indexes=list(range(index,len(requests))))
                _fail('revisit_budget')
            remaining=sum(not exhausted(pending) for pending in requests[index:])
            capacity=min(20,(packet_image_limit-len(packet['frames']))//remaining)
            if business_timeline is not None:
                capacity=min(capacity,(64-sum(len(a['new_frames']) for a in revisit_attempts))//remaining)
            if capacity<2:
                diagnose('capacity_refusal',reason='request_image_capacity',packet_frames=len(packet['frames']),
                         capacity=capacity,pending_request_indexes=list(range(index,len(requests))))
                raise ValueError('video_vision_limit')
            try:
                extra=(revisit_bounded(request,capacity,owned_time_range_ms=(start,end))
                       if bounded_revisit else revisit(request,capacity))
            except (ValueError,TimeoutError) as error:
                diagnose('acquisition_failure',**provenance,reason='loader_capacity' if str(error)=='video_revisit_limit' else 'acquisition_failed',
                         packet_frames=len(packet['frames']),capacity=capacity,
                         pending_request_indexes=list(range(index,len(requests))))
                raise
            if cancelled():raise ValueError('video_cancelled')
            if not isinstance(extra,list) or not 0<=len(extra)<=capacity:_fail('revisit_result')
            stamps=set(times.values());names=set(times)
            for f in extra:
                if (not isinstance(f,dict) or set(f)!={'frame_id','timestamp_ms'} or
                    type(f['frame_id']) is not str or f['frame_id'] in names or type(f['timestamp_ms']) is not int or
                    not clipped_left<=f['timestamp_ms']<=clipped_right or not start<=f['timestamp_ms']<end or
                    f['timestamp_ms'] in stamps):_fail('revisit_result')
                names.add(f['frame_id']);stamps.add(f['timestamp_ms'])
            revisits+=1
            if extra:outcome='frames_added'
            elif outcome!='frames_added':outcome='no_new_frames'
            attempt={'request':copy.deepcopy(request),'new_frames':copy.deepcopy(extra),'packet_id':packet['packet_id'],
                     'time_range_ms':[left,right],'requested_time_range_ms':[left,right],
                     'decoded_time_range_ms':[clipped_left,clipped_right],'decoded_end_exclusive':clipped_right==end,
                     'owned_time_range_ms':[start,end],'status':'frames_added' if extra else 'no_new_frames',**provenance}
            revisit_attempts.append(attempt)
            diagnose('acquisition',**provenance,status=attempt['status'],request_index=index,new_frame_count=len(extra),capacity=capacity,
                     **({'attempt':copy.deepcopy(attempt)} if platform else {}))
            if platform:dense_plan.update(status=attempt['status'],attempt=copy.deepcopy(attempt))
            # A sampling opportunity is not a model question or an evidence verdict.
            if not extra and not platform:
                if business_timeline is not None:questions.extend([request['question'],'补帧无新增原帧，仍需核实。'])
                else:questions.append('补帧无新增原帧，仍需核实：'+request['first_frame']+'至'+request['last_frame']+'：'+request['question'])
            frames=sorted(frames+extra,key=lambda f:f['timestamp_ms'])
            packet['frames']=sorted(packet['frames']+extra,key=lambda f:f['timestamp_ms'])
            owned=set(packet['owned_frame_ids'])|{f['frame_id'] for f in extra if start<=f['timestamp_ms']<end}
            packet['owned_frame_ids']=[f['frame_id'] for f in packet['frames'] if f['frame_id'] in owned]
            packet['context_frame_ids']=[f['frame_id'] for f in packet['frames'] if f['frame_id'] not in owned]
            allowed.update(f['frame_id'] for f in extra);times.update({f['frame_id']:f['timestamp_ms'] for f in extra})
            if record:record(f'revisit-{revisits}',redact(attempt))
        return outcome
    images=overview_frames(frames)
    overview=ask('overview',images)
    if overview['events'] or overview['checks']:_fail('overview_shape')
    states=validate_states(overview['states'],images)
    overview_hypotheses=copy.deepcopy(overview['uncertainties'])
    packets=make_packets(frames,states);events=[];tracks=[];questions=[];multi=overview['multiple_scenarios']
    ownership=freeze_ownership(frames,packets) if forward_context_issues else None
    dense_plan=select_input_tail(frames,states,packets) if dense_input_tail_sampling else None
    if dense_plan:
        dense_plan['status']='planned'
        diagnose('acquisition_plan',**dense_plan)
        if record:record('dense-input-tail-plan',redact(dense_plan))
    packet_outputs=[];checks=[];fragment_checks=[]
    for index,packet in enumerate(packets):
        if business_timeline is not None:correction_scope_start=corrections
        if dense_plan and packet['packet_id']==dense_plan['packet_id']:
            if cancelled():raise ValueError('video_cancelled')
            expand(packet,[dense_plan['request']],origin='platform_input_tail')
        if progress:progress('observing',index,len(packets),events)
        feedback='';audit_feedback=None;positive_trigger_locks=[];candidate_signatures=Counter()
        # Audit before proceeding: a requested revisit can re-observe this packet
        # without invalidating identities in any later packet.
        while True:
            raw=ask('observe',packet['frames'],revisit_packet=packet,states=states,overview_hypotheses=overview_hypotheses,packet_id=packet['packet_id'],
                    owned_frame_ids=packet['owned_frame_ids'],context_frame_ids=packet['context_frame_ids'],
                    owned_time_range_ms=[packet['owned_start_ms'],packet['owned_end_ms']],
                    prior_events=[{**t,'event':e} for t,e in list(zip(tracks,events))[-8:]],validation_feedback=feedback,
                    **({'audit_feedback':copy.deepcopy(audit_feedback)} if audit_feedback is not None else {}),
                    **({'candidate_preservation_constraints':{
                        'classification':'unverified_candidate_preservation_constraints','instruction_authority':False,
                        'signatures':[{'action':a,'trigger_frame':f,'trigger_kind':k,'multiplicity':n}
                                      for (a,f,k),n in candidate_signatures.items()]}}
                       if candidate_signatures else {}))
            try:
                extra=validate_states(raw['states'],packet['frames'])
                proposed,history=bind_packet_events(raw['events'],packet,frames,events,tracks)
                if execution_contract is not None:_validate_positive_trigger_locks(positive_trigger_locks,history,packet)
                if business_timeline is not None:
                    from .ui_video_temporal_ownership import ordinary_export_conflicts, unsafe_current_correction_conflicts
                    signatures=Counter((fragment['action'],fragment['trigger_frame'],fragment['trigger_kind'])
                        for track in history for fragment in track['fragments']
                        if fragment['trigger_frame'] in packet['owned_frame_ids'] and
                           fragment['action'] in INTERACTIONS and not fragment['continuation_of'])
                    if candidate_signatures-signatures:_fail('ordinary_candidate_preservation')
                    if candidate_signatures:candidate_signatures |= signatures
                    conflicts=ordinary_export_conflicts(proposed,history,frames,duration,packets)
                    required_conflicts=[c for c in conflicts if c['required']]
                    if required_conflicts:
                        if unsafe_current_correction_conflicts(required_conflicts,events,tracks,frames,packet['packet_id']):
                            _fail('ordinary_temporal_cross_packet',required_conflicts)
                        candidate_signatures |= signatures
                        _fail('ordinary_temporal_conflict',required_conflicts)
            except ValueError as error:
                reason=getattr(error,'trajectory_reason','contract')
                preserve_questions(raw)
                diagnose('rejection',reason=reason,**({'conflicts':error.model_feedback}
                    if reason.startswith('ordinary_temporal_') and hasattr(error,'model_feedback') else {}))
                if reason=='ordinary_temporal_cross_packet':raise
                if not correction_available():
                    if reason=='audit_positive_trigger_changed':_fail('audit_positive_trigger_exhausted')
                    raise
                corrections+=1
                if reason=='audit_positive_trigger_changed' and record:
                    record(f'{calls:02d}-positive-trigger-rejection',{'reason':reason,'packet_id':packet['packet_id']})
                feedback='结构或证据未通过：'+reason+'。重看同一组原图，完整输出当前包；不改写上下文动作，不补造操作。'
                if reason=='audit_positive_trigger_changed':
                    feedback+='audit_feedback.positive_trigger_locks是平台保留约束，不是结果支持；按零起始lock_index定位原锁，missing_count为该精确身份缺少的次数。逐字保留action、target、value、trigger_frame、trigger_kind、continuation_of，包括标点、空格、空值及次数；不加形容词、别名或语义改写，不删除、合并、改移触发或改成automatic规避。重看同组原图，完整输出当前包，仍按原有门禁核验本动作自身响应与证据，保留全部实质疑问。'
                    if hasattr(error,'model_feedback'):
                        feedback+=json.dumps(error.model_feedback,ensure_ascii=False,separators=(',',':'))
                if reason in {'interaction_as_state','automatic_interaction','input_contract'} and hasattr(error,'model_feedback'):
                    feedback+=json.dumps(error.model_feedback,ensure_ascii=False,separators=(',',':'))
                    feedback+='重新核对独立导航与自动响应。只有原图支持实际可见的等价控件时才能表达导航操作；不得猜测控件、删除独立导航或改变trigger_kind规避合同。完整保留真实疑问与输入。'
                if reason in {'ordinary_temporal_conflict','ordinary_candidate_preservation'}:
                    feedback+='已接受的历史事件、片段和审核保持不可变，只能重看当前包；触发时间仅用于纠正资格，最终仍须满足下一独立事件的实际证据起点上界。重看整个当前包，重新判断本动作自身证据与结果；区分无即时响应和后次操作后的模糊响应。保留每个独立动作的action/trigger_frame/trigger_kind及次数，这些是未验证候选保留约束，不是已获支持的审核锁。保留所有原问题；不得删除、合并独立动作、改移触发、自动裁剪证据或借用后次结果来制造成功。'
                    if hasattr(error,'model_feedback'):
                        feedback+=json.dumps(error.model_feedback,ensure_ascii=False,separators=(',',':'))
                if reason=='event_evidence':
                    feedback+='frame_refs必须保留该事件的真实trigger_frame和outcome锚点，只能选择已提供的原帧。'
                if reason=='outcome_reference' and hasattr(error,'model_feedback'):
                    feedback+=json.dumps(error.model_feedback,ensure_ascii=False,separators=(',',':'))
                    feedback+='重新判断本动作自身的证据范围及实际响应；保留后续独立动作及其触发和结果，不能借用后次动作结果。不能仅延长outcome或删去证据来满足顺序；重看原图后完整输出当前包。'
                if reason=='input_continuation':
                    if hasattr(error,'model_feedback'):
                        feedback+=json.dumps(error.model_feedback,ensure_ascii=False,separators=(',',':'))
                    feedback+='若原图支持续接，continuation_of和target分别沿用紧邻上一项event_id和event.target；page_before与page_after都必须逐字等于上一项page_after，不能用新的编辑区间state_id替换页面身份。'
                    feedback+='continuation_of只能延续紧邻的最后一个aiInput，且必须同页面同字段、无提交或其他业务操作间隔；不能把跨页导航合并为输入。'
                if reason=='state_reference' and hasattr(error,'model_feedback'):
                    feedback+=json.dumps(error.model_feedback,ensure_ascii=False,separators=(',',':'))
                if reason in {'state_shape','state_reference','state_kind'}:
                    feedback+='states每项必须且只能含state_id、first_frame、last_frame、kind、description；state_id和description为非空字符串，kind只能为page/input/wait/transition。first_frame和last_frame必须引用本次已提供的真实frame_id，first_frame不晚于last_frame，各项按first_frame时间排列。无新增状态时states可为[]；完整保留本包events和uncertainties，不能以删除状态代替修正元数据。'
                continue
            if expand(packet,raw.get('revisit',[])) in {'frames_added','no_new_frames'}:continue
            owned=set(packet['owned_frame_ids']);batch=[]
            for track,event in zip(history,proposed):
                for number,fragment in enumerate(track['fragments'],1):
                    if fragment['trigger_frame'] not in owned:continue
                    review={**copy.deepcopy(fragment),'event_id':f"{track['event_id']}:fragment-{number}",
                            'canonical_event_id':track['event_id']}
                    times={f['frame_id']:f['timestamp_ms'] for f in packet['frames']}
                    review['required_frame_refs']=sorted({review['trigger_frame'],review['outcome']['frame_refs'][-1]},key=times.get)
                    review['allowed_frame_refs']=sorted(set(review['frame_refs'])|set(review['outcome']['frame_refs']),key=times.get)
                    batch.append({'track':review})
            audit_batch,audit_prior=audit_claim_context(batch,[{**t,'event':e} for t,e in list(zip(tracks,events))[-8:]],execution_contract=execution_contract)
            audit_batches=([audit_batch] if audit_batcher is None else
                           _validated_audit_batches(audit_batch,audit_batcher,packet))
            audit_shards=[];bound_checks=[];expanded=False;repair=False
            for shard_index,shard in enumerate(audit_batches,1):
                first_call=calls+1;validated_audit=[]
                def validate_audit(result):
                    bound=bind_audit_checks(result['checks'],[p['track'] for p in shard],packet['frames'])
                    if forward_context_issues:partition_issues(result['issues'],packet,ownership)
                    else:validate_audit_issues(result['issues'],packet)
                    validated_audit.append(bound)
                while True:
                    audited=ask('audit',packet['frames'],revisit_packet=packet,
                            validator=validate_audit,
                            state_memory=states+extra,
                            **({'state_memory_status':'unverified_model_claims','prior_context_status':'unverified_model_claims'}
                               if execution_contract is not None else {'overview_hypotheses':overview_hypotheses}),
                            trajectory=shard,
                            **({'context_trajectory':audit_batch} if audit_batcher is not None else {}),
                            packet_id=packet['packet_id'],owned_frame_ids=packet['owned_frame_ids'],
                            context_frame_ids=packet['context_frame_ids'],
                            owned_time_range_ms=[packet['owned_start_ms'],packet['owned_end_ms']],
                            prior_events=audit_prior)
                    current_issues=audited['issues']
                    if forward_context_issues:
                        current_issues,findings=partition_issues(audited['issues'],packet,ownership)
                        # Whole envelope/revisit/check/issue validation has completed.
                        # Preserve all admitted questions before acquisition replaces raw.
                        questions.extend(i['question'] for i in audited['issues'])
                        questions.extend(c['question'] for c in audited['checks'] if c['question'])
                        for finding in findings:
                            row={**finding,'source_packet_id':packet['packet_id'],'audit_call':calls,
                                 'classification':'unverified_forward_context_question','instruction_authority':False,
                                 'observer_calls':[],'audit_calls':[],
                                 'origin_refs':copy.deepcopy({k:last_diagnostic[k] for k in ('proof_ref','receipt_ref','raw_response_ref') if k in last_diagnostic})}
                            forward_questions.append(row)
                        if findings:
                            diagnose('forward_context_questions',findings=[{k:copy.deepcopy(q[k]) for k in
                                ('source_packet_id','target_packet_id','issue_index','audit_call','origin_refs')}
                                for q in forward_questions[-len(findings):]],status='pending')
                    acquisition=expand(packet,audited.get('revisit',[]))
                    if acquisition=='frames_added' or (acquisition=='no_new_frames' and business_timeline is None):
                        expanded=True;break
                    if acquisition=='no_new_frames':
                        # Keep this candidate and earlier shards immutable. The next
                        # independent audit receives the original exhausted record;
                        # a repeated exhausted request cannot dispatch another loop.
                        questions.extend(issue['question'] for issue in audited['issues'])
                        questions.extend(check['question'] for check in audited['checks'] if check['question'])
                        missing=any(issue['kind']=='missing_action' for issue in current_issues)
                        contradicted=any(check[field]=='contradicted' for check in audited['checks']
                                         for field in ('trigger_verdict','outcome_verdict'))
                        if not (execution_contract is not None and (missing or contradicted)):continue
                    break
                if expanded:break
                shard_checks=validated_audit[-1]
                bound_checks.extend(shard_checks)
                audit_shards.append({'index':shard_index,'event_ids':[p['track']['event_id'] for p in shard],
                                     'calls':list(range(first_call,calls+1)),'raw':copy.deepcopy(audited),
                                     **({'current_issues':copy.deepcopy(current_issues)} if forward_context_issues else {})})
                missing=any(issue['kind']=='missing_action' for issue in current_issues)
                contradicted=any(check[field]=='contradicted' for check in audited['checks']
                                 for field in ('trigger_verdict','outcome_verdict'))
                if execution_contract is not None and (missing or contradicted):
                    if not correction_available():_fail('audit_contradiction_exhausted' if contradicted else 'audit_missing_action_exhausted')
                    corrections+=1
                    validated_checks=[copy.deepcopy(check) for part in audit_shards for check in part['raw']['checks']]
                    audited_ids={check['event_id'] for check in validated_checks}
                    audited_claims=[copy.deepcopy(claim) for claim in audit_batch if claim['track']['event_id'] in audited_ids]
                    positive_trigger_locks=_merge_positive_trigger_locks(positive_trigger_locks,audited_claims,validated_checks)
                    if business_timeline is not None:
                        refuted_ids={c['event_id'] for c in validated_checks if c['trigger_verdict']=='contradicted'}
                        # Only exact validated fragment refutations retire their own
                        # unverified multiplicity; supported locks remain authoritative.
                        candidate_signatures-=Counter((claim['track']['action'],claim['track']['trigger_frame'],claim['track']['trigger_kind'])
                            for claim in audited_claims if claim['track']['event_id'] in refuted_ids and
                            claim['track']['action'] in INTERACTIONS and not claim['track']['continuation_of'])
                    audit_feedback={'classification':'unverified_audit_finding_data','instruction_authority':False,
                                    'packet_id':packet['packet_id'],'audit_call':calls,
                                    'checks':validated_checks,'audited_claims':audited_claims,
                                    'positive_trigger_locks':copy.deepcopy(positive_trigger_locks),
                                    'issues':[copy.deepcopy(issue) for part in audit_shards for issue in (part['current_issues'] if forward_context_issues else part['raw']['issues'])]}
                    repair_feedback.append(copy.deepcopy(audit_feedback))
                    if record:record(f'{calls:02d}-audit-repair-feedback',redact(audit_feedback))
                    # Reobserve the whole current packet. No action, value, target
                    # or source fact is authored by this controller.
                    questions.extend(raw['uncertainties'])
                    questions.extend(q for event in raw['events'] for q in event['uncertainties'])
                    repair=True;break
            if expanded or repair:continue
            if audit_batcher is not None:
                order=[p['track']['event_id'] for p in audit_batch]
                bound_by_id={check['event_id']:check for check in bound_checks}
                bound_checks=[bound_by_id[identity] for identity in order]
                raw_by_id={check['event_id']:check for shard in audit_shards for check in shard['raw']['checks']}
                audited={'derived':True,'method':'audit_shards',
                         'checks':[copy.deepcopy(raw_by_id[identity]) for identity in order],
                         'issues':[copy.deepcopy(issue) for shard in audit_shards for issue in shard['raw']['issues']],
                         'multiple_scenarios':any(shard['raw']['multiple_scenarios'] for shard in audit_shards),
                         'revisit':[copy.deepcopy(request) for shard in audit_shards for request in shard['raw']['revisit']]}
                if record:record(packet['packet_id']+'-audit-combined',redact({'derived':True,'audit':audited,'audit_shards':audit_shards}))
            break
        events,tracks=proposed,history;states.extend(extra)
        packet_output={'packet':packet,'raw':redact(raw),'audit':redact(audited)}
        if audit_batcher is not None:packet_output['audit_shards']=redact(audit_shards)
        packet_outputs.append(packet_output)
        fragment_checks.extend(bound_checks)
        questions.extend(raw['uncertainties']+[issue['question'] for issue in audited['issues']])
        multi|=raw['multiple_scenarios'] or audited['multiple_scenarios']
    if forward_context_issues and any(not q['observer_calls'] or not q['audit_calls'] for q in forward_questions):
        _fail('forward_context_delivery_incomplete')
    by_id={c['event_id']:c for c in fragment_checks}
    for track,event in zip(tracks,events):
        decisions=[by_id[f"{track['event_id']}:fragment-{n}"] for n in range(1,len(track['fragments'])+1)]
        pending=[c for c in decisions if c['status']=='needs_review']
        question='；'.join(c['question'] for c in pending)
        checks.append({'event_id':track['event_id'],'status':'needs_review' if pending else 'confirmed',
                       'frame_refs':list(dict.fromkeys(r for c in decisions for r in c['frame_refs'])),
                       'question':question,'reference_binding':'platform_immutable_fragment',
                       'fragment_verdicts':[{k:c[k] for k in ('event_id','trigger_verdict','outcome_verdict','question')} for c in decisions]})
        for c in pending:event['uncertainties'].append('独立证据复核：'+c['question'])
    owners=[r for p in packets for r in p['owned_frame_ids']]
    if len(owners)!=len(set(owners)) or set(owners)!={f['frame_id'] for f in frames}:_fail('frame_ownership_incomplete')
    if not events:_fail('empty_trajectory')
    if business_timeline is not None:
        from .ui_video_temporal_ownership import ordinary_export_conflicts
        final_conflicts=ordinary_export_conflicts(events,tracks,frames,duration,packets)
        if any(c['required'] for c in final_conflicts):_fail('ordinary_temporal_final',final_conflicts)
        questions.extend(rejected_questions)
    try:
        checked=validate_analysis({'events':events,'uncertainties':list(dict.fromkeys(questions)),
            'multiple_scenarios':multi},frames,duration,secrets,max_events=120,
            max_questions=128 if business_timeline is not None else 30)
    except ValueError as error:
        if getattr(error,'analysis_reason',None)=='root_question_limit':
            diagnose('capacity_refusal',reason='trajectory_questions',
                question_count=error.question_count,question_limit=error.question_limit)
        raise
    for e in checked['events']:e['uncertainties']=list(dict.fromkeys(e['uncertainties']))
    result={**checked,'media':{**copy.deepcopy(document['media']),'frames':frames},'trajectory_audit':redact({'version':'trajectory-v2','audit_input_mode':'claims_without_observer_narrative','overview_hypotheses':overview_hypotheses,'states':states,'tracks':tracks,'checks':checks,'fragment_checks':fragment_checks,
             'packets':packet_outputs,**({'forward_context_questions':forward_questions} if forward_context_issues else {}),**({'deferred_revisit_requests':deferred_revisit_requests} if business_timeline is not None else {}),**({'dense_input_tail':dense_plan} if dense_input_tail_sampling else {}),'revisit_attempts':revisit_attempts,'calls':call_audit,'elapsed_seconds':round(time.monotonic()-started,3)})}
    result['trajectory_audit'].update(corrections_used=corrections,
        correction_policy='business_packet_two' if business_timeline is not None else 'legacy_global_two')
    if business_timeline is not None:
        result['trajectory_audit']['ordinary_export_conflicts']=final_conflicts
    if execution_contract is not None:
        result['execution_contract']=execution_contract
        result['trajectory_audit'].update(execution_contract=execution_contract,
            audit_input_mode='positive_claim_projection_v1',repair_feedback=redact(repair_feedback))
    if record:record('trajectory-final',redact(result))
    if progress:progress('planning',1,1,checked['events'])
    return result
