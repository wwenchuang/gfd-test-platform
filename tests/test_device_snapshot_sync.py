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
        self.assertEqual(device["snapshot_app_package"], "com.kfb.model")
        self.assertTrue(device["snapshot_file"].endswith(".png"))
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

    def test_capture_provenance_only_changes_after_successful_upload(self):
        request = runner_service.request_device_snapshots()["requests"][0]
        runner_service.save_device_snapshot("r1", {"device_id":"p1","request_id":request["request_id"],"content_base64":base64.b64encode(PNG).decode(),"foreground_package":"com.first"})
        first = runner_service.load_runners()["r1"]["devices"][0]
        old_file = first["snapshot_file"]
        runner_service.register_runner({"runner_id":"r1","devices":[{"device_id":"p1","foreground_package":"com.other","snapshot_app_package":"com.forged","snapshot_file":"forged.png"}]})
        after_heartbeat = runner_service.load_runners()["r1"]["devices"][0]
        self.assertEqual(after_heartbeat["snapshot_app_package"],"com.first")
        self.assertEqual(after_heartbeat["snapshot_file"],old_file)
        request = runner_service.request_device_snapshots()["requests"][0]
        runner_service.save_device_snapshot("r1", {"device_id":"p1","request_id":request["request_id"],"error":"capture failed","foreground_package":"com.other"})
        after_failure = runner_service.load_runners()["r1"]["devices"][0]
        self.assertEqual(after_failure["snapshot_app_package"],"com.first")
        self.assertEqual(after_failure["snapshot_file"],old_file)
        request = runner_service.request_device_snapshots()["requests"][0]
        runner_service.save_device_snapshot("r1", {"device_id":"p1","request_id":request["request_id"],"content_base64":base64.b64encode(PNG).decode(),"foreground_package":"com.second"})
        latest = runner_service.load_runners()["r1"]["devices"][0]
        self.assertEqual(latest["snapshot_app_package"],"com.second")
        self.assertNotEqual(latest["snapshot_file"],old_file)
        self.assertEqual(len(list(self.snapshots.glob("*.png"))),1)

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

class IosPreviewDispatchTest(DeviceSnapshotSyncTest):
    def test_preview_device_cannot_claim_jobs(self):
        runner_service.register_runner({'runner_id':'r1','capabilities':{'job_execution':False},
            'devices':[{'device_id':'p1','platform':'ios','ios_version':'26.6','display_name':'iPhone 13'}]})
        runner_service.write_json_file(str(self.jobs), [{'job_id':'ios-j','status':'pending','target_runner_id':'r1','device_id':'p1'}])
        with patch('task_server.services.sonic_service.sonic_list_device_statuses',return_value={'__source__':{'available':True},'p1':{'usage_status':'idle'}}):
            self.assertIsNone(runner_service.assign_job('r1'))
        self.assertEqual(runner_service._load_jobs()[0]['status'],'pending')

    def test_preview_device_is_visible_without_claiming_execution_readiness(self):
        runner_service.register_runner({'runner_id':'r1','capabilities':{'job_execution':False},
            'devices':[{'device_id':'p1','platform':'ios','ios_version':'26.6','display_name':'iPhone 13'}]})
        row=runner_service.all_online_devices()[0]
        self.assertEqual(row['platform'],'ios')
        self.assertEqual(row['label'],'iPhone 13')
        self.assertEqual(row['usage_status'],'preview')
        self.assertEqual(runner_service.request_device_snapshots()['requested'],1)
