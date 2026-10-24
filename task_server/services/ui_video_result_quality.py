"""Request bounded pixel review for known result-loss signals, never invent values."""
import re

# Categories only: values and assertions must still be read from outcome pixels.
_SETTING_NAMES=('份数','色彩','页码范围','单双面','纸张大小','打印顺序')
_PRINT_SETTINGS=(
    r'份数\s*(?:显示)?\s*(?:为|是|=|：|:)?\s*\d+|\d+\s*份',
    r'彩色|黑白',
    r'\d+\s*(?:至|到|[-—])\s*\d+\s*页|全部(?:页面|页)',
    r'单面|(?<!单)双面',
    r'(?<![A-Za-z0-9])[AaBb][0-6](?![A-Za-z0-9])',
    r'逆序|正序',
)


def needs_result_review(event,expected):
    """A suspicion is a review trigger, not permission to rewrite the case."""
    if event.get('selection') or '{{' in event.get('target',''):
        return False
    if event.get('action') not in {'aiTap','aiScroll'}:
        return False
    observed=(event.get('outcome') or {}).get('observation','')
    incidental_parts=[part for part in re.split(r'[，,。；;]',expected)
                      if not re.search(r'已(?:关闭|隐藏|清空|禁用|取消)|不再显示',part)]
    if (any(re.search(r'搜索(?:框)?[^，,。；;]{0,10}(?:推荐|热词)|推荐位内容|个性化推荐内容',part)
            for part in incidental_parts) and
        not re.search(r'搜索|推荐|热词',event.get('target',''))):
        return True
    if (event['action']=='aiScroll' and re.search(r'轮播[^，,。；;]{0,16}切换为',expected) and
        not re.search(r'轮播|广告',event.get('target',''))):
        return True
    if re.search(r'打印(?:设置|配置|参数)',observed):
        if any(re.search(pattern,observed) and not re.search(pattern,expected)
               for pattern in _PRINT_SETTINGS):
            return True
    return bool(re.search(r'任务传输中|正在传输',observed+'。'+expected) and
                re.search(r'任务完成|打印完成|打印成功',expected))


def review_requirements(event):
    """Only platform category names enter feedback, never source text or values."""
    observed=(event.get('outcome') or {}).get('observation','')
    return {
        'configuration_categories_to_check':[
            name for name,pattern in zip(_SETTING_NAMES,_PRINT_SETTINGS)
            if re.search(r'打印(?:设置|配置|参数)',observed) and re.search(pattern,observed)],
        'check_business_status_not_reward_overlay':bool(re.search(r'任务传输中|正在传输',observed)),
    }


def needs_configuration_review(event):
    """Recognize a visible configuration surface, not a specific business value.

    This only selects original frames for an independent review. It does not
    infer facts or decide whether the case is correct.
    """
    if event.get('selection') or '{{' in event.get('target',''):
        return False
    if event.get('action') not in {'aiTap','aiScroll'}:
        return False
    observed=(event.get('outcome') or {}).get('observation','') or event.get('observation','')
    return bool(re.search(r'(?:设置|配置|参数|筛选)(?:面板|页面|页|弹窗|对话框)|表单(?:可见|显示|展开)',observed))


def configuration_review_indexes(plan,candidates,reviewed,protected=False):
    if protected:return set()
    result=set()
    for event in plan['events']:
        index=event.get('candidate_index')
        if (type(index) is int and 0<=index<len(candidates) and index not in reviewed and
            event.get('action') in {'aiTap','aiScroll'} and
            not event.get('selection') and '{{' not in event.get('target','') and
            (needs_configuration_review(event) or needs_configuration_review(candidates[index]))):
            result.add(index)
    return result
