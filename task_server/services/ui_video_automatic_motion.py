"""Select possible automatic motion for original-pixel review, never rewrite it."""
import re


def review_refs(events):
    refs=set()
    for index,event in enumerate(events):
        if event.get('action')!='aiScroll':continue
        if index and _fresh_dialog_motion(events[index-1],event):
            refs.update(event.get('frame_refs',[]));continue
        clauses=re.split(r'[。；;]|但是|但',event.get('observation',''))
        for clause in clauses:
            if re.search(r'未|没有|不是|并非|不属于',clause):continue
            if (re.search(r'自动(?:回弹|布局(?:变化|调整|重排))',clause) or
                re.search(r'刷新(?:完成|结束)?[^。；]{0,12}(?:头部|提示|区域)[^。；]{0,12}(?:收起|移出|消失|回弹)',clause)):
                refs.update(event.get('frame_refs',[]));break
    return refs


def _fresh_dialog_motion(previous,event):
    """Review inferred motion immediately after a dialog opens, without deciding it."""
    if previous.get('action')!='aiTap' or event.get('action')!='aiScroll' or event.get('evidence_kind')!='inferred':return False
    start,end=event.get('start_ms'),previous.get('end_ms')
    if not isinstance(start,(int,float)) or not isinstance(end,(int,float)) or not 0<=start-end<=3000:return False
    previous_note=previous.get('observation','')
    container=r'(?:弹窗|对话框|面板|浮层|底部弹层|弹层)'
    if not re.search(rf'(?:弹出|出现|打开|显示).{{0,24}}{container}|{container}.{{0,12}}(?:弹出|出现|打开|显示)',previous_note):return False
    if not re.search(container,event.get('target','')):return False
    # An observation of an actual continuous contact is stronger than merely
    # describing content displacement or asserting the user scrolled.
    note=event.get('observation','')
    for clause in re.split(r'[，。；;,]',note):
        if re.search(r'未|没有|不|无法|难以|不能',clause):continue
        if re.search(r'(?:触点|手指).{0,30}(?:连续|持续).{0,20}(?:移动|拖动|滑动)',clause):return False
    return True


def fresh_dialog_review_questions(events):
    """Surface unresolved dialog motion even if planning omits uncertainty."""
    return [f"弹窗出现后画面发生变化，请核对原视频帧 {'、'.join(event.get('frame_refs',[]))} 是否存在独立滚动。"
            for previous,event in zip(events,events[1:]) if _fresh_dialog_motion(previous,event)]


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


def disputed_tap_scroll_refs(original, reviewed):
    """Review a re-observed scroll confined to evidence already owned by an old click."""
    refs=set()
    taps=[event for event in original if event.get('action')=='aiTap']
    for tap in taps:
        old=set(tap.get('frame_refs',[]))
        if not old:continue
        for event in reviewed:
            frames=set(event.get('frame_refs',[]))
            if event.get('action')=='aiScroll' and frames and frames<=old:
                refs.update(frames)
    return refs


_NO_ACTION_ASSERTION=re.compile(
    r'(?:未见|未观察到|没有观察到|没有|未发生|无)'
    r'(?:当前|本帧|本次|任何|新的|独立的|独立|用户|手动|触点|手指){0,4}'
    r'(?:用户)?(?:触点|点击|操作|交互|按下)(?:操作)?')


def explicit_no_action_assertion(note):
    return bool(_NO_ACTION_ASSERTION.search(note or ''))


def contradictory_tap_assertion_refs(*event_groups):
    """Return shared original frames claimed by a tap and an explicit no-action assertion."""
    groups=[list(group or []) for group in event_groups]
    refs=set()
    for i,left_group in enumerate(groups):
        for right_group in groups[i:]:
            for left_index,left in enumerate(left_group):
                start=left_index+1 if left_group is right_group else 0
                for right in right_group[start:]:
                    if left.get('action')=='aiAssert' and right.get('action')=='aiTap':
                        assertion,tap=left,right
                    elif left.get('action')=='aiTap' and right.get('action')=='aiAssert':
                        assertion,tap=right,left
                    else:continue
                    common=set(assertion.get('frame_refs',[])) & set(tap.get('frame_refs',[]))
                    if common and explicit_no_action_assertion(assertion.get('observation','')):
                        refs.update(common)
    return refs


def contradictory_tap_assertion_questions(*event_groups):
    refs=contradictory_tap_assertion_refs(*event_groups)
    if not refs:return []
    return ['同一原帧同时支持点击与“未观察到用户操作”的判断，请核对触点证据；无法确认时保留疑问，不删除或替换动作。']


def disputed_tap_scroll_questions(original, reviewed):
    refs=disputed_tap_scroll_refs(original,reviewed)
    if not refs:return []
    return ['独立复核把旧点击所引用原帧中的位移描述为滚动，请核对完整触点轨迹；图片加载或布局变化不能单独证明用户滚动，真实拖动仍须保留。']


REVIEW_QUESTION=(
    '核对疑似自动位移：刷新头部/提示收起、自动回弹或布局重排不等于新的用户滚动。'
    '同组画面被分别解释为自动变化和用户滚动时，核对固定控件位置与完整触点轨迹；'
    '异步数据填充、占位卡片替换、弹窗图片加载或推荐内容出现，不等于页面整体滚动。'
    '结合完整前后原帧判断，真实重新按下拖动须保留，单次真实拖动后的惯性不能抹掉原滚动。'
    '仅未看见触点不足以证明没有操作。确为自动变化时用aiAssert完整引用旧锚点原帧，'
    '明确未见独立用户滚动及自动变化依据，并描述稳定的后续页面；不要把短暂刷新提示作为可执行预期。'
    '无法区分时保留疑问，不造动作、不静默删步骤。'
)


_NEGATIVE_DRAG=re.compile(
    r'(?:未观察到|未见|未发生|没有观察到|没有发生|没有|并非|而非|不是|(?<=[，。；])非)'
    r'(?:任何|实际的|实际|用户|手指|触点或|触点|页面|内容|新的|独立的|独立|发起的|发起了|本次|进行|发生){0,4}'
    r'(?:滚动|滑动|拖动)')


def has_positive_drag(note):
    """Only remove a negated movement predicate, not unrelated negations."""
    # A double negative asserts that movement did occur. Do not erase it.
    if re.search(r'(?:并非|不是|不)(?:没有|未见|未发生)(?:用户)?(?:手动)?(?:滚动|滑动|拖动)',note):return True
    # This exact wording appears in model visual reviews; limit the match to
    # its negated predicate so a later independent drag is still detected.
    note=re.sub(r'(?<!不是)(?<!并非)而非用户手动(?:滚动|滑动|拖动)','',note)
    remaining=_NEGATIVE_DRAG.sub('',note)
    for clause in re.split(r'[，。；;,]|但是|但|然而|随后|并且|且',remaining):
        if re.search(r'(?:用户|手指|触点|重新按下|再次|又进行了).{0,24}(?:滚动|滑动|拖动)',clause):return True
    return False
