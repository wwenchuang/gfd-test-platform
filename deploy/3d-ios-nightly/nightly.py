"""Dispatch the nightly iPhone regression through the workbench and send one Feishu summary."""
import argparse
from datetime import timedelta
import fcntl
import importlib.util
import json
from pathlib import Path
import time

from task_server.services.operation_attribution import actor_context
from task_server.services.ui_execution_contract import require_ui_actor
from task_server.services.ui_execution_service import create_ui_batch, preflight_ui_batch
from task_server.services.ui_execution_store import UiExecutionStore

# Share the deployed daily regression's owner, revision check and approved Feishu channel.
daily_path = Path(__file__).resolve().parents[1] / '3d-daily/run.py'
if not daily_path.is_file():
    daily_path = Path('/opt/midscene-3d-daily/current/run.py')
spec = importlib.util.spec_from_file_location("daily_3d", daily_path)
daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily)

STATE = Path('/var/lib/midscene-3d-ios-nightly')
TARGET = dict(platform='ios', runner_id='mac-ios-adouceshi', device_id='00008110-000150CC21C2801E')
SMOKE = ('打印记录查看', '个人导入文件保存期限', '个人作品按类型筛选')
APP = 'com.kfb.model'
BASE = 'http://101.34.197.12:8088'
RETRYABLE = {'device_busy', 'device_queued', 'device_not_ready', 'runner_offline', 'device_offline'}


def phases(rows, configured=False):
    active = [row for row in rows if not row.get('deferred')]
    selected = {row['name']: row for row in active}
    if (not configured and len(selected) != len(active)) or len({row['case_id'] for row in rows}) != len(rows):
        raise ValueError('duplicate_baseline')
    if configured:
        preferred = {name + '.yaml': index for index, name in enumerate(SMOKE)}
        smoke = sorted((r for r in active if r['filename'].removeprefix('iOS-') in preferred),
                       key=lambda r: preferred[r['filename'].removeprefix('iOS-')]) or active[:3]
    else:
        smoke = [selected[name] for name in SMOKE]
    return [(label, group) for label, group in [('冒烟', smoke),
            ('功能回归', [r for r in active if r not in smoke])] if group]


def dispatch(record, path, label, group, deadline):
    key = 'ios-nightly-' + record.get('manual_run_id', record['date']) + ('-smoke' if label == '冒烟' else '-remaining')
    previous = UiExecutionStore().find_idempotent(record['owner_id'], key)
    if previous:
        bid = daily.resume_batch(previous, record, group)
    else:
        request = daily.batch_request(record.get('target', TARGET), group)
        if record.get('manual_run_id'):
            from task_server.services.ui_schedule_service import schedule_device_busy
            if schedule_device_busy(record['target']['device_id']):
                record['blockers'] = ['device_busy']
                return 'iPhone 或用例预检未通过'
        stop = min(deadline, daily.now() + timedelta(minutes=5))
        while True:
            if daily.now() >= deadline:
                return '执行时间窗口已结束'
            check = preflight_ui_batch(None, request)
            if check['ready']:
                break
            blockers = {item['code'] for item in check['blockers']}
            if not blockers or not blockers <= RETRYABLE or daily.now() >= stop:
                record['blockers'] = sorted(blockers)
                return 'iPhone 或用例预检未通过'
            time.sleep(10)
        bid = create_ui_batch(None, request, key)['ui_batch_id']
    record['batches'][label] = bid
    daily.write_record(path, record)
    attempts = daily.watch(bid, deadline)
    record['outcomes'][label] = {a['case_id']: a['status'] for a in attempts}
    daily.write_record(path, record)
    if not daily.stage_complete(group, attempts, smoke=label == '冒烟'):
        return '冒烟未达继续条件' if label == '冒烟' else '部分用例未完成'
    return None


