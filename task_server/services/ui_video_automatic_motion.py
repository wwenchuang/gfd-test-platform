"""Select possible automatic motion for original-pixel review, never rewrite it."""
import re


def review_refs(events):
    refs=set()
    for event in events:
        if event.get('action')!='aiScroll':continue
        clauses=re.split(r'[。；;]|但是|但',event.get('observation',''))
        for clause in clauses:
            if re.search(r'未|没有|不是|并非|不属于',clause):continue
            if (re.search(r'自动(?:回弹|布局(?:变化|调整|重排))',clause) or
                re.search(r'刷新(?:完成|结束)?[^。；]{0,12}(?:头部|提示|区域)[^。；]{0,12}(?:收起|移出|消失|回弹)',clause)):
                refs.update(event.get('frame_refs',[]));break
    return refs


def disputed_scroll_refs(original, reviewed):
    """Request pixel review for a newly inferred scroll, never choose its action."""
    observations = [set(e.get('frame_refs', [])) for e in original
                    if e.get('action') == 'aiAssert']
    interactions = set().union(*(set(e.get('frame_refs', [])) for e in original
                                 if e.get('action') in ('aiTap', 'aiScroll', 'aiInput')))
    refs = set()
    for event in reviewed:
        frames = set(event.get('frame_refs', []))
        if (event.get('action') == 'aiScroll' and frames and
                not frames & interactions and any(frames <= old for old in observations)):
            refs.update(frames)
    return refs


REVIEW_QUESTION=(
    '核对疑似自动位移：刷新头部/提示收起、自动回弹或布局重排不等于新的用户滚动。'
    '同组画面被分别解释为自动变化和用户滚动时，核对固定控件位置与完整触点轨迹；'
    '异步数据填充、占位卡片替换或推荐内容出现，不等于页面整体滚动。'
    '结合完整前后原帧判断，真实重新按下拖动须保留，单次真实拖动后的惯性不能抹掉原滚动。'
    '仅未看见触点不足以证明没有操作。确为自动变化时用aiAssert完整引用旧锚点原帧，'
    '明确未见独立用户滚动及自动变化依据，并描述稳定的后续页面；不要把短暂刷新提示作为可执行预期。'
    '无法区分时保留疑问，不造动作、不静默删步骤。'
)


_NEGATIVE_DRAG=re.compile(
    r'(?:未观察到|未见|未发生|没有观察到|没有发生|没有|并非|而非|不是|(?<=[，。；])非)'
    r'(?:任何|实际的|实际|用户|手指|触点|页面|内容|新的|独立的|独立|发起的|发起了|本次|进行|发生){0,4}'
    r'(?:滚动|滑动|拖动)')


def has_positive_drag(note):
    """Only remove a negated movement predicate, not unrelated negations."""
    remaining=_NEGATIVE_DRAG.sub('',note)
    for clause in re.split(r'[，。；;,]|但是|但|然而|随后|并且|且',remaining):
        if re.search(r'(?:用户|手指|触点|重新按下|再次|又进行了).{0,24}(?:滚动|滑动|拖动)',clause):return True
    return False
