import unittest
from unittest.mock import Mock, patch
from task_server import router

from task_server.router import _is_snapshot_capture_failure, _visible_snapshot_error


class RunnerStatusMergeTest(unittest.TestCase):
    def test_busy_capture_skip_is_not_reported_as_device_failure(self):
        self.assertFalse(_is_snapshot_capture_failure("设备正在执行平台任务，本次未采集新画面"))
        self.assertFalse(_is_snapshot_capture_failure("设备正在执行平台任务"))

    def test_timeout_and_runner_errors_are_reported_as_device_failure(self):
        self.assertTrue(_is_snapshot_capture_failure("设备状态采集超时，已保留上次画面"))
        self.assertTrue(_is_snapshot_capture_failure("ADB 截图失败"))

    def test_finished_job_hides_stale_busy_capture_message(self):
        message = "设备正在执行平台任务，本次未采集新画面"
        self.assertEqual(_visible_snapshot_error(message, platform_busy=False), "")
        self.assertEqual(_visible_snapshot_error(message, platform_busy=True), message)

    def test_ios_keeps_device_measurements_instead_of_sonic_defaults(self):
        device = {"device_id": "ios-phone", "platform": "ios", "usage_status": "preview", "battery_level": 100}
        handler = Mock()
        with patch.object(router, "_require_user_auth", return_value=False), \
             patch.object(router, "all_online_devices", return_value=[device]), \
             patch.object(router, "list_runners", return_value={}), \
             patch.object(router, "sonic_list_device_statuses", return_value={
                 "ios-phone": {"usage_status": "idle", "usage_label": "空闲", "battery_level": 0, "battery_temperature_c": 0.0}}):
            router._get_runners(handler, {})
        result = handler._json.call_args.args[0]["devices"][0]
        self.assertEqual(result["battery_level"], 100)
        self.assertNotIn("battery_temperature_c", result)
        self.assertEqual(result["usage_status"], "preview")

    def test_android_still_receives_sonic_measurements(self):
        device = {"device_id": "android-phone", "platform": "android", "usage_status": "idle", "battery_level": 75}
        handler = Mock()
        with patch.object(router, "_require_user_auth", return_value=False), \
             patch.object(router, "all_online_devices", return_value=[device]), \
             patch.object(router, "list_runners", return_value={}), \
             patch.object(router, "sonic_list_device_statuses", return_value={
                 "android-phone": {"usage_status": "idle", "usage_label": "空闲", "battery_level": 80, "battery_temperature_c": 31.2}}):
            router._get_runners(handler, {})
        result = handler._json.call_args.args[0]["devices"][0]
        self.assertEqual(result["battery_level"], 80)
        self.assertEqual(result["battery_temperature_c"], 31.2)


if __name__ == "__main__":
    unittest.main()
