"""Finite server-reserved keyboard operation; never model supplied commands."""
PREFIX = '平台软键盘收起v1：'
MARKER = PREFIX + 'dismissKeyboard'
TARGET = '当前页面的软键盘'
CAPABILITY = 'ui_keyboard_dismiss_v1'


def encode(action, target, value):
    if action != 'dismissKeyboard' or target != TARGET or value != '':
        raise ValueError('收起软键盘仅支持固定目标和空值')
    return MARKER


def validate_flow(data):
    found = False
    for task in data.get('tasks', []):
        for step in task.get('flow', []):
            prompt = step.get('aiQuery', '')
            if isinstance(prompt, str) and prompt.startswith(PREFIX):
                if step != {'aiQuery': MARKER}:
                    raise ValueError('收起软键盘保留动作无效')
                found = True
    return found
