import unittest
from task_server.services.ui_video_stage_policy import video_stage_timeout

class StageTimeoutTests(unittest.TestCase):
    def test_closed_stage_caps(self):
        for stage in ('grounding', 'audit'):
            self.assertEqual(video_stage_timeout(stage), 180)
        for stage in (None, 'overview', 'semantic', 'timeline', 'unknown'):
            self.assertEqual(video_stage_timeout(stage), 120)
