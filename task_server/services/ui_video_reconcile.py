"""Re-read full-flow evidence after long recovery loops, before case planning."""
import re


def needs_reconciliation(events,frames):
    gesture=any(re.search(r'手势|侧滑|边缘.*返回',e['target']+' '+e['observation']) for e in events)
    return gesture or (len(frames)>60 and any('取消' in e['target'] for e in events) and any(
        re.search(r'取消.*(?:未完成|失败|未成功)',e['observation']) for e in events))


def check_coverage(original,reviewed,duration=None,resolutions=None,source=None):
    from .ui_video_analysis import _system_boundary
    from .ui_video_coverage_resolution import positions
    mapped=positions(resolutions,source,reviewed) if resolutions else {}
    required=[];claimed=set()
    for index,event in enumerate(original):
        if event['action'] not in ('aiTap','aiInput','aiScroll') or (duration is not None and _system_boundary(event,duration)):continue
        if index in mapped:
            position=mapped[index]
            if position in claimed:continue
            claimed.add(position)
            required.append({**reviewed[position],'_resolved_position':position})
        else:required.append(event)
    # aiInput already focuses its field. Only absorb an explicit focus tap when
    # the immediately following interaction is input into that exact same field,
    # and one reviewed input covers both original time ranges and evidence.
    absorbed=set()
    for i,(first,second) in enumerate(zip(required,required[1:])):
        if '_resolved_position' in first or '_resolved_position' in second:continue
        if (first['action']==second['action']=='aiTap' and first['target']==second['target'] and
            first['end_ms']==second['start_ms'] and second['end_ms']-first['start_ms']<=500 and
            set(first['frame_refs'])&set(second['frame_refs']) and
            first['observation'] and second['observation'].startswith(first['observation']) and
            len(second['observation'])>len(first['observation']) and
            not re.search(r'再次|重试|重新|又|第二次',second['observation'][len(first['observation']):]) and
            re.match(r'(?:随后|点击后|之后)(?:弹窗|页面|界面)',second['observation'][len(first['observation']):]) and
            '触点' in first['observation']):
            absorbed.add(i)
        returns={'返回上一级','左上角返回箭头','左上角返回按钮'}
        if (first['action']==second['action']=='aiTap' and
            first['target'] in returns and second['target'] in returns and
            first['observation']==second['observation'] and
            first['end_ms']==second['end_ms'] and
            set(second['frame_refs'])<=set(first['frame_refs'])):
            absorbed.add(i+1)
    for i,(focus,typed) in enumerate(zip(required,required[1:])):
        if '_resolved_position' in focus or '_resolved_position' in typed:continue
        if (focus['action']!='aiTap' or typed['action']!='aiInput' or
            focus['target']!=typed['target'] or
            not re.search(r'输入框.*(?:获得焦点|键盘)|键盘.*(?:弹出|显示)',focus['observation'])):continue
        if any(new['action']=='aiInput' and new['target']==typed['target'] and
               new['start_ms']<=focus['start_ms'] and new['end_ms']>=typed['end_ms'] and
               set(new['frame_refs'])&set(focus['frame_refs']) and
               set(new['frame_refs'])&set(typed['frame_refs']) for new in reviewed):absorbed.add(i)
    required=[e for i,e in enumerate(required) if i not in absorbed]
    owners={}
    def assign(index,seen):
        old=required[index]
        for position,new in enumerate(reviewed):
            if '_resolved_position' in old and position!=old['_resolved_position']:continue
            if position in seen or not set(old['frame_refs']) & set(new['frame_refs']):continue
            if old['action']=='aiInput' and new['action']!='aiInput':continue
            if old['action']=='aiScroll' and new['action']!='aiScroll':
                # An explicit, full-evidence correction can explain a false
                # scroll caused by asynchronous layout; a generic wait cannot.
                if not (new['action'] in {'aiAssert','aiTap'} and set(old['frame_refs'])<=set(new['frame_refs'])
                    and re.search(r'(?:未观察到|未见|未发生|没有观察到|没有发生|没有|并非|不是|(?<=[，。；])非)(?:任何|实际的|实际|用户|手指|触点|页面|内容|新的|本次|进行|发生){0,3}(?:滚动|滑动)',new['observation'])
                    and re.search(r'自动|加载|布局|轮播' if new['action']=='aiAssert' else r'实际点击|触点.*按钮',new['observation'])):continue
            if old['action']=='aiTap' and new['action']!='aiTap':
                # A return/navigation is still an interaction even if the
                # physical gesture is not directly executable as a tap.
                if not (new['action']=='aiAssert' and set(old['frame_refs'])<=set(new['frame_refs']) and
                    re.search(r'未(?:见|观察到|发生)|没有',new['observation']) and
                    re.search(r'自动|静止|保持不变',new['observation']) and
                    not re.search(r'用户.*(?:执行|触发).*返回|完成.*导航|从.*返回',new['observation'])):continue
            seen.add(position)
            if position not in owners or assign(owners[position],seen):
                owners[position]=index
                return True
        return False
    missing=[index for index in range(len(required)) if not assign(index,set())]
    if missing:
        error=ValueError('video_required_action_missing')
        error.missing_frame_refs={ref for i in missing for ref in required[i]['frame_refs']}
        raise error
    for position,index in owners.items():
        if required[index]['action'] in ('aiTap','aiScroll') and reviewed[position]['action']!=required[index]['action']:
            kind='点击' if required[index]['action']=='aiTap' else '滚动'
            corrected='点击' if reviewed[position]['action']=='aiTap' else '自动状态变化'
            warning=f'首轮{kind}已复核为{corrected}，请核对该段视频'
            if warning not in reviewed[position]['uncertainties']:reviewed[position]['uncertainties'].append(warning)


