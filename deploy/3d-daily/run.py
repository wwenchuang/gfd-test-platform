"""Daily OPPO regression. Only the running platform may dispatch and claim Jobs."""
import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time

APP = 'com.kfb.model'
PHONE = 'ecbfd645'
MACHINE = '9888E0094F2A'
SMOKE = ('我的收藏查看.yaml', '打印记录查看.yaml', '模型生成记录.yaml')
DRAGON = '关节龙打印.yaml'
# Keep the original group membership and idempotency key for resumable daily runs.
MODEL_FUNCTIONS = (
    '模型导入-微信导入.yaml', 'OBJ保龄球打印.yaml', '文字建模.yaml', '姓名牌打印.yaml',
    '标牌打印.yaml', '普通印章打印.yaml', '十二生肖印章打印.yaml',
)
MAX_BATCH_CASES = 200  # ui_execution_service._normalize_request
LOCAL = timezone(timedelta(hours=8))
MANIFEST = Path(__file__).with_name('baselines.json')
STATE = Path('/var/lib/midscene-3d-daily')


def schedule_snapshot(platform):
    path = os.environ.get('MIDSCENE_UI_SCHEDULE_SNAPSHOT')
    if path is None:
        return None
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    target = config['target']
    if (config['platform'] != platform or set(target) != {'platform', 'device_id', 'runner_id'}
            or target['platform'] != platform or not all(isinstance(v, str) and v.strip() for v in target.values())
            or not config['version'] or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', config['time'])
            or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', config['scheduled_date'])):
        raise ValueError('invalid_schedule_snapshot')
    start = datetime.fromisoformat(config['scheduled_date'] + 'T' + config['time']).replace(tzinfo=LOCAL)
    manual = {}
    if 'manual_run_id' in config:
        if not re.fullmatch(r'manual-[a-f0-9]{32}', config['manual_run_id']):
            raise ValueError('invalid_manual_run_id')
        start = datetime.fromisoformat(config['started_at']).astimezone(LOCAL)
        manual = {key: config[key] for key in ('manual_run_id', 'requested_by')}
    rows = [dict(row, sha256=row.get('revision') or row['sha256'],
                 name=row.get('task_name') or row.get('name') or Path(row['filename']).stem)
            for row in config['cases']]
    if not rows or not any(not row.get('deferred') for row in rows):
        raise ValueError('empty_schedule_selection')
    digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    metadata = {'target': target, 'schedule_version': config['version'],
                'schedule_digest': digest, 'operate_at': start.isoformat(), **manual}
    if config.get('selection_error'):
        metadata['selection_error'] = config['selection_error']
    return start, rows, metadata


def plan(rows, selected=False):
    if not isinstance(rows, list):
        raise ValueError('invalid_baseline_manifest')
    by_file = {}
    ids = set()
    for row in rows:
        if (not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k]
                for k in ('case_id', 'module', 'filename', 'sha256'))
                or not re.fullmatch('[0-9a-f]{64}', row['sha256'])
                or row['case_id'] in ids or row['filename'] in by_file):
            raise ValueError('invalid_baseline_manifest')
        by_file[row['filename']] = row
        ids.add(row['case_id'])
    if not selected and not {DRAGON, *SMOKE} <= by_file.keys():
        raise ValueError('missing_required_baselines')
    remaining = [row for row in rows if row['filename'] not in (*SMOKE, *MODEL_FUNCTIONS, DRAGON)]
    previews = [by_file[name] for name in MODEL_FUNCTIONS if name in by_file]
    smoke = [by_file[name] for name in SMOKE if name in by_file]
    if not smoke:
        # Keep the physical print in its own guarded final stage.
        smoke = (remaining + previews)[:3]
        remaining = [row for row in remaining if row not in smoke]
        previews = [row for row in previews if row not in smoke]
    phases = [('smoke', smoke)] if smoke else []
    for start in range(0, len(remaining), MAX_BATCH_CASES):
        name = 'remaining' if start == 0 else f'remaining_{start // MAX_BATCH_CASES + 1}'
        phases.append((name, remaining[start:start + MAX_BATCH_CASES]))
    if previews:
        phases.append(('previews', previews))
    if DRAGON in by_file:
        phases.append(('dragon', [by_file[DRAGON]]))
    return phases


