import unittest
from task_server.services.ui_video_analysis import _required_interactions
from task_server.services.ui_video_temporal_ownership import INTERACTIONS
from task_server.services.ui_video_causal_recheck import INTERACTIONS as CAUSAL

class KeyboardRetention(unittest.TestCase):
    def test_keyboard_is_required_intervention(self):
        event={'action':'dismissKeyboard','start_ms':100,'end_ms':200,'target':'当前页面的软键盘'}
        self.assertEqual(_required_interactions([event],1000),[0])
    def test_keyboard_bounds_temporal_and_sticky_causal_domains(self):
        self.assertIn('dismissKeyboard',INTERACTIONS)
        self.assertIn('dismissKeyboard',CAUSAL)
