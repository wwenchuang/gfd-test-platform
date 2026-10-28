"""Private durable screen-video sources. No device dispatch or execution authority."""
import json
from decimal import Decimal, ROUND_HALF_UP
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
MAX_FRAMES = 300
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
        try:
            source_limit=int(os.getenv('TASK_UI_VIDEO_MAX_SOURCES','100'))
        except ValueError:
            source_limit=100
        if not 1<=source_limit<=1000:source_limit=100
        # Reserve the newly admitted decoder as well as existing active work.
        if active>=4 or (new_source and len(docs)>=source_limit) or self._disk_bytes()+additional_bytes+128*1024**2*(active+1)>2*1024**3:
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
                doc.pop('failure_diagnostic',None)
                from .ui_video_workspace import PRIVATE_FIELDS
                for key in PRIVATE_FIELDS:doc.pop(key,None)
                doc.update(status='queued',error='',error_code='',events=[],segments=[],candidate_steps=[],uncertainties=[],raw_events=[],first_pass_events=[],reconciliation_audit=None,raw_uncertainties=[],omitted_retries=[],print_recovery=None,response_review=[],input_recovery=[],analysis_progress={},analysis_version=doc['analysis_version']+1)
                self._save(db,doc)
                return doc

    def transition(self, identifier, allowed, status, expected_version=None, **fields):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT document FROM sources WHERE id=?', (identifier,)).fetchone()
            if not row: raise KeyError(identifier)
            doc = json.loads(row[0])
            if doc['status'] not in allowed or (expected_version is not None and doc['analysis_version']!=expected_version): return None
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


def _transition_anchors(times,changes,include_direction=True):
    """Keep stable endpoints and direction evidence of soft page transitions."""
    from bisect import bisect_left
    if len(changes)!=len(times):return set()
    runs=[]
    for index,change in enumerate(changes):
        if change<12:continue
        if not runs or times[index]-times[runs[-1][-1]]>250:runs.append([index])
        else:runs[-1].append(index)
    selected=set()
    for position,run in enumerate(runs):
        after=min(len(times)-1,bisect_left(times,times[run[-1]]+150))
        if position+1<len(runs):after=min(after,runs[position+1][0]-1)
        selected.update((max(0,run[0]-1),after))
        # Only a short, substantial multi-frame transition needs direction
        # evidence; do not spend the bounded budget on sustained spinners.
        if include_direction and 80<=times[run[-1]]-times[run[0]]<=1000 and max(changes[i] for i in run)>=24:
            middle=min(run,key=lambda i:abs(times[i]-(times[run[0]]+times[run[-1]])/2))
            selected.add(middle)
    return selected



def _timestamp_ms(value):
    # FFmpeg logs decimal timestamps; avoid binary-float half-ms drift.
    text=value.decode('ascii') if isinstance(value,bytes) else str(value)
    return int((Decimal(text)*1000).to_integral_value(rounding=ROUND_HALF_UP))


def _micro_motion_groups(times, changes):
    """Retain short local motion as visual evidence, never as a claimed touch.

    Clock ticks are isolated; sustained spinners are long. Small touch marks can
    occupy too few pixels to qualify as a page transition, so preserve the short
    burst's onset, middle, release and adjacent context for the vision model.
    """
    if len(times)!=len(changes):return []
    runs=[]
    # Keep all original contiguous bursts. Bridged candidates supplement them;
    # joining several short bursts must never turn them into a discarded spinner.
    for bridge_quiet in (False,True):
        grouped=[]
        for index,change in enumerate(changes):
            if not .08<=change<12:continue
            if (not grouped or times[index]-times[grouped[-1][-1]]>150 or
                (not bridge_quiet and index!=grouped[-1][-1]+1) or
                any(c>=12 for c in changes[grouped[-1][-1]+1:index])):
                grouped.append([index])
            else:grouped[-1].append(index)
        runs.extend(grouped)
    selected=[]
    # A faint one-frame pointer can precede a visible response without meeting
    # the sustained-burst threshold. Preserve original before/onset/response
    # pixels after a quiet gap; this is evidence, not a claimed tap.
    for index in range(1,len(times)):
        if times[index]-times[index-1]<200 or not .04<=changes[index]<.08:continue
        response=next((j for j in range(index+1,min(len(times),index+7))
                       if 0<times[j]-times[index]<=150 and .08<=changes[j]<12
                       and not any(c>=12 for c in changes[index+1:j])),None)
        if response is not None:selected.append({index-1,index,response})
    for run in runs:
        if len(run)<2 or not 30<=times[run[-1]]-times[run[0]]<=800:continue
        middle=min(run,key=lambda i:abs(times[i]-(times[run[0]]+times[run[-1]])/2))
        group={max(0,run[0]-1),run[0],middle,run[-1],min(len(times)-1,run[-1]+1)}
        if group not in selected:selected.append(group)
    return selected



