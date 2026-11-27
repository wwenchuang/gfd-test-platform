"""Daily Beijing-time starts with a frozen per-day request and restart-safe admission."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import logging
import re
import threading
import time
import uuid

from . import ui_monkey_service as m
from .operation_attribution import actor_context, current_actor
from .ui_execution_contract import AccessDenied, require_ui_actor
from .ui_execution_store import UiExecutionStore, default_db_path

_BEIJING = timezone(timedelta(hours=8))
_WORKER = None


def _slot(now, hhmm):
    hour, minute = map(int, hhmm.split(':'))
    return datetime.fromtimestamp(now, _BEIJING).replace(hour=hour, minute=minute, second=0, microsecond=0)


def _next(now, hhmm):
    slot = _slot(now, hhmm)
    if slot.timestamp() <= now:
        slot += timedelta(days=1)
    return int(slot.timestamp())


def _public(db, row):
    last = db.execute("""SELECT d.run_id,r.status,r.message FROM ui_monkey_schedule_days d
        LEFT JOIN ui_monkey_runs r ON d.run_id=r.run_id WHERE d.schedule_id=?
        ORDER BY d.day DESC LIMIT 1""", (row['schedule_id'],)).fetchone()
    return {**json.loads(row['config_json']), 'schedule_id': row['schedule_id'],
            'next_run_at': datetime.fromtimestamp(row['due_at'], _BEIJING).isoformat() if row['due_at'] else None,
            'last_run_id': last['run_id'] if last else None, 'last_status': last['status'] if last else None,
            'last_message': (last['message'] or '') if last else ''}


def list_schedules(app_id):
    if not isinstance(app_id, str) or not app_id.strip():
        raise m.MonkeyError('app_required', '请选择应用。')
    profile = require_ui_actor(permission='ui.view', app_id=app_id)
    with closing(UiExecutionStore()._connect()) as db:
        rows = db.execute("""SELECT * FROM ui_monkey_schedules WHERE app_id=? AND owner_id=?
            ORDER BY runner_id,device_id""", (app_id, profile['user_id']))
        return [_public(db, row) for row in rows]


def save_schedule(request):
    if (not isinstance(request, dict) or 'request_id' in request or
            type(request.get('enabled')) is not bool or not isinstance(request.get('daily_time'), str) or
            not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', request['daily_time'])):
        raise m.MonkeyError('invalid_schedule', '请选择是否启用，并填写北京时间 HH:MM。')
    run_args = {k:v for k,v in request.items() if k not in ('daily_time', 'enabled')}
    body = m.normalize_request({**run_args, 'request_id':'validate'})
    app_id = body['app_id']
    require_ui_actor(permission='ui.execute', app_id=app_id)
    profile = require_ui_actor(permission='ui.edit', app_id=app_id)
    actor = current_actor()
    config = {k: body[k] for k in ('app_id','target','mode','seed','event_count','duration_seconds')}
    config.update(enabled=request['enabled'], daily_time=request['daily_time'])
    store = UiExecutionStore()
    args = (app_id, body['runner_id'], body['device_id'])
    lookup = 'SELECT * FROM ui_monkey_schedules WHERE app_id=? AND runner_id=? AND device_id=?'
    def accessible(row):
        if row and (row['owner_id'] != profile['user_id'] or json.loads(row['config_json'])['target'] != body['target']):
            raise m.MonkeyError('schedule_conflict', '该应用和设备已有其他方案。', 409)
        return row
    with closing(store._connect()) as db:
        previous = accessible(db.execute(lookup, args).fetchone())
    if not previous:
        m._target(app_id, body['target'])
    with store._transaction() as db:
        row = accessible(db.execute(lookup, args).fetchone())
        if row and json.loads(row['config_json']) == config:
            return _public(db, row)
        schedule_id = row['schedule_id'] if row else 'ms_' + uuid.uuid4().hex
        due_at = _next(time.time(), config['daily_time']) if config['enabled'] else None
        if row and config['enabled']:
            old = json.loads(row['config_json'])
            if old['enabled'] and old['daily_time'] == config['daily_time']:
                due_at = row['due_at']
        db.execute("""INSERT INTO ui_monkey_schedules(schedule_id,owner_id,owner_json,app_id,
            runner_id,device_id,config_json,due_at) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(app_id,runner_id,device_id) DO UPDATE SET config_json=excluded.config_json,due_at=excluded.due_at""",
            (schedule_id, profile['user_id'], json.dumps(dict(actor)), *args, json.dumps(config), due_at))
        return _public(db, db.execute(lookup, args).fetchone())


def _freeze_due(store, now):
    with store._transaction() as db:
        m._expire_unacknowledged(db, time.time_ns())
        rows = list(db.execute('SELECT * FROM ui_monkey_schedules WHERE due_at<=?', (now,)))
        for row in rows:
            config = json.loads(row['config_json'])
            today = _slot(now, config['daily_time'])
            if today.timestamp() <= now:
                day = today.date().isoformat()
                db.execute("""INSERT OR IGNORE INTO ui_monkey_schedule_days
                    (schedule_id,day,request_id,owner_json,config_json) VALUES(?,?,?,?,?)""",
                    (row['schedule_id'], day, 'daily-monkey-'+row['schedule_id']+'-'+day,
                     row['owner_json'], row['config_json']))
            db.execute('UPDATE ui_monkey_schedules SET due_at=? WHERE schedule_id=?',
                       (_next(now, config['daily_time']), row['schedule_id']))
        return list(db.execute('SELECT * FROM ui_monkey_schedule_days WHERE run_id IS NULL ORDER BY day,schedule_id'))


def tick(now=None):
    if not m.enabled() or not default_db_path().exists():
        return
    store = UiExecutionStore()
    now = time.time() if now is None else now
    for day in _freeze_due(store, now):
        config = json.loads(day['config_json'])
        request = {k:v for k,v in config.items() if k not in ('daily_time','enabled')}
        request['request_id'] = day['request_id']
        actor = json.loads(day['owner_json'])
        try:
            with actor_context(actor):
                if day['day'] != datetime.fromtimestamp(now, _BEIJING).date().isoformat():
                    raise m.MonkeyError('schedule_expired','已超过当日执行窗口。')
                require_ui_actor(permission='ui.edit', app_id=config['app_id'])
                run = m.create_run(request, scheduled=True)
        except (AccessDenied, m.MonkeyError) as exc:
            body = m.normalize_request(request)
            message = exc.message if isinstance(exc, m.MonkeyError) else '配置账号已失去此应用的执行或编辑权限。'
            with store._transaction() as db:
                run = m._previous(db, actor['user_id'], body) or m._insert_run(
                    db, body, actor, '', '{}', 'inconclusive', message)
        with store._transaction() as db:
            db.execute('UPDATE ui_monkey_schedule_days SET run_id=? WHERE request_id=? AND run_id IS NULL',
                       (run['run_id'], day['request_id']))


def start_scheduler():
    global _WORKER
    if not m.enabled() or (_WORKER and _WORKER.is_alive()):
        return
    def loop():
        while m.enabled():
            try:
                tick()
            except Exception:
                logging.getLogger(__name__).exception('Daily Monkey schedule check failed')
            time.sleep(30)
    _WORKER = threading.Thread(target=loop, name='ui-monkey-scheduler', daemon=True)
    _WORKER.start()
