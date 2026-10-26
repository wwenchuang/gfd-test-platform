"""Re-read full-flow evidence after long recovery loops, before case planning."""
import re
from .ui_video_coverage_resolution import has_return_gesture_evidence


def needs_reconciliation(events,frames):
    gesture=any(re.search(r'手势|侧滑|边缘.*返回',e['target']+' '+e['observation']) for e in events)
    return gesture or (len(frames)>60 and any('取消' in e['target'] for e in events) and any(
        re.search(r'取消.*(?:未完成|失败|未成功)',e['observation']) for e in events))


def _tap_is_drag(old,new,eligible_refs):
    if (new['action']!='aiScroll' or '返回' in old['target'] or new.get('uncertainties') or
        not set(old['frame_refs'])<=set(new['frame_refs']) or not set(old['frame_refs'])<=eligible_refs):return False
    note=new['observation']
    from .ui_video_delayed_response import has_drag_conflict
    if has_drag_conflict(note):return False
    if re.search(r'可能|不确定|无法确认|疑似',note):return False
    affirmative='。'.join(c for c in re.split(r'[。；，,;]',note) if not re.search(r'未|没|无|不|并非',c))
    return bool(re.search(r'(?:未见|未观察到|没有)独立点击',note) and
                re.search(r'同一次|连续',affirmative) and
                re.search(r'触点.{0,20}(?:移动|位移|拖动)',affirmative) and
                re.search(r'内容.{0,20}(?:位移|上移|下移|移动)',affirmative))


def check_coverage(original,reviewed,duration=None,resolutions=None,source=None):
    from .ui_video_analysis import _system_boundary
    from .ui_video_coverage_resolution import positions
    mapped=positions(resolutions,source,reviewed) if resolutions else {}
    required=[];claimed=set()
    drag_refs=_tap_scroll_refs(original)
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
                from .ui_video_automatic_motion import has_positive_drag
                if has_positive_drag(new['observation']):continue
                # An explicit, full-evidence correction can explain a false
                # scroll caused by asynchronous layout; a generic wait cannot.
                if not (new['action'] in {'aiAssert','aiTap'} and set(old['frame_refs'])<=set(new['frame_refs'])
                    and re.search(r'(?:未观察到|未见|未发生|没有观察到|没有发生|没有|并非|而非|不是|(?<=[，。；])非)(?:任何|实际的|实际|用户|手指|触点|页面|内容|新的|独立的|独立|发起的|发起了|本次|进行|发生){0,4}(?:滚动|滑动|拖动)',new['observation'])
                    and re.search(r'自动|加载|布局|轮播' if new['action']=='aiAssert' else r'实际点击|触点.*按钮',new['observation'])):continue
            if old['action']=='aiTap' and new['action']!='aiTap':
                from .ui_video_delayed_response import has_positive_tap
                if new['action']=='aiAssert' and has_positive_tap(new['observation']):continue
                # A return/navigation is still an interaction even if the
                # physical gesture is not directly executable as a tap.
                if not (_tap_is_drag(old,new,drag_refs) or (new['action']=='aiAssert' and set(old['frame_refs'])<=set(new['frame_refs']) and
                    re.search(r'未(?:见|观察到|发生)|没有',new['observation']) and
                    re.search(r'自动|静止|保持不变',new['observation']) and
                    not re.search(r'用户.*(?:执行|触发).*返回|完成.*导航|从.*返回',new['observation']))):continue
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
            # The repair audit retains old/new actions. Do not turn a verified
            # drag correction into a model uncertainty before subsequent checks.
            if reviewed[position]['action']=='aiScroll':continue
            kind='点击' if required[index]['action']=='aiTap' else '滚动'
            corrected={'aiTap':'点击','aiScroll':'连续拖动'}.get(reviewed[position]['action'],'自动状态变化')
            warning=f'首轮{kind}已复核为{corrected}，请核对该段视频'
            if warning not in reviewed[position]['uncertainties']:reviewed[position]['uncertainties'].append(warning)


