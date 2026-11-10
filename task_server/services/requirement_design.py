"""Frozen PRD/prototype inputs and private, review-pending AI design runs."""
import base64
from contextlib import closing, contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time
import uuid

from task_server import identity
from task_server.background_jobs import acquire_heavy_workload_slot, release_heavy_workload_slot

CATALOG = '@sample'
MAX_IMAGE = 2 * 1024 * 1024


class Busy(Exception):
    pass


class Missing(Exception):
    pass


def authorized(owner, username):
    profile = identity.get_access_profile(username) if username else None
    if not profile or profile.get('user_id') != owner or profile.get('status') != 'active' or profile.get('must_change_password'):
        return False
    return profile.get('is_superuser') or (
        {'platform.configure','ui.view'}.issubset(profile.get('permissions') or [])
        and (profile.get('scope') or {}).get('ui_apps') == '*')


def _root():
    return identity.default_db_path().parent


def _digest(data):
    return hashlib.sha256(data).hexdigest()


@contextmanager
def _db():
    root = _root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / 'requirement-design.sqlite3'
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600))
    with closing(sqlite3.connect(path, timeout=8)) as db:
        db.row_factory = sqlite3.Row
        db.executescript("""
          CREATE TABLE IF NOT EXISTS sources(owner TEXT, id TEXT, meta TEXT NOT NULL,
            created REAL NOT NULL, PRIMARY KEY(owner,id));
          CREATE TABLE IF NOT EXISTS images(owner TEXT, source TEXT, id TEXT,
            image BLOB NOT NULL, PRIMARY KEY(owner,source,id));
          CREATE TABLE IF NOT EXISTS designs(id TEXT PRIMARY KEY, owner TEXT NOT NULL,
            source TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL,
            created REAL NOT NULL, finished REAL, result TEXT, error TEXT,
            UNIQUE(owner,request));
        """)
        with db:
            yield db


def _source_id(meta):
    return _digest(json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode())


def _store(owner, meta, images):
    sid = _source_id(meta)
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM sources WHERE owner=? AND id=?',(owner,sid)).fetchone():
            return sid
        count = db.execute('SELECT COUNT(*) FROM sources WHERE owner=?',(owner,)).fetchone()[0]
        if count >= 32:
            raise ValueError('此账号已达到材料存储上限')
        db.execute('INSERT INTO sources VALUES (?,?,?,?)',(owner,sid,json.dumps(meta,ensure_ascii=False),time.time()))
        db.executemany('INSERT INTO images VALUES (?,?,?,?)',[(owner,sid,k,b) for k,b in images.items()])
    return sid


def _sample_id():
    directory = _root() / 'requirement-design-materials'
    path = directory / 'source.json'
    if not path.is_file():
        return None
    source = json.loads(path.read_text('utf-8'))
    meta = {k:source[k] for k in ('title','app','version','prd_file_sha256','clauses','prototypes')}
    meta['source_type'] = 'curated'
    sid = _source_id(meta)
    with _db() as db:
        if db.execute('SELECT 1 FROM sources WHERE owner=? AND id=?',(CATALOG,sid)).fetchone():
            return sid
    images = {}
    for ref in meta['prototypes']:
        filename = ref['file']
        if Path(filename).name != filename or not filename.endswith('.png'):
            raise ValueError('原型文件名异常')
        data = (directory / filename).read_bytes()
        if len(data) > 3*1024*1024 or _digest(data) != ref['sha256']:
            raise ValueError('原型源文件校验失败')
        images[ref['id']] = data
    return _store(CATALOG,meta,images)


def _text(value, limit):
    if not isinstance(value,str) or not 0 < len(value.strip()) <= limit:
        raise ValueError('文本为空或超出长度限制')
    return value.strip()


