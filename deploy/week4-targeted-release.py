#!/usr/bin/env python3
"""Read-only Week 4 backend preflight. A task snapshot is NOT an admission gate."""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import urllib.request
from pathlib import Path

STAGE = Path('/root/ai-test-practice-20261105/w04-backend-stage-20261106')
ROOT = Path('/opt/midscene-task-platform')
BACKUP_PARENT = Path('/root/ai-test-practice-20261105')
VIDEO = Path('/opt/midscene-auth/ui-video-sources/sources.sqlite3')
UI = Path('/opt/midscene-auth/ui-executions.sqlite3')
JOBS = Path('/opt/midscene-learning/jobs.json')
GENERATE = Path('/opt/midscene-generate-jobs')
HEALTH = 'http://127.0.0.1:8091/api/health'
CONTAINER = 'midscene-api-testing-postgres-1'
NEW = {
    'task_server/api_testing/contracts/source.py': '69a1ff4d899348b5ae4bf103a1d8c01ac16ff2dc17c17a1e444b44dceb29425d',
    'task_server/api_testing/services/source_service.py': '2d32eccd89cb6611af574ea56fa320577a3007d3bd6e45b59176874a7a1ee345',
    'task_server/api_testing/services/response_contract_diff.py': '34827db25a1469c0b2279070cdd544eb5d106e6c1722287989d2803808a3b294',
    'task_server/api_testing/assertions.py': '2f66483db4d314dd243935f72bbeda6d321f59e38d8a011df59c5c6ce09874ec',
}
OLD = {
    'task_server/api_testing/contracts/source.py': 'f9255daefdd9d5d410e1522fc2ab9322823b2cf2cb6369ed17e6bdc0919e4146',
    'task_server/api_testing/services/source_service.py': '77bdf8a727c52a171562832dc5544a1e25df34112c36b7dbb6b0271236fe0c9d',
    'task_server/api_testing/assertions.py': '93bb7bec4e24bc411216be8ed340395bb3a387a32a05518f1a49d693c003127a',
}
ADDED = 'task_server/api_testing/services/response_contract_diff.py'
FILES = tuple(NEW)
SERVICES = {'main': 'midscene-task.service', 'worker': 'midscene-api-worker.service'}
UI_ACTIVE_SQL = "SELECT count(*) FROM case_attempts WHERE COALESCE(status,'') NOT IN ('passed','failed','cancelled') AND NOT (COALESCE(status,'')='interrupted' AND COALESCE(stop_confirmed,0)=1)"
LOAD_ACTIVE_SQL = "SELECT count(*) FROM api_load_runs WHERE COALESCE(state,'') NOT IN ('draft','finished','failed','cancelled') AND NOT (COALESCE(state,'')='queued' AND COALESCE(id,'')='1d2af319-f6c9-4cd1-9667-5ae3424a55c2' AND started_at IS NULL AND COALESCE(created_at < '2026-09-12'::date, false))"
# Only an untouched assigned shard of the exact old queued run is an idle
# allocation. Unknown or claimed shards still stop the release. This is a
# snapshot; the parent run can be started concurrently by another actor.
HISTORIC_SHARD_ACTIVE_SQL = """SELECT count(*) FROM api_load_run_shards s
WHERE s.run_id='1d2af319-f6c9-4cd1-9667-5ae3424a55c2'
  AND COALESCE(s.state,'') NOT IN ('finished','failed','cancelled','lost')
  AND NOT (
    COALESCE(s.state,'')='assigned'
    AND s.last_heartbeat_at IS NULL
    AND COALESCE(s.process_info='{}', false)
    AND NOT EXISTS (SELECT 1 FROM api_load_metric_buckets b WHERE b.shard_id=s.id)
    AND NOT EXISTS (SELECT 1 FROM api_load_samples t WHERE t.shard_id=s.id)
    AND EXISTS (SELECT 1 FROM api_load_runs r WHERE r.id=s.run_id
      AND r.state='queued' AND r.started_at IS NULL
      AND r.created_at < '2026-09-12')
  )"""


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def old_files_ok():
    added = ROOT / ADDED
    if added.exists() or added.is_symlink(): raise ValueError('new module already present')
    for name, expected in OLD.items():
        path = ROOT / name
        if path.is_symlink() or not path.is_file() or sha(path) != expected:
            raise ValueError('installed baseline mismatch: ' + name)


def stage_files_ok():
    for name, expected in NEW.items():
        path = STAGE / name
        if path.is_symlink() or not path.is_file() or sha(path) != expected:
            raise ValueError('staged payload mismatch: ' + name)


