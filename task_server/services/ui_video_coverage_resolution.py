"""Validate explicit same-interaction evidence; never erase unresolved anchors."""
import copy
import re


def _fail(reason=None):
    error=ValueError('video_coverage_resolution_invalid')
    if reason is not None:error.coverage_reason=reason
    raise error


def has_return_gesture_evidence(note):
    """Require affirmative motion evidence; a negated observation is not proof."""
    affirmative=[]
    for clause in re.split(r'[。；，,;\n]|但是|但', note):
        if re.search(r'未|没|无|不|并非', clause):
            continue
        affirmative.append(clause)
        if re.search(r'返回手势|侧滑|手势.*返回|触点.{0,80}(?:位移|轨迹)', clause):
            return True
    return (any(re.search(r'横向位移手势', clause) for clause in affirmative) and
            any(re.search(r'返回操作|返回导航|退回上一级', clause) for clause in affirmative))


def _without_negated_repeat(note):
    """Remove only the negated repeat predicate, never the following action."""
    return re.sub(r'(?:并非|而非|不是|非)(?:两次|多次|第二次)'
                  r'(?:独立的?|单独的?)?(?:点击|按下|返回操作|返回手势|返回导航)', '', note)


def has_confirmed_return_gesture(note):
    """Merge evidence must establish navigation, not merely moving pixels."""
    clauses=re.split(r'[。；，,;\n]|但是|但',_without_negated_repeat(note))
    if any(re.search(r'未|没|无|不|并非',c) and re.search(r'返回|退回|导航',c) for c in clauses):
        return False
    affirmative=[c for c in clauses if not re.search(r'未|没|无|不|并非',c)]
    return (any('返回手势' in c for c in affirmative) or
            (any(re.search(r'滑动手势|横向位移手势',c) for c in affirmative) and
             any(re.search(r'触发返回导航|返回操作|退回上一级',c) for c in affirmative)))


def has_visible_return_control(note):
    if re.search(r'(?:返回箭头|返回按钮|实际控件)[^。；，,]{0,20}(?:不可见|未显示|没有显示|隐藏)|(?:没有|未显示|不可见)[^。；，,]{0,20}返回(?:箭头|按钮)', note):
        return False
    for clause in re.split(r'[。；，,;\n]|但是|但', note):
        if re.search(r'未|没|无|不|并非', clause):
            continue
        if re.search(r'说明|文字|文案', clause):
            continue
        if re.search(r'返回(?:箭头|按钮).*(?:可见|显示)|(?:可见|显示|有).*返回(?:箭头|按钮)', clause):
            return True
    return False



