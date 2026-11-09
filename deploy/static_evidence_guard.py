#!/usr/bin/env python3
"""Reject known historical evidence in static files without logging its contents.

This is a bounded signature check, not a general data-leak detector. Callers must
coordinate writers: scanning and subsequent publication are separate operations.
"""
import gzip
import hashlib
import os
import re
import stat
import sys
import zlib

# SHA-256 of the lowercased known historical identifiers and path, not the
# underlying values. Do not reintroduce those identifiers into public assets.
SIGNATURE_DIGESTS = {bytes.fromhex(value) for value in (
    '338b797cbe091f978d3f6fb8b9cbe8be480a9892da8e86627e1917248ee481ef',
    '9732335ec228de4edf60824804fda5c414423034ce2d3d9d36daef41df03b176',
    '89fffb34adbeedc19e19856e42cadc5e498c9bee8480ed630e246939358035af',
    '26e24ccf3a3bcd4e4d936711c969c0358d440ef1f800d0f37d432a70e018e3e7',
    '384d2ee3f429bbd2b1d9580f008bc473093dcfdc1a6da7e3a290989af3bbcee5',
    '34b6f01726e5b22702722604d8038d403c2d342018c1967fc57b95923f41ea6c',
    '963208870b0d028d7fbf3782101f845b3701d4bb9affe0e52206cb001750af61',
)}
UUID = re.compile(rb'(?=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}))')
MAX_FILE = 32 * 1024 * 1024
MAX_DECODED = 64 * 1024 * 1024
MAX_ENTRIES = 20000


def contains_signature(data):
    data = data.lower()
    if any(hashlib.sha256(match.group(1)).digest() in SIGNATURE_DIGESTS for match in UUID.finditer(data)):
        return True
    # The known non-UUID signature is an ASCII path of length 39 beginning /.
    return any(hashlib.sha256(data[i:i + 39]).digest() in SIGNATURE_DIGESTS
               for i, char in enumerate(data) if char == 47 and len(data) - i >= 39)


def check_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('not regular')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise ValueError('file limit')
        if path.lower().endswith('.br'):
            raise ValueError('unsupported compression')
        if path.lower().endswith('.gz') or data.startswith(b'\x1f\x8b'):
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as source:
                data = source.read(MAX_DECODED + 1)
            if len(data) > MAX_DECODED or data.startswith(b'\x1f\x8b'):
                raise ValueError('decoded limit or nested compression')
        if contains_signature(data):
            raise ValueError('known evidence')
    finally:
        if fd is not None:
            os.close(fd)


def scan(paths):
    seen = 0
    for root in paths:
        if os.path.islink(root):
            raise ValueError('symlink')
        if not os.path.exists(root):
            raise ValueError('missing')
        todo = [root]
        while todo:
            path = todo.pop()
            if contains_signature(os.fsencode(path)):
                raise ValueError('known evidence in filename')
            seen += 1
            if seen > MAX_ENTRIES:
                raise ValueError('entry limit')
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                raise ValueError('symlink')
            if stat.S_ISDIR(st.st_mode):
                with os.scandir(path) as entries:
                    todo.extend(entry.path for entry in entries)
            elif stat.S_ISREG(st.st_mode):
                check_file(path)
            else:
                raise ValueError('unsupported file')


def main(paths=None):
    paths = sys.argv[1:] if paths is None else paths
    try:
        if not paths:
            raise ValueError('missing sources')
        scan(paths)
    except (OSError, ValueError, EOFError, zlib.error):
        print('静态资源检查未通过：存在已知历史证据或无法完整检查，发布已停止。', file=sys.stderr)
        return 1
    print('静态资源已通过已知历史证据检查（不代表不存在其他敏感信息）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
