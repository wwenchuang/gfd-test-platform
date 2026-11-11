"""Durable owner-scoped execution of the fixed workbench suites."""
import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from contextlib import closing, contextmanager

from task_server import identity
from task_server.background_jobs import acquire_heavy_workload_slot, release_heavy_workload_slot
from task_server.services import week1_smoke, week5_web_service as web

SUITES = {
    'published-catalog': ('已发布用例目录数量核对', 'node', 'ui_catalog_count'),
    'feedback-categories': ('反馈分类只读接口检查', 'python', 'feedback_categories'),
    'catalog-failure-drill': ('目录数量断言失败演练', 'node', 'ui_catalog_failure_drill'),
}
_ACTIVE = ('queued', 'running', 'cancelling')
_TERMINAL = ('passed', 'failed', 'blocked', 'timed_out', 'interrupted', 'cancelled')
_ROWS = 'SELECT r.*, c.payload FROM runs r LEFT JOIN run_cases c USING(run_id)'

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
        db.execute('''CREATE TABLE IF NOT EXISTS run_cases (
            run_id TEXT PRIMARY KEY REFERENCES runs(run_id), process_start TEXT, payload TEXT)''')
        yield db


def _process_start(pid):
    # Linux start ticks distinguish a reused PID. Other hosts retain the PID check.
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return 'dead' if fields[0] in ('Z', 'X') else fields[19]
    except (OSError, ValueError, IndexError):
        return None


def _recover(db):
    now = time.time()
    rows = db.execute("""SELECT r.run_id,r.deadline,r.worker_pid,r.status,c.process_start,c.run_id AS current_worker
                         FROM runs r LEFT JOIN run_cases c USING(run_id)
                         WHERE r.status IN ('queued','running','cancelling')""").fetchall()
    for row in rows:
        try:
            os.kill(row['worker_pid'], 0)
        except ProcessLookupError:
            dead = True
        except PermissionError:
            dead = False
        else:
            start = _process_start(row['worker_pid'])
            dead = start == 'dead' or (start and row['process_start'] and start != row['process_start'])
        if dead:
            status, reason = 'interrupted', 'service_restarted'
        elif row['deadline'] <= now and (row['status'] == 'queued' or row['current_worker'] is None):
            status, reason = 'timed_out', 'worker_timeout'
        else:
            continue
        db.execute('UPDATE runs SET status=?,error_kind=?,finished_at=? WHERE run_id=?',
                   (status,reason,now,row['run_id']))


def _artifact_links(runner_id):
    if not runner_id:
        return {}
    return {kind:f"/api/ui-executions/web-check/artifact?id={runner_id}&item={kind}" for kind in ('screenshot','trace')}


def _case(row):
    if row['payload']:
        return json.loads(row['payload'])
    # W10 rows predate capture of the process exit code and runtime version.
    duration = max(0, round(1000 * (row['finished_at'] - row['started_at']))) if row['started_at'] is not None else 0
    evidence = _artifact_links(row['runner_id'])
    if row['total'] is not None:
        evidence['total'] = row['total']
    return {'run_id': row['run_id'], 'case_id': SUITES[row['suite_id']][2], 'attempt': 1,
            'runner': SUITES[row['suite_id']][1], 'status': row['status'], 'error_kind': row['error_kind'],
            'exit_code': {'passed':0, 'failed':1}.get(row['status']), 'worker_exit_code': None,
            'duration_ms': duration, 'version': None, 'evidence': evidence}


def _view(row):
    fields = ('run_id','request_id','suite_id','environment','status','created_at','started_at',
              'finished_at','runner_id','total','error_kind')
    result = {field: row[field] for field in fields}
    result['evidence'] = _artifact_links(row['runner_id'])
    terminal = row['status'] not in _ACTIVE
    result['cases'] = [_case(row)] if terminal else []
    result['junit_url'] = '/api/platform/mcp/junit?run_id=' + row['run_id'] if terminal else None
    return result


def _feedback_authorized(actor):
    from task_server.services.platform_runner import feedback_authorized
    return feedback_authorized(actor)


def _suite_access(actor, suite_id):
    if suite_id == 'feedback-categories' and not _feedback_authorized(actor):
        raise _missing()


def _execute(suite_id, actor, cancel_requested):
    from task_server.services.platform_runner import execute
    return execute(suite_id, actor, cancel_requested)