def _micro_motion_anchors(times, changes):
    return set().union(*_micro_motion_groups(times, changes))


def _select_micro_motion(groups, times, base, limit, cancelled=lambda:False):
    """Add complete micro-bursts across the timeline without displacing pages."""
    selected=set(base)
    candidates=[{times[j] for j in group} for group in groups]
    centers=[(min(group)+max(group))/2 for group in candidates]
    remaining={i for i,candidate in enumerate(candidates) if not candidate<=selected}
    distance={i:float('inf') for i in remaining}
    chosen=False
    while remaining and len(selected)<limit:
        if cancelled():raise ValueError('video_cancelled')
        # Preserve brief onset/response triplets before filling longer bursts;
        # then complete partially captured bursts and spread equal-cost ones.
        index=max(remaining,key=lambda i:(len(candidates[i])<=3,-len(candidates[i]-selected),distance[i] if chosen else -i,-i))
        remaining.remove(index)
        candidate=candidates[index]
        if len(selected|candidate)>limit:continue
        selected.update(candidate);chosen=True
        remaining={i for i in remaining if not candidates[i]<=selected}
        for i in remaining:distance[i]=min(distance[i],abs(centers[i]-centers[index]))
    return selected, sum(candidate<=selected for candidate in candidates)


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
    all_times = [_timestamp_ms(t) for t in re.findall(rb'\bn:\s*\d+.*?pts_time:([\d.e+\-]+)', scan)]
    if not all_times or len(all_times)>72000:raise ValueError('video_split_required')
    clusters=[];first=0
    for index in range(1,len(all_times)+1):
        if index==len(all_times) or all_times[index]-all_times[index-1]>300:
            clusters.append((first,index-1));first=index
    selected={0, len(all_times)-1}
    # Mean absolute pixel change retains spatial cuts even when two pages have
    # identical overall brightness. Unlike scene-score acceleration, this also
    # detects repeated abrupt cuts at a constant cadence. Smooth motion remains
    # eligible for temporal sampling rather than treating every frame as a cut.
    changes=[float(value) for value in re.findall(rb'lavfi\.signalstats\.YDIF=([\d.e+\-]+)',scan)]
    if len(changes)==len(all_times):
        for index,change in enumerate(changes[1:],1):
            if change>=64:
                selected.update((index-1,index))
    transition_anchors=_transition_anchors(all_times,changes)
    direction_extra=transition_anchors-_transition_anchors(all_times,changes,include_direction=False)-selected
    selected.update(transition_anchors)
    for first,last in clusters:
        for index in (max(0,first-1),first,last):
            if min(abs(all_times[index]-all_times[j]) for j in selected)>400:
                selected.add(index)
    # Independent sensitive pass keeps small/local motion that the page-level
    # decimator may discard. No coordinates or action labels leave this layer.
    sensitive=scale+",format=yuv420p,mpdecimate=hi=128:lo=64:frac=0.01"
    _, micro_scan=_process([*common,'-vf',sensitive+',signalstats,metadata=print:key=lavfi.signalstats.YDIF,showinfo','-f','null','-'],cancelled)
    micro_times=[_timestamp_ms(t) for t in re.findall(rb'\bn:\s*\d+.*?pts_time:([\d.e+\-]+)',micro_scan)]
    micro_changes=[float(t) for t in re.findall(rb'lavfi\.signalstats\.YDIF=([\d.e+\-]+)',micro_scan)]
    if len(micro_times)>72000 or len(micro_times)!=len(micro_changes):raise ValueError('video_frames_invalid')
    micro_groups=_micro_motion_groups(micro_times,micro_changes)
    base_limit=min(240,60 if duration<=90000 else ((duration+59999)//60000)*60)
    # Reserve only observed direction anchors, not an arbitrary larger sample.
    # Keep per-request image and aggregate byte limits unchanged.
    frame_limit=min(MAX_FRAMES,base_limit+len(direction_extra))
    if len(selected)>=frame_limit:
        # Dense page changes may need the existing per-minute allowance even
        # below 90 seconds. Expand only to preserve observed anchors; smooth
        # videos keep the smaller budget, and sub-minute rapid cuts still fail.
        base_limit=max(base_limit,min(240,((duration+59999)//60000)*60))
        frame_limit=min(MAX_FRAMES,base_limit+len(direction_extra))
    budget=frame_limit-1  # preserve original page density and final anchor first
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
    # FFmpeg expression parsing has a recursion limit: keep the sum balanced.
    def expression(items):
        if len(items)==1:return f"eq(n,{items[0]})"
        middle=len(items)//2
        return "("+expression(items[:middle])+"+"+expression(items[middle:])+")"
    base_times={all_times[i] for i in indexes}
    combined,sampled_groups=_select_micro_motion(micro_groups,micro_times,base_times,MAX_FRAMES-1,cancelled)
    expected_times=sorted(combined)
    if combined-base_times:
        # Select by original timestamps: sensitive-pass indexes are not indexes
        # in the old decimated stream. Never relabel an unrelated page frame.
        # Use the original integer PTS, not rounded seconds in the selector.
        # 29.97fps includes half-ms values whose binary floats differ between
        # Python and FFmpeg even with nominally identical rounding rules.
        pts_by_time={_timestamp_ms(t):int(pts) for pts,t in re.findall(
            rb'\bn:\s*\d+.*?pts:\s*(-?\d+)\s+pts_time:([\d.e+\-]+)',scan+micro_scan)}
        if any(t not in pts_by_time for t in expected_times):raise ValueError('video_frames_invalid')
        selector=expression([pts_by_time[t] for t in expected_times]).replace('eq(n,','eq(pts,')
        extract_base=scale+",format=yuv420p"
    else:
        selector=expression(indexes);extract_base=base
    _, log = _process([*common, '-vf', extract_base+",select='"+selector+"',showinfo", *_sync_args(cancelled), '-threads', '1', '-pix_fmt', 'yuvj420p', '-q:v', '4', str(output/'frame-%03d.jpg')], cancelled)
    times = [_timestamp_ms(t) for t in re.findall(rb'\bn:\s*\d+.*?pts_time:([\d.e+\-]+)', log)]
    files = sorted(output.glob('frame-*.jpg'))
    if not files or len(times) != len(files) or times!=expected_times: raise ValueError('video_frames_invalid')
    if sum(path.stat().st_size for path in files)>16*1024**2:raise ValueError('video_frames_invalid')
    # Always decode a last-frame anchor to expose missing endings rather than
    # representing an earlier frame as the end of the recording.
    tail = output/'frame-end.jpg'
    _process([_tool('ffmpeg'), '-nostdin', '-v', 'error', '-threads', '1', '-sseof', '-0.08', '-protocol_whitelist', 'file,pipe', '-f', 'mov', '-i', str(video), '-xerror', '-an', '-sn', '-dn', '-vf', scale, '-frames:v', '1', '-threads', '1', '-pix_fmt', 'yuvj420p', '-y', str(tail)], cancelled, 20)
    if tail.exists() and duration-80>times[-1]:
        files.append(tail);times.append(duration-80)
    elif tail.exists():tail.unlink()
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
            'frames': frames, 'static': static, 'black': black, 'width': width, 'height': height,
            'micro_motion_frame_count':len(combined-base_times),
            'micro_motion_groups_total':len(micro_groups), 'micro_motion_groups_sampled':sampled_groups}


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
    from .ui_video_workspace import PRIVATE_FIELDS
    return {k:v for k,v in document.items() if k not in {'actor','owner_user_id','baseline_examples'}|PRIVATE_FIELDS}


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
    from .ui_video_execution_contract import compiler_options
    compiled=build_draft_yaml(proposal,app,**compiler_options(snapshot,app,proposal['steps']))
    store.transition(identifier,{'ready'},'ready',retained=True)
    draft=_store().create(profile,app,'来源：上传录屏，观察与推断未经手机验证。',recording_snapshot=snapshot,
                           recording_content={**proposal,'yaml_content':compiled['yaml_content'],'ready':False})
    # create is deterministic; concurrent imports return existing edited content.
    store.transition(identifier,{'ready'},'ready',draft_id=draft['draft_id'])
    return draft
