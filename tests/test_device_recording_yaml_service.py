import unittest

from task_server.services.device_recording_yaml_service import generate_recording_yaml, normalize_recorded_step


class DeviceRecordingYamlServiceTest(unittest.TestCase):
    def test_tap_prefers_visible_text_and_never_emits_coordinates(self):
        result = normalize_recorded_step({
            "type": "tap", "point": {"x": 10, "y": 20},
            "ui_node": {"text": "下一步", "resource_id": "com.demo:id/next"},
        })
        self.assertEqual(result["flow"], [{"aiTap": "下一步"}])
        self.assertNotIn("10", str(result["flow"]))
        self.assertFalse(result["requires_confirmation"])

    def test_tap_without_semantics_requires_confirmation(self):
        result = normalize_recorded_step({"type": "tap", "point": {"x": 10, "y": 20}})
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual(result["flow"], [])

    def test_legacy_image_base64_is_not_generated_as_a_tap_name(self):
        result = normalize_recorded_step({"type": "tap", "ui_node": {"text": "Fn+i0op0v4AAAAAElFTkSuQmCC"}})
        self.assertEqual(result["flow"], [])
        self.assertTrue(result["requires_confirmation"])

    def test_human_reviewed_semantics_resolves_ambiguous_tap(self):
        result = normalize_recorded_step({
            "type": "tap", "point": {"x": 10, "y": 20}, "semantic_description": "提交订单按钮",
        })
        self.assertEqual(result["flow"], [{"aiTap": "提交订单按钮"}])
        self.assertFalse(result["requires_confirmation"])

    def test_input_scroll_key_and_checkpoint_use_supported_actions(self):
        steps = [
            {"type": "text", "text": "测试内容", "ui_node": {"content_desc": "搜索输入框"}},
            {"type": "swipe", "start": {"x": 500, "y": 1500}, "end": {"x": 500, "y": 400}},
            {"type": "key", "key": "BACK"},
            {"type": "checkpoint", "checkpoint_kind": "assert", "description": "页面显示提交成功"},
        ]
        normalized = [normalize_recorded_step(step) for step in steps]
        self.assertEqual(normalized[0]["flow"], [{"aiInput": "搜索输入框", "value": "测试内容"}])
        self.assertEqual(normalized[1]["flow"][0]["aiScroll"], "在当前页面向上滚动")
        self.assertEqual(normalized[2]["flow"], [{"runAdbShell": "input keyevent 4"}])
        self.assertEqual(normalized[3]["flow"], [{"aiAssert": "页面显示提交成功"}])

    def test_generates_validated_draft_and_blocks_ambiguous_steps(self):
        session = {
            "id": "session-1", "status": "finished", "app_package": "com.demo", "steps": [
                {"type": "launch", "package": "com.demo"},
                {"type": "tap", "ui_node": {"text": "我的"}},
                {"type": "checkpoint", "checkpoint_kind": "assert", "description": "页面显示个人中心"},
            ],
        }
        result = generate_recording_yaml(session, task_name="打开个人中心")
        self.assertEqual(result["task_name"], "打开个人中心")
        self.assertIn("launch: com.demo", result["yaml"])
        self.assertIn('aiTap: 我的', result["yaml"])
        self.assertFalse(result["requires_confirmation"])
        self.assertIn("validation", result)
        self.assertIn("score", result)

        blocked = generate_recording_yaml({**session, "steps": [{"type": "tap", "point": {"x": 1, "y": 2}}]})
        self.assertTrue(blocked["requires_confirmation"])
        self.assertTrue(blocked["issues"])

    def test_selected_application_is_the_only_launch_and_is_always_first(self):
        session = {
            "status": "finished", "app_package": "com.kfb.model", "steps": [
                {"type": "tap", "semantic_description": "我的"},
                {"type": "launch", "package": "com.wrong.app"},
                {"type": "launch", "package": "com.kfb.model"},
            ],
        }
        result = generate_recording_yaml(session, task_name="查看我的")
        import yaml
        flow = yaml.safe_load(result["yaml"])["tasks"][0]["flow"]
        self.assertEqual(flow[0], {"launch": "com.kfb.model"})
        self.assertEqual([item for item in flow if "launch" in item], [{"launch": "com.kfb.model"}])
        self.assertEqual(flow[1], {"aiTap": "我的"})

    def test_rejects_empty_or_active_recording_instead_of_inventing_launch_only_yaml(self):
        with self.assertRaisesRegex(ValueError, "结束录制"):
            generate_recording_yaml({"status": "recording", "app_package": "com.demo", "steps": [{"type": "tap", "ui_node": {"text": "我的"}}]})
        with self.assertRaisesRegex(ValueError, "没有记录到手机操作"):
            generate_recording_yaml({"status": "finished", "app_package": "com.demo", "steps": []})


if __name__ == "__main__":
    unittest.main()
