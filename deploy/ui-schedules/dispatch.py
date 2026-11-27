"""Systemd entry point. Claim one immutable selection per platform and local date."""
import argparse
from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

from task_server.services import ui_schedule_clock as clock
from task_server.services.ui_schedule_clock import write_atomic
from task_server.services.ui_schedule_service import ScheduleError, latest_selection

DEPLOY = Path(__file__).resolve().parents[1]
WORKERS = {'android': (DEPLOY / '3d-daily/run.py', '--run'),
           'ios': (DEPLOY / '3d-ios-nightly/nightly.py', 'run')}


def due_snapshot(platform, config, now):
    """Existing claims resume their own revision, even after the schedule was edited."""
    for date in (now.date() - timedelta(days=1), now.date()):
        claim = clock.claim_path(platform, date)
        if claim.with_suffix('.done').exists():
            continue
        if claim.exists():
            saved = json.loads(claim.read_text())
            if now < clock.scheduled_time(date, saved['time']) + timedelta(hours=19 if platform == 'android' else 6):
                return claim, saved
            continue
        slot = clock.scheduled_time(date, config['time'])
        # The original Android timer skipped missed starts. iOS allowed catch-up for six hours.
        window = timedelta(minutes=2) if platform == 'android' else timedelta(hours=6)
        if slot <= now < slot + window and slot >= datetime.fromisoformat(config['effective_at']) and not clock.already_started(platform, date):
            return claim, dict(config, scheduled_date=date.isoformat())
    return None, None


def read_config(platform):
    config = json.loads((clock.CONFIG_DIR / f'{platform}.json').read_text())
    if config['platform'] != platform or config['target']['platform'] != platform or not config['cases']:
        raise ValueError('invalid installed UI schedule')
    return config


def claim_due(platform, now):
    path, snapshot = due_snapshot(platform, read_config(platform), now)
    if path and not path.exists():
        try:
            snapshot = latest_selection(snapshot)
        except ScheduleError as error:
            # Keep the scheduled attempt visible to the worker and notification channel.
            snapshot = dict(snapshot, selection_error=error.message)
        write_atomic(path, snapshot)
    return path, snapshot


def claim_manual(platform, now):
    directory = clock.manual_dir(platform)
    # A restart resumes the same snapshot and stage idempotency keys.
    for path in sorted(directory.glob('*.running')):
        return path
    for path in sorted(directory.glob('*.queued')):
        config = json.loads(path.read_text())
        config.update(started_at=now.isoformat(), scheduled_date=now.date().isoformat())
        write_atomic(path, config)
        return path.rename(path.with_suffix('.running'))
    return None


def execute_manual(platform, path, command, env):
    process = subprocess.Popen(command, env=env)
    while True:
        try:
            code = process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            code = None
        # The oneshot timer cannot tick while this worker runs. Freeze any
        # daily selection that becomes due, even if its start window is short.
        with clock.config_lock(platform):
            claim_due(platform, datetime.now(clock.LOCAL))
            if code is not None:
                record = clock.LEGACY_STATE[platform] / (path.stem + '.json')
                completed = code == 0 and record.is_file() and json.loads(record.read_text()).get('final')
                path.rename(path.with_suffix('.finished' if completed else '.failed'))
                return 0 if completed else (code or 1)


def run(platform):
    if not (clock.CONFIG_DIR / 'enabled').is_file():
        return 0
    # This lock spans execution; the editor lock only spans reading and claiming.
    with (clock.CONFIG_DIR / (platform + '.worker.lock')).open('a') as worker_lock:
        try:
            fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        with clock.config_lock(platform):
            now = datetime.now(clock.LOCAL)
            path, snapshot = claim_due(platform, now)
            if path is None:
                path = claim_manual(platform, now)
                if path is None:
                    return 0
        worker, mode = WORKERS[platform]
        command = [sys.executable, str(worker), mode]
        env = dict(os.environ, MIDSCENE_UI_SCHEDULE_SNAPSHOT=str(path))
        if path.suffix == '.running':
            return execute_manual(platform, path, command, env)
        result = subprocess.run(command, check=False, env=env)
        write_atomic(path.with_suffix('.done'), {'version': snapshot['version'], 'returncode': result.returncode,
                                                'finished_at': datetime.now(clock.LOCAL).isoformat()})
        return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platform', choices=WORKERS)
    parser.add_argument('--inspect', action='store_true', help='Preview the next selection; never claim or run it')
    args = parser.parse_args()
    if not args.inspect:
        return run(args.platform)
    with clock.config_lock(args.platform):
        config = read_config(args.platform)
        next_at = clock.next_run_at(args.platform, config['time'])
        _, snapshot = due_snapshot(args.platform, config, datetime.fromisoformat(next_at))
        print(json.dumps({'next_run_at': next_at, 'config': snapshot}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
