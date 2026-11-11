"""Explicit human decisions bound to the source pixels and draft contents."""
import hashlib
import json
import os
import re
import stat


def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def content_hash(draft):
    return _digest({k:draft.get(k) for k in ('title','preconditions','steps','questions','yaml_content','yaml_candidate')})


def source_projection(steps):
    """Freeze executable source semantics, allowing review of input and expected values."""
    return [{key:value for key,value in step.items() if key not in ('expected','review_state') and
             not (key=='value' and step.get('action')=='aiInput')}
            for step in steps if isinstance(step,dict) and str(step.get('source_ref','')).startswith('video:')]


def source_projection_matches(draft):
    snapshot=draft['source_snapshot']
    expected=snapshot.get('touch_source_projection')
    return isinstance(expected,list) and expected==source_projection(draft['steps'])


def manual_position_valid(draft, frame_id, step_id):
    """A new tap must lie between the original events around its source frame."""
    try:
        source=draft['source_snapshot'];frames=source['frames'];events=source['events'];steps=draft['steps']
        stamps=[f['timestamp_ms'] for f in frames if f['frame_id']==frame_id]
        positions=[i for i,step in enumerate(steps) if step.get('step_id')==step_id]
        if len(stamps)!=1 or type(stamps[0]) is not int or len(positions)!=1:return False
        stamp=stamps[0];position=positions[0]
        group=source.get('print_recovery')
        if group is not None:
            if not isinstance(group,dict) or type(group.get('submit')) is not int or not 0<=group['submit']<len(events):return False
            submit_start=events[group['submit']]['start_ms']
            # Both submitPrint task binding and cancelPrint terminal wait are
            # composite operations without trusted insertion end bounds.
            if type(submit_start) is not int or stamp>=submit_start:return False
        prefix=f"video:{source['source_id']}:{source['analysis_version']}:"
        for i,step in enumerate(steps):
            ref=step.get('source_ref','')
            if not isinstance(ref,str) or not ref.startswith('video:'):continue
            if not ref.startswith(prefix) or not ref[len(prefix):].isdigit():return False
            event_index=int(ref[len(prefix):])
            if not 0<=event_index<len(events):return False
            event=events[event_index];start=event['start_ms'];end=event['end_ms']
            if type(start) is not int or type(end) is not int or start>end:return False
            if end<stamp and i>=position:return False
            if start>stamp and i<=position:return False
            if start<=stamp<=end:return False
        return True
    except (KeyError,TypeError,ValueError):return False


def manual_decisions_order_valid(draft, decisions):
    try:
        frames=draft['source_snapshot']['frames'];steps=draft['steps'];pairs=[]
        for decision in decisions.values():
            if decision.get('disposition')!='manual_step':continue
            stamps=[f['timestamp_ms'] for f in frames if f['frame_id']==decision['frame_id']]
            positions=[i for i,s in enumerate(steps) if s.get('step_id')==decision['step_id']]
            if len(stamps)!=1 or type(stamps[0]) is not int or len(positions)!=1:return False
            pairs.append((stamps[0],positions[0]))
        for i,(stamp,position) in enumerate(pairs):
            for other_stamp,other_position in pairs[i+1:]:
                if stamp==other_stamp or (stamp-other_stamp)*(position-other_position)<=0:return False
        return True
    except (KeyError,TypeError,ValueError,AttributeError):return False


def redact_touch_evidence(items, secrets):
    from .ui_video_analysis import _redact_question
    result=[]
    for item in items:
        entry=dict(item)
        for key in ('target','observation'):
            if key in entry:entry[key]=_redact_question(entry[key],secrets)
        if 'questions' in entry:entry['questions']=[_redact_question(q,secrets) for q in entry['questions']]
        result.append(entry)
    return result


def _matches_review_source(source, draft):
    snapshot=draft['source_snapshot']
    if snapshot.get('analysis_mode')!='first_pass':
        return source['status']=='ready'
    return (source['status'] in {'review_needed','failed','interrupted','cancelled'} and
            source.get('retained') and source.get('draft_id')==draft['draft_id'] and
            bool(snapshot.get('first_pass_events')) and
            source.get('first_pass_events')==snapshot['first_pass_events'])


