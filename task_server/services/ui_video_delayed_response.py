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
    return re.sub(r'(?:未见|未观察到|未发生|没有|并非|而非|不是)'+action+r'(?:(?:或|和|及)'+action+r')*','',note)


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
