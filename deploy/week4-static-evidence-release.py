#!/usr/bin/env python3
"""Update exactly two workbench files. Inspection is the default mode.

Run on the server after extracting the two files from pinned git commit
9cae9c5e into STAGE. Set WEB_ROOT only after inspecting nginx and actual hashes.
This lock protects only invocations of this script, not unrelated deployers.
"""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

STAGE = Path('/tmp/w04-practice-page')
HOST = Path('/opt/midscene-task-platform')
CONTAINER = 'sonic-server-272-midscene-reports-1'
WEB_ROOTS = ('/usr/share/nginx/html', '/var/www/html', '/www/html')
FILES = (
    ('js/ui-simple-entry.js', '61020d9cf0444fec47b2ccb7b2f0a91670f75c7d834067d9fdba9024a270b40b', '3d23e09000b134dd2ed1debd5cd5a06a275fc6cfedc68d868cdd7e9bbe771824'),
    ('task-manager.html', 'cde44b35fcbede6bb2bf4f1f041f03c8af649fd2f96f1af6c8f937fc1cea57a6', '3ef0d7f22b21733333a32c5341549f5d172ea4af4fceea96a3688e9f1fe33566'),
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, timeout=15).strip()


def container_hash(path):
    return docker('exec', CONTAINER, 'sha256sum', path).split()[0]


def preflight(root):
    for rel, old, new in FILES:
        if digest(STAGE / rel) != new:
            raise RuntimeError('staged hash mismatch: ' + rel)
        if digest(HOST / rel) != old:
            raise RuntimeError('host target changed: ' + rel)
        if container_hash(root + '/' + rel) != old:
            raise RuntimeError('container target changed: ' + rel)
    print('PRECHECK OK: snapshots only; coordinate concurrent deployment', flush=True)


def host_install(rel, old, new, backup):
    target = HOST / rel
    backup_file = backup / rel
    backup_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup_file)
    if digest(backup_file) != old or digest(target) != old:
        raise RuntimeError('host changed before install: ' + rel)
    info = target.stat()
    fd, tmp = tempfile.mkstemp(prefix='.w04-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write((STAGE / rel).read_bytes())
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, info.st_mode & 0o7777)
        os.chown(tmp, info.st_uid, info.st_gid)
        if digest(Path(tmp)) != new or digest(target) != old:
            raise RuntimeError('host changed during staging: ' + rel)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def container_install(root, rel, old, new, backup_tag):
    target = root + '/' + rel
    temp = target + '.w04-' + backup_tag + '.tmp'
    backup = target + '.w04-' + backup_tag + '.bak'
    docker('cp', str(STAGE / rel), CONTAINER + ':' + temp)
    # Check and rename in one shell; an unrelated deployer still cannot be locked out.
    code = '''set -eu
target="$1"; temp="$2"; backup="$3"; old="$4"; new="$5"
[ "$(sha256sum "$target" | cut -d' ' -f1)" = "$old" ]
[ "$(sha256sum "$temp" | cut -d' ' -f1)" = "$new" ]
cp -p "$target" "$backup"
[ "$(sha256sum "$backup" | cut -d' ' -f1)" = "$old" ]
mode="$(stat -c %a "$target")"
owner="$(stat -c %u:%g "$target")"
chmod "$mode" "$temp"
chown "$owner" "$temp"
[ "$(sha256sum "$target" | cut -d' ' -f1)" = "$old" ]
mv -f "$temp" "$target"
'''
    docker('exec', CONTAINER, 'sh', '-c', code, 'sh', target, temp, backup, old, new)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--web-root', required=True, choices=WEB_ROOTS)
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    with open('/tmp/week4-static-release.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        preflight(args.web_root)
        if not args.apply: return
        backup = Path(tempfile.mkdtemp(prefix='w04-web-', dir='/root/ai-test-practice-20261105'))
        tag = backup.name
        print('BACKUP', backup, flush=True)
        # JS first: the new JS is backwards compatible; HTML version is activated last.
        for rel, old, new in FILES:
            host_install(rel, old, new, backup)
            container_install(args.web_root, rel, old, new, tag)
            if digest(HOST / rel) != new or container_hash(args.web_root + '/' + rel) != new:
                raise RuntimeError('post-install hash mismatch: ' + rel)
            print('INSTALLED', rel, flush=True)
        print('INSTALL OK; verify HTTP and real user interface separately', flush=True)


if __name__ == '__main__':
    main()
