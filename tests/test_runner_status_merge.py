import unittest

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


if __name__ == "__main__":
    unittest.main()
