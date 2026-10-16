"""Private durable screen-video sources. No device dispatch or execution authority."""
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

MAX_BYTES = 200 * 1024 * 1024
MAX_DURATION_MS = 600000
MAX_FRAMES = 60
_ADMISSION = threading.Lock()
@contextmanager
def _admit():
    if not _ADMISSION.acquire(blocking=False):raise ValueError('video_capacity')
    try:yield
    finally:_ADMISSION.release()


_ID = re.compile(r'uvs_[0-9a-f]{32}')
TERMINAL = {'ready', 'review_needed', 'failed', 'cancelled', 'interrupted'}


class VideoStore:
    def __init__(self, root=None):
        from .ui_case_draft_store import default_db_path
        self.root = Path(root or os.getenv('TASK_UI_VIDEO_ROOT') or default_db_path().parent / 'ui-video-sources').absolute()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or (hasattr(os,'geteuid') and self.root.stat().st_uid!=os.geteuid()): raise ValueError('video_store_path')
        os.chmod(self.root, 0o700)
        self.path = self.root / 'sources.sqlite3'
        fd=os.open(self.path,os.O_CREAT|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            if hasattr(os,'geteuid') and os.fstat(fd).st_uid!=os.geteuid():raise ValueError('video_store_path')
            os.fchmod(fd,0o600)
        finally:os.close(fd)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, owner TEXT NOT NULL, app TEXT NOT NULL, updated REAL NOT NULL, document TEXT NOT NULL)')
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            yield db
            db.commit()
        finally: db.close()

    def _save(self, db, doc):
        doc['updated_at'] = time.time()
        db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?)', (doc['source_id'], doc['owner_user_id'], doc['app_id'], doc['updated_at'], json.dumps(doc, ensure_ascii=False)))

    def get(self, identifier, owner, app):
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier): raise KeyError(identifier)
        with self.db() as db:
            row = db.execute('SELECT document FROM sources WHERE id=? AND owner=? AND app=?', (identifier, owner, app)).fetchone()
        if not row: raise KeyError(identifier)
        return json.loads(row[0])

    def list(self, owner, app):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT document FROM sources WHERE owner=? AND app=? ORDER BY updated DESC LIMIT 30', (owner, app))]

    def upload(self, profile, app, filename, stream, length, *, max_bytes=MAX_BYTES, actor=None, goal=''):
        if type(length) is not int or not 1 <= length <= max_bytes: raise ValueError('video_size')
        if not isinstance(filename, str) or len(filename) > 255 or Path(filename).suffix.lower() not in {'.mp4', '.mov'}: raise ValueError('video_format')
        with _admit():
            self.cleanup()
            with self.db() as db:
                self._check_admission(db,additional_bytes=length,new_source=True)
            identifier = 'uvs_' + uuid.uuid4().hex
            directory = self.root / identifier
            directory.mkdir(mode=0o700)
            try:
                remaining = length
                deadline = time.monotonic()+120
                with (directory/'source.mp4').open('xb') as output:
                    os.chmod(directory/'source.mp4', 0o600)
                    while remaining:
                        if time.monotonic()>deadline:raise ValueError('video_upload_timeout')
                        chunk = stream.read(min(65536, remaining))
                        if not chunk: raise ValueError('video_truncated_upload')
                        output.write(chunk); remaining -= len(chunk)
                doc = {'source_id': identifier, 'owner_user_id': profile['user_id'], 'app_id': app,
                       'filename': Path(filename).name, 'size': length, 'status': 'queued', 'error': '',
                       'created_at': time.time(), 'goal': goal, 'actor': actor or {}, 'segments': [],
                       'candidate_steps': [], 'uncertainties': [], 'draft_id': '', 'analysis_version': 1}
                with self.db() as db: self._save(db, doc)
                return doc
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise

    def _disk_bytes(self):
        total=0
        for path in self.root.rglob('*'):
            try:
                if path.is_file():total+=path.stat().st_size
            except FileNotFoundError:pass # Concurrent terminal cleanup only reduces usage.
        return total

    def _check_admission(self, db, *, additional_bytes=0, new_source=False):
        docs=[json.loads(row[0]) for row in db.execute('SELECT document FROM sources')]
        active=sum(d['status'] not in TERMINAL for d in docs)
        # Reserve the newly admitted decoder as well as existing active work.
        if active>=4 or (new_source and len(docs)>=100) or self._disk_bytes()+additional_bytes+128*1024**2*(active+1)>2*1024**3:
            raise ValueError('video_capacity')

    def retry(self, identifier, owner, app):
        # The same process admission lock serializes retries with streaming uploads;
        # SQLite protects the read/check/version transition from other mutations.
        with _admit():
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT document FROM sources WHERE id=? AND owner=? AND app=?',(identifier,owner,app)).fetchone()
                if not row:raise KeyError(identifier)
                doc=json.loads(row[0])
                if doc['status'] not in {'failed','cancelled','interrupted','review_needed'} or doc.get('draft_id') or doc.get('retained'):
                    raise ValueError('video_retry_state')
                self._check_admission(db)
                doc.pop('media',None)
                doc.pop('title',None)
                doc.update(status='queued',error='',error_code='',events=[],segments=[],candidate_steps=[],uncertainties=[],raw_events=[],raw_uncertainties=[],analysis_version=doc['analysis_version']+1)
                self._save(db,doc)
                return doc

    def transition(self, identifier, allowed, status, **fields):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT document FROM sources WHERE id=?', (identifier,)).fetchone()
            if not row: raise KeyError(identifier)
            doc = json.loads(row[0])
            if doc['status'] not in allowed: return None
            doc.update(fields, status=status)
            self._save(db, doc)
            return doc

    def cancel(self, identifier):
        return self.transition(identifier, {'queued', 'decoding', 'analyzing', 'interrupted', 'failed', 'review_needed'}, 'cancelled', error='已取消；已有草稿与来源证据保留')

    def recover(self):
        with self.db() as db:
            for row in db.execute('SELECT document FROM sources').fetchall():
                doc = json.loads(row[0])
                if doc['status'] in {'queued', 'decoding', 'analyzing'}:
                    doc.update(status='interrupted', error='服务重启已停止分析，请明确重试；不会自动执行')
                    self._save(db, doc)

    def cleanup(self):
        with self.db() as db:
            for row in db.execute('SELECT document FROM sources').fetchall():
                doc = json.loads(row[0])
                if not doc.get('draft_id') and not doc.get('retained') and doc['status'] in TERMINAL and doc['updated_at'] < time.time()-7*86400:
                    shutil.rmtree(self.root/doc['source_id'], ignore_errors=True)
                    db.execute('DELETE FROM sources WHERE id=?', (doc['source_id'],))
        # Crash during streaming leaves no row; clean only old orphan directories.
        with self.db() as db: known = {row[0] for row in db.execute('SELECT id FROM sources')}
        for path in self.root.glob('uvs_*'):
            if path.name not in known and path.stat().st_mtime < time.time()-86400:
                shutil.rmtree(path, ignore_errors=True)


