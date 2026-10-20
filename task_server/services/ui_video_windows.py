"""Bounded full-timeline observation windows; no partial result is publishable."""
import base64


def load_assets(frames,root,cancelled):
    assets=[];total=0
    for frame in frames:
        if cancelled():raise ValueError('video_cancelled')
        path=root/'frames'/(frame['frame_id']+'.jpg')
        if path.stat().st_size>8*1024*1024:raise ValueError('video_vision_limit')
        data=base64.b64encode(path.read_bytes()).decode();total+=len(data)
        if total>10*1024*1024:raise ValueError('video_vision_limit')
        assets.append({'name':frame['frame_id'],'timestamp_ms':frame['timestamp_ms'],'mime':'image/jpeg','base64':data})
    return assets



def context_frames(window,frames,root,events,cancelled):
    """Select prior look-alike pages as references, never as action evidence."""
    prior=[f for f in frames if f['timestamp_ms']<window[0]['timestamp_ms']-2000]
    if not prior:return []
    try:
        from PIL import Image,ImageChops,ImageStat
        small={}
        for f in prior+window:
            if cancelled():raise ValueError('video_cancelled')
            with Image.open(root/'frames'/(f['frame_id']+'.jpg')) as source:
                small[f['frame_id']]=source.convert('RGB').resize((32,64))
        ranked={}
        for current in window:
            scores=[(sum(ImageStat.Stat(ImageChops.difference(small[f['frame_id']],small[current['frame_id']])).mean),f['frame_id']) for f in prior]
            score,ref=min(scores)
            ranked[ref]=min(score,ranked.get(ref,float('inf')))
        ids=set(sorted(ranked,key=ranked.get)[:16])
        return [f for f in prior if f['frame_id'] in ids]
    except (OSError,ImportError):
        # Optional reference ranking cannot authorize any action. The normal
        # asset transport still validates the actual images and local refs.
        refs=list(dict.fromkeys(e['frame_refs'][-1] for e in events if e['end_ms']<window[0]['timestamp_ms']))
        if len(refs)>16:refs=[refs[round(i*(len(refs)-1)/15)] for i in range(16)]
        return [f for f in prior if f['frame_id'] in refs]


def temporal_context_frames(window,frames):
    """Bounded nearby pages explain transitions without becoming local evidence."""
    if not window:return []
    start,end=window[0]['timestamp_ms'],window[-1]['timestamp_ms']
    before=[f for f in frames if f['timestamp_ms']<start]
    after=[f for f in frames if f['timestamp_ms']>end]
    ids=set()
    for pool,targets in ((before,[start-3000,start-1500,start-500,start-1]),
                         (after,[end+1,end+500,end+1500,end+3000])):
        for target in targets:
            if pool:ids.add(min(pool,key=lambda f:abs(f['timestamp_ms']-target))['frame_id'])
    return [f for f in frames if f['frame_id'] in ids]


def _same_scroll_direction_conflict(old,event):
    """Same gesture evidence can disagree on finger versus content direction."""
    if old['action']!=event['action'] or event['action']!='aiScroll' or old['start_ms']!=event['start_ms']:
        return False
    left,right=old['value'],event['value']
    if not isinstance(left,dict) or not isinstance(right,dict):return False
    return ({left.get('direction'),right.get('direction')} in ({'up','down'},{'left','right'}) and
            {k:v for k,v in left.items() if k!='direction'}=={k:v for k,v in right.items() if k!='direction'})


