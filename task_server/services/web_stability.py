"""Operator-run, first-attempt stability measurements of the deployed web catalog."""
import csv
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import time
import urllib.request
import uuid

from task_server import auth, identity
from task_server.services import week1_smoke, week5_web_service as web

CASES = ('ui_catalog_count', 'ui_catalog_filter', 'ui_catalog_empty', 'ui_case_selection',
         'ui_case_revision', 'ui_start_handoff', 'ui_catalog_paging', 'ui_catalog_module',
         'ui_case_hidden_selection', 'ui_start_empty_guard')
ROUNDS = 20
FIELDS = ('round', 'scenario', 'status', 'reason', 'duration_ms', 'run_id')
ROOT = Path(__file__).resolve().parents[2]


def folder(owner):
    key = hashlib.sha256(owner.encode()).hexdigest()
    return identity.default_db_path().parent / 'web-stability' / key


def latest(owner, username, token):
    profile = web._profile(owner, username, token)
    if not profile:
        return None
    try:
        data = json.loads((folder(owner) / 'latest.json').read_text())
    except FileNotFoundError:
        return None
    if data['scope'] != web._scope(profile):
        return None
    return data['report']


def build_report(rows, started, run, fingerprint):
    by_case = {case: [r['duration_ms'] for r in rows if r['scenario'] == case and r['status'] != 'blocked'] for case in CASES}
    count = len(rows)
    passed = sum(r['status'] == 'passed' for r in rows)
    blocked = sum(r['status'] == 'blocked' for r in rows)
    return dict(id=run, source_sha256=fingerprint, started=started, updated=time.time(),
                planned=len(CASES)*ROUNDS, attempted=count, passed=passed, failed=count-passed-blocked,
                blocked=blocked, unrun=len(CASES)*ROUNDS-count, rows=rows,
                median_ms={k: round(statistics.median(v)) for k,v in by_case.items() if v})


def save(path, value):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(value, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def source_hash():
    files = [ROOT/'task-manager.html', Path(web.__file__), Path(__file__), *sorted(web._WORKER.parent.glob('*.js'))]
    return hashlib.sha256(b''.join(hashlib.sha256(p.read_bytes()).digest() for p in files)).hexdigest()


def measure(username):
    # This CLI runs on the platform host under its service identity.
    password = getpass.getpass('Platform password: ')
    url = 'http://127.0.0.1:8088/api/auth/login'
    req = urllib.request.Request(url, json.dumps(dict(username=username,password=password)).encode(),
                                 {'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=10) as response:
        token = json.load(response)['token']
    del password
    actor = auth.verify_session_token(token)
    if not actor or actor['user'] != username:
        raise RuntimeError('platform session unavailable')
    owner = actor['user_id']
    profile = web._profile(owner, username, token)
    if not profile:
        raise RuntimeError('ui.view required')
    scope, directory = web._scope(profile), folder(owner)
    os.umask(0o077)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / 'measurement.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run = uuid.uuid4().hex
        trial = directory / run
        trial.mkdir(mode=0o700)
        rows, signatures = [], {}
        fingerprint, started = source_hash(), time.time()
        shared_lease = identity.default_db_path().with_name('week1-smoke.sqlite3')
        for number in range(1, ROUNDS+1):
            batch = trial / str(number)
            batch.mkdir(mode=0o700)
            for case in CASES:
                if source_hash() != fingerprint:
                    raise RuntimeError('source changed during measurement')
                if shutil.disk_usage(trial).free < 2 * 1024**3:
                    raise RuntimeError('insufficient evidence storage')
                current = web._profile(owner, username, token)
                if not current or web._scope(current) != scope:
                    raise RuntimeError('authorization changed')
                begin = time.monotonic()
                try:
                    result = web.run(owner, username, token, scenario=case, db_path=batch/'results.sqlite3',
                                     root=batch/'evidence', lease_path=shared_lease)
                except week1_smoke.SmokeBusy:
                    result = dict(status='blocked',error_kind='worker_busy')
                elapsed = round(1000*(time.monotonic()-begin))
                status = result['status']
                reason = result.get('detail') or result.get('error_kind') or ''
                if status == 'passed':
                    signature = {k:result.get(k) for k in ('asset','total','pages_checked')}
                    if signatures.setdefault(case, signature) != signature:
                        status, reason = 'blocked', 'catalog_changed'
                rows.append(dict(round=number, scenario=case, status=status, reason=reason,
                                 duration_ms=elapsed, run_id=result.get('id','')))
                report = build_report(rows, started, run, fingerprint)
                save(trial/'report.json', report)
                save(directory/'latest.json', dict(scope=scope, report=report))
                print(number, case, status, reason, elapsed, flush=True)
                time.sleep(3.1)
        with (trial/'stability.csv').open('w',newline='') as output:
            writer=csv.DictWriter(output, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return report['passed'] / report['planned'] >= .95


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--username', required=True)
    raise SystemExit(0 if measure(parser.parse_args().username) else 1)
