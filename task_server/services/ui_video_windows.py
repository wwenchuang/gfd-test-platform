"""Bounded full-timeline observation windows; no partial result is publishable."""
import base64


def _retained_dialog_events(previous,current):
    """Return an injective old-to-new mapping without weakening evidence."""
    def compatible(old,new):
        skip={'start_ms','end_ms','frame_refs','uncertainties','observation'}
        return ({k:v for k,v in old.items() if k not in skip}=={k:v for k,v in new.items() if k not in skip} and
                new['start_ms']<=old['start_ms'] and new['end_ms']>=old['end_ms'] and
                set(old['frame_refs'])<=set(new['frame_refs']))
    edges=[[j for j,e in enumerate(current) if compatible(old,e)] for old in previous]
    assigned={}
    def assign(i,seen):
        for j in edges[i]:
            if j in seen:continue
            seen.add(j)
            if j not in assigned or assign(assigned[j],seen):
                assigned[j]=i
                return True
        return False
    for i in range(len(previous)):
        if not assign(i,set()):return None
    return {i:j for j,i in assigned.items()}


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


def observation_windows(frames):
    size=30 if len(frames)>60 else 60
    return [frames[i:i+size] for i in range(0,max(1,len(frames)-4),size-4)]


def observe_windows(frames,duration,root,cancelled,progress=None,review_events=None,prior_secrets=(),observation_timeout_retry_budget=None):
    from . import ai_skill_service as ai
    from . import ui_video_ordinary_timing as timing
    from .ui_video_analysis import _bind_whole_evidence,validate_analysis,_text,_check_question_count
    windows=observation_windows(frames)
    events=[];questions=[];secrets=list(prior_secrets);multi=False;previous_end=-1
    touch_target_timeout_retry_budget={'used':False}
    if observation_timeout_retry_budget is None:observation_timeout_retry_budget={'used':False}
    if (type(observation_timeout_retry_budget) is not dict or set(observation_timeout_retry_budget)!={'used'} or
            type(observation_timeout_retry_budget['used']) is not bool):raise ValueError('video_retry_invalid')
    def preserve_new_questions(old,event):
        from .ui_video_business_inputs import _MANUAL
        questions.extend('局部事件待核对：'+q
                         for q in event['uncertainties'] if q and q!=_MANUAL and q not in old['uncertainties'])
    for index,window in enumerate(windows):
        timing.window('review' if review_events is not None else 'observe',index+1,len(windows))
        with timing.span('window_total'):
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
                refs=[f['frame_id'] for f in timing.measure('context',lambda:context_frames(window,frames,root,review_events,cancelled))]
                ids=set(refs)|current_ids|temporal_ids
                images=[f for f in frames if f['frame_id'] in ids]
                current_ids={f['frame_id'] for f in window}
                local_events=[{'frame_refs':[ref for ref in e['frame_refs'] if ref in current_ids]}
                              for e in review_events if set(e['frame_refs']) & current_ids]
                payload.update(review_mode='full_flow_after_recovery',candidate_events=local_events,
                               image_frames=images,context_frame_ids=[f['frame_id'] for f in images if f['frame_id'] not in current_ids])
            if progress:progress('reviewing' if review_events is not None else 'observing',index,len(windows),events)
            assets=timing.measure('assets',lambda:load_assets(images,root,cancelled))
            from .ui_video_touch_detail import append_details
            payload['detail_frames']=timing.measure('detail',lambda:append_details(images,root,assets,cancelled,current_ids))
            from .ui_video_touch_targets import probe,conflict,bind_uncertain_hints,dialog_target_review,guard_ambiguous_dialog_hints
            from .ui_video_reconcile import _redact_audit
            payload['touch_targets']=probe(images,payload['detail_frames'],assets,duration,cancelled,
                                           touch_target_timeout_retry_budget)
            sticky_questions=[];sticky_multiple=False;dialog_retry=None
            for attempt in range(2):
                if cancelled():raise ValueError('video_cancelled')
                attempt_questions=[];attempt_multiple=False
                dialog_reviewed=False
                payload['touch_targets']=_redact_audit(payload['touch_targets'],secrets)
                if dialog_retry and payload['touch_targets']!=dialog_retry['hints']:
                    raise ValueError('video_model_invalid')
                if 'prior_events' in payload:
                    payload['prior_events']=_redact_audit(payload['prior_events'],secrets)
                if 'validation_feedback' in payload:
                    payload['validation_feedback']=_redact_audit(payload['validation_feedback'],secrets)
                try:
                    while True:
                        if cancelled():raise ValueError('video_cancelled')
                        try:
                            result=timing.measure('observe_model',lambda:ai.run_ai_skill('ui_video_observe',payload,image_assets=assets,
                                timeout=120,retry_count=0,max_tokens=10000,repair_invalid_json=False,
                                **({'allow_missing_video_questions':True} if attempt else {})),attempt=attempt+1)
                            break
                        except ai.VideoObservationProviderTimeoutError:
                            if cancelled():raise ValueError('video_cancelled') from None
                            if observation_timeout_retry_budget['used']:raise
                            observation_timeout_retry_budget['used']=True
                    if cancelled():raise ValueError('video_cancelled')
                    if isinstance(result,dict) and isinstance(result.get('events'),list):
                        secrets.extend(e['value'] for e in result['events'] if isinstance(e,dict) and e.get('action')=='aiInput' and isinstance(e.get('value'),str) and e['value'])
                        for event in result['events']:
                            if isinstance(event,dict) and isinstance(event.get('uncertainties'),list):
                                _check_question_count(event['uncertainties'],10)
                                attempt_questions.extend('初次观察待核对：'+_text(q) for q in event['uncertainties'] if q)
                    if isinstance(result,dict) and isinstance(result.get('uncertainties'),list):
                        _check_question_count(result['uncertainties'],30)
                        attempt_questions.extend(_text(q) for q in result['uncertainties'])
                    if isinstance(result,dict) and type(result.get('multiple_scenarios')) is bool:
                        attempt_multiple|=result['multiple_scenarios']
                    result=_bind_whole_evidence(result,window)
                    checked=validate_analysis(result,window,duration,secrets)
                    all_questions=list(dict.fromkeys(_redact_audit(checked['uncertainties']+sticky_questions,secrets)))
                    _check_question_count(all_questions,30)
                    checked['uncertainties']=all_questions
                    checked['multiple_scenarios']|=sticky_multiple
                    if dialog_retry:
                        previous=_redact_audit(dialog_retry['events'],secrets)
                        mapping=_retained_dialog_events(previous,checked['events'])
                        if mapping is None:raise ValueError('video_required_action_missing')
                        matched=set(mapping.values())
                        for i,j in mapping.items():
                            old,new=previous[i],checked['events'][j]
                            for q in old['uncertainties']:
                                if q not in new['uncertainties']:
                                    if len(new['uncertainties'])>=10:raise ValueError('video_model_invalid')
                                    new['uncertainties'].append(q)
                            if old['observation']!=new['observation']:
                                # Preserve the first observation. A changed second
                                # description is unverified, not an automatic
                                # replacement or a reason to erase either action.
                                new['observation']=old['observation']
                                q='原帧 '+old['frame_refs'][0]+' 的两次画面描述不同，请对照原视频核对实际情况。'
                                if len(new['uncertainties'])>=10 or len(checked['uncertainties'])>=30:raise ValueError('video_model_invalid')
                                new['uncertainties'].append(q);checked['uncertainties'].append(q)
                        for j,event in enumerate(checked['events']):
                            if j not in matched and (event['action']!='aiTap' or dialog_retry['frame'] not in event['frame_refs']):
                                raise ValueError('video_required_action_missing')
                    target_diagnostic={};target_evidence={}
                    guard_ambiguous_dialog_hints(checked['events'],payload['touch_targets'],images)
                    if dialog_retry:
                        candidates=[e for j,e in enumerate(checked['events']) if j not in matched and e['action']=='aiTap' and dialog_retry['frame'] in e['frame_refs']]
                        if len(candidates)!=1 or len(candidates[0]['uncertainties'])>=10 or len(checked['uncertainties'])>=30:
                            raise ValueError('video_model_invalid')
                        q='局部重观在原帧 '+dialog_retry['frame']+' 补充了触点归因，请对照原视频核对是否存在独立操作及具体对象。'
                        candidates[0]['uncertainties'].append(q);checked['uncertainties'].append(q)
                    target_conflict=conflict(checked['events'],payload['touch_targets'],images,target_diagnostic,evidence=target_evidence)
                    if target_conflict:
                        candidate=dialog_target_review(checked['events'],payload['touch_targets'],images,target_diagnostic) if not attempt or dialog_retry else None
                        if candidate:
                            dialog_reviewed=True
                            event,frame,observed,probe_target=candidate
                            question=_text(_redact_audit(f'触点 {frame} 的目标有两种描述：主观察“{observed}”，局部复核“{probe_target}”；请对照原视频核对是否实际点击、是否同一次操作及具体控件。',secrets))
                            if question not in event['uncertainties']:
                                if len(event['uncertainties'])>=10:raise ValueError('video_model_invalid')
                                event['uncertainties'].append(question)
                            if question not in checked['uncertainties']:
                                if len(checked['uncertainties'])>=30:raise ValueError('video_model_invalid')
                                checked['uncertainties'].append(question)
                            target_conflict=''
                    if target_conflict:
                        error=ValueError('video_model_invalid')
                        error.model_feedback=target_conflict
                        error.target_diagnostic=target_diagnostic
                        # Private source evidence only; never included in service logs.
                        for key in ('observed_target','probe_target'):
                            for secret in secrets:
                                if secret:target_evidence[key]=target_evidence[key].replace(secret,'[输入内容未保存]')
                        error.target_failure_evidence=target_evidence
                        raise error
                    try:
                        hint_questions=bind_uncertain_hints(checked['events'],payload['touch_targets'],checked)
                    except ValueError as error:
                        if type(getattr(error,'touch_binding_evidence',None)) is dict:
                            error.touch_binding_evidence.update(phase='review' if review_events is not None else 'observe',
                                window_index=index+1,window_count=len(windows),attempt=attempt+1)
                        raise
                    if not isinstance(hint_questions,list):raise ValueError('video_model_invalid')
                    from .ui_video_analysis import _text
                    for hint_question in hint_questions:
                        hint_question=_text(hint_question)
                        if hint_question and hint_question not in checked['uncertainties']:
                            if len(checked['uncertainties'])>=30:raise ValueError('video_model_invalid')
                            checked['uncertainties'].append(hint_question)
                    break
                except ValueError as error:
                    guard=getattr(error,'dialog_guard_evidence',{})
                    anticipated=list(dict.fromkeys(_redact_audit(checked['uncertainties']+attempt_questions,secrets))) if getattr(error,'dialog_review_blocked',False) else []
                    if (not cancelled() and not attempt and getattr(error,'dialog_review_blocked',False) and
                        guard.get('reason')=='other_probe_match_count' and guard.get('match_count')==0 and
                        guard.get('probe_frame') in current_ids and len(anticipated)<=28 and
                        all(len(e['uncertainties'])<10 for e in checked['events']) and
                        _redact_audit(payload['touch_targets'],secrets)==payload['touch_targets']):
                        import copy
                        dialog_retry={'events':copy.deepcopy(checked['events']),'hints':copy.deepcopy(payload['touch_targets']),'frame':guard['probe_frame']}
                        sticky_questions.extend(attempt_questions);sticky_multiple|=attempt_multiple
                        payload['validation_feedback']='上次局部探针的原帧尚未被主观察明确解释。重新观察当前原帧，完整保留首答全部操作、结果、证据和疑问；仅原图确证时补充相关操作和帧。不能为满足校验编造、删除或合并操作；不确定时保留疑问。'
                        continue
                    if cancelled() or attempt or dialog_reviewed or getattr(error,'dialog_review_blocked',False) or getattr(error,'target_diagnostic',{}).get('ambiguous_bare_probe'):raise
                    sticky_questions.extend(attempt_questions)
                    sticky_multiple|=attempt_multiple
                    if getattr(error,'reference_reason',None)=='outside_window':
                        # Re-observe original local pixels; never trim invalid model
                        # references or accept context-only events as evidence.
                        payload.update(image_frames=window,context_frame_ids=[],temporal_context_frame_ids=[])
                        assets=timing.measure('assets',lambda:load_assets(window,root,cancelled),attempt=attempt+1)
                        payload['detail_frames']=timing.measure('detail',lambda:append_details(window,root,assets,cancelled),attempt=attempt+1)
                    payload['validation_feedback']=(getattr(error,'model_feedback','')+
                        ' 本窗口输出未通过格式或证据检查。重新核对当前frames。'
                        '根对象必须同时包含events数组、uncertainties数组和multiple_scenarios布尔值；'
                        'multiple_scenarios必须根据画面判断，不得照抄示例值。'
                        'events数组中的每个事件仅包含action、target、value、frame_refs、observation及可选uncertainties；'
                        '不得把根字段当作事件字段，也不得遗漏根字段。'
                        '事件不输出start_ms/end_ms/evidence_kind或其它字段。'
                        'frame_refs只引用当前窗frames中的真实帧，按时间排序，完整保留当前窗操作；'
                        'context_frame_ids仅供理解上下文，不能作为事件证据。')
            previous_window_events=list(events)
            for event in checked['events']:
                # Only identical evidence in an overlap is a duplicate. Never erase
                # a repeated business click merely because the target text matches.
                duplicate=next((old for old in reversed(events) if old['action']==event['action'] and
                                old['target']==event['target'] and old['value']==event['value'] and
                                old['frame_refs']==event['frame_refs']),None)
                if duplicate:
                    preserve_new_questions(duplicate,event)
                    continue
                # Adjacent windows can describe the same interaction with a
                # nested subset of its evidence. Keep the complete evidence when
                # either endpoint is shared, not a second action at the boundary.
                # A shared start plus two shared anchors also identifies the same
                # tap, provided no other interaction intervenes. Keep its union.
                # Disjoint evidence and different controls stay distinct.
                overlap=next((old for old in reversed(previous_window_events)
                    if old['action']==event['action'] and event['action'] in {'aiTap','aiScroll'} and
                    old['target']==event['target'] and (old['value']==event['value'] or _same_scroll_direction_conflict(old,event)) and
                    (old['start_ms']==event['start_ms'] or old['end_ms']==event['end_ms']) and
                    (set(old['frame_refs'])<=set(event['frame_refs']) or set(event['frame_refs'])<=set(old['frame_refs']) or
                     (old['action']=='aiTap' and old['start_ms']==event['start_ms'] and
                      len(set(old['frame_refs']) & set(event['frame_refs']))>=2 and
                      len(set(old['frame_refs']) | set(event['frame_refs']))<=8 and
                      not any(other['action'] in {'aiTap','aiInput','aiScroll','dismissKeyboard','navigateBack'} and
                              old['start_ms']<other['start_ms']<=max(old['end_ms'],event['end_ms'])
                              for other in events)))),None)
                if overlap:
                    if _same_scroll_direction_conflict(overlap,event):
                        question='同一滚动方向存在识别分歧，请对照原画面核对内容移动方向；该段仅保留一次操作'
                        event['uncertainties'].append(question);questions.append(question)
                    merged_questions=list(dict.fromkeys(overlap['uncertainties']+event['uncertainties']))
                    overlap['uncertainties']=merged_questions[:10];questions.extend(merged_questions[10:])
                    times={f['frame_id']:f['timestamp_ms'] for f in frames}
                    refs=sorted(set(overlap['frame_refs']) | set(event['frame_refs']),key=times.get)
                    if refs!=overlap['frame_refs']:
                        overlap.update(start_ms=times[refs[0]],end_ms=times[refs[-1]],frame_refs=refs,observation=event['observation'])
                    continue
                old=events[-1] if events else None
                if (old and old['action']==event['action']=='aiInput' and old['target']==event['target'] and
                    event['start_ms']<=old['end_ms']):
                    preserve_new_questions(old,event)
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
    result=validate_analysis({'events':events,'uncertainties':list(dict.fromkeys(questions)),
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
        if event['action'] not in {'aiTap','aiInput','aiScroll','dismissKeyboard','navigateBack'} or _system_boundary(event,duration):continue
        boundary=next((e['start_ms'] for e in events[i+1:]
                       if e['action'] in {'aiTap','aiInput','aiScroll','dismissKeyboard','navigateBack'} or _system_boundary(e,duration)),duration+1)
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
