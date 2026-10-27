"""State memory and owned action packets; diagnostic observer, not execution authority."""
import copy
import math
import time

from .ui_video_analysis import _bind_whole_evidence, validate_analysis

ROOT_FIELDS={'states','events','checks','uncertainties','multiple_scenarios'}
EVENT_FIELDS={'action','target','value','frame_refs','observation','uncertainties','trigger_frame',
              'trigger_kind','page_before','page_after','continuation_of','edit_boundary','outcome'}
INTERACTIONS={'aiTap','aiInput','aiScroll'}


def _fail(reason):
    error=ValueError('video_model_invalid');error.trajectory_reason=reason
    raise error


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


def validate_states(states,frames):
    times={f['frame_id']:f['timestamp_ms'] for f in frames}
    if not isinstance(states,list) or len(states)>60:_fail('states')
    last=-1
    for s in states:
        if not isinstance(s,dict) or set(s)!={'state_id','first_frame','last_frame','kind','description'}:_fail('state_shape')
        _text(s['state_id'],80);_text(s['description'])
        if s['kind'] not in {'page','input','wait','transition'}:_fail('state_kind')
        a,b=s['first_frame'],s['last_frame']
        if a not in times or b not in times or not last<=times[a]<=times[b]:_fail('state_reference')
        last=times[a]
    return copy.deepcopy(states)


def make_packets(frames,states):
    if not 2<=len(frames)<=300:_fail('frame_count')
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
                        'owned_start_ms':frames[start]['timestamp_ms'],
                        'owned_end_ms':frames[stop]['timestamp_ms'] if stop<len(frames) else frames[-1]['timestamp_ms']+1,
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
        if action in INTERACTIONS and kind=='automatic':_fail('automatic_interaction')
        if action not in INTERACTIONS and kind in {'touch','pressed_control','navigation','edit'}:_fail('interaction_as_state')
        if action=='aiInput' and (kind!='edit' or raw['value']!=''):_fail('input_contract')
        if raw['edit_boundary'] not in {'none','submission','field_change','navigation'}:_fail('edit_boundary')
        outcome=raw['outcome']
        if not isinstance(outcome,dict) or set(outcome)!={'frame_refs','observation'}:_fail('outcome')
        after=_refs(outcome['frame_refs'],times,limit=60)
        if not set(after)<=allowed or times[after[0]]<times[trigger] or times[after[-1]]<times[refs[-1]]:_fail('outcome_reference')
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
                not 0<=times[refs[0]]-events[-1]['end_ms']<=5000):_fail('input_continuation')
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


def audit_claim_context(trajectory,prior_events):
    """Fork audit claims without observer narrative; preserve original evidence."""
    claims=copy.deepcopy(trajectory);prior=copy.deepcopy(prior_events)
    for item in claims:item['track'].pop('observation',None)
    for item in prior:
        item['event'].pop('observation',None)
        for fragment in item['fragments']:fragment.pop('observation',None)
    return claims,prior


