"""App-scoped Monkey runs on one explicitly selected phone, with durable stop proof."""
from contextlib import closing
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid

from . import job_service, runner_service
from .operation_attribution import actor_context, current_actor
from .ui_batch_dispatch import DISPATCH_LOCK, enabled, _read_occupancy
from .ui_execution_contract import AccessDenied, require_ui_actor, resolve_ui_target
from .ui_execution_messages import blocker_message
from .ui_execution_store import UiExecutionStore, default_db_path

TARGET = ('platform', 'runner_id', 'device_id')
TERMINAL = {'passed', 'failed', 'stopped', 'crashed', 'inconclusive'}
PUBLIC = ('run_id', 'app_id', 'status', 'executed', 'event_count', 'duration_seconds', 'elapsed_seconds', 'seed', 'mode', 'log',
          'message', 'crash_log', 'created_ns', 'started_ns', 'updated_ns')


class MonkeyError(ValueError):
    def __init__(self, code, message=None, status=400):
        super().__init__(code)
        self.code, self.message, self.status = code, message or blocker_message(code), status


def _public(row):
    stale = row['status'] in ('running', 'stopping') and time.time_ns() - row['updated_ns'] > 120 * 10**9
    return {**{k: row[k] for k in PUBLIC}, 'connection_lost': stale, 'target': {k: row[k] for k in TARGET},
            'finished_ns': row['updated_ns'] if row['status'] in TERMINAL else None,
            'analysis': json.loads(row['analysis']) if row['analysis'] else None}


def _require_row(db, run_id, permission):
    profile = require_ui_actor(permission=permission)
    row = db.execute('SELECT * FROM ui_monkey_runs WHERE run_id=?', (run_id,)).fetchone()
    if not row or (row['owner_id'] != profile['user_id'] and not profile.get('is_superuser')):
        raise MonkeyError('not_found', '任务不存在。', 404)
    require_ui_actor(permission=permission, app_id=row['app_id'])
    return row


def get_run(run_id):
    with closing(UiExecutionStore()._connect()) as db:
        return _public(_require_row(db, run_id, 'ui.view'))


def list_runs(app_id):
    if not isinstance(app_id, str) or not app_id.strip():
        raise MonkeyError('app_required', '请选择应用。')
    profile = require_ui_actor(permission='ui.view', app_id=app_id)
    with closing(UiExecutionStore()._connect()) as db:
        rows = db.execute("""SELECT * FROM ui_monkey_runs WHERE owner_id=? AND app_id=?
            ORDER BY created_ns DESC LIMIT 50""", (profile['user_id'], app_id))
        return [_public(row) for row in rows]


def _target(app_id, target):
    resolved = resolve_ui_target(app_id, target)
    if not resolved['ok']:
        raise MonkeyError(resolved['block_code'], status=409)
    runner = runner_service.list_runners().get(target['runner_id'], {})
    if (runner.get('capabilities') or {}).get('ui_monkey_v1') is not True:
        raise MonkeyError('runner_upgrade_required', '所选 Runner 尚未上报 Monkey 执行能力。', 409)
    versions = resolved['device_snapshot']['app_versions']
    if len(versions) != 1 or versions[0].get('installed') is not True:
        raise MonkeyError('app_version_unavailable', status=409)
    return resolved['target_package'], json.dumps(versions[0], sort_keys=True)


def _check_occupancy(occupancy, device):
    if not (occupancy.get('__source__') or {}).get('available') or not occupancy.get(device):
        raise MonkeyError('occupancy_unavailable', status=409)
    if occupancy[device].get('usage_status') != 'idle':
        raise MonkeyError('device_busy', status=409)


