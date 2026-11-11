"""Durable owner-scoped calls to the existing published-catalog browser check."""
import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
from contextlib import closing, contextmanager

from task_server import identity
from task_server.background_jobs import acquire_heavy_workload_slot, release_heavy_workload_slot
from task_server.services import week1_smoke, week5_web_service as web

SUITE = {'suite_id': 'published-catalog', 'environment': 'platform', 'title': '已发布用例目录数量核对'}
_DEADLINE_SECONDS = 90


class ToolError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


def _invalid():
    return ToolError(400, 'invalid_arguments', '工具参数无效')


def _missing():
    return ToolError(404, 'run_not_found', '执行不存在或无权查看')


def _uuid(value):
    if not isinstance(value, str):
        raise _invalid()
    try:
        if str(uuid.UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise _invalid()


def _scope(actor):
    profile = web._profile(*actor)
    if not profile:
        raise ToolError(403, 'unauthorized', '登录已失效或缺少 UI 查看权限')
    return web._scope(profile)


def _verify_scope(actor, scope):
    if _scope(actor) != scope:
        raise ToolError(403, 'unauthorized', '权限已变更')


def _enabled():
    return os.environ.get('TASK_MCP_RUN_ENABLED', '1').strip().lower() in ('1', 'true', 'yes', 'on')


@contextmanager
def _db():
    path = identity.default_db_path().with_name('platform-mcp.sqlite3')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise OSError('invalid database owner')
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, owner TEXT NOT NULL, scope TEXT NOT NULL,
            request_id TEXT NOT NULL, suite_id TEXT NOT NULL, environment TEXT NOT NULL,
            status TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, finished_at REAL,
            worker_pid INTEGER NOT NULL, deadline REAL NOT NULL,
            runner_id TEXT, total INTEGER, error_kind TEXT, UNIQUE(owner, request_id))""")
        db.execute('CREATE INDEX IF NOT EXISTS mcp_active_runs ON runs(status)')
        db.execute('CREATE INDEX IF NOT EXISTS mcp_owner_history ON runs(owner,scope,created_at)')
        yield db


def _recover(db):
    now = time.time()
    for row in db.execute("SELECT run_id, deadline, worker_pid FROM runs WHERE status IN ('queued','running')").fetchall():
        if row['deadline'] <= now:
            status, reason = 'timed_out', 'worker_timeout'
        else:
            try:
                os.kill(row['worker_pid'], 0)
                continue
            except PermissionError:
                continue
            except ProcessLookupError:
                status, reason = 'interrupted', 'service_restarted'
        db.execute("UPDATE runs SET status=?, error_kind=?, finished_at=? WHERE run_id=? AND status IN ('queued','running')",
                   (status, reason, now, row['run_id']))


def _view(row):
    fields = ('run_id','request_id','suite_id','environment','status','created_at','started_at',
              'finished_at','runner_id','total','error_kind')
    result = {field: row[field] for field in fields}
    result['evidence'] = {}
    if row['runner_id']:
        result['evidence'] = {
            kind: f"/api/ui-executions/web-check/artifact?id={row['runner_id']}&item={kind}"
            for kind in ('screenshot', 'trace')
        }
    return result


def _result(outcome):
    if outcome.get('scenario') != 'ui_catalog_count' or outcome.get('version') != 1:
        return 'blocked', 'invalid_runner_result', None, None
    status = outcome.get('status')
    if status == 'blocked':
        if outcome.get('detail') == 'browser_timeout':
            return 'timed_out', 'browser_timeout', None, None
        kind = outcome.get('error_kind')
        return 'blocked', kind if kind in ('authentication','environment','evidence') else 'environment', None, None
    runner_id, total = outcome.get('id'), outcome.get('total')
    if (status not in ('passed', 'failed') or not isinstance(runner_id, str) or not re.fullmatch('[0-9a-f]{32}', runner_id)
            or outcome.get('artifacts') != ['screenshot', 'trace'] or outcome.get('deletion_pending')
            or (status == 'passed' and (type(total) is not int or total < 0 or outcome.get('error_kind') is not None))):
        return 'blocked', 'invalid_runner_result', None, None
    return status, None if status == 'passed' else 'business', runner_id, total if type(total) is int else None


def _finish(run_id, status, error_kind, runner_id=None, total=None):
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        _recover(db)
        db.execute("""UPDATE runs SET status=?,error_kind=?,runner_id=?,total=?,finished_at=?
                      WHERE run_id=? AND status IN ('queued','running')""",
                   (status,error_kind,runner_id,total,time.time(),run_id))


def _worker(run_id, actor, scope):
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        _recover(db)
        claimed = db.execute("UPDATE runs SET status='running',started_at=? WHERE run_id=? AND status='queued' AND worker_pid=?",
                             (time.time(), run_id, os.getpid())).rowcount
    if not claimed:
        return
    try:
        _verify_scope(actor, scope)
        if not _enabled():
            result = ('blocked', 'execution_disabled')
        elif not acquire_heavy_workload_slot('sync', blocking=False):
            result = ('blocked', 'heavy_workload_busy')
        else:
            try:
                outcome = web.run(*actor, scenario='ui_catalog_count')
                _verify_scope(actor, scope)
                result = _result(outcome)
            finally:
                release_heavy_workload_slot('sync')
    except ToolError:
        result = ('blocked', 'authentication')
    except week1_smoke.SmokeBusy:
        result = ('blocked', 'check_busy')
    except TimeoutError:
        result = ('timed_out', 'browser_timeout')
    except Exception:
        result = ('blocked', 'environment')
    _finish(run_id, *result)


def _launch(run_id, actor, scope):
    threading.Thread(target=_worker, args=(run_id, actor, scope), name='mcp-published-catalog', daemon=True).start()


def _run(actor, scope, args):
    if set(args) != {'request_id','suite_id','environment'} or any(args[k] != SUITE[k] for k in ('suite_id','environment')):
        raise _invalid()
    request_id = _uuid(args['request_id'])
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        _recover(db)
        row = db.execute('SELECT * FROM runs WHERE owner=? AND request_id=?',(actor[0],request_id)).fetchone()
        if row:
            if row['scope'] != scope:
                raise _missing()
            _verify_scope(actor, scope)
            return _view(row)
        if not _enabled():
            raise ToolError(503, 'execution_disabled', '平台已暂停工具执行')
        if db.execute("SELECT 1 FROM runs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise ToolError(429, 'check_busy', '已有目录检查在执行')
        run_id = str(uuid.uuid4())
        now = time.time()
        db.execute("""INSERT INTO runs(run_id,owner,scope,request_id,suite_id,environment,status,created_at,worker_pid,deadline)
                      VALUES(?,?,?,?,?,?,'queued',?,?,?)""",
                   (run_id,actor[0],scope,request_id,args['suite_id'],args['environment'],now,os.getpid(),now+_DEADLINE_SECONDS))
        row = db.execute('SELECT * FROM runs WHERE run_id=?',(run_id,)).fetchone()
    try:
        _launch(run_id,actor,scope)
    except RuntimeError:
        _finish(run_id, 'interrupted', 'worker_unavailable')
        return _get(actor,scope,run_id)
    return _view(row)


def _get(actor, scope, run_id):
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        _recover(db)
        row = db.execute('SELECT * FROM runs WHERE run_id=? AND owner=? AND scope=?',(run_id,actor[0],scope)).fetchone()
    if not row:
        raise _missing()
    _verify_scope(actor, scope)
    return _view(row)


def call(actor, body):
    scope = _scope(actor)
    if not isinstance(body, dict) or set(body) != {'name','arguments'} or not isinstance(body['arguments'],dict):
        raise _invalid()
    name, args = body['name'], body['arguments']
    if name == 'list_suites' and not args:
        return {'suites':[dict(SUITE)]}
    if name == 'run_suite':
        return _run(actor,scope,args)
    if name == 'get_run' and set(args) == {'run_id'}:
        return _get(actor,scope,_uuid(args['run_id']))
    if name not in ('list_suites','run_suite','get_run'):
        raise ToolError(400, 'unknown_tool', '未知工具')
    raise _invalid()


def history(actor):
    scope = _scope(actor)
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        _recover(db)
        rows = db.execute('SELECT * FROM runs WHERE owner=? AND scope=? ORDER BY created_at DESC LIMIT 80',
                          (actor[0],scope)).fetchall()
    _verify_scope(actor, scope)
    return [_view(row) for row in rows]


def _receipt():
    path = identity.default_db_path().with_name('mcp-acceptance.json')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in (0,os.geteuid())
                    or info.st_mode & 0o077 or info.st_size > 65536):
                raise ValueError()
            raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError()
            doc = json.loads(raw)
        if (not isinstance(doc,dict) or set(doc) != {'version','run_id','checked_at','checks'} or type(doc['version']) != int
                or doc['version'] != 1 or type(doc['checked_at']) not in (int,float) or not math.isfinite(doc['checked_at'])
                or not isinstance(doc['checks'], list) or not 1 <= len(doc['checks']) <= 40):
            raise ValueError()
        _uuid(doc['run_id'])
        for check in doc['checks']:
            if (not isinstance(check,dict) or set(check) != {'name','status','detail'}
                    or check['status'] not in ('passed','blocked','failed') or not isinstance(check['name'],str)
                    or not 1 <= len(check['name']) <= 96 or not isinstance(check['detail'],str) or len(check['detail']) > 240):
                raise ValueError()
        return doc
    except FileNotFoundError:
        return None
    except (ValueError, OSError, RecursionError, ToolError, OverflowError):
        raise ToolError(503,'receipt_unavailable','实测回执暂时无法读取') from None


def acceptance(actor):
    scope = _scope(actor)
    report = _receipt()
    if report:
        try:
            _get(actor,scope,report['run_id'])
        except ToolError as exc:
            if exc.code != 'run_not_found':
                raise
            return None
    return report