def validate_published(rows):
    from task_server.services import case_service
    catalog = {}
    for asset in case_service.list_task_case_assets():
        if (asset.get('app_package') == APP and asset.get('status') in {'active', 'baseline'}
                and not asset.get('error')):
            case_id = asset['case_id']
            if case_id in catalog:
                raise ValueError('ambiguous_published_case')
            catalog[case_id] = asset
    for row in rows:
        asset = catalog.get(row['case_id'])
        if not asset or (asset['module'], asset['file']) != (row['module'], row['filename']):
            raise ValueError('published_case_mismatch:' + row['case_id'])
        if hashlib.sha256(case_service.task_case_yaml(asset).encode()).hexdigest() != row['sha256']:
            raise ValueError('published_revision_changed:' + row['case_id'])


def printer_idle(query=None):
    if query is None:
        from task_server.services.print_job_evidence import _query as query
    # The exact state field is present only on status messages. A later print
    # command can precede its first state change, so it invalidates that sample.
    sql = """
SET SESSION MAX_EXECUTION_TIME=3000;
START TRANSACTION READ ONLY;
SELECT JSON_OBJECT('machine', s.device_id, 'state', s.state,
  'age_s', TIMESTAMPDIFF(SECOND,s.created_at,NOW()),
  'pending_command', EXISTS(
    SELECT 1 FROM print3d_pmc.iot_downs d WHERE d.device_id='9888E0094F2A'
    AND d.cmd_code='print' AND d.created_at>=s.created_at))
FROM (
  SELECT device_id, created_at, JSON_UNQUOTE(JSON_EXTRACT(content,'$.print_state')) AS state
  FROM print3d_pmc.iot_ups WHERE device_id='9888E0094F2A'
  AND created_at BETWEEN NOW() - INTERVAL 60 SECOND AND NOW()
  AND IF(JSON_VALID(content),JSON_EXTRACT(content,'$.print_state'),NULL) IS NOT NULL
  ORDER BY created_at DESC,id DESC LIMIT 1
) s;
ROLLBACK;
"""
    rows = query(sql, 'prod')
    if len(rows) != 1:
        return False
    row = rows[0]
    return (row.get('machine') == MACHINE and row.get('state') == 'StandingBy'
            and type(row.get('age_s')) is int and 0 <= row['age_s'] <= 60
            and row.get('pending_command') == 0)


def submitted_slice(attempt):
    from task_server.services.ui_execution_store import UiExecutionStore
    from contextlib import closing
    with closing(UiExecutionStore()._connect()) as db:
        rows = db.execute("""SELECT a.content FROM ui_report_evidence e JOIN ui_report_artifacts a
            USING(ui_batch_id,artifact_id) WHERE e.ui_batch_id=? AND e.attempt_id=? AND a.kind='html' LIMIT 2""",
                          (attempt['ui_batch_id'], attempt['attempt_id'])).fetchall()
    if len(rows) == 1:
        return slice_from_report(bytes(rows[0][0]))
    return None


def slice_from_report(content):
    markers = re.findall(rb'"output":"(verified|submitting) slice=([0-9]{19}) printer=9888E0094F2A(?=[" ])', content)
    if (len(markers) == 2 and [action for action, _ in markers] == [b'verified', b'submitting']
            and markers[0][1] == markers[1][1]):
        return markers[0][1].decode()
    return None