def observe_trajectory(document,root,cancelled,progress=None,record=None,revisit=None):
    from . import ai_skill_service as ai
    from .ui_video_windows import load_assets
    frames=copy.deepcopy(document['media']['frames']);duration=document['media']['duration_ms']
    calls=0;corrections=0;revisits=0;started=time.monotonic();secrets=[];call_audit=[]
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
        if calls>=24 or time.monotonic()-started>1200:raise ValueError('video_vision_limit')
        calls+=1;trace={};tick=time.monotonic()
        payload={'phase':'whole-trajectory','duration_ms':duration,'frames':images,'image_frames':images,**context,'reasoning_mode':'bounded'}
        if record:record(f'{calls:02d}-{version}-request',redact(payload))
        raw=ai.run_ai_skill('ui_video_trajectory',payload,version=version,image_assets=load_assets(images,root,cancelled),
                            runtime_trace=trace,timeout=120,respect_global_timeout=False,retry_count=0,max_tokens=10000,repair_invalid_json=False)
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
            if counts:entry['usage']=counts
        call_audit.append(entry)
        if record:record(f'{calls:02d}-{version}-result',redact({'result':raw,'runtime':entry}))
        return _envelope(raw,version)
    def ask(version,images,validator=None,**context):
        nonlocal corrections
        while True:
            try:
                raw=ask_once(version,images,**context)
                if version=='overview' and (raw['events'] or raw['checks']):_fail('overview_shape')
                if version=='observe' and raw['checks']:_fail('observe_checks')
                if validator is not None and not raw.get('revisit'):validator(raw)
                return raw
            except ValueError as error:
                if corrections>=2:raise
                from .ai_skill_service import SkillSchemaError
                if not isinstance(error,SkillSchemaError) and not hasattr(error,'trajectory_reason'):raise
                corrections+=1
                reason=getattr(error,'trajectory_reason','schema')
                if record:record(f'{calls:02d}-contract-rejection',{'stage':version,'reason':reason})
                explanation={
                    'audit_shape':'audit根只能含checks/issues/multiple_scenarios/revisit；check只能含event_id/trigger_verdict/outcome_verdict/question，不得输出旧status/frame_refs或states/events。',
                    'audit_missing_event':'checks数量必须与trajectory一致，每个track.event_id恰好出现一次，不得漏项或重复。',
                    'audit_identity':'checks只能使用给定track.event_id，每个ID恰好一次。',
                    'frame_reference':'每项frame_refs必须为1至8个真实且唯一的原帧ID，按时间顺序排列。',
                    'audit_evidence':'每项frame_refs必须包含其required_frame_refs，额外帧只能来自同项allowed_frame_refs。',
                    'audit_verdict':'trigger_verdict和outcome_verdict只能是supported、unclear或contradicted字符串。',
                    'audit_issue_owner':'issues的owner_frame必须属于本包owned_frame_ids且包含在该issue的frame_refs中，不可把context帧当本包新操作。',
                    'audit_issue_shape':'issues每项只能含kind/owner_frame/frame_refs/question。',
                    'audit_issue_kind':'issue.kind只能为missing_action或unresolved_evidence。',
                    'audit_issues':'issues必须为最多30项的数组。',
                    'audit_question':'任一verdict不是supported时必须填写具体证据疑问。',
                    'audit_contradiction':'双supported的question必须为空；仍有疑问请明确使用unclear或contradicted。',
                }.get(reason,'当前阶段输出结构不符合合同，请只返回规定字段及合法值。')
                context['validation_feedback']=('平台合同校验未通过：'+explanation+
                    ('重新核对全部附图，分别判断给定片段的触发和结果；check不返回帧号，由平台绑定原片段锚点。'
                     if version=='audit' else '重新核对本阶段全部原图，完整输出该阶段合同。')+'不补造操作。')
    def expand(packet,requests):
        nonlocal revisits,frames
        if not requests:return False
        if revisit is None:_fail('revisit_unavailable')
        times={f['frame_id']:f['timestamp_ms'] for f in frames}
        allowed={f['frame_id'] for f in packet['frames']}
        start,end=packet['owned_start_ms'],packet['owned_end_ms']
        for request in requests:
            if (not isinstance(request,dict) or set(request)!={'first_frame','last_frame','question'} or
                type(request['first_frame']) is not str or type(request['last_frame']) is not str or
                request['first_frame'] not in allowed or request['last_frame'] not in allowed):_fail('revisit_reference')
            _text(request['question'])
            left,right=times[request['first_frame']],times[request['last_frame']]
            if left<start or right>end:_fail('revisit_outside_owned_interval')
            if not 0<right-left<=3000 or revisits>=4:_fail('revisit_budget')
            capacity=min(20,60-len(packet['frames']))
            if capacity<2:raise ValueError('video_vision_limit')
            extra=revisit(request,capacity)
            if cancelled():raise ValueError('video_cancelled')
            if not isinstance(extra,list) or not 1<=len(extra)<=capacity:_fail('revisit_result')
            stamps=set(times.values());names=set(times)
            for f in extra:
                if (not isinstance(f,dict) or set(f)!={'frame_id','timestamp_ms'} or
                    type(f['frame_id']) is not str or f['frame_id'] in names or type(f['timestamp_ms']) is not int or
                    not left<=f['timestamp_ms']<=right or f['timestamp_ms'] in stamps):_fail('revisit_result')
                names.add(f['frame_id']);stamps.add(f['timestamp_ms'])
            revisits+=1;frames=sorted(frames+extra,key=lambda f:f['timestamp_ms'])
            packet['frames']=sorted(packet['frames']+extra,key=lambda f:f['timestamp_ms'])
            owned=set(packet['owned_frame_ids'])|{f['frame_id'] for f in extra if start<=f['timestamp_ms']<end}
            packet['owned_frame_ids']=[f['frame_id'] for f in packet['frames'] if f['frame_id'] in owned]
            packet['context_frame_ids']=[f['frame_id'] for f in packet['frames'] if f['frame_id'] not in owned]
            allowed.update(f['frame_id'] for f in extra);times.update({f['frame_id']:f['timestamp_ms'] for f in extra})
            if record:record(f'revisit-{revisits}',{'request':request,'new_frames':extra,'packet_id':packet['packet_id']})
        return True
    images=overview_frames(frames)
    overview=ask('overview',images)
    if overview['events'] or overview['checks']:_fail('overview_shape')
    states=validate_states(overview['states'],images)
    overview_hypotheses=copy.deepcopy(overview['uncertainties'])
    packets=make_packets(frames,states);events=[];tracks=[];questions=[];multi=overview['multiple_scenarios']
    packet_outputs=[];checks=[];fragment_checks=[]
    for index,packet in enumerate(packets):
        if progress:progress('observing',index,len(packets),events)
        feedback=''
        # Audit before proceeding: a requested revisit can re-observe this packet
        # without invalidating identities in any later packet.
        while True:
            raw=ask('observe',packet['frames'],states=states,overview_hypotheses=overview_hypotheses,packet_id=packet['packet_id'],
                    owned_frame_ids=packet['owned_frame_ids'],context_frame_ids=packet['context_frame_ids'],
                    owned_time_range_ms=[packet['owned_start_ms'],packet['owned_end_ms']],
                    prior_events=[{**t,'event':e} for t,e in list(zip(tracks,events))[-8:]],validation_feedback=feedback)
            if expand(packet,raw.get('revisit',[])):continue
            try:
                extra=validate_states(raw['states'],packet['frames'])
                proposed,history=bind_packet_events(raw['events'],packet,frames,events,tracks)
            except ValueError as error:
                if corrections>=2:raise
                corrections+=1;feedback='结构或证据未通过：'+getattr(error,'trajectory_reason','contract')+'。重看同一组原图，完整输出当前包；不改写上下文动作，不补造操作。'
                continue
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
            audit_batch,audit_prior=audit_claim_context(batch,[{**t,'event':e} for t,e in list(zip(tracks,events))[-8:]])
            audited=ask('audit',packet['frames'],validator=lambda result:(bind_audit_checks(result['checks'],[p['track'] for p in batch],packet['frames']),validate_audit_issues(result['issues'],packet)),
                    state_memory=states+extra,overview_hypotheses=overview_hypotheses,trajectory=audit_batch,
                    packet_id=packet['packet_id'],owned_frame_ids=packet['owned_frame_ids'],
                    context_frame_ids=packet['context_frame_ids'],
                    prior_events=audit_prior)
            if expand(packet,audited.get('revisit',[])):continue
            bound_checks=bind_audit_checks(audited['checks'],[p['track'] for p in batch],packet['frames'])
            break
        events,tracks=proposed,history;states.extend(extra)
        packet_outputs.append({'packet':packet,'raw':redact(raw),'audit':redact(audited)})
        fragment_checks.extend(bound_checks)
        questions.extend(raw['uncertainties']+[issue['question'] for issue in audited['issues']])
        multi|=raw['multiple_scenarios'] or audited['multiple_scenarios']
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
    checked=validate_analysis({'events':events,'uncertainties':list(dict.fromkeys(questions)),
                               'multiple_scenarios':multi},frames,duration,secrets,max_events=120)
    for e in checked['events']:e['uncertainties']=list(dict.fromkeys(e['uncertainties']))
    result={**checked,'media':{**copy.deepcopy(document['media']),'frames':frames},'trajectory_audit':redact({'version':'trajectory-v2','audit_input_mode':'claims_without_observer_narrative','overview_hypotheses':overview_hypotheses,'states':states,'tracks':tracks,'checks':checks,'fragment_checks':fragment_checks,
             'packets':packet_outputs,'calls':call_audit,'elapsed_seconds':round(time.monotonic()-started,3)})}
    if record:record('trajectory-final',redact(result))
    if progress:progress('planning',1,1,checked['events'])
    return result
