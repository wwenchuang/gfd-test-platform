import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release', Path(__file__).with_name('week4-static-evidence-release.py'))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_preflight_checks_source_host_and_container(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.object(release, 'STAGE', root / 'stage'), patch.object(release, 'HOST', root / 'host'), patch.object(release, 'FILES', (('x', 'old', 'new'),)), patch.object(release, 'digest', side_effect=['new', 'old']), patch.object(release, 'container_hash', return_value='old'):
                release.preflight('/www/html')

    def test_preflight_refuses_unknown_container(self):
        with patch.object(release, 'FILES', (('x', 'old', 'new'),)), patch.object(release, 'digest', side_effect=['new', 'old']), patch.object(release, 'container_hash', return_value='unexpected'):
            with self.assertRaisesRegex(RuntimeError, 'container target changed'):
                release.preflight('/www/html')

    def test_host_install_preserves_backup_and_new_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            host, stage, backup = root/'host', root/'stage', root/'backup'
            host.mkdir(); stage.mkdir(); backup.mkdir()
            (host/'x').write_text('old')
            (stage/'x').write_text('new')
            old = release.digest(host/'x')
            new = release.digest(stage/'x')
            with patch.object(release, 'HOST', host), patch.object(release, 'STAGE', stage):
                release.host_install('x', old, new, backup)
            self.assertEqual((host/'x').read_text(), 'new')
            self.assertEqual((backup/'x').read_text(), 'old')

    def test_host_install_refuses_target_changed_after_preflight(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            host, stage, backup = root/'host', root/'stage', root/'backup'
            host.mkdir(); stage.mkdir(); backup.mkdir()
            (host/'x').write_text('other')
            (stage/'x').write_text('new')
            with patch.object(release, 'HOST', host), patch.object(release, 'STAGE', stage):
                with self.assertRaisesRegex(RuntimeError, 'host changed'):
                    release.host_install('x', 'old_hash', release.digest(stage/'x'), backup)
            self.assertEqual((host/'x').read_text(), 'other')

    def test_container_install_uses_busybox_compatible_numeric_metadata(self):
        calls = []
        with patch.object(release, 'docker', side_effect=lambda *args: calls.append(args) or ''):
            release.container_install('/var/www/html', 'js/ui-simple-entry.js', 'old', 'new', 'tag')
        code = calls[1][4]
        self.assertIn('stat -c %a', code)
        self.assertIn('stat -c %u:%g', code)
        self.assertNotIn('--reference', code)


if __name__ == '__main__':
    unittest.main()
