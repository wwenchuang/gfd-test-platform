"""Owner-scoped fixed browser check; credentials are never stored as evidence."""
import hashlib
import fcntl
import json
import os
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from task_server import auth, identity
from task_server.services import week1_smoke
from task_server.services.week5_trace_scan import safe_trace
from task_server.services.week5_web_evidence import read_evidence

_WORKER = Path(__file__).resolve().parent.parent / 'fixtures' / 'week5_web' / 'worker.js'
_TIMEOUT_SECONDS = 45
_OWNER_BYTES = 256 * 1024 * 1024
_GLOBAL_BYTES = 2 * 1024 * 1024 * 1024
_OWNER_RUNS = 80
_GLOBAL_RUNS = 256
_FREE_RESERVE = 1024 * 1024 * 1024
_SHM_FREE_RESERVE = 512 * 1024 * 1024
_SHM_RUN_LIMIT = 128 * 1024 * 1024
_SINGLE_FILE_LIMIT = 32 * 1024 * 1024
WORKFLOW_SCENARIOS = ('ui_case_selection', 'ui_case_revision', 'ui_start_handoff', 'ui_start_switch', 'ui_start_unready',
                      'ui_catalog_paging', 'ui_catalog_module', 'ui_case_hidden_selection', 'ui_start_empty_guard')
SCENARIOS = ('ui_catalog_count', 'ui_catalog_filter', 'ui_catalog_empty', 'ui_catalog_failure_drill') + WORKFLOW_SCENARIOS


class ResourceLimit(Exception):
    pass


class IdentityUnavailable(Exception):
    pass


def _profile(owner, username, token):
    session = auth.verify_session_token(token)
    profile = identity.get_access_profile(username) if session and session.get('user') == username else None
    if (not profile or profile.get('user_id') != owner or session.get('user_id') != owner
            or profile.get('status') != 'active' or profile.get('must_change_password')
            or (not profile.get('is_superuser') and 'ui.view' not in (profile.get('permissions') or []))):
        return None
    return profile


def _scope(profile):
    data = {'user_id':profile['user_id'], 'superuser':bool(profile.get('is_superuser')),
            'scope':profile.get('scope') or {}, 'permissions':sorted(profile.get('permissions') or [])}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _memory_temp():
    # Require tmpfs for temporary artifacts. tmpfs may still use swap.
    root = Path('/dev/shm')
    try:
        entries = Path('/proc/self/mountinfo').read_text().splitlines()
        if root.resolve() != root or not any(line.split(' - ', 1)[0].split()[4] == '/dev/shm'
                and line.split(' - ', 1)[1].split()[0] == 'tmpfs' for line in entries if ' - ' in line):
            raise ValueError('memory_storage_unavailable')
        if shutil.disk_usage(root).free < _SHM_FREE_RESERVE + _SHM_RUN_LIMIT:
            raise ResourceLimit('memory_storage_busy')
        return tempfile.mkdtemp(prefix='week5-web-', dir=str(root))
    except (OSError, IndexError):
        raise ValueError('memory_storage_unavailable') from None


def _core_limited_args(command):
    wrapper = ('import os,resource,sys;resource.setrlimit(resource.RLIMIT_CORE,(0,0));'
               'resource.setrlimit(resource.RLIMIT_FSIZE,(33554432,33554432));os.execv(sys.argv[1],sys.argv[1:])')
    return [sys.executable, '-c', wrapper, *command]


def _scratch_within_limit(root):
    try:
        if shutil.disk_usage(root).free < _SHM_FREE_RESERVE:
            return False
        total = 0
        for directory, _dirs, files in os.walk(root, followlinks=False):
            for name in files:
                try:
                    info = (Path(directory) / name).lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    if total > _SHM_RUN_LIMIT or info.st_size > _SINGLE_FILE_LIMIT:
                        return False
        return True
    except OSError:
        return False


