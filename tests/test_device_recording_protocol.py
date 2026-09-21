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
        self.patches = [
            mock.patch.object(recording, "DEVICE_RECORDINGS_FILE", self.store),
            mock.patch.object(router, "_require_user_auth", return_value=False),
            mock.patch.object(router, "_authenticated_user", return_value="admin"),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def create(self):
        handler = Handler({
            "runner_id": "win-runner-01",
            "device_id": recording.FIXED_RECORDING_DEVICE_ID,
            "app_package": "com.tencent.mm",
        })
        router._post_device_recordings(handler, {})
        self.assertEqual(handler.responses[0][0], 200)
        return handler.responses[0][1]["session"]

    def test_create_read_action_finish_flow(self):
        session = self.create()
        self.assertIn("recording_token", session)
        action = Handler({
            "session_id": session["id"],
            "recording_token": session["recording_token"],
            "action": {
                "event_id": "evt-1", "type": "key", "key": "BACK",
                "device_id": recording.FIXED_RECORDING_DEVICE_ID,
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
            "action": {"event_id": "evt-1", "type": "tap", "point": {"x": 1, "y": 2}, "device_id": recording.FIXED_RECORDING_DEVICE_ID},
        })
        router._post_device_recording_action(action, {})
        self.assertEqual(action.responses[0][0], 401)


if __name__ == "__main__":
    unittest.main()
