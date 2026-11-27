"""Edit the two installed mobile schedules; keep their runner inputs pinned on disk."""
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from . import case_service, ui_schedule_clock as clock
from .ui_case_compiler import compile_ui_case
from .ui_case_platforms import platform_binding
from .ui_execution_catalog import get_ui_devices
from .ui_execution_service import preflight_ui_batch
from .ui_execution_contract import require_ui_actor, resolve_ui_target

APP = 'com.kfb.model'
MANIFESTS = {
    'android': Path('/opt/midscene-3d-daily/current/baselines.json'),
    'ios': Path('/opt/midscene-3d-ios-nightly/current/baselines.json'),
}
TIME = re.compile(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]')
REVISION = re.compile(r'[0-9a-f]{64}')


class ScheduleError(ValueError):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(code)


def _unavailable():
    return ScheduleError('schedule_unavailable', '定时任务配置不可读取，请检查安装状态。', 503)


def _platform(platform):
    if platform not in MANIFESTS:
        raise ScheduleError('invalid_platform', '没有此平台的定时任务。', 404)


def _target(platform, target):
    if (not isinstance(target, dict) or set(target) != {'platform', 'device_id', 'runner_id'}
            or target['platform'] != platform or any(not isinstance(v, str) or not v.strip()
            or v.strip() != v or len(v) > 256 for v in target.values())):
        raise ScheduleError('invalid_target', '请选择当前平台的设备和 Runner。')
    return dict(target)


def _time(value):
    if not isinstance(value, str) or not TIME.fullmatch(value):
        raise ScheduleError('invalid_time', '时间须为 00:00 至 23:59。')
    return value


def _pinned_rows(rows):
    if not isinstance(rows, list) or not rows or len(rows) > 200:
        raise _unavailable()
    cases = {}
    for row in rows:
        if (not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k]
                for k in ('case_id', 'filename', 'module', 'sha256'))
                or not REVISION.fullmatch(row['sha256']) or row['case_id'] in cases):
            raise _unavailable()
        case = {k: row[k] for k in ('case_id', 'filename', 'module')}
        case.update(revision=row['sha256'], task_name=row.get('name') or Path(row['filename']).stem)
        if row.get('deferred'):
            case['deferred'] = row['deferred']
        cases[case['case_id']] = case
    return cases


def _read(platform):
    try:
        doc = json.loads((clock.CONFIG_DIR / (platform + '.json')).read_text(encoding='utf-8'))
        if (not isinstance(doc, dict) or doc.get('platform') != platform
                or not isinstance(doc.get('version'), str) or not doc['version']
                or not isinstance(doc.get('effective_at'), str)
                or not isinstance(doc.get('cases'), list) or not doc['cases']):
            raise _unavailable()
        _time(doc.get('time'))
        _target(platform, doc.get('target'))
        ids = set()
        for case in doc['cases']:
            if (not isinstance(case, dict) or not all(isinstance(case.get(k), str) and case[k]
                    for k in ('case_id', 'filename', 'module', 'revision', 'task_name'))
                    or not REVISION.fullmatch(case['revision']) or case['case_id'] in ids):
                raise _unavailable()
            ids.add(case['case_id'])
        return doc
    except (ValueError, OSError, UnicodeError) as error:
        raise _unavailable() from error


def _write(platform, time_string, target, cases):
    doc = dict(platform=platform, time=time_string, target=target, cases=cases,
               version=uuid4().hex, effective_at=datetime.now(timezone.utc).isoformat())
    clock.write_atomic(clock.CONFIG_DIR / (platform + '.json'), doc)
    return doc


def seed_schedule(platform, time_string, target, manifest_rows):
    """Installer entry point. Existing administrator edits survive reinstalls."""
    _platform(platform)
    with clock.config_lock(platform):
        if (clock.CONFIG_DIR / (platform + '.json')).exists():
            return _read(platform)
        cases = _pinned_rows(manifest_rows)
        unnamed = {r['case_id'] for r in manifest_rows if not r.get('name')}
        if unnamed:
            for asset in case_service.list_task_case_assets():
                key = asset.get('case_id')
                if key not in unnamed or asset.get('app_package') != APP or not asset.get('task_name'):
                    continue
                row = cases[key]
                if (asset.get('module'), asset.get('file')) == (row['module'], row['filename']):
                    row['task_name'] = asset['task_name']
        return _write(platform, _time(time_string), _target(platform, target), list(cases.values()))


def _public(doc):
    return {**doc, 'next_run_at': clock.next_run_at(doc['platform'], doc['time'])}


def _require_enabled():
    if not (clock.CONFIG_DIR / 'enabled').is_file():
        raise ScheduleError('schedule_inactive', '定时任务尚未完成启用。', 503)