def cmd(*args, timeout=30):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout).stdout.strip()


def sqlite_scalar(path, sql):
    if not path.is_file(): raise ValueError('database missing: ' + str(path))
    db = sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True, timeout=3)
    try:
        result = db.execute(sql).fetchone()
        if result is None or len(result) != 1 or type(result[0]) is not int:
            raise ValueError('database check yielded no count: ' + str(path))
        return result[0]
    finally: db.close()


def postgres_scalar(sql):
    out = cmd('docker', 'exec', CONTAINER, 'sh', '-c',
              'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$1"',
              'sh', sql, timeout=10)
    if not out.isdigit(): raise ValueError('PostgreSQL check returned invalid output')
    return int(out)


def health():
    with urllib.request.urlopen(HEALTH, timeout=3) as response:
        data = json.load(response)
    if data.get('ok') is not True or data.get('dependencies', {}).get('pyyaml') is not True:
        raise ValueError('health check failed')
    return data


class LiveOps:
    def preflight(self):
        stage_files_ok()
        old_files_ok()
        for service in SERVICES.values(): cmd('systemctl', 'is-active', '--quiet', service, timeout=5)
        health()

    def active_check(self):
        # Any missing source, parse error, unknown status, or unavailable dependency aborts.
        body = health()
        runtime = body.get('runtime', {})
        for key in ('background_running', 'background_waiting_for_slot', 'background_queued', 'heavy_workloads_active'):
            if type(runtime.get(key)) is not int or runtime[key] != 0:
                raise ValueError('background status unavailable or active: ' + key)
        if type(runtime.get('active_requests')) is not int or runtime['active_requests'] > 1:
            raise ValueError('HTTP request status unavailable or active')
        if sqlite_scalar(VIDEO, "SELECT count(*) FROM sources WHERE COALESCE(json_extract(document,'$.status'),'') NOT IN ('ready','failed','review_needed','cancelled','interrupted')"):
            raise ValueError('video task active or unknown')
        if sqlite_scalar(UI, UI_ACTIVE_SQL):
            raise ValueError('UI attempt active, unsettled, or unknown')
        self.check_jobs()
        if postgres_scalar("SELECT count(*) FROM api_executions WHERE COALESCE(state,'') NOT IN ('DONE','CANCELLED','ARCHIVED')"):
            raise ValueError('API execution active or unknown')
        # One historic queued load is preserved only if still unscheduled/unstarted and unchanged.
        if postgres_scalar(LOAD_ACTIVE_SQL):
            raise ValueError('load task active or unknown')
        if postgres_scalar("SELECT count(*) FROM api_load_schedules WHERE active_run_id='1d2af319-f6c9-4cd1-9667-5ae3424a55c2'"):
            raise ValueError('historic load is now associated with a schedule')
        if postgres_scalar(HISTORIC_SHARD_ACTIVE_SQL):
            raise ValueError('historic load has a nonterminal shard')
        self.check_worker_queues()
        print('LISTED CHECKS CLEAR ONLY: other tasks and Redis messages may exist; this is not an admission gate', flush=True)

    def check_worker_queues(self):
        raw_pid = cmd('systemctl', 'show', SERVICES['worker'], '-p', 'MainPID', '--value', timeout=5)
        if not raw_pid.isdigit() or int(raw_pid) <= 0: raise ValueError('worker PID unavailable')
        # Inherit the already-running worker's environment without printing credentials.
        raw = Path('/proc/' + raw_pid + '/environ').read_bytes().split(b'\0')
        env = {}
        for item in raw:
            if not item: continue
            if b'=' not in item: raise ValueError('invalid worker environment')
            key, value = item.split(b'=', 1)
            env[os.fsdecode(key)] = os.fsdecode(value)
        for kind in ('active', 'reserved', 'scheduled'):
            result = subprocess.run(
                ['/opt/midscene-task-platform/.venv/bin/celery', '-A',
                 'task_server.api_testing.tasks:celery_app', 'inspect', kind, '--timeout=5', '--json'],
                cwd=str(ROOT), env=env, check=True, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=12)
            data = json.loads(result.stdout)
            if not isinstance(data, dict) or set(data) != {'celery@qa.test.sonic-00.txsh'}:
                raise ValueError('worker inspection missing or unexpected workers: ' + kind)
            if data['celery@qa.test.sonic-00.txsh'] != []:
                raise ValueError('worker tasks active or malformed: ' + kind)

    def check_jobs(self):
        if not JOBS.is_file() or JOBS.is_symlink(): raise ValueError('job status file unavailable')
        jobs = json.loads(JOBS.read_text())
        if not isinstance(jobs, list): raise ValueError('invalid job status file')
        # Unknown / incomplete statuses cannot be treated as terminal.
        for row in jobs:
            if not isinstance(row, dict): raise ValueError('invalid job status entry')
            status = str(row.get('status') or row.get('state') or '').strip().lower()
            if status not in {'success', 'passed', 'failed', 'cancelled', 'canceled', 'timeout', 'completed', 'done', 'error'}:
                raise ValueError('device job active or unknown')
        if not GENERATE.is_dir() or GENERATE.is_symlink(): raise ValueError('generation job directory unavailable')
        for path in GENERATE.glob('*.json'):
            if path.is_symlink() or not path.is_file(): raise ValueError('invalid generation job file')
            row = json.loads(path.read_text())
            if not isinstance(row, dict): raise ValueError('invalid generation job record')
            status = str(row.get('status') or '').strip().lower()
            if status not in {'success', 'passed', 'failed', 'cancelled', 'canceled', 'timeout', 'completed', 'done', 'error'}:
                raise ValueError('generation job active or unknown')


