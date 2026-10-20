"""Validate explicit same-interaction evidence; never erase unresolved anchors."""
import copy
import re


def _fail():
    raise ValueError('video_coverage_resolution_invalid')


def _signature(event):
    return (event['action'],event['target'],tuple(event['frame_refs']))


def boundary_safe(event,boundary,resolutions,frames):
    """Only validated mappings may share a prior state, never a prior touch."""
    overlap=set(event['frame_refs'])&set(boundary)
    if event['action'] not in {'aiTap','aiInput','aiScroll'} or not overlap:return True
    if event['action']!='aiTap':return False
    matches=[r for r in resolutions if _signature(r['reviewed_event'])==_signature(event)]
    if len(matches)!=1:return False
    ev=matches[0]['evidence'];times={f['frame_id']:f['timestamp_ms'] for f in frames}
    before,touch,after=(ev[k] for k in ('before_frame','interaction_frame','after_frame'))
    return (overlap=={before} and touch not in boundary and after not in boundary
            and times[before]<times[touch]<times[after])


def diagnose(claims,sources,reviewed,frames):
    """Read-only retry context. Structural candidates never authorize a merge."""
    groups=eligible_groups(sources,{f['frame_id'] for f in frames})
    rejected=[];anchors=[]
    for index,claim in enumerate(claims if isinstance(claims,list) else []):
        try:
            validate([claim],sources,reviewed,frames)
        except (ValueError,KeyError,TypeError):
            rejected.append(index)
        else:
            continue
        if not isinstance(claim,dict):continue
        evidence=claim.get('evidence')
        refs=evidence.get('examined_frame_refs',[]) if isinstance(evidence,dict) else []
        refs={r for r in refs if isinstance(r,str)} if isinstance(refs,list) else set()
        ids=claim.get('source_anchor_ids',[])
        for aid in ids if isinstance(ids,list) else []:
            if not isinstance(aid,str):continue
            parts=aid.split(':')
            if len(parts)!=2 or parts[0] not in sources or not parts[1].isdigit():continue
            pos=int(parts[1])
            if pos>=len(sources[parts[0]]):continue
            old=sources[parts[0]][pos]
            anchors.append({
                'anchor_id':aid,'action':old['action'],'frame_refs':list(old['frame_refs']),
                'missing_declared_frame_refs':[r for r in old['frame_refs'] if r not in refs],
                'structural_groups':[g for g in groups if aid in g['anchor_ids']],
            })
    return {'rejected_claims':rejected,'anchors_to_recheck':anchors,
            'instruction':'删除非法声明不免除旧锚点的解释责任。重新核对完整原帧；结构候选不是动作真值。属于已有交互时完整声明对应组及改型证据，独立操作必须保留；不能确定则报告疑问，不造操作或断言凑数。'}


