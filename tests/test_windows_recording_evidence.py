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


if __name__ == "__main__":
    unittest.main()