# This serializes this deployer only. Other legacy deploy commands do not take this lock.
import fcntl
import signal
import shutil
import tempfile
import time

GROUPS = {'main': tuple(name for name in NEW if name != 'task_server/api_testing/assertions.py'),
          'worker': ('task_server/api_testing/assertions.py',)}
LOCK = Path('/run/lock/week4-targeted-release.lock')

class ReleaseFailure(RuntimeError): pass
class CompetingRelease(ValueError): pass


def apply_kind(ops, kind):
    touched = False
    try:
        ops.preflight()
        ops.active_check()
        ops.backup(kind)
        ops.active_check()
        touched = True # stop may have succeeded despite a failed/timed-out command
        ops.stop(kind)
        ops.verify_stopped(kind)
        ops.install(kind)
        ops.start(kind)
        ops.ready(kind)
        ops.verify_installed(kind)
    except BaseException as exc:
        if not touched:
            raise ReleaseFailure('preflight failed, service unchanged: ' + repr(exc)) from exc
        failures = []
        previous = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
        try:
            competing = False
            try:
                ops.halt_for_recovery(kind)
                conflicts = ops.restore_known(kind)
                if conflicts: failures.append('unknown changed files: ' + ', '.join(conflicts))
            except CompetingRelease as rollback_exc:
                competing = True
                failures.append('competing release left in control: ' + repr(rollback_exc))
            except BaseException as rollback_exc:
                failures.append('rollback failed: ' + repr(rollback_exc))
            if not competing:
                try:
                    ops.start(kind)
                    ops.ready(kind)
                except BaseException as start_exc:
                    failures.append('service restart/readiness failed: ' + repr(start_exc))
        finally:
            for s, handler in previous.items(): signal.signal(s, handler)
        msg = 'deployment failed: ' + repr(exc)
        if failures: msg += '; RECOVERY REQUIRES INSPECTION: ' + '; '.join(failures)
        else: msg += '; known original files restored and service responding'
        raise ReleaseFailure(msg) from exc


def stop_signal(signum, frame):
    raise RuntimeError('interrupted by signal ' + str(signum))


