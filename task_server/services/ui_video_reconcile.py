"""Re-read full-flow evidence after long recovery loops, before case planning."""
import re


def needs_reconciliation(events,frames):
    gesture=any(re.search(r'手势|侧滑|边缘.*返回',e['target']+' '+e['observation']) for e in events)
    return gesture or (len(frames)>60 and any('取消' in e['target'] for e in events) and any(
        re.search(r'取消.*(?:未完成|失败|未成功)',e['observation']) for e in events))


def check_coverage(original,reviewed,duration=None):
    from .ui_video_analysis import _system_boundary
    required=[event for event in original if event['action'] in ('aiTap','aiInput','aiScroll')
              and not (duration is not None and _system_boundary(event,duration))]
    # aiInput already focuses its field. Only absorb an explicit focus tap when
    # the immediately following interaction is input into that exact same field,
    # and one reviewed input covers both original time ranges and evidence.
    absorbed=set()
    for i,(first,second) in enumerate(zip(required,required[1:])):
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
            if position in seen or not set(old['frame_refs']) & set(new['frame_refs']):continue
            if old['action']=='aiInput' and new['action']!='aiInput':continue
            if old['action']=='aiScroll' and new['action']!='aiScroll':
                # An explicit, full-evidence correction can explain a false
                # scroll caused by asynchronous layout; a generic wait cannot.
                if not (new['action'] in {'aiAssert','aiTap'} and set(old['frame_refs'])<=set(new['frame_refs'])
                    and re.search(r'(?:未观察到|未见|未发生|没有观察到|没有发生|没有|并非|不是)(?:任何|实际的|实际|用户|手指|触点|页面|内容|新的|本次|进行|发生){0,3}(?:滚动|滑动)',new['observation'])
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
            reviewed[position]['uncertainties'].append(f'首轮{kind}已复核为{corrected}，请核对该段视频')


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
        check_coverage(events,checked['events'],duration)
    warnings=[q for e in checked['events'] for q in e['uncertainties'] if q.startswith(('首轮点击','首轮滚动'))]
    checked['uncertainties']=list(dict.fromkeys(checked['uncertainties']+warnings))
    return checked


def _gap_frames(frames,events,missing_refs):
    positions={f['frame_id']:i for i,f in enumerate(frames)}
    selected={i for ref in missing_refs for i in range(max(0,positions[ref]-2),min(len(frames),positions[ref]+3))}
    while True:
        expanded=set(selected)
        for event in events:
            indexes=[positions[r] for r in event['frame_refs']]
            if selected.intersection(indexes):
                expanded.update(range(min(indexes),max(indexes)+1))
        if len(expanded)>40:
            raise ValueError('video_required_action_missing')
        if expanded==selected:
            return [f for i,f in enumerate(frames) if i in selected]
        selected=expanded


def _repair_coverage(original,checked,frames,duration,root,cancelled,secrets,missing_refs):
    """Revisit one bounded gap without accepting omission or copying old semantics."""
    import copy
    from . import ai_skill_service as ai
    from .ui_video_analysis import _bind_whole_evidence,validate_analysis
    from .ui_video_windows import load_assets,context_frames
    if cancelled():raise ValueError('video_cancelled')
    # Multiple distant gaps share 40 local images and one call; at most 16
    # reference images keep the complete transport below its 60-image budget.
    # Expand complete existing observations, never truncate their evidence.
    window=_gap_frames(frames,checked['events'],missing_refs)
    window_ids={f['frame_id'] for f in window}
    refs=[f['frame_id'] for f in context_frames(window,frames,root,original,cancelled)]
    images=[f for f in frames if f['frame_id'] in window_ids|set(refs)]
    payload={'phase':'whole-observe','frames':window,'image_frames':images,'duration_ms':duration,
             'window':{'index':1,'total':1,'final':window[-1]==frames[-1]},'review_mode':'full_flow_after_recovery',
             'gap_note':'frames可能包含相距较远的多个局部片段，按真实时间和帧号分别核对，不补写间隔中的业务；间隔部分由平台保留原步骤。',
             'context_frame_ids':refs,'candidate_events':[{'frame_refs':[r for r in e['frame_refs'] if r in window_ids]}
                for e in original if set(e['frame_refs'])&window_ids],
             'coverage_checks':{name:[{'action':e['action'],'frame_refs':[r for r in e['frame_refs'] if r in window_ids]} for e in source
                if e['action'] in ('aiTap','aiInput','aiScroll') and set(e['frame_refs'])&window_ids]
                for name,source in [('first_pass',original),('independent_review',checked['events'])]},
             'validation_feedback':'coverage_checks分首轮与独立复核两组，每组内部每个交互锚点都需要独立解释；同一解释可同时对应两组中的同一次操作，不要因此重复生成；首轮可能把等待当重复点击，若本段没有该次操作，用独立aiAssert及对应帧说明自动变化/静止事实，不复制虚假点击，也不能让一条解释同时代替两个锚点。完整性复核发现本段遗漏或合并操作。逐帧区分每一次返回、同页滚动和自动转场；每一次导航独立记录，不将两次返回合成一次。锚点只指示证据位置，不代表预定动作。'}
    payload['validation_feedback']+=' action是待核对的旧识别，不是真实答案。将旧点击改为aiAssert时，必须引用该锚点全部帧，并明确实际是否无新点击而为自动加载/静止变化；确有导航不得如此改写。将旧滚动改为实际点击时，引用原锚点全部帧，明确说明未见滚动、实际点击的控件及所见变化。禁止为满足锚点数量创造操作。'
    before=copy.deepcopy(checked['events'])
    for attempt in range(2):
        if cancelled():raise ValueError('video_cancelled')
        raw=ai.run_ai_skill('ui_video_observe',payload,image_assets=load_assets(images,root,cancelled),
            timeout=120,retry_count=0,max_tokens=5000,repair_invalid_json=False)
        if cancelled():raise ValueError('video_cancelled')
        if isinstance(raw,dict) and isinstance(raw.get('events'),list):
            secrets.extend(e['value'] for e in raw['events'] if isinstance(e,dict) and e.get('action')=='aiInput' and isinstance(e.get('value'),str) and e['value'])
        repaired=validate_analysis(_bind_whole_evidence(raw,window),window,duration,secrets)
        combined=[e for e in checked['events'] if not set(e['frame_refs'])&window_ids]+repaired['events']
        combined.sort(key=lambda e:(e['start_ms'],e['end_ms']))
        result=validate_analysis({'events':combined,'uncertainties':list(dict.fromkeys(checked['uncertainties']+repaired['uncertainties']))[:30],
            'multiple_scenarios':checked['multiple_scenarios'] or repaired['multiple_scenarios']},frames,duration,secrets,max_events=120)
        try:
            check_coverage(before,result['events'],duration)
            check_coverage(original,result['events'],duration)
            break
        except ValueError as error:
            missing=getattr(error,'missing_frame_refs',None)
            if attempt or not missing or not missing<=window_ids:raise
            payload['unresolved_hypotheses']=[{'action':e['action'],'target':e['target'],'observation':e['observation'],'frame_refs':e['frame_refs']} for e in original+before if set(e['frame_refs'])<=missing]
            payload['validation_feedback']+=' 上次仍未完整解释这些原帧：'+','.join(sorted(missing))+ '。unresolved_hypotheses是待证伪的旧识别，绝非正确答案。请逐帧核对旧识别是否误判；纠正旧点击/滚动时，完整引用其帧并明确说明实际动作或无新交互的自动状态。若两条旧识别实际为同一次操作，保留一次真实交互，另一锚点用aiAssert完整引用原帧，明确未见新增点击/滚动、只见同一次操作的自动转场或静止结果。不要复制旧动作凑数量。'
    before=validate_analysis({'events':before,'uncertainties':[],'multiple_scenarios':False},frames,duration,secrets,max_events=120)['events']
    result['reconciliation_audit']={'frame_refs':[f['frame_id'] for f in window],'before_events':before}
    return result
