"""A busy Android Runner renews its registration independently of model retrieval."""
import threading
from types import SimpleNamespace

from test_mac_runner_runtime_config import load_runner


def test_slow_model_refresh_does_not_block_heartbeat(monkeypatch):
    runner = load_runner()
    monkeypatch.setattr(runner, "RUNNER_PLATFORM", "android")
    runner_env = {"MIDSCENE_MODEL_NAME": "qwen3.7-plus", "MIDSCENE_MODEL_API_KEY": "secret-fixture"}
    monkeypatch.setattr(runner, "task_runtime_env", lambda: runner_env)
    assert runner.midscene_env()["MIDSCENE_MODEL_API_KEY"] == "secret-fixture"
    entered, release = threading.Event(), threading.Event()

    def delayed_refresh():
        entered.set()
        assert release.wait(2), "unexpected network dependency in heartbeat"
        return {**runner_env, "MIDSCENE_MODEL_NAME": "qwen3.6-plus"}

    sent = []
    monkeypatch.setattr(runner, "task_runtime_env", delayed_refresh)
    monkeypatch.setattr(runner, "http_json", lambda *args, **kwargs: sent.append((args, kwargs)) or {})
    # A new case may refresh credentials while the heartbeat advertises the last resolved model.
    worker = threading.Thread(target=runner.midscene_env)
    worker.start()
    try:
        assert entered.wait(1)
        runner.heartbeat([])
        assert len(sent) == 1
        args, kwargs = sent[0]
        assert args[1] == "/api/runner/heartbeat" and kwargs["timeout"] == 8
        assert args[2]["capabilities"]["midscene_model_name"] == "qwen3.7-plus"
        assert args[2]["capabilities"]["midscene_model_family"] == "qwen3"
        assert "secret-fixture" not in repr(args)
    finally:
        release.set()
        worker.join(3)
    runner.heartbeat([])
    assert sent[-1][0][2]["capabilities"]["midscene_model_family"] == "qwen3.6"


class StopAfterWaits:
    def __init__(self, n):
        self.n = n
        self.delays = []

    def wait(self, delay):
        self.delays.append(delay)
        return len(self.delays) > self.n

    def is_set(self):
        return len(self.delays) > self.n


def test_job_heartbeat_retries_once_and_reports_transport_loss(monkeypatch, capsys):
    runner = load_runner()
    monkeypatch.setattr(runner, "RUNNER_PLATFORM", "android")
    snapshot = [{"device_id": "phone", "status": "online", "adb_path": "/sdk/adb"}]
    states = iter(["device", "device", "offline", "unauthorized", None])
    sent = []

    def list_transports(args, timeout):
        assert args == ["/sdk/adb", "devices"] and timeout == 3
        state = next(states)
        rows = f"phone\t{state}\n" if state else ""
        return SimpleNamespace(returncode=0, stdout="List of devices attached\nnew-phone\tdevice\n" + rows)

    def send(devices):
        sent.append(devices)
        if len(sent) == 1:
            raise TimeoutError("secret retry detail")
        return {}

    monkeypatch.setattr(runner, "run_cmd", list_transports)
    monkeypatch.setattr(runner, "heartbeat", send)
    stopped = StopAfterWaits(5)
    runner.keep_job_alive(stopped, "job-id", snapshot)
    assert stopped.delays == [10, 2, 10, 10, 10, 10]
    assert [row[0]["status"] for row in sent] == ["online", "online", "offline", "unauthorized", "offline"]
    assert all([device["device_id"] for device in rows] == ["phone"] for rows in sent)
    assert snapshot[0]["status"] == "online"
    out = capsys.readouterr().out
    assert "job-id" in out and "TimeoutError" in out and "secret retry detail" not in out


def test_unavailable_transport_inventory_does_not_renew_from_stale_snapshot(monkeypatch):
    runner = load_runner()
    monkeypatch.setattr(runner, "RUNNER_PLATFORM", "android")
    monkeypatch.setattr(runner, "run_cmd", lambda *a, **kw: SimpleNamespace(returncode=1, stdout=""))
    sent = []
    monkeypatch.setattr(runner, "heartbeat", lambda devices: sent.append(devices))
    stopped = StopAfterWaits(4)
    runner.keep_job_alive(stopped, "job", [{"device_id":"phone","status":"online"}])
    assert sent == []
    assert stopped.delays == [10, 2, 10, 2, 10]


def test_ios_job_does_not_probe_adb_or_fetch_model(monkeypatch):
    runner = load_runner()
    monkeypatch.setattr(runner, "RUNNER_PLATFORM", "ios")
    monkeypatch.setattr(runner, "run_cmd", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("ADB")))
    monkeypatch.setattr(runner, "task_runtime_env", lambda: (_ for _ in ()).throw(AssertionError("model")))
    sent = []
    monkeypatch.setattr(runner, "http_json", lambda *args, **kw: sent.append(args[2]) or {})
    runner.keep_job_alive(StopAfterWaits(1), "ios-job", [])
    assert len(sent) == 1 and sent[0]["capabilities"]["midscene_model_name"] == ""