def _normalize(suite, native):
    runner, case_id = SUITES[suite][1:]
    status = native['status']
    if status not in _TERMINAL or native['runner'] != runner:
        raise ValueError('invalid_runner_result')
    version = native.get('runner_version')
    if version is not None and (not isinstance(version,str) or not re.fullmatch(r'[a-zA-Z0-9.+_-]{1,100}', version)):
        raise ValueError('invalid_runner_version')
    logical = native['exit_code']
    raw_exit = native.get('worker_exit_code')
    error = native['error_kind']
    if (logical != {'passed':0, 'failed':1}.get(status) or type(logical) not in (type(None),int)
            or type(raw_exit) not in (type(None),int) or (status=='passed') != (error is None)
            or (error is not None and not re.fullmatch('[a-z_]{1,64}',error))):
        raise ValueError('invalid_runner_result')
    evidence = native['evidence']
    if not isinstance(evidence,dict):
        raise ValueError('invalid_runner_evidence')
    runner_id = total = None
    if runner == 'node' and status in ('passed','failed'):
        runner_id, total = evidence.get('id'), evidence.get('total')
        if (evidence.get('scenario') != case_id or evidence.get('version') != 1
                or evidence.get('status') != status or (status == 'passed' and (type(total) is not int or total < 0))
                or not isinstance(runner_id,str) or not re.fullmatch('[a-f0-9]{32}',runner_id)
                or evidence.get('artifacts') != ['screenshot','trace'] or evidence.get('deletion_pending')):
            raise ValueError('invalid_browser_result')
        evidence = {key:evidence[key] for key in ('total','expected_total') if type(evidence.get(key)) is int}
        evidence.update(_artifact_links(runner_id))
        total = total if type(total) is int else None
    elif runner == 'node':
        evidence = {}
    else:
        evidence = {key:evidence[key] for key in ('http_status','actual_code','expected_code','data_is_list','checks','request_id')
                    if key in evidence}
    if len(json.dumps(evidence, allow_nan=False)) > 4096:
        raise ValueError('invalid_evidence_size')
    if status == 'timed_out' and runner == 'node' and error == 'timeout':
        error = 'browser_timeout'
    return status,error,runner_id,total,dict(version=version, worker_exit_code=raw_exit, exit_code=logical, evidence=evidence)


def _finish(run_id, status, error_kind, runner_id=None, total=None, details=None):
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute(_ROWS+' WHERE r.run_id=?',(run_id,)).fetchone()
        if not row or row['status'] not in _ACTIVE:
            return
        now = time.time()
        if row['deadline'] <= now and status != 'interrupted':
            status, error_kind, runner_id, total = 'timed_out', 'worker_timeout', None, None
            if details:
                details.update(exit_code=None, evidence={})
        db.execute('UPDATE runs SET status=?,error_kind=?,runner_id=?,total=?,finished_at=? WHERE run_id=?',
                   (status,error_kind,runner_id,total,now,run_id))
        if details:
            row = db.execute(_ROWS+' WHERE r.run_id=?',(run_id,)).fetchone()
            case = dict(_case(row), **details)
            db.execute('UPDATE run_cases SET payload=? WHERE run_id=?',(json.dumps(case,allow_nan=False),run_id))


def _worker(run_id, actor, scope):
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        _recover(db)
        claimed = db.execute("""UPDATE runs SET status='running',started_at=? WHERE run_id=? AND owner=?
                   AND scope=? AND status='queued' AND worker_pid=?""",
                   (time.time(),run_id,actor[0],scope,os.getpid())).rowcount
        if not claimed:
            return
        suite = db.execute('SELECT suite_id FROM runs WHERE run_id=?',(run_id,)).fetchone()[0]

    def cancel_requested():
        with _db() as db:
            row = db.execute('SELECT status,deadline FROM runs WHERE run_id=?',(run_id,)).fetchone()
        return not row or row['status'] != 'running' or row['deadline'] <= time.time()

    try:
        _verify_scope(actor, scope)
        _suite_access(actor, suite)
        if not _enabled():
            result = ('blocked', 'execution_disabled')
        elif not acquire_heavy_workload_slot('sync', blocking=False):
            result = ('blocked', 'heavy_workload_busy')
        else:
            try:
                outcome = _execute(suite, actor, cancel_requested)
                _verify_scope(actor, scope)
                _suite_access(actor, suite)
                try:
                    result = _normalize(suite,outcome)
                except (KeyError, ValueError, TypeError):
                    result = ('blocked','invalid_runner_result')
            finally:
                release_heavy_workload_slot('sync')
    except ToolError:
        result = ('blocked', 'authentication')
    except week1_smoke.SmokeBusy:
        result = ('blocked', 'check_busy')
    except TimeoutError:
        result = ('timed_out', 'browser_timeout' if suite != 'feedback-categories' else 'timeout')
    except Exception:
        result = ('blocked', 'environment')
    _finish(run_id, *result)