def _validate_return_touch_span(anchors,new,evidence,times):
    """A later overlapping observation may contain only a gesture's suffix.

    Keep the original touch instead of asking the model to label a later
    animation as a new touch. The suffix must have exact source bounds and
    share a retained intermediate frame with every anchor. The caller still
    requires a single return gesture, no second press and no intervening action.
    """
    touch=times[evidence['interaction_frame']]
    if all(a['start_ms']<=touch<=a['end_ms'] for a in anchors):return
    if not min(a['start_ms'] for a in anchors)<=touch<=min(a['end_ms'] for a in anchors):_fail('return_touch_outside_original_span')
    for anchor in anchors:
        refs=anchor['frame_refs']
        if (anchor['start_ms']!=min(times[r] for r in refs) or
            anchor['end_ms']!=max(times[r] for r in refs)):_fail('return_anchor_bounds_not_exact')
    bridge=set(new['frame_refs']).intersection(*(set(a['frame_refs']) for a in anchors))
    if not any(touch<times[r]<times[evidence['after_frame']] for r in bridge):_fail('return_shared_intermediate_frame_missing')


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
    rejected=[];anchors=[];results=[]
    times={f['frame_id']:f['timestamp_ms'] for f in frames}
    for index,claim in enumerate(claims if isinstance(claims,list) else []):
        try:
            validate([claim],sources,reviewed,frames)
        except (ValueError,KeyError,TypeError) as error:
            reason=getattr(error,'coverage_reason','invalid_merge_evidence')
            rejected.append(index)
        else:
            continue
        if not isinstance(claim,dict):continue
        evidence=claim.get('evidence')
        refs=evidence.get('examined_frame_refs',[]) if isinstance(evidence,dict) else []
        refs={r for r in refs if isinstance(r,str)} if isinstance(refs,list) else set()
        ids=claim.get('source_anchor_ids',[])
        result_index=claim.get('reviewed_event_index')
        valid_index=type(result_index) is int and 0<=result_index<len(reviewed)
        result_refs=set(reviewed[result_index].get('frame_refs',[])) if valid_index else set()
        valid_ids=set()
        for aid in ids if isinstance(ids,list) else []:
            if not isinstance(aid,str):continue
            parts=aid.split(':')
            if len(parts)==2 and parts[0] in sources and parts[1].isdigit() and int(parts[1])<len(sources[parts[0]]):
                valid_ids.add((parts[0],int(parts[1])))
        states=[evidence.get(k) for k in ('before_frame','interaction_frame','after_frame')] if isinstance(evidence,dict) else []
        shared_candidates=[]
        if reason=='interaction_frame_not_shared' and valid_ids:
            shared_candidates=sorted((r for r in set.intersection(*(set(sources[name][pos]['frame_refs']) for name,pos in valid_ids)) if r in times),key=times.get)
        bridge_candidates=[]
        if reason=='return_shared_intermediate_frame_missing' and valid_ids and isinstance(evidence,dict):
            shared=set.intersection(*(set(sources[name][pos]['frame_refs']) for name,pos in valid_ids))
            touch_time=times.get(evidence.get('interaction_frame'))
            after_time=times.get(evidence.get('after_frame'))
            if touch_time is not None and after_time is not None:
                bridge_candidates=sorted((r for r in shared if r in times and touch_time<times[r]<after_time),key=times.get)
        results.append({'claim_index':index,'valid_result_index':valid_index,'reason':reason,
                        'shared_intermediate_frame_candidates':bridge_candidates,
                        'shared_interaction_frame_candidates':shared_candidates,
                        'missing_result_state_frames':list(dict.fromkeys(r for r in states if isinstance(r,str) and r in times and r not in result_refs)),
                        'has_same_source_duplicates':any(sum(source==name for source,pos in valid_ids)>=2 for name in sources)})
        for aid in ids if isinstance(ids,list) else []:
            if not isinstance(aid,str):continue
            parts=aid.split(':')
            if len(parts)!=2 or parts[0] not in sources or not parts[1].isdigit():continue
            pos=int(parts[1])
            if pos>=len(sources[parts[0]]):continue
            old=sources[parts[0]][pos]
            touch_time=times.get(evidence.get('interaction_frame')) if isinstance(evidence,dict) and isinstance(evidence.get('interaction_frame'),str) else None
            anchors.append({
                'anchor_id':aid,'action':old['action'],'frame_refs':list(old['frame_refs']),
                'anchor_start_ms':old.get('start_ms'),'anchor_end_ms':old.get('end_ms'),
                'interaction_frame_within_anchor_time':touch_time is not None and old.get('start_ms',float('inf'))<=touch_time<=old.get('end_ms',float('-inf')),
                'missing_declared_frame_refs':[r for r in old['frame_refs'] if r not in refs],
                'interaction_frame_belongs_to_anchor':isinstance(evidence,dict) and evidence.get('interaction_frame') in old['frame_refs'],
                'undeclared_state_frames':[evidence.get(k) for k in ('before_frame','interaction_frame','after_frame') if isinstance(evidence,dict) and (not isinstance(evidence.get(k),str) or evidence.get(k) not in refs)],
                'same_result_claim_anchor_ids':list(dict.fromkeys(
                    ref for other in claims if isinstance(other,dict) and type(claim.get('reviewed_event_index')) is int and
                    type(other.get('reviewed_event_index')) is int and other.get('reviewed_event_index')==claim.get('reviewed_event_index') and
                    isinstance(other.get('source_anchor_ids'),list)
                    for ref in other['source_anchor_ids'] if isinstance(ref,str))),
                'structural_groups':[g for g in groups if aid in g['anchor_ids']],
            })
    return {'rejected_claims':rejected,'anchors_to_recheck':anchors,'result_evidence_to_recheck':results,
            'instruction':'reason说明被拒绝的证据规则。shared_intermediate_frame_candidates仅是全部旧锚点共同引用且位于实际触点与后态之间的原帧；必须核对原图确属同一连续手势后再引用到结果，不得把后态或动画虚构成新触点。missing_result_state_frames表示声明中的前态、实际交互或后态帧未被结果事件引用；须重核原图，仅原图支持时完整引用。has_same_source_duplicates为false表示没有同来源多锚点可合并，普通跨来源一对一对应不需要合并声明。删除非法声明不免除旧锚点的解释责任。重新核对完整原帧；结构候选不是动作真值。属于已有交互时完整声明对应组及改型证据，独立操作必须保留；不能确定则报告疑问，不造操作或断言凑数。'}


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
        if claim['resolution'] not in {'same_interaction','same_interaction_with_action_correction','same_return_gesture','same_scroll_gesture'}:_fail()
        required_evidence={'before_frame','interaction_frame','after_frame','examined_frame_refs','before_state','interaction','after_state','why_not_separate_actions'}
        if claim['resolution']=='same_scroll_gesture':required_evidence.add('gesture_facts')
        if not isinstance(ev,dict) or set(ev)!=required_evidence:_fail()
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
        gesture=claim['resolution']=='same_return_gesture'
        drag=claim['resolution']=='same_scroll_gesture'
        anchors=[]
        for aid in ids:
            parts=aid.split(':')
            if len(parts)!=2 or parts[0] not in sources or not parts[1].isdigit():_fail()
            pos=int(parts[1]);source=sources[parts[0]]
            if pos>=len(source):_fail()
            old=source[pos]
            if old['action'] not in {'aiTap','aiScroll'} or not set(old['frame_refs'])<=set(refs):_fail()
            if gesture:
                if (old['action']!='aiTap' or new['action']!='aiTap' or old['target']!=new['target'] or
                    old.get('value','')!='' or new.get('value','')!='' or
                    times[touch]>old['end_ms']):_fail()
            elif touch not in old['frame_refs']:_fail('interaction_frame_not_shared')
            if old['action']!=new['action'] and not (drag and old['action']=='aiTap' and new['action']=='aiScroll'):
                if (old['action']!='aiScroll' or new['action']!='aiTap' or
                    not re.search(r'(?:未见|没有|未观察到)(?:独立|新增|新的|用户)?(?:滚动|滑动)',ev['interaction']) or '点击' not in ev['interaction']):_fail()
            if new['action']=='aiScroll' and not drag:
                statement=ev['interaction']+'。'+ev['why_not_separate_actions']
                # Remove explicit negatives before looking for admitted new gestures.
                affirmative=re.sub(r'(?:未见|未观察到|没有|无)[^。；，,]{0,40}(?:触点|按下|重触|滑动|滚动|操作|手势)', '', statement)
                if ((old.get('value')!=new.get('value') and not (drag and old['action']=='aiTap' and old.get('value','')=='')) or
                    not re.search(r'同一次|单次',statement) or
                    not re.search(r'(?:未见|未观察到|没有|无)[^。；，,]{0,30}(?:第二次|二次|再次|重新|新的)[^。；，,]{0,20}(?:触点|按下|重触|滑动|滚动|手势)',statement) or
                    re.search(r'再次|重新|反向|两次|多次|先.*随后',affirmative)):_fail()
            anchors.append(old)
        if drag:
            _validate_drag(ids,sources,anchors,new,ev)
        if gesture:
            _validate_return_touch_span(anchors,new,ev,times)
            old_notes='。'.join(a.get('observation','') for a in anchors)
            old_affirmative=re.sub(r'(?:未见|未观察到|没有|无)[^。；，,但]*', '', old_notes)
            if (not has_return_gesture_evidence(old_notes) or re.search(r'(?:再次|第二次|两次|多次)[^。；，,]{0,30}(?:点击|返回|导航|按下)',old_affirmative)):_fail()
            if new['target'] not in {'左上角返回箭头','左上角返回按钮','返回箭头','返回按钮'}:
                _fail('return_target_not_supported')
            if not set.intersection(*(set(a['frame_refs']) for a in anchors)):
                _fail('return_anchors_not_overlapping')
            if not any(touch in a['frame_refs'] for a in anchors):
                _fail('return_touch_not_in_anchor')
            if not has_visible_return_control(ev['before_state']):
                _fail('return_control_not_visible')
            if not re.search(r'同一次|单次|(?<!第)一次(?:连续|完整)的?返回手势',ev['interaction']+'。'+ev['why_not_separate_actions']):
                _fail('return_single_gesture_not_explicit')
            if not has_confirmed_return_gesture(ev['interaction']):
                _fail('return_gesture_not_explicit')
            if not re.search(r'(?:未见|未观察到|没有|无)[^。；]{0,30}(?:第二次|二次|再次|重新|新的)[^。；]{0,20}(?:按下|触点|手势|导航)',ev['why_not_separate_actions']):
                _fail('return_second_press_not_excluded')
            affirmative=re.sub(r'(?:未见|未观察到|没有|无)[^。；，,但]*', '', ev['interaction']+'。'+ev['why_not_separate_actions'])
            # Only remove an explicit attribution to a mistaken OLD analysis.
            # Stop at clause boundaries so a real subsequent action survives.
            affirmative=re.sub(
                r'(?:旧识别|旧锚点|首轮识别)[^。；，,但\n]{0,80}?'
                r'(?:误判为|误识别为|误拆成|拆分为)(?:两次|多次)(?:独立的?|单独的?)?'
                r'(?:点击|返回|操作|手势|导航)(?:操作)?', '', affirmative)
            # A historical attribution may use neutral wording rather than
            # 'misidentified'; require an immediate explicit single-gesture
            # correction and retain that correction and all following clauses.
            affirmative=re.sub(
                r'(?:旧识别|旧锚点|首轮识别)[^。；，,但\n]{0,80}?'
                r'分别识别为(?:两次|多次)(?:独立的?|单独的?)?点击(?:或重复操作)?'
                r'(?=[，,]实际为同一次(?:连续)?返回手势)', '', affirmative)
            # A neutral historical partition is harmless only with an immediate
            # explicit correction. Parentheses cannot conceal a repeated action.
            affirmative=re.sub(
                r'(?:旧识别|旧锚点|首轮识别)[^。；，,但\n]{0,40}?分为(?:两次|多次)点击'
                r'(?:（(?:(?!再次|重新|第二次|两次|多次|[。；，,但\n]).){0,100}）)?'
                r'(?=[，,]实际为同一次(?:连续)?(?:返回)?手势)', '', affirmative)
            affirmative=_without_negated_repeat(affirmative)
            if re.search(r'再次|重新|第[二三四五六七八九十百0-9]+次|[两二三四五六七八九十百2-9]次|多次|又[^。；，,]{0,12}(?:点击|按下|返回|导航)',affirmative):_fail()
        low=min(e['start_ms'] for e in anchors);high=max(e['end_ms'] for e in anchors)
        for source_name,source in sources.items():
            for pos,old in enumerate(source):
                if (f'{source_name}:{pos}' not in ids and old['action'] in {'aiTap','aiInput','aiScroll'} and
                    low<old['start_ms']<high and old['end_ms']<=high):_fail()
        used.update(ids);destinations.add(index);targets[touch]=new['target']
        normalized='same_interaction_with_action_correction' if any(old['action']!=new['action'] for old in anchors) else 'same_interaction'
        result.append({**copy.deepcopy(claim),'resolution':'same_return_gesture' if gesture else 'same_scroll_gesture' if drag else normalized,'reviewed_event':copy.deepcopy(new),
                       'source_anchors':{aid:copy.deepcopy(sources[aid.split(':')[0]][int(aid.split(':')[1])]) for aid in ids}})
    return result


