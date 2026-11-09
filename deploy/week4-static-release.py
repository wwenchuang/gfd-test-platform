#!/usr/bin/env python3
"""Conservative, static-only deployment of the pinned Week 4 UI changes.

No restarts or backend changes. Coordinate an exclusive deployment window:
this program's local lock cannot block another publisher. Default is read-only.
"""
import argparse
from contextlib import contextmanager
import fcntl
import stat
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

OLD_COMMIT = 'f614f84a75c2f815cb36059d8d38ae5d0df0628e'
NEW_COMMIT = '667e610c8bbfea0c2a19bf757552b35f674a9f23'
HOST_ROOT = Path('/opt/midscene-task-platform')
BACKUP_ROOT = Path('/root/ai-test-practice-20261105')
CONTAINER = 'sonic-server-272-midscene-reports-1'
WEB_ROOT = '/var/www/html'
OLD_HASHES = {
    'js/ui-simple-entry.js': '67b4c5b0ba8c85b4ea62afbbd60fc6c87c1f360afcf0acae7d646d972685f435',
    'task-manager.html': '770395d6733752567da094428efb7d5948835480b3474cadc9b2e14f3b0b6e7f',
    'api-test/index.html': '08303aeb4f2ac5ce90c9891725f6122f162ab8619f60ec5d118d48908d1e8d4b',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


@contextmanager
def exclusive_lock(path):
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise RuntimeError('invalid lock file owner or type')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def checked_path(root, rel):
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', rel) or '..' in rel.split('/'):
        raise RuntimeError('invalid relative path: ' + rel)
    root = Path(root)
    for p in (root, *[root.joinpath(*rel.split('/')[:i]) for i in range(1, len(rel.split('/')) + 1)]):
        if p.is_symlink(): raise RuntimeError('symlink refused: ' + str(p))
    return root / rel


def host_read(root, rel):
    p = checked_path(root, rel)
    if p.exists() and not p.is_file(): raise RuntimeError('not a regular file: ' + str(p))
    return p.read_bytes() if p.exists() else None


def host_write(root, rel, data):
    p = checked_path(root, rel)
    if not p.parent.is_dir(): raise RuntimeError('missing directory: ' + str(p.parent))
    info = p.stat() if p.exists() else p.parent.stat()
    fd, name = tempfile.mkstemp(prefix='.week4-', dir=str(p.parent))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.chmod(name, (info.st_mode & 0o777) if p.exists() else 0o644)
        os.chown(name, info.st_uid, info.st_gid)
        os.replace(name, p)
    finally:
        if os.path.exists(name): os.unlink(name)


class Docker:
    def __init__(self):
        self.container = CONTAINER

    def _run(self, args, check=True):
        return subprocess.run(['docker'] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)

    def check_mounts(self):
        info = json.loads(self._run(['inspect', CONTAINER]).stdout)[0]
        if not info.get('State', {}).get('Running'): raise RuntimeError('web container is not running')
        container_id = info.get('Id', '')
        if not re.fullmatch(r'[0-9a-f]{64}', container_id): raise RuntimeError('invalid container ID')
        self.container = container_id
        for mount in info.get('Mounts', []):
            dest = mount.get('Destination', '').rstrip('/') or '/'
            if dest == '/' or dest == WEB_ROOT or WEB_ROOT.startswith(dest + '/') or dest.startswith(WEB_ROOT + '/'):
                raise RuntimeError('web root has a bind/volume mount; inspect and deploy separately: ' + dest)

    def check_layout(self):
        for path in (WEB_ROOT, WEB_ROOT + '/js', WEB_ROOT + '/api-test', WEB_ROOT + '/api-test/assets'):
            command = """set -eu
p="$1"; cur=''; oldifs="$IFS"; IFS='/'; set -- $p; IFS="$oldifs"
for part do [ -n "$part" ] || continue; cur="$cur/$part"; [ ! -L "$cur" ] || exit 4; done
[ -d "$p" ]"""
            result = self._run(['exec', self.container, 'sh', '-c', command, 'sh', path], check=False)
            if result.returncode: raise RuntimeError('container directory missing or symlink: '+path)

    def _path(self, rel):
        if not re.fullmatch(r'[A-Za-z0-9_./-]+', rel) or '..' in rel.split('/'):
            raise RuntimeError('invalid container path')
        return WEB_ROOT + '/' + rel

    def read(self, rel):
        path = self._path(rel)
        # Refuse symlink in every path component, including the web root itself.
        command = '''set -eu
p="$1"; cur=''; oldifs="$IFS"; IFS='/'; set -- $p; IFS="$oldifs"
for part do [ -n "$part" ] || continue; cur="$cur/$part"; [ ! -L "$cur" ] || exit 4; done
[ -e "$p" ] || exit 3
[ -f "$p" ] || exit 4
cat "$p"'''
        result = self._run(['exec', self.container, 'sh', '-c', command, 'sh', path], check=False)
        if result.returncode == 3: return None
        if result.returncode: raise RuntimeError('container path read failed: ' + path)
        return result.stdout

    def write(self, rel, data):
        path = self._path(rel)
        temp = path + '.week4-' + uuid.uuid4().hex
        fd, local = tempfile.mkstemp(prefix='week4-static-')
        try:
            with os.fdopen(fd, 'wb') as f: f.write(data)
            self._run(['cp', local, self.container + ':' + temp])
            self._run(['exec', self.container, 'sh', '-c',
                       'set -eu; test ! -L "$1"; chmod 644 "$2"; mv "$2" "$1"', 'sh', path, temp])
        finally:
            os.unlink(local)
            self._run(['exec', self.container, 'rm', '-f', temp], check=False)


def read_payload(repo):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args])
    for commit in (OLD_COMMIT, NEW_COMMIT):
        if git('rev-parse', commit + '^{commit}').decode().strip() != commit:
            raise RuntimeError('missing pinned commit: ' + commit)
    old = {path: git('show', OLD_COMMIT + ':' + path) for path in OLD_HASHES}
    if any(digest(old[path]) != expected for path, expected in OLD_HASHES.items()):
        raise RuntimeError('old commit integrity mismatch')
    js = git('show', NEW_COMMIT + ':js/ui-simple-entry.js')
    html = git('show', NEW_COMMIT + ':task-manager.html')
    expected_ref = b'js/ui-simple-entry.js?v=20261109-practice-evidence-v3'
    js_name = 'js/ui-simple-entry.' + digest(js)[:16] + '.js'
    if html.count(expected_ref) != 1: raise RuntimeError('unexpected HTML script reference')
    html = html.replace(expected_ref, js_name.encode() + b'?v=20261109-practice-evidence-v3')
    entries = {'task-manager.html': html, 'api-test/index.html': git('show', NEW_COMMIT + ':api-test/index.html')}
    names = git('ls-tree', '-r', '--name-only', NEW_COMMIT, '--', 'api-test/assets').decode().splitlines()
    if not names: raise RuntimeError('no API frontend assets')
    assets = {name: git('show', NEW_COMMIT + ':' + name) for name in names}
    assets[js_name] = js
    return old, entries, assets


