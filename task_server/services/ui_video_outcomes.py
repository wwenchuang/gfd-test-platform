"""One focused visual repair for missing ordinary outcomes; never replan actions."""
import copy


def repair_outcomes(plan,candidates,frames,duration,root,indexes,cancelled,secrets):
    from . import ai_skill_service as ai
    from .ui_video_analysis import _system_boundary,_bind_whole_evidence,validate_analysis,_expected_candidate
    from .ui_video_windows import load_assets
    if cancelled():raise ValueError('video_cancelled')
    if not indexes or len(indexes)>8 or len(set(indexes))!=len(indexes):raise ValueError('video_expected_missing')
    planned={e['candidate_index']:e for e in plan['events']}
    checks=[];allowed={};image_ids=set()
    for index in indexes:
        event=planned[index];candidate=candidates[index]
        # Runtime-object outcomes retain their dedicated selection/reuse review.
        if event.get('selection') or '{{' in event['target'] or event['action'] not in {'aiTap','aiScroll'}:
            raise ValueError('video_expected_missing')
        boundary=next((e['start_ms'] for e in candidates[index+1:] if e['action'] in {'aiTap','aiScroll','aiInput'}),duration)
        system_start=min((e['start_ms'] for e in candidates[index+1:] if _system_boundary(e,duration)),default=duration+1)
        results=[f for f in frames if candidate['end_ms']<=f['timestamp_ms']<=boundary and f['timestamp_ms']<system_start]
        if not results:raise ValueError('video_expected_missing')
        if len(results)>6:results=[results[round(i*(len(results)-1)/5)] for i in range(6)]
        key=f'candidate:{index}'
        allowed[key]={f['frame_id'] for f in results}
        image_ids.update(allowed[key]);image_ids.add(candidate['frame_refs'][0])
        operation=event['target']
        for secret in secrets:
            if secret:operation=operation.replace(secret,'[输入内容未保存]')
        from .ui_video_result_quality import review_requirements
        checks.append({'target':key,'operation':operation,'outcome_frame_ids':[f['frame_id'] for f in results],
                       **review_requirements(event)})
    images=[f for f in frames if f['frame_id'] in image_ids]
    if not 2<=len(images)<=60:raise ValueError('video_vision_limit')
    result=ai.run_ai_skill('ui_video_observe',{'phase':'whole-observe','frames':images,'image_frames':images,
        'duration_ms':duration,'checks':checks},image_assets=load_assets(images,root,cancelled),version='outcome',
        timeout=120,retry_count=0,max_tokens=4000,repair_invalid_json=False)
    if cancelled():raise ValueError('video_cancelled')
    checked=validate_analysis(_bind_whole_evidence(result,images),images,duration,secrets)
    if checked['multiple_scenarios'] or checked['uncertainties']:raise ValueError('video_expected_missing')
    updates={}
    for event in checked['events']:
        key=event['target']
        if (key not in allowed or key in updates or event['action']!='aiAssert' or event['value'] or
            event['uncertainties'] or not set(event['frame_refs'])<=allowed[key]):
            raise ValueError('video_reference_invalid')
        stable=_expected_candidate(event,'')['text']
        if not stable:raise ValueError('video_expected_missing')
        updates[key]=(event,stable)
    if set(updates)!=set(allowed):raise ValueError('video_expected_missing')
    repaired=copy.deepcopy(plan)
    for event in repaired['events']:
        key=f"candidate:{event['candidate_index']}"
        if key in updates:
            observed,stable=updates[key]
            from .ui_video_result_quality import needs_result_review
            if needs_result_review(event,stable):raise ValueError('video_expected_missing')
            event['outcome']={'observation':observed['observation'],'frame_refs':observed['frame_refs']}
            event['reusable_expected']=stable
    return repaired,images