def _tool(name):
    candidate = os.getenv('UI_VIDEO_'+name.upper()) or shutil.which(name)
    if not candidate and Path('/opt/homebrew/bin', name).is_file(): candidate = '/opt/homebrew/bin/'+name
    if not candidate or not Path(candidate).is_file(): raise ValueError('video_decoder_missing')
    return candidate


def _process(command, cancelled, timeout=150):
    if cancelled(): raise ValueError('video_cancelled')
    # Resource limits are applied in a fresh interpreter, never unsafe preexec_fn
    # in the multi-threaded HTTP process. 2GB virtual memory, CPU 120s, 32MB/file.
    wrapper = "import os,resource,sys;resource.setrlimit(resource.RLIMIT_CPU,(120,120));sys.platform.startswith('linux') and resource.setrlimit(resource.RLIMIT_AS,(2147483648,2147483648));resource.setrlimit(resource.RLIMIT_FSIZE,(33554432,33554432));os.execv(sys.argv[1],sys.argv[1:])"
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen([sys.executable, '-c', wrapper, *command], stdout=stdout, stderr=stderr, start_new_session=True)
        deadline = time.monotonic()+timeout
        try:
            while process.poll() is None:
                if cancelled(): raise ValueError('video_cancelled')
                if sys.platform == 'darwin':
                    usage = subprocess.run(['/bin/ps', '-o', 'rss=', '-p', str(process.pid)], capture_output=True, timeout=2).stdout.strip()
                    if usage and int(usage) > 512*1024: raise ValueError('video_memory_limit')
                if time.monotonic() > deadline: raise ValueError('video_decode_timeout')
                time.sleep(.05)
            stdout.seek(0); stderr.seek(0)
            out, err = stdout.read(2*1024**2), stderr.read(32*1024**2)
            if process.returncode: raise ValueError('video_decode_invalid')
            return out, err
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


