"""Pure bounded planning ownership; global evidence gates remain authoritative."""
import copy
import json

ROOT_KEYS={'title','events','uncertainties','multiple_scenarios','omitted_retries','print_recovery'}
EVENT_KEYS={'candidate_index','action','target','value','observation','uncertainties','outcome','selection','reusable_expected'}


class PlanningReplyError(ValueError):
    """Local reply contract failure, distinct from provider exceptions."""


def fail(reason):raise PlanningReplyError('video_planning_'+reason)


def partition(full):
    candidates=full['candidate_events'];images=full['image_frames']
    if not 1<=len(candidates)<=68:raise ValueError('video_planning_capacity')
    if [c['candidate_index'] for c in candidates]!=list(range(len(candidates))):fail('candidate_index_space')
    image_ids={f['frame_id'] for f in images}
    if len(image_ids)!=len(images) or len(images)>60:fail('partition_images')
    if any(not set(c['ordinary_outcome_frame_ids'])<=image_ids for c in candidates):fail('outcome_image_missing')
    options=[]
    single=len(candidates)<=34 and 2<=len(images)<=40
    for cut in ([len(candidates)] if single else range(1,len(candidates))):
        stamp=candidates[cut]['start_ms'] if not single else float('inf')
        if any(c['start_ms']<stamp<c['end_ms'] for c in candidates):continue
        if any(c['end_ms']>stamp for c in candidates[:cut]):continue
        ranges=[list(range(cut))] if single else [list(range(cut)),list(range(cut,len(candidates)))];groups=[];sizes=[]
        for ordinal,owned in enumerate(ranges):
            if len(owned)>34:break
            selected={f['frame_id'] for f in images if (f['timestamp_ms']<stamp)==(ordinal==0)}
            selected.update(ref for i in owned for ref in candidates[i]['ordinary_outcome_frame_ids'])
            selected.update(ref for i in owned for ref in candidates[i].get('frame_refs',[]) if ref in image_ids)
            attached=[copy.deepcopy(f) for f in images if f['frame_id'] in selected]
            if not 2<=len(attached)<=40:break
            payload=copy.deepcopy(full);payload['image_frames']=attached
            owned_set=set(owned)
            # Every global candidate row, including prose/questions/domains, stays intact.
            payload['required_candidate_indexes']=[i for i in full['required_candidate_indexes'] if i in owned_set]
            payload['planning_scope']={'version':'bounded-business-ownership-v1','group_index':ordinal,'group_count':len(ranges),
                'owned_candidate_indexes':owned,'read_only_context_candidate_indexes':[i for i in range(len(candidates)) if i not in owned_set],
                'global_required_candidate_indexes':list(full['required_candidate_indexes']),
                'context_status':'unverified_source_text_not_execution_or_success_authority',
                'scope':'planning_request_only_merge_into_one_global_case',
                'original_global_outcome_intervals_preserved':True}
            groups.append(payload);sizes.append(len(json.dumps([candidates[i] for i in owned],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()))
        if len(groups)!=len(ranges):continue
        score=(max(len(g['image_frames']) for g in groups),max(map(len,ranges)),max(sizes),cut)
        options.append((score,groups))
    if not options:raise ValueError('video_planning_capacity')
    return min(options,key=lambda row:row[0])[1]


def validate_reply(group,reply):
    if not isinstance(reply,dict) or set(reply)-ROOT_KEYS or not {'events','uncertainties','multiple_scenarios'}<=set(reply):fail('reply_shape')
    if type(reply['multiple_scenarios']) is not bool or not isinstance(reply['events'],list):fail('reply_shape')
    if not isinstance(reply['uncertainties'],list) or len(reply['uncertainties'])>30 or any(not isinstance(q,str) or len(q)>2000 for q in reply['uncertainties']):fail('reply_questions')
    if 'title' in reply and (not isinstance(reply['title'],str) or len(reply['title'])>80):fail('reply_title')
    owned=set(group['planning_scope']['owned_candidate_indexes']);seen=set();last=-1
    attached={f['frame_id']:f['timestamp_ms'] for f in group['image_frames']}
    for event in reply['events']:
        if not isinstance(event,dict) or set(event)-EVENT_KEYS or not {'candidate_index','action','target','value','observation','uncertainties'}<=set(event):fail('event_shape')
        index=event['candidate_index']
        if type(index) is not int or index not in owned or index in seen or index<=last:fail('reply_ownership')
        if event['action']!=group['candidate_events'][index]['action']:fail('reply_action')
        if (any(not isinstance(event[k],str) for k in ('target','observation')) or
                not isinstance(event['value'],(str,dict)) or
                not isinstance(event['uncertainties'],list) or len(event['uncertainties'])>30 or
                any(not isinstance(q,str) or len(q)>2000 for q in event['uncertainties']) or
                ('selection' in event and not isinstance(event['selection'],str)) or
                ('reusable_expected' in event and not isinstance(event['reusable_expected'],str))):fail('event_shape')
        seen.add(index);last=index
        if 'outcome' in event:
            outcome=event['outcome'];refs=outcome.get('frame_refs') if isinstance(outcome,dict) else None
            if (not isinstance(outcome,dict) or not isinstance(outcome.get('observation'),str) or set(outcome)!={'observation','frame_refs'} or not isinstance(refs,list) or
                    not refs or len(refs)>8 or any(type(r) is not str or r not in attached for r in refs) or
                    len(set(refs))!=len(refs) or [attached[r] for r in refs]!=sorted(attached[r] for r in refs)):
                fail('reply_outcome_images')
    omitted=set()
    declarations=reply.get('omitted_retries',[])
    if not isinstance(declarations,list) or len(declarations)>20:fail('reply_omission')
    for row in declarations:
        if not isinstance(row,dict) or set(row)!={'candidate_index','repeated_by','reason'}:fail('reply_omission')
        index,representative=row['candidate_index'],row['repeated_by']
        if type(index) is not int or type(representative) is not int or index not in owned or not 0<=representative<len(group['candidate_events']) or index in seen or index in omitted:fail('reply_omission_ownership')
        if not isinstance(row['reason'],str) or len(row['reason'])>2000:fail('reply_omission')
        omitted.add(index)
    if 'print_recovery' in reply:
        recovery=reply['print_recovery']
        if not isinstance(recovery,dict) or set(recovery)!={'submit','cancel','confirm','end'} or any(type(v) is not int or not 0<=v<len(group['candidate_events']) for v in recovery.values()) or recovery['cancel'] not in owned:fail('reply_recovery_ownership')


def merge(full,groups,replies):
    if not 1<=len(groups)<=2 or len(replies)!=len(groups):fail('merge_count')
    events=[];questions=list(full['raw_uncertainties']);omissions=[];recovery=[];title=None;multi=full['multiple_scenarios']
    for group,reply in zip(groups,replies):
        validate_reply(group,reply)
        events.extend(copy.deepcopy(reply['events']));questions.extend(reply['uncertainties']);multi|=reply['multiple_scenarios']
        omissions.extend(copy.deepcopy(reply.get('omitted_retries',[])))
        if 'print_recovery' in reply:recovery.append(copy.deepcopy(reply['print_recovery']))
        if title is None and reply.get('title','').strip():title=reply['title']
    ids=[e['candidate_index'] for e in events]
    if len(ids)!=len(set(ids)):fail('merge_duplicate')
    if len(recovery)>1:fail('merge_recovery_conflict')
    omitted=[o['candidate_index'] for o in omissions]
    if len(omissions)>20 or len(omitted)!=len(set(omitted)) or set(omitted)&set(ids):fail('merge_omission_conflict')
    result={'events':sorted(events,key=lambda e:e['candidate_index']),'uncertainties':list(dict.fromkeys(questions)),'multiple_scenarios':multi}
    if title is not None:result['title']=title
    if omissions:result['omitted_retries']=omissions
    if recovery:result['print_recovery']=recovery[0]
    return result