def _validate_drag(ids,sources,anchors,new,evidence):
    """Only a complete adjacent tap/scroll hypothesis can be one drag."""
    if new['action']!='aiScroll' or {a['action'] for a in anchors}!={'aiTap','aiScroll'}:_fail()
    facts=evidence.get('gesture_facts')
    reverse={'up':'down','down':'up','left':'right','right':'left'}
    direction=new.get('value',{}).get('direction')
    if direction not in reverse:_fail('drag_direction_invalid')
    expected={'content_motion_direction':reverse[direction],'interaction_count':1,'independent_click_observed':False,'continuous_drag_observed':True,'release_then_press_observed':False}
    if not isinstance(facts,dict) or set(facts)!=set(expected) or any(type(facts[k]) is not type(v) or facts[k]!=v for k,v in expected.items()):_fail('drag_facts_not_established')
    if any(a['action']=='aiScroll' and {k:v for k,v in a.get('value',{}).items() if k!='direction'}!={k:v for k,v in new['value'].items() if k!='direction'} for a in anchors):_fail()
    if any(a['action']=='aiTap' and '返回' in a.get('target','') for a in anchors):_fail()
    paired=False
    for name,source in sources.items():
        indexes=sorted(int(a.split(':')[1]) for a in ids if a.startswith(name+':'))
        if len(indexes)<2:continue
        selected=[source[i] for i in indexes]
        if len(selected)!=2 or [e['action'] for e in selected]!=['aiTap','aiScroll']:_fail()
        first,second=selected
        between=source[indexes[0]+1:indexes[1]]
        if (any(e['action'] in {'aiTap','aiInput','aiScroll'} for e in between) or
            not first['start_ms']<=second['start_ms']<=first['end_ms']):_fail()
        paired=True
    statement=evidence['interaction']+'。'+evidence['why_not_separate_actions']
    from .ui_video_delayed_response import has_drag_conflict
    if has_drag_conflict(statement+'。'+new['observation']):_fail('drag_conflicting_click_evidence')
    motion_words={'上':'up','下':'down','左':'left','右':'right'}
    for motion in re.findall(r'内容(?:随触点|随手指)?(?:向)?([上下左右])移',statement+'。'+new['observation']):
        if motion_words[motion]!=facts['content_motion_direction']:_fail('drag_conflicting_direction_evidence')
    affirmative='。'.join(c for c in re.split(r'[。；，,;]',statement) if not re.search(r'未|没|无|不|并非',c))
    if (not paired or not re.search(r'触点.{0,20}(?:移动|位移|拖动)',affirmative) or
        not re.search(r'内容.{0,20}(?:位移|上移|下移|移动)',affirmative) or
        re.search(r'独立点击|重新按下|再次|两次|反向',affirmative)):_fail()


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
