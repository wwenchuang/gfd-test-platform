"""Private, durable readonly investigations using the shared bounded AI dispatcher."""
import json
import os
import re
import sqlite3
import stat
import time
import uuid
from contextlib import closing, contextmanager

from task_server import identity
from task_server.background_jobs import PersistedJobDispatcher
from .operation_attribution import actor_context, current_actor

ACTIVE = ('queued', 'running')
TERMINAL = ('completed', 'limited', 'failed', 'cancelled', 'interrupted')
BOOT = uuid.uuid4().hex
PAYLOAD_FIELDS = frozenset(('stop_reason', 'result', 'evidence', 'trace', 'rounds', 'usage',
                            'elapsed_ms', 'model', 'usage_complete', 'stage'))


class Busy(Exception):
    pass


def _profile(actor):
    trusted = current_actor()
    if (actor.get('kind') != 'user' or any(actor.get(k) != trusted.get(k) for k in ('kind', 'user_id', 'username'))):
        raise PermissionError('trusted user required')
    profile = identity.get_access_profile(actor.get('username'))
    if (not profile or profile.get('user_id') != actor.get('user_id') or
            profile.get('status') != 'active' or profile.get('must_change_password')):
        raise PermissionError('account unavailable')
    return profile


def _source(actor, source, run_id):
    if source in ('print', 'print_prod'):
        from .print_job_evidence import PrintInvestigation
        return PrintInvestigation(actor, run_id, production=source == 'print_prod')
    from .failure_analysis_sources import EvidenceSource
    return EvidenceSource(actor, source, run_id)


def _investigate(source, **kwargs):
    from .failure_analysis_agent import investigate
    return investigate(source, **kwargs)


def _bound_source(actor, source, run_id):
    _profile(actor)
    if source not in ('api', 'ui', 'print', 'print_prod') or not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', run_id):
        raise ValueError('报告来源或运行编号无效')
    result = _source(actor, source, run_id)
    result.authorize()
    return result


def _uuid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError('任务或请求编号无效') from None
    return value


@contextmanager
def _db():
    path = identity.default_db_path().with_name('failure-analysis.sqlite3')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise PermissionError('invalid database owner')
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, actor TEXT NOT NULL,
            source TEXT NOT NULL, run_id TEXT NOT NULL, request_id TEXT NOT NULL,
            status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            deadline REAL NOT NULL, pid INTEGER NOT NULL, boot TEXT NOT NULL,
            payload TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', UNIQUE(owner,request_id))''')
        db.execute('CREATE INDEX IF NOT EXISTS analysis_latest ON tasks(owner,source,run_id,created_at)')
        db.execute('''CREATE TABLE IF NOT EXISTS request_aliases (
            owner TEXT NOT NULL, request_id TEXT NOT NULL, task_id TEXT NOT NULL,
            PRIMARY KEY(owner,request_id))''')
        yield db


def _recover(db):
    now = time.time()
    for row in db.execute("SELECT id,status,pid,boot,deadline FROM tasks WHERE status IN ('queued','running')").fetchall():
        dead = row['pid'] == os.getpid() and row['boot'] != BOOT
        if not dead:
            try:
                os.kill(row['pid'], 0)
            except ProcessLookupError:
                dead = True
            except PermissionError:
                pass
        if dead or row['deadline'] <= now:
            error = '服务已重启，已保留取得的证据，请重新分析。' if dead else '分析达到时间上限，已保留取得的证据。'
            db.execute("UPDATE tasks SET status=?,error=?,updated_at=? WHERE id=? AND status IN ('queued','running')",
                       ('interrupted' if dead else 'limited', error, now, row['id']))


def _public(row):
    result = json.loads(row['payload'])
    result.update({k: row[k] for k in ('id', 'source', 'run_id', 'status', 'created_at', 'updated_at', 'error')})
    result['limits'] = {'rounds': 6, 'tool_calls': 12, 'seconds': 180, 'tokens': 18000}
    return result


def start(actor, body):
    if not isinstance(body, dict) or set(body) != {'source', 'run_id', 'request_id'}:
        raise ValueError('只接受报告来源、运行编号和请求编号')
    _bound_source(actor, body['source'], body['run_id'])
    _uuid(body['request_id'])
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        _recover(db)
        old = db.execute('SELECT * FROM tasks WHERE owner=? AND request_id=?', (actor['user_id'], body['request_id'])).fetchone()
        if old is None:
            old = db.execute('''SELECT t.* FROM tasks t JOIN request_aliases a ON a.task_id=t.id
                                WHERE a.owner=? AND a.request_id=? AND t.owner=a.owner''',
                             (actor['user_id'], body['request_id'])).fetchone()
        if old:
            if (old['source'], old['run_id']) != (body['source'], body['run_id']):
                raise ValueError('同一请求编号不能用于其他报告')
            return _public(old)
        active = db.execute("SELECT * FROM tasks WHERE owner=? AND status IN ('queued','running')", (actor['user_id'],)).fetchone()
        if active:
            if (active['source'], active['run_id']) == (body['source'], body['run_id']):
                db.execute('INSERT INTO request_aliases VALUES(?,?,?)', (actor['user_id'], body['request_id'], active['id']))
                return _public(active)
            raise Busy('已有分析正在进行，请完成或停止后再试。')
        key, now = str(uuid.uuid4()), time.time()
        payload = {'result': None, 'evidence': [], 'trace': [], 'rounds': 0, 'usage': {}, 'elapsed_ms': 0}
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (key, actor['user_id'], json.dumps(dict(actor)), body['source'], body['run_id'], body['request_id'],
                    'queued', now, now, now + 60, os.getpid(), BOOT, json.dumps(payload), ''))
    if not _dispatcher.submit(key):
        _finish_error(key, '分析队列已满，请稍后重试。')
    return read(actor, key)