def list_schedules():
    require_ui_actor(permission='ui.view', app_id=APP)
    _require_enabled()
    return [_public(_read(platform)) for platform in MANIFESTS]


def _available_cases(platform, target):
    devices = get_ui_devices(None, APP)['devices']
    if not any(row['target'] == target and row['connection_status'] == 'ready' for row in devices):
        raise ScheduleError('device_unavailable', '所选设备或 Runner 未就绪，或未安装此应用。')
    resolved = resolve_ui_target(APP, target, permission='ui.view')
    if not resolved['ok']:
        raise ScheduleError('target_unavailable', '当前 Runner 无法执行所选平台的用例。')
    try:
        pinned = _pinned_rows(json.loads(MANIFESTS[platform].read_text(encoding='utf-8')))
    except (OSError, ValueError) as error:
        raise _unavailable() from error
    eligible = {}
    seen = set()
    for asset in case_service.list_task_case_assets():
        case_id = asset.get('case_id')
        row = pinned.get(case_id)
        if not row or row.get('deferred') or asset.get('app_package') != APP:
            continue
        if asset.get('error') or asset.get('status') not in ('active', 'baseline'):
            continue
        if case_id in seen:
            eligible.pop(case_id, None)
            continue
        seen.add(case_id)
        if ((asset.get('module'), asset.get('file')) != (row['module'], row['filename'])
                or platform not in platform_binding(asset)[0]):
            continue
        source = case_service.task_case_yaml(asset)
        if compile_ui_case({'yaml_content': source}, resolved)['ok']:
            eligible[case_id] = dict(row, revision=hashlib.sha256(source.encode()).hexdigest(),
                                     task_name=row['task_name'] if platform == 'ios' else asset.get('task_name') or row['task_name'])
    return [eligible[k] for k in pinned if k in eligible]


def schedule_options(platform, target):
    _platform(platform)
    require_ui_actor(permission='ui.view', app_id=APP)
    _require_enabled()
    _read(platform)
    target = _target(platform, target)
    return {'target': target, 'cases': _available_cases(platform, target)}


def save_schedule(platform, body):
    _platform(platform)
    for permission in ('ui.edit', 'ui.execute'):
        require_ui_actor(permission=permission, app_id=APP)
    _require_enabled()
    if not isinstance(body, dict) or set(body) != {'time', 'target', 'cases', 'version'}:
        raise ScheduleError('invalid_request', '请提交时间、设备、用例和原配置版本。')
    time_string, target = _time(body['time']), _target(platform, body['target'])
    requested = body['cases']
    if not isinstance(requested, list) or not 1 <= len(requested) <= 200:
        raise ScheduleError('invalid_cases', '请至少选择一条可执行用例，最多 200 条。')
    def latest_version():
        current = _read(platform)
        if body['version'] != current['version']:
            raise ScheduleError('version_conflict', '配置已被更新，请刷新后重新编辑。', 409)
        return current

    latest_version()
    available = {r['case_id']: r for r in _available_cases(platform, target)}
    selected = []
    for choice in requested:
        if (not isinstance(choice, dict) or set(choice) != {'case_id', 'revision'}
                or not isinstance(choice['case_id'], str)):
            raise ScheduleError('invalid_cases', '用例编号和版本无效。')
        row = available.pop(choice['case_id'], None)
        if not row or row['revision'] != choice['revision']:
            raise ScheduleError('case_unavailable', '用例版本已变化、重复或当前设备不支持，请刷新后重新选择。')
        selected.append(row)
    # Keep catalog scanning and compilation outside the timer's claim lock.
    with clock.config_lock(platform):
        _require_enabled()
        current = latest_version()
        selected.extend(row for row in current['cases'] if row.get('deferred'))
        doc = _write(platform, time_string, target, selected)
    return _public(doc)


def schedule_device_busy(phone):
    from . import job_service
    from .ui_execution_store import UiExecutionStore
    store = UiExecutionStore()
    if store.device_reserved(phone):
        return True
    # Pending UI work can exist before the first device lease is acquired.
    db = store._connect()
    try:
        if db.execute("""SELECT 1 FROM case_attempts a JOIN batches b USING(ui_batch_id)
                WHERE a.device_id=? AND b.execution_settled=0 LIMIT 1""", (phone,)).fetchone():
            return True
    finally:
        db.close()
    for job in job_service._read_jobs_raw(use_cache=False):
        device_id = job.get('device_id') or job.get('deviceId')
        status = job_service.normalize_job_status(job.get('status') or job.get('state'))
        if device_id == phone and status in {'pending', 'dispatched', 'running'}:
            return True
    return False


