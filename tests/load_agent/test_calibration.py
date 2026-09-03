from datetime import datetime, timedelta, timezone
from pathlib import Path

from load_agent.calibration import CalibrationRunner, calibration_state


class _FakeProcess:
    pid = 999999

    def __init__(self):
        self.returncode = None
        self._polls = [None, 0]

    def poll(self):
        self.returncode = self._polls.pop(0) if self._polls else 0
        return self.returncode

    def communicate(self, timeout):
        return (
            '{"metrics":{"iterations":{"values":{"rate":420}},"vus_max":{"values":{"max":180}}}}',
            "",
        )


def test_calibration_uses_local_only_k6_script_and_records_seven_day_signature(tmp_path):
    commands = []

    def popen(command, **kwargs):
        commands.append((command, kwargs, Path(command[-1]).read_text()))
        return _FakeProcess()

    now = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
    result = CalibrationRunner(
        k6_binary="k6",
        data_dir=tmp_path,
        popen=popen,
        now=lambda: now,
        hardware_signature=lambda: "cpu4-mem8g",
        agent_version="1.2.0",
        k6_version="0.52.0",
        hard_max_vus=200,
        hard_max_iterations_per_second=1000,
        process_sampler=lambda: (73.2, 256.5),
    ).run()

    script = commands[0][2]
    assert "k6/http" not in script
    assert "http.request" not in script
    assert "PLATFORM_URL" not in script
    assert result["state"] == "valid"
    assert result["max_iterations_per_second"] == 336
    assert result["max_vus"] == 144
    assert result["valid_until"] == (now + timedelta(days=7)).isoformat()
    assert result["hardware_signature"] == "cpu4-mem8g"
    assert result["cpu_peak_percent"] == 73.2
    assert result["memory_peak_mb"] == 256.5
    assert "ramping-vus" in script
    assert '"target":200' in script


def test_calibration_expires_or_invalidates_on_binary_or_hardware_change():
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    record = {
        "state": "valid",
        "valid_until": (now + timedelta(days=7)).isoformat(),
        "agent_version": "1.2.0",
        "k6_version": "0.52.0",
        "hardware_signature": "cpu4-mem8g",
    }
    assert calibration_state(record, now, "1.2.0", "0.52.0", "cpu4-mem8g") == "valid"
    assert calibration_state(record, now + timedelta(days=8), "1.2.0", "0.52.0", "cpu4-mem8g") == "expired"
    assert calibration_state(record, now, "1.3.0", "0.52.0", "cpu4-mem8g") == "invalidated"
    assert calibration_state(record, now, "1.2.0", "0.52.0", "cpu8-mem16g") == "invalidated"
