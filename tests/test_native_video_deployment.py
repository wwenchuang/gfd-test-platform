"""Exercise deployment copies in temporary trees, without installing services."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = 'native-video-upload.js'


class NativeVideoDeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='native-video-deployment-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.source = self.work / 'source'
        gateway = self.source / 'ai-gateway'
        gateway.mkdir(parents=True)
        # Only public source files enter the fixture; no workspace configs/secrets.
        for name in ('server.js', 'gateway-auth.js', HELPER, 'package.json'):
            shutil.copyfile(ROOT / 'ai-gateway' / name, gateway / name)
        for name in ('config', 'prompts', 'validators', 'agent'):
            (gateway / name).mkdir()
        (gateway / '.env.example').write_text('# fixture\n')
        (gateway / 'README.md').write_text('fixture\n')
        (self.source / 'ai_skills').mkdir()
        (self.source / 'deploy').mkdir()
        shutil.copyfile(ROOT / 'deploy/package-server.sh',
                        self.source / 'deploy/package-server.sh')
        shutil.copyfile(ROOT / 'deploy/static_evidence_guard.py',
                        self.source / 'deploy/static_evidence_guard.py')
        for name in ('midscene-upload.py', 'midscene_upload_compat.py',
                     'requirements-api-testing.txt', 'requirements-api-testing-dev.txt',
                     'task-manager.html', 'trace-viewer.html',
                     'sonic-midscene-task-runner.groovy'):
            (self.source / name).write_text('fixture\n')

    def run_shell(self, *arguments, **environment):
        result = subprocess.run(
            ['bash', *arguments], cwd=self.work,
            env={'PATH': os.defpath, **{key: str(value) for key, value in environment.items()}},
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_archive_contains_exact_native_upload_module(self):
        output = self.work / 'packages'
        self.run_shell(str(self.source / 'deploy/package-server.sh'),
                       OUT_DIR=output, VERSION='native-regression', KEEP_PACKAGES=0)
        archive = output / 'midscene-task-platform-native-regression.tar.gz'
        with tarfile.open(archive, 'r:gz') as package:
            for name in ('server.js', HELPER):
                member = f'midscene-task-platform/ai-gateway/{name}'
                self.assertIn(member, package.getnames(), f'missing deployed module: {member}')
                self.assertEqual(package.extractfile(member).read(),
                                 (ROOT / 'ai-gateway' / name).read_bytes())

    def test_installer_gateway_copy_loop_installs_and_updates_exact_module(self):
        installer = (ROOT / 'deploy/install-server.sh').read_text()
        # Execute the actual bounded copy loop, never the privileged installer body.
        gateway_section = installer.split('# Deploy AI Gateway', 1)[1]
        match = re.search(r'^  for item in .*?^  done$', gateway_section,
                          flags=re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, 'Gateway copy loop must remain identifiable')
        destination = self.work / 'installed-gateway'
        destination.mkdir()
        for stale in (False, True):
            with self.subTest(stale=stale):
                if stale:
                    (destination / HELPER).write_bytes(b'stale upload module\n')
                self.run_shell('-euc', match.group(0),
                               SRC_DIR=self.source, AI_GATEWAY_DIR=destination)
                installed = destination / HELPER
                self.assertTrue(installed.is_file(), 'installer omitted native upload module')
                self.assertEqual(installed.read_bytes(), (ROOT / 'ai-gateway' / HELPER).read_bytes())
                self.assertEqual((destination / 'server.js').read_bytes(),
                                 (ROOT / 'ai-gateway/server.js').read_bytes())


if __name__ == '__main__':
    unittest.main()