def _expire_unacknowledged(db, now):
    # The driver requires the first authenticated progress response before it can start.
    # A lost claim response leaves no local Runner journal or active process.
    db.execute("""UPDATE ui_monkey_runs SET status='inconclusive',message=?,updated_ns=?
        WHERE status='running' AND started_ns IS NULL AND updated_ns<?""",
        ('Runner 未确认启动，认领已过期。', now, now - 120 * 10**9))
    db.execute("""UPDATE ui_monkey_runs SET status='inconclusive',message=?,updated_ns=?
        WHERE status='queued' AND created_ns<?""",
        ('指定 Runner 未在两分钟内认领，任务已结束。', now, now - 120 * 10**9))


def _previous(db, owner, body):
    row = db.execute('SELECT * FROM ui_monkey_runs WHERE owner_id=? AND request_id=?',
                     (owner, body['request_id'])).fetchone()
    if row and any(row[k] != body[k] for k in ('app_id', *TARGET, 'event_count', 'duration_seconds', 'seed', 'mode')):
        raise MonkeyError('idempotency_conflict', '同一请求编号已用于不同参数。', 409)
    return _public(row) if row else None


def normalize_request(request):
    fields = {'app_id', 'target', 'event_count', 'duration_seconds', 'seed', 'request_id', 'mode'}
    if not isinstance(request, dict):
        raise MonkeyError('invalid_request', '执行参数无效。')
    mode = request.get('mode', 'mixed')
    count, duration = request.get('event_count'), request.get('duration_seconds')
    budget_ok = ((duration is None and type(count) is int and 1 <= count <= 10000) or
                 (count is None and type(duration) is int and 60 <= duration <= 86400))
    if (not isinstance(request, dict) or request.keys() - fields or
            not isinstance(request.get('target'), dict) or set(request['target']) != set(TARGET) or
            any(not isinstance(request['target'][k], str) or not request['target'][k].strip() for k in TARGET) or
            not isinstance(request.get('request_id'), str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', request['request_id']) or
            not isinstance(request.get('app_id'), str) or not request['app_id'].strip() or
            not budget_ok or
            type(request.get('seed')) is not int or not 0 <= request['seed'] <= 2147483647 or
            mode not in ('browse', 'mixed')):
        raise MonkeyError('invalid_request', '请选择应用、手机、模式和有效的事件数及随机种子。')
    return {**request, **request['target'], 'mode': mode,
            'duration_seconds': duration, 'event_count': count}


def _insert_run(db, body, actor, package, version, status='queued', message=''):
    run_id, now = 'mon_' + uuid.uuid4().hex, time.time_ns()
    db.execute("""INSERT INTO ui_monkey_runs(run_id,owner_id,owner_json,app_id,platform,runner_id,
        device_id,target_package,version_json,request_id,event_count,duration_seconds,seed,mode,status,message,created_ns,updated_ns)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (run_id, actor['user_id'], json.dumps({
            k: actor[k] for k in ('kind', 'username', 'user_id', 'display_name')}), body['app_id'],
            *(body[k] for k in TARGET), package, version, body['request_id'], body['event_count'],
            body['duration_seconds'], body['seed'], body['mode'], status, message, now, now))
    return _public(db.execute('SELECT * FROM ui_monkey_runs WHERE run_id=?', (run_id,)).fetchone())


def create_run(request, *, scheduled=False):
    body = normalize_request(request)
    if not scheduled and body['request_id'].startswith('daily-monkey-'):
        raise MonkeyError('invalid_request', '请求编号无效。')
    actor = current_actor()
    owner = require_ui_actor(permission='ui.execute', app_id=body['app_id'])['user_id']
    store = UiExecutionStore()
    with store._transaction() as db:
        _expire_unacknowledged(db, time.time_ns())
        previous = _previous(db, owner, body)
    if previous:
        return previous
    package, version = _target(body['app_id'], body['target'])
    from .ui_schedule_service import schedule_device_busy
    occupancy = _read_occupancy(fresh=True)
    device = body['device_id']
    already_busy = schedule_device_busy(device)
    with DISPATCH_LOCK, job_service.JOB_LOCK, store._transaction() as db:
        previous = _previous(db, owner, body)
        if previous:
            return previous
        if scheduled and not db.execute("""SELECT 1 FROM ui_monkey_schedule_days d
                JOIN ui_monkey_schedules s USING(schedule_id) WHERE d.request_id=? AND s.due_at IS NOT NULL""",
                (body['request_id'],)).fetchone():
            raise MonkeyError('schedule_disabled', '每日方案已停用，本次未执行。', 409)
        _check_occupancy(occupancy, device)
        _, running = job_service.load_ui_dispatch_jobs()
        if (already_busy or running.get(device) or store.monkey_device_reserved(db, device) or
                db.execute('SELECT 1 FROM device_leases WHERE device_id=?', (device,)).fetchone()):
            raise MonkeyError('device_busy', status=409)
        return _insert_run(db, body, actor, package, version)


def claim(runner_id, devices):
    if not devices or not enabled() or not default_db_path().exists():
        return None
    store = UiExecutionStore()
    with closing(store._connect()) as db:
        if db.execute("""SELECT 1 FROM ui_monkey_runs WHERE runner_id=? AND status='running'
                AND started_ns IS NULL AND updated_ns<? LIMIT 1""", (runner_id, time.time_ns()-120*10**9)).fetchone():
            _expire_unacknowledged(db, time.time_ns())
        pending = list(db.execute("""SELECT * FROM ui_monkey_runs WHERE runner_id=?
            AND status='queued' ORDER BY created_ns""", (runner_id,)))
    candidates = [row for row in pending if row['device_id'] in devices]
    if not candidates:
        return None
    occupancy = _read_occupancy(fresh=True)
    for row in candidates:
        device, run_id = row['device_id'], row['run_id']
        try:
            with actor_context(json.loads(row['owner_json'])):
                package, version = _target(row['app_id'], {k: row[k] for k in TARGET})
            if package != row['target_package'] or version != row['version_json']:
                raise MonkeyError('app_version_changed', status=409)
            _check_occupancy(occupancy, device)
        except (MonkeyError, AccessDenied) as exc:
            with store._transaction() as db:
                db.execute("""UPDATE ui_monkey_runs SET status='inconclusive',message=?,updated_ns=?
                    WHERE run_id=? AND status='queued'""", (blocker_message(exc.code), time.time_ns(), run_id))
            continue
        with DISPATCH_LOCK, job_service.JOB_LOCK, store._transaction() as db:
            _, running = job_service.load_ui_dispatch_jobs()
            if running.get(device) or db.execute('SELECT 1 FROM device_leases WHERE device_id=?', (device,)).fetchone():
                continue
            token = secrets.token_urlsafe(32)
            now = time.time_ns()
            updated = db.execute("""UPDATE ui_monkey_runs SET status='running',claim_hash=?,updated_ns=?
                WHERE run_id=? AND status='queued'""", (hashlib.sha256(token.encode()).hexdigest(), now, run_id))
            if updated.rowcount:
                return {**{k: row[k] for k in ('run_id', 'app_id', *TARGET, 'event_count', 'duration_seconds', 'seed', 'mode', 'target_package')},
                        'job_id': run_id, 'job_type': 'ui_monkey', 'monkey_claim_token': token}
    return None


def stop(run_id):
    with UiExecutionStore()._transaction() as db:
        row = _require_row(db, run_id, 'ui.execute')
        status = {'queued': 'stopped', 'running': 'stopping' if row['started_ns'] else 'stopped'}.get(row['status'])
        if status:
            db.execute('UPDATE ui_monkey_runs SET status=?,updated_ns=? WHERE run_id=?', (status, time.time_ns(), run_id))
        return _public(db.execute('SELECT * FROM ui_monkey_runs WHERE run_id=?', (run_id,)).fetchone())


def callback(run_id, kind, body):
    from .ui_monkey_analysis import diagnostic_log, safe_text
    if not isinstance(body, dict) or kind not in ('progress', 'result'):
        raise MonkeyError('event_invalid', '无效的 Runner 回报。')
    token = body.get('monkey_claim_token')
    with UiExecutionStore()._transaction() as db:
        row = db.execute('SELECT * FROM ui_monkey_runs WHERE run_id=?', (run_id,)).fetchone()
        if (not row or not isinstance(token, str) or not token or
                not hmac.compare_digest(row['claim_hash'], hashlib.sha256(token.encode()).hexdigest()) or
                any(body.get(k) != row[k] for k in ('runner_id', 'device_id'))):
            raise MonkeyError('claim_mismatch', '任务认领凭据不匹配。', 403)
        if row['status'] in TERMINAL:
            return {'ignored': 'terminal_job', 'status': row['status'], 'stop_requested': True}
        count = body.get('executed')
        duration = row['duration_seconds']
        elapsed = body.get('elapsed_seconds', None if duration else row['elapsed_seconds'])
        limit = row['event_count'] if duration is None else 2**63 - 1
        if (type(count) is not int or not 0 <= count <= limit or
                type(elapsed) is not int or not 0 <= elapsed <= 2**63 - 1):
            raise MonkeyError('event_invalid', '操作计数超出范围。')
        status = row['status']
        log = safe_text(body.get('log'), 16000, token)
        message = safe_text(body.get('message'), 500, token)
        crash = ''
        if kind == 'result':
            status = body.get('status')
            if (body.get('process_stopped') is not True or not isinstance(status, str) or status not in TERMINAL or count < row['executed'] or
                    elapsed < row['elapsed_seconds'] or (status == 'passed' and
                        ((duration is None and count != row['event_count']) or
                         (duration is not None and (elapsed < duration or count == 0))))):
                raise MonkeyError('result_invalid', '缺少进程退出回执或操作结果不完整。', 409)
            if status in ('crashed', 'failed'):
                crash = diagnostic_log(safe_text(body.get('crash_log'), 16000, token) or log, row['platform'])
                if status == 'crashed' and not crash:
                    status, message = 'inconclusive', '缺少本轮目标应用的崩溃记录。'
        if count >= row['executed'] and elapsed >= row['elapsed_seconds']:
            db.execute("""UPDATE ui_monkey_runs SET status=?,executed=?,elapsed_seconds=?,message=?,log=?,crash_log=?,updated_ns=?,
                started_ns=COALESCE(started_ns,?) WHERE run_id=?""",
                (status, count, elapsed, message or row['message'], log or row['log'], crash, time.time_ns(),
                 time.time_ns() if kind == 'progress' else None, run_id))
        return {'status': status, 'stop_requested': status == 'stopping'}


def analyze(run_id):
    from .ui_monkey_analysis import analysis_lock, analyze_evidence
    lock = analysis_lock(run_id)
    if not lock.acquire(blocking=False):
        raise MonkeyError('analysis_busy', '此任务的日志正在分析。', 409)
    try:
        store = UiExecutionStore()
        with closing(store._connect()) as db:
            row = _require_row(db, run_id, 'ui.execute')
            if row['analysis']:
                return _public(row)
        if not row['crash_log'] or row['status'] not in ('crashed', 'failed'):
            raise MonkeyError('crash_evidence_missing', '本轮没有可分析的目标应用崩溃或 ANR 证据。', 409)
        try:
            result = analyze_evidence(row)
        except (ValueError, OSError, TimeoutError):
            raise MonkeyError('analysis_unavailable', '模型暂时无法返回有效分析，原始测试结果已保留。', 503) from None
        with store._transaction() as db:
            _require_row(db, run_id, 'ui.execute')
            db.execute('UPDATE ui_monkey_runs SET analysis=? WHERE run_id=? AND analysis IS NULL',
                       (json.dumps(result, ensure_ascii=False), run_id))
            return _public(db.execute('SELECT * FROM ui_monkey_runs WHERE run_id=?', (run_id,)).fetchone())
    finally:
        lock.release()