class TargetOps(LiveOps):
    def __init__(self):
        self.backup_paths = {}
        self.before_pids = {}
        self.started_pids = {}

    def preflight(self):
        stage_files_ok()
        # For staged main then worker apply, main can already be NEW.
        for name, expected in OLD.items():
            p = ROOT / name
            if p.is_symlink() or not p.is_file() or sha(p) not in {expected, NEW[name]}:
                raise ValueError('unexpected installed version: ' + name)
        added = ROOT / ADDED
        if added.is_symlink() or (added.exists() and sha(added) != NEW[ADDED]):
            raise ValueError('unexpected contract module')
        for svc in SERVICES.values(): cmd('systemctl','is-active','--quiet',svc,timeout=5)
        health()
        self.check_other_deploys()

    def check_other_deploys(self):
        own = {os.getpid(), os.getppid()}
        names = ('update-main-server.sh','install-server.sh','release-server.sh','week4-targeted-release.py')
        for p in Path('/proc').iterdir():
            if not p.name.isdigit() or int(p.name) in own: continue
            try: words = p.joinpath('cmdline').read_bytes().split(b'\0')
            except (FileNotFoundError, ProcessLookupError): continue
            except OSError as exc: raise ValueError('cannot inspect process '+p.name) from exc
            # Avoid matching arbitrary code, only shell script path arguments.
            if any(os.fsdecode(w).endswith(names) for w in words if w):
                raise CompetingRelease('another release process is running: pid '+p.name)

    def active_check(self):
        super().active_check()
        if sqlite_scalar(UI,"SELECT count(*) FROM batches WHERE COALESCE(status,'') NOT IN ('passed','failed','cancelled')"):
            raise ValueError('UI batch active or unknown')
        if sqlite_scalar(UI,"SELECT count(*) FROM device_leases"):
            raise ValueError('UI device lease exists')
        self.check_redis()
        self.check_other_deploys()

    def worker_env(self):
        raw = cmd('systemctl','show',SERVICES['worker'],'-p','MainPID','--value',timeout=5)
        if not raw.isdigit() or int(raw)<=0: raise ValueError('worker PID unavailable')
        data=Path('/proc/'+raw+'/environ').read_bytes().split(b'\0')
        return {os.fsdecode(k):os.fsdecode(v) for k,v in (x.split(b'=',1) for x in data if x)}

    def check_redis(self):
        code = ("import redis; from task_server.api_testing.tasks import settings; "
                "r=redis.Redis.from_url(settings.redis_url,socket_timeout=3,socket_connect_timeout=3); "
                "q=settings.queue; names=[q,q+'\\x06\\x16'+'3',q+'\\x06\\x16'+'6',q+'\\x06\\x16'+'9']; "
                "print(sum(r.llen(x) for x in names),r.hlen('unacked'),r.zcard('unacked_index'))")
        out = subprocess.run([str(ROOT/'.venv/bin/python'),'-c',code],cwd=str(ROOT),env=self.worker_env(),
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,check=True,timeout=10).stdout.strip()
        if out != '0 0 0': raise ValueError('broker queue or unacknowledged messages present/unknown')

    def backup(self, kind):
        d=Path(tempfile.mkdtemp(prefix='w04-'+kind+'-',dir=str(BACKUP_PARENT))); os.chmod(d,0o700)
        record={}
        for name in GROUPS[kind]:
            target=ROOT/name
            if name==ADDED:
                if target.exists() or target.is_symlink(): raise ValueError('new module already exists')
                record[name]=None
            else:
                if sha(target)!=OLD[name]: raise ValueError('target is not original: '+name)
                p=d/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,p)
                if sha(p)!=OLD[name]: raise ValueError('backup mismatch: '+name)
                record[name]=OLD[name]
        self.backup_paths[kind]=d
        (d/'manifest.json').write_text(json.dumps(record))
        print('BACKUP',str(d),flush=True)

    def stop(self,kind):
        raw=cmd('systemctl','show',SERVICES[kind],'-p','MainPID','--value',timeout=5)
        if not raw.isdigit() or int(raw)<=0: raise ValueError('missing current PID')
        self.before_pids[kind]=int(raw)
        cmd('systemctl','stop',SERVICES[kind],timeout=90)

    def verify_stopped(self,kind):
        out=cmd('systemctl','show',SERVICES[kind],'-p','ActiveState','--value',timeout=5)
        if out!='inactive': raise ValueError('service not stopped: '+out)
        self.check_other_deploys()

    def atomic_write(self,path,data,source_meta):
        st=source_meta.stat(); fd,name=tempfile.mkstemp(prefix='.w04-',dir=str(path.parent))
        try:
            with os.fdopen(fd,'wb') as out:
                out.write(data);out.flush();os.fsync(out.fileno())
                os.fchown(out.fileno(),st.st_uid,st.st_gid);os.fchmod(out.fileno(),st.st_mode & 0o7777)
            os.replace(name,path)
            dfd=os.open(str(path.parent),os.O_RDONLY)
            try:os.fsync(dfd)
            finally:os.close(dfd)
        finally:
            if os.path.exists(name):os.unlink(name)

    def install(self,kind):
        for name in GROUPS[kind]:
            p=ROOT/name
            if name==ADDED:
                if p.exists() or p.is_symlink(): raise ValueError('module appeared during release')
                meta=ROOT/'task_server/api_testing/services/source_service.py'
            else:
                if p.is_symlink() or not p.is_file() or sha(p)!=OLD[name]:raise ValueError('version changed during release: '+name)
                meta=p
            data=(STAGE/name).read_bytes()
            if hashlib.sha256(data).hexdigest()!=NEW[name]:raise ValueError('staged file changed: '+name)
            self.atomic_write(p,data,meta)
            if sha(p)!=NEW[name]:raise ValueError('installed hash mismatch: '+name)

    def start(self,kind):
        cmd('systemctl','start',SERVICES[kind],timeout=90)
        raw=cmd('systemctl','show',SERVICES[kind],'-p','MainPID','--value',timeout=5)
        if not raw.isdigit() or int(raw)<=0:raise ValueError('started PID unavailable')
        self.started_pids[kind]=int(raw)

    def ready(self,kind):
        last=None
        for _ in range(40):
            try:
                cmd('systemctl','is-active','--quiet',SERVICES[kind],timeout=5)
                raw=cmd('systemctl','show',SERVICES[kind],'-p','MainPID','--value',timeout=5)
                if not raw.isdigit() or int(raw)<=0:raise ValueError('service PID missing')
                if kind=='main':
                    data=health()
                    if int(data.get('runtime',{}).get('pid',-1))!=int(raw):raise ValueError('health PID mismatch')
                else:
                    result=subprocess.run([str(ROOT/'.venv/bin/celery'),'-A','task_server.api_testing.tasks:celery_app',
                        'inspect','ping','--timeout=3','--json'],cwd=str(ROOT),env=self.worker_env(),check=True,
                        text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=8)
                    if json.loads(result.stdout)!={'celery@qa.test.sonic-00.txsh':{'ok':'pong'}}:raise ValueError('worker ping mismatch')
                return
            except Exception as exc:last=exc;time.sleep(.5)
        raise ValueError('service not ready: '+repr(last))

    def verify_installed(self,kind):
        raw=cmd('systemctl','show',SERVICES[kind],'-p','MainPID','--value',timeout=5)
        if not raw.isdigit() or int(raw)==self.before_pids.get(kind):raise ValueError('service did not restart')
        for name in GROUPS[kind]:
            if sha(ROOT/name)!=NEW[name]:raise ValueError('new file mismatch: '+name)

    def halt_for_recovery(self,kind):
        self.check_other_deploys()
        state=cmd('systemctl','show',SERVICES[kind],'-p','ActiveState','--value',timeout=5)
        if state=='inactive': return
        raw=cmd('systemctl','show',SERVICES[kind],'-p','MainPID','--value',timeout=5)
        if not raw.isdigit() or int(raw)<=0 or int(raw) not in {self.before_pids.get(kind),self.started_pids.get(kind)}:
            raise CompetingRelease('unknown service PID; competing release possible')
        cmd('systemctl','stop',SERVICES[kind],timeout=90)
        if cmd('systemctl','show',SERVICES[kind],'-p','ActiveState','--value',timeout=5) != 'inactive':
            raise ValueError('recovery stop not confirmed')

    def restore_known(self,kind):
        # Validate every file before touching any, so unknown external versions remain intact.
        d=self.backup_paths[kind]; conflicts=[]; observed={}
        for name in GROUPS[kind]:
            p=ROOT/name
            if p.is_symlink():conflicts.append(name);continue
            if name==ADDED:
                actual=sha(p) if p.exists() else None
                if actual not in {None,NEW[name]}:conflicts.append(name)
            else:
                if not p.is_file():conflicts.append(name);continue
                actual=sha(p)
                if actual not in {OLD[name],NEW[name]}:conflicts.append(name)
                backup=d/name
                if not backup.is_file() or backup.is_symlink() or sha(backup)!=OLD[name]:
                    raise ValueError('invalid backup: '+name)
            observed[name]=actual
        if conflicts:return conflicts
        for name in GROUPS[kind]:
            p=ROOT/name
            if p.is_symlink() or (sha(p) if p.exists() else None)!=observed[name]:
                raise ValueError('file changed during rollback: '+name)
            if name==ADDED:
                if observed[name]==NEW[name]:p.unlink()
            elif observed[name]==NEW[name]:
                self.atomic_write(p,(d/name).read_bytes(),p)
        return []


def main(argv=None):
    parser=argparse.ArgumentParser(description='Targeted release; snapshots cannot prevent new submissions')
    parser.add_argument('--apply',choices=('main','worker')); args=parser.parse_args(argv)
    print('WARNING: no common platform admission or deployment gate; current activity checks are snapshots only.',flush=True)
    with LOCK.open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:print('another targeted release holds lock');return 1
        ops=TargetOps()
        try:
            if args.apply:
                for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(sig,stop_signal)
                apply_kind(ops,args.apply)
                print('RELEASE OK',args.apply,'verify online workflow separately',flush=True)
            else:
                ops.preflight();ops.active_check();print('CHECK OK; not a complete admission gate',flush=True)
            return 0
        except BaseException as error:
            print('RELEASE ERROR:',str(error),flush=True);return 1

if __name__=='__main__':raise SystemExit(main())