def reconcile(events,frames,duration,root,cancelled,secrets,progress=None):
    from .ui_video_windows import observe_windows
    # Revisit all decoded pixels. Resampling a long film back to sixty images
    # would lose the before/after states this pass is intended to recover.
    checked,new_secrets=observe_windows(frames,duration,root,cancelled,progress,
                                       review_events=events,prior_secrets=secrets)
    secrets.extend(new_secrets)
    try:
        check_coverage(events,checked['events'],duration)
    except ValueError as error:
        if not getattr(error,'missing_frame_refs',None):raise
        checked=_repair_coverage(events,checked,frames,duration,root,cancelled,secrets,error.missing_frame_refs)
        check_coverage(events,checked['events'],duration,checked.get('reconciliation_audit',{}).get('coverage_resolutions'),'first_pass')
    warnings=[q for e in checked['events'] for q in e['uncertainties'] if q.startswith(('首轮点击','首轮滚动'))]
    checked['uncertainties']=list(dict.fromkeys(checked['uncertainties']+warnings))
    return checked


def _gap_frames(frames,events,missing_refs):
    positions={f['frame_id']:i for i,f in enumerate(frames)}
    seed={i for ref in missing_refs for i in range(max(0,positions[ref]-2),min(len(frames),positions[ref]+3))}
    selected=set(seed)
    for event in events:
        indexes=[positions[r] for r in event['frame_refs']]
        if seed.intersection(indexes):selected.update(range(min(indexes),max(indexes)+1))
    # Keep partial loading/assertion spans unchanged, but a real interaction
    # must never straddle the replacement boundary and be emitted twice.
    while True:
        expanded=set(selected)
        for event in events:
            indexes=[positions[r] for r in event['frame_refs']]
            if event.get('action') in {'aiTap','aiInput','aiScroll'} and selected.intersection(indexes):
                expanded.update(range(min(indexes),max(indexes)+1))
        if len(expanded)>40:raise ValueError('video_required_action_missing')
        if expanded==selected:break
        selected=expanded
    return [f for i,f in enumerate(frames) if i in selected]


