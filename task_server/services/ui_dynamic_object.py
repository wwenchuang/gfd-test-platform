"""Platform-owned runtime references; never an SDK variable or an AI permission grant."""
import re

PREFIX = '平台运行时对象选择v1：'
TARGET = '{{本次对象}}'
RETAINED = '{{保留对象}}'
TOKENS = {TARGET, RETAINED}


def validate_references(steps):
    selected = False
    for step in steps:
        action = step['action']
        text = step['target'] + ' ' + step.get('expected', '')
        refs = set(re.findall(r'\{\{[^{}]*\}\}', text))
        if refs - TOKENS:
            raise ValueError('不支持的运行时对象引用')
        if action == 'selectRecord':
            if selected or refs or step.get('expected'):
                raise ValueError('每条用例只能选取一次对象，选取条件不能引用尚未读取的对象')
            selected = True
        elif refs and not selected:
            raise ValueError('请先添加“选取本次测试对象”，再引用本次对象')
        if selected and re.search(r'\d{12,}|\d{4}[-/]\d{2}[-/]\d{2}', text):
            raise ValueError('可复用步骤不能固定录屏编号或日期，请使用本次对象/保留对象')
    if selected:
        after = steps[next(i for i,s in enumerate(steps) if s['action']=='selectRecord')+1:]
        if not any(TARGET in s['target'] and s['action']=='aiTap' for s in after):
            raise ValueError('选取后必须使用本次对象定位操作')
        checks = [s['target'] if s['action']=='aiAssert' else s.get('expected','') for s in after]
        if not any(TARGET in text and RETAINED in text for text in checks):
            raise ValueError('请同时检查本次对象的结果和保留对象，避免误操作')


def uses_dynamic_object(text):
    return any(token in text for token in (*TOKENS, PREFIX))