def _bounded_process(argv, env, token, owner, *, scratch=None, scenario='ui_catalog_count'):
    if scenario not in SCENARIOS:
        raise ValueError('invalid_scenario')
    payload = json.dumps({'token':token, 'userId':owner, 'scenario':scenario}, separators=(',', ':')).encode()
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=env, start_new_session=True)
    first = True
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    try:
        while True:
            if scratch is not None and not _scratch_within_limit(scratch):
                raise ResourceLimit('memory_storage_busy')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('browser_timeout')
            try:
                stdout, _ = process.communicate(payload if first else None, timeout=min(.2, remaining))
                break
            except subprocess.TimeoutExpired:
                first = False
    except (TimeoutError, ResourceLimit):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=3)
        raise
    if process.returncode != 0 or len(stdout) > 4096:
        raise ValueError('browser_unavailable')
    result = json.loads(stdout)
    if (not isinstance(result, dict) or result.get('scenario') != scenario or result.get('version') != 1
            or result.get('status') not in ('passed', 'failed', 'blocked')
            or result.get('error_kind') not in (None, 'business', 'authentication', 'environment', 'evidence', 'drill_assertion', 'precondition')):
        raise ValueError('invalid_browser_result')
    if result['status'] == 'passed' and (result['error_kind'] is not None or type(result.get('total')) is not int or result['total'] < 0):
        raise ValueError('invalid_browser_result')
    asset = result.get('asset')
    phase = result.get('phase')
    if phase is not None and phase not in ('initial','filter','empty','drill','workflow'):
        raise ValueError('invalid_browser_result')
    if result['error_kind'] == 'drill_assertion':
        total, expected = result.get('total'), result.get('expected_total')
        if (scenario != 'ui_catalog_failure_drill' or phase != 'drill' or result['status'] != 'failed'
                or type(total) is not int or not 0 <= total <= 2**53 - 1
                or type(expected) is not int or expected != (1 if total == 0 else 0)):
            raise ValueError('invalid_browser_result')
    elif ('expected_total' in result
          or (phase == 'drill' and (scenario != 'ui_catalog_failure_drill' or result['status'] != 'blocked'))
          or (scenario == 'ui_catalog_failure_drill' and result['status'] == 'passed')):
        raise ValueError('invalid_browser_result')
    workflow = scenario in WORKFLOW_SCENARIOS
    if result['error_kind'] == 'precondition':
        if not workflow or result['status'] != 'blocked' or result.get('detail') not in (
                'execute_permission_required','no_published_case','no_second_app','case_version_changed','no_devices','ready_device_present',
                'no_case_module','catalog_changed'):
            raise ValueError('invalid_browser_result')
    elif 'detail' in result:
        raise ValueError('invalid_browser_result')
    if (phase == 'workflow' and not workflow) or (workflow and result['status'] == 'passed' and (phase != 'workflow' or asset is None)):
        raise ValueError('invalid_browser_result')
    if not workflow and scenario != 'ui_catalog_filter' and asset is not None:
        raise ValueError('invalid_browser_result')
    if scenario == 'ui_catalog_filter' and asset is None and (result['status'] == 'passed' or (result['status'] == 'failed' and phase != 'initial')):
        raise ValueError('invalid_browser_result')
    if asset is not None:
        if not ((workflow and phase == 'workflow') or (scenario == 'ui_catalog_filter' and phase == 'filter')):
            raise ValueError('invalid_browser_result')
        if (not isinstance(asset, dict) or set(asset) != {'app_id','case_id','revision'}
                or not isinstance(asset['app_id'], str) or not 0 < len(asset['app_id']) <= 128
                or not isinstance(asset['case_id'], str) or not 0 < len(asset['case_id']) <= 512
                or not isinstance(asset['revision'], str) or len(asset['revision']) != 64
                or any(char not in '0123456789abcdef' for char in asset['revision'])):
            raise ValueError('invalid_browser_result')
    passed = result['status'] == 'passed'
    if ('pages_checked' in result) != (scenario == 'ui_catalog_paging' and passed):
        raise ValueError('invalid_browser_result')
    if 'pages_checked' in result and (type(result['pages_checked']) is not int
            or result['pages_checked'] != (2 if result['total'] > 20 else 1)):
        raise ValueError('invalid_browser_result')
    if ('device_count' in result) != (scenario == 'ui_start_unready' and passed):
        raise ValueError('invalid_browser_result')
    if 'device_count' in result and (type(result['device_count']) is not int or result['device_count'] <= 0):
        raise ValueError('invalid_browser_result')
    if ('switched_app_id' in result) != (scenario == 'ui_start_switch' and passed):
        raise ValueError('invalid_browser_result')
    if 'switched_app_id' in result:
        other = result['switched_app_id']
        if not isinstance(other, str) or not 0 < len(other) <= 128 or other == asset['app_id']:
            raise ValueError('invalid_browser_result')
    return {key: result[key] for key in ('scenario','version','phase','status','error_kind','total','expected_total','asset','detail','device_count','switched_app_id','pages_checked') if key in result}


