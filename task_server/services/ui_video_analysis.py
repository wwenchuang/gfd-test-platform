"""Bound visual proposals to actual chronological assets, never model authority."""
import base64
import copy
import hashlib
import json
import math
import re
import uuid

from .ui_case_draft_contract import ACTIONS, normalize_steps


def _text(value, limit=2000):
    if not isinstance(value, str) or len(value)>limit: raise ValueError('video_model_invalid')
    return value.strip()


def _scroll_object(value):
    """Normalize only a bounded JSON-object encoding, preserving strict semantics."""
    if isinstance(value,str):
        if not value or len(value)>4000:raise ValueError('video_action_invalid')
        def unique_object(pairs):
            result={}
            for key,item in pairs:
                if key in result:raise ValueError('video_action_invalid')
                result[key]=item
            return result
        def reject_constant(_):raise ValueError('video_action_invalid')
        try:
            value=json.loads(value,object_pairs_hook=unique_object,parse_constant=reject_constant)
        except (ValueError,RecursionError):
            raise ValueError('video_action_invalid') from None
    if not isinstance(value,dict):raise ValueError('video_action_invalid')
    return value


def _align_batch_evidence_times(result, frames, duration):
    """Expand valid batch intervals only to include their actual source frames.

    The model describes actions; the platform owns frame timestamps. Unknown
    frames, malformed intervals and event order still go to strict validation.
    Consolidated intervals are never adjusted here.
    """
    if not isinstance(result,dict) or not isinstance(result.get('events'),list):return result
    last=0
    for event in result['events']:
        if not isinstance(event,dict):return result
        start,end=event.get('start_ms'),event.get('end_ms')
        if type(start) is not int or type(end) is not int or not last<=start<=end<=duration:return result
        last=start
    aligned=copy.deepcopy(result)
    times={frame['frame_id']:frame['timestamp_ms'] for frame in frames}
    for event in aligned['events']:
        if not isinstance(event,dict):continue
        start,end,refs=event.get('start_ms'),event.get('end_ms'),event.get('frame_refs')
        if (type(start) is not int or type(end) is not int or not 0<=start<=end<=duration or
            not isinstance(refs,list) or not 1<=len(refs)<=8 or
            any(not isinstance(ref,str) or ref not in times for ref in refs)):continue
        stamps=[times[ref] for ref in refs]
        if stamps!=sorted(stamps):continue
        event['start_ms']=min(start,stamps[0])
        event['end_ms']=max(end,stamps[-1])
    return aligned