def _sync_args(cancelled):
    # FFmpeg 4.4 only has vsync; 9 removed it. Inspect actual capabilities instead
    # of requiring a distribution upgrade or trusting an advertised version.
    help_text,_=_process([_tool('ffmpeg'),'-hide_banner','-h','full'],cancelled,20)
    return ['-fps_mode','vfr'] if b'-fps_mode' in help_text else ['-vsync','vfr']


def decode_video(video, output, cancelled):
    if cancelled(): raise ValueError('video_cancelled')
    probe, _ = _process([_tool('ffprobe'), '-v', 'error', '-protocol_whitelist', 'file,pipe', '-f', 'mov', '-show_format', '-show_streams', '-of', 'json', str(video)], cancelled, 20)
    try:
        metadata = json.loads(probe)
        streams = [s for s in metadata['streams'] if s['codec_type'] == 'video']
        stream = streams[0]
        duration = round(float(metadata['format']['duration'])*1000)
        width, height = int(stream['width']), int(stream['height'])
    except (KeyError, IndexError, ValueError, TypeError): raise ValueError('video_metadata_invalid') from None
    if len(streams) != 1 or not 1 <= duration <= MAX_DURATION_MS or not 1 <= width <= 4096 or not 1 <= height <= 4096 or width*height > 9000000:
        raise ValueError('video_limits')
    # First pass scans the entire source, discards near-duplicate waiting frames,
    # and records timestamps only. Preserve motion boundaries and abrupt luminance
    # cuts, then distribute remaining anchors across the entire timeline. Continuous
    # scrolling is motion, not a reason to reject an otherwise short recording.
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    for old in output.glob('frame-*.jpg'): old.unlink()
    scale = f"scale={min(width,1280)}:{min(height,1280)}:force_original_aspect_ratio=decrease"
    base = scale+",format=yuv420p,mpdecimate=hi=768:lo=320:frac=0.10"
    common = [_tool('ffmpeg'), '-nostdin', '-hide_banner', '-v', 'info', '-threads', '1', '-protocol_whitelist', 'file,pipe', '-f', 'mov', '-i', str(video), '-xerror', '-an', '-sn', '-dn']
    _, scan = _process([*common, '-vf', base+',signalstats,metadata=print:key=lavfi.signalstats.YDIF,showinfo', '-f', 'null', '-'], cancelled)
    all_times = [round(float(t)*1000) for t in re.findall(rb'\bn:\s*\d+.*?pts_time:([\d.e+\-]+)', scan)]
    if not all_times or len(all_times)>72000:raise ValueError('video_split_required')
    clusters=[];first=0
    for index in range(1,len(all_times)+1):
        if index==len(all_times) or all_times[index]-all_times[index-1]>300:
            clusters.append((first,index-1));first=index
    selected={0, len(all_times)-1}
    for first,last in clusters:
        selected.update((max(0,first-1),first,last))
    # Mean absolute pixel change retains spatial cuts even when two pages have
    # identical overall brightness. Unlike scene-score acceleration, this also
    # detects repeated abrupt cuts at a constant cadence. Smooth motion remains
    # eligible for temporal sampling rather than treating every frame as a cut.
    changes=[float(value) for value in re.findall(rb'lavfi\.signalstats\.YDIF=([\d.e+\-]+)',scan)]
    if len(changes)==len(all_times):
        for index,change in enumerate(changes[1:],1):
            if change>=64:
                selected.update((index-1,index))
    budget=MAX_FRAMES-1  # reserve the independently decoded final anchor
    if len(selected)>budget:raise ValueError('video_split_required')
    # One-second targets use actual changed-frame timestamps. Add the least
    # represented targets first so a full budget never silently truncates the tail.
    from bisect import bisect_left
    candidates=set()
    for target in range(1000,duration,1000):
        position=bisect_left(all_times,target)
        choices=[i for i in (position-1,position) if 0<=i<len(all_times)]
        candidates.add(min(choices,key=lambda i:abs(all_times[i]-target)))
    candidates-=selected
    while candidates and len(selected)<budget:
        index=max(candidates,key=lambda i:(min(abs(all_times[i]-all_times[j]) for j in selected),-i))
        selected.add(index);candidates.remove(index)
    indexes=sorted(selected)
    # Do not compress long sustained activity into sparse, misleading evidence.
    # Static intervals may be long: only enforce density inside motion clusters.
    for first,last in clusters:
        anchors=[all_times[i] for i in indexes if first<=i<=last]
        if any(b-a>3000 for a,b in zip(anchors,anchors[1:])):
            raise ValueError('video_split_required')
    selector="+".join(f"eq(n,{i})" for i in indexes)
    _, log = _process([*common, '-vf', base+",select='"+selector+"',showinfo", *_sync_args(cancelled), '-threads', '1', '-pix_fmt', 'yuvj420p', '-q:v', '4', str(output/'frame-%03d.jpg')], cancelled)
    times = [round(float(t)*1000) for t in re.findall(rb'\bn:\s*\d+.*?pts_time:([\d.e+\-]+)', log)]
    files = sorted(output.glob('frame-*.jpg'))
    if not files or len(times) != len(files) or times!=[all_times[i] for i in indexes]: raise ValueError('video_frames_invalid')
    if sum(path.stat().st_size for path in files)>16*1024**2:raise ValueError('video_frames_invalid')
    # Always decode a last-frame anchor to expose missing endings rather than
    # representing an earlier frame as the end of the recording.
    tail = output/'frame-end.jpg'
    _process([_tool('ffmpeg'), '-nostdin', '-v', 'error', '-threads', '1', '-sseof', '-0.08', '-protocol_whitelist', 'file,pipe', '-f', 'mov', '-i', str(video), '-xerror', '-an', '-sn', '-dn', '-vf', scale, '-frames:v', '1', '-threads', '1', '-pix_fmt', 'yuvj420p', '-y', str(tail)], cancelled, 20)
    if tail.exists(): files.append(tail); times.append(max(times[-1], duration-80))
    if len(files)>MAX_FRAMES: raise ValueError('video_split_required')
    from PIL import Image, ImageChops, ImageStat
    signatures = []
    black = True
    for path in files:
        with Image.open(path) as img:
            gray = img.convert('L').resize((32,32)); signatures.append(gray.copy())
            if ImageStat.Stat(gray).mean[0] > 12: black = False
    static = all(ImageStat.Stat(ImageChops.difference(signatures[0], image)).mean[0] < 2 for image in signatures[1:])
    frames = [{'frame_id': path.stem, 'timestamp_ms': timestamp} for path,timestamp in zip(files,times)]
    return {'duration_ms': duration, 'coverage_start_ms': 0, 'coverage_end_ms': duration,
            'frames': frames, 'static': static, 'black': black, 'width': width, 'height': height}


