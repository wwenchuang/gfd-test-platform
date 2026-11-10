"""Verify owned evidence files on every read, without following symlinks."""
import hashlib
import hmac
import os
import re
import stat

_FILES = {'screenshot': ('screenshot.png', 8 * 1024 * 1024),
          'trace': ('trace.zip', 30 * 1024 * 1024)}


def read_evidence(root, run_id, item, expected_hash):
    if not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id) or item not in _FILES:
        raise ValueError('not_found')
    if not isinstance(expected_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_hash):
        raise ValueError('not_found')
    filename, limit = _FILES[item]
    fds = []
    try:
        flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        fds.append(os.open(str(root), flags))
        fds.append(os.open(run_id, flags, dir_fd=fds[-1]))
        fds.append(os.open('evidence', flags, dir_fd=fds[-1]))
        fds.append(os.open(filename, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0), dir_fd=fds[-1]))
        info = os.fstat(fds[-1])
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid()):
            raise ValueError('not_found')
        with os.fdopen(os.dup(fds[-1]), 'rb') as source:
            data = source.read(limit + 1)
        if len(data) > limit or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected_hash):
            raise ValueError('not_found')
        return data
    except (OSError, ValueError):
        raise ValueError('not_found') from None
    finally:
        for fd in reversed(fds):
            os.close(fd)