def validate(claims,sources,reviewed,frames):
    if not isinstance(claims,list) or len(claims)>8:_fail()
    times={f['frame_id']:f['timestamp_ms'] for f in frames}
    used=set();result=[];targets={};destinations=set()
    for claim in claims:
        if not isinstance(claim,dict) or set(claim)!={'source_anchor_ids','resolution','reviewed_event_index','evidence'}:_fail()
        ids=claim['source_anchor_ids'];index=claim['reviewed_event_index'];ev=claim['evidence']
        if (not isinstance(ids,list) or not 2<=len(ids)<=8 or any(not isinstance(i,str) for i in ids) or
            len(set(ids))!=len(ids) or set(ids)&used or type(index) is not int or not 0<=index<len(reviewed) or index in destinations):_fail()
        if not any(sum(aid.startswith(name+':') for aid in ids)>=2 for name in sources):_fail()
        if claim['resolution'] not in {'same_interaction','same_interaction_with_action_correction'}:_fail()
        if not isinstance(ev,dict) or set(ev)!={'before_frame','interaction_frame','after_frame','examined_frame_refs','before_state','interaction','after_state','why_not_separate_actions'}:_fail()
        refs=ev['examined_frame_refs']
        if (not isinstance(refs,list) or not 2<=len(refs)<=8 or any(not isinstance(r,str) or r not in times for r in refs) or
            len(set(refs))!=len(refs) or refs!=sorted(refs,key=times.get)):_fail()
        for key in ('before_state','interaction','after_state','why_not_separate_actions'):
            if not isinstance(ev[key],str) or not 3<=len(ev[key].strip())<=800 or re.search(r'可能|不确定|无法确认|疑似',ev[key]):_fail()
        before,touch,after=(ev[k] for k in ('before_frame','interaction_frame','after_frame'))
        if any(not isinstance(r,str) or r not in refs for r in (before,touch,after)):_fail()
        if not times[before]<=times[touch]<times[after] or before==after:_fail()
        new=reviewed[index]
        if new['action'] not in {'aiTap','aiScroll'} or new.get('uncertainties') or not {before,touch,after}<=set(new['frame_refs']):_fail()
        if touch in targets and targets[touch]!=new['target']:_fail()
        anchors=[]
        for aid in ids:
            parts=aid.split(':')
            if len(parts)!=2 or parts[0] not in sources or not parts[1].isdigit():_fail()
            pos=int(parts[1]);source=sources[parts[0]]
            if pos>=len(source):_fail()
            old=source[pos]
            if old['action'] not in {'aiTap','aiScroll'} or touch not in old['frame_refs'] or not set(old['frame_refs'])<=set(refs):_fail()
            if old['action']!=new['action']:
                if (old['action']!='aiScroll' or new['action']!='aiTap' or
                    not re.search(r'(?:未见|没有|未观察到)(?:独立|新增|新的|用户)?(?:滚动|滑动)',ev['interaction']) or '点击' not in ev['interaction']):_fail()
            if new['action']=='aiScroll':
                statement=ev['interaction']+'。'+ev['why_not_separate_actions']
                # Remove explicit negatives before looking for admitted new gestures.
                affirmative=re.sub(r'(?:未见|未观察到|没有|无)[^。；，,]{0,40}(?:触点|按下|重触|滑动|滚动|操作|手势)', '', statement)
                if (old.get('value')!=new.get('value') or
                    not re.search(r'同一次|单次',statement) or
                    not re.search(r'(?:未见|未观察到|没有|无)[^。；，,]{0,30}(?:第二次|二次|再次|重新|新的)[^。；，,]{0,20}(?:触点|按下|重触|滑动|滚动|手势)',statement) or
                    re.search(r'再次|重新|反向|两次|多次|先.*随后',affirmative)):_fail()
            anchors.append(old)
        low=min(e['start_ms'] for e in anchors);high=max(e['end_ms'] for e in anchors)
        for source_name,source in sources.items():
            for pos,old in enumerate(source):
                if (f'{source_name}:{pos}' not in ids and old['action'] in {'aiTap','aiInput','aiScroll'} and
                    low<old['start_ms']<high and old['end_ms']<=high):_fail()
        used.update(ids);destinations.add(index);targets[touch]=new['target']
        normalized='same_interaction_with_action_correction' if any(old['action']!=new['action'] for old in anchors) else 'same_interaction'
        result.append({**copy.deepcopy(claim),'resolution':normalized,'reviewed_event':copy.deepcopy(new),
                       'source_anchors':{aid:copy.deepcopy(sources[aid.split(':')[0]][int(aid.split(':')[1])]) for aid in ids}})
    return result


def positions(resolutions,source,reviewed):
    mapping={}
    for resolution in resolutions or []:
        matches=[i for i,e in enumerate(reviewed) if _signature(e)==_signature(resolution['reviewed_event'])]
        if len(matches)!=1:_fail()
        for aid in resolution['source_anchor_ids']:
            name,index=aid.split(':')
            if name==source:mapping[int(index)]=matches[0]
    return mapping


def eligible_groups(sources,frame_ids):
    """Expose structural candidates only, never claim pixels show a single action."""
    by_frame={}
    for name,events in sources.items():
        for index,event in enumerate(events):
            if event['action'] not in {'aiTap','aiScroll'} or not set(event['frame_refs'])<=frame_ids:continue
            for ref in event['frame_refs']:
                by_frame.setdefault(ref,[]).append((f'{name}:{index}',event['action']))
    result=[]
    for ref,anchors in by_frame.items():
        if not any(sum(aid.startswith(name+':') for aid,_ in anchors)>=2 and
                   any(aid.startswith(name+':') and action=='aiTap' for aid,action in anchors) for name in sources):continue
        result.append({'common_frame':ref,'anchor_ids':[aid for aid,_ in anchors]})
    return result
