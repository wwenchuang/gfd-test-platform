import importlib.util
import pathlib
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("windows_runner_recording", pathlib.Path(__file__).parents[1] / "windows-midscene-runner.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class Result:
    def __init__(self, stdout=b"", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


class WindowsRecordingEvidenceTest(unittest.TestCase):
    def test_capture_ui_xml_is_read_only_except_device_temp_dump(self):
        calls = []
        def fake_run(command, **kwargs):
            calls.append(command)
            if "cat" in command:
                return Result(b'<hierarchy><node text="ok" bounds="[0,0][1,1]" /></hierarchy>')
            return Result()
        value = runner.capture_ui_xml("adb", "ecbfd645", run=fake_run)
        self.assertIn("<hierarchy", value)
        self.assertEqual(calls[0][-3:], ["uiautomator", "dump", "/sdcard/midscene-recording-window.xml"])
        self.assertIn("cat", calls[1])

    def test_uploader_rejects_device_not_present_in_runner_adb_list(self):
        runner.COMPLETED_RECORDING_EVIDENCE.clear()
        response = {"recording_evidence_requests": [{"request_id": "r1", "session_id": "s1", "step_id": "p1", "device_id": "other"}]}
        posted = []
        with mock.patch.object(runner, "resolve_adb_with_devices", return_value=("adb", [])), \
             mock.patch.object(runner, "http_json", side_effect=lambda method, path, payload, timeout=0: posted.append(payload) or {"ok": True}), \
             mock.patch.object(runner, "capture_screen_png") as capture:
            runner.upload_recording_evidence_requests(response, [])
        capture.assert_not_called()
        self.assertIn("离线", posted[0]["error"])

    def test_uploader_keeps_screenshot_when_ui_xml_is_unavailable(self):
        runner.COMPLETED_RECORDING_EVIDENCE.clear()
        response = {"recording_evidence_requests": [{"request_id": "r2", "session_id": "s1", "step_id": "p2", "device_id": "phone"}]}
        posted = []
        with mock.patch.object(runner, "resolve_adb_with_devices", return_value=("adb", ["phone"])), \
             mock.patch.object(runner, "capture_screen_png", return_value=b"\x89PNG\r\n\x1a\nfixture"), \
             mock.patch.object(runner, "capture_ui_xml", side_effect=RuntimeError("ADB 页面结构采集失败")), \
             mock.patch.object(runner, "http_json", side_effect=lambda method, path, payload, timeout=0: posted.append(payload) or {"ok": True}):
            runner.upload_recording_evidence_requests(response, [{"device_id": "phone"}])
        self.assertTrue(posted[0]["content_base64"])
        self.assertEqual(posted[0]["ui_xml_error"], "ADB 页面结构采集失败")
        self.assertNotIn("error", posted[0])

    def test_uploader_supports_a_pre_action_frame_without_a_step_id(self):
        runner.COMPLETED_RECORDING_EVIDENCE.clear()
        response = {"recording_evidence_requests": [{
            "request_id": "pre-1", "kind": "pre_action_frame",
            "session_id": "s1", "step_id": "", "device_id": "phone",
        }]}
        posted = []
        with mock.patch.object(runner, "resolve_adb_with_devices", return_value=("adb", ["phone"])), \
             mock.patch.object(runner, "capture_screen_png", return_value=b"\x89PNG\r\n\x1a\npre"), \
             mock.patch.object(runner, "capture_ui_xml", return_value="<hierarchy />"), \
             mock.patch.object(runner, "http_json", side_effect=lambda method, path, payload, timeout=0: posted.append(payload) or {"ok": True}):
            runner.upload_recording_evidence_requests(response, [{"device_id": "phone", "resolution": "Physical size: 1200x2640\nOverride size: 1080x2400"}])
        self.assertEqual(posted[0]["request_id"], "pre-1")
        self.assertEqual(posted[0]["step_id"], "")
        self.assertEqual(posted[0]["phase"], "capturing")
        self.assertTrue(posted[1]["content_base64"])
        self.assertEqual((posted[1]["coordinate_width"], posted[1]["coordinate_height"]), (1200, 2640))

    def test_recording_capture_is_processed_before_ordinary_snapshot(self):
        order = []
        with mock.patch.object(runner, "upload_recording_evidence_requests", side_effect=lambda *_: order.append("recording")), \
             mock.patch.object(runner, "upload_snapshot_requests", side_effect=lambda *_: order.append("snapshot")):
            runner.process_heartbeat_requests({}, [])
        self.assertEqual(order, ["recording", "snapshot"])

    def test_device_details_are_cached_but_online_ids_are_checked_each_round(self):
        runner._DEVICE_DETAILS_CACHE.clear()
        with mock.patch.object(runner, "detect_device_ids", side_effect=[["phone"], ["phone"], []]) as online, \
             mock.patch.object(runner, "resolve_adb_with_devices", return_value=("adb", ["phone"])), \
             mock.patch.object(runner, "adb_shell_text", return_value="example") as details, \
             mock.patch.object(runner, "detect_package_info", return_value={"installed": True}), \
             mock.patch.object(runner, "device_market_name", return_value="Phone"), \
             mock.patch.object(runner.time, "time", side_effect=[100, 101, 102]):
            first = runner.detect_devices()
            count = details.call_count
            second = runner.detect_devices()
            third = runner.detect_devices()
        self.assertEqual(online.call_count, 3)
        self.assertEqual(details.call_count, count)
        self.assertEqual(first[0]["device_id"], second[0]["device_id"])
        self.assertEqual(third, [])

    def test_configured_phone_still_requires_live_adb_presence_without_fallback(self):
        with mock.patch.dict(runner.os.environ, {"DEVICE_ID": "phone", "ANDROID_DEVICE_ID": "", "ANDROID_SERIAL": ""}), \
             mock.patch.object(runner, "resolve_adb_with_devices", return_value=("adb", ["other-phone"])):
            with self.assertRaisesRegex(RuntimeError, "phone"):
                runner.detect_device_ids()

    def test_enables_android_touch_and_pointer_overlays_for_recording(self):
        calls = []
        errors = runner.enable_recording_touch_indicators(
            "adb", "phone", run=lambda command, **kwargs: calls.append(command) or Result()
        )
        self.assertEqual(errors, [])
        self.assertEqual(calls[0][-5:], ["settings", "put", "system", "show_touches", "1"])
        self.assertEqual(calls[1][-5:], ["settings", "put", "system", "pointer_location", "1"])

    def test_uploader_enables_touch_indicators_only_once_per_device(self):
        runner.COMPLETED_RECORDING_EVIDENCE.clear()
        runner.ENABLED_RECORDING_TOUCH_INDICATORS.clear()
        device = [{"device_id": "phone"}]
        def batch(request_id):
            return {"recording_evidence_requests": [{"request_id": request_id, "session_id": "s1", "step_id": "", "device_id": "phone"}]}
        with mock.patch.object(runner, "resolve_adb_with_devices", return_value=("adb", ["phone"])), \
             mock.patch.object(runner, "enable_recording_touch_indicators", return_value=[]) as indicators, \
             mock.patch.object(runner, "capture_screen_png", return_value=b"\x89PNG\r\n\x1a\nfixture"), \
             mock.patch.object(runner, "capture_ui_xml", return_value="<hierarchy />"), \
             mock.patch.object(runner, "http_json", return_value={"ok": True}):
            runner.upload_recording_evidence_requests(batch("r-first"), device)
            runner.upload_recording_evidence_requests(batch("r-second"), device)
        self.assertEqual(indicators.call_count, 1)


if __name__ == "__main__":
    unittest.main()
