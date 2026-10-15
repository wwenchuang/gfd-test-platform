import base64
import os
import tempfile
import unittest
from unittest import mock

from task_server import router
from task_server.services import device_recording_service as recording


class Handler:
    def __init__(self, body=None):
        self.body = body or {}
        self.headers = {}
        self.responses = []

    def _body(self):
        return self.body

    def _json(self, payload, status=200):
        self.responses.append((status, payload))


class DeviceRecordingProtocolTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = os.path.join(self.tempdir.name, "recordings.json")
        self.evidence_dir = os.path.join(self.tempdir.name, "evidence")
        self.patches = [
            mock.patch.object(recording, "DEVICE_RECORDINGS_FILE", self.store),
            mock.patch.object(recording, "DEVICE_RECORDING_EVIDENCE_DIR", self.evidence_dir),
            mock.patch.object(router, "_require_user_auth", return_value=False),
            mock.patch.object(router, "_authenticated_user", return_value="admin"),
            mock.patch.object(router, "all_online_devices", return_value=[{
                "runner_id": "win-runner-01", "device_id": "ecbfd645", "runner_online": True, "status": "online", "platform": "android",
            }]),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def create(self):
        handler = Handler({
            "runner_id": "win-runner-01",
            "device_id": "ecbfd645",
            "app_package": "com.tencent.mm",
        })
        router._post_device_recordings(handler, {})
        self.assertEqual(handler.responses[0][0], 200)
        return handler.responses[0][1]["session"]

    def test_unsupported_platform_rejected_before_capture_for_all_entry_points(self):
        for metadata in ({"platform": "ios", "android_version": "14"}, {}, {"platform": "other"}):
            with self.subTest(metadata=metadata):
                row = {"runner_id": "win-runner-01", "device_id": "ecbfd645", "runner_online": True, "status": "online", **metadata}
                session = recording.create_recording_session("admin", "", "", "com.tencent.mm")
                with mock.patch.object(router, "all_online_devices", return_value=[row]):
                    for route in (router._post_device_recordings, router._post_device_recording_bind, router._post_device_recording_bridge):
                        handler = Handler({"runner_id": "win-runner-01", "device_id": "ecbfd645", "app_package": "com.tencent.mm", "session_id": session["id"], "recording_token": session["recording_token"]})
                        route(handler, {})
                        self.assertIn(handler.responses[0][0], (400, 409))
                self.assertEqual(recording.pending_recording_evidence_requests("win-runner-01"), [])
                self.assertEqual(recording.get_recording_session(session["id"])["device_id"], "")

    def test_supported_legacy_android_and_explicit_runner_selection(self):
        for metadata in ({"platform":"android"}, {"android_version":"14"}, {"adb_path":"/usr/bin/adb"}):
            with self.subTest(metadata=metadata):
                session = recording.create_recording_session("admin", "", "", "com.tencent.mm")
                rows = [{"runner_id":"wrong", "device_id":"phone", "runner_online":True, "status":"online", "platform":"ios"},
                        {"runner_id":"right", "device_id":"phone", "runner_online":True, "status":"online", **metadata}]
                with mock.patch.object(router, "all_online_devices", return_value=rows):
                    handler = Handler({"session_id":session["id"], "runner_id":"right", "device_id":"phone"})
                    router._post_device_recording_bind(handler, {})
                self.assertEqual(handler.responses[0][0], 200)
                self.assertEqual(handler.responses[0][1]["session"]["runner_id"], "right")
                recording.cancel_recording_session(session["id"], "admin")

    def test_requested_runner_never_falls_back(self):
        session = recording.create_recording_session("admin", "", "", "com.tencent.mm")
        for route in (router._post_device_recording_bind, router._post_device_recording_bridge):
            handler = Handler({"session_id":session["id"], "recording_token":session["recording_token"], "runner_id":"missing", "device_id":"ecbfd645"})
            route(handler, {})
            self.assertEqual(handler.responses[0][0], 409)
        self.assertEqual(recording.pending_recording_evidence_requests("win-runner-01"), [])

    def test_bridge_invalid_token_does_not_disclose_platform(self):
        session = recording.create_recording_session("admin", "", "", "com.tencent.mm")
        with mock.patch.object(router, "all_online_devices", return_value=[{"runner_id":"r", "device_id":"phone", "runner_online":True, "status":"online", "platform":"ios"}]):
            handler = Handler({"session_id":session["id"], "recording_token":"wrong", "device_id":"phone"})
            router._post_device_recording_bridge(handler, {})
        self.assertEqual(handler.responses[0][0], 401)
        self.assertNotIn("iOS", handler.responses[0][1]["error"])

    def test_create_read_action_finish_flow(self):
        session = self.create()
        self.assertIn("recording_token", session)
        request = recording.pending_recording_evidence_requests("win-runner-01")[0]
        recording.save_recording_evidence("win-runner-01", {
            "request_id": request["request_id"], "session_id": session["id"], "step_id": "",
            "device_id": "ecbfd645", "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode(),
            "ui_xml": "<hierarchy />",
        })
        action = Handler({
            "session_id": session["id"],
            "recording_token": session["recording_token"],
            "action": {
                "event_id": "evt-1", "type": "key", "key": "BACK",
                "device_id": "ecbfd645",
            },
        })
        router._post_device_recording_action(action, {})
        self.assertEqual(action.responses[0][1]["step"]["sequence"], 1)

        read = Handler()
        router._get_device_recordings(read, {"id": session["id"]})
        self.assertEqual(len(read.responses[0][1]["session"]["steps"]), 1)
        self.assertNotIn("recording_token_hash", read.responses[0][1]["session"])

        finish = Handler({"session_id": session["id"]})
        router._post_device_recording_finish(finish, {})
        self.assertEqual(finish.responses[0][1]["session"]["status"], "finished")

    def test_action_endpoint_rejects_invalid_token(self):
        session = self.create()
        action = Handler({
            "session_id": session["id"], "recording_token": "wrong",
            "action": {"event_id": "evt-1", "type": "tap", "point": {"x": 1, "y": 2}, "device_id": "ecbfd645"},
        })
        router._post_device_recording_action(action, {})
        self.assertEqual(action.responses[0][0], 401)

    def test_create_rejects_device_not_reported_by_runner(self):
        with mock.patch.object(router, "all_online_devices", return_value=[]):
            handler = Handler({"runner_id": "win-runner-01", "device_id": "missing", "app_package": "com.tencent.mm"})
            router._post_device_recordings(handler, {})
        self.assertEqual(handler.responses[0][0], 400)
        self.assertIn("未由该 Runner 在线上报", handler.responses[0][1]["error"])

    def test_recording_module_is_validated_and_persisted_for_history(self):
        apps = {"apps": [{"package": "com.tencent.mm", "enabled": True, "modules": ["微信登录"]}]}
        with mock.patch.object(router, "load_task_apps", return_value=apps):
            bad = Handler({"app_package": "com.tencent.mm", "module_name": "3D打印基线"})
            router._post_device_recordings(bad, {})
            self.assertEqual(bad.responses[0][0], 400)
            good = Handler({"app_package": "com.tencent.mm", "module_name": "微信登录"})
            router._post_device_recordings(good, {})
            session = good.responses[0][1]["session"]
            self.assertEqual(session["module_name"], "微信登录")
            wrong_module = Handler({"session_id": session["id"], "module_name": "3D打印基线"})
            router._post_device_recording_module(wrong_module, {})
            self.assertEqual(wrong_module.responses[0][0], 400)
            self.assertEqual(recording.get_recording_session(session["id"])["module_name"], "微信登录")
            update = Handler({"session_id": session["id"], "module_name": ""})
            router._post_device_recording_module(update, {})
            self.assertEqual(update.responses[0][1]["session"]["module_name"], "")
            with mock.patch.object(router, "_authenticated_user", return_value="other"):
                denied = Handler({"session_id": session["id"], "module_name": "微信登录"})
                router._post_device_recording_module(denied, {})
                self.assertEqual(denied.responses[0][0], 403)

    def test_history_pages_are_owner_scoped_summaries_and_details_remain_available(self):
        for i in range(32):
            recording.create_recording_session("admin", "", "", "com.tencent.mm", now=100+i)
        recording.create_recording_session("other", "", "", "com.tencent.mm", now=200)
        page = Handler()
        router._get_device_recordings(page, {"limit": "20", "offset": "0"})
        rows = page.responses[0][1]["sessions"]
        self.assertEqual(len(rows), 20)
        self.assertEqual(page.responses[0][1]["next_offset"], 20)
        self.assertNotIn("steps", rows[0])
        self.assertEqual(rows[0]["step_count"], 0)
        next_page = Handler()
        router._get_device_recordings(next_page, {"limit": "20", "offset": "20"})
        self.assertEqual(len(next_page.responses[0][1]["sessions"]), 12)
        self.assertIsNone(next_page.responses[0][1]["next_offset"])
        detail = Handler()
        router._get_device_recordings(detail, {"id": rows[0]["id"]})
        self.assertIn("steps", detail.responses[0][1]["session"])

    def test_sonic_actual_phone_binds_an_unbound_session_before_actions(self):
        create = Handler({"app_package": "com.tencent.mm"})
        router._post_device_recordings(create, {})
        self.assertEqual(create.responses[0][0], 200)
        session = create.responses[0][1]["session"]
        self.assertEqual(session["device_id"], "")

        bind = Handler({"session_id": session["id"], "device_id": "ecbfd645"})
        router._post_device_recording_bind(bind, {})
        self.assertEqual(bind.responses[0][0], 200)
        self.assertEqual(bind.responses[0][1]["session"]["runner_id"], "win-runner-01")
        self.assertEqual(bind.responses[0][1]["session"]["device_id"], "ecbfd645")


if __name__ == "__main__":
    unittest.main()