def latest_selection(doc):
    """Freeze the published revision of each selected identity; preserve deferred rows and order."""
    selected = {row['case_id']: row for row in doc['cases'] if not row.get('deferred')}
    available = {}
    for asset in case_service.list_task_case_assets():
        case_id = asset.get('case_id')
        if case_id not in selected or asset.get('app_package') != APP:
            continue
        if asset.get('status') not in ('active', 'baseline') or asset.get('error'):
            continue
        if case_id in available:
            raise ScheduleError('case_unavailable', '已发布用例编号重复：' + selected[case_id]['task_name'], 409)
        row = selected[case_id]
        if ((asset.get('module'), asset.get('file')) != (row['module'], row['filename'])
                or doc['platform'] not in platform_binding(asset)[0]):
            raise ScheduleError('case_unavailable', '用例与所选平台或文件不匹配：' + row['task_name'], 409)
        available[case_id] = hashlib.sha256(case_service.task_case_yaml(asset).encode()).hexdigest()
    missing = selected.keys() - available.keys()
    if missing:
        raise ScheduleError('case_unavailable', '已选用例缺少已发布版本：' + '、'.join(selected[k]['task_name'] for k in sorted(missing)), 409)
    return dict(doc, cases=[dict(row, revision=available[row['case_id']]) if not row.get('deferred') else row
                            for row in doc['cases']])


def _check_manual_selection(doc):
    target = doc['target']
    if not any(row['target'] == target and row['connection_status'] == 'ready'
               for row in get_ui_devices(None, APP)['devices']):
        raise ScheduleError('device_unavailable', '已保存的设备或 Runner 未就绪。', 409)
    cases = [{k: row[k] for k in ('case_id', 'revision')} for row in doc['cases'] if not row.get('deferred')]
    if not cases:
        raise ScheduleError('case_unavailable', '没有可执行的已保存用例。', 409)
    if schedule_device_busy(target['device_id']):
        raise ScheduleError('device_busy', '已保存的设备有正在执行或排队的任务。', 409)
    check = preflight_ui_batch(None, {'app_id': APP, 'targets': [target], 'cases': cases,
                                     'release_report': {'enabled': True}, 'notifications': {'enabled': False}})
    if not check['ready']:
        message = next((r.get('message') for r in check['blockers'] if r.get('message')), '请检查设备和用例版本。')
        raise ScheduleError('preflight_blocked', '手动执行预检未通过：' + message, 409)


def run_schedule(platform, body):
    _platform(platform)
    require_ui_actor(permission='ui.edit', app_id=APP)
    profile = require_ui_actor(permission='ui.execute', app_id=APP)
    require_ui_actor(permission='ui.view', app_id=APP)
    _require_enabled()
    if (not isinstance(body, dict) or set(body) != {'request_id', 'version'}
            or not isinstance(body['request_id'], str)
            or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', body['request_id'])
            or not isinstance(body['version'], str)):
        raise ScheduleError('invalid_request', '请提交已保存的配置版本和本次请求编号。')

    key = json.dumps([platform, profile['user_id'], body['request_id']]).encode()
    run_id = 'manual-' + hashlib.sha256(key).hexdigest()[:32]
    directory = clock.manual_dir(platform)

    def existing():
        for suffix in ('finished', 'failed', 'running', 'queued'):
            path = directory / (run_id + '.' + suffix)
            if path.exists():
                if json.loads(path.read_text())['version'] != body['version']:
                    raise ScheduleError('version_conflict', '请求编号已用于其他版本，请刷新后重试。', 409)
                return {'run_id': run_id, 'status': suffix}

    def current():
        doc = _read(platform)
        if doc['version'] != body['version']:
            raise ScheduleError('version_conflict', '配置已更新，请刷新后运行已保存的版本。', 409)
        return doc

    with clock.config_lock(platform):
        prior = existing()
        if prior:
            return prior
        doc = current()

    try:
        doc = latest_selection(doc)
        _check_manual_selection(doc)
    except (ValueError, OSError):
        # A simultaneous submission can have started this very run during
        # preflight. Return its receipt even if the phone is now occupied.
        with clock.config_lock(platform):
            prior = existing()
            if prior:
                return prior
        raise

    with clock.config_lock(platform):
        prior = existing()
        if prior:
            return prior
        _require_enabled()
        current()
        with (clock.CONFIG_DIR / (platform + '.worker.lock')).open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ScheduleError('run_conflict', '此平台的回归正在执行，请稍后重试。', 409) from None
            directory.mkdir(parents=True, exist_ok=True)
            if any(directory.glob('*.queued')) or any(directory.glob('*.running')):
                raise ScheduleError('run_conflict', '此平台已有手动回归等待或正在执行。', 409)
            snapshot = dict(doc, manual_run_id=run_id, requested_by=profile['user_id'],
                            requested_at=datetime.now(clock.LOCAL).isoformat())
            clock.write_atomic(directory / (run_id + '.queued'), snapshot)
    return {'run_id': run_id, 'status': 'queued'}
