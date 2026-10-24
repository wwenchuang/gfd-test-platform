"""Recognize evidenced recording-tail app exits, never business navigation."""
import re


def is_terminal_app_exit(events, index, duration):
    event = events[index]
    if event.get('action') != 'aiTap' or event.get('start_ms', 0) < max(0, duration - 5000):
        return False
    target = str(event.get('target', ''))
    observation = str(event.get('observation', ''))
    if not re.fullmatch(r'(?:系统返回手势|系统返回键|系统主页键|系统Home键)', target):
        return False
    if event.get('uncertainties'):
        return False
    if re.search(r'可能|无法确认|未确认|是否|(?:未|不|无|没)[^，。；\n]{0,12}(?:退出|桌面)', observation):
        return False
    if not re.search(r'退出应用|应用退出|应用完全退出|触发应用退出', observation):
        return False
    following = events[index + 1:]
    if any(e.get('action') in {'aiTap', 'aiInput', 'aiScroll'} for e in following):
        return False
    # Require an explicit desktop outcome in the action's own evidence. Later
    # assertions alone cannot establish that this particular action caused it.
    if not re.search(r'(?:回到|返回至?|显示|露出|可见)[^，。；\n]{0,16}(?:手机|系统)?桌面', observation):
        return False
    return True
