import unittest
from task_server.services.ui_video_analysis import _required_interactions
from task_server.services.ui_video_temporal_ownership import INTERACTIONS
from task_server.services.ui_video_causal_recheck import INTERACTIONS as CAUSAL

class NavigationRetention(unittest.TestCase):
    def test_navigation_is_required_intervention(self):
        event={'action':'navigateBack','start_ms':100,'end_ms':200,'target':'上一页面'}
        self.assertEqual(_required_interactions([event],1000),[0])
    def test_navigation_bounds_temporal_and_sticky_causal_domains(self):
        self.assertIn('navigateBack',INTERACTIONS)
        self.assertIn('navigateBack',CAUSAL)

    def test_navigation_identity_keeps_interaction_refutation_key(self):
        from task_server.services.ui_video_causal_recheck import identity
        original={"action":"aiTap","trigger_frame":"f1"}
        refined={"action":"navigateBack","trigger_frame":"f1"}
        self.assertEqual(identity(original),identity(refined))
