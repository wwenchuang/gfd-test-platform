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


def observe_windows(frames,duration,root,cancelled):
    from . import ai_skill_service as ai
    from .ui_video_analysis import _bind_whole_evidence,validate_analysis
    windows=[frames[i:i+60] for i in range(0,max(1,len(frames)-4),56)]
    events=[];questions=[];secrets=[];multi=False;previous_end=-1
    for index,window in enumerate(windows):
        if cancelled():raise ValueError('video_cancelled')
        payload={'phase':'whole-observe','frames':window,'image_frames':window,'duration_ms':duration}
        if len(windows)>1:
            payload['window']={'index':index+1,'total':len(windows),'previous_end_ms':previous_end,
                               'final':index==len(windows)-1}
            payload['prior_events']=events[-8:]
        result=ai.run_ai_skill('ui_video_observe',payload,image_assets=load_assets(window,root,cancelled),
            timeout=120,retry_count=0,max_tokens=10000,repair_invalid_json=False)
        if cancelled():raise ValueError('video_cancelled')
        result=_bind_whole_evidence(result,window)
        secrets.extend(e['value'] for e in result['events'] if e['action']=='aiInput' and isinstance(e.get('value'),str) and e['value'])
        checked=validate_analysis(result,window,duration,secrets)
        for event in checked['events']:
            # Only identical evidence in an overlap is a duplicate. Never erase
            # a repeated business click merely because the target text matches.
            duplicate=next((old for old in reversed(events) if old['action']==event['action'] and
                            old['frame_refs']==event['frame_refs']),None)
            if duplicate:continue
            events.append(event)
        questions.extend(checked['uncertainties']);multi|=checked['multiple_scenarios']
        previous_end=window[-1]['timestamp_ms']
    events.sort(key=lambda e:(e['start_ms'],e['end_ms']))
    # Revalidate aggregate budgets, chronology and redaction across all windows.
    result=validate_analysis({'events':events,'uncertainties':list(dict.fromkeys(questions))[:30],
                              'multiple_scenarios':multi},frames,duration,secrets)
    return result,secrets


def planning_frames(events,frames):
    from .ui_video_analysis import _consolidation_images
    chosen=_consolidation_images(events,frames,58)
    ids={f['frame_id'] for f in chosen}|{frames[0]['frame_id'],frames[-1]['frame_id']}
    # Fill unused slots across the whole recording, never only its prefix.
    remaining=[f for f in frames if f['frame_id'] not in ids]
    while remaining and len(ids)<60:
        selected=[f['timestamp_ms'] for f in frames if f['frame_id'] in ids]
        item=max(remaining,key=lambda f:min(abs(f['timestamp_ms']-t) for t in selected))
        ids.add(item['frame_id']);remaining.remove(item)
    return [f for f in frames if f['frame_id'] in ids]