def _load_private(path, limit):
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid()):
            raise ValueError('invalid_evidence')
        with os.fdopen(os.dup(fd), 'rb') as source:
            return source.read(limit + 1)
    finally:
        os.close(fd)


def _execute(owner, token, scenario='ui_catalog_count'):
    scratch = Path(_memory_temp())
    try:
        node = week1_smoke._configured_path('TASK_WEEK1_NODE', executable=True)
        env = {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'HOME':str(scratch), 'TMPDIR':str(scratch),
               'WEEK5_ARTIFACTS':str(scratch), 'NODE_PATH':week1_smoke._configured_path('TASK_WEEK1_NODE_PATH'),
               'PLAYWRIGHT_BROWSERS_PATH':week1_smoke._configured_path('TASK_WEEK1_BROWSERS_PATH')}
        if os.environ.get('TASK_WEEK1_LIBRARY_PATH'):
            env['LD_LIBRARY_PATH'] = week1_smoke._configured_path('TASK_WEEK1_LIBRARY_PATH')
        fontconfig = os.environ.get('TASK_WEEK1_FONTCONFIG_FILE', '')
        if fontconfig:
            if not os.path.isabs(fontconfig) or not Path(fontconfig).is_file() or not os.access(fontconfig, os.R_OK):
                raise ValueError('dependency_unavailable')
            env['FONTCONFIG_FILE'] = fontconfig
        result = _bounded_process(_core_limited_args([node, str(_WORKER)]), env, token, owner, scratch=scratch, scenario=scenario)
        if result['status'] == 'blocked':
            return result, b'', b''
        image = _load_private(scratch / 'screenshot.png', 8 * 1024 * 1024)
        trace = _load_private(scratch / 'trace.zip', 30 * 1024 * 1024)
        return result, image, trace
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _db_path(path):
    return Path(path) if path is not None else identity.default_db_path().with_name('week5-web.sqlite3')


def _root(path):
    return Path(path) if path is not None else identity.default_db_path().with_name('week5-web-evidence')


@contextmanager
def _records_lock(db_path):
    path = _db_path(db_path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = path.with_name('.' + path.name + '.records.lock')
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid()):
            raise ValueError('invalid_lock')
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _within_quota(owner_bytes, global_bytes, owner_runs, global_runs, required, free):
    return (owner_bytes + required <= _OWNER_BYTES and global_bytes + required <= _GLOBAL_BYTES
            and owner_runs < _OWNER_RUNS and global_runs < _GLOBAL_RUNS
            and free - required >= _FREE_RESERVE)


def _check_quota(db_path, owner, required, root):
    with week1_smoke._db(db_path) as db:
        rows = db.execute('SELECT owner,payload FROM results').fetchall()
    owner_bytes = global_bytes = owner_runs = global_runs = 0
    try:
        for record_owner, raw in rows:
            payload = json.loads(raw)
            sizes = payload['sizes']
            size = sizes['screenshot'] + sizes['trace']
            if type(size) is not int or size < 1 or size > 38 * 1024 * 1024:
                return False
            global_runs += 1
            global_bytes += size
            if record_owner == owner:
                owner_runs += 1
                owner_bytes += size
        free = shutil.disk_usage(root.parent).free
        return _within_quota(owner_bytes, global_bytes, owner_runs, global_runs, required, free)
    except (TypeError, ValueError, KeyError, OSError):
        return False


def _save_files(root, run_id, image, trace):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid()):
        raise ValueError('evidence_unavailable')
    os.chmod(root, 0o700)
    run_directory = root / run_id
    run_directory.mkdir(mode=0o700, exist_ok=False)
    directory = run_directory / 'evidence'
    directory.mkdir(mode=0o700, exist_ok=False)
    digests = {}
    try:
        for name, data in (('screenshot', image), ('trace', trace)):
            filename = 'screenshot.png' if name == 'screenshot' else 'trace.zip'
            fd = os.open(str(directory / filename), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'wb') as target:
                target.write(data)
            digests[name] = hashlib.sha256(data).hexdigest()
        return digests
    except Exception:
        shutil.rmtree(directory.parent, ignore_errors=True)
        raise


