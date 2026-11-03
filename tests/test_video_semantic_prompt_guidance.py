import unittest
from pathlib import Path

class SemanticPromptGuidance(unittest.TestCase):
    def test_observer_does_not_project_gestures_onto_arrow_clicks(self):
        text=Path('ai_skills/prompts/ui_video_trajectory.observe.md').read_text()
        self.assertNotIn('可记录aiTap该箭头',text)
        self.assertNotIn('可表达为 aiTap/navigation',text)
        self.assertIn('aiTap 仅用于原图支持的实际控件点击',text)
    def test_observer_and_auditor_distinguish_effect_from_touch_location(self):
        for name in ['ui_video_trajectory.observe','ui_video_trajectory.audit']:
            text=Path('ai_skills/prompts/'+name+'.md').read_text()
            for rule in ['逻辑效果目标，不是物理触点位置','不要求触点落在键盘内部','键盘外点击','系统返回或边缘手势','同页 IME 隐藏','实际页面返回','灰点本身均不足以证明']:
                with self.subTest(name=name,rule=rule):self.assertIn(rule,text)