def _tap_scroll_refs(events):
    """A shared tap/drag boundary requests pixels; it never removes a tap."""
    interactions=[e for e in events if e['action'] in {'aiTap','aiInput','aiScroll'}]
    refs=set()
    for first,second in zip(interactions,interactions[1:]):
        if (first['action']=='aiTap' and second['action']=='aiScroll' and
            first['start_ms']<=second['start_ms']<=first['end_ms'] and
            set(first['frame_refs'])&set(second['frame_refs'])):
            refs.update(first['frame_refs']);refs.update(second['frame_refs'])
    return refs


def _overlapping_gesture_refs(events):
    """Flag ambiguous window overlaps for pixel review, never merge by wording."""
    refs=set()
    interactions=[e for e in events if e['action'] in {'aiTap','aiInput','aiScroll'}]
    for first,second in zip(interactions,interactions[1:]):
        if (first['action']==second['action']=='aiTap' and first['target']==second['target'] and
            first['start_ms']<=second['start_ms']<first['end_ms'] and
            set(first['frame_refs'])&set(second['frame_refs']) and
            '返回' in first['target'] and
            all(has_return_gesture_evidence(e['observation']) for e in (first,second))):
            refs.update(first['frame_refs']);refs.update(second['frame_refs'])
    return refs


def _overlapping_tap_refs(events):
    """Request pixel review for duplicate hypotheses, never deduplicate actions."""
    refs=set()
    interactions=[e for e in events if e['action'] in {'aiTap','aiInput','aiScroll'}]
    for first,second in zip(interactions,interactions[1:]):
        if (first['action']==second['action']=='aiTap' and first['target']==second['target'] and
            first.get('value','')==second.get('value','') and
            first['start_ms']<second['start_ms']<first['end_ms'] and
            len(set(first['frame_refs'])&set(second['frame_refs']))>=2):
            refs.update(first['frame_refs']);refs.update(second['frame_refs'])
    return refs


