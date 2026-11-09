import importlib.util
import tempfile
import unittest
import json
import fcntl
from unittest.mock import patch
from pathlib import Path

spec = importlib.util.spec_from_file_location('release', Path(__file__).parents[1] / 'deploy' / 'week4-static-release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

class FakeDocker:
    def __init__(self, root):
        self.root = root
    def check_layout(self):
        for rel in ('js', 'api-test/assets'):
            if not (self.root/rel).is_dir(): raise RuntimeError('missing container directory')
    def read(self, path):
        p = self.root / path
        if p.is_symlink(): raise RuntimeError('container symlink')
        return p.read_bytes() if p.exists() else None
    def write(self, path, data):
        p = self.root / path
        if p.is_symlink(): raise RuntimeError('container symlink')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); self.host = root/'host'; self.docker = FakeDocker(root/'container'); self.backup = root/'backup'
        self.old = {'js/ui-simple-entry.js': b'old-js', 'task-manager.html': b'old-html', 'api-test/index.html': b'old-index'}
        (self.host/'api-test/assets').mkdir(parents=True)
        (self.docker.root/'api-test/assets').mkdir(parents=True)
        self.entries = {'task-manager.html': b'new-html', 'api-test/index.html': b'new-index'}
        self.assets = {'js/ui-simple-entry.12345678.js': b'new-js', 'api-test/assets/index-abc.js': b'new-asset'}
        for path, data in self.old.items():
            host = self.host/path; host.parent.mkdir(parents=True, exist_ok=True); host.write_bytes(data)
            self.docker.write(path, data)
        self.r = release.Release(self.host, self.docker, self.backup, self.old, self.entries, self.assets)
    def test_dry_run_checks_but_writes_nothing(self):
        self.r.preflight()
        self.assertFalse(self.backup.exists())
        self.assertFalse((self.host/'js/ui-simple-entry.12345678.js').exists())
    def test_missing_layout_is_rejected_before_changes(self):
        (self.host/'api-test/assets').rmdir()
        with self.assertRaises(RuntimeError): self.r.preflight()
        self.assertFalse(self.backup.exists())

    def test_process_lock_rejects_another_publisher_using_same_script(self):
        lock_path = Path(self.tmp.name) / 'lock'
        with release.exclusive_lock(lock_path):
            with self.assertRaises(BlockingIOError):
                with release.exclusive_lock(lock_path): pass

    def test_docker_mount_on_web_root_is_rejected(self):
        class Result:
            stdout = json.dumps([{'Id': 'a'*64, 'State': {'Running': True}, 'Mounts': [{'Destination': '/var/www/html'}]}]).encode()
        with patch.object(release.Docker, '_run', return_value=Result()):
            with self.assertRaises(RuntimeError): release.Docker().check_mounts()

    def test_rejects_mismatched_entry_and_asset_collision(self):
        (self.host/'task-manager.html').write_bytes(b'other')
        with self.assertRaises(RuntimeError): self.r.preflight()
        (self.host/'task-manager.html').write_bytes(self.old['task-manager.html'])
        self.docker.write('api-test/assets/index-abc.js', b'other')
        with self.assertRaises(RuntimeError): self.r.preflight()
    def test_apply_and_verified_rollback_preserve_old_js_and_assets(self):
        self.r.apply()
        self.assertEqual((self.host/'task-manager.html').read_bytes(), b'new-html')
        self.assertEqual(self.docker.read('api-test/index.html'), b'new-index')
        self.r.rollback()
        for path, data in self.old.items():
            self.assertEqual((self.host/path).read_bytes(), data)
            self.assertEqual(self.docker.read(path), data)
        self.assertEqual((self.host/'api-test/assets/index-abc.js').read_bytes(), b'new-asset')
    def test_rollback_does_not_overwrite_unknown_changes(self):
        self.r.apply()
        self.docker.write('task-manager.html', b'changed-by-others')
        with self.assertRaises(RuntimeError): self.r.rollback()
        self.assertEqual(self.docker.read('task-manager.html'), b'changed-by-others')
        self.assertEqual((self.host/'task-manager.html').read_bytes(), b'new-html')
    def test_partial_apply_can_be_rolled_back(self):
        original = self.docker.write
        def write(path, data):
            if path == 'task-manager.html' and data == b'new-html': raise RuntimeError('simulated copy failure')
            original(path, data)
        self.docker.write = write
        with self.assertRaises(RuntimeError): self.r.apply()
        self.docker.write = original
        self.r.rollback()
        self.assertEqual((self.host/'task-manager.html').read_bytes(), b'old-html')
        self.assertEqual(self.docker.read('api-test/index.html'), b'old-index')

if __name__ == '__main__': unittest.main()