def _launch(run_id, actor, scope):
    threading.Thread(target=_worker, args=(run_id, actor, scope), name='workbench-runner', daemon=True).start()


def _run(actor, scope, args):
    if (set(args) != {'request_id','suite_id','environment'} or not isinstance(args['suite_id'],str)
            or args['suite_id'] not in SUITES or args['environment'] != 'platform'):
        raise _invalid()
    request_id = _uuid(args['request_id'])
    suite = args['suite_id']
    _suite_access(actor, suite)
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        _recover(db)
        row = db.execute(_ROWS+' WHERE r.owner=? AND r.request_id=?',(actor[0],request_id)).fetchone()
        if row:
            if row['scope'] != scope:
                raise _missing()
            if row['suite_id'] != suite or row['environment'] != args['environment']:
                raise ToolError(409,'request_conflict','此请求号已经用于其他套件')
            _verify_scope(actor, scope)
            return _view(row)
        if not _enabled():
            raise ToolError(503, 'execution_disabled', '平台已暂停工具执行')
        if db.execute("SELECT 1 FROM runs WHERE status IN ('queued','running','cancelling') LIMIT 1").fetchone():
            raise ToolError(429, 'check_busy', '已有工具检查在执行')
        run_id = str(uuid.uuid4())
        now = time.time()
        db.execute("""INSERT INTO runs(run_id,owner,scope,request_id,suite_id,environment,status,created_at,worker_pid,deadline)
                      VALUES(?,?,?,?,?,?,'queued',?,?,?)""",
                   (run_id,actor[0],scope,request_id,suite,args['environment'],now,os.getpid(),now+_DEADLINE_SECONDS))
        db.execute('INSERT INTO run_cases(run_id,process_start) VALUES(?,?)',(run_id,_process_start(os.getpid())))
        row = db.execute(_ROWS+' WHERE r.run_id=?',(run_id,)).fetchone()
    try:
        _launch(run_id,actor,scope)
    except RuntimeError:
        _finish(run_id, 'interrupted', 'worker_unavailable')
        return _get(actor,scope,run_id)
    return _view(row)


def _owned(db, actor, scope, run_id):
    row = db.execute(_ROWS+' WHERE r.run_id=? AND r.owner=? AND r.scope=?',(run_id,actor[0],scope)).fetchone()
    if not row:
        raise _missing()
    return row


def _get(actor, scope, run_id):
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        _recover(db)
        row = _owned(db, actor, scope, run_id)
    _suite_access(actor,row['suite_id'])
    _verify_scope(actor, scope)
    return _view(row)


def _cancel(actor, scope, run_id):
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        _recover(db)
        row = _owned(db, actor, scope, run_id)
        _suite_access(actor, row['suite_id'])
        _verify_scope(actor, scope)
        if row['status'] == 'queued':
            db.execute("UPDATE runs SET status='cancelled',error_kind='cancelled',finished_at=? WHERE run_id=?",
                       (time.time(),run_id))
        elif row['status'] == 'running':
            db.execute("UPDATE runs SET status='cancelling' WHERE run_id=?",(run_id,))
        return _view(_owned(db,actor,scope,run_id))


def call(actor, body):
    scope = _scope(actor)
    if not isinstance(body, dict) or set(body) != {'name','arguments'} or not isinstance(body['arguments'],dict):
        raise _invalid()
    name, args = body['name'], body['arguments']
    if name == 'list_suites' and not args:
        feedback = _feedback_authorized(actor)
        _verify_scope(actor,scope)
        return {'suites':[dict(suite_id=id, environment='platform', title=title, runner=runner,
                               drill=id=='catalog-failure-drill') for id,(title,runner,_) in SUITES.items()
                         if id != 'feedback-categories' or feedback]}
    if name == 'run_suite':
        return _run(actor,scope,args)
    if name in ('get_run','cancel_run') and set(args) == {'run_id'}:
        op = _get if name == 'get_run' else _cancel
        return op(actor,scope,_uuid(args['run_id']))
    if name not in ('list_suites','run_suite','get_run','cancel_run'):
        raise ToolError(400, 'unknown_tool', '未知工具')
    raise _invalid()


