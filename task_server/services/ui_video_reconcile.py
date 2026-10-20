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
                if not (new['action']=='aiAssert' and set(old['frame_refs'])<=set(new['frame_refs'])
                    and re.search(r'(?:未观察到|未见|未发生|没有观察到|没有发生|没有|并非|不是)(?:任何|实际的|实际|用户|手指|触点|页面|内容|新的|本次|进行|发生){0,3}(?:滚动|滑动)',new['observation'])
                    and re.search(r'自动|加载|布局|轮播',new['observation'])):continue
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
            reviewed[position]['uncertainties'].append(f'首轮{kind}已复核为自动状态变化，请核对该段视频')


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


def _repair_coverage(original,checked,frames,duration,root,cancelled,secrets,missing_refs):
    """Revisit one bounded gap without accepting omission or copying old semantics."""
    import copy
    from . import ai_skill_service as ai
    from .ui_video_analysis import _bind_whole_evidence,validate_analysis
    from .ui_video_windows import load_assets,context_frames
    if cancelled():raise ValueError('video_cancelled')
    positions={f['frame_id']:i for i,f in enumerate(frames)}
    indexes=[positions[ref] for ref in missing_refs]
    lo=max(0,min(indexes)-2);hi=min(len(frames)-1,max(indexes)+2)
    # Never replace half an observation at a repair boundary.
    while True:
        touched=[e for e in checked['events'] if any(lo<=positions[r]<=hi for r in e['frame_refs'])]
        expanded=[positions[r] for e in touched for r in e['frame_refs']]+[lo,hi]
        new_lo,new_hi=min(expanded),max(expanded)
        if (new_lo,new_hi)==(lo,hi):break
        lo,hi=new_lo,new_hi
    if hi-lo+1>30:raise ValueError('video_required_action_missing')
    window=frames[lo:hi+1];window_ids={f['frame_id'] for f in window}
    refs=[f['frame_id'] for f in context_frames(window,frames,root,original,cancelled)]
    images=[f for f in frames if f['frame_id'] in window_ids|set(refs)]
    payload={'phase':'whole-observe','frames':window,'image_frames':images,'duration_ms':duration,
             'window':{'index':1,'total':1,'final':hi==len(frames)-1},'review_mode':'full_flow_after_recovery',
             'context_frame_ids':refs,'candidate_events':[{'frame_refs':[r for r in e['frame_refs'] if r in window_ids]}
                for e in original if set(e['frame_refs'])&window_ids],
             'coverage_checks':{name:[{'frame_refs':[r for r in e['frame_refs'] if r in window_ids]} for e in source
                if e['action'] in ('aiTap','aiInput','aiScroll') and set(e['frame_refs'])&window_ids]
                for name,source in [('first_pass',original),('independent_review',checked['events'])]},
             'validation_feedback':'coverage_checks分首轮与独立复核两组，每组内部每个交互锚点都需要独立解释；同一解释可同时对应两组中的同一次操作，不要因此重复生成；首轮可能把等待当重复点击，若本段没有该次操作，用独立aiAssert及对应帧说明自动变化/静止事实，不复制虚假点击，也不能让一条解释同时代替两个锚点。完整性复核发现本段遗漏或合并操作。逐帧区分每一次返回、同页滚动和自动转场；每一次导航独立记录，不将两次返回合成一次。锚点只指示证据位置，不代表预定动作。'}
    raw=ai.run_ai_skill('ui_video_observe',payload,image_assets=load_assets(images,root,cancelled),
        timeout=120,retry_count=0,max_tokens=5000,repair_invalid_json=False)
    if cancelled():raise ValueError('video_cancelled')
    if isinstance(raw,dict) and isinstance(raw.get('events'),list):
        secrets.extend(e['value'] for e in raw['events'] if isinstance(e,dict) and e.get('action')=='aiInput' and isinstance(e.get('value'),str) and e['value'])
    repaired=validate_analysis(_bind_whole_evidence(raw,window),window,duration,secrets)
    before=copy.deepcopy(checked['events'])
    combined=[e for e in checked['events'] if not set(e['frame_refs'])&window_ids]+repaired['events']
    combined.sort(key=lambda e:(e['start_ms'],e['end_ms']))
    result=validate_analysis({'events':combined,'uncertainties':list(dict.fromkeys(checked['uncertainties']+repaired['uncertainties']))[:30],
        'multiple_scenarios':checked['multiple_scenarios'] or repaired['multiple_scenarios']},frames,duration,secrets,max_events=120)
    check_coverage(before,result['events'],duration)
    before=validate_analysis({'events':before,'uncertainties':[],'multiple_scenarios':False},frames,duration,secrets,max_events=120)['events']
    result['reconciliation_audit']={'frame_refs':[f['frame_id'] for f in window],'before_events':before}
    return result
