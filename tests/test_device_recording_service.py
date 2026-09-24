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
        captured_at = time.time() if now is None else now
        if now is None:
            now = time.time() + 3
        request = recording.pending_recording_evidence_requests(
            "win-runner-01", store_path=self.store, now=now
        )[0]
        return recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"], "step_id": "",
            "device_id": "ecbfd645", "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode(),
            "ui_xml": xml, "ui_xml_error": ui_xml_error,
        }, store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"), now=captured_at)

    def test_background_recognition_is_nonblocking_deduplicated_and_releases_slot(self):
        import threading
        from unittest.mock import patch
        entered, release = threading.Event(), threading.Event()
        session = self.create()
        self.prepare_frame(session)
        recording.append_recorded_action(session["id"], session["recording_token"], {
            "event_id": "background", "type": "tap", "device_id": "ecbfd645", "point": {"x": 10, "y": 10}
        }, store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"))
        snapshot = recording.get_recording_session(session["id"], store_path=self.store)
        def slow_model(*args, **kwargs):
            entered.set()
            release.wait(2)
        with patch.object(recording, "recognize_recording_semantics", side_effect=slow_model) as recognize:
            worker = recording.schedule_recording_recognition(snapshot, store_path=self.store)
            self.assertIsNotNone(worker)
            self.assertTrue(entered.wait(1))
            self.assertIsNone(recording.schedule_recording_recognition(snapshot, store_path=self.store))
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(recognize.call_count, 1)
        snapshot["status"] = "cancelled"
        self.assertIsNone(recording.schedule_recording_recognition(snapshot, store_path=self.store))

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

    def test_next_phone_image_warns_when_webview_has_no_ui_xml_and_screen_is_unchanged(self):
        from PIL import Image

        def frame(color):
            output = io.BytesIO()
            Image.new("RGB", (108, 241), color).save(output, format="PNG")
            return base64.b64encode(output.getvalue()).decode()

        session = self.create()
        request = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store)[0]
        recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"],
            "device_id": "ecbfd645", "content_base64": frame("white"),
            "ui_xml_error": "WebView UI XML unavailable",
        }, store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"))
        recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-visual-unchanged", "type": "tap", "point": {"x": 50, "y": 60}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"),
        )
        request = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store, now=time.time() + 3)[0]
        after = recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"],
            "device_id": "ecbfd645", "content_base64": frame("white"),
            "ui_xml_error": "WebView UI XML unavailable",
        }, store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"))
        self.assertEqual(after["steps"][0]["screen_change_status"], "unchanged")
        self.assertIn("画面几乎未变化", after["steps"][0]["evidence_warning"])

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
            closeup = Image.open(io.BytesIO(base64.b64decode(kwargs["image_assets"][0]["base64"])))
            self.assertEqual(closeup.size, (1208, 1208))
            self.assertNotEqual(closeup.getpixel((460, 880)), (255, 255, 255))
            return '{"semantic_description":"底部导航首页","confidence":0.9}'

        recognized = recording.recognize_recording_semantics(
            session["id"], "admin", store_path=self.store, model_call=model_call,
        )
        self.assertEqual(recognized["steps"][0]["semantic_description"], "底部导航首页")

    def test_small_video_frame_includes_unmarked_context_for_foreground_dialogs(self):
        from PIL import Image
        image = Image.new("RGB", (358, 800), "white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        with tempfile.NamedTemporaryFile(suffix=".png") as file:
            file.write(output.getvalue())
            file.flush()
            assets = recording._visual_image_assets(file.name, {"x": 323, "y": 764})
        self.assertEqual(len(assets), 2)
        self.assertEqual(assets[0]["name"], "tap-target-closeup.png")
        closeup = Image.open(io.BytesIO(base64.b64decode(assets[0]["base64"])))
        self.assertEqual(closeup.size, (400, 400))
        self.assertNotEqual(closeup.getpixel((260, 256)), (255, 255, 255))

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

    def test_late_visual_result_cannot_overwrite_manual_label_or_corrected_point(self):
        for correction in ('label', 'point', 'newer_recognition'):
            with self.subTest(correction=correction):
                session = self.create(device_id='ecbfd645', user=correction)
                self.prepare_frame(session)
                step = recording.append_recorded_action(
                    session['id'], session['recording_token'],
                    {'event_id':'race', 'type':'tap', 'point':{'x':1,'y':2}, 'device_id':'ecbfd645'},
                    store_path=self.store, evidence_dir=os.path.join(self.tempdir.name,'evidence'))
                # Valid dimensions for coordinate correction; image rendering is not under test.
                with open(step['screenshot_path'], 'wb') as handle:
                    handle.write(b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + struct.pack('>II',1080,2400))
                def late_model(*args, **kwargs):
                    if correction == 'label':
                        recording.update_recorded_step(session['id'], correction, step['id'], '左上角返回', store_path=self.store)
                    elif correction == 'point':
                        recording.update_recorded_step_point(session['id'], correction, step['id'], {'x':20,'y':30}, store_path=self.store)
                    else:
                        recording.recognize_recording_semantics(session['id'], correction, store_path=self.store,
                            force=True, model_call=lambda *a, **k: '{"semantic_description":"新的识别","confidence":0.9}')
                    return '{"semantic_description":"过期的我的","confidence":0.95}'
                result = recording.recognize_recording_semantics(session['id'], correction, store_path=self.store, model_call=late_model)
                actual=result['steps'][0]
                if correction == 'point':
                    self.assertEqual(actual['point'], {'x':20,'y':30})
                    self.assertNotIn('semantic_description', actual)
                    self.assertEqual(actual['semantic_recognition_status'], 'pending')
                else:
                    self.assertEqual(actual['semantic_description'], '左上角返回' if correction == 'label' else '新的识别')
                recording.cancel_recording_session(session['id'], correction, store_path=self.store)

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
        recording.save_generated_recording_result(session['id'], 'admin', {'yaml':'old'}, store_path=self.store)
        updated = recording.update_recorded_step(
            session["id"], "admin", step["id"], "返回上一页", store_path=self.store
        )
        self.assertEqual(updated["steps"][0]["semantic_description"], "返回上一页")
        self.assertNotIn('generated_result', updated)
        self.assertEqual(updated['steps'][0]['semantic_source'], 'manual')
        recording.save_generated_recording_result(session['id'], 'admin', {'yaml':'old'}, store_path=self.store)
        with self.assertRaisesRegex(PermissionError, "发起人"):
            recording.delete_recorded_step(session["id"], "another", step["id"], store_path=self.store)
        deleted = recording.delete_recorded_step(session["id"], "admin", step["id"], store_path=self.store)
        self.assertEqual(deleted["steps"], [])
        self.assertNotIn('generated_result', deleted)

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

    def test_sonic_touch_coordinates_scale_to_live_video_frame(self):
        session = self.create()
        request = recording.pending_recording_evidence_requests(
            "win-runner-01", store_path=self.store, now=time.time() + 3
        )[0]
        def png(width, height):
            return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height) + b"frame"
        evidence_dir = os.path.join(self.tempdir.name, "evidence")
        recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"], "step_id": "",
            "device_id": "ecbfd645", "content_base64": base64.b64encode(png(1080, 2412)).decode(),
        }, store_path=self.store, evidence_dir=evidence_dir)
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-scaled", "type": "tap", "point": {"x": 65, "y": 154},
             "device_id": "ecbfd645", "evidence_content_base64": base64.b64encode(png(358, 800)).decode()},
            store_path=self.store, evidence_dir=evidence_dir,
        )
        self.assertEqual(step["point"], {"x": 22, "y": 51})
        self.assertEqual(step["raw_point"], {"x": 65, "y": 154})
        self.assertEqual(step["coordinate_transform"], "1080x2412->358x800")

    def test_stale_runner_frame_cannot_name_a_later_tap(self):
        session = self.create()
        self.prepare_frame(session, xml='<hierarchy><node text="版本号" bounds="[0,0][100,100]" /></hierarchy>')
        with self.assertRaisesRegex(ValueError, "过期"):
            recording.append_recorded_action(
                session["id"], session["recording_token"],
                {"event_id": "evt-stale", "type": "tap", "point": {"x": 50, "y": 50}, "device_id": "ecbfd645"},
                store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"), now=time.time() + 70,
            )
        self.assertEqual(recording.get_recording_session(session["id"], store_path=self.store)["steps"], [])

    def test_runner_frame_remains_valid_while_user_switches_to_sonic_tab(self):
        session = self.create()
        self.prepare_frame(session, xml='<hierarchy><node text="我的" bounds="[0,0][100,100]" /></hierarchy>')
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-after-tab-switch", "type": "tap", "point": {"x": 50, "y": 50}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"), now=time.time() + 10,
        )
        self.assertEqual(step["evidence_status"], "captured")
        self.assertEqual(step["evidence_source"], "runner_cached_frame")
        self.assertEqual(step["ui_node"]["text"], "我的")

    def test_idle_bridge_refreshes_runner_frame_before_it_expires(self):
        session = self.create()
        self.prepare_frame(session, xml='<hierarchy><node text="打印记录" bounds="[0,0][100,100]" /></hierarchy>')
        before = recording.get_recording_session(session["id"], store_path=self.store)
        refreshed = recording.bridge_recording_device(
            session["id"], session["recording_token"], "win-runner-01", "ecbfd645",
            store_path=self.store, now=time.time() + 50,
        )
        self.assertEqual(refreshed["pre_action_frame_status"], "ready")
        self.assertNotEqual(refreshed["pre_action_frame_request_id"], before["pre_action_frame_request_id"])
        requests = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store)
        self.assertEqual(requests[0]["request_id"], refreshed["pre_action_frame_request_id"])
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-during-refresh", "type": "tap", "point": {"x": 50, "y": 50}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"), now=time.time() + 51,
        )
        self.assertEqual(step["ui_node"]["text"], "打印记录")
        self.assertEqual(step["evidence_status"], "captured")

    def test_idle_frame_expiry_waits_for_refresh_without_reusing_old_evidence(self):
        session = self.create()
        self.prepare_frame(session)
        refreshing = recording.bridge_recording_device(
            session["id"], session["recording_token"], "win-runner-01", "ecbfd645",
            store_path=self.store, now=time.time() + 50,
        )
        expired = recording.bridge_recording_device(
            session["id"], session["recording_token"], "win-runner-01", "ecbfd645",
            store_path=self.store, now=time.time() + 61,
        )
        self.assertEqual(expired["pre_action_frame_status"], "pending")
        self.assertEqual(expired["pre_action_frame_request_id"], refreshing["pre_action_frame_request_id"])
        with self.assertRaisesRegex(ValueError, "尚未准备"):
            recording.append_recorded_action(
                session["id"], session["recording_token"],
                {"event_id": "evt-expired", "type": "tap", "point": {"x": 50, "y": 50}, "device_id": "ecbfd645"},
                store_path=self.store, now=time.time() + 61,
            )
        self.prepare_frame(session)
        self.assertEqual(recording.get_recording_session(session["id"], store_path=self.store)["pre_action_frame_status"], "ready")

    def test_failed_idle_refresh_does_not_keep_claiming_ready(self):
        session = self.create()
        self.prepare_frame(session)
        refreshing = recording.bridge_recording_device(
            session["id"], session["recording_token"], "win-runner-01", "ecbfd645",
            store_path=self.store, now=time.time() + 50,
        )
        failed = recording.save_recording_evidence("win-runner-01", {
            "request_id": refreshing["pre_action_frame_request_id"], "session_id": session["id"],
            "step_id": "", "device_id": "ecbfd645", "error": "ADB 截图超时",
        }, store_path=self.store)
        self.assertEqual(failed["pre_action_frame_status"], "failed")
        self.assertFalse(failed["pre_action_frame_refresh_pending"])

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
            }, store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"), now=100 + (attempt - 1) * 4)
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

    def test_pre_action_request_times_out_at_120_seconds_and_rejects_late_capture(self):
        session = self.create(runner_id="", device_id="")
        bound = recording.bind_recording_device(
            session["id"], "admin", "win-runner-01", "ecbfd645", store_path=self.store, now=100,
        )
        self.assertEqual(bound["pre_action_frame_stage"], "waiting_runner")
        self.assertEqual(recording.pending_recording_evidence_requests(
            "win-runner-01", store_path=self.store, now=219,
        )[0]["request_id"], bound["pre_action_frame_request_id"])
        self.assertEqual(recording.pending_recording_evidence_requests(
            "win-runner-01", store_path=self.store, now=220,
        ), [])
        timed_out = recording.get_recording_session(session["id"], store_path=self.store, now=220)
        self.assertEqual((timed_out["pre_action_frame_status"], timed_out["pre_action_frame_stage"]), ("failed", "failed"))
        self.assertIn("120", timed_out["pre_action_frame_error"])
        late = recording.save_recording_evidence("win-runner-01", {
            "request_id": bound["pre_action_frame_request_id"], "session_id": session["id"],
            "step_id": "", "device_id": "ecbfd645",
            "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nlate").decode(),
        }, store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"), now=221)
        self.assertEqual(late["pre_action_frame_status"], "failed")
        retry = recording.bridge_recording_device(
            session["id"], session["recording_token"], "win-runner-01", "ecbfd645",
            refresh_evidence=True, store_path=self.store, now=222,
        )
        self.assertEqual(retry["pre_action_frame_status"], "pending")
        self.assertNotEqual(retry["pre_action_frame_request_id"], bound["pre_action_frame_request_id"])

    def test_runner_capture_ack_sets_real_stage_without_replacing_request(self):
        session = self.create()
        request = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store)[0]
        ack = recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"],
            "step_id": "", "device_id": "ecbfd645", "phase": "capturing",
        }, store_path=self.store)
        self.assertEqual(ack["pre_action_frame_stage"], "capturing")
        self.assertGreater(ack["pre_action_frame_picked_up_ts"], ack["pre_action_frame_requested_ts"])
        self.assertEqual(recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store), [])
        ready = recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"],
            "step_id": "", "device_id": "ecbfd645",
            "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nready").decode(),
        }, store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"))
        self.assertEqual((ready["pre_action_frame_status"], ready["pre_action_frame_stage"]), ("ready", "ready"))

    def test_idle_refresh_ack_updates_stage_without_discarding_valid_frame(self):
        session = self.create()
        ready = self.prepare_frame(session)
        refreshed_at = ready["pre_action_frame_captured_ts"] + 50
        refreshing = recording.bridge_recording_device(
            session["id"], session["recording_token"], "win-runner-01", "ecbfd645",
            store_path=self.store, now=refreshed_at,
        )
        self.assertEqual(refreshing["pre_action_frame_status"], "ready")
        ack = recording.save_recording_evidence("win-runner-01", {
            "request_id": refreshing["pre_action_frame_request_id"], "session_id": session["id"],
            "step_id": "", "device_id": "ecbfd645", "phase": "capturing",
        }, store_path=self.store, now=refreshed_at + 1)
        self.assertEqual(ack["pre_action_frame_status"], "ready")
        self.assertEqual(ack["pre_action_frame_stage"], "capturing")
        self.assertEqual(recording.pending_recording_evidence_requests(
            "win-runner-01", store_path=self.store, now=refreshed_at + 1,
        ), [])

    def test_finished_session_ignores_late_pre_action_frame(self):
        session = self.create(runner_id="", device_id="")
        bound = recording.bind_recording_device(
            session["id"], "admin", "win-runner-01", "ecbfd645", store_path=self.store,
        )
        recording.cancel_recording_session(session["id"], "admin", store_path=self.store)
        late = recording.save_recording_evidence("win-runner-01", {
            "request_id": bound["pre_action_frame_request_id"], "session_id": session["id"],
            "step_id": "", "device_id": "ecbfd645",
            "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nlate").decode(),
        }, store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, "evidence"))
        self.assertEqual(late["status"], "cancelled")
        self.assertEqual(late["pre_action_frame_status"], "pending")

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

    def test_scaled_screenshot_point_uses_raw_coordinates_for_ui_xml(self):
        session = self.create()
        request = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store)[0]
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 358, 800)
        evidence_dir = os.path.join(self.tempdir.name, "evidence")
        xml = ('<hierarchy><node text="AI建模" bounds="[200,600][500,900]" />'
               '<node text="我的" bounds="[900,2200][1079,2411]" /></hierarchy>')
        recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"], "step_id": "",
            "device_id": "ecbfd645", "content_base64": base64.b64encode(png).decode(),
            "coordinate_width": 1080, "coordinate_height": 2412, "ui_xml": xml,
        }, store_path=self.store, evidence_dir=evidence_dir)
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-my", "type": "tap", "point": {"x": 974, "y": 2304}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=evidence_dir,
        )
        self.assertEqual(step["point"], {"x": 323, "y": 764})
        self.assertEqual(step["ui_node"]["text"], "我的")
        adjusted = recording.update_recorded_step_point(
            session["id"], "admin", step["id"], {"x": 321, "y": 774}, store_path=self.store,
        )
        self.assertEqual(adjusted["steps"][0]["ui_node"]["text"], "我的")

    def test_xml_background_text_and_resource_id_require_visual_confirmation(self):
        for node, label in [
            ('text="2026-09-21 18:48:09"', '删除确认弹窗的确定按钮'),
            ('resource-id="com.kfb.model:id/app_home_icon_container"', '底部导航首页'),
        ]:
            with self.subTest(node=node):
                session = self.create()
                self.prepare_frame(session, xml=f'<hierarchy><node {node} bounds="[0,0][200,200]" /></hierarchy>')
                step = recording.append_recorded_action(
                    session['id'], session['recording_token'],
                    {'event_id': 'target', 'type': 'tap', 'point': {'x': 50, 'y': 60}, 'device_id': 'ecbfd645'},
                    store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, 'evidence'))
                calls = []
                def recognize(*args, **kwargs):
                    calls.append(kwargs)
                    return json.dumps({'semantic_description': label, 'confidence': 0.95})
                result = recording.recognize_recording_semantics(session['id'], 'admin', store_path=self.store, model_call=recognize)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result['steps'][0]['semantic_description'], label)
                recording.finish_recording_session(session['id'], 'admin', store_path=self.store)

    def test_failed_visual_recognition_does_not_fall_back_to_xml_or_retry_forever(self):
        session = self.create()
        self.prepare_frame(session, xml='<hierarchy><node text="背景日期" bounds="[0,0][200,200]" /></hierarchy>')
        step = recording.append_recorded_action(session['id'], session['recording_token'],
            {'event_id': 'target', 'type': 'tap', 'point': {'x': 50, 'y': 60}, 'device_id': 'ecbfd645'},
            store_path=self.store, evidence_dir=os.path.join(self.tempdir.name, 'evidence'))
        calls = []
        def unavailable(*args, **kwargs):
            calls.append(True)
            raise TimeoutError('model timeout')
        result = recording.recognize_recording_semantics(session['id'], 'admin', store_path=self.store, model_call=unavailable)
        self.assertEqual(result['steps'][0]['semantic_recognition_status'], 'failed')
        self.assertEqual(recording.confirmed_recording_description(result['steps'][0]), '')
        recording.recognize_recording_semantics(session['id'], 'admin', store_path=self.store, model_call=unavailable)
        self.assertEqual(len(calls), 1)
        recording.update_recorded_step(session['id'], 'admin', step['id'], '弹窗确认', store_path=self.store)
        recording.recognize_recording_semantics(session['id'], 'admin', store_path=self.store, model_call=unavailable)
        self.assertEqual(len(calls), 1)

    def test_forced_recognition_rechecks_xml_after_click_point_correction(self):
        session = self.create()
        request = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store)[0]
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 358, 800)
        evidence_dir = os.path.join(self.tempdir.name, "evidence")
        recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"], "step_id": "",
            "device_id": "ecbfd645", "content_base64": base64.b64encode(png).decode(),
            "coordinate_width": 1080, "coordinate_height": 2412,
            "ui_xml": '<hierarchy><node text="我的" bounds="[900,2200][1079,2411]" /></hierarchy>',
        }, store_path=self.store, evidence_dir=evidence_dir)
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-recheck", "type": "tap", "point": {"x": 974, "y": 2304}, "device_id": "ecbfd645"},
            store_path=self.store, evidence_dir=evidence_dir,
        )
        called = []
        result = recording.recognize_recording_semantics(
            session["id"], "admin", store_path=self.store, step_id=step["id"], force=True,
            model_call=lambda *_args, **_kwargs: (called.append(True) or '{"semantic_description":"我的","confidence":0.95}'),
        )
        self.assertEqual(result["steps"][0]["semantic_description"], "我的")
        self.assertEqual(result["steps"][0]["semantic_source"], "ai_visual")
        self.assertEqual(called, [True])

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
