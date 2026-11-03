"""Finite server-reserved keyboard operation; never model supplied commands."""
PREFIX = '平台原生返回v1：'
MARKER = PREFIX + 'navigateBack'
TARGET = '上一页面'
CAPABILITY = 'ui_native_back_v1'


def encode(action, target, value):
    if action != 'navigateBack' or target != TARGET or value != '':
        raise ValueError('原生返回仅支持固定目标和空值')
    return MARKER


def validate_flow(data):
    found = False
    for task in data.get('tasks', []):
        for step in task.get('flow', []):
            prompt = step.get('aiQuery', '')
            if isinstance(prompt, str) and prompt.startswith(PREFIX):
                if step != {'aiQuery': MARKER}:
                    raise ValueError('原生返回保留动作无效')
                found = True
    return found
