"""Analyze one existing API execution with the caller's platform authorization."""
import hashlib
import fcntl
import json
import os
import signal
import stat
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from task_server import identity
from task_server.services import week1_smoke

EXECUTION_ID = 'de803f8b-e18b-4e67-986d-b14ce737b8aa'
CASE_ID = '22dc4d0b-d319-4ce8-a9e6-08aede8ad4af'
_TIMEOUT_SECONDS = 30
_HISTORY_TIMEOUT_SECONDS = 15
_HISTORY_SLOTS = 2
_LEASE_SECONDS = 120
_FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures' / 'week2_real'


class PracticeBusy(Exception):
    pass


class AmbiguousCase(ValueError):
    pass


class WorkerCleanupUncertain(Exception):
    pass


class HistoryBusy(Exception):
    pass


def _db(path):
    actual = Path(path) if path else identity.default_db_path().with_name('week2-real.sqlite3')
    return week1_smoke._db(actual)


@contextmanager
def _process_slot(path, kind, count, busy_error):
    # A passed descriptor retains its flock in the child if the parent dies.
    db_path = Path(path) if path else identity.default_db_path().with_name('week2-real.sqlite3')
    for index in range(count):
        lock_path = db_path.with_name('.' + db_path.name + '.' + kind + '-' + str(index) + '.lock')
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid()):
                raise PermissionError('invalid history lock')
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
        except BaseException:
            os.close(fd)
            raise
        try:
            yield fd
        finally:
            # Do not explicitly unlock: a still-running child shares this lock.
            os.close(fd)
        return
    raise busy_error()


def _history_slot(path):
    return _process_slot(path, 'history-auth', _HISTORY_SLOTS, HistoryBusy)


def _analysis_slot(path):
    return _process_slot(path, 'analysis', 1, PracticeBusy)


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
    permissions = (profile or {}).get('permissions') or ()
    scope = (profile or {}).get('scope') or {}
    return bool(profile and profile.get('user_id') == owner and profile.get('status') == 'active'
                and not profile.get('must_change_password', False)
                and (profile.get('is_superuser') or
                     ('api.view' in permissions and 'platform.configure' in permissions
                      and isinstance(scope, dict) and scope.get('ui_apps') == '*')))


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


def _derive(owner, username, execution_id, case_id):
    try:
        if not authorized_actor(owner, username):
            raise ValueError('actor_not_authorized')
        source = _fetch_authorized(username, execution_id, case_id)
        if not authorized_actor(owner, username):
            raise ValueError('actor_not_authorized')
        resolved_case_id = case_id or str(uuid.UUID(source['case_result']['execution_case_id']))
        outcome = _analyze(source, execution_id, resolved_case_id)
        _scope_authorized(username, execution_id, resolved_case_id)
        return True, outcome
    except AmbiguousCase:
        try:
            _scope_authorized(username, execution_id, None)
            return False, 'ambiguous_case'
        except Exception:
            return False, None
    except Exception:
        return False, None


def _run_isolated(command, request_body, timeout, slot_fd=None):
    if timeout <= 0:
        raise subprocess.TimeoutExpired(command, timeout)
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, start_new_session=True,
                               pass_fds=(slot_fd,) if slot_fd is not None else ())
    try:
        output, _ = process.communicate(input=request_body, timeout=timeout)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as error:
            raise WorkerCleanupUncertain() from error
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired as error:
                raise WorkerCleanupUncertain() from error
        raise
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command)
    return output


def _invoke_worker(owner, username, execution_id, case_id, timeout=None, slot_fd=None):
    request_body = json.dumps({'operation': 'analyze', 'arguments': [owner, username, execution_id, case_id]})
    output = _run_isolated([sys.executable, '-m', 'task_server.services.week2_real'],
                           request_body, _TIMEOUT_SECONDS if timeout is None else timeout, slot_fd=slot_fd)
    result = json.loads(output)
    if not isinstance(result, list) or len(result) != 2 or not isinstance(result[0], bool):
        raise ValueError('invalid_worker_result')
    return result


def _verify_sources(owner, username, sources):
    verified = []
    for execution_id, case_id in sources:
        try:
            execution_id = str(uuid.UUID(execution_id))
            case_id = str(uuid.UUID(case_id)) if case_id is not None else None
            if not authorized_actor(owner, username):
                raise ValueError('actor_not_authorized')
            _scope_authorized(username, execution_id, case_id)
            if not authorized_actor(owner, username):
                raise ValueError('actor_not_authorized')
            verified.append(True)
        except Exception:
            verified.append(False)
    return verified