def observe_windows(frames,duration,root,cancelled,progress=None,review_events=None,prior_secrets=()):
    from . import ai_skill_service as ai
    from .ui_video_analysis import _bind_whole_evidence,validate_analysis
    size=30 if len(frames)>60 else 60
    windows=[frames[i:i+size] for i in range(0,max(1,len(frames)-4),size-4)]
    events=[];questions=[];secrets=list(prior_secrets);multi=False;previous_end=-1
    for index,window in enumerate(windows):
        if cancelled():raise ValueError('video_cancelled')
        payload={'phase':'whole-observe','frames':window,'image_frames':window,'duration_ms':duration}
        if len(windows)>1:
            payload['window']={'index':index+1,'total':len(windows),'previous_end_ms':previous_end,
                               'final':index==len(windows)-1}
            payload['prior_events']=[{k:e[k] for k in ('action','target','value','observation','uncertainties')} for e in events[-8:]]
        temporal=temporal_context_frames(window,frames) if len(windows)>1 else []
        temporal_ids={f['frame_id'] for f in temporal}
        current_ids={f['frame_id'] for f in window}
        images=[f for f in frames if f['frame_id'] in current_ids|temporal_ids]
        payload.update(image_frames=images,context_frame_ids=[f['frame_id'] for f in temporal],
                       temporal_context_frame_ids=[f['frame_id'] for f in temporal])
        if review_events is not None:
            # Show previous page identities as visual context without letting
            # the model emit events outside the current continuous window.
            refs=[f['frame_id'] for f in context_frames(window,frames,root,review_events,cancelled)]
            ids=set(refs)|current_ids|temporal_ids
            images=[f for f in frames if f['frame_id'] in ids]
            current_ids={f['frame_id'] for f in window}
            local_events=[{'frame_refs':[ref for ref in e['frame_refs'] if ref in current_ids]}
                          for e in review_events if set(e['frame_refs']) & current_ids]
            payload.update(review_mode='full_flow_after_recovery',candidate_events=local_events,
                           image_frames=images,context_frame_ids=[f['frame_id'] for f in images if f['frame_id'] not in current_ids])
        if progress:progress('reviewing' if review_events is not None else 'observing',index,len(windows),events)
        assets=load_assets(images,root,cancelled)
        for attempt in range(2):
            if cancelled():raise ValueError('video_cancelled')
            result=ai.run_ai_skill('ui_video_observe',payload,image_assets=assets,
                timeout=120,retry_count=0,max_tokens=10000,repair_invalid_json=False)
            if cancelled():raise ValueError('video_cancelled')
            if isinstance(result,dict) and isinstance(result.get('events'),list):
                secrets.extend(e['value'] for e in result['events'] if isinstance(e,dict) and e.get('action')=='aiInput' and isinstance(e.get('value'),str) and e['value'])
            try:
                result=_bind_whole_evidence(result,window)
                checked=validate_analysis(result,window,duration,secrets)
                break
            except ValueError as error:
                if attempt:raise
                if getattr(error,'reference_reason',None)=='outside_window':
                    # Re-observe original local pixels; never trim invalid model
                    # references or accept context-only events as evidence.
                    payload.update(image_frames=window,context_frame_ids=[],temporal_context_frame_ids=[])
                    assets=load_assets(window,root,cancelled)
                payload['validation_feedback']=getattr(error,'model_feedback','')+' 本窗口输出未通过格式或证据检查。重新核对当前frames，仅输出action/target/value/frame_refs/observation及可选uncertainties，不输出start_ms/end_ms/evidence_kind或其它字段。只引用当前窗真实帧且按时间排序，完整保留当前窗操作。'
        previous_window_events=list(events)
        for event in checked['events']:
            # Only identical evidence in an overlap is a duplicate. Never erase
            # a repeated business click merely because the target text matches.
            duplicate=next((old for old in reversed(events) if old['action']==event['action'] and
                            old['target']==event['target'] and old['value']==event['value'] and
                            old['frame_refs']==event['frame_refs']),None)
            if duplicate:continue
            # Adjacent windows can describe the same interaction with a
            # nested subset of its evidence. Keep the complete evidence when
            # either endpoint is shared, not a second action at the boundary.
            # Disjoint/non-nested evidence and different controls stay distinct.
            overlap=next((old for old in reversed(previous_window_events)
                if old['action']==event['action'] and event['action'] in {'aiTap','aiScroll'} and
                old['target']==event['target'] and (old['value']==event['value'] or _same_scroll_direction_conflict(old,event)) and
                (old['start_ms']==event['start_ms'] or old['end_ms']==event['end_ms']) and
                (set(old['frame_refs'])<=set(event['frame_refs']) or set(event['frame_refs'])<=set(old['frame_refs']))),None)
            if overlap:
                if _same_scroll_direction_conflict(overlap,event):
                    question='同一滚动方向存在识别分歧，请对照原画面核对内容移动方向；该段仅保留一次操作'
                    event['uncertainties'].append(question);questions.append(question)
                merged_questions=list(dict.fromkeys(overlap['uncertainties']+event['uncertainties']))
                overlap['uncertainties']=merged_questions[:10];questions.extend(merged_questions[10:])
                if set(event['frame_refs'])>set(overlap['frame_refs']):
                    overlap.update(start_ms=event['start_ms'],end_ms=event['end_ms'],frame_refs=event['frame_refs'],observation=event['observation'])
                continue
            old=events[-1] if events else None
            if (old and old['action']==event['action']=='aiInput' and old['target']==event['target'] and
                event['start_ms']<=old['end_ms']):
                times={f['frame_id']:f['timestamp_ms'] for f in frames}
                refs=sorted(set(old['frame_refs']+event['frame_refs']),key=times.get)
                if len(refs)>8:refs=[refs[round(i*(len(refs)-1)/7)] for i in range(8)]
                old.update(start_ms=min(old['start_ms'],event['start_ms']),end_ms=max(old['end_ms'],event['end_ms']),frame_refs=refs,observation=event['observation'])
                continue
            events.append(event)
        questions.extend(checked['uncertainties']);multi|=checked['multiple_scenarios']
        previous_end=window[-1]['timestamp_ms']
    events.sort(key=lambda e:(e['start_ms'],e['end_ms']))
    # Revalidate aggregate budgets, chronology and redaction across all windows.
    result=validate_analysis({'events':events,'uncertainties':list(dict.fromkeys(questions))[:30],
                              'multiple_scenarios':multi},frames,duration,secrets,max_events=120)
    if progress:progress('planning',len(windows),len(windows),result['events'])
    return result,secrets