def reconcile(events,frames,duration,root,cancelled,secrets,progress=None):
    from .ui_video_windows import observe_windows
    # Revisit all decoded pixels. Resampling a long film back to sixty images
    # would lose the before/after states this pass is intended to recover.
    checked,new_secrets=observe_windows(frames,duration,root,cancelled,progress,
                                       review_events=events,prior_secrets=secrets)
    secrets.extend(new_secrets)
    from .ui_video_automatic_motion import review_refs,disputed_scroll_refs
    repair_refs=review_refs(events) | review_refs(checked['events']) | _overlapping_gesture_refs(checked['events']) | _tap_scroll_refs(events) | _tap_scroll_refs(checked['events'])
    repair_refs.update(_overlapping_tap_refs(events) | _overlapping_tap_refs(checked['events']))
    repair_refs.update(disputed_scroll_refs(events,checked['events']))
    from .ui_video_delayed_response import review_refs as delayed_refs
    repair_refs.update(delayed_refs(events,checked['events']))
    try:
        check_coverage(events,checked['events'],duration)
    except ValueError as error:
        if not getattr(error,'missing_frame_refs',None):raise
        repair_refs.update(error.missing_frame_refs)
    if repair_refs:
        checked=_repair_coverage(events,checked,frames,duration,root,cancelled,secrets,repair_refs)
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
    # A loading observation can span many seconds and share its final frame
    # with a tap. Preserve that observation outside the replacement instead
    # of pulling its entire static interval into the interaction repair.
    affected=[e for e in original+checked['events']
              if e['action'] in {'aiTap','aiInput','aiScroll'} and set(e['frame_refs'])&missing_refs]
    positions={f['frame_id']:i for i,f in enumerate(frames)}
    selected={positions[r] for r in missing_refs}
    for event in affected:
        indexes=[positions[r] for r in event['frame_refs']]
        selected.update(range(min(indexes),max(indexes)+1))
    # Include same-action/target overlapping evidence from both passes. A
    # partial duplicate outside the gap competes with its repaired counterpart
    # when the windows are joined, despite each local coverage check passing.
    while True:
        expanded=set(selected)
        for event in original+checked['events']:
            indexes=[positions[r] for r in event['frame_refs']]
            related=any(other['action']==event['action'] and other['target']==event['target']
                        and set(other['frame_refs'])&set(event['frame_refs'])
                        and all(positions[r] in selected for r in other['frame_refs'])
                        for other in original+checked['events'])
            # A second pass can name the same scroll region differently and
            # shift its sampled endpoints. Review both complete spans; this
            # only expands evidence, never merges actions or accepts direction.
            counterpart=checked['events'] if any(event is e for e in original) else original
            scroll_overlap=event['action']=='aiScroll' and any(
                other['action']=='aiScroll' and other['value']==event['value'] and
                max(other['start_ms'],event['start_ms'])<min(other['end_ms'],event['end_ms']) and
                all(positions[r] in selected for r in other['frame_refs'])
                for other in counterpart)
            if event['action'] in {'aiTap','aiInput','aiScroll'} and selected.intersection(indexes) and (related or scroll_overlap):
                expanded.update(range(min(indexes),max(indexes)+1))
        if len(expanded)>40:raise ValueError('video_required_action_missing')
        if expanded==selected:break
        selected=expanded
    window=[f for i,f in enumerate(frames) if i in selected]
    groups=[]
    for frame in window:
        if not groups or positions[frame['frame_id']]!=positions[groups[-1][-1]['frame_id']]+1:groups.append([])
        groups[-1].append(frame)
    if len(groups)>4:raise ValueError('video_required_action_missing')
    result=copy.deepcopy(checked);resolutions=[];replacements=[]
    all_ids={f['frame_id'] for f in window}
    replaced={i for i,e in enumerate(checked['events']) if set(e['frame_refs'])<=all_ids}
    preserved=[e for i,e in enumerate(checked['events']) if i not in replaced]
    boundary={r for e in preserved if e['action'] in {'aiTap','aiInput','aiScroll'} for r in e['frame_refs']}
    all_ids={f['frame_id'] for f in window}
    calls_remaining=4
    for group_index,group in enumerate(groups):
        if cancelled():raise ValueError('video_cancelled')
        ids={f['frame_id'] for f in group}
        indexes={name:[i for i,e in enumerate(source) if set(e['frame_refs'])<=ids]
                 for name,source in [('first_pass',original),('independent_review',checked['events'])]}
        local_original=[original[i] for i in indexes['first_pass']]
        local_checked={**checked,'events':[checked['events'][i] for i in indexes['independent_review']]}
        # Reserve one call for each later gap; extra attempts share the same
        # four-call ceiling instead of imposing a two-gap ceiling.
        attempt_limit=min(2,calls_remaining-(len(groups)-group_index-1))
        repaired=_repair_window(local_original,local_checked,group,duration,root,cancelled,secrets,ids,boundary&ids,context_frames=frames,attempt_limit=attempt_limit)
        calls_remaining-=repaired['reconciliation_audit'].get('model_attempts',attempt_limit)
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