def card(record, rows, setup=False):
    from task_server.services.ui_regression_card import regression_card
    if not setup:
        reason = record['status']
        if reason == '冒烟未达继续条件':
            reason = '首批验证未通过继续条件，回归已停止'
        return regression_card(record, rows, 'ios', reason, BASE)
    start = (record.get('operate_at') or '')[11:16] or '22:00'
    text = f'北京时间每天 {start} 执行。回归结束后可在本群查看结果和工作台报告。'
    return {'msg_type': 'interactive', 'card': {'header': {'template': 'blue',
            'title': {'tag': 'plain_text', 'content': '智小白 3D · iOS 定时回归已启用'}},
            'elements': [{'tag': 'div', 'text': {'tag': 'plain_text', 'content': text}}]}}


def notify(record, path, rows, setup):
    if record.get('feishu') in ('sending', 'sent', 'unknown'):
        return
    send = daily.daily_sender()
    message = card(record, rows, setup)
    record['feishu'] = 'sending'
    daily.write_record(path, record)
    try:
        result = send(message)
        code = result.get('code', result.get('StatusCode'))
        record.update(feishu='sent' if type(code) is int and code == 0 else 'rejected', feishu_code=code)
    except Exception:
        record['feishu'] = 'unknown'
    daily.write_record(path, record)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['check', 'run', 'announce'])
    mode = parser.parse_args().mode
    snapshot = daily.schedule_snapshot('ios')
    rows = snapshot[1] if snapshot else json.loads(Path(__file__).with_name('baselines.json').read_text())
    stages = phases(rows, configured=bool(snapshot))
    actor = daily.trusted_actor()
    with actor_context(actor):
        for permission in ('ui.view', 'ui.execute', 'platform.notify'):
            require_ui_actor(permission=permission, app_id=APP)
        if mode == 'check':
            daily.validate_published([r for _, group in stages for r in group])
            daily.daily_sender()
            print('Verified pinned revisions, owner and Feishu channel:', [len(g) for _, g in stages])
            return
        clock = daily.now()
        # A resumed service after midnight may still own the preceding evening.
        if snapshot:
            start, _, metadata = snapshot
            date = start.date()
        else:
            date = (clock - timedelta(hours=6)).date()
            start = clock.replace(year=date.year, month=date.month, day=date.day, hour=22, minute=0, second=0, microsecond=0)
            metadata = {}
        setup = mode == 'announce'
        path = STATE / ('enabled.json' if setup else metadata.get('manual_run_id', str(date)) + '.json')
        with (STATE / 'schedule.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not setup and not start <= clock < start + timedelta(hours=6):
                return
            record = json.loads(path.read_text()) if path.exists() else dict(
                date=str(clock.date() if setup else date), owner_id=actor['user_id'], batches={}, outcomes={}, **metadata)
            if record['owner_id'] != actor['user_id']:
                raise RuntimeError('schedule_owner_changed')
            if record.get('schedule_digest') != metadata.get('schedule_digest'):
                return
            if not setup and not record.get('final'):
                from task_server.services.ui_regression_card import capture_phone
                capture_phone(record, record.get('target', TARGET))
                if snapshot and not path.exists():
                    daily.write_record(path, record)
                try:
                    if record.get('selection_error'):
                        raise ValueError('selection_blocked')
                    daily.validate_published([r for _, group in stages for r in group])
                    for label, group in stages:
                        result = dispatch(record, path, label, group, start + timedelta(hours=6))
                        if result:
                            break
                    else:
                        failures = any('failed' in outcomes.values() for outcomes in record['outcomes'].values())
                        result = '存在失败用例' if failures else '已执行用例全部通过'
                except Exception as error:
                    result = record.get('selection_error') or '调度异常'
                    record['error'] = type(error).__name__ + ': ' + str(getattr(error, 'code', ''))
                record.update(status=result, final=True, finished_at=daily.now().isoformat())
                daily.write_record(path, record)
            notify(record, path, rows, setup)
            if record['feishu'] != 'sent' or record.get('status') == '调度异常':
                raise SystemExit(1)


if __name__ == '__main__':
    main()
