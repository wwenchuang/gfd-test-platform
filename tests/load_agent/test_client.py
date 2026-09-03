import json
import os

from load_agent.client import AgentClient
from load_agent.config import AgentConfig
from load_agent.main import _CommandSource, _run_requested_calibration


class _Transport:
    def __init__(self):
        self.calls = []
        self.fail_once = False

    def request(self, method, url, body, headers, timeout):
        self.calls.append((method, url, body, dict(headers)))
        if self.fail_once:
            self.fail_once = False
            raise OSError("temporary network error")
        if url.endswith("/register"):
            return {"data": {"agent": {"id": "agent-1"}, "secret": "agent-secret-value"}}
        return {"data": {"accepted": 1}}


def _config(tmp_path):
    return AgentConfig.from_mapping({
        "PLATFORM_URL": "https://platform.example.com",
        "AGENT_DATA_DIR": str(tmp_path / "data"),
        "ENROLL_TOKEN": "enroll-secret-value",
    })


def test_registration_persists_credential_with_0600_and_logs_no_secret(tmp_path, caplog):
    transport = _Transport()
    client = AgentClient(_config(tmp_path), transport=transport)

    credential = client.ensure_registered({"agent_version": "1", "k6_version": "0.52.0", "hard_limits": {}})

    assert credential.agent_id == "agent-1"
    assert credential.secret == "agent-secret-value"
    assert os.stat(client.config.credential_file).st_mode & 0o777 == 0o600
    assert json.loads(client.config.credential_file.read_text())["secret"] == "agent-secret-value"
    assert "agent-secret-value" not in caplog.text
    assert "enroll-secret-value" not in caplog.text


def test_metric_retry_reuses_batch_id_and_authorization_is_not_part_of_error(tmp_path):
    transport = _Transport()
    client = AgentClient(_config(tmp_path), transport=transport, retry_attempts=2)
    client.ensure_registered({"agent_version": "1", "k6_version": "0.52.0", "hard_limits": {}})
    transport.fail_once = True

    client.post_metrics("shard-1", {"buckets": [{"step_id": "search"}]}, batch_id="batch-7")

    metric_calls = [call for call in transport.calls if "/metrics" in call[1]]
    assert len(metric_calls) == 2
    assert metric_calls[0][2]["batch_id"] == metric_calls[1][2]["batch_id"] == "batch-7"
    assert metric_calls[0][3]["Authorization"] == "Agent agent-secret-value"


def test_running_command_source_rate_limits_control_calls_and_reuses_known_k6_version(tmp_path):
    class Client:
        def __init__(self):
            self.heartbeats = []
            self.command_calls = []

        def heartbeat(self, payload):
            self.heartbeats.append(payload)

        def commands(self, shard_id):
            self.command_calls.append(shard_id)
            return [{"type": "start"}]

    client = Client()
    now = [0.0]
    source = _CommandSource(
        client,
        {"id": "shard-1", "allocation": {"vus": 25, "rate": 80}},
        _config(tmp_path),
        {"state": "valid"},
        {"max_vus": 100},
        k6_version="k6 v0.52.0",
        monotonic=lambda: now[0],
    )

    assert source() == [{"type": "start"}]
    now[0] = 1.0
    assert source() == [{"type": "start"}]
    assert len(client.heartbeats) == 1
    assert len(client.command_calls) == 1
    assert client.heartbeats[0]["k6_version"] == "k6 v0.52.0"
    assert client.heartbeats[0]["current_usage"] == {
        "processes": 1,
        "vus": 25,
        "iterations_per_second": 80,
    }

    now[0] = 11.0
    source()
    assert len(client.heartbeats) == 2
    assert len(client.command_calls) == 2


def test_idle_agent_executes_requested_calibration_and_correlates_the_result(tmp_path):
    class Runner:
        def run(self):
            return {"state": "valid", "max_vus": 300, "max_iterations_per_second": 900}

    saved = []
    result = _run_requested_calibration(
        [{"type": "calibrate", "id": "calibration-command-1"}],
        {"state": "valid", "command_id": "old"},
        runner=Runner(),
        save=lambda value: saved.append(value),
    )

    assert result["state"] == "valid"
    assert result["command_id"] == "calibration-command-1"
    assert saved == [result]


def test_failed_requested_calibration_is_reported_without_stopping_the_agent(tmp_path):
    class Runner:
        def run(self):
            raise RuntimeError("k6 unavailable")

    saved = []
    result = _run_requested_calibration(
        [{"type": "calibrate", "id": "calibration-command-2"}],
        {"state": "missing"},
        runner=Runner(),
        save=lambda value: saved.append(value),
    )

    assert result == {
        "state": "failed",
        "command_id": "calibration-command-2",
        "message": "本地k6校准失败，请检查k6版本、CPU/内存限制和Agent日志",
    }
    assert saved == [result]
