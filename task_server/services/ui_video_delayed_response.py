"""Review newly found touches followed by an older response hypothesis."""


def review_refs(original,reviewed):
    refs=set()
    interactions=[e for e in reviewed if e['action'] in {'aiTap','aiInput','aiScroll'}]
    for first,second in zip(interactions,interactions[1:]):
        if first['action']!=second['action'] or first['action']!='aiTap':continue
        if not 0<=second['start_ms']-first['end_ms']<=500:continue
        first_refs=set(first['frame_refs']);second_refs=set(second['frame_refs'])
        if not any(e['action']=='aiAssert' and first_refs.intersection(e['frame_refs']) for e in original):continue
        if any(e['action'] in {'aiTap','aiInput','aiScroll'} and first_refs.intersection(e['frame_refs']) for e in original):continue
        if not any(e['action']=='aiTap' and second_refs.intersection(e['frame_refs']) for e in original):continue
        refs.update(first_refs|second_refs)
    return refs


REVIEW_QUESTION=(
    '核对相邻触点生命周期：触点按下、松开与随后页面响应不是多次点击。'
    '当前按下状态与历史指针总数不同；如顶部P:0/1代表当前没有按下，不把历史十字残留当新点击。'
    '本窗口确有新点击或重试须保留；如果只是前一操作延迟响应，用aiAssert完整引用该旧锚点原帧，'
    '明确未见新点击及自动转场依据。不得仅凭画面变化推断点击，也不能仅凭没有触点就否定操作。'
    '结合完整前后画面判断，不能确认时保留疑问，不删除真实操作。'
)


def _without_negated_actions(note):
    import re
    action=r'(?:松开后)?(?:任何|新的|新|独立的|独立|用户|手指|触点|再次|重新){0,4}(?:点击|按下|重试|滑动|滚动|拖动)'
    target=r'(?:针对(?:“[^“”，。；\n]{1,80}”|「[^「」，。；\n]{1,80}」|"[^"，。；\n]{1,80}")(?:按钮|控件|入口)?的)?'
    negative=r'(?:未见|未观察到|未发生|没有|并非|而非|不是|(?<!并非)(?<!不是)(?<!没有)(?<!不)无)'
    return re.sub(negative+target+action+r'(?:(?:或|和|及)'+action+r')*','',note)


def has_positive_tap(note):
    import re
    remaining=_without_negated_actions(note)
    return any(re.search(r'(?:独立|用户|手指|触点|再次|重新).{0,20}(?:点击|按下|重试)',clause)
               for clause in re.split(r'[，。；;,]|但是|但|随后|并且|且',remaining))


def has_drag_conflict(note):
    """Reject independent actions; response negation does not negate an action."""
    import re
    if has_positive_tap(note):return True
    note=_without_negated_actions(note)
    note=re.sub(r'(?:未见|没有|无)(?:停顿|抬手|松开)(?:或|及|和)(?:重新|再次)(?:按压|按下)(?:迹象)?','',note)
    remaining=re.sub(
        r'(?:未观察到|未见|未发生|没有|并非|而非|不是|无)(?:松开后)?'
        r'(?:任何|新的|新|独立的|独立|用户|手指|触点|再次|重新|第二次){0,4}'
        r'(?:点击|按下|按压|滚动|滑动|拖动)', '', note)
    return bool(re.search(
        r'(?:再次|重新|第二次|又)[^，。；;,]{0,12}(?:滑动|滚动|拖动|按下)|'
        r'(?:[两二三四五六七八九十多]|[2-9]|[1-9][0-9]+)次(?:独立)?(?:滑动|滚动|拖动)', remaining))


def pending_response_checks(events,frames):
    """Find a repeated-control hypothesis separated by a confirmation gate."""
    import re
    interactions=[(i,e) for i,e in enumerate(events) if e['action'] in {'aiTap','aiInput','aiScroll'}]
    checks=[]
    for (opening_index,opening),(confirm_index,confirm),(index,event) in zip(interactions,interactions[1:],interactions[2:]):
        if (not opening['action']==confirm['action']==event['action']=='aiTap' or
            opening['target']!=event['target'] or
            not 0<=confirm['start_ms']-opening['end_ms']<=10000 or
            not 0<=event['start_ms']-confirm['end_ms']<=3000 or
            not re.search(r'确认|确定|同意|提交',confirm['target']) or
            not re.search(r'弹窗|对话框',confirm['observation']) or
            not re.search(r'消失|关闭',confirm['observation'])):continue
        checks.append({'candidate_index':index,'opening_index':opening_index,'confirmation_index':confirm_index,
            'target':f'candidate:{index}', 'control':event['target'],
            'opening_frame_ids':opening['frame_refs'],'confirmation_frame_ids':confirm['frame_refs'],
            'response_frame_ids':event['frame_refs'],
            'context_frame_ids':[f['frame_id'] for f in frames if opening['start_ms']<=f['timestamp_ms']<=event['end_ms']]})
    return checks


def review_pending_responses(observed,document,root,cancelled,known_secrets=()):
    """Review the response cause, preserving real clicks and original coverage."""
    import copy
    from . import ai_skill_service as ai
    from .ui_video_windows import load_assets
    from .ui_video_analysis import _bind_whole_evidence,validate_analysis
    from .ui_video_reconcile import check_coverage
    if cancelled():raise ValueError('video_cancelled')
    frames=document['media']['frames'];events=observed['events']
    checks=pending_response_checks(events,frames)
    if not checks:return observed
    ids={ref for check in checks for ref in check['context_frame_ids']}
    images=[f for f in frames if f['frame_id'] in ids]
    if not 1<=len(checks)<=4 or not 2<=len(images)<=60:raise ValueError('video_vision_limit')
    raw=ai.run_ai_skill('ui_video_observe',{'phase':'whole-observe','frames':images,'image_frames':images,
        'duration_ms':document['media']['duration_ms'],'checks':checks},image_assets=load_assets(images,root,cancelled),
        version='response',timeout=120,retry_count=0,max_tokens=4000,repair_invalid_json=False)
    if cancelled():raise ValueError('video_cancelled')
    checked=validate_analysis(_bind_whole_evidence(raw,images),images,document['media']['duration_ms'],known_secrets)
    if checked['uncertainties'] or checked['multiple_scenarios']:raise ValueError('video_expected_missing')
    by_key={check['target']:check for check in checks};seen=set();out=copy.deepcopy(observed);audit=[]
    for response in checked['events']:
        key=response['target']
        if key not in by_key or key in seen or response['action'] not in {'aiTap','aiAssert'} or response['uncertainties']:
            raise ValueError('video_reference_invalid')
        check=by_key[key];index=check['candidate_index'];seen.add(key)
        if response['frame_refs']!=events[index]['frame_refs']:raise ValueError('video_reference_invalid')
        revised={**events[index],'action':response['action'],'observation':response['observation'],'uncertainties':[]}
        if revised['action']!=events[index]['action']:
            revised.pop('outcome',None);revised.pop('reusable_expected',None)
        # Inferred page motion is not enough: the ordinary evidence gate still
        # requires full old refs and explicit no-new-click + automatic cause.
        check_coverage([events[index]],[revised],document['media']['duration_ms'])
        out['events'][index]=revised
        audit.append({'candidate_index':index,'opening_index':check['opening_index'],
            'confirmation_index':check['confirmation_index'],'action':revised['action'],
            'frame_refs':list(response['frame_refs']),'context_frame_ids':check['context_frame_ids']})
    if seen!=set(by_key):raise ValueError('video_model_invalid')
    return {**out,'response_review':audit}