def service_store():
    from task_server.workers.ui_video_worker import ensure_worker
    store=VideoStore();ensure_worker(store)
    return store


def owned_source(actor, identifier, app, permission='ui.view'):
    from .ui_execution_contract import require_ui_actor
    from .ui_case_draft_service import _application
    profile=require_ui_actor(actor,permission=permission,app_id=app)
    _application(app)
    store=service_store()
    return store,profile,store.get(identifier,profile['user_id'],app)


def public_source(document):
    return {k:v for k,v in document.items() if k not in {'actor','owner_user_id','baseline_examples'}}


def create_video_source(actor, upload):
    from .ui_execution_contract import require_ui_actor
    from .operation_attribution import current_actor
    from .ui_case_draft_service import _application
    profile=require_ui_actor(actor,permission='ui.edit',app_id=upload['app_id']);_application(upload['app_id'])
    goal=upload.get('goal','')
    if not isinstance(goal,str) or len(goal)>2000:raise ValueError('video_goal_invalid')
    store=service_store()
    document=store.upload(profile,upload['app_id'],upload['filename'],upload['stream'],upload['length'],actor=dict(current_actor() if actor is None else actor),goal=goal)
    from task_server.workers.ui_video_worker import ensure_worker
    ensure_worker(store).set()
    return public_source(document)


def import_video_draft(actor, identifier, app):
    from .ui_case_draft_service import _store
    from .ui_case_draft_contract import build_draft_yaml
    from .ui_video_analysis import draft_proposal
    store,profile,document=owned_source(actor,identifier,app,'ui.edit')
    # Once imported, retries always reopen the same immutable-origin draft.
    if document.get('draft_id'):return _store().get(document['draft_id'],profile['user_id'])
    if document['status']!='ready':raise ValueError('video_not_ready')
    proposal,snapshot=draft_proposal(document)
    compiled=build_draft_yaml(proposal,app)
    store.transition(identifier,{'ready'},'ready',retained=True)
    draft=_store().create(profile,app,'来源：上传录屏，观察与推断未经手机验证。',recording_snapshot=snapshot,
                           recording_content={**proposal,'yaml_content':compiled['yaml_content'],'ready':False})
    # create is deterministic; concurrent imports return existing edited content.
    store.transition(identifier,{'ready'},'ready',draft_id=draft['draft_id'])
    return draft
