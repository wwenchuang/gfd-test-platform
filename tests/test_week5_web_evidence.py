import hashlib
import tempfile
import unittest
from pathlib import Path

from task_server.services.week5_web_evidence import read_evidence


class EvidenceTest(unittest.TestCase):
    def test_returns_original_and_rejects_tampering_and_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root) / ('a' * 32) / 'evidence'
            run.mkdir(parents=True)
            image = run / 'screenshot.png'
            image.write_bytes(b'original')
            digest = hashlib.sha256(b'original').hexdigest()
            self.assertEqual(read_evidence(root, 'a' * 32, 'screenshot', digest), b'original')
            image.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                read_evidence(root, 'a' * 32, 'screenshot', digest)
            image.unlink()
            image.symlink_to(Path(root) / 'another-file')
            with self.assertRaises(ValueError):
                read_evidence(root, 'a' * 32, 'screenshot', digest)

    def test_rejects_unknown_items_and_unsafe_ids(self):
        with tempfile.TemporaryDirectory() as root:
            for run_id, item in [('../secret', 'screenshot'), ('a' * 32, 'other')]:
                with self.assertRaises(ValueError):
                    read_evidence(root, run_id, item, 'a' * 64)