def run(owner, username, token, *, scenario='ui_catalog_count', db_path=None, root=None, lease_path=None):
    if scenario not in SCENARIOS:
        raise ValueError('invalid_scenario')
    profile = _profile(owner, username, token)
    blocked = {'scenario':scenario, 'version':1, 'status':'blocked', 'error_kind':'authentication'}
    if not profile:
        return blocked
    scope = _scope(profile)
    shared_lease_db = _db_path(db_path) if db_path is not None else identity.default_db_path().with_name('week1-smoke.sqlite3')
    db_path, root = _db_path(db_path), _root(root)
    if lease_path is not None:
        shared_lease_db = lease_path
    lease = week1_smoke._claim(shared_lease_db)
    try:
        if not _check_quota(db_path, owner, 38 * 1024 * 1024, root):
            return dict(blocked, error_kind='evidence', detail='quota_exceeded')
        try:
            result, image, trace = _execute(owner, token, scenario)
        except Exception:
            return dict(blocked, error_kind='environment')
        refreshed = _profile(owner, username, token)
        if not refreshed or _scope(refreshed) != scope or result['error_kind'] == 'authentication':
            return blocked
        if result['status'] == 'blocked':
            return {**blocked, 'error_kind':result['error_kind'], **({'detail':result['detail']} if 'detail' in result else {})}
        if not image.startswith(b'\x89PNG\r\n\x1a\n') or not safe_trace(trace, token) or token.encode() in image:
            return dict(blocked, error_kind='evidence')
        if not _check_quota(db_path, owner, len(image) + len(trace), root):
            return dict(blocked, error_kind='evidence', detail='quota_exceeded')
        run_id = uuid.uuid4().hex
        with _records_lock(db_path):
            refreshed = _profile(owner, username, token)
            if not refreshed or _scope(refreshed) != scope:
                return blocked
            created = time.time()
            pending_result = {'scenario':scenario, 'version':1, 'status':'blocked',
                              'error_kind':'evidence', 'id':run_id, 'created':created,
                              'deletion_pending':True, 'artifacts':[]}
            payload = {'result':pending_result, 'scope':scope, 'state':'saving',
                       'sizes':{'screenshot':len(image), 'trace':len(trace)}}
            with week1_smoke._db(db_path) as db:
                db.execute('INSERT INTO results VALUES (?,?,?,?)', (run_id, owner, created, json.dumps(payload, ensure_ascii=True)))
            try:
                digests = _save_files(root, run_id, image, trace)
                refreshed = _profile(owner, username, token)
                if not refreshed or _scope(refreshed) != scope:
                    return blocked
                result = {**result, 'id':run_id, 'created':created, 'artifacts':['screenshot','trace']}
                payload.update(result=result, state='ready', digests=digests)
                with week1_smoke._db(db_path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('UPDATE results SET payload=? WHERE id=? AND owner=?', (json.dumps(payload, ensure_ascii=True), run_id, owner))
                    try:
                        refreshed = _profile(owner, username, token)
                    except Exception:
                        raise IdentityUnavailable('authorization_unavailable') from None
                    if not refreshed or _scope(refreshed) != scope:
                        raise IdentityUnavailable('authorization_changed')
            except IdentityUnavailable:
                return blocked
            except Exception:
                return pending_result
        return result
    finally:
        week1_smoke._release(shared_lease_db, lease)


def artifact(owner, username, token, run_id, item, *, db_path=None, root=None):
    with _records_lock(db_path):
        return _artifact_locked(owner, username, token, run_id, item, db_path=db_path, root=root)


def _artifact_locked(owner, username, token, run_id, item, *, db_path=None, root=None):
    profile = _profile(owner, username, token)
    if not profile:
        raise ValueError('not_found')
    with week1_smoke._db(_db_path(db_path)) as db:
        row = db.execute('SELECT payload FROM results WHERE id=? AND owner=?', (run_id, owner)).fetchone()
    if not row:
        raise ValueError('not_found')
    try:
        payload = json.loads(row[0])
        if payload['scope'] != _scope(profile) or payload.get('deleting') or payload.get('state') != 'ready':
            raise ValueError('not_found')
        data = read_evidence(_root(root), run_id, item, payload['digests'][item])
        refreshed = _profile(owner, username, token)
        if not refreshed or _scope(refreshed) != payload['scope']:
            raise ValueError('not_found')
        return data
    except (ValueError, KeyError, TypeError):
        raise ValueError('not_found') from None


def list_results(owner, username, token, *, db_path=None):
    with _records_lock(db_path):
        return _list_locked(owner, username, token, db_path=db_path)


def _list_locked(owner, username, token, *, db_path=None):
    profile = _profile(owner, username, token)
    if not profile or profile.get('user_id') != owner:
        return []
    with week1_smoke._db(_db_path(db_path)) as db:
        rows = db.execute('SELECT payload FROM results WHERE owner=? ORDER BY created DESC LIMIT ?', (owner,_OWNER_RUNS)).fetchall()
    result = []
    for (raw,) in rows:
        try:
            payload = json.loads(raw)
            if isinstance(payload.get('result'), dict):
                if payload.get('scope') != _scope(profile):
                    stored = payload['result']
                    result.append({'id':stored.get('id'), 'created':stored.get('created'),
                                   'scope_changed':True, 'deletion_pending':bool(payload.get('deleting') or payload.get('state') == 'saving'),
                                   'artifacts':[]})
                    continue
                item = dict(payload['result'])
                if payload.get('deleting') or payload.get('state') == 'saving':
                    item.update(deletion_pending=True, artifacts=[])
                result.append(item)
        except (ValueError, TypeError):
            continue
    refreshed = _profile(owner, username, token)
    if not refreshed or _scope(refreshed) != _scope(profile):
        return []
    return result


def _remove_evidence(root, run_id):
    """Delete only the two known files under a fixed owned run directory."""
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fds = []
    try:
        try:
            fds.append(os.open(str(root), flags))
            fds.append(os.open(run_id, flags, dir_fd=fds[-1]))
        except FileNotFoundError:
            return
        for fd in fds:
            info = os.fstat(fd)
            if hasattr(os, 'geteuid') and info.st_uid != os.geteuid():
                raise ValueError('invalid_evidence_owner')
        entries = set(os.listdir(fds[-1]))
        if entries - {'evidence'}:
            raise ValueError('unexpected_evidence_files')
        if 'evidence' in entries:
            fds.append(os.open('evidence', flags, dir_fd=fds[-1]))
            if hasattr(os, 'geteuid') and os.fstat(fds[-1]).st_uid != os.geteuid():
                raise ValueError('invalid_evidence_owner')
            names = set(os.listdir(fds[-1]))
            if names - {'screenshot.png','trace.zip'}:
                raise ValueError('unexpected_evidence_files')
            for name in names:
                info = os.stat(name, dir_fd=fds[-1], follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid()):
                    raise ValueError('invalid_evidence')
                os.unlink(name, dir_fd=fds[-1])
            os.rmdir('evidence', dir_fd=fds[-2])
        os.rmdir(run_id, dir_fd=fds[0])
    finally:
        for fd in reversed(fds):
            os.close(fd)


def delete(owner, username, token, run_id, *, db_path=None, root=None):
    if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
        raise ValueError('not_found')
    with _records_lock(db_path):
        profile = _profile(owner, username, token)
        if not profile:
            raise ValueError('not_found')
        path = _db_path(db_path)
        with week1_smoke._db(path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM results WHERE id=? AND owner=?', (run_id,owner)).fetchone()
            if not row:
                raise ValueError('not_found')
            payload = json.loads(row[0])
            refreshed = _profile(owner, username, token)
            if not refreshed or _scope(refreshed) != _scope(profile):
                raise ValueError('not_found')
            payload['deleting'] = True
            db.execute('UPDATE results SET payload=? WHERE id=? AND owner=?', (json.dumps(payload, ensure_ascii=True),run_id,owner))
        try:
            _remove_evidence(_root(root), run_id)
        except (OSError, ValueError):
            return False
        with week1_smoke._db(path) as db:
            db.execute('DELETE FROM results WHERE id=? AND owner=?', (run_id,owner))
        return True
