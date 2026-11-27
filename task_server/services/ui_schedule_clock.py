"""Shared calendar for the two installed, once-per-local-day UI regressions."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOCAL = timezone(timedelta(hours=8))
CONFIG_DIR = Path('/var/lib/midscene-ui-schedules')
LEGACY_STATE = {'android': Path('/var/lib/midscene-3d-daily'),
                'ios': Path('/var/lib/midscene-3d-ios-nightly')}


@contextmanager
def config_lock(platform):
    if platform not in LEGACY_STATE:
        raise ValueError('unknown schedule')
    with (CONFIG_DIR / (platform + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def scheduled_time(date, time_string):
    hour, minute = map(int, time_string.split(':'))
    return datetime.combine(date, datetime.min.time(), LOCAL).replace(hour=hour, minute=minute)


def claim_path(platform, date):
    return CONFIG_DIR / 'claims' / f'{platform}-{date}.json'


def already_started(platform, date):
    return claim_path(platform, date).is_file() or (LEGACY_STATE[platform] / f'{date}.json').is_file()


def next_run_at(platform, time_string, now=None):
    local = (now or datetime.now(LOCAL)).astimezone(LOCAL)
    slot = scheduled_time(local.date(), time_string)
    if local >= slot or already_started(platform, slot.date()):
        slot += timedelta(days=1)
    return slot.isoformat()


def write_atomic(path, obj):
    """Publish a complete config or claim before releasing its lock."""
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False, encoding='utf-8') as file:
        try:
            json.dump(obj, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
            os.replace(file.name, path)
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            Path(file.name).unlink(missing_ok=True)


def manual_dir(platform):
    return CONFIG_DIR / 'manual' / platform
