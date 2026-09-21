import importlib.util
import pathlib
import unittest


SPEC = importlib.util.spec_from_file_location("windows_runner", pathlib.Path("windows-midscene-runner.py"))
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class RunnerDeviceSnapshotTest(unittest.TestCase):
    def test_battery_and_display_state_are_parsed(self):
        text = "level: 80\ntemperature: 312\nstatus: 2\n"
        self.assertEqual(runner.parse_battery_state(text)["battery_level"], 80)
        self.assertEqual(runner.parse_battery_state(text)["battery_temperature_c"], 31.2)
        self.assertTrue(runner.parse_screen_on("Display Power: state=ON"))
        self.assertFalse(runner.parse_screen_on("Display Power: state=OFF"))

    def test_capture_rejects_non_png(self):
        def fake(args, **kwargs):
            class Result:
                stdout = b"bad"
                stderr = b""
                returncode = 0
            return Result()
        with self.assertRaises(RuntimeError):
            runner.capture_screen_png("adb", "p1", run=fake)


if __name__ == "__main__":
    unittest.main()