def planning_frames(events,frames):
    from .ui_video_analysis import _consolidation_images,_system_boundary
    ids={frames[0]['frame_id'],frames[-1]['frame_id']}
    ids.update(f['frame_id'] for f in _consolidation_images(events,frames,6))
    duration=frames[-1]['timestamp_ms']
    # Reserve candidate result pixels for every interaction before spending the
    # budget on repeated loading/failure observations. Last does not mean stable:
    # the model and evidence validator must still establish the visible outcome.
    for i,event in enumerate(events):
        if event['action'] not in {'aiTap','aiInput','aiScroll'} or _system_boundary(event,duration):continue
        boundary=next((e['start_ms'] for e in events[i+1:]
                       if e['action'] in {'aiTap','aiInput','aiScroll'} or _system_boundary(e,duration)),duration+1)
        result=[f for f in frames if event['end_ms']<=f['timestamp_ms']<boundary]
        if not result and boundary==event['end_ms']:
            # Consecutive interactions can share the after/before screenshot.
            # Keep it available, but never borrow a system-cleanup frame.
            system_start=min((e['start_ms'] for e in events[i+1:] if _system_boundary(e,duration)),default=duration+1)
            result=[f for f in frames if f['timestamp_ms']==boundary and f['timestamp_ms']<system_start]
        if result:ids.add(result[-1]['frame_id'])
    if len(ids)>60:raise ValueError('video_vision_limit')
    chosen=_consolidation_images(events,frames,58)
    for frame in chosen:
        if len(ids)<60:ids.add(frame['frame_id'])
    # Fill unused slots across the whole recording, never only its prefix.
    remaining=[f for f in frames if f['frame_id'] not in ids]
    while remaining and len(ids)<60:
        selected=[f['timestamp_ms'] for f in frames if f['frame_id'] in ids]
        item=max(remaining,key=lambda f:min(abs(f['timestamp_ms']-t) for t in selected))
        ids.add(item['frame_id']);remaining.remove(item)
    return [f for f in frames if f['frame_id'] in ids]