def _source_evidence(draft):
    """Require exact owner/app/version/source evidence and original frame bytes."""
    from .ui_video_source_service import VideoStore
    snapshot=draft['source_snapshot']; items=snapshot['touch_ambiguities']
    if (snapshot.get('kind')!='video' or snapshot.get('app_id')!=draft['app_id'] or
        type(snapshot.get('analysis_version')) is not int or not isinstance(items,list) or
        not 1<=len(items)<=100 or not isinstance(snapshot.get('frames'),list)):
        raise ValueError('video_touch_evidence_invalid')
    store=VideoStore(); identifier=snapshot['source_id']
    source=store.get(identifier,draft['owner_user_id'],draft['app_id'])
    if (source['analysis_version']!=snapshot['analysis_version'] or not _matches_review_source(source,draft) or
        source.get('touch_ambiguities')!=items or source.get('media',{}).get('frames')!=snapshot['frames']):
        raise ValueError('video_touch_evidence_invalid')
    from .ui_video_workspace import resolve_frame_directory
    frame_directory=resolve_frame_directory(store.root,source)
    valid_frames={f['frame_id'] for f in snapshot['frames']}
    frame_hashes={};total_bytes=0
    for item in items:
        if not isinstance(item,dict):raise ValueError('video_touch_evidence_invalid')
        frame=item.get('frame_id');digest=item.get('frame_sha256')
        if (item.get('reason')!='no_match' or item.get('phase') not in ('observe','review') or
            not isinstance(frame,str) or not re.fullmatch(r'frame-(?:[0-9]{3,6}|end)',frame) or frame not in valid_frames or
            not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest)):
            raise ValueError('video_touch_evidence_invalid')
        if frame in frame_hashes:
            if frame_hashes[frame]!=digest:raise ValueError('video_touch_evidence_invalid')
            continue
        fd=os.open(frame_directory/(frame+'.jpg'),os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=8*1024*1024:raise ValueError('video_touch_evidence_invalid')
            total_bytes+=info.st_size
            if total_bytes>32*1024*1024:raise ValueError('video_touch_evidence_invalid')
            h=hashlib.sha256();remaining=info.st_size
            while remaining:
                block=os.read(fd,min(65536,remaining))
                if not block:raise ValueError('video_touch_evidence_invalid')
                h.update(block);remaining-=len(block)
            if os.read(fd,1):raise ValueError('video_touch_evidence_invalid')
            if h.hexdigest()!=digest:raise ValueError('video_touch_evidence_invalid')
            frame_hashes[frame]=digest
        finally:os.close(fd)
    return items


def read_verified_touch_frame(draft,index):
    """Return the exact hashed bytes, never a path to re-open after checking."""
    items=_source_evidence(draft)
    if type(index) is not int or not 0<=index<len(items):raise ValueError('video_touch_evidence_invalid')
    from .ui_video_source_service import VideoStore
    from .ui_video_workspace import resolve_frame_directory
    source=draft['source_snapshot'];store=VideoStore()
    doc=store.get(source['source_id'],draft['owner_user_id'],draft['app_id'])
    if (doc['analysis_version']!=source['analysis_version'] or doc.get('touch_ambiguities')!=items or
        doc.get('media',{}).get('frames')!=source['frames'] or not _matches_review_source(doc,draft)):
        raise ValueError('video_touch_evidence_invalid')
    frame=items[index]['frame_id'];path=resolve_frame_directory(store.root,doc)/(frame+'.jpg')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=8*1024*1024:raise ValueError('video_touch_evidence_invalid')
        remaining=info.st_size;chunks=[]
        while remaining:
            block=os.read(fd,min(65536,remaining))
            if not block:raise ValueError('video_touch_evidence_invalid')
            chunks.append(block);remaining-=len(block)
        if os.read(fd,1):raise ValueError('video_touch_evidence_invalid')
        data=b''.join(chunks)
    finally:os.close(fd)
    if hashlib.sha256(data).hexdigest()!=items[index]['frame_sha256']:raise ValueError('video_touch_evidence_invalid')
    latest=store.get(source['source_id'],draft['owner_user_id'],draft['app_id'])
    if (latest['analysis_version']!=source['analysis_version'] or latest.get('touch_ambiguities')!=items or
        latest.get('media',{}).get('frames')!=source['frames'] or not _matches_review_source(latest,draft)):
        raise ValueError('video_touch_evidence_invalid')
    return data


def decision_for(draft,index,frame,disposition,step_id,actor):
    items=_source_evidence(draft)
    if not source_projection_matches(draft):raise ValueError('video_touch_decision_invalid')
    if type(index) is not int or not 0<=index<len(items) or items[index]['frame_id']!=frame:
        raise ValueError('video_touch_evidence_invalid')
    if any(key!=str(index) and value.get('frame_id')==frame and value.get('disposition')!=disposition
           for key,value in draft.get('touch_decisions',{}).items()):
        raise ValueError('video_touch_decision_invalid')
    if disposition=='not_action':
        if step_id!='':raise ValueError('video_touch_decision_invalid')
    elif disposition=='manual_step':
        if sum(item.get('frame_id')==frame for item in items)>1:
            raise ValueError('video_touch_same_frame_ambiguous')
        matches=[s for s in draft['steps'] if s.get('step_id')==step_id]
        if (len(matches)!=1 or matches[0].get('source_ref')!='text' or matches[0].get('action')!='aiTap' or
            matches[0].get('review_state')!='confirmed' or not matches[0].get('expected') or
            not manual_position_valid(draft,frame,step_id)):
            raise ValueError('video_touch_decision_invalid')
        if any(key!=str(index) and value.get('disposition')=='manual_step' and value.get('step_id')==step_id
               for key,value in draft.get('touch_decisions',{}).items()):
            raise ValueError('video_touch_decision_invalid')
    else:raise ValueError('video_touch_decision_invalid')
    decision={'index':index,'frame_id':frame,'evidence_sha256':_digest(items[index]),
            'source_id':draft['source_snapshot']['source_id'],'app_id':draft['app_id'],
            'analysis_version':draft['source_snapshot']['analysis_version'],
            'content_sha256':content_hash(draft),'disposition':disposition,'step_id':step_id,
            'actor_user_id':actor['user_id'],'previous_revision':draft['revision'],
            'revision':draft['revision']+1}
    decisions=dict(draft.get('touch_decisions',{}));decisions[str(index)]=decision
    if not manual_decisions_order_valid(draft,decisions):raise ValueError('video_touch_decision_invalid')
    return decision


def touch_review_blocked(draft):
    snapshot=draft.get('source_snapshot',{})
    if not isinstance(snapshot,dict) or 'touch_ambiguities' not in snapshot:return False
    items=snapshot['touch_ambiguities']
    if not isinstance(items,list):return True
    if not items:return False
    decisions=draft.get('touch_decisions',{})
    if not isinstance(decisions,dict) or set(decisions)!={str(i) for i in range(len(items))}:return True
    try:
        _source_evidence(draft)
        if not source_projection_matches(draft):return True
        if not manual_decisions_order_valid(draft,decisions):return True
        used_manual_steps=set();frame_dispositions={}
        for index,item in enumerate(items):
            decision=decisions[str(index)]
            if item.get('frame_id') in frame_dispositions and frame_dispositions[item['frame_id']]!=decision.get('disposition'):return True
            frame_dispositions[item.get('frame_id')]=decision.get('disposition')
            if (decision.get('index')!=index or decision.get('frame_id')!=item['frame_id'] or
                decision.get('evidence_sha256')!=_digest(item) or decision.get('source_id')!=snapshot['source_id'] or
                decision.get('analysis_version')!=snapshot['analysis_version'] or decision.get('app_id')!=draft['app_id'] or
                decision.get('actor_user_id')!=draft['owner_user_id'] or decision.get('content_sha256')!=content_hash(draft) or
                decision.get('disposition') not in ('not_action','manual_step')):return True
            if (type(decision.get('revision')) is not int or type(decision.get('previous_revision')) is not int or
                decision['revision'] != decision['previous_revision']+1 or not 1<decision['revision']<=draft['revision']):return True
            if decision['disposition']=='not_action' and decision.get('step_id')!='':return True
            if decision['disposition']=='manual_step':
                if sum(other.get('frame_id')==item['frame_id'] for other in items)>1:return True
                if decision.get('step_id') in used_manual_steps:return True
                used_manual_steps.add(decision.get('step_id'))
                matches=[s for s in draft['steps'] if s.get('step_id')==decision.get('step_id')]
                if (len(matches)!=1 or matches[0].get('source_ref')!='text' or matches[0].get('action')!='aiTap' or
                    matches[0].get('review_state')!='confirmed' or not matches[0].get('expected') or
                    not manual_position_valid(draft,item['frame_id'],decision.get('step_id'))):return True
        return False
    except (KeyError,TypeError,ValueError,OSError,AttributeError):return True