def validate_analysis(result, frames, duration, prior_secrets=()):
    required_fields={'events','uncertainties','multiple_scenarios'}
    if not isinstance(result, dict) or not required_fields<=set(result)<=required_fields|{'title'}: raise ValueError('video_model_invalid')
    title=_text(result['title'],80) if 'title' in result else ''
    if type(result['multiple_scenarios']) is not bool: raise ValueError('video_model_invalid')
    events=result['events']; questions=result['uncertainties']
    if not isinstance(events,list) or len(events)>50 or not isinstance(questions,list) or len(questions)>30: raise ValueError('video_model_invalid')
    questions=[_text(q) for q in questions]
    by_id={f['frame_id']:f['timestamp_ms'] for f in frames}
    last=-1
    clean=[]
    for raw in events:
        required={'action','target','value','start_ms','end_ms','frame_refs','observation','evidence_kind','uncertainties'}
        if not isinstance(raw,dict) or not required<=set(raw)<=required|{'outcome','selection','reusable_expected'}: raise ValueError('video_model_invalid')
        event=copy.deepcopy(raw)
        if event['action'] not in ACTIONS-{'launch','selectRecord'} or event['evidence_kind'] not in {'observed','inferred'}: raise ValueError('video_action_invalid')
        start,end=event['start_ms'],event['end_ms'];refs=event['frame_refs']
        if type(start) is not int or type(end) is not int or not last<=start<=end<=duration: raise ValueError('video_reference_invalid')
        # Separate structural, unknown-frame and time-range failures so the safe
        # traceback identifies the cause without logging model text or media.
        if not isinstance(refs,list) or not 1<=len(refs)<=8:
            raise ValueError('video_reference_invalid')
        if any(not isinstance(r,str) or r not in by_id for r in refs):
            raise ValueError('video_reference_invalid')
        if any(not start<=by_id[r]<=end for r in refs):
            raise ValueError('video_reference_invalid')
        if [by_id[r] for r in refs]!=sorted(by_id[r] for r in refs): raise ValueError('video_reference_invalid')
        event['target']=_text(event['target']);event['observation']=_text(event['observation'])
        if not event['observation']: raise ValueError('video_observation_missing')
        if 'selection' in event:
            event['selection']=_text(event['selection'])
            if not event['selection'] or event['action']!='aiTap' or '{{本次对象}}' not in event['target']:
                raise ValueError('video_selection_invalid')
        if 'reusable_expected' in event:
            event['reusable_expected']=_text(event['reusable_expected'])
            if not event['reusable_expected']:
                raise ValueError('video_selection_invalid')
        if 'outcome' in event:
            outcome=event['outcome']
            if not isinstance(outcome,dict) or set(outcome)!={'observation','frame_refs'}:
                raise ValueError('video_model_invalid')
            outcome['observation']=_text(outcome['observation'])
            outcome_refs=outcome['frame_refs']
            if (not outcome['observation'] or not isinstance(outcome_refs,list) or not 1<=len(outcome_refs)<=8 or
                any(not isinstance(ref,str) or ref not in by_id or not end<=by_id[ref]<=duration for ref in outcome_refs) or
                [by_id[ref] for ref in outcome_refs]!=sorted(by_id[ref] for ref in outcome_refs)):
                raise ValueError('video_reference_invalid')
        if not isinstance(event['uncertainties'],list) or len(event['uncertainties'])>10: raise ValueError('video_model_invalid')
        event['uncertainties']=[_text(q) for q in event['uncertainties']]
        if event['action']=='aiInput':
            _text(event['value'],4000)
            # Video transcription cannot establish whether text is a credential.
            # Require explicit user configuration for all input values, avoiding
            # unknown tokens, passwords and OTPs ever entering saved proposals.
            event['value']=''
            event['uncertainties'].append('请手工填写非敏感测试输入；密码、验证码或令牌请使用人工前置条件')
        elif event['action']=='aiScroll':
            from .yaml_static_validator import validate_midscene_action_parameters
            value=_scroll_object(event['value'])
            event['value']=value
            if not isinstance(value,dict) or set(value)-{'direction','distance','scrollType'}:raise ValueError('video_action_invalid')
            distance=value.get('distance')
            if isinstance(distance,(int,float)) and not math.isfinite(distance):raise ValueError('video_action_invalid')
            if validate_midscene_action_parameters('aiScroll',{'aiScroll':event['target'],**value}):raise ValueError('video_action_invalid')
        elif event['value']!='': raise ValueError('video_action_invalid')
        last=start
        clean.append(event)
    secrets=list(prior_secrets)+[e['value'] for e in events if isinstance(e,dict) and e.get('action')=='aiInput' and isinstance(e.get('value'),str) and e['value']]
    def redact(value):
        if isinstance(value,str):
            for secret in secrets:value=value.replace(secret,'[输入内容未保存]')
            return value
        if isinstance(value,list):return [redact(v) for v in value]
        if isinstance(value,dict):return {k:redact(v) for k,v in value.items()}
        return value
    return redact({'events':clean,'uncertainties':questions,'multiple_scenarios':result['multiple_scenarios'],**({'title':title} if title else {})})


def _system_boundary(event,duration):
    """Only explicit system-shell evidence at recording boundaries is removable."""
    text=event['target']+' '+event['observation']
    return (event['action'] in {'aiTap','aiWaitFor','aiAssert'} and
            bool(re.search(r'系统桌面|桌面应用图标|系统最近任务|最近任务卡片|系统多任务|停止录屏|系统通知栏',text)) and
            (event['end_ms']<=min(5000,duration*0.15) or
             event['start_ms']>=max(duration*0.85,duration-5000)))


def _required_interactions(events,duration):
    return [index for index,event in enumerate(events) if event['action'] in {'aiTap','aiInput','aiScroll'}
            and not _system_boundary(event,duration)]


def _preserve_interactions(candidates,events,required):
    # One output cannot stand in for two different interactions. Bipartite time
    # matching also rejects equal-count replacements concentrated in one period.
    owners={}
    def assign(index,seen):
        original=candidates[index]
        for position,event in enumerate(events):
            if (position in seen or original['action']!=event['action'] or
                original['start_ms']>event['end_ms'] or event['start_ms']>original['end_ms']):continue
            seen.add(position)
            if position not in owners or assign(owners[position],seen):
                owners[position]=index
                return True
        return False
    if any(not assign(index,set()) for index in required):
        raise ValueError('video_required_action_missing')