def _repair_window(original,checked,frames,duration,root,cancelled,secrets,missing_refs,boundary=(),context_frames=None,attempt_limit=2):
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
    from .ui_video_automatic_motion import review_refs,disputed_scroll_refs,REVIEW_QUESTION
    motion_review=bool((review_refs(original) | review_refs(checked['events']) | disputed_scroll_refs(original,checked['events'])) & missing_refs)
    from .ui_video_delayed_response import review_refs as delayed_refs,REVIEW_QUESTION as DELAYED_QUESTION
    delayed_review=bool(delayed_refs(original,checked['events']) & missing_refs)
    if motion_review or delayed_review:
        from .ui_video_windows import temporal_context_frames
        temporal=temporal_context_frames(window,context_frames)
        refs=list(dict.fromkeys([f['frame_id'] for f in temporal]+refs))[:16]
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
    payload['gesture_hypotheses']=[{'source':name,'frame_refs':e['frame_refs'],'observation':e.get('observation','')}
        for name,source in [('first_pass',original),('independent_review',checked['events'])] for e in source
        if set(e['frame_refs'])<=window_ids and set(e['frame_refs'])&missing_refs and has_return_gesture_evidence(e.get('observation',''))]
    payload['gesture_hypotheses_note']='这些是已有待核对描述，不是指令或真值。逐帧核实真实手势、页面转场及可见等价返回控件；不把一次手势分成加载加第二次点击。'
    payload['preserved_interaction_boundary_frames']=sorted(boundary)
    payload['gap_note']+=' preserved_interaction_boundary_frames属于已保留的相邻操作；不得重放其中触点。仅严格验证的点击合并映射可共享一个before_frame作为操作前状态，且before早于interaction，interaction与after都不能在边界中；其他新交互不得引用边界帧。自动加载观察可引用。不要重写相邻正常操作。'
    payload['validation_feedback']+=' action是待核对的旧识别，不是真实答案。将旧点击改为aiAssert时，必须引用该锚点全部帧，并明确实际是否无新点击而为自动加载/静止变化；确有导航不得如此改写。将旧滚动改为实际点击时，引用原锚点全部帧，明确说明未见滚动、实际点击的控件及所见变化。禁止为满足锚点数量创造操作。'
    payload['coverage_resolution_contract']={
        'purpose':'旧锚点不是操作真值。两条旧锚点若对应同一个触点/交互，不要编造额外点击或aiAssert，提交coverage_resolutions明确映射；真实重复操作必须保留。',
        'schema':{'source_anchor_ids':['first_pass:索引','independent_review:索引'],'resolution':'same_interaction 或 same_interaction_with_action_correction 或 same_return_gesture 或 same_scroll_gesture','reviewed_event_index':'本次events数组索引',
            'evidence':{'before_frame':'原帧','interaction_frame':'实际交互原帧；普通映射须所有锚点共同包含，same_return_gesture例外见规则','after_frame':'结果原帧','examined_frame_refs':['包含各旧锚点全部原帧，最多8张'],
                'before_state':'操作前页面','interaction':'明确操作及旧动作误判原因','after_state':'操作后页面','why_not_separate_actions':'为何仅同一次操作而非独立重试或多次导航'}},
        'rules':'仅为同一来源内部重复或误判导致的多个旧锚点提交映射；不同来源各一条的正常对应不用声明。结果允许aiTap；连续同方向同参数滚动可用same_interaction，所有旧滚动value必须与结果完全相同，禁止反向/再次手势合并；2至8个锚点映射一个真实事件，共同交互帧必须属于每个锚点和结果事件，结果事件含前/交互/后帧；不能跨其他独立操作、不能合并输入、不能声明不确定。scroll改tap须明确未见独立滚动及实际点击。多个来源中对应同次交互的锚点一并列入。events每项1至8帧，自动加载选代表帧，不罗列所有帧。无映射需求时coverage_resolutions为空数组。'}
    payload['coverage_resolution_contract']['rules']+=' 唯一例外same_return_gesture：仅相同可见返回箭头目标的aiTap重复假设，完整原帧证实同一次连续返回手势；实际interaction_frame须属于至少一条旧锚点及结果，且不晚于任何旧锚点结束。后窗只含同次手势后半段时不伪造新的触点：所有锚点时间须与所引原帧一致，并在实际触点之后、结果之前共享至少一张中间原帧，该帧也须保留在结果事件中。不得用共享动画帧冒充实际手势帧。before_state明确返回箭头/按钮可见，interaction明确同一次返回手势，why_not_separate_actions明确未见第二次按下/手势/导航。无独立操作夹入，各旧帧完整审查，不合并不同目标、再次返回或输入。'
    payload['validation_feedback']+=' 相邻旧点击和滚动也可能实际是两次滚动，不能强行合并。旧点击若为第一段连续拖动，可一对一改为aiScroll，不提交映射，但结果必须引用该旧点击的全部原帧，observation明确未见独立点击、触点连续移动、内容随之位移；保留之后新的独立滚动。真实点击无响应仍须保留。只有多个旧锚点确属一次手势才使用same_scroll_gesture；普通一对一改型coverage_resolutions为空。'
    payload['coverage_resolution_contract']['rules']+=' same_scroll_gesture仅用于相邻点击和滚动原来属于一次连续拖动：完整引用两个锚点全部原帧，共同交互帧真实存在，interaction明确触点持续移动且内容随之位移；why_not_separate_actions明确未见独立点击、未见松开后重新按下。结果只为aiScroll，除由content_motion_direction原图证据纠正的方向外，既有滚动参数必须保持一致；所有旧原帧须在examined_frame_refs中完整审查，结果frame_refs只保留本次交互的前/中/后证据，不强塞下一操作的起始上下文。若确实先点击再滚动，保留两项，不提交映射；真实无响应点击也必须保留。空心圆跨控件移动是触点，不因起点在按钮旁就当点击或加载图标。证据不清保留疑问。'
    payload['coverage_resolution_contract']['gesture_facts_for_same_scroll_gesture']={'content_motion_direction':'原帧内容移动方向up/down/left/right，与结果浏览direction相反；可纠正旧误判方向，其它参数保持','interaction_count':'实际独立交互次数整数','independent_click_observed':'存在独立点击的布尔事实','continuous_drag_observed':'原帧证明持续拖动的布尔事实','release_then_press_observed':'松开后再次按下的布尔事实'}
    from .ui_video_coverage_resolution import eligible_groups
    payload['eligible_resolution_groups']=[g for g in eligible_groups({'first_pass':original,'independent_review':checked['events']},window_ids) if g['common_frame'] in missing_refs]
    payload['gap_note']+=' coverage_checks只列真正冲突的旧识别，不能为未列出的正常操作编造映射。其余画面仍按原图输出，后端独立检查完整性。'
    repair_version='coverage'
    # Scroll-only duplicates are legal but not enumerated by eligible_groups.
    # Keep their existing mapping-capable path rather than forbidding recovery.
    has_scroll=any(sum(e['action']=='aiScroll' for e in source)>=2
                   for source in (original,checked['events']))
    if not payload['eligible_resolution_groups'] and not has_scroll:
        repair_version='coverage-observe'
        payload.pop('coverage_resolution_contract')
        payload['gap_note']='逐帧重新核对本窗口的旧识别。没有合法的同次交互合并候选；coverage_resolutions必须为空。context_frame_ids仅作上下文，不属于可引用证据；不得重放preserved_interaction_boundary_frames中的相邻操作。'
        payload['validation_feedback']='旧锚点不是真值。真实点击、滚动和输入须保留；旧点击若只是前序动作后的自动转场，引用旧锚点全部帧并明确未见新点击以及实际自动变化。不得用自动状态代替真实导航。连续输入分段观察，保留各段证据，不提交输入合并声明。旧滚动若为自动加载或静止事实，完整引用旧帧并明确未见滚动的依据。'
    if motion_review:
        payload['motion_review_question']=REVIEW_QUESTION
        # Re-observe pixels without priming the answer with the disputed action.
        # Original actions stay in backend coverage checks and the audit.
        payload['coverage_checks']={name:[{'anchor_id':a['anchor_id'],'frame_refs':a['frame_refs']} for a in anchors]
                                    for name,anchors in payload['coverage_checks'].items()}
        payload['gesture_hypotheses']=[]
        payload['validation_feedback']=REVIEW_QUESTION+' 不使用旧动作名称作为答案；先根据头部高度的变化与列表相对位置，区分布局收起和独立用户拖动。不能仅以内容上移符合滑动特征来推断用户操作。'

    if delayed_review:
        payload['delayed_response_question']=DELAYED_QUESTION
        payload['coverage_checks']={name:[{'anchor_id':a['anchor_id'],'frame_refs':a['frame_refs']} for a in anchors] for name,anchors in payload['coverage_checks'].items()}
        payload['gesture_hypotheses']=[]
        payload['validation_feedback']=(REVIEW_QUESTION if motion_review else '')+DELAYED_QUESTION
    # Reuse bounded same-frame detail during conflict review as in initial observation.
    # Crops aid perception only; validate_analysis still accepts original frame IDs only.
    assets=load_assets(images,root,cancelled)
    from .ui_video_touch_detail import append_details
    payload['detail_frames']=append_details(images,root,assets,cancelled,window_ids)
    before=copy.deepcopy(checked['events'])
    resolutions=[]
    for attempt in range(attempt_limit):
        if cancelled():raise ValueError('video_cancelled')
        try:
            raw=ai.run_ai_skill('ui_video_observe',payload,version=repair_version,image_assets=assets,
                timeout=120,retry_count=0,max_tokens=5000,repair_invalid_json=False)
        except ai.SkillSchemaError as error:
            if cancelled():raise ValueError('video_cancelled') from None
            if attempt+1>=attempt_limit:raise
            # Consume the existing local/global repair budget; do not create
            # another retry layer or turn malformed output into empty events.
            payload['validation_feedback']+=' '+error.model_feedback+' 核对同一组原帧，完整输出规定字段；不补造操作。'
            continue
        if cancelled():raise ValueError('video_cancelled')
        if isinstance(raw,dict) and isinstance(raw.get('events'),list):
            secrets.extend(e['value'] for e in raw['events'] if isinstance(e,dict) and e.get('action')=='aiInput' and isinstance(e.get('value'),str) and e['value'])
        from .ui_video_coverage_resolution import validate as validate_resolutions
        claims=raw.get('coverage_resolutions',[]) if isinstance(raw,dict) else []
        observation={k:v for k,v in raw.items() if k!='coverage_resolutions'} if isinstance(raw,dict) else raw
        try:
            repaired=validate_analysis(_bind_whole_evidence(observation,window),window,duration,secrets)
        except ValueError as error:
            if attempt+1>=attempt_limit or str(error) not in {'video_action_invalid','video_model_invalid','video_reference_invalid'}:raise
            payload['validation_feedback']+=' 上次结构校验未通过。events每项只含规定字段，frame_refs须1至8个本次原帧且按时间排列；滚动只用direction=up/down/left/right及scrollType=singleAction；不输出时间字段或未提供帧。'
            continue
        try:
            resolutions=validate_resolutions(claims,{'first_pass':original,'independent_review':before},repaired['events'],window)
            from .ui_video_coverage_resolution import boundary_safe
            if any(not boundary_safe(e,boundary,resolutions,window) for e in repaired['events']):
                raise ValueError('video_reference_invalid')
        except ValueError:
            if attempt+1>=attempt_limit:raise
            # If the observed events already cover both sources one-to-one,
            # a merge declaration is unnecessary. Ask for a fresh observation
            # response; never accept or silently strip an invalid declaration.
            from .ui_video_coverage_resolution import boundary_safe
            try:
                if _overlapping_gesture_refs(before) or _tap_scroll_refs(before):
                    raise ValueError('video_required_action_missing')
                check_coverage(original,repaired['events'],duration)
                check_coverage(before,repaired['events'],duration)
                if any(not boundary_safe(e,boundary,[],window) for e in repaired['events']):
                    raise ValueError('video_reference_invalid')
            except ValueError:
                pass
            else:
                repair_version='coverage-observe'
                payload.pop('coverage_resolution_contract',None)
                payload.pop('eligible_resolution_groups',None)
                payload['gap_note']='本轮已能独立解释旧锚点，不需要合并声明；重新核对原帧，coverage_resolutions必须为空。上下文只作参考，不得重放相邻已保留交互。'
                payload['validation_feedback']='上次事件已分别解释旧锚点，但附加合并声明无效。只按原画面重新输出实际点击及自动状态，完整保留证据，不把自动加载映射为点击；coverage_resolutions必须为空。'
                continue
            if repair_version=='coverage-observe':
                payload['validation_feedback']+=' 上次声明无效。本窗coverage_resolutions必须为空，不提交合并声明；重新观察原帧保留真实操作，自动转场只作状态说明。'
                continue
            payload['validation_feedback']+=' 返回手势映射需逐项核对：before_state写明返回箭头可见（无可见控件则不能映射）；interaction只陈述实际单次手势，不将旧假设的多次点击混入实际动作；why_not_separate_actions明确是否存在第二次按下或导航。仅原帧支持时给出肯定描述，否则保留疑问。'
            payload['invalid_resolution_candidate']=claims
            from .ui_video_coverage_resolution import diagnose
            payload['resolution_diagnostics']=diagnose(claims,{'first_pass':original,'independent_review':before},repaired['events'],window)
            payload['validation_feedback']+=' interaction_frame_not_shared表示交互帧未同时属于全部旧锚点。shared_interaction_frame_candidates只列共同原帧候选，须重新看图确认真实触点；持续按压可选共同的后续按压帧，不必固定第一次按下帧。结果frame_refs必须包含该交互帧；只有共同结果而无真实交互证据时不能合并。'
            payload['validation_feedback']+=' 上次invalid_resolution_candidate已被拒绝。不要重抄：aiAssert不得作为映射目标，应删除自动加载映射，只保留该aiAssert完整覆盖相应旧帧。每个anchor_id仅能出现在一个映射；before_frame允许与interaction_frame相同，但after_frame必须更晚。映射不符合合同：仅声明同一来源内部至少两条旧锚点合并到一次交互，不声明普通跨来源一对一对应。同action同value连续滚动用same_interaction；旧tap加scroll确为一次拖动则仅用same_scroll_gesture，不能声明action_correction，examined_frame_refs必须覆盖全部旧帧，结果保留真实前态、交互与后态帧。examined_frame_refs必须完整包含这些锚点的帧并集，不能遗漏较早帧；interaction_frame必须是实际触点所在帧，不能使用之后转场帧；前/触点/后帧也须在结果事件中。scroll改tap明确写未见独立滚动，理由不能含可能或不确定；仍不能确定就保留疑问，不造证据。'
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
            if attempt+1>=attempt_limit or not missing or not missing<=window_ids:raise
            payload['unresolved_hypotheses']=[{'action':e['action'],'target':e['target'],'observation':e['observation'],'frame_refs':e['frame_refs']} for e in original+before if set(e['frame_refs'])<=missing]
            if repair_version=='coverage-observe':
                payload['validation_feedback']+=' 上次仍未解释这些原帧：'+','.join(sorted(missing))+'。coverage_resolutions必须为空；根据原画面保留真实操作，若旧点击实际为自动转场则完整引用旧帧并说明未见新点击，不复制旧假设凑步骤。'
                continue
            payload['validation_feedback']+=' 上次仍未完整解释这些原帧：'+','.join(sorted(missing))+ '。unresolved_hypotheses是待证伪的旧识别，绝非正确答案。请逐帧核对旧识别是否误判；纠正旧点击/滚动时，完整引用其帧并明确说明实际动作或无新交互的自动状态。若多条旧识别实际为同一次操作，保留一次真实交互，并在coverage_resolutions中完整列入对应锚点，不额外造aiAssert代替重复锚点。仅独立的自动加载阶段可用aiAssert。不要复制旧动作凑数量。'
    before=validate_analysis({'events':before,'uncertainties':[],'multiple_scenarios':False},frames,duration,secrets,max_events=120)['events']
    resolutions=_redact_audit(resolutions,secrets)
    result['reconciliation_audit']=_redact_audit({'frame_refs':[f['frame_id'] for f in window],'before_events':before,'coverage_resolutions':resolutions,'model_attempts':attempt+1},secrets)
    return result


def _redact_audit(value,secrets):
    if isinstance(value,str):
        for secret in secrets:
            if secret:value=value.replace(secret,'[输入内容未保存]')
        return value
    if isinstance(value,list):return [_redact_audit(item,secrets) for item in value]
    if isinstance(value,dict):return {k:_redact_audit(v,secrets) for k,v in value.items()}
    return value
