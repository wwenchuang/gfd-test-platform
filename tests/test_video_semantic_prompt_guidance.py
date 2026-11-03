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

    def test_closed_action_contracts_cover_schema_semantics(self):
        import json,re
        configs=[('ui_video_trajectory.observe','ui_video_trajectory',r'action：([^；]+)'),('ui_video_steps.consolidate','ui_video_steps',r'action仅([^；]+)'),('ui_video_observe.v1','ui_video_observe',r'action仅([^。]+)'),('ui_step_normalizer.v1','ui_step_normalizer',r'仅允许 ([^。]+)')]
        def enum_actions(node):
            if isinstance(node,dict):
                for key,value in node.items():
                    if key=='action' and isinstance(value,dict) and 'enum' in value:return set(value['enum'])
                    found=enum_actions(value)
                    if found:return found
            elif isinstance(node,list):
                for value in node:
                    found=enum_actions(value)
                    if found:return found
            return set()
        for prompt,schema,pattern in configs:
            text=Path('ai_skills/prompts/'+prompt+'.md').read_text()
            actions=set(re.findall(r'[A-Za-z][A-Za-z0-9]*',re.search(pattern,text).group(1)))
            semantics=enum_actions(json.loads(Path('ai_skills/schemas/'+schema+'.schema.json').read_text())) & {'dismissKeyboard','navigateBack'}
            with self.subTest(prompt=prompt):self.assertTrue(semantics<=actions,(semantics,actions))

    def test_all_stages_reject_old_equivalent_arrow_and_input_merger_rules(self):
        for name in ['ui_video_trajectory.observe','ui_video_trajectory.audit','ui_video_steps.consolidate','ui_video_observe.v1','ui_step_normalizer.v1']:
            text=Path('ai_skills/prompts/'+name+'.md').read_text()
            with self.subTest(name=name):
                for obsolete in ['可用该箭头表达等价返回','输出aiTap该箭头作为跨端等价返回','表达为等价可见控件','以及收起键盘都属于同一个字段']:
                    self.assertFalse(obsolete in text,obsolete)
                self.assertIn('逻辑效果目标，不是物理触点位置',text)