def read(actor, key):
    _profile(actor)
    _uuid(key)
    with _db() as db:
        _recover(db)
        row = db.execute('SELECT * FROM tasks WHERE id=? AND owner=?', (key, actor['user_id'])).fetchone()
        if row is None:
            raise LookupError('分析不存在或无权查看')
    _bound_source(actor, row['source'], row['run_id'])
    return _public(row)


def latest(actor, source, run_id):
    _bound_source(actor, source, run_id)
    with _db() as db:
        _recover(db)
        row = db.execute('SELECT * FROM tasks WHERE owner=? AND source=? AND run_id=? ORDER BY created_at DESC LIMIT 1',
                         (actor['user_id'], source, run_id)).fetchone()
    return _public(row) if row else None


def cancel(actor, key):
    read(actor, key)
    with _db() as db:
        db.execute("UPDATE tasks SET status='cancelled',updated_at=?,error=? WHERE id=? AND owner=? AND status IN ('queued','running')",
                   (time.time(), '已停止分析，已取得的证据保留。', key, actor['user_id']))
    return read(actor, key)


def _load(key):
    with _db() as db:
        _recover(db)
        row = db.execute('SELECT status FROM tasks WHERE id=?', (key,)).fetchone()
    return {'type': 'failure-analysis', 'status': 'pending', 'request_data': {'task_id': key}} if row and row['status'] == 'queued' else None


def _finish_error(key, message, *_):
    # Never persist exception text: it may contain model credentials or request data.
    allowed = {'分析队列已满，请稍后重试。', '分析权限已变更，请重新登录并核对报告权限。', '分析服务暂时不可用，已保留取得的证据，请重试。'}
    message = message if message in allowed else '分析服务暂时不可用，已保留取得的证据，请重试。'
    with _db() as db:
        db.execute("UPDATE tasks SET status='failed',error=?,updated_at=? WHERE id=? AND status IN ('queued','running')",
                   (message, time.time(), key))


def _run(key, _request):
    with _db() as db:
        _recover(db)
        now = time.time()
        changed = db.execute("UPDATE tasks SET status='running',updated_at=?,deadline=? WHERE id=? AND status='queued'",
                             (now, now + 180, key)).rowcount
        if not changed:
            return
        row = db.execute('SELECT * FROM tasks WHERE id=?', (key,)).fetchone()
    actor = json.loads(row['actor'])
    with actor_context(actor):
        try:
            source = _bound_source(actor, row['source'], row['run_id'])
            def cancelled():
                with _db() as db:
                    current = db.execute('SELECT status,deadline FROM tasks WHERE id=?', (key,)).fetchone()
                return current is None or current['status'] != 'running' or current['deadline'] <= time.time()

            def progress(value):
                source.authorize()
                payload = {k: v for k, v in value.items() if k in PAYLOAD_FIELDS}
                with _db() as db:
                    current = db.execute('SELECT payload FROM tasks WHERE id=?', (key,)).fetchone()
                    previous = json.loads(current['payload'])
                    previous.update(payload)
                    encoded = json.dumps(previous, ensure_ascii=False)
                    if len(encoded.encode()) > 512000:
                        raise ValueError('analysis evidence limit')
                    db.execute("UPDATE tasks SET payload=?,updated_at=? WHERE id=? AND status='running' AND deadline>?",
                               (encoded, time.time(), key, time.time()))
            result = _investigate(source, progress=progress, cancelled=cancelled)
            source.authorize()
            if row['source'] in ('print', 'print_prod'):
                source.qualify_result(result)
            progress(result)
            status = result.get('status')
            if status not in TERMINAL:
                status = 'failed'
            with _db() as db:
                db.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=? AND status='running' AND deadline>?",
                           (status, time.time(), key, time.time()))
                _recover(db)
        except PermissionError:
            _finish_error(key, '分析权限已变更，请重新登录并核对报告权限。')
        except Exception:
            _finish_error(key, '分析服务暂时不可用，已保留取得的证据，请重试。')


_dispatcher = PersistedJobDispatcher(worker_count=2, queue_size=8, job_loader=_load,
                                   runner_resolver=lambda kind: _run if kind == 'failure-analysis' else None,
                                   failure_recorder=_finish_error)