def _invoke_authorization(owner, username, sources, timeout, slot_fd=None):
    body = json.dumps({'operation': 'authorize', 'arguments': [owner, username, sources]})
    raw = _run_isolated([sys.executable, '-m', 'task_server.services.week2_real'], body, timeout, slot_fd=slot_fd)
    verified = json.loads(raw)
    if not isinstance(verified, list) or len(verified) != len(sources) or any(type(item) is not bool for item in verified):
        raise ValueError('invalid_authorization_result')
    return verified


def run(owner, username, path=None, *, execution_id=EXECUTION_ID, case_id=CASE_ID):
    # The advisory lock is inherited by each child, so an orphan remains counted
    # even if the parent dies and the SQLite lease later expires.
    database = _db(path)
    database.close()
    with _analysis_slot(path) as slot:
        return _run_in_slot(owner, username, path, execution_id=execution_id, case_id=case_id, slot_fd=slot)


def _run_in_slot(owner, username, path, *, execution_id, case_id, slot_fd):
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
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    ok, data, release_lease = False, None, True
    try:
        ok, data = _invoke_worker(owner, username, execution_id, case_id, timeout=deadline - time.monotonic(), slot_fd=slot_fd)
        resolved_case = data.get('execution_case_id') if ok and isinstance(data, dict) else case_id
        if ok and (not isinstance(data, dict) or resolved_case is None
                   or (case_id is not None and resolved_case != case_id)):
            raise ValueError('invalid_worker_result')
        if ok or data == 'ambiguous_case':
            if not _invoke_authorization(owner, username, [(execution_id, resolved_case)],
                                         deadline - time.monotonic(), slot_fd=slot_fd)[0]:
                ok, data = False, None
        if ok:
            result.update(data, source_verification='server_authorized_platform_read',
                          message='已按当前账号权限读取既有记录；检查为本地数据处理，未重跑业务接口。')
        else:
            result.update(status='blocked', message='该记录包含多条子用例，请填写子用例编号。' if data == 'ambiguous_case' else '记录不可读取、权限不足、分析超时或来源无法核对。')
    except WorkerCleanupUncertain:
        release_lease = False
        result.update(status='blocked', message='分析进程未确认结束，请稍后再试。')
    except Exception:
        result.update(status='blocked', message='记录不可读取、权限不足、分析超时或来源无法核对。')
    try:
        _save(path, owner, result, created)
    finally:
        if release_lease:
            _release(path, token)
    return result


def list_results(owner, path=None, *, username=None):
    if not isinstance(owner, str) or not owner: raise ValueError('invalid_actor')
    with _db(path) as db:
        rows = db.execute('SELECT created, payload FROM results WHERE owner=? ORDER BY created DESC LIMIT 50', (owner,)).fetchall()
    result = []
    scopes = {}
    hidden_message = '当前无法核验来源权限，详情已隐藏。'
    if username and rows:
        sources = list(dict.fromkeys((json.loads(raw).get('execution_id'), json.loads(raw).get('execution_case_id')) for _, raw in rows))
        try:
            with _history_slot(path) as slot:
                scopes = dict(zip(sources, _invoke_authorization(owner, username, sources, _HISTORY_TIMEOUT_SECONDS, slot_fd=slot)))
        except HistoryBusy:
            hidden_message = '核验繁忙，详情已隐藏，请稍后重试。'
        except Exception:
            scopes = {}
    for created, raw in rows:
        item = json.loads(raw)
        if username:
            source = (item.get('execution_id'), item.get('execution_case_id'))
            if not scopes.get(source, False):
                result.append({'id': item.get('id'), 'created_at': item.get('created_at'),
                               'status': 'blocked', 'message': hidden_message})
                continue
        if item['status'] == 'running' and created + _LEASE_SECONDS < time.time():
            item.update(status='blocked', message='分析中断，请重新尝试。')
        result.append(item)
    return result


if __name__ == '__main__':
    try:
        request = json.load(sys.stdin)
        arguments = request['arguments']
        if request['operation'] == 'analyze' and len(arguments) == 4:
            result = _derive(*arguments)
        elif request['operation'] == 'authorize' and len(arguments) == 3:
            result = _verify_sources(*arguments)
        else:
            raise ValueError('invalid_worker_arguments')
        print(json.dumps(result, ensure_ascii=False))
    except Exception:
        print('[false, null]')