def history(actor):
    scope = _scope(actor)
    feedback = _feedback_authorized(actor)
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        _recover(db)
        rows = db.execute(_ROWS+""" WHERE r.owner=? AND r.scope=? AND (? OR r.suite_id!='feedback-categories')
                          ORDER BY r.created_at DESC LIMIT 80""",(actor[0],scope,feedback)).fetchall()
    _verify_scope(actor, scope)
    return [_view(row) for row in rows]


def junit(actor, run_id):
    run = _get(actor,_scope(actor),_uuid(run_id))
    if not run['cases']:
        raise ToolError(409,'run_active','执行完成后可以导出 JUnit')
    case = run['cases'][0]
    status = case['status']
    duration = format(case['duration_ms']/1000,'.3f')
    suite = ET.Element('testsuite', name=run['suite_id'], id=run['run_id'], tests='1', time=duration,
                       failures=str(int(status=='failed')), errors=str(int(status in ('blocked','timed_out','interrupted'))),
                       skipped=str(int(status=='cancelled')))
    test = ET.SubElement(suite,'testcase', name=case['case_id'], classname=case['runner'], time=duration)
    props = ET.SubElement(test,'properties')
    for field in ('run_id','case_id','attempt','runner','status','exit_code','worker_exit_code','duration_ms','version'):
        value = case[field]
        ET.SubElement(props,'property', name=field,value='' if value is None else str(value))
    tag = {'failed':'failure','blocked':'error','interrupted':'error','timed_out':'error','cancelled':'skipped'}.get(status)
    if tag:
        ET.SubElement(test,tag,type=status,message=case['error_kind'] or status)
    ET.SubElement(test,'system-out').text=json.dumps(case['evidence'],ensure_ascii=True,sort_keys=True)
    return ET.tostring(suite, encoding='utf-8', xml_declaration=True)


def _private_json(filename, *, require_0600=False):
    path = identity.default_db_path().with_name(filename)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in (0,os.geteuid())
                    or info.st_mode & 0o077 or info.st_size > 65536
                    or (require_0600 and stat.S_IMODE(info.st_mode) != 0o600)):
                raise ValueError()
            raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError()
            doc = json.loads(raw)
            if not isinstance(doc,dict):
                raise ValueError()
            return doc
    except FileNotFoundError:
        return None
    except (ValueError, OSError, RecursionError, OverflowError):
        raise ToolError(503,'receipt_unavailable','实测回执暂时无法读取') from None


def _receipt():
    doc = _private_json('mcp-acceptance.json')
    if doc is None:
        return None
    try:
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
    except (ValueError, RecursionError, ToolError, OverflowError):
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


_DRILL_OUTCOMES = {
    'worker_crash': ('interrupted', ('worker_crash',)),
    'cancel': ('cancelled', ('cancelled',)),
    'timeout': ('timed_out', ('timeout','worker_timeout')),
    'service_restart': ('interrupted', ('service_restarted',)),
}


def recovery_drill(actor):
    scope = _scope(actor)
    report = _private_json('mcp-recovery-drill.json', require_0600=True)
    if report is None:
        return None
    try:
        if (not isinstance(report,dict) or set(report) != {'version','environment','checked_at','checks'}
                or type(report['version']) is not int or report['version'] != 1 or report['environment'] != 'isolated'
                or type(report['checked_at']) not in (int,float) or not math.isfinite(report['checked_at'])
                or report['checked_at'] <= 0 or not isinstance(report['checks'],list) or len(report['checks']) != 4):
            raise ValueError()
        seen_kinds, seen_runs = set(), set()
        for check in report['checks']:
            if not isinstance(check,dict) or set(check) != {
                    'kind','run_id','before','status','error_kind','worker_exit_code','duration_ms','verified'}:
                raise ValueError()
            kind = check['kind']
            status, reasons = _DRILL_OUTCOMES[kind]
            rid = _uuid(check['run_id'])
            raw = check['worker_exit_code']
            if (rid in seen_runs or kind in seen_kinds or check['status'] != status or check['error_kind'] not in reasons
                    or check['verified'] is not True or type(check['duration_ms']) is not int
                    or not 0 <= check['duration_ms'] <= 86400000 or check['before'] not in ('running','cancelling')
                    or (kind != 'cancel' and check['before'] != 'running')
                    or (kind == 'service_restart' and raw is not None)
                    or (kind != 'service_restart' and (type(raw) is not int or not -255 <= raw <= 255 or raw == 0))):
                raise ValueError()
            seen_kinds.add(kind)
            seen_runs.add(rid)
    except (ValueError, TypeError, KeyError, ToolError, OverflowError):
        raise ToolError(503,'receipt_unavailable','隔离演练回执格式无效') from None
    _verify_scope(actor, scope)
    return report