def import_source(owner, body):
    from PIL import Image
    if not isinstance(body,dict) or set(body) != {'title','app','version','clauses','image_base64'}:
        raise ValueError('需要需求标题、应用、版本、条款和一张 PNG 原型')
    meta = {k:_text(body[k],n) for k,n in [('title',160),('app',128),('version',100)]}
    items = body['clauses']
    if not isinstance(items,list) or not 1 <= len(items) <= 20:
        raise ValueError('请填写 1 至 20 条 PRD 条款')
    clauses = [_text(v,900) for v in items]
    encoded = body['image_base64']
    if not isinstance(encoded,str) or len(encoded)>4*((MAX_IMAGE+2)//3):
        raise ValueError('原型图片超过 2 MiB')
    try:
        content = base64.b64decode(encoded,validate=True)
        with Image.open(io.BytesIO(content)) as img:
            if img.format != 'PNG' or max(img.size)>4096 or img.width*img.height>8_000_000 or getattr(img,'n_frames',1)!=1:
                raise ValueError()
            img.load()
            clean=img.convert('RGBA')
            clean.info.clear()
            out=io.BytesIO()
            clean.save(out, format='PNG')
            data=out.getvalue()
            if len(data)>MAX_IMAGE:
                raise ValueError()
    except (ValueError, OSError, Image.DecompressionBombError) as e:
        raise ValueError('请使用 2 MiB 内、单幅且可正常解码的 PNG 原型') from e
    meta.update(source_type='excerpt',prd_file_sha256=_digest('\n'.join(clauses).encode()),
                clauses=[dict(id='P'+str(i),text=v) for i,v in enumerate(clauses,1)],
                prototypes=[dict(id='V1',file='prototype.png',caption='上传的原型图',sha256=_digest(data))])
    return _store(owner,meta,{'V1':data})


def _source(db, owner, sid):
    row = db.execute('SELECT owner,meta FROM sources WHERE id=? AND owner IN (?,?) ORDER BY owner=? DESC LIMIT 1',
                     (sid,owner,CATALOG,owner)).fetchone()
    if row is None:
        raise Missing()
    return row['owner'],dict(json.loads(row['meta']), id=sid)


def _run(row):
    view = {k:row[k] for k in ('id','status','created','finished','error')}
    view['review']='pending'
    if row['result']:
        view['result']=json.loads(row['result'])
    return view


def _lock():
    fd=os.open(_root() / '.requirement-design.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def _reconcile(db):
    db.execute("UPDATE designs SET status='failed',finished=?,error=? WHERE status='running'",
               (time.time(),'服务重启中断本次生成，可重新生成'))


def overview(owner, sid=None):
    example = _sample_id()
    with _db() as db:
        lock=_lock()
        if lock is not None:
            try:
                _reconcile(db)
                db.commit()
            finally:
                os.close(lock)
        choices=db.execute('SELECT id,meta FROM sources WHERE owner IN (?,?) ORDER BY created DESC',(owner,CATALOG)).fetchall()
        sources=[{k:v for k,v in dict(json.loads(row['meta']),id=row['id']).items() if k in ('id','title','version')} for row in choices]
        selected=sid or example or (sources[0]['id'] if sources else None)
        if not selected:
            return dict(source=None,sources=[],runs=[])
        _,source=_source(db,owner,selected)
        rows=db.execute('SELECT * FROM designs WHERE owner=? AND source=? ORDER BY created DESC LIMIT 20',(owner,selected))
        return dict(source=source,sources=sources,runs=[_run(row) for row in rows])


def prototype(owner, sid, vid):
    with _db() as db:
        source_owner,_=_source(db,owner,sid)
        row=db.execute('SELECT image FROM images WHERE owner=? AND source=? AND id=?',(source_owner,sid,vid)).fetchone()
        if row is None:
            raise Missing()
        return row[0]


def _generate(owner, username, sid, rid, lock):
    result=None
    error='生成未完成，请稍后重试'
    try:
        from task_server.services.requirement_design_model import generate
        with _db() as db:
            source_owner,source=_source(db,owner,sid)
            blobs=dict(db.execute('SELECT id,image FROM images WHERE owner=? AND source=?',(source_owner,sid)))
        if not authorized(owner,username):
            raise PermissionError()
        with tempfile.TemporaryDirectory(prefix='requirement-design-') as temp:
            directory=Path(temp)
            for image in source['prototypes']:
                file=directory/image['file']
                file.write_bytes(blobs[image['id']])
            candidate=generate(source,directory)
        if not authorized(owner,username):
            raise PermissionError()
        result=json.dumps(candidate,ensure_ascii=False)
        error=None
    except PermissionError:
        error='账号或材料访问权限已变化'
    except Exception as exc:
        # Only share short, enumerated machine codes. Raw upstream text can contain source material.
        code=str(getattr(exc,'code','') or str(exc))
        if re.fullmatch('[a-z][a-z0-9_]{2,64}',code):
            error='生成失败（'+code+'）'
    finally:
        try:
            with _db() as db:
                db.execute("UPDATE designs SET status=?,finished=?,result=?,error=? WHERE id=? AND status='running'",
                           ('completed' if result else 'failed',time.time(),result,error,rid))
        finally:
            release_heavy_workload_slot('sync')
            os.close(lock)


def start(owner, username, sid, request_id):
    if not re.fullmatch('[a-f0-9]{64}',sid) or not re.fullmatch('[a-f0-9]{32}',request_id):
        raise ValueError('材料或请求编号无效')
    with _db() as db:
        _source(db,owner,sid)
        def existing():
            row=db.execute('SELECT * FROM designs WHERE owner=? AND request=?',(owner,request_id)).fetchone()
            if row and row['source'] != sid:
                raise ValueError('请求编号属于另一个需求')
            return _run(row) if row else None
        # Serialize claims through the row insert so a simultaneous retry sees its run.
        db.execute('BEGIN IMMEDIATE')
        prior=existing()
        if prior and prior['status'] != 'running':
            return prior
        lock=_lock()
        if lock is None:
            if prior:
                return prior
            raise Busy()
        claimed=False
        try:
            _reconcile(db)
            prior=existing()
            if prior:
                return prior
            if not acquire_heavy_workload_slot('sync',blocking=False):
                db.commit()  # Preserve orphan recovery even when no new work can start.
                raise Busy()
            claimed=True
            rid=uuid.uuid4().hex
            db.execute('INSERT INTO designs (id,owner,source,request,status,created) VALUES (?,?,?,?,?,?)',
                       (rid,owner,sid,request_id,'running',time.time()))
            db.commit()
            try:
                worker=threading.Thread(target=_generate,args=(owner,username,sid,rid,lock),daemon=True)
                worker.start()
            except Exception:
                db.execute("UPDATE designs SET status='failed',finished=?,error=? WHERE id=? AND status='running'",
                           (time.time(),'生成线程未能启动，可重新生成',rid))
                db.commit()
                return existing()
            claimed=False
            lock=None
            return existing()
        finally:
            if claimed:
                release_heavy_workload_slot('sync')
            if lock is not None:
                os.close(lock)