def _repair_coverage(original,checked,frames,duration,root,cancelled,secrets,missing_refs):
    """Keep distant conflicts separate, with a fixed four-call total ceiling."""
    import copy
    from .ui_video_analysis import validate_analysis
    affected=[e for e in original+checked['events'] if set(e['frame_refs'])&missing_refs]
    positions={f['frame_id']:i for i,f in enumerate(frames)}
    selected={positions[r] for r in missing_refs}
    for event in affected:
        indexes=[positions[r] for r in event['frame_refs']]
        selected.update(range(min(indexes),max(indexes)+1))
    if len(selected)>40:raise ValueError('video_required_action_missing')
    window=[f for i,f in enumerate(frames) if i in selected]
    groups=[]
    for frame in window:
        if not groups or positions[frame['frame_id']]!=positions[groups[-1][-1]['frame_id']]+1:groups.append([])
        groups[-1].append(frame)
    if len(groups)>2:raise ValueError('video_required_action_missing')
    result=copy.deepcopy(checked);resolutions=[];replacements=[]
    replaced={i for i,e in enumerate(checked['events']) if set(e['frame_refs'])&missing_refs}
    preserved=[e for i,e in enumerate(checked['events']) if i not in replaced]
    boundary={r for e in preserved if e['action'] in {'aiTap','aiInput','aiScroll'} for r in e['frame_refs']}
    all_ids={f['frame_id'] for f in window}
    for group in groups:
        if cancelled():raise ValueError('video_cancelled')
        ids={f['frame_id'] for f in group}
        indexes={name:[i for i,e in enumerate(source) if set(e['frame_refs'])<=ids and set(e['frame_refs'])&missing_refs]
                 for name,source in [('first_pass',original),('independent_review',checked['events'])]}
        local_original=[original[i] for i in indexes['first_pass']]
        local_checked={**checked,'events':[checked['events'][i] for i in indexes['independent_review']]}
        repaired=_repair_window(local_original,local_checked,group,duration,root,cancelled,secrets,missing_refs&ids,boundary&ids,context_frames=frames)
        from .ui_video_coverage_resolution import boundary_safe
        if any(not boundary_safe(e,boundary,repaired['reconciliation_audit']['coverage_resolutions'],frames) for e in repaired['events']):
            raise ValueError('video_required_action_missing')
        replacements.extend(repaired['events'])
        for resolution in repaired['reconciliation_audit']['coverage_resolutions']:
            translated=copy.deepcopy(resolution)
            def global_id(aid):
                name,index=aid.split(':')
                return f'{name}:{indexes[name][int(index)]}'
            translated['source_anchor_ids']=[global_id(aid) for aid in resolution['source_anchor_ids']]
            translated['source_anchors']={global_id(aid):e for aid,e in resolution['source_anchors'].items()}
            resolutions.append(translated)
        result['uncertainties']=list(dict.fromkeys(result['uncertainties']+repaired['uncertainties']))
        result['multiple_scenarios']=result['multiple_scenarios'] or repaired['multiple_scenarios']
    result['events']=preserved+replacements
    result['events'].sort(key=lambda e:(e['start_ms'],e['end_ms']))
    result=validate_analysis(result,frames,duration,secrets,max_events=120)
    resolutions=_redact_audit(resolutions,secrets)
    check_coverage(original,result['events'],duration,resolutions,'first_pass')
    check_coverage(checked['events'],result['events'],duration,resolutions,'independent_review')
    before=validate_analysis({'events':checked['events'],'uncertainties':[],'multiple_scenarios':False},frames,duration,secrets,max_events=120)['events']
    result['reconciliation_audit']=_redact_audit({'frame_refs':[f['frame_id'] for f in window],'before_events':before,'coverage_resolutions':resolutions},secrets)
    return result