class Release:
    def __init__(self, host, docker, backup, old, entries, assets):
        self.host, self.docker, self.backup = Path(host), docker, Path(backup)
        self.old, self.entries, self.assets = old, entries, assets

    def preflight(self):
        for rel in ('js', 'api-test', 'api-test/assets'):
            if not checked_path(self.host, rel).is_dir(): raise RuntimeError('missing host directory: ' + rel)
        self.docker.check_layout()
        for rel, old in self.old.items():
            if host_read(self.host, rel) != old or self.docker.read(rel) != old:
                raise RuntimeError('existing file differs from expected baseline: ' + rel)
        for rel, data in self.assets.items():
            for value in (host_read(self.host, rel), self.docker.read(rel)):
                if value is not None and value != data: raise RuntimeError('asset collision: ' + rel)
        print('PRECHECK OK (snapshot only; no concurrent publishers permitted)')

    def _backup(self):
        self.backup.mkdir(mode=0o700, parents=False, exist_ok=False)
        for side, getter in (('host', lambda p: host_read(self.host, p)), ('container', self.docker.read)):
            for rel in self.old:
                target = checked_path(self.backup / side, rel)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                data = getter(rel)
                if data != self.old[rel]: raise RuntimeError('baseline changed during backup: ' + rel)
                target.write_bytes(data); os.chmod(target, 0o600)
        manifest = {'version': 1, 'old_commit': OLD_COMMIT, 'new_commit': NEW_COMMIT,
                    'old': {p: digest(v) for p,v in self.old.items()}, 'new': {p: digest(v) for p,v in self.entries.items()}}
        (self.backup/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        os.chmod(self.backup/'manifest.json', 0o600)
        print('BACKUP:', self.backup, flush=True)

    def apply(self):
        self.preflight(); self._backup()
        for rel, data in self.assets.items():
            for read, write in ((lambda p: host_read(self.host,p), lambda p,d: host_write(self.host,p,d)), (self.docker.read, self.docker.write)):
                existing = read(rel)
                if existing is None: write(rel, data)
                elif existing != data: raise RuntimeError('asset changed during deployment: ' + rel)
                if read(rel) != data: raise RuntimeError('asset verification failed: ' + rel)
        for rel, data in self.entries.items():
            for read, write in ((lambda p: host_read(self.host,p), lambda p,d: host_write(self.host,p,d)), (self.docker.read, self.docker.write)):
                if read(rel) != self.old[rel]: raise RuntimeError('entry changed during deployment: ' + rel)
                write(rel, data)
                if read(rel) != data: raise RuntimeError('entry verification failed: ' + rel)
            print('INSTALLED:', rel, digest(data), flush=True)
        print('STATIC FILES UPDATED; verify HTTP and the real webpage. No backend was updated.')

    def rollback(self):
        manifest = json.loads((self.backup/'manifest.json').read_text())
        expected = {'version': 1, 'old_commit': OLD_COMMIT, 'new_commit': NEW_COMMIT,
                    'old': {p: digest(v) for p,v in self.old.items()}, 'new': {p: digest(v) for p,v in self.entries.items()}}
        if manifest != expected: raise RuntimeError('backup manifest mismatch')
        for side, getter in (('host', lambda p: host_read(self.host,p)), ('container', self.docker.read)):
            for rel in self.old:
                if host_read(self.backup/side, rel) != self.old[rel]: raise RuntimeError('backup hash mismatch: '+side+'/'+rel)
            for rel in self.old.keys() - self.entries.keys():
                if getter(rel) != self.old[rel]: raise RuntimeError('rollback refused; old dependency changed: '+side+'/'+rel)
            for rel, new in self.entries.items():
                if getter(rel) not in (self.old[rel], new): raise RuntimeError('rollback refused; unknown changes: '+side+'/'+rel)
        for rel, new in reversed(tuple(self.entries.items())):
            for read, write in ((lambda p: host_read(self.host,p), lambda p,d: host_write(self.host,p,d)), (self.docker.read, self.docker.write)):
                current = read(rel)
                if current == new: write(rel, self.old[rel])
                elif current != self.old[rel]: raise RuntimeError('rollback refused; file changed: '+rel)
                if read(rel) != self.old[rel]: raise RuntimeError('rollback verification failed: '+rel)
        print('ROLLBACK OK; added hashed assets were kept')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true')
    group.add_argument('--rollback', metavar='BACKUP_DIR')
    parser.add_argument('--confirm-exclusive', action='store_true', help='确认已协调停止并行发布；本脚本无法阻止其他发布者')
    parser.add_argument('--repo', default='/opt/midscene-task-platform-src', help='包含固定 commit 对象的 Git 仓库')
    args = parser.parse_args()
    if os.geteuid() != 0: parser.error('请以 root 运行预检和部署')
    if (args.apply or args.rollback) and not args.confirm_exclusive:
        parser.error('应用或回滚需要 --confirm-exclusive；先协调所有发布者')
    if BACKUP_ROOT.is_symlink() or not BACKUP_ROOT.is_dir(): raise RuntimeError('backup directory unavailable')
    backup_info = BACKUP_ROOT.stat()
    if backup_info.st_uid != os.geteuid() or (backup_info.st_mode & 0o022): raise RuntimeError('backup directory must be owned by root and not group/world writable')
    if args.rollback:
        rb = Path(args.rollback)
        if rb.parent != BACKUP_ROOT or not re.fullmatch(r'week4-static-[0-9a-f]{32}', rb.name):
            raise RuntimeError('invalid backup directory')
    with exclusive_lock(BACKUP_ROOT / '.week4-static-release.lock'):
        docker = Docker(); docker.check_mounts()
        old, entries, assets = read_payload(args.repo)
        backup = Path(args.rollback) if args.rollback else BACKUP_ROOT / ('week4-static-' + uuid.uuid4().hex)
        rel = Release(HOST_ROOT, docker, backup, old, entries, assets)
        print('TARGET', NEW_COMMIT, 'generated HTML SHA256', digest(entries['task-manager.html']))
        print('Old JS is retained; no backend, service restart or old asset deletion.')
        if args.rollback: rel.rollback()
        elif args.apply:
            try: rel.apply()
            except Exception:
                print('Deployment interrupted. Do not retry blindly. If BACKUP/manifest.json exists, inspect files then use --rollback', backup, '--confirm-exclusive', file=sys.stderr)
                raise
        else: rel.preflight()

if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print('STOP:', exc, file=sys.stderr); sys.exit(1)
