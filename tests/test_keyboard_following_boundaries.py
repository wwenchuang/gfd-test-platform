import unittest
from unittest.mock import patch
from task_server.services.ui_video_terminal_cleanup import is_terminal_app_exit
from task_server.services.ui_video_business_inputs import recover_business_inputs
from task_server.services.ui_video_outcomes import repair_outcomes

class Captured(Exception): pass
class KeyboardBoundaries(unittest.TestCase):
    def test_exit_followed_by_keyboard_is_not_terminal_cleanup(self):
        events=[dict(action='aiTap',start_ms=900,target='系统返回键',observation='退出应用，返回手机桌面',uncertainties=[]),dict(action='dismissKeyboard',start_ms=950)]
        self.assertFalse(is_terminal_app_exit(events,0,1000))

    def test_input_recovery_cannot_borrow_after_keyboard(self):
        frames=[dict(frame_id='f0',timestamp_ms=0),dict(frame_id='f1',timestamp_ms=100),dict(frame_id='f2',timestamp_ms=200),dict(frame_id='f3',timestamp_ms=300)]
        events=[dict(action='aiInput',target='搜索',observation='输入',end_ms=100,frame_refs=['f0','f1'],uncertainties=[]),dict(action='dismissKeyboard',start_ms=200)]
        def runner(name,payload,**kwargs):
            self.assertEqual(payload['checks'][0]['final_input_frame_ids'],['f1'])
            raise Captured()
        with patch('task_server.services.ui_video_windows.load_assets',return_value=[]),self.assertRaises(Captured):
            recover_business_inputs({'events':events},{'media':{'frames':frames,'duration_ms':400}},None,lambda:False,skill_runner=runner)

    def test_outcome_repair_cannot_borrow_after_keyboard(self):
        frames=[dict(frame_id='f0',timestamp_ms=0),dict(frame_id='f1',timestamp_ms=100),dict(frame_id='f2',timestamp_ms=200),dict(frame_id='f3',timestamp_ms=300)]
        candidate=dict(action='aiTap',target='搜索',start_ms=0,end_ms=100,frame_refs=['f0','f1'])
        def runner(name,payload,**kwargs):
            self.assertEqual(payload['checks'][0]['outcome_frame_ids'],['f1','f2'])
            raise Captured()
        with patch('task_server.services.ui_video_windows.load_assets',return_value=[]),self.assertRaises(Captured):
            repair_outcomes({'events':[dict(candidate,candidate_index=0)]},[candidate,dict(action='dismissKeyboard',start_ms=200,target='当前页面的软键盘')],frames,400,None,[0],lambda:False,[],skill_runner=runner)
