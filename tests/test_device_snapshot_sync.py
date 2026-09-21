import base64
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from task_server.services import runner_service


PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


class DeviceSnapshotSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runners = Path(self.tmp.name) / "runners.json"
        self.snapshots = Path(self.tmp.name) / "snapshots"
        self.jobs = Path(self.tmp.name) / "jobs.json"
        self.patches = [
            patch.object(runner_service, "RUNNERS_FILE", str(self.runners)),
            patch.object(runner_service, "DEVICE_SNAPSHOT_DIR", str(self.snapshots)),
            patch.object(runner_service, "JOBS_FILE", str(self.jobs)),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        runner_service.save_runners({"r1": {
            "runner_id": "r1", "last_seen_ts": runner_service.time.time(),
            "devices": [{"device_id": "p1", "status": "online"}],
        }})

    def test_active_platform_job_marks_device_busy_and_skips_capture(self):
        runner_service.write_json_file(str(self.jobs), [{
            "job_id": "j1", "status": "running", "target_runner_id": "r1",
            "device_id": "p1", "task_name": "登录回归", "created_by": "wangwc",
        }])
        devices = runner_service.all_online_devices()
        self.assertEqual(devices[0]["usage_status"], "busy")
        self.assertEqual(devices[0]["active_job_name"], "登录回归")
        self.assertEqual(devices[0]["active_job_operator"], "wangwc")
        self.assertEqual(devices[0]["usage_label"], "wangwc · 平台任务执行中")
        self.assertEqual(runner_service.request_device_snapshots()["requested"], 0)
        self.assertIn("执行平台任务", runner_service.load_runners()["r1"]["devices"][0]["snapshot_error"])

    def test_refresh_request_is_returned_once_until_upload(self):
        result = runner_service.request_device_snapshots()
        self.assertEqual(result["requested"], 1)
        repeated = runner_service.request_device_snapshots()
        self.assertEqual(repeated["requests"][0]["request_id"], result["requests"][0]["request_id"])
        record = runner_service.register_runner({"runner_id": "r1", "devices": [{"device_id": "p1"}]})
        self.assertEqual(record["snapshot_requests"][0]["device_id"], "p1")

    def test_valid_snapshot_is_saved_and_merged_on_future_heartbeat(self):
        request = runner_service.request_device_snapshots()["requests"][0]
        saved = runner_service.save_device_snapshot("r1", {
            "device_id": "p1", "request_id": request["request_id"],
            "content_base64": base64.b64encode(PNG).decode(),
            "battery_level": 80, "battery_temperature_c": 31.2,
            "foreground_package": "com.kfb.model", "screen_on": True,
        })
        self.assertTrue(Path(saved["snapshot_path"]).read_bytes().startswith(b"\x89PNG"))
        record = runner_service.register_runner({"runner_id": "r1", "devices": [{"device_id": "p1"}]})
        device = record["devices"][0]
        self.assertEqual(device["battery_level"], 80)
        self.assertIn("/api/runner/device-snapshot", device["snapshot_url"])
        self.assertEqual(record["snapshot_requests"], [])

    def test_wrong_runner_invalid_image_and_oversize_are_rejected(self):
        request = runner_service.request_device_snapshots()["requests"][0]
        payload = {"device_id": "p1", "request_id": request["request_id"], "content_base64": base64.b64encode(PNG).decode()}
        with self.assertRaises(ValueError):
            runner_service.save_device_snapshot("other", payload)
        payload["content_base64"] = base64.b64encode(b"not-png").decode()
        with self.assertRaises(ValueError):
            runner_service.save_device_snapshot("r1", payload)
        with patch.object(runner_service, "DEVICE_SNAPSHOT_MAX_BYTES", 8):
            payload["content_base64"] = base64.b64encode(PNG).decode()
            with self.assertRaises(ValueError):
                runner_service.save_device_snapshot("r1", payload)

    def test_failed_capture_keeps_previous_image_and_records_error(self):
        request = runner_service.request_device_snapshots()["requests"][0]
        runner_service.save_device_snapshot("r1", {"device_id": "p1", "request_id": request["request_id"], "content_base64": base64.b64encode(PNG).decode()})
        before = next(self.snapshots.glob("*.png")).read_bytes()
        request = runner_service.request_device_snapshots()["requests"][0]
        runner_service.save_device_snapshot("r1", {"device_id": "p1", "request_id": request["request_id"], "error": "设备正在执行平台任务"})
        self.assertEqual(next(self.snapshots.glob("*.png")).read_bytes(), before)
        self.assertEqual(runner_service.load_runners()["r1"]["devices"][0]["snapshot_error"], "设备正在执行平台任务")

    def test_heartbeat_cannot_inject_snapshot_url(self):
        record = runner_service.register_runner({"runner_id": "r1", "devices": [{"device_id": "p1", "snapshot_url": "https://attacker.invalid/collect"}]})
        self.assertNotIn("snapshot_url", record["devices"][0])

    def test_job_started_after_request_prevents_snapshot_replacement(self):
        request = runner_service.request_device_snapshots()["requests"][0]
        runner_service.write_json_file(str(self.jobs), [{"job_id": "j1", "status": "running", "target_runner_id": "r1", "device_id": "p1"}])
        saved = runner_service.save_device_snapshot("r1", {"device_id": "p1", "request_id": request["request_id"], "content_base64": base64.b64encode(PNG).decode()})
        self.assertEqual(saved["snapshot_path"], "")
        self.assertFalse(list(self.snapshots.glob("*.png")))
        self.assertIn("执行平台任务", saved["device"]["snapshot_error"])

    def test_refresh_cancels_an_old_request_when_device_becomes_busy(self):
        runner_service.request_device_snapshots()
        runner_service.write_json_file(str(self.jobs), [{"job_id": "j1", "status": "pending", "target_runner_id": "r1", "device_id": "p1"}])
        self.assertEqual(runner_service.request_device_snapshots()["requested"], 0)
        self.assertEqual(runner_service.load_runners()["r1"]["snapshot_requests"], [])

    def test_heartbeat_drops_requests_for_missing_or_expired_devices(self):
        request = runner_service.request_device_snapshots()["requests"][0]
        missing = runner_service.register_runner({"runner_id": "r1", "devices": []})
        self.assertEqual(missing["snapshot_requests"], [])
        runner_service.save_runners({"r1": {"runner_id": "r1", "last_seen_ts": runner_service.time.time(), "devices": [{"device_id": "p1", "status": "online"}], "snapshot_requests": [{**request, "requested_ts": 1}]}})
        expired = runner_service.register_runner({"runner_id": "r1", "devices": [{"device_id": "p1", "status": "online"}]})
        self.assertEqual(expired["snapshot_requests"], [])
        self.assertIn("采集超时", expired["devices"][0]["snapshot_error"])

    def test_refresh_reuses_request_for_entire_ttl(self):
        request = runner_service.request_device_snapshots()["requests"][0]
        runners = runner_service.load_runners()
        runners["r1"]["snapshot_requests"][0]["requested_ts"] = runner_service.time.time() - 60
        runner_service.save_runners(runners)
        repeated = runner_service.request_device_snapshots()["requests"][0]
        self.assertEqual(repeated["request_id"], request["request_id"])


if __name__ == "__main__":
    unittest.main()
