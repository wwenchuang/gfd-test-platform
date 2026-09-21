import json
import os
import tempfile
import time
import unittest
import base64

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

    def test_owner_controls_finish_and_state_is_persisted_atomically(self):
        session = self.create()
        with self.assertRaisesRegex(PermissionError, "发起人"):
            recording.finish_recording_session(session["id"], "another", store_path=self.store)
        recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-finish", "type": "key", "key": "BACK", "device_id": "ecbfd645"},
            store_path=self.store,
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
        first = recording.append_recorded_action(
            session["id"], token,
            {"event_id": "evt-1", "type": "tap", "point": {"x": 120, "y": 240}, "device_id": "ecbfd645"},
            store_path=self.store,
        )
        second = recording.append_recorded_action(
            session["id"], token,
            {"event_id": "evt-2", "type": "key", "key": "BACK", "device_id": "ecbfd645"},
            store_path=self.store,
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
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-1", "type": "tap", "point": {"x": 50, "y": 60}, "device_id": "ecbfd645"},
            store_path=self.store,
        )
        pending = recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store)
        self.assertEqual(pending[0]["step_id"], step["id"])
        xml = '<hierarchy><node text="外层" bounds="[0,0][200,200]"><node text="确定" resource-id="com.demo:id/ok" bounds="[20,30][100,90]" /></node></hierarchy>'
        evidence_dir = os.path.join(self.tempdir.name, "evidence")
        saved = recording.save_recording_evidence("win-runner-01", {
            "session_id": session["id"], "step_id": step["id"], "device_id": "ecbfd645",
            "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode(), "ui_xml": xml,
        }, store_path=self.store, evidence_dir=evidence_dir)
        self.assertEqual(saved["steps"][0]["ui_node"]["text"], "确定")
        self.assertEqual(recording.pending_recording_evidence_requests("win-runner-01", store_path=self.store), [])
        again = recording.save_recording_evidence("win-runner-01", {
            "session_id": session["id"], "step_id": step["id"], "device_id": "ecbfd645",
        }, store_path=self.store, evidence_dir=evidence_dir)
        self.assertEqual(again["steps"][0]["evidence_status"], "captured")

    def test_owner_can_confirm_semantic_target_for_ambiguous_step(self):
        session = self.create()
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-1", "type": "tap", "point": {"x": 1, "y": 2}, "device_id": "ecbfd645"},
            store_path=self.store,
        )
        with self.assertRaisesRegex(PermissionError, "发起人"):
            recording.update_recorded_step(session["id"], "another", step["id"], "提交按钮", store_path=self.store)
        updated = recording.update_recorded_step(session["id"], "admin", step["id"], "提交按钮", store_path=self.store)
        self.assertEqual(updated["steps"][0]["semantic_description"], "提交按钮")

    def test_owner_can_edit_description_and_delete_recorded_step(self):
        session = self.create()
        step = recording.append_recorded_action(
            session["id"], session["recording_token"],
            {"event_id": "evt-1", "type": "key", "key": "BACK", "device_id": "ecbfd645"},
            store_path=self.store,
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

    def test_unbound_session_uses_the_phone_actually_opened_in_sonic(self):
        session = self.create(runner_id="", device_id="")
        second = self.create(user="another", runner_id="", device_id="")
        self.assertNotEqual(session["id"], second["id"])
        self.assertEqual(session["device_id"], "")
        bound = recording.bind_recording_device(
            session["id"], "admin", "win-runner-01", "ecbfd645", store_path=self.store
        )
        self.assertEqual((bound["runner_id"], bound["device_id"]), ("win-runner-01", "ecbfd645"))
        with self.assertRaisesRegex(ValueError, "已经绑定"):
            recording.bind_recording_device(
                session["id"], "admin", "win-runner-01", "other-phone", store_path=self.store
            )


if __name__ == "__main__":
    unittest.main()
