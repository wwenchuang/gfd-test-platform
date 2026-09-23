import json
import os
import tempfile
import time
import unittest
import base64
import io
import struct

from task_server.services import device_recording_service as recording


class DeviceRecordingServiceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = os.path.join(self.tempdir.name, "recordings.json")

    def create(self, **overrides):
        values = dict(
            user="admin",
            runner_id="win-runner-01",
            device_id="ecbfd645",
            app_package="com.tencent.mm",
            store_path=self.store,
        )
        values.update(overrides)
        return recording.create_recording_session(**values)

    def prepare_frame(self, session, *, xml='<hierarchy />', ui_xml_error='', now=None):
        if now is None:
            now = time.time() + 3
        request = recording.pending_recording_evidence_requests(
            "win-runner-01", store_path=self.store, now=now
        )[0]
        return recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"], "step_id": "",
            "device_id": "ecbfd645", "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode(),
            "ui_xml": xml, "ui_xml_error": ui_xml_error,
        }, store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"))

    def test_rejects_business_printer_identifier(self):
        with self.assertRaisesRegex(ValueError, "打印机"):
            self.create(device_id="18CEDF5BA7B2")
        with self.assertRaisesRegex(ValueError, "打印机"):
            self.create(device_id="9888E0094F2A")

    def test_only_one_active_session_per_device(self):
        first = self.create()
        with self.assertRaisesRegex(ValueError, "正在录制"):
            self.create(user="another")
        self.assertEqual(
            recording.active_recording_for_device("win-runner-01", "ecbfd645", store_path=self.store)["id"],
            first["id"],
        )

    def test_short_lived_recording_token_can_bind_and_poll_the_actual_sonic_phone(self):
        session = self.create(runner_id="", device_id="")
        with self.assertRaisesRegex(PermissionError, "令牌"):
            recording.bridge_recording_device(session["id"], "wrong", "win-runner-01", "ecbfd645", store_path=self.store)
        bound = recording.bridge_recording_device(
            session["id"], session["recording_token"], "win-runner-01", "ecbfd645", store_path=self.store
        )
        self.assertEqual((bound["runner_id"], bound["device_id"]), ("win-runner-01", "ecbfd645"))
        self.assertEqual(bound["pre_action_frame_status"], "pending")
        polled = recording.bridge_recording_device(
            session["id"], session["recording_token"], "win-runner-01", "ecbfd645", store_path=self.store
        )
        self.assertEqual(polled["pre_action_frame_request_id"], bound["pre_action_frame_request_id"])

    def test_bridge_refresh_discards_stale_frame_after_sonic_reconnect(self):
        session = self.create(runner_id="", device_id="")
        bound = recording.bridge_recording_device(
            session["id"], session["recording_token"], "win-runner-01", "ecbfd645", store_path=self.store
        )
        self.prepare_frame(session, xml='<hierarchy><node text="我的" bounds="[0,0][200,200]" /></hierarchy>')
        refreshed = recording.bridge_recording_device(
            session["id"], session["recording_token"], "win-runner-01", "ecbfd645",
            refresh_evidence=True, store_path=self.store,
        )
        self.assertEqual(refreshed["pre_action_frame_status"], "pending")
        self.assertNotEqual(refreshed["pre_action_frame_request_id"], bound["pre_action_frame_request_id"])
        with self.assertRaisesRegex(ValueError, "尚未准备"):
            recording.append_recorded_action(
                session["id"], session["recording_token"],
                {"event_id": "evt-stale", "type": "tap", "point": {"x": 50, "y": 60}, "device_id": "ecbfd645"},
                store_path=self.store,
            )
        self.prepare_frame(session, xml='<hierarchy><node text="删除" bounds="[0,0][200,200]" /></hierarchy>')
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-fresh", "type": "tap", "point": {"x": 50, "y": 60}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        self.assertEqual(step["ui_node"]["text"], "删除")

    def test_owner_controls_finish_and_state_is_persisted_atomically(self):
        session = self.create()
        with self.assertRaisesRegex(PermissionError, "发起人"):
            recording.finish_recording_session(session["id"], "another", store_path=self.store)
        self.prepare_frame(session)
        recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-finish", "type": "key", "key": "BACK", "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        finished = recording.finish_recording_session(session["id"], "admin", store_path=self.store)
        self.assertEqual(finished["status"], "finished")
        with open(self.store, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["sessions"][0]["status"], "finished")
        self.assertFalse(os.path.exists(self.store + ".tmp"))

    def test_stale_recording_is_paused_and_no_longer_blocks_device(self):
        session = self.create(now=100.0)
        current = recording.get_recording_session(
            session["id"], store_path=self.store, now=100.0 + recording.RECORDING_STALE_SECONDS + 1
        )
        self.assertEqual(current["status"], "paused")
        replacement = self.create(user="another", now=100.0 + recording.RECORDING_STALE_SECONDS + 2)
        self.assertNotEqual(replacement["id"], session["id"])

    def test_touch_resumes_owner_session_and_rejects_other_user(self):
        session = self.create(now=100.0)
        recording.get_recording_session(
            session["id"], store_path=self.store, now=100.0 + recording.RECORDING_STALE_SECONDS + 1
        )
        with self.assertRaisesRegex(PermissionError, "发起人"):
            recording.touch_recording_session(session["id"], "another", store_path=self.store)
        resumed = recording.touch_recording_session(session["id"], "admin", store_path=self.store, now=500.0)
        self.assertEqual(resumed["status"], "recording")
        self.assertEqual(resumed["heartbeat_ts"], 500.0)

    def test_action_token_orders_actions_and_rejects_replay(self):
        session = self.create()
        token = session["recording_token"]
        self.prepare_frame(session)
        first = recording.append_recorded_action(
            session["id"], token,
            {"event_id": "evt-1", "type": "tap", "point": {"x": 120, "y": 240}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        self.prepare_frame(session)
        second = recording.append_recorded_action(
            session["id"], token,
            {"event_id": "evt-2", "type": "key", "key": "BACK", "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        self.assertEqual((first["sequence"], second["sequence"]), (1, 2))
        replay = recording.append_recorded_action(
            session["id"], token,
            {"event_id": "evt-1", "type": "tap", "point": {"x": 120, "y": 240}, "device_id": "ecbfd645"},
            store_path=self.store,
        )
        self.assertTrue(replay["duplicate"])
        self.assertEqual(len(recording.get_recording_session(session["id"], store_path=self.store)["steps"]), 2)

    def test_action_rejects_wrong_token_device_type_and_large_text(self):
        session = self.create()
        token = session["recording_token"]
        base = {"event_id": "evt", "type": "tap", "point": {"x": 1, "y": 2}, "device_id": "ecbfd645"}
        with self.assertRaisesRegex(PermissionError, "令牌"):
            recording.append_recorded_action(session["id"], "wrong", base, store_path=self.store)
        with self.assertRaisesRegex(ValueError, "设备"):
            recording.append_recorded_action(session["id"], token, {**base, "device_id": "other"}, store_path=self.store)
        with self.assertRaisesRegex(ValueError, "动作类型"):
            recording.append_recorded_action(session["id"], token, {**base, "type": "shell"}, store_path=self.store)
        with self.assertRaisesRegex(ValueError, "文本"):
            recording.append_recorded_action(session["id"], token, {**base, "type": "text", "text": "x" * 501}, store_path=self.store)

    def test_evidence_matches_smallest_ui_node_and_is_idempotent(self):
        session = self.create()
        xml = '<hierarchy><node text="外层" bounds="[0,0][200,200]"><node text="确定" resource-id="com.demo:id/ok" bounds="[20,30][100,90]" /></node></hierarchy>'
        self.prepare_frame(session, xml=xml)
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-1", "type": "tap", "point": {"x": 50, "y": 60}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        self.assertEqual(step["ui_node"]["text"], "确定")
        again = recording.save_recording_evidence("win-runner-01", {
            "session_id": session["id"], "step_id": step["id"], "device_id": "ecbfd645",
        }, store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"))
        self.assertEqual(again["steps"][0]["evidence_status"], "captured")

    def test_next_phone_frame_marks_tap_without_page_change_for_review(self):
        session = self.create()
        xml = '<hierarchy><node text="打印记录" bounds="[0,0][200,200]" /></hierarchy>'
        self.prepare_frame(session, xml=xml)
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-print-history", "type": "tap", "point": {"x": 50, "y": 60}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        after = self.prepare_frame(session, xml=xml)
        saved = after["steps"][0]
        self.assertEqual(saved["id"], step["id"])
        self.assertEqual(saved["screen_change_status"], "unchanged")
        self.assertIn("页面结构未变化", saved["evidence_warning"])

    def test_next_phone_frame_marks_observed_change_without_claiming_navigation(self):
        session = self.create()
        self.prepare_frame(session, xml='<hierarchy><node text="我的" bounds="[0,0][200,200]" /></hierarchy>')
        recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-open", "type": "tap", "point": {"x": 50, "y": 60}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        after = self.prepare_frame(session, xml='<hierarchy><node text="打印记录页" bounds="[0,0][200,200]" /></hierarchy>')
        self.assertEqual(after["steps"][0]["screen_change_status"], "changed")

    def test_screenshot_is_kept_and_visually_named_when_ui_xml_is_unavailable(self):
        session = self.create()
        self.prepare_frame(session, ui_xml_error="ADB 页面结构采集失败")
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-visual", "type": "tap", "point": {"x": 80, "y": 120}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        self.assertEqual(step["evidence_status"], "captured")
        self.assertEqual(step["ui_xml_error"], "ADB 页面结构采集失败")

        def model_call(prompt, **kwargs):
            self.assertIn("x=80, y=120", prompt)
            self.assertNotIn('"semantic_description":"底部导航「我的」"', prompt)
            self.assertTrue(kwargs["image_assets"][0]["base64"])
            return '{"semantic_description":"底部导航「我的」","confidence":0.95}'

        recognized = recording.recognize_recording_semantics(
            session["id"], "admin", store_path=self.store, model_call=model_call,
        )
        saved = recognized["steps"][0]
        self.assertEqual(saved["semantic_description"], "底部导航「我的」")
        self.assertEqual(saved["semantic_source"], "ai_visual")
        self.assertEqual(saved["semantic_recognition_status"], "recognized")

    def test_base64_image_node_is_not_used_as_a_control_name(self):
        session = self.create()
        self.prepare_frame(session, xml='<hierarchy><node text="Fn+i0op0v4AAAAAElFTkSuQmCC" class="android.widget.Image" bounds="[0,0][200,200]" /></hierarchy>')
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-image", "type": "tap", "point": {"x": 72, "y": 176}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        self.assertEqual(step["ui_node"]["text"], "")

    def test_visual_recognition_receives_marked_click_closeup(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed in this local Python environment")
        session = self.create()
        request = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store)[0]
        image = Image.new("RGB", (1080, 2400), "white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"], "step_id": "",
            "device_id": "ecbfd645", "content_base64": base64.b64encode(output.getvalue()).decode(),
            "ui_xml_error": "ADB 页面结构采集失败",
        }, store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"))
        recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-home", "type": "tap", "point": {"x": 115, "y": 2318}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )

        def model_call(prompt, **kwargs):
            self.assertIn("红色圆圈", prompt)
            self.assertEqual(len(kwargs["image_assets"]), 2)
            closeup = Image.open(io.BytesIO(base64.b64decode(kwargs["image_assets"][1]["base64"])))
            self.assertEqual(closeup.size, (600, 600))
            self.assertNotEqual(closeup.getpixel((115, 518)), (255, 255, 255))
            return '{"semantic_description":"底部导航首页","confidence":0.9}'

        recognized = recording.recognize_recording_semantics(
            session["id"], "admin", store_path=self.store, model_call=model_call,
        )
        self.assertEqual(recognized["steps"][0]["semantic_description"], "底部导航首页")

    def test_finished_recording_keeps_pending_evidence_available_during_grace_period(self):
        session = self.create(now=1000)
        self.prepare_frame(session, now=1000)
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-last", "type": "tap", "point": {"x": 50, "y": 60}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"), now=1001,
        )
        recording.finish_recording_session(session["id"], "admin", store_path=self.store, now=1002)
        pending = recording.pending_recording_evidence_requests(
            "win-runner-01", store_path=self.store, now=1003,
        )
        self.assertEqual(pending, [])
        self.assertEqual(
            recording.pending_recording_evidence_requests(
                "win-runner-01", store_path=self.store, now=1002 + recording.FINISHED_EVIDENCE_GRACE_SECONDS + 1,
            ),
            [],
        )

    def test_owner_can_confirm_semantic_target_for_ambiguous_step(self):
        session = self.create()
        self.prepare_frame(session)
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-1", "type": "tap", "point": {"x": 1, "y": 2}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        with self.assertRaisesRegex(PermissionError, "发起人"):
            recording.update_recorded_step(session["id"], "another", step["id"], "提交按钮", store_path=self.store)
        updated = recording.update_recorded_step(session["id"], "admin", step["id"], "提交按钮", store_path=self.store)
        self.assertEqual(updated["steps"][0]["semantic_description"], "提交按钮")

    def test_owner_history_and_generated_yaml_are_persisted(self):
        session = self.create()
        self.prepare_frame(session)
        recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-history", "type": "key", "key": "BACK", "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        recording.finish_recording_session(session["id"], "admin", store_path=self.store)
        saved = recording.save_generated_recording_result(
            session["id"], "admin", {"yaml": "tasks: []", "can_debug": True}, store_path=self.store,
        )
        self.assertEqual(saved["generated_result"]["yaml"], "tasks: []")
        self.assertEqual(recording.list_recording_sessions("admin", store_path=self.store)[0]["id"], session["id"])
        self.assertEqual(recording.list_recording_sessions("another", store_path=self.store), [])

    def test_owner_can_edit_description_and_delete_recorded_step(self):
        session = self.create()
        self.prepare_frame(session)
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-1", "type": "key", "key": "BACK", "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        updated = recording.update_recorded_step(
            session["id"], "admin", step["id"], "返回上一页", store_path=self.store
        )
        self.assertEqual(updated["steps"][0]["semantic_description"], "返回上一页")
        with self.assertRaisesRegex(PermissionError, "发起人"):
            recording.delete_recorded_step(session["id"], "another", step["id"], store_path=self.store)
        deleted = recording.delete_recorded_step(session["id"], "admin", step["id"], store_path=self.store)
        self.assertEqual(deleted["steps"], [])

    def test_empty_recording_cannot_be_finished_as_success(self):
        session = self.create()
        with self.assertRaisesRegex(ValueError, "没有记录到手机操作"):
            recording.finish_recording_session(session["id"], "admin", store_path=self.store)
        cancelled = recording.cancel_recording_session(session["id"], "admin", store_path=self.store)
        self.assertEqual(cancelled["status"], "cancelled")
        history = recording.list_recording_sessions("admin", store_path=self.store)
        self.assertEqual(history[0]["id"], session["id"])
        self.assertEqual(history[0]["status"], "cancelled")

    def test_unbound_session_uses_the_phone_actually_opened_in_sonic(self):
        session = self.create(runner_id="", device_id="")
        second = self.create(user="another", runner_id="", device_id="")
        self.assertNotEqual(session["id"], second["id"])
        self.assertEqual(session["device_id"], "")
        bound = recording.bind_recording_device(
            session["id"], "admin", "win-runner-01", "ecbfd645", store_path=self.store
        )
        self.assertEqual((bound["runner_id"], bound["device_id"]), ("win-runner-01", "ecbfd645"))
        self.assertEqual(bound["pre_action_frame_status"], "pending")
        requests = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store)
        self.assertEqual(requests[0]["kind"], "pre_action_frame")
        self.assertEqual(requests[0]["step_id"], "")
        with self.assertRaisesRegex(ValueError, "已经绑定"):
            recording.bind_recording_device(
                session["id"], "admin", "win-runner-01", "other-phone", store_path=self.store
            )

    def test_action_uses_runner_cached_pre_action_frame_and_requests_the_next_one(self):
        session = self.create(runner_id="", device_id="")
        bound = recording.bind_recording_device(
            session["id"], "admin", "win-runner-01", "ecbfd645", store_path=self.store
        )
        request = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store)[0]
        evidence_dir = os.path.join(self.tempdir.name, "evidence")
        ready = recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"], "step_id": "", "device_id": "ecbfd645",
            "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\npre-action").decode(),
            "ui_xml": '<hierarchy><node text="返回" bounds="[0,0][100,100]" /></hierarchy>',
        }, store_path=self.store, evidence_dir=evidence_dir)
        self.assertEqual(ready["pre_action_frame_status"], "ready")
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id":"evt-pre","type":"tap","point":{"x":20,"y":30},"device_id":"ecbfd645"},
            store_path=self.store, evidence_dir=evidence_dir,
        )
        self.assertEqual(step["evidence_status"], "captured")
        self.assertEqual(step["ui_node"]["text"], "返回")
        current = recording.get_recording_session(session["id"], store_path=self.store)
        self.assertEqual(current["pre_action_frame_status"], "pending")
        self.assertEqual(recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store), [])
        next_request = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store, now=time.time() + 3)[0]
        self.assertEqual(next_request["kind"], "pre_action_frame")
        self.assertNotEqual(next_request["request_id"], request["request_id"])

    def test_action_prefers_current_sonic_frame_over_stale_runner_frame(self):
        session = self.create()
        self.prepare_frame(session, xml='<hierarchy><node text="版本号" bounds="[0,0][100,100]" /></hierarchy>')
        current_frame = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 200, 400) + b"current frame"
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-current", "type": "tap", "point": {"x": 50, "y": 100},
             "device_id": "ecbfd645", "evidence_content_base64": base64.b64encode(current_frame).decode()},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        with open(step["screenshot_path"], "rb") as handle:
            self.assertEqual(handle.read(), current_frame)
        self.assertEqual(step["evidence_source"], "sonic_live_frame")
        self.assertEqual(step["point"], {"x": 50, "y": 100})
        self.assertEqual(step["ui_node"], {})

    def test_stale_runner_frame_cannot_name_a_later_tap(self):
        session = self.create()
        self.prepare_frame(session, xml='<hierarchy><node text="版本号" bounds="[0,0][100,100]" /></hierarchy>')
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-stale", "type": "tap", "point": {"x": 50, "y": 50}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"), now=time.time() + 10,
        )
        self.assertEqual(step["evidence_status"], "failed")
        self.assertEqual(step["ui_node"], {})
        self.assertIn("过期", step["evidence_warning"])

    def test_action_is_rejected_until_the_real_pre_action_frame_is_ready(self):
        session = self.create(runner_id="", device_id="")
        recording.bind_recording_device(
            session["id"], "admin", "win-runner-01", "ecbfd645", store_path=self.store
        )
        with self.assertRaisesRegex(ValueError, "点击前画面尚未准备"):
            recording.append_recorded_action(
                session["id"], session["recording_token"],
                {"event_id":"evt-too-early","type":"tap","point":{"x":20,"y":30},"device_id":"ecbfd645"},
                store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
            )
        current = recording.get_recording_session(session["id"], store_path=self.store)
        self.assertEqual(current["steps"], [])

    def test_failed_pre_action_frame_retries_with_backoff_and_stops_after_three_attempts(self):
        session = self.create(runner_id="", device_id="")
        bound = recording.bind_recording_device(
            session["id"], "admin", "win-runner-01", "ecbfd645", store_path=self.store, now=100
        )
        for attempt in range(1, 4):
            request = recording.pending_recording_evidence_requests(
                "win-runner-01", store_path=self.store, now=100 + (attempt - 1) * 4
            )[0]
            failed = recording.save_recording_evidence("win-runner-01", {
                "request_id": request["request_id"], "session_id": session["id"], "step_id": "",
                "device_id": "ecbfd645", "error": "ADB screenshot failed",
            }, store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"))
            self.assertEqual(failed["pre_action_frame_status"], "failed")
            self.assertEqual(failed["pre_action_frame_attempts"], attempt)
            recording.touch_recording_session(
                session["id"], "admin", store_path=self.store, now=101 + (attempt - 1) * 4
            )
            self.assertEqual(
                recording.pending_recording_evidence_requests(
                    "win-runner-01", store_path=self.store, now=101 + (attempt - 1) * 4
                ), []
            )
            recording.touch_recording_session(
                session["id"], "admin", store_path=self.store, now=104 + (attempt - 1) * 4
            )
        current = recording.get_recording_session(session["id"], store_path=self.store, now=120)
        self.assertEqual(current["pre_action_frame_status"], "failed")
        self.assertEqual(current["pre_action_frame_attempts"], 3)
        self.assertEqual(
            recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store, now=120), []
        )

    def test_consecutive_actions_never_reuse_the_previous_pre_action_frame(self):
        session = self.create(runner_id="", device_id="")
        recording.bind_recording_device(session["id"], "admin", "win-runner-01", "ecbfd645", store_path=self.store)
        evidence_dir = os.path.join(self.tempdir.name, "evidence")
        steps = []
        for index, label in enumerate(("我的", "打印记录"), 1):
            request = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store, now=time.time() + 3)[0]
            recording.save_recording_evidence("win-runner-01", {
                "request_id": request["request_id"], "session_id": session["id"], "step_id": "", "device_id": "ecbfd645",
                "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n" + label.encode()).decode(),
                "ui_xml": f'<hierarchy><node text="{label}" bounds="[0,0][100,100]" /></hierarchy>',
            }, store_path=self.store, evidence_dir=evidence_dir)
            steps.append(recording.append_recorded_action(
                session["id"], session["recording_token"],
                {"event_id":f"evt-{index}","type":"tap","point":{"x":20,"y":30},"device_id":"ecbfd645"},
                store_path=self.store, evidence_dir=evidence_dir,
            ))
        self.assertNotEqual(steps[0]["screenshot_sha256"], steps[1]["screenshot_sha256"])
        self.assertEqual([step["ui_node"]["text"] for step in steps], ["我的", "打印记录"])

    def test_sonic_physical_coordinates_are_scaled_to_the_adb_screenshot(self):
        session = self.create(runner_id="", device_id="")
        recording.bind_recording_device(session["id"], "admin", "win-runner-01", "ecbfd645", store_path=self.store)
        request = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store)[0]
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1080, 2400)
        evidence_dir = os.path.join(self.tempdir.name, "evidence")
        recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"], "step_id": "",
            "device_id": "ecbfd645", "content_base64": base64.b64encode(png).decode(),
            "coordinate_width": 1200, "coordinate_height": 2640, "ui_xml": "<hierarchy />",
        }, store_path=self.store, evidence_dir=evidence_dir)
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id":"evt-scale","type":"tap","point":{"x":1084,"y":2564},"device_id":"ecbfd645"},
            store_path=self.store, evidence_dir=evidence_dir,
        )
        self.assertEqual(step["raw_point"], {"x": 1084, "y": 2564})
        self.assertEqual(step["point"], {"x": 976, "y": 2331})
        self.assertEqual(step["coordinate_transform"], "1200x2640->1080x2400")

    def test_owner_can_adjust_and_reset_the_click_point_on_the_same_evidence(self):
        session = self.create(runner_id="", device_id="")
        recording.bind_recording_device(session["id"], "admin", "win-runner-01", "ecbfd645", store_path=self.store)
        request = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store)[0]
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1080, 2400)
        evidence_dir = os.path.join(self.tempdir.name, "evidence")
        recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"], "step_id": "",
            "device_id": "ecbfd645", "content_base64": base64.b64encode(png).decode(), "ui_xml": "<hierarchy />",
        }, store_path=self.store, evidence_dir=evidence_dir)
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id":"evt-adjust","type":"tap","point":{"x":100,"y":200},"device_id":"ecbfd645"},
            store_path=self.store, evidence_dir=evidence_dir,
        )
        adjusted = recording.update_recorded_step_point(
            session["id"], "admin", step["id"], {"x": 250, "y": 350}, store_path=self.store,
        )
        self.assertEqual(adjusted["steps"][0]["point"], {"x": 250, "y": 350})
        self.assertTrue(adjusted["steps"][0]["point_manually_adjusted"])
        reset = recording.update_recorded_step_point(
            session["id"], "admin", step["id"], reset=True, store_path=self.store,
        )
        self.assertEqual(reset["steps"][0]["point"], {"x": 100, "y": 200})
        self.assertFalse(reset["steps"][0]["point_manually_adjusted"])

    def test_cancelled_or_finished_session_can_be_deleted_with_its_evidence(self):
        session = self.create()
        evidence_dir = os.path.join(self.tempdir.name, "evidence")
        os.makedirs(os.path.join(evidence_dir, session["id"]), exist_ok=True)
        with open(os.path.join(evidence_dir, session["id"], "proof.png"), "wb") as handle:
            handle.write(b"proof")
        with self.assertRaisesRegex(ValueError, "先取消"):
            recording.delete_recording_session(session["id"], "admin", store_path=self.store, evidence_dir=evidence_dir)
        recording.cancel_recording_session(session["id"], "admin", store_path=self.store)
        with self.assertRaises(PermissionError):
            recording.delete_recording_session(session["id"], "other", store_path=self.store, evidence_dir=evidence_dir)
        deleted = recording.delete_recording_session(session["id"], "admin", store_path=self.store, evidence_dir=evidence_dir)
        self.assertEqual(deleted["status"], "cancelled")
        self.assertFalse(os.path.exists(os.path.join(evidence_dir, session["id"])))
        self.assertEqual(recording.list_recording_sessions("admin", store_path=self.store), [])


if __name__ == "__main__":
    unittest.main()