def _repair_window(original,checked,frames,duration,root,cancelled,secrets,missing_refs,boundary=(),context_frames=None):
    """Revisit one bounded gap without accepting omission or copying old semantics."""
    import copy
    from . import ai_skill_service as ai
    from .ui_video_analysis import _bind_whole_evidence,validate_analysis
    from .ui_video_windows import load_assets
    if cancelled():raise ValueError('video_cancelled')
    # Repair one bounded region; global neighbors are read-only context.
    # Expand complete existing observations, never truncate their evidence.
    window=_gap_frames(frames,original+checked['events'],missing_refs)
    window_ids={f['frame_id'] for f in window}
    context_frames=frames if context_frames is None else context_frames
    positions={f['frame_id']:i for i,f in enumerate(context_frames)}
    neighbors={positions[f['frame_id']]+offset for f in window for offset in (-2,-1,1,2) if 0<=positions[f['frame_id']]+offset<len(context_frames)}
    refs=[context_frames[i]['frame_id'] for i in sorted(neighbors) if context_frames[i]['frame_id'] not in window_ids][:16]
    images=[f for f in context_frames if f['frame_id'] in window_ids|set(refs)]
    payload={'phase':'whole-observe','frames':window,'image_frames':images,'duration_ms':duration,
             'window':{'index':1,'total':1,'final':window[-1]==frames[-1]},'review_mode':'full_flow_after_recovery',
             'gap_note':'frames仅包含需纠正锚点的完整局部片段，可能相距较远。只输出这些片段内证据完整的操作，不重写边界前后的业务；平台保留其余原步骤。',
             'context_frame_ids':refs,'candidate_events':[{'frame_refs':[r for r in e['frame_refs'] if r in window_ids]}
                for e in original if set(e['frame_refs'])<=window_ids],
             'coverage_checks':{name:[{'anchor_id':f'{name}:{i}','action':e['action'],'frame_refs':[r for r in e['frame_refs'] if r in window_ids]} for i,e in enumerate(source)
                if e['action'] in ('aiTap','aiInput','aiScroll') and set(e['frame_refs'])<=window_ids and set(e['frame_refs'])&missing_refs]
                for name,source in [('first_pass',original),('independent_review',checked['events'])]},
             'validation_feedback':'coverage_checks分首轮与独立复核两组，每组内部每个交互锚点都需要独立解释；同一解释可同时对应两组中的同一次操作，不要因此重复生成；同来源多个锚点若由一次真实交互重复识别而来，按coverage_resolutions合并并完整列出重复锚点。首轮可能把等待当重复点击，若本段只有自动变化，用aiAssert及完整原帧说明静止事实；不要为一次真实交互额外造aiAssert凑数量。完整性复核发现本段遗漏或合并操作。逐帧区分每一次返回、同页滚动和自动转场；每一次导航独立记录，不将两次返回合成一次。锚点只指示证据位置，不代表预定动作。'}
    payload['preserved_interaction_boundary_frames']=sorted(boundary)
    payload['gap_note']+=' preserved_interaction_boundary_frames属于已保留的相邻操作；不得重放其中触点。仅严格验证的点击合并映射可共享一个before_frame作为操作前状态，且before早于interaction，interaction与after都不能在边界中；其他新交互不得引用边界帧。自动加载观察可引用。不要重写相邻正常操作。'
    payload['validation_feedback']+=' action是待核对的旧识别，不是真实答案。将旧点击改为aiAssert时，必须引用该锚点全部帧，并明确实际是否无新点击而为自动加载/静止变化；确有导航不得如此改写。将旧滚动改为实际点击时，引用原锚点全部帧，明确说明未见滚动、实际点击的控件及所见变化。禁止为满足锚点数量创造操作。'
    payload['coverage_resolution_contract']={
        'purpose':'旧锚点不是操作真值。两条旧锚点若对应同一个触点/交互，不要编造额外点击或aiAssert，提交coverage_resolutions明确映射；真实重复操作必须保留。',
        'schema':{'source_anchor_ids':['first_pass:索引','independent_review:索引'],'resolution':'same_interaction 或 same_interaction_with_action_correction','reviewed_event_index':'本次events数组索引',
            'evidence':{'before_frame':'原帧','interaction_frame':'所有旧锚点共同包含的实际交互原帧','after_frame':'结果原帧','examined_frame_refs':['包含各旧锚点全部原帧，最多8张'],
                'before_state':'操作前页面','interaction':'明确操作及旧动作误判原因','after_state':'操作后页面','why_not_separate_actions':'为何仅同一次操作而非独立重试或多次导航'}},
        'rules':'仅为同一来源内部重复或误判导致的多个旧锚点提交映射；不同来源各一条的正常对应不用声明。结果允许aiTap；连续同方向同参数滚动可用same_interaction，所有旧滚动value必须与结果完全相同，禁止反向/再次手势合并；2至8个锚点映射一个真实事件，共同交互帧必须属于每个锚点和结果事件，结果事件含前/交互/后帧；不能跨其他独立操作、不能合并输入、不能声明不确定。scroll改tap须明确未见独立滚动及实际点击。多个来源中对应同次交互的锚点一并列入。events每项1至8帧，自动加载选代表帧，不罗列所有帧。无映射需求时coverage_resolutions为空数组。'}
    from .ui_video_coverage_resolution import eligible_groups
    payload['eligible_resolution_groups']=[g for g in eligible_groups({'first_pass':original,'independent_review':checked['events']},window_ids) if g['common_frame'] in missing_refs]
    payload['gap_note']+=' coverage_checks只列真正冲突的旧识别，不能为未列出的正常操作编造映射。其余画面仍按原图输出，后端独立检查完整性。'
    before=copy.deepcopy(checked['events'])
    resolutions=[]
    for attempt in range(2):
        if cancelled():raise ValueError('video_cancelled')
        raw=ai.run_ai_skill('ui_video_observe',payload,version='coverage',image_assets=load_assets(images,root,cancelled),
            timeout=120,retry_count=0,max_tokens=5000,repair_invalid_json=False)
        if cancelled():raise ValueError('video_cancelled')
        if isinstance(raw,dict) and isinstance(raw.get('events'),list):
            secrets.extend(e['value'] for e in raw['events'] if isinstance(e,dict) and e.get('action')=='aiInput' and isinstance(e.get('value'),str) and e['value'])
        from .ui_video_coverage_resolution import validate as validate_resolutions
        claims=raw.get('coverage_resolutions',[]) if isinstance(raw,dict) else []
        observation={k:v for k,v in raw.items() if k!='coverage_resolutions'} if isinstance(raw,dict) else raw
        try:
            repaired=validate_analysis(_bind_whole_evidence(observation,window),window,duration,secrets)
        except ValueError as error:
            if attempt or str(error) not in {'video_action_invalid','video_model_invalid','video_reference_invalid'}:raise
            payload['validation_feedback']+=' 上次结构校验未通过。events每项只含规定字段，frame_refs须1至8个本次原帧且按时间排列；滚动只用direction=up/down/left/right及scrollType=singleAction；不输出时间字段或未提供帧。'
            continue
        try:
            resolutions=validate_resolutions(claims,{'first_pass':original,'independent_review':before},repaired['events'],window)
            from .ui_video_coverage_resolution import boundary_safe
            if any(not boundary_safe(e,boundary,resolutions,window) for e in repaired['events']):
                raise ValueError('video_reference_invalid')
        except ValueError:
            if attempt:raise
            payload['invalid_resolution_candidate']=claims
            from .ui_video_coverage_resolution import diagnose
            payload['resolution_diagnostics']=diagnose(claims,{'first_pass':original,'independent_review':before},repaired['events'],window)
            payload['validation_feedback']+=' 上次invalid_resolution_candidate已被拒绝。不要重抄：aiAssert不得作为映射目标，应删除自动加载映射，只保留该aiAssert完整覆盖相应旧帧。每个anchor_id仅能出现在一个映射；before_frame允许与interaction_frame相同，但after_frame必须更晚。映射不符合合同：仅声明同一来源内部至少两条旧锚点合并到一次交互，不声明普通跨来源一对一对应。连续滚动只允许同action同value的same_interaction，不能声明action_correction。examined_frame_refs必须完整包含这些锚点的帧并集，不能遗漏较早帧；interaction_frame必须是实际触点所在帧，不能使用之后转场帧；前/触点/后帧也须在结果事件中。scroll改tap明确写未见独立滚动，理由不能含可能或不确定；仍不能确定就保留疑问，不造证据。'
            continue
        combined=[e for e in checked['events'] if not set(e['frame_refs'])<=window_ids]+repaired['events']
        combined.sort(key=lambda e:(e['start_ms'],e['end_ms']))
        result=validate_analysis({'events':combined,'uncertainties':list(dict.fromkeys(checked['uncertainties']+repaired['uncertainties']))[:30],
            'multiple_scenarios':checked['multiple_scenarios'] or repaired['multiple_scenarios']},frames,duration,secrets,max_events=120)
        try:
            check_coverage(before,result['events'],duration,resolutions,'independent_review')
            check_coverage(original,result['events'],duration,resolutions,'first_pass')
            break
        except ValueError as error:
            missing=getattr(error,'missing_frame_refs',None)
            if attempt or not missing or not missing<=window_ids:raise
            payload['unresolved_hypotheses']=[{'action':e['action'],'target':e['target'],'observation':e['observation'],'frame_refs':e['frame_refs']} for e in original+before if set(e['frame_refs'])<=missing]
            payload['validation_feedback']+=' 上次仍未完整解释这些原帧：'+','.join(sorted(missing))+ '。unresolved_hypotheses是待证伪的旧识别，绝非正确答案。请逐帧核对旧识别是否误判；纠正旧点击/滚动时，完整引用其帧并明确说明实际动作或无新交互的自动状态。若多条旧识别实际为同一次操作，保留一次真实交互，并在coverage_resolutions中完整列入对应锚点，不额外造aiAssert代替重复锚点。仅独立的自动加载阶段可用aiAssert。不要复制旧动作凑数量。'
    before=validate_analysis({'events':before,'uncertainties':[],'multiple_scenarios':False},frames,duration,secrets,max_events=120)['events']
    resolutions=_redact_audit(resolutions,secrets)
    result['reconciliation_audit']=_redact_audit({'frame_refs':[f['frame_id'] for f in window],'before_events':before,'coverage_resolutions':resolutions},secrets)
    return result


def _redact_audit(value,secrets):
    if isinstance(value,str):
        for secret in secrets:
            if secret:value=value.replace(secret,'[输入内容未保存]')
        return value
    if isinstance(value,list):return [_redact_audit(item,secrets) for item in value]
    if isinstance(value,dict):return {k:_redact_audit(v,secrets) for k,v in value.items()}
    return value
