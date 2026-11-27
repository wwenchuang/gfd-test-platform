"""Feishu summary shared by the Android and iOS 3D regression workers."""
import logging
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit

from .ui_execution_messages import blocker_message


def capture_phone(record, target):
    """Keep the execution-time label even if a phone disconnects before notification."""
    if not target or record.get('report_phone'):
        return
    from .runner_service import list_runners
    try:
        runner = list_runners().get(target['runner_id'], {})
        for device in runner.get('devices', []):
            if device.get('device_id') != target['device_id'] or device.get('platform') != target['platform']:
                continue
            name = device.get('label') or device.get('model')
            if name and name != target['device_id']:
                record['report_phone'] = {'name': name, 'platform': target['platform'],
                                          'os': device.get('ios_version') or device.get('android_version') or ''}
            return
    except (OSError, ValueError, KeyError):
        logging.getLogger(__name__).warning('Could not read device label for regression notification')


def _text(content):
    return {'tag': 'plain_text', 'content': content}


def _line(content):
    return {'tag': 'div', 'text': _text(content)}


def regression_card(record, rows, platform, reason, base):
    outcomes = {key: value for group in record.get('outcomes', {}).values() for key, value in group.items()}
    active = [r for r in rows if not r.get('deferred')]
    def label(row):
        return row.get('name') or Path(row['filename']).stem
    failed = [label(r) for r in active if outcomes.get(r['case_id']) == 'failed']
    deferred = [label(r) for r in rows if r.get('deferred')]
    passed = sum(outcomes.get(r['case_id']) == 'passed' for r in active)
    missing = len(active) - passed - len(failed)
    success = record.get('status') in ('passed', '已执行用例全部通过')
    if failed:
        verdict, color = f'{len(failed)} 条失败', 'red'
    elif success and passed and passed == len(rows):
        verdict, color = f'{passed} 条全部通过', 'green'
    elif success and passed and passed == len(active) and deferred:
        verdict, color = f'{passed} 条通过 · {len(deferred)} 条暂缓', 'blue'
    else:
        verdict, color = '回归未完成', 'orange'

    phone = record.get('report_phone') or {}
    system = 'iOS' if platform == 'ios' else 'Android'
    name = phone.get('name') if phone.get('platform') == platform else None
    device = f"{name} · {system} {phone.get('os') or ''}".strip() if name else f'{system} 设备（机型未记录）'
    clock = record.get('operate_at') or record.get('created_at') or ''
    stamp = ' '.join(filter(None, [record.get('date'), clock[11:16]]))
    kind = '手动回归' if record.get('manual_run_id') else '定时回归'
    elements = [_line(f'{device}  ｜  {kind}\n{stamp} 北京时间'),
                {'tag':'hr'}, _line(f'计划 {len(rows)} 条　通过 {passed}　失败 {len(failed)}'
                                   + (f'　暂缓 {len(deferred)}' if deferred else '')
                                   + (f'　未完成 {missing}' if missing else ''))]
    if failed:
        elements.append(_line('失败用例　' + '、'.join(failed)))
    if not success and reason not in ('存在失败用例', 'finished_with_failures'):
        elements.append(_line(reason))
    if record.get('blockers'):
        elements.append(_line('执行前检查　' + '、'.join(dict.fromkeys(blocker_message(k) for k in record['blockers']))))
    if deferred:
        elements.append(_line('暂缓用例　' + '、'.join(deferred)))
    labels = {'smoke':'首批验证', '冒烟':'首批验证', 'remaining':'基础功能', '功能回归':'功能回归',
              'previews':'建模与导入', 'dragon':'关节龙实打'}
    batches = record.get('batches', {})
    url = urlsplit(base or '')
    if batches and url.scheme in ('https','http') and url.hostname and not (url.username or url.password or url.query or url.fragment):
        actions = []
        for stage, bid in batches.items():
            if isinstance(bid, str) and re.fullmatch(r'uib_[a-f0-9]{32}', bid):
                actions.append({'tag':'button', 'type':'default' if actions else 'primary',
                                'text':_text('查看' + labels.get(stage, '回归') + '报告'),
                                'url':base.rstrip('/') + '/task-manager.html?' + urlencode({'ui_batch':bid})})
        if actions:
            elements.append({'tag':'action', 'actions':actions})
    if batches and elements[-1].get('tag') != 'action':
        elements.append(_line('查看详情　工作台 → UI 测试报告'))
    return {'msg_type':'interactive', 'card':{'header':{'template':color,
            'title':_text('智小白 3D 回归 · ' + verdict)}, 'elements':elements}}