def print_receipt(attempt, query=None, read_slice=submitted_slice):
    """Require the exact slice submitted by this attempt and its printer acknowledgement."""
    start, finish = attempt.get('started_ns'), attempt.get('finished_ns')
    if (attempt['status'] != 'passed' or attempt['attempt_no'] != 1 or type(start) != int
            or type(finish) != int or not 0 < start <= finish):
        return None
    sn = read_slice(attempt)
    if not sn or not re.fullmatch('[0-9]{19}', sn):
        return None
    if query is None:
        from task_server.services.print_job_evidence import _query as query
    lo = '%d.%06d' % (start // 10**9, start % 10**9 // 1000)
    hi = '%d.%06d' % (finish // 10**9, finish % 10**9 // 1000)
    rows = query(f"""
SET SESSION MAX_EXECUTION_TIME=3000;
START TRANSACTION READ ONLY;
SELECT JSON_OBJECT('machine', d.device_id, 'print_sn', d.request_id, 'state', d.status,
    'command_id',d.id, 'ack_id',(SELECT u.id FROM print3d_pmc.iot_ups u
      WHERE u.device_id=d.device_id AND u.request_id=d.request_id
      AND u.created_at BETWEEN d.created_at AND FROM_UNIXTIME({hi})
      ORDER BY u.created_at,u.id LIMIT 1))
FROM print3d_pmc.iot_downs d WHERE d.device_id='9888E0094F2A' AND d.cmd_code='print'
AND d.created_at BETWEEN FROM_UNIXTIME({lo}) AND FROM_UNIXTIME({hi})
ORDER BY d.created_at,d.id LIMIT 2;
ROLLBACK;
""", 'prod')
    if (len(rows) != 1 or rows[0].get('machine') != MACHINE or rows[0].get('print_sn') != sn
            or rows[0].get('state') != 'ok' or not rows[0].get('ack_id') or not rows[0].get('command_id')):
        return None
    return rows[0]


def stage_complete(group, attempts, smoke=False):
    expected = {(row['case_id'], row['sha256']) for row in group}
    completed = {(a['case_id'], a['case_revision']) for a in attempts if
                 a['attempt_no'] == 1 and a['status'] in {'passed', 'failed'}}
    if expected != completed or len(attempts) != len(group):
        return False
    return not smoke or 2 * sum(a['status'] == 'passed' for a in attempts) >= len(group)


def trusted_actor():
    from task_server.services.operation_attribution import actor_from_trusted_username
    actor = actor_from_trusted_username(os.getenv('MIDSCENE_3D_DAILY_USERNAME'))
    if actor['kind'] != 'user' or actor['user_id'] != os.getenv('MIDSCENE_3D_DAILY_USER_ID'):
        raise PermissionError('schedule_owner_unavailable')
    return actor


def phone_target(expected=None):
    from task_server.services import sonic_service
    from task_server.services.ui_execution_catalog import get_ui_devices
    from task_server.services.ui_schedule_service import schedule_device_busy
    phone = expected['device_id'] if expected else PHONE
    devices = [d for d in get_ui_devices(None, APP)['devices'] if d['target']['device_id'] == phone
               and d['target']['platform'] == 'android' and d['connection_status'] != 'offline']
    if (len(devices) != 1 or devices[0]['connection_status'] != 'ready'
            or (expected and devices[0]['target'] != expected)):
        return None
    status = sonic_service.sonic_list_device_statuses(force=True)
    if not status.get('__source__', {}).get('available') or status.get(phone, {}).get('usage_status') != 'idle':
        return None
    if schedule_device_busy(phone):
        return None
    return devices[0]['target']


def now():
    return datetime.now(LOCAL)


def write_record(path, record):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as file:
        json.dump(record, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    temp.replace(path)
    print(json.dumps(record, ensure_ascii=False), flush=True)


def discard_unclaimed(batch_id):
    from task_server.services.ui_execution_store import UiExecutionStore
    store = UiExecutionStore()
    with store._transaction() as db:
        # Claim and expiry must race in one SQLite transaction. A claimed
        # process retains its lease and is allowed to report its own outcome.
        db.execute("""DELETE FROM device_leases WHERE ui_batch_id=? AND state='reserved'
                      AND attempt_id IN (SELECT attempt_id FROM case_attempts
                      WHERE ui_batch_id=? AND claim_token='' AND status IN ('blocked','queued'))""",
                   (batch_id, batch_id))
        db.execute("""UPDATE case_attempts SET status='cancelled', finished_ns=?,
                      job_sync_pending=CASE WHEN job_id IS NULL THEN 0 ELSE 1 END
                      WHERE ui_batch_id=? AND claim_token='' AND status IN ('blocked','queued')""",
                   (time.time_ns(), batch_id))
        store._rollup(db, batch_id)


def watch(batch_id, deadline, dragon=False):
    from task_server.services.ui_execution_service import get_ui_batch
    while True:
        batch = get_ui_batch(None, batch_id)
        if not batch:
            raise PermissionError('batch_unavailable')
        attempts = batch['case_attempts']
        if batch['execution_settled']:
            return attempts
        stopped = any(a['status'] in {'cancelled', 'interrupted', 'cancelling'} for a in attempts)
        # An unclaimed print must not sit in the ordinary queue with obsolete
        # preflight evidence. The YAML also checks the printer before the tap.
        stale_print = dragon and attempts[0]['status'] in {'queued', 'blocked'} and (
            attempts[0]['status'] == 'blocked' or time.time_ns() - batch['created_ns'] > 60 * 10**9)
        if stopped or stale_print or now() >= deadline:
            discard_unclaimed(batch_id)
            return get_ui_batch(None, batch_id)['case_attempts']
        time.sleep(5 if dragon and not attempts[0]['started_ns'] else 15)


def batch_request(target, rows):
    return {'app_id': APP, 'targets': [target],
            'cases': [{'case_id': row['case_id'], 'revision': row['sha256']} for row in rows],
            'release_report': {'enabled': True}, 'notifications': {'enabled': False}}


def resume_batch(previous, record, rows):
    if record.get('schedule_digest'):
        from task_server.services.ui_execution_service import _normalize_request, _request_hash
        request = batch_request(record['target'], rows)
        if previous['request_hash'] != _request_hash(_normalize_request(request)):
            raise ValueError('scheduled_batch_conflict')
    return previous['ui_batch_id']


def run_stages(record, path, phases, deadline):
    from task_server.services.ui_execution_service import create_ui_batch, preflight_ui_batch
    from task_server.services.ui_execution_store import UiExecutionStore
    for name, group in phases:
        key = 'daily-3d-' + record.get('manual_run_id', record['date']) + '-' + name
        previous = UiExecutionStore().find_idempotent(record['owner_id'], key)
        if previous:
            batch_id = resume_batch(previous, record, group)
        else:
            if now() >= deadline:
                return 'dispatch_window_ended'
            target = phone_target(record['target']) if record.get('schedule_digest') else phone_target()
            if not target:
                return 'skipped_phone_busy_or_unavailable_' + name
            if record.get('target') and target != record['target']:
                return 'runner_changed'
            record['target'] = target
            request = batch_request(target, group)
            preflight = preflight_ui_batch(None, request)
            if not preflight['ready']:
                record['blockers'] = sorted({b['code'] for b in preflight['blockers']})
                return 'preflight_blocked_' + name
            if name == 'dragon':
                try:
                    idle = printer_idle()
                except Exception:
                    return 'skipped_printer_status_unavailable'
                if not idle:
                    return 'skipped_printer_busy_or_unknown'
            write_record(path, record)
            batch_id = create_ui_batch(None, request, key)['ui_batch_id']
        record['batches'][name] = batch_id
        record['status'] = 'monitoring_' + name
        write_record(path, record)
        attempts = watch(batch_id, deadline, dragon=name == 'dragon')
        record.setdefault('outcomes', {})[name] = {a['case_id']:a['status'] for a in attempts}
        if not stage_complete(group, attempts, smoke=name == 'smoke'):
            return 'stopped_' + name
        if name == 'dragon' and attempts[0]['status'] == 'passed':
            try:
                receipt = print_receipt(attempts[0])
            except Exception:
                receipt = None
            if not receipt:
                return 'dragon_receipt_unconfirmed'
            record['dragon_receipt'] = receipt
    failed = any('failed' in outcomes.values() for outcomes in record['outcomes'].values())
    return 'finished_with_failures' if failed else 'passed'


def daily_card(record, rows, base):
    from urllib.parse import urlencode, urlsplit
    outcomes = {}
    for batch in record.get('outcomes', {}).values():
        outcomes.update(batch)
    passed = sum(outcomes.get(r['case_id']) == 'passed' for r in rows)
    failures = [r.get('name') or Path(r['filename']).stem for r in rows if outcomes.get(r['case_id']) == 'failed']
    status = record['status']
    reasons = {
        'passed': '全部通过', 'finished_with_failures': '存在失败用例',
        'skipped_printer_busy_or_unknown': '打印机忙或状态未确认，已跳过实打',
        'skipped_printer_status_unavailable': '打印机遥测不可用，已跳过实打',
        'dragon_receipt_unconfirmed': '打印下发与设备回执尚未对上',
        'runner_changed': '执行手机或 Runner 已变更', 'dispatch_window_ended': '执行时间窗口已结束',
        'stopped_smoke': '冒烟阶段未满足继续执行条件', 'error': '调度中断，需查看工作台记录',
    }
    reason = reasons.get(status, '回归提前结束')
    if status == 'selection_blocked':
        reason = record['selection_error']
    if status.startswith('skipped_phone_busy_or_unavailable_'):
        reason = '测试手机忙或离线'
    if status.startswith('preflight_blocked_'):
        reason = '工作台执行前检查未通过'
    text = (f"计划 {len(rows)} 条　通过 {passed}　失败 {len(failures)}　未完成 {len(rows)-passed-len(failures)}"
            + ('\n失败用例：' + '、'.join(failures) if failures else ''))
    if record.get('schedule_version'):
        target = record['target']
        text += f"\n北京时间 {record['operate_at'][11:16]} · Android · {target['runner_id']} / {target['device_id']}"
    elements = [{'tag': 'div', 'text': {'tag': 'plain_text', 'content': text}}]
    batches = record.get('batches', {})
    labels = {'smoke': '冒烟', 'remaining': '基础功能', 'previews': '建模与导入', 'dragon': '关节龙实打'}
    url = urlsplit(base)
    if url.scheme in {'http','https'} and url.hostname and not (url.username or url.password or url.query or url.fragment):
        links = [f"[{labels.get(stage, '基础功能')}报告]({base.rstrip('/')}/task-manager.html?{urlencode({'ui_batch': bid})})"
                 for stage, bid in batches.items() if re.fullmatch(r'uib_[a-f0-9]{32}', bid)]
        if links:
            elements.append({'tag': 'markdown', 'content': ' · '.join(links)})
    if batches and len(elements) == 1:
        elements.append({'tag': 'div', 'text': {'tag': 'plain_text', 'content': '查看详情：测试工作台 → UI 测试报告'}})
    kind = '手动 UI 回归' if record.get('manual_run_id') else '每日 UI 回归'
    title = f"{record['date']} 智小白 3D {kind}｜{reason}"
    return {'msg_type': 'interactive', 'card': {'header': {'template': 'green' if status == 'passed' else 'orange',
            'title': {'tag': 'plain_text', 'content': title}}, 'elements': elements}}


def daily_sender():
    from task_server.services.business_line_service import configured_test_application
    from task_server.services.feishu_service import resolve_task_app_feishu_channel, send_feishu_card
    from task_server.services.ui_execution_contract import require_ui_actor
    require_ui_actor(permission='ui.view', app_id=APP)
    require_ui_actor(permission='platform.notify', app_id=APP)
    app = configured_test_application(APP)
    if not app or not app.get('enabled') or app.get('historical_only'):
        raise ValueError('app_unavailable')
    channel, target = resolve_task_app_feishu_channel(app)
    if not channel['ready'] or channel['source'] not in {'app', 'package_default'}:
        raise ValueError('app_channel_unavailable')
    return lambda card: send_feishu_card(card, webhook=target)


def deliver_daily(record, path, rows, sender=None, test=False):
    # The caller holds the schedule lock. Persist the intent before the network call.
    if record.get('feishu', {}).get('status') not in (None, 'configuration_error'):
        return
    base = (os.getenv('TASK_PUBLIC_BASE_URL') or os.getenv('MIDSCENE_PUBLIC_BASE_URL')
            or os.getenv('PUBLIC_BASE_URL') or '').strip()
    try:
        send = sender or daily_sender()
        if test:
            card = {'msg_type': 'interactive', 'card': {'header': {'template': 'blue', 'title': {'tag': 'plain_text',
                    'content': '【通知测试】智小白 3D 每日 UI 回归'}}, 'elements': [
                    {'tag': 'div', 'text': {'tag': 'plain_text', 'content': '工作台正在验证每天一条回归汇总的投递。本次没有执行用例，无需处理。'}}]}}
        else:
            card = daily_card(record, rows, base)
    except Exception as exc:
        record['feishu'] = {'status': 'configuration_error', 'error_type': type(exc).__name__}
        write_record(path, record)
        return
    delivery = record['feishu'] = {'status': 'sending', 'attempted_at': now().isoformat()}
    write_record(path, record)
    try:
        reply = send(card)
        code = reply.get('code', reply.get('StatusCode')) if isinstance(reply, dict) else None
        if type(code) is int:
            delivery.update(status='sent' if code == 0 else 'rejected', code=code)
        else:
            delivery['status'] = 'unknown'
    except Exception as exc:
        delivery.update(status='unknown', error_type=type(exc).__name__)
    write_record(path, record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true', help='check identity and pinned catalog without sending jobs')
    group.add_argument('--printer-status', action='store_true', help='read current production telemetry without sending jobs')
    group.add_argument('--test-notification', action='store_true', help='send one labeled Feishu test per day without running any cases')
    group.add_argument('--run', action='store_true')
    args = parser.parse_args()
    from task_server.services.operation_attribution import actor_context
    from task_server.services.ui_execution_contract import require_ui_actor
    from task_server.services.ui_execution_service import _require_ui_admission

    snapshot = schedule_snapshot('android')
    rows = snapshot[1] if snapshot else json.loads(MANIFEST.read_text(encoding='utf-8'))
    phases = plan(rows, selected=bool(snapshot))
    actor = trusted_actor()
    with actor_context(actor):
        require_ui_actor(permission='ui.view', app_id=APP)
        require_ui_actor(permission='ui.execute', app_id=APP)
        if args.test_notification:
            path = STATE / ('feishu-test-' + now().date().isoformat() + '.json')
            with (STATE / 'feishu-test.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                record = json.loads(path.read_text()) if path.exists() else {}
                deliver_daily(record, path, rows, test=True)
                print('feishu_test', record['feishu']['status'])
                if record['feishu']['status'] != 'sent':
                    raise SystemExit(1)
            return
        if not args.run:
            _require_ui_admission()
        if args.printer_status:
            try:
                idle = printer_idle()
            except Exception as error:
                print('printer_status_unavailable', type(error).__name__)
                raise SystemExit(1) from None
            print('printer_idle' if idle else 'printer_busy_or_unknown', MACHINE)
            return
        if args.check:
            validate_published(rows)
            print(f'validated {len(rows)} published revisions, UI admission and schedule owner')
            return
        clock = now()
        start = snapshot[0] if snapshot else clock.replace(hour=5, minute=0, second=0, microsecond=0)
        metadata = snapshot[2] if snapshot else {}
        deadline = start + timedelta(hours=19)
        path = STATE / (metadata.get('manual_run_id', start.date().isoformat()) + '.json')
        with (STATE / 'schedule.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            if path.exists():
                record = json.loads(path.read_text(encoding='utf-8'))
                if record['owner_id'] != actor['user_id'] or record.get('schedule_digest') != metadata.get('schedule_digest'):
                    return
                if record.get('final'):
                    if record.get('notify_daily'):
                        deliver_daily(record, path, rows)
                    return
            else:
                if not start <= clock < (deadline if snapshot else start + timedelta(minutes=2)):
                    raise ValueError('outside_daily_start_window')
                record = {'date': str(start.date()), 'owner_id': actor['user_id'], 'created_at': clock.isoformat(),
                          'operate_at': start.isoformat(), 'status': 'scheduled', 'batches': {}, 'notify_daily': True, **metadata}
                write_record(path, record)
            delay = (datetime.fromisoformat(record['operate_at']) - now()).total_seconds()
            if delay > 0:
                time.sleep(delay)
            try:
                _require_ui_admission()
                if record.get('selection_error'):
                    result = 'selection_blocked'
                else:
                    validate_published(rows)
                    result = run_stages(record, path, phases, deadline)
            except Exception as error:
                # Preserve last known batch IDs if a submission or read is
                # interrupted. Stable date/phase keys recover the same receipts.
                code = str(getattr(error, 'code', ''))
                record.update(status='error', error_type=type(error).__name__,
                              error_code=code if re.fullmatch('[a-z_]{1,80}', code) else '')
                write_record(path, record)
                if record.get('notify_daily'):
                    deliver_daily(record, path, rows)
                raise SystemExit(1) from None
            record.update(final=True, status=result, finished_at=now().isoformat())
            write_record(path, record)
            if record.get('notify_daily'):
                deliver_daily(record, path, rows)


if __name__ == '__main__':
    main()
