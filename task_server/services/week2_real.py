"""Analyze one existing API execution with the caller's platform authorization."""
import hashlib
import json
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from task_server import identity
from task_server.services import week1_smoke

EXECUTION_ID = 'de803f8b-e18b-4e67-986d-b14ce737b8aa'
CASE_ID = '22dc4d0b-d319-4ce8-a9e6-08aede8ad4af'
_TIMEOUT_SECONDS = 30
_LEASE_SECONDS = 120
_FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures' / 'week2_real'
_WORKER_LOCK = threading.Lock()
_WORKERS = []


class PracticeBusy(Exception):
    pass


class AmbiguousCase(ValueError):
    pass


def _db(path):
    actual = Path(path) if path else identity.default_db_path().with_name('week2-real.sqlite3')
    return week1_smoke._db(actual)


def _claim(path):
    token = uuid.uuid4().hex
    with _db(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT until_time FROM lease WHERE id=1').fetchone()
        if row and row[0] > time.time(): raise PracticeBusy()
        db.execute('INSERT OR REPLACE INTO lease VALUES (1, ?, ?)', (time.time() + _LEASE_SECONDS, token))
    return token


def _release(path, token):
    with _db(path) as db:
        db.execute('DELETE FROM lease WHERE id=1 AND token=?', (token,))


def _save(path, owner, result, created):
    with _db(path) as db:
        db.execute('INSERT OR REPLACE INTO results VALUES (?,?,?,?)',
                   (result['id'], owner, created, json.dumps(result, ensure_ascii=False)))


def authorized_actor(owner, username):
    """Never allow compatibility fallback for unknown users on this route."""
    profile = identity.get_access_profile(username) if username else None
    return bool(profile and profile.get('user_id') == owner and profile.get('status') == 'active'
                and not profile.get('must_change_password', False)
                and (profile.get('is_superuser') or 'api.view' in profile.get('permissions', ())))


def _fetch_authorized(username, execution_id, case_id):
    # Reuse the API HTTP adapter, including its project/environment scope checks.
    # Its authentication is performed by the caller; actor is a trusted username,
    # never an administrator chosen by this service or a client-provided value.
    from task_server.api_testing import access, http
    from task_server.api_testing.config import ApiTestingSettings
    settings = ApiTestingSettings.from_env()
    if not settings.enabled:
        raise ValueError('api_testing_disabled')
    actor = username
    exec_segments = ('executions', execution_id)
    case_segments = ('executions', execution_id, 'cases', case_id)
    access.authorize_http(actor, 'GET', exec_segments)
    execution = http._get(exec_segments, {}, actor, settings)
    if case_id is None:
        records = execution.get('execution', {}).get('case_results')
        if isinstance(records, (list, tuple)) and len(records) > 1:
            raise AmbiguousCase('ambiguous_case')
        if not isinstance(records, (list, tuple)) or len(records) != 1 or not isinstance(records[0], dict):
            raise ValueError('case_unavailable')
        case_id = str(uuid.UUID(records[0].get('execution_case_id', '')))
        case_segments = ('executions', execution_id, 'cases', case_id)
    access.authorize_http(actor, 'GET', case_segments)
    case = http._get(case_segments, {}, actor, settings)
    return {'execution': execution.get('execution'), 'case_result': case.get('case_result')}


def _scope_authorized(username, execution_id, case_id):
    from task_server.api_testing import access, http
    access.authorize_http(username, 'GET', ('executions', execution_id))
    factory = http._factory()
    execution = http._scope_execution(factory, execution_id, username)
    if case_id:
        child = http._scope_execution_case(factory, case_id, username)
        if child.execution_id != execution.id:
            raise ValueError('not_found')


def _analyze(payload, execution_id, case_id):
    source_request = payload.get('case_result', {}).get('sanitized_result', {}).get('request', {})
    url = urlsplit(source_request.get('url', ''))
    if (source_request.get('method') != 'GET' or url.scheme != 'https'
            or url.hostname != 'print.wisebeginner3d.com' or url.port not in (None, 443)
            or url.username or url.password or url.path != '/app/print3d/api/v1/feedback/category/first'):
        raise ValueError('source_not_allowed')
    # Compile exactly the bytes whose hash is recorded; no Python path changes.
    files = [(name, (_FIXTURES / name).read_bytes()) for name in ('evidence.py', 'checks.py')]
    fingerprint = hashlib.sha256()
    modules = {}
    for name, source in files:
        fingerprint.update(name.encode('utf-8') + b'\0' + source + b'\0')
        namespace = {'__name__': '_week2_real_' + name[:-3]}
        exec(compile(source, name, 'exec'), namespace)
        modules[name] = namespace
    try:
        parsed = modules['evidence.py']['parse_evidence'](payload, execution_id, case_id)
    except modules['evidence.py']['EvidenceError'] as error:
        if getattr(error, 'source_failed', False):
            return {'status': 'failed', 'execution_case_id': case_id, 'original_assertions': [], 'checks': [], 'total': 0,
                    'business_http_status': None, 'code_sha256': fingerprint.hexdigest()}
        raise
    checks = modules['checks.py']['run_checks'](parsed)
    allowed = {'status_code', 'json_path', 'schema'}
    original = [{'type': item.get('type') if item.get('type') in allowed else 'other',
                 'passed': item['passed']} for item in parsed['assertions']]
    failed = parsed['source_status'] == 'FAILED' or any(not item['passed'] for item in original) or any(item['status'] == 'failed' for item in checks)
    complete = (len(original) >= 3 and allowed <= {item['type'] for item in original}
                and len(checks) >= 12 and len({item['id'] for item in checks}) == len(checks))
    blocked = not complete or any(item['status'] == 'blocked' for item in checks)
    return {'status': 'failed' if failed else 'blocked' if blocked else 'passed', 'execution_case_id': case_id,
            'original_assertions': original, 'checks': checks, 'total': len(checks),
            'business_http_status': parsed['http_status'], 'code_sha256': fingerprint.hexdigest()}


def run(owner, username, path=None, *, execution_id=EXECUTION_ID, case_id=CASE_ID):
    if not isinstance(owner, str) or not owner or not isinstance(username, str) or not username:
        raise ValueError('invalid_actor')
    try:
        execution_id = str(uuid.UUID(execution_id))
        case_id = str(uuid.UUID(case_id)) if case_id is not None else None
    except (TypeError, ValueError, AttributeError):
        raise ValueError('invalid_identifier') from None
    token = _claim(path)
    created = time.time()
    result = {'id': uuid.uuid4().hex, 'created_at': datetime.now(timezone.utc).isoformat(),
              'status': 'running', 'source': 'platform_record', 'source_verification': 'pending',
              'execution_id': execution_id, 'execution_case_id': case_id,
              'original_assertions': [], 'checks': [], 'total': 0, 'business_requests': 0,
              'cleanup': 'not_applicable_no_business_data_created', 'code_sha256': None,
              'message': '正在按当前账号权限读取平台记录。'}
    try:
        _save(path, owner, result, created)
    except BaseException:
        _release(path, token)
        raise
    output = queue.Queue(maxsize=1)
    def worker():
        ok, outcome = False, None
        try:
            if not authorized_actor(owner, username): raise ValueError('actor_not_authorized')
            source = _fetch_authorized(username, execution_id, case_id)
            # Recheck the same identity before deriving or returning data.
            if not authorized_actor(owner, username): raise ValueError('actor_not_authorized')
            resolved_case_id = case_id or str(uuid.UUID(source['case_result']['execution_case_id']))
            outcome, ok = _analyze(source, execution_id, resolved_case_id), True
            _scope_authorized(username, execution_id, resolved_case_id)
        except AmbiguousCase:
            try:
                _scope_authorized(username, execution_id, None)
                ok, outcome = False, 'ambiguous_case'
            except Exception:
                ok, outcome = False, None
        except Exception:
            ok, outcome = False, None
        finally:
            try:
                _release(path, token)
            except Exception:
                ok, outcome = False, None
            output.put((ok, outcome))
    try:
        with _WORKER_LOCK:
            _WORKERS[:] = [item for item in _WORKERS if item.is_alive()]
            if _WORKERS:
                _release(path, token)
                raise PracticeBusy()
            thread = threading.Thread(target=worker, daemon=True)
            _WORKERS.append(thread)
            thread.start()
    except PracticeBusy:
        result.update(status='blocked', message='上一次分析仍在结束，请稍后重试。')
        _save(path, owner, result, created)
        raise
    except Exception:
        _release(path, token)
        result.update(status='blocked', message='分析任务无法启动。')
        _save(path, owner, result, created)
        return result
    try:
        ok, data = output.get(timeout=_TIMEOUT_SECONDS)
    except queue.Empty:
        ok, data = False, None
    if not authorized_actor(owner, username):
        ok, data = False, None
    if ok:
        result.update(data, source_verification='server_authorized_platform_read',
                      message='已按当前账号权限读取既有记录；检查为本地数据处理，未重跑业务接口。')
    else:
        result.update(status='blocked', message='该记录包含多条子用例，请填写子用例编号。' if data == 'ambiguous_case' else '记录不可读取、权限不足、分析超时或来源无法核对。')
    _save(path, owner, result, created)
    return result


def list_results(owner, path=None, *, username=None):
    if not isinstance(owner, str) or not owner: raise ValueError('invalid_actor')
    with _db(path) as db:
        rows = db.execute('SELECT created, payload FROM results WHERE owner=? ORDER BY created DESC LIMIT 50', (owner,)).fetchall()
    result = []
    scopes = {}
    for created, raw in rows:
        item = json.loads(raw)
        if username:
            source = (item.get('execution_id'), item.get('execution_case_id'))
            if source not in scopes:
                try:
                    _scope_authorized(username, *source)
                    scopes[source] = True
                except Exception:
                    scopes[source] = False
            if not scopes[source]:
                result.append({'id': item.get('id'), 'created_at': item.get('created_at'),
                               'status': 'blocked', 'message': '当前无法核验来源权限，详情已隐藏。'})
                continue
        if item['status'] == 'running' and created + _LEASE_SECONDS < time.time():
            item.update(status='blocked', message='分析中断，请重新尝试。')
        result.append(item)
    return result
