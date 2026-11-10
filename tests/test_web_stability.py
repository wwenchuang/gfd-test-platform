import fcntl
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from task_server.services import web_stability as stability


class WebStabilityTest(unittest.TestCase):
    def test_different_scope_cannot_read_latest(self):
        profile = {'user_id':'u', 'scope':{'ui_apps':['a']}}
        other = {'user_id':'u', 'scope':{'ui_apps':['b']}}
        with tempfile.TemporaryDirectory() as directory, patch.object(stability, 'folder', return_value=Path(directory)), \
             patch.object(stability.web, '_profile', side_effect=(profile,other)):
            stability.save(Path(directory)/'latest.json', {'scope':stability.web._scope(profile), 'report':{}})
            self.assertIsNone(stability.latest('u','name','token'))

    def test_release_of_measurement_lock_marks_partial_as_interrupted(self):
        profile = {'user_id':'u'}
        with tempfile.TemporaryDirectory() as directory, patch.object(stability, 'folder', return_value=Path(directory)), \
             patch.object(stability.web, '_profile', return_value=profile):
            root=Path(directory)
            stability.save(root/'latest.json', {'scope':stability.web._scope(profile), 'report':{'unrun':1}})
            with (root/'measurement.lock').open('w') as writer:
                fcntl.flock(writer, fcntl.LOCK_EX)
                self.assertEqual(stability.latest('u','name','token')['state'], 'running')
            self.assertEqual(stability.latest('u','name','token')['state'], 'interrupted')

    def test_artifact_rejects_traversal_and_unknown_round_without_creating_files(self):
        query={'measurement':'1'*32,'id':'a'*32,'round':'8','item':'trace'}
        with tempfile.TemporaryDirectory() as directory, patch.object(stability,'folder',return_value=Path(directory)):
            for change in ({}, {'round':'../3'}, {'round':'21'}, {'measurement':'../../secrets'}, {'id':'other'}):
                with self.assertRaises(ValueError):
                    stability.artifact(('u','n','t'),{**query, **change})
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_first_attempt_report_keeps_blocked_failed_and_unrun_separate(self):
        rows=[dict(scenario=stability.CASES[0], status=status, duration_ms=ms)
              for status,ms in [('passed',40),('failed',60),('blocked',999)]]
        result=stability.build_report(rows,1,'id','sha')
        self.assertEqual([result[k] for k in ('passed','failed','blocked','unrun')],[1,1,1,197])
        self.assertEqual(result['median_ms'],{stability.CASES[0]:50})

    def test_example_archive_contains_real_failure_and_visual_images(self):
        with zipfile.ZipFile(io.BytesIO(stability.smoke_evidence())) as archive:
            self.assertIsNone(archive.testzip())
            audit=json.loads(archive.read('docs/audits/week6-ci/verified.json'))
            self.assertEqual([r['exit'] for r in audit['results'] if r['profile']=='wrong-title'],[1])
            with zipfile.ZipFile(io.BytesIO(archive.read('docs/audits/week6-ci/wrong-title-trace.zip'))) as trace:
                self.assertIn('trace.trace',trace.namelist())
            self.assertTrue(archive.read('docs/audits/week6-visual/spacing-diff.png').startswith(b'\x89PNG'))