def _consolidation_images(events,frames,limit):
    """Prioritize ambiguous/destructive evidence; otherwise cover event chronology."""
    counts={}
    for event in events:
        key=(event['action'],event['target'])
        counts[key]=counts.get(key,0)+1
    priority=set();secondary=set()
    for index,event in enumerate(events):
        destructive=re.search(r'删除|移除|清空|支付|付款|delete|remove|pay',event['target'],re.I)
        ambiguous=counts[(event['action'],event['target'])]>1 or re.search(r'打印|下单',event['target'])
        if destructive or ambiguous:
            refs=priority if destructive else secondary
            refs.update(event['frame_refs'])
            for following in events[index+1:index+(3 if destructive else 2)]:
                refs.update(following['frame_refs'])
    all_refs={ref for event in events for ref in event['frame_refs']}
    def spread(items,count):
        if len(items)<=count:return items
        return [items[round(i*(len(items)-1)/(count-1))] for i in range(count)] if count>1 else [items[len(items)//2]]
    duration=frames[-1]['timestamp_ms'] if frames else 0
    reserved=[]
    navigations=[event for event in events if event['action'] in {'aiTap','aiInput'} and
                 not _system_boundary(event,duration) and not set(event['frame_refs'])&priority]
    if navigations:reserved.append(navigations[0]['frame_refs'][-1])
    scrolls=[event for event in events if event['action']=='aiScroll']
    if scrolls:reserved.extend((scrolls[0]['frame_refs'][0],scrolls[0]['frame_refs'][-1]))
    # With six slots reserve up to three for the prerequisite chain; retain at
    # least half for consequential before/confirmation/result evidence.
    reserved=list(dict.fromkeys(reserved))[:max(0,limit-3)] if priority else list(dict.fromkeys(reserved))[:limit]
    chosen=[f for f in frames if f['frame_id'] in reserved]
    # Preserve an actual before/confirmation/result triplet. Spreading the
    # union can pick the later return page as the only result image.
    times={f['frame_id']:f['timestamp_ms'] for f in frames}
    for index,event in enumerate(events):
        if event['action']!='aiTap' or not re.search(r'删除|移除|清空|支付|付款|delete|remove|pay',event['target'],re.I):continue
        confirmation=next((i for i in range(index+1,len(events)) if events[i]['action'] in {'aiTap','aiInput','aiScroll'}),None)
        if confirmation is None or not re.search(r'确认|确定|提交|confirm|submit',events[confirmation]['target'],re.I):continue
        following=next((events[i]['start_ms'] for i in range(confirmation+1,len(events)) if events[i]['action'] in {'aiTap','aiInput','aiScroll'}),duration+1)
        # A stable result can be a decoded frame with no local event. The
        # model must see it before deciding what happened; this selects evidence,
        # not a successful outcome. Never include the next interaction's frame.
        result_refs={frame['frame_id'] for frame in frames
                     if events[confirmation]['end_ms']<=frame['timestamp_ms']<following}
        # If no later business interaction bounds the interval, the decoded
        # tail may already be the desktop. Prefer locally associated result
        # evidence when available, instead of replacing it with a system tail.
        if following==duration+1:
            associated={ref for item in events[confirmation+1:] for ref in item['frame_refs'] if ref in result_refs}
            associated.update(ref for item in events[confirmation:] for ref in item.get('outcome',{}).get('frame_refs',[]) if ref in result_refs)
            if associated:result_refs=associated
        if not result_refs:continue
        anchors=[event['frame_refs'][0],events[confirmation]['frame_refs'][0],max(result_refs,key=times.get)]
        selected={f['frame_id'] for f in chosen}
        for ref in anchors:
            if len(chosen)<limit and ref not in selected:
                chosen.append(next(f for f in frames if f['frame_id']==ref));selected.add(ref)
        break
    selected={f['frame_id'] for f in chosen}
    if len(chosen)<limit:
        chosen+=spread([f for f in frames if f['frame_id'] in priority-selected],limit-len(chosen))
    selected={f['frame_id'] for f in chosen}
    if len(chosen)<limit:
        chosen+=spread([f for f in frames if f['frame_id'] in secondary-selected],limit-len(chosen))
        selected={f['frame_id'] for f in chosen}
    remaining=[f for f in frames if f['frame_id'] in all_refs-selected]
    chosen+=spread(remaining,limit-len(chosen)) if len(chosen)<limit else []
    return sorted(chosen,key=lambda frame:frame['timestamp_ms'])


def _bind_candidate_evidence(result, candidates, required_indices=()):
    """Bind indexed plans to validated evidence; legacy plans remain strict."""
    if not isinstance(result,dict) or not isinstance(result.get('events'),list):return result
    events=result['events']
    indexed=[isinstance(event,dict) and 'candidate_index' in event for event in events]
    if not any(indexed):return result
    if not all(indexed):raise ValueError('video_model_timeline')
    bound=copy.deepcopy(result);last=-1;seen=set()
    owned=('start_ms','end_ms','frame_refs','evidence_kind')
    for event in bound['events']:
        index=event.pop('candidate_index')
        if type(index) is not int or not last<index<len(candidates):
            raise ValueError('video_model_timeline')
        candidate=candidates[index]
        if any(key in event for key in owned) or event.get('action')!=candidate['action']:
            raise ValueError('video_model_timeline')
        for key in owned:event[key]=copy.deepcopy(candidate[key])
        last=index;seen.add(index)
    if not set(required_indices)<=seen:raise ValueError('video_required_action_missing')
    return bound


def _validate_consolidated_evidence(checked,raw_events,candidates,frames,image_frames,duration,required_indices):
    all_refs={ref for raw in raw_events for ref in raw['frame_refs']}
    all_refs.update(frame['frame_id'] for frame in image_frames)
    times={frame['frame_id']:frame['timestamp_ms'] for frame in frames}
    for event_index,event in enumerate(checked['events']):
        matching=[raw for raw in raw_events if raw['action']==event['action'] and
                  raw['start_ms']<=event['end_ms'] and event['start_ms']<=raw['end_ms']]
        covered=event['start_ms']
        for raw in sorted(matching,key=lambda item:item['start_ms']):
            if raw['start_ms']>covered:break
            covered=max(covered,raw['end_ms'])
            if covered>=event['end_ms']:break
        supported_refs={ref for raw in matching for ref in raw['frame_refs']}
        supported_refs.update(frame['frame_id'] for frame in image_frames)
        if (not matching or min(raw['start_ms'] for raw in matching)>event['start_ms'] or
            covered<event['end_ms'] or not set(event['frame_refs'])<=supported_refs):
            raise ValueError('video_model_timeline')
        if 'outcome' in event:
            next_start=min((raw['start_ms'] for raw in candidates if raw['action'] in {'aiTap','aiInput','aiScroll'}
                            and raw['start_ms']>event['start_ms']),default=duration)
            next_output=next((item['start_ms'] for item in checked['events'][event_index+1:]
                              if item['action'] in {'aiTap','aiInput','aiScroll'}),duration)
            next_start=min(next_start,next_output)
            if (not set(event['outcome']['frame_refs'])<=all_refs or
                any(times[ref]>next_start for ref in event['outcome']['frame_refs'])):
                raise ValueError('video_reference_invalid')
        if all(raw['evidence_kind']=='inferred' for raw in matching):event['evidence_kind']='inferred'
    _preserve_interactions(candidates,checked['events'],required_indices)


def _bind_whole_evidence(result, frames):
    """Bind only actual sent frame IDs; model times or confidence cannot override them."""
    if not isinstance(result, dict) or not isinstance(result.get('events'), list):
        raise ValueError('video_model_invalid')
    bound=copy.deepcopy(result)
    times={frame['frame_id']:frame['timestamp_ms'] for frame in frames}
    last=-1
    for event in bound['events']:
        required={'action','target','value','frame_refs','observation'}
        if not isinstance(event,dict) or not required<=set(event)<=required|{'uncertainties'}:
            raise ValueError('video_model_invalid')
        refs=event['frame_refs']
        if (not isinstance(refs,list) or not 1<=len(refs)<=8 or
            any(not isinstance(ref,str) or ref not in times for ref in refs) or len(set(refs))!=len(refs)):
            raise ValueError('video_reference_invalid')
        stamps=[times[ref] for ref in refs]
        if stamps!=sorted(stamps) or stamps[0]<last:raise ValueError('video_reference_invalid')
        if 'uncertainties' in event and not isinstance(event['uncertainties'],list):raise ValueError('video_model_invalid')
        event.setdefault('uncertainties',[])
        event.update(start_ms=stamps[0],end_ms=stamps[-1],evidence_kind='inferred')
        last=stamps[0]
    return bound


def analyze_whole_frames(document, root, cancelled):
    """One full-context observation, one bounded plan, at most one correction."""
    from . import ai_skill_service as ai
    frames=document['media']['frames'];duration=document['media']['duration_ms']
    if cancelled():raise ValueError('video_cancelled')
    if not 2<=len(frames)<=60:raise ValueError('video_vision_limit')
    assets=[];total=0
    for frame in frames:
        if cancelled():raise ValueError('video_cancelled')
        path=root/'frames'/(frame['frame_id']+'.jpg')
        if path.stat().st_size>8*1024*1024:raise ValueError('video_vision_limit')
        data=base64.b64encode(path.read_bytes()).decode()
        total+=len(data)
        if total>10*1024*1024:raise ValueError('video_vision_limit')
        assets.append({'name':frame['frame_id'],'timestamp_ms':frame['timestamp_ms'],'mime':'image/jpeg','base64':data})
    payload={'phase':'whole-observe','frames':frames,'image_frames':frames,'duration_ms':duration}
    # No user intent or baseline style in factual observation. Planning receives both.
    result=ai.run_ai_skill('ui_video_observe',payload,image_assets=assets,timeout=120,
        retry_count=0,max_tokens=10000,repair_invalid_json=False)
    if cancelled():raise ValueError('video_cancelled')
    result=_bind_whole_evidence(result,frames)
    known_secrets=[e['value'] for e in result['events'] if e['action']=='aiInput' and isinstance(e.get('value'),str) and e['value']]
    observed=validate_analysis(result,frames,duration)
    candidates=observed['events'];multi=observed['multiple_scenarios']
    if not candidates:return {**observed,'raw_events':[],'raw_uncertainties':observed['uncertainties']}
    required_indices=_required_interactions(candidates,duration)
    payload={'phase':'whole-consolidate','app_id':document['app_id'],'goal':document['goal'],
        'frames':frames,'image_frames':frames,'duration_ms':duration,'candidate_events':candidates,
        'required_candidate_indexes':required_indices,'raw_uncertainties':observed['uncertainties'],
        'multiple_scenarios':multi,'baseline_examples':document.get('baseline_examples',[])}
    for attempt in range(2):
        if cancelled():raise ValueError('video_cancelled')
        planned=ai.run_ai_skill('ui_video_steps',payload,image_assets=assets,version='consolidate',
            timeout=120,retry_count=0,max_tokens=10000,repair_invalid_json=False)
        if cancelled():raise ValueError('video_cancelled')
        if isinstance(planned,dict) and type(planned.get('multiple_scenarios')) is bool:multi|=planned['multiple_scenarios']
        try:
            bound=_bind_candidate_evidence(planned,candidates,required_indices)
            checked=validate_analysis(bound,frames,duration,known_secrets)
            _validate_consolidated_evidence(checked,candidates,candidates,frames,frames,duration,required_indices)
            proposal,_=draft_proposal({**document,**checked,'source_id':document.get('source_id','uvs_'+'0'*32),
                                      'analysis_version':document.get('analysis_version',1)})
            if any(step['action'] in {'aiTap','aiScroll'} and not step['expected'] for step in proposal['steps']):
                raise ValueError('video_expected_missing')
            return {**checked,'multiple_scenarios':multi,'raw_events':candidates,'raw_uncertainties':observed['uncertainties']}
        except ValueError:
            if attempt:raise
            # Never send model output/error strings back: only a fixed safe correction.
            if isinstance(planned,dict) and isinstance(planned.get('events'),list):
                known_secrets.extend(e['value'] for e in planned['events'] if isinstance(e,dict) and
                    e.get('action')=='aiInput' and isinstance(e.get('value'),str) and e['value'])
            payload={**payload,'multiple_scenarios':multi,'validation_feedback':
                '上一结果未通过平台证据/复用校验。每事件必须有candidate_index/action/target/value/observation/uncertainties。'
                '索引严格递增，保留全部required_candidate_indexes及原action，不输出时间或frame_refs/evidence_kind。'
                'outcome只用不早于该候选end_ms且不晚于下一交互start_ms的真实结果帧。'
                'selection仅在首次对象操作上出现一次，不含任何旧编号/日期/{{}}。后续操作引用{{本次对象}}；'
                '确认后的reusable_expected检查{{本次对象}}消失和{{保留对象}}仍显示。不等待toast。其他导航只检查相关稳定页面。'
                '每次点击和滚动都要有可见的稳定结果；缺少时重查对应结果帧，补outcome及简短reusable_expected，'
                '不要留空让用户手写，也不要编造证据中未出现的结果。'}


def analyze_frames(document, root, cancelled):
    from . import ai_skill_service as ai
    frames=document['media']['frames']
    # The existing Gateway imagePartsFromBody accepts at most six images.
    limit=max(2,min(6,int(ai.AI_VISION_IMAGE_LIMIT)))
    if int(ai.AI_VISION_IMAGE_LIMIT)<2: raise ValueError('video_vision_limit')
    events=[];questions=[];multi=False;last_end=-1;audit_events=[];known_secrets=[];repair_used=False;title=''
    # Adjacent batches share one boundary frame, preserving the whole chronology.
    for offset in range(0,len(frames)-1,limit-1):
        if cancelled(): raise ValueError('video_cancelled')
        batch=frames[offset:offset+limit]
        assets=[{'name':f['frame_id'], 'mime':'image/jpeg','base64':base64.b64encode((root/'frames'/(f['frame_id']+'.jpg')).read_bytes()).decode()} for f in batch]
        if cancelled():raise ValueError('video_cancelled')
        payload={'app_id':document['app_id'],'goal':document['goal'],
                              'duration_ms':document['media']['duration_ms'],'phase':'batch','frames':batch,'image_frames':batch,
                              'previous_events':[{k:e[k] for k in ('start_ms','end_ms','action','target')} for e in events[-3:]],
                              'baseline_examples':document.get('baseline_examples',[])}
        result=ai.run_ai_skill('ui_video_steps', payload, image_assets=assets, version='batch',
                              timeout=120,retry_count=0,max_tokens=10000,repair_invalid_json=False)
        if cancelled(): raise ValueError('video_cancelled')
        result=_align_batch_evidence_times(result,batch,document['media']['duration_ms'])
        try:
            checked=validate_analysis(result,batch,document['media']['duration_ms'],known_secrets)
        except ValueError as error:
            if repair_used or error.args!=('video_reference_invalid',):raise
            # Repair only malformed optional outcome references. All original
            # action evidence must independently pass before retry is allowed.
            action_only=copy.deepcopy(result)
            for event in action_only.get('events',[]):event.pop('outcome',None)
            original=validate_analysis(action_only,batch,document['media']['duration_ms'],known_secrets)
            known_secrets.extend(e['value'] for e in result['events'] if e['action']=='aiInput' and isinstance(e['value'],str) and e['value'])
            repair_used=True
            if cancelled():raise ValueError('video_cancelled')
            result=ai.run_ai_skill('ui_video_steps',{**payload,
                'candidate_events':original['events'],
                'required_candidate_indexes':_required_interactions(original['events'],document['media']['duration_ms']),
                'validation_feedback':
                'candidate_events是上次已校验且脱敏的操作证据。逐项保留原action、target、时间及frame_refs；只补正可选outcome，不重新猜测或删除交互。上次outcome结果帧引用无效。重新核对同一图片：事件frame_refs在start_ms至end_ms之间；outcome每个引用须在end_ms之后或同刻。没有合法结果帧请省略outcome。保留原有可靠交互，不编造业务结果。'},
                image_assets=assets,version='batch',timeout=120,retry_count=0,max_tokens=10000,repair_invalid_json=False)
            if cancelled():raise ValueError('video_cancelled')
            result=_align_batch_evidence_times(result,batch,document['media']['duration_ms'])
            checked=validate_analysis(result,batch,document['media']['duration_ms'],known_secrets)
            _preserve_interactions(original['events'],checked['events'],_required_interactions(original['events'],document['media']['duration_ms']))
            checked['multiple_scenarios']|=original['multiple_scenarios']
        known_secrets.extend(e['value'] for e in result['events'] if e['action']=='aiInput' and isinstance(e['value'],str) and e['value'])
        audit_events.extend(copy.deepcopy(checked['events']))
        for event in checked['events']:
            # Overlap may report a prior event; only exact same temporal identity
            # is deduplicated. Repeated same controls at different times survive.
            identity=(event['start_ms'],event['end_ms'],event['action'],event['target'])
            if any((e['start_ms'],e['end_ms'],e['action'],e['target'])==identity for e in events): continue
            if event['start_ms']<last_end: raise ValueError('video_model_timeline')
            events.append(event);last_end=event['start_ms']
        questions.extend(checked['uncertainties']);multi|=checked['multiple_scenarios']
        if len(events)>50: raise ValueError('video_split_required')
    raw_events=copy.deepcopy(audit_events)
    candidates=copy.deepcopy(events)
    raw_questions=list(dict.fromkeys(questions))
    if events:
        if cancelled():raise ValueError('video_cancelled')
        required_indices=_required_interactions(candidates,document['media']['duration_ms'])
        image_frames=_consolidation_images(events,frames,limit)
        assets=[{'name':f['frame_id'],'mime':'image/jpeg','base64':base64.b64encode((root/'frames'/(f['frame_id']+'.jpg')).read_bytes()).decode()} for f in image_frames]
        if cancelled():raise ValueError('video_cancelled')
        payload={'phase':'consolidate','app_id':document['app_id'],
                    'goal':document['goal'],'duration_ms':document['media']['duration_ms'],
                    'frames':frames,'image_frames':image_frames,'candidate_events':candidates,
                    'raw_uncertainties':raw_questions,'multiple_scenarios':multi,
                    'required_candidate_indexes':required_indices}
        reuse_errors={
            'video_model_timeline','video_required_action_missing',
            'video_reference_invalid','video_selection_invalid','不支持的运行时对象引用',
            '每条用例只能选取一次对象，选取条件不能引用尚未读取的对象',
            '请先添加“选取本次测试对象”，再引用本次对象',
            '可复用步骤不能固定录屏编号或日期，请使用本次对象/保留对象',
            '选取后必须使用本次对象定位操作',
            '请同时检查本次对象的结果和保留对象，避免误操作',
        }
        for correction in range(2):
            if cancelled():raise ValueError('video_cancelled')
            result=ai.run_ai_skill('ui_video_steps',payload,image_assets=assets,version='consolidate',
                        timeout=120,retry_count=0,max_tokens=10000,repair_invalid_json=False)
            if cancelled():raise ValueError('video_cancelled')
            try:
                result=_bind_candidate_evidence(result,candidates,required_indices)
                checked=validate_analysis(result,frames,document['media']['duration_ms'],known_secrets)
                multi|=checked['multiple_scenarios']
                _validate_consolidated_evidence(checked,raw_events,candidates,frames,image_frames,document['media']['duration_ms'],required_indices)
                if any(event.get('selection') or '{{' in event['target'] or event.get('reusable_expected') for event in checked['events']):
                    draft_proposal({**document,**checked,
                        'source_id':document.get('source_id','uvs_'+'0'*32),
                        'analysis_version':document.get('analysis_version',1)})
                break
            except ValueError as error:
                # Share the existing one-correction budget. Never relax the
                # draft gate or include arbitrary provider output in feedback.
                reason=error.args[0] if error.args else None
                if correction or repair_used or reason not in reuse_errors:raise
                if reason=='video_reference_invalid':
                    action_only=copy.deepcopy(result)
                    for event in action_only.get('events',[]):event.pop('outcome',None)
                    validate_analysis(action_only,frames,document['media']['duration_ms'],known_secrets)
                # Preserve first-response restrictions even when schema checks
                # failed before producing a cleaned result.
                if type(result.get('multiple_scenarios')) is bool:multi|=result['multiple_scenarios']
                known_secrets.extend(e['value'] for e in result.get('events',[])
                    if isinstance(e,dict) and e.get('action')=='aiInput' and isinstance(e.get('value'),str) and e['value'])
                repair_used=True
                payload={**payload,'multiple_scenarios':multi,'validation_feedback':
                    '整理校验失败：'+reason+'。逐项保留required_candidate_indexes的必要交互，不可遗漏或合并两次点击。'
                    '用candidate_index引用原候选，每项一个，严格递增不重复且action不变。不要输出start_ms/end_ms/frame_refs/evidence_kind；平台负责绑定来源。'
                    'outcome仅引用事件end_ms之后或同刻且下一业务交互之前的结果帧；操作前图片不得混入。'
                    '没有合法结果帧时省略outcome并保留结果不明问题，不编造成功。'
                    'selection全片只能一个，与首次使用{{本次对象}}的aiTap同一事件；'
                    'selection本身只写普通中文筛选条件，不得含任何{{}}引用或历史编号。'
                    '后续操作使用{{本次对象}}，最终结果使用{{本次对象}}和{{保留对象}}；'
                    '观察仍保留原事实，reusable_expected单独给出无历史编号的检查建议。'
                    '不适用的可选字段省略，不得改动允许范围或新增业务操作。'}
        events=checked['events'];questions=checked['uncertainties'];title=checked.get('title','')
        multi=multi or checked['multiple_scenarios']
    return {'events':events,'uncertainties':list(dict.fromkeys(questions)), 'multiple_scenarios':multi,
            'raw_events':raw_events,'raw_uncertainties':raw_questions,**({'title':title} if title else {})}


def _visible_state(text):
    if re.search(r'显示|出现|可见|打开|包含|弹出|消失|保留|仍在|关闭|显现|返回|成为',text):return True
    for target in re.findall(r'(?:跳转至|跳转到|进入|切换至|切换到|变为)([^，,。；;]{2,100})',text):
        if not re.fullmatch(r'(?:目标|新|下一|其他|另一个|未知)?(?:页面|界面|页)',target.strip()):return True
    return False


def _stable_visible_observation(observation):
    """Project only literal stable clauses; preserve the complete source audit."""
    feedback=r'高亮|按下态|点击态|(?i:toast)|提示浮层|短暂提示|短暂(?:显示|出现|弹出)[^，,。；;]{0,80}提示|正在加载|加载(?:中|状态|动画)'
    if not re.search(feedback,observation):return observation
    # Removing a conditional/contrast clause may reverse its meaning. Leave the
    # expected unresolved instead of extracting an apparently positive outcome.
    if re.search(r'如果|假如|若(?!干)|除非|只有|仅当|一旦|否则|不是|并非|而是|不代表|所以|因此|不仅|不但|而且|没有证据|缺乏证据|无证据|证据不足|未证实|否认|无法确认|不能确定|(?:未|没|没有|不曾|尚未)(?:观察|看到|看见|见到|发现|检测)',observation):return ''
    stable=[]
    for clause in re.split(r'[，,。；;]|并且|且|同时',observation):
        clause=clause.strip()
        if (clause and not re.search(feedback,clause) and
            _visible_state(clause)):
            stable.append(clause)
    return '；'.join(stable)


def _expected_candidate(event, goal):
    """A sourced visible check suggestion, never an established business outcome."""
    outcome=event.get('outcome')
    observation=_stable_visible_observation((outcome['observation'] if outcome else event['observation']).strip())
    concrete=(len(observation)>=6 and _visible_state(observation)
              and not re.search(r'不明确|不确定|无法|可能|似乎|未知|跳帧|页面变化|界面变化',observation))
    candidate=observation if concrete else ''
    # Never extract a goal clause: doing so loses negation and scope. Only a
    # literal identical goal can share this observed candidate's attribution.
    basis=('user_goal' if candidate and candidate==goal.strip()
           else 'visible_candidate' if candidate else 'unresolved')
    return {'text':candidate,'basis':basis,'frame_refs':list(outcome['frame_refs'] if outcome else event['frame_refs']),'requires_confirmation':True}


def draft_proposal(document):
    identifier=document['source_id'];version=document['analysis_version']
    snapshot={'kind':'video','source_id':identifier,'app_id':document['app_id'],'analysis_version':version,
              'duration_ms':document['media']['duration_ms'],'frames':document['media']['frames'],
              'events':copy.deepcopy(document['events']),'goal':document['goal'],
              'generated_title':document.get('title','')}
    snapshot['snapshot_id']=hashlib.sha256(json.dumps([identifier,version]).encode()).hexdigest()
    steps=[];questions=list(document['uncertainties'])
    for index,event in enumerate(snapshot['events']):
        step_id=str(uuid.uuid4());event['draft_step_id']=step_id
        ref=f"video:{identifier}:{version}:{index}"
        event['expected_candidate']=_expected_candidate(event,document['goal'])
        expected=event['expected_candidate']['text'] if event['action'] in {'aiTap','aiInput','aiScroll'} else ''
        target=event['target']
        if event.get('reusable_expected') and event['expected_candidate']['text']:
            # A reuse proposal is not a new observed fact. Keep the literal
            # expected_candidate and its frame references in the source snapshot.
            if event['action'] in {'aiWaitFor','aiAssert'}:target=event['reusable_expected']
            else:expected=event['reusable_expected']
            questions.append('请确认复用后的结果检查与原录屏一致：'+event['reusable_expected'])
        if event.get('selection'):
            event['selection_step_id']=str(uuid.uuid4())
            steps.append({'step_id':event['selection_step_id'],'action':'selectRecord','target':event['selection'],'value':'',
                          'expected':'','source_ref':ref,'review_state':'needs_review'})
            questions.append('请确认本次测试对象的选取范围：'+event['selection']+'。录屏仅证明历史操作，不代表允许操作其他记录。')
        steps.append({'step_id':step_id,'action':event['action'],'target':target,'value':event['value'],
                      'expected':expected,'source_ref':ref,'review_state':'needs_review'})
        questions.extend(f'片段 {event["start_ms"]/1000:g}–{event["end_ms"]/1000:g} 秒：{q}' for q in event['uncertainties'])
        if re.search('删除|移除',event['target']) and re.search(r'\d{12,}|\d{4}[-/]\d{2}[-/]\d{2}',event['target']) and not event.get('selection'):
            questions.append('删除目标包含录屏中的一次性编号或日期，请改为选取本次测试对象；特定对象用例请确认数据仍存在。')
        if re.search('删除|支付|付款|打印|下单',event['target']): questions.append('含删除/支付/打印等有副作用动作，请核对测试数据与授权后再手工试跑')
    if steps and steps[-1]['action']!='aiAssert' and len(steps)<50:
        event=snapshot['events'][-1]
        if event['expected_candidate']['text']:
            check_id=str(uuid.uuid4());event['expected_check_step_id']=check_id
            steps.append({'step_id':check_id,'action':'aiAssert','target':(steps[-1]['target'] if steps[-1]['action']=='aiWaitFor' and event.get('reusable_expected') else steps[-1].get('expected') or event['expected_candidate']['text']),
                          'value':'','expected':'','source_ref':steps[-1]['source_ref'],'review_state':'needs_review'})
    if not steps or steps[-1]['action']!='aiAssert':
        questions.append('视频未给出明确结束检查，请补充业务预期；观察到的变化不能代替业务应有结果。')
    goal=document['goal'].strip()
    final_check=steps[-1]['target'] if steps and steps[-1]['action']=='aiAssert' else ''
    candidates=list(dict.fromkeys(event['expected_candidate']['text'] for event in snapshot['events'] if event['expected_candidate']['text']))
    execution_targets={step['step_id']:step['target'] for step in steps}
    assertion_mismatch=any(event['action']=='aiAssert' and event['expected_candidate']['text'] and execution_targets[event['draft_step_id']]!=event['expected_candidate']['text'] for event in snapshot['events'])
    needs_confirmation=bool(goal and goal!=final_check) or assertion_mismatch
    snapshot['goal_review']={'status':'needs_confirmation' if needs_confirmation else 'literal_match' if goal else 'not_provided',
                             'visible_candidates':candidates,'requires_confirmation':needs_confirmation}
    if needs_confirmation:
        # Central human confirmation preserves the entire original goal in the
        # immutable snapshot; no keyword-based success/negation interpretation.
        visible='；'.join(candidates)[:500] or '没有明确可见结果'
        questions.append(f'请明确确认目标与候选检查是否一致。用户目标原文：{goal[:600] or "未填写"}。录屏可见检查候选：{visible}。末尾检查候选：{final_check[:400] or "未明确"}。'
                         '平台未解释目标中的否定、范围或业务含义；如有冲突请修改预期/结束检查，再逐步确认。完整目标保留在来源详情。')
    normalized=normalize_steps({'title':document.get('title') or '录屏操作验证','preconditions':[],'steps':steps,'questions':questions},document['app_id'])
    return normalized,snapshot
