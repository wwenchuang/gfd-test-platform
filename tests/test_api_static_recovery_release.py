import importlib.util
import tempfile
import unittest
import json
import fcntl
import contextlib
import io
from unittest.mock import patch
from pathlib import Path

RELEASE_PATH = Path(__file__).parents[1] / 'deploy' / 'api-static-recovery-release.py'
if not RELEASE_PATH.exists():
    raise AssertionError('API-only release script is missing')
spec = importlib.util.spec_from_file_location('release', Path(__file__).parents[1] / 'deploy' / 'api-static-recovery-release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

class FakeDocker:
    def __init__(self, root):
        self.root = root
    def check_layout(self):
        for rel in ('api-test/assets',):
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
        self.old = {'api-test/index.html': b'old-index'}
        (self.host/'api-test/assets').mkdir(parents=True)
        (self.docker.root/'api-test/assets').mkdir(parents=True)
        self.entries = {'api-test/index.html': b'new-index'}
        self.assets = {'api-test/assets/index-abc.js': b'new-asset', 'api-test/assets/old.js': b'old-asset'}
        self.baseline_assets = {'api-test/assets/old.js': b'old-asset'}
        for path, data in self.old.items():
            host = self.host/path; host.parent.mkdir(parents=True, exist_ok=True); host.write_bytes(data)
            self.docker.write(path, data)
        self.r = release.Release(self.host, self.docker, self.backup, self.old, self.entries, self.assets, self.baseline_assets)
    def test_pinned_payload_is_api_only_and_preserves_every_old_asset(self):
        old, entries, assets, baseline = release.read_payload(Path(__file__).parents[1])
        self.assertEqual(set(old), {'api-test/index.html'})
        self.assertEqual(set(entries), {'api-test/index.html'})
        self.assertEqual(release.digest(old['api-test/index.html']), release.OLD_HASHES['api-test/index.html'])
        self.assertGreater(len(baseline), 0)
        self.assertTrue(all(assets.get(name) == data for name, data in baseline.items()))
        self.assertTrue(all(name.startswith('api-test/assets/') for name in assets))
        self.assertIn(b'index-BNFKrw44.js', entries['api-test/index.html'])

    def test_apply_and_rollback_require_explicit_exclusive_flag(self):
        for args in (['release', '--apply'], ['release', '--rollback', '/fake/backup']):
            with self.subTest(args=args), patch.object(release.os, 'geteuid', return_value=0), patch.object(release.sys, 'argv', args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit): release.main()
        self.assertFalse(self.backup.exists())

    def test_dry_run_does_not_create_a_lock_file(self):
        lock = Path(self.tmp.name) / 'does-not-exist'
        with release.release_lock(lock, modifying=False):
            self.assertFalse(lock.exists())
        self.assertFalse(lock.exists())

    def test_dry_run_checks_but_writes_nothing(self):
        self.r.preflight()
        self.assertFalse(self.backup.exists())
        self.assertFalse((self.host/'api-test/assets/index-abc.js').exists())
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
        (self.host/'api-test/index.html').write_bytes(b'other')
        with self.assertRaises(RuntimeError): self.r.preflight()
        (self.host/'api-test/index.html').write_bytes(self.old['api-test/index.html'])
        self.docker.write('api-test/assets/index-abc.js', b'other')
        with self.assertRaises(RuntimeError): self.r.preflight()
    def test_apply_and_verified_rollback_preserve_old_js_and_assets(self):
        self.r.apply()
        self.assertEqual((self.host/'api-test/index.html').read_bytes(), b'new-index')
        self.assertEqual(self.docker.read('api-test/index.html'), b'new-index')
        self.r.rollback()
        for path, data in self.old.items():
            self.assertEqual((self.host/path).read_bytes(), data)
            self.assertEqual(self.docker.read(path), data)
        self.assertEqual((self.host/'api-test/assets/index-abc.js').read_bytes(), b'new-asset')
    def test_rollback_does_not_overwrite_unknown_changes(self):
        self.r.apply()
        self.docker.write('api-test/index.html', b'changed-by-others')
        with self.assertRaises(RuntimeError): self.r.rollback()
        self.assertEqual(self.docker.read('api-test/index.html'), b'changed-by-others')
        self.assertEqual((self.host/'api-test/index.html').read_bytes(), b'new-index')
    def test_rollback_rejects_missing_or_modified_old_api_assets(self):
        for mutation in ('missing', 'modified'):
            with self.subTest(mutation=mutation):
                self.setUp()
                self.r.apply()
                target = self.docker.root/'api-test/assets/old.js'
                if mutation == 'missing': target.unlink()
                else: target.write_bytes(b'changed')
                with self.assertRaises(RuntimeError): self.r.rollback()
                self.assertEqual(self.docker.read('api-test/index.html'), b'new-index')

    def test_partial_apply_can_be_rolled_back(self):
        original = self.docker.write
        def write(path, data):
            if path == 'api-test/index.html' and data == b'new-index': raise RuntimeError('simulated copy failure')
            original(path, data)
        self.docker.write = write
        with self.assertRaises(RuntimeError): self.r.apply()
        self.docker.write = original
        self.r.rollback()
        self.assertEqual((self.host/'api-test/index.html').read_bytes(), b'old-index')
        self.assertEqual(self.docker.read('api-test/index.html'), b'old-index')

if __name__ == '__main__': unittest.main()
